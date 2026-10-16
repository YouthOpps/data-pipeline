"""Discover IBM early-career programmes and extract current official facts."""

import argparse
import base64
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import functools
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
_LOCAL_WORKFLOW_ID = 380775192
_LOCAL_START_VERIFIED = False
COLLECTION_TIMEOUT = 900
RUN_TIMEOUT = 1290
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 4
_ARTIFACT_NAME = "ibm-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_STATE_DIR_FD = _STATE_DIR_ID = None
_STATE_INITIALIZING = False
_COLLECTION_LOCK_FD = None
_COLLECTION_LOCK_ID = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_ROBOT_BODIES = {}
_PUBLISHER_STARTS = _PUBLISHER_BYTES = 0
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False
PUBLISHER_START_LIMIT = 100
PUBLISHER_BYTE_LIMIT = 64 * 1024 * 1024
RESPONSE_BYTE_LIMIT = 8 * 1024 * 1024
_RUN_END = _PHASE_END = None
_PHASE_NAME = None


_ATTEMPT_HEADS = {}
_PAGE_COMPLETED = None


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




@contextlib.contextmanager
def protocol_budget():
    with locked_budget():
        state = load_budget()
        yield state
        save_budget(state)


def protocol_publisher(url, kwargs):
    return kwargs.get("publisher", False) or urllib.parse.urlsplit(
        url
    ).hostname in ("ibm.com", "www.ibm.com")


def protocol_rate_count(state):
    return state.get("rate_failures", 0)


# Protocol state is source-local. Old schemas require an exact reviewed handoff.
_PROTOCOL_ARTIFACT_ENV = "IBM_PACING_ARTIFACT_PATH"
_PROTOCOL_ROBOTS_HASHES = {}
_PROTOCOL_POLICY_FRAMES = []
_PROTOCOL_POLICY_REVIEW = None
_PROTOCOL_EXPECTED_POLICY_RECEIPT = None
_PROTOCOL_POLICY_ORIGINS = ('https://www.ibm.com',)
_PROTOCOL_PARENT_IDS = {}
_PROTOCOL_INODE_IDS = {}
_PROTOCOL_DESCRIPTOR_PATHS = {}
_PROTOCOL_ATOMIC = False
_PROTOCOL_RESPONSES = []
_PROTOCOL_OWNER = None
_PROTOCOL_DEPTH = 0
_PROTOCOL_FATAL = False
_PROTOCOL_RAM = None
_JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
_JOURNAL_DIRTY = False
_JOURNAL_LOCKS = {}
_JOURNAL_BOOTSTRAP = None
_PROTOCOL_RETAINED_IDS = {}
_REVIEWED_PROTOCOL_TRANSITION = {
    "purpose": "local",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issue"
        "s/273#issuecomment-6205602169"
    ),
    "pins_sha256": (
        "b6fc403500af2a849de5c81922dc22fd5df57e86720414ed"
        "1a4ffc68e76d0fd4"
    ),
    "seed_sha256": (
        "9a28936b043f19dfe8b5b847092e731d02c804c427811793"
        "1ead5d1d9eed0726"
    ),
    "legacy_boundary_acknowledged": True,
    "collection_lock": None,
}
_LEGACY_PROTOCOL_PINS = json.loads(
    '{"aged_starts_omitted":[],"artifact_digest":"sha256:8c0d3c40'
    '1650f5d284dc43efd849b990c6e6203c1f203b398494939b4697bef7","a'
    'rtifact_id":11999834293,"attempt":1,"head":"2f02f583ba92d952'
    '46cdd34f42dd8f05afd2996d","jobs_sha256":"1bd05be3e172b2b25ec'
    '375b42b86d86ed9bd07ac3f2b7bad0d627896a4e5a8a3","local_namesp'
    'ace":{"directory_device":34,"directory_inode":2964,"director'
    'y_mode":"0o700","entries":[{"device":34,"inode":3053,"name":'
    '"collection.lock"},{"device":34,"inode":146073,"name":"state'
    '.json"},{"device":34,"inode":2965,"name":"state.lock"}],"sou'
    'rce":"ibm"},"local_raw_sha256":"ede4d6ba303a5d5ce0deaf0bef49'
    '33a7244aca829acd57ac9ef1ae41feca12d6","local_state":{"blocke'
    'd":false,"family":"youthopps-ibm-careers-publisher-v1","inte'
    'rval":12.2,"legacy_rate_history_unknown":true,"not_before":1'
    '792089631.3273845,"observed_at":1792089619.1273844,"page_int'
    'erval":30,"rate_failures":0,"schema":1,"starts":[1792089565.'
    '6838763,1792089578.9899173,1792089592.3797474,1792089605.128'
    '8328,1792089618.0136244]},"owned_state":{"blocked":false,"fa'
    'mily":"youthopps-ibm-careers-publisher-v1","interval":12.2,"'
    'legacy_rate_history_unknown":true,"not_before":1792091722.49'
    '48153,"observed_at":1792091710.2948153,"page_interval":30,"r'
    'ate_failures":0,"schema":1,"starts":[1792091658.9586363,1792'
    '091671.6431189,1792091684.3044937,1792091696.7436388,1792091'
    '709.2719138]},"run_id":38791804543,"seed":{"blocked":false,"'
    'current_policy_assessment":{"allowed":true,"delay":0,"id":"5'
    '81c962b95623cc7b66bffac935c13a8e47b58d98c1ff8eb06806a637575a'
    '0eb","origin":"https://www.ibm.com","path":"/careers/blog/5-'
    'things-that-make-the-ibm-internship-programs-unique","policy'
    '":"8e29d2cf6f21b8d8eb24567c36e257fe1823582a6a9f3b2130410d8cc'
    '67f0f70","rate":null,"target":"0ebf490f601ba04a957cf658ca6f6'
    '786822ae91edcbdd300d050901ef5c7f202","transaction":"e946813e'
    '51f1dfce23b45792b16d31fa2311395b01b3e89ef727d713f3589356"},"'
    'family":"youthopps-ibm-careers-publisher-v1","historical_all'
    'owed_witnesses":[{"allowed":true,"delay":0,"origin":"https:/'
    '/www.ibm.com","path":"/careers","policy":"8e29d2cf6f21b8d8eb'
    '24567c36e257fe1823582a6a9f3b2130410d8cc67f0f70","rate":null,'
    '"target":"f31f582d1d72c0316581655c68f07031a819f58379361c7e78'
    '3b8eae944f2495"}],"in_flight":false,"interval":12.2,"legacy_'
    'policy_history_unknown":true,"legacy_rate_history_unknown":t'
    'rue,"not_before":1792111840.0617418,"observed_at":1792111779'
    '.3896918,"observed_policy_conflict":false,"page_interval":30'
    ',"pending":null,"policy_denials":[],"policy_pending":null,"r'
    'ate_failures":0,"schema":4,"starts":[1792111726.3057876,1792'
    '111726.391801,1792111739.4613533,1792111739.516558,179211175'
    '3.1244943,1792111753.193284,1792111766.099035,1792111766.154'
    '237,1792111778.850741,1792111778.8884788]},"source":"ibm","s'
    'ource_sha256":"ccea51f98357d30848aa02d857eae7fc315bd1c2a156b'
    '9c7fba78bd82992524c","workflow_file":"fetch-ibm.yml","workfl'
    'ow_id":380775192,"workflow_path":".github/workflows/fetch-ib'
    'm.yml"}'
)
_REVIEWED_HOSTED_PROTOCOL_TRANSITION = {
    "purpose": "hosted",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issue"
        "s/273#issuecomment-6205602169"
    ),
    "pins_sha256": (
        "b6fc403500af2a849de5c81922dc22fd5df57e86720414ed"
        "1a4ffc68e76d0fd4"
    ),
    "seed_sha256": (
        "9a28936b043f19dfe8b5b847092e731d02c804c427811793"
        "1ead5d1d9eed0726"
    ),
    "legacy_boundary_acknowledged": True,
    "collection_lock": None,
}
_PROTOCOL_LEGACY_SELECTED = None
_PROTOCOL_BOUNDARY_WALL = 0
_PROTOCOL_COMPAT_READY = False
_PROTOCOL_FIRST_WIRE_VERIFIED = False
_PROTOCOL_COMPAT_FDS = []
_PROTOCOL_COMPAT_DIRECTORY = None
_PROTOCOL_COMPAT_CONTEXT = None
_PROTOCOL_QUIET_UNTIL = 0
_PROTOCOL_LIFECYCLE_DEPTH = 0




