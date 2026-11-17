"""Scoped research harness for issue 428. GitHub Actions only and no publication.

Legacy publisher-origin bytes use a bounded non-redirecting transport here,
while owner selection, archive decoding, MAX restore, paced reservation and
journal writes run through the current Flanders adapter. A separate one-use
outside receipt distinguishes each physical pending event from assessed and
closed evidence; the hosted workflow must stay paused throughout.
"""
import base64
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
ROOT = None
ROOT_ID = None
ADAPTER = CHECKOUT / "adapters/be-study-in-flanders/adapter.py"
PROTOCOL = HERE / "protocol.py"
MAXIMUM = HERE / "campaign-max.json"
RUNS = HERE / "runs.json"
APPROVAL = {
    "decision": "https://github.com/YouthOpps/data-pipeline/issues/428#issuecomment-6961946776",
    "recipient_sha256": "c8bfa742ffcd2638a1926c8068b58c3f0cc9bcda7077b95ab3ac8a8ac5b01531",
}
BRANCH = "qa/428-flanders-hub"
WORKFLOW_PATH = ".github/workflows/qa-flanders428-hub.yml"
WORKFLOW = 378390050
OWNER = (44156178659, 1, "be-study-in-flanders", "32f5bb7ea4994872888459988f23e7cdf1818b91")
ARTIFACT = 14360159644
ARTIFACT_SHA = "10a83aff656087d26a947cf09c979cd95ad246be9cf76722641d4e94915601cb"
ORIGIN = "https://www.studyinflanders.be"
ROBOTS = ORIGIN + "/robots.txt"
HUB = ORIGIN + "/scholarships"
RAW_LIMIT = 1024 * 1024
AUTHORITY = None
INPUTS = None
RESULT = {'outcome': None, 'requests': [], 'journal': []}


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_input(path):
    """Read one reviewed file without accepting a substituted link or generation."""
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        named = path.lstat()
        raw = os.pread(descriptor, 1_500_001, 0)
        after = os.fstat(descriptor)
        assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        assert info.st_uid == os.getuid() and info.st_mode & 0o022 == 0
        assert (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino)
        assert (info.st_size, info.st_mtime_ns, info.st_ctime_ns) == (
            after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        assert 0 < len(raw) == info.st_size <= 1_500_000
        return raw
    finally:
        os.close(descriptor)


def gate():
    assert __debug__ and type(APPROVAL) is dict, "Bounded hosted Flanders capture is OFF"
    assert set(APPROVAL) == {"decision", "recipient_sha256"}
    assert re.fullmatch(
        r"https://github\.com/YouthOpps/data-pipeline/issues/428#issuecomment-[1-9][0-9]*",
        APPROVAL["decision"],
    )
    assert "DATA_SOURCE_TOKEN" not in os.environ and os.environ.get("GITHUB_TOKEN")
    assert "STUDYINFLANDERS_PACING_ARTIFACT_PATH" not in os.environ
    assert "STUDYINFLANDERS_PACING_MAINTENANCE" not in os.environ
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "YouthOpps/data-pipeline",
        "GITHUB_REF": "refs/heads/" + BRANCH,
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "capture",
        "GITHUB_WORKFLOW_REF": (
            "YouthOpps/data-pipeline/" + WORKFLOW_PATH + "@refs/heads/" + BRANCH
        ),
    }
    assert all(os.environ.get(key) == value for key, value in expected.items())
    head, run = os.environ["GITHUB_SHA"], os.environ["GITHUB_RUN_ID"]
    assert re.fullmatch("[0-9a-f]{40}", head) and re.fullmatch("[1-9][0-9]*", run)
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    assert event["created"] is True and event["deleted"] is False
    assert event["ref"] == expected["GITHUB_REF"]
    assert event["before"] == "0" * 40 and event["after"] == head
    assert event["repository"]["full_name"] == expected["GITHUB_REPOSITORY"]
    assert INPUTS is not None
    assert len(INPUTS) == 7
    for path, digest in INPUTS.items():
        assert sha(private_input(path)) == digest, str(path)
    assert INPUTS[HERE / "reviewer.pem"] == APPROVAL["recipient_sha256"]


