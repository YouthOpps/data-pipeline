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
import runpy
from email.message import Message
import subprocess
import sys
import urllib.error
import urllib.parse

APPROVAL = 'https://github.com/YouthOpps/data-pipeline/issues/365#issuecomment-6963852898'
REPO = "YouthOpps/data-pipeline"
BRANCH = "qa/yfe-365-exact-evidence"
WORKFLOW = ".github/workflows/yfe-exact-evidence.yml"
OBSERVER = ".github/yfe-exact-evidence.py"
CERT = ".github/yfe-review.pem"
SOURCE = "adapters/youth-for-europe/adapter.py"
TEST = "adapters/youth-for-europe/test_adapter.py"
PRODUCER_PATH = ".github/workflows/fetch-youth-for-europe.yml"
PRODUCER = 384792184
PRODUCER_RUN = 44107433490
PRODUCER_HEAD = "31da8cb5ca9664dfae01f8ef7d4b64c79829fdc9"
PRODUCER_JOB = 134123503311
PRODUCER_ARCHIVE = 14337489541
PRODUCER_ARCHIVE_HASH = "sha256:c4a397cb8ced3ea97bce0e3eb730c3b0c629945e0fd4e3cb7b321f834ddfd574"
PRODUCER_HISTORY_HASH = "2b4ffc655ce930c215fdcde764098dbe2c4871cef392a5223352b97ad268fa44"
SOURCE_SHA = "62e85331ced56982f101f57bca20d0dd776f4b6990eec2a6f0e9eaafd526a870"
CERT_SHA = "c0cf6563f92ff2e3e4a05e9ef6a38de1f88201a39f7fce1d178b0f2cf6d30905"
ENTRY_TARGETS = (
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
    for path in (SOURCE, TEST, WORKFLOW, OBSERVER, CERT):
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


def instrumentation(adapter, rows, writes, targets):
    original_open = adapter._OPENER.open
    original_request = adapter.request_bytes
    original_write = adapter.write_budget

    def bounded_request(url, **kwargs):
        if urllib.parse.urlsplit(url).hostname in {"youthforeurope.eu", "www.youthforeurope.eu"}:
            need(len(rows) < len(targets) and url == targets[len(rows)]
                 and kwargs.get("method", "GET") == "GET" and not kwargs.get("headers")
                 and kwargs.get("payload") is None, "Unreviewed publisher request or retry")
        publisher = urllib.parse.urlsplit(url).hostname in {
            "youthforeurope.eu", "www.youthforeurope.eu"}
        result = original_request(url, **kwargs)
        if publisher:
            status, headers, _ = result
            robots = url == ENTRY_TARGETS[0]
            need(rows and rows[-1]["url"] == url and rows[-1].get("completion")
                 and rows[-1].get("body_complete"),
                 "Publisher response body framing or closure cannot be certified")
            need(status == 200 or robots and 400 <= status < 500 and status != 429,
                 "Publisher status is outside approved access; no continuation")
            if not robots:
                need(headers.get_content_type() in {"text/html", "application/xhtml+xml"},
                     "Publisher did not return reviewed HTML content")
        return result

    def audited_write(state):
        row = {"intended": json.loads(canonical(state)), "acknowledged": False}
        writes.append(row)
        original_write(state)
        row["observed"] = adapter.load_budget()
        row["acknowledged"] = row["observed"] == row["intended"]
        row["acknowledged_at"] = adapter.time.monotonic()
        need(row["acknowledged"], "Original journal write readback differs")

    def audited_open(request, *args, **kwargs):
        url = request.full_url
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname in {"youthforeurope.eu", "www.youthforeurope.eu"}:
            need(len(rows) < len(targets) and url == targets[len(rows)]
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



DIAGNOSTIC_OWNER = (44192484351, 1, 'a69c4e2886bf338a459df0b453d93bb8a69b4809', 421955363, 134425223683)
DIAGNOSTIC_WORKFLOW = ".github/workflows/yfe-frontier-research.yml"
DIAGNOSTIC_BRANCH = "research/yfe-365-current-frontier"
DIAGNOSTIC_STATE_ARTIFACT = {'digest': 'sha256:bacfe2a0d86387ffced1537d8071ec0a4149b6e560fa79c24437b772b44c861e', 'id': 14376279119, 'size': 442}
DIAGNOSTIC_STATE_SHA = 'aa668c3b781bc2550b02f2d7047aab9f4956afc0ae86b1c5932bf51f89851c65'
TRANSFER_STATE_SHA = 'ffa47ccfc9ad831fb4a30e1d6c27b7515c16fb2df8e0b857e55472ecb7151c26'
RETAINED_TIMES = (1794927088.1677175, 1794927088.16976, 1794927100.6817563, 1794927100.683682, 1794955363.5624373, 1794955363.5656853, 1794955376.0595891, 1794955376.0630865)
WEBSITE_HEAD = "7da2b3eb81853707def0ff67de64f8d43773fe87"
EXPECTED_DETAILS = 12


FAILED_OWNER = (44194188565, 1, '004028bfd92fbbee66683eb3b0426a69ca924d18', 421963534, 134431131980)
FAILED_WORKFLOW = ".github/workflows/yfe-complete-qa.yml"
FAILED_BRANCH = "qa/yfe-365-complete-frontier"
FAILED_JOB_COMPLETED = "2026-11-17T22:54:30Z"
FAILED_STATE_ARTIFACT = {'digest': 'sha256:995146da1d69dc5257732f608469dc1d5a50cdf91970044328f2b4ddf3951926', 'id': 14377903117, 'size_in_bytes': 462}
FAILED_EVIDENCE_ARTIFACT = {'digest': 'sha256:cadf52a71c84fc47ccd8110505c1e21ec9e00eb49bea0097d0674f4515732005', 'id': 14378551800, 'size_in_bytes': 867475}
FAILED_STATE_SHA = '7110277f4c02bb7c977a12dd08bf68a1e4606a38ca62f8b91e0e226c1a0c0df4'
FAILED_TRANSFER_SHA = '41ef3a5f4c3808594ee2d8d45df7e115f4eee873ef90e52851955a7b54e0eba2'
FAILED_RETAINED_TIMES = (1794927088.1677175, 1794927088.16976, 1794927100.6817563, 1794927100.683682, 1794955363.5624373, 1794955363.5656853, 1794955376.0595891, 1794955376.0630865, 1794956021.011247, 1794956021.0136116, 1794956033.700837, 1794956033.703196, 1794956048.740699, 1794956048.7428298, 1794956061.6355479, 1794956061.6377199)
REVIEWED_ROSTER = (('98622', 'volunteering-try', 'https://youthforeurope.eu/project/public-health-hygiene-awareness-3/'), ('98706', 'stage', 'https://youthforeurope.eu/project/paid-traineeships-at-the-european-court-of-auditors-in-luxembourg-up-to-e2500-month/'), ('98690', 'training-course', 'https://youthforeurope.eu/project/innovation-study-tours/'), ('98594', 'stage', 'https://youthforeurope.eu/project/paid-internship-in-environmental-and-social-sustainability-and-climate-change-at-the-council-of-europe-development-bank-in-paris/'), ('98574', 'evs-project', 'https://youthforeurope.eu/project/esc-volunteering-in-a-residential-community-for-young-women-and-girls-in-italy/'), ('98567', 'evs-project', 'https://youthforeurope.eu/project/esc-volunteering-in-a-womens-community-house-and-a-nursery/'), ('98444', 'volunteering-try', 'https://youthforeurope.eu/project/english-teaching-professional-internship-in-buenos-aires-argentina/'), ('98248', 'volunteering-try', 'https://youthforeurope.eu/project/indonesia-volunteering-teaching-english-and-culture-exchange-2/'), ('97980', 'stage', 'https://youthforeurope.eu/project/remote-unpaid-internship-traineeship-journalist-and-content-creator/'), ('96459', 'volunteering-try', 'https://youthforeurope.eu/project/public-health-hygiene-awareness-2/'), ('81201', 'stage', 'https://youthforeurope.eu/project/event-planning-and-coordination-intern/'), ('81184', 'evs-project', 'https://youthforeurope.eu/project/volunteer-genova-italy-children-adolescents-centro-storico-ragazzi/'))

CONSUMER = """
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const {loadData} = await import(pathToFileURL(resolve('.qa-website/scripts/data.mjs')));
let input = '';
for await (const part of process.stdin) input += part;
const {records,metadata} = JSON.parse(input);
const source = 'youth-for-europe';
if(metadata.source !== source || records.some(record=>record.source!==source)) {
  throw new Error('Observer source identity mismatch');
}
const root = '/ram/datas';
const files = new Map([
  [root + '/' + source + '/data.json',JSON.stringify(records)],
  [root + '/' + source + '/metadata.json',JSON.stringify(metadata)],
]);
fs.lstat = async file => {
  if(file !== root && !files.has(file)) throw new Error('Unexpected observer path');
  return {isSymbolicLink:()=>false};
};
fs.readdir = async file => {
  if(file !== root) throw new Error('Unexpected observer root');
  return [{name:source,isSymbolicLink:()=>false,isDirectory:()=>true}];
};
fs.readFile = async file => {
  if(!files.has(file)) throw new Error('Missing observer input');
  return files.get(file);
};
const result = await loadData(root);
const expected = records.map(record=>record.id).sort();
const actual = result.opportunities.map(record=>record.id).sort();
if(JSON.stringify(expected)!==JSON.stringify(actual)) throw new Error('Observer ID mismatch');
console.log(JSON.stringify({count:actual.length,ids:actual,sources:result.sources.map(item=>item.id)}));
"""


def latest_diagnostic(adapter):
    run, attempt, head, workflow_id, job_id = DIAGNOSTIC_OWNER
    workflow = adapter.workflow_api(f"/actions/workflows/{workflow_id}")
    need(workflow["id"] == workflow_id and workflow["state"] == "disabled_manually"
         and workflow["path"] == DIAGNOSTIC_WORKFLOW, "Diagnostic workflow must be retired")
    expected = {"id": run, "run_attempt": attempt, "head_sha": head,
                "head_branch": DIAGNOSTIC_BRANCH, "path": DIAGNOSTIC_WORKFLOW,
                "workflow_id": workflow_id, "status": "completed", "conclusion": "success",
                "event": "push"}
    actual = adapter.workflow_api(f"/actions/runs/{run}/attempts/{attempt}")
    inventory = adapter.workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100")
    need(inventory["total_count"] == len(inventory["workflow_runs"]) == 1
         and all(all(row[k] == v for k, v in expected.items())
                 and all(row[k]["full_name"] == REPO for k in ("repository", "head_repository"))
                 for row in (actual, inventory["workflow_runs"][0])),
         "Reviewed single diagnostic owner differs")
    jobs = adapter.workflow_api(f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100")
    need(jobs["total_count"] == len(jobs["jobs"]) == 1, "Diagnostic job cardinality differs")
    job = jobs["jobs"][0]
    need(job["id"] == job_id and job["name"] == "observe" and job["run_id"] == run
         and job["run_attempt"] == attempt and job["head_sha"] == head
         and job["status"] == "completed" and job["conclusion"] == "success",
         "Reviewed diagnostic job differs")
    artifacts = adapter.workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    rows = [item for item in artifacts["artifacts"] if item["name"] == "yfe-frontier-pacing-state-1"]
    need(artifacts["total_count"] == len(artifacts["artifacts"]) and len(rows) == 1,
         "Reviewed diagnostic archive missing")
    info = rows[0]
    need(info["id"] == DIAGNOSTIC_STATE_ARTIFACT["id"]
         and info["digest"] == DIAGNOSTIC_STATE_ARTIFACT["digest"]
         and info["size_in_bytes"] == DIAGNOSTIC_STATE_ARTIFACT["size"]
         and info["workflow_run"]["id"] == run and info["workflow_run"]["head_sha"] == head
         and info["expired"] is False, "Reviewed exact diagnostic archive identity differs")
    return run, attempt, adapter.SOURCE_ID


def reviewed_incoming(adapter, original, expected_owner, identity, artifact_id, digest):
    need(identity == expected_owner and artifact_id == FAILED_STATE_ARTIFACT["id"]
         and digest == FAILED_STATE_ARTIFACT["digest"], "Unexpected failed native seed fetch")
    incoming = original(identity, artifact_id, digest)
    need(sha(canonical(incoming)) == FAILED_STATE_SHA and not incoming["blocked"],
         "Reviewed original failed native state differs")
    state = dict(incoming, starts=sorted(set(incoming["starts"]) | set(FAILED_RETAINED_TIMES)))
    need(sha(canonical(state)) == FAILED_TRANSFER_SHA, "Reviewed timeline transfer differs")
    return adapter.validate_budget(state)


def latest_failed_native(adapter):
    run, attempt, head, workflow_id, job_id = FAILED_OWNER
    workflow = adapter.workflow_api(f"/actions/workflows/{workflow_id}")
    need(workflow.get("id") == workflow_id and workflow.get("state") == "disabled_manually"
         and workflow.get("path") == FAILED_WORKFLOW, "Original failed native workflow must be retired")
    expected = {"id": run, "run_attempt": attempt, "head_sha": head,
                "head_branch": FAILED_BRANCH, "path": FAILED_WORKFLOW,
                "workflow_id": workflow_id, "status": "completed", "conclusion": "failure",
                "event": "push"}
    actual = adapter.workflow_api(f"/actions/runs/{run}/attempts/{attempt}")
    inventory = adapter.workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100")
    need(inventory["total_count"] == len(inventory["workflow_runs"]) == 1
         and all(all(row.get(key) == value for key, value in expected.items())
                 and all(row.get(key, {}).get("full_name") == REPO
                         for key in ("repository", "head_repository"))
                 for row in (actual, inventory["workflow_runs"][0])),
         "Exact original failed native owner differs")
    jobs = adapter.workflow_api(f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100")
    need(jobs["total_count"] == len(jobs["jobs"]) == 1, "Failed owner job cardinality differs")
    job = jobs["jobs"][0]
    need(job["id"] == job_id and job["name"] == "observe" and job["run_id"] == run
         and job["run_attempt"] == attempt and job["head_sha"] == head
         and job["status"] == "completed" and job["conclusion"] == "failure"
         and job["completed_at"] == FAILED_JOB_COMPLETED,
         "Exact original failed native job differs")
    artifacts = adapter.workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    rows = artifacts["artifacts"]
    need(artifacts["total_count"] == len(rows) == 2,
         "Failed owner artifact inventory changed")
    for expected_name, exact in (("yfe-complete-pacing-state-1", FAILED_STATE_ARTIFACT),
                                 ("yfe-complete-observations-1", FAILED_EVIDENCE_ARTIFACT)):
        candidates = [item for item in rows if item["name"] == expected_name]
        need(len(candidates) == 1, "Exact failed owner artifact missing")
        info = candidates[0]
        need(all(info.get(key) == value for key, value in exact.items())
             and info["workflow_run"]["id"] == run and info["workflow_run"]["head_sha"] == head
             and info["expired"] is False, "Exact failed owner artifact identity differs")
    return run, attempt, adapter.SOURCE_ID


def inspect_captured_details(adapter, original_explore, rows):
    """Inspect every actual detail independently after the full scope closes."""
    def decode(row):
        headers = Message()
        for key, value in row["headers"]:
            headers[key] = value
        raw = b"".join(base64.b64decode(segment, validate=True) for segment in row["segments"])
        return raw.decode(headers.get_content_charset() or "utf-8")

    original_source = adapter.source_text
    listing = [decode(rows[1]), decode(rows[-1])]
    try:
        adapter.source_text = lambda _url: listing.pop(0)
        _, _, opening = original_explore()
        try:
            _, _, closing = original_explore()
            closing_agrees = closing == opening
        except Exception:
            closing_agrees = False
    finally:
        adapter.source_text = original_source
    details = {row["url"]: row for row in rows[2:-1]}
    results = []
    for identifier, card in opening.items():
        try:
            result = adapter.detail_record(identifier, card, document=decode(details[card["url"]]))
            adapter.validate_records([result])
        except Exception as error:
            results.append({"native_id": identifier, "valid": False,
                            "stage": getattr(error, "stage", "parse"),
                            "error_type": type(error).__name__, "error": adapter.safe_error(error)})
        else:
            results.append({"native_id": identifier, "valid": True})
    return closing_agrees, results


def consume(records, metadata, *, positive=True):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", CONSUMER], cwd=ROOT,
        input=json.dumps({"records": records, "metadata": metadata}, ensure_ascii=False),
        encoding="utf-8", capture_output=True, timeout=30, check=False,
    )
    need((result.returncode == 0) == positive, "Pinned website consumer outcome differs")
    return json.loads(result.stdout) if positive else {"rejected": True}


def consume_complete(adapter, records):
    adapter.validate_records(records)
    stamp = adapter.utc_now()
    filtered = adapter.remove_expired_records(records, stamp)
    wrong = dict(adapter.metadata(stamp, {}, records), source="unreviewed-source")
    return {"raw": consume(records, adapter.metadata(stamp, {}, records)),
            "filtered": consume(filtered, adapter.metadata(stamp, {}, filtered)),
            "valid_empty": consume([], adapter.metadata(stamp, {}, [])),
            "wrong_source": consume(records, wrong, positive=False)}


def validate_rows(adapter, targets, rows, writes):
    need(len(rows) == len(targets) == 2 + EXPECTED_DETAILS + 1,
         "Wire count differs from reviewed full frontier")
    need([row["url"] for row in rows] == targets and all(row.get("body_complete")
         and row.get("completion") for row in rows)
         and all(row.get("status") == 200 for row in rows[1:])
         and (rows[0]["status"] == 200 or
              400 <= rows[0]["status"] < 500 and rows[0]["status"] != 429),
         "Original complete response capture is missing")
    need(all(w["acknowledged"] and w["observed"] == w["intended"] for w in writes),
         "Original pacing writes are not exactly acknowledged")
    for row in rows:
        start = row["actual_wall_start"]
        prior = [w for w in writes if w["acknowledged_at"] <= row["actual_start"]]
        need(bool(prior) and bool(prior[-1]["observed"]["starts"]) and
             prior[-1]["observed"]["starts"][-1] <= start and
             start - prior[-1]["observed"]["completed"] >= prior[-1]["observed"]["interval"],
             "Physical reservation or retained completion pacing differs")
    gaps = [right["actual_start"] - left["completion"] for left, right in zip(rows, rows[1:])]
    need(min(gaps) >= 12.2 and rows[-1]["actual_start"] - rows[1]["completion"] >= 30,
         "Measured original pacing differs")
    begins = [row["actual_start"] for row in rows]
    need(max(sum(later - 60 < prior <= later for prior in begins) for later in begins) <= 5,
         "Measured rolling publisher pace differs")
    return min(gaps)


def replay(adapter, original_explore, rows, records):
    bodies = {}
    for row in rows[1:]:
        value = b"".join(base64.b64decode(segment, validate=True) for segment in row["segments"])
        headers = Message()
        for field, value_for_field in row["headers"]:
            headers[field] = value_for_field
        bodies.setdefault(row["url"], []).append(
            value.decode(headers.get_content_charset() or "utf-8"))
    original_source, original_now = adapter.source_text, adapter.utc_now
    try:
        adapter.source_text = lambda url: bodies[url].pop(0)
        _, _, cards = original_explore()
        by_id = {row["id"]: row for row in records}
        for name, card in cards.items():
            expected = by_id[adapter.SOURCE_ID + "-" + name]
            stamp = expected["created_at"]
            adapter.utc_now = lambda: stamp
            need(adapter.detail_record(name, card) == expected,
                 "Actual response replay differs from the original normalized record")
        _, _, closing = original_explore()
        need(cards == closing and all(not retained for retained in bodies.values()),
             "Actual opening and closing inventories differ")
    finally:
        adapter.source_text, adapter.utc_now = original_source, original_now


def run():
    adapter = load_adapter()
    targets = list(ENTRY_TARGETS)
    rows, writes = [], []
    instrumentation(adapter, rows, writes, targets)
    original_explore = adapter.explore_page
    report = {"owner": None, "original_outcome": None, "observer_outcome": 1,
              "source_sha256": SOURCE_SHA, "predecessor": list(FAILED_OWNER),
              "complete_actual_capture": False}
    semantic_details = []
    output, diagnostics = io.StringIO(), io.StringIO()
    ownership = None

    def enumerate_first_browse(*args, **kwargs):
        result = original_explore(*args, **kwargs)
        if len(targets) == len(ENTRY_TARGETS):
            _, first, cards = result
            need(first["found_posts"] == len(cards) == EXPECTED_DETAILS
                 and first["max_num_pages"] == 1 and first["posts_per_page"] == 20,
                 "Publisher frontier changed before any detail request")
            live_roster = tuple((key, card["type"], card["url"]) for key, card in cards.items())
            need(live_roster == REVIEWED_ROSTER, "Exact reviewed route roster changed")
            detail_urls = [route for _, _, route in REVIEWED_ROSTER]
            need(len(set(detail_urls)) == EXPECTED_DETAILS and all(
                urllib.parse.urlsplit(url).scheme == "https"
                and urllib.parse.urlsplit(url).netloc == "youthforeurope.eu"
                and urllib.parse.urlsplit(url).path.startswith("/project/")
                and not urllib.parse.urlsplit(url).query and not urllib.parse.urlsplit(url).fragment
                for url in detail_urls), "Native catalogue returned unreviewed detail URLs")
            targets.extend(detail_urls + [ENTRY_TARGETS[-1]])
        return result

    adapter.explore_page = enumerate_first_browse
    with adapter.execution(), contextlib.redirect_stderr(diagnostics):
        try:
            with adapter.phase("restore", 180):
                ownership = exact_qa(adapter)
                report["owner"] = ownership
                latest_producer(adapter)
                latest_diagnostic(adapter)
                predecessor = latest_failed_native(adapter)
                tested_website = subprocess.check_output(
                    ["git", "-C", str(ROOT / ".qa-website"), "rev-parse", "HEAD"],
                    text=True, timeout=10).strip()
                need(tested_website == WEBSITE_HEAD, "Pinned website consumer revision differs")
                need(protect(b"Youth for Europe complete evidence preflight"),
                     "Exact complete evidence sealing unavailable")
                adapter.current_run_identity = lambda: ownership[:2]

                def restore_owner():
                    adapter._ATTEMPT_HEADS[predecessor[:2]] = FAILED_OWNER[2]
                    return predecessor

                adapter.latest_family_run = restore_owner
                adapter._ARTIFACT_NAME = "yfe-complete-pacing-state"
                original_download = adapter.download_inert_artifact
                adapter.download_inert_artifact = lambda *details: reviewed_incoming(
                    adapter, original_download, predecessor, *details)
                adapter.prepare_collection()
                with adapter.locked_budget():
                    state = adapter.load_budget()
                need(not state["blocked"] and state["rate_limit_until"] <= adapter.time.time(),
                     "Existing publisher hold prevents native test")
                wait = state["until"] - adapter.time.time()
                if wait > 0:
                    adapter.time.sleep(wait)
                need(exact_qa(adapter) == ownership, "QA owner changed during startup quiet")
                latest_producer(adapter)
                latest_diagnostic(adapter)
                need(latest_failed_native(adapter) == predecessor, "Failed native predecessor changed")
                adapter.journal_preopener()
            saved_argv, saved_module = sys.argv, sys.modules.get("adapter")
            try:
                sys.argv = [str(ROOT / TEST)]
                sys.modules["adapter"] = adapter
                with contextlib.redirect_stdout(output):
                    try:
                        runpy.run_path(str(ROOT / TEST), run_name="__main__")
                        report["original_outcome"] = 0
                    except SystemExit as exit_error:
                        report["original_outcome"] = int(exit_error.code or 0)
            finally:
                sys.argv = saved_argv
                if saved_module is None:
                    sys.modules.pop("adapter", None)
                else:
                    sys.modules["adapter"] = saved_module
            if len(rows) == 2 + EXPECTED_DETAILS + 1:
                report["measured_minimum_gap"] = validate_rows(adapter, targets, rows, writes)
                report["complete_actual_capture"] = True
                closing_agrees, semantic_details = inspect_captured_details(adapter, original_explore, rows)
                report["closing_frontier_matches"] = closing_agrees
                report["detail_analysis"] = [
                    {key: value for key, value in row.items() if key != "error"}
                    for row in semantic_details
                ]
            need(report["original_outcome"] == 0, "Original nonpublishing test failed")
            need(report["complete_actual_capture"] and report["closing_frontier_matches"]
                 and len(semantic_details) == EXPECTED_DETAILS
                 and all(row["valid"] for row in semantic_details),
                 "Complete detail replay and closing frontier differ from native success")
            records = json.loads(output.getvalue())
            need(len(records) == EXPECTED_DETAILS, "Original native output cardinality differs")
            replay(adapter, original_explore, rows, records)
            report["body_replay"] = True
            report["consumer"] = consume_complete(adapter, records)
            report["website_revision"] = WEBSITE_HEAD
            report["observer_outcome"] = 0
        except BaseException as failure:
            report["error"] = type(failure).__name__
        finally:
            if ownership is not None:
                try:
                    with adapter.phase("export", adapter.EXPORT_TIMEOUT):
                        adapter.export_family_artifact()
                except BaseException as failure:
                    report["export_error"] = type(failure).__name__
                    report["observer_outcome"] = 1
    report["requests"], report["writes"] = len(rows), len(writes)
    temp = Path(os.environ["RUNNER_TEMP"])
    plaintext = canonical({"report": report, "requests": rows, "persistence": writes,
                           "original_stdout": output.getvalue(),
                           "original_stderr": diagnostics.getvalue(),
                           "detail_analysis": semantic_details})
    encrypted = protect(plaintext)
    (temp / "yfe-exact-evidence.cms").write_bytes(encrypted)
    report.update(evidence_sha256=sha(encrypted), plaintext_sha256=sha(plaintext))
    (temp / "yfe-exact-observations.json").write_bytes(canonical(report))
    print(json.dumps({key: report[key] for key in ("original_outcome", "observer_outcome",
                                                 "requests", "writes")}))
    return report["observer_outcome"]


if __name__ == "__main__":
    sys.exit(run())
