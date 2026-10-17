"""Discover and collect current public INSAIT and EXPLORER opportunities."""

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
import xml.etree.ElementTree as ET

SOURCE_ID = "bg-insait"
SOURCE_URL = "https://insait.ai/insait-opens-applications-for-surf-2026-summer-research-internship-program/"
WEBSITE_URL = "https://insait.ai/surf/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "BG"
PUBLISHER_TYPE = "university"
ATTRIBUTION = "INSAIT at Sofia University https://insait.ai/ and its EXPLORER programme https://explorer.insait.ai/ with DeepMind for the named scholarship"

_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {
    "scholarships", "grants", "internships", "jobs", "training", "fellowships",
    "other",
}
FAMILY = "youthopps-insait-publisher-v1"
FAMILY_SOURCES = {"bg-insait"}
COLLECTION_TIMEOUT = 4200
RUN_TIMEOUT = 4590
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 3
_ARTIFACT_NAME = "insait-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
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
        self.attrs = dict(attrs)
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
    if (
        _STATIC_TOKEN_END is not None and _PHASE_NAME != "export"
        and time.monotonic() >= _STATIC_TOKEN_END
    ):
        raise PhaseExpired()
    if _PHASE_END is None or _RUN_END is None:
        raise AdapterError("Bounded execution context required", "access")
    if time.monotonic() >= min(_PHASE_END, _RUN_END):
        raise PhaseExpired()


def request_timeout(publisher=False):
    check_deadline()
    remaining = min(_PHASE_END, _RUN_END) - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    if _STATIC_TOKEN_END is not None and _PHASE_NAME != "export":
        remaining = min(remaining, _STATIC_TOKEN_END - time.monotonic())
    return min(60 if publisher else 15, remaining)


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    if _STATIC_TOKEN_END is not None and _PHASE_NAME != "export":
        deadline = min(deadline, _STATIC_TOKEN_END)
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


@contextlib.contextmanager
def publisher_attempt(publisher):
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
    global _PUBLISHER_INVENTORY_COMPLETED_MONOTONIC
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
        if _PROTOCOL_PHYSICAL_INVENTORY:
            _PUBLISHER_INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
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


def protocol_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


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
    now = time.time()
    # Only points strictly outside the rolling window can be folded here.
    # Every numeric hold/completion/unknown remains independently joined.
    retained = [point for point in union if point >= now - 60]
    if len(retained) > 10:
        raise AdapterError("Unaged timing union exceeds original cap", "access")
    result["starts"] = retained
    for key in ["policy_allowed", "policy_denied"]:
        denied = key == "policy_denied"
        for target, packed in right[key].items():
            witness = protocol_history_unpack(target, packed, denied)
            current = result[key].get(target)
            if denied and current is not None and (
                protocol_history_unpack(target, current, True)["origin"]
                != witness["origin"]
            ):
                raise AdapterError("Conflicting scoped policy origin", "access")
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
    if not _PROTOCOL_READY or _GUARD_FATAL or _PROTOCOL_RATE_STOP:
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
        or obj.get("path") != ".github/workflows/fetch-bg-insait.yml"
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
        != ".github/workflows/fetch-bg-insait.yml"
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
                "fetch-bg-insait.yml@refs/heads/main"
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


def protocol_policy_parser(text, body_sha256):
    """Use native rule matching without parsing unrelated timing numbers."""
    import urllib.robotparser

    parser = urllib.robotparser.RobotFileParser()
    groups = protocol_policy_groups(text)
    parser._protocol_groups = groups
    parser._protocol_body_sha256 = body_sha256
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
        hashlib.sha256(policy["text"].encode()).hexdigest()
        != policy["text_sha256"]
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
        if not policy["available"]
        or urllib.parse.urlsplit(url).path == "/robots.txt"
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
            "Publisher robots excludes required INSAIT input", "access"
        )
    return receipt


