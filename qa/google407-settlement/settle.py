"""Zero-publisher Google407 logical settlement from an exact hosted owner archive."""

import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile


HERE = Path(__file__).resolve().parent
REPO = "YouthOpps/data-pipeline"
BRANCH = "qa/google407-terms-settlement"
REF = "refs/heads/" + BRANCH
WORKFLOW = ".github/workflows/qa-google407-terms-settlement.yml"
ROOT = HERE.parents[1]
API = "https://api.github.com/repos/" + REPO
ACTOR = 12278069
APPROVAL = {'scope_sha256': '1546ff33df8aed2b3d20bd7d43231862b3036c3e6d3d6a86c849935125d756fb'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, message, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def require(valid, message):
    if not valid:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def request(url, size, *, api=False):
    """Only the fixed GitHub repository and authenticated signed archive storage."""
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.port is None and not parsed.fragment, "GitHub transfer boundary differs")
    if api:
        require(url.startswith(API + "/") and parsed.hostname == "api.github.com", "Unexpected GitHub API route")
    else:
        require(parsed.hostname and parsed.hostname.endswith((".blob.core.windows.net", ".actions.githubusercontent.com")),
                "Unexpected signed original archive host")
    headers = {"Accept": "application/vnd.github+json", "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"]} if api else {}
    try:
        response = OPENER.open(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(size + 1)
        require(len(raw) <= size, "GitHub transfer exceeds the reviewed bound")
        return response.status, response.headers, raw


def api(path):
    require(path.startswith("/"), "Unexpected GitHub path")
    code, _, raw = request(API + path, 2_000_000, api=True)
    require(code == 200, "GitHub owner metadata unavailable")
    return json.loads(raw)


def exact_inputs(head):
    def committed(path):
        return subprocess.check_output(["git", "-C", str(ROOT), "show", f"{head}:{path}"], timeout=25)

    pin = {}
    for path in (Path(__file__).resolve(), HERE / "scope.json", HERE / "retained-effects.json", ROOT / WORKFLOW):
        relative = path.relative_to(ROOT).as_posix()
        raw = path.read_bytes()
        require(raw == committed(relative), "Exact zero-wire GitHub checkout differs")
        pin[relative] = digest(raw)
    return pin


def active_owner(scope, pin):
    head, run = os.environ["GITHUB_SHA"], int(os.environ["GITHUB_RUN_ID"])
    require(type(APPROVAL) is dict and APPROVAL == {"scope_sha256": pin["qa/google407-settlement/scope.json"]},
            "Exact logical settlement authority remains OFF")
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPO, "GITHUB_REF": REF,
           "GITHUB_EVENT_NAME": "push", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_JOB": "settlement",
           "GITHUB_ACTOR_ID": str(ACTOR), "GITHUB_WORKFLOW_SHA": head,
           "GITHUB_WORKFLOW_REF": f"{REPO}/{WORKFLOW}@{REF}"}
    require(re.fullmatch(r"[a-f0-9]{40}", head) and run > 0 and "DATA_SOURCE_TOKEN" not in os.environ
            and all(os.environ.get(k) == v for k, v in env.items()), "Unexpected zero-wire GitHub runner")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    require(event.get("created") is True and event.get("deleted") is False and event.get("forced") is False
            and event.get("ref") == REF and event.get("before") == "0" * 40 and event.get("after") == head
            and event.get("sender", {}).get("id") == ACTOR and event.get("repository", {}).get("full_name") == REPO,
            "Only the original zero-wire branch creation is authorized")
    require(pin["qa/google407-settlement/retained-effects.json"] == scope["effects_sha256"], "Retained effect ledger changed")
    lines = {f"Approved Google407 logical settlement head {head}",
             f"Google407 logical settlement scope {APPROVAL['scope_sha256']}"}
    comments = api("/issues/407/comments?per_page=100")
    require(type(comments) is list and len(comments) < 100, "Complete issue decision inventory unavailable")
    found = [row for row in comments if lines.issubset((row.get("body") or "").splitlines())]
    require(len(found) == 1, "Unique exact zero-wire decision is unavailable")
    comment = found[0]
    ident, born = comment.get("id"), comment.get("created_at")
    require(type(ident) is int and ident > 6962558398 and type(born) is str and born > scope["terminal_job"]
            and comment.get("updated_at") == born and comment.get("user", {}).get("id") == ACTOR
            and comment.get("user", {}).get("type") == "User" and comment.get("issue_url") == API + "/issues/407"
            and comment.get("html_url") == f"https://github.com/{REPO}/issues/407#issuecomment-{ident}",
            "Invalid exact decision author, immutable issue or timestamp")
    route = f"/actions/runs/{run}/attempts/1"
    expected = {"id": run, "head_sha": head, "head_branch": BRANCH, "path": WORKFLOW,
                "event": "push", "run_attempt": 1}
    for poll in range(5):
        row = api(route)
        require(all(row.get(k) == v for k, v in expected.items()) and row.get("created_at", "") >= born
                and row.get("repository", {}).get("full_name") == REPO and row.get("head_repository", {}).get("full_name") == REPO
                and row.get("actor", {}).get("id") == ACTOR and row.get("triggering_actor", {}).get("id") == ACTOR,
                "Zero-wire run identity changed")
        wid = row.get("workflow_id")
        require(type(wid) is int and wid not in (380770427, 421935029) and wid > 0, "Unexpected zero-wire workflow owner")
        workflow = api(f"/actions/workflows/{wid}")
        require((workflow.get("id"), workflow.get("path"), workflow.get("state")) == (wid, WORKFLOW, "active"),
                "Unexpected active zero-wire workflow")
        inventory = api(f"/actions/workflows/{wid}/runs?per_page=100")
        jobs = api(route + "/jobs?per_page=100")
        prior, values = inventory["workflow_runs"], jobs["jobs"]
        require(len(prior) == inventory["total_count"] <= 1 and len(values) == jobs["total_count"] <= 1,
                "Ambiguous zero-wire workflow execution")
        require(not prior or all(prior[0].get(k) == v for k, v in expected.items()), "Unexpected zero-wire history")
        require(not values or (values[0].get("name"), values[0].get("run_id"), values[0].get("run_attempt"),
                               values[0].get("head_sha"), values[0].get("head_branch")) == ("settlement", run, 1, head, BRANCH),
                "Unexpected zero-wire job")
        states = [item.get("status") for item in (row, *prior, *values)]
        if states == ["in_progress"] * 3:
            break
        require(poll < 4 and all(s in {"queued", "pending", "requested", "waiting", "in_progress"} for s in states),
                "Zero-wire current job is unavailable")
        time.sleep(poll + 1)
    branch = api("/git/ref/heads/" + BRANCH)
    require(branch["ref"] == REF and branch["object"]["sha"] == head, "Zero-wire branch head moved")
    return {"workflow": wid, "run_id": run, "attempt": 1, "job": values[0]["id"], "head": head, "grant": ident}


