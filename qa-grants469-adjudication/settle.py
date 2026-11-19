"""Verify the signed disposition of one unsent reservation without publisher access."""

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path.cwd()
QA = ROOT / "qa-grants469-adjudication"
REPO = "YouthOpps/data-pipeline"
API = f"https://api.github.com/repos/{REPO}"
BRANCH = "qa/grants-at-fh469-adjudicate-r2"
WORKFLOW = ".github/workflows/settle-at-grants-at-fh469-r2.yml"
MAIN = "483b219c403414050f4c5868e8a93e990f9376d5"
CERT_SHA = "c8bfa742ffcd2638a1926c8068b58c3f0cc9bcda7077b95ab3ac8a8ac5b01531"
ACTOR = 12278069


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def require(test, reason):
    if not test:
        raise RuntimeError(reason)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def check(row, **wanted):
    require(all(key in row and row[key] == value and type(row[key]) is type(value)
                for key, value in wanted.items()), "Exact GitHub identity or state changed")


def checked_body(url, *, authenticated, cap):
    parts = urllib.parse.urlsplit(url)
    require(parts.scheme == "https" and parts.hostname and not parts.username
            and not parts.password and parts.port is None, "Untrusted infrastructure address")
    headers = {"Accept": "application/vnd.github+json"}
    if authenticated:
        require(url.startswith(API + "/"), "Unexpected GitHub API scope")
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    else:
        require(parts.hostname.endswith((".blob.core.windows.net",
                                         ".actions.githubusercontent.com")),
                "Unexpected GitHub artifact host")
    req = urllib.request.Request(url, headers=headers)
    try:
        response = OPENER.open(req, timeout=12)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(cap + 1)
        require(len(raw) <= cap, "Infrastructure response exceeds the review scope")
        return response.status, response.headers, raw


def api(path):
    status, _, raw = checked_body(API + path, cap=200000, authenticated=True)
    require(status == 200, f"GitHub authority read returned HTTP {status}")
    return json.loads(raw)


def artifact_bytes(owner, identifier, digest, name, listing, size):
    by_id = [value for value in listing if value.get("id") == identifier]
    require(len(by_id) == 1, "Exact owner artifact is absent")
    check(by_id[0], id=identifier, name=name, digest="sha256:" + digest,
          expired=False, size_in_bytes=size)
    check(by_id[0]["workflow_run"], id=owner["id"], head_sha=owner["head_sha"])
    status, headers, _ = checked_body(
        API + f"/actions/artifacts/{identifier}/zip", cap=1024, authenticated=True)
    require(status == 302 and headers.get("Location"), "Missing GitHub artifact URL")
    status, _, raw = checked_body(headers["Location"], cap=size, authenticated=False)
    require(status == 200 and len(raw) == size and sha(raw) == digest,
            "Original GitHub archive digest differs")
    return raw


def unzip(raw, names):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        require(sorted(item.filename for item in members) == sorted(names)
                and all(not item.flag_bits & 1 and item.file_size <= 96000 for item in members),
                "Unexpected original artifact archive")
        return {name: archive.read(name) for name in names}


def verify_review(head):
    files = (QA / "proof.json", QA / "proof.cms", QA / "reviewer.pem", QA / "settle.py",
             ROOT / WORKFLOW)
    for path in files:
        committed = subprocess.check_output(
            ["git", "show", f"{head}:{path.relative_to(ROOT)}"], cwd=ROOT, timeout=10)
        require(path.resolve() == path and path.read_bytes() == committed,
                "Local review file differs from approved Git bytes")
    require(sha((QA / "reviewer.pem").read_bytes()) == CERT_SHA,
            "Review certificate changed")
    outcome = subprocess.run(
        ["openssl", "cms", "-verify", "-binary", "-inform", "DER", "-nointern",
         "-in", str(QA / "proof.cms"), "-content", str(QA / "proof.json"),
         "-certfile", str(QA / "reviewer.pem"), "-CAfile", str(QA / "reviewer.pem"),
         "-purpose", "any", "-out", os.devnull],
        capture_output=True, timeout=10, check=False)
    require(outcome.returncode == 0, "The exact detached reviewer proof is absent or invalid")
    return json.loads((QA / "proof.json").read_bytes())


