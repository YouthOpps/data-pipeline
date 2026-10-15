"""Discover current Study in Czechia programmes from official pages."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import sys
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "cz-study-in-czechia"
SOURCE_URL = "https://studyin.gov.cz/scholarships/"
WEBSITE_URL = "https://studyin.gov.cz/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "CZ"
PUBLISHER_TYPE = "government"
ATTRIBUTION = (
    "Study in Czechia — Czech National Agency for International Education "
    "and Research (DZS); official programme portal"
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {
    "scholarships", "internships", "volunteering", "training", "jobs",
    "competitions", "grants", "fellowships", "other",
}
FAMILY = "youthopps-dzs-publisher-v1"
FAMILY_SOURCES = {"cz-study-in-czechia", "cz-dzs"}
COLLECTION_TIMEOUT = 10800

_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            wait = _PAGE_COMPLETED + 12 - time.monotonic()
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        parts = []
        for child in self.children:
            parts.append(child.text() if isinstance(child, Node) else child)
        return " ".join(" ".join(parts).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

class PublisherHTML(HTMLParser):
    """Small structural parser retaining the publisher's content boundaries."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in (
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
        ):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)

def nodes(node, tag=None, class_name=None):
    return [
        n
        for n in node.walk()
        if (tag is None or n.tag == tag)
        and (
            class_name is None or class_name in n.attrs.get("class", "").split()
        )
    ]

def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]

class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None

def utc_now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit(
            (
                urllib.parse.urlsplit(match[0]).scheme,
                urllib.parse.urlsplit(match[0]).hostname or "",
                urllib.parse.urlsplit(match[0]).path,
                "",
                "",
            )
        ),
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return " ".join(text.split())[:500]

_OPENER = urllib.request.build_opener(NoRedirect)
_ROBOTS = {}
_RESTORED = False
_COLLECTION_STARTED = None

_STATE_SCHEMA = 1
_STATE_PATH = None
_STATE_LOCK = None
_PUBLISHING = False
_STATE_TRUSTED = False
_COLLECTION_LOCK_FD = None
_ARTIFACT_NAME = "dzs-pacing-state"


