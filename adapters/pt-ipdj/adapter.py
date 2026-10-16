"""Collect IPDJ’s bounded public programme, call and educational frontier."""

import argparse
import base64
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import gzip
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

SOURCE_ID = "pt-ipdj"
SOURCE_URL = "https://ipdj.gov.pt/candidaturas"
WEBSITE_URL = "https://ipdj.gov.pt/"
LANGUAGE = "pt"
PUBLISHER_COUNTRY = "PT"
PUBLISHER_TYPE = "public-body"
ATTRIBUTION = "Instituto Português do Desporto e Juventude https://ipdj.gov.pt/ and the named programme administrators"

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
    "volunteering",
    "competitions",
    "other",
}
FAMILY = "youthopps-ipdj-publisher-v1"
FAMILY_SOURCES = {"pt-ipdj"}
COLLECTION_TIMEOUT = 3100
RUN_TIMEOUT = 3490
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "ipdj-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_STATE_NEW = False
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 6
_PUBLISHER_GATE_FAILED = False


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


class TransientPublisherError(AdapterError):
    """Typed network failure eligible for one bounded public-GET retry."""


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
        journal_finish()


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
    global _STATE_PATH, _STATE_LOCK, _GUARD_NEW_DIRECTORY
    if _STATE_PATH and os.path.lexists(os.path.dirname(_STATE_PATH)):
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + ("-") + str(os.getuid())
    )
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")
    if not os.path.lexists(directory) and not _GUARD_REMOTE_INITIALIZING:
        return
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    else:
        _GUARD_NEW_DIRECTORY = _GUARD_REMOTE_INITIALIZING
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AdapterError("Unsafe publisher pacing directory", "access")
    if info.st_mode & 0o077:
        raise AdapterError("Unsafe publisher pacing permissions", "access")
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")


