"""Authorize a single independent, nonpublishing FRSE maintenance container."""
import hashlib
import io
import urllib.request
import zipfile
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "adapters/pl-frse"))
import adapter as a

REPOSITORY = "YouthOpps/data-pipeline"
PREFIX = ".github/workflows/frse-501-native2.yml"
branch = os.environ["GITHUB_REF_NAME"]
live = branch == "qa/501-native2-live"
assert branch in {"qa/501-native2-preflight", "qa/501-native2-live"}
assert os.environ["GITHUB_WORKFLOW_REF"] == f"{REPOSITORY}/{PREFIX}@refs/heads/{branch}"
assert os.environ["GITHUB_WORKFLOW_SHA"] == os.environ["GITHUB_SHA"]
assert not os.getenv("DATA_SOURCE_TOKEN")
assert os.environ["GITHUB_EVENT_NAME"] == "push"
owner = (45871463683, 1, a.SOURCE_ID)
owner_head = "d0c2731027b50c6e85928883133c38cfe8153053"
candidate = "e823781b233bdd061133b23630d124d4c24f8dc8"
for name in ("adapter.py", "test_adapter.py"):
    path = "adapters/pl-frse/" + name
    actual = (ROOT / path).read_bytes()
    assert actual == subprocess.check_output(["git", "show", f"{candidate}:{path}"])
    print(name, hashlib.sha256(actual).hexdigest())