def authenticate(adapter):
    """Bind the actual QA run, immutable branch and active sole capture job."""
    gate()
    run_id = int(os.environ["GITHUB_RUN_ID"])
    head = os.environ["GITHUB_SHA"]
    repository = "YouthOpps/data-pipeline"
    ref = adapter.workflow_api("/git/ref/heads/" + BRANCH)
    assert ref["ref"] == "refs/heads/" + BRANCH and ref["object"]["sha"] == head
    run = adapter.workflow_api(f"/actions/runs/{run_id}")
    assert run["id"] == run_id and run["head_sha"] == head
    assert run["event"] == "push" and run["run_attempt"] == 1
    assert run["head_branch"] == BRANCH and run["status"] == "in_progress"
    assert run["path"] == WORKFLOW_PATH and run["workflow_id"] != WORKFLOW
    assert run["repository"]["full_name"] == run["head_repository"]["full_name"] == repository
    history = adapter.workflow_api(
        f"/actions/workflows/{run['workflow_id']}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&per_page=2"
    )
    assert history["total_count"] == 1 and [r["id"] for r in history["workflow_runs"]] == [run_id]
    jobs = adapter.workflow_api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=2")
    assert jobs["total_count"] == len(jobs["jobs"]) == 1
    job, = jobs["jobs"]
    assert job["name"] == "capture" and job["status"] == "in_progress"
    assert job["run_id"] == run_id and job["run_attempt"] == 1
    assert job["head_sha"] == head and job["head_branch"] == BRANCH
    return dict(repository=repository, ref=expected_ref(), head_sha=head,
                run_id=run_id, run_attempt=1, workflow_id=run["workflow_id"],
                job_id=job["id"], workflow=WORKFLOW_PATH)


def expected_ref():
    return "refs/heads/" + BRANCH


def create_record(name, value):
    """Write one durable receipt in the originally allocated outside attempt directory."""
    raw = canonical(value)
    info = ROOT.lstat()
    assert (info.st_dev, info.st_ino) == ROOT_ID
    assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
    assert stat.S_IMODE(info.st_mode) == 0o700
    fd = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    outcome = None
    try:
        seen = os.fstat(fd)
        assert (info.st_dev, info.st_ino) == (seen.st_dev, seen.st_ino)
        child = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
        try:
            assert os.write(child, raw) == len(raw)
            os.fsync(child)
            assert os.pread(child, len(raw) + 1, 0) == raw
            target = os.fstat(child)
            named = os.stat(name, dir_fd=fd, follow_symlinks=False)
            assert (target.st_dev, target.st_ino) == (named.st_dev, named.st_ino)
        finally:
            os.close(child)
        os.fsync(fd)
        outcome = [target.st_dev, target.st_ino]
    finally:
        os.close(fd)
    return outcome


def matches_history(adapter):
    inventory = adapter.workflow_api(f'/actions/workflows/{WORKFLOW}/runs?per_page=100')
    prior = json.loads(RUNS.read_bytes())
    fields = ('id', 'run_attempt', 'head_sha', 'status', 'conclusion')
    def identities(value):
        return sorted(tuple(x[k] for k in fields) for x in value['workflow_runs'])
    assert inventory['total_count'] == prior['total_count'] == 42
    assert identities(inventory) == identities(prior)
    assert all(x['status'] == 'completed' for x in inventory['workflow_runs'])
    current = adapter.workflow_api(f'/actions/workflows/{WORKFLOW}')
    assert current['id'] == WORKFLOW and current['state'] == 'disabled_manually'
    assert current['path'] == '.github/workflows/fetch-be-study-in-flanders.yml'


def verify_preopener(adapter):
    """Recheck hosted ownership and the paused producer after all waits."""
    assert authenticate(adapter) == AUTHORITY
    matches_history(adapter)
    gate()


def wrap_restoration(adapter):
    """Use the original hosted transfer/decoder and widen only the known MAX."""
    download = adapter.download_inert_artifact
    merge = adapter.merge_budgets
    original_owner = adapter.latest_family_run
    floor = json.loads(private_input(MAXIMUM))

    def checked_download(identity, artifact_id, digest):
        assert identity == OWNER and artifact_id == ARTIFACT
        assert digest == "sha256:" + ARTIFACT_SHA
        incoming = download(identity, artifact_id, digest)
        RESULT["owned_archive"] = {"artifact": ARTIFACT, "sha256": ARTIFACT_SHA}
        return incoming

    def merged(local, received, now):
        assert "owned_archive" in RESULT
        return merge(merge(local, received, now), floor, now)

    def verified_owner():
        found = original_owner()
        assert found == OWNER
        return found

    adapter.download_inert_artifact = checked_download
    adapter.merge_budgets = merged
    adapter.latest_family_run = verified_owner
    return adapter._OPENER.open


