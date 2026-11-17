"""Observe one exact original Flanders live test in nonpublishing GitHub Actions."""
import base64
import contextlib
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from zipfile import ZipFile

import rep

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
WEBSITE = CHECKOUT / "qa-website"
WEBSITE_SHA = "7da2b3eb81853707def0ff67de64f8d43773fe87"
WEBSITE_PINS = {
    "scripts/data.mjs": "bc9d9f9f83c58061c6d29f3fd3d6b04b56ad1b7503b12a009c2f91e24b4deef3",
    "assets/discovery.js": "39dccc7be14705e2c338ae065044b2f6736d5e0b7bec70436937ad543a8741bb",
    "package.json": "7cd869b0bff66038195746c4bac649e863b140c6b6505b39bed557ec1b4b1833",
}
ADAPTER = CHECKOUT / "adapters/be-study-in-flanders/adapter.py"
TEST = ADAPTER.with_name("test_adapter.py")
RUNS = HERE / "runs.json"
CARRY = HERE / "recovered-carry-review-only.json"
CUSTODY = HERE / "custody.json"
CERT = HERE / "reviewer.pem"
ROOT = ROOT_ID = AUTHORITY = INPUTS = None
APPROVAL = {
    "decision": "https://github.com/YouthOpps/data-pipeline/issues/428#issuecomment-6963001788",
    "recipient_sha256": "c8bfa742ffcd2638a1926c8068b58c3f0cc9bcda7077b95ab3ac8a8ac5b01531",
}
BRANCH = "qa/428-flanders-native"
WORKFLOW_PATH = ".github/workflows/qa-flanders428-native.yml"
WORKFLOW = 378390050
PRIOR_WORKFLOW = 421912393
PRIOR_HEAD = "f2c619de5b66c2071ba9b4e408a3e6d747e9ecc8"
PRIOR_BRANCH = "qa/428-flanders-hub"
PRIOR_RUN, PRIOR_JOB, PRIOR_ARTIFACT = 44183750124, 134394644238, 14373437413
PRIOR_SHA = "3d33ee1b0e0a4e65dba2deae23b43c6b5253c6124143e1bb0d70e72353ab95c8"
OWNER = (44156178659, 1, "be-study-in-flanders", "32f5bb7ea4994872888459988f23e7cdf1818b91")
ARTIFACT = 14360159644
ARTIFACT_SHA = "10a83aff656087d26a947cf09c979cd95ad246be9cf76722641d4e94915601cb"
ADAPTER_SHA = "81e4223cfebcf1c012e71cb86f4a99ac1a8a88fa32267fdf016b33bfe8d4e47c"
TEST_SHA = "e502f276152f1f20ec370a5f786bc888582b34b3cd122154e2574eec879e9155"
CARRY_SHA = "72ac638143d0840f1fbe6e6a4aa93a8c57c806160c52ad0834dd6d70005780b7"
CUSTODY_SHA = "e8979f82b0c6adf9e1b1d0232566081a6a70014f8ac5216e4b00ecbc39cc1db3"
CERT_SHA = "c8bfa742ffcd2638a1926c8068b58c3f0cc9bcda7077b95ab3ac8a8ac5b01531"
ORIGIN = "https://www.studyinflanders.be"
ROBOTS = ORIGIN + "/robots.txt"


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
    if not __debug__ or type(APPROVAL) is not dict:
        raise RuntimeError("Hosted Flanders native test is OFF")
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
        "GITHUB_JOB": "native",
        "GITHUB_WORKFLOW_REF": (
            "YouthOpps/data-pipeline/" + WORKFLOW_PATH + "@refs/heads/" + BRANCH
        ),
    }
    assert all(os.environ.get(key) == value for key, value in expected.items())
    head, run = os.environ["GITHUB_SHA"], os.environ["GITHUB_RUN_ID"]
    assert os.environ.get("GITHUB_WORKFLOW_SHA") == head
    assert re.fullmatch("[0-9a-f]{40}", head) and re.fullmatch("[1-9][0-9]*", run)
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    assert event["created"] is True and event["deleted"] is False
    assert event["ref"] == expected["GITHUB_REF"]
    assert event["before"] == "0" * 40 and event["after"] == head
    assert event["repository"]["full_name"] == expected["GITHUB_REPOSITORY"]
    assert INPUTS is not None
    assert len(INPUTS) == 10
    for path, digest in INPUTS.items():
        assert sha(private_input(path)) == digest, path.name
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
    assert run["path"] == WORKFLOW_PATH and run["workflow_id"] not in {WORKFLOW, PRIOR_WORKFLOW}
    assert run["repository"]["full_name"] == run["head_repository"]["full_name"] == repository
    assert run["repository"]["id"] == run["head_repository"]["id"] == 1163601208
    workflow = adapter.workflow_api(f"/actions/workflows/{run['workflow_id']}")
    assert workflow["path"] == WORKFLOW_PATH and workflow["state"] == "active"
    history = adapter.workflow_api(
        f"/actions/workflows/{run['workflow_id']}/runs"
        f"?branch={urllib.parse.quote(BRANCH, safe='')}&per_page=2"
    )
    assert history["total_count"] == 1 and [r["id"] for r in history["workflow_runs"]] == [run_id]
    jobs = adapter.workflow_api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=2")
    assert jobs["total_count"] == len(jobs["jobs"]) == 1
    job, = jobs["jobs"]
    assert job["name"] == "native" and job["status"] == "in_progress"
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
    verify_prior(adapter)
    gate()


