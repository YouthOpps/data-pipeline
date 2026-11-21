"""Collect fifteen guarded Google student and early-career programme pages.

This is a bounded programme-information source, not a global vacancy inventory.
Only original factual summaries and canonical links are republished. IIE terms
prohibit automated extraction, so delegated IIE eligibility is never imported.
"""

import argparse
import base64
import contextlib
import copy
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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib

SOURCE_ID = "google"
SOURCE_URL = "https://buildyourfuture.withgoogle.com/"
WEBSITE_URL = "https://www.google.com/about/careers/applications/buildyourfuture/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "US"
PUBLISHER_TYPE = "company"
ATTRIBUTION = "Google — official student and early-career programme information"
SOURCE_NAME = "Google — student and early-career programme information"

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
FAMILY = "youthopps-google-publisher-v1"
FAMILY_SOURCES = {"google"}
COLLECTION_TIMEOUT = 900
RUN_TIMEOUT = 1290
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 3
_ARTIFACT_NAME = "google-pacing-state"
_REQUEST_STARTS = 0
MAX_PUBLISHER_STARTS = 100
_COLLECTION_BYTES = 0
MAX_COLLECTION_BYTES = 64 * 1024 * 1024
_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_INITIALIZING = False
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False
_INVENTORY_ACTIVE = False
_INVENTORY_COMPLETED_MONOTONIC = None


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
    signal.setitimer(signal.ITIMER_REAL, 0.05, 0.05)
    for request_frame in _REQUEST_FRAMES:
        request_frame["assessed"] = False
    if _REQUEST_FRAMES:
        fail_policy()
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
    global _REQUEST_STARTS, _COLLECTION_BYTES, _RESTORED, _STATE_TRUSTED
    global _STARTUP_QUIET_UNTIL, _LOCAL_FIRST_WIRE_VERIFIED
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    if (
        _PUBLISHER_GATE_FAILED
        or _JOURNAL_FATAL
        or _ACTIVE_FLIGHT_ID
        or _ACTIVE_REQUEST_ID
        or _REQUEST_FRAMES
    ):
        raise AdapterError("Google publisher failure remains sticky", "access")
    _REQUEST_STARTS = _COLLECTION_BYTES = 0
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
    _STARTUP_QUIET_UNTIL = None
    _ROBOTS.clear()
    _REP_PROVENANCE.clear()
    _REP_TARGETS.clear()
    _LOCAL_CLAIMS.clear()
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        primary = sys.exc_info()[1]
        try:
            try:
                journal_release()
            finally:
                close_state_authorities()
        except BaseException:
            with contextlib.suppress(BaseException):
                journal_invalidate_export()
            if primary is None:
                raise
        finally:
            if _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
                with contextlib.suppress(BaseException):
                    journal_invalidate_export()
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            _RUN_END = None


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
    """Accept only complete modern state; legacy migration is explicit."""
    if (
        type(state) is not dict
        or set(state) != set(empty_budget(0))
        or type(state["schema"]) is not int
        or state["schema"] != 3
        or state["family"] != FAMILY
        or any(
            type(state[k]) is not bool
            for k in ("blocked", "in_flight", "request_inventory")
        )
        or state["legacy_rate_history_unknown"] is not True
        or state["legacy_policy_history_unknown"] is not True
    ):
        raise AdapterError("Untrusted Google journal schema", "access")
    numeric_keys = (
        "observed_at",
        "not_before",
        "completed",
        "interval",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_until",
        "flight_started",
        "request_started",
    )
    if (
        any(
            not numeric(state[k]) or state[k] > 253402300799
            for k in numeric_keys
        )
        or state["interval"] < 12.2
        or state["page_interval"] < 30
        or type(state["rate_limit_strikes"]) is not int
        or not 0 <= state["rate_limit_strikes"] <= 100
        or type(state["starts"]) is not list
        or len(state["starts"]) > 512
        or any(
            not numeric(v) or v > state["observed_at"] for v in state["starts"]
        )
        or state["starts"] != sorted(set(state["starts"]))
        or state["completed"] > state["observed_at"]
        or state["last_inventory_completed"] > state["observed_at"]
        or state["observed_at"] > time.time() + 1
    ):
        raise AdapterError("Untrusted Google timing constraints", "access")
    for key in (
        "flight_id",
        "completed_flight_id",
        "request_id",
        "completed_request_id",
    ):
        if state[key] is not None and not (
            isinstance(state[key], str)
            and re.fullmatch("[0-9a-f]{32}", state[key])
        ):
            raise AdapterError(
                "Untrusted Google transaction identity", "access"
            )
    if state["flight_kind"] not in (None, "policy"):
        raise AdapterError("Untrusted Google policy owner", "access")
    if state["in_flight"] and (
        not state["flight_id"] or state["flight_kind"] != "policy"
    ):
        raise AdapterError("Missing Google pending owner", "access")
    if state["request_outcome"] not in (
        None,
        "reserved",
        "response_assessed",
        "no_response",
    ):
        raise AdapterError("Untrusted Google request outcome", "access")
    if state["request_outcome"] is not None and not state["request_id"]:
        raise AdapterError("Missing Google physical owner", "access")
    if (
        state["request_outcome"] == "reserved"
        and not state["in_flight"]
        or state["request_outcome"] == "response_assessed"
        and state["request_id"] != state["completed_request_id"]
    ):
        raise AdapterError("Unresolved Google physical owner", "access")
    return copy.deepcopy(state)


def merge_budgets(local, incoming, now):
    """MAX restrictions and union observations without settling any owner."""
    local, incoming = validate_budget(local), validate_budget(incoming)
    if local["in_flight"] or incoming["in_flight"]:
        if local != incoming:
            raise AdapterError(
                "Unresolved Google state cannot be merged", "access"
            )
        return local
    combined = copy.deepcopy(
        local if local["observed_at"] >= incoming["observed_at"] else incoming
    )
    for key in (
        "observed_at",
        "not_before",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_strikes",
        "rate_limit_until",
    ):
        combined[key] = max(local[key], incoming[key])
    combined["observed_at"] = max(combined["observed_at"], now)
    combined["blocked"] = local["blocked"] or incoming["blocked"]
    combined["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    return validate_budget(combined)


def empty_budget(now):
    return {
        "schema": 3,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "completed": 0,
        "page_interval": 30,
        "last_inventory_completed": 0,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "legacy_policy_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "flight_started": 0,
        "flight_kind": None,
        "completed_flight_id": None,
        "request_id": None,
        "request_started": 0,
        "request_inventory": False,
        "completed_request_id": None,
        "request_outcome": None,
    }


def verify_state_directory():
    parent = os.fstat(_STATE_PARENT_FD)
    parent_name = os.stat(
        os.path.dirname(os.path.dirname(_STATE_PATH)), follow_symlinks=False
    )
    if (
        not stat.S_ISDIR(parent.st_mode)
        or (parent.st_dev, parent.st_ino) != _STATE_PARENT_ID
        or (parent_name.st_dev, parent_name.st_ino) != _STATE_PARENT_ID
        or (parent.st_uid, stat.S_IMODE(parent.st_mode))
        != _STATE_PARENT_METADATA
        or (parent_name.st_uid, stat.S_IMODE(parent_name.st_mode))
        != _STATE_PARENT_METADATA
    ):
        raise AdapterError("Original Google parent changed", "access")
    info = os.fstat(_STATE_DIR_FD)
    current = os.stat(
        os.path.basename(os.path.dirname(_STATE_PATH)),
        dir_fd=_STATE_PARENT_FD,
        follow_symlinks=False,
    )
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_nlink < 2
        or (info.st_dev, info.st_ino) != _STATE_DIR_ID
        or (current.st_dev, current.st_ino) != _STATE_DIR_ID
    ):
        raise AdapterError("Original Google family changed", "access")


def secure_file(name, flags):
    verify_state_directory()
    descriptor, identity = journal_open_original(_STATE_DIR_FD, name, flags)
    return descriptor


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING, _STATE_PARENT_FD, _STATE_PARENT_ID
    global _STATE_PARENT_METADATA
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    named = os.stat(temporary, follow_symlinks=False)
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identity = (named.st_dev, named.st_ino)
    metadata = (named.st_uid, stat.S_IMODE(named.st_mode))
    shared_sticky = metadata[1] == 0o1777
    if (
        (named.st_uid not in (0, os.getuid()) and not shared_sticky)
        or named.st_mode & 0o022
        and not shared_sticky
        or (os.fstat(parent).st_dev, os.fstat(parent).st_ino) != identity
    ):
        os.close(parent)
        raise AdapterError("Unsafe Google journal parent", "access")
    try:
        leaf = FAMILY + "-" + str(os.getuid())
        created = False
        if os.environ.get("GITHUB_ACTIONS") == "true":
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
            or (linked.st_dev, linked.st_ino) != (info.st_dev, info.st_ino)
        ):
            os.close(descriptor)
            raise AdapterError("Unsafe Google journal family", "access")
        _STATE_DIR_FD, _STATE_DIR_ID = descriptor, (info.st_dev, info.st_ino)
        _STATE_PARENT_FD = os.dup(parent)
        _STATE_PARENT_ID, _STATE_PARENT_METADATA = identity, metadata
        _STATE_INITIALIZING = created
        _STATE_PATH = os.path.join(temporary, leaf, "state.json")
        _STATE_LOCK = os.path.join(temporary, leaf, "state.lock")
        verify_state_directory()
    finally:
        os.close(parent)


def locked_budget():
    """Lock the retained original journal authority for a budget transaction."""
    return JournalLease()


def load_budget():
    global _JOURNAL_PRESERVE_UNKNOWN
    raw = journal_read()
    if not raw and _JOURNAL_NEW and _JOURNAL_BOOTSTRAP_ALLOWED:
        return empty_budget(time.time())
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, UnicodeError, AdapterError):
        # Unknown local history already blocks admission. Preserve it for
        # review rather than replacing it with an empty allowance/barrier.
        _JOURNAL_PRESERVE_UNKNOWN = True
        journal_failure()
        raise


def save_budget(state):
    """Commit only through the retained, locked original RW descriptor."""
    journal_write(state)


def pace(*, inventory=False):
    global _REQUEST_STARTS, _PUBLISHER_INTERVAL, _ACTIVE_REQUEST_ID
    check_deadline()
    if (
        _REQUEST_STARTS >= MAX_PUBLISHER_STARTS
        or _PUBLISHER_GATE_FAILED
        or not _ACTIVE_FLIGHT_ID
    ):
        raise AdapterError("Google physical admission unavailable", "access")
    with locked_budget():
        state = load_budget()
        if (
            not state["in_flight"]
            or state["flight_id"] != _ACTIVE_FLIGHT_ID
            or state["blocked"]
            or state["request_outcome"] == "reserved"
            or state["rate_limit_strikes"] >= 100
        ):
            raise AdapterError(
                "Unresolved Google physical admission", "access"
            )
        now = time.time()
        starts = [v for v in state["starts"] if v > now - 60]
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        wait_until = max(
            state["not_before"],
            state["rate_limit_until"],
            state["completed"] + _PUBLISHER_INTERVAL,
            now,
        )
        if len(starts) >= 5:
            wait_until = max(wait_until, starts[-5] + 60)
        if _PUBLISHER_COMPLETED_MONOTONIC is not None:
            wait_until = max(
                wait_until,
                now
                + max(
                    0,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                ),
            )
        if _STARTUP_QUIET_UNTIL is not None:
            wait_until = max(
                wait_until,
                now + max(0, _STARTUP_QUIET_UNTIL - time.monotonic()),
            )
        while True:
            check_deadline()
            wait = max(0, wait_until - time.time())
            if _PUBLISHER_COMPLETED_MONOTONIC is not None:
                wait = max(
                    wait,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                )
            if _STARTUP_QUIET_UNTIL is not None:
                wait = max(wait, _STARTUP_QUIET_UNTIL - time.monotonic())
            if wait <= 0:
                break
            if wait + 120 > min(_PHASE_END, _RUN_END) - time.monotonic():
                raise AdapterError(
                    "Google physical guard exceeds phase", "access"
                )
            time.sleep(wait)
        now = time.time()
        ident = os.urandom(16).hex()
        if ident == state["request_id"]:
            raise AdapterError("Google physical identity collision", "access")
        state.update(
            request_id=ident,
            request_started=now,
            request_outcome="reserved",
            request_inventory=inventory,
            observed_at=max(state["observed_at"], now),
        )
        retained = _GOOGLE407_HOSTED_STATE["starts"] if os.environ.get("GITHUB_ACTIONS") == "true" else []
        state["starts"] = sorted(set(retained + [v for v in starts if v > now - 60] + [now]))
        state["interval"] = _PUBLISHER_INTERVAL
        state["not_before"] = max(
            state["not_before"], now + _PUBLISHER_INTERVAL
        )
        save_budget(state)
        _ACTIVE_REQUEST_ID = ident
        _REQUEST_STARTS += 1


@contextlib.contextmanager
def publisher_attempt(publisher, inventory=False):
    global _ACTIVE_REQUEST_ID, _PUBLISHER_COMPLETED_MONOTONIC
    global _INVENTORY_COMPLETED_MONOTONIC, _LAST_RESPONSE_RECEIPT
    global _LAST_RESPONSE_URL
    if not publisher:
        yield None
        return
    frame = {
        "id": _ACTIVE_REQUEST_ID,
        "assessed": False,
        "received": False,
        "inventory": inventory,
        "no_response": False,
    }
    _REQUEST_FRAMES.append(frame)
    try:
        yield frame
    finally:
        primary = sys.exc_info()[1]
        _REQUEST_FRAMES.pop()
        if not frame["assessed"] and not frame["no_response"]:
            fail_policy()
        elif not _PUBLISHER_GATE_FAILED and not _JOURNAL_FATAL:
            try:
                with locked_budget():
                    state = load_budget()
                    if (
                        state["flight_id"] != _ACTIVE_FLIGHT_ID
                        or state["request_id"] != frame["id"]
                        or state["request_outcome"] != "reserved"
                    ):
                        raise AdapterError(
                            "Google receipt owner changed", "access"
                        )
                    now = time.time()
                    state.update(
                        completed=max(state["completed"], now),
                        observed_at=max(state["observed_at"], now),
                        request_outcome=(
                            "no_response"
                            if frame["no_response"]
                            else "response_assessed"
                        ),
                        completed_request_id=frame["id"],
                    )
                    state["not_before"] = max(
                        state["not_before"],
                        state["completed"] + state["interval"],
                    )
                    if inventory:
                        state["last_inventory_completed"] = max(
                            state["last_inventory_completed"], now
                        )
                    save_budget(state)
                _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
                if inventory:
                    _INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
                _LAST_RESPONSE_RECEIPT = frame["id"]
                _LAST_RESPONSE_URL = frame["url"]
                _ACTIVE_REQUEST_ID = None
            except BaseException:
                fail_policy()
                raise
        if (
            primary is None
            and not frame["assessed"]
            and not frame["no_response"]
        ):
            raise AdapterError(
                "Google response was not fully assessed", "access"
            )


def retry_after_until(value, now):
    if not isinstance(value, str):
        raise AdapterError("Unassessed Retry-After type", "access")
    value = value.strip()
    if re.fullmatch("[0-9]+", value):
        if len(value) > 12:
            raise AdapterError("Unsupported positive Retry-After", "access")
        until = now + int(value)
        if not numeric(until) or until > 253402300799:
            raise AdapterError("Unsupported positive Retry-After", "access")
        return until if until > now else None
    if re.fullmatch(r"\+?[0-9]+\.[0-9]+", value):
        raise AdapterError(
            "Unassessed positive Retry-After precision", "access"
        )
    if re.fullmatch(_HTTP_DATE_PATTERN, value, re.I):
        try:
            parsed = parsedate_to_datetime(value)
            if re.search(r"[0-9]{2}-[A-Za-z]{3}-[0-9]{2} ", value):
                current_year = datetime.fromtimestamp(now, timezone.utc).year
                year = current_year // 100 * 100 + parsed.year % 100
                if year < current_year - 50:
                    year += 100
                if year > current_year + 50:
                    year -= 100
                parsed = parsed.replace(year=year)
            if parsed.utcoffset() is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            until = parsed.timestamp()
        except (ValueError, TypeError, OverflowError):
            raise AdapterError(
                "Unassessed Retry-After date", "access"
            ) from None
        if not math.isfinite(until) or until > 253402300799:
            raise AdapterError("Unsupported Retry-After date", "access")
        return until if until > now else None
    if re.fullmatch(
        _HTTP_DATE_PATTERN.replace("[0-9]{4}", "[0-9]{4,}"), value, re.I
    ):
        raise AdapterError("Unsupported Retry-After clock", "access")
    return None


def record_retry_after(values, status=None, *, strike=True):
    """Persist received status first, then MAX every assessable field."""
    now = time.time()
    if status == 429 and strike:
        with locked_budget():
            state = load_budget()
            count = state["rate_limit_strikes"] + 1
            if count > 100:
                raise AdapterError("Google rate counter exhausted", "access")
            current_interval, current_page = (
                state["interval"] * 2,
                state["page_interval"] * 2,
            )
            unsupported = any(
                not numeric(v) or v > 253402300799
                for v in (current_interval, current_page, now + 1800)
            )
            state.update(rate_limit_strikes=count, observed_at=now)
            if numeric(current_interval) and current_interval <= 253402300799:
                state["interval"] = max(state["interval"], current_interval)
            if numeric(current_page) and current_page <= 253402300799:
                state["page_interval"] = max(
                    state["page_interval"], current_page
                )
            state["rate_limit_until"] = max(
                state["rate_limit_until"], state["not_before"], now + 1800
            )
            state["not_before"] = max(
                state["not_before"], state["rate_limit_until"]
            )
            # Commit what the received status already establishes before any
            # stronger exponential calculation or header/body callback.
            save_budget(state)
            interval = 12.2 * (2**count)
            page_interval = 30 * (2**count)
            fallback = now + 1800 * (2 ** (count - 1))
            unsupported = unsupported or any(
                not numeric(v) or v > 253402300799
                for v in (interval, page_interval, fallback)
            )
            changed = False
            for key, value in (
                ("interval", interval),
                ("page_interval", page_interval),
                ("rate_limit_until", fallback),
            ):
                if (
                    numeric(value)
                    and value <= 253402300799
                    and value > state[key]
                ):
                    state[key] = value
                    changed = True
            if changed:
                state["not_before"] = max(
                    state["not_before"], state["rate_limit_until"]
                )
                save_budget(state)
            if unsupported:
                raise AdapterError(
                    "Unsupported Google rate arithmetic", "access"
                )
    if isinstance(values, str):
        values = [values]
    if values is None:
        values = []
    if not isinstance(values, (tuple, list)):
        raise AdapterError("Unassessed Retry-After fields", "access")
    maximum, error = 0, None
    for field in values:
        try:
            # A legal HTTP-date contains a comma. Joined dates/delays are
            # ambiguous: retain every visible numeric constraint, then stop.
            if not isinstance(field, str):
                raise AdapterError("Unassessed Retry-After field", "access")
            if re.fullmatch(r"\s*[0-9]+(?:\s*,\s*[0-9]+)+\s*", field):
                pieces = field.split(",")
            elif re.fullmatch(_HTTP_DATE_PATTERN, field.strip(), re.I):
                pieces = [field]
            else:
                dates = list(
                    re.finditer(_HTTP_DATE_COMPONENT_PATTERN, field, re.I)
                )
                if dates or "," in field:
                    pieces = [match.group(1) for match in dates]
                    remainder = re.sub(
                        _HTTP_DATE_COMPONENT_PATTERN, " ", field, flags=re.I
                    )
                    pieces.extend(
                        v for v in remainder.split(",") if v.strip().isdigit()
                    )
                    error = AdapterError(
                        "Ambiguous combined Retry-After", "access"
                    )
                else:
                    pieces = [field]
            for piece in pieces:
                try:
                    maximum = max(maximum, retry_after_until(piece, now) or 0)
                except (AdapterError, OverflowError, ValueError) as problem:
                    error = problem
        except (AdapterError, OverflowError, ValueError) as problem:
            error = problem
    if maximum:
        with locked_budget():
            state = load_budget()
            changed = maximum > state["not_before"]
            state["not_before"] = max(state["not_before"], maximum)
            if status == 429:
                changed = changed or maximum > state["rate_limit_until"]
                state["rate_limit_until"] = max(
                    state["rate_limit_until"], maximum
                )
            if changed:
                state["observed_at"] = max(state["observed_at"], now)
                save_budget(state)
    if error is not None:
        raise error


def retain_publisher_hold():
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
        state["observed_at"] = time.time()
        save_budget(state)


