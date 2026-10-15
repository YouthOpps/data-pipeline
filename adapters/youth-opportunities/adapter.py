"""Collect the complete Youth Opportunities browse inventory with durable pacing."""

import argparse
import base64
import contextlib
import datetime
import email.utils
import errno
import fcntl
import hashlib
import html
import html.parser
import io
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "youth-opportunities"
SOURCE_URL = "https://www.youthop.com/search/"
WEBSITE_URL = "https://www.youthop.com/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "BD"
PUBLISHER_TYPE = "aggregator"
ATTRIBUTION = "Youth Opportunities"

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
_ROBOTS_PURPOSE = object()
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-youth-opportunities-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_ARTIFACT_NAME = "youth-opportunities-pacing-state"
_RESTORED = _STATE_TRUSTED = False
_BUDGET_INITIALIZING = None
_COLLECTION_LOCK_HELD = False
_PUBLISHER_HOST = "youthop.com"
_SCANNED_COMPLETED = {}


_PAGE_COMPLETED = None
_INVENTORY_REQUEST = False


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space all inventory attempts without changing normal detail pacing."""
    global _PAGE_COMPLETED, _INVENTORY_REQUEST
    if _PAGE_COMPLETED is not None:
        while True:
            with budget_state() as state:
                page_interval = state["page_interval"]
            wait = _PAGE_COMPLETED + page_interval - time.monotonic()
            if wait <= 0:
                break
            if (
                _COLLECTION_DEADLINE is not None
                and time.time() + wait + 90 > _COLLECTION_DEADLINE
            ):
                raise AdapterError(
                    "Inventory page gap exceeds remaining collection budget",
                    "fetch",
                )
            time.sleep(wait)
    prior_inventory = _INVENTORY_REQUEST
    _INVENTORY_REQUEST = True
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()
        _INVENTORY_REQUEST = prior_inventory


def _feed_text(value):
    return " ".join(
        html.unescape(re.sub(r"<[^>]*>", " ", value or "")).split()
    )


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
        "schema": 1,
        "family": FAMILY,
        "starts": [],
        "interval": 12.1,
        "until": now,
        "rate_failures": 0,
        "legacy_rate_history_unknown": True,
        "page_interval": 30,
        "blocked": False,
        "observed_at": now,
    }


def validate_budget(state):
    """Validate source-bound inert state; never expire refusals or embargoes."""
    if isinstance(state, dict) and "legacy_rate_history_unknown" not in state:
        state = {**state, "legacy_rate_history_unknown": True}
    if (
        not isinstance(state, dict)
        or set(state)
        not in (
            set(empty_budget(0)),
            set(empty_budget(0)) | {"unassessed_response_failure"},
        )
        or (
            "unassessed_response_failure" in state
            and state["unassessed_response_failure"] is not True
        )
        or type(state["schema"]) is not int
        or state["schema"] != 1
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
        or type(state["legacy_rate_history_unknown"]) is not bool
        or type(state["blocked"]) is not bool
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 5
        or any(
            not numeric(t) or t > state["observed_at"] for t in state["starts"]
        )
        or sorted(set(state["starts"])) != state["starts"]
    ):
        raise AdapterError("Invalid retained publisher state", "access")
    return state


def budget_path(host=None):
    global _BUDGET_INITIALIZING
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    legacy_key = hashlib.sha256(
        (SOURCE_ID + "|youthop.com").encode()
    ).hexdigest()[:24]
    legacy = os.path.join(
        tempfile.gettempdir(), "youthopps-" + legacy_key + ".budget"
    )
    if os.path.lexists(legacy):
        raise AdapterError(
            "Legacy native publisher journal requires reviewed migration; "
            "no state reset",
            "access",
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
    """Serialize durable local state updates outside the delivered tree."""
    global _BUDGET_INITIALIZING
    path = budget_path()
    fd = os.open(
        path,
        os.O_RDWR
        | (os.O_CREAT if _BUDGET_INITIALIZING else 0)
        | os.O_NOFOLLOW,
        0o600,
    )
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher state file", "access")
    with os.fdopen(fd, "r+", encoding="utf-8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        raw = file.read(16385)
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher state", "access")
        try:
            if not raw and not _BUDGET_INITIALIZING:
                raise ValueError("Mature state is empty")
            state = (
                validate_budget(json.loads(raw))
                if raw
                else empty_budget(time.time())
            )
        except (ValueError, TypeError) as error:
            raise AdapterError("Corrupt publisher state", "access") from error
        try:
            yield state
        finally:
            state["observed_at"] = time.time()
            state["starts"] = [
                t for t in state["starts"] if t > time.time() - 60
            ]
            validate_budget(state)
            file.seek(0)
            file.truncate()
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
            _BUDGET_INITIALIZING = False


def pace(url, interval=12):
    """Preserve actual-start history and completion guards across local runs."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    while True:
        with budget_state() as state:
            if state.get("unassessed_response_failure"):
                raise AdapterError(
                    "Unassessed publisher response requires reviewed recovery",
                    "access",
                )
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
            if (
                _COLLECTION_DEADLINE is not None
                and ready + 90 > _COLLECTION_DEADLINE
            ):
                raise AdapterError(
                    "Insufficient remaining source collection time", "fetch"
                )
            if ready <= now:
                state["starts"].append(now)
                return
            if ready - now > 120:
                raise AdapterError(
                    "Publisher backoff exceeds collection wait budget",
                    "access",
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


def retry_after_instant(value, now):
    """Validate a future aware representable closing instant before state mutation."""
    if not isinstance(value, str):
        raise ValueError("Retry-After must be a string")
    value = value.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Retry-After delay must be finite and positive")
        until = now + seconds
    else:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.utcoffset() is None:
            raise ValueError("Retry-After date requires an explicit timezone")
        until = parsed.timestamp()
    if not numeric(until) or until <= now:
        raise ValueError("Retry-After instant must be finite and future")
    # A finite float can still exceed datetime/platform timestamp support.
    aware = datetime.datetime.fromtimestamp(until, datetime.timezone.utc)
    if aware.utcoffset() is None:
        raise ValueError("Retry-After instant is not representable")
    return until


def retryable_connection_error(error):
    """Recognize ordinary pre-response network faults without storage/TLS."""
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, (ssl.SSLError, FileNotFoundError, PermissionError)):
        return False
    if isinstance(reason, socket.gaierror):
        return reason.errno == socket.EAI_AGAIN
    return isinstance(reason, (ConnectionError, TimeoutError)) or (
        isinstance(reason, OSError)
        and reason.errno
        in {
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETRESET,
            errno.ENETUNREACH,
            errno.EHOSTUNREACH,
            errno.ETIMEDOUT,
        }
    )


