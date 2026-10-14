"""Discover University of Zagreb calls and parse live programme tables."""

import argparse
import base64
import contextlib
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
import signal
import ssl
import stat
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "hr-university-of-zagreb"
SOURCE_URL = "https://www.unizg.hr/studiji-i-studiranje/upisi-stipendije-priznavanja/stipendije/"
WEBSITE_URL = "https://www.unizg.hr/"
LANGUAGE = "hr"
PUBLISHER_COUNTRY = "HR"
PUBLISHER_TYPE = "university"
ATTRIBUTION = "Sveučilište u Zagrebu https://www.unizg.hr/ and the named programme administrators"

_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = (
    "YouthOpps/1.0 (+https://github.com/YouthOpps/data-pipeline; "
    "public opportunity metadata)"
)
CATEGORIES = {
    "scholarships",
    "grants",
    "internships",
    "jobs",
    "training",
    "fellowships",
}
FAMILY = "youthopps-unizg-publisher-v1"
FAMILY_SOURCES = {"hr-university-of-zagreb"}
COLLECTION_TIMEOUT = 2100
RUN_TIMEOUT = 2490
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "unizg-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None


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
            class_name is None
            or class_name in n.attrs.get("class", "").split()
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


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


def check_deadline():
    if _PHASE_END is None or _RUN_END is None:
        raise AdapterError("Bounded execution context required", "access")
    if time.monotonic() >= min(_PHASE_END, _RUN_END):
        raise PhaseExpired()


def request_timeout(publisher=False):
    check_deadline()
    remaining = min(_PHASE_END, _RUN_END) - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    return min(60 if publisher else 15, remaining)


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextlib.contextmanager
def execution():
    global _RUN_END, _COLLECTION_LOCK_FD
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = None
        if _COLLECTION_LOCK_FD is not None:
            try:
                os.close(_COLLECTION_LOCK_FD)
            except OSError:
                pass
            _COLLECTION_LOCK_FD = None


@contextlib.contextmanager
def phase(name, seconds):
    global _PHASE_END, _PHASE_NAME
    if _PHASE_END is not None or _RUN_END is None:
        raise AdapterError("Invalid phase transition", "access")
    _PHASE_NAME = name
    limit = _RUN_END if name == "export" else _RUN_END - EXPORT_TIMEOUT
    _PHASE_END = min(time.monotonic() + seconds, limit)
    try:
        check_deadline()
        arm_alarm(_PHASE_END)
        yield
        check_deadline()
    finally:
        _PHASE_END = _PHASE_NAME = None
        arm_alarm(limit)


_OPENER = urllib.request.build_opener(NoRedirect)


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) != {
        "schema",
        "family",
        "observed_at",
        "not_before",
        "starts",
        "blocked",
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
    if (
        type(state["schema"]) is not int
        or state["schema"] != _STATE_SCHEMA
        or state["family"] != FAMILY
        or not numeric(state["observed_at"])
        or not numeric(state["not_before"])
        or type(state["blocked"]) is not bool
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 10
        or any(not numeric(t) for t in state["starts"])
        or state["starts"] != sorted(state["starts"])
        or len(set(state["starts"])) != len(state["starts"])
        or any(t > state["observed_at"] for t in state["starts"])
    ):
        raise AdapterError("Invalid publisher pacing history", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK
    if _STATE_PATH:
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + ("-") + str(os.getuid())
    )
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
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
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
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe publisher pacing state owner", "access")
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher pacing state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError(
            "Corrupt publisher pacing state", "access"
        ) from None


def save_budget(state):
    validate_budget(state)
    fd, name = tempfile.mkstemp(
        prefix=("pending-"), dir=os.path.dirname(_STATE_PATH)
    )
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
    check_deadline()
    with locked_budget():
        state = load_budget()
        if state["blocked"]:
            raise AdapterError(
                ("Publisher backoff requires reviewed recovery"), ("access")
            )
        now = time.time()
        starts = [t for t in state["starts"] if t > now - 60]
        target = max(now, state["not_before"])
        if starts:
            target = max(target, starts[-1] + 6)
        if len(starts) == 10:
            target = max(target, starts[0] + 60)
        if target + 120 > time.time() + (_PHASE_END - time.monotonic()):
            raise AdapterError(
                "Publisher backoff exceeds phase budget", "access"
            )
        if _COLLECTION_STARTED is not None and (
            target + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError(
                ("Publisher backoff exceeds collection budget"), ("access")
            )
        if target > now:
            time.sleep(target - now)
        now = time.time()
        if now + 0.001 < target:
            raise AdapterError(
                ("Publisher pacing clock moved backwards"), ("access")
            )
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


def publisher_transport_error(error, url):
    """Describe a publisher transport failure without logging exception text."""
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(cause, ssl.SSLCertVerificationError):
        category = "TLS certificate verification failure"
    elif isinstance(cause, ssl.SSLError):
        category = "TLS handshake/read failure"
    elif isinstance(cause, TimeoutError):
        category = "Network timeout"
    elif isinstance(cause, ConnectionError):
        category = "Connection failure"
    else:
        category = "Network transport failure"
    parsed = urllib.parse.urlsplit(url)
    hop = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.hostname or "", parsed.path, "", "")
    )
    return AdapterError(
        "Publisher transport failure at " + hop + " (" + category + ")",
        "fetch",
    )