class VisibleText(HTMLParser):
    """Inspect primary visible text, preserving nested DOM suppression."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        # HTML block starts close a paragraph in button scope, including its
        # inline descendants. Keep suppression inherited from outer elements.
        if tag in {
            "address",
            "article",
            "aside",
            "blockquote",
            "center",
            "details",
            "dialog",
            "dir",
            "div",
            "dl",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "header",
            "hgroup",
            "main",
            "menu",
            "nav",
            "ol",
            "p",
            "search",
            "section",
            "summary",
            "ul",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "pre",
            "listing",
            "hr",
        }:
            for index in range(len(self.stack) - 1, -1, -1):
                ancestor = self.stack[index][0]
                if ancestor == "p":
                    del self.stack[index:]
                    break
                if ancestor in {
                    "applet",
                    "button",
                    "caption",
                    "html",
                    "marquee",
                    "object",
                    "table",
                    "td",
                    "template",
                    "th",
                }:
                    break
        values = {}
        for name, value in attrs:
            values.setdefault(name, value)
        style = re.sub(
            r"/\*.*?\*/", "", values.get("style") or "", flags=re.S
        ).lower()
        declarations, priorities = {}, {}
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                name = name.strip()
                important = bool(re.search(r"!\s*important\s*$", value))
                if priorities.get(name) and not important:
                    continue
                declarations[name] = re.sub(
                    r"\s*!\s*important\s*$", "", value
                ).strip()
                priorities[name] = important
        suppressed = (
            tag
            in {
                "script",
                "style",
                "template",
                "noscript",
                "footer",
                "nav",
                "aside",
            }
            or "hidden" in values
            or (values.get("aria-hidden") or "").strip().lower() == "true"
            or declarations.get("display") == "none"
        )
        inherited = self.stack[-1][1] if self.stack else False
        visibility = self.stack[-1][2] if self.stack else False
        if declarations.get("visibility") == "hidden":
            visibility = True
        elif declarations.get("visibility") == "visible":
            visibility = False
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
            self.stack.append((tag, inherited or suppressed, visibility))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.stack or not any(self.stack[-1][1:]):
            self.text.append(data)


def has_429_head_refusal(body, headers):
    """Recognize a qualified HEAD refusal only for the caller's 429 path."""
    prefix = body[:512].decode("utf-8", "replace")
    obvious_html = re.match(
        r"^[\s\ufeff]*(?:<!doctype\s+html\b|"
        r"<(?:html|head|title|body|main|article|section|div|p|form|h[1-6])"
        r"(?:\s|>))", prefix, re.I,
    )
    if (
        "html" not in headers.get("Content-Type", "").lower()
        and not obvious_html
    ):
        return False

    class HeadTitle(VisibleText):
        """Read only the first unsuppressed HEAD's immediate TITLE."""

        def __init__(self):
            super().__init__()
            self.title = []
            self.head_seen = False
            self.head_depth = None
            self.body_started = False

        def handle_starttag(self, tag, attrs):
            if tag not in {
                "html", "head", "title", "meta", "link", "base",
                "style", "script", "noscript", "template",
            }:
                self.body_started = True
            if (
                tag == "head" and not self.head_seen
                and not self.body_started
                and [frame[0] for frame in self.stack] in ([], ["html"])
            ):
                self.head_seen = True
                self.head_depth = len(self.stack) + 1
            super().handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            if tag == "head":
                self.head_depth = None

        def handle_data(self, data):
            if (
                data.strip() and self.head_depth is None
                and (not self.stack or not any(self.stack[-1][1:]))
                and not any(
                    frame[0] in {"head", "title"} for frame in self.stack
                )
            ):
                self.body_started = True
            if (
                self.head_depth is not None and not self.body_started
                and len(self.stack) == self.head_depth + 1
                and self.stack[-1][0] == "title"
                and self.stack[-2][0] == "head"
                and not any(self.stack[-1][1:])
            ):
                self.title.append(data)

    parser = HeadTitle()
    parser.feed(body.decode("utf-8", "replace"))
    title = " ".join(" ".join(parser.title).lower().split())
    return bool(re.search(
        r"just a moment|attention required|access denied|captcha", title, re.I
    ))


def reject_visible_challenge(
    body, headers, *, challenge_only=False, unavailable_rep=False
):
    if challenge_only and has_429_head_refusal(body, headers):
        retain_publisher_hold()
        raise AdapterError("Visible publisher challenge or denial", "access")

    # A challenge redirect must not escape inspection through a wrong MIME.
    prefix = body[:512].decode("utf-8", "replace")
    obvious_html = re.match(
        r"^[\s\ufeff]*(?:<!doctype\s+html\b|"
        r"<(?:html|head|title|body|main|article|section|div|p|form|h[1-6])"
        r"(?:\s|>))", prefix, re.I,
    )
    if "html" not in headers.get("Content-Type", "").lower() and not obvious_html:
        return
    parser = VisibleText()
    parser.feed(body.decode("utf-8", "replace"))
    text = " ".join(" ".join(parser.text).lower().split())
    phrases = (
        "verify you are human",
        "verify that you are human",
        "complete the captcha",
        "enable javascript and cookies to continue",
        "checking your browser",
    )
    if not challenge_only and not unavailable_rep:
        phrases += (
            "access denied",
            "permission denied",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    if any(phrase in text for phrase in phrases):
        retain_publisher_hold()
        raise AdapterError("Visible publisher challenge or denial", "access")


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


def account_collection_bytes(count):
    """Bound aggregate encoded and decoded publisher bytes for this operation."""
    global _COLLECTION_BYTES
    if (
        type(count) is not int
        or count < 0
        or count > MAX_COLLECTION_BYTES - _COLLECTION_BYTES
    ):
        raise AdapterError("Publisher collection byte budget exceeded", "fetch")
    _COLLECTION_BYTES += count


def decode_observed_gzip(body, byte_limit):
    """Decode one complete gzip member within both encoded and decoded bounds."""
    if not isinstance(body, bytes) or len(body) > byte_limit:
        raise AdapterError("Oversized encoded publisher response", "fetch")
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = decoder.decompress(body, byte_limit + 1)
    except zlib.error:
        raise AdapterError(
            "Invalid publisher gzip checksum or stream", "fetch"
        ) from None
    if (
        len(decoded) > byte_limit
        or not decoder.eof
        or decoder.unconsumed_tail
        or decoder.unused_data
    ):
        raise AdapterError(
            "Incomplete, oversized or multiple-member publisher gzip", "fetch"
        )
    return decoded




def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=8388608,
    robots=False,
    inventory=False,
):
    """Bound complete physical responses; keep ambiguous results pending."""
    request_headers = {
        k: v
        for k, v in (headers or {}).items()
        if k.lower() not in ("user-agent", "cookie")
    }
    if publisher:
        request_headers = {
            k: v
            for k, v in request_headers.items()
            if k.lower() not in ("authorization", "proxy-authorization")
        }
    request_headers["User-Agent"] = _USER_AGENT
    current = url
    for hop in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            parsed = urllib.parse.urlsplit(current)
            if (
                method != "GET"
                or payload is not None
                or robots
                and (parsed.path != "/robots.txt" or parsed.query or inventory)
            ):
                raise AdapterError(
                    "Invalid Google publisher purpose", "access"
                )
            if not robots:
                check_robots(current)
            if inventory:
                wait_inventory_page()
            pace(inventory=inventory)
        else:
            infrastructure_url(current)
        request = urllib.request.Request(
            current, data=payload, headers=request_headers, method=method
        )
        with publisher_attempt(publisher, inventory) as frame:
            if publisher:
                frame["url"] = current
            with operation_guard(publisher):
                try:
                    if publisher:
                        journal_before_wire()
                    response = _OPENER.open(
                        request, timeout=request_timeout(publisher)
                    )
                except urllib.error.HTTPError as error:
                    response = error
                except (urllib.error.URLError, OSError) as error:
                    # Record a proven pre-response opener failure separately;
                    # do not create an additional retry or hide TLS failures.
                    cause = (
                        error.reason
                        if isinstance(error, urllib.error.URLError)
                        else error
                    )
                    retryable = isinstance(
                        cause, (ConnectionError, TimeoutError)
                    ) and not isinstance(cause, ssl.SSLError)
                    if publisher and retryable:
                        frame["no_response"] = True
                    raise
                if publisher:
                    frame["received"] = True
                with assessed_response(response, publisher, robots) as status:
                    response_headers = response.headers
                    if publisher:
                        record_retry_after(
                            response_headers.get_all("Retry-After", []),
                            status,
                            strike=False,
                        )
                    lengths = response_headers.get_all("Content-Length", [])
                    transfers = response_headers.get_all(
                        "Transfer-Encoding", []
                    )
                    encodings = response_headers.get_all(
                        "Content-Encoding", []
                    )
                    chunked = (
                        len(transfers) == 1
                        and transfers[0].strip().lower() == "chunked"
                        and not lengths
                        and getattr(response, "chunked", False) is True
                    )
                    if (
                        len(lengths) > 1
                        or transfers
                        and not chunked
                        or any(
                            not v.isdigit()
                            or len(v) > 10
                            or int(v) > byte_limit
                            for v in lengths
                        )
                        or len(encodings) > 1
                    ):
                        raise AdapterError(
                            "Ambiguous Google response framing", "fetch"
                        )
                    declared = int(lengths[0]) if lengths else None
                    location = response_headers.get("Location")
                    body = response.read(byte_limit + 1)
                    if (
                        len(body) > byte_limit
                        or declared is not None
                        and len(body) != declared
                    ):
                        raise AdapterError(
                            "Incomplete or oversized response", "fetch"
                        )
                    if publisher:
                        account_collection_bytes(len(body))
                    encoding = (
                        encodings[0].strip().lower()
                        if encodings
                        else "identity"
                    )
                    if publisher and encoding == "gzip":
                        body = decode_observed_gzip(body, byte_limit)
                        account_collection_bytes(len(body))
                    elif encoding not in ("", "identity"):
                        raise AdapterError(
                            "Unsupported response encoding", "fetch"
                        )
                    if publisher:
                        try:
                            reject_visible_challenge(
                                body,
                                response_headers,
                                challenge_only=status == 429,
                                unavailable_rep=(
                                    robots and 400 <= status < 500
                                    and status != 429
                                ),
                            )
                        except AdapterError:
                            if robots:
                                raise
                            with locked_budget():
                                if not load_budget()["blocked"]:
                                    raise
                            stop = True
                        else:
                            stop = False
                    else:
                        stop = False
                if publisher:
                    frame["assessed"] = True
                if publisher and (
                    stop
                    or status == 429
                    or not robots
                    and status in (401, 403, 451)
                ):
                    raise PublisherStop(
                        "Google publisher refusal", "access", status
                    )
        if status in (301, 302, 303, 307, 308):
            if not location or hop == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            if publisher:
                public_url(destination)
                if (
                    robots
                    and urllib.parse.urlsplit(destination).path
                    != "/robots.txt"
                ):
                    raise AdapterError(
                        "Invalid Google robots redirect", "access"
                    )
                if not robots:
                    check_robots(destination)
            else:
                infrastructure_url(destination)
                old, new = urllib.parse.urlsplit(
                    current
                ), urllib.parse.urlsplit(destination)
                if old.hostname == "api.github.com":
                    raise AdapterError(
                        "Unexpected GitHub API redirect", "publish"
                    )
                if new.netloc != old.netloc:
                    request_headers = {
                        k: v
                        for k, v in request_headers.items()
                        if k.lower()
                        not in (
                            "authorization",
                            "cookie",
                            "proxy-authorization",
                        )
                    }
            current = destination
            continue
        return status, response_headers, body
    raise AdapterError("Unresolved redirect chain", "access")


def wait_inventory_page():
    """Separate inventory completion from ordinary physical completion."""
    with locked_budget():
        state = load_budget()
        target = state["last_inventory_completed"] + state["page_interval"]
        if _INVENTORY_COMPLETED_MONOTONIC is not None:
            target = max(
                target, time.time() + max(
                    0, _INVENTORY_COMPLETED_MONOTONIC + state["page_interval"]
                    - time.monotonic()
                )
            )
        while target > time.time():
            check_deadline()
            wait = target - time.time()
            if wait + 120 > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Inventory gap exceeds phase budget", "access"
                )
            if (
                time.time() + wait + 120
                > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Inventory gap exceeds collection budget", "access"
                )
            time.sleep(wait)


def mark_inventory_completed():
    global _INVENTORY_COMPLETED_MONOTONIC
    _INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
    with locked_budget():
        state = load_budget()
        now = time.time()
        state["last_inventory_completed"] = now
        state["observed_at"] = now
        save_budget(state)


@contextlib.contextmanager
def inventory_page(url):
    """Guard the complete inventory unit, including robots and redirects."""
    global _INVENTORY_ACTIVE
    if url not in INVENTORY_INPUTS:
        yield
        return
    wait_inventory_page()
    _INVENTORY_ACTIVE = True
    try:
        yield
    finally:
        _INVENTORY_ACTIVE = False
        mark_inventory_completed()


def fetch_public(url):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(
            reviewed, publisher=True, inventory=_INVENTORY_ACTIVE
        )
        if status == 200:
            return status, headers, body
        if status == 503 and attempt == 0:
            with locked_budget():
                state = load_budget()
                now = time.time()
                state["not_before"] = max(state["not_before"], now + 30)
                state["observed_at"] = now
                save_budget(state)
            if _INVENTORY_ACTIVE:
                mark_inventory_completed()
                wait_inventory_page()
            continue
        key = reviewed
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise AdapterError(
            f"Required publisher input {key} returned HTTP {status}: "
            + provenance,
            "fetch",
            status,
        )


def workflow_api(path):
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    separator = "&" if "?" in path else "?"
    status, _, raw = request_bytes(
        _WORKFLOW_GITHUB + path + separator + "_fresh=" + str(time.time_ns()),
        headers=headers,
        byte_limit=2 * 1024 * 1024,
    )
    if status != 200:
        try:
            message = json.loads(raw).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        raise AdapterError(
            "GitHub workflow API HTTP "
            + str(status)
            + ": "
            + safe_error(message)[:400],
            "access",
            status,
        )
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise AdapterError("Invalid workflow API JSON", "access") from None


def current_run_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or not re.fullmatch(r"[0-9a-f]{40}", os.environ.get("GITHUB_SHA", ""))
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


