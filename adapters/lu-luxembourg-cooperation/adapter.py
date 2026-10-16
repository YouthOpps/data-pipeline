"""Collect Luxembourg cooperation programmes and all linked recruitment calls."""

import argparse
import base64
import contextlib
import copy
import datetime
import email.utils
import fcntl
import functools
import hashlib
import html
import html.parser
import ipaddress
import io
import math
import stat
import ssl
import zipfile
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_ID = "lu-luxembourg-cooperation"
SOURCE_URL = "https://cooperation.gouvernement.lu/en/s-engager.html"
WEBSITE_URL = "https://cooperation.gouvernement.lu/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "LU"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Luxembourg Directorate for Development Cooperation and Humanitarian Affairs"

CATEGORIES = (
    "scholarships",
    "internships",
    "volunteering",
    "fellowships",
    "training",
    "competitions",
    "grants",
    "jobs",
    "other",
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
_PUBLISHER_GATE_FAILED = False
_ROBOTS_CACHE = {}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-lu-luxembourg-cooperation-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_ARTIFACT_NAME = "lu-luxembourg-cooperation-pacing-state"
_RESTORED = _STATE_TRUSTED = False
_BUDGET_INITIALIZING = None
_COLLECTION_LOCK_HELD = False
_PUBLISHER_HOST = "cooperation.gouvernement.lu"


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            with budget_state() as state:
                page_interval = state["page_interval"]
            wait = _PAGE_COMPLETED + page_interval - time.monotonic()
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


def _feed_text(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value or "")).split())