def import_original_qa_adapter():
    """Only two role checks differ from the immutable production adapter."""
    raw = private_input(ADAPTER)
    assert sha(raw) == "a79c9a06c03ccfff0877ddcaa815c4610bff4e0531de019b1ac740fac621aec6"
    replacements = (
        (b'    hosted = os.environ.get("GITHUB_ACTIONS") == "true"\n',
         b'    hosted = os.environ.get("GITHUB_ACTIONS") == "true" and not _BOUNDED_HOSTED_QA\n'),
        (b'    elif os.environ.get("GITHUB_ACTIONS") == "true":\n'
         b'        raise AdapterError(\n            "Full-native-MAX production approval required", "access"\n        )',
         b'    elif os.environ.get("GITHUB_ACTIONS") == "true" and not _BOUNDED_HOSTED_QA:\n'
         b'        raise AdapterError(\n            "Full-native-MAX production approval required", "access"\n        )'),
    )
    qa = raw
    for original, replacement in replacements:
        assert qa.count(original) == 1
        qa = qa.replace(original, replacement, 1)
    reversed_qa = qa
    for original, replacement in reversed(replacements):
        assert reversed_qa.count(replacement) == 1
        reversed_qa = reversed_qa.replace(replacement, original, 1)
    assert reversed_qa == raw
    import types
    module = types.ModuleType("flanders428_qa_adapter")
    module.__file__ = str(ADAPTER)
    module._BOUNDED_HOSTED_QA = True
    exec(compile(qa, str(ADAPTER), "exec"), module.__dict__)
    return module


class Journal:
    """Use original verified descriptors for every actual budget transition."""

    def __init__(self, adapter):
        self.adapter = adapter

    def read(self):
        with self.adapter.locked_budget():
            state = self.adapter.load_budget()
        return state

    def save(self, state):
        # Rate specifications are converted to at least period/count spacing by
        # the REP code. Keep the actual directives in raw captured evidence.
        if 'rates' in state:
            state = {k: v for k, v in state.items() if k != 'rates'}
        with self.adapter.locked_budget():
            prior = self.adapter.load_budget()
            state['observed_at'] = max(state['observed_at'], time.time())
            state = self.adapter.merge_budgets(prior, state, time.time())
            self.adapter.save_budget(state)
            actual = self.adapter.journal_read()
            assert json.loads(actual) == state
        RESULT['journal'].append({'intended': state, 'sha256': sha(actual)})

    def fence_pending(self, expected):
        """Expose research pending state to the unchanged original collector."""
        adapter = self.adapter
        with adapter.locked_budget():
            state = adapter.load_budget()
            assert state == expected and not state['blocked']
            state['blocked'] = True
            state['observed_at'] = max(state['observed_at'], time.time())
            adapter.save_budget(state)
            raw = adapter.journal_read()
            assert json.loads(raw) == state
        RESULT['journal'].append({'pending_barrier': state, 'sha256': sha(raw)})
        return sha(raw)

    def settle(self, name, receipt, *, retain_block):
        """A completed receipt precedes any clear of the temporary barrier."""
        adapter = self.adapter
        with adapter.locked_budget():
            fenced = adapter.load_budget()
            raw = adapter.journal_read()
            assert fenced['blocked'] and json.loads(raw) == fenced
            receipt = dict(receipt, acknowledged_state_sha256=sha(raw),
                           block_retained=retain_block)
            create_record(name, receipt)
            assert adapter.journal_read() == raw
            if not retain_block:
                # This is the only non-OR transition. The original held
                # journal is rechecked after the exact one-use assessment.
                # No protocol error or unassessed status reaches this point.
                settled = dict(fenced, blocked=False)
                adapter.save_budget(settled)
                raw = adapter.journal_read()
                assert json.loads(raw) == settled
            else:
                settled = fenced
        RESULT['journal'].append({'settled': settled, 'sha256': sha(raw)})