def validate_family_identity(run, expected=None):
    source = family_source(run)
    identity, attempt = run.get("id"), run.get("run_attempt")
    head = run.get("head_sha")
    if (
        source != SOURCE_ID
        or run.get("workflow_id") != _WORKFLOW_ID
        or type(identity) is not int
        or identity <= 0
        or type(attempt) is not int
        or attempt <= 0
        or run.get("repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or not isinstance(head, str)
        or not re.fullmatch(r"[0-9a-f]{40}", head)
        or expected is not None
        and (identity, attempt, source, head) != expected
    ):
        raise AdapterError("Untrusted family run identity", "access")
    return identity, attempt, source, head


def completed_identity(run, expected=None):
    """Bind exact attempt/head and actual completed jobs, never updated_at."""
    identity = validate_family_identity(run, expected)
    run_id, attempt, source, head = identity
    exact = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(exact, identity)
    if exact.get("status") != "completed":
        raise AdapterError("Prior family attempt is incomplete", "access")
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        not isinstance(jobs, list) or not jobs or len(jobs) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(jobs)
    ):
        raise AdapterError("Incomplete family attempt jobs", "access")
    starts, ends, seen = [], [], set()
    try:
        for job in jobs:
            if (
                type(job.get("id")) is not int or job["id"] <= 0
                or job["id"] in seen
                or type(job.get("run_id")) is not int
                or job["run_id"] != run_id
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt or job.get("head_sha") != head
                or job.get("head_branch") != "main"
                or job.get("status") != "completed"
            ):
                raise ValueError("Invalid attempt job binding")
            seen.add(job["id"])
            times = [datetime.fromisoformat(job[k].replace("Z", "+00:00"))
                     for k in ("started_at", "completed_at")]
            if any(t.utcoffset() is None for t in times):
                raise ValueError("Missing job timezone")
            began, finished = (t.timestamp() for t in times)
            if not 0 <= began <= finished <= time.time() + 1:
                raise ValueError("Invalid attempt job chronology")
            starts.append(began)
            ends.append(finished)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AdapterError(
            "Untrusted completed family jobs", "access"
        ) from None
    return max(ends), min(starts), *identity


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
    """Exhaust the singleton family and completed queued-rerun history."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    candidates, seen_ids = [], set()
    total, scanned, current_seen = None, 0, False
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/workflows/fetch-google.yml/runs?per_page=100&page={page}"
        )
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int or count < 0
            or not isinstance(runs, list) or len(runs) > 100
        ):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace(
                "Workflow inventory changed during restore", "access"
            )
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            identity, attempt, source, head = validate_family_identity(run)
            if identity in seen_ids:
                raise AdapterError("Duplicate family workflow run", "access")
            seen_ids.add(identity)
            if current and identity == current[0]:
                if (
                    (identity, attempt) != current
                    or head != os.environ["GITHUB_SHA"]
                ):
                    raise AdapterError(
                        "Current attempt/head mismatch", "access"
                    )
                current_seen = True
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(completed_identity(
                        prior, (identity, attempt - 1, source, head)
                    ))
                continue
            if run.get("status") != "completed":
                if not hosted or run.get("status") not in {
                    "queued", "waiting", "pending", "requested"
                }:
                    raise AdapterError("Another family run is active", "access")
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(completed_identity(
                        prior, (identity, attempt - 1, source, head)
                    ))
                continue
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total or hosted and not current_seen:
                raise AdapterError(
                    "Incomplete current family inventory", "access"
                )
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError("Ambiguous family chronology", "access")
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def download_inert_artifact(identity, artifact_id, digest=None):
    """Decode the exact legacy ZIP or a bound modern owned envelope."""
    token = os.environ.get("GITHUB_TOKEN", "")
    request = urllib.request.Request(
        _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
            **({"Authorization": "Bearer " + token} if token else {}),
        },
    )
    try:
        with operation_guard():
            try:
                response = _OPENER.open(request, timeout=request_timeout())
            except urllib.error.HTTPError as error:
                response = error
            with response:
                if response.status != 302:
                    raise AdapterError(
                        "Artifact redirect unavailable",
                        "access",
                        response.status,
                    )
                locations = response.headers.get_all("Location", [])
                if len(locations) != 1:
                    raise AdapterError(
                        "Ambiguous artifact destination", "access"
                    )
                destination = locations[0]
                if len(response.read(1001)) > 1000:
                    raise AdapterError(
                        "Oversized artifact redirect body", "access"
                    )
        infrastructure_url(destination)
        if urllib.parse.urlsplit(destination).hostname == "api.github.com":
            raise AdapterError("Artifact storage origin required", "access")
        # A fresh request carries no API or publication credential.
        status, _, body = request_bytes(destination, byte_limit=32768)
        if (
            status != 200
            or not isinstance(digest, str)
            or not re.fullmatch("sha256:[0-9a-f]{64}", digest)
            or hashlib.sha256(body).hexdigest() != digest[7:]
        ):
            raise AdapterError("Artifact bytes/digest differ", "access")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "pacing-state.json":
                raise AdapterError("Unexpected artifact members", "access")
            entry = entries[0]
            if (
                entry.file_size > 16384
                or entry.flag_bits & 1
                or entry.external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise AdapterError("Unsafe artifact entry", "access")
            envelope = json.loads(archive.read(entry))
        run_id, attempt, source, head = identity
        common = {
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "head_sha",
            "state",
        }
        if (
            type(envelope) is not dict
            or type(envelope.get("schema")) is not int
            or envelope["schema"] != 1
            or envelope.get("repository") != "YouthOpps/data-pipeline"
            or type(envelope.get("run_id")) is not int
            or envelope["run_id"] != run_id
            or type(envelope.get("run_attempt")) is not int
            or envelope["run_attempt"] != attempt
            or envelope.get("source") != source
            or envelope.get("head_sha") != head
        ):
            raise AdapterError("Artifact ownership differs", "access")
        producer = hashlib.sha256(verified_source(head)).hexdigest()
        if set(envelope) == common:
            reviewed_transition()
            if (
                tuple(identity) != _LEGACY_OWNER
                or artifact_id != _LEGACY_ARTIFACT_ID
                or digest != "sha256:" + _LEGACY_ZIP_SHA
                or producer != _LEGACY_SOURCE_SHA
                or hashlib.sha256(
                    canonical_state(envelope["state"])
                ).hexdigest()
                != _LEGACY_STATE_SHA
            ):
                raise AdapterError("Unreviewed legacy Google owner", "access")
            return upgrade_legacy_budget(envelope["state"])
        if (
            set(envelope)
            != common | {"producer_source_sha256", "state_sha256"}
            or envelope["producer_source_sha256"] != producer
            or envelope["state_sha256"]
            != hashlib.sha256(canonical_state(envelope["state"])).hexdigest()
        ):
            raise AdapterError("Untrusted modern Google envelope", "access")
        return validate_budget(envelope["state"])
    except (ValueError, TypeError, KeyError, zipfile.BadZipFile) as error:
        raise AdapterError("Invalid Google owned ZIP", "access") from error


def restore_family_artifact():
    global _JOURNAL_BOOTSTRAP_ALLOWED
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Mature Google family history is missing", "access")
    run_id, attempt, source, head = latest
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or len(artifacts) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(artifacts)
        or any(not isinstance(a, dict) for a in artifacts)
    ):
        raise AdapterError("Incomplete family artifact inventory", "access")
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest family artifact missing or expired", "access"
        )
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != head
        or not numeric(artifact.get("size_in_bytes"))
        or artifact["size_in_bytes"] > 32768
        or not isinstance(artifact.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"])
    ):
        raise AdapterError("Invalid family artifact binding", "access")
    incoming = download_inert_artifact(
        latest, artifact["id"], artifact["digest"]
    )
    if incoming["in_flight"]:
        raise AdapterError("Newest Google owner is unresolved", "access")
    incoming = hosted_floor(incoming, latest)
    _JOURNAL_BOOTSTRAP_ALLOWED = (
        _STATE_INITIALIZING
        and not _JOURNAL_SAVE_ATTEMPTED
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    with locked_budget():
        original = load_budget()
        if _JOURNAL_NEW and _JOURNAL_BOOTSTRAP_ALLOWED:
            # A current-time empty runner sentinel must not outrank the
            # authenticated prior source's completed logical or physical owner.
            combined = copy.deepcopy(incoming)
        else:
            combined = merge_budgets(original, incoming, time.time())
        combined["not_before"] = max(combined["not_before"], time.time() + 60)
        combined["observed_at"] = time.time()
        save_budget(combined)
    _RESTORED, _STATE_TRUSTED = True, True


def mark_pacing_artifact_ready(runner_parent, runner_root):
    """Preserve the existing readiness envelope with bound output IO."""
    journal_output_signal("true")


def retain_policy_review_state():
    """Capture an unresolved journal for human review, never for admission."""
    run_id, attempt = current_run_identity()
    head = os.environ["GITHUB_SHA"]
    if _HOSTED_IDENTITY != (run_id, attempt, SOURCE_ID, head):
        raise AdapterError("Unverified Google review owner", "access")
    with locked_budget():
        state = load_budget()
        if not state["in_flight"]:
            return
        journal_verify()
    root = os.environ["RUNNER_TEMP"]
    review_path = os.path.join(root, "google-pending-review.json")
    with tempfile.NamedTemporaryFile(dir=root, mode="w", encoding="utf-8") as file:
        json.dump({
            "purpose": "manual-review-only",
            "repository": "YouthOpps/data-pipeline",
            "source": SOURCE_ID,
            "head_sha": head,
            "run_id": run_id,
            "run_attempt": attempt,
            "pending_journal": state,
        }, file)
        file.flush()
        os.fsync(file.fileno())
        os.link(file.name, review_path)


def export_family_artifact():
    path = os.environ.get("GOOGLE_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
        journal_invalidate_export()
        if _STATE_TRUSTED and _PUBLISHER_GATE_FAILED and not _JOURNAL_FATAL:
            retain_policy_review_state()
        return

    def payload():
        run_id, attempt = current_run_identity()
        head = os.environ["GITHUB_SHA"]
        if _HOSTED_IDENTITY != (run_id, attempt, SOURCE_ID, head):
            raise AdapterError(
                "Unverified current Google export owner", "access"
            )
        with locked_budget():
            state = load_budget()
            if state["in_flight"]:
                raise AdapterError(
                    "Pending Google state cannot be exported", "access"
                )
        verify_legacy_claims()
        producer = hashlib.sha256(verified_source(head)).hexdigest()
        return {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "head_sha": head,
            "state": state,
            "producer_source_sha256": producer,
            "state_sha256": hashlib.sha256(canonical_state(state)).hexdigest(),
        }

    journal_export_envelope(path, payload, atomic=False, signal=True)


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _STARTUP_QUIET_UNTIL, _COLLECTION_FLOCK_GUARD, _COLLECTION_GUARD_ID
    try:
        if _JOURNAL_FATAL or _PUBLISHER_GATE_FAILED:
            raise AdapterError("Google publisher gate is sticky", "access")
        _COLLECTION_STARTED = time.time()
        # Pin the local generation through subsequent metadata callbacks.
        require_existing_local_state()
        workflow_mode_guard()
        claim_local_attempt()
        current_job_context()
        budget_file()
        if _COLLECTION_LOCK_FD is None:
            descriptor = journal_lifecycle_open(
                os.path.join(os.path.dirname(_STATE_PATH), "collection.lock"),
                _STATE_INITIALIZING,
            )
            try:
                journal_descriptor(
                    descriptor, _JOURNAL_ACQUIRED_IDS[descriptor]
                )
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                journal_bind_collection(
                    descriptor, _JOURNAL_ACQUIRED_IDS[descriptor]
                )
                _COLLECTION_FLOCK_GUARD = os.dup(descriptor)
                _COLLECTION_GUARD_ID = _JOURNAL_ACQUIRED_IDS[descriptor]
            except BaseException:
                journal_failure()
                with contextlib.suppress(BaseException):
                    journal_close_acquired(
                        descriptor, _JOURNAL_ACQUIRED_IDS.get(descriptor)
                    )
                raise
            _COLLECTION_LOCK_FD = descriptor
        restore_family_artifact()
        preserve_legacy_pacing()
        with locked_budget():
            state = load_budget()
            if state["in_flight"] or state["blocked"]:
                raise AdapterError(
                    "Restored Google state requires review", "access"
                )
            state["not_before"] = max(state["not_before"], time.time() + 60)
            state["observed_at"] = max(state["observed_at"], time.time())
            save_budget(state)
        _STARTUP_QUIET_UNTIL = time.monotonic() + 60
        _STATE_TRUSTED = True
    except BaseException:
        fail_policy()
        raise


def document_root(text, complete=True):
    reject_visible_challenge(text.encode(), {"Content-Type": "text/html"})
    if complete and not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I
    ):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    return root


def remove_expired_records(records, checked_at):
    """Filter a validated refresh before its atomic replacement publication."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Cleanup requires an aware check time", "validate")
    retained = []
    for record in records:
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
                    expired = deadline.tzinfo is not None and checked > deadline
            except (ValueError, TypeError):
                # Missing or ambiguous deadlines never imply expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, published=False, previous=False):
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

    validate_owned_records(records, published=published, previous=previous)


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


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "display_name": "Google",
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
        "last_success_at": (previous.get("last_success_at") if error else attempt),
        "last_checked_at": (previous.get("last_checked_at") if error else checked_at),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (
                f"Validated refresh retains {len(records)} "
                "reviewed Google programme information pages"
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
                    candidate_records = json.loads(candidate["files"]["data.json"])
                    candidate_metadata = json.loads(candidate["files"]["metadata.json"])
                    validate_records(
                        candidate_records, published=True, previous=True
                    )
                    validate_prior_metadata(candidate_metadata, candidate_records)
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


UNRESERVED = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


def normalize_octets(value):
    if not (
        isinstance(value, str)
        and len(value) <= 8192
        and (not any((ord(c) < 32 or ord(c) == 127 for c in value)))
    ):
        raise AdapterError("Invalid robots URI length or control character", "access")
    out = []
    i = 0
    while i < len(value):
        c = value[i]
        if c == "%":
            if not (
                i + 2 < len(value)
                and re.fullmatch("[a-fA-F0-9]{2}", value[i + 1 : i + 3])
            ):
                raise AdapterError(
                    "Malformed robots URI percent escape", "access"
                )
            n = int(value[i + 1 : i + 3], 16)
            out.append(
                chr(n) if n in UNRESERVED else "%" + value[i + 1 : i + 3].upper()
            )
            i += 3
        else:
            out.append(
                c
                if ord(c) < 128
                else "".join(("%%%02X" % b for b in c.encode("utf-8")))
            )
            i += 1
    return "".join(out)


def groups(body):
    if not (isinstance(body, bytes) and len(body) <= 65536):
        raise AdapterError("Robots policy exceeds byte bound or has invalid type", "access")
    lines = body.decode("utf-8-sig", "strict").splitlines()
    if not len(lines) <= 2048:
        raise AdapterError("Robots policy exceeds line bound", "access")
    result = []
    agents = []
    rules = []
    directives = False
    for line_number, line in enumerate(lines + [""], start=1):
        line = line.split("#", 1)[0].strip()
        if not line:
            if agents and directives:
                result.append((agents, rules))
                agents = []
                rules = []
                directives = False
            continue
        if ":" not in line:
            continue
        key, value = (v.strip() for v in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if directives:
                result.append((agents, rules))
                agents = []
                rules = []
                directives = False
            if not (value == "*" or re.fullmatch("[A-Za-z_-]+", value)):
                raise AdapterError(
                    f"Invalid robots user-agent token at line {line_number}", "access"
                )
            agents.append(value.lower())
            if not len(agents) <= 32:
                raise AdapterError(
                    f"Robots group exceeds user-agent bound at line {line_number}", "access"
                )
        elif key in {"allow", "disallow"} and agents:
            directives = True
            if not value:
                continue
            if not (
                value.startswith("/") and len(value) <= 2048 and (value.count("*") <= 8)
            ):
                raise AdapterError(
                    f"Invalid robots rule path, length or wildcard count at line {line_number}", "access"
                )
            try:
                value = normalize_octets(value)
            except AdapterError as error:
                raise AdapterError(f"{error} at line {line_number}", "access") from error
            rules.append((key == "allow", value))
            if not len(rules) <= 512:
                raise AdapterError(
                    f"Robots group exceeds rule bound at line {line_number}", "access"
                )
        elif agents:
            directives = True
    if not len(result) <= 100:
        raise AdapterError("Robots policy exceeds group bound", "access")
    return result


def rep_allowed(body, url, agent=None):
    if agent is None:
        agent = _USER_AGENT.split("/", 1)[0]
    p = urllib.parse.urlsplit(url)
    if not (
        p.scheme == "https"
        and p.hostname
        in {"www.google.com", "buildyourfuture.withgoogle.com", "policies.google.com"}
        and (not p.username)
        and (not p.password)
        and (not p.fragment)
        and (p.port in {None, 443})
    ):
        raise AdapterError("Invalid robots target origin or URI", "access")
    target = normalize_octets((p.path or "/") + ("?" + p.query if "?" in url else ""))
    best = -1
    chosen = []
    for agents, rules in groups(body):
        scores = [
            0 if a == "*" else len(a) for a in agents if a == "*" or a in agent.lower()
        ]
        if not scores:
            continue
        score = max(scores)
        if score > best:
            best = score
            chosen = list(rules)
        elif score == best:
            chosen.extend(rules)
    matches = []
    for permit, pattern in chosen:
        end = pattern.endswith("$")
        literal = pattern[:-1] if end else pattern
        expr = "^" + re.escape(literal).replace("\\*", ".*") + ("$" if end else "")
        if re.search(expr, target):
            matches.append(
                (
                    len(literal.replace("*", "").encode("utf-8"))
                    - 2 * len(re.findall("%[A-F0-9]{2}", literal)),
                    permit,
                    pattern,
                )
            )
    if not matches:
        return True
    longest = max((v[0] for v in matches))
    return any((v[1] for v in matches if v[0] == longest))


def check_robots(url):
    """Preserve Google's query/wildcard matcher with actual REP provenance."""
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.path == "/robots.txt":
        return
    if origin not in _ROBOTS:
        status, headers, body = request_bytes(
            origin + "/robots.txt",
            publisher=True,
            byte_limit=65536,
            robots=True,
        )
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
            body_digest = None
        elif status == 200:
            types = headers.get_all("Content-Type", [])
            if (
                len(types) != 1
                or types[0].split(";", 1)[0].strip().lower() != "text/plain"
            ):
                raise AdapterError(
                    "Google REP requires explicit text/plain", "access"
                )
            text = body.decode("utf-8-sig", "strict")
            if re.search(r"<\s*[A-Za-z!/?]", text):
                raise AdapterError("Markup is not Google REP", "access")
            try:
                groups(body)
            except AdapterError as error:
                digest = hashlib.sha256(body).hexdigest()
                raise AdapterError(
                    f"{origin}/robots.txt ({digest}): {error}", "access"
                ) from error
            _ROBOTS[origin] = body
            body_digest = hashlib.sha256(body).hexdigest()
        else:
            raise AdapterError("Google REP is unavailable", "access", status)
        _REP_PROVENANCE[origin] = {
            "status": status,
            "body_sha256": body_digest,
            "request_id": _LAST_RESPONSE_RECEIPT,
            "url": _LAST_RESPONSE_URL,
        }
    body = _ROBOTS[origin]
    if origin not in _REP_PROVENANCE:
        raise AdapterError("Google REP provenance is missing", "access")
    if body is not None:
        if not (
            rep_allowed(body, url)
            and rep_allowed(body, url, agent="YouthOpps")
        ):
            raise AdapterError("Google REP excludes required input", "access")
        for raw in body.decode("utf-8-sig", "strict").splitlines():
            if re.match(
                r"(?i)^(crawl-delay|request-rate)\s*:",
                raw.split("#", 1)[0].strip(),
            ):
                raise AdapterError(
                    "Google REP rate directive requires review", "access"
                )
    _REP_TARGETS[url] = copy.deepcopy(_REP_PROVENANCE[origin])


def preserve_legacy_pacing():
    """Read and retain all original research flocks without rewriting files."""
    global _LEGACY_ROOT_ABSENT
    root = os.path.join(tempfile.gettempdir(), "issue111-google-research")
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        _LEGACY_ROOT_ABSENT = True
        return
    info = os.fstat(parent)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(parent)
        raise AdapterError("Unsafe legacy Google parent", "access")
    _LEGACY_CLAIMS.append(
        (parent, root, (info.st_dev, info.st_ino), None, None)
    )
    incoming = []
    for host in LEGACY_HOSTS:
        name = host + ".budget.json"
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            _LEGACY_MISSING.append((parent, name))
            continue
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        _LEGACY_CLAIMS.append(
            (descriptor, os.path.join(root, name), identity, b"", None)
        )
        journal_descriptor(descriptor, identity)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        verify_lock_description(descriptor, identity)
        raw = os.pread(descriptor, 16385, 0)
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            len(raw) > 16384
            or journal_version(info) != journal_version(after)
            or (named.st_dev, named.st_ino) != identity
        ):
            raise AdapterError("Legacy Google generation changed", "access")
        state = json.loads(raw)
        if (
            type(state) is not dict
            or not {"starts", "until", "blocked"} <= set(state)
            or set(state) - {"starts", "until", "blocked", "interval"}
            or not numeric(state["until"])
            or state["until"] > 253402300799
            or type(state["blocked"]) is not bool
            or not numeric(state.get("interval", 6))
            or not 6 <= state.get("interval", 6) <= 253402300799
            or type(state["starts"]) is not list
            or len(state["starts"]) > 10
            or any(
                not numeric(v) or v > time.time() + 1 for v in state["starts"]
            )
            or state["starts"] != sorted(set(state["starts"]))
        ):
            raise AdapterError("Untrusted legacy Google constraints", "access")
        _LEGACY_CLAIMS[-1] = (
            descriptor,
            os.path.join(root, name),
            identity,
            raw,
            journal_version(after),
        )
        incoming.append(state)
    with locked_budget():
        state = load_budget()
        for prior in incoming:
            state["interval"] = max(
                state["interval"], prior.get("interval", 6)
            )
            state["not_before"] = max(state["not_before"], prior["until"])
            state["blocked"] = state["blocked"] or prior["blocked"]
            state["starts"] = sorted(set(state["starts"] + prior["starts"]))
        state["observed_at"] = max(state["observed_at"], time.time())
        save_budget(state)
    verify_legacy_claims()


class MaterialFacts(HTMLParser):
    """Bind full visible facts, literal links and all actual filter controls."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.text, self.links, self.controls, self.h1 = [], [], [], []
        self.capture = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if len(values) != len(attrs):
            raise AdapterError("Duplicate material HTML attributes", "parse")
        if tag in {"script", "style"}:
            self.skip += 1
        if tag == "h1":
            self.capture = True
        if not self.skip:
            if tag == "a" and values.get("href"):
                self.links.append(values["href"])
            selected = {
                key: value
                for key, value in values.items()
                if key in {"action", "method", "name", "value", "selected", "data-cy"}
                or key.startswith("data-glue-filter-")
            }
            if selected:
                self.controls.append((tag, sorted(selected.items())))

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip -= 1
            if self.skip < 0:
                raise AdapterError("Unbalanced material HTML", "parse")
        if tag == "h1":
            self.capture = False

    def handle_data(self, data):
        if not self.skip and data.strip():
            text = " ".join(data.split())
            self.text.append(text)
            if self.capture:
                self.h1.append(text)


def html_fingerprint(body):
    parser = MaterialFacts()
    parser.feed(body)
    parser.close()
    if parser.skip:
        raise AdapterError("Incomplete material HTML", "parse")
    facts = {"text": parser.text, "links": parser.links, "controls": parser.controls}
    digest = hashlib.sha256(
        json.dumps(facts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    return digest, parser.h1


def embedded_html(body):
    """Decode only the observed ds:0 first string, never execute scripts."""
    text = body.decode("utf-8", "strict")
    document_root(text, complete=False)
    pattern = r"AF_initDataCallback\(\{key: 'ds:0', hash: '[0-9]+', data:\["
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise AdapterError("Changed Google embedded document boundary", "parse")
    start = matches[0].end()
    try:
        value, used = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        raise AdapterError(
            "Invalid Google embedded document", "parse"
        ) from None
    if (
        not isinstance(value, str)
        or not value.lower().startswith("<!doctype html>")
        or not text[start + used :].startswith(",")
        or len(value.encode()) > 8388608
    ):
        raise AdapterError("Invalid Google embedded document envelope", "parse")
    document_root(value)
    return value


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    allowed = set(INPUTS) | {origin + "/robots.txt" for origin in ROBOTS_ORIGINS}
    if (
        value not in allowed
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.fragment
    ):
        raise AdapterError("Unreviewed Google public route", "access")
    return value


def read_input(url):
    with policy_operation():
        with inventory_page(url):
            return read_guarded_input(url, INPUTS[url])


def terms_fingerprint(headers, root):
    """Review the complete substantive terms independently of page chrome."""
    url = "https://policies.google.com/terms"
    document = one(nodes(root, "html"), "Google terms document")
    main = one([node for node in document.walk() if node.attrs.get("role") == "main"],
               "Google terms primary content")
    canonical = one([node for node in nodes(document, "link")
                     if node.attrs.get("rel", "").casefold() == "canonical"],
                    "Google terms canonical link")
    base = one(nodes(document, "base"), "Google terms link base")
    base_url = "https://policies.google.com/"
    if (document.duplicate_attrs or document.attrs.get("lang") != "en-US"
            or main.duplicate_attrs or canonical.duplicate_attrs
            or urllib.parse.urljoin(url, canonical.attrs.get("href", "")) != url + "?hl=en-US"
            or base.duplicate_attrs or base.attrs != {"href": base_url} or nodes(document, "iframe")):
        raise AdapterError("Google terms document or access boundary changed", "parse")

    policies = []
    for node in nodes(document, "meta"):
        purpose = node.attrs.get("name", "").casefold()
        if node.attrs.get("http-equiv", "").casefold() == "refresh":
            raise AdapterError("Google terms client redirect changed", "parse")
        if purpose == "robots" or purpose.endswith("bot"):
            if node.duplicate_attrs:
                raise AdapterError("Google terms crawler policy metadata is ambiguous", "parse")
            policies.append((purpose, " ".join(node.attrs.get("content", "").lower().split())))
    directives = (headers.get_all("X-Robots-Tag", []) if hasattr(headers, "get_all")
                  else [headers[k] for k in headers if k.casefold() == "x-robots-tag"])
    policies.extend(("x-robots-tag", " ".join(value.lower().split())) for value in directives)

    # Known website chrome is omitted from the main text comparison. It cannot
    # introduce an unreviewed direct automation or reuse notice outside main.
    def outside(node):
        if node is main or node.tag in {"script", "style"}:
            return ""
        return " ".join(outside(item) if isinstance(item, Node) else item for item in node.children)

    outer = " ".join(outside(document).split()).casefold()
    access = re.compile(r"\b(?:automat\w*|programmatic\w*|systematic\w*|scrap\w*|crawl\w*|"
                        r"spider\w*|extract\w*|machine[- ]readable|robots\.txt|reuse|"
                        r"republication|bulk collection|permission to access)\b")
    notices = [outer[max(0, match.start() - 80):match.end() + 80] for match in access.finditer(outer)]
    links = []
    for node in nodes(main, "a"):
        href = node.attrs.get("href", "")
        if href and not href.startswith("#"):
            if node.duplicate_attrs:
                raise AdapterError("Google terms substantive link is ambiguous", "parse")
            links.append((node.text(), urllib.parse.urljoin(base_url, href)))
    if not main.text() or not links:
        raise AdapterError("Incomplete Google substantive terms", "parse")
    forms = sorted((node.attrs.get("action", ""), node.attrs.get("method", "").casefold())
                   for node in nodes(document, "form"))
    payload = {"main_text": main.text(), "main_links": links, "forms": forms,
               "crawler_directives": sorted(policies), "outside_access_indicators": notices}
    fingerprint = hashlib.sha256(canonical_state(payload)).hexdigest()
    return fingerprint, [node.text() for node in nodes(main, "h1")]


def read_guarded_input(url, info):
    _, headers, body = fetch_public(url)
    if info["format"] == "embedded-html":
        digest, h1 = html_fingerprint(embedded_html(body))
    elif info["format"] == "html":
        text = body.decode("utf-8", "strict")
        root = document_root(
            text,
            complete=urllib.parse.urlsplit(url).hostname
            != "policies.google.com",
        )
        if url == SOURCE_URL:
            scripts = [
                urllib.parse.urljoin(url, node.attrs["src"])
                for node in nodes(root, "script")
                if node.attrs.get("src", "").startswith("main.")
            ]
            reviewed = [
                route
                for route, value in INPUTS.items()
                if value["format"] == "redirect-js"
            ]
            if scripts != reviewed:
                raise AdapterError(
                    "Changed original Google redirect script link", "parse"
                )
        digest, h1 = (
            terms_fingerprint(headers, root) if url == "https://policies.google.com/terms"
            else html_fingerprint(text)
        )
    elif info["format"] == "redirect-js":
        text = body.decode("utf-8", "strict")
        # Review literal redirect-script bytes without executing code.
        digest, h1 = hashlib.sha256(body).hexdigest(), []
        if 'const Q="' + WEBSITE_URL + '"' not in text:
            raise AdapterError(
                "Changed original Google redirect destination", "parse"
            )
    else:
        raise AdapterError("Invalid guarded Google input type", "parse")
    if digest != info["sha256"] or h1 != info["h1"]:
        if (
            url in INPUTS
            and info == INPUTS[url]
            and info["format"] == "embedded-html"
            and reviewable_embedded_material(body, h1, info["h1"])
        ):
            raise DataReviewDeferred(
                "Current Google programme/directory material requires review: "
                + url,
                "configured-material",
                url,
                _LAST_RESPONSE_RECEIPT,
            )
        raise AdapterError(
            "Reviewed Google material facts/frontier changed: " + url, "parse"
        )
    return digest


def read_pages():
    """Exhaust all twenty-three guarded inputs plus three fresh robots origins."""
    return {url: read_input(url) for url in INPUTS}


def profile_identity(profile):
    return hashlib.sha256((SOURCE_ID + "|" + profile["url"]).encode()).hexdigest()[:24]


def validate_owned_records(records, published=False, previous=False):
    if previous and not published:
        raise AdapterError("Prior material requires snapshot mode", "validate")
    profiles = {profile_identity(profile): profile for profile in PROFILES}
    identities = {record["id"] for record in records}
    if not identities <= set(profiles) or (
        not published and identities != set(profiles)
    ):
        raise AdapterError(
            "Incomplete Google programme identity partition", "validate"
        )
    temporal = {
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    for record in records:
        profile = profiles[record["id"]]
        expected = dict(profile["fields"], id=profile_identity(profile))
        material = {
            key: value for key, value in record.items() if key not in temporal
        }
        old_apm = (
            previous and profile["key"] == "apm"
            and material == {**expected, **_GOOGLE407_PREVIOUS_APM}
        )
        if material != expected and not old_apm:
            raise AdapterError(
                "Changed owned Google record material fields", "validate"
            )
        values = {
            key: datetime.fromisoformat(record[key].replace("Z", "+00:00"))
            for key in temporal
        }
        if not (
            values["created_at"] <= values["updated_at"] <= values["last_checked_at"]
            and values["first_seen_at"]
            <= values["last_seen_at"]
            <= values["last_checked_at"]
        ):
            raise AdapterError("Invalid owned Google chronology", "validate")


def parse_inventory(pages):
    """Normalize only the entire independently reviewed fifteen-page partition."""
    if set(pages) != set(INPUTS) or any(
        pages[url] != info["sha256"] for url, info in INPUTS.items()
    ):
        raise AdapterError("Incomplete guarded Google frontier", "parse")
    now = utc_now()
    records = []
    for profile in PROFILES:
        record = json.loads(json.dumps(profile["fields"]))
        record["id"] = profile_identity(profile)
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


LEGACY_HOSTS = [
    "buildyourfuture.withgoogle.com",
    "www.google.com",
    "policies.google.com",
    "summerofcode.withgoogle.com",
]
ROBOTS_ORIGINS = [
    "https://www.google.com",
    "https://buildyourfuture.withgoogle.com",
    "https://policies.google.com",
]
INPUTS = {
    "https://www.google.com/about/careers/applications/buildyourfuture/": {
        "format": "embedded-html",
        "sha256": "f00a00ff645287bdb255e11c574e66c6893bbc391f3ec863e139e2be62e15400",
        "h1": ["Build " "your " "future " "with " "Google"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/": {
        "format": "embedded-html",
        "sha256": "db3587e8041ae004e2bd5bc78fbb928c06064a2bcb4bf44b9400466a45087244",
        "h1": ["Scholarships"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/": {
        "format": "embedded-html",
        "sha256": "f4c4bef3401b0ef6d41f2fb2836833e88c7883e0a432f48f4f3ede402052c4fc",
        "h1": ["Programs"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/apprenticeships/": {
        "format": "embedded-html",
        "sha256": "bd8b7940591ed28d649df8d632c6d1e7d9a365f80434ec987f5bae273f8bc395",
        "h1": ["Apprenticeships"],
    },
    "https://www.google.com/about/careers/applications/internships": {
        "format": "embedded-html",
        "sha256": "52f0eda94acb9fd1b04680783185c8cf31cec3266e6830bf1e594ed403f17eb1",
        "h1": ["Internships " "at Google"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/googleireland/": {
        "format": "embedded-html",
        "sha256": "0a9162190651cd2919e848014cf5839eb4831585689aa4a6cb509714d802c5ac",
        "h1": [
            "Scholarship: "
            "Google "
            "Ireland "
            "for "
            "women "
            "in "
            "computer "
            "science"
        ],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/summer-of-code/": {
        "format": "embedded-html",
        "sha256": "eb3b55cf667e7a51b31c86201b3f6584ca2da302c7f7a8e9eef01616115bdec4",
        "h1": ["Google " "Summer " "of " "Code"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/googlestudentambassador": {
        "format": "embedded-html",
        "sha256": "2a7e4593031da7767bc365921bc2398d7594dabfe3598eecc90e99e98f3cba5a",
        "h1": ["US " "Google " "Student " "Ambassador " "Program"],
    },
    "https://www.google.com/about/careers/applications/early-career": {
        "format": "embedded-html",
        "sha256": "46876faa1a23697f18a910a154690e4cbbcfe8bf83b2352166f25579e5c837c5",
        "h1": ["Early " "career at " "Google"],
    },
    "https://www.google.com/about/careers/applications/programs/apm/": {
        "format": "embedded-html",
        "sha256": (
            "e92a7a8834e747279a892b12245fdae1bcd5d7ea9704e4d965c6bbe1406064e3"
        ),
        "h1": ["Google " "Associate " "Product " "Manager " "Program"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/lsi/": {
        "format": "embedded-html",
        "sha256": "90acc1e0d4fe508e49c7cb138f4689889b4b9ec1f12eb171bab80499d2532151",
        "h1": ["US " "Legal " "Summer " "Institute"],
    },
    "https://www.google.com/about/careers/applications/students/engineering-and-technical-internships/": {
        "format": "embedded-html",
        "sha256": "73a8106d637c94e5a4362a98c53d99b46a9dc07ea405ac071fcb18d3e8238112",
        "h1": ["Engineering " "and " "technical " "internships"],
    },
    "https://www.google.com/about/careers/applications/research-internships/": {
        "format": "embedded-html",
        "sha256": "7c7a925b2a60f54eaeb6201ca78a9b6ebc59bc1957f5383575557c85fdf64699",
        "h1": ["Research " "internships"],
    },
    "https://www.google.com/about/careers/applications/students/business-internships/": {
        "format": "embedded-html",
        "sha256": "fe573aa29a9a7280bad615ea8ea379811a45a3a3d9f872db808f554b109be8a4",
        "h1": ["Business " "internships"],
    },
    "https://www.google.com/about/careers/applications/programs/aicareercatalyst": {
        "format": "embedded-html",
        "sha256": "c5696a1a3c6e3c40668f5e1c769e350605442fcb19d357ba1a0eb2ebef06c259",
        "h1": ["Google " "AI " "Career " "Catalyst " "Program"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/americas-sales-associate/": {
        "format": "embedded-html",
        "sha256": "f2ff9ff457738699fa7b2d116a4544f41bcfb3cbda2c86011f27d09586726cf2",
        "h1": ["Americas " "Sales " "Associate " "Program"],
    },
    "https://www.google.com/about/careers/applications/programs/spark/": {
        "format": "embedded-html",
        "sha256": "1a349c3be3a82b615036ec3fab00a27b9e08276e629de4d36c96805e7a9bc583",
        "h1": ["Your " "Future " "Starts " "Here"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/genesis": {
        "format": "embedded-html",
        "sha256": "19d20a4c26dc96245e022572f2e98a4143326a48823508d3ff339537c2702bf0",
        "h1": ["Genesis " "Finance " "Program"],
    },
    "https://www.google.com/about/careers/applications/buildyourfuture/programs/emea-lsi/": {
        "format": "embedded-html",
        "sha256": "cfd25d4c1993c424fa56944b8bfb636c99b5d6e824a437705f0667059a5f3586",
        "h1": ["EMEA " "Legal " "Summer " "Institute " "(LSI)"],
    },
    "https://www.google.com/about/careers/applications/programs/apmm/": {
        "format": "embedded-html",
        "sha256": "a2c440cefe084f9762a3c36a5864ab13d8b083ccfff6d65a60afd3e280410284",
        "h1": ["Connect " "users to " "the " "magic"],
    },
    "https://buildyourfuture.withgoogle.com/": {
        "format": "html",
        "sha256": "74cd3fb423f468938fbbb5a31693d0698bc171bb7213f3b9ed5994794e0b16f3",
        "h1": [],
    },
    'https://policies.google.com/terms': {
        "format": "html",
        "sha256": '05700da7039f8e03d5936ce1c61b07f75786886c8b051a2c560624848d6c7cc5',
        "h1": ['', '', 'Privacy & Terms', 'Google Terms of Service', 'Terms', 'Your relationship with Google', 'Using Google services', 'Content in Google services', 'Software in Google services', 'In case of problems or disagreements', 'About these terms', 'Definitions'],
    },
    "https://buildyourfuture.withgoogle.com/main.9ca0c65bf5ce4c16.js": {
        "format": "redirect-js",
        "sha256": "5a9c216c54299e5a67467f5fa8584e2fc5ce14cb5177a85a9aef0a2e514e1c7d",
        "h1": [],
    },
}
PROFILES = [
    {
        "key": "gsoc",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/summer-of-code/",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/summer-of-code/"
        ],
        "fields": {
            "title": "Google Summer of Code",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/summer-of-code/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Google Summer of Code pairs students or beginner open-source "
            "contributors aged over 18 with mentors for 12+ week coding "
            "projects. Applicants need work eligibility where they live, "
            "non-embargoed residence and at most two prior acceptances. "
            "Passed evaluations earn a stipend; amounts are not specified "
            "here. The 2026 application window was March 16-31. The page "
            "also labels parts of its timeline 2025; check linked official "
            "rules for additional conditions.",
            "tags": [],
            "location": None,
            "deadline": "2026-03-31",
            "language": "en",
            "summary_language": "en",
            "category": "training",
            "categories": ["training", "grants"],
            "kind": "programme-overview",
            "host_countries": [],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "expired",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/summer-of-code/",
                ],
            },
        },
    },
    {
        "key": "student-ambassador",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/googlestudentambassador",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/googlestudentambassador",
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/",
        ],
        "fields": {
            "title": "US Google Student Ambassador Program",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/googlestudentambassador",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "This paid, remote and campus-based programme recruits U.S. "
            "college students to connect peers with Google AI products, "
            "host campus events and share product insights. Active campus "
            "involvement, interest in AI and experience organising events "
            "or social content strengthen applications. Recruitment "
            "follows the academic calendar, and the page says applications "
            "are closed. The programme index mistakenly repeats a Summer "
            "of Code description; the individual page supplies these "
            "facts.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs"],
            "kind": "programme-overview",
            "host_countries": ["US"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "expired",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/googlestudentambassador",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/",
                ],
            },
        },
    },
    {
        "key": "ai-career-catalyst",
        "url": "https://www.google.com/about/careers/applications/programs/aicareercatalyst",
        "inputs": [
            "https://www.google.com/about/careers/applications/programs/aicareercatalyst"
        ],
        "fields": {
            "title": "Google AI Career Catalyst Program",
            "url": "https://www.google.com/about/careers/applications/programs/aicareercatalyst",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "The AI Career Catalyst programme offers early-career software "
            "engineers 13 months of development in AI and infrastructure: "
            "three four-month rotations, senior mentorship and a final "
            "full-time team placement matched to business needs. It is "
            "based in Sunnyvale, with possible other U.S. rotations. The "
            "page lists a June 2027 start but says the 2026 cohort is "
            "closed and the 2027 intake will open this summer; current "
            "intake status and detailed qualifications need checking.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs"],
            "kind": "programme-overview",
            "host_countries": ["US"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/programs/aicareercatalyst",
                ],
            },
        },
    },
    {
        "key": "americas-sales-associate",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/americas-sales-associate/",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/americas-sales-associate/"
        ],
        "fields": {
            "title": "Americas Sales Associate Program",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/americas-sales-associate/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "This U.S. sales programme has quarterly starts and two years "
            "of rotations, with mentors and routes into account-management "
            "or analytical roles. Both tracks require a degree or "
            "equivalent practical experience and one year of relevant "
            "sales/advertising or analytical experience. Track-specific "
            "communication, client, Google Ads, data-visualisation or "
            "spreadsheet/SQL skills also apply. The page links two "
            "applications and says they are open; individual vacancy "
            "conditions and closing dates were not collected.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs"],
            "kind": "programme-overview",
            "host_countries": ["US"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/americas-sales-associate/",
                ],
            },
        },
    },
    {
        "key": "apm",
        "url": "https://www.google.com/about/careers/applications/programs/apm/",
        "inputs": ["https://www.google.com/about/careers/applications/programs/apm/"],
        "fields": {
            "title": "Google Associate Product Manager Program",
            "url": "https://www.google.com/about/careers/applications/programs/apm/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "APM gives new and recent graduates two one-year "
            "product-management rotations, mentorship and training; summer "
            "interns join one team for 12 weeks and must graduate within 12 "
            "months after the internship. Technical understanding, product "
            "sense, analysis and communication matter. U.S. internship and "
            "graduate applications are closed for 2027 cohorts. The page "
            "lists London internships October 5-November 12 and Bengaluru "
            "graduate roles October 30-November 24, without explicit years. "
            "2027 London/EMEA graduate intake details remain unannounced. "
            "Check each role's eligibility and timing.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs", "internships"],
            "kind": "programme-overview",
            "host_countries": ['US', 'GB', 'DE', 'CH', 'JP', 'IN'],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/programs/apm/",
                ],
            },
        },
    },
    {
        "key": "spark",
        "url": "https://www.google.com/about/careers/applications/programs/spark/",
        "inputs": ["https://www.google.com/about/careers/applications/programs/spark/"],
        "fields": {
            "title": "Your Future Starts Here",
            "url": "https://www.google.com/about/careers/applications/programs/spark/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Spark at Google develops early-career digital-marketing and "
            "sales staff through three months of initial training, ongoing "
            "learning and mentorship while advising small-business "
            "clients. Prior sales experience is not required; specific job "
            "qualifications still apply. Locations include New York, the "
            "Bay Area and Mexico City, generally with three office days "
            "and two flexible days weekly. U.S. applicants need U.S. work "
            "authorisation. Apply only when a Spark role is posted; "
            "current vacancies and deadlines are unknown.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs"],
            "kind": "programme-overview",
            "host_countries": ["US", "MX"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/programs/spark/",
                ],
            },
        },
    },
    {
        "key": "genesis-finance",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/genesis",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/genesis"
        ],
        "fields": {
            "title": "Genesis Finance Program",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/genesis",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Genesis Finance develops early-career finance staff through "
            "four six-month rotations over two years, training, mentors "
            "and a peer cohort. No specific experience is universally "
            "required; individual job qualifications apply, and applicants "
            "must have work authorisation in the country chosen. The page "
            "lists a summer 2026 start and fall 2025 closing without a "
            "day, alongside usual regional recruitment seasons. Current "
            "vacancies are not verified; the location list contains "
            "duplicate Ireland wording.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs"],
            "kind": "programme-overview",
            "host_countries": ["US", "IE", "SG"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/genesis",
                ],
            },
        },
    },
    {
        "key": "us-lsi",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/lsi/",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/lsi/"
        ],
        "fields": {
            "title": "US Legal Summer Institute",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/lsi/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "US LSI combines paid partner-law-firm summer work with a "
            "Google campus week, not an in-house Google internship. Rising "
            "2L/3E students need U.S. work authorisation, an ABA-approved "
            "law school, transcripts, résumé and essays, and must attend "
            "the whole Google week. The page says 2027 applications "
            "closed, yet its 2028 calendar lists November 30, 2026-January "
            "11, 2027 applications. Its class-of-2028 and spring-2029 "
            "eligibility wording conflicts; verify cohort eligibility "
            "before applying.",
            "tags": [],
            "location": None,
            "deadline": "2027-01-11",
            "language": "en",
            "summary_language": "en",
            "category": "internships",
            "categories": ["internships"],
            "kind": "programme-overview",
            "host_countries": ["US"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/lsi/",
                ],
            },
        },
    },
    {
        "key": "emea-lsi",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/emea-lsi/",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/programs/emea-lsi/",
            "https://www.google.com/about/careers/applications/early-career",
        ],
        "fields": {
            "title": "EMEA Legal Summer Institute (LSI)",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/programs/emea-lsi/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "EMEA LSI combines paid partner-law-firm experience with a "
            "Google learning week and optional six-month legal mentorship. "
            "Law students, apprentices or recent graduates need work "
            "authorisation in the placement country, academic excellence, "
            "a résumé, English and the local language. Applications are "
            "closed; the Google week was September 7-11, 2026. Placement "
            "timing varies, and travel or accommodation is not covered. "
            "The detail lists 23 countries although the programme index "
            "says 19.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "internships",
            "categories": ["internships"],
            "kind": "programme-overview",
            "host_countries": [
                "AT",
                "BE",
                "EG",
                "EE",
                "FR",
                "DE",
                "GB",
                "IE",
                "IL",
                "IT",
                "KE",
                "LV",
                "LT",
                "NL",
                "PL",
                "PT",
                "RO",
                "SA",
                "ES",
                "SE",
                "CH",
                "TR",
                "AE",
            ],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "expired",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/programs/emea-lsi/",
                    "https://www.google.com/about/careers/applications/early-career",
                ],
            },
        },
    },
    {
        "key": "apmm",
        "url": "https://www.google.com/about/careers/applications/programs/apmm/",
        "inputs": [
            "https://www.google.com/about/careers/applications/programs/apmm/",
            "https://www.google.com/about/careers/applications/early-career",
        ],
        "fields": {
            "title": "Connect users to the magic",
            "url": "https://www.google.com/about/careers/applications/programs/apmm/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "The Associate Product Marketing Manager programme lasts two "
            "years according to the index and offers two roles, training "
            "and mentors. Applicants may be students or have a few years "
            "of experience; no single prior experience is universally "
            "required. Technology and marketing interest, leadership, "
            "problem solving and creative analysis matter. Participants "
            "must start in their home country, and visa sponsorship is "
            "unavailable. Roles open according to business demand; "
            "detailed job requirements and deadlines vary.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs"],
            "kind": "programme-overview",
            "host_countries": [],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/programs/apmm/",
                    "https://www.google.com/about/careers/applications/early-career",
                ],
            },
        },
    },
    {
        "key": "google-ireland",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/googleireland/",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/googleireland/",
            "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/",
        ],
        "fields": {
            "title": "Scholarship: Google Ireland for women in computer science",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/googleireland/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Google Ireland awards selected undergraduate computer-science "
            "students EUR 5,000 annually for two consecutive years, based "
            "on leadership and academic background. Google states the "
            "application deadline was October 5, 2026 and encourages women "
            "to apply. Complete minimum eligibility is delegated to IIE "
            "and is not verified by this collector; follow the "
            "administrator link before relying on eligibility. No Irish "
            "citizenship or women-only requirement is inferred from the "
            "programme name.",
            "tags": [],
            "location": None,
            "deadline": "2026-10-05",
            "language": "en",
            "summary_language": "en",
            "category": "scholarships",
            "categories": ["scholarships"],
            "kind": "programme-overview",
            "host_countries": ["IE"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "expired",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/googleireland/",
                    "https://www.google.com/about/careers/applications/buildyourfuture/scholarships/",
                ],
            },
        },
    },
    {
        "key": "apprenticeships",
        "url": "https://www.google.com/about/careers/applications/buildyourfuture/apprenticeships/",
        "inputs": [
            "https://www.google.com/about/careers/applications/buildyourfuture/apprenticeships/"
        ],
        "fields": {
            "title": "Apprenticeships",
            "url": "https://www.google.com/about/careers/applications/buildyourfuture/apprenticeships/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Google apprenticeships offer paid work, training and "
            "benefits. Applicants need local work rights and "
            "English/local-language fluency; generally age over 18, with a "
            "Swiss exception. U.S. programmes take 40 hours weekly, "
            "exclude same-field bachelor's students/graduates and require "
            "under 12 months' experience (under six with an associate "
            "degree). This overview covers 20 tracks; individual vacancies "
            "are not collected. Several cohort dates are stale or closed. "
            "India Software Application Development says 12 months in the "
            "heading and 24 in the text.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "jobs",
            "categories": ["jobs", "training"],
            "kind": "programme-overview",
            "host_countries": ["US", "FR", "IE", "CH", "GB", "IN", "BR"],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/buildyourfuture/apprenticeships/",
                ],
            },
        },
    },
    {
        "key": "engineering-internships",
        "url": "https://www.google.com/about/careers/applications/students/engineering-and-technical-internships/",
        "inputs": [
            "https://www.google.com/about/careers/applications/students/engineering-and-technical-internships/",
            "https://www.google.com/about/careers/applications/internships",
        ],
        "fields": {
            "title": "Engineering and technical internships",
            "url": "https://www.google.com/about/careers/applications/students/engineering-and-technical-internships/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Google describes software, user-experience, "
            "product-management, mechanical and hardware internships, not "
            "an exhaustive vacancy list. Software roles serve "
            "undergraduate and graduate or PhD students; mechanical roles "
            "require full-time degree study in mechanical engineering or a "
            "related field, and hardware roles degree study in electrical "
            "engineering with system-design emphasis. Dates and "
            "qualifications vary by role and location. Google offers "
            "interns compensation, benefits and mentorship; specific "
            "vacancies were not collected.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "internships",
            "categories": ["internships"],
            "kind": "programme-overview",
            "host_countries": [],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/students/engineering-and-technical-internships/",
                    "https://www.google.com/about/careers/applications/internships",
                ],
            },
        },
    },
    {
        "key": "research-internships",
        "url": "https://www.google.com/about/careers/applications/research-internships/",
        "inputs": [
            "https://www.google.com/about/careers/applications/research-internships/",
            "https://www.google.com/about/careers/applications/internships",
        ],
        "fields": {
            "title": "Research internships",
            "url": "https://www.google.com/about/careers/applications/research-internships/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Google research internships let final-year PhD students work "
            "with researchers on computing and scientific projects. A "
            "separate Student Researcher programme hires students into "
            "projects aligned to company research priorities, with varying "
            "locations and durations. Google offers interns compensation, "
            "benefits and mentorship. These are programme descriptions; "
            "individual project qualifications, current vacancies and "
            "deadlines were not collected. The linked student-engagement "
            "site supplies additional information.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "internships",
            "categories": ["internships"],
            "kind": "programme-overview",
            "host_countries": [],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/research-internships/",
                    "https://www.google.com/about/careers/applications/internships",
                ],
            },
        },
    },
    {
        "key": "business-internships",
        "url": "https://www.google.com/about/careers/applications/students/business-internships/",
        "inputs": [
            "https://www.google.com/about/careers/applications/students/business-internships/",
            "https://www.google.com/about/careers/applications/internships",
        ],
        "fields": {
            "title": "Business internships",
            "url": "https://www.google.com/about/careers/applications/students/business-internships/",
            "source": "google",
            "source_url": "https://buildyourfuture.withgoogle.com/",
            "published_at": None,
            "summary": "Google describes business internships for undergraduate and "
            "graduate students, MBA internships for currently enrolled MBA "
            "students and legal internships for law students in selected "
            "countries. Requirements and application dates vary by role "
            "and location; MBA recruitment generally opens in "
            "September-October and legal recruitment in October, without a "
            "cohort year here. Google offers interns compensation, "
            "benefits and mentorship. The separate four-years-experience "
            "career-break gCareer route is excluded from this student "
            "scope.",
            "tags": [],
            "location": None,
            "deadline": None,
            "language": "en",
            "summary_language": "en",
            "category": "internships",
            "categories": ["internships"],
            "kind": "programme-overview",
            "host_countries": [],
            "eligible_countries": [],
            "publisher_country": "US",
            "status": "unknown",
            "classification": {
                "method": "editorial-review",
                "status": "classified",
                "evidence": [
                    "Google official programme information; "
                    "original factual summary",
                    "Bounded programmeoverview; current "
                    "global vacancies not collected",
                    "https://www.google.com/about/careers/applications/students/business-internships/",
                    "https://www.google.com/about/careers/applications/internships",
                ],
            },
        },
    },
]


# Inventory hubs retain the original INPUTS order; details use physical pacing.
INVENTORY_INPUTS = frozenset(
    tuple(INPUTS)[index] for index in (0, 1, 2, 3, 4, 8)
)


_JOURNAL_FD = None
_JOURNAL_ID = None
_JOURNAL_VERSION = None
_JOURNAL_PARENT_FD = None
_JOURNAL_PARENT_ID = None
_JOURNAL_PATH = None
_JOURNAL_LOCK_FD = None
_JOURNAL_LOCK_ID = None
_JOURNAL_COLLECTION_ID = None
_JOURNAL_DEPTH = 0
_JOURNAL_FATAL = False
_JOURNAL_DIRTY = False
_JOURNAL_SAVE_ATTEMPTED = False
_JOURNAL_BOOTSTRAP_ALLOWED = False
_JOURNAL_NEW = False
_JOURNAL_FAILED_STATE = None


def journal_version(info):
    """Identify observable file generations without adopting a new inode."""
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def journal_descriptor(descriptor, identity, directory=False, unlinked=False):
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or directory
        and not stat.S_ISDIR(info.st_mode)
        or not directory
        and (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink not in ((0, 1) if unlinked else (1,))
        )
    ):
        raise AdapterError("Original publisher descriptor changed", "access")
    return info


def journal_parent():
    verify_state_directory()
    journal_descriptor(_JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, True)
    named = os.stat(os.path.dirname(_JOURNAL_PATH), follow_symlinks=False)
    if (named.st_dev, named.st_ino) != _JOURNAL_PARENT_ID:
        raise AdapterError(
            "Original Google journal directory changed", "access"
        )


def journal_poison():
    """Invalidate the held original only; preserve unrelated generations."""
    if (
        _JOURNAL_FD is None
        or not _JOURNAL_AUTHORITY
        or _JOURNAL_PRESERVE_UNKNOWN
    ):
        return
    verify_lock_description(_JOURNAL_GUARD_FD, _JOURNAL_GUARD_ID)
    journal_parent()
    info = journal_descriptor(_JOURNAL_FD, _JOURNAL_ID, unlinked=True)
    if not _JOURNAL_DIRTY and journal_version(info) != _JOURNAL_VERSION:
        raise AdapterError("Stronger publisher generation preserved", "access")
    os.ftruncate(_JOURNAL_FD, 0)
    os.fsync(_JOURNAL_FD)


def journal_failure(candidate=None):
    """Latch every journal authority or persistence failure across reentry."""
    global _JOURNAL_FATAL, _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    global _JOURNAL_FAILED_STATE
    _JOURNAL_FATAL = _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False
    if candidate is not None:
        with contextlib.suppress(BaseException):
            _JOURNAL_FAILED_STATE = copy.deepcopy(candidate)
    with contextlib.suppress(BaseException):
        journal_poison()


def journal_close_descriptor(descriptor, identity, directory=False):
    """Release our acquisition, preserving a primary error and foreign FD."""
    primary = sys.exc_info()[1]
    try:
        journal_descriptor(descriptor, identity, directory, unlinked=True)
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def journal_verify(generation=True):
    try:
        if _JOURNAL_FATAL:
            raise AdapterError("Publisher journal gate is sticky", "access")
        journal_parent()
        if _JOURNAL_AUTHORITY:
            verify_journal_flocks()
        info = journal_descriptor(_JOURNAL_FD, _JOURNAL_ID)
        named = os.stat(
            os.path.basename(_JOURNAL_PATH),
            dir_fd=_JOURNAL_PARENT_FD,
            follow_symlinks=False,
        )
        if (named.st_dev, named.st_ino) != _JOURNAL_ID or (
            generation and journal_version(info) != _JOURNAL_VERSION
        ):
            raise AdapterError(
                "Publisher journal generation changed", "access"
            )
        if _JOURNAL_LOCK_FD is not None:
            journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
            if _JOURNAL_LOCK_ID != _JOURNAL_ID:
                lock = os.stat(
                    "state.lock",
                    dir_fd=_JOURNAL_PARENT_FD,
                    follow_symlinks=False,
                )
                if (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID:
                    raise AdapterError(
                        "Publisher state lock changed", "access"
                    )
        return info
    except BaseException:
        journal_failure()
        raise


def journal_acquire():
    """Retain only a fully verified original RW inode and lock acquisition."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_PATH
    global _JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, _JOURNAL_NEW
    global _JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID
    if _JOURNAL_FATAL:
        raise AdapterError("Publisher journal gate is sticky", "access")
    if _JOURNAL_FD is not None:
        journal_verify()
        return
    path = journal_file_path()
    _JOURNAL_PATH = path
    parent, parent_id = journal_open_directory(os.path.dirname(path))
    _JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID = parent, parent_id
    try:
        expected = None
        try:
            descriptor, identity = journal_open_original(
                parent, os.path.basename(path), os.O_RDWR, expected
            )
        except FileNotFoundError:
            if not (
                _STATE_INITIALIZING
                and _JOURNAL_BOOTSTRAP_ALLOWED
                and not _JOURNAL_SAVE_ATTEMPTED
            ):
                raise AdapterError(
                    "Mature publisher state is missing", "access"
                )
            descriptor, identity = journal_open_original(
                parent,
                os.path.basename(path),
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
            )
            _JOURNAL_NEW = True
        info = journal_descriptor(descriptor, identity)
        _JOURNAL_FD, _JOURNAL_ID = descriptor, identity
        _JOURNAL_VERSION = journal_version(info)
        journal_verify()
        flags = os.O_RDWR
        if _STATE_INITIALIZING and _JOURNAL_BOOTSTRAP_ALLOWED:
            flags |= os.O_CREAT
        lock, lock_id = journal_open_original(parent, "state.lock", flags)
        _JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID = lock, lock_id
        journal_verify()
    except BaseException:
        journal_failure()
        raise


@contextlib.contextmanager
def journal_locked():
    global _JOURNAL_DEPTH, _JOURNAL_AUTHORITY
    global _JOURNAL_GUARD_FD, _JOURNAL_GUARD_ID
    journal_acquire()
    outer = _JOURNAL_DEPTH == 0
    if outer:
        try:
            fcntl.flock(_JOURNAL_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _JOURNAL_GUARD_FD = os.dup(_JOURNAL_LOCK_FD)
            _JOURNAL_GUARD_ID = _JOURNAL_LOCK_ID
            _JOURNAL_AUTHORITY = True
            verify_journal_flocks()
        except BaseException:
            journal_failure()
            raise
    _JOURNAL_DEPTH += 1
    try:
        journal_verify()
        yield
    finally:
        primary = sys.exc_info()[1]
        _JOURNAL_DEPTH -= 1
        if outer:
            try:
                journal_verify()
                verify_journal_flocks()
                fcntl.flock(_JOURNAL_LOCK_FD, fcntl.LOCK_UN)
                _JOURNAL_AUTHORITY = False
                journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
                journal_close_descriptor(_JOURNAL_GUARD_FD, _JOURNAL_GUARD_ID)
                _JOURNAL_GUARD_FD = None
            except BaseException:
                journal_failure()
                if primary is None:
                    raise
            finally:
                _JOURNAL_AUTHORITY = False


def journal_read():
    journal_acquire()
    before = journal_verify()
    try:
        raw = os.pread(_JOURNAL_FD, 16385, 0)
        after = journal_verify()
        if (
            journal_version(before) != journal_version(after)
            or len(raw) > 16384
        ):
            raise AdapterError(
                "Publisher journal changed during read", "access"
            )
        return raw
    except BaseException:
        journal_failure()
        raise


def journal_write(state):
    global _JOURNAL_VERSION, _JOURNAL_DIRTY, _JOURNAL_SAVE_ATTEMPTED
    global _JOURNAL_NEW, _STATE_INITIALIZING
    try:
        validate_budget(state)
        raw = json.dumps(state, separators=(",", ":")).encode()
        if not raw or len(raw) > 16384:
            raise AdapterError("Oversized publisher journal", "access")
        journal_verify()
        _JOURNAL_SAVE_ATTEMPTED = True
        _JOURNAL_DIRTY = True
        os.ftruncate(_JOURNAL_FD, 0)
        os.fsync(_JOURNAL_FD)
        offset = 0
        while offset < len(raw):
            journal_verify(False)
            written = os.pwrite(_JOURNAL_FD, raw[offset:], offset)
            journal_verify(False)
            if written <= 0:
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += written
        journal_verify(False)
        os.ftruncate(_JOURNAL_FD, len(raw))
        journal_verify(False)
        os.fsync(_JOURNAL_FD)
        before = journal_verify(False)
        actual = os.pread(_JOURNAL_FD, 16385, 0)
        after = journal_verify(False)
        if actual != raw or journal_version(before) != journal_version(after):
            _JOURNAL_DIRTY = False
            raise AdapterError("Publisher journal readback changed", "access")
        _JOURNAL_VERSION = journal_version(after)
        _JOURNAL_DIRTY = False
        _JOURNAL_NEW = False
        _STATE_INITIALIZING = False
    except BaseException:
        _JOURNAL_SAVE_ATTEMPTED = True
        journal_failure(state)
        raise


def journal_bind_collection(descriptor, expected=None):
    global _JOURNAL_COLLECTION_ID
    try:
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        if expected is not None and identity != expected:
            raise AdapterError(
                "Lifecycle FD changed after acquisition", "access"
            )
        _JOURNAL_COLLECTION_ID = expected if expected is not None else identity
        journal_descriptor(descriptor, _JOURNAL_COLLECTION_ID)
    except BaseException:
        journal_failure()
        raise


def journal_verify_collection():
    verify_lock_description(_COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID)
    verify_lock_description(_COLLECTION_FLOCK_GUARD, _COLLECTION_GUARD_ID)

    named = os.stat(
        os.path.join(os.path.dirname(_JOURNAL_PATH), "collection.lock"),
        follow_symlinks=False,
    )
    if (named.st_dev, named.st_ino) != _JOURNAL_COLLECTION_ID:
        raise AdapterError("Original lifecycle lock changed", "access")


def journal_before_wire():
    global _LOCAL_FIRST_WIRE_VERIFIED
    try:
        if (
            _PUBLISHER_GATE_FAILED
            or _JOURNAL_FATAL
            or not _ACTIVE_FLIGHT_ID
            or not _ACTIVE_REQUEST_ID
            or _STARTUP_QUIET_UNTIL is None
            or time.monotonic() < _STARTUP_QUIET_UNTIL
        ):
            raise AdapterError(
                "Google physical admission gate is closed", "access"
            )
        journal_verify()
        journal_verify_collection()
        verify_legacy_claims()
        verify_local_claims()
        if not _LOCAL_FIRST_WIRE_VERIFIED:
            workflow_mode_guard()
            if os.environ.get("GITHUB_ACTIONS") == "true":
                owner = _HOSTED_IDENTITY
                current_job_context()
                if owner is None or _HOSTED_IDENTITY != owner:
                    raise AdapterError("Original Google producer changed during startup quiet", "access")
                workflow_mode_guard()
            journal_verify()
            journal_verify_collection()
            verify_legacy_claims()
            verify_local_claims()
            _LOCAL_FIRST_WIRE_VERIFIED = True
        journal_verify()
        journal_verify_collection()
        with locked_budget():
            state = load_budget()
            if (
                not state["in_flight"]
                or state["flight_id"] != _ACTIVE_FLIGHT_ID
                or state["request_id"] != _ACTIVE_REQUEST_ID
                or state["request_outcome"] != "reserved"
                or state["blocked"]
            ):
                raise AdapterError(
                    "Google physical reservation differs", "access"
                )
    except BaseException:
        fail_policy()
        raise


def journal_release():
    """Attempt independent releases; never clear the sticky failure gate."""
    global _JOURNAL_FD, _JOURNAL_LOCK_FD, _JOURNAL_PARENT_FD
    global _COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID
    global _JOURNAL_ID, _JOURNAL_LOCK_ID, _JOURNAL_PARENT_ID
    primary = sys.exc_info()[1]
    errors = []
    releases = [
        (_COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID, False),
        (_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID, False),
        (_JOURNAL_FD, _JOURNAL_ID, False),
        (_JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, True),
    ]
    seen = set()
    for descriptor, identity, directory in releases:
        if descriptor is None or descriptor in seen:
            continue
        seen.add(descriptor)
        try:
            journal_close_descriptor(descriptor, identity, directory)
        except BaseException as error:
            errors.append(error)
    _COLLECTION_LOCK_FD = _JOURNAL_COLLECTION_ID = None
    _JOURNAL_FD = _JOURNAL_LOCK_FD = _JOURNAL_PARENT_FD = None
    _JOURNAL_ID = _JOURNAL_LOCK_ID = _JOURNAL_PARENT_ID = None
    if _JOURNAL_FATAL:
        with contextlib.suppress(BaseException):
            journal_invalidate_export()
    if errors and primary is None:
        raise errors[0]


class JournalLease:
    """Preserve the existing immediate-lock and explicit-close interface."""

    def __init__(self):
        self.context = journal_locked()
        self.context.__enter__()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *failure):
        if not self.closed:
            self.closed = True
            return self.context.__exit__(*failure)

    def close(self):
        self.__exit__(*sys.exc_info())

    def fileno(self):
        journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
        return _JOURNAL_LOCK_FD


def journal_file_path():
    budget_file()
    return _STATE_PATH


def journal_invalidate_export():
    path = os.environ.get("GOOGLE_PACING_ARTIFACT_PATH")
    if path:
        journal_invalidate_owned_output(path)


def output_identity(descriptor, expected, directory=False, unlinked=False):
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != expected
        or info.st_uid != os.getuid()
        or directory
        and (
            not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o022
            or info.st_mode & 0o700 != 0o700
        )
        or not directory
        and (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or info.st_nlink not in ((0, 1) if unlinked else (1,))
        )
    ):
        raise AdapterError("Original owned output changed", "access")
    return info


