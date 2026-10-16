"""Discover FRSE funding, training, jobs and calls from current public HTML."""

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
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "pl-frse"
SOURCE_URL = "https://www.frse.org.pl/"
WEBSITE_URL = "https://www.frse.org.pl/"
LANGUAGE = "pl"
PUBLISHER_COUNTRY = "PL"
PUBLISHER_TYPE = "education-foundation"
ATTRIBUTION = (
    "Fundacja Rozwoju Systemu Edukacji (FRSE), https://www.frse.org.pl/; "
    "official Erasmus+, European Solidarity Corps and named FRSE programmes"
)

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
FAMILY = "youthopps-frse-publisher-v1"
FAMILY_SOURCES = {"pl-frse"}
_LOCAL_WORKFLOW_ID = 378391590
_LOCAL_START_VERIFIED = False
COLLECTION_TIMEOUT = 1800
RUN_TIMEOUT = 2190
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "frse-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None


_ATTEMPT_HEADS = {}
_PAGE_COMPLETED = None
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False


def fetch_inventory_page(fetch, *args, **kwargs):
    """Mark real inventory requests; cached documents perform no transport."""
    return fetch(*args, inventory=True, **kwargs)


def wait_inventory_page():
    """Keep every physical inventory hop after the previous completion."""
    with locked_budget():
        interval = max(load_budget().get("page_interval", 30), 30)
    if _PAGE_COMPLETED is not None:
        while True:
            check_deadline()
            wait = _PAGE_COMPLETED + interval - time.monotonic()
            if wait <= 0:
                break
            if wait + 120 > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Inventory spacing exceeds phase budget", "access"
                )
            time.sleep(wait)


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
        journal_release()


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
    if not isinstance(state, dict) or set(state) - {
        "interval",
        "page_interval",
        "rate_failures",
        "legacy_rate_history_unknown",
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
    if (
        type(state.get("legacy_rate_history_unknown", True)) is not bool
        or type(state.get("rate_failures", 0)) is not int
        or not 0 <= state.get("rate_failures", 0) <= 100
        or not numeric(state.get("page_interval", 12))
        or state.get("page_interval", 12) < 12
    ):
        raise AdapterError("Invalid adaptive publisher limits", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    state.setdefault("legacy_rate_history_unknown", True)
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "legacy_rate_history_unknown": True,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_INITIALIZING
    if _STATE_PATH:
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    created = False
    try:
        os.mkdir(directory, 0o700)
        created = True
    except FileExistsError:
        pass
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    filename = "state.json"
    _STATE_PATH = os.path.join(directory, filename)
    _STATE_LOCK = os.path.join(directory, "state.lock")
    _STATE_INITIALIZING = created


def locked_budget():
    """Lock the retained original journal authority for a budget transaction."""
    return JournalLease()


def load_budget():
    try:
        raw = journal_read()
        if not raw and _JOURNAL_NEW and _JOURNAL_BOOTSTRAP_ALLOWED:
            return empty_budget(time.time())
        return validate_budget(json.loads(raw))
    except BaseException:
        journal_failure()
        raise


def save_budget(state):
    """Commit only through the retained, locked original RW descriptor."""
    journal_write(state)


def pace():
    """Serialize publisher starts under all retained physical constraints."""
    global _PUBLISHER_INTERVAL
    check_deadline()
    with locked_budget():
        state = load_budget()
        if state["blocked"]:
            raise AdapterError(
                ("Publisher backoff requires reviewed recovery"), ("access")
            )
        _PUBLISHER_INTERVAL = max(
            _PUBLISHER_INTERVAL, state.get("interval", 12.2), 12.2
        )
        state["interval"] = _PUBLISHER_INTERVAL
        if state.get("rate_failures", 0) >= 100:
            raise AdapterError("Rate-limit counter requires review", "access")
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
        state["observed_at"] = now
        state["not_before"] = max(
            state["not_before"], now + _PUBLISHER_INTERVAL
        )
        save_budget(state)


@contextlib.contextmanager
def publisher_attempt(publisher, inventory=False):
    """Retain completion gates around each actual publisher transport."""
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
    global _PAGE_COMPLETED, _LOCAL_START_VERIFIED
    if not publisher:
        yield
        return
    if _COLLECTION_LOCK_FD is None or _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Exclusive healthy publisher collector required", "access"
        )
    check_deadline()
    if os.environ.get("GITHUB_ACTIONS") != "true" and not _LOCAL_START_VERIFIED:
        verify_local_workflow_pause()
        _LOCAL_START_VERIFIED = True
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
        primary = sys.exc_info()[1]
        if not _JOURNAL_FATAL:
            try:
                completed = time.monotonic()
                _PUBLISHER_COMPLETED_MONOTONIC = completed
                if inventory:
                    _PAGE_COMPLETED = completed
                _PUBLISHER_GATE_FAILED = True
                with locked_budget():
                    state = load_budget()
                    now = time.time()
                    state["not_before"] = max(
                        state["not_before"],
                        now
                        + max(
                            _PUBLISHER_INTERVAL,
                            state.get("interval", 12.2),
                            12.2,
                        ),
                    )
                    state["observed_at"] = now
                    save_budget(state)
                _PUBLISHER_GATE_FAILED = False
            except BaseException:
                journal_failure()
                if primary is None:
                    raise
        elif primary is None:
            raise AdapterError("Publisher journal gate is sticky", "access")


def record_retry_after(value):
    """Retain valid embargoes; unusable timing alone does not imply denial."""
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
            if not numeric(until) or until <= now:
                raise ValueError("Invalid embargo")
            datetime.fromtimestamp(until, timezone.utc)
            state["not_before"] = max(state["not_before"], until)
        except Exception:
            pass
        state["observed_at"] = now
        save_budget(state)


def retain_rate_limit():
    """Persist a recoverable restriction before consuming a 429 body."""
    with locked_budget():
        state = load_budget()
        count = state.get("rate_failures", 0) + 1
        interval = max(state.get("interval", 6) * 2, 12.2)
        page_interval = max(state.get("page_interval", 12) * 2, 30)
        now = time.time()
        if count > 100 or not numeric(interval) or not numeric(page_interval):
            raise AdapterError("Rate-limit state exceeds safe range", "access")
        state["rate_failures"] = count
        state["interval"] = interval
        state["page_interval"] = page_interval
        state["not_before"] = max(
            state["not_before"],
            now + 1800 * 2 ** (count - 1),
            now + interval,
            now + page_interval,
        )
        state["observed_at"] = now
        save_budget(state)


class RateLimitText(HTMLParser):
    """Read visible primary refusals, excluding unrelated ancillary notices."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack, self.head_title_parts = [], [], []
        self.head_seen = self.head_allowed = False
        self.title_depth = None
        self.feed(document)
        self.close()

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
        explicit_suppressed = (
            "hidden" in values
            or (values.get("aria-hidden") or "").strip().lower() == "true"
            or declarations.get("display") == "none"
            or declarations.get("visibility") == "hidden"
        )
        if (
            tag == "head"
            and not self.head_seen
            and all(
                name == "html" and not suppressed and not visibility
                for name, suppressed, visibility in self.stack
            )
        ):
            self.head_seen = True
            self.head_allowed = not explicit_suppressed
        if (
            tag == "title"
            and self.head_allowed
            and self.stack
            and self.stack[-1][0] == "head"
            and not explicit_suppressed
        ):
            self.title_depth = len(self.stack) + 1
        suppressed = (
            tag
            in {
                "head",
                "title",
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

    def handle_endtag(self, tag):
        if tag == "title":
            self.title_depth = None
        if tag == "head":
            self.head_allowed = False
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                del self.stack[position:]
                break

    def handle_data(self, value):
        if (
            self.title_depth is not None
            and len(self.stack) == self.title_depth
            and self.stack[-1][0] == "title"
        ):
            self.head_title_parts.append(value)
        if not self.stack or not any(self.stack[-1][1:]):
            self.parts.append(value)


def visible_access_challenge(body, challenge_only=False):
    """Recognize primary HTML refusals, not arbitrary JS/JSON/PDF strings."""
    document = body.decode("utf-8-sig", "replace")
    if not document.lstrip().startswith("<"):
        return False

    class RefusalText(RateLimitText):
        """Retain already-visible headlines as independent refusal evidence."""

        def __init__(self, value):
            self.headlines = []
            self.heading = None
            super().__init__(value)

        def handle_starttag(self, tag, attrs):
            super().handle_starttag(tag, attrs)
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.headlines.append([])
                self.heading = (tag, len(self.headlines) - 1)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            if self.heading is not None and self.heading[0] == tag:
                self.heading = None

        def handle_data(self, value):
            before = len(self.parts)
            super().handle_data(value)
            if self.heading is not None and len(self.parts) > before:
                self.headlines[self.heading[1]].append(value)

    parser = RefusalText(document)

    # This support instruction describes a possible error, not a refusal.
    # Remove only its own mention; adjacent denials and real HEAD titles stay.
    def without_instruction(parts):
        text = " ".join(" ".join(parts).lower().split())
        return re.sub(
            r"\bif you see access denied\s*,?\s*"
            r"(?:please\s+)?contact support\b",
            " ",
            text,
        )

    units = [parser.parts, parser.head_title_parts] + parser.headlines
    text = " ".join(without_instruction(parts) for parts in units)
    phrases = (
        "verify you are human",
        "verify that you are human",
        "human verification",
        "confirm you are human",
        "confirm that you are human",
        "confirm you are a human",
        "confirm that you are a human",
        "complete a captcha",
        "complete the captcha",
        "enable javascript and cookies to continue",
        "checking your browser",
    )
    if not challenge_only:
        phrases += (
            "access denied",
            "permission denied",
            "request rejected",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    explicit_title = without_instruction(parser.head_title_parts)
    return any(phrase in text for phrase in phrases) or bool(
        re.search(
            r"just a moment|attention required|access denied|captcha",
            explicit_title,
        )
    )


def retain_publisher_hold():
    """Retain a genuine content refusal without clearing timed restrictions."""
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
        state["observed_at"] = time.time()
        save_budget(state)


def reject_visible_challenge(
    body, headers, *, challenge_only=False, status=None
):
    """Inspect actual HTML, including obvious markup with a wrong MIME."""
    prefix = body[:512].decode("utf-8", "replace")
    obvious_html = re.match(
        r"^[\s\ufeff]*(?:<!doctype\s+html\b|"
        r"<(?:html|head|title|body|main|article|section|div|p|form|h[1-6])"
        r"(?:\s|>))",
        prefix,
        re.I,
    )
    if (
        "html" not in headers.get("Content-Type", "").lower()
        and not obvious_html
    ):
        return
    if visible_access_challenge(body, challenge_only=challenge_only):
        retain_publisher_hold()
        raise AdapterError(
            "Visible publisher challenge or denial", "access", status
        )


def stop_rate_limit(response, *, byte_limit=65536):
    """Stop without reading the received rate-limit body."""
    try:
        raise HeaderRateLimitStop(
            "Publisher stopped: HTTP 429", "access", 429
        )
    finally:
        with locked_budget():
            state = load_budget()
            now = time.time()
            state["not_before"] = max(
                state["not_before"],
                now + state.get("interval", 6),
                now + state.get("page_interval", 12),
            )
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


def header_rate_limit_failure_guard(function):
    """Preserve a marked HTTP 429 error through failing cleanup."""
    def invoke(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            current, seen = error, set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                if getattr(current, "_header429_unassessed", False):
                    raise current
                current = current.__context__
            raise
    invoke.__name__ = function.__name__
    invoke.__doc__ = function.__doc__
    invoke.__wrapped__ = function
    return invoke


@header_rate_limit_failure_guard
def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
    robots=False,
    inventory=False,
):
    """Bound each response and keep purpose through physical redirects."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            parsed = urllib.parse.urlsplit(current)
            if (
                method != "GET"
                or payload is not None
                or (
                    robots
                    and (
                        parsed.path != "/robots.txt"
                        or parsed.query
                        or inventory
                    )
                )
            ):
                raise AdapterError(
                    "Invalid publisher request purpose", "access"
                )
            if inventory:
                wait_inventory_page()
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        with publisher_attempt(publisher, inventory):
            try:
                if publisher:
                    journal_before_wire()
                response = _OPENER.open(
                    request, timeout=request_timeout(publisher)
                )
            except urllib.error.HTTPError as error:
                response = error
            except (urllib.error.URLError, OSError) as error:
                if publisher:
                    raise publisher_transport_error(error, current) from None
                raise
            with (
                header_rate_limit_response(response)
                if publisher and response.status == 429
                else response
            ):
                status, response_headers = response.status, response.headers
                if publisher:
                    record_retry_after(response_headers.get("Retry-After"))
                    if status == 429:
                        retain_rate_limit()
                        stop_rate_limit(response, byte_limit=byte_limit)
                    if not robots and status in (401, 403, 451):
                        retain_publisher_hold()
                location = response_headers.get("Location")
                if publisher and robots and 400 <= status < 500:
                    # REP is unavailable, with no invented content refusal.
                    body = b""
                else:
                    limit = (
                        min(byte_limit, 65536)
                        if publisher and status in (301, 302, 303, 307, 308)
                        else byte_limit
                    )
                    declared = response_headers.get("Content-Length")
                    if declared and (
                        not declared.isdigit() or int(declared) > limit
                    ):
                        raise AdapterError(
                            "Oversized or invalid response length", "fetch"
                        )
                    try:
                        body = response.read(limit + 1)
                    except (urllib.error.URLError, OSError) as error:
                        if publisher:
                            raise publisher_transport_error(
                                error, current
                            ) from None
                        raise
                    if (
                        len(body) > limit
                        or declared
                        and len(body) != int(declared)
                    ):
                        raise AdapterError(
                            "Incomplete or oversized response", "fetch"
                        )
                    if (
                        response_headers.get("Content-Encoding", "identity")
                        != "identity"
                    ):
                        raise AdapterError(
                            "Unsupported response content encoding", "fetch"
                        )
                    if publisher:
                        reject_visible_challenge(
                            body, response_headers, status=status
                        )
                if publisher and not robots and status in (401, 403, 451):
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
                        "Publisher refused access: HTTP "
                        + str(status)
                        + ": "
                        + provenance,
                        "access",
                        status,
                    )
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            old, new = urllib.parse.urlsplit(current), urllib.parse.urlsplit(
                destination
            )
            if publisher:
                public_url(destination)
                if (
                    old.scheme == "https"
                    and new.scheme != "https"
                    or robots
                    and (new.path != "/robots.txt" or new.query)
                ):
                    raise AdapterError(
                        "Invalid publisher redirect purpose", "access"
                    )
                if not robots:
                    check_robots(destination)
            else:
                if new.scheme != "https" or new.username or new.password:
                    raise AdapterError(
                        "Unsafe authenticated redirect", "publish"
                    )
                if new.netloc != old.netloc:
                    headers = {
                        key: value
                        for key, value in headers.items()
                        if key.lower() != "authorization"
                    }
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    raise AdapterError(
                        "Unexpected GitHub API redirect", "publish"
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
    import urllib.robotparser

    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.path == "/robots.txt":
        return
    if origin not in _ROBOTS:
        status, _, body = request_bytes(
            origin + "/robots.txt", publisher=True, robots=True
        )
        if 400 <= status < 500 and status != 429:
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


def fetch_public(url, *, inventory=False):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(
            reviewed, publisher=True, inventory=inventory
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
            continue
        key = next(
            (
                key
                for key, info in ACCESS_POLICIES.items()
                if info["url"] == url
            ),
            "unmapped-public-input",
        )
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise AdapterError(
            f"Required publisher input {key} returned HTTP {status}: "
            + provenance,
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
            _WORKFLOW_GITHUB
            + path
            + ("&" if "?" in path else "?")
            + "_fresh="
            + str(time.time_ns()),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
            },
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            ("Workflow pacing state unavailable"), ("access")
        ) from None


def verify_local_workflow_pause(definition=None):
    """Require maintainer coordination before local publisher access."""
    if definition is None:
        definition = workflow_api(
            "/actions/workflows/fetch-" + SOURCE_ID + ".yml"
        )
    if (
        not isinstance(definition, dict)
        or type(definition.get("id")) is not int
        or definition["id"] != _LOCAL_WORKFLOW_ID
        or definition.get("path")
        != ".github/workflows/fetch-" + SOURCE_ID + ".yml"
        or definition.get("state") != "disabled_manually"
    ):
        raise AdapterError(
            "Local collection requires its exact manually paused workflow",
            "access",
        )


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


def attempt_completion(identity, attempt, head):
    """Use actual exact-attempt job completion, never mutable run updated_at."""
    total, jobs_seen = None, set()
    finished = []
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}"
        )
        count, jobs = result.get("total_count"), result.get("jobs")
        if (
            type(count) is not int
            or count <= 0
            or not isinstance(jobs, list)
            or len(jobs) > 100
            or total is not None
            and total != count
        ):
            raise ValueError("Incomplete exact-attempt job inventory")
        total = count
        for job in jobs:
            if (
                not isinstance(job, dict)
                or type(job.get("id")) is not int
                or job["id"] <= 0
                or job["id"] in jobs_seen
                or type(job.get("run_id")) is not int
                or job["run_id"] != identity
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt
                or job.get("head_sha") != head
                or job.get("head_branch") != "main"
                or job.get("status") != "completed"
            ):
                raise ValueError("Untrusted exact-attempt job")
            jobs_seen.add(job["id"])
            value = datetime.fromisoformat(
                job["completed_at"].replace("Z", "+00:00")
            )
            if value.utcoffset() is None:
                raise ValueError("Missing job completion timezone")
            finished.append(value.timestamp())
        if len(jobs_seen) == total:
            return max(finished)
        if len(jobs_seen) > total or len(jobs) != 100:
            raise ValueError("Truncated exact-attempt job inventory")
    raise ValueError("Exact-attempt job inventory exceeds bound")


