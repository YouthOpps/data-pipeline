"""Read one bounded page through the original Charles publisher transport."""

import base64
import contextlib
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
SOURCE = CHECKOUT / "adapters/cz-charles-university/adapter.py"
SCOPE = HERE / "scope.json"
CERTIFICATE = HERE / "reviewer.pem"
REPOSITORY = "YouthOpps/data-pipeline"
BRANCH = "qa/charles-international-research"
WORKFLOW = ".github/workflows/qa-charles-international-research.yml"
ROUTES = ("https://cuni.cz/robots.txt", "https://cuni.cz/UKEN-2497.html")
PREVIOUS_ASSESSMENT = "2026-11-17T08:35:46Z"
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
        "GITHUB_ACTOR_ID": str(account), "GITHUB_SHA": head,
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
    approvals = a.workflow_api(f"/issues/{issue}/comments?per_page=100")
    demand(isinstance(approvals, list) and len(approvals) < 100, "Incomplete grant inventory")
    lines = {f"Approved Charles research head {head}",
             f"Charles research scope {sha256(canonical(scope)).hexdigest()}"}
    grants = [item for item in approvals if owner(item.get("user"), account)
              and isinstance(item.get("body"), str) and lines.issubset(item["body"].splitlines())]
    demand(len(grants) == 1, "Exact maintainer research grant unavailable or ambiguous")
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
    a.local_workflow_definition()
    snapshot = a.workflow_api(f"/actions/workflows/{scope['publisher_workflow']}/runs?per_page=100&page=1")
    verify_inventory(snapshot, scope)


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
        rows = sorted((run, attempt, head, workflow, "completed")
                      for (run, attempt), (head, workflow) in a._SCANNED_HISTORY.items())
        demand(len(rows) == scope["production_inventory_count"]
               and sha256(canonical(rows)).hexdigest() == scope["production_inventory_sha256"],
               "Original production chronology changed")
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


def instrument(a, scope, pins, wire, acknowledgements):
    opening = a._OPENER.open
    writing = a.journal_write

    def observed_write(state):
        writing(state)
        ack = original_ack(a)
        demand(ack["state"] == state, "Persisted original journal contents differ")
        ack["acknowledged_at"] = time.monotonic()
        acknowledgements.append(ack)

    def observed_open(request, *args, **kwargs):
        url = request.full_url
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if host != "cuni.cz" and not host.endswith(".cuni.cz"):
            action_api = host == "api.github.com" and parsed.path.startswith("/repos/" + REPOSITORY + "/")
            owned_transfer = (
                a.verified_artifact_transfer and host == scope["owned_transfer_host"]
                and request.get_header("Authorization") is None and request.get_header("Cookie") is None
            )
            demand(parsed.scheme == "https" and parsed.port is None and parsed.username is None
                   and parsed.password is None and request.get_method() == "GET"
                   and (action_api or owned_transfer), "Research infrastructure route differs")
            return opening(request, *args, **kwargs)
        demand(len(wire) < len(ROUTES) and url == ROUTES[len(wire)] and request.get_method() == "GET"
               and request.get_header("Authorization") is None and request.get_header("Cookie") is None
               and request.get_header("User-agent") == a._USER_AGENT,
               "Publisher request is outside the exact two-GET research scope")
        paused(a, scope)
        pins()
        a.journal_before_wire()
        reserved = original_ack(a)
        demand(acknowledgements and acknowledgements[-1]["sha256"] == reserved["sha256"],
               "Actual publisher open lacks exact preceding durable reservation")
        row = {"url": url, "method": "GET", "user_agent": a._USER_AGENT,
               "reservation_sha256": reserved["sha256"], "opened_at": time.monotonic(),
               "opened_wall": time.time(), "inventory_page": a._INVENTORY_ACTIVE}
        wire.append(row)
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

    a.journal_write = observed_write
    a._OPENER.open = observed_open


def verify_wire(wire, acknowledgements):
    demand([row["url"] for row in wire] == list(ROUTES), "Exact scoped publisher wire incomplete")
    required_gap = 12.2
    for index, row in enumerate(wire):
        status = row.get("status")
        rep = index == 0
        demand(type(status) is int and (status == 200 or rep and status == 404)
               and row.get("framed") is True and row.get("closed") is True
               and row.get("inventory_page") is not rep,
               "Publisher wire is unframed, unclosed or not approved content")
        preceding = [ack for ack in acknowledgements if ack["acknowledged_at"] <= row["opened_at"]]
        demand(preceding and preceding[-1]["sha256"] == row["reservation_sha256"],
               "Actual response does not match its preceding reservation")
        reserved = preceding[-1]["state"]
        demand(0 < len(reserved["starts"]) <= 5 and reserved["starts"][-1] <= row["opened_wall"],
               "Actual response reservation differs from the start limit")
        required_gap = max(required_gap, reserved["interval"])
        following = wire[index + 1]["opened_at"] if index + 1 < len(wire) else float("inf")
        settled = [ack for ack in acknowledgements if row["closed_at"] <= ack["acknowledged_at"] < following
                   and ack["state"]["completed"] >= row["closed_wall"]
                   and ack["state"]["starts"] == reserved["starts"]]
        demand(settled, "Actual response closure lacks a matching subsequent original acknowledgement")
        row["settlement_sha256"] = settled[0]["sha256"]
    demand(wire[1]["opened_at"] - wire[0]["closed_at"] >= required_gap,
           "Actual physical pacing gap differs")


