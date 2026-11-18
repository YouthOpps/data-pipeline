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


def check_retired(adapter, scope, prior, demand):
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
        "head_branch": prior["branch"], "event": "push", "status": "completed",
        "conclusion": prior["conclusion"],
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


def read_state(adapter, scope, demand, *, prior=None):
    prior = scope["predecessor"] if prior is None else prior
    check_retired(adapter, scope, prior, demand)
    run = prior["run"]
    jobs = adapter.workflow_api(f"/actions/runs/{run}/attempts/1/jobs?per_page=100")
    job_fields = {
        "id": prior["job"], "run_id": run, "run_attempt": 1, "head_sha": prior["head"],
        "status": "completed", "conclusion": prior["conclusion"], "name": prior["job_name"],
        "completed_at": prior["job_finished"],
    }
    demand(
        jobs.get("total_count") == 1 and len(jobs.get("jobs", [])) == 1
        and all(jobs["jobs"][0].get(key) == value for key, value in job_fields.items()),
        "Exact predecessor job custody changed",
    )
    artifacts = adapter.workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    required = {
        "id": prior["artifact"], "name": prior["artifact_name"],
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
    size_limit = prior["artifact_size"]
    demand(type(size_limit) is int and 0 < size_limit <= 5_000_000,
           "Authenticated predecessor size exceeds the original transport bound")
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
        status, _, data = adapter.request_bytes(url, byte_limit=size_limit)
    finally:
        adapter.verified_artifact_transfer = None
    demand(status == 200, "Predecessor archive bytes unavailable")
    return data


def member_limit(prior):
    return 8_388_608 if "observed_reservations" in prior else 262144


def decode_artifact(adapter, prior, raw, demand):
    demand(len(raw) == prior["artifact_size"]
           and "sha256:" + sha256(raw).hexdigest() == prior["artifact_digest"],
           "Exact predecessor archive bytes differ")
    zero = prior.get("journal_terminal") == "never-opened"
    newer_research = prior.get("journal_terminal") == "settled-research"
    failed = prior["conclusion"] == "failure"
    evidence_name = "native-evidence.json" if failed and not zero else "research-evidence.json"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        demand(len(members) == 2 and {m.filename for m in members} == {"capture.cms", evidence_name}
               and all(not m.flag_bits & 1 and stat.S_IFMT(m.external_attr >> 16) != stat.S_IFLNK
                       and 0 < m.file_size <= member_limit(prior) for m in members),
               "Unexpected predecessor archive members")
        evidence = json.loads(archive.read(evidence_name))
        capture = archive.read("capture.cms")
    identity = {
        "run_id": prior["run"], "run_attempt": 1, "workflow_id": prior["workflow_id"],
        "job_id": prior["job"], "head": prior["head"], "grant": prior["grant"],
        "ref": "refs/heads/" + prior["branch"],
    }
    if zero:
        expected_count = 0
    elif newer_research:
        expected_count = 5
    elif "observed_reservations" in prior:
        expected_count = 21
    else:
        expected_count = 2
    demand(evidence.get("identity") == identity and evidence.get("physical_opens") == expected_count
           and evidence.get("capture_sha256") == sha256(capture).hexdigest(),
           "Predecessor evidence identity differs")
    if zero:
        demand(failed and prior["job_name"] == "research"
               and len(prior["physical_receipt_sha256"]) == 64
               and evidence.get("research_complete") is False
               and evidence.get("failure_type") == "AdapterError"
               and all(key in evidence and evidence[key] is None for key in (
                   "qualified_research_ack", "unqualified_latest_ack",
                   "predecessor_merged_ack_sha256", "leaf_material_sha256",
               )), "Prior zero-wire research scope contradicts archived evidence")
        return None
    if failed:
        demand(evidence.get("native_started") is True
               and evidence.get("qualification_complete") is False
               and evidence.get("original_native_pass") is False
               and evidence.get("qualified_native_ack") is None
               and evidence.get("failure_type") == "RuntimeError",
               "Predecessor must retain the original failed qualification")
        ack = evidence.get("unqualified_latest_ack")
    else:
        demand(evidence.get("research_complete") is True
               and evidence.get("failure_type") is None
               and evidence.get("unqualified_latest_ack") is None
               and (evidence.get("leaf_material_sha256") == prior["leaf_material_sha256"]
                    if newer_research else
                    evidence.get("observed_text_sha256") == prior["observed_text_sha256"]),
               "Predecessor does not bind the qualified research outcome")
        ack = evidence.get("qualified_research_ack")
    demand(isinstance(ack, dict), "Predecessor exact ACK unavailable")
    original = base64.b64decode(ack["raw_base64"], validate=True)
    generation = ack.get("generation")
    demand(isinstance(generation, list) and len(generation) == 3 and generation[0] == prior["state_size"]
           and len(original) == ack.get("bytes") == prior["state_size"]
           and sha256(original).hexdigest() == ack.get("sha256") == prior["state_sha256"]
           and json.loads(original) == ack.get("state"), "Predecessor original journal bytes differ")
    state = adapter.validate_budget(ack["state"])
    if failed or newer_research:
        # Import confirmed reservations and measured opens without advancing the
        # original completion/rate-limit/refusal fields. Never adopt a failed or
        # physically unsettled acknowledgement on the basis of an exit status.
        actual = prior["observed_opens"]
        reserved = prior.get("observed_reservations", state["starts"])
        demand(prior.get("journal_terminal") in {"settled", "settled-research"} or expected_count == 2,
               "Prior terminal journal lacks reviewed physical settlement")
        demand(state["completed"] == prior["completion_floor"]
               and len(prior["physical_receipt_sha256"]) == 64
               and isinstance(actual, list) and actual == sorted(set(actual))
               and isinstance(reserved, list) and reserved == sorted(set(reserved))
               and len(actual) == len(reserved) == expected_count
               and len(state["starts"]) > 0
               and all(reservation <= opening <= state["completed"]
                       for reservation, opening in zip(reserved, actual))
               and state["starts"] == reserved[-len(state["starts"]):],
               "Reviewed physical completion or actual opener evidence differs")
        state = dict(state, starts=sorted(set(reserved + actual)))
    return adapter.validate_budget(state)