def request_bytes(
    url,
    *,
    interval=12,
    method="GET",
    headers=None,
    payload=None,
    _purpose=None,
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
    started = time.time()
    received = assessed = unresolved = False
    try:
        try:
            response = _OPENER.open(request, timeout=90)
        except urllib.error.HTTPError as error:
            response = error
        received = True
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
                    and _purpose is not _ROBOTS_PURPOSE
                ):
                    state["blocked"] = True
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        until = retry_after_instant(retry, now)
                        state["until"] = max(state["until"], until)
                    except (ValueError, TypeError, OverflowError, OSError):
                        # Unusable timing never establishes access refusal.
                        pass
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds byte limit", "fetch")
            if _purpose is not _ROBOTS_PURPOSE:
                try:
                    check_content_challenge(
                        body.decode("utf-8", errors="replace"),
                        status=response.status,
                    )
                except AdapterError:
                    # Only a durably retained challenge is fully assessed.
                    with budget_state() as state:
                        assessed = state["blocked"]
                    raise
            assessed = True
            return response.status, response.headers, body
    except Exception as error:
        if (
            received and not (assessed and isinstance(error, AdapterError))
        ) or (not received and not retryable_connection_error(error)):
            unresolved = True
            raise AdapterError(
                "Publisher response assessment failed; "
                "reviewed recovery required",
                "access",
            ) from error
        raise
    finally:
        _PUBLISHER_GATE_FAILED = True
        completed = time.time()
        _RESPONSE_SECONDS.append(max(0, completed - started))
        with budget_state() as state:
            if unresolved:
                state["unassessed_response_failure"] = True
            state["until"] = max(
                state["until"],
                completed
                + max(
                    12.1,
                    interval,
                    state["interval"],
                    state["page_interval"] if _INVENTORY_REQUEST else 0,
                ),
            )
        if not unresolved:
            _PUBLISHER_GATE_FAILED = False
        print(
            json.dumps(
                {"event": "request_complete", "url": url, "time": completed}
            ),
            file=sys.stderr,
            flush=True,
        )


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
                    current,
                    interval=min_interval,
                    _purpose=(
                        _ROBOTS_PURPOSE
                        if original.path == "/robots.txt"
                        and not original.query
                        and not original.fragment
                        else None
                    ),
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(
                        current, headers["Location"]
                    )
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
                text = body.decode(headers.get_content_charset() or "utf-8")
                if original.path != "/robots.txt":
                    check_content_challenge(text)
                return (text, headers) if with_headers else text
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch",
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


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
            with budget_state() as state:
                state["blocked"] = True
            raise AdapterError(
                "Robots policy disallows this source endpoint", "access"
            )
    return max([12] + [g["delay"] for policy in policies for g in policy])


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


