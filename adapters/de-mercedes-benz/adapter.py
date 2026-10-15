"""Collect the complete reviewed public Mercedes-Benz youth vacancy inventory.

Original factual summaries and publisher qualification evidence.
Task descriptions and media are not republished.
All current descriptors and referenced conditions are validated.
"""

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

SOURCE_ID = "de-mercedes-benz"
SOURCE_URL = "https://jobs.mercedes-benz.com/en"
WEBSITE_URL = "https://jobs.mercedes-benz.com/"
LANGUAGE = "multi"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "company"
SOURCE_NAME = "Mercedes-Benz — bounded public youth vacancies"
ATTRIBUTION = (
    "Mercedes-Benz official public job portal; original factual summaries and "
    "direct links"
)
DESCRIPTION = (
    "Complete public youth vacancy inventory with minimal factual metadata."
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {
    "scholarships",
    "internships",
    "volunteering",
    "training",
    "jobs",
    "competitions",
    "grants",
    "fellowships",
    "other",
}
FAMILY = "youthopps-mercedes-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 1800
PUBLICATION_TIMEOUT = 240
FAILURE_TIMEOUT = 120
RUN_TIMEOUT = 2280
EXPORT_TIMEOUT = 120
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "mercedes-pacing-state"
MAX_PUBLISHER_STARTS = 42
MAX_PUBLISHER_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_REQUEST_STARTS = _PUBLISHER_BYTES = 0
_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_NEW = False
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_LEGACY_FDS = []
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12
_PUBLISHER_GATE_FAILED = False
_RUN_END = _PHASE_END = None
_PHASE_NAME = None


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    with locked_budget():
        gap = max(30, load_budget().get("page_interval", 30))
    if _PAGE_COMPLETED is not None:
        while True:
            wait = _PAGE_COMPLETED + gap - time.monotonic()
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
        while _LEGACY_FDS:
            os.close(_LEGACY_FDS.pop())
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


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) - {
        "interval",
        "robots_recoveries",
        "rate_limit_count",
        "page_interval",
    } != {
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
        or not numeric(state.get("interval", 6))
        or state.get("interval", 6) < 6
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
    page_interval = state.get("page_interval", 30)
    if not numeric(page_interval) or page_interval < 30:
        raise AdapterError("Invalid inventory page interval", "access")
    count = state.get("rate_limit_count", 0)
    if type(count) is not int or not 0 <= count <= 100:
        raise AdapterError("Invalid rate-limit history", "access")
    recoveries = state.get("robots_recoveries", [])
    if (
        not isinstance(recoveries, list)
        or any(
            value not in REVIEWED_ROBOTS_ONLY_STATES for value in recoveries
        )
        or len(recoveries) != len(set(recoveries))
    ):
        raise AdapterError(
            "Invalid reviewed robots recovery provenance", "access"
        )
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12,
        "page_interval": 30,
    }


def pace():
    (
        "Serialize every publisher request start across the complete "
        "origin set."
    )
    global _PUBLISHER_INTERVAL, _REQUEST_STARTS
    check_deadline()
    if _REQUEST_STARTS >= MAX_PUBLISHER_STARTS:
        raise AdapterError("Publisher request-start cap reached", "access")
    with locked_budget():
        state = load_budget()
        state["interval"] = max(12, state.get("interval", 6))
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        if state["blocked"]:
            raise AdapterError(
                ("Publisher backoff requires reviewed recovery"), ("access")
            )
        now = time.time()
        starts = [t for t in state["starts"] if t > now - 60]
        target = max(now, state["not_before"])
        if _PUBLISHER_COMPLETED_MONOTONIC is not None:
            target = max(
                target,
                now
                + max(
                    0,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                ),
            )
        if starts:
            target = max(target, starts[-1] + state.get("interval", 6))
        if len(starts) >= 5:
            target = max(target, starts[-5] + 60)
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
        while True:
            check_deadline()
            wait = max(0, target - time.time())
            if _PUBLISHER_COMPLETED_MONOTONIC is not None:
                wait = max(
                    wait,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                )
            if wait <= 0:
                break
            if wait + 120 > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Publisher backoff exceeds phase budget", "access"
                )
            if _COLLECTION_STARTED is not None and (
                time.time() + wait + 120
                > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Publisher backoff exceeds collection budget", "access"
                )
            time.sleep(wait)
        now = time.time()
        if now < target:
            raise AdapterError(
                ("Publisher pacing clock moved backwards"), ("access")
            )
        check_deadline()
        if (
            _PUBLISHER_COMPLETED_MONOTONIC is not None
            and time.monotonic()
            < _PUBLISHER_COMPLETED_MONOTONIC + _PUBLISHER_INTERVAL
        ):
            raise AdapterError(
                "Publisher physical completion gate not elapsed", "access"
            )
        state["starts"] = [t for t in starts if t > now - 60] + [now]
        state["not_before"] = max(
            state["not_before"], now + state.get("interval", 6)
        )
        state["observed_at"] = now
        save_budget(state)
        _REQUEST_STARTS += 1


@contextlib.contextmanager
def publisher_attempt(publisher):
    """Persist a completion gate around every physical publisher attempt."""
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED, _PUBLISHER_INTERVAL
    if not publisher:
        yield
        return
    if _COLLECTION_LOCK_FD is None or _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Exclusive healthy publisher collector required", "access"
        )
    check_deadline()
    if (
        _PUBLISHER_COMPLETED_MONOTONIC is not None
        and time.monotonic()
        < _PUBLISHER_COMPLETED_MONOTONIC + _PUBLISHER_INTERVAL
    ):
        raise AdapterError(
            "Publisher physical completion gate not elapsed", "access"
        )
    try:
        yield
    finally:
        _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
        _PUBLISHER_GATE_FAILED = True
        with locked_budget():
            state = load_budget()
            _PUBLISHER_INTERVAL = max(
                _PUBLISHER_INTERVAL, state.get("interval", 6)
            )
            now = time.time()
            state["not_before"] = max(
                state["not_before"], now + _PUBLISHER_INTERVAL
            )
            state["observed_at"] = now
            save_budget(state)
        _PUBLISHER_GATE_FAILED = False


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


class TransientPublisherError(AdapterError):
    """A bounded publisher timeout or connection failure, excluding TLS."""


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
    error_type = (
        TransientPublisherError
        if not isinstance(cause, ssl.SSLError)
        and isinstance(cause, (TimeoutError, ConnectionError))
        else AdapterError
    )
    return error_type(
        "Publisher transport failure at " + hop + " (" + category + ")",
        "fetch",
    )


