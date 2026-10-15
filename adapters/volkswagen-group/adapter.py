"""Discover current Volkswagen AG youth opportunities and their native facts.

The advertised Volkswagen AG partition excludes other Group brands,
non-youth roles and the robots-blocked HRD portal.
English aliases are deduplicated.
Republish minimal original factual summaries and direct official links only.
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

SOURCE_ID = "volkswagen-group"
SOURCE_URL = WEBSITE_URL = "https://www.volkswagen-karriere.de/"
LANGUAGE = "de"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "company"
SOURCE_NAME = "Volkswagen AG student and early-career opportunities"
ATTRIBUTION = (
    "Volkswagen AG — original factual summaries and direct official "
    "individual opportunity links."
)
DESCRIPTION = (
    "Volkswagen AG individual student and early-career listings; "
    "excludes non-youth roles, other Group brands and the "
    "robots-blocked HRD apprenticeship portal."
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
FAMILY = "youthopps-volkswagen-group-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 2400
PUBLICATION_TIMEOUT = 900
RUN_TIMEOUT = 4320
EXPORT_TIMEOUT = 120
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "volkswagen-group-pacing-state"
MAX_PUBLISHER_STARTS = 240
MAX_PUBLISHER_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_REQUEST_STARTS = _PUBLISHER_BYTES = 0
_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_INITIALIZING = False
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
            if _PUBLISHER_BYTES >= MAX_PUBLISHER_BYTES:
                raise AdapterError("Whole publisher byte cap reached", "fetch")
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
        raw = os.environ.get("VOLKSWAGEN_GROUP_PACING_BOOTSTRAP", "")
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


def mark_pacing_artifact_ready(runner_parent, runner_root):
    """Signal only a successfully finalized artifact via fresh step output."""
    value = os.environ.get("GITHUB_OUTPUT")
    if not value:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Fresh step output required", "access")
        return
    if not os.path.isabs(value):
        raise AdapterError("Unsafe step output path", "access")
    relative = os.path.relpath(value, runner_root)
    parts = relative.split(os.sep)
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
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
            ):
                raise AdapterError("Unsafe step output parent", "access")
            parent = descriptor
        descriptor = os.open(
            parts[-1], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=parent
        )
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_nlink != 1
            or info.st_size != 0
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Fresh owned step output required", "access")
        line = b"pacing_ready=true\n"
        if os.write(descriptor, line) != len(line):
            raise AdapterError("Incomplete pacing-ready handoff", "access")
        os.fsync(descriptor)
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def export_family_artifact():
    """Export only healthy infrastructure state; invalidate an owned stale file."""
    path = os.environ.get("VOLKSWAGEN_GROUP_PACING_ARTIFACT_PATH")
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
            mark_pacing_artifact_ready(parent, root)
        except BaseException:
            # An incomplete new file cannot masquerade as a valid artifact.
            os.unlink(name, dir_fd=parent)
            raise
    finally:
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
        actual, length = self.data[self.offset : self.offset + 2]
        self.offset += 2
        if actual != tag:
            raise ValueError("Unexpected DER tag")
        if length & 128:
            count = length & 127
            if not 1 <= count <= 4:
                raise ValueError("Invalid DER length")
            raw = self.data[self.offset : self.offset + count]
            if len(raw) != count or raw[0] == 0:
                raise ValueError("Nonminimal DER length")
            self.offset += count
            length = int.from_bytes(raw, "big")
            if length < 128:
                raise ValueError("Nonminimal DER length")
        end = self.offset + length
        if end > len(self.data):
            raise ValueError("Incomplete DER value")
        result = self.data[self.offset : end]
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
            r"([A-Za-z0-9+/=\r\n]+)-----END \1-----\s*",
            pem,
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
            version != 0
            or not 2048 <= n.bit_length() <= 8192
            or e < 3
            or e % 2 == 0
            or e >= n
            or min(d, p, q, dp, dq, inverse) <= 0
            or d >= n
            or inverse >= p
            or p == q
            or p % 2 == 0
            or q % 2 == 0
            or p * q != n
            or e * d % math.lcm(p - 1, q - 1) != 1
            or dp != d % (p - 1)
            or dq != d % (q - 1)
            or inverse * q % p != 1
        ):
            raise ValueError("Inconsistent RSA key")
        return n, e, d
    except Exception:
        raise AdapterError(
            ("Invalid GitHub App signing key"), ("publish")
        ) from None


def app_jwt(now):
    app_id = os.environ.get("WEBSITE_APP_ID", "")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", app_id):
        raise AdapterError("Invalid GitHub App identifier", "publish")
    n, e, d = rsa_key(os.environ.get("WEBSITE_APP_PRIVATE_KEY", ""))

    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")

    header = encode(b'{"alg":"RS256","typ":"JWT"}')
    payload = encode(
        json.dumps(
            {
                "iat": int(now) - 60,
                "exp": int(now) + 540,
                "iss": app_id,
            },
            separators=(",", ":"),
        ).encode()
    )
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
            "https://api.github.com" + path,
            method=method,
            headers={
                "Authorization": "Bearer " + jwt,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            payload=(
                json.dumps(payload).encode() if payload is not None else None
            ),
        )
        if status not in (200, 201):
            raise ValueError("App API failure")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError("Invalid App response")
        return result
    except Exception:
        raise AdapterError(
            ("GitHub App authentication failed"), ("publish")
        ) from None


def publication_token():
    """Renew a repository-scoped installation token before expiry/CAS."""
    global _APP_TOKEN, _APP_EXPIRY, _APP_CONFIGURATION
    if not os.environ.get("WEBSITE_APP_ID"):
        raise AdapterError("GitHub App configuration required", "publish")
    configuration = hashlib.sha256(
        (
            os.environ.get("WEBSITE_APP_ID", "")
            + "\0"
            + os.environ.get("WEBSITE_APP_INSTALLATION_ID", "")
            + "\0"
            + os.environ.get("WEBSITE_APP_PRIVATE_KEY", "")
        ).encode()
    ).digest()
    if _APP_CONFIGURATION != configuration:
        _APP_TOKEN, _APP_EXPIRY = None, 0
        _APP_CONFIGURATION = configuration
    now = time.time()
    if _APP_TOKEN and now + 300 < _APP_EXPIRY <= now + 3700:
        return _APP_TOKEN
    jwt = app_jwt(now)
    installation = app_api(
        "GET",
        "/repos/YouthOpps/data-source/installation",
        jwt,
    )
    identity = installation.get("id")
    expected = os.environ.get("WEBSITE_APP_INSTALLATION_ID", "")
    account = installation.get("account", {})
    if (
        type(identity) is not int
        or identity <= 0
        or str(identity) != expected
        or not isinstance(account, dict)
        or installation.get("app_id") != int(os.environ["WEBSITE_APP_ID"])
        or account.get("login") != "YouthOpps"
        or account.get("type") != "Organization"
    ):
        raise AdapterError("Unexpected GitHub App installation", "publish")
    result = app_api(
        ("POST"),
        (f"/app/installations/{identity}/access_tokens"),
        jwt,
        {
            "repositories": ["data-source"],
            "permissions": {"contents": "write"},
        },
    )
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
        raise AdapterError(
            ("Invalid installation token expiry"), ("publish")
        ) from None
    now = time.time()
    if (
        not isinstance(result.get("token"), str)
        or not re.fullmatch(r"[!-~]{1,4096}", result["token"])
        or not now + 300 < expiry <= now + 3700
        or not isinstance(repositories, list)
        or len(repositories) != 1
        or not isinstance(repositories[0], dict)
        or repositories[0].get("full_name") != "YouthOpps/data-source"
        or not isinstance(permissions, dict)
        or permissions.get("contents") != "write"
        or any(
            key not in ("contents", "metadata")
            or key == "metadata"
            and value != "read"
            for key, value in permissions.items()
        )
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
            validate_records(records, require_complete=True)
            records = remove_expired_records(records)
            checked = utc_now()
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
                with phase("failure", PUBLICATION_TIMEOUT):
                    if snapshot is None:
                        raise AdapterError(
                            "No validated previous source pair", "publish"
                        )
                    latest = read_snapshot()
                    if desired is not None and latest["files"] == desired:
                        outcome = json.loads(desired["metadata.json"])
                        failure = None
                    elif previous and latest["files"] == snapshot["files"]:
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
        elif not previous:
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
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        leaf = FAMILY + "-" + str(os.getuid())
        created = False
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        linked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_nlink < 2
            or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            os.close(descriptor)
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _STATE_INITIALIZING = created
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
    descriptor = secure_file(
        "state.lock", os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0)
    )
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
        if not _STATE_INITIALIZING:
            raise AdapterError(
                "Mature publisher state is missing", "access"
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
    global _STATE_INITIALIZING
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
        # Once a state has existed, missing state must never become fresh again,
        # including if the following directory fsync fails.
        _STATE_INITIALIZING = False
        os.fsync(_STATE_DIR_FD)
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
        ("issue117-vw-research", "vw-family.budget.json"),
        ("youthopps-source-triage/117", "pacing.json"),
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
    descriptor = secure_file(
        "collection.lock",
        os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
    )
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
        state["not_before"] = max(state["not_before"], _COLLECTION_STARTED + 60)
        state["observed_at"] = time.time()
        save_budget(state)
    _ROBOTS.clear()
    _STATE_TRUSTED = True


def public_url(value):
    """Permit guarded policy inputs and literal discovery routes only."""
    parsed = urllib.parse.urlsplit(value)
    clean = urllib.parse.urlunsplit(parsed._replace(fragment=""))
    if (
        clean
        not in {info["url"] for info in INPUTS.values()} | _DISCOVERED_URLS
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError("Unreviewed public Volkswagen AG route", "access")
    return clean


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
            raise AdapterError(
                "Unreviewed Volkswagen AG robots origin", "access"
            )
        read_input(key)
    parser = _ROBOTS[origin]
    if not robots_directive(parser, "can_fetch", url):
        raise AdapterError(
            "Publisher robots excludes required Volkswagen AG input", "access"
        )


def fetch_public(url):
    """Retry only bounded transient server failures under original pacing."""
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(reviewed, publisher=True)
        if status == 200:
            return status, headers, body
        if status in (502, 503, 504) and attempt == 0:
            with locked_budget():
                state = load_budget()
                now = time.time()
                state["not_before"] = max(state["not_before"], now + 30)
                state["observed_at"] = now
                save_budget(state)
            continue
        raise AdapterError(
            "Required publisher input returned HTTP " + str(status),
            "fetch",
            status,
        )


def read_input(key):
    import urllib.robotparser

    info = INPUTS[key]
    status, headers, body = fetch_public(info["url"])
    content_type = (
        headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
    )
    if info["format"] == "robots":
        if content_type not in {"text/plain", "text/html"}:
            raise AdapterError("Unexpected robots response format", "access")
        digest = hashlib.sha256(body).hexdigest()
    else:
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise AdapterError("Unexpected programme response format", "parse")
        digest = html_fingerprint(body)
    if digest != info["sha256"]:
        raise AdapterError(
            "Reviewed Volkswagen AG material or frontier changed: " + key,
            "parse",
        )
    if info["format"] == "robots":
        parsed = urllib.parse.urlsplit(info["url"])
        origin = parsed.scheme + "://" + parsed.netloc
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(body.decode("utf-8", "strict").splitlines())
        delay = robots_directive(parser, "crawl_delay")
        if delay is not None and delay > 6:
            with locked_budget():
                state = load_budget()
                state["interval"] = max(state.get("interval", 6), delay)
                if state["starts"]:
                    state["not_before"] = max(
                        state["not_before"], state["starts"][-1] + delay
                    )
                state["observed_at"] = time.time()
                save_budget(state)
        rate = robots_directive(parser, "request_rate")
        if rate and rate.seconds / rate.requests > 6:
            raise AdapterError(
                "Stricter publisher request rate requires review", "access"
            )
        _ROBOTS[origin] = parser
    return digest


def read_pages():
    """Retrieve the current bounded partition and its individual details."""
    global _CURRENT_RECORD_IDS
    _CURRENT_RECORD_IDS = None
    _DISCOVERED_URLS.clear()
    policies = [
        key for key, info in INPUTS.items() if info["format"] == "robots"
    ]
    guarded = [key for key, info in INPUTS.items() if info["format"] == "html"]
    for key in policies + guarded:
        read_input(key)
    german = discover_listing(INPUTS["german-listing"]["url"], "de")
    english = discover_listing(INPUTS["english-listing"]["url"], "en")
    if len(german) + len(english) > 220:
        raise AdapterError(
            "Current Volkswagen AG detail frontier exceeds review bound",
            "parse",
        )
    profiles = {}
    excluded = set()
    withdrawn = set()
    for url, listing in german.items():
        profile = parse_detail(read_document(url), url, listing)
        if profile is None:
            withdrawn.add(url)
            continue
        identity = profile["identity"]
        if identity in profiles or identity in excluded:
            raise AdapterError("Duplicate current native JobID", "parse")
        if profile["excluded"]:
            excluded.add(identity)
        else:
            profiles[identity] = profile
    for url, listing in english.items():
        alias = parse_detail(read_document(url), url, listing)
        if alias is None:
            withdrawn.add(url)
            continue
        identity = alias["identity"]
        if alias["excluded"]:
            if identity not in excluded:
                raise AdapterError(
                    "English excluded alias lacks German identity", "parse"
                )
            continue
        if identity not in profiles:
            raise AdapterError(
                "English alias lacks current German opportunity", "parse"
            )
        canonical = profiles[identity]
        if (
            alias["level"] != canonical["level"]
            or alias["location"] != canonical["location"]
        ):
            raise AdapterError(
                "English alias contradicts native career level/location",
                "parse",
            )
        canonical.setdefault("aliases", []).append(url)
    records = [make_record(profile) for profile in profiles.values()]
    for record, profile in zip(records, profiles.values()):
        record["classification"]["evidence"].extend(profile.get("aliases", []))
    _CURRENT_RECORD_IDS = {record["id"] for record in records}
    print(
        f"Current Volkswagen AG census: {len(german)} German listings, "
        f"{len(english)} English aliases; {len(records)} qualifying JobIDs, "
        f"{len(excluded)} non-youth JobIDs, "
        f"{len(withdrawn)} explicit withdrawals.",
        file=sys.stderr,
    )
    validate_records(records, require_complete=True)
    return records


def profile_identity(profile):
    return (
        SOURCE_ID
        + "-"
        + hashlib.sha256(
            (SOURCE_ID + "|" + profile["identity"]).encode()
        ).hexdigest()[:24]
    )


def make_record(profile, checked=None):
    """Normalize only facts extracted from the current official detail page."""
    now = checked or utc_now()
    category, categories = _LEVEL_CATEGORIES[profile["level"]]
    if profile["level"] == "Studierende" and re.search(
        r"Abschlussarbeit|Masterarbeit",
        profile["title"],
    ):
        category, categories = "training", ["training"]
    summary, window, requirements = summarize_detail(profile)
    status = "unknown"
    if window:
        today = (
            datetime.fromisoformat(now.replace("Z", "+00:00"))
            .astimezone(timezone(timedelta(hours=-12)))
            .date()
            .isoformat()
        )
        if today > window["closes"]:
            status = "expired"
        elif window["opens"] <= today:
            status = "open"
    record = {
        "id": profile_identity(profile),
        "title": profile["title"],
        "url": profile["url"],
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": None,
        "summary": summary,
        "summary_language": profile["language"],
        "tags": [],
        "location": profile["location"],
        "deadline": window["closes"] if window else None,
        "language": profile["language"],
        "category": category,
        "categories": categories,
        "kind": "individual-opportunity",
        "host_countries": ["DE"],
        "eligible_countries": [],
        "publisher_country": PUBLISHER_COUNTRY,
        "classification": {
            "method": "publisher-native-fields",
            "status": "classified",
            "evidence": [
                profile["url"],
                "Actual publisher Karrierelevel: " + profile["level"],
                "Actual publisher JobID: " + profile["identity"],
            ],
        },
        "status": status,
    }
    record["requirements"] = requirements
    for key in (
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    ):
        record[key] = now
    return record


def validate_owned_records(records):
    """Validate current and earlier published source-owned records."""
    temporal = {
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    for record in records:
        url = urllib.parse.urlsplit(record["url"])
        classification = record.get("classification")
        if (
            record.get("source") != SOURCE_ID
            or record.get("source_url") != SOURCE_URL
            or url.netloc != "jobs.volkswagen-group.com"
            or not re.fullmatch(r"/Volkswagen/job/[^/]+/[1-9][0-9]*/", url.path)
            or url.query
            or url.fragment
            or record.get("publisher_country") != "DE"
            or record.get("host_countries") != ["DE"]
            or record.get("eligible_countries") != []
            or record.get("kind") != "individual-opportunity"
            or record.get("language") not in {"de", "en"}
            or record.get("summary_language") not in {"de", "en"}
            or record.get("status") not in {"open", "expired", "unknown"}
            or record.get("category") not in CATEGORIES
            or not isinstance(record.get("categories"), list)
            or record["category"] not in record["categories"]
            or not set(record["categories"]) <= CATEGORIES
            or not isinstance(classification, dict)
            or set(classification) != {"method", "status", "evidence"}
            or classification.get("method")
            not in {"editorial-review", "publisher-native-fields"}
            or classification.get("status") != "classified"
            or not isinstance(classification.get("evidence"), list)
            or record["url"] not in classification["evidence"]
        ):
            raise AdapterError(
                "Invalid owned Volkswagen AG factual contract", "validate"
            )
        evidence = classification["evidence"]
        job_ids = {
            item.split(": ", 1)[1]
            for item in evidence
            if isinstance(item, str)
            and item.startswith("Actual publisher JobID: ")
        }
        if classification["method"] == "publisher-native-fields":
            if (
                len(job_ids) != 1
                or not re.fullmatch(r"[1-9][0-9]*", next(iter(job_ids)))
                or profile_identity({"identity": next(iter(job_ids))})
                != record["id"]
            ):
                raise AdapterError(
                    "Native stable JobID does not match record", "validate"
                )
        deadline = record.get("deadline")
        if deadline is not None:
            if not isinstance(deadline, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}", deadline
            ):
                raise AdapterError("Unsupported native deadline", "validate")
            try:
                datetime.strptime(deadline, "%Y-%m-%d")
            except ValueError:
                raise AdapterError(
                    "Invalid native deadline", "validate"
                ) from None
        clocks = {
            field: datetime.fromisoformat(record[field].replace("Z", "+00:00"))
            for field in temporal
        }
        if not (
            clocks["created_at"]
            <= clocks["updated_at"]
            <= clocks["last_checked_at"]
            and clocks["first_seen_at"]
            <= clocks["last_seen_at"]
            <= clocks["last_checked_at"]
        ):
            raise AdapterError("Invalid Volkswagen AG chronology", "validate")


def remove_expired_records(records, now=None):
    """Filter a validated refresh; never mutate the prior published snapshot."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=-12))).date()
    retained = []
    for record in records:
        value = record.get("deadline")
        expired = False
        if isinstance(value, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    expired = datetime.fromisoformat(value).date() < today
                else:
                    closing = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = closing.tzinfo is not None and closing < now
            except ValueError:
                # Ambiguous deadlines never establish expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, *, allow_empty=False, require_complete=False):
    if (
        not isinstance(records, list)
        or (not records and not allow_empty)
        or require_complete
        and (
            _CURRENT_RECORD_IDS is None
            or {record.get("id") for record in records} != _CURRENT_RECORD_IDS
        )
    ):
        raise AdapterError(
            "Incomplete current Volkswagen AG identity inventory", "validate"
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
        "classification",
        "status",
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    identities = set()
    for record in records:
        if not isinstance(record, dict) or set(record) not in (
            expected_fields,
            expected_fields | {"requirements"},
        ):
            raise AdapterError(
                "Invalid complete source record contract", "validate"
            )
        if (
            not isinstance(record.get("id"), str)
            or not re.fullmatch(
                SOURCE_ID + r"-[0-9a-f]{24}", record.get("id", "")
            )
            or record["id"] in identities
        ):
            raise AdapterError(
                "Invalid or duplicate Volkswagen AG identity", "validate"
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
                    "Invalid Volkswagen AG UTC timestamp", "validate"
                ) from None
        summary = record["summary"]
        if (
            not isinstance(summary, str)
            or not summary.strip()
            or len(summary.encode("utf-16-le")) // 2 > 600
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
    for record in records:
        requirements = record.get("requirements")
        if require_complete and requirements is None:
            raise AdapterError(
                "Current raw record lacks native requirements", "validate"
            )
        if requirements is not None and (
            not isinstance(requirements, list)
            or not 1 <= len(requirements) <= 64
            or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value.encode("utf-16-le")) // 2 > 2000
                or re.search(r"<[^>]+>|[\x00-\x1f\x7f]", value)
                for value in requirements
            )
        ):
            raise AdapterError("Invalid native requirements array", "validate")
    validate_owned_records(records)


def parse_inventory(records):
    validate_records(records, require_complete=True)
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
        "description": DESCRIPTION,
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
                "Volkswagen AG individual opportunities"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


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
            "description",
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
        or previous.get("description") != DESCRIPTION
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


_CURRENT_RECORD_IDS = None
_DISCOVERED_URLS = set()
_LEVEL_CATEGORIES = {
    "Auszubildende": ("training", ["training", "jobs"]),
    "Dual Studierende": ("training", ["training", "jobs"]),
    "Praktikanten": ("internships", ["internships"]),
    "Studierende": ("jobs", ["jobs"]),
    "Doktoranden": ("jobs", ["jobs", "training"]),
    "Berufseinsteigende": ("jobs", ["jobs"]),
    "Referendare": ("training", ["training", "internships"]),
}
_EXCLUDED_LEVELS = {"Berufserfahrene", "Führungskräfte & Management"}
_ENGLISH_LEVELS = {
    "Interns": "Praktikanten",
    "PhD students": "Doktoranden",
    "Entry Level": "Berufseinsteigende",
    "Professionals": "Berufserfahrene",
}


def read_document(url):
    """Read a fresh permitted document without freezing ordinary job facts."""
    _, headers, body = fetch_public(url)
    content_type = (
        headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    )
    if content_type not in {"text/html", "application/xhtml+xml"}:
        raise AdapterError("Unexpected Volkswagen AG document format", "parse")
    return document_root(body.decode("utf-8", "strict"))


def unique_property(root, key):
    values = {
        n.text()
        for n in nodes(root)
        if n.attrs.get("data-careersite-propertyid") == key
    }
    if len(values) != 1 or not next(iter(values)):
        raise AdapterError(
            "Missing or conflicting native job field: " + key, "parse"
        )
    return values.pop()


def listing_route(base, href, locale):
    """Retain the advertised locale when following a literal pagination link."""
    url = urllib.parse.urljoin(base, href)
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = dict(pairs)
    expected = {
        "q": "",
        "sortColumn": "referencedate",
        "sortDirection": "desc",
        "searchby": "location",
        "d": "15",
        "optionsFacetsDD_facility": "Volkswagen AG",
    }
    if (
        parsed.scheme != "https"
        or parsed.netloc != "jobs.volkswagen-group.com"
        or parsed.path != "/Volkswagen/search/"
        or parsed.fragment
        or len(pairs) != len(query)
        or set(query) - set(expected) - {"startrow", "locale"}
        or any(query.get(key) != value for key, value in expected.items())
        or query.get("locale", locale) != locale
        or not re.fullmatch(r"[0-9]+", query.get("startrow", ""))
        or int(query["startrow"]) % 25
        or not 0 < int(query["startrow"]) < 200
    ):
        raise AdapterError("Changed Volkswagen AG pagination route", "parse")
    if "locale" not in query:
        url += "&locale=" + locale
    _DISCOVERED_URLS.add(url)
    return url, int(query["startrow"])


def parse_listing(root, url, language):
    expression = (
        r"Ergebnisse (\d+) [–-] (\d+) von (\d+) Seite (\d+) von (\d+)"
        if language == "de"
        else r"Results (\d+) [–-] (\d+) of (\d+) Page (\d+) of (\d+)"
    )
    spans = {
        tuple(map(int, match)) for match in re.findall(expression, root.text())
    }
    if len(spans) != 1:
        raise AdapterError(
            "Missing complete advertised Volkswagen AG census", "parse"
        )
    first, last, total, page, pages = spans.pop()
    if (
        not 0 < total <= 200
        or pages != math.ceil(total / 25)
        or first != (page - 1) * 25 + 1
        or last != min(page * 25, total)
        or not 1 <= page <= pages
    ):
        raise AdapterError("Invalid Volkswagen AG page range", "parse")
    rows = nodes(root, "tr", "data-row")
    if len(rows) != last - first + 1:
        raise AdapterError(
            "Incomplete native Volkswagen AG listing page", "parse"
        )
    listings = {}
    for row in rows:
        anchors = nodes(row, "a", "jobTitle-link")
        urls = {
            urllib.parse.urljoin(url, n.attrs.get("href", "")) for n in anchors
        }
        titles = {n.text() for n in anchors}
        if len(urls) != 1 or len(titles) != 1:
            raise AdapterError(
                "Conflicting native individual job links", "parse"
            )
        detail, title = urls.pop(), titles.pop()
        parsed = urllib.parse.urlsplit(detail)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "jobs.volkswagen-group.com"
            or not re.fullmatch(
                r"/Volkswagen/job/[^/]+/[1-9][0-9]*/", parsed.path
            )
            or parsed.query
            or parsed.fragment
            or not title
            or detail in listings
        ):
            raise AdapterError(
                "Invalid literal Volkswagen AG detail route", "parse"
            )
        company = one(nodes(row, "td", "colFacility"), "company cell").text()
        location = one(nodes(row, "td", "colLocation"), "location cell").text()
        published = one(
            nodes(row, "td", "colDate"), "publication date cell"
        ).text()
        if company != "Volkswagen AG" or not re.fullmatch(
            r"DE|.+, DE, \d{5}(?: \+\d+ weitere …)?", location
        ):
            raise AdapterError(
                "Advertised Volkswagen AG partition changed", "parse"
            )
        try:
            stamp = datetime.strptime(
                published, "%d.%m.%Y" if language == "de" else "%b %d, %Y"
            )
        except ValueError:
            raise AdapterError(
                "Unsupported literal publication date", "parse"
            ) from None
        _DISCOVERED_URLS.add(detail)
        listings[detail] = {
            "title": title,
            "location": location,
            "language": language,
            "published_date": stamp.date().isoformat(),
        }
    locale = "de_DE" if language == "de" else "en_US"
    next_pages = {}
    for anchor in nodes(root, "a"):
        href = anchor.attrs.get("href", "")
        if "startrow=" in href:
            route, offset = listing_route(url, href, locale)
            if offset >= total:
                raise AdapterError("Pagination exceeds current census", "parse")
            before = next_pages.get(offset)
            if before and before != route:
                raise AdapterError(
                    "Conflicting native pagination routes", "parse"
                )
            next_pages[offset] = route
    return listings, next_pages, (first, last, total, page, pages)