class AdapterError(Exception):
    """Collection/publication failure with a stage and optional HTTP status."""

    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent implicit redirects so each source hop is checked and paced."""

    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def persist_budget(file, state):
    """Commit state through retained original journal authority."""
    journal_acquire()
    journal_write(state)


@contextlib.contextmanager
def protocol_budget():
    with budget_state() as state:
        yield state


def protocol_publisher(url, kwargs):
    return (urllib.parse.urlsplit(url).hostname or "").removeprefix(
        "www."
    ) == _PUBLISHER_HOST


def protocol_rate_count(state):
    return state.get("rate_failures", 0)


# Protocol state is source-local. Old schemas require an exact reviewed handoff.
_PROTOCOL_ARTIFACT_ENV = "LU_COOPERATION_PACING_ARTIFACT_PATH"
_PROTOCOL_ROBOTS_HASHES = {}
_PROTOCOL_POLICY_FRAMES = []
_PROTOCOL_POLICY_REVIEW = None
_PROTOCOL_EXPECTED_POLICY_RECEIPT = None
_PROTOCOL_POLICY_ORIGINS = (
    "https://cooperation.gouvernement.lu",
    "https://www.cooperation.gouvernement.lu",
)
_PROTOCOL_PARENT_IDS = {}
_PROTOCOL_INODE_IDS = {}
_PROTOCOL_DESCRIPTOR_PATHS = {}
_PROTOCOL_ATOMIC = False
_PROTOCOL_RESPONSES = []
_PROTOCOL_OWNER = None
_PROTOCOL_DEPTH = 0
_PROTOCOL_FATAL = False
_PROTOCOL_RAM = None
_REVIEWED_PROTOCOL_TRANSITION = {
    "purpose": "local",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/273#issuec"
        "omment-6204933912"
    ),
    "pins_sha256": (
        "3c31ecb1b83783c848c447a72fa9169672040cf4adbaba292b930ef8993e"
        "f5f7"
    ),
    "seed_sha256": (
        "e6d81cfc15913db7d70f91ef977de0c1c37fc89a612d7f84ce4f79fdcdf6"
        "a701"
    ),
    "legacy_boundary_acknowledged": True,
    "collection_lock": [34, 339092],
}
_LEGACY_PROTOCOL_PINS = json.loads(
    '{"aged_starts_omitted":[1792067576.9975526],"artifact_digest'
    '":"sha256:4d3e58e3913b7c89bce1c2aa3e725e13f357e85c5ebf2db0b4'
    'e04f7b57b0beac","artifact_id":11986879298,"attempt":1,"head"'
    ':"f77eed642c4544bbeed009440c406064a4f5c6ad","jobs_sha256":"5'
    'd80ca9b562a0510f017b0b2741c4ee4a756da7d1f72c5e92b2407eb93d2f'
    '6da","local_namespace":{"directory_device":34,"directory_ino'
    'de":82812,"directory_mode":"0o700","entries":[{"device":34,"'
    'inode":82813,"name":"state.json"}],"source":"lu-luxembourg-c'
    'ooperation"},"local_raw_sha256":"c2093170a00f5bcbac4072e4269'
    '15f5e45797bdce6c007d38c2b703eda4f1e66","local_state":{"block'
    'ed":false,"family":"youthopps-lu-luxembourg-cooperation-publ'
    'isher-v1","interval":12.1,"observed_at":1792067611.3803442,"'
    'page_interval":30,"rate_failures":0,"schema":1,"starts":[179'
    '2067576.9975526,1792067609.2846851],"until":1792067623.48008'
    '87},"owned_state":{"blocked":false,"family":"youthopps-lu-lu'
    'xembourg-cooperation-publisher-v1","interval":12.1,"observed'
    '_at":1792079943.8632283,"page_interval":30,"rate_failures":0'
    ',"schema":1,"starts":[1792079911.9027379,1792079942.9077017]'
    ',"until":1792079955.9629714},"run_id":38763419391,"seed":{"b'
    'locked":false,"current_policy_assessment":{"allowed":true,"d'
    'elay":12,"id":"7a3bd1b47b3c877c7477a33fea260a53c00e5400c8b7e'
    '4de2d778343b6675f1c","origin":"https://cooperation.gouvernem'
    'ent.lu","path":"/en/s-engager/appels-a-candidature/UNV.html"'
    ',"policy":"07291afcac02b104c6314f7846fb8052975552b218c8ea963'
    'b17dd71f4931b8a","rate":null,"target":"2bbb8b98a25f232c4a1ba'
    '43bb2f70b5dce2862d510933e0906a7480ecaca8cfb","transaction":"'
    'da4468afb675b0a00b32f3b3017c7fdaff287e4a1ffddf96e18552d60cc6'
    '8934"},"family":"youthopps-lu-luxembourg-cooperation-publish'
    'er-v1","historical_allowed_witnesses":[{"allowed":true,"dela'
    'y":12,"origin":"https://cooperation.gouvernement.lu","path":'
    '"/en/s-engager.html","policy":"07291afcac02b104c6314f7846fb8'
    '052975552b218c8ea963b17dd71f4931b8a","rate":null,"target":"a'
    '9ae0f6955db693021ac9a4c680711463e3490d6fd60d8623a97723e58cd7'
    '1d8"}],"in_flight":false,"interval":12.1,"legacy_policy_hist'
    'ory_unknown":true,"legacy_rate_history_unknown":true,"observ'
    'ed_at":1792108957.082905,"observed_policy_conflict":false,"p'
    'age_interval":30,"pending":null,"policy_denials":[],"policy_'
    'pending":null,"rate_failures":0,"schema":4,"starts":[1792108'
    '891.8132899,1792108923.8077855,1792108924.10595,1792108955.2'
    '681339,1792108955.4492805],"until":1792109029.2900872},"sour'
    'ce":"lu-luxembourg-cooperation","source_sha256":"6d99dade0d8'
    '57f517bba6ec72a91df9c6e7fe8b4ee655ce1a684ac673311260f","work'
    'flow_file":"fetch-lu-luxembourg-cooperation.yml","workflow_i'
    'd":378391272,"workflow_path":".github/workflows/fetch-lu-lux'
    'embourg-cooperation.yml"}'
)
_REVIEWED_HOSTED_PROTOCOL_TRANSITION = {
    "purpose": "hosted",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/273#issuec"
        "omment-6204933912"
    ),
    "pins_sha256": (
        "3c31ecb1b83783c848c447a72fa9169672040cf4adbaba292b930ef8993e"
        "f5f7"
    ),
    "seed_sha256": (
        "e6d81cfc15913db7d70f91ef977de0c1c37fc89a612d7f84ce4f79fdcdf6"
        "a701"
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
    """Invalidate safe prior output through the same retained-output guards."""
    path = os.environ.get(_PROTOCOL_ARTIFACT_ENV)
    if not path:
        return
    root = os.environ.get("RUNNER_TEMP")
    if (
        not root
        or os.path.dirname(os.path.abspath(path)) != os.path.realpath(root)
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe prior pacing export path", "access")
    journal_invalidate_owned_output(path)


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
    """Retain failure through the original descriptor, never a new pathname."""
    journal_failure(candidate)


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
    journal_before_wire()
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
                and kind in {"physical", "policy"}
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
                if kind == "assessment":
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
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
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


def protocol_safe_descriptor(parent, name, flags, expected=None):
    descriptor, identity = journal_open_original(parent, name, flags, expected)
    _PROTOCOL_RETAINED_IDS[descriptor] = identity
    _PROTOCOL_RETAINED_VERSIONS[descriptor] = journal_version(
        os.fstat(descriptor)
    )
    return descriptor


def protocol_peek_local():
    """Read only beneath the exact mature parent before any legacy bridge."""
    pins = _LEGACY_PROTOCOL_PINS["local_namespace"]
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    parent, parent_id = journal_open_directory(directory)
    try:
        info = os.fstat(parent)
        _PROTOCOL_RETAINED_IDS[parent] = (info.st_dev, info.st_ino)
        if (
            (info.st_dev, info.st_ino)
            != (pins["directory_device"], pins["directory_inode"])
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise AdapterError("Legacy parent changed", "access")
        descriptor = protocol_safe_descriptor(parent, "state.json", os.O_RDONLY)
        try:
            raw = protocol_read_original(parent, "state.json", descriptor)
            if len(raw) > 16384:
                raise AdapterError("Oversized legacy local state", "access")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise AdapterError("Invalid local state", "access")
            return value
        finally:
            protocol_release_original(descriptor)
    finally:
        protocol_release_original(parent, directory=True)


def protocol_open_namespace(incoming):
    """Acquire lifecycle then state locks; never recreate mature local files."""
    global _PROTOCOL_COMPAT_DIRECTORY
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
    parent, parent_id = journal_open_directory(directory)
    keep = []
    try:
        info = os.fstat(parent)
        _PROTOCOL_RETAINED_IDS[parent] = (info.st_dev, info.st_ino)
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
                preview_raw = protocol_read_original(
                    parent, "state.json", check
                )
                preview = json.loads(preview_raw)
            finally:
                protocol_release_original(check)
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
        journal_bind_collection(collection, _PROTOCOL_RETAINED_IDS[collection])
        global _JOURNAL_COLLECTION_FD
        _JOURNAL_COLLECTION_FD = collection
        fcntl.flock(collection, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
        raw = protocol_read_original(parent, "state.json", state_fd)
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
            with contextlib.suppress(BaseException):
                protocol_release_original(descriptor)
        with contextlib.suppress(BaseException):
            protocol_release_original(parent, directory=True)
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
    """Close only retained own descriptors; interrupted files require review."""
    global _PROTOCOL_COMPAT_FDS, _PROTOCOL_COMPAT_DIRECTORY
    global _PROTOCOL_COMPAT_READY, _PROTOCOL_FIRST_WIRE_VERIFIED
    global _RESTORED, _STATE_TRUSTED
    primary = sys.exc_info()[1]
    errors = []
    try:
        journal_release()
    except BaseException as error:
        errors.append(error)
    for descriptor in reversed(_PROTOCOL_COMPAT_FDS):
        try:
            protocol_release_original(descriptor)
        except BaseException as error:
            journal_failure()
            errors.append(error)
    _PROTOCOL_COMPAT_FDS = []
    if _PROTOCOL_COMPAT_DIRECTORY is not None:
        try:
            protocol_release_original(
                _PROTOCOL_COMPAT_DIRECTORY[1], directory=True
            )
        except BaseException as error:
            journal_failure()
            errors.append(error)
    _PROTOCOL_COMPAT_DIRECTORY = None
    _PROTOCOL_COMPAT_READY = _PROTOCOL_FIRST_WIRE_VERIFIED = False
    _RESTORED = _STATE_TRUSTED = False
    global _COLLECTION_LOCK_HELD
    _COLLECTION_LOCK_HELD = False

    if _JOURNAL_FATAL:
        with contextlib.suppress(BaseException):
            journal_invalidate_export()
    if errors and primary is None:
        raise errors[0]


def protocol_prepare_compatibility():
    """Perform only the finite reviewed bridge; not a generic bootstrap."""
    global _PROTOCOL_LEGACY_SELECTED, _PROTOCOL_BOUNDARY_WALL
    global _PROTOCOL_COMPAT_READY, _PROTOCOL_COMPAT_FDS
    global _PROTOCOL_FIRST_WIRE_VERIFIED, _PROTOCOL_COMPAT_CONTEXT
    global _PROTOCOL_QUIET_UNTIL, _RESTORED, _STATE_TRUSTED
    require_protocol_transition()
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
            raise AdapterError(
                "Modern local state invalidates the legacy bootstrap", "access"
            )
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
                if _PROTOCOL_COMPAT_READY:
                    time.sleep(12.1)
                protocol_close_compatibility()

    return invoke


def protocol_commit_bridge(parent, state_fd, state):
    """Use the original in-place writer and preserve the legacy state inode."""
    global _COLLECTION_LOCK_HELD, _BUDGET_INITIALIZING
    directory = _PROTOCOL_COMPAT_DIRECTORY[0]
    path = os.path.join(directory, "state.json")
    pin_protocol_parent(path, state_fd, _PROTOCOL_COMPAT_DIRECTORY[2])
    try:
        info = journal_descriptor(state_fd, _PROTOCOL_RETAINED_IDS[state_fd])
        expected_version = _PROTOCOL_RETAINED_VERSIONS[state_fd]
        if journal_version(info) != expected_version:
            raise AdapterError("Initial state generation changed", "access")
        _PROTOCOL_EXPECTED_GENERATIONS[path] = expected_version
        persist_budget(None, state)
    except BaseException:
        journal_failure(state)
        raise
    _BUDGET_INITIALIZING = False
    protocol_release_original(state_fd, unlock=True)
    _PROTOCOL_COMPAT_FDS.remove(state_fd)
    _COLLECTION_LOCK_HELD = True


def utc_now():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def safe_error(error):
    """Return a bounded actionable error without URL queries or credentials."""
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


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def empty_budget(now):
    return {
        "schema": 4,
        "in_flight": False,
        "pending": None,
        "historical_allowed_witnesses": [],
        "policy_denials": [],
        "policy_pending": None,
        "current_policy_assessment": None,
        "legacy_policy_history_unknown": True,
        "observed_policy_conflict": False,
        "legacy_rate_history_unknown": True,
        "family": FAMILY,
        "starts": [],
        "interval": 12.1,
        "until": now,
        "rate_failures": 0,
        "page_interval": 30,
        "blocked": False,
        "observed_at": now,
    }


def validate_budget(state):
    """Validate source-bound inert state; never expire refusals or embargoes."""
    if (
        not isinstance(state, dict)
        or set(state) != set(empty_budget(0))
        or type(state["schema"]) is not int
        or state["schema"] != 4
        or state["family"] != FAMILY
        or not numeric(state["until"])
        or not numeric(state["observed_at"])
        or state["observed_at"] > time.time() + 1
        or not numeric(state["interval"])
        or state["interval"] < 12
        or not numeric(state["page_interval"])
        or state["page_interval"] < 30
        or type(state["rate_failures"]) is not int
        or state["rate_failures"] < 0
        or type(state["blocked"]) is not bool
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 5
        or any(
            not numeric(t) or t > state["observed_at"] for t in state["starts"]
        )
        or sorted(set(state["starts"])) != state["starts"]
    ):
        raise AdapterError("Invalid retained publisher state", "access")
    return validate_protocol(state)


def budget_path(host=None):
    global _BUDGET_INITIALIZING
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    try:
        os.mkdir(directory, 0o700)
        _BUDGET_INITIALIZING = True
    except FileExistsError:
        if _BUDGET_INITIALIZING is None:
            _BUDGET_INITIALIZING = False
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    return os.path.join(directory, "state.json")


@contextlib.contextmanager
def budget_state(host=None):
    """Serialize updates through the retained schema4 journal inode."""
    global _BUDGET_INITIALIZING
    with journal_locked():
        try:
            raw = journal_read()
            state = validate_budget(json.loads(raw))
            protocol_healthy(state)
        except BaseException:
            journal_failure()
            raise
        try:
            yield state
        finally:
            primary = sys.exc_info()[1]
            if not _JOURNAL_FATAL:
                try:
                    state["observed_at"] = time.time()
                    state["starts"] = [
                        t for t in state["starts"] if t > time.time() - 60
                    ]
                    persist_budget(None, state)
                    _BUDGET_INITIALIZING = False
                except BaseException:
                    journal_failure(state)
                    if primary is None:
                        raise
            elif primary is None:
                raise AdapterError("Publisher journal gate is sticky", "access")


def pace(url, interval=12):
    """Preserve actual-start history and completion guards across local runs."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    while True:
        with budget_state() as state:
            protocol_healthy(state)
            if state["blocked"]:
                raise AdapterError(
                    "Publisher refusal requires reviewed recovery", "access"
                )
            now = time.time()
            starts = [t for t in state["starts"] if now - t < 60]
            state["starts"] = starts
            state["interval"] = max(12.1, interval, state["interval"])
            ready = max(
                state["until"],
                starts[-1] + state["interval"] if starts else now,
                starts[0] + 60 if len(starts) >= 5 else now,
            )
            if ready <= now:
                state["starts"].append(now)
                return
            if ready - now > 120:
                raise AdapterError(
                    "Publisher backoff exceeds collection wait budget", "access"
                )
        time.sleep(max(0, ready - time.time()))