def original_owner(scope):
    workflow = api("/actions/workflows/421935029")
    require(workflow["state"] == "disabled_manually" and workflow["path"] == ".github/workflows/qa-google407-native.yml",
            "Earlier publisher test workflow is not retired")
    require(api("/actions/workflows/380770427")["state"] == "disabled_manually", "Original production is no longer paused")
    production = api("/actions/workflows/380770427/runs?per_page=100")
    observed = [[item[k] for k in ("id", "run_attempt", "head_sha", "status", "conclusion")] for item in production["workflow_runs"]]
    require(production["total_count"] == len(observed) == 42 and digest(canonical(sorted(observed))) == scope["production_history_sha256"],
            "Original mature production chronology changed")
    inventory = api("/actions/workflows/421935029/runs?per_page=100")
    require(inventory["total_count"] == len(inventory["workflow_runs"]) == 1, "Earlier publisher test workflow history changed")
    run_id = scope["predecessor"]["run_id"]
    head = scope["predecessor"]["head"]
    path = f"/actions/runs/{run_id}/attempts/1"
    for row in (api(path), inventory["workflow_runs"][0]):
        require((row["id"], row["workflow_id"], row["run_attempt"], row["head_sha"], row["head_branch"],
                 row["event"], row["status"], row["conclusion"]) ==
                (run_id, 421935029, 1, head, "qa/google407-native", "push", "completed", "failure"),
                "Earlier exact failed publisher run changed")
    jobs = api(path + "/jobs?per_page=100")
    require(jobs["total_count"] == len(jobs["jobs"]) == 1, "Earlier publisher job inventory changed")
    job = jobs["jobs"][0]
    require((job["id"], job["completed_at"], job["conclusion"], job["name"], job["run_id"], job["run_attempt"], job["head_sha"]) ==
            (scope["predecessor"]["job"], scope["terminal_job"], "failure", "native", run_id, 1, head),
            "Earlier exact publisher job differs")
    artifacts = api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    require(artifacts["total_count"] == len(artifacts["artifacts"]) == 1, "Earlier ZIP inventory differs")
    row = artifacts["artifacts"][0]
    expected = scope["archive"]
    require((row["id"], row["digest"], row["size_in_bytes"], row["expired"], row["name"],
             row["workflow_run"]["id"], row["workflow_run"]["head_sha"]) ==
            (expected["id"], "sha256:" + expected["sha256"], expected["size"], False,
             f"google407-native-qa-{run_id}-1", run_id, head), "Earlier exact owned ZIP metadata differs")


