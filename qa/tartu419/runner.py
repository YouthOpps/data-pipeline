"""Hosted QA derivative entry point. No publisher state may be published."""
import base64
import contextlib
from hashlib import sha256
import importlib.util
import io
import json
import os
import stat
from email.message import Message
from pathlib import Path
import runpy
import re
import subprocess
import sys
from urllib.parse import urlsplit

import observer

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
SOURCE = CHECKOUT / "adapters/ee-university-of-tartu/adapter.py"
TEST = SOURCE.with_name("test_adapter.py")
WEBSITE = CHECKOUT / "qa-website"
WORKFLOW = CHECKOUT / ".github/workflows/qa-tartu419-native-r2.yml"
WEBSITE_SHA = "fe54fa839233f34973991584567408acae269487"
REVIEWER_SHA256 = "72a45911ad29b43f1b353402ebc6f7da372c98f85aed773ae864220e01ff0edf"
TEST_SHA = "d0c1723a84e39113f8c2f2bfbb5d2c6cdf9a5833bf0fe3da61c4184e3a9f974e"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def consumer(a, records, reference):
    desired = sorted(record["id"] for record in records)
    message = {"records": records, "metadata": a.metadata(reference, {}, records)}
    call = subprocess.run(
        ["node", str(HERE / "consumer.mjs")],
        input=json.dumps(message), text=True, capture_output=True, timeout=30, check=True,
    )
    value = json.loads(call.stdout)
    require(value["ids"] == desired and value["count"] == len(desired),
            "Current website consumer disagrees with native records")
    return value


def custody(a):
    if a._STATE_PATH is None:
        return None
    with a.locked_budget():
        descriptor, before = a.retained_journal()
        raw = os.pread(descriptor, 16385, 0)
        current, after = a.retained_journal()
        require(descriptor == current and a.journal_version(before) == a.journal_version(after)
                and 0 < len(raw) == before.st_size <= 16384, "Existing journal generation changed")
        return {"sha256": sha256(raw).hexdigest(), "bytes": len(raw),
                "authority": observer.journal_authority(a, before),
                "state": a.validate_budget(json.loads(raw))}


def verify_current_custody(a, snapshot):
    """Read the exact last known journal through the original named parent."""
    proof = snapshot["authority"]
    target = Path(proof["path"])
    with contextlib.ExitStack() as stack:
        parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        stack.callback(os.close, parent)
        descriptor = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        stack.callback(os.close, descriptor)

        def check():
            info = os.fstat(parent)
            named = target.parent.lstat()
            identity = lambda s: [s.st_dev, s.st_ino, s.st_uid, stat.S_IMODE(s.st_mode)]
            require(identity(info) == identity(named) == proof["parent"],
                    "Original publisher directory changed")
            state = os.fstat(descriptor)
            linked = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            a.validate_owned_output(state)
            require(list(a.journal_version(state)) == list(a.journal_version(linked))
                    == proof["generation"], "Original publisher generation changed")
        check()
        raw = os.pread(descriptor, 16385, 0)
        check()
        require(len(raw) == snapshot["bytes"] <= 16384
                and sha256(raw).hexdigest() == snapshot["sha256"],
                "Original publisher bytes changed")


def acknowledged_rows(a, persistence):
    acknowledgements = []
    for row in persistence:
        if row["acknowledged"] is not True:
            continue
        raw = base64.b64decode(row["after_base64"], validate=True)
        state = a.validate_budget(json.loads(raw))
        a.require_modern_state(state)
        require(0 < len(raw) <= 16384 and sha256(raw).hexdigest() == row["after_sha256"]
                and state == row["state_before_call"]
                and row["authority"]["generation"][2] == len(raw),
                "Invalid witnessed journal acknowledgement")
        acknowledgements.append({
            "sha256": row["after_sha256"], "bytes": len(raw), "state": state,
            "raw_base64": row["after_base64"], "authority": row["authority"],
            "ack_start": row["monotonic"], "ack_end": row["completed"]["monotonic"],
        })
    return acknowledgements