def http_bytes(
    url, *, headers=None, method="GET", payload=None, limit=5_000_000
):
    """Read bounded nonpublisher HTTP without implicit credential redirects."""
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={**(headers or {}), "User-Agent": _USER_AGENT},
    )
    try:
        response = _OPENER.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise AdapterError("Response exceeds byte limit", "fetch")
        return response.status, response.headers, body


@durable_transaction("physical")
def request_bytes(
    url, *, interval=12, method="GET", headers=None, payload=None
):
    """Apply publisher-only physical-start and persistent response guards."""
    global _PUBLISHER_GATE_FAILED
    if (urllib.parse.urlsplit(url).hostname or "").removeprefix(
        "www."
    ) != _PUBLISHER_HOST:
        return http_bytes(url, method=method, headers=headers, payload=payload)
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={**(headers or {}), "User-Agent": _USER_AGENT},
    )
    pace(url, interval)
    print(
        json.dumps(
            {
                "event": "request_start",
                "url": url,
                "time": time.time(),
                "user_agent": _USER_AGENT,
            }
        ),
        file=sys.stderr,
        flush=True,
    )
    try:
        try:
            protocol_transport_started()
            response = _OPENER.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        protocol_response_received()
        with response:
            with budget_state() as state:
                now = time.time()
                if response.status == 429:
                    state["rate_failures"] += 1
                    state["until"] = max(
                        state["until"],
                        now + 1800 * 2 ** (state["rate_failures"] - 1),
                    )
                    state["interval"] = max(24, state["interval"] * 2)
                    state["page_interval"] = max(60, state["page_interval"] * 2)
                if (
                    response.status in (401, 403, 451)
                    and urllib.parse.urlsplit(url).path != "/robots.txt"
                ):
                    state["blocked"] = True
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        until = (
                            now + float(retry)
                            if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                            else email.utils.parsedate_to_datetime(
                                retry
                            ).timestamp()
                        )
                        if not numeric(until):
                            raise ValueError()
                        state["until"] = max(state["until"], until)
                    except (ValueError, TypeError, OverflowError):
                        if response.status != 429:
                            state["blocked"] = True
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds byte limit", "fetch")
            if (
                urllib.parse.urlsplit(url).path == "/robots.txt"
                and response.status == 200
            ):
                _PROTOCOL_ROBOTS_HASHES[protocol_target(url)["origin"]] = (
                    hashlib.sha256(body).hexdigest()
                )
            return response.status, response.headers, body
    finally:
        _PUBLISHER_GATE_FAILED = True
        completed = time.time()
        with budget_state() as state:
            state["until"] = max(
                state["until"],
                completed + max(12.1, interval, state["interval"]),
            )
        _PUBLISHER_GATE_FAILED = False
        print(
            json.dumps(
                {"event": "request_complete", "url": url, "time": completed}
            ),
            file=sys.stderr,
            flush=True,
        )