def output_close(descriptor, expected, directory=False):
    primary = sys.exc_info()[1]
    try:
        output_identity(descriptor, expected, directory, True)
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def output_parent(parent, root, expected):
    output_identity(parent, expected, True)
    current = os.stat(root, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != expected:
        raise AdapterError("Owned output directory changed", "access")


def output_poison(descriptor, expected):
    info = output_identity(descriptor, expected, unlinked=True)
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise AdapterError("Unsafe owned artifact permissions", "access")
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)


def output_open_parent(path):
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Explicit runner temporary root required", "access")
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    named_root = os.stat(root, follow_symlinks=False)
    expected_root = (named_root.st_dev, named_root.st_ino)
    parent = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    info = os.fstat(parent)
    identity = expected_root
    try:
        output_parent(parent, root, identity)
    except BaseException:
        with contextlib.suppress(BaseException):
            journal_close_acquired(parent, identity)
        raise
    return parent, root, identity


def journal_invalidate_owned_output(path):
    """Invalidate a verified owned inode before fallible cleanup/signals."""
    parent, root, parent_id = output_open_parent(path)
    descriptor = None
    identity = None
    try:
        try:
            named_before = os.stat(
                os.path.basename(path), dir_fd=parent, follow_symlinks=False
            )
            identity = (named_before.st_dev, named_before.st_ino)
            descriptor = os.open(
                os.path.basename(path),
                os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            return
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            raise AdapterError("Owned invalidation FD changed", "access")
        output_identity(descriptor, identity)
        current = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Owned output identity changed", "access")
        output_parent(parent, root, parent_id)
        output_poison(descriptor, identity)
        output_parent(parent, root, parent_id)
        current = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(os.path.basename(path), dir_fd=parent)
            os.fsync(parent)
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, expected, directory in (
            (descriptor, identity, False),
            (parent, parent_id, True),
        ):
            if fd is not None:
                try:
                    output_close(fd, expected, directory)
                except BaseException as error:
                    errors.append(error)
        if errors and primary is None:
            raise errors[0]


def journal_output_signal(value):
    """Bind fresh step output and all nested descriptors through close."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Fresh workflow output required", "access")
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    root = os.path.realpath(configured)
    if not configured or not os.path.isabs(path):
        raise AdapterError("Unsafe workflow output", "access")
    relative = os.path.relpath(path, root)
    parts = relative.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        raise AdapterError("Workflow output outside runner root", "access")
    parent, root, root_id = output_open_parent(
        os.path.join(root, ".output-binding")
    )
    handles = [(parent, root_id, True, None, None)]
    try:
        output_parent(parent, root, root_id)
        for name in parts[:-1]:
            named_before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            expected_before = (named_before.st_dev, named_before.st_ino)
            fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            info = os.fstat(fd)
            identity = expected_before
            if (info.st_dev, info.st_ino) != identity:
                journal_failure()
                raise AdapterError(
                    "Step directory FD changed during open", "access"
                )
            handles.append((fd, identity, True, parent, name))
            output_identity(fd, identity, True)
            parent = fd
        named_before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        expected_before = (named_before.st_dev, named_before.st_ino)
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        identity = expected_before
        if (info.st_dev, info.st_ino) != identity:
            journal_failure()
            raise AdapterError(
                "Workflow output FD changed during open", "access"
            )
        handles.append((descriptor, identity, False, parent, parts[-1]))
        if value == "true" and info.st_size != 0:
            raise AdapterError("Fresh workflow output required", "access")

        def verify():
            output_parent(handles[0][0], root, root_id)
            for fd, expected, directory, base, name in handles[1:]:
                output_identity(fd, expected, directory)
                named = os.stat(name, dir_fd=base, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != expected:
                    raise AdapterError("Workflow output path changed", "access")

        verify()
        line = ("pacing_ready=" + value + "\n").encode()
        if os.write(descriptor, line) != len(line):
            raise AdapterError("Incomplete workflow output signal", "access")
        verify()
        os.fsync(descriptor)
        verify()
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, expected, directory, _, _ in reversed(handles):
            try:
                output_close(fd, expected, directory)
            except BaseException as error:
                errors.append(error)
        if errors and primary is None:
            raise errors[0]


def journal_export_envelope(path, envelope, atomic=False, signal=False):
    """Retain owned output references through finalization and all releases."""
    parent, root, parent_id = output_open_parent(path)
    name = os.path.basename(path)
    handles = []
    named_id = None
    temporary = None
    primary = None
    try:
        try:
            named_before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            old_expected = (named_before.st_dev, named_before.st_ino)
            old = os.open(
                name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            old = None
        if old is not None:
            info = os.fstat(old)
            named_id = old_expected
            named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != named_id or (
                named_after.st_dev,
                named_after.st_ino,
            ) != named_id:
                journal_failure()
                raise AdapterError(
                    "Prior output changed during acquisition", "access"
                )
            handles.append((old, named_id))
            output_identity(old, named_id)
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise AdapterError("Unsafe old pacing artifact", "access")
            if not atomic:
                output_poison(old, named_id)
                output_parent(parent, root, parent_id)
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != named_id:
                    raise AdapterError("Old artifact was replaced", "access")
                os.unlink(name, dir_fd=parent)
                named_id = None
        check_deadline()
        journal_output_authority()
        if callable(envelope):
            envelope = envelope()
        temporary = ".owned-pacing-" + os.urandom(16).hex() if atomic else name
        descriptor = os.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        named_new = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
        identity = (named_new.st_dev, named_new.st_ino)
        if (info.st_dev, info.st_ino) != identity:
            journal_failure()
            raise AdapterError(
                "New output FD changed during acquisition", "access"
            )
        handles.append((descriptor, identity))
        guard = os.dup(descriptor)
        handles.append((guard, identity))
        raw = json.dumps(envelope, separators=(",", ":")).encode()
        if len(raw) > 16384:
            raise AdapterError("Oversized pacing artifact", "access")
        offset = 0
        while offset < len(raw):
            check_deadline()
            output_parent(parent, root, parent_id)
            output_identity(descriptor, identity)
            written = os.pwrite(descriptor, raw[offset:], offset)
            output_identity(descriptor, identity)
            if written <= 0:
                raise AdapterError(
                    "Incomplete pacing artifact write", "access"
                )
            offset += written
        output_identity(descriptor, identity)
        os.ftruncate(descriptor, len(raw))
        output_identity(descriptor, identity)
        os.fsync(descriptor)
        before = output_identity(guard, identity)
        actual = os.pread(guard, 16385, 0)
        after = output_identity(guard, identity)
        if actual != raw or journal_version(before) != journal_version(after):
            raise AdapterError("Pacing artifact changed during read", "access")
        output_parent(parent, root, parent_id)
        if atomic:
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if (
                None if current is None else (current.st_dev, current.st_ino)
            ) != named_id:
                raise AdapterError("Prior pacing artifact changed", "access")
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            temporary = None
        os.fsync(parent)
        journal_output_authority()
        if signal:
            journal_output_signal("true")
        journal_output_authority()
        output_parent(parent, root, parent_id)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        output_identity(descriptor, identity)
        output_identity(guard, identity)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Final pacing artifact was replaced", "access")
    except BaseException as error:
        primary = error
        journal_failure()
        raise
    finally:
        errors = []
        failed = primary is not None or _JOURNAL_FATAL
        if failed:
            for fd, identity in handles:
                with contextlib.suppress(BaseException):
                    output_poison(fd, identity)
        for fd, identity in reversed(handles):
            try:
                output_close(fd, identity)
            except BaseException as error:
                errors.append(error)
        try:
            output_close(parent, parent_id, True)
        except BaseException as error:
            errors.append(error)
        if failed or errors or _JOURNAL_FATAL:
            # Reopen only the safely owned current output for the known ID.
            # Existing unsafe/replacement objects must remain untouched.
            with contextlib.suppress(BaseException):
                journal_invalidate_known_output(path, handles, parent_id)
            if signal:
                with contextlib.suppress(BaseException):
                    journal_output_signal("false")
        if errors and primary is None:
            raise errors[0]


def journal_invalidate_known_output(path, handles, expected_parent):
    """Last-close recovery cannot invalidate a different replacement inode."""
    parent, root, parent_id = output_open_parent(path)
    if parent_id != expected_parent:
        output_close(parent, parent_id, True)
        raise AdapterError("Original output parent replaced", "access")
    descriptor = None
    identity = None
    try:
        named_before = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        identity = (named_before.st_dev, named_before.st_ino)
        if identity not in [expected for _, expected in handles]:
            raise AdapterError("Unrelated owned output preserved", "access")
        descriptor = os.open(
            os.path.basename(path),
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            raise AdapterError("Owned cleanup FD changed", "access")
        if identity not in [expected for _, expected in handles]:
            raise AdapterError("Unrelated owned output preserved", "access")
        output_parent(parent, root, parent_id)
        output_poison(descriptor, identity)
        output_parent(parent, root, parent_id)
        current = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(os.path.basename(path), dir_fd=parent)
            os.fsync(parent)
    finally:
        if descriptor is not None:
            output_close(descriptor, identity)
        output_close(parent, parent_id, True)


def journal_close_acquired(descriptor, expected):
    """Rollback only an actual named acquisition, never an unknown FD."""
    if expected is None:
        return
    primary = sys.exc_info()[1]
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != expected:
            raise AdapterError("Acquired descriptor was recycled", "access")
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def journal_open_directory(path):
    before = os.stat(path, follow_symlinks=False)
    expected = (before.st_dev, before.st_ino)
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    try:
        info = journal_descriptor(descriptor, expected, True)
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != expected:
            raise AdapterError(
                "Publisher parent changed at acquisition", "access"
            )
        return descriptor, expected
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            journal_close_acquired(descriptor, expected)
        raise


def journal_open_original(parent, name, flags, expected=None):
    """Validate both named generations before publishing an FD as authority."""
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        previous = (before.st_dev, before.st_ino)
    except FileNotFoundError:
        previous = None
        if not flags & os.O_CREAT:
            raise
        flags |= os.O_EXCL
    if expected is not None and previous != expected:
        raise AdapterError("Expected publisher inode changed", "access")
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent
    )
    safe_close = previous
    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identity = (named.st_dev, named.st_ino)
        if previous is not None and identity != previous:
            raise AdapterError("Publisher name changed during open", "access")
        journal_descriptor(descriptor, identity)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError(
                "Publisher file changed at acquisition", "access"
            )
        safe_close = identity
        _JOURNAL_ACQUIRED_IDS[descriptor] = identity
        return descriptor, identity
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            journal_close_acquired(descriptor, safe_close)
        raise


_JOURNAL_ACQUIRED_IDS = {}


def journal_lifecycle_open(path, create):
    """Capture the actual collection acquisition before flock or cleanup."""
    directory = os.path.dirname(path)
    named = os.stat(directory, follow_symlinks=False)
    parent_id = (named.st_dev, named.st_ino)
    parent = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    descriptor = identity = None
    try:
        held = os.fstat(parent)
        current = os.stat(directory, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != parent_id or (
            current.st_dev,
            current.st_ino,
        ) != parent_id:
            raise AdapterError("Lifecycle parent changed during open", "access")
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        descriptor, identity = journal_open_original(
            parent, os.path.basename(path), flags
        )
        return descriptor
    except BaseException:
        journal_failure()
        if descriptor is not None:
            with contextlib.suppress(BaseException):
                journal_close_acquired(descriptor, identity)
        raise
    finally:
        primary = sys.exc_info()[1]
        try:
            journal_close_acquired(parent, parent_id)
        except BaseException:
            if descriptor is not None:
                with contextlib.suppress(BaseException):
                    journal_close_acquired(descriptor, identity)
            if primary is None:
                raise






_GOOGLE407_DECISION = {
    "owner": [
        43985597132,
        1,
        'google',
        '9e8e59d64d479829912bc343fc23b526f6519aea',
    ],
    "artifact_sha256": (
        '5867da89e99ad5b2271766ca81461a3b0cf3990e0510a26ea311adeb37b9'
        '2403'
    ),
    "legacy_state_sha256": (
        'fb4cd8075eeb83701170d3b9447e967be98c26941151d036a9e533f25735'
        'd5b9'
    ),
    "legacy_source_sha256": (
        '65c40228bfb0f43323f1050fd634799b9c344c651ff10b8aed597825bdd3'
        '4db9'
    ),
    "evidence": (
        'https://github.com/YouthOpps/data-pipeline/issues/407#issuec'
        'omment-6956512566'
    ),
    "artifact_id": 14284968983,
}
_GOOGLE407_LOCAL_SEED = None
_GOOGLE407_HOSTED_STATE = {'blocked': False,
 'completed': 1794956686.8066864,
 'completed_flight_id': '5451be4bc1ffaaea4a4a4be26e65476d',
 'completed_request_id': '68f80ad06f7ea43798873f7530a31bec',
 'family': 'youthopps-google-publisher-v1',
 'flight_id': '5451be4bc1ffaaea4a4a4be26e65476d',
 'flight_kind': 'policy',
 'flight_started': 1794956674.5613139,
 'in_flight': False,
 'interval': 12.2,
 'last_inventory_completed': 1794956487.0972388,
 'legacy_policy_history_unknown': True,
 'legacy_rate_history_unknown': True,
 'not_before': 1794956699.0066864,
 'observed_at': 1794956686.8066864,
 'page_interval': 30,
 'rate_limit_strikes': 0,
 'rate_limit_until': 0,
 'request_id': '68f80ad06f7ea43798873f7530a31bec',
 'request_inventory': False,
 'request_outcome': 'response_assessed',
 'request_started': 1794956686.7575746,
 'schema': 3,
 'starts': [1792060933.2197416,
            1792060939.4147806,
            1792060945.9070847,
            1792079865.2485468,
            1792079871.4669194,
            1792079877.7242067,
            1792079883.9553993,
            1792079890.1697528,
            1792079896.390569,
            1792079902.5101707,
            1792079908.628138,
            1792079914.9911332,
            1792079921.462702,
            1792083810.3543506,
            1792083810.5581048,
            1792083823.2867877,
            1792083823.3464828,
            1792083853.8944688,
            1792083853.9168265,
            1792083884.543068,
            1792083884.5686438,
            1792083915.1517706,
            1792083915.1768608,
            1792083945.804046,
            1792083945.8247814,
            1792083958.4160109,
            1792083958.4430606,
            1792083971.0959797,
            1792083971.125289,
            1792083983.7918556,
            1792083983.8174372,
            1792083996.467733,
            1792083996.5333624,
            1792084009.0953674,
            1792084009.1125574,
            1792084021.8147693,
            1792084021.8348918,
            1792084034.4615664,
            1792084034.4967043,
            1792084047.1461682,
            1792084047.172508,
            1792084059.8203301,
            1792084059.8493474,
            1792084072.4644437,
            1792084072.4834332,
            1792084085.0599077,
            1792084085.0795536,
            1792084097.721615,
            1792084097.7457745,
            1792084110.3657067,
            1792084110.4038274,
            1792084123.0697997,
            1792084123.0958548,
            1792084135.8883502,
            1792084135.9074852,
            1792084148.5881813,
            1792084148.6411285,
            1792084161.2249997,
            1792084161.3315165,
            1792084173.9378836,
            1792084173.9742126,
            1792084186.7428734,
            1792084186.805952,
            1792084199.7940655,
            1792084199.859546,
            1794874536.1127372,
            1794874548.5183935,
            1794874560.9156075,
            1794874573.471394,
            1794874585.9206994,
            1794939082.5930498,
            1794939083.1968074,
            1794939096.2314758,
            1794939096.2378514,
            1794939127.3270407,
            1794939127.3286905,
            1794939158.6089325,
            1794939158.6125765,
            1794939189.7574008,
            1794939189.762089,
            1794939221.6820674,
            1794939221.683489,
            1794939235.727031,
            1794939235.7311342,
            1794939248.6512785,
            1794939248.6584084,
            1794939262.0902982,
            1794939262.0980175,
            1794939275.1871078,
            1794939275.1893945,
            1794939288.724466,
            1794939288.7332008,
            1794942774.0692103,
            1794942775.4908984,
            1794942788.1845622,
            1794942788.1938713,
            1794942819.7138402,
            1794942819.7176027,
            1794942850.9415843,
            1794942850.9474866,
            1794942882.2556329,
            1794942882.2580929,
            1794942913.8714826,
            1794942913.8733876,
            1794942927.6597743,
            1794942927.665298,
            1794942940.3868465,
            1794942940.3961165,
            1794942954.1881585,
            1794942954.1959682,
            1794942967.5998402,
            1794942967.6109195,
            1794942981.2192473,
            1794942981.221926,
            1794942995.2310688,
            1794942995.2394295,
            1794943008.550879,
            1794943008.5631301,
            1794943022.0143259,
            1794943022.0210276,
            1794943035.8465614,
            1794943035.85513,
            1794943048.4072397,
            1794943048.4128084,
            1794943062.0717096,
            1794943062.0763297,
            1794953861.3372455,
            1794953864.664844,
            1794953876.9063282,
            1794953876.9098287,
            1794953907.2205076,
            1794953907.223747,
            1794953937.5424337,
            1794953937.5458355,
            1794953967.8690314,
            1794953967.8722377,
            1794953999.1247349,
            1794953999.1279216,
            1794954011.6507254,
            1794954011.6541986,
            1794954024.1543913,
            1794954024.157855,
            1794954036.662007,
            1794954036.665538,
            1794954049.1584246,
            1794954049.1622639,
            1794954061.689241,
            1794954061.6929147,
            1794954074.1903975,
            1794954074.1945102,
            1794954086.7028372,
            1794954086.7064018,
            1794954099.2610912,
            1794954099.2647223,
            1794954111.668589,
            1794954111.6721754,
            1794954124.1808755,
            1794954124.1844513,
            1794954136.6964502,
            1794954136.6999893,
            1794954149.1869886,
            1794954149.1908877,
            1794954161.7159598,
            1794954161.7198334,
            1794954174.23232,
            1794954174.2361977,
            1794954186.7400663,
            1794954186.7436967,
            1794954199.253325,
            1794954199.256863,
            1794954211.5536609,
            1794954211.5572536,
            1794954223.8019571,
            1794954223.8056335,
            1794954236.3494165,
            1794954236.3532825,
            1794956296.3516657,
            1794956303.061989,
            1794956315.3118386,
            1794956315.318801,
            1794956345.6615314,
            1794956345.668492,
            1794956375.9903564,
            1794956375.9970572,
            1794956406.4928796,
            1794956406.4996123,
            1794956436.9109273,
            1794956436.9173355,
            1794956449.461675,
            1794956449.4682963,
            1794956461.8943324,
            1794956461.9012353,
            1794956474.4124963,
            1794956474.4194596,
            1794956486.8338525,
            1794956486.840552,
            1794956499.3013625,
            1794956499.3085876,
            1794956511.8329675,
            1794956511.8398328,
            1794956524.2397406,
            1794956524.246614,
            1794956536.6642084,
            1794956536.671224,
            1794956549.0857475,
            1794956549.092675,
            1794956561.6205251,
            1794956561.627607,
            1794956574.127025,
            1794956574.1343553,
            1794956586.906734,
            1794956586.91379,
            1794956599.3340082,
            1794956599.341175,
            1794956611.8644807,
            1794956611.8714106,
            1794956624.385523,
            1794956624.3922715,
            1794956636.9213078,
            1794956636.9284644,
            1794956649.2560887,
            1794956649.262797,
            1794956661.615788,
            1794956661.6228974,
            1794956674.0665238,
            1794956674.0741127,
            1794956686.7575746,
            1794956686.7643583]}
_GOOGLE407_HOSTED_EVIDENCE = 'https://github.com/YouthOpps/data-pipeline/issues/407#issuecomment-6963768713'
_STATE_PARENT_FD = _STATE_PARENT_ID = _STATE_PARENT_METADATA = None
_ACTIVE_FLIGHT_ID = _ACTIVE_REQUEST_ID = None
_TRANSACTION_DEPTH = 0
_REQUEST_FRAMES = []
_STARTUP_QUIET_UNTIL = None
_LOCAL_FIRST_WIRE_VERIFIED = False
_OPERATION_END = None
_LOCAL_CLAIMS = {}

def upgrade_legacy_budget(state):
    """Import exact legacy fields while retaining unknown policy history."""
    legacy_keys = {
        "schema",
        "family",
        "observed_at",
        "not_before",
        "starts",
        "blocked",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_strikes",
        "rate_limit_until",
        "legacy_rate_history_unknown",
    }
    if (
        type(state) is not dict
        or set(state) != legacy_keys
        or type(state["schema"]) is not int
        or state["schema"] != 2
        or type(state["legacy_rate_history_unknown"]) is not bool
    ):
        raise AdapterError("Unreviewed legacy Google journal", "access")
    incoming = empty_budget(state["observed_at"])
    incoming.update(copy.deepcopy(state))
    incoming.update(
        schema=3,
        legacy_rate_history_unknown=True,
        legacy_policy_history_unknown=True,
    )
    return validate_budget(incoming)

def require_existing_local_state():
    """Pin the mature nonempty generation before metadata callbacks."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    if _GOOGLE407_LOCAL_SEED is None:
        verify_native3_namespace()
    budget_file()
    with locked_budget():
        state = load_budget()
        if state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Google journal requires reviewed recovery", "access"
            )
        journal_verify()

