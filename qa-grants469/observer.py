"""Run the one original nonpublishing grants.at test and retain exact custody."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import zipfile

ROOT = Path.cwd()
QA = ROOT / "qa-grants469"
SOURCE = ROOT / "adapters/at-grants-at"
WORKFLOW = ".github/workflows/verify-at-grants-at-fh469-r2.yml"
BRANCH = "qa/grants-at-fh469-native-r2"
CERT = QA / "review-cert.pem"
SCOPE_BYTES = (QA / "scope.json").read_bytes()
SCOPE = json.loads(SCOPE_BYTES)
MAINTAINER_ID = 12278069
RUN_LIMIT_SECONDS = 1740


class EvidenceError(RuntimeError):
    """An exact QA guard with a literal, publishable reason."""


class InfrastructureError(EvidenceError):
    """Record a bounded GitHub API failure without logging credentials or body."""

    def __init__(self, route, status, reason):
        super().__init__(f"GitHub {route}: {reason}")
        self.status = status


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def require_fields(row, values, message):
    require(all(row.get(field) == wanted and type(row.get(field)) is type(wanted)
                for field, wanted in values.items()), message)


def verify_checkout(head):
    require(subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, timeout=15, text=True
    ).strip() == head, "Hosted checkout head differs")
    files = (SOURCE / "adapter.py", SOURCE / "test_adapter.py", ROOT / WORKFLOW,
             QA / "observer.py", QA / "consumer.mjs", CERT, QA / "scope.json")
    for path in files:
        committed = subprocess.check_output(
            ["git", "show", f"{head}:{path.relative_to(ROOT)}"], cwd=ROOT, timeout=15
        )
        require(path.resolve() == path and committed == path.read_bytes(),
                "Hosted checkout file differs from approved Git object")
        if path == QA / "scope.json":
            require(committed == SCOPE_BYTES, "The consumed scope bytes differ")
    website = ROOT / "website"
    require(subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=website, timeout=15, text=True
    ).strip() == SCOPE["website"], "Website consumer revision differs")
    require(not subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=website, timeout=15
    ), "Website consumer checkout changed")


def grant_lines(head):
    return (
        f"Approved Ernst Mach FH native head {head}",
        f"Ernst Mach FH native scope {sha(SCOPE_BYTES)}",
    )


def arm_infrastructure(adapter):
    """Start the source's one nonrenewable deadline before its first API read."""
    require(adapter._RUN_END is None and adapter._COLLECTION_END is None,
            "Original source startup deadline already initialized")
    adapter._RUN_END = time.monotonic() + RUN_LIMIT_SECONDS
    original = adapter.workflow_api

    def read(path):
        try:
            return original(path)
        except adapter.AdapterError as error:
            if path.startswith("/actions/runs/"):
                route = "run verification"
            elif path.startswith("/actions/workflows/"):
                route = "workflow inventory"
            elif path.startswith("/issues/"):
                route = "maintainer grant"
            else:
                route = "repository or source binding"
            status = error.status
            if type(status) is int and 100 <= status <= 599:
                reason = f"HTTP {status}"
            elif str(error) == "Absolute operation deadline exhausted":
                reason = "original operation deadline exhausted"
            else:
                reason = "original infrastructure transport refused the read"
            raise InfrastructureError(route, status, reason) from None

    adapter.workflow_api = read


def verify_grant(api, run):
    want = grant_lines(run["head_sha"])
    matches = []
    for page in range(1, 11):
        rows = api(f"/issues/469/comments?per_page=100&page={page}")
        require(isinstance(rows, list), "Missing maintainer grant inventory")
        for row in rows:
            lines = row.get("body", "").splitlines()
            if not all(line in lines for line in want):
                continue
            require(row.get("issue_url") ==
                    "https://api.github.com/repos/YouthOpps/data-pipeline/issues/469"
                    and row.get("user", {}).get("id") == MAINTAINER_ID
                    and row.get("user", {}).get("type") == "User"
                    and row.get("created_at") == row.get("updated_at")
                    and isinstance(row.get("created_at"), str)
                    and row["created_at"] < run["created_at"]
                    and all(lines.count(line) == 1 for line in want),
                    "Invalid exact native maintainer grant")
            matches.append(row["id"])
        if len(rows) < 100:
            break
    else:
        raise RuntimeError("Incomplete maintainer grant inventory")
    require(len(matches) == 1, "Expected one exact native maintainer grant")
    return matches[0]