@durable_transaction("policy")
def fetch_source(url, min_interval=12, *, with_headers=False):
    """Read a reviewed HTTPS source with checked, paced redirects and retries.

    Return decoded text, or (decoded text, HTTP headers) with with_headers=True.
    Raise AdapterError when permitted retrieval cannot complete.
    """
    original = urllib.parse.urlsplit(url)

    def checked(value):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or host != (original.hostname or "").lower().removeprefix("www.")
            or parsed.port != original.port
        ):
            raise AdapterError(
                "Source redirect leaves the reviewed HTTPS host", "fetch"
            )
        if host == "localhost" or host.endswith(".localhost"):
            raise AdapterError("Public HTTPS source required", "fetch")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise AdapterError("Public HTTPS source required", "fetch")
        except ValueError:
            pass

    for attempt in range(3):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(
                    current, interval=min_interval
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(current, headers["Location"])
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status in (429, 503):
                    raise AdapterError(
                        f"Publisher returned HTTP {status}; backoff retained",
                        "fetch",
                        status,
                    )
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status}", "fetch", status
                    )
                if original.path == "/robots.txt":
                    _PROTOCOL_ROBOTS_HASHES[protocol_target(url)["origin"]] = (
                        hashlib.sha256(body).hexdigest()
                    )
                text = body.decode(headers.get_content_charset() or "utf-8")
                return (text, headers) if with_headers else text
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error), "fetch"
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


