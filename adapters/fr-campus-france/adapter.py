"""Index minimal programme facts from the official CampusBourses UI."""

import argparse
import base64
import zipfile
import stat
import math
import ipaddress
import io
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
import urllib.robotparser

SOURCE_ID = "fr-campus-france"
SOURCE_URL = "https://campusbourses.campusfrance.org/?lang=en"
WEBSITE_URL = "https://campusbourses.campusfrance.org/"
LANGUAGE = "und"
PUBLISHER_COUNTRY = "FR"
PUBLISHER_TYPE = "public-institution"
ATTRIBUTION = "Source: CampusBourses / Campus France"
SERVICE_URL = "https://bourses-api.campusfrance.org/"
INDEX_URL = SERVICE_URL + "sgetgrants/en"
SCRIPT_PATH = "js/ilangular.b81726ae.js"
SCRIPT_URL = WEBSITE_URL + SCRIPT_PATH
POLICY_URL = (
    "https://www.campusfrance.org/fr/conditions-generales-d-utilisation"
)
REVIEWED_POLICY_HASH = (
    "2494fb8e6483716f946524b3897fe0596999feb67473e1e99a679db4fbd5bf40"
)
TYPE_LABELS = {
    "1": "Study",
    "2": "Research",
    "3": "Internship",
    "4": "Travel",
    "5": "Partial tuition fee waivers",
    "6": "Partial fee waivers (CVEC, Health insurance)",
}
_ALLOWED_HOSTS = {
    "campusbourses.campusfrance.org",
    "bourses-api.campusfrance.org",
    "www.campusfrance.org",
}
_ROBOTS = {}
_PAGE_COMPLETED = None
_COLLECTION_END = None

_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-campus-france-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 900
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "campus-france-pacing-state"
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


def pace(url, interval=12.2):
    """Serialize source requests, retaining every stronger restored limit."""
    global _PAGE_INTERVAL
    state = locked_budget()
    try:
        budget = load_budget()
        if budget["blocked"]:
            raise AdapterError(
                "Retained publisher refusal requires review", "access"
            )
        if budget["rate_limit_strikes"] >= 100:
            raise AdapterError(
                "Publisher rate-limit history requires review", "access", 429
            )
        if budget["rate_limit_until"] > time.time():
            raise AdapterError(
                "Retained publisher rate-limit embargo remains active",
                "access",
                429,
            )
        budget["interval"] = max(budget["interval"], interval, 12.2)
        _PAGE_INTERVAL = max(_PAGE_INTERVAL, budget["page_interval"], 30)
        while True:
            if (
                _COLLECTION_END is not None
                and time.monotonic() >= _COLLECTION_END
            ):
                raise AdapterError(
                    "Complete collection exceeded fifteen minutes", "fetch"
                )
            now = time.time()
            starts = [t for t in budget["starts"] if now - t < 60]
            ready = max(
                budget["not_before"],
                budget["completed"] + budget["interval"],
                starts[-1] + budget["interval"] if starts else now,
                starts[-5] + 60 if len(starts) >= 5 else now,
            )
            if (
                _COLLECTION_END is not None
                and ready - now + 30 > _COLLECTION_END - time.monotonic()
            ):
                raise AdapterError(
                    "Retained pacing exceeds collection budget", "access"
                )
            if ready <= now:
                budget["starts"] = starts + [now]
                budget["observed_at"] = now
                write_budget(budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def request_bytes(
    url,
    *,
    interval=12.2,
    method="GET",
    headers=None,
    payload=None,
    byte_limit=5_000_000,
    robots=False,
):
    publisher = urllib.parse.urlsplit(url).hostname in _ALLOWED_HOSTS
    state, budget = pace(url, interval) if publisher else (None, None)
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={**(headers or {}), "User-Agent": _USER_AGENT},
        )
        try:
            response = _OPENER.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if publisher:
                value = response.headers.get("Retry-After")
                if value:
                    try:
                        if re.fullmatch(r"\d+(?:\.\d+)?", value):
                            seconds = float(value)
                        else:
                            date = parsedate_to_datetime(value)
                            if date.tzinfo is None:
                                raise ValueError(
                                    "Retry-After requires timezone"
                                )
                            seconds = date.timestamp() - time.time()
                        if not math.isfinite(seconds) or seconds <= 0:
                            raise ValueError("Unusable Retry-After")
                        until = time.time() + seconds
                        datetime.fromtimestamp(until, timezone.utc)
                        budget["not_before"] = max(budget["not_before"], until)
                    except (ValueError, TypeError, OverflowError, OSError):
                        pass
                if response.status in {401, 403} and not robots:
                    budget["blocked"] = True
                if response.status == 429:
                    strikes = budget["rate_limit_strikes"] + 1
                    budget["rate_limit_strikes"] = strikes
                    factor = 2**strikes
                    budget["interval"] = max(
                        budget["interval"] * 2, 12.2 * factor
                    )
                    budget["page_interval"] = max(
                        budget["page_interval"] * 2, 30 * factor
                    )
                    budget["not_before"] = max(
                        budget["not_before"],
                        time.time() + 1800 * (2 ** (strikes - 1)),
                    )
                    budget["rate_limit_until"] = max(
                        budget["rate_limit_until"], budget["not_before"]
                    )
            body = response.read(byte_limit + 1)
            if len(body) > byte_limit:
                raise AdapterError(
                    "Response exceeds bounded native limit", "fetch"
                )
            return response.status, response.headers, body
    finally:
        if state is not None:
            budget["completed"] = budget["observed_at"] = time.time()
            write_budget(budget)
            state.close()