def verify_exclusive(api):
    identifier = SCOPE["production_workflow_id"]
    expected_path = ".github/workflows/fetch-at-grants-at.yml"
    definition = api(f"/actions/workflows/{identifier}")
    require_fields(definition, {
        "id": identifier, "path": expected_path, "state": "disabled_manually"
    }, "Exact production owner is not paused")
    runs = api(f"/actions/workflows/{identifier}/runs?per_page=1")
    latest, = runs["workflow_runs"]
    require_fields(latest, {
        "id": SCOPE["production_run_id"], "head_sha": SCOPE["production_head"],
        "workflow_id": identifier, "head_branch": "main", "path": expected_path,
        "run_attempt": 1, "status": "completed", "conclusion": "success",
    }, "Latest production owner changed")
    require(all(latest.get(k, {}).get("full_name") == SCOPE["repo"]
                for k in ("head_repository", "repository")), "Foreign latest owner")
    old = SCOPE["failed_preflight"]
    retired = api(f'/actions/workflows/{old["workflow"]}')
    require_fields(retired, {"id": old["workflow"], "path": old["path"],
                             "state": "disabled_manually"},
                   "The exact prior native workflow must remain retired")
    return latest


def verify_hosted(api):
    required = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": SCOPE["repo"],
        "GITHUB_REF": f"refs/heads/{BRANCH}", "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "verify",
        "GITHUB_ACTOR_ID": str(MAINTAINER_ID),
        "GITHUB_WORKFLOW_REF": f'{SCOPE["repo"]}/{WORKFLOW}@refs/heads/{BRANCH}',
    }
    require(all(os.environ.get(k) == v for k, v in required.items())
            and "DATA_SOURCE_TOKEN" not in os.environ and bool(os.environ.get("GITHUB_TOKEN")),
            "Unauthorized hosted scope")
    head = os.environ.get("GITHUB_SHA", "")
    require(re.fullmatch(r"[0-9a-f]{40}", head) and os.environ.get("GITHUB_WORKFLOW_SHA") == head,
            "Untrusted hosted head SHA")
    execution = os.environ.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[1-9][0-9]*", execution), "Invalid native run identity")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    require_fields(event, {"created": True, "deleted": False,
                           "ref": required["GITHUB_REF"], "before": "0" * 40, "after": head},
                   "Native job must follow the first exact branch creation")
    require(event.get("sender", {}).get("id") == MAINTAINER_ID
            and event.get("repository", {}).get("full_name") == SCOPE["repo"],
            "Unauthorized source of branch creation")
    run = api(f"/actions/runs/{execution}/attempts/1")
    require_fields(run, {"id": int(execution), "event": "push", "run_attempt": 1,
                          "head_sha": head, "head_branch": BRANCH, "path": WORKFLOW,
                          "status": "in_progress"}, "Hosted run identity differs")
    require(all(run.get(k, {}).get("id") == MAINTAINER_ID
                and run.get(k, {}).get("type") == "User" for k in ("actor", "triggering_actor"))
            and all(run.get(k, {}).get("full_name") == SCOPE["repo"]
                    for k in ("repository", "head_repository"))
            and isinstance(run.get("created_at"), str),
            "Foreign hosted attempt")
    owner = api(f'/actions/workflows/{run["workflow_id"]}')
    require_fields(owner, {"id": run["workflow_id"], "path": WORKFLOW, "state": "active"},
                   "Original QA workflow identity differs")
    inventory = api(f'/actions/workflows/{run["workflow_id"]}/runs?per_page=2')
    require(inventory.get("total_count") == 1
            and [row["id"] for row in inventory["workflow_runs"]] == [run["id"]],
            "The native workflow has another attempt or run")
    jobs = api(f"/actions/runs/{execution}/attempts/1/jobs?per_page=2")
    require(jobs.get("total_count") == 1, "Native job inventory differs")
    job, = jobs["jobs"]
    require_fields(job, {"name": "verify", "status": "in_progress", "run_id": run["id"],
                         "head_sha": head}, "Native verification job identity differs")
    require(api("/branches/main")["commit"]["sha"] == SCOPE["base"],
            "Main has moved since this scope was reviewed")
    verify_checkout(head)
    verify_exclusive(api)
    return run, verify_grant(api, run)


