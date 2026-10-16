"""Index minimal IKY programme facts through reviewed public HTML only."""

import argparse
import base64
import contextlib
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

SOURCE_ID = "gr-iky"
SOURCE_URL = "https://www.iky.gr/ypotrofies/"
WEBSITE_URL = "https://www.iky.gr/"
LANGUAGE = "el"
PUBLISHER_COUNTRY = "GR"
PUBLISHER_TYPE = "public-institution"
ATTRIBUTION = "Source: IKY / Greek State Scholarships Foundation"
_ALLOWED_HOSTS = {"www.iky.gr"}
_ROBOTS = {}
_PAGE_COMPLETED = None
_COLLECTION_END = None

_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-iky-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 900
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "iky-pacing-state"
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



_SCOPE_MIGRATION = "iky-public-html-api401-v1"
_LEGACY_CANONICAL_HASH = (
    '641e94aecb86781378bc26f15a14dc5f8afdcb5ee3e45969a6265c8320bf2183'
)
_LEGACY_RAW_HASH = (
    'ceb73f205ff048c9dcf6f7dccf1a6bbafe9d8e94f645ff40bff9e844f924e5ea'
)
_REFUSED_API_URL = (
    'https://www.iky.gr/wp-json/wp/v2/pages?slug=didaktorikes-ypotrofies-'
    'espa-2014-2020%2Cdidaktorikes-ypotrofies-espa-2021-2027%2Cdidaktorik'
    'es-ypotrofies-i-k-y-sto-evro%2Cdorea-dionysiou-angelopoulou%2Cefpath'
    'eis-koinonikes-omades-eko-espa-2021-2027%2Ceis-mnimin-marias-zaousi%'
    '2Cekoiky%2Cellino-galliki-synergasia%2Cellino-vretaniki-synergasia%2'
    'Cellinovretanikis%2Cenischysi-symmetochis-se-diethneis-diag%2Cerevna'
    '-epi-florentias%2Cfani-sarigianni%2Ciky-daad%2Ciky-evdap%2Ciky-fulbr'
    'ight%2Cklilodotima-nxrisovergi%2Cklirodotimata-theodorideion%2Ckliro'
    'dotimata-tsouridi-proptuxiaka%2Cklirodotimata-xatzopoulou-proptuxiak'
    'a%2Cprogramma-hubert-curien%2Cvraveia-diakrisis-se-diethneis-diagon%'
    '2Cypotrofies-espa-2021-2027%2Cypotrofies-gia-erevna-klirodotima-leon'
    'ida-nikolaidi%2Cypotrofies-iky-fulbright-greece%2Cypotrofies-stin-mn'
    'imi-tou-kathigiti-konstantinou-d-kerameos%2Cypotrofies-synergasias-m'
    'e-foreis-ellinikoy-politismoy%2C%CE%B2%CF%81%CE%B1%CE%B2%CE%B5%CE%AF'
    '%CE%B1-%CE%B1%CF%81%CE%B9%CF%83%CF%84%CE%B5%CE%AF%CE%B1%CF%82-%CE%B1'
    '%CF%80%CE%BF%CF%86%CE%BF%CE%AF%CF%84%CF%89%CE%BD-%CE%B1%CE%B5%CE%B9%'
    '2C%CE%B5%CF%81%CE%B5%CF%85%CE%BD%CE%B1-%CE%B5%CF%83%CF%80%CE%B1-2014'
    '-2020&per_page=100&orderby=id&order=asc&_fields=id%2Clink%2Ctitle%2C'
    'date_gmt%2Cslug&page=1'
)
_SCOPE_POLICY_HASH = (
    '8f51df44ea3ebbb4269f29f001bb8cdd209939b44ecda24bab92c6bc7e3ddb58'
)
_LEGACY_RAW_BASE64 = (
    'eyJzY2hlbWEiOjEsImZhbWlseSI6InlvdXRob3Bwcy1pa3ktcHVibGlzaGVyLXYxIiwi'
    'b2JzZXJ2ZWRfYXQiOjE3OTIwNzQ4OTEuNzYxMDU5OCwibm90X2JlZm9yZSI6MTc5MjA3'
    'NDIzNi4yNjI2MjMzLCJzdGFydHMiOlsxNzkyMDc0ODg2Ljc1OTIwNjNdLCJibG9ja2Vk'
    'Ijp0cnVlLCJpbnRlcnZhbCI6MTIuMiwicGFnZV9pbnRlcnZhbCI6MzAsInJhdGVfbGlt'
    'aXRfc3RyaWtlcyI6MCwicmF0ZV9saW1pdF91bnRpbCI6MCwiY29tcGxldGVkIjoxNzky'
    'MDc0ODkxLjc2MTA1OTh9'
)
_LEGACY_STATE = json.loads(base64.b64decode(_LEGACY_RAW_BASE64))
_HTML_PATHS = frozenset((
    '/metaptyxiakes-ypotrofies/ellinovretanikis/',
    '/robots.txt',
    '/ypotrofies/',
    (
        '/ypotrofies/%ce%b2%cf%81%ce%b1%ce%b2%ce%b5%ce%af%ce%b1-%ce%b1%cf'
        '%81%ce%b9%cf%83%cf%84%ce%b5%ce%af%ce%b1%cf%82-%ce%b1%cf%80%ce%bf'
        '%cf%86%ce%bf%ce%af%cf%84%cf%89%ce%bd-%ce%b1%ce%b5%ce%b9/'
    ),
    (
        '/ypotrofies/%ce%b5%cf%81%ce%b5%cf%85%ce%bd%ce%b1-%ce%b5%cf%83%cf'
        '%80%ce%b1-2014-2020/'
    ),
    '/ypotrofies/didaktorikes-ypotrofies-espa-2021-2027/',
    (
        '/ypotrofies/didaktorikes-ypotrofies/didaktorikes-ypotrofies-espa'
        '-2014-2020/'
    ),
    (
        '/ypotrofies/didaktorikes-ypotrofies/didaktorikes-ypotrofies-i-k-'
        'y-sto-evro/'
    ),
    '/ypotrofies/didaktorikes-ypotrofies/iky-fulbright/',
    '/ypotrofies/dorea-dionysiou-angelopoulou/',
    '/ypotrofies/efpatheis-koinonikes-omades-eko-espa-2021-2027/',
    '/ypotrofies/eis-mnimin-marias-zaousi/',
    '/ypotrofies/ekoiky/',
    '/ypotrofies/ellino-galliki-synergasia/',
    '/ypotrofies/ellino-vretaniki-synergasia/',
    '/ypotrofies/enischysi-symmetochis-se-diethneis-diag/',
    '/ypotrofies/erevna-epi-florentias/',
    '/ypotrofies/fani-sarigianni/',
    '/ypotrofies/iky-daad/',
    '/ypotrofies/iky-evdap/',
    '/ypotrofies/klilodotima-nxrisovergi/',
    '/ypotrofies/klirodotimata-theodorideion/',
    '/ypotrofies/klirodotimata-tsouridi-proptuxiaka/',
    '/ypotrofies/klirodotimata-xatzopoulou-proptuxiaka/',
    '/ypotrofies/programma-hubert-curien/',
    '/ypotrofies/vraveia-diakrisis-se-diethneis-diagon/',
    '/ypotrofies/ypotrofies-espa-2021-2027/',
    '/ypotrofies/ypotrofies-gia-erevna-klirodotima-leonida-nikolaidi/',
    '/ypotrofies/ypotrofies-iky-fulbright-greece/',
    '/ypotrofies/ypotrofies-stin-mnimi-tou-kathigiti-konstantinou-d-kerameos/',
    '/ypotrofies/ypotrofies-synergasias-me-foreis-ellinikoy-politismoy/',
))