def policy_bytes(value):
    """Canonical whole witnesses are samples, never access authority."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def policy_fields():
    return {"target", "origin", "path", "policy", "allowed", "delay", "rate"}


def policy_conflict(witnesses):
    """A retained contradiction requires review, never an allowed override."""
    seen = {}
    for value in witnesses:
        key = (value["target"], value["policy"])
        raw = policy_bytes(value)
        if key in seen and seen[key] != raw:
            return True
        seen[key] = raw
    return False


def validate_observation(value):
    if (
        not isinstance(value, dict)
        or set(value) != policy_fields()
        or type(value.get("allowed")) is not bool
        or not isinstance(value.get("policy"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["policy"])
        or not numeric(value.get("delay"))
        or value.get("rate") is not None
        and not numeric(value["rate"])
    ):
        raise AdapterError("Invalid scoped REP observation", "access")
    validate_target(value)
    return value


def protocol_size(state, owner=None):
    """Check actual compact JSON bounds without granting an access decision."""
    if len(policy_bytes(state)) > 16384:
        raise AdapterError("Publisher protocol exceeds state bound", "access")
    if owner is not None or os.environ.get("GITHUB_ACTIONS") == "true":
        run_id, attempt = owner or current_run_identity()
        if (
            type(run_id) is not int
            or type(attempt) is not int
            or min(run_id, attempt) <= 0
        ):
            raise AdapterError("Invalid protocol envelope owner", "access")
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "source": SOURCE_ID,
            "run_id": run_id,
            "run_attempt": attempt,
            "state": state,
        }
        if len(policy_bytes(envelope)) > 16384:
            raise AdapterError("Publisher envelope exceeds bound", "access")


def begin_policy_assessment(url):
    """Persist a new exact target owner BEFORE fresh or cached REP learning."""
    if _PROTOCOL_POLICY_FRAMES:
        raise AdapterError("Overlapping REP assessment", "access")
    target = protocol_target(url)
    frame = {
        "id": os.urandom(32).hex(),
        "transaction": _PROTOCOL_OWNER,
        **target,
    }
    with protocol_budget() as state:
        protocol_healthy(state)
        if state["policy_pending"] is not None or not state["in_flight"]:
            raise AdapterError("Unsettled REP assessment", "access")
        # Fold only a completed receipt; no pending owner can be compacted.
        prior = dict(state)
        prior.update(in_flight=False, pending=None)
        state.update(merge_policy_observations(prior, prior))
        state["policy_pending"] = frame.copy()
    _PROTOCOL_POLICY_FRAMES.append({**frame, "complete": False})


def merge_policy_observations(local, incoming):
    """Join restrictions; an arbitrary old allowed witness grants nothing."""
    for state in (local, incoming):
        validate_protocol(state)
        if state["in_flight"] or state["policy_pending"] is not None:
            raise AdapterError("Pending policy cannot be merged", "access")
    candidates = []
    for state in (local, incoming):
        candidates.extend(state["historical_allowed_witnesses"])
        candidates.extend(state["policy_denials"])
        receipt = state["current_policy_assessment"]
        if receipt is not None:
            candidates.append({key: receipt[key] for key in policy_fields()})
    allowed, denials, seen = {}, {}, {}
    conflict = any(
        state["observed_policy_conflict"] for state in (local, incoming)
    )
    for witness in candidates:
        validate_observation(witness)
        raw = policy_bytes(witness)
        key = (witness["target"], witness["policy"])
        if key in seen and seen[key] != raw:
            conflict = True
        seen[key] = raw
        if witness["allowed"]:
            origin = witness["origin"]
            if origin not in allowed or raw < policy_bytes(allowed[origin]):
                allowed[origin] = witness
        else:
            denials[raw] = witness
    if len(denials) > 32:
        raise AdapterError("Scoped denials exceed retained bound", "access")
    return {
        "historical_allowed_witnesses": [allowed[k] for k in sorted(allowed)],
        "policy_denials": [denials[k] for k in sorted(denials)],
        "policy_pending": None,
        "current_policy_assessment": None,
        "legacy_policy_history_unknown": any(
            state["legacy_policy_history_unknown"]
            for state in (local, incoming)
        ),
        "observed_policy_conflict": conflict,
    }


def validate_protocol(state):
    """Validate typed ownership independently of historical allowed samples."""
    if (
        type(state.get("legacy_rate_history_unknown")) is not bool
        or type(state.get("legacy_policy_history_unknown")) is not bool
        or type(state.get("observed_policy_conflict")) is not bool
        or type(state.get("in_flight")) is not bool
        or not {"pending", "policy_pending", "current_policy_assessment"}
        <= set(state)
    ):
        raise AdapterError("Invalid durable publisher protocol", "access")
    pending = state["pending"]
    if state["in_flight"]:
        if (
            not isinstance(pending, dict)
            or set(pending) != {"id", "kind", "target", "origin", "path"}
            or not isinstance(pending.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", pending["id"])
            or not isinstance(pending.get("kind"), str)
            or pending.get("kind") not in {"physical", "policy"}
        ):
            raise AdapterError(
                "Invalid pending publisher transaction", "access"
            )
        validate_target(pending)
    elif pending is not None:
        raise AdapterError("Unexpected settled transaction", "access")
    allowed = state.get("historical_allowed_witnesses")
    denials = state.get("policy_denials")
    if (
        not isinstance(allowed, list)
        or not isinstance(denials, list)
        or len(allowed) > len(_PROTOCOL_POLICY_ORIGINS)
        or len(denials) > 32
    ):
        raise AdapterError("Invalid bounded REP history", "access")
    for witness in allowed + denials:
        validate_observation(witness)
    if (
        any(not p["allowed"] for p in allowed)
        or any(p["allowed"] for p in denials)
        or [p["origin"] for p in allowed]
        != sorted(set(p["origin"] for p in allowed))
        or [policy_bytes(p) for p in denials]
        != sorted(set(policy_bytes(p) for p in denials))
    ):
        raise AdapterError("Noncanonical REP history", "access")
    assessment = state["policy_pending"]
    if assessment is not None:
        if (
            not isinstance(assessment, dict)
            or set(assessment)
            != {"id", "transaction", "target", "origin", "path"}
            or not state["in_flight"]
            or not isinstance(assessment.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", assessment["id"])
            or assessment["transaction"] != pending["id"]
        ):
            raise AdapterError("Invalid pending REP ownership", "access")
        validate_target(assessment)
    receipt = state["current_policy_assessment"]
    if receipt is not None:
        if (
            not isinstance(receipt, dict)
            or set(receipt) != policy_fields() | {"id", "transaction"}
            or any(
                not isinstance(receipt.get(k), str)
                or not re.fullmatch(r"[0-9a-f]{64}", receipt[k])
                for k in ("id", "transaction")
            )
        ):
            raise AdapterError("Invalid current REP receipt", "access")
        witness = {key: receipt[key] for key in policy_fields()}
        validate_observation(witness)
        if assessment is not None and any(
            receipt[key] != assessment[key] for key in assessment
        ):
            raise AdapterError("REP receipt owner mismatch", "access")
        if not receipt["allowed"] and witness not in denials:
            raise AdapterError("REP denial is missing from history", "access")
    known = allowed + denials
    if receipt is not None:
        known = known + [{key: receipt[key] for key in policy_fields()}]
    if any(state["interval"] < max(p["delay"], p["rate"] or 0) for p in known):
        raise AdapterError(
            "REP restriction exceeds retained interval", "access"
        )
    if policy_conflict(known) and not state["observed_policy_conflict"]:
        raise AdapterError("Unmarked conflicting REP evidence", "access")
    protocol_size(state)
    return state


def protocol_target(url):
    """Retain a bounded canonical target hash and a query-free safe path."""
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise AdapterError("Unsafe protocol target", "access")
    origin = "https://" + parsed.hostname.lower()
    path = parsed.path or "/"
    canonical = origin + path + ("?" + parsed.query if parsed.query else "")
    value = {
        "target": hashlib.sha256(canonical.encode()).hexdigest(),
        "origin": origin,
        "path": path,
    }
    validate_target(value)
    return value


def validate_target(value):
    origin, path = value.get("origin"), value.get("path")
    if (
        not isinstance(origin, str)
        or origin not in _PROTOCOL_POLICY_ORIGINS
        or len(origin) > 255
        or not re.fullmatch(r"https://[a-z0-9.-]+", origin)
        or not isinstance(path, str)
        or len(path) > 1024
        or not path.startswith("/")
        or "?" in path
        or "#" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or not isinstance(value.get("target"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["target"])
    ):
        raise AdapterError("Invalid scoped publisher target", "access")


def invalidate_protocol_export():
    """Remove apparent readiness BEFORE new policy or publisher access."""
    path = os.environ.get(_PROTOCOL_ARTIFACT_ENV)
    if not path:
        return
    root = os.environ.get("RUNNER_TEMP", "")
    if (
        not root
        or not os.path.isabs(root)
        or os.path.dirname(os.path.abspath(path)) != os.path.realpath(root)
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe prior pacing export path", "access")
    parent = os.open(
        os.path.realpath(root),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    parent_identity = identity = None
    try:
        directory = os.fstat(parent)
        parent_identity = (directory.st_dev, directory.st_ino)
        if (
            directory.st_uid != os.getuid()
            or directory.st_mode & 0o022
            or directory.st_mode & 0o700 != 0o700
        ):
            raise AdapterError("Unsafe prior pacing export directory", "access")
        try:
            descriptor = os.open(
                "pacing-state.json",
                os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            return
        try:
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            current = os.stat(
                "pacing-state.json",
                dir_fd=parent,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino)
                != (current.st_dev, current.st_ino)
            ):
                raise AdapterError("Unsafe prior pacing export inode", "access")
            artifact_descriptor(descriptor, identity)
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            artifact_descriptor(descriptor, identity)
        finally:
            close_owned_descriptor(descriptor, identity)
    except BaseException:
        journal_failure()
        raise
    finally:
        close_owned_descriptor(parent, parent_identity, True)


def pin_protocol_parent(path, descriptor, expected=None):
    """Bind cleanup to the actual verified namespace, not a replacement path."""
    parent = os.open(
        os.path.dirname(path),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        directory = os.fstat(parent)
        current = os.stat(
            os.path.basename(path),
            dir_fd=parent,
            follow_symlinks=False,
        )
        file_info = os.fstat(descriptor)
        identity = (directory.st_dev, directory.st_ino)
        recorded_parent = _PROTOCOL_PARENT_IDS.get(path)
        recorded_file = _PROTOCOL_INODE_IDS.get(path)
        file_identity = (file_info.st_dev, file_info.st_ino)
        if (
            directory.st_uid != os.getuid()
            or stat.S_IMODE(directory.st_mode) != 0o700
            or not stat.S_ISREG(file_info.st_mode)
            or file_info.st_uid != os.getuid()
            or stat.S_IMODE(file_info.st_mode) != 0o600
            or file_info.st_nlink != 1
            or recorded_parent is not None
            and identity != recorded_parent
            or not _PROTOCOL_ATOMIC
            and recorded_file is not None
            and file_identity != recorded_file
            or expected is not None
            and identity != expected
            or (file_info.st_dev, file_info.st_ino)
            != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Publisher state namespace changed", "access")
        _PROTOCOL_PARENT_IDS[path] = identity
        if not _PROTOCOL_ATOMIC:
            _PROTOCOL_INODE_IDS[path] = file_identity
        _PROTOCOL_DESCRIPTOR_PATHS[descriptor] = path
    finally:
        os.close(parent)


def protocol_failure(candidate, paths):
    """Retain the failed candidate and invalidate only the original held FD."""
    global _PROTOCOL_FATAL, _PROTOCOL_RAM, _PUBLISHER_GATE_FAILED
    global _STATE_TRUSTED
    _PROTOCOL_FATAL = _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False
    try:
        _PROTOCOL_RAM = json.loads(json.dumps(candidate))
    except BaseException:
        # Retain the actual candidate even if its serialization was the fault.
        _PROTOCOL_RAM = candidate
    try:
        invalidate_journal()
    except BaseException:
        pass


def protocol_healthy(state):
    if (
        _PROTOCOL_FATAL
        or state["observed_policy_conflict"]
        or (
            state["in_flight"]
            and (state["pending"] or {}).get("id") != _PROTOCOL_OWNER
        )
    ):
        raise AdapterError(
            "Unsettled publisher transaction requires review", "access"
        )



def protocol_transport_started():
    """Mark an actual opener invocation, after all reservation/state checks."""
    if not _PROTOCOL_RESPONSES:
        raise AdapterError("Publisher transport has no transaction", "access")
    protocol_before_wire()
    _PROTOCOL_RESPONSES[-1].update(started=True, received=False)


def protocol_response_received():
    """An HTTPError is a response too; mark before entering its context."""
    if not _PROTOCOL_RESPONSES:
        raise AdapterError("Publisher response has no transaction", "access")
    _PROTOCOL_RESPONSES[-1]["received"] = True


def durable_transaction(kind):
    """Keep response policy and cached REP decisions inside durable pending."""

    def decorate(function):
        @functools.wraps(function)
        def invoke(url, *args, **kwargs):
            global _PROTOCOL_OWNER, _PROTOCOL_DEPTH
            global _PROTOCOL_EXPECTED_POLICY_RECEIPT
            global _PROTOCOL_FATAL, _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
            if not protocol_publisher(url, kwargs):
                return function(url, *args, **kwargs)
            if _PROTOCOL_FATAL:
                raise AdapterError(
                    "Publisher persistence failure is sticky", "access"
                )
            if kind == "physical":
                protocol_startup_quiet()
            outer = _PROTOCOL_DEPTH == 0
            if (
                outer
                and kind == "physical"
                and urllib.parse.urlsplit(url).path == "/robots.txt"
            ):
                raise AdapterError(
                    "REP retrieval requires its target policy transaction",
                    "access",
                )
            if outer:
                _PROTOCOL_EXPECTED_POLICY_RECEIPT = None
                invalidate_protocol_export()
                with protocol_budget() as state:
                    protocol_healthy(state)
                    owner = os.urandom(32).hex()
                    state["in_flight"] = True
                    state["pending"] = {
                        "id": owner,
                        "kind": "policy" if kind == "assessment" else kind,
                        **protocol_target(url),
                    }
                _PROTOCOL_OWNER = owner
            _PROTOCOL_DEPTH += 1
            settled = False
            retryable_no_response = False
            physical = (
                {"started": False, "received": False}
                if kind == "physical"
                else None
            )
            if physical is not None:
                _PROTOCOL_RESPONSES.append(physical)
            policy_frame = None
            try:
                if (
                    kind == "assessment"
                    and urllib.parse.urlsplit(url).path != "/robots.txt"
                ):
                    begin_policy_assessment(url)
                    policy_frame = _PROTOCOL_POLICY_FRAMES[-1]
                result = function(url, *args, **kwargs)
                settled = True
                return result
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                retryable_no_response = (
                    not outer
                    and physical is not None
                    and physical["started"]
                    and not physical["received"]
                    and not _PROTOCOL_FATAL
                    and not isinstance(error, urllib.error.HTTPError)
                    and not isinstance(
                        getattr(error, "reason", error), ssl.SSLError
                    )
                )
                raise
            except AdapterError as error:
                if not _PROTOCOL_FATAL:
                    with protocol_budget() as state:
                        settled = (
                            getattr(error, "policy_retained", False)
                            or state.get("blocked", False)
                            or error.status == 429
                            and protocol_rate_count(state) > 0
                        )
                raise
            finally:
                if policy_frame is not None:
                    if not policy_frame["complete"]:
                        settled = False
                    if (
                        not _PROTOCOL_POLICY_FRAMES
                        or _PROTOCOL_POLICY_FRAMES.pop() is not policy_frame
                    ):
                        settled = False
                _PROTOCOL_DEPTH -= 1
                if physical is not None:
                    if (
                        not _PROTOCOL_RESPONSES
                        or _PROTOCOL_RESPONSES.pop() is not physical
                    ):
                        _PROTOCOL_FATAL = _PUBLISHER_GATE_FAILED = True
                        _STATE_TRUSTED = False
                if (
                    not settled
                    and not retryable_no_response
                    and (outer or physical is not None)
                ):
                    _PROTOCOL_FATAL = _PUBLISHER_GATE_FAILED = True
                    _STATE_TRUSTED = False
                if settled and _PROTOCOL_FATAL:
                    raise AdapterError(
                        "Unhealthy publisher transaction cannot return data",
                        "access",
                    )
                if outer:
                    try:
                        if settled and not _PROTOCOL_FATAL:
                            with protocol_budget() as state:
                                protocol_healthy(state)
                                if state["policy_pending"] is not None:
                                    raise AdapterError(
                                        "REP assessment remains pending",
                                        "access",
                                    )
                                if policy_frame is not None:
                                    receipt = state["current_policy_assessment"]
                                    if receipt is None or any(
                                        receipt[k] != policy_frame[k]
                                        for k in (
                                            "id",
                                            "transaction",
                                            "target",
                                            "origin",
                                            "path",
                                        )
                                    ):
                                        raise AdapterError(
                                            "REP settlement receipt mismatch",
                                            "access",
                                        )
                                if (
                                    _PROTOCOL_EXPECTED_POLICY_RECEIPT
                                    is not None
                                    and state["current_policy_assessment"]
                                    != _PROTOCOL_EXPECTED_POLICY_RECEIPT
                                ):
                                    raise AdapterError(
                                        "Nested REP receipt mismatch", "access"
                                    )
                                state["in_flight"] = False
                                state["pending"] = None
                        else:
                            _PROTOCOL_FATAL = _PUBLISHER_GATE_FAILED = True
                            _STATE_TRUSTED = False
                    except BaseException:
                        _PROTOCOL_FATAL = _PUBLISHER_GATE_FAILED = True
                        _STATE_TRUSTED = False
                        raise
                    finally:
                        _PROTOCOL_OWNER = None
                        _PROTOCOL_EXPECTED_POLICY_RECEIPT = None

        return invoke

    return decorate


def unavailable_policy(url):
    """Associate only an actual unavailable REP result with empty policy."""
    _PROTOCOL_ROBOTS_HASHES[protocol_target(url)["origin"]] = hashlib.sha256(
        b""
    ).hexdigest()


def retain_policy(url, text, allowed, delay=0, rate=None):
    """Commit current exact receipt and all restrictions before settlement."""
    global _PROTOCOL_POLICY_REVIEW, _PROTOCOL_EXPECTED_POLICY_RECEIPT
    target = protocol_target(url)
    if not _PROTOCOL_POLICY_FRAMES:
        raise AdapterError("REP decision has no assessment owner", "access")
    frame = _PROTOCOL_POLICY_FRAMES[-1]
    if any(frame[key] != target[key] for key in target):
        raise AdapterError("REP decision target mismatch", "access")
    policy_hash = _PROTOCOL_ROBOTS_HASHES.get(target["origin"])
    if policy_hash is None:
        if text:
            raise AdapterError(
                "REP cache has no physical body digest", "access"
            )
        policy_hash = hashlib.sha256(b"").hexdigest()
    witness = {
        **target,
        "policy": policy_hash,
        "allowed": allowed,
        "delay": delay,
        "rate": rate,
    }
    validate_observation(witness)
    receipt = {
        **witness,
        "id": frame["id"],
        "transaction": frame["transaction"],
    }
    with protocol_budget() as state:
        protocol_healthy(state)
        pending = {
            key: frame[key]
            for key in ("id", "transaction", "target", "origin", "path")
        }
        if state["policy_pending"] != pending:
            raise AdapterError("REP assessment ownership changed", "access")
        history = dict(state)
        history.update(
            in_flight=False,
            pending=None,
            policy_pending=None,
            current_policy_assessment=receipt,
        )
        if not allowed:
            history["policy_denials"] = sorted(
                {
                    policy_bytes(p): p
                    for p in state["policy_denials"] + [witness]
                }.values(),
                key=policy_bytes,
            )
        # Oversize or contradictory decisions remain full RAM review evidence.
        history["interval"] = max(state["interval"], delay, rate or 0)
        _PROTOCOL_POLICY_REVIEW = json.loads(
            json.dumps(
                dict(
                    history,
                    in_flight=True,
                    pending=state["pending"],
                    policy_pending=pending,
                    interval=max(state["interval"], delay, rate or 0),
                )
            )
        )
        history["observed_policy_conflict"] = history[
            "observed_policy_conflict"
        ] or policy_conflict(
            history["historical_allowed_witnesses"]
            + history["policy_denials"]
            + [witness]
        )
        merged = merge_policy_observations(history, history)
        state.update(merged)
        state["policy_pending"] = pending
        state["current_policy_assessment"] = receipt
        state["interval"] = max(state["interval"], delay, rate or 0)
        # First durable commit retains pending AND exact matching receipt.
    with protocol_budget() as state:
        protocol_healthy(state)
        if (
            state["current_policy_assessment"] != receipt
            or state["policy_pending"] != pending
        ):
            raise AdapterError("REP durable receipt mismatch", "access")
        state["policy_pending"] = None
    frame["complete"] = True
    _PROTOCOL_EXPECTED_POLICY_RECEIPT = json.loads(json.dumps(receipt))
    _PROTOCOL_POLICY_REVIEW = None
    if state["observed_policy_conflict"]:
        raise AdapterError(
            "Conflicting REP observations require review", "access"
        )
    if not allowed:
        error = AdapterError(
            "Robots policy disallows this source endpoint", "access"
        )
        error.policy_retained = True
        raise error


def require_protocol_transition():
    """Require the separate literal boundary before real access."""
    protocol_approval(protocol_purpose())


def protocol_approval(purpose):
    """Require a separate literal public boundary for each execution purpose."""
    marker = (
        _REVIEWED_HOSTED_PROTOCOL_TRANSITION
        if purpose == "hosted"
        else _REVIEWED_PROTOCOL_TRANSITION
    )
    if (
        purpose not in {"local", "hosted"}
        or not isinstance(marker, dict)
        or set(marker)
        != {
            "purpose",
            "evidence",
            "pins_sha256",
            "seed_sha256",
            "legacy_boundary_acknowledged",
            "collection_lock",
        }
        or marker["purpose"] != purpose
        or marker["legacy_boundary_acknowledged"] is not True
        or marker["pins_sha256"] != protocol_digest(_LEGACY_PROTOCOL_PINS)
        or marker["seed_sha256"]
        != protocol_digest(_LEGACY_PROTOCOL_PINS["seed"])
        or not isinstance(marker["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/273"
            r"#issuecomment-[1-9][0-9]*",
            marker["evidence"],
        )
    ):
        raise AdapterError("Exact public protocol boundary required", "access")
    return marker


def protocol_digest(value):
    """Hash canonical inert metadata without interpreting missing fields."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def protocol_purpose():
    return "hosted" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"


