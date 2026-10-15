"""Discover IBM early-career programmes and extract current official facts."""

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

SOURCE_ID = "ibm"
SOURCE_URL = "https://www.ibm.com/careers"
WEBSITE_URL = "https://www.ibm.com/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "US"
PUBLISHER_TYPE = "company"
ATTRIBUTION = "IBM https://www.ibm.com/ ; original factual programme summaries and direct official links"

_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {
    "scholarships",
    "grants",
    "internships",
    "jobs",
    "training",
    "fellowships",
    "competitions",
    "volunteering",
}
FAMILY = "youthopps-ibm-careers-publisher-v1"
FAMILY_SOURCES = {"ibm"}
COLLECTION_TIMEOUT = 900
RUN_TIMEOUT = 1290
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "ibm-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_INITIALIZING = False
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_PUBLISHER_STARTS = _PUBLISHER_BYTES = 0
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 6
_PUBLISHER_GATE_FAILED = False
PUBLISHER_START_LIMIT = 100
PUBLISHER_BYTE_LIMIT = 64 * 1024 * 1024
RESPONSE_BYTE_LIMIT = 8 * 1024 * 1024
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
        and (class_name is None or class_name in n.attrs.get("class", "").split())
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
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
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
        raise AdapterError("Publisher state directory identity changed", "access")


def secure_file(name, flags):
    verify_state_directory()
    descriptor = os.open(name, flags | os.O_NOFOLLOW, 0o600, dir_fd=_STATE_DIR_FD)
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


def locked_budget():
    budget_file()
    descriptor = secure_file(
        "state.lock", os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0)
    )
    file = os.fdopen(descriptor, "a+")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except BaseException:
        file.close()
        raise
    return file


def load_budget():
    try:
        descriptor = secure_file("state.json", os.O_RDONLY)
    except FileNotFoundError:
        if not _STATE_INITIALIZING:
            raise AdapterError("Mature publisher state is missing", "access") from None
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


def pace():
    ("Serialize every publisher request start across the complete " "origin set.")
    global _PUBLISHER_STARTS, _PUBLISHER_INTERVAL
    check_deadline()
    if _PUBLISHER_STARTS >= PUBLISHER_START_LIMIT:
        raise AdapterError("Publisher physical-start bound exhausted", "access")
    with locked_budget():
        state = load_budget()
        if state["blocked"]:
            raise AdapterError(
                ("Publisher backoff requires reviewed recovery"), ("access")
            )
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state.get("interval", 6))
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
            target = max(target, starts[-1] + _PUBLISHER_INTERVAL)
        if len(starts) == 10:
            target = max(target, starts[0] + 60)
        if target + 120 > time.time() + (_PHASE_END - time.monotonic()):
            raise AdapterError("Publisher backoff exceeds phase budget", "access")
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
                raise AdapterError("Publisher backoff exceeds phase budget", "access")
            if _COLLECTION_STARTED is not None and (
                time.time() + wait + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Publisher backoff exceeds collection budget", "access"
                )
            time.sleep(wait)
        now = time.time()
        if now < target:
            raise AdapterError(("Publisher pacing clock moved backwards"), ("access"))
        check_deadline()
        if (
            _PUBLISHER_COMPLETED_MONOTONIC is not None
            and time.monotonic() < _PUBLISHER_COMPLETED_MONOTONIC + _PUBLISHER_INTERVAL
        ):
            raise AdapterError(
                "Publisher physical completion gate not elapsed", "access"
            )
        state["starts"] = [t for t in starts if t > now - 60] + [now]
        state["observed_at"] = now
        state["not_before"] = max(state["not_before"], now + _PUBLISHER_INTERVAL)
        save_budget(state)
        _PUBLISHER_STARTS += 1


