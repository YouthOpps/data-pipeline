"""Isolated IBM #444 native observer admission. Never used in production."""

import hashlib
import json
import os
from pathlib import Path
import re


BRANCH = "issue/444-ibm-native-qa"
PATH = ".github/workflows/qa-ibm-444-native.yml"
REPOSITORY = "YouthOpps/data-pipeline"
PRODUCTION = "/actions/workflows/fetch-ibm.yml"


ISSUE = 444
ISSUE_ID = 6460740439
MAINTAINER = 12278069


def configure(adapter, owner_reads, scope):
    original_api = adapter.workflow_api
    original_binding = adapter.protocol_run_binding
    original_jobs = adapter.protocol_exact_jobs
    original_history = adapter.protocol_fresh_history
    predecessor = adapter._PRE_WIRE_API_PREDECESSOR

    def fail(message):
        raise adapter.AdapterError(message, "access")

    def maintainer(value):
        return (
            isinstance(value, dict)
            and type(value.get("id")) is int
            and value["id"] == MAINTAINER
            and value.get("type") == "User"
        )

    def identity():
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if (
            os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
            or os.environ.get("GITHUB_REF") != "refs/heads/" + BRANCH
            or os.environ.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/{PATH}@refs/heads/{BRANCH}"
            or os.environ.get("GITHUB_EVENT_NAME") != "push"
            or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
            or os.environ.get("GITHUB_JOB") != "collect"
            or os.environ.get("GITHUB_ACTOR_ID") != str(MAINTAINER)
            or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
            or not re.fullmatch(r"[1-9][0-9]*", run_id)
        ):
            fail("Unexpected isolated IBM QA execution identity")
        return int(run_id), 1

    current = identity()
    head = os.environ.get("GITHUB_SHA", "")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    if (
        not re.fullmatch(r"[0-9a-f]{40}", head)
        or event.get("created") is not True
        or event.get("deleted") is not False
        or event.get("forced") is not False
        or event.get("ref") != "refs/heads/" + BRANCH
        or event.get("before") != "0" * 40
        or event.get("after") != head
        or not maintainer(event.get("sender"))
        or scope.get("issue") != ISSUE
        or scope.get("maintainer") != MAINTAINER
        or scope.get("qa_branch") != BRANCH
        or scope.get("qa_workflow_path") != PATH
        or scope.get("predecessor") != list(predecessor)
        or scope.get("wire_failure") != list(adapter._PRE_WIRE_API_FAILURE)
        or scope.get("production_workflow_id") != adapter._LEGACY_PROTOCOL_PINS["workflow_id"]
    ):
        fail("Isolated IBM QA has no exact branch-creation scope")
    scope_digest = hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    current_route = f"/actions/runs/{current[0]}/attempts/1"
    issue = original_api(f"/issues/{ISSUE}")
    comments = original_api(f"/issues/{ISSUE}/comments?per_page=100")
    if (
        issue.get("number") != ISSUE
        or issue.get("id") != ISSUE_ID
        or issue.get("state") != "open"
        or issue.get("pull_request") is not None
        or not isinstance(comments, list)
        or len(comments) >= 100
    ):
        fail("Exact IBM QA tracker authority is unavailable")
    grant_lines = {
        f"Approved IBM #444 isolated native head {head}",
        f"IBM #444 isolated native scope {scope_digest}",
    }
    grants = [
        item for item in comments
        if maintainer(item.get("user"))
        and isinstance(item.get("body"), str)
        and grant_lines.issubset(item["body"].splitlines())
    ]
    observed_run = original_api(current_route)
    workflow_id = observed_run.get("workflow_id")
    if (
        len(grants) != 1
        or type(workflow_id) is not int
        or workflow_id <= 0
        or workflow_id == scope["production_workflow_id"]
    ):
        fail("Exact IBM QA maintainer grant or distinct workflow is unavailable")
    grant = grants[0]
    date = grant.get("created_at")
    run_created = observed_run.get("created_at", "")
    if (
        type(grant.get("id")) is not int
        or grant["id"] <= 0
        or not isinstance(date, str)
        or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", date)
        or not "2026-11-18T04:00:00Z" < date < run_created
        or grant.get("updated_at") != date
        or grant.get("issue_url") != f"https://api.github.com/repos/{REPOSITORY}/issues/{ISSUE}"
        or grant.get("html_url") != f"https://github.com/{REPOSITORY}/issues/{ISSUE}#issuecomment-{grant['id']}"
    ):
        fail("Exact unedited IBM QA maintainer grant identity differs")
    qa_route = "/actions/workflows/" + str(workflow_id)

    def api(path):
        response = original_api(path)
        if path == PRODUCTION and (
            response.get("state") != "disabled_manually"
            or response.get("id") != adapter._LEGACY_PROTOCOL_PINS["workflow_id"]
            or response.get("path") != adapter._LEGACY_PROTOCOL_PINS["workflow_path"]
        ):
            fail("Exact canonical IBM publication must remain paused")
        return response

    def run_binding(run, binding, expected_head=None):
        if tuple(binding) != current:
            return original_binding(run, binding, expected_head)
        if (
            type(run.get("id")) is not int
            or type(run.get("run_attempt")) is not int
            or (run["id"], run["run_attempt"]) != current
            or run.get("workflow_id") != workflow_id
            or run.get("path") != PATH
            or run.get("repository", {}).get("full_name") != REPOSITORY
            or run.get("head_repository", {}).get("full_name") != REPOSITORY
            or run.get("head_branch") != BRANCH
            or run.get("head_sha") != head
            or expected_head != head
            or run.get("event") != "push"
            or not maintainer(run.get("actor"))
            or not maintainer(run.get("triggering_actor"))
            or run.get("status") != "in_progress"
        ):
            fail("Unexpected GitHub-owned isolated IBM QA run")
        return run

    def exact_jobs(run, completed=True):
        if (run["id"], run["run_attempt"]) != current:
            return original_jobs(run, completed)
        if completed:
            fail("The current isolated IBM QA job must be in progress")
        inventory = api(current_route + "/jobs?per_page=100")
        jobs = inventory.get("jobs")
        if (
            type(inventory.get("total_count")) is not int
            or inventory["total_count"] != 1
            or not isinstance(jobs, list)
            or len(jobs) != 1
        ):
            fail("Unexpected isolated IBM QA job inventory")
        job = jobs[0]
        if (
            type(job.get("id")) is not int
            or job["id"] <= 0
            or job.get("run_id") != current[0]
            or job.get("run_attempt") != 1
            or job.get("head_sha") != head
            or job.get("head_branch") != BRANCH
            or job.get("name") != "collect"
            or job.get("status") != "in_progress"
        ):
            fail("Unexpected GitHub-owned isolated IBM QA job")
        return jobs, None

    def qa_history():
        definition = api(qa_route)
        if (
            definition.get("id") != workflow_id
            or definition.get("path") != PATH
            or definition.get("state") != "active"
        ):
            fail("Unexpected isolated IBM QA workflow")
        inventory = api(qa_route + "/runs?per_page=100&page=1")
        rows = inventory.get("workflow_runs")
        if (
            not isinstance(rows, list)
            or type(inventory.get("total_count")) is not int
            or inventory["total_count"] != len(rows)
            or len(rows) != 1
        ):
            fail("Unexpected mixed or incomplete isolated IBM QA history")
        run = run_binding(rows[0], current, head)
        jobs, _ = exact_jobs(run, completed=False)
        return (
            definition["id"], definition["path"], definition["state"],
            run["id"], run["run_attempt"], run["head_sha"], run["head_branch"],
            run["status"], jobs[0]["id"], jobs[0]["status"],
        )

    def fresh_history():
        api(PRODUCTION)
        qa_before = qa_history()
        latest, artifact = original_history()
        if adapter.protocol_context_key(latest, artifact) != predecessor:
            fail("Exact reviewed canonical predecessor is absent")
        expected_bound = adapter.protocol_timestamp(adapter._PRE_WIRE_API_FAILURE[4])
        if adapter._NO_PUBLISHER_BOUND != expected_bound:
            fail("Exact reviewed pre-wire continuity bound is absent")
        if qa_history() != qa_before:
            fail("Isolated IBM QA ownership changed during coordination")
        api(PRODUCTION)
        owner_reads.append({
            "approval": grant["html_url"],
            "scope_sha256": scope_digest,
            "canonical_pause": "disabled_manually",
            "canonical_predecessor": list(predecessor),
            "pre_wire_completion_bound": expected_bound,
            "qa_workflow_id": workflow_id,
            "qa_run": current[0],
            "qa_attempt": current[1],
            "qa_head": head,
            "qa_branch": BRANCH,
        })
        return latest, artifact

    run_binding(observed_run, current, head)
    api(PRODUCTION)
    qa_history()
    adapter.current_run_identity = identity
    adapter.workflow_api = api
    adapter.protocol_run_binding = run_binding
    adapter.protocol_exact_jobs = exact_jobs
    adapter.protocol_fresh_history = fresh_history