def verify_failed_preflight(adapter):
    """Authenticate the previously reviewed encrypted zero-traffic preflight."""
    old = SCOPE["failed_preflight"]
    api = adapter.workflow_api
    expected_run = {
        "id": old["run"], "workflow_id": old["workflow"], "run_attempt": 1,
        "path": old["path"], "head_branch": old["branch"], "head_sha": old["head"],
        "event": "push", "status": "completed", "conclusion": "failure",
    }
    previous = api(f'/actions/runs/{old["run"]}/attempts/1')
    inventory = api(f'/actions/workflows/{old["workflow"]}/runs?per_page=2')
    require(inventory.get("total_count") == 1
            and len(inventory.get("workflow_runs", [])) == 1,
            "Prior workflow history contains another execution")
    for row in (previous, inventory["workflow_runs"][0]):
        require_fields(row, expected_run, "Exact failed preflight attempt differs")
        require(all(row.get(k, {}).get("full_name") == SCOPE["repo"]
                    for k in ("repository", "head_repository")), "Foreign preflight owner")
    jobs = api(f'/actions/runs/{old["run"]}/attempts/1/jobs?per_page=2')
    require(jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1,
            "Prior preflight job inventory differs")
    require_fields(jobs["jobs"][0], {
        "id": old["job"], "name": "verify", "run_id": old["run"],
        "head_sha": old["head"], "status": "completed", "conclusion": "failure",
    }, "Exact failed preflight job differs")
    listed = api(f'/actions/runs/{old["run"]}/artifacts?per_page=2')
    require(listed.get("total_count") == 1 and len(listed.get("artifacts", [])) == 1,
            "Prior attempt has a competing or missing state artifact")
    artifact = listed["artifacts"][0]
    require_fields(artifact, {
        "id": old["artifact"], "digest": old["artifact_digest"], "size_in_bytes": old["bytes"],
        "name": "grants-at-fh469-qa-result-1", "expired": False,
    }, "Exact failed preflight archive differs")
    require_fields(artifact.get("workflow_run", {}), {"id": old["run"], "head_sha": old["head"]},
                   "Prior artifact belongs to another execution")

    status, headers, _ = adapter.request_bytes(
        adapter._WORKFLOW_GITHUB + f'/actions/artifacts/{old["artifact"]}/zip',
        byte_limit=1000,
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                 "Accept": "application/vnd.github+json"},
    )
    destination = headers.get("Location", "")
    target = urllib.parse.urlsplit(destination)
    require(status == 302 and target.scheme == "https" and target.hostname
            and not target.username and not target.password and target.port is None
            and target.hostname.endswith((".blob.core.windows.net",
                                          ".actions.githubusercontent.com")),
            "Failed preflight archive redirect is outside GitHub artifact custody")
    status, _, body = adapter.request_bytes(destination, byte_limit=8192)
    require(status == 200 and len(body) == old["bytes"]
            and "sha256:" + sha(body) == old["artifact_digest"],
            "Failed preflight download bytes differ")
    with zipfile.ZipFile(io.BytesIO(body)) as source:
        members = source.infolist()
        require(len(members) == 2 and {item.filename for item in members} ==
                {"manifest.json", "evidence.cms"} and all(
                    item.file_size <= 8192 and not item.flag_bits & 1 for item in members),
                "Prior preflight archive contains unexpected or missing evidence")
        manifest = json.loads(source.read("manifest.json"))
        encrypted = source.read("evidence.cms")
    require(manifest == {
        "encrypted_evidence_sha256": old["encrypted_evidence_digest"],
        "error_type": "AdapterError", "observer": "failed", "publisher_requests": 0,
    } and sha(encrypted) == old["encrypted_evidence_digest"],
            "Prior attempt lacks the exact reviewed zero-traffic evidence")
    return old["run"]