def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        try:
            response = _OPENER.open(
                request, timeout=request_timeout(publisher)
            )
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, OSError) as error:
            if publisher:
                raise publisher_transport_error(error, current) from None
            raise
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
                raise AdapterError(
                    ("Oversized or invalid response length"), ("fetch")
                )
            try:
                body = response.read(byte_limit + 1)
            except (urllib.error.URLError, OSError) as error:
                if publisher:
                    raise publisher_transport_error(error, current) from None
                raise
            if (
                len(body) > byte_limit
                or declared
                and len(body) != int(declared)
            ):
                raise AdapterError("Incomplete or oversized response", "fetch")
            encoding = response.headers.get("Content-Encoding", "identity")
            if encoding != "identity":
                raise AdapterError(
                    ("Unsupported response content encoding"), ("fetch")
                )
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
                    raise AdapterError(
                        ("Publisher redirect transport downgrade"), ("access")
                    )
                check_robots(destination)
            else:
                if new.scheme != "https" or new.username or new.password:
                    raise AdapterError(
                        ("Unsafe authenticated redirect"), ("publish")
                    )
                if new.netloc != old.netloc:
                    headers = {
                        key: value
                        for key, value in headers.items()
                        if key.lower() != "authorization"
                    }
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    raise AdapterError(
                        ("Unexpected GitHub API redirect"), ("publish")
                    )
            current = destination
            continue
        if publisher and status in (401, 403, 429):
            refused = urllib.parse.urlsplit(current)
            provenance = urllib.parse.urlunsplit(
                (refused.scheme, refused.hostname or "", refused.path, "", "")
            )
            raise AdapterError(
                f"Publisher refused access: HTTP {status}: " + provenance,
                "access",
                status,
            )
        return status, response_headers, body
    raise AdapterError("Unresolved redirect", "access")


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
            raise AdapterError(
                ("Publisher robots policy unavailable"), ("access"), status
            )
    parser = _ROBOTS[origin]
    if parser is not None and not parser.can_fetch(_USER_AGENT, url):
        raise AdapterError(
            "Publisher robots excludes required input", "access"
        )
    if parser is not None:
        delay = parser.crawl_delay(_USER_AGENT)
        if delay and delay > 6:
            with locked_budget():
                state = load_budget()
                if state["starts"]:
                    state[("not_before")] = max(
                        state[("not_before")], state[("starts")][-1] + delay
                    )
                    save_budget(state)
        rate = parser.request_rate(_USER_AGENT)
        if rate and rate.seconds / rate.requests > 6:
            raise AdapterError(
                ("Stricter publisher request-rate requires review"), ("access")
            )


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
            f"Publisher returned HTTP {status}: " + provenance,
            "fetch",
            status,
        )


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            ("Workflow pacing state unavailable"), ("access")
        ) from None


def current_run_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "")
        for key in (("GITHUB_RUN_ID"), ("GITHUB_RUN_ATTEMPT"))
    ]
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
        if (
            not source
            or type(identity) is not int
            or identity <= 0
            or type(attempt) is not int
            or attempt <= 0
            or run["status"] != "completed"
            or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run[("head_repository")][("full_name")]
            != ("YouthOpps/data-pipeline")
            or run["head_branch"] != "main"
            or expected
            and (identity, attempt, source) != expected
        ):
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
        raise AdapterError(
            ("Untrusted completed family attempt"), ("access")
        ) from None


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
                    "Workflow inventory remained unstable", "access"
                ) from None


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    current_id, current_attempt = current_run_identity()
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        result = workflow_api(f"/actions/runs?per_page=100&page={page}")
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(runs, list)
            or len(runs) > 100
        ):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace(
                ("Workflow inventory changed during restore"), ("access")
            )
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            run_id = run.get("id")
            if type(run_id) is not int or run_id <= 0 or run_id in seen_ids:
                raise AdapterError(
                    "Invalid or duplicate workflow run identity", "access"
                )
            seen_ids.add(run_id)
            source = family_source(run)
            if not source:
                continue
            if (
                run.get(("repository"), {}).get(("full_name"))
                != ("YouthOpps/data-pipeline")
                or run.get("head_repository", {}).get("full_name")
                != ("YouthOpps/data-pipeline")
                or run.get(("head_branch")) != ("main")
            ):
                raise AdapterError(
                    ("Workflow repository or branch mismatch"), ("access")
                )
            identity, attempt = run.get("id"), run.get("run_attempt")
            if type(identity) is not int or type(attempt) is not int:
                raise AdapterError("Invalid family run identity", "access")
            if identity == current_id:
                if attempt != current_attempt:
                    raise AdapterError(
                        ("Current workflow attempt mismatch"), ("access")
                    )
                if current_attempt > 1:
                    prior = workflow_api(
                        (
                            f"/actions/runs/{identity}/attempts/"
                            f"{current_attempt - 1}"
                        )
                    )
                    candidates.append(
                        completed_identity(
                            prior, (identity, current_attempt - 1, source)
                        )
                    )
                continue
            if run.get(("status")) in (
                ("queued"),
                ("waiting"),
                ("pending"),
                ("requested"),
            ):
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError(
                    ("Workflow inventory count mismatch"), ("access")
                )
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(
                    ("Ambiguous family attempt chronology"), ("access")
                )
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
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != 302:
                raise ValueError("Expected artifact redirect")
            destination = response.headers.get("Location", "")
            response.read(1001)
        parsed = urllib.parse.urlsplit(destination)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            raise ValueError("Unsafe artifact URL")
        if "." not in parsed.hostname or parsed.hostname.endswith(
            ((".local"), (".internal"), (".localhost"))
        ):
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
            if (
                not isinstance(digest, str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                or digest[7:] != hashlib.sha256(body).hexdigest()
            ):
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
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "state",
        }:
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source = identity
        if (
            type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or envelope["repository"] != "YouthOpps/data-pipeline"
            or type(envelope["run_id"]) is not int
            or type(envelope["run_attempt"]) is not int
            or envelope["run_id"] != run_id
            or envelope["run_attempt"] != attempt
            or envelope["source"] != source
        ):
            raise ValueError("Artifact ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raw = os.environ.get("UNIZG_PACING_BOOTSTRAP", "")
        try:
            bootstrap = json.loads(raw)
            if (
                set(bootstrap)
                != {"schema", "family", "not_before", "evidence"}
                or type(bootstrap["schema"]) is not int
                or bootstrap["schema"] != 1
                or bootstrap["family"] != FAMILY
                or not numeric(bootstrap["not_before"])
                or not isinstance(bootstrap["evidence"], str)
                or not re.fullmatch(
                    (
                        "https://github\\.com/YouthOpps/data-pipeline/(?:is"
                        "sues|pull)/[0-9]+(?:#issuecomment-[0-9]+)?"
                    ),
                    bootstrap[("evidence")],
                )
            ):
                raise ValueError("Invalid bootstrap")
            incoming = empty_budget(time.time())
            incoming[("not_before")] = max(
                time.time() + 60, bootstrap[("not_before")]
            )
        except Exception:
            raise AdapterError(
                ("Reviewed first-family bootstrap required"), ("access")
            ) from None
    else:
        run_id, attempt, source = latest
        expected = _ARTIFACT_NAME + "-" + str(attempt)
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or result.get(("total_count")) != len(artifacts)
            or len(artifacts) > 100
        ):
            raise AdapterError(
                "Incomplete family artifact inventory", "access"
            )
        matches = [a for a in artifacts if a.get("name") == expected]
        if len(matches) != 1 or matches[0].get("expired") is not False:
            raise AdapterError(
                ("Newest family pacing artifact missing or expired"),
                ("access"),
            )
        artifact = matches[0]
        if (
            type(artifact.get("id")) is not int
            or artifact["id"] <= 0
            or artifact.get("workflow_run", {}).get("id") != run_id
            or not numeric(artifact.get("size_in_bytes"))
            or artifact["size_in_bytes"] > 32768
        ):
            raise AdapterError("Invalid family artifact binding", "access")
        incoming = download_inert_artifact(
            latest, artifact[("id")], artifact.get(("digest"))
        )
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined[("not_before")] = max(
            local[("not_before")], incoming[("not_before")], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined[("starts")] = sorted(
            set(local[("starts")] + incoming[("starts")])
        )[-10:]
        save_budget(combined)
    _RESTORED, _STATE_TRUSTED = True, True


def export_family_artifact():
    """Best-effort infrastructure state; never contains records or secrets."""
    path = os.environ.get("UNIZG_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
    run_id, attempt = current_run_identity()
    with locked_budget():
        state = load_budget()
    envelope = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "run_id": run_id,
        "run_attempt": attempt,
        "source": SOURCE_ID,
        "state": state,
    }
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError(
            ("Explicit runner temporary directory required"), ("access")
        )
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
            raise AdapterError(
                ("Another local family collector is active"), ("access")
            ) from None
        _COLLECTION_LOCK_FD = descriptor
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    _STATE_TRUSTED = True


def document_root(text, complete=True):
    if complete and not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I
    ):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(
        r"just a moment|access denied|attention required|"
        r"checking your browser|verify you are human",
        title,
    ):
        raise AdapterError("Publisher access challenge", "access")
    return root


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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
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
            raise AdapterError(
                "Invalid opportunity kind or status", "validate"
            )
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
                raise AdapterError(
                    "Invalid record field: " + field, "validate"
                )
        if "summary_language" in record and (
            not isinstance(record["summary_language"], str)
            or not re.fullmatch(
                r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*",
                record["summary_language"],
            )
        ):
            raise AdapterError("Invalid summary language", "validate")
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