def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=MAX_RESPONSE_BYTES,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    global _PUBLISHER_BYTES
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept-Encoding": "identity",
        **(headers or {}),
    }
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        with publisher_attempt(publisher):
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
                robots_unavailable = publisher and unavailable_robots(
                    current, status
                )
                if (
                    publisher
                    and status in (401, 403, 429)
                    and not robots_unavailable
                ):
                    with locked_budget():
                        state = load_budget()
                        if status == 429:
                            count = min(
                                100, state.get("rate_limit_count", 0) + 1
                            )
                            state["rate_limit_count"] = count
                            state["interval"] = (
                                max(12, state.get("interval", 6)) * 2
                            )
                            state["page_interval"] = (
                                max(30, state.get("page_interval", 30)) * 2
                            )
                            if not retry:
                                state["not_before"] = max(
                                    state["not_before"],
                                    time.time() + 1800 * 2 ** (count - 1),
                                )
                        else:
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
                        f"Publisher refused access: HTTP {status}: "
                        + provenance,
                        "access",
                        status,
                    )
                location = response.headers.get("Location")
                is_redirect = status in (301, 302, 303, 307, 308)
                if is_redirect and publisher:
                    if not location or redirect == 5:
                        raise AdapterError("Invalid redirect chain", "access")
                    destination = urllib.parse.urljoin(current, location)
                    public_url(destination)
                    if (
                        urllib.parse.urlsplit(current).scheme == "https"
                        and urllib.parse.urlsplit(destination).scheme
                        != "https"
                    ):
                        raise AdapterError(
                            "Publisher redirect transport downgrade", "access"
                        )
                if is_redirect:
                    body = b""
                else:
                    if publisher:
                        byte_limit = min(
                            byte_limit, MAX_PUBLISHER_BYTES - _PUBLISHER_BYTES
                        )
                        if byte_limit <= 0:
                            raise AdapterError(
                                "Whole publisher byte cap reached", "fetch"
                            )
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
                    if publisher:
                        _PUBLISHER_BYTES += len(body)
                    encoding = response.headers.get(
                        "Content-Encoding", "identity"
                    )
                    if encoding != "identity":
                        raise AdapterError(
                            ("Unsupported response content encoding"),
                            ("fetch"),
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


def completed_identity(run, expected=None, expected_head=None):
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
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
            or expected_head is not None
            and run["head_sha"] != expected_head
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
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100"
        )
        jobs = result.get("jobs")
        if (
            not isinstance(jobs, list)
            or not jobs
            or len(jobs) > 100
            or result.get("total_count") != len(jobs)
        ):
            raise ValueError("Incomplete completed-attempt jobs")
        starts, finishes = [], []
        for job in jobs:
            if (
                job.get("run_id") != identity
                or job.get("head_sha") != run["head_sha"]
                or job.get("run_attempt", attempt) != attempt
                or job.get("status") != "completed"
            ):
                raise ValueError("Unbound completed-attempt job")
            clocks = []
            for name in ("started_at", "completed_at"):
                value = datetime.fromisoformat(
                    job[name].replace("Z", "+00:00")
                )
                if value.utcoffset() is None:
                    raise ValueError("Missing completed-job timezone")
                clocks.append(value.timestamp())
            if not 0 <= clocks[0] <= clocks[1] <= time.time() + 1:
                raise ValueError("Invalid completed-job chronology")
            starts.append(clocks[0])
            finishes.append(clocks[1])
        return max(finishes), min(starts), identity, attempt, source
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
            if (
                type(identity) is not int
                or type(attempt) is not int
                or identity <= 0
                or attempt <= 0
            ):
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
                            prior,
                            (identity, current_attempt - 1, source),
                            run["head_sha"],
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
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, attempt - 1, source),
                            run["head_sha"],
                        )
                    )
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
        raw = os.environ.get("MERCEDES_PACING_BOOTSTRAP", "")
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
    merge_budget(incoming)
    _RESTORED, _STATE_TRUSTED = True, True


def mark_pacing_artifact_ready(descriptor, run_id, attempt):
    """Signal only a fully finalized healthy artifact for this run attempt."""
    if descriptor is None:
        return
    if _PUBLISHER_GATE_FAILED or not _STATE_TRUSTED:
        raise AdapterError(
            "Unhealthy pacing export cannot be signaled", "access"
        )
    payload = (
        f"pacing_run_id={run_id}\npacing_run_attempt={attempt}\n"
        "pacing_ready=true\n"
    ).encode()
    try:
        if os.write(descriptor, payload) != len(payload):
            raise AdapterError("Incomplete pacing export handoff", "access")
        os.fsync(descriptor)
    except BaseException:
        # Cleanup is best effort; the positive signal was never attempted
        # until the healthy, current-run artifact was finalized and verified.
        try:
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
        except OSError:
            pass
        raise


def export_family_artifact():
    (
        "Export only healthy infrastructure state; invalidate an owned stale "
        "file."
    )
    path = os.environ.get("MERCEDES_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError(
            "Explicit runner temporary directory required", "access"
        )
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    name = os.path.basename(path)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    output = None
    try:
        directory = os.fstat(parent)
        if (
            directory.st_uid != os.getuid()
            or not stat.S_ISDIR(directory.st_mode)
            or directory.st_mode & 0o022
            or directory.st_mode & 0o700 != 0o700
        ):
            raise AdapterError(
                "Unsafe pacing artifact directory owner", "access"
            )
        output = open_fresh_pacing_output(parent, root)
        existing = None
        try:
            existing = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            pass
        if existing is not None:
            try:
                info = os.fstat(existing)
                if (
                    info.st_uid != os.getuid()
                    or not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or info.st_nlink != 1
                ):
                    raise AdapterError(
                        "Unsafe existing pacing artifact owner", "access"
                    )
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (
                    info.st_dev,
                    info.st_ino,
                ):
                    raise AdapterError(
                        "Pacing artifact identity changed", "access"
                    )
                # Invalidate before unlink: an unlink failure cannot leave a
                # valid stale envelope on a writable, owned artifact inode.
                os.ftruncate(existing, 0)
                os.fsync(existing)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(existing)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion gate cannot be exported", "access"
            )
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
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump(envelope, file, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())
            os.fsync(parent)
            check = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(check)
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (
                    info.st_uid != os.getuid()
                    or not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                    or (info.st_dev, info.st_ino)
                    != (current.st_dev, current.st_ino)
                ):
                    raise AdapterError(
                        "Unsafe finalized artifact identity", "access"
                    )
                raw = os.read(check, 16385)
                if len(raw) > 16384 or json.loads(raw) != envelope:
                    raise AdapterError(
                        "Finalized pacing artifact mismatch", "access"
                    )
            finally:
                os.close(check)
            check_deadline()
            mark_pacing_artifact_ready(output, run_id, attempt)
        except BaseException:
            # An incomplete new file cannot masquerade as a valid artifact.
            os.unlink(name, dir_fd=parent)
            raise
    finally:
        if output is not None:
            os.close(output)
        os.close(parent)


