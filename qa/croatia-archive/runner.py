"""Observe one exact native Study in Croatia test without publication."""

import base64
import contextlib
from hashlib import sha256
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
from urllib.error import HTTPError
from urllib.request import Request
import zipfile
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
SOURCE = CHECKOUT / "adapters/hr-study-in-croatia/adapter.py"
SCOPE = HERE / "scope.json"
CERTIFICATE = HERE / "reviewer.pem"
REPOSITORY = "YouthOpps/data-pipeline"
BRANCH = "qa/croatia-archive-native"
WORKFLOW = ".github/workflows/qa-croatia-archive-native.yml"
ROBOTS = "https://www.studyincroatia.hr/robots.txt"
PREVIOUS_ASSESSMENT = "2026-11-18T06:06:07Z"
TEST = SOURCE.with_name("test_adapter.py")
WEBSITE = CHECKOUT / "website"
CONSUMER = HERE / "consumer.mjs"
NEW_SIGNATURE = b"def scan_family_history(*, hosted_research=False):"
OLD_SIGNATURE = b"def scan_family_history():"
NEW_MODE = b'    hosted = os.environ.get("GITHUB_ACTIONS") == "true" and not hosted_research'
OLD_MODE = b'    hosted = os.environ.get("GITHUB_ACTIONS") == "true"'


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
        "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "native",
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
    approvals = a.workflow_api(f"/issues/{issue}/comments?since={PREVIOUS_ASSESSMENT}&per_page=100")
    demand(isinstance(approvals, list) and len(approvals) < 100, "Incomplete grant inventory")
    lines = {f"Approved Croatia archive native head {head}",
             f"Croatia archive native scope {sha256(canonical(scope)).hexdigest()}"}
    grants = [item for item in approvals if owner(item.get("user"), account)
              and isinstance(item.get("body"), str) and lines.issubset(item["body"].splitlines())]
    demand(len(grants) == 1, "Exact maintainer native grant unavailable or ambiguous")
    grant = grants[0]
    created = grant.get("created_at")
    demand(
        type(grant.get("id")) is int and grant["id"] > 0
        and isinstance(created, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", created)
        and created > PREVIOUS_ASSESSMENT and grant.get("updated_at") == created
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
            "Immutable hosted native run identity differs",
        )
        if status.get("status") == "in_progress":
            break
        demand(status.get("status") in {"queued", "requested", "pending", "waiting"} and retry < 4,
               "Hosted native run did not become active")
        time.sleep(2)
    definition = a.workflow_api(f"/actions/workflows/{workflow}")
    history = a.workflow_api(f"/actions/workflows/{workflow}/runs?per_page=100")
    jobs = a.workflow_api(f"/actions/runs/{run}/attempts/1/jobs?per_page=100")
    demand(
        definition.get("id") == workflow and definition.get("path") == WORKFLOW
        and definition.get("state") == "active"
        and isinstance(status.get("created_at"), str) and created <= status["created_at"]
        and history.get("total_count") == 1 and len(history.get("workflow_runs", [])) == 1
        and all(history["workflow_runs"][0].get(k) == v for k, v in fields.items()),
        "Native workflow has an unexpected grant chronology or another run",
    )
    demand(jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1,
           "Expected a single genuine hosted native job")
    job = jobs["jobs"][0]
    expected_job = {"run_id": run, "run_attempt": 1, "name": "native",
                    "head_sha": head, "head_branch": BRANCH, "status": "in_progress"}
    demand(all(job.get(k) == v for k, v in expected_job.items())
           and type(job.get("id")) is int and job["id"] > 0, "Active native job identity differs")
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
    a.verify_local_workflow_pause()
    snapshot = a.workflow_api(f"/actions/workflows/{scope['publisher_workflow']}/runs?per_page=100&page=1")
    verify_inventory(snapshot, scope)
    for prior in scope["owners"]:
        expected = prior["identity"]["workflow_id"]
        definition = a.workflow_api(f"/actions/workflows/{expected}")
        demand(definition.get("id") == expected and definition.get("path") == prior["workflow"]
               and definition.get("state") == "disabled_manually", "Reviewed owner is still active")


def original_continuity(a, scope):
    download = a.download_inert_artifact
    api = a.workflow_api
    a.verified_artifact_transfer = False
    observed_jobs = {}
    chronology_fields = ("run_id", "run_attempt", "id", "head_sha", "head_branch",
                         "status", "conclusion", "started_at", "completed_at")

    def read_metadata(path):
        result = api(path)
        matched = re.fullmatch(r"/actions/runs/([0-9]+)/attempts/([0-9]+)/jobs\?per_page=100&page=1", path)
        if matched:
            demand(result.get("total_count") == 1 and len(result.get("jobs", [])) == 1,
                   "Exact production attempt must still own one terminal job")
            values = result["jobs"][0]
            observed_jobs[tuple(map(int, matched.groups()))] = tuple(values[k] for k in chronology_fields)
        return result

    def latest():
        observed_jobs.clear()
        found = a.scan_family_history(hosted_research=True)
        demand(found == tuple(scope["latest_owner"]), "Newest completed production owner changed")
        demand(
            len(a._ATTEMPT_HEADS) == scope["production_inventory_count"],
            "Original production history differs",
        )
        run, attempt, _ = found
        jobs = a.workflow_api(f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page=1")
        demand(jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1
               and jobs["jobs"][0].get("id") == scope["latest_job"], "Newest owner job changed")
        demand(len(observed_jobs) == scope["production_inventory_count"]
               and sha256(canonical(sorted(observed_jobs.values()))).hexdigest()
               == scope["production_job_chronology_sha256"], "Exact production job chronology changed")
        return found

    def exact_download(identity, artifact, digest=None):
        demand(identity == tuple(scope["latest_owner"]) and artifact == scope["latest_artifact"]
               and digest == scope["latest_artifact_digest"], "Newest production archive changed")
        item = a.workflow_api(f"/actions/artifacts/{artifact}")
        demand(item.get("size_in_bytes") == scope["latest_artifact_bytes"]
               and item.get("workflow_run", {}).get("head_sha") == scope["latest_head"],
               "Newest production archive owner metadata changed")
        a.verified_artifact_transfer = True
        try:
            state = download(identity, artifact, digest)
        finally:
            a.verified_artifact_transfer = False
        demand(sha256(canonical(state)).hexdigest() == scope["latest_state_sha256"],
               "Exact production state changed")
        return state

    a.latest_family_run = latest
    a.download_inert_artifact = exact_download
    a.workflow_api = read_metadata


def original_ack(a):
    before = a.journal_verify()
    raw = a.journal_read()
    after = a.journal_verify()
    demand(0 < len(raw) == after.st_size <= 16384
           and a.journal_version(before) == a.journal_version(after), "Journal generation changed")
    state = a.validate_budget(json.loads(raw))
    return {"bytes": len(raw), "sha256": sha256(raw).hexdigest(),
            "raw_base64": base64.b64encode(raw).decode(), "state": state,
            "generation": list(a.journal_version(after))}


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


def instrument(a, scope, pins, wire, acknowledgements, persistence, identity, output):
    routes = scope["routes"]
    rep_assessed = False
    latest_inventory_completion = None
    opening = a._OPENER.open
    writing = a.journal_write
    attempting = a.publisher_attempt
    requesting = a.request_bytes
    checking_robots = a.check_robots
    inventory_request = False

    @contextlib.contextmanager
    def observed_attempt(publisher, inventory=False):
        nonlocal inventory_request
        prior = inventory_request
        inventory_request = publisher and inventory
        try:
            with attempting(publisher, inventory):
                yield
        finally:
            inventory_request = prior

    def observed_write(state):
        receipt = {"state_before": acknowledgements[-1]["state"] if acknowledgements else None,
                   "intended": state.copy(), "attempted_at": time.monotonic()}
        persistence.append(receipt)
        try:
            writing(state)
            ack = original_ack(a)
            demand(ack["state"] == state, "Persisted original journal contents differ")
            ack["acknowledged_at"] = time.monotonic()
            acknowledgements.append(ack)
            receipt["acknowledgement"] = ack
        except BaseException as error:
            receipt["failure_type"] = type(error).__name__
            raise

    def observed_open(request, *args, **kwargs):
        url = request.full_url
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if host != "studyincroatia.hr" and not host.endswith(".studyincroatia.hr"):
            action_api = host == "api.github.com" and parsed.path.startswith("/repos/" + REPOSITORY + "/")
            owned_transfer = (
                a.verified_artifact_transfer
                and host in {scope["owned_transfer_host"], *(prior["host"] for prior in scope["owners"])}
                and request.get_header("Authorization") is None and request.get_header("Cookie") is None
            )
            demand(parsed.scheme == "https" and parsed.port is None and parsed.username is None
                   and parsed.password is None and request.get_method() == "GET"
                   and (action_api or owned_transfer), "Research infrastructure route differs")
            return opening(request, *args, **kwargs)
        demand(all(row.get("settlement_sha256") for row in wire),
               "Previous publisher request remains pending")
        demand(
            len(wire) < len(routes) and url == routes[len(wire)]
            and request.get_method() == "GET"
            and request.get_header("Authorization") is None
            and request.get_header("Cookie") is None
            and request.get_header("User-agent") == a._USER_AGENT
            and inventory_request is (url in scope["inventory_routes"])
            and (not wire or rep_assessed),
            "Publisher request is outside the exact native scope",
        )
        paused(a, scope)
        pins()
        a.journal_before_wire()
        reserved = original_ack(a)
        demand(acknowledgements and acknowledgements[-1]["sha256"] == reserved["sha256"],
               "Actual publisher open lacks exact preceding durable reservation")
        try:
            pending = {
                "identity": identity(), "source": a.SOURCE_ID, "url": url,
                "sequence": len(wire) + 1, "reservation": reserved,
            }
            store(output / f"pending-{len(wire) + 1:02}.json", canonical(pending))
            a.journal_before_wire()
            pins()
            opened_at = time.monotonic()
            if wire:
                demand(
                    opened_at - wire[-1]["closed_at"] >= reserved["state"]["interval"],
                    "Actual publisher completion gap has not elapsed",
                )
            baseline = scope["seed"]
            demand(
                reserved["state"]["interval"] >= baseline["interval"]
                and reserved["state"]["page_interval"] >= baseline["page_interval"]
                and reserved["state"]["rate_failures"] >= baseline["rate_failures"]
                and opened_at >= 0 and time.time() >= baseline["not_before"]
                and (not baseline["blocked"] and not reserved["state"]["blocked"])
                and (not baseline["legacy_rate_history_unknown"] or
                     reserved["state"]["legacy_rate_history_unknown"])
                and all(t in reserved["state"]["starts"] for t in baseline["starts"] if t > time.time() - 60),
                "Native reservation violates retained research ownership",
            )
            if inventory_request and latest_inventory_completion is not None:
                demand(opened_at - latest_inventory_completion >= reserved["state"]["page_interval"],
                       "Actual inventory page gap has not elapsed")
            row = {
                "url": url, "method": "GET", "user_agent": a._USER_AGENT,
                "reservation_sha256": reserved["sha256"],
                "opened_at": opened_at, "opened_wall": time.time(),
                "inventory_page": inventory_request,
            }
            wire.append(row)
        except BaseException:
            a.journal_failure()
            raise
        try:
            response = opening(request, *args, **kwargs)
            row["open_boundary"] = "response"
        except HTTPError as error:
            response = error
            row["open_boundary"] = "HTTPError"
        except BaseException as error:
            row["open_failure"] = type(error).__name__
            raise
        return Response(response, row)

    def observed_request(url, *args, **kwargs):
        nonlocal latest_inventory_completion
        result = requesting(url, *args, **kwargs)
        if kwargs.get("publisher") and not kwargs.get("robots"):
            settle_wire(a, wire, acknowledgements, output)
            if wire[-1]["inventory_page"]:
                latest_inventory_completion = wire[-1]["closed_at"]
        return result

    def observed_robots(url):
        nonlocal rep_assessed
        checking_robots(url)
        if not rep_assessed:
            demand(len(wire) == 1 and wire[0]["url"] == ROBOTS, "Native REP evidence is missing")
            settle_wire(a, wire, acknowledgements, output)
            rep_assessed = True

    a.journal_write = observed_write
    a.publisher_attempt = observed_attempt
    a.request_bytes = observed_request
    a.check_robots = observed_robots
    a._OPENER.open = observed_open


def settle_wire(a, wire, acknowledgements, output):
    row = wire[-1]
    rep = row["url"] == ROBOTS
    status = row.get("status")
    unavailable_rep = rep and type(status) is int and 400 <= status < 500 and status != 429
    demand(
        (status == 200 and row.get("framed") is True or unavailable_rep and "body_bytes" not in row)
        and row.get("closed") is True,
        "Publisher response is incomplete or outside the reviewed diagnostic scope",
    )
    row["rep_body_intentionally_unread"] = unavailable_rep
    preceding = [ack for ack in acknowledgements if ack["acknowledged_at"] <= row["opened_at"]]
    demand(preceding and preceding[-1]["sha256"] == row["reservation_sha256"],
           "Actual response does not match its preceding reservation")
    reserved = preceding[-1]["state"]
    demand(0 < len(reserved["starts"]) <= 5 and reserved["starts"][-1] <= row["opened_wall"],
           "Actual response reservation differs from the start limit")
    settled = acknowledgements[-1]
    completion_floor = row["closed_wall"] + max(reserved["interval"], settled["state"]["interval"], 12.2)
    demand(
        settled["acknowledged_at"] >= row["closed_at"]
        and settled["state"]["observed_at"] >= row["closed_wall"]
        and settled["state"]["not_before"] >= completion_floor
        and settled["state"]["starts"] == reserved["starts"],
        "Actual response closure lacks a matching journal acknowledgement",
    )
    if len(wire) > 1:
        gap = row["opened_at"] - wire[-2]["closed_at"]
        demand(gap >= reserved["interval"], "Actual physical pacing gap differs")
    try:
        with a.locked_budget():
            current = original_ack(a)
            demand(
                current["sha256"] == settled["sha256"]
                and current["generation"] == settled["generation"],
                "Current journal generation changed before settlement",
            )
        store(output / f"settled-{len(wire):02}.json", canonical({
            "url": row["url"], "closed_wall": row["closed_wall"],
            "opened_wall": row["opened_wall"], "status": status,
            "reservation_sha256": row["reservation_sha256"], "acknowledgement": settled,
        }))
        row["settlement_sha256"] = settled["sha256"]
    except BaseException:
        a.journal_failure()
        raise


def validate_ack(a, ack):
    raw = base64.b64decode(ack["raw_base64"], validate=True)
    demand(0 < len(raw) == ack["bytes"] <= 16384 and sha256(raw).hexdigest() == ack["sha256"],
           "Previous owner journal receipt bytes differ")
    state = a.validate_budget(json.loads(raw))
    demand(state == ack["state"], "Previous owner journal receipt state differs")
    return state


def authenticate_owner(a, scope, prior):
    """Reconstruct the exact settled timing of one retired hosted owner."""
    identity = prior["identity"]
    run, workflow, job = (identity[key] for key in ("run_id", "workflow_id", "job_id"))
    history = a.workflow_api(f"/actions/workflows/{workflow}/runs?per_page=100")
    attempt = a.workflow_api(f"/actions/runs/{run}/attempts/1")
    jobs = a.workflow_api(f"/actions/runs/{run}/attempts/1/jobs?per_page=100")
    expected = {
        "id": run, "workflow_id": workflow, "path": prior["workflow"], "run_attempt": 1,
        "head_sha": identity["head"], "head_branch": identity["ref"].removeprefix("refs/heads/"),
        "status": "completed", "conclusion": prior["conclusion"], "event": "push",
    }
    demand(history.get("total_count") == len(history.get("workflow_runs", [])) == 1
           and all(item.get(key) == value for item in (history["workflow_runs"][0], attempt)
                   for key, value in expected.items())
           and all(owner(attempt.get(key), scope["maintainer_account"])
                   for key in ("actor", "triggering_actor")),
           "Exact terminal previous owner differs")
    expected_job = {
        "id": job, "run_id": run, "run_attempt": 1, "head_sha": identity["head"], "name": prior["job"],
        "status": "completed", "conclusion": prior["conclusion"], "completed_at": prior["completed_at"],
    }
    demand(jobs.get("total_count") == len(jobs.get("jobs", [])) == 1
           and all(jobs["jobs"][0].get(key) == value for key, value in expected_job.items()),
           "Exact previous completion differs")
    source = a.workflow_api(f"/contents/{prior['scope_path']}?ref={identity['head']}")
    demand(source.get("path") == prior["scope_path"] and source.get("encoding") == "base64",
           "Immutable previous request scope missing")
    native_scope = json.loads(base64.b64decode(source["content"]))
    closure_count = prior["physical_opens"]
    demand(
        sha256(canonical(native_scope)).hexdigest() == prior["canonical_scope_sha256"]
        and native_scope.get("enabled") is True
        and native_scope["routes"][:closure_count] == prior["routes"],
        "Authenticated previous route scope differs",
    )
    artifact_id = prior["artifact_id"]
    artifacts = a.workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    artifact = a.workflow_api(f"/actions/artifacts/{artifact_id}")
    expected_artifact = {
        "id": artifact_id, "name": prior["archive_name"], "expired": False,
        "digest": prior["archive_digest"], "size_in_bytes": prior["archive_bytes"],
    }
    demand(
        artifacts.get("total_count") == len(artifacts.get("artifacts", [])) == 1
        and all(item.get(key) == value for item in (artifacts["artifacts"][0], artifact)
                for key, value in expected_artifact.items())
        and artifact.get("workflow_run", {}).get("id") == run
        and artifact["workflow_run"].get("head_sha") == identity["head"],
        "Latest native archive lineage differs",
    )
    request = Request(
        f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                 "User-Agent": a._USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        response = a._OPENER.open(request, timeout=a.request_timeout())
    except HTTPError as error:
        response = error
    with response:
        demand(response.status == 302, "Exact native archive redirect unavailable")
        location = response.headers.get("Location", "")
    redirect = urlsplit(location)
    demand(redirect.scheme == "https" and redirect.hostname == prior["host"]
           and redirect.port is None and redirect.username is None and redirect.password is None,
           "Previous owner archive transfer origin changed")
    a.verified_artifact_transfer = True
    try:
        status, _, raw = a.request_bytes(location, byte_limit=prior["archive_bytes"])
    finally:
        a.verified_artifact_transfer = False
    demand(status == 200 and len(raw) == prior["archive_bytes"]
           and "sha256:" + sha256(raw).hexdigest() == prior["archive_digest"],
           "Previous owner archive size or digest changed")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        files = archive.namelist()
        expected_files = {"capture.cms", prior["member"]} | {
            f"{name}-{i:02}.json" for i in range(1, closure_count + 1) for name in ("pending", "settled")
        }
        demand(len(files) == len(expected_files) and set(files) == expected_files
               and all(member.external_attr >> 16 & 0o170000 != 0o120000 and not member.flag_bits & 1
                       for member in archive.infolist())
               and archive.getinfo("capture.cms").file_size <= 1_500_000,
               "Previous owner archive settlement members differ")

        def read_receipt(name):
            demand(archive.getinfo(name).file_size <= 16384, "Previous owner receipt exceeds reviewed bound")
            return json.loads(archive.read(name))

        evidence = read_receipt(prior["member"])
        digest = sha256(archive.read("capture.cms")).hexdigest()
        complete = prior["conclusion"] == "success"
        demand(evidence["identity"] == identity and evidence[prior["complete"]] is complete
               and evidence["physical_opens"] == closure_count
               and evidence["unresolved_attempt"] is False and evidence["failure_type"] == prior["failure_type"]
               and digest == evidence["capture_sha256"] == prior["capture_digest"],
               "Previous hosted owner lacks exact settled evidence")
        if complete:
            demand(evidence["unqualified_latest_ack"] is None, "Qualified owner has unqualified custody")
        else:
            demand(evidence["qualified_native_ack"] is None, "Failed test has qualified custody")
        terminal = evidence[prior["receipt"]]
        last_state = validate_ack(a, terminal)
        chronology = []
        for index, url in enumerate(prior["routes"], 1):
            reserved = read_receipt(f"pending-{index:02}.json")
            settled = read_receipt(f"settled-{index:02}.json")
            before = validate_ack(a, reserved["reservation"])
            after = validate_ack(a, settled["acknowledgement"])
            opened, closed = settled["opened_wall"], settled["closed_wall"]
            demand(reserved["identity"] == identity and reserved["source"] == a.SOURCE_ID
                   and reserved["url"] == settled["url"] == url and reserved["sequence"] == index
                   and settled["status"] == 200 and reserved["reservation"]["sha256"] == settled["reservation_sha256"]
                   and before["starts"] == after["starts"] and before["starts"][-1] <= opened <= closed
                   and after["not_before"] >= closed + max(before["interval"], after["interval"], 12.2)
                   and (not chronology or opened - chronology[-1]["closed"] >= before["interval"]),
                   "Exact previous reservation or closure differs")
            chronology.append(
                {"url": url, "opened": opened, "closed": closed,
                 "reserved": reserved["reservation"]["sha256"], "settled": settled["acknowledgement"]["sha256"]}
            )
        demand(
            sha256(canonical(chronology)).hexdigest() == prior["chronology_digest"]
            and terminal["sha256"] == chronology[-1]["settled"] == prior["state_digest"]
            and last_state == prior["state"] and not last_state["blocked"],
            "Newest retained previous pacing receipt differs",
        )
    return last_state, chronology


def restore_owned_history(a, scope):
    retained = {}
    chronology = []
    starts = set()
    for prior in scope["owners"]:
        state, confirmed = authenticate_owner(a, scope, prior)
        chronology.extend(confirmed)
        starts.update(state["starts"])
        starts.update(item["opened"] for item in confirmed)
        if not retained:
            retained = state.copy()
        else:
            for key in ("not_before", "interval", "page_interval", "rate_failures", "observed_at"):
                retained[key] = max(retained[key], state[key])
            for key in ("blocked", "legacy_rate_history_unknown"):
                retained[key] = retained[key] or state[key]
    retained["starts"] = sorted(starts)[-10:]
    demand(
        len(chronology) == 22 and {item["url"] for item in chronology} == set(scope["routes"])
        and all(left["closed"] < right["opened"] for left, right in zip(chronology, chronology[1:]))
        and a.validate_budget(retained) == scope["seed"] and not retained["blocked"],
        "Combined previous custody or actual source coverage differs",
    )
    return chronology


def install_seed(a, seed):
    with a.locked_budget():
        current = a.load_budget()
        for key in ("not_before", "interval", "page_interval", "rate_failures"):
            current[key] = max(current[key], seed[key])
        current["starts"] = sorted(set(current["starts"] + seed["starts"]))[-10:]
        for key in ("blocked", "legacy_rate_history_unknown"):
            current[key] = current[key] or seed[key]
        current["observed_at"] = time.time()
        a.save_budget(current)
        a._PUBLISHER_INTERVAL = max(a._PUBLISHER_INTERVAL, current["interval"])


def native_test(a, stdout, stderr):
    old_argv, old_adapter = sys.argv, sys.modules.get("adapter")
    try:
        sys.argv = [str(TEST)]
        sys.modules["adapter"] = a
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(str(TEST), run_name="__main__")
            except SystemExit as outcome:
                demand(outcome.code in (None, 0), "Original native collection failed")
    finally:
        sys.argv = old_argv
        if old_adapter is None:
            sys.modules.pop("adapter", None)
        else:
            sys.modules["adapter"] = old_adapter


def consume(a, records, checked):
    result = subprocess.run(
        ["node", str(CONSUMER)],
        input=canonical({"records": records, "metadata": a.metadata(checked, {}, records, checked_at=checked)}),
        capture_output=True, timeout=30, check=False,
    )
    demand(result.returncode == 0, "Current website consumer rejected the actual input")
    observed = json.loads(result.stdout)
    demand(observed == {"count": len(records), "sources": [a.SOURCE_ID],
                        "ids": sorted(row["id"] for row in records)},
           "Exact website consumer source or output records differ")
    return observed


def assess(a, scope, stdout, wire, chronology):
    records = json.loads(stdout.getvalue())
    a.validate_records(records)
    demand(
        [row["url"] for row in wire] == scope["routes"]
        and all(row.get("settlement_sha256") for row in wire),
        "Original native input or physical settlement is incomplete",
    )
    starts = [row["opened"] for row in chronology] + [row["opened_wall"] for row in wire]
    rolling = max(sum(stamp - 60 < seen <= stamp for seen in starts) for stamp in starts)
    demand(rolling <= 5, "Actual physical starts exceeded the rolling budget")
    captured = {row["url"]: base64.b64decode(row["body_base64"], validate=True) for row in wire[1:]}
    original_fetch = a.fetch_public
    try:
        a.fetch_public = lambda url, **kwargs: (200, {}, captured[url])
        replay = a.parse_inventory(a.read_pages())
    finally:
        a.fetch_public = original_fetch
    clock_fields = {"created_at", "updated_at", "first_seen_at", "last_seen_at", "last_checked_at"}
    def immutable(rows):
        return [{key: value for key, value in row.items() if key not in clock_fields} for row in rows]
    demand(immutable(replay) == immutable(records), "Complete actual input replay differs")
    checked = a.utc_now()
    filtered = a.remove_expired_records(records, checked)
    return {
        "raw": consume(a, records, checked),
        "filtered": consume(a, filtered, checked),
        "actual_rolling_max": rolling,
        "stdout_sha256": sha256(stdout.getvalue().encode()).hexdigest(),
    }


def main():
    scope = json.loads(SCOPE.read_bytes())
    demand(
        scope.get("enabled") is True and scope.get("issue") == 365
        and scope.get("origin") == REPOSITORY and scope.get("branch") == BRANCH
        and scope.get("workflow") == WORKFLOW,
        "Croatia archive native QA remains OFF",
    )
    demand(not {"DATA_SOURCE_TOKEN", "STUDYINCROATIA_PACING_ARTIFACT_PATH"} & os.environ.keys(),
           "Native QA requires isolated nonpublishing credentials")
    demand(sha256(CERTIFICATE.read_bytes()).hexdigest() == scope["reviewer_sha256"],
           "Reviewed native recipient differs")
    seal(b"Croatia native QA CMS preflight")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True).strip()
    demand(re.fullmatch(r"[0-9a-f]{40}", head) and head == os.environ.get("GITHUB_SHA"),
           "Exact hosted revision differs")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", WORKFLOW,
                    "adapters/hr-study-in-croatia", "qa/croatia-archive"], cwd=CHECKOUT, check=True)
    source = SOURCE.read_bytes()
    demand(source.count(NEW_SIGNATURE) == source.count(NEW_MODE) == 1
           and sha256(source.replace(NEW_SIGNATURE, OLD_SIGNATURE).replace(NEW_MODE, OLD_MODE)).hexdigest()
           == scope["candidate_adapter_sha256"], "Candidate adapter literal reversal differs")
    demand(sha256(TEST.read_bytes()).hexdigest() == scope["test_sha256"], "Native test revision differs")
    website_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=WEBSITE, text=True).strip()
    website_consumer = WEBSITE / "scripts/data.mjs"
    demand(website_head == scope["website_head"] and
           sha256(website_consumer.read_bytes()).hexdigest() == scope["website_consumer_sha256"],
           "Exact website consumer revision differs")
    website_paths = ("scripts/data.mjs", "assets/discovery.js", "package.json")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *website_paths], cwd=WEBSITE, check=True)
    protected = (SOURCE, TEST, CONSUMER, SCOPE, CERTIFICATE, Path(__file__), CHECKOUT / WORKFLOW,
                 *(WEBSITE / path for path in website_paths))
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in protected}

    def pins():
        demand(all(sha256(path.read_bytes()).hexdigest() == value for path, value in hashes.items()),
               "Reviewed code or evidence inputs changed")

    spec = importlib.util.spec_from_file_location("croatia_adapter", SOURCE)
    a = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)
    demand(scope["routes"] == [ROBOTS, *(item["url"] for item in a.INPUTS.values())]
           and scope["inventory_routes"] == [item["url"] for key, item in a.INPUTS.items()
                                               if key == "news" or key.startswith("news-page")],
           "Exact native publisher input scope differs")
    original_continuity(a, scope)
    output = Path(os.environ["RUNNER_TEMP"]) / "croatia-archive-native"
    output.mkdir(mode=0o700)
    wire, acknowledgements, persistence = [], [], []
    stdout, stderr = io.StringIO(), io.StringIO()
    identity = assessment = post_state = failure = None
    chronology = []
    instrument(a, scope, pins, wire, acknowledgements, persistence, lambda: identity, output)
    try:
        with a.execution(), contextlib.redirect_stderr(stderr):
            try:
                with a.phase("prepublication", 900):
                    identity = authenticate(a, scope, head)
                    paused(a, scope)
                    chronology = restore_owned_history(a, scope)
                    a.prepare_collection()
                    install_seed(a, scope["seed"])
                    native_test(a, stdout, stderr)
                    assessment = assess(a, scope, stdout, wire, chronology)
            finally:
                if acknowledgements and not a._PUBLISHER_GATE_FAILED and not a._JOURNAL_FATAL:
                    with a.phase("export", 30), a.locked_budget():
                        current = original_ack(a)
                        demand(current["sha256"] == acknowledgements[-1]["sha256"]
                               and current["generation"] == acknowledgements[-1]["generation"],
                               "Final original journal custody differs")
                        post_state = current
    except BaseException as error:
        failure = type(error).__name__
        stderr.write(str(error))
    pending = {path.name[-7:] for path in output.glob("pending-*.json")}
    settled = {path.name[-7:] for path in output.glob("settled-*.json")}
    qualified = assessment is not None and post_state is not None and failure is None and pending == settled
    capture = {
        "identity": identity, "wire": wire, "acknowledgements": acknowledgements,
        "persistence": persistence, "assessment": assessment, "previous_chronology": chronology,
        "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
    }
    ciphertext = seal(canonical(capture))
    store(output / "capture.cms", ciphertext)
    evidence = {
        "identity": identity, "native_complete": qualified, "failure_type": failure,
        "physical_opens": len(wire), "capture_sha256": sha256(ciphertext).hexdigest(),
        "qualified_native_ack": post_state if qualified else None,
        "unqualified_latest_ack": acknowledgements[-1] if acknowledgements and not qualified else None,
        "unresolved_attempt": pending != settled,
        "raw_count": assessment["raw"]["count"] if assessment else None,
        "filtered_count": assessment["filtered"]["count"] if assessment else None,
    }
    store(output / "native-evidence.json", canonical(evidence))
    print(json.dumps({key: evidence[key] for key in
                      ("identity", "native_complete", "failure_type", "physical_opens",
                       "unresolved_attempt", "raw_count", "filtered_count")}))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