def import_original_qa_adapter():
    """Only two role checks differ from the immutable production adapter."""
    raw = private_input(ADAPTER)
    assert sha(raw) == ADAPTER_SHA
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
    module = types.ModuleType("adapter")
    module.__file__ = str(ADAPTER)
    module._BOUNDED_HOSTED_QA = True
    exec(compile(qa, str(ADAPTER), "exec"), module.__dict__)
    return module


def prior_artifact_metadata(adapter):
    result = adapter.workflow_api(f"/actions/runs/{PRIOR_RUN}/artifacts?per_page=2")
    artifact, = result["artifacts"]
    assert result["total_count"] == 1 and artifact["id"] == PRIOR_ARTIFACT
    assert artifact["expired"] is False and artifact["size_in_bytes"] == 55604
    assert artifact["digest"] == "sha256:" + PRIOR_SHA
    assert artifact["name"] == f"flanders428-bounded-research-{PRIOR_RUN}-1"
    owner = artifact["workflow_run"]
    assert (owner["id"], owner["head_branch"], owner["head_sha"]) == (
        PRIOR_RUN, PRIOR_BRANCH, PRIOR_HEAD,
    )
    assert owner["repository_id"] == owner["head_repository_id"] == 1163601208
    return artifact


def verify_prior(adapter):
    workflow = adapter.workflow_api(f"/actions/workflows/{PRIOR_WORKFLOW}")
    assert workflow["state"] == "disabled_manually" and workflow["id"] == PRIOR_WORKFLOW
    assert workflow["path"] == ".github/workflows/qa-flanders428-hub.yml"
    lineage = adapter.workflow_api(f"/actions/workflows/{PRIOR_WORKFLOW}/runs?per_page=2")
    run, = lineage["workflow_runs"]
    assert lineage["total_count"] == 1
    assert (run["id"], run["run_attempt"], run["head_sha"], run["head_branch"]) == (
        PRIOR_RUN, 1, PRIOR_HEAD, PRIOR_BRANCH,
    )
    assert run["status"] == "completed" and run["conclusion"] == "failure"
    assert run["repository"]["id"] == run["head_repository"]["id"] == 1163601208
    ref = adapter.workflow_api("/git/ref/heads/" + PRIOR_BRANCH)
    assert ref["ref"] == "refs/heads/" + PRIOR_BRANCH and ref["object"]["sha"] == PRIOR_HEAD
    result = adapter.workflow_api(f"/actions/runs/{PRIOR_RUN}/attempts/1/jobs?per_page=2")
    job, = result["jobs"]
    assert result["total_count"] == 1
    assert (job["id"], job["run_id"], job["run_attempt"], job["head_sha"]) == (
        PRIOR_JOB, PRIOR_RUN, 1, PRIOR_HEAD,
    )
    assert job["status"] == "completed" and job["conclusion"] == "failure"
    artifact = prior_artifact_metadata(adapter)
    return job, artifact


def download_prior(adapter):
    """Fetch only the pinned GitHub-owned diagnostic archive, without blob credentials."""
    request = urllib.request.Request(
        adapter._WORKFLOW_GITHUB + f"/actions/artifacts/{PRIOR_ARTIFACT}/zip",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                 "User-Agent": adapter._USER_AGENT},
    )
    try:
        response = adapter._OPENER.open(request, timeout=adapter.request_timeout())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        assert response.status == 302
        location, = response.headers.get_all("Location", [])
    target = urllib.parse.urlsplit(location)
    assert target.scheme == "https" and not target.fragment
    assert target.hostname and target.netloc.lower() == target.hostname
    assert target.hostname.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))
    target_request = urllib.request.Request(location, headers={"User-Agent": adapter._USER_AGENT})
    try:
        with adapter._OPENER.open(target_request, timeout=adapter.request_timeout()) as response:
            assert response.status == 200
            body = response.read(55605)
    except (urllib.error.URLError, OSError):
        raise RuntimeError("Owned diagnostic archive transfer failed") from None
    assert len(body) == 55604 and sha(body) == PRIOR_SHA
    return body