def download(scope):
    expected = scope["archive"]
    status, headers, _ = request(API + f"/actions/artifacts/{expected['id']}/zip", 1024, api=True)
    redirects = headers.get_all("Location", [])
    require(status == 302 and len(redirects) == 1, "Exact owned ZIP redirect unavailable")
    status, _, original = request(redirects[0], expected["size"])
    require(status == 200, "Original owned ZIP download failed")
    return original


def settle(original, effects, scope):
    archive = scope["archive"]
    require(len(original) == archive["size"] and digest(original) == archive["sha256"], "Earlier ZIP digest or length differs")
    with zipfile.ZipFile(io.BytesIO(original)) as zipped:
        members = zipped.infolist()
        bounds = {"manifest.json": 8192, "owned-state.json": 16384, "evidence.cms": archive["size"]}
        require(len(members) == 3 and set(zipped.namelist()) == set(bounds)
                and all(0 < row.file_size <= bounds[row.filename] and not row.flag_bits & 1
                        and not stat.S_ISLNK(row.external_attr >> 16) for row in members), "Earlier owned ZIP membership differs")
        prior = json.loads(zipped.read("manifest.json"))
        raw_state = zipped.read("owned-state.json")
        ciphertext_sha = digest(zipped.read("evidence.cms"))
    require(prior["identity"] == scope["predecessor"] == effects["owner"] and prior["original_outcome"] == 1
            and prior["observer_outcome"] == 1 and prior["state_artifact_present"] is True
            and prior["encrypted_sha256"] == ciphertext_sha == effects["encrypted_sha256"], "Earlier encrypted source outcome differs")
    require(prior["last_original_ack"] == {"blocked": False, "current_custody": True, "pending": True,
            "sha256": scope["original_state_sha256"]} and digest(raw_state) == scope["original_state_sha256"],
            "Earlier original acknowledged pending bytes differ")
    state = json.loads(raw_state)
    ledger = effects["physical_ledger"]
    last = ledger[-1]
    require(state["in_flight"] and not state["blocked"] and state["flight_kind"] == "policy"
            and state["request_id"] == state["completed_request_id"] == last["id"]
            and state["request_outcome"] == "response_assessed" and state["flight_id"] != state["completed_flight_id"]
            and last["url"] == "https://policies.google.com/terms" and last["status"] == 200
            and last["body_sha256"] == scope["terms_body_sha256"] and len(ledger) == prior["publisher_opener_entries"] == 25
            and prior["journal_acks"] == 102 and len(state["starts"]) == 151
            and all(row["reservation"] in state["starts"] and row["reservation"] <= row["actual_start"] <= row["actual_close"]
                    for row in ledger) and last["actual_close"] <= state["completed"], "Earlier closed physical receipt or historical timing differs")
    starts = sorted(set(state["starts"] + [row["actual_start"] for row in ledger]))
    require(len(starts) == 176, "Original complete timing floor differs")
    settled = dict(state, starts=starts, in_flight=False, completed_flight_id=state["flight_id"])
    require(digest(canonical(settled)) == scope["proposed_settled_sha256"], "Exact logical-only settlement differs")
    return settled


def main():
    require(APPROVAL is not None and os.environ.get("GITHUB_ACTIONS") == "true", "Logical settlement is OFF")
    head = os.environ["GITHUB_SHA"]
    pins = exact_inputs(head)
    scope = json.loads((HERE / "scope.json").read_bytes())
    owner = active_owner(scope, pins)
    original_owner(scope)
    effects = json.loads((HERE / "retained-effects.json").read_bytes())
    settled = settle(download(scope), effects, scope)
    original_owner(scope)
    require(active_owner(scope, exact_inputs(head)) == owner, "Original zero-wire owner changed after artifact transfer")
    temp = Path(os.environ["RUNNER_TEMP"])
    require(temp == temp.resolve() and temp.stat().st_uid == os.getuid(), "Unexpected original runner output root")
    output = temp / "google407-logical-only-settlement"
    output.mkdir(mode=0o700)
    for name, value in {
        "settled-state.json": settled,
        "manifest.json": {"new_owner": owner, "original_owner": scope["predecessor"], "original_archive": scope["archive"],
                          "prior_state_sha256": scope["original_state_sha256"], "settled_sha256": scope["proposed_settled_sha256"],
                          "publisher_requests": 0, "authorized_publication": False, "proposed_terms_candidate_sha256": scope["terms_candidate_sha256"],
                          "adopts_exact_original_closed_physical_boundary": True},
    }.items():
        raw = canonical(value)
        with (output / name).open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        require((output / name).read_bytes() == raw, "Exact settlement artifact write differs")


if __name__ == "__main__":
    main()