def discover_listing(seed, language):
    pending = {0: seed}
    visited = set()
    inventory = {}
    expected_total = None
    while pending:
        offset = min(pending)
        url = pending.pop(offset)
        if offset in visited:
            continue
        rows, following, span = parse_listing(read_document(url), url, language)
        if span[0] != offset + 1:
            raise AdapterError(
                "Volkswagen AG pagination did not advance", "parse"
            )
        if expected_total is None:
            expected_total = span[2]
        if span[2] != expected_total or set(inventory) & set(rows):
            raise AdapterError(
                "Volkswagen AG current inventory changed mid-scan", "parse"
            )
        inventory.update(rows)
        visited.add(offset)
        for target, route in following.items():
            if target not in visited:
                pending[target] = route
    if (
        visited != set(range(0, expected_total, 25))
        or len(inventory) != expected_total
    ):
        raise AdapterError(
            "Incomplete current Volkswagen AG pagination", "parse"
        )
    return inventory


def description_sections(description):
    """Retain qualification, document and benefit boundaries."""
    sections = {}
    current = ""

    def events(node):
        if node.tag in {"h1", "h2", "h3", "p", "li"}:
            yield node.tag, node.text()
            return
        for child in node.children:
            if isinstance(child, Node):
                yield from events(child)

    for tag, text in events(description):
        text = text.replace("\xad", "")
        if (
            tag.startswith("h")
            or text == "Das bieten wir"
            or text.startswith("Folgende Dokumente sind")
            or text.startswith("Folgende Unterlagen sind")
        ):
            current = text
        elif text:
            sections.setdefault(current, []).append(text)
    return sections