def github(method, path, payload=None, missing_ok=False):
    check_deadline()
    token = os.environ.get("DATA_SOURCE_TOKEN", "")
    if not token:
        raise AdapterError("Publication credentials missing", "publish")
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
            "GitHub connection failed; outcome unconfirmed", "publish"
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(
            f"GitHub publication HTTP {status}", "publish", status
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
                raise AdapterError(
                    "Existing blob identity mismatch", "publish"
                )
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
        check_deadline()
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


CATALOGUE_URL = (
    WEBSITE_URL + "o-sveucilistu/sveuciliste-jucer-danas-sutra/"
    "osiguravanje-kvalitete/cjelozivotno-obrazovanje/"
    "baza-programa-cjelozivotnog-obrazovanja/"
    "pregled-programa-czo-po-sastavnicama/"
)
RECTORATE_URL = (
    WEBSITE_URL + "o-sveucilistu/dokumenti-i-javnost-informacija/"
    "natjecaji/rektorat-natjecaji/"
)
RESEARCH_URL = (
    WEBSITE_URL + "istrazivanje/istrazivanje-i-inovacije/"
    "financiranje-istrazivanja/otvoreni-natjecaji/"
)
LISTINGS = {
    WEBSITE_URL
    + "nc/suradnja/medunarodna-razmjena/"
    + audience
    + route: category
    for audience, route, category in (
        ("razmjena-studenata/", "natjecaji-za-studente/", "scholarships"),
        (
            "razmjena-studenata/",
            "ostale-mogucnosti-stipendiranja/",
            "scholarships",
        ),
        ("razmjena-studenata/", "medunarodne-ljetne-skole/", "training"),
        (
            "razmjena-sveucilisnih-djelatnika/",
            "natjecaji-za-djelatnike/",
            "grants",
        ),
        (
            "razmjena-sveucilisnih-djelatnika/",
            "ostale-mogucnosti-stipendiranja/",
            "fellowships",
        ),
    )
}
MONTHS = {
    word: month
    for month, word in enumerate(
        (
            "sijecnja",
            "veljace",
            "ozujka",
            "travnja",
            "svibnja",
            "lipnja",
            "srpnja",
            "kolovoza",
            "rujna",
            "listopada",
            "studenog",
            "prosinca",
        ),
        1,
    )
}


def folded(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text.lower())
        if not unicodedata.combining(c)
    )


def public_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "www.unizg.hr"
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.query
            or ".." in urllib.parse.unquote(parsed.path).split("/")
        ):
            raise ValueError()
        allowed = (
            parsed.path == "/robots.txt"
            or parsed.path == urllib.parse.urlsplit(SOURCE_URL).path
            or parsed.path == urllib.parse.urlsplit(RECTORATE_URL).path
            or parsed.path == urllib.parse.urlsplit(RESEARCH_URL).path
            or parsed.path.startswith(
                urllib.parse.urlsplit(CATALOGUE_URL).path
            )
            or parsed.path.startswith("/nc/vijest/article/")
            or any(
                parsed.path.startswith(urllib.parse.urlsplit(u).path)
                for u in LISTINGS
            )
        )
        if not allowed:
            raise ValueError()
    except ValueError:
        raise AdapterError(
            "Unsupported official source route", "access"
        ) from None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def read_html(url):
    _, headers, body = fetch_public(url)
    if "html" not in headers.get("Content-Type", "").lower():
        raise AdapterError("Expected public HTML: " + url, "parse")
    return document_root(body.decode("utf-8", "strict"))