# Proposed source-local additions; this fragment is inert, not a dispatcher.
# Requires independent runtime review and explicit root scope release.
_SCOPE_APPROVAL = (
    'https://github.com/YouthOpps/data-pipeline/issues/40#issuecomment-6195556550'
)


def scoped_refusal_valid(state):
    """Recognize only the reviewed API-only historical refusal annotation."""
    if _SCOPE_APPROVAL is None:
        return False
    expected = {
        "migration": _SCOPE_MIGRATION,
        "policy_sha256": _SCOPE_POLICY_HASH,
        "legacy_state_sha256": _LEGACY_CANONICAL_HASH,
        "legacy_state": _LEGACY_STATE,
        "legacy_raw_base64": _LEGACY_RAW_BASE64,
        "refused_url": _REFUSED_API_URL,
        "refused_status": 401,
        "review": _SCOPE_APPROVAL,
    }
    return (
        type(state.get("schema")) is int
        and state["schema"] == _STATE_SCHEMA
        and state.get("family") == FAMILY
        and state.get("blocked") is True
        and type(state.get("html_global_hold")) is bool
        and state.get("refusal_scope") == expected
    )


def annotate_reviewed_refusal(state):
    """Annotate the exact pinned old snapshot; never clear blocked=True."""
    if _SCOPE_APPROVAL is None:
        raise AdapterError("Reviewed HTML scope release required", "access")
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    if (
        hashlib.sha256(canonical.encode()).hexdigest()
        != _LEGACY_CANONICAL_HASH
        or state != _LEGACY_STATE
    ):
        raise AdapterError("Unknown or mixed historical refusal", "access")
    result = copy.deepcopy(state)
    result["html_global_hold"] = False
    result["refusal_scope"] = {
        "migration": _SCOPE_MIGRATION,
        "policy_sha256": _SCOPE_POLICY_HASH,
        "legacy_state_sha256": _LEGACY_CANONICAL_HASH,
        "legacy_state": copy.deepcopy(state),
        "legacy_raw_base64": _LEGACY_RAW_BASE64,
        "refused_url": _REFUSED_API_URL,
        "refused_status": 401,
        "review": _SCOPE_APPROVAL,
    }
    return result