def recover_floor(adapter, job, archive_body):
    assert sha(private_input(CARRY)) == CARRY_SHA and sha(private_input(CUSTODY)) == CUSTODY_SHA
    carry = json.loads(private_input(CARRY))
    custody = json.loads(private_input(CUSTODY))
    assert carry["status"] == "review-only" and carry["source"] == adapter.SOURCE_ID
    assert carry["archive_id"] == custody["archive_id"] == PRIOR_ARTIFACT
    assert carry["archive_sha256"] == custody["archive_sha256"] == sha(archive_body) == PRIOR_SHA
    assert custody["run"] == PRIOR_RUN and custody["job"] == PRIOR_JOB
    assert custody["job_ended"] == job["completed_at"]
    assert carry["snapshot_sha256"] == custody["exact_journal_sha256"]
    assert carry["latest_actual_owner"]["head_sha"] == PRIOR_HEAD
    assert custody["rep"] == {"legacy": True, "newer": True, "unavailable": False}
    with ZipFile(io.BytesIO(archive_body)) as archive:
        paths = ["attempt.json", "capture.cms"] + [
            f"request-{i}-{stage}.json" for i in (1, 2) for stage in ("pending", "assessed")
        ]
        assert sorted(archive.namelist()) == sorted(paths)
        assert all(item.file_size < 65536 and not item.flag_bits & 1 for item in archive.infolist())
        assert sha(archive.read("capture.cms")) == custody["cipher_sha256"]
        attempt = json.loads(archive.read("attempt.json"))
        assert attempt["run_id"] == PRIOR_RUN and attempt["head_sha"] == PRIOR_HEAD
        for sequence, wire in enumerate(custody["physical_wires"], 1):
            pending = json.loads(archive.read(f"request-{sequence}-pending.json"))
            assessed = json.loads(archive.read(f"request-{sequence}-assessed.json"))
            assert pending["state_sha256"] == wire["state_sha256"] == assessed["pending_state_sha256"]
            assert pending["url"] == wire["url"] == assessed["url"]
            assert assessed["status"] == wire["status"] == 200 and assessed["verified_close"]
            assert assessed["block_retained"] is False and wire["closed"] and wire["assessed"]
            assert assessed["body_sha256"] == wire["body_sha256"]
            assert assessed["completion"] == wire["physical_complete"]
    floor = adapter.validate_budget(carry["state"])
    assert len(floor["starts"]) == 244 and not floor["blocked"] and floor["legacy_rate_history_unknown"]
    assert len(custody["physical_wires"]) == 2
    for wire in custody["physical_wires"]:
        assert wire["reserved_at"] in floor["starts"] and wire["physical_start"] in floor["starts"]
        assert floor["completed"] >= wire["physical_complete"]
    assert floor["last_inventory_completed"] >= custody["physical_wires"][-1]["physical_complete"]
    completion = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00")).timestamp()
    assert floor["observed_at"] >= completion and floor["not_before"] >= completion + 120
    return floor


def snapshot(adapter):
    """Read the exact acknowledged original generation under its original lock."""
    with adapter.locked_budget():
        adapter.journal_verify()
        raw = adapter.journal_read()
        state = adapter.validate_budget(json.loads(raw))
        info = os.fstat(adapter._GUARD_FD)
        assert info.st_size == len(raw) and adapter.journal_read() == raw
        parent = os.fstat(adapter._STATE_DIR_FD)
        return {"raw": raw, "state": state, "sha256": sha(raw),
                "generation": list(adapter.journal_generation(info)),
                "parent": [parent.st_dev, parent.st_ino], "path": adapter._STATE_PATH}


def verify_released_snapshot(adapter, saved):
    path = Path(saved["path"])
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory = os.fstat(parent)
        assert [directory.st_dev, directory.st_ino] == saved["parent"]
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            actual = os.pread(descriptor, 16385, 0)
            after = os.fstat(descriptor)
            assert stat.S_ISREG(before.st_mode) and before.st_uid == os.getuid()
            assert stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
            assert list(adapter.journal_generation(before)) == saved["generation"]
            assert adapter.journal_generation(before) == adapter.journal_generation(after)
            assert adapter.journal_generation(before) == adapter.journal_generation(linked)
            assert actual == saved["raw"] and sha(actual) == saved["sha256"]
            named = path.parent.lstat()
            assert [named.st_dev, named.st_ino] == saved["parent"]
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