def content_blocks(root):
    return nodes(root, class_name="csc-default")


def application_deadline(text):
    """Only parse an explicit application closing with an explicit year."""
    normalized = folded(text)
    dates = set()
    date = r"(\d{1,2})\.?\s*(\d{1,2}|[a-z]+)\.?\s*(\d{4})"
    opening_date = r"\d{1,2}\.\s*[a-z]+\s*\d{4}\s*\.?"
    pattern = (
        r"(?:(?:krajnji\s+)?rok\s+(?:za\s+)?"
        r"(?:(?:podnosenje|predaju)\s+(?:(?:prvih|svih)\s+)?)?"
        r"prijav(?:u|a|e|nih\s+dokumenata)"
        r"(?:\s+(?:na\s+natjecaj|za\s+stipendiju))?"
        r"(?:\s*:\s*|\s+)(?:je\s+)?(?:zakljucno\s+s\s+|do\s+|istjece\s+)?"
        r"|prijave\s+(?:su\s+)?(?:otvorene|moguce|traju|se zaprimaju)\s+do\s+"
        r"|prijave\s+(?:su moguce od|zapocinju)\s+"
        + opening_date
        + r"\s*(?:godine\s+)?(?:i traju\s+)?do\s+"
        r"|prijav(?:u|nu dokumentaciju)\b[^.!?]{0,140}?"
        r"najkasnije\s+(?:zakljucno\s+s|do)\s+)" + date
    )
    for match in re.finditer(pattern, normalized):
        day, month, year = match.groups()
        try:
            number = int(month) if month.isdigit() else MONTHS[month]
            dates.add(datetime(int(year), number, int(day)).date().isoformat())
        except (ValueError, KeyError):
            continue
    english_months = {
        name: number
        for number, name in enumerate(
            (
                "january",
                "february",
                "march",
                "april",
                "may",
                "june",
                "july",
                "august",
                "september",
                "october",
                "november",
                "december",
            ),
            1,
        )
    }
    for match in re.finditer(
        r"(?:applications\s+(?:close|are due)\s+(?:on\s+)?"
        r"|open for applications from [a-z]+ \d{1,2} to )"
        r"([a-z]+) (\d{1,2}),? (\d{4})",
        normalized,
    ):
        month, day, year = match.groups()
        try:
            dates.add(
                datetime(int(year), english_months[month], int(day))
                .date()
                .isoformat()
            )
        except (ValueError, KeyError):
            continue
    return next(iter(dates)) if len(dates) == 1 else None


def material_facts(text):
    """Retain concise live funding and eligibility evidence beyond the lead."""
    normalized = folded(text)
    facts = []
    patterns = (
        (
            "Published funding: ",
            r"\d[\d., ]*\s*(?:eur\b|€)|iznos stipendije|cijena",
        ),
        (
            "Published eligibility: ",
            r"prijaviti se mogu|se mogu prijaviti|pravo prijave|uvjeti|prosjek|ects|invaliditet|rok za podnosenje iskaza interesa",
        ),
    )
    for label, pattern in patterns:
        for match in re.finditer(pattern, normalized):
            excerpt = text[
                max(0, match.start() - 100) : match.end() + 180
            ].strip()
            fact = label + excerpt
            if fact not in facts:
                facts.append(fact)
    return facts


def live_record(title, url, category, text, key=None, kind="opportunity"):
    record = make_record(
        title,
        url,
        [category],
        kind=kind,
        method="publisher-html",
        evidence=[ATTRIBUTION, "Live official content: " + url]
        + material_facts(text),
    )
    record["id"] = hashlib.sha256(
        (SOURCE_ID + "|" + (key or url)).encode()
    ).hexdigest()[:24]
    record["summary_language"] = LANGUAGE
    # A short attributed factual extract; never copy an entire notice.
    summary = " ".join(text.split())
    record["summary"] = (
        summary[:480].rsplit(" ", 1)[0] if len(summary) > 480 else summary
    )
    record["deadline"] = application_deadline(text)
    if (
        record["deadline"]
        and record["deadline"] < datetime.now(timezone.utc).date().isoformat()
    ):
        record["status"] = "expired"
    if re.search(r"natjecaj je zatvoren|natjecaj zatvoren", folded(text)):
        record["status"] = "expired"
    return record


def parse_courses(root, url):
    programmes = []
    current = None
    for table in nodes(root, "table"):
        for row in nodes(table, "tr"):
            cells = [n.text() for n in nodes(row, "td")]
            if len(cells) >= 2 and folded(cells[0]).replace(
                " ", ""
            ).startswith("nazivprograma"):
                if not cells[1].strip():
                    raise AdapterError(
                        "Empty catalogue programme name", "parse"
                    )
                current = (cells[1], [])
                programmes.append(current)
            elif current is not None and len(cells) == 2:
                if any(
                    word in folded(cells[0])
                    for word in (
                        "trajanje",
                        "jezik",
                        "nacin",
                        "cijena",
                        "ects",
                        "uvjet",
                    )
                ):
                    current[1].append(cells[0] + ": " + cells[1])
    records = []
    for title, facts in programmes:
        record = live_record(
            title,
            url,
            "training",
            "; ".join([title] + facts),
            key=url + "|" + title,
            kind="programme-overview",
        )
        record["deadline"] = None
        records.append(record)
    if not records:
        raise AdapterError("Missing programme table: " + url, "parse")
    return records