def numeric(value):
    try:
        return (
            type(value) in (int, float)
            and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) != {
        "schema", "family", "observed_at", "not_before", "starts", "blocked",
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
    if (type(state["schema"]) is not int or state["schema"] != _STATE_SCHEMA
            or state["family"] != FAMILY
            or not numeric(state["observed_at"])
            or not numeric(state["not_before"])
            or type(state["blocked"]) is not bool
            or not isinstance(state["starts"], list)
            or len(state["starts"]) > 10
            or any(not numeric(t) for t in state["starts"])
            or state["starts"] != sorted(state["starts"])
            or len(set(state["starts"])) != len(state["starts"])
            or any(t > state["observed_at"] for t in state["starts"])):
        raise AdapterError("Invalid publisher pacing history", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA, "family": FAMILY, "observed_at": now,
        "not_before": now, "starts": [], "blocked": False,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK
    if _STATE_PATH:
        return
    directory = os.path.join(tempfile.gettempdir(), FAMILY + (
        "-"
    ) + str(os.getuid()))
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AdapterError("Unsafe publisher pacing directory", "access")
    if info.st_mode & 0o077:
        raise AdapterError("Unsafe publisher pacing permissions", "access")
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")


def locked_budget():
    budget_file()
    descriptor = os.open(
        _STATE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    info = os.fstat(descriptor)
    if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077 or info.st_nlink != 1):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher pacing lock", "access")
    file = os.fdopen(descriptor, "a+")
    fcntl.flock(file, fcntl.LOCK_EX)
    return file


def load_budget():
    try:
        fd = os.open(_STATE_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return empty_budget(time.time())
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise AdapterError("Unsafe publisher pacing state owner", "access")
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher pacing state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher pacing state", "access") from None


def save_budget(state):
    validate_budget(state)
    fd, name = tempfile.mkstemp(prefix=(
        "pending-"
    ), dir=os.path.dirname(_STATE_PATH))
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, _STATE_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def pace():
    (
        "Serialize every publisher request start across the complete "
        "origin set."
    )
    with locked_budget():
        state = load_budget()
        if state["blocked"]:
            raise AdapterError((
                "Publisher backoff requires reviewed recovery"
            ), (
                "access"
            ))
        now = time.time()
        starts = [t for t in state["starts"] if t > now - 60]
        target = max(now, state["not_before"])
        if starts:
            target = max(target, starts[-1] + 6)
        if len(starts) == 10:
            target = max(target, starts[0] + 60)
        if _COLLECTION_STARTED is not None and (
            target + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError((
                "Publisher backoff exceeds collection budget"
            ), (
                "access"
            ))
        if target > now:
            time.sleep(target - now)
        now = time.time()
        if now + 0.001 < target:
            raise AdapterError((
                "Publisher pacing clock moved backwards"
            ), (
                "access"
            ))
        state["starts"] = [t for t in starts if t > now - 60] + [now]
        state["observed_at"] = now
        save_budget(state)


def record_retry_after(value):
    """Persist the embargo before reporting any refusal or retrieval failure."""
    if value is None:
        return
    with locked_budget():
        state = load_budget()
        now = time.time()
        try:
            if re.fullmatch(r"[0-9]+", value.strip()):
                until = now + int(value)
            else:
                parsed = parsedate_to_datetime(value)
                if parsed.utcoffset() is None:
                    raise ValueError("Missing timezone")
                until = parsed.timestamp()
            if not numeric(until):
                raise ValueError("Invalid embargo")
            state["not_before"] = max(state["not_before"], until)
        except Exception:
            state["blocked"] = True
        state["observed_at"] = now
        save_budget(state)


def request_bytes(url, method="GET", headers=None, payload=None,
                  publisher=False, byte_limit=30000000):
    """Bound response bytes; redirects are explicit and credential-safe."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        if publisher:
            public_url(current)
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        try:
            response = _OPENER.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status
            response_headers = response.headers
            retry = response.headers.get("Retry-After")
            if publisher:
                record_retry_after(retry)
            declared = response.headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) > byte_limit
            ):
                raise AdapterError((
                    "Oversized or invalid response length"
                ), (
                    "fetch"
                ))
            body = response.read(byte_limit + 1)
            if (len(body) > byte_limit
                    or declared and len(body) != int(declared)):
                raise AdapterError("Incomplete or oversized response", "fetch")
            encoding = response.headers.get("Content-Encoding", "identity")
            if encoding != "identity":
                raise AdapterError((
                    "Unsupported response content encoding"
                ), (
                    "fetch"
                ))
            location = response.headers.get("Location")
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            old, new = (
                urllib.parse.urlsplit(current),
                urllib.parse.urlsplit(destination),
            )
            if publisher:
                public_url(destination)
                if old.scheme == "https" and new.scheme != "https":
                    raise AdapterError((
                        "Publisher redirect transport downgrade"
                    ), (
                        "access"
                    ))
                check_robots(destination)
            else:
                if new.scheme != "https" or new.username or new.password:
                    raise AdapterError((
                        "Unsafe authenticated redirect"
                    ), (
                        "publish"
                    ))
                if new.netloc != old.netloc:
                    headers = {key: value for key, value in headers.items()
                               if key.lower() != "authorization"}
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    raise AdapterError((
                        "Unexpected GitHub API redirect"
                    ), (
                        "publish"
                    ))
            current = destination
            continue
        if publisher and status in (401, 403, 429):
            refused = urllib.parse.urlsplit(current)
            provenance = urllib.parse.urlunsplit((
                refused.scheme, refused.hostname or "", refused.path, "", ""))
            raise AdapterError(
                f"Publisher refused access: HTTP {status}: " + provenance,
                "access", status)
        return status, response_headers, body
    raise AdapterError("Unresolved redirect", "access")


def robots_directive(parser, directive, url=None):
    """Retain native policy and every applicable product group's restriction."""
    import copy

    policies = [parser]
    for entry in parser.entries:
        if entry.applies_to(_USER_AGENT):
            policy = copy.copy(parser)
            policy.entries = [entry]
            policy.default_entry = None
            policies.append(policy)
    if directive == "can_fetch":
        return all(policy.can_fetch(_USER_AGENT, url) for policy in policies)
    values = [
        getattr(policy, directive)(_USER_AGENT) for policy in policies
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    if directive == "crawl_delay":
        return max(values)
    if any(rate.requests <= 0 for rate in values):
        raise AdapterError("Invalid robots request rate", "access")
    return max(values, key=lambda rate: rate.seconds / rate.requests)


def check_robots(url):
    import urllib.robotparser
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.path == "/robots.txt":
        return
    if origin not in _ROBOTS:
        status, _, body = request_bytes(origin + "/robots.txt", publisher=True)
        if status == 404:
            _ROBOTS[origin] = None
        elif status == 200:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(body.decode("utf-8", "strict").splitlines())
            _ROBOTS[origin] = parser
        else:
            raise AdapterError((
                "Publisher robots policy unavailable"
            ), (
                "access"
            ), status)
    parser = _ROBOTS[origin]
    if parser is not None and not robots_directive(parser, "can_fetch", url):
        raise AdapterError("Publisher robots excludes required input", "access")
    if parser is not None:
        delay = robots_directive(parser, "crawl_delay")
        if delay and delay > 6:
            with locked_budget():
                state = load_budget()
                if state["starts"]:
                    state[(
                        "not_before"
                    )] = max(state[(
                        "not_before"
                    )], state[(
                        "starts"
                    )][-1] + delay)
                    save_budget(state)
        rate = robots_directive(parser, "request_rate")
        if rate and rate.seconds / rate.requests > 6:
            raise AdapterError((
                "Stricter publisher request-rate requires review"
            ), (
                "access"
            ))


def fetch_public(url):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(reviewed, publisher=True)
        if status == 200:
            return status, headers, body
        if status == 503 and attempt == 0:
            with locked_budget():
                state = load_budget()
                now = time.time()
                state["not_before"] = max(state["not_before"], now + 30)
                state["observed_at"] = now
                save_budget(state)
            continue
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise AdapterError(
            f"Required publisher input returned HTTP {status}: "
            + provenance, "fetch", status)


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json"},
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError((
            "Workflow pacing state unavailable"
        ), (
            "access"
        )) from None


def current_run_identity():
    if (os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [os.environ.get(key, "") for key in ((
        "GITHUB_RUN_ID"
    ), (
        "GITHUB_RUN_ATTEMPT"
    ))]
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))


def family_source(run):
    path = run.get("path", "")
    for source in FAMILY_SOURCES:
        if path == ".github/workflows/fetch-" + source + ".yml":
            return source
    return None


def completed_identity(run, expected=None):
    """Bind a completed attempt and its chronological publisher interval."""
    try:
        source = family_source(run)
        identity, attempt = run["id"], run["run_attempt"]
        if (not source or type(identity) is not int or identity <= 0
                or type(attempt) is not int or attempt <= 0
                or run["status"] != "completed"
                or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
                or run[(
                    "head_repository"
                )][(
                    "full_name"
                )] != (
                    "YouthOpps/data-pipeline"
                )
                or run["head_branch"] != "main"
                or expected and (identity, attempt, source) != expected):
            raise ValueError("Untrusted completed attempt")
        times = []
        for field in ("run_started_at", "updated_at"):
            value = datetime.fromisoformat(run[field].replace("Z", "+00:00"))
            if value.utcoffset() is None:
                raise ValueError("Missing attempt timezone")
            times.append(value.timestamp())
        began, finished = times
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        return finished, began, identity, attempt, source
    except Exception:
        raise AdapterError((
            "Untrusted completed family attempt"
        ), (
            "access"
        )) from None


class InventoryRace(AdapterError):
    """A changed advertised total permits a bounded fresh history scan."""


def latest_family_run():
    """Restart only count-drift races, without reusing partial candidates."""
    for attempt in range(3):
        try:
            return scan_family_history()
        except InventoryRace:
            if attempt == 2:
                raise AdapterError(
                    "Workflow inventory remained unstable", "access") from None


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    current_id, current_attempt = current_run_identity()
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        result = workflow_api(f"/actions/runs?per_page=100&page={page}")
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (type(count) is not int or count < 0
                or not isinstance(runs, list) or len(runs) > 100):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace((
                "Workflow inventory changed during restore"
            ), (
                "access"
            ))
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            run_id = run.get("id")
            if (type(run_id) is not int or run_id <= 0
                    or run_id in seen_ids):
                raise AdapterError(
                    "Invalid or duplicate workflow run identity", "access")
            seen_ids.add(run_id)
            source = family_source(run)
            if not source:
                continue
            if (run.get((
                "repository"
            ), {}).get((
                "full_name"
            )) != (
                "YouthOpps/data-pipeline"
            )
                    or run.get("head_repository", {}).get("full_name")
                    != (
                        "YouthOpps/data-pipeline"
                    ) or run.get((
                        "head_branch"
                    )) != (
                        "main"
                    )):
                raise AdapterError((
                    "Workflow repository or branch mismatch"
                ), (
                    "access"
                ))
            identity, attempt = run.get("id"), run.get("run_attempt")
            if type(identity) is not int or type(attempt) is not int:
                raise AdapterError("Invalid family run identity", "access")
            if identity == current_id:
                if attempt != current_attempt:
                    raise AdapterError((
                        "Current workflow attempt mismatch"
                    ), (
                        "access"
                    ))
                if current_attempt > 1:
                    prior = workflow_api((
                        f"/actions/runs/{identity}/attempts/"
                        f"{current_attempt - 1}"
                    ))
                    candidates.append(completed_identity(
                        prior, (identity, current_attempt - 1, source)))
                continue
            if run.get((
                "status"
            )) in ((
                "queued"
            ), (
                "waiting"
            ), (
                "pending"
            ), (
                "requested"
            )):
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError((
                    "Workflow inventory count mismatch"
                ), (
                    "access"
                ))
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError((
                    "Ambiguous family attempt chronology"
                ), (
                    "access"
                ))
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def download_inert_artifact(identity, artifact_id, digest=None):
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    try:
        try:
            response = _OPENER.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != 302:
                raise ValueError("Expected artifact redirect")
            destination = response.headers.get("Location", "")
            response.read(1001)
        parsed = urllib.parse.urlsplit(destination)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.port):
            raise ValueError("Unsafe artifact URL")
        if ("." not in parsed.hostname
                or parsed.hostname.endswith(((
                    ".local"
                ), (
                    ".internal"
                ), (
                    ".localhost"
                )))):
            raise ValueError("Private artifact hostname")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Literal artifact address")
        # Signed URL is never stored or included in exceptions; no auth header.
        status, _, body = request_bytes(destination, byte_limit=32768)
        if status != 200 or len(body) > 32768:
            raise ValueError("Invalid artifact response")
        if digest is not None:
            if (not isinstance(digest, str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or digest[7:] != hashlib.sha256(body).hexdigest()):
                raise ValueError("Artifact digest mismatch")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "pacing-state.json":
                raise ValueError("Unexpected artifact members")
            entry = entries[0]
            if entry.file_size > 16384 or entry.flag_bits & 1:
                raise ValueError("Oversized or encrypted state")
            if entry.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("State symlink")
            raw = archive.read(entry)
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema", "repository", "run_id", "run_attempt", "source", "state",
        }:
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source = identity
        if (type(envelope["schema"]) is not int or envelope["schema"] != 1
                or envelope["repository"] != "YouthOpps/data-pipeline"
                or type(envelope["run_id"]) is not int
                or type(envelope["run_attempt"]) is not int
                or envelope["run_id"] != run_id
                or envelope["run_attempt"] != attempt
                or envelope["source"] != source):
            raise ValueError("Artifact ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError((
            "Invalid or unavailable family pacing artifact"
        ), (
            "access"
        )) from None


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raw = os.environ.get("DZS_PACING_BOOTSTRAP", "")
        try:
            bootstrap = json.loads(raw)
            if (set(bootstrap) != {"schema", "family", "not_before", "evidence"}
                    or type(bootstrap["schema"]) is not int
                    or bootstrap["schema"] != 1 or bootstrap["family"] != FAMILY
                    or not numeric(bootstrap["not_before"])
                    or not isinstance(bootstrap["evidence"], str)
                    or not re.fullmatch((
                        "https://github\\.com/YouthOpps/data-pipeline/(?:is"
                        "sues|pull)/[0-9]+(?:#issuecomment-[0-9]+)?"
                    ), bootstrap[(
                        "evidence"
                    )])):
                raise ValueError("Invalid bootstrap")
            incoming = empty_budget(time.time())
            incoming[(
                "not_before"
            )] = max(time.time() + 60, bootstrap[(
                "not_before"
            )])
        except Exception:
            raise AdapterError((
                "Reviewed first-family bootstrap required"
            ), (
                "access"
            )) from None
    else:
        run_id, attempt, source = latest
        expected = _ARTIFACT_NAME + "-" + str(attempt)
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (not isinstance(artifacts, list) or result.get((
            "total_count"
        )) != len(artifacts)
                or len(artifacts) > 100):
            raise AdapterError("Incomplete family artifact inventory", "access")
        matches = [a for a in artifacts if a.get("name") == expected]
        if len(matches) != 1 or matches[0].get("expired") is not False:
            raise AdapterError((
                "Newest family pacing artifact missing or expired"
            ), (
                "access"
            ))
        artifact = matches[0]
        if (type(artifact.get("id")) is not int or artifact["id"] <= 0
                or artifact.get("workflow_run", {}).get("id") != run_id
                or not numeric(artifact.get("size_in_bytes"))
                or artifact["size_in_bytes"] > 32768):
            raise AdapterError("Invalid family artifact binding", "access")
        incoming = download_inert_artifact(latest, artifact[(
            "id"
        )], artifact.get((
            "digest"
        )))
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined[(
            "not_before"
        )] = max(local[(
            "not_before"
        )], incoming[(
            "not_before"
        )], now + 60)
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined[(
            "starts"
        )] = sorted(set(local[(
            "starts"
        )] + incoming[(
            "starts"
        )]))[-10:]
        save_budget(combined)
    _RESTORED, _STATE_TRUSTED = True, True


def export_family_artifact():
    """Best-effort infrastructure state; never contains records or secrets."""
    path = os.environ.get("DZS_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
    run_id, attempt = current_run_identity()
    with locked_budget():
        state = load_budget()
    envelope = {
        "schema": 1, "repository": "YouthOpps/data-pipeline",
        "run_id": run_id, "run_attempt": attempt, "source": SOURCE_ID,
        "state": state,
    }
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError((
            "Explicit runner temporary directory required"
        ), (
            "access"
        ))
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as file:
        json.dump(envelope, file, separators=(",", ":"))


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    _COLLECTION_STARTED = time.time()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(os.path.dirname(_STATE_PATH), "collection.lock")
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            if os.fstat(descriptor).st_uid != os.getuid():
                raise OSError("Wrong lock owner")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise AdapterError((
                "Another local family collector is active"
            ), (
                "access"
            )) from None
        _COLLECTION_LOCK_FD = descriptor
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    _STATE_TRUSTED = True


def public_url(value):
    """Restrict collection to the official portal and public navigation."""
    parsed = urllib.parse.urlsplit(value)
    allowed = {"studyin.gov.cz", "studujnavs.gov.cz"}
    if (parsed.hostname not in allowed or parsed.username or parsed.password
            or parsed.port or parsed.scheme != "https"):
        raise AdapterError("Unreviewed public source origin", "access")
    if re.search((
        "/(?:ucet|login|register|account|prihlasit|registrovat)(?:/|$)"
    ),
                 parsed.path, re.I):
        raise AdapterError("Private application route is excluded", "access")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def document_root(text, complete=True):
    if complete and not re.search(
            r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$",
            text, re.I):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(r"just a moment|access denied|attention required|"
                r"checking your browser|verify you are human", title):
        raise AdapterError("Publisher access challenge", "access")
    return root


def read_public_page(url):
    """Read a complete official page or its advertised navigation fragment."""
    _, headers, body = fetch_public(url)
    try:
        text = body.decode(headers.get_content_charset() or "utf-8")
        if urllib.parse.urlsplit(url).path.startswith("/ajax/"):
            envelope = json.loads(text)
            if (envelope.get("version") != 1
                    or not isinstance(envelope.get("data"), list)
                    or len(envelope["data"]) != 1):
                raise ValueError()
            item = envelope["data"][0]
            if (item.get("targetBlock") != "#scholarshipSearchListContainer"
                    or item.get("type") != "html"
                    or not isinstance(item.get("content"), str)):
                raise ValueError()
            return document_root(item["content"], complete=False)
        return document_root(text)
    except (UnicodeError, LookupError, ValueError, TypeError, AttributeError):
        raise AdapterError("Invalid public page response", "parse") from None


def programme_id(url):
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parsed.query)
    if (parsed.hostname != "studyin.gov.cz"
            or parsed.path != "/en/scholarships/scholarship-detail/"
            or set(query) != {"id"} or len(query["id"]) != 1
            or not re.fullmatch(r"[1-9][0-9]*", query["id"][0])):
        raise AdapterError("Invalid programme identity", "parse")
    return int(query["id"][0])


def catalogue_fields(root, anchor):
    """Read labelled card facts without inferring countries from prose."""
    parents = {id(child): parent for parent in nodes(root)
               for child in parent.children if isinstance(child, Node)}
    card = anchor
    while id(card) in parents:
        if "group/scholarship" in card.attrs.get("class", "").split():
            break
        card = parents[id(card)]
    else:
        raise AdapterError("Programme card boundary missing", "parse")
    hosts = []
    fields = []
    for node in nodes(card):
        if "icon-marker" in node.attrs.get("class", "").split():
            value = parents[id(node)].text()
            if not re.fullmatch(r"[A-Z]{2}(?:\s*,\s*[A-Z]{2})*", value):
                raise AdapterError(
                    "Unrecognized catalogue host country", "parse"
                )
            hosts.extend(re.findall(r"[A-Z]{2}", value))
        if node.attrs.get("role") == "tooltip" and node.text() in (
            "Scholarship type", "Academic year", "Length of stay",
            "Field of study",
        ):
            label = node.text()
            parent = node
            while id(parent) in parents and parent is not card:
                parent = parents[id(parent)]
                text = parent.text()
                if text != label:
                    if not text.startswith(label) or len(text) > 500:
                        raise AdapterError("Invalid catalogue field", "parse")
                    fields.append(label + ":" + text[len(label):])
                    break
    statuses = {n.text() for n in nodes(card, "span")
                if n.text() in {"applications open", "Will open soon"}}
    return {"hosts": sorted(set(hosts)), "fields": fields,
            "application_status": sorted(statuses)}


def discover_programmes():
    """Follow public pagers and reconcile each scope's live advertised total."""
    inventory = {}
    for scope, query in (
        ("incoming", "outgoing=false"),
        ("outgoing", "outgoing=true"),
        ("compatriots", "compatriots=true"),
    ):
        url = SOURCE_URL + "?" + query
        page, total, seen = 1, None, set()
        while True:
            root = fetch_inventory_page(read_public_page, url)
            totals = re.findall(r"Results found:\s*(\d+)", root.text())
            empty = (
                "No scholarships found for the given criteria" in root.text()
            )
            if len(totals) == 1:
                current_total = int(totals[0])
            elif not totals and empty:
                current_total = 0
            else:
                raise AdapterError("Catalogue total missing", "parse")
            if total is None:
                total = current_total
            if current_total != total:
                raise AdapterError(
                    "Catalogue changed during pagination", "parse"
                )
            current = [n.text() for n in nodes(root)
                       if n.attrs.get("aria-current") == "page"
                       and n.text().isdigit()]
            if total and current != [str(page)]:
                raise AdapterError("Catalogue page mismatch", "parse")
            details = []
            for anchor in nodes(root, "a"):
                target = urllib.parse.urljoin(url, anchor.attrs.get("href", ""))
                if urllib.parse.urlsplit(target).path != (
                    "/en/scholarships/scholarship-detail/"
                ):
                    continue
                identity = programme_id(target)
                if identity in seen:
                    raise AdapterError("Duplicate catalogue programme", "parse")
                seen.add(identity)
                details.append(identity)
                facts = catalogue_fields(root, anchor)
                if identity in inventory and any(
                    inventory[identity][key] != facts[key] for key in facts
                ):
                    raise AdapterError("Conflicting catalogue cards", "parse")
                inventory.setdefault(
                    identity, {"url": target, "scopes": [], **facts}
                )
                inventory[identity]["scopes"].append(scope)
            if not details and total:
                raise AdapterError("Empty catalogue page", "parse")
            if len(seen) > total:
                raise AdapterError(
                    "Catalogue exceeds advertised total", "parse"
                )
            next_urls = set()
            for anchor in nodes(root, "a"):
                ajax = anchor.attrs.get("data-ajax", "")
                parsed = urllib.parse.urlsplit(ajax)
                if parsed.path != "/ajax/scholarshipsearch/switchpage":
                    continue
                params = urllib.parse.parse_qs(
                    parsed.query, keep_blank_values=True
                )
                outgoing = "false" if scope == "incoming" else "true"
                compatriots = "true" if scope == "compatriots" else "false"
                if (params.get("isOutgoing") != [outgoing]
                        or params.get("compatriots") != [compatriots]
                        or params.get("language") != ["en"]):
                    raise AdapterError(
                        "Catalogue navigation scope mismatch", "parse"
                    )
                if params.get("page") == [str(page + 1)]:
                    next_urls.add(public_url(urllib.parse.urljoin(url, ajax)))
                if len(seen) == total and any(
                    int(value) > page for value in params.get("page", [])
                ):
                    raise AdapterError(
                        "Unexpected terminal navigation", "parse"
                    )
            if len(seen) == total:
                break
            if len(next_urls) != 1:
                raise AdapterError("Catalogue next page missing", "parse")
            url = next_urls.pop()
            page += 1
        print(
            f"{scope}: {len(seen)} programmes across {page} pages",
            file=sys.stderr,
        )
    if not inventory:
        raise AdapterError("Empty programme inventory", "parse")
    return inventory


def portal_date(text):
    """Keep portal date-only calendars without guessing a closing timezone."""
    if not text.strip():
        return None
    match = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", text)
    if not match:
        raise AdapterError("Unrecognized portal call date", "parse")
    try:
        return datetime(
            int(match[3]), int(match[2]), int(match[1])
        ).date().isoformat()
    except ValueError:
        raise AdapterError("Invalid portal call date", "parse") from None


def programme_calls(root, identity, language="en"):
    """Read all advertised annual calendars, including historical calls."""
    if language == "cs":
        expected = [
            "ID výzvy", "Název", "Akademický rok", "Žádat od", "Žádat do"
        ]
        origin = "https://studujnavs.gov.cz/"
        path = "/prehled-stipendii/detail-stipendia/detail-vyzvy/"
    else:
        expected = [
            "Call ID", "Name", "Academic year", "Submit from", "Submit to"
        ]
        origin = WEBSITE_URL
        path = "/en/scholarships/scholarship-detail/call-detail/"
    calls = {}
    for table in nodes(root, "table"):
        headers = [n.text() for n in nodes(table, "th")]
        if expected[0] not in headers:
            continue
        if headers not in (expected, expected + [""]):
            raise AdapterError("Call table columns changed", "parse")
        for row in nodes(table, "tr"):
            cells = nodes(row, "td")
            if not cells:
                continue
            if len(cells) < 5 or not cells[0].text().isdigit():
                raise AdapterError("Malformed call row", "parse")
            call_id = int(cells[0].text())
            anchors = nodes(cells[0], "a")
            link = urllib.parse.urljoin(
                origin, one(anchors, "call link").attrs.get("href", "")
            )
            parsed = urllib.parse.urlsplit(public_url(link))
            if (parsed.hostname != urllib.parse.urlsplit(origin).hostname
                    or parsed.path != path
                    or urllib.parse.parse_qs(parsed.query) != {
                        "id": [str(identity)], "callId": [str(call_id)]}):
                raise AdapterError("Call identity mismatch", "parse")
            call = {"id": call_id, "url": link, "year": cells[2].text(),
                    "opening": portal_date(cells[3].text()),
                    "deadline": portal_date(cells[4].text())}
            if call_id in calls and calls[call_id] != call:
                raise AdapterError("Conflicting duplicated call", "parse")
            calls[call_id] = call
    return list(calls.values())


def selected_call(calls):
    today = utc_now()[:10]
    valid = [c for c in calls if (c["opening"] or c["deadline"])
             and not calendar_conflict(c)]
    current = [c for c in valid if c["opening"] and c["deadline"]
               and c["opening"] <= today <= c["deadline"]]
    future = [c for c in valid
              if (c["opening"] and today < c["opening"])
              or (c["deadline"] and today <= c["deadline"])]
    if current:
        return min(current, key=lambda c: (c["deadline"], c["id"]))
    if future:
        return min(future, key=lambda c: (
            c["opening"] if c["opening"] and today < c["opening"]
            else c["deadline"], c["id"],
        ))
    return max(valid, key=lambda c: (
        c["deadline"] or c["opening"], c["id"]
    ), default=None)


def calendar_conflict(call):
    """Missing endpoints are unknown, not inverted publisher calendars."""
    return bool(call["opening"] and call["deadline"]
                and call["opening"] > call["deadline"])


def bounded_excerpt(value, limit=360):
    """Keep a bounded factual excerpt and visibly mark truncation."""
    value = " ".join(value.split())
    if (re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value)
            or re.search(
                r"\b(?:phone|telephone|telefon|kontaktní informace)\s*:",
                value, re.I)):
        return None
    if len(value.encode("utf-16-le")) // 2 > limit:
        value = value.encode("utf-16-le")[:(limit - 3) * 2].decode(
            "utf-16-le", errors="ignore"
        ).rsplit(" ", 1)[0] + "..."
    return value or None


def factual_excerpt(root, heading):
    """Read a bounded section excerpt including its factual list entries."""
    parents = {id(child): parent for parent in nodes(root)
               for child in parent.children if isinstance(child, Node)}
    label = heading.rstrip(":").casefold()
    headings = [n for n in nodes(root) if n.tag in ("h2", "h3")
                and n.text().rstrip(":").casefold() == label]
    if not headings:
        return None
    node = one(headings, heading)
    level = int(node.tag[1])
    while id(node) in parents:
        parent = parents[id(node)]
        following = parent.children[parent.children.index(node) + 1:]
        parts = []
        for sibling in following:
            if isinstance(sibling, Node):
                if any(n.tag in ("h1", "h2", "h3")
                       and int(n.tag[1]) <= level for n in nodes(sibling)):
                    break
                parts.append(sibling.text())
            else:
                parts.append(sibling)
        text = " ".join(" ".join(parts).split())
        if text:
            return bounded_excerpt(text)
        if parent is root or parent.tag == "section":
            return None
        node = parent
    return None


def programme_description(main):
    """Find prose without treating empty-call messages or headings as offers."""
    for paragraph in nodes(main, "p"):
        text = paragraph.text()
        if (not text or text.endswith((":", "?"))
                or text.startswith(("There are currently no",
                                    "Pro aktivitu nejsou aktuálně"))):
            continue
        excerpt = bounded_excerpt(text)
        if excerpt:
            return excerpt
    list_heading = False
    for node in nodes(main):
        if node.tag in ("h2", "h3"):
            list_heading = node.text().rstrip(":").casefold() in {
                "intended for", "scholarship information", "description",
                "eligibility", "určeno pro", "upřesnění",
                "informace o stipendiu", "popis",
            }
        if list_heading and node.tag == "li":
            excerpt = bounded_excerpt(node.text())
            if excerpt:
                return excerpt
    return None


def native_programme_url(root, identity):
    """Follow only an advertised Czech translation of this same programme."""
    links = set()
    for anchor in nodes(root, "a"):
        if anchor.attrs.get("lang") != "cs":
            continue
        target = public_url(urllib.parse.urljoin(
            SOURCE_URL, anchor.attrs.get("href", "")
        ))
        parsed = urllib.parse.urlsplit(target)
        if (parsed.hostname != "studujnavs.gov.cz"
                or parsed.path.rstrip("/") != (
                    "/prehled-stipendii/detail-stipendia")
                or urllib.parse.parse_qs(parsed.query) != {
                    "id": [str(identity)]}):
            raise AdapterError("Native programme identity mismatch", "parse")
        links.add(target)
    if len(links) > 1:
        raise AdapterError("Ambiguous native programme link", "parse")
    return next(iter(links), None)


def programme_fields(main):
    """Retain populated structured facts, including amounts and frequency."""
    prefixes = (
        "As part of the programme:", "Education level:",
        "Relationship to the institution:", "Minimum length of stay",
        "Maximum length of stay", "Amount:", "Payment method:",
        "Payment period:", "Reimbursed:", "Reimbursed by institution:",
        "V rámci programu:", "Úroveň vzdělání:", "Vztah k instituci:",
        "Minimální délka pobytu", "Maximální délka pobytu", "Částka:",
        "Výše stipendia:", "Platební metoda:", "Perioda vyplacení:",
        "Propláceno:", "Hradí škola:",
    )
    fields = []
    for span in nodes(main, "span"):
        value = span.text()
        if (value.startswith(prefixes) and value.partition(":")[2].strip()
                and value not in fields):
            fields.append(value)
    return fields


def material_fields(fields):
    """Distinguish offer terms from title, affiliation and calendar metadata."""
    prefixes = (
        "Minimum length of stay", "Maximum length of stay", "Amount:",
        "Payment method:", "Payment period:", "Reimbursed:",
        "Reimbursed by institution:", "Minimální délka pobytu",
        "Maximální délka pobytu", "Částka:", "Výše stipendia:",
        "Platební metoda:", "Perioda vyplacení:", "Propláceno:",
        "Hradí škola:",
    )
    return [value for value in fields if value.startswith(prefixes)]


# Historical nomination-channel identity equivalences, not source facts.
_IDENTITY_GROUPS = {
    8: 7, 9: 7, 11: 10, 12: 10, 13: 10, 15: 14, 22: 21,
    71: 70, 76: 75, 81: 80, 83: 82, 95: 87,
}


def parse_programme(identity, info, root, native_root=None, native_url=None):
    main = one(nodes(root, "main"), "programme main content")
    title = one(nodes(main, "h1"), "programme title").text()
    if not title:
        raise AdapterError("Empty programme title", "parse")
    detail_fields = programme_fields(main)
    english_terms = material_fields(detail_fields)
    description = programme_description(main)
    content = main
    language = "en"
    native_calls = []
    document_pages = [(main, info["url"])]
    if native_root is not None:
        if not native_url:
            raise AdapterError("Native programme provenance missing", "parse")
        native_main = one(nodes(native_root, "main"), "native main content")
        document_pages.append((native_main, native_url))
        if not one(nodes(native_main, "h1"), "native programme title").text():
            raise AdapterError("Empty native programme title", "parse")
        detail_fields.extend(value for value in programme_fields(native_main)
                             if value not in detail_fields)
        native_calls = programme_calls(native_main, identity, "cs")
        native_description = programme_description(native_main)
        if native_description or (not description and not english_terms):
            content, language = native_main, "cs"
            description = native_description
    terms = material_fields(detail_fields)
    if not description and not terms:
        print(
            f"Programme {identity}: excluded; "
            "no description or substantive offer terms in official details",
            file=sys.stderr,
        )
        return None
    fields = list(info.get("fields", [])) + detail_fields
    calls = programme_calls(main, identity)
    by_id = {call["id"]: call for call in calls}
    for call in native_calls:
        previous = by_id.get(call["id"])
        if previous:
            for key in ("opening", "deadline"):
                if previous[key] and call[key] and previous[key] != call[key]:
                    raise AdapterError(
                        "Conflicting translated call dates", "parse"
                    )
                if previous[key] is None:
                    previous[key] = call[key]
        else:
            calls.append(call)
            by_id[call["id"]] = call
    chosen = selected_call(calls)
    if any(calendar_conflict(call) for call in calls):
        chosen = None
    evidence = [ATTRIBUTION, "Programme portal: " + info["url"],
                "Portal programme overview; external documents and "
                "call-specific conditions are not collected. "
                "Eligibility and funding are incomplete."]
    evidence.extend(fields)
    if native_url:
        evidence.append("Official Czech programme detail: " + native_url)
    if description:
        evidence.append(
            "Portal description excerpt (incomplete): " + description
        )
    excerpts = {}
    headings = (
        (("Určeno pro:", "Intended for:"),
         ("Upřesnění:", "Eligibility details"),
         ("Informace o stipendiu", "Scholarship information"),
         ("Cestovné", "Travel costs"))
        if language == "cs" else
        (("Intended for:", "Intended for:"),
         ("Scholarship information", "Scholarship information"),
         ("Travel costs", "Travel costs"))
    )
    for heading, label in headings:
        excerpt = factual_excerpt(content, heading)
        if excerpt:
            excerpts[label] = excerpt
            label = label.rstrip(":").lower()
            evidence.append(
                f"Portal {label} excerpt ({language}, incomplete): {excerpt}"
            )
    for paragraph in nodes(content, "p"):
        value = paragraph.text()
        if value.startswith((
            "Termín a délka stáže:", "Možnost bezplatného ubytování:",
            "Požadované jazykové znalosti", "Specifická dokumentace",
            "Další poznámky:", "Počet požadovaných stážistů",
        )):
            excerpt = bounded_excerpt(value)
            if excerpt:
                evidence.append("Placement terms excerpt (cs): " + excerpt)
    evidence.append(
        "Catalogue host-country labels: " + ", ".join(info.get("hosts", []))
    )
    evidence.append("Portal application status: " + ", ".join(
        info.get("application_status", [])
    ))
    documents = set()
    for page, url in document_pages:
        for anchor in nodes(page, "a"):
            target = urllib.parse.urljoin(url, anchor.attrs.get("href", ""))
            parsed = urllib.parse.urlsplit(target)
            if (parsed.scheme in ("http", "https") and parsed.hostname
                    and not parsed.username and not parsed.password
                    and parsed.path.lower().endswith((".pdf", ".xlsx"))):
                documents.add(target)
    evidence.extend(
        "Linked document (not fetched; contents unverified): " + document
        for document in sorted(documents)
    )
    evidence.extend("Czech portal calendar: " + call["url"]
                    for call in native_calls)
    for call in sorted(calls, key=lambda c: c["id"]):
        evidence.append(
            f"Call {call['id']}: {call['url']}; academic year {call['year']}; "
            f"applications {call['opening'] or 'unknown'} "
            f"to {call['deadline'] or 'unknown'} (date only)."
            + (" Publisher calendar conflict: closing precedes opening."
               if calendar_conflict(call) else "")
        )
    # Classification uses current explicit programme vocabulary, not an ID map.
    words = (title + " " + " ".join(
        field for field in fields if field.startswith("Scholarship type:")
    )).lower()
    if re.search(r"\b(internships?|traineeships?|placements?)\b", words):
        categories = ["internships"]
    elif re.search(
        r"\b(courses?|seminars?|summer schools?|summer colleges?)\b", words
    ):
        categories = ["training"]
    elif re.search(r"\b(research|teaching|academic staff|fellowship)\b", words):
        categories = ["fellowships"]
    elif re.search(r"\b(scholarship|scholarships|study|students)\b", words):
        categories = ["scholarships"]
    else:
        categories = ["other"]
    record = make_record(title, info["url"], categories,
                         kind="opportunity" if chosen else "programme-overview",
                         method="publisher-fields",
                         host_countries=info.get("hosts"), evidence=evidence)
    record["id"] = hashlib.sha256(
        f"{SOURCE_ID}|programme-{identity}".encode()
    ).hexdigest()[:24]
    record["language"] = language
    citizenship = "Praktická stáž může být umožněna pouze občanům ČR."
    if citizenship in content.text():
        record["eligible_countries"] = ["CZ"]
        evidence.append("Explicit citizenship requirement (cs): " + citizenship)
    parts = []
    amount = next((value for value in detail_fields if value.startswith(
        ("Amount:", "Částka:", "Výše stipendia:")
    )), None)
    if amount:
        parts.append(amount + ".")
        period_label = ("Payment period:" if amount.startswith("Amount:")
                        else "Perioda vyplacení:")
        period = next((value for value in detail_fields
                       if value.startswith(period_label)), None)
        if period:
            parts.append(period + ".")
    if description:
        parts.append(description)
    audience = excerpts.get("Intended for:") or excerpts.get(
        "Eligibility details"
    )
    if audience and audience != description:
        parts.append("Intended for: " + audience)
    funding = excerpts.get("Scholarship information")
    if funding and funding not in parts:
        parts.append(funding)
    if not parts:
        parts = terms + [value for value in fields if value not in terms]
    record["summary"] = bounded_excerpt(" ".join(parts), 560) or ""
    record["tags"] = [field.split(":", 1)[1].strip() for field in fields
                      if field.startswith("Scholarship type:")]
    if chosen:
        record["deadline"] = chosen["deadline"]
        evidence.append("Selected portal calendar: " + chosen["url"])
        # This status describes only the dated portal application channel.
        if chosen["deadline"] and utc_now()[:10] > chosen["deadline"]:
            record["status"] = "expired"
        elif (chosen["opening"] and chosen["deadline"]
              and chosen["opening"] <= utc_now()[:10] < chosen["deadline"]
              and info.get("application_status") == ["applications open"]):
            record["status"] = "open"
    return record


def collect():
    prepare_collection()
    inventory = discover_programmes()
    records = {}
    excluded = set()
    native_ids = []
    for index, (identity, info) in enumerate(sorted(inventory.items()), 1):
        root = read_public_page(info["url"])
        main = one(nodes(root, "main"), "programme main content")
        native_url = None
        native_root = None
        if not programme_description(main):
            native_url = native_programme_url(root, identity)
            if native_url:
                native_root = read_public_page(native_url)
                native_ids.append(identity)
        record = parse_programme(
            identity, info, root, native_root=native_root, native_url=native_url
        )
        if record is not None:
            records[identity] = record
        else:
            excluded.add(identity)
        print(
            f"Programme {identity}: {index}/{len(inventory)}", file=sys.stderr
        )
    merged = {}
    for alias, representative in _IDENTITY_GROUPS.items():
        if alias not in records or representative not in records:
            continue
        channel = records.pop(alias)
        merged[alias] = representative
        target = records[representative]
        target["classification"]["evidence"].extend([
            "Nomination channel: " + channel["url"],
            "Channel title: " + channel["title"],
            *channel["classification"]["evidence"][3:],
        ])
        target["host_countries"] = sorted(set(
            target["host_countries"] + channel["host_countries"]
        ))
        if channel["deadline"] != target["deadline"]:
            target["deadline"] = None
            target["status"] = "unknown"
    if set(records) | excluded | set(merged) != set(inventory):
        raise AdapterError("Incomplete programme accounting", "validate")
    print(json.dumps({
        "catalogue_programmes": len(inventory),
        "records": len(records), "excluded_programmes": sorted(excluded),
        "merged_channels": merged, "native_details": native_ids,
    }), file=sys.stderr)
    result = [records[key] for key in sorted(records)]
    validate_records(result)
    return result

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

def remove_expired_records(records, checked_at):
    """Filter validated deadlines after retrieval, before atomic publication.

    Unknown publisher zones use UTC-12 only as a global certainty bound:
    a date remains eligible until its closing day has ended everywhere.
    No age, status, missing response or unstated local clock implies expiry.
    """
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    latest_day = checked.astimezone(timezone(timedelta(hours=-12))).date()
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if isinstance(deadline, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                    closing_day = datetime.strptime(deadline, "%Y-%m-%d").date()
                    expired = closing_day < latest_day
                else:
                    cutoff = datetime.fromisoformat(
                        deadline.replace("Z", "+00:00")
                    )
                    expired = cutoff.tzinfo is not None and cutoff < checked
            except ValueError:
                # Unsupported or ambiguous deadlines never authorize removal.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, *, allow_empty=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError(
            "Empty or invalid opportunity collection", "validate"
        )
    ids = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("source") != SOURCE_ID
            or record.get("source_url") != SOURCE_URL
        ):
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
            if (
                not isinstance(record.get(field), str)
                or not record[field].strip()
            ):
                raise AdapterError("Missing record field: " + field, "validate")
        if record["id"] in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"])
        url = urllib.parse.urlsplit(record["url"])
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
        ):
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
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        if field != "deadline":
                            raise ValueError()
                        datetime.strptime(value, "%Y-%m-%d")
                    elif (
                        datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        ).tzinfo
                        is None
                    ):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError(
                        "Invalid record timestamp: " + field, "validate"
                    )
        for field in ("tags", "host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise AdapterError("Invalid record list: " + field, "validate")
            if field != "tags" and any(
                not re.fullmatch("[A-Z]{2}", value) for value in values
            ):
                raise AdapterError("Invalid country code", "validate")
        categories = record.get("categories")
        if (
            not isinstance(categories, list)
            or not categories
            or any(value not in CATEGORIES for value in categories)
            or len(set(categories)) != len(categories)
            or record.get("category") != categories[0]
        ):
            raise AdapterError("Invalid opportunity categories", "validate")
        if record.get("kind") not in (
            "opportunity",
            "programme-overview",
            "institutional-grant",
            "unknown",
        ) or record.get("status") not in ("open", "expired", "unknown"):
            raise AdapterError("Invalid opportunity kind or status", "validate")
        summary = record.get("summary")
        if not isinstance(summary, str) or re.search("<[^>]+>", summary):
            raise AdapterError("Invalid plain summary", "validate")
        length = len(summary.encode("utf-16-le")) // 2
        if length > 600:
            raise AdapterError("Invalid plain summary", "validate")
        for field in ("location", "language", "publisher_country"):
            if (
                field not in record
                or record[field] is not None
                and not isinstance(record[field], str)
            ):
                raise AdapterError("Invalid record field: " + field, "validate")
        classification = record.get("classification")
        if (
            not isinstance(classification, dict)
            or not isinstance(classification.get("method"), str)
            or classification.get("status") not in ("classified", "unknown")
            or not isinstance(classification.get("evidence"), list)
            or any(
                not isinstance(value, str)
                for value in classification["evidence"]
            )
        ):
            raise AdapterError("Invalid classification evidence", "validate")

_APP_TOKEN = None
_APP_EXPIRY = 0
_APP_CONFIGURATION = None


class DER:
    """Strict definite-length DER reader for unencrypted RSA private keys."""

    def __init__(self, data):
        self.data, self.offset = data, 0

    def read(self, tag):
        if self.offset + 2 > len(self.data):
            raise ValueError("Incomplete DER")
        actual, length = self.data[self.offset:self.offset + 2]
        self.offset += 2
        if actual != tag:
            raise ValueError("Unexpected DER tag")
        if length & 128:
            count = length & 127
            if not 1 <= count <= 4:
                raise ValueError("Invalid DER length")
            raw = self.data[self.offset:self.offset + count]
            if len(raw) != count or raw[0] == 0:
                raise ValueError("Nonminimal DER length")
            self.offset += count
            length = int.from_bytes(raw, "big")
            if length < 128:
                raise ValueError("Nonminimal DER length")
        end = self.offset + length
        if end > len(self.data):
            raise ValueError("Incomplete DER value")
        result = self.data[self.offset:end]
        self.offset = end
        return result

    def integer(self):
        raw = self.read(2)
        if not raw or raw[0] & 128:
            raise ValueError("Negative or empty DER integer")
        if len(raw) > 1 and raw[0] == 0 and not raw[1] & 128:
            raise ValueError("Nonminimal DER integer")
        return int.from_bytes(raw, "big")

    def finish(self):
        if self.offset != len(self.data):
            raise ValueError("Trailing DER content")


def rsa_key(pem):
    """Validate a complete PKCS#1/PKCS#8 two-prime RSA private key."""
    try:
        if not isinstance(pem, str) or len(pem) > 20000:
            raise ValueError("Invalid PEM size")
        match = re.fullmatch(
            r"\s*-----BEGIN (RSA PRIVATE KEY|PRIVATE KEY)-----\r?\n"
            r"([A-Za-z0-9+/=\r\n]+)-----END \1-----\s*", pem,
        )
        if not match:
            raise ValueError("Unsupported PEM")
        encoded = match[2].replace("\r", "").replace("\n", "")
        raw = base64.b64decode(encoded, validate=True)
        if base64.b64encode(raw).decode() != encoded:
            raise ValueError("Noncanonical PEM encoding")
        outer = DER(raw)
        sequence = DER(outer.read(48))
        outer.finish()
        if match[1] == "PRIVATE KEY":
            if sequence.integer() != 0:
                raise ValueError("Unsupported PKCS#8 version")
            algorithm = DER(sequence.read(48))
            if algorithm.read(6) != bytes.fromhex("2a864886f70d010101"):
                raise ValueError("Not RSA")
            if algorithm.read(5) != b"":
                raise ValueError("Invalid RSA parameters")
            algorithm.finish()
            inner = DER(sequence.read(4))
            sequence.finish()
            sequence = DER(inner.read(48))
            inner.finish()
        values = [sequence.integer() for _ in range(9)]
        sequence.finish()
        version, n, e, d, p, q, dp, dq, inverse = values
        if (
            version != 0 or not 2048 <= n.bit_length() <= 8192
            or e < 3 or e % 2 == 0 or e >= n
            or min(d, p, q, dp, dq, inverse) <= 0
            or d >= n or inverse >= p
            or p == q or p % 2 == 0 or q % 2 == 0 or p * q != n
            or e * d % math.lcm(p - 1, q - 1) != 1
            or dp != d % (p - 1) or dq != d % (q - 1)
            or inverse * q % p != 1
        ):
            raise ValueError("Inconsistent RSA key")
        return n, e, d
    except Exception:
        raise AdapterError((
            "Invalid GitHub App signing key"
        ), (
            "publish"
        )) from None


def app_jwt(now):
    app_id = os.environ.get("WEBSITE_APP_ID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", app_id):
        raise AdapterError("Invalid GitHub App identifier", "publish")
    n, e, d = rsa_key(os.environ.get("WEBSITE_APP_PRIVATE_KEY", ""))
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")
    header = encode(b'{"alg":"RS256","typ":"JWT"}')
    payload = encode(json.dumps({
        "iat": int(now) - 60, "exp": int(now) + 540, "iss": app_id,
    }, separators=(",", ":")).encode())
    message = header + b"." + payload
    digest = bytes.fromhex("3031300d060960864801650304020105000420")
    digest += hashlib.sha256(message).digest()
    size = (n.bit_length() + 7) // 8
    padded = b"\x00\x01" + b"\xff" * (size - len(digest) - 3)
    padded += b"\x00" + digest
    signature = pow(int.from_bytes(padded, "big"), d, n)
    if pow(signature, e, n) != int.from_bytes(padded, "big"):
        raise AdapterError("Invalid RSA signing operation", "publish")
    return (message + b"." + encode(signature.to_bytes(size, "big"))).decode()


def app_api(method, path, jwt, payload=None):
    """Use only the authenticated GitHub origin; never expose credentials."""
    try:
        status, _, body = request_bytes(
            "https://api.github.com" + path, method=method,
            headers={
                "Authorization": "Bearer " + jwt,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            }, payload=(json.dumps(payload).encode()
                        if payload is not None else None),
        )
        if status not in (200, 201):
            raise ValueError("App API failure")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Invalid App response")
        return result
    except Exception:
        raise AdapterError((
            "GitHub App authentication failed"
        ), (
            "publish"
        )) from None


def publication_token():
    """Renew a repository-scoped installation token before expiry/CAS."""
    global _APP_TOKEN, _APP_EXPIRY, _APP_CONFIGURATION
    if not os.environ.get("WEBSITE_APP_ID"):
        token = os.environ.get("DATA_SOURCE_TOKEN")
        if not token:
            raise AdapterError("Publication credentials missing", "publish")
        return token
    configuration = hashlib.sha256((
        os.environ.get("WEBSITE_APP_ID", "") + "\0"
        + os.environ.get("WEBSITE_APP_INSTALLATION_ID", "") + "\0"
        + os.environ.get("WEBSITE_APP_PRIVATE_KEY", "")
    ).encode()).digest()
    if _APP_CONFIGURATION != configuration:
        _APP_TOKEN, _APP_EXPIRY = None, 0
        _APP_CONFIGURATION = configuration
    now = time.time()
    if _APP_TOKEN and now + 300 < _APP_EXPIRY <= now + 3700:
        return _APP_TOKEN
    jwt = app_jwt(now)
    installation = app_api(
        "GET", "/repos/YouthOpps/data-source/installation", jwt,
    )
    identity = installation.get("id")
    expected = os.environ.get("WEBSITE_APP_INSTALLATION_ID", "")
    account = installation.get("account", {})
    if (
        type(identity) is not int or identity <= 0
        or str(identity) != expected
        or not isinstance(account, dict)
        or installation.get("app_id") != int(os.environ["WEBSITE_APP_ID"])
        or account.get("login") != "YouthOpps"
        or account.get("type") != "Organization"
    ):
        raise AdapterError("Unexpected GitHub App installation", "publish")
    result = app_api((
        "POST"
    ), (
        f"/app/installations/{identity}/access_tokens"
    ), jwt, {
        "repositories": ["data-source"], "permissions": {"contents": "write"},
    })
    repositories = result.get("repositories")
    permissions = result.get("permissions", {})
    try:
        expiry = datetime.fromisoformat(
            result["expires_at"].replace("Z", "+00:00")
        )
        if expiry.utcoffset() is None:
            raise ValueError("Missing token timezone")
        expiry = expiry.timestamp()
    except Exception:
        raise AdapterError((
            "Invalid installation token expiry"
        ), (
            "publish"
        )) from None
    now = time.time()
    if (
        not isinstance(result.get("token"), str)
        or not re.fullmatch(r"[!-~]{1,4096}", result["token"])
        or not now + 300 < expiry <= now + 3700
        or not isinstance(repositories, list) or len(repositories) != 1
        or not isinstance(repositories[0], dict)
        or repositories[0].get("full_name") != "YouthOpps/data-source"
        or not isinstance(permissions, dict)
        or permissions.get("contents") != "write"
        or any(key not in ("contents", "metadata")
               or key == "metadata" and value != "read"
               for key, value in permissions.items())
    ):
        raise AdapterError("Unexpected installation token scope", "publish")
    _APP_TOKEN, _APP_EXPIRY = result["token"], expiry
    return _APP_TOKEN


def github(method, path, payload=None, missing_ok=False):
    token = publication_token()
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
            payload=(
                json.dumps(payload).encode() if payload is not None else None
            ),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; publication is unconfirmed", "publish"
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(
            f"GitHub publication API returned HTTP {status}", "publish", status
        )
    return json.loads(body)

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
        if file is not None and file.get("encoding") == "none":
            sha = file.get("sha", "")
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise AdapterError("Invalid existing blob identity", "publish")
            file = github("GET", "/git/blobs/" + sha)
            if file.get("sha") != sha:
                raise AdapterError("Existing blob identity mismatch", "publish")
        if file is not None and file.get("encoding") != "base64":
            raise AdapterError(
                "Unsupported existing source file encoding", "publish"
            )
        files[name] = (
            None
            if file is None
            else base64.b64decode(file["content"]).decode("utf-8")
        )
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish"
        )
    return {"head": head, "tree": tree, "files": files}

