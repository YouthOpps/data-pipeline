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
COLLECTION_TIMEOUT = 7200
PUBLICATION_TIMEOUT = 900
RUN_TIMEOUT = 9120
EXPORT_TIMEOUT = 120
_STATE_SCHEMA = 3
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
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False
_RUN_END = _PHASE_END = None
_PHASE_NAME = None


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    with protocol_inventory():
        return fetch(*args, **kwargs)


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
        for name, witness in state[key].items():
            protocol_observation(witness)
            if witness["allowed"] is denied or name != (
                witness["target_sha256"] if denied else witness["origin"]
            ):
                raise AdapterError("Policy observation key mismatch", "access")
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
            r"/contents/adapters/volkswagen-group/adapter\.py"
            r"\?ref=[0-9a-f]{40}",
            path,
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
    """Export healthy state; invalidate owned stale output on failure."""
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
    state = validate_budget(state)
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
        ("issue117-vw-research", "vw-family.budget.json"),
        ("youthopps-source-triage/117", "pacing.json"),
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
                raise AdapterError(
                    "Unreviewed Volkswagen AG robots origin", "access"
                )
            read_input(key)
        protocol_assess_policy(reviewed)


def _fetch_public_body(url):
    """Retry only bounded transient server failures under original pacing."""
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(reviewed, publisher=True)
        if status == 200 or (
            urllib.parse.urlsplit(reviewed).path == "/robots.txt"
            and 400 <= status < 500
            and status != 429
        ):
            return status, headers, body
        if (
            status in (502, 503, 504)
            and attempt == 0
            and urllib.parse.urlsplit(reviewed).path != "/robots.txt"
        ):
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

def fetch_public(url):
    with protocol_transaction("fetch"):
        return _fetch_public_body(url)


def read_input(key):
    import urllib.robotparser

    info = INPUTS[key]
    if info["format"] != "robots":
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
                "Reviewed Volkswagen AG material or frontier changed: " + key,
                "parse",
            )
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
            .astimezone(timezone.utc)
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
    today = now.astimezone(timezone.utc).date()
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
        "display_name": "Volkswagen",
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
        rows, following, span = parse_listing(
            fetch_inventory_page(read_document, url), url, language)
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
    if _GUARD_FATAL:
        with contextlib.suppress(BaseException):
            export_family_artifact()
    if errors and primary is None:
        raise errors[0]


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
            if _PUBLISHER_BYTES >= MAX_PUBLISHER_BYTES:
                raise AdapterError("Whole publisher byte cap reached", "fetch")
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
                # Save the adaptive MAX before any body or further request.
                # A close failure is unassessed regardless of exception type.
                try:
                    record_retry_after(
                        response.headers.get("Retry-After"), rate_limited=True
                    )
                except BaseException:
                    try:
                        response.close()
                    except BaseException:
                        journal_failed()
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