def fail_policy():
    """Keep the last durable pending owner; revoke this process's access."""
    global _PUBLISHER_GATE_FAILED
    _PUBLISHER_GATE_FAILED = True

def begin_transaction():
    global _ACTIVE_FLIGHT_ID
    check_deadline()
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
        raise AdapterError(
            "Healthy restored Google journal required", "access"
        )
    refusal = None
    with locked_budget():
        state = load_budget()
        if (
            state["in_flight"]
            or state["blocked"]
            or state["rate_limit_strikes"] >= 100
        ):
            raise AdapterError(
                "Google journal requires reviewed recovery", "access"
            )
        now = time.time()
        ready = max(
            state["not_before"], state["completed"] + state["interval"]
        )
        if state["rate_limit_until"] > now:
            refusal = "Retained Google cooldown"
        elif ready + 120 > now + min(_PHASE_END, _RUN_END) - time.monotonic():
            refusal = "Known Google guard exceeds phase budget"
        journal_verify()
        if refusal is None:
            ident = os.urandom(16).hex()
            if ident == state["flight_id"]:
                raise AdapterError("Google owner identity collision", "access")
            state.update(
                in_flight=True,
                flight_id=ident,
                flight_kind="policy",
                flight_started=now,
                observed_at=now,
            )
            save_budget(state)
            _ACTIVE_FLIGHT_ID = ident
    if refusal:
        raise AdapterError(refusal, "access")

