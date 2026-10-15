"""Collect the complete public Opportunities for Youth API archive."""

import argparse
import base64
import binascii
import contextlib
import datetime
import email.utils
import fcntl
import hashlib
import html
import html.parser
import io
import ipaddress
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "opportunities-for-youth"
SOURCE_URL = "https://opportunitiesforyouth.org/wp-json/wp/v2/posts"
WEBSITE_URL = "https://opportunitiesforyouth.org/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = None
PUBLISHER_TYPE = "aggregator"
ATTRIBUTION = "Opportunities for Youth"

CATEGORIES = (
    "scholarships",
    "internships",
    "volunteering",
    "fellowships",
    "training",
    "competitions",
    "grants",
    "jobs",
    "other",
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
_ROBOTS_CACHE = {}
_MAX_SNAPSHOT_BYTES = 20 * 1024 * 1024
_MAX_GITHUB_BLOB_BYTES = 32 * 1024 * 1024


_PAGE_COMPLETED = None


_STATE_TRUSTED = False
_COLLECTION_LOCK = None
_STARTUP_NOT_BEFORE = 0
_LEGACY_HISTORY = {
    38622923304: [
        1,
        "1ec0ebb3ac9520c768ef2bf02ac11563b728d3e2",
        "success",
        "2026-10-14T19:37:54Z",
    ],
    38627583437: [
        1,
        "9d3509de25412b04ddcb153e0543209b3b5f349b",
        "success",
        "2026-10-14T20:54:23Z",
    ],
    38642304839: [
        1,
        "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
        "success",
        "2026-10-14T21:54:38Z",
    ],
    38684236083: [
        1,
        "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "failure",
        "2026-10-15T06:32:18Z",
    ],
    38684947098: [
        1,
        "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "success",
        "2026-10-15T07:13:05Z",
    ],
    38721259441: [
        1,
        "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "success",
        "2026-10-15T10:59:23Z",
    ],
    38720084089: [
        1,
        "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "failure",
        "2026-10-15T12:24:38Z",
    ],
}
_HELD_STATE = {
    "starts": [
        1792006674.0,
        1792011263.0,
        1792012732.5680666,
        1792014878.0,
        1792045938.0,
        1792048385.0,
        1792061963.0,
        1792067078.0,
    ],
    "interval": 30,
    "until": 1792013933.5680666,
    "blocked": True,
    "completed_at": 1792067078.0,
    "rate_failures": 0,
    "legacy_rate_history_unknown": True,
    "page_gap": 12,
    "page_completed_at": 0,
}


def release_collection():
    global _COLLECTION_LOCK, _STATE_TRUSTED
    if _COLLECTION_LOCK is not None:
        os.close(_COLLECTION_LOCK)
        _COLLECTION_LOCK = None
    _STATE_TRUSTED = False


@contextlib.contextmanager
def locked_budget(url):
    """Keep the original host budget and inode under its existing lock."""
    host = (
        (urllib.parse.urlsplit(url).hostname or "")
        .lower()
        .removeprefix("www.")
    )
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    information = os.lstat(directory)
    if (
        not os.path.isdir(directory)
        or os.path.islink(directory)
        or information.st_uid != os.getuid()
        or information.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    descriptor = os.open(
        budget_path(host), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    information = os.fstat(descriptor)
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_uid != os.getuid()
        or information.st_nlink != 1
        or information.st_mode & 0o077
    ):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher journal", "access")
    state = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read(16385)
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher pacing journal", "access")
        if not raw and os.path.exists(os.path.join(directory, "mature")):
            raise AdapterError("Missing mature publisher journal", "access")
        budget = (
            json.loads(raw)
            if raw
            else {"starts": [], "interval": 6, "until": 0}
        )
        budget = validate_budget(budget)
        yield state, budget
    finally:
        state.close()


def validate_budget(value):
    """Validate inert state, including the original three-field local format."""
    if (
        not isinstance(value, dict)
        or not {"starts", "interval", "until"} <= value.keys()
    ):
        raise AdapterError("Invalid publisher pacing state", "access")
    result = {
        **value,
        "blocked": value.get("blocked", False),
        "page_gap": value.get("page_gap", 12),
        "page_completed_at": value.get("page_completed_at", 0),
        "completed_at": value.get("completed_at", 0),
        "rate_failures": value.get("rate_failures", 0),
        "legacy_rate_history_unknown": value.get(
            "legacy_rate_history_unknown", True
        ),
    }
    if (
        set(result)
        != {
            "starts",
            "interval",
            "until",
            "blocked",
            "completed_at",
            "rate_failures",
            "legacy_rate_history_unknown",
            "page_gap",
            "page_completed_at",
        }
        or type(result["rate_failures"]) is not int
        or not 0 <= result["rate_failures"] <= 1000000
        or type(result["legacy_rate_history_unknown"]) is not bool
        or type(result["blocked"]) is not bool
        or not isinstance(result["starts"], list)
        or len(result["starts"]) > 128
    ):
        raise AdapterError("Invalid publisher pacing envelope", "access")
    numbers = [
        result["interval"],
        result["until"],
        result["completed_at"],
        result["page_gap"],
        result["page_completed_at"],
        *result["starts"],
    ]
    if (
        any(
            type(n) not in (int, float) or not math.isfinite(n) or n < 0
            for n in numbers
        )
        or result["interval"] < 6
        or result["page_gap"] < 12
    ):
        raise AdapterError("Invalid publisher pacing values", "access")
    if (
        result["starts"] != sorted(set(result["starts"]))
        or any(n > time.time() + 5 for n in result["starts"])
        or result["completed_at"] > time.time() + 5
        or result["page_completed_at"] > time.time() + 5
    ):
        raise AdapterError("Invalid publisher pacing chronology", "access")
    result["interval"] = max(result["interval"], 30)
    return result


def workflow_api(path):
    """Read only this repository's authenticated workflow evidence."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    status, _, body = request_bytes(
        "https://api.github.com/repos/YouthOpps/data-pipeline" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        },
    )
    if status != 200:
        raise AdapterError("Workflow evidence unavailable", "access")
    return json.loads(body)


def workflow_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError(
            "Unexpected workflow repository or branch", "access"
        )
    values = [
        os.environ.get(k, "") for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    ]
    if any(not re.fullmatch(r"[1-9][0-9]*", v) for v in values):
        raise AdapterError("Invalid current workflow identity", "access")
    return tuple(map(int, values))


def trusted_run(run):
    if (
        run.get("repository", {}).get("full_name") != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or run.get("path") != ".github/workflows/fetch-" + SOURCE_ID + ".yml"
        or run.get("workflow_id") != 384711022
        or type(run.get("id")) is not int
        or type(run.get("run_attempt")) is not int
        or run["run_attempt"] < 1
        or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
    ):
        raise AdapterError("Untrusted source workflow history", "access")
    return run


def previous_attempt(run):
    """Keep completed state hidden by a current or queued rerun attempt."""
    if run["run_attempt"] == 1:
        return None
    prior = trusted_run(
        workflow_api(
            f"/actions/runs/{run['id']}/attempts/{run['run_attempt'] - 1}"
        )
    )
    if (
        prior["id"] != run["id"]
        or prior["run_attempt"] != run["run_attempt"] - 1
        or prior["head_sha"] != run["head_sha"]
        or prior.get("status") != "completed"
    ):
        raise AdapterError("Untrusted prior completed rerun attempt", "access")
    return prior


def workflow_history():
    """Read complete unfiltered source history, including older-ID reruns."""
    current_id, current_attempt = workflow_identity()
    runs, seen, total = [], set(), None
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/workflows/384711022/runs?per_page=100&page={page}"
        )
        entries = result.get("workflow_runs")
        count = result.get("total_count")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(entries, list)
            or len(entries) > 100
            or total is not None
            and total != count
        ):
            raise AdapterError(
                "Changed or invalid source workflow inventory", "access"
            )
        total = count
        for run in entries:
            trusted_run(run)
            if run["id"] in seen:
                raise AdapterError("Duplicate workflow history", "access")
            seen.add(run["id"])
            if run["id"] == current_id:
                if run["run_attempt"] != current_attempt:
                    raise AdapterError(
                        "Current workflow attempt changed", "access"
                    )
                prior = previous_attempt(run)
                if prior is not None:
                    runs.append(prior)
                continue
            if run.get("status") in (
                "queued",
                "waiting",
                "pending",
                "requested",
            ):
                prior = previous_attempt(run)
                if prior is not None:
                    runs.append(prior)
                continue
            if run.get("status") != "completed":
                raise AdapterError(
                    "Another source collection is active", "access"
                )
            runs.append(run)
        if len(seen) == total:
            if current_id not in seen or not runs:
                raise AdapterError(
                    "Missing current or previous workflow evidence", "access"
                )
            return runs
        if len(entries) != 100 or len(seen) > total:
            raise AdapterError(
                "Incomplete source workflow inventory", "access"
            )
    raise AdapterError("Source history exceeds reviewed limit", "access")


def run_completion(run):
    jobs = workflow_api(
        f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
    )
    entries = jobs.get("jobs")
    if (
        run.get("status") != "completed"
        or not isinstance(entries, list)
        or len(entries) != jobs.get("total_count")
        or any(
            j.get("status") != "completed"
            or j.get("run_id") != run["id"]
            or j.get("run_attempt") != run["run_attempt"]
            or j.get("head_sha") != run["head_sha"]
            or j.get("head_branch") != "main"
            for j in entries
        )
    ):
        raise AdapterError("Incomplete completed attempt jobs", "access")
    if not entries:
        if run.get("conclusion") == "cancelled":
            return None
        raise AdapterError("Executed source attempt has no jobs", "access")
    try:
        times = [
            datetime.datetime.fromisoformat(
                j["completed_at"].replace("Z", "+00:00")
            )
            for j in entries
        ]
        if any(t.tzinfo is None for t in times):
            raise ValueError()
        end = max(t.timestamp() for t in times)
        if not 0 < end <= time.time() + 5:
            raise ValueError()
        return end
    except (ValueError, KeyError, TypeError):
        raise AdapterError(
            "Invalid completed job chronology", "access"
        ) from None


def artifact_inventory(run):
    data = workflow_api(f"/actions/runs/{run['id']}/artifacts?per_page=100")
    entries = data.get("artifacts")
    if not isinstance(entries, list) or len(entries) != data.get(
        "total_count"
    ):
        raise AdapterError("Incomplete source artifact inventory", "access")
    return entries


def artifact_budget(run):
    """Require the newest exact attempt's authenticated bounded inert artifact."""
    matches = [
        a
        for a in artifact_inventory(run)
        if a.get("name")
        == "opportunities-for-youth-pacing-state-" + str(run["run_attempt"])
    ]
    if len(matches) != 1:
        raise AdapterError(
            "Newest source attempt lacks its pacing artifact", "access"
        )
    artifact = matches[0]
    if (
        artifact.get("expired") is not False
        or type(artifact.get("id")) is not int
        or artifact.get("workflow_run", {}).get("id") != run["id"]
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
    ):
        raise AdapterError("Invalid source artifact ownership", "access")
    token = os.environ["GITHUB_TOKEN"]
    status, headers, _ = request_bytes(
        "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/"
        + str(artifact["id"])
        + "/zip",
        headers={"Authorization": "Bearer " + token},
    )
    destination = headers.get("Location", "")
    parsed = urllib.parse.urlsplit(destination)
    if (
        status != 302
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or not (parsed.hostname or "").endswith(
            (".blob.core.windows.net", ".githubusercontent.com")
        )
    ):
        raise AdapterError("Unsafe source artifact redirect", "access")
    # The signed URL is neither logged nor saved; credentials stay at GitHub.
    status, _, archive_bytes = request_bytes(destination)
    try:
        if (
            status != 200
            or len(archive_bytes) > 32768
            or hashlib.sha256(archive_bytes).hexdigest()
            != artifact["digest"][7:]
        ):
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                raise ValueError()
            entry = entries[0]
            if (
                entry.filename != "pacing-state.json"
                or entry.file_size > 16384
                or entry.flag_bits & 1
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError()
            envelope = json.loads(archive.read(entry))
        expected = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "source": SOURCE_ID,
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
            "head_sha": run["head_sha"],
        }
        if set(envelope) != {*expected, "state"} or any(
            type(envelope[k]) is not type(v) or envelope[k] != v
            for k, v in expected.items()
        ):
            raise ValueError()
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            "Invalid source pacing artifact", "access"
        ) from None


def prepare_pacing():
    """Conserve native local state and restore the newest completed production state."""
    global _STATE_TRUSTED, _COLLECTION_LOCK, _STARTUP_NOT_BEFORE
    if _STATE_TRUSTED:
        return
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    with locked_budget(SOURCE_URL):
        pass
    descriptor = os.open(
        os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise AdapterError("Unsafe collection lock", "access")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        os.close(descriptor)
        raise AdapterError(
            "Another collector or unsafe collection lock", "access"
        ) from None
    _COLLECTION_LOCK = descriptor
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if hosted:
        runs = workflow_history()
        if {r["id"] for r in runs} == set(_LEGACY_HISTORY):
            incoming = legacy_migration(runs)
        else:
            completed = []
            for run in runs:
                ended = run_completion(run)
                if ended is not None:
                    completed.append(
                        (ended, run["id"], run["run_attempt"], run)
                    )
            completed.sort()
            if not completed or (
                len(completed) > 1 and completed[-1][0] == completed[-2][0]
            ):
                raise AdapterError(
                    "Ambiguous source attempt chronology", "access"
                )
            incoming = artifact_budget(completed[-1][-1])
    else:
        marker = os.path.join(directory, "mature")
        incoming = None if os.path.exists(marker) else held_transition()
    if incoming is not None:
        with locked_budget(SOURCE_URL) as (state, local):
            for field in (
                "interval",
                "until",
                "completed_at",
                "rate_failures",
                "page_gap",
                "page_completed_at",
            ):
                local[field] = max(local[field], incoming[field])
            local["starts"] = sorted(set(local["starts"] + incoming["starts"]))
            local["blocked"] |= incoming["blocked"]
            local["legacy_rate_history_unknown"] |= incoming[
                "legacy_rate_history_unknown"
            ]
            save_budget(state, local)
    # This safety patch cannot release the unexplained historical 403.
    with locked_budget(SOURCE_URL) as (state, local):
        local["blocked"] = True
        save_budget(state, local)
    _STARTUP_NOT_BEFORE = time.time() + 60
    marker = os.open(
        os.path.join(directory, "mature"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(marker)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise AdapterError("Unsafe publisher maturity marker", "access")
    finally:
        os.close(marker)
    _STATE_TRUSTED = True


def export_pacing():
    """Export only trusted finalized state and signal this step's fresh artifact."""
    path = os.environ.get("OFY_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
    run_id, attempt = workflow_identity()
    head = os.environ.get("GITHUB_SHA", "")
    directory = os.environ.get("RUNNER_TEMP", "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", head)
        or not os.path.isabs(directory)
        or os.path.dirname(os.path.abspath(path))
        != os.path.realpath(directory)
    ):
        raise AdapterError("Unsafe source artifact output identity", "access")
    with locked_budget(SOURCE_URL) as (_, state):
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "source": SOURCE_ID,
            "run_id": run_id,
            "run_attempt": attempt,
            "head_sha": head,
            "state": validate_budget(state),
        }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=directory, delete=False
        ) as file:
            temporary = file.name
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(
            os.environ["GITHUB_OUTPUT"],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
        )
        with os.fdopen(descriptor, "a") as output:
            info = os.fstat(output.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise AdapterError("Unsafe workflow output file", "access")
            output.write("pacing_ready=true\n")
    finally:
        if temporary:
            os.unlink(temporary)


def pace(url, interval=30):
    """Reserve starts while retaining the lock through response completion."""
    manager = locked_budget(url)
    state, budget = manager.__enter__()
    try:
        if budget["blocked"]:
            raise AdapterError(
                "Publisher refusal requires reviewed recovery", "access"
            )
        if (
            budget["rate_failures"] >= 100
            or budget["interval"] > sys.float_info.max / 2
            or budget["page_gap"] > sys.float_info.max / 2
        ):
            raise AdapterError("Rate-limit history requires review", "access")
        if budget["until"] > time.time():
            raise AdapterError(
                "Retained publisher embargo is not complete", "access"
            )
        budget["interval"] = max(budget["interval"], interval, 30)
        while True:
            request_timeout()
            now = time.time()
            budget["starts"] = [
                start for start in budget["starts"] if now - start < 60
            ]
            ready = max(
                _STARTUP_NOT_BEFORE,
                budget["until"],
                budget["completed_at"] + budget["interval"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (
                    budget["starts"][0] + 60
                    if len(budget["starts"]) >= 5
                    else now
                ),
            )
            if ready <= now:
                budget["starts"].append(now)
                save_budget(state, budget)
                return state, budget, manager
            time.sleep(min(ready - now, 60))
    except Exception:
        manager.__exit__(*sys.exc_info())
        raise


def save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


@contextlib.contextmanager
def budget_state(host):
    """Keep request starts and backoff shared across local processes."""
    with open(budget_path(host), "a+", encoding="utf-8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            file.seek(0)
            text = file.read()
            state = (json.loads(text) if text else {
                "starts": [],
                "interval": 6,
                "until": 0
            })
            yield state
            file.seek(0)
            file.truncate()
            json.dump(state, file)
            file.flush()
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def service_pace(url, interval=6):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    host = host.removeprefix("www.")
    while True:
        with budget_state(host) as state:
            now = time.time()
            starts = [start for start in state["starts"] if now - start < 60]
            state["starts"] = starts
            state["interval"] = max(6, interval, state["interval"])
            ready = max(
                state["until"],
                starts[-1] + state["interval"] if starts else now,
                starts[0] + 60 if len(starts) >= 10 else now,
            )
            if ready <= now:
                state["starts"].append(now)
                return {"host": host}
            wait = ready - now
        time.sleep(wait)


def request_service_bytes(url,
                  *,
                  interval=6,
                  method="GET",
                  headers=None,
                  payload=None,
                  max_bytes=5_000_000):
    budget = service_pace(url, interval)
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "User-Agent": _USER_AGENT,
            **(headers or {})
        },
    )
    try:
        response = _OPENER.open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        retry = response.headers.get("Retry-After")
        if retry:
            try:
                seconds = (
                    float(retry) if re.fullmatch(r"\d+(?:\.\d+)?", retry) else
                    email.utils.parsedate_to_datetime(retry).timestamp() -
                    time.time())
                with budget_state(budget["host"]) as state:
                    state["until"] = max(state["until"],
                                         time.time() + max(0, seconds))
            except (ValueError, TypeError, OverflowError):
                pass
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise AdapterError(
                "Response exceeds permitted byte limit",
                "publish" if url.startswith(_GITHUB + "/") else "fetch",
            )
        return response.status, response.headers, body


class AccessText(html.parser.HTMLParser):
    """Read primary visible text without navigation or CAPTCHA decoration."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title_parts = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        skipped = (
            tag
            in {
                "head",
                "script",
                "style",
                "noscript",
                "footer",
                "header",
                "nav",
                "aside",
                "template",
            }
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or bool(
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden",
                    attrs.get("style", ""),
                    re.I,
                )
            )
            or attrs.get("role")
            in {"navigation", "contentinfo", "complementary", "banner"}
        )
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.stack.append((tag, skipped))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        tags = [tag for tag, _ in self.stack]
        if (
            "head" in tags
            and "title" in tags
            and not any(
                tag in {"body", "footer", "nav", "aside", "header", "template"}
                for tag in tags
            )
        ):
            self.title_parts.append(data)
        if not any(skipped for _, skipped in self.stack):
            self.parts.append(data)


def ensure_publisher_document(document, state, budget):
    visible = AccessText(document)
    text = " ".join(visible.parts)
    denial = re.search(
        r"\b(?:verify (?:that )?you are human|access denied|"
        r"checking your browser|complete the (?:security check|captcha)|"
        r"you have been blocked)\b",
        text,
        re.I,
    )
    title_denial = re.fullmatch(
        r"\s*(?:Access denied|Forbidden|Just a moment\.*|"
        r"Attention required\.*)\s*",
        " ".join(visible.title_parts),
        re.I,
    )
    if denial or title_denial:
        budget["blocked"] = True
        save_budget(state, budget)
        raise AdapterError(
            "Publisher returned a visible access challenge", "access"
        )


def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    max_bytes=5_000_000,
    robots=False,
):
    publisher = urllib.parse.urlsplit(url).hostname in (
        "opportunitiesforyouth.org",
        "www.opportunitiesforyouth.org",
    )
    if not publisher:
        return request_service_bytes(
            url,
            interval=interval,
            method=method,
            headers=headers,
            payload=payload,
            max_bytes=max_bytes,
        )
    if not _STATE_TRUSTED:
        prepare_pacing()
    state, budget, manager = (
        pace(url, interval) if publisher else (None, None, None)
    )
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={**(headers or {}), "User-Agent": _USER_AGENT},
        )
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retry = response.headers.get("Retry-After")
            if publisher and retry:
                try:
                    now = time.time()
                    if re.fullmatch(r"\d+(?:\.\d+)?", retry):
                        seconds = float(retry)
                    else:
                        instant = email.utils.parsedate_to_datetime(retry)
                        if instant.tzinfo is None:
                            raise ValueError("Retry-After requires timezone")
                        seconds = instant.timestamp() - now
                    until = now + seconds
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise ValueError("Unusable Retry-After")
                    datetime.datetime.fromtimestamp(
                        until, datetime.timezone.utc
                    )
                    budget["until"] = max(budget["until"], until)
                    save_budget(state, budget)
                except (ValueError, TypeError, OverflowError, OSError):
                    # Bad timing supplies no evidence of an access refusal.
                    pass
            if publisher and response.status == 429:
                budget["rate_failures"] += 1
                budget["interval"] = max(budget["interval"], 30) * 2
                budget["page_gap"] = max(
                    budget["page_gap"] * 2,
                    60 * 2 ** (budget["rate_failures"] - 1),
                )
                budget["until"] = max(
                    budget["until"],
                    time.time() + 1800 * 2 ** (budget["rate_failures"] - 1),
                )
                save_budget(state, budget)
            if publisher and response.status in (401, 403):
                if not robots:
                    budget["blocked"] = True
                    save_budget(state, budget)
                    raise AdapterError(
                        "Publisher refused content: HTTP "
                        + str(response.status),
                        "access",
                        response.status,
                    )
            try:
                body = response.read(max_bytes + 1)
            except Exception:
                if response.status == 429:
                    raise AdapterError(
                        "Publisher rate limit; retained backoff; body unreadable",
                        "access",
                        429,
                    ) from None
                raise
            if len(body) > max_bytes and response.status != 429:
                raise AdapterError(
                    "Response exceeds permitted byte limit",
                    "publish" if url.startswith(_GITHUB + "/") else "fetch",
                )
            if (
                not robots or response.status < 400 or response.status == 429
            ) and (
                re.match(
                    rb"\s*(?:<!doctype\s+html|<html\b|<head\b|<body\b|<main\b|<p\b|<div\b)",
                    body,
                    re.I,
                )
                or response.headers.get_content_type() == "text/html"
            ):
                ensure_publisher_document(
                    body.decode(
                        response.headers.get_content_charset() or "utf-8",
                        errors="replace",
                    ),
                    state,
                    budget,
                )
            if response.status == 429:
                raise AdapterError(
                    "Publisher rate limit; retained backoff"
                    + ("; body oversized" if len(body) > max_bytes else ""),
                    "access",
                    429,
                )
            return response.status, response.headers, body
    finally:
        if manager is not None:
            budget["completed_at"] = time.time()
            try:
                save_budget(state, budget)
            finally:
                manager.__exit__(None, None, None)

def legacy_migration(runs):
    """Preserve explicit unknown history; never infer an empty old allowance."""
    if len(runs) != len(_LEGACY_HISTORY) or {run["id"] for run in runs} != set(
        _LEGACY_HISTORY
    ):
        raise AdapterError("Legacy source history changed", "access")
    completions = []
    for run in runs:
        expected = _LEGACY_HISTORY.get(run["id"])
        if (
            expected is None
            or [run["run_attempt"], run["head_sha"], run.get("conclusion")]
            != expected[:3]
            or artifact_inventory(run)
        ):
            raise AdapterError("Untrusted legacy attempt", "access")
        ended = run_completion(run)
        wanted = (
            datetime.datetime.fromisoformat(
                expected[3].replace("Z", "+00:00")
            ).timestamp()
            if expected[3] is not None
            else None
        )
        if ended != wanted:
            raise AdapterError("Legacy job chronology changed", "access")
        if ended is not None:
            completions.append(ended)
    state = held_transition()
    # Completion upper bounds conservatively count known past attempts;
    # they are not reconstructed physical-start measurements or old headers.
    state["starts"] = sorted(set(state["starts"] + completions))
    state["completed_at"] = max(state["completed_at"], max(completions))
    state["until"] = max(
        state["until"], state["completed_at"] + state["interval"]
    )
    return validate_budget(state)


def held_transition():
    """Retain known restrictions and unknown history without granting access."""
    return validate_budget(_HELD_STATE)


def request_timeout():
    return 30


@contextlib.contextmanager
def collection_lock():
    """Use one lifecycle lock from state restoration through final export."""
    owned = _COLLECTION_LOCK is None
    try:
        prepare_pacing()
        yield
    finally:
        if owned:
            try:
                export_pacing()
            finally:
                release_collection()


def collect():
    with collection_lock():
        with locked_budget(SOURCE_URL) as (_, state):
            if state["blocked"]:
                raise AdapterError(
                    "Unresolved historical publisher 403; source remains held",
                    "access",
                    403,
                )
        return collect_inventory()


def fetch_inventory_page(fetch, *args, **kwargs):
    """Retain the existing page floor and stronger durable throttle spacing."""
    global _PAGE_COMPLETED
    prepare_pacing()
    with locked_budget(SOURCE_URL) as (_, state):
        if state["blocked"]:
            raise AdapterError("Publisher access remains held", "access", 403)
        if state["until"] > time.time():
            raise AdapterError(
                "Retained publisher embargo is not complete", "access"
            )
        completed, gap = state["page_completed_at"], state["page_gap"]
    if _PAGE_COMPLETED is not None:
        completed = max(
            completed, time.time() - (time.monotonic() - _PAGE_COMPLETED)
        )
    while completed and completed + gap > time.time():
        time.sleep(min(completed + gap - time.time(), 60))
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()
        with locked_budget(SOURCE_URL) as (file, state):
            state["page_completed_at"] = max(
                state["page_completed_at"], time.time()
            )
            save_budget(file, state)


def _feed_text(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value or "")).split())


class AdapterError(Exception):

    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def utc_now():
    return (datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z"))


def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit((
            urllib.parse.urlsplit(match[0]).scheme,
            urllib.parse.urlsplit(match[0]).hostname or "",
            urllib.parse.urlsplit(match[0]).path,
            "",
            "",
        )),
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return " ".join(text.split())[:500]


def budget_path(host):
    key = hashlib.sha256((SOURCE_ID + "|" + host).encode()).hexdigest()[:24]
    return os.path.join(tempfile.gettempdir(), "youthopps-" + key + ".budget")


def fetch_source(url, min_interval=6, *, with_headers=False):
    original = urllib.parse.urlsplit(url)

    def checked(value):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                host != (original.hostname or "").lower().removeprefix("www.")
                or parsed.port != original.port):
            raise AdapterError("Source redirect leaves the reviewed HTTPS host",
                               "fetch")
        if host == "localhost" or host.endswith(".localhost"):
            raise AdapterError("Public HTTPS source required", "fetch")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise AdapterError("Public HTTPS source required", "fetch")
        except ValueError:
            pass

    for attempt in range(3):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(current,
                                                      interval=min_interval,
                                                      robots=(original.path == "/robots.txt" and
                                                              urllib.parse.urlsplit(current).path == "/robots.txt"))
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch")
                    current = urllib.parse.urljoin(current, headers["Location"])
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status == 503 and attempt < 2:
                    with locked_budget(url) as (file, state):
                        state["until"] = max(state["until"], time.time() + 120)
                        save_budget(file, state)
                        retry_at = state["until"]
                    while retry_at > time.time():
                        time.sleep(min(retry_at - time.time(), 60))
                    break
                if status != 200:
                    raise AdapterError(f"Publisher returned HTTP {status}",
                                       ("robots" if original.path == "/robots.txt" and
                                        urllib.parse.urlsplit(current).path == "/robots.txt" else "fetch"), status)
                text = body.decode(headers.get_content_charset() or "utf-8")
                return (text, headers) if with_headers else text
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch") from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    if robots_url not in _ROBOTS_CACHE:
        try:
            _ROBOTS_CACHE[robots_url] = fetch_source(robots_url)
        except AdapterError as error:
            if (error.stage == "robots" and error.status is not None and
                    400 <= error.status < 500 and error.status != 429):
                _ROBOTS_CACHE[robots_url] = ""
            else:
                raise
    text = _ROBOTS_CACHE[robots_url]
    groups, group = [], {"agents": [], "rules": [], "delay": 0}
    for line in text.splitlines():
        key, separator, value = line.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
        elif key == "crawl-delay":
            try:
                group["delay"] = float(value)
                if not 0 <= group["delay"] <= 60:
                    raise ValueError()
            except ValueError:
                raise AdapterError("Unsupported robots crawl delay", "access")
    groups.append(group)
    directives = [
        line.split("#", 1)[0].strip().lower()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (directives and not any(g["agents"] for g in groups) and
            not all(line.startswith("sitemap:") for line in directives)):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    agent = _USER_AGENT.split("/", 1)[0].lower()
    specific = [g for g in groups if agent in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    policies = [policy for policy in (specific, legacy) if policy]
    if not policies:
        policies = [[g for g in groups if "*" in g["agents"]]]
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    for selected in policies:
        matches = []
        for g in selected:
            for directive, pattern in g["rules"]:
                if not pattern:
                    continue
                expression = ("^" + ".*".join(
                    re.escape(part)
                    for part in pattern.removesuffix("$").split("*")) +
                              ("$" if pattern.endswith("$") else ""))
                if re.search(expression, path):
                    matches.append((
                        len(pattern.replace("*", "").removesuffix("$")),
                        directive == "allow",
                    ))
        if matches and not max(matches)[1]:
            with locked_budget(url) as (file, state):
                state["blocked"] = True
                save_budget(file, state)
            raise AdapterError("Robots policy disallows this source endpoint",
                               "access")
    return max(
        [6] + [g["delay"] for policy in policies for g in policy]
    )


def make_record(
    title,
    url,
    categories,
    kind="opportunity",
    published_at=None,
    tags=None,
    host_countries=None,
    method="editorial-review",
    evidence=None,
):
    now = utc_now()
    return {
        "id": hashlib.sha256(f"{SOURCE_ID}|{url}".encode()).hexdigest()[:24],
        "title": title,
        "url": url,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": published_at,
        "summary": "",
        "tags": tags or [],
        "location": None,
        "deadline": None,
        "language": LANGUAGE,
        "category": categories[0],
        "categories": categories,
        "kind": kind,
        "host_countries": host_countries or [],
        "eligible_countries": [],
        "publisher_country": PUBLISHER_COUNTRY,
        "created_at": now,
        "updated_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "last_checked_at": now,
        "status": "unknown",
        "classification": {
            "method": method,
            "status": "unknown" if categories == ["other"] else "classified",
            "evidence": evidence or [],
        },
    }


def validate_records(records, *, allow_empty=False, allow_legacy_ids=False):
    """Validate fresh IDs; legacy native IDs are allowed only for old reads."""
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError("Empty or invalid opportunity collection",
                           "validate")
    ids = set()
    for record in records:
        if (not isinstance(record, dict) or record.get("source") != SOURCE_ID or
                record.get("source_url") != SOURCE_URL):
            raise AdapterError("Record source identity mismatch", "validate")
        for field in (
                "id",
                "title",
                "url",
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
        ):
            if (not isinstance(record.get(field), str) or
                    not record[field].strip()):
                raise AdapterError("Missing record field: " + field, "validate")
        pattern = re.escape(SOURCE_ID) + r"-[1-9][0-9]*"
        if allow_legacy_ids:
            pattern = re.escape(SOURCE_ID) + r"[-:][1-9][0-9]*"
        if not re.fullmatch(pattern, record["id"]):
            raise AdapterError("Invalid native opportunity identifier",
                               "validate")
        identity = record["id"].replace(SOURCE_ID + ":", SOURCE_ID + "-", 1)
        if identity in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(identity)
        url = urllib.parse.urlsplit(record["url"])
        if (url.scheme not in ("http", "https") or not url.hostname or
                url.username or url.password):
            raise AdapterError("Unsafe opportunity URL", "validate")
        for field in (
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
                "published_at",
                "deadline",
        ):
            value = record.get(field)
            if field not in record:
                raise AdapterError("Missing record field: " + field, "validate")
            if value is not None:
                try:
                    if (datetime.datetime.fromisoformat(
                            value.replace("Z", "+00:00")).tzinfo is None):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError("Invalid record timestamp: " + field,
                                       "validate")
        for field in ("tags", "host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values):
                raise AdapterError("Invalid record list: " + field, "validate")
            if field != "tags" and any(
                    not re.fullmatch("[A-Z]{2}", value) for value in values):
                raise AdapterError("Invalid country code", "validate")
        categories = record.get("categories")
        if (not isinstance(categories, list) or not categories or
                any(value not in CATEGORIES for value in categories) or
                len(set(categories)) != len(categories) or
                record.get("category") != categories[0]):
            raise AdapterError("Invalid opportunity categories", "validate")
        if record.get("kind") not in (
                "opportunity",
                "programme-overview",
                "institutional-grant",
                "unknown",
        ) or record.get("status") not in ("open", "expired", "unknown"):
            raise AdapterError("Invalid opportunity kind or status", "validate")
        if (not isinstance(record.get("summary"), str) or
                len(record["summary"]) > 600 or
                re.search("<[^>]+>", record["summary"])):
            raise AdapterError("Invalid plain summary", "validate")
        for field in ("location", "language", "publisher_country"):
            if (field not in record or record[field] is not None and
                    not isinstance(record[field], str)):
                raise AdapterError("Invalid record field: " + field, "validate")
        classification = record.get("classification")
        if (not isinstance(classification, dict) or
                not isinstance(classification.get("method"), str) or
                classification.get("status") not in ("classified", "unknown") or
                not isinstance(classification.get("evidence"), list) or
                any(not isinstance(value, str)
                    for value in classification["evidence"])):
            raise AdapterError("Invalid classification evidence", "validate")


_TAXONOMY = {
    "Scholarships": "scholarships",
    "Internships": "internships",
    "Volunteering": "volunteering",
    "Fellowship": "fellowships",
    "Short Courses": "training",
    "training": "training",
    "Competition": "competitions",
    "Awards": "competitions",
    "Grants": "grants",
    "Jobs": "jobs",
}


class Content(html.parser.HTMLParser):
    """Extract publisher content without scripts, styles or HTML markup."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.headings = []
        self.heading = None
        self.blocks = []
        self.block_stack = []
        self.skip = 0
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "strong"):
            self.block_stack.append({"tag": tag, "parts": [], "links": []})
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("h2", "h3", "h4") and not self.skip:
            self.heading = []
        if tag == "a" and not self.skip:
            href = dict(attrs).get("href", "")
            if href:
                try:
                    href = urllib.parse.urljoin(WEBSITE_URL, href)
                    urllib.parse.urlsplit(href).hostname
                except ValueError:
                    return
                self.links.append(href)
                for block in self.block_stack:
                    block["links"].append(href)

    def handle_endtag(self, tag):
        if self.block_stack and self.block_stack[-1]["tag"] == tag:
            block = self.block_stack.pop()
            text = " ".join(" ".join(block["parts"]).split())
            if text:
                self.blocks.append((block["tag"], text, block["links"]))
        if tag in ("h2", "h3", "h4") and self.heading is not None:
            self.headings.append(" ".join(self.heading))
            self.heading = None
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)
            for block in self.block_stack:
                block["parts"].append(value)
            if self.heading is not None:
                self.heading.append(value)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


def read_api(url):
    delay = max(12, check_robots(url))
    text, headers = fetch_source(url, delay, with_headers=True)
    try:
        entries = json.loads(text)
        total = int(headers["X-WP-Total"])
        pages = int(headers["X-WP-TotalPages"])
        if not isinstance(entries, list) or total < 0 or pages < 0:
            raise ValueError()
    except (ValueError, TypeError, KeyError) as error:
        raise AdapterError("Invalid WordPress inventory response") from error
    return entries, total, pages


class CanonicalContent(Content):
    """Read the identified article body, excluding menus and sharing widgets."""

    def __init__(self, document, post_id):
        self.post_id = post_id
        self.in_article = False
        self.depth = 0
        self.sharing_depth = 0
        self.newsletter = False
        super().__init__(document)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "article":
            self.in_article = attributes.get("id") == f"post-{self.post_id}"
        if not self.in_article:
            return
        if tag == "div":
            classes = attributes.get("class", "").split()
            if self.depth:
                self.depth += 1
            elif "entry-content" in classes:
                self.depth = 1
            if self.sharing_depth:
                self.sharing_depth += 1
            elif self.depth and any(
                    value in classes
                    for value in ("the_champ_sharing_container", "sharedaddy",
                                  "jp-relatedposts")):
                self.sharing_depth = 1
        if self.depth and not self.sharing_depth and not self.newsletter:
            super().handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self.depth and not self.sharing_depth and not self.newsletter:
            super().handle_endtag(tag)
        if tag == "div":
            self.sharing_depth = max(0, self.sharing_depth - 1)
            self.depth = max(0, self.depth - 1)
        if tag == "article":
            self.in_article = False

    def handle_data(self, value):
        if value.strip().startswith(
                "Discover more from Opportunities for Youth"):
            self.newsletter = True
        if self.depth and not self.sharing_depth and not self.newsletter:
            super().handle_data(value)


_CALL_WORDS = (
    r"\b(?:scholarships?|fellowships?|internships?|opportunities|jobs?|"
    r"grants?|prizes?|awards?|courses?|competitions?|challenges?|"
    r"universit(?:y|ies)|colleges?|programmes?|programs?|masters?|PhD|"
    r"assistantships?|ambassadors?|officers?|traineeships?)\b")
_PARTICIPATION = (
    r"\b(?:apply|application|applicants|eligibility|eligible|deadline|"
    r"register|registration|enrol|enroll|enrolment|enrollment|submit|submitted|"
    r"submissions?|proposal|nominate|nomination|nominations|due date|"
    r"sign[- ]up|start learning|self[- ]paced|"
    r"fill out (?:this |the |a )?form|"
    r"join (?:the |a )?(?:course|programme|program|event)|"
    r"rsvp|participate|participation|(?:secure|book) (?:your |a )?spot)\b")


def material_roundup(title, content):
    """Require collection framing and distinct linked calls in content."""
    explicit_list = re.search(
        r"\b(?:(?:list|roundup|round-up) of|database|directory)\b",
        title,
        re.I,
    )
    numbered_list = re.search(
        r"^\W*(?:top\s+)?\d{1,3}\b(?![,\d]).{0,80}"
        r"\b(?:scholarships|opportunities|internships|fellowships|jobs|"
        r"grants|courses|awards|competitions)\b",
        title,
        re.I,
    ) and not re.search(
        r"\b(?:programme|program|scheme)\b",
        title,
        re.I,
    ) and not re.search(
        r"\b(?:the|this|a)\s+[^.!]{0,80}"
        r"\b(?:programme|program|scheme|fellowship|scholarship)\b",
        content.text[:400],
        re.I,
    )
    collection_title = explicit_list or numbered_list
    collection_intro = re.search(
        r"\b(?:curated (?:a )?(?:list|selection) of.{0,100} |"
        r"edition of Opportunities for Youth|explore the options below|"
        r"following (?:scholarships|opportunities|internships|fellowships))\b",
        content.text[:1000],
        re.I,
    )
    references = set()
    for link in content.links:
        parsed = urllib.parse.urlsplit(link)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if parsed.hostname == "opportunitiesforyouth.org" and (
                parsed.path == "/" or parsed.path.startswith("/category/")):
            continue
        query = urllib.parse.urlencode([
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query)
            if not key.startswith("utm_") and key != "fbclid"
        ])
        references.add(
            urllib.parse.urlunsplit((
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                query,
                "",
            )))
    named = set()
    labelled_calls = set()
    for tag, text, links in content.blocks:
        has_call_word = re.search(_CALL_WORDS, text[:200], re.I)
        if not has_call_word and not links:
            continue
        if re.search(
                r"^(?:scholarship|fellowship|internship|programme|program)?\s*"
                r"(?:benefits|eligibility|requirements|application|deadline|"
                r"how to|selection|funding|overview|coverage|timeline)\b",
                text,
                re.I,
        ):
            continue
        key = text.casefold()[:100]
        if len(text) <= 350 and (links or re.match(r"^[✅👉]", text)):
            named.add(key)
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "strong") and (
                has_call_word and len(text) <= 150) and not re.search(
                    r"\b(?:about|benefits|eligibility|coverage|"
                    r"application|deadline|"
                    r"requirements|future|dedicated|selection|overview)\b",
                    text,
                    re.I,
                ):
            labelled_calls.add(key)
    framed = collection_title or collection_intro
    article_entries = {}
    sections = []
    active = None
    for tag, text, links in content.blocks:
        specific_heading = (
            tag in ("h1", "h2", "h3", "h4", "h5", "h6") and len(text) <= 150 and
            re.search(_CALL_WORDS, text, re.I) and not re.search(
                r"\b(?:about|benefits|eligibility|coverage|"
                r"application|deadline|"
                r"requirements|future|dedicated|selection|overview|timeline|"
                r"sessions|stages|rounds|schedule|intakes)\b",
                text,
                re.I,
            ))
        if specific_heading:
            active = {"name": text.casefold(), "links": set(), "closing": False}
            sections.append(active)
        if active is not None:
            active["links"].update(links)
            if re.match(r"^deadlines?\s*:", text, re.I):
                active["closing"] = True
        if (len(text) <= 350 and re.search(_CALL_WORDS, text, re.I) and
                re.search(r"\bapply (?:here|now)\b", text, re.I) and
                0 <= content.text.find(text) < 800):
            for link in links:
                parsed = urllib.parse.urlsplit(link)
                if (parsed.hostname == "opportunitiesforyouth.org" and
                        re.match(r"^/\d{4}/\d{2}/\d{2}/", parsed.path)):
                    article_entries.setdefault(parsed.path,
                                               text.casefold()[:100])
    closed_sections = [
        section for section in sections
        if section["closing"] and section["links"]
    ]
    distinct_sections = re.search(
        r"\b(?:scholarships|opportunities|internships|fellowships|jobs|grants|"
        r"courses|awards|competitions)\b",
        title,
        re.I,
    ) and any(left["name"] != right["name"] and
              left["links"].isdisjoint(right["links"])
              for index, left in enumerate(closed_sections)
              for right in closed_sections[index + 1:])
    return bool((framed and len(references) >= 3) or
                (framed and len(named) >= 2 and len(references) >= 2) or
                (explicit_list and len(labelled_calls) >= 3) or
                len(set(article_entries.values())) >= 2 or distinct_sections)