def validate_records(records, *, allow_empty=False, allow_legacy_ids=False):
    """Validate every record, stable ID, source, date, category and provenance.

    Allow empty lists only for existing snapshots and validated expiry cleanup.
    Fresh collection must produce genuine nonempty records. Legacy IDs are
    accepted only when explicitly validating a prior published snapshot.
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
        pattern = re.escape(SOURCE_ID) + r"-[1-9][0-9]*"
        if allow_legacy_ids:
            pattern = re.escape(SOURCE_ID) + r"[-:][1-9][0-9]*"
        if not re.fullmatch(pattern, record["id"]):
            raise AdapterError(
                "Invalid stable publisher identifier", "validate"
            )
        identity = record["id"].replace(SOURCE_ID + ":", SOURCE_ID + "-", 1)
        if identity in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(identity)
        url = urllib.parse.urlsplit(record["url"])
        if url.scheme != "https" or url.netloc != "www.youthop.com":
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
            if value is not None:
                try:
                    if (
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
            raise AdapterError(
                "Invalid opportunity kind or status", "validate"
            )
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


def validate_factual_records(records):
    """Reject rich stale fields in a newly checked minimal factual collection."""
    validate_records(records)
    if any(
        record["published_at"] is not None
        or record["eligible_countries"]
        or record["host_countries"]
        or record["classification"]["method"]
        != "publisher-complete-browse-and-compact-facts"
        or not record["summary"].startswith(
            "A Youth Opportunities listing classified as "
        )
        for record in records
    ):
        raise AdapterError(
            "Fresh factual records contain unproved or stale rich fields",
            "validate",
        )


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


def collect():
    """Retrieve the full inventory under the shared collection lock."""
    global _STATE_TRUSTED
    with collection_lock():
        if os.environ.get("GITHUB_ACTIONS") == "true":
            restore_family_artifact()
        _STATE_TRUSTED = True
        if _COLLECTION_DEADLINE is None:
            set_collection_deadline()
        return collect_inventory()


class PrimaryText(html.parser.HTMLParser):
    """Ignore ordinary ancillary/script notices when recognizing real refusals."""

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


def check_content_challenge(document, *, status=None):
    if not document.lstrip().startswith("<"):
        return
    primary = PrimaryText(document)
    visible = " ".join(primary.parts + primary.head_title_parts)
    if re.search(
        r"verify (?:that )?you are human|confirm (?:that )?you are (?:a )?human|unusual traffic|access (?:denied|blocked)|request (?:has been )?(?:blocked|denied)|checking your browser|enable javascript and cookies to continue",
        visible,
        re.I,
    ):
        with budget_state() as state:
            state["blocked"] = True
        raise AdapterError(
            "Publisher returned an explicit content challenge",
            "access",
            status,
        )


class BrowsePage(html.parser.HTMLParser):
    """Read every native opportunity card and semantic pagination fact."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.entries, self.pages = [], {1}
        self.current = self.page_number = None
        self.advertisements = self.hidden = self.span_depth = 0
        self.title_open = False
        self.location_depth = None
        self.feed(document)
        self.close()
        if self.current is not None or not self.entries:
            raise AdapterError("Empty or incomplete publisher browse page")
        if not re.search(r"</body\s*>\s*</html\s*>\s*$", document, re.I):
            raise AdapterError("Incomplete publisher browse HTML")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "span":
            self.span_depth += 1
        if tag == "article":
            if self.current is not None:
                raise AdapterError("Nested publisher opportunity cards")
            classes = attrs.get("class", "").split()
            match = re.fullmatch(r"post-([1-9][0-9]*)", attrs.get("id", ""))
            if attrs.get("id") and not match or "post-item" not in classes:
                raise AdapterError("Malformed publisher browse article")
            if match and not {"type-post", "status-publish"}.issubset(classes):
                raise AdapterError(
                    "Browse card is not a published announcement"
                )
            slugs = [
                value.removeprefix("category-")
                for value in classes
                if value.startswith("category-")
            ]
            self.current = {
                "id": int(match[1]) if match else None,
                "url": None,
                "title_parts": [],
                "title_count": 0,
                "slugs": sorted(set(slugs)),
                "location_parts": [],
                "deadline_labels": [],
                "advertisement": False,
                "text": [],
            }
        if self.current is not None:
            if (
                tag == "ins"
                and "adsbygoogle" in attrs.get("class", "").split()
            ):
                self.current["advertisement"] = True
            if (
                tag == "h3"
                and attrs.get("itemprop") == "name"
                and "post-title" in attrs.get("class", "").split()
            ):
                self.title_open = True
                self.current["title_count"] += 1
            if tag == "span" and attrs.get("itemprop") == "location":
                if self.location_depth is not None:
                    raise AdapterError("Nested browse location")
                self.location_depth = self.span_depth
            if attrs.get("itemprop") == "startDate":
                self.current["deadline_labels"].append(attrs.get("content"))
            if tag == "a" and attrs.get("itemprop") == "url":
                if self.current["url"] is not None:
                    raise AdapterError("Duplicate canonical browse URL")
                self.current["url"] = attrs.get("href", "")
        if tag == "span" and "page-numbers" in attrs.get("class", "").split():
            self.page_number = []
        if tag == "a" and "page-numbers" in attrs.get("class", "").split():
            parsed = urllib.parse.urlsplit(attrs.get("href", ""))
            query = urllib.parse.parse_qs(parsed.query)
            try:
                if set(query) - {"sf_paged"}:
                    raise ValueError()
                page = int(query.get("sf_paged", ["1"])[0])
            except (ValueError, IndexError) as error:
                raise AdapterError("Invalid browse pagination link") from error
            if (
                parsed.scheme != "https"
                or parsed.netloc != "www.youthop.com"
                or parsed.path != "/search/"
                or parsed.fragment
                or page < 1
            ):
                raise AdapterError("Unexpected browse pagination route")
            self.pages.add(page)

    def handle_data(self, value):
        if self.current is not None and not self.hidden and value.strip():
            self.current["text"].append(value.strip())
            if self.title_open:
                self.current["title_parts"].append(value)
            if self.location_depth is not None:
                self.current["location_parts"].append(value)
        if self.page_number is not None:
            self.page_number.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "h3":
            self.title_open = False
        if tag == "span":
            if self.location_depth == self.span_depth:
                self.location_depth = None
            self.span_depth = max(0, self.span_depth - 1)
            if self.page_number is not None:
                text = "".join(self.page_number).strip()
                if text.isdecimal():
                    self.pages.add(int(text))
                self.page_number = None
        if tag == "article":
            card = self.current
            if card is None:
                raise AdapterError("Unmatched publisher browse article")
            if (
                card["id"] is None
                and card["advertisement"]
                and not card["url"]
                and not card["text"]
            ):
                self.advertisements += 1
            elif (
                card["id"] is None
                or not card["url"]
                or card["title_count"] != 1
                or not card["slugs"]
            ):
                raise AdapterError("Incomplete native opportunity card")
            else:
                title = _feed_text(" ".join(card["title_parts"]))
                if not title or len(card["deadline_labels"]) > 1:
                    raise AdapterError(
                        "Ambiguous browse title or closing date"
                    )
                self.entries.append(
                    {
                        "id": card["id"],
                        "url": card["url"],
                        "title": title,
                        "slugs": card["slugs"],
                        "location": _feed_text(
                            " ".join(card["location_parts"])
                        )
                        or None,
                        "deadline_label": (
                            card["deadline_labels"][0]
                            if card["deadline_labels"]
                            else None
                        ),
                    }
                )
            self.current, self.location_depth, self.title_open = (
                None,
                None,
                False,
            )


