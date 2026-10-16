"""Collect eighteen guarded official Bosch student/graduate programme overviews.

This permitted, bounded programme scope excludes blocked job inventories and
English country routes. It does not establish current vacancies or enrolment.
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

SOURCE_ID = "de-bosch"
SOURCE_URL = WEBSITE_URL = "https://www.bosch.com/careers/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "company"
SOURCE_NAME = "Bosch career programmes"
ATTRIBUTION = "Bosch — official career programme information; direct links to original sources."
DESCRIPTION = "Official allowed student and graduate programme overviews; excludes blocked vacancies and country routes; availability is unknown."
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
FAMILY = "youthopps-bosch-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 1800
PUBLICATION_TIMEOUT = 900
RUN_TIMEOUT = 3420
EXPORT_TIMEOUT = 120
_STATE_SCHEMA = 3
_ARTIFACT_NAME = "bosch-pacing-state"
MAX_PUBLISHER_STARTS = 64
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
_PUBLISHER_INTERVAL = 12.2
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


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(state):
    if (
        not isinstance(state, dict)
        or set(state) != set(empty_budget(0))
        or type(state["schema"]) is not int
        or state["schema"] != 3
        or state["family"] != FAMILY
    ):
        raise AdapterError("Invalid modern publisher state schema", "access")
    for key in [
        "observed_at",
        "not_before",
        "interval",
        "page_interval",
        "completed_at",
        "inventory_completed_at",
        "rate_limit_until",
    ]:
        if not protocol_number(state[key]):
            raise AdapterError("Unsupported publisher numeric state", "access")
    try:
        for key in [
            "observed_at",
            "not_before",
            "completed_at",
            "inventory_completed_at",
            "rate_limit_until",
        ]:
            datetime.fromtimestamp(state[key], timezone.utc)
    except (ValueError, OverflowError, OSError):
        raise AdapterError("Unsupported publisher clock", "access") from None
    if (
        state["interval"] < 12.2
        or state["page_interval"] < 30
        or state["observed_at"] > time.time() + 1
        or max(state["completed_at"], state["inventory_completed_at"])
        > state["observed_at"]
    ):
        raise AdapterError("Invalid publisher timing evidence", "access")
    for key in [
        "blocked",
        "in_flight",
        "legacy_rate_history_unknown",
        "legacy_policy_history_unknown",
        "legacy_completion_history_unknown",
    ]:
        if type(state[key]) is not bool:
            raise AdapterError("Invalid publisher boolean provenance", "access")
    if (
        type(state["rate_limit_count"]) is not int
        or not 0 <= state["rate_limit_count"] <= 100
    ):
        raise AdapterError("Unsupported observed rate counter", "access")
    starts = state["starts"]
    if (
        not isinstance(starts, list)
        or len(starts) > 10
        or any(
            not protocol_number(t) or t > state["observed_at"] for t in starts
        )
        or starts != sorted(set(starts))
    ):
        raise AdapterError("Invalid retained start observations", "access")
    if state["in_flight"]:
        if not re.fullmatch(r"[0-9a-f]{32}", str(state["flight_id"])):
            raise AdapterError("Pending owner missing", "access")
    elif state["flight_id"] is not None or state["policy_pending"] is not None:
        raise AdapterError("Settled state retains unresolved owner", "access")
    if state["policy_pending"] is not None and (
        not isinstance(state["policy_pending"], dict)
        or set(state["policy_pending"]) != {"owner", "target_sha256"}
        or state["policy_pending"]["owner"] != state["flight_id"]
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(state["policy_pending"]["target_sha256"])
        )
    ):
        raise AdapterError("Invalid pending policy target", "access")
    observations = []
    for key, denied in [("policy_allowed", False), ("policy_denied", True)]:
        if not isinstance(state[key], dict):
            raise AdapterError("Invalid policy observation map", "access")
        for name, packed in state[key].items():
            witness = protocol_history_unpack(name, packed, denied)
            observations.append(witness)
    if state["last_policy_receipt"] is not None:
        observations.append(protocol_observation(state["last_policy_receipt"]))
    for witness in observations:
        required = witness["delay"]
        if witness["rate"] is not None:
            required = max(
                required,
                witness["rate"]["seconds"] / witness["rate"]["requests"] + 0.01,
            )
        if not protocol_number(required) or state["interval"] < required:
            raise AdapterError(
                "State weakens known policy restriction", "access"
            )
    settled = state["last_settled_owner"]
    if settled is not None and (
        not isinstance(settled, dict)
        or set(settled) != {"owner", "receipt_sha256", "completed_at"}
        or not re.fullmatch(r"[0-9a-f]{32}", str(settled["owner"]))
        or not re.fullmatch(r"[0-9a-f]{64}", str(settled["receipt_sha256"]))
        or not protocol_number(settled["completed_at"])
        or settled["completed_at"] > state["observed_at"]
    ):
        raise AdapterError("Invalid exact owner settlement receipt", "access")
    receipt = state["last_policy_receipt"]
    if (
        state["in_flight"]
        and receipt is not None
        and receipt["owner"] != state["flight_id"]
    ):
        raise AdapterError("Current policy receipt has foreign owner", "access")
    if settled is not None and (
        settled["receipt_sha256"] != protocol_digest(receipt)
        or receipt is not None
        and receipt["owner"] != settled["owner"]
    ):
        raise AdapterError(
            "Settled receipt does not match exact assessment", "access"
        )
    if state["transition_id"] is not None and not re.fullmatch(
        r"[0-9a-f]{64}", str(state["transition_id"])
    ):
        raise AdapterError("Invalid reviewed transition provenance", "access")
    bound_envelope = {
        "schema": 2,
        "repository": "YouthOpps/data-pipeline",
        "source": SOURCE_ID,
        "run_id": 2**63 - 1,
        "run_attempt": 2**31 - 1,
        "head_sha": "f" * 40,
        "adapter_sha256": "f" * 64,
        "state_sha256": "f" * 64,
        "state": state,
    }
    if (
        max(
            len(json.dumps(state, separators=(",", ":")).encode()),
            len(json.dumps(bound_envelope, separators=(",", ":")).encode()),
        )
        > 16384
    ):
        raise AdapterError(
            "Publisher state/envelope exceeds original byte bound", "access"
        )
    return state


def empty_budget(now):
    return {
        "schema": 3,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "completed_at": 0,
        "inventory_completed_at": 0,
        "rate_limit_count": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "legacy_policy_history_unknown": True,
        "legacy_completion_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "policy_pending": None,
        "last_policy_receipt": None,
        "policy_allowed": {},
        "policy_denied": {},
        "last_settled_owner": None,
        "transition_id": None,
    }


def pace(inventory=False):
    global _PUBLISHER_INTERVAL, _REQUEST_STARTS, _PROTOCOL_PHYSICAL_INVENTORY
    _PROTOCOL_PHYSICAL_INVENTORY = bool(inventory)
    check_deadline()
    if _REQUEST_STARTS >= MAX_PUBLISHER_STARTS:
        raise AdapterError("Publisher request-start cap reached", "access")
    with locked_budget():
        state = load_budget()
        protocol_current_owner(state)
        if state["blocked"] or state["rate_limit_count"] >= 100:
            raise AdapterError(
                "Retained publisher hold/counter requires review", "access"
            )
        now = time.time()
        starts = [t for t in state["starts"] if t > now - 60]
        target = max(
            now,
            state["not_before"],
            state["rate_limit_until"],
            state["completed_at"] + state["interval"],
        )
        if len(starts) >= 5:
            target = max(target, starts[-5] + 60.01)
        if starts:
            target = max(target, starts[-1] + state["interval"])
        if _PROTOCOL_PHYSICAL_INVENTORY:
            target = max(
                target, state["inventory_completed_at"] + state["page_interval"]
            )
        if _PUBLISHER_COMPLETED_MONOTONIC is not None:
            target = max(
                target,
                now
                + max(
                    0,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + state["interval"]
                    - time.monotonic(),
                ),
            )
        if (
            target + 120
            > time.time() + min(_PHASE_END, _RUN_END) - time.monotonic()
            or target + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError(
                "Full publisher timing exceeds original phase", "access"
            )
        while target > time.time():
            check_deadline()
            wait = target - time.time()
            if (
                wait + 120 > min(_PHASE_END, _RUN_END) - time.monotonic()
                or time.time() + wait + 120
                > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Full publisher timing exceeds original phase", "access"
                )
            time.sleep(min(wait, 30))
        check_deadline()
        now = time.time()
        if now < target:
            raise AdapterError("Publisher clock moved backwards", "access")
        # Recheck current deadlines after waiting/processing; phase ownership
        # never renews and zero-wait paths retain the same original reserve.
        if (
            now + 120
            > time.time() + min(_PHASE_END, _RUN_END) - time.monotonic()
            or now + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError(
                "Full publisher timing exceeds original phase", "access"
            )
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        state["starts"] = [t for t in starts if t > now - 60] + [now]
        state["not_before"] = max(state["not_before"], now + state["interval"])
        state["observed_at"] = now
        save_budget(state)
        _REQUEST_STARTS += 1


@contextlib.contextmanager
def publisher_attempt(publisher):
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
    if not publisher:
        yield
        return
    if _COLLECTION_LOCK_FD is None or _PUBLISHER_GATE_FAILED or _GUARD_FATAL:
        raise AdapterError(
            "Exclusive healthy publisher collector required", "access"
        )
    primary = None
    try:
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
        _PUBLISHER_GATE_FAILED = True
        try:
            with locked_budget():
                state = load_budget()
                protocol_current_owner(state)
                completed = time.time()
                state["completed_at"] = max(state["completed_at"], completed)
                if _PROTOCOL_PHYSICAL_INVENTORY:
                    state["inventory_completed_at"] = max(
                        state["inventory_completed_at"], completed
                    )
                state["not_before"] = max(
                    state["not_before"], completed + state["interval"]
                )
                state["observed_at"] = completed
                save_budget(state)
            if not _GUARD_FATAL:
                _PUBLISHER_GATE_FAILED = False
        except BaseException:
            journal_failed()
            if primary is None:
                raise


def record_retry_after(value, rate_limited=False):
    with locked_budget():
        state = load_budget()
        protocol_current_owner(state)
        now = time.time()
        deadline = None
        if value is not None:
            try:
                raw = str(value).strip()
                seconds = (
                    float(raw)
                    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", raw)
                    else None
                )
                if seconds is not None:
                    if not protocol_number(seconds) or seconds <= 0:
                        raise ValueError("Invalid seconds")
                    deadline = now + seconds
                else:
                    date = parsedate_to_datetime(raw)
                    if date.utcoffset() is None:
                        raise ValueError("Missing timezone")
                    deadline = date.timestamp()
                if not protocol_number(deadline) or deadline <= now:
                    raise ValueError("Stale or unsupported retry deadline")
                datetime.fromtimestamp(deadline, timezone.utc)
            except (ValueError, TypeError, OverflowError, OSError):
                deadline = None
        if rate_limited:
            count = state["rate_limit_count"] + 1
            physical = state["interval"] * 2
            page = state["page_interval"] * 2
            fallback = now + 1800 * 2 ** (count - 1)
            if count > 100 or not all(
                protocol_number(v) for v in [physical, page, fallback]
            ):
                raise AdapterError("Unassessed rate-limit arithmetic", "access")
            state["rate_limit_count"] = count
            state["interval"] = max(12.2, physical)
            state["page_interval"] = max(30, page)
            state["rate_limit_until"] = max(
                state["rate_limit_until"], fallback, deadline or 0
            )
        state["not_before"] = max(
            state["not_before"], state["rate_limit_until"], deadline or 0
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


def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=MAX_RESPONSE_BYTES,
):
    host = urllib.parse.urlsplit(url).hostname
    if not publisher and host in {
        urllib.parse.urlsplit(x["url"]).hostname for x in INPUTS.values()
    }:
        raise AdapterError(
            "Publisher requests cannot bypass durable pacing", "access"
        )
    if not publisher:
        return _transport_bytes(
            url, method, headers, payload, publisher, byte_limit
        )
    if method != "GET" or payload is not None:
        raise AdapterError("Unexpected publisher request method", "access")
    protocol_preflight_wire()
    with protocol_transaction("physical"):
        return _transport_bytes(
            url, method, headers, payload, publisher, byte_limit
        )


def workflow_api(path):
    """Read fresh authoritative metadata; local proxy auth is optional."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Hosted workflow credentials missing", "access")
    if not (
        path.startswith("/actions/")
        or re.fullmatch(
            r"/contents/adapters/de-bosch/adapter\.py"
            r"\?ref=[0-9a-f]{40}", path
        )
    ):
        raise AdapterError("Unexpected workflow metadata route", "access")
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    separator = "&" if "?" in path else "?"
    status, _, raw = request_bytes(
        _WORKFLOW_GITHUB + path + separator + "_fresh=" + str(time.time_ns()),
        headers=headers,
        byte_limit=3000000,
    )
    if status != 200:
        raise AdapterError("Fresh workflow metadata unavailable", "access")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise AdapterError("Malformed workflow metadata", "access") from None


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






class InventoryRace(AdapterError):
    """A changed advertised total permits a bounded fresh history scan."""


def download_inert_artifact(identity, artifact_id, digest=None, context=None):
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Hosted workflow credentials missing", "access")
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers={
            **({"Authorization": "Bearer " + token} if token else {}),
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
        if context is None or identity != (
            context["latest"]["id"],
            context["latest"]["attempt"],
            SOURCE_ID,
        ):
            raise AdapterError(
                "Exact current artifact context required", "access"
            )
        return protocol_decode(envelope, context)
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED, _PROTOCOL_CONTEXT, _PROTOCOL_READY
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        and _REVIEWED_LOCAL_ABSENCE_TRANSITION is not None
    ):
        protocol_restore_absence()
        return
    context = protocol_history()
    latest, artifact = context["latest"], context["artifact"]
    context["source_sha256"] = protocol_source_hash(latest["head"])
    if (
        context["hosted"]
        and protocol_source_hash(context["current"][2])
        != hashlib.sha256(open(__file__, "rb").read()).hexdigest()
    ):
        raise AdapterError(
            "Hosted executing adapter/source head mismatch", "access"
        )
    incoming = download_inert_artifact(
        (latest["id"], latest["attempt"], SOURCE_ID),
        artifact["id"],
        artifact["digest"],
        context,
    )
    legacy = incoming["schema"] == 1
    marker = None
    if legacy:
        purpose = "HOSTED" if context["hosted"] else "LOCAL"
        marker = (
            _REVIEWED_HOSTED_TRANSITION
            if context["hosted"]
            else _REVIEWED_LOCAL_TRANSITION
        )
        proposed = protocol_marker(context, marker, purpose)
        incoming = protocol_legacy_into(proposed, incoming)
    # Only authenticated HOSTED input may create an ephemeral new namespace.
    scope = (
        journal_restore_scope(incoming)
        if context["hosted"]
        else contextlib.nullcontext()
    )
    with scope:
        budget_file()
        protocol_acquire_collection()
        with locked_budget():
            raw = journal_read()
            if not raw and _GUARD_CREATED and journal_creation_allowed():
                local = incoming
            else:
                try:
                    local = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise AdapterError(
                        "Existing mature state is unreadable", "access"
                    ) from None
                if isinstance(local, dict) and local.get("schema") == 1:
                    if (
                        not legacy
                        or context["hosted"]
                        or protocol_local_pins(raw) != marker["local_pins"]
                    ):
                        raise AdapterError(
                            "Finite existing LOCAL legacy pins required",
                            "access",
                        )
                    local = protocol_legacy_into(proposed, local)
                else:
                    local = validate_budget(local)
                    if legacy:
                        # A completed native cannot be replayed against an older
                        # owned legacy artifact without a separate exact
                        # release.
                        proposed = protocol_marker(
                            context,
                            _REVIEWED_MATURE_LOCAL_REENTRY,
                            "MATURE_LOCAL",
                        )
                        if (
                            protocol_local_pins(raw)
                            != _REVIEWED_MATURE_LOCAL_REENTRY["local_pins"]
                        ):
                            raise AdapterError(
                                "Mature LOCAL oneoff pins changed", "access"
                            )
                        local = protocol_join(local, proposed)
            protocol_same_history(context)
            state = protocol_join(local, incoming)
            state["completed_at"] = max(
                state["completed_at"], max(context["completion_bounds"])
            )
            state["not_before"] = max(
                state["not_before"],
                time.time() + 60,
                state["completed_at"] + state["interval"],
            )
            state["observed_at"] = time.time()
            if legacy:
                state["transition_id"] = protocol_digest(marker)
            save_budget(state)
        journal_mark_mature()
    _PROTOCOL_CONTEXT = context
    _RESTORED = _STATE_TRUSTED = _PROTOCOL_READY = True