@contextlib.contextmanager
def publisher_attempt(publisher):
    """Persist a completion gate around every physical publisher attempt."""
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
    if not publisher:
        yield
        return
    if _COLLECTION_LOCK_FD is None or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Exclusive healthy publisher collector required", "access")
    check_deadline()
    if (
        _PUBLISHER_COMPLETED_MONOTONIC is not None
        and time.monotonic() < _PUBLISHER_COMPLETED_MONOTONIC + _PUBLISHER_INTERVAL
    ):
        raise AdapterError("Publisher physical completion gate not elapsed", "access")
    try:
        yield
    finally:
        _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
        _PUBLISHER_GATE_FAILED = True
        with locked_budget():
            state = load_budget()
            now = time.time()
            state["not_before"] = max(
                state["not_before"],
                now + max(_PUBLISHER_INTERVAL, state.get("interval", 6)),
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
    byte_limit=30000000,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    global _PUBLISHER_BYTES
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            remaining = PUBLISHER_BYTE_LIMIT - _PUBLISHER_BYTES
            if remaining <= 0:
                raise AdapterError("Publisher aggregate-byte bound exhausted", "fetch")
            byte_limit = min(byte_limit, RESPONSE_BYTE_LIMIT, remaining)
            public_url(current)
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        with publisher_attempt(publisher):
            try:
                response = _OPENER.open(request, timeout=request_timeout(publisher))
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
                        f"Publisher refused access: HTTP {status}: " + provenance,
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
                    if publisher:
                        _PUBLISHER_BYTES += len(body)
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
                    raise AdapterError(("Unsafe authenticated redirect"), ("publish"))
                if new.netloc != old.netloc:
                    headers = {
                        key: value
                        for key, value in headers.items()
                        if key.lower() != "authorization"
                    }
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    raise AdapterError(("Unexpected GitHub API redirect"), ("publish"))
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
    global _PUBLISHER_INTERVAL
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
    if parser is not None and not robots_directive(parser, "can_fetch", url):
        raise AdapterError("Publisher robots excludes required input", "access")
    if parser is not None:
        delay = robots_directive(parser, "crawl_delay")
        if delay and delay > 6:
            _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, delay)
            with locked_budget():
                state = load_budget()
                if state["starts"]:
                    state["interval"] = max(state.get("interval", 6), delay)
                    state[("not_before")] = max(
                        state[("not_before")], state[("starts")][-1] + delay
                    )
                    save_budget(state)
        rate = robots_directive(parser, "request_rate")
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
            f"Required publisher input returned HTTP {status}: " + provenance,
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
        raise AdapterError(("Workflow pacing state unavailable"), ("access")) from None


def current_run_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "") for key in (("GITHUB_RUN_ID"), ("GITHUB_RUN_ATTEMPT"))
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
            or run[("head_repository")][("full_name")] != ("YouthOpps/data-pipeline")
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
        raise AdapterError(("Untrusted completed family attempt"), ("access")) from None


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
                        (f"/actions/runs/{identity}/attempts/" f"{current_attempt - 1}")
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
                raise AdapterError(("Workflow inventory count mismatch"), ("access"))
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(("Ambiguous family attempt chronology"), ("access"))
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def download_inert_artifact(identity, artifact_id, digest=None):
    ("Download a bounded authenticated artifact, stripping " "cross-origin auth.")
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
        raw = os.environ.get("IBM_PACING_BOOTSTRAP", "")
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
            incoming[("not_before")] = max(time.time() + 60, bootstrap[("not_before")])
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
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined[("not_before")] = max(
            local[("not_before")], incoming[("not_before")], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined["interval"] = max(
            local.get("interval", 6), incoming.get("interval", 6)
        )
        combined[("starts")] = sorted(set(local[("starts")] + incoming[("starts")]))[
            -10:
        ]
        save_budget(combined)
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
        raise AdapterError("Step output outside runner temporary root", "access")
    descriptors = []
    parent = runner_parent
    try:
        for name in parts[:-1]:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
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
    path = os.environ.get("IBM_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Explicit runner temporary directory required", "access")
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
            raise AdapterError("Unsafe pacing artifact directory owner", "access")
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
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise AdapterError("Pacing artifact identity changed", "access")
                # Invalidate before unlink: an unlink failure cannot leave a
                # valid stale envelope on a writable, owned artifact inode.
                os.ftruncate(existing, 0)
                os.fsync(existing)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            finally:
                os.close(existing)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError("Publisher completion gate cannot be exported", "access")
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
                    or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
                ):
                    raise AdapterError("Unsafe finalized artifact identity", "access")
                raw = os.read(check, 16385)
                if len(raw) > 16384 or json.loads(raw) != envelope:
                    raise AdapterError("Finalized pacing artifact mismatch", "access")
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


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _PUBLISHER_STARTS, _PUBLISHER_BYTES
    _PUBLISHER_STARTS = _PUBLISHER_BYTES = 0
    _COLLECTION_STARTED = time.time()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        descriptor = secure_file(
            "collection.lock", os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0)
        )
        try:
            info = os.fstat(descriptor)
            if (
                info.st_uid != os.getuid()
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                raise OSError("Unsafe collection lock")
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


def validate_records(records, *, allow_empty=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError("Empty or invalid opportunity collection", "validate")
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
            if not isinstance(record.get(field), str) or not record[field].strip():
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
                        datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo
                        is None
                    ):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError("Invalid record timestamp: " + field, "validate")
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
        if "source_documents" in record:
            documents = record["source_documents"]
            if not isinstance(documents, list) or not documents:
                raise AdapterError("Missing IBM source documents", "validate")
            for document in documents:
                public_url(document["url"])
                if not re.fullmatch(r"[0-9a-f]{64}", document.get("sha256", "")):
                    raise AdapterError("Invalid IBM content digest", "validate")
            for field in (
                "eligibility",
                "funding",
                "programme_features",
                "programme_details",
                "tracks",
                "deadline_text",
            ):
                if not isinstance(record.get(field), list) or any(
                    not isinstance(value, str) for value in record[field]
                ):
                    raise AdapterError("Invalid IBM facts: " + field, "validate")
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
            or any(not isinstance(value, str) for value in classification["evidence"])
        ):
            raise AdapterError("Invalid classification evidence", "validate")

    validate_owned_records(records)


