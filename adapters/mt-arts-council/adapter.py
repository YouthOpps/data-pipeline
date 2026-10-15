"""Index the complete official Arts Council Malta opportunity listings."""

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
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "mt-arts-council"
SOURCE_URL = "https://artscouncilmalta.gov.mt/en/apply-now/"
WEBSITE_URL = "https://artscouncilmalta.gov.mt/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "MT"
PUBLISHER_TYPE = "government-agency"
ATTRIBUTION = "Arts Council Malta https://artscouncilmalta.gov.mt/ and the named programme administrators"

_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
_PUBLISHER_GATE_FAILED = False
CATEGORIES = {
    "scholarships",
    "grants",
    "internships",
    "jobs",
    "training",
    "fellowships",
    "competitions",
    "other",
    "volunteering",
}
FAMILY = "youthopps-artscouncilmalta-publisher-v1"
FAMILY_SOURCES = {"mt-arts-council"}
_REVIEWED_LOCAL_NOT_BEFORE = 1792070136.1917381
_REVIEWED_NATIVE_STATE = {
    "schema": 2,
    "family": "youthopps-artscouncilmalta-publisher-v1",
    "observed_at": 1792070119.53849,
    "not_before": 1792069839.148789,
    "starts": [1792070073.9316003, 1792070105.28068, 1792070118.2923744],
    "blocked": False,
    "interval": 12.1,
    "page_interval": 30,
    "rate_failures": 0,
    "completed_at": 1792070119.53849,
    "inventory_completed_at": 1792070106.1917381,
}
COLLECTION_TIMEOUT = 2100
RUN_TIMEOUT = 2490
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 2
_STATE_NEW = None
_ARTIFACT_NAME = "artscouncilmalta-pacing-state"
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
        attrs = list(attrs)
        self.attrs = dict(attrs)
        self.duplicate_attrs = len(self.attrs) != len(attrs)
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
    """Upgrade legacy limits without clearing history, backoff or refusals."""
    legacy = {
        "schema",
        "family",
        "observed_at",
        "not_before",
        "starts",
        "blocked",
    }
    if (
        isinstance(state, dict)
        and type(state.get("schema")) is int
        and state.get("schema") == 1
        and set(state) == legacy
    ):
        state = {
            **state,
            "schema": 2,
            "interval": 12.1,
            "page_interval": 30,
            "rate_failures": 0,
            "completed_at": 0,
            "inventory_completed_at": 0,
        }
    fields = legacy | {
        "interval",
        "page_interval",
        "rate_failures",
        "completed_at",
        "inventory_completed_at",
    }
    if (
        not isinstance(state, dict)
        or set(state) != fields
        or type(state["schema"]) is not int
        or state["schema"] != 2
        or state["family"] != FAMILY
        or any(
            not numeric(state[k])
            for k in [
                "observed_at",
                "not_before",
                "interval",
                "page_interval",
                "completed_at",
                "inventory_completed_at",
            ]
        )
        or state["interval"] < 12
        or state["page_interval"] < 30
        or type(state["rate_failures"]) is not int
        or state["rate_failures"] < 0
        or type(state["blocked"]) is not bool
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 10
        or any(
            not numeric(v) or v > state["observed_at"] for v in state["starts"]
        )
        or sorted(set(state["starts"])) != state["starts"]
        or state["observed_at"] > time.time() + 1
        or state["completed_at"] > state["observed_at"]
        or state["inventory_completed_at"] > state["observed_at"]
    ):
        raise AdapterError("Invalid retained publisher state", "access")
    return state


def empty_budget(now):
    return {
        "schema": 2,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12.1,
        "page_interval": 30,
        "rate_failures": 0,
        "completed_at": 0,
        "inventory_completed_at": 0,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_NEW
    if _STATE_PATH:
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + ("-") + str(os.getuid())
    )
    try:
        os.mkdir(directory, 0o700)
        _STATE_NEW = True
    except FileExistsError:
        _STATE_NEW = False
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
        if not _STATE_NEW:
            raise AdapterError(
                "Mature publisher journal missing", "access"
            ) from None
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
    global _STATE_NEW
    state = validate_budget(state)
    fd, name = tempfile.mkstemp(
        prefix=("pending-"), dir=os.path.dirname(_STATE_PATH)
    )
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, _STATE_PATH)
        _STATE_NEW = False
    finally:
        if os.path.exists(name):
            os.unlink(name)