def protocol_run_binding(run, identity, head=None):
    """Bind authenticated attempt metadata; environment values alone do not."""
    if (
        not isinstance(run, dict)
        or type(run.get("id")) is not int
        or type(run.get("run_attempt")) is not int
        or run["id"] <= 0
        or run["run_attempt"] <= 0
        or (run["id"], run["run_attempt"]) != tuple(identity)
        or run.get("workflow_id") != _LEGACY_PROTOCOL_PINS["workflow_id"]
        or run.get("path") != _LEGACY_PROTOCOL_PINS["workflow_path"]
        or run.get("repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha") or "")
        or head is not None
        and run["head_sha"] != head
    ):
        raise AdapterError("Untrusted protocol attempt identity", "access")
    return run


def protocol_exact_jobs(run, completed=True):
    """Require a complete inventory with explicit identity on every job."""
    identity, attempt = run["id"], run["run_attempt"]
    result = workflow_api(
        f"/actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        not isinstance(jobs, list)
        or not jobs
        or len(jobs) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(jobs)
    ):
        raise AdapterError("Incomplete protocol job inventory", "access")
    ids, finished = set(), []
    for job in jobs:
        if (
            not isinstance(job, dict)
            or type(job.get("id")) is not int
            or job["id"] <= 0
            or job["id"] in ids
            or type(job.get("run_id")) is not int
            or type(job.get("run_attempt")) is not int
            or job.get("run_id") != identity
            or job.get("run_attempt") != attempt
            or job.get("head_sha") != run["head_sha"]
            or job.get("head_branch") != "main"
            or job.get("status")
            not in (
                {"completed"}
                if completed
                else {"queued", "in_progress", "completed"}
            )
        ):
            raise AdapterError("Untrusted protocol job binding", "access")
        ids.add(job["id"])
        if completed:
            finished.append(protocol_timestamp(job.get("completed_at")))
    if not completed and not any(j["status"] == "in_progress" for j in jobs):
        raise AdapterError("No authenticated running publisher job", "access")
    return jobs, max(finished) if finished else None