def parse_scholarships(root):
    block = one(
        [n for n in content_blocks(root) if nodes(n, "h1")],
        "scholarship content",
    )
    text = block.text()
    categories = {}
    for link in nodes(block, "a"):
        match = re.match(r"([A-Z])\.\s*(stipendije\b.+)", link.text(), re.I)
        if match:
            categories.setdefault(match[1].upper(), match[2])
    if not categories:
        raise AdapterError("Scholarship categories not found", "parse")
    records = []
    heading = one(nodes(block, "h1"), "scholarship title").text()
    for code, title in categories.items():
        record = live_record(
            title,
            SOURCE_URL,
            "scholarships",
            heading + ". " + title,
            key=SOURCE_URL + "|category-" + code,
        )
        record["classification"]["evidence"].extend(material_facts(text))
        # The hub mixes call and results dates; do not infer a closing from them.
        if "konacni rezultati natjecaja" in folded(text):
            record["status"] = "expired"
        records.append(record)
    return records


def parse_vacancies(root):
    records = []
    for block in content_blocks(root):
        text = block.text()
        match = re.search(
            r"za popunu\s+(?:[–-]\s*)?(?:(?:slobodnog|znanstveno-nastavnog)\s+)*"
            r"radnog mjesta\s+(.+?)(?=\s*,?\s*(?:na |neodređeno|određeno|u punom|Stručni uvjeti))",
            text,
            re.I,
        )
        if not match:
            continue
        anchor = block.attrs.get("id", "")
        if not re.fullmatch(r"c\d+", anchor):
            raise AdapterError("Vacancy has no stable section anchor", "parse")
        title = match[1].strip(" ,.")
        records.append(
            live_record(
                title,
                RECTORATE_URL + "#" + anchor,
                "jobs",
                text[text.index(match[0]) :],
            )
        )
    if not records:
        raise AdapterError("Rectorate vacancies not found", "parse")
    return records


def parse_research(root):
    candidates = []
    keys = {}
    for block in content_blocks(root):
        headings = [
            node for node in block.walk() if node.tag in ("h1", "h2", "h3")
        ]
        if not headings:
            continue
        title = headings[0].text()
        if not re.search(r"poziv|natjecaj", folded(title)):
            continue
        text = block.text()
        if not re.search(r"prijav|iskaz interesa", folded(text)):
            continue
        anchor = block.attrs.get("id", "")
        if not re.fullmatch(r"c\d+", anchor):
            raise AdapterError("Research call lacks a stable anchor", "parse")
        year = re.search(r"\b20\d{2}\b", text)
        key = "UNIC" + year[0] if "UNIC" in text and year else anchor
        candidates.append((title, text, anchor, key))
        keys[key] = keys.get(key, 0) + 1
    records = []
    for title, text, anchor, key in candidates:
        if keys[key] > 1:
            key = anchor
        records.append(
            live_record(
                title,
                RESEARCH_URL,
                "grants",
                text,
                key=RESEARCH_URL + "|" + key,
                kind="institutional-grant",
            )
        )
    if not records:
        raise AdapterError("Research call inventory missing", "parse")
    return records


def discover_calls(seed, category):
    pending, seen, cards = [seed], set(), {}
    listed_urls = set()
    advertised_total = None
    year = datetime.now(timezone.utc).year
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        if len(seen) >= 100:
            raise AdapterError(
                "Call pagination exceeded safety ceiling", "parse"
            )
        seen.add(url)
        root = read_html(url)
        items = nodes(root, class_name="news-list-item")
        if not items:
            raise AdapterError("Missing call listing: " + url, "parse")
        for item in items:
            links = [
                n for n in nodes(item, "a") if n.text() and n.text() != "više"
            ]
            link = one(links, "call title link")
            target = urllib.parse.urljoin(url, link.attrs["href"])
            listed_urls.add(target)
            dates = nodes(item, class_name="news-list-date")
            published = one(dates, "call publication date").text()
            publication = re.search(r"\b(\d{4})\b", published)
            title = link.text()
            # Old cards can lack a date span value but name their edition.
            title_years = [
                int(value) for value in re.findall(r"\b20\d{2}\b", title)
            ]
            publication_year = (
                int(publication[1])
                if publication
                else max(title_years, default=year)
            )
            if publication_year < year - 1 and not any(
                str(y) in title for y in (year, year + 1)
            ):
                continue
            if re.search(
                r"^(?:rezultati|odluka o|obavijest o rezultatima)",
                folded(title),
            ):
                continue
            cards[public_url(target)] = (title, category)
        for box in nodes(root, class_name="tx-ttnews-browsebox"):
            total = re.search(r"od ukupno\s+(\d+)", box.text())
            if total:
                if advertised_total is not None and advertised_total != int(
                    total[1]
                ):
                    raise AdapterError(
                        "Listing changed during pagination: " + seed, "parse"
                    )
                advertised_total = int(total[1])
            for link in nodes(box, "a"):
                target = public_url(
                    urllib.parse.urljoin(url, link.attrs.get("href", ""))
                )
                if (
                    target.startswith(seed)
                    and target not in seen
                    and target not in pending
                ):
                    pending.append(target)
    if advertised_total is not None and len(listed_urls) != advertised_total:
        raise AdapterError("Incomplete listing pagination: " + seed, "parse")
    print(
        f"Listing {seed}: {len(seen)} pages, {len(listed_urls)} identities",
        file=sys.stderr,
    )
    return cards


def notice_blocks(content):
    """Keep paragraph/list boundaries; attachments are not HTML suboffers."""
    result = []
    for node in content.walk():
        if node.tag not in ("p", "li", "h2", "h3", "h4"):
            continue
        if any(
            child is not node and child.tag in ("p", "li")
            for child in node.walk()
        ):
            continue
        text = node.text()
        if re.match(r"privitci\s*:", folded(text)):
            break
        if text:
            result.append((node, text))
    return result


def suboffer_deadline(text):
    """Normalize explicit track-closing clauses, not event/handoff dates."""
    text = folded(text)
    text = re.sub(
        r"rok za (?:online )?prijavu\s*(?:\([^)]*\))?\s*:",
        "rok za prijavu: ",
        text,
    )
    text = re.sub(
        r"(:\s*)(?:ponedjeljak|utorak|srijeda|cetvrtak|petak|subota|nedjelja),?\s*",
        r"\1",
        text,
    )
    text = re.sub(
        r"prijave za [^!?]{0,200}?traju do\s+",
        "rok za prijavu: ",
        text,
    )
    text = re.sub(
        r"kandidati se mogu prijaviti online do zakljucno\s+"
        r"|rok za slanje prijavne dokumentacije[^.]*? je\s+",
        "rok za prijavu: ",
        text,
    )
    return application_deadline(text)