def profile_identity(profile):
    """Use the reviewed canonical programme URL, never its menu position."""
    return (
        "ibm-"
        + hashlib.sha256((SOURCE_ID + "|" + profile["url"]).encode()).hexdigest()[:24]
    )


def validate_owned_records(records):
    """Reject foreign identities and impossible owned record chronologies."""
    for record in records:
        public_url(record["url"])
        if record["id"] != profile_identity(record):
            raise AdapterError("Unowned IBM record identity or URL", "validate")
        values = {
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
            values["created_at"] <= values["updated_at"] <= values["last_checked_at"]
            and values["first_seen_at"]
            <= values["last_seen_at"]
            <= values["last_checked_at"]
        ):
            raise AdapterError("Invalid owned IBM chronology", "validate")
        if record["kind"] == "programme-overview" and (
            record["status"] != "unknown" or record["deadline"] is not None
        ):
            raise AdapterError("Unsupported IBM programme availability", "validate")


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
            payload=(json.dumps(payload).encode() if payload is not None else None),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; outcome unconfirmed", "publish"
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication HTTP {status}", "publish", status)
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
            raise AdapterError("Unsupported existing source file encoding", "publish")
        files[name] = (
            None if file is None else base64.b64decode(file["content"]).decode("utf-8")
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


def public_url(value):
    """Restrict dynamic discovery to public IBM careers HTML and legal pages."""
    parsed = urllib.parse.urlsplit(value)
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.ibm.com"
        or parsed.username
        or parsed.password
        or parsed.port
        or not (
            path == "/robots.txt"
            or path in {"/legal", "/legal/terms"}
            or path == "/careers"
            or path.startswith("/careers/")
        )
        or path == "/careers/search"
        or any(part in {"..", "."} for part in parsed.path.split("/"))
        or "%" in parsed.path
    ):
        raise AdapterError("Unreviewed public source route", "access")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def linked_url(base, href):
    """Normalize only literal public links; do not invent API routes."""
    url = urllib.parse.urljoin(base, href)
    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "www.ibm.com" or parsed.path == "/careers/search":
        return None
    if not (parsed.path == "/careers" or parsed.path.startswith("/careers/")):
        return None
    return public_url(url)


def article_body(root):
    return one(nodes(root, class_name="body-article-8"), "IBM article body")