def protocol_source_hash(head):
    if not re.fullmatch(r"[0-9a-f]{40}", str(head)):
        raise AdapterError("Invalid immutable producer source ref", "access")
    result = workflow_api("/contents/adapters/bg-insait/adapter.py?ref=" + head)
    if (
        not isinstance(result, dict)
        or result.get("type") != "file"
        or result.get("path") != "adapters/bg-insait/adapter.py"
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
        if descriptor is not None:
            if identity is None:
                # Only this fresh local open lacks its first fstat identity.
                # Preserve the primary acquisition error during rollback.
                with contextlib.suppress(BaseException):
                    os.close(descriptor)
            else:
                journal_close(descriptor, identity, True)
        raise


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
    if _STATIC_TOKEN_END is not None:
        if os.environ.get("GITHUB_ACTIONS") == "true" or (
            time.monotonic() + 120 >= _STATIC_TOKEN_END
        ):
            raise AdapterError(
                "External static-token budget exhausted",
                "publish",
            )
        return os.environ["DATA_SOURCE_TOKEN"]
    """Renew a repository-scoped installation token before expiry/CAS."""
    global _APP_TOKEN, _APP_EXPIRY, _APP_CONFIGURATION
    if (
        not re.fullmatch(r"[1-9][0-9]{0,19}",
                         os.environ.get("WEBSITE_APP_ID", ""))
        or not re.fullmatch(r"[1-9][0-9]{0,19}",
                            os.environ.get("WEBSITE_APP_INSTALLATION_ID", ""))
        or not os.environ.get("WEBSITE_APP_PRIVATE_KEY")
    ):
        raise AdapterError(
            "Complete GitHub App configuration required",
            "publish",
        )
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

_REVIEWED_LOCAL_ABSENCE_TRANSITION = {
    'schema': 1,
    'source': 'bg-insait',
    'family': 'youthopps-insait-publisher-v1',
    'purpose': 'PROSPECTIVE_FIRST_LOCAL_INITIALIZATION',
    'reviewed_core_sha256': (
        'd9ee1f3e031aa2b89f34bb754f27656c28e6b690574e742cea503ea51744d269'
    ),
    'owner': {
        'run_id': 38763419209,
        'run_attempt': 1,
        'job_id': 116520330173,
        'job_sha256': (
            '5be7ae5756143f9224db521f17c7b6b60679c2c4d8de7eaf7f6441f478b52d1b'
        ),
        'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
        'artifact_id': 11987052980,
        'artifact_name': 'insait-pacing-state-1',
        'artifact_sha256': (
            '3177f08d2a3d48b77d1ca6039acee19498aa0c6dc5da887a3e6c51668b351c13'
        ),
        'artifact_bytes': 407,
        'producer_sha256': (
            '0f8429b590c67c64c7df292cfe38a2d0d1268021b4b0f9354484e9a25e730734'
        ),
    },
    'history': {
        'sha256': (
            '3e9135fdd3c2a1db0e927240bd19c6f0200d831e312cad8a82e0ed39566d6100'
        ),
        'run_projection': [
            [
                38034262485,
                1,
                'd825752f5226893d72979344305b7a80a552a71a',
                'completed',
                'success',
            ],
            [
                38034266496,
                1,
                'd825752f5226893d72979344305b7a80a552a71a',
                'completed',
                'success',
            ],
            [
                38393947129,
                1,
                '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                'completed',
                'failure',
            ],
            [
                38507918473,
                1,
                '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                'completed',
                'success',
            ],
            [
                38536227296,
                1,
                '92235b48ad33b9fa377e0736b689010173d7bfab',
                'completed',
                'success',
            ],
            [
                38684236003,
                1,
                '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                'completed',
                'success',
            ],
            [
                38720083910,
                1,
                'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                'completed',
                'success',
            ],
            [
                38721259483,
                1,
                'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                'completed',
                'success',
            ],
            [
                38763419209,
                1,
                'f77eed642c4544bbeed009440c406064a4f5c6ad',
                'completed',
                'success',
            ],
        ],
        'completion_bounds': [
            1791617276.0,
            1791617538.0,
            1791892934.0,
            1791949894.0,
            1791967287.0,
            1792040613.0,
            1792060298.0,
            1792060970.0,
            1792079651.0,
        ],
    },
    'owned_state': {
        'schema': 1,
        'canonical_sha256': (
            '08ada253601a94874c415e099b67d84ea3ec3f9700dc9e275cc0724ea467b080'
        ),
        'member_sha256': (
            '2fda9a11b4abd07d3aa8fac0f4ee4eb7b7d61e1293399320ee38de6ab0269cb1'
        ),
        'member_bytes': 450,
        'member_RAM_ACK': {
            'event_index': 45,
            'sha256': (
                'e6424ae78cac58f08b366bd3b243c8c8582fb6fe7f21a3c1ca1bb4c9f1'
                'e61542'
            ),
            'bytes': 713,
        },
        'decoded_RAM_ACK': {
            'event_index': 46,
            'sha256': (
                'ff035b11970b02bd0509a65c1d735919267139c241d5f8d94d053fb5aa'
                '47fea9'
            ),
            'bytes': 620,
        },
        'outer_has_head_or_adapter_hash': False,
    },
    'seed': {
        'blocked': False,
        'completed_at': 1792079651.0,
        'family': 'youthopps-insait-publisher-v1',
        'flight_id': None,
        'in_flight': False,
        'interval': 12.2,
        'inventory_completed_at': 1792079651.0,
        'last_policy_receipt': None,
        'last_settled_owner': None,
        'legacy_completion_history_unknown': True,
        'legacy_policy_history_unknown': True,
        'legacy_rate_history_unknown': True,
        'not_before': 1792154750.0255497,
        'observed_at': 1792154690.0255497,
        'page_interval': 30,
        'policy_allowed': {
        },
        'policy_denied': {
        },
        'policy_pending': None,
        'rate_limit_count': 0,
        'rate_limit_until': 0,
        'schema': 3,
        'starts': [],
        'transition_id': None,
    },
    'seed_sha256': (
        '79bab972f66c2ec1eabaf0ac41807dcae59ba9bf2ffdca7668b1c8ed4451cdf2'
    ),
    'age_fold': {
        'cutoff': 1792154630.0255497,
        'observation_at': 1792154690.0255497,
        'full_typed_ledger': [
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079588.541302,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079594.5414298,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079600.5415626,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079606.541675,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079612.5418172,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079618.5419636,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079624.5421095,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079630.542246,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079636.5423825,
            },
            {
                'kind': 'owned_legacy_retained_start',
                'at': 1792079642.5425072,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1791617276.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1791617538.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1791892934.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1791949894.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1791967287.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1792040613.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1792060298.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1792060970.0,
            },
            {
                'kind': 'exact_actual_job_completion_upper_bound',
                'at': 1792079651.0,
            },
        ],
        'full_unique_points': [
            1791617276.0,
            1791617538.0,
            1791892934.0,
            1791949894.0,
            1791967287.0,
            1792040613.0,
            1792060298.0,
            1792060970.0,
            1792079588.541302,
            1792079594.5414298,
            1792079600.5415626,
            1792079606.541675,
            1792079612.5418172,
            1792079618.5419636,
            1792079624.5421095,
            1792079630.542246,
            1792079636.5423825,
            1792079642.5425072,
            1792079651.0,
        ],
        'removed_points': [
            1791617276.0,
            1791617538.0,
            1791892934.0,
            1791949894.0,
            1791967287.0,
            1792040613.0,
            1792060298.0,
            1792060970.0,
            1792079588.541302,
            1792079594.5414298,
            1792079600.5415626,
            1792079606.541675,
            1792079612.5418172,
            1792079618.5419636,
            1792079624.5421095,
            1792079630.542246,
            1792079636.5423825,
            1792079642.5425072,
            1792079651.0,
        ],
        'retained_points': [],
        'all_removed_strictly_before_cutoff': True,
        'full_ledger_sha256': (
            'd33514266b212692dc53dc9238bbc80d8635d57ef15396d571840af2230b9993'
        ),
    },
    'minimum_actual_initialization_clock': 1792154690.0255497,
    'root_owned_receipt_sha256': (
        '40b4a0c0947788faa4965c78c6c91a64359d6343cde37a0c38c0304e917b7126'
    ),
    'prior_absence_observation_sha256': (
        '8a69e6c6497d345e940e0c8d21a3051844d954c2d22686510ddc33250b18f883'
    ),
    'evidence': (
        'https://github.com/YouthOpps/data-pipeline/issues/336#issuecomment'
        '-6216672353'
    ),
    'absence': {
        'uid': 1000,
        'root': {
            'dev': 27,
            'ino': 524289,
            'uid': 65534,
            'mode': 493,
        },
        'temporary_name': 'tmp',
        'temporary': {
            'dev': 34,
            'ino': 1,
            'uid': 65534,
            'mode': 1023,
        },
        'family_name': 'youthopps-insait-publisher-v1-1000',
        'family_absent': True,
        'claim_absent': True,
        'observed_at': 1792157537.9010937,
        'observation_sha256': (
            '08490163548d4e4adb653f5669d39bdae02d7d9ea63c531462d618ea6b31da97'
        ),
    },
    'once_claim': {
        'nonce': (
            '7c4aea444631f42dd07b1935d663142d9a12c7cd646d0cc46f1bef9386b68955'
        ),
        'name': (
            'youthopps-insait-publisher-v1-local-once-1000-7c4aea444631f42d'
            'd07b1935d663142d9a12c7cd646d0cc46f1bef9386b68955.json'
        ),
        'purpose': 'PROSPECTIVE_FIRST_LOCAL_INITIALIZATION',
        'observation_sha256': (
            '08490163548d4e4adb653f5669d39bdae02d7d9ea63c531462d618ea6b31da97'
        ),
    },
    'legacy_unknowns_acknowledged': True,
    'prospective_false_owner_acknowledged': True,
    'age_fold_acknowledged': True,
}
_PROTOCOL_ABSENCE_HANDLES = []
_PROTOCOL_ABSENCE_FAMILY = None
_PROTOCOL_ABSENCE_CLAIM_RAW = None
_PROTOCOL_ABSENCE_BASELINE = json.loads(
    '{"schema":1,"source":"bg-insait","family":"youthopps-insait-publ'
    'isher-v1","purpose":"PROSPECTIVE_FIRST_LOCAL_INITIALIZATION","re'
    'viewed_core_sha256":"d9ee1f3e031aa2b89f34bb754f27656c28e6b690574'
    'e742cea503ea51744d269","owner":{"run_id":38763419209,"run_attemp'
    't":1,"job_id":116520330173,"job_sha256":"5be7ae5756143f9224db521'
    'f17c7b6b60679c2c4d8de7eaf7f6441f478b52d1b","head_sha":"f77eed642'
    'c4544bbeed009440c406064a4f5c6ad","artifact_id":11987052980,"arti'
    'fact_name":"insait-pacing-state-1","artifact_sha256":"3177f08d2a'
    '3d48b77d1ca6039acee19498aa0c6dc5da887a3e6c51668b351c13","artifac'
    't_bytes":407,"producer_sha256":"0f8429b590c67c64c7df292cfe38a2d0'
    'd1268021b4b0f9354484e9a25e730734"},"history":{"sha256":"3e9135fd'
    'd3c2a1db0e927240bd19c6f0200d831e312cad8a82e0ed39566d6100","run_p'
    'rojection":[[38034262485,1,"d825752f5226893d72979344305b7a80a552'
    'a71a","completed","success"],[38034266496,1,"d825752f5226893d729'
    '79344305b7a80a552a71a","completed","success"],[38393947129,1,"5d'
    'ff04768f0e558b4d301d5677c5a2c433c3a64b","completed","failure"],['
    '38507918473,1,"02792bda730b08eeb1eda86a2cd71af80af3cef7","comple'
    'ted","success"],[38536227296,1,"92235b48ad33b9fa377e0736b6890101'
    '73d7bfab","completed","success"],[38684236003,1,"9b97e13c2e2a56e'
    'e77700e40202eb6acd2212cf5","completed","success"],[38720083910,1'
    ',"a62a88f5cad62f4fb1ef5230358557d37d2b2bba","completed","success'
    '"],[38721259483,1,"cb14a94366e7c9f3a518a6bd9f8a3da5331da39f","co'
    'mpleted","success"],[38763419209,1,"f77eed642c4544bbeed009440c40'
    '6064a4f5c6ad","completed","success"]],"completion_bounds":[17916'
    '17276.0,1791617538.0,1791892934.0,1791949894.0,1791967287.0,1792'
    '040613.0,1792060298.0,1792060970.0,1792079651.0]},"owned_state":'
    '{"schema":1,"canonical_sha256":"08ada253601a94874c415e099b67d84e'
    'a3ec3f9700dc9e275cc0724ea467b080","member_sha256":"2fda9a11b4abd'
    '07d3aa8fac0f4ee4eb7b7d61e1293399320ee38de6ab0269cb1","member_byt'
    'es":450,"member_RAM_ACK":{"event_index":45,"sha256":"e6424ae78ca'
    'c58f08b366bd3b243c8c8582fb6fe7f21a3c1ca1bb4c9f1e61542","bytes":7'
    '13},"decoded_RAM_ACK":{"event_index":46,"sha256":"ff035b11970b02'
    'bd0509a65c1d735919267139c241d5f8d94d053fb5aa47fea9","bytes":620}'
    ',"outer_has_head_or_adapter_hash":false},"seed":{"blocked":false'
    ',"completed_at":1792079651.0,"family":"youthopps-insait-publishe'
    'r-v1","flight_id":null,"in_flight":false,"interval":12.2,"invent'
    'ory_completed_at":1792079651.0,"last_policy_receipt":null,"last_'
    'settled_owner":null,"legacy_completion_history_unknown":true,"le'
    'gacy_policy_history_unknown":true,"legacy_rate_history_unknown":'
    'true,"not_before":1792154750.0255497,"observed_at":1792154690.02'
    '55497,"page_interval":30,"policy_allowed":{},"policy_denied":{},'
    '"policy_pending":null,"rate_limit_count":0,"rate_limit_until":0,'
    '"schema":3,"starts":[],"transition_id":null},"seed_sha256":"79ba'
    'b972f66c2ec1eabaf0ac41807dcae59ba9bf2ffdca7668b1c8ed4451cdf2","a'
    'ge_fold":{"cutoff":1792154630.0255497,"observation_at":179215469'
    '0.0255497,"full_typed_ledger":[{"kind":"owned_legacy_retained_st'
    'art","at":1792079588.541302},{"kind":"owned_legacy_retained_star'
    't","at":1792079594.5414298},{"kind":"owned_legacy_retained_start'
    '","at":1792079600.5415626},{"kind":"owned_legacy_retained_start"'
    ',"at":1792079606.541675},{"kind":"owned_legacy_retained_start","'
    'at":1792079612.5418172},{"kind":"owned_legacy_retained_start","a'
    't":1792079618.5419636},{"kind":"owned_legacy_retained_start","at'
    '":1792079624.5421095},{"kind":"owned_legacy_retained_start","at"'
    ':1792079630.542246},{"kind":"owned_legacy_retained_start","at":1'
    '792079636.5423825},{"kind":"owned_legacy_retained_start","at":17'
    '92079642.5425072},{"kind":"exact_actual_job_completion_upper_bou'
    'nd","at":1791617276.0},{"kind":"exact_actual_job_completion_uppe'
    'r_bound","at":1791617538.0},{"kind":"exact_actual_job_completion'
    '_upper_bound","at":1791892934.0},{"kind":"exact_actual_job_compl'
    'etion_upper_bound","at":1791949894.0},{"kind":"exact_actual_job_'
    'completion_upper_bound","at":1791967287.0},{"kind":"exact_actual'
    '_job_completion_upper_bound","at":1792040613.0},{"kind":"exact_a'
    'ctual_job_completion_upper_bound","at":1792060298.0},{"kind":"ex'
    'act_actual_job_completion_upper_bound","at":1792060970.0},{"kind'
    '":"exact_actual_job_completion_upper_bound","at":1792079651.0}],'
    '"full_unique_points":[1791617276.0,1791617538.0,1791892934.0,179'
    '1949894.0,1791967287.0,1792040613.0,1792060298.0,1792060970.0,17'
    '92079588.541302,1792079594.5414298,1792079600.5415626,1792079606'
    '.541675,1792079612.5418172,1792079618.5419636,1792079624.5421095'
    ',1792079630.542246,1792079636.5423825,1792079642.5425072,1792079'
    '651.0],"removed_points":[1791617276.0,1791617538.0,1791892934.0,'
    '1791949894.0,1791967287.0,1792040613.0,1792060298.0,1792060970.0'
    ',1792079588.541302,1792079594.5414298,1792079600.5415626,1792079'
    '606.541675,1792079612.5418172,1792079618.5419636,1792079624.5421'
    '095,1792079630.542246,1792079636.5423825,1792079642.5425072,1792'
    '079651.0],"retained_points":[],"all_removed_strictly_before_cuto'
    'ff":true,"full_ledger_sha256":"d33514266b212692dc53dc9238bbc80d8'
    '635d57ef15396d571840af2230b9993"},"minimum_actual_initialization'
    '_clock":1792154690.0255497,"root_owned_receipt_sha256":"40b4a0c0'
    '947788faa4965c78c6c91a64359d6343cde37a0c38c0304e917b7126","prior'
    '_absence_observation_sha256":"8a69e6c6497d345e940e0c8d21a3051844'
    'd954c2d22686510ddc33250b18f883"}'
)