def verify_hosted():
    expected = {
        "GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": f"refs/heads/{BRANCH}", "GITHUB_ACTOR_ID": str(ACTOR),
        "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "adjudicate",
        "GITHUB_WORKFLOW_REF": f"{REPO}/{WORKFLOW}@refs/heads/{BRANCH}",
    }
    require(all(os.environ.get(key) == value for key, value in expected.items())
            and "DATA_SOURCE_TOKEN" not in os.environ and os.environ.get("GITHUB_TOKEN"),
            "Adjudication is outside the no-publication scope")
    head = os.environ.get("GITHUB_SHA", "")
    expected_id = os.environ.get("GITHUB_RUN_ID", "")
    require(re.fullmatch(r"[0-9a-f]{40}", head) and os.environ.get("GITHUB_WORKFLOW_SHA") == head
            and re.fullmatch(r"[1-9][0-9]*", expected_id)
            and subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=10).strip() == head,
            "Incorrect immutable adjudication job")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    check(event, created=True, deleted=False, before="0" * 40, after=head, ref=expected["GITHUB_REF"])
    check(event["sender"], id=ACTOR)
    check(event["repository"], full_name=REPO)
    run = api(f"/actions/runs/{expected_id}/attempts/1")
    check(run, id=int(expected_id), status="in_progress", event="push", run_attempt=1,
          head_branch=BRANCH, head_sha=head, path=WORKFLOW)
    check(run["actor"], id=ACTOR, type="User")
    check(run["triggering_actor"], id=ACTOR, type="User")
    check(run["head_repository"], full_name=REPO)
    check(run["repository"], full_name=REPO)
    workflow_id = run["workflow_id"]
    check(api(f"/actions/workflows/{workflow_id}"), id=workflow_id,
          path=WORKFLOW, state="active")
    all_runs = api(f"/actions/workflows/{workflow_id}/runs?per_page=2")
    check(all_runs, total_count=1)
    require([row["id"] for row in all_runs["workflow_runs"]] == [run["id"]],
            "Adjudication has a competing attempt")
    jobs = api(f"/actions/runs/{expected_id}/attempts/1/jobs?per_page=2")
    check(jobs, total_count=1)
    job, = jobs["jobs"]
    check(job, status="in_progress", name="adjudicate", run_id=run["id"], head_sha=head)
    check(api("/branches/main")["commit"], sha=MAIN)
    return run, job, verify_review(head)


def previous_owner(identifier, run_id, job_id, path, branch, head, outcome, sole):
    check(api(f"/actions/workflows/{identifier}"), id=identifier,
          path=path, state="disabled_manually")
    result = api(f"/actions/workflows/{identifier}/runs?per_page={2 if sole else 1}")
    if sole:
        check(result, total_count=1)
    previous, = result["workflow_runs"]
    detailed = api(f"/actions/runs/{run_id}/attempts/1")
    for row in (previous, detailed):
        check(row, workflow_id=identifier, id=run_id, run_attempt=1, status="completed",
              conclusion=outcome, head_branch=branch, head_sha=head, path=path)
        check(row["head_repository"], full_name=REPO)
        check(row["repository"], full_name=REPO)
    jobs = api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=2")
    check(jobs, total_count=1)
    job, = jobs["jobs"]
    check(job, id=job_id, run_id=run_id, head_sha=head, status="completed", conclusion=outcome)
    return previous


def read_evidence(proof):
    check(api("/actions/workflows/423719685"), id=423719685,
          path=".github/workflows/verify-at-grants-at-fh469.yml", state="disabled_manually")
    prior = previous_owner(
        proof["production_workflow"], proof["production_run"], proof["production_job"],
        ".github/workflows/fetch-at-grants-at.yml", "main", proof["production_head"], "success", False)
    native = previous_owner(
        proof["native_workflow"], proof["native_run"], proof["native_job"],
        ".github/workflows/verify-at-grants-at-fh469-r2.yml", "qa/grants-at-fh469-native-r2",
        proof["native_head"], "failure", True)
    prior_listing = api(f'/actions/runs/{prior["id"]}/artifacts?per_page=2')
    native_listing = api(f'/actions/runs/{native["id"]}/artifacts?per_page=3')
    check(prior_listing, total_count=1)
    check(native_listing, total_count=2)
    return (
        artifact_bytes(prior, proof["production_artifact"], proof["production_archive_sha256"],
                       "grants-at-pacing-state-1", prior_listing["artifacts"], 862),
        artifact_bytes(native, proof["native_state_artifact"], proof["native_state_archive_sha256"],
                       "grants-at-fh469-qa-state-1", native_listing["artifacts"], 884),
        artifact_bytes(native, proof["native_result_artifact"], proof["native_result_archive_sha256"],
                       "grants-at-fh469-qa-result-1", native_listing["artifacts"], 21519),
    )


