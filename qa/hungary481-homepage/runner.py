"""Capture the exact homepage through the original Fulbright Hungary transport."""

import base64
import contextlib
from datetime import datetime
from hashlib import sha256
import importlib.util
import io
import tempfile
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

import escrow


HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
SOURCE = CHECKOUT / "adapters/hu-fulbright-hungary/adapter.py"
SCOPE = HERE / "scope.json"
CERTIFICATE = HERE / "reviewer.pem"
REPOSITORY = "YouthOpps/data-pipeline"
BRANCH = "qa/hungary481-homepage-research"
WORKFLOW = ".github/workflows/qa-hungary481-homepage-research.yml"
ROUTES = ("https://fulbright.hu/robots.txt", "https://fulbright.hu/")
def demand(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def seal(raw):
    completed = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
         "-recip", str(CERTIFICATE)],
        input=raw, capture_output=True, timeout=30, check=False,
    )
    demand(completed.returncode == 0 and completed.stdout, "Research encryption failed")
    return completed.stdout


def store(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    demand(path.read_bytes() == data, "Research evidence readback differs")


def owner(value, account):
    return (
        isinstance(value, dict) and type(value.get("id")) is int
        and value["id"] == account and value.get("type") == "User"
    )


def authenticate(a, scope, head):
    account = scope["maintainer_account"]
    ref = "refs/heads/" + BRANCH
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": ref, "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/{WORKFLOW}@{ref}",
        "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "research",
        "GITHUB_ACTOR_ID": str(account), "GITHUB_SHA": head, "GITHUB_WORKFLOW_SHA": head,
    }
    demand(all(os.environ.get(k) == v for k, v in expected.items()), "Exact hosted identity differs")
    demand(re.fullmatch(r"[1-9][0-9]*", os.environ.get("GITHUB_RUN_ID", "")), "Invalid run ID")
    run = int(os.environ["GITHUB_RUN_ID"])
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    demand(
        event.get("created") is True and event.get("deleted") is False
        and event.get("forced") is False and event.get("ref") == ref
        and event.get("before") == "0" * 40 and event.get("after") == head
        and owner(event.get("sender"), account), "Expected first branch creation by maintainer",
    )
    issue = scope["issue"]
    previous_assessment = scope["production_owner"]["completed_at"]
    approvals = a.workflow_api(f"/issues/{issue}/comments?since={previous_assessment}&per_page=100")
    demand(isinstance(approvals, list) and len(approvals) < 100, "Incomplete grant inventory")
    lines = {f"Approved Hungary homepage research head {head}",
             f"Hungary homepage research scope {sha256(canonical(scope)).hexdigest()}"}
    grants = [item for item in approvals if owner(item.get("user"), account)
              and isinstance(item.get("body"), str) and lines.issubset(item["body"].splitlines())]
    demand(len(grants) == 1, "Exact maintainer research grant unavailable or ambiguous")
    grant = grants[0]
    created = grant.get("created_at")
    demand(
        type(grant.get("id")) is int and grant["id"] > 0
        and isinstance(created, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", created)
        and created > previous_assessment and grant.get("updated_at") == created
        and grant.get("issue_url") == f"https://api.github.com/repos/{REPOSITORY}/issues/{issue}"
        and grant.get("html_url") == f"https://github.com/{REPOSITORY}/issues/{issue}#issuecomment-{grant['id']}",
        "Exact unedited maintainer grant identity differs",
    )
    fields = {"id": run, "run_attempt": 1, "event": "push", "path": WORKFLOW,
              "head_sha": head, "head_branch": BRANCH}
    for retry in range(5):
        status = a.workflow_api(f"/actions/runs/{run}/attempts/1")
        workflow = status.get("workflow_id")
        demand(
            all(status.get(k) == v for k, v in fields.items())
            and status.get("repository", {}).get("full_name") == REPOSITORY
            and status.get("head_repository", {}).get("full_name") == REPOSITORY
            and owner(status.get("actor"), account) and owner(status.get("triggering_actor"), account)
            and type(workflow) is int and workflow > 0 and workflow != scope["publisher_workflow"],
            "Immutable hosted research run identity differs",
        )
        if status.get("status") == "in_progress":
            break
        demand(status.get("status") in {"queued", "requested", "pending", "waiting"} and retry < 4,
               "Hosted research run did not become active")
        time.sleep(2)
    definition = a.workflow_api(f"/actions/workflows/{workflow}")
    history = a.workflow_api(f"/actions/workflows/{workflow}/runs?per_page=100")
    jobs = a.workflow_api(f"/actions/runs/{run}/attempts/1/jobs?per_page=100")
    demand(
        definition.get("id") == workflow and definition.get("path") == WORKFLOW
        and definition.get("state") == "active"
        and history.get("total_count") == 1 and len(history.get("workflow_runs", [])) == 1
        and all(history["workflow_runs"][0].get(k) == v for k, v in fields.items()),
        "Research workflow already has another or unverifiable run",
    )
    demand(jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1,
           "Expected a single genuine hosted research job")
    job = jobs["jobs"][0]
    expected_job = {"run_id": run, "run_attempt": 1, "name": "research",
                    "head_sha": head, "head_branch": BRANCH, "status": "in_progress"}
    demand(all(job.get(k) == v for k, v in expected_job.items())
           and type(job.get("id")) is int and job["id"] > 0, "Active research job identity differs")
    started = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
    elapsed = time.time() - started.timestamp()
    demand(started.utcoffset() is not None and -1 <= elapsed < 300,
           "Active research job start time differs")
    a._RUN_END = min(a._RUN_END, time.monotonic() + 1800 - max(elapsed, 0) - 2)
    return {"run_id": run, "run_attempt": 1, "workflow_id": workflow,
            "job_id": job["id"], "head": head, "grant": grant["id"], "ref": ref}


def verify_inventory(snapshot, scope):
    runs = snapshot.get("workflow_runs")
    count = scope["production_inventory_count"]
    demand(isinstance(runs, list) and snapshot.get("total_count") == count == len(runs),
           "Paused production inventory count changed")
    rows = sorted((run["id"], run["run_attempt"], run["head_sha"], run["workflow_id"],
                   run["status"]) for run in runs)
    demand(all(row[4] == "completed" for row in rows)
           and sha256(canonical(rows)).hexdigest() == scope["production_inventory_sha256"],
           "Exact paused production inventory changed")


def paused(a, scope):
    owner_workflow = a.workflow_api(f"/actions/workflows/{scope['publisher_workflow']}")
    demand(owner_workflow.get("id") == scope["publisher_workflow"]
           and owner_workflow.get("path") == scope["production_workflow_path"]
           and owner_workflow.get("state") == "disabled_manually",
           "Exact production workflow must be manually paused")
    snapshot = a.workflow_api(f"/actions/workflows/{scope['publisher_workflow']}/runs?per_page=100&page=1")
    verify_inventory(snapshot, scope)
    return snapshot


class Response:
    def __init__(self, original, row):
        self.original = original
        self.row = row
        self.status = original.status
        self.headers = original.headers
        row.update(status=self.status, headers=list(self.headers.raw_items()))

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.close()

    def read(self, limit):
        try:
            body = self.original.read(limit)
            self.row["body_base64"] = base64.b64encode(body).decode()
            self.row["body_bytes"] = len(body)
            self.row["body_sha256"] = sha256(body).hexdigest()
            cl = self.headers.get_all("Content-Length", [])
            te = self.headers.get_all("Transfer-Encoding", [])
            demand(
                len(cl) <= 1 and len(te) <= 1 and not (cl and te)
                and (not te or te[0].strip().lower() == "chunked")
                and (not cl or re.fullmatch(r"[0-9]+", cl[0]) and int(cl[0]) == len(body))
                and len(body) < limit and self.original.read(1) == b"",
                "Incomplete or ambiguous publisher response framing",
            )
            self.row["framed"] = True
            return body
        except BaseException as error:
            self.row["read_failure"] = type(error).__name__
            partial = getattr(error, "partial", None)
            if isinstance(partial, bytes):
                self.row["partial_body_base64"] = base64.b64encode(partial).decode()
            raise

    def close(self):
        try:
            self.original.close()
            self.row["closed"] = True
        finally:
            self.row["closed_at"] = time.monotonic()
            self.row["closed_wall"] = time.time()


def restore_exact(a, scope):
    snapshot = paused(a, scope)
    rows = [a.completed_identity(run) for run in snapshot["workflow_runs"]]
    completed = sorted(row for row in rows if row is not None)
    owner = scope["production_owner"]
    expected = (owner["run"], owner["attempt"], a.SOURCE_ID, owner["head"])
    demand(len(completed) >= 2 and completed[-1][0] > completed[-2][0]
           and completed[-1][2:] == expected, "Exact newest production chronology differs")
    result = a.workflow_api(f"/actions/runs/{owner['run']}/attempts/{owner['attempt']}/jobs?per_page=100")
    jobs = result.get("jobs")
    demand(result.get("total_count") == 1 and len(jobs) == 1, "Production job count changed")
    job = jobs[0]
    demand(job.get("id") == owner["job"] and job.get("conclusion") == owner["conclusion"]
           and job.get("completed_at") == owner["completed_at"], "Exact production job differs")

    def pinned_history():
        a._HISTORY_COMPLETED = completed
        return expected

    original_download = a.download_inert_artifact

    def pinned_archive(identity, artifact_id, digest):
        demand(identity == expected and artifact_id == owner["artifact"]
               and digest == owner["digest"], "Exact newest production archive differs")
        a._hu481_verified_transfer = True
        try:
            state = original_download(identity, artifact_id, digest)
        finally:
            a._hu481_verified_transfer = False
        demand(not state["in_flight"] and not state["blocked"]
               and state["request_outcome"] == "response_assessed"
               and state["rate_limit_until"] <= time.time(), "Newest custody has an unresolved hold")
        return state

    a.latest_family_run = pinned_history
    a.download_inert_artifact = pinned_archive
    return a.restored_state()


def original_ack(a):
    with a.locked_budget():
        a.verify_journal()
        generation, raw = a.journal_snapshot()
        demand(0 < len(raw) <= 16384 and generation == a._JOURNAL_VERSION
               and raw == a._JOURNAL_BYTES, "Readback differs from original journal ACK")
        return {"sha256": sha256(raw).hexdigest(), "generation": list(generation),
                "raw_base64": base64.b64encode(raw).decode(),
                "state": a.validate_budget(json.loads(raw))}


def instrument(a, scope, pins, wire, output, identity):
    original_open = a._OPENER.open
    original_request = a.request_bytes

    def bounded_request(url, **kwargs):
        if (len(wire) >= len(ROUTES) or url != ROUTES[len(wire)]
                or kwargs.get("robots", False) != (url == ROUTES[0])):
            raise a.PolicyStop("Research excludes redirects and retries", "access")
        return original_request(url, **kwargs)

    def observe(request, *args, **kwargs):
        url = request.full_url
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if not a.is_publisher(url):
            action_api = host == "api.github.com" and parsed.path.startswith("/repos/" + REPOSITORY + "/")
            owned_transfer = (
                getattr(a, "_hu481_verified_transfer", False)
                and host.endswith((".blob.core.windows.net", ".actions.githubusercontent.com"))
                and request.get_header("Authorization") is None
                and request.get_header("Cookie") is None
            )
            demand(parsed.scheme == "https" and parsed.port is None and parsed.username is None
                   and parsed.password is None and request.get_method() == "GET"
                   and (action_api or owned_transfer), "Research infrastructure route differs")
            return original_open(request, *args, **kwargs)

        demand(len(wire) < len(ROUTES) and url == ROUTES[len(wire)]
               and request.get_method() == "GET" and request.data is None
               and request.get_header("User-agent") == a._USER_AGENT
               and request.get_header("Authorization") is None
               and request.get_header("Cookie") is None
               and all(row.get("settlement_sha256") for row in wire),
               "Unexpected physical publisher request")
        paused(a, scope)
        pins()
        reservation = original_ack(a)
        state = reservation["state"]
        demand(state["in_flight"] and not state["blocked"]
               and state["flight_id"] == a._ACTIVE_FLIGHT_ID
               and state["request_id"] == a._ACTIVE_REQUEST_ID
               and state["request_outcome"] == "reserved"
               and state["request_inventory"] == (url == ROUTES[1])
               and 0 < len(state["starts"]) <= 5, "No exact durable reservation for this request")
        sequence = len(wire) + 1
        pending = {
            "identity": identity(), "url": url, "sequence": sequence, "reservation": reservation,
        }
        stem = f"pending-{sequence:02}"
        pending["escrow"] = escrow.upload(identity(), stem, pending, a.workflow_api, seal)
        store(output / (stem + ".json"), canonical(pending))
        demand(original_ack(a) == reservation, "Publisher reservation changed")
        pins()
        row = {
            "url": url, "reservation": reservation, "identity": identity(),
            "opened_at": time.monotonic(), "opened_wall": time.time(),
            "user_agent": a._USER_AGENT,
        }
        if wire:
            demand(row["opened_at"] - wire[-1]["closed_at"] >= state["interval"],
                   "Actual publisher completion gap has not elapsed")
        wire.append(row)
        try:
            response = original_open(request, *args, **kwargs)
        except HTTPError as error:
            response = error
        except BaseException as error:
            row["open_failure"] = type(error).__name__
            raise
        return Response(response, row)

    a.request_bytes = bounded_request
    a._OPENER.open = observe


def settle(a, wire, output):
    if not wire or wire[-1].get("settlement_sha256"):
        return
    row = wire[-1]
    current = original_ack(a)
    state = current["state"]
    earlier = row["reservation"]["state"]
    demand(not state["in_flight"] and row.get("closed") is True
           and state["completed_flight_id"] == earlier["flight_id"]
           and state["completed_request_id"] == earlier["request_id"]
           and state["request_outcome"] == "response_assessed"
           and state["completed"] >= row["closed_wall"]
           and state["not_before"] >= row["closed_wall"] + state["interval"]
           and state["starts"] == earlier["starts"], "Original physical and logical closure remain unproved")
    settlement = {
        "url": row["url"], "status": row["status"], "closed_at": row["closed_wall"],
        "reservation_sha256": row["reservation"]["sha256"], "settlement": current,
    }
    stem = f"settled-{len(wire):02}"
    settlement["escrow"] = escrow.upload(row["identity"], stem, settlement, a.workflow_api, seal)
    store(output / (stem + ".json"), canonical(settlement))
    row["settlement_sha256"] = current["sha256"]


def load_adapter(scope):
    a_spec = importlib.util.spec_from_file_location("hu481_original", SOURCE)
    a = importlib.util.module_from_spec(a_spec)
    a_spec.loader.exec_module(a)
    demand(a.SOURCE_URL == ROUTES[1] and a._WORKFLOW_ID == scope["publisher_workflow"],
           "Original adapter owner or URL differs")
    return a


def apply_original_maintenance(scope):
    production = CHECKOUT / scope["production_workflow_path"]
    demand(sha256(production.read_bytes()).hexdigest() == scope["production_workflow_sha256"],
           "Original production workflow differs")
    config = re.findall(r"^\s+HUNGARY_PACING_MAINTENANCE: '([^']+)'$", production.read_text(), re.M)
    demand(len(config) == 1 and "HUNGARY_PACING_MAINTENANCE" not in os.environ,
           "Exact original native MAX is unavailable")
    os.environ["HUNGARY_PACING_MAINTENANCE"] = config[0]


def capture(a, scope, head, output, pins):
    wire = []
    identity = None
    final = None
    failure = None
    diagnostics = io.StringIO()

    def bind_hosted_job():
        demand(identity is not None, "Research identity is unbound")
        a.check_deadline()

    owner = scope["production_owner"]
    # The original adapter's main-branch hosted checks are kept unchanged on disk.
    # The isolated runner authenticates this one hosted job and original owner itself.
    a.workflow_mode_guard = lambda: paused(a, scope)
    a.current_job_context = bind_hosted_job
    original_restore = a.restored_state

    def restore_owned():
        a.restored_state = original_restore
        try:
            return restore_exact(a, scope)
        finally:
            a.restored_state = restore_owned

    a.restored_state = restore_owned
    instrument(a, scope, pins, wire, output, lambda: identity)
    try:
        with a.execution(), contextlib.redirect_stderr(diagnostics):
            try:
                with a.phase("restore", 420):
                    identity = authenticate(a, scope, head)
                    a.prepare_collection()
                with a.phase("collection", 420):
                    minimum = a.check_robots(ROUTES[1])
                    settle(a, wire, output)
                    demand(len(wire) == 1 and wire[0].get("status") == 200
                           and wire[0].get("framed") is True,
                           "Homepage requires an explicit complete successful robots assessment")
                    a.fetch_source(ROUTES[1], min_interval=minimum)
            finally:
                if wire and not a._PUBLISHER_GATE_FAILED:
                    with a.phase("export", 30):
                        settle(a, wire, output)
                        final = original_ack(a)
    except BaseException as error:
        failure = type(error).__name__
        diagnostics.write(str(error))

    ciphertext = seal(canonical({
        "identity": identity, "production_owner": owner, "wire": wire,
        "diagnostics": diagnostics.getvalue(), "final": final,
    }))
    store(output / "capture.cms", ciphertext)
    pending = sorted(path.name[8:] for path in output.glob("pending-*.json"))
    settled = sorted(path.name[8:] for path in output.glob("settled-*.json"))
    complete = (
        failure is None and len(wire) == 2 and len(settled) == 2 and final is not None
        and all(row.get("status") == 200 and row.get("framed") is True
                and row.get("settlement_sha256") for row in wire)
    )
    summary = {
        "identity": identity, "production_owner": owner,
        "qualified_capture": complete, "failure_type": failure,
        "physical_opens": len(wire), "unresolved_attempt": pending != settled,
        "responses": [{name: row.get(name) for name in (
            "url", "status", "body_bytes", "body_sha256", "settlement_sha256",
        )} for row in wire], "capture_sha256": sha256(ciphertext).hexdigest(),
    }
    store(output / "research-evidence.json", canonical(summary))
    print(json.dumps(summary))
    return 0 if complete else 1


def main():
    scope = json.loads(SCOPE.read_bytes())
    demand(scope.get("enabled") is True and scope.get("issue") == 481
           and scope.get("origin") == REPOSITORY and scope.get("branch") == BRANCH
           and scope.get("workflow") == WORKFLOW and tuple(scope.get("routes", [])) == ROUTES,
           "Hungary homepage research remains OFF")
    demand("DATA_SOURCE_TOKEN" not in os.environ and "HUNGARY_PACING_ARTIFACT_PATH" not in os.environ,
           "Research requires isolated nonpublishing credentials")
    demand(sha256(CERTIFICATE.read_bytes()).hexdigest() == scope["reviewer_sha256"],
           "Reviewed research recipient differs")
    subprocess.run(["openssl", "x509", "-in", str(CERTIFICATE), "-checkend", "1800", "-noout"], check=True)
    seal(b"Hungary homepage research CMS preflight")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True).strip()
    demand(re.fullmatch(r"[0-9a-f]{40}", head) and head == os.environ.get("GITHUB_SHA"),
           "Exact hosted revision differs")
    source_dir = SOURCE.relative_to(CHECKOUT).parent
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", WORKFLOW,
                    str(source_dir), "qa/hungary481-homepage"], cwd=CHECKOUT, check=True)
    demand(sha256(SOURCE.read_bytes()).hexdigest() == scope["original_adapter_sha256"],
           "Original reviewed production adapter differs")
    production = CHECKOUT / scope["production_workflow_path"]
    protected = (SOURCE, SCOPE, CERTIFICATE, Path(__file__), HERE / "escrow.py", HERE / "action.yml", HERE / "launch.js", CHECKOUT / WORKFLOW, production)
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in protected}

    def pins():
        demand(all(sha256(path.read_bytes()).hexdigest() == value for path, value in hashes.items()),
               "Reviewed code or evidence inputs changed")

    apply_original_maintenance(scope)
    a = load_adapter(scope)
    output = Path(os.environ["RUNNER_TEMP"]) / "hungary481-homepage-research"
    output.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="hungary481-journal-", dir=output.parent) as directory:
        tempfile.tempdir = directory
        try:
            return capture(a, scope, head, output, pins)
        finally:
            tempfile.tempdir = None


if __name__ == "__main__":
    raise SystemExit(main())
