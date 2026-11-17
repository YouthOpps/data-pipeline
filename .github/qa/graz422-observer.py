"""Temporary TU Graz bound QA workflow observer. Raw evidence stays in RAM until encrypted."""
import base64
import contextlib
import copy
from datetime import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import time

ROOT = Path.cwd()
SLUG = "at-tu-graz-msc-selection"
SOURCE = ROOT / "adapters" / SLUG / "adapter.py"
TEST = SOURCE.with_name("test_adapter.py")
HELPER = ROOT / ".github/qa"
CONSUMER = HELPER / "graz422-consumer.mjs"
CERTIFICATE = HELPER / "graz422-review.pem"
WEBSITE_SHA = "bc9d9f9f83c58061c6d29f3fd3d6b04b56ad1b7503b12a009c2f91e24b4deef3"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def serial(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


def load_adapter():
    spec = importlib.util.spec_from_file_location("adapter", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def instrument(a):
    """Mark actual publisher opener entry rather than only reservation."""
    original = a.OPENER.open

    def checked_open(request, *args, **kw):
        if request.full_url in a.URLS:
            current = a.AUDIT["requests"][-1]
            if (
                current["url"] != request.full_url
                or "actual_start" in current
                or dict((k.casefold(), v) for k, v in request.header_items()).get("user-agent") != a.USER_AGENT
            ):
                raise RuntimeError("Native observer did not match physical authority")
            current["actual_wall_start"] = a.now()
            current["actual_start"] = a.CLOCK.monotonic()
        elif not request.full_url.startswith(("https://api.github.com/repos/YouthOpps/data-pipeline/", "https://api.github.com/repos/YouthOpps/ai-workspace/actions/")):
            raise RuntimeError("Unexpected network opener destination")
        return original(request, *args, **kw)

    a.OPENER.open = checked_open


def original_test(a):
    out, err = io.StringIO(), io.StringIO()
    result = {"exit": None, "error_type": None}
    old_handler = signal.getsignal(signal.SIGALRM)
    old_argv, old_module = sys.argv, sys.modules.get("adapter")

    def expired(_signum, _frame):
        signal.setitimer(signal.ITIMER_REAL, 0.25, 0.25)
        raise a.Deadline("Bounded native observer deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 420)
    try:
        sys.argv = [str(TEST)]
        sys.modules["adapter"] = a
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runpy.run_path(str(TEST), run_name="__main__")
    except BaseException as error:
        result["exit"] = 1
        result["error_type"] = type(error).__name__
    else:
        result["exit"] = 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        sys.argv = old_argv
        if old_module is None:
            sys.modules.pop("adapter", None)
        else:
            sys.modules["adapter"] = old_module
    result["stdout"] = out.getvalue()
    result["stderr"] = err.getvalue()
    return result


def consume(a, records, checked_at):
    metadata = a.metadata(checked_at, {}, records, checked_at=checked_at)
    checked = subprocess.run(
        ["node", str(CONSUMER)], input=json.dumps({"records": records, "metadata": metadata}),
        text=True, capture_output=True, timeout=30, check=True,
    )
    result = json.loads(checked.stdout)
    if result["ids"] != sorted(row["id"] for row in records) or result["sources"] != [SLUG]:
        raise ValueError("Complete actual consumer identity differs")
    return result


def retained_state(a, binding):
    """Verify any surviving original journal, even when the test itself failed."""
    if a.NATIVE_TEST_EXPORT is None:
        return {}
    authority, state, expected, claim = a.NATIVE_TEST_EXPORT
    if authority != binding:
        raise ValueError("Exact native workflow identity changed")
    with a.Journal(expected, lambda: None) as journal:
        if a.canonical(state) != journal.ack:
            raise ValueError("Final native state no longer matches the journal")
        journal.capture_claim = claim
        journal.verify_capture()
        marker = json.loads(claim[0])
        if marker["source_sha256"] != a.own_source_hash() or marker["binding"] != list(binding):
            raise ValueError("Original native collection claim differs")
    writes = a.AUDIT["persistence"]
    if not writes or not writes[-1]["acknowledged"] or writes[-1]["actual_journal_bytes"] != a.canonical(state):
        raise ValueError("Final native state lacks its original persistence acknowledgement")
    return {"owned_state": state, "state_sha256": sha(a.canonical(state))}


def assess(a, test, retained):
    records = json.loads(test["stdout"])
    a.validate_records(records)
    checked_at = datetime.fromisoformat(records[0]["last_checked_at"].replace("Z", "+00:00"))
    visible = a.expire(records, checked_at)
    a.validate_records(visible, allow_empty=True)
    requests = a.AUDIT["requests"]
    if (len(requests) != 2 or [row["url"] for row in requests] != list(a.URLS)
            or requests[1]["status"] != 200
            or any(not row["segments"] or row["segments"][-1] != b"" for row in requests)):
        raise ValueError("Original native source did not capture the complete scope")
    gaps = [right["actual_start"] - left["completion"] for left, right in zip(requests, requests[1:])]
    if min(gaps) < 12.2 or any(
        row["completion"] is None or not row["status"]
        or any(row.get(key) for key in ("read_error", "close_error", "error"))
        for row in requests
    ):
        raise ValueError("Actual native capture is incomplete or failed pacing")
    headers = a.Message()
    for key, value in requests[1]["headers"]:
        headers[key] = value
    document = b"".join(requests[1]["segments"]).decode(headers.get_content_charset() or "utf-8", "strict")
    if a.normalize(a.extract_facts(document), checked_at) != records:
        raise ValueError("Source replay differs from the complete original test result")
    final_state = retained.get("owned_state")
    if final_state is None:
        raise ValueError("Successful original test lacks a verified final state")
    writes = a.AUDIT["persistence"]
    if not writes or any(not item["acknowledged"] or item["bytes"] != item["actual_journal_bytes"] for item in writes):
        raise ValueError("Original journal acknowledgement is incomplete")
    if writes[-1]["bytes"] != a.canonical(final_state) or any(
        final_state[key] for key in ("blocked", "in_flight", "physical_pending")
    ):
        raise ValueError("Final native state is not complete")
    if len(final_state["starts"]) < 4 or not set(a.reviewed_transfer()["starts"]).issubset(final_state["starts"]):
        raise ValueError("Original research timing observations did not survive")
    native_times = []
    for row in requests:
        request_id = row["request_id"]
        reservations = [item for item in writes
                        if item["after"]["physical_id"] == request_id
                        and item["after"]["physical_pending"]]
        assessed = [item for item in writes
                    if item["after"]["completed_physical_id"] == request_id
                    and not item["after"]["physical_pending"]]
        if not reservations or len(assessed) != 1:
            raise ValueError("Exact physical request lacks its owned state transitions")
        reservation = reservations[0]["after"]["physical_started"]
        current = row["actual_wall_start"]
        if (
            reservation not in final_state["starts"]
            or not reservation <= row["wall_start"] <= current <= row["wall_completion"]
            or not reservations[0]["completed_monotonic"] <= row["actual_start"] <= row["completion"]
            or not row["completion"] <= assessed[0]["completed_monotonic"]
        ):
            raise ValueError("Durable reservation, actual opener and completion ordering differ")
        native_times.append({
            "request_id": request_id,
            "target": row["url"],
            "reservation": reservation,
            "actual_wall_start": current,
            "actual_monotonic_start": row["actual_start"],
        })
    interval = final_state["interval"]
    if min(gaps) < max(12.2, interval):
        raise ValueError("The retained current pacing floor was not observed")
    starts = [row["actual_start"] for row in requests]
    per_minute = max(sum(stamp - 60 < earlier <= stamp for earlier in starts) for stamp in starts)
    if per_minute > 5:
        raise ValueError("Rolling publisher request budget was exceeded")
    # The original state and the conservative transfer floor are distinct.
    # Do not count reservations or timing marks as additional physical requests.
    research = a.RESEARCH_OBSERVATIONS
    transfer_state = copy.deepcopy(final_state)
    transfer_state["starts"] = sorted(set(final_state["starts"])
                                      | {row["pre_opener_wall"] for row in research}
                                      | {row["actual_wall_start"] for row in native_times})
    a.validate(transfer_state)
    return {
        "actual_checks": {
            "physical_requests": len(requests),
            "minimum_physical_gap_seconds": min(gaps),
            "persistence_acknowledged": len(writes),
            "original_state_times_retained": len(final_state["starts"]),
            "actual_native_rolling_starts_max": per_minute,
            "raw_consumer": consume(a, records, checked_at),
            "expiry_filtered_consumer": consume(a, visible, checked_at),
            "actual_expired_subset_exists": len(visible) < len(records),
            "complete_original_body_replay": True,
        },
        "classified_start_observations": {"research": research, "native": native_times},
        "retired_lineage": {
            "original_archive": a.ORIGINAL_ARCHIVE, "original_pending_sha256": a.ORIGINAL_SHA,
            "settlement_archive": a.SETTLEMENT_ARCHIVE, "settled_state_sha256": a.SETTLED_SHA,
            "settlement_provenance_sha256": a.PROVENANCE_SHA, "transfer_sha256": a.TRANSFER_SHA,
            "settlement_publisher_requests": 0,
        },
        "carry_forward_state": transfer_state,
        "carry_forward_state_sha256": sha(a.canonical(transfer_state)),
    }


def seal(plaintext):
    return subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm", str(CERTIFICATE)],
        input=plaintext, capture_output=True, timeout=30, check=True,
    ).stdout


def emit_manifest(output, manifest):
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in manifest.items()
                      if key not in ("owned_state", "carry_forward_state")}, sort_keys=True))