def derive(proof, production_zip, state_zip, evidence_zip):
    check(proof, source="at-grants-at", kind="independently-reviewed-unsent-physical-reservation",
          publisher_opens=0, authenticated_journal_writes=5, preserved_start_union_count=23)
    production = json.loads(unzip(production_zip, ["pacing-state.json"])["pacing-state.json"])
    latest = json.loads(unzip(state_zip, ["pacing-state.json"])["pacing-state.json"])
    retained = unzip(evidence_zip, ["manifest.json", "evidence.cms"])
    report = json.loads(retained["manifest.json"])
    check(report, observer="failed", original_outcome=1, publisher_requests=0,
          run_id=proof["native_run"], head_sha=proof["native_head"],
          owned_state_sha256=proof["original_state_sha256"],
          encrypted_evidence_sha256=proof["native_result_ciphertext_sha256"])
    require(sha(retained["evidence.cms"]) == report["encrypted_evidence_sha256"],
            "Encrypted original witness differs")
    check(production, repository=REPO, source=proof["source"], run_id=proof["production_run"],
          run_attempt=1, head_sha=proof["production_head"],
          state_sha256=proof["production_state_sha256"])
    check(latest, repository=REPO, source=proof["source"], run_id=proof["native_run"],
          run_attempt=1, head_sha=proof["native_head"], state_sha256=proof["original_state_sha256"])
    prior, state = production["state"], latest["state"]
    require(sha(canonical(prior)) == production["state_sha256"]
            and sha(canonical(state)) == latest["state_sha256"],
            "The original acknowledged state bytes differ")
    check(state, pending=True, pending_id=proof["original_pending_id"], pending_kind="physical",
          completed_id=proof["original_completed_id"], blocked=False)
    check(prior, pending=False, completed_id=state["completed_id"], blocked=False)
    require(state["pending_id"] != state["completed_id"]
            and proof["reserved_start"] == max(state["starts"])
            and state["pending_started"] <= proof["reserved_start"] <= state["observed_at"],
            "Failed pre-opener reservation provenance differs")
    for key in ("legacy_owned", "legacy_history_unknown", "family", "schema"):
        require(state[key] == prior[key], "Original inherited provenance differs")
    for key in ("interval", "page_interval", "completed", "inventory_completed", "observed_at",
                "not_before", "rate_limit_strikes", "rate_limit_until"):
        require(state[key] >= prior[key], "A retained production constraint weakened")
    adjudicated = deepcopy(state)
    adjudicated.update(pending=False, pending_id=None, pending_kind=None)
    adjudicated["starts"] = sorted(set(state["starts"] + prior["starts"]))
    require(len(adjudicated["starts"]) == proof["preserved_start_union_count"]
            and sha(canonical(adjudicated)) == proof["adjudicated_state_sha256"],
            "The exact independently reviewed disposition cannot be derived")
    return state, adjudicated


def main():
    run, job, proof = verify_hosted()
    original, adjudicated = derive(proof, *read_evidence(proof))
    output = Path(os.environ["RUNNER_TEMP"]) / "grants-at-fh469-r2-adjudication"
    output.mkdir(mode=0o700)
    (output / "proof.json").write_bytes((QA / "proof.json").read_bytes())
    (output / "proof.cms").write_bytes((QA / "proof.cms").read_bytes())
    (output / "adjudication.json").write_bytes(canonical({
        "kind": "reviewed-unsent-reservation-adjudication",
        "run": run["id"], "job": job["id"], "head": run["head_sha"],
        "source": proof["source"], "network_publisher_opens": 0,
        "native_held_run": proof["native_run"], "native_held_job": proof["native_job"],
        "original_state_sha256": proof["original_state_sha256"],
        "state_sha256": proof["adjudicated_state_sha256"],
        "original_state": original, "adjudicated_state": adjudicated,
    }))


if __name__ == "__main__":
    main()