_REVIEWED_LOCAL_TRANSITION = None
_REVIEWED_HOSTED_TRANSITION = {'age_fold': {'cutoff': 1792178799.1337228,
              'purpose': 'EXPLICIT_LEGACY_AGE_FOLD',
              'timings': [1791617276.0,
                          1791617538.0,
                          1791892934.0,
                          1791949894.0,
                          1791967287.0,
                          1792040613.0,
                          1792060298.0,
                          1792060970.0,
                          1792079588.541302,
                          1792079594.5414298,
                          1792079600.5415626,
                          1792079606.541675,
                          1792079612.5418172,
                          1792079618.5419636,
                          1792079624.5421095,
                          1792079630.542246,
                          1792079636.5423825,
                          1792079642.5425072,
                          1792079651.0]},
 'artifact_digest': 'sha256:3177f08d2a3d48b77d1ca6039acee19498aa0c6dc5da887a3e6c51668b351c13',
 'artifact_id': 11987052980,
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/336#issuecomment-6223222979',
 'history_sha256': '3e9135fdd3c2a1db0e927240bd19c6f0200d831e312cad8a82e0ed39566d6100',
 'legacy_state_sha256': '08ada253601a94874c415e099b67d84ea3ec3f9700dc9e275cc0724ea467b080',
 'legacy_unknowns_acknowledged': True,
 'local_pins': None,
 'producer_sha256': '0f8429b590c67c64c7df292cfe38a2d0d1268021b4b0f9354484e9a25e730734',
 'purpose': 'PROSPECTIVE_SCHEMA1_TO3_TRANSITION',
 'seed_sha256': 'd7b8009889e399f110419eb2719657affe44e778734f467da546576e96af4b32'}
_REVIEWED_LEGACY_SEED = {'blocked': False,
 'completed_at': 1792174127.0077496,
 'family': 'youthopps-insait-publisher-v1',
 'flight_id': None,
 'in_flight': False,
 'interval': 12.2,
 'inventory_completed_at': 1792173752.7099524,
 'last_policy_receipt': None,
 'last_settled_owner': None,
 'legacy_completion_history_unknown': True,
 'legacy_policy_history_unknown': True,
 'legacy_rate_history_unknown': True,
 'not_before': 1792178919.1337228,
 'observed_at': 1792178859.1337228,
 'page_interval': 30,
 'policy_allowed': {'https://explorer.insait.ai': ['00ba97e9219e09307b3c05b5ff1d4685',
                                                   'e3a0d919d4c5178b383c2c49cfee9cb0a502c1290cad5fc6aa2beb6f9114a913',
                                                   '6728b0a7899a32028b1bd8fc9e1af82ecac160d34e1773b20b0c39acafb0f16c',
                                                   True,
                                                   True,
                                                   0,
                                                   None],
                    'https://insait.ai': ['05aacf594d1ce64f7f803ca38ff4babd',
                                          '1eb5105cea9c3124a98ad7befe97aed4ff386f9836192c812a253f6fc2ea41af',
                                          'a4729c12bb2f503fad30e478cfa18ad77edd929bff1d30cd60cbd25f39825656',
                                          True,
                                          True,
                                          0,
                                          None]},
 'policy_denied': {},
 'policy_pending': None,
 'rate_limit_count': 0,
 'rate_limit_until': 0,
 'schema': 3,
 'starts': [],
 'transition_id': None}
_PROTOCOL_WORKFLOW_ID = 378390055
_PROTOCOL_REPOSITORY_ID = 1163601208
_PROTOCOL_READY = _PROTOCOL_FIRST_WIRE = False
_PROTOCOL_RATE_STOP = False
_PROTOCOL_CONTEXT = _PROTOCOL_OWNER = _PROTOCOL_EXPECTED_RECEIPT = None
_PROTOCOL_DEPTH = _PROTOCOL_INVENTORY_DEPTH = 0
_PROTOCOL_PHYSICAL_INVENTORY = False
_PROTOCOL_POLICY_RAW = {}
_PROTOCOL_FETCHING_REP = set()
_PROTOCOL_SEQUENCE = 0
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INVENTORY_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_APP_TOKEN = None
_APP_EXPIRY = 0
_APP_CONFIGURATION = None
_STATIC_TOKEN_END = None


class AssessedStop(AdapterError):
    """A durable, fully assessed refusal; never a transport failure."""


def protocol_origins():
    return {"https://insait.ai", "https://explorer.insait.ai"}


def protocol_collection_call(function):
    def call(*args, **kwargs):
        with protocol_transaction("logical-input"):
            return function(*args, **kwargs)
    return call


def protocol_transport(function):
    def call(*args, **kwargs):
        if kwargs.get("publisher", args[4] if len(args) > 4 else False):
            with protocol_transaction("physical-input"):
                return function(*args, **kwargs)
        return function(*args, **kwargs)
    return call


def protocol_hosted_identity():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise AdapterError("Owned export requires hosted context", "access")
    return tuple(map(int, [
        os.environ.get("GITHUB_RUN_ID", ""),
        os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    ]))


def protocol_legacy_state(state):
    fields = {
        "schema", "family", "observed_at", "not_before", "starts", "blocked"
    }
    if (
        not isinstance(state, dict) or set(state) != fields
        or type(state["schema"]) is not int or state["schema"] != 1
        or state["family"] != FAMILY
        or not protocol_number(state["observed_at"])
        or not protocol_number(state["not_before"])
        or state["observed_at"] > time.time() + 1
        or type(state["blocked"]) is not bool
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 10
        or any(not protocol_number(t) or t > state["observed_at"]
               for t in state["starts"])
        or state["starts"] != sorted(set(state["starts"]))
    ):
        raise AdapterError("Invalid exact legacy state", "access")
    return state


def protocol_marker(context):
    marker = (_REVIEWED_HOSTED_TRANSITION if context["hosted"]
              else _REVIEWED_LOCAL_TRANSITION)
    if (
        not isinstance(marker, dict)
        or set(marker) != {
            "evidence", "history_sha256", "artifact_id", "artifact_digest",
            "producer_sha256", "legacy_state_sha256", "local_pins",
            "seed_sha256", "legacy_unknowns_acknowledged",
            "age_fold", "purpose",
        }
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/336"
            r"#issuecomment-[1-9][0-9]*", str(marker.get("evidence"))
        )
        or marker["purpose"] != "PROSPECTIVE_SCHEMA1_TO3_TRANSITION"
        or marker["legacy_unknowns_acknowledged"] is not True
        or marker["history_sha256"] != context["history_sha256"]
        or marker["artifact_id"] != context["artifact"]["id"]
        or marker["artifact_digest"] != context["artifact"]["digest"]
        or marker["producer_sha256"] != context["source_sha256"]
        or marker["seed_sha256"] != protocol_digest(_REVIEWED_LEGACY_SEED)
    ):
        raise AdapterError("Exact public legacy boundary required", "access")
    seed = validate_budget(_REVIEWED_LEGACY_SEED)
    if seed["in_flight"] or any(not seed[k] for k in [
        "legacy_rate_history_unknown", "legacy_policy_history_unknown",
        "legacy_completion_history_unknown",
    ]):
        raise AdapterError("Legacy pending absence is not settlement", "access")
    if context["hosted"] and marker["local_pins"] is not None:
        raise AdapterError(
            "LOCAL identity cannot authorize hosted state",
            "access",
        )
    return marker


def protocol_legacy_constraints(seed, incoming, local, context, marker):
    """Require an explicit lossless ledger and separately approved age fold."""
    old = [incoming] + ([local] if local is not None else [])
    known = sorted(set(context["completion_bounds"] + [
        point for value in old for point in value["starts"]
    ]))
    fold = marker["age_fold"]
    if (
        not isinstance(fold, dict)
        or set(fold) != {"cutoff", "timings", "purpose"}
        or fold["purpose"] != "EXPLICIT_LEGACY_AGE_FOLD"
        or not protocol_number(fold["cutoff"])
        or fold["cutoff"] > time.time() - 60
        or fold["timings"] != known
    ):
        raise AdapterError(
            "Complete approved legacy timing ledger required",
            "access",
        )
    for value in old:
        protocol_legacy_state(value)
        if value["blocked"] and not seed["blocked"]:
            raise AdapterError("Seed weakens retained content hold", "access")
        if seed["not_before"] < value["not_before"]:
            raise AdapterError("Seed weakens retained embargo", "access")
        if seed["observed_at"] < value["observed_at"]:
            raise AdapterError("Seed misses a legacy observation", "access")
    if any(point not in seed["starts"] and point >= fold["cutoff"]
           for point in known):
        raise AdapterError("Unaged legacy timing cannot be dropped", "access")