def pace(inventory=False):
    """Keep real starts within durable physical and inventory-page limits."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    check_deadline()
    with locked_budget():
        state = load_budget()
        if state["blocked"]:
            raise AdapterError(
                "Publisher refusal requires reviewed recovery", "access"
            )
        now = time.time()
        if state["not_before"] > now + 120:
            raise AdapterError(
                "Retained embargo requires a later ordinary attempt", "access"
            )
        starts = [v for v in state["starts"] if v > now - 60]
        target = max(
            now,
            state["not_before"],
            state["completed_at"] + state["interval"],
            starts[-1] + state["interval"] if starts else 0,
            starts[-5] + 60 if len(starts) >= 5 else 0,
            (
                state["inventory_completed_at"] + state["page_interval"]
                if inventory
                else 0
            ),
        )
        if target + 120 > time.time() + (_PHASE_END - time.monotonic()):
            raise AdapterError(
                "Retained publisher backoff exceeds phase budget", "access"
            )
        if (
            _COLLECTION_STARTED is not None
            and target + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError(
                "Retained publisher backoff exceeds collection budget",
                "access",
            )
        if target > now:
            time.sleep(target - now)
        now = time.time()
        if now + 0.001 < target:
            raise AdapterError("Publisher clock moved backwards", "access")
        state["starts"] = [v for v in starts if v > now - 60] + [now]
        state["observed_at"] = now
        save_budget(state)


def record_retry_after(value, rate_limited=False):
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
            if not rate_limited:
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


def transport_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
    purpose="content",
    inventory=False,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    if publisher:
        byte_limit = min(byte_limit, 8_000_000)
    headers = {**(headers or {}), "User-Agent": _USER_AGENT}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            pace(inventory=inventory)
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        if publisher:
            print(
                json.dumps(
                    {
                        "event": "request_start",
                        "url": current,
                        "time": time.time(),
                        "user_agent": request.get_header("User-agent"),
                    }
                ),
                file=sys.stderr,
                flush=True,
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
                record_retry_after(retry, rate_limited=status == 429)
            if publisher and status == 429:
                with locked_budget():
                    state = load_budget()
                    state["rate_failures"] += 1
                    state["not_before"] = max(
                        state["not_before"],
                        time.time()
                        + 1800 * 2 ** min(state["rate_failures"] - 1, 20),
                    )
                    state["interval"] = max(24, state["interval"] * 2)
                    state["page_interval"] = max(
                        60, state["page_interval"] * 2
                    )
                    state["observed_at"] = time.time()
                    save_budget(state)
                raise AdapterError(
                    "Publisher rate limited; adaptive embargo retained",
                    "access",
                    status,
                )
            if publisher and purpose != "robots" and status in (401, 403, 451):
                with locked_budget():
                    state = load_budget()
                    state["blocked"] = True
                    state["observed_at"] = time.time()
                    save_budget(state)
                refused = urllib.parse.urlsplit(current)
                provenance = urllib.parse.urlunsplit(
                    (
                        refused.scheme,
                        refused.hostname or "",
                        refused.path,
                        "",
                        "",
                    )
                )
                raise AdapterError(
                    f"Publisher refused access: HTTP {status}: " + provenance,
                    "access",
                    status,
                )
            location = response.headers.get("Location")
            is_redirect = status in (301, 302, 303, 307, 308)
            if is_redirect and publisher:
                raise AdapterError(
                    "Unreviewed publisher redirect", "access", status
                )
            if is_redirect:
                body = b""
            else:
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
                        raise publisher_transport_error(
                            error, current
                        ) from None
                    raise
                if (
                    len(body) > byte_limit
                    or declared
                    and len(body) != int(declared)
                ):
                    raise AdapterError(
                        "Incomplete or oversized response", "fetch"
                    )
                encoding = response.headers.get("Content-Encoding", "identity")
                if encoding != "identity":
                    raise AdapterError(
                        ("Unsupported response content encoding"), ("fetch")
                    )
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
    values = [getattr(policy, directive)(_USER_AGENT) for policy in policies]
    values = [value for value in values if value is not None]
    if not values:
        return None
    if directive == "crawl_delay":
        return max(values)
    if any(rate.requests <= 0 for rate in values):
        raise AdapterError("Invalid robots request rate", "access")
    return max(values, key=lambda rate: rate.seconds / rate.requests)


def check_robots(url):
    """Unavailable robots4xx alone is distinct from actual content refusal."""
    import urllib.robotparser

    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.path == "/robots.txt":
        return
    if origin not in _ROBOTS:
        status, _, body = request_bytes(
            origin + "/robots.txt", publisher=True, purpose="robots"
        )
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
        elif status == 200:
            text = body.decode("utf-8", "strict")
            meaningful = [
                line.split("#", 1)[0].strip()
                for line in text.splitlines()
                if line.split("#", 1)[0].strip()
            ]
            if (
                meaningful
                and not any(
                    re.match(r"user-agent\s*:", line, re.I)
                    for line in meaningful
                )
                and not all(
                    re.match(r"sitemap\s*:", line, re.I) for line in meaningful
                )
            ):
                raise AdapterError(
                    "Invalid/unavailable robots policy", "access"
                )
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(text.splitlines())
            _ROBOTS[origin] = parser
        else:
            raise AdapterError(
                "Robots unreachable; access not inferred", "access", status
            )
    parser = _ROBOTS[origin]
    if parser is not None and not robots_directive(parser, "can_fetch", url):
        with locked_budget():
            state = load_budget()
            state["blocked"] = True
            state["observed_at"] = time.time()
            save_budget(state)
        raise AdapterError(
            "Explicit robots disallows required listing", "access"
        )
    if parser is not None:
        delay = robots_directive(parser, "crawl_delay") or 0
        rate = robots_directive(parser, "request_rate")
        spacing = max(12.1, delay, rate.seconds / rate.requests if rate else 0)
        with locked_budget():
            state = load_budget()
            state["interval"] = max(state["interval"], spacing)
            state["observed_at"] = time.time()
            save_budget(state)


def fetch_public(url, inventory=False):
    """Read a complete native listing or its explicitly observed loader asset."""
    reviewed = public_url(url)
    check_robots(reviewed)
    return request_bytes(
        reviewed, publisher=True, byte_limit=3_000_000, inventory=inventory
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


def run_completion(run):
    """Order exact completed attempts by their actual job completion times."""
    identity, attempt = run["id"], run["run_attempt"]
    response = workflow_api(
        f"/actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = response.get("jobs")
    if (
        not isinstance(jobs, list)
        or not jobs
        or len(jobs) > 100
        or type(response.get("total_count")) is not int
        or response["total_count"] != len(jobs)
    ):
        raise AdapterError(
            "Incomplete completed-attempt job inventory", "access"
        )
    completed, began = [], []
    for job in jobs:
        try:
            if (
                not isinstance(job, dict)
                or job.get("status") != "completed"
                or job.get("run_id", identity) != identity
                or job.get("run_attempt", attempt) != attempt
                or job.get("head_sha", run["head_sha"]) != run["head_sha"]
            ):
                raise ValueError()
            end = datetime.fromisoformat(
                job["completed_at"].replace("Z", "+00:00")
            )
            if (
                end.utcoffset() is None
                or not 0 <= end.timestamp() <= time.time() + 1
            ):
                raise ValueError()
            start = datetime.fromisoformat(
                job.get("started_at", job["completed_at"]).replace(
                    "Z", "+00:00"
                )
            )
            if (
                start.utcoffset() is None
                or not 0 <= start.timestamp() <= end.timestamp()
            ):
                raise ValueError()
            completed.append(end.timestamp())
            began.append(start.timestamp())
        except (ValueError, TypeError, AttributeError, KeyError):
            raise AdapterError(
                "Invalid actual job completion chronology", "access"
            ) from None
    return max(completed), min(began)


def completed_identity(run, expected=None, expected_head=None):
    """Bind a completed attempt to its source, immutable head and actual jobs."""
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
            or run["head_repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_branch"] != "main"
            or not isinstance(run.get("head_sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            or expected is not None
            and (identity, attempt, source) != expected
            or expected_head is not None
            and run["head_sha"] != expected_head
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise AdapterError(
            "Untrusted completed source attempt", "access"
        ) from None
    finished, began = run_completion(run)
    return finished, began, identity, attempt, source


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


def download_reviewed_log(job_id):
    """Read a bounded complete job log without forwarding API credentials."""
    request = urllib.request.Request(
        _WORKFLOW_GITHUB + f"/actions/jobs/{job_id}/logs",
        headers={
            "Authorization": "Bearer " + os.environ.get("GITHUB_TOKEN", ""),
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
                raise ValueError()
            destination = response.headers.get("Location", "")
            response.read(1001)
        parsed = urllib.parse.urlsplit(destination)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or "." not in parsed.hostname
            or parsed.hostname.endswith((".local", ".internal", ".localhost"))
        ):
            raise ValueError()
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError()
        status, _, body = request_bytes(destination, byte_limit=3_000_000)
        if status != 200:
            raise ValueError()
        return body
    except Exception:
        raise AdapterError(
            "Reviewed recovery log unavailable", "access"
        ) from None


def reviewed_zero_publisher_attempt(run):
    """Exclude only two immutable restore failures proven to precede traffic."""
    pins = {
        38745452175: (
            "push",
            116457775228,
            "31a65cc300105e0fbff4dfb3c033b6eb692e2c62676eb671eb33c95d859b592d",
            "ef649323afdd494683c0a69e568eb77b03f904667c571571ddf3c5369ac3839a",
            "Robots migration cannot replace a new attempt's state",
        ),
        38745452432: (
            "workflow_dispatch",
            116458268088,
            "06e5982a8238811dffefaba06cb3054ca99bc0b04eeab66c77f2a24e9e0b1ab4",
            "ae84a45299db996b493d68f37975649c6a5197be9e515060b2085b3c27d64c70",
            "Latest exact attempt pacing artifact missing or expired",
        ),
    }
    identity = run.get("id")
    if identity not in pins:
        return False
    event, job_id, jobs_digest, log_digest, failure = pins[identity]
    if (
        run.get("run_attempt") != 1
        or run.get("head_sha") != "e60eab85cf26eb1bfa6b0c9eaa93d8f13924b5fc"
        or run.get("event") != event
        or run.get("workflow_id") != 380704472
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
    ):
        raise AdapterError("Reviewed zero-publisher identity changed", "access")
    completed_identity(run, (identity, 1, SOURCE_ID))
    jobs = workflow_api(
        f"/actions/runs/{identity}/attempts/1/jobs?per_page=100"
    )
    canonical = json.dumps(jobs, sort_keys=True, separators=(",", ":")).encode()
    if (
        jobs.get("total_count") != 1
        or len(jobs.get("jobs", [])) != 1
        or jobs["jobs"][0].get("id") != job_id
        or hashlib.sha256(canonical).hexdigest() != jobs_digest
    ):
        raise AdapterError("Reviewed zero-publisher jobs changed", "access")
    artifacts = workflow_api(f"/actions/runs/{identity}/artifacts?per_page=100")
    if artifacts.get("total_count") != 0 or artifacts.get("artifacts") != []:
        raise AdapterError(
            "Reviewed zero-publisher artifacts changed", "access"
        )
    log = download_reviewed_log(job_id)
    if (
        hashlib.sha256(log).hexdigest() != log_digest
        or failure.encode() not in log
        or b'"request_start"' in log
        or b'"request_complete"' in log
    ):
        raise AdapterError("Reviewed zero-publisher log changed", "access")
    return True


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    current_id, current_attempt = current_run_identity()
    candidates = []
    completed_rows = {}
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
            if (
                type(identity) is not int
                or identity <= 0
                or type(attempt) is not int
                or attempt <= 0
                or not isinstance(run.get("head_sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            ):
                raise AdapterError("Invalid family run identity", "access")
            if identity in {38745452175, 38745452432} and attempt != 1:
                raise AdapterError(
                    "Reviewed recovery attempt changed", "access"
                )
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
                    completed_rows[(identity, current_attempt - 1)] = prior
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, current_attempt - 1, source),
                            expected_head=run["head_sha"],
                        )
                    )
                continue
            if run.get(("status")) in (
                ("queued"),
                ("waiting"),
                ("pending"),
                ("requested"),
            ):
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    completed_rows[(identity, attempt - 1)] = prior
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, attempt - 1, source),
                            expected_head=run["head_sha"],
                        )
                    )
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            completed_rows[(identity, attempt)] = run
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError(
                    ("Workflow inventory count mismatch"), ("access")
                )
            if not candidates:
                return None
            ordered = sorted(candidates)
            while ordered:
                if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                    raise AdapterError(
                        "Ambiguous family attempt chronology", "access"
                    )
                selected = ordered[-1]
                row = completed_rows[(selected[2], selected[3])]
                if not reviewed_zero_publisher_attempt(row):
                    return selected[2:]
                ordered.pop()
            return None
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
    """Require newest exact trusted state; only reviewed original robots hold migrates."""
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raise AdapterError(
            "Known existing family history missing; no empty bootstrap",
            "access",
        )
    run_id, attempt, _ = latest
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or len(artifacts) > 100
        or result.get("total_count") != len(artifacts)
    ):
        raise AdapterError("Incomplete retained artifact inventory", "access")
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Latest exact attempt pacing artifact missing or expired", "access"
        )
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or not numeric(artifact.get("size_in_bytes"))
        or artifact["size_in_bytes"] > 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
    ):
        raise AdapterError("Invalid retained artifact owner/digest", "access")
    incoming = download_inert_artifact(
        latest, artifact["id"], artifact["digest"]
    )
    if run_id in {38073040828, 38073085228}:
        incoming = migrate_robots_hold(latest, incoming, artifact)
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined["not_before"] = max(
            local["not_before"], incoming["not_before"], now + 60
        )
        for key in (
            "interval",
            "page_interval",
            "rate_failures",
            "completed_at",
            "inventory_completed_at",
        ):
            combined[key] = max(local[key], incoming[key])
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined["starts"] = sorted(set(local["starts"] + incoming["starts"]))[
            -10:
        ]
        save_budget(combined)
    _RESTORED, _STATE_TRUSTED = True, True


def invalidate_artifact(parent, name):
    """Invalidate only a safely owned artifact, before attempting unlink."""
    try:
        descriptor = os.open(
            name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent
        )
    except FileNotFoundError:
        return
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe existing pacing artifact", "access")
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def artifact_output(path):
    """Keep a pinned output descriptor until export and signaling succeed."""
    global _PUBLISHER_GATE_FAILED
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    name = os.path.basename(path)
    try:
        info = os.fstat(parent)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o022
            or info.st_mode & 0o700 != 0o700
        ):
            raise AdapterError("Unsafe pacing artifact directory", "access")
        invalidate_artifact(parent, name)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion cannot be exported", "access"
            )
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            yield descriptor
            os.fsync(descriptor)
            os.fsync(parent)
        except BaseException:
            _PUBLISHER_GATE_FAILED = True
            # Keep the exact owned descriptor open until failure cleanup.
            # Truncate before unlink: failed unlink still leaves invalid JSON.
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            with contextlib.suppress(Exception):
                signal = os.open(
                    os.environ["GITHUB_OUTPUT"],
                    os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
                )
                with os.fdopen(signal, "a") as output:
                    info = os.fstat(output.fileno())
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                    ):
                        raise AdapterError("Unsafe readiness output", "access")
                    output.write("pacing_ready=false\n")
                    output.flush()
                    os.fsync(output.fileno())
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            raise

    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("ARTSCOUNCILMALTA_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    with artifact_output(path) as descriptor:
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
        with os.fdopen(os.dup(descriptor), "w") as file:
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
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
                raise AdapterError("Unsafe workflow output signal", "access")
            output.write("pacing_ready=true\n")
            output.flush()
            os.fsync(output.fileno())


def prepare_collection():
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
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
    with locked_budget():
        state = load_budget()
        state["not_before"] = max(state["not_before"], time.time() + 60)
        state["observed_at"] = time.time()
        save_budget(state)
    _STATE_TRUSTED = True


def document_root(text, complete=True):
    """Reject primary visible refusals, including outside native card containers."""
    root = PublisherHTML(text).root

    def primary_text(node):
        if (
            node.tag
            in {
                "head",
                "header",
                "footer",
                "nav",
                "aside",
                "noscript",
                "script",
                "style",
            }
            or node.attrs.get("role")
            in {"banner", "contentinfo", "navigation", "complementary"}
            or node.attrs.get("data-elementor-type") in {"header", "footer"}
            or {"elementor-location-header", "elementor-location-footer"}
            & set(node.attrs.get("class", "").split())
        ):
            return ""
        return " ".join(
            primary_text(child) if isinstance(child, Node) else child
            for child in node.children
        )

    visible = " ".join(primary_text(root).split())
    title = " ".join(
        n.text() for head in nodes(root, "head") for n in nodes(head, "title")
    )
    challenge = r"verify (?:that )?you are human|confirm (?:that )?you are (?:a )?human|access (?:denied|blocked)|request (?:has been )?(?:blocked|denied)|checking your browser|just a moment|unusual traffic"
    if re.search(challenge, visible, re.I) or re.match(challenge, title, re.I):
        with locked_budget():
            state = load_budget()
            state["blocked"] = True
            state["observed_at"] = time.time()
            save_budget(state)
        raise AdapterError(
            "Publisher returned a visible content challenge", "access"
        )
    if complete and not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I
    ):
        raise AdapterError("Incomplete public listing HTML", "parse")
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
        "id": SOURCE_ID + "-" + hashlib.sha256(url.encode()).hexdigest()[:24],
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
    """Filter a validated refresh before its atomic replacement publication."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Cleanup requires an aware check time", "validate")
    retained = []
    for record in records:
        # Fresh parser status expired means an explicit source closure, not age.
        if record.get("status") == "expired":
            continue
        value = record.get("deadline")
        expired = False
        if isinstance(value, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    # An undocumented zone cannot justify an earlier cutoff.
                    closing_date = datetime.strptime(value, "%Y-%m-%d").date()
                    latest_zone = timezone(timedelta(hours=-12))
                    expired = (
                        checked.astimezone(latest_zone).date() > closing_date
                    )
                else:
                    deadline = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = (
                        deadline.tzinfo is not None and checked > deadline
                    )
            except (ValueError, TypeError):
                # Missing or ambiguous deadlines never imply expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, published=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not published):
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
        if record["id"].casefold() in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].casefold())
        canonical_url(record["url"])
        expected = (
            SOURCE_ID
            + "-"
            + hashlib.sha256(record["url"].encode()).hexdigest()[:24]
        )
        if record["id"] != expected:
            raise AdapterError(
                "Record identity/canonical mismatch", "validate"
            )
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
        clocks = {
            field: datetime.fromisoformat(record[field].replace("Z", "+00:00"))
            for field in (
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
            )
        }
        if not (
            clocks["created_at"]
            <= clocks["updated_at"]
            <= clocks["last_checked_at"]
            and clocks["first_seen_at"]
            <= clocks["last_seen_at"]
            <= clocks["last_checked_at"]
        ):
            raise AdapterError(
                "Incoherent owned record timestamp chronology", "validate"
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


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "display_name": "Arts Council Malta",
        "source": SOURCE_ID,
        "name": "Arts Council Malta — official funding, training, jobs and programme overviews",
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
                "reviewed Arts Council Malta opportunities"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


def validate_prior_metadata(previous, records):
    """Accept failure writes only after the complete owned last-good pair."""
    if (
        not isinstance(previous, dict)
        or not {
            "source",
            "name",
            "source_url",
            "website_url",
            "language",
            "publisher_country",
            "publisher_type",
            "attribution",
            "status",
            "last_attempt_at",
            "last_success_at",
            "last_checked_at",
            "record_count",
            "message",
            "error",
        }
        <= set(previous)
        or previous.get("publisher_type") != PUBLISHER_TYPE
        or previous.get("attribution") != ATTRIBUTION
        or previous.get("name")
        != "Arts Council Malta — official funding, training, jobs and programme overviews"
        or previous.get("source") != SOURCE_ID
        or previous.get("source_url") != SOURCE_URL
        or previous.get("website_url") != WEBSITE_URL
        or not isinstance(previous.get("message"), str)
        or previous.get("status") not in {"success", "fail"}
        or type(previous.get("record_count")) is not int
        or previous["record_count"] != len(records)
        or previous.get("language") != LANGUAGE
        or previous.get("publisher_country") != PUBLISHER_COUNTRY
        or (
            previous.get("status") == "success"
            and previous.get("error") is not None
        )
        or (
            previous.get("status") == "fail"
            and (
                not isinstance(previous.get("error"), str)
                or not previous["error"].strip()
                or not isinstance(previous.get("failure_stage"), str)
                or not previous["failure_stage"].strip()
            )
        )
    ):
        raise AdapterError(
            "Invalid previous source metadata contract", "publish"
        )
    timestamps = {}
    try:
        for field in ("last_attempt_at", "last_success_at", "last_checked_at"):
            value = previous.get(field)
            if not isinstance(value, str):
                raise ValueError()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            timestamps[field] = parsed
        if timestamps["last_success_at"] > timestamps["last_checked_at"]:
            raise ValueError()
        if previous["status"] == "success":
            if timestamps["last_attempt_at"] != timestamps["last_success_at"]:
                raise ValueError()
        elif timestamps["last_attempt_at"] < timestamps["last_checked_at"]:
            raise ValueError()
        for record in records:
            parsed = datetime.fromisoformat(
                record["last_checked_at"].replace("Z", "+00:00")
            )
            if (
                parsed.tzinfo is None
                or parsed != timestamps["last_checked_at"]
            ):
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AdapterError(
            "Invalid previous metadata timestamps", "publish"
        ) from None


def phase_error(name):
    return AdapterError(
        "Whole " + name + " phase deadline exceeded",
        "publish" if name == "publication" else "access",
    )


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def fresh_records(records, old, attempt, checked):
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
        same = {k: v for k, v in before.items() if k not in temporal} == {
            k: v for k, v in record.items() if k not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", checked) if same else checked,
            last_seen_at=checked,
            last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records)


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
                    validate_records(candidate_records, published=True)
                    validate_prior_metadata(
                        candidate_metadata, candidate_records
                    )
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            fresh_records(records, old, attempt, checked)
            records = remove_expired_records(records, checked)
            validate_records(records, published=True)
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
                    elif (
                        snapshot["files"]["data.json"] is not None
                        and latest["files"] == snapshot["files"]
                    ):
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


def public_url(value):
    """Allow only observed official listing, robots and client-loader routes."""
    parsed = urllib.parse.urlsplit(value)
    valid_path = (
        parsed.path == "/robots.txt"
        or parsed.path == "/en/opportunities/"
        or re.fullmatch(
            r"/en/apply-now/(?:(?:[2-9]|[1-9][0-9]+)/)?", parsed.path
        )
        or parsed.path == SCRIPT_PATH
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "artscouncilmalta.gov.mt"
        or not valid_path
        or parsed.fragment
        or parsed.query
        and not (
            parsed.path == SCRIPT_PATH
            and re.fullmatch(r"ver=[0-9.]+", parsed.query)
        )
    ):
        raise AdapterError("Unreviewed source retrieval route", "access")
    return value


def collect():
    """Retrieve every native listing page without publishing or detail crawling."""
    if _PHASE_END is not None:
        check_deadline()
        return collect_listings()
    with execution():
        with phase("prepublication", 2100):
            prepare_collection()
            return collect_listings()


ROBOTS_URL = "https://artscouncilmalta.gov.mt/robots.txt"
FUNDING_URL = "https://artscouncilmalta.gov.mt/en/apply-now/"
OPPORTUNITIES_URL = "https://artscouncilmalta.gov.mt/en/opportunities/"
SCRIPT_PATH = "/wp-content/themes/hello-elementor-child/assets/js/script.js"


def request_bytes(url, **kwargs):
    """Retain a completion guard even after HTTP, TLS or body-read failure."""
    global _PUBLISHER_GATE_FAILED
    publisher = kwargs.get("publisher", False)
    if publisher and _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    try:
        return transport_bytes(url, **kwargs)
    finally:
        if publisher:
            _PUBLISHER_GATE_FAILED = True
            completed = time.time()
            with locked_budget():
                state = load_budget()
                state["completed_at"] = completed
                if kwargs.get("inventory"):
                    state["inventory_completed_at"] = completed
                state["observed_at"] = completed
                save_budget(state)
            _PUBLISHER_GATE_FAILED = False
            print(
                json.dumps(
                    {
                        "event": "request_complete",
                        "url": url,
                        "time": completed,
                    }
                ),
                file=sys.stderr,
                flush=True,
            )


def canonical_url(value):
    """Validate a literal official programme/call link without fetching it."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "artscouncilmalta.gov.mt"
        or not re.fullmatch(
            r"/en/(?:funding-and-grants|opportunitie)/[^/?#]+/", parsed.path
        )
        or parsed.query
        or parsed.fragment
    ):
        raise AdapterError(
            "Unsafe or non-opportunity listing link", "validate"
        )
    return value


def listing_record(
    title, url, listing, funding=False, closed=False, deadline=None
):
    """Keep only facts established by the native card, without inferred terms."""
    canonical_url(url)
    category = "grants" if funding else "other"
    if not funding:
        if re.search(
            r"\b(?:position|administrator|coordinator|manager|director|officer|evaluators|call for service|executive)\b",
            title,
            re.I,
        ):
            category = "jobs"
        elif re.search(r"\btraineeship\b", title, re.I):
            category = "internships"
        elif re.search(
            r"\b(?:training|workshops?|masterclass)\b", title, re.I
        ):
            category = "training"
    kind = (
        "opportunity"
        if funding
        or re.search(
            r"\b(?:call|applications?|position|traineeship)\b", title, re.I
        )
        else "programme-overview"
    )
    record = make_record(
        title,
        url,
        [category],
        kind=kind,
        method="official-listing",
        evidence=[
            listing,
            "Literal publisher card title and canonical link; detail fields not collected.",
        ],
    )
    record["language"] = None
    record["summary_language"] = "en"
    record["summary"] = (
        "Arts Council Malta lists this "
        + (
            "funding programme or call"
            if funding
            else "opportunity or programme"
        )
        + ". "
        + ("The publisher marks applications closed. " if closed else "")
        + "See the official page for eligibility, application details and current availability."
    )
    record["status"] = "expired" if closed else "unknown"
    record["deadline"] = deadline
    if deadline:
        record["classification"]["evidence"].append(
            "Explicit card application Deadline: "
            + deadline
            + "; no closing time or zone is supplied."
        )
    record["tags"] = ["arts", "culture", "listing-facts-only"]
    return record


# These titles explicitly identify directories, fiscal advice or public attendance,
# rather than an applicant call. Unknown future rows are not dropped by a count cap.
_EXCLUSIONS = {
    "General Budget Guidelines": "generic funding guidance",
    "National Opportunities": "generic opportunity directory",
    "Open Calls": "generic call directory without an individual identity",
    "Reduced Tax Rate for Creative Practitioners": "fiscal advice",
    "Tax Deduction on Fees paid for Cultural and Creative Courses": "fiscal deduction",
    "150% tax deduction on donations to culture – 2026": "fiscal deduction",
    "The European Film Festival 2026": "ordinary film/ticket attendance notice",
    "The State of the Arts – Malta National Symposium": "ordinary public-attendance notice",
    "Re:Create Europe": "programme news and external call catalogue referral",
    "ESF+.03.243 Strengthening Malta’s Cultural and Creative Sectors through Lifelong Learning": "umbrella initiative; named training and grants are separately listed",
}


def funding_cards(root, listing):
    cards = [
        n
        for n in root.walk()
        if "e-loop-item" in n.attrs.get("class", "").split()
    ]
    if not cards:
        raise AdapterError("Funding listing has no native cards", "parse")
    records, excluded = [], []
    for card in cards:
        classes = card.attrs.get("class", "").split()
        if (
            "type-funding" not in classes
            or len(
                [c for c in classes if re.fullmatch(r"post-[1-9][0-9]*", c)]
            )
            != 1
        ):
            raise AdapterError("Funding card native identity changed", "parse")
        title = one(nodes(card, "h1"), "funding card title").text()
        url = one(
            [n.attrs["href"] for n in nodes(card, "a") if n.attrs.get("href")],
            "funding canonical",
        )
        canonical_url(url)
        if title in _EXCLUSIONS:
            excluded.append(
                {"title": title, "url": url, "reason": _EXCLUSIONS[title]}
            )
            continue
        deadline = None
        headings = [n.text() for n in nodes(card, "h2")]
        if any(h.lower().strip() == "deadline:" for h in headings):
            values = [
                h for h in headings if re.fullmatch(r"\d{2}-\d{2}-\d{4}", h)
            ]
            raw = one(values, "explicit funding deadline")
            try:
                deadline = (
                    datetime.strptime(raw, "%d-%m-%Y").date().isoformat()
                )
            except ValueError:
                raise AdapterError(
                    "Invalid explicit listing deadline", "parse"
                ) from None
        closed = "funding_category-closed-for-applications" in classes
        if closed and "funding_category-open-for-applications" in classes:
            raise AdapterError(
                "Conflicting publisher application state", "parse"
            )
        record = listing_record(
            title, url, listing, funding=True, closed=closed, deadline=deadline
        )
        if "funding_category-open-for-applications" in classes:
            record["status"] = "open"
            record["summary"] = (
                "The publisher labels applications open. " + record["summary"]
            )
            record["classification"]["evidence"].append(
                "Publisher listing labels open for applications; this is not an inferred deadline."
            )
        records.append(record)
    return records, excluded, len(cards)


def opportunity_cards(root):
    grids = [
        n
        for n in root.walk()
        if {"grid", "publications"} <= set(n.attrs.get("class", "").split())
    ]
    grid = one(grids, "preloaded opportunity inventory")
    cards = [
        n
        for n in grid.walk()
        if "element-item" in n.attrs.get("class", "").split()
    ]
    if not cards:
        raise AdapterError("Opportunity listing has no cards", "parse")
    positions, records, excluded = [], [], []
    for card in cards:
        relevance = card.attrs.get("data-relevance", "")
        if (
            not re.fullmatch(r"[1-9][0-9]*", relevance)
            or card.attrs.get("data-category") != "post"
        ):
            raise AdapterError(
                "Native preloaded inventory ordinal changed", "parse"
            )
        positions.append(int(relevance))
        title = one(
            [
                n
                for n in nodes(card, "h3")
                if "title" in n.attrs.get("class", "").split()
            ],
            "opportunity card title",
        ).text()
        url = one(
            [n.attrs["href"] for n in nodes(card, "a") if n.attrs.get("href")],
            "opportunity canonical",
        )
        canonical_url(url)
        if title in _EXCLUSIONS:
            excluded.append(
                {"title": title, "url": url, "reason": _EXCLUSIONS[title]}
            )
            continue
        # data-date is the article/publication date, never an application cutoff.
        closed = bool(re.search(r"[–-]\s*closed\s*$", title, re.I))
        records.append(
            listing_record(title, url, OPPORTUNITIES_URL, closed=closed)
        )
    if positions != list(range(1, len(cards) + 1)):
        raise AdapterError(
            "Preloaded opportunity inventory has gaps or duplicates", "parse"
        )
    return records, excluded, len(cards)


def loader_script(root):
    """Follow the exact observed own loader asset, never a guessed API."""
    scripts = [
        n.attrs["src"]
        for n in nodes(root, "script")
        if n.attrs.get("src")
        and urllib.parse.urlsplit(n.attrs["src"]).path == SCRIPT_PATH
    ]
    return public_url(one(scripts, "own opportunity loader script"))


def verify_client_loader(script):
    """Prove both observed load-more handlers only reveal preloaded elements."""
    active = re.sub(r"//[^\n]*", "", script)
    handlers = re.findall(
        r'\$\("#load-more"\)\.click\(function\s*\(\)\s*\{([\s\S]*?)\}\);',
        active,
    )
    if (
        active.count('$("#load-more")') != 6
        or len(handlers) != 2
        or any(
            re.sub(r"\s+", "", body) != "counter+=initShow;loadMore(counter);"
            for body in handlers
        )
    ):
        raise AdapterError("Opportunity load-more handlers changed", "parse")
    blocks = re.findall(
        r"function loadMore\(toShow\)\s*\{([\s\S]*?)\n\s*function resetLoadMore",
        active,
    )
    if len(blocks) != 2:
        raise AdapterError(
            "Preloaded opportunity loader functions changed", "parse"
        )
    for block in blocks:
        if any(
            marker not in block
            for marker in [
                "iso.filteredItems",
                ".slice(toShow, filteredItems.length)",
                "item.element",
                '.addClass("hidden")',
                '.isotope("layout")',
            ]
        ):
            raise AdapterError(
                "Client-only complete opportunity inventory unproven", "parse"
            )
        if re.search(
            r"\b(?:fetch|ajax|XMLHttpRequest|post|getJSON)\s*\(", block
        ):
            raise AdapterError(
                "Unexpected network opportunity continuation", "parse"
            )


def collect_listings():
    """Exhaust declared funding pagination and the complete preloaded call hub."""
    records, exclusions, seen_pages, expected_max = [], [], set(), None
    url, page_number, funding_count = FUNDING_URL, 1, 0
    while True:
        if url in seen_pages:
            raise AdapterError("Funding pagination cycle", "parse")
        seen_pages.add(url)
        status, _, body = fetch_public(url, inventory=True)
        if status != 200:
            raise AdapterError(
                "Funding listing returned HTTP " + str(status), "fetch", status
            )
        root = document_root(body.decode("utf-8", "strict"))
        anchors = [
            n
            for n in root.walk()
            if "e-load-more-anchor" in n.attrs.get("class", "").split()
        ]
        anchor = one(anchors, "native funding pagination anchor")
        current, maximum = anchor.attrs.get("data-page", ""), anchor.attrs.get(
            "data-max-page", ""
        )
        if (
            not re.fullmatch(r"[1-9][0-9]*", current)
            or not re.fullmatch(r"[1-9][0-9]*", maximum)
            or int(current) != page_number
            or int(maximum) < page_number
        ):
            raise AdapterError("Funding pagination metadata changed", "parse")
        if expected_max is None:
            expected_max = int(maximum)
        if expected_max != int(maximum):
            raise AdapterError(
                "Funding inventory changed during complete scan", "parse"
            )
        rows, dropped, count = funding_cards(root, url)
        records.extend(rows)
        exclusions.extend(dropped)
        funding_count += count
        if page_number == expected_max:
            break
        following = anchor.attrs.get("data-next-page", "")
        if following != FUNDING_URL + str(page_number + 1) + "/":
            raise AdapterError("Unproven native funding continuation", "parse")
        url, page_number = public_url(following), page_number + 1
    status, _, body = fetch_public(OPPORTUNITIES_URL, inventory=True)
    if status != 200:
        raise AdapterError(
            "Opportunity hub returned HTTP " + str(status), "fetch", status
        )
    root = document_root(body.decode("utf-8", "strict"))
    rows, dropped, opportunity_count = opportunity_cards(root)
    script_url = loader_script(root)
    status, _, body = fetch_public(script_url)
    if status != 200:
        raise AdapterError(
            "Opportunity loader could not be verified", "fetch", status
        )
    verify_client_loader(body.decode("utf-8", "strict"))
    records.extend(rows)
    exclusions.extend(dropped)
    # These exact publisher crossposts identify the same listed Artivisti call.
    alias = "https://artscouncilmalta.gov.mt/en/opportunitie/artivisti/"
    canonical = (
        "https://artscouncilmalta.gov.mt/en/funding-and-grants/artivisti/"
    )
    canonical_rows = [r for r in records if r["url"] == canonical]
    alias_rows = [r for r in records if r["url"] == alias]
    if alias_rows:
        if (
            len(alias_rows) != 1
            or len(canonical_rows) != 1
            or not re.fullmatch(
                r"Artivisti(?: 2026)?", alias_rows[0]["title"], re.I
            )
            or canonical_rows[0]["title"].lower() != "artivisti"
        ):
            raise AdapterError(
                "Known Artivisti crosspost relationship changed", "parse"
            )
        canonical_rows[0]["classification"]["evidence"].append(alias)
        records = [r for r in records if r["url"] != alias]
        exclusions.append(
            {
                "url": alias,
                "reason": "same Artivisti call represented by funding canonical",
            }
        )
    validate_records(records)
    records.sort(key=lambda row: row["id"])
    print(
        json.dumps(
            {
                "event": "complete_inventory",
                "funding_pages": len(seen_pages),
                "funding_cards": funding_count,
                "opportunity_cards": opportunity_count,
                "records": len(records),
                "exclusions": exclusions,
            }
        ),
        file=sys.stderr,
        flush=True,
    )
    return records


def migrate_robots_hold(latest, incoming, artifact):
    """Apply only the independently reviewed exact original robots403 provenance."""
    old_head = "a97e28475634b9ff61489a11cd6378c9dafc67ae"
    pins = {
        38073040828: (
            11677184845,
            "sha256:38476379c439f6e907b2ee8787595ce1b3731f0c7add08477d0d1f1cceffaffa",
        ),
        38073085228: (
            11678320011,
            "sha256:7509f5ad862fe2460a925db4275942e4e766fcc809737e5751173f6b9d3bffb6",
        ),
    }
    if (
        latest != (38073085228, 1, SOURCE_ID)
        or (artifact["id"], artifact["digest"]) != pins[38073085228]
    ):
        raise AdapterError(
            "Original robots migration identity changed", "access"
        )
    current_id, _ = current_run_identity()
    result = workflow_api("/actions/workflows/380704472/runs?per_page=100")
    runs = result.get("workflow_runs")
    if not isinstance(runs, list) or result.get("total_count") != len(runs):
        raise AdapterError("Incomplete migration history", "access")
    historical = [
        run
        for run in runs
        if run.get("id") != current_id
        and not reviewed_zero_publisher_attempt(run)
    ]
    if len(historical) != 2 or {run.get("id") for run in historical} != set(
        pins
    ):
        raise AdapterError(
            "Robots migration cannot replace a new attempt's state", "access"
        )
    for run in historical:
        if (
            run.get("run_attempt") != 1
            or run.get("head_sha") != old_head
            or run.get("conclusion") != "failure"
        ):
            raise AdapterError("Original migration history changed", "access")
        completed_identity(run)
    first = workflow_api("/actions/runs/38073040828/artifacts?per_page=100")
    entries = first.get("artifacts")
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or first.get("total_count") != 1
        or entries[0].get("id") != pins[38073040828][0]
        or entries[0].get("digest") != pins[38073040828][1]
        or entries[0].get("expired") is not False
        or entries[0].get("workflow_run", {}).get("id") != 38073040828
    ):
        raise AdapterError(
            "Original refusal artifact unavailable/changed", "access"
        )
    first_state = download_inert_artifact(
        (38073040828, 1, SOURCE_ID), *pins[38073040828]
    )
    legacy = {
        "schema": 1,
        "family": FAMILY,
        "starts": [1791654420.0719957],
        "blocked": True,
    }
    expected_first = validate_budget(
        {
            **legacy,
            "observed_at": 1791654420.2602344,
            "not_before": 1791654420.0718863,
        }
    )
    expected_last = validate_budget(
        {
            **legacy,
            "observed_at": 1791654460.8156085,
            "not_before": 1791654520.8156075,
        }
    )
    if first_state != expected_first or incoming != expected_last:
        raise AdapterError(
            "Mixed/unknown refusal history cannot be reclassified", "access"
        )
    native = validate_budget(dict(_REVIEWED_NATIVE_STATE))
    combined = dict(incoming)
    for key in (
        "observed_at",
        "not_before",
        "interval",
        "page_interval",
        "rate_failures",
        "completed_at",
        "inventory_completed_at",
    ):
        combined[key] = max(first_state[key], incoming[key], native[key])
    combined["starts"] = sorted(
        set(first_state["starts"] + incoming["starts"] + native["starts"])
    )[-10:]
    # Only the exact two verified robots bits are reclassified; native/local
    # refusals remain binding. restore_family_artifact merges live local state.
    combined["blocked"] = native["blocked"]
    combined["not_before"] = max(
        combined["not_before"], _REVIEWED_LOCAL_NOT_BEFORE
    )
    return validate_budget(combined)


if __name__ == "__main__":
    sys.exit(main())