class ResponseWitness:
    """Keep exact body segments and the actual result of response closure."""

    def __init__(self, response, row):
        self.response = response
        self.row = row
        self.read_original = response.read
        self.close_original = response.close
        row["status"] = response.status
        row["headers"] = list(response.headers.raw_items())

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def read(self, count=-1):
        part = {"limit": count, "start": time.time()}
        self.row["segments"].append(part)
        try:
            body = self.read_original(count)
            part["body"] = body
            return body
        except BaseException as error:
            partial = getattr(error, "partial", None)
            if type(partial) is bytes:
                part["partial"] = partial
            part["error"] = type(error).__name__
            raise
        finally:
            part["completed"] = time.time()

    def close(self):
        try:
            self.close_original()
            self.row["closed"] = True
        except BaseException as error:
            self.row["close_error"] = type(error).__name__
            raise
        finally:
            self.row["close_wall"] = time.time()
            self.row["close_mono"] = time.monotonic()


class PolicyJournal:
    """Retain fallback REP limits in the original Flanders journal."""

    def __init__(self, adapter):
        self.adapter = adapter

    def read(self):
        a = self.adapter
        with a.locked_budget():
            return dict(a.load_budget(), rates=[])

    def save(self, state):
        a = self.adapter
        expected = {key: value for key, value in state.items() if key != "rates"}
        with a.locked_budget():
            expected["observed_at"] = time.time()
            merged = a.merge_budgets(expected, a.load_budget(), expected["observed_at"])
            a.save_budget(merged)
            assert json.loads(a.journal_read()) == merged