def install_native_binding(adapter, run, grant, capture):
    history = adapter.family_history
    archive = adapter.download_inert_artifact
    opener = adapter._OPENER.open
    write_budget = adapter.write_budget

    def read_production_history():
        verify_exclusive(adapter.workflow_api)
        # The inventory being verified is the production workflow. The separate
        # native workflow and exact running job were authenticated above.
        hosted = os.environ.pop("GITHUB_ACTIONS")
        try:
            rows = history()
        finally:
            os.environ["GITHUB_ACTIONS"] = hosted
        require(rows and rows[0][1]["id"] == SCOPE["production_run_id"]
                and rows[0][2]["id"] == SCOPE["production_job_id"],
                "Production state chronology changed")
        return rows

    def read_production_archive(identity, identifier, digest):
        require(identity == (SCOPE["production_run_id"], 1, adapter.SOURCE_ID,
                             SCOPE["production_head"])
                and identifier == SCOPE["production_artifact_id"]
                and digest == SCOPE["production_artifact_digest"],
                "Publisher source state is not the exact reviewed owner")
        state = archive(identity, identifier, digest)
        require(sha(canonical(state)) == SCOPE["production_state_digest"],
                "Original publisher journal differs")
        capture["predecessor"] = state
        return state

    def verify_native_initial_state(incoming, predecessor):
        original = capture.get("predecessor")
        require(predecessor["id"] == SCOPE["production_run_id"] and original is not None
                and adapter.merge_budget(original, incoming) == incoming
                and not incoming["pending"] and not incoming["blocked"],
                "A production restriction or state transfer was lost")
        return incoming

    def retain_write(state):
        row = {"intended": json.loads(json.dumps(state)), "before": time.monotonic()}
        capture["writes"].append(row)
        try:
            write_budget(state)
            row["actual_journal_bytes"] = adapter.journal_read()
        except BaseException as error:
            row["error"] = type(error).__name__
            raise
        finally:
            row["after"] = time.monotonic()

    def observed_open(request, *args, **kwargs):
        url = request.full_url
        if urllib.parse.urlsplit(url).hostname not in adapter._ALLOWED_HOSTS:
            return opener(request, *args, **kwargs)
        require(SCOPE["paths"].get(url) == request.get_method(),
                "An unreviewed publisher route reached the opener")
        verify_exclusive(adapter.workflow_api)
        require(verify_grant(adapter.workflow_api, run) == grant,
                "The exact native authorization changed before a publisher request")
        receipts = capture["requests"]
        row = {"url": url, "method": request.get_method(), "payload": request.data,
               "request_headers": list(request.header_items()),
               "monotonic_start": time.monotonic(), "wall_start": time.time(),
               "inventory": adapter._INVENTORY_ACTIVE, "segments": [],
               "reservation_index": len(capture["writes"]) - 1}
        receipts.append(row)
        try:
            response = opener(request, *args, **kwargs)
        except urllib.error.HTTPError as error:
            observe_response(error, row)
            raise
        except BaseException as error:
            row.update(error=type(error).__name__, wall_completion=time.time(),
                       monotonic_completion=time.monotonic())
            raise
        observe_response(response, row)
        return response

    adapter.family_history = read_production_history
    adapter.download_inert_artifact = read_production_archive
    adapter.transfer_fh_native_state = verify_native_initial_state
    adapter.write_budget = retain_write
    adapter._OPENER.open = observed_open


def observe_response(response, row):
    read, close = response.read, response.close
    row.update(status=response.status, response_headers=list(response.headers.items()))

    def observe_read(*args, **kwargs):
        try:
            data = read(*args, **kwargs)
        except BaseException as error:
            partial = getattr(error, "partial", None)
            if isinstance(partial, bytes):
                row["segments"].append(partial)
            row["read_error"] = type(error).__name__
            raise
        row["segments"].append(data)
        return data

    def observe_close():
        try:
            close()
        except BaseException as error:
            row["close_error"] = type(error).__name__
            raise
        finally:
            row.update(wall_completion=time.time(), monotonic_completion=time.monotonic())

    response.read, response.close = observe_read, observe_close