@contextmanager
def response_deadline(adapter):
    """Bound reads and closure, restoring the original phase alarm."""
    previous = signal.getsignal(signal.SIGALRM)
    limit = min(20, adapter._PHASE_END - time.monotonic())
    assert limit > 0
    signal.signal(signal.SIGALRM, adapter.alarm_expired)
    signal.setitimer(signal.ITIMER_REAL, limit, 0.2)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        adapter.arm_alarm(adapter._PHASE_END)


def observed_request(adapter, protocol, journal, opener, url):
    gate()
    assert url in (ROBOTS, HUB)
    robots = url == ROBOTS
    frames = RESULT['requests']
    sequence = len(frames) + 1
    if robots:
        assert sequence == 1
    else:
        assert sequence == 2 and frames[0]['url'] == ROBOTS
        assert frames[0].get('assessed') and frames[0]['disposition'] == 'rep-assessed'
    inventory = not robots
    if inventory:
        adapter.wait_inventory_page()
    adapter.pace()
    before = journal.read()
    pending_digest = journal.fence_pending(before)
    wire = {'sequence': sequence, 'url': url, 'body_parts': [],
            'reserved_at': before['starts'][-1], 'state_sha256': pending_digest}
    create_record(f'request-{sequence}-pending.json', {
        k: v for k, v in wire.items() if k != 'body_parts'
    })
    RESULT['requests'].append(wire)
    req = urllib.request.Request(url, headers={'User-Agent': adapter._USER_AGENT}, method='GET')
    assert req.get_header('User-agent') == 'YouthOppsBot/1.0 (+https://youthopps.org/bot/)'
    # Both the original startup quiet and per-request pacing wait have ended.
    # Repeat the complete exact paused-run/path check immediately before wire.
    verify_preopener(adapter)
    with adapter.publisher_attempt(True, inventory=inventory):
        adapter.journal_preopener()
        response = None
        primary = None
        try:
            with response_deadline(adapter):
                try:
                    try:
                        timeout = adapter.request_timeout(True)
                        wire['physical_start'] = time.time()
                        wire['opener_monotonic'] = time.monotonic()
                        response = opener(req, timeout=timeout)
                    except urllib.error.HTTPError as response_error:
                        response = response_error
                    status = wire['status'] = response.status
                    assert type(status) is int and 100 <= status <= 599
                    status_error = None
                    try:
                        protocol.known_status(journal, status, robots)
                        if not robots and status == 451:
                            adapter.retain_publisher_hold()
                    except BaseException as error:
                        status_error = error
                    headers = response.headers
                    wire['headers'] = list(headers.raw_items())
                    if status_error is not None:
                        raise status_error
                    protocol.retry_after(journal, headers)
                    length = protocol.framing(headers)
                    bound = 65536 if robots else RAW_LIMIT
                    if length is not None and length > bound:
                        raise RuntimeError('Declared response exceeds bounded capture')
                    if headers.get_all('Content-Encoding', []) not in ([], ['identity']):
                        raise RuntimeError('Unsupported capture body encoding')
                    try:
                        body = response.read(bound + 1)
                    except BaseException as error:
                        partial = getattr(error, 'partial', b'')
                        if isinstance(partial, bytes) and partial:
                            wire['body_parts'].append(partial)
                        raise
                    wire['body_parts'].append(body)
                    if len(body) > bound or length is not None and len(body) != length:
                        raise RuntimeError('Incomplete or oversized capture')
                    protocol.challenge(journal, body, headers, robots, status)
                except BaseException as error:
                    primary = error
                    raise
                finally:
                    if response is not None:
                        try:
                            response.close()
                        except BaseException as close_error:
                            wire['close_error'] = type(close_error).__name__
                            if primary is None:
                                raise
                        else:
                            wire['closed'] = True
                            wire['close_monotonic'] = time.monotonic()
        finally:
            wire['physical_complete'] = time.time()
    # Original completion is acknowledged, but the barrier still encloses
    # REP assessment and its durable policy save.
    adapter.journal_verify()
    retained = journal.read()
    if status == 429:
        assert retained['rate_limit_strikes'] == before['rate_limit_strikes'] + 1
        assert retained['interval'] >= before['interval'] * 2
        assert retained['page_interval'] >= before['page_interval'] * 2
        assert retained['rate_limit_until'] >= wire['physical_start'] + 1800
        disposition = 'timed-stop'
        retain_block = False
    elif robots:
        retain_block = not target_policy(protocol, journal, body, status)
        disposition = 'rep-denied' if retain_block else 'rep-assessed'
    else:
        if status != 200:
            raise RuntimeError(f'Official hub returned HTTP {status}')
        RESULT['hub_original_fingerprint'] = adapter.html_fingerprint(body, 'hub')
        disposition = 'hub-assessed'
        retain_block = False
    receipt = {
        'url': url, 'status': status, 'body_sha256': sha(body), 'bytes': len(body),
        'completion': wire['physical_complete'], 'verified_close': True,
        'disposition': disposition, 'pending_state_sha256': pending_digest,
    }
    journal.settle(f'request-{sequence}-assessed.json', receipt, retain_block=retain_block)
    wire.update(assessed=True, disposition=disposition)
    return status, body