def exclusion_reason(title, content):
    """Return an evidenced exclusion without requiring outbound links."""
    if not title or not content.text:
        return "no-publisher-opportunity-content"
    guidance = re.search(
        r"\b(?:tips?|how to (?:write|structure|prepare)|"
        r"steps to a winning|winning research proposal)\b",
        title,
        re.I,
    )
    actual_call = re.search(
        r"\b(?:applications? (?:are |is )?(?:now )?open|apply (?:now|here)|"
        r"who can apply|eligibility criteria)\b|"
        r"\bapplication deadline\s*:",
        content.text,
        re.I,
    )
    if guidance and not actual_call:
        return "editorial-guidance"
    if material_roundup(title, content):
        return "multi-opportunity-roundup"
    if not re.search(_PARTICIPATION, content.text, re.I):
        return "no-application-or-participation-evidence"
    return None


def collect_inventory():
    """Traverse every public post, validating pagination before filtering."""
    _ROBOTS_CACHE.clear()
    root = WEBSITE_URL + "wp-json/wp/v2/"
    taxonomy = {}
    category_page = 1
    while True:
        entries, total, pages = fetch_inventory_page(
            read_api, root + f"categories?per_page=100&page={category_page}")
        for entry in entries:
            if entry["id"] in taxonomy:
                raise AdapterError("Duplicate publisher category")
            taxonomy[entry["id"]] = html.unescape(entry["name"])
        if category_page >= pages:
            if len(taxonomy) != total:
                raise AdapterError("Incomplete category inventory")
            break
        category_page += 1
    query = urllib.parse.urlencode({
        "per_page": 100,
        "orderby": "id",
        "order": "asc",
        "_fields": "id,date_gmt,link,title,content,categories",
    })
    seen = set()
    records = []
    exclusions = {}
    expected_total = expected_pages = None
    page = 1
    while True:
        entries, total, pages = fetch_inventory_page(
            read_api, SOURCE_URL + "?" + query + f"&page={page}")
        if expected_total is None:
            expected_total, expected_pages = total, pages
        if (total, pages) != (expected_total, expected_pages):
            raise AdapterError("Publisher inventory changed during pagination")
        if len(entries) != min(100, total - (page - 1) * 100):
            raise AdapterError("Missing publisher inventory page entries")
        for entry in entries:
            post_id = entry["id"]
            if not isinstance(post_id, int) or post_id in seen:
                raise AdapterError(
                    "Invalid or duplicate publisher post identifier")
            seen.add(post_id)
            url = entry["link"]
            parsed = urllib.parse.urlsplit(url)
            if (parsed.scheme != "https" or
                    parsed.hostname != "opportunitiesforyouth.org"):
                raise AdapterError("Unexpected publisher article URL")
            title = _feed_text(entry["title"]["rendered"])
            content = Content(entry["content"]["rendered"])
            if not content.text:
                content = CanonicalContent(
                    fetch_source(url, min_interval=max(12, check_robots(url))),
                    post_id,
                )
            try:
                tags = [taxonomy[value] for value in entry["categories"]]
            except KeyError as error:
                raise AdapterError("Unknown publisher category") from error
            reason = exclusion_reason(title, content)
            if reason:
                if exclusions.get(reason, 0) < 3:
                    print(json.dumps({
                        "excluded_reason": reason,
                        "id": post_id,
                        "title": title,
                        "url": url,
                        "tags": tags,
                        "content_sample": content.text[:700]
                    }),
                          file=sys.stderr)
                exclusions[reason] = exclusions.get(reason, 0) + 1
                continue
            categories = list(
                dict.fromkeys(
                    _TAXONOMY[tag] for tag in tags if tag in _TAXONOMY))
            if any(tag in ("Bachelor", "Master", "PhD")
                   for tag in tags) and re.search(
                       r"\b(?:scholarship|studentship|fully funded|"
                       r"tuition waiver)\b",
                       content.text,
                       re.I,
                   ) and "scholarships" not in categories:
                categories.append("scholarships")
            categories = categories or ["other"]
            published = entry["date_gmt"]
            try:
                date = datetime.datetime.fromisoformat(published)
                if date.tzinfo is not None:
                    raise ValueError()
                published = date.replace(
                    tzinfo=datetime.timezone.utc).isoformat(
                        timespec="milliseconds").replace("+00:00", "Z")
            except (ValueError, TypeError) as error:
                raise AdapterError(
                    "Invalid publisher GMT publication date") from error
            record = make_record(
                title,
                url,
                categories,
                published_at=published,
                tags=tags,
                method="publisher-taxonomy-and-full-content",
                evidence=[url],
            )
            deadline_label = re.search(
                r"\b(?:(?:application|submission)\s+)?deadline\s*[:–-]?\s*"
                r"(?:\d{4}-\d{2}-\d{2}|"
                r"\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|"
                r"May|June|July|August|September|October|November|December)"
                r"\s*,?\s*\d{4}|"
                r"(?:January|February|March|April|May|June|July|August|"
                r"September|October|November|December)\s+"
                r"\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4})\b",
                content.text,
                re.I,
            )
            if deadline_label:
                record["summary"] = "Publisher-stated " + deadline_label[0]
            record["id"] = SOURCE_ID + "-" + str(post_id)
            record["classification"]["status"] = "classified"
            records.append(record)
        print(f"Archive page {page}/{pages}: {len(seen)}/{total} posts",
              file=sys.stderr)
        if page >= pages:
            break
        page += 1
    if len(seen) != expected_total:
        raise AdapterError("Incomplete publisher post archive")
    print(json.dumps({
        "inventory_posts": len(seen),
        "pages": expected_pages,
        "retained": len(records),
        "exclusions": exclusions,
        "pagination_order": "ascending publisher post ID"
    }),
          file=sys.stderr)
    validate_records(records)
    return records


