"""Authorize a single independent, nonpublishing FRSE maintenance container."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT / "adapters/pl-frse"))
import adapter as a

REPOSITORY = "YouthOpps/data-pipeline"
PREFIX = ".github/workflows/frse-501-native.yml"
branch = os.environ["GITHUB_REF_NAME"]
live = branch == "qa/501-native-live"
assert branch in {"qa/501-native-preflight", "qa/501-native-live"}
assert os.environ["GITHUB_WORKFLOW_REF"] == f"{REPOSITORY}/{PREFIX}@refs/heads/{branch}"
assert os.environ["GITHUB_WORKFLOW_SHA"] == os.environ["GITHUB_SHA"]
assert not os.getenv("DATA_SOURCE_TOKEN")
assert os.environ["GITHUB_EVENT_NAME"] == "push"
owner = (45871463683, 1, a.SOURCE_ID)
owner_head = "d0c2731027b50c6e85928883133c38cfe8153053"
candidate = "66c925e0f4915fafa56cc46c65cd51ab9a8e8659"
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
    assert not state["in_flight"] and not state["blocked"]
    write = Path(os.environ["RUNNER_TEMP"]) / "frse-seed"
    write.mkdir(mode=0o700)
    (write / "state.json").write_text(json.dumps(state))
    (write / "state.json").chmod(0o600)
    (write / "collection.lock").touch(mode=0o600)
print("Formal candidate and authentic complete predecessor verified.")