def application_window(text):
    matches = set(
        re.findall(
            (
                r"(?:Bewerbungszeitraum|Bewerbungsfrist) (?:läuft|"
                r"geht) vom (\d{2}\.\d{2}\.\d{4}) bis (?:zum "
                r")?(\d{2}\.\d{2}\.\d{4})"
            ),
            text,
        )
    )
    if len(matches) > 1:
        raise AdapterError("Conflicting native application windows", "parse")
    if not matches:
        if re.search(r"Bewerbungszeitraum|Bewerbungsfrist", text):
            raise AdapterError(
                "Unsupported explicit application date clause", "parse"
            )
        return None
    began, closes = matches.pop()
    try:
        opened = datetime.strptime(began, "%d.%m.%Y").date()
        closed = datetime.strptime(closes, "%d.%m.%Y").date()
    except ValueError:
        raise AdapterError(
            "Invalid native application dates", "parse"
        ) from None
    if opened > closed:
        raise AdapterError("Native application window reversed", "parse")
    return {"opens": opened.isoformat(), "closes": closed.isoformat()}


def parse_detail(root, url, listing):
    """Bind factual listings to the native JobID, never the postal slug."""
    notices = (
        "Der Veröffentlichungszeitraum für diese Position ist "
        "bereits abgelaufen.",
        "The posting for this position has already expired.",
    )
    if any(notice in root.text() for notice in notices):
        if any(
            node.attrs.get("data-careersite-propertyid") == "customfield1"
            or node.attrs.get("itemprop") == "description"
            for node in nodes(root)
        ):
            raise AdapterError(
                "Conflicting native posting-expiry notice", "parse"
            )
        return None
    identity = unique_property(root, "customfield1")
    title = unique_property(root, "title").replace("\xad", "")
    company = unique_property(root, "facility")
    location = unique_property(root, "location")
    level = unique_property(root, "customfield2")
    addresses = {
        n.attrs.get("content")
        for n in nodes(root, "meta")
        if n.attrs.get("itemprop") == "streetAddress"
    }
    if not addresses or any(
        not isinstance(item, str) or not re.fullmatch(r"DE|.+, DE, \d{5}", item)
        for item in addresses
    ):
        raise AdapterError(
            "Native Volkswagen AG work locations changed scope", "parse"
        )
    advertised = listing["location"]
    truncation = re.fullmatch(r"(.+, DE, \d{5}) \+(\d+) weitere …", advertised)
    if truncation:
        if (
            truncation[1] not in addresses
            or len(addresses) != int(truncation[2]) + 1
        ):
            raise AdapterError(
                "Incomplete native multi-location opportunity", "parse"
            )
    elif addresses != {advertised}:
        raise AdapterError("Native location contradicts listing", "parse")
    locations = (
        re.findall(r"[^,]+, DE, \d{5}", location)
        if location != "DE"
        else ["DE"]
    )
    if {value.strip() for value in locations} != addresses:
        raise AdapterError("Native location fields disagree", "parse")
    location = "; ".join(value.strip() for value in locations)
    canonical = {
        n.attrs.get("href")
        for n in nodes(root, "link")
        if n.attrs.get("rel") == "canonical"
    }
    if (
        not re.fullmatch(r"[1-9][0-9]*", identity)
        or company != "Volkswagen AG"
        or title != listing["title"].replace("\xad", "")
        or canonical != {url}
    ):
        raise AdapterError(
            "Conflicting Volkswagen AG native job identity", "parse"
        )
    if listing["language"] == "en":
        if level not in _ENGLISH_LEVELS:
            raise AdapterError(
                "Unknown native English career level: " + level, "parse"
            )
        level = _ENGLISH_LEVELS[level]
    if level in _EXCLUDED_LEVELS:
        return {
            "identity": identity,
            "excluded": True,
            "url": url,
            "level": level,
        }
    if level not in _LEVEL_CATEGORIES:
        raise AdapterError(
            "Unreviewed Volkswagen AG career level: " + level, "parse"
        )
    descriptions = [
        n for n in nodes(root) if n.attrs.get("itemprop") == "description"
    ]
    if not descriptions or len({node.text() for node in descriptions}) != 1:
        raise AdapterError(
            "Conflicting native individual descriptions", "parse"
        )
    description = descriptions[0]
    if not description.text():
        raise AdapterError("Empty native job description", "parse")
    return {
        "identity": identity,
        "excluded": False,
        "url": url,
        "level": level,
        "title": title,
        "location": location,
        "language": listing["language"],
        "published_date": listing["published_date"],
        "description": description,
    }