class Observer:
    def __init__(self, adapter, floor, result):
        self.a, self.floor, self.result = adapter, floor, result
        self.wires = result["wires"]
        self.pending = self.purpose = None
        self.timeline = result["persistence"]
        self.policy_checked = False
        self.closed = False

    def install(self):
        a = self.a
        self.open_original = a._OPENER.open
        self.owner_original = a.latest_family_run
        self.restore_original = a.restore_family_artifact
        self.download_original = a.download_inert_artifact
        self.commit_original = a.journal_commit
        self.prepare_original = a.prepare_collection
        self.request_original = a.request_bytes
        self.policy_original = a.check_robots
        self.input_original = a.read_input
        self.hold_original = a.retain_publisher_hold
        self.finish_original = a.journal_finish
        a._OPENER.open = self.open
        a.latest_family_run = self.owner
        a.download_inert_artifact = self.download
        a.restore_family_artifact = self.restore
        a.journal_commit = self.commit
        a.prepare_collection = self.prepare
        a.request_bytes = self.request
        a.check_robots = self.policy
        a.read_input = self.read_input
        a.retain_publisher_hold = self.hold
        a.journal_finish = self.finish

    def owner(self):
        selected = self.owner_original()
        assert selected == OWNER
        return selected

    def download(self, identity, artifact_id, digest):
        assert identity == OWNER and artifact_id == ARTIFACT and digest == "sha256:" + ARTIFACT_SHA
        production_state = self.download_original(identity, artifact_id, digest)
        self.result["production_owner"] = {"identity": OWNER, "artifact": ARTIFACT,
                                              "sha256": ARTIFACT_SHA, "state": production_state}
        return production_state

    def restore(self):
        a = self.a
        assert "startup" not in self.result
        self.restore_original()
        assert self.result.get("production_owner") and a._STATE_TRUSTED
        with a.locked_budget():
            now = time.time()
            current = a.load_budget()
            merged = a.merge_budgets(current, self.floor, now)
            a.save_budget(merged)
            assert json.loads(a.journal_read()) == merged

    def prepare(self):
        self.prepare_original()
        initial = snapshot(self.a)
        state = initial["state"]
        assert set(self.floor["starts"]).issubset(state["starts"])
        assert not state["blocked"] and state["legacy_rate_history_unknown"]
        for name in ("not_before", "completed", "last_inventory_completed", "interval",
                     "page_interval", "rate_limit_strikes", "rate_limit_until"):
            assert state[name] >= self.floor[name]
        self.result["startup"] = initial

    def commit(self, state):
        row = {"intended": state.copy(), "attempted_at": time.time()}
        self.timeline.append(row)
        try:
            self.commit_original(state)
            actual = self.a.journal_read()
            assert json.loads(actual) == state
            row["acknowledged_sha256"] = sha(actual)
        except BaseException as error:
            row["error"] = type(error).__name__
            raise

    def hold(self):
        if self.pending:
            self.pending["actual_refusal"] = True
        self.hold_original()

    def open(self, request, *args, **kwargs):
        addr = urllib.parse.urlsplit(request.full_url)
        if addr.hostname != "www.studyinflanders.be":
            assert addr.scheme == "https" and addr.netloc.lower() == addr.hostname
            if addr.hostname == "api.github.com":
                assert addr.path.startswith("/repos/YouthOpps/data-pipeline/")
            else:
                assert addr.hostname.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))
                assert request.get_header("Authorization") is None
            assert request.get_method() == "GET"
            return self.open_original(request, *args, **kwargs)
        a = self.a
        assert self.pending is None and self.purpose and "startup" in self.result
        url, inventory, robots = self.purpose
        assert request.full_url == url and request.get_method() == "GET"
        assert request.get_header("User-agent") == a._USER_AGENT
        assert request.get_header("Authorization") is None
        a.check_deadline()
        assert not a._GUARD_FATAL and not a._PUBLISHER_GATE_FAILED
        with a.locked_budget():
            state = a.load_budget()
            assert not state["blocked"] and state["starts"]
            row = {"sequence": len(self.wires) + 1, "url": url, "inventory": inventory,
                   "robots": robots, "reservation": state["starts"][-1], "segments": [],
                   "prior_state": state.copy(), "actual_refusal": False}
            self.pending = row
            self.wires.append(row)
            state["blocked"] = True
            state["observed_at"] = time.time()
            a.save_budget(state)
            row["pending_sha256"] = sha(a.journal_read())
            create_record(f"wire-{row['sequence']:02d}-pending.json", {
                "sequence": row["sequence"], "url": url, "reservation": row["reservation"],
                "pending_sha256": row["pending_sha256"],
            })
            assert a.load_budget()["blocked"]
        verify_preopener(a)
        a.journal_preopener()
        a.check_deadline()
        row["opener_wall"] = time.time()
        row["opener_mono"] = time.monotonic()
        try:
            response = self.open_original(request, *args, **kwargs)
        except urllib.error.HTTPError as error:
            row["response_branch"] = "raised-http-error"
            witnessed = ResponseWitness(error, row)
            error.read, error.close = witnessed.read, witnessed.close
            raise
        except BaseException as error:
            row["opener_error"] = type(error).__name__
            raise
        row["response_branch"] = "returned-response"
        return ResponseWitness(response, row)

    def settle(self, label):
        a, row = self.a, self.pending
        assert row and row.get("closed") and not row["actual_refusal"]
        with a.locked_budget():
            state = a.load_budget()
            assert state["blocked"] and state["completed"] >= row["close_wall"]
            raw = a.journal_read()
            body_sha = sha(b"".join(part.get("body", b"") for part in row["segments"]))
            receipt = {"sequence": row["sequence"], "url": row["url"],
                       "status": row["status"], "outcome": label,
                       "opener": row["opener_wall"], "closed": row["close_wall"],
                       "body_sha256": body_sha, "pending_sha256": row["pending_sha256"],
                       "closed_pending_sha256": sha(raw)}
            create_record(f"wire-{row['sequence']:02d}-assessed.json", receipt)
            assert a.journal_read() == raw
            state["blocked"] = False
            state["observed_at"] = time.time()
            a.save_budget(state)
            final = a.journal_read()
            assert json.loads(final) == state
            row["settlement"] = dict(receipt, settled_sha256=sha(final))
        self.pending = None

    def request(self, url, *args, **kwargs):
        if not kwargs.get("publisher"):
            return self.request_original(url, *args, **kwargs)
        assert self.purpose is None and self.pending is None
        robots, inventory = kwargs.get("robots", False), kwargs.get("inventory", False)
        exact = {item["url"] for item in self.a.INPUTS.values()}
        assert url in exact | {ROBOTS} and (url == ROBOTS) == robots
        assert inventory == (url in self.a.INVENTORY_INPUTS)
        self.purpose = url, inventory, robots
        try:
            try:
                status, headers, body = self.request_original(url, *args, **kwargs)
            except self.a.HeaderRateLimitStop:
                self.settle_rate_limit()
                raise
            row = self.pending
            assert row and row.get("closed") and row["status"] == status
            assert [part.get("body") for part in row["segments"]] == [body]
            size = rep.framing(headers)
            assert size is None or size == len(body)
            if not robots and status == 503:
                self.settle("transport-503-assessed")
            return status, headers, body
        finally:
            self.purpose = None

    def settle_rate_limit(self):
        a, row = self.a, self.pending
        assert row and row.get("closed") and row["status"] == 429 and not row["segments"]
        with a.locked_budget():
            saved, before = a.load_budget(), row["prior_state"]
            assert saved["rate_limit_strikes"] == before["rate_limit_strikes"] + 1
            assert saved["interval"] >= before["interval"] * 2
            assert saved["page_interval"] >= before["page_interval"] * 2
            assert saved["rate_limit_until"] >= row["opener_wall"] + 1800
        self.settle("header-429-assessed-stop")

    def policy(self, url):
        self.policy_original(url)
        if self.policy_checked:
            assert self.pending is None
            return
        row = self.pending
        assert row and row["robots"] and row["url"] == ROBOTS
        body, = (part["body"] for part in row["segments"])
        from email.message import Message
        headers = Message()
        for key, value in row["headers"]:
            headers[key] = value
        journal = PolicyJournal(self.a)
        rep.challenge(journal, body, headers, True, row["status"])
        checks = {}
        for info in self.a.INPUTS.values():
            rep.ENTRY = info["url"]
            checks[rep.ENTRY] = rep.robot_policy(journal, body, row["status"])
        row["rep"] = checks
        if not all(new and legacy for new, legacy, _ in checks.values()):
            self.hold()
            raise self.a.AdapterError("Publisher robots excludes an original required input", "access")
        self.settle("rep-assessed")
        self.policy_checked = True

    def read_input(self, key):
        value = self.input_original(key)
        row = self.pending
        assert row and row["url"] == self.a.INPUTS[key]["url"] and row["status"] == 200
        row["input"] = key
        row["validated_digest"] = value
        self.settle("original-input-assessed")
        return value

    def finish(self):
        try:
            if self.a._STATE_TRUSTED and not self.a._GUARD_FATAL:
                self.result["snapshot"] = snapshot(self.a)
        except BaseException as error:
            self.result["snapshot_error"] = type(error).__name__
        finally:
            self.finish_original()
        self.closed = True