_TAXONOMY = {
    "Scholarships": "scholarships",
    "Masters/Post Graduate": "scholarships",
    "PhD/Post Doctoral": "scholarships",
    "Undergraduate": "scholarships",
    "Internships": "internships",
    "Jobs": "jobs",
    "Voluntary": "volunteering",
    "Fellowships": "fellowships",
    "Workshops": "training",
    "Summer School/Winter School": "training",
    "Competitions": "competitions",
    "Awards": "competitions",
    "Grants": "grants",
}


def read_json(url):
    """Read a permitted JSON endpoint, returning its parsed body and headers."""
    text, headers = fetch_source(url, check_robots(url), with_headers=True)
    try:
        return json.loads(text), headers
    except (ValueError, TypeError) as error:
        raise AdapterError("Invalid publisher JSON response") from error


def deadline_value(entry):
    """Preserve the publisher's explicit Unix deadline, never invent a zone."""
    flag = entry.get("has-deadline")
    value = entry.get("deadline")
    if flag in (False, None, 0, "0", ""):
        # The publisher hides inactive legacy epochs and renders On Going.
        # An unused date field is not evidence of an actual closing instant.
        return None
    if flag not in (True, 1, "1") or isinstance(value, bool):
        raise AdapterError("Unknown publisher deadline representation")
    try:
        if not re.fullmatch(r"\d+", str(value)):
            raise ValueError()
        parsed = datetime.datetime.fromtimestamp(
            int(value), datetime.timezone.utc
        )
        if not 2000 <= parsed.year <= 2100:
            raise ValueError()
        return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError) as error:
        raise AdapterError("Invalid publisher Unix deadline") from error


def normalize_entry(entry, card, taxonomy):
    """Cross-check fresh compact facts against the complete native Browse card."""
    if (
        not isinstance(entry, dict)
        or type(entry.get("id")) is not int
        or entry["id"] != card["id"]
    ):
        raise AdapterError(
            f"Native ID {card['id']}: compact ID does not match"
        )
    parsed = urllib.parse.urlsplit(card["url"])
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.youthop.com"
        or parsed.query
        or parsed.fragment
        or not parsed.path.strip("/")
    ):
        raise AdapterError(f"Native ID {card['id']}: invalid canonical URL")
    field = "title"
    try:
        title = _feed_text(entry["title"])
        if not isinstance(entry["title"], str) or title != card["title"]:
            raise ValueError()
        field = "category memberships"
        by_slug = {value["slug"]: value for value in taxonomy.values()}
        tags = sorted({by_slug[slug]["name"] for slug in card["slugs"]})
        field = "primary category"
        primary = entry["category"]
        category = taxonomy[primary["id"]]
        if (
            type(primary["id"]) is not int
            or category["slug"] not in card["slugs"]
            or _feed_text(primary["name"]) != category["name"]
        ):
            raise ValueError()
        field = "self-link provenance"
        if entry["_links"]["self"] != [
            {"href": WEBSITE_URL + "wp-json/wp/v2/posts/" + str(card["id"])}
        ]:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise AdapterError(
            f"Native ID {card['id']}: incomplete or conflicting {field}"
        ) from error
    categories = list(
        dict.fromkeys(_TAXONOMY[tag] for tag in tags if tag in _TAXONOMY)
    ) or ["other"]
    record = make_record(
        title,
        card["url"],
        categories,
        published_at=None,
        tags=tags,
        method="publisher-complete-browse-and-compact-facts",
        evidence=[
            SOURCE_URL,
            WEBSITE_URL + "wp-json/wp/v2/posts/" + str(card["id"]),
        ],
    )
    record["id"] = SOURCE_ID + "-" + str(card["id"])
    record["classification"]["status"] = "classified"
    try:
        record["deadline"] = deadline_value(entry)
    except AdapterError as error:
        raise AdapterError(
            f"Native ID {card['id']}: invalid compact deadline"
        ) from error
    label = card["deadline_label"]
    if record["deadline"] is None:
        if label is not None:
            raise AdapterError(
                f"Native ID {card['id']}: inactive deadline conflicts "
                "with visible startDate"
            )
    else:
        try:
            matches = aware_timestamp(label) == aware_timestamp(
                record["deadline"]
            )
        except AdapterError as error:
            raise AdapterError(
                f"Native ID {card['id']}: invalid visible deadline startDate"
            ) from error
        if not matches:
            raise AdapterError(
                f"Native ID {card['id']}: compact deadline and "
                "visible startDate disagree"
            )
    record["status"] = (
        ("open" if record["deadline"] >= utc_now() else "expired")
        if record["deadline"]
        else "unknown"
    )
    # The visible native card is authoritative; compact region is not an
    # interchangeable label (e.g. "Multiple Region" vs "Multiple Regions").
    record["location"] = card["location"]
    summary = (
        "A Youth Opportunities listing classified as " + ", ".join(tags) + "."
    )
    if record["location"]:
        summary += " Listed location: " + record["location"] + "."
    summary += (
        " The publisher supplies a closing instant; check the linked "
        "announcement for eligibility and application instructions."
        if record["deadline"]
        else " No active closing instant is supplied; check the linked "
        "announcement for availability, eligibility and application instructions."
    )
    record["summary"] = summary
    return record