def fetch_source(url, min_interval=12.2):
    status, headers, body = source_request(url, interval=min_interval)
    return body.decode(headers.get_content_charset() or "utf-8", "strict")


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    origin = parsed.scheme + "://" + parsed.netloc
    if origin not in _ROBOTS:
        status, _, body = source_request(origin + "/robots.txt", robots=True)
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
        elif status == 200:
            text = body.decode("utf-8-sig", "strict")
            if "<html" in text.lower() or "<!doctype" in text.lower():
                raise AdapterError("Robots endpoint returned HTML", "access")
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(text.splitlines())
            _ROBOTS[origin] = parser
        else:
            raise AdapterError(
                "Robots temporarily unavailable", "access", status
            )
    parser = _ROBOTS[origin]
    if parser is None:
        return 12.2
    policies = [parser]
    for entry in parser.entries:
        if entry.applies_to(_USER_AGENT):
            single = copy.copy(parser)
            single.entries = [entry]
            single.default_entry = None
            policies.append(single)
    if not all(p.can_fetch(_USER_AGENT, url) for p in policies):
        raise AdapterError("Publisher robots disallows this route", "access")
    intervals = [12.2]
    for policy in policies:
        delay = policy.crawl_delay(_USER_AGENT)
        rate = policy.request_rate(_USER_AGENT)
        if delay is not None:
            intervals.append(delay)
        if rate:
            if rate.requests <= 0:
                raise AdapterError("Invalid robots request rate", "access")
            intervals.append(rate.seconds / rate.requests)
    return max(intervals)


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
                    expired = (
                        checked.astimezone(latest_zone).date() > closing_date
                    )
                else:
                    deadline = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = (
                        deadline.tzinfo is not None and checked > deadline
                    )
            except (ValueError, TypeError):
                # Missing or ambiguous deadlines never imply expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, published=False):
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
            if (
                not isinstance(record.get(field), str)
                or not record[field].strip()
            ):
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
        if (
            not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", record["id"])
            or record["id"].lower() in ids
        ):
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].lower())
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
        native = re.fullmatch(
            re.escape(SOURCE_ID) + r"-([1-9][0-9]{0,11})", record["id"]
        )
        if not native or record["url"] != programme_url(int(native[1])):
            raise AdapterError(
                "Native programme identity/link mismatch", "validate"
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
            raise AdapterError(
                "Invalid existing source blob identity", "publish"
            )
        if file.get("encoding") != "base64":
            file = github("GET", "/git/blobs/" + expected)
        if file.get("encoding") != "base64" or file.get("sha") != expected:
            raise AdapterError("Unsupported existing source blob", "publish")
        try:
            raw = base64.b64decode(
                re.sub(r"\s+", "", file["content"]), validate=True
            )
            actual = hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()
            if actual != expected:
                raise ValueError("Source blob digest mismatch")
            files[name] = raw.decode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise AdapterError(
                "Invalid existing source blob bytes", "publish"
            ) from None
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish"
        )
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
    result = {
        "display_name": "Campus France",
        "source": SOURCE_ID,
        "name": "CampusBourses / Campus France",
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
            previous.get("last_checked_at")
            if error
            else checked_at
            or max(
                (record["last_checked_at"] for record in records),
                default=attempt,
            )
        ),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (
                f"Validated refresh retains {len(records)} "
                "CampusBourses financial-support programme records"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


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
        "interval",
        "page_interval",
        "rate_limit_strikes",
        "rate_limit_until",
        "completed",
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
    if (
        any(
            not numeric(state.get(key))
            for key in (
                "interval",
                "page_interval",
                "rate_limit_until",
                "completed",
            )
        )
        or state["interval"] < 12.2
        or state["page_interval"] < 30
        or type(state["rate_limit_strikes"]) is not int
        or not 0 <= state["rate_limit_strikes"] <= 100
    ):
        raise AdapterError("Invalid persisted pacing/backoff", "access")
    if state["completed"] > state["observed_at"]:
        raise AdapterError("Invalid publisher completion timestamp", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": now + 60,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "page_interval": 30,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "completed": 0,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK
    if _STATE_PATH:
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + ("-") + str(os.getuid())
    )
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AdapterError("Unsafe publisher pacing directory", "access")
    if info.st_mode & 0o077:
        raise AdapterError("Unsafe publisher pacing permissions", "access")
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")


def locked_budget():
    budget_file()
    descriptor = os.open(
        _STATE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    info = os.fstat(descriptor)
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or info.st_nlink != 1
    ):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher pacing lock", "access")
    file = os.fdopen(descriptor, "a+")
    fcntl.flock(file, fcntl.LOCK_EX)
    return file


def load_budget():
    try:
        fd = os.open(_STATE_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return empty_budget(time.time())
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe publisher pacing state owner", "access")
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher pacing state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError(
            "Corrupt publisher pacing state", "access"
        ) from None


def write_budget(state):
    validate_budget(state)
    fd, name = tempfile.mkstemp(
        prefix=("pending-"), dir=os.path.dirname(_STATE_PATH)
    )
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, _STATE_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


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
        return finished, began, identity, attempt, source
    except Exception:
        raise AdapterError(
            ("Untrusted completed family attempt"), ("access")
        ) from None


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
                            (run.get("head_sha"), run.get("workflow_id")),
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
        raw = os.environ.get("CAMPUS_FRANCE_PACING_BOOTSTRAP", "")
        try:
            bootstrap = json.loads(raw)
            if (
                set(bootstrap)
                != {"schema", "family", "not_before", "evidence", "state"}
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
            incoming = validate_budget(bootstrap["state"])
            incoming["not_before"] = max(
                incoming["not_before"],
                time.time() + 60,
                bootstrap["not_before"],
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
            raise AdapterError(
                "Incomplete family artifact inventory", "access"
            )
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
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined[("not_before")] = max(
            local[("not_before")], incoming[("not_before")], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        for key in (
            "interval",
            "page_interval",
            "rate_limit_strikes",
            "rate_limit_until",
            "completed",
        ):
            combined[key] = max(local[key], incoming[key])
        combined[("starts")] = sorted(
            set(local[("starts")] + incoming[("starts")])
        )[-10:]
        write_budget(combined)
    _RESTORED, _STATE_TRUSTED = True, True


def export_family_artifact():
    """Best-effort infrastructure state; never contains records or secrets."""
    path = os.environ.get("CAMPUS_FRANCE_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
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
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError(
            ("Explicit runner temporary directory required"), ("access")
        )
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as file:
        json.dump(envelope, file, separators=(",", ":"))


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    _COLLECTION_STARTED = time.time()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(os.path.dirname(_STATE_PATH), "collection.lock")
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            if os.fstat(descriptor).st_uid != os.getuid():
                raise OSError("Wrong lock owner")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise AdapterError(
                ("Another local family collector is active"), ("access")
            ) from None
        _COLLECTION_LOCK_FD = descriptor
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    _STATE_TRUSTED = True


class InventoryRace(AdapterError):
    """The advertised complete workflow inventory changed during reading."""


def request_timeout():
    return 30


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


def reject_visible_challenge(body, headers, *, challenge_only=False):
    if "html" not in headers.get("Content-Type", "").lower():
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
    if not challenge_only:
        phrases += (
            "access denied",
            "permission denied",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    if any(phrase in text for phrase in phrases):
        with locked_budget():
            state = load_budget()
            state["blocked"] = True
            state["observed_at"] = time.time()
            write_budget(state)
        raise AdapterError("Visible publisher challenge or denial", "access")


def source_request(
    url, *, interval=12.2, method="GET", payload=None, robots=False
):
    """Use only literal official public hosts; preserve redirect method/body."""
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError("Robots retrieval requires robots.txt", "access")
    current = url
    for hop in range(4):
        parsed = urllib.parse.urlsplit(current)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise AdapterError("Unreviewed public source URL", "access")
        if not robots:
            interval = max(interval, check_robots(current))
        status, headers, body = request_bytes(
            current,
            interval=interval,
            method=method,
            payload=payload,
            robots=robots,
            headers=(
                {"Content-Type": "application/json; charset=utf-8"}
                if payload is not None
                else None
            ),
        )
        if not robots:
            reject_visible_challenge(
                body, headers, challenge_only=status == 429
            )
        if status in {301, 302, 303, 307, 308}:
            if not headers.get("Location") or hop == 3:
                raise AdapterError("Invalid source redirect", "fetch")
            target = urllib.parse.urljoin(current, headers["Location"])
            if urllib.parse.urlsplit(target).hostname != parsed.hostname:
                raise AdapterError(
                    "Redirect changes reviewed source host", "access"
                )
            if robots and urllib.parse.urlsplit(target).path != "/robots.txt":
                raise AdapterError(
                    "Robots redirect leaves the policy route", "access"
                )
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


def fetch_inventory_page(fetch, *args, **kwargs):
    """Keep inventory partitions thirty seconds apart after completion."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            wait = _PAGE_COMPLETED + _PAGE_INTERVAL - time.monotonic()
            if (
                _COLLECTION_END is not None
                and wait + 30 > _COLLECTION_END - time.monotonic()
            ):
                raise AdapterError(
                    "Retained page gap exceeds collection budget", "access"
                )
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


class PolicyText(HTMLParser):
    """Read the visible main legal-page body, excluding unrelated chrome."""

    def __init__(self):
        super().__init__()
        self.main = False
        self.sections = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.main = True
            self.sections += 1

    def handle_endtag(self, tag):
        if tag == "main":
            self.main = False

    def handle_data(self, data):
        if self.main:
            self.parts.append(data)


def clean(value):
    if not isinstance(value, str):
        raise AdapterError("Invalid publisher text", "parse")
    text = " ".join(unescape(value).split())
    if not text or re.search(r"<[^>]+>", text):
        raise AdapterError("Empty or nonliteral publisher text", "parse")
    return text


def read_configuration(script):
    if (
        'apiUrl:"' + SERVICE_URL + '"' not in script
        or 'rootUrl:"https://campusbourses.campusfrance.org/"' not in script
        or 'apiUrl+"sgetgrants/"+e' not in script
        or '"ws/cfgrant.php?id="+e.bourse_id+"_"+d.use()' not in script
    ):
        raise AdapterError("Reviewed public UI routes changed", "access")
    match = re.search(
        r'factory\("IwType",\[function\(\)\{var l=\[(.*?)\];return',
        script,
    )
    if not match:
        raise AdapterError(
            "Public financial-support taxonomy missing", "parse"
        )
    labels = re.findall(
        r'\{id:([1-9][0-9]*),fr:"[^"]*",en:"([^"]*)",es:"[^"]*"\}',
        match[1],
    )
    if len(labels) != 6 or dict(labels) != TYPE_LABELS:
        raise AdapterError(
            "Publisher financial-support taxonomy changed", "parse"
        )


def read_frontier():
    _, _, body = fetch_inventory_page(source_request, INDEX_URL)
    try:
        result = json.loads(body)
    except (ValueError, UnicodeError):
        raise AdapterError("Invalid public catalogue JSON", "parse") from None
    if (
        not isinstance(result, dict)
        or set(result) != {"count", "programs", "op", "errmsg"}
        or type(result["op"]) is not int
        or result["op"] != 1
        or type(result["count"]) is not int
        or not 0 < result["count"] <= 5000
        or not isinstance(result["programs"], list)
        or len(result["programs"]) != result["count"]
    ):
        raise AdapterError("Incomplete or empty full catalogue", "parse")
    records = {}
    for row in result["programs"]:
        if (
            not isinstance(row, dict)
            or type(row.get("bourseId")) is not int
            or not 0 < row["bourseId"] < 10**12
            or row["bourseId"] in records
            or not isinstance(row.get("typeListId"), str)
            or not re.fullmatch(r"[1-6](?:,[1-6])*", row["typeListId"])
        ):
            raise AdapterError(
                "Invalid or duplicate native programme", "parse"
            )
        types = row["typeListId"].split(",")
        if len(types) != len(set(types)):
            raise AdapterError("Duplicate native support type", "parse")
        records[row["bourseId"]] = (
            clean(row.get("title")),
            tuple(sorted(types)),
        )
    return records


def collect():
    """Read two complete anonymous UI inventories, keeping minimal facts."""
    global _COLLECTION_END
    prepare_collection()
    _COLLECTION_END = time.monotonic() + COLLECTION_TIMEOUT
    home = fetch_inventory_page(fetch_source, SOURCE_URL)
    if 'src="' + SCRIPT_PATH + '"' not in home:
        raise AdapterError("Reviewed public application changed", "access")
    read_configuration(fetch_source(SCRIPT_URL))
    policy = PolicyText()
    policy.feed(fetch_source(POLICY_URL))
    policy_text = " ".join(" ".join(policy.parts).split())
    if (
        policy.sections != 1
        or hashlib.sha256(policy_text.encode()).hexdigest()
        != REVIEWED_POLICY_HASH
    ):
        raise AdapterError("Reviewed legal-page scope changed", "access")
    first = read_frontier()
    closing = read_frontier()
    if first != closing:
        raise AdapterError(
            "Closing full catalogue differs; no partial output", "parse"
        )
    records = []
    for identifier, (title, types) in sorted(first.items()):
        record = make_record(
            title,
            programme_url(identifier),
            (
                ["scholarships", "grants"]
                if "1" in types and "2" in types
                else ["grants"] if "2" in types else ["scholarships"]
            ),
            kind="programme-overview",
            tags=[TYPE_LABELS[value] for value in types],
            method="publisher-financial-support-catalogue",
            evidence=["CampusBourses financial-support programme"],
        )
        record.update(
            id=SOURCE_ID + "-" + str(identifier),
            summary_language="en",
            summary=(
                "CampusBourses lists this financial support programme. "
                "Check the original source for current application conditions "
                "and dates."
            ),
        )
        records.append(record)
    validate_records(records)
    print(
        json.dumps(
            {
                "source": SOURCE_ID,
                "records": len(records),
                "full_frontiers": 2,
                "scope": "minimal-programme-facts",
            }
        ),
        file=sys.stderr,
    )
    return records


def programme_url(identifier):
    """Use the native public client share link without copied prose."""
    return (
        "https://campusbourses.campusfrance.org/ws/cfgrant.php?id="
        + str(identifier)
        + "_en"
    )


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Write only this source to YouthOpps/data-source "
            "using DATA_SOURCE_TOKEN"
        ),
    )
    args = parser.parse_args()
    if args.publish:
        prepare_collection()
    snapshot = read_snapshot() if args.publish else None
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
        validate_records(old, published=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
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
                updated_at=(
                    before.get("updated_at", attempt) if same else attempt
                ),
                last_seen_at=attempt,
                last_checked_at=checked_at,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
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
    if args.publish and (
        not failure or snapshot["files"]["data.json"] is not None
    ):
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
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


def main():
    try:
        return run()
    finally:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            export_family_artifact()


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
                    "message": (
                        "Run could not complete; " "durable status unavailable"
                    ),
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