def git(directory, *args):
    return subprocess.check_output(["git", "-C", str(directory), *args], timeout=20)


def consumer(adapter, records, stamp, wrong_source=False):
    metadata = adapter.metadata(stamp, {}, records, checked_at=stamp)
    if wrong_source:
        metadata["source"] = "unreviewed-source"
    call = subprocess.run(
        ["node", str(HERE / "consumer.mjs")], cwd=CHECKOUT,
        input=canonical({"records": records, "metadata": metadata}),
        capture_output=True, timeout=25, check=False,
    )
    if wrong_source:
        assert call.returncode != 0 and b"Observer source identity mismatch" in call.stderr
        return {"rejected_wrong_source": True}
    assert call.returncode == 0, "Actual website consumer failed"
    value = json.loads(call.stdout)
    expected = sorted(record["id"] for record in records)
    assert value == {"count": len(expected), "ids": expected, "sources": [adapter.SOURCE_ID]}
    return {"result": value, "metadata": metadata}


def check_consumers(adapter, records, stamp, *, include_empty):
    adapter.validate_records(records)
    now = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert now.utcoffset() is not None
    filtered = adapter.remove_expired_records(records, now)
    adapter.validate_records(filtered, allow_empty=True)
    data = {"raw": consumer(adapter, records, stamp),
            "filtered": consumer(adapter, filtered, stamp)}
    if include_empty:
        data["synthetic_empty"] = consumer(adapter, [], stamp)
        data["wrong_source"] = consumer(adapter, records, stamp, wrong_source=True)
    return data


def consumer_preflight(adapter):
    assert git(WEBSITE, "rev-parse", "HEAD").decode().strip() == WEBSITE_SHA
    current, ref = git(WEBSITE, "ls-remote", "origin", "refs/heads/main").decode().split()
    assert ref == "refs/heads/main" and re.fullmatch("[0-9a-f]{40}", current)
    if current != WEBSITE_SHA:
        git(WEBSITE, "fetch", "--no-tags", "--depth=1", "origin", ref)
        assert git(WEBSITE, "rev-parse", "FETCH_HEAD").decode().strip() == current
    for relative, digest in WEBSITE_PINS.items():
        view = private_input(WEBSITE / relative)
        assert sha(view) == digest
        assert view == git(WEBSITE, "show", f"{WEBSITE_SHA}:{relative}")
        assert view == git(WEBSITE, "show", f"{current}:{relative}")
    records = adapter.parse_inventory({key: info["sha256"] for key, info in adapter.INPUTS.items()})
    stamp = adapter.utc_now()
    readiness = check_consumers(adapter, records, stamp, include_empty=True)
    readiness["current_website_main"] = current
    readiness["checked_out_website_revision"] = WEBSITE_SHA
    expired = dict(records[-1], deadline="2000-01-01")
    adapter.validate_records([expired])
    assert adapter.remove_expired_records([expired], datetime.now(timezone.utc)) == []
    return readiness


