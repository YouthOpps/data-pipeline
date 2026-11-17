"""Run the unchanged Google test once on GitHub and retain encrypted evidence."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
from email.message import Message

from observer import Observer, encode_bytes, sha


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE = ROOT / "adapters/google/adapter.py"
TEST = SOURCE.with_name("test_adapter.py")
WORKFLOW = ROOT / ".github/workflows/qa-google407-native-r2.yml"
SITE = ROOT / "qa-website"
SITE_HEAD = "7da2b3eb81853707def0ff67de64f8d43773fe87"
SITE_FILES = {
    "scripts/data.mjs": "bc9d9f9f83c58061c6d29f3fd3d6b04b56ad1b7503b12a009c2f91e24b4deef3",
    "assets/discovery.js": "39dccc7be14705e2c338ae065044b2f6736d5e0b7bec70436937ad543a8741bb",
}
CERT = HERE / "reviewer.pem"


def json_bytes(value):
    return json.dumps(value, default=encode_bytes, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()


def create(path, raw):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_EXCL | os.O_CREAT
    with os.fdopen(os.open(path, flags, 0o600), "w+b") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
        stream.seek(0)
        if stream.read(len(raw) + 1) != raw:
            raise ValueError("Exact evidence write readback differs")
        return generation(os.fstat(stream.fileno()))


def generation(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_nlink,
            stat.S_IMODE(info.st_mode), info.st_size, info.st_ctime_ns, info.st_mtime_ns)


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], timeout=15)


def pinned_inputs(head):
    if git(ROOT, "rev-parse", "HEAD").decode().strip() != head or git(SITE, "rev-parse", "HEAD").decode().strip() != SITE_HEAD:
        raise ValueError("Exact reviewed Git checkout differs")
    pins = {str(path.relative_to(ROOT)): sha(git(ROOT, "show", f"{head}:{path.relative_to(ROOT)}"))
            for path in (SOURCE, TEST, WORKFLOW, HERE / "runner.py", HERE / "observer.py", HERE / "consumer.mjs",
                         HERE / "scope.json", HERE / "producer-history.json", HERE / "settlement-witness.json", CERT)}
    for file, wanted in SITE_FILES.items():
        if sha(git(SITE, "show", f"{SITE_HEAD}:{file}")) != wanted:
            raise ValueError("Exact website consumer revision differs")
        pins[f"qa-website/{file}"] = wanted
    scope = json.loads((HERE / "scope.json").read_bytes())
    if any(pins.get(file) != digest for file, digest in scope["inputs"].items()):
        raise ValueError("Approved Google407 input pins differ")
    return pins


def load(name):
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_original(adapter, out, err):
    old, sys.argv = sys.argv, [str(TEST)]
    sys.modules["adapter"] = adapter
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            runpy.run_path(str(TEST), run_name="__main__")
    except SystemExit as result:
        return int(result.code) if isinstance(result.code, int) else int(result.code is not None)
    finally:
        sys.argv = old
    raise ValueError("Missing original unittest result")


def seal(raw):
    result = subprocess.run(["openssl", "cms", "-encrypt", "-binary", "-outform", "DER",
                             "-aes-256-gcm", str(CERT)], input=raw, capture_output=True, timeout=90, check=True)
    if not result.stdout:
        raise ValueError("Missing original encrypted evidence")
    return result.stdout


def consume(adapter, records, stamp, *, wrong=False):
    metadata = adapter.metadata(stamp, {}, records, checked_at=stamp)
    if wrong:
        metadata["source"] = "invalid-source"
    result = subprocess.run(["node", str(HERE / "consumer.mjs")],
                            input=json_bytes({"records": records, "metadata": metadata}),
                            capture_output=True, timeout=40, check=False)
    if wrong:
        if result.returncode == 0:
            raise ValueError("Real consumer accepted a foreign source")
        return {"rejected": True}
    if result.returncode != 0:
        raise ValueError("Real website consumer rejected original records")
    value = json.loads(result.stdout)
    if value["ids"] != sorted(record["id"] for record in records) or value["sources"] != [adapter.SOURCE_ID]:
        raise ValueError("Real website consumer source or IDs differ")
    return value


def replay_original(adapter, observer, records):
    if set(observer.pages) != set(adapter.INPUTS):
        raise ValueError("Original complete programme frontier differs")
    bodies = {wire["request_id"]: wire for wire in observer.wires}
    for page in observer.pages.values():
        wire = bodies[page["request_id"]]
        raw = b"".join(wire["segments"])
        codings = [v.lower().strip() for k, v in wire["response_headers"] if k.lower() == "content-encoding"]
        decoded = adapter.decode_observed_gzip(raw, 8388608) if codings == ["gzip"] else raw
        if decoded != page["body"] or wire["status"] != page["status"]:
            raise ValueError("Original guarded input differs from recorded physical body")
    other = load("google407_offline_original_replay")
    stamp = records[0]["last_checked_at"]
    other.utc_now = lambda: stamp

    def captured(url):
        page = observer.pages[url]
        headers = Message()
        for key, value in page["headers"]:
            headers[key] = value
        return page["status"], headers, page["body"]

    def refuse_network(*_args, **_kwargs):
        raise RuntimeError("Original in-memory replay cannot open a request")

    other._OPENER.open = refuse_network
    other.fetch_public = captured
    other.read_input = lambda url: other.read_guarded_input(url, other.INPUTS[url])
    result = other.parse_inventory(other.read_pages())
    temporal = {"created_at", "updated_at", "first_seen_at", "last_seen_at", "last_checked_at"}
    def material(values):
        return {row["id"]: {k: v for k, v in row.items() if k not in temporal} for row in values}
    if material(result) != material(records):
        raise ValueError("Original body replay differs from original test records")


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline":
        raise ValueError("Google407 live test is GitHub Actions only")
    root = Path(os.environ["RUNNER_TEMP"])
    if root != root.resolve() or root.stat().st_uid != os.getuid():
        raise ValueError("Original runner temporary authority differs")
    output = root / "google407-native-qa"
    output.mkdir(mode=0o700)
    head = os.environ["GITHUB_SHA"]
    report = {"head": head, "website_head": SITE_HEAD, "original_outcome": None,
              "observer_outcome": 1, "publisher_opener_entries": 0}
    out, err = io.StringIO(), io.StringIO()
    observer = None
    adapter = None
    try:
        pins = pinned_inputs(head)
        seal(b"Google407 hosted evidence recipient preflight")
        claim = root / "google407-native-qa.claim"
        payload = json_bytes({"head": head, "run_id": os.environ["GITHUB_RUN_ID"], "pins": pins})
        claim_version = create(claim, payload)

        def witness():
            fd = os.open(claim, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                before = generation(os.fstat(stream.fileno()))
                if (before != claim_version or stream.read(len(payload) + 1) != payload
                        or generation(os.fstat(stream.fileno())) != before or generation(claim.lstat()) != before):
                    raise ValueError("Original hosted Google407 claim changed")
            if any(sha((ROOT / path).read_bytes()) != digest for path, digest in pins.items()):
                raise ValueError("Original hosted Google407 checked-out inputs changed")
            return pins

        witness()
        adapter = load("google407_native_original")
        adapter._GOOGLE407_QA_WITNESS = witness
        observer = Observer(adapter, witness)
        report["original_outcome"] = run_original(adapter, out, err)
        if report["original_outcome"] != 0:
            raise ValueError("Original nonpublishing test failed")
        records = json.loads(out.getvalue())
        adapter.validate_records(records)
        replay_original(adapter, observer, records)
        stamp = adapter.utc_now()
        report["consumer"] = {"raw": consume(adapter, records, stamp),
                              "current": consume(adapter, adapter.remove_expired_records(records, stamp), stamp),
                              "valid_empty": consume(adapter, [], stamp),
                              "wrong_source": consume(adapter, records, stamp, wrong=True)}
        report["transport"] = observer.certify()
        report["complete_original_body_replay"] = True
        witness()
    except BaseException as error:
        report["error_type"] = type(error).__name__

    if observer is not None:
        report["publisher_opener_entries"] = len(observer.wires)
        report["journal_acks"] = sum(row["ack"] for row in observer.journals)
        report["identity"] = adapter._GOOGLE407_QA_CONTEXT
        report["owned_archive"] = observer.archive
        report["settlement_owner"] = adapter._GOOGLE407_R2_OWNER
        report["restored_settlement_sha256"] = (sha(adapter.canonical_state(adapter._GOOGLE407_R2_STATE))
            if adapter._GOOGLE407_R2_STATE is not None else None)
        last = observer.latest
        if last is not None:
            try:
                current = observer.released and observer.verify_current_state() and not adapter._JOURNAL_FATAL
            except BaseException:
                current = False
            report["last_original_ack"] = {"sha256": last["sha256"], "current_custody": bool(current),
                                           "pending": last["state"]["in_flight"], "blocked": last["state"]["blocked"]}
        complete = {"manifest": report, "original_stdout": out.getvalue(), "original_stderr": err.getvalue(),
                    "wires": observer.wires, "persistence": observer.journals, "guarded_bodies": observer.pages}
        try:
            raw = json_bytes(complete)
            encrypted = seal(raw)
            create(output / "evidence.cms", encrypted)
            report.update(encrypted_sha256=sha(encrypted), plaintext_sha256=sha(raw))
            state_retained = False
            if last is not None and current:
                witness()
                if not observer.verify_current_state():
                    raise ValueError("Original Google407 journal changed after encryption")
                create(output / "owned-state.json", last["raw"])
                if not observer.verify_current_state():
                    raise ValueError("Original Google407 journal changed during export")
                state_retained = True
            report["state_artifact_present"] = state_retained
            if "error_type" not in report and state_retained:
                observations = report["transport"]["wires"]
                carried = dict(last["state"], starts=sorted(set(last["state"]["starts"]) |
                    {item["actual_start"] for item in observations}))
                adapter.validate_budget(carried)
                create(output / "carry-forward-review-only.json", adapter.canonical_state(carried))
                report["review_only_floor_sha256"] = sha(adapter.canonical_state(carried))
                report["observer_outcome"] = 0
        except BaseException as error:
            report["retention_error"] = type(error).__name__
        finally:
            try:
                observer.close()
            except BaseException as error:
                report["cleanup_error"] = type(error).__name__
                report["observer_outcome"] = 1
    create(output / "manifest.json", json_bytes(report) + b"\n")
    print(json.dumps({key: report[key] for key in ("original_outcome", "observer_outcome", "publisher_opener_entries")}))
    return report["observer_outcome"]


if __name__ == "__main__":
    raise SystemExit(main())