def suboffer_record(
    title, url, category, label, key, facts, kind="opportunity"
):
    record = live_record(
        title + " - " + label,
        url,
        category,
        facts,
        key=url + "|" + key,
        kind=kind,
    )
    record["deadline"] = suboffer_deadline(facts)
    record["status"] = (
        "expired"
        if record["deadline"]
        and record["deadline"] < utc_now()[:10]
        or re.search(r"natjecaj (?:je )?zatvoren", folded(facts))
        else "unknown"
    )
    return record


def parse_suboffers(content, title, url, category):
    """Extract independently described offers from current HTML structure."""
    blocks = notice_blocks(content)
    heading = folded(title)
    records = []
    if "akademsku mobilnost" in heading:
        shared = [text for _, text in blocks if text != title]
        for _, text in blocks:
            match = re.match(r"([a-z])\)\s*(.+)", text)
            if not match:
                continue
            code, label = match.groups()
            incoming = "dolazne mobilnosti" in folded(label)
            facts = [text]
            for other in sorted(
                shared, key=lambda text: not suboffer_deadline(text)
            ):
                norm = folded(other)
                if re.match(r"[a-z]\)", other) or other == text:
                    continue
                if not re.search(r"mobilnosti|kandidati|prijav", norm):
                    continue
                if norm.startswith("za sastavnice"):
                    continue
                if "stavkama" in norm:
                    sentences = re.split(
                        r"(?<=\.)\s+(?=[A-Z])|,?\s+dok\s+", other
                    )
                    facts.extend(
                        sentence
                        for sentence in sentences
                        if code in re.findall(r"\b([a-z])\)", sentence)
                    )
                elif not incoming or not re.search(
                    r"kandidati|online|rok", norm
                ):
                    facts.append(other)
            records.append(
                suboffer_record(
                    title,
                    url,
                    "grants",
                    label,
                    code,
                    " ".join(facts),
                    kind="institutional-grant" if incoming else "opportunity",
                )
            )
        return records
    if "erasmus" in heading and "nenastavnog" in heading:
        activity_list = False
        activities = []
        deadlines = {"outgoing": [], "incoming": []}
        direction = None
        for node, text in blocks:
            norm = folded(text)
            if norm == "za aktivnosti:":
                activity_list = True
                continue
            if activity_list and node.tag == "li":
                activities.append(text)
                continue
            activity_list = False
            if "za odlazno osoblje" in norm:
                direction = "outgoing"
            elif "za dolazno nenastavno osoblje" in norm:
                direction = "incoming"
            if direction and re.search(r"rok|prijav", norm):
                deadlines[direction].append(text)
            if norm.startswith("nekoliko novina"):
                direction = None
        for text in activities:
            label, _, participants = text.partition("(")
            norm = folded(label)
            key = {
                "poucavanje": "outgoing-STA",
                "osposobljavanje": "outgoing-STT",
            }.get(norm.strip(), "activity-" + folded(label.strip()))
            outgoing = text
            if "te nenastavno osoblje iz tvrtki" in participants:
                outgoing = (
                    text.split(" te nenastavno osoblje iz tvrtki")[0] + ")"
                )
                incoming = (
                    "nenastavno osoblje iz tvrtki"
                    + participants.split("nenastavno osoblje iz tvrtki", 1)[1]
                ).rstrip("). ")
                records.append(
                    suboffer_record(
                        title,
                        url,
                        "grants",
                        label.strip() + " - " + incoming,
                        "incoming-company-teaching",
                        " ".join(
                            [label.strip() + " - " + incoming]
                            + deadlines["incoming"]
                        ),
                    )
                )
            records.append(
                suboffer_record(
                    title,
                    url,
                    (
                        "training"
                        if norm.strip() == "osposobljavanje"
                        else "grants"
                    ),
                    label.strip(),
                    key,
                    " ".join([outgoing] + deadlines["outgoing"]),
                )
            )
        return records
    if "istrazivackog boravka" in heading:
        offers = [
            text
            for _, text in blocks
            if re.match(r"\d+ stipendij[ae] za:", folded(text))
        ]
        common = [
            text
            for _, text in blocks
            if text not in offers
            and re.match(r"stipendije su otvorene|rok za prijav", folded(text))
        ]
        for text in offers:
            norm = folded(text)
            key = (
                "postdoc"
                if "poslijedoktorand" in norm
                else (
                    "PhD"
                    if "doktorand" in norm
                    else folded(text.split(":", 1)[-1])
                )
            )
            records.append(
                suboffer_record(
                    title,
                    url,
                    "fellowships",
                    text.split(":", 1)[-1].strip(" -"),
                    key,
                    " ".join([text] + common),
                )
            )
        return records
    if "azrieli" in heading:
        for _, text in blocks:
            norm = folded(text)
            if not norm.startswith("prijave za ") or "traju do" not in norm:
                continue
            label = text[: norm.index("traju do")].strip()
            cycle = re.search(r"\b(20\d{2})\.?/\s*(20\d{2}|\d{2})", norm)
            season = re.search(r"\b(proljece|jesen)\s+(20\d{2})", norm)
            if "poslijedoktorske" in norm and cycle:
                key = "postdoc" + cycle[1] + "-" + cycle[2][-2:]
            elif "doktorske" in norm and season:
                key = (
                    "PhD-"
                    + {"proljece": "Spring", "jesen": "Fall"}[season[1]]
                    + season[2]
                )
            else:
                key = folded(label)
            records.append(
                suboffer_record(title, url, "fellowships", label, key, text)
            )
        return records
    if "taiwan" in heading or "tajvan" in heading:
        sections = []
        for node, text in blocks:
            if "scholarship" in folded(text) and (
                text.split(":", 1)[0].isupper()
                or node.tag in ("h2", "h3", "h4")
            ):
                sections.append((text, []))
            elif sections:
                sections[-1][1].append(text)
        for label, facts in sections:
            norm = folded(label)
            key = (
                "Taiwan-HES"
                if norm.startswith("huayu enrichment scholarship")
                else (
                    "Taiwan-degree"
                    if norm.startswith("taiwan scholarship")
                    and "full degree" in norm
                    else norm
                )
            )
            records.append(
                suboffer_record(
                    title,
                    url,
                    "scholarships",
                    label,
                    key,
                    " ".join(facts),
                )
            )
        return records
    if "summer school" in heading or "ljetna skola" in heading:
        aliases = {
            "deep tech & business case": "Deep-Tech-Business",
            "machine learning": "Machine-Learning",
            "authentic leadership & human-centered innovation (design thinking)": "Authentic-Leadership",
            "empowering sustainability: transformative pathways in consumption": "Empowering-Sustainability",
        }
        event_months = set(MONTHS) | set(
            "sijecanj veljaca ozujak travanj svibanj lipanj srpanj "
            "kolovoz rujan listopad studeni prosinac".split()
        )
        shared_periods = [
            text
            for _, text in blocks
            if re.fullmatch(r"[A-Za-zčćđšžČĆĐŠŽ]+\s+20\d{2}\.?", text)
            and folded(text.split()[0]) in event_months
        ]
        shared_courses = False
        for node, text in blocks:
            parts = re.split(r"\s+[–-]\s+", text, maxsplit=1)
            if (
                node.tag == "li"
                and re.fullmatch(r".+\s+summer school", text, re.I)
                and not re.search(r"https?://|^više:", text, re.I)
            ):
                records.append(
                    suboffer_record(
                        title,
                        url,
                        category,
                        text,
                        "course-" + folded(text),
                        " ".join([text] + shared_periods),
                        kind="programme-overview",
                    )
                )
                shared_courses = True
                continue
            if (
                node.tag != "li"
                or len(parts) != 2
                or not re.search(r"\b20\d{2}\b", parts[1])
            ):
                continue
            label = parts[0]
            if not re.search(r"[a-z]{3}", folded(label)) or re.match(
                r"\d", label
            ):
                continue
            records.append(
                suboffer_record(
                    title,
                    url,
                    category,
                    label,
                    aliases.get(folded(label), "course-" + folded(label)),
                    text,
                    kind="programme-overview",
                )
            )
        if shared_courses:
            records.append(
                live_record(
                    title,
                    url,
                    category,
                    " ".join(text for _, text in blocks),
                    kind="programme-overview",
                )
            )
        return records
    return records