def execute_test(adapter, result):
    assert sha(private_input(TEST)) == TEST_SHA and "DATA_SOURCE_TOKEN" not in os.environ
    assert "adapter" not in sys.modules
    stdout, stderr = io.StringIO(), io.StringIO()
    original_argv = sys.argv
    sys.modules["adapter"] = adapter
    sys.argv = [str(TEST)]
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(str(TEST), run_name="__main__")
            except SystemExit as exit_code:
                result["native_test_exit"] = exit_code.code
            else:
                raise RuntimeError("Original unittest exited without an outcome")
    finally:
        sys.argv = original_argv
        sys.modules.pop("adapter")
        result["native_stdout"], result["native_stderr"] = stdout.getvalue(), stderr.getvalue()


def certify_result(adapter, observer, result, floor):
    saved = result.get("snapshot")
    assert saved and observer.closed and not adapter._GUARD_FATAL
    verify_released_snapshot(adapter, saved)
    result["snapshot_verified_after_original_close"] = True
    assert observer.pending is None and not saved["state"]["blocked"]
    assert result["native_test_exit"] == 0
    assert len(observer.wires) >= 11
    assert observer.wires[0]["url"] == ROBOTS and observer.wires[0]["settlement"]["outcome"] == "rep-assessed"
    observed = [row["input"] for row in observer.wires if row.get("input")]
    assert observed == list(adapter.INPUTS)
    expected = {item["url"] for item in adapter.INPUTS.values()}
    actual_starts = []
    previous, previous_inventory = None, None
    for row in observer.wires:
        assert row["settlement"] and row["closed"]
        assert row["url"] in expected | {ROBOTS}
        if row["url"] == ROBOTS:
            assert row["sequence"] == 1
        assert row["reservation"] <= row["opener_wall"] <= row["close_wall"]
        assert row["opener_mono"] <= row["close_mono"]
        assert row["opener_wall"] >= max(floor["not_before"], floor["rate_limit_until"])
        starts = [start for start in actual_starts if start > row["opener_mono"] - 60]
        assert len(starts) < 5
        actual_starts.append(row["opener_mono"])
        if previous:
            assert row["opener_mono"] - previous["close_mono"] >= row["prior_state"]["interval"]
        if row["inventory"]:
            if previous_inventory:
                assert row["opener_mono"] - previous_inventory["close_mono"] >= row["prior_state"]["page_interval"]
            previous_inventory = row
        previous = row
    records = json.loads(result["native_stdout"])
    assert len(records) == 4
    result["actual_consumers"] = check_consumers(adapter, records, adapter.utc_now(), include_empty=False)
    result["observer_verified"] = True


def proposed_carry(adapter, result, floor):
    """Validate observation chronology before merging; the post-job quiet comes later."""
    saved = result.get("snapshot")
    if not saved or not result.get("snapshot_verified_after_original_close"):
        return None
    basis = floor.copy()
    starts = set(basis["starts"])
    for row in result["wires"]:
        starts.update(row[key] for key in ("reservation", "opener_wall") if key in row)
    basis["starts"] = sorted(starts)
    basis["observed_at"] = max(basis["observed_at"], saved["state"]["observed_at"], *starts)
    merged = adapter.merge_budgets(saved["state"], basis, basis["observed_at"])
    assert starts.issubset(merged["starts"])
    if any("settlement" not in row for row in result["wires"]):
        merged["blocked"] = True
    return adapter.validate_budget(merged)


def encode_raw(value):
    assert type(value) is bytes
    return {"base64": base64.b64encode(value).decode()}


def seal(raw):
    encrypted = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
         "-recip", str(CERT)], input=raw, capture_output=True, timeout=90, check=False,
    )
    assert encrypted.returncode == 0 and encrypted.stdout, "CMS evidence encryption failed"
    return encrypted.stdout