run_id = int(os.environ["GITHUB_RUN_ID"])
attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
assert attempt == 1
with a.execution(), a.phase("preparation", 180):
    run = a.workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    assert run["path"] == PREFIX and run["head_branch"] == branch
    assert run["head_sha"] == os.environ["GITHUB_SHA"]
    assert run["repository"]["full_name"] == run["head_repository"]["full_name"] == REPOSITORY
    assert run["event"] == "push"
    jobs = a.workflow_api(f"/actions/runs/{run_id}/attempts/1/jobs")
    assert jobs["total_count"] == 1 and jobs["jobs"][0]["name"] == "verify"
    assert jobs["jobs"][0]["status"] == "in_progress"
    production = a.workflow_api("/actions/workflows/fetch-pl-frse.yml")
    assert production["id"] == a._LOCAL_WORKFLOW_ID
    if live:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        assert event["created"] is True and event["deleted"] is False
        assert event["before"] == "0"*40 and event["after"] == run["head_sha"]
        assert event["ref"] == "refs/heads/qa/501-native2-live"
        assert run["created_at"].startswith("2026-11-27T")
        prior = a.workflow_api("/actions/runs?branch=qa%2F501-native2-live"
                               "&event=push&per_page=100")
        assert prior["total_count"] == 1 and len(prior["workflow_runs"]) == 1
        assert prior["workflow_runs"][0]["id"] == run_id
        assert prior["workflow_runs"][0]["run_attempt"] == 1
        a.verify_local_workflow_pause(production)
    latest = a.workflow_api(f"/actions/workflows/{production['id']}/runs?per_page=1")
    assert latest["workflow_runs"][0]["id"] == owner[0]
    assert latest["workflow_runs"][0]["run_attempt"] == owner[1]
    assert latest["workflow_runs"][0]["status"] == "completed"
    metadata = a.workflow_api("/actions/artifacts/15144177944")
    assert metadata["expired"] is False
    assert metadata["workflow_run"]["id"] == owner[0]
    assert metadata["workflow_run"]["head_sha"] == owner_head
    digest = "sha256:5860e76953a5c2add277e6596cadb957ff2d6fcef5e18c918331aabf6ed5b229"
    assert metadata["digest"] == digest
    a._ATTEMPT_HEADS[owner[:2]] = owner_head
    state = a.production_maintenance_state(a.download_inert_artifact(owner, 15144177944, digest))
    research = a.workflow_api("/actions/artifacts/15183924820")
    source_run = a.workflow_api("/actions/runs/45966438770/attempts/1")
    assert source_run["conclusion"] == "failure" and source_run["head_branch"] == "qa/501-native-live"
    assert source_run["head_sha"] == "3eef7977b94fa7cbeddb143440651b2d5c3d8e84"
    assert research["workflow_run"]["head_sha"] == source_run["head_sha"]
    assert research["workflow_run"]["id"] == source_run["id"]
    assert research["name"] == "frse-501-maintenance-state-1" and research["expired"] is False
    payload = subprocess.check_output(["gh","api","--allow-escape-sequences",
        "repos/YouthOpps/data-pipeline/actions/artifacts/15183924820/zip"],
        env={**os.environ,"GH_TOKEN":os.environ["GITHUB_TOKEN"]})
    assert len(payload) == research["size_in_bytes"] == 10998
    assert hashlib.sha256(payload).hexdigest() == research["digest"][7:] == "3dcb3b768b4b59b28d7fd2a4a2c3ef6fe1aea08324491060d355e83daa28fa47"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {"terminal-state.json","receipts.json"}
        terminal = archive.read("terminal-state.json")
        proof = json.loads(archive.read("receipts.json"))
        assert proof["invalid_write_count"] == 0 and len(proof["requests"]) == 96
    assert hashlib.sha256(terminal).hexdigest() == "873d9884758abce1851753b21ab2b937fb0d3df1f4e7e9b0d26788a43c3ddc46"
    state = a.merge_budget(state, a.validate_budget(json.loads(terminal)))
    scoped = a.workflow_api("/actions/artifacts/15184413815")
    study = a.workflow_api("/actions/runs/45970910663/attempts/1")
    assert study["conclusion"] == "success" and study["head_branch"] == "qa/501-research-live"
    assert study["head_sha"] == "387ced39e1a1e313352ff1ea899b39477b35aae7"
    assert scoped["workflow_run"]["head_sha"] == study["head_sha"]
    assert scoped["workflow_run"]["id"] == study["id"]
    assert scoped["name"] == "frse-501-targeted-state-1" and scoped["expired"] is False
    payload = subprocess.check_output(["gh","api","--allow-escape-sequences",
        "repos/YouthOpps/data-pipeline/actions/artifacts/15184413815/zip"],
        env={**os.environ,"GH_TOKEN":os.environ["GITHUB_TOKEN"]})
    assert len(payload) == scoped["size_in_bytes"] == 3351
    assert hashlib.sha256(payload).hexdigest() == scoped["digest"][7:] == "82f9211988084c25ec8ccb980957c4cf7ca68ab42fd73ac0529b3200acd445e4"
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert set(archive.namelist()) == {"terminal-state.json", "facts.json", "receipts.json"}
        terminal = archive.read("terminal-state.json")
        proof = json.loads(archive.read("receipts.json"))
        assert proof["invalid_write_count"] == 0 and len(proof["requests"]) == 7
        assert all(r["status"] == 200 and r["fully_read"] and not any(r[k] for k in
                   ("read_failure","open_failure","close_failure")) for r in proof["requests"])
        assert hashlib.sha256(archive.read("facts.json")).hexdigest() == "2e976b4972ec19c90f676e23bcf2f812846bc5b37f7b67cc968b72d6b708f83d"
    assert hashlib.sha256(terminal).hexdigest() == "9929374843a57712e78adf38cbc2c836baa2662a3d19a4b30a96d0a629549637"
    state = a.merge_budget(state, a.validate_budget(json.loads(terminal)))
    assert not state["in_flight"] and not state["blocked"]
    write = Path(os.environ["RUNNER_TEMP"]) / "frse-seed"
    write.mkdir(mode=0o700)
    (write / "state.json").write_text(json.dumps(state))
    (write / "state.json").chmod(0o600)
    (write / "collection.lock").touch(mode=0o600)
print("Formal candidate and authentic complete predecessor verified.")