def compact_qualification(text):
    """Preserve qualification alternatives and numeric thresholds in recaps."""
    text = text.replace("\u200b", "").replace("\xad", "")
    for before, after in _FACT_CONTRACTIONS:
        text = text.replace(before, after)
    return " ".join(text.split()).strip(" .;,")


def summarize_detail(detail):
    sections = description_sections(detail["description"])
    qualifications = sections.get(
        "Anforderungen an die Qualifikation",
        sections.get("Anforderung an die Qualifikation", []),
    )
    if not qualifications:
        raise AdapterError("Missing native qualifications", "parse")
    documents = []
    benefits = sections.get("Das bieten wir", [])
    for heading, values in sections.items():
        if heading.startswith(
            ("Folgende Dokumente sind", "Folgende Unterlagen sind")
        ):
            documents.extend(values)
    facts = []
    for requirement in qualifications:
        if nonspecific_personality(requirement):
            continue
        value = compact_qualification(requirement)
        if value:
            facts.append(value)
    docs = []
    for value in documents:
        if value.startswith(
            (
                "Für eine zügige",
                "Kontakt für",
                "Der Bruttostundenlohn",
                "Das Bruttomonatsentgelt",
                "Höhe des aktuellen Mindestlohns",
                "Weitere Informationen",
            )
        ):
            continue
        if re.search(r"Bachelorstudierende.*(?:CP|CreditPoints)", value):
            facts.append(compact_qualification(value))
            continue
        if re.search(
            (
                r"Lebenslauf|Anschreiben|Motivationsschreiben|"
                r"Immatrikulations|Notenspiegel|Notenübersicht|"
                r"Arbeitserlaubnis|Aufenthaltserlaubnis|"
                r"Führungszeugnis|Zeugnis|Bescheinigung|Zertifikat|"
                r"Abschluss|Portfolio|Arbeitsprobe|Mappe"
            ),
            value,
            re.IGNORECASE,
        ):
            docs.append(compact_qualification(value))
        else:
            raise AdapterError(
                "Unreviewed native application document clause", "parse"
            )
    if docs:
        facts.append("Dok.: " + ", ".join(docs))
    requirements = [
        "Qualifikation: " + fact
        for fact in facts
        if not fact.startswith("Dok.:")
    ]
    requirements.extend("Dokument: " + doc for doc in docs)
    text = detail["description"].text().replace("\xad", "")
    window = application_window(text)
    if window:
        facts.append("Bewerbung ab " + window["opens"])
    starts = set(
        re.findall(
            r"starten (?:zum|im) (\d{2}\.\d{2}\.\d{4}|[A-Za-zäöüÄÖÜ]+ \d{4})",
            text,
        )
    )
    title_years = set(re.findall(r"\b20\d{2}\b", detail["title"]))
    if len(starts) > 1:
        raise AdapterError("Conflicting native start dates", "parse")
    if starts:
        facts.append("Start: " + next(iter(starts)))
    if starts and title_years:
        start = next(iter(starts))
        start_year = re.search(r"20\d{2}", start).group()
        if start_year not in title_years:
            facts.append(
                "Titeljahr "
                + "/".join(sorted(title_years))
                + "; Start "
                + start
            )
    pay = []
    for clause in benefits:
        if re.fullmatch(
            (
                r"Attraktive Vergütung & (\d+) Urlaubstage \(\+ "
                r"24\.12\. und 31\.12\. frei\)"
            ),
            clause,
        ):
            days = re.search(r"(\d+) Urlaubstage", clause).group(1)
            pay.append(days + " Urlaubstage+24./31.12. frei")
        elif clause.startswith(
            ("Attraktive Ausbildungsvergütung", "Attraktive Vergütung")
        ):
            match = re.fullmatch(
                (
                    r"Attraktive (?:Ausbildungsvergütung|Vergütung) "
                    r"von monatlich (über )?([\d.,]+)\s*€"
                ),
                clause,
            )
            if not match:
                raise AdapterError(
                    "Unsupported native remuneration clause", "parse"
                )
            pay.append((">" if match[1] else "") + match[2] + "€/Monat")
        elif re.fullmatch(
            r"\d+ Urlaubstage \+ 24\.12\. und 31\.12\. frei", clause
        ):
            pay.append(clause.split()[0] + " Urlaubstage+24./31.12. frei")
        elif re.fullmatch(r"\d+-Stunden-Woche", clause):
            pay.append(clause.split("-")[0] + "h/W")
        elif re.fullmatch(
            r"(\d+)-Stunden-Woche, flexible Arbeitszeit, mobile Arbeit", clause
        ):
            hours = clause.split("-")[0]
            pay.append(hours + "h/W; flexibel/mobil")
        elif clause.startswith("Das Bruttomonatsentgelt für diese Stelle"):
            pattern = (
                r"Das Bruttomonatsentgelt für diese Stelle liegt auf "
                r"Basis einer (\d+)-Stunden-Woche, gestaffelt nach "
                r"dem jeweiligen Beschäftigungsjahr, bei ([\d.]+) € "
                r"\(1\. Jahr\), ([\d.]+) € \(2\. Jahr\) und ([\d.]+) "
                r"€ \(3\. Jahr\)\. Hinzu kommt eine monatliche Zulage "
                r"i\.H\.v\. (\d+) € sowie eine Beteiligung am "
                r"variablen Bonus zu (\d+)%\."
            )
            match = re.fullmatch(pattern, clause)
            if not match:
                raise AdapterError("Unsupported doctoral remuneration", "parse")
            hours, first, second, third, allowance, bonus = match.groups()
            pay.append(
                "Jahr1/2/3: "
                + "/".join((first, second, third))
                + "€ brutto/Monat+"
                + allowance
                + "€ Zulage; "
                + bonus
                + "% variabler Bonus; "
                + hours
                + "h/W"
            )
        elif clause == (
            "Seminare zu wissenschaftlichem Arbeiten und "
            "überfachlicher Qualifizierung"
        ):
            pay.append("Wissenschafts-/Qualifizierungsseminare")
        elif clause.startswith(
            "Teilnahme am Doktorandenkolleg zum wissenschaftlichen Austausch"
        ):
            pay.append("Doktorandenkolleg")
        elif clause == "Tarifliche Urlaubstage + 24.12. und 31.12. frei":
            pay.append("Tarifurlaub (Zahl offen)+24./31.12. frei")
        elif clause == "Bildungs- und Beratungsangebote":
            pay.append("Bildungs-/Beratungsangebote")
        elif clause == "Flexible Arbeitszeiten":
            pay.append("flexible Arbeitszeiten")
        elif clause == "Mobiles Arbeiten":
            pay.append("mobiles Arbeiten")
        elif clause == (
            "Individuelle Sonderzahlung: Zusätzlich zum "
            "Fixgehalt bieten wir eine variable Sonderzahlung, "
            "die sich an Ihrer regelmäßigen Leistungsbeurteilung "
            "orientiert"
        ):
            pay.append("leistungsabhängige variable Sonderzahlung")
        elif clause == (
            "30 Urlaubstage + 24.12. und 31.12. frei zzgl. 6 "
            "Tage Tarifliche Freistellungszeit (Anspruch ab zwei "
            "Jahren Konzernzugehörigkeit)"
        ):
            pay.append(
                (
                    "30 Urlaubstage+24./31.12. frei; 6 Freistelltage "
                    "ab 2 Konzernjahren"
                )
            )
        elif clause == "Dienstwagen inkl. Nutzung für private Zwecke":
            pay.append("Dienstwagen privat nutzbar")
        elif clause == (
            "Beteiligung am Zeit-Wertpapier mit dem Anspruch auf "
            "bezahlte Freistellung unmittelbar vor dem Übergang "
            "in die gesetzliche Altersrente"
        ):
            pay.append("Zeit-Wertpapier: bezahlt frei vor Altersrente")
        elif clause == "Erstklassige Gesundheitsangebote on the Job":
            pay.append("Gesundheitsangebote bei der Arbeit")
        elif clause.startswith("Das Einstiegsmonatsbruttoentgelt kann"):
            match = re.fullmatch(
                (
                    r"Das Einstiegsmonatsbruttoentgelt kann für diese "
                    r"Stelle bei einer (\d+)[-‑]Stunden[-‑]Woche "
                    r"([\d.,]+) € betragen\."
                ),
                clause,
            )
            if not match:
                raise AdapterError(
                    "Unsupported entry-level remuneration", "parse"
                )
            pay.append(
                "Einstieg kann "
                + match[2]
                + "€ brutto/Monat bei "
                + match[1]
                + "h/W betragen"
            )
        elif clause == "Betriebsrente":
            pay.append("Betriebsrente")
        elif clause == "Sonderkonditionen bei Kauf und Leasing von Fahrzeugen":
            pay.append("Fahrzeugkauf/-leasing: Sonderkond.")
        elif (
            clause
            == "FahrradleasingÜbernahmemöglichkeit nach erfolgreichem Abschluss"
        ):
            pay.append("Radleasing; Übernahme bei Abschluss möglich")
        elif clause == "Fahrradleasing":
            pay.append("Radleasing")
        elif clause == "Übernahmemöglichkeit nach erfolgreichem Abschluss":
            pay.append("Übernahme bei Abschluss möglich")
        elif clause.startswith(
            (
                "Haben wir Ihr Interesse",
                "Weitere Informationen",
                "Wir freuen uns",
                "Kontakt für",
            )
        ):
            continue
        else:
            raise AdapterError("Unreviewed native benefit clause", "parse")
    if pay:
        facts.append("; ".join(pay))
    internship_or_thesis = detail["level"] == "Praktikanten" or (
        detail["level"] == "Studierende"
        and re.search(r"Abschlussarbeit|Masterarbeit", detail["title"])
    )
    if internship_or_thesis:
        minimum_wage = (
            "Der Bruttostundenlohn für Praktika (auch Pflichtpraktika) und "
            "Abschlussarbeiten entspricht der Höhe des aktuellen Mindestlohns"
        )
        if minimum_wage in text:
            facts.append("Praktikum/Abschlussarbeit: aktueller Mindestlohn")
        elif re.search(
            r"Bruttostundenlohn für Praktika.*Abschlussarbeiten", text
        ):
            raise AdapterError("Unsupported placement pay clause", "parse")
    elif detail["level"] == "Studierende":
        wages = re.findall(
            r"Bruttostundenlohn für Werkstudierende beträgt aktuell "
            r"([\d.,]+) €",
            text,
        )
        if wages:
            if len(set(wages)) != 1:
                raise AdapterError("Conflicting student pay", "parse")
            facts.append(wages[0] + "€ brutto/h")
        elif "Bruttostundenlohn für Werkstudierende" in text:
            raise AdapterError("Unsupported student pay clause", "parse")
    duration = re.findall(r"mindestens (zehn|drei|\d+) (Wochen|Monate)", text)
    for count, unit in sorted(set(duration)):
        facts.append(
            "mindestens "
            + {"zehn": "10", "drei": "3"}.get(count, count)
            + " "
            + unit
        )
    # The existing evidence array retains every extracted objective clause.
    # The overview selects semantic groups consistently, never slices text.
    overview = [facts[0]]
    for item in facts[1:]:
        if re.search(
            (
                r"Studienleistung|(?:DE|EN)\b.*[ABC][12]|"
                r"(?:Sprachniveau|Mindestalter|Führerschein|"
                r"Startalter|Grundstudium|Bachelorstudierende)|"
                r"Titeljahr|Start:|Bewerbung ab|mindestens \d+ "
                r"(?:Wochen|Monate)"
            ),
            item,
        ):
            overview.append(item)
    overview.extend(pay)
    overview.extend(
        item
        for item in facts
        if re.fullmatch(
            (
                r"[\d.,]+€ brutto/h|Praktikum/Abschlussarbeit: "
                r"aktueller Mindestlohn"
            ),
            item,
        )
    )
    overview.append(
        "Weitere Fach-/Dokumentanforderungen: offizielle Ausschreibung prüfen"
    )
    summary = "; ".join(overview) + "."
    if len(summary.encode("utf-16-le")) // 2 > 600:
        raise AdapterError(
            "Native factual overview exceeds supported summary length", "parse"
        )
    return summary, window, requirements


