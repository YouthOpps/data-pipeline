"""Index basic programme facts from the official grants.at public UI."""

import argparse
import zipfile
import stat
import math
import io
import base64
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import copy
import signal
import threading
import urllib.robotparser

SOURCE_ID = "at-grants-at"
SOURCE_URL = "https://grants.at/en/"
WEBSITE_URL = "https://grants.at/"
LANGUAGE = "und"
PUBLISHER_COUNTRY = "AT"
PUBLISHER_TYPE = "public-institution"
ATTRIBUTION = "Source: grants.at / OeAD (© OeAD)"
SERVICE_URL = "https://ajs.scholarships.at/"
SCRIPT_URL = SERVICE_URL + "JS_1.0/STIMAQuery.min.js"
IMPRINT_URL = "https://oead.at/en/imprint-en"
ERNST_MACH_FH_KEY = (44772, 0)
ERNST_MACH_FH_TITLE = (
    "Ernst Mach Grant for studying at an Austrian university of applied "
    "sciences (Fachhochschule)"
)
ERNST_MACH_FH_FAQ = (
    "https://oead.at/en/study-research-teaching/overview-grants-and-scholarships/"
    "ernst-mach-grant/faq-ernst-mach-grant-fachhochschule"
)
_ALLOWED_HOSTS = {"grants.at", "ajs.scholarships.at", "oead.at"}
_ROBOTS = {}
_PAGE_COMPLETED = None
_COLLECTION_END = None

_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-grants-at-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 900
_STATE_SCHEMA = 2
_ARTIFACT_NAME = "grants-at-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_PAGE_INTERVAL = 30

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

_LEGACY_ENVELOPE_SHA256 = "74ecab6ef584f377b7c10583e427b3c4cf7cd2b1fb5eb347df5d0b09b6aff305"
_RESTORE_INCOMING = None
_STARTUP_QUIET_UNTIL = None
_PHYSICAL_COMPLETED_MONO = None
_INVENTORY_COMPLETED_MONO = None
_INVENTORY_ACTIVE = False
_ROBOTS_CONTEXT = None
_RUN_END = None
_LOCAL_NATIVE_STATE_SHA256 = None
_LOCAL_FIRST_ACCESS_CHECKED = False
_LOCAL_NATIVE_MACHINE_SHA256 = None
_LOCAL_NATIVE_EVIDENCE = None
_LOCAL_CLAIM_ID = None
# Accepted native source41c0b7; local native activation remains OFF.
# Final native journal SHA256:
# 7f92136f755a613eeb0800b8c58ed04bca40ae8423247d6f1ea88198f089753c
# The union of all 87 journals and its lineage are in the source README.
_HOSTED_MAX_STATE_SHA256 = (
    'eecaaec8f9f2e107d7a3c57e55152299fe562e56b1afc5cd416db9181b41d709'
)
_HOSTED_MAX_EVIDENCE = (
    'https://github.com/YouthOpps/data-pipeline/issues/393#issuecomment-6948764586'
)
_HOSTED_MAX_EXPECTED_SHA256 = (
    "eecaaec8f9f2e107d7a3c57e55152299fe562e56b1afc5cd416db9181b41d709"
)
_HOSTED_MAX_STATE = {
    "blocked": False,
    "completed": 1794919230.4643943,
    "completed_id": "1dfa560fc6b8088968e88beefe8cd3b7",
    "family": "youthopps-grants-at-publisher-v1",
    "interval": 12.2,
    "inventory_completed": 1794919230.4643943,
    "legacy_history_unknown": True,
    "legacy_owned": {
        "repository": "YouthOpps/data-pipeline",
        "run_attempt": 1,
        "run_id": 41990792713,
        "schema": 1,
        "source": "at-grants-at",
        "state": {
            "blocked": False,
            "completed": 1793865924.5074487,
            "family": "youthopps-grants-at-publisher-v1",
            "interval": 12.2,
            "not_before": 1793865567.157777,
            "observed_at": 1793865924.5074487,
            "page_interval": 30,
            "rate_limit_strikes": 0,
            "rate_limit_until": 0,
            "schema": 1,
            "starts": [
                1793865892.6449895,
                1793865923.6611776
            ]
        }
    },
    "not_before": 1794918837.8175097,
    "observed_at": 1794919230.4644923,
    "page_interval": 30,
    "pending": False,
    "pending_id": None,
    "pending_kind": None,
    "pending_started": 1794919230.030361,
    "rate_limit_strikes": 0,
    "rate_limit_until": 0,
    "schema": 2,
    "starts": [
        1792068163.257482,
        1792068194.6272607,
        1793865892.6449895,
        1793865923.6611776,
        1794918863.4620998,
        1794918879.9591222,
        1794918896.6567416,
        1794918910.2872803,
        1794918923.461708,
        1794918937.3685918,
        1794918951.4805322,
        1794918983.202406,
        1794919014.6145973,
        1794919045.38211,
        1794919075.8144252,
        1794919106.8373353,
        1794919137.7704546,
        1794919168.5432615,
        1794919199.431933,
        1794919230.031033
    ]
}

_COLLECTION_ATTEMPTED = _COLLECTION_CLOSED = False

_WORKFLOW_ID = 385560984
_WORKFLOW_PATH = ".github/workflows/fetch-at-grants-at.yml"
_ADAPTER_PATH = "adapters/at-grants-at/adapter.py"
_FH_QA_BRANCH = "qa/grants-at-fh469-native-r3"
_FH_QA_PATH = ".github/workflows/verify-at-grants-at-fh469-r3.yml"
_FH_NATIVE_HANDOFF = None
_LEGACY_SOURCE_SHA256 = "53061cf5e6a42f8395252218fb4ee45af4af5f6710f3fa6c2b0ef3107a737977"
_LEGACY_WORKFLOW_SHA256 = "1d6fd8b2a43ec3b5160532e50985823efc26bc7510a8aecd51807a89b8612b3e"
_WORKFLOW_SHA256 = '54d611bdd987b6c71881d34092efe664e82827658bba81c8ead348a2edb08f0e'
_IMMUTABLE_FILES = {}
_LEGACY_OWNER = (
    41990792713, 1, 13397744160, "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
    "sha256:667c19b1a95e9f55a0847e6b6d505d1a04e0bf74f2b1a7d9eff19ec08b2b2226")