def target_policy(protocol, journal, body, status):
    protocol.ENTRY = HUB
    class PolicyJournal:
        def read(self):
            # REP parser collects request-rate directives in memory. Its stricter
            # spacing is persisted in the original field by Journal.save.
            return dict(journal.read(), rates=[])
        def save(self, state):
            journal.save(state)
    newer, legacy, unavailable = protocol.robot_policy(PolicyJournal(), body, status)
    RESULT['rep'] = {'newer': newer, 'legacy': legacy, 'unavailable': unavailable}
    return newer and legacy


def capture_state(adapter):
    """Capture exact acknowledged bytes before the original descriptors close."""
    if not adapter._STATE_TRUSTED:
        return None
    with adapter.locked_budget():
        adapter.journal_verify()
        first = adapter.journal_read()
        state = adapter.validate_budget(json.loads(first))
        adapter.journal_verify()
        assert adapter.journal_read() == first
        info = os.fstat(adapter._GUARD_FD)
        assert info.st_size == len(first)
        return {"state": state, "exact_journal_base64": base64.b64encode(first).decode(),
                "sha256": sha(first), "identity": [info.st_dev, info.st_ino]}


def research(adapter, protocol):
    opener = wrap_restoration(adapter)
    import tempfile
    assert tempfile.gettempdir() == "/tmp"
    assert not os.path.lexists(Path("/tmp") / f"{adapter.FAMILY}-{os.getuid()}")
    with adapter.execution():
        with adapter.phase("prepublication", adapter.COLLECTION_TIMEOUT):
            try:
                matches_history(adapter)
                adapter.prepare_collection()
                initial = Journal(adapter).read()
                assert not initial["blocked"] and initial["legacy_rate_history_unknown"]
                observed_request(adapter, protocol, Journal(adapter), opener, ROBOTS)
                if RESULT["requests"][-1]["disposition"] == "rep-assessed":
                    observed_request(adapter, protocol, Journal(adapter), opener, HUB)
            finally:
                # A failure to verify the original custody is never repaired
                # by reporting an older acknowledgement as the current state.
                primary = sys.exc_info()[1]
                try:
                    RESULT["snapshot"] = capture_state(adapter)
                except BaseException as error:
                    RESULT["snapshot_error"] = type(error).__name__
                    if primary is None:
                        raise


def validate_timing():
    wires = [wire for wire in RESULT["requests"] if "physical_start" in wire]
    if len(wires) == 2:
        gap = wires[1]["opener_monotonic"] - wires[0]["close_monotonic"]
        assert gap >= 12.2
    assert [wire["url"] for wire in wires] in ([], [ROBOTS], [ROBOTS, HUB])
    return wires


def encode_bytes(value):
    assert type(value) is bytes
    return {"base64": base64.b64encode(value).decode()}


def encrypt_evidence(raw):
    result = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
         "-recip", str(HERE / "reviewer.pem")],
        input=raw, capture_output=True, timeout=25, check=False,
    )
    return result.stdout if result.returncode == 0 and result.stdout else None