@durable_transaction("assessment")
def check_robots(url):
    """Check endpoint access against current robots and return start spacing."""
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
    )
    if robots_url not in _ROBOTS_CACHE:
        try:
            _ROBOTS_CACHE[robots_url] = fetch_source(robots_url)
        except AdapterError as error:
            if (
                error.status is not None
                and 400 <= error.status < 500
                and error.status != 429
            ):
                unavailable_policy(url)
                _ROBOTS_CACHE[robots_url] = ""
            else:
                raise
    text = _ROBOTS_CACHE[robots_url]
    groups, group = [], {"agents": [], "rules": [], "delay": 0}
    for line in text.splitlines():
        key, separator, value = line.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
        elif key == "crawl-delay":
            try:
                group["delay"] = float(value)
                if not 0 <= group["delay"] <= 60:
                    raise ValueError()
            except ValueError:
                raise AdapterError("Unsupported robots crawl delay", "access")
    groups.append(group)
    directives = [
        line.split("#", 1)[0].strip().lower()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (
        directives
        and not any(g["agents"] for g in groups)
        and not all(line.startswith("sitemap:") for line in directives)
    ):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    agent = _USER_AGENT.split("/", 1)[0].lower()
    specific = [g for g in groups if agent in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    policies = [policy for policy in (specific, legacy) if policy]
    if not policies:
        policies = [[g for g in groups if "*" in g["agents"]]]
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    for selected in policies:
        matches = []
        for g in selected:
            for directive, pattern in g["rules"]:
                if not pattern:
                    continue
                expression = (
                    "^"
                    + ".*".join(
                        re.escape(part)
                        for part in pattern.removesuffix("$").split("*")
                    )
                    + ("$" if pattern.endswith("$") else "")
                )
                if re.search(expression, path):
                    matches.append(
                        (
                            len(pattern.replace("*", "").removesuffix("$")),
                            directive == "allow",
                        )
                    )
        if matches and not max(matches)[1]:
            retain_policy(
                url,
                text,
                False,
                max([0] + [g["delay"] for p in policies for g in p]),
            )
    delay = max([12] + [g["delay"] for policy in policies for g in policy])
    retain_policy(url, text, True, delay)
    return delay

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
    """Build the public record envelope with factual source provenance."""
    now = utc_now()
    return {
        "id": SOURCE_ID
        + "-"
        + hashlib.sha256(f"{SOURCE_ID}|{url}".encode()).hexdigest()[:24],
        "title": title,
        "url": url,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": published_at,
        "summary": "",
        "attribution": ATTRIBUTION,
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
    """Validate every record, stable ID, source, date, category and provenance.

    Allow empty lists only for existing snapshots and validated expiry cleanup.
    Fresh collection must produce genuine nonempty records.
    """
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
        if record["id"].lower() in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].lower())
        if record.get("attribution") != ATTRIBUTION:
            raise AdapterError("Missing publisher attribution", "validate")
        if not re.fullmatch(SOURCE_ID + r"-[a-f0-9]{24}", record["id"]):
            raise AdapterError(
                "Invalid stable publisher identifier", "validate"
            )
        url = urllib.parse.urlsplit(record["url"])
        if (
            url.scheme != "https"
            or url.netloc != "cooperation.gouvernement.lu"
            or not url.path.startswith("/en/s-engager")
        ):
            raise AdapterError("Unexpected publisher record URL", "validate")
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
                    if field == "deadline" and re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        datetime.date.fromisoformat(value)
                    elif (
                        datetime.datetime.fromisoformat(
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
        if (
            not isinstance(record.get("summary"), str)
            or len(record["summary"]) > 600
            or re.search("<[^>]+>", record["summary"])
        ):
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


@contextlib.contextmanager
def collection_lock():
    """Hold one source lifecycle lock, including restore/snapshot/export."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    global _COLLECTION_LOCK_HELD
    if _COLLECTION_LOCK_HELD:
        yield
        return
    path = os.path.join(os.path.dirname(budget_path()), "collection.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise AdapterError("Unsafe source lifecycle lock", "access")
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _COLLECTION_LOCK_HELD = True
        try:
            time.sleep(12.1)
            yield
        finally:
            _COLLECTION_LOCK_HELD = False
            time.sleep(12.1)
            fcntl.flock(lock, fcntl.LOCK_UN)


@protocol_lifecycle
def collect():
    """Retrieve the full inventory under the shared collection lock."""
    global _STATE_TRUSTED
    protocol_prepare_compatibility()
    with collection_lock():
        if os.environ.get("GITHUB_ACTIONS") == "true":
            restore_family_artifact()
        _STATE_TRUSTED = True
        return collect_inventory()


class Element:
    """Minimal tree for explicit publisher content and native navigation."""

    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return " ".join(
            " ".join(
                child.text() if isinstance(child, Element) else child
                for child in self.children
            ).split()
        )


class PublicHTML(html.parser.HTMLParser):
    """Parse public HTML without scripts, browser state or remote assets."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
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


def one(nodes, description):
    if len(nodes) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return nodes[0]


@durable_transaction("policy")
def page(url, inventory=False):
    """Read permitted source markup and reject soft refusals or error pages."""

    def retrieve():
        return fetch_source(url, min_interval=check_robots(url))

    document = fetch_inventory_page(retrieve) if inventory else retrieve()
    parsed = PublicHTML(document)
    visible = parsed.root.text()
    if (
        re.search(
            r"verify (?:that )?you are human|confirm (?:that )?you are (?:a )?human|unusual traffic|access (?:denied|blocked)|request (?:has been )?(?:blocked|denied)",
            visible,
            re.I,
        )
        or "cf-chl-" in document
    ):
        with budget_state() as state:
            state["blocked"] = True
        raise AdapterError(
            "Publisher returned an explicit content challenge", "access"
        )
    if not re.search(r"</body\s*>\s*</html\s*>\s*$", document, re.I):
        raise AdapterError("Incomplete publisher HTML", "fetch")
    root = one(
        [node for node in parsed.root.walk() if node.tag == "main"],
        "publisher main region",
    )
    title = one(
        [node for node in root.walk() if node.tag == "h1"], "publisher title"
    ).text()
    if not title or re.search(
        r"access denied|not found|captcha|forbidden", title, re.I
    ):
        raise AdapterError(
            "Publisher did not return the opportunity page", "fetch"
        )
    return root, title


# These are source-specific semantic mappings, checked against live content.
# They describe actual standing programmes, not fabricated currently-open calls.
_PROGRAMMES = {
    "Internships at the Directorate for Development Cooperation and Humanitarian Affairs": (
        "directorate-internships",
        "internships",
        [],
        (
            "Paid internship positions",
            "3 to 6 months",
            "Master’s level",
            "Luxembourg citizen or resident",
            "English and French",
            "understand and speak Luxembourgish",
        ),
        "Paid 3–6 month placements for Master's students with Luxembourg citizenship or residence. English, French and spoken Luxembourgish are required. Availability varies.",
    ),
    "Internships at ENDA Santé": (
        "enda-sante-internships",
        "internships",
        ["SN"],
        ("Dakar, Senegal", "travel expenses", "internship report"),
        "Internships in Dakar, Senegal. Luxembourg contributes to travel costs when the intern submits a detailed report; other eligibility and availability are unspecified.",
    ),
    "The Junior Technical Assistant Programme (JTA/ATJ)": (
        "jta",
        "jobs",
        [],
        (
            "Lux-Development",
            "not having exceeded the age of 30",
            "resident in Luxembourg for five years",
            "Each May",
        ),
        "LuxDev early-career placements for post-secondary graduates aged 30 or younger. Luxembourg citizenship or five years' residence is required. Recruitment uses a separate annual call.",
    ),
    "The Junior Professional Officer Programme (JPO/JEA)": (
        "jpo",
        "jobs",
        [],
        (
            "Master",
            "at least two years",
            "not being over 32 years",
            "resident in Luxembourg for five years",
            "recruitment is carried out yearly",
        ),
        "UN placements for Master's graduates with at least two years' experience. Luxembourg citizenship or five years' residence and age conditions apply; specific vacancies govern selection.",
    ),
    "The UN Volunteer Program (UNV)": (
        "unv",
        "volunteering",
        [],
        (
            "UNV Youth Internationals",
            "UNV Specialists",
            "Bachelor",
            "not being more than 26 years old",
            "no professional experience is required",
        ),
        "Standing UN volunteer programme with youth and specialist routes. The youth route requires a Bachelor's degree and no work experience; nationality or residence rules depend on the route. Current calls govern actual selection.",
    ),
    "The Junior Professional Programme in delegation (JPD)": (
        "jpd",
        "jobs",
        [],
        (
            "European Union",
            "Master",
            "no more than one year",
            "Every two years in January",
            "having been in Luxembourg for 5 years",
        ),
        "EU delegation placements for Master's graduates with Luxembourg nationality or five years' residence and no more than one year of EU-institution experience. Separate recruitment calls govern availability.",
    ),
    "The Voluntary Cooperation Service (VCS)": (
        "vcs",
        "volunteering",
        [],
        (
            "6 and 12 months",
            "18 and 30 years old",
            "Luxembourg resident",
            "not having the status of a student or employee",
            "subsistence allowance",
            "social insurance coverage",
        ),
        "Supported 6–12 month development volunteering for Luxembourg residents aged 18–30 who are neither students nor employees when applying. A subsistence allowance and social insurance are provided.",
    ),
}
_EXCLUDED = {
    "Volunteering within NGOs in Luxembourg": "generic NGO directory referral",
    "Professional involvement with NGOs in Luxembourg": "external jobs referral",
    "Development cooperation leave": "leave/reimbursement for existing workers",
}
_CALLS = (
    "https://cooperation.gouvernement.lu/en/s-engager/appels-a-candidature.html"
)


def programme_records(root):
    """Extract every substantive advertised programme and explicit exclusion."""
    records, seen = [], set()
    for node in root.walk():
        if (
            node.tag != "details"
            or "cmp-accordion__item" not in node.attrs.get("class", "").split()
        ):
            continue
        heading = one(
            [
                item
                for item in node.walk()
                if item.tag == "h3"
                and "cmp-accordion__header"
                in item.attrs.get("class", "").split()
            ],
            "programme heading",
        ).text()
        if heading in seen:
            raise AdapterError("Duplicate programme heading", "validate")
        seen.add(heading)
        if heading in _EXCLUDED:
            continue
        if heading not in _PROGRAMMES:
            raise AdapterError(
                "Unclassified new programme: " + heading, "parse"
            )
        identity, category, countries, markers, summary = _PROGRAMMES[heading]
        text = node.text()
        if any(marker not in text for marker in markers):
            raise AdapterError("Programme facts changed: " + heading, "parse")
        links = [
            item.attrs.get("href", "")
            for item in node.walk()
            if item.tag == "a"
        ]
        if not any(
            link.startswith("mailto:sengager.cooperation@")
            or link.startswith("https://www.volontaires.lu/")
            or ("luxdev" in link.lower() or "lux-development" in link.lower())
            for link in links
        ):
            raise AdapterError(
                "Programme application route is missing", "parse"
            )
        anchor = node.attrs.get("id")
        if not anchor or not re.fullmatch(r"[A-Za-z0-9_-]+", anchor):
            raise AdapterError("Programme anchor is unavailable", "parse")
        record = make_record(
            heading,
            SOURCE_URL + "#" + anchor,
            [category],
            kind="programme-overview",
            host_countries=countries,
            method="publisher-programme",
            evidence=[SOURCE_URL],
        )
        # Identity follows the stable programme mechanism, not mutable headings or anchors.
        record["id"] = (
            SOURCE_ID
            + "-"
            + hashlib.sha256(("programme|" + identity).encode()).hexdigest()[
                :24
            ]
        )
        record["summary"] = summary
        record["tags"] = ["standing-programme", "availability-unconfirmed"]
        if countries == ["LU"]:
            record["location"] = "Luxembourg"
        elif countries == ["SN"]:
            record["location"] = "Dakar, Senegal"
        records.append(record)
    if set(_PROGRAMMES) - seen:
        raise AdapterError(
            "Advertised programme inventory is incomplete", "parse"
        )
    return records


def call_links(root, parent):
    """Read native subsection navigation; never fabricate search parameters."""
    menus = [
        node
        for node in root.walk()
        if node.tag == "ul" and node.attrs.get("id") == "localnav"
    ]
    if len(menus) > 1:
        raise AdapterError(
            "Ambiguous recruitment subsection navigation", "parse"
        )
    result = []
    for menu in menus:
        for node in menu.walk():
            if node.tag != "a" or not node.attrs.get("href"):
                continue
            url = urllib.parse.urljoin(parent, node.attrs["href"])
            parsed = urllib.parse.urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "cooperation.gouvernement.lu"
                or parsed.query
                or not parsed.path.startswith(
                    "/en/s-engager/appels-a-candidature/"
                )
            ):
                raise AdapterError("Unreviewed recruitment subsection", "parse")
            if url in result:
                raise AdapterError("Duplicate recruitment subsection", "parse")
            result.append(url)
    # Any future pagination must be reviewed, not silently truncated.
    for node in root.walk():
        if node.tag == "a" and (
            "next" in node.attrs.get("rel", "").split()
            or re.search(r"pagination|pager", node.attrs.get("class", ""), re.I)
        ):
            raise AdapterError("Recruitment pagination changed", "parse")
    return result


def recruitment_record(root, title, url):
    """Record a genuine call as one call, without inventing individual posts."""
    text = root.text()
    if "Recruitment Notice" not in text:
        raise AdapterError("Recruitment page lacks an actual notice", "parse")
    if not re.search(
        r"United Nations Volunteers|Junior Professional|Junior Technical|internship|volunteer",
        text,
        re.I,
    ):
        raise AdapterError(
            "Call youth/programme relevance needs review", "parse"
        )
    deadlines = re.findall(
        r"application deadline of\s+(\d{1,2} [A-Za-z]+ \d{4})", text
    )
    if not deadlines or len(set(deadlines)) != 1:
        raise AdapterError("Missing or ambiguous recruitment deadline", "parse")
    try:
        deadline = (
            datetime.datetime.strptime(deadlines[0], "%d %B %Y")
            .date()
            .isoformat()
        )
    except ValueError as error:
        raise AdapterError(
            "Unsupported recruitment deadline", "parse"
        ) from error
    applications = [
        node.attrs.get("href", "")
        for node in root.walk()
        if node.tag == "a"
        and node.attrs.get("href", "").startswith("https://app.unv.org/")
    ]
    if not applications or not all(
        term in text
        for term in (
            "Associate",
            "Specialist",
            "Luxembourg nationals",
            "five years",
            "Benin",
            "Cabo Verde",
            "Costa Rica",
            "Laos",
            "Rwanda",
            "Senegal",
        )
    ):
        raise AdapterError("Call facts or application routes changed", "parse")
    record = make_record(
        title, url, ["volunteering"], method="publisher-call", evidence=[url]
    )
    record["deadline"] = deadline
    record["summary"] = (
        "Grouped UN volunteer recruitment for Associate and Specialist roles. "
        "Applicants must have Luxembourg nationality, five years' Luxembourg "
        "residence, or nationality of Benin, Cabo Verde, Costa Rica, Laos, Rwanda "
        "or Senegal. Individual role criteria remain on the official application site."
    )
    record["tags"] = ["grouped-recruitment-call"]
    # Residency is an alternative to nationality, so no exclusive citizenship list.
    record["eligible_countries"] = []
    closing = datetime.date.fromisoformat(deadline)
    current = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=-12))
    ).date()
    record["status"] = "expired" if current > closing else "open"
    return record


