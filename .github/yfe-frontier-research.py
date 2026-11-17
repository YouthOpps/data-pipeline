"""Observe two already permitted Youth for Europe requests on one hosted attempt."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.parse

APPROVAL = 'https://github.com/YouthOpps/data-pipeline/issues/365#issuecomment-6963099314'
REPO = "YouthOpps/data-pipeline"
BRANCH = "research/yfe-365-current-frontier"
WORKFLOW = ".github/workflows/yfe-frontier-research.yml"
OBSERVER = ".github/yfe-frontier-research.py"
CERT = ".github/yfe-review.pem"
SOURCE = "adapters/youth-for-europe/adapter.py"
PRODUCER_PATH = ".github/workflows/fetch-youth-for-europe.yml"
PRODUCER = 384792184
PRODUCER_RUN = 44107433490
PRODUCER_HEAD = "31da8cb5ca9664dfae01f8ef7d4b64c79829fdc9"
PRODUCER_JOB = 134123503311
PRODUCER_ARCHIVE = 14337489541
PRODUCER_ARCHIVE_HASH = "sha256:c4a397cb8ced3ea97bce0e3eb730c3b0c629945e0fd4e3cb7b321f834ddfd574"
PRODUCER_HISTORY_HASH = "2b4ffc655ce930c215fdcde764098dbe2c4871cef392a5223352b97ad268fa44"
SOURCE_SHA = "f32780b7963dda9485f7dd16b7b81a5062e6520da84011d08ffb1030251a0113"
CERT_SHA = "c0cf6563f92ff2e3e4a05e9ef6a38de1f88201a39f7fce1d178b0f2cf6d30905"
TARGETS = (
    "https://youthforeurope.eu/robots.txt",
    "https://youthforeurope.eu/explore/?type=general&sort=latest&pg=1",
)
ROOT = Path(__file__).resolve().parents[1]


def need(check, message):
    if not check:
        raise RuntimeError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def immutable(adapter, head, path):
    item = adapter.workflow_api(f"/contents/{path}?ref={head}")
    need(item.get("path") == path and item.get("type") == "file"
         and item.get("encoding") == "base64", "Invalid immutable input")
    return base64.b64decode("".join(item["content"].splitlines()), validate=True)


def exact_qa(adapter):
    need(type(APPROVAL) is str and re.fullmatch(
        r"https://github\.com/YouthOpps/data-pipeline/issues/365#issuecomment-[1-9][0-9]*",
        APPROVAL), "Exact operator grant required")
    env = os.environ
    need(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") == REPO
         and env.get("GITHUB_REF") == "refs/heads/" + BRANCH
         and env.get("GITHUB_WORKFLOW_REF") == f"{REPO}/{WORKFLOW}@refs/heads/{BRANCH}"
         and env.get("GITHUB_EVENT_NAME") == "push" and env.get("GITHUB_RUN_ATTEMPT") == "1"
         and "DATA_SOURCE_TOKEN" not in env, "Unexpected hosted diagnostic scope")
    event = json.loads(Path(env["GITHUB_EVENT_PATH"]).read_bytes())
    head, run_id = env["GITHUB_SHA"], int(env["GITHUB_RUN_ID"])
    need(event.get("created") is True and event.get("before") == "0" * 40
         and event.get("after") == head and event.get("ref") == "refs/heads/" + BRANCH,
         "Only exact initial branch creation is admitted")
    run = adapter.workflow_api(f"/actions/runs/{run_id}/attempts/1")
    workflow_id = run["workflow_id"]
    identity = {"id": run_id, "run_attempt": 1, "head_sha": head, "head_branch": BRANCH,
                "path": WORKFLOW, "workflow_id": workflow_id, "event": "push",
                "status": "in_progress"}
    need(all(run.get(key) == value for key, value in identity.items())
         and all(run.get(key, {}).get("full_name") == REPO
                 for key in ("repository", "head_repository")), "Exact diagnostic run differs")
    workflow = adapter.workflow_api(f"/actions/workflows/{workflow_id}")
    need(workflow["path"] == WORKFLOW and workflow["state"] == "active",
         "Exact diagnostic workflow differs")
    listing = adapter.workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100")
    need(listing["total_count"] == len(listing["workflow_runs"]) == 1
         and all(listing["workflow_runs"][0].get(key) == value
                 for key, value in identity.items()), "Diagnostic one-use history differs")
    jobs = adapter.workflow_api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100")
    need(jobs["total_count"] == len(jobs["jobs"]) == 1, "Diagnostic job inventory differs")
    job = jobs["jobs"][0]
    need(job["run_id"] == run_id and job["run_attempt"] == 1
         and job["head_sha"] == head and job["name"] == "observe"
         and job["status"] == "in_progress", "Exact diagnostic job differs")
    branch = adapter.workflow_api("/git/ref/heads/" + BRANCH)
    need(branch["ref"] == "refs/heads/" + BRANCH and branch["object"]["sha"] == head
         and branch["object"]["type"] == "commit", "Diagnostic branch owner changed")
    for path in (SOURCE, WORKFLOW, OBSERVER, CERT):
        need(immutable(adapter, head, path) == (ROOT / path).read_bytes(),
             "Executable diagnostic inputs differ from immutable bytes")
    need(sha((ROOT / SOURCE).read_bytes()) == SOURCE_SHA
         and sha((ROOT / CERT).read_bytes()) == CERT_SHA, "Pinned source or certificate differs")
    return run_id, 1, head, workflow_id, job["id"]


def latest_producer(adapter):
    workflow = adapter.workflow_api(f"/actions/workflows/{PRODUCER}")
    need(workflow.get("id") == PRODUCER and workflow.get("path") == PRODUCER_PATH
         and workflow.get("state") == "disabled_manually", "Production workflow must be paused")
    history = adapter.workflow_api(f"/actions/workflows/{PRODUCER}/runs?per_page=100")
    rows = history["workflow_runs"]
    projected = [[run[key] for key in
                  ("id", "run_attempt", "head_sha", "status", "conclusion", "run_started_at")]
                 for run in rows]
    need(history["total_count"] == len(rows) == 39
         and sha(canonical(projected)) == PRODUCER_HISTORY_HASH
         and all(row["status"] == "completed" and row["run_attempt"] == 1
                 and row["path"] == PRODUCER_PATH and row["workflow_id"] == PRODUCER
                 and row["head_branch"] == "main"
                 and all(row[key]["full_name"] == REPO
                         for key in ("repository", "head_repository")) for row in rows),
         "Reviewed exclusive production chronology changed")
    run = adapter.workflow_api(f"/actions/runs/{PRODUCER_RUN}/attempts/1")
    need(run["id"] == PRODUCER_RUN and run["head_sha"] == PRODUCER_HEAD and
         run["run_attempt"] == 1 and run["workflow_id"] == PRODUCER and
         run["path"] == PRODUCER_PATH and run["status"] == "completed" and
         run["conclusion"] == "failure" and run["head_branch"] == "main",
         "Exact latest production attempt differs")
    jobs = adapter.workflow_api(f"/actions/runs/{PRODUCER_RUN}/attempts/1/jobs?per_page=100")
    need(jobs["total_count"] == len(jobs["jobs"]) == 1, "Producer job inventory differs")
    job = jobs["jobs"][0]
    need(job["id"] == PRODUCER_JOB and job["name"] == "collect" and
         job["run_id"] == PRODUCER_RUN and job["run_attempt"] == 1 and
         job["head_sha"] == PRODUCER_HEAD and job["status"] == "completed"
         and job["conclusion"] == "failure" and job["completed_at"] == "2026-11-17T14:51:50Z",
         "Exact latest production job differs")
    artifacts = adapter.workflow_api(f"/actions/runs/{PRODUCER_RUN}/artifacts?per_page=100")
    found = [item for item in artifacts["artifacts"] if item["name"] == "yfe-pacing-state-1"]
    need(artifacts["total_count"] == len(artifacts["artifacts"]) and len(found) == 1,
         "Exact newest production archive missing")
    artifact = found[0]
    need(artifact["id"] == PRODUCER_ARCHIVE and artifact["digest"] == PRODUCER_ARCHIVE_HASH
         and artifact["expired"] is False and artifact["size_in_bytes"] == 439
         and artifact["workflow_run"]["id"] == PRODUCER_RUN
         and artifact["workflow_run"]["head_sha"] == PRODUCER_HEAD,
         "Exact newest production archive differs")
    return PRODUCER_RUN, 1, adapter.SOURCE_ID


def protect(raw):
    return subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
         str(ROOT / CERT)], input=raw, capture_output=True, timeout=25, check=True,
    ).stdout


class CaptureResponse:
    def __init__(self, response, audit, clock):
        self.response, self.audit, self.clock = response, audit, clock
        self.status, self.headers = response.status, response.headers
        audit.update(status=self.status, headers=list(self.headers.items()), segments=[])

    def read(self, *args):
        try:
            raw = self.response.read(*args)
        except BaseException as error:
            partial = getattr(error, "partial", b"")
            if isinstance(partial, bytes) and partial:
                self.audit["segments"].append(base64.b64encode(partial).decode())
            self.audit["read_error"] = type(error).__name__
            raise
        self.audit["segments"].append(base64.b64encode(raw).decode())
        limit = args[0] if args else -1
        declared = self.headers.get_all("Content-Length", [])
        framed = not declared or (len(declared) == 1 and declared[0].isdigit()
                                  and int(declared[0]) == len(raw))
        self.audit["body_complete"] = framed and (limit < 0 or len(raw) < limit)
        return raw

    def __enter__(self):
        self.response.__enter__()
        return self

    def __exit__(self, *args):
        try:
            result = self.response.__exit__(*args)
        except BaseException as error:
            self.audit["close_error"] = type(error).__name__
            raise
        self.audit["completion"] = self.clock.monotonic()
        self.audit["wall_completion"] = self.clock.time()
        return result


def instrumentation(adapter, rows, writes):
    original_open = adapter._OPENER.open
    original_request = adapter.request_bytes
    original_write = adapter.write_budget

    def bounded_request(url, **kwargs):
        if urllib.parse.urlsplit(url).hostname in {"youthforeurope.eu", "www.youthforeurope.eu"}:
            need(len(rows) < len(TARGETS) and url == TARGETS[len(rows)]
                 and kwargs.get("method", "GET") == "GET" and not kwargs.get("headers")
                 and kwargs.get("payload") is None, "Unreviewed publisher request or retry")
        return original_request(url, **kwargs)

    def audited_write(state):
        row = {"intended": json.loads(canonical(state)), "acknowledged": False}
        writes.append(row)
        original_write(state)
        row["observed"] = adapter.load_budget()
        row["acknowledged"] = row["observed"] == row["intended"]
        need(row["acknowledged"], "Original journal write readback differs")

    def audited_open(request, *args, **kwargs):
        url = request.full_url
        parsed = urllib.parse.urlsplit(url)
        if url in TARGETS:
            need(len(rows) < len(TARGETS) and url == TARGETS[len(rows)]
                 and request.get_method() == "GET"
                 and request.get_header("User-agent") == adapter._USER_AGENT
                 and request.get_header("Authorization") is None,
                 "Unreviewed actual publisher opener")
            row = {"url": url, "actual_start": adapter.time.monotonic(),
                   "actual_wall_start": adapter.time.time(), "user_agent": adapter._USER_AGENT}
            rows.append(row)
            try:
                response = original_open(request, *args, **kwargs)
            except urllib.error.HTTPError as error:
                response = error
            except BaseException as error:
                row["opener_error"] = type(error).__name__
                raise
            return CaptureResponse(response, row, adapter.time)
        allowed_api = url.startswith("https://api.github.com/repos/YouthOpps/data-pipeline/")
        allowed_storage = parsed.hostname and (
            parsed.hostname.endswith(".blob.core.windows.net") or
            parsed.hostname.endswith(".actions.githubusercontent.com"))
        need(parsed.scheme == "https" and (allowed_api or
             allowed_storage and request.get_header("Authorization") is None),
             "Unreviewed diagnostic infrastructure opener")
        return original_open(request, *args, **kwargs)

    adapter._OPENER.open = audited_open
    adapter.request_bytes = bounded_request
    adapter.write_budget = audited_write


def load_adapter():
    spec = importlib.util.spec_from_file_location("yfe_research_source", ROOT / SOURCE)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def run():
    adapter = load_adapter()
    rows, writes = [], []
    instrumentation(adapter, rows, writes)
    scope, error, frontier = None, None, None
    log = io.StringIO()
    with adapter.execution(), contextlib.redirect_stderr(log):
        try:
            with adapter.phase("bounded research", 420):
                scope = exact_qa(adapter)
                predecessor = latest_producer(adapter)
                need(protect(b"Youth for Europe evidence protection preflight"),
                     "Research evidence sealing unavailable")
                adapter.current_run_identity = lambda: scope[:2]

                def restore_owner():
                    adapter._ATTEMPT_HEADS[(PRODUCER_RUN, 1)] = PRODUCER_HEAD
                    return predecessor

                adapter.latest_family_run = restore_owner
                adapter.prepare_collection()
                with adapter.locked_budget():
                    starting = adapter.load_budget()
                need(not starting["blocked"] and starting["rate_limit_until"] <= adapter.time.time(),
                     "Existing pacing or access hold prevents diagnostics")
                wait = starting["until"] - adapter.time.time()
                if wait > 0:
                    adapter.time.sleep(wait)
                need(exact_qa(adapter) == scope and latest_producer(adapter) == predecessor,
                     "Exclusive hosted owner changed during startup quiet")
                adapter.journal_preopener()
                _, first, cards = adapter.explore_page()
                total, pages = first["found_posts"], first["max_num_pages"]
                page_size = first["posts_per_page"]
                need(total > 0 and pages == (total + page_size - 1) // page_size,
                     "Current source frontier is inconsistent")
                need(len(cards) == min(total, page_size), "Current page-1 cardinality differs")
                need(len(rows) == 2 and all(row.get("body_complete") and row.get("completion")
                     for row in rows) and rows[1]["status"] == 200 and (
                         rows[0]["status"] == 200 or 400 <= rows[0]["status"] < 500
                         and rows[0]["status"] != 429),
                     "Native research did not capture two complete responses")
                floor = rows[1]["actual_start"] - rows[0]["completion"]
                need(floor >= 12.2, "Actual closed-to-start gap is below source floor")
                frontier = {"details": total, "pages_per_pass": pages,
                            "page_1_cards": len(cards), "page_size": page_size,
                            "complete_base_requests": 1 + 2 * pages + total,
                            "remaining_after_page_1": 2 * pages - 1 + total,
                            "actual_closed_to_start": floor}
        except Exception as failure:
            error = type(failure).__name__
        finally:
            if scope is not None:
                try:
                    with adapter.phase("export", adapter.EXPORT_TIMEOUT):
                        adapter.export_family_artifact()
                except Exception as failure:
                    error = error or type(failure).__name__
    report = {"owner": scope, "frontier": frontier, "error": error,
              "outcome": 0 if frontier and not error else 1,
              "requests": [{key: value for key, value in row.items()
                            if key not in {"headers", "segments"}} |
                           {"body_sha256": sha(b"".join(
                            base64.b64decode(part) for part in row.get("segments", [])))}
                           for row in rows],
              "persistence": len(writes), "source_sha256": SOURCE_SHA}
    temp = Path(os.environ["RUNNER_TEMP"])
    content = canonical({"report": report, "requests": rows, "persistence": writes,
                         "diagnostics": log.getvalue()})
    encrypted = protect(content)
    (temp / "yfe-frontier.cms").write_bytes(encrypted)
    report.update(evidence_sha256=sha(encrypted), plaintext_sha256=sha(content))
    (temp / "yfe-frontier.json").write_bytes(canonical(report))
    print(json.dumps({"outcome": report["outcome"], "frontier": frontier, "error": error}))
    return report["outcome"]


if __name__ == "__main__":
    sys.exit(run())