def nonspecific_personality(text):
    """Omit exact generic traits while retaining mixed technical clauses."""
    return text in {
        "Ausgeprägte Team- und Kommunikationsfähigkeit",
        "Selbstständige, strukturierte und zuverlässige Arbeitsweise",
        "Selbstständige, strukturierte und engagierte Arbeitsweise",
        "Selbstständige und strukturierte Arbeitsweise",
        "Selbständige und strukturierte Arbeitsweise",
        "Teamfähigkeit und eine ausgeprägte Kommunikationsfähigkeit",
        "Ausgeprägte Kommunikations- und Teamfähigkeit",
        "Hohe Eigeninitiative sowie wissenschaftliche Neugier",
        "Ausgeprägte analytische und konzeptionelle Denkweise",
        "Stark ausgeprägter Sinn für Kreativität",
        "Klaren Denkvermögen und zielgerichtetem Arbeiten",
        "Einsatzbereitschaft sowie eine selbständige Arbeitsweise",
    }


INPUTS = {
    "input-193": {
        (
            "url"
        ): "https://jobs.volkswagen-group.com/content/Nutzungsbedingungen/?locale=de_DE",
        "format": "html",
        (
            "sha256"
        ): "257d17b39bbddfb809d03015552a054c22a979f585f593ef52b91a5c1e58eafa",
        "role": "legal terms",
    },
    "input-194": {
        "url": "https://jobs.volkswagen-group.com/robots.txt",
        "format": "robots",
        (
            "sha256"
        ): "91669efc2767d124199e1d848655af18d64ac71e61ae1227be7b6aa866f03caf",
        "role": "fresh origin robots/policy",
    },
    "input-195": {
        "url": "https://www.hrd-portal.com/robots.txt",
        "format": "robots",
        (
            "sha256"
        ): "58c109cc44da82a7ff610aaaa11a3801cb99eaa3cd98f3e05183607801af6608",
        "role": "fresh origin robots/policy",
    },
    "input-196": {
        "url": "https://www.volkswagen-karriere.de/de.html",
        "format": "html",
        (
            "sha256"
        ): "f3e7a788aa530d33a4427a0ac1a0254a056ef9e8209139b76b386547e0b94e25",
        "role": "official homepage",
    },
    "input-197": {
        "url": "https://www.volkswagen-karriere.de/de/nutzungsbedingungen.html",
        "format": "html",
        (
            "sha256"
        ): "2c333250fb0f843e9ee1b1c414a8fff4e5a7bc69f804ce30007cf90e6b950185",
        "role": "legal terms",
    },
    "input-198": {
        "url": "https://www.volkswagen-karriere.de/en.html",
        "format": "html",
        (
            "sha256"
        ): "1a6f26069f0dfad07cdb9caabd2afa781b5867e48193369bc7fec10b59752eb7",
        "role": "official homepage",
    },
    "input-199": {
        "url": "https://www.volkswagen-karriere.de/robots.txt",
        "format": "robots",
        (
            "sha256"
        ): "dd84e3470dafcf77ca25f1d3d448c84dfd4255988f006a0318437961d06368fb",
        "role": "fresh origin robots/policy",
    },
    "german-listing": {
        (
            "url"
        ): "https://jobs.volkswagen-group.com/Volkswagen/search/?searchby=location&createNewAlert=false&q=&locationsearch=&geolocation=&optionsFacetsDD_city=&optionsFacetsDD_facility=Volkswagen+AG&optionsFacetsDD_department=&optionsFacetsDD_customfield2=&optionsFacetsDD_shifttype=&optionsFacetsDD_customfield3=&locale=de_DE",
        "format": "listing",
        (
            "sha256"
        ): "5653fda3ee4b50ad5d3ad8711ef3022e235ba78a98b4f0e6589cde7e226a0902",
        "role": "official German discovery seed",
    },
    "english-listing": {
        (
            "url"
        ): "https://jobs.volkswagen-group.com/Volkswagen/search/?searchby=location&createNewAlert=false&q=&locationsearch=&geolocation=&optionsFacetsDD_location=&optionsFacetsDD_facility=Volkswagen+AG&optionsFacetsDD_department=&optionsFacetsDD_customfield2=&optionsFacetsDD_shifttype=&optionsFacetsDD_customfield3=&locale=en_US",
        "format": "listing",
        (
            "sha256"
        ): "83d7d93cafb07a58ff34d248ffe7deee8915dd0205ceb6bdcde34495255c3f41",
        "role": "official English alias discovery seed",
    },
}