def run_original(adapter):
    real_spec = importlib.util.spec_from_file_location
    bound = []

    class BoundLoader:
        def create_module(self, spec):
            return adapter

        def exec_module(self, module):
            require(module is adapter, "Original test obtained a foreign source module")
            bound.append(True)

    def exact_spec(name, location, *args, **kwargs):
        if Path(location).resolve() == SOURCE / "adapter.py":
            return importlib.util.spec_from_loader(name, BoundLoader(), origin=str(location))
        return real_spec(name, location, *args, **kwargs)

    stdout, stderr = io.StringIO(), io.StringIO()
    result = 0
    prior = sys.argv
    try:
        importlib.util.spec_from_file_location = exact_spec
        sys.argv = [str(SOURCE / "test_adapter.py")]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(sys.argv[0], run_name="__main__")
            except SystemExit as error:
                result = error.code
    finally:
        importlib.util.spec_from_file_location = real_spec
        sys.argv = prior
    require(bound == [True], "Original live test did not load the reviewed adapter")
    return {"outcome": result, "stdout": stdout.getvalue(), "stderr": stderr.getvalue()}


def consume(adapter, records, timestamp, wrong=False):
    metadata = adapter.metadata(timestamp, {}, records, checked_at=timestamp)
    if wrong:
        metadata["source"] = "foreign-observer-fixture"
    value = subprocess.run(
        ["node", str(QA / "consumer.mjs")],
        input=canonical({"records": records, "metadata": metadata}),
        capture_output=True, timeout=30, check=False,
    )
    if wrong:
        require(value.returncode != 0, "Wrong-source consumer case was accepted")
        return {"rejected": True}
    require(value.returncode == 0, "Current website consumer rejected original grants.at records")
    result = json.loads(value.stdout)
    require(result["ids"] == sorted(row["id"] for row in records)
            and result["sources"] == [adapter.SOURCE_ID], "Website consumer identity differs")
    return {"count": result["count"], "ids_sha256": sha(canonical(result["ids"]))}


def verify_output(adapter, original, capture, terminal):
    records = json.loads(original["stdout"])
    adapter.validate_records(records)
    target, = [row for row in records if row["id"] == "at-grants-at-44772-0"]
    require(target["deadline"] == "2027-02-01T22:59:00Z"
            and target["host_countries"] == ["AT"] and target["eligible_countries"] == [],
            "Current Ernst Mach FH cycle or identity differs from the reviewed scope")
    checked = adapter.utc_now()
    filtered = adapter.remove_expired_records(records, checked)
    wires = capture["requests"]
    require(wires and sum(row["url"] == adapter.ERNST_MACH_FH_FAQ for row in wires) == 1
            and all("wall_completion" in row for row in wires),
            "Original complete request receipts are incomplete")
    for row in wires:
        headers = {key.casefold(): value for key, value in row["request_headers"]}
        require(headers.get("user-agent") == adapter._USER_AGENT
                and "authorization" not in headers and row.get("status") in (
                    200, 403, 404, 410),
                "Original publisher response or crawler identity differs")
        if row["status"] != 200:
            require(row["url"].endswith("/robots.txt"),
                    "A failed ordinary publisher response cannot qualify")
    minimum = min(next_row["monotonic_start"] - previous["monotonic_completion"]
                  for previous, next_row in zip(wires, wires[1:]))
    pages = [row for row in wires if row["inventory"] and not row["url"].endswith("/robots.txt")]
    minimum_pages = min(next_row["monotonic_start"] - previous["monotonic_completion"]
                        for previous, next_row in zip(pages, pages[1:]))
    starts = [row["monotonic_start"] for row in wires]
    rolling = max(sum(start - 60 < value <= start for value in starts) for start in starts)
    require(minimum >= 12.2 and minimum_pages >= 30 and rolling <= 5
            and all(row["wall_completion"] >= row["wall_start"] for row in wires),
            "Original publisher pacing differs")
    require(not terminal["pending"] and not terminal["blocked"]
            and capture["writes"] and all("actual_journal_bytes" in row for row in capture["writes"]),
            "Successful output lacks complete acknowledged custody")
    for assignment in capture["writes"]:
        require(assignment["before"] <= assignment["after"]
                and json.loads(assignment["actual_journal_bytes"]) == assignment["intended"],
                "Original acknowledged journal differs from the planned write")
    for wire in wires:
        index = wire["reservation_index"]
        require(0 <= index < len(capture["writes"]), "Original reservation index differs")
        reservation = capture["writes"][index]
        require(reservation["after"] <= wire["monotonic_start"]
                and reservation["intended"]["pending"]
                and reservation["intended"]["pending_kind"] == "physical",
                "Original wire attempt has no acknowledged physical reservation")
    expiry = adapter.remove_expired_records([target], "2027-02-02T00:00:00Z")
    require(expiry == [], "Actual opportunity remained after its exact closing date")
    return {
        "raw": consume(adapter, records, checked),
        "filtered": consume(adapter, filtered, checked),
        "target_after_deadline": consume(adapter, expiry, "2027-02-02T00:00:00Z"),
        "wrong_source": consume(adapter, records, checked, wrong=True),
        "deadline": target["deadline"], "physical_gap": minimum,
        "inventory_gap": minimum_pages, "rolling_60_max": rolling,
    }