def programme_cards(root):
    """Keep the programme section separate from advice, events and job alerts."""
    active = False
    cards = []
    for node in root.walk():
        if node.tag == "h2":
            label = node.text().lower()
            if label.startswith(("our programs", "our programmes")):
                active = True
            elif label:
                active = False
        if active and node.tag == "c4d-cta-block-item":
            cards.append(node)
    if not cards:
        raise AdapterError("IBM programme section missing or empty", "parse")
    return cards


def pagination(root, url):
    links = set()
    for node in root.walk():
        rel = (node.attrs.get("rel") or "").split()
        if "next" in rel or (
            node.tag == "a" and re.fullmatch(r"next(?: page)?", node.text(), re.I)
        ):
            target = linked_url(url, node.attrs.get("href", ""))
            if not target or target == url:
                raise AdapterError("Unresolved IBM listing pagination", "parse")
            links.add(target)
        for key in ("data-pagescount", "data-page-count"):
            count = node.attrs.get(key)
            if count and (not count.isdigit() or int(count) > 1) and not links:
                # Check again after inspecting all literal navigation links.
                links.add(None)
    if None in links:
        links.remove(None)
        if not links:
            raise AdapterError("IBM pagination has no public next link", "parse")
    return sorted(links)


def read_pages():
    """Exhaust the current programme cards, child programmes and pagination."""
    pages = {}

    def read(url):
        if url not in pages:
            _, headers, body = fetch_public(url)
            if "html" not in headers.get("Content-Type", "").lower():
                raise AdapterError("Expected official IBM HTML", "parse")
            pages[url] = document_root(body.decode("utf-8", "strict"))
        return pages[url]

    programmes = discover_programmes(read)
    return {"documents": pages, "programmes": programmes}


def discover_programmes(read):
    """Walk the programme frontier using one memoized official document reader."""
    programmes = {}
    home = read(SOURCE_URL)
    terms = read("https://www.ibm.com/legal/terms")
    if not re.search(
        r"only use a crawler to crawl.*?as permitted by.*?robots.txt",
        terms.text(),
        re.I,
    ):
        raise AdapterError("IBM crawler permission requires review", "access")
    hubs = {
        linked_url(SOURCE_URL, node.attrs["href"])
        for node in nodes(home, "a")
        if node.attrs.get("href") and re.search(r"entry[- ]level", node.text(), re.I)
    }
    hubs.discard(None)
    if not hubs:
        raise AdapterError("IBM early-career hub not discovered", "parse")
    visited = set()
    while hubs:
        url = min(hubs)
        hubs.remove(url)
        if url in visited:
            continue
        visited.add(url)
        root = fetch_inventory_page(read, url)
        for card in programme_cards(root):
            targets = {
                linked_url(url, node.attrs["href"])
                for node in nodes(card, "a")
                if node.attrs.get("href")
            }
            targets.discard(None)
            target = one(list(targets), "public programme card destination")
            programmes.setdefault(target, {target})
        hubs.update(set(pagination(root, url)) - visited)

    pending = set(programmes)
    scanned = set()
    while pending:
        url = min(pending)
        pending.remove(url)
        if url in scanned:
            continue
        scanned.add(url)
        root = read(url)
        body = article_body(root) if "/blog/" in url else root
        for node in nodes(body, "a"):
            href = node.attrs.get("href")
            if not href:
                continue
            target = linked_url(url, href)
            if not target or target == url or "/blog/" not in target:
                continue
            if not re.search(r"program(?:me)?s?\b", node.text() + " " + target, re.I):
                continue
            # This explanatory article belongs to the already published
            # internship identity, rather than a second internship offer.
            if target == INTERNSHIP_EXPLANATION:
                programmes.setdefault(INTERNSHIP_URL, {INTERNSHIP_URL}).add(target)
            else:
                programmes.setdefault(target, {target})
            if target not in scanned:
                pending.add(target)
        if url == INTERNSHIP_URL:
            # Programme links on the hub use "Read now"; the slug carries
            # the programme context without admitting application-tip articles.
            for node in nodes(root, "a"):
                target = linked_url(url, node.attrs.get("href", ""))
                if target == INTERNSHIP_EXPLANATION:
                    programmes[url].add(target)
                    pending.add(target)
    return programmes