def main():
    output = Path(os.environ["RUNNER_TEMP"]) / "graz422-native-evidence"
    output.mkdir(mode=0o700)
    try:
        expected_files = {'assets/discovery.js': '39dccc7be14705e2c338ae065044b2f6736d5e0b7bec70436937ad543a8741bb', 'package.json': '7cd869b0bff66038195746c4bac649e863b140c6b6505b39bed557ec1b4b1833'}
        expected_files["scripts/data.mjs"] = WEBSITE_SHA
        if any(sha((ROOT / "_qa/website" / name).read_bytes()) != expected
               for name, expected in expected_files.items()):
            raise RuntimeError("Current qualifying website consumer revision differs")
        a = load_adapter()
        binding = a.ci_qa_binding()
        # An invalid recipient or unsupported encryption command must fail before access.
        if not seal(b"TU Graz QA recipient preflight"):
            raise RuntimeError("Review certificate preflight failed")
    except Exception as error:
        emit_manifest(output, {
            "schema": 1, "source": SLUG, "run_id": os.environ.get("GITHUB_RUN_ID"),
            "head_sha": os.environ.get("GITHUB_SHA"), "original_test_exit": None,
            "preflight_error": type(error).__name__,
            "identity_failure_stage": getattr(error, "qa_stage", None),
            "publisher_opener_entries": 0,
        })
        return 1
    instrument(a)
    test = original_test(a)
    check = {}
    retained = {}
    state_error = None
    check_error = None
    try:
        retained = retained_state(a, binding)
    except Exception as error:
        state_error = type(error).__name__
    if test["exit"] == 0:
        try:
            check = assess(a, test, retained)
        except Exception as error:
            check_error = type(error).__name__
    evidence = {
        "native_test": test, "audited_original_transport": a.AUDIT,
        "binding": binding, "source_sha256": a.own_source_hash(),
        "test_sha256": sha(TEST.read_bytes()),
        "review_certificate_sha256": sha(CERTIFICATE.read_bytes()),
        "final_state_validation_error": state_error,
        **retained, **check,
    }
    plaintext = json.dumps(evidence, default=serial, sort_keys=True, allow_nan=False).encode()
    manifest = {
        "schema": 1, "source": SLUG, "binding": binding,
        "source_sha256": evidence["source_sha256"], "test_sha256": evidence["test_sha256"],
        "original_test_exit": test["exit"], "original_test_error": test["error_type"],
        "publisher_opener_entries": sum("actual_start" in row for row in a.AUDIT["requests"]),
        "observer_error": check_error,
        "final_state_validation_error": state_error,
        "plaintext_sha256": sha(plaintext),
        "review_certificate_sha256": evidence["review_certificate_sha256"],
        **retained, **check,
    }
    try:
        encrypted = seal(plaintext)
        (output / "evidence.cms").write_bytes(encrypted)
        manifest["encrypted_sha256"] = sha(encrypted)
    except (OSError, subprocess.SubprocessError) as error:
        manifest["encrypted_evidence_error"] = type(error).__name__
        emit_manifest(output, manifest)
        return 1
    emit_manifest(output, manifest)
    return 0 if test["exit"] == 0 and check and not check_error and not state_error else 1


if __name__ == "__main__":
    sys.exit(main())