def seal(data):
    value = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER",
         "-aes-256-gcm", str(CERT)],
        input=data, capture_output=True, timeout=45, check=True,
    )
    require(bool(value.stdout), "Empty authenticated ciphertext")
    return value.stdout


def serial(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError("Unexpected native capture value")


def main():
    output = Path(os.environ["RUNNER_TEMP"]) / "grants-at-fh469-qa"
    output.mkdir(mode=0o700)
    report = {"observer": "failed", "publisher_requests": 0, "stage": "startup"}
    capture = {"requests": [], "writes": []}
    adapter = None
    try:
        spec = importlib.util.spec_from_file_location("adapter", SOURCE / "adapter.py")
        adapter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(adapter)
        arm_infrastructure(adapter)
        report["stage"] = "hosted_preflight"
        run, grant = verify_hosted(adapter.workflow_api)
        report["stage"] = "retained_preflight"
        report["verified_zero_traffic_run"] = verify_failed_preflight(adapter)
        seal(b"grants.at reviewer preflight")
        report.update(run_id=run["id"], head_sha=run["head_sha"],
                      grant_id=grant, scope_sha256=sha(SCOPE_BYTES))
        install_native_binding(adapter, run, grant, capture)
        report["stage"] = "original_test"
        original = run_original(adapter)
        capture["original"] = original
        report["original_outcome"] = original["outcome"]
        adapter.current_run_identity = lambda: (run["id"], run["run_attempt"])
        report["stage"] = "owned_state_export"
        adapter.export_family_artifact()
        owned = Path(os.environ["GRANTS_AT_PACING_ARTIFACT_PATH"]).read_bytes()
        envelope = json.loads(owned)
        report["owned_state_sha256"] = envelope["state_sha256"]
        capture["terminal_state"] = envelope["state"]
        if original["outcome"] == 0:
            report["stage"] = "website_validation"
            report.update(verify_output(adapter, original, capture, envelope["state"]))
            report["observer"] = "passed"
    except BaseException as error:
        report.update(error_type=type(error).__name__, observer="failed")
        capture["error_type"] = type(error).__name__
        if isinstance(error, EvidenceError):
            report["guard_reason"] = str(error)
        if isinstance(error, InfrastructureError) and type(error.status) is int:
            report["github_http_status"] = error.status
    finally:
        if adapter is not None:
            with contextlib.suppress(BaseException):
                adapter.journal_finish()
        report["publisher_requests"] = len(capture["requests"])
        try:
            encrypted = seal(json.dumps(capture, default=serial, allow_nan=False).encode())
            (output / "evidence.cms").write_bytes(encrypted)
            report["encrypted_evidence_sha256"] = sha(encrypted)
        except BaseException as error:
            report.update(sealing_error=type(error).__name__, observer="failed")
        (output / "manifest.json").write_bytes(canonical(report))
    return 0 if report["observer"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