def challenge_text(node):
    """Inspect visible primary content, excluding scripts and unrelated chrome."""
    if node.tag in {"script", "style", "noscript", "nav", "footer", "aside"}:
        return ""
    marker = (
        node.attrs.get("id", "") + " " + node.attrs.get("class", "")
    ).casefold()
    if any(
        value in marker
        for value in (
            "cookie-banner",
            "cookie-notice",
            "cookie-consent",
            "onetrust",
            "usercentrics",
        )
    ):
        return ""
    return " ".join(
        challenge_text(child) if isinstance(child, Node) else child
        for child in node.children
    )


def document_root(text, complete=True):
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    main = nodes(root, "main")
    bodies = nodes(root, "body")
    scope = (
        main[0] if len(main) == 1 else bodies[0] if len(bodies) == 1 else root
    )
    visible = " ".join(challenge_text(scope).split())
    title_challenge = re.match(
        r"just a moment|access denied|attention required|checking your browser|"
        r"verify you are human|captcha|robot check",
        title,
    )
    body_challenge = re.search(
        r"(?:complete|solve|enter|pass) (?:the |a )?(?:security )?captcha\b|"
        r"(?:verify|confirm|prove) (?:that )?(?:you are|you're) (?:a )?human\b|"
        r"enable (?:javascript|cookies)(?: and (?:javascript|cookies))? to continue|"
        r"(?:bestätigen|beweisen) Sie.{0,60}(?:Mensch|kein Roboter)",
        visible,
        re.I,
    )
    body_denial = re.search(
        r"\b(?:access\s*denied|permission denied|zugriff verweigert|"
        r"zugang verweigert|anfrage blockiert)\b|"
        r"\b(?:your|this|the) request (?:has been |was |is )?(?:blocked|denied)\b|"
        r"\byou (?:do not|don't) have permission to access\b|"
        r"\baccess to (?:this|the|requested) (?:page|resource|website|site) "
        r"(?:is |has been )?(?:denied|blocked)\b",
        visible,
        re.I,
    )
    if title_challenge or body_challenge or body_denial:
        with locked_budget():
            state = load_budget()
            state["blocked"] = True
            state["observed_at"] = time.time()
            save_budget(state)
        raise AdapterError("Publisher access challenge", "access")
    if complete and not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I
    ):
        raise AdapterError("Incomplete public HTML", "parse")
    return root


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


def phase_error(name):
    return AdapterError(
        "Whole " + name + " phase deadline exceeded",
        "publish" if name == "publication" else "access",
    )


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def run_body(publishing):
    attempt = utc_now()
    snapshot = None
    old, previous, records, desired = [], {}, [], None
    failure = None
    try:
        with phase("prepublication", COLLECTION_TIMEOUT):
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
                    validate_prior_metadata(
                        candidate_metadata, candidate_records
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
            with phase("publication", PUBLICATION_TIMEOUT):
                publish_files(snapshot, desired)
        except PhaseExpired:
            failure = phase_error("publication")
        except Exception as error:
            failure = error
    if failure is not None:
        outcome = metadata(attempt, previous, old, failure)
        if publishing:
            try:
                with phase("failure", FAILURE_TIMEOUT):
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


def verify_state_directory():
    """Bind the mature leaf to its retained descriptor before each operation."""
    info = os.fstat(_STATE_DIR_FD)
    current = os.stat(os.path.dirname(_STATE_PATH), follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_nlink < 2
        or (info.st_dev, info.st_ino) != _STATE_DIR_ID
        or (current.st_dev, current.st_ino) != _STATE_DIR_ID
    ):
        raise AdapterError(
            "Publisher state directory identity changed", "access"
        )


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID, _STATE_NEW
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        leaf = FAMILY + "-" + str(os.getuid())
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent)
            _STATE_NEW = True
        except FileExistsError:
            _STATE_NEW = False
        descriptor = os.open(
            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_nlink < 2
        ):
            os.close(descriptor)
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _STATE_DIR_FD = descriptor
        _STATE_DIR_ID = (info.st_dev, info.st_ino)
        directory = os.path.join(temporary, leaf)
        _STATE_PATH = os.path.join(directory, "state.json")
        _STATE_LOCK = os.path.join(directory, "state.lock")
        verify_state_directory()
    finally:
        os.close(parent)


def secure_file(name, flags):
    verify_state_directory()
    if name in {"state.lock", "collection.lock"} and not _STATE_NEW:
        flags &= ~os.O_CREAT
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW, 0o600, dir_fd=_STATE_DIR_FD
    )
    info = os.fstat(descriptor)
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher state file", "access")
    return descriptor


