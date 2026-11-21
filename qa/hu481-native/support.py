"""Authenticate the exact hosted Fulbright Hungary native collector."""

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
BRANCH = "qa/hungary481-native-validation"
WORKFLOW = ".github/workflows/qa-hungary481-native-validation.yml"
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
    previous_assessment = scope["production_owner"]["completed_at"]
    approvals = a.workflow_api(f"/issues/{issue}/comments?since={previous_assessment}&per_page=100")
    demand(isinstance(approvals, list) and len(approvals) < 100, "Incomplete grant inventory")
    lines = {f"Approved Hungary native test head {head}",
             f"Hungary native test scope {sha256(canonical(scope)).hexdigest()}"}
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
    demand(isinstance(status.get("created_at"), str) and created < status["created_at"],
           "Grant must predate the original native run")
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
    expected_job = {"run_id": run, "run_attempt": 1, "name": "native",
                    "head_sha": head, "head_branch": BRANCH, "status": "in_progress"}
    demand(all(job.get(k) == v for k, v in expected_job.items())
           and type(job.get("id")) is int and job["id"] > 0, "Active research job identity differs")
    started = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
    elapsed = time.time() - started.timestamp()
    demand(started.utcoffset() is not None and -1 <= elapsed < 300,
           "Active research job start time differs")
    a._RUN_END = min(a._RUN_END, time.monotonic() + 1800 - max(elapsed, 0) - 2)
    return {"run_id": run, "run_attempt": 1, "workflow_id": workflow,
            "job_id": job["id"], "head": head, "grant": grant["id"], "ref": ref,
            "grant_created": created, "grant_sha256": sha256(grant["body"].encode()).hexdigest()}


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


def original_ack(a):
    with a.locked_budget():
        a.verify_journal()
        generation, raw = a.journal_snapshot()
        demand(0 < len(raw) <= 16384 and generation == a._JOURNAL_VERSION
               and raw == a._JOURNAL_BYTES, "Readback differs from original journal ACK")
        return {"sha256": sha256(raw).hexdigest(), "generation": list(generation),
                "raw_base64": base64.b64encode(raw).decode(),
                "state": a.validate_budget(json.loads(raw))}




def verify_grant(a, identity, scope):
    grant = a.workflow_api(f"/issues/comments/{identity['grant']}")
    demand(grant.get("id") == identity["grant"] and owner(grant.get("user"), scope["maintainer_account"])
           and grant.get("issue_url") == f"https://api.github.com/repos/{REPOSITORY}/issues/{scope['issue']}"
           and grant.get("created_at") == grant.get("updated_at") == identity["grant_created"]
           and isinstance(grant.get("body"), str) and sha256(grant["body"].encode()).hexdigest() == identity["grant_sha256"],
           "Native grant changed or was withdrawn")