def protocol_check_local_pins(pins, raw):
    if not isinstance(pins, dict) or set(pins) != {
        "parent", "state", "state_lock", "collection_lock", "raw_sha256"
    }:
        raise AdapterError("Exact mature LOCAL pins required", "access")
    actual = {
        "parent": list(_GUARD_PARENT_ID),
        "state": list(_GUARD_ID),
        "state_lock": list(_GUARD_LOCK_ID),
        "collection_lock": list(_GUARD_COLLECTION_ID),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if actual != pins:
        raise AdapterError("Mature LOCAL pins changed", "access")


def protocol_absence_marker():
    """Validate the complete finite grant before metadata or path access."""
    marker = _REVIEWED_LOCAL_ABSENCE_TRANSITION
    baseline = _PROTOCOL_ABSENCE_BASELINE
    missing = {
        "evidence", "absence", "once_claim", "legacy_unknowns_acknowledged",
        "prospective_false_owner_acknowledged", "age_fold_acknowledged",
    }
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or not isinstance(marker, dict)
        or set(marker) != set(baseline) | missing
        or any(protocol_digest(marker[k]) != protocol_digest(v)
               for k, v in baseline.items())
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/336"
            r"#issuecomment-[1-9][0-9]*", str(marker["evidence"])
        )
        or any(marker[k] is not True for k in missing - {
            "evidence", "absence", "once_claim"
        })
    ):
        raise AdapterError("Finite LOCAL absence grant required", "access")
    absence, claim = marker["absence"], marker["once_claim"]
    if (
        not isinstance(absence, dict)
        or set(absence) != {
            "observation_sha256", "observed_at", "uid", "root",
            "temporary_name", "temporary", "family_name", "family_absent",
            "claim_absent",
        }
        or not isinstance(claim, dict)
        or set(claim) != {"nonce", "name", "purpose", "observation_sha256"}
        or type(absence["uid"]) is not int
        or absence["uid"] != os.getuid()
        or absence["temporary_name"] != "tmp"
        or absence["family_name"] != FAMILY + "-" + str(os.getuid())
        or absence["family_absent"] is not True
        or absence["claim_absent"] is not True
        or not re.fullmatch(r"[0-9a-f]{64}", str(claim["nonce"]))
        or claim["name"] != (
            FAMILY + "-local-once-" + str(os.getuid())
            + "-" + claim["nonce"] + ".json"
        )
        or claim["purpose"] != marker["purpose"]
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(absence["observation_sha256"])
        )
        or claim["observation_sha256"] != absence["observation_sha256"]
        or not protocol_number(absence["observed_at"])
        or not marker["minimum_actual_initialization_clock"]
        <= absence["observed_at"] <= time.time()
        or any(os.environ.get(k, "/tmp") != "/tmp"
               for k in ("TMPDIR", "TEMP", "TMP"))
        or tempfile.gettempdir() != "/tmp"
    ):
        raise AdapterError("Incomplete fresh absence/claim boundary", "access")
    for name in ("root", "temporary"):
        pin = absence[name]
        if (
            not isinstance(pin, dict)
            or set(pin) != {"dev", "ino", "uid", "mode"}
            or any(type(v) is not int for v in pin.values())
            or min(pin["dev"], pin["ino"]) <= 0
            or pin["uid"] < 0
            or not 0 <= pin["mode"] <= 0o7777
            or name == "temporary" and pin["mode"] != 0o1777
            or name == "root" and pin["mode"] & 0o022
        ):
            raise AdapterError("Exact trusted ancestor pins required", "access")
    validate_budget(marker["seed"])
    return marker


def protocol_absence_context(marker, context, incoming=None):
    """Require every authentic owner/history pin, never an older fallback."""
    owner, history = marker["owner"], marker["history"]
    latest, artifact = context["latest"], context["artifact"]
    if (
        context["hosted"] or context["current"] is not None
        or context["history_sha256"] != history["sha256"]
        or protocol_digest(context["run_projection"])
        != protocol_digest(history["run_projection"])
        or context["completion_bounds"] != history["completion_bounds"]
        or any(latest[k] != owner[v] for k, v in [
            ("id", "run_id"), ("attempt", "run_attempt"),
            ("job_id", "job_id"), ("job_sha256", "job_sha256"),
            ("head", "head_sha"),
        ])
        or artifact["id"] != owner["artifact_id"]
        or artifact["name"] != owner["artifact_name"]
        or artifact["digest"] != "sha256:" + owner["artifact_sha256"]
        or artifact["size_in_bytes"] != owner["artifact_bytes"]
        or protocol_source_hash(latest["head"]) != owner["producer_sha256"]
    ):
        raise AdapterError("Finite absence owner/history changed", "access")
    if incoming is not None:
        protocol_legacy_state(incoming)
        if protocol_digest(incoming) != marker["owned_state"][
            "canonical_sha256"
        ]:
            raise AdapterError("Authentic legacy state changed", "access")
        ledger = [
            {"kind": "owned_legacy_retained_start", "at": point}
            for point in incoming["starts"]
        ] + [
            {"kind": "exact_actual_job_completion_upper_bound", "at": point}
            for point in context["completion_bounds"]
        ]
        fold = marker["age_fold"]
        points = sorted({entry["at"] for entry in ledger})
        if (
            ledger != fold["full_typed_ledger"]
            or protocol_digest(ledger) != fold["full_ledger_sha256"]
            or points != fold["full_unique_points"]
            or fold["removed_points"] != points
            or fold["retained_points"] != []
            or fold["cutoff"] != fold["observation_at"] - 60
            or fold["all_removed_strictly_before_cutoff"] is not True
            or any(t >= fold["cutoff"] for t in points)
            or fold["cutoff"] > time.time() - 60
            or incoming["blocked"] and not marker["seed"]["blocked"]
            or marker["seed"]["not_before"] < incoming["not_before"]
            or marker["seed"]["observed_at"] < incoming["observed_at"]
        ):
            raise AdapterError("Lossless explicit age fold required", "access")


def protocol_absence_directory(descriptor, pin):
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode))
        != (pin["dev"], pin["ino"], pin["uid"], pin["mode"])
    ):
        raise AdapterError("Trusted absence ancestor changed", "access")