def collect_inventory():
    """Traverse the complete publisher-maintained Browse Opportunities index."""
    _ROBOTS_CACHE.clear()
    root = WEBSITE_URL + "wp-json/wp/v2/"
    taxonomy = {}
    category_page = 1
    expected_categories = None
    while True:
        entries, headers = fetch_inventory_page(
            read_json, root + f"categories?per_page=100&page={category_page}"
        )
        try:
            total, pages = int(headers["X-WP-Total"]), int(
                headers["X-WP-TotalPages"]
            )
            if (
                total <= 0
                or pages <= 0
                or not isinstance(entries, list)
                or not entries
                or len(entries) > 100
            ):
                raise ValueError()
            if expected_categories is None:
                expected_categories = (total, pages)
            if expected_categories != (total, pages):
                raise ValueError()
            for entry in entries:
                if entry["id"] in taxonomy:
                    raise ValueError()
                if (
                    type(entry["id"]) is not int
                    or entry["id"] <= 0
                    or not isinstance(entry["slug"], str)
                    or not re.fullmatch(
                        r"[a-z0-9]+(?:-[a-z0-9]+)*", entry["slug"]
                    )
                ):
                    raise ValueError()
                taxonomy[entry["id"]] = {
                    "name": html.unescape(entry["name"]),
                    "slug": entry["slug"],
                }
        except (KeyError, TypeError, ValueError) as error:
            raise AdapterError(
                "Invalid or changing publisher taxonomy"
            ) from error
        if category_page == pages:
            break
        category_page += 1
    if (
        len(taxonomy) != total
        or len({entry["slug"] for entry in taxonomy.values()}) != total
    ):
        raise AdapterError(
            "Incomplete or ambiguous publisher category inventory"
        )
    cards = []
    advertisements = 0
    seen = set()
    page = 1
    expected_pages = expected_size = None
    while True:
        url = SOURCE_URL + (f"?sf_paged={page}" if page > 1 else "")
        listing = BrowsePage(
            fetch_inventory_page(fetch_source, url, check_robots(url))
        )
        pages = max(listing.pages)
        advertisements += listing.advertisements
        if expected_pages is None:
            expected_pages, expected_size = pages, len(listing.entries)
        if pages != expected_pages:
            raise AdapterError(
                "Publisher browse pagination changed during retrieval"
            )
        remaining_pages = expected_pages - page
        possible_batches = (expected_pages * expected_size + 99) // 100
        check_workload(remaining_pages + possible_batches + 1)
        if (
            len(listing.entries) > expected_size
            or page < pages
            and len(listing.entries) != expected_size
        ):
            raise AdapterError("Incomplete publisher browse page")
        for card in listing.entries:
            if card["id"] in seen:
                raise AdapterError("Duplicate publisher browse identifier")
            seen.add(card["id"])
            cards.append(card)
        print(
            f"Browse page {page}/{pages}: {len(cards)} opportunities",
            file=sys.stderr,
        )
        if page == pages:
            break
        page += 1
    # Detect shifts during the complete paginated Browse collection.
    first = BrowsePage(
        fetch_inventory_page(
            fetch_source, SOURCE_URL, check_robots(SOURCE_URL)
        )
    )
    if (
        first.entries != cards[:expected_size]
        or max(first.pages) != expected_pages
    ):
        raise AdapterError("Publisher browse index changed during pagination")
    records, reconciled = [], set()
    batch_count = (len(cards) + 99) // 100
    check_workload(batch_count)
    for offset in range(0, len(cards), 100):
        batch = cards[offset : offset + 100]
        requested = {card["id"] for card in batch}
        ids = ",".join(str(card["id"]) for card in batch)
        check_workload(batch_count - offset // 100)
        entries, headers = fetch_inventory_page(
            read_json, root + "posts?include=" + ids + "&per_page=100"
        )
        if (
            not isinstance(entries, list)
            or len(entries) != len(batch)
            or headers.get("X-WP-Total") != str(len(batch))
            or headers.get("X-WP-TotalPages") != "1"
        ):
            raise AdapterError("Incomplete compact batch inventory")
        by_id = {}
        for entry in entries:
            identity = entry.get("id") if isinstance(entry, dict) else None
            if (
                type(identity) is not int
                or identity not in requested
                or identity in by_id
                or identity in reconciled
            ):
                raise AdapterError("Duplicate or unexpected compact native ID")
            by_id[identity] = entry
        if set(by_id) != requested:
            raise AdapterError("Compact batch native ID coverage mismatch")
        for card in batch:
            records.append(normalize_entry(by_id[card["id"]], card, taxonomy))
        reconciled.update(by_id)
        print(
            f"Reconciled {len(records)}/{len(cards)} native Browse calls "
            "with compact facts",
            file=sys.stderr,
        )
    if reconciled != seen or len(records) != len(cards):
        raise AdapterError(
            "Complete Browse and compact native inventories disagree"
        )
    print(
        json.dumps(
            {
                "inventory_scope": "complete-publisher-browse-opportunities",
                "pages": expected_pages,
                "inventory_posts": len(cards),
                "excluded_advertisements": advertisements,
                "retained": len(records),
                "compact_batches": batch_count,
                "detail_requests": 0,
            }
        ),
        file=sys.stderr,
    )
    validate_factual_records(records)
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
                or type(job.get("run_id")) is not int
                or job["run_id"] != identity
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt
                or job.get("head_sha") != run["head_sha"]
                or job.get("head_branch") != run["head_branch"]
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
    _SCANNED_COMPLETED[(identity, attempt)] = {
        "head_sha": run["head_sha"],
        "jobs_sha256": hashlib.sha256(
            json.dumps(
                response, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "jobs": jobs,
    }
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
    global _SCANNED_COMPLETED
    _SCANNED_COMPLETED = {}
    current_id, current_attempt = current_run_identity()
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    current_seen = False
    for page in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-youth-opportunities.yml/runs"
            f"?per_page=100&page={page}"
        )
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
                if run.get("status") not in (
                    "in_progress",
                    "queued",
                    "waiting",
                    "pending",
                    "requested",
                ):
                    raise AdapterError(
                        "Current workflow status is not active", "access"
                    )
                current_seen = True
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
            if not current_seen:
                raise AdapterError(
                    "Current workflow attempt missing from history", "access"
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
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = http_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
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
    try:
        status, headers, _ = http_bytes(
            _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip",
            headers={
                "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
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
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            "Invalid or unavailable source pacing artifact", "access"
        ) from None


def merge_budget(local, incoming, now):
    """Intersect retained limits without weakening refusals or rate history."""
    local = validate_budget(local)
    incoming = validate_budget(incoming)
    merged = empty_budget(now)
    merged["until"] = max(local["until"], incoming["until"], now + 60)
    merged["interval"] = max(local["interval"], incoming["interval"])
    merged["page_interval"] = max(
        local["page_interval"], incoming["page_interval"]
    )
    merged["rate_failures"] = max(
        local["rate_failures"], incoming["rate_failures"]
    )
    merged["legacy_rate_history_unknown"] = (
        local["legacy_rate_history_unknown"]
        or incoming["legacy_rate_history_unknown"]
    )
    merged["blocked"] = local["blocked"] or incoming["blocked"]
    if local.get("unassessed_response_failure") or incoming.get(
        "unassessed_response_failure"
    ):
        merged["unassessed_response_failure"] = True
    merged["starts"] = sorted(set(local["starts"] + incoming["starts"]))[-5:]
    return validate_budget(merged)


def reviewed_legacy_transition(latest):
    """Bind the exact reviewed seven-attempt pre-artifact history."""
    expected = (38763419724, 1, SOURCE_ID)
    old_head = "f77eed642c4544bbeed009440c406064a4f5c6ad"
    completed_at = "2026-10-15T15:49:53Z"
    job_digest = (
        "34e687ecadae8c6e44416850229ddcf3650dc4d05f6156e5630926ab0c9efa9f"
    )
    reviewed = {
        (38623035090, 1): "5f82ad39165005553048f1377a0fbd844aaa50cf",
        (38645520096, 1): "ecab24b35f1012e12cab4bb22c6c3210db48bc7b",
        (38684236080, 1): "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        (38685114252, 1): "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        (38720084082, 1): "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        (38721259301, 1): "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        (38763419724, 1): old_head,
    }
    try:
        config = json.loads(os.environ.get("YO_PACING_TRANSITION", ""))
        required = {
            "schema",
            "family",
            "prior_run_id",
            "prior_run_attempt",
            "prior_head_sha",
            "prior_completed_at",
            "prior_jobs_sha256",
            "state",
            "not_before",
            "evidence",
        }
        if (
            set(config) != required
            or type(config["schema"]) is not int
            or config["schema"] != 1
            or config["family"] != FAMILY
            or latest != expected
            or type(config["prior_run_id"]) is not int
            or config["prior_run_id"] != expected[0]
            or type(config["prior_run_attempt"]) is not int
            or config["prior_run_attempt"] != expected[1]
            or config["prior_head_sha"] != old_head
            or config["prior_completed_at"] != completed_at
            or config["prior_jobs_sha256"] != job_digest
            or {
                key: proof["head_sha"]
                for key, proof in _SCANNED_COMPLETED.items()
            }
            != reviewed
            or not numeric(config["not_before"])
            or not isinstance(config["evidence"], str)
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/(?:issues|pull)/[0-9]+#issuecomment-[0-9]+",
                config["evidence"],
            )
        ):
            raise ValueError()
        run = workflow_api(
            f"/actions/runs/{expected[0]}/attempts/{expected[1]}"
        )
        finished, _, _, _, _ = completed_identity(
            run, expected, expected_head=old_head
        )
        if finished != aware_timestamp(completed_at):
            raise ValueError()
        proof = _SCANNED_COMPLETED[expected[:2]]
        if (
            run.get("conclusion") != "cancelled"
            or proof["jobs_sha256"] != job_digest
            or len(proof["jobs"]) != 1
        ):
            raise ValueError()
        job = proof["jobs"][0]
        if (
            job.get("id") != 116520331641
            or job.get("conclusion") != "cancelled"
            or job.get("runner_id") != 0
            or job.get("runner_name") != ""
            or job.get("steps") != []
        ):
            raise ValueError()
        incoming = validate_budget(config["state"])
        if incoming["legacy_rate_history_unknown"] is not True:
            raise ValueError()
        if config["not_before"] < max(
            finished + 60, incoming["until"], incoming["observed_at"] + 60
        ):
            raise ValueError()
        incoming["until"] = max(incoming["until"], config["not_before"])
        return incoming
    except Exception:
        raise AdapterError(
            "Exact old-attempt transition requires independent reviewed "
            "full-state approval",
            "access",
        ) from None


def restore_family_artifact():
    """Require the exact newest completed source attempt, never an older hit."""
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raise AdapterError(
            "Existing source requires exact completed-attempt history",
            "access",
        )
    else:
        run_id, attempt, _ = latest
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or len(artifacts) > 100
            or result.get("total_count") != len(artifacts)
        ):
            raise AdapterError(
                "Incomplete pacing artifact inventory", "access"
            )
        matches = [
            a
            for a in artifacts
            if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
        ]
        if not artifacts:
            incoming = reviewed_legacy_transition(latest)
        else:
            if len(matches) != 1 or matches[0].get("expired") is not False:
                raise AdapterError(
                    "Latest attempt pacing artifact missing or expired",
                    "access",
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
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("YO_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    with artifact_output(path) as descriptor:
        run_id, attempt = current_run_identity()
        with budget_state() as local:
            state = dict(local)
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "state": state,
        }
        with os.fdopen(os.dup(descriptor), "w") as file:
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())


def github(method, path, payload=None, missing_ok=False):
    """Call only the configured publication repository with scoped credentials.

    Return the decoded JSON response, or None for an allowed missing resource.
    The optional payload is a JSON-serializable object.
    """
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token and method != "GET":
        raise AdapterError(
            "DATA_SOURCE_TOKEN is required for publication", "publish"
        )
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
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
        "display_name": "Youth Opportunities",
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
        "inventory_scope": "complete-publisher-browse-opportunities",
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

    Unzoned closing dates use UTC-12 as a conservative world-wide boundary;
    no publisher timezone is inferred. Explicit offsets retain their instant.
    """
    validate_records(records)
    checked = datetime.datetime.fromisoformat(
        checked_at.replace("Z", "+00:00")
    )
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


def run_refresh(
    publish, *, raw=None, prepared_failure=None, snapshot=None, attempt=None
):
    """Collect, validate and optionally publish one source outcome safely."""
    snapshot = (
        (snapshot if snapshot is not None else read_snapshot())
        if publish
        else None
    )
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
        validate_records(old, allow_empty=True, allow_legacy_ids=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = attempt or utc_now(), None
    try:
        if prepared_failure is not None:
            raise prepared_failure
        records = collect() if raw is None else raw
        validate_factual_records(records)
        records = remove_expired_records(records, utc_now())
        prior = {
            record["id"].replace(SOURCE_ID + ":", SOURCE_ID + "-", 1): record
            for record in old
        }
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
                last_seen_at=record.get("last_seen_at", attempt),
                last_checked_at=record.get("last_checked_at", attempt),
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


_PHASE_SECONDS = 9600
_COLLECTION_DEADLINE = None
_RESPONSE_SECONDS = []
_HANDOFF_LIMIT = 8_000_000


def pair_digests(snapshot):
    return {
        name: (
            None
            if value is None
            else hashlib.sha256(value.encode()).hexdigest()
        )
        for name, value in snapshot["files"].items()
    }


def phase_identity():
    run_id, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[a-f0-9]{40}", head):
        raise AdapterError("Missing immutable workflow head", "publish")
    with open(__file__, "rb") as file:
        code_hash = hashlib.sha256(file.read()).hexdigest()
    return {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "ref": "refs/heads/main",
        "source": SOURCE_ID,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": head,
        "code_sha256": code_hash,
    }


def aware_timestamp(value):
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError):
        raise AdapterError("Invalid handoff timestamp", "publish") from None


@contextlib.contextmanager
def handoff_directory(create=False):
    identity = phase_identity()
    root = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(root):
        raise AdapterError("Owned RUNNER_TEMP required", "publish")
    info = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise AdapterError("Unsafe runner temporary directory", "publish")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = f"yo-handoff-{identity['run_id']}-{identity['run_attempt']}"
    directory_fd = None
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=root_fd)
            except FileExistsError:
                pass
        directory_fd = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd
        )
        info = os.fstat(directory_fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AdapterError("Unsafe handoff directory", "publish")
        yield directory_fd
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(root_fd)


def canonical_bytes(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def write_handoff(payload):
    content = {"identity": phase_identity(), "payload": payload}
    content["digest"] = hashlib.sha256(canonical_bytes(content)).hexdigest()
    body = canonical_bytes(content)
    if len(body) > _HANDOFF_LIMIT:
        raise AdapterError(
            "Complete handoff exceeds reviewed byte limit", "publish"
        )
    with handoff_directory(create=True) as directory_fd:
        fd = os.open(
            "pending.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory_fd,
        )
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(body)
                file.flush()
                os.fsync(file.fileno())
            os.replace(
                "pending.json",
                "handoff.json",
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink("pending.json", dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def read_handoff():
    with handoff_directory() as directory_fd:
        fd = os.open(
            "handoff.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd
        )
        with os.fdopen(fd, "rb") as file:
            info = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise AdapterError("Unsafe prepared outcome file", "publish")
            body = file.read(_HANDOFF_LIMIT + 1)
    if len(body) > _HANDOFF_LIMIT:
        raise AdapterError("Prepared outcome exceeds byte limit", "publish")
    try:
        content = json.loads(body)
        if set(content) != {
            "identity",
            "payload",
            "digest",
        } or canonical_bytes(content["identity"]) != canonical_bytes(
            phase_identity()
        ):
            raise ValueError()
        digest = content.pop("digest")
        if (
            not isinstance(digest, str)
            or digest != hashlib.sha256(canonical_bytes(content)).hexdigest()
        ):
            raise ValueError()
        payload = content["payload"]
        if set(payload) != {
            "started_at",
            "completed_at",
            "result",
            "complete",
            "count",
            "records",
            "error",
            "failure_stage",
            "baseline",
            "state",
        }:
            raise ValueError()
        started, completed = aware_timestamp(
            payload["started_at"]
        ), aware_timestamp(payload["completed_at"])
        now = time.time()
        if not (
            started <= completed <= now + 5
            and completed - started <= 10800
            and 0 <= now - completed <= 900
        ):
            raise ValueError()
        baseline = payload["baseline"]
        if set(baseline) != {"data.json", "metadata.json"} or any(
            v is not None
            and (not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v))
            for v in baseline.values()
        ):
            raise ValueError()
        validate_budget(payload["state"])
        if type(payload["count"]) is not int or payload["count"] != len(
            payload["records"]
        ):
            raise ValueError()
        if payload["result"] == "success":
            if payload["state"].get("unassessed_response_failure"):
                raise ValueError()
            if (
                payload["complete"] is not True
                or payload["error"] is not None
                or payload["failure_stage"] is not None
            ):
                raise ValueError()
            validate_factual_records(payload["records"])
        elif payload["result"] == "failure":
            if (
                payload["complete"] is not False
                or payload["records"] != []
                or not isinstance(payload["error"], str)
                or not payload["error"]
                or len(payload["error"]) > 500
                or payload["failure_stage"]
                not in ("fetch", "parse", "validate", "access", "publish")
            ):
                raise ValueError()
        else:
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AdapterError(
            "Invalid, stale or incomplete prepared outcome", "publish"
        ) from None


def delete_handoff():
    try:
        with handoff_directory() as directory_fd:
            for name in ("handoff.json", "pending.json"):
                try:
                    os.unlink(name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass


def set_collection_deadline():
    global _COLLECTION_DEADLINE
    now = time.time()
    deadline = now + _PHASE_SECONDS
    if os.environ.get("GITHUB_ACTIONS") == "true":
        run_id, attempt = current_run_identity()
        inventory = workflow_api(
            f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
        )
        jobs = inventory.get("jobs", [])
        if (
            inventory.get("total_count") != len(jobs)
            or len(jobs) != 1
            or jobs[0].get("status") != "in_progress"
        ):
            raise AdapterError(
                "Current job budget chronology unavailable", "access"
            )
        began = aware_timestamp(jobs[0].get("started_at"))
        if not now - 10800 <= began <= now + 5:
            raise AdapterError("Invalid current job start", "access")
        deadline = min(deadline, began + 10800 - 1200)
    if deadline <= now + 300:
        raise AdapterError("Insufficient remaining collection budget", "fetch")
    _COLLECTION_DEADLINE = deadline


def check_workload(remaining):
    if _COLLECTION_DEADLINE is None:
        return
    with budget_state() as state:
        interval = max(state["interval"], state["page_interval"])
    response_margin = max(
        5,
        (
            sum(_RESPONSE_SECONDS) / len(_RESPONSE_SECONDS)
            if _RESPONSE_SECONDS
            else 5
        ),
    )
    if (
        time.time() + remaining * (interval + response_margin) + 120
        > _COLLECTION_DEADLINE
    ):
        raise AdapterError(
            "Complete inventory cannot fit retained collection budget; "
            "no truncation permitted",
            "fetch",
        )


def prepare_phase():
    """Collect without publication credentials and stage one trusted outcome."""
    if os.environ.get("DATA_SOURCE_TOKEN"):
        raise AdapterError(
            "Prepare phase must not receive publication token", "publish"
        )
    set_collection_deadline()
    snapshot = read_snapshot()
    if snapshot["files"]["data.json"] is not None:
        old = json.loads(snapshot["files"]["data.json"])
        validate_records(old, allow_empty=True, allow_legacy_ids=True)
        previous = json.loads(snapshot["files"]["metadata.json"])
        if previous.get("source") != SOURCE_ID:
            raise AdapterError("Baseline metadata source mismatch", "publish")
    else:
        old = []
    started, records, failure = utc_now(), [], None
    try:
        # Prior size estimates work only. The fresh native traversal proves
        # actual coverage.
        check_workload((len(old) + 14) // 15 + (len(old) + 99) // 100 + 2)
        records = collect()
        validate_factual_records(records)
    except Exception as error:
        failure = error
        records = []
    with budget_state() as state:
        retained_state = json.loads(json.dumps(state))
    payload = {
        "started_at": started,
        "completed_at": utc_now(),
        "result": "failure" if failure else "success",
        "complete": failure is None,
        "count": len(records),
        "records": records,
        "error": safe_error(failure) if failure else None,
        "failure_stage": (
            getattr(failure, "stage", "parse") if failure else None
        ),
        "baseline": pair_digests(snapshot),
        "state": retained_state,
    }
    write_handoff(payload)
    read_handoff()
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as file:
            file.write("handoff_ready=true\n")
    print(
        json.dumps(
            {
                "source": SOURCE_ID,
                "status": payload["result"],
                "prepared_count": len(records),
                "message": "Prepared outcome; publication has not occurred",
            }
        )
    )
    return 1 if failure else 0


def publish_phase():
    """Validate the same-attempt outcome and publish with a late fresh token."""
    global _STATE_TRUSTED
    payload = read_handoff()
    with budget_state() as state:
        merged = merge_budget(dict(state), payload["state"], time.time())
        state.clear()
        state.update(merged)
    _STATE_TRUSTED = True
    if not os.environ.get("DATA_SOURCE_TOKEN"):
        raise AdapterError(
            "Late publication credential unavailable; "
            "last-good data preserved",
            "publish",
        )
    snapshot = read_snapshot()
    if pair_digests(snapshot) != payload["baseline"]:
        raise AdapterError(
            "Source pair changed after collection baseline; no files written",
            "publish",
        )
    if (
        merged.get("unassessed_response_failure")
        and payload["result"] == "success"
    ):
        raise AdapterError(
            "Unassessed publisher response prevents publication", "access"
        )
    failure = (
        AdapterError(payload["error"], payload["failure_stage"])
        if payload["result"] == "failure"
        else None
    )
    return run_refresh(
        True,
        raw=payload["records"],
        prepared_failure=failure,
        snapshot=snapshot,
        attempt=payload["started_at"],
    )


def main():
    """Run a standalone refresh or the two credential-separated hosted phases."""
    global _STATE_TRUSTED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    phase = os.environ.get("YO_PHASE")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        phase not in (None, "prepare", "publish")
        or (phase and (not hosted or not args.publish))
        or (hosted and args.publish and phase is None)
    ):
        raise AdapterError("Invalid source workflow phase", "publish")
    try:
        with collection_lock():
            try:
                if phase == "publish":
                    return publish_phase()
                if hosted and args.publish:
                    restore_family_artifact()
                else:
                    with budget_state():
                        pass
                _STATE_TRUSTED = True
                if phase == "prepare":
                    return prepare_phase()
                set_collection_deadline()
                return run_refresh(args.publish)
            finally:
                export_family_artifact()
    finally:
        if phase == "publish":
            delete_handoff()


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