def main():
    global AUTHORITY, INPUTS, ROOT, ROOT_ID
    root = Path(os.environ["RUNNER_TEMP"])
    assert root.is_absolute() and root == root.resolve()
    root_info = root.lstat()
    assert stat.S_ISDIR(root_info.st_mode) and root_info.st_uid == os.getuid()
    ROOT = root / "flanders428-research"
    ROOT.mkdir(mode=0o700)
    info = ROOT.lstat()
    ROOT_ID = (info.st_dev, info.st_ino)
    head = os.environ["GITHUB_SHA"]
    assert re.fullmatch("[0-9a-f]{40}", head)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(CHECKOUT), *args], timeout=15)

    assert git("rev-parse", "HEAD").decode().strip() == head
    scoped = (Path(__file__).resolve(), ADAPTER, PROTOCOL, MAXIMUM, RUNS,
              HERE / "reviewer.pem", CHECKOUT / WORKFLOW_PATH)
    INPUTS = {}
    for path in scoped:
        relative = path.relative_to(CHECKOUT).as_posix()
        exact = private_input(path)
        assert git("show", f"{head}:{relative}") == exact, relative
        INPUTS[path] = sha(exact)
    gate()
    adapter = import_original_qa_adapter()
    protocol = load_module(PROTOCOL, "flanders428_original_policy")
    create_record("attempt.json", {"state": "unresolved", "head_sha": head,
                  "decision": APPROVAL["decision"], "run_id": int(os.environ["GITHUB_RUN_ID"])})
    try:
        with adapter.execution():
            with adapter.phase("prepublication", 45):
                AUTHORITY = authenticate(adapter)
                matches_history(adapter)
                # Confirm the pinned recipient works before opening the journal
                # or attempting any publisher request.
                probe = b"Flanders QA recipient preflight"
                sealed = encrypt_evidence(probe)
                assert sealed and probe not in sealed, "CMS evidence recipient is unusable"
        # Adapter execution contexts do not reset budget or custody here:
        # authentication before initialization never opens a family journal.
        research(adapter, protocol)
        validate_timing()
        RESULT["outcome"] = "captured" if (
            len(RESULT["requests"]) == 2 and
            RESULT["requests"][-1].get("disposition") == "hub-assessed"
        ) else "stopped"
    except BaseException as error:
        RESULT["outcome"] = "stopped"
        RESULT["error"] = type(error).__name__

    # Source bodies and diagnostics stay in RAM, with only authenticated
    # encrypted evidence leaving the ephemeral runner.
    snapshot = RESULT.get("snapshot")
    RESULT["authority"] = AUTHORITY
    RESULT["decision"] = APPROVAL["decision"]
    RESULT["input_pins"] = {str(path.relative_to(CHECKOUT)): h for path, h in INPUTS.items()}
    payload = json.dumps(RESULT, default=encode_bytes, sort_keys=True, allow_nan=False).encode()
    cipher = encrypt_evidence(payload)
    complete = cipher is not None
    if complete:
        directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            assert (os.fstat(directory).st_dev, os.fstat(directory).st_ino) == ROOT_ID
            fd = os.open("capture.cms", os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                         0o600, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb", closefd=False) as file:
                    file.write(cipher)
                    file.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(directory)
        finally:
            os.close(directory)
    if snapshot:
        from copy import deepcopy
        state = deepcopy(snapshot["state"])
        floor = json.loads(private_input(MAXIMUM))
        for wire in RESULT["requests"]:
            for key in ("reserved_at", "physical_start"):
                if key in wire:
                    floor["starts"].append(wire[key])
        floor["starts"] = sorted(set(floor["starts"]))
        carry = adapter.merge_budgets
        # The runtime merge is used only after the successful exact archive
        # lookup, and all unresolved/disallowed evidence remains held.
        complete_states = RESULT.get("owned_archive") is not None
        if complete_states:
            merged = carry(state, floor, time.time())
            if RESULT.get("snapshot_error") or adapter._GUARD_FATAL:
                merged["blocked"] = True
            create_record("carry-forward-review-only.json", {
                "original_journal": snapshot, "conservative_state": merged,
                "outcome": RESULT["outcome"], "authenticated_owner": AUTHORITY,
            })
    rows = [{k: v for k, v in wire.items() if k not in ("body_parts", "headers")}
            for wire in RESULT["requests"]]
    create_record("result.json", {
        "outcome": RESULT["outcome"], "error": RESULT.get("error"),
        "authority": AUTHORITY, "wire_observations": rows,
        "encrypted_capture_sha256": sha(cipher) if complete else None,
        "snapshot_sha256": snapshot["sha256"] if snapshot else None,
        "unresolved": (snapshot is None or snapshot["state"]["blocked"] or
                       adapter._GUARD_FATAL or not complete),
        "production_or_native_test": False,
    })
    print("Bounded Flanders capture:", RESULT["outcome"], "observations", len(rows))
    if RESULT["outcome"] != "captured" or not complete or not snapshot:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