def main():
    scope = json.loads(SCOPE.read_bytes())
    demand(
        scope.get("enabled") is True and type(scope.get("issue")) is int and scope["issue"] > 0
        and scope.get("origin") == REPOSITORY and scope.get("branch") == BRANCH
        and scope.get("workflow") == WORKFLOW and tuple(scope.get("routes", [])) == ROUTES,
        "Charles research remains OFF",
    )
    demand("DATA_SOURCE_TOKEN" not in os.environ and "CHARLES_PACING_ARTIFACT_PATH" not in os.environ,
           "Research requires isolated nonpublishing credentials")
    demand(isinstance(scope["reviewer_sha256"], str)
           and sha256(CERTIFICATE.read_bytes()).hexdigest() == scope["reviewer_sha256"],
           "Reviewed research recipient differs")
    seal(b"Charles international research CMS preflight")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True).strip()
    demand(re.fullmatch(r"[0-9a-f]{40}", head) and head == os.environ.get("GITHUB_SHA"),
           "Exact hosted revision differs")
    subprocess.run(["git", "diff", "--quiet", "HEAD", "--", WORKFLOW,
                    "adapters/cz-charles-university", "qa/charles-international"], cwd=CHECKOUT, check=True)
    source = SOURCE.read_bytes()
    demand(source.count(NEW_SIGNATURE) == source.count(NEW_MODE) == 1
           and sha256(source.replace(NEW_SIGNATURE, OLD_SIGNATURE).replace(NEW_MODE, OLD_MODE)).hexdigest()
           == scope["original_adapter_sha256"], "Original adapter literal reversal differs")
    protected = (SOURCE, SCOPE, CERTIFICATE, Path(__file__), CHECKOUT / WORKFLOW)
    hashes = {path: sha256(path.read_bytes()).hexdigest() for path in protected}

    def pins():
        demand(all(sha256(path.read_bytes()).hexdigest() == value for path, value in hashes.items()),
               "Reviewed code or evidence inputs changed")

    spec = importlib.util.spec_from_file_location("charles_adapter", SOURCE)
    a = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)
    a.workflow_api = a.local_workflow_api
    demand(a.REVIEWED_CONDITIONS["international"] == scope["reviewed_international_text_sha256"],
           "Original reviewed international facts guard differs")
    original_continuity(a, scope)
    output = Path(os.environ["RUNNER_TEMP"]) / "charles-international-research"
    output.mkdir(mode=0o700)
    wire, acknowledgements, diagnostics = [], [], io.StringIO()
    identity = None
    assessment = None
    post_state = None
    failure = None
    instrument(a, scope, pins, wire, acknowledgements)
    try:
        with a.execution(), contextlib.redirect_stderr(diagnostics):
            try:
                with a.phase("restore", 300):
                    identity = authenticate(a, scope, head)
                    paused(a, scope)
                    a.prepare_collection()
                with a.phase("collection", 180):
                    node = a.content(a.page(ROUTES[1]), ROUTES[1])
                    review_text = node.text().split("Last change:")[0]
                    verify_wire(wire, acknowledgements)
                    assessment = {"observed_text_sha256": sha256(review_text.encode()).hexdigest(),
                                  "reviewed_text_sha256": scope["reviewed_international_text_sha256"],
                                  "review_text": review_text}
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
        diagnostics.write(str(error))
    qualified = assessment is not None and post_state is not None and failure is None
    capture = {"identity": identity, "wire": wire, "acknowledgements": acknowledgements,
               "assessment": assessment, "diagnostics": diagnostics.getvalue()}
    ciphertext = seal(canonical(capture))
    store(output / "capture.cms", ciphertext)
    evidence = {"identity": identity, "research_complete": qualified, "failure_type": failure,
                "physical_opens": len(wire), "capture_sha256": sha256(ciphertext).hexdigest(),
                "qualified_research_ack": post_state if qualified else None,
                "unqualified_latest_ack": acknowledgements[-1] if acknowledgements and not qualified else None,
                "observed_text_sha256": assessment["observed_text_sha256"] if qualified else None}
    store(output / "research-evidence.json", canonical(evidence))
    print(json.dumps({k: evidence[k] for k in
                      ("identity", "research_complete", "failure_type", "physical_opens", "observed_text_sha256")}))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