def parse_call(root, url, category):
    content = one(nodes(root, class_name="single-news"), "call detail")
    title = one(nodes(content, "h1"), "call title").text()
    text = content.text()
    if len(text) < len(title) + 40:
        raise AdapterError("Incomplete call detail: " + url, "parse")
    if "strucn" in folded(title) and "praks" in folded(title):
        category = "internships"
    elif "stipend" in folded(title):
        category = "scholarships"
    offers = parse_suboffers(content, title, url, category)
    if offers:
        return offers
    paragraphs = [n.text() for n in nodes(content, "p") if len(n.text()) > 60]
    summary = " ".join(paragraphs[:2]) or text
    record = live_record(title, url, category, text)
    record["summary"] = (
        summary[:480].rsplit(" ", 1)[0] if len(summary) > 480 else summary
    )
    tracks = []
    if re.search(r"\bSMS\s*[-(]", text):
        tracks.append(("SMS", "scholarships"))
    if re.search(r"\bSMT\s*[-(]", text):
        tracks.append(("SMT", "internships"))
    if not tracks and "erasmus" in folded(title):
        if "strucn" in folded(title) and "praks" in folded(title):
            tracks = [("SMT", "internships")]
        elif "studijski boravak" in folded(
            title
        ) and "drzave clanice" in folded(title):
            tracks = [("SMS", "scholarships")]
    if "kombinirane intenzivne programe" in folded(title):
        tracks = [("BIP", "grants")]
    records = []
    for code, track_category in tracks:
        track = dict(record)
        track["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + url + "|" + code).encode()
        ).hexdigest()[:24]
        track["category"] = track_category
        track["categories"] = [track_category]
        track["summary"] = code + ": " + record["summary"]
        records.append(track)
    return records or [record]


def read_pages():
    records = parse_scholarships(read_html(SOURCE_URL))
    root = read_html(CATALOGUE_URL)
    faculties = sorted(
        {
            public_url(urllib.parse.urljoin(CATALOGUE_URL, link.attrs["href"]))
            for block in content_blocks(root)
            for link in nodes(block, "a")
            if urllib.parse.urljoin(
                CATALOGUE_URL, link.attrs.get("href", "")
            ).startswith(CATALOGUE_URL)
            and urllib.parse.urljoin(CATALOGUE_URL, link.attrs.get("href", ""))
            != CATALOGUE_URL
        }
    )
    if not faculties:
        raise AdapterError("Empty faculty catalogue", "parse")
    for url in faculties:
        records.extend(parse_courses(read_html(url), url))
    records.extend(parse_vacancies(read_html(RECTORATE_URL)))
    records.extend(parse_research(read_html(RESEARCH_URL)))
    calls = {}
    for seed, category in LISTINGS.items():
        for url, item in discover_calls(seed, category).items():
            calls.setdefault(url, item)
    for url, (listed_title, category) in sorted(calls.items()):
        records.extend(parse_call(read_html(url), url, category))
    print(
        f"Discovered {len(faculties)} faculty pages and {len(calls)} recent-edition notices",
        file=sys.stderr,
    )
    return records


def parse_inventory(records):
    unique = {}
    for record in records:
        before = unique.get(record["id"])
        if before is not None and before["title"] != record["title"]:
            raise AdapterError("Conflicting publisher identities", "validate")
        unique.setdefault(record["id"], record)
    result = sorted(unique.values(), key=lambda record: record["id"])
    validate_records(result)
    return result