def unqualified_ack(a, acknowledgements):
    if not acknowledgements:
        return None
    row = acknowledgements[-1]
    try:
        verify_current_custody(a, row)
    except Exception:
        current = "different_or_unverified"
    else:
        current = "same_original_ack"
    # A fatal/uncertain run cannot promote even identical, original bytes.
    return {**row, "classification": "last_exact_ack_unqualified",
            "current_custody": current, "continuation_allowance": False}


def publisher_rows():
    return [
        row for row in observer.RAM["capture"]
        if (host := urlsplit(row["url"]).hostname)
        and (host == "ut.ee" or host.endswith(".ut.ee"))
    ]


def wire_summary(row):
    segments = [base64.b64decode(s.get("base64", s.get("partial_base64", "")), validate=True)
                for s in row["segments"]]
    return {
        "target_sha256": sha256(row["url"].encode()).hexdigest(),
        "user_agent": next((v for k, v in row["request_headers"] if k.lower() == "user-agent"), None),
        "started": row["monotonic"],
        "completed": row.get("completed", {}).get("monotonic"),
        "closed": row.get("closed", False), "status": row.get("status"),
        "bytes": sum(map(len, segments)), "body_sha256": sha256(b"".join(segments)).hexdigest(),
        "journal_sha256_at_open": row.get("journal_sha256"),
        "inventory_page": row["inventory_page"],
        "partial_segment": any("error" in s for s in row["segments"]),
    }


def verify_receipts(a, rows, acks):
    require(len(rows) == a._PUBLISHER_ENTRIES and bool(rows) and bool(acks),
            "Incomplete genuine publisher wire or acknowledgement ledger")
    summaries = [wire_summary(row) for row in rows]
    for index, (row, summary) in enumerate(zip(rows, summaries)):
        status = summary["status"]
        is_rep = urlsplit(row["url"]).path == "/robots.txt"
        require(
            summary["user_agent"] == a._USER_AGENT and summary["closed"] is True
            and type(status) is int and (
                200 <= status < 400 or is_rep and 400 <= status < 500 and status != 429)
            and summary["completed"] is not None and summary["completed"] >= summary["started"]
            and not summary["partial_segment"] and not row.get("open_error")
            and not row.get("close_error") and row["segments"]
            and row["segments"][-1].get("base64") == "",
            "Incomplete, unframed or disallowed genuine publisher wire",
        )
        headers = Message()
        for name, value in row["headers"]:
            headers[name] = value
        length = a.response_framing(headers, 5_000_000)
        require(length is None or summary["bytes"] == length, "Genuine wire body length differs")
        owner = row["journal_owner"]["physical"]
        require(owner["pending"] is True and owner["receipt"] is None
                and owner["target"] == row["url"] and owner["started"] <= row["wall"],
                "Physical reservation does not own this wire")
        previous = [ack for ack in acks if ack["ack_end"] <= summary["started"]]
        require(previous and previous[-1]["sha256"] == row["journal_sha256"]
                and previous[-1]["state"]["physical"] == owner,
                "Actual opener lacks its preceding original persisted owner")
        next_start = (summaries[index + 1]["started"] if index + 1 < len(summaries)
                      else float("inf"))
        settled = []
        for ack in acks:
            physical = ack["state"]["physical"]
            if not summary["completed"] <= ack["ack_start"] < ack["ack_end"] <= next_start:
                continue
            receipt = physical["receipt"]
            if (physical["id"] == owner["id"] and physical["pending"] is False
                    and isinstance(receipt, dict) and receipt["owner"] == owner["id"]
                    and receipt["completed"] >= row["completed"]["wall"]):
                settled.append(ack)
        require(bool(settled), "Actual response close lacks its exact settled journal owner")
        assessment = settled[0]["state"]["physical"]["receipt"]["assessment"]
        require(all(assessment.get(k) == value for k, value in {
            "status": status, "body_sha256": summary["body_sha256"],
            "bytes": summary["bytes"], "url": row["url"],
        }.items()), "Final journal assessment differs from actual framed bytes")
        if is_rep:
            policy = settled[0]["state"]["policy"]["receipt"]
            require(
                isinstance(policy, dict) and policy["owner"] == row["journal_owner"]["policy"]["id"]
                and policy["assessment"] == assessment.get("policy"),
                "REP close lacks its exact separately settled policy owner",
            )
        summary.update({"reservation_ack_sha256": previous[-1]["sha256"],
                        "settlement_ack_sha256": settled[0]["sha256"], "physical_owner": owner["id"]})
    gaps = [second["started"] - first["completed"]
            for first, second in zip(summaries, summaries[1:])]
    rolling = max(sum(r["started"] - 60 < e["started"] <= r["started"] for e in summaries)
                  for r in summaries)
    require(all(g >= 12.2 for g in gaps) and rolling <= 5, "Publisher pacing boundary not proved")
    pages = [r for r in summaries if r["inventory_page"]]
    page_gaps = [second["started"] - first["completed"] for first, second in zip(pages, pages[1:])]
    require(all(g >= 30 for g in page_gaps), "Inventory page pacing boundary not proved")
    return {"minimum_physical_gap": min(gaps, default=None), "maximum_starts_per_60s": rolling,
            "minimum_inventory_page_gap": min(page_gaps, default=None), "verified_receipts": summaries}