def locked_budget():
    global _GUARD_LOCK_ID
    budget_file()
    if _GUARD_FATAL:
        raise AdapterError("Publisher persistence gate failed", "access")
    parent = journal_parent()
    descriptor = identity = None
    try:
        if _GUARD_LOCKS:
            original = next(iter(_GUARD_LOCKS))
            journal_owned(original, _GUARD_LOCKS[original])
            descriptor = os.dup(original)
        else:
            name = os.path.basename(_STATE_LOCK)
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open(name, flags, dir_fd=parent)
            except FileNotFoundError:
                if not journal_creation_allowed():
                    raise AdapterError(
                        "Mature publisher lock missing", "access"
                    )
                descriptor = os.open(
                    name,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent,
                )
                pass
        acquired = os.fstat(descriptor)
        identity = (acquired.st_dev, acquired.st_ino)
        info = journal_owned(descriptor, identity)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        journal_owned(descriptor, identity)
        current = os.stat(
            os.path.basename(_STATE_LOCK), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != identity or (
            _GUARD_LOCK_ID is not None and _GUARD_LOCK_ID != identity
        ):
            raise AdapterError("Publisher journal lock changed", "access")
        _GUARD_LOCK_ID = identity
        result = JournalLock(descriptor, identity)
        try:
            journal_retain(None)
        except BaseException:
            result.close()
            descriptor = None
            raise
        return result
    except BaseException:
        journal_failed()
        if descriptor is not None:
            if identity is not None:
                journal_close(descriptor, identity, True)
        raise


def load_budget():
    raw = journal_read()
    if not raw and _GUARD_CREATED and journal_creation_allowed():
        return empty_budget(time.time())
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher pacing state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher pacing state", "access") from None


def save_budget(state):
    state = validate_budget(state)
    journal_commit(state)


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
        state["observed_at"] = now
        save_budget(state)


@contextlib.contextmanager
def publisher_attempt(publisher):
    """Persist a completion gate around every physical publisher attempt."""
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
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


def reviewed_redirect(destination, current, status):
    """Reject unchanged routes with bounded credential-free provenance."""
    try:
        return public_url(destination)
    except AdapterError:
        try:
            parsed = urllib.parse.urlsplit(destination)
            target = urllib.parse.urlunsplit(
                (parsed.scheme, parsed.hostname or "", parsed.path, "", "")
            )
        except ValueError:
            target = "invalid redirect destination"
        raise AdapterError(
            "Publisher redirect refused: HTTP "
            + str(status)
            + ": "
            + safe_error(current)
            + " -> "
            + safe_error(target),
            "access",
            status,
        ) from None


def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=8388608,
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
        with publisher_attempt(publisher):
            try:
                if publisher:
                    journal_preopener()
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
                location = response.headers.get("Location")
                if publisher and status in (401, 403, 429):
                    if status in (401, 403) or retry is None:
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
                if publisher and status in (301, 302, 303, 307, 308):
                    if not location or redirect == 5:
                        raise AdapterError("Invalid redirect chain", "access")
                    destination = urllib.parse.urljoin(current, location)
                    reviewed_redirect(destination, current, status)
                    if urllib.parse.urlsplit(destination).scheme != "https":
                        raise AdapterError(
                            "Publisher redirect transport downgrade", "access"
                        )
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
                    encoding = response.headers.get(
                        "Content-Encoding", "identity"
                    ).lower()
                    if encoding != "identity" and not (
                        publisher
                        and encoding == "gzip"
                        and headers.get("Accept-Encoding") == "gzip"
                    ):
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
                reviewed_redirect(destination, current, status)
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
        try:
            status, headers, body = request_bytes(
                reviewed, headers={"Accept-Encoding": "gzip"}, publisher=True
            )
        except TransientPublisherError:
            if attempt:
                raise
            status = None
        if status in (200, 404, 410):
            encoding = headers.get("Content-Encoding", "identity").lower()
            if encoding == "gzip":
                try:
                    with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                        body = compressed.read(8388609)
                except (OSError, EOFError):
                    raise AdapterError(
                        "Invalid compressed publisher HTML", "parse"
                    ) from None
                if len(body) > 8388608:
                    raise AdapterError(
                        "Oversized decoded publisher HTML", "parse"
                    )
            elif encoding not in ("", "identity"):
                raise AdapterError(
                    "Unsupported publisher content encoding", "parse"
                )
            return status, headers, body
        if status in (None, 503) and attempt == 0:
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
        raw = os.environ.get("IPDJ_PACING_BOOTSTRAP", "")
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
    with journal_restore_scope(incoming), locked_budget():
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


def open_fresh_pacing_output(runner_parent, runner_root):
    """Use the official fresh per-step output file, never an inherited marker."""
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
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("IPDJ_PACING_ARTIFACT_PATH")
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
    parent_identity = None
    output_identity = None
    output_handles = ()
    try:
        directory = os.fstat(parent)
        parent_identity = (directory.st_dev, directory.st_ino)
        if (
            directory.st_uid != os.getuid()
            or not stat.S_ISDIR(directory.st_mode)
            or directory.st_mode & 0o022
            or directory.st_mode & 0o700 != 0o700
        ):
            raise AdapterError(
                "Unsafe pacing artifact directory owner", "access"
            )
        io_verify_current_parent(parent, root, parent_identity)
        existing = None
        try:
            existing = os.open(
                name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            pass
        if existing is not None:
            existing_identity = None
            try:
                info = os.fstat(existing)
                existing_identity = (info.st_dev, info.st_ino)
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
                io_artifact_descriptor(existing, existing_identity)
                os.ftruncate(existing, 0)
                os.fsync(existing)
                io_artifact_descriptor(existing, existing_identity)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            finally:
                io_close_owned_descriptor(existing, existing_identity)
        if _PUBLISHER_GATE_FAILED or _GUARD_FATAL:
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
        io_verify_current_parent(parent, root, parent_identity)
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        acquired = os.fstat(descriptor)
        identity = (acquired.st_dev, acquired.st_ino)
        parent_id = parent_identity
        output_identity = identity
        guard = None
        output_handles = (descriptor, guard)
        try:
            io_artifact_descriptor(descriptor, identity)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise AdapterError(
                    "Owned artifact acquisition changed", "access"
                )
            guard = os.dup(descriptor)
            output_handles = (descriptor, guard)
            io_artifact_descriptor(guard, identity)
            with locked_budget():
                writer = os.dup(descriptor)
                try:
                    io_artifact_descriptor(writer, identity)
                    payload = json.dumps(
                        envelope, separators=(",", ":")
                    ).encode()
                    offset = 0
                    while offset < len(payload):
                        io_verify_current_parent(parent, root, parent_identity)
                        io_artifact_descriptor(writer, identity)
                        written = os.write(writer, payload[offset:])
                        if (
                            type(written) is not int
                            or not 0 < written <= len(payload) - offset
                        ):
                            raise AdapterError(
                                "Incomplete owned artifact write", "access"
                            )
                        offset += written
                        io_artifact_descriptor(writer, identity)
                    os.fsync(writer)
                    io_artifact_descriptor(writer, identity)
                finally:
                    io_close_owned_descriptor(writer, identity)
                os.fsync(parent)
                io_verify_current_parent(parent, root, parent_identity)
                check = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
                try:
                    info = os.fstat(check)
                    current = os.stat(
                        name, dir_fd=parent, follow_symlinks=False
                    )
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
                    reader_identity = (info.st_dev, info.st_ino)
                    before_read = journal_generation(info)
                    raw = os.read(check, 16385)
                    io_verify_current_parent(parent, root, parent_identity)
                    after_read = io_artifact_descriptor(check, reader_identity)
                    current = os.stat(
                        name, dir_fd=parent, follow_symlinks=False
                    )
                    if (
                        journal_generation(after_read) != before_read
                        or (current.st_dev, current.st_ino) != identity
                    ):
                        raise AdapterError(
                            "Artifact changed during read", "access"
                        )
                    if len(raw) > 16384 or json.loads(raw) != envelope:
                        raise AdapterError(
                            "Finalized pacing artifact mismatch", "access"
                        )
                finally:
                    # The known artifact identity is available even if the
                    # reader validation/read failed before reader_identity.
                    io_close_owned_descriptor(check, identity)
                journal_verify()
                io_signal_ready(parent, root, run_id, attempt)
                journal_verify()
                io_artifact_descriptor(descriptor, identity)
                io_artifact_descriptor(guard, identity)
                io_verify_current_parent(parent, root, parent_identity)
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if identity != (named.st_dev, named.st_ino):
                    raise AdapterError("Owned artifact changed", "access")
        except BaseException:
            journal_failed()
            # Cleanup must never replace the primary handoff/body failure.
            with contextlib.suppress(BaseException):
                io_invalidate_owned_artifact(
                    parent, root, name, parent_id, identity, (guard, descriptor)
                )
            raise
        finally:
            primary = sys.exc_info()[1]
            errors = []
            for handle in (descriptor, guard):
                if handle is not None:
                    try:
                        io_artifact_descriptor(handle, identity)
                        os.close(handle)
                    except BaseException as error:
                        journal_failed()
                        errors.append(error)
            if errors:
                with contextlib.suppress(BaseException):
                    io_invalidate_owned_artifact(
                        parent,
                        root,
                        name,
                        parent_id,
                        identity,
                        (guard, descriptor),
                    )
                if primary is None:
                    raise errors[0]
    finally:
        primary = sys.exc_info()[1]
        close_error = None
        try:
            io_verify_current_parent(parent, root, parent_identity)
            os.close(parent)
        except BaseException as error:
            journal_failed()
            close_error = error
        if close_error is not None or _GUARD_FATAL:
            # Keep the original primary even when a parent replacement made
            # earlier output cleanup impossible through its retained FD.
            if output_identity is not None:
                recovery = None
                with contextlib.suppress(BaseException):
                    recovery = os.open(
                        root,
                        os.O_RDONLY
                        | os.O_DIRECTORY
                        | os.O_NOFOLLOW
                        | os.O_NONBLOCK,
                    )
                    io_verify_owned_descriptor(recovery, parent_identity, True)
                    io_invalidate_owned_artifact(
                        recovery,
                        root,
                        name,
                        parent_identity,
                        output_identity,
                        output_handles,
                    )
                if recovery is not None:
                    with contextlib.suppress(BaseException):
                        io_close_owned_descriptor(
                            recovery, parent_identity, True
                        )
        if close_error is not None and primary is None:
            raise close_error


def prepare_collection():
    global _GUARD_COLLECTION_ID, _GUARD_NEW_DIRECTORY
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    _COLLECTION_STARTED = time.time()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(os.path.dirname(_STATE_PATH), "collection.lock")
        parent = journal_parent()
        descriptor = acquired_id = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open("collection.lock", flags, dir_fd=parent)
            except FileNotFoundError:
                if not _GUARD_NEW_DIRECTORY:
                    raise AdapterError(
                        "Mature collection lock missing", "access"
                    )
                descriptor = os.open(
                    "collection.lock",
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent,
                )
            acquired = os.fstat(descriptor)
            acquired_id = (acquired.st_dev, acquired.st_ino)
            journal_owned(descriptor, acquired_id)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal_owned(descriptor, acquired_id)
            current = os.stat(
                "collection.lock", dir_fd=parent, follow_symlinks=False
            )
            if acquired_id != (current.st_dev, current.st_ino):
                raise AdapterError(
                    "Publisher collection lock changed", "access"
                )
        except BaseException:
            journal_failed()
            if descriptor is not None and acquired_id is not None:
                journal_close(descriptor, acquired_id, True)
            raise
        _GUARD_COLLECTION_ID = acquired_id
        _COLLECTION_LOCK_FD = descriptor
        _GUARD_NEW_DIRECTORY = False
    with locked_budget():
        state = load_budget()
        now = time.time()
        state["not_before"] = max(state["not_before"], now + 60)
        state["observed_at"] = now
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
            expired = checked > closing
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


NEWS_URL = WEBSITE_URL + "noticias"
PROGRAMMES_URL = WEBSITE_URL + "programas"
SESSIONS_URL = WEBSITE_URL + "formacao-online-e-oficinas"
# Public programme sections not linked by the main programme directory.
PROGRAMME_ROOTS = (
    WEBSITE_URL
    + "programa-pejene-programa-de-est%C3%A1gios-de-jovens-estudantes-do-ensino-superior-e-nas-empresas",
    WEBSITE_URL + "programa-de-apoio-a-formacao-de-recursos-humanos-pafrh",
    WEBSITE_URL + "programa-de-apoio-a-acoes-de-formacao-paaf",
    WEBSITE_URL + "bolsas-acad%C3%A9micas",
)
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "janeiro",
            "fevereiro",
            "marco",
            "abril",
            "maio",
            "junho",
            "julho",
            "agosto",
            "setembro",
            "outubro",
            "novembro",
            "dezembro",
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
    """Admit discovered public HTML routes, never forms or delegated hosts."""
    try:
        parsed = urllib.parse.urlsplit(value)
        path = urllib.parse.unquote(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "ipdj.gov.pt"
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.query
            or ".." in path.split("/")
            or "\\" in path
            or not path.startswith("/")
            or path.startswith(("/c/", "/o/", "/group/", "/web/"))
            or re.match(r"/(?:login|logout|registo|autentic)", path, re.I)
        ):
            raise ValueError()
    except ValueError:
        raise AdapterError(
            "Unsupported official public route", "access"
        ) from None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def read_html(url):
    status, headers, body = fetch_public(url)
    if status != 200:
        raise AdapterError(
            "Publisher detail unavailable: " + url, "fetch", status
        )
    if "html" not in headers.get("Content-Type", "").lower():
        raise AdapterError("Expected publisher HTML: " + url, "parse")
    return document_root(body.decode("utf-8", "strict"))


def classify(title):
    value = folded(title)
    if re.search(
        r"\b(?:webinares?|seminarios?|conferencias?|cursos?|workshops?|modulo\s+\d)\b",
        value,
    ):
        return "training"
    for pattern, category in (
        (r"voluntari|campo.s?.de.trabalho", "volunteering"),
        (r"estagio|inov contacto|pejene", "internships"),
        (r"bolsa", "scholarships"),
        (r"premio|concurso|jovens criadores", "competitions"),
        (r"apoio|financia|associa-te|arribar|instalacoes|pndpt", "grants"),
        (
            r"formacao|formar|curso|oficina|workshop|summit|academia|lideranc|webinar|seminario|conferencia|simposio|capacitacao",
            "training",
        ),
    ):
        if re.search(pattern, value):
            return category
    return "other"


def application_dates(text, context_year=None):
    """Keep dates without an explicit year or unambiguous closing unset."""
    value = folded(text)
    closings = set()
    pattern = (
        r"(?:(?:candidaturas|inscricoes)\s+"
        r"(?:(?:estao|encontram-se)\s+)?(?:abertas\s*)?"
        r"(?:(?:estao a )?decorre[m]?\s+(?:aqui\s+)?|(?:podem|devem) ser (?:apresentadas|formalizadas)\s+)?"
        r",?\s*(?:ate|terminam(?:\s+em)?|encerram(?:\s+em)?)"
        r"|candidaturas\s+para atribuicao de bolsas\s+ate"
        r"|candidaturas\s+podem ser realizadas aqui\s*,\s*de\s+\d{1,2}\s+de\s+[a-z]+\s+a"
        r"|periodo de candidaturas?\s*(?:\d{4}(?:-\d{4})?)?\s*:\s*decorreu\s+ate"
        r"|prazo(?:\s+limite)?\s+(?:de|para)\s+(?:candidaturas?|inscric(?:ao|oes))"
        r"\s+(?:termina(?:\s+em)?|encerra(?:\s+em)?|ate)"
        r"|(?:candidaturas|inscricoes)\s+(?:decorrem|estao abertas)\s+de\s+"
        r"\d{1,2}(?:\s+de\s+[a-z]+(?:\s+de\s+\d{4})?)?\s+(?:a|ate))\s+"
        r"(?:(?:ao\s+)?(?:proximo\s+)?dia\s+)?"
        r"(?:as\s+\d{1,2}[h:]\d{2}\s+(?:de|do dia)\s+)?"
        r"(\d{1,2})(?:\s+de\s+|/)([a-z]+|\d{1,2})"
        r"(?:(?:\s+de\s+|/)(\d{4}))?"
    )
    matches = list(re.finditer(pattern, value))
    # A date range qualifies only when its predicate explicitly opens applications.
    reverse = (
        r"de\s+\d{1,2}(?:\s+de\s+[a-z]+)?\s+a\s+"
        r"(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\s*,\s*"
        r"(?:(?:estao abertas|decorre o periodo de)\s+)?(?:as\s+)?candidaturas"
    )
    matches.extend(re.finditer(reverse, value))
    date_end = r"(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?"
    date_start = r"\d{1,2}(?:\s+de\s+[a-z]+(?:\s+de\s+\d{4})?)?"
    # Bound qualifiers to the application noun; subordinate event/project clauses
    # must not turn their execution dates into application deadlines.
    qualifier = r"(?:(?!\b(?:que|execucao|decorrem|decorre|decorrera)\b)[^.!?;]){0,180}?"
    matches.extend(
        re.finditer(
            r"(?:candidaturas|inscricoes)"
            + qualifier
            + r"\s+(?:decorrem|decorre|decorrera)\s+(?:entre|de)\s+"
            + date_start
            + r"\s+(?:e|a)\s+"
            + date_end,
            value,
        )
    )
    matches.extend(
        re.finditer(
            r"(?:periodo de inscricoes)"
            + qualifier
            + r", que decorre entre\s+"
            + date_start
            + r"\s+e\s+"
            + date_end,
            value,
        )
    )
    matches.extend(
        re.finditer(
            r"(?:candidaturas|inscricoes)"
            + qualifier
            + r"\s+(?:foi|foram) prorrogad[oa]s?\s+ate\s+"
            + r"(?:(?:ao\s+)?(?:proximo\s+)?dia\s+)?"
            + date_end,
            value,
        )
    )
    matches.extend(
        re.finditer(
            r"estao abertas,\s+ate\s+(?:(?:ao\s+)?(?:proximo\s+)?dia\s+)?"
            + date_end
            + r",\s+as candidaturas",
            value,
        )
    )
    for match in matches:
        day, month, year = match.groups()
        year = year or context_year
        if not year:
            continue
        try:
            number = int(month) if month.isdigit() else MONTHS[month]
            closings.add(
                datetime(int(year), number, int(day)).date().isoformat()
            )
        except (ValueError, KeyError):
            continue
    return closings


def application_deadline(text, context_year=None):
    closings = application_dates(text, context_year)
    return next(iter(closings)) if len(closings) == 1 else None


def material_facts(text):
    """Expose substantive funding and entry conditions outside the introduction."""
    normalized = folded(text)
    facts = []
    patterns = (
        (
            "Published funding: ",
            r"\d[\d., ]*\s*(?:eur\b|€|euros?\b)|financiamento|dotacao",
        ),
        (
            "Published eligibility: ",
            r"destinatarios|beneficiarios|podem candidatar|idades?\s|ensino basico|requisitos|criterios de selecao|direitos dos|residencia|experiencia|qualquer jovem|independentemente|\d+ (?:a|aos) \d+ anos",
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


def article_content(root, url):
    articles = [
        n
        for n in nodes(root, class_name="journal-content-article")
        if nodes(n, "h1")
    ]
    if len(articles) == 1:
        content = articles[0]
        title = one(nodes(content, "h1"), "article title").text()
        return title, [content]
    titles = [n for n in nodes(root, "h1") if n.attrs.get("id") == "titulo"]
    if len(titles) == 1:
        blocks = [
            n
            for n in nodes(root, class_name="padding")
            if n.text() and "col-sm-7" in n.attrs.get("class", "").split()
        ]
        if blocks:
            return titles[0].text(), blocks
    raise AdapterError(
        "Missing substantive programme/article content: " + url, "parse"
    )


def parse_detail(root, url, programme=False):
    canonical = [
        link.attrs["href"]
        for link in nodes(root, "link")
        if "canonical" in link.attrs.get("rel", "").split()
        and link.attrs.get("href")
    ]
    if canonical:
        url = public_url(
            urllib.parse.urljoin(url, one(canonical, "canonical URL"))
        )
    programme = programme or "/-/" not in urllib.parse.urlsplit(url).path
    title, blocks = article_content(root, url)
    text = " ".join(block.text() for block in blocks)
    if len(text) < len(title) + 60:
        raise AdapterError("Incomplete official detail: " + url, "parse")
    value = folded(text)
    # Require an applicant/participant offer; a navigation label is insufficient.
    if not re.search(
        r"candidatur|candidatar\b|inscric|inscrev|participar\b|participe\b|destinatari|beneficiari|requerer\b|destina-se|destinam-se|aberto ao publico|sao destinadas|podem usufruir|promove, no proximo|junte-se a nos|podem dirigir as suas sugestoes|aceda aqui ao programa do evento",
        value,
    ) and not (
        programme
        and re.search(
            r"apoio formativo|bolsas individuais|apoio ao empreendedorismo|formacao destinado",
            value,
        )
    ):
        return None
    category = classify(title)
    if category == "other":
        category = classify(
            " ".join(n.text() for b in blocks for n in nodes(b, "h2"))
        )
    kind = "programme-overview" if programme else "opportunity"
    if category == "grants" and re.search(
        r"associac|entidades|organizac|municip", value
    ):
        kind = "institutional-grant"
    record = make_record(
        title,
        url,
        [category],
        kind=kind,
        method="publisher-html",
        evidence=[ATTRIBUTION, "Live official content: " + url]
        + material_facts(text),
    )
    paragraphs = [
        n.text() for b in blocks for n in nodes(b, "p") if len(n.text()) > 60
    ]
    summary = " ".join(paragraphs[:2]) or text
    record["summary"] = (
        summary[:480].rsplit(" ", 1)[0] if len(summary) > 480 else summary
    )
    record["summary_language"] = LANGUAGE
    record["deadline"] = application_deadline(text)
    today = datetime.now(timezone.utc).date().isoformat()
    if record["deadline"] and record["deadline"] < today:
        record["status"] = "expired"
    elif re.search(r"(?:candidaturas|inscricoes) (?:estao )?encerradas", value):
        record["status"] = "expired"
    # Publication and event dates are not application deadlines.
    return record


def offer_units(blocks):
    """Flatten prose boundaries without repeating nested list/paragraph text."""
    result = []
    for block in blocks:
        for node in block.walk():
            if node.tag in ("p", "h2", "h3", "h4") or (
                node.tag == "li"
                and not nodes(node, "p")
                and len(nodes(node, "li")) == 1
            ):
                if node.text() and not folded(node.text()).startswith(
                    "atualizado em:"
                ):
                    result.append((node.tag, node.text()))
    return result


def edition_year(title, units):
    years = set(re.findall(r"\b20\d{2}\b", title))
    if not years:
        text = " ".join(value for _, value in units)
        text = re.sub(r"\b\d{1,2}/\d{1,2}/\d{4}\b", "", text)
        years = set(re.findall(r"\b20\d{2}\b", text))
    return next(iter(years)) if len(years) == 1 else None


def section_marker(text):
    value = folded(text)
    if value.startswith("webinar ") and ":" in value and len(text) <= 240:
        if "papel dos municipios" in value:
            return "municipalities", text
        if "papel dos clubes e associacoes" in value:
            return "clubs-associations", text
        return re.sub(r"\W+", "-", value).strip("-"), text
    module = re.match(r"modulo\s+(\d+)\s*[-–]\s*(.+)", value)
    if module:
        key = "module-" + module[1]
        for pattern, legacy in (
            ("enraizar a mudanca", "enraizar-mudanca"),
            ("orientacao sexual", "oiec"),
            ("mutilacao genital", "fgm"),
        ):
            if pattern in module[2]:
                key = legacy
        return key, text
    if value.startswith("premio boas praticas | associativismo "):
        audience = value.split(" | ", 1)[1]
        key = {
            "associativismo juvenil": "youth-associations",
            "associativismo estudantil": "higher-education-student-associations",
        }.get(audience, re.sub(r"\W+", "-", audience).strip("-"))
        # Some list items contain the explanatory paragraph in the same element.
        if "associativismo juvenil" in audience:
            key = "youth-associations"
        elif "associativismo estudantil" in audience:
            key = "higher-education-student-associations"
        return key, text.split(" Reconhece", 1)[0]
    if len(text) <= 240 and (
        value.startswith("programa de apoio")
        or re.match(r"PA[A-Z]+\s*\|", text)
    ):
        code = re.search(r"\b(PA[A-Z]+)\b", text)
        if code:
            return code[1].lower(), text.split(" – ", 1)[0]
    return None


def split_offers(root, record):
    """Extract individually selectable HTML modules, regions and funding tracks."""
    _, blocks = article_content(root, record["url"])
    units = offer_units(blocks)
    year = edition_year(record["title"], units)
    independent_modules = re.search(
        r"inscricao nao e obrigatoria para todos os modulos|inscrever-se nas sessoes",
        folded(" ".join(text for _, text in units)),
    )
    common, sections = [], {}
    current = None
    pending_date = ""
    for tag, text in units:
        if tag in ("h3", "h4") and re.match(r"\d{1,2} de [^|]+\|", text):
            pending_date = text
            continue
        marker = section_marker(text)
        if folded(text).startswith("modulo ") and not independent_modules:
            marker = None
        if marker:
            key, label = marker
            current = sections.setdefault(key, {"label": label, "facts": []})
            if pending_date:
                current["facts"].append(pending_date)
                pending_date = ""
            current["facts"].append(text)
        elif folded(text) in (
            "premios por regiao",
            "premios por regiao:",
            "como candidatar-se",
            "divulgacao dos vencedores",
        ):
            current = None
            common.append(text)
        elif current is not None:
            current["facts"].append(text)
        else:
            common.append(text)
    value = folded(record["title"])
    if value == "namorar com fair play":
        common, sections = [], {}
        current = None
        edition = None
        for tag, text in units:
            label = folded(text)
            if label == "modalidade 1":
                current = sections.setdefault(
                    "entidades", {"label": "Entidades", "facts": []}
                )
            elif label == "inscricoes":
                current = sections.setdefault(
                    "voluntariado", {"label": "Voluntariado", "facts": []}
                )
            elif label == "modalidade 2":
                current = None
            elif label == "financiamento da modalidade 1":
                current = sections.get("entidades")
            elif label == "mais informacoes":
                current = None
            elif current is not None:
                current["facts"].append(text)
                if "candidaturas" in label:
                    years = set(re.findall(r"\b20\d{2}\b", text))
                    if len(years) == 1:
                        edition = years.pop()
            else:
                common.append(text)
        if edition:
            for key, section in sections.items():
                section["legacy"] = (
                    "Namorar com Fair Play — "
                    + key
                    + " — "
                    + edition
                    + "::::::::"
                )
    if "apoio financeiro" in value and "desporto federado" in value:
        common, sections, current = [], {}, None
        for tag, text in units:
            if tag == "h3":
                key = re.sub(r"\W+", "-", folded(text)).strip("-")
                current = sections.setdefault(key, {"label": text, "facts": []})
            elif current is None:
                common.append(text)
            else:
                current["facts"].append(text)
        for section in sections.values():
            facts = " ".join(section["facts"])
            edition = re.search(
                r"período de candidatura\s+(\d{4}(?:-\d{4})?)", facts, re.I
            )
            label = folded(section["label"])
            legacy = None
            if edition and label == "atividades regulares":
                legacy = (
                    "Apoio à atividade regular das federações — " + edition[1]
                )
            elif (
                edition
                and label == "organizacao de eventos desportivos internacionais"
            ):
                legacy = (
                    "Apoio à organização de eventos desportivos internacionais — "
                    + edition[1]
                )
            elif (
                edition
                and label == "deslocacoes de e para as regioes autonomas"
            ):
                legacy = "Deslocações às Regiões Autónomas — época " + edition[
                    1
                ].replace("-", "/")
            if legacy:
                section["legacy"] = legacy + "::::::::"
    if not sections and "academias" in value and "regionais" in value:
        common = []
        for tag, paragraph in units:
            if tag != "p":
                continue
            current = None
            date = re.search(r"No dia \d{1,2} de \w+", paragraph)
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                match = re.search(
                    r"sessão(?: regional)? (?:do|de|da) ([^,.]+?)"
                    r"(?= (?:realiza|acontece)|,)",
                    sentence,
                    re.I,
                )
                if match:
                    label = match[1]
                    key = re.sub(r"\W+", "-", folded(label)).strip("-")
                    if key == "lisboa-e-vale-do-tejo":
                        key = "lvt"
                    current = sections.setdefault(
                        key, {"label": label, "facts": []}
                    )
                    if date:
                        current["facts"].append(date[0])
                if current is not None:
                    current["facts"].append(sentence)
                else:
                    common.append(sentence)
    if not sections and "festival politica" in value:
        common = []
        for _, paragraph in units:
            for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
                match = re.search(
                    r"edição de (.+?)(?= do Festival| serão)", sentence
                )
                if match:
                    label = match[1]
                    key = re.sub(r"\W+", "-", folded(label)).strip("-")
                    sections[key] = {"label": label, "facts": [sentence]}
                else:
                    common.append(sentence)
    if not sections and "voluntariado" in value and "portugal vs" in value:
        common = [text for _, text in units]
        for _, paragraph in units:
            match = re.search(
                r"dias (\d{1,2}) e (\d{1,2}) de (\w+).*?nacionais da (.+?) e da (.+?), respetivamente",
                paragraph,
                re.I,
            )
            if not match:
                continue
            day1, day2, month, country1, country2 = match.groups()
            number = MONTHS.get(folded(month))
            if not number:
                raise AdapterError("Unknown volunteering event month", "parse")
            month_key = (
                "jan",
                "feb",
                "mar",
                "apr",
                "may",
                "jun",
                "jul",
                "aug",
                "sep",
                "oct",
                "nov",
                "dec",
            )[number - 1]
            for day, country in ((day1, country1), (day2, country2)):
                key = {
                    "republica da irlanda": "ireland",
                    "hungria": "hungary",
                }.get(
                    folded(country),
                    re.sub(r"\W+", "-", folded(country)).strip("-"),
                )
                sections[key + "-" + month_key + str(int(day))] = {
                    "label": "Portugal vs " + country,
                    "facts": [
                        "Portugal vs "
                        + country
                        + ": "
                        + day
                        + " de "
                        + month
                        + "."
                    ],
                }
    if not sections:
        return [record]
    shared = record["title"] + " " + " ".join(common)
    result = []
    for key, section in sections.items():
        facts = " ".join(section["facts"])
        track = dict(record)
        track["id"] = hashlib.sha256(
            (
                SOURCE_ID
                + "|"
                + record["url"]
                + "::"
                + section.get("legacy", key)
            ).encode()
        ).hexdigest()[:24]
        track["title"] = record["title"] + " | " + section["label"]
        category = classify(section["label"])
        if category == "other":
            category = classify(record["title"])
        kind = (
            "programme-overview"
            if "/-/" not in record["url"]
            else "opportunity"
        )
        if value == "namorar com fair play":
            category = "grants" if key == "entidades" else "volunteering"
        if category == "grants" and re.search(
            r"associac|entidades|organizac|municip|federac|comite",
            folded(shared + " " + facts),
        ):
            kind = "institutional-grant"
        track["category"] = category
        track["categories"] = [category]
        track["kind"] = kind
        summary = facts or section["label"]
        track["summary"] = (
            summary[:480].rsplit(" ", 1)[0] if len(summary) > 480 else summary
        )
        track["classification"] = {
            **record["classification"],
            "status": "unknown" if category == "other" else "classified",
            "evidence": [
                ATTRIBUTION,
                "Live official section: " + record["url"],
                "Published section: " + section["label"],
            ]
            + material_facts(shared + " " + facts),
        }
        closings = application_dates(facts, year)
        if not closings:
            closings = application_dates(shared, year)
        track["deadline"] = next(iter(closings)) if len(closings) == 1 else None
        track["status"] = "unknown"
        if (
            track["deadline"]
            and track["deadline"]
            < datetime.now(timezone.utc).date().isoformat()
        ):
            track["status"] = "expired"
        result.append(track)
    return result


def discover_news(root):
    cards = nodes(root, class_name="card-body")
    if not cards:
        raise AdapterError("Missing news cards", "parse")
    result = {}
    for card in cards:
        title = one(
            nodes(card, class_name="card-text-title"), "news card title"
        ).text()
        value = folded(card.text())
        if not re.search(
            r"candidat|inscri|bolsa|estagio|voluntariado|curso|formacao|oficina|workshop|summit|webinar|seminario|conferencia|simposio|capacitacao|encontro",
            value,
        ):
            continue
        if re.search(
            r"^(?:resultados|vencedores|entrega|balanco)|foram distinguid|foram premiad",
            folded(title),
        ):
            continue
        link = one(nodes(card, "a", "stretched-link"), "news detail link")
        url = public_url(
            urllib.parse.urljoin(NEWS_URL, link.attrs.get("href", ""))
        )
        result[url] = False
    return result, len(cards)


def parse_sessions(root):
    _, blocks = article_content(root, SESSIONS_URL)
    records = []
    year = None
    for block in blocks:
        for node in block.walk():
            text = node.text()
            if node.tag in ("p", "h2", "h3") and re.fullmatch(r"20\d{2}", text):
                year = text
            if node.tag != "li" or not re.match(r"\d{1,2} de ", text):
                continue
            match = re.match(
                r"(\d{1,2}) de ([^,]+),\s*(\d{1,2}h\d{2})\s*[-–]\s*«([^»]+)»",
                text,
            )
            if not year or not match:
                raise AdapterError(
                    "Unrecognized dated calendar session", "parse"
                )
            day, month, clock, title = match.groups()
            try:
                datetime(int(year), MONTHS[folded(month)], int(day))
            except (ValueError, KeyError):
                raise AdapterError(
                    "Invalid session calendar date", "parse"
                ) from None
            key = "::".join((SESSIONS_URL, title, year, day, month, clock))
            record = make_record(
                title,
                SESSIONS_URL,
                ["training"],
                kind="programme-overview",
                method="publisher-html",
                evidence=[
                    ATTRIBUTION,
                    "Live official calendar: " + SESSIONS_URL,
                ],
            )
            record["id"] = hashlib.sha256(
                (SOURCE_ID + "|" + key).encode()
            ).hexdigest()[:24]
            summary = "Calendário publicado: " + year + ". " + text
            record["summary"] = (
                summary[:480].rsplit(" ", 1)[0]
                if len(summary) > 480
                else summary
            )
            record["summary_language"] = LANGUAGE
            records.append(record)
    if not records:
        raise AdapterError("Missing educational session calendar", "parse")
    return records


def discover_programmes(root):
    items = nodes(root, class_name="list-item")
    if not items:
        raise AdapterError("Missing programme inventory", "parse")
    result = {}
    external = 0
    for item in items:
        link = one(nodes(item, "a"), "programme link")
        target = urllib.parse.urljoin(
            PROGRAMMES_URL, link.attrs.get("href", "")
        )
        if urllib.parse.urlsplit(target).hostname != "ipdj.gov.pt":
            external += 1
            continue
        if re.match(
            r"/(?:registo|login|autentic)", urllib.parse.urlsplit(target).path
        ):
            external += 1
            continue
        result[public_url(target)] = True
    return result, len(items), external


def read_pages():
    programmes, programme_count, external = discover_programmes(
        read_html(PROGRAMMES_URL)
    )
    news, news_count = discover_news(read_html(NEWS_URL))
    discovered = {**news, **programmes, **dict.fromkeys(PROGRAMME_ROOTS, True)}
    if len(discovered) > 400:
        raise AdapterError(
            "Discovered detail frontier exceeds safety ceiling", "parse"
        )
    records = parse_sessions(read_html(SESSIONS_URL))
    excluded = 0
    unavailable = []
    for url, programme in sorted(discovered.items()):
        try:
            root = read_html(url)
        except AdapterError as error:
            if error.status not in (404, 410) or url in PROGRAMME_ROOTS:
                raise
            unavailable.append(url)
            print(
                f"Unavailable detail HTTP {error.status}: {url}",
                file=sys.stderr,
            )
            continue
        record = parse_detail(root, url, programme)
        if record is None:
            excluded += 1
        else:
            records.extend(split_offers(root, record))
    print(
        f"Inspected {news_count} news cards, {programme_count} programme links; "
        f"{external} delegated programmes excluded; {len(discovered)} own details; "
        f"{excluded} details without participant offers; {len(unavailable)} unavailable details",
        file=sys.stderr,
    )
    return records


def parse_inventory(records):
    unique = {}
    for record in records:
        previous = unique.get(record["id"])
        if previous is not None and previous["title"] != record["title"]:
            raise AdapterError(
                "Conflicting canonical page identities", "validate"
            )
        unique[record["id"]] = record
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
        "display_name": "IPDJ",
        "source": SOURCE_ID,
        "name": "IPDJ — bounded public programmes, calls and educational sessions",
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
            else f"Collected {len(records)} reviewed IPDJ opportunities"
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
        or previous.get("source") != SOURCE_ID
        or previous.get("source_url") != SOURCE_URL
        or previous.get("website_url") != WEBSITE_URL
        or not isinstance(previous.get("message"), str)
        or previous.get("status") not in {"success", "fail"}
        or type(previous.get("record_count")) is not int
        or previous["record_count"] != len(records)
        or previous.get("language") != LANGUAGE
        or previous.get("publisher_country") != PUBLISHER_COUNTRY
        or previous.get("publisher_type") != PUBLISHER_TYPE
        or previous.get("attribution") != ATTRIBUTION
        or previous.get("name")
        != ("IPDJ — bounded public programmes, calls and educational sessions")
        or "error" not in previous
        or (
            previous.get("status") == "success"
            and previous["error"] is not None
        )
        or (
            previous.get("status") == "fail"
            and (
                not isinstance(previous["error"], str)
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
        if records and timestamps["last_checked_at"] != max(
            datetime.fromisoformat(
                record["last_checked_at"].replace("Z", "+00:00")
            )
            for record in records
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
    validate_records(records)


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


_GUARD_PARENT = _GUARD_PARENT_ID = None
_GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
_GUARD_LOCK_ID = None
_GUARD_LOCKS = {}
_GUARD_CREATED = _GUARD_DIRTY = _GUARD_FATAL = False
_GUARD_COLLECTION_ID = None
_GUARD_NEW_DIRECTORY = False
_GUARD_FAILED_STATE = None
_GUARD_REMOTE_INITIALIZING = False
_PUBLISHER_GATE_FAILED = False


def journal_generation(info):
    """Identify one observed inode and byte generation, not an FS CAS."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    )


def journal_failed():
    """Keep storage failures sticky independently of completion flags."""
    global _GUARD_FATAL, _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    _GUARD_FATAL = _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False


def journal_parent():
    """Retain the original safe parent; never follow a replacement."""
    global _GUARD_PARENT, _GUARD_PARENT_ID
    directory = os.path.dirname(_STATE_PATH)
    descriptor = _GUARD_PARENT
    fresh = descriptor is None
    identity = None
    try:
        if fresh:
            descriptor = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
        held = os.fstat(descriptor)
        identity = (held.st_dev, held.st_ino)
        current = os.stat(directory, follow_symlinks=False)
        if (
            not stat.S_ISDIR(held.st_mode)
            or held.st_uid != os.getuid()
            or stat.S_IMODE(held.st_mode) != 0o700
            or held.st_nlink < 2
            or identity != (current.st_dev, current.st_ino)
            or not fresh
            and identity != _GUARD_PARENT_ID
        ):
            raise AdapterError("Publisher journal parent changed", "access")
    except BaseException:
        journal_failed()
        if fresh and identity is not None:
            io_close_owned_descriptor(descriptor, identity, True)
        raise
    if fresh:
        _GUARD_PARENT, _GUARD_PARENT_ID = descriptor, identity
    return descriptor


def journal_owned(descriptor, identity=None, unlinked=False):
    """Check the actual acquired FD before use or release."""
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink not in ((0, 1) if unlinked else (1,))
            or identity is not None
            and (info.st_dev, info.st_ino) != identity
        ):
            raise AdapterError(
                "Publisher descriptor ownership changed", "access"
            )
        return info
    except BaseException:
        journal_failed()
        raise


def journal_close(descriptor, identity, primary=False, unlinked=False):
    """Preserve foreign reused FDs and an already active primary error."""
    try:
        journal_owned(descriptor, identity, unlinked)
        os.close(descriptor)
    except BaseException:
        journal_failed()
        if not primary:
            raise


def journal_verify(check_generation=True):
    """Require original current RW journal and all actually held locks."""
    try:
        if _GUARD_FATAL or _GUARD_FD is None:
            raise AdapterError(
                "Publisher journal authority unavailable", "access"
            )
        parent = journal_parent()
        info = journal_owned(_GUARD_FD, _GUARD_ID)
        current = os.stat(
            os.path.basename(_STATE_PATH),
            dir_fd=parent,
            follow_symlinks=False,
        )
        if (
            (current.st_dev, current.st_ino) != _GUARD_ID
            or check_generation
            and journal_generation(info) != _GUARD_VERSION
            or fcntl.fcntl(_GUARD_FD, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        for descriptor, identity in _GUARD_LOCKS.items():
            journal_owned(descriptor, identity)
        if _GUARD_LOCK_ID is not None:
            lock = os.stat(
                os.path.basename(_STATE_LOCK),
                dir_fd=parent,
                follow_symlinks=False,
            )
            if (lock.st_dev, lock.st_ino) != _GUARD_LOCK_ID:
                raise AdapterError("Publisher journal lock changed", "access")
        return info
    except BaseException:
        journal_failed()
        raise


def journal_retain(descriptor=None):
    """Pin a genuine RW journal only under its original stable lock."""
    global _GUARD_FD, _GUARD_ID, _GUARD_VERSION, _GUARD_CREATED
    if _GUARD_FD is not None:
        journal_verify()
        return
    parent = journal_parent()
    name = os.path.basename(_STATE_PATH)
    if descriptor is not None:
        info = journal_owned(descriptor)
        duplicate = os.dup(descriptor)
        try:
            journal_owned(duplicate, (info.st_dev, info.st_ino))
        except BaseException:
            journal_failed()
            raise
        descriptor = duplicate
    else:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            if not journal_creation_allowed():
                raise AdapterError("Mature publisher journal missing", "access")
            descriptor = os.open(
                name,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
            _GUARD_CREATED = True
    info = journal_owned(descriptor)
    _GUARD_FD = descriptor
    _GUARD_ID = (info.st_dev, info.st_ino)
    _GUARD_VERSION = journal_generation(info)
    journal_verify()
    if _GUARD_CREATED:
        os.fsync(parent)


class JournalLock:
    """Own an acquired lock FD without implicit file-object destruction."""

    def __init__(self, descriptor, identity):
        self.descriptor = descriptor
        self.identity = identity
        _GUARD_LOCKS[descriptor] = identity

    def fileno(self):
        journal_owned(self.descriptor, self.identity)
        return self.descriptor

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def close(self):
        descriptor = self.descriptor
        if descriptor is None:
            return
        primary = sys.exc_info()[0] is not None
        self.descriptor = None
        _GUARD_LOCKS.pop(descriptor, None)
        try:
            journal_owned(descriptor, self.identity, True)
            if not _GUARD_LOCKS:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            journal_owned(descriptor, self.identity, True)
            os.close(descriptor)
        except BaseException:
            journal_failed()
            if not primary:
                raise


def journal_read():
    """Read and recheck the same current generation before trusting bytes."""
    try:
        if not _GUARD_LOCKS:
            raise AdapterError("Publisher journal lock required", "access")
        journal_verify()
        raw = os.pread(_GUARD_FD, 16385, 0)
        journal_verify()
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher journal", "access")
        return raw
    except BaseException:
        journal_failed()
        raise


def journal_commit(state):
    """Rewrite only our held inode; interrupted bytes sacrifice availability."""
    global _GUARD_VERSION, _GUARD_CREATED, _GUARD_DIRTY
    global _GUARD_FAILED_STATE
    if _GUARD_FATAL:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        payload = json.dumps(state, separators=(",", ":")).encode()
        if not _GUARD_LOCKS or len(payload) > 16384:
            raise AdapterError(
                "Bounded locked publisher commit required", "access"
            )
        journal_verify()
        _GUARD_CREATED = False
        journal_mark_mature()
        _GUARD_DIRTY = True
        os.ftruncate(_GUARD_FD, 0)
        os.fsync(_GUARD_FD)
        journal_verify(False)
        offset = 0
        while offset < len(payload):
            journal_verify(False)
            written = os.pwrite(_GUARD_FD, payload[offset:], offset)
            if (
                type(written) is not int
                or not 0 < written <= len(payload) - offset
            ):
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += written
            journal_verify(False)
        os.fsync(_GUARD_FD)
        before = journal_verify(False)
        raw = os.pread(_GUARD_FD, len(payload) + 1, 0)
        after = journal_verify(False)
        if raw != payload or journal_generation(before) != journal_generation(
            after
        ):
            raise AdapterError("Publisher journal readback changed", "access")
        _GUARD_VERSION = journal_generation(after)
        _GUARD_DIRTY = False
    except BaseException:
        journal_failed()
        with contextlib.suppress(BaseException):
            _GUARD_FAILED_STATE = json.loads(json.dumps(state))
        with contextlib.suppress(BaseException):
            journal_invalidate()
        raise


def journal_invalidate():
    """Invalidate only our own bytes, never a stronger changed generation."""
    if _GUARD_FD is None:
        return
    journal_parent()
    info = journal_owned(_GUARD_FD, _GUARD_ID, True)
    current = os.stat(
        os.path.basename(_STATE_PATH),
        dir_fd=_GUARD_PARENT,
        follow_symlinks=False,
    )
    if (current.st_dev, current.st_ino) != _GUARD_ID:
        return
    if not _GUARD_DIRTY and journal_generation(info) != _GUARD_VERSION:
        return
    os.ftruncate(_GUARD_FD, 0)
    os.fsync(_GUARD_FD)


def journal_preopener():
    """Final publisher authority check, after request construction."""
    try:
        journal_verify()
        if _GUARD_COLLECTION_ID is None or _COLLECTION_LOCK_FD is None:
            raise AdapterError(
                "Publisher collection ownership missing", "access"
            )
        journal_owned(_COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID)
        named = os.stat(
            "collection.lock", dir_fd=journal_parent(), follow_symlinks=False
        )
        if (named.st_dev, named.st_ino) != _GUARD_COLLECTION_ID:
            raise AdapterError("Publisher collection lock changed", "access")
    except BaseException:
        journal_failed()
        raise


def journal_release():
    """Release retained IO only; never close a recycled foreign FD."""
    global _GUARD_FD, _GUARD_ID, _GUARD_VERSION
    global _GUARD_PARENT, _GUARD_PARENT_ID
    descriptor, identity = _GUARD_FD, _GUARD_ID
    _GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
    primary = sys.exc_info()[1]
    errors = []
    if descriptor is not None:
        try:
            journal_close(descriptor, identity, False, True)
        except BaseException as error:
            errors.append(error)
    parent, parent_id = _GUARD_PARENT, _GUARD_PARENT_ID
    _GUARD_PARENT = _GUARD_PARENT_ID = None
    if parent is not None:
        try:
            io_close_owned_descriptor(parent, parent_id, True)
        except BaseException as error:
            errors.append(error)
    if errors and primary is None:
        raise errors[0]


def io_verify_current_parent(descriptor, root, expected):
    """Check both the retained directory and its current configured name."""
    io_verify_owned_descriptor(descriptor, expected, True)
    named = os.stat(root, follow_symlinks=False)
    if (named.st_dev, named.st_ino) != expected:
        raise AdapterError("Owned artifact parent changed", "access")


def io_verify_owned_descriptor(descriptor, expected, directory=False):
    """Verify an acquired IO descriptor without adopting a recycled number."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != expected
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or directory
        and not stat.S_ISDIR(info.st_mode)
        or not directory
        and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)
    ):
        raise AdapterError("Owned IO descriptor changed", "access")
    return info


def io_close_owned_descriptor(descriptor, expected, directory=False):
    """Close our acquired object only, preserving an active primary error."""
    primary = sys.exc_info()[1]
    try:
        info = os.fstat(descriptor)
        if not directory and info.st_nlink == 0:
            # An exact acquired file may have been deliberately unlinked.
            # Closing that original inode is safe; live path checks stay strict.
            if (
                (info.st_dev, info.st_ino) != expected
                or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
            ):
                raise AdapterError("Owned IO descriptor changed", "access")
        else:
            io_verify_owned_descriptor(descriptor, expected, directory)
        os.close(descriptor)
    except BaseException:
        journal_failed()
        if primary is None:
            raise


def io_artifact_descriptor(descriptor, expected):
    """Require the genuine acquired output inode, not a recycled FD number."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != expected
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink not in (0, 1)
    ):
        raise AdapterError("Owned artifact descriptor changed", "access")
    return info


def io_invalidate_owned_artifact(
    parent, root, name, parent_id, identity, handles
):
    """Invalidate only our held original or an exact known original name."""
    held_parent = os.fstat(parent)
    named_parent = os.stat(root, follow_symlinks=False)
    if (held_parent.st_dev, held_parent.st_ino) != parent_id or (
        named_parent.st_dev,
        named_parent.st_ino,
    ) != parent_id:
        raise AdapterError("Owned artifact parent changed", "access")
    descriptor = None
    owned = False
    for handle in handles:
        if handle is not None:
            try:
                io_artifact_descriptor(handle, identity)
                descriptor = handle
                break
            except (OSError, AdapterError):
                pass
    if descriptor is None:
        # Last-close errors may leave no held FD. Reopening is allowed only
        # for this exact original output identity, never a newer pathname.
        descriptor = os.open(
            name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        owned = True
    try:
        io_artifact_descriptor(descriptor, identity)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and (current.st_dev, current.st_ino) == identity:
            os.unlink(name, dir_fd=parent)
    finally:
        if owned:
            primary = sys.exc_info()[1]
            try:
                io_artifact_descriptor(descriptor, identity)
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise


def journal_creation_allowed():
    return _GUARD_NEW_DIRECTORY and _GUARD_REMOTE_INITIALIZING


def journal_mark_mature():
    global _GUARD_CREATED
    _GUARD_CREATED = False


@contextlib.contextmanager
def journal_restore_scope(incoming):
    """Authorize creation only from the original verified incoming state."""
    global _GUARD_REMOTE_INITIALIZING
    validate_budget(incoming)
    if _GUARD_REMOTE_INITIALIZING:
        raise AdapterError("Nested publisher restore refused", "access")
    _GUARD_REMOTE_INITIALIZING = True
    try:
        yield
    finally:
        _GUARD_REMOTE_INITIALIZING = False


def journal_finish():
    """Release both independently; a healthy close failure is fatal."""
    global _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    primary = sys.exc_info()[1]
    errors = []
    descriptor, identity = _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    _COLLECTION_LOCK_FD = _GUARD_COLLECTION_ID = None
    if descriptor is not None:
        try:
            if identity is None:
                raise AdapterError("Collection identity not acquired", "access")
            journal_close(descriptor, identity)
        except BaseException as error:
            journal_failed()
            errors.append(error)
    try:
        journal_release()
    except BaseException as error:
        journal_failed()
        errors.append(error)
    if _GUARD_FATAL:
        with contextlib.suppress(BaseException):
            export_family_artifact()
    if errors and primary is None:
        raise errors[0]


def io_signal_ready(runner_parent, runner_root, run_id, attempt):
    """Signal readiness while checking every acquired output descriptor."""
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
    root_info = os.fstat(runner_parent)
    root_id = (root_info.st_dev, root_info.st_ino)

    def verify_outputs():
        io_verify_owned_descriptor(runner_parent, root_id, True)
        named_root = os.stat(runner_root, follow_symlinks=False)
        if (named_root.st_dev, named_root.st_ino) != root_id:
            raise AdapterError("Step output root changed", "access")
        for fd, identity, is_directory, parent_fd, name in descriptors:
            io_verify_owned_descriptor(fd, identity, is_directory)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != identity:
                raise AdapterError("Step output path changed", "access")

    try:
        for name in parts[:-1]:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            descriptors.append((descriptor, identity, True, parent, name))
            verify_outputs()
            parent = descriptor
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        descriptors.append((descriptor, identity, False, parent, parts[-1]))
        verify_outputs()
        line = (
            f"pacing_run_id={run_id}\npacing_run_attempt={attempt}\n"
            "pacing_ready=true\n"
        ).encode()
        if os.write(descriptor, line) != len(line):
            raise AdapterError("Incomplete pacing-ready handoff", "access")
        verify_outputs()
        os.fsync(descriptor)
        verify_outputs()
    except BaseException:
        journal_failed()
        raise
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, identity, is_directory, _, _ in reversed(descriptors):
            try:
                io_close_owned_descriptor(fd, identity, is_directory)
            except BaseException as error:
                errors.append(error)
        if errors and primary is None:
            raise errors[0]


if __name__ == "__main__":
    sys.exit(main())