def mark_pacing_artifact_ready(runner_parent, runner_root):
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
        line = b"pacing_ready=true\n"
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


def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("BOSCH_PACING_ARTIFACT_PATH")
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
        envelope = protocol_export_envelope(state, run_id, attempt)
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
                check_deadline()
                mark_pacing_artifact_ready(parent, root)
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
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING, _GUARD_NEW_DIRECTORY
    if _STATE_PATH and _STATE_DIR_FD is not None:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    directory = os.path.join(temporary, FAMILY + "-" + str(os.getuid()))
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")
    if not os.path.lexists(directory) and not _GUARD_REMOTE_INITIALIZING:
        return
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_info = os.fstat(parent)
    parent_id = (parent_info.st_dev, parent_info.st_ino)
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
            journal_close_acquired(descriptor, (info.st_dev, info.st_ino), True)
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _GUARD_NEW_DIRECTORY = created and _GUARD_REMOTE_INITIALIZING
        _STATE_INITIALIZING = _GUARD_NEW_DIRECTORY
        _STATE_DIR_FD = descriptor
        _STATE_DIR_ID = (info.st_dev, info.st_ino)
        directory = os.path.join(temporary, leaf)
        _STATE_PATH = os.path.join(directory, "state.json")
        _STATE_LOCK = os.path.join(directory, "state.lock")
        verify_state_directory()
    finally:
        journal_close_acquired(parent, parent_id, sys.exc_info()[0] is not None)


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
        raise AdapterError("Oversized publisher state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher state", "access") from None


def save_budget(state):
    validate_budget(state)
    journal_commit(state)


def merge_budget(incoming):
    with locked_budget():
        combined = protocol_join(load_budget(), incoming)
        combined["not_before"] = max(combined["not_before"], time.time() + 60)
        combined["observed_at"] = time.time()
        save_budget(combined)