def protocol_timestamp(value):
    """Parse only bounded aware completion timestamps, never updated_at."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        result = parsed.timestamp()
        if parsed.utcoffset() is None or not 0 <= result <= time.time() + 1:
            raise ValueError()
        return result
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise AdapterError(
            "Invalid protocol completion timestamp", "access"
        ) from None


def protocol_fresh_history():
    """Check current coordination and exact completion chronology."""
    purpose = protocol_purpose()
    protocol_approval(purpose)
    pins = _LEGACY_PROTOCOL_PINS
    definition = workflow_api("/actions/workflows/" + pins["workflow_file"])
    if (
        definition.get("id") != pins["workflow_id"]
        or definition.get("path") != pins["workflow_path"]
        or purpose == "local"
        and definition.get("state") != "disabled_manually"
    ):
        raise AdapterError("Protocol workflow coordination required", "access")
    current = None
    if purpose == "hosted":
        if not re.fullmatch(r"[0-9a-f]{40}", os.environ.get("GITHUB_SHA", "")):
            raise AdapterError("Hosted source revision missing", "access")
        current = current_run_identity()
        run = workflow_api(f"/actions/runs/{current[0]}/attempts/{current[1]}")
        protocol_run_binding(run, current, os.environ.get("GITHUB_SHA"))
        if run.get("status") != "in_progress":
            raise AdapterError("Current protocol run is not active", "access")
        protocol_exact_jobs(run, completed=False)
    candidates, total, seen, first = [], None, set(), None
    for page in range(1, 101):
        path = "/actions/workflows/" + pins["workflow_file"]
        result = workflow_api(path + f"/runs?per_page=100&page={page}")
        runs, count = result.get("workflow_runs"), result.get("total_count")
        if (
            not isinstance(runs, list)
            or len(runs) > 100
            or type(count) is not int
            or count < 0
            or total is not None
            and total != count
        ):
            raise AdapterError("Unstable protocol history", "access")
        if first is None:
            first = result
        total = count
        for run in runs:
            identity, attempt = run.get("id"), run.get("run_attempt")
            protocol_run_binding(
                run,
                (identity, attempt),
                (
                    os.environ.get("GITHUB_SHA")
                    if (identity, attempt) == current
                    else None
                ),
            )
            if identity in seen or attempt <= 0:
                raise AdapterError("Duplicate protocol run", "access")
            seen.add(identity)
            if (identity, attempt) != current and run.get(
                "status"
            ) != "completed":
                raise AdapterError(
                    "Another publisher attempt is active", "access"
                )
            upper = attempt - 1 if (identity, attempt) == current else attempt
            for number in range(1, upper + 1):
                detail = workflow_api(
                    f"/actions/runs/{identity}/attempts/{number}"
                )
                protocol_run_binding(
                    detail, (identity, number), run["head_sha"]
                )
                if detail.get("status") != "completed":
                    raise AdapterError(
                        "Unfinished prior protocol attempt", "access"
                    )
                jobs, finished = protocol_exact_jobs(detail)
                candidates.append((finished, identity, number, detail, jobs))
        if len(seen) == total:
            break
        if len(runs) != 100 or len(seen) > total:
            raise AdapterError("Truncated protocol history", "access")
    else:
        raise AdapterError("Protocol history exceeds bound", "access")
    reread = workflow_api(path + "/runs?per_page=100&page=1")
    if reread != first or not candidates:
        raise AdapterError("Protocol history changed or is absent", "access")
    candidates.sort(key=lambda row: row[:3])
    if len(candidates) > 1 and candidates[-1][0] == candidates[-2][0]:
        raise AdapterError("Ambiguous protocol completion chronology", "access")
    latest = candidates[-1]
    if (
        workflow_api("/actions/workflows/" + pins["workflow_file"])
        != definition
    ):
        raise AdapterError(
            "Protocol workflow changed during coordination", "access"
        )
    result = workflow_api(f"/actions/runs/{latest[1]}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or result.get("total_count") != len(
        artifacts
    ):
        raise AdapterError("Incomplete protocol artifact inventory", "access")
    matches = [
        a
        for a in artifacts
        if a.get("name") == SOURCE_ID + "-pacing-state-" + str(latest[2])
    ]
    if len(matches) != 1:
        raise AdapterError("Newest protocol artifact required", "access")
    artifact = matches[0]
    owner = artifact.get("workflow_run", {})
    if (
        artifact.get("expired") is not False
        or type(artifact.get("id")) is not int
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", artifact.get("digest") or ""
        )
        or owner.get("id") != latest[1]
        or owner.get("head_sha") != latest[3]["head_sha"]
        or owner.get("head_branch") != "main"
        or owner.get("repository_id") != 1163601208
        or owner.get("head_repository_id") != 1163601208
    ):
        raise AdapterError("Untrusted newest protocol artifact", "access")
    return latest, artifact


def protocol_legacy_selection(latest, artifact):
    """Legacy conversion has one finite authenticated producing attempt."""
    pins = _LEGACY_PROTOCOL_PINS
    if (
        (latest[1], latest[2], latest[3]["head_sha"])
        != (pins["run_id"], pins["attempt"], pins["head"])
        or protocol_digest(latest[4]) != pins["jobs_sha256"]
        or artifact["id"] != pins["artifact_id"]
        or artifact["digest"] != pins["artifact_digest"]
    ):
        raise AdapterError(
            "Legacy boundary invalidated by newer state", "access"
        )
    file = workflow_api(
        "/contents/adapters/" + SOURCE_ID + "/adapter.py?ref=" + pins["head"]
    )
    try:
        raw = base64.b64decode(file["content"], validate=False)
        if (
            file["encoding"] != "base64"
            or hashlib.sha256(raw).hexdigest() != pins["source_sha256"]
        ):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise AdapterError(
            "Legacy producing source mismatch", "access"
        ) from None


def protocol_convert_legacy(value, origin, raw=None):
    """Convert only exact approved old bytes after authenticated selection."""
    purpose = protocol_purpose()
    protocol_approval(purpose)
    if _PROTOCOL_LEGACY_SELECTED is None:
        raise AdapterError("Legacy owner was not authenticated", "access")
    pins = _LEGACY_PROTOCOL_PINS
    expected = pins["local_state"] if origin == "local" else pins["owned_state"]
    if origin not in {"local", "owned"} or value != expected:
        raise AdapterError(
            "Unapproved legacy state or unknown fields", "access"
        )
    if origin == "local" and (
        purpose != "local"
        or not isinstance(raw, bytes)
        or hashlib.sha256(raw).hexdigest() != pins["local_raw_sha256"]
    ):
        raise AdapterError("Exact legacy local bytes required", "access")
    now = time.time()
    if now < _PROTOCOL_BOUNDARY_WALL or any(
        t >= now - 60 for t in pins["aged_starts_omitted"]
    ):
        raise AdapterError("Legacy history cannot yet be aged", "access")
    return validate_budget(json.loads(json.dumps(pins["seed"])))


def protocol_owned_value(value):
    """Modern typed states never fall back to a legacy missing-field default."""
    if isinstance(value, dict) and value.get("schema") == 1:
        return protocol_convert_legacy(value, "owned")
    return validate_budget(value)



# Separate default-null gate: ordinary modern-owned collection does not use it.
_REVIEWED_LOCAL_NATIVE_REENTRY = None
_LOCAL_REENTRY_ARMED = None
_LOCAL_REENTRY_CONTEXT = None
_LOCAL_REENTRY_CONSUMED = False


def reviewed_local_reentry_marker():
    """Require one exact public LOCAL boundary without metadata or file I/O."""
    marker = _REVIEWED_LOCAL_NATIVE_REENTRY
    keys = {
        "purpose",
        "evidence",
        "local_boundary_evidence",
        "pins_sha256",
        "seed_sha256",
        "local_raw_sha256",
        "directory_identity",
        "state_identity",
        "collection_lock_identity",
        "state_lock_identity",
        "newest_owned",
        "mode",
    }
    pins = _LEGACY_PROTOCOL_PINS
    if (
        protocol_purpose() != "local"
        or "DATA_SOURCE_TOKEN" in os.environ
        or not isinstance(marker, dict)
        or set(marker) != keys
        or marker["purpose"] != "one-off-local-native-reentry"
        or marker["mode"] != "LOCAL_ONLY_ORIGINAL_NONPUBLISHING_TEST"
        or not isinstance(marker["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/273"
            r"#issuecomment-[1-9][0-9]*",
            marker["evidence"],
        )
        or marker["local_boundary_evidence"]
        != protocol_approval("local")["evidence"]
        or marker["pins_sha256"] != protocol_digest(pins)
        or marker["seed_sha256"] != protocol_digest(pins["seed"])
        or not isinstance(marker["local_raw_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", marker["local_raw_sha256"])
    ):
        raise AdapterError(
            "Exact mature LOCAL reentry review required", "access"
        )
    for key in (
        "directory_identity",
        "state_identity",
        "collection_lock_identity",
        "state_lock_identity",
    ):
        value = marker[key]
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(type(n) is not int or n < 0 for n in value)
        ):
            raise AdapterError("Invalid mature reentry identity", "access")
    namespace = pins["local_namespace"]
    entries = {row["name"]: row for row in namespace["entries"]}
    if marker["directory_identity"] != [
        namespace["directory_device"],
        namespace["directory_inode"],
    ] or any(
        marker[key] != [entries[name]["device"], entries[name]["inode"]]
        for key, name in (
            ("collection_lock_identity", "collection.lock"),
            ("state_lock_identity", "state.lock"),
        )
    ):
        raise AdapterError("Reentry mature namespace differs", "access")
    owner_keys = (
        "run_id",
        "attempt",
        "head",
        "jobs_sha256",
        "source_sha256",
        "artifact_id",
        "artifact_digest",
    )
    if not isinstance(marker["newest_owned"], dict) or protocol_digest(
        marker["newest_owned"]
    ) != protocol_digest({key: pins[key] for key in owner_keys}):
        raise AdapterError("Reentry producing owner differs", "access")
    return marker


def arm_reviewed_local_native_reentry():
    """Dedicated oneoff entry; an absent literal refuses before any I/O."""
    global _LOCAL_REENTRY_ARMED
    marker = reviewed_local_reentry_marker()
    if _LOCAL_REENTRY_ARMED is not None or _LOCAL_REENTRY_CONSUMED:
        raise AdapterError("LOCAL native reentry was already armed", "access")
    _LOCAL_REENTRY_ARMED = json.loads(json.dumps(marker))


def verify_local_reentry_state(parent=None, state_fd=None, locks=None):
    """Read exact current mature bytes initially and under exclusive locks."""
    marker = reviewed_local_reentry_marker()
    if _LOCAL_REENTRY_ARMED != marker or _LOCAL_REENTRY_CONSUMED:
        raise AdapterError("Dedicated reentry is not armed", "access")
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    own_parent, own_state = parent is None, state_fd is None
    if own_parent:
        parent = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    try:
        parent_info = os.fstat(parent)
        named_parent = os.stat(directory, follow_symlinks=False)
        if (
            [parent_info.st_dev, parent_info.st_ino]
            != marker["directory_identity"]
            or (parent_info.st_dev, parent_info.st_ino)
            != (named_parent.st_dev, named_parent.st_ino)
            or parent_info.st_uid != os.getuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise AdapterError("Mature reentry parent changed", "access")
        if own_state:
            state_fd = protocol_safe_descriptor(
                parent, "state.json", os.O_RDONLY, marker["state_identity"]
            )
        try:
            before = os.fstat(state_fd)
            raw = os.pread(state_fd, 16385, 0)
            after = os.fstat(state_fd)
            named = os.stat("state.json", dir_fd=parent, follow_symlinks=False)
            fields = (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_mode",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
            if (
                any(getattr(before, k) != getattr(after, k) for k in fields)
                or any(getattr(after, k) != getattr(named, k) for k in fields)
                or [after.st_dev, after.st_ino] != marker["state_identity"]
                or after.st_uid != os.getuid()
                or after.st_nlink != 1
                or not stat.S_ISREG(after.st_mode)
                or stat.S_IMODE(after.st_mode) != 0o600
                or len(raw) != after.st_size
                or len(raw) > 16384
                or hashlib.sha256(raw).hexdigest() != marker["local_raw_sha256"]
                or (
                    os.stat(directory, follow_symlinks=False).st_dev,
                    os.stat(directory, follow_symlinks=False).st_ino,
                )
                != (parent_info.st_dev, parent_info.st_ino)
            ):
                raise AdapterError(
                    "Exact mature reentry state changed", "access"
                )
            for name, key in (
                ("collection.lock", "collection_lock_identity"),
                ("state.lock", "state_lock_identity"),
            ):
                descriptor = (locks or {}).get(name)
                if descriptor is None:
                    check = protocol_safe_descriptor(
                        parent, name, os.O_RDONLY, marker[key]
                    )
                    os.close(check)
                else:
                    info = os.fstat(descriptor)
                    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (
                        [info.st_dev, info.st_ino] != marker[key]
                        or (info.st_dev, info.st_ino)
                        != (named.st_dev, named.st_ino)
                        or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or not stat.S_ISREG(info.st_mode)
                        or stat.S_IMODE(info.st_mode) != 0o600
                    ):
                        raise AdapterError(
                            "Mature reentry lock changed", "access"
                        )
            value = validate_budget(json.loads(raw))
            protocol_healthy(value)
            if (
                value["blocked"]
                or value["in_flight"]
                or value["pending"] is not None
                or value["policy_pending"] is not None
            ):
                raise AdapterError(
                    "Mature reentry cannot clear a hold", "access"
                )
            return value
        finally:
            if own_state:
                os.close(state_fd)
    finally:
        if own_parent:
            os.close(parent)


def verify_exclusive_local_reentry(parent, state_fd, locks):
    """Repeat full exact chronology under both locks immediately precommit."""
    global _LOCAL_REENTRY_CONSUMED
    verify_local_reentry_state(parent, state_fd, locks)
    latest, artifact = protocol_fresh_history()
    if protocol_context_key(latest, artifact) != _LOCAL_REENTRY_CONTEXT:
        raise AdapterError("Mature reentry owner changed under lock", "access")
    protocol_legacy_selection(latest, artifact)
    # Metadata verification cannot substitute for the second current FD check.
    verify_local_reentry_state(parent, state_fd, locks)
    _LOCAL_REENTRY_CONSUMED = True


def protocol_safe_descriptor(parent, name, flags, expected=None):
    """Bind files beneath a retained verified parent without following links."""
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent
    )
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or identity != (current.st_dev, current.st_ino)
            or expected is not None
            and identity != tuple(expected)
        ):
            raise AdapterError("Untrusted protocol file identity", "access")
        _PROTOCOL_RETAINED_IDS[descriptor] = identity
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def protocol_peek_local():
    """Read only beneath the exact mature parent before any legacy bridge."""
    pins = _LEGACY_PROTOCOL_PINS["local_namespace"]
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(parent)
        if (
            (info.st_dev, info.st_ino)
            != (pins["directory_device"], pins["directory_inode"])
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise AdapterError("Legacy parent changed", "access")
        descriptor = protocol_safe_descriptor(parent, "state.json", os.O_RDONLY)
        try:
            raw = os.read(descriptor, 16385)
            if len(raw) > 16384:
                raise AdapterError("Oversized legacy local state", "access")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise AdapterError("Invalid local state", "access")
            return value
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def protocol_open_namespace(incoming):
    """Acquire lifecycle then state locks; never recreate mature local files."""
    global _PROTOCOL_COMPAT_DIRECTORY, _JOURNAL_BOOTSTRAP
    purpose, pins = protocol_purpose(), _LEGACY_PROTOCOL_PINS
    protocol_approval(purpose)
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    if purpose == "hosted":
        if os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
            raise AdapterError("Fresh hosted runner context required", "access")
        # Fresh creation requires authenticated owner and literal approval.
        os.mkdir(directory, 0o700)
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    keep = []
    try:
        info = os.fstat(parent)
        current = os.stat(directory, follow_symlinks=False)
        namespace = pins["local_namespace"]
        expected = (namespace["directory_device"], namespace["directory_inode"])
        if (
            info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            or purpose == "local"
            and (info.st_dev, info.st_ino) != expected
        ):
            raise AdapterError("Untrusted protocol parent", "access")
        entries = {entry["name"]: entry for entry in namespace["entries"]}
        preview = None
        if purpose == "local":
            check = protocol_safe_descriptor(parent, "state.json", os.O_RDONLY)
            try:
                preview_raw = os.read(check, 16385)
                preview = json.loads(preview_raw)
            finally:
                os.close(check)
            if len(preview_raw) > 16384:
                raise AdapterError("Oversized local protocol state", "access")
        modern = isinstance(preview, dict) and preview.get("schema") == 4
        if purpose == "local":
            if modern:
                protocol_healthy(validate_budget(preview))
            else:
                protocol_convert_legacy(preview, "local", preview_raw)
        old_lock = (
            entries.get("collection.lock") if purpose == "local" else None
        )
        if purpose == "local" and modern and old_lock is None:
            identity = protocol_approval(purpose)["collection_lock"]
            if (
                not isinstance(identity, list)
                or len(identity) != 2
                or any(type(v) is not int or v < 0 for v in identity)
            ):
                raise AdapterError(
                    "New lifecycle inode needs reviewed pin", "access"
                )
            old_lock = {"device": identity[0], "inode": identity[1]}
        flags = os.O_RDWR
        if old_lock is None:
            flags |= os.O_CREAT | os.O_EXCL
        collection = protocol_safe_descriptor(
            parent,
            "collection.lock",
            flags,
            (old_lock["device"], old_lock["inode"]) if old_lock else None,
        )
        keep.append(collection)
        fcntl.flock(collection, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = os.fstat(collection)
        named = os.stat("collection.lock", dir_fd=parent, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != _PROTOCOL_RETAINED_IDS[collection] or (
            named.st_dev,
            named.st_ino,
        ) != _PROTOCOL_RETAINED_IDS[collection]:
            raise AdapterError("Collection acquisition changed", "access")
        if SOURCE_ID == "ibm":
            old = entries.get("state.lock") if purpose == "local" else None
            state_lock = protocol_safe_descriptor(
                parent,
                "state.lock",
                os.O_RDWR | (0 if old else os.O_CREAT | os.O_EXCL),
                (old["device"], old["inode"]) if old else None,
            )
            keep.append(state_lock)
            fcntl.flock(state_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old_state = entries["state.json"] if purpose == "local" else None
        expected_state = old_state
        if modern and SOURCE_ID == "ibm":
            expected_state = None
        state_fd = protocol_safe_descriptor(
            parent,
            "state.json",
            os.O_RDWR | (0 if old_state else os.O_CREAT | os.O_EXCL),
            (
                (expected_state["device"], expected_state["inode"])
                if expected_state
                else None
            ),
        )
        keep.append(state_fd)
        if SOURCE_ID != "ibm":
            fcntl.flock(state_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before_read = os.fstat(state_fd)
        raw = os.read(state_fd, 16385)
        after_read = os.fstat(state_fd)
        named_state = os.stat(
            "state.json", dir_fd=parent, follow_symlinks=False
        )
        if (
            journal_version(before_read) != journal_version(after_read)
            or journal_version(after_read) != journal_version(named_state)
            or len(raw) != after_read.st_size
        ):
            raise AdapterError("Bridge journal changed during read", "access")
        _JOURNAL_BOOTSTRAP = (
            state_fd,
            journal_version(after_read),
            hashlib.sha256(raw).hexdigest(),
        )
        if purpose == "local":
            if len(raw) > 16384:
                raise AdapterError("Oversized protocol local state", "access")
            parsed = json.loads(raw)
            if parsed != preview:
                raise AdapterError(
                    "Local state changed before exclusive lock", "access"
                )
            local = (
                validate_budget(parsed)
                if modern
                else protocol_convert_legacy(parsed, "local", raw)
            )
        else:
            if raw:
                raise AdapterError("Fresh hosted state is not empty", "access")
            local = json.loads(json.dumps(pins["seed"]))
        if _LOCAL_REENTRY_ARMED is not None:
            verify_exclusive_local_reentry(
                parent,
                state_fd,
                {"collection.lock": keep[0], "state.lock": keep[1]},
            )
        # Typed constraints are the independently approved full MAX, not zero.
        state = protocol_merge_seed(local, incoming)
        _PROTOCOL_COMPAT_DIRECTORY = (
            directory,
            parent,
            (info.st_dev, info.st_ino),
        )
        return keep, parent, state_fd, state
    except BaseException:
        for descriptor in reversed(keep):
            try:
                held = os.fstat(descriptor)
                if (held.st_dev, held.st_ino) != _PROTOCOL_RETAINED_IDS.pop(
                    descriptor, None
                ):
                    raise AdapterError("Acquired descriptor reused", "access")
                os.close(descriptor)
            except BaseException:
                journal_failure()
        os.close(parent)
        # Never delete a competing or interrupted namespace/lock to retry.
        raise


def protocol_merge_seed(local, incoming):
    """Preserve pending/fatal/holds and all approved independent MAX bounds."""
    validate_budget(local)
    validate_budget(incoming)
    protocol_healthy(local)
    protocol_healthy(incoming)
    seed = json.loads(json.dumps(_LEGACY_PROTOCOL_PINS["seed"]))
    key = "not_before" if SOURCE_ID == "ibm" else "until"
    values = [seed, local, incoming]
    for field in (
        "observed_at",
        key,
        "interval",
        "page_interval",
        "rate_failures",
    ):
        seed[field] = max(state[field] for state in values)
    seed["blocked"] = any(state["blocked"] for state in values)
    seed["legacy_rate_history_unknown"] = True
    policy = dict(seed)
    policy.update(merge_policy_observations(seed, local))
    seed.update(merge_policy_observations(policy, incoming))
    starts = sorted(set(t for state in values for t in state["starts"]))
    capacity = 10 if SOURCE_ID == "ibm" else 5
    if any(t >= time.time() - 60 for t in starts[:-capacity]):
        raise AdapterError("Live protocol history exceeds storage", "access")
    seed["starts"] = starts[-capacity:]
    seed[key] = max(seed[key], time.time() + 60)
    seed["observed_at"] = max(seed["observed_at"], time.time())
    return validate_budget(seed)


def protocol_close_compatibility():
    """Release verified own FDs even after a different cleanup failed."""
    global _PROTOCOL_COMPAT_FDS, _PROTOCOL_COMPAT_DIRECTORY
    global _PROTOCOL_COMPAT_READY, _PROTOCOL_FIRST_WIRE_VERIFIED
    global _RESTORED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _STATE_DIR_FD, _STATE_DIR_ID, _STATE_PATH
    primary = sys.exc_info()[1]
    errors = []
    try:
        close_journal()
    except BaseException as error:
        errors.append(error)
    descriptors = [
        (fd, _PROTOCOL_RETAINED_IDS.pop(fd, None))
        for fd in reversed(_PROTOCOL_COMPAT_FDS)
    ]
    _PROTOCOL_COMPAT_FDS = []
    if _PROTOCOL_COMPAT_DIRECTORY is not None:
        descriptors.append(
            (_PROTOCOL_COMPAT_DIRECTORY[1], _PROTOCOL_COMPAT_DIRECTORY[2])
        )
    _PROTOCOL_COMPAT_DIRECTORY = None
    if _STATE_DIR_FD is not None:
        descriptors.append((_STATE_DIR_FD, _STATE_DIR_ID))
    _COLLECTION_LOCK_FD = None
    _STATE_DIR_FD = _STATE_DIR_ID = _STATE_PATH = None
    _PROTOCOL_COMPAT_READY = _PROTOCOL_FIRST_WIRE_VERIFIED = False
    _RESTORED = _STATE_TRUSTED = False
    for descriptor, identity in descriptors:
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Compatibility FD was reused", "access")
            os.close(descriptor)
        except BaseException as error:
            journal_failure()
            errors.append(error)
    if errors or _PUBLISHER_GATE_FAILED:
        with contextlib.suppress(BaseException):
            export_family_artifact()
    if errors and primary is None:
        raise errors[0]


def protocol_prepare_compatibility():
    """Perform only the finite reviewed bridge; not a generic bootstrap."""
    global _PROTOCOL_LEGACY_SELECTED, _PROTOCOL_BOUNDARY_WALL
    global _PROTOCOL_COMPAT_READY, _PROTOCOL_COMPAT_FDS
    global _PROTOCOL_FIRST_WIRE_VERIFIED, _PROTOCOL_COMPAT_CONTEXT
    global _PROTOCOL_QUIET_UNTIL, _RESTORED, _STATE_TRUSTED
    global _LOCAL_REENTRY_CONTEXT
    require_protocol_transition()
    if _LOCAL_REENTRY_ARMED is not None:
        reviewed_local_reentry_marker()
    if _PROTOCOL_FATAL:
        raise AdapterError("Protocol failure is sticky", "access")
    if _PROTOCOL_COMPAT_READY:
        # Even same-process public reentry must repeat fresh coordination.
        latest, artifact = protocol_fresh_history()
        if protocol_context_key(latest, artifact) != _PROTOCOL_COMPAT_CONTEXT:
            raise AdapterError("Protocol owner changed on reentry", "access")
        _PROTOCOL_FIRST_WIRE_VERIFIED = False
        _PROTOCOL_QUIET_UNTIL = time.monotonic() + 60
        return
    _PROTOCOL_BOUNDARY_WALL = time.time()
    latest, artifact = protocol_fresh_history()
    pins = _LEGACY_PROTOCOL_PINS
    if protocol_purpose() == "local":
        preview = protocol_peek_local()
        if preview.get("schema") == 4 and (
            latest[1],
            latest[2],
            latest[3]["head_sha"],
        ) == (pins["run_id"], pins["attempt"], pins["head"]):
            if _LOCAL_REENTRY_ARMED is None:
                raise AdapterError(
                    "Modern local state invalidates the legacy bootstrap",
                    "access",
                )
            verify_local_reentry_state()
            _LOCAL_REENTRY_CONTEXT = protocol_context_key(latest, artifact)
    if _LOCAL_REENTRY_ARMED is not None and _LOCAL_REENTRY_CONTEXT is None:
        raise AdapterError("Oneoff reentry owner no longer matches", "access")
    if (latest[1], latest[2], latest[3]["head_sha"]) == (
        pins["run_id"],
        pins["attempt"],
        pins["head"],
    ):
        protocol_legacy_selection(latest, artifact)
        _PROTOCOL_LEGACY_SELECTED = protocol_context_key(latest, artifact)
    else:
        # Only a modern typed owned state may continue; old bytes cannot use
        # an old review after a newer actual attempt.
        _PROTOCOL_LEGACY_SELECTED = None
    incoming = download_inert_artifact(
        (latest[1], latest[2], SOURCE_ID), artifact["id"], artifact["digest"]
    )
    try:
        descriptors, parent, state_fd, state = protocol_open_namespace(incoming)
        _PROTOCOL_COMPAT_FDS = descriptors
        protocol_commit_bridge(parent, state_fd, state)
        _PROTOCOL_COMPAT_CONTEXT = protocol_context_key(latest, artifact)
        _PROTOCOL_QUIET_UNTIL = time.monotonic() + 60
        _PROTOCOL_FIRST_WIRE_VERIFIED = False
        _PROTOCOL_COMPAT_READY = True
        _RESTORED = _STATE_TRUSTED = True
    except BaseException:
        _STATE_TRUSTED = False
        protocol_close_compatibility()
        raise


def protocol_context_key(latest, artifact):
    return (
        latest[1],
        latest[2],
        latest[3]["head_sha"],
        artifact["id"],
        artifact["digest"],
    )


def protocol_startup_quiet():
    """Wait before a physical reservation, not after recording its start."""
    require_protocol_transition()
    if not _PROTOCOL_COMPAT_READY or _PROTOCOL_FATAL:
        raise AdapterError("Publisher protocol is not prepared", "access")
    if not _PROTOCOL_FIRST_WIRE_VERIFIED:
        remaining = _PROTOCOL_QUIET_UNTIL - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


def protocol_before_wire():
    """Recheck coordination after quiet and before the first wire."""
    global _PROTOCOL_FIRST_WIRE_VERIFIED
    require_protocol_transition()
    if not _PROTOCOL_COMPAT_READY or _PROTOCOL_FATAL:
        raise AdapterError("Publisher protocol is not prepared", "access")
    if _PROTOCOL_FIRST_WIRE_VERIFIED:
        return
    if time.monotonic() < _PROTOCOL_QUIET_UNTIL:
        raise AdapterError("Startup quiet must precede reservation", "access")
    if time.time() < _PROTOCOL_BOUNDARY_WALL:
        raise AdapterError("Protocol clock moved backwards", "access")
    latest, artifact = protocol_fresh_history()
    if protocol_context_key(latest, artifact) != _PROTOCOL_COMPAT_CONTEXT:
        raise AdapterError(
            "Newest publisher owner changed during quiet", "access"
        )
    directory, parent, expected = _PROTOCOL_COMPAT_DIRECTORY
    info = os.stat(directory, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != expected:
        raise AdapterError("Protocol parent changed before access", "access")
    for descriptor in _PROTOCOL_COMPAT_FDS:
        info = os.fstat(descriptor)
        current = os.stat(
            "collection.lock", dir_fd=parent, follow_symlinks=False
        )
        if (
            info.st_uid != os.getuid()
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError(
                "Protocol descriptor changed before access", "access"
            )
    if SOURCE_ID == "ibm" and protocol_purpose() == "local":
        lock = next(
            e
            for e in _LEGACY_PROTOCOL_PINS["local_namespace"]["entries"]
            if e["name"] == "state.lock"
        )
        check = protocol_safe_descriptor(
            parent, "state.lock", os.O_RDONLY, (lock["device"], lock["inode"])
        )
        os.close(check)
    _PROTOCOL_FIRST_WIRE_VERIFIED = True


def protocol_lifecycle(function):
    """Retain bridge descriptors through publication and export."""

    @functools.wraps(function)
    def invoke(*args, **kwargs):
        global _PROTOCOL_LIFECYCLE_DEPTH
        _PROTOCOL_LIFECYCLE_DEPTH += 1
        try:
            return function(*args, **kwargs)
        finally:
            _PROTOCOL_LIFECYCLE_DEPTH -= 1
            if not _PROTOCOL_LIFECYCLE_DEPTH:
                protocol_close_compatibility()

    return invoke


def protocol_commit_bridge(parent, state_fd, state):
    """Adopt the authenticated locked RW generation before the first commit."""
    global _STATE_PATH, _STATE_DIR_FD, _STATE_DIR_ID, _STATE_INITIALIZING
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION
    global _JOURNAL_LOCK_ID
    directory = _PROTOCOL_COMPAT_DIRECTORY[0]
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_DIR_FD = os.dup(parent)
    info = os.fstat(parent)
    _STATE_DIR_ID = (info.st_dev, info.st_ino)
    _PROTOCOL_PARENT_IDS[_STATE_PATH] = _STATE_DIR_ID
    _STATE_INITIALIZING = False
    _COLLECTION_LOCK_FD = _PROTOCOL_COMPAT_FDS[0]
    info = os.fstat(_COLLECTION_LOCK_FD)
    _COLLECTION_LOCK_ID = _PROTOCOL_RETAINED_IDS[_COLLECTION_LOCK_FD]
    verify_collection_lock()
    state_lock = _PROTOCOL_COMPAT_FDS[-2]
    info = os.fstat(state_fd)
    if (
        _JOURNAL_BOOTSTRAP is None
        or _JOURNAL_BOOTSTRAP[0] != state_fd
        or journal_version(info) != _JOURNAL_BOOTSTRAP[1]
        or hashlib.sha256(os.pread(state_fd, 16385, 0)).hexdigest()
        != _JOURNAL_BOOTSTRAP[2]
    ):
        raise AdapterError("Initial journal generation changed", "access")
    _JOURNAL_FD = os.dup(state_fd)
    _JOURNAL_ID = (info.st_dev, info.st_ino)
    _JOURNAL_VERSION = journal_version(info)
    lock_info = os.fstat(state_lock)
    _JOURNAL_LOCK_ID = (lock_info.st_dev, lock_info.st_ino)
    _JOURNAL_LOCKS[state_lock] = _JOURNAL_LOCK_ID
    try:
        verify_journal()
        save_budget(state)
        verify_journal()
        verify_collection_lock()
    finally:
        primary = sys.exc_info()[1]
        _JOURNAL_LOCKS.pop(state_lock, None)
        errors = []
        for descriptor, unlock in ((state_fd, False), (state_lock, True)):
            try:
                protocol_release_descriptor(descriptor, unlock)
            except BaseException as error:
                journal_failure()
                errors.append(error)
        if errors and primary is None:
            raise errors[0]


def protocol_release_descriptor(descriptor, unlock=False):
    """Release only the exact original FD captured at namespace acquisition."""
    info = os.fstat(descriptor)
    identity = _PROTOCOL_RETAINED_IDS.get(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AdapterError("Initial bridge descriptor changed", "access")
    if unlock:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Initial bridge unlock changed FD", "access")
    os.close(descriptor)
    _PROTOCOL_RETAINED_IDS.pop(descriptor, None)
    _PROTOCOL_COMPAT_FDS.remove(descriptor)



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
        primary = sys.exc_info()[1]
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = None
        cleanup_error = None
        try:
            close_collection_lock()
        except BaseException as error:
            cleanup_error = error
        try:
            close_journal()
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
        if _PUBLISHER_GATE_FAILED:
            with contextlib.suppress(BaseException):
                export_family_artifact()
        if cleanup_error is not None and primary is None:
            raise cleanup_error


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
    if not isinstance(state, dict) or set(state) != set(empty_budget(0)):
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
    return validate_protocol(state)


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "rate_failures": 0,
        "in_flight": False,
        "pending": None,
        "historical_allowed_witnesses": [],
        "policy_denials": [],
        "policy_pending": None,
        "current_policy_assessment": None,
        "legacy_policy_history_unknown": True,
        "observed_policy_conflict": False,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "legacy_rate_history_unknown": True,
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
    descriptor = os.open(
        name,
        flags | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=_STATE_DIR_FD,
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
        _PROTOCOL_PARENT_IDS[_STATE_PATH] = _STATE_DIR_ID
        _STATE_LOCK = os.path.join(directory, "state.lock")
        verify_state_directory()
    finally:
        os.close(parent)


def journal_version(info):
    """Describe a held journal generation; timestamps are change fences."""
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


def journal_failure():
    """Make an authority mismatch sticky without adopting its new pathname."""
    global _PROTOCOL_FATAL, _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    _PROTOCOL_FATAL = _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False


def verify_journal(check_generation=True):
    """Require the retained RW inode, named state and original stable lock."""
    try:
        if _PROTOCOL_FATAL:
            raise AdapterError("Publisher journal gate is sticky", "access")
        verify_state_directory()
        if _JOURNAL_FD is None:
            raise AdapterError("Publisher journal was not retained", "access")
        info = os.fstat(_JOURNAL_FD)
        named = os.stat(
            "state.json", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        lock = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != _JOURNAL_ID
            or (named.st_dev, named.st_ino) != _JOURNAL_ID
            or check_generation
            and journal_version(info) != _JOURNAL_VERSION
            or not stat.S_ISREG(lock.st_mode)
            or lock.st_uid != os.getuid()
            or stat.S_IMODE(lock.st_mode) != 0o600
            or lock.st_nlink != 1
            or (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        return info
    except BaseException:
        journal_failure()
        raise


def close_journal():
    """Release our FD, preserving primary errors and foreign replacements."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_LOCK_ID
    global _JOURNAL_DIRTY, _JOURNAL_BOOTSTRAP
    primary = sys.exc_info()[1]
    descriptor, identity = _JOURNAL_FD, _JOURNAL_ID
    _JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
    _JOURNAL_DIRTY = False
    _JOURNAL_BOOTSTRAP = None
    if descriptor is not None:
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Publisher journal FD was reused", "access")
            os.close(descriptor)
        except BaseException:
            journal_failure()
            if primary is None:
                raise