def github(method, path, payload=None, missing_ok=False):
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError("DATA_SOURCE_TOKEN is required for publication",
                           "publish")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, _, body = request_bytes(
            _GITHUB + path,
            method=method,
            headers=headers,
            payload=(json.dumps(payload).encode()
                     if payload is not None else None),
            max_bytes=(_MAX_GITHUB_BLOB_BYTES if method == "GET" and
                       re.fullmatch(r"/git/blobs/[0-9a-f]{40}", path) else
                       5_000_000),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; publication is unconfirmed",
            "publish") from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication API returned HTTP {status}",
                           "publish", status)
    return json.loads(body)


def snapshot_file(file, name):
    """Verify a source file, using its immutable blob when necessary."""
    if file is None:
        return None
    expected_path = f"datas/{SOURCE_ID}/{name}"
    size, sha = file.get("size"), file.get("sha")
    if (file.get("type") != "file" or file.get("path") != expected_path or
            not isinstance(size, int) or isinstance(size, bool) or
            not 0 <= size <= _MAX_SNAPSHOT_BYTES or not isinstance(sha, str) or
            not re.fullmatch(r"[0-9a-f]{40}", sha)):
        raise AdapterError("Invalid existing source file identity or size",
                           "publish")
    if file.get("encoding") == "none" and file.get("content") == "":
        file = github("GET", "/git/blobs/" + sha)
        if file.get("sha") != sha or file.get("size") != size:
            raise AdapterError("Existing source blob identity or size mismatch",
                               "publish")
    if file.get("encoding") != "base64" or not isinstance(
            file.get("content"), str):
        raise AdapterError("Unsupported existing source file encoding",
                           "publish")
    try:
        raw = base64.b64decode("".join(file["content"].split()), validate=True)
        digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw)
        if len(raw) != size or digest.hexdigest() != sha:
            raise ValueError()
        return raw.decode("utf-8")
    except (binascii.Error, UnicodeError, ValueError) as error:
        raise AdapterError("Existing source blob content failed validation",
                           "publish") from error