# Finite restoration-only failures; independent evidence: issue393 comment6944753581.
_NO_ACCESS_PINS = {
    44054289403: (133939514322, 'b9cbbb88c3b9e276376879ff14d3659220faa612',
                  '2026-11-17T09:17:58Z', '2026-11-17T09:19:36Z'),
    43828206431: (133166945702, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-16T08:03:21Z', '2026-11-16T08:05:09Z'),
    43692078812: (132744662091, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-15T07:55:14Z', '2026-11-15T07:56:42Z'),
    43572072161: (132378411487, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-14T07:38:56Z', '2026-11-14T07:40:24Z'),
    43390771856: (131769844076, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-13T08:07:38Z', '2026-11-13T08:09:18Z'),
    43182718992: (131062371734, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-12T08:24:50Z', '2026-11-12T08:26:25Z'),
    42982066566: (130394540873, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-11T07:47:34Z', '2026-11-11T07:49:17Z'),
    42782943990: (129717114172, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-10T08:28:45Z', '2026-11-10T08:30:44Z'),
    42583443479: (129047438714, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-09T08:05:23Z', '2026-11-09T08:06:52Z'),
    42458914019: (128663772693, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-08T07:28:14Z', '2026-11-08T07:29:35Z'),
    42350580540: (128337633946, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-07T07:16:25Z', '2026-11-07T07:17:52Z'),
    42184446001: (127781356007, '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
                  '2026-11-06T07:59:03Z', '2026-11-06T08:00:35Z')
}


class AdapterError(Exception):

    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)
_ROBOTS = {}


def utc_now():
    return (datetime.now(
        timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit((
            urllib.parse.urlsplit(match[0]).scheme,
            urllib.parse.urlsplit(match[0]).hostname or "",
            urllib.parse.urlsplit(match[0]).path,
            "",
            "",
        )),
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return " ".join(text.split())[:500]


def fetch_source(url, min_interval=12.2):
    status, headers, body = source_request(url, interval=min_interval)
    return body.decode(headers.get_content_charset() or "utf-8", "strict")


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
                    expired = (checked.astimezone(latest_zone).date()
                               > closing_date)
                else:
                    deadline = datetime.fromisoformat(
                        value.replace("Z", "+00:00"))
                    expired = (deadline.tzinfo is not None and
                               checked > deadline)
            except (ValueError, TypeError):
                # Missing or ambiguous deadlines never imply expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, published=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not published):
        raise AdapterError("Empty or invalid opportunity collection",
                           "validate")
    ids = set()
    for record in records:
        if (not isinstance(record, dict) or record.get("source") != SOURCE_ID or
                record.get("source_url") != SOURCE_URL):
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
            if (not isinstance(record.get(field), str) or
                    not record[field].strip()):
                raise AdapterError("Missing record field: " + field, "validate")
        if (not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", record["id"]) or
                record["id"].lower() in ids):
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].lower())
        url = urllib.parse.urlsplit(record["url"])
        if (url.scheme not in ("http", "https") or not url.hostname or
                url.username or url.password):
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
                    elif (datetime.fromisoformat(value.replace("Z",
                                                               "+00:00")).tzinfo
                          is None):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError("Invalid record timestamp: " + field,
                                       "validate")
        for field in ("tags", "host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values):
                raise AdapterError("Invalid record list: " + field, "validate")
            if field != "tags" and any(
                    not re.fullmatch("[A-Z]{2}", value) for value in values):
                raise AdapterError("Invalid country code", "validate")
        categories = record.get("categories")
        if (not isinstance(categories, list) or not categories or
                any(value not in CATEGORIES for value in categories) or
                len(set(categories)) != len(categories) or
                record.get("category") != categories[0]):
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
            if (field not in record or record[field] is not None and
                    not isinstance(record[field], str)):
                raise AdapterError("Invalid record field: " + field, "validate")
        classification = record.get("classification")
        if (not isinstance(classification, dict) or
                not isinstance(classification.get("method"), str) or
                classification.get("status") not in ("classified", "unknown") or
                not isinstance(classification.get("evidence"), list) or
                any(not isinstance(value, str)
                    for value in classification["evidence"])):
            raise AdapterError("Invalid classification evidence", "validate")


def github(method, path, payload=None, missing_ok=False):
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError("DATA_SOURCE_TOKEN is required for publication",
                           "publish")
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
            payload=(json.dumps(payload).encode()
                     if payload is not None else None),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; publication is unconfirmed",
            "publish") from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication API returned HTTP {status}",
                           "publish", status)
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
        if file is None:
            files[name] = None
            continue
        expected = file.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", expected):
            raise AdapterError("Invalid existing source blob identity",
                               "publish")
        if file.get("encoding") != "base64":
            file = github("GET", "/git/blobs/" + expected)
        if file.get("encoding") != "base64" or file.get("sha") != expected:
            raise AdapterError("Unsupported existing source blob", "publish")
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", file["content"]),
                                   validate=True)
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" +
                                  raw).hexdigest()
            if actual != expected:
                raise ValueError("Source blob digest mismatch")
            files[name] = raw.decode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise AdapterError("Invalid existing source blob bytes",
                               "publish") from None
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish")
    return {"head": head, "tree": tree, "files": files}


def publish_files(snapshot, files):
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        candidate = None
        try:
            tree = github(
                "POST",
                "/git/trees",
                {
                    "base_tree":
                        snapshot["tree"],
                    "tree": [{
                        "path": f"datas/{SOURCE_ID}/{name}",
                        "mode": "100644",
                        "type": "blob",
                        "content": text,
                    } for name, text in files.items()],
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
                {
                    "sha": candidate,
                    "force": False
                },
            )
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (latest["head"] == candidate or
                              latest["files"] == desired):
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
    result = {
        "display_name": "OeAD",
        "source": SOURCE_ID,
        "name": "grants.at / OeAD",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at":
            (previous.get("last_success_at") if error else attempt),
        "last_checked_at":
            (previous.get("last_checked_at") if error else checked_at or max(
                (record["last_checked_at"] for record in records),
                default=attempt,
            )),
        "record_count": len(records),
        "message": ("Collection failed; last-good data preserved" if error else
                    (f"Validated refresh retains {len(records)} "
                     "validated grants.at funding programme records")),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def numeric(value):
    try:
        return (type(value) in (int, float) and math.isfinite(value) and
                value >= 0)
    except OverflowError:
        return False


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _GUARD_NEW_DIRECTORY
    if _STATE_PATH and os.path.lexists(os.path.dirname(_STATE_PATH)):
        return
    directory = os.path.join(tempfile.gettempdir(),
                             FAMILY + ("-") + str(os.getuid()))
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")
    if not os.path.lexists(directory) and not (
            _GUARD_REMOTE_INITIALIZING and
            os.environ.get("GITHUB_ACTIONS") == "true"):
        return
    if (_GUARD_REMOTE_INITIALIZING and
            os.environ.get("GITHUB_ACTIONS") == "true"):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            pass
        else:
            _GUARD_NEW_DIRECTORY = True
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
                    raise AdapterError("Mature publisher lock missing",
                                       "access")
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
        current = os.stat(os.path.basename(_STATE_LOCK),
                          dir_fd=parent,
                          follow_symlinks=False)
        if (current.st_dev,
                current.st_ino) != identity or (_GUARD_LOCK_ID is not None and
                                                _GUARD_LOCK_ID != identity):
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


def write_budget(state):
    global _GUARD_FAILED_STATE
    try:
        validate_budget(state)
    except BaseException:
        _GUARD_FAILED_STATE = copy.deepcopy(state)
        journal_failed()
        raise
    journal_commit(state)


def current_run_identity():
    if (os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline" or
            os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "")
        for key in (("GITHUB_RUN_ID"), ("GITHUB_RUN_ATTEMPT"))
    ]
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))


def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("GRANTS_AT_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Explicit runner temporary directory required",
                           "access")
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    if os.path.basename(path) != "pacing-state.json":
        raise AdapterError("Unsafe pacing artifact path", "access")
    name = os.path.basename(path)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_identity = None
    output_identity = None
    output_handles = ()
    try:
        directory = os.fstat(parent)
        parent_identity = (directory.st_dev, directory.st_ino)
        if (directory.st_uid != os.getuid() or
                not stat.S_ISDIR(directory.st_mode) or
                directory.st_mode & 0o022 or
                directory.st_mode & 0o700 != 0o700):
            raise AdapterError("Unsafe pacing artifact directory owner",
                               "access")
        io_verify_current_parent(parent, root, parent_identity)
        existing = None
        try:
            existing = os.open(name,
                               os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                               dir_fd=parent)
        except FileNotFoundError:
            pass
        if existing is not None:
            existing_identity = None
            try:
                info = os.fstat(existing)
                existing_identity = (info.st_dev, info.st_ino)
                if (info.st_uid != os.getuid() or
                        not stat.S_ISREG(info.st_mode) or
                        info.st_mode & 0o077 or info.st_nlink != 1):
                    raise AdapterError("Unsafe existing pacing artifact owner",
                                       "access")
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (current.st_dev, current.st_ino) != (
                        info.st_dev,
                        info.st_ino,
                ):
                    raise AdapterError("Pacing artifact identity changed",
                                       "access")
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
        if _GUARD_FATAL:
            raise AdapterError(
                "Publisher persistence failure cannot be exported", "access")
        run_id, attempt = current_run_identity()
        with locked_budget():
            state = load_budget()
            retained = retained_hosted_max(state)
            if retained != state:
                write_budget(retained)
                state = retained
        envelope = {
            "schema":
                1,
            "repository":
                "YouthOpps/data-pipeline",
            "run_id":
                run_id,
            "run_attempt":
                attempt,
            "source":
                SOURCE_ID,
            "head_sha":
                os.environ.get("GITHUB_SHA"),
            "adapter_sha256":
                source_digest(),
            "state_sha256":
                hashlib.sha256(
                    json.dumps(state, sort_keys=True,
                               separators=(",", ":")).encode()).hexdigest(),
            "state":
                state,
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
                raise AdapterError("Owned artifact acquisition changed",
                                   "access")
            guard = os.dup(descriptor)
            output_handles = (descriptor, guard)
            io_artifact_descriptor(guard, identity)
            with locked_budget():
                writer = os.dup(descriptor)
                try:
                    io_artifact_descriptor(writer, identity)
                    payload = json.dumps(envelope,
                                         separators=(",", ":")).encode()
                    offset = 0
                    while offset < len(payload):
                        io_verify_current_parent(parent, root, parent_identity)
                        io_artifact_descriptor(writer, identity)
                        written = os.write(writer, payload[offset:])
                        if (type(written) is not int or
                                not 0 < written <= len(payload) - offset):
                            raise AdapterError(
                                "Incomplete owned artifact write", "access")
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
                    current = os.stat(name,
                                      dir_fd=parent,
                                      follow_symlinks=False)
                    if (info.st_uid != os.getuid() or
                            not stat.S_ISREG(info.st_mode) or
                            stat.S_IMODE(info.st_mode) != 0o600 or
                            info.st_nlink != 1 or (info.st_dev, info.st_ino)
                            != (current.st_dev, current.st_ino)):
                        raise AdapterError("Unsafe finalized artifact identity",
                                           "access")
                    reader_identity = (info.st_dev, info.st_ino)
                    before_read = journal_generation(info)
                    raw = os.read(check, 16385)
                    io_verify_current_parent(parent, root, parent_identity)
                    after_read = io_artifact_descriptor(check, reader_identity)
                    current = os.stat(name,
                                      dir_fd=parent,
                                      follow_symlinks=False)
                    if (journal_generation(after_read) != before_read or
                        (current.st_dev, current.st_ino) != identity):
                        raise AdapterError("Artifact changed during read",
                                           "access")
                    if len(raw) > 16384 or json.loads(raw) != envelope:
                        raise AdapterError("Finalized pacing artifact mismatch",
                                           "access")
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
                io_invalidate_owned_artifact(parent, root, name, parent_id,
                                             identity, (guard, descriptor))
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
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW |
                        os.O_NONBLOCK,
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
                        io_close_owned_descriptor(recovery, parent_identity,
                                                  True)
        if close_error is not None and primary is None:
            raise close_error


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
        style = re.sub(r"/\*.*?\*/", "", values.get("style") or "",
                       flags=re.S).lower()
        declarations, priorities = {}, {}
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                name = name.strip()
                important = bool(re.search(r"!\s*important\s*$", value))
                if priorities.get(name) and not important:
                    continue
                declarations[name] = re.sub(r"\s*!\s*important\s*$", "",
                                            value).strip()
                priorities[name] = important
        suppressed = (tag in {
            "script",
            "style",
            "template",
            "noscript",
            "footer",
            "nav",
            "aside",
        } or "hidden" in values or (values.get("aria-hidden") or
                                    "").strip().lower() == "true" or
                      declarations.get("display") == "none")
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
        r"(?:\s|>))",
        prefix,
        re.I,
    )
    if ("html" not in headers.get("Content-Type", "").lower() and
            not obvious_html):
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
                    "html",
                    "head",
                    "title",
                    "meta",
                    "link",
                    "base",
                    "style",
                    "script",
                    "noscript",
                    "template",
            }:
                self.body_started = True
            if (tag == "head" and not self.head_seen and
                    not self.body_started and
                [frame[0] for frame in self.stack] in ([], ["html"])):
                self.head_seen = True
                self.head_depth = len(self.stack) + 1
            super().handle_starttag(tag, attrs)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            if tag == "head":
                self.head_depth = None

        def handle_data(self, data):
            if (data.strip() and self.head_depth is None and
                (not self.stack or not any(self.stack[-1][1:])) and
                    not any(frame[0] in {"head", "title"}
                            for frame in self.stack)):
                self.body_started = True
            if (self.head_depth is not None and not self.body_started and
                    len(self.stack) == self.head_depth + 1 and
                    self.stack[-1][0] == "title" and
                    self.stack[-2][0] == "head" and
                    not any(self.stack[-1][1:])):
                self.title.append(data)

    parser = HeadTitle()
    parser.feed(body.decode("utf-8", "replace"))
    title = " ".join(" ".join(parser.title).lower().split())
    return bool(
        re.search(r"just a moment|attention required|access denied|captcha",
                  title, re.I))


def source_request(url,
                   *,
                   interval=12.2,
                   method="GET",
                   payload=None,
                   robots=False):
    """Use only literal official public hosts; preserve redirect method/body."""
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError("Robots retrieval requires robots.txt", "access")
    current = url
    for hop in range(4):
        parsed = urllib.parse.urlsplit(current)
        if (parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS or
                parsed.username or parsed.password or
                parsed.port not in {None, 443}):
            raise AdapterError("Unreviewed public source URL", "access")
        if not robots:
            interval = max(interval, check_robots(current))
        status, headers, body = request_bytes(
            current,
            interval=interval,
            method=method,
            payload=payload,
            robots=robots,
            headers=({
                "Content-Type": "application/json; charset=utf-8"
            } if payload is not None else None),
        )
        if status in {301, 302, 303, 307, 308}:
            if not headers.get("Location") or hop == 3:
                raise AdapterError("Invalid source redirect", "fetch")
            target = urllib.parse.urljoin(current, headers["Location"])
            if urllib.parse.urlsplit(target).hostname != parsed.hostname:
                raise AdapterError("Redirect changes reviewed source host",
                                   "access")
            if robots and urllib.parse.urlsplit(target).path != "/robots.txt":
                raise AdapterError("Robots redirect leaves the policy route",
                                   "access")
            if method != "GET" and status not in {307, 308}:
                raise AdapterError("Unexpected public POST redirect", "fetch")
            current = target
            continue
        if status != 200 and not robots:
            raise AdapterError(
                "Publisher content returned HTTP " + str(status),
                "fetch",
                status,
            )
        return status, headers, body
    raise AdapterError("Source redirect limit exceeded", "fetch")


def service(method, payload):
    _, _, body = source_request(
        SERVICE_URL + method,
        method="POST",
        payload=json.dumps(payload).encode(),
    )
    try:
        return json.loads(body)
    except (UnicodeError, ValueError):
        raise AdapterError("Invalid public service JSON", "parse") from None


def clean(value):
    if not isinstance(value, str):
        raise AdapterError("Invalid publisher text", "parse")
    text = " ".join(unescape(value).split())
    if not text or re.search(r"<[^>]+>", text):
        raise AdapterError("Empty or nonliteral publisher text", "parse")
    return text


def search(types):
    parameters = ("foerderart:" + ";".join(str(t) for t in types) +
                  ",eingabetyp:4")
    payload = {
        "TemplateID": "44",
        "SearchParameters": urllib.parse.quote(parameters, safe="~()*!.'"),
        "LanguageID": "0",
    }
    result = fetch_inventory_page(service, "STIMAEntrySearchForTextParameters",
                                  payload)
    if not isinstance(result, list) or not result or len(result) > 20000:
        raise AdapterError("Empty or invalid full inventory partition", "parse")
    return result


def row_key(row, types):
    if not isinstance(row, dict):
        raise AdapterError("Invalid programme row", "parse")
    for key in ("StipFoeID", "HZGID", "FoerderArtID", "PubOrgID"):
        if type(row.get(key)) is not int or row[key] < 0:
            raise AdapterError("Invalid native programme identity", "parse")
    if row["StipFoeID"] <= 0 or row["FoerderArtID"] not in types:
        raise AdapterError("Unknown programme or funding category", "parse")
    title, label = clean(row.get("StipName")), clean(row.get("FoerderArtText"))
    if label != types[row["FoerderArtID"]]:
        raise AdapterError("Changed publisher funding taxonomy", "parse")
    institution = " ".join(row.get("PubOrgName", "").split())
    return (
        row["StipFoeID"],
        row["HZGID"],
        row["FoerderArtID"],
        title,
        institution,
    )


def programme_url(identifier, group):
    # This is the publisher's own Copy Link format. The optional third part is
    # a result-list position, ignored by its detail reader; fix it at zero.
    native = f"{identifier}_{group}_0".encode("ascii")
    return SOURCE_URL + "?=" + base64.b64encode(native).decode("ascii")


def add_ernst_mach_fh_call(record):
    """Read the application deadline from the official award FAQ."""
    if record["title"].casefold() != ERNST_MACH_FH_TITLE.casefold():
        raise AdapterError("Ernst Mach FH programme identity changed", "parse")
    visible = VisibleText()
    visible.feed(fetch_source(ERNST_MACH_FH_FAQ))
    visible.close()
    text = " ".join(" ".join(visible.text).split())
    eligibility = (
        r"studying a Master['’]s or a Bachelor['’]s programme at a university "
        r"in one of the defined countries of origin",
        r"Bachelor students must also have successfully completed at least "
        r"4 semesters",
        r"The maximum age is 35 years",
        r"Applicants need to be accepted as exchange scholars from an Austrian "
        r"university of applied sciences",
    )
    if any(not re.search(rule, text, re.IGNORECASE) for rule in eligibility):
        raise AdapterError("Changed Ernst Mach FH eligibility wording", "parse")
    matches = set(re.findall(
        r"\bThe deadline for all applications for stays during the academic year "
        r"(\d{4})\s*/\s*(\d{2}|\d{4}) is February (\d{1,2})"
        r"(?:st|nd|rd|th)?,\s*(\d{4}),\s*(\d{2}):(\d{2})\s*"
        r"\(CET(?:\s*[-–]\s*Central European Time)?\)",
        text, flags=re.IGNORECASE,
    ))
    if len(matches) != 1:
        raise AdapterError("Missing or conflicting Ernst Mach FH call", "parse")
    start, end, day, year, hour, minute = matches.pop()
    next_year = str(int(start) + 1)
    if year != start or end not in {next_year, next_year[-2:]}:
        raise AdapterError("Inconsistent Ernst Mach FH call year", "parse")
    try:
        deadline = datetime(
            int(year), 2, int(day), int(hour), int(minute),
            tzinfo=timezone(timedelta(hours=1)),
        )
    except ValueError:
        raise AdapterError("Invalid Ernst Mach FH deadline", "parse") from None
    record["deadline"] = deadline.astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    record["host_countries"] = ["AT"]
    record["summary"] = (
        f"Funding for a {start}/{next_year} exchange or visiting stay at an "
        "Austrian university of applied sciences. Applicants must study for "
        "a bachelor's or master's degree at a university in an eligible "
        "non-European country and secure acceptance from an Austrian host. "
        "Bachelor's students need four completed semesters. The maximum age "
        "is 35."
    )
    record["classification"]["evidence"].append(
        "Call deadline and prior acceptance: " + ERNST_MACH_FH_FAQ
    )


def collect_body():
    """Read the complete public funding index and reconcile every partition."""
    home = fetch_source(SOURCE_URL)
    script = fetch_source(SCRIPT_URL)
    imprint = fetch_source(IMPRINT_URL)
    if SCRIPT_URL not in home or not re.search(
            r'new STIMAQuery\.View\("STIMAQueryContainer",\s*2,\s*44\)', home):
        raise AdapterError("Publisher UI configuration changed", "access")
    for literal in (
            "GetMetadata",
            "STIMAEntrySearchForTextParameters",
            "GetDetailHtml",
            "StipFoeID",
            "HZGID",
            'URLPARAMETER=""',
            "_copyToClipboard",
    ):
        if literal not in script:
            raise AdapterError("Publisher public interface changed", "access")
    # Ordinary attributed text excerpts are permitted; this index does not
    # embed a live database widget, copy descriptions or provide a database API.
    if ("The publication of excerpts of the OeAD website is generally permitted"
            not in unescape(re.sub(r"<[^>]+>", " ", imprint))):
        raise AdapterError("OeAD text-excerpt access evidence changed",
                           "access")
    meta = fetch_inventory_page(service, "GetMetadata", {
        "TemplateID": "44",
        "LanguageID": "0"
    })
    if (not isinstance(meta, dict) or meta.get("TemplateID") != 44 or
            not isinstance(meta.get("FoerderArten"), list)):
        raise AdapterError("Invalid native metadata", "parse")
    types = {}
    for item in meta["FoerderArten"]:
        identifier = item.get("ID")
        if (type(identifier) is not int or identifier <= 0 or
                identifier in types):
            raise AdapterError("Duplicate or invalid taxonomy identity",
                               "parse")
        types[identifier] = clean(item.get("T"))
    if not types:
        raise AdapterError("No publisher funding categories", "parse")
    rows = search(list(types))
    all_rows = [row_key(row, types) for row in rows]
    if len(set(all_rows)) != len(all_rows):
        raise AdapterError("Duplicate native inventory row", "parse")
    verified = set()
    for identifier in types:
        part = [row_key(row, types) for row in search([identifier])]
        if any(row[2] != identifier for row in part) or len(
                set(part)) != len(part):
            raise AdapterError("Invalid funding partition", "parse")
        verified.update(part)
    if verified != set(all_rows):
        raise AdapterError("Public complete inventory changed or was truncated",
                           "parse")
    programmes = {}
    for identifier, group, funding, title, institution in all_rows:
        key = identifier, group
        if key not in programmes:
            programmes[key] = {
                "title": title,
                "institution": institution,
                "types": set(),
            }
        entry = programmes[key]
        if entry["title"] != title or entry["institution"] != institution:
            raise AdapterError("Conflicting native programme facts", "validate")
        entry["types"].add(funding)
    records = []
    for (identifier, group), item in sorted(programmes.items()):
        labels = [types[t] for t in sorted(item["types"])]
        url = programme_url(identifier, group)
        record = make_record(
            item["title"],
            url,
            ["grants"],
            kind="programme-overview",
            tags=labels,
            method="publisher-funding-index",
            evidence=["Funding category: " + label for label in labels],
        )
        record["id"] = f"{SOURCE_ID}-{identifier}-{group}"
        # The shallow public index supplies no verified deadlines, destination
        # countries or applicant citizenship. None are inferred from the title.
        record["summary"] = (
            "Funding programme listed in grants.at. Categories: " +
            "; ".join(labels) +
            ". Check the original publisher for current application conditions."
        )
        record["summary_language"] = "en"
        if item["institution"]:
            record["institution"] = item["institution"]
        if (identifier, group) == ERNST_MACH_FH_KEY:
            add_ernst_mach_fh_call(record)
        records.append(record)
    validate_records(records)
    print(
        json.dumps({
            "source": SOURCE_ID,
            "raw_category_rows": len(rows),
            "funding_partitions": len(types),
            "programme_records": len(records),
            "scope": "complete-basic-programme-index",
        }),
        file=sys.stderr,
    )
    return records


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help=("Write only this source to YouthOpps/data-source "
              "using DATA_SOURCE_TOKEN"),
    )
    args = parser.parse_args()
    if args.publish and _FH_NATIVE_HANDOFF is None:
        raise AdapterError("Reviewed Ernst Mach FH native handoff is OFF", "access")
    global _RUN_END
    if _RUN_END is None:
        _RUN_END = time.monotonic() + 1740
    snapshot = read_snapshot() if args.publish else None
    old = (json.loads(snapshot["files"]["data.json"])
           if snapshot and snapshot["files"]["data.json"] is not None else [])
    previous = (json.loads(snapshot["files"]["metadata.json"]) if snapshot and
                snapshot["files"]["metadata.json"] is not None else {})
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, published=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish")
    attempt, failure = utc_now(), None
    try:
        records = collect()
        checked_at = utc_now()
        validate_records(records)
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
                updated_at=(before.get("updated_at", attempt)
                            if same else attempt),
                last_seen_at=attempt,
                last_checked_at=checked_at,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(key=lambda record: record["published_at"] or "",
                     reverse=True)
        validate_records(records)
        records = remove_expired_records(records, checked_at)
        validate_records(records, published=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
    if args.publish and (not failure or
                         snapshot["files"]["data.json"] is not None):
        files = {
            "metadata.json":
                json.dumps(outcome, ensure_ascii=False, indent=2) + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n")
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(
                attempt,
                previous,
                old,
                AdapterError("Publication failed: " + safe_error(error),
                             "publish"),
            )
            try:
                latest = read_snapshot()
                if (snapshot["files"]["data.json"] is None or
                        latest["files"] != snapshot["files"]):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files(
                    latest,
                    {
                        "metadata.json":
                            json.dumps(outcome, ensure_ascii=False, indent=2) +
                            "\n"
                    },
                )
            except Exception:
                outcome["message"] = (
                    "Publication failed; durable failure status could not be "
                    "recorded. Remote data was not force-overwritten.")
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created")
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


def main():
    try:
        return run()
    finally:
        primary = sys.exc_info()[1]
        try:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                if primary is None:
                    export_family_artifact()
                else:
                    with contextlib.suppress(BaseException):
                        export_family_artifact()
        finally:
            journal_finish()


_GUARD_PARENT = _GUARD_PARENT_ID = None
_GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
_GUARD_LOCK_ID = None
_GUARD_LOCKS = {}
_GUARD_CREATED = _GUARD_DIRTY = _GUARD_FATAL = False
_GUARD_COLLECTION_ID = None
_GUARD_NEW_DIRECTORY = False
_GUARD_FAILED_STATE = None
_GUARD_REMOTE_INITIALIZING = False


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
    if fresh:
        descriptor = os.open(directory,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    identity = None
    try:
        held = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (held.st_dev, held.st_ino)
        if (not stat.S_ISDIR(held.st_mode) or held.st_uid != os.getuid() or
                stat.S_IMODE(held.st_mode) != 0o700 or held.st_nlink < 2 or
                identity != (current.st_dev, current.st_ino) or
                not fresh and identity != _GUARD_PARENT_ID):
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
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or
                info.st_nlink not in ((0, 1) if unlinked else
                                      (1,)) or identity is not None and
            (info.st_dev, info.st_ino) != identity):
            raise AdapterError("Publisher descriptor ownership changed",
                               "access")
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
            raise AdapterError("Publisher journal authority unavailable",
                               "access")
        parent = journal_parent()
        info = journal_owned(_GUARD_FD, _GUARD_ID)
        current = os.stat(
            os.path.basename(_STATE_PATH),
            dir_fd=parent,
            follow_symlinks=False,
        )
        if ((current.st_dev, current.st_ino) != _GUARD_ID or
                check_generation and journal_generation(info) != _GUARD_VERSION
                or fcntl.fcntl(_GUARD_FD, fcntl.F_GETFL) & os.O_ACCMODE
                != os.O_RDWR):
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
    if (raw != expected or len(raw) != after.st_size or
            journal_generation(before) != journal_generation(after)):
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
            raise AdapterError("Bounded locked publisher commit required",
                               "access")
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
            if (type(written) is not int or
                    not 0 < written <= len(payload) - offset):
                raise AdapterError("Incomplete publisher journal write",
                                   "access")
            offset += written
            journal_acknowledge(payload[:offset])
        os.fsync(_GUARD_FD)
        before = journal_verify()
        raw = os.pread(_GUARD_FD, len(payload) + 1, 0)
        after = journal_verify()
        if raw != payload or journal_generation(before) != journal_generation(
                after):
            raise AdapterError("Publisher journal readback changed", "access")
        _GUARD_VERSION = journal_generation(after)
        _GUARD_DIRTY = False
    except BaseException:
        _GUARD_FAILED_STATE = json.loads(json.dumps(state))
        journal_failed()
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
    if ((info.st_dev, info.st_ino) != expected or info.st_uid != os.getuid() or
            info.st_mode & 0o022 or
            directory and not stat.S_ISDIR(info.st_mode) or not directory and
        (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
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
            if ((info.st_dev, info.st_ino) != expected or
                    not stat.S_ISREG(info.st_mode) or
                    info.st_uid != os.getuid() or info.st_mode & 0o022):
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
    if ((info.st_dev, info.st_ino) != expected or
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink not in (0, 1)):
        raise AdapterError("Owned artifact descriptor changed", "access")
    return info


def io_invalidate_owned_artifact(parent, root, name, parent_id, identity,
                                 handles):
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
        descriptor = os.open(name,
                             os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
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


def validate_legacy_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) != {
            "schema",
            "family",
            "observed_at",
            "not_before",
            "starts",
            "blocked",
            "interval",
            "page_interval",
            "rate_limit_strikes",
            "rate_limit_until",
            "completed",
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
    if (type(state["schema"]) is not int or state["schema"] != 1 or
            state["family"] != FAMILY or not numeric(state["observed_at"]) or
            not numeric(state["not_before"]) or
            type(state["blocked"]) is not bool or
            not isinstance(state["starts"], list) or
            len(state["starts"]) > 10 or
            any(not numeric(t) for t in state["starts"]) or
            state["starts"] != sorted(state["starts"]) or
            len(set(state["starts"])) != len(state["starts"]) or
            any(t > state["observed_at"] for t in state["starts"])):
        raise AdapterError("Invalid publisher pacing history", "access")
    if (any(not numeric(state.get(key)) for key in (
            "interval",
            "page_interval",
            "rate_limit_until",
            "completed",
    )) or state["interval"] < 12.2 or state["page_interval"] < 30 or
            type(state["rate_limit_strikes"]) is not int or
            not 0 <= state["rate_limit_strikes"] <= 100):
        raise AdapterError("Invalid persisted pacing/backoff", "access")
    if state["completed"] > state["observed_at"]:
        raise AdapterError("Invalid publisher completion timestamp", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    return state


def legacy_owned_envelope(value):
    """Accept only the independently reviewed last actual six-key owner."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != _LEGACY_ENVELOPE_SHA256:
        raise AdapterError("Unreviewed legacy owned state", "access")
    validate_legacy_budget(value["state"])
    return value


def migrate_owned_state(value):
    """Retain every legacy byte/fact and explicitly unknown older history."""
    legacy_owned_envelope(value)
    state = copy.deepcopy(value["state"])
    state.update(
        schema=2,
        inventory_completed=value["state"]["completed"],
        pending=False,
        pending_id=None,
        pending_kind=None,
        pending_started=0,
        completed_id=None,
        legacy_history_unknown=True,
        legacy_owned=copy.deepcopy(value),
    )
    # The successful legacy run has no exact inventory completion receipt.
    # Its final physical completion is the conservative inventory upper bound.
    return validate_budget(state)


def validate_budget(state):
    """Require modern provenance and unresolved ownership without age expiry."""
    extra = {
        "inventory_completed", "pending", "pending_id", "pending_kind",
        "pending_started", "completed_id", "legacy_history_unknown",
        "legacy_owned"
    }
    if not isinstance(state, dict) or not extra <= state.keys():
        raise AdapterError("Missing modern owned-state provenance", "access")
    if type(state["schema"]) is not int or state["schema"] != 2:
        raise AdapterError("Unsupported publisher state schema", "access")
    base = {key: value for key, value in state.items() if key not in extra}
    base["schema"] = 1
    starts = base.get("starts")
    if (not isinstance(starts, list) or len(starts) > 1000 or
            any(not numeric(value) for value in starts) or
            starts != sorted(set(starts))):
        raise AdapterError("Invalid retained physical history", "access")
    # The legacy numeric validator is unchanged; its ten-item representation
    # bound is superseded only for explicitly retained modern observations.
    base["starts"] = starts[-10:]
    validate_legacy_budget(base)
    try:
        for key in ("observed_at", "not_before", "rate_limit_until",
                    "completed", "inventory_completed", "pending_started"):
            datetime.fromtimestamp(state[key], timezone.utc)
    except (ValueError, TypeError, OverflowError, OSError):
        raise AdapterError("Unrepresentable retained timestamp",
                           "access") from None
    original = legacy_owned_envelope(state["legacy_owned"])["state"]
    for key in ("not_before", "interval", "page_interval", "rate_limit_strikes",
                "rate_limit_until", "completed"):
        if state[key] < original[key]:
            raise AdapterError("Legacy restriction was weakened", "access")
    if (state["legacy_history_unknown"] is not True or
            type(state["pending"]) is not bool or
            not numeric(state["pending_started"]) or
            not numeric(state["inventory_completed"]) or
            state["inventory_completed"] < original["completed"] or
            state["inventory_completed"] > state["observed_at"] or
            state["pending_started"] > state["observed_at"] or
            any(value > state["observed_at"] for value in starts) or
            original["blocked"] and not state["blocked"]):
        raise AdapterError("Invalid pending/history provenance", "access")
    for key in ("pending_id", "completed_id"):
        value = state[key]
        if value is not None and (not isinstance(value, str) or
                                  not re.fullmatch(r"[0-9a-f]{32}", value)):
            raise AdapterError("Invalid transaction identity", "access")
    if state["pending"]:
        if (state["pending_id"] is None or
                state["pending_kind"] not in {"physical", "policy"} or
                state["pending_started"] <= 0):
            raise AdapterError("Unidentified pending transaction", "access")
    elif state["pending_id"] is not None or state["pending_kind"] is not None:
        raise AdapterError("Settled state retains a pending owner", "access")
    if len(json.dumps(state, separators=(",", ":")).encode()) > 16384:
        raise AdapterError("Publisher state exceeds owned byte bound", "access")
    return state


def merge_budget(local, incoming):
    """MAX restrictions and event ownership; older settlement never wins."""
    validate_budget(local)
    validate_budget(incoming)
    if local["legacy_owned"] != incoming["legacy_owned"]:
        raise AdapterError("Conflicting legacy family provenance", "access")
    result = copy.deepcopy(
        max((local, incoming), key=lambda s: s["observed_at"]))
    for key in ("observed_at", "not_before", "interval", "page_interval",
                "rate_limit_strikes", "rate_limit_until", "completed",
                "inventory_completed"):
        result[key] = max(local[key], incoming[key])
    result["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    result["blocked"] = local["blocked"] or incoming["blocked"]
    pending = [s for s in (local, incoming) if s["pending"]]
    if pending:
        if len({s["pending_id"] for s in pending}) > 1:
            raise AdapterError("Conflicting unresolved publisher owners",
                               "access")
        for key in ("pending", "pending_id", "pending_kind", "pending_started"):
            result[key] = pending[0][key]
    return validate_budget(result)


def empty_budget(_now):
    raise AdapterError("An established source has no fresh empty allowance",
                       "access")


def load_budget():
    raw = journal_read()
    if not raw and _GUARD_CREATED and journal_creation_allowed():
        if _RESTORE_INCOMING is None:
            raise AdapterError("No exact incoming owner for journal creation",
                               "access")
        return copy.deepcopy(validate_budget(_RESTORE_INCOMING))
    if not raw or len(raw) > 16384:
        raise AdapterError("Missing or oversized mature publisher state",
                           "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher pacing state", "access") from None


@contextlib.contextmanager
def journal_restore_scope(incoming):
    """Fresh hosted journals require exact reviewed owned state, never zeroes."""
    global _GUARD_REMOTE_INITIALIZING, _RESTORE_INCOMING
    validate_budget(incoming)
    if _GUARD_REMOTE_INITIALIZING:
        raise AdapterError("Nested publisher restore refused", "access")
    _GUARD_REMOTE_INITIALIZING = os.environ.get("GITHUB_ACTIONS") == "true"
    _RESTORE_INCOMING = copy.deepcopy(incoming)
    try:
        yield
    finally:
        _GUARD_REMOTE_INITIALIZING = False
        _RESTORE_INCOMING = None


def remaining_time():
    limits = [
        value for value in (_RUN_END, _COLLECTION_END) if value is not None
    ]
    if not limits or min(limits) <= time.monotonic():
        raise AdapterError("Absolute operation deadline exhausted", "access")
    return min(limits) - time.monotonic()


def request_timeout():
    return min(30, remaining_time())


@contextlib.contextmanager
def wire_deadline(publisher):
    """Interrupt slow bodies and closure at one nonrenewable phase deadline."""
    if (threading.current_thread() is not threading.main_thread() or
            signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)):
        raise AdapterError("Exclusive main-thread deadline authority required",
                           "access")
    previous = signal.getsignal(signal.SIGALRM)
    cancelling = False

    def expired(_number, _frame):
        nonlocal cancelling
        global _PUBLISHER_GATE_FAILED
        if publisher:
            _PUBLISHER_GATE_FAILED = True
        if cancelling:
            signal.setitimer(signal.ITIMER_REAL, 0)
            raise AdapterError("Response cancellation grace exhausted",
                               "access")
        cancelling = True
        signal.setitimer(signal.ITIMER_REAL, 0.25)
        raise AdapterError("Absolute deadline interrupted response I/O",
                           "access")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, remaining_time())
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def available_state(state):
    validate_budget(state)
    if _PUBLISHER_GATE_FAILED or _GUARD_FATAL or _COLLECTION_CLOSED or state[
            "pending"]:
        raise AdapterError("Unresolved publisher transaction retained",
                           "access")
    if state["blocked"]:
        raise AdapterError("Retained publisher refusal requires review",
                           "access")
    if state["rate_limit_strikes"] >= 100:
        raise AdapterError("Publisher rate-limit history requires review",
                           "access")
    if state["rate_limit_until"] > time.time():
        raise AdapterError("Retained rate-limit embargo remains active",
                           "access", 429)


def pacing_wait(state, inventory):
    now, mono = time.time(), time.monotonic()
    starts = [value for value in state["starts"] if now - value < 60]
    wall = max(state["not_before"], state["completed"] + state["interval"],
               starts[-1] + state["interval"] if starts else now,
               starts[-5] + 60 if len(starts) >= 5 else now)
    if _STARTUP_QUIET_UNTIL is None:
        raise AdapterError("Publisher startup quiet not established", "access")
    target = _STARTUP_QUIET_UNTIL
    if _PHYSICAL_COMPLETED_MONO is not None:
        target = max(target, _PHYSICAL_COMPLETED_MONO + state["interval"])
    if inventory:
        wall = max(wall, state["inventory_completed"] + state["page_interval"])
        if _INVENTORY_COMPLETED_MONO is not None:
            target = max(target,
                         _INVENTORY_COMPLETED_MONO + state["page_interval"])
    return max(0, wall - now, target - mono)


def begin_pending(state, kind):
    available_state(state)
    state.update(pending=True,
                 pending_id=os.urandom(16).hex(),
                 pending_kind=kind,
                 pending_started=time.time(),
                 observed_at=time.time())
    write_budget(state)


def settle_pending(state):
    global _GUARD_FAILED_STATE
    if not state["pending"] or state["pending_id"] is None:
        raise AdapterError("Missing exact pending owner", "access")
    candidate = copy.deepcopy(state)
    candidate["completed_id"] = candidate["pending_id"]
    candidate.update(pending=False,
                     pending_id=None,
                     pending_kind=None,
                     observed_at=time.time())
    try:
        write_budget(candidate)
    except BaseException:
        _GUARD_FAILED_STATE = copy.deepcopy(state)
        raise
    state.clear()
    state.update(candidate)


def pace(url, interval=12.2, robots=False):
    """Wait under the actual journal lock before reserving one physical owner."""
    global _PAGE_INTERVAL
    lock = locked_budget()
    try:
        state = load_budget()
        available_state(state)
        state["interval"] = max(state["interval"], interval, 12.2)
        _PAGE_INTERVAL = max(_PAGE_INTERVAL, state["page_interval"], 30)
        while True:
            remaining = remaining_time()
            delay = pacing_wait(state, _INVENTORY_ACTIVE and not robots)
            if delay + 1 >= remaining:
                raise AdapterError("Retained pacing exceeds fixed phase budget",
                                   "access")
            if delay <= 0:
                break
            time.sleep(min(1, delay))
        # Durable transaction precedes every physical reservation and opener.
        begin_pending(state, "physical")
        return lock, state
    except BaseException:
        lock.close()
        raise


def visible_refusal(body, headers, human_only=False):
    if human_only and has_429_head_refusal(body, headers):
        return True
    content_type = headers.get("Content-Type", "").lower()
    if "html" not in content_type and "text/plain" not in content_type:
        return False
    parser = VisibleText()
    parser.feed(body.decode("utf-8", "replace"))
    text = " ".join(" ".join(parser.text).lower().split())
    phrases = ("verify you are human", "verify that you are human",
               "complete the captcha",
               "enable javascript and cookies to continue",
               "checking your browser")
    if not human_only:
        phrases += ("access denied", "permission denied", "request denied",
                    "request blocked", "unusual traffic")
    return any(value in text for value in phrases)


def apply_policy(parser, target, state):
    if parser is None:
        return state["interval"]
    policies = [parser]
    for entry in parser.entries:
        if entry.applies_to(_USER_AGENT):
            single = copy.copy(parser)
            single.entries, single.default_entry = [entry], None
            policies.append(single)
    if not all(policy.can_fetch(_USER_AGENT, target) for policy in policies):
        state["blocked"] = True
        write_budget(state)
        raise AdapterError("Publisher robots disallows this route", "access")
    interval = max(state["interval"], robots_timing_delay(parser))
    for policy in policies:
        delay, rate = policy.crawl_delay(_USER_AGENT), policy.request_rate(
            _USER_AGENT)
        if delay is not None:
            if not numeric(delay):
                raise AdapterError("Unassessed robots delay", "access")
            interval = max(interval, delay)
        if rate:
            if rate.requests <= 0 or not numeric(rate.seconds):
                raise AdapterError("Invalid robots request rate", "access")
            interval = max(interval, rate.seconds / rate.requests)
    state["interval"] = max(interval, 12.2)
    write_budget(state)
    return state["interval"]


def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True

def retry_embargo(value):
    """Retain supported future timing; never discard positive overflow."""
    if value is None:
        return 0
    if not isinstance(value, str):
        raise AdapterError("Unsupported Retry-After field", "access")
    value = value.strip()
    numeric_form = re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
    if numeric_form:
        try:
            delay = float(value)
            until = time.time() + delay
            if not numeric(delay) or not supported_epoch(until):
                raise ValueError("Unsupported positive duration")
        except (ValueError, OverflowError, OSError):
            raise AdapterError("Unsupported positive Retry-After duration",
                               "access") from None
        return until if delay > 0 and until > time.time() else 0
    # Numeric-looking positive extensions cannot establish an unbounded grant.
    if (re.fullmatch(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
                     r"(?:[eE][+-]?[0-9]+)?", value)
            or value.lower() in {"inf", "+inf", "infinity", "+infinity"}):
        raise AdapterError("Unsupported positive Retry-After syntax", "access")
    try:
        date = parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        # An explicitly out-of-range future year is restrictive but cannot
        # be represented in this runtime's durable timestamp schema.
        year = re.search(r"\b[0-9]{1,2} [A-Za-z]{3} ([0-9]{5,})\b", value)
        if year:
            raise AdapterError("Unsupported Retry-After date", "access")
        return 0
    if date.utcoffset() is None:
        return 0
    try:
        until = date.timestamp()
        if not supported_epoch(until):
            raise ValueError("Unsupported date timestamp")
    except (ValueError, OverflowError, OSError):
        raise AdapterError("Unsupported Retry-After date", "access") from None
    return until if until > time.time() else 0

def retry_after_constraints(headers):
    """Inspect every observed field, retaining MAX even alongside overflow."""
    if hasattr(headers, "get_all"):
        values = headers.get_all("Retry-After") or []
    else:
        values = [value for key, value in headers.items()
                  if key.lower() == "retry-after"]
    maximum, unsupported = 0, False
    for value in values:
        # Combined numeric fields contain no date comma ambiguity.
        parts = (value.split(",") if isinstance(value, str) and re.fullmatch(
            r"\s*[0-9]+(?:\.[0-9]+)?"
            r"(?:\s*,\s*[0-9]+(?:\.[0-9]+)?)+\s*", value) else [value])
        if (len(parts) == 1 and isinstance(value, str) and "," in value
                and not re.fullmatch(r"[A-Za-z]{3,9},[^,]+", value.strip())):
            # The email date parser accepts prefixes. Do not silently lose a
            # second combined field or guess how ambiguous dates were joined.
            unsupported = True
            continue
        for part in parts:
            try:
                maximum = max(maximum, retry_embargo(part))
            except AdapterError:
                unsupported = True
    return maximum, unsupported

def robots_response_text(headers, body):
    """An HTTP200 policy needs one explicit plaintext representation."""
    if hasattr(headers, "get_all"):
        types = headers.get_all("Content-Type") or []
    else:
        types = [value for key, value in headers.items()
                 if key.lower() == "content-type"]
    if (len(types) != 1 or not isinstance(types[0], str)
            or types[0].split(";", 1)[0].strip().lower() != "text/plain"):
        raise AdapterError("Unsupported robots representation", "access")
    text = body.decode(headers.get_content_charset() or "utf-8-sig", "strict")
    text = text.removeprefix("\ufeff")
    meaningful = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    if re.search(r"<!--|-->|<!doctype\b|</?[A-Za-z][^>]*>", meaningful, re.I):
        raise AdapterError("HTML is not a robots policy", "access")
    return text


def robots_timing_groups(text):
    """Retain timing fields that the standard parser silently drops."""
    groups = []
    agents, timing, directives = [], [], False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise AdapterError("Unassessed robots directive", "access")
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if not value:
                raise AdapterError("Empty robots agent", "access")
            if directives:
                groups.append((agents, timing))
                agents, timing, directives = [], [], False
            agents.append(value)
        else:
            directives = True
            if key in ("crawl-delay", "request-rate"):
                if not agents:
                    raise AdapterError("Ungrouped robots timing", "access")
                timing.append((key, value))
    groups.append((agents, timing))
    return groups


def robots_timing_delay(parser):
    """Assess every applicable finite decimal delay/rate before settlement."""
    delay = 12.2
    product = _USER_AGENT.split("/", 1)[0].lower()
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    for agents, timing in getattr(parser, "_timing_groups", []):
        if not any(agent == "*" or agent.lower() in product
                   for agent in agents):
            continue
        for key, value in timing:
            try:
                if key == "crawl-delay":
                    if not re.fullmatch(decimal, value):
                        raise ValueError()
                    seconds = float(value)
                else:
                    match = re.fullmatch(
                        r"([0-9]+)\s*/\s*(" + decimal + r")", value)
                    if not match:
                        raise ValueError()
                    count, period = int(match[1]), float(match[2])
                    if count <= 0 or not numeric(period) or period <= 0:
                        raise ValueError()
                    seconds = period / count
                if (not numeric(seconds) or
                        not supported_epoch(time.time() + seconds)):
                    raise ValueError()
                delay = max(delay, seconds)
            except (ValueError, OverflowError, OSError):
                raise AdapterError("Unassessed robots timing; pending retained",
                                   "access") from None
    return delay


def assess_robots(body, headers, status, url, state):
    origin = urllib.parse.urlsplit(url)
    origin = origin.scheme + "://" + origin.netloc
    target = (_ROBOTS_CONTEXT or {}).get("target", origin + "/")
    if 400 <= status < 500 and status != 429:
        parser = None
    elif status == 200:
        text = robots_response_text(headers, body)
        meaningful = "\n".join(line.split("#", 1)[0]
                               for line in text.splitlines())
        if meaningful.strip() and not any(
                re.match(r"(?i)\s*(?:user-agent|sitemap)\s*:", line)
                for line in meaningful.splitlines()):
            raise AdapterError("Unassessed robots policy document", "access")
        timing = robots_timing_groups(text)
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        parser._timing_groups = timing
    else:
        raise AdapterError("Robots temporarily unavailable", "access", status)
    # Cache only after the exact current target and every delay were assessed.
    delay = apply_policy(parser, target, state)
    _ROBOTS[origin] = parser
    if _ROBOTS_CONTEXT is not None:
        _ROBOTS_CONTEXT["delay"] = delay


def check_robots(url):
    global _ROBOTS_CONTEXT, _PUBLISHER_GATE_FAILED
    parsed = urllib.parse.urlsplit(url)
    origin = parsed.scheme + "://" + parsed.netloc
    with locked_budget():
        available_state(load_budget())
    if origin not in _ROBOTS:
        previous = _ROBOTS_CONTEXT
        _ROBOTS_CONTEXT = {"target": url, "delay": None}
        try:
            source_request(origin + "/robots.txt", robots=True)
            return _ROBOTS_CONTEXT["delay"]
        finally:
            _ROBOTS_CONTEXT = previous
    with locked_budget():
        state = load_budget()
        begin_pending(state, "policy")
        try:
            delay = apply_policy(_ROBOTS[origin], url, state)
        except AdapterError:
            if state["blocked"] and not _GUARD_FATAL:
                settle_pending(state)
            else:
                _PUBLISHER_GATE_FAILED = True
            raise
        except BaseException:
            _PUBLISHER_GATE_FAILED = True
            raise
        settle_pending(state)
        return delay


def request_bytes(url,
                  *,
                  interval=12.2,
                  method="GET",
                  headers=None,
                  payload=None,
                  byte_limit=5_000_000,
                  robots=False):
    """Retain one pending owner through headers, bounded body, policy and close."""
    global _PUBLISHER_GATE_FAILED, _PHYSICAL_COMPLETED_MONO
    global _INVENTORY_COMPLETED_MONO, _LOCAL_FIRST_ACCESS_CHECKED
    parsed = urllib.parse.urlsplit(url)
    publisher = parsed.hostname in _ALLOWED_HOSTS
    if (parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port or not parsed.hostname or
            not publisher and parsed.hostname != "api.github.com" and
            not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com"))):
        raise AdapterError("Unreviewed transport origin", "access")
    if robots and (not publisher or
                   urllib.parse.urlsplit(url).path != "/robots.txt"):
        raise AdapterError("Robots purpose requires the literal policy route",
                           "access")
    lock, state = pace(url, interval, robots) if publisher else (None, None)
    response = None
    assessed = closed = attempted = False
    try:
        request = urllib.request.Request(url,
                                         data=payload,
                                         method=method,
                                         headers={
                                             **(headers or {}), "User-Agent":
                                                 _USER_AGENT
                                         })
        if publisher:
            if os.environ.get("GITHUB_ACTIONS"
                             ) != "true" and not _LOCAL_FIRST_ACCESS_CHECKED:
                verify_local_pause()
                _LOCAL_FIRST_ACCESS_CHECKED = True
            journal_preopener()
            if pacing_wait(state, _INVENTORY_ACTIVE and not robots) > 0:
                raise AdapterError("Physical pre-wire pacing guard refused",
                                   "access")
            state["starts"] = [
                v for v in state["starts"] if time.time() - v < 60
            ]
            state["starts"].append(time.time())
            state["observed_at"] = time.time()
            write_budget(state)
            journal_preopener()
        with wire_deadline(publisher):
            try:
                attempted = True
                try:
                    response = _OPENER.open(request, timeout=request_timeout())
                except urllib.error.HTTPError as error:
                    response = error
                status = response.status
                if publisher:
                    if status == 429:
                        strikes = state["rate_limit_strikes"] + 1
                        state.update(rate_limit_strikes=strikes,
                                     interval=max(state["interval"] * 2,
                                                  12.2 * 2**strikes),
                                     page_interval=max(
                                         state["page_interval"] * 2,
                                         30 * 2**strikes))
                        state["not_before"] = max(
                            state["not_before"],
                            time.time() + 1800 * 2**(strikes - 1))
                        state["rate_limit_until"] = max(
                            state["rate_limit_until"], state["not_before"])
                    if status in (401, 403, 451) and not robots:
                        state["blocked"] = True
                    # Known status restrictions precede any header assessment.
                    state["observed_at"] = time.time()
                    write_budget(state)
                    embargo, unsupported = retry_after_constraints(
                        response.headers)
                    if embargo > state["not_before"]:
                        state["not_before"] = embargo
                        if status == 429:
                            state["rate_limit_until"] = max(
                                state["rate_limit_until"], embargo)
                        state["observed_at"] = time.time()
                        write_budget(state)
                    if unsupported:
                        raise AdapterError(
                            "Unsupported Retry-After; pending retained",
                            "access", status)
                lengths = response.headers.get_all("Content-Length", [])
                if (any(not value.isdigit() for value in lengths) or
                        len(set(lengths)) > 1 or
                        lengths and response.headers.get("Transfer-Encoding") or
                        lengths and int(lengths[0]) > byte_limit or
                        response.headers.get("Content-Encoding",
                                             "identity") != "identity"):
                    raise AdapterError("Unassessed response framing", "access")
                body = response.read(byte_limit + 1)
                if (not isinstance(body, bytes) or len(body) > byte_limit or
                        lengths and len(body) != int(lengths[0])):
                    raise AdapterError("Incomplete or oversized response body",
                                       "access")
                if publisher:
                    human = status == 429 or robots and 400 <= status < 500
                    if visible_refusal(body, response.headers, human):
                        state["blocked"] = True
                        write_budget(state)
                    if state["blocked"]:
                        assessed = True
                        raise AdapterError(
                            "Visible publisher verification or refusal",
                            "access", status)
                    if status == 429:
                        assessed = True
                        raise AdapterError(
                            "Publisher429; adaptive cooldown retained",
                            "access", 429)
                    if robots:
                        try:
                            assess_robots(body, response.headers, status, url,
                                          state)
                        except AdapterError:
                            assessed = state["blocked"]
                            raise
                    assessed = True
                return status, response.headers, body
            finally:
                if response is not None:
                    response.close()
                    closed = True
    finally:
        if lock is not None:
            primary = sys.exc_info()[0] is not None
            try:
                if attempted:
                    _PHYSICAL_COMPLETED_MONO = time.monotonic()
                    state["completed"] = state["observed_at"] = time.time()
                    if _INVENTORY_ACTIVE and not robots:
                        _INVENTORY_COMPLETED_MONO = _PHYSICAL_COMPLETED_MONO
                        state["inventory_completed"] = state["completed"]
                if assessed and closed and not _PUBLISHER_GATE_FAILED and not _GUARD_FATAL:
                    settle_pending(state)
                else:
                    _PUBLISHER_GATE_FAILED = True
                    state["observed_at"] = time.time()
                    write_budget(state)
            except BaseException:
                journal_failed()
                if not primary:
                    raise
            finally:
                lock.close()


def fetch_inventory_page(fetch, *args, **kwargs):
    """Mark actual inventory physical requests; robots retain their own floor."""
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = True
    try:
        return fetch(*args, **kwargs)
    finally:
        _INVENTORY_ACTIVE = previous


def workflow_api(path):
    """Keep typed, redacted infrastructure diagnostics without publisher access."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json"
            },
        )
        if status != 200:
            raise AdapterError("Workflow API HTTP " + str(status), "access",
                               status)
        return json.loads(body)
    except AdapterError:
        raise
    except Exception as error:
        raise AdapterError(
            "Workflow state unavailable: " + type(error).__name__,
            "access") from None


def immutable_file(head, path):
    key = (head, path)
    if key not in _IMMUTABLE_FILES:
        value = workflow_api("/contents/" + path + "?ref=" + head)
        if (value.get("type") != "file" or value.get("encoding") != "base64" or
                value.get("path") != path):
            raise AdapterError("Incomplete immutable file binding", "access")
        raw = base64.b64decode(value["content"], validate=False)
        git_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" +
                               raw).hexdigest()
        if git_sha != value.get("sha"):
            raise AdapterError("Immutable source blob digest mismatch",
                               "access")
        _IMMUTABLE_FILES[key] = (hashlib.sha256(raw).hexdigest(), git_sha)
    return _IMMUTABLE_FILES[key]


def exact_jobs(run):
    jobs, total = [], None
    for page in range(1, 101):
        value = workflow_api(
            f'/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs?per_page=100&page={page}'
        )
        count, rows = value.get("total_count"), value.get("jobs")
        if (type(count) is not int or count <= 0 or
                not isinstance(rows, list) or len(rows) > 100 or
                total is not None and total != count):
            raise AdapterError("Incomplete exact-attempt jobs", "access")
        total = count
        jobs.extend(rows)
        if len(jobs) == total:
            break
        if len(jobs) > total or len(rows) != 100:
            raise AdapterError("Truncated exact-attempt jobs", "access")
    else:
        raise AdapterError("Exact jobs exceed infrastructure bound", "access")
    if len(jobs) != 1:
        raise AdapterError("Unexpected family job inventory", "access")
    job = jobs[0]
    if (type(job.get("id")) is not int or job["id"] <= 0 or
            job.get("run_id") != run["id"] or
            type(job.get("run_attempt")) is not int or
            job["run_attempt"] != run["run_attempt"] or
            job.get("head_sha") != run["head_sha"] or
            job.get("name") != "collect"):
        raise AdapterError("Exact job identity mismatch", "access")
    return job


def epoch(value):
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.utcoffset() is None:
            raise ValueError("Naive timestamp")
        result = moment.timestamp()
        if not numeric(result):
            raise ValueError("Invalid timestamp")
        return result
    except (AttributeError, ValueError, TypeError, OverflowError):
        raise AdapterError("Invalid exact attempt timestamp",
                           "access") from None


def family_history():
    """Complete exact source history, including the current authenticated owner."""
    current = current_run_identity() if os.environ.get(
        "GITHUB_ACTIONS") == "true" else None
    if current is not None and current[1] != 1:
        raise AdapterError("Rerun needs separately reviewed owned continuity",
                           "access")
    runs, total, seen = [], None, set()
    for page in range(1, 101):
        value = workflow_api(
            f"/actions/workflows/{_WORKFLOW_ID}/runs?per_page=100&page={page}")
        count, rows = value.get("total_count"), value.get("workflow_runs")
        if (type(count) is not int or count < 1 or not isinstance(rows, list) or
                len(rows) > 100 or total is not None and total != count):
            raise AdapterError("Incomplete or changing source history",
                               "access")
        total = count
        for run in rows:
            if (type(run.get("id")) is not int or run["id"] <= 0 or
                    run["id"] in seen or
                    run.get("workflow_id") != _WORKFLOW_ID or
                    run.get("path") != _WORKFLOW_PATH or
                    run.get("head_branch") != "main" or
                    run.get("repository",
                            {}).get("full_name") != "YouthOpps/data-pipeline" or
                    run.get("head_repository",
                            {}).get("full_name") != "YouthOpps/data-pipeline" or
                    type(run.get("run_attempt")) is not int or
                    run["run_attempt"] < 1 or
                    not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))):
                raise AdapterError("Untrusted source run identity", "access")
            seen.add(run["id"])
            runs.append(run)
        if len(runs) == total:
            break
        if len(runs) > total or len(rows) != 100:
            raise AdapterError("Truncated source run history", "access")
    else:
        raise AdapterError("Source history exceeds infrastructure bound",
                           "access")
    ordered, found = [], False
    for run in runs:
        job = exact_jobs(run)
        if current is not None and run["id"] == current[0]:
            if (run["run_attempt"] != current[1] or
                    run["head_sha"] != os.environ.get("GITHUB_SHA") or
                    run.get("status") != "in_progress" or
                    job.get("status") != "in_progress" or immutable_file(
                        run["head_sha"], _ADAPTER_PATH)[0] != source_digest() or
                    immutable_file(run["head_sha"],
                                   _WORKFLOW_PATH)[0] != _WORKFLOW_SHA256):
                raise AdapterError(
                    "Current executing owner is not immutable-bound", "access")
            found = True
            continue
        if run.get("status") != "completed" or job.get("status") != "completed":
            raise AdapterError("Another source owner is active", "access")
        started, completed = epoch(job.get("started_at")), epoch(
            job.get("completed_at"))
        if not 0 <= started <= completed <= time.time() + 1:
            raise AdapterError("Untrusted exact job chronology", "access")
        ordered.append((completed, run, job))
    if current is not None and not found:
        raise AdapterError(
            "Current attempt absent from complete source history", "access")
    ordered.sort(key=lambda row: row[0], reverse=True)
    if len({row[0] for row in ordered}) != len(ordered):
        raise AdapterError("Ambiguous completed owner chronology", "access")
    return ordered


def source_digest():
    with open(__file__, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()


def run_artifacts(run):
    value = workflow_api(f'/actions/runs/{run["id"]}/artifacts?per_page=100')
    rows = value.get("artifacts")
    if (not isinstance(rows, list) or
            type(value.get("total_count")) is not int or
            value["total_count"] != len(rows) or len(rows) > 100 or
            any(not isinstance(row, dict) for row in rows)):
        raise AdapterError("Incomplete exact artifact inventory", "access")
    return rows


def reviewed_no_access(run, job, artifacts):
    if artifacts or run["run_attempt"] != 1 or run.get(
            "conclusion") != "failure" or job.get("conclusion") != "failure":
        return False
    pin = _NO_ACCESS_PINS.get(run["id"])
    if pin is not None:
        return ((job["id"], run["head_sha"], job["started_at"],
                 job["completed_at"]) == pin and
                immutable_file(run["head_sha"],
                               _ADAPTER_PATH)[0] == _LEGACY_SOURCE_SHA256 and
                immutable_file(run["head_sha"],
                               _WORKFLOW_PATH)[0] == _LEGACY_WORKFLOW_SHA256)
    # This is a positive time/code proof, never an error-text or old-head fallback.
    return (
        0 <= epoch(job["completed_at"]) - epoch(job["started_at"]) < 58 and
        immutable_file(run["head_sha"], _ADAPTER_PATH)[0] == source_digest() and
        immutable_file(run["head_sha"], _WORKFLOW_PATH)[0] == _WORKFLOW_SHA256)


def download_inert_artifact(identity, artifact_id, digest=None):
    """Use the real bounded infrastructure transport and strict owned ZIP decoder."""
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}",
                                                       digest):
        raise AdapterError("Missing authenticated archive digest", "access")
    token = os.environ.get("GITHUB_TOKEN", "")
    status, headers, _ = request_bytes(
        _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip",
        byte_limit=1000,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json"
        })
    destination = headers.get("Location", "")
    parsed = urllib.parse.urlsplit(destination)
    if (status != 302 or parsed.scheme != "https" or parsed.username or
            parsed.password or parsed.port or not parsed.hostname or
            not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com"))):
        raise AdapterError("Untrusted artifact redirect", "access")
    status, _, body = request_bytes(destination, byte_limit=32768)
    if status != 200 or "sha256:" + hashlib.sha256(body).hexdigest() != digest:
        raise AdapterError("Owned archive response/digest mismatch", "access")
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if (len(entries) != 1 or
                    entries[0].filename != "pacing-state.json" or
                    entries[0].file_size > 16384 or entries[0].flag_bits & 1 or
                    stat.S_ISLNK(entries[0].external_attr >> 16)):
                raise ValueError("Invalid owned ZIP members")
            envelope = json.loads(archive.read(entries[0]))
    except (ValueError, KeyError, zipfile.BadZipFile):
        raise AdapterError("Malformed owned-state archive", "access") from None
    run_id, attempt, source, head = identity
    common = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "run_id": run_id,
        "run_attempt": attempt,
        "source": source
    }
    if any(
            envelope.get(key) != value or
            type(envelope.get(key)) is not type(value)
            for key, value in common.items()):
        raise AdapterError("Owned archive identity mismatch", "access")
    if set(envelope) == set(common) | {"state"}:
        if ((run_id, attempt, artifact_id, head, digest) != _LEGACY_OWNER or
                immutable_file(head,
                               _ADAPTER_PATH)[0] != _LEGACY_SOURCE_SHA256):
            raise AdapterError("Legacy archive is not the reviewed owner",
                               "access")
        return migrate_owned_state(envelope)
    if set(envelope) != set(common) | {
            "head_sha", "adapter_sha256", "state_sha256", "state"
    }:
        raise AdapterError("Invalid modern owned envelope", "access")
    if (envelope["head_sha"] != head or envelope["adapter_sha256"]
            != immutable_file(head, _ADAPTER_PATH)[0] or
            envelope["state_sha256"] != hashlib.sha256(
                json.dumps(envelope["state"],
                           sort_keys=True,
                           separators=(",", ":")).encode()).hexdigest()):
        raise AdapterError("Owned state/code digest mismatch", "access")
    return validate_budget(envelope["state"])


def retained_hosted_max(incoming):
    """Merge the reviewed native floor after genuine newest-owner decoding."""
    validate_budget(incoming)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return incoming
    if (_HOSTED_MAX_STATE_SHA256 != _HOSTED_MAX_EXPECTED_SHA256 or
            not isinstance(_HOSTED_MAX_EVIDENCE, str) or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/393"
                r"#issuecomment-[1-9][0-9]*", _HOSTED_MAX_EVIDENCE)):
        raise AdapterError("Reviewed hosted native MAX decision is OFF",
                           "access")
    candidate = copy.deepcopy(_HOSTED_MAX_STATE)
    digest = hashlib.sha256(json.dumps(
        candidate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != _HOSTED_MAX_STATE_SHA256:
        raise AdapterError("Committed hosted MAX candidate changed", "access")
    return merge_budget(incoming, validate_budget(candidate))


def transfer_fh_native_state(incoming, predecessor):
    """Adopt the exact retired native owner, or its validated production descendant."""
    approval = _FH_NATIVE_HANDOFF
    if (not isinstance(approval, dict) or set(approval) != {
            "approval", "workflow", "run", "job", "head", "artifact",
            "artifact_sha256", "state_sha256", "state", "production_predecessor"}
            or any(type(approval[field]) is not int or approval[field] <= 0
                   for field in ("approval", "workflow", "run", "job",
                                 "artifact", "production_predecessor"))
            or not re.fullmatch(r"[0-9a-f]{40}", approval["head"])
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", approval["artifact_sha256"])):
        raise AdapterError("Reviewed Ernst Mach FH native handoff is OFF", "access")
    native = validate_budget(approval["state"])
    if (hashlib.sha256(json.dumps(native, sort_keys=True, separators=(",", ":"))
                      .encode()).hexdigest() != approval["state_sha256"]
            or native["blocked"] or native["pending"]):
        raise AdapterError("Native handoff digest or access state differs", "access")
    if predecessor["id"] != approval["production_predecessor"]:
        # Established journals prune request starts outside the rolling window.
        recent = dict(native, starts=[started for started in native["starts"]
                                     if incoming["observed_at"] - started < 60])
        combined = merge_budget(incoming, recent)
        if combined != incoming or incoming["observed_at"] <= native["observed_at"]:
            raise AdapterError("Latest production owner did not retain the native handoff",
                               "access")
        return incoming

    run_id = approval["run"]
    workflow = workflow_api(f'/actions/workflows/{approval["workflow"]}')
    if (workflow.get("id") != approval["workflow"]
            or workflow.get("path") != _FH_QA_PATH
            or workflow.get("state") != "disabled_manually"):
        raise AdapterError("Exact native owner is not retired", "access")
    expected = {
        "id": run_id, "workflow_id": approval["workflow"], "run_attempt": 1,
        "head_sha": approval["head"], "head_branch": _FH_QA_BRANCH,
        "path": _FH_QA_PATH, "event": "push", "status": "completed",
        "conclusion": "success",
    }
    inventory = workflow_api(f'/actions/workflows/{approval["workflow"]}/runs?per_page=2')
    rows = inventory.get("workflow_runs")
    run = workflow_api(f"/actions/runs/{run_id}/attempts/1")
    if (inventory.get("total_count") != 1 or not isinstance(rows, list) or len(rows) != 1
            or any(run.get(field) != value or rows[0].get(field) != value
                   for field, value in expected.items())
            or any(run.get(field, {}).get("full_name") != "YouthOpps/data-pipeline"
                   for field in ("head_repository", "repository"))):
        raise AdapterError("Exact native run or chronology differs", "access")
    jobs = workflow_api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=2")
    matching = jobs.get("jobs")
    if (jobs.get("total_count") != 1 or not isinstance(matching, list)
            or len(matching) != 1 or any(
                matching[0].get(field) != value for field, value in {
                    "id": approval["job"], "run_id": run_id, "name": "verify",
                    "head_sha": approval["head"], "status": "completed",
                    "conclusion": "success",
                }.items())):
        raise AdapterError("Exact native verification job differs", "access")
    comment = workflow_api(f'/issues/comments/{approval["approval"]}')
    decision = (f'Approved Ernst Mach FH production state {approval["state_sha256"]}')
    if (comment.get("issue_url") != _WORKFLOW_GITHUB + "/issues/469"
            or comment.get("user", {}).get("id") != 12278069
            or comment.get("user", {}).get("type") != "User"
            or comment.get("created_at") != comment.get("updated_at")
            or epoch(comment.get("created_at")) < epoch(matching[0].get("completed_at"))
            or comment.get("body", "").splitlines().count(decision) != 1):
        raise AdapterError("Exact production handoff decision is absent", "access")
    artifacts = [row for row in run_artifacts(run)
                 if row.get("name") == "grants-at-fh469-qa-state-1"]
    if (len(artifacts) != 1 or artifacts[0].get("id") != approval["artifact"]
            or artifacts[0].get("digest") != approval["artifact_sha256"]
            or artifacts[0].get("expired") is not False
            or artifacts[0].get("workflow_run", {}).get("id") != run_id
            or artifacts[0].get("workflow_run", {}).get("head_sha") != approval["head"]):
        raise AdapterError("Exact retired native state artifact differs", "access")
    witnessed = download_inert_artifact(
        (run_id, 1, SOURCE_ID, approval["head"]), approval["artifact"],
        approval["artifact_sha256"])
    if witnessed != native:
        raise AdapterError("Reviewed native state differs from original bytes", "access")
    return merge_budget(incoming, native)


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED, _STARTUP_QUIET_UNTIL
    if _RESTORED:
        return
    ordered = family_history()
    incoming = None
    for _, run, job in ordered:
        artifacts = run_artifacts(run)
        if reviewed_no_access(run, job, artifacts):
            continue
        expected = _ARTIFACT_NAME + "-" + str(run["run_attempt"])
        matches = [row for row in artifacts if row.get("name") == expected]
        if len(matches) != 1 or matches[0].get("expired") is not False:
            raise AdapterError(
                "Newest applicable owner lacks its exact state; no fallback",
                "access")
        artifact = matches[0]
        if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or
                artifact.get("workflow_run", {}).get("id") != run["id"] or
                artifact.get("workflow_run",
                             {}).get("head_sha") != run["head_sha"] or
                type(artifact.get("size_in_bytes")) is not int or
                not 0 < artifact["size_in_bytes"] <= 32768):
            raise AdapterError("Invalid newest artifact binding", "access")
        incoming = download_inert_artifact(
            (run["id"], run["run_attempt"], SOURCE_ID, run["head_sha"]),
            artifact["id"], artifact.get("digest"))
        break
    if incoming is None:
        raise AdapterError("No exact owned source state", "access")
    incoming = retained_hosted_max(incoming)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        incoming = transfer_fh_native_state(incoming, run)
    with journal_restore_scope(incoming), locked_budget():
        local = load_budget()
        combined = merge_budget(local, incoming)
        combined["observed_at"] = time.time()
        combined["not_before"] = max(combined["not_before"], time.time() + 60)
        write_budget(combined)
    _STARTUP_QUIET_UNTIL = time.monotonic() + 60
    _RESTORED = _STATE_TRUSTED = True
    available_state(combined)


def verify_local_pause():
    value = workflow_api(f"/actions/workflows/{_WORKFLOW_ID}")
    if (value.get("id") != _WORKFLOW_ID or
            value.get("path") != _WORKFLOW_PATH or
            value.get("state") != "disabled_manually"):
        raise AdapterError(
            "Exact source workflow must remain paused for local collection",
            "access")
    # Complete history is checked again before the first physical opener.
    family_history()


def require_existing_local_state():
    """Pin the existing local parent and journal before metadata callbacks."""
    try:
        budget_file()
        with locked_budget():
            available_state(load_budget())
    except BaseException:
        journal_failed()
        raise


def prepare_collection():
    """Restore exact ownership before source access; local execution is one-shot."""
    global _RUN_END, _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    global _GUARD_COLLECTION_ID, _GUARD_NEW_DIRECTORY, _LOCAL_CLAIM_ID
    if _PUBLISHER_GATE_FAILED or _GUARD_FATAL or _COLLECTION_CLOSED:
        raise AdapterError("Publisher lifecycle is already closed", "access")
    if _RUN_END is None:
        _RUN_END = time.monotonic() + 1740
    if _RESTORED and _COLLECTION_LOCK_FD is not None:
        with locked_budget():
            available_state(load_budget())
        return
    _COLLECTION_STARTED = time.time()
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if not hosted:
        if (not isinstance(_LOCAL_NATIVE_STATE_SHA256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", _LOCAL_NATIVE_STATE_SHA256
        ) or _LOCAL_NATIVE_MACHINE_SHA256 is None or not isinstance(
                _LOCAL_NATIVE_EVIDENCE, str
        ) or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/393#issuecomment-[1-9][0-9]*",
                _LOCAL_NATIVE_EVIDENCE)):
            raise AdapterError(
                "Local native handoff has not been independently reviewed",
                "access")
        if os.environ.get("DATA_SOURCE_TOKEN"):
            raise AdapterError(
                "Local native cannot receive publication credentials", "access")
        with open("/etc/machine-id", "rb") as machine:
            if hashlib.sha256(
                    machine.read()).hexdigest() != _LOCAL_NATIVE_MACHINE_SHA256:
                raise AdapterError("Reviewed native machine changed", "access")
        require_existing_local_state()
        verify_local_pause()
        budget_file()
        # Local mature state is never synthesized from hosted or old bytes.
        with locked_budget():
            available_state(load_budget())
            if hashlib.sha256(
                    journal_read()).hexdigest() != _LOCAL_NATIVE_STATE_SHA256:
                raise AdapterError("Local native state generation changed",
                                   "access")
            parent = journal_parent()
            descriptor = os.open("native-claim",
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW,
                                 0o600,
                                 dir_fd=parent)
            try:
                raw = json.dumps(
                    {
                        "source_sha256": source_digest(),
                        "state_sha256": _LOCAL_NATIVE_STATE_SHA256,
                        "evidence": _LOCAL_NATIVE_EVIDENCE
                    },
                    sort_keys=True).encode()
                if os.write(descriptor, raw) != len(raw):
                    raise AdapterError("Short native ownership claim", "access")
                os.fsync(descriptor)
                info = os.fstat(descriptor)
                _LOCAL_CLAIM_ID = (info.st_dev, info.st_ino,
                                   hashlib.sha256(raw).hexdigest())
                os.fsync(parent)
            finally:
                os.close(descriptor)
    restore_family_artifact()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        parent = journal_parent()
        descriptor = identity = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open("collection.lock", flags, dir_fd=parent)
            except FileNotFoundError:
                if not _GUARD_NEW_DIRECTORY:
                    raise AdapterError("Mature collection lock missing",
                                       "access")
                descriptor = os.open("collection.lock",
                                     flags | os.O_CREAT | os.O_EXCL,
                                     0o600,
                                     dir_fd=parent)
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            journal_owned(descriptor, identity)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = os.stat("collection.lock",
                              dir_fd=parent,
                              follow_symlinks=False)
            if (current.st_dev, current.st_ino) != identity:
                raise AdapterError("Collection lock identity changed", "access")
        except BaseException:
            journal_failed()
            if descriptor is not None and identity is not None:
                journal_close(descriptor, identity, True)
            raise
        _GUARD_COLLECTION_ID = identity
        _COLLECTION_LOCK_FD = descriptor
        _GUARD_NEW_DIRECTORY = False
    with locked_budget():
        available_state(load_budget())
    _STATE_TRUSTED = True


def journal_preopener():
    """Verify live kernel flock, pending owner and retained local claim."""
    try:
        journal_verify()
        if _GUARD_COLLECTION_ID is None or _COLLECTION_LOCK_FD is None:
            raise AdapterError("Publisher collection ownership missing",
                               "access")
        journal_owned(_COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID)
        named = os.stat("collection.lock",
                        dir_fd=journal_parent(),
                        follow_symlinks=False)
        if (named.st_dev, named.st_ino) != _GUARD_COLLECTION_ID:
            raise AdapterError("Publisher collection lock changed", "access")
        with open(f"/proc/self/fdinfo/{_COLLECTION_LOCK_FD}",
                  encoding="ascii") as info:
            if not re.search(r"lock:\s+.*FLOCK\s+ADVISORY\s+WRITE\s+",
                             info.read()):
                raise AdapterError("Publisher collection flock was released",
                                   "access")
        state = load_budget()
        if not state["pending"] or state["pending_kind"] != "physical":
            raise AdapterError("Physical pending owner is absent", "access")
        if os.environ.get("GITHUB_ACTIONS") != "true":
            if _LOCAL_CLAIM_ID is None:
                raise AdapterError("Local one-shot owner absent", "access")
            descriptor = os.open("native-claim",
                                 os.O_RDONLY | os.O_NOFOLLOW,
                                 dir_fd=journal_parent())
            try:
                info = journal_owned(descriptor, _LOCAL_CLAIM_ID[:2])
                if info.st_size > 2048 or hashlib.sha256(
                        os.pread(descriptor, info.st_size + 1,
                                 0)).hexdigest() != _LOCAL_CLAIM_ID[2]:
                    raise AdapterError("Local one-shot owner changed", "access")
            finally:
                os.close(descriptor)
    except BaseException:
        journal_failed()
        raise


def collect():
    """One complete unchanged source scan within a nonrenewable phase."""
    global _COLLECTION_ATTEMPTED, _COLLECTION_CLOSED, _COLLECTION_END
    if _COLLECTION_ATTEMPTED:
        raise AdapterError("This process already owns a collection attempt",
                           "access")
    _COLLECTION_ATTEMPTED = True
    try:
        prepare_collection()
        _COLLECTION_END = min(_RUN_END - 60,
                              time.monotonic() + COLLECTION_TIMEOUT)
        return collect_body()
    finally:
        _COLLECTION_CLOSED = True
        _COLLECTION_END = None


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
                    "message": ("Run could not complete; "
                                "durable status unavailable"),
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            ))
        sys.exit(1)