def protocol_absence_verify(family=False, claim=False):
    """Retain exact original ancestor and claim authority through first wire."""
    marker = _REVIEWED_LOCAL_ABSENCE_TRANSITION
    root, temporary, acquired_claim = _PROTOCOL_ABSENCE_HANDLES
    absence = marker["absence"]
    for entry, pin in ((root, absence["root"]),
                       (temporary, absence["temporary"])):
        protocol_absence_directory(entry[0], pin)
    named_root = os.stat("/", follow_symlinks=False)
    named_tmp = os.stat("tmp", dir_fd=root[0], follow_symlinks=False)
    if (
        (named_root.st_dev, named_root.st_ino) != root[1]
        or (named_tmp.st_dev, named_tmp.st_ino) != temporary[1]
    ):
        raise AdapterError("Current trusted ancestor changed", "access")
    for name, expected in [
        (absence["family_name"], _PROTOCOL_ABSENCE_FAMILY if family else None),
        (marker["once_claim"]["name"], acquired_claim[1] if claim else None),
    ]:
        try:
            current = os.stat(name, dir_fd=temporary[0], follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if expected is None:
            if current is not None:
                raise AdapterError(
                    "Finite absence path already exists", "access"
                )
        elif current is None or (current.st_dev, current.st_ino) != expected:
            raise AdapterError("Finite initialization path changed", "access")
    if claim:
        descriptor, identity = acquired_claim
        before = journal_owned(descriptor, identity)
        raw = os.pread(descriptor, 4097, 0)
        after = journal_owned(descriptor, identity)
        if (
            raw != _PROTOCOL_ABSENCE_CLAIM_RAW
            or journal_generation(before) != journal_generation(after)
        ):
            raise AdapterError("Durable once claim changed", "access")
    if family:
        journal_parent()
        if _GUARD_PARENT_ID != _PROTOCOL_ABSENCE_FAMILY:
            raise AdapterError("Exclusive family claim changed", "access")


def protocol_absence_claim(marker):
    global _PROTOCOL_ABSENCE_HANDLES, _PROTOCOL_ABSENCE_CLAIM_RAW
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    for path, pin in (("/", marker["absence"]["root"]),
                      ("/tmp", marker["absence"]["temporary"])):
        descriptor = os.open(path, flags)
        try:
            info = os.fstat(descriptor)
        except BaseException:
            # Only this fresh local descriptor lacks its first identity.
            with contextlib.suppress(BaseException):
                os.close(descriptor)
            raise
        _PROTOCOL_ABSENCE_HANDLES.append(
            (descriptor, (info.st_dev, info.st_ino))
        )
        protocol_absence_directory(descriptor, pin)
    _PROTOCOL_ABSENCE_HANDLES.append((None, None))
    protocol_absence_verify()
    body = {
        "schema": 1, "source": SOURCE_ID, "family": FAMILY,
        "purpose": marker["purpose"], "nonce": marker["once_claim"]["nonce"],
        "marker_sha256": protocol_digest(marker),
        "seed_sha256": marker["seed_sha256"],
        "owner_sha256": protocol_digest(marker["owner"]),
        "history_sha256": marker["history"]["sha256"],
        "observation_sha256": marker["absence"]["observation_sha256"],
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 4096:
        raise AdapterError("Oversized finite once claim", "access")
    temporary = _PROTOCOL_ABSENCE_HANDLES[1][0]
    descriptor = os.open(
        marker["once_claim"]["name"],
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600, dir_fd=temporary,
    )
    try:
        info = os.fstat(descriptor)
    except BaseException:
        with contextlib.suppress(BaseException):
            os.close(descriptor)
        raise
    _PROTOCOL_ABSENCE_HANDLES[2] = descriptor, (info.st_dev, info.st_ino)
    journal_owned(descriptor, _PROTOCOL_ABSENCE_HANDLES[2][1])
    _PROTOCOL_ABSENCE_CLAIM_RAW = raw
    offset = 0
    while offset < len(raw):
        journal_owned(descriptor, _PROTOCOL_ABSENCE_HANDLES[2][1])
        written = os.pwrite(descriptor, raw[offset:], offset)
        if type(written) is not int or not 0 < written <= len(raw) - offset:
            raise AdapterError("Incomplete durable once claim", "access")
        offset += written
    os.fsync(descriptor)
    protocol_absence_verify(claim=True)
    os.fsync(temporary)
    protocol_absence_verify(claim=True)


def protocol_absence_collection_lock():
    """Acquire only a genuinely new collection lock for this finite claim."""
    global _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID
    if _COLLECTION_LOCK_FD is not None:
        raise AdapterError("Finite collection already acquired", "access")
    parent = journal_parent()
    descriptor = identity = None
    try:
        descriptor = os.open(
            "collection.lock",
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600, dir_fd=parent,
        )
        info = os.fstat(descriptor)
        identity = info.st_dev, info.st_ino
        journal_owned(descriptor, identity)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_owned(descriptor, identity)
        current = os.stat(
            "collection.lock", dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("New collection lock changed", "access")
        _COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID = descriptor, identity
        os.fsync(parent)
    except BaseException:
        journal_failed()
        if descriptor is not None and _COLLECTION_LOCK_FD is None:
            if identity is None:
                with contextlib.suppress(BaseException):
                    os.close(descriptor)
            else:
                journal_close(descriptor, identity, True)
        raise


def protocol_absence_state_lock():
    """Exclusively acquire the genuinely new stable lock, never repair it."""
    global _GUARD_LOCK_ID
    parent = journal_parent()
    descriptor = os.open(
        "state.lock", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        | os.O_NONBLOCK, 0o600, dir_fd=parent,
    )
    identity = None
    try:
        info = journal_owned(descriptor)
        identity = info.st_dev, info.st_ino
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_owned(descriptor, identity)
        current = os.stat("state.lock", dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("New state lock changed", "access")
        _GUARD_LOCK_ID = identity
        result = JournalLock(descriptor, identity)
        try:
            journal_retain()
            os.fsync(parent)
        except BaseException:
            try:
                result.close()
            finally:
                descriptor = None
            raise
        return result
    except BaseException:
        journal_failed()
        if descriptor is None:
            pass
        elif identity is None:
            with contextlib.suppress(BaseException):
                os.close(descriptor)
        elif descriptor not in _GUARD_LOCKS:
            journal_close(descriptor, identity, True)
        raise


def protocol_restore_absent():
    """One exact prospective LOCAL initialization; no absent-mature reset."""
    global _STATE_PATH, _STATE_LOCK, _GUARD_NEW_DIRECTORY
    global _PROTOCOL_ABSENCE_FAMILY, _PROTOCOL_CONTEXT, _PROTOCOL_SEQUENCE
    global _COLLECTION_STARTED, _RESTORED, _PROTOCOL_READY, _STATE_TRUSTED
    marker = protocol_absence_marker()
    if (
        _RESTORED or _PROTOCOL_READY or _PROTOCOL_ABSENCE_HANDLES
        or _GUARD_FATAL
    ):
        raise AdapterError("Finite initialization cannot reopen", "access")
    try:
        context = protocol_history()
        protocol_absence_context(marker, context)
        context["source_sha256"] = marker["owner"]["producer_sha256"]
        incoming = download_inert_artifact(
            context, context["artifact"]["id"], context["artifact"]["digest"]
        )
        protocol_absence_context(marker, context, incoming)
        check_deadline()
        protocol_absence_claim(marker)
        parent = _PROTOCOL_ABSENCE_HANDLES[1][0]
        protocol_absence_verify(claim=True)
        os.mkdir(marker["absence"]["family_name"], 0o700, dir_fd=parent)
        created = os.stat(
            marker["absence"]["family_name"], dir_fd=parent,
            follow_symlinks=False,
        )
        _PROTOCOL_ABSENCE_FAMILY = created.st_dev, created.st_ino
        os.fsync(parent)
        _STATE_PATH = "/tmp/" + marker["absence"]["family_name"] + "/state.json"
        _STATE_LOCK = "/tmp/" + marker["absence"]["family_name"] + "/state.lock"
        _GUARD_NEW_DIRECTORY = True
        seed = json.loads(json.dumps(marker["seed"]))
        with journal_restore_scope(seed):
            protocol_absence_verify(family=True, claim=True)
            protocol_absence_collection_lock()
            with protocol_absence_state_lock():
                if journal_read() != b"" or not _GUARD_CREATED:
                    raise AdapterError("New journal was not empty", "access")
                fresh = protocol_same_history(context)
                protocol_absence_context(marker, fresh, incoming)
                protocol_absence_verify(family=True, claim=True)
                check_deadline()
                now = time.time()
                owner = os.urandom(16).hex()
                seed.update(
                    observed_at=now,
                    not_before=max(seed["not_before"], now + 60),
                    transition_id=protocol_digest(marker),
                    in_flight=True, flight_id=owner,
                )
                save_budget(seed)
                verified = load_budget()
                if verified != seed or verified["flight_id"] != owner:
                    raise AdapterError(
                        "Pending initialization changed", "access"
                    )
                protocol_absence_verify(family=True, claim=True)
                now = time.time()
                seed.update(
                    in_flight=False, flight_id=None, observed_at=now,
                    last_settled_owner={
                        "owner": owner, "receipt_sha256": protocol_digest(None),
                        "completed_at": now,
                    },
                )
                save_budget(seed)
                if load_budget() != seed:
                    raise AdapterError(
                        "Matching initialization changed", "access"
                    )
        _COLLECTION_STARTED = time.time()
        _PROTOCOL_SEQUENCE += 1
        _ROBOTS.clear()
        _PROTOCOL_POLICY_RAW.clear()
        _PROTOCOL_CONTEXT = context
        _RESTORED = _PROTOCOL_READY = _STATE_TRUSTED = True
    except BaseException:
        journal_failed()
        raise


def protocol_absence_release():
    """Release held originals independently; never remove the durable claim."""
    handles = list(_PROTOCOL_ABSENCE_HANDLES)
    _PROTOCOL_ABSENCE_HANDLES.clear()
    errors = []
    for index in reversed(range(len(handles))):
        descriptor, identity = handles[index]
        if descriptor is None:
            continue
        try:
            if index == 2:
                journal_close(descriptor, identity)
            else:
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != identity:
                    raise AdapterError("Absence ancestor FD changed", "access")
                os.close(descriptor)
        except BaseException as error:
            journal_failed()
            errors.append(error)
    if errors:
        raise errors[0]


def protocol_gate():
    marker = (_REVIEWED_HOSTED_TRANSITION
              if os.environ.get("GITHUB_ACTIONS") == "true"
              else _REVIEWED_LOCAL_TRANSITION)
    if not isinstance(marker, dict):
        raise AdapterError(
            "Reviewed source continuity is not released",
            "access",
        )


def protocol_first_wire_wait():
    """Check fresh owner coordination after quiet and before first wire."""
    global _PROTOCOL_FIRST_WIRE
    if not _PROTOCOL_READY or _GUARD_FATAL:
        raise AdapterError("Healthy reviewed continuity required", "access")
    if _PROTOCOL_FIRST_WIRE:
        return
    # pace performs the actual >=60 startup wait; metadata must follow it.
    with locked_budget():
        state = load_budget()
        protocol_current_owner(state)
        target = max(state["not_before"], _COLLECTION_STARTED + 60)
    while target > time.time():
        check_deadline()
        wait = target - time.time()
        if wait + 120 > min(_PHASE_END, _RUN_END) - time.monotonic():
            raise AdapterError("Startup quiet exceeds phase", "access")
        time.sleep(min(wait, 30))
    with locked_budget():
        fresh_context = protocol_same_history(_PROTOCOL_CONTEXT)
        if _PROTOCOL_ABSENCE_HANDLES:
            protocol_absence_context(
                _REVIEWED_LOCAL_ABSENCE_TRANSITION, fresh_context
            )
            protocol_absence_verify(family=True, claim=True)
        state = load_budget()
        protocol_current_owner(state)
        if state["blocked"] or state["rate_limit_until"] > time.time():
            raise AdapterError("Retained hold prevents first wire", "access")
        journal_verify()
        check_deadline()
        if time.monotonic() + 120 > min(_PHASE_END, _RUN_END) or (
            time.time() + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError(
                "Fresh coordination consumed admission reserve",
                "access",
            )
    _PROTOCOL_FIRST_WIRE = True


def publication_configuration():
    """Validate long-run credentials before publisher access; no mint yet."""
    global _STATIC_TOKEN_END
    if not _PUBLISHING:
        return
    names = [
        "WEBSITE_APP_ID", "WEBSITE_APP_INSTALLATION_ID",
        "WEBSITE_APP_PRIVATE_KEY",
    ]
    values = [os.environ.get(name, "") for name in names]
    if all(values):
        if any(not re.fullmatch(r"[1-9][0-9]{0,19}", v) for v in values[:2]):
            raise AdapterError("Invalid App configuration", "publish")
        rsa_key(values[2])
        return
    if any(values) or os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError(
            "Complete renewable App configuration required",
            "publish",
        )
    # Explicit external-only compatibility is bounded by actual token expiry;
    # its shorter deadline never claims the full long hosted allocation.
    expiry = os.environ.get("INSAIT_STATIC_TOKEN_EXPIRES_AT", "")
    if not os.environ.get("DATA_SOURCE_TOKEN"):
        raise AdapterError("External publication token missing", "publish")
    try:
        instant = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if instant.utcoffset() is None:
            raise ValueError()
        remaining = instant.timestamp() - time.time() - 300
        if not 120 < remaining <= 3300:
            raise ValueError()
    except (ValueError, OverflowError, OSError):
        raise AdapterError(
            "Bounded external static-token expiry required",
            "publish",
        ) from None
    _STATIC_TOKEN_END = time.monotonic() + remaining
    arm_alarm(min(_PHASE_END, _RUN_END - EXPORT_TIMEOUT))


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
    validate_budget(state)
    journal_commit(state)


def pace(inventory=False):
    global _PUBLISHER_INTERVAL, _PROTOCOL_PHYSICAL_INVENTORY
    _PROTOCOL_PHYSICAL_INVENTORY = bool(inventory)
    check_deadline()
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
        if inventory and _PUBLISHER_INVENTORY_COMPLETED_MONOTONIC is not None:
            target = max(
                target,
                now + max(
                    0, _PUBLISHER_INVENTORY_COMPLETED_MONOTONIC
                    + state["page_interval"] - time.monotonic(),
                ),
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


def record_retry_after(value, rate_limited=False):
    global _PROTOCOL_RATE_STOP
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
                if not rate_limited:
                    state["blocked"] = True
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
        if rate_limited:
            _PROTOCOL_RATE_STOP = True


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


@contextlib.contextmanager
def protocol_response(response, publisher):
    """Treat any publisher response-close error as unassessed, not a stop."""
    if not publisher:
        with response:
            yield
        return
    try:
        response.__enter__()
    except BaseException:
        journal_failed()
        raise
    primary = None
    try:
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            response.__exit__(
                type(primary) if primary is not None else None,
                primary,
                primary.__traceback__ if primary is not None else None,
            )
        except BaseException:
            journal_failed()
            if primary is None:
                raise


@protocol_transport
def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            pace(
                _PROTOCOL_INVENTORY_DEPTH > 0
                and urllib.parse.urlsplit(url).path != "/robots.txt"
            )
            protocol_first_wire_wait()
        with publisher_attempt(publisher):
            request = urllib.request.Request(
                current, data=payload, headers=headers, method=method
            )
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
                primary = None
                try:
                    record_retry_after(
                        response.headers.get("Retry-After"), True
                    )
                except BaseException as error:
                    primary = error
                    journal_failed()
                    raise
                finally:
                    try:
                        response.close()
                    except BaseException:
                        journal_failed()
                        if primary is None:
                            raise
                raise AssessedStop(
                    "Publisher rate limited: HTTP 429",
                    "access",
                    429,
                )
            with protocol_response(response, publisher):
                status = response.status
                response_headers = response.headers
                retry = response.headers.get("Retry-After")
                if publisher:
                    record_retry_after(retry)
                location = response.headers.get("Location")
                literal_rep = (
                    urllib.parse.urlsplit(current).path == "/robots.txt"
                )
                if publisher and status in (401, 403, 451) and not literal_rep:
                    protocol_hold("Publisher refused content access", status)
                if publisher and status in (301, 302, 303, 307, 308):
                    if not location or redirect == 5:
                        raise AdapterError("Invalid redirect chain", "access")
                    destination = urllib.parse.urljoin(current, location)
                    public_url(destination)
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
                    )
                    if encoding != "identity":
                        raise AdapterError(
                            ("Unsupported response content encoding"), ("fetch")
                        )
            if publisher and status == 200:
                if protocol_primary_challenge(body, status):
                    protocol_hold(
                        "Publisher primary response is an access gate", status
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
                if _PROTOCOL_FETCHING_REP:
                    if urllib.parse.urlsplit(destination).path != "/robots.txt":
                        raise AdapterError(
                            "REP redirect changed purpose",
                            "access",
                        )
                else:
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
    """Assess each actual target, including every cached-policy decision."""
    with protocol_transaction("policy"):
        parsed = urllib.parse.urlsplit(public_url(url))
        origin = parsed.scheme + "://" + parsed.netloc
        if origin not in _ROBOTS:
            if origin in _PROTOCOL_FETCHING_REP:
                raise AdapterError("Recursive REP policy dependency", "access")
            _PROTOCOL_FETCHING_REP.add(origin)
            try:
                status, _, body = request_bytes(
                    origin + "/robots.txt", publisher=True
                )
                if status == 200:
                    if protocol_primary_challenge(body, status):
                        protocol_hold("REP response is a primary challenge")
                    text = body.decode("utf-8", "strict")
                    digest = hashlib.sha256(body).hexdigest()
                    parser = protocol_policy_parser(text, digest)
                    available = True
                elif 400 <= status < 500 and status != 429:
                    text, digest, parser, available = "", None, None, False
                else:
                    raise AdapterError(
                        "REP policy unavailable",
                        "access",
                        status,
                    )
                _ROBOTS[origin] = parser
                _PROTOCOL_POLICY_RAW[origin] = {
                    "text": text,
                    "sha256": digest,
                    "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "available": available,
                    "collection": _PROTOCOL_SEQUENCE,
                }
            finally:
                _PROTOCOL_FETCHING_REP.remove(origin)
        return protocol_assess_policy(url)


@protocol_collection_call
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
            f"Publisher page returned HTTP {status}: "
            + provenance,
            "fetch",
            status,
        )


def workflow_api(path):
    """Read fresh metadata; hosted callers must have workflow credentials."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Workflow state credentials missing", "access")
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    separator = "&" if "?" in path else "?"
    status, _, body = request_bytes(
        _WORKFLOW_GITHUB + path + separator + "_fresh=" + str(time.time_ns()),
        headers=headers,
    )
    if status != 200:
        raise AdapterError("Workflow metadata unavailable", "access", status)
    return json.loads(body)


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
        return protocol_decode(envelope, identity)
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def restore_family_artifact():
    """Restore newest exact-owned state; never initialize from empty history."""
    global _RESTORED, _PROTOCOL_CONTEXT, _PROTOCOL_READY
    global _STATE_TRUSTED, _COLLECTION_STARTED, _PROTOCOL_SEQUENCE
    if _RESTORED or _PROTOCOL_READY:
        raise AdapterError(
            "Collection reentry requires fresh process",
            "access",
        )
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        and _REVIEWED_LOCAL_ABSENCE_TRANSITION is not None
    ):
        return protocol_restore_absent()
    protocol_gate()
    context = protocol_history()
    context["source_sha256"] = protocol_source_hash(context["latest"]["head"])
    if context["hosted"] and protocol_source_hash(context["current"][2]) != (
        hashlib.sha256(open(__file__, "rb").read()).hexdigest()
    ):
        raise AdapterError(
            "Executing source differs from current run head",
            "access",
        )
    incoming = download_inert_artifact(
        context, context["artifact"]["id"], context["artifact"]["digest"]
    )
    legacy = incoming["schema"] == 1
    marker = protocol_marker(context) if legacy else None
    if legacy and protocol_digest(incoming) != marker["legacy_state_sha256"]:
        raise AdapterError("Legacy owned state differs from boundary", "access")
    if not context["hosted"]:
        # A missing LOCAL family never borrows hosted namespace creation.
        directory = os.path.join(
            tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
        )
        if not os.path.lexists(directory):
            raise AdapterError("Mature LOCAL namespace missing", "access")
    if legacy:
        seed = json.loads(json.dumps(_REVIEWED_LEGACY_SEED))
    else:
        seed = protocol_join(
            validate_budget(_REVIEWED_LEGACY_SEED), incoming
        )
    with journal_restore_scope(seed):
        budget_file()
        protocol_acquire_collection()
        with locked_budget():
            raw = journal_read()
            if raw:
                local = json.loads(raw)
                if local.get("schema") == 1:
                    protocol_legacy_state(local)
                    if not legacy or context["hosted"]:
                        raise AdapterError(
                            "Unreviewed old LOCAL state",
                            "access",
                        )
                    protocol_check_local_pins(marker["local_pins"], raw)
                    if local["blocked"]:
                        raise AdapterError(
                            "Legacy content hold retained",
                            "access",
                        )
                    if seed["not_before"] < local["not_before"]:
                        raise AdapterError(
                            "Seed weakens LOCAL embargo",
                            "access",
                        )
                    if local["observed_at"] > seed["observed_at"]:
                        raise AdapterError(
                            "Seed misses newer LOCAL observation",
                            "access",
                        )
                    combined = json.loads(json.dumps(seed))
                else:
                    if legacy:
                        raise AdapterError(
                            "Modern LOCAL cannot fall back to legacy",
                            "access",
                        )
                    combined = protocol_join(validate_budget(local), seed)
            elif not _GUARD_CREATED or not journal_creation_allowed():
                raise AdapterError(
                    "Mature state cannot reset from missing bytes",
                    "access",
                )
            else:
                combined = json.loads(json.dumps(seed))
            if legacy:
                protocol_legacy_constraints(
                    combined, incoming, local if raw else None, context, marker
                )
            protocol_same_history(context)
            now = time.time()
            bounds = context["completion_bounds"]
            if any(t >= now - 60 for t in bounds):
                raise AdapterError(
                    "Recent job completion requires quiet review",
                    "access",
                )
            combined["completed_at"] = max(
                combined["completed_at"], max(bounds)
            )
            combined["inventory_completed_at"] = max(
                combined["inventory_completed_at"], max(bounds)
            )
            combined["observed_at"] = now
            combined["not_before"] = max(combined["not_before"], now + 60)
            if legacy:
                combined["transition_id"] = protocol_digest(marker)
            owner = os.urandom(16).hex()
            combined.update(in_flight=True, flight_id=owner,
                            policy_pending=None, last_policy_receipt=None,
                            last_settled_owner=None)
            save_budget(combined)
            verified = load_budget()
            if verified != combined:
                raise AdapterError("Initialization commit changed", "access")
            combined.update(
                in_flight=False, flight_id=None, observed_at=time.time()
            )
            save_budget(combined)
    _COLLECTION_STARTED = time.time()
    _PROTOCOL_SEQUENCE += 1
    _ROBOTS.clear()
    _PROTOCOL_POLICY_RAW.clear()
    _PROTOCOL_CONTEXT = context
    _RESTORED = _PROTOCOL_READY = _STATE_TRUSTED = True


def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("INSAIT_PACING_ARTIFACT_PATH")
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
        run_id, attempt = protocol_hosted_identity()
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
    if _GUARD_FATAL or _PROTOCOL_READY:
        raise AdapterError("Collection lifecycle cannot reopen", "access")
    publication_configuration()
    restore_family_artifact()


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
    """Accept public pages on the two publisher origins, including new calls."""
    try:
        parsed = urllib.parse.urlsplit(value)
        decoded = urllib.parse.unquote(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc not in {"insait.ai", "explorer.insait.ai"}
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.query
            or re.search(r"[\x00-\x20\x7f\\]", value)
            or any(part in {".", ".."} for part in decoded.split("/"))
            or re.search(
                r"/(?:wp-admin|wp-login\.php|wp-json|xmlrpc\.php|"
                r"cdn-cgi|my-account|login|apply-for-post-roc)(?:/|$)",
                decoded,
                re.I,
            )
        ):
            raise ValueError()
    except ValueError:
        raise AdapterError("Invalid public publisher route", "access") from None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def opportunity_route(url):
    """Select programme and call routes, excluding people and result stories."""
    parsed = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(parsed.path).lower()
    if parsed.hostname == "explorer.insait.ai" and path == "/":
        return True
    path = re.sub(r"^/bg/", "/", path)
    if re.search(
        r"current-faculty|phd-post-docs|graduate-research-fellows|"
        r"surfers|research-fellows-\d|program-class|/category/|"
        r"selected|accepted|full-swing|record-interest|joins-insait|"
        r"applicants-for|positions-bulgaria|/apply-for-",
        path,
    ):
        return False
    return bool(
        re.search(
            r"^/(?:surf|phd|faculty|post-doc-at-insait|mit)/$|"
            r"scholarship|internship|fellowship|research-program|"
            r"(?:open|launch|call|recruit).*(?:application|program|position)|"
            r"(?:non-academic|staff)-positions?|"
            r"(?:кандидатств|прием|стипенди|стажант|набира).*(?:програм|20\d{2})|"
            r"(?:нова|нови).*(?:програм|стипенди)|"
            r"(?:програм|стипенди).*(?:кандидатств|прием)",
            path,
        )
    )


def page_document(url):
    status, headers, body = fetch_public(url)
    mime = headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if status != 200 or mime not in {"text/html", "application/xhtml+xml"}:
        raise AdapterError("Invalid programme document: " + url, "parse")
    root = document_root(body.decode("utf-8", "strict"))
    main = one(nodes(root, "main"), "publisher main content")
    if not main.text():
        raise AdapterError("Empty publisher content: " + url, "parse")
    return root, main


def sitemap_routes():
    """Follow all advertised sitemap pages; never publish a truncated frontier."""
    pending = [
        "https://insait.ai/sitemap_index.xml",
        "https://explorer.insait.ai/sitemap_index.xml",
    ]
    seen, routes = set(), set()
    namespace = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        if len(seen) >= 64:
            raise AdapterError("Publisher sitemap limit exceeded", "parse")
        seen.add(url)
        _, _, body = fetch_inventory_page(fetch_public, public_url(url))
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise AdapterError(
                "Invalid publisher sitemap: " + url, "parse"
            ) from None
        if root.tag == namespace + "sitemapindex":
            entries = root.findall(namespace + "sitemap")
            tag = "index"
        elif root.tag == namespace + "urlset":
            entries = root.findall(namespace + "url")
            tag = "pages"
        else:
            raise AdapterError("Unknown publisher sitemap format", "parse")
        for entry in entries:
            location = entry.find(namespace + "loc")
            if location is None or not location.text:
                raise AdapterError("Missing sitemap destination", "parse")
            destination = location.text.strip()
            if tag == "index":
                destination = public_url(destination)
                if not urllib.parse.urlsplit(destination).path.endswith(".xml"):
                    raise AdapterError("Invalid child sitemap route", "parse")
                pending.append(destination)
            else:
                parsed = urllib.parse.urlsplit(destination)
                if parsed.scheme != "https" or parsed.netloc not in {
                    "insait.ai",
                    "explorer.insait.ai",
                }:
                    raise AdapterError(
                        "Foreign publisher sitemap entry", "parse"
                    )
                if opportunity_route(destination):
                    routes.add(public_url(destination))
                if len(routes) > 50000:
                    raise AdapterError(
                        "Publisher route limit exceeded", "parse"
                    )
    if not routes:
        raise AdapterError("Empty publisher sitemap inventory", "parse")
    return routes


def read_pages():
    pages = {}
    hub_url = "https://insait.ai/join-insait/"
    hub, _ = fetch_inventory_page(page_document, hub_url)
    candidates = {url for url in sitemap_routes() if opportunity_route(url)}
    # The navigation includes programmes omitted from the publisher's sitemap.
    for node in nodes(hub, "a"):
        href = urllib.parse.urljoin(hub_url, node.attrs.get("href", ""))
        if urllib.parse.urlsplit(href).hostname not in {
            "insait.ai",
            "explorer.insait.ai",
        }:
            continue
        if opportunity_route(href):
            candidates.add(public_url(href))
    if not candidates or len(candidates) > 100:
        raise AdapterError("Invalid programme discovery count", "parse")
    pending, seen, aliases = sorted(candidates), set(), {}
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        if len(seen) >= 100:
            raise AdapterError("Programme traversal limit exceeded", "parse")
        seen.add(url)
        root, main = page_document(url)
        english = {
            public_url(node.attrs.get("href", ""))
            for node in nodes(root, "link")
            if node.attrs.get("rel") == "alternate"
            and node.attrs.get("hreflang", "").lower() == "en"
        }
        if len(english) > 1:
            raise AdapterError(
                "Ambiguous English programme alternative", "parse"
            )
        if english and url not in english:
            aliases[url] = next(iter(english))
            pending.extend(sorted(english - seen))
            continue
        pages[url] = (root, main)
    if any(target not in pages for target in aliases.values()):
        raise AdapterError("Unresolved programme language alternative", "parse")
    return pages


def programme_title(root, main):
    titles = list(
        dict.fromkeys(n.text() for n in nodes(main, "h1") if n.text())
    )
    if len(titles) == 1:
        return titles[0]
    metadata = [
        n.attrs.get("content", "").strip()
        for n in nodes(root, "meta")
        if n.attrs.get("property") == "og:title"
    ]
    if len(metadata) == 1 and metadata[0]:
        return re.sub(r"\s+[|–-]\s+(?:INSAIT|EXPLORER)$", "", metadata[0])
    raise AdapterError("Missing unambiguous programme title", "parse")


def published_time(root):
    values = [
        n.attrs.get("content")
        for n in nodes(root, "meta")
        if n.attrs.get("property") == "article:published_time"
    ]
    if not values:
        return None
    try:
        result = datetime.fromisoformat(values[0].replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return (
            result.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        )
    except (ValueError, AttributeError):
        raise AdapterError(
            "Invalid publisher publication date", "parse"
        ) from None


def application_deadlines(main):
    """Read all explicitly dated closings without guessing a year or round."""
    month_names = (
        "january february march april may june july august september "
        "october november december"
    ).split()
    month_pattern = (
        r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
        r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
        r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    )
    dates = set()
    for node in main.walk():
        if node.tag not in {"tr", "p", "li"}:
            continue
        for clause in re.split(r"[.!?;]\s+(?=[A-Z])", node.text()):
            marker = re.search(
                r"application (?:window|deadlines?)|"
                r"applications?\b[^.!?;]{0,60}?\b(?:close|until|by|open from)|"
                r"deadline\s+(?:for|to)\s+(?:applications?|apply)|"
                r"apply\s+(?:by|until|before)|closing date",
                clause,
                re.I,
            )
            if marker is None:
                continue
            text = clause[marker.end() :]
            dates.update(closing_dates(text, month_pattern, month_names))
    return dates


def application_deadline(main):
    """Represent a single unambiguous closing in the public deadline field."""
    dates = application_deadlines(main)
    return next(iter(dates)) if len(dates) == 1 else None


def closing_dates(text, month_pattern, month_names):
    """Resolve a dated closing clause, retaining distinct-round ambiguity."""
    matches = list(
        re.finditer(
            rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?"
            r",?\s+(20\d{2})\b",
            text,
            re.I,
        )
    )
    matches += list(
        re.finditer(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})"
            r",?\s+(20\d{2})\b",
            text,
            re.I,
        )
    )
    matches.sort(key=lambda item: item.start())
    if len(matches) == 2 and re.fullmatch(
        r"\s*(?:to|through|until|[–—-])\s*",
        text[matches[0].end() : matches[1].start()],
        re.I,
    ):
        matches = matches[1:]
    dates = set()
    for match in matches:
        first, second, year = match.groups()
        month, day = (second, first) if first.isdigit() else (first, second)
        month_number = next(
            index
            for index, name in enumerate(month_names, 1)
            if name.startswith(month.lower().rstrip(".")[:3])
        )
        try:
            closing = datetime(int(year), month_number, int(day))
        except ValueError:
            raise AdapterError(
                "Invalid application closing date", "parse"
            ) from None
        clock = re.match(
            r"\s*,?\s*(?:at\s+)?\(?(\d{1,2}):(\d{2})\s*(EET|EEST|UTC)\b",
            text[match.end() :],
            re.I,
        )
        if clock:
            hour, minute, zone = clock.groups()
            offset = {"EET": 2, "EEST": 3, "UTC": 0}[zone.upper()]
            try:
                closing = closing.replace(
                    hour=int(hour),
                    minute=int(minute),
                    tzinfo=timezone(timedelta(hours=offset)),
                )
            except ValueError:
                raise AdapterError(
                    "Invalid application closing time", "parse"
                ) from None
            dates.add(
                closing.astimezone(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z")
            )
        else:
            dates.add(closing.date().isoformat())
    return dates


def programme_summary(main, title):
    """Use a short current factual excerpt, excluding navigation and testimonials."""
    blocks = []
    for node in main.walk():
        if node.tag in {"p", "li", "tr", "h2", "h3"}:
            text = node.text()
            if (
                len(text) >= 30
                and text != title
                and not text.startswith(("“", "„", '"'))
                and not re.search(
                    r"email protected|cookies|copyright", text, re.I
                )
                and re.search(
                    r"application|eligible|stipend|scholarship|fellowship|"
                    r"candidates|program|research|прием|кандидат|стипенд|програма",
                    text,
                    re.I,
                )
            ):
                blocks.append(text)
    blocks = list(dict.fromkeys(blocks))
    blocks.sort(
        key=lambda text: not bool(
            re.search(
                r"application (?:window|deadline)|applications?\b.{0,60}\b(?:from|by).{0,15}\d|"
                r"\beligib|\b(?:stipend|scholarship|fellowship)s?\b.{0,50}\d|"
                r"прием\s+20\d{2}|стипендия.{0,50}\d",
                text,
                re.I,
            )
        )
    )
    text = " ".join(blocks) or main.text().removeprefix(title).strip()
    upcoming = re.search(
        r"прием\s+20\d{2}[/–-](?:20)?\d{2}\s*[–-]?\s*очаквайте скоро[!]?",
        main.text(),
        re.I,
    )
    if upcoming:
        text = upcoming[0] + " " + text
    text = text.replace("<", "").replace(">", "")
    if len(text.encode("utf-16-le")) // 2 > 600:
        text = text.encode("utf-16-le")[:1194].decode("utf-16-le", "ignore")
        text = text.rsplit(" ", 1)[0].rstrip(" ,;:") + "..."
    return text


def record_identity(url, title, text):
    """Retain published identities for established programmes and their editions."""
    suffixes = {
        "/faculty/": "faculty|tenure-track-and-tenured",
        "/post-doc-at-insait/": "postdoc|rolling",
        "/phd/": "PhD|rolling",
        "/mit/": "MIT-CSAIL|yearless-programme",
        "/deepmind-phd-scholarships/": "DeepMind|ML-AI-PhD",
    }
    path = urllib.parse.urlsplit(url).path
    if url.startswith("https://insait.ai/") and path in suffixes:
        return url + "|" + suffixes[path]
    if path == "/surf/":
        year = re.search(r"\b20\d{2}\b", title)
        return url + "|SURF|" + (year[0] if year else "undated")
    if path == "/summer-ai-research-program-for-students/":
        year = re.search(r"\bsummer\s+(?:of\s+)?(20\d{2})\b", text, re.I)
        return (
            url
            + "|STARS|"
            + (year[1] + "-historical-overview" if year else "undated")
        )
    if url == "https://explorer.insait.ai/":
        year = re.search(r"прием\s+(20\d{2})[/–-](?:20)?(\d{2})", text, re.I)
        return (
            url
            + "|EXPLORER|"
            + (
                year[1] + "-" + year[2] + "-upcoming-overview"
                if year
                else "undated"
            )
        )
    if "explorer" in title.lower():
        year = re.search(r"\b20\d{2}\b", title)
        if year:
            return url + "|EXPLORER|" + year[0] + "-call"
    return url


def parse_inventory(pages):
    if not pages:
        raise AdapterError("Empty programme discovery result", "parse")
    records = []
    current_surf = pages.get("https://insait.ai/surf/")
    surf_title = programme_title(*current_surf) if current_surf else ""
    for url, (root, main) in pages.items():
        title = programme_title(root, main)
        text = main.text()
        if re.search(
            r"no (?:open|available) positions|нямаме отворени позиции",
            text,
            re.I,
        ):
            continue
        closings = application_deadlines(main)
        deadline = next(iter(closings)) if len(closings) == 1 else None
        is_news = any(
            node.attrs.get("property") == "article:section"
            and node.attrs.get("content", "").lower() in {"news", "новини"}
            for node in nodes(root, "meta")
        )
        if (
            is_news
            and not closings
            and not re.search(
                r"call for applications|apply now|accepting applications|"
                r"applications\b[^.!?]{0,40}\bopen|"
                r"кандидатстването\b[^.!?]{0,40}(?:започ|отвор)|"
                r"прием\b[^.!?]{0,40}(?:открит|започ)|кандидатствайте",
                text,
                re.I,
            )
        ):
            # Programme publicity and recipient stories are not separate calls.
            continue
        # Programme news corroborates the same SURF edition, not a second call.
        if url != "https://insait.ai/surf/" and "surf" in title.lower():
            edition = re.search(r"\b20\d{2}\b", title)
            if edition and edition[0] in surf_title:
                continue
        lower = (
            title + " " + urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        ).lower()
        if re.search(r"postdoc|post-doc|faculty|position|позици", lower):
            category = "jobs"
        elif re.search(r"scholarship|phd|doctoral|explorer|стипенд", lower):
            category = "scholarships"
        elif re.search(r"summer|internship|surf", lower):
            category = "internships"
        elif re.search(r"fellowship|research program", lower):
            category = "fellowships"
        else:
            category = "other"
        hosts = []
        if re.search(r"\bSofia\b|\bBulgaria\b|София|България", text):
            hosts = ["BG"]
        if urllib.parse.urlsplit(url).path == "/mit/" and "MIT CSAIL" in text:
            hosts = ["US"]
        upcoming = bool(re.search(r"coming soon|очаквайте скоро", text, re.I))
        record = make_record(
            title,
            url,
            [category],
            kind="programme-overview" if upcoming else "opportunity",
            published_at=published_time(root),
            host_countries=hosts,
            method="publisher-html",
            evidence=[ATTRIBUTION, "Current programme content: " + url],
        )
        identity = record_identity(url, title, text)
        record["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + identity).encode()
        ).hexdigest()[:24]
        record["language"] = "bg" if re.search(r"[А-Яа-я]", title) else LANGUAGE
        record["summary"] = programme_summary(main, title)
        if len(closings) > 1:
            record["classification"]["evidence"].append(
                "Multiple published application closings: "
                + ", ".join(sorted(closings))
            )
        record["deadline"] = deadline
        if not record["deadline"] and re.search(r"research program", lower):
            record["kind"] = "programme-overview"
        if upcoming:
            record["deadline"] = None
        elif record["deadline"]:
            deadline = record["deadline"]
            now = datetime.now(timezone.utc)
            if len(deadline) == 10:
                today = now.date().isoformat()
                record["status"] = (
                    "expired"
                    if today > deadline
                    else "open" if today < deadline else "unknown"
                )
            else:
                closing = datetime.fromisoformat(
                    deadline.replace("Z", "+00:00")
                )
                record["status"] = "expired" if now > closing else "open"
        elif re.search(
            r"applications? (?:are |is )?(?:now )?closed|"
            r"application (?:window|period) (?:has )?closed",
            text,
            re.I,
        ):
            record["status"] = "expired"
        records.append(record)
    validate_records(records)
    return records


def collect():
    if _PHASE_END is not None:
        check_deadline()
        if not _PROTOCOL_READY:
            raise AdapterError(
                "Reviewed collection continuity required",
                "access",
            )
        return parse_inventory(read_pages())
    with execution():
        with phase("prepublication", COLLECTION_TIMEOUT):
            prepare_collection()
            return parse_inventory(read_pages())


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "display_name": "INSAIT",
        "source": SOURCE_ID,
        "name": "INSAIT — reviewed public research, recruitment and EXPLORER programmes",
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
                f"Validated refresh retains {len(records)} reviewed "
                "INSAIT opportunities"
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
        or previous.get("source") != SOURCE_ID
        or previous.get("source_url") != SOURCE_URL
        or previous.get("website_url") != WEBSITE_URL
        or not isinstance(previous.get("message"), str)
        or previous.get("status") not in {"success", "fail"}
        or type(previous.get("record_count")) is not int
        or previous["record_count"] != len(records)
        or previous.get("language") != LANGUAGE
        or previous.get("publisher_country") != PUBLISHER_COUNTRY
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
        if records and timestamps["last_checked_at"] != max(
            datetime.fromisoformat(record["last_checked_at"].replace("Z", "+00:00"))
            for record in records
        ):
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


def remove_expired_records(records, checked_at):
    """Filter verified deadlines after validating the complete fresh inventory.

    Unzoned closing dates use UTC-12 as a conservative world-wide boundary;
    no publisher timezone is inferred. Explicit offsets retain their instant.
    """
    validate_records(records)
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing = datetime.fromisoformat(deadline).date()
            latest_date = checked.astimezone(
                timezone(timedelta(hours=-12))
            ).date()
            expired = latest_date > closing
        elif deadline:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            if closing.tzinfo is not None:
                expired = checked > closing
        if not expired:
            retained.append(record)
    validate_records(retained, allow_empty=True)
    return retained


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
    global _PUBLISHING
    _PUBLISHING = bool(publishing)
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
                    validate_prior_metadata(candidate_metadata, candidate_records)
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            fresh_records(records, old, attempt, checked)
            records = remove_expired_records(records, checked)
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


def journal_acknowledge(expected):
    """Acknowledge only complete bytes produced by our own last syscall."""
    global _GUARD_VERSION
    before = journal_verify(False)
    raw = os.pread(_GUARD_FD, 16385, 0)
    after = journal_verify(False)
    if (
        raw != expected
        or len(raw) != after.st_size
        or journal_generation(before) != journal_generation(after)
    ):
        raise AdapterError("Publisher write generation changed", "access")
    _GUARD_VERSION = journal_generation(after)


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
        journal_acknowledge(b"")
        os.fsync(_GUARD_FD)
        journal_verify()
        offset = 0
        while offset < len(payload):
            journal_verify()
            written = os.pwrite(_GUARD_FD, payload[offset:], offset)
            if (
                type(written) is not int
                or not 0 < written <= len(payload) - offset
            ):
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += written
            journal_acknowledge(payload[:offset])
        os.fsync(_GUARD_FD)
        before = journal_verify()
        raw = os.pread(_GUARD_FD, len(payload) + 1, 0)
        after = journal_verify()
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
    if journal_generation(info) != _GUARD_VERSION:
        return
    os.ftruncate(_GUARD_FD, 0)
    os.fsync(_GUARD_FD)


def journal_preopener():
    """Final publisher authority check, after request construction."""
    try:
        journal_verify()
        with locked_budget():
            state = load_budget()
            protocol_current_owner(state)
            if state["blocked"] or state["rate_limit_until"] > time.time():
                raise AdapterError(
                    "Current publisher hold before opener",
                    "access",
                )
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


if __name__ == "__main__":
    sys.exit(main())
