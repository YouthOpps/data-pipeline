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
    "Complete reviewed public youth vacancy inventory and referenced entry "
    "conditions."
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
_PUBLISHER_INTERVAL = 6
_PUBLISHER_GATE_FAILED = False
_RUN_END = _PHASE_END = None
_PHASE_NAME = None


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
    if not isinstance(state, dict) or set(state) - {"interval"} != {
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
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 6,
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
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state.get("interval", 6))
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
                if publisher and status in (401, 403, 429):
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
                        and urllib.parse.urlsplit(destination).scheme != "https"
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
        raw = os.environ.get("MERCEDES_PACING_BOOTSTRAP", "")
        try:
            bootstrap = json.loads(raw)
            if (
                set(bootstrap) != {"schema", "family", "not_before", "evidence"}
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
            raise AdapterError("Incomplete family artifact inventory", "access")
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


def merge_budget(incoming):
    """Conserve all timing restrictions and refuse uncertainty after union."""
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined["interval"] = max(
            local.get("interval", 6), incoming.get("interval", 6)
        )
        combined["not_before"] = max(
            local["not_before"], incoming["not_before"], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        union = sorted(set(local["starts"] + incoming["starts"]))
        if union:
            combined["not_before"] = max(
                combined["not_before"], union[-1] + combined["interval"]
            )
        if len(union) > 10:
            combined["not_before"] = max(combined["not_before"], union[-1] + 60)
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
        state = load_budget()
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
        raise AdapterError("Publisher robots excludes required input", "access")


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
        if status == 200 or status == 404 and reviewed.endswith("/robots.txt"):
            return status, headers, body
        if status in (None, 503) and attempt == 0:
            with locked_budget():
                state = load_budget()
                state["not_before"] = max(state["not_before"], time.time() + 30)
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
    if info["format"] == "robots":
        if status == 404:
            if info["url"] != "https://jobs.api.mercedes-benz.com/robots.txt":
                raise AdapterError("Unreviewed absent robots policy", "access")
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
            raise AdapterError("Unexpected public API response format", "parse")
        return parse_api_page(body, info)
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise AdapterError("Unexpected policy/criteria format", "parse")
    digest = html_fingerprint(body)
    if digest != info["sha256"]:
        raise AdapterError(
            "Reviewed policy or entry-condition material changed: " + key,
            "parse",
        )
    return digest


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


def read_frontier():
    """Discover all current variants; incomplete or unstable runs fail."""
    seen, total, first = {}, None, 1
    while total is None or first <= total:
        status, headers, body = fetch_inventory_page(
            fetch_public, search_url(first))
        content_type = headers.get("Content-Type", "").split(";", 1)[0].lower()
        if status != 200 or content_type.strip() not in {
            "application/json",
            "text/json",
        }:
            raise AdapterError("Invalid official search response", "parse")
        count, descriptors = parse_api_page(body, {"first_item": first})
        if total is not None and total != count:
            raise AdapterError("Search total changed during traversal", "parse")
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
    pages["initial"] = read_frontier()
    for key, info in INPUTS.items():
        if info["format"] == "html":
            pages[key] = read_input(key)
    pages["closing"] = read_frontier()
    return pages


def make_record(variants):
    """Use reviewed factual summaries bound to exact current evidence."""
    selected = next(
        (
            d
            for d in variants
            if str(d["PublicationLanguage"].get("Code")) == "2"
        ),
        variants[0],
    )
    position = selected["PositionID"]
    recipe = FACTUAL_RECIPES.get(position)
    observed = {str(d["ID"]): material_hash(d) for d in variants}
    if recipe is None or observed != recipe["variant_hashes"]:
        raise AdapterError(
            "New or changed current qualifications require factual review",
            "parse",
        )
    now = utc_now()
    locations = selected["PositionLocation"]
    countries = sorted(
        {
            location["CountryCode"]
            for d in variants
            for location in d["PositionLocation"]
        }
    )
    evidence = [ATTRIBUTION, "Publisher PositionID: " + position]
    evidence.extend(recipe["condition_evidence"])
    for descriptor in variants:
        evidence.append(descriptor["PositionURI"])
        # Exact official date facts remain provenance, never guessed deadlines.
        evidence.append(
            "Publisher factual dates: "
            + json.dumps(
                {
                    key: descriptor.get(key)
                    for key in [
                        "PublicationStartDate",
                        "PublicationEndDate",
                        "PositionStartDate",
                    ]
                },
                sort_keys=True,
            )
        )
        evidence.append(
            "Complete descriptor SHA256: " + material_hash(descriptor)
        )
        for block in descriptor["PositionFormattedDescription"]:
            linked = set()
            for field in ["Tasks", "Qualifications"]:
                root = PublisherHTML(block[field]).root
                linked.update(
                    urllib.parse.urljoin(
                        descriptor["PositionURI"], node.attrs["href"]
                    )
                    for node in nodes(root, "a")
                    if node.attrs.get("href")
                )
            for key, info in INPUTS.items():
                if key.startswith("criteria-") and info["url"] in linked:
                    conditions = {
                        "criteria-school": (
                            "Technical Abitur average at most 2.7 and "
                            "mathematics at least 8 points; "
                            "business average at most 2.5 and mathematics "
                            "at least 10 points; "
                            "Fachhochschulreife average and mathematics at "
                            "most 2.0 plus DHBW aptitude test. "
                            "Applicable programme alternatives must be "
                            "checked with the publisher."
                        ),
                        "criteria-working": (
                            "Excellent grades; regular study duration plus "
                            "at most three semesters; "
                            "outstanding examinations; 7–20 weekly hours; "
                            "relevant internships preferred."
                        ),
                        "criteria-intern": (
                            "Study documentation; voluntary-internship "
                            "study-duration conditions; "
                            "degree-gap alternatives; applicable "
                            "residence/work documents. "
                            "Accommodation depends on location and capacity."
                        ),
                        "criteria-thesis": (
                            (
                                "Above-average grades and relevant internships;"
                                " applicable study and residence documents."
                            )
                        ),
                    }
                    evidence.append(
                        "Additional publisher entry conditions: " + info["url"]
                    )
                    evidence.append(conditions[key])
    return {
        "id": hashlib.sha256(
            (SOURCE_ID + "|mercedes-public-position|" + position).encode()
        ).hexdigest()[:24],
        "title": selected["PositionTitle"],
        "url": selected["PositionURI"],
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": None,
        "summary": recipe["summary"],
        "summary_language": "en",
        "tags": [],
        "location": ", ".join(dict.fromkeys(x["CityName"] for x in locations)),
        "deadline": None,
        "language": recipe.get("language"),
        "category": recipe["categories"][0],
        "categories": recipe["categories"],
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
        if info["format"] == "html" and pages.get(key) != info["sha256"]:
            raise AdapterError(
                "Changed reviewed criteria/policy material", "parse"
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
            if parsed.tzinfo is None or parsed != timestamps["last_checked_at"]:
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
        "sha256": (
            "dd5a6df217e238dbabdd64187feeadef"
            "8d395c7a3eddd9b036b48c82f18a8b9f"
        ),
    },
    "criteria-working": {
        "url": "https://group.mercedes-benz.com/karriere/studenten/werkstudenten/",
        "format": "html",
        "sha256": (
            "e77a28476b2d28744b224e363e3ad754"
            "a556c4a0b168edbd4addafc98b2b9cb5"
        ),
    },
    "criteria-intern": {
        "url": "https://group.mercedes-benz.com/karriere/studenten/praktikum/",
        "format": "html",
        "sha256": (
            "f0402779cf739bc022b5e77ebcd0ec0a"
            "de865eb485eed77cdaad417db2412351"
        ),
    },
    "criteria-thesis": {
        "url": "https://group.mercedes-benz.com/karriere/studenten/abschlussarbeit/",
        "format": "html",
        "sha256": (
            "a2b1eeae856232d09e799656190cb2fd"
            "cb1001b30a67e3a3e15207c2da59259d"
        ),
    },
    "policy-1": {
        "url": "https://group.mercedes-benz.com/provider/",
        "format": "html",
        "sha256": (
            "e20f8d931cf3fddc696d103e8bf241b8"
            "3cd02ad7560e18e50a1caf87de39e8fb"
        ),
    },
    "policy-2": {
        "url": "https://group.mercedes-benz.com/careers/general-terms-conditions.html",
        "format": "html",
        "sha256": (
            "a488b18f683764e7c596807b3626bf30"
            "3ccfa839002e94091cec85f9f457d801"
        ),
    },
    "api-template": {
        "url": "https://jobs.api.mercedes-benz.com/search?data=%7B%22LanguageCode%22%3A%22EN%22%2C%22SearchParameters%22%3A%7B%22FirstItem%22%3A1%2C%22CountItem%22%3A100%2C%22Sort%22%3A%5B%7B%22Criterion%22%3A%22PositionID%22%2C%22Direction%22%3A%22ASC%22%7D%5D%7D%2C%22SearchCriteria%22%3A%5B%7B%22CriterionName%22%3A%22CareerLevel.Code%22%2C%22CriterionValue%22%3A%5B19%2C20%2C21%2C22%2C23%2C24%2C25%2C26%2C27%5D%7D%5D%7D",
        "format": "api",
        "count": 100,
        "frontier": "initial",
        "first_item": 1,
    },
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


FACTUAL_RECIPES = {
    "ano000003n": {
        "summary": (
            "Passenger-car mechatronics apprenticeship in Hanau starts 2 August"
            " 2027. Learn vehicle diagnosis, repair, maintenance and connected "
            "systems. Applicants need technical and practical aptitude, maths "
            "and physics, and interest in automotive electronics/mechanics. "
            "Apply online with a motivation letter, CV, the two latest school "
            "reports and relevant certificates in DOC/PDF. The notice states no"
            " specific school-leaving minimum or pay amount."
        ),
        "condition_evidence": [
            (
                "Technical understanding, practical aptitude, maths and "
                "physics, and interest in automotive electronics and mechanics "
                "are requested. Responsibility, care and reliability are also "
                "expected."
            ),
            (
                "Application is online only: covering/motivation letter, CV, "
                "the latest two school reports and any relevant additional "
                "certificates or internship confirmations; DOC or PDF "
                "documents."
            ),
            (
                "No school-leaving qualification threshold or remuneration "
                "amount is stated in the reviewed description. Disability "
                "applications are explicitly welcomed."
            ),
            (
                "Additional structured facilities/support listed: inhouse "
                "doctor, parking, barrier-free workplace. These names do not "
                "establish an individual cash award."
            ),
            (
                "Structured role labels: Full time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "233454": (
                "0f9b54541e4caadaa4a720700d5806f6"
                "55fb607cab035d8cd6782a341700884f"
            ),
            "233452": (
                "68d3d5dec27f5b9e4c6da0386dda7837"
                "87b4f337ca10e875ca5196edb4ec7f17"
            ),
            "233451": (
                "88faa940f013c339a1e70ac90a59791f"
                "4aea453038fdae794309c66273904b0f"
            ),
        },
    },
    "ano000003o": {
        "summary": (
            "Technical and practical aptitude, good maths and physics, interest"
            " in automotive electronics/mechanics, and careful, reliable work "
            "are expected. School minimum: good Mittelschulabschluss. Listed "
            "support: Inhouse Doctor, Good Public Transport."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "ano000003o",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230741": (
                "fd2ae43e95e70b2554b30127351bfe98"
                "d322cd11a66a6fc81b6476a0c969b7dd"
            ),
            "230739": (
                "657055877d4e8d8fe2d9483e7d85479d"
                "f953d1a52194aa551f78deec4a986248"
            ),
            "230738": (
                "789677b71725b5a2fcb1973ee58d01a1"
                "7c96e97e86be85ca0fd627753c9c1076"
            ),
        },
    },
    "ano000003p": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Mittelschulabschluss. Listed "
            "support: Inhouse Doctor, Good Public Transport."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "ano000003p",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230749": (
                "b0a7197d2026751be2d8ba61de345533"
                "9dd83f5e141b49e486c372f439fd1d39"
            ),
            "230746": (
                "72891a52474e9f6dae03b759df6727c1"
                "0c5c5ea466ac38aa86d05c2b1317fecc"
            ),
            "230745": (
                "5334b4bcd9051e3de83a459eb4dc8d27"
                "e1282eb88f4495aa8590cea86be35c61"
            ),
        },
    },
    "j000206057": {
        "summary": (
            "Requires business/HR, psychology, education/adult-learning or "
            "related study; strong communication, MS Office and modern AI-tool "
            "experience; regular on-site attendance; very good German and "
            "English. HR experience and collaboration-tool knowledge are "
            "preferred. Listed support: Mobile Phone for Employees Possible, "
            "Discounts for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000206057",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "206057": (
                "ef6686d002d1e7cd8f9649dbcb2545bf"
                "79a03dce9ed7774fe033858a268f38cb"
            )
        },
    },
    "j000216637": {
        "summary": (
            "Requires engineering, business, technical business, production or "
            "logistics enrolment with foundational studies completed; MS "
            "Office, presentation and analytical skills. Automotive "
            "logistics/lean and SAP experience are preferred. Listed support: "
            "Discounts for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000216637",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "216637": (
                "ea253ca99cfcea5c9013449f4694127a"
                "26f26dcd2972202d785ddfbaef22d4f0"
            )
        },
    },
    "j000219466": {
        "summary": (
            "Requires a general, subject-restricted or applied-sciences "
            "university entrance qualification and very good German. English "
            "and initial software-development experience are advantageous; IT "
            "interest, initiative and teamwork are expected. Submit cover "
            "letter, CV, grades. Listed support: Hybrid Work Possible, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000219466",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219466": (
                "378f0ba113e3b0d46dd70aa10cfcbd2f"
                "0cfbf940fe2feb4bcf3a1945811c2446"
            )
        },
    },
    "j000219468": {
        "summary": (
            "Requires a general, subject-restricted or applied-sciences "
            "university entrance qualification and very good German. English "
            "and initial software-development experience are advantageous; IT "
            "interest, initiative and teamwork are expected. Submit cover "
            "letter, CV, grades. Listed support: Hybrid Work Possible, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000219468",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219468": (
                "de2700e218e86435f39471f3600d1392"
                "070628b3241e52a270b7b2451032766b"
            )
        },
    },
    "j000219470": {
        "summary": (
            "Requires a general, subject-restricted or applied-sciences "
            "university entrance qualification and very good German. English "
            "and initial software-development experience are advantageous; IT "
            "interest, initiative and teamwork are expected. Submit cover "
            "letter, CV, grades. Listed support: Hybrid Work Possible, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000219470",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219470": (
                "27bae6512016d525082135d8f77c2f58"
                "8552e5ea8a07fe8b0167967857e40d2c"
            )
        },
    },
    "j000219501": {
        "summary": (
            "Requires an intermediate school certificate or general, "
            "subject-restricted or applied-sciences university entrance "
            "qualification; very good German and basic computer-system "
            "knowledge. English is advantageous; application-development "
            "interest and willingness to learn are expected. Submit cover "
            "letter, CV, grades. Listed support: Hybrid Work Possible, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000219501",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219501": (
                "2de1b3f38738f91199051fd113289b82"
                "4ec71c38088965a4e2d61f84cc404ff0"
            )
        },
    },
    "j000219503": {
        "summary": (
            "Requires an intermediate school certificate or general, "
            "subject-restricted or applied-sciences university entrance "
            "qualification; very good German and basic computer-system "
            "knowledge. English is advantageous; application-development "
            "interest and willingness to learn are expected. Submit cover "
            "letter, CV, grades. Listed support: Hybrid Work Possible, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000219503",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219503": (
                "4099d053af7517e67371f3c720356487"
                "1ab211ce569b5cf9f193baa446aba8a5"
            )
        },
    },
    "j000230415": {
        "summary": (
            "Requires a general, subject-restricted or applied-sciences "
            "university entrance qualification and very good German. English "
            "and initial IT/software experience are advantageous; analytical "
            "and mathematical ability and interest in AI are expected. Submit "
            "cover letter, CV, grades. Listed support: Hybrid Work Possible, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000230415",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230415": (
                "d72b1ebc6d49c58e69efd63f783168b8"
                "f58d9286a0903cd61f93c29831f7454e"
            )
        },
    },
    "j000230417": {
        "summary": (
            "Requires a general, subject-restricted or applied-sciences "
            "university entrance qualification and very good German. English "
            "and initial IT/software experience are advantageous; analytical "
            "and mathematical ability and interest in AI are expected. Submit "
            "cover letter, CV, grades. Listed support: Hybrid Work Possible, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000230417",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230417": (
                "b001dc1b36488a14d9f94620a3c8dff7"
                "10eb7ac932e4106bd921cee52602b5dd"
            )
        },
    },
    "j000232384": {
        "summary": (
            "Requires a completed engineering, mechanical-engineering, "
            "economics or related bachelor’s degree; very good MS Office, "
            "analytical and teamwork skills, and business-fluent German and "
            "English. AMG internal-tool experience is optional. Listed support:"
            " Discounts for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000232384",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232384": (
                "f37bd0d2e5c61ee1dccb7853c3b486c7"
                "f51fd3e415b75779c0168a9aeed61426"
            )
        },
    },
    "j000233950": {
        "summary": (
            "Berlin innovation-management internship or working-student role "
            "starts 1 November 2026. Research AI, security and emerging "
            "technologies, assess trends and partnerships, and prepare reports."
            " Applicants must be studying a relevant technical/digital degree, "
            "have excellent German and English, and use PowerPoint/Excel "
            "confidently. Submit a complete letter, CV, grades and enrolment "
            "certificate, plus residence/work permission where applicable. "
            "Consulting or project/change experience is preferred."
        ),
        "condition_evidence": [
            (
                "Current study in business informatics, computer science, "
                "IT/cyber security, AI, data science, software engineering, "
                "technical communications or a comparable technical course with"
                " a substantial digital/new-technology focus."
            ),
            (
                "Excellent German and English in speech and writing and "
                "confident MS Office, especially PowerPoint and Excel; "
                "analytical/conceptual ability, independent organised proactive"
                " work, creativity, reliability and collaborative "
                "communication."
            ),
            (
                "Initial consulting, project-management or change-management "
                "experience is an ideal preference, not a mandatory "
                "employment-history threshold."
            ),
            (
                "Complete application required: covering letter, CV, grade "
                "overview and enrolment certificate; residence permit/work "
                "permit when applicable. Incomplete applications are not "
                "considered."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: hybrid work, mobile phone for employees, "
                "discounts for employees, annual profit share, flextime."
            ),
            (
                "Additional structured facilities/support listed: health "
                "benefits, events for employees, meal-discounts, lifelong "
                "learning, mobility offers, coaching, company retirement, "
                "canteen, café, parking, good public transport, barrier-free "
                "workplace. These names do not establish an individual cash "
                "award."
            ),
            (
                "Structured role labels: Full time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["internships", "jobs"],
        "variant_hashes": {
            "233950": (
                "2a1040f0ad4ee46f023f128c23cfea71"
                "2cd2b07eba7cf789e5bed5e67174c35e"
            )
        },
    },
    "j000233952": {
        "summary": (
            "Berlin innovation-management internship or working-student role "
            "starts 1 November 2026. Research AI, security and emerging "
            "technologies, assess trends and partnerships, and prepare reports."
            " Applicants must be studying a relevant technical/digital degree, "
            "have excellent German and English, and use PowerPoint/Excel "
            "confidently. Submit a complete letter, CV, grades and enrolment "
            "certificate, plus residence/work permission where applicable. "
            "Consulting or project/change experience is preferred."
        ),
        "condition_evidence": [
            (
                "Current study in business informatics, computer science, "
                "IT/cyber security, AI, data science, software engineering, "
                "technical communications or a comparable engineering course "
                "strongly related to digitalisation and emerging technologies."
            ),
            (
                "Excellent spoken/written German and English; confident "
                "PowerPoint/Excel and other MS Office use; "
                "analytical/conceptual skills, independent structured proactive"
                " work and collaborative communication."
            ),
            (
                "Early consulting, project-management or change-management "
                "experience is desirable rather than an absolute requirement."
            ),
            (
                "Complete covering letter, CV, grades overview and enrolment "
                "certificate are required; residence/work permission is "
                "requested where applicable. Incomplete submissions cannot "
                "proceed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: hybrid work, mobile phone for employees, "
                "discounts for employees, annual profit share, flextime."
            ),
            (
                "Additional structured facilities/support listed: health "
                "benefits, events for employees, meal-discounts, lifelong "
                "learning, mobility offers, coaching, company retirement, "
                "canteen, café, parking, good public transport, barrier-free "
                "workplace. These names do not establish an individual cash "
                "award."
            ),
            (
                "Structured role labels: Full time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["internships", "jobs"],
        "variant_hashes": {
            "233952": (
                "1bd88b9f7ab288d39bf1295f5f0654e5"
                "a9e816982e0645af1ce064bb84becf00"
            )
        },
    },
    "j000234758": {
        "summary": (
            "Requires enrolment in computing, business informatics, visual "
            "computing or related study; initial real-time rendering experience"
            " and C++, C# and Python; excellent German/English and occasional "
            "Böblingen attendance. A class-B licence is preferred; intended "
            "term is winter 2026/27. Submit cover letter, CV, grades, enrolment"
            " proof, compulsory-placement proof if applicable, residence/work "
            "permit if applicable. Listed support: Hybrid Work Possible, Mobile"
            " Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000234758",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "234758": (
                "92b5f1b21fea399babb19b462da6fdde"
                "3443f5fd07c011a400dc92c42e4b6cc2"
            )
        },
    },
    "j000235565": {
        "summary": (
            "Requires a completed mechatronics, automotive, "
            "electrical/electronic engineering or related bachelor’s degree; "
            "initial high-voltage hardware/software development experience and "
            "very good German/English. ETAS/Vector tools and MATLAB are "
            "preferred. Listed support: Discounts for Employees Possible, "
            "Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000235565",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "235565": (
                "d0c19dac052eaf441076b96f0e2d2dee"
                "715d88d75dcc7f81329d0b978d083a0f"
            )
        },
    },
    "j000236131": {
        "summary": (
            "Requires automotive/mechanical/industrial engineering or related "
            "technical enrolment with foundational studies completed; MS "
            "Office, class-B licence and German/English at least B2. Technical "
            "vocational training or practical experience is advantageous. "
            "Listed support: Discounts for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000236131",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "236131": (
                "acb3837e2050fe3c61531d4feef434d4"
                "0b76fa086bc6a6d30eec58283f37a52b"
            )
        },
    },
    "j000236141": {
        "summary": (
            "Requires engineering, technical-business or related enrolment with"
            " foundational studies completed; vehicle-technology basics, very "
            "good MS Office, business-fluent English and class-B licence. "
            "Flexibility, discretion and communication skills are expected. "
            "Listed support: Discounts for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000236141",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236141": (
                "f608893271a7a170b1a8128fabefc333"
                "0442e606b37f8825bc55f78bb797709c"
            )
        },
    },
    "j000236719": {
        "summary": (
            "Requires engineering, technical-business, automotive or related "
            "enrolment with foundational studies completed; good German/English"
            " and class-B licence. Project-management basics and practical "
            "experience are preferred. Listed support: Discounts for Employees "
            "Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000236719",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "236719": (
                "1cb571b42fb9aa97177ad0c79edb97c5"
                "4939b6473de819f40979634f25e9bbca"
            )
        },
    },
    "j000236732": {
        "summary": (
            "Requires mechanical/electrical engineering, technical computing, "
            "automotive or related enrolment with foundational studies "
            "completed; electric-drive and software knowledge plus "
            "application/measurement tools such as AVL, Vector, INCA and "
            "MATLAB/Simulink. Independent, careful teamwork is expected. Listed"
            " support: Discounts for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000236732",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236732": (
                "4c69d106055f6e145d02a3235df21f4d"
                "02295d5f0a01d6e35370f4de8b27f2b5"
            )
        },
    },
    "j000237548": {
        "summary": (
            "Requires mechanical engineering, mechatronics, automotive or "
            "related enrolment with foundational studies completed; very good "
            "MS Office and CAD/Siemens NX, automotive interest and class-B "
            "licence. Listed support: Discounts for Employees Possible, Health "
            "Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000237548",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "237548": (
                "c9986a2a2280bfca581843094b06f461"
                "fe50659a26c77d85d6f92b905cb11ff9"
            )
        },
    },
    "j000237757": {
        "summary": (
            "Requires IT, computer-science, software-engineering or related "
            "study; initial Java/Kotlin and Android-development experience, "
            "careful working habits and teamwork. Excellent German and English "
            "are required. Submit cover letter, CV, grades, enrolment proof, "
            "residence/work permit if applicable. Listed support: Hybrid Work "
            "Possible, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000237757",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "237757": (
                "5f788f7e2de9c90997d7df4dbeccd688"
                "f9fea9555f50072d7e659ebf5a052448"
            )
        },
    },
    "j000237991": {
        "summary": (
            "Requires IT, computer-science, software-engineering or related "
            "study; initial Java/Kotlin and Android-development experience, "
            "careful working habits and teamwork. Excellent German and English "
            "are required. Submit cover letter, CV, grades, enrolment proof, "
            "residence/work permit if applicable. Listed support: Hybrid Work "
            "Possible, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000237991",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "237991": (
                "1acb252222c4f48a7cfa4ac8d48c84d2"
                "7959c91294648dde75ba34a1ca5fcdd4"
            )
        },
    },
    "j000239702": {
        "summary": (
            "Requires ongoing business/economics, industrial engineering, "
            "mathematics/statistics or related bachelor’s/master’s study; "
            "planning/reporting experience, MS Office and data-analysis "
            "software, analytical ability and very good German/English. Listed "
            "support: Discounts for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000239702",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239702": (
                "8283d3ecfcb780b0134bb5b5aa81a284"
                "4dd5fc72988c11b09e9459122293520c"
            )
        },
    },
    "j000240636": {
        "summary": (
            "Requires enrolled real-estate management, business or related "
            "study, very good German/English and willingness to work on site in"
            " Ulm. Intended internship term is summer 2027; reliability, "
            "initiative and communication are expected. Submit cover letter, "
            "CV, grades, enrolment proof, residence/work permit if applicable. "
            "Listed support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240636",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240636": (
                "6a1b2566966ade77a57262a6bf976b04"
                "b413b84e48ff47247d5fc1880cfcd27c"
            )
        },
    },
    "j000240741": {
        "summary": (
            "Requires media informatics or related study, Photoshop/Figma and "
            "MS Office including PowerPoint/Excel, communication and "
            "organization, and regular Stuttgart/Ulm attendance. German/English"
            " requirements range from good in the role profile to excellent in "
            "the application condition. Submit cover letter, CV, grades, "
            "enrolment proof, residence/work permit if applicable. Listed "
            "support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240741",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240741": (
                "02f3a23c3c642b03df0b1d3fb83e9178"
                "b9ea610256fb2c81b4ccbee57800e032"
            )
        },
    },
    "j000240743": {
        "summary": (
            "Requires media informatics or related study, Photoshop/Figma and "
            "MS Office including PowerPoint/Excel, communication and "
            "organization, and regular Stuttgart/Ulm attendance. German/English"
            " requirements range from good in the role profile to excellent in "
            "the application condition. Submit CV, cover letter, grades, "
            "enrolment proof, residence/work permit if applicable. Listed "
            "support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240743",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "240743": (
                "d1ac81156f82542f64a1845deb50f704"
                "b2e2c577f7ca302e260b748cd8a5421c"
            )
        },
    },
    "j000240754": {
        "summary": (
            "Requires media informatics or related study, Photoshop/Figma and "
            "MS Office including PowerPoint/Excel, communication and "
            "organization, and regular Stuttgart/Ulm attendance. German/English"
            " requirements range from good in the role profile to excellent in "
            "the application condition. Submit cover letter, CV, grades, "
            "enrolment proof, residence/work permit if applicable. Listed "
            "support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240754",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240754": (
                "6be4a4544b57002e513c390d24575ff8"
                "1466242155639b4128461185752d1a6a"
            )
        },
    },
    "j000240756": {
        "summary": (
            "Requires media informatics or related study, Photoshop/Figma and "
            "MS Office including PowerPoint/Excel, communication and "
            "organization, and regular Stuttgart/Ulm attendance. German/English"
            " requirements range from good in the role profile to excellent in "
            "the application condition. Submit CV, cover letter, grades, "
            "enrolment proof, residence/work permit if applicable. Listed "
            "support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240756",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "240756": (
                "86b2f6b045ddb44d2ba6f615d9f79658"
                "fef15a19d02a579977273004f5632df7"
            )
        },
    },
    "j000240894": {
        "summary": (
            "Requires automotive, mechanical/electrical, industrial engineering"
            " or related technical enrolment; MS Office, good German/English "
            "and a valid class-B licence. Independent, careful teamwork and "
            "communication are expected. Listed support: Discounts for "
            "Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240894",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240894": (
                "c5c04c73dfff817b4e98ef3dc22b1ff9"
                "2bb0e83bcb73e7c0ea2eee0e0e3fe2e7"
            )
        },
    },
    "j000240908": {
        "summary": (
            "Requires business law/business/economics/law, business "
            "informatics, data protection/compliance or related study; "
            "process/documentation understanding and Microsoft 365. The profile"
            " specifies very good German and good English; the introductory "
            "application condition asks for excellent skills in both. Submit "
            "cover letter, CV, grades, enrolment proof, residence/work permit "
            "if applicable. Listed support: Hybrid Work Possible, Mobile Phone "
            "for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240908",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240908": (
                "ea6bb9df98e096c5d4ceee2b11c9a5b7"
                "cca9fbc871c877f44a6ffe0c4d76f2e8"
            )
        },
    },
    "j000240910": {
        "summary": (
            "Requires business law/business/economics/law, business "
            "informatics, data protection/compliance or related study; "
            "process/documentation understanding and Microsoft 365. The profile"
            " specifies very good German and good English; the introductory "
            "application condition asks for excellent skills in both. Submit "
            "CV, cover letter, grades, enrolment proof, residence/work permit "
            "if applicable. Listed support: Hybrid Work Possible, Mobile Phone "
            "for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240910",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "240910": (
                "a0e00163d7b8098e8584993cbf651391"
                "13979cf81bd369968c1d9964200fa9c9"
            )
        },
    },
    "j000240915": {
        "summary": (
            "Requires computing/media/business informatics or related study; "
            "JavaScript, TypeScript, HTML/CSS, initial Python, AI/LLM basics, "
            "engineering practices and GitHub; Language conditions ask for "
            "excellent German/English in the introduction and very good in the "
            "later profile. Server/API and design-system experience are "
            "preferred. Submit cover letter, CV, grades, enrolment proof, "
            "residence/work permit if applicable. Listed support: Hybrid Work "
            "Possible, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240915",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240915": (
                "f3c560b8ec371403320a70229a87b44b"
                "160a2214358633f12c1e53e85b2200e9"
            )
        },
    },
    "j000240917": {
        "summary": (
            "Requires computing/media/business informatics or related study; "
            "JavaScript, TypeScript, HTML/CSS, initial Python, AI/LLM basics, "
            "engineering practices and GitHub; Language conditions ask for "
            "excellent German/English in the introduction and very good in the "
            "later profile. Server/API and design-system experience are "
            "preferred. Submit CV, cover letter, grades, enrolment proof, "
            "residence/work permit if applicable. Listed support: Hybrid Work "
            "Possible, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240917",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "240917": (
                "88b0869a57c4f4a9d04d47c202f1dc7b"
                "cb2c2df966f1e021e2947b27553bbda0"
            )
        },
    },
    "j000240928": {
        "summary": (
            "Requires interaction/communication design, information "
            "management/corporate communication or related study; practical "
            "Figma libraries/variants/tokens and Adobe Photoshop/Illustrator; "
            "very good German/English. Blender/3D knowledge and generative-AI "
            "design experience are advantageous. Submit cover letter, CV, "
            "grades, enrolment proof, residence/work permit if applicable. "
            "Listed support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240928",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240928": (
                "8c52b68d0631143aa609611b72612e80"
                "12e3d4bd4b8d7dd757e7502d3ece18fa"
            )
        },
    },
    "j000240936": {
        "summary": (
            "Requires interaction/communication design, information "
            "management/corporate communication or related study; practical "
            "Figma libraries/variants/tokens and Adobe Photoshop/Illustrator; "
            "very good German/English. Blender/3D knowledge and generative-AI "
            "design experience are advantageous. Submit CV, cover letter, "
            "grades, enrolment proof, residence/work permit if applicable. "
            "Listed support: Hybrid Work Possible, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000240936",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "240936": (
                "e7e8fcfcd765363ca726f88c109f8a17"
                "f5838905120d49b80a17620b6121abbd"
            )
        },
    },
    "j000241242": {
        "summary": (
            "Requires technical, business-related or digital enrolment and at "
            "least 20 hours/week from December/January; strong AI/automation "
            "interest and very good German/English. Initial low-code/no-code, "
            "Power Platform/Databricks or AI experience is preferred. Submit "
            "cover letter, CV, grades, enrolment proof, residence/work permit "
            "if applicable. Listed support: Hybrid Work Possible, Mobile Phone "
            "for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000241242",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241242": (
                "46d841c920e5f4aeeb624c013888fab7"
                "433cfa0b0753cbbf038d06761c274476"
            )
        },
    },
    "j000241244": {
        "summary": (
            "Requires technical, business-related or digital enrolment and at "
            "least 20 hours/week from December/January; strong AI/automation "
            "interest and very good German/English. Initial low-code/no-code, "
            "Power Platform/Databricks or AI experience is preferred. Submit "
            "cover letter, CV, grades, enrolment proof, residence/work permit "
            "if applicable. Listed support: Hybrid Work Possible, Mobile Phone "
            "for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000241244",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "241244": (
                "01de48d8ef1f3e6359e199ea4eaa91f0"
                "239d21cfb3063192980c0f5f734ff379"
            )
        },
    },
    "j000241249": {
        "summary": (
            "Requires creative/media-related study, Adobe Premiere/After "
            "Effects/Photoshop, communication/strategy understanding and good "
            "German/English; the application condition also asks for excellent "
            "language skills. An IT component in the degree is preferred. "
            "Submit cover letter, CV, grades, enrolment proof, residence/work "
            "permit if applicable. Listed support: Hybrid Work Possible, Mobile"
            " Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000241249",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241249": (
                "de579342909ff14f2e7e4885793f5584"
                "7df24c6cf65f8468b02f3892dc998b6f"
            )
        },
    },
    "j000241251": {
        "summary": (
            "Requires creative/media-related study, Adobe Premiere/After "
            "Effects/Photoshop, communication/strategy understanding and good "
            "German/English; the application condition also asks for excellent "
            "language skills. An IT component in the degree is preferred. "
            "Submit CV, cover letter, grades, enrolment proof, residence/work "
            "permit if applicable. Listed support: Hybrid Work Possible, Mobile"
            " Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000241251",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "241251": (
                "bce5b5cc1c8b6cc19d2ac2bf978f494f"
                "47b25c2e571f3efe5a97bf30fa7cf7f9"
            )
        },
    },
    "j000241695": {
        "summary": (
            "Requires psychology/business psychology, HR-focused business or "
            "related enrolment; MS Office, independent structured work and "
            "excellent German/English. Initial HR experience is preferred. "
            "Submit cover letter, CV, grades, enrolment proof, residence/work "
            "permit if applicable. Listed support: Hybrid Work Possible, Mobile"
            " Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000241695",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241695": (
                "6192ce2aadf19b63fd5461072db9d06d"
                "fbb0cb0d0af8b63435608239172a8f1e"
            )
        },
    },
    "j000241697": {
        "summary": (
            "Requires psychology/business psychology, HR-focused business or "
            "related enrolment; MS Office, independent structured work and "
            "excellent German/English. Initial HR experience is preferred. "
            "Submit CV, cover letter, grades, enrolment proof, residence/work "
            "permit if applicable. Listed support: Hybrid Work Possible, Mobile"
            " Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "j000241697",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241697": (
                "327050555bf20ad1f5fd4b0ab1c7ee9a"
                "9e22127b74dea25b81343240faf1b633"
            )
        },
    },
    "j000241704": {
        "summary": (
            "Six-month legal internship in Eugendorf begins from 1 December "
            "2026, part time, preferably 30 hours/week. Support contracts, "
            "legal research, project advice and complaints. Applicants may be "
            "in the final stage of a law degree or already graduated, with good"
            " English, MS Office and independent teamwork. Stated gross pay is "
            "EUR 2,300, or EUR 2,500 for completed studies, on a full-time "
            "collective-agreement basis. Submit an application stating "
            "availability; flexible and mobile work are offered."
        ),
        "condition_evidence": [
            (
                "Final-stage law students OR applicants with a completed law "
                "degree are eligible; these are alternatives, not a "
                "completed-degree requirement for everyone."
            ),
            (
                "Good English, MS Office familiarity, ability to learn new IT "
                "programs, careful structured independent work, legal "
                "problem-solving and cooperative communication."
            ),
            (
                "Temporary six-month placement starting from 1 December 2026 in"
                " Eugendorf, Austria; narrative specifies part time, preferably"
                " 30 weekly hours."
            ),
            (
                "Gross remuneration quoted on the retail-sector collective "
                "agreement FULL-TIME basis: EUR 2,300 or EUR 2,500 with "
                "completed studies. No payment interval is explicitly stated in"
                " the description; do not represent these as guaranteed "
                "part-time take-home amounts."
            ),
            (
                "Application must state availability. Flexible hours and mobile"
                " working are offered."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: discounts for employees, mobile phone for "
                "employees, hybrid work, flextime."
            ),
            (
                "Additional structured facilities/support listed: company "
                "retirement, health benefits, events for employees, "
                "meal-discounts, inhouse doctor, parking, barrier-free "
                "workplace, canteen, café, good public transport. These names "
                "do not establish an individual cash award."
            ),
            (
                "Structured role labels: Part time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241704": (
                "305e32cbfecd5855f1c759e6013c4601"
                "c3f33a0bf29967e533e6ab6e5931cc1f"
            )
        },
    },
    "j000241953": {
        "summary": (
            "Mandatory internship in AMG sales planning and operations, "
            "Affalterbach, starts 1 December 2026. Support sales/production "
            "targets, risk assessments and analytical reporting for high-end "
            "vehicle ranges. Applicants must be enrolled in a business, "
            "industrial-engineering or similar bachelor/master course for a "
            "compulsory internship, with planning/reporting experience, strong "
            "numerical analysis, MS Office/data tools and very good German and "
            "English. Pay and a closing date are not stated."
        ),
        "condition_evidence": [
            (
                "Mandatory internship only, with bachelor OR master enrolment "
                "in economics/business, industrial engineering or a similar "
                "subject."
            ),
            (
                "Planning and systematic-reporting methods/experience, strong "
                "numerical and analytical abilities; confident MS Office and "
                "data-analysis software; very good spoken/written German and "
                "English."
            ),
            (
                "Creativity, reliability, initiative, communication, "
                "flexibility and teamwork are requested."
            ),
            (
                "Work covers volume/inventory targets, production bottlenecks, "
                "market risk/growth analysis, data visualisation and management"
                " presentations in Affalterbach, Germany. No pay amount or "
                "application cutoff is stated in the reviewed description."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: discounts for employees, hybrid work, mobile "
                "phone for employees, flextime, annual profit share."
            ),
            (
                "Additional structured facilities/support listed: health "
                "benefits, events for employees, coaching, company retirement, "
                "mobility offers, near-site childcare, parking, canteen, café, "
                "good public transport, barrier-free workplace, inhouse doctor."
                " These names do not establish an individual cash award."
            ),
            (
                "Structured role labels: Full time. Narrative requirements "
                "above take precedence where more specific; source publication "
                "end dates are not independently verified application closing "
                "dates."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241953": (
                "04eb337f9b96e4006f94d3c9a5d320da"
                "303bf1a4f89b60881f3e46c373fe70b2"
            )
        },
    },
    "j000242218": {
        "summary": (
            "Data analytics and BI internship in Leinfelden-Echterdingen is "
            "full time and temporary, available as soon as possible. Build "
            "Power BI reports and dashboards for budgets, resources and KPIs. "
            "Applicants need ongoing business studies, initial Power BI/Tableau"
            " experience, strong analysis, modern AI-tool experience and very "
            "good German/English; regular onsite attendance is required. "
            "IT/digital specialisation, collaboration tools and other languages"
            " are preferences. Pay is unstated."
        ),
        "condition_evidence": [
            (
                "Ongoing business/economics studies, ideally focused on IT, "
                "digital business or innovation; the specialisation is a "
                "preference."
            ),
            (
                "Initial visualisation-tool experience such as Power BI OR "
                "Tableau; strong analytical/conceptual thinking, independence, "
                "flexibility, digital affinity and presentation/communication "
                "skills."
            ),
            (
                "Very good experience using modern AI-assisted tools; very good"
                " spoken/written German and English including business English."
                " Additional languages are advantageous."
            ),
            (
                "Very good MS Office and collaboration-tool skills, such as "
                "Mural or Teams, are stated as desirable."
            ),
            (
                "Regular onsite presence is required. Narrative specifies a "
                "full-time temporary role at Leinfelden-Echterdingen as soon as"
                " possible; structured start date is 13 October 2026, which is "
                "not an application deadline."
            ),
            (
                "Flexible/part-time models, flexitime and mobile work including"
                " from European countries are described as employer offerings; "
                "do not treat them as an unconditional override of this role’s "
                "onsite/full-time requirements. Employee discounts, onboarding "
                "and free beverages are mentioned. Disability applications are "
                "welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, hybrid work, annual profit share, flextime."
            ),
            (
                "Additional structured facilities/support listed: events for "
                "employees, company retirement, mobility offers, health "
                "benefits, coaching, parking, good public transport, inhouse "
                "doctor. These names do not establish an individual cash award."
            ),
            (
                "Structured role labels: Temporary. Narrative requirements "
                "above take precedence where more specific; source publication "
                "end dates are not independently verified application closing "
                "dates."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242218": (
                "211d7cf990a23e4a649db2522c931ea2"
                "480010eaecd197418f5627de144ae997"
            )
        },
    },
    "mbg0000014": {
        "summary": (
            "Requires completed foundational "
            "business/economics/industrial-engineering studies, MS Office and "
            "good English. Part-time 20 hours/week over 2–3 days at "
            "€16.50/hour; start by agreement. Submit enrolment confirmation, "
            "standard-study-duration proof and semester-break plan. Listed "
            "support: Events for Employees, Coaching."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mbg0000014",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "115062": (
                "411cbecac5464d24231e11dbcf230651"
                "792c6223ce9de11649d3b9652c246d9d"
            ),
            "115060": (
                "2d47aea3c0f3372452961c8aba8545c9"
                "12a76e1f7a852cfb8dc66ccde42a223d"
            ),
            "205421": (
                "fd39bfecee8c197012f5f15d46c550eb"
                "e7ed72f2edb5b115cf5b4937c41a01f8"
            ),
        },
    },
    "mer0002sg2": {
        "summary": (
            "Applicants must currently attend high school, be at least a "
            "sophomore, and be enrolled in a career-technical programme in "
            "modern manufacturing, automotive technology, welding or logistics."
            " Listed support includes compensation, insurance, retirement "
            "contributions, bonuses, tuition assistance and paid holidays; "
            "terms must be checked. Listed support: Health Benefits, Company "
            "Retirement."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0002sg2",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "82444": (
                "a3bf8d6bba408094f3c68d886c0d5fdc"
                "f20a895f3c7bd137ff55f1a7eec7af35"
            ),
            "82443": (
                "dbb3fff914d17f8294c2d15048f024a8"
                "0fcc2736bc25646ee5fafdc9ec1807ae"
            ),
            "190680": (
                "a9e62fc45584ffdb4c9deabe9007af09"
                "ecafd34220cb3e96911ab76b491ffca7"
            ),
        },
    },
    "mer0004182": {
        "summary": (
            "Requires electrical-engineering, computing, physics or related "
            "study; good Python programming, familiarity with git, compilers "
            "and IDEs, and analytical working habits. Submit cover letter, CV, "
            "enrolment proof, school report(s), standard-study-duration proof, "
            "compulsory-placement proof if applicable. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004182",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "211634": (
                "1354db8d0b8b8278b8b758fd6e887f9b"
                "60683220e4225a3f8f97669183ce0e4e"
            ),
            "211632": (
                "46f4ddfdd4c9a9a9aa351c3f8081f8c3"
                "9b04d62aca73b58fcdba09c32e50b250"
            ),
            "211630": (
                "a069c19b43f4200465721ffd295d575f"
                "bcfd9831959f5e9a15fe6b156b2f6c75"
            ),
        },
    },
    "mer00041dc": {
        "summary": (
            "Requires current related bachelor’s/master’s study or graduation "
            "within the past 12 months and availability for six months full "
            "time. Vehicle-development interest is expected; practical "
            "experience, German and project-management skills are preferred. "
            "The workplace offers international exposure. Listed support: "
            "Mobile Phone for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00041dc",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "211446": (
                "de5dcdf112094ea9087d027f9d588377"
                "c865796a158a6d6fcf9492dedd170899"
            ),
            "211442": (
                "bb55d16a0b7fc7bd5cbe1257b83e5cdc"
                "3f1f631c026e5ac6b7029626ff435241"
            ),
            "211441": (
                "35f92935dd0ca8530c57cc9286fc7585"
                "c18a7b3f053f24c2774321bf9c05490d"
            ),
        },
    },
    "mer0004204": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, interest in"
            " machines/production equipment, and careful responsible work are "
            "expected. School minimum: good Hauptschulabschluss or "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004204",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "213469": (
                "f7bc496d17250955e4f24587a275ee89"
                "a4b23938e8a9ad347b6ad581de59b829"
            ),
            "213467": (
                "0953394cb2137954a9dd70ace450a317"
                "095bde48753f2924146c7c5e9270e4e6"
            ),
            "213464": (
                "2b4995bd189cdcbfbba34f966035e577"
                "89aa14272fc77a36cd2b8ad957fa6c06"
            ),
        },
    },
    "mer000420a": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss or Realschulabschluss. Training pay: EUR 1.242"
            " first year to 1.586 final year (site-dependent). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000420a",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "213488": (
                "57fb8888eebce76d687442023d7771f5"
                "0cc9f45dca5825b6b2dff9abb653c059"
            ),
            "213487": (
                "b5a20a021dd65f3ca3ffffc2ab5bb6e4"
                "7c9e149b298e7be2a4c316a0babe7bee"
            ),
            "213486": (
                "13c2ad7e47b138629e10dd59cfe72679"
                "af3e72eea0f75a1cdbbb4b0d3680cc74"
            ),
        },
    },
    "mer000422d": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Hauptschulabschluss or Realschulabschluss. Training pay: EUR 1.242"
            " first year to 1.586 final year (site-dependent). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000422d",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "213790": (
                "1fd05fc96202978d8ef979a92807d931"
                "f671688aad1a98b55c96f466e6537aaa"
            ),
            "213787": (
                "40834cc56eac5b31f8012ff16b31d0c5"
                "45ad9cef96424abc295eee13f7aaa049"
            ),
            "213786": (
                "35c57d6b504714214f9731cd6c139b7f"
                "d15500a66a5e050342de6dadd7a92995"
            ),
        },
    },
    "mer000422i": {
        "summary": (
            "Technical/practical aptitude, good maths and physics and "
            "English-language skills are expected. School minimum: good "
            "Hauptschulabschluss or Realschulabschluss. Training pay: EUR 1.242"
            " first year to 1.586 final year (site-dependent). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000422i",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "213796": (
                "50c70d0abe42f64599fdb52ae1f0ab78"
                "d54c4e902cbb3234dfcd32826e03988b"
            ),
            "213795": (
                "78bd82b8ee1f10729d7130e445e2842f"
                "c09fb601666cb2a8e96d46c10139a76d"
            ),
            "213791": (
                "26dc386fcc67a7248984e6db06da17e6"
                "0f2cfa542f08abe5def62cf3bfad03ae"
            ),
        },
    },
    "mer000422l": {
        "summary": (
            "Technical/practical aptitude, good maths, chemistry and physics, "
            "spatial ability, reliable colour vision and careful responsible "
            "work are expected. School minimum: good Hauptschulabschluss or "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000422l",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "213811": (
                "ab78d99be86cfce9ced7c5dfb178856a"
                "a0e76cb2a7a5ae4e3c1894489aae89c3"
            ),
            "213807": (
                "31e90eaa46a7fc95938a43c1061bf93a"
                "ebf66955286fcee438a2473647558036"
            ),
            "213806": (
                "d661337dc5a96194c43a88c2366e87df"
                "e63093a19cb353e02976858e4916064f"
            ),
        },
    },
    "mer000422o": {
        "summary": (
            "Technical/practical aptitude, good maths and physics and "
            "English-language skills are expected. School minimum: good "
            "Hauptschulabschluss or Realschulabschluss. Training pay: EUR 1.242"
            " first year to 1.586 final year (site-dependent). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000422o",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "213834": (
                "111e92bd82151111c014b39280ed5663"
                "1180788f4058548510bc3ddb9fb3a01c"
            ),
            "213832": (
                "ebda58fe89298d95982f9abfac3bb86e"
                "ef57fcb6c8b766a688e605c13c65e774"
            ),
            "213830": (
                "1446287e82e358982c80ea6e5ed05af5"
                "85f7435f89474037a9f1c118b18cc6a4"
            ),
        },
    },
    "mer00042fz": {
        "summary": (
            "Interest in health and social-insurance law, solid maths, good "
            "German and English, customer orientation and responsible helpful "
            "work are expected. School minimum: good Mittlere Reife. Training "
            "pay: EUR 1.303,50 first year to 1.488,50 final year. Submit CV and"
            " latest two school reports. Listed support: Coaching, Hybrid Work "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042fz",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215183": (
                "b74afe6befcc70c86ff6333ba35e53b4"
                "ecaf523d3800b3da206d8a523d746a0d"
            ),
            "215177": (
                "cacdf9e87517211a5159db9ab0d4819e"
                "8b4f0046b758cf1d8122f0766208fe3b"
            ),
            "215176": (
                "c2dd4e6be274b1f70442123724ed4728"
                "c4cc57906d19b5d8644c22cca8f33a5e"
            ),
        },
    },
    "mer00042g1": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042g1",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215205": (
                "062df0cc2ad93126d08e8dd35efb840e"
                "3589538b7a2bb4dfe7ca223a4e0982dc"
            ),
            "215200": (
                "2e75ed1ae855a0eb183e088d221fe5af"
                "e9d619752b771986fbf1615e4f29d79b"
            ),
            "215198": (
                "3f7ea47fc51c7f9eae1a752de0aa3454"
                "76ff5f47d6f011e15845625cf451e0d3"
            ),
        },
    },
    "mer00042g6": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042g6",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215227": (
                "e6525d5d146c0860527f668375dd462e"
                "ca00ef55fe2bb279da560ee6ce443735"
            ),
            "215220": (
                "83b39825333554490ff23232e3071886"
                "4401b57eb02604b5cb74c5753e15da20"
            ),
            "215219": (
                "b8599629d9f7b99f6163cb5bedc7b260"
                "9b0270691894df3e8b2f283072a52c69"
            ),
        },
    },
    "mer00042gc": {
        "summary": (
            "Requires initiative, resilience, creativity, independent teamwork,"
            " programming interest, logical analytical ability and mobility. "
            "Submit CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible. Entry routes: technical "
            "Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR "
            "avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042gc",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215241": (
                "a7b2582a425f611514b30142ada5990b"
                "0c3decd0b6274ff7895899459cd50605"
            ),
            "215238": (
                "4ae75cb05dad3ab62a28eda9f6345ed2"
                "30ee9c72a3a6b0d0c7d8e331e7ef7c67"
            ),
            "215237": (
                "8e93dc51d70cac4e3f19f7c1bb3a3a45"
                "fc1c46f284e8cfdc725886e84c31d91b"
            ),
        },
    },
    "mer00042gh": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042gh",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215292": (
                "ac4bb2f66c56e0ae9980ae4fa2565ae2"
                "94f0dddeeda65eb0d7dd1b59aefd8442"
            ),
            "215291": (
                "0fd27a56e424978b69e718c4ac9e2a95"
                "a0675d2ec744a7af18a589c11844d9ee"
            ),
            "215293": (
                "56e14b82839c09075843fce48fe8a22a"
                "2b4cb2c4c3f40413a6e5e86ec36fbb39"
            ),
        },
    },
    "mer00042go": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical/IT interest. Submit CV,"
            " 2 school reports. Listed support: Meal-Discounts, Mobile Phone "
            "for Employees Possible. Entry routes: technical Abitur "
            "avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR avg/math≤2.0 "
            "plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042go",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215352": (
                "b97ef202ff87c2968325b4d3e7a18c43"
                "e29f19b5ab3b35ed9e8c2c1da4078ee8"
            ),
            "215350": (
                "279fa14cf91148389fa7ec3ac887e479"
                "ccad4227d70f7186c1853bc7b117decc"
            ),
            "215348": (
                "a4cf76a850a397ffccaecc0f47ad382a"
                "6cfec20edc1b320c8c69c4a1b5008bb4"
            ),
        },
    },
    "mer00042gu": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042gu",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215366": (
                "42671e64866d0e810074ec8468bb4741"
                "0470392b05735d486557186637d4bded"
            ),
            "215365": (
                "e7d27c66d7f8b173050e2ddd0f2f31cc"
                "2820724057d2197cfb21d1a2b08616f9"
            ),
            "215368": (
                "f6a2fc977d26cec5775ea761b88d8c52"
                "9569cabb73a3b8ed3211771049a0b371"
            ),
        },
    },
    "mer00042gw": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042gw",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215375": (
                "fabf7bf7196212b41658b0bf9d56c8c9"
                "09eb97c0d5f0a25ac7b036e0d3b4b9ad"
            ),
            "215373": (
                "a34a884f03f648eecbc38097d03508ad"
                "c086cb174a72df5e11e072b060be7d43"
            ),
            "215372": (
                "021eba9aad2db017d4088f7640545a1e"
                "19de71b35aecb86eca6b18702924e77c"
            ),
        },
    },
    "mer00042h0": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042h0",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215380": (
                "20050ae83bd0bc840a21b330d64df9b9"
                "c8a6d5911db27676b60817f69d56f2c3"
            ),
            "215378": (
                "466f8611105716968747984e85922492"
                "6c7713e6ea4b421898742155a791cf9d"
            ),
            "215377": (
                "30e9d8851cf75f4365ebb172d0b888ca"
                "75d579308bb252c30e8c0fe96a0aeada"
            ),
        },
    },
    "mer00042h2": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042h2",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215389": (
                "2fe84f6eb39c719ae4b8a19967089dfb"
                "b5fc4980a976373afdeced31eb1231b9"
            ),
            "215387": (
                "25927052e712e361690078a72a07283f"
                "6a393a13c5191cc4080db1bada314787"
            ),
            "215386": (
                "df6e39d449bc7c2e8b6584f7c799f796"
                "154f86e719dd7df14b7de994a1590f1a"
            ),
        },
    },
    "mer00042h6": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042h6",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215396": (
                "e25ac67aa334c817ac4099a89e939b3d"
                "5356d358fc2b6f322b38150d2b4db48f"
            ),
            "215394": (
                "1d2a9e1552bc0267701f1b0fff219b86"
                "70c15704d832f2796d8c965c32a871ca"
            ),
            "215392": (
                "dd19da713f1fd3349c8dd53ca7daa631"
                "06b6bda7b37f6a6356a5b7a5a5efd549"
            ),
        },
    },
    "mer00042h7": {
        "summary": (
            "Requires initiative, resilience, creativity, independent teamwork,"
            " programming interest, logical analytical ability and mobility. "
            "Submit CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible. Entry routes: technical "
            "Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR "
            "avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042h7",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215400": (
                "fe68eec4ef0b99fcb43a766ff653105a"
                "110fcafe7113d7995f77eb1ad0f80e55"
            ),
            "215399": (
                "39bbd211d2c0a5a2bc6e64d7b2f028a4"
                "524f9af10bb868e64f6ef5e24107c39d"
            ),
            "215397": (
                "feb3d00ae58f533fd5c0b1338d3a1e3c"
                "8930bff8a76ef4b4f95b06d6c826f7c9"
            ),
        },
    },
    "mer00042h8": {
        "summary": (
            "Requires initiative, resilience, creativity, independent teamwork,"
            " programming interest, logical analytical ability and mobility. "
            "Submit CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible. Entry routes: technical "
            "Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR "
            "avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042h8",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215407": (
                "a3e00d2083094988d2288bda986fd4f6"
                "714e4a3446cfa9103ef8db3210a0ad1c"
            ),
            "215406": (
                "c7eeb0a6e4786e9ff8e83ce9e39ac588"
                "2c51806d883d04889c827002a61f75e4"
            ),
            "215405": (
                "ecf8f0b9278efdc729ebf017ba5ba0c9"
                "aed5d0decd920de6e217bce5a7180fb8"
            ),
        },
    },
    "mer00042ha": {
        "summary": (
            "Requires initiative, resilience, creativity, independent teamwork,"
            " programming interest, logical analytical ability and mobility. "
            "Submit CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible. Entry routes: technical "
            "Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR "
            "avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042ha",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215413": (
                "3d4ece8cc272c1ace9ae27549b35fbdf"
                "75f82f9b96be198de1ad1990bd44259c"
            ),
            "215412": (
                "546bc4c7df3b2be20d3ee45cd377fd8b"
                "32f052bf51bd421ec8dbf8f3a1c02feb"
            ),
            "215410": (
                "e5385868355820c207cee3f2e5d21e32"
                "203136ab1f0a951d58b7d29071a9e10d"
            ),
        },
    },
    "mer00042hb": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042hb",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215420": (
                "fd032daaba36e7b15ed69749230f9ad1"
                "f98265504f497126d3b6ce69cd5c61a0"
            ),
            "215418": (
                "7f84ebd1c3c53ccdf896f3fcdd833a81"
                "03b9d4687936eedfd32949ccce904dea"
            ),
            "215417": (
                "1f1ef6903eb94623e7cc4bec8cab8db4"
                "0365ca3b7fb82d43a4210c0c5ff804b9"
            ),
        },
    },
    "mer00042hc": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042hc",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215425": (
                "b92b471a47b2eb7c3cf98f80317efef4"
                "98c535a8ddea90bca7c139c06755c94c"
            ),
            "215424": (
                "b37c875ba81eb4aed4e67b064feb92f4"
                "5b9429cb0e2b8135da09709fcb518067"
            ),
            "215423": (
                "5e3f1052274ff9f40367f274c4fc17fb"
                "d2787ae58bee29c059d01a9bcd0a5dba"
            ),
        },
    },
    "mer00042he": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical/IT interest. Submit CV,"
            " 2 school reports. Listed support: Meal-Discounts, Mobile Phone "
            "for Employees Possible. Entry routes: technical Abitur "
            "avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR avg/math≤2.0 "
            "plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042he",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215431": (
                "feb1eae6b3c694df6bd70de7622b2cd6"
                "57775c9906e7b1c5ff953ae5fc7360e4"
            ),
            "215430": (
                "c547ce19622d311e15354191aae23f24"
                "95126fde5354783c701391ccfb9ec204"
            ),
            "215429": (
                "34ba70a509bfc302f76789d225b47641"
                "e31259366615ba578973fe1394f5cf84"
            ),
        },
    },
    "mer00042j2": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042j2",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215595": (
                "55a66d43f874660d2757c0a986d257b8"
                "01e22857c166afc0a8fe406eba1e9dae"
            ),
            "215594": (
                "1103ca9513c7dfc1a537efd25d26f18b"
                "799a644bd94d776fbb03a43c0836ca5f"
            ),
            "215590": (
                "a52850f1c0052e8c676d33be6e9a88aa"
                "f386f2676f5bd6206ef544ccd8ec30f2"
            ),
        },
    },
    "mer00042j5": {
        "summary": (
            "Paid mechanical-engineering/production B.Eng. dual study runs 1 "
            "October 2027–30 September 2030. Theory is at DHBW Stuttgart; "
            "practical assignments are mainly in Stuttgart, with a possible "
            "worldwide placement. Applicants need analytical ability, "
            "initiative, teamwork, technical/business interest and willingness "
            "to move. Apply online with a CV and the latest two school reports;"
            " school admission minima are delegated to the linked requirements."
            " YouthBW Deutschland-Ticket support is offered; pay amount is "
            "unstated."
        ),
        "condition_evidence": [
            (
                "Three-year study period 1 October 2027 through 30 September "
                "2030; theory at DHBW Stuttgart, practical placements mainly in"
                " Stuttgart with a possible worldwide assignment."
            ),
            (
                "Initiative, engagement, flexibility, communication/teamwork, "
                "strong logical/analytical thinking, technical/business "
                "interest and willingness to be mobile."
            ),
            (
                "Online application requires a CV and latest two school "
                "reports. School qualification minima are provided on a linked "
                "homepage and are not independently specified in this "
                "descriptor."
            ),
            (
                "Remuneration during dual studies is offered without an amount."
                " YouthBW Deutschland-Ticket supports mobility. Successful "
                "completion improves employment prospects; no guaranteed "
                "permanent job is stated. Disability applications are welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, annual profit share, flextime, hybrid work."
            ),
            (
                "Additional structured facilities/support listed: "
                "meal-discounts, events for employees, coaching, health "
                "benefits, company retirement, mobility offers, parking, "
                "near-site childcare, canteen, café, good public transport, "
                "barrier-free workplace, inhouse doctor. These names do not "
                "establish an individual cash award."
            ),
            (
                "Structured role labels: Full time (part time possible), "
                "Temporary. Narrative requirements above take precedence where "
                "more specific; source publication end dates are not "
                "independently verified application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "215631": (
                "50def1cdac1c7001a20995f790bf8fbc"
                "806f1f971f61e4b3e510a630ab01250d"
            ),
            "215628": (
                "317e8ef9f15dff7e982e9f8cb7cc31e2"
                "541e3916001874ed190f88c2dbc1885c"
            ),
            "215622": (
                "56989f458f3eb18ed43ed6fc70b1b832"
                "29518b74ce65cecdbd1b5f06af4b24ac"
            ),
        },
    },
    "mer00042j6": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042j6",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215633": (
                "87f0e2446609907e61d56ef1586a3381"
                "f9cbb7ffe8d5eeb44c36b992ad6b2421"
            ),
            "215624": (
                "07f57955cd0835f5cdb52c44208813ee"
                "6debfdfc0d3dcf66c81b7a02f4617d9d"
            ),
            "215623": (
                "5f3c61e1686b5321bffb6a5692d7db92"
                "bc436e60154be948862936781cb7db6b"
            ),
        },
    },
    "mer00042ja": {
        "summary": (
            "Requires initiative, resilience, creativity, independent teamwork,"
            " programming interest, logical analytical ability and mobility. "
            "Submit CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible. Entry routes: technical "
            "Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR "
            "avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042ja",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215646": (
                "6d181ce7d726f75ce114b3ec7ec12bae"
                "9bd2ff5f14547a167df9896026fdc9c3"
            ),
            "215645": (
                "f5a56e0862ae1f17725680f1d833763d"
                "d7981f909fd2ea33f33960df871021bf"
            ),
            "215642": (
                "60ebc0b6f585f6851576d9a3fd183856"
                "2aad4d68401d9a074ec4e05783dba668"
            ),
        },
    },
    "mer00042jc": {
        "summary": (
            "Paid computer-science/AI B.Sc. dual study runs 1 October 2027–30 "
            "September 2030. Study at DHBW Stuttgart and gain practical "
            "experience mainly in Stuttgart, with a possible worldwide "
            "placement. Applicants need analytical and programming interest, "
            "independent teamwork, commitment, resilience, creativity and "
            "mobility. Apply online with a CV and the latest two school "
            "reports; linked school admission minima still apply. YouthBW "
            "Deutschland-Ticket support is offered; the pay amount is not "
            "specified."
        ),
        "condition_evidence": [
            (
                "Three-year programme 1 October 2027 to 30 September 2030; "
                "theory at DHBW Stuttgart, practical assignments mainly in "
                "Stuttgart and possibly worldwide."
            ),
            (
                "Commitment, resilience, creativity, independent/team-oriented "
                "work, interest in conceptual design/programming, "
                "logical/analytical thinking, technical interest and "
                "willingness to move."
            ),
            (
                "Online application attachments: CV and latest two school "
                "reports. School entry thresholds are delegated to linked "
                "admission information; no unverified threshold is invented."
            ),
            (
                "Paid dual study with no stated amount. YouthBW "
                "Deutschland-Ticket mobility support is offered; career "
                "prospects after successful graduation are not a guaranteed "
                "job. Disability applications are welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, annual profit share, flextime, hybrid work."
            ),
            (
                "Additional structured facilities/support listed: "
                "meal-discounts, events for employees, coaching, health "
                "benefits, company retirement, mobility offers, parking, "
                "near-site childcare, canteen, café, good public transport, "
                "barrier-free workplace, inhouse doctor. These names do not "
                "establish an individual cash award."
            ),
            (
                "Structured role labels: Full time (part time possible), "
                "Temporary. Narrative requirements above take precedence where "
                "more specific; source publication end dates are not "
                "independently verified application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "215658": (
                "ab6e4c6cfaf17c4e26f94975aae60c8c"
                "51a87d866c7936eeab71d4d6b62066f8"
            ),
            "215656": (
                "733eed24b0890fa5282f3f23e96e2919"
                "ed79d9f24ee38fe2f2fab28238ba4efd"
            ),
            "215653": (
                "0ec1e4b7ae74abe0fd505c732b123055"
                "432d1d50ae11da0d7a14f63fa912b5fa"
            ),
        },
    },
    "mer00042je": {
        "summary": (
            "Requires initiative, resilience, creativity, independent teamwork,"
            " programming interest, logical analytical ability and mobility. "
            "Submit CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible. Entry routes: technical "
            "Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR "
            "avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042je",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215663": (
                "8fb287b091f4e0f298a4f5537386e796"
                "470470aece8e09e435f4a6963f4e49ef"
            ),
            "215660": (
                "108765d39df08a6178118d4ad37f0040"
                "f4f61b1a303b79afe7c6cb7e5f79a7fb"
            ),
            "215659": (
                "58bc84e2942e458cd4cf806953a4a601"
                "995c43abd1342f1c8ab07990c1fe54ad"
            ),
        },
    },
    "mer00042jf": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042jf",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215679": (
                "7bce70cb81d047c0b97bb33b6811fb5b"
                "e5260c4d583801186eab99be33a38cdf"
            ),
            "215678": (
                "bb6616c65753eb734a569e159314f365"
                "109d4a640adf1b1a2b9eb40767d23e34"
            ),
            "215682": (
                "e36e84e4e95a83b2cfa848eb09c7eea0"
                "54f05ddf204b33f6c73695bd3695b8f3"
            ),
        },
    },
    "mer00042jj": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical/IT interest. Submit CV,"
            " 2 school reports. Listed support: Meal-Discounts, Mobile Phone "
            "for Employees Possible. Entry routes: technical Abitur "
            "avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR avg/math≤2.0 "
            "plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042jj",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215691": (
                "5c6316442c019797662ed493f3b8d667"
                "5fbdc6c4c42efbb3158e1376fdb22560"
            ),
            "215690": (
                "d8e946415cdd4c0acd482ca3d51d2628"
                "e8eca5a15f68a201998a040352ac4df2"
            ),
            "215689": (
                "98ade117e4ff4f7f9deb7a5a4fb3849d"
                "4380802c7826fe8575fdd3a494b13ef7"
            ),
        },
    },
    "mer00042lf": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Submit 2 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042lf",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215941": (
                "17caa54339b4f25909af1a2c13dfd008"
                "2b0fc87f129cabd9533d691e5f67c961"
            ),
            "215946": (
                "131ec654ad6844092a29cd6478b9d276"
                "c1c5fadb78b08606dd5df354b5d48764"
            ),
            "215945": (
                "e60adf72ac58d8d472f4d27852985056"
                "4db8f06a611edf1c14619e86a5e68ab7"
            ),
        },
    },
    "mer00042lr": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Hauptschulabschluss. Training pay: "
            "EUR 1.227 first year to 1.398 final year (site-dependent). Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042lr",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217038": (
                "942da053d6e8ea461a64a3ff4a0f7bf9"
                "7fe671a988b9b0e6cc217463225110a1"
            ),
            "217037": (
                "584f94721af018235c2284600cc0043c"
                "d07c6493fd4aa938b290ae2f7c94cefb"
            ),
            "217036": (
                "e0d41eeb81e0b520576fb5937fb4f84f"
                "66c3c4d8c8d5da059f2725951edabb49"
            ),
        },
    },
    "mer00042ls": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Hauptschulabschluss. Training pay: "
            "EUR 1.295 first year to 1460 final year (site-dependent). Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042ls",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217044": (
                "db91281204754726b06ea12cf5a90e49"
                "1a6c9fd78777faa4196de9c277d4ce0a"
            ),
            "217043": (
                "daf96fcaa1339f13fad79ea2437c94a4"
                "778c767b4a6a2a742a6f53e418953249"
            ),
            "217042": (
                "49f520682eff8fdea2deea4a59c441a5"
                "1cd238bb4a37b78aaf4aea9219fa954d"
            ),
        },
    },
    "mer00042m0": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, creativity and interest in materials/form/colour design, "
            "and careful responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Submit 2 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042m0",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215940": (
                "7cb5c11d875bee6146503c0de6bbc43f"
                "06f7bf34571ed32f04102dc6cad291eb"
            ),
            "215938": (
                "4c2c6801b899377142a105a81bbc8d40"
                "9e31720878253b40a079799998409359"
            ),
            "215937": (
                "822065f0b0f3dcff326cc474d405cb34"
                "3df907cfff9457ab6c1210a51d41eaa4"
            ),
        },
    },
    "mer00042m5": {
        "summary": (
            "Technical/practical aptitude, good maths, chemistry and physics, "
            "spatial ability, reliable colour vision and careful responsible "
            "work are expected. School minimum: good Hauptschulabschluss. "
            "Training pay: EUR 1.242 first year to 1.586 final year "
            "(site-dependent). Submit 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042m5",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215958": (
                "08cb17350500449c03763f6cb850df76"
                "7c055068d4c09939373a4e5f41ec9fd3"
            ),
            "215955": (
                "75c83978129e16cdc4feb51bf1f41123"
                "f01abc74089add57fb7b021bb78b9256"
            ),
            "215953": (
                "1811274fec452fa3a5d19f0f35a781df"
                "a8bce68d5509d0adbf0dbdf1e08fa8cb"
            ),
        },
    },
    "mer00042mj": {
        "summary": (
            "Exclusively for young people with a learning impairment under §64 "
            "BBiG. Requires attendance at Hauptschule/special-needs school or a"
            " one-year vocational-preparation programme, technical/practical "
            "aptitude and interest in vehicles/technology. Training pay: EUR "
            "1.242 first year to 1.586 final year (site-dependent). Submit 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042mj",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215985": (
                "59fe6a5a3eb9ef62f1f35296afc86f20"
                "cbd08798470a9847590d6925bb5e1b59"
            ),
            "215984": (
                "b1a8f29f182c9d45bcec4e77bff19a27"
                "df2733c0d0399a10f353179a2b092b69"
            ),
            "215980": (
                "3703713f3d41c47bdd032c76ff5ae7bc"
                "fb7f269ee6e0a47d6e776395830b2ae0"
            ),
        },
    },
    "mer00042mk": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicles/machines/equipment and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Submit 2 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042mk",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215986": (
                "46ae2daf5be604dba4e0b5644d2d9b37"
                "751c4724f6d16a58bfbe53ed57f8d953"
            ),
            "215990": (
                "741b32b0542fa9e205a8b9fe8de6dbe1"
                "e486b4ad6dc7eea2e7610ce230760558"
            ),
            "215987": (
                "94879acdc13f824595caf5c051a42438"
                "396a3541aaa9ea272f7373d5599d1223"
            ),
        },
    },
    "mer00042ml": {
        "summary": (
            "Requires good computer knowledge, solid maths, good German and "
            "English, basic commercial understanding and careful responsible "
            "work. School minimum: good Realschulabschluss. Training pay: EUR "
            "1.242 first year to 1.586 final year (site-dependent). Submit 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042ml",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "215996": (
                "daed1c7292f9a6b7bda584ca36222667"
                "257d21e47bbe4013a8ad92135d8d5f2a"
            ),
            "215994": (
                "e70b3576298d3e76dd94c4e7a8f42c41"
                "4cf854ceaced7dc9bc806307d3d447d5"
            ),
            "215993": (
                "f114a3757d3526b8313fac3538ceb36a"
                "2683ac31d6f8ba38ce66ac38220be129"
            ),
        },
    },
    "mer00042mn": {
        "summary": (
            "Technical/practical aptitude, good maths and physics and "
            "English-language skills are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Submit 2 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042mn",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216002": (
                "bd9d3543775077ff20811c80bb7e24c8"
                "5b47aa205c5655ae22fcc1bed94807e1"
            ),
            "215999": (
                "ce8224cb0141510633ca0c2f359c9aa2"
                "bb0b8d8db9e9efb6423bd8121e285a77"
            ),
            "215998": (
                "928c51a9f90fdfb8eebfe17428322d5e"
                "839f394f80cbef991baf506777abe0de"
            ),
        },
    },
    "mer00042mo": {
        "summary": (
            "Requires good computer knowledge, solid maths, good German and "
            "English, basic commercial understanding and careful responsible "
            "work. School minimum: good MSA school-leaving certificate. "
            "Training pay: EUR 1.272 first year to 1.402 final year "
            "(site-dependent). Listed support: Meal-Discounts, Mobile Phone for"
            " Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042mo",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216020": (
                "4f6c5bc75f6c893715f205513612c157"
                "914390ae6905b27849d655d241975982"
            ),
            "216018": (
                "a372425402f71d1c6fa8db9ef152e907"
                "90e699b9fca49d25cf94b75a42f7d6c3"
            ),
            "216016": (
                "6f138ac349228454bc63a4774166203a"
                "1f0caed9ed27754fb89938ae226a7d63"
            ),
        },
    },
    "mer00042mq": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Submit 2 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042mq",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216027": (
                "d2d9bdfc53032d5d250c6390a5c582e3"
                "aed313e82a3de0bedeaffbfb4891a01a"
            ),
            "216026": (
                "8922e5e12049b1e77431670a64fa8820"
                "03c1a7558dadfcbd393e54cb7e9a1697"
            ),
            "216023": (
                "cf21c471031ba19d38f16414b35558da"
                "47c9a13bd5c6e28d85298d852853d6a6"
            ),
        },
    },
    "mer00042mr": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern manufacturing, spatial ability and careful responsible work"
            " are expected. School minimum: good Hauptschulabschluss. Training "
            "pay: EUR 1.242 first year to 1.586 final year (site-dependent). "
            "Submit 2 school reports. Listed support: Meal-Discounts, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042mr",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216036": (
                "f99b27843049dd2626a11636081593b1"
                "3e3cfc6f81b42c2f80f10bbce4f35b6b"
            ),
            "216034": (
                "24fb939776b447c68088b64f0797d1ed"
                "dc9a765b0285b566a882c64a1b68f891"
            ),
            "216031": (
                "e33e1833c77e843897c78e7f37ff276e"
                "ccecd66dd4eb2022b89b3af02b91f776"
            ),
        },
    },
    "mer00042p3": {
        "summary": (
            "Technical/practical aptitude, good maths and physics and "
            "English-language skills are expected. School minimum: good MSA "
            "school-leaving certificate. Training pay: EUR 1.272 first year to "
            "1.461 final year. Listed support: Meal-Discounts, Mobile Phone for"
            " Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042p3",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216174": (
                "6dbe5a82365e7f7e3bc118415e9cb8c2"
                "858fcebc9b5ca364c641dee4911e353b"
            ),
            "216173": (
                "4e93a9ebf5396fb5221e2f66f7c058cb"
                "c316601a09e80eeb7e5a282761eb6486"
            ),
            "216170": (
                "dabdf98aa0e4b09196c206e1006267dd"
                "376739591423f911ab2376791bc26589"
            ),
        },
    },
    "mer00042qp": {
        "summary": (
            "Requires general, subject-restricted or applied-sciences "
            "university entrance qualification with good to very good grades, "
            "especially maths; analytical independence, communication/teamwork,"
            " creativity and mobility. Three-year study, with possible overseas"
            " study/practice. Further minimums are referred to the homepage. "
            "Submit CV, 2 school reports. Listed support: Coaching, Hybrid Work"
            " Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042qp",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216296": (
                "01965ced85141d54379b07c965f72e9f"
                "6251ea2a3357876dd959d1dfa2eb5337"
            ),
            "216294": (
                "7529c02fb6bd8b3a1332f91d032976c6"
                "701b66cf540788aeb1cd95f2b935b083"
            ),
            "216293": (
                "486087368b364b947f73fdb551eaa41a"
                "f72aaba2ac8e1de56a26d76708258acf"
            ),
        },
    },
    "mer00042r5": {
        "summary": (
            "Requires current bachelor’s or master’s enrolment and availability"
            " for the stated 5–6-month internship period; the descriptor gives "
            "a maximum of 12 months. No specific grade, language or citizenship"
            " requirement is stated. Listed support: Events for Employees, "
            "Discounts for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042r5",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "216530": (
                "8b819d8d691c9e22540451855da42295"
                "ec7bc8fd5f932046dd300d0a5d379bf9"
            ),
            "216529": (
                "6e48583573b9afe402cabc54196d0e64"
                "60bfe8d8485bb3b56b55fea4420b8498"
            ),
            "216528": (
                "f809a6c34948abdaefdf9c017f4707d9"
                "b04d0666eb90ed3596b45db1fb913980"
            ),
        },
    },
    "mer00042ts": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical/IT interest. Submit CV,"
            " 2 school reports. Listed support: Meal-Discounts, Mobile Phone "
            "for Employees Possible. Entry routes: technical Abitur "
            "avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR avg/math≤2.0 "
            "plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042ts",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "216711": (
                "2e259977048d27348ecda836b1bbaaf7"
                "0d21060ce67f9dbd5bcfa8875e755211"
            ),
            "216708": (
                "4aa426f11b288357237edf018d7762aa"
                "c05275da3f3bd8f01fb981b157ba2019"
            ),
            "216707": (
                "e6c99c861ec896e93440caecc5a8baf4"
                "3c7a1fcc8274d43cc41dbba9c16b65a9"
            ),
        },
    },
    "mer00042w9": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Hauptschulabschluss. Training pay: "
            "EUR 1.242 first year to 1.586 final year (site-dependent). Submit "
            "CV, 2 school reports. Listed support: Meal-Discounts, Mobile Phone"
            " for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042w9",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217027": (
                "5960cc0a88899d92e98c57d3f41610f5"
                "aadd3f6d7db2bdd70a8e4b37bc92f3e8"
            ),
            "217026": (
                "9eda020401385e9c25c2ebf363c4b9d8"
                "14033af603fed873aa6a569bc9dbf766"
            ),
            "217025": (
                "3d2dfdd774032745f895ea3e54d9819b"
                "7a42127761823a936bec21498074a6e9"
            ),
        },
    },
    "mer00042wa": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Hauptschulabschluss. Training pay: "
            "EUR 1.272,00 first year to 1.402,00 final year (site-dependent). "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042wa",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220192": (
                "7d3584b6aaab5ff573532f7323a32e51"
                "4308961c62df214e15aa15de57724b07"
            ),
            "220189": (
                "1b12207dc167bea1d3e7627f80ad88e2"
                "7b7d16b760caade4214c9ef8829f8470"
            ),
            "220187": (
                "c72fc8bad0d02d4bedf1c8812c022ccf"
                "6e37ada2a9c4ade6de3184415b9d5ebc"
            ),
        },
    },
    "mer00042wb": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Mittelschulabschluss. Training pay:"
            " EUR 1.277,49 first year to 1.401,07 final year (site-dependent). "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042wb",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217034": (
                "9d750cdd8712a9dd9f20736de8246344"
                "2c9ac8d46b679c44040ee42964ad07eb"
            ),
            "217033": (
                "d5132c6694e8b0584a543fe41752443f"
                "ba7b4aceb0051e50cf856ddf54532516"
            ),
            "217031": (
                "be0f4b9ddf5ae29dff2cfba8f3182f13"
                "d02a121803b34e1561459c24f5efe4bd"
            ),
        },
    },
    "mer00042z0": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042z0",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217169": (
                "76292d8249da78a952e3cefa9e92ce55"
                "fd50cfa0687bdec9470775134d7a6cc5"
            ),
            "217168": (
                "1064898c92aeb656b1d25d2334a73cac"
                "d7629dee0a5501e464044dcd96d8c438"
            ),
            "217164": (
                "f61dface515e59bb869534060af1e7e8"
                "ff5924f8ccb0840a831f9ee94efd7ee2"
            ),
        },
    },
    "mer00042za": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042za",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217246": (
                "ce2ef17f28f0d83ef317de34bb02d8cf"
                "df33162f68449ab438b156ac6c2b4bf0"
            ),
            "217243": (
                "51e6564cd0d4e40bcea567ca720620f6"
                "be5e34b5fcdcd540d1298d661c87698f"
            ),
            "217241": (
                "f3c4e31311d877f560a440e290745e3b"
                "e6b6e75b772435cf6e3340ccce7c7892"
            ),
        },
    },
    "mer00042zb": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and interest in technical/business "
            "subjects. Submit CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Entry routes:"
            " technical Abitur avg≤2.7/math≥8 points; business avg≤2.5/math≥10;"
            " FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042zb",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217244": (
                "ad928d7862822f3bc70167d541cca78e"
                "b0e3a080b5fa9be11814f9c3f0e572a9"
            ),
            "217240": (
                "314632b9f878e43aa550a9fbf46c3eb4"
                "477c1024051b112a20b3e4e87a9fd0b6"
            ),
            "217248": (
                "e1a9412675883ecaa80d87f5c17afc82"
                "bee1fc4799ac7c4153d54fffebb35d88"
            ),
        },
    },
    "mer00042zd": {
        "summary": (
            "Technical and practical aptitude, good maths and physics, interest"
            " in automotive electronics/mechanics, and careful, reliable work "
            "are expected. School minimum: good Realschulabschluss. Training "
            "pay: EUR 1.242 first year to 1.586 final year (site-dependent). "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042zd",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217259": (
                "8f2787437b28880fb6a2d2757759fc50"
                "423767d44db645d57eab3bcaff50c8b2"
            ),
            "217257": (
                "acf214e3ecbf14cea1a1c566613328a1"
                "3476ace076e216548be1ae467a91b295"
            ),
            "217256": (
                "e24179fbad3c8b7e570287305147b32f"
                "9f7c4a0ab997e49a621cfafb1ca5618e"
            ),
        },
    },
    "mer00042zf": {
        "summary": (
            "Requires good computer knowledge, solid maths, good German and "
            "English, basic commercial understanding and careful responsible "
            "work. School minimum: good Realschulabschluss. Training pay: EUR "
            "1.242 first year to 1.586 final year (site-dependent). Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042zf",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217265": (
                "15424cbe5f25ad90da327948a8251928"
                "5e0be63be75b03bebd3aa424327d9189"
            ),
            "217262": (
                "e06333068dc200325984d21541b85fba"
                "242f80c124d11340f1708119da3e2f3f"
            ),
            "217261": (
                "43f06959fc9432fd50cd501575db9d7c"
                "f8ca975a24c5c5652112cf90cdcff788"
            ),
        },
    },
    "mer00042zh": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042zh",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217268": (
                "8e987558d85cdaa81b27d163c955fce5"
                "5f36fa05edcc9063616aeb640617e53e"
            ),
            "217272": (
                "ef3ce397017600fb6f0ba4ea3140dc8a"
                "6a4894f098188bbd753a008ea1f10e4b"
            ),
            "217270": (
                "fe9d6cb08d0e2d48897d579294ef76b4"
                "6c99acd32003284515ed7eb81eb2ab73"
            ),
        },
    },
    "mer00042zi": {
        "summary": (
            "Technical/practical aptitude, solid maths and physics, spatial "
            "ability, interest in machines/production equipment and careful "
            "responsible work are expected. School minimum: good "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00042zi",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "217284": (
                "d79a241529e91c25a3f82e45ea86bc13"
                "8048a1a387f299a119587de45aa579ad"
            ),
            "217282": (
                "7926bd1d20967687a159ea34633aa8a7"
                "5c9800dcee2fef8699000dd84341ed9d"
            ),
            "217279": (
                "1b3196f5fa9609d38281428936b7f2c8"
                "2b12e83caea8657409d273f8b10c42c7"
            ),
        },
    },
    "mer0004374": {
        "summary": (
            "Technical/practical aptitude, solid maths and physics, spatial "
            "ability, interest in machines/production equipment and careful "
            "responsible work are expected. School minimum: good "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004374",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218100": (
                "6e6dffed04a495dc1ee1a16be6ee5263"
                "e41861e093715d461df347c382538831"
            ),
            "218099": (
                "84bf5e0e230267b4affc3eeeb9bea798"
                "bb9e021fb313ca9d93b326f609781f39"
            ),
            "218097": (
                "571882971ac44f6aa4d593f3ab9e72ff"
                "82979961d9e24c87dda3cfe8a940a488"
            ),
        },
    },
    "mer0004376": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Realschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004376",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218114": (
                "bb61dab55c0a42daae0d99c935683120"
                "02346a866dbbc16d3296757c5d655e15"
            ),
            "218112": (
                "f8436e33f2f48cc69ac7fc29f800404c"
                "dcca513eb32a02727ab99bf4633e41da"
            ),
            "218110": (
                "3b7e5897b6f08ad2d96a2e4adea3a82a"
                "9cb269cc2a243e9409f245f0adc73d9e"
            ),
        },
    },
    "mer0004379": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern manufacturing, spatial ability and careful responsible work"
            " are expected. School minimum: good Realschulabschluss. Training "
            "pay: EUR 1.242 first year to 1.586 final year (site-dependent). "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004379",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218135": (
                "4032764f04ea58cf769bf4b6591866ca"
                "85085f90076b3117e91e9ae2e7e4e6b1"
            ),
            "218133": (
                "3a2110e985062e00fb8c34a6f947165d"
                "864e0bc695512daeebbed6317e310436"
            ),
            "218130": (
                "91eb2c9d02116bf0858a46416fddc67e"
                "48c9eba9f7f0b8535431b1c540fb7650"
            ),
        },
    },
    "mer00043co": {
        "summary": (
            "Technical/practical aptitude, good maths and physics and "
            "English-language skills are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.303 first year to 1.563 "
            "final year. Submit CV, school report(s). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043co",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218596": (
                "dca355eb27a789d350bf738f2f83c11b"
                "bb49d1d4c313df2fd4b951c275500f67"
            ),
            "218593": (
                "99fce66fc890ccd69d3ecfd5e430427d"
                "de5fc641e17bf66417e5fa6760dad03a"
            ),
            "218592": (
                "704532d8fffc5be744078de6bb4462f4"
                "fdafe58997fb6e6c0b89c7de3ecacd57"
            ),
        },
    },
    "mer00043cr": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.303 first year to 1.563 "
            "final year. Submit CV, school report(s). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043cr",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218603": (
                "f6efebf329d20e3276bfbc568b8d9e69"
                "3485f5222bbe16b93ce7a70fa41db53f"
            ),
            "218601": (
                "ea6351a72897519aef49cc5930a14345"
                "7058062e4d16b38fa9d6e5628947b846"
            ),
            "218599": (
                "b3e3da41ccb4c0624b5b191e4c026c49"
                "e5ef1b11c037f9ae52e0688965cf93e4"
            ),
        },
    },
    "mer00043cu": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.303 first year to 1.563 "
            "final year. Submit CV, school report(s). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043cu",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218611": (
                "095bae3d5c526bb4f7ae179d4a833722"
                "042cb4f8ebc9d104c0158c894228ef77"
            ),
            "218609": (
                "b413138c91cafa4cb0c30fbbf16e5a37"
                "02e75503b87f8a785d3c87292213e9c9"
            ),
            "218608": (
                "7c58d9f8d89680d6b483a7b44676eb51"
                "c970589662afd9bfc7fd8e5bd915d1d0"
            ),
        },
    },
    "mer00043cv": {
        "summary": (
            "Requires good computer knowledge, solid maths, good German and "
            "English, basic commercial understanding and careful responsible "
            "work. School minimum: good Realschulabschluss. Training pay: EUR "
            "1.303 first year to 1.488 final year. Submit CV, school report(s)."
            " Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043cv",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218615": (
                "d75df72a9e4e61d9c302e4749dd036fa"
                "793fd62d2bafdb372e7f1c66441e9743"
            ),
            "218614": (
                "7e9f62a45d4dd66dba39bfdc0a1c634d"
                "d81879f887fe302bd89934f3333822e0"
            ),
            "218613": (
                "12d3ac50ce26e9d248eea94c3aecfa56"
                "75f04442a03c81507f3bd3e1dec2652c"
            ),
        },
    },
    "mer00043cw": {
        "summary": (
            "Technical/practical aptitude, solid maths and physics, spatial "
            "ability, interest in machines/production equipment and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.303 first year to 1.563 "
            "final year. Submit CV, school report(s). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043cw",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218628": (
                "3689170accbd0888f9a6531a38453c25"
                "3266dc3161e91c8a5bc2c8954ce2aeb7"
            ),
            "218627": (
                "fed9b7e7ae8c3ad3e8180a9f24a62d4d"
                "f8331ad3833b14feb55219d859d16375"
            ),
            "218623": (
                "b667189bbf53bb2124ef5723b27b173e"
                "d761463ce7613a979b3e05b48fbc5e2e"
            ),
        },
    },
    "mer00043cy": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, interest in"
            " machines/production equipment, and careful responsible work are "
            "expected. School minimum: good Hauptschulabschluss. Training pay: "
            "EUR 1.303 first year to 1.488 final year. Submit CV, school "
            "report(s). Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043cy",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218622": (
                "3018d15fcea8cbaa76a6dc954867a5af"
                "693403b27f58ed81190801bc5215fc1b"
            ),
            "218621": (
                "6e595608376570c15fc4664065eebf3a"
                "6331f81007ae5c6882d626290bcf5c15"
            ),
            "218619": (
                "6fba973ede27fe9065b48e762eeaa1be"
                "67ed764fbba100e1ac7bbf0411f88071"
            ),
        },
    },
    "mer00043ec": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability and careful responsible work are expected. School minimum:"
            " good Hauptschulabschluss. Training pay: EUR 1.326 first year to "
            "1.586 final year. Submit CV, school report(s). Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043ec",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "218908": (
                "d5bf359021659596f20309ffc4670d4c"
                "f78cfa49fc609f261524318d00320bf3"
            ),
            "218907": (
                "e8d01fc95cbf394688b2592ede5f718a"
                "6b657403a2cc1b7913a4dc29fa748f82"
            ),
            "218904": (
                "093485c4cf6478fb1d59b5f280451b69"
                "6d57a4474680c11ec3d45aa25fe13069"
            ),
        },
    },
    "mer00043ef": {
        "summary": (
            "Social-insurance administration apprenticeship in Bremen starts 1 "
            "September 2027. Train in insured-person advice, casework and "
            "social-insurance law. Applicants need at least a good Realschule "
            "qualification, solid maths, good German/English, health/legal "
            "interest and customer care. Stated training pay ranges from EUR "
            "1,332 in year one to up to EUR 1,434 in the final year, depending "
            "on location. Apply online with a letter, CV, two latest school "
            "reports and relevant certificates in DOC/PDF."
        ),
        "condition_evidence": [
            (
                "Minimum good Realschule school-leaving qualification; solid "
                "mathematics and good German and English; interest in health "
                "matters and social-insurance law, customer orientation, "
                "responsibility and helpfulness."
            ),
            (
                "Training remuneration is quoted as EUR 1,332 in the first "
                "training year up to EUR 1,434 in the final year, depending on "
                "location. The text does not explicitly give a payment "
                "interval. Good retention prospects after successful completion"
                " are not a guaranteed employment offer."
            ),
            (
                "Online-only application with covering/motivation letter, CV, "
                "latest two school reports and any relevant additional "
                "certificates/internship confirmations in DOC or PDF."
            ),
            (
                "Starts 1 September 2027 at Bremen, Germany; disability "
                "applicants are explicitly welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, annual profit share, flextime, hybrid work."
            ),
            (
                "Additional structured facilities/support listed: "
                "meal-discounts, events for employees, coaching, health "
                "benefits, company retirement, mobility offers, parking, "
                "inhouse doctor, good public transport, barrier-free workplace,"
                " canteen, café. These names do not establish an individual "
                "cash award."
            ),
            (
                "Structured role labels: Full time (part time possible), "
                "Temporary. Narrative requirements above take precedence where "
                "more specific; source publication end dates are not "
                "independently verified application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "219021": (
                "f18b0bb176b6376eefe100da69ec5fdb"
                "1bd2ffaae1596c2ab2431b4b8928c37c"
            ),
            "219017": (
                "ad890fa9e74b4c742ecd26bf28a39701"
                "6013d2106da838c967fa6bb484821d8b"
            ),
            "219016": (
                "0cebad117499c5c65d72c91d1f355fac"
                "7c8575f5d2703c5b07e948af24955044"
            ),
        },
    },
    "mer00043em": {
        "summary": (
            "Interest in commercial processes and technical equipment, "
            "organization and careful responsible work are expected. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043em",
        ],
        "categories": ["training"],
        "language": "en",
        "variant_hashes": {
            "218935": (
                "1b7fef9ddabe85a3fcd46c2b33d8ecea"
                "9b40ce8a7dceb929cced446ef48f8323"
            ),
            "218934": (
                "50a25820d060591f856e9e9fb52cb3a4"
                "3c715d9a6b8a4b60501b99ad666097f5"
            ),
            "218938": (
                "c60b0bf46bb0209a5fd3005d1190d7e7"
                "ff80253e1deecadffc6f51d1b759825c"
            ),
        },
    },
    "mer00043fl": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.332 first year to 1.434 "
            "final year (site-dependent). Submit cover letter, CV, 2 school "
            "reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043fl",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219026": (
                "265e132643c6a5d7ae36ec2cb2d9f24b"
                "703986629f418c2cb1ead9935b26522d"
            ),
            "219025": (
                "4fd6fc3cb1816cc8df466d0143550c83"
                "4b0b86a484c8a9b9f4d7dd35e29d2210"
            ),
            "219022": (
                "bea2582b4d644b99191b2e2a89bc6bdb"
                "fb23977d8a075ffcfa6ceb94e01e1efa"
            ),
        },
    },
    "mer00043fm": {
        "summary": (
            "Technical/practical aptitude, good maths and physics and "
            "English-language skills are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.332 first year to 1.434 "
            "final year (site-dependent). Submit cover letter, CV, 2 school "
            "reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043fm",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219733": (
                "3090ec74d256341bff773bcc3ba42acd"
                "978188bc2dfea0a1a200f18bc828501c"
            ),
            "219731": (
                "487988bb4316bf7ca0ae7937b2a48241"
                "e7a233d96e7415f42133ecc02528dd3c"
            ),
            "219729": (
                "d2e1454795ca17f81800dcaf4c68147f"
                "13daa8af02f75b9e094ebd4ff9541fa8"
            ),
        },
    },
    "mer00043kt": {
        "summary": (
            "Requires initiative, flexibility, communication/teamwork, logical "
            "analytical ability, mobility and technical interest. Submit CV, 2 "
            "school reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043kt",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219547": (
                "6a003fa0898e61b463cc87ec89def27a"
                "39fae7fc2011576ebd0aac6002b9faaa"
            ),
            "219546": (
                "dcd821928b0103587045497083ea70b0"
                "0ef993ab64e84d0b198f7d0cf757a146"
            ),
            "219551": (
                "09ddcb74fcc86566379d64509eb327da"
                "470366f4348d245039d2891e3d235a6e"
            ),
        },
    },
    "mer00043me": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.332 first year to 1.434 "
            "final year (site-dependent). Submit cover letter, CV, 2 school "
            "reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043me",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219858": (
                "de2fb5148ec21707c4f2e855790bb698"
                "51d09e310e16e5a7f78950541faf1c03"
            ),
            "219857": (
                "3384e4a6940ec025374573083483ef2b"
                "1685eab7afcd9809bf5cca84ab1841da"
            ),
            "219860": (
                "b2257ff09a696f335b09d7ed8a19000d"
                "a90142538fbf311c36b86c176ae5e83c"
            ),
        },
    },
    "mer00043my": {
        "summary": (
            "Technical/practical aptitude, good maths, chemistry and physics, "
            "spatial ability, reliable colour vision and careful responsible "
            "work are expected. School minimum: good Hauptschulabschluss. "
            "Training pay: EUR 1.332 first year to 1.434 final year "
            "(site-dependent). Submit cover letter, CV, 2 school reports. "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043my",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219874": (
                "bcf518ec75f4561e6d43abc9f3fd49cd"
                "a3e6328118b294f44787e416666143a4"
            ),
            "219873": (
                "92da5b49fb25c39e6c978cdd950e2920"
                "548a687a1f7247ab94899c7253ab227b"
            ),
            "219872": (
                "3f6bd75a8d8a88ef625ba4f8652e5702"
                "934a8b826bd0b9bfaa2f27cd381a42b1"
            ),
        },
    },
    "mer00043n3": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern manufacturing, spatial ability and careful responsible work"
            " are expected. School minimum: good Hauptschulabschluss. Training "
            "pay: EUR 1.332 first year to 1.434 final year (site-dependent). "
            "Submit cover letter, CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043n3",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219891": (
                "021918e037b023ad3b06f16d2a10cf31"
                "4977bbf86d7af833cb17b85c974ca066"
            ),
            "219889": (
                "3322e077421f8cc8b1ea746180eb8fc5"
                "b2ec706f7fb8e6e5bef2be901c904c9b"
            ),
            "219892": (
                "c5a5cbe75c93270b6cb64cd9ee731578"
                "fc633f2fe01598263c9c1ff6adaaa3d4"
            ),
        },
    },
    "mer00043n9": {
        "summary": (
            "Basic commercial and technical understanding, good spoken/written "
            "communication and English skills, customer orientation and "
            "teamwork are expected. Listed support: Meal-Discounts, Mobile "
            "Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043n9",
        ],
        "categories": ["training"],
        "language": "en",
        "variant_hashes": {
            "219905": (
                "1d07f54a8aeaf67bf4744e20995b7b36"
                "c171d6ace26e1537ccf986b94a803513"
            ),
            "219904": (
                "7800ce0a42c12b62ab333dd16b3e9aa5"
                "f7891ac524cfdb1244d51506cdc3945e"
            ),
            "219902": (
                "82595c13b0011a4164bf2416c324b3a6"
                "8310a7ef312a23380fd4b8474023ba21"
            ),
        },
    },
    "mer00043nn": {
        "summary": (
            "Exclusive to young people with a learning impairment under §64 "
            "BBiG. Secondary/special-needs school attendance or one-year "
            "vocational preparation, technical/manual aptitude and vehicle "
            "interest required. Pay EUR1,332 first training year to EUR1,434 "
            "final year, site-dependent. Apply online with cover letter, CV, "
            "latest two school reports and mandatory Federal Employment Agency "
            "referral under §66 BBiG; contact the agency to establish referral "
            "eligibility."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043nn",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219922": (
                "f240905589c6048ebd4cbb8c2f5bd46c"
                "bb13c3d2abd3b6e139031906451b8eea"
            ),
            "219920": (
                "6179276c5dd7bcc7f557232df7b295a0"
                "6616ecf0a1f9ab3a6e926fc4059572b6"
            ),
            "219917": (
                "b7d0442ea1d9e062434ebbc1ed17b27a"
                "88e1de07a60128e7b1960129fbdad721"
            ),
        },
    },
    "mer00043np": {
        "summary": (
            "Requires current engineering/business or related "
            "bachelor’s/master’s study or graduation within the past 12 months."
            " Offers paid practical experience, a study-compatible work "
            "schedule and German-language/cultural exposure. July 20, 2026 is a"
            " preferred start, not an application deadline. Listed support: "
            "Mobile Phone for Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043np",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "220218": (
                "e2077ece75514caf322747744cf7deb9"
                "be60f7aba46c3f0412ae9afd061f68f4"
            ),
            "220217": (
                "78f3d81d3b3618559e4e515324296153"
                "be12f39aa848e287307e19bf67f98fca"
            ),
            "220215": (
                "0cfe4ca761c19ff2aca385c41086fdfa"
                "b49e5f3a4aece1ef5efdf921bcceaa65"
            ),
        },
    },
    "mer00043ol": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss/Realschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043ol",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "219985": (
                "d03e7f272130ceeeac030be0f0ade034"
                "c465a2ec644df070f0818dd511c032f1"
            ),
            "219984": (
                "c08d9fdedbb8e7b5b9f6944888716d55"
                "82d68f80a71312cd7d6ee2b06bdf2f88"
            ),
            "219989": (
                "d51c51be6105659cb70c6c2e4ed5e61a"
                "cf404c8b56f5a140bcfbe30a88eda51e"
            ),
        },
    },
    "mer00043on": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. Submit cover letter, CV, 3 school "
            "reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043on",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220006": (
                "745882ba2922a6dd621a58d53b2ff9f1"
                "705c4ce2c0bf67856e43ddd5ad2300fc"
            ),
            "220002": (
                "21f6e7eda90c310f9b9fea6d9d6ab366"
                "d7173b719d03d15d6d33cf1433542461"
            ),
            "220001": (
                "6f7662e3cb903d5d35157aa688ca19b7"
                "715f3f2550024ae4df0a998cccb49f4c"
            ),
        },
    },
    "mer00043op": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicle repair and careful responsible work "
            "are expected. School minimum: good "
            "Hauptschulabschluss/Realschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043op",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220019": (
                "3b187b2567ba285115acb7f00411b81b"
                "847ff721c0d7ba5a60d86cc5bbaf206c"
            ),
            "220018": (
                "0864d60cf1fee379b29ccba82d343a92"
                "2971c8addc447c7d813ce7bce4577951"
            ),
            "220017": (
                "246764f6e74d0b52beda2b123704a14a"
                "39d433a6196077f114cfc5b0f2c5d384"
            ),
        },
    },
    "mer00043os": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. Submit cover letter, CV, 3 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043os",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220034": (
                "3f8c12f710964355a072083bc064cf98"
                "bf1e37469a676c00d834cc8b2267eb29"
            ),
            "220033": (
                "5eea1e00b900668b29064978e55eb89f"
                "9642a85fe90b6e2815b1784d7a9fe2d6"
            ),
            "220028": (
                "a260eae0472e4031d61f7fb4deaedb3d"
                "43fe73ae315ac863c241949725f79988"
            ),
        },
    },
    "mer00043ot": {
        "summary": (
            "Technical/practical aptitude, basic maths, chemistry and physics, "
            "reliable colour vision and careful responsible work are expected. "
            "School minimum: good Hauptschulabschluss/Realschulabschluss. "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043ot",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220030": (
                "b8de66feb67aef8ece1088ff05028560"
                "193d1b0664ec91801f38f9e1e7a05962"
            ),
            "220027": (
                "24af370e4e9d15f9a9bbd0c0dc22996f"
                "f71705a98414dfd868de79d513b38544"
            ),
            "220025": (
                "28741e719fe3cf271764f7bb06d50f51"
                "5e883b7981914b6f36a9a6c2ef3278f6"
            ),
        },
    },
    "mer00043oz": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss/Realschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043oz",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220102": (
                "c8600d844f63be387db335bf2f31344a"
                "9f3573d2a84179efd7bf4233bde8dfde"
            ),
            "220101": (
                "f5ff62cc7f3d843f33610fd3cf46f4f1"
                "23d947625496aa89473cf9e328c8eed0"
            ),
            "220100": (
                "311fcecaeb72972d10c8108a416869f9"
                "babc62097171a3e173f652ecf76704d8"
            ),
        },
    },
    "mer00043p3": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss/Realschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043p3",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220119": (
                "3964849c48b9730a34e4854bb1329e2b"
                "08e14e332a8cb5437da4d86e300586e2"
            ),
            "220116": (
                "020c48f2a361ea1fa871471124b5d86e"
                "1b77be1345aba07cdce7ccb0c2842187"
            ),
            "220115": (
                "1b71b4f4567d56ab64579cdd7efcbfc8"
                "f86e195e7e23177294fae568450723d9"
            ),
        },
    },
    "mer00043p4": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicle repair and careful responsible work "
            "are expected. School minimum: good "
            "Hauptschulabschluss/Realschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043p4",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220125": (
                "21d05356f4ef68e876c61c0a7a36e5aa"
                "d49a781f87a922bb0a856b9404c95648"
            ),
            "220124": (
                "85de631cb82f7544ca14c33bb51e2783"
                "4c6ae0f1553a4d8301b81bb84606534d"
            ),
            "220121": (
                "fc419aa5dd5756fb34131523225e8b5e"
                "06aa93e1889c3a1d8a7dd332c2de6e0f"
            ),
        },
    },
    "mer00043q5": {
        "summary": (
            "Requires good computer knowledge, solid maths, good German and "
            "English, basic commercial understanding and careful responsible "
            "work. School minimum: good Hauptschulabschluss. Training pay: EUR "
            "1.332 first year to 1.434 final year (site-dependent). Submit "
            "cover letter, CV, 2 school reports. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043q5",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220233": (
                "16d0c078124de9195e1e7fb5c19aa44f"
                "36651e610988c07e4e73f73a36b32b90"
            ),
            "220231": (
                "783e04e19bd625e74a66499335637ef5"
                "1b71ef281f474f10bf640fb2356fb8f9"
            ),
            "220229": (
                "e9223994f4a9b7503f12befde0b83872"
                "d334288539dd702fc472ba9a496e5252"
            ),
        },
    },
    "mer00043q6": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, interest in"
            " machines/production equipment, and careful responsible work are "
            "expected. School minimum: good Hauptschulabschluss or "
            "Realschulabschluss. Training pay: EUR 1.332 first year to 1.434 "
            "final year (site-dependent). Submit cover letter, CV, 2 school "
            "reports. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043q6",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220234": (
                "559705f046657f35d36d7026d67d039c"
                "1f4cb813475374f98df363f5b00cdd19"
            ),
            "220239": (
                "26d944390fedc4a4f5d868f52827dcc9"
                "728724eefba07887d935791c944b2259"
            ),
            "220237": (
                "d02d5ae7a2f032032e6120a753780d92"
                "34e43dce2ee55ebb6d06495035f8af5f"
            ),
        },
    },
    "mer00043q8": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicles/machines/equipment and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss or Realschulabschluss. Training pay: EUR 1.332"
            " first year to 1.434 final year (site-dependent). Submit cover "
            "letter, CV, 2 school reports. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043q8",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220244": (
                "3d5891343ac839279b5ce8c7ff92069c"
                "987a97eef620e77b5453d657dd3b1e6e"
            ),
            "220242": (
                "4bf87d731f0ddfe445255f6efc4bfb0b"
                "435d8db2e573e0872f15fcc29c8565f6"
            ),
            "220241": (
                "7dcd6be8e556361ca5cc41d38c5c49f7"
                "4c28358e5328ddc3b6416eb297ea2b37"
            ),
        },
    },
    "mer00043qq": {
        "summary": (
            "Basic commercial and technical understanding, good spoken/written "
            "communication and English skills, customer orientation and "
            "teamwork are expected. School minimum: good intermediate "
            "school-leaving certificate. Listed support: Meal-Discounts, Mobile"
            " Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043qq",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220463": (
                "d7a8383807ec690b0264148350f2de6a"
                "46955736a83b6a41c5c18a5f7f06c39f"
            ),
            "220461": (
                "cc6e91f4cab63152181cca4859f3ff96"
                "203ea551a2e0443cb3fd56f1fe9c3ca3"
            ),
            "220460": (
                "4c7198d8e678e39be5e8185d304e389c"
                "79067a88c128efdf1e28ac2e89776cbb"
            ),
        },
    },
    "mer00043qv": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Listed support: Meal-Discounts, Mobile Phone "
            "for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043qv",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220443": (
                "43b4859b2e1e1aac2b95ee8dec38b073"
                "6d0b2247b2028801fd606265b195168e"
            ),
            "220442": (
                "dea5c1e3861de737beacd686652f7599"
                "4b73ec92b2c0bc432b2b250075e533b2"
            ),
            "220438": (
                "f44d7c647d4256d5bcaa51e80e320c64"
                "8fc8202ee272f340160ced4d60944cae"
            ),
        },
    },
    "mer00043qx": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Hauptschulabschluss. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043qx",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220455": (
                "1b3a476eabc85a3284ccd45457a736f2"
                "313516c26fb34850d4bd1fa499b1564c"
            ),
            "220454": (
                "25c1579b2e99ced7cfd80a948f0f069f"
                "2cc245f249c70dd5fe2b3d09bb5be9ee"
            ),
            "220449": (
                "9e910acfed81c8a1afb83b0189a23815"
                "78faafaf1665eaa15f62682ae2d5dee0"
            ),
        },
    },
    "mer00043r1": {
        "summary": (
            "Vehicle mechatronics apprenticeship for van/commercial-vehicle "
            "systems and high-voltage technology starts 16 August 2027 across "
            "Düsseldorf, Duisburg and Neuss. Train in diagnosis, maintenance, "
            "connected systems and electrical safety. Applicants need at least "
            "a good Hauptschule qualification, technical/manual aptitude, maths"
            " and physics, reliable colour vision, care and precision. Interest"
            " in modern vehicle technology is required. Disability applicants "
            "are welcomed; pay is not specified."
        ),
        "condition_evidence": [
            (
                "At least a good Hauptschule qualification, technical "
                "understanding/manual aptitude and good maths/physics; interest"
                " in modern vehicle technologies."
            ),
            (
                "Reliable colour vision is explicitly required, alongside "
                "responsibility, care and precise working."
            ),
            (
                "Training takes place across the Rhein-Ruhr sites in "
                "Düsseldorf, Duisburg and Neuss, Germany, from 16 August 2027."
            ),
            (
                "The reviewed description does not state a remuneration amount "
                "or specific application-document list. Disability applications"
                " are explicitly welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, annual profit share, flextime, hybrid work."
            ),
            (
                "Additional structured facilities/support listed: "
                "meal-discounts, events for employees, coaching, health "
                "benefits, company retirement, mobility offers, inhouse doctor,"
                " good public transport. These names do not establish an "
                "individual cash award."
            ),
            (
                "Structured role labels: Full time (part time possible), "
                "Temporary. Narrative requirements above take precedence where "
                "more specific; source publication end dates are not "
                "independently verified application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "220458": (
                "56e396eec065add5027b4d468cc24f52"
                "5796af0bf6a4113cd5da259e1285e99c"
            ),
            "220457": (
                "c04a5c41406be5dd16021722d1e3619d"
                "23df5432d841c7c904da14805020d0e9"
            ),
            "220456": (
                "01ffcc6eff406fbe2ac0a651a4d1cbff"
                "5bbdbd939c9795b86413c388d9c8833b"
            ),
        },
    },
    "mer00043r4": {
        "summary": (
            "Technical/practical aptitude, basic maths, chemistry and physics, "
            "reliable colour vision and careful responsible work are expected. "
            "School minimum: good Hauptschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043r4",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220467": (
                "4b22693436fde595e1ebbe950e51590c"
                "2b36550aa1692c718a39bf3cbc335fc9"
            ),
            "220466": (
                "8d71512eac6dec6ab6cd9546a1d06d97"
                "9d9ff38fc4f017b317c88ddb3f70ddab"
            ),
            "220465": (
                "83ea67e5b9554b979f71c6fd266e8c67"
                "d3e29957419df1afb86fa062add37465"
            ),
        },
    },
    "mer00043r5": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicle repair and careful responsible work "
            "are expected. School minimum: good Hauptschulabschluss. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043r5",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220450": (
                "fc782ea715f9f0860280f06f1d297b67"
                "d2e6c84efeb9855bdc855adf40bd6ca4"
            ),
            "220447": (
                "0e7f871e48419923362cd00f673e1c40"
                "3c9426412fb691067a67579a12bd37ff"
            ),
            "220444": (
                "a1f394f2e58f510e91d04dfe25bea5c7"
                "afb79de670d9bc7fbb6725c5b537249e"
            ),
        },
    },
    "mer00043ra": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicle repair and careful responsible work "
            "are expected. Submit cover letter, CV, 3 school reports. Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043ra",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "220404": (
                "384e76262e1489c8314036ce1b969d33"
                "94e8d90e8ce5dbe22723a093602615fa"
            ),
            "220401": (
                "04f13c1d068a36a13451e8512de3a612"
                "a475a6a3f5f777d82b31e60cda7c91fa"
            ),
            "220399": (
                "c4dd2bae80f11dee24e30621f008fe61"
                "59837fe8cd6824638a638444ba3033f7"
            ),
        },
    },
    "mer00043u1": {
        "summary": (
            "Requires economics/business study, secure German/English and MS "
            "Office, analytical/numerical ability and independent teamwork. The"
            " placement must include a curriculum-required compulsory "
            "internship component. Submit cover letter, CV, enrolment proof, "
            "school report(s), standard-study-duration proof, "
            "compulsory-placement proof if applicable. Listed support: "
            "Coaching, Hybrid Work Possible. Study proof; voluntary internships"
            " within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00043u1",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238425": (
                "b9afd9cc016b1bc9ff1b2d06386c2d27"
                "f7dcb2bf56eb84d708851feae2aaf09d"
            ),
            "238424": (
                "33e41836fbb0966cfe2a934974099367"
                "1c1640efde20d3838fa719d78ccda404"
            ),
            "238422": (
                "9dc400d644213a8a2cfb73069e0aaace"
                "edec8c006c6fa75e334912c5df769697"
            ),
        },
    },
    "mer00044i0": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, reliable colour vision, interest in production systems "
            "and careful responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Hybrid Work Possible,"
            " Company Retirement."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044i0",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "224037": (
                "fc25d1ea082e03ce017d722a903b13c0"
                "4694a32ed77040818c7a5ced85f29994"
            ),
            "224035": (
                "683bbf607b9c49e1b2334f94561551c9"
                "668aee741b5c643f0ea9391c6eb7b5c7"
            ),
            "224033": (
                "f1198c36a2872deb53b4330de0f3a052"
                "480edb18d5cec8b4121f10e9a0d64b42"
            ),
        },
    },
    "mer00044i1": {
        "summary": (
            "Technical/practical aptitude, solid maths and physics, spatial "
            "ability, interest in machines/production equipment and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Hybrid Work Possible,"
            " Company Retirement."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044i1",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "224028": (
                "32e70455b52e762dadccec4af857c5d7"
                "38295cbdc4cccc5b837f155e74072005"
            ),
            "224027": (
                "1007ef90ba8ba4bb03c725ada4d03bfd"
                "36b65d6168b7e13cf8a81d89d1a950e9"
            ),
            "224029": (
                "ce82e07db4570877243e07d2d5515781"
                "6d0268fc2c90ed9615007ff208e539dd"
            ),
        },
    },
    "mer00044i5": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Hybrid Work Possible,"
            " Company Retirement."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044i5",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "224041": (
                "06227a205ce30b91e2193c30f588c43e"
                "c0dbec4a815b4aa237c7ac9cb60f6504"
            ),
            "224039": (
                "1cbc4e4ff30e5f266aa4b86d210d6d4c"
                "b4db1a41a6e414477e5014b09c882462"
            ),
            "224038": (
                "b493410de60b9a471b2492d40100d6e5"
                "90e873a2189c83a74a41908afe200dcf"
            ),
        },
    },
    "mer00044i7": {
        "summary": (
            "Technical and practical aptitude, good maths and physics, interest"
            " in automotive electronics/mechanics, and careful, reliable work "
            "are expected. School minimum: good Hauptschulabschluss. Training "
            "pay: EUR 1.242 first year to 1.586 final year (site-dependent). "
            "Listed support: Hybrid Work Possible, Company Retirement."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044i7",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "224024": (
                "dcaad7280709978ba419e13950a75624"
                "a68df1f960e0bf936617f4b1bbc10c3e"
            ),
            "224020": (
                "fadc966951b58dbc7629cd54b85d28b4"
                "53546fbc91bb588634d17a4c4d4f7126"
            ),
            "224019": (
                "3317fbe273dd97fc619da4ffbf9d1d2c"
                "e344b9e5f2781c2135142711c56fbd10"
            ),
        },
    },
    "mer00044i9": {
        "summary": (
            "Requires engineering, computing, industrial-engineering or related"
            " study, fluent English, analytical/communication skills and "
            "interest in vehicle software/electronics. Initial Jira/Confluence "
            "exposure is advantageous, not mandatory. Submit cover letter, CV, "
            "enrolment proof, school report(s), standard-study-duration proof, "
            "compulsory-placement proof if applicable. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044i9",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "224483": (
                "1cbd96f815d3d329fb3f53e069dcedb0"
                "fc75de79a95d52a71e67217d90bc57e6"
            ),
            "224482": (
                "44f09a9352585198831da79f641245d1"
                "1644c1a6071c5a6ecbd7bdd238fb77ba"
            ),
            "224480": (
                "287eb53ae39a30be70c8a56348b303ab"
                "3b5f0c908aab13bb0714242e4af71a94"
            ),
        },
    },
    "mer00044sg": {
        "summary": (
            "Three-year technical-procurement/business B.Sc. dual study starts "
            "1 October 2027. Theory is at DHBW Heilbronn; practical work is in "
            "Stuttgart-Vaihingen, generally 40 hours/week, with optional "
            "overseas semesters. Applicants need general, applied or "
            "subject-specific higher-education entrance qualifications with "
            "good/very good grades, technical/business interest and analytical "
            "teamwork. Apply online with a CV and two latest school reports: at"
            " least two files, names up to 20 characters and 6 MB total."
        ),
        "condition_evidence": [
            (
                "Alternative entry qualifications: general higher-education "
                "entrance certificate, Fachhochschulreife OR subject-specific "
                "higher-education entrance certificate, with good to very good "
                "performance. Further linked admission minima apply."
            ),
            (
                "Business and technical interest, desire to understand/improve "
                "products, processes and supply chains, independent "
                "logical/analytical work, communication, teamwork, engagement, "
                "resilience and creativity."
            ),
            (
                "Programme begins 1 October 2027 including induction, lasts "
                "three years, and generally uses a 40-hour working week. Theory"
                " at DHBW Heilbronn; practice at Mercedes-Benz Customer "
                "Solutions in Stuttgart-Vaihingen."
            ),
            (
                "An overseas partner-university semester and an overseas "
                "practical semester are possible options, not guaranteed "
                "assignments or country eligibility lists."
            ),
            (
                "Portal/online-form application only: CV and latest two school "
                "reports; at least two uploaded documents, filenames no longer "
                "than 20 characters, maximum 6 MB total."
            ),
            (
                "No remuneration amount is stated in the reviewed descriptor. "
                "Severely disabled and equivalent-status applicants are "
                "welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: hybrid work, discounts for employees, annual "
                "profit share, mobile phone for employees, flextime."
            ),
            (
                "Additional structured facilities/support listed: coaching, "
                "events for employees, health benefits, meal-discounts, company"
                " retirement, good public transport, barrier-free workplace, "
                "parking, inhouse doctor. These names do not establish an "
                "individual cash award."
            ),
            (
                "Structured role labels: Full time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "225744": (
                "db18ea825e728aefcce5693ca92261bf"
                "8d535faa5ce465034f7b9b77cc15f161"
            ),
            "225743": (
                "6c62e08077374d575d45ffc39f60ac61"
                "68647203d066168ba2057e69a3c77cf0"
            ),
            "225747": (
                "80d5ff579489af5926f3e26bf6f1ccbf"
                "a21ec2ae5784873bb541f3549e2b3126"
            ),
        },
    },
    "mer00044v0": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. School minimum: good "
            "Hauptschulabschluss. Training pay: EUR 1.242 first year to 1.586 "
            "final year (site-dependent). Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044v0",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "225525": (
                "d0bc5ea5cd3f131d4d811b8c3cc95545"
                "1d08f1b14e7ef095357bbef5c390eda1"
            ),
            "225524": (
                "c2277863888cc630ba6a9c107bad2adb"
                "84bb22072273dd83259a77b957dbe2d0"
            ),
            "225523": (
                "47acb28ac01aee9618314b94e95b61b2"
                "0a49d3bc632b5aaae44ddb790813288f"
            ),
        },
    },
    "mer00044v1": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "vehicle electronics/mechanics/metalworking and careful reliable "
            "work are expected. Listed support: Meal-Discounts, Mobile Phone "
            "for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044v1",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "225528": (
                "13a1aaddeeb53c6d08bc8efd5c63e3a4"
                "5649da158cc1196240c0411d83c8cba0"
            ),
            "225527": (
                "38cf2fe9d78e8991a242afbe49852d6b"
                "3b47284c0bddce7cbb882d97b219202a"
            ),
            "225526": (
                "1d0ad8acad3473a049b95967f9992c3d"
                "ea822c9633716c29a2f0e125a32c72d7"
            ),
        },
    },
    "mer00044v2": {
        "summary": (
            "Technical/practical aptitude, basic maths, chemistry and physics, "
            "reliable colour vision and careful responsible work are expected. "
            "School minimum: good Hauptschulabschluss. Listed support: "
            "Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044v2",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "225531": (
                "e0123911e0be19de27284f6445f045c9"
                "da968f2e3b17f1c2d693b4dd68fe9e9c"
            ),
            "225530": (
                "4335c93e152fffddfd518ab6d038da6c"
                "fe4158b592f112b3c3ca159b5d1b9b50"
            ),
            "225529": (
                "cb0e0b7b5c12fb9790b95b3a14e26bc5"
                "88e968f965834af5bff7b1b877a4f400"
            ),
        },
    },
    "mer00044v5": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. School minimum: good Hauptschulabschluss. Training pay: "
            "EUR 1.242 first year to 1.586 final year (site-dependent). Listed "
            "support: Meal-Discounts, Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00044v5",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "225534": (
                "af8f7391546293cec52bf46c07952ff1"
                "00aff242c6f900b1ed48a652389c59d7"
            ),
            "225533": (
                "9cc055486246cb45348a2a5ba6c8320c"
                "ef75514add4f784e53f9ab8e038cafa0"
            ),
            "225532": (
                "e569e53da325071f9d14a2301b3f48dc"
                "e6a6f5b56cd68b8e95eb52b3bd2daca4"
            ),
        },
    },
    "mer000450f": {
        "summary": (
            "Requires computing, software engineering, data science, "
            "electrical/automotive/mechanical engineering or related study, "
            "very good German and good English, and AI/LLM/data-analysis "
            "interest. Python or similar programming knowledge is advantageous,"
            " explicitly not mandatory. Submit cover letter, CV, enrolment "
            "proof, school report(s), standard-study-duration proof, "
            "compulsory-placement proof if applicable. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit. Coaching listed."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000450f",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240412": (
                "83f26fbe550ed1592f76c90d9b4a1bdc"
                "4e11b76c98c9921dd3b036b52037ca95"
            ),
            "240410": (
                "3dd8e7d10716e8f497d9555827afc163"
                "b820e7437a514579187e6a2d923eaeeb"
            ),
            "240408": (
                "8f180b4321f026bce460c2f432e32377"
                "d0050939b408ba8f59ee06a1c61e5ce3"
            ),
        },
    },
    "mer000456v": {
        "summary": (
            "Technical/practical aptitude, basic maths, chemistry and physics, "
            "reliable colour vision and careful responsible work are expected. "
            "Listed support: Meal-Discounts, Mobile Phone for Employees "
            "Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000456v",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232126": (
                "4a2947a3bc704007383e3a40ba1fe5ae"
                "dda127e3a6e38ddcbb98fe206ad9542d"
            ),
            "232124": (
                "27f93b3b8ea3ae6ae4291bc6a14ff336"
                "b1b3f281c7369cfc73bdc39870bddf7f"
            ),
            "232123": (
                "b1a9b5e4f711dc57a765c7bef4e042ae"
                "e38007522ddcfc5e8b7912b266665c99"
            ),
        },
    },
    "mer0004575": {
        "summary": (
            "Bachelor/master students from semester five, with excellent "
            "business/technical/science grades and 1–2 prior international "
            "strategy-consulting, automotive or corporate-strategy internships."
            " Fluent German/English, strong Office and a compulsory curriculum "
            "component. Submit CV, cover letter, enrolment, school, "
            "study-duration and compulsory-placement proofs as applicable. "
            "Voluntary internships: regular study+3 semesters. Germany: EEA "
            "ID/passport; others residence/possible work permit. Coaching "
            "listed."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004575",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "227785": (
                "e6d0beccfe245c3de50c8b980b63908f"
                "0b8ca7ed39f54b3903e00b0bac576cf4"
            ),
            "227784": (
                "c11a476a4e1126fff39b42a90abc90ec"
                "9624fa2fe7f9e253369181378c1d2ee3"
            ),
            "227783": (
                "6770c674feb35d8d573c7079e01064b8"
                "c3c368e8ce2267d19108f3262a32f716"
            ),
        },
    },
    "mer0004588": {
        "summary": (
            "Requires undergraduate communications, PR, marketing or related "
            "bachelor’s study, strong spoken/written communication, creative "
            "content ability and MS Office. Detail-conscious teamwork and "
            "initiative are expected. Listed support: Mobile Phone for "
            "Employees Possible, Health Benefits."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004588",
        ],
        "categories": ["training"],
        "language": "en",
        "variant_hashes": {
            "227410": (
                "c8be1b620a7c5d425a472404c52ff253"
                "6b1251a6aea1e330259cb1495e6592ae"
            ),
            "227407": (
                "e941cddd7130e270d3f4dffd08a5b594"
                "c3c8e2885fece312e79be52c19cbcd58"
            ),
            "227405": (
                "6571a4146c96b7dcf961c99f39cd17d3"
                "854af1aeedb09c450d685a70f2dca22f"
            ),
        },
    },
    "mer00045cc": {
        "summary": (
            "Technical/practical aptitude, good maths and physics, interest in "
            "modern vehicle technology, reliable colour vision and careful "
            "responsible work are expected. Listed support: Meal-Discounts, "
            "Mobile Phone for Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045cc",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232121": (
                "c0b6047229186d3e2df5e2edcbda425e"
                "96debc1d80d210fcbf8fc41c6a40e933"
            ),
            "232118": (
                "51f185324d2e1ec54fd7b737090b5e3b"
                "e23e93c60f3138fe7c3f67be52541266"
            ),
            "232116": (
                "9f4cd8cfd0c33046ff558e70a3ae8fd7"
                "fcdf7701e71b593376fe2b3a012fcb2a"
            ),
        },
    },
    "mer00045cf": {
        "summary": (
            "Automotive commercial apprenticeship in Mannheim starts 1 "
            "September 2027. Learn vehicle sales/service administration, "
            "spare-parts support, customer advice and invoicing. Applicants "
            "need basic commercial and technical understanding, good "
            "written/spoken expression, English skills, customer orientation "
            "and teamwork. The notice welcomes disability applicants. It does "
            "not state a minimum school-leaving qualification, a pay amount or "
            "a specific closing date."
        ),
        "condition_evidence": [
            (
                "Basic commercial and technical understanding; good "
                "spoken/written expression and English language skills; "
                "customer orientation, friendliness, engagement and teamwork."
            ),
            (
                "Apprenticeship starts 1 September 2027 at the "
                "Mannheim-Heidelberg-Landau branch, with the structured "
                "location Mannheim, Germany."
            ),
            (
                "The reviewed descriptor does not specify a school-leaving "
                "threshold, remuneration amount or required "
                "application-document list. Disability applicants are "
                "explicitly welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, annual profit share, flextime, hybrid work."
            ),
            (
                "Additional structured facilities/support listed: "
                "meal-discounts, events for employees, coaching, health "
                "benefits, company retirement, mobility offers, inhouse doctor,"
                " good public transport, parking, barrier-free workplace. These"
                " names do not establish an individual cash award."
            ),
            (
                "Structured role labels: Full time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["training", "jobs"],
        "variant_hashes": {
            "232145": (
                "eb219f5f4338b8f8db9ed14e87be85ff"
                "c66a9d77df3a35013ebbf6cdf491ed29"
            ),
            "232144": (
                "4f7f2bdad56982c31402857bdd7e5013"
                "64e3c82119d7f60486c96e63bb219de1"
            ),
            "232140": (
                "943d9e8f95896a9204a0614789b77dc0"
                "301d9dc5d3dcda70d05ba21bd6a40207"
            ),
        },
    },
    "mer00045cj": {
        "summary": (
            "Basic commercial understanding, organization, good German and "
            "basic maths, with careful and responsible working habits, are "
            "expected. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045cj",
        ],
        "categories": ["training"],
        "language": "en",
        "variant_hashes": {
            "232139": (
                "bc435f2582d0876a7da029096e3c4a2b"
                "ee7556b2d2d206f41c3a58cf8937b269"
            ),
            "232136": (
                "8db8e6d83a9c407c83333f38d0f86405"
                "bd14142d3fd2025e60cdd43a600212ab"
            ),
            "232135": (
                "5ab761e6e28f026fd631f6d59cb22428"
                "e578738f48da08c84c5486c212b0bd51"
            ),
        },
    },
    "mer00045fn": {
        "summary": (
            "Requires automotive/mechanical/electrical engineering, computing, "
            "business informatics, mechatronics, industrial engineering or "
            "other technical study; secure German, vehicle knowledge and "
            "class-B licence. Completed foundational studies and "
            "MATLAB/C++/Python basics are preferred. Submit cover letter, CV, "
            "enrolment proof, school report(s), standard-study-duration proof, "
            "compulsory-placement proof if applicable. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit. Coaching listed."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045fn",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238432": (
                "bc14550947be6f5624b7cbb4edc1747a"
                "22649d0194b3b21d565c151a304bab11"
            ),
            "238430": (
                "0305efc126f3f785c3078f94bf086668"
                "a4642eb9cfbd7be50882ecd67804e48b"
            ),
            "238427": (
                "2db5e808d660a2d1ead3c0803553018f"
                "988b5c35608e4836680e075726a23547"
            ),
        },
    },
    "mer00045gk": {
        "summary": (
            "Technical/practical aptitude, basic maths and physics, spatial "
            "ability, interest in vehicle repair and careful responsible work "
            "are expected. Listed support: Meal-Discounts, Mobile Phone for "
            "Employees Possible."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045gk",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232132": (
                "40153937159c9b81ca86c24290f911d3"
                "578a6fcbcea91c2595aa94fe043ba655"
            ),
            "232131": (
                "5ada94022a10dbcacea52937f72b2209"
                "47f361231935e11d4a827acdcee695ac"
            ),
            "232129": (
                "907b071baac41239714bd9ac52bc47ca"
                "508e1dabbc7b2f87806403933170dec1"
            ),
        },
    },
    "mer00045ia": {
        "summary": (
            "Automotive business apprenticeship. Applicants should have at "
            "least a good Realschulabschluss, basic business and technical "
            "understanding, clear spoken/written communication and English, "
            "customer focus and teamwork. Listed benefits: coaching, health "
            "benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045ia",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "228265": (
                "ff46eb593e540231dc3f9a075f04ebb1"
                "9188f72a8e7a8096cba2d2df06f5bd23"
            ),
            "228262": (
                "ecc29c5cd0c815c8453ecf785b27aa82"
                "691b5144e583b5ab9ee27b7563f8754f"
            ),
            "228261": (
                "304fb4d6c1ade616702944f4fe8816c9"
                "722aad8184c76928792f95d3f34ab83c"
            ),
        },
    },
    "mer00045ml": {
        "summary": (
            "Bachelor thesis on battery-cell formats. Study "
            "mechanical/automotive engineering, mechatronics, electrical "
            "engineering or a related field; German and English and MS Office "
            "are required. Battery production, CAD/Siemens NX and completed "
            "technical vocational training are advantages. Submit enrolment and"
            " study-duration evidence. Listed benefits: coaching, health "
            "benefits, meal discounts. Above-average grades and prior relevant "
            "internships; EEA ID/passport, otherwise residence/possible work "
            "permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045ml",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238698": (
                "b21f766f6394ffaa4b6aa25f5b10330e"
                "8d40046b3e1034d3da38af194e819b79"
            ),
            "238696": (
                "ea431a0319e0627d39795230d759c122"
                "7a86afdac18e89ef391bbc86d4f9dce5"
            ),
            "238693": (
                "22e1d663d0993c324b733e79ec916781"
                "fb8b5f8fa38c5fdfdfcfbd02a77e1bda"
            ),
        },
    },
    "mer00045sg": {
        "summary": (
            "Industrial-mechanics apprenticeship requiring at least an MSA "
            "school certificate, technical/manual aptitude, maths and physics, "
            "spatial reasoning and interest in production machinery. Pay is "
            "listed at EUR 1,272 in year one to EUR 1,461 in the final year, "
            "depending on location; later employment is a prospect, not a "
            "guarantee. Listed benefits: coaching, health benefits, meal "
            "discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00045sg",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236298": (
                "29f5abf9a7b45b202f23dbd2144ccdac"
                "dcce14d8a307def888972500f80f0d69"
            ),
            "236297": (
                "536d654100021e650382e82735e4a37c"
                "c6c8f6ea897fb047fa074ca27848eb67"
            ),
            "236293": (
                "95fb29a30e56c31cc0eded25871bd3a3"
                "5cbb17014ac2de30ac642e2ae6dc4933"
            ),
        },
    },
    "mer0004626": {
        "summary": (
            "Vehicle mechatronics/high-voltage apprenticeship. Requires "
            "technical/manual aptitude, maths and physics, interest in vehicle "
            "technology, reliable colour vision and careful work. Apply online "
            "with motivation letter, CV and the last two school reports; no "
            "specific school qualification is stated here. Listed benefits: "
            "coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004626",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232526": (
                "cee07ba58226b8116f6d2ddfd6c9d24a"
                "fa41638e82f13f95c057d464660d7a13"
            ),
            "232530": (
                "2524be193884995c52f27b0d9a988ec7"
                "b2da39c641676e472a06ca1e59e1467e"
            ),
            "232528": (
                "b7e3cbd080d91004227a9f98051cd0a3"
                "0d8217d94803761208bb350a69b5f6d8"
            ),
        },
    },
    "mer0004627": {
        "summary": (
            "Vehicle-painting apprenticeship. Requires technical/manual "
            "aptitude, maths, chemistry and physics, reliable colour vision and"
            " careful work. Submit an online application with motivation "
            "letter, CV and the last two school reports; no specific school "
            "qualification is stated here. Listed benefits: coaching, health "
            "benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004627",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232541": (
                "aee3933facf38da4d32b440eb19c9b6d"
                "d028b5391c003034504d553d6cbce2cd"
            ),
            "232540": (
                "e85318f1999d8f98a612b09310dea71a"
                "f57fac8e33102cfb80f7484009bc48f6"
            ),
            "232539": (
                "57e55d288425d71ed11cc264514f6122"
                "e385f5ab1c14ca8079a6e170fa92ad7b"
            ),
        },
    },
    "mer0004628": {
        "summary": (
            "Automotive business apprenticeship requiring a Realschulabschluss,"
            " business/technical understanding, clear communication and "
            "English, customer focus and teamwork. Submit motivation letter, CV"
            " and the last two school reports online. Listed benefits: "
            "coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004628",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232548": (
                "b19176a48533e7544a6e622e37d5c841"
                "9ae038f1f5071eb0cb9f43653497e103"
            ),
            "232545": (
                "9148130f2785dbc6a9921456cea320df"
                "d7e05dbbe81c4240443856067cf9a2e5"
            ),
            "232544": (
                "5f802e6f4f88206d16e3fde780f5aaaf"
                "e01b12535fbd473399e585e286a2e987"
            ),
        },
    },
    "mer000462a": {
        "summary": (
            "Warehouse-logistics apprenticeship requiring a Hauptschule school "
            "certificate, business understanding, organisation, good German and"
            " basic maths, responsibility and careful work. Submit motivation "
            "letter, CV and the last two school reports online. Listed "
            "benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000462a",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232558": (
                "7d2b64bb5a05a63a69fd22f2c46982c2"
                "db1d7aab3cdb966b7a942c978bf7af92"
            ),
            "232557": (
                "261b6b8603688557f9926be450ce47a1"
                "07bf923f00f93674f3d3370aaeb508ea"
            ),
            "232556": (
                "73a16c02d66ec01d146dd23f3a636b63"
                "b7dc05220d76965c652ff12e05bb0f5d"
            ),
        },
    },
    "mer000462b": {
        "summary": (
            "Body-repair apprenticeship requiring technical/manual aptitude, "
            "maths and physics, spatial reasoning, interest in vehicle repair "
            "and careful work. Apply online with motivation letter, CV and the "
            "last two school reports; no specific school qualification is "
            "stated here. Listed benefits: coaching, health benefits, meal "
            "discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000462b",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232536": (
                "d7cc2bd84355509a0cb8c85d5993b973"
                "7e34d553cd88feec220f25dc59a54c7d"
            ),
            "232532": (
                "98f78b97ef2d4d7a26a22085a23dc2e2"
                "03ce3bbdc4ae2bfa0dfd853436b5a7f9"
            ),
            "232531": (
                "958dfc3b0eaab21a2f8c11554e7b1556"
                "2b38cb048b0b3e8d2335c5ccbbc70b91"
            ),
        },
    },
    "mer000462c": {
        "summary": (
            "Vehicle mechatronics/high-voltage apprenticeship requiring "
            "technical/manual aptitude, maths and physics, interest in vehicle "
            "technology, reliable colour vision and careful work. Submit "
            "motivation letter, CV and the last two school reports online; no "
            "school qualification is specified. Listed benefits: coaching, "
            "health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000462c",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232554": (
                "61757addaa3ad04c4a7d44ea89387993"
                "3bfed9fdc177eb7cacf4cec78b0cdbdf"
            ),
            "232552": (
                "0b7716d5db7de1917acb6604812c3f7e"
                "8e1403aac15734fcafae0871ea324e9f"
            ),
            "232550": (
                "ab568808ac462cafbba85ae230c1a875"
                "e69140472438f09af71b2af16e02bda0"
            ),
        },
    },
    "mer000462l": {
        "summary": (
            "Warehouse-logistics apprenticeship requiring a Hauptschule school "
            "certificate, business understanding, organisation, good German and"
            " basic maths and careful work. Submit motivation letter, CV and "
            "the last two school reports online. Listed benefits: coaching, "
            "health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000462l",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232566": (
                "375b5fc1391656e8a91572ce7beab01a"
                "d4fd287c5592427082bdcf7b0f685d0c"
            ),
            "232564": (
                "0f0960b056d388ac412c9db456122810"
                "e35789ecb722699fd6d46d9a4a0504be"
            ),
            "232561": (
                "05bf81646f1ff9df36ee2487e69af585"
                "4d4ce0e9afd633f2e636c9ce16f5133f"
            ),
        },
    },
    "mer000462p": {
        "summary": (
            "Automotive business apprenticeship requiring a Realschulabschluss,"
            " business/technical understanding, clear communication and "
            "English, customer focus and teamwork. Submit motivation letter, CV"
            " and the last two school reports online. Listed benefits: "
            "coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000462p",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "232524": (
                "af237ace22e6bf30eb480cba43cc524e"
                "ae442c0bae9db96a74de832206f7e9fd"
            ),
            "232522": (
                "ca3ae0d328931a542a6ffd29b20ce768"
                "37b13816646d9da8390592df15d719f2"
            ),
            "232520": (
                "db9fd8527d218817f5bb735b906b1853"
                "c1a27087799bd59ec8ede29538684ce0"
            ),
        },
    },
    "mer000465b": {
        "summary": (
            "Purchasing-process internship for business, "
            "media/communications-management or related students. Requires "
            "German and English, PowerPoint/Excel, communication and analytical"
            " skills. Presentation/visual-content experience is an advantage; "
            "process modelling is preferred. Submit enrolment and "
            "study-duration evidence, plus compulsory-placement proof where "
            "applicable. Listed benefits: coaching, health benefits, meal "
            "discounts. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000465b",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "230662": (
                "74457dafc181cf32d942188d6b5713f6"
                "12c6cf46b479ec765927453892ad6658"
            ),
            "230660": (
                "1e6cf5c0a54d3d8d149ca6698de5b592"
                "0b4268aff365da8565e89168482b2d94"
            ),
            "230659": (
                "cdeff9a17a1984336bda780b4afc8bfb"
                "2b7da054bb5b64fe2c39cde96271562b"
            ),
        },
    },
    "mer0004673": {
        "summary": (
            "Labour/social-law legal traineeship requires a passed first state "
            "law examination, German and English, MS Office and independent "
            "legal work. Labour/social-law knowledge is advantageous. The "
            "placement must include a mandatory component. Submit first-exam "
            "results and, where applicable, an assignment request. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004673",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "230462": (
                "ea4abadbccf0c67137215b8ede3b440a"
                "226f37923fc9476f823f795f8793229a"
            ),
            "230460": (
                "907302d342ef6d5754e4436eed5dc141"
                "b2eefe35a27c8fff3f1901fe1b8fd7d9"
            ),
            "230459": (
                "06579befe830ca028e4cec0af4307fa0"
                "31df01f9ba27f658ccc8296960b03fbe"
            ),
        },
    },
    "mer00046f9": {
        "summary": (
            "Passenger-car mechatronics apprenticeship requiring at least a "
            "good Mittelschulabschluss, technical/manual aptitude, "
            "maths/physics and interest in vehicle mechanics/electronics. Pay "
            "is listed at EUR 1,242 in year one to EUR 1,586 in the last year, "
            "depending on location. Successful training offers employment "
            "prospects, not guaranteed hiring. Listed benefits: coaching, "
            "health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046f9",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230666": (
                "9d4d43e8fa76924b2962dbfaa6de9e9a"
                "7fe586d6c0f6d6f2c45ab85ec7da576f"
            ),
            "230665": (
                "e55318f01daaeede128a4c376af57a08"
                "378d7934df6f407fb838f3c96860ae79"
            ),
            "230669": (
                "d339f599afd44e2f4ea55c6c5fbd574d"
                "57d903f06d5310179089db43e933f3ce"
            ),
        },
    },
    "mer00046fl": {
        "summary": (
            "Warehouse-logistics apprenticeship requiring at least a good "
            "Mittelschulabschluss, basic business knowledge, organisation, good"
            " German and maths and careful work. Listed pay ranges from EUR "
            "1,242 in year one to EUR 1,586 in the final year, depending on "
            "location; subsequent employment is a prospect. Listed benefits: "
            "coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046fl",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230678": (
                "1c67748ffea4b5c84c3426ea12962a80"
                "15b6fc2c069a8ab8c063b52c9c2bb86d"
            ),
            "230676": (
                "40a75e1d607bc34fff93228b6ab947eb"
                "15072a5cd6e6c1355caf9c1cb49f6510"
            ),
            "230673": (
                "0e475203498a5aaffb5ce2d5a2066936"
                "c274a1c5b0b2213d2d707d46267734e9"
            ),
        },
    },
    "mer00046fu": {
        "summary": (
            "Automotive business apprenticeship requiring at least a good "
            "Realschulabschluss, basic business/technical knowledge, clear "
            "spoken/written expression and English, customer focus and "
            "teamwork. Listed benefits: coaching, health benefits, meal "
            "discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046fu",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230692": (
                "39a3596f071f03827d9419b8e5689e20"
                "cf067679929a7bcaa684fc9a2c9453aa"
            ),
            "230689": (
                "fb169f410e89adfd40054af15a204ca8"
                "24f16a12c2a909953ebe6fb12df4e17f"
            ),
            "230693": (
                "eaf6864df636d8497b9bc574dd1d8a2f"
                "9feee9939bd72565b8197536e7718352"
            ),
        },
    },
    "mer00046g3": {
        "summary": (
            "High-voltage vehicle-mechatronics apprenticeship requiring at "
            "least a good Mittelschulabschluss, technical/manual aptitude, "
            "maths and physics, interest in vehicle technology and reliable "
            "colour vision. Successful completion offers employment prospects, "
            "not a guaranteed job. Listed benefits: coaching, health benefits, "
            "meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046g3",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230698": (
                "b9bf3bfef9a8c39756470a47d7a71d83"
                "d12bd141436ddfd9d91eb42bfee02197"
            ),
            "230697": (
                "3df4f41926e2c36faa99054889788201"
                "a25815f4fa336bfe3ab24181b05a9459"
            ),
            "230694": (
                "31fa458f5340d99cafbe34eaf9b9159d"
                "4c079dc55acb39494e6c2918af4f1812"
            ),
        },
    },
    "mer00046ge": {
        "summary": (
            "Vehicle-painting apprenticeship requiring at least a good "
            "Realschulabschluss, technical/manual aptitude, maths, chemistry "
            "and physics, reliable colour vision and careful work. Listed "
            "benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046ge",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230789": (
                "b6fce46b2e7dba0839f34cfd7326befb"
                "48d5f94c6965b02e6354ea0e4a7d4ab6"
            ),
            "230788": (
                "e7064c9348286cf8cc013fd21de37333"
                "c5859843b72f13254c838dba234ebe3a"
            ),
            "230787": (
                "2d8962e05f0a5a850cf660371023c64f"
                "8f6b4e60a0e46adfa9cd4a1a7aad1ec3"
            ),
        },
    },
    "mer00046gf": {
        "summary": (
            "Warehouse-logistics apprenticeship requiring at least a good "
            "Realschulabschluss, business understanding, organisation, good "
            "German and basic maths and careful work. Successful completion "
            "offers employment prospects rather than guaranteed hiring. Listed "
            "benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046gf",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230813": (
                "5ceed3eaec69dbe6da1f5cb5347fbfb1"
                "a31ee85718f7cd6cba1af1e9c46b5a11"
            ),
            "230811": (
                "795b1d988b7843f504e7c463a5775508"
                "845c73a8154612db3fa0104e2b7c239a"
            ),
            "230810": (
                "952ded008eb691a8f3c46d9347ac3292"
                "cc7b89b2c57d4741fc949e32be1f39e2"
            ),
        },
    },
    "mer00046gg": {
        "summary": (
            "Automotive business apprenticeship requiring at least a good "
            "Realschulabschluss, business/technical understanding, clear "
            "communication and English, customer focus and teamwork. Listed "
            "benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046gg",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "230798": (
                "41112f91b22729ee4c2ebbd420dfc141"
                "2d95590fee055fb9400db9233c85da29"
            ),
            "230797": (
                "248c88705a01dcaa38e278279f8c2952"
                "0458c06f2c22f89e5e18905763a43c21"
            ),
            "230796": (
                "14c677f4f939d008d34c7e914b747050"
                "a8f57526c9628360d6366b716a0f8db7"
            ),
        },
    },
    "mer00046ih": {
        "summary": (
            "Compulsory supplier-quality internship for "
            "mechanical/electrical/industrial engineering, technical-business "
            "or related students, preferably focused on digitalisation. "
            "Requires German/English, strong Excel/PowerPoint, "
            "technical/analytical and communication skills. "
            "Quality/process-management and moderation knowledge are "
            "advantages. A mandatory placement component is required. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046ih",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238342": (
                "b3ec0f06f329ce6c1860acf1d2e37a16"
                "6e4db478a47ce156cb9e90a959e1a974"
            ),
            "238339": (
                "6b56308e1451e68af5dd5db9a4cc7249"
                "3e16853fcc4c55cb0427af35b4bfc8f9"
            ),
            "238337": (
                "bfc7504b77fcd03257eed9e0e48fe378"
                "3c9c155a88d025d0b60e7611d14d3a1f"
            ),
        },
    },
    "mer00046iu": {
        "summary": (
            "HR/labour-policy internship for business, psychology, HR, "
            "humanities, law or related students. Requires very good "
            "German/English and MS Office, communication and precise "
            "independent work. Preferred six-month placement from January2027 "
            "in Bremen at35hours/week; paid work, feedback, job shadowing, "
            "intern network and flexible/home-working opportunities are "
            "described. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046iu",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "231364": (
                "263f3a649af2ecf0e037805b1af6b8b6"
                "498788d814204ee3c01f17532118e554"
            ),
            "231362": (
                "007eb36c8e5ece2d3fcaef745bda8991"
                "57c1bbddaaf523c879c59091824f4127"
            ),
            "231361": (
                "7f9f2bca1cc901769f665a469a6c704f"
                "506a37d10651a500c9461b977fd47d9f"
            ),
        },
    },
    "mer00046km": {
        "summary": (
            "Full-time logistics internship for current bachelor's/master's "
            "students in logistics/related engineering or graduates within12 "
            "months. Logistics/supplier-quality experience and Spanish/German "
            "are advantages. Three completed semesters and six-month "
            "availability are preferred. Listed pay is USD 20/hour; no housing "
            "assistance or relocation package is offered. Office/Adobe and "
            "communication skills required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046km",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "231234": (
                "86232e57ab02c37fc0f2e2983eb55bf6"
                "126be897a14d7ec36da136534e77e71e"
            ),
            "231231": (
                "1c11e953b327125058ff715d906e7879"
                "3b028208dfe6ab89b0427aa30a2824c1"
            ),
            "231230": (
                "1cfdd2fff369bf12313ffc8c3270fb7b"
                "43c8eda74107463c6f59caa5647c72eb"
            ),
        },
    },
    "mer00046oh": {
        "summary": (
            "People Solutions internship for business/social-science or related"
            " students, preferably HR-focused. Requires German/English, MS "
            "Office, structured administration/coordination and ability to "
            "learn systems. Initial practical experience, preferably HR, is "
            "desirable. Submit enrolment and study-duration evidence and "
            "compulsory-placement proof if applicable. Listed benefits: "
            "coaching, health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046oh",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "232025": (
                "89d6c7307c762f54673f1d59d8268088"
                "2672244746fd96fa71be509f55bc05a9"
            ),
            "232023": (
                "b65913a60afa8262f7098faadf344c3a"
                "0f162c588f0d2a2ca97f4203d52ae0bc"
            ),
            "232021": (
                "f59f62ac80ff0d8726bab37cebcab05f"
                "02d519316288724903c0c11358728f49"
            ),
        },
    },
    "mer00046ps": {
        "summary": (
            "Controlling internship for college or graduate-school students "
            "with fluent written/spoken English and Chinese, MS Office and "
            "communication skills. AI tools/VBA are advantages. Requires a "
            "valid Taiwan work permit, at least three days weekly for six "
            "months, and an indicated starting date."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046ps",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "231847": (
                "7d95c2b10be7cda5615eb9545e09e149"
                "8e434bc8688d6d8c5f87eba5ab5e0838"
            ),
            "231846": (
                "2886a2c31e66497c96a0f951b186e0fa"
                "3101fc6c71e2ffa955edeadbc4e3339a"
            ),
            "231844": (
                "70d6f69778a18f8944ec12dd66cfd56f"
                "d3f5adfc2601d7d0bebe9505c20416cf"
            ),
        },
    },
    "mer00046uy": {
        "summary": (
            "Compulsory van body-engineering internship for mechanical/design, "
            "automotive or related technical students. Requires German and "
            "English, MS Office, analytical teamwork, structured responsible "
            "work and willingness to learn. The placement must include a "
            "prescribed mandatory component; submit supporting study/placement "
            "documents. Listed benefits: coaching, health benefits, meal "
            "discounts. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046uy",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239614": (
                "bbc5fa6326612c90ac38e0ff034326b2"
                "b1fc4c18095973a38cfcc86221c020a6"
            ),
            "239613": (
                "e3bf221ad242008eed5b2661d57000ea"
                "ac9d0d629f3d36693e773d5171388efa"
            ),
            "239612": (
                "eafbb1d93e3e2d11816631d411cf6ac0"
                "11198f1011c326fad27bdafd0d9b7609"
            ),
        },
    },
    "mer00046zd": {
        "summary": (
            "Logistics-engineering methods/project internship for business, "
            "communications, business-IT, industrial-engineering or related "
            "students. Requires very good German/English, MS Office, strong "
            "communication/organisation and structured conceptual work. Submit "
            "enrolment and study-duration evidence. Listed benefits: coaching, "
            "health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00046zd",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "232911": (
                "b27ba9c2c4b6aeb1e1ae502fffc2426f"
                "18dfa57e8cf74c72acb596b5bdd42d6e"
            ),
            "232910": (
                "eef5802d42fe6c67665106c0039b5a5f"
                "c25c487b557244cd0361e0d80edeb329"
            ),
            "232909": (
                "64e2c767eed2b378855f0845f6ab42e1"
                "c46a06dd3b70ecb93083950067dfb906"
            ),
        },
    },
    "mer000470e": {
        "summary": (
            "Transportation student job for business/industrial-engineering or "
            "related students, ideally focused on transport, logistics or "
            "supply chains. Requires German/English, MS Office, analytical "
            "teamwork and initiative. Content/social-media experience is "
            "desirable; experience with or interest in workplace AI tools is "
            "sought. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000470e",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239935": (
                "cef5317abff7f50301e98fe04d21a95f"
                "f7b6b0825c531e194f3d7c0ac54206a2"
            ),
            "239932": (
                "ddb30038e519386984484764a079f1aa"
                "0b5c4721eefd4ca47d8697355dcc7646"
            ),
            "239931": (
                "ff7e353438af1ddc61ed82a94fd373e1"
                "90b45fdbc4b60feef1c5b33b6330dcd9"
            ),
        },
    },
    "mer000475x": {
        "summary": (
            "On-site engineering internship for "
            "mechanical/electrical/industrial-engineering or related "
            "bachelor's/master's students or graduates within12 months. "
            "Applicants should be self-motivated and willing to learn from "
            "engineers. Listed pay is USD 20/hour; housing assistance and "
            "relocation packages are not provided."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000475x",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "233527": (
                "455a61b860846e9ed57d04f20b4338c2"
                "c37056d58dc3ab7ff9cc41a8d4685863"
            ),
            "233526": (
                "4baaf787f06288f9742740c485628530"
                "63e5455481295d2cec5a1744a7ff4906"
            ),
            "233524": (
                "d4c6cff38a118a3d2f18bb43523e85c9"
                "dc34a94bc28c5c5bc7b448e271f074bd"
            ),
        },
    },
    "mer00047a0": {
        "summary": (
            "Data/software student job for computing, business-IT/mathematics, "
            "mechanical/industrial/electrical engineering, business or related "
            "students. German OR English is sufficient. Requires "
            "Python/PySpark, large/unstructured-data handling, Databricks and "
            "basic software-lifecycle/CI-CD knowledge; "
            "JavaScript/TypeScript/React experience is desirable. Listed "
            "benefits: coaching, health benefits, meal discounts. Excellent "
            "grades; regular-study duration+max3 semesters, open exams;7–20 "
            "h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047a0",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238606": (
                "7bc739d1025080857daf27eef180dedd"
                "685e7657154192ee7f03c414df629703"
            ),
            "238603": (
                "f74ba8a2ff26c0e2f492e500fffc3471"
                "2ade4b29eea7feef0077fe035dbba6de"
            ),
            "238602": (
                "8da51125f25b902bd5f0b0501b00e5dc"
                "11fb5771e2198a6b9f2c2aa06b01e50b"
            ),
        },
    },
    "mer00047bj": {
        "summary": (
            "On-site strategic-project internship. Preferred education is "
            "current bachelor's/master's study or graduation within12 months in"
            " construction/mechanical/civil engineering, project management, "
            "data analytics, computing or related fields. Advanced Excel, "
            "strong Power BI and analytical/communication skills are sought. "
            "Listed pay is USD 20/hour; no housing or relocation package."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047bj",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "233558": (
                "0ebeb5410e218e7022ff0379bd20b41f"
                "f1bf9e8145205f3f34b1b6dc6c7a15df"
            ),
            "233556": (
                "1513b9f30a4adb59fc975c3f38466a30"
                "f63b05b2ebde2552b4c8346b01126729"
            ),
            "233554": (
                "4100a7e097340d529a29a81055989b97"
                "5f664bbc960221ec898a5d0cdc1176d6"
            ),
        },
    },
    "mer00047fi": {
        "summary": (
            "Technical-support internship requires current bachelor's/master's "
            "enrolment or graduation within12 months, "
            "September2026-February2027 availability, evidence of proficiency "
            "and automotive-dealership experience. Maximum28hours/week. MS "
            "Office, technical writing, database/reporting and Power BI skills "
            "are preferred; automotive diagnostics experience is advantageous. "
            "Listed benefits: coaching, health benefits, company pension."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047fi",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "233981": (
                "c1af50b71d49743d3363ef27df232403"
                "d7ef7b25d3ccfefb68d319a62e379c4e"
            ),
            "233980": (
                "11be4ac2a847105bc6fc1998bb294252"
                "1ed636b59952096864de833fe94f3b69"
            ),
            "233979": (
                "dd27f4a246ccf006005c0cb241b635a7"
                "c358ea6a4d5d66bc6263224f5c8f6580"
            ),
        },
    },
    "mer00047hh": {
        "summary": (
            "Site-management-assistance internship seeks advanced business, "
            "industrial-engineering, business-IT, communications or related "
            "study. Requires strong organisation/communication, very good "
            "German/English and PowerPoint/Excel, analytical teamwork and "
            "substantial on-site availability; interest in management, "
            "digitalisation and AI is expected. Listed benefits: coaching, "
            "health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047hh",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "234715": (
                "659a3c3e706efedc35872c3f143e8b39"
                "a248ee3792710fbdac13810705ab4c25"
            ),
            "234714": (
                "b699c9ed52f30f94d7a836af847ffc5a"
                "52699d343f2bc4729e2b676fa19e09a0"
            ),
            "234718": (
                "afd8d0a58468a5f9e5830896a85f66c4"
                "efdbf68d76242705f648706940b361a1"
            ),
        },
    },
    "mer00047i2": {
        "summary": (
            "Vehicle-electronics/software student job for engineering or "
            "related students with German and English, MS Office, teamwork and "
            "analytical, strategic working skills. Submit enrolment and "
            "study-duration documentation. Listed benefits: coaching, health "
            "benefits, meal discounts. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047i2",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "235140": (
                "9477a9233a90d7947105fa9cf1263c54"
                "97181a65671a5f48dac79dc01bc4bfc7"
            ),
            "235139": (
                "c915370d2d7bf4bc1070f40a4f07afc2"
                "eb3212b03527d7adda78262308c70369"
            ),
            "235135": (
                "dafe83c480a0051634f288d4fa1ee86c"
                "7507fbb816a0ab61d15a3f4f8ce57909"
            ),
        },
    },
    "mer00047i3": {
        "summary": (
            "Whiplash-simulation student job for automotive or "
            "mechanical-engineering students. Requires German and English, MS "
            "Office, teamwork and analytical/strategic working skills. Submit "
            "enrolment and study-duration documentation. Listed benefits: "
            "coaching, health benefits, meal discounts. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047i3",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239404": (
                "79819c6147427dec99a969b05cf4164f"
                "492fc5bbef1148952f36911395694feb"
            ),
            "239402": (
                "2c82f2ff8af46afeba23e7bdcbbabdb8"
                "21a01a9ee0855e105a99c148115c813c"
            ),
            "239398": (
                "1a8d93b85b2b06b01f6c598419d1ac4b"
                "1841f6446fd784df116fa35294b20eaa"
            ),
        },
    },
    "mer00047ok": {
        "summary": (
            "Vehicle-data student job for business-IT, AI or related students "
            "with German/English, MS Office, analytical teamwork, dashboard "
            "experience such as Power BI and initial AI experience. Submit "
            "enrolment and study-duration documentation. Listed benefits: "
            "coaching, health benefits, meal discounts. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047ok",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238900": (
                "53d5ce1c77a227b0911fd7f726ffaf42"
                "229707632842474de7a49c192237923b"
            ),
            "238898": (
                "415426b3fb07d58b2d6c596794482976"
                "95803216bec4dfa36f2077eff58bbc3c"
            ),
            "238893": (
                "a6ed2e025929a1b7cb8ae000c6f8904d"
                "59e3eb83c6fb5fa18323010ff454e717"
            ),
        },
    },
    "mer00047on": {
        "summary": (
            "Communications/management-assistance internship for "
            "communications, media, business, event-management or related "
            "marketing-focused students. Requires very good German/English and "
            "MS Office, strong communication, initiative and responsible "
            "teamwork; digital-media and AI interest is expected. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047on",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238533": (
                "0350708c70bb02f9873448fb10401e31"
                "6cf35d62216344f98ff0fbb3a4987e25"
            ),
            "238532": (
                "a53fae01d7f9e4db4cc85a7716acafff"
                "30838c7f57b24ea453d7221896b90484"
            ),
            "238529": (
                "196991a3e283587e830fd5a13f755c59"
                "3bfd2991115baf3c3586a4dcd4d85610"
            ),
        },
    },
    "mer00047p3": {
        "summary": (
            "Engine-development student job for mechanical-engineering students"
            " specialising in vehicles/engines or a related field. Requires "
            "German/English, MS Office and structured teamwork. Programming, "
            "specialist-software and combustion-engine/thermodynamics knowledge"
            " are desirable. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047p3",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239600": (
                "983eb0bf2e3d3aee7cd91294be35c8aa"
                "8a0877cb98ceb1a6bf9ec75479c4ed67"
            ),
            "239596": (
                "7a5a320d668f7c4f93f962f868cfe7fa"
                "f2a6af1df5d79a91a372de637c34d72b"
            ),
            "239594": (
                "5946fb02a7a0496c096a0009615b589f"
                "f296779d41be8de7129266347787be90"
            ),
        },
    },
    "mer00047r3": {
        "summary": (
            "Logistics-process internship for industrial-engineering, business "
            "or related logistics-focused students. Requires negotiation-level "
            "German, good English, PowerPoint/Excel, analytical problem-solving"
            " and independent teamwork. Database/AI experience is desirable. "
            "Submit enrolment and study-duration evidence. Listed benefits: "
            "coaching, health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047r3",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239914": (
                "e9b40020431987ece6f585e26d8f81d3"
                "d897a4425f3317c74519784458b8e2ba"
            ),
            "239913": (
                "55d66ad2b0a27bf840c28bbc6767b098"
                "a7e4046baefacdb3afd7b2261868e0ae"
            ),
            "239910": (
                "832b7860ecd7bbc2aba3a1710958a0fb"
                "cf3837bd1194d2d045ba3494d6fdba60"
            ),
        },
    },
    "mer00047s3": {
        "summary": (
            "Dual BEng automation programme runs October2027-September2030, "
            "with DHBW Friedrichshafen theory and Stuttgart/Berlin placements; "
            "mobility is required. Requires technical/programming interest and "
            "logical independent teamwork. Submit a CV and last two school "
            "reports; grade alternatives apply. Youth travel-ticket and "
            "relocation support are described; Stuttgart dormitory housing is "
            "normally possible. Entry routes: technical Abitur avg≤2.7/math≥8 "
            "points; business avg≤2.5/math≥10; FHR avg/math≤2.0 plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047s3",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "235931": (
                "65c9b0d47ce830d6355239bd5984793c"
                "9506580370a4678d3fec209f5974e182"
            ),
            "235928": (
                "05397306fa93e778bde77bdedf717995"
                "3ea76c0a990c0ed94d3787765d211f6b"
            ),
            "235927": (
                "ca40158a250faf87c4d634afbd9e962b"
                "794c731f883a3470ce29c28bbfe385c7"
            ),
        },
    },
    "mer00047s8": {
        "summary": (
            "Six-month logistics-provider internship may start mid-November2026"
            " in Bremen. Business/industrial-engineering or related study with "
            "a logistics focus is sought; German at least C1, good English and "
            "Word/PowerPoint/Excel are required, alongside communication, "
            "initiative and interest in logistics, AI and automation. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047s8",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236073": (
                "16f1f0b2e0289d5118e1e761d196bb42"
                "4bc6d804bea887a2d0a04eebd5a971ee"
            ),
            "236071": (
                "b202ca66e27c91492715336744a2f6d5"
                "04d89376f7d8b7a25c0d7f8062558ad1"
            ),
            "236068": (
                "9df5a94e661d15e028f5a74e98c608e8"
                "f4b3d0ae78828faa58aba2075e6918a2"
            ),
        },
    },
    "mer00047s9": {
        "summary": (
            "Berlin automotive-business apprenticeship lasts2.5years. The "
            "advert seeks completed/near-complete Abitur or Fachhochschulreife,"
            " while also stating at least a very good MSA: verify this mixed "
            "wording. Requires English, clear communication, business/technical"
            " understanding and customer focus. Submit CV, motivation, three "
            "reports and placement certificates; it is not sales training. "
            "Listed benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047s9",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236213": (
                "ba09cd87d56daf5cba02ec8efaa34b90"
                "d92e82d3c89185378ffed3c8811b9468"
            ),
            "236212": (
                "67816c55ff38626f1a367bc738d53ccc"
                "5d8d6346091446518cefd4c54ff211c0"
            ),
            "236209": (
                "cf4f0fe5f5f7fef04370cfa965487452"
                "d80ed56fe66cf2ec4298acf4f8d6fa89"
            ),
        },
    },
    "mer00047sg": {
        "summary": (
            "Insurance-governance student job for economics/business, law, "
            "insurance or related students. Requires MS Office, analytical "
            "independent structured work and communication/teamwork; interest "
            "in governance, compliance and regulation is sought. No language "
            "threshold is specified in these qualifications. Listed benefits: "
            "staff discounts may be available, staff events, on-site doctor. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047sg",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241028": (
                "5c6b97ed2e92fc0c9cd913c788291824"
                "746d1660ae394f2313112a3c065fdff4"
            ),
            "241032": (
                "07eddcaeca25c97b3ada8f6a094f2dab"
                "0738497590c49817ebc101bc382dbedf"
            ),
            "241030": (
                "a4dd2c2df540a69f002f9e49646ccf54"
                "06e9fc51c993292e8663b777ae7c627a"
            ),
        },
    },
    "mer00047t1": {
        "summary": (
            "Vehicle-testing internship for enrolled "
            "electrical/automotive-engineering or related students with initial"
            " electric-mobility knowledge and strong MS Office, "
            "communication/organisation and independent working skills. Python "
            "is advantageous. Six months,16hours/week, immediate start; EUR "
            "2,243 gross monthly is quoted as a FULL-TIME reference, not "
            "the16-hour salary."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047t1",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236011": (
                "2ce9dc4f78309c0346d71e5f5ec4ecf2"
                "fedf5569c9b115d606923a8ab77cc7fd"
            ),
            "236013": (
                "e24bd4095a92861225729caa28d62728"
                "795d64e538328d8225e6f24e603aa06a"
            ),
            "236012": (
                "1865fb835e956a4f614344d60a590411"
                "92a6db2115bddbd39c1d4a2a10a319d3"
            ),
        },
    },
    "mer00047td": {
        "summary": (
            "Six-month talent-management internship for psychology, business, "
            "business-education/psychology or related students. Requires "
            "German/English, PowerPoint/Excel, communication, teamwork and "
            "analytical, pragmatic working skills. Submit enrolment and "
            "study-duration evidence. Listed benefits: coaching, health "
            "benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047td",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236443": (
                "a46509f0c92f6e9c3533242f6258f636"
                "32caab1bfba57c4f3c52f6aff925d9c5"
            ),
            "236442": (
                "91020f8d38191a5953401e07c5b80c7c"
                "83cccdbc13439fe373c407b3e3847d23"
            ),
            "236441": (
                "f91d2dbbeb769d07af324bea459c1928"
                "c60dd26d99109d7e14cc68d4064d105e"
            ),
        },
    },
    "mer00047u1": {
        "summary": (
            "Engineering co-op requires at least sophomore standing, current "
            "related undergraduate study at an accredited U.S. university, "
            "minimum3.0GPA and availability for three alternating "
            "spring/summer/fall terms in Vance, Alabama. A housing stipend is "
            "available only if the mileage conditions are met."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047u1",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "238991": (
                "7149e0f66beec13fa962098b9af85a1c"
                "f92ba1ae504184d7fdd032868a93b8ef"
            ),
            "238990": (
                "b8d82a6d78fceb3ca57de2347697db5f"
                "13c326234657a06ddc3f48a07c43c857"
            ),
            "238989": (
                "e41e230b41cd3f735254f43935982204"
                "265e5af0f386daab2589f9af34022263"
            ),
        },
    },
    "mer00047u2": {
        "summary": (
            "Business-major co-op in Vance, Alabama involves three alternating "
            "spring/summer/fall terms with mentored industry projects. "
            "Applicants must be at least sophomores, currently enrolled in a "
            "related undergraduate major at an accredited U.S. university, have"
            " a GPA of at least 3.0 and complete all three terms onsite in "
            "Vance. A housing stipend is available only when qualifying mileage"
            " criteria are met; no amount or citizenship condition is stated."
        ),
        "condition_evidence": [
            (
                "At least sophomore level and CURRENT undergraduate enrolment "
                "in a related major at an accredited university in the United "
                "States. This is an institution/enrolment condition, not a "
                "U.S.-citizenship requirement."
            ),
            (
                "Minimum GPA 3.0 and ability to complete three alternating "
                "terms in spring, summer and fall."
            ),
            (
                "Must be able to work in Vance, Alabama. Housing stipend is "
                "conditional on meeting qualifying mileage criteria; neither "
                "exact mileage threshold nor payment amount is stated."
            ),
            (
                "Term projects, supervisor/mentor meetings, required "
                "departmental/safety training and end-of-term presentations are"
                " part of the programme. Structured start date is 12 October "
                "2026, not an application deadline."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: annual profit share, flextime, discounts for "
                "employees, mobile phone for employees."
            ),
            (
                "Additional structured facilities/support listed: health "
                "benefits, company retirement, mobility offers, coaching, "
                "events for employees, parking, canteen, café, near-site "
                "childcare. These names do not establish an individual cash "
                "award."
            ),
            (
                "Structured role labels: Full time. Narrative requirements "
                "above take precedence where more specific; source publication "
                "end dates are not independently verified application closing "
                "dates."
            ),
        ],
        "categories": ["internships", "jobs"],
        "variant_hashes": {
            "240942": (
                "db5db1e2bc02109ebe6e2b60817e67db"
                "96b9c178356129d22e050937c7a0db5a"
            ),
            "240941": (
                "928a976cc043715e13f9ccd00b109f51"
                "a532c7ae18f2ab02327468cf049c639a"
            ),
            "240940": (
                "cd3087aae2b5f89c39f0778bf266e32b"
                "81badfac52409c2eff10bb4110f3c99b"
            ),
        },
    },
    "mer00047uq": {
        "summary": (
            "Vehicle-painting apprenticeship requires at least a good "
            "MSA/Realschulabschluss, technical/manual aptitude, maths, "
            "chemistry and physics, reliable colour vision and careful work. "
            "Submit CV, motivation letter, last three school reports and "
            "placement certificates online. Listed benefits: coaching, health "
            "benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047uq",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236223": (
                "29c03c0faf0f3ea8e39f34a28346c086"
                "0bfa4e8e1f9e75fd490d341ae27a52f3"
            ),
            "236222": (
                "a613d9c23bb3088ed7d796e4a0a007f5"
                "bf6979d2170ef41e69ead09d294722c5"
            ),
            "236221": (
                "d382bdafcbc1385d2a63c53108f14d18"
                "3dfa7911d7776fb3b50cedae359d2c06"
            ),
        },
    },
    "mer00047ur": {
        "summary": (
            "High-voltage vehicle-mechatronics apprenticeship requires at least"
            " a good MSA/Realschulabschluss, technical/manual aptitude, "
            "maths/physics, vehicle-technology interest and reliable colour "
            "vision. Submit CV, motivation letter, last three reports and "
            "placement certificates. Successful completion offers employment "
            "prospects, not guaranteed hiring. Listed benefits: coaching, "
            "health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047ur",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236240": (
                "d567d79bd2cf07ff48ac1c61595c9e84"
                "39fb8a2fe75288dfc6a42f6048d6ed70"
            ),
            "236239": (
                "4e85433e42010851d94ba0a6ae5ea80b"
                "184385a04269f233a7b0393822ba39b3"
            ),
            "236237": (
                "787e91564e1f64f05609f2eba6fd0eab"
                "73406ddd56f4bd08c3a2adc6121c9b32"
            ),
        },
    },
    "mer00047ut": {
        "summary": (
            "Body-repair apprenticeship requires at least a good "
            "MSA/Realschulabschluss, technical/manual aptitude, maths/physics, "
            "spatial reasoning and interest in vehicle repair. Submit CV, "
            "motivation letter, last three school reports and placement "
            "certificates online. Listed benefits: coaching, health benefits, "
            "meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047ut",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236249": (
                "bc74cf4f5bb5cb1e8ede29392831ba93"
                "b94fac111903cd35c1566a0982630426"
            ),
            "236246": (
                "97abe19b99add4d20300a41a75d0a9dd"
                "9dec11c7fa4bf7d775be24c2546dabb9"
            ),
            "236245": (
                "7f0914889c29891010f86e6e8397b0cd"
                "e6c1e75083963f6f7f979fa5dfcf24d5"
            ),
        },
    },
    "mer00047uu": {
        "summary": (
            "Warehouse-logistics apprenticeship requiring at least a good "
            "MSA/Realschulabschluss, good German and maths, business "
            "understanding, organisation and careful work. Submit CV, "
            "motivation, last three reports and placement certificates online. "
            "Successful completion offers employment prospects, not guaranteed "
            "hiring. Listed benefits: coaching, health benefits, meal "
            "discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047uu",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236252": (
                "4b91817b043f1c41ef2024ad387bd582"
                "a1360706995eb813c9c2e1b26a24f7f8"
            ),
            "236251": (
                "f289be765b91f5618eb61f6558d98f70"
                "1e33ca569e89758e202d6c3bf987ae35"
            ),
            "236255": (
                "5d4c4a738b2b7d6a936254916e1642f6"
                "e5542fe21f0531b6a32b07e2ba16f9c9"
            ),
        },
    },
    "mer00047w3": {
        "summary": (
            "Vehicle-quality project internship for business, "
            "mechanical/industrial-engineering or related students with "
            "German/English, MS Office, communication and analytical teamwork. "
            "Basic technical understanding is advantageous and a category-B "
            "driving licence desirable; interest in cross-functional "
            "project/quality management is sought. Listed benefits: coaching, "
            "health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047w3",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236556": (
                "90511d33252df12103d652ce41bbe74b"
                "55dfa7073cb6065b32094a5022773afd"
            ),
            "236553": (
                "36d7918c823864c679ef0e156fdfd999"
                "cd843848cf19d625f719d50576f2dad0"
            ),
            "236552": (
                "d81606cb37668beb861d8a6bd63daebc"
                "7aa3de189871ee58797a7f908144b20b"
            ),
        },
    },
    "mer00047yf": {
        "summary": (
            "Digitalisation/AI internship requires negotiation-level German and"
            " English, relevant "
            "engineering/production/logistics/computing/data-science/mathem"
            "atics"
            " or related study and Excel/PowerPoint. Java/Python/SimTalk and "
            "Power BI/Databricks experience are advantages; "
            "ML/process-mining/simulation experience OR willingness to learn is"
            " accepted. Submit enrolment/study-regulation evidence. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047yf",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239798": (
                "492a607e3c62ae26ccca0f7925e33e73"
                "dab8df5500586fcb3d7b0002fc503912"
            ),
            "239797": (
                "fb1842e80cccd1537525e1fe2c7129b1"
                "083f0e004a48416bee939c08751840b9"
            ),
            "239800": (
                "33c5fa737706db7ccf0d335402c51e48"
                "b71ecb08bacbfac921d18f034c00b303"
            ),
        },
    },
    "mer00047yw": {
        "summary": (
            "Compulsory HR-data internship for economics/business, psychology, "
            "computing or related students. Requires interest in data "
            "visualisation/analysis, structured analytical work and willingness"
            " to learn HR systems. A mandatory placement component is required;"
            " may start March2027. No language threshold is explicitly "
            "specified in the qualifications. Listed benefits: coaching, health"
            " benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047yw",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "236817": (
                "53a73664893e5bb0e9444447335cbb22"
                "9637a10e661e0baa2fb74df18273ccb5"
            ),
            "236814": (
                "ea61de2a7c4193545350a6f90663f2ea"
                "b1dd04f8db5e708723b9e903a61b83b9"
            ),
            "236813": (
                "cd0e4d28e6d42d552ed6987b34a82015"
                "f039caec9d52ed8f38d8681faea1d149"
            ),
        },
    },
    "mer00047zd": {
        "summary": (
            "AI documentation-consistency thesis for "
            "automotive/mechanical/electrical/industrial engineering, "
            "technically focused business or related students. Requires "
            "German/English, MS Office, technical understanding, communication "
            "and analytical teamwork. International/intercultural experience is"
            " desirable. Listed benefits: coaching, health benefits, meal "
            "discounts. Above-average grades and prior relevant internships; "
            "EEA ID/passport, otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047zd",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239452": (
                "9bac28a3034b750bb0d52793128db156"
                "7cee81523ebd911293d99036eb39f993"
            ),
            "239450": (
                "3e57619faee41a7e4431ab70637375c7"
                "83fedd22072d381bff0110c3e2ca2ddb"
            ),
            "239448": (
                "2d7342625c2b489bcef408b6db21e540"
                "d91435624c55e43a55e9016b57e2c450"
            ),
        },
    },
    "mer00047zi": {
        "summary": (
            "Full-time logistics-planning internship requires current "
            "logistics/related-engineering bachelor's/master's study or "
            "graduation within12 months. MS Office, Adobe/Visio and "
            "communication skills are required; logistics experience, Power BI "
            "and Spanish/German are advantages. Three semesters and six-month "
            "availability are preferred. USD 20/hour; no housing or relocation "
            "package. Listed benefits: health benefits, staff discounts may be "
            "available, staff events."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00047zi",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "236725": (
                "3e55711e65a3b448048642217d370d4b"
                "c54ed77bfdf70a01bed5d706c9cbedcc"
            ),
            "236724": (
                "105ac0223b6624fbb9467c20b553f006"
                "afa8b1ec71b761ae7cfa2b72bfe8018f"
            ),
            "236720": (
                "a44e6ce8744f9ec9c7f35087e033540b"
                "f9b169cef14efb2880486dd33e7655ef"
            ),
        },
    },
    "mer000480m": {
        "summary": (
            "Vehicle-testing student job for engineering, "
            "automotive/mechatronics or related students. Requires a category-B"
            " driving licence, German and English, MS Office and analytical "
            "teamwork. Planned at12hours/week from October2026. Listed "
            "benefits: coaching, health benefits, meal discounts. Excellent "
            "grades; regular-study duration+max3 semesters, open exams;7–20 "
            "h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000480m",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239133": (
                "c96d03d68d0972b39e9fbcda0cc5561d"
                "05090c1e109bccdbc86dc670def96090"
            ),
            "239132": (
                "3548d3b382e7f3d95143963b70fca46c"
                "18caa7c319592e30e031572196415ee2"
            ),
            "239125": (
                "1903275790db6cf6d0a5a3b264212950"
                "d9bbc7e89c436e6beff0f39d3ff8134e"
            ),
        },
    },
    "mer000480w": {
        "summary": (
            "Design-content internship for enrolled "
            "media/design/film/photography/communications or related students. "
            "Requires fluent German/English, MS Office, practical photo/video "
            "production, confident Premiere Pro/After Effects/Photoshop, "
            "organisation and independent teamwork. Portfolio/work samples are "
            "welcomed, not explicitly mandatory. Listed benefits: coaching, "
            "health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000480w",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "237274": (
                "0ef573b783f79c00a131482c3bc23242"
                "9bffac369d2e3b6369093ffac55f85d9"
            ),
            "237271": (
                "6dc1e3c4d98a42450560cbb3fdc01fe9"
                "181528d74a10affa6ce74e69ea2c743a"
            ),
            "237270": (
                "dfaf6598e2f26c1add02c26b8ec2bcc0"
                "06af5fee8747301cc775f87e51232520"
            ),
        },
    },
    "mer0004810": {
        "summary": (
            "Risk-model-validation internship for "
            "mathematics/statistics/science/economics/business-IT/data-scie"
            "nce/industrial-engineering"
            " or related students. Requires basic quantitative/statistical or "
            "econometric methods, programming/data analysis such as "
            "Python/R/SAS/SQL, negotiation-level German, good English, "
            "Excel/PowerPoint and careful analytical work. Listed benefits: "
            "company pension, hybrid work may be available, flexitime may be "
            "available. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004810",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238589": (
                "58d88fd287147329ff76c0753bec6e1b"
                "7b592062f5d354c64c05f171aade1716"
            ),
            "238587": (
                "d430617456a8b53cd2826018ef7dfff2"
                "a009467aadea5720d1b3e3e5bd9551f3"
            ),
            "238586": (
                "2931a682ce1356b30102dad3743efca7"
                "c62298f919aea1a808676d01b44fda4b"
            ),
        },
    },
    "mer000481i": {
        "summary": (
            "Compulsory employer-branding internship for "
            "marketing/communications/media/design or related students, "
            "preferably from semester3. Requires negotiation-level "
            "German/English, MS Office, graphic-production experience and very "
            "good Photoshop/InDesign/Illustrator, creativity and teamwork. "
            "Hybrid model; a mandatory placement component is required. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000481i",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "237285": (
                "9866d2bb8765fd8ab09e0d32ddedab03"
                "c8f014b35f697cb90a34b65875b6936b"
            ),
            "237283": (
                "111ceefb7efea6be3fd52bc9769a9db4"
                "fb4ed474f5106ce6b5bb393f8a42098f"
            ),
            "237282": (
                "ce71804d5d6e2baf4051cab3b9206145"
                "8431211759d0484c05d094f5c82b719e"
            ),
        },
    },
    "mer000481q": {
        "summary": (
            "Vehicle-customisation student job for industrial-engineering, "
            "business, automotive/mechanical-engineering or related students. "
            "Requires very good German/English, PowerPoint/Excel and MS "
            "Copilot, analytical organisation and independent work; "
            "premium-vehicle customisation and project-management interest is "
            "sought. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000481q",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238622": (
                "07e33974f812ebb5b665e09a292d1df3"
                "0e5a3c60e2b05aac67ea203b3af0dddf"
            ),
            "238621": (
                "37dc6ffbab3c509ebe3fccb6313a21cf"
                "896dc33c148a314289519c6e5d5f63d2"
            ),
            "238620": (
                "4834c00b2279ef6f254609295d28bcff"
                "bae39aded7907ae65031926c707f0992"
            ),
        },
    },
    "mer000481u": {
        "summary": (
            "Inverter-development student job for electrical-engineering or "
            "related students. Requires basic inverter/power-electronics and "
            "synchronous-motor knowledge, MATLAB and/or Python, German/English "
            "and independent efficient communication. Willingness to learn new "
            "technical areas is expected. Listed benefits: coaching, health "
            "benefits, meal discounts. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000481u",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239629": (
                "56ea8fbf1a0fbd95a6e15114a2d73fed"
                "db81dcc36d522081f93beab4000b7b18"
            ),
            "239623": (
                "333b6b46bf7bb26e2710a12213eaf3e7"
                "c801605812bc8d2dd0c619257f5fe570"
            ),
            "239621": (
                "fef8e6113c0ad332a33ca882571ddb1c"
                "8c3b27228ae198cc7c949801ee0c44d7"
            ),
        },
    },
    "mer000482m": {
        "summary": (
            "Material-cost-controlling thesis for "
            "business/industrial-engineering or related students with "
            "German/English, MS Office, communication, analytical teamwork and "
            "strong business understanding. Month-end-close knowledge is "
            "required, ideally from a Mercedes-Benz setting. Listed benefits: "
            "coaching, health benefits, meal discounts. Above-average grades "
            "and prior relevant internships; EEA ID/passport, otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000482m",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238932": (
                "ed22088066c50ab2633521ba2e553ca6"
                "609297a8fe9a7802db4814f2abe189af"
            ),
            "238931": (
                "f8ab2d68a95f655d0dab24110ca75419"
                "73f3ecd887e74caa03ec9141b81a8551"
            ),
            "238930": (
                "ecf9e5bfcc76607a14a9cc6708076679"
                "384d767ae76f3e0be27ff1f19e5f56b8"
            ),
        },
    },
    "mer000483p": {
        "summary": (
            "Automotive business apprenticeship requiring at least a good "
            "intermediate school certificate, basic business/technical "
            "knowledge, clear expression and English, customer focus and "
            "teamwork. Listed benefits: coaching, health benefits, meal "
            "discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000483p",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "236981": (
                "b3a9162c907a8475035310ec2a6e8664"
                "c2ecfa34225883648649930d5fbb6cf6"
            ),
            "236980": (
                "17789b538be1aa7fdc1d3c022ae3e293"
                "d13314c2aff7fbbb2fab03042684d920"
            ),
            "236979": (
                "824e4e11e6a45dcd08de64498a8e0517"
                "e9c14b0d88c166af81477739f496a1b9"
            ),
        },
    },
    "mer000483q": {
        "summary": (
            "Midsize strategic-project internship for "
            "mechanical/automotive/industrial-engineering, business or related "
            "students. Requires German and English, MS Office, teamwork and an "
            "analytical approach with interest in automotive work. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000483q",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241563": (
                "59fa1a1d2ba905851bf068285541de86"
                "93a661b838f10b110353086d45006fae"
            ),
            "241562": (
                "a48073dc24cb33b9d34747bae99a08ca"
                "2c62c6a83b2a40fe81efc4d0d0967517"
            ),
            "241561": (
                "465c8029fc2449d1ec3dabe3ba41ecd9"
                "8b4caf2cf0db341f068961cc750aa5a1"
            ),
        },
    },
    "mer000483r": {
        "summary": (
            "Quality-management internship for "
            "business/economics/industrial-engineering, communications, "
            "international business, business-IT, mechanical-engineering or "
            "related students. Requires German/English, MS Office, "
            "communication and analytical, independent responsible teamwork. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000483r",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239045": (
                "0dac7718df6666b59601b565859c5715"
                "01fa7a76f64a6acf491521085030c418"
            ),
            "239042": (
                "1aa6d7b429ce28f15268daee386b5351"
                "1a6fe82bcecfd3fd1e45c651ad422958"
            ),
            "239038": (
                "1ae74f251cc033461a4f0590c745d2ee"
                "507a93b343da5d7fb8741a7f0ef1cb7e"
            ),
        },
    },
    "mer000483s": {
        "summary": (
            "Office-management apprenticeship requires at least a good "
            "intermediate school certificate, basic business knowledge, good "
            "German and English, customer focus and careful responsible work. "
            "Pay is listed at EUR 1,242 in year one to EUR 1,586 in the final "
            "year, depending on location; subsequent employment is a prospect. "
            "Listed benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000483s",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "237011": (
                "706fc604f053cc05ce41f0548e6c34ee"
                "64a4d550dc66c144af0e885eff2c693f"
            ),
            "237010": (
                "aebc9b3907a67153fe03a0b184c622ab"
                "254a5c24b376971c4e00ea49145e7777"
            ),
            "237009": (
                "c6ac7129a677912e2b4e07c8c02caae3"
                "3bc5e30efca3263e9960b89d9e5478dd"
            ),
        },
    },
    "mer000483t": {
        "summary": (
            "Warehouse-logistics apprenticeship requires at least a good "
            "intermediate school certificate, good German/basic maths, business"
            " understanding, organisation and careful work. Listed pay ranges "
            "from EUR 1,242 in year one to EUR 1,586 in the final year by "
            "location; later hiring is not guaranteed. Listed benefits: "
            "coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000483t",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "237014": (
                "e07cb74cadb9c699262272cd3e4821d9"
                "be73e7479680738d3abc3fdd4dcb8d03"
            ),
            "237013": (
                "1704e381b629cd7c66ad8dccad75e146"
                "8d8fa4b28bf5543913b359354b00650a"
            ),
            "237012": (
                "daa93a248f9ac7fd112f9806a350bfb4"
                "c2ca9702f0bb1f1e1390cbf55abb588f"
            ),
        },
    },
    "mer0004845": {
        "summary": (
            "High-voltage vehicle-mechatronics apprenticeship requires at least"
            " a good intermediate school certificate, technical/manual "
            "aptitude, maths/physics, vehicle-technology interest and reliable "
            "colour vision. Listed pay: EUR 1,242 in year one to EUR 1,586 in "
            "the last year, depending on location; later hiring is a prospect. "
            "Listed benefits: coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004845",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "237052": (
                "40c6f9bf831f1b70847ad51d35331e0f"
                "4a66aee14f15d831f937a9a7eaa87732"
            ),
            "237051": (
                "a334ef422afcba348a3c5b9285df6e33"
                "13f8ec99efc7b70a7313f39751fd2703"
            ),
            "237050": (
                "f762fac21434f698bb0a63f06a029506"
                "7de588a2ca775a22ad3a6cf1641c7ccc"
            ),
        },
    },
    "mer0004846": {
        "summary": (
            "Passenger-car mechatronics apprenticeship requires at least a good"
            " intermediate school certificate, technical/manual aptitude, "
            "maths/physics and interest in vehicle mechanics/electronics. Pay "
            "ranges from EUR 1,242 in year one to EUR 1,586 in the last year by"
            " location; later hiring is a prospect. Listed benefits: coaching, "
            "health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004846",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "237055": (
                "f9ca7b890556067a0b638d4f26c479d2"
                "316d3047278597e7d7ad66abb5beb3e9"
            ),
            "237054": (
                "50371f29894c68cf8f1c2b630ce72291"
                "7c97d77e60fc0b0ed33790c71781d528"
            ),
            "237053": (
                "6c0ddca10859dc365654f176bca8b83d"
                "e865312e5982d842869208a372c2f696"
            ),
        },
    },
    "mer0004847": {
        "summary": (
            "Commercial-vehicle mechatronics apprenticeship requires at least a"
            " good intermediate school certificate, technical/manual aptitude, "
            "maths/physics, interest in vehicle electronics/mechanics, "
            "responsibility, care and reliability. Listed benefits: coaching, "
            "health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004847",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "237058": (
                "838c4dfe030170e542539beeb6bb2656"
                "8968e87a908abe9045ba31916159f2a9"
            ),
            "237057": (
                "7268ee2cad7f0575705c492af9eb088f"
                "1573090bdc369458c011f9d7732e155b"
            ),
            "237056": (
                "e84e5394ef11f8ad5f7423bf054b0ba7"
                "8bda6d056abf6280bf14f849e1298e39"
            ),
        },
    },
    "mer000484f": {
        "summary": (
            "Six-month front-office/infrastructure internship from "
            "November1,2026 at20-40hours/week requires current business OR "
            "technical study, excellent German, good English, MS Office, "
            "service orientation and a category-B driving licence. EUR 2,243 "
            "gross/month is stated for FULL-TIME work; part-time pay is not "
            "established. Listed benefits: coaching, health benefits, staff "
            "discounts may be available."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000484f",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "237078": (
                "09e74ab4dce365a0db140ce1ccb78bf6"
                "c6ece2cf6b033f7fbec4bcf842583eb5"
            ),
            "237076": (
                "e08030d78c51375207008fe94d82d925"
                "e3a710395ee208ed0e4ac5496c0b99c1"
            ),
            "237075": (
                "0e8cb65b22f826c534e72361a09839de"
                "613edaf5b66009c0d2ff5908c39b335c"
            ),
        },
    },
    "mer000484u": {
        "summary": (
            "Logistics-planning internship requires active full-time Hungarian "
            "student status, native Hungarian, intermediate English and MS "
            "Office; German is advantageous. Minimum six months at three "
            "days/week. Submit Hungarian and German/English CVs. Benefits "
            "subject to employer rules include pay, thesis/mandatory-placement "
            "options,100% public-transport support and role-dependent home "
            "working."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000484u",
        ],
        "categories": ["internships"],
        "language": "hu",
        "variant_hashes": {
            "237167": (
                "2f591a79df03c9944c31669db1d34cdd"
                "af15848f5aadd3753aad1ca0c7e7f6b0"
            ),
            "237166": (
                "80302908f251adccb3631b74d8fc51a2"
                "97b7cfb45500aa7491c6f5404f8483ba"
            ),
            "237165": (
                "3eba403519e12f16cc160f1472029246"
                "4f510f513987ddb934aa4f69016f9bfd"
            ),
        },
    },
    "mer0004853": {
        "summary": (
            "MANUFAKTUR project student job for industrial-engineering, "
            "business, automotive/mechanical-engineering or related students. "
            "Requires very good German/English, PowerPoint/Excel and MS "
            "Copilot, analytical organisation and independent work. Offers "
            "project responsibilities and exposure to vehicle-customisation "
            "management. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004853",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239443": (
                "de938c758cf6595eb1859896e0c53c94"
                "5edf5587ac7bb0c8d88495033f719c17"
            ),
            "239442": (
                "d985e1e18ae1248067df78c8f6dcc993"
                "cabb893aa7398043c052ddfd942506fc"
            ),
            "239436": (
                "ec69e93004375dc57d1587093168745d"
                "fe065d53985b63b1da0f3467c07438bd"
            ),
        },
    },
    "mer000485i": {
        "summary": (
            "Connected-car student job for automotive, "
            "business-IT/computing/software/electrical engineering or related "
            "students. Requires a category-B driving licence, German/English, "
            "MS Office and reliable analytical teamwork. The title says student"
            " job while prose calls it an internship; confirm the exact "
            "placement type. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000485i",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241555": (
                "35298c4f6ceabea47ebac5bdc19b56d6"
                "4b57d4c309d05397f322b74affce66ed"
            ),
            "241554": (
                "e23d70964d998377f8786ad79bb44361"
                "7720d49d9b0002435fb658453feb29df"
            ),
            "241553": (
                "b150752f5b4b236b9dbaf50c6ff49432"
                "269382e3e730efe37ef6c949b559b88e"
            ),
        },
    },
    "mer000485l": {
        "summary": (
            "Six-month ON-SITE compulsory strategic-van-project internship for "
            "business/industrial-engineering or related students. Requires "
            "German/English, PowerPoint/Excel/Outlook, category-B driving "
            "licence and structured analytical communication. Initial "
            "project-management/consulting/automotive experience is preferred; "
            "a mandatory placement component is required. Listed benefits: "
            "coaching, health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000485l",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241052": (
                "e0e8c9edc37756ffd5d2ec4c859169ec"
                "0e848be86b652c674408ad1cd9d7e1c0"
            ),
            "241051": (
                "90ef28363c2cbd3775a7ee6e97af12ca"
                "e8ec2b19b308db6bc49e36b14014f6c5"
            ),
            "241050": (
                "23923f96decc4f6cf22b5527a330b7ea"
                "78a2aab5faaad676429abbf39fe8d118"
            ),
        },
    },
    "mer000485w": {
        "summary": (
            "AI-testing/connected-car internship for "
            "computing/software/electrical/automotive-engineering or related "
            "students. Requires very good German/English, initial exposure to "
            "AI tools/agents/workflows and analytical independent initiative. "
            "Offers practical learning in agile software, vehicle engineering "
            "and AI-assisted development. Listed benefits: coaching, health "
            "benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000485w",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238874": (
                "c3c8fb9b3a518fc1409a1100c65af1f2"
                "95b1b664a9bb27b6c723891c9c5b15b2"
            ),
            "238873": (
                "88101e4fdc967a829d18f80765cd28b6"
                "ca929a516978fb1155c8d404b0bc05f4"
            ),
            "238869": (
                "50d163e397049e2ccbee90ea506c4a02"
                "6fbd41cb2693fd2e7d3c88733e2598c3"
            ),
        },
    },
    "mer000486m": {
        "summary": (
            "IT-financial-controlling internship requires a college "
            "diploma/associate qualification and ongoing controlling, finance, "
            "accounting or business study, excellent Excel and written/spoken "
            "English, independent motivation and intercultural communication. "
            "SAP-FICO and Excel-based data-warehouse proficiency are preferred."
            " Listed benefits: health benefits, staff discounts may be "
            "available, staff events."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000486m",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241467": (
                "ff1d6b01b21b169f91e144e636299973"
                "6737e783dd753d453e066aedf630b990"
            ),
            "241465": (
                "ca72f8e7a3572a07925fcbeccf5e74cd"
                "f757a14f26957346c0d5b85532d18ffa"
            ),
            "241464": (
                "5aac31769ff34acc360de465374f5a3c"
                "1467315267233e7460ca943a04ebd32c"
            ),
        },
    },
    "mer0004870": {
        "summary": (
            "Current economics/business, industrial-engineering or related "
            "studies; very good German/English, Office, initial "
            "collaboration-tool experience and analytical communication. "
            "Project/requirements/business-analysis experience advantageous. "
            "Body says October2027 start; structured date2026-10-01 conflicts: "
            "confirm year. Excellent grades; regular study+max3 semesters, open"
            " exams;7–20 h/week; relevant internships preferred. Benefits: "
            "coaching, health benefits, meal discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004870",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241514": (
                "3ed435d5c027012ea95f4e7569b0502b"
                "99aee482f4db6405317bb9ada29a0d00"
            ),
            "241509": (
                "d38f6ca847b5fe83c64c54989e4728d6"
                "5a8c3b6cf15d78bfb09d2c8669b2175a"
            ),
            "241508": (
                "9678af92164522dbac5d06a659a3ac2b"
                "25e3ed2a7a6eb93e7d1bdddb020f3106"
            ),
        },
    },
    "mer0004875": {
        "summary": (
            "Six-month engineering-planning internship in Hamburg for "
            "engineering, mechanical/production/industrial-engineering "
            "students. Requires German/English, MS Office, initiative, "
            "independent reliable work and interdisciplinary "
            "communication/teamwork, including resilience under time pressure. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004875",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239962": (
                "ebe3225f81241ddae0530ea97e54c79b"
                "c1ef60a6ac97dfd177aff405edf7c6bb"
            ),
            "239958": (
                "9ed7a776d64460807e4c9176328e014c"
                "a2cf3f765cae942601631320e9c775a2"
            ),
            "239957": (
                "326ff49e4b9933e8c26bda5f773f93db"
                "6c646bf3d70ad4648e05553ea5e83d04"
            ),
        },
    },
    "mer000487t": {
        "summary": (
            "Recruitment internship requires active full-time Hungarian student"
            " status throughout, native Hungarian AND working German and "
            "English, at least six months at three days/week. Business/HR/law "
            "study is advantageous. Submit Hungarian and German/English CVs. "
            "Employer-rule benefits include pay, thesis/mandatory-placement "
            "options and100% public-transport support; home working depends on "
            "role."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000487t",
        ],
        "categories": ["internships"],
        "language": "hu",
        "variant_hashes": {
            "237489": (
                "a70d8a5be0b37b71d19f279d9a837f38"
                "bef65d86600bb435b485d2ca8f076506"
            ),
            "237488": (
                "d9a30e5e14fa34ad77780cec96978f05"
                "6c182bbab8a6d346700f506e1ff4f603"
            ),
            "237487": (
                "9391fb871030cc8f863cdbe1bdde21eb"
                "e54a9b75605ff0904431e5a5234e915d"
            ),
        },
    },
    "mer000487w": {
        "summary": (
            "Electronics/software-project student job for engineering/computing"
            " or related technical students. Requires software/electronics "
            "understanding, German/English, MS Office and structured "
            "independent communication/teamwork. Offers exposure to the MB.OS "
            "vehicle-system programme and cross-functional project management. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000487w",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241529": (
                "7e943427b487844f129b5d25ad8d906d"
                "93e9da289fb43a59be6fa8d4ba4bbbba"
            ),
            "241528": (
                "cd4c0194245b230123df06a4b912e0b5"
                "dc3097bc3d199b243bff3d673c30fdc5"
            ),
            "241527": (
                "df8e3a5502f3bd28c96dab3f2c836cd6"
                "c297fdf0fa3e55ba815486b7b23c2508"
            ),
        },
    },
    "mer0004897": {
        "summary": (
            "Infotainment-AI student job for "
            "business-IT/computing/media-computing, business or related "
            "students. Requires initial LLM/VLM/AI-model experience, a "
            "category-B driving licence, analytical independent work and "
            "interest in automotive and generative/agentic AI. Android-app "
            "development is desirable; no language threshold is specified here."
            " Listed benefits: coaching, health benefits, meal discounts. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004897",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239751": (
                "11c47c3a11dd7747a2b0b79490a5c743"
                "6d9c0187c8d590960f6a62c23d5f4be3"
            ),
            "239748": (
                "0ee8134716eb993c76255fa93de88f6d"
                "f415dec3cc79f3f2bf1e5a370fbc119d"
            ),
            "239745": (
                "97429f1af33a3d2b9627558e16f3f55c"
                "ed59466e5962e4126c38529e60056ff7"
            ),
        },
    },
    "mer000489e": {
        "summary": (
            "Compulsory powertrain-quality internship in Stuttgart can start "
            "March 2027. Support engine-project quality metrics, milestones and"
            " reporting. Applicants need engineering, business or comparable "
            "studies, confident German/English and MS Office, especially "
            "PowerPoint; the placement must include a required internship "
            "component. Apply online with CV, letter, certificates, "
            "enrolment/study-term details and applicable "
            "compulsory-placement/standard-duration proof. Mark uploads "
            "relevant; maximum file size is 5 MB."
        ),
        "condition_evidence": [
            (
                "Engineering, business/economics OR comparable studies; "
                "confident spoken/written German and English, MS Office "
                "especially PowerPoint, quick comprehension, creativity, "
                "initiative, communication and teamwork."
            ),
            (
                "This location requires a prescribed compulsory internship "
                "component; do not offer the role as an unrestricted voluntary "
                "placement."
            ),
            (
                "Online application: CV, covering letter, certificates, current"
                " enrolment certificate showing study semester, applicable "
                "compulsory-internship evidence and proof of normal study "
                "duration."
            ),
            (
                "Mark uploaded documents as relevant to this application and "
                "observe the stated maximum file size of 5 MB. Further linked "
                "hiring criteria apply but are not independently restated."
            ),
            (
                "Can start March 2027 in Stuttgart, Germany; no pay amount or "
                "application cutoff is stated in the reviewed description. "
                "Severely disabled/equivalent-status applicants are welcomed."
            ),
            (
                "Potential structured benefits, without a guaranteed amount or "
                "entitlement: mobile phone for employees, discounts for "
                "employees, annual profit share, flextime, hybrid work."
            ),
            (
                "Additional structured facilities/support listed: "
                "meal-discounts, events for employees, coaching, health "
                "benefits, company retirement, mobility offers, parking, "
                "canteen, café, good public transport, barrier-free workplace, "
                "inhouse doctor, near-site childcare. These names do not "
                "establish an individual cash award."
            ),
            (
                "Structured role labels: Full time, Temporary. Narrative "
                "requirements above take precedence where more specific; source"
                " publication end dates are not independently verified "
                "application closing dates."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "237968": (
                "37b310498d24051cfbb3ed5c32533fdf"
                "bccc30a2fc1daab5ba4523b9e292f4bc"
            ),
            "237967": (
                "ec6768a662a61f959c12f21a06168567"
                "5ea6a183b723cb512007adfe2def74c3"
            ),
            "237963": (
                "18e17b59912d12e25e50da3b8198c641"
                "7048b4afb0d29fb46dabc6a06ca913e3"
            ),
        },
    },
    "mer000489t": {
        "summary": (
            "Seat-development project internship for industrial-engineering, "
            "business or related students. Requires negotiation-level German "
            "AND English, PowerPoint/Excel, technical/interior interest, strong"
            " organisation/communication, structured analysis and teamwork. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000489t",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238838": (
                "153c8ea5e337fd86f6096e6f78026f95"
                "b6bf4770c475860865177724537b1901"
            ),
            "238841": (
                "e9a86b9965a67f7d31f87d109ee379fd"
                "e5462b788d9ec621ea3dffae982b1f33"
            ),
            "238839": (
                "bb93b61467c652c06cc2a2243eaf11c8"
                "6caa6c217d425fec998cb48d7649a5b3"
            ),
        },
    },
    "mer00048ae": {
        "summary": (
            "ADAS AI/community student job for "
            "computing/business-IT/data-science/AI/engineering or related "
            "students. Requires initial ChatGPT/Claude/Copilot-type tool "
            "experience, basic software/data-analysis or automation "
            "understanding, very good German/English and strong "
            "communication/organisation with interest in community/event work. "
            "Listed benefits: coaching, health benefits, meal discounts. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ae",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241698": (
                "e5961c3e03fe97e3694d022dd5c9f773"
                "1473e980f8db37a9ed80757c5cb38da2"
            ),
            "241703": (
                "97c12b32f6d3b22ec9e30ed3e9e07d43"
                "473f67a9f24c96d70364c24553ed7d8d"
            ),
            "241699": (
                "7f569d6152d262b0923c54095a6fe0b9"
                "772472b2d6bc5709dce963b81b4cc398"
            ),
        },
    },
    "mer00048ag": {
        "summary": (
            "Laser-welding thesis for mechanical/production/laser/materials "
            "engineering, physics, chemistry or related technical/science "
            "students. Requires German/English, MS Office, scientific analysis "
            "and structured independent communication/teamwork. "
            "Laser-material-processing knowledge is preferred. Listed benefits:"
            " coaching, health benefits, meal discounts. Above-average grades "
            "and prior relevant internships; EEA ID/passport, otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ag",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240934": (
                "50dd39ee7c62bd1299f1b8cdaf713c04"
                "173db6b2d715998a15223d86bf3a0645"
            ),
            "240933": (
                "aba0fc894fcab666d87f15f7abd93f7c"
                "379ac5d962ee27f6a48cec059d0e9e32"
            ),
            "240932": (
                "0ff302e325ef74384fa71125942d1e6b"
                "7e54a11bad6d8ecda731038e4647f6a0"
            ),
        },
    },
    "mer00048ap": {
        "summary": (
            "Body-shell/concept development internship in Sindelfingen for "
            "automotive, mechanical engineering or related students. Requires "
            "German and English, MS Office, CAD, analytical and structured "
            "work. Submit CV, cover letter, certificates, current enrolment "
            "showing semester and standard study duration; mandatory-internship"
            " proof where applicable. Full-time temporary role; structured "
            "start 23 November 2026. Meal discounts and coaching listed; "
            "hybrid/flexible work and some employee benefits are conditional."
        ),
        "condition_evidence": [
            (
                "Eligible study fields are automotive engineering, mechanical "
                "engineering or comparable studies; no country-of-citizenship "
                "restriction is stated."
            ),
            (
                "German and English, MS Office and CAD knowledge are expected, "
                "with analytical/structured work, commitment and teamwork."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Sindelfingen, DE. Published schedule: "
                "Full time. Offering: Temporary. Structured advertised "
                "start(s): 2026-11-23. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241868": (
                "baf7258a1062636e7dff059204e9c05d"
                "21fd919eb7bdf5354d0ee9ff7e8ba3be"
            ),
            "241867": (
                "7868a0c9dee707fb8787b7de5e7a7471"
                "a44d9143c68bb97cf3d7aa31fadcc3cd"
            ),
            "241866": (
                "cf95ccc9cdcf4587c49d1381baedf1de"
                "33845574cae0e4c626696df85d598188"
            ),
        },
    },
    "mer00048bi": {
        "summary": (
            "Innovation/future-technologies internship for engineering, "
            "industrial-engineering, business-IT or related students. Requires "
            "German/English, Excel/PowerPoint, analytical/conceptual "
            "communication and teamwork. Initial practical experience and time "
            "abroad are advantages. Listed benefits: coaching, health benefits,"
            " meal discounts. Study proof; voluntary internships within regular"
            " study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048bi",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239535": (
                "c4c41a2ca21c8b68b6e06c8b4e386a32"
                "1b4b0ab57c4f5c95cce716c132ac5265"
            ),
            "239532": (
                "82797ac351c6e5853677465304ecf5f0"
                "99ee8b20b5bbfa6b2c0c7776cacfebae"
            ),
            "239531": (
                "e5d40015a0113de3b9853bc5c7452e86"
                "331dde421c29b468ca45800662a447ed"
            ),
        },
    },
    "mer00048by": {
        "summary": (
            "Connectivity/cloud executive-assistance internship for "
            "economics/business, IT, communications/media or related students. "
            "Requires very good German/English and Excel/PowerPoint, "
            "independent organisation, communication and teamwork. Initial "
            "AI-tool, data-analysis or automation experience is advantageous. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048by",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238348": (
                "2c775b077bc9c25ffb607e70ec9d9e03"
                "b88d63cd9a82bc0f05e31682a0881284"
            ),
            "238347": (
                "33595572365f50209d6c588543e6e09e"
                "e140f78747a238c74630862fa054ed02"
            ),
            "238346": (
                "8905246da250076921aa0340d1258483"
                "279bb564e655dc908446fb9595f01c45"
            ),
        },
    },
    "mer00048c3": {
        "summary": (
            "IT project-management internship in Böblingen for industrial "
            "engineering, business informatics, business, logistics or related "
            "students. Strong German/English, MS Office and project-management "
            "experience required; SAP skills advantageous. Five to six months "
            "at 35 hours/week, with flexible time; German is the main office "
            "language. Submit CV, cover letter, certificates, "
            "enrolment/semester and standard-duration evidence; "
            "compulsory-placement proof where applicable. Structured start 1 "
            "February 2027."
        ),
        "condition_evidence": [
            (
                "Study fields: industrial engineering, business informatics, "
                "business administration, logistics or comparable studies."
            ),
            (
                "Very good German/English and MS Office; SAP knowledge is "
                "advantageous. Project participation and IT/project-management "
                "experience, including relevant Waterfall/Scrum/Kanban/SAFe "
                "methods, are expected."
            ),
            (
                "International teamwork, independent responsibility, commitment"
                " and communication expected. Internship lasts five to six "
                "months, 35 hours weekly, with flexitime; office communication "
                "mainly German plus English in international projects."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Böblingen, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2027-02-01. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
            (
                "Hybrid work requires at least 2–4 onsite office days in "
                "Böblingen, with Tuesday and Thursday as fixed office days."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241919": (
                "cf1ea775e64ef38680fc709b0460dc5b"
                "de1b30249bdd32ca90ceff2bf461e6bc"
            ),
            "241918": (
                "f4317a51804f54cbe0897f10eca11bec"
                "8d5d64affd09ff7279bd9ed332b0b56d"
            ),
            "241917": (
                "02936b535310fec1c6ec12accc8492cd"
                "68bb3fdc4feb43ddac313d1c332582e7"
            ),
        },
    },
    "mer00048c8": {
        "summary": (
            "Steering-module student job for "
            "electrical/electronics/mechatronics/IT/computing/mechanical/au"
            "tomotive/industrial-engineering"
            " or related students. Requires German/English, MS Office, "
            "technical/analytical communication and teamwork. Programming and "
            "Jira/Confluence are desirable; CAD/technical-drawing and "
            "automotive-quality/development knowledge are advantages; AI "
            "experience is welcomed. Listed benefits: coaching, health "
            "benefits, meal discounts. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048c8",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238915": (
                "2c120e42cdfea66e2c9e0e81011ad35a"
                "ee10217c8820720722b2e35cd8c18512"
            ),
            "238914": (
                "7fad8a85532f68f19d426ba4dc1d84dd"
                "999d16d039aa5cb1ccbfb8eba9ab5593"
            ),
            "238913": (
                "2299d96ace291b474693f5a805e3a5eb"
                "fb69b6247accf3ecb010994f6a996c9d"
            ),
        },
    },
    "mer00048c9": {
        "summary": (
            "People Consulting internship for business, social-science, law or "
            "related students. Requires good English, MS Office, "
            "analytical/conceptual communication and proactive independent "
            "work. Initial HR/project-management experience is preferred. No "
            "German threshold is explicitly specified in these qualifications. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048c9",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240047": (
                "be5f97a746c94c7a4b40551a18f61530"
                "2b4a0d1e19fa6c4cbb3f171d47da9252"
            ),
            "240044": (
                "29e749e8e97f843de3dde58b4985ad15"
                "fbd07f9fe9341517e326e7a3078f2a10"
            ),
            "240043": (
                "d01f8fb678b3417aa50e91538502ee29"
                "3a5dca2ecfa662c5ce099b655820358f"
            ),
        },
    },
    "mer00048cb": {
        "summary": (
            "International HR internship requires non-U.S. university enrolment"
            " or a bachelor's/master's gap year, three semesters and at least "
            "six-month availability. Relevant business/HR study or equivalent "
            "education/experience, fluent English and MS Office are required. "
            "Preferred start January2027. USD 18/hour, visa costs and USD 700 "
            "housing bonus. Submit cover letter, CV, transcripts, school "
            "diploma, enrolment proof and references if available."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048cb",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239152": (
                "548a1c115d2d3a59c0646532141ce66c"
                "68f64f5728ce332e4d9e205328d67412"
            ),
            "239157": (
                "16b07e1b35892f75466db30d5bc6ed4a"
                "d1f74fd0ad24c426b8d17f450822f56c"
            ),
            "239155": (
                "0345665604dcbad170490dceec21a930"
                "bd11fb371e78ec725663f7581ffcdfea"
            ),
        },
    },
    "mer00048cc": {
        "summary": (
            "International organisational-development internship requires "
            "non-U.S. university study or a bachelor's/master's gap year, three"
            " semesters and at least six months. Relevant "
            "business/HR/psychology study or equivalent education/experience, "
            "fluent English, MS Office and project skills are required; "
            "German/HR internships are advantages. Preferred start "
            "February2027. USD 18/hour, visa costs and USD 700 housing bonus; "
            "study/application documents required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048cc",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239153": (
                "ac58656e2e33f8e16d6a0f5fe2ae6ded"
                "edb4253a7f7cb7d181f56bf2f5871886"
            ),
            "239156": (
                "8ee17e9a40e5a32018976a8ddbf99810"
                "344e1ebef9abd4ba8491f0bcde537d86"
            ),
            "239154": (
                "d102232e9be84f259eae10127f46844c"
                "d047ce9aec3b549a7e1d8e0289e95789"
            ),
        },
    },
    "mer00048ce": {
        "summary": (
            "International financial-transparency internship requires non-U.S. "
            "university study or a bachelor's/master's gap year, three "
            "semesters and at least six months. Supply-chain/finance/business "
            "study or equivalent education/experience plus strong Microsoft, "
            "SAP/AM Supply, analytics and Power BI are required. Preferred "
            "March2027 start; USD 18/hour, visa costs and USD 700 housing "
            "bonus. Cover letter, CV, transcripts, school diploma, references "
            "and enrolment proof required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ce",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239163": (
                "89bb2f29d8e8fd4993c893af7975eb34"
                "d44724c08b8e56bb5364e8db1d589994"
            ),
            "239162": (
                "8de16534ffb83960aff708c4ffdd9917"
                "1be7e48cc08030548eee40c63cfa14a6"
            ),
            "239161": (
                "349e419f344dcc2042a31f2b7b01df22"
                "61cbd33dc9cbf0bb5f7198183ccc4bb9"
            ),
        },
    },
    "mer00048cf": {
        "summary": (
            "Non-US university study or bachelor–master gap, three semesters "
            "and at least six months. Supply-chain/industrial-engineering/MIS "
            "study or equivalent background; fluent English, Office and "
            "analytical business-requirements skills. Familiarity with OR "
            "willingness to learn agile/waterfall/hybrid methods required; "
            "Power Platform preferred. Preferred April2027 start conflicts with"
            " structured2026-09-21; confirm timing. USD18/hour, visa costs and "
            "USD700 housing bonus. Application package includes CV, cover "
            "letter, school/university transcripts, work references and "
            "enrolment proof."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048cf",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239166": (
                "bc4bb7c0232f0277f233d81ab4292787"
                "d3fbed49aeab7408d23782c16bb6dbbf"
            ),
            "239165": (
                "0268d3aec72e36121994012113c88143"
                "0c0ab16cc5bb39207eb316c12380096c"
            ),
            "239164": (
                "50d2379c37fe8cc2ccbdee6cd858a14e"
                "09491e79a3364aba2347bb9c9928f0b9"
            ),
        },
    },
    "mer00048cg": {
        "summary": (
            "International yard-automation internship lists a relevant "
            "engineering/supply-chain/finance bachelor's degree OR equivalent "
            "education/experience AND non-U.S. enrolment/bachelor's-master's "
            "gap year, three semesters and at least six months. Requires Power "
            "BI, MS Office, data analysis, communication and intermodal-freight"
            " understanding. Preferred January2027 start; USD 18/hour, visa "
            "costs and USD 700 housing bonus. Required study/application "
            "documents; verify mixed degree/student wording."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048cg",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239202": (
                "64bc6b1dadb3f864a1a15567b5ef606d"
                "bf267a36c38b906d50f554b02f34ed94"
            ),
            "239201": (
                "2d8293498bcb893dced4d8e90a081f70"
                "04c5f0f2eda7173f0e5a6188f32ae512"
            ),
            "239200": (
                "2a0b42468745ef5520657d4e475d86e4"
                "8eb434fe15ecb09b04dbfb24a628215a"
            ),
        },
    },
    "mer00048ch": {
        "summary": (
            "International ramp-up-logistics internship requires non-U.S. study"
            " or a bachelor's/master's gap year, three semesters and at least "
            "six months. Logistics/business/supply-chain study OR equivalent "
            "education/experience, supply-chain and SAP experience and strong "
            "Excel/PowerPoint are required. Preferred March2027 start; USD "
            "18/hour, visa costs and USD 700 housing bonus. Cover letter, CV, "
            "university transcripts, school diploma, references and enrolment "
            "proof required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ch",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239184": (
                "3a9299c787d3b7ae3d630e0dd996d7ff"
                "6b9aaeaab232d04fb497157b8fd40624"
            ),
            "239183": (
                "ba7bbb6611d213fb05fd9865b14791eb"
                "6382ff9fad899287aa80727bc83ebc75"
            ),
            "239182": (
                "b90736b40d9711e13446f5da3152cea1"
                "d1d6bf50f4ecdeb6c0388fbed69469d8"
            ),
        },
    },
    "mer00048ci": {
        "summary": (
            "International supply-chain executive-support internship requires "
            "non-U.S. study or a bachelor's/master's gap year, three semesters "
            "and at least six months. Relevant "
            "supply-chain/industrial-engineering/business study OR equivalent "
            "education/experience, fluent English AND German, Excel/PowerPoint "
            "and analytical teamwork are required; logistics/Mercedes "
            "experience is advantageous. Preferred February2027 start; USD "
            "18/hour, visa costs and USD 700 housing bonus. Required "
            "study/application documents."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ci",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239181": (
                "00ec405bb419f37909023c04215961a2"
                "df5b64e63de617241ab594caad0d6e69"
            ),
            "239180": (
                "339a4a9df7ecf0759ac3951d09e14efa"
                "741169d77c4bfed7842b928e62f5b64b"
            ),
            "239179": (
                "df9485729969ac6a9cb89c8fa3375048"
                "25d16832c4ef5f4a17aea845c1c27f52"
            ),
        },
    },
    "mer00048ck": {
        "summary": (
            "International supply-chain project internship requires non-U.S. "
            "study or a bachelor's/master's gap year, three semesters and "
            "six-month availability. Relevant study OR equivalent "
            "education/experience, fluent English and Excel/Word/PowerPoint are"
            " required; Power BI/SAP are advantages. Preferred January2027 "
            "start; USD 18/hour, visa costs and USD 700 housing bonus. Submit "
            "cover letter, CV, university transcripts, school diploma, "
            "enrolment proof and references if available."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ck",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239175": (
                "ae550d5527c9d13641d1ceafd6b0eb85"
                "42001416300c80e9ec473fac64c5d821"
            ),
            "239174": (
                "58f628f18ee1ce0ea9c5cc258305b993"
                "8113092a3a036b87d96d5cc08a94e5b2"
            ),
            "239173": (
                "8fe3650050d653e6a436764d48c64b83"
                "03038c5bc3b52288a77c3f746b9a4b57"
            ),
        },
    },
    "mer00048cq": {
        "summary": (
            "Compulsory requirements-management internship for "
            "industrial-engineering, computing/business-IT, "
            "electrical/mechanical-engineering, business or related students. "
            "Requires negotiation-level German, confident English, "
            "PowerPoint/Excel and analytical independent communication. Six "
            "months is desirable; a mandatory placement component is required. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048cq",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239073": (
                "c992d7a3fdadee3daf7bdca0325c63e4"
                "012dbda8e82e3767b7183505d0bbdd38"
            ),
            "239077": (
                "8074fd5a796e545aaf015c711854b072"
                "66610648afe2d282021953fdcc2ae8c7"
            ),
            "239076": (
                "1c327191f9619eed77b8d5b16169b11b"
                "0d257bb63324a589d615d4f6f1476ef8"
            ),
        },
    },
    "mer00048dk": {
        "summary": (
            "Marketing/direct-sales internship for "
            "business/communications/media or related "
            "marketing/media-management students. Requires very good "
            "German/English, MS Office/web applications, extensive "
            "photography/video and layout/editing-tool experience, creativity "
            "and teamwork, and a category-B driving licence. Listed benefits: "
            "coaching, health benefits, meal discounts. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048dk",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238835": (
                "b3156783900fc6edcd2ff809a8fd2b14"
                "19661bd062a1da0a7ae2b47b882fcd2f"
            ),
            "238831": (
                "15a746909270d227abe5bc80aa6502d6"
                "e662d5264015d75754d1531aa92ec3da"
            ),
            "238830": (
                "89d1951c91f12bb5532dea5cf66722fa"
                "ce298802be6952d4ff01f40ba143f67e"
            ),
        },
    },
    "mer00048dm": {
        "summary": (
            "Customer-quality analytics internship for psychology, survey "
            "statistics, cognitive science, sociology, statistics, mathematics,"
            " economics or related students. Requires German/English, MS Office"
            " and programming/analytics-tool experience; Python/R, statistical "
            "or multivariate-analysis and customer/dealership experience are "
            "desirable or advantageous. Listed benefits: coaching, health "
            "benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048dm",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239605": (
                "8302f7700bb05fdfa011d2c2c391caa1"
                "bd5788e407f1d4505f6b59d32e80ffd2"
            ),
            "239604": (
                "f345e297ea398ba8736549aacad7b70e"
                "4b91ceebfcd94bc2ef322d3d173c8e01"
            ),
            "239607": (
                "0a84916f2f6fef89c06e2cb471cf35b4"
                "c092661c181fe01517daedfd209a34d5"
            ),
        },
    },
    "mer00048dq": {
        "summary": (
            "Thermal-comfort defect-management internship for "
            "mechanical/automotive/mechatronics/electrical/computing/indust"
            "rial-engineering"
            " or related students. Requires German/English, very good MS "
            "Office, analytical independent communication and learning "
            "interest. Jira/CANoe/CANape/MATLAB experience is advantageous; "
            "automotive experience and a category-B licence are desirable. "
            "Listed benefits: coaching, health benefits, meal discounts. Study "
            "proof; voluntary internships within regular study+3 semesters. EEA"
            " ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048dq",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238385": (
                "078eef593a183d5c6d00e9bbf3bf19d9"
                "b71230e28afc29b979849261810d5124"
            ),
            "238383": (
                "cab0cb617a9f53e55ffd7db86811c110"
                "73a9c5d5054f9a6b7ef132be8096ae78"
            ),
            "238382": (
                "dd0bbf483acc19592461f5fbd11a590c"
                "3798421dcdaa8ede6a3f6b7ce7b128d6"
            ),
        },
    },
    "mer00048dy": {
        "summary": (
            "Prototype-purchasing/build internship accepts current study OR "
            "completed higher HTL/HAK education. Requires MS Office/excellent "
            "Excel, communication/teamwork and structured independent "
            "willingness to learn. Six months at24-40hours/week from "
            "December1,2026. EUR 2,243 gross/month is stated for FULL-TIME "
            "work; part-time pay is not established. Listed benefits: coaching,"
            " health benefits, staff discounts may be available."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048dy",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238079": (
                "87b594aae79c54e1e3a13c950b13df7e"
                "2cb54d5e7a69b52e76cf85ee7686e2dc"
            ),
            "238078": (
                "1275e67f223d5d3398d6185906b35b28"
                "5b0d1a6e4d2f279eff97ac47c4b28a51"
            ),
            "238077": (
                "eff3005e8ae0f72784a2c78f1949fde2"
                "d79dc239036c7d7ba7be11cb73b92dee"
            ),
        },
    },
    "mer00048e1": {
        "summary": (
            "Prototype-build/workshop internship accepts current study OR "
            "completed higher HTL/HAK education. Requires MS Office/excellent "
            "Excel, communication/teamwork and structured independent "
            "willingness to learn. Six months at24-40hours/week from "
            "December1,2026. EUR 2,243 gross/month is the FULL-TIME reference; "
            "part-time pay is not established. Listed benefits: coaching, "
            "health benefits, staff discounts may be available."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048e1",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238088": (
                "19449f965a0a7bd7f2926f13f950ebce"
                "1ecbc19d2a58a01c3acb0867dcf3624f"
            ),
            "238087": (
                "0c7527c666cecbd6beb99ba0e7b4727a"
                "68832ad3af21794f1c277c38ada9b87c"
            ),
            "238086": (
                "58a400a1d853af88b81bb3d3939155c4"
                "0a878e2e19cf76b32e5b180d4713265e"
            ),
        },
    },
    "mer00048e3": {
        "summary": (
            "Compact-car electronics-integration internship for engineering or "
            "related students, preferably electrical engineering. Requires a "
            "category-B driving licence, German/English, MS Office, analytical "
            "teamwork and adaptability. Practical vehicle/mechatronics or "
            "similar experience is desirable. Listed benefits: coaching, health"
            " benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048e3",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238812": (
                "377247d4bccd123177ace01aa1796a74"
                "f29088e22cf7631e25b8af52347176a2"
            ),
            "238816": (
                "7207b63447c09e2ec43fedfbce3ce87b"
                "5a2a167e7cbad617357415640c3ed54f"
            ),
            "238814": (
                "9101a542dda2a0dc0ddea37267860cd8"
                "d85f3bfc9cc8b23b6201f7092e1476bb"
            ),
        },
    },
    "mer00048ea": {
        "summary": (
            "Powertrain integration/project student job for "
            "mechanical/mechatronics/electrical-engineering or related "
            "students. Requires good English, Microsoft tools, "
            "vehicle/hybrid-powertrain interest and organised independent "
            "teamwork. MATLAB/Simulink and AI-data-analysis "
            "experience/programming are desirable; no German threshold is "
            "explicitly stated. Listed benefits: coaching, health benefits, "
            "meal discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ea",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238655": (
                "3c26afe1bcd37bbeed3c7a2ea737b1c4"
                "0b2eee60955fffd2a4992dbfbc5e33f9"
            ),
            "238652": (
                "49e4ec5723149c2129ed4c6788a097d2"
                "73d958bf4b8b87c5dae43abb0eba59ce"
            ),
            "238651": (
                "dd9166fc12cfc49ac565707668c6d476"
                "2338a2812ea5f4c770ec14f08b9ee6e6"
            ),
        },
    },
    "mer00048ed": {
        "summary": (
            "Driver-assistance/base-software student job for computing/IT or "
            "related students. Requires practical Linux/Python/Bash/Git and "
            "local IP-network use/configuration, confident English, MS Office "
            "and analytical independent teamwork. No German threshold is "
            "explicitly specified. Listed benefits: coaching, health benefits, "
            "meal discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ed",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238405": (
                "b8a7ee6652006f66594a66b2879af45d"
                "770db508b9f36c9bf09a247de38c02a5"
            ),
            "238404": (
                "22df7ae7683af0d96cb1f4bc6b8e700b"
                "ff82f6e05af3dd00629b05a9c8fca179"
            ),
            "238411": (
                "cd0d2c20ca8646c27657a7851abac4dc"
                "14c9f959f1e0a7bf12b0ee61a45b7972"
            ),
        },
    },
    "mer00048ee": {
        "summary": (
            "Packaging-logistics internship requires active full-time Hungarian"
            " student status, logistics/business higher study, native "
            "Hungarian, working German and/or English, MS Office and at least "
            "six months at three days/week. A category-B licence is "
            "advantageous. Submit Hungarian and German/English CVs. "
            "Rule-dependent benefits include pay,100% public-transport support "
            "and role-dependent home working."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ee",
        ],
        "categories": ["internships"],
        "language": "hu",
        "variant_hashes": {
            "238109": (
                "5342ceb792f19a5592ccf5df42f2223e"
                "5298b102bbf59b8c92dba3871b8513fb"
            ),
            "238108": (
                "67ad0c09e313528416347b56100d26c6"
                "73510a2779efd4ee0c70f36e8c43c880"
            ),
            "238107": (
                "fef85092d40c52c2815e8bc80073832e"
                "6ddb2f2191f3e30abf3aa3fe006a778a"
            ),
        },
    },
    "mer00048ej": {
        "summary": (
            "Passive-safety thesis for mechanical/automotive-engineering or "
            "related students with German and English, MS Office, commitment, "
            "teamwork and analytical, strategic working skills. Submit "
            "enrolment and study-duration evidence. Listed benefits: coaching, "
            "health benefits, meal discounts. Above-average grades and prior "
            "relevant internships; EEA ID/passport, otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ej",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238639": (
                "9656b053fe385e1d8480ccb5d40f5149"
                "837326633d62f76ba42cf515fe77b8e5"
            ),
            "238638": (
                "c9c1e6d491fe435f38c44a4367b925b1"
                "2bbd134f9acda8b72e5593ad26676d38"
            ),
            "238636": (
                "1edab8625a52efeba966e5127a54d4d8"
                "d9ad186b05968a17d19e3ef3a899ed56"
            ),
        },
    },
    "mer00048eo": {
        "summary": (
            "High-voltage simulation student job for "
            "simulation/electrical-IT/mechanical/mechatronics/computing/ind"
            "ustrial-engineering"
            " or related students. Requires initial simulation experience and "
            "fundamentals, good MATLAB/Simulink-Simscape/StarCCM+, "
            "German/English, MS Office, scientific working methods and "
            "analytical teamwork. Listed benefits: coaching, health benefits, "
            "meal discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048eo",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239572": (
                "47aee23230402e2b7ca12c17caae2c63"
                "57f2da519de91308071386c52dffe80b"
            ),
            "239570": (
                "b5a165f91335a3e015ad16bb906549bb"
                "488afa3caa834548f4e0e4762a079e21"
            ),
            "239568": (
                "4cd58f280f3bf10742c7105128c331e8"
                "3ed1ed3c02b912c48e7bcfd58966fb2d"
            ),
        },
    },
    "mer00048ep": {
        "summary": (
            "Battery-EV high-voltage simulation internship for "
            "simulation/big-data/data-science/automotive/mechanical/mechatr"
            "onics/electrical/computing/industrial-engineering"
            " or related students. Requires German/English, MS Office, basic "
            "StarCCM+ and MATLAB Simulink and analytical teamwork. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ep",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239586": (
                "36c089db0f79f46a8e6ffe578d2b705e"
                "9c4c92a46f3e091e0195e2d6aa1153b6"
            ),
            "239584": (
                "578b150ed3eb0fb7ff25033258a63d2d"
                "8f0349df100c4ce62781d576b0a447d8"
            ),
            "239582": (
                "a9607333706da7c6072931db21e73e7f"
                "b568a13a72cb85e45a17d96d927f6343"
            ),
        },
    },
    "mer00048es": {
        "summary": (
            "International programme-planning internship requires non-U.S. "
            "study or a bachelor's/master's gap year, three semesters and at "
            "least six months. Relevant "
            "supply-chain/finance/mechanical/industrial-engineering/informa"
            "tion-management/business"
            " study OR equivalent education/experience, fluent English and "
            "strong analytical communication are required. Preferred "
            "January2027 start; USD 18/hour, visa costs and USD 700 housing "
            "bonus. Cover letter, CV, transcripts, school diploma, references "
            "and enrolment proof required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048es",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239190": (
                "d6605a783637575c60c039fac74052a3"
                "7c8916a87e4998defe1e97144aaa2027"
            ),
            "239189": (
                "ff555754c11f9dae1b8896d963d067f3"
                "f62414776ae23a7ffb72fc7fcd12df1a"
            ),
            "239188": (
                "05517cdaecc57386b99b9526dc61366a"
                "181fe330416d6a33325b6c0714f9b652"
            ),
        },
    },
    "mer00048et": {
        "summary": (
            "International electronics-engineering internship requires non-U.S."
            " study or a bachelor's/master's gap year, three semesters and at "
            "least six months. Electrical-engineering/related study OR "
            "equivalent education/experience, fluent English, MS Office, "
            "vehicle-network/bus knowledge and production experience are "
            "required. Preferred January2027 start; USD 18/hour, visa costs and"
            " USD 700 housing stipend. Study/application documents and "
            "references if available are required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048et",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239160": (
                "91f6fbb2dc378c05d09c60a7123a7b38"
                "8c0bf815beac6fb12539e874aa569f19"
            ),
            "239159": (
                "5b4b033ca0ea154b13fa8bcfb66995af"
                "880deccb08dbae5400ddd1a4b3121c6d"
            ),
            "239158": (
                "8c9a89be7f177266ad47a03b8a8f494a"
                "92b8dd39fcf137e306ef2bb4f9238f72"
            ),
        },
    },
    "mer00048eu": {
        "summary": (
            "International quality-market-analysis internship requires non-U.S."
            " study or a bachelor's/master's gap year, three semesters and at "
            "least six months. "
            "Mechanical/electrical-engineering/business/related study OR "
            "equivalent education/experience, fluent English and MS Office are "
            "required; KPI reporting, process engineering/mining and "
            "collaboration are preferred. Preferred January2027 start; USD "
            "18/hour, visa costs and USD 700 MONTHLY housing stipend. Required "
            "study/application documents."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048eu",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239213": (
                "b661ab25363acb16750aa337b0aeaef8"
                "93c884f785bce7a439ce642a5f78efdd"
            ),
            "239212": (
                "066c5a2d9334d70657d18a6d4ce07071"
                "d01c740230fbb7f85f77d15a36230651"
            ),
            "239210": (
                "8aa1f4be63a128b3c84050451dbdc41b"
                "a95ecc4c29f78abe48dd59900460e84d"
            ),
        },
    },
    "mer00048ev": {
        "summary": (
            "International quality-audit internship requires non-U.S. study or "
            "a bachelor's/master's gap year, three semesters and at least six "
            "months. Mechanical/industrial/electrical/computer-engineering or "
            "related study OR equivalent education/experience, strong "
            "analysis/time-management, extensive MS Office and flexible working"
            " times are required. Preferred March2027 start; USD 18/hour, visa "
            "costs and USD 700 housing stipend. Cover letter, CV, transcripts, "
            "school diploma, references and enrolment proof required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ev",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239217": (
                "5a16931c2c5126aa2be60e605ea8160e"
                "95f7e3fe813080dec10a0af9ab8b1e05"
            ),
            "239216": (
                "89aebd1b2de574e1b991f5b6d70b1d45"
                "362f58da318980b99e410cfd3c9a8f79"
            ),
            "239215": (
                "11f63dcd7924b29615d69e1159013470"
                "970fe446370505434966dd9aaa2cf53e"
            ),
        },
    },
    "mer00048ew": {
        "summary": (
            "International chief-engineer-support internship requires non-U.S. "
            "study or a bachelor's/master's gap year, three semesters and at "
            "least six months. Relevant "
            "mechanical/industrial/electrical-engineering study OR equivalent "
            "education/experience, MS Office, data-analysis/problem-solving and"
            " CAD are required; German and Mercedes application experience are "
            "preferred. Preferred February2027 start; USD 18/hour, visa costs "
            "and USD 700 housing bonus. Study/application documents and "
            "references if available required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ew",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239199": (
                "d7526d2eeb1229b4b51bb9166975364e"
                "b48e6897334b01bccf204d71257cf1d8"
            ),
            "239198": (
                "8a8d18cbdc4faa78fbdac8567433a36e"
                "f4a4005a37742139cdceb88db769b8d6"
            ),
            "239197": (
                "7ec0954c68ae3302cdb08e34017c8e69"
                "a8714a9a2e436030d67234a53c17b402"
            ),
        },
    },
    "mer00048ex": {
        "summary": (
            "International body-in-white internship requires non-U.S. study or "
            "a bachelor's/master's gap year, three semesters and at least six "
            "months. Mechanical/industrial-engineering or related study OR "
            "equivalent education/experience plus Excel/PowerPoint/OneNote, "
            "CAD/SolidWorks/Siemens NX, Java and basic AI/neural-network "
            "knowledge are required. February2027 start; USD 18/hour, visa "
            "costs and USD 700 housing bonus. Cover letter, CV, working "
            "references and enrolment proof required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ex",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239172": (
                "3b72040c3db43eefacbd7fb8faae3d7b"
                "64c2c949bac1c88eca6997a0421893d7"
            ),
            "239171": (
                "27765e91a1062ba8f5b0b0453958f970"
                "4944bb8d8532eb91eda1b0d6e5b8b1a4"
            ),
            "239170": (
                "0df72b46a014614ebbfc05cebe3985ba"
                "39ef7f962a58ea5b1d44959a8e4f05a1"
            ),
        },
    },
    "mer00048ey": {
        "summary": (
            "International exterior/interior-engineering internship requires "
            "non-U.S. study or a bachelor's/master's gap year, three semesters "
            "and at least six months. Relevant "
            "mechanical/electrical/industrial-engineering study OR equivalent "
            "education/experience, fluent English and MS Office are required; "
            "critical thinking, organisation and automotive-manufacturing "
            "experience are preferred. Preferred March2027 start; USD 18/hour, "
            "visa costs and USD 700 MONTHLY housing stipend. Required "
            "study/application documents."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ey",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239205": (
                "7b2ba994599e7403bcd43796074e3a57"
                "564e0b44049c90414e246a07d205fcb6"
            ),
            "239208": (
                "a22c371ebb9aac16baa160f182c0e41e"
                "ec99d8ef27a92c51346222eadffc7518"
            ),
            "239207": (
                "0215dd492b082053e4417918d4af2967"
                "d58924448e03b5a1d49314cc7b3cc307"
            ),
        },
    },
    "mer00048f0": {
        "summary": (
            "Engine-calibration student job requires enrolment in "
            "automotive/mechatronics or related study, "
            "combustion-engine/whole-vehicle understanding, good German/English"
            " and a valid category-B driving licence. Initial "
            "calibration/simulation/evaluation-software experience is "
            "desirable; independent initiative and flexibility are expected. "
            "Listed benefits: coaching, health benefits, meal discounts. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048f0",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238579": (
                "bc7f4ace123a937eb2b4cd6225cacb5b"
                "06fa3dbc241f39b506d32080bfbe5e3b"
            ),
            "238578": (
                "8efb49b875bc3c594b8a16b0eb5ffd4e"
                "10db5c79e1493fabbb23fb0add575caa"
            ),
            "238576": (
                "0ce62493648bbd4f73917d4e0188693b"
                "e8241b5ce563bbbf5c3c691c32365638"
            ),
        },
    },
    "mer00048f2": {
        "summary": (
            "Procurement analytics/AI internship for technical, mathematics, "
            "business or related students. Requires negotiation-level "
            "German/English and initial practical process-analysis/optimisation"
            " OR software-development experience, including "
            "digitalisation-project implementation, plus analytical "
            "organisation/teamwork. Python/R and Azure Data "
            "Factory/Fabric/Databricks experience are advantages. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048f2",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239786": (
                "02c8e1833735606ff8328c2f88939f8b"
                "1a3405bfc96aab2a41f01131c3bc9244"
            ),
            "239783": (
                "a6f620bc9a305d7447d98973f9024b4c"
                "d7aee94c8bdcaf71a0ec395ca40484dd"
            ),
            "239781": (
                "ba38053b3f3436c5379afbb2646afe19"
                "5cc12237c8e1a6a95728521b5489982a"
            ),
        },
    },
    "mer00048f3": {
        "summary": (
            "Charging-validation/data-analytics internship for "
            "computing/data-science/business-IT/electrical/automotive/mecha"
            "tronics/industrial-engineering"
            " or related technical students. Requires German/English, "
            "Excel/PowerPoint, analytical organisation and teamwork. Initial "
            "programming/data-analysis/vehicle/testing experience is "
            "advantageous; Python/AI/diagnostics knowledge is desirable. Listed"
            " benefits: coaching, health benefits, meal discounts. Study proof;"
            " voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048f3",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239905": (
                "283f1c24e22fb0a7ee4d1c4e23f6b55b"
                "6a12400fd917f6938ce7321a08253bb0"
            ),
            "239903": (
                "a01b83bfe2562ad766df84c10487d904"
                "3c590433f6f3683daf71c5eb13c680b5"
            ),
            "239901": (
                "5616658258e40f847ce2eb6ad52d269d"
                "3806f175c693385d4a09a15bcabd6cd2"
            ),
        },
    },
    "mer00048f5": {
        "summary": (
            "International-logistics internship for "
            "business/industrial-engineering or related students, ideally "
            "logistics-focused. Requires German/English, MS Office, independent"
            " analytical organisation and international communication. "
            "Teams/Power Apps knowledge and initial logistics/supply-chain "
            "experience are preferred. Listed benefits: coaching, health "
            "benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048f5",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240439": (
                "4d60530dc95a7566388830d147594f53"
                "d76091892265227cbf2ecd555ba90c60"
            ),
            "240438": (
                "906cd24e44924f5f8e03b51daad17ff1"
                "1147017b0a2062d7b25d335746ac4b51"
            ),
            "240437": (
                "bb1bd68c2e2b292fc0ef8014c8ef34e6"
                "5bd896b5f09fed3417904abaf4af67c9"
            ),
        },
    },
    "mer00048fo": {
        "summary": (
            "Compulsory driving/media-events internship for communications, "
            "business, event-management or related students. Requires "
            "negotiation-level German/English and creative strategic work. MS "
            "Office/graphics-tool experience, category-B licence, flexible "
            "hours and willingness to travel are desirable. A mandatory "
            "placement component is required. Listed benefits: coaching, health"
            " benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048fo",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239072": (
                "a2818042ccf1bba6860b937c3af516f2"
                "75fad3d33e1e3485f32d67ed6fe9ddf0"
            ),
            "239070": (
                "f2b70869abeca75da5a638cbb3fbad39"
                "d9b5e90982e9eafe6b17cd0d89e1c162"
            ),
            "239067": (
                "ca6dbef4f75545cc36941f9e2d9c8189"
                "8d9675fe249e3501f2d2a531992bdc9d"
            ),
        },
    },
    "mer00048fq": {
        "summary": (
            "Bachelor thesis on a control model for "
            "business/industrial-engineering or related students. Requires "
            "German and English, MS Office, analytical/strategic thinking, "
            "commitment and teamwork. Submit supporting enrolment and "
            "study-duration evidence. Listed benefits: coaching, health "
            "benefits, meal discounts. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048fq",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238677": (
                "54dc780eae0f24644a58af9fb11a2425"
                "9611563737873ed724170244e3d661a2"
            ),
            "238676": (
                "444ca00f531573d177a81b8354f6642a"
                "fadf4e22cbb8a559888d764fe03e98aa"
            ),
            "238674": (
                "d7aef1cf6f3ead1cfad9e52f11902c95"
                "f1ecfdb3771bf9c3bfaa4cda5b071342"
            ),
        },
    },
    "mer00048ft": {
        "summary": (
            "Workplace/project internship for "
            "business/industrial-engineering/property/civil-engineering/arc"
            "hitecture/marketing"
            " or related students. Requires very good German/English and "
            "PowerPoint, MS Office, structured independent communication and "
            "interest in workplace/facility/property strategy. Initial "
            "design/communication/visualisation experience is preferred. Listed"
            " benefits: coaching, health benefits, meal discounts. Study proof;"
            " voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ft",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238632": (
                "a6d9d721b02851bef1ad60245f13c550"
                "57bf1754eb71f3fa4a231eb110fc14b0"
            ),
            "238631": (
                "ee348f8f23ee9b2e22c6c2139a94aa92"
                "471cb0ef0648c8f0e665f9b75fd05bed"
            ),
            "238630": (
                "e74c784475333359c0af76d4bd930e33"
                "95e2f4f2cb53a70082bdfbfaf5c23a19"
            ),
        },
    },
    "mer00048fu": {
        "summary": (
            "Module-assembly production-planning internship for "
            "mechanical/industrial/production-engineering or related students. "
            "Requires German and English, MS Office, commitment, analytical "
            "structured work and strong communication/teamwork. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048fu",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238778": (
                "5be406dc56188cb4c50e9921dddf7fa1"
                "86bc1900e384a81c83c4e1fa4474f398"
            ),
            "238777": (
                "fa1a03a3a79e3854c813732ffbcd1da5"
                "166482b71db166971588de0be0420948"
            ),
            "238776": (
                "2409f7a8439deeec4a91ce9bcbe0e28f"
                "3886bc126692829a6fd0516c53137f36"
            ),
        },
    },
    "mer00048fx": {
        "summary": (
            "Compulsory van-retail internship for economics/business or related"
            " students. Requires negotiation-level German/English and MS "
            "Office; other European languages and initial sales/project "
            "experience are advantages. Mandatory placement component required;"
            " may start March2027. Described benefits include paid work, "
            "flexible/mobile-working options, subsidised meals and supervised "
            "onboarding. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048fx",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239277": (
                "844b5942473fb320d45027cc332ef288"
                "564e610e0d6f99cba6bb2eef6f694102"
            ),
            "239276": (
                "3cae8839c46c4ec650962c61f9b8212e"
                "6a2d7d4633f10848c7901a10e7d55e39"
            ),
            "239272": (
                "b21a413a8bbc636249f2c6c29f48db8f"
                "afa88da6ba1f16ce8aed3e0bfb86da97"
            ),
        },
    },
    "mer00048g0": {
        "summary": (
            "R&D-services procurement internship in Stuttgart for business, "
            "engineering or IT students. Requires basic automotive knowledge, "
            "C1/C2 German, MS Office, communication and analytical skills. Six "
            "months; the internship must include a compulsory component. Submit"
            " CV, cover letter, certificates and current enrolment showing "
            "semester, standard study duration and applicable "
            "compulsory-placement proof. Full-time temporary role; structured "
            "start 1 December 2026. Coaching and conditional hybrid work "
            "listed."
        ),
        "condition_evidence": [
            (
                "Study in business/economics, engineering or IT; basic "
                "automotive technical knowledge and C1/C2 German required."
            ),
            (
                "Expected skills: MS Office, communication/confident "
                "presentation, independent work, numerical interpretation, "
                "flexible social/team skills and analytical/strategic thinking."
            ),
            (
                "The proposed duration is six months. This location requires "
                "the placement to contain a prescribed compulsory-internship "
                "component."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: coaching, hybrid work may "
                "be available, employee discounts may be available, health "
                "support, mobility offers, employee phone may be available, "
                "staff events, discounted meals, annual profit share may be "
                "available, occupational retirement support, flexible hours may"
                " be available, continuing learning, parking, workplace doctor,"
                " public-transport access, accessible workplace, nearby "
                "childcare, canteen/cafe. Labels do not separately establish "
                "payment amounts or individual eligibility; conditional offers "
                "are not guaranteed."
            ),
            (
                "Official location(s): Stuttgart, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2026-12-01. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241938": (
                "4822d010e54576c03618c887845d7fb6"
                "7e6b570e297ebe3e76b4af73a4547354"
            ),
            "241936": (
                "95e63647584977d580fa2870b9b56a8a"
                "1300b537c00492a81ace51dfb73152c6"
            ),
            "241939": (
                "8cbd417f1ca25405aee5725e8e9f34b7"
                "1627a688612bdee5cf744e73a54917a7"
            ),
        },
    },
    "mer00048g2": {
        "summary": (
            "Compulsory BI-platform internship for "
            "business/business-IT/industrial-engineering/computing/internat"
            "ional-business"
            " or mathematics/science students. Requires confident English, "
            "Excel/Outlook/PowerPoint, strong presentation/organisation and "
            "independent analytical teamwork; German, Jira/Confluence, "
            "SQL/Python and Databricks are advantages. A mandatory placement "
            "component is required. Listed benefits: coaching, health benefits,"
            " meal discounts. Study proof; voluntary internships within regular"
            " study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048g2",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238594": (
                "b263ad5455e90af63e5bdffe94f6bb50"
                "79307a3d57b8eb76acebf346a504ba43"
            ),
            "238599": (
                "4c2ea4710d8656d668cde4abc9edb564"
                "42f1c679d749c90e46df151474f83fc2"
            ),
            "238598": (
                "47ddb2ecef6aa82d19c771f9cdbebb96"
                "0a62058a8c7768a0c8ab9c9c35068d13"
            ),
        },
    },
    "mer00048g5": {
        "summary": (
            "Compulsory key-account internship for business/business-IT or "
            "related students. Requires German/English, PowerPoint/Excel, "
            "audience-focused presentation, independent analytical organisation"
            " and teamwork. A mandatory placement component is required. Listed"
            " benefits: coaching, health benefits, meal discounts. Study proof;"
            " voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048g5",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241156": (
                "286b0caa30ba0ab463b4564c5e15bc09"
                "8e32be30211e15c55b610b303f137df5"
            ),
            "241153": (
                "12e0bee31ca5230c3279680c09b559d0"
                "11cbb96b0f5230a444edb1604fe757bc"
            ),
            "241152": (
                "0775cb118a6cea4c9fe416c874e00ba5"
                "510b1f27f26b4a86f47b9d4b74b2d62f"
            ),
        },
    },
    "mer00048g6": {
        "summary": (
            "Compulsory battery-cell-development internship for "
            "mechanical/mechatronics/electrical-engineering or related "
            "technical students. Requires German/English, MS Office, basic CAD,"
            " technical/battery-production interest and independent careful "
            "teamwork. Technical vocational training is advantageous; initial "
            "project-management experience desirable. A mandatory component is "
            "required. Listed benefits: coaching, health benefits, meal "
            "discounts. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048g6",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239816": (
                "588a99d88d9ba72d0dd39d051f795a4b"
                "a9d0129c658ff3c71be13851f41088bd"
            ),
            "239815": (
                "b9589cba7f3f3a0793f506b618d94a71"
                "c6dbea17b1309b1683aacd21691d689e"
            ),
            "239814": (
                "c40e9e9bec8fbc41e10512901db77b7a"
                "6736467774a013484e672e3da865ee54"
            ),
        },
    },
    "mer00048gf": {
        "summary": (
            "International productivity internship requires non-U.S. study or a"
            " bachelor's/master's gap year, three semesters and six-month "
            "availability. Finance/business/industrial-engineering or related "
            "study OR equivalent education/experience, confident English AND "
            "German, project-management, Excel/PowerPoint/Power BI and "
            "analytical communication are required. Preferred January2027 "
            "start; USD 18/hour, visa costs and USD 700 housing bonus. "
            "Study/application documents and references if available required."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048gf",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239178": (
                "b54590ef8117cd270f91c0e0851b9fd4"
                "aed8a8012bf5027528efbb7abc7305fa"
            ),
            "239177": (
                "7278c4e49f519d701a0175cc7eb8b800"
                "cba8172f194dc22a417362346f2f6022"
            ),
            "239176": (
                "6f1737c6df1eb5cbbfa2b80679fc8605"
                "ede406a1a0e78896e27610c28196a6e9"
            ),
        },
    },
    "mer00048gg": {
        "summary": (
            "International executive-office internship lists a relevant "
            "mechanical/industrial-engineering/business bachelor's degree OR "
            "equivalent education/experience AND non-U.S. "
            "study/bachelor's-master's gap year, three semesters and at least "
            "six months. Requires fluent English, MS Office, discretion and "
            "project/organisation skills; German preferred. Preferred March2027"
            " start; USD 18/hour, visa costs and USD 700 housing bonus. "
            "Required study/application documents; verify mixed degree/student "
            "wording."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048gg",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239196": (
                "e4a61ffb8836014c9a3e3336f682d250"
                "5a8e917154fbe5a54cff8b32f05959c5"
            ),
            "239195": (
                "84321506aae7b9666d40104ce7c190fc"
                "ed9ff73be6b8d76ea95402fc1ebeed32"
            ),
            "239194": (
                "80986572cd481f930da1a7c9c09b55df"
                "e43c1cb2d6fc4e3a595198f0f9f30198"
            ),
        },
    },
    "mer00048gr": {
        "summary": (
            "IT-quality student job for economics/business, engineering or "
            "related strongly IT-focused students. Requires German/English, "
            "very good Word/Excel/PowerPoint, AI prompting/agent skills and "
            "independent analytical teamwork. Data-analysis tools such as Power"
            " BI may be used; application-development/testing knowledge is "
            "desirable. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048gr",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240299": (
                "d2efb92216e42beae581580cf058eda1"
                "f4e28eb10a710001f3de6f0595034cd4"
            ),
            "240296": (
                "93d5c78df87e7f3f831966f2111eb310"
                "bf456bf94b7f9482091ccf8b71616322"
            ),
            "240295": (
                "2972fe9fbdb3d0d54ec7ddd9230d0103"
                "334ed1632652c11eba8c1f69deeb0d97"
            ),
        },
    },
    "mer00048h3": {
        "summary": (
            "Battery-EV development-project student job for "
            "mechanical/industrial-engineering or related students. Requires "
            "German and English, MS Office, confident use of common AI tools "
            "and analytical, strategic teamwork. Listed benefits: coaching, "
            "health benefits, meal discounts. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048h3",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239736": (
                "2b06d4574eb1ee0e78adb3bab37343b3"
                "30dbf001a885045dbb14df002edd4ae1"
            ),
            "239731": (
                "18284ab5d7ffdcdf16ff454e59564f58"
                "313ca4b022bd607ec96cc117b571e4ba"
            ),
            "239730": (
                "501dc3127b2ca21a0dcdf0ccc8ca9c74"
                "ffa40b96d4ff8e95dd7cd300bd1bb625"
            ),
        },
    },
    "mer00048h7": {
        "summary": (
            "Plug-in-hybrid project-support student job requires current "
            "engineering, economics/business or related technical/business "
            "study, German/English, PowerPoint/Excel/Word/Teams and strong "
            "communication across organisational levels, proactive reliable "
            "teamwork and commitment. Listed benefits: coaching, health "
            "benefits, meal discounts. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048h7",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238684": (
                "a1cbf4c30ac5df9e9c96273f7cc8881f"
                "fcdb252cc055236a14649523b8ad909f"
            ),
            "238689": (
                "e7588452ba0a538fb46bada92bb958ba"
                "8ec05f23c7ab15a38d0fc30f59971fd0"
            ),
            "238688": (
                "24fea96a9c8e43fb8b5d157ceb97b3c4"
                "cef65fd264a5128fa4bdb01dca58ecd6"
            ),
        },
    },
    "mer00048h8": {
        "summary": (
            "Plug-in-hybrid vehicle-management internship for "
            "industrial-engineering, mechanical/mechatronics or related "
            "students. Requires German/English, Word/Excel/PowerPoint and "
            "reliable flexible teamwork. A category-B driving licence is "
            "advantageous, not mandatory. Listed benefits: coaching, health "
            "benefits, meal discounts. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048h8",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238704": (
                "0ff4c42695ce7449c861e73d7aba28de"
                "433175486609aab178c0f67d5b488da3"
            ),
            "238702": (
                "cb9e4eb08d688ebe38a4aa5483522c37"
                "9539003a2e361bff98f996b9a413e061"
            ),
            "238700": (
                "11ef8a0b59deef0385e6be8cbfc9dd3c"
                "bf735b42242096f74ba0ced17b04d195"
            ),
        },
    },
    "mer00048hh": {
        "summary": (
            "Museum-events internship requires completed preliminary/basic "
            "university study, very good English, MS Office, customer-oriented "
            "communication, organisation and independent teamwork. "
            "Event-organisation experience is preferred. Six months from "
            "February1,2027 at40hours/week; submit motivation letter, CV and "
            "certificates. Listed benefits: on-site doctor, parking, "
            "public-transport access."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048hh",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238357": (
                "c817b6c95918bce7d2d097476cabc1b1"
                "8c9291284fa444797b703138b3e02b28"
            ),
            "238355": (
                "e7007225d1bcd855503ed4dabf7c6962"
                "4e9f079e7a0cd260ab8762f62da5b4df"
            ),
            "238352": (
                "a8fbd113e2e0b9aae7a7895fad8d0075"
                "e80f3e6d6a4d563529220a6c5f071070"
            ),
        },
    },
    "mer00048hj": {
        "summary": (
            "Cybersecurity/SIEM student job for "
            "computing/business-IT/cybersecurity/data-science or related "
            "students. Requires German/English, initial AI-tool knowledge, "
            "basic Windows/Linux/MS Office and structured analytical "
            "reliability. REST/JSON/regex, n8n, cloud-security interests and "
            "initial Python/Bash scripting are preferred extras. Listed "
            "benefits: company pension, hybrid work may be available, flexitime"
            " may be available. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048hj",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238711": (
                "5aedea8e6d69f090afe5a9100da469cb"
                "4f110671d954721c0a1a0524da78d913"
            ),
            "238710": (
                "1a3961dbbc4d1cffd70dcb6b4cfb3606"
                "32beafe1143446e1007afc2e41f09764"
            ),
            "238707": (
                "427a016cf027328104332b540f9973fa"
                "9f8f0d460a0e095ad632a15bca52fd60"
            ),
        },
    },
    "mer00048hk": {
        "summary": (
            "Compulsory digital-customer-experience internship requires current"
            " economics/business-IT/computing/digital-business/business or "
            "related study, good German/English and PowerPoint/Excel. Initial "
            "agile/Jira/Confluence exposure is preferred; "
            "digital-product/web/analytics experience is advantageous and "
            "explicitly NOT required. University proof of the mandatory "
            "placement must accompany the application. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit. Coaching listed."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048hk",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240042": (
                "c6123d6c2861f54780973d7feab6a27f"
                "b4949ca07cdb4f26c5382832b0f7f7be"
            ),
            "240039": (
                "e3fa79e4d9986b1bf254e443fc97303a"
                "ddda8f09d2ec24e61036d6db27e27959"
            ),
            "240037": (
                "b6143813596d1fd1f24ac7f7fa5fe3bc"
                "e35e6ead9f3c1fb0ee47214c4ebf43fd"
            ),
        },
    },
    "mer00048hs": {
        "summary": (
            "Compulsory HR internship for economics/business, law, "
            "social-science or related HR-focused students. Requires "
            "German/English, Outlook/Word/Excel/PowerPoint, organised "
            "independent communication and teamwork. Initial HR experience is "
            "preferred; a mandatory placement component is required. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048hs",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240632": (
                "81deae3d20abc30e1bd38b81ffe028ad"
                "f5c604c0fda66e85493576ecfd452eaf"
            ),
            "240630": (
                "a4dae492f95fd9afd655800acad20ab8"
                "9a6afb302578098d8f1b9293b003dae8"
            ),
            "240627": (
                "19261ff5451767f953611e71ad7eaa28"
                "077d43cd9aee39fe7f95625d3a73bdb1"
            ),
        },
    },
    "mer00048hu": {
        "summary": (
            "HR-project/digital-communications internship for "
            "business/economics/communications/business-psychology or related "
            "students. Requires German/English, Outlook/Teams/PowerPoint/Excel,"
            " structured communication/teamwork and initial practical "
            "experience from an internship, student job or similar work. Listed"
            " benefits: coaching, health benefits, meal discounts. Study proof;"
            " voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048hu",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238955": (
                "cb8b5d6ac9c166e4f08e494899acdc7b"
                "c7eb814c7b5ee43c3dae0265274bf1b1"
            ),
            "238953": (
                "5077989a83518c90691860cd6c0ce953"
                "105a65cbe5374b9fe8d723176b8fa290"
            ),
            "238951": (
                "1fff3ffd2149263f4091eca43b6423ff"
                "b5bb9f9365a0846cb635872282a4dd58"
            ),
        },
    },
    "mer00048i2": {
        "summary": (
            "Workplace/office-management student job for facility-management, "
            "business, event-management or related students. Requires a "
            "category-B driving licence, German and English, MS Office, "
            "efficient independent organisation and cross-departmental "
            "teamwork. Listed benefits: coaching, health benefits, meal "
            "discounts. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048i2",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239794": (
                "fac351926eedd0916ddd1fac66cd9d05"
                "fea9c816c77a883b1844134b063c7a19"
            ),
            "239793": (
                "ac766d62353b4d4b2bf0263263194963"
                "39857d1044abf3767d90ed410ff33ea9"
            ),
            "239789": (
                "af71b857f59efc4bddcfd5638427b981"
                "f5aa1e353c95f94fe144208b7ed72b63"
            ),
        },
    },
    "mer00048i3": {
        "summary": (
            "Construction/infrastructure student job for civil-engineering, "
            "architecture, industrial-engineering, "
            "facility/infrastructure/project-management or related students. "
            "Requires very good German, good English, PowerPoint/Excel/Outlook "
            "and structured analytical organisation and communication/teamwork."
            " Listed benefits: coaching, health benefits, meal discounts. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048i3",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238910": (
                "bc83387de13d92e6d2d568ccec2fd492"
                "181faebb4cdfacb2364452f24747269e"
            ),
            "238907": (
                "2a2d8292bd71d400f32f5722b9922a3f"
                "5c4a2582fb422a6b5def0c350bf5a58b"
            ),
            "238904": (
                "1595bd5f17484c389d60790ea4637f42"
                "590072649077ab4d2e30c6254ff2a35b"
            ),
        },
    },
    "mer00048i4": {
        "summary": (
            "Master’s-thesis role in seat-development project "
            "management/digitalisation, Sindelfingen, for industrial "
            "engineering, business or related students. Requires technical "
            "understanding, organisation, communication, MS Office and fluent "
            "German/English. Initial data-analysis, BI or AI experience "
            "advantageous. Submit CV, cover letter, certificates, current "
            "enrolment showing semester and standard study duration. Full-time "
            "temporary role; API start 16 September 2026 is an advertised "
            "start, not a closing date."
        ),
        "condition_evidence": [
            (
                "Master’s-thesis position: industrial engineering, business "
                "administration or similar study. Interests include project "
                "management, process improvement, digitalisation and vehicle "
                "interiors."
            ),
            (
                "Requires organisation, communication, technical understanding,"
                " analytical/structured and independent teamwork, professional "
                "presentation, and negotiation-level German/English."
            ),
            (
                "MS Office including Excel/PowerPoint required; initial "
                "experience with data analysis, business intelligence or AI is "
                "advantageous. Interest in data-driven methods and AI expected."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Mark documents as relevant for this "
                "application; each attachment is subject to the stated 5 MB "
                "limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Sindelfingen, DE. Published schedule: "
                "Full time. Offering: Temporary. Structured advertised "
                "start(s): 2026-09-16. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "241931": (
                "e01eea5adf4aaeb0113caba2e7b31948"
                "257fe4d271d117b14f57afe66ee79b23"
            ),
            "241929": (
                "06e838d527e8acbef6167b98cec2a4bc"
                "bc035c4c9aee23df666e5813313b41fd"
            ),
            "241927": (
                "cde53d5feba7935731c042a3cdbc6aec"
                "98dd3fc228ed2e84487c4b8ccf685237"
            ),
        },
    },
    "mer00048i5": {
        "summary": (
            "Bachelor thesis on perforated seat coverings seeks "
            "materials-development, chemistry, textile-technology or related "
            "study, very good German/good English, Excel/PowerPoint, basic "
            "data-analysis/documentation and careful scientific teamwork. "
            "Polymer/synthetic-leather/textile/composite knowledge and "
            "statistics experience are advantages; prior field experience is "
            "preferred. Listed benefits: coaching, health benefits, meal "
            "discounts. Above-average grades and prior relevant internships; "
            "EEA ID/passport, otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048i5",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240063": (
                "e7022049121fd7f46ee355c728156fc3"
                "40723fc9ce1fbb6e3d282378b58c9001"
            ),
            "240062": (
                "e9f1a415f4f6ce294bbf48d46990d013"
                "ae71a0c3a62349401d75314b2432f44d"
            ),
            "240059": (
                "01a1d2badcb7ad09d26d361c4c5b722f"
                "ec6b322a8da75e0920f87efc70e28dfd"
            ),
        },
    },
    "mer00048i6": {
        "summary": (
            "Development-controlling internship for "
            "business/business-IT/industrial-engineering or related students. "
            "Requires interest in controlling, finance and development "
            "processes, structured independent analytical work and strong "
            "communication/teamwork. No language threshold or specific software"
            " requirement is explicitly stated in these qualifications. Listed "
            "benefits: coaching, health benefits, meal discounts. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048i6",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238865": (
                "cda01e2e4bab029bcdf43cd856f38f6c"
                "f99fd1ba3f9a17d6aefb6fd247baa399"
            ),
            "238864": (
                "40a0c0be6169d688d69e7d5524c998d8"
                "763819ae82839be0425c198dfbe0b171"
            ),
            "238862": (
                "4c65596199dd2c3d945b861dd527f71f"
                "21bb7ff60bc56694bf5b2cadcc95b5ac"
            ),
        },
    },
    "mer00048i8": {
        "summary": (
            "Vehicle-motion-prediction internship requires a COMPLETED "
            "bachelor's degree and CURRENT master's enrolment in "
            "computing/electrical/robotics/automotive-engineering/mathemati"
            "cs/data-science"
            " or related study. Requires good English, Python and solid "
            "knowledge in at least one of estimation/sensor-fusion, "
            "dynamics/models, signal-processing or robotics/autonomy. German "
            "and C++ are advantages. May start November2026; study documents "
            "required. Listed benefits: coaching, health benefits, meal "
            "discounts."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048i8",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241607": (
                "10f3513b90c997d3adec2dd99928a67a"
                "f018bb6ce7536f238a85c69d59b9feea"
            ),
            "241606": (
                "e1c98ba6815a4ede9387c74349732d32"
                "98e2ac089a75a269f2ae2be9332d31f6"
            ),
            "241604": (
                "564e9029b8ad324f518077d244ac63a6"
                "0bf785cec152c61a68dc96d010fceb64"
            ),
        },
    },
    "mer00048if": {
        "summary": (
            "International project-coordination internship lists a relevant "
            "engineering/business/finance/architecture/civil-engineering "
            "bachelor's degree OR equivalent education/experience AND non-U.S. "
            "study/bachelor's-master's gap year, three semesters and at least "
            "six months. Requires fluent English AND German, MS Office and "
            "numerical/organisational teamwork. Preferred February2027 start; "
            "USD 18/hour, visa costs and USD 700 housing bonus. Required "
            "study/application documents; verify mixed degree/student wording."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048if",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239169": (
                "88dee23be02b3762b98dfdf2579911c3"
                "0334f91b3dbf49938a48fc023927a2c4"
            ),
            "239168": (
                "5344d14bb19865c666a8007a955bf918"
                "2dbc3b23f6429eb78d3ed5f97d9a724c"
            ),
            "239167": (
                "f0aa04617de8a8c5630891a280ed28e7"
                "db88a93d3bb2db66e219d75ed59091a8"
            ),
        },
    },
    "mer00048ii": {
        "summary": (
            "Studies in engineering, finance or architecture/civil engineering,"
            " or equivalent experience; English/German and Office skills. "
            "Non-US university enrolment or bachelor–master gap, at least 3 "
            "semesters and 6 months required. USD 18/hour; visa costs covered; "
            "USD 700 housing bonus. Required application documents include "
            "enrolment proof. Listed benefits: coaching, health benefits, "
            "parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ii",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239204": (
                "d256da8b3c6d6c68889d3bb461961838"
                "af745809a32a4d36d0053798c9aad7d2"
            ),
            "239203": (
                "5035813306eb9ef8e4eb0f931501aa6f"
                "b13f4e1da0e660ba7cd12273148fd15b"
            ),
            "239206": (
                "c5dbbc2f9d56f39448ab29fe3ce57920"
                "d3d0e7ae0665c69c7ff21c7f7421767a"
            ),
        },
    },
    "mer00048ij": {
        "summary": (
            "Media, communications, design, marketing, journalism or related "
            "studies; strong German/English. Image/video/animation software "
            "experience is advantageous. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ij",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238877": (
                "d4312377eeca2e15a2854faf5e406878"
                "a9621477552f8f29e811ab8cb4d7a5ef"
            ),
            "238876": (
                "efe21aa5ee206fcaa2b729cafb347b96"
                "1bfef9414a2b434b88bbf0d34a98dd35"
            ),
            "238880": (
                "c7b1a5efe33c255165c68fafc35ff99e"
                "7a6907ebc95751a8fa9b77de4bed971c"
            ),
        },
    },
    "mer00048il": {
        "summary": (
            "Vietnamese citizenship and legal work eligibility; relevant "
            "logistics/engineering undergraduate enrolment through April 2027; "
            "Vietnamese/English and Office. A stamped enrolment letter with "
            "graduation date is mandatory with the CV; CAD and presentation "
            "skills are preferred. Listed benefits: health benefits, parking, "
            "possible hybrid work, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048il",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "238518": (
                "d38a1f8692cd08341a83e4e30767ebd3"
                "a8b998373a45f091c3cd4a352faeb580"
            ),
            "238517": (
                "f746cd52ffa13c63dc78d51e83b2b2cf"
                "ff21809bf4bd6b3ee71ab05345eb5618"
            ),
            "238516": (
                "75d3a2a346fa72557b5aa92cc77e2263"
                "84b347375d89f2dd85aab3b6ee090855"
            ),
        },
    },
    "mer00048io": {
        "summary": (
            "Industrial engineering or related studies; German/English and "
            "Office skills. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Excellent "
            "grades; regular-study duration+max3 semesters, open exams;7–20 "
            "h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048io",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239955": (
                "69e0d58b0891aac24bc580bf96a0bea0"
                "80f4423af4b159d538fc0bee899c3ee6"
            ),
            "239953": (
                "6305da73468aee6497efb306fc801c3e"
                "ed585dd0c14d062eaab42842f9cfcbc2"
            ),
            "239951": (
                "8dbb07333f741723bd9001361435504b"
                "84ef63fd40a288f7a872247e1f82303a"
            ),
        },
    },
    "mer00048iv": {
        "summary": (
            "Engineering or related studies with a grade average of 2.5 or "
            "better; German/English above B2 and simulation experience are "
            "required. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Above-average grades and"
            " prior relevant internships; EEA ID/passport, otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048iv",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "238845": (
                "e9bd9161a195d7e73f73ee0ff4fa4e9a"
                "052ebc51dbfb1fe250d1775c8859538a"
            ),
            "238843": (
                "a594394e8e64bc946e82912fa5c2765d"
                "ca7e4bf548109147d0160fc29bdc8c75"
            ),
            "238842": (
                "908d80497963494f18ee2640074bcb8e"
                "992f8201d239c8107c5fd749c8b2251f"
            ),
        },
    },
    "mer00048j7": {
        "summary": (
            "Marketing internship in Munich for marketing or related students. "
            "Requires German/English, MS Office, communication, analytical and "
            "strategic work, and teamwork. Full-time, fixed-term six-month "
            "placement from April to September 2027; structured start 1 April. "
            "Submit CV, cover letter and certificates online, mark attachments "
            "as relevant and observe the 5 MB file limit. Meal discounts, "
            "coaching and mobility support listed; hybrid/flexible work and "
            "selected employee benefits remain conditional."
        ),
        "condition_evidence": [
            (
                "Study in marketing or a comparable subject; German/English and"
                " MS Office required."
            ),
            (
                "Communication, teamwork, engagement, analytical/strategic "
                "work, customer focus, reliability and independence expected."
            ),
            (
                "Advertised title specifies April–September 2027; qualification"
                " section says full-time six months. Do not convert either "
                "employment period into an application deadline."
            ),
            (
                "Submit CV, cover letter and certificates online. Mark "
                "documents as relevant for this application; each attachment is"
                " subject to the stated 5 MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, accessible workplace, nearby childcare, "
                "workplace doctor, canteen/cafe, public-transport access. "
                "Labels do not separately establish payment amounts or "
                "individual eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): München, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2027-04-01. Listing-publication end(s): 2026-11-12; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242131": (
                "c3dd1abdefb27c0cb79db3167b89d436"
                "0175c59273f52e59dbf1c52b12501fa7"
            ),
            "242128": (
                "48f13d075f8ca5bc51536a7683d2e79c"
                "3a8101b5803b218615e29ea1ff4a9390"
            ),
            "242126": (
                "9c4e280b306e8ed69941561df17a2b2f"
                "8dec93f0f42d5fc7e8ee780d28003d93"
            ),
        },
    },
    "mer00048je": {
        "summary": (
            "Mechanical/automotive engineering or related technical studies; "
            "strong German, good English and Office experience; interest in AI."
            " Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048je",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241684": (
                "6cb2efba50f899013a1effd3e750ead9"
                "f6d4327f8251d54b2b6441ae47b26300"
            ),
            "241683": (
                "a860f14f677f4588f69a4c95a3719aad"
                "a460e258766960c50ac67c199fb86979"
            ),
            "241682": (
                "051bdaa6ebcbd7aca0f1f1bad09206cf"
                "792f56e6497ba0243e6452982ecb146e"
            ),
        },
    },
    "mer00048jg": {
        "summary": (
            "Completed art-history/art/cultural-studies degree; "
            "contemporary-art knowledge, independent exhibition/project "
            "experience, Office and negotiation-level English; other languages "
            "advantageous. Required for hiring: university-confirmed "
            "dissertation topic in20th/21st-century art/culture AND "
            "university-lecturer supervision, selected by the candidate. "
            "Three-year fixed-term doctoral contract, possible March2027 start;"
            " company mentor and university supervision. Apply online with CV, "
            "cover letter and certificates."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jg",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239282": (
                "88616c436b7da4edd638d445dc43abbf"
                "f49d9d3aea8dff3be1904079be77f764"
            ),
            "239281": (
                "5c39fa7c833889d50cc555d604f19761"
                "88cd211d36d8541ab90f732e0799724a"
            ),
            "239279": (
                "5fbe3d432f593d359a978346394e90b6"
                "95f5281140bd6ca9997d2470a25ffc52"
            ),
        },
    },
    "mer00048jh": {
        "summary": (
            "Strong results in economics/industrial engineering or related "
            "studies; German/English, PowerPoint and agile-work experience. "
            "Mandatory internship component; at least three months, ideally "
            "six. MURAL, coding and PowerBI knowledge are advantageous. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jh",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238887": (
                "504185bc7e2c3ba5bae3294d7f1a2cf7"
                "afb4124f01b55ad98511710a5bf0276d"
            ),
            "238890": (
                "f15398bc59f49023922dde635d8da8ae"
                "5ba49ec4ea78960ef152aeef31942410"
            ),
            "238889": (
                "0bd246b21fedfd3d128b2faba03078cb"
                "75314fb3973ff6e9134e87d5f1b5dc5a"
            ),
        },
    },
    "mer00048jk": {
        "summary": (
            "Engineering, electrical/mechanical engineering, business "
            "informatics, computing or related studies; German/English and "
            "Office. Vehicle-network/diagnostic knowledge is advantageous. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jk",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239036": (
                "31237d00477a364c88951d3dfd68abba"
                "a732587a8f13109add92f4fa307634d0"
            ),
            "239035": (
                "27bc45ea636daa53bb9691ccf9fadc84"
                "73a8cf37a83d9f46814c2d08270d1983"
            ),
            "239032": (
                "0a36b81396d2392a735157b3f224f8c6"
                "ab8239e793d26d470312ba4cbe8285c3"
            ),
        },
    },
    "mer00048jl": {
        "summary": (
            "Business, international management or related studies; "
            "German/English and Office, particularly Excel/PowerPoint. A "
            "mandatory internship component is required; Copilot experience is "
            "preferred. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jl",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239291": (
                "122331fd091dcf03f73a00f68a1093d1"
                "9ffdb1d45cf2dfeb35cf01060a861864"
            ),
            "239289": (
                "85798a49f2b7a2c5a99db81d5407d408"
                "9baade7e7ad24fe38e09f5589bd25423"
            ),
            "239292": (
                "94ae43eeb60a2d1e714d47ae6cd5df41"
                "9b0e8ac2f064d15a4f34f664c61a4906"
            ),
        },
    },
    "mer00048jm": {
        "summary": (
            "Business informatics, industrial engineering, technical business "
            "or related studies; German/English and Office. UX/UI/data-science "
            "knowledge is advantageous. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jm",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239052": (
                "563d056d8230fcbe452b06fc3ef140c4"
                "de75fb8f61021770ec720e55672069cb"
            ),
            "239051": (
                "407f98d2e891234e284c10a216688933"
                "d9ac33e2576293e51a6a9be6a44112ad"
            ),
            "239050": (
                "058eeae2f55e41096aceb3626a09d06e"
                "2d27443dda9f5ce061e8fdfeaf697bd5"
            ),
        },
    },
    "mer00048jr": {
        "summary": (
            "Business, industrial engineering, media, communication, computing,"
            " logistics or related studies; German/English, Microsoft365 and "
            "video/image-production experience. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jr",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240236": (
                "2882686eb221d79e51473d8fb260ffaa"
                "6de4c3829f4e5151e6b1b82183818aaf"
            ),
            "240234": (
                "e385d67f43c202ab7893cc09a12d6b5f"
                "44bc2a6e5c211de48f6b998f903f0f4d"
            ),
            "240232": (
                "7b0a22f1f535f72a70ee504babce9d81"
                "7330cd6483a73c64804c7c392d2f6dac"
            ),
        },
    },
    "mer00048jw": {
        "summary": (
            "Logistics, automotive/industrial engineering or related studies; "
            "German/English, Office and a category-B driving licence. Coding is"
            " desirable. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jw",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239769": (
                "aa4bfdce13204da337f9b47839e4b0c0"
                "f4e70b024a26b30747af40c2a33fd573"
            ),
            "239768": (
                "47b2d6a9b4783434c22918c32c65eae4"
                "5954b456e207ac19983c99f6c61387b6"
            ),
            "239766": (
                "905860c9ef5a987b5964c77d03e800d4"
                "e98cec23296d620fb079318d9a5c5921"
            ),
        },
    },
    "mer00048jy": {
        "summary": (
            "Recent graduate or junior/senior business bachelor/master student,"
            " preferably HR/communications/project-related; Office/Adobe, "
            "communication/design/social-media skills. Prior corporate "
            "experience is preferred. At least six months and three semesters "
            "are preferred, with 40 h/week preferred but part-time possible. "
            "USD 20/hour; no housing/relocation assistance."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048jy",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "238683": (
                "473ca8dfd1ec341a6908dd188c9b3dfd"
                "3add6fd770bd13a8394343399a34e200"
            ),
            "238681": (
                "dc3e1af69291be7312fa234d3b6abb9c"
                "90ea1cbf5fcf3635aae59aa7790843fa"
            ),
            "238678": (
                "4494dccc1091ec530cc561463d9a52c1"
                "7bcb167a0aabc82d756094c1c953623a"
            ),
        },
    },
    "mer00048k0": {
        "summary": (
            "Marketing, communications, media, business, project management or "
            "related studies; German/English, Office and digital-content "
            "skills. Mandatory internship component; prior digital brand "
            "communications experience is advantageous. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048k0",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239062": (
                "e991b2ec1946c4381d2388a45668f653"
                "118e59b622659adbeba4fdd5a71fd3cd"
            ),
            "239061": (
                "2c90785d1dec22981ef7b57b6dddb31f"
                "b351e5c29786d58300e49a94b1bdc6f1"
            ),
            "239064": (
                "509ec8362fda03c215284c4e94cd7b2e"
                "62745fe0c5afc160b9c0e793b5ff5e8e"
            ),
        },
    },
    "mer00048k1": {
        "summary": (
            "Communication/graphic design or related studies; Adobe Creative "
            "Cloud, Office and German/English. 3D software and AI-tool "
            "knowledge are desirable. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048k1",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239824": (
                "d6e482809c2688130e2e17a888def60a"
                "9e57d6b19c3ddd5dca6056766b22a1a5"
            ),
            "239822": (
                "a5f97fe13c4c9956bc773aff4d26c0ff"
                "999cb7ed94adde6fda8ed0e92e335a0f"
            ),
            "239821": (
                "0680d774613434d7182009c48089fc68"
                "d613b2b2ea85f0e4e058394446080da0"
            ),
        },
    },
    "mer00048k4": {
        "summary": (
            "Electrical/industrial engineering, supply-chain/business degree or"
            " equivalent education/experience; English/German, Office, project "
            "skills and BIC Process Design/SAP Signavio experience. Non-US "
            "university enrolment or bachelor–master gap, at least 3 semesters "
            "and 6 months required. USD 18/hour; visa costs covered; USD 700 "
            "housing bonus. Required application documents include enrolment "
            "proof. Listed benefits: coaching, health benefits, parking, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048k4",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239226": (
                "41f948d56db37490ba867395b21a23ec"
                "40ae4b0773ab88f654a1585ea0d07cbb"
            ),
            "239225": (
                "e1da5d335734b1fdda86a4130da938d4"
                "7964c3c07a5a2457e3e55481201f61d8"
            ),
            "239224": (
                "f0aad0e9eaff4db8bdf6adfe95085657"
                "8e9a3410a348d2094b2eb9d324b54a93"
            ),
        },
    },
    "mer00048k7": {
        "summary": (
            "Finance, MIS, computing or related degree or equivalent "
            "education/experience; Office/Excel skills. VBA, "
            "PowerBI/PowerQuery, SAP and project-management experience are "
            "preferred. Non-US university enrolment or bachelor–master gap, at "
            "least 3 semesters and 6 months required. USD 18/hour; visa costs "
            "covered; USD 700 monthly housing stipend. Required application "
            "documents include enrolment proof. Listed benefits: coaching, "
            "health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048k7",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239187": (
                "2430dd6bbf4193d506cc1db1b0d4623d"
                "bf6fd6cd4922ea3fddb04e4c20bf376a"
            ),
            "239186": (
                "085ef2d1e76163fdda618ea96ddda60f"
                "94d63ac189b9adc49a80b1a82e754940"
            ),
            "239185": (
                "a8ce819a78415f2156efeb3c505170a5"
                "314622f49b48f810934f8a36a6dd6f8e"
            ),
        },
    },
    "mer00048k8": {
        "summary": (
            "Finance/industrial engineering or related studies or equivalent "
            "education/experience; fluent English, Office, SAP, PowerBI and "
            "PowerQuery. Non-US university enrolment or bachelor–master gap, at"
            " least 3 semesters and 6 months required. USD 18/hour; visa costs "
            "covered; USD 700 housing bonus. Required application documents "
            "include enrolment proof. Listed benefits: coaching, health "
            "benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048k8",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239193": (
                "b0cc79af42186697f6dc1f561c3edec3"
                "7e26326230d13dacbeacdb48b52b728e"
            ),
            "239192": (
                "8284d2de96323b37c10372b3d77827ed"
                "511eb9c62b9ae9bc381125ee5f73a88b"
            ),
            "239191": (
                "9f96a6ff8056d06ea1ec1b6fb35b4afc"
                "02a27c45baa448385ba4ac33fabdf97d"
            ),
        },
    },
    "mer00048k9": {
        "summary": (
            "Engineering, finance, procurement/legal, business, supply-chain or"
            " related studies or equivalent education/experience; fluent "
            "English, Office and organizational/project skills. Non-US "
            "university enrolment or bachelor–master gap, at least 3 semesters "
            "and 6 months required. USD 18/hour; visa costs covered; USD 700 "
            "housing bonus. Required application documents include enrolment "
            "proof. Listed benefits: coaching, health benefits, parking, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048k9",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241587": (
                "6d7870295e94d805feda6ed7c2aa2beb"
                "61944003bb25534603ea654e06a77981"
            ),
            "241586": (
                "92de760cd45c42dae7686d7a7601453f"
                "32ef1f8b1cfd78bf7f2a588df9296e01"
            ),
            "241585": (
                "4e938cfcead9a5a5aaa21a3d88c23deb"
                "5dd57e44ec3c9827b1b9a60580a3f91f"
            ),
        },
    },
    "mer00048ka": {
        "summary": (
            "Finance or engineering studies or equivalent education/experience;"
            " English, Office, analytical skills and PowerBI dashboard "
            "creation. Non-US university enrolment or bachelor–master gap, at "
            "least 3 semesters and 6 months required. USD 18/hour; visa costs "
            "covered; USD 700 housing bonus. Required application documents "
            "include enrolment proof. Listed benefits: coaching, health "
            "benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ka",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239220": (
                "38f4c3d5b29b67ac6fee58da644fe809"
                "3ad5c6955898905bf83ba64a57300a1a"
            ),
            "239219": (
                "52a20c2963846edaa2d21dd57cddafd3"
                "a372f8edd80a8fdab03640b6e9060588"
            ),
            "239218": (
                "c4a67b8e5f22cfb5d93e6cac06e3677d"
                "55c8b5aa488e416e2dcdefe59f3bead3"
            ),
        },
    },
    "mer00048kb": {
        "summary": (
            "Finance, business or industrial engineering studies or equivalent "
            "education/experience; Office, PowerBI and project-management "
            "experience. Supply-cost engineering knowledge is preferred. Non-US"
            " university enrolment or bachelor–master gap, at least 3 semesters"
            " and 6 months required. USD 18/hour; visa costs covered; USD 700 "
            "housing bonus. Required application documents include enrolment "
            "proof. Listed benefits: coaching, health benefits, parking, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kb",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239256": (
                "1c84983cb7c3d9cd1af7707b214d1017"
                "d5d670699094710314e3e465f00b7d62"
            ),
            "239255": (
                "9abc9992ffa15cccab5cb8972ba61551"
                "2f678c2137ee893e6dc9527dc22cce8d"
            ),
            "239254": (
                "6b5d7e4e56e5e42eaed668caee5af180"
                "932006989cbc471d26dcc0c15e528856"
            ),
        },
    },
    "mer00048kc": {
        "summary": (
            "Engineering/computing/software or related degree or equivalent "
            "education/experience; basic Python/SQL/Excel, "
            "PowerBI/PowerApps/automation and AI/data-visualization awareness. "
            "Non-US university enrolment or bachelor–master gap, at least 3 "
            "semesters and 6 months required. USD 18/hour; visa costs covered; "
            "USD 700 monthly housing stipend. Required application documents "
            "include enrolment proof. Listed benefits: coaching, health "
            "benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kc",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239244": (
                "ad25fa889b007703942627ee2e18446c"
                "c9cd6c51ce5903d4bdf26243f92ed76b"
            ),
            "239243": (
                "4f27ac7b7672df57fda6df97e2f2b0b8"
                "6794b95df4ff94200a1c3db79294f35c"
            ),
            "239242": (
                "09b9f6f7f02ae98866381bcec32aa2ec"
                "e424837d9afc5f96740640aaaf5a694f"
            ),
        },
    },
    "mer00048kf": {
        "summary": (
            "Industrial engineering, supply-chain, business or related degree "
            "or equivalent education/experience; English, Office, "
            "supply-chain/project skills and strong coding. German, "
            "PowerBI/PowerApps and automotive knowledge are preferred. Non-US "
            "university enrolment or bachelor–master gap, at least 3 semesters "
            "and 6 months required. USD 18/hour; visa costs covered; USD 700 "
            "housing bonus. Required application documents include enrolment "
            "proof. Listed benefits: coaching, health benefits, parking, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kf",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239259": (
                "61b232963addac27613e3ece7275bac6"
                "350d74d13fc031e2e3bdc43641556bf6"
            ),
            "239258": (
                "0c7b250249155cf45464bdeaa3605635"
                "5b1d4a2e6fd6070476ccc4c47de35063"
            ),
            "239257": (
                "af7c64de7e3f55c82ad88737eeb6fb98"
                "11c6c91bd4920ca53aa01c761aa3dbdb"
            ),
        },
    },
    "mer00048kj": {
        "summary": (
            "Business, finance or engineering degree or equivalent "
            "education/experience; analytical/organizational skills, basic "
            "reporting and Excel/PowerPoint. Non-US university enrolment or "
            "bachelor–master gap, at least 3 semesters and 6 months required. "
            "USD 18/hour; visa costs covered; USD 700 housing bonus. Required "
            "application documents include enrolment proof. Listed benefits: "
            "coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kj",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239250": (
                "29f707e055b09d10857fbcef07c17d6e"
                "d45cf5dbd8ca845d61d7e9b630702b31"
            ),
            "239249": (
                "b3c8437a76cc34172c515d883d54cc5a"
                "59e5fc82572b805c1cbc34c20f28f3a4"
            ),
            "239248": (
                "a474b29fefd8a240bb49faa8af70abca"
                "6088f8e280180eddb1162b0fe3f09a77"
            ),
        },
    },
    "mer00048kl": {
        "summary": (
            "Finance, business or industrial engineering studies or equivalent "
            "education/experience; Office, PowerBI and project-management "
            "experience. Supply-cost engineering knowledge is preferred. Non-US"
            " university enrolment or bachelor–master gap, at least 3 semesters"
            " and 6 months required. USD 18/hour; visa costs covered; USD 700 "
            "housing bonus. Required application documents include enrolment "
            "proof. Listed benefits: coaching, health benefits, parking, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kl",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239246": (
                "11dfb70b6f135accb65e5663ad3bd6d1"
                "2af2b27d500d4f9cb33fb45cad06fb47"
            ),
            "239245": (
                "bce327727350186d2740411561d761e7"
                "e375f6e875a73e874469fa94cc543d82"
            ),
            "239247": (
                "84a32e677f46492fdbf948f4f8bf43be"
                "ba831df1ad4a21e80871606046324578"
            ),
        },
    },
    "mer00048kr": {
        "summary": (
            "Engineering, supply-chain, business, computing/data or related "
            "studies or equivalent education/experience; manufacturing, "
            "engineering problem-solving and reporting skills. PowerBI is "
            "preferred. Non-US university enrolment or bachelor–master gap, at "
            "least 3 semesters and 6 months required. USD 18/hour; visa costs "
            "covered; USD 700 housing bonus. Required application documents "
            "include enrolment proof. Listed benefits: coaching, health "
            "benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kr",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240002": (
                "833179f21b8f87ff7e3b5295f8714c3d"
                "7b655364dfcb7cfd9e8374583f006b37"
            ),
            "240001": (
                "7b184eeed502654f36b8a8cbb4d43868"
                "1752afbb455e59e2a109820f4b4c1333"
            ),
            "239999": (
                "d2940ea235855aa36feb5588969c1ee0"
                "4093c4b405cfd4ce90fae12c7171ef3c"
            ),
        },
    },
    "mer00048ks": {
        "summary": (
            "Industrial engineering, supply-chain, business or related studies "
            "or equivalent education/experience; English, Office and "
            "project/reporting skills. German, supplier/manufacturing and "
            "PowerBI/PowerApps knowledge are preferred. Non-US university "
            "enrolment or bachelor–master gap, at least 3 semesters and 6 "
            "months required. USD 18/hour; visa costs covered; USD 700 housing "
            "bonus. Required application documents include enrolment proof. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ks",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239235": (
                "ff7278dcf833b6a0364134df41369297"
                "9959bc6c29f60166b2117e9dc5d64c04"
            ),
            "239234": (
                "cd5b20d08749474dfbb77b196402b912"
                "c3fcf3cae3f8db0cc40d9bc12308c08f"
            ),
            "239233": (
                "79f5018d0b069d4bca1270df0b94a0cf"
                "a97edef69f9c8647c4a29ec145174fcb"
            ),
        },
    },
    "mer00048kt": {
        "summary": (
            "Industrial engineering, business, supply-chain or related studies "
            "or equivalent education/experience; English, Office, organization "
            "and data analysis. PowerBI, project and manufacturing experience "
            "are preferred. Non-US university enrolment or bachelor–master gap,"
            " at least 3 semesters and 6 months required. USD 18/hour; visa "
            "costs covered; USD 700 housing bonus. Required application "
            "documents include enrolment proof. Listed benefits: coaching, "
            "health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kt",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239238": (
                "862863351b104bd776571c40d1037b8f"
                "99c7715281e10bb081f87a631c66e2eb"
            ),
            "239237": (
                "4657caf69004574d5fc0f62ce5d9a779"
                "d8eef5a46acbfac1848c9a34acd87959"
            ),
            "239236": (
                "1c9920db44d9d83f4297a455c78a90df"
                "1bebb8f05211fbef512e9c0167fbed7b"
            ),
        },
    },
    "mer00048kw": {
        "summary": (
            "Exclusive to young people with a learning impairment under §64 "
            "BBiG. Minimum Hauptschulabschluss required; publisher also lists "
            "secondary/special-school attendance or one-year vocational "
            "preparation. Technical and manual aptitude required. Pay EUR1,303 "
            "in the first training year to EUR1,377 in the final year; strong "
            "employment prospects after successful completion, without a job "
            "guarantee. Apply online with CV, latest report and applicable "
            "certificates."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kw",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "238731": (
                "cf53d0460817a0d92e00d7db161005d8"
                "82c478e07e6eb69870fd3a88324e5eef"
            ),
            "238730": (
                "4732f165fcff72aa25c30e3908c3934f"
                "f1bd02bfe60321ac6133f703a6dcd634"
            ),
            "238728": (
                "b57e3ab35e266844808a2294f8025152"
                "cd63e3c60c28206459e022efda297e19"
            ),
        },
    },
    "mer00048kx": {
        "summary": (
            "Engineering/business or related studies or equivalent "
            "education/experience; Office and Mercedes "
            "quality/development-process/tool knowledge. Non-US university "
            "enrolment or bachelor–master gap, at least 3 semesters and 6 "
            "months required. USD 18/hour; visa costs covered; USD 700 housing "
            "bonus. Required application documents include enrolment proof. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048kx",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239241": (
                "5f8a9eb5683fc50fbdda2e6b60981480"
                "fef0b8c63a2cee1194b94a4e68c50499"
            ),
            "239240": (
                "b55449f52e4aa90e6107b629a4abfec8"
                "c8cab31651fdbdfe139b8264616fcc71"
            ),
            "239239": (
                "7af6d1ee989c55860a74db4a0681f0ed"
                "c2ee341f4d1da4a3552a2353d4a246cb"
            ),
        },
    },
    "mer00048lr": {
        "summary": (
            "Mechanical engineering or related technical studies; "
            "German/English, Office, CAD and manufacturing-technique knowledge."
            " Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Above-average grades and prior "
            "relevant internships; EEA ID/passport, otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048lr",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239083": (
                "2d1c15658c663a2b298e2ca1b1b4d9de"
                "2e2c9a66396e7cebca7849c84ada5771"
            ),
            "239082": (
                "56bcafd4829f2a39ba2950d04be1e4ef"
                "6e4f0d128ef54650bbbd93a600f6209e"
            ),
            "239080": (
                "76398aa75f373617bfc6edbf28223519"
                "f60c36fb8c5cf394340907a86759cc10"
            ),
        },
    },
    "mer00048ls": {
        "summary": (
            "Business, economics, industrial engineering or related studies; "
            "strong German, good English, Office and previous Mercedes-Benz "
            "internship experience. Data-analysis/PowerBI experience is "
            "preferred. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ls",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239123": (
                "0aff7b1d784502795d75a1c32f86b3b4"
                "7dcc00b2200162faa91554b61b6c29b0"
            ),
            "239119": (
                "fdb52c860460db0af868291adf83b3a9"
                "14a67269f0ec9c82bdf2e7b685b33cb8"
            ),
            "239117": (
                "f5dbea8836a3dddf300db566b6d568a4"
                "6ef8f73c9cd6b6446f1727d58f1de354"
            ),
        },
    },
    "mer00048lt": {
        "summary": (
            "Current business/IT studies or comparable training; strong German "
            "and Excel/Office. Six months,40 h/week, starting December2026. "
            "Stated gross full-time pay EUR 2243/month; IT affinity is "
            "advantageous. Listed benefits: coaching, health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048lt",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "238806": (
                "c31d4c0ac63e7f52e909a93417a47078"
                "56c0fe1ad3f65f326939fa16617d86dc"
            ),
            "238805": (
                "183fd08fa3c8c60d2077abeb53c3e46d"
                "eeda9ec2b597ffaad211c430dfc718c7"
            ),
            "238798": (
                "d75dc26f959352f7bd1b5ee70d3f7f81"
                "e0aeef2e9ab7c055a16ed3577939dcf3"
            ),
        },
    },
    "mer00048lu": {
        "summary": (
            "Law, ethics, business or related studies with strong IT interest; "
            "excellent German, secure English and Office. Mandatory internship "
            "component; advanced AI/digital knowledge is preferred. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048lu",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239099": (
                "9ba9ed9d406a89b67c1e210d3c088f89"
                "59186bfc55616808838c3f73c763dc2b"
            ),
            "239098": (
                "bf7903d4822fafd2a73b4837b1a05406"
                "bec1c716acae81b9a96246b6f9a4f5d7"
            ),
            "239095": (
                "f2b5571c421a0b00e628a785c41ff53f"
                "b51708fb7c9fbc7e455384a75ad60730"
            ),
        },
    },
    "mer00048lv": {
        "summary": (
            "Computing, data science/processing or related studies; "
            "German/English, Office/Copilot, good Python/Java or similar coding"
            " and XML/JSON/image-data experience. ML knowledge is advantageous."
            " Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048lv",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239387": (
                "96b354e4586c8dcf29a7cdcc5b80d6bf"
                "01f46acf09c654e6e6748882451b43f8"
            ),
            "239386": (
                "04549d81d8944a0d7aa239a58b712784"
                "ac6b177c341f77aacd6f269e94a2ac80"
            ),
            "239385": (
                "abd76e29bd5150a637361bc47302895a"
                "c8346cefef38e77427e8a6fc1202ae18"
            ),
        },
    },
    "mer00048m4": {
        "summary": (
            "Engineering, automotive/mechanical/industrial engineering, "
            "media/communications or related studies; German/English, Office "
            "and a category-B driving licence. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048m4",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239376": (
                "e7542d428ea2e8b16b0090f6500899a0"
                "6e75543ee30dd130fca7eb69be58fc16"
            ),
            "239374": (
                "061aad802b39bb81b24d13bc68af88bc"
                "6ff09332b5385701bc508320ccdb8780"
            ),
            "239372": (
                "624a47df83d0357ef244b8784889aaa2"
                "0dfe136b2819c13caa8a7ecf097664ef"
            ),
        },
    },
    "mer00048m5": {
        "summary": (
            "Engineering, industrial engineering, communications/media, "
            "business or business psychology studies; German/English at least "
            "B2, Office and initial project-management knowledge. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048m5",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239420": (
                "11d5534fe1bbe093c17d22679bf01c63"
                "88587a2e7baab2e5ca0ca80c0889cf86"
            ),
            "239419": (
                "7c544323a2819c441ab1cd242cabe5b9"
                "7a00efe50e4ee7527ed7be779af41f3b"
            ),
            "239417": (
                "bd267ad7a75f1777fc26619750e69735"
                "6993491d89d44cb436e7891b9712a383"
            ),
        },
    },
    "mer00048mi": {
        "summary": (
            "Engineering, materials or natural-science studies; German/English,"
            " Office and willingness to work in a workshop. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048mi",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239413": (
                "36fb2442472670bd6bfc1903dd23fa42"
                "0cd076b038e2ffb0cf197e786bbfa945"
            ),
            "239408": (
                "d8529e8fbf436409d9577f7cdf74f122"
                "f0b30fa30164e966445e96259eebfafb"
            ),
            "239407": (
                "4deda951e4d977a011d16ee6fb23aa6e"
                "038f1c4e61d9c034fbefe74afae35ef2"
            ),
        },
    },
    "mer00048mn": {
        "summary": (
            "Mechanical/industrial/electrical engineering or related studies or"
            " equivalent education/experience; Office and "
            "reporting/data-analysis skills. PowerBI, project/manufacturing "
            "knowledge are preferred. Non-US university enrolment or "
            "bachelor–master gap, at least 3 semesters and 6 months required. "
            "USD 18/hour; visa costs covered; USD 700 housing bonus. Required "
            "application documents include enrolment proof. Listed benefits: "
            "coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048mn",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239253": (
                "16deeb87206eb2182f3921ee178595b4"
                "77dd02a078663fab768e59762ce538e6"
            ),
            "239252": (
                "5cc20f152bb4ef19f5fb1031523e3785"
                "b9bf6ea8f3b25635ac54462bde219a75"
            ),
            "239251": (
                "1b141b521290ab1c4b0f4b49fa636fe3"
                "004664a2376cf73cfa13b5d843797b49"
            ),
        },
    },
    "mer00048mt": {
        "summary": (
            "Industrial/other engineering, economics, business or related "
            "studies; German/English and Excel/PowerPoint. A mandatory "
            "internship component is required. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048mt",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239369": (
                "00a6dcdfbf99e24786eab8d180dda773"
                "a165853d58e30878325606f4c82b7722"
            ),
            "239368": (
                "2e0dfcc437325502859e0e43f5c22d89"
                "45dcfc9fe197929a096891f6fb16f8fa"
            ),
            "239365": (
                "5cdc10821327d3dd1b622e28f9e4e083"
                "d48e59396c89cf36d4684eaa29173a6f"
            ),
        },
    },
    "mer00048n7": {
        "summary": (
            "Industrial engineering, logistics, mechanical engineering, "
            "technical business or related studies; strong German/English and "
            "Excel/PowerPoint. VBA and logistics knowledge are advantageous. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048n7",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240036": (
                "2e2ec39ce28472f69d11ab1bf90284b8"
                "7b0bc08b2c2a7465fe957152c7a58874"
            ),
            "240035": (
                "ac2cdce82204e0098bed781cf19ecc45"
                "760389129eb6f95027e7307dc3eaf75c"
            ),
            "240031": (
                "af62ed06a5ffc2046dd5ed086a2c6ad4"
                "6d067ac4125dc307243f5a0aa855f2c4"
            ),
        },
    },
    "mer00048n9": {
        "summary": (
            "Mandatory internship placement required. Computing, robotics, "
            "physics, mathematics, electrical engineering or related studies; "
            "strong Python/deep-learning/PyTorch, Linux experience and English."
            " Research publications, Docker and advanced AI-model knowledge are"
            " preferred. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048n9",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239967": (
                "ff16f1e1b5fb643d52dc7529c58d4fd8"
                "2f1d99e69392ae9fde4478c6883922e1"
            ),
            "239966": (
                "e86598630aaa4c792dbac0fe95bb64a4"
                "9648682e2c438dccc5d05214895b3223"
            ),
            "239963": (
                "35bfd265c2dd156af1c9495d396767b7"
                "50875de525c7cd57de583eaec4be30ce"
            ),
        },
    },
    "mer00048nm": {
        "summary": (
            "Economics, industrial engineering or economics-focused engineering"
            " studies; German/English, Excel/PowerPoint and a category-B "
            "driving licence. Mandatory internship component required. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048nm",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239591": (
                "cd7e463ca253ec96ac894305b08e2822"
                "8c2a363aebdbd8486e0fb43ff70c003e"
            ),
            "239590": (
                "ac65283d8b23501d2f58f1c800c979b9"
                "8d2e5b3fb45c2d3b6485e3504242a45f"
            ),
            "239588": (
                "146baaf520fa41b1a738839240ed3fbb"
                "a7cd7e5095c9c894fe0405309c3cbebe"
            ),
        },
    },
    "mer00048no": {
        "summary": (
            "Technical, organizational or communications/media studies; "
            "German/English, Office and a category-B driving licence. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048no",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240305": (
                "95912a16934f4a51ab3c0b55a0dd2ea2"
                "d2198c75945dc6b696f2d5299c7fc0ea"
            ),
            "240304": (
                "5a34d8b1d731605272865f1e4c3dedd1"
                "4c989e32135530ec6ed1f26b2b8519d9"
            ),
            "240302": (
                "453f5d2eaa33bca6bc1d303810600dca"
                "98121f67e72ca0c814a971fd9e780a8f"
            ),
        },
    },
    "mer00048ob": {
        "summary": (
            "A valid Taiwan work permit; relevant IT/computing/business "
            "bachelor degree, with an advanced degree preferred; fluent "
            "English/Chinese and digital M365/PowerPlatform/AI familiarity. Six"
            " months, at least three days/week; provide available start date in"
            " the CV. Listed benefits: health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ob",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239298": (
                "3f13d8f1136f43d8908edfca7a8d02a5"
                "07258241c36cb4c9ac22428d88305596"
            ),
            "239297": (
                "7ecf9e2e3c200392ac005340c54afa22"
                "c59f946592515c12396965834e78148e"
            ),
            "239294": (
                "676e288e043b5266cbadb2b65c8a2b80"
                "0df72d0656f1f9b03f09acda4235ad72"
            ),
        },
    },
    "mer00048of": {
        "summary": (
            "Current university studies; excellent German, Office and a "
            "category-B driving licence; automotive experience advantageous. "
            "Six months,20 h/week from November2026; stated gross full-time "
            "salary benchmark EUR 2243/month. Listed benefits: coaching, health"
            " benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048of",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239286": (
                "7e3c4a7aa8d551ce8ebba5a9374ea435"
                "413934a7e8490dec7e22df85988f8f84"
            ),
            "239285": (
                "54a344eb0112ba9ffecf1159bd0153eb"
                "9c96ef2e9a77c097baa9554d56e75a5e"
            ),
            "239284": (
                "5fd261d1eaa806701a5eaa513d1962fb"
                "1f6b1a9f21aa7c68a2cc524090d0bd58"
            ),
        },
    },
    "mer00048ok": {
        "summary": (
            "Marketing-focused business, industrial engineering or related "
            "studies; negotiation-level German/English and Office. A mandatory "
            "internship component is required; further languages desirable. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ok",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241500": (
                "8eeb16aaf502df779bf556a19f147664"
                "8f1ed91c648bdc0b9013a641c385b19e"
            ),
            "241499": (
                "597589e64815687b3627c08ac29fbe2e"
                "0ca88984b5fc8e086189fab369cffb67"
            ),
            "241496": (
                "af4d14ebae07d32e5ece34682697baf6"
                "df9b3efdd28d81fab4b9caebb7e1838b"
            ),
        },
    },
    "mer00048om": {
        "summary": (
            "Business, economics, marketing-focused industrial engineering or "
            "related studies; German/English and Office. Mandatory internship "
            "component required. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048om",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240893": (
                "728f88b726f0857e766db319bd3a1416"
                "04560e2a4d01cc49079158011f812078"
            ),
            "240892": (
                "c46e2ce94ae423df3976b8f8dd418c95"
                "82023a5311069cac43d2f766121d1258"
            ),
            "240891": (
                "96603b03fe1f8ae66a5a173395acb413"
                "f442e1c9053605c295a8ba81152f1038"
            ),
        },
    },
    "mer00048oy": {
        "summary": (
            "Mechanical/production/industrial engineering or related studies; "
            "German/English, Office and interest in production, AI and digital "
            "technologies. Listed benefits: coaching, health benefits, parking,"
            " possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048oy",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241320": (
                "faedcac0ef1ae59a2e6d6099aeed7e69"
                "39c2897b34810cb5777e170f0eff29e3"
            ),
            "241319": (
                "db73aab92b252516e864dc1c2af1c140"
                "bc68c0ec674331995dcce56203fc5577"
            ),
            "241318": (
                "4186f12ca7f6127025dec67e95aa40c9"
                "7e2a099f6bdfc1e2d4911494da809bc6"
            ),
        },
    },
    "mer00048pa": {
        "summary": (
            "Industrial/mechanical engineering or related studies; "
            "German/English and Office. Vehicle-development/project knowledge "
            "and a category-B driving licence are desirable, not mandatory. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048pa",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240513": (
                "45171c8b4dc97827d27edb1520441e60"
                "36d5ad294dcfca35e835d07b14061304"
            ),
            "240511": (
                "a45376550bd65c3050feb0102c139370"
                "8018a837c43ab9acc11c1d2ec415eafb"
            ),
            "240509": (
                "9b078014a642743d8917e9a99ef5db0e"
                "535758e4ce9e915b3cacec02af0beecc"
            ),
        },
    },
    "mer00048pb": {
        "summary": (
            "Relevant automotive, engineering, computing, mathematics, "
            "physics/materials studies; measurement, coding and statistical "
            "experience are advantageous. Source specifies a driving licence "
            "but omits its class. Six months,20 h/week; immediate start; EUR "
            "2243/month stated as a full-time benchmark. Listed benefits: "
            "coaching, health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048pb",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239470": (
                "969178caeb84818cbd743ac8898b2a2e"
                "f9acda8ddf40b482f80b38635dd602d7"
            ),
            "239469": (
                "f0fac35efee065cd2746dab299aa099e"
                "2980887577d05589c86876fece2aa47b"
            ),
            "239468": (
                "699ef6b5af574d86c40cf1aa077a16ac"
                "d9faf833fd3bc7222d24f48795882568"
            ),
        },
    },
    "mer00048pd": {
        "summary": (
            "Business informatics, industrial engineering, data/computing, "
            "logistics/business or related studies; German/English. "
            "Power-platform, Excel/SQL/Python/Celonis knowledge is "
            "advantageous. Listed benefits: coaching, health benefits, parking,"
            " possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048pd",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240685": (
                "e726fb4d015da4115348c395defbd87c"
                "9ece39f7d5ddaed0a3a5e654806b723a"
            ),
            "240684": (
                "2a7ca00d8dc62b773b011eb2f7cffa5b"
                "59522af593e2f6d7dfda8ecb365e3755"
            ),
            "240682": (
                "d89b214a61eb78d439663dbfc14c4cf8"
                "19b717612e9cb99521c65845f68c0eda"
            ),
        },
    },
    "mer00048pf": {
        "summary": (
            "Business informatics, industrial engineering, data/computing, "
            "logistics/business or related studies; German/English and Office. "
            "Power-platform, SQL/Python/Celonis experience is advantageous. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048pf",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240664": (
                "1c44b28bcf6e770e00c5da79987fd902"
                "70161a68284664211876d819269f3fca"
            ),
            "240663": (
                "7313696d7c3d529ced42c895e9c6d8dc"
                "a4377f602c16b7f31d31b13463d45cb9"
            ),
            "240662": (
                "a526a827f7a1c95ffb7fbe88fb2ab609"
                "74f6b0baff0d206eb364dcfbeca1b4a6"
            ),
        },
    },
    "mer00048ph": {
        "summary": (
            "Electrical engineering or related studies; German/English, "
            "inverter/power-electronics and synchronous-motor basics, plus "
            "MATLAB/Python knowledge. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ph",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239699": (
                "d65fcb79c3cdf8bc529132e71c04a279"
                "6b4a4fbd0dcb8f64f4b9887c16d4afeb"
            ),
            "239696": (
                "54562a022b0a6fa1080f1982f1406a70"
                "0108b5f827249d9c6be58e73d6dd920d"
            ),
            "239701": (
                "39dbc1fb37e5aeee282d0a5fdec4a6b5"
                "20857c4a1b2350399d3fa4be1ec96c05"
            ),
        },
    },
    "mer00048pi": {
        "summary": (
            "Clear on-site focus for vehicle/prototype work. Electrical "
            "engineering, software/computing, vehicle electronics or related "
            "studies; German/English, Office and strong technical "
            "understanding. Vehicle-network, diagnostic/ECU tools experience is"
            " advantageous, not required. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048pi",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239671": (
                "2e7e9f1378dca3f5004e56441049dc58"
                "d5c03a47339f07e922e62c34553d3b2a"
            ),
            "239676": (
                "68617be895c96da58d3021e039195846"
                "0a1ed8adf013c2cf7963974814e9ba22"
            ),
            "239673": (
                "5f7d28035162293460605645ea614d97"
                "31e12e74ff4cd6ed30b30f3664cc232d"
            ),
        },
    },
    "mer00048px": {
        "summary": (
            "Computing/software studies, at least semester3–4;16–18 h/week for "
            "at least six months and one office day/week. German/English, "
            "backend or frontend coding with willingness to learn the other, "
            "Git and testing required. DevOps/ADAS experience is desirable; "
            "safe vehicle operations and verified AI-assisted code are "
            "expected. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048px",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240466": (
                "6ba1b748861ebfd66d45312df6a3a7e1"
                "02c5c6102d99b0cffd4b91cd9bbb7dea"
            ),
            "240459": (
                "ce35274b6948612dd3189dfca7f7b279"
                "5062c75d129b661a799dcaff33ae21b5"
            ),
            "240458": (
                "4f99414c88895eeb4cc2c3faf5194498"
                "c6dc9f0d10d294d9398c271fd2bab43f"
            ),
        },
    },
    "mer00048q6": {
        "summary": (
            "Digitalisation/battery-and-engine controlling internship in "
            "Esslingen for business with digitalisation/controlling, computing,"
            " business informatics, industrial engineering or related students."
            " Requires Excel/PowerPoint, fluent German/English and "
            "analytical/technical ability; Power BI/database experience "
            "desirable. Must include a compulsory internship component. Submit "
            "CV, cover letter, certificates, enrolment/semester and "
            "standard-duration evidence, plus applicable placement proof. Start"
            " 1 March 2027."
        ),
        "condition_evidence": [
            (
                "Study in business administration focused on "
                "digitalisation/controlling, computer science, business "
                "informatics, industrial engineering or a comparable course."
            ),
            (
                "Interest in digitalisation/new technologies; Excel/PowerPoint,"
                " negotiation-level German/English, analytical/conceptual and "
                "technical understanding expected."
            ),
            (
                "Learning willingness, independent work, engagement and "
                "teamwork expected; initial Power BI/database experience "
                "desirable, not mandatory."
            ),
            (
                "This location requires a prescribed compulsory-internship "
                "component."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers. Labels do not separately establish payment amounts or "
                "individual eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Esslingen am Neckar, DE. Published "
                "schedule: Full time. Offering: Temporary. Structured "
                "advertised start(s): 2027-03-01. Listing-publication end(s): "
                "2026-11-11; publication end is not a supported application "
                "closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242118": (
                "e724752e564aee90479331bdae685286"
                "a870dd8933f6183b374384cbedf06e4e"
            ),
            "242115": (
                "947f17ed6839b0c2c0e84596ee150538"
                "0d74a3f2dcc9e1f0919b6b6b4bf7f5a6"
            ),
            "242113": (
                "b183e9cb2fa3a9a7d9f41ec7dd2d1e6e"
                "84f781074d94b61e9d51394bbf8a8071"
            ),
        },
    },
    "mer00048qb": {
        "summary": (
            "Economics, business, finance or related studies; German and "
            "Excel/PowerPoint. A mandatory internship component is required. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048qb",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240337": (
                "81b6659f70c91f0dd3a13b8941d79e64"
                "1e6fae20ed9a0ccf75269c78e0e8a553"
            ),
            "240336": (
                "c8c460beda68f11511be46059e42c29c"
                "657006215de84906990cda90216a24e4"
            ),
            "240334": (
                "7f6dd05bccb1de780949faeab52505af"
                "bb3c2f034e74412a8b55cec0825ed141"
            ),
        },
    },
    "mer00048qh": {
        "summary": (
            "Market-research internship in Stuttgart for market research, "
            "psychology, sociology or related students. Requires strong "
            "German/English, MS Office, basic quantitative/qualitative "
            "social-research methods and statistics, plus analytical and "
            "communication skills. Six months from late February 2027; must "
            "include a compulsory component. Submit CV, cover letter, "
            "certificates, current enrolment showing semester, "
            "standard-duration evidence and applicable compulsory-placement "
            "proof. Structured start 22 February."
        ),
        "condition_evidence": [
            (
                "Study in market research, psychology, sociology or comparable "
                "course. Basic quantitative/qualitative "
                "empirical-social-research methods and statistics expected."
            ),
            (
                "Very good German/English, MS Office, "
                "structured/logical/critical thinking, numerical aptitude, "
                "communication/teamwork, care and learning willingness "
                "expected."
            ),
            (
                "This location requires a compulsory-internship component. "
                "Advertised activity may begin late February 2027 and is "
                "fixed-term six months."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Stuttgart, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2027-02-22. Listing-publication end(s): 2026-11-11; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242224": (
                "9e116abb15e83b04ed99530a6a844853"
                "a2c3e12e0eb595c7ba31d0ef7bbe865e"
            ),
            "242223": (
                "1380c565d96e535f9adbfa923aecbb79"
                "87f4b777a56673bfd3d942fa6ced7317"
            ),
            "242221": (
                "47232cbc02dc5249babb316ac5651a3c"
                "dfecfac8d3bf7ac8163a34c371c0f60c"
            ),
        },
    },
    "mer00048qu": {
        "summary": (
            "Current technical university studies; Office. Siemens NX and "
            "automotive experience advantageous. Six months,40 h/week from 11 "
            "January2027; stated gross full-time pay EUR 2243/month. Listed "
            "benefits: coaching, health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048qu",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "239929": (
                "942d33ee26a59700ceddbbcf620b1d0b"
                "52a64846dff7daf1c63ceb5d93e26ea5"
            ),
            "239928": (
                "24efc7a909b9b92b440bfe46eeb33582"
                "a711da5f4c91a9bcda5e57063186cb53"
            ),
            "239927": (
                "1e2f6de2227d6b85b667e5951c063006"
                "7d0f4893c164d98a09f5930ada6a8152"
            ),
        },
    },
    "mer00048r2": {
        "summary": (
            "University-lecturer dissertation supervision required for hiring; "
            "candidate selects supervisor, with topic jointly agreed. Completed"
            " AI, simulation, aerospace/automotive/mechanical engineering or "
            "related degree; substantive AI/ML and Python/Java/SQL knowledge, "
            "Office/Linux and German/English. FE/multibody simulation "
            "experience and NVH basics are advantageous. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048r2",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239974": (
                "5d0c0e39c91cb5968d1991ec166fbc88"
                "0a07edc8192e4ebb0b666084981354a1"
            ),
            "239972": (
                "4745a694d2896cd70bfb86c40ec807c8"
                "530d2e18c24d1e9560ce6c333a23d414"
            ),
            "239971": (
                "e6b8984f8e4dd044d448ceb51c60c557"
                "a12fda3e1967c4039e14e6c2da88181f"
            ),
        },
    },
    "mer00048r6": {
        "summary": (
            "Economics, business or related studies; German/English and Office."
            " Presentation-document experience is advantageous. Listed "
            "benefits: coaching, health benefits, possible hybrid work, "
            "possible flexitime. Study proof; voluntary internships within "
            "regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048r6",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240150": (
                "75a958c665ca9cea8453b5169ccd683a"
                "a36cd653a83442a20f05515bc6cb5e8c"
            ),
            "240147": (
                "3c4239dd8d5e45fa447243171d560a83"
                "b8e467eaf772b0f1acf63e3a5ce71437"
            ),
            "240145": (
                "302422e9bfab77f9f14f28e44932dc74"
                "3b39d75f93dd807ecdb5853e598b4fe4"
            ),
        },
    },
    "mer00048rr": {
        "summary": (
            "Intended for a recent graduate; assignment expected to last at "
            "least one year. Relevant communications/media/marketing, "
            "automotive/engineering/design/business bachelor degree; recent "
            "graduate or early-career applicant with relevant project/portfolio"
            " experience. Digital/video-content, automotive and Office skills "
            "required; editing tools, platform/EV knowledge and content "
            "portfolios preferred. Listed benefits: coaching, health benefits, "
            "parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048rr",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239635": (
                "3c21ed870739d2b71737626c545602f3"
                "f83b65e68f15c17011da75db81ce83fa"
            ),
            "239634": (
                "979ea4bc500137c8025c0160375a8487"
                "a0a2a8cdba1a048fd5185228d264b2da"
            ),
            "239633": (
                "ae3326b18644435b8fd0968bec3df5d3"
                "c8174a85633c1bd9f60cd87395c8c805"
            ),
        },
    },
    "mer00048rs": {
        "summary": (
            "Current/recent relevant business, automotive management, "
            "analytics/economics/finance/marketing bachelor/master studies; US "
            "work authorization and availability for a12-month hybrid "
            "assignment. Excel/research/presentation skills required; prior "
            "experience, dashboards and AI/automotive exposure are preferred. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048rs",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239632": (
                "c754327c5b5fe2bebbcdc81c2d8e3a41"
                "1099623d7879c63e2797380de28012a6"
            ),
            "239631": (
                "a2f2d85cf399367514d26e03ff993890"
                "90bc16ca609dcd904a3acd4cacb78433"
            ),
            "239630": (
                "70d939b37dd7454b59f9e4809c48a2b7"
                "c0e4e56cb7536bcaeb6d2774954ce2f9"
            ),
        },
    },
    "mer00048s3": {
        "summary": (
            "Science/engineering or related studies focused on processes, "
            "materials, mechanical engineering or lightweight design; "
            "German/English, Office/CAD and a category-B driving licence. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048s3",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239980": (
                "3185f54680d1f10fd024f4d080eea9b1"
                "dd03ebb24c4be37c3ec27bc7e9f35fa3"
            ),
            "239983": (
                "db343da1585bcdc91ce6636a42ce210c"
                "e5c2b8d7aa386998dddba8f70cab8417"
            ),
            "239981": (
                "77d167dcb5db79775fa1dc9151f797bf"
                "823c0e78e28f9274df3d121ccfe0022a"
            ),
        },
    },
    "mer00048s5": {
        "summary": (
            "Current computing/electrical/automotive/mechanical/industrial "
            "engineering or technical-business studies; strong German, good "
            "English, Office and valid class-B driving licence required. "
            "Project, testing, data-analysis and agile-tool experience "
            "desirable. Benefits: coaching, health benefits, parking, possible "
            "hybrid work/flexitime. Study proof; voluntary internships within "
            "regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048s5",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240285": (
                "a912203df711e536dc51eb706c029ecc"
                "d477785b255832f139a920be2d121eae"
            ),
            "240282": (
                "f26a5189ffeecc93044a37568c214c83"
                "f45d1bbff26e69bc5296e3ebd1e478a7"
            ),
            "240281": (
                "caf09ac2423fe38c462c8bd0871bd04b"
                "c48ec1c431855b4faface560dff0bf96"
            ),
        },
    },
    "mer00048sa": {
        "summary": (
            "Automotive/mechanical engineering or related studies; "
            "German/English, Office and CAD knowledge. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048sa",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "239989": (
                "b1c7a65933c04d42e1f87c4e561a2940"
                "b78d2f0887eafa9c76a0daedd2ca47ee"
            ),
            "239988": (
                "2789583c60fd6e652eebfa4dbcb180df"
                "65bec2649399b96cf77a8c76446511c0"
            ),
            "239987": (
                "f2889ae29e7cc75068a8967956f5e2ce"
                "5cbdd113cb9293c7484fcf9017864013"
            ),
        },
    },
    "mer00048sg": {
        "summary": (
            "Automotive, IT, electrical engineering or related studies; "
            "German/English, Office and a category-B driving licence. "
            "DTSMonaco/CANoe, safety-training evidence and Mercedes familiarity"
            " are desirable. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Excellent "
            "grades; regular-study duration+max3 semesters, open exams;7–20 "
            "h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048sg",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240172": (
                "ca1ad0762a7c50b1589a2a6c90b833b1"
                "dd9ddf8e890ffa3bdc12679b08dec792"
            ),
            "240170": (
                "899dcdab4ac0ef89ec5e3d3246452151"
                "76385be76eea9f3a0c5c68853a4feac3"
            ),
            "240169": (
                "3f7804efdd81ecb3b93eca28a5271761"
                "7c046feb52f8cca1771276edba66c0ed"
            ),
        },
    },
    "mer00048sl": {
        "summary": (
            "International management, data science, industrial engineering, "
            "computing or related studies; German/English, Excel/Pivot, "
            "Jira/Confluence and scripting. Regular onsite collaboration around"
            " Stuttgart required; AI experience advantageous. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048sl",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240645": (
                "cbd34d95b387a2cc66c1acf4e3037e12"
                "6037e7f32916f977c48bbc6f0ffa5b9b"
            ),
            "240651": (
                "8d25e4f53fa101098f8fa244c808731b"
                "47a3a83a2b315035acbda97e53f9e1d4"
            ),
            "240650": (
                "4de1eaec2eb6d1c7a59fefee087ffcf2"
                "171f79a2ddf913c9487e4dabe859932b"
            ),
        },
    },
    "mer00048sx": {
        "summary": (
            "Mechanical, IT, electrical engineering or related studies; English"
            " and Office. Mercedes familiarity and a category-B driving licence"
            " are desirable, not required. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048sx",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240398": (
                "a4a91d74d6affbce7831cd8d5c2c4d26"
                "8d7402b873108d3768d7645e7dfd482b"
            ),
            "240395": (
                "b7dc5b58fdc42068670312a1cc8b40b3"
                "add61f6ba538526288f24f72e1bac977"
            ),
            "240394": (
                "e544c2ad62c8b9cad374ba300caee152"
                "cd0c078b3169fdf9ef86134f1c36995f"
            ),
        },
    },
    "mer00048t3": {
        "summary": (
            "Mechanical/manufacturing/materials engineering or related studies;"
            " German/English and Office. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048t3",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240055": (
                "8b75e83e3a7d05a52ca739067d10a6d8"
                "4d4bfd769bbc3a50f5c8a7d6abc5cd58"
            ),
            "240050": (
                "13d262f4c80a122986d4b442b6b28465"
                "ea5e257504516f98d2414e83ed86d35c"
            ),
            "240049": (
                "8c1e77068aac8c6efd808dea932524e8"
                "dabad368094f3c879042e2e88ea50fb9"
            ),
        },
    },
    "mer00048t4": {
        "summary": (
            "Industrial/mechanical/electrical engineering or related studies or"
            " equivalent education/experience; analytics, "
            "PowerBI/Excel/databases and coding experience. Non-US university "
            "enrolment or bachelor–master gap, at least 3 semesters and 6 "
            "months required. USD 18/hour; visa costs covered; USD 700 housing "
            "bonus. Required application documents include enrolment proof. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048t4",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239848": (
                "5ac55cd4e40fa86e2fc61efdf42dab34"
                "ac6d1882a90999cec1b07a9d7bc3243c"
            ),
            "239847": (
                "94a6e9846f2e8c921226ce02f4150fd4"
                "f322265f002bfd169748943f216590e1"
            ),
            "239846": (
                "a3bf258ceb78eda1a2cac20f2e4fc570"
                "e3c1866c11fc5267b24bceddb8174cd8"
            ),
        },
    },
    "mer00048t5": {
        "summary": (
            "Electrical/mechanical/industrial engineering or related degree or "
            "equivalent education/experience; manufacturing/quality, "
            "data/reporting and project skills. PowerBI/data visualization "
            "preferred. Non-US university enrolment or bachelor–master gap, at "
            "least 3 semesters and 6 months required. USD 18/hour; visa costs "
            "covered; USD 700 housing bonus. Required application documents "
            "include enrolment proof. Listed benefits: coaching, health "
            "benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048t5",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "239851": (
                "d107d177ba1b916ffced43d54d7cb625"
                "56471505c4bb79c623992d76075344e2"
            ),
            "239850": (
                "9686a13496ff65135f4ec17d0e1b9a44"
                "90f3aabd0e9a0422fc2511b6d7d7e71a"
            ),
            "239849": (
                "1dcf1411cff733da33f963aa338f48a0"
                "9d7c413b60823020ec37d4d36498be2b"
            ),
        },
    },
    "mer00048tb": {
        "summary": (
            "STEM studies, data-structure/filter-logic understanding, Office "
            "and German/English. Jira and coding/JQL knowledge are "
            "advantageous. Listed benefits: coaching, health benefits, parking,"
            " possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048tb",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241544": (
                "095e7c0ea1dabfa159a92cb9953e74f9"
                "63dbb17a70f0dff4e0123e3da947e1f4"
            ),
            "241543": (
                "61467afebc3f777761a4b54ba6a688a4"
                "22da3f5481f00f9ea909b7f42d20182b"
            ),
            "241540": (
                "a14fc4db0d14cfa176b7af8bcfaf5ebb"
                "ff60f3e9b7c1966a275ca0e15be83763"
            ),
        },
    },
    "mer00048tl": {
        "summary": (
            "Automotive/mechanical engineering or related studies; "
            "German/English and Office. Siemens NX design experience is "
            "advantageous. Listed benefits: coaching, health benefits, parking,"
            " possible hybrid work, possible flexitime. Above-average grades "
            "and prior relevant internships; EEA ID/passport, otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048tl",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240137": (
                "9ebc3fe18192834446ca431a0b476b4a"
                "6dca102b4eff5bf51858a2ce9a6e6693"
            ),
            "240143": (
                "b9d1c1931f51d0c885e842b714e223f5"
                "8a001968d4f0b1444c0daba1f07a4021"
            ),
            "240141": (
                "7d4af0d691f3d5a796e7aea77c62aff8"
                "a79b37f750138398d5081136e6015f46"
            ),
        },
    },
    "mer00048u4": {
        "summary": (
            "Business informatics, data/analytics, industrial engineering or "
            "related studies; German/English, Office and familiarity/initial "
            "experience with AI tools such as Copilot, Claude Code or "
            "PowerApps. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048u4",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240118": (
                "68f66232b85a46143abfe771f7e91a97"
                "f9e3507c409117a046e0554a07f1f619"
            ),
            "240114": (
                "279716948db06bd490f54592ec12af45"
                "4540edbf9e352b3650541d0c9d780a11"
            ),
            "240112": (
                "be04fea36cfe0cbff9218ff3702bbaba"
                "e79dfda244735b13a95c8db902ac6258"
            ),
        },
    },
    "mer00048ui": {
        "summary": (
            "Relevant finance, engineering, business, HR, procurement/legal or "
            "supply-chain bachelor degree, or equivalent education/experience. "
            "English, Office and project/interpersonal skills are preferred. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ui",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241590": (
                "88b76e3164368b16e93b8759c6fa665f"
                "c37d3d28dde06338933488c1cead2478"
            ),
            "241589": (
                "58e330ab10142aa7a684b62733b08009"
                "abb0982d3d312bae643285f5c949eb0c"
            ),
            "241588": (
                "db3bd13ddb030a3d83b1e0d588049d4a"
                "b92dec13fb3dbe2f019f6c80f985d3eb"
            ),
        },
    },
    "mer00048uj": {
        "summary": (
            "Procurement/legal or related bachelor degree, or equivalent "
            "education/experience. Fluent English, digital/Office, analytics "
            "and project/interpersonal skills are preferred. Listed benefits: "
            "coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048uj",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241593": (
                "bc04ad13d30099ce236d61f42567d443"
                "98263ce50865690fa91ef4ff9ccd1a59"
            ),
            "241592": (
                "1b1165aabf9945589c45977bf12477b4"
                "3bb07099b5fadea874dedcfc0b5e7414"
            ),
            "241591": (
                "fb029cab0b0d98498746390a3d1b409c"
                "e48c2cbda1642ea967592a2663896218"
            ),
        },
    },
    "mer00048ut": {
        "summary": (
            "Industrial/mechanical/automotive engineering or related studies; "
            "excellent German, good English and very good Office. VBA and 3D "
            "print/design knowledge advantageous. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ut",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240613": (
                "23ed833cd7ceeaf07508fe090cab1c0b"
                "0475023b79b52003ff1b7d78b9726ba8"
            ),
            "240611": (
                "cdae3b7fdd7973d652e51bb5245ec526"
                "40dc98f7fe2dd62e2cd3b06ae186e58d"
            ),
            "240604": (
                "c703ab989d27478dbb38c557e38aa4a4"
                "e25c13ff48ec3fa993db169f38062a87"
            ),
        },
    },
    "mer00048uy": {
        "summary": (
            "IT/electrical engineering or related studies; German/English, "
            "practical software-development experience and Office. "
            "Swift/Python/Docker/Linux knowledge is advantageous. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048uy",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241125": (
                "01a5dec44f63c739690c147335ddf31a"
                "424c3c118e0370e983ed3c9a573fd468"
            ),
            "241124": (
                "b06f938f7e84f93821f79881d290b918"
                "94d7263bfc84c33da2737e76de1e9ca9"
            ),
            "241121": (
                "c25463074d56b3510f13c55b3837e71e"
                "0fe5af889c86ecd6d12c864b54711ea8"
            ),
        },
    },
    "mer00048vd": {
        "summary": (
            "Electrical/mechanical/industrial engineering or similar studies; "
            "negotiation-level German/English and confidence with varied IT/AI "
            "tools. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vd",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240434": (
                "527003295822fc14aacfb99cad0150d7"
                "c7ffb8fe1d5f5a90d34be2ef071d8c1d"
            ),
            "240433": (
                "c7fc52c31d896a4b4c075bf0cfda22fe"
                "2793421150f398a0df3340947943a577"
            ),
            "240428": (
                "3cff003556157cce8fcc96f085d7c0cb"
                "f47c7412713e8a60bff7af51836ad717"
            ),
        },
    },
    "mer00048ve": {
        "summary": (
            "Part-time working-student procurement role in Sindelfingen for "
            "business, industrial engineering or related students. Requires "
            "Excel/PowerPoint, German/English, independent analytical work, "
            "teamwork and stakeholder communication. Initial "
            "purchasing/project-management experience advantageous. Submit CV, "
            "cover letter, certificates, current enrolment showing semester and"
            " standard study duration. Temporary role; structured start 4 "
            "January 2027. Meal discounts and coaching listed; hybrid/flexible "
            "work conditional."
        ),
        "condition_evidence": [
            (
                "Working-student route for business administration, industrial "
                "engineering or comparable students."
            ),
            (
                "Excel/PowerPoint, German/English, independent analytical work,"
                " team commitment and professional stakeholder communication "
                "expected."
            ),
            (
                "Initial procurement or project-management experience "
                "advantageous. Publisher schedule is part-time and offering "
                "temporary."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Mark documents as relevant for this "
                "application; each attachment is subject to the stated 5 MB "
                "limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers. Labels do not separately establish payment amounts or "
                "individual eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Sindelfingen, DE. Published schedule: "
                "Part time. Offering: Temporary. Structured advertised "
                "start(s): 2027-01-04. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "241892": (
                "350cb92e2c710cd80dfdb7cd3035ce98"
                "a23e90c663190b0926f40e84b4e35dad"
            ),
            "241888": (
                "0aad7840f5724ab0fc5a6d88bdd6c098"
                "87dcb4c55ce9fd6e6c1067c0f90e5517"
            ),
            "241885": (
                "160b22b579408a4e02d44823dee7a317"
                "8b96c4e5e2d4274c803c1a919ba6a137"
            ),
        },
    },
    "mer00048vg": {
        "summary": (
            "Economics/business, business informatics/international business or"
            " related studies; strong German/English and Office. "
            "Jira/Confluence, other languages and project/sales/marketing "
            "experience preferred. Mandatory internship component; minimum "
            "duration stated as5–6 months. Prose seeks January/February2026 "
            "versus structured2027-02-01: confirm year. Study proof; voluntary "
            "regular study+3 semesters; EEA ID/passport, otherwise "
            "residence/possible work permit. Benefits: coaching, health "
            "benefits; possible hybrid/flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vg",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240267": (
                "607613779b4d69b2b27116e40b4189eb"
                "aa352b9de0238fe13df0add52636ed64"
            ),
            "240266": (
                "21d3903d2c86157fef6709c6e3d67f48"
                "99c64f0453e8792ee53c00bcf9b1e9f9"
            ),
            "240264": (
                "24b6775122f9999bfb5dace60a044636"
                "57d7ac548ddb6f68ccb2ae1a1ee06514"
            ),
        },
    },
    "mer00048vh": {
        "summary": (
            "Communications, media, economics/social sciences, "
            "journalism/PR/marketing or similar studies; strong German/English "
            "and Office. Communication/media experience advantageous; mandatory"
            " internship component required. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vh",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240201": (
                "8531fd817570091ffe532c0a81064604"
                "5227daf9eb5be97bff507ed530fd8890"
            ),
            "240200": (
                "bb23e1f741915252ff6da8df8c464431"
                "103eaffdb43c682b6324706e6cca904e"
            ),
            "240198": (
                "3a3f79679d07a15875067d4c70af7678"
                "c845449a2d1303165341aa9c931e0192"
            ),
        },
    },
    "mer00048vj": {
        "summary": (
            "Relevant engineering, supply-chain/business/operations degree or "
            "equivalent education/experience; manufacturing/lean, "
            "analysis/project and Office/PowerBI skills are preferred. Non-US "
            "university enrolment or bachelor–master gap, at least 3 semesters "
            "and 6 months required. USD 18/hour; visa costs covered; USD 700 "
            "monthly housing stipend. Required application documents include "
            "enrolment proof. Listed benefits: coaching, health benefits, "
            "parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vj",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240102": (
                "6c21595928e0961450e389a933ffd805"
                "fac2e941c9d66a8361991c4a6172b15e"
            ),
            "240101": (
                "9e021b49f32b98fc95969fde232d2541"
                "ef8c2860033523000a34bf01f05fccd7"
            ),
            "240100": (
                "61d71a6a5bc0261c52d1a18b02e68c23"
                "e3eb1bd4de0a22ec1a57ffa47143a3bc"
            ),
        },
    },
    "mer00048vm": {
        "summary": (
            "Relevant engineering, supply-chain/operations degree or equivalent"
            " education/experience; engineering/manufacturing, digital and "
            "PowerBI skills are preferred. Non-US university enrolment or "
            "bachelor–master gap, at least 3 semesters and 6 months required. "
            "USD 18/hour; visa costs covered; USD 700 monthly housing stipend. "
            "Required application documents include enrolment proof. Listed "
            "benefits: coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vm",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240105": (
                "41683d03725db90740fd0116178b53e0"
                "3a8610b0f976dbc3fb32928a264ff902"
            ),
            "240104": (
                "ab59103ed2248722c79040acc58e7d0a"
                "79411c30b1d6d99662e4a8226e2313f1"
            ),
            "240103": (
                "0ccab67a3d79b98e68b50705db6da602"
                "f4461b8fc2a95a1935fe20d72341d165"
            ),
        },
    },
    "mer00048vo": {
        "summary": (
            "Industrial/electrical engineering, business, computing, "
            "supply-chain or related studies; strong German/English and "
            "Excel/PowerPoint. Semiconductor/project/supply-chain interest "
            "advantageous. Listed benefits: coaching, health benefits, parking,"
            " possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vo",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240321": (
                "0a9618b0e2b2f74596c9cfaff3d2e7d7"
                "3f877dcadc3edc588db21d5e0c6add6e"
            ),
            "240314": (
                "9a54d46407cb914ec6dab270c2d4ac47"
                "5716c43ba61e10a7f9557883db046438"
            ),
            "240313": (
                "c0fc43ef49af85693d730715dddc1dc9"
                "5474826dae2ee920034a0f2e779d1757"
            ),
        },
    },
    "mer00048vx": {
        "summary": (
            "Industrial engineering, economics, business or related studies; "
            "German/English and Excel. Mandatory internship component required."
            " Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048vx",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240620": (
                "58fa850d45da617fe44768f41aaa2510"
                "b2721d80c6dd43581d07f4a3029cd473"
            ),
            "240618": (
                "4989b76a07937e23b47d77ccabf1936e"
                "cb33c3fff3839f1574b6daa7d0d4a79a"
            ),
            "240617": (
                "90858cf610c73284a8bb64a41d45f9d6"
                "b331741ac6edf6fba15791ad1a5b4431"
            ),
        },
    },
    "mer00048w0": {
        "summary": (
            "Business, engineering, mathematics or related studies; "
            "German/English and Office; title identifies a mandatory "
            "internship. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048w0",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241577": (
                "094afcbb8e29055685c1629f3784477a"
                "4bf0f94056832a93cdc77b444301c718"
            ),
            "241576": (
                "9d4577ef80d48c17a7a28e1ebe00858c"
                "44822a97edc73269d8d9c24b9435029e"
            ),
            "241575": (
                "13943f750bc36b1f2b2211cc2ee3c964"
                "266a036217fde87b38c22f7806777c2d"
            ),
        },
    },
    "mer00048w1": {
        "summary": (
            "Computing, business informatics or comparable studies; fluent "
            "German/English and technical/data/AI interest. Initial "
            "Python/data/BI/ML experience is preferred. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048w1",
        ],
        "categories": ["jobs"],
        "language": "en",
        "variant_hashes": {
            "240443": (
                "ba2ddf340f886fc844411cf19707be22"
                "67f2d6ebaf632d596eb7f5d4d664c025"
            ),
            "240442": (
                "90c742a612d8b605286ca2c27ae01ecb"
                "32a3ff2faa9060d336d39c10110a7275"
            ),
            "240441": (
                "07a4aee725ed24722c8e533cad7b0d3f"
                "77cced30e954b8f0a87b07a8b013b8d5"
            ),
        },
    },
    "mer00048w6": {
        "summary": (
            "Transport-logistics/supply-chain internship in Esslingen for "
            "business, industrial engineering or related students; "
            "logistics/materials or supply-chain focus advantageous. Strong "
            "German/English and Word/Excel/PowerPoint required; SAP R3 and "
            "logistics knowledge advantageous. Six-month compulsory internship."
            " Submit CV, cover letter, certificates, enrolment/semester and "
            "standard-duration evidence, plus applicable placement proof. "
            "Full-time temporary role; structured start 1 March 2027."
        ),
        "condition_evidence": [
            (
                "Study in business administration, industrial engineering or "
                "comparable course; focus on logistics/materials or "
                "supply-chain management is advantageous."
            ),
            (
                "Very good German/English and MS Office Word/Excel/PowerPoint; "
                "SAP R3 knowledge advantageous. Existing theoretical/basic "
                "logistics knowledge preferred."
            ),
            (
                "Teamwork, commitment, flexibility, independence, rapid "
                "comprehension, conceptual thinking and communication expected."
            ),
            (
                "Title specifies compulsory internship; task section states a "
                "six-month placement in load-carrier management."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers. Labels do not separately establish payment amounts or "
                "individual eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Esslingen am Neckar, DE. Published "
                "schedule: Full time. Offering: Temporary. Structured "
                "advertised start(s): 2027-03-01. Listing-publication end(s): "
                "2026-10-28; publication end is not a supported application "
                "closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "240310": (
                "2708d0342b5ff55431a0bb46c7d3f0d4"
                "8dd52c88c4aa4a19bfeb8c69b5ca99af"
            ),
            "240311": (
                "ec469e896767977c5cd8ccd0450015fc"
                "03bfaffc88560a185099d7d85dd396b0"
            ),
            "240308": (
                "e8d993adbf63deb7ca1f7df0aaad4e1f"
                "18e2c44b7b2a9f7f890d96625eb2f73d"
            ),
        },
    },
    "mer00048wg": {
        "summary": (
            "Computing, mechatronics, mechanical engineering or related "
            "studies; German/English, Python programming and advanced MATLAB. "
            "Data-analysis knowledge advantageous. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wg",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241403": (
                "6255a2074bdf9ff993eee96137d96de5"
                "85765a56f904b7071c55756f126808e0"
            ),
            "241401": (
                "780f680f8d9dd04f246ba6a37fd973ca"
                "ef06fc4558ff366df1878e001abe4137"
            ),
            "241399": (
                "c3c4e7b17b218be4b94a76d3dccbfa10"
                "af73094bff99ddb71995430307130839"
            ),
        },
    },
    "mer00048wi": {
        "summary": (
            "Industrial engineering, business, media/communications or related "
            "studies; strong German/English and Office. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wi",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241715": (
                "788cc77a2c05d6269114e341609f1c94"
                "67cbd970924a0270f92015e3ed0b0cfb"
            ),
            "241713": (
                "614806ad9ea415c6ae959eeb02bd8f8c"
                "e0ad55462e95350b16518865f48fd557"
            ),
            "241711": (
                "52eccee06baeb2de6c4601b23e2e9b9b"
                "1c36d9909b7963371da735a80abf0ea6"
            ),
        },
    },
    "mer00048wm": {
        "summary": (
            "Marketing, business/economics or marketing-related technical "
            "studies; German/English and Office. Presentation/reporting "
            "experience and a category-B licence are desirable. Mandatory "
            "internship component required. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wm",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240674": (
                "eb72460a09b9fc1ca12989d1ab7cb51c"
                "324b067be1e905a00dd6398824c66e7c"
            ),
            "240679": (
                "2cf6d3cec1c6757bed2c0914f23f2f92"
                "3f02f07a6a2ca2d4c3e63de5224e18c0"
            ),
            "240678": (
                "d32bd837d51af45e240aade52271c05f"
                "46ebfce2d1baa288d7e16c268aa3514e"
            ),
        },
    },
    "mer00048wr": {
        "summary": (
            "Industrial engineering, business informatics, business psychology "
            "or related studies; good/very-good AI knowledge and "
            "process-automation/LLM understanding. Initial automation-platform "
            "experience and Python advantageous. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wr",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240425": (
                "be446555e336d3d6ba3711063f0d4c79"
                "0092798594cfdced3b969681ae578b31"
            ),
            "240421": (
                "e79e20af0aa1762ae3951d78e65f4217"
                "2efa2c2f591a6cd20199dde4be291243"
            ),
            "240419": (
                "08694d684e38fc0e5ec145d61d1122fc"
                "0ffc01628a60a8e709c23de16a4e4297"
            ),
        },
    },
    "mer00048ws": {
        "summary": (
            "International Diplomatic Sales internship in Stuttgart-Vaihingen "
            "for business, economics, international business, industrial "
            "engineering or related students. Requires fluent German/English "
            "and MS Office; further languages, Salesforce/Copilot and "
            "sales/customer-contact experience advantageous. Must include a "
            "compulsory component; minimum six months. Submit CV, cover letter,"
            " certificates, enrolment/semester and standard-duration evidence, "
            "plus applicable placement proof. Start 18 January 2027."
        ),
        "condition_evidence": [
            (
                "Study in business administration, economics, international "
                "business, industrial engineering or comparable subject."
            ),
            (
                "Negotiation-level German/English, MS Office "
                "Outlook/Word/PowerPoint/Excel/OneNote expected. Additional "
                "languages, Salesforce/Microsoft Copilot knowledge and initial "
                "sales/direct-customer experience are advantageous."
            ),
            (
                "Communication/teamwork, intercultural/customer focus, "
                "independent/structured work, analytical/conceptual ability and"
                " interest in digital transformation/AI in sales and automotive"
                " industry expected."
            ),
            (
                "Compulsory internship component required; minimum duration six"
                " months, Stuttgart-Vaihingen location."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Stuttgart, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2027-01-18. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241968": (
                "023141de53d765759acf8c7d6939ea78"
                "dfc15521a7486c9ccd6c48033bcc2ad0"
            ),
            "241967": (
                "5798000f31e9812d7bfe3f3b21118f23"
                "5a9d34b4af3d0e5a4f067a5a22567449"
            ),
            "241965": (
                "af191e69b6f36067e2984aec2904af08"
                "08d9e0926cde95bc8282e75ff0ebd062"
            ),
        },
    },
    "mer00048wt": {
        "summary": (
            "Current business, marketing, events, industrial engineering or "
            "related studies; strong German/English, Office/Copilot and "
            "category-B driving licence. Six months,20 h/week on Wed–Fri from "
            "Nov/Dec2026; EUR 2243/month is the full-time pay benchmark. Listed"
            " benefits: coaching, health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wt",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240640": (
                "0e3c1f14bf07bd000c910452623eea70"
                "ee8fbd9d12d05cb0a15164ec3f2361e2"
            ),
            "240639": (
                "20c250780a23364e881f5e8b72ccce3a"
                "97bf13088cc40428a9120169d2e222a6"
            ),
            "240638": (
                "043bc89ff22d543cdda8dc3ad0b949cb"
                "e17a546ae6853804c0dc29a32c4915ce"
            ),
        },
    },
    "mer00048wv": {
        "summary": (
            "Current electrical/automotive engineering or related studies; "
            "battery basics, strong German and Office. Six months,16 h/week "
            "from Dec2026/Jan2027; EUR 2243/month is a full-time salary "
            "benchmark. Listed benefits: coaching, health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wv",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240538": (
                "534f07066b4606ae5b37d6eeb2f7d60b"
                "740e123ab6b599b3b170243a124417e7"
            ),
            "240537": (
                "18e5fc15bcc82da5ccbb6298e7bda288"
                "64a1151655bd8756c6488637744896ac"
            ),
            "240536": (
                "b3f974b36f780d1ed663a9c6d766cb0f"
                "372e92e8b1d997ee30305f4f530a9c40"
            ),
        },
    },
    "mer00048ww": {
        "summary": (
            "Technical computing, software, mechatronics or similar studies; "
            "German/English and Office/Jira/Confluence. Python/Shell/Java/Linux"
            " skills are desired. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Excellent "
            "grades; regular-study duration+max3 semesters, open exams;7–20 "
            "h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048ww",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240522": (
                "7a68e6e2df4f4b242e655eda0599f5ca"
                "040a540d2c55aa16668bf0788d636b6c"
            ),
            "240518": (
                "a58273c4dc22f099280ecc7ae94feb12"
                "bff8f9cab258f05ee0a642bb8ba1df74"
            ),
            "240516": (
                "d2590ed2793deb0d81c5d1e9c901a09d"
                "b9fad596de4e90c03ecc85fe1ab1e783"
            ),
        },
    },
    "mer00048wx": {
        "summary": (
            "Mechanical, IT, electrical engineering or related studies; English"
            " and Office. A category-B licence and Mercedes familiarity are "
            "desirable, not mandatory. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wx",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240454": (
                "7db104e16169cea363ee243e02d81539"
                "21c44d9bbe6fa68785f06d1bdf1f25a2"
            ),
            "240453": (
                "69f425c8f1948f84cefd64629f1c1f3f"
                "de0ba840523aba2ab900314c331d6ad9"
            ),
            "240452": (
                "26d8eb07a60c18875271634aad8c6ffe"
                "f78c04d2eca2a4ec6786efe2b1c36636"
            ),
        },
    },
    "mer00048wz": {
        "summary": (
            "Current computing/business informatics, communications/data "
            "science or related enrolment; at least six months; German/English "
            "and Office. Content/knowledge-management/Confluence experience "
            "preferred; mandatory internship component required. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048wz",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241572": (
                "be3d9914a0cdb3c98840f4b869151359"
                "afd38e9d02ac11433e3da60d6953812c"
            ),
            "241570": (
                "d98fb4954b6ff3345d1d1d587c12f57b"
                "b69c098536ee9ddcd55e71c46c2d1862"
            ),
            "241568": (
                "097c4b81e5718a5a577c61a3f508c173"
                "b328e5d5d804a3692cf667636fb767ae"
            ),
        },
    },
    "mer00048x0": {
        "summary": (
            "Economics, psychology, social-science or related studies, "
            "preferably HR/organization/management; German/English and Office. "
            "Data/dashboard/digital or AI-process experience advantageous. "
            "Mandatory internship component. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048x0",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240470": (
                "36a98b50971181e444439d0011df95f5"
                "ba6186f57f558d74e3579b650720d1e9"
            ),
            "240469": (
                "5da17ff7292dc04ad94ccfb71861ce99"
                "e9320ba102a37f78a3baf56d9333c039"
            ),
            "240468": (
                "03e8ec30dfeb9492f061a8ab30ad6a13"
                "cccbdc20b0b4ef1056c9a5f614bac05d"
            ),
        },
    },
    "mer00048x1": {
        "summary": (
            "Relevant electrical/electronic/mechatronic, "
            "computing/mechanical/automotive/industrial engineering studies; "
            "German/English and Office. AI/data/automation experience welcome; "
            "coding and Jira/Confluence desirable. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048x1",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240507": (
                "a8ce07d2446e77f0e2a23fb925997fd3"
                "6b867fbba504f772b38aa8940e4d388a"
            ),
            "240506": (
                "3962fc1c3779455a4a61a7b0bcd7963d"
                "f176a176040d685c41caf6fe78bde268"
            ),
            "240505": (
                "65ceb2cddbf25f025324354d40a27771"
                "9a0fc3980e261aa20893a8df36a146c7"
            ),
        },
    },
    "mer00048xc": {
        "summary": (
            "AI, computing or comparable studies; Python/React and "
            "negotiation-level German/English. Generative-AI experience "
            "advantageous; MCP/agents/multi-agent knowledge desirable. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048xc",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240533": (
                "8f664aa68b3427de2ee7fbfde8ebef63"
                "602466ef9f4947f5077f7e148690b90c"
            ),
            "240531": (
                "8065f4023af5e5bb9aa7227c64161d3c"
                "bfbda115096897b4007dceb89ca72d6a"
            ),
            "240528": (
                "0d42598e7b83696fb965a2b5e69d5816"
                "e32e6106b76d885773dc893c76aa2867"
            ),
        },
    },
    "mer00048xg": {
        "summary": (
            "Business informatics, computing/electrical/industrial engineering,"
            " energy management or similar studies; German/English, Office and "
            "EnEffCo. Energy-sector understanding advantageous. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048xg",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240714": (
                "50055fddf020a62bd471038394186fd1"
                "11cca0ae9e50250ea37026925f26b116"
            ),
            "240712": (
                "80741e05c24e0ea6ed4fe7958190f1c3"
                "c15362dbb9810316749c36672e6ff8e5"
            ),
            "240707": (
                "d87d9e9c8516ee5a73be76c7aa62b3a0"
                "d582bc3dc9bc11f154b100d2ed48ce7f"
            ),
        },
    },
    "mer00048xn": {
        "summary": (
            "School/university student or specified transition/service status; "
            "age18+, good German, at least six continuous weeks between10 Jan "
            "and 26 Jun2027. Full-time shift work; listing also states a24-week"
            " fixed term, so duration needs clarification. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048xn",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240818": (
                "7d03d381ab2b197146827cfd90850135"
                "1a84bc75256796d457d9107f3a934e78"
            ),
            "240815": (
                "ac8ab7f51a7f561994b0955c0eaf52c7"
                "fc4442651bfe527dbd2d3606df4cd69a"
            ),
            "240814": (
                "6c09ada9cdd301da40df90f5bcab1c1a"
                "10c8a03e3f68d12826bf7ce36ed52497"
            ),
        },
    },
    "mer00048xr": {
        "summary": (
            "Mechatronics, computing/mechanical/automotive/electrical/control "
            "engineering or related studies; German/English and Office. Initial"
            " technical experience preferred. Listed benefits: coaching, health"
            " benefits, parking, possible hybrid work, possible flexitime. "
            "Above-average grades and prior relevant internships; EEA "
            "ID/passport, otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048xr",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240694": (
                "704f56d23eb6b22f4c1ad63803b1e651"
                "159bb8c9940d5d024748fba0bbe1c08c"
            ),
            "240690": (
                "4d77ae12bf0573e6257a8bc013ad1376"
                "6f3a8291778f1887e5ea90b5b0130675"
            ),
            "240689": (
                "3a7589450d784969c14c490a2d5cc1f8"
                "4eae147298efe697750a41ccf20f63ab"
            ),
        },
    },
    "mer00048xy": {
        "summary": (
            "Current accounting, finance, business, economics or related degree"
            " studies; Excel/PowerPoint/Word and analytical/communication "
            "skills. SAP/financial-system knowledge advantageous. Career-level "
            "label says vocational training while title/body describe an "
            "internship. Listed benefits: health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048xy",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240725": (
                "4fa3340bea0652301aec85c6ec461879"
                "c6337df14e1a4f6c390014acafbc9251"
            ),
            "240723": (
                "37f8a5a7402f8b6c81fed709ed38a908"
                "2f3c04d900f1d82ac51be3d145dc5ba5"
            ),
            "240720": (
                "293cc2713275199e2304df3e70fd8452"
                "2c084b29963c837530b749df05a883a2"
            ),
        },
    },
    "mer00048yf": {
        "summary": (
            "Economics, business, psychology or related studies; "
            "German/English, Office and six months of availability. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048yf",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241324": (
                "ba5a3974236aaa5b89b6b93834c3c57b"
                "f565630c0e85cc2317fbacfc911628a8"
            ),
            "241323": (
                "c46edaa1b1f5481c50f18bbd14d5c009"
                "b2eff4646f425efcd3cb08e9ffda3725"
            ),
            "241322": (
                "b632e96a8c140219ebd7fae9afc70d83"
                "0c4644e10392466868507c42ce0a7af0"
            ),
        },
    },
    "mer00048yl": {
        "summary": (
            "Automotive business, business, engineering, PR/journalism/media or"
            " sales/marketing-related studies; German/English, Office and a "
            "category-B driving licence. Mandatory internship component "
            "required. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048yl",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240700": (
                "6cf6ad3ecf2e3a46c62cc717ec967e06"
                "8523182f424138a07a584e5786c4284b"
            ),
            "240699": (
                "f4b0d6ad88f5fbaa6b31b36fdf1361eb"
                "d82376ec586ee043c8df17ca69422b31"
            ),
            "240698": (
                "dc6ae149a3bfaf698fb65053db76cbc0"
                "ecea6c459643ec1605a0fdc4bc175b30"
            ),
        },
    },
    "mer00048yo": {
        "summary": (
            "Current mechanical/technical cybernetics/industrial engineering or"
            " related enrolment with foundation studies completed; "
            "German/English and Office. MATLAB/Simulink, dSPACE/Python and "
            "measurement/control knowledge are preferred. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048yo",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240722": (
                "42b044b3bdf314cd3e66799a297bbad2"
                "b37f89c9b2940e25af72650f6386fc73"
            ),
            "240719": (
                "a659eed5a4b346190a3e369310c57b7e"
                "ce83034a08e435ddefdc64db89c323ab"
            ),
            "240727": (
                "14da4d0dbb33dbd0720bd2a75ea306f3"
                "c11feb8344ca8770561039a01288731d"
            ),
        },
    },
    "mer00048yx": {
        "summary": (
            "Good Realschule-level school qualification; commercial/technical "
            "understanding, English and customer communication. Apprenticeship "
            "starts15 Aug2027 by title. Listed benefits: coaching, health "
            "benefits, possible hybrid work, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048yx",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "240490": (
                "8b2df9ac88a42ab3ab8b0ab14e3f9ef5"
                "35dc815edfd235d93c917d2006604b80"
            ),
            "240489": (
                "b729998c2b61e16c7eef64a6e0cc20b1"
                "1b4f173c8ed342cead7dd35cfc768aec"
            ),
            "240488": (
                "f352f1e5e6ec9fc1e6d2aa4fe6250a88"
                "eeec13ffc7df42765285923bd3b2e3c5"
            ),
        },
    },
    "mer00048yy": {
        "summary": (
            "Part-time working-student IT role in Böblingen for computing, "
            "media informatics, information design, corporate communication or "
            "related students. Requires German/English, MS Office, teamwork and"
            " analytical/strategic work. Submit CV, cover letter, certificates,"
            " current enrolment showing semester and standard study duration; "
            "mark attachments as relevant and observe the 5 MB file limit. "
            "Temporary role; structured start 1 December 2026. Meal "
            "discounts/coaching listed; hybrid/flexible work conditional."
        ),
        "condition_evidence": [
            (
                "Working-student studies: computer science, media informatics, "
                "information design, corporate communication or comparable "
                "course."
            ),
            (
                "German/English, MS Office, commitment/teamwork and "
                "analytical/strategic work expected; publisher schedule "
                "part-time and offering temporary."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Mark documents as relevant for this "
                "application; each attachment is subject to the stated 5 MB "
                "limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Böblingen, DE. Published schedule: Part "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2026-12-01. Listing-publication end(s): 2026-11-10; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "241900": (
                "b0285723ac4a032ceeb22b6f939397ff"
                "64c5aa307159a598249d5010288f633b"
            ),
            "241897": (
                "3bed39798e8654d82300cc6e401b80d1"
                "62aaba28a8e0d3592eabf21f5201a52d"
            ),
            "241896": (
                "736a9eea5f13dcbe4d73d9ddda216643"
                "4141f136a9ba61a7fdb4f8f4da4409e4"
            ),
        },
    },
    "mer00048yz": {
        "summary": (
            "Mechanical/industrial engineering, business or related studies; "
            "initial automotive/production/planning/project experience, strong "
            "Office and German/English. PowerBI advantageous. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048yz",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241548": (
                "3afd06021b810653d226f1629a854386"
                "4a44459187372a411c6959b0de95ef8d"
            ),
            "241547": (
                "83d4b0e5ad77380d379b0308d634483d"
                "88de26942ef0d466da598d4d2d6cdb74"
            ),
            "241546": (
                "8ab7bf304302ace7637ae0634feb88ba"
                "71b8ea036671d29c4df241f799f3ee5b"
            ),
        },
    },
    "mer00048z1": {
        "summary": (
            "Relevant engineering/operations degree or equivalent "
            "education/experience; manufacturing/process/data/statistical "
            "skills and PowerBI are preferred. Non-US university enrolment or "
            "bachelor–master gap, at least 3 semesters and 6 months required. "
            "USD 18/hour; visa costs covered; USD 700 monthly housing stipend. "
            "Required application documents include enrolment proof. Listed "
            "benefits: coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048z1",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240526": (
                "df211174a047056f3c418bf1b5bde364"
                "fb4d61a716b177933ed0f7ed3f39276e"
            ),
            "240525": (
                "9bd661533656cac6621614c6232b5148"
                "b05930686dcb92acaef17709da016c89"
            ),
            "240524": (
                "5af478c9fd25e422a51c2ebaedb0b2f0"
                "42138287bf092ea45b94087091803b54"
            ),
        },
    },
    "mer00048z2": {
        "summary": (
            "Mechanical/mechatronic/electrical/industrial engineering, business"
            " or related studies; German/English and Office. Coding "
            "advantageous but explicitly not required. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048z2",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241518": (
                "85a952666a0ff77bad335215014af476"
                "175cd658faa2e80d13afb1c680d777a2"
            ),
            "241517": (
                "a312a984cffb9ebb774596ba64496995"
                "0f6f6e807371d2ad6d1fb1421148f39d"
            ),
            "241524": (
                "93a9f258bb2506a8e4665e6206023c4e"
                "a31e52a049a6121db6b2997f73e37454"
            ),
        },
    },
    "mer00048z3": {
        "summary": (
            "Industrial engineering, business or related studies; "
            "German/English and Office. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048z3",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241047": (
                "7d66d3098efa87322a8e95cd78f8274a"
                "ddefdde205ed55511b5d15217b395030"
            ),
            "241044": (
                "c80463584fe8bae5c70d3d88b759144f"
                "cf84a9227a636947e650b4d9a124ba08"
            ),
            "241042": (
                "c904b5714b2ca3279dc36436c311f1dc"
                "db2d740e6b40331d8a046b940a9d476b"
            ),
        },
    },
    "mer00048z8": {
        "summary": (
            "Canada work eligibility without visa sponsorship; "
            "business/finance/accounting graduate in 2025 or bySeptember2026, "
            "transcripts at onboarding, Office and communication skills.40 "
            "h/week, onsite2–3 days; CAD 21.49/hour; contract ends7 July2027. "
            "Preferred French; structured start differs from body's "
            "“immediately”. Listed benefits: health benefits, parking, possible"
            " flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048z8",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240548": (
                "5fb96c96e8462ab80b2e3f487abfb262"
                "a71532b1813b1f94eb32e48344246c74"
            ),
            "240547": (
                "79e3561550649e5657de82db74a22fdb"
                "af829e03c0189f83e5fd9536d5be1d3b"
            ),
            "240546": (
                "45a480f505327bade87d10b3137cf5cb"
                "fa86a9de7fce2dcabd510c818124c81a"
            ),
        },
    },
    "mer00048zn": {
        "summary": (
            "Rising junior/senior business-related undergraduate or graduate, "
            "preferably marketing/business/project management; project and "
            "stakeholder skills. Nov2026–Oct2027, three onsite days/week, "
            "maximum28 h and 10:00–15:00 core availability. Listed benefits: "
            "coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048zn",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240792": (
                "434680cf52a8649574c49df7fc90b308"
                "be0255cb1ae4cfb15296e8fb3699a215"
            ),
            "240791": (
                "20960687c5ca1ba5985b1135d31b16f3"
                "4593f65facef037e4d41b89418379d95"
            ),
            "240790": (
                "3a4dd5ed363b907248c9e053ff9ac5a9"
                "967dd0b67f405df00dc64b03f1d660ca"
            ),
        },
    },
    "mer00048zx": {
        "summary": (
            "Active full-time Hungarian university enrolment in "
            "business/HR/management/projects or related studies; native "
            "Hungarian, German or English, Office, at least six months/three "
            "days weekly. Hungarian and German/English CVs requested. Transport"
            " reimbursement and other benefits subject to company rules; home "
            "working depends on role."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer00048zx",
        ],
        "categories": ["internships"],
        "language": "hu",
        "variant_hashes": {
            "240599": (
                "551ec1bd8df957414c47eb25c45089a2"
                "2d84d7c215ebf899200cbb349901e13d"
            ),
            "240598": (
                "7e610281b7deecd41231917bfea878e6"
                "b766f1892c5518bce65471c2ec49f50b"
            ),
            "240597": (
                "9d30ef725b2a1b1ae6eef7cc1c895417"
                "20fcc0bac8348aa4f3eb7eba8d229949"
            ),
        },
    },
    "mer0004900": {
        "summary": (
            "Computing/electrical/business-informatics or related technical "
            "studies; English, Python/JavaScript/TypeScript basics and Git. "
            "React ideal; Jira/Confluence/Mural and Office are nice-to-have. "
            "Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004900",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240923": (
                "78e11bf8d19f168bd1bc5ead3f2a4118"
                "6e93b6cfeb7f8c97c148c75f5e4d0a86"
            ),
            "240922": (
                "a662792fdcde33a59d0640386686c7b2"
                "0962591e62da96428147ddef7fe025e3"
            ),
            "240921": (
                "2582f70de543acec9bdeee87cd614cdd"
                "6a02dec3871d90aea928bf492dff183a"
            ),
        },
    },
    "mer0004903": {
        "summary": (
            "Vietnamese citizenship and legal work eligibility; relevant "
            "undergraduate enrolment through April2027, six-month availability,"
            " Vietnamese/English and Office. Mandatory stamped enrolment letter"
            " with graduation date plus CV. CAD/presentation skills "
            "advantageous. Listed benefits: health benefits, parking, possible "
            "hybrid work, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004903",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240801": (
                "1702066dd90486115c2e9917ca3ddeef"
                "960833cc8d4be8ba17c757d901c565f6"
            ),
            "240800": (
                "d23069d6c2e261a892f831cf4fb6a6b4"
                "de7d792dcb01ba70223d16723d2d5cfe"
            ),
            "240799": (
                "53c1bc8557ba8c3487be8495ec02966b"
                "8cf6e5147a9fbc3743003d3b223ebb03"
            ),
        },
    },
    "mer0004909": {
        "summary": (
            "Economics/business/sales/marketing/international business/business"
            " informatics or related studies; strong German/English and Office."
            " Minimum five-to-six months; mandatory internship component. Extra"
            " languages/project/sales/marketing experience preferred. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004909",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241058": (
                "e4ef96de96503cbc5442136435aaf68f"
                "0bd607f6e3c54ff11198bbe95d23a54b"
            ),
            "241056": (
                "4b423a5db01c87a0bb3602e09c60bc2c"
                "a48801948e22cb355f5a7cc27b0b101a"
            ),
            "241054": (
                "1926c4e0fa350ec9d8b06ec4f058e045"
                "9a8ace6940bac8393bc4bd0cfdbbdf2f"
            ),
        },
    },
    "mer000490a": {
        "summary": (
            "Industrial engineering or related studies; German/English and "
            "Office; independent analytical work. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490a",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241720": (
                "56699fb21cc156b67002467a8f2eb78e"
                "b088c399d7a56fe28b8ee4c37afc77fa"
            ),
            "241718": (
                "6fa185a91b112cd65b9c1ab6fa53f015"
                "12b2fa5068bad392a28b61f6f30e24a9"
            ),
            "241716": (
                "7094c307e736ad8efbb79d6f2bc2aa62"
                "8e44828bf5db69d61365a439bad63a7a"
            ),
        },
    },
    "mer000490b": {
        "summary": (
            "Mechanical/automotive/industrial engineering or related studies; "
            "strong German/English and Office. Project experience and "
            "electrical/electronic architecture familiarity desirable. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490b",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240749": (
                "bf5eaac04cf6cb7b5c8b455f5ac838ff"
                "82e25a7c73c2492a26fc3a46e1faace0"
            ),
            "240745": (
                "a75093ef6d07a1c1e82e5cc89f3222e8"
                "3eabdba3ffc5b48069956d4937b7d4d9"
            ),
            "240744": (
                "da95aa27c2d14f0f4134be48ec371bf0"
                "154da4b254fd7e6a1d244745ce7dcdcb"
            ),
        },
    },
    "mer000490g": {
        "summary": (
            "Good intermediate school qualification; technical/manual aptitude,"
            " mathematics/chemistry/physics and reliable colour vision. "
            "Apprenticeship starts1 Aug2027 by title. Listed benefits: "
            "coaching, health benefits, parking, possible hybrid work, possible"
            " flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490g",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "240736": (
                "80786b21eb972483769517b948b15e31"
                "c1703ff218cb820f45e129eda92f5477"
            ),
            "240735": (
                "df521c3670885dc320849cc67c95968e"
                "7d534fc07d07b0dd9abd0e8e5334b7cc"
            ),
            "240734": (
                "1d1ac5bb7c901c939b9688396571f6a5"
                "996d912e74d3bb9edfbd48419488c577"
            ),
        },
    },
    "mer000490l": {
        "summary": (
            "Sound/audio/automotive/electrical/computing/mechatronic studies or"
            " comparable training; vehicle/project, software, user-interaction "
            "and product-design experience; German/English and Office. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490l",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "240837": (
                "92194735f47fa089b77d3591b50bfafa"
                "0e2e9ed2be6a0740f5c79c868fc16a7e"
            ),
            "240835": (
                "091b98a82af0042ab38d8a69e96b8ca1"
                "8fbf7702f2e7b1d074f629275da8e8ea"
            ),
            "240834": (
                "a5eda4316a78d75001e44b6e64a460b8"
                "5ddbab3b8ae9d776d0d0c8b6f7ea4bcd"
            ),
        },
    },
    "mer000490m": {
        "summary": (
            "Sound/audio/automotive/electrical/computing/mechatronic studies or"
            " comparable training; vehicle/project, software, user-interaction "
            "and product-design experience; German/English and Office. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490m",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240824": (
                "1054143142f1c9084e37ce4f8b86fb92"
                "2aef03dedd1fbcd5b8f57b0a36a7fc1a"
            ),
            "240823": (
                "8084c2389ac46b4e08ec92b922ccd49f"
                "102b86df063d141ff8d0bb16164c5462"
            ),
            "240822": (
                "15ed595338bcc6e407ef7f99e119166e"
                "5430c4a6af64331b0936cc7206ac0e9d"
            ),
        },
    },
    "mer000490n": {
        "summary": (
            "Business, economics or related studies; accounting/valuation "
            "basics, Office, strong German/English and initial AI-tool "
            "experience. Mandatory internship component required. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490n",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240849": (
                "18187fb47db91e11b6cfd3b5430c5e5f"
                "56fbfbc7c7869ed5ebbe8a929538c78c"
            ),
            "240848": (
                "2379f99795ff252f372b77d2152eb4b4"
                "e554c094f1f41eac899fe36e1c5e991b"
            ),
            "240845": (
                "712ac6631a8c02bb4748714c16cdd23f"
                "4801cb06b44be204fd4895e337a50393"
            ),
        },
    },
    "mer000490r": {
        "summary": (
            "Supply-chain or related degree or equivalent education/experience;"
            " English, Office and analytical skills preferred; "
            "supply-chain/warehouse knowledge optional. Non-US university "
            "enrolment or bachelor–master gap, at least 3 semesters and 6 "
            "months required. USD 18/hour; visa costs covered; USD 700 monthly "
            "housing stipend. Required application documents include enrolment "
            "proof. Listed benefits: coaching, health benefits, parking, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490r",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240768": (
                "a291df8e1905138b692756c3f26b2e50"
                "e6b239a2d87deadf977d53b973499b34"
            ),
            "240767": (
                "10c55f8f92b50c74e599e49d4f193631"
                "34ece6a86f4ecfc83408e4dd0fe6cd84"
            ),
            "240766": (
                "b1d005e162bcf4bb4089c516a5388e45"
                "d0cf38e813e4bf696de5e74bf512494e"
            ),
        },
    },
    "mer000490v": {
        "summary": (
            "Economics, social sciences or related studies, preferably HR; a "
            "combined internship as part of studies, negotiation-level German, "
            "Office and a category-B driving licence. Title gives Feb/Mar2027 "
            "start. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490v",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241108": (
                "5896831fb1add46f56feff0d1aaa0488"
                "7f93181cde56b48a5e83ca8c32e99018"
            ),
            "241107": (
                "fdd82852631c53ac5d6af7c93d5f3a73"
                "6a1e1caca65e2efe9441cec08cef4561"
            ),
            "241106": (
                "39bf3235763011ee7a6303de4ba2c84b"
                "82e6ec6d4cbf5443cfbc11e586dfb019"
            ),
        },
    },
    "mer000490w": {
        "summary": (
            "Six-month pedal-system testing internship in Hamburg for "
            "mechanical/automotive/electrical engineering, mechatronics, "
            "physics or related students. Requires German/English, MS Office, "
            "teamwork and analytical/strategic work; practical craft aptitude "
            "advantageous. Submit CV, cover letter, certificates, current "
            "enrolment showing semester and standard study duration; "
            "compulsory-placement proof where applicable. Full-time temporary "
            "role; structured start 1 March 2027. Meal discounts/coaching "
            "listed; hybrid work conditional."
        ),
        "condition_evidence": [
            (
                "Study in mechanical engineering, automotive engineering, "
                "electrical engineering, mechatronics, physics or comparable "
                "subject."
            ),
            (
                "German/English and MS Office expected alongside commitment, "
                "teamwork and analytical/strategic work; practical/manual craft"
                " aptitude advantageous."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, nearby childcare, parking, canteen/cafe, "
                "public-transport access, accessible workplace, workplace "
                "doctor. Labels do not separately establish payment amounts or "
                "individual eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Hamburg, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2027-03-01. Listing-publication end(s): 2026-11-11; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
            (
                "Task section explicitly describes the placement as a six-month"
                " internship, covering pedal-system testing and "
                "evaluation/documentation of laboratory test series."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242112": (
                "50d94c60bbd0b7f6ca742eaa48057559"
                "d1ab733c633f6be6753a8693a0f94eac"
            ),
            "242109": (
                "79fcf3fd869745628be9f78b5daefced"
                "05908e98ab16881acdc874e1f8cc6f6b"
            ),
            "242108": (
                "2a5c6d452cd754cfc91666b8283edb66"
                "d6c8c5432e29d847f0068d1f8d39685d"
            ),
        },
    },
    "mer000490x": {
        "summary": (
            "Development project-management internship in Hamburg for "
            "mechanical/industrial engineering, engineering or natural "
            "sciences, automotive, production or plant engineering, or related "
            "students. German/English and MS Office required; organisational "
            "and analytical ability advantageous. Submit CV, cover letter, "
            "certificates, current enrolment showing semester and standard "
            "study duration; compulsory-placement proof where applicable. "
            "Full-time temporary role; structured start 1 February 2027."
        ),
        "condition_evidence": [
            (
                "Study in mechanical/industrial engineering, engineering or "
                "natural sciences, automotive, production/plant engineering or "
                "comparable studies required."
            ),
            (
                "German/English and MS Office required; engagement, motivation,"
                " communication/teamwork expected. Organisation and "
                "logical/analytical thinking advantageous; project-work "
                "interest desired."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, nearby childcare, parking, canteen/cafe, "
                "public-transport access, accessible workplace, workplace "
                "doctor. Labels do not separately establish payment amounts or "
                "individual eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Hamburg, DE. Published schedule: Full "
                "time. Offering: Temporary. Structured advertised start(s): "
                "2027-02-01. Listing-publication end(s): 2026-11-11; "
                "publication end is not a supported application closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242162": (
                "7ed325100488256279f0888572d7679f"
                "e792e642d19f48b334ef68ae7f3099af"
            ),
            "242164": (
                "8879b43bd7d97bb8b517117a14e3987d"
                "dbed70c0fc2bf3cfb3a4059b0c8eba78"
            ),
            "242163": (
                "c520530cd12569c0d80031525ecf0520"
                "51940b282daab3057be8f69c97d60fac"
            ),
        },
    },
    "mer000490y": {
        "summary": (
            "Market-management/sales internship in Stuttgart for economics, "
            "business, automotive business, international business or related "
            "students; sales focus preferred. Requires strong German/English "
            "and Excel/PowerPoint, intercultural interest and analytical/team "
            "skills. Category-B driving licence advantageous. Must include a "
            "compulsory component. Submit CV, cover letter, certificates, "
            "enrolment/semester and standard-duration evidence plus applicable "
            "placement proof. Start 1 February 2027; part-time possible."
        ),
        "condition_evidence": [
            (
                "Study in economics, business administration, automotive "
                "business, international business or comparable subject; sales "
                "specialisation preferred."
            ),
            (
                "Very good German/English and Excel/PowerPoint expected; "
                "intercultural collaboration, confident presentation, quick "
                "comprehension, analytical/conceptual ability and teamwork "
                "expected."
            ),
            (
                "Category-B driving licence advantageous, not mandatory. This "
                "location requires a prescribed compulsory-internship "
                "component."
            ),
            (
                "Publisher schedule is full-time with part-time possible; "
                "offering temporary."
            ),
            (
                "Submit CV, cover letter, certificates, current enrolment "
                "showing current semester, and evidence of standard study "
                "duration online. Include compulsory-internship proof where "
                "applicable; positions explicitly requiring a compulsory "
                "component remain mandatory. Mark documents as relevant for "
                "this application; each attachment is subject to the stated 5 "
                "MB limit."
            ),
            (
                "Structured employer support labels: discounted meals, employee"
                " phone may be available, employee discounts may be available, "
                "annual profit share may be available, staff events, coaching, "
                "flexible hours may be available, hybrid work may be available,"
                " health support, occupational retirement support, mobility "
                "offers, parking, workplace doctor, public-transport access, "
                "accessible workplace, nearby childcare, canteen/cafe. Labels "
                "do not separately establish payment amounts or individual "
                "eligibility; conditional offers are not guaranteed."
            ),
            (
                "Official location(s): Stuttgart, DE. Published schedule: Full "
                "time (part time possible). Offering: Temporary. Structured "
                "advertised start(s): 2027-02-01. Listing-publication end(s): "
                "2026-11-10; publication end is not a supported application "
                "closing."
            ),
            (
                "No citizenship whitelist or guaranteed pay amount is stated in"
                " these descriptors; host location is separate from "
                "eligibility."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241946": (
                "d27f463bbd335e4c6159087c9f0962df"
                "e748eca722ffab1747fd831ac0c22ca9"
            ),
            "241943": (
                "d3803d648ccd5c12a35ca39cf05d6aac"
                "da006efc8f6bb023fa6b9fa8758120cb"
            ),
            "241942": (
                "7ff1fab3a6f56a45d49b8d99afc0efdf"
                "664f7066bec0b2a0f056754184dd3a99"
            ),
        },
    },
    "mer000490z": {
        "summary": (
            "Economics/business, automotive business, international business or"
            " similar studies; German/English. Office and a category-B driving "
            "licence desirable; title specifies mandatory internship. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000490z",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241724": (
                "0363983ffda7432ffeed4b96dc493ece"
                "87b4bcef93c2f6611964ca8a58fb9a91"
            ),
            "241723": (
                "0aa59a425e1bbe87021f0e6e4bcb94a6"
                "170d23e34dc0721e5cda5f6c5ead33c8"
            ),
            "241722": (
                "176d7d6fd750f1e539484875b674a39c"
                "254baafb0a79f2d333237a6b8fc6502e"
            ),
        },
    },
    "mer0004911": {
        "summary": (
            "Relevant mechanical/electrical/industrial engineering/supply-chain"
            " degree or equivalent education/experience; "
            "assembly/lean/data/problem-solving and Office skills are "
            "preferred. Non-US university enrolment or bachelor–master gap, at "
            "least 3 semesters and 6 months required. USD 18/hour; visa costs "
            "covered; USD 700 housing bonus. Required application documents "
            "include enrolment proof. Listed benefits: coaching, health "
            "benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004911",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240945": (
                "bd08cc2d0eb46db239882c081fb8f3de"
                "d0bbde05dbe45748fb27fa6e477cbfc7"
            ),
            "240944": (
                "1bb88b28720a6c9575e3cdb77e47c06e"
                "11cdc34515397d071da38d44a80d04f7"
            ),
            "240943": (
                "ae067e9f15cebe4147f89d9b03178e12"
                "443242972d37286b33bf618fcd7f2dfa"
            ),
        },
    },
    "mer0004912": {
        "summary": (
            "Engineering/computing or related degree or equivalent "
            "education/experience; equipment, analysis/CAD/PowerBI/project "
            "skills are preferred. Non-US university enrolment or "
            "bachelor–master gap, at least 3 semesters and 6 months required. "
            "USD 18/hour; visa costs covered; USD 700 housing bonus. Required "
            "application documents include enrolment proof. Listed benefits: "
            "coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004912",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241275": (
                "2fe2c0658aa1c6d5ba3ecbb6abb78da7"
                "ca41a7e76b2975c484cb1711750d0b8a"
            ),
            "241274": (
                "dfcf9ed92ded5d4d3cf7e634dc5a0b1c"
                "a5a2d16554c9ff06fe331b21f05a8758"
            ),
            "241273": (
                "639b9ae01797f7e29c09f62a787d3641"
                "9cac72517f55bd4d626a15d0c3d19c9d"
            ),
        },
    },
    "mer0004916": {
        "summary": (
            "Mechanical/industrial engineering, business/economics or related "
            "studies or equivalent education/experience; project skills, "
            "Office/Adobe and German/English. Non-US university enrolment or "
            "bachelor–master gap, at least 3 semesters and 6 months required. "
            "USD 18/hour; visa costs covered; USD 700 housing bonus. Required "
            "application documents include enrolment proof. Listed benefits: "
            "coaching, health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004916",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "240795": (
                "662e000c91b8435c08a41f187eb0c63a"
                "caec71db9e01fdcfb77a07c6d1f131bd"
            ),
            "240794": (
                "fe6a627c6a88e3c64ef99fff9c540818"
                "59a9c275c1595994331c2b85b645a680"
            ),
            "240793": (
                "6ce67114631d0e669d56a2ead2de30d8"
                "aeae4bf97a8728a2b3b0afcb0990b0bf"
            ),
        },
    },
    "mer000491p": {
        "summary": (
            "Six-month internship offered. Business/industrial engineering or "
            "related enrolment, preferably logistics; German B2 and English "
            "plus analytical/process skills. Prior logistics/supply-chain "
            "experience preferred. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000491p",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240857": (
                "259be771c2620d8bc993e0250a97a2e4"
                "9f358120b5518391b925b44b006552b6"
            ),
            "240856": (
                "3819a58e1d64329ec98a7034849e96bb"
                "4496e32c3fd2f54c46c5b0a0461d6d78"
            ),
            "240855": (
                "eaafd65f07fce28fbb64c9a68b5fc143"
                "849592f717f3d174cbb02230cc4ae0fe"
            ),
        },
    },
    "mer000491q": {
        "summary": (
            "Placement takes place onsite at MTC Sindelfingen. "
            "Industrial/mechanical/automotive engineering, business or related "
            "studies; German/English and Office. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000491q",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241338": (
                "47f9847a51b5082cb26a3a8fa6619b5e"
                "6d23e084a67e26c40e087084c8db8a1f"
            ),
            "241335": (
                "02de836c7e8b967c715195ed98d278fd"
                "bb0ceabd865b1e9551e9827476a040fd"
            ),
            "241334": (
                "5fc24133e78ea5aa6c45c98383a470a5"
                "6a7359ac2fca5685a35297492d4fc258"
            ),
        },
    },
    "mer000491t": {
        "summary": (
            "Automotive/electrical/IT/mechatronic/automation engineering, "
            "business informatics/computing/software or related studies; "
            "software-development and project/data experience; German/English, "
            "Office and category-B licence. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. "
            "Excellent grades; regular-study duration+max3 semesters, open "
            "exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000491t",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241781": (
                "79d308f1a1585a158e02f692137353a6"
                "9156ab70bfd8352876b0d5251a6840de"
            ),
            "241780": (
                "03abdc9c6f48e80d629dbbd3702fd39d"
                "e15fcbcd032906ead33d453dc4ef5158"
            ),
            "241778": (
                "c43702969b1311987e89766db24400ce"
                "de44baa3ee3e582df2e83b1c9c899bf7"
            ),
        },
    },
    "mer000491v": {
        "summary": (
            "Relevant engineering, simulation or mathematics studies; "
            "mechanics/finite-element/IT, coding, Office and strong "
            "German/English. Completed bachelor degree desirable but explicitly"
            " not essential; initial numerical-simulation experience preferred."
            " Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000491v",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241005": (
                "e00620de6e1abd482ba3d9a88c161959"
                "5b4deb2ecd2ec5fbcfca6d1460ab7294"
            ),
            "241004": (
                "fd0cef085bb163d70b63b9cf71805539"
                "9da3da2372fb922fd3e8fda562f8cfdc"
            ),
            "241003": (
                "458466ce2192422b4e54237acfddc254"
                "351fa506bf7a9fadd3e98b4816dac94d"
            ),
        },
    },
    "mer0004922": {
        "summary": (
            "Business/business informatics or related studies; "
            "negotiation-level German/English, strong Excel/PowerPoint and "
            "SAP/BI/reporting experience. Copilot/n8n/AI-automation knowledge "
            "desirable. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004922",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241038": (
                "76c25db07fb5dc3421acffecff0ab5ec"
                "7ce0f68e60b34f5a56a83b1bd8a1bd51"
            ),
            "241036": (
                "947a087d6f2eb9c51aec8426f95f2eaa"
                "350a90243d7fc25c7bbf483a9ec6d75c"
            ),
            "241033": (
                "607d80d718b69f95fa49a8a83e7177b5"
                "d36832110dff7c0f052c81392fff4460"
            ),
        },
    },
    "mer000492j": {
        "summary": (
            "Five-to-six-month internship offered. Supply-chain/industrial "
            "engineering or related studies; German/English and Office. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000492j",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241494": (
                "fb19e79f577fd89e65d4fe797c581475"
                "2f630e377baf6af0b17979bab6d8c6e2"
            ),
            "241493": (
                "047d4e26789566ddb7f1969fe5ceae37"
                "ef417dd8c3eea66243f7245a4bd6c782"
            ),
            "241492": (
                "9f75ca6d9bfea3fb27550c2b48163e80"
                "dda6a6e12c2e0644df79749803e410e7"
            ),
        },
    },
    "mer0004932": {
        "summary": (
            "Relevant business/economics/industrial engineering studies "
            "desirable; German/English and Excel/PowerPoint. Category-B licence"
            " and automotive/smartphone interest explicitly essential; "
            "mandatory internship component. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004932",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "240990": (
                "bc3c4a4aff0a03731e1fc156cf374c1d"
                "31d0e39c49a36f5b75cf8433b8259075"
            ),
            "240986": (
                "20169fc21f5875c1012b5ba22ecaf466"
                "bb98f346866078307b7e4fd9d0c96ce2"
            ),
            "240985": (
                "e9f81ed4fd957644d04b4110d14ae648"
                "b3c0a6ccc1a807a2b390981d98ec7cae"
            ),
        },
    },
    "mer0004933": {
        "summary": (
            "Six-month S-Class Guard production internship in Sindelfingen from"
            " March 2027. For mechanical, automotive, industrial or production "
            "engineering, technical management or related students. Requires "
            "structured analysis, initiative, teamwork and Excel/PowerPoint "
            "skills; interest in production and project management. Supports "
            "vehicle launches, process planning, production metrics and "
            "digitalization."
        ),
        "condition_evidence": [
            (
                "Accepted studies: mechanical engineering, automotive "
                "engineering, industrial engineering, production engineering, "
                "technical management or comparable."
            ),
            (
                "Structured analytical work, initiative, communication and "
                "teamwork; interest in production processes and project "
                "management; competent Excel and PowerPoint use."
            ),
            "Six-month placement from March 2027.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Sindelfingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-11; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242100": (
                "ae3eed12eaed1601c57e9bf594529dc1"
                "4099b2417e2e2b880c3c699b12e06d03"
            ),
            "242094": (
                "29177c39ef36dcc330683577f0ebc87d"
                "67632bfa314ab60840c94dc90961361b"
            ),
            "242092": (
                "6fc7a7666517d5ab9db61e9c4915a8d5"
                "3ebc37f21ff16cc5c8bae165ffdc4e99"
            ),
        },
    },
    "mer0004938": {
        "summary": (
            "Mechatronics/automotive/computing/mathematics/physics or related "
            "technical studies; German C1+, good English, Python, "
            "mathematics/statistics and Office. Sensor/data-validation "
            "experience desirable. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Excellent "
            "grades; regular-study duration+max3 semesters, open exams;7–20 "
            "h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004938",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241136": (
                "f0c8a3d65545f8a3e235feb5f8806d8c"
                "ec7526e2415a33179a077104f7434ad8"
            ),
            "241141": (
                "af6022cea66ea219971acde65eb5a950"
                "573407e03d2b927efe3be7c099b5e4ee"
            ),
            "241140": (
                "e9ebd1015bb0a849b0460eb562c7c750"
                "1ccd404eed3bef71dc52e4a4e12839fa"
            ),
        },
    },
    "mer000493a": {
        "summary": (
            "Business/industrial engineering/logistics or related studies; "
            "German/English and Office. Mandatory internship component "
            "required. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000493a",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241090": (
                "6eb9b6c6a45ebc20d9c2f093df8c167e"
                "894fe4576fbbd0af101a483e43f8e5b0"
            ),
            "241089": (
                "0e56d04c936a5376b9c5730fbdd40ea7"
                "c0aa0999de6dd00302ca5d8787d79c73"
            ),
            "241088": (
                "ab188102a1277d791c7f744fbaa9a71c"
                "f25cfed0dd3dc13537ec48a9fc769288"
            ),
        },
    },
    "mer000493g": {
        "summary": (
            "Business, business informatics, economics or related studies, "
            "preferably corporate finance/controlling/accounting; "
            "German/English and Office. SAP desirable; mandatory internship "
            "component. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000493g",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241476": (
                "9c1e66873ff2391e657b6c000149df89"
                "e7ba160dfd6ed131a2c977a8f82ab877"
            ),
            "241474": (
                "3fd1ff4ae68c3038c2d8a6556ad6ef15"
                "a06f2a0bac0c81f7be8dea0ae4251fe1"
            ),
            "241473": (
                "bf49bf8acf45498f81d15d47da5a9302"
                "608f16733ecfdefd85e6859d2c033964"
            ),
        },
    },
    "mer000493z": {
        "summary": (
            "IT/industrial engineering/business or related studies; "
            "German/English and Office. PowerBI/Databricks/n8n knowledge "
            "advantageous. Listed benefits: coaching, health benefits, parking,"
            " possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000493z",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241356": (
                "16350a14dbe21378c441056bdc267375"
                "c145c156b5695c2514d8bc23a82f3d55"
            ),
            "241353": (
                "da1ed29dd16ce84f272b0fc876dc3473"
                "f36dcec6c711ac9345807f64576cc46a"
            ),
            "241351": (
                "537831d10f8314dd2e0031fe378cfc09"
                "77a25b947389b29a55d21ee3d6eef964"
            ),
        },
    },
    "mer0004940": {
        "summary": (
            "Relevant engineering/business/communications/media studies; "
            "German/English, Office and a category-B driving licence. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004940",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241169": (
                "976bb2921b7a54cf8e249d58da3b6a7d"
                "0b7b97cbeefb454d1934c278a6235133"
            ),
            "241166": (
                "859edcc9442f455c9d51ef0a1a1af2d3"
                "263e961d1f5ae9b9f4c7eb41007666ca"
            ),
            "241164": (
                "794f4212c44622354a04c119c7409097"
                "15bad61b0e96ec991e06ffa2ad01bb57"
            ),
        },
    },
    "mer0004941": {
        "summary": (
            "Automotive/mechanical engineering, technology "
            "management/industrial engineering or related studies; "
            "negotiation-level German/English and Office; interest in technical"
            " regulation. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004941",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241347": (
                "1478c5d8738b2bab71ea4218f15d2f33"
                "4367d34aee9320861623d93f6d51a003"
            ),
            "241342": (
                "73560d9f36e789af43a9151c345bd972"
                "11bbfc0d0c41db2ccb55eb9e39ba5992"
            ),
            "241339": (
                "bc5be19aaa1057be446c8f0986cec08d"
                "5d26f05609ab7d01600e68f717f8d1a7"
            ),
        },
    },
    "mer0004942": {
        "summary": (
            "Global order-management internship from March 2027 for industrial "
            "engineering, business administration or comparable students "
            "specializing in logistics or supply chains. A curriculum-required "
            "internship component is mandatory at this location. Requires "
            "German and English, MS Office, analytical work, initiative and "
            "teamwork; SAP knowledge is preferred. Work covers order flows, "
            "shortage handling and digital logistics processes."
        ),
        "condition_evidence": [
            (
                "Accepted studies: industrial engineering, business "
                "administration or comparable with logistics or supply-chain "
                "specialization."
            ),
            (
                "Written and spoken German and English; MS Office competence; "
                "engagement, teamwork, analytical and strategic work, "
                "initiative and independent working. SAP is desirable."
            ),
            (
                "The internship must include a prescribed compulsory-study "
                "component at this location; a purely voluntary placement is "
                "not established."
            ),
            "Start from March 2027; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Esslingen am Neckar (DE). No citizenship"
                " eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241974": (
                "981c52fe70cc8c77f330720433bc238f"
                "290fa28da5fe7a4a3db5c81528b876f3"
            ),
            "241972": (
                "a9d18f6e1eebc2ccc06559fa41df6ceb"
                "8c138cabd7e1e13fadef012014934165"
            ),
            "241971": (
                "ac16cad98ff6c1ac3e8339761a5ac01c"
                "7daf41f5ab616c8cbee2aeb301550fc8"
            ),
        },
    },
    "mer0004943": {
        "summary": (
            "University enrolment with at least three semesters, preferably "
            "marketing/PR/journalism/media/content; communications/marketing "
            "experience, English, Office and category-B licence. Six months,40 "
            "h/week in Stuttgart from 1 March2027. Listed benefits: parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004943",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241080": (
                "55105dc80319fef0cb0cc7a0a21b42d7"
                "46820c1434572c17708e67f83761e600"
            ),
            "241076": (
                "dfaa0a1c58025f63ab8c46b7523c68b6"
                "dd7c06ae9239789e28947e369b61a912"
            ),
            "241073": (
                "3b4fb7f1422050b24b5bba0e62ce1803"
                "ea88578678909f283be6568d1cdeb205"
            ),
        },
    },
    "mer0004947": {
        "summary": (
            "Mechanical/aerospace/automotive engineering, "
            "computing/cybernetics/physics or related studies; initial coding, "
            "simulation experience or interest, German/English and Office. "
            "Vehicle/chassis basics desirable, not mandatory. Flexible10–20 "
            "h/week. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Excellent grades; "
            "regular-study duration+max3 semesters, open exams;7–20 h/week. "
            "Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004947",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241205": (
                "8388282b0bdf6c2ee13fefcf123d6586"
                "bca898ff065a2fbe15642258ff4372b3"
            ),
            "241204": (
                "71eaf85716b91fab91435cb4cb6f0bc6"
                "d8caf85601ebf8eeacd6f4600421d463"
            ),
            "241203": (
                "b358f0513a83889ad9e4ebdfdb1f9b45"
                "b8420fa55a5449d115cc49bcc5f09a9f"
            ),
        },
    },
    "mer000494l": {
        "summary": (
            "Good Realschule qualification; commercial/organizational skills, "
            "German and mathematics. Apprenticeship starts15 Aug2027 by title. "
            "Listed benefits: coaching, health benefits, possible hybrid work, "
            "possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000494l",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "241096": (
                "daf50d4783f06c634f617b5eaccf2311"
                "be858245e33ec757d1d27fce631631e5"
            ),
            "241095": (
                "80714b227de01eec3683515ed34ee795"
                "e26e675cdc8a279bed1463f41aa943a8"
            ),
            "241094": (
                "852d70c676770e86865725783817f1de"
                "ed05e6bf9ccf72ae2e32b0b6a36cdb86"
            ),
        },
    },
    "mer000494m": {
        "summary": (
            "Good Realschule qualification; technical/manual aptitude, "
            "mathematics/physics and reliable colour vision. Apprenticeship "
            "starts15 Aug2027 by title. Listed benefits: coaching, health "
            "benefits, possible hybrid work, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000494m",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "241091": (
                "d402a8f353785d709f550d815f6a1ef4"
                "dfba0d3e7bc0ba9008a344ad0be361f2"
            ),
            "241093": (
                "0bb00ed52d5852b3494eb14afffe2117"
                "2d500c49d2699125ca42618921424a69"
            ),
            "241092": (
                "1bcc136225795ee34d3534db7032fc14"
                "bc6d578a8ccf3c88aa6471b62af5e3f5"
            ),
        },
    },
    "mer000494n": {
        "summary": (
            "Business informatics, computing, industrial engineering/business "
            "or related studies; strong German, good English and "
            "Excel/PowerPoint. PowerBI/database/web/agile experience preferred."
            " Listed benefits: coaching, health benefits, parking, possible "
            "hybrid work, possible flexitime. Excellent grades; regular-study "
            "duration+max3 semesters, open exams;7–20 h/week. Relevant "
            "internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000494n",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241222": (
                "f3663a53887763a4a6dce9527f7c2395"
                "54653fb67609e6e22bbf8b31bbc665c0"
            ),
            "241220": (
                "2c7e45fbb4f08c37a80176a482545196"
                "bb3a2b0ffcb484abee6b883222ab1aea"
            ),
            "241216": (
                "a4bfd38581f2b51d825b6750c7758c7f"
                "9fdefd25f5e89b58bfa02cddf347726f"
            ),
        },
    },
    "mer0004958": {
        "summary": (
            "Active full-time Hungarian enrolment in "
            "business/management/marketing or communication studies; native "
            "Hungarian, German and/or English and Office; six months, at least "
            "three days/week. Web-design experience advantageous; bilingual CVs"
            " requested. Transport/other benefits conditional under company "
            "rules."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004958",
        ],
        "categories": ["training"],
        "language": "hu",
        "variant_hashes": {
            "241114": (
                "9798d44c0b570083eca979bc45195190"
                "99a91c780e6daa2de5e008bd01d1515b"
            ),
            "241113": (
                "92dda67ec944e988815583972d8711ec"
                "b42198a86a15f57118cab706b903a40f"
            ),
            "241112": (
                "b3995625ca0de9568b94f5aaceba22ff"
                "01ffbfdd923d63f8127e4b3f6d784d44"
            ),
        },
    },
    "mer000495c": {
        "summary": (
            "At least six months of availability required. Computing/business "
            "informatics or related studies; Python/TypeScript/SQL, app/web/API"
            " and design/mock-up experience; German/English and Office. "
            "Jira/Confluence/Azure/DevOps desirable. Listed benefits: coaching,"
            " health benefits, parking, possible hybrid work, possible "
            "flexitime. Excellent grades; regular-study duration+max3 "
            "semesters, open exams;7–20 h/week. Relevant internships preferred."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000495c",
        ],
        "categories": ["jobs"],
        "language": "de",
        "variant_hashes": {
            "241378": (
                "cd1dfb15e1919fd28db2bc6010c5cc7c"
                "0ad1a1034609d8bef55f4ba2dab4f70d"
            ),
            "241377": (
                "263f40dc51e78f5c002ef176ff4ac8a2"
                "aba6f2c4954bfbf1390bdab85a1afb84"
            ),
            "241376": (
                "ebba6d06e746a4ef78180ee852005d3b"
                "a316ebea5296af9f8190c56b6dabda71"
            ),
        },
    },
    "mer000495l": {
        "summary": (
            "Automotive/mechanical/mechatronic engineering or similar studies; "
            "German/English, Office and category-B licence. Analysis tools, "
            "combustion/thermodynamics knowledge and technical vocational "
            "training advantageous. Mandatory internship component. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000495l",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241361": (
                "9112599cbb2585e9d889c6a474012990"
                "d9096ed0a7065336b293c92d6776df33"
            ),
            "241359": (
                "69e9b706efe8c47b870bc2f6587fde4a"
                "f983ca211b19ee047823e57208d47e79"
            ),
            "241357": (
                "f6262cb5c61f35cdba7c0541b5e97531"
                "11fafaaa3e24c3d9d459e78d811b4aac"
            ),
        },
    },
    "mer000495n": {
        "summary": (
            "Title/qualifications describe mandatory internship, but Tasks say "
            "working student: confirm role. Automotive/mechanical/mechatronic "
            "engineering or similar studies; German/English, Office and "
            "category-B licence. Analysis tools, combustion/thermodynamics "
            "knowledge and technical training advantageous. Mandatory "
            "internship component. Listed benefits: coaching, health benefits, "
            "parking, possible hybrid work, possible flexitime. Study proof; "
            "voluntary internships within regular study+3 semesters. EEA "
            "ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000495n",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241387": (
                "30f39711ebef7b6fdfebbcad319f0023"
                "97f92bafbc203a3f6cf30ba7f0eadf3c"
            ),
            "241386": (
                "db11618a2ffeddc68b0e52e910ea75b1"
                "7dfac08e9bd78c71ff93198d40274b28"
            ),
            "241388": (
                "b0068c6bf1489d0f112fe51abe3ef17f"
                "7881c54ecad65796eb0701b8cbbfab0a"
            ),
        },
    },
    "mer0004960": {
        "summary": (
            "Economics/social science/humanities or related studies, such as "
            "HR/business/business psychology; Office and strong communication, "
            "analytical and organizational skills. Listed benefits: coaching, "
            "health benefits, parking, possible hybrid work, possible "
            "flexitime. Study proof; voluntary internships within regular "
            "study+3 semesters. EEA ID/passport; otherwise residence/possible "
            "work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer0004960",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241484": (
                "7494394a781bb8954da8c1c46ffae989"
                "c78b5e9df6f07d9a409c8349eb6f71e7"
            ),
            "241482": (
                "a17db358a3db2b5b49c17c9a93d4f424"
                "8fe571e7de07b632bf4fd160bd497337"
            ),
            "241481": (
                "c172c7313ec97d2cf33f62c9c02d295d"
                "cd1ddb3bbb96e8d680d90384bf2f52d9"
            ),
        },
    },
    "mer000496a": {
        "summary": (
            "Current electrical/mechatronic/automotive/technical computing or "
            "related university studies; category-B licence and hands-on "
            "vehicle/software-testing interest. Automotive/SPICE experience "
            "advantageous. Six months,20 h/week from 16 Nov2026; EUR 2243/month"
            " is a full-time salary benchmark. Listed benefits: coaching, "
            "health benefits, parking."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000496a",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241247": (
                "c7bb8b18a5a37a23cad7c0d2b4ef6e09"
                "e485144c997ba84ad02a56e07d3d3a01"
            ),
            "241246": (
                "e842792118e2d762916d7f625c0b837a"
                "d1e458716eff95e75fbbaeb2b08196cd"
            ),
            "241245": (
                "52f4a2ccc290b0b5d518ebfff9c1838e"
                "dc946e98a2ad3b8428c058dc5963a70e"
            ),
        },
    },
    "mer000496e": {
        "summary": (
            "Good Realschule qualification; technical/manual/spatial skills and"
            " mathematics/physics. Start1 Sep2027. Stated training pay EUR 1242"
            " in year1 up toEUR1586 in final year, depending on location; later"
            " employment is an opportunity, not guaranteed."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000496e",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "241256": (
                "00aa48c50a64bb63cfa544a80d5e3c53"
                "a28fec00ea591abb8a56fcf40a2f9430"
            ),
            "241255": (
                "b9b0f988c757574e474c70e969d9704a"
                "fa65c1794a354b0f5f702f526b90c53b"
            ),
            "241253": (
                "fdbc6ca2450d0931d41e812ccb23ad5f"
                "367e736276f1e4d4dbeeca1bccf9ab58"
            ),
        },
    },
    "mer000496h": {
        "summary": (
            "Industrial engineering/supply-chain/business or related degree or "
            "equivalent education/experience; English/German, Office and "
            "analytical skills preferred; logistics/Mercedes experience "
            "advantageous. Non-US university enrolment or bachelor–master gap, "
            "at least 3 semesters and 6 months required. USD 18/hour; visa "
            "costs covered; USD 700 housing bonus. Required application "
            "documents include enrolment proof. Listed benefits: coaching, "
            "health benefits, parking, possible flexitime."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000496h",
        ],
        "categories": ["internships"],
        "language": "en",
        "variant_hashes": {
            "241269": (
                "fd4aede9422b9f265ab87906dad26f31"
                "1c02f0ebcaf37f38603051dacdd19f44"
            ),
            "241268": (
                "d37736592a41797256ccc3024a201bcd"
                "566bd35851f457a73bedd87a0607c233"
            ),
            "241267": (
                "62c0caccad46872240538c1fa8ea0f11"
                "8d06c462a61ba7c17e89ccba44b5474e"
            ),
        },
    },
    "mer000496u": {
        "summary": (
            "Automotive/mechanical/industrial engineering or related studies; "
            "German/English, Office and valid driving licence. Title identifies"
            " mandatory internship; licence class is not stated. Listed "
            "benefits: coaching, health benefits, parking, possible hybrid "
            "work, possible flexitime. Study proof; voluntary internships "
            "within regular study+3 semesters. EEA ID/passport; otherwise "
            "residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000496u",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241584": (
                "d5073d9b0a095b859dbd9fc9cdcfd614"
                "120fc90cbeb3076c8c38b2d2501d1882"
            ),
            "241582": (
                "1a115a6db6ae195bb12fb03a3bbcee22"
                "78b187356bc72b587cfe292a8db707bf"
            ),
            "241580": (
                "78cd9d39fde6d1088d64d23d92d07bc6"
                "0d25d2334058082e0b1c0878c681dfeb"
            ),
        },
    },
    "mer000497b": {
        "summary": (
            "Economics/engineering/science or related studies with excellent "
            "results; practical strategy/automotive experience, Office and "
            "German/English. Mandatory internship component; hybrid model "
            "stated. Listed benefits: coaching, health benefits, parking, "
            "possible hybrid work, possible flexitime. Study proof; voluntary "
            "internships within regular study+3 semesters. EEA ID/passport; "
            "otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000497b",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241758": (
                "be74558398d18ae8d680e15180b6e5e9"
                "ddf275f99a5e18be622b068206eeb92b"
            ),
            "241757": (
                "d8b97ab089418664e89079ff0e69f8b0"
                "47e6718add3af89f82fcf7ae02c06ac1"
            ),
            "241756": (
                "8cdfb5a8f8d8e5295de1bd2fc3ca87e0"
                "1d7b97d4fb5e44223cb8890f0d6a2251"
            ),
        },
    },
    "mer000497g": {
        "summary": (
            "Infotainment and connected-car internship from March 2027 for "
            "automotive, business IT, computer science, software, engineering, "
            "electrical engineering or related students. Requires German and "
            "English, a category B driving licence, reliability, teamwork and "
            "analytical interest in technical systems. Initial coding, "
            "automation or data-analysis experience is helpful. Work includes "
            "vehicle testing, fault analysis and coordination with development "
            "teams."
        ),
        "condition_evidence": [
            (
                "Accepted studies: automotive engineering, business "
                "informatics, computer science, software engineering, "
                "engineering, electrical engineering or comparable."
            ),
            (
                "Written and spoken German and English and a category B driving"
                " licence are required. Engagement, teamwork, reliability and "
                "analytical interest in technical relationships are requested."
            ),
            (
                "Initial programming, automation or data-analysis experience is"
                " advantageous, not mandatory."
            ),
            "Start from March 2027; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Sindelfingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241859": (
                "44743e45c2e1bd64b50caa119eb827a6"
                "e2eb80b065b5befd7690555e233ba3ef"
            ),
            "241855": (
                "94c5858e48d9ec45d9966ed77a68fca6"
                "747f56660f4fbf9628dbd6934dcec9c0"
            ),
            "241854": (
                "c4962c846aaf6c6ed3c9f856b1c2649c"
                "192296a4d2701d033177852021668cc9"
            ),
        },
    },
    "mer000497h": {
        "summary": (
            "Economics/engineering/science or related studies with excellent "
            "results; Office/PowerPoint, Mercedes/product-market knowledge, "
            "German C2 and English B2+. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000497h",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241681": (
                "347bbc9864ad7a6ec0faa3490a0247dc"
                "8efee2209a8076a125a0380d3ca059f0"
            ),
            "241678": (
                "6d879a3e036e4c3449d3b6a23469d5c0"
                "bbc82ab65d9500f83983340d0a96dddf"
            ),
            "241676": (
                "c910cd8974231add5780e80d9c44bbc3"
                "68437ba2e8f2f88960739d444f3a8858"
            ),
        },
    },
    "mer000497i": {
        "summary": (
            "Economics, HR, psychology, adult education/communications or "
            "related studies; strong German, good English and Office. First "
            "practical experience advantageous but not required. Mandatory "
            "internship component required. Listed benefits: coaching, health "
            "benefits, parking, possible hybrid work, possible flexitime. Study"
            " proof; voluntary internships within regular study+3 semesters. "
            "EEA ID/passport; otherwise residence/possible work permit."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000497i",
        ],
        "categories": ["internships"],
        "language": "de",
        "variant_hashes": {
            "241772": (
                "1beff07a453ecc9122203fce9106ff6f"
                "2c04c38599e84dee99fa4ec7abe3c44d"
            ),
            "241771": (
                "97d1369c5110eaae8b77b1695285090a"
                "43c38b489884818de8407217c3e1a85d"
            ),
            "241775": (
                "3639850812867f4f6f73b37412cf8e6c"
                "7d8cf6e90a0530d1e2212df0ff70657f"
            ),
        },
    },
    "mer000497r": {
        "summary": (
            "Mobility and technical-business interest; submit two latest school"
            " reports and CV; school grade routes apply. Study1 Oct2027–30 "
            "Sep2030 at DHBW Stuttgart with Hamburg/Stuttgart practice. Youth "
            "transport ticket, extra relocation support and possible Stuttgart "
            "dormitory accommodation stated. Entry routes: technical Abitur "
            "avg≤2.7/math≥8 points; business avg≤2.5/math≥10; FHR avg/math≤2.0 "
            "plus DHBW test."
        ),
        "condition_evidence": [
            (
                "Mercedes-Benz official public youth career lookup, complete "
                "individual descriptors and referenced entry conditions"
            ),
            "mer000497r",
        ],
        "categories": ["training"],
        "language": "de",
        "variant_hashes": {
            "241406": (
                "97f20761d1a0aa7a815784146bc1bc70"
                "9ff6f8e7805f80e0e225e1d9f652871a"
            ),
            "241405": (
                "a14a25110dabd62f82f76aee0472bdb5"
                "34b1d0e6468eb1bacc783cd32f6b2920"
            ),
            "241410": (
                "22e8c661cc380c11f24145ee2271f580"
                "8735ec37c97e51346dd88b00d2e2aa5f"
            ),
        },
    },
    "mer000497t": {
        "summary": (
            "Hybrid passenger-car transmission development internship from "
            "March 2027. For mechanical, automotive, industrial engineering or "
            "comparable students with a required internship component. Requires"
            " German and English, Excel/PowerPoint, technical understanding, "
            "teamwork and analytical work; prior drivetrain experience is "
            "preferred. Supports technical analysis, project coordination and "
            "improvements to project-management tools."
        ),
        "condition_evidence": [
            (
                "Accepted studies: mechanical engineering, automotive "
                "engineering, industrial engineering or comparable."
            ),
            (
                "Written and spoken German and English; competent MS Office "
                "including Excel and PowerPoint; engagement, teamwork, "
                "analytical and strategic work; technical understanding. Prior "
                "drivetrain experience is preferred."
            ),
            (
                "A prescribed compulsory-study internship component is required"
                " at this location."
            ),
            "Start from March 2027; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Stuttgart (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-11; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242184": (
                "663e000a9c74eed92980ee6bcf7ac376"
                "4bc158631a5e594b16641f052fbc7d68"
            ),
            "242183": (
                "870eb0203d741ba5dd653c6334f61718"
                "8062e325334774dbd0004930a04baa11"
            ),
            "242180": (
                "534b6f487184888aeef57f4374eb79f6"
                "a05ffe92aa34c70b0b41d9cd9d381832"
            ),
        },
    },
    "mer000497u": {
        "summary": (
            "Thermal-management working-student role from November 2026 for "
            "mechanical, automotive engineering or comparable students. "
            "Requires German and English, MS Office, INCA and MDA familiarity, "
            "teamwork and analytical work. Tasks include ASPICE support "
            "processes, project documentation, variant management and "
            "competitor benchmarking. The API gives 24 November 2026 as the "
            "start date; the advert states November more generally."
        ),
        "condition_evidence": [
            (
                "Accepted studies: mechanical engineering, automotive "
                "engineering or comparable."
            ),
            (
                "Written and spoken German and English; MS Office; INCA and MDA"
                " knowledge; engagement, teamwork, analytical and strategic "
                "work."
            ),
            (
                "Working-student position, not an internship; API "
                "start2026-11-24, description says November2026. Duration "
                "unspecified."
            ),
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Advertised workplace: Sindelfingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a part-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242017": (
                "7b19275a0bdfb4df9bd1cf762598b4a2"
                "9ce26713c0054d617fad7bcb574a38dd"
            ),
            "242021": (
                "34d60013f0d607879e7d75649472cbb6"
                "da10b3655e84185ca49472c82fbd7ddb"
            ),
            "242019": (
                "78538d920c3b29d6b58498c52e899c2d"
                "06bf9cbf25e9574f17b835ac5a14016c"
            ),
        },
    },
    "mer000499c": {
        "summary": (
            "Digital customer-touchpoints internship from March 2027 for "
            "marketing, business, computer science or related students. A "
            "required internship component is mandatory. Requires very good "
            "German and English, Excel/PowerPoint, analytical and structured "
            "work, creativity, teamwork and communication. Interest in digital "
            "trends, new technologies and AI is requested. Supports "
            "digital-channel rollout, customer-experience analysis and project "
            "reporting."
        ),
        "condition_evidence": [
            (
                "Accepted studies: marketing, business, computer science or "
                "comparable."
            ),
            (
                "Very good written and spoken German and English; MS Office "
                "including PowerPoint and Excel; enthusiasm for digital trends,"
                " new technologies and AI; creativity, innovation, analytical "
                "and solution-focused work, teamwork and communication."
            ),
            (
                "A prescribed compulsory-study internship component is required"
                " at this location."
            ),
            "Start from March 2027; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Stuttgart (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-11; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242103": (
                "3e91a4da326bfb775a51808bfec59055"
                "bd77a94f41140936e4f90804dbf7e35e"
            ),
            "242102": (
                "5f0d802084c838629d992a082849d98b"
                "6d4299eae008d5e6efb7eb9e8c52025e"
            ),
            "242101": (
                "7efb51fbe08897984773248509b3bf9a"
                "cc52c912aa800b5e99d84c45fdf488a2"
            ),
        },
    },
    "mer000499i": {
        "summary": (
            "Six-month HR consulting internship in Bremen from January 2027 for"
            " psychology, business psychology, economics or related students. "
            "Requires very good German and English, MS Office and initial "
            "practical experience, ideally in international-company HR. "
            "Independent, reliable and structured work, initiative, teamwork "
            "and interest in culture, personnel and organizational development "
            "are requested. Helps organize and evaluate leadership-development "
            "activities."
        ),
        "condition_evidence": [
            (
                "Accepted studies: psychology, business psychology, economics "
                "or comparable."
            ),
            (
                "Very good written and spoken German and English; Word, Excel, "
                "PowerPoint and Outlook; initial practical experience, "
                "preferably in HR at an international company."
            ),
            (
                "Quick comprehension; independent, structured and reliable "
                "work; interest in culture, personnel and organizational "
                "development; engagement, initiative and teamwork."
            ),
            "Advert proposes six months from January2027; API start2027-01-18.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Bremen (DE). No citizenship eligibility "
                "is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking. Listing a nearby childcare facility does not "
                "establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-11; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242082": (
                "1441cf783950f5c7826b21e804fb025b"
                "7bacca4fd967e64dd5843e33953c4bb0"
            ),
            "242078": (
                "3dbcf10a8b44e3175eb32b5b65669233"
                "ec3774c94f6ddd3cc0a96f8c576273a8"
            ),
            "242077": (
                "c4b21f300e5a6dc9e69df034a52fa701"
                "f480afda03fbab0ded4fa9b103e1058c"
            ),
        },
    },
    "mer000499k": {
        "summary": (
            "Omnichannel commerce internship from December 2026 for economics, "
            "marketing, communication, business IT or related students. A "
            "required internship component is mandatory. Requires German and "
            "English, MS Office, analytical and structured work, communication "
            "and teamwork. Interest in digital customer communication, CRM, AI "
            "and data-driven marketing is requested; Salesforce or other CRM "
            "experience is helpful. Supports campaigns, lead data and "
            "performance analysis."
        ),
        "condition_evidence": [
            (
                "Accepted studies: economics, marketing, communication studies,"
                " business informatics or comparable."
            ),
            (
                "Written and spoken German and English; MS Office; analytical, "
                "structured work, communication, engagement and teamwork; "
                "interest in digital customer communication, CRM, AI and "
                "data-driven marketing."
            ),
            (
                "Initial CRM-tool experience such as Salesforce is "
                "advantageous. A prescribed compulsory-study internship "
                "component is required."
            ),
            "Start from December2026; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Stuttgart (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242010": (
                "3ece22620abf390542fce4afb8db4fae"
                "40e41c9b86182d02d0ff6acd4db4a29c"
            ),
            "242009": (
                "285e5d754e385e9de6c73740f5701086"
                "4cfbc593a02cd1a2e215d0dfdbf5f4a6"
            ),
            "242007": (
                "a1f485d123721305a61def89a7d6f986"
                "2a285c6efa7b9cf96a136bc1576cf327"
            ),
        },
    },
    "mer000499l": {
        "summary": (
            "Event-management internship at the Rastatt customer center from "
            "March 2027. Students in event management, marketing, communication"
            " or related subjects are preferred. Requires enrollment throughout"
            " the placement, category B driving licence, MS Office, "
            "evening/weekend availability, creativity, organization, "
            "independent work and teamwork. Supports event planning, marketing "
            "and guest coordination. Apply with a CV and short motivation "
            "statement plus the listed study documents."
        ),
        "condition_evidence": [
            (
                "Study preference: event management, marketing, communication "
                "or comparable; subject preference is described as ideal rather"
                " than a strict exclusion."
            ),
            (
                "Valid enrollment certificate covering the entire internship; "
                "category B driving licence; willingness to work evenings and "
                "weekends; MS Office competence."
            ),
            (
                "Creativity, organizational ability and understanding of brand "
                "presentation; independent, structured and proactive work; "
                "teamwork and communication."
            ),
            (
                "Tasks specifically request a CV and short motivation letter; "
                "the additional application instructions also require the "
                "standard listed documents."
            ),
            "Start from March2027; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Rastatt (DE). No citizenship eligibility"
                " is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241989": (
                "1215a59e45210bf30ea9719c4e19d208"
                "9486849542be6bd727f79a41efcc873c"
            ),
            "241988": (
                "6bda17def26473bb2f10e1935e1453da"
                "1f08edfe64be534060ad22083c5ce78b"
            ),
            "241985": (
                "1cc5971399fd228311f95c4763d23044"
                "e1a19b6ccb01afec0b8e830a7311feb9"
            ),
        },
    },
    "mer000499w": {
        "summary": (
            "After-sales vehicle-project internship for engineering, IT, "
            "economics or related students. Starts in March 2027, with 1 April "
            "possible by agreement. Requires German and English, MS Office, "
            "analytical and customer/service-focused work, teamwork and "
            "communication. Presentation experience is desirable; driving "
            "licence, technical/vehicle affinity and international experience "
            "are advantageous. Supports repair/service concepts, parts tracking"
            " and vehicle-launch preparation."
        ),
        "condition_evidence": [
            (
                "Accepted studies: engineering including mechanical, "
                "industrial, automotive or service engineering; IT; economics "
                "including international business or technical business "
                "administration; or comparable."
            ),
            (
                "Written and spoken German and English; MS Office; analytical, "
                "strategic and results-oriented work; customer and service "
                "orientation, teamwork and communication."
            ),
            (
                "Presentation experience is desirable. Driving licence, "
                "technical understanding and vehicle interest are advantages; "
                "international or intercultural experience is desirable, not a "
                "nationality criterion."
            ),
            (
                "Start from March2027, alternatively2027-04-01 after agreement."
                " Duration unspecified."
            ),
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Sindelfingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241993": (
                "69d13261e00fba342fc11c9aabeeea8a"
                "d2849e655be7e5533943d3b6d662021a"
            ),
            "241992": (
                "7784131e4d0c6e16aba2074a2bb1190f"
                "ec3ccd66ec6295909f9536fc3385d850"
            ),
            "241990": (
                "ca39c03f3da93819e81cb42066f14fbb"
                "a63053828ec11387ecb651c936ca76ce"
            ),
        },
    },
    "mer00049a1": {
        "summary": (
            "Infotainment testing and AI-automation internship scheduled for 1 "
            "March–31 August 2027; starting 1 February is also possible. For "
            "computer science, automotive electronics or related students. "
            "Requires German and English, Java or Python, MS Office, common AI "
            "tools and basic automotive-network knowledge including CAN, "
            "Ethernet/SomeIP and MOST. Supports vehicle/test-bench software "
            "updates, test runs and result analysis; teamwork and analytical "
            "work are requested."
        ),
        "condition_evidence": [
            (
                "Accepted studies: computer science, automotive electronics or "
                "comparable."
            ),
            (
                "Java or Python programming; MS Office; common AI tools; basic "
                "automotive electronics covering CAN, Ethernet/SomeIP and MOST;"
                " written and spoken German and English."
            ),
            "Engagement, teamwork, analytical and strategic working.",
            (
                "Scheduled2027-03-01 through2027-08-31; an earlier2027-02-01 "
                "start is explicitly possible."
            ),
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Sindelfingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-11; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242193": (
                "3bc0aded774a5eff06f11cb6b3ec1fb8"
                "7bd96707973a6f3cfa33c7d5d9cd6ef1"
            ),
            "242191": (
                "19f965e1bbaf7d4ef01dfebe3f45e798"
                "520858e068852162c18921c89f646487"
            ),
            "242189": (
                "b72df36b3aa21bed0bdf7656b02d423e"
                "f1f796774f3e288297fc8c2cdc18b9ce"
            ),
        },
    },
    "mer00049ag": {
        "summary": (
            "IT-controlling working-student role from March 2027 for business "
            "administration, economics, industrial engineering or related "
            "students. Requires German and English, Excel, strong numerical "
            "analysis, careful independent work, initiative, responsibility and"
            " teamwork. Interest in controlling, finance and reporting is "
            "requested; initial Power BI, data-visualization or AI experience "
            "is preferred. Tasks cover budgets, supplier invoices, internal "
            "service contracts and reporting."
        ),
        "condition_evidence": [
            (
                "Accepted studies: business administration, economics, "
                "industrial engineering or comparable."
            ),
            (
                "Written and spoken German and English; MS Office especially "
                "Excel; strong analytical ability and affinity for numbers and "
                "data; interest in controlling, finance and reporting."
            ),
            (
                "Structured, careful and independent work; initiative, "
                "responsibility, teamwork and communication. Initial "
                "data-analysis or visualization experience such as Power BI and"
                " AI use is preferred."
            ),
            (
                "Working-student position, not an internship; start from "
                "March2027. Duration unspecified."
            ),
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Advertised workplace: Böblingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a part-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "241977": (
                "1d31757c335bed6f64fc1ac01537782a"
                "f7ff68d46c0311b57a5cd327932b5181"
            ),
            "241975": (
                "44919e1ac6fd38f7dba6da1a4f1a426e"
                "d8081937bcf5b993f4cf33746afd9f08"
            ),
            "241978": (
                "a23297dd236eb5e09b9670a591337dd2"
                "2045454b25178520654b9d7cbd3eeb83"
            ),
        },
    },
    "mer00049ai": {
        "summary": (
            "Midsize-car interior/exterior vehicle-testing internship from "
            "March 2027 for mechanical, industrial engineering or related "
            "students. Requires written and spoken German, MS Office, a valid "
            "driving licence, teamwork and analytical, strategic work. Supports"
            " test preparation, vehicle modifications, coordination across "
            "integration teams and small independent projects. English or a "
            "particular licence category is not stated in the supplied "
            "qualifications."
        ),
        "condition_evidence": [
            (
                "Accepted studies: mechanical engineering, industrial "
                "engineering or comparable."
            ),
            (
                "Written and spoken German; MS Office; engagement, teamwork, "
                "analytical and strategic work; valid driving licence. No "
                "specific licence category or English requirement is supplied."
            ),
            "Start from March2027; duration unspecified.",
            (
                "Online application: CV, cover letter, certificates, current "
                "enrollment proof identifying the study semester, and proof of "
                "the standard study duration. Mark uploads as relevant to this "
                "application; maximum file size5MB."
            ),
            (
                "Provide proof of a compulsory internship if applicable; the "
                "standard document list does not turn all placements into "
                "compulsory-only internships."
            ),
            (
                "Advertised workplace: Sindelfingen (DE). No citizenship "
                "eligibility is established by the workplace."
            ),
            (
                "The API lists a full-time temporary contract. No pay amount is"
                " stated in the supplied advert."
            ),
            (
                "Listed support includes discounted meals, staff events, "
                "coaching, health services, occupational retirement provision "
                "and mobility options. Employee phone, staff discounts, "
                "profit-sharing, flexible hours and hybrid work are expressly "
                "conditional or possible; they are not guaranteed."
            ),
            (
                "Additional listed facilities: on-site medical service, "
                "accessible workplace, public-transport access, canteen or "
                "cafe, parking, childcare nearby. Listing a nearby childcare "
                "facility does not establish a reserved or free place."
            ),
            (
                "Applicants with severe disabilities or equivalent status are "
                "explicitly welcome; publisher assistance is available without "
                "copying contact details."
            ),
            (
                "Publication ends 2026-11-10; this is a listing-publication "
                "date, not a verified application closing date."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241950": (
                "bc07e272f4faf6d7fc9efc9f42093d0c"
                "439f481b9b7a384b96c9a517d22eedde"
            ),
            "241949": (
                "cbbd68b2c9198f7fca3af868deb507e5"
                "944fc8092273fb1a725d869127482ad7"
            ),
            "241948": (
                "3bfcc4a7e401d417bf26ee7b201dc11e"
                "6b846b99147f4a0f31dc7e29c2bc9599"
            ),
        },
    },
    "mer00049bn": {
        "summary": (
            "Thesis placement in Stuttgart on insurance investment and capital "
            "efficiency under Solvency II, including an alternative investment "
            "fund. For business, economics, finance or related students with "
            "initial financial-services/investment experience, very good "
            "German/English and MS Office skills. Finance-related "
            "specialisation is preferred. Start from December 2026; topic "
            "agreed with the university and employer. Submit CV, cover letter, "
            "certificates, current enrolment with semester and "
            "standard-study-period proof; files at most 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in business administration, economics, finance, "
                "business studies or a comparable field; a finance, investment "
                "or financial-services focus is ideal rather than required."
            ),
            (
                "Initial practical experience in financial services, "
                "asset-liability management, asset management or investment "
                "management is requested."
            ),
            (
                "Very good written/spoken German and English; confident MS "
                "Office and numerical work; capital-markets/regulatory "
                "interest; strong analytical/conceptual skills, initiative, "
                "independence and structured work."
            ),
            (
                "Thesis concerns investment income/capital allocation, "
                "portfolio optimisation with the existing alternative "
                "investment fund, return/risk/capital requirements and minimum "
                "return claims, under Solvency II."
            ),
            (
                "Final subject agreed jointly with student, university and "
                "employer; can start from December 2026."
            ),
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Discounts for Employees Possible, "
                "Events for Employees, Good Public Transport, Parking, Canteen,"
                " Café, Inhouse Doctor, Barrier-Free Workplace. Labels using "
                "possible do not guarantee availability; no cash value, "
                "unconditional remote-work promise or universal eligibility is "
                "inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2026-12-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242091": (
                "ef54ce490a0c66fda0377905d85b3d2a"
                "6b06cd1094b9d4a397104c0568dacb22"
            ),
            "242090": (
                "1384850af9dbe129000883effa596999"
                "7ef1c80900849de0eda1239611fab3a0"
            ),
            "242088": (
                "843647dfe6079d38d4803ac6b570e510"
                "836184c5f27e2c4d48aacdebae73ac80"
            ),
        },
    },
    "mer00049bp": {
        "summary": (
            "Supply-chain internship in Böblingen supporting AI/digitalisation "
            "research, workshops, project tracking, reporting and change "
            "activities for AmSupply. For business informatics, computing, "
            "industrial engineering, business or related students with very "
            "good German/English, Microsoft 365 and organisational skills. "
            "Start from February 2027. Apply online with CV, cover letter, "
            "certificates, enrolment/semester and standard-study-period proof; "
            "mandatory-placement proof if applicable, files at most 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in business informatics, computer science, industrial "
                "engineering, business administration or a comparable field."
            ),
            (
                "Independent structured work, strong organisation and "
                "initiative, communication and collaboration with different "
                "stakeholders."
            ),
            (
                "Confident Microsoft 365 including PowerPoint, Excel, Teams and"
                " Outlook; interest in digitalisation, process optimisation and"
                " AI; very good German and English."
            ),
            (
                "Responsibilities include workshops/management meetings, "
                "presentations and decision material, AI/logistics research, "
                "project/action tracking, cross-functional coordination and "
                "innovation/automation support."
            ),
            (
                "Can start from February 2027; required-internship proof is "
                "conditional if applicable, not proof that every internship "
                "must be mandatory."
            ),
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Application requests proof of a compulsory placement if "
                "applicable; this is conditional document wording, separate "
                "from any express mandatory-component requirement in the role."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2027-02-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242174": (
                "d8a085fb85af49b7ba704be25446c7e0"
                "51257c4ae353c78350086a6cc101f2ef"
            ),
            "242178": (
                "1e54d5a466edd69b18610af20d8996bd"
                "ea3d8f6477332f98d72de292e76559f8"
            ),
            "242177": (
                "c3b3c4c4933fc835b2c3098343d86fcd"
                "4e7b7de9ce7c34dc3badcef72121adc7"
            ),
        },
    },
    "mer00049bq": {
        "summary": (
            "Working-student role in Böblingen supporting supply-chain AI, "
            "AmSupply projects, workshops, reporting and cross-team change "
            "coordination. For business informatics, computing, industrial "
            "engineering, business or related students with very good "
            "written/spoken German and English, Microsoft 365, organisation and"
            " communication skills. Start from February 2027. Submit CV, cover "
            "letter, certificates, current enrolment showing semester and "
            "standard-study-period proof online; mark relevant documents, at "
            "most 5 MB each."
        ),
        "condition_evidence": [
            (
                "Studies in business informatics, computer science, industrial "
                "engineering, business administration or a comparable field."
            ),
            (
                "Very good written/spoken German and English; confident "
                "Microsoft 365, particularly PowerPoint, Excel, Teams and "
                "Outlook."
            ),
            (
                "Structured independent work, organisational ability, "
                "initiative, communication and cross-stakeholder collaboration;"
                " interest in digitalisation, process optimisation and AI "
                "applications."
            ),
            (
                "Team works on global AmSupply/SAP S/4HANA rollout and AI/RPA; "
                "role supports workshops, presentations, research, "
                "project/action tracking, information preparation and "
                "change/automation projects."
            ),
            "Can start from February 2027.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Part time and offering type Temporary;"
                " start-date field 2027-02-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242235": (
                "56b4fac6eeeb12f66058e00716767bb5"
                "5f7821c40558a998fca0278cada5d282"
            ),
            "242243": (
                "9d95b5c279bb60cacd2f645b8aac8350"
                "e05d7a1fcba3d0a844f850e62527a583"
            ),
            "242241": (
                "4d76d392ba8b14d20c192926a35a8a92"
                "a7f038e9f30403bdc30b0f318e677dbb"
            ),
        },
    },
    "mer00049br": {
        "summary": (
            "Supply-chain transformation internship in Böblingen, supporting "
            "AmSupply rollouts, project coordination, KPI dashboards, SAP "
            "process documentation and system improvements. For industrial "
            "engineering, business informatics, business, logistics or related "
            "students with very good German/English and MS Office; initial SAP "
            "knowledge is advantageous. Start from February 2027. Online "
            "application needs CV, cover letter, certificates, "
            "enrolment/semester and standard-study-period proof; "
            "mandatory-placement proof if applicable, files up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in industrial engineering, business informatics, "
                "business administration, logistics or a comparable field."
            ),
            (
                "Very good written/spoken German and English; confident MS "
                "Office; initial SAP knowledge is beneficial, not mandatory."
            ),
            (
                "Analytical skills and rapid grasp of complex processes; "
                "communication/teamwork in an international setting; interest "
                "in supply-chain management, digitalisation and IT."
            ),
            (
                "Supports international AmSupply rollouts, workshops/task "
                "coordination/KPI reporting, new system functions, SAP Solution"
                " Manager documentation, process optimisation and dashboard "
                "development; may own subprojects."
            ),
            (
                "Can start from February 2027; required-internship evidence "
                "requested only if applicable."
            ),
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Application requests proof of a compulsory placement if "
                "applicable; this is conditional document wording, separate "
                "from any express mandatory-component requirement in the role."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2027-02-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242234": (
                "91f1ab159109ba59992b5a5f2ff8543c"
                "28c3fffa7c788e2fb644f4005f1dc8ec"
            ),
            "242232": (
                "78b45e5b41072b947b0aa39f195976df"
                "4215f6fb7f03588196ecb9bec4788eb1"
            ),
            "242231": (
                "0892ea81a65a217bdfb7c0e19e72543e"
                "02b1d8faa3b4023fecadb32ca262ce4d"
            ),
        },
    },
    "mer00049bs": {
        "summary": (
            "Working-student IT role in Berlin supporting sales/aftersales "
            "telephony, migrations, incident/change management and "
            "service-provider coordination. For computing, business "
            "informatics, industrial engineering or related students with "
            "above-average performance, very good German/English and MS Office."
            " Network basics and ServiceNow are advantageous, not required. "
            "Start from December 2026; flexible work with remote-working "
            "possibility and mentoring. Online application requires study "
            "evidence and documents up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Ongoing computing, business informatics, industrial "
                "engineering or comparable studies, with above-average academic"
                " performance."
            ),
            (
                "Very good written/spoken German and English and confident MS "
                "Office."
            ),
            (
                "Basic network knowledge such as MAC/IP addressing and "
                "ServiceNow experience are advantageous but explicitly not "
                "prerequisites."
            ),
            (
                "Ability to connect technical/business questions, independence "
                "and communication; role supports telephony projects, "
                "migrations/carve-outs/number transfers, incident/change "
                "coordination, root-cause analyses, devices and escalation "
                "management."
            ),
            (
                "Flexible work environment offers a possibility of remote work "
                "and individual mentoring; not a guarantee of wholly remote "
                "work."
            ),
            "Can start from December 2026.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Canteen, Café, Parking, "
                "Good Public Transport, Barrier-Free Workplace. Labels using "
                "possible do not guarantee availability; no cash value, "
                "unconditional remote-work promise or universal eligibility is "
                "inferred."
            ),
            (
                "API advertises schedule Part time and offering type Temporary;"
                " start-date field 2026-12-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242004": (
                "d8cac94ebfe5b13ed70fdc644f2948f3"
                "ae352c9b5db4a975ef49ed0c31fee816"
            ),
            "241999": (
                "ff62e2e9955f7f97157448d2d2785979"
                "5255082beffd09e6722e3af0bb2bf4ed"
            ),
            "241998": (
                "c252c6d09096ca7d8cd7e5a2d143e24a"
                "57e7ebde71ddc18ae677498ebddd5438"
            ),
        },
    },
    "mer00049bv": {
        "summary": (
            "Working-student AmSupply product-owner support in Böblingen: "
            "international rollouts, workshops, KPI reporting, SAP "
            "documentation and dashboards. For industrial engineering, business"
            " informatics, business, logistics or related students with "
            "confident German/English and MS Office, analytical and teamwork "
            "skills. SAP knowledge is beneficial. Start from February 2027. "
            "Apply online with CV, cover letter, certificates, "
            "enrolment/semester and standard-study-period proof; mark relevant "
            "files, at most 5 MB each."
        ),
        "condition_evidence": [
            (
                "Studies in industrial engineering, business informatics, "
                "business administration, logistics or a comparable field."
            ),
            (
                "Confident written/spoken German and English and MS Office; SAP"
                " knowledge is advantageous rather than required."
            ),
            (
                "Analytical thinking, structured work, communication and "
                "teamwork; interest in supply-chain management, digitalisation "
                "and IT."
            ),
            (
                "Role supports AmSupply international rollouts, workshops/task "
                "tools/KPI reporting, new system functions, SAP Solution "
                "Manager documentation, process optimisation and dashboards, "
                "with scope for independent subprojects."
            ),
            (
                "Team uses SAFe and works on GenAI, AI, RPA and S/4HANA "
                "conversion; participation does not establish a guaranteed "
                "qualification or pay amount."
            ),
            "Can start from February 2027.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Part time and offering type Temporary;"
                " start-date field 2027-02-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242145": (
                "6923a1d7cdce70f77b8e44356f188699"
                "1f4175b48fec88d1c094a42bd4022e94"
            ),
            "242148": (
                "668716c305986e8f5736003549a4771c"
                "06c7d3a079d7dfd5571d9c72e0983c51"
            ),
            "242146": (
                "ac73bb09d4aa30684fcb020c5741072d"
                "b4a56abe514a4d1876da7bb484ba2728"
            ),
        },
    },
    "mer00049by": {
        "summary": (
            "Mandatory internship in battery-production ramp-up at Esslingen am"
            " Neckar, supporting commissioning, process acceptance and "
            "production/quality analysis. For mechanical, industrial, "
            "automation, production or electrical engineering students, or "
            "related fields, with confident German/English and MS Office. "
            "Technical vocational training and a category-B licence are "
            "desirable. Start from February 2027; the placement must include a "
            "required-study component. Submit application and study documents "
            "online, files up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in mechanical engineering, industrial engineering, "
                "automation, production engineering, electrical engineering or "
                "a comparable field."
            ),
            (
                "Confident written/spoken German and English and MS Office; "
                "commitment, teamwork, analytical and strategic work."
            ),
            (
                "Technical vocational training and a category-B driving licence"
                " are desirable, not stated as mandatory."
            ),
            (
                "The internship at this location must have a compulsory "
                "component required by the course."
            ),
            (
                "Supports new high-voltage battery production "
                "commissioning/ramp-up, machine/process acceptance, start-up "
                "planning, analysis tools, production-quality/process KPIs and "
                "cross-location work."
            ),
            "Can start from February 2027.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Application requests proof of a compulsory placement if "
                "applicable; this is conditional document wording, separate "
                "from any express mandatory-component requirement in the role."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Good Public "
                "Transport, Inhouse Doctor, Canteen, Café, Barrier-Free "
                "Workplace, Near-Site Childcare. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2027-02-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "241879": (
                "ee1da656a0a6cda718df3505839c775c"
                "d9d0f688f294105b858d3c900dd0632d"
            ),
            "241878": (
                "f84179832965c65ec6c0711a5d569404"
                "49c55b02f8f14f40604674951b7bab71"
            ),
            "241876": (
                "7515b59f056d25084d7abd78431c876d"
                "fe36b20b83df7158409494c672eab9cd"
            ),
        },
    },
    "mer00049c3": {
        "summary": (
            "Mandatory people-transformation internship in Stuttgart covering "
            "change analysis, enablement/training formats, global workshops and"
            " readiness measurement. For business, psychology, HR, "
            "communication or related students with confident German/English, "
            "analytical/communication skills and MS Office. Start from February"
            " 2027; placement must include a compulsory-study component. Apply "
            "online with CV, cover letter, certificates, enrolment/semester, "
            "standard-study-period proof and required-placement evidence if "
            "applicable; files up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in business, psychology, human resources, "
                "communication or a comparable field."
            ),
            (
                "Interest in change management, organisational development and "
                "transformation; analytical/conceptual skills and structured "
                "independent work."
            ),
            (
                "Confident written/spoken German and English, communication, "
                "commitment and motivation; confident Word, Excel and "
                "PowerPoint."
            ),
            (
                "Role supports audience/needs/impact analysis, change concepts,"
                " training/communication formats, international end-to-end "
                "enablement, workshops, readiness/effectiveness measurement and"
                " quality assurance."
            ),
            (
                "The internship at this location must include a compulsory "
                "component required by the course."
            ),
            "Can start from February 2027.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Application requests proof of a compulsory placement if "
                "applicable; this is conditional document wording, separate "
                "from any express mandatory-component requirement in the role."
            ),
            (
                "Publisher benefit labels: Coaching, Hybrid Work Possible, "
                "Discounts for Employees Possible, Health Benefits, Mobility "
                "Offers, Mobile Phone for Employees Possible, Events for "
                "Employees, Meal-Discounts, Annual Profit Share Possible, "
                "Company Retirement, Flextime Possible, Lifelong Learning, "
                "Parking, Near-Site Childcare, Canteen, Café, Good Public "
                "Transport, Barrier-Free Workplace, Inhouse Doctor. Labels "
                "using possible do not guarantee availability; no cash value, "
                "unconditional remote-work promise or universal eligibility is "
                "inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2027-02-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242172": (
                "df86d2f79b80c7b032447f42f741b5b9"
                "4832ab3fc3d8cc279bc5872f3fd5fc1e"
            ),
            "242170": (
                "8046ac1a0c538e4b1c842e7959262c3e"
                "f85ca0d138e35663fc9897f4020014b9"
            ),
            "242168": (
                "d2a2353e1670ac06832198a189e1cfb9"
                "c3495e6a1dda4796b263cd9a3e4f949a"
            ),
        },
    },
    "mer00049dk": {
        "summary": (
            "Brand-strategy internship in Stuttgart-Vaihingen combining onsite "
            "and mobile work, analysing global image/competitor data and brand "
            "requirements. For business, economics, media management, "
            "communication or related students with very good German/English, "
            "MS Office and analytical skills. AI-analysis affinity and initial "
            "Microsoft 365 Copilot experience are advantages. Start from March "
            "2027; mandatory or combined placement must include a "
            "required-study component. Apply online with study/application "
            "evidence, files up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in business administration, economics/business "
                "studies, media management, communication studies or a "
                "comparable field."
            ),
            (
                "Very good written/spoken German and English, confident MS "
                "Office, commitment/teamwork, analytical and strategic "
                "thinking, and enjoyment of data work."
            ),
            (
                "Affinity for AI-based data analysis and initial Microsoft 365 "
                "Copilot experience are advantageous rather than requirements."
            ),
            (
                "The internship must include a course-prescribed compulsory "
                "component; the title explicitly supports mandatory or combined"
                " internships."
            ),
            (
                "Hybrid work model combines mobile work and onsite work in "
                "Stuttgart-Vaihingen, not wholly remote."
            ),
            (
                "Role analyses global brand-image/competitor data, coordinates "
                "smaller projects and bilingual presentations, contributes to "
                "central image research and derives brand-based vehicle "
                "requirements."
            ),
            "Can start from March 2027.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Application requests proof of a compulsory placement if "
                "applicable; this is conditional document wording, separate "
                "from any express mandatory-component requirement in the role."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2027-03-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242248": (
                "8b8f35d3e3c3a79bae2788865c94c50e"
                "44f0a96a791610787671352b3175a337"
            ),
            "242247": (
                "910b511dd3c7bd4e63f1b2055b3871b0"
                "5ea234e82361bc56f4a53bd5191bb590"
            ),
            "242246": (
                "93223bc82c5a1b4ed35004f448260f23"
                "2ca65f4e15b7e8a53cd954436ad2e57d"
            ),
        },
    },
    "mer00049dm": {
        "summary": (
            "Six-month restraint-system development internship in Sindelfingen,"
            " supporting airbags/seatbelts, suppliers, test planning and "
            "crash-result evaluation. For mechanical, automotive or related "
            "engineering students with confident German/English, MS Office, "
            "analytical/teamwork/communication skills and a car driving "
            "licence. Text says start from mid-November 2026; the API start "
            "field is 24 November. Apply online with CV, cover letter, "
            "certificates, enrolment/semester and standard-study-period proof; "
            "compulsory-placement evidence if applicable, files up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in mechanical engineering, automotive engineering or a"
                " comparable field."
            ),
            (
                "Confident written/spoken German and English and MS Office; "
                "commitment, teamwork, analytical/strategic work and "
                "communication."
            ),
            (
                "A passenger-car driving licence is requested; the notice does "
                "not specify a licence class."
            ),
            (
                "Six-month duration is explicit. Text allows beginning from "
                "mid-November 2026; PositionStartDate is 2026-11-24. Preserve "
                "both rather than invent one reconciled exact date."
            ),
            (
                "Supports airbag/seatbelt development coordination with "
                "domestic/foreign suppliers, planning and evaluation of "
                "sled/component/crash tests, series projects and benchmarking."
            ),
            (
                "Mandatory-placement proof is conditional if applicable; the "
                "title does not make every internship compulsory."
            ),
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Application requests proof of a compulsory placement if "
                "applicable; this is conditional document wording, separate "
                "from any express mandatory-component requirement in the role."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2026-11-24. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["internships"],
        "variant_hashes": {
            "242201": (
                "02706fc74679d596284577711413562d"
                "d968129018bec3eae7b58c96cdf0d741"
            ),
            "242199": (
                "a20b21f9e6be0a2afb27d08a6e2f0c85"
                "090529e730e0993ace91e398a103ae34"
            ),
            "242198": (
                "1ca06dff7e09832785ad83c022e6fd79"
                "117a385d20a2018dc01b26a302645080"
            ),
        },
    },
    "mer00049dy": {
        "summary": (
            "Working-student L2+ automated-driving role in Sindelfingen "
            "supporting test-vehicle operation, software updates, fault "
            "analysis and AI-assisted evaluations. For electrical engineering, "
            "computing or related students with German/English, vehicle-network"
            " basics, initial AI-tool experience, Python basics and a "
            "category-B licence. Start from December 2026. Apply online with "
            "CV, cover letter, certificates, current enrolment showing semester"
            " and standard-study-period proof; mark relevant documents, at most"
            " 5 MB each."
        ),
        "condition_evidence": [
            (
                "Studies in electrical engineering, computer science or a "
                "comparable field."
            ),
            (
                "Interest in passenger-car development, especially "
                "assistance/automation systems; basic vehicle networking such "
                "as Ethernet, CAN or FlexRay."
            ),
            (
                "Confident written/spoken German and English; motivation to "
                "learn/develop tools and methods."
            ),
            (
                "Initial experience with AI-supported tools, exemplified by "
                "Microsoft 365 Copilot, GitHub Copilot or agentic-AI "
                "applications; basic scripting knowledge, particularly Python."
            ),
            (
                "Innovative, creative, solution-oriented thinking and a "
                "category-B driving licence requested."
            ),
            (
                "Work supports a test-vehicle fleet, process/software/fault "
                "handling, trial-result analysis and LLM/agentic-AI-supported "
                "documentation/evaluation."
            ),
            "Can start from December 2026.",
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Part time and offering type Temporary;"
                " start-date field 2026-12-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242253": (
                "aac19461d795d1659bbf8f1bdc1ec82e"
                "1083a18dfd8a11a1ef90ddc2ee704206"
            ),
            "242252": (
                "093594abbe19d7580007035cacd28939"
                "e910bd72cdf511caffe78f61b02ca2e1"
            ),
            "242250": (
                "40def98ffc8471f25eba7407208b9e7a"
                "7a788c5922df96569650e894fa29ba40"
            ),
        },
    },
    "mer00049em": {
        "summary": (
            "Working-student load-carrier planning in Rastatt, supporting "
            "vehicle launches, logistics/assembly/supplier coordination and "
            "economic/feasibility analysis. For logistics, mechanical, "
            "industrial engineering or related students with confident "
            "German/English, MS Office, analytical and technical skills. Text "
            "says start from November 2026; API start is 30 November. Submit "
            "CV, cover letter, certificates, current enrolment showing semester"
            " and standard-study-period proof online; mark relevant files, at "
            "most 5 MB each."
        ),
        "condition_evidence": [
            (
                "Studies in logistics, mechanical engineering, industrial "
                "engineering or a comparable field."
            ),
            (
                "Confident written/spoken German and English, MS Office, "
                "commitment/teamwork, analytical and strategic thinking, and "
                "technical understanding."
            ),
            (
                "Responsibilities include load-carrier concepts for MMA "
                "launches, process improvement, economic/feasibility "
                "assessment, implementation aligned with delivery methods and "
                "presentation of analyses."
            ),
            (
                "Work coordinates logistics, assembly and external suppliers; "
                "team covers Rastatt and Kecskemet, but the advertised work "
                "location is Rastatt, Germany. Do not infer an additional host "
                "country from project scope."
            ),
            (
                "Text allows starting from November 2026; API PositionStartDate"
                " is 2026-11-30. Preserve both rather than invent a different "
                "exact start."
            ),
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Near-Site Childcare, "
                "Canteen, Café, Inhouse Doctor, Barrier-Free Workplace, Good "
                "Public Transport, Parking. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Part time and offering type Temporary;"
                " start-date field 2026-11-30. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242205": (
                "334715f5b7629daff92e1f49cf0e7a4d"
                "4765a05b0322e8d21107f57569e6c8a5"
            ),
            "242204": (
                "147813db635061b63c9baf2e1db10299"
                "02c3b8a9bb4fb42d335ca83a7b6f9588"
            ),
            "242203": (
                "7c964b013d8c9c241565ef41105824db"
                "f042cf615e8cfab6eb9401d0d07eec45"
            ),
        },
    },
    "mer00049f3": {
        "summary": (
            "Thesis placement in Sindelfingen designing innovation-project "
            "control and reporting across the VDIZS process, including KPIs, a "
            "digital cockpit and validation on a real project. For mechanical, "
            "mechatronics, electrical, industrial engineering, business or "
            "related students with German/English and MS Office; programming is"
            " advantageous, not required. Start from December 2026; topic "
            "agreed with university and employer. Apply online with CV, cover "
            "letter, certificates, enrolment/semester and standard-study-period"
            " proof; files up to 5 MB."
        ),
        "condition_evidence": [
            (
                "Studies in mechanical engineering, mechatronics, electrical "
                "engineering, industrial engineering, business administration "
                "or a comparable field."
            ),
            (
                "Confident MS Office and written/spoken German and English; "
                "programming knowledge is advantageous but explicitly not "
                "compulsory."
            ),
            (
                "Commitment/teamwork, analytical/strategic thinking, "
                "complex-process understanding, rapid learning and independent "
                "work, assertiveness, communication and coordination, interest "
                "in logistics innovation/digitalisation."
            ),
            (
                "Thesis analyses innovation-project/reporting requirements and "
                "duplicated work, develops maturity/KPI/status models and a "
                "digital management cockpit with one-time data collection, "
                "examines standard-technology integration, and validates "
                "against a real project such as RTLS."
            ),
            (
                "Final topic jointly agreed with the university, student and "
                "employer; can start from December 2026."
            ),
            (
                "Online application requests CV, cover letter, certificates, "
                "current enrolment certificate showing semester and proof of "
                "standard course duration. Mark documents relevant to the "
                "application; maximum file size 5 MB."
            ),
            (
                "Severely disabled applicants and applicants with equivalent "
                "status are expressly welcomed."
            ),
            (
                "Advertised locations are in Germany; language, enrolment and "
                "driving-licence requirements do not establish citizenship "
                "eligibility. No pay amount is stated in these descriptors."
            ),
            (
                "Publisher benefit labels: Meal-Discounts, Mobile Phone for "
                "Employees Possible, Discounts for Employees Possible, Annual "
                "Profit Share Possible, Events for Employees, Coaching, "
                "Flextime Possible, Hybrid Work Possible, Health Benefits, "
                "Company Retirement, Mobility Offers, Parking, Inhouse Doctor, "
                "Good Public Transport, Barrier-Free Workplace, Near-Site "
                "Childcare, Canteen, Café. Labels using possible do not "
                "guarantee availability; no cash value, unconditional "
                "remote-work promise or universal eligibility is inferred."
            ),
            (
                "API advertises schedule Full time and offering type Temporary;"
                " start-date field 2026-12-01. These fields do not establish a "
                "pay amount or citizenship entitlement."
            ),
        ],
        "categories": ["jobs"],
        "variant_hashes": {
            "242152": (
                "6cb7eb3e5a9182ea932ec1ccfab031aa"
                "579f01fdc39932d8766b23c61726cf7e"
            ),
            "242151": (
                "7eed220c8aa7f9d6ca3584025cdaaa1f"
                "7be7424fe9b73519e362c01fbd9b433c"
            ),
            "242149": (
                "0904fc417693278f4165f9fb0ba5d5d6"
                "d1d396ca1e72514d235ea5bbe3daedfe"
            ),
        },
    },
}

if __name__ == "__main__":
    sys.exit(main())