_FACT_CONTRACTIONS = (
    (
        "Für eine zügige und optimale Bearbeitung fordern wir vollständige "
        "Bewerbungsunterlagen:",
        "",
    ),
    (
        "Aktueller vollständiger Lebenslauf, der unter anderem die derzeitige "
        "Tätigkeit verdeutlicht",
        "aktueller vollständiger CV",
    ),
    (
        (
            "Alle Seiten des letzten Zeugnisses sowie alle "
            "erreichten Schulabschlüsse (bei ausländischen "
            "Zeugnissen beglaubigte deutsche Übersetzung)"
        ),
        (
            "letztes Zeugnis vollständig+alle Schulabschlüsse; "
            "ausländisch: beglaubigte DE-Übersetzung"
        ),
    ),
    (
        "Auswahlverfahren, Ausbildung und Prüfungen finden auf Deutsch statt",
        "Auswahl/Ausbildung/Prüfung auf DE",
    ),
    (
        "Abitur mit einem Durchschnitt von mindestens ",
        "Abitur-Schnitt mindestens ",
    ),
    (
        "Gute Noten in studiennahen Leistungskursen",
        "gute fachnahe Leistungskursnoten",
    ),
    (
        (
            "Abgeschlossene Schulausbildung, min. ein "
            "Realschulabschluss wird empfohlen"
        ),
        "Schule abgeschlossen; Realschulabschluss empfohlen",
    ),
    ("Abgeschlossene Schulausbildung", "Schule abgeschlossen"),
    ("Lust, sich mit ", "Interesse: "),
    (
        " zu beschäftigen und innovative Lösungen zu entwickeln",
        "+innovative Lösungen",
    ),
    (" zu beschäftigen", ""),
    ("Interesse an den Fächern ", "Interesse: "),
    ("Besonders Ihr ", ""),
    ("Besonders Ihre ", ""),
    ("Besonders ihr ", ""),
    (" zeichnen Sie aus", ""),
    ("Deutsch Sprachniveau ", "DE "),
    ("Englisch Sprachniveau ", "EN "),
    ("Deutsch und Englisch Sprachniveau ", "DE UND EN "),
    (
        "Deutsch und Englisch Sprach- und Schriftniveau ",
        "DE UND EN mündlich/schriftlich ",
    ),
    ("Sprachniveau Deutsch ", "DE "),
    ("Sprachniveau Englisch ", "EN "),
    ("Gute bis sehr gute Studienleistungen", "gut–sehr gute Studienleistungen"),
    ("Sehr gute Studienleistungen", "sehr gute Studienleistungen"),
    ("Studierende ", "Studium: "),
    ("in den Bereichen ", ""),
    ("im Bereich ", ""),
    ("Fachrichtung ", ""),
    ("vergleichbare Studienrichtungen", "vergleichbare Fächer"),
    ("vergleichbaren Studienrichtungen", "vergleichbare Fächer"),
    ("vergleichbarer Studienrichtungen", "vergleichbare Fächer"),
    ("erfolgreich absolviertem Grundstudium", "abgeschlossenem Grundstudium"),
    ("Credit Points", "CP"),
    ("Gute Kenntnisse in der ", "gute Kenntnisse: "),
    ("Gute Kenntnisse im ", "gute Kenntnisse: "),
    ("Gute Kenntnisse ", "gute Kenntnisse "),
    ("Grundkenntnisse im Bereich ", "Grundlagen: "),
    ("Grundkenntnisse in der ", "Grundlagen: "),
    ("Grundkenntnisse in ", "Grundlagen: "),
    ("Grundlagen in ", "Grundlagen: "),
    ("Kenntnisse im Umgang mit ", "Kenntnisse: "),
    ("Kenntnisse im Bereich ", "Kenntnisse: "),
    ("Kenntnisse über die Durchführung von ", "Kenntnisse: "),
    ("Kenntnisse in ", "Kenntnisse: "),
    ("Sicherer Umgang mit den gängigen ", "sicher: "),
    ("Sicherer Umgang mit ", "sicher: "),
    ("Erfahrung in ", "Erfahrung: "),
    ("Erfahrungen in ", "Erfahrungen: "),
    ("Ausgeprägte ", "starke "),
    ("Kommunikationsfähigkeit", "Kommunikation"),
    ("Problemlösungskompetenz", "Problemlösen"),
    ("Kooperationsbereitschaft", "Kooperation"),
    ("Organisationsstärke", "Organisation"),
    ("Teamfähigkeit", "Teamarbeit"),
    (
        "Selbstständige, strukturierte und zuverlässige Arbeitsweise",
        "selbstständig, strukturiert UND zuverlässig",
    ),
    ("sorgfältige Arbeitsweise", "sorgfältig"),
    ("modernen Technologien in der Automobilindustrie", "moderner Autotechnik"),
    ("logisches und analytisches Denken", "logisch UND analytisch"),
    (
        "Spaß an innovativen Problemlösungsstrategien",
        "innovatives Problemlösen",
    ),
    (
        "tiefes Verständnis für technische Zusammenhänge",
        "tiefes technisches Verständnis",
    ),
    ("MS-Office-Anwendungen", "MS Office"),
    (
        "Erstellung und Bearbeitung von Medieninhalten (Bilder, Logos, Videos, "
        "usw.)",
        "Medienerstellung/-bearbeitung (Bild/Logo/Video)",
    ),
    ("Gestaltung und Pflege von Webseiten", "Webgestaltung/-pflege"),
    ("Arbeitsweise", "Arbeit"),
    ("Aktuelle Immatrikulationsbescheinigung", "aktuelle Immatrikulation"),
    ("Aktueller Notenspiegel", "aktueller Notenspiegel"),
    (
        (
            "Bei einem Pflichtpraktikum zusätzlich eine "
            "Bescheinigung der Hochschule"
        ),
        "Pflichtpraktikum: Hochschulnachweis",
    ),
    (
        "Arbeitserlaubnis für Nicht-EU Bürgerinnen / Bürger",
        "Nicht-EU: Arbeitserlaubnis",
    ),
    ("Anschreiben und Lebenslauf", "Anschreiben+CV"),
    ("Studierende im Bachelorstudium", "Bachelor"),
    ("Studium: im Bachelorstudium", "Bachelor"),
    (
        "Masterstudierende mit guten bis sehr guten Studienleistungen",
        "Master; Studienleistung gut–sehr gut:",
    ),
    (
        "Studium: mit guten bis sehr guten Studienleistungen",
        "Studium; Studienleistung gut–sehr gut:",
    ),
    (
        (
            "Gute Programmierkenntnisse, vorzugsweise in Python "
            "(oder einer vergleichbaren Programmiersprache) sowie "
            "Erfahrung mit GenAI, LLMs und AI-gestützten "
            "Engineering-Workflows"
        ),
        (
            "gut programmieren; Python oder vergl. Sprache "
            "bevorzugt; Erfahrung GenAI, LLMs UND AI-Engineering"
        ),
    ),
    (
        (
            "Erste Kenntnisse: Simulations- und "
            "Engineering-Werkzeugen (z. B. MATLAB/Simulink, LTspice, "
            "PCB-Entwicklungsumgebungen oder vergleichbare Software) "
            "sind von Vorteil"
        ),
        "erste Simulations-/Engineering-Kenntnisse (MATLAB/Simulink, LTspice, "
        "PCB-Tools oder vergl.) vorteilhaft",
    ),
    (
        (
            "Erste Kenntnisse oder Erfahrungen CFD-Berechnung und "
            "1-D-Berechnung sind wünschenswert"
        ),
        "CFD- UND 1-D-Kenntnisse/Erfahrung erwünscht",
    ),
    (
        "Gutes Verständnis für komplexe technische Zusammenhänge",
        "gutes komplexes Technikverständnis",
    ),
    (
        (
            "Kreativität sowie Interesse an der Entwicklung neuer "
            "Ansätze und Methoden"
        ),
        "Kreativität UND neue Methoden",
    ),
    (
        "Selbstständige, strukturierte und engagierte Arbeit",
        "selbstständig, strukturiert UND engagiert",
    ),
    (
        (
            "Versiert im Skizzieren von Konzepten, der Illustration "
            "mit Photoshop und dem Erstellen von 3D-Modellen"
        ),
        "versiert: Konzeptskizzen, Photoshop-Illustration UND 3D-Modellierung",
    ),
    ("Stark ausgeprägter Sinn für Kreativität", "stark kreativ"),
    (
        "Klaren Denkvermögen und zielgerichtetem Arbeiten",
        "klar denken UND zielgerichtet arbeiten",
    ),
    ("Deutsch und EN ", "DE UND EN "),
    ("Englisch und DE ", "EN UND DE "),
    ("Gute bis sehr gute ", "gut–sehr gute "),
    ("mit guten bis sehr guten ", "mit gut–sehr guten "),
    ("mit sehr guten ", "mit sehr guten "),
    ("Grundkenntnisse mit ", "Grundlagen: "),
    ("sind wünschenswert", "erwünscht"),
    ("sind von Vorteil", "vorteilhaft"),
    ("ist wünschenswert", "erwünscht"),
    ("ist von Vorteil", "vorteilhaft"),
    ("idealerweise", "bevorzugt"),
    ("vorzugsweise", "bevorzugt"),
    ("Studienrichtungen", "Fächer"),
    ("Studiengänge", "Fächer"),
    ("Studienrichtung", "Fach"),
    ("oder einer vergleichbaren ", "oder vergl. "),
    ("oder vergleichbare ", "oder vergl. "),
    ("Erste Kenntnisse ", "erste Kenntnisse "),
    (
        "in der Programmierung und Datenanalyse",
        "Programmierung UND Datenanalyse",
    ),
    ("Begeisterung für Themen rund um Mobilität", "Mobilitätsinteresse"),
    (
        (
            "Relevante Erfahrungen in der Beratung, Automobilbranche "
            "oder Industrie"
        ),
        "relevante Beratungs-, Auto- oder Industrieerfahrung",
    ),
    (
        (
            "Erfolgreiches bestandenes 1. Staatsexamen, Verständnis "
            "für wirtschaftliche und technische Zusammenhänge"
        ),
        (
            "1. Staatsexamen bestanden; wirtschaftliches UND "
            "technisches Verständnis"
        ),
    ),
    (
        "Freude an juristisch anspruchsvoller Tätigkeit, ",
        "Interesse an anspruchsvoller juristischer Arbeit; ",
    ),
    (
        "Einsatzbereitschaft sowie eine selbständige Arbeit",
        "einsatzbereit UND selbstständig",
    ),
    (
        (
            "Begeisterung, als Jurist/in in einem internationalen "
            "Unternehmen tätig zu sein"
        ),
        "Interesse an internationaler juristischer Tätigkeit",
    ),
    (
        (
            "logistischen Prozessen in der Automobilindustrie und "
            "modernen Technologien"
        ),
        "Automobillogistik UND moderner Technik",
    ),
    (
        "technischen Zeichnungen sowie modernen Technologien",
        "Technikzeichnen UND moderner Technik",
    ),
    (
        "technischen Zeichnungen und modernen Technologien",
        "Technikzeichnen UND moderner Technik",
    ),
    (
        "Programmiersprachen, Coding und IT-Sicherheit",
        "Programmiersprachen, Coding UND IT-Sicherheit",
    ),
    (
        "betriebswirtschaftlichen Zusammenhängen in der Automobilindustrie",
        "Automobilwirtschaft",
    ),
    ("aktueller vollständiger CV", "CV aktuell/vollständig"),
    (
        "letztes Zeugnis vollständig+alle Schulabschlüsse",
        "letztes Zeugnis alle Seiten+alle Schulabschlüsse",
    ),
    ("Auswahl/Ausbildung/Prüfung auf DE", "Auswahl/Ausbildung/Prüfung DE"),
    (
        "Mindestalter 16 ½ bei Ausbildungsstart und Führerschein Klasse B "
        "spätestens 18 Monate nach Ausbildungsbeginn",
        "Startalter≥16½; Führerschein B spätestens 18 Monate nach Start",
    ),
    (
        "Lust sich mit Rettungstechniken sowie Maßnahmen zur Brandbekämpfung",
        "Interesse: Rettung UND Brandbekämpfung",
    ),
    (
        (
            "Sicherheitstechnik und Wartung von Hard- und "
            "Softwarekomponenten sowie IT-Systemen"
        ),
        "Sicherheitstechnik UND Hard-/Software-/IT-Wartung",
    ),
    (
        "Software im Bereich IT-Sicherheit sowie Netzwerk- und "
        "Kommunikationstechnik",
        "IT-Sicherheitssoftware, Netzwerk-/Kommunikationstechnik",
    ),
    (
        "Arbeits- und Aufenthaltserlaubnis für Nicht-EU-Bürgerinnen / -Bürger",
        "Nicht-EU: Arbeits-/Aufenthaltserlaubnis",
    ),
    (
        "Arbeits- und Aufenthaltserlaubnis für Nicht-EU Bürgerinnen / Bürger",
        "Nicht-EU: Arbeits-/Aufenthaltserlaubnis",
    ),
    (
        "Ggf. Arbeits- und Aufenthaltserlaubnis inkl. Zusatzblatt für Nicht-EU "
        "Bürgerinnen / Bürger",
        "ggf. Nicht-EU: Arbeits-/Aufenthaltserlaubnis+Zusatzblatt",
    ),
    (
        "Ggf. Arbeits- und Aufenthaltserlaubnis inkl. Zusatzblatt für "
        "Nicht-EU-Bürgerinnen / -Bürger",
        "ggf. Nicht-EU: Arbeits-/Aufenthaltserlaubnis+Zusatzblatt",
    ),
    (
        "Ggf. Arbeits- und Aufenthaltserlaubnis inkl. Zusatzblatt für "
        "Nicht-EU-Bürgerinnen/-Bürger",
        "ggf. Nicht-EU: Arbeits-/Aufenthaltserlaubnis+Zusatzblatt",
    ),
    (
        "Gute Kommunikations- und Teamarbeit",
        "gute Kommunikation UND Teamarbeit",
    ),
    ("sicher: den MS Office", "sicher: MS Office"),
    ("Idealerweise", "bevorzugt"),
    ("oder vergleichbarer", "oder vergl."),
    (
        "Gut bis sehr gut abgeschlossenes Studium mit Promotionsberechtigung",
        "Promotionsberechtigter Abschluss gut–sehr gut",
    ),
    ("Künstliche Intelligenz", "KI"),
    ("Generativer Künstlicher Intelligenz", "GenAI"),
    (
        "Multi-Agenten-Systemen oder verwandten Technologien",
        "Multi-Agenten-Systeme oder Verwandtes",
    ),
    ("Wirtschaftsingenieurwissenschaften", "Wirtschaftsingenieurwiss."),
    (
        "Softwareentwicklung oder der Entwicklung datenbasierter Systeme",
        "Software- oder datenbasierte Systementwicklung",
    ),
    (
        "Interesse an wissenschaftlicher Forschung und der Anwendung moderner "
        "KI-Verfahren auf reale Problemstellungen",
        "Forschungsinteresse UND reale KI-Anwendung",
    ),
    (
        "Aktueller Notenspiegel / Master- oder Diplomzeugnis",
        "Notenspiegel / Master- oder Diplomzeugnis",
    ),
    (
        "Ausgeprägte analytische und konzeptionelle Denkweise",
        "stark analytisch UND konzeptionell",
    ),
    ("Vorlage eines aktuellen Führungszeugnisses", "aktuelles Führungszeugnis"),
    ("gut–sehr gute Studienleistungen", "Studienleistungen gut–sehr gut"),
    ("sehr gute Studienleistungen", "Studienleistungen sehr gut"),
    (
        "Abgeschlossene Schulausbildung, mind. ein Realschulabschluss wird "
        "empfohlen",
        "Schule abgeschlossen; Realschulabschluss empfohlen",
    ),
    (
        "Schule abgeschlossen, mind. ein Realschulabschluss wird empfohlen",
        "Schule abgeschlossen; Realschulabschluss empfohlen",
    ),
    (
        (
            "Kreativität, gestalterisches Verständnis sowie ein "
            "ausgeprägtes Gespür für Farben und Formen"
        ),
        "kreativ; starkes Gestaltungs-/Farb-/Formverständnis",
    ),
    (
        "Interesse an Gestaltung, Medien, digitalen Technologien und kreativen "
        "Prozessen",
        "Interesse: Gestaltung, Medien, digitale Technik UND kreative Prozesse",
    ),
    (
        (
            "Bitte fügen Sie Ihrer Bewerbung 5 bis 10 Arbeitsproben "
            "als eine zusammengefasste PDF-Datei bei. Die "
            "Arbeitsproben sollen Ihre fachlichen Fähigkeiten und "
            "Ihre Arbeitsweise veranschaulichen und jeweils mit "
            "einer kurzen Beschreibung (Thema, Zielsetzung und Ihr "
            "Beitrag) versehen sein. Bei Teamprojekten bitten wir "
            "zusätzlich um eine kurze Erläuterung Ihrer Rolle im "
            "Projekt."
        ),
        (
            "5–10 Arbeitsproben in 1 PDF; je Thema/Ziel/eigener "
            "Beitrag; Team: eigene Rolle erklären"
        ),
    ),
    ("Deutsch oder EN ", "DE ODER EN "),
    ("Sutidenleistungen", "Studienleistungen"),
    (
        (
            "tiefes technisches Verständnis, -Dokumentationen und - "
            "Programmen zeichnet Sie aus"
        ),
        (
            "tiefes Verständnis: technische Zusammenhänge, "
            "Dokumentationen UND Programme"
        ),
    ),
    (
        (
            "Bitte fügen Sie Ihrer Bewerbung 5 bis 10 Arbeitsproben "
            "als eine zusammengefasste PDF-Datei bei. Die "
            "Arbeitsproben sollen Ihre fachlichen Fähigkeiten und "
            "Ihre Arbeit veranschaulichen und jeweils mit einer "
            "kurzen Beschreibung (Thema, Zielsetzung und Ihr "
            "Beitrag) versehen sein. Bei Teamprojekten bitten wir "
            "zusätzlich um eine kurze Erläuterung Ihrer Rolle im "
            "Projekt."
        ),
        (
            "5–10 Arbeitsproben in 1 PDF; je Thema/Ziel/eigener "
            "Beitrag; Team: eigene Rolle erklären"
        ),
    ),
)


if __name__ == "__main__":
    raise SystemExit(main())