def require_reviewed_html_route(url, *, method="GET", payload=None):
    """Reject every unreviewed URL before pacing, DNS, or transport."""
    try:
        parsed = urllib.parse.urlsplit(url)
        decoded = urllib.parse.unquote(parsed.path)
    except (ValueError, UnicodeError):
        raise AdapterError("Invalid public HTML URL", "access") from None
    if (
        method != "GET"
        or payload is not None
        or parsed.scheme != "https"
        or parsed.netloc != "www.iky.gr"
        or parsed.query
        or parsed.fragment
        or "\\" in url
        or any(ord(char) < 33 for char in url)
        or "%" in decoded
        or "\\" in decoded
        or "//" in decoded
        or any(part in {".", ".."} for part in decoded.split("/"))
        or decoded.lower().startswith("/wp-json")
        or parsed.path not in _HTML_PATHS
    ):
        raise AdapterError("Outside reviewed public HTML scope", "access")


def merge_refusal_scope(combined, local, incoming):
    """Retain typed API evidence and all unknown/global publisher holds."""
    # Mature incoming artifacts MUST have the exact typed provenance, even if
    # an incompatible artifact claims blocked=False. Fail rather than reset.
    if not scoped_refusal_valid(incoming):
        raise AdapterError("Incompatible incoming refusal scope", "access")
    if "refusal_scope" in local:
        if not scoped_refusal_valid(local):
            raise AdapterError("Incompatible local refusal scope", "access")
        local_global = local["html_global_hold"]
    else:
        # A genuinely empty local runner has no hold. An unknown/mixed old
        # blocked state cannot inherit the API-only scope of an artifact.
        local_global = local["blocked"]
    combined["blocked"] = True
    combined["html_global_hold"] = (
        incoming["html_global_hold"] or local_global
    )
    combined["refusal_scope"] = copy.deepcopy(incoming["refusal_scope"])
    return combined