def locked_budget():
    budget_file()
    descriptor = secure_file("state.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise AdapterError("Publisher state lock is busy", "access") from None
    return os.fdopen(descriptor, "a+")


def load_budget():
    try:
        descriptor = secure_file("state.json", os.O_RDONLY)
    except FileNotFoundError:
        if not _STATE_NEW:
            raise AdapterError(
                "Missing mature publisher state; no reset", "access"
            ) from None
        return empty_budget(time.time())
    with os.fdopen(descriptor, "rb") as file:
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher state", "access") from None


def save_budget(state):
    global _STATE_NEW
    validate_budget(state)
    verify_state_directory()
    name = "pending-" + str(os.getpid()) + "-" + str(time.time_ns())
    descriptor = secure_file(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(descriptor, "w") as file:
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        verify_state_directory()
        os.replace(
            name,
            "state.json",
            src_dir_fd=_STATE_DIR_FD,
            dst_dir_fd=_STATE_DIR_FD,
        )
        os.fsync(_STATE_DIR_FD)
        _STATE_NEW = False
    finally:
        try:
            os.unlink(name, dir_fd=_STATE_DIR_FD)
        except FileNotFoundError:
            pass


REVIEWED_ROBOTS_ONLY_STATES = {
    "b649b04a88505deef9e072371668796671411de359a099f826b6344e06bd0668": 38086095380,
    "80f02fd04e978032f090e20cfe003593524f854e2adc32de79f503e3a0f79b93": 38086095524,
}


def recover_reviewed_robots_hold(state):
    """Migrate only exact authenticated historical robots-only refusal states.

    Both source run logs and their digest-verified pacing artifacts were reviewed.
    No unknown, mixed, content, challenge, rate-limit or TLS hold is recoverable.
    All original timing fields remain unchanged; provenance survives later merges.
    """
    validate_budget(state)
    digest = hashlib.sha256(
        json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if state["blocked"] and digest in REVIEWED_ROBOTS_ONLY_STATES:
        state = dict(state)
        state["blocked"] = False
        state["robots_recoveries"] = [digest]
    return state


def merge_budget(incoming):
    """Conserve all timing restrictions and refuse uncertainty after union."""
    with locked_budget():
        local = recover_reviewed_robots_hold(load_budget())
        incoming = recover_reviewed_robots_hold(incoming)
        now = time.time()
        combined = empty_budget(now)
        combined["interval"] = max(
            12, local.get("interval", 6), incoming.get("interval", 6)
        )
        combined["page_interval"] = max(
            30,
            local.get("page_interval", 30),
            incoming.get("page_interval", 30),
        )
        rate_limits = max(
            local.get("rate_limit_count", 0),
            incoming.get("rate_limit_count", 0),
        )
        if rate_limits:
            combined["rate_limit_count"] = rate_limits
        combined["not_before"] = max(
            local["not_before"], incoming["not_before"], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        recoveries = sorted(
            set(
                local.get("robots_recoveries", [])
                + incoming.get("robots_recoveries", [])
            )
        )
        if recoveries:
            combined["robots_recoveries"] = recoveries
        union = sorted(set(local["starts"] + incoming["starts"]))
        if union:
            combined["not_before"] = max(
                combined["not_before"], union[-1] + combined["interval"]
            )
        if len(union) > 10:
            combined["not_before"] = max(
                combined["not_before"], union[-1] + 60
            )
        combined["starts"] = union[-10:]
        save_budget(combined)


def preserve_legacy_pacing():
    """Keep every legacy inode and its exclusive lock throughout collection."""
    roots = [
        ("issue115-mercedes-research", "mercedes-family.budget.json"),
        ("youthopps-source-triage/115", "pacing.json"),
    ]
    for directory, filename in roots:
        parent = os.open(
            tempfile.gettempdir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        opened = [parent]
        try:
            for name in directory.split("/"):
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                opened.append(descriptor)
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700
                    or info.st_nlink < 2
                ):
                    raise AdapterError(
                        "Unsafe legacy publisher directory", "access"
                    )
                parent = descriptor
            descriptor = os.open(
                filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
            )
            opened.append(descriptor)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise AdapterError("Unsafe legacy publisher file", "access")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise AdapterError(
                    "Legacy publisher collector active", "access"
                ) from None
            raw = os.read(descriptor, 16385)
            try:
                legacy = json.loads(raw)
                # Historical research retains eleven aged starts after a wait;
                # accept that exact known shape without altering legacy bytes.
                if (
                    len(raw) > 16384
                    or not isinstance(legacy, dict)
                    or not {"starts", "until"} <= set(legacy)
                    or set(legacy) - {"starts", "until", "interval", "blocked"}
                    or not numeric(legacy["until"])
                    or not numeric(legacy.get("interval", 6))
                    or legacy.get("interval", 6) < 6
                    or type(legacy.get("blocked", False)) is not bool
                    or not isinstance(legacy["starts"], list)
                    or len(legacy["starts"]) > 11
                    or any(not numeric(t) for t in legacy["starts"])
                    or legacy["starts"] != sorted(set(legacy["starts"]))
                    or any(t > time.time() + 1 for t in legacy["starts"])
                ):
                    raise ValueError("Invalid legacy schema")
            except (ValueError, TypeError, KeyError):
                raise AdapterError(
                    "Invalid legacy publisher history", "access"
                ) from None
            incoming = empty_budget(time.time())
            incoming.update(
                not_before=legacy["until"],
                blocked=legacy.get("blocked", False),
                interval=legacy.get("interval", 6),
                starts=legacy["starts"],
            )
            merge_budget(incoming)
            _LEGACY_FDS.extend(opened)
            opened = []
        except FileNotFoundError:
            pass
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _REQUEST_STARTS, _PUBLISHER_BYTES
    _COLLECTION_STARTED = time.time()
    _REQUEST_STARTS = _PUBLISHER_BYTES = 0
    budget_file()
    if _COLLECTION_LOCK_FD is not None:
        raise AdapterError("Nested family collection refused", "access")
    descriptor = secure_file("collection.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(descriptor)
        raise AdapterError(
            "Another local family collector active", "access"
        ) from None
    _COLLECTION_LOCK_FD = descriptor
    preserve_legacy_pacing()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    with locked_budget():
        state = recover_reviewed_robots_hold(load_budget())
        state["interval"] = max(12, state.get("interval", 6))
        state["not_before"] = max(state["not_before"], time.time() + 60)
        state["observed_at"] = time.time()
        save_budget(state)
    _ROBOTS.clear()
    _STATE_TRUSTED = True


def public_url(value):
    """Allow reviewed policies and bounded official youth search windows."""
    parsed = urllib.parse.urlsplit(value)
    clean = urllib.parse.urlunsplit(parsed._replace(fragment=""))
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError("Unsafe public Mercedes-Benz route", "access")
    if clean in {info["url"] for info in INPUTS.values()}:
        return clean
    if (
        parsed.netloc == "jobs.api.mercedes-benz.com"
        and parsed.path == "/search"
    ):
        try:
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
            if set(query) != {"data"} or len(query["data"]) != 1:
                raise ValueError()
            request = json.loads(query["data"][0])
            expected = search_request(request["SearchParameters"]["FirstItem"])
            if request != expected:
                raise ValueError()
            return clean
        except (ValueError, KeyError, TypeError):
            pass
    raise AdapterError("Unreviewed public Mercedes-Benz route", "access")


def html_fingerprint(body):
    """Guard full material prose, literal links and finite-frontier controls."""
    root = document_root(body.decode("utf-8", "strict"))
    links = sorted(
        {
            (node.tag, node.attrs.get("rel", ""), node.attrs["href"])
            for tag in ("a", "link")
            for node in nodes(root, tag)
            if node.attrs.get("href")
        }
    )
    controls = []
    for node in root.walk():
        attrs = {
            key: value
            for key, value in node.attrs.items()
            if key in {"action", "method", "name", "value", "selected"}
            or key.startswith("data-")
        }
        if attrs:
            controls.append((node.tag, sorted(attrs.items())))
    facts = {"text": root.text(), "links": links, "controls": controls}
    return hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def robots_directive(parser, directive, url=None):
    """Retain native policy and every applicable product group's restriction."""
    import copy

    policies = [parser]
    for entry in parser.entries:
        if entry.applies_to(_USER_AGENT) or entry.applies_to("YouthOpps/1.0"):
            policy = copy.copy(parser)
            policy.entries = [entry]
            policy.default_entry = None
            if entry.applies_to("YouthOpps/1.0") and not entry.applies_to(
                _USER_AGENT
            ):
                entry = copy.copy(entry)
                entry.useragents = ["YouthOppsBot"]
                policy.entries = [entry]
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
    import urllib.robotparser

    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.path == "/robots.txt":
        return
    if origin not in _ROBOTS:
        key = next(
            (
                key
                for key, info in INPUTS.items()
                if info["url"] == origin + "/robots.txt"
            ),
            None,
        )
        if key is None:
            raise AdapterError("Unreviewed robots origin", "access")
        read_input(key)
    parser = _ROBOTS[origin]
    if not robots_directive(parser, "can_fetch", url):
        raise AdapterError(
            "Publisher robots excludes required input", "access"
        )


def unavailable_robots(url, status):
    """REP4xx policy is distinct from content authorization and rate limits."""
    return (
        url in ROBOTS_URLS
        and type(status) is int
        and 400 <= status < 500
        and status != 429
    )


def fetch_public(url):
    """Use at most two attempts, counting redirects in the same physical cap."""
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        try:
            status, headers, body = request_bytes(reviewed, publisher=True)
        except TransientPublisherError:
            if attempt:
                raise
            status = None
        if (
            status == 200
            and reviewed not in ROBOTS_URLS
            and (
                headers.get("Content-Type", "").split(";", 1)[0].lower()
                in {"text/html", "application/xhtml+xml"}
                or re.match(rb"\s*(?:<!doctype\s+html|<html\b)", body, re.I)
            )
        ):
            document_root(body.decode("utf-8", "strict"))
        if status == 200 or unavailable_robots(reviewed, status):
            return status, headers, body
        if status in (None, 503) and attempt == 0:
            with locked_budget():
                state = load_budget()
                state["not_before"] = max(
                    state["not_before"], time.time() + 30
                )
                state["observed_at"] = time.time()
                save_budget(state)
            continue
        raise AdapterError(
            "Required input returned HTTP " + str(status), "fetch", status
        )


def read_input(key):
    import urllib.robotparser

    info = INPUTS[key]
    status, headers, body = fetch_public(info["url"])
    content_type = (
        headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
    )
    if info["format"] == "robots" and re.search(
        rb"captcha|verify.{0,25}(?:human|browser)|challenge-platform",
        body,
        re.I,
    ):
        with locked_budget():
            state = load_budget()
            state["blocked"] = True
            state["observed_at"] = time.time()
            save_budget(state)
        raise AdapterError(
            "Publisher challenge requires reviewed access", "access"
        )
    if info["format"] == "robots":
        if status == 200 and re.search(rb"<html\b", body, re.I):
            raise AdapterError("Invalid HTML robots response", "access")
        if unavailable_robots(info["url"], status):
            print(
                f"Unavailable robots policy: HTTP {status}: {info['url']}",
                file=sys.stderr,
            )
            text = ""
        else:
            text = body.decode("utf-8", "strict")
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        origin = urllib.parse.urlsplit(info["url"])
        delay = robots_directive(parser, "crawl_delay")
        rate = robots_directive(parser, "request_rate")
        if rate and rate.seconds / rate.requests > 6:
            raise AdapterError(
                "Stricter request rate requires reviewed budget", "access"
            )
        if delay and delay > 6:
            with locked_budget():
                state = load_budget()
                state["interval"] = max(state.get("interval", 6), delay)
                if state["starts"]:
                    state["not_before"] = max(
                        state["not_before"], state["starts"][-1] + delay
                    )
                state["observed_at"] = time.time()
                save_budget(state)
        _ROBOTS[origin.scheme + "://" + origin.netloc] = parser
        return {"robots": True}
    if info["format"] == "api":
        if content_type not in {"application/json", "text/json"}:
            raise AdapterError(
                "Unexpected public API response format", "parse"
            )
        return parse_api_page(body, info)
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise AdapterError("Unexpected policy/criteria format", "parse")
    root = document_root(body.decode("utf-8", "strict"))
    main = nodes(root, "main")
    if len(main) > 1:
        raise AdapterError("Ambiguous policy/conditions content", "access")
    content = (
        main[0] if main else one(nodes(root, "body"), "complete page body")
    )
    text = content.text()
    if not text.strip():
        raise AdapterError("Missing policy/conditions text", "access")
    if re.search(
        r"(?:scrap(?:ing|ers?)|automat(?:ed|ic)|robots?|crawler|text.and.data.mining)"
        r".{0,180}(?:prohibit|not permitted|not allowed|forbidden|untersagt|verboten)"
        r"|(?:prohibit|not permitted|not allowed|forbidden|untersagt|verboten)"
        r".{0,180}(?:scrap|automat|robots?|crawler|text.and.data.mining)",
        text,
        re.I,
    ):
        raise AdapterError(
            "Explicit automated-access restriction: " + key, "access"
        )
    return {"material_sha256": html_fingerprint(body), "url": info["url"]}


def search_request(first):
    """Retain the youth career-level filter and ascending stable order."""
    if type(first) is not int or not 1 <= first <= 1553:
        raise AdapterError("Search exceeds physical request budget", "parse")
    template = json.loads(
        urllib.parse.parse_qs(
            urllib.parse.urlsplit(INPUTS["api-template"]["url"]).query
        )["data"][0]
    )
    template["SearchParameters"]["FirstItem"] = first
    return template


def search_url(first):
    return (
        "https://jobs.api.mercedes-benz.com/search?"
        + urllib.parse.urlencode(
            {"data": json.dumps(search_request(first), separators=(",", ":"))}
        )
    )


def check_frontier_budget(total, first, passes_left):
    """Refuse a provably infeasible traversal without relaxing retained pacing."""
    with locked_budget():
        state = load_budget()
    gap = max(
        30,
        state.get("page_interval", 30),
        state.get("interval", 12),
        _PUBLISHER_INTERVAL,
    )
    remaining = (total - first) // 97
    remaining += (passes_left - 1) * math.ceil(total / 97)
    if remaining * gap + 120 > _PHASE_END - time.monotonic():
        raise AdapterError(
            "Complete remaining inventory exceeds phase at retained pacing",
            "access",
        )


def read_frontier(passes_left=1):
    """Discover all current variants; incomplete or unstable runs fail."""
    seen, total, first = {}, None, 1
    while total is None or first <= total:
        status, headers, body = fetch_inventory_page(
            fetch_public, search_url(first)
        )
        content_type = headers.get("Content-Type", "").split(";", 1)[0].lower()
        if status != 200 or content_type.strip() not in {
            "application/json",
            "text/json",
        }:
            raise AdapterError("Invalid official search response", "parse")
        count, descriptors = parse_api_page(body, {"first_item": first})
        if total is None:
            check_frontier_budget(count, first, passes_left)
        if total is not None and total != count:
            raise AdapterError(
                "Search total changed during traversal", "parse"
            )
        total = count
        for descriptor in descriptors:
            identity = str(descriptor["ID"])
            before = seen.get(identity)
            if before is not None and before != descriptor:
                raise AdapterError("Variant changed across overlap", "parse")
            seen[identity] = descriptor
        first += 97
    if len(seen) != total:
        raise AdapterError(
            "Incomplete current youth variant inventory", "parse"
        )
    return seen


def read_pages():
    """Retain current robots/terms gates before complete two-pass discovery."""
    pages = {}
    for key, info in INPUTS.items():
        if info["format"] == "robots":
            pages[key] = read_input(key)
    for key, info in INPUTS.items():
        if info["format"] == "html":
            pages[key] = read_input(key)
    pages["initial"] = read_frontier(2)
    pages["closing"] = read_frontier()
    return pages


def make_record(variants):
    """Normalize current publisher fields without frozen qualification recipes."""
    selected = next(
        (
            d
            for d in variants
            if str(d["PublicationLanguage"].get("Code")) == "2"
        ),
        variants[0],
    )
    position = selected["PositionID"]
    title = " ".join(selected["PositionTitle"].split())
    levels = sorted(
        {str(level["Name"]) for d in variants for level in d["CareerLevel"]}
    )
    label = (title + " " + " ".join(levels)).casefold()
    working = bool(
        re.search(r"werkstudent|working.student|holiday|ferien", label)
    )
    internship = bool(re.search(r"praktik|internship|\bintern\b", label))
    training = bool(
        re.search(r"ausbildung|apprentice|duales? stud|dual stud", label)
    )
    categories = (
        ["training"]
        if training
        else (
            ["jobs"]
            if working
            else ["internships"] if internship else ["jobs"]
        )
    )
    if working and internship:
        categories.append("internships")
    countries = sorted(
        {
            location["CountryCode"]
            for d in variants
            for location in d["PositionLocation"]
        }
    )
    locations = list(
        dict.fromkeys(x["CityName"] for x in selected["PositionLocation"])
    )
    summary = (
        "Mercedes-Benz advertises this "
        + categories[0]
        + " listing in "
        + ", ".join(locations)
        + ". Publisher career levels: "
        + ", ".join(levels)
        + ". Check the original notice for "
        "role-specific eligibility, conditions and application instructions."
    )
    if len(summary) > 550:
        summary = (
            "Mercedes-Benz youth " + categories[0] + " listing. "
            "Check the original notice for the advertised location, "
            "role-specific eligibility, conditions and application instructions."
        )
    evidence = [
        ATTRIBUTION,
        "Publisher PositionID: " + position,
        "Publisher career levels: " + ", ".join(levels),
    ]
    for descriptor in variants:
        evidence.extend(
            [
                descriptor["PositionURI"],
                "Complete descriptor SHA256: " + material_hash(descriptor),
                "Publisher factual dates: "
                + json.dumps(
                    {
                        key: descriptor.get(key)
                        for key in (
                            "PublicationStartDate",
                            "PublicationEndDate",
                            "PositionStartDate",
                        )
                    },
                    sort_keys=True,
                ),
            ]
        )
        for block in descriptor["PositionFormattedDescription"]:
            for field in ("Tasks", "Qualifications"):
                root = PublisherHTML(block[field]).root
                linked = {
                    urllib.parse.urljoin(
                        descriptor["PositionURI"], node.attrs["href"]
                    )
                    for node in nodes(root, "a")
                    if node.attrs.get("href")
                }
                evidence.extend(
                    "Additional publisher entry conditions: " + info["url"]
                    for key, info in INPUTS.items()
                    if key.startswith("criteria-") and info["url"] in linked
                )
    now = utc_now()
    return {
        "id": hashlib.sha256(
            (SOURCE_ID + "|mercedes-public-position|" + position).encode()
        ).hexdigest()[:24],
        "title": title,
        "url": selected["PositionURI"],
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": None,
        "summary": summary,
        "summary_language": "en",
        "tags": levels,
        "location": ", ".join(locations),
        "deadline": None,
        "language": None,
        "category": categories[0],
        "categories": categories,
        "kind": "opportunity",
        "host_countries": countries,
        "eligible_countries": [],
        "publisher_country": PUBLISHER_COUNTRY,
        "status": "unknown",
        "classification": {
            "method": "publisher-api",
            "status": "classified",
            "evidence": list(dict.fromkeys(evidence)),
        },
        "created_at": now,
        "updated_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "last_checked_at": now,
    }


def validate_owned_records(records):
    (
        "Validate structural ownership without requiring an obsolete frozen "
        "recipe."
    )
    for record in records:
        classification = record.get("classification")
        if not isinstance(classification, dict):
            raise AdapterError("Missing publisher classification", "validate")
        evidence = classification.get("evidence")
        if (
            record.get("source") != SOURCE_ID
            or record.get("source_url") != SOURCE_URL
            or not isinstance(evidence, list)
            or any(not isinstance(item, str) for item in evidence)
        ):
            raise AdapterError(
                "Foreign or invalid publisher record", "validate"
            )
        positions = [
            item.removeprefix("Publisher PositionID: ")
            for item in evidence
            if item.startswith("Publisher PositionID: ")
        ]
        if not positions:
            # Earlier records used the same PositionID without a label.
            positions = [
                item for item in evidence if re.fullmatch(r"[a-z0-9]+", item)
            ]
        if len(positions) != 1 or not re.fullmatch(r"[a-z0-9]+", positions[0]):
            raise AdapterError("Missing stable publisher identity", "validate")
        identity = hashlib.sha256(
            (SOURCE_ID + "|mercedes-public-position|" + positions[0]).encode()
        ).hexdigest()[:24]
        if record["id"] != identity:
            raise AdapterError("Publisher identity mismatch", "validate")
        url = urllib.parse.urlsplit(record["url"])
        if url.hostname != "jobs.mercedes-benz.com":
            raise AdapterError("Unofficial publisher record URL", "validate")
        categories = record.get("categories")
        if (
            not isinstance(categories, list)
            or not categories
            or any(item not in CATEGORIES for item in categories)
            or record.get("category") != categories[0]
        ):
            raise AdapterError("Invalid publisher classification", "validate")
        if (
            record.get("publisher_country") != PUBLISHER_COUNTRY
            or record.get("kind") != "opportunity"
            or record.get("status") not in {"open", "expired", "unknown"}
            or record.get("summary_language") != "en"
            or not isinstance(record.get("tags"), list)
            or any(not isinstance(tag, str) for tag in record["tags"])
            or len(categories) != len(set(categories))
        ):
            raise AdapterError("Invalid factual record contract", "validate")
        clocks = {
            field: datetime.fromisoformat(record[field].replace("Z", "+00:00"))
            for field in [
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
            ]
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
                "Invalid publisher record chronology", "validate"
            )
        if record.get("deadline") is not None:
            deadline = record["deadline"]
            try:
                if (
                    datetime.fromisoformat(
                        deadline.replace("Z", "+00:00")
                    ).tzinfo
                    is None
                ):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise AdapterError(
                    "Unsupported application deadline", "validate"
                ) from None


def validate_records(records, *, allow_empty=False):
    if not isinstance(records, list) or (not allow_empty and not records):
        raise AdapterError(
            "Incomplete reviewed Mercedes-Benz identity inventory", "validate"
        )
    expected_fields = {
        "id",
        "title",
        "url",
        "source",
        "source_url",
        "published_at",
        "summary",
        "summary_language",
        "tags",
        "location",
        "deadline",
        "language",
        "category",
        "categories",
        "kind",
        "host_countries",
        "eligible_countries",
        "publisher_country",
        "status",
        "classification",
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    identities = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise AdapterError("Invalid complete25-field contract", "validate")
        if (
            not isinstance(record.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{24}", record.get("id", ""))
            or record["id"] in identities
        ):
            raise AdapterError(
                "Invalid or duplicate Mercedes-Benz identity", "validate"
            )
        identities.add(record["id"])
        for field in (
            "created_at",
            "updated_at",
            "first_seen_at",
            "last_seen_at",
            "last_checked_at",
        ):
            try:
                value = record[field]
                if (
                    not isinstance(value, str)
                    or not re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z",
                        value,
                    )
                    or datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    ).tzinfo
                    is None
                ):
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise AdapterError(
                    "Invalid Mercedes-Benz UTC timestamp", "validate"
                ) from None
        summary = record["summary"]
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary.encode("utf-16-le")) // 2 > 580
            or re.search(r"<[^>]+>|[\x00-\x1f\x7f]", summary)
        ):
            raise AdapterError("Invalid original factual summary", "validate")
        for name in ("host_countries", "eligible_countries"):
            values = record[name]
            if (
                not isinstance(values, list)
                or any(
                    not isinstance(v, str) or not re.fullmatch(r"[A-Z]{2}", v)
                    for v in values
                )
                or len(values) != len(set(values))
            ):
                raise AdapterError("Invalid country array", "validate")
        if not isinstance(record["title"], str) or not record["title"].strip():
            raise AdapterError("Invalid programme title", "validate")
        if not isinstance(record["url"], str):
            raise AdapterError("Invalid official URL type", "validate")
        try:
            url = urllib.parse.urlsplit(record["url"])
            invalid_url = (
                url.scheme != "https"
                or not url.hostname
                or url.username
                or url.password
                or url.port
            )
        except ValueError:
            invalid_url = True
        if invalid_url:
            raise AdapterError("Unsafe official URL", "validate")
    validate_owned_records(records)


def validate_api_frontier(pages, frontier):
    variants = pages.get(frontier)
    if not isinstance(variants, dict) or not variants:
        raise AdapterError("Missing complete publisher frontier", "parse")
    return variants


def parse_inventory(pages):
    for key, info in INPUTS.items():
        if info["format"] == "html" and not (
            isinstance(pages.get(key), dict)
            and pages[key].get("url") == info["url"]
            and re.fullmatch(
                r"[0-9a-f]{64}", pages[key].get("material_sha256", "")
            )
        ):
            raise AdapterError(
                "Missing current criteria/policy material", "parse"
            )
        if info["format"] == "robots" and pages.get(key) != {"robots": True}:
            raise AdapterError("Incomplete fresh robots gate", "parse")
    initial = validate_api_frontier(pages, "initial")
    closing = validate_api_frontier(pages, "closing")
    if initial != closing:
        raise AdapterError("Initial and closing inventories differ", "parse")
    by_position = {}
    for descriptor in initial.values():
        by_position.setdefault(descriptor["PositionID"], []).append(descriptor)
    records = [
        make_record(sorted(variants, key=lambda d: str(d["ID"])))
        for _, variants in sorted(by_position.items())
    ]
    validate_records(records)
    return records


def collect():
    if _PHASE_END is not None:
        check_deadline()
        return parse_inventory(read_pages())
    with execution():
        with phase("prepublication", COLLECTION_TIMEOUT):
            attempt = utc_now()
            prepare_collection()
            records = parse_inventory(read_pages())
            fresh_records(records, [], attempt, utc_now())
            return records


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "source": SOURCE_ID,
        "name": SOURCE_NAME,
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
                "public youth vacancies"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


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
            key: value for key, value in before.items() if key not in temporal
        } == {
            key: value for key, value in record.items() if key not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", checked) if same else checked,
            last_seen_at=attempt,
            last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records, allow_empty=True)


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
        <= previous.keys()
        or previous.get("name") != SOURCE_NAME
        or previous.get("publisher_type") != PUBLISHER_TYPE
        or previous.get("attribution") != ATTRIBUTION
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


_OPENER = urllib.request.build_opener(NoRedirect())


def material_hash(descriptor):
    material = {key: descriptor.get(key) for key in MATERIAL_FIELDS}
    return hashlib.sha256(
        json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def parse_api_page(body, info):
    (
        "Validate every current descriptor and complete sorted pagination "
        "window."
    )
    try:
        result = json.loads(body)["SearchResult"]
        items = result["SearchResultItems"]
        total = result["SearchResultCountAll"]
        first = info["first_item"]
        if (
            type(total) is not int
            or not 0 < total <= 1552
            or not isinstance(items, list)
            or len(items) != min(100, total - first + 1)
            or result["SearchResultCount"] != len(items)
        ):
            raise ValueError()
        ids, positions, descriptors = set(), [], []
        for item in items:
            descriptor = item["MatchedObjectDescriptor"]
            identity = str(descriptor["ID"])
            position = descriptor["PositionID"]
            if (
                str(item["MatchedObjectId"]) != identity
                or identity in ids
                or not re.fullmatch(r"[a-z0-9]+", position)
            ):
                raise ValueError()
            ids.add(identity)
            positions.append(position)
            for key in ["PositionTitle", "PositionURI", "OrganizationName"]:
                if (
                    not isinstance(descriptor[key], str)
                    or not descriptor[key].strip()
                ):
                    raise ValueError()
            uri = urllib.parse.urlsplit(descriptor["PositionURI"])
            if (
                uri.scheme != "https"
                or uri.hostname != "jobs.mercedes-benz.com"
                or uri.username
                or uri.password
            ):
                raise ValueError()
            levels = descriptor["CareerLevel"]
            if (
                not isinstance(levels, list)
                or not levels
                or any(
                    str(level["Code"]) not in {str(x) for x in range(19, 28)}
                    or not isinstance(level["Name"], str)
                    for level in levels
                )
            ):
                raise ValueError()
            if not isinstance(descriptor["PublicationLanguage"], dict):
                raise ValueError()
            locations = descriptor["PositionLocation"]
            if (
                not isinstance(locations, list)
                or not locations
                or any(
                    not re.fullmatch(r"[A-Z]{2}", x["CountryCode"])
                    or not isinstance(x["CityName"], str)
                    for x in locations
                )
            ):
                raise ValueError()
            blocks = descriptor["PositionFormattedDescription"]
            if (
                not isinstance(blocks, list)
                or not blocks
                or any(
                    not isinstance(block, dict)
                    or not isinstance(block.get("Qualifications"), str)
                    or not isinstance(block.get("Tasks"), str)
                    for block in blocks
                )
            ):
                raise ValueError()
            for key in [
                "PositionStartDate",
                "PublicationStartDate",
                "PublicationEndDate",
            ]:
                value = descriptor.get(key)
                if value not in (None, ""):
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
            descriptors.append(descriptor)
        if positions != sorted(positions):
            raise ValueError()
        return total, descriptors
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AdapterError(
            "Invalid current complete publisher descriptor/page", "parse"
        ) from None


def open_fresh_pacing_output(runner_parent, runner_root):
    (
        "Use the official fresh per-step output file, never an inherited "
        "marker."
    )
    value = os.environ.get("GITHUB_OUTPUT")
    if not value:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Fresh step output required", "access")
        return None
    if not os.path.isabs(value):
        raise AdapterError("Unsafe step output path", "access")
    parts = os.path.relpath(value, runner_root).split(os.sep)
    if not parts or any(name in {"", ".", ".."} for name in parts):
        raise AdapterError(
            "Step output outside runner temporary root", "access"
        )
    descriptors = []
    parent = runner_parent
    try:
        for name in parts[:-1]:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            descriptors.append(descriptor)
            info = os.fstat(descriptor)
            if (
                info.st_uid != os.getuid()
                or not stat.S_ISDIR(info.st_mode)
                or info.st_mode & 0o022
            ):
                raise AdapterError("Unsafe step output parent", "access")
            parent = descriptor
        descriptor = os.open(
            parts[-1], os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW, dir_fd=parent
        )
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Fresh owned step output required", "access")
        if info.st_size != 0:
            # This path is owned and safe, but it violates the platform's
            # per-step freshness contract. Remove its old marker, then refuse.
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            raise AdapterError("Inherited step output refused", "access")
        return os.dup(descriptor)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


INPUTS = {
    "api-robots": {
        "url": "https://jobs.api.mercedes-benz.com/robots.txt",
        "format": "robots",
    },
    "group-robots": {
        "url": "https://group.mercedes-benz.com/robots.txt",
        "format": "robots",
    },
    "criteria-school": {
        "url": "https://group.mercedes-benz.com/karriere/schueler/duales-studium/bewerbung/",
        "format": "html",
    },
    "criteria-working": {
        "url": "https://group.mercedes-benz.com/karriere/studenten/werkstudenten/",
        "format": "html",
    },
    "criteria-intern": {
        "url": "https://group.mercedes-benz.com/karriere/studenten/praktikum/",
        "format": "html",
    },
    "criteria-thesis": {
        "url": "https://group.mercedes-benz.com/karriere/studenten/abschlussarbeit/",
        "format": "html",
    },
    "policy-1": {
        "url": "https://group.mercedes-benz.com/provider/",
        "format": "html",
    },
    "policy-2": {
        "url": "https://group.mercedes-benz.com/careers/general-terms-conditions.html",
        "format": "html",
    },
    "api-template": {
        "url": "https://jobs.api.mercedes-benz.com/search?data=%7B%22LanguageCode%22%3A%22EN%22%2C%22SearchParameters%22%3A%7B%22FirstItem%22%3A1%2C%22CountItem%22%3A100%2C%22Sort%22%3A%5B%7B%22Criterion%22%3A%22PositionID%22%2C%22Direction%22%3A%22ASC%22%7D%5D%7D%2C%22SearchCriteria%22%3A%5B%7B%22CriterionName%22%3A%22CareerLevel.Code%22%2C%22CriterionValue%22%3A%5B19%2C20%2C21%2C22%2C23%2C24%2C25%2C26%2C27%5D%7D%5D%7D",
        "format": "api",
        "count": 100,
        "frontier": "initial",
        "first_item": 1,
    },
}

ROBOTS_URLS = {
    info["url"] for info in INPUTS.values() if info["format"] == "robots"
}

MATERIAL_FIELDS = [
    "PositionTitle",
    "PositionURI",
    "PublicationLanguage",
    "CareerLevel",
    "PositionFormattedDescription",
    "PositionBenefits",
    "PositionLocation",
    "PositionStartDate",
    "PublicationStartDate",
    "PublicationEndDate",
    "PublicationChannel",
    "PositionSchedule",
    "PositionOfferingType",
    "RemunerationDescription",
    "TargetGroup",
    "ApplyURI",
    "OrganizationName",
    "DepartmentName",
]


if __name__ == "__main__":
    sys.exit(main())