def preserve_legacy_pacing():
    """Keep every legacy inode and its exclusive lock throughout collection."""
    roots = [
        ("issue119-bosch-research", "bosch-family.budget.json"),
        ("youthopps-source-triage/119", "pacing.json"),
    ]
    for directory, filename in roots:
        parent = os.open(
            tempfile.gettempdir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        opened = [parent]
        acquired = os.fstat(parent)
        _GUARD_LEGACY_IDS[parent] = (acquired.st_dev, acquired.st_ino)
        _GUARD_LEGACY_PATHS[parent] = tempfile.gettempdir()
        current_path = tempfile.gettempdir()
        try:
            for name in directory.split("/"):
                descriptor = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
                current_path = os.path.join(current_path, name)
                acquired = os.fstat(descriptor)
                _GUARD_LEGACY_IDS[descriptor] = (
                    acquired.st_dev,
                    acquired.st_ino,
                )
                _GUARD_LEGACY_PATHS[descriptor] = current_path
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
            acquired = os.fstat(descriptor)
            _GUARD_LEGACY_IDS[descriptor] = (acquired.st_dev, acquired.st_ino)
            _GUARD_LEGACY_PATHS[descriptor] = os.path.join(
                current_path, filename
            )
            _GUARD_LEGACY_VERSIONS[descriptor] = journal_generation(acquired)
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
            journal_verify_legacy()
            raw = os.read(descriptor, 16385)
            journal_verify_legacy()
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
            incoming["not_before"] = max(
                incoming["not_before"], legacy["until"]
            )
            incoming["interval"] = max(
                incoming["interval"], legacy.get("interval", 6)
            )
            incoming["blocked"] = legacy.get("blocked", False)
            union = legacy["starts"]
            if len(union) > 10:
                incoming["not_before"] = max(
                    incoming["not_before"], union[-1] + 60
                )
            incoming["starts"] = union[-10:]
            validate_budget(incoming)
            merge_budget(incoming)
            _LEGACY_FDS.extend(opened)
            opened = []
        except FileNotFoundError:
            pass
        finally:
            for descriptor in reversed(opened):
                identity = _GUARD_LEGACY_IDS.pop(descriptor)
                _GUARD_LEGACY_PATHS.pop(descriptor, None)
                _GUARD_LEGACY_VERSIONS.pop(descriptor, None)
                journal_close_acquired(
                    descriptor, identity, sys.exc_info()[0] is not None
                )


def prepare_collection():
    global _COLLECTION_STARTED, _REQUEST_STARTS, _PUBLISHER_BYTES
    global _PROTOCOL_FIRST_WIRE, _PROTOCOL_SEQUENCE
    if _GUARD_FATAL or _PROTOCOL_DEPTH:
        raise AdapterError(
            "Unhealthy or nested publisher preparation", "access"
        )
    _COLLECTION_STARTED = time.time()
    _REQUEST_STARTS = _PUBLISHER_BYTES = 0
    _PROTOCOL_FIRST_WIRE = False
    try:
        restore_family_artifact()
        preserve_legacy_pacing()
        _PROTOCOL_SEQUENCE += 1
        _ROBOTS.clear()
        _PROTOCOL_POLICY_RAW.clear()
    except BaseException:
        journal_failed()
        raise


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    clean = urllib.parse.urlunsplit(parsed._replace(fragment=""))
    if (
        clean not in {info["url"] for info in INPUTS.values()}
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError("Unreviewed public Bosch route", "access")
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
    """Intersect current-specific-or-wildcard with legacy-specific policy."""
    import urllib.robotparser

    groups = getattr(parser, "_protocol_groups", None)
    if groups is None:
        raise AdapterError(
            "REP cache lacks actual grouped policy provenance", "access"
        )
    values = []
    for _, entries in protocol_selected_policy_groups(groups):
        policy = urllib.robotparser.RobotFileParser()
        lines = ["User-agent: " + _USER_AGENT.split("/", 1)[0]]
        lines += [
            key + ": " + value
            for key, value in entries
            if key not in {"crawl-delay", "request-rate"}
        ]
        policy.parse(lines)
        if directive == "can_fetch":
            values.append(policy.can_fetch(_USER_AGENT, url))
        else:
            values.append(getattr(policy, directive)(_USER_AGENT))
    if directive == "can_fetch":
        return all(values)
    values = [v for v in values if v is not None]
    if not values:
        return None
    return (
        max(values)
        if directive == "crawl_delay"
        else max(values, key=lambda v: v.seconds / v.requests)
    )


def check_robots(url):
    reviewed = public_url(url)
    parsed = urllib.parse.urlsplit(reviewed)
    if parsed.path == "/robots.txt":
        return
    origin = parsed.scheme + "://" + parsed.netloc
    with protocol_transaction("policy"):
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
                raise AdapterError("Unreviewed Bosch robots origin", "access")
            read_input(key)
        protocol_assess_policy(reviewed)


def _fetch_public_body(url):
    reviewed = public_url(url)
    check_robots(reviewed)
    status, headers, body = request_bytes(reviewed, publisher=True)
    if status != 200 and not (
        urllib.parse.urlsplit(reviewed).path == "/robots.txt"
        and 400 <= status < 500
        and status != 429
    ):
        raise AdapterError(
            "Required publisher input returned HTTP " + str(status),
            "fetch",
            status,
        )
    return status, headers, body


def fetch_public(url):
    with protocol_transaction("fetch"):
        return _fetch_public_body(url)


def read_input(key):
    import urllib.robotparser

    info = INPUTS[key]
    if info["format"] != "robots":
        if key in _PROTOCOL_INVENTORY_KEYS:
            return fetch_inventory_page(_read_input_base, key)
        return _read_input_base(key)
    with protocol_transaction("robots"):
        status, headers, body = fetch_public(info["url"])
        origin = urllib.parse.urlsplit(info["url"])
        origin = origin.scheme + "://" + origin.netloc
        available = not (400 <= status < 500 and status != 429)
        if available:
            content_type = (
                headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            )
            if content_type not in {"text/plain", "text/html"}:
                raise AdapterError(
                    "Unexpected robots response format", "access"
                )
            text = body.decode("utf-8", "strict")
            digest = hashlib.sha256(body).hexdigest()
        else:
            text, digest = "", None
        parser = protocol_policy_parser(text)
        _ROBOTS[origin] = parser
        _PROTOCOL_POLICY_RAW[origin] = {
            "text": text,
            "sha256": digest,
            "available": available,
            "collection": _PROTOCOL_SEQUENCE,
        }
        protocol_assess_policy(info["url"])
        if available and digest != info["sha256"]:
            raise AdapterError(
                "Reviewed Bosch material or frontier changed: " + key, "parse"
            )
        return digest


def read_pages():
    """Exhaust all49 nominal inputs; fresh robots precede every document."""
    order = [key for key, info in INPUTS.items() if info["format"] == "robots"]
    order += [key for key, info in INPUTS.items() if info["format"] != "robots"]
    return {key: read_input(key) for key in order}


def profile_identity(profile):
    return hashlib.sha256(
        (SOURCE_ID + "|" + profile["identity"]).encode()
    ).hexdigest()[:24]


def make_record(profile):
    now = utc_now()
    return {
        "id": profile_identity(profile),
        "title": profile["title"],
        "url": profile["url"],
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": None,
        "summary": profile["summary"],
        "summary_language": "en",
        "tags": [],
        "location": None,
        "deadline": None,
        "language": LANGUAGE,
        "category": profile["categories"][0],
        "categories": profile["categories"],
        "kind": "programme-overview",
        "host_countries": profile["hosts"],
        "eligible_countries": [],
        "publisher_country": PUBLISHER_COUNTRY,
        "created_at": now,
        "updated_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "last_checked_at": now,
        "status": "unknown",
        "classification": {
            "method": "editorial-review",
            "status": "classified",
            "evidence": [profile["url"]],
        },
    }


def validate_owned_records(records):
    temporal = {
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    profiles = {profile_identity(profile): profile for profile in PROFILES}
    for record in records:
        profile = profiles.get(record.get("id"))
        if profile is None:
            raise AdapterError("Unowned Bosch identity", "validate")
        expected = make_record(profile)
        if {k: v for k, v in record.items() if k not in temporal} != {
            k: v for k, v in expected.items() if k not in temporal
        }:
            raise AdapterError("Owned Bosch factual recipe changed", "validate")
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
            raise AdapterError("Invalid Bosch chronology", "validate")


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


def validate_records(records, published=False):
    if (
        not isinstance(records, list)
        or not records and not published
        or not published and len(records) != len(PROFILES)
    ):
        raise AdapterError(
            "Incomplete reviewed Bosch identity inventory", "validate"
        )
    expected_fields = set(make_record(PROFILES[0]))
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
                "Invalid or duplicate Bosch identity", "validate"
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
                    "Invalid Bosch UTC timestamp", "validate"
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


def parse_inventory(pages):
    if set(pages) != set(INPUTS) or any(
        pages[key] != info["sha256"] for key, info in INPUTS.items()
    ):
        raise AdapterError(
            "Incomplete reviewed Bosch material frontier", "parse"
        )
    records = [make_record(profile) for profile in PROFILES]
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
        "display_name": "Bosch",
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
                "reviewed Bosch programme overviews"
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
    validate_records(records)


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

INPUTS = {
    "input-00": {
        "url": "https://www.bosch.africa/careers/students-and-graduates/",
        "format": "html",
        "sha256": "9829a10fde15762cca1a1d89c201f7976efc845d555f32ae2f0038d0ef08f891",
    },
    "input-01": {
        "url": "https://www.bosch.africa/legal-notice/?prevent-auto-open-privacy-settings=1",
        "format": "html",
        "sha256": "d0bf69d352401f9394cc2f6837af12378ddab2fc39a480fb73dc93e276c9404e",
    },
    "input-02": {
        "url": "https://www.bosch.africa/robots.txt",
        "format": "robots",
        "sha256": "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
    },
    "input-03": {
        "url": "https://www.bosch.at/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-04": {
        "url": "https://www.bosch.ca/careers/students-and-graduates/",
        "format": "html",
        "sha256": "af3cae0657408cb7f57585f1d78dc513a3649cd69b12dd8202f8282014fb2dd1",
    },
    "input-05": {
        "url": "https://www.bosch.ca/legal-notice/?prevent-auto-open-privacy-settings=1",
        "format": "html",
        "sha256": "088a0afd85eddababd63815cacb3c8e1f08ca6c8afb580c23de4923565504c1d",
    },
    "input-06": {
        "url": "https://www.bosch.ca/robots.txt",
        "format": "robots",
        "sha256": "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
    },
    "input-07": {
        "url": "https://www.bosch.ch/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-08": {
        "url": "https://www.bosch.com.cn/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-09": {
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/",
        "format": "html",
        "sha256": "3a65abe003687d7480557af2df961e9076e29a3d5c23bda554de7ff47bc1568b",
    },
    "input-10": {
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/final-thesis/",
        "format": "html",
        "sha256": "d3745db91410022c0a6e738d66562189477271a7a7ad8ea55e6bc786bb496e21",
    },
    "input-11": {
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/internships/",
        "format": "html",
        "sha256": "7a2c3c75d182bc2c96fb34db5873dd9eb2e61d654336f884310ce046127fd334",
    },
    "input-12": {
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/junior-managers-program/",
        "format": "html",
        "sha256": "de9a193a80839bd538c8cccd0641317e0f1c9bee888088dc6888fed1c867e815",
    },
    "input-13": {
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/phd/",
        "format": "html",
        "sha256": "15226723dcbf178b9099310c341220493b839a0970b4c0be27203506d0499e57",
    },
    "input-14": {
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/students-bosch/",
        "format": "html",
        "sha256": "0ba6362f42ea5a56de5530bbf58e4759df8000411c884b92f2c28ebeeed4e5f1",
    },
    "input-15": {
        "url": "https://www.bosch.com.sg/legal-notice/?prevent-auto-open-privacy-settings=1",
        "format": "html",
        "sha256": "9d57c0d36cdb7c96027b41675454d4f71104d7b66ffc3e7d46867ccc105811b0",
    },
    "input-16": {
        "url": "https://www.bosch.com.sg/robots.txt",
        "format": "robots",
        "sha256": "6763f04d00f1166333a88d3510c6729e649ff60818bb1e633d034ed54b215d7e",
    },
    "input-17": {
        "url": "https://www.bosch.com.tr/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-18": {
        "url": "https://www.bosch.com/careers/",
        "format": "html",
        "sha256": "39aab17d6de3a2f4415ec1acbb302bc00df048baeec97195896888659473ae8d",
    },
    "input-19": {
        "url": "https://www.bosch.com/careers/career-opportunities/",
        "format": "html",
        "sha256": "35e5dbd13143b2e8b5fcffdac7a1479c849eb1b9df386ea1dd90899735fb5ba4",
    },
    "input-20": {
        "url": "https://www.bosch.com/careers/career-opportunities/junior-managers-program/",
        "format": "html",
        "sha256": "462025e37cadfe3d9ddfa8b4447a5884c3fecc9f0de9865b8e229f490f97c366",
    },
    "input-21": {
        "url": "https://www.bosch.com/careers/faq/",
        "format": "html",
        "sha256": "6da147a9ba1d015dba9b72564ace2dff1a749c48a4b9ba71bb73b226d997d888",
    },
    "input-22": {
        "url": "https://www.bosch.com/legal-notice/?prevent-auto-open-privacy-settings=1",
        "format": "html",
        "sha256": "29da1b00655ca187c6e16e3dbca2870da6dd827158818c4fc1ca4596960ac116",
    },
    "input-23": {
        "url": "https://www.bosch.com/robots.txt",
        "format": "robots",
        "sha256": "7f03f031ad7213632f4c0bda4daf101371e779a6c8181e02a225d980b3d45da7",
    },
    "input-24": {
        "url": "https://www.bosch.cz/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-25": {
        "url": "https://www.bosch.de/robots.txt",
        "format": "robots",
        "sha256": "9d191636043dd361d07221a83532669ccf8c6919bbc6b63100de701160d015c9",
    },
    "input-26": {
        "url": "https://www.bosch.dk/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-27": {
        "url": "https://www.bosch.fr/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-28": {
        "url": "https://www.bosch.hu/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-29": {
        "url": "https://www.bosch.in/careers/students-and-graduates/",
        "format": "html",
        "sha256": "8d9496031d4f5a565b6a5d69a08e3405ba5f5c7436f5e53c062cf7cff274799f",
    },
    "input-30": {
        "url": "https://www.bosch.in/careers/students-and-graduates/commercial-management-trainee-program/",
        "format": "html",
        "sha256": "a0a090a3450f1ce00b0d4ab476cf861d51817f9d5520d2690919626635ac1b60",
    },
    "input-31": {
        "url": "https://www.bosch.in/careers/students-and-graduates/graduate-apprentice/",
        "format": "html",
        "sha256": "2b4c46b3e96ce2607d7b4684b9016e387e0b0ed5cf82708265f97f8981f0514a",
    },
    "input-32": {
        "url": "https://www.bosch.in/careers/students-and-graduates/indian-institute-of-technology-recruitment/",
        "format": "html",
        "sha256": "0c4b9a66d27c1d3651ab7b45cb1cfb88a8556a8be178d5f1e911440bb6937ac3",
    },
    "input-33": {
        "url": "https://www.bosch.in/careers/students-and-graduates/internships/",
        "format": "html",
        "sha256": "2c9368e4c1ecfe19be094f368661bdc18ee6396cc0e5d57186c79b0ebe5c6a15",
    },
    "input-34": {
        "url": "https://www.bosch.in/careers/students-and-graduates/junior-managers-program/",
        "format": "html",
        "sha256": "8d048aaa6c9f5edf22880b3873b553c28578c9f57b93e2ec4871198a8ea74236",
    },
    "input-35": {
        "url": "https://www.bosch.in/careers/students-and-graduates/technical-management-trainee-program/",
        "format": "html",
        "sha256": "cb47eae8fccc86049c81ca22fb6f3f71f6fc11fd9595c5cfb5c0d4392a7e1255",
    },
    "input-36": {
        "url": "https://www.bosch.in/legal-notice/?prevent-auto-open-privacy-settings=1",
        "format": "html",
        "sha256": "96cc1faaf476bbc5945f45b13619f4e1f4059ea0b28c02037dcc7c7259d488b3",
    },
    "input-37": {
        "url": "https://www.bosch.in/robots.txt",
        "format": "robots",
        "sha256": "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
    },
    "input-38": {
        "url": "https://www.bosch.it/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-39": {
        "url": "https://www.bosch.nl/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-40": {
        "url": "https://www.bosch.pl/robots.txt",
        "format": "robots",
        "sha256": "ae43f983986fef50defdc9c3ab497a016e6f49ad1e1517850af2202f2b918548",
    },
    "input-41": {
        "url": "https://www.bosch.pt/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-42": {
        "url": "https://www.bosch.ro/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-43": {
        "url": "https://www.bosch.se/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
    "input-44": {
        "url": "https://www.bosch.us/careers/start-your-career/graduates/",
        "format": "html",
        "sha256": "8f02e851068f26f348c13637c4aef7096fecb8c09b869c1bb65caba9fc4af526",
    },
    "input-45": {
        "url": "https://www.bosch.us/careers/start-your-career/students/",
        "format": "html",
        "sha256": "9a60aec2c93c2cdc242cea5b4a4c0cf30ebe08f68f2cfe17a6322d027384d2b5",
    },
    "input-46": {
        "url": "https://www.bosch.us/legal-notice/?prevent-auto-open-privacy-settings=1",
        "format": "html",
        "sha256": "3ed6be7415dfc5c528f7b294ffca5d1b71708c5beebebe7b64644890acdbaf97",
    },
    "input-47": {
        "url": "https://www.bosch.us/robots.txt",
        "format": "robots",
        "sha256": "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
    },
    "input-48": {
        "url": "https://www.grupo-bosch.es/robots.txt",
        "format": "robots",
        "sha256": "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
    },
}

PROFILES = [
    {
        "identity": "global-jmp",
        "title": "Bosch Junior Managers Program — global overview",
        "url": "https://www.bosch.com/careers/career-opportunities/junior-managers-program/",
        "categories": ["training", "jobs"],
        "hosts": [],
        "summary": "Graduate management-development framework offered across countries, "
        "combining rotations, an international assignment and mentoring. Entry "
        "conditions vary by country. This overview does not establish a current "
        "vacancy, closing date or guaranteed management appointment.",
        "proof": "Graduate audience; country-specific conditions apply; no universal "
        "degree/GPA/age/work authorization established; Cross-functional and "
        "cross-divisional rotations; international assignment; experienced personal "
        "mentor",
        "parents": [],
    },
    {
        "identity": "in-jmp",
        "title": "Bosch India Junior Managers Program",
        "url": "https://www.bosch.in/careers/students-and-graduates/junior-managers-program/",
        "categories": ["training", "jobs"],
        "hosts": ["IN"],
        "summary": "India management-development programme with 18–24 months of rotations, "
        "including an assignment abroad, mentoring and seminars. Requires "
        "postgraduate MBA, M.Tech or Chartered Accountancy qualifications, over "
        "60% throughout academics and 1–3 years of professional experience. "
        "Overseas experience is preferred; current vacancies must be checked with "
        "Bosch.",
        "proof": "Postgraduate MBA/M.Tech/Chartered Accountancy; more than 60% throughout "
        "academic studies; 1–3 years professional experience; foreign experience "
        "preferred; mobility, leadership and language-learning readiness; 18–24 "
        "months; 4–6 assignments including one abroad; nine functions; mentor and "
        "seminars",
        "parents": [],
    },
    {
        "identity": "in-tmt",
        "title": "Bosch India Technical Management Trainee Program",
        "url": "https://www.bosch.in/careers/students-and-graduates/technical-management-trainee-program/",
        "categories": ["training", "jobs"],
        "hosts": ["IN"],
        "summary": "An 18-month engineering-graduate training programme combining classroom "
        "learning, workplace assignments, projects and mentoring. Applicants must "
        "be full-time engineering graduates from Bosch-selected institutes with "
        "at least 65% throughout academics. Apply through the institute placement "
        "office; selection includes assessments and interviews.",
        "proof": "Full-time engineering graduates from Bosch-selected institutes; at least "
        "65% throughout academics; application through institute placement office; "
        "selection assessments/interview; 18-month classroom and on-the-job "
        "training across functions/locations; projects, workshops and mentoring",
        "parents": [],
    },
    {
        "identity": "in-cmt",
        "title": "Bosch India Commercial Management Trainee Program",
        "url": "https://www.bosch.in/careers/students-and-graduates/commercial-management-trainee-program/",
        "categories": ["training", "jobs"],
        "hosts": ["IN"],
        "summary": "An 18-month management-graduate programme with practical assignments, "
        "projects, seminars and senior guidance. Requires full-time management "
        "graduates from Bosch-selected institutes and first-class results "
        "throughout academics, including postgraduate study. Applications go "
        "through institute placement offices, followed by assessments and "
        "interviews.",
        "proof": "Full-time management graduates from Bosch-selected institutes; first class "
        "throughout academics including postgraduate study; institute placement "
        "route; staged assessments/domain and HR interview; 18-month training in "
        "one functional area; projects, seminars and senior guidance",
        "parents": [],
    },
    {
        "identity": "in-ga",
        "title": "Bosch India Graduate Apprentice",
        "url": "https://www.bosch.in/careers/students-and-graduates/graduate-apprentice/",
        "categories": ["training"],
        "hosts": ["IN"],
        "summary": "A 12-month engineering apprenticeship combining workplace and classroom "
        "learning. Requires first-class results throughout, every semester or "
        "year passed on the first attempt without backlog, and the final "
        "engineering examination in the selection year. Eligible engineering "
        "disciplines and selected institutes apply. Recruitment uses college "
        "placement officers and assessments.",
        "proof": "Full-time engineering graduate; first-class results throughout; every "
        "semester/year passed first attempt with no backlog; final engineering "
        "examination in selection year; listed mechanical-related or "
        "electrical/electronics disciplines when required; selected institutes; 12 "
        "months workplace and classroom learning, projects, seminars and guidance",
        "parents": [],
    },
    {
        "identity": "in-iit",
        "title": "Bosch India IIT Graduate Recruitment",
        "url": "https://www.bosch.in/careers/students-and-graduates/indian-institute-of-technology-recruitment/",
        "categories": ["training", "jobs"],
        "hosts": ["IN"],
        "summary": "IIT engineering-graduate recruitment with on-the-job training, mentoring "
        "and workshops. Requires at least 70% throughout academics or a minimum "
        "CGPA of 7.0, including graduation, with every semester or year passed on "
        "the first attempt without backlog. Listed engineering disciplines apply. "
        "Recruitment proceeds through institute placement offices and "
        "assessments.",
        "proof": "Full-time engineering graduate from any IIT; at least 70% throughout or "
        "minimum CGPA 7.0 including graduation; every semester/year first attempt "
        "without backlog; specified mechanical or electrical/electronics "
        "disciplines; placement-office route; On-the-job training in a functional "
        "area, varied assignments, mentor and workshops",
        "parents": [],
    },
    {
        "identity": "in-internship",
        "title": "Bosch India Internships",
        "url": "https://www.bosch.in/careers/students-and-graduates/internships/",
        "categories": ["internships"],
        "hosts": ["IN"],
        "summary": "Practical project-based internships lasting up to one year, with "
        "remuneration and support from experienced colleagues. Applicants need "
        "strong academic and subject knowledge, good English, teamwork and "
        "flexibility. Apply through the institute placement office; selection "
        "includes assessments and an interview. Future employment may be possible "
        "but is not guaranteed.",
        "proof": "Strong subject knowledge and academic results; dedication, flexibility, "
        "teamwork and good English; other foreign languages advantageous; institute "
        "placement route with assessments and interview; Practical project "
        "responsibility and international teamwork; up to one year; remuneration "
        "stated, amount unknown; possible future employment, not guaranteed",
        "parents": [],
    },
    {
        "identity": "sg-jmp",
        "title": "Bosch Singapore Junior Managers Program",
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/junior-managers-program/",
        "categories": ["training", "jobs"],
        "hosts": ["SG"],
        "summary": "Singapore graduate management programme with 18–24 months of functional "
        "rotations, an assignment abroad, mentoring and seminars. Requires "
        "outstanding bachelor’s or master’s results in IT, engineering, sciences "
        "or business, foreign experience, and fresh-graduate status or fewer than "
        "three years of experience.",
        "proof": "Bachelor/master in IT, engineering, sciences or business; outstanding "
        "grades; fresh graduate or fewer than 3 years experience; foreign "
        "experience; literal source wording: during a semester of internship; "
        "leadership/mobility; 18–24 months; 4–6 functional assignments including "
        "one abroad; mentoring and seminars",
        "parents": [],
    },
    {
        "identity": "sg-phd",
        "title": "Bosch Singapore PhD Research Programme",
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/phd/",
        "categories": ["training", "jobs"],
        "hosts": ["SG"],
        "summary": "Doctoral research supported through a 2–4-year temporary work contract, "
        "specialist mentoring and a PhD candidate network. Requires a completed "
        "bachelor’s or master’s degree with top grades; technical disciplines are "
        "preferred, and practical or overseas experience is ideal. Financial "
        "support is stated without an amount.",
        "proof": "Completed bachelor/master with top grades; technical discipline preferred; "
        "practical and overseas experience ideal; entrepreneurial, creative and "
        "collaborative outlook; 2–4-year temporary work contract; financial "
        "support, amount unknown; specialist mentoring through dissertation "
        "publication; PhD network",
        "parents": ["https://www.bosch.com.sg/careers/students-and-graduates/"],
    },
    {
        "identity": "sg-internship",
        "title": "Bosch Singapore Internships",
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/internships/",
        "categories": ["internships"],
        "hosts": ["SG"],
        "summary": "Study-related compulsory or voluntary internships with practical "
        "responsibilities alongside specialists and international teams. Strong "
        "academic knowledge, teamwork, creativity and flexibility are expected. "
        "Voluntary placement duration and field must fit the student’s studies "
        "and Bosch. A placement can lead to consideration for students@bosch.",
        "proof": "Study-related strong theoretical knowledge and academic results; "
        "dedication/flexibility, creativity, interpersonal skills and teamwork; "
        "compulsory or voluntary placements; Practical responsibilities with "
        "specialists/international teams; voluntary duration/field agreed to fit "
        "studies; eligibility for students@bosch not automatic",
        "parents": [],
    },
    {
        "identity": "sg-thesis",
        "title": "Bosch Singapore Bachelor’s and Master’s Thesis Programme",
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/final-thesis/",
        "categories": ["training"],
        "hosts": ["SG"],
        "summary": "Bachelor’s and master’s thesis opportunities combining university study "
        "with practical work alongside Bosch specialists. Participants receive a "
        "personal mentor and access to relevant expertise, while developing "
        "professional contacts. Sound subject knowledge, curiosity, flexibility, "
        "creativity and teamwork are expected.",
        "proof": "Bachelor/master final-thesis student; sound subject knowledge, curiosity, "
        "dedication/flexibility, creativity and teamwork; Personal mentor; "
        "collaboration with Bosch specialists and university; practical research "
        "and professional network",
        "parents": [],
    },
    {
        "identity": "students-bosch",
        "title": "Bosch students@bosch Programme",
        "url": "https://www.bosch.com.sg/careers/students-and-graduates/students-bosch/",
        "categories": ["training", "internships"],
        "hosts": [],
        "summary": "An invitation-based development network: strong Bosch internship "
        "performance can lead to an invitation, and STEM students may be "
        "considered. Participants receive specialist mentoring, HR career "
        "guidance, events and peer networking, with opportunities for "
        "international internships. Admission is not an unrestricted public "
        "application.",
        "proof": "Invitation after strong performance during a Bosch internship, explicitly "
        "stated on SG parent; no public unrestricted admission; SG and Canada "
        "parents conditionally state that students studying a STEM subject may be "
        "chosen; this is consideration, not a universal STEM-only admissions rule.; "
        "Department mentor, HR career guidance, events and peer contacts; "
        "internships around the world",
        "parents": [
            "https://www.bosch.com.sg/careers/students-and-graduates/",
            "https://www.bosch.ca/careers/students-and-graduates/",
        ],
    },
    {
        "identity": "us-rdp",
        "title": "Bosch USA Rotational Development Program",
        "url": "https://www.bosch.us/careers/start-your-career/graduates/",
        "categories": ["training", "jobs"],
        "hosts": ["US"],
        "summary": "An 18–24-month US graduate rotational programme with executive "
        "mentoring. Requires a related bachelor’s or master’s degree, GPA of at "
        "least 3.0, and a completed internship/co-op or 6 months–5 years of "
        "relevant full-time experience. Candidates must accept relocations and "
        "have indefinite US work authorization; future sponsorship is "
        "unavailable.",
        "proof": "Bachelor/master in listed "
        "engineering/computing/supply-chain/finance/accounting/marketing/HR "
        "disciplines; GPA >=3.0; at least one completed internship/co-op or 6 "
        "months–5 years related full-time experience; willingness/ability multiple "
        "US relocations and international assignment; indefinite US work "
        "authorization; future sponsorship unavailable; 18–24 months function/team "
        "rotations; executive mentor and programme coordinator; international "
        "rotation conditional on position",
        "parents": [],
    },
    {
        "identity": "us-internship",
        "title": "Bosch USA Student Internships",
        "url": "https://www.bosch.us/careers/start-your-career/students/",
        "categories": ["internships"],
        "hosts": ["US"],
        "summary": "US internships offering degree-related practical work and team "
        "responsibilities. Applicants must be at least 18, enrolled at an "
        "accredited university in a related bachelor’s or master’s programme, and "
        "have an overall GPA of at least 3.0. Duration depends on the placement. "
        "Check Bosch for current openings.",
        "proof": "At least 18 years old; currently enrolled in accredited university "
        "pursuing related bachelor/master; overall GPA >=3.0; Degree-related "
        "practical team experience with real responsibilities; duration varies by "
        "internship",
        "parents": [],
    },
    {
        "identity": "us-coop",
        "title": "Bosch USA Co-op Programme",
        "url": "https://www.bosch.us/careers/start-your-career/students/",
        "categories": ["training", "internships"],
        "hosts": ["US"],
        "summary": "A US co-op programme with select universities, combining academic study "
        "with practical work, structured training and increasing "
        "responsibilities. Applicants must be at least 18, enrolled at an "
        "accredited university for a related bachelor’s or master’s degree, and "
        "have an overall GPA of at least 3.0.",
        "proof": "At least 18 years old; accredited-university related bachelor/master "
        "enrolment; overall GPA >=3.0; programme designated with select "
        "universities; On-the-job academic/work experience; structured "
        "technical/professional training and increased responsibilities",
        "parents": [],
    },
    {
        "identity": "ca-gsp",
        "title": "Bosch Canada Graduate Specialist Program",
        "url": "https://www.bosch.ca/careers/students-and-graduates/",
        "categories": ["training", "jobs"],
        "hosts": ["CA"],
        "summary": "Graduate specialist development through rotations across functions, "
        "divisions and locations, with mentoring from experienced managers. The "
        "programme develops subject expertise and professional responsibility. "
        "This overview does not establish a current vacancy, specific entry "
        "threshold or closing date.",
        "proof": "Graduate audience; no specific grade, degree field, nationality, age or "
        "experience criterion established; Functional/division/location rotations; "
        "experienced manager mentoring; subject expertise and responsibility",
        "parents": [],
    },
    {
        "identity": "africa-gep",
        "title": "Bosch Africa Graduate Experience Program",
        "url": "https://www.bosch.africa/careers/students-and-graduates/",
        "categories": ["training", "jobs"],
        "hosts": [],
        "summary": "Graduate development through rotations across functions, divisions and "
        "locations, supported by experienced managers. Participants build "
        "specialist knowledge and take responsibility through practical "
        "assignments. Specific locations, entry conditions, vacancies and closing "
        "dates must be checked with Bosch.",
        "proof": "Graduate audience; no universal specific degree, nationality, "
        "age/GPA/work-authorization threshold established; "
        "Function/division/location rotations; experienced top-manager mentoring; "
        "specialist knowledge and responsibility",
        "parents": [],
    },
    {
        "identity": "za-learnership",
        "title": "Bosch South Africa Learnerships",
        "url": "https://www.bosch.africa/careers/students-and-graduates/",
        "categories": ["training"],
        "hosts": ["ZA"],
        "summary": "South African learnership framework combining structured study at a "
        "college or training centre with workplace learning linked to employment. "
        "It leads to a nationally recognized occupational qualification. This "
        "overview does not establish current enrolment, pay, a duration or "
        "specific entry requirements.",
        "proof": "Occupational learnership audience; no specific entry "
        "qualification/age/grade/enrolment requirements stated; Employment-linked "
        "structured college/training-centre study and workplace learning; "
        "nationally recognized occupational qualification",
        "parents": [],
    },
]

_GUARD_PARENT = _GUARD_PARENT_ID = None
_GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
_GUARD_LOCK_ID = None
_GUARD_LOCKS = {}
_GUARD_CREATED = _GUARD_DIRTY = _GUARD_FATAL = False
_GUARD_COLLECTION_ID = None
_GUARD_NEW_DIRECTORY = False
_GUARD_FAILED_STATE = None
_GUARD_REMOTE_INITIALIZING = False
_GUARD_LEGACY_IDS = {}
_GUARD_LEGACY_PATHS = {}
_GUARD_LEGACY_VERSIONS = {}


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


def _journal_preopener_base():
    """Final publisher authority check, after request construction."""
    try:
        journal_verify()
        journal_verify_legacy()
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


def journal_preopener():
    _journal_preopener_base()
    protocol_first_wire()
    _journal_preopener_base()


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
    global _GUARD_CREATED, _STATE_INITIALIZING
    _GUARD_CREATED = _STATE_INITIALIZING = False


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


def journal_close_acquired(descriptor, expected, primary=False):
    """Release the actual acquired descriptor, including read-only parents."""
    try:
        held = os.fstat(descriptor)
        if expected is None or (held.st_dev, held.st_ino) != expected:
            raise AdapterError("Acquired descriptor changed", "access")
        os.close(descriptor)
    except BaseException:
        journal_failed()
        if not primary:
            raise


def journal_verify_legacy():
    """Preserve authority of every genuinely held read-only legacy object."""
    try:
        for descriptor, expected in _GUARD_LEGACY_IDS.items():
            held = os.fstat(descriptor)
            current = os.stat(
                _GUARD_LEGACY_PATHS[descriptor], follow_symlinks=False
            )
            if (
                expected != (held.st_dev, held.st_ino)
                or expected != (current.st_dev, current.st_ino)
                or descriptor in _GUARD_LEGACY_VERSIONS
                and journal_generation(held)
                != _GUARD_LEGACY_VERSIONS[descriptor]
            ):
                raise AdapterError(
                    "Held legacy publisher object changed", "access"
                )
    except BaseException:
        journal_failed()
        raise


def journal_finish():
    """Release both independently; a healthy close failure is fatal."""
    global _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    global _STATE_DIR_FD, _STATE_DIR_ID
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
    while _LEGACY_FDS:
        held = _LEGACY_FDS.pop()
        expected = _GUARD_LEGACY_IDS.pop(held, None)
        _GUARD_LEGACY_PATHS.pop(held, None)
        _GUARD_LEGACY_VERSIONS.pop(held, None)
        try:
            journal_close_acquired(held, expected)
        except BaseException as error:
            errors.append(error)
    parent = _STATE_DIR_FD
    parent_id = _STATE_DIR_ID
    _STATE_DIR_FD = _STATE_DIR_ID = None
    if parent is not None:
        try:
            journal_close_acquired(parent, parent_id)
        except BaseException as error:
            errors.append(error)
    try:
        journal_release()
    except BaseException as error:
        journal_failed()
        errors.append(error)
    try:
        protocol_absence_release()
    except BaseException as error:
        journal_failed()
        errors.append(error)
    if _GUARD_FATAL:
        with contextlib.suppress(BaseException):
            export_family_artifact()
    if errors and primary is None:
        raise errors[0]


def _transport_bytes(
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
            pace(
                inventory=bool(_PROTOCOL_INVENTORY_DEPTH)
                and urllib.parse.urlsplit(current).path != "/robots.txt"
            )
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
            if publisher and response.status == 429:
                try:
                    record_retry_after(
                        response.headers.get("Retry-After"), rate_limited=True
                    )
                except BaseException:
                    journal_failed()
                    # Closing cannot replace the original persistence error.
                    with contextlib.suppress(BaseException):
                        response.close()
                    raise
                try:
                    response.close()
                except BaseException:
                    journal_failed()
                    raise
                raise AssessedStop(
                    "Publisher rate limit; timed embargo retained",
                    "access",
                    429,
                )
            with response:
                status = response.status
                response_headers = response.headers
                retry = response.headers.get("Retry-After")
                robots = (
                    publisher
                    and urllib.parse.urlsplit(url).path == "/robots.txt"
                )
                unavailable = robots and 400 <= status < 500 and status != 429
                if publisher:
                    record_retry_after(retry, rate_limited=status == 429)
                if publisher and not robots and status in (401, 403, 451):
                    with locked_budget():
                        state = load_budget()
                        protocol_current_owner(state)
                        state.update(blocked=True, observed_at=time.time())
                        save_budget(state)
                location = response.headers.get("Location")
                is_redirect = status in (301, 302, 303, 307, 308)
                if is_redirect and publisher:
                    if (
                        robots
                        and urllib.parse.urlsplit(
                            urllib.parse.urljoin(current, location or "")
                        ).path
                        != "/robots.txt"
                    ):
                        raise AdapterError(
                            "REP redirect changed literal policy purpose",
                            "access",
                        )
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
                if is_redirect and not publisher:
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
                if publisher and not unavailable:
                    if protocol_primary_challenge(body, status):
                        protocol_hold(
                            "Publisher displayed genuine access challenge",
                            status,
                        )
                    if status == 429:
                        raise AssessedStop(
                            "Publisher rate limit; timed embargo retained",
                            "access",
                            status,
                        )
                    if not robots and status in (401, 403, 451):
                        raise AssessedStop(
                            "Publisher refused content access", "access", status
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


def _read_input_base(key):
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
            "Reviewed Bosch material or frontier changed: " + key, "parse"
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


_REVIEWED_LOCAL_ABSENCE_TRANSITION = {
    "schema": 1,
    "source": "de-bosch",
    "purpose": "LOCAL_FINITE_ABSENCE",
    "root_purpose": "PROSPECTIVE_FIRST_LOCAL_INITIALIZATION",
    "observation_sha256": "5b7cfa0ce31123606ff3342ff91e1458ec5f061824df46fe95d1c1a7c1f9d86b",
    "history_sha256": "e5bfde81d0a49b00678285162c2d36afc9cba4817e193eacf3e60d9b9485137b",
    "owner": {
        "run_id": 38763419264,
        "attempt": 1,
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "job_id": 116520332522,
        "artifact_id": 11988456639,
        "artifact_digest": "sha256:af0f2696e633e013349caecbb6d28c34794b2169cb0536a9c41021e2d03633e3",
        "source_sha256": "1e25a4b2e8ac43e637c77e2a6ba544cf480eb52919617d87f0d6eab5a2df5c1c",
    },
    "owned_state_sha256": "3e438cfa7a509087c86056e0348780ddcaa11d999697e6acac55de43cf036356",
    "seed": {
        "schema": 3,
        "family": "youthopps-bosch-publisher-v1",
        "observed_at": 1792139910.553088,
        "not_before": 1792139970.553088,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "completed_at": 1792080130.0,
        "inventory_completed_at": 1792080130.0,
        "rate_limit_count": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "legacy_policy_history_unknown": True,
        "legacy_completion_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "policy_pending": None,
        "last_policy_receipt": None,
        "policy_allowed": {},
        "policy_denied": {},
        "last_settled_owner": None,
        "transition_id": None,
    },
    "seed_sha256": "cd4d453fbc0c33bec487dde7f45795f4744ee3be410f2b717da5b29206927482",
    "age_fold": {
        "cutoff": 1792139850.553088,
        "full_ledger_sha256": "8017e064c309665f1a076308baf462c819d9da06df90c8527fbe4f0fddb0c308",
        "full_union_sha256": "5368f3a56d4375a5958730b6cad1c1b9635cdad12559b179cee08fc9fe5bd535",
        "removed_count": 20,
        "retained_count": 0,
    },
    "absence_pins": {
        "ancestor_path": "/tmp",
        "temporary_name": "tmp",
        "ancestor_id": [34, 1],
        "ancestor_uid": 65534,
        "ancestor_mode": 1023,
        "modern_component": "youthopps-bosch-publisher-v1-1000",
        "missing_components": [
            "youthopps-bosch-publisher-v1-1000",
            "issue119-bosch-research",
            "youthopps-source-triage",
        ],
    },
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/322#issuecomment-6213077821",
    "once_claim": {
        "nonce": "69355efef8111e3c326be1f3e83b4b0b3b9b498dd5cee479b670045c47623e40",
        "name": "youthopps-bosch-publisher-v1-local-once-1000-69355efef8111e3c326be1f3e83b4b0b3b9b498dd5cee479b670045c47623e40.claim",
        "purpose": "PROSPECTIVE_FIRST_LOCAL_INITIALIZATION",
        "observation_sha256": "ad8cb28048f8545f173e5dfab858c417d14842d8fff3a33858b1023e32f77b89",
    },
}
_PROTOCOL_ABSENCE_ANCHORS = None
_PROTOCOL_ABSENCE_ONCE = None
_PROTOCOL_ABSENCE_BASELINE = {
    "schema": 1,
    "source": "de-bosch",
    "purpose": "LOCAL_FINITE_ABSENCE",
    "root_purpose": "PROSPECTIVE_FIRST_LOCAL_INITIALIZATION",
    "observation_sha256": (
        "5b7cfa0ce31123606ff3342ff91e1458ec5f061824df46fe95d1c1a7c1f9d86b"
    ),
    "history_sha256": (
        "e5bfde81d0a49b00678285162c2d36afc9cba4817e193eacf3e60d9b9485137b"
    ),
    "owner": {
        "run_id": 38763419264,
        "attempt": 1,
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "job_id": 116520332522,
        "artifact_id": 11988456639,
        "artifact_digest": (
            "sha256:af0f2696e633e013349caecbb6d28c34794b2169cb0536a9c41021e2d03633e3"
        ),
        "source_sha256": (
            "1e25a4b2e8ac43e637c77e2a6ba544cf480eb52919617d87f0d6eab5a2df5c1c"
        ),
    },
    "owned_state_sha256": (
        "3e438cfa7a509087c86056e0348780ddcaa11d999697e6acac55de43cf036356"
    ),
    "seed": {
        "schema": 3,
        "family": "youthopps-bosch-publisher-v1",
        "observed_at": 1792139910.553088,
        "not_before": 1792139970.553088,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "completed_at": 1792080130.0,
        "inventory_completed_at": 1792080130.0,
        "rate_limit_count": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "legacy_policy_history_unknown": True,
        "legacy_completion_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "policy_pending": None,
        "last_policy_receipt": None,
        "policy_allowed": {},
        "policy_denied": {},
        "last_settled_owner": None,
        "transition_id": None,
    },
    "seed_sha256": (
        "cd4d453fbc0c33bec487dde7f45795f4744ee3be410f2b717da5b29206927482"
    ),
    "age_fold": {
        "cutoff": 1792139850.553088,
        "full_ledger_sha256": (
            "8017e064c309665f1a076308baf462c819d9da06df90c8527fbe4f0fddb0c308"
        ),
        "full_union_sha256": (
            "5368f3a56d4375a5958730b6cad1c1b9635cdad12559b179cee08fc9fe5bd535"
        ),
        "removed_count": 20,
        "retained_count": 0,
    },
    "absence_pins": {
        "ancestor_path": "/tmp",
        "temporary_name": "tmp",
        "ancestor_id": [34, 1],
        "ancestor_uid": 65534,
        "ancestor_mode": 1023,
        "modern_component": "youthopps-bosch-publisher-v1-1000",
        "missing_components": [
            "youthopps-bosch-publisher-v1-1000",
            "issue119-bosch-research",
            "youthopps-source-triage",
        ],
    },
}

_REVIEWED_LOCAL_TRANSITION = None
_REVIEWED_HOSTED_TRANSITION = {
    "schema": 1,
    "source": "de-bosch",
    "purpose": "HOSTED",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/322#issuecomment-6213889587",
    "history_sha256": "e5bfde81d0a49b00678285162c2d36afc9cba4817e193eacf3e60d9b9485137b",
    "owner": {
        "run_id": 38763419264,
        "attempt": 1,
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "job_id": 116520332522,
        "artifact_id": 11988456639,
        "artifact_digest": "sha256:af0f2696e633e013349caecbb6d28c34794b2169cb0536a9c41021e2d03633e3",
        "source_sha256": "1e25a4b2e8ac43e637c77e2a6ba544cf480eb52919617d87f0d6eab5a2df5c1c",
    },
    "seed": {
        "schema": 3,
        "family": "youthopps-bosch-publisher-v1",
        "observed_at": 1792147733.1138608,
        "not_before": 1792147793.1138608,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "completed_at": 1792146202.8375678,
        "inventory_completed_at": 1792146189.735272,
        "rate_limit_count": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "legacy_policy_history_unknown": True,
        "legacy_completion_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "policy_pending": None,
        "last_policy_receipt": {
            "owner": "dc5c4be60597a99264b25e7e12b242cb",
            "origin": "https://www.bosch.us",
            "target_sha256": "fff8bd04c53bf5bdd4a3f7e6df03203ef8a9377e41211aa96ae902ec6da77bc3",
            "body_sha256": "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
            "available": True,
            "allowed": True,
            "delay": 0,
            "rate": None,
        },
        "policy_allowed": {
            "https://www.bosch.africa": [
                "133c66abaafaa47b7375111b727c3571",
                "8831cc09d40521af643670fcfd587a8f6f6d2a5a90ac3f372211f6301d81116d",
                "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.at": [
                "157520ee205d38f40788096f1727640a",
                "9930fc61d300fc035c82a03a308fb216405fb783fb7e1e644c6f2a181dde7ceb",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.ca": [
                "09c03a1ee21d32e91adca61dcfeedcdb",
                "9bd2a93a5b514986a13e489ba3a1a32cc83f976553a2f7b74bb8f2e263457a1f",
                "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.ch": [
                "6f96a54a06a990783c8d30b05b6c30f2",
                "3488cdcb6509163ca250fe2cb0c89999f7b0beabb84fcf29fc68dfb56b9a540d",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.com.cn": [
                "6e78f993260859f337dfe42214a720e6",
                "a9100208fb5885e8ee207867897025b99350588983f063c1cc84c1a5260e18a9",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.com.sg": [
                "0b5bfe4fee2ca708001ea35f3f19341b",
                "dfa8be2daca997797635765fddee122f843d201a49d9389c62da23c59fd4ab50",
                "6763f04d00f1166333a88d3510c6729e649ff60818bb1e633d034ed54b215d7e",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.com.tr": [
                "244afdd85a6b6bc1856f1c9a97b68f06",
                "695f993ca4c1cbe982defb5e04ac5c25f0f84a2104a8b7ae1fb65b2cafe0e8bd",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.com": [
                "1bceba79acd3f748027a21998d7a61e5",
                "b15f16521c38e94aa82de157fba238bbfd29abfc616bfa536c540ac58f762b0f",
                "7f03f031ad7213632f4c0bda4daf101371e779a6c8181e02a225d980b3d45da7",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.cz": [
                "7ca007f2c92cc217e403f7cf8528df63",
                "39724e518d2ed0d49bf62d2feb10d242064bb317133a68526215fd14aa3e5682",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.de": [
                "279f535889e986c44a541fc853813791",
                "2c97a981b365eb75da4db55430dd6d07e827ad1bfca904fe31bba628bd510810",
                "9d191636043dd361d07221a83532669ccf8c6919bbc6b63100de701160d015c9",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.dk": [
                "b7e9c3fe52c6fe4d11cbd65ec2e829a0",
                "2f9e0f49eaa9fb277f1a167d499172cbc6b159af5bf97cc11eaa90f80653a094",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.fr": [
                "2e32dda63629402d741598e6c6344234",
                "75f2f24fba3432bce56215cced074a5264402af0742f506de83ac7290e3e24ec",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.hu": [
                "ca16db7cf46aaf58631a78f4dcf1dbb6",
                "e39f5ca9189e23e0c9136b2276f99273e0805b7ac8ae19f5c7e0a820dd10606b",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.in": [
                "09caf8f9f04888e88a1ee7d2ea7166b3",
                "1735bbec203a5b01418e5cacea0067ff572ffe282a78a212fc01eddf945afa7a",
                "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.it": [
                "c4c847b47b30dc91fa6a4d117599832c",
                "2ddafb0041dffe86a4196ed65ba57db381903344839f5a2280074c433aa50640",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.nl": [
                "2b1b0a9de4f3f3c03e40b0eb4e774bb5",
                "685af4f71e5e790d726b080607bd48b9df24cd14177d35fc35d6901dd76660f7",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.pl": [
                "25f70bc41f596260ee5a1e5392532cac",
                "cb92741218ec3acba49fb7c9cbe9ad648383c342524ceffa494b612ac6ad633e",
                "ae43f983986fef50defdc9c3ab497a016e6f49ad1e1517850af2202f2b918548",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.pt": [
                "7e4f73d167e315c1128da87776966991",
                "ee4331002fcce5a831b9f00bfffc88307029fa8187ee5723f1e9cdd663cc8bdc",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.ro": [
                "36803343207d4be7e5cd063defd6e6df",
                "619b926b2d80789fa615a8aa6a5647ee8226756e159cc2ab037b5424b4657e75",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.se": [
                "06d1ae46f396bb774bf7cea9e15ca82c",
                "fe38714bbf070e07ab64684d6a76f94b3a79d10d0273100383cb715c041991a4",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
            "https://www.bosch.us": [
                "07e7216df32014d60599a9899abc06d7",
                "a011b214fb66eac4210da47df294996d23ed03295bc84eb98a32983436ed88bf",
                "9413b582ba30e2177c263ea05ad47889c9612aaf120a03494eaababe7130fb6b",
                True,
                True,
                0,
                None,
            ],
            "https://www.grupo-bosch.es": [
                "18ba13cfdef5aa7a0f14b41be89b85ec",
                "cab8b3abddadfb264dfa6040ccf16663ddb877670d902ab7c282e067f63c260d",
                "b4703ca8b1c7cdf98d9bcdc7bd7b7baa7cc24ccd02c009b8a4bef88fa0551fc0",
                True,
                True,
                0,
                None,
            ],
        },
        "policy_denied": {},
        "last_settled_owner": {
            "owner": "dc5c4be60597a99264b25e7e12b242cb",
            "receipt_sha256": "098d18a660dc0821ad6d6212eb24d409d899e910e7646ca75e0320ec6e5cf825",
            "completed_at": 1792146202.8668375,
        },
        "transition_id": "5c14e23df6bdae76b04b793187f86d79f0a0066fd502c7fcc6ef33b16ecb53b2",
    },
    "seed_sha256": "c209a7d6adbb75ab6f8c0f9ce36e05124a547ea8840af8320ea5e9ed8a562942",
    "local_pins": None,
}
_REVIEWED_MATURE_LOCAL_REENTRY = None
_PROTOCOL_READY = False
_PROTOCOL_FIRST_WIRE = False
_PROTOCOL_CONTEXT = None
_PROTOCOL_DEPTH = 0
_PROTOCOL_OWNER = None
_PROTOCOL_EXPECTED_RECEIPT = None
_PROTOCOL_POLICY_RAW = {}
_PROTOCOL_INVENTORY_DEPTH = 0
_PROTOCOL_SEQUENCE = 0
_PROTOCOL_PHYSICAL_INVENTORY = False
_PROTOCOL_WORKFLOW_ID = 380774834
_PROTOCOL_REPOSITORY_ID = 1163601208
_PROTOCOL_INVENTORY_KEYS = {
    "input-00",
    "input-04",
    "input-09",
    "input-18",
    "input-19",
    "input-29",
    "input-44",
    "input-45",
}


class AssessedStop(AdapterError):
    """Stop after actual restrictive response/REP state is fully assessed."""


def protocol_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def protocol_origins():
    return {
        urllib.parse.urlsplit(info["url"]).scheme
        + "://"
        + urllib.parse.urlsplit(info["url"]).netloc
        for info in INPUTS.values()
    }


def protocol_target(url):
    parsed = urllib.parse.urlsplit(public_url(url))
    return (
        parsed.scheme + "://" + parsed.netloc,
        hashlib.sha256(
            urllib.parse.urlunsplit(parsed._replace(fragment="")).encode()
        ).hexdigest(),
    )


def protocol_number(value):
    return type(value) in (int, float) and numeric(value)


def protocol_observation(value):
    fields = {
        "owner",
        "origin",
        "target_sha256",
        "body_sha256",
        "available",
        "allowed",
        "delay",
        "rate",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise AdapterError("Invalid policy receipt shape", "access")
    if (
        not re.fullmatch(r"[0-9a-f]{32}", str(value["owner"]))
        or value["origin"] not in protocol_origins()
        or not re.fullmatch(r"[0-9a-f]{64}", str(value["target_sha256"]))
    ):
        raise AdapterError("Invalid policy receipt identity", "access")
    if (
        type(value["available"]) is not bool
        or type(value["allowed"]) is not bool
        or not protocol_number(value["delay"])
    ):
        raise AdapterError("Invalid policy receipt decision", "access")
    if (
        value["available"]
        and not re.fullmatch(r"[0-9a-f]{64}", str(value["body_sha256"]))
    ) or (not value["available"] and value["body_sha256"] is not None):
        raise AdapterError("Invalid actual policy body provenance", "access")
    targets = {
        protocol_target(info["url"]): info["format"] for info in INPUTS.values()
    }
    route = targets.get((value["origin"], value["target_sha256"]))
    if route is None or route == "robots" and not value["allowed"]:
        raise AdapterError(
            "Policy receipt outside fixed target purpose", "access"
        )
    rate = value["rate"]
    if rate is not None and (
        not isinstance(rate, dict)
        or set(rate) != {"requests", "seconds"}
        or type(rate["requests"]) is not int
        or not 1 <= rate["requests"] <= 1000000
        or not protocol_number(rate["seconds"])
        or rate["seconds"] <= 0
    ):
        raise AdapterError("Invalid policy request rate", "access")
    return value


def protocol_history_pack(witness, denied):
    """Losslessly omit only the origin/target duplicated by the map key."""
    protocol_observation(witness)
    if witness["allowed"] is denied:
        raise AdapterError("Historical policy decision mismatch", "access")
    return [
        witness["owner"],
        witness["origin"] if denied else witness["target_sha256"],
        witness["body_sha256"],
        witness["available"],
        witness["allowed"],
        witness["delay"],
        witness["rate"],
    ]


def protocol_history_unpack(name, packed, denied):
    if not isinstance(packed, list) or len(packed) != 7:
        raise AdapterError("Invalid compact historical policy shape", "access")
    owner, identity, body, available, allowed, delay, rate = packed
    witness = {
        "owner": owner,
        "origin": identity if denied else name,
        "target_sha256": name if denied else identity,
        "body_sha256": body,
        "available": available,
        "allowed": allowed,
        "delay": delay,
        "rate": rate,
    }
    protocol_observation(witness)
    if allowed is denied:
        raise AdapterError("Compact policy map decision mismatch", "access")
    return witness


def protocol_join(left, right):
    left, right = validate_budget(left), validate_budget(right)
    if left["in_flight"] or right["in_flight"]:
        raise AdapterError(
            "Unsettled state cannot restore older allowance", "access"
        )
    result = json.loads(json.dumps(left))
    for key in [
        "observed_at",
        "not_before",
        "interval",
        "page_interval",
        "completed_at",
        "inventory_completed_at",
        "rate_limit_count",
        "rate_limit_until",
    ]:
        result[key] = max(left[key], right[key])
    for key in [
        "blocked",
        "legacy_rate_history_unknown",
        "legacy_policy_history_unknown",
        "legacy_completion_history_unknown",
    ]:
        result[key] = left[key] or right[key]
    union = sorted(set(left["starts"] + right["starts"]))
    if len(union) > 10:
        result["not_before"] = max(result["not_before"], union[-1] + 60)
    result["starts"] = union[-10:]
    for key in ["policy_allowed", "policy_denied"]:
        denied = key == "policy_denied"
        for target, packed in right[key].items():
            witness = protocol_history_unpack(target, packed, denied)
            current = result[key].get(target)
            if current is None or json.dumps(
                witness, sort_keys=True
            ) < json.dumps(
                protocol_history_unpack(target, current, denied), sort_keys=True
            ):
                result[key][target] = json.loads(json.dumps(packed))
    # Current-owner receipts never become permission or chronology authority.
    result["last_policy_receipt"] = None
    result["last_settled_owner"] = None
    result["observed_at"] = max(time.time(), result["observed_at"])
    return validate_budget(result)


@contextlib.contextmanager
def protocol_transaction(kind):
    global _PROTOCOL_DEPTH, _PROTOCOL_OWNER, _PROTOCOL_EXPECTED_RECEIPT
    if not _PROTOCOL_READY or _GUARD_FATAL:
        raise AdapterError("Reviewed publisher continuity required", "access")
    outer = _PROTOCOL_DEPTH == 0
    if outer:
        with locked_budget():
            state = load_budget()
            if state["in_flight"] or state["blocked"]:
                raise AdapterError(
                    "Retained pending state or access hold", "access"
                )
            owner = os.urandom(16).hex()
            state.update(
                in_flight=True,
                flight_id=owner,
                policy_pending=None,
                last_policy_receipt=None,
                last_settled_owner=None,
                observed_at=time.time(),
            )
            save_budget(state)
        _PROTOCOL_OWNER = owner
        _PROTOCOL_EXPECTED_RECEIPT = None
    _PROTOCOL_DEPTH += 1
    primary = None
    assessed = False
    try:
        yield
        assessed = True
    except AssessedStop as error:
        primary = error
        assessed = True
        raise
    except BaseException as error:
        primary = error
        journal_failed()
        raise
    finally:
        _PROTOCOL_DEPTH -= 1
        if outer:
            try:
                if assessed and not _GUARD_FATAL:
                    with locked_budget():
                        state = load_budget()
                        if (
                            not state["in_flight"]
                            or state["flight_id"] != _PROTOCOL_OWNER
                            or state["policy_pending"] is not None
                            or state["last_policy_receipt"]
                            != _PROTOCOL_EXPECTED_RECEIPT
                        ):
                            raise AdapterError(
                                "Exact nested policy receipt missing", "access"
                            )
                        state["last_settled_owner"] = {
                            "owner": _PROTOCOL_OWNER,
                            "receipt_sha256": protocol_digest(
                                _PROTOCOL_EXPECTED_RECEIPT
                            ),
                            "completed_at": time.time(),
                        }
                        state.update(
                            in_flight=False,
                            flight_id=None,
                            observed_at=time.time(),
                        )
                        save_budget(state)
                else:
                    journal_failed()
            except BaseException:
                journal_failed()
                if primary is None:
                    raise
            finally:
                _PROTOCOL_OWNER = _PROTOCOL_EXPECTED_RECEIPT = None


def protocol_current_owner(state):
    if (
        not state["in_flight"]
        or state["flight_id"] != _PROTOCOL_OWNER
        or _PROTOCOL_OWNER is None
    ):
        raise AdapterError("Publisher pending owner changed", "access")


@contextlib.contextmanager
def protocol_inventory():
    global _PROTOCOL_INVENTORY_DEPTH
    with protocol_transaction("inventory"):
        _PROTOCOL_INVENTORY_DEPTH += 1
        try:
            yield
        finally:
            primary = sys.exc_info()[1]
            _PROTOCOL_INVENTORY_DEPTH -= 1
            try:
                if not _GUARD_FATAL:
                    with locked_budget():
                        state = load_budget()
                        protocol_current_owner(state)
                        state["inventory_completed_at"] = max(
                            state["inventory_completed_at"], time.time()
                        )
                        state["observed_at"] = time.time()
                        save_budget(state)
            except BaseException:
                journal_failed()
                if primary is None:
                    raise


def fetch_inventory_page(fetch, *args, **kwargs):
    with protocol_inventory():
        return fetch(*args, **kwargs)


def protocol_timestamp(value):
    if not isinstance(value, str):
        raise AdapterError("Missing genuine job time", "access")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError()
        result = parsed.timestamp()
        if not protocol_number(result) or result > time.time() + 1:
            raise ValueError()
        return result
    except (ValueError, OverflowError, OSError):
        raise AdapterError("Invalid genuine job chronology", "access") from None


def protocol_workflow_definition():
    obj = workflow_api("/actions/workflows/" + str(_PROTOCOL_WORKFLOW_ID))
    if (
        not isinstance(obj, dict)
        or obj.get("id") != _PROTOCOL_WORKFLOW_ID
        or obj.get("path") != ".github/workflows/fetch-de-bosch.yml"
    ):
        raise AdapterError("Workflow definition identity changed", "access")
    expected = (
        "active"
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else "disabled_manually"
    )
    if obj.get("state") != expected:
        raise AdapterError("Publisher workflow coordination changed", "access")
    return obj


def protocol_run(run, expected=None):
    if (
        not isinstance(run, dict)
        or run.get("repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("repository", {}).get("id") != _PROTOCOL_REPOSITORY_ID
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("id") != _PROTOCOL_REPOSITORY_ID
        or run.get("head_branch") != "main"
        or run.get("workflow_id") != _PROTOCOL_WORKFLOW_ID
        or run.get("path", "").split("@", 1)[0]
        != ".github/workflows/fetch-de-bosch.yml"
    ):
        raise AdapterError("Untrusted publisher run identity", "access")
    identity, attempt, head = (
        run.get("id"),
        run.get("run_attempt"),
        run.get("head_sha"),
    )
    if (
        type(identity) is not int
        or not 1 <= identity < 2**63
        or type(attempt) is not int
        or not 1 <= attempt < 2**31
        or not isinstance(head, str)
        or not re.fullmatch(r"[0-9a-f]{40}", head)
        or expected is not None
        and (identity, attempt, head) != expected
    ):
        raise AdapterError("Invalid publisher run attempt", "access")
    return identity, attempt, head


def protocol_jobs(identity, attempt, head, current=False):
    jobs = []
    total = None
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}"
        )
        if (
            not isinstance(result, dict)
            or type(result.get("total_count")) is not int
            or result["total_count"] < 1
            or not isinstance(result.get("jobs"), list)
        ):
            raise AdapterError("Missing exact-attempt job inventory", "access")
        if total is None:
            total = result["total_count"]
        if total != result["total_count"]:
            raise AdapterError("Job inventory changed", "access")
        jobs.extend(result["jobs"])
        if len(jobs) >= total:
            break
        if len(result["jobs"]) != 100:
            raise AdapterError("Incomplete job pagination", "access")
    if (
        len(jobs) != total
        or len({j.get("id") for j in jobs}) != total
        or len(jobs) != 1
    ):
        raise AdapterError("Ambiguous publisher job ownership", "access")
    job = jobs[0]
    if (
        type(job.get("id")) is not int
        or job["id"] <= 0
        or type(job.get("run_id")) is not int
        or type(job.get("run_attempt")) is not int
        or job.get("run_id") != identity
        or job.get("run_attempt") != attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("name") != "collect"
    ):
        raise AdapterError("Exact job fields do not match attempt", "access")
    began = protocol_timestamp(job.get("started_at"))
    if current:
        if (
            job.get("status") != "in_progress"
            or job.get("completed_at") is not None
            or job.get("conclusion") is not None
        ):
            raise AdapterError("Current publisher job is not running", "access")
        finished = None
    else:
        if job.get("status") != "completed" or job.get("conclusion") not in {
            "success",
            "failure",
            "cancelled",
            "timed_out",
            "skipped",
            "action_required",
            "neutral",
            "startup_failure",
            "stale",
        }:
            raise AdapterError("Publisher attempt job not terminal", "access")
        finished = protocol_timestamp(job.get("completed_at"))
        if finished < began:
            raise AdapterError("Job completion precedes start", "access")
    return {
        "id": identity,
        "attempt": attempt,
        "head": head,
        "job_id": job["id"],
        "started_at": began,
        "completed_at": finished,
        "conclusion": job.get("conclusion"),
        "job_sha256": protocol_digest(job),
    }


def protocol_history():
    """Authenticate current run and every full terminal attempt chronology."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = None
    if hosted:
        if (
            os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"
            or os.environ.get("GITHUB_JOB") != "collect"
            or os.environ.get("GITHUB_WORKFLOW_REF")
            != (
                "YouthOpps/data-pipeline/.github/workflows/"
                "fetch-de-bosch.yml@refs/heads/main"
            )
        ):
            raise AdapterError("Unexpected hosted execution context", "access")
        values = [
            os.environ.get(k, "")
            for k in ["GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"]
        ]
        head = os.environ.get("GITHUB_SHA", "")
        if (
            any(not re.fullmatch(r"[1-9][0-9]{0,18}", v) for v in values)
            or not re.fullmatch(r"[0-9a-f]{40}", head)
            or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        ):
            raise AdapterError("Incomplete hosted source identity", "access")
        current = int(values[0]), int(values[1]), head
    definition = protocol_workflow_definition()
    rows, total = [], None
    for page in range(1, 101):
        obj = workflow_api(
            f"/actions/workflows/{_PROTOCOL_WORKFLOW_ID}/runs"
            f"?per_page=100&page={page}"
        )
        if (
            not isinstance(obj, dict)
            or type(obj.get("total_count")) is not int
            or obj["total_count"] < 1
            or not isinstance(obj.get("workflow_runs"), list)
        ):
            raise AdapterError("Mature publisher history unavailable", "access")
        if total is None:
            total = obj["total_count"]
        if total != obj["total_count"]:
            raise AdapterError("Run inventory changed", "access")
        rows.extend(obj["workflow_runs"])
        if len(rows) >= total:
            break
        if len(obj["workflow_runs"]) != 100:
            raise AdapterError("Incomplete family history", "access")
    if len(rows) != total or len({r.get("id") for r in rows}) != total:
        raise AdapterError("Ambiguous family history", "access")
    completed, projection = [], []
    found_current = False
    for row in rows:
        identity, latest_attempt, head = protocol_run(row)
        projection.append(
            (
                identity,
                latest_attempt,
                head,
                row.get("status"),
                row.get("conclusion"),
            )
        )
        own = current is not None and identity == current[0]
        if own:
            if (identity, latest_attempt, head) != current or row.get(
                "status"
            ) != "in_progress":
                raise AdapterError("Current attempt context mismatch", "access")
            protocol_jobs(*current, current=True)
            found_current = True
        elif row.get("status") != "completed":
            raise AdapterError(
                "Another active or queued family attempt", "access"
            )
        for attempt in range(1, latest_attempt + 1):
            if own and attempt == latest_attempt:
                continue
            detail = workflow_api(
                f"/actions/runs/{identity}/attempts/{attempt}"
            )
            protocol_run(detail, (identity, attempt, head))
            if detail.get("status") != "completed":
                raise AdapterError("Prior attempt still unsettled", "access")
            record = protocol_jobs(identity, attempt, head)
            inventory = workflow_api(
                f"/actions/runs/{identity}/artifacts?per_page=100"
            )
            if (
                not isinstance(inventory, dict)
                or not isinstance(inventory.get("artifacts"), list)
                or type(inventory.get("total_count")) is not int
                or len(inventory["artifacts"]) != inventory["total_count"]
                or len(inventory["artifacts"]) > 100
            ):
                raise AdapterError("Incomplete artifact inventory", "access")
            record["artifacts"] = inventory["artifacts"]
            record["artifacts_sha256"] = protocol_digest(inventory)
            completed.append(record)
        after = workflow_api(f"/actions/runs/{identity}")
        if protocol_run(after) != (identity, latest_attempt, head) or after.get(
            "status"
        ) != row.get("status"):
            raise AdapterError(
                "Publisher attempt changed during history", "access"
            )
    if hosted and not found_current:
        raise AdapterError(
            "Current hosted attempt absent from history", "access"
        )
    if not completed:
        raise AdapterError("Mature source cannot use empty bootstrap", "access")
    ordered = sorted(
        completed, key=lambda r: (r["completed_at"], r["started_at"])
    )
    if (
        len(ordered) > 1
        and ordered[-1]["completed_at"] == ordered[-2]["completed_at"]
    ):
        raise AdapterError("Ambiguous newest job chronology", "access")
    latest = ordered[-1]
    candidates = [
        a
        for a in latest["artifacts"]
        if a.get("name") == _ARTIFACT_NAME + "-" + str(latest["attempt"])
    ]
    if len(candidates) != 1:
        raise AdapterError("Newest attempt lacks its owned artifact", "access")
    artifact = candidates[0]
    owner = artifact.get("workflow_run", {})
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("expired") is not False
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(artifact.get("digest")))
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= 32768
        or owner.get("id") != latest["id"]
        or owner.get("head_sha") != latest["head"]
        or owner.get("head_branch") != "main"
        or owner.get("repository_id") != _PROTOCOL_REPOSITORY_ID
        or owner.get("head_repository_id") != _PROTOCOL_REPOSITORY_ID
    ):
        raise AdapterError("Newest owned artifact binding mismatch", "access")
    fresh_rows = []
    for page in range(1, (total + 99) // 100 + 1):
        fresh = workflow_api(
            f"/actions/workflows/{_PROTOCOL_WORKFLOW_ID}/runs"
            f"?per_page=100&page={page}"
        )
        if (
            not isinstance(fresh, dict)
            or fresh.get("total_count") != total
            or not isinstance(fresh.get("workflow_runs"), list)
        ):
            raise AdapterError(
                "Family history changed during full read", "access"
            )
        fresh_rows.extend(fresh["workflow_runs"])
    fresh_projection = sorted(
        (*(protocol_run(r)), r.get("status"), r.get("conclusion"))
        for r in fresh_rows
    )
    if fresh_projection != sorted(projection) or len(fresh_rows) != total:
        raise AdapterError("Fresh family run prefix changed", "access")
    protocol_workflow_definition()
    stable = [
        {k: v for k, v in r.items() if k != "artifacts"}
        for r in sorted(completed, key=lambda x: (x["id"], x["attempt"]))
    ]
    return {
        "hosted": hosted,
        "current": current,
        "latest": latest,
        "artifact": artifact,
        "history_sha256": protocol_digest(stable),
        "run_projection": sorted(projection),
        "workflow_id": definition["id"],
        "completion_bounds": sorted({r["completed_at"] for r in completed}),
    }


def protocol_legacy_state(state):
    if (
        not isinstance(state, dict)
        or set(state) - {"interval"}
        != {
            "schema",
            "family",
            "observed_at",
            "not_before",
            "starts",
            "blocked",
        }
        or type(state["schema"]) is not int
        or state["schema"] != 1
        or state["family"] != FAMILY
        or type(state["blocked"]) is not bool
    ):
        raise AdapterError("Unreviewed legacy state shape", "access")
    if (
        any(
            not protocol_number(state.get(k, 6))
            for k in ["observed_at", "not_before", "interval"]
        )
        or state.get("interval", 6) < 6
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 11
        or any(
            not protocol_number(x) or x > state["observed_at"]
            for x in state["starts"]
        )
        or state["starts"] != sorted(set(state["starts"]))
        or state["observed_at"] > time.time() + 1
    ):
        raise AdapterError("Untrusted legacy timing provenance", "access")
    return state


def protocol_marker(context, marker, purpose):
    keys = {
        "schema",
        "source",
        "purpose",
        "evidence",
        "history_sha256",
        "owner",
        "seed",
        "seed_sha256",
        "local_pins",
    }
    if (
        not isinstance(marker, dict)
        or set(marker) != keys
        or marker["schema"] != 1
        or marker["source"] != SOURCE_ID
        or marker["purpose"] != purpose
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/322"
            r"#issuecomment-[0-9]+",
            str(marker["evidence"]),
        )
        or marker["history_sha256"] != context["history_sha256"]
    ):
        raise AdapterError(
            "Explicit reviewed mature transition required", "access"
        )
    latest, artifact = context["latest"], context["artifact"]
    expected = {
        "run_id": latest["id"],
        "attempt": latest["attempt"],
        "head": latest["head"],
        "job_id": latest["job_id"],
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
        "source_sha256": context["source_sha256"],
    }
    if (
        marker["owner"] != expected
        or protocol_digest(marker["seed"]) != marker["seed_sha256"]
    ):
        raise AdapterError(
            "Reviewed transition owner or MAX seed changed", "access"
        )
    seed = validate_budget(marker["seed"])
    if (
        seed["completed_at"] < max(context["completion_bounds"])
        or seed["not_before"]
        < max(context["completion_bounds"]) + seed["interval"]
    ):
        raise AdapterError(
            "Reviewed MAX omits known job completion bounds", "access"
        )
    if seed["in_flight"]:
        raise AdapterError(
            "Reviewed transition cannot ignore pending owner", "access"
        )
    if purpose == "HOSTED" and (
        not context["hosted"] or marker["local_pins"] is not None
    ):
        raise AdapterError(
            "HOSTED transition cannot impersonate LOCAL pins", "access"
        )
    if purpose in {"LOCAL", "MATURE_LOCAL"} and (
        context["hosted"] or not isinstance(marker["local_pins"], dict)
    ):
        raise AdapterError(
            "LOCAL transition requires exact existing namespace", "access"
        )
    return json.loads(json.dumps(seed))


def protocol_local_pins(raw):
    journal_verify()
    parent = os.fstat(journal_parent())
    state = os.fstat(_GUARD_FD)
    collection = os.fstat(_COLLECTION_LOCK_FD)
    lock = os.stat(
        os.path.basename(_STATE_LOCK),
        dir_fd=journal_parent(),
        follow_symlinks=False,
    )
    return {
        "parent": [parent.st_dev, parent.st_ino],
        "state": [state.st_dev, state.st_ino],
        "collection_lock": [collection.st_dev, collection.st_ino],
        "state_lock": [lock.st_dev, lock.st_ino],
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }


def protocol_legacy_into(seed, legacy):
    legacy = protocol_legacy_state(legacy)
    result = json.loads(json.dumps(seed))
    result["not_before"] = max(result["not_before"], legacy["not_before"])
    result["interval"] = max(result["interval"], legacy.get("interval", 6))
    result["blocked"] = result["blocked"] or legacy["blocked"]
    result["legacy_rate_history_unknown"] = result[
        "legacy_policy_history_unknown"
    ] = result["legacy_completion_history_unknown"] = True
    union = sorted(set(result["starts"] + legacy["starts"]))
    if len(union) > 10:
        result["not_before"] = max(result["not_before"], union[-1] + 60)
    result["starts"] = union[-10:]
    result["observed_at"] = max(
        result["observed_at"], legacy["observed_at"], time.time()
    )
    return validate_budget(result)


def protocol_policy_groups(text):
    """Retain group/rule order; select exact product identities separately."""
    groups, agents, entries = [], [], []
    for raw in text.splitlines():
        field = raw.partition("#")[0].strip()
        key, separator, value = field.partition(":")
        if not separator:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if entries:
                groups.append((agents, entries))
                agents, entries = [], []
            agents.append(value.split("/", 1)[0].lower())
        elif agents and key in {
            "allow",
            "disallow",
            "crawl-delay",
            "request-rate",
        }:
            entries.append((key, value))
    if agents:
        groups.append((agents, entries))
    return groups


def protocol_selected_policy_groups(groups):
    current = _USER_AGENT.split("/", 1)[0].lower()
    specific = [g for g in groups if current in g[0]]
    wildcard = [g for g in groups if "*" in g[0]]
    legacy = [g for g in groups if "youthopp" in g[0]]
    return (specific or wildcard) + legacy


def protocol_policy_parser(text):
    """Use native rule matching without parsing unrelated timing numbers."""
    import urllib.robotparser

    parser = urllib.robotparser.RobotFileParser()
    groups = protocol_policy_groups(text)
    parser._protocol_groups = groups
    parser._protocol_body_sha256 = hashlib.sha256(text.encode()).hexdigest()
    policy_lines = []
    for line in text.splitlines():
        key = line.partition(":")[0].strip().lower()
        policy_lines.append(
            "Crawl-delay: 0" if key in {"crawl-delay", "request-rate"} else line
        )
    parser.parse(policy_lines)
    return parser


class PrimaryText(HTMLParser):
    ("Ignore ordinary ancillary/script notices when "
     "recognizing real refusals.")

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


def protocol_primary_challenge(body, status):
    """Inspect actual HTML only; ancillary mentions are not access refusals."""
    prefix = body[:1024].lstrip().lower()
    if not re.match(
        rb"<(?:!doctype\s+html\b|html\b|head\b|body\b|main\b|"
        rb"h[1-6]\b|div\b|p\b)",
        prefix,
    ):
        return False
    text = PrimaryText(body.decode("utf-8", "replace"))
    title = " ".join(" ".join(text.head_title_parts).split()).lower()
    primary = " ".join(" ".join(text.parts).split()).lower()
    if re.search(
        r"just a moment|attention required|access denied|captcha|"
        r"verify you are human|checking your browser",
        title,
    ):
        return True
    phrases = [
        "verify you are human",
        "checking your browser",
        "complete the captcha",
        "security verification required",
        "unusual traffic from your network",
    ]
    if any(p in primary for p in phrases):
        return True
    if status == 429:
        return False
    # Preserve real standalone primary denials without matching help prose.
    return bool(
        re.fullmatch(
            r"(?:access denied|forbidden|request blocked)[.!\s]*", primary
        )
    )


def protocol_hold(message, status=None):
    with locked_budget():
        state = load_budget()
        protocol_current_owner(state)
        state.update(blocked=True, observed_at=time.time())
        save_budget(state)
    raise AssessedStop(message, "access", status)


def protocol_policy_values(text):
    """Read decimal timing from the same applicable REP product groups."""
    groups = protocol_selected_policy_groups(protocol_policy_groups(text))
    selected = [entry for _, entries in groups for entry in entries]
    delays, rates = [], []
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    for key, value in selected:
        if key == "crawl-delay":
            if not re.fullmatch(decimal, value):
                raise AdapterError(
                    "Unsupported applicable REP crawl delay", "access"
                )
            delay = float(value)
            if not protocol_number(delay):
                raise AdapterError("Nonfinite applicable REP delay", "access")
            delays.append(delay)
        elif key == "request-rate":
            match = re.fullmatch(r"([0-9]+)\s*/\s*(" + decimal + r")", value)
            if match is None:
                raise AdapterError(
                    "Unsupported applicable REP request rate", "access"
                )
            requests, seconds = int(match[1]), float(match[2])
            if (
                not 1 <= requests <= 1000000
                or not protocol_number(seconds)
                or seconds <= 0
            ):
                raise AdapterError(
                    "Invalid applicable REP request rate", "access"
                )
            rates.append({"requests": requests, "seconds": seconds})
    rate = max(rates, key=lambda r: r["seconds"] / r["requests"], default=None)
    return max(delays, default=0), rate


def protocol_assess_policy(url):
    """Commit this exact target's actual policy before its owner can settle."""
    global _PROTOCOL_EXPECTED_RECEIPT
    origin, target = protocol_target(url)
    with locked_budget():
        state = load_budget()
        protocol_current_owner(state)
        state["policy_pending"] = {
            "owner": _PROTOCOL_OWNER,
            "target_sha256": target,
        }
        state["observed_at"] = time.time()
        save_budget(state)
    policy = _PROTOCOL_POLICY_RAW.get(origin)
    if (
        policy is None
        or origin not in _ROBOTS
        or policy.get("collection") != _PROTOCOL_SEQUENCE
    ):
        raise AdapterError("Actual REP body provenance unavailable", "access")
    parser = _ROBOTS[origin]
    if policy["available"] and (
        hashlib.sha256(policy["text"].encode()).hexdigest() != policy["sha256"]
        or getattr(parser, "_protocol_body_sha256", None) != policy["sha256"]
    ):
        raise AdapterError("REP cache body provenance changed", "access")
    delay, rate = (
        protocol_policy_values(policy["text"])
        if policy["available"]
        else (0, None)
    )
    allowed = (
        True
        if urllib.parse.urlsplit(url).path == "/robots.txt"
        else robots_directive(_ROBOTS[origin], "can_fetch", url)
    )
    groups = getattr(parser, "_protocol_groups", [])
    current = _USER_AGENT.split("/", 1)[0].lower()
    scoped = any(current in names or "*" in names for names, _ in groups)
    if target in state["policy_denied"] and (
        not policy["available"] or not scoped
    ):
        allowed = False
    receipt = {
        "owner": _PROTOCOL_OWNER,
        "origin": origin,
        "target_sha256": target,
        "body_sha256": policy["sha256"],
        "available": policy["available"],
        "allowed": allowed,
        "delay": delay,
        "rate": rate,
    }
    protocol_observation(receipt)
    required = delay
    if rate is not None:
        required = max(required, rate["seconds"] / rate["requests"] + 0.01)
    if not protocol_number(required):
        raise AdapterError("Unsupported applicable REP arithmetic", "access")
    with locked_budget():
        state = load_budget()
        protocol_current_owner(state)
        if state["policy_pending"] != {
            "owner": _PROTOCOL_OWNER,
            "target_sha256": target,
        }:
            raise AdapterError("Policy assessment owner changed", "access")
        state["interval"] = max(state["interval"], required)
        state["not_before"] = max(
            state["not_before"], state["completed_at"] + state["interval"]
        )
        key, name = (
            ("policy_allowed", origin) if allowed else ("policy_denied", target)
        )
        old = state[key].get(name)
        if old is None or json.dumps(receipt, sort_keys=True) < json.dumps(
            protocol_history_unpack(name, old, not allowed), sort_keys=True
        ):
            state[key][name] = protocol_history_pack(receipt, not allowed)
        state.update(
            last_policy_receipt=receipt,
            policy_pending=None,
            observed_at=time.time(),
        )
        save_budget(state)
        _PROTOCOL_EXPECTED_RECEIPT = json.loads(json.dumps(receipt))
    if not allowed:
        raise AssessedStop(
            "Publisher robots excludes required Bosch input", "access"
        )
    return receipt


def protocol_source_hash(head):
    if not re.fullmatch(r"[0-9a-f]{40}", str(head)):
        raise AdapterError("Invalid immutable producer source ref", "access")
    result = workflow_api("/contents/adapters/de-bosch/adapter.py?ref=" + head)
    if (
        not isinstance(result, dict)
        or result.get("type") != "file"
        or result.get("path") != "adapters/de-bosch/adapter.py"
        or result.get("encoding") != "base64"
    ):
        raise AdapterError("Immutable producer source unavailable", "access")
    try:
        raw = base64.b64decode(
            result["content"].replace("\n", ""), validate=True
        )
    except (KeyError, ValueError, TypeError):
        raise AdapterError(
            "Invalid immutable producer source", "access"
        ) from None
    if (
        len(raw) > 2000000
        or result.get("size") != len(raw)
        or hashlib.sha1(
            b"blob " + str(len(raw)).encode() + b"\0" + raw
        ).hexdigest()
        != result.get("sha")
    ):
        raise AdapterError("Producer Git blob digest mismatch", "access")
    return hashlib.sha256(raw).hexdigest()


def protocol_decode(envelope, context):
    """Validate real ZIP ownership before any namespace can be initialized."""
    latest = context["latest"]
    base = {"schema", "repository", "run_id", "run_attempt", "source", "state"}
    modern = base | {"head_sha", "adapter_sha256", "state_sha256"}
    if (
        not isinstance(envelope, dict)
        or set(envelope) not in (base, modern)
        or type(envelope["schema"]) is not int
        or envelope["repository"] != "YouthOpps/data-pipeline"
        or envelope["source"] != SOURCE_ID
        or type(envelope["run_id"]) is not int
        or type(envelope["run_attempt"]) is not int
        or (envelope["run_id"], envelope["run_attempt"])
        != (latest["id"], latest["attempt"])
    ):
        raise AdapterError("Owned state envelope mismatch", "access")
    if set(envelope) == base:
        if envelope["schema"] != 1:
            raise AdapterError("Unreviewed legacy envelope", "access")
        return protocol_legacy_state(envelope["state"])
    if (
        envelope["schema"] != 2
        or envelope["head_sha"] != latest["head"]
        or envelope["adapter_sha256"] != context["source_sha256"]
        or envelope["state_sha256"] != protocol_digest(envelope["state"])
    ):
        raise AdapterError(
            "Modern producer/source/state digest mismatch", "access"
        )
    state = validate_budget(envelope["state"])
    if state["in_flight"] or state["policy_pending"] is not None:
        raise AdapterError(
            "Newest owned pending state requires review", "access"
        )
    return state


def protocol_same_history(expected):
    current = protocol_history()
    if (
        current["history_sha256"] != expected["history_sha256"]
        or current["run_projection"] != expected["run_projection"]
        or current["current"] != expected["current"]
        or current["artifact"] != expected["artifact"]
    ):
        raise AdapterError(
            "Fresh owner/history changed before publisher access", "access"
        )
    return current


def protocol_acquire_collection():
    global _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    if _COLLECTION_LOCK_FD is not None:
        raise AdapterError("Nested family collection refused", "access")
    parent = journal_parent()
    descriptor = identity = None
    try:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open("collection.lock", flags, dir_fd=parent)
        except FileNotFoundError:
            if not journal_creation_allowed():
                raise AdapterError("Mature collection lock missing", "access")
            descriptor = os.open(
                "collection.lock",
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent,
            )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        journal_owned(descriptor, identity)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_owned(descriptor, identity)
        current = os.stat(
            "collection.lock", dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Publisher collection lock changed", "access")
        _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID = descriptor, identity
    except BaseException:
        journal_failed()
        if descriptor is not None and identity is not None:
            journal_close(descriptor, identity, True)
        raise


def protocol_preflight_wire():
    if not _PROTOCOL_READY or _GUARD_FATAL:
        raise AdapterError(
            "Explicit mature continuity required before publisher access",
            "access",
        )


def protocol_first_wire():
    """Fresh authoritative coordination after quiet, under actual state lock."""
    global _PROTOCOL_FIRST_WIRE
    protocol_preflight_wire()
    if _PROTOCOL_FIRST_WIRE:
        return
    with locked_budget():
        protocol_absence_verify()
        protocol_same_history(_PROTOCOL_CONTEXT)
        state = load_budget()
        protocol_current_owner(state)
        if state["blocked"] or time.time() < max(
            _COLLECTION_STARTED + 60, state["rate_limit_until"]
        ):
            raise AdapterError(
                "Publisher quiet/hold changed before first wire", "access"
            )
        protocol_absence_verify()
        journal_verify()
        _PROTOCOL_FIRST_WIRE = True


def protocol_export_envelope(state, run_id, attempt):
    validate_budget(state)
    if (
        not _PROTOCOL_READY
        or _GUARD_FATAL
        or state["in_flight"]
        or state["policy_pending"] is not None
    ):
        raise AdapterError(
            "Unsettled publisher state cannot be uploaded", "access"
        )
    context = protocol_history()
    if not context["hosted"] or context["current"][:2] != (run_id, attempt):
        raise AdapterError(
            "Export requires genuine current hosted owner", "access"
        )
    head = context["current"][2]
    source_hash = hashlib.sha256(open(__file__, "rb").read()).hexdigest()
    if source_hash != protocol_source_hash(head):
        raise AdapterError("Export executing source/head mismatch", "access")
    envelope = {
        "schema": 2,
        "repository": "YouthOpps/data-pipeline",
        "run_id": run_id,
        "run_attempt": attempt,
        "source": SOURCE_ID,
        "head_sha": head,
        "adapter_sha256": source_hash,
        "state_sha256": protocol_digest(state),
        "state": state,
    }
    if len(json.dumps(envelope, separators=(",", ":")).encode()) > 16384:
        raise AdapterError(
            "Owned envelope exceeds original byte bound", "access"
        )
    return envelope


def protocol_absence_once_payload():
    """A root-bound nonce consumes one source-local prospective grant."""
    marker = _REVIEWED_LOCAL_ABSENCE_TRANSITION
    claim = marker.get("once_claim") if isinstance(marker, dict) else None
    if (
        not isinstance(claim, dict)
        or set(claim) != {"nonce", "name", "purpose", "observation_sha256"}
        or not isinstance(claim["nonce"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", claim["nonce"])
        or claim["name"]
        != (
            FAMILY
            + "-local-once-"
            + str(os.getuid())
            + "-"
            + claim["nonce"]
            + ".claim"
        )
        or claim["purpose"] != "PROSPECTIVE_FIRST_LOCAL_INITIALIZATION"
        or not isinstance(claim["observation_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", claim["observation_sha256"])
    ):
        raise AdapterError("Explicit finite source claim required", "access")
    value = {
        "schema": 1,
        "source": SOURCE_ID,
        "purpose": claim["purpose"],
        "nonce": claim["nonce"],
        "transition_sha256": protocol_digest(marker),
        "history_sha256": marker["history_sha256"],
        "owner_sha256": protocol_digest(marker["owner"]),
        "seed_sha256": marker["seed_sha256"],
        "evidence": marker["evidence"],
    }
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if len(payload) > 4096:
        raise AdapterError("Source claim exceeds reviewed bound", "access")
    return claim["name"], payload


def protocol_absence_once_verify():
    """Require original claim bytes/FD/name under the retained ancestor."""
    held = _PROTOCOL_ABSENCE_ONCE
    if held is None or _PROTOCOL_ABSENCE_ANCHORS is None:
        raise AdapterError("Original durable source claim required", "access")
    descriptor, identity, name, payload = held
    root, root_id, parent, parent_id, unused = _PROTOCOL_ABSENCE_ANCHORS
    pins = _PROTOCOL_ABSENCE_BASELINE["absence_pins"]
    root_info = os.fstat(root)
    named_root = os.stat("/", follow_symlinks=False)
    info = os.fstat(parent)
    named_parent = os.stat(
        pins["temporary_name"], dir_fd=root, follow_symlinks=False
    )
    if (
        (root_info.st_dev, root_info.st_ino) != root_id
        or (named_root.st_dev, named_root.st_ino) != root_id
        or (info.st_dev, info.st_ino) != parent_id
        or (named_parent.st_dev, named_parent.st_ino) != parent_id
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != pins["ancestor_uid"]
        or stat.S_IMODE(info.st_mode) != pins["ancestor_mode"]
    ):
        raise AdapterError("Source claim ancestor changed", "access")
    before = journal_owned(descriptor, identity)
    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (named.st_dev, named.st_ino) != identity:
        raise AdapterError("Source claim name changed", "access")
    raw = os.pread(descriptor, 4097, 0)
    after = journal_owned(descriptor, identity)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        raw != payload
        or len(raw) > 4096
        or journal_generation(before) != journal_generation(after)
        or journal_generation(after) != journal_generation(current)
    ):
        raise AdapterError("Original source claim bytes changed", "access")


def protocol_absence_once_create():
    """Persist consumption before mkdir; never unlink even a partial claim."""
    global _PROTOCOL_ABSENCE_ONCE
    if _PROTOCOL_ABSENCE_ONCE is not None:
        raise AdapterError("Nested source claim refused", "access")
    name, payload = protocol_absence_once_payload()
    protocol_absence_verify(True)
    parent = _PROTOCOL_ABSENCE_ANCHORS[2]
    descriptor = os.open(
        name,
        os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CREAT | os.O_EXCL,
        0o600,
        dir_fd=parent,
    )
    info = os.fstat(descriptor)
    identity = (info.st_dev, info.st_ino)
    _PROTOCOL_ABSENCE_ONCE = descriptor, identity, name, payload
    journal_owned(descriptor, identity)
    offset = 0
    while offset < len(payload):
        protocol_absence_verify(True)
        journal_owned(descriptor, identity)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != identity:
            raise AdapterError("New source claim name changed", "access")
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise AdapterError("Incomplete source claim write", "access")
        offset += written
    os.fsync(descriptor)
    protocol_absence_once_verify()
    os.fsync(parent)
    protocol_absence_verify(True)
    protocol_absence_once_verify()


def protocol_absence_marker(context, incoming):
    """Validate a finite prospective boundary, never infer old settlement."""
    marker = _REVIEWED_LOCAL_ABSENCE_TRANSITION
    expected = _PROTOCOL_ABSENCE_BASELINE
    if (
        context["hosted"]
        or os.environ.get("GITHUB_ACTIONS") == "true"
        or not isinstance(marker, dict)
        or set(marker) != set(expected) | {"evidence", "once_claim"}
        or protocol_digest({k: marker[k] for k in expected})
        != protocol_digest(expected)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/322"
            r"#issuecomment-[0-9]+",
            str(marker["evidence"]),
        )
    ):
        raise AdapterError(
            "Exact prospective LOCAL absence approval required", "access"
        )
    protocol_absence_once_payload()
    latest, artifact = context["latest"], context["artifact"]
    identity = {
        "run_id": latest["id"],
        "attempt": latest["attempt"],
        "head": latest["head"],
        "job_id": latest["job_id"],
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
        "source_sha256": context["source_sha256"],
    }
    if (
        context["history_sha256"] != marker["history_sha256"]
        or identity != marker["owner"]
        or incoming["schema"] != 1
        or protocol_digest(incoming) != marker["owned_state_sha256"]
        or protocol_digest(marker["seed"]) != marker["seed_sha256"]
    ):
        raise AdapterError("Finite absence provenance changed", "access")
    seed = validate_budget(json.loads(json.dumps(marker["seed"])))
    if (
        seed["blocked"]
        or seed["in_flight"]
        or seed["policy_pending"] is not None
        or not all(
            seed[k]
            for k in [
                "legacy_rate_history_unknown",
                "legacy_policy_history_unknown",
                "legacy_completion_history_unknown",
            ]
        )
        or seed["completed_at"] < max(context["completion_bounds"])
        or seed["not_before"] < seed["completed_at"] + seed["interval"]
    ):
        raise AdapterError("Unreviewed prospective absence seed", "access")
    # Full legacy starts are covered by the separately approved age proof.
    if any(t >= marker["age_fold"]["cutoff"] for t in incoming["starts"]):
        raise AdapterError("Legacy starts no longer match age proof", "access")
    return seed


def protocol_absence_verify(before_claim=False):
    """Retain original ancestors and exact names through first-wire check."""
    held = _PROTOCOL_ABSENCE_ANCHORS
    if held is None:
        return
    root, root_id, temporary, temporary_id, leaf_id = held
    pins = _PROTOCOL_ABSENCE_BASELINE["absence_pins"]
    root_info = os.fstat(root)
    root_named = os.stat("/", follow_symlinks=False)
    info = os.fstat(temporary)
    named = os.stat(pins["temporary_name"], dir_fd=root, follow_symlinks=False)
    if (
        (root_info.st_dev, root_info.st_ino) != root_id
        or (root_named.st_dev, root_named.st_ino) != root_id
        or not stat.S_ISDIR(root_info.st_mode)
        or (info.st_dev, info.st_ino) != temporary_id
        or (named.st_dev, named.st_ino) != temporary_id
        or list(temporary_id) != pins["ancestor_id"]
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != pins["ancestor_uid"]
        or stat.S_IMODE(info.st_mode) != pins["ancestor_mode"]
    ):
        raise AdapterError("Original absence ancestor changed", "access")
    for name in pins["missing_components"]:
        if not before_claim and name == FAMILY + "-" + str(os.getuid()):
            info = os.stat(name, dir_fd=temporary, follow_symlinks=False)
            if leaf_id is None or (info.st_dev, info.st_ino) != leaf_id:
                raise AdapterError("Claimed LOCAL namespace changed", "access")
            verify_state_directory()
        else:
            try:
                os.stat(name, dir_fd=temporary, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise AdapterError("Reviewed absent component now exists", "access")

    if not before_claim:
        protocol_absence_once_verify()


def protocol_absence_release():
    """Close only original retained handles; never remove the once-claim."""
    global _PROTOCOL_ABSENCE_ANCHORS, _PROTOCOL_ABSENCE_ONCE
    held, once = _PROTOCOL_ABSENCE_ANCHORS, _PROTOCOL_ABSENCE_ONCE
    _PROTOCOL_ABSENCE_ANCHORS = _PROTOCOL_ABSENCE_ONCE = None
    primary, errors, handles = sys.exc_info()[1], [], []
    if once is not None:
        handles.append((once[0], once[1]))
    if held is not None:
        handles.extend([(held[2], held[3]), (held[0], held[1])])
    for descriptor, identity in handles:
        try:
            journal_close_acquired(descriptor, identity)
        except BaseException as error:
            errors.append(error)
    if errors and primary is None:
        raise errors[0]


def protocol_absence_claim():
    """Exclusive mkdir claims one namespace; failures never delete/reset it."""
    global _PROTOCOL_ABSENCE_ANCHORS, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_PATH, _STATE_LOCK, _GUARD_NEW_DIRECTORY, _STATE_INITIALIZING
    if _PROTOCOL_ABSENCE_ANCHORS is not None or _STATE_DIR_FD is not None:
        raise AdapterError("Nested absence initialization refused", "access")
    pins = _PROTOCOL_ABSENCE_BASELINE["absence_pins"]
    if tempfile.gettempdir() != pins["ancestor_path"]:
        raise AdapterError("Unexpected LOCAL temporary root", "access")
    root = temporary = None
    root_id = temporary_id = None
    try:
        root = os.open(
            "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        info = os.fstat(root)
        root_id = (info.st_dev, info.st_ino)
        temporary = os.open(
            pins["temporary_name"],
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=root,
        )
        info = os.fstat(temporary)
        temporary_id = (info.st_dev, info.st_ino)
        _PROTOCOL_ABSENCE_ANCHORS = (
            root,
            root_id,
            temporary,
            temporary_id,
            None,
        )
        protocol_absence_verify(True)
        leaf = FAMILY + "-" + str(os.getuid())
        if leaf != pins["modern_component"]:
            raise AdapterError("Finite LOCAL namespace mismatch", "access")
        protocol_absence_once_create()
        # EEXIST is terminal here, including a previously completed attempt.
        os.mkdir(leaf, mode=0o700, dir_fd=temporary)
        descriptor = os.open(
            leaf,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=temporary,
        )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        _STATE_DIR_FD, _STATE_DIR_ID = descriptor, identity
        _STATE_PATH = os.path.join(pins["ancestor_path"], leaf, "state.json")
        _STATE_LOCK = os.path.join(pins["ancestor_path"], leaf, "state.lock")
        _PROTOCOL_ABSENCE_ANCHORS = (
            root,
            root_id,
            temporary,
            temporary_id,
            identity,
        )
        protocol_absence_verify()
        _GUARD_NEW_DIRECTORY = _STATE_INITIALIZING = True
        os.fsync(descriptor)
        os.fsync(temporary)
    except BaseException:
        journal_failed()
        if _PROTOCOL_ABSENCE_ANCHORS is not None:
            protocol_absence_release()
        else:
            for descriptor, identity in [
                (temporary, temporary_id),
                (root, root_id),
            ]:
                if descriptor is not None and identity is not None:
                    journal_close_acquired(descriptor, identity, True)
        raise


def protocol_absence_initialize(context, seed):
    """Commit pending initialization then its exact durable settlement."""
    global _GUARD_REMOTE_INITIALIZING, _GUARD_LOCK_ID, _GUARD_CREATED
    protocol_absence_claim()
    _GUARD_REMOTE_INITIALIZING = True
    lock = lock_id = state_fd = state_id = None
    try:
        protocol_acquire_collection()
        parent = journal_parent()
        protocol_absence_verify()
        lock = os.open(
            "state.lock",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        info = journal_owned(lock)
        lock_id = (info.st_dev, info.st_ino)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_owned(lock, lock_id)
        named = os.stat("state.lock", dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != lock_id:
            raise AdapterError("Initial state lock changed", "access")
        _GUARD_LOCK_ID = lock_id
        with JournalLock(lock, lock_id):
            lock = None
            protocol_absence_verify()
            state_fd = os.open(
                "state.json",
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | os.O_NONBLOCK,
                0o600,
                dir_fd=parent,
            )
            info = journal_owned(state_fd)
            state_id = (info.st_dev, info.st_ino)
            journal_retain(state_fd)
            journal_close(state_fd, state_id)
            state_fd = None
            _GUARD_CREATED = True
            # Mandatory under BOTH original exclusive locks before any commit.
            protocol_same_history(context)
            protocol_absence_verify()
            state = json.loads(json.dumps(seed))
            owner = os.urandom(16).hex()
            now = time.time()
            state.update(
                observed_at=max(state["observed_at"], now),
                not_before=max(
                    state["not_before"],
                    now + 60,
                    state["completed_at"] + state["interval"],
                    state["inventory_completed_at"] + state["page_interval"],
                ),
                in_flight=True,
                flight_id=owner,
                transition_id=protocol_digest(
                    _REVIEWED_LOCAL_ABSENCE_TRANSITION
                ),
            )
            save_budget(state)
            committed = load_budget()
            if committed != state or committed["flight_id"] != owner:
                raise AdapterError(
                    "Exact initialization owner missing", "access"
                )
            protocol_absence_verify()
            now = time.time()
            committed.update(
                observed_at=max(committed["observed_at"], now),
                not_before=max(committed["not_before"], now + 60),
                in_flight=False,
                flight_id=None,
                last_settled_owner={
                    "owner": owner,
                    "receipt_sha256": protocol_digest(None),
                    "completed_at": now,
                },
            )
            save_budget(committed)
            if load_budget() != committed:
                raise AdapterError("Initial settlement not durable", "access")
            os.fsync(parent)
        journal_mark_mature()
    except BaseException:
        journal_failed()
        raise
    finally:
        _GUARD_REMOTE_INITIALIZING = False
        # No namespace deletion: partial initialization always needs new review.
        for descriptor, identity in [(state_fd, state_id), (lock, lock_id)]:
            if descriptor is not None and identity is not None:
                journal_close(
                    descriptor, identity, sys.exc_info()[0] is not None
                )


def protocol_restore_absence():
    """Dedicated LOCAL entry: null approval refuses before metadata or IO."""
    global _PROTOCOL_CONTEXT, _PROTOCOL_READY, _RESTORED, _STATE_TRUSTED
    if (
        _REVIEWED_LOCAL_ABSENCE_TRANSITION is None
        or os.environ.get("GITHUB_ACTIONS") == "true"
        or _GUARD_FATAL
        or _PROTOCOL_READY
    ):
        raise AdapterError("Unreleased finite LOCAL absence entry", "access")
    try:
        context = protocol_history()
        context["source_sha256"] = protocol_source_hash(
            context["latest"]["head"]
        )
        latest, artifact = context["latest"], context["artifact"]
        incoming = download_inert_artifact(
            (latest["id"], latest["attempt"], SOURCE_ID),
            artifact["id"],
            artifact["digest"],
            context,
        )
        seed = protocol_absence_marker(context, incoming)
        protocol_absence_initialize(context, seed)
        _PROTOCOL_CONTEXT = context
        _RESTORED = _STATE_TRUSTED = _PROTOCOL_READY = True
    except BaseException:
        journal_failed()
        raise


if __name__ == "__main__":
    sys.exit(main())