def read_snapshot():
    head = github("GET", "/git/ref/heads/main")["object"]["sha"]
    tree = github("GET", "/git/commits/" + head)["tree"]["sha"]
    files = {}
    for name in ("data.json", "metadata.json"):
        file = github(
            "GET",
            f"/contents/datas/{SOURCE_ID}/{name}?ref={head}",
            missing_ok=True,
        )
        files[name] = snapshot_file(file, name)
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish")
    return {"head": head, "tree": tree, "files": files}


def publish_files(snapshot, files):
    for text in files.values():
        if (not isinstance(text, str) or
                len(text.encode("utf-8")) > _MAX_SNAPSHOT_BYTES):
            raise AdapterError("Source publication file exceeds byte limit",
                               "publish")
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        candidate = None
        try:
            tree = github(
                "POST",
                "/git/trees",
                {
                    "base_tree":
                        snapshot["tree"],
                    "tree": [{
                        "path": f"datas/{SOURCE_ID}/{name}",
                        "mode": "100644",
                        "type": "blob",
                        "content": text,
                    } for name, text in files.items()],
                },
            )["sha"]
            candidate = github(
                "POST",
                "/git/commits",
                {
                    "message": f"Update {SOURCE_ID} collection outcome",
                    "tree": tree,
                    "parents": [snapshot["head"]],
                },
            )["sha"]
            github(
                "PATCH",
                "/git/refs/heads/main",
                {
                    "sha": candidate,
                    "force": False
                },
            )
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (latest["head"] == candidate or
                              latest["files"] == desired):
                return
            if latest["files"] != expected:
                raise AdapterError(
                    "Source changed concurrently; newer remote data "
                    "was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; durable remote outcome "
                    "could not be updated",
                    "publish",
                )


