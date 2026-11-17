"""Authenticate and decode the separate retired Charles research owner."""

import base64
from hashlib import sha256
import io
import json
import os
import stat
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request
import zipfile


def read_state(adapter, scope, demand):
    prior = scope["predecessor"]
    repo, run, workflow = scope["origin"], prior["run"], prior["workflow_id"]
    definition = adapter.workflow_api(f"/actions/workflows/{workflow}")
    demand(
        definition.get("id") == workflow
        and definition.get("path") == prior["workflow_path"]
        and definition.get("state") == "disabled_manually",
        "Approved predecessor workflow is not retired",
    )
    status = adapter.workflow_api(f"/actions/runs/{run}/attempts/1")
    fields = {
        "id": run, "run_attempt": 1, "workflow_id": workflow,
        "path": prior["workflow_path"], "head_sha": prior["head"],
        "head_branch": prior["branch"], "event": "push", "status": "completed", "conclusion": "success",
    }
    history = adapter.workflow_api(f"/actions/workflows/{workflow}/runs?per_page=100")
    runs = history.get("workflow_runs")
    demand(
        history.get("total_count") == 1 and isinstance(runs, list) and len(runs) == 1
        and all(status.get(key) == value and runs[0].get(key) == value for key, value in fields.items())
        and status.get("repository", {}).get("full_name") == repo
        and status.get("head_repository", {}).get("full_name") == repo,
        "Exact predecessor chronology changed",
    )
    jobs = adapter.workflow_api(f"/actions/runs/{run}/attempts/1/jobs?per_page=100")
    job_fields = {
        "id": prior["job"], "run_id": run, "run_attempt": 1, "head_sha": prior["head"],
        "status": "completed", "conclusion": "success", "name": "research",
        "completed_at": prior["job_finished"],
    }
    demand(
        jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1
        and all(jobs["jobs"][0].get(key) == value for key, value in job_fields.items()),
        "Exact predecessor job custody changed",
    )
    artifacts = adapter.workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    required = {
        "id": prior["artifact"], "name": "charles-international-research-1",
        "digest": prior["artifact_digest"], "expired": False, "size_in_bytes": prior["artifact_size"],
    }
    demand(
        artifacts.get("total_count") == 1 and len(artifacts.get("artifacts", [])) == 1
        and all(artifacts["artifacts"][0].get(key) == value for key, value in required.items())
        and artifacts["artifacts"][0].get("workflow_run", {}).get("id") == run
        and artifacts["artifacts"][0].get("workflow_run", {}).get("head_sha") == prior["head"],
        "Exact predecessor artifact custody changed",
    )
    raw = download_artifact(adapter, prior, demand)
    return decode_artifact(adapter, prior, raw, demand)


def download_artifact(adapter, prior, demand):
    request = Request(
        adapter._WORKFLOW_GITHUB + f"/actions/artifacts/{prior['artifact']}/zip",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"], "User-Agent": adapter._USER_AGENT},
    )
    try:
        response = adapter._OPENER.open(request, timeout=adapter.request_timeout())
    except HTTPError as error:
        response = error
    with response:
        demand(response.status == 302, "Exact predecessor archive redirect unavailable")
        url = response.headers.get("Location", "")
        response.read(1001)
    destination = urlsplit(url)
    demand(destination.scheme == "https" and destination.hostname == prior["artifact_host"]
           and destination.port is None and destination.username is None and destination.password is None,
           "Predecessor archive storage differs")
    adapter.verified_artifact_transfer = prior["artifact_host"]
    try:
        status, _, data = adapter.request_bytes(url, byte_limit=262144)
    finally:
        adapter.verified_artifact_transfer = None
    demand(status == 200, "Predecessor archive bytes unavailable")
    return data


def decode_artifact(adapter, prior, raw, demand):
    demand(len(raw) == prior["artifact_size"]
           and "sha256:" + sha256(raw).hexdigest() == prior["artifact_digest"],
           "Exact predecessor archive bytes differ")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        demand(len(members) == 2 and {m.filename for m in members} == {"capture.cms", "research-evidence.json"}
               and all(not m.flag_bits & 1 and stat.S_IFMT(m.external_attr >> 16) != stat.S_IFLNK
                       and 0 < m.file_size <= 262144 for m in members),
               "Unexpected predecessor archive members")
        evidence = json.loads(archive.read("research-evidence.json"))
        capture = archive.read("capture.cms")
    identity = {
        "run_id": prior["run"], "run_attempt": 1, "workflow_id": prior["workflow_id"],
        "job_id": prior["job"], "head": prior["head"], "grant": prior["grant"],
        "ref": "refs/heads/" + prior["branch"],
    }
    demand(evidence.get("identity") == identity and evidence.get("research_complete") is True
           and evidence.get("physical_opens") == 2 and evidence.get("failure_type") is None
           and evidence.get("unqualified_latest_ack") is None
           and evidence.get("observed_text_sha256") == prior["observed_text_sha256"]
           and evidence.get("capture_sha256") == sha256(capture).hexdigest(),
           "Predecessor does not bind the qualified research outcome")
    ack = evidence.get("qualified_research_ack")
    demand(isinstance(ack, dict), "Predecessor exact ACK unavailable")
    original = base64.b64decode(ack["raw_base64"], validate=True)
    generation = ack.get("generation")
    demand(isinstance(generation, list) and len(generation) == 3 and generation[0] == prior["state_size"]
           and len(original) == ack.get("bytes") == prior["state_size"]
           and sha256(original).hexdigest() == ack.get("sha256") == prior["state_sha256"]
           and json.loads(original) == ack.get("state"), "Predecessor original journal bytes differ")
    return adapter.validate_budget(ack["state"])
