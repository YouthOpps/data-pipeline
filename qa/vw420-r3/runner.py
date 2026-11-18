"""Execute the unchanged VW test in the exact nonpublishing hosted lane."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import signal
import stat
import subprocess
import sys
import time

from observer import Observer, digest, encode, require

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
WEBSITE = CHECKOUT / "qa-website"
SOURCE = CHECKOUT / "adapters/volkswagen-group/adapter.py"
TEST = SOURCE.with_name("test_adapter.py")
WORKFLOW = CHECKOUT / ".github/workflows/qa-vw420-native-r3.yml"
TEST_SHA256 = "18ffca3b510c0e6fcf94ceea627172392dfb7a4b598080fb6eeee376f459b1c5"
SEED_SHA256 = "42c06efd57ce5b5bb388ca982fcb091efd61b0c487988d246ef0c2269eab92fc"
REVIEWER_SHA256 = "c8bfa742ffcd2638a1926c8068b58c3f0cc9bcda7077b95ab3ac8a8ac5b01531"
CONTINUITY_SHA256 = "a52762bf27b5b6b1e2e3fd1fe8ed741bf45e1ccc52b34928c8c6a85c9bbd233e"
PRIOR_ACK_SHA256 = "1af2ff8c972c7e11a3cd4c13d670863941729f155dcba43c16ba98ecd3528f0f"
WEBSITE_SHA = "4987b37f306cee6f9ba76acd26be7ffb61f0a1f7"
WEBSITE_FILES = {
    "scripts/data.mjs": "bc9d9f9f83c58061c6d29f3fd3d6b04b56ad1b7503b12a009c2f91e24b4deef3",
    "assets/discovery.js": "39dccc7be14705e2c338ae065044b2f6736d5e0b7bec70436937ad543a8741bb",
}
FORBIDDEN = {
    "DATA_SOURCE_TOKEN", "WEBSITE_APP_ID", "WEBSITE_APP_PRIVATE_KEY",
    "WEBSITE_APP_INSTALLATION_ID", "VOLKSWAGEN_GROUP_PACING_ARTIFACT_PATH",
}


def write_exclusive(path, data):
    with path.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=encode).encode()


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, timeout=30)


def generation(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink)


class Claim:
    def __init__(self, root, payload):
        self.root = root
        self.raw = serialized(payload)
        self.name = "vw420-native-original.claim"
        self.parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.parent_identity = generation(os.fstat(self.parent))[:2]
        self.fd = None
        try:
            self.fd = os.open(self.name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=self.parent)
            require(os.write(self.fd, self.raw) == len(self.raw), "Incomplete QA claim reservation")
            os.fsync(self.fd)
            os.fsync(self.parent)
            self.version = generation(os.fstat(self.fd))
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self):
        require(generation(os.fstat(self.parent))[:2] == self.parent_identity
                == generation(os.lstat(self.root))[:2], "Original QA claim parent changed")
        before = generation(os.fstat(self.fd))
        raw = os.pread(self.fd, len(self.raw) + 1, 0)
        after = generation(os.fstat(self.fd))
        current = generation(os.stat(self.name, dir_fd=self.parent, follow_symlinks=False))
        require(before == after == current == self.version and raw == self.raw
                and before[5:] == (os.getuid(), 0o600, 1),
                "Original QA claim no longer owns exact bytes")
        return {"parent": self.parent_identity, "generation": self.version, "sha256": digest(raw)}

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        os.close(self.parent)


def exact_checkout():
    head = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", head) and
            git(CHECKOUT, "rev-parse", "HEAD").decode().strip() == head,
            "Original GitHub checkout differs")
    require(git(WEBSITE, "rev-parse", "HEAD").decode().strip() == WEBSITE_SHA,
            "Pinned website checkout differs")
    files = (SOURCE, TEST, WORKFLOW, HERE / "runner.py", HERE / "observer.py",
             HERE / "consumer.mjs", HERE / "seed.json", HERE / "continuity.json",
             HERE / "prior-ack.json", HERE / "reviewer.pem")
    pins = {}
    for path in files:
        rel = str(path.relative_to(CHECKOUT))
        pins[path] = digest(git(CHECKOUT, "show", f"{head}:{rel}"))
    for rel, reference in WEBSITE_FILES.items():
        require(digest(git(WEBSITE, "show", f"{WEBSITE_SHA}:{rel}")) == reference,
                "Pinned website revision bytes differ")
        pins[WEBSITE / rel] = reference
    require(pins[TEST] == TEST_SHA256 and pins[HERE / "seed.json"] == SEED_SHA256
            and pins[HERE / "continuity.json"] == CONTINUITY_SHA256
            and pins[HERE / "prior-ack.json"] == PRIOR_ACK_SHA256
            and pins[HERE / "reviewer.pem"] == REVIEWER_SHA256,
            "Exact original test, seed or reviewer certificate differs")
    return head, pins


def verify_pins(pins):
    require(all(digest(path.read_bytes()) == value for path, value in pins.items()),
            "Retained QA checkout bytes changed")


def adapter_module():
    spec = importlib.util.spec_from_file_location("adapter", SOURCE)
    adapter = importlib.util.module_from_spec(spec)
    sys.modules["adapter"] = adapter
    spec.loader.exec_module(adapter)
    return adapter


def unchanged_test(stdout, stderr):
    argv = sys.argv
    sys.argv = [str(TEST)]
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                runpy.run_path(str(TEST), run_name="__main__")
            except SystemExit as result:
                return result.code is False or type(result.code) is int and result.code == 0
        return False
    finally:
        sys.argv = argv


def site_check(adapter, stdout):
    native = json.loads(stdout)
    adapter.validate_records(native, require_complete=True)
    checked = datetime.now(timezone.utc)
    records = adapter.remove_expired_records(native, checked)
    adapter.validate_records(records, allow_empty=True)
    at = adapter.utc_now()
    outcome = adapter.metadata(at, {}, records, checked_at=at)
    result = subprocess.run(
        ["node", str(HERE / "consumer.mjs")], input=serialized({"records": records, "metadata": outcome}),
        capture_output=True, check=True, timeout=30,
    )
    consumer = json.loads(result.stdout)
    require(consumer["count"] == len(records) and consumer["sources"] == [adapter.SOURCE_ID],
            "Website snapshot source/count differs")
    return {"native_count": len(native), "retained_count": len(records), "consumer": consumer,
            "native_stdout_sha256": digest(stdout), "records_sha256": digest(serialized(records))}


def seal_capture(recipient, value):
    process = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER",
         "-aes-256-gcm", "-recip", str(recipient)], input=serialized(value),
        capture_output=True, timeout=150, check=True,
    )
    require(process.stdout, "Complete encrypted QA evidence is empty")
    return process.stdout


def encrypt(output, recipient, value):
    sealed = seal_capture(recipient, value)
    write_exclusive(output / "capture.cms", sealed)
    return digest(sealed)


def main():
    require(os.environ.get("GITHUB_ACTIONS") == "true" and not FORBIDDEN.intersection(os.environ),
            "Verified Actions without publication credentials required")
    root = Path(os.environ["RUNNER_TEMP"])
    require(root.is_absolute() and root.resolve() == root, "Absolute native runner temp required")
    recipient = HERE / "reviewer.pem"
    output = root / "vw420-native-r3-qa"
    output.mkdir(mode=0o700)
    head, pins = exact_checkout()
    verify_pins(pins)
    continuity = json.loads((HERE / "continuity.json").read_bytes())
    observations = continuity["observations"]["full_union"]
    seed = json.loads((HERE / "seed.json").read_bytes())
    wire_marks = [
        value for wire in continuity["previous_qa_wires"]
        for value in (wire["reserve"], wire["actual_start"])
    ]
    prior = continuity["prior_continuity"]
    original_marks = prior["previous_research"]["observations"]["full_union"]
    first_qa_marks = [
        value for wire in prior["previous_qa_wires"]
        for value in (wire["reserve"], wire["actual_start"])
    ]
    historical = prior["observations"]["full_union"]
    require(len(original_marks) == 187 and len(first_qa_marks) == 10
            and sorted(set(original_marks + first_qa_marks)) == historical
            and len(historical) == 197 and len(wire_marks) == 12
            and sorted(set(historical + wire_marks)) == observations and len(observations) == 209
            and seed["starts"] == observations[-10:]
            and continuity["previous_qa"]["exact_last_ack_sha256"] == PRIOR_ACK_SHA256,
            "Full original timing history does not bind its rolling seed")
    try:
        seal_capture(recipient, {"kind": "vw420-evidence-preflight"})
    except (OSError, subprocess.SubprocessError, RuntimeError) as failure:
        result = {"schema": 1, "qualification": "unqualified",
                  "stage": "cms_preflight", "error_type": type(failure).__name__,
                  "head": head, "run_id": os.environ["GITHUB_RUN_ID"],
                  "attempt": os.environ["GITHUB_RUN_ATTEMPT"], "physical_count": 0,
                  "ack_count": 0, "encrypted_capture_sha256": None}
        write_exclusive(output / "result.json", serialized(result))
        print("VW evidence encryption preflight failed before the original test")
        return 1
    payload = {"source": "volkswagen-group", "head": head, "started_at": time.time(),
               "run_id": os.environ["GITHUB_RUN_ID"], "attempt": os.environ["GITHUB_RUN_ATTEMPT"],
               "ref": os.environ["GITHUB_REF"], "job": os.environ["GITHUB_JOB"],
               "pins": {str(p.relative_to(CHECKOUT)): v for p, v in pins.items()}}
    claim = Claim(root, payload)
    claim_identity = claim.verify()
    stdout, stderr = io.StringIO(), io.StringIO()
    observer = None
    site = wires = error = sealed = None
    qualified = False
    custody = None

    def witness():
        require(claim.verify() == claim_identity, "Original QA claim changed")
        verify_pins(pins)
        return payload["pins"]

    def interruption(signum, _frame):
        raise InterruptedError(signum)

    signals = [signal.SIGINT, signal.SIGTERM]
    prior = {sig: signal.signal(sig, interruption) for sig in signals}
    try:
        adapter = adapter_module()
        require(adapter.hosted_qa_mode() and
                adapter.protocol_digest(adapter._HOSTED_QA_APPROVAL["state"]) == SEED_SHA256,
                "Exact QA activation remains OFF")
        adapter._HOSTED_QA_WITNESS = witness
        adapter._HOSTED_QA_PRIOR_ACK = (HERE / "prior-ack.json").read_bytes()
        observer = Observer(adapter, witness)
        passed = unchanged_test(stdout, stderr)
        if observer.finished and not adapter._GUARD_FATAL:
            custody = observer.verify_released()
            require(custody == observer.finished_generation,
                    "Original journal changed since collector release")
        require(passed, "Original native unittest failed")
        raw_stdout = stdout.getvalue().encode("utf-8")
        site = site_check(adapter, raw_stdout)
        wires = observer.derive_wires()
        require(wires and len(wires) == adapter._REQUEST_STARTS,
                "Original durable reservation and observed physical-open counts differ")
        require(observer.finished and not adapter._GUARD_FATAL and observer.last_ack is not None,
                "Original journal close could not be qualified")
        state = adapter.validate_budget(observer.last_ack["intended"])
        require(not state["blocked"] and not state["in_flight"] and state["policy_pending"] is None,
                "Settled original state retained a hold or incomplete owner")
        require(custody is not None, "Original journal release custody is unavailable")
        witness()
        qualified = True
    except BaseException as failure:
        error = type(failure).__name__
    finally:
        for sig, previous in prior.items():
            signal.signal(sig, previous)

    context = observer.context if observer else None
    last_ack = observer.last_ack if observer else None
    if context is None and observer is not None:
        context = getattr(observer.adapter, "_PROTOCOL_CONTEXT", None)
    owner = context.get("qa_owner") if context else None
    summary = {
        "schema": 1, "qualification": "qualified" if qualified else "unqualified",
        "identity": owner, "original_claim": claim_identity, "witness": payload,
        "error_type": error, "site": site, "wires": wires, "released_generation": custody,
        "physical_count": len(observer.wires) if observer else 0,
        "ack_count": sum(row["acknowledged"] for row in observer.journals) if observer else 0,
        "last_original_ack": {k: last_ack[k] for k in
                              ("generation", "sha256", "parent_identity", "collection_identity")}
                             if last_ack else None,
    }
    try:
        sealed = encrypt(output, recipient, {
            "manifest": summary, "claim_bytes": claim.raw,
            "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
            "wires": observer.wires if observer else [],
            "journals": observer.journals if observer else [],
            "original_context": context, "original_full_continuity": continuity,
        })
        if custody is not None:
            require(claim.verify() == claim_identity and observer.verify_released() == custody,
                    "Original claim or final ACK changed after evidence encryption")
    except BaseException as failure:
        qualified = False
        summary["qualification"] = "unqualified"
        summary["retention_error_type"] = type(failure).__name__
        summary["released_generation"] = None
    finally:
        if observer:
            observer.close()
        claim.close()
    summary["encrypted_capture_sha256"] = sealed
    write_exclusive(output / "result.json", serialized(summary))
    print(json.dumps({"qualification": summary["qualification"], "physical_count": summary["physical_count"],
                      "ack_count": summary["ack_count"], "evidence_retained": sealed is not None}))
    return 0 if qualified and sealed else 1


if __name__ == "__main__":
    raise SystemExit(main())