@contextlib.contextmanager
def policy_operation():
    global _TRANSACTION_DEPTH, _ACTIVE_FLIGHT_ID
    outer = _TRANSACTION_DEPTH == 0
    if outer:
        begin_transaction()
    _TRANSACTION_DEPTH += 1
    assessed = False
    try:
        yield
        assessed = True
    except PublisherStop:
        assessed = not _PUBLISHER_GATE_FAILED and not _JOURNAL_FATAL
        raise
    except DataReviewDeferred as error:
        assessed = data_review_transport_assessed(error, outer)
        raise
    finally:
        _TRANSACTION_DEPTH -= 1
        if outer:
            if not assessed or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
                fail_policy()
            else:
                try:
                    with locked_budget():
                        state = load_budget()
                        if (
                            not state["in_flight"]
                            or state["flight_id"] != _ACTIVE_FLIGHT_ID
                            or state["request_outcome"] != "response_assessed"
                            or state["request_id"]
                            != state["completed_request_id"]
                            or state["request_started"]
                            < state["flight_started"]
                            or state["completed"] < state["request_started"]
                            or _ACTIVE_REQUEST_ID is not None
                            or _REQUEST_FRAMES
                        ):
                            raise AdapterError(
                                "Google owner changed before completion",
                                "access",
                            )
                        now = time.time()
                        state.update(
                            in_flight=False,
                            completed_flight_id=_ACTIVE_FLIGHT_ID,
                            completed=max(state["completed"], now),
                            observed_at=max(state["observed_at"], now),
                        )
                        state["not_before"] = max(
                            state["not_before"],
                            state["completed"] + state["interval"],
                        )
                        save_budget(state)
                    _ACTIVE_FLIGHT_ID = None
                except BaseException:
                    fail_policy()
                    raise


_REP_PROVENANCE = {}
_REP_TARGETS = {}
_LAST_RESPONSE_RECEIPT = None

class PublisherStop(AdapterError):
    """A completely assessed refusal stops collection with retained limits."""

@contextlib.contextmanager
def operation_guard(publisher=False):
    """Bound headers, body and closure by the earliest operation end."""
    global _OPERATION_END
    check_deadline()
    previous_end = _OPERATION_END
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    began = time.monotonic()
    end = min(_PHASE_END, _RUN_END, began + (60 if publisher else 15))
    if previous_end is not None:
        end = min(end, previous_end)
    if previous_timer[0] > 0:
        end = min(end, began + previous_timer[0])
    if threading.current_thread() is not threading.main_thread():
        raise AdapterError("Main-thread operation timer required", "access")
    _OPERATION_END = end

    def exhausted(signum, frame):
        for request_frame in _REQUEST_FRAMES:
            request_frame["assessed"] = False
        if publisher or _REQUEST_FRAMES:
            fail_policy()
        # Repeat during cancellation: body timeout must also bound slow close.
        signal.setitimer(signal.ITIMER_REAL, 0.05, 0.05)
        raise PhaseExpired()

    try:
        signal.signal(signal.SIGALRM, exhausted)
        remaining = end - time.monotonic()
        if remaining <= 0:
            exhausted(None, None)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield
        check_deadline()
        if time.monotonic() >= end:
            exhausted(None, None)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        _OPERATION_END = previous_end
        if previous_timer[0] > 0:
            remaining = previous_timer[0] - (time.monotonic() - began)
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, remaining),
                previous_timer[1] if remaining > 0 else 0.05,
            )