def completed_identity(run, expected=None, revision=None):
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
            or type(run.get("workflow_id")) is not int
            or run["workflow_id"] <= 0
            or revision is not None
            and (run["head_sha"], run["workflow_id"]) != revision
            or expected
            and (identity, attempt, source) != expected
        ):
            raise ValueError("Untrusted completed attempt")
        value = datetime.fromisoformat(
            run["run_started_at"].replace("Z", "+00:00")
        )
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        finished = attempt_completion(identity, attempt, run["head_sha"])
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
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
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current_id, current_attempt = (
        current_run_identity() if hosted else (None, None)
    )
    definition = workflow_api("/actions/workflows/fetch-" + SOURCE_ID + ".yml")
    expected_path = ".github/workflows/fetch-" + SOURCE_ID + ".yml"
    if (
        type(definition.get("id")) is not int
        or definition["id"] <= 0
        or definition.get("path") != expected_path
    ):
        raise AdapterError("Untrusted family workflow definition", "access")
    if not hosted:
        verify_local_workflow_pause(definition)
    candidates = []
    current_seen = False
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        route = (
            "/actions/runs"
            if hosted
            else ("/actions/workflows/" + str(definition["id"]) + "/runs")
        )
        result = workflow_api(route + f"?per_page=100&page={page}")
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
                if not hosted:
                    raise AdapterError(
                        "Unexpected local family source", "access"
                    )
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
            if run.get("workflow_id") != definition["id"]:
                raise AdapterError(
                    "Family workflow identity mismatch", "access"
                )
            identity, attempt = run.get("id"), run.get("run_attempt")
            if (
                type(identity) is not int
                or identity <= 0
                or type(attempt) is not int
                or attempt <= 0
            ):
                raise AdapterError("Invalid family run identity", "access")
            if hosted and identity == current_id:
                current_seen = True
                if (
                    run.get("head_sha") != os.environ.get("GITHUB_SHA")
                    or not re.fullmatch(
                        r"[0-9a-f]{40}", run.get("head_sha", "")
                    )
                    or type(run.get("workflow_id")) is not int
                    or run["workflow_id"] <= 0
                    or run.get("path")
                    != ".github/workflows/fetch-" + SOURCE_ID + ".yml"
                ):
                    raise AdapterError(
                        "Current workflow binding mismatch", "access"
                    )
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
                            (run.get("head_sha"), run.get("workflow_id")),
                        )
                    )
                continue
            if not hosted and run.get("status") != "completed":
                raise AdapterError(
                    "Local collection requires terminal family history",
                    "access",
                )
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
                            (run.get("head_sha"), run.get("workflow_id")),
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
            if hosted and not current_seen:
                raise AdapterError(
                    "Current workflow missing from history", "access"
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
    global _JOURNAL_BOOTSTRAP_ALLOWED
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Mature family history is missing", "access")
    run_id, attempt, source = latest
    expected = _ARTIFACT_NAME + "-" + str(attempt)
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or result.get("total_count") != len(artifacts)
        or len(artifacts) > 100
    ):
        raise AdapterError("Incomplete family artifact inventory", "access")
    matches = [a for a in artifacts if a.get("name") == expected]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest family pacing artifact missing or expired", "access"
        )
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or (
            artifact.get("workflow_run", {}).get("head_sha")
            != _ATTEMPT_HEADS.get((run_id, attempt))
        )
        or ((run_id, attempt) not in _ATTEMPT_HEADS)
        or (
            not re.fullmatch(
                "sha256:[0-9a-f]{64}", artifact.get("digest") or ""
            )
        )
        or (not numeric(artifact.get("size_in_bytes")))
        or (artifact["size_in_bytes"] > 32768)
    ):
        raise AdapterError("Invalid family artifact binding", "access")
    incoming = download_inert_artifact(
        latest, artifact["id"], artifact.get("digest")
    )
    _JOURNAL_BOOTSTRAP_ALLOWED = (
        _STATE_INITIALIZING
        and not _JOURNAL_SAVE_ATTEMPTED
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined["not_before"] = max(
            local["not_before"], incoming["not_before"], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined["interval"] = max(
            local.get("interval", 6), incoming.get("interval", 6), 12.2
        )
        combined["legacy_rate_history_unknown"] = local.get(
            "legacy_rate_history_unknown", True
        ) or incoming.get("legacy_rate_history_unknown", True)
        combined["page_interval"] = max(
            local.get("page_interval", 12),
            incoming.get("page_interval", 12),
            30,
        )
        combined["rate_failures"] = max(
            local.get("rate_failures", 0), incoming.get("rate_failures", 0)
        )
        combined["starts"] = sorted(set(local["starts"] + incoming["starts"]))[
            -10:
        ]
        save_budget(combined)
    _RESTORED, _STATE_TRUSTED = (True, True)


def reject_failed_completion_export(path):
    """Invalidate an owned stale artifact after lost completion persistence."""
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
        raise AdapterError(
            "Publisher completion gate cannot be exported", "access"
        )
    finally:
        os.close(parent)


def export_family_artifact():
    """Export the unchanged owned envelope under retained journal authority."""
    path = os.environ.get("FRSE_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
        journal_invalidate_export()
        return

    def payload():
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
        return envelope

    journal_export_envelope(
        path,
        payload,
        atomic=False,
        signal=False,
    )


def preserve_legacy_pacing():
    """Lock bound legacy state through read and merge, retaining its inode."""
    descriptors = []
    try:
        parent = os.open(
            tempfile.gettempdir(),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        descriptors.append(parent)
        for name in ("youthopps-source-triage", "37"):
            try:
                directory = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
            except FileNotFoundError:
                return
            descriptors.append(directory)
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AdapterError("Unsafe legacy pacing directory", "access")
            parent = directory
        try:
            descriptor = os.open(
                "pacing.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent
            )
        except FileNotFoundError:
            return
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe legacy FRSE pacing state", "access")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AdapterError(
                "Legacy FRSE collector active", "access"
            ) from None
        raw = os.read(descriptor, 16385)
        try:
            legacy = json.loads(raw)
            if (
                len(raw) > 16384
                or not isinstance(legacy, dict)
                or not {"starts", "interval", "until"} <= set(legacy)
                or set(legacy) - {"starts", "interval", "until", "blocked"}
                or not numeric(legacy["until"])
                or not numeric(legacy["interval"])
                or legacy["interval"] < 6
                or type(legacy.get("blocked", False)) is not bool
                or not isinstance(legacy["starts"], list)
                or len(legacy["starts"]) > 10
                or any(not numeric(value) for value in legacy["starts"])
                or legacy["starts"] != sorted(set(legacy["starts"]))
                or any(value > time.time() + 1 for value in legacy["starts"])
            ):
                raise ValueError("Invalid legacy timing history")
        except (ValueError, TypeError, KeyError):
            raise AdapterError(
                "Invalid legacy FRSE pacing state", "access"
            ) from None
        with locked_budget():
            state = load_budget()
            state["interval"] = max(
                state.get("interval", 6), legacy["interval"]
            )
            state["not_before"] = max(state["not_before"], legacy["until"])
            state["blocked"] = state["blocked"] or legacy.get("blocked", False)
            state["starts"] = sorted(set(state["starts"] + legacy["starts"]))[
                -10:
            ]
            if state["starts"]:
                state["not_before"] = max(
                    state["not_before"],
                    state["starts"][-1] + state["interval"],
                )
            state["observed_at"] = time.time()
            save_budget(state)

    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _PAGE_COMPLETED, _PUBLISHER_INTERVAL, _LOCAL_START_VERIFIED
    if _JOURNAL_FATAL or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher journal gate is sticky", "access")
    _LOCAL_START_VERIFIED = False
    _COLLECTION_STARTED = time.time()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(os.path.dirname(_STATE_PATH), "collection.lock")
        descriptor = journal_lifecycle_open(path, _STATE_INITIALIZING)
        try:
            journal_descriptor(descriptor, _JOURNAL_ACQUIRED_IDS[descriptor])
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal_bind_collection(
                descriptor, _JOURNAL_ACQUIRED_IDS[descriptor]
            )
        except BaseException as error:
            journal_failure()
            with contextlib.suppress(BaseException):
                journal_close_acquired(
                    descriptor, _JOURNAL_ACQUIRED_IDS.get(descriptor)
                )
            if isinstance(error, OSError):
                raise AdapterError(
                    "Another local family collector is active", "access"
                ) from None
            raise
        _COLLECTION_LOCK_FD = descriptor
    restore_family_artifact()
    preserve_legacy_pacing()
    with locked_budget():
        state = load_budget()
        state["interval"] = max(state.get("interval", 6), 12.2)
        state["page_interval"] = max(state.get("page_interval", 12), 30)
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        state["not_before"] = max(state["not_before"], _COLLECTION_STARTED + 60)
        state["observed_at"] = time.time()
        save_budget(state)
    _PAGE_COMPLETED = time.monotonic()
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

    validate_owned_records(records)


def record_identity(url, reference=None):
    """Keep original URL hashes and literal recruitment-reference identities."""
    identity = url + ("|" + reference if reference else "")
    return hashlib.sha256((SOURCE_ID + "|" + identity).encode()).hexdigest()[
        :24
    ]


def validate_owned_records(records):
    """Reject foreign identities and impossible owned record chronologies."""
    for record in records:
        public_url(record["url"])
        reference = record.get("reference")
        if record["url"] == SOURCE_URL + "praca-w-frse":
            references = re.findall(
                r"\bFRSE/\d+/\d{4}\b",
                reference or " ".join(record["classification"]["evidence"]),
            )
            reference = one(sorted(set(references)), "recruitment reference")
        elif reference is not None:
            raise AdapterError("Unexpected recruitment reference", "validate")
        if record["id"] != record_identity(record["url"], reference):
            raise AdapterError(
                "Unowned FRSE record identity or URL", "validate"
            )
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
            values["created_at"]
            <= values["updated_at"]
            <= values["last_checked_at"]
            and values["first_seen_at"]
            <= values["last_seen_at"]
            <= values["last_checked_at"]
        ):
            raise AdapterError("Invalid owned FRSE chronology", "validate")
        if (
            record["status"] == "expired"
            and record["deadline"] is None
            and not (
                record.get("application_closed") is True
                or (
                    record["kind"] == "institutional-grant"
                    and any(
                        "institutional competition closed" in value
                        for value in record["classification"]["evidence"]
                    )
                )
            )
        ):
            raise AdapterError("Missing guarded FRSE call closure", "validate")


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


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    clean = urllib.parse.urlunsplit(parsed._replace(fragment=""))
    if (
        "https://" + parsed.netloc not in ROBOTS_ORIGINS
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or re.search(r"[\x00-\x20\\]", value)
        or any(
            part in parsed.path.lower().split("/")
            for part in ("admin", "login", "logout", "api")
        )
    ):
        raise AdapterError(
            "Unreviewed public source origin or route", "access"
        )
    return clean


def policy_content(root, key):
    """Select the reviewed legal body and its applicable policy links."""
    selectors = {
        "etwinning-policy": ("section", "text_desc_box"),
        "erasmus-policy": ("section", "standard_text_page"),
        "eks-cookies": ("section", "text_page"),
        "selfie-cookies": ("section", "text_page"),
        "edu-rodo": ("section", "text_page"),
        "edu-cookies": ("section", "text_page"),
        "data-policy": ("div", "split_view"),
        "cookies": ("div", "split_view"),
    }
    if key not in selectors:
        raise AdapterError("Unreviewed policy selector", "access")
    main = one(nodes(root, "main"), "policy main")
    tag, name = selectors[key]
    content = one(nodes(main, tag, class_name=name), "policy body")
    if not content.text() or content.duplicate_attrs:
        raise AdapterError("Missing or ambiguous policy body", "access")
    return content


def policy_fingerprint(root, key):
    content = policy_content(root, key)
    facts = json.dumps(
        {
            "text": content.text(),
            "links": sorted(
                {
                    node.attrs["href"]
                    for node in nodes(content, "a")
                    if node.attrs.get("href")
                }
            ),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(facts.encode()).hexdigest()


def html_fingerprint(body, key=None):
    return policy_fingerprint(
        document_root(body.decode("utf-8", "strict")), key
    )


def absolute_link(base, href):
    """Normalize public links without interpreting forms or fetching assets."""
    url = urllib.parse.urljoin(base, href)
    parts = urllib.parse.urlsplit(url)
    host = "www.frse.org.pl" if parts.netloc == "frse.org.pl" else parts.netloc
    scheme = "https" if "https://" + host in ROBOTS_ORIGINS else parts.scheme
    path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@-._~")
    path = re.sub(r"/{2,}", "/", path).rstrip("/") or "/"
    query = "" if parts.query.startswith("anchor=") else parts.query
    return urllib.parse.urlunsplit(
        parts._replace(path=path, netloc=host, scheme=scheme, query=query)
    )


def content_blocks(root):
    """Keep complete paragraph/list boundaries, including late-page conditions."""
    result = []

    def visit(node):
        if node.tag in ("script", "style", "nav", "form", "footer"):
            return
        if node.tag in ("p", "li", "h1", "h2", "h3", "h4", "td"):
            text = node.text()
            if text:
                result.append(text)
            return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)

    visit(root)
    return result or ([root.text()] if root.text() else [])


def material_links(root, url):
    return sorted(
        {
            absolute_link(url, node.attrs["href"])
            for node in nodes(root, "a")
            if node.attrs.get("href", "").strip()
            and urllib.parse.urlsplit(
                absolute_link(url, node.attrs["href"])
            ).scheme
            in ("http", "https")
        }
    )


def read_html(url, *, inventory=False):
    _, headers, body = fetch_public(url, inventory=inventory)
    if "html" not in headers.get("Content-Type", "").lower():
        raise AdapterError("Expected public HTML: " + url, "parse")
    return document_root(body.decode("utf-8", "strict"))


LISTINGS = {
    "finansowanie-projektow": "grants",
    "wydarzenia-i-szkolenia": "training",
    "konkursy": "competitions",
}
# These are identity aliases, never a store of programme facts.
ALIASES = {
    SOURCE_URL
    + "finansowanie-projektow/zmiana-ktora-pomagasz-tworzyc": SOURCE_URL
    + "finansowanie-projektow/zmiana-ktora-ty-tworzysz",
}
# Historical discrepancies are unresolved warnings, not replacement facts.
_GOVERNING_CONFLICTS = {
    "zagraniczne-wyjazdy-doskonalenia-kadry": "Course-duration limits differ from the previously reviewed programme guide.",
    "erasmus-mundus-design-measures": "The overview grant amount differs from the previously reviewed programme guide.",
    "dzialania-jean-monnet-debata-nt-polityk": "Network geography and consortium eligibility differ from the previously reviewed guide.",
    "erasmus-mundus-joint-masters": "The overview omits governing mobility/residence-country eligibility conditions.",
}
_FACT_PATTERNS = {
    "eligibility": r"dla kogo|wiek|pełnolet|wymag|uprawni|wyklucz|nie mog|nie może|skierowan|beneficjent|sympatyk|"
    r"co najmniej|minimum|muszą|musi|uczestni|nauczyciel|student|uczni",
    "funding": r"dofinans|stypend|nagrod|honorarium|\beuro\b|\bEUR\b|\bPLN\b|"
    r"\bzł\b|koszt|bezpłat",
    "application": r"prześlij|przesłać|zgłosz|zgłasz|wniosk|formularz|aplik|"
    r"rejestr|zapis|termin|\bCV\b|list motyw",
    "requirements": r"język|polsku|znaków|słowach|limitu|format|maksymaln|autorski|oryginaln|niepublikowan|nienagradz|jedną pracę|skan|zdjęcie|kategorii",
    "duration": r"(?<![\w@])\d{1,3}(?:[–-]\d{1,3})?\s+(?:dni|dzień|godzin\w*|miesi\w*|tygodni\w*|lat|sekund\w*|minut\w*)\b",
}
_CALL_WORDS = re.compile(r"konkurs|nab[oó]r|discovereu", re.I)
_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}
_DATE = re.compile(
    r"(?<!\d)(?:(?P<day>\d{1,2})\s+(?P<month>"
    + "|".join(_MONTHS)
    + r")\s+(?P<year>20\d{2})|(?P<d>\d{1,2})[./](?P<m>\d{1,2})"
    r"[./](?P<y>20\d{2}))(?!\d)",
    re.I,
)
_CLOSING = re.compile(
    r"(?:zgłosze\w*|wniosk\w*|aplikac\w*|zapis\w*|rejestrac\w*|"
    r"nab[oó]r|nadsyłani\w*|przesyłani\w*)[^.!?;]{0,100}?"
    r"(?:\bdo\b|upływa|kończy się|mija)|"
    r"(?:prześlij|przesłać|nadsyłać)[^.!?;]{0,100}?\bdo\b|"
    r"(?:prace będą przyjmowane|zdjęcie należy dodać)[^.!?;]{0,100}?\bdo\b|"
    r"uczestnicy zgłaszają swój udział.{0,150}?\bw terminie od.{0,60}?\bdo\b|"
    r"konkurs trwa(?:[^.!?;]|\br\.){0,80}?\bdo\b",
    re.I,
)


def closing_dates(blocks):
    """Use only explicit application closings with a complete source year."""
    dates = []
    for block in blocks:
        if re.search(
            r"czas trwania.*projekt|okres realizacji|okres trwałości",
            block,
            re.I,
        ):
            continue
        if re.search(
            r"planowan|przewidywan|orientacyjn|wstępny termin", block, re.I
        ):
            continue
        for closing in _CLOSING.finditer(block):
            tail = block[closing.end() :]
            # A partial date cannot borrow a year from a later event or result.
            match = re.match(r"\s*(?:dnia\s+)?", tail)
            offset = match.end()
            date = _DATE.match(tail, offset)
            if date is None:
                continue
            try:
                if date["day"]:
                    value = datetime(
                        int(date["year"]),
                        _MONTHS[date["month"].lower()],
                        int(date["day"]),
                    )
                else:
                    value = datetime(
                        int(date["y"]), int(date["m"]), int(date["d"])
                    )
            except ValueError:
                continue
            evidence = block[closing.start() : closing.end() + date.end()]
            dates.append(
                {"date": value.date().isoformat(), "evidence": evidence}
            )
    return [
        dict(pair) for pair in dict.fromkeys(tuple(d.items()) for d in dates)
    ]


def page_numbers(root):
    values = {1}
    for node in root.walk():
        for key in ("data-page", "data-pagescount"):
            value = node.attrs.get(key)
            if value is not None:
                if not re.fullmatch(r"[1-9]\d*", value) or int(value) > 100:
                    raise AdapterError(
                        "Invalid or excessive directory pagination"
                    )
                values.update(range(1, int(value) + 1))
    return values


def listing_url(slug, page):
    if page == 1:
        return SOURCE_URL + slug
    prefix = "s_" if slug == "wydarzenia-i-szkolenia" else ""
    params = {prefix + "filter_for_who": "0"}
    if prefix:
        params[prefix + "filter_terms"] = "0"
    params.update(
        {
            prefix + "filter_string": "",
            prefix + "filter_send": "filtruj",
            "active_page": str(page),
        }
    )
    return SOURCE_URL + slug + "?" + urllib.parse.urlencode(params)


def read_pages():
    """Exhaust advertised catalogue pages and discovered current call details."""
    pages, candidates, catalogues, supporting = {}, {}, [], {}

    def read(url, *, inventory=False):
        url = public_url(url)
        if url not in pages:
            pages[url] = read_html(url, inventory=inventory)
        return pages[url]

    # Policy review remains separate from changing opportunity facts.
    for key, info in ACCESS_POLICIES.items():
        _, _, body = fetch_public(info["url"])
        if html_fingerprint(body, key) != info["sha256"]:
            raise AdapterError(
                "Publisher access policy changed: " + key, "access"
            )
        pages[info["url"]] = document_root(body.decode("utf-8", "strict"))

    for slug, category in LISTINGS.items():
        pending, seen, signatures = {1}, set(), set()
        while pending:
            page = min(pending)
            pending.remove(page)
            url = listing_url(slug, page)
            root = fetch_inventory_page(read, url)
            pending.update(page_numbers(root) - seen - {page})
            seen.add(page)
            main = one(nodes(root, "main"), "directory main")
            if slug == "konkursy":
                cards = nodes(main, class_name="desc_holder")
                if not cards:
                    raise AdapterError("Missing contest catalogue cards")
                signature = tuple(card.text() for card in cards)
                for card in cards:
                    title = one(nodes(card, "h2"), "contest title").text()
                    links = material_links(card, url)
                    catalogues.append((title, links, card))
                    for link in links:
                        if (
                            urllib.parse.urlsplit(link).netloc
                            in {
                                "www.frse.org.pl",
                                "erasmusplus.org.pl",
                                "eks.org.pl",
                                "etwinning.pl",
                                "selfieplus.frse.org.pl",
                                "eduinspiracje.org.pl",
                            }
                            and not link.lower().endswith(".pdf")
                            and not re.search(
                                r"laurea|/aktualnosci/",
                                urllib.parse.urlsplit(link).path,
                            )
                        ):
                            candidates.setdefault(public_url(link), category)
            else:
                links = {
                    absolute_link(url, node.attrs["href"])
                    for node in nodes(main, "a")
                    if node.attrs.get("href", "").startswith("/" + slug + "/")
                }
                if not links:
                    raise AdapterError("Empty catalogue page: " + url)
                signature = tuple(sorted(links))
                for link in sorted(links):
                    candidates.setdefault(link, category)
            if signature in signatures:
                raise AdapterError("Repeated directory page: " + url)
            signatures.add(signature)
        print(
            f"{slug}: exhausted {len(seen)} advertised pages", file=sys.stderr
        )

    candidates[SOURCE_URL + "praca-w-frse"] = "jobs"
    # News recommendations expose current rounds omitted by evergreen catalogues.
    homepage = fetch_inventory_page(read, SOURCE_URL)
    for node in nodes(homepage, "a"):
        href = node.attrs.get("href", "")
        if href.startswith("/aktualnosci/") and _CALL_WORDS.search(node.text()):
            candidates.setdefault(
                absolute_link(SOURCE_URL, href), "competitions"
            )

    pending = list(candidates)
    visited = set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        if len(visited) >= 180:
            raise AdapterError(
                "Discovered detail inventory exceeds collection bound"
            )
        visited.add(url)
        root = read(url)
        main = one(nodes(root, "main"), "detail main")
        for node in nodes(root, "a"):
            href = node.attrs.get("href", "")
            if not href:
                continue
            link = absolute_link(url, href)
            parsed = urllib.parse.urlsplit(link)
            if "https://" + parsed.netloc not in ROBOTS_ORIGINS:
                continue
            if parsed.path.lower().endswith(".pdf"):
                continue
            related = (
                parsed.netloc == "www.frse.org.pl"
                and parsed.path.startswith("/aktualnosci/")
                and _CALL_WORDS.search(node.text())
            )
            rules = (
                re.match(
                    r"^(?:szczegółowe zasady|regulamin|zasady konkursu|"
                    r"zobacz regulamin)",
                    node.text(),
                    re.I,
                )
                and node in nodes(main, "a")
                or re.search(
                    r"/konkurs-\d{4}$|/obwieszczenie-o-konkursie$", parsed.path
                )
                or parsed.fragment
                and _CALL_WORDS.search(node.text())
            )
            if not (related or rules):
                continue
            clean = public_url(link)
            if rules:
                supporting.setdefault(url, set()).add(clean)
            if clean not in candidates:
                candidates[clean] = candidates[url]
                pending.append(clean)
    fetch_inventory_page(read, "https://erasmusplus.org.pl/dokumenty")
    return pages, candidates, catalogues, supporting


def detail_content(root):
    """Exclude navigation, related cards and decorative image captions."""
    for name in ("financing_details", "events_details", "news_details"):
        found = nodes(root, class_name=name)
        if found:
            return one(found, name)
    main = one(nodes(root, "main"), "detail main")
    result = Node("article")
    for section in nodes(main, "section"):
        if set(section.attrs.get("class", "").split()) & {
            "section_box",
            "text_page",
            "white_content",
            "details_more",
            "standard_text_page",
            "text_desc_box",
            "about",
            "download",
        }:
            result.children.append(section)
    if not result.children:
        raise AdapterError("Unrecognized substantive page layout")
    return result


def factual_extracts(blocks):
    """Select short attributed factual clauses, not an article or rulebook."""
    facts = {key: [] for key in _FACT_PATTERNS}
    for block in blocks:
        if re.match(r"Załącznik nr", block, re.I):
            break
        block = re.sub(r"^Dla kogo\?\s*", "Dla kogo: ", block, flags=re.I)
        for clause in re.split(r";|(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])", block):
            for label, pattern in _FACT_PATTERNS.items():
                if label == "duration" and not re.search(
                    r"trwa|długo|okres|maks|minimum|dni|godzin|miesi|tygodni|sekund|minut",
                    clause,
                    re.I,
                ):
                    continue
                covered = -1
                for match in re.finditer(pattern, clause, re.I):
                    if match.end() <= covered:
                        continue
                    start = max(0, match.start() - 70)
                    end = min(len(clause), max(match.end(), start + 230))
                    excerpt = clause[start:end]
                    if start:
                        excerpt = "[...] " + excerpt.split(" ", 1)[-1]
                    if end < len(clause):
                        excerpt = excerpt.rsplit(" ", 1)[0] + " [...]"
                    covered = start + len(
                        excerpt.removeprefix("[...] ").removesuffix(" [...]")
                    )
                    if excerpt and excerpt not in facts[label]:
                        facts[label].append(excerpt)
    return {key: values for key, values in facts.items() if values}


def factual_material(content, url):
    blocks = content_blocks(content)
    links = material_links(content, url)
    material = json.dumps(
        {"text": content.text(), "links": links},
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "facts": factual_extracts(blocks),
        "links": links,
        "material_sha256": hashlib.sha256(material.encode()).hexdigest(),
    }


def record_from_content(url, title, content, category, reference=None):
    fields = []
    for group in nodes(content, class_name="details_list"):
        labels = nodes(group, class_name="list_left")
        values = nodes(group, class_name="list_right")
        if len(labels) != len(values):
            raise AdapterError("Unpaired programme fields: " + url)
        fields.extend(
            {"label": label.text(), "text": value.text()}
            for label, value in zip(labels, values)
        )
    blocks = content_blocks(content)
    links = material_links(content, url)
    text = " ".join(blocks)
    categories = [category]
    kind = "opportunity"
    if "/finansowanie-projektow/" in url:
        kind = "institutional-grant"
        if re.search(r"studiuj", title, re.I):
            categories, kind = [
                "scholarships",
                "internships",
            ], "programme-overview"
        elif re.search(r"uczniowie.*praktyk", title, re.I):
            categories, kind = ["internships"], "programme-overview"
        elif re.search(r"wyjedź prowadzić|doskonalenia kadry", title, re.I):
            categories, kind = ["training"], "programme-overview"
        elif re.search(r"zostań wolontariuszem", title, re.I):
            categories, kind = ["volunteering"], "programme-overview"
    if re.search(r"worldskills|euroskills", title, re.I):
        categories, kind = ["competitions"], "programme-overview"
    overview = None
    if category == "training":
        overview = re.search(
            r"[^.!?]*(?:coroczn\w*|co roku|katalog kursów|archiwum nagrań webinariów|"
            r"wg harmonogramu|tegorocznej edycji|edycji Tool Fair w roku \d{4}|"
            r"wszystkie lekcje)[^.!?]*",
            text,
            re.I,
        )
        if overview:
            kind = "programme-overview"
    record = make_record(
        title,
        url,
        categories,
        kind=kind,
        method="publisher-html",
        evidence=[ATTRIBUTION, url],
    )
    record["id"] = record_identity(url, reference)
    if reference:
        record["reference"] = reference
    record["country"] = PUBLISHER_COUNTRY
    if overview:
        record["classification"]["evidence"].append(overview[0].strip())
    record["details"] = factual_material(content, url)
    record["details"]["fields"] = [
        {
            "label": item["label"],
            "facts": factual_extracts([item["label"] + " " + item["text"]]),
        }
        for item in fields
    ]
    record["limitations"] = [
        "HTML-reported conditions; linked programme rules can supersede overview text.",
        "PDF-only eligibility, funding and deadlines are not verified by this adapter.",
        "Residence and institutional affiliation are not nationality eligibility.",
    ]
    closings = closing_dates(
        blocks + [item["label"] + " " + item["text"] for item in fields]
    )
    record["application_dates"] = closings
    unique = sorted({entry["date"] for entry in closings})
    if len(unique) == 1 and kind != "programme-overview":
        record["deadline"] = unique[0]
    term = " ".join(
        n.text() for n in nodes(content, class_name="term_box_content")
    )
    closure = bool(
        re.search(
            r"(?:nab[oó]r|konkurs|przyjmowanie zgłoszeń)(?:\s+w konkursie)?"
            r"(?:\s+\d{4})?\s+"
            r"(?:(?:jest|został|zostało)\s+)?(?:już\s+)?"
            r"(?:zakończon[yae]|zamknięt[yae]|zakończył się)\b",
            term or text,
            re.I,
        )
    )
    record["application_closed"] = closure and kind != "programme-overview"
    if record["application_closed"]:
        record["status"] = "expired"
    elif record["deadline"]:
        today = datetime.now(timezone.utc).date().isoformat()
        if record["deadline"] < today:
            record["status"] = "expired"
        elif (
            record["deadline"] > today
            and re.search(
                r"zapraszamy|prześlij|należy przesłać|prosimy o przesłanie|"
                r"przyjmujemy|trwa nabór|zgłoszenia do|na zgłoszenia czekamy",
                text,
                re.I,
            )
            and not re.search(r"nabór (?:rozpocznie|zacznie) się", text, re.I)
        ):
            record["status"] = "open"
    locations = [
        item["text"] for item in fields if item["label"].startswith("Gdzie")
    ]
    if len(locations) == 1 and locations[0].lower() not in (
        "online",
        "internet",
    ):
        record["location"] = locations[0]
    conflict = _GOVERNING_CONFLICTS.get(
        urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    )
    if conflict:
        record["source_conflicts"] = [
            {
                "status": "unresolved",
                "issue": conflict,
                "affected_fields": ["eligibility", "funding", "duration"],
                "governing_documents": [],
            }
        ]
        record["details"][
            "verification"
        ] = "conflicting-overview-not-governing-conditions"
        record["limitations"].append(
            conflict
            + " Current PDF guide not parsed; do not rely on overview figures."
        )
    if re.search(r"worldskills|euroskills", title, re.I):
        record["limitations"].append(
            "FRSE programme overview only. Current WorldSkills-specific rules, "
            "eligibility, costs and recruitment dates have not been verified; "
            "the dedicated external origin is not accessed."
        )
    timing = (
        "Termin zgłoszeń: " + record["deadline"] + ". "
        if record["deadline"]
        else "Brak jednoznacznego terminu zgłoszeń. "
    )
    record["summary"] = (
        "FRSE; "
        + categories[0]
        + ". "
        + timing
        + (
            "Warunki strony są sprzeczne lub niepełne względem przewodnika; wymagają weryfikacji."
            if conflict
            else "Warunki udziału i szczegóły: oficjalna strona organizatora."
        )
    )
    return record


def anchored_offers(content):
    """Split named call headings across the CMS's nested modular wrappers."""
    offers = []
    active = None

    def visit(node):
        nonlocal active
        if (
            node.tag == "section"
            and node.attrs.get("id")
            and nodes(node, "h2")
        ):
            active = (
                node.attrs["id"],
                one(nodes(node, "h2"), "suboffer title").text(),
                Node("article"),
            )
            offers.append(active)
            active[2].children.append(node)
            return
        if node.tag in ("p", "li", "h1", "h2", "h3", "h4", "td", "a"):
            if active is not None:
                active[2].children.append(node)
            return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)

    visit(content)
    return offers


def parse_inventory(pages):
    documents, candidates, catalogues, supporting = pages
    records = {}
    excluded = []
    attachments = set().union(*supporting.values()) if supporting else set()
    primary_rules = "https://eduinspiracje.org.pl/eduinspirator/regulamin"
    support_roots = {
        "https://selfieplus.frse.org.pl/",
        "https://selfieplus.frse.org.pl/o-selfie",
        "https://eduinspiracje.org.pl/eduinspirator/o-konkursie",
        "https://etwinning.pl/konkursy/konkursy-polskie",
        SOURCE_URL + "wydawnictwo",
        SOURCE_URL + "wydawnictwo/regulamin",
    }
    for url in sorted(candidates, key=lambda value: (value in ALIASES, value)):
        category = candidates[url]
        root = documents[url]
        if url in support_roots or (
            url in attachments
            and url != primary_rules
            and (
                "regulamin" in urllib.parse.urlsplit(url).path
                or "lista-plikow" in url
            )
        ):
            excluded.append(
                (url, "supporting catalogue or rules, not another call")
            )
            continue
        if category == "jobs":
            boxes = nodes(root, class_name="module_faq_box")
            references = set()
            for box in boxes:
                found = sorted(
                    set(re.findall(r"\bFRSE/\d+/\d{4}\b", box.text()))
                )
                if not found:
                    continue
                reference = one(found, "job reference")
                if reference in references:
                    raise AdapterError("Repeated job reference")
                references.add(reference)
                title_nodes = nodes(box, class_name="title")
                title = one(title_nodes, "job title").text()
                record = record_from_content(
                    url, title, box, category, reference
                )
                records[record["id"]] = record
            if not references:
                raise AdapterError("No current recruitment references")
            continue
        content = detail_content(root)
        text = content.text()
        if re.search(
            r"nie ma możliwości samodzielnego zgłoszenia|"
            r"nie wymaga samodzielnego zgłoszenia|"
            r"nie muszą ubiegać się o nagrodę|"
            r"do udziału w konkursie są zapraszane osoby wytypowane|"
            r"pierwszym etapem jest przyznanie przez nas nominacji",
            text,
            re.I,
        ):
            excluded.append((url, "publisher explicitly requires nomination"))
            continue
        specialized = any(
            nodes(root, class_name=name)
            for name in ("financing_details", "events_details", "news_details")
        )
        titles = nodes(content, "h2") if specialized else nodes(root, "h1")
        if url == primary_rules:
            titles = nodes(content, "h2")
        if not titles:
            raise AdapterError("Missing opportunity title: " + url)
        title = titles[0].text()
        if not title:
            raise AdapterError("Empty opportunity title: " + url)
        if re.search(
            r"bieg erasmusa|gala nagród|erasmus\s*days|"
            r"dzień autorów|europejski tydzień młodzieży",
            title,
            re.I,
        ):
            excluded.append((url, "celebration, ceremony or charity activity"))
            continue
        offers = anchored_offers(content)
        if offers:
            for anchor, offer_title, offer in offers:
                record = record_from_content(
                    url + "#" + anchor, offer_title, offer, category
                )
                records[record["id"]] = record
            continue
        # A call announcement linking individually named sections is a parent,
        # not an additional fifth competition.
        links = material_links(content, url)
        if any(
            urllib.parse.urlsplit(link).fragment
            and public_url(link) in documents
            and anchored_offers(detail_content(documents[public_url(link)]))
            for link in links
            if "https://" + urllib.parse.urlsplit(link).netloc
            in ROBOTS_ORIGINS
        ):
            excluded.append(
                (url, "parent of separately collected anchored calls")
            )
            continue
        canonical = ALIASES.get(url, url)
        if url.endswith("/wydawnictwo/obwieszczenie-o-konkursie"):
            category = "grants"
            title = one(
                [
                    label
                    for label, targets, _ in catalogues
                    if any("/wydawnictwo" in target for target in targets)
                ],
                "publishing call catalogue title",
            )
        record = record_from_content(canonical, title, content, category)
        if record["id"] in records:
            prior = records[record["id"]]
            prior["details"].setdefault("aliases", []).append(
                {"url": url, "details": record["details"]}
            )
        else:
            records[record["id"]] = record
    for record in records.values():
        url = record["url"].split("#", 1)[0]
        linked = set(supporting.get(url, set())) - {url}
        # These mappings attach evergreen context to the current discovered
        # round while retaining the already published canonical identities.
        if urllib.parse.urlsplit(url).netloc == "selfieplus.frse.org.pl":
            linked.add("https://selfieplus.frse.org.pl/o-selfie")
        elif url == primary_rules:
            linked.add(
                "https://eduinspiracje.org.pl/eduinspirator/o-konkursie"
            )
        elif "/wydawnictwo/obwieszczenie-o-konkursie" in url:
            linked.add(SOURCE_URL + "wydawnictwo/regulamin")
        elif "nasz-projekt-etwinning" in url:
            linked.add("https://etwinning.pl/konkursy/konkursy-polskie")
        record["details"]["supporting"] = [
            {
                "url": link,
                **factual_material(detail_content(documents[link]), link),
            }
            for link in sorted(linked)
            if link in documents
        ]
        if record.get("source_conflicts"):
            index = "https://erasmusplus.org.pl/dokumenty"
            guides = [
                link
                for link in material_links(documents[index], index)
                if re.search(r"programme-guide-20\d{2}.*\.pdf$", link, re.I)
            ]
            if not guides:
                raise AdapterError(
                    "Current governing programme-guide links missing"
                )
            year = max(
                re.search(r"programme-guide-(20\d{2})", link, re.I)[1]
                for link in guides
            )
            current = [
                link
                for link in guides
                if "programme-guide-" + year in link.lower()
            ]
            record["source_conflicts"][0]["governing_documents"] = [
                index
            ] + sorted(current)
            record["classification"]["evidence"].extend(
                [index] + sorted(current)
            )
    result = list(records.values())
    validate_records(result)
    print(
        json.dumps(
            {
                "detail_pages": len(candidates),
                "records": len(result),
                "excluded": excluded,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return result


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
        "display_name": "FRSE",
        "source": SOURCE_ID,
        "name": "FRSE — funding, training, jobs and awards",
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
            else f"Validated refresh retains {len(records)} FRSE opportunities"
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
        or previous.get("name") != "FRSE — funding, training, jobs and awards"
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
    validate_records(records, allow_empty=True)


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


ROBOTS_ORIGINS = [
    "https://eduinspiracje.org.pl",
    "https://eks.org.pl",
    "https://erasmusplus.org.pl",
    "https://etwinning.pl",
    "https://selfieplus.frse.org.pl",
    "https://www.frse.org.pl",
]

ACCESS_POLICIES = {
    "etwinning-policy": {
        "url": "https://etwinning.pl/klauzula-rodo",
        "format": "html",
        "sha256": "ad1d077225592a200ef0c4b027c2c0c8f6419a1676a41b32e6e98c82d39511c7",
    },
    "erasmus-policy": {
        "url": "https://erasmusplus.org.pl/ochrona-danych-osobowych-w-programie-erasmus-2021-2027",
        "format": "html",
        "sha256": "075762ac2bd97b3e05bab61400399fdbb68c7ebf2598adb79085c9103d28e826",
    },
    "eks-cookies": {
        "url": "https://eks.org.pl/pliki-cookies",
        "format": "html",
        "sha256": "1b254c885f5486b4ed19afb6403df320592e9da80acaffcf676b743e58083af9",
    },
    "selfie-cookies": {
        "url": "https://selfieplus.frse.org.pl/pliki-cookies",
        "format": "html",
        "sha256": "fbfbec993f5ada3723a9948c8b8e43a107bb384b3f8cfec1c041cad3f9a09f6f",
    },
    "edu-rodo": {
        "url": "https://eduinspiracje.org.pl/rodo",
        "format": "html",
        "sha256": "65e51c255257535831d2cc2b157a0d7b4584eb2f5caca28290969c16081ab3da",
    },
    "edu-cookies": {
        "url": "https://eduinspiracje.org.pl/pliki-cookies",
        "format": "html",
        "sha256": "1e46a71bc622c675ff41d6c2e8bbb1aa092770dee96b2c0920ebfb3cd1b7865b",
    },
    "data-policy": {
        "url": "https://www.frse.org.pl/rodo",
        "format": "html",
        "sha256": "801a0f900ef0a76a0be5ea8209ba0b3ba6582c567075a9f76ac79dd3bc7203e2",
    },
    "cookies": {
        "url": "https://www.frse.org.pl/pliki-cookies",
        "format": "html",
        "sha256": "1d9fecbed93387fac66917822db032ea4d54d5e0c3b206c3db7f2a7a4b21e225",
    },
}


_STATE_INITIALIZING = False
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
    journal_descriptor(_JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, True)
    named = os.stat(os.path.dirname(_JOURNAL_PATH), follow_symlinks=False)
    if (named.st_dev, named.st_ino) != _JOURNAL_PARENT_ID:
        raise AdapterError("Original publisher directory changed", "access")


def journal_poison():
    """Invalidate the held original only; preserve unrelated generations."""
    if _JOURNAL_FD is None:
        return
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
        info = journal_descriptor(_JOURNAL_FD, _JOURNAL_ID)
        named = os.stat(
            os.path.basename(_JOURNAL_PATH),
            dir_fd=_JOURNAL_PARENT_FD,
            follow_symlinks=False,
        )
        if (named.st_dev, named.st_ino) != _JOURNAL_ID or (
            generation and journal_version(info) != _JOURNAL_VERSION
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        if _JOURNAL_LOCK_FD is not None:
            journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
            if _JOURNAL_LOCK_ID != _JOURNAL_ID:
                lock = os.stat(
                    "state.lock",
                    dir_fd=_JOURNAL_PARENT_FD,
                    follow_symlinks=False,
                )
                if (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID:
                    raise AdapterError("Publisher state lock changed", "access")
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
    global _JOURNAL_DEPTH
    journal_acquire()
    if _JOURNAL_DEPTH == 0:
        try:
            fcntl.flock(_JOURNAL_LOCK_FD, fcntl.LOCK_EX)
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
        if _JOURNAL_DEPTH == 0:
            try:
                journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
                fcntl.flock(_JOURNAL_LOCK_FD, fcntl.LOCK_UN)
                journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
            except BaseException:
                journal_failure()
                if primary is None:
                    raise


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
        raw = json.dumps(state, ensure_ascii=True).encode()
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
    journal_descriptor(_COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID)

    named = os.stat(
        os.path.join(os.path.dirname(_JOURNAL_PATH), "collection.lock"),
        follow_symlinks=False,
    )
    if (named.st_dev, named.st_ino) != _JOURNAL_COLLECTION_ID:
        raise AdapterError("Original lifecycle lock changed", "access")


def journal_before_wire():
    try:
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError("Publisher completion gate is sticky", "access")
        journal_verify()
        journal_verify_collection()
    except BaseException:
        journal_failure()
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
    path = os.environ.get("FRSE_PACING_ARTIFACT_PATH")
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
        journal_before_wire()
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
                raise AdapterError("Incomplete pacing artifact write", "access")
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
        journal_before_wire()
        if signal:
            journal_output_signal("true")
        journal_before_wire()
        output_parent(parent, root, parent_id)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        output_identity(descriptor, identity)
        output_identity(guard, identity)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Final pacing artifact was replaced", "access")
    except BaseException:
        journal_failure()
        raise
    finally:
        primary = sys.exc_info()[1]
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


class HeaderRateLimitStop(AdapterError):
    """A header-only HTTP 429 stop assessed after safe closure."""


@contextlib.contextmanager
def header_rate_limit_response(response):
    """Retain existing barriers when closing an HTTP 429 fails."""
    try:
        with response:
            yield
    except HeaderRateLimitStop:
        raise
    except BaseException as error:
        error._header429_unassessed = True
        # Latch before any fallible best-effort diagnostics.
        with contextlib.suppress(BaseException):
            journal_failure()
        raise


if __name__ == "__main__":
    sys.exit(main())