def write_cipher(cipher):
    parent = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory = os.fstat(parent)
        assert (directory.st_dev, directory.st_ino) == ROOT_ID
        fd = os.open("capture.cms", os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "wb", closefd=False) as output:
                assert output.write(cipher) == len(cipher)
                output.flush()
                os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)


def main():
    global INPUTS, AUTHORITY, ROOT, ROOT_ID
    if not __debug__ or type(APPROVAL) is not dict:
        raise RuntimeError("Hosted Flanders native test is OFF")
    assert os.environ.get("PYTHONOPTIMIZE") in (None, "", "0")
    assert APPROVAL["decision"] != "https://github.com/YouthOpps/data-pipeline/issues/428#issuecomment-6961946776"
    assert APPROVAL["recipient_sha256"] == CERT_SHA
    head = os.environ["GITHUB_SHA"]
    assert git(CHECKOUT, "rev-parse", "HEAD").decode().strip() == head
    scoped = (Path(__file__).resolve(), ADAPTER, TEST, RUNS, CARRY, CUSTODY, CERT,
              HERE / "consumer.mjs", HERE / "rep.py", CHECKOUT / WORKFLOW_PATH)
    INPUTS = {}
    for path in scoped:
        exact = private_input(path)
        assert exact == git(CHECKOUT, "show", f"{head}:{path.relative_to(CHECKOUT)}")
        INPUTS[path] = sha(exact)
    gate()
    root = Path(os.environ["RUNNER_TEMP"])
    assert root.is_absolute() and root.resolve() == root and root.is_dir()
    ROOT = root / "flanders428-native"
    ROOT.mkdir(mode=0o700)
    identity = ROOT.lstat()
    ROOT_ID = (identity.st_dev, identity.st_ino)
    assert tempfile.gettempdir() == "/tmp"
    adapter = import_original_qa_adapter()
    assert not os.path.lexists(Path("/tmp") / f"{adapter.FAMILY}-{os.getuid()}")
    result = {"native_test_exit": None, "observer_verified": False,
              "wires": [], "persistence": [], "decision": APPROVAL["decision"],
              "pin_sha256": {str(path.relative_to(CHECKOUT)): digest for path, digest in INPUTS.items()}}
    create_record("attempt.json", {"state": "unresolved", "head_sha": head,
                  "decision": APPROVAL["decision"], "run_id": int(os.environ["GITHUB_RUN_ID"])})
    observer = floor = None
    try:
        with adapter.execution():
            with adapter.phase("prepublication", 120):
                AUTHORITY = authenticate(adapter)
                matches_history(adapter)
                job, _ = verify_prior(adapter)
                probe = b"Flanders official native QA recipient preflight"
                assert probe not in seal(probe)
                floor = recover_floor(adapter, job, download_prior(adapter))
        result["prior_diagnostic_verified"] = True
        result["consumer_preflight"] = consumer_preflight(adapter)
        observer = Observer(adapter, floor, result)
        observer.install()
        execute_test(adapter, result)
        certify_result(adapter, observer, result, floor)
    except BaseException as error:
        result["observer_error"] = type(error).__name__
        result["observer_traceback"] = traceback.format_exc()
    if observer and result.get("snapshot") and observer.closed and not adapter._GUARD_FATAL:
        try:
            verify_released_snapshot(adapter, result["snapshot"])
            result["snapshot_verified_after_original_close"] = True
            result["carry_review_only"] = proposed_carry(adapter, result, floor)
        except BaseException as error:
            result["carry_error"] = type(error).__name__
    result["authority"] = AUTHORITY
    result["unresolved"] = bool(
        not result.get("snapshot_verified_after_original_close")
        or not result.get("carry_review_only") or result["carry_review_only"]["blocked"]
        or result.get("carry_error") or adapter._GUARD_FATAL
    )
    plaintext = json.dumps(result, default=encode_raw, ensure_ascii=False,
                           sort_keys=True, allow_nan=False).encode()
    cipher = seal(plaintext)
    write_cipher(cipher)
    create_record("outcome.json", {
        "authority": AUTHORITY, "cipher_sha256": sha(cipher),
        "native_test_exit": result["native_test_exit"],
        "observer_verified": result["observer_verified"], "unresolved": result["unresolved"],
        "wire_count": sum("opener_wall" in row for row in result["wires"]),
        "observer_error": result.get("observer_error"), "carry_error": result.get("carry_error"),
        "carry_is_review_only": True,
    })
    print("Flanders original native test exit:", result["native_test_exit"],
          "observer verified:", result["observer_verified"])
    if result["unresolved"] or not result["observer_verified"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