INTERNSHIP_URL = "https://www.ibm.com/careers/internships"
INTERNSHIP_EXPLANATION = (
    "https://www.ibm.com/careers/blog/"
    "5-things-that-make-the-ibm-internship-programs-unique"
)


def content_blocks(root):
    """Retain complete article sections, excluding shared navigation."""
    body = article_body(root)
    return [
        node.text()
        for node in body.walk()
        if node.tag in {"h2", "h3", "p", "li"}
        and node.text()
        and not (node.tag == "li" and nodes(node, "p"))
    ]


def normalize_fact(text):
    return text.translate(str.maketrans({"‑": "-", "–": "-", "’": "'"}))


def hub_blocks(root):
    """Read substantive hub text while pruning navigation and testimonials."""
    blocks = []
    navigation_tail = False

    def visit(node):
        nonlocal navigation_tail
        if node.tag in {"h2", "h3"} and node.text().lower().startswith(
            ("keep exploring", "let's find your next steps")
        ):
            navigation_tail = True
        classes = node.attrs.get("class", "").lower()
        if (
            navigation_tail
            or node.tag in {"nav", "header", "footer", "script", "style"}
            or any(word in node.tag for word in ("masthead", "footer", "breadcrumb"))
            or any(word in classes for word in ("breadcrumb", "testimonial", "quote"))
            or node.attrs.get("data-component-name") == "next-steps"
        ):
            return
        if node.tag in {"h2", "h3", "p", "li"}:
            text = node.text()
            if text and not text.startswith(("“", '"', "-")):
                blocks.append(text)
            return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)

    visit(root)
    return blocks


def closing_dates(blocks):
    """Resolve closing clauses only when every advertised timing is known."""
    statements = []
    dates = set()
    unresolved = False
    trigger = re.compile(
        r"\b(?:applications? (?:deadline|close|closing)|"
        r"apply (?:by|before)|deadline for applications)\b",
        re.I,
    )
    date_pattern = re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b|"
        r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b|"
        r"\b[A-Za-z]+\s+\d{1,2},?\s+\d{4}\b"
    )
    for block in blocks:
        for sentence in re.split(r"(?<=[.!?])\s+", block):
            closings = list(trigger.finditer(sentence))
            if not closings:
                continue
            statements.append(sentence)
            for index, closing in enumerate(closings):
                end = (
                    closings[index + 1].start()
                    if index + 1 < len(closings)
                    else len(sentence)
                )
                clause = sentence[closing.end() : end]
                # Dates belonging to a project, event or programme start are
                # outside the application-closing clause, even in one sentence.
                clause = re.split(
                    r"\b(?:for\s+)?(?:projects?|events?|courses?|programmes?)\s+"
                    r"(?:start(?:ing|s)?|begin(?:ning|s)?|on)|"
                    r"\b(?:starting|beginning)\b",
                    clause,
                    maxsplit=1,
                    flags=re.I,
                )[0]
                matches = list(date_pattern.finditer(clause))
                if len(matches) != 1 or re.search(r"\d", clause[: matches[0].start()]):
                    unresolved = True
                    continue
                value = matches[0].group()
                for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
                    try:
                        dates.add(
                            datetime.strptime(value.replace(",", ""), fmt)
                            .date()
                            .isoformat()
                        )
                        break
                    except ValueError:
                        continue
                else:
                    unresolved = True
    return (
        next(iter(dates)) if len(dates) == 1 and not unresolved else None
    ), statements