def verify_collection_lock():
    """Require the descriptor captured at actual exclusive acquisition."""
    try:
        info = os.fstat(_COLLECTION_LOCK_FD)
        named = os.stat(
            "collection.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (
            (info.st_dev, info.st_ino) != _COLLECTION_LOCK_ID
            or (named.st_dev, named.st_ino) != _COLLECTION_LOCK_ID
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise AdapterError("Collection lock identity changed", "access")
    except BaseException:
        journal_failure()
        raise


def close_collection_lock():
    """Never close a descriptor number now owned by another file."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
    primary = sys.exc_info()[1]
    descriptor, identity = _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
    _COLLECTION_LOCK_FD = _COLLECTION_LOCK_ID = None
    if descriptor is None:
        return
    if descriptor in _PROTOCOL_COMPAT_FDS:
        _PROTOCOL_COMPAT_FDS.remove(descriptor)
    _PROTOCOL_RETAINED_IDS.pop(descriptor, None)
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            raise AdapterError("Collection lock FD was reused", "access")
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def invalidate_journal():
    """Poison our original FD only; never reopen a replacement pathname."""
    if _JOURNAL_FD is None:
        return
    verify_state_directory()
    info = os.fstat(_JOURNAL_FD)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink not in (0, 1)
        or (info.st_dev, info.st_ino) != _JOURNAL_ID
        or not _JOURNAL_DIRTY
        and info.st_nlink == 1
        and journal_version(info) != _JOURNAL_VERSION
    ):
        raise AdapterError("Original publisher journal changed", "access")
    os.ftruncate(_JOURNAL_FD, 0)
    os.fsync(_JOURNAL_FD)


@contextlib.contextmanager
def locked_budget():
    """Hold the stable state lock around reads/writes of our retained FD."""
    budget_file()
    descriptor, identity = None, None
    primary = sys.exc_info()[1]
    try:
        if _JOURNAL_LOCKS:
            original = next(iter(_JOURNAL_LOCKS))
            info = os.fstat(original)
            if (info.st_dev, info.st_ino) != _JOURNAL_LOCKS[original]:
                raise AdapterError("Publisher lock FD was reused", "access")
            descriptor = os.dup(original)
        else:
            descriptor = secure_file("state.lock", os.O_RDWR)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        info = os.fstat(descriptor)
        named = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        identity = (info.st_dev, info.st_ino)
        if identity != (named.st_dev, named.st_ino) or (
            _JOURNAL_LOCK_ID is not None and identity != _JOURNAL_LOCK_ID
        ):
            raise AdapterError("Publisher state lock changed", "access")
        _JOURNAL_LOCKS[descriptor] = identity
        # Adoption is possible only through the authenticated bridge, never
        # from a missing/changed mature journal at an ordinary read or save.
        verify_journal()
        yield descriptor
    except BaseException as error:
        primary = error
        journal_failure()
        raise
    finally:
        if descriptor is not None:
            _JOURNAL_LOCKS.pop(descriptor, None)
            try:
                info = os.fstat(descriptor)
                if (
                    identity is not None
                    and (info.st_dev, info.st_ino) == identity
                ):
                    os.close(descriptor)
                else:
                    raise AdapterError("Publisher lock FD was reused", "access")
            except BaseException:
                journal_failure()
                if primary is None:
                    raise


def load_budget():
    """Read only the retained generation and verify again after the read."""
    if not _JOURNAL_LOCKS:
        with locked_budget():
            return load_budget()
    try:
        verify_journal()
        raw = os.pread(_JOURNAL_FD, 16385, 0)
        verify_journal()
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher state", "access")
        return validate_budget(json.loads(raw))
    except BaseException:
        journal_failure()
        raise


def save_budget(state):
    """Commit through the retained RW inode; interrupted rewrites fail closed."""
    global _STATE_INITIALIZING, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PROTOCOL_FATAL:
        raise AdapterError("Publisher persistence failure is sticky", "access")
    if not _JOURNAL_LOCKS:
        with locked_budget():
            return save_budget(state)
    written_version = None
    try:
        validate_budget(state)
        payload = json.dumps(state, separators=(",", ":")).encode()
        if len(payload) > 16384:
            raise AdapterError("Oversized publisher state", "access")
        verify_journal()
        _STATE_INITIALIZING = False
        _JOURNAL_DIRTY = True
        # The explicit availability tradeoff is deliberate: a failed rewrite
        # leaves an unusable mature journal, never valid stale permissions.
        os.ftruncate(_JOURNAL_FD, 0)
        os.fsync(_JOURNAL_FD)
        verify_journal(False)
        offset = 0
        while offset < len(payload):
            verify_journal(False)
            count = os.pwrite(_JOURNAL_FD, payload[offset:], offset)
            if type(count) is not int or not 0 < count <= len(payload) - offset:
                raise AdapterError("Incomplete publisher state write", "access")
            offset += count
            verify_journal(False)
        os.fsync(_JOURNAL_FD)
        written_version = journal_version(verify_journal(False))
        actual = os.pread(_JOURNAL_FD, len(payload) + 1, 0)
        info = verify_journal(False)
        if journal_version(info) != written_version or actual != payload:
            raise AdapterError("Publisher state bytes changed", "access")
        _JOURNAL_VERSION = written_version
        _JOURNAL_DIRTY = False
    except BaseException:
        if written_version is not None:
            try:
                if journal_version(os.fstat(_JOURNAL_FD)) != written_version:
                    # Do not erase a different same-inode generation observed
                    # while verifying our write's readback.
                    _JOURNAL_DIRTY = False
            except BaseException:
                pass
        protocol_failure(state, [_STATE_PATH])
        raise


def pace():
    """Serialize publisher starts under all retained physical constraints."""
    global _PUBLISHER_STARTS, _PUBLISHER_INTERVAL
    check_deadline()
    if _PUBLISHER_STARTS >= PUBLISHER_START_LIMIT:
        raise AdapterError("Publisher physical-start bound exhausted", "access")
    with locked_budget():
        state = load_budget()
        protocol_healthy(state)
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
        _PUBLISHER_STARTS += 1


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
    verify_collection_lock()
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
                + max(_PUBLISHER_INTERVAL, state.get("interval", 12.2), 12.2),
            )
            state["observed_at"] = now
            save_budget(state)
        _PUBLISHER_GATE_FAILED = False


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
    """Read only bounded error evidence after the durable timer is saved."""
    global _PUBLISHER_BYTES
    try:
        try:
            limit = min(byte_limit, 65536)
            body = response.read(limit + 1)
        except Exception:
            raise AdapterError(
                "Publisher429 body unavailable; timed backoff retained",
                "access",
                429,
            ) from None
        if len(body) > limit:
            raise AdapterError(
                "Publisher429 body exceeds bound; timed backoff retained",
                "access",
                429,
            )
        _PUBLISHER_BYTES += len(body)
        reject_visible_challenge(
            body, response.headers, challenge_only=True, status=429
        )
        raise AdapterError(
            "Publisher429; durable adaptive backoff retained", "access", 429
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


@durable_transaction("physical")
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
    global _PUBLISHER_BYTES
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
            remaining = PUBLISHER_BYTE_LIMIT - _PUBLISHER_BYTES
            if remaining <= 0:
                raise AdapterError(
                    "Publisher aggregate-byte bound exhausted", "fetch"
                )
            byte_limit = min(byte_limit, RESPONSE_BYTE_LIMIT, remaining)
            if inventory:
                wait_inventory_page()
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        with publisher_attempt(publisher, inventory):
            try:
                if publisher:
                    protocol_transport_started()
                    verify_journal()
                    verify_collection_lock()
                response = _OPENER.open(
                    request, timeout=request_timeout(publisher)
                )
            except urllib.error.HTTPError as error:
                response = error
            except (urllib.error.URLError, OSError) as error:
                if publisher:
                    raise publisher_transport_error(error, current) from None
                raise
            if publisher:
                protocol_response_received()
            with response:
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
                    if publisher:
                        _PUBLISHER_BYTES += len(body)
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
        if publisher and robots and status == 200:
            _PROTOCOL_ROBOTS_HASHES[protocol_target(current)["origin"]] = (
                hashlib.sha256(body).hexdigest()
            )
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


@durable_transaction("assessment")
def check_robots(url):
    global _PUBLISHER_INTERVAL
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
            unavailable_policy(url)
            _ROBOTS[origin] = None
            _ROBOT_BODIES[origin] = ""
        elif status == 200:
            parser = urllib.robotparser.RobotFileParser()
            text = body.decode("utf-8", "strict")
            parser.parse(text.splitlines())
            _ROBOT_BODIES[origin] = text
            _ROBOTS[origin] = parser
        else:
            raise AdapterError(
                ("Publisher robots policy unavailable"), ("access"), status
            )
    parser = _ROBOTS[origin]
    if origin not in _ROBOT_BODIES:
        raise AdapterError("REP cache has no body provenance", "access")
    policy_delay = robots_directive(parser, "crawl_delay") if parser else None
    policy_rate = robots_directive(parser, "request_rate") if parser else None
    policy_interval = (
        policy_rate.seconds / policy_rate.requests if policy_rate else None
    )
    if parser is not None and not robots_directive(parser, "can_fetch", url):
        retain_policy(
            url,
            _ROBOT_BODIES[origin],
            False,
            policy_delay or 0,
            policy_interval,
        )
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
        retain_policy(
            url,
            _ROBOT_BODIES[origin],
            True,
            policy_delay or 0,
            policy_interval,
        )
        if rate and rate.seconds / rate.requests > 6:
            raise AdapterError(
                ("Stricter publisher request-rate requires review"), ("access")
            )

    if parser is None:
        retain_policy(url, _ROBOT_BODIES[origin], True)


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
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB
            + path
            + ("&" if "?" in path else "?")
            + "_fresh="
            + str(time.time_ns()),
            headers={
                **({"Authorization": "Bearer " + token} if token else {}),
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
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Workflow state credentials missing", "access")
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
        return protocol_owned_value(envelope["state"])
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
    with locked_budget():
        local = load_budget()
        protocol_healthy(local)
        protocol_healthy(incoming)
        now = time.time()
        combined = empty_budget(now)
        combined.update(merge_policy_observations(local, incoming))
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


def verify_owned_descriptor(descriptor, expected, directory=False):
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


def close_owned_descriptor(descriptor, expected, directory=False):
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
            verify_owned_descriptor(descriptor, expected, directory)
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


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
        verify_owned_descriptor(runner_parent, root_id, True)
        named_root = os.stat(runner_root, follow_symlinks=False)
        if (named_root.st_dev, named_root.st_ino) != root_id:
            raise AdapterError("Step output root changed", "access")
        for fd, identity, is_directory, parent_fd, name in descriptors:
            verify_owned_descriptor(fd, identity, is_directory)
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
        if info.st_size != 0:
            raise AdapterError("Fresh owned step output required", "access")
        line = b"pacing_ready=true\n"
        if os.write(descriptor, line) != len(line):
            raise AdapterError("Incomplete pacing-ready handoff", "access")
        verify_outputs()
        os.fsync(descriptor)
        verify_outputs()
    except BaseException:
        journal_failure()
        raise
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, identity, is_directory, _, _ in reversed(descriptors):
            try:
                close_owned_descriptor(fd, identity, is_directory)
            except BaseException as error:
                errors.append(error)
        if errors and primary is None:
            raise errors[0]


def artifact_descriptor(descriptor, expected):
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


def invalidate_owned_artifact(parent, root, name, parent_id, identity, handles):
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
                artifact_descriptor(handle, identity)
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
        artifact_descriptor(descriptor, identity)
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
                artifact_descriptor(descriptor, identity)
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise


def export_family_artifact():
    """Export only healthy infrastructure state; invalidate an owned stale file."""
    invalidate_protocol_export()
    path = os.environ.get("IBM_PACING_ARTIFACT_PATH")
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
        existing = None
        try:
            existing = os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
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
                artifact_descriptor(existing, existing_identity)
                os.ftruncate(existing, 0)
                os.fsync(existing)
                artifact_descriptor(existing, existing_identity)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            finally:
                close_owned_descriptor(existing, existing_identity)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion gate cannot be exported", "access"
            )
        run_id, attempt = current_run_identity()
        with locked_budget():
            state = load_budget()
            protocol_healthy(state)
            if state["in_flight"]:
                raise AdapterError(
                    "Unsettled state cannot be exported", "access"
                )
        protocol_size(state, (run_id, attempt))
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
        acquired = os.fstat(descriptor)
        identity = (acquired.st_dev, acquired.st_ino)
        parent_id = parent_identity
        output_identity = identity
        guard = None
        output_handles = (descriptor, guard)
        try:
            artifact_descriptor(descriptor, identity)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise AdapterError(
                    "Owned artifact acquisition changed", "access"
                )
            guard = os.dup(descriptor)
            output_handles = (descriptor, guard)
            artifact_descriptor(guard, identity)
            with locked_budget():
                with os.fdopen(os.dup(descriptor), "w") as file:
                    json.dump(envelope, file, separators=(",", ":"))
                    file.flush()
                    os.fsync(file.fileno())
                os.fsync(parent)
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
                    before_read = journal_version(info)
                    raw = os.read(check, 16385)
                    after_read = artifact_descriptor(check, reader_identity)
                    current = os.stat(
                        name, dir_fd=parent, follow_symlinks=False
                    )
                    if (
                        journal_version(after_read) != before_read
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
                    close_owned_descriptor(check, identity)
                check_deadline()
                verify_journal()
                mark_pacing_artifact_ready(parent, root)
                verify_journal()
                artifact_descriptor(descriptor, identity)
                artifact_descriptor(guard, identity)
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if identity != (named.st_dev, named.st_ino):
                    raise AdapterError("Owned artifact changed", "access")
        except BaseException:
            journal_failure()
            # Cleanup must never replace the primary handoff/body failure.
            with contextlib.suppress(BaseException):
                invalidate_owned_artifact(
                    parent, root, name, parent_id, identity, (guard, descriptor)
                )
            raise
        finally:
            primary = sys.exc_info()[1]
            errors = []
            for handle in (descriptor, guard):
                if handle is not None:
                    try:
                        artifact_descriptor(handle, identity)
                        os.close(handle)
                    except BaseException as error:
                        journal_failure()
                        errors.append(error)
            if errors:
                with contextlib.suppress(BaseException):
                    invalidate_owned_artifact(
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
            verify_owned_descriptor(parent, parent_identity, True)
            os.close(parent)
        except BaseException as error:
            journal_failure()
            close_error = error
        if close_error is not None or _PROTOCOL_FATAL:
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
                    verify_owned_descriptor(recovery, parent_identity, True)
                    invalidate_owned_artifact(
                        recovery,
                        root,
                        name,
                        parent_identity,
                        output_identity,
                        output_handles,
                    )
                if recovery is not None:
                    with contextlib.suppress(BaseException):
                        close_owned_descriptor(recovery, parent_identity, True)
        if close_error is not None and primary is None:
            raise close_error


def prepare_collection():
    protocol_prepare_compatibility()
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _COLLECTION_LOCK_ID
    global _PAGE_COMPLETED, _PUBLISHER_INTERVAL, _LOCAL_START_VERIFIED
    global _PUBLISHER_STARTS, _PUBLISHER_BYTES
    _PUBLISHER_STARTS = _PUBLISHER_BYTES = 0
    _LOCAL_START_VERIFIED = False
    _COLLECTION_STARTED = time.time()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        descriptor = secure_file(
            "collection.lock",
            os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
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
            identity = (info.st_dev, info.st_ino)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = os.fstat(descriptor)
            named = os.stat(
                "collection.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
            )
            if (held.st_dev, held.st_ino) != identity or (
                named.st_dev,
                named.st_ino,
            ) != identity:
                raise AdapterError("Collection acquisition changed", "access")
        except OSError:
            os.close(descriptor)
            raise AdapterError(
                ("Another local family collector is active"), ("access")
            ) from None
        _COLLECTION_LOCK_FD = descriptor
        _COLLECTION_LOCK_ID = (info.st_dev, info.st_ino)
    verify_collection_lock()
    restore_family_artifact()
    with locked_budget():
        state = load_budget()
        state["interval"] = max(state.get("interval", 6), 12.2)
        state["page_interval"] = max(state.get("page_interval", 12), 30)
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        state["not_before"] = max(state["not_before"], time.time() + 60)
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

    def read(url, *, inventory=False):
        if url not in pages:
            _, headers, body = fetch_public(url, inventory=inventory)
            if "html" not in headers.get("Content-Type", "").lower():
                raise AdapterError("Expected official IBM HTML", "parse")
            pages[url] = document_root(body.decode("utf-8", "strict"))
        return pages[url]

    programmes = discover_programmes(read)
    return {"documents": pages, "programmes": programmes}


def discover_programmes(read):
    """Walk the programme frontier using one memoized official document reader."""
    programmes = {}
    home = fetch_inventory_page(read, SOURCE_URL)
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
        if node.attrs.get("href")
        and re.search(r"entry[- ]level", node.text(), re.I)
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
            if not re.search(
                r"program(?:me)?s?\b", node.text() + " " + target, re.I
            ):
                continue
            # This explanatory article belongs to the already published
            # internship identity, rather than a second internship offer.
            if target == INTERNSHIP_EXPLANATION:
                programmes.setdefault(INTERNSHIP_URL, {INTERNSHIP_URL}).add(
                    target
                )
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


@protocol_lifecycle
def collect():
    require_protocol_transition()
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
        "display_name": "IBM",
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


@protocol_lifecycle
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    require_protocol_transition()
    try:
        with execution():
            return run_lifecycle(args.publish)
    except PhaseExpired:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