def collect_inventory():
    """Collect all advertised programmes and every native linked call page."""
    global _PAGE_COMPLETED
    _PAGE_COMPLETED = None
    root, title = page(SOURCE_URL, inventory=True)
    if title != "Getting involved":
        raise AdapterError("Source programme hub changed", "parse")
    records = programme_records(root)
    links = [
        urllib.parse.urljoin(SOURCE_URL, node.attrs.get("href", ""))
        for node in root.walk()
        if node.tag == "a"
    ]
    if _CALLS not in links:
        raise AdapterError("Official recruitment hub link is missing", "parse")
    pending, visited = [_CALLS], set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            raise AdapterError("Recruitment navigation cycle", "parse")
        visited.add(url)
        call, heading = page(url, inventory=True)
        children = call_links(call, url)
        if children:
            pending.extend(children)
        elif url != _CALLS:
            records.append(recruitment_record(call, heading, url))
        elif "Calls for application" not in heading:
            raise AdapterError("Official calls hub changed", "parse")
    validate_records(records)
    return records


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


def run_completion(run):
    """Order exact completed attempts by their actual job completion times."""
    identity, attempt = run["id"], run["run_attempt"]
    response = workflow_api(
        f"/actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = response.get("jobs")
    if (
        not isinstance(jobs, list)
        or not jobs
        or len(jobs) > 100
        or type(response.get("total_count")) is not int
        or response["total_count"] != len(jobs)
    ):
        raise AdapterError(
            "Incomplete completed-attempt job inventory", "access"
        )
    completed, began = [], []
    for job in jobs:
        try:
            if (
                not isinstance(job, dict)
                or job.get("status") != "completed"
                or job.get("run_id", identity) != identity
                or job.get("run_attempt", attempt) != attempt
                or job.get("head_sha", run["head_sha"]) != run["head_sha"]
            ):
                raise ValueError()
            end = datetime.datetime.fromisoformat(
                job["completed_at"].replace("Z", "+00:00")
            )
            if (
                end.utcoffset() is None
                or not 0 <= end.timestamp() <= time.time() + 1
            ):
                raise ValueError()
            start = datetime.datetime.fromisoformat(
                job.get("started_at", job["completed_at"]).replace(
                    "Z", "+00:00"
                )
            )
            if (
                start.utcoffset() is None
                or not 0 <= start.timestamp() <= end.timestamp()
            ):
                raise ValueError()
            completed.append(end.timestamp())
            began.append(start.timestamp())
        except (ValueError, TypeError, AttributeError, KeyError):
            raise AdapterError(
                "Invalid actual job completion chronology", "access"
            ) from None
    return max(completed), min(began)


def completed_identity(run, expected=None, expected_head=None):
    """Bind a completed attempt to its source, immutable head and actual jobs."""
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
            or run["head_repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_branch"] != "main"
            or not isinstance(run.get("head_sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            or expected is not None
            and (identity, attempt, source) != expected
            or expected_head is not None
            and run["head_sha"] != expected_head
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise AdapterError(
            "Untrusted completed source attempt", "access"
        ) from None
    finished, began = run_completion(run)
    return finished, began, identity, attempt, source


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
                or identity <= 0
                or type(attempt) is not int
                or attempt <= 0
                or not isinstance(run.get("head_sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
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
                            expected_head=run["head_sha"],
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
                            expected_head=run["head_sha"],
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


def workflow_api(path):
    """Read workflow history without the publication credential."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = http_bytes(
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
            raise ValueError()
        return json.loads(body)
    except Exception:
        raise AdapterError(
            "Workflow pacing state unavailable", "access"
        ) from None


def download_inert_artifact(identity, artifact_id, digest=None):
    """Read authenticated source/attempt-bound data; never execute artifacts."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, headers, _ = http_bytes(
            _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip",
            headers={
                **({"Authorization": "Bearer " + token} if token else {}),
                "Accept": "application/vnd.github+json",
            },
            limit=1024,
        )
        if status != 302:
            raise ValueError()
        target = headers.get("Location", "")
        parsed = urllib.parse.urlsplit(target)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or "." not in parsed.hostname
            or parsed.hostname.endswith((".local", ".internal", ".localhost"))
        ):
            raise ValueError()
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError()
        # Signed storage requests have no Authorization and are never logged.
        status, _, body = http_bytes(target, limit=32768)
        if status != 200:
            raise ValueError()
        if digest is not None and (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or digest[7:] != hashlib.sha256(body).hexdigest()
        ):
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "pacing-state.json":
                raise ValueError()
            entry = entries[0]
            if (
                entry.file_size > 16384
                or entry.flag_bits & 1
                or entry.external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise ValueError()
            envelope = json.loads(archive.read(entry))
        run_id, attempt, source = identity
        if (
            not isinstance(envelope, dict)
            or set(envelope)
            != {
                "schema",
                "repository",
                "run_id",
                "run_attempt",
                "source",
                "state",
            }
            or type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or envelope["repository"] != "YouthOpps/data-pipeline"
            or type(envelope["run_id"]) is not int
            or envelope["run_id"] != run_id
            or type(envelope["run_attempt"]) is not int
            or envelope["run_attempt"] != attempt
            or envelope["source"] != source
        ):
            raise ValueError()
        return protocol_owned_value(envelope["state"])
    except Exception:
        raise AdapterError(
            "Invalid or unavailable source pacing artifact", "access"
        ) from None


def merge_budget(local, incoming, now):
    """Intersect retained limits without weakening refusals or rate history."""
    validate_budget(local)
    validate_budget(incoming)
    protocol_healthy(local)
    protocol_healthy(incoming)
    merged = empty_budget(now)
    merged["until"] = max(local["until"], incoming["until"], now + 60)
    merged["interval"] = max(local["interval"], incoming["interval"])
    merged["page_interval"] = max(
        local["page_interval"], incoming["page_interval"]
    )
    merged["rate_failures"] = max(
        local["rate_failures"], incoming["rate_failures"]
    )
    merged["blocked"] = local["blocked"] or incoming["blocked"]
    merged["starts"] = sorted(set(local["starts"] + incoming["starts"]))[-5:]
    merged["legacy_rate_history_unknown"] = (
        local["legacy_rate_history_unknown"]
        or incoming["legacy_rate_history_unknown"]
    )
    merged.update(merge_policy_observations(local, incoming))
    return validate_budget(merged)


def restore_family_artifact():
    """Require the exact newest completed source attempt, never an older hit."""
    global _RESTORED, _STATE_TRUSTED
    require_protocol_transition()
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        try:
            bootstrap = json.loads(
                os.environ.get("LU_COOPERATION_PACING_BOOTSTRAP", "")
            )
            if (
                set(bootstrap) != {"schema", "family", "not_before", "evidence"}
                or type(bootstrap["schema"]) is not int
                or bootstrap["schema"] != 1
                or bootstrap["family"] != FAMILY
                or not numeric(bootstrap["not_before"])
                or not isinstance(bootstrap["evidence"], str)
                or not re.fullmatch(
                    r"https://github\.com/YouthOpps/data-pipeline/(?:issues|pull)/[0-9]+(?:#issuecomment-[0-9]+)?",
                    bootstrap["evidence"],
                )
            ):
                raise ValueError()
            incoming = empty_budget(time.time())
            incoming["until"] = max(incoming["until"], bootstrap["not_before"])
        except Exception:
            raise AdapterError(
                "Reviewed first-source bootstrap required", "access"
            ) from None
    else:
        run_id, attempt, _ = latest
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or len(artifacts) > 100
            or result.get("total_count") != len(artifacts)
        ):
            raise AdapterError("Incomplete pacing artifact inventory", "access")
        matches = [
            a
            for a in artifacts
            if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
        ]
        if len(matches) != 1 or matches[0].get("expired") is not False:
            raise AdapterError(
                "Latest attempt pacing artifact missing or expired", "access"
            )
        artifact = matches[0]
        if (
            type(artifact.get("id")) is not int
            or artifact["id"] <= 0
            or artifact.get("workflow_run", {}).get("id") != run_id
            or not numeric(artifact.get("size_in_bytes"))
            or artifact["size_in_bytes"] > 32768
        ):
            raise AdapterError("Invalid pacing artifact binding", "access")
        incoming = download_inert_artifact(
            latest, artifact["id"], artifact.get("digest")
        )
    with budget_state() as local:
        merged = merge_budget(local, incoming, time.time())
        local.clear()
        local.update(merged)
    _RESTORED, _STATE_TRUSTED = True, True


def invalidate_artifact(parent, name):
    """Invalidate only a safely owned artifact, before attempting unlink."""
    try:
        descriptor = os.open(
            name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent
        )
    except FileNotFoundError:
        return
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe existing pacing artifact", "access")
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def artifact_output(path):
    """Keep a pinned output descriptor until export and signaling succeed."""
    global _PUBLISHER_GATE_FAILED
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    name = os.path.basename(path)
    try:
        info = os.fstat(parent)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o022
            or info.st_mode & 0o700 != 0o700
        ):
            raise AdapterError("Unsafe pacing artifact directory", "access")
        invalidate_artifact(parent, name)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion cannot be exported", "access"
            )
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            yield descriptor
            os.fsync(descriptor)
            os.fsync(parent)
        except BaseException:
            _PUBLISHER_GATE_FAILED = True
            # Keep the exact owned descriptor open until failure cleanup.
            # Truncate before unlink: failed unlink still leaves invalid JSON.
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            raise

    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def export_family_artifact():
    """Export the unchanged owned envelope under retained journal authority."""
    path = os.environ.get("LU_COOPERATION_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
        journal_invalidate_export()
        return

    def payload():
        run_id, attempt = current_run_identity()
        with budget_state() as local:
            protocol_healthy(local)
            if local["in_flight"]:
                raise AdapterError(
                    "Unsettled state cannot be exported", "access"
                )
            state = dict(local)
        protocol_size(state, (run_id, attempt))
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


def github(method, path, payload=None, missing_ok=False):
    """Call only the configured publication repository with scoped credentials.

    Return the decoded JSON response, or None for an allowed missing resource.
    The optional payload is a JSON-serializable object.
    """
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError(
            "DATA_SOURCE_TOKEN is required for publication", "publish"
        )
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
    """Read one source data/metadata pair at an exact remote main revision."""
    head = github("GET", "/git/ref/heads/main")["object"]["sha"]
    tree = github("GET", "/git/commits/" + head)["tree"]["sha"]
    files = {}
    for name in ("data.json", "metadata.json"):
        file = github(
            "GET",
            f"/contents/datas/{SOURCE_ID}/{name}?ref={head}",
            missing_ok=True,
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
    """Atomically replace only source files, preserving concurrent updates.

    Files maps data/metadata filenames to complete JSON text. Retries rebase
    only when this source is unchanged; newer source snapshots are preserved.
    """
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
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
                    "Source changed concurrently; newer remote data "
                    "was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; durable remote outcome "
                    "could not be updated",
                    "publish",
                )


def metadata(attempt, previous, records, error=None):
    """Build attributed run metadata, retaining the last success on failure."""
    result = {
        "display_name": "Luxembourg Development Cooperation",
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (
            previous.get("last_success_at") if error else attempt
        ),
        "last_checked_at": (
            previous.get("last_checked_at") if error else attempt
        ),
        "record_count": len(records),
        "inventory_scope": "complete-advertised-youth-programmes-and-linked-calls",
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else f"Validated refresh retains {len(records)} records"
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def remove_expired_records(records, checked_at):
    """Filter verified deadlines after validating the complete fresh inventory.

    Unspecified recruitment closing times use the last civil date (UTC−12).
    """
    validate_records(records)
    checked = datetime.datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing = datetime.datetime.fromisoformat(deadline).date()
            latest_date = checked.astimezone(
                datetime.timezone(datetime.timedelta(hours=-12))
            ).date()
            expired = latest_date > closing
        elif deadline:
            closing = datetime.datetime.fromisoformat(
                deadline.replace("Z", "+00:00")
            )
            if closing.tzinfo is not None:
                expired = checked > closing
        if not expired:
            retained.append(record)
    validate_records(retained, allow_empty=True)
    return retained


@protocol_lifecycle
def main():
    """Keep trusted pacing available even when snapshot/authentication fails."""
    global _STATE_TRUSTED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source "
        "using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    protocol_prepare_compatibility()
    with collection_lock():
        if args.publish and os.environ.get("GITHUB_ACTIONS") == "true":
            restore_family_artifact()
        else:
            with budget_state():
                pass
        _STATE_TRUSTED = True
        try:
            return run_refresh(args.publish)
        finally:
            export_family_artifact()


def run_refresh(publish):
    """Refresh one source under the already-held lifecycle lock."""
    snapshot = read_snapshot() if publish else None
    old = (
        json.loads(snapshot["files"]["data.json"])
        if snapshot and snapshot["files"]["data.json"] is not None
        else []
    )
    previous = (
        json.loads(snapshot["files"]["metadata.json"])
        if snapshot and snapshot["files"]["metadata.json"] is not None
        else {}
    )
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = utc_now(), None
    try:
        records = collect()
        validate_records(records)
        records = remove_expired_records(records, utc_now())
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
                key: value
                for key, value in before.items()
                if key not in temporal
            } == {
                key: value
                for key, value in record.items()
                if key not in temporal
            }
            record.update(
                created_at=before.get("created_at", attempt),
                first_seen_at=before.get("first_seen_at", attempt),
                updated_at=(
                    before.get("updated_at", attempt) if same else attempt
                ),
                last_seen_at=attempt,
                last_checked_at=attempt,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
    if publish and (not failure or snapshot["files"]["data.json"] is not None):
        files = {
            "metadata.json": json.dumps(outcome, ensure_ascii=False, indent=2)
            + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(
                attempt,
                previous,
                old,
                AdapterError(
                    "Publication failed: " + safe_error(error), "publish"
                ),
            )
            try:
                latest = read_snapshot()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files(
                    latest,
                    {
                        "metadata.json": json.dumps(
                            outcome, ensure_ascii=False, indent=2
                        )
                        + "\n"
                    },
                )
            except Exception:
                outcome["message"] = (
                    "Publication failed; durable failure status could not be "
                    "recorded. Remote data was not force-overwritten."
                )
    elif publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


_JOURNAL_COLLECTION_FD = None
_PROTOCOL_RETAINED_IDS = {}
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
    global _JOURNAL_FAILED_STATE, _PROTOCOL_FATAL, _PROTOCOL_RAM
    _JOURNAL_FATAL = _PUBLISHER_GATE_FAILED = _PROTOCOL_FATAL = True
    _STATE_TRUSTED = False
    if candidate is not None:
        with contextlib.suppress(BaseException):
            _JOURNAL_FAILED_STATE = copy.deepcopy(candidate)
            _PROTOCOL_RAM = copy.deepcopy(candidate)
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
    path = budget_path()
    _JOURNAL_PATH = path
    parent, parent_id = journal_open_directory(os.path.dirname(path))
    _JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID = parent, parent_id
    try:
        expected = _PROTOCOL_INODE_IDS.get(path)
        try:
            descriptor, identity = journal_open_original(
                parent, os.path.basename(path), os.O_RDWR, expected
            )
        except FileNotFoundError:
            if not (False):
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
        required_version = _PROTOCOL_EXPECTED_GENERATIONS.get(path)
        if (
            required_version is not None
            and journal_version(info) != required_version
        ):
            journal_close_acquired(descriptor, identity)
            raise AdapterError("Initial publisher generation changed", "access")
        _JOURNAL_FD, _JOURNAL_ID = descriptor, identity
        _JOURNAL_VERSION = journal_version(info)
        journal_verify()
        _JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID = descriptor, identity
        journal_verify()
        _PROTOCOL_DESCRIPTOR_PATHS[_JOURNAL_FD] = path
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
    global _JOURNAL_NEW, _BUDGET_INITIALIZING
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
        _BUDGET_INITIALIZING = False
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
    journal_descriptor(_JOURNAL_COLLECTION_FD, _JOURNAL_COLLECTION_ID)

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
    global _JOURNAL_COLLECTION_FD, _JOURNAL_COLLECTION_ID
    global _JOURNAL_ID, _JOURNAL_LOCK_ID, _JOURNAL_PARENT_ID
    primary = sys.exc_info()[1]
    errors = []
    releases = [
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
    _JOURNAL_COLLECTION_FD = _JOURNAL_COLLECTION_ID = None
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


def journal_invalidate_export():
    path = os.environ.get("LU_COOPERATION_PACING_ARTIFACT_PATH")
    if path:
        journal_invalidate_owned_output(path)


def protocol_release_original(descriptor, unlock=False, directory=False):
    identity = _PROTOCOL_RETAINED_IDS.get(descriptor)
    if identity is None:
        raise AdapterError("Unrecorded protocol descriptor", "access")
    journal_descriptor(descriptor, identity, directory)
    if unlock:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        journal_descriptor(descriptor, identity, directory)
    journal_close_descriptor(descriptor, identity, directory)
    _PROTOCOL_RETAINED_IDS.pop(descriptor, None)


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


def protocol_read_original(parent, name, descriptor):
    """Read initial compatibility bytes through their acquired identity."""
    try:
        expected = _PROTOCOL_RETAINED_IDS[descriptor]
        before = journal_descriptor(descriptor, expected)
        parent_id = _PROTOCOL_RETAINED_IDS[parent]
        journal_descriptor(parent, parent_id, True)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (named.st_dev, named.st_ino) != expected:
            raise AdapterError("Initial state path changed", "access")
        raw = os.pread(descriptor, 16385, 0)
        after = journal_descriptor(descriptor, expected)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        journal_descriptor(parent, parent_id, True)
        if (
            (named.st_dev, named.st_ino) != expected
            or journal_version(before) != journal_version(after)
            or len(raw) > 16384
        ):
            raise AdapterError("Initial state generation changed", "access")
        _PROTOCOL_RETAINED_VERSIONS[descriptor] = journal_version(after)
        return raw
    except BaseException:
        journal_failure()
        raise


_PROTOCOL_RETAINED_VERSIONS = {}
_PROTOCOL_EXPECTED_GENERATIONS = {}


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


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "source": SOURCE_ID,
                    "status": "fail",
                    "last_attempt_at": utc_now(),
                    "message": "Run could not complete; "
                    "durable status unavailable",
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