def collect():
    if _PHASE_END is not None:
        check_deadline()
        return parse_inventory(read_pages())
    with execution(), phase("prepublication", COLLECTION_TIMEOUT):
        prepare_collection()
        return parse_inventory(read_pages())


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "source": SOURCE_ID,
        "name": "University of Zagreb — bounded public calls and educational catalogue",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "attribution": ATTRIBUTION,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (
            previous.get("last_success_at") if error else attempt
        ),
        "last_checked_at": (
            previous.get("last_checked_at") if error else checked_at
        ),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (
                f"Validated refresh retains {len(records)} "
                "University of Zagreb opportunities"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


def phase_error(name):
    return AdapterError(
        "Whole " + name + " phase deadline exceeded",
        "publish" if name == "publication" else "access",
    )


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def remove_expired_records(records, checked):
    """Filter the validated replacement, leaving prior publication untouched."""
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise AdapterError("Cleanup clock requires a timezone", "validate")
    current = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if isinstance(deadline, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                # Without a documented zone, wait for worldwide day closure.
                expired = (
                    datetime.strptime(deadline, "%Y-%m-%d").date()
                    < now.astimezone(timezone(timedelta(hours=-12))).date()
                )
            else:
                instant = datetime.fromisoformat(
                    deadline.replace("Z", "+00:00")
                )
                expired = instant.tzinfo is not None and instant < now
        if not expired:
            current.append(record)
    # Valid nonempty retrieval may have no remaining eligible records.
    validate_records(current, allow_empty=True)
    return current


def fresh_records(records, old, attempt, checked):
    prior = {record["id"]: record for record in old}
    identities = {}
    prior_urls = {}
    current_urls = {}
    current_identities = {}
    used_ids = {record["id"] for record in records if record["id"] in prior}
    for record in old:
        identities.setdefault(
            (record["url"], folded(record["title"])), []
        ).append(record)
        prior_urls.setdefault(record["url"], []).append(record)
    for record in records:
        current_urls[record["url"]] = current_urls.get(record["url"], 0) + 1
        identity = (record["url"], folded(record["title"]))
        current_identities[identity] = current_identities.get(identity, 0) + 1
    temporal = {
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    for record in records:
        identity = (record["url"], folded(record["title"]))
        matches = (
            identities.get(identity, [])
            if current_identities[identity] == 1
            else []
        )
        if (
            len(matches) != 1
            and len(prior_urls.get(record["url"], [])) == 1
            and current_urls[record["url"]] == 1
        ):
            matches = prior_urls[record["url"]]
        if (
            record["id"] not in prior
            and len(matches) == 1
            and matches[0]["id"] not in used_ids
        ):
            record["id"] = matches[0]["id"]
            used_ids.add(record["id"])
        before = prior.get(record["id"], {})
        same = {k: v for k, v in before.items() if k not in temporal} == {
            k: v for k, v in record.items() if k not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", attempt) if same else attempt,
            last_seen_at=attempt,
            last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records, allow_empty=True)


def run_body(publishing):
    attempt = utc_now()
    snapshot = None
    old, previous, records, desired = [], {}, [], None
    failure = None
    try:
        with phase("prepublication", 2100):
            prepare_collection()
            if publishing:
                candidate = read_snapshot()
                if candidate["files"]["data.json"] is not None:
                    candidate_records = json.loads(
                        candidate["files"]["data.json"]
                    )
                    candidate_metadata = json.loads(
                        candidate["files"]["metadata.json"]
                    )
                    validate_records(candidate_records, allow_empty=True)
                    if (
                        not isinstance(candidate_metadata, dict)
                        or candidate_metadata.get("source") != SOURCE_ID
                    ):
                        raise AdapterError(
                            "Remote metadata source mismatch", "publish"
                        )
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            records = remove_expired_records(records, checked)
            fresh_records(records, old, attempt, checked)
            outcome = metadata(attempt, previous, records, checked_at=checked)
            desired = {
                "data.json": serialize(records),
                "metadata.json": serialize(outcome),
            }
    except PhaseExpired:
        failure = phase_error("prepublication")
    except Exception as error:
        failure = error
    if failure is None and publishing:
        try:
            with phase("publication", 180):
                publish_files(snapshot, desired)
        except PhaseExpired:
            failure = phase_error("publication")
        except Exception as error:
            failure = error
    if failure is not None:
        outcome = metadata(attempt, previous, old, failure)
        if publishing:
            try:
                with phase("failure", 180):
                    if snapshot is None:
                        raise AdapterError(
                            "No validated previous source pair", "publish"
                        )
                    latest = read_snapshot()
                    if desired is not None and latest["files"] == desired:
                        outcome = json.loads(desired["metadata.json"])
                        failure = None
                    elif (snapshot["files"]["data.json"] is not None
                          and latest["files"] == snapshot["files"]):
                        publish_files(
                            latest,
                            {
                                "metadata.json": serialize(outcome),
                            },
                        )
                    else:
                        raise AdapterError(
                            "No safe unchanged last-good pair", "publish"
                        )
            except (Exception, PhaseExpired):
                outcome["message"] = (
                    "Run failed; durable outcome unconfirmed. "
                    "No newer source pair was overwritten."
                )
        elif not old:
            outcome["message"] = "Collection failed; no publication attempted"
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure is not None else 0


def run_lifecycle(publishing):
    result = None
    try:
        result = run_body(publishing)
    except PhaseExpired:
        result = 1
    finally:
        try:
            with phase("export", EXPORT_TIMEOUT):
                export_family_artifact()
        except (Exception, PhaseExpired):
            try:
                arm_alarm(_RUN_END)
                print(
                    "Timing artifact export failed; next run fail closed",
                    file=sys.stderr,
                )
            except (Exception, PhaseExpired):
                pass
            if result == 0:
                result = 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        with execution():
            return run_lifecycle(args.publish)
    except PhaseExpired:
        return 1


if __name__ == "__main__":
    sys.exit(main())