_SOURCE_PATH = "adapters/google/adapter.py"
_WORKFLOW_ID = 380770427
_LEGACY_OWNER = (
    43985597132,
    1,
    "google",
    "9e8e59d64d479829912bc343fc23b526f6519aea",
)
_LEGACY_ARTIFACT_ID = 14284968983
_LEGACY_ZIP_SHA = (
    "5867da89e99ad5b2271766ca81461a3b0cf3990e0510a26ea311adeb37b92403"
)
_LEGACY_STATE_SHA = (
    "fb4cd8075eeb83701170d3b9447e967be98c26941151d036a9e533f25735d5b9"
)
_LEGACY_SOURCE_SHA = (
    "65c40228bfb0f43323f1050fd634799b9c344c651ff10b8aed597825bdd34db9"
)
_IMMUTABLE_BYTES = {}
_HOSTED_IDENTITY = None

def canonical_state(state):
    return json.dumps(state, sort_keys=True, separators=(",", ":")).encode()

def infrastructure_url(value):
    """Allow exact GitHub API and supported artifact storage origins."""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.fragment
            or parsed.netloc != host
            or not parsed.path.startswith("/")
            or any(ord(c) < 33 for c in value)
        ):
            raise ValueError()
        if host == "api.github.com":
            if not re.match(
                r"^/repos/YouthOpps/(?:data-pipeline|data-source)(?:/|$)",
                parsed.path,
            ):
                raise ValueError()
        elif not (
            re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", host)
            or re.fullmatch(
                r"[a-z0-9][a-z0-9-]*\.actions\.githubusercontent\.com", host
            )
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise AdapterError(
            "Unreviewed infrastructure origin", "access"
        ) from None
    return value

def verified_source(head):
    if not isinstance(head, str) or not re.fullmatch("[0-9a-f]{40}", head):
        raise AdapterError("Invalid producer revision", "access")
    if head not in _IMMUTABLE_BYTES:
        result = workflow_api("/contents/" + _SOURCE_PATH + "?ref=" + head)
        try:
            raw = base64.b64decode(result["content"], validate=False)
            expected = hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()
            if (
                result["type"] != "file"
                or result["encoding"] != "base64"
                or result["path"] != _SOURCE_PATH
                or result["sha"] != expected
                or len(raw) > 1000000
                or result["size"] != len(raw)
            ):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise AdapterError(
                "Untrusted immutable Google producer", "access"
            ) from None
        _IMMUTABLE_BYTES[head] = raw
    return _IMMUTABLE_BYTES[head]

def reviewed_transition():
    value = copy.deepcopy(_GOOGLE407_DECISION)
    expected = {
        "owner": list(_LEGACY_OWNER),
        "artifact_id": _LEGACY_ARTIFACT_ID,
        "artifact_sha256": _LEGACY_ZIP_SHA,
        "legacy_state_sha256": _LEGACY_STATE_SHA,
        "legacy_source_sha256": _LEGACY_SOURCE_SHA,
    }
    if type(value) is not dict:
        raise AdapterError("Reviewed Google407 transition is OFF", "access")
    evidence = value.pop("evidence", None)
    if (
        value != expected
        or type(value.get("artifact_id")) is not int
        or type(value["owner"][0]) is not int
        or type(value["owner"][1]) is not int
        or not isinstance(evidence, str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/407"
            r"#issuecomment-[1-9][0-9]*",
            evidence,
        )
    ):
        raise AdapterError("Unreviewed Google407 transition", "access")
    return evidence

def workflow_mode_guard():
    value = workflow_api("/actions/workflows/fetch-google.yml")
    expected = (
        "active"
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else "disabled_manually"
    )
    if (
        value.get("id") != _WORKFLOW_ID
        or value.get("path") != ".github/workflows/fetch-google.yml"
        or value.get("state") != expected
    ):
        raise AdapterError("Google workflow coordination changed", "access")

def current_job_context():
    global _HOSTED_IDENTITY
    if os.environ.get("GITHUB_ACTIONS") != "true":
        if "DATA_SOURCE_TOKEN" in os.environ:
            raise AdapterError(
                "Local Google collection refuses publication token", "access"
            )
        return
    run_id, attempt = current_run_identity()
    head = os.environ["GITHUB_SHA"]
    run = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(run, (run_id, attempt, SOURCE_ID, head))
    jobs = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    rows = jobs.get("jobs")
    if (
        type(jobs.get("total_count")) is not int
        or jobs["total_count"] != 1
        or type(rows) is not list
        or len(rows) != 1
        or run.get("status") != "in_progress"
    ):
        raise AdapterError("Unexpected current Google job inventory", "access")
    job = rows[0]
    if (
        type(job.get("run_id")) is not int
        or job["run_id"] != run_id
        or type(job.get("run_attempt")) is not int
        or job["run_attempt"] != attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("status") != "in_progress"
        or job.get("name") != "collect"
        or type(job.get("id")) is not int
        or job["id"] <= 0
    ):
        raise AdapterError("Current Google job identity differs", "access")
    with open(__file__, "rb") as stream:
        local_source = stream.read(1000001)
    if (
        len(local_source) > 1000000
        or hashlib.sha256(verified_source(head)).hexdigest()
        != hashlib.sha256(local_source).hexdigest()
    ):
        raise AdapterError("Current Google producer bytes differ", "access")
    _HOSTED_IDENTITY = (run_id, attempt, SOURCE_ID, head)

_GOOGLE407_ACCEPTED_NATIVE = {
    "workflow": 421966693,
    "run": 44194838216,
    "job": 134433403271,
    "attempt": 1,
    "head": "392fe46d3e9961a7a5935cb597a321ee99bd445c",
    "terminal": "2026-11-17T23:04:56Z",
    "path": ".github/workflows/qa-google407-native-r2.yml",
    "branch": "qa/google407-native-r2",
    "artifact_id": 14378927055,
    "artifact_size": 46721296,
    "archive_sha256": "5777d34ff585040e4db30fce42ea7952bfafa7db7e546904cfcaab888d2eeed6",
    "state_sha256": "f8e7cc81bd84946e90cc9564a949905bbdf8f49124760400bf188d1a1a72d721",
    "floor_sha256": "9768cab438cdc01636c0f4f41c4a0444411237db0566a4b8c3748e7572cfde21",
    "observation_count": 228,
}


def verify_first_hosted_native_owner():
    """Authorize only the artifact that independently passed the original complete test."""
    accepted = _GOOGLE407_ACCEPTED_NATIVE
    repo = "YouthOpps/data-pipeline"
    workflow, run = accepted["workflow"], accepted["run"]
    definition = workflow_api(f"/actions/workflows/{workflow}")
    if (definition.get("id"), definition.get("path"), definition.get("state")) != (
            workflow, accepted["path"], "disabled_manually"):
        raise AdapterError("Google407 first production requires the retired successful native owner", "access")
    for wid, path in ((421935029, ".github/workflows/qa-google407-native.yml"),
                      (421958094, ".github/workflows/qa-google407-terms-settlement.yml")):
        previous = workflow_api(f"/actions/workflows/{wid}")
        if (previous.get("id"), previous.get("path"), previous.get("state")) != (wid, path, "disabled_manually"):
            raise AdapterError("Earlier Google407 evidence workflow is not retired", "access")
    listing = workflow_api(f"/actions/workflows/{workflow}/runs?per_page=100")
    entries = listing.get("workflow_runs")
    if type(entries) is not list or len(entries) != listing.get("total_count") or len(entries) != 1:
        raise AdapterError("Successful Google407 native history no longer has its exact attempt", "access")
    endpoint = f"/actions/runs/{run}/attempts/1"
    expected = {"id": run, "run_attempt": 1, "head_sha": accepted["head"], "head_branch": accepted["branch"],
                "workflow_id": workflow, "path": accepted["path"], "event": "push", "status": "completed",
                "conclusion": "success"}
    for row in (entries[0], workflow_api(endpoint)):
        if (any(row.get(k) != v for k, v in expected.items())
                or any(row.get(k, {}).get("full_name") != repo for k in ("repository", "head_repository"))):
            raise AdapterError("Exact successful Google407 native attempt differs", "access")
    result = workflow_api(endpoint + "/jobs?per_page=100")
    jobs = result.get("jobs")
    job = {"id": accepted["job"], "name": "native", "run_id": run, "run_attempt": 1, "head_sha": accepted["head"],
           "head_branch": accepted["branch"], "status": "completed", "conclusion": "success",
           "completed_at": accepted["terminal"]}
    if type(jobs) is not list or len(jobs) != result.get("total_count") or len(jobs) != 1 or any(jobs[0].get(k) != v for k, v in job.items()):
        raise AdapterError("Exact successful Google407 native job differs", "access")
    items = workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    records = items.get("artifacts")
    if type(records) is not list or len(records) != items.get("total_count") or len(records) != 1:
        raise AdapterError("Successful Google407 native owned archive inventory differs", "access")
    artifact = records[0]
    owner = artifact.get("workflow_run", {})
    if ((artifact.get("id"), artifact.get("size_in_bytes"), artifact.get("digest")) !=
            (accepted["artifact_id"], accepted["artifact_size"], "sha256:" + accepted["archive_sha256"])
            or artifact.get("expired") is not False or artifact.get("name") != f"google407-native-r2-qa-{run}-1"
            or (owner.get("id"), owner.get("head_branch"), owner.get("head_sha")) != (run, accepted["branch"], accepted["head"])
            or owner.get("repository_id") != 1163601208 or owner.get("head_repository_id") != 1163601208):
        raise AdapterError("Accepted Google407 native archive owner differs", "access")


def hosted_floor(incoming, latest):
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return incoming
    if (type(_GOOGLE407_HOSTED_STATE) is not dict or not isinstance(_GOOGLE407_HOSTED_EVIDENCE, str)
            or not re.fullmatch(r"https://github\.com/YouthOpps/data-pipeline/issues/407#issuecomment-[1-9][0-9]*",
                               _GOOGLE407_HOSTED_EVIDENCE)):
        raise AdapterError("Google407 hosted MAX handoff is OFF", "access")
    approved = validate_budget(copy.deepcopy(_GOOGLE407_HOSTED_STATE))
    if (hashlib.sha256(canonical_state(approved)).hexdigest() != _GOOGLE407_ACCEPTED_NATIVE["floor_sha256"]
            or len(approved["starts"]) != _GOOGLE407_ACCEPTED_NATIVE["observation_count"]
            or approved["blocked"] or approved["in_flight"]
            or approved["request_id"] != approved["completed_request_id"]
            or approved["flight_id"] != approved["completed_flight_id"]):
        raise AdapterError("The exact successful Google407 native MAX differs", "access")
    if latest == _LEGACY_OWNER:
        verify_first_hosted_native_owner()
        if merge_budgets(approved, incoming, approved["observed_at"]) != approved:
            raise AdapterError("Google407 first production floor omits original owned state", "access")
        return approved
    if (not set(approved["starts"]).issubset(incoming["starts"])
            or merge_budgets(incoming, approved, incoming["observed_at"]) != incoming):
        raise AdapterError("Newest Google407 producer cannot restore a weaker mature state", "access")
    return incoming




def local_seed_claim():
    evidence = reviewed_transition()
    packet = _GOOGLE407_LOCAL_SEED
    if (
        type(packet) is not dict
        or set(packet) != {"state_sha256", "machine_sha256"}
        or any(
            not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v)
            for v in packet.values()
        )
    ):
        raise AdapterError("Google407 local initialization is OFF", "access")
    with open("/etc/machine-id", "rb") as stream:
        if (
            hashlib.sha256(stream.read(4097)).hexdigest()
            != packet["machine_sha256"]
        ):
            raise AdapterError("Google initializer machine differs", "access")
    with open(__file__, "rb") as stream:
        raw = stream.read(1000001)
    if len(raw) > 1000000:
        raise AdapterError(
            "Google source exceeds initialization bound", "access"
        )
    return {
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "state_sha256": packet["state_sha256"],
        "machine_sha256": packet["machine_sha256"],
        "evidence": evidence,
        "family": FAMILY,
    }

def claim_local_attempt():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    if "DATA_SOURCE_TOKEN" in os.environ:
        raise AdapterError(
            "Local Google collection refuses publication token", "access"
        )
    if _GOOGLE407_LOCAL_SEED is None:
        return claim_local_continuation()
    expected = local_seed_claim()
    budget_file()
    name = FAMILY + "-issue407-initialized-" + str(os.getuid()) + ".claim"
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=_STATE_PARENT_FD,
    )
    try:
        before = os.fstat(descriptor)
        named = os.stat(name, dir_fd=_STATE_PARENT_FD, follow_symlinks=False)
        raw = os.pread(descriptor, 4097, 0)
        after = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            or journal_version(before) != journal_version(after)
            or len(raw) > 4096
            or json.loads(raw) != expected
        ):
            raise AdapterError("Google initializer claim differs", "access")
        _LOCAL_CLAIMS[name] = (
            _STATE_PARENT_FD,
            (before.st_dev, before.st_ino),
            raw,
        )
    finally:
        os.close(descriptor)
    with locked_budget():
        state = load_budget()
        if (
            state["in_flight"]
            or state["blocked"]
            or hashlib.sha256(journal_read()).hexdigest()
            != expected["state_sha256"]
        ):
            raise AdapterError("Initial Google generation differs", "access")
        descriptor = secure_file(
            "issue407-native.claim", os.O_RDWR | os.O_CREAT | os.O_EXCL
        )
        try:
            raw = canonical_state(expected)
            if os.write(descriptor, raw) != len(raw):
                raise AdapterError("Short Google native claim write", "access")
            os.fsync(descriptor)
            if os.pread(descriptor, len(raw) + 1, 0) != raw:
                raise AdapterError(
                    "Google native claim readback differs", "access"
                )
            os.fsync(_STATE_DIR_FD)
            info = os.fstat(descriptor)
            _LOCAL_CLAIMS["issue407-native.claim"] = (
                _STATE_DIR_FD,
                (info.st_dev, info.st_ino),
                raw,
            )
        finally:
            os.close(descriptor)

def verify_local_claims():
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    expected = 5 if _GOOGLE407_LOCAL_SEED is None else 2
    if len(_LOCAL_CLAIMS) != expected:
        raise AdapterError("Google exclusive claims missing", "access")
    for name, (parent, identity, raw) in _LOCAL_CLAIMS.items():
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        try:
            info = os.fstat(descriptor)
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600
                or (info.st_dev, info.st_ino) != identity
                or (named.st_dev, named.st_ino) != identity
                or os.pread(descriptor, len(raw) + 1, 0) != raw
            ):
                raise AdapterError(
                    "Original Google exclusive claim changed", "access"
                )
        finally:
            os.close(descriptor)
    if _GOOGLE407_LOCAL_SEED is None:
        verify_native3_namespace()
        verify_native3_witness()


_JOURNAL_AUTHORITY = False

def verify_lock_description(descriptor, identity):
    """Reject an inode-correct FD that no longer owns its original flock."""
    journal_descriptor(descriptor, identity)
    with open(
        "/proc/self/fdinfo/" + str(descriptor), encoding="ascii"
    ) as stream:
        entries = stream.read(8193)
    if len(entries) > 8192 or not re.search(
        r"^lock:.*FLOCK\s+ADVISORY\s+WRITE\s+", entries, re.M
    ):
        raise AdapterError("Original Google flock was released", "access")

_LEGACY_CLAIMS = []
_LEGACY_MISSING = []
_LEGACY_ROOT_ABSENT = False

def verify_legacy_claims():
    root = os.path.join(tempfile.gettempdir(), "issue111-google-research")
    if _LEGACY_ROOT_ABSENT:
        if os.path.lexists(root):
            raise AdapterError(
                "Legacy Google parent appeared after admission", "access"
            )
        return
    for descriptor, path, identity, raw, version in _LEGACY_CLAIMS:
        info = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != identity or (
            named.st_dev,
            named.st_ino,
        ) != identity:
            raise AdapterError(
                "Original legacy Google identity changed", "access"
            )
        if raw is None:
            journal_descriptor(descriptor, identity, directory=True)
        else:
            verify_lock_description(descriptor, identity)
            if (
                journal_version(info) != version
                or os.pread(descriptor, len(raw) + 1, 0) != raw
            ):
                raise AdapterError(
                    "Original legacy Google constraints changed", "access"
                )
    for parent, name in _LEGACY_MISSING:
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise AdapterError(
            "Legacy Google state appeared after admission", "access"
        )

def close_state_authorities():
    """Close retained research and namespace FDs, preserving primary errors."""
    global _STATE_DIR_FD, _STATE_PARENT_FD, _STATE_DIR_ID, _STATE_PARENT_ID
    global _STATE_PATH, _STATE_LOCK
    global _JOURNAL_GUARD_FD, _COLLECTION_FLOCK_GUARD
    global _LEGACY_ROOT_ABSENT
    errors = []
    try:
        verify_legacy_claims()
    except BaseException as error:
        fail_policy()
        errors.append(error)
    for descriptor, path, identity, raw, version in reversed(_LEGACY_CLAIMS):
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError(
                    "Legacy Google descriptor was reused", "access"
                )
            os.close(descriptor)
        except BaseException as error:
            fail_policy()
            errors.append(error)
    _LEGACY_CLAIMS.clear()
    _LEGACY_MISSING.clear()
    _LEGACY_ROOT_ABSENT = False
    for descriptor, identity in (
        (_STATE_DIR_FD, _STATE_DIR_ID),
        (_STATE_PARENT_FD, _STATE_PARENT_ID),
    ):
        if descriptor is None:
            continue
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity or not stat.S_ISDIR(
                info.st_mode
            ):
                raise AdapterError(
                    "Google namespace descriptor was reused", "access"
                )
            os.close(descriptor)
        except BaseException as error:
            fail_policy()
            errors.append(error)
    _STATE_DIR_FD = _STATE_PARENT_FD = None
    _STATE_DIR_ID = _STATE_PARENT_ID = None
    _STATE_PATH = _STATE_LOCK = None
    for descriptor, identity in (
        (_JOURNAL_GUARD_FD, _JOURNAL_GUARD_ID),
        (_COLLECTION_FLOCK_GUARD, _COLLECTION_GUARD_ID),
    ):
        if descriptor is None:
            continue
        try:
            journal_close_descriptor(descriptor, identity)
        except BaseException as error:
            fail_policy()
            errors.append(error)
    _JOURNAL_GUARD_FD = _COLLECTION_FLOCK_GUARD = None
    if errors:
        raise errors[0]

@contextlib.contextmanager
def assessed_response(response, publisher, robots):
    """Retain received constraints before entry; close even if entry fails."""
    try:
        status = response.status
        if publisher:
            if status == 429:
                record_retry_after([], 429)
            elif not robots and status in (401, 403, 451):
                retain_publisher_hold()
        with response:
            yield status
    finally:
        primary = sys.exc_info()[1]
        try:
            if not getattr(response, "closed", False):
                response.close()
        except BaseException:
            if publisher:
                fail_policy()
            if primary is None:
                raise


class DataReviewDeferred(AdapterError):
    """Configured material needs review; transport remains a separate gate."""

    def __init__(self, message, reason, target=None, receipt=None):
        super().__init__(message, "parse")
        self.reason = reason
        self.target = target
        self.receipt = receipt


def reviewable_embedded_material(body, headings, expected):
    """Recognize the same embedded document, never changed access policy."""
    if not expected or headings != expected:
        return False
    document = embedded_html(body)
    parser = MaterialFacts()
    parser.feed(document)
    parser.close()
    outer = MaterialFacts()
    outer.feed(body.decode("utf-8", "strict"))
    outer.close()
    if parser.skip or outer.skip:
        return False
    text = " ".join(parser.text + outer.text)
    # Changed material with access-policy vocabulary needs policy review, even
    # when its permission/negation wording is unfamiliar or far apart. This
    # conservative gate deliberately prefers a hard barrier to inferred consent.
    policy_vocabulary = (
        r"\b(?:autom\w*|(?:chat)?bots?|robots?|crawl\w*|spider\w*|scrap\w*|"
        r"programmatic\w*|harvest\w*|bulk|permission|approv\w*|consent|"
        r"authori[sz]\w*|licen[cs]\w*|prohibit\w*|forbid\w*|restrict\w*)\b|"
        r"\b(?:software\s+agents?|non[-\s]+human|manual\s+access|"
        r"only\s+(?:human|manual)\w*|terms\s+of\s+access|"
        r"api\s+(?:access|use)|machine[-\s]+(?:access|read\w*)|"
        r"(?:data|text)[-\s]+mining)\b"
    )
    if re.search(policy_vocabulary, text, re.I):
        return False
    return bool(text.strip())