def metadata(attempt, previous, records, error=None):
    result = {
        "display_name": "Opportunities for Youth",
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at":
            (previous.get("last_success_at") if error else attempt),
        "last_checked_at":
            (previous.get("last_checked_at") if error else attempt),
        "record_count": len(records),
        "inventory_scope": "complete-public-wordpress-post-archive",
        "message": ("Collection failed; last-good data preserved" if error else
                    f"Validated refresh retains {len(records)} records"),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def remove_expired_records(records, checked_at):
    """Filter verified deadlines after validating the complete fresh inventory.

    Unzoned closing dates use UTC-12 as a conservative world-wide boundary;
    no publisher timezone is inferred. Explicit offsets retain their instant.
    """
    validate_records(records)
    checked = datetime.datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Cleanup requires an aware check timestamp",
                           "validate")
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing = datetime.datetime.fromisoformat(deadline).date()
            latest_date = checked.astimezone(
                datetime.timezone(datetime.timedelta(hours=-12))).date()
            expired = latest_date > closing
        elif deadline:
            closing = datetime.datetime.fromisoformat(
                deadline.replace("Z", "+00:00"))
            if closing.tzinfo is not None:
                expired = checked > closing
        if not expired:
            retained.append(record)
    validate_records(retained, allow_empty=True)
    return retained


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source "
        "using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    prepare_pacing()
    snapshot = read_snapshot() if args.publish else None
    old = (json.loads(snapshot["files"]["data.json"])
           if snapshot and snapshot["files"]["data.json"] is not None else [])
    previous = (json.loads(snapshot["files"]["metadata.json"]) if snapshot and
                snapshot["files"]["metadata.json"] is not None else {})
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, allow_empty=True, allow_legacy_ids=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish")
    attempt, failure = utc_now(), None
    try:
        records = collect()
        validate_records(records)
        records = remove_expired_records(records, utc_now())
        prior = {
            record["id"].replace(SOURCE_ID + ":", SOURCE_ID + "-", 1): record
            for record in old
        }
        temporal = {
            "created_at",
            "updated_at",
            "first_seen_at",
            "last_seen_at",
            "last_checked_at",
        }
        for record in records:
            before = prior.get(record["id"], {})
            same = {
                key: value
                for key, value in before.items()
                if key not in temporal
            } == {
                key: value
                for key, value in record.items()
                if key not in temporal
            }
            record.update(
                created_at=before.get("created_at", attempt),
                first_seen_at=before.get("first_seen_at", attempt),
                updated_at=(before.get("updated_at", attempt)
                            if same else attempt),
                last_seen_at=attempt,
                last_checked_at=attempt,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(key=lambda record: record["published_at"] or "",
                     reverse=True)
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
    if args.publish and (not failure or
                         snapshot["files"]["data.json"] is not None):
        files = {
            "metadata.json":
                json.dumps(outcome, ensure_ascii=False, indent=2) + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n")
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(
                attempt,
                previous,
                old,
                AdapterError("Publication failed: " + safe_error(error),
                             "publish"),
            )
            try:
                latest = read_snapshot()
                if (snapshot["files"]["data.json"] is None or
                        latest["files"] != snapshot["files"]):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files(
                    latest,
                    {
                        "metadata.json":
                            json.dumps(outcome, ensure_ascii=False, indent=2) +
                            "\n"
                    },
                )
            except Exception:
                outcome["message"] = (
                    "Publication failed; durable failure status could not be "
                    "recorded. Remote data was not force-overwritten.")
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created")
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


def main():
    try:
        return run()
    finally:
        try:
            export_pacing()
        finally:
            release_collection()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "source": SOURCE_ID,
                    "status": "fail",
                    "last_attempt_at": utc_now(),
                    "message": "Run could not complete; "
                               "durable status unavailable",
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            ))
        sys.exit(1)