def original_test(records_stdout, diagnostics):
    argv = sys.argv
    try:
        sys.argv = [str(TEST)]
        with contextlib.redirect_stdout(records_stdout), contextlib.redirect_stderr(diagnostics):
            runpy.run_path(str(TEST), run_name="__main__")
    except SystemExit as result:
        # unittest exits with the Boolean negation of wasSuccessful().
        require(result.code is False or type(result.code) is int and result.code == 0,
                "Original test exited unsuccessfully")
    else:
        raise RuntimeError("Missing original test exit")
    finally:
        sys.argv = argv


def seal(recipient, raw):
    try:
        result = subprocess.run(
            ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
             "-recip", str(recipient)], input=raw, capture_output=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def encrypt_complete_evidence(output, recipient, snapshot):
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ciphertext = seal(recipient, raw)
    if ciphertext is None:
        return None
    with (output / "capture.cms").open("xb") as stream:
        stream.write(ciphertext)
        stream.flush()
        os.fsync(stream.fileno())
    return sha256(ciphertext).hexdigest()


def main():
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Only genuine GitHub QA is admitted")
    require("DATA_SOURCE_TOKEN" not in os.environ and "TARTU_PACING_ARTIFACT_PATH" not in os.environ,
            "Publication credentials and production state targets are forbidden")
    recipient = HERE / "reviewer.pem"
    require(isinstance(REVIEWER_SHA256, str) and re.fullmatch("[0-9a-f]{64}", REVIEWER_SHA256)
            and digest(recipient) == REVIEWER_SHA256, "Exact reviewer certificate remains OFF")
    require(seal(recipient, b"Tartu hosted QA CMS preflight"), "Reviewer CMS preflight failed")
    output = Path(os.environ["RUNNER_TEMP"]) / "tartu-native-qa"
    output.mkdir(mode=0o700)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=CHECKOUT).strip()
    require(sha == os.environ.get("GITHUB_SHA"), "Exact GitHub checkout differs")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", "adapters/ee-university-of-tartu",
                    "qa/tartu419", str(WORKFLOW.relative_to(CHECKOUT))],
                   cwd=CHECKOUT, check=True)
    website_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, cwd=WEBSITE).strip()
    require(website_sha == WEBSITE_SHA, "Reviewed consumer checkout differs")
    witnesses = {p: digest(p) for p in (SOURCE, TEST, WORKFLOW, HERE / "runner.py",
                                      HERE / "observer.py", HERE / "consumer.mjs", recipient,
                                      WEBSITE / "scripts/data.mjs", WEBSITE / "assets/discovery.js")}
    require(witnesses[TEST] == TEST_SHA, "Original native test changed")

    def pin():
        require(all(digest(path) == value for path, value in witnesses.items()),
                "Reviewed QA implementation or consumer changed")
        return {str(path.relative_to(CHECKOUT)): value for path, value in witnesses.items()}

    spec = importlib.util.spec_from_file_location("adapter", SOURCE)
    a = importlib.util.module_from_spec(spec)
    sys.modules["adapter"] = a
    spec.loader.exec_module(a)
    require(a.hosted_qa_mode(), "Tartu hosted QA approval remains OFF")
    a._HOSTED_QA_WITNESS = pin
    observer.pin_inputs = pin
    observer.install_witness(a)
    records_stdout, diagnostics = io.StringIO(), io.StringIO()
    identity, snapshot, checks, failure = None, None, None, None
    native_started, native_pass = False, False
    try:
        with a.execution():
            try:
                with a.phase("restore", 180):
                    identity = a.verify_hosted_qa_identity()
                    a.prepare_collection()
                native_started = True
                original_test(records_stdout, diagnostics)
                records = json.loads(records_stdout.getvalue())
                a.validate_records(records)
                native_pass = True
                reference = a.utc_now()
                filtered = a.remove_expired_records(records, reference)
                a.validate_records(filtered, allow_empty=True)
                checks = {
                    "raw": consumer(a, records, reference),
                    "current": consumer(a, filtered, reference),
                    "valid_empty_consumer": consumer(a, [], reference),
                    "raw_stdout_sha256": sha256(records_stdout.getvalue().encode()).hexdigest(),
                    "wire_timing": verify_receipts(a, publisher_rows(), acknowledged_rows(a, observer.RAM["persistence"])),
                }
            finally:
                if not a._PUBLISHER_GATE_FAILED:
                    with a.phase("export", a.EXPORT_TIMEOUT):
                        snapshot = custody(a)
    except BaseException as error:
        failure = {"type": type(error).__name__, "stage": getattr(error, "stage", None)}
        if isinstance(error, a.AdapterError):
            failure["message"] = a.safe_error(error)
    receipts = [wire_summary(row) for row in publisher_rows()]
    acks = acknowledged_rows(a, observer.RAM["persistence"])
    if snapshot is not None:
        try:
            require(a._EXECUTION_CLOSED and not a._PUBLISHER_GATE_FAILED
                    and acks and acks[-1]["sha256"] == snapshot["sha256"]
                    and acks[-1]["authority"] == snapshot["authority"],
                    "Post-execution original journal authority differs")
            verify_current_custody(a, snapshot)
        except Exception as error:
            snapshot = None
            failure = {"type": type(error).__name__, "stage": "qa_state_custody"}
    unqualified = unqualified_ack(a, acks) if snapshot is None else None
    settled = snapshot and not snapshot["state"]["blocked"] and not snapshot["state"]["in_flight"] \
        and not any(snapshot["state"][k]["pending"] for k in ("physical", "policy", "logical"))
    capture_digest = encrypt_complete_evidence(output, recipient, {
        "identity": identity, "head_sha": sha,
        "publisher_capture": publisher_rows(), "acknowledgements": acks,
        "persistence_attempts": observer.RAM["persistence"],
        "logical_source_inputs": observer.RAM["pages"],
        "original_stdout": records_stdout.getvalue(),
        "original_stderr": diagnostics.getvalue(),
    })
    after = {
        "schema": "tartu-hosted-qa-evidence-v1",
        "identity": identity, "source": a.SOURCE_ID,
        "head_sha": sha, "pins": pin(),
        "native_started": native_started,
        "original_native_pass": native_pass,
        "accepted_run_candidate": bool(native_pass and checks and not failure and settled and capture_digest),
        "encrypted_complete_capture_sha256": capture_digest,
        "failure": failure, "publisher_entries": a._PUBLISHER_ENTRIES,
        "journal_commits": a._JOURNAL_COMMITS, "execution_closed": a._EXECUTION_CLOSED,
        "post_run_state": snapshot, "unqualified_last_acknowledgement": unqualified,
        "wire_receipts": receipts,
        "journal_witness": [
            {key: row.get(key) for key in ("after_sha256", "acknowledged", "monotonic", "completed")}
            for row in observer.RAM["persistence"]
        ],
        "public_consumer_checks": checks,
    }
    with (output / "qa-pacing-evidence.json").open("x") as stream:
        json.dump(after, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"source": a.SOURCE_ID, "run": identity, "native_started": native_started,
                      "original_native_pass": native_pass, "checks_passed": bool(checks),
                      "publisher_entries": a._PUBLISHER_ENTRIES, "failure": failure}, sort_keys=True))
    return 0 if after["accepted_run_candidate"] else 1


if __name__ == "__main__":
    sys.exit(main())