_REVIEWED_LOCAL_TRANSITION = {
    "schema": 1,
    "source": "volkswagen-group",
    "purpose": "LOCAL",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/316#issuecomment-6210612968",
    "history_sha256": "46846fa352cf704b08fd060b516a62b96444dc4aa81b79f407782aaf10877020",
    "owner": {
        "run_id": 38850140544,
        "attempt": 1,
        "head": "12f61ef6e7caba5099d1c6ef367bbe5c9abd1cff",
        "job_id": 116809322808,
        "artifact_id": 12026041596,
        "artifact_digest": "sha256:f634c73ecbbbe50154cc353429adacde8748b55e8fd9ba9bf05aefb58d2b0fa3",
        "source_sha256": "73e6f0084dc5a92415dc9a905552c09e9aa7527e21537c3966a6d9b99de7756c",
    },
    "seed": {
        "schema": 3,
        "family": "youthopps-volkswagen-group-publisher-v1",
        "observed_at": 1792134221.0645838,
        "not_before": 1792134281.0645838,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "completed_at": 1792125881.0,
        "inventory_completed_at": 1792125881.0,
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
    "seed_sha256": "adefe5478df2084975251190f450c6501d88e659acaa5cd57d12750b07b9b49c",
    "local_pins": {
        "parent": [34, 2044],
        "state": [34, 11265],
        "collection_lock": [34, 3194],
        "state_lock": [34, 3195],
        "raw_sha256": "63710a5802ab4eb634cfe8f4c3a4f852446614f4479185af235e1157b57194f5",
    },
}
_REVIEWED_HOSTED_TRANSITION = {'schema': 1,
 'source': 'volkswagen-group',
 'purpose': 'HOSTED',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/316#issuecomment-6212633235',
 'history_sha256': '46846fa352cf704b08fd060b516a62b96444dc4aa81b79f407782aaf10877020',
 'owner': {'run_id': 38850140544,
           'attempt': 1,
           'head': '12f61ef6e7caba5099d1c6ef367bbe5c9abd1cff',
           'job_id': 116809322808,
           'artifact_id': 12026041596,
           'artifact_digest': 'sha256:f634c73ecbbbe50154cc353429adacde8748b55e8fd9ba9bf05aefb58d2b0fa3',
           'source_sha256': '73e6f0084dc5a92415dc9a905552c09e9aa7527e21537c3966a6d9b99de7756c'},
 'seed': {'schema': 3,
          'family': 'youthopps-volkswagen-group-publisher-v1',
          'observed_at': 1792140630.6432664,
          'not_before': 1792140690.6432664,
          'starts': [],
          'blocked': False,
          'interval': 12.2,
          'page_interval': 30,
          'completed_at': 1792140554.5588405,
          'inventory_completed_at': 1792138048.7293131,
          'rate_limit_count': 0,
          'rate_limit_until': 0,
          'legacy_rate_history_unknown': True,
          'legacy_policy_history_unknown': True,
          'legacy_completion_history_unknown': True,
          'in_flight': False,
          'flight_id': None,
          'policy_pending': None,
          'last_policy_receipt': None,
          'policy_allowed': {'https://jobs.volkswagen-group.com': {'owner': '0254aef1344e678b8ff42bb05171ed9b',
                                                                   'origin': 'https://jobs.volkswagen-group.com',
                                                                   'target_sha256': '2c49bea3221f3e7fb55308d284ea6d40d436b564d9bcef405462139d82c68999',
                                                                   'body_sha256': '91669efc2767d124199e1d848655af18d64ac71e61ae1227be7b6aa866f03caf',
                                                                   'available': True,
                                                                   'allowed': True,
                                                                   'delay': 0,
                                                                   'rate': None},
                             'https://www.hrd-portal.com': {'owner': '665b4c0fd5ddbeea66917dfdbefbf207',
                                                            'origin': 'https://www.hrd-portal.com',
                                                            'target_sha256': 'a76aa434a8d8d79dcaffd45d33914dc657514b964022c0c84aea077566c44431',
                                                            'body_sha256': '58c109cc44da82a7ff610aaaa11a3801cb99eaa3cd98f3e05183607801af6608',
                                                            'available': True,
                                                            'allowed': True,
                                                            'delay': 0,
                                                            'rate': None},
                             'https://www.volkswagen-karriere.de': {'owner': '4621fdba595ed092c9054239ad8863c5',
                                                                    'origin': 'https://www.volkswagen-karriere.de',
                                                                    'target_sha256': '7cfc6a64968a2e8f5070596ac86e392f41e361316efd716185b0087497bc7bd2',
                                                                    'body_sha256': 'dd84e3470dafcf77ca25f1d3d448c84dfd4255988f006a0318437961d06368fb',
                                                                    'available': True,
                                                                    'allowed': True,
                                                                    'delay': 0,
                                                                    'rate': None}},
          'policy_denied': {},
          'last_settled_owner': None,
          'transition_id': '1d07fbffdcc7b91edd7b9f5b7524763eb056b01edad2aa74c2a317d83a00a869'},
 'seed_sha256': '88fda0e9c05c6b605276cf7c17bc2fd2fa295c73bbcd464eb07afe2e9b721755',
 'local_pins': None}
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
_PROTOCOL_LAST_COMPLETED = None
_PROTOCOL_LEGACY_INITIALIZING = False
_PROTOCOL_PHYSICAL_INVENTORY = False
_PROTOCOL_WORKFLOW_ID = 380821053
_PROTOCOL_REPOSITORY_ID = 1163601208

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
        for target, witness in right[key].items():
            current = result[key].get(target)
            if current is None or json.dumps(
                witness, sort_keys=True
            ) < json.dumps(current, sort_keys=True):
                result[key][target] = witness
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
        or obj.get("path") != ".github/workflows/fetch-volkswagen-group.yml"
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
        != ".github/workflows/fetch-volkswagen-group.yml"
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
                "fetch-volkswagen-group.yml@refs/heads/main"
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/316"
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
            old, sort_keys=True
        ):
            state[key][name] = receipt
        state.update(
            last_policy_receipt=receipt,
            policy_pending=None,
            observed_at=time.time(),
        )
        save_budget(state)
        _PROTOCOL_EXPECTED_RECEIPT = json.loads(json.dumps(receipt))
    if not allowed:
        raise AssessedStop(
            "Publisher robots excludes required Volkswagen AG input", "access"
        )
    return receipt

def protocol_source_hash(head):
    if not re.fullmatch(r"[0-9a-f]{40}", str(head)):
        raise AdapterError("Invalid immutable producer source ref", "access")
    result = workflow_api(
        "/contents/adapters/volkswagen-group/adapter.py?ref=" + head
    )
    if (
        not isinstance(result, dict)
        or result.get("type") != "file"
        or result.get("path") != "adapters/volkswagen-group/adapter.py"
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
        protocol_same_history(_PROTOCOL_CONTEXT)
        state = load_budget()
        protocol_current_owner(state)
        if state["blocked"] or time.time() < max(
            _COLLECTION_STARTED + 60, state["rate_limit_until"]
        ):
            raise AdapterError(
                "Publisher quiet/hold changed before first wire", "access"
            )
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


if __name__ == "__main__":
    raise SystemExit(main())