def pace(url, interval=12.2):
    """Serialize source requests, retaining every stronger restored limit."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    global _PAGE_INTERVAL
    state = locked_budget()
    try:
        budget = load_budget()
        if (
            not scoped_refusal_valid(budget)
            or budget["html_global_hold"]
        ):
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
    *,
    interval=12.2,
    method="GET",
    headers=None,
    payload=None,
    byte_limit=5_000_000,
    robots=False,
):
    global _PUBLISHER_GATE_FAILED
    publisher = urllib.parse.urlsplit(url).hostname in _ALLOWED_HOSTS
    if publisher:
        require_reviewed_html_route(url, method=method, payload=payload)
        if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
            raise AdapterError("Robots purpose requires robots.txt", "access")
    state, budget = pace(url, interval) if publisher else (None, None)
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={**(headers or {}), "User-Agent": _USER_AGENT},
        )
        try:
            if publisher:
                journal_preopener()
            response = _OPENER.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with (
            header_rate_limit_response(response)
            if publisher and response.status == 429
            else response
        ):
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
                if response.status in {401, 403, 451} and not robots:
                    budget["blocked"] = True
                    budget["html_global_hold"] = True
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
            if publisher and (
                response.status == 429
                or response.status in {401, 403, 451}
                and not robots
            ):
                budget["observed_at"] = time.time()
                write_budget(budget)
            if publisher and response.status == 429:
                raise HeaderRateLimitStop(
                    "Publisher stopped: HTTP 429", "access", 429
                )
            body = response.read(byte_limit + 1)
            if len(body) > byte_limit:
                raise AdapterError(
                    "Response exceeds bounded native limit", "fetch"
                )
            return response.status, response.headers, body
    finally:
        if state is not None:
            _PUBLISHER_GATE_FAILED = True
            try:
                budget["completed"] = budget["observed_at"] = time.time()
                write_budget(budget)
                _PUBLISHER_GATE_FAILED = False
            finally:
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
        programme_paths = _HTML_PATHS - {"/robots.txt", "/ypotrofies/"}
        if not native or url.path not in programme_paths:
            raise AdapterError(
                "Native programme identity/link mismatch", "validate"
            )
        require_reviewed_html_route(record["url"])
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
        "source": SOURCE_ID,
        "name": "IKY / Greek State Scholarships Foundation",
        "display_name": "IKY",
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
                "IKY programme-overview records"
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
    base_keys = {
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
    }
    if not isinstance(state, dict) or set(state) not in (
        base_keys, base_keys | {"refusal_scope", "html_global_hold"}
    ):
        raise AdapterError("Invalid publisher pacing state", "access")
    if "refusal_scope" in state and not scoped_refusal_valid(state):
        raise AdapterError("Unrecognised refusal annotation", "access")
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


def write_budget(state):
    validate_budget(state)
    journal_commit(state)


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
        raw = os.environ.get("IKY_PACING_BOOTSTRAP", "")
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
            raise AdapterError("Incomplete family artifact inventory", "access")
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
    with journal_restore_scope(incoming), locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined[("not_before")] = max(
            local[("not_before")], incoming[("not_before")], now + 60
        )
        merge_refusal_scope(combined, local, incoming)
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
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("IKY_PACING_ARTIFACT_PATH")
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
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "state": state,
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
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    global _GUARD_COLLECTION_ID, _GUARD_NEW_DIRECTORY
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    _COLLECTION_STARTED = time.time()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(os.path.dirname(_STATE_PATH), "collection.lock")
        parent = journal_parent()
        descriptor = acquired_id = None
        try:
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                descriptor = os.open("collection.lock", flags, dir_fd=parent)
            except FileNotFoundError:
                if not _GUARD_NEW_DIRECTORY:
                    raise AdapterError(
                        "Mature collection lock missing", "access"
                    )
                descriptor = os.open(
                    "collection.lock",
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent,
                )
            acquired = os.fstat(descriptor)
            acquired_id = (acquired.st_dev, acquired.st_ino)
            journal_owned(descriptor, acquired_id)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal_owned(descriptor, acquired_id)
            current = os.stat(
                "collection.lock", dir_fd=parent, follow_symlinks=False
            )
            if acquired_id != (current.st_dev, current.st_ino):
                raise AdapterError(
                    "Publisher collection lock changed", "access"
                )
        except BaseException:
            journal_failed()
            if descriptor is not None and acquired_id is not None:
                journal_close(descriptor, acquired_id, True)
            raise
        _GUARD_COLLECTION_ID = acquired_id
        _COLLECTION_LOCK_FD = descriptor
        _GUARD_NEW_DIRECTORY = False
    if os.environ.get("GITHUB_ACTIONS") != "true":
        with locked_budget():
            state = load_budget()
            if state["blocked"] and "refusal_scope" not in state:
                # Hold remains untouched while approval is absent. Do not
                # annotate unknown/mixed state or reconstruct old raw bytes.
                if _SCOPE_APPROVAL is None:
                    raise AdapterError("Reviewed HTML scope required", "access")
                raw = journal_read()
                if hashlib.sha256(raw).hexdigest() != _LEGACY_RAW_HASH:
                    raise AdapterError("Historical raw state changed", "access")
                state = annotate_reviewed_refusal(state)
                state["not_before"] = max(state["not_before"], time.time() + 60)
                write_budget(state)
            elif not scoped_refusal_valid(state):
                raise AdapterError("Reviewed typed state required", "access")
    with locked_budget():
        load_budget()
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


def reject_visible_challenge(body, headers, *, challenge_only=False):
    if challenge_only and has_429_head_refusal(body, headers):
        with locked_budget():
            state = load_budget()
            state["blocked"] = True
            state["html_global_hold"] = True
            state["observed_at"] = time.time()
            write_budget(state)
        raise AdapterError("Visible publisher challenge or denial", "access")

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
            state["html_global_hold"] = True
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
        require_reviewed_html_route(current, method=method, payload=payload)
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


class HtmlNode:
    """Retain a bounded response tree in RAM, never a catalogue file."""

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.parts = []

    def nodes(self, tag=None):
        for part in self.parts:
            if isinstance(part, HtmlNode):
                if tag is None or part.tag == tag:
                    yield part
                yield from part.nodes(tag)

    def text(self):
        return " ".join(
            part if isinstance(part, str) else part.text()
            for part in self.parts
            if isinstance(part, str) or part.tag not in {"script", "style"}
        )


class HtmlTree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = HtmlNode(tag, attrs)
        self.stack[-1].parts.append(node)
        if tag not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }:
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
        self.stack[-1].parts.append(data)


def clean(value):
    return " ".join(value.split())


def parse_catalogue(body):
    """Read the eight financial-programme branches, excluding publicity."""
    parser = HtmlTree()
    parser.feed(body)
    menus = [
        node for node in parser.root.nodes("nav")
        if "ypot-mn" in node.attrs.get("class", "").split()
    ]
    if len(menus) != 1:
        raise AdapterError("Expected unique programme catalogue", "parse")
    lists = [
        part for part in menus[0].parts
        if isinstance(part, HtmlNode) and part.tag == "ul"
    ]
    if len(lists) != 1:
        raise AdapterError("Programme menu structure changed", "parse")
    groups = [
        part for part in lists[0].parts
        if isinstance(part, HtmlNode) and part.tag == "li"
        and "w-nav-close" not in part.attrs.get("class", "").split()
    ]
    expected = (
        "Προπτυχιακές", "Μεταπτυχιακές", "Διδακτορικές", "Για έρευνα",
        "Προγράμ. ακαδημ. συνεργασίας", "Κληροδοτήματα",
        "Ειδικά προγ. βραβείων & ενισχύσεων",
        "Υποτροφίες Αλλοδαπών Υπηκόων", "Προβολή/Δημοσιότητα",
    )
    if len(groups) != len(expected):
        raise AdapterError("Programme branches changed", "parse")
    inventory = {}
    for index, group in enumerate(groups):
        anchors = [
            part for part in group.parts
            if isinstance(part, HtmlNode) and part.tag == "a"
        ]
        if len(anchors) != 1 or clean(anchors[0].text()) != expected[index]:
            raise AdapterError("Programme branch identity changed", "parse")
        if index == 8:
            continue
        category = "grants" if index in {4, 6} else "scholarships"
        for item in group.nodes("li"):
            if "menu-item-has-children" in item.attrs.get("class", "").split():
                continue
            links = list(item.nodes("a"))
            if len(links) != 1:
                raise AdapterError("Invalid financial programme leaf", "parse")
            link = links[0]
            url = urllib.parse.urljoin(SOURCE_URL, link.attrs.get("href", ""))
            require_reviewed_html_route(url)
            if url == SOURCE_URL or not clean(link.text()):
                raise AdapterError("Invalid programme identity", "parse")
            titles, branches, categories = inventory.get(url, ((), (), ()))
            inventory[url] = (
                tuple(sorted(set(titles) | {clean(link.text())})),
                tuple(sorted(set(branches) | {expected[index]})),
                tuple(sorted(set(categories) | {category})),
            )
    if not inventory:
        raise AdapterError("Empty financial programme catalogue", "parse")
    # No new routes are guessed/approved from a changed page. Explicitly hold
    # on drift before the first new programme URL is requested.
    expected_paths = _HTML_PATHS - {"/robots.txt", "/ypotrofies/"}
    paths = {urllib.parse.urlsplit(url).path for url in inventory}
    if paths != expected_paths:
        raise AdapterError("Reviewed HTML inventory changed", "access")
    return inventory


def parse_programme(body, url, groups, categories):
    """Use native ID/title/canonical/date; no descriptions or images."""
    parser = HtmlTree()
    parser.feed(body)
    headings = [clean(node.text()) for node in parser.root.nodes("h1")]
    bodies = list(parser.root.nodes("body"))
    canonical = [
        node.attrs.get("href") for node in parser.root.nodes("link")
        if node.attrs.get("rel", "").lower() == "canonical"
    ]
    if len(headings) != 1 or not headings[0] or len(bodies) != 1:
        raise AdapterError("Expected unique native programme heading", "parse")
    ids = re.findall(
        r"\bpage-id-([1-9][0-9]*)\b", bodies[0].attrs.get("class", "")
    )
    if len(ids) != 1 or canonical != [url]:
        raise AdapterError("Native programme ID/canonical mismatch", "parse")
    dates = set()
    for node in parser.root.nodes("script"):
        if node.attrs.get("type") != "application/ld+json":
            continue
        raw = "".join(part for part in node.parts if isinstance(part, str))
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise AdapterError(
                "Invalid native structured facts", "parse"
            ) from None
        graph = data.get("@graph", []) if isinstance(data, dict) else []
        for item in graph:
            if not isinstance(item, dict):
                raise AdapterError("Invalid native structured node", "parse")
            if item.get("@type") == "WebPage" and item.get("url") == url:
                value = item.get("datePublished")
                if value is not None:
                    try:
                        date = datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        )
                        if date.tzinfo is None:
                            raise ValueError("Unknown publication timezone")
                        dates.add(
                            date.astimezone(timezone.utc).isoformat()
                            .replace("+00:00", "Z")
                        )
                    except (ValueError, TypeError, AttributeError):
                        raise AdapterError(
                            "Invalid native publication date", "parse"
                        ) from None
    if len(dates) > 1:
        raise AdapterError("Conflicting native publication dates", "parse")
    record = make_record(
        headings[0], url, list(categories), kind="programme-overview",
        published_at=next(iter(dates), None), tags=list(groups),
        method="publisher-financial-programme-menu",
        evidence=["IKY financial programme menu: " + group for group in groups],
    )
    record.update(
        id=SOURCE_ID + "-" + ids[0], summary_language="en",
        summary=(
            "IKY lists this financial support or award programme. "
            "Check the original source for current application conditions "
            "and dates."
        ),
    )
    return record


def collect():
    """Read every approved programme and reconcile the complete catalogue."""
    global _COLLECTION_END
    prepare_collection()
    _COLLECTION_END = time.monotonic() + COLLECTION_TIMEOUT
    first = parse_catalogue(fetch_inventory_page(fetch_source, SOURCE_URL))
    records = []
    for url, (_, groups, categories) in sorted(first.items()):
        records.append(
            parse_programme(fetch_source(url), url, groups, categories)
        )
    closing = parse_catalogue(fetch_inventory_page(fetch_source, SOURCE_URL))
    if first != closing:
        raise AdapterError("Closing complete catalogue differs", "parse")
    validate_records(records)
    print(json.dumps({
        "source": SOURCE_ID, "records": len(records),
        "complete_catalogue_frontiers": 2, "scope": "minimal-programme-facts",
    }), file=sys.stderr)
    return records


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
        descriptor = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    identity = None
    try:
        held = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (held.st_dev, held.st_ino)
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
    if not _GUARD_DIRTY and journal_generation(info) != _GUARD_VERSION:
        return
    os.ftruncate(_GUARD_FD, 0)
    os.fsync(_GUARD_FD)


def journal_preopener():
    """Final publisher authority check, after request construction."""
    try:
        journal_verify()
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
    if _GUARD_FATAL:
        with contextlib.suppress(BaseException):
            export_family_artifact()
    if errors and primary is None:
        raise errors[0]


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
            journal_failed()
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