def current_facts(blocks):
    """Extract eligibility and funding statements without inferred nationality."""
    sentences = list(
        dict.fromkeys(
            sentence.strip()
            for block in blocks
            for sentence in re.split(r"(?<=[.!?])\s+", normalize_fact(block))
            if sentence.strip()
        )
    )
    eligibility = [
        text
        for text in sentences
        if re.search(
            r"\b(?:eligible|eligibility|applicants? must|enrolled|"
            r"pursuing a degree|designed for|requires? less than|"
            r"recent (?:university|college|graduate)|fresh graduates|"
            r"career switcher|individuals who|citizen|nationality)\b",
            text,
            re.I,
        )
    ]
    funding = [
        text
        for text in sentences
        if re.search(
            r"\b(?:salary|salaries|stipend|paid|unpaid|earn-and-learn|"
            r"funding|scholarship|tuition|remuneration)\b",
            text,
            re.I,
        )
    ]
    features = []
    for pattern, label in (
        (r"\bmentor(?:ing|ship|s)?\b", "Mentoring"),
        (r"\bstructured onboarding\b", "Structured onboarding"),
        (r"\bcertifications?\b", "Certifications"),
        (r"\bdigital credentials\b", "Digital credentials"),
        (r"\bhands-on\b", "Hands-on work"),
        (
            r"\b(?:program(?:me)?|experience)\b[^.]*\bfull-time\b|"
            r"\bfull-time\b[^.]*\b(?:program(?:me)?|experience)\b",
            "Full-time",
        ),
        (r"\bon-site\b", "On-site"),
        (
            r"\blearning (?:and career development )?resources\b|"
            r"\blearning.*?platform",
            "Learning resources",
        ),
    ):
        if any(re.search(pattern, text, re.I) for text in sentences):
            features.append(label)
    return eligibility, funding, features