def publish_files(snapshot, files):
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        candidate = None
        try:
            tree = github(
                "POST",
                "/git/trees",
                {
                    "base_tree": snapshot["tree"],
                    "tree": [
                        {
                            "path": f"datas/{SOURCE_ID}/{name}",
                            "mode": "100644",
                            "type": "blob",
                            "content": text,
                        }
                        for name, text in files.items()
                    ],
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
                {"sha": candidate, "force": False},
            )
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (
                latest["head"] == candidate or latest["files"] == desired
            ):
                return
            if latest["files"] != expected:
                raise AdapterError(
                    "Source changed concurrently; "
                    "newer remote data was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; "
                    "durable remote outcome could not be updated",
                    "publish",
                )

def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "source": SOURCE_ID,
        "name": "Study in Czechia",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (
            previous.get("last_success_at") if error else attempt
        ),
        "last_checked_at": (
            previous.get("last_checked_at")
            if error
            else checked_at
            or max(
                (record["last_checked_at"] for record in records),
                default=attempt,
            )
        ),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (
                f"Collected {len(records)} validated Study in Czechia "
                "funding programmes and placements"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Write only this source to YouthOpps/data-source "
            "using DATA_SOURCE_TOKEN"
        ),
    )
    args = parser.parse_args()
    snapshot = read_snapshot() if args.publish else None
    old = (
        json.loads(snapshot["files"]["data.json"])
        if snapshot and snapshot["files"]["data.json"] is not None
        else []
    )
    previous = (
        json.loads(snapshot["files"]["metadata.json"])
        if snapshot and snapshot["files"]["metadata.json"] is not None
        else {}
    )
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = utc_now(), None
    try:
        records = collect()
        checked_at = utc_now()
        validate_records(records)
        prior = {record["id"]: record for record in old}
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
                updated_at=(
                    before.get("updated_at", attempt) if same else attempt
                ),
                last_seen_at=attempt,
                last_checked_at=checked_at,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
        records = remove_expired_records(records, utc_now())
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
    if args.publish and (
        not failure or snapshot["files"]["data.json"] is not None
    ):
        files = {
            "metadata.json": json.dumps(outcome, ensure_ascii=False, indent=2)
            + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(
                attempt,
                previous,
                old,
                AdapterError(
                    "Publication failed: " + safe_error(error), "publish"
                ),
            )
            try:
                latest = read_snapshot()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files(
                    latest,
                    {
                        "metadata.json": json.dumps(
                            outcome, ensure_ascii=False, indent=2
                        )
                        + "\n"
                    },
                )
            except Exception:
                outcome["message"] = (
                    "Publication failed; durable failure status could not be "
                    "recorded. Remote data was not force-overwritten."
                )
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0



if __name__ == "__main__":
    try:
        try:
            result = main()
        finally:
            export_family_artifact()
        sys.exit(result)
    except Exception as error:
        print(json.dumps({
            "source": SOURCE_ID,
            "status": "fail",
            "last_attempt_at": utc_now(),
            "message": "Run could not complete; durable status unavailable",
            "error": safe_error(error),
            "failure_stage": getattr(error, "stage", "publish"),
        }, ensure_ascii=False))
        sys.exit(1)