def data_review_transport_assessed(error, outer):
    """Semantic review cannot heal received/namespace/persistence failures."""
    if (
        type(error) is not DataReviewDeferred
        or error.reason != "configured-material"
        or error.target not in INPUTS
        or INPUTS[error.target]["format"] != "embedded-html"
        or error.target != _LAST_RESPONSE_URL
        or not error.receipt
        or error.receipt != _LAST_RESPONSE_RECEIPT
        or not outer
        or _TRANSACTION_DEPTH != 1
        or not _STATE_TRUSTED
        or _PUBLISHER_GATE_FAILED
        or _JOURNAL_FATAL
        or _REQUEST_FRAMES
        or _ACTIVE_REQUEST_ID is not None
    ):
        return False
    with locked_budget():
        state = load_budget()
        if (
            not state["in_flight"]
            or state["flight_kind"] != "policy"
            or state["flight_id"] != _ACTIVE_FLIGHT_ID
            or state["blocked"]
            or not state["request_id"]
            or state["request_id"] != state["completed_request_id"]
            or state["completed_request_id"] != error.receipt
            or state["request_outcome"] != "response_assessed"
            or state["request_started"] < state["flight_started"]
            or state["completed"] < state["request_started"]
        ):
            return False
    journal_verify()
    journal_verify_collection()
    verify_local_claims()
    verify_legacy_claims()
    return True


_JOURNAL_PRESERVE_UNKNOWN = False


_JOURNAL_GUARD_FD = _JOURNAL_GUARD_ID = None
_COLLECTION_FLOCK_GUARD = _COLLECTION_GUARD_ID = None


def verify_journal_flocks():
    verify_lock_description(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
    verify_lock_description(_JOURNAL_GUARD_FD, _JOURNAL_GUARD_ID)


_LAST_RESPONSE_URL = None


_HTTP_DATE_PATTERN = (
    r"(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} [A-Z][a-z]{2} "
    r"[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT|"
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
    r"[0-9]{2}-[A-Z][a-z]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT|"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} [ 0-9][0-9] "
    r"[0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4})"
)


_HTTP_DATE_COMPONENT_PATTERN = (
    r"(?:^|(?<=,))\s*(" + _HTTP_DATE_PATTERN + r")(?=\s*(?:,|$))"
)


def journal_output_authority():
    """Verify output ownership without a new publisher reservation."""
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
        raise AdapterError(
            "Healthy Google output authority required", "access"
        )
    journal_verify()
    journal_verify_collection()
    verify_local_claims()
    verify_legacy_claims()



_GOOGLE407_PREVIOUS_APM = {
    "summary": (
        'APM offers recent graduates two one-year product-management '
        'rotations, mentors and professional development; summer inte'
        'rns join one team for 12 weeks. Internship applicants must g'
        'raduate within 12 months after the internship. Technical und'
        'erstanding, product judgement, analysis and communication ma'
        'tter. U.S. applications are listed as September 22-October 6'
        ' and London internships October 5-November 12, without a yea'
        'r; other graduate intake dates are unknown. Check actual rol'
        'e requirements.'
    ),
    "host_countries": ['US', 'GB', 'DE', 'CH', 'JP'],
}

_GOOGLE407_CONTINUATION = None


_GOOGLE407_NATIVE3_FLOOR = {'blocked': False,
 'completed': 1794943063.2627711,
 'completed_flight_id': 'e1aeb891132854a02ca52caa96bbe976',
 'completed_request_id': 'f6d914f92dc3028a0943d1a8face61ff',
 'family': 'youthopps-google-publisher-v1',
 'flight_id': 'e1aeb891132854a02ca52caa96bbe976',
 'flight_kind': 'policy',
 'flight_started': 1794943049.8722267,
 'in_flight': False,
 'interval': 12.2,
 'last_inventory_completed': 1794942969.0184925,
 'legacy_policy_history_unknown': True,
 'legacy_rate_history_unknown': True,
 'not_before': 1794943075.4627712,
 'observed_at': 1794943063.2627711,
 'page_interval': 30,
 'rate_limit_strikes': 0,
 'rate_limit_until': 0,
 'request_id': 'f6d914f92dc3028a0943d1a8face61ff',
 'request_inventory': False,
 'request_outcome': 'response_assessed',
 'request_started': 1794943062.0717096,
 'schema': 3,
 'starts': [1792060933.2197416,
            1792060939.4147806,
            1792060945.9070847,
            1792079865.2485468,
            1792079871.4669194,
            1792079877.7242067,
            1792079883.9553993,
            1792079890.1697528,
            1792079896.390569,
            1792079902.5101707,
            1792079908.628138,
            1792079914.9911332,
            1792079921.462702,
            1792083810.3543506,
            1792083810.5581048,
            1792083823.2867877,
            1792083823.3464828,
            1792083853.8944688,
            1792083853.9168265,
            1792083884.543068,
            1792083884.5686438,
            1792083915.1517706,
            1792083915.1768608,
            1792083945.804046,
            1792083945.8247814,
            1792083958.4160109,
            1792083958.4430606,
            1792083971.0959797,
            1792083971.125289,
            1792083983.7918556,
            1792083983.8174372,
            1792083996.467733,
            1792083996.5333624,
            1792084009.0953674,
            1792084009.1125574,
            1792084021.8147693,
            1792084021.8348918,
            1792084034.4615664,
            1792084034.4967043,
            1792084047.1461682,
            1792084047.172508,
            1792084059.8203301,
            1792084059.8493474,
            1792084072.4644437,
            1792084072.4834332,
            1792084085.0599077,
            1792084085.0795536,
            1792084097.721615,
            1792084097.7457745,
            1792084110.3657067,
            1792084110.4038274,
            1792084123.0697997,
            1792084123.0958548,
            1792084135.8883502,
            1792084135.9074852,
            1792084148.5881813,
            1792084148.6411285,
            1792084161.2249997,
            1792084161.3315165,
            1792084173.9378836,
            1792084173.9742126,
            1792084186.7428734,
            1792084186.805952,
            1792084199.7940655,
            1792084199.859546,
            1794874536.1127372,
            1794874548.5183935,
            1794874560.9156075,
            1794874573.471394,
            1794874585.9206994,
            1794939082.5930498,
            1794939083.1968074,
            1794939096.2314758,
            1794939096.2378514,
            1794939127.3270407,
            1794939127.3286905,
            1794939158.6089325,
            1794939158.6125765,
            1794939189.7574008,
            1794939189.762089,
            1794939221.6820674,
            1794939221.683489,
            1794939235.727031,
            1794939235.7311342,
            1794939248.6512785,
            1794939248.6584084,
            1794939262.0902982,
            1794939262.0980175,
            1794939275.1871078,
            1794939275.1893945,
            1794939288.724466,
            1794939288.7332008,
            1794942774.0692103,
            1794942775.4908984,
            1794942788.1845622,
            1794942788.1938713,
            1794942819.7138402,
            1794942819.7176027,
            1794942850.9415843,
            1794942850.9474866,
            1794942882.2556329,
            1794942882.2580929,
            1794942913.8714826,
            1794942913.8733876,
            1794942927.6597743,
            1794942927.665298,
            1794942940.3868465,
            1794942940.3961165,
            1794942954.1881585,
            1794942954.1959682,
            1794942967.5998402,
            1794942967.6109195,
            1794942981.2192473,
            1794942981.221926,
            1794942995.2310688,
            1794942995.2394295,
            1794943008.550879,
            1794943008.5631301,
            1794943022.0143259,
            1794943022.0210276,
            1794943035.8465614,
            1794943035.85513,
            1794943048.4072397,
            1794943048.4128084,
            1794943062.0717096,
            1794943062.0763297]}

_GOOGLE407_NATIVE3_FLOOR_SHA = '51baddd79def7327680acd9045cb2ecd4f5557088860c6d3546c2f26dc235bf1'

_GOOGLE407_NATIVE3_LINEAGE = {'initializer_claim': {'bytes': 382,
                       'gid': 1000,
                       'identity': [34, 128488],
                       'mode': 384,
                       'path': '/tmp/youthopps-google-publisher-v1-issue407-initialized-1000.claim',
                       'sha256': '985b4f9f4e6ae6f90707f897aea16f193e20b6e4e9d4878cfa6f32fcfbc0cc42',
                       'uid': 1000},
 'journal': {'bytes': 3134,
             'gid': 1000,
             'identity': [34, 128492],
             'mode': 384,
             'path': '/tmp/youthopps-google-publisher-v1-1000/state.json',
             'sha256': '51baddd79def7327680acd9045cb2ecd4f5557088860c6d3546c2f26dc235bf1',
             'uid': 1000},
 'native1_claim': {'bytes': 382,
                   'gid': 1000,
                   'identity': [34, 128534],
                   'mode': 384,
                   'path': '/tmp/youthopps-google-publisher-v1-1000/issue407-native.claim',
                   'sha256': '985b4f9f4e6ae6f90707f897aea16f193e20b6e4e9d4878cfa6f32fcfbc0cc42',
                   'uid': 1000},
 'native1_launch': {'bytes': 422,
                    'gid': 1000,
                    'identity': [27, 284414],
                    'mode': 384,
                    'path': '/workspace/work/google407/native-launch-once.json',
                    'sha256': '97cca2dc65115215e041067012d5384edde450b0bd8d333a84eaf064ecf41bcd',
                    'uid': 1000},
 'native2_claim': {'bytes': 468,
                   'gid': 1000,
                   'identity': [34, 141990],
                   'mode': 384,
                   'path': '/tmp/youthopps-google-publisher-v1-1000/issue407-native2.claim',
                   'sha256': '1928de7cc771bbd1aaa53d87663fb183bd135d0bbe81332530a063d5574a855e',
                   'uid': 1000},
 'native2_launch': {'bytes': 463,
                    'gid': 1000,
                    'identity': [27, 284508],
                    'mode': 384,
                    'path': '/workspace/work/google407/native2-launch-once.json',
                    'sha256': 'b8967fdfd83acb362af2daf86164ebbc2b8501b5aa55269a815084ec923a3f7b',
                    'uid': 1000},
 'native2_result': {'bytes': 373,
                    'gid': 1000,
                    'identity': [27, 284510],
                    'mode': 384,
                    'path': '/workspace/work/google407/native2-launch-result.json',
                    'sha256': '43ef392ca2c9b178cd24821030b426c3f22dbed3785f18294b5ac23a90e2b008',
                    'uid': 1000},
 'outside_attempt': {'bytes': 1131,
                     'gid': 1000,
                     'identity': [27, 284411],
                     'mode': 384,
                     'path': '/workspace/work/google407/initialization-process-once.json',
                     'sha256': '668f7f23c5f0579070cf1258665c4f314fed65ae46e6010658aa2b988526af4f',
                     'uid': 1000},
 'outside_proof': {'bytes': 1916,
                   'gid': 1000,
                   'identity': [27, 284379],
                   'mode': 384,
                   'path': '/workspace/work/google407/initialization-success-proof.json',
                   'sha256': 'a4eefbcd200b5871e972fc386b3c0ab5df688e60aa5a4836f419270636a08373',
                   'uid': 1000},
 'root_observed_zero': {'bytes': 444,
                        'gid': 1000,
                        'identity': [27, 284413],
                        'mode': 384,
                        'path': '/workspace/work/google407/root-observed-initialization-success.json',
                        'sha256': '50c9fa42a1147eb2548305ed678630725e141a5429a695eaaf949d90d20199eb',
                        'uid': 1000},
 'root_settlement_witness': {'bytes': 1560,
                             'gid': 1000,
                             'identity': [27, 284591],
                             'mode': 384,
                             'path': '/workspace/work/google407/root-observed-v5-settlement-success.json',
                             'sha256': 'bfb35c34119027ac7e848a9c7aa880ef747f43151a4859192cdf3f1ecfb7e18f',
                             'uid': 1000},
 'settlement_attempt': {'bytes': 303,
                        'gid': 1000,
                        'identity': [27, 284563],
                        'mode': 384,
                        'path': '/workspace/work/google407/settlement-v5-process-once.json',
                        'sha256': '18f68b8ff21d0736ce35989c9314acd6fa3bbd472e9311b1c13fa968f055c0be',
                        'uid': 1000},
 'settlement_claim': {'bytes': 839,
                      'gid': 1000,
                      'identity': [34, 185938],
                      'mode': 384,
                      'path': '/tmp/youthopps-google-publisher-v1-1000/issue407-native2-logical-settlement.claim',
                      'sha256': '60c167d5850a5b72fb7f82887828966c4a352c04dd78c8ccec026584b4b2d9f9',
                      'uid': 1000},
 'settlement_proof': {'bytes': 2770,
                      'gid': 1000,
                      'identity': [27, 284589],
                      'mode': 384,
                      'path': '/workspace/work/google407/settlement-v5-success-proof.json',
                      'sha256': '86089d0d3aa73ec5b8b75d6fc9781d8f2cbaad31af1e7103a1fd657cca94347d',
                      'uid': 1000}}

_GOOGLE407_NATIVE3_NAMESPACE = {'collection_lock': {'bytes': 0,
                     'gid': 1000,
                     'identity': [34, 128491],
                     'mode': 384,
                     'path': '/tmp/youthopps-google-publisher-v1-1000/collection.lock',
                     'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                     'uid': 1000},
 'family': {'gid': 1000,
            'identity': [34, 128489],
            'mode': 448,
            'path': '/tmp/youthopps-google-publisher-v1-1000',
            'uid': 1000},
 'outside_root': {'gid': 1000,
                  'identity': [27, 283900],
                  'mode': 448,
                  'path': '/workspace/work/google407',
                  'uid': 1000},
 'state_lock': {'bytes': 0,
                'gid': 1000,
                'identity': [34, 128490],
                'mode': 384,
                'path': '/tmp/youthopps-google-publisher-v1-1000/state.lock',
                'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
                'uid': 1000},
 'temporary_parent': {'gid': 65534,
                      'identity': [34, 1],
                      'mode': 1023,
                      'path': '/tmp',
                      'uid': 65534}}


def read_native3_authority(value):
    """Read one frozen native authority without adopting a new generation."""
    descriptor = os.open(value["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    primary = None
    try:
        before = os.fstat(descriptor)
        named = os.stat(value["path"], follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != value["uid"]
            or before.st_uid != os.getuid()
            or before.st_gid != value["gid"]
            or stat.S_IMODE(before.st_mode) != value["mode"]
            or value["mode"] != 0o600
            or (before.st_dev, before.st_ino) != tuple(value["identity"])
            or (named.st_dev, named.st_ino) != tuple(value["identity"])
        ):
            raise AdapterError("Original Google native3 authority differs", "access")
        raw = os.pread(descriptor, 16385, 0)
        after = os.fstat(descriptor)
        named = os.stat(value["path"], follow_symlinks=False)
        if (
            journal_version(before) != journal_version(after)
            or journal_version(named) != journal_version(after)
            or len(raw) != value["bytes"]
            or len(raw) > 16384
            or hashlib.sha256(raw).hexdigest() != value["sha256"]
        ):
            raise AdapterError("Google native3 authority generation differs", "access")
        return raw
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            if primary is None:
                raise


def verify_native3_namespace():
    """Reject replacement of the original publisher or outside namespace."""
    for key in ("temporary_parent", "family", "outside_root"):
        value = _GOOGLE407_NATIVE3_NAMESPACE[key]
        descriptor = os.open(
            value["path"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        primary = None
        try:
            info = os.fstat(descriptor)
            named = os.stat(value["path"], follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_nlink < 2
                or (info.st_dev, info.st_ino) != tuple(value["identity"])
                or (named.st_dev, named.st_ino) != tuple(value["identity"])
                or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
                != (value["uid"], value["gid"], value["mode"])
            ):
                raise AdapterError("Original Google native3 namespace differs", "access")
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise
    for key in ("state_lock", "collection_lock"):
        read_native3_authority(_GOOGLE407_NATIVE3_NAMESPACE[key])


def verify_native3_witness():
    """Require the actual original writer and ROOT's post-finalizer witness."""
    values = _GOOGLE407_NATIVE3_LINEAGE
    observed = json.loads(read_native3_authority(values["root_settlement_witness"]))
    proof_raw = read_native3_authority(values["settlement_proof"])
    proof = json.loads(proof_raw)
    attempt = json.loads(read_native3_authority(values["settlement_attempt"]))
    result = observed["result"]
    if (
        type(observed["outcome"]) is not int or observed["outcome"] != 0
        or observed["qualified_success"] is not True
        or type(observed["child_exit"]) is not int or observed["child_exit"] != 0
        or observed["proof_sha256"] != hashlib.sha256(proof_raw).hexdigest()
        or observed["proof_identity"] != values["settlement_proof"]["identity"]
        or observed["child_pid"] != proof["child_pid"]
        or observed["supervisor_pid"] != attempt["pid"]
        or proof["attempt"] != attempt
        or type(proof["original_child_exit"]) is not int
        or proof["original_child_exit"] != 0
        or proof["original_stderr"] != ""
        or proof["original_stdout"] != (canonical_state(result) + b"\n").decode()
        or proof["result"] != result
        or result["state_sha256"] != _GOOGLE407_NATIVE3_FLOOR_SHA
        or result["state_identity"] != values["journal"]["identity"]
        or result["observations"] != 126
        or result["publisher_HTTP"] != 0
        or result["native_execution"] != 0
        or result["logical_owner"] != _GOOGLE407_NATIVE3_FLOOR["completed_flight_id"]
        or result["physical_receipt"] != _GOOGLE407_NATIVE3_FLOOR["completed_request_id"]
        or observed["root_decision"] != result["evidence"]
        or proof["root_decision"] != observed["root_decision"]
        or proof["writer_sha256"] != result["writer_sha256"]
        or attempt["writer_sha256"] != result["writer_sha256"]
    ):
        raise AdapterError("Original Google settlement witness differs", "access")


def claim_local_continuation():
    """One finite native3 claim after the exact separately observed settlement."""
    global _COLLECTION_LOCK_FD, _COLLECTION_FLOCK_GUARD, _COLLECTION_GUARD_ID
    evidence = _GOOGLE407_CONTINUATION
    if (
        not isinstance(evidence, str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/407"
            r"#issuecomment-[1-9][0-9]*", evidence
        )
        or evidence.endswith(("-6956512566", "-6958071051", "-6959251891", "-6960520829"))
    ):
        raise AdapterError("Google407 native3 continuation is OFF", "access")
    with open("/etc/machine-id", "rb") as stream:
        machine = hashlib.sha256(stream.read(4097)).hexdigest()
    if machine != "11463ef315e49ed08fa5dcf991991d21efa26dfe0b3fff9a3b5d75d93ec973ab":
        raise AdapterError("Google native3 machine differs", "access")
    verify_native3_namespace()
    verify_native3_witness()
    floor = validate_budget(_GOOGLE407_NATIVE3_FLOOR)
    if hashlib.sha256(canonical_state(floor)).hexdigest() != _GOOGLE407_NATIVE3_FLOOR_SHA:
        raise AdapterError("Google native3 full126 MAX differs", "access")
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        descriptor = journal_lifecycle_open(
            os.path.join(os.path.dirname(_STATE_PATH), "collection.lock"), False
        )
        _COLLECTION_LOCK_FD = descriptor
        identity = _JOURNAL_ACQUIRED_IDS[descriptor]
        journal_descriptor(descriptor, identity)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_bind_collection(descriptor, identity)
        _COLLECTION_FLOCK_GUARD = os.dup(descriptor)
        _COLLECTION_GUARD_ID = identity
    for key in ("initializer_claim", "native1_claim", "native2_claim", "settlement_claim"):
        value = _GOOGLE407_NATIVE3_LINEAGE[key]
        raw = read_native3_authority(value)
        parent = _STATE_PARENT_FD if key == "initializer_claim" else _STATE_DIR_FD
        _LOCAL_CLAIMS[os.path.basename(value["path"])] = (
            parent, tuple(value["identity"]), raw
        )
    with open(__file__, "rb") as stream:
        source = stream.read(1000001)
    if len(source) > 1000000:
        raise AdapterError("Google native3 source too large", "access")
    with locked_budget():
        state = load_budget()
        if (
            state != floor
            or state["in_flight"] or state["blocked"]
            or _JOURNAL_ID != tuple(_GOOGLE407_NATIVE3_LINEAGE["journal"]["identity"])
            or hashlib.sha256(journal_read()).hexdigest() != _GOOGLE407_NATIVE3_FLOOR_SHA
        ):
            raise AdapterError("Exact settled Google native3 boundary differs", "access")
        verify_native3_namespace()
        verify_native3_witness()
        expected = {
            "family": FAMILY, "source_sha256": hashlib.sha256(source).hexdigest(),
            "state_sha256": _GOOGLE407_NATIVE3_FLOOR_SHA, "evidence": evidence,
            "root_settlement_witness_sha256": _GOOGLE407_NATIVE3_LINEAGE[
                "root_settlement_witness"]["sha256"],
            "settlement_proof_sha256": _GOOGLE407_NATIVE3_LINEAGE[
                "settlement_proof"]["sha256"],
        }
        descriptor = secure_file("issue407-native3.claim", os.O_RDWR | os.O_CREAT | os.O_EXCL)
        primary = None
        try:
            raw = canonical_state(expected)
            if os.write(descriptor, raw) != len(raw):
                raise AdapterError("Short Google native3 claim", "access")
            os.fsync(descriptor)
            if os.pread(descriptor, len(raw) + 1, 0) != raw:
                raise AdapterError("Original Google native3 claim differs", "access")
            os.fsync(_STATE_DIR_FD)
            info = os.fstat(descriptor)
            _LOCAL_CLAIMS["issue407-native3.claim"] = (
                _STATE_DIR_FD, (info.st_dev, info.st_ino), raw
            )
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise
        journal_verify_collection()
        verify_local_claims()


if __name__ == "__main__":
    raise SystemExit(main())