def parse_inventory(pages):
    documents, programmes = pages["documents"], pages["programmes"]
    if not programmes:
        raise AdapterError("Empty IBM programme frontier", "parse")
    records = []
    now = utc_now()
    for url, sources in sorted(programmes.items()):
        root = documents[url]
        title = one(
            [node.text() for node in nodes(root, "h1") if node.text()],
            "programme title",
        )
        blocks = []
        provenance = []
        for source in sorted(sources):
            page = documents[source]
            if "/blog/" in source:
                blocks.extend(content_blocks(page))
                body = article_body(page)
            else:
                blocks.extend(hub_blocks(page))
                body = page
            provenance.append(
                {
                    "url": source,
                    "sha256": hashlib.sha256(
                        json.dumps(
                            {
                                "text": body.text(),
                                "links": sorted(
                                    {
                                        node.attrs["href"]
                                        for node in body.walk()
                                        if node.attrs.get("href")
                                    }
                                ),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode()
                    ).hexdigest(),
                }
            )
        if url == INTERNSHIP_URL:
            # The co-op comparison explicitly covers both programmes. Keep
            # its scope in the statement rather than generalizing its duration.
            for other_url, other_root in documents.items():
                if "/blog/" not in other_url or other_url in sources:
                    continue
                comparisons = [
                    block
                    for block in content_blocks(other_root)
                    if re.search(r"Both co.ops and internships", block)
                ]
                if comparisons:
                    blocks.extend(comparisons)
                    provenance.append(
                        {
                            "url": other_url,
                            "sha256": hashlib.sha256(
                                " ".join(comparisons).encode()
                            ).hexdigest(),
                        }
                    )
        if not blocks:
            raise AdapterError(
                "Programme has no substantive article facts: " + url, "parse"
            )
        eligibility, funding, features = current_facts(blocks)
        details = list(
            dict.fromkeys(
                sentence
                for block in blocks
                for sentence in re.split(r"(?<=[.!?])\s+", normalize_fact(block))
                if re.search(
                    r"\b(?:\d+|one|two|three|four|six|twelve|sixteen)[- ]"
                    r"(?:weeks?|months?|years?|hours?)\b",
                    sentence,
                    re.I,
                )
            )
        )
        deadline, deadline_text = closing_dates(blocks)
        title_key = normalize_fact(title).lower()
        if "apprentic" in title_key:
            categories = ["training", "jobs"]
        elif "intern" in title_key or "co-op" in title_key:
            categories = ["internships"]
        elif re.search(r"entry.level|sales.*program", title_key):
            categories = ["jobs", "training"]
        elif re.search(r"program|training|academy", title_key):
            categories = ["training"]
        else:
            raise AdapterError(
                "Unclassified advertised IBM programme: " + title, "parse"
            )
        summary_parts = []
        for statement in eligibility + funding + details:
            if not statement.endswith((".", "!", "?")):
                statement += "."
            if (
                statement not in summary_parts
                and len(" ".join(summary_parts + [statement])) <= 400
            ):
                summary_parts.append(statement)
        if features:
            summary_parts.append("Programme features: " + ", ".join(features) + ".")
        suffix = "Programme overview; current vacancies are not established."
        if deadline_text:
            suffix = "Application timing is stated on the official programme page."
        summary_parts.append(suffix)
        summary = " ".join(summary_parts)
        # Never clip a qualification or funding condition to fit the summary.
        # Complete extracted statements remain in their dedicated fields.
        while len(summary.encode("utf-16-le")) // 2 > 600 and len(summary_parts) > 1:
            summary_parts.pop(-2)
            summary = " ".join(summary_parts)
        tracks = []
        in_tracks = False
        for block in blocks:
            if re.search(r"(?:specialized|specialised) tracks:", block, re.I):
                in_tracks = True
                continue
            if in_tracks:
                if " – " in block or " –" in block or " - " in block:
                    tracks.append(block)
                else:
                    in_tracks = False
        host_countries = []
        for track in tracks:
            if re.search(r"assignment in", track, re.I):
                for name, code in (("Spain", "ES"), ("Ireland", "IE")):
                    if re.search(r"\b" + name + r"\b", track):
                        host_countries.append(code)
        record = {
            "title": title,
            "url": url,
            "source": SOURCE_ID,
            "source_url": SOURCE_URL,
            "published_at": None,
            "summary": summary,
            "tags": [],
            "location": None,
            "deadline": deadline,
            "deadline_text": deadline_text,
            "language": LANGUAGE,
            "summary_language": LANGUAGE,
            "category": categories[0],
            "categories": categories,
            "kind": "opportunity" if deadline_text else "programme-overview",
            "host_countries": sorted(set(host_countries)),
            "eligible_countries": [],
            "publisher_country": PUBLISHER_COUNTRY,
            "status": "expired" if deadline and deadline < now[:10] else "unknown",
            "eligibility": eligibility,
            "funding": funding,
            "programme_features": features,
            "programme_details": details,
            "tracks": tracks,
            "source_documents": provenance,
            "classification": {
                "method": "official-html-programme",
                "status": "classified",
                "evidence": [
                    "Official early-career programme discovery; not a vacancy inventory",
                    *sorted(sources),
                ],
            },
        }
        record["id"] = profile_identity(record)
        record.update(
            {
                key: now
                for key in (
                    "created_at",
                    "updated_at",
                    "first_seen_at",
                    "last_seen_at",
                    "last_checked_at",
                )
            }
        )
        records.append(record)
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
        "name": "IBM — advertised early-career programme information",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "attribution": ATTRIBUTION,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (previous.get("last_success_at") if error else attempt),
        "last_checked_at": (previous.get("last_checked_at") if error else checked_at),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else f"Validated refresh retains {len(records)} IBM programme records"
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
        <= previous.keys()
        or previous.get("name") != "IBM — advertised early-career programme information"
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
        or (previous.get("status") == "success" and previous.get("error") is not None)
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
        raise AdapterError("Invalid previous source metadata contract", "publish")
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
        raise AdapterError("Invalid previous metadata timestamps", "publish") from None


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
            last_seen_at=attempt,
            last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records)


def remove_expired_records(records, checked_at):
    """Filter validated deadlines without guessing missing semantics."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    retained = []
    for record in records:
        deadline = record["deadline"]
        if deadline is None:
            retained.append(record)
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing_date = datetime.strptime(deadline, "%Y-%m-%d").date()
            expired = (
                checked.astimezone(timezone(timedelta(hours=-12))).date()
                > closing_date
            )
        else:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            expired = closing.tzinfo is not None and checked > closing
        if not expired:
            retained.append(record)
    return retained


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
                    candidate_records = json.loads(candidate["files"]["data.json"])
                    candidate_metadata = json.loads(candidate["files"]["metadata.json"])
                    validate_records(candidate_records, allow_empty=True)
                    validate_prior_metadata(candidate_metadata, candidate_records)
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            fresh_records(records, old, attempt, checked)
            records = remove_expired_records(records, checked)
            validate_records(records, allow_empty=True)
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
    raise SystemExit(main())
