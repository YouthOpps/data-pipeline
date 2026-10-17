"""Collect factual metadata from the reviewed publisher programme page."""

import argparse
import base64
from collections import deque
import contextlib
import copy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html import unescape
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import random
import re
import signal
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile


SOURCE_ID = "de-fulbright-germany"
SOURCE_URL = "https://www.fulbright.de/stipendien/programm/studienstipendium-uni-und-haw"
WEBSITE_URL = "https://www.fulbright.de/stipendien/programm/studienstipendium-uni-und-haw"
LANGUAGE = "de/en"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "unknown"
ATTRIBUTION = None

class ProgrammeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonicals = []
        self.headings = []
        self.structured = []
        self.heading = None
        self.script = None
        self.ignore = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "link" and "canonical" in (attributes.get("rel") or "").lower().split():
            self.canonicals.append(attributes.get("href"))
        if tag in ("script", "style"):
            self.ignore += 1
        if tag == "script" and (attributes.get("type") or "").lower() == "application/ld+json":
            self.script = []
        if tag == "h1" and not self.ignore:
            if self.heading is not None:
                raise ValueError("Malformed programme heading")
            self.heading = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.heading is not None and not self.ignore:
            self.heading.append(data)

    def handle_endtag(self, tag):
        if tag == "h1" and self.heading is not None:
            self.headings.append(" ".join(unescape("".join(self.heading)).split()))
            self.heading = None
        if tag == "script" and self.script is not None:
            self.structured.append("".join(self.script))
            self.script = None
        if tag in ("script", "style"):
            self.ignore = max(0, self.ignore - 1)

EXPECTED_TITLE = "Studienstipendium (Uni und HAW)"

def collect():
    delay = check_robots(SOURCE_URL)
    page = ProgrammeHTML()
    page.feed(fetch_source(SOURCE_URL, min_interval=delay))
    page.close()
    if len(page.canonicals) != 1 or (page.canonicals[0] or "").replace("https://www.", "https://") != SOURCE_URL.replace("https://www.", "https://"):
        raise ValueError("Reviewed programme canonical URL changed or is ambiguous")
    if page.heading is not None or page.headings != [EXPECTED_TITLE]:
        raise ValueError("Expected one exact reviewed programme heading")
    # A calendar date or closed application message does not establish publication or a deadline.
    return [make_record(EXPECTED_TITLE, SOURCE_URL, ["scholarships"],
                        kind="programme-overview", tags=["programme-overview"],
                        method="editorial-review", evidence=[SOURCE_URL])]

# All transport, output and publication code below belongs to this adapter.
# Python standard library only; no imports from another adapter or project module.

CATEGORIES = ('scholarships', 'internships', 'volunteering', 'fellowships', 'training', 'competitions', 'grants', 'jobs', 'other')
_GITHUB = 'https://api.github.com/repos/YouthOpps/data-source'
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
_BUDGETS = {}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-de-fulbright-germany-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_LOCAL_WORKFLOW_ID = 378390061
_LOCAL_START_VERIFIED = False
_STATE_SCHEMA = 3
_NEW_STATE_DIRECTORY = False
_STATE_DIRECTORY_IDENTITY = None
_ARTIFACT_NAME = "de-fulbright-germany-pacing-state"
_STATE_PATH = _STATE_LOCK = _COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = _PHASE_END = _PAGE_COMPLETED = None
_PUBLISHER_COMPLETED_MONOTONIC = None
_PUBLISHER_INTERVAL = 12.2
_PUBLISHER_GATE_FAILED = False
_PERSISTENCE_FAILED = False
_RESPONSE_STATE_READY = False
_RESTORED = _STATE_TRUSTED = False
_RESTRICTIVE_STATE = None
_ATTEMPT_HEADS = {}
_OBSERVED_ATTEMPTS = {}
COLLECTION_TIMEOUT = 1500
_LOCAL_TRANSITION = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/270#issuecomment-6202125047',
 'history_sha256': 'c5c68e8fcfd4666100bc90f163fcbcddb5670b44894bcc8e6948024bf2c5f85c',
 'state': {'schema': 3,
           'family': 'youthopps-de-fulbright-germany-publisher-v1',
           'observed_at': 1792079673.0,
           'not_before': 1792079733.0,
           'starts': [1791953662.0,
                      1791969342.0,
                      1791978753.0,
                      1791997874.0,
                      1792014099.0,
                      1792039154.0,
                      1792042251.0,
                      1792060529.0,
                      1792061866.0,
                      1792079673.0],
           'blocked': False,
           'interval': 12.2,
           'page_interval': 30,
           'rate_failures': 0,
           'legacy_rate_history_unknown': True,
           'in_flight': False}}
_PRODUCTION_TRANSITION = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/270#issuecomment-6202857572',
 'history_sha256': 'c5c68e8fcfd4666100bc90f163fcbcddb5670b44894bcc8e6948024bf2c5f85c',
 'state': {'schema': 3,
           'family': 'youthopps-de-fulbright-germany-publisher-v1',
           'observed_at': 1792099800.3336883,
           'not_before': 1792099860.452569,
           'starts': [1792061866,
                      1792079673,
                      1792099785.7167075,
                      1792099786.0310576,
                      1792099786.2013786,
                      1792099786.4577825,
                      1792099798.6589155,
                      1792099798.6597083,
                      1792099798.742686,
                      1792099800.3332999],
           'blocked': False,
           'in_flight': False,
           'interval': 12.2,
           'page_interval': 30,
           'legacy_rate_history_unknown': True,
           'rate_failures': 0}}
_LEGACY_HISTORY_SHA256 = (
    "c5c68e8fcfd4666100bc90f163fcbcddb5670b44894bcc8e6948024bf2c5f85c"
)
_LEGACY_ATTEMPTS = (
    (37774794678, 1, "615c2b80001b97654b7229d16dde5fc32c98ddd8", 1791461341.0),
    (37827559469, 1, "6429b6913fd6bb208ccabb79105336c7e31e16b7", 1791485561.0),
    (37891986092, 1, "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb", 1791526150.0),
    (37935808036, 1, "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa", 1791551907.0),
    (38000285025, 1, "2d6491ddf2a37047efc7b996f190f648e647c0c6", 1791585542.0),
    (38028999592, 1, "6e5922277de22b96874c23a705a7d838512c6caf", 1791611614.0),
    (38052281392, 1, "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488", 1791635547.0),
    (38088741724, 1, "b41ccae8f603bee61d1a9825b8724848941f54c8", 1791668682.0),
    (38115874627, 1, "10d99f8ef021c092851bf189ff67307865b8760c", 1791697637.0),
    (38140751080, 1, "10d99f8ef021c092851bf189ff67307865b8760c", 1791723027.0),
    (38178502328, 1, "10d99f8ef021c092851bf189ff67307865b8760c", 1791755925.0),
    (38210262340, 1, "5dff04768f0e558b4d301d5677c5a2c433c3a64b", 1791784717.0),
    (38270064760, 1, "5dff04768f0e558b4d301d5677c5a2c433c3a64b", 1791818746.0),
    (38328326568, 1, "5dff04768f0e558b4d301d5677c5a2c433c3a64b", 1791852016.0),
    (38411226119, 1, "5dff04768f0e558b4d301d5677c5a2c433c3a64b", 1791900738.0),
    (38479819871, 1, "5dff04768f0e558b4d301d5677c5a2c433c3a64b", 1791931536.0),
    (38513743389, 1, "02792bda730b08eeb1eda86a2cd71af80af3cef7", 1791953662.0),
    (38536227316, 1, "92235b48ad33b9fa377e0736b689010173d7bfab", 1791969342.0),
    (38559574328, 1, "92235b48ad33b9fa377e0736b689010173d7bfab", 1791978753.0),
    (38604372858, 1, "92235b48ad33b9fa377e0736b689010173d7bfab", 1791997874.0),
    (38642105797, 1, "a3a41a05d4965d11b560f64271c5a6e3af1467be", 1792014099.0),
    (38682520560, 1, "e1a68725fe923582b5640481e29c7166eb77397c", 1792039154.0),
    (38684236027, 1, "9b97e13c2e2a56ee77700e40202eb6acd2212cf5", 1792042251.0),
    (38721259466, 1, "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f", 1792060529.0),
    (38720083784, 1, "a62a88f5cad62f4fb1ef5230358557d37d2b2bba", 1792061866.0),
    (38763419148, 1, "f77eed642c4544bbeed009440c406064a4f5c6ad", 1792079673.0),
)



class AdapterError(Exception):
    def __init__(self, message, stage='parse', status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def safe_error(error):
    text = str(error)
    text = re.sub(r'https?://[^\s<>"\']+', lambda match: urllib.parse.urlunsplit((urllib.parse.urlsplit(match[0]).scheme, urllib.parse.urlsplit(match[0]).hostname or '', urllib.parse.urlsplit(match[0]).path, '', '')), text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+\S+', '[redacted]', text)
    text = re.sub(r'(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+', r'\1=[redacted]', text)
    return ' '.join(text.split())[:500]


def pace(url, interval=6, *, start_padding=0):
    if start_padding:
        padding_until = None
    host = (urllib.parse.urlsplit(url).hostname or '').lower().removeprefix('www.')
    budget = _BUDGETS.setdefault(host, {'starts': deque(), 'interval': 6, 'until': 0})
    budget['interval'] = max(budget['interval'], interval, 6)
    while True:
        now = time.monotonic()
        while budget['starts'] and now - budget['starts'][0] >= 60:
            budget['starts'].popleft()
        ready = max(budget['until'], budget['starts'][-1] + budget['interval'] if budget['starts'] else now,
                    budget['starts'][0] + 60 if len(budget['starts']) >= 10 else now)
        if start_padding and ready <= now:
            if padding_until is None:
                padding_until = now + start_padding
            ready = max(ready, padding_until)
        if ready <= now:
            budget['starts'].append(now)
            return budget
        time.sleep(ready - now)


def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    deadline=None,
    retry_jitter=0,
    publisher=False,
    robots=False,
    inventory=False,
    byte_limit=5_000_000,
):
    if publisher:
        if method != "GET" or payload is not None or headers:
            raise AdapterError("Invalid source request arguments", "access")
        return publisher_request_bytes(
            url, interval, robots=robots, inventory=inventory, deadline=deadline
        )
    if type(byte_limit) is not int or not 0 < byte_limit <= 5_000_000:
        raise AdapterError("Invalid transport byte bound", "fetch")
    if retry_jitter:
        budget = pace(url, interval, start_padding=retry_jitter)
    else:
        budget = pace(url, interval)
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={"User-Agent": _USER_AGENT, **(headers or {})},
    )
    try:
        transport_timeout = 30
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdapterError("Publication deadline exhausted", "publish")
            transport_timeout = min(30, remaining)
        response = _OPENER.open(request, timeout=transport_timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        retry = response.headers.get("Retry-After")
        if retry:
            try:
                seconds = (
                    float(retry)
                    if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                    else parsedate_to_datetime(retry).timestamp() - time.time()
                )
                budget["until"] = max(
                    budget["until"], time.monotonic() + max(0, seconds)
                )
            except (ValueError, TypeError, OverflowError):
                pass
        body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise AdapterError("Response exceeds 5 MB", "fetch")
        return response.status, response.headers, body


def fetch_source(url, min_interval=6, *, robots=False, inventory=False):
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

    for attempt in range(2):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(
                    current,
                    interval=min_interval,
                    publisher=True,
                    robots=robots,
                    inventory=inventory,
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(current, headers["Location"])
                    continue
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status}", "fetch", status
                    )
                return body.decode(headers.get_content_charset() or "utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error), "fetch"
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    prepare_collection()
    parsed = urllib.parse.urlsplit(url)
    try:
        text = fetch_source(
            urllib.parse.urlunsplit(
                (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
            ),
            robots=True,
        )
    except AdapterError as error:
        if (
            error.status is not None
            and 400 <= error.status < 500
            and error.status != 429
        ):
            return 6
        raise
    delay = assess_robots_policy(url, text)
    with locked_budget():
        state = load_budget()
        state["interval"] = max(state["interval"], delay, 12.2)
        state["page_interval"] = max(state["page_interval"], delay, 30)
        state["not_before"] = max(
            state["not_before"], time.time() + state["interval"]
        )
        save_budget(state)
        state["in_flight"] = False
        save_budget(state)
    return delay


def make_record(title, url, categories, kind='opportunity', published_at=None, tags=None, host_countries=None, method='editorial-review', evidence=None):
    now = utc_now()
    return {'id': hashlib.sha256(f'{SOURCE_ID}|{url}'.encode()).hexdigest()[:24], 'title': title,
            'url': url, 'source': SOURCE_ID, 'source_url': SOURCE_URL, 'published_at': published_at,
            'summary': '', 'tags': tags or [], 'location': None, 'deadline': None, 'language': LANGUAGE,
            'category': categories[0], 'categories': categories, 'kind': kind, 'host_countries': host_countries or [],
            'eligible_countries': [], 'publisher_country': PUBLISHER_COUNTRY,
            'created_at': now, 'updated_at': now, 'first_seen_at': now, 'last_seen_at': now, 'last_checked_at': now,
            'status': 'unknown', 'classification': {'method': method, 'status': 'unknown' if categories == ['other'] else 'classified', 'evidence': evidence or []}}


def validate_records(records, *, allow_empty=False):
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError('Empty or invalid opportunity collection', 'validate')
    ids = set()
    for record in records:
        if not isinstance(record, dict) or record.get('source') != SOURCE_ID or record.get('source_url') != SOURCE_URL:
            raise AdapterError('Record source identity mismatch', 'validate')
        for field in ('id', 'title', 'url', 'created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at'):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise AdapterError('Missing record field: ' + field, 'validate')
        if record['id'] in ids:
            raise AdapterError('Duplicate opportunity identifier', 'validate')
        ids.add(record['id'])
        url = urllib.parse.urlsplit(record['url'])
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise AdapterError('Unsafe opportunity URL', 'validate')
        for field in ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at', 'published_at', 'deadline'):
            value = record.get(field)
            if field not in record:
                raise AdapterError('Missing record field: ' + field, 'validate')
            if value is not None:
                try:
                    if datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is None:
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError('Invalid record timestamp: ' + field, 'validate')
        for field in ('tags', 'host_countries', 'eligible_countries'):
            values = record.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise AdapterError('Invalid record list: ' + field, 'validate')
            if field != 'tags' and any(not re.fullmatch('[A-Z]{2}', value) for value in values):
                raise AdapterError('Invalid country code', 'validate')
        categories = record.get('categories')
        if not isinstance(categories, list) or not categories or any(value not in CATEGORIES for value in categories) or len(set(categories)) != len(categories) or record.get('category') != categories[0]:
            raise AdapterError('Invalid opportunity categories', 'validate')
        if record.get('kind') not in ('opportunity', 'programme-overview', 'institutional-grant', 'unknown') or record.get('status') not in ('open', 'expired', 'unknown'):
            raise AdapterError('Invalid opportunity kind or status', 'validate')
        if not isinstance(record.get('summary'), str) or len(record['summary']) > 600 or re.search('<[^>]+>', record['summary']):
            raise AdapterError('Invalid plain summary', 'validate')
        for field in ('location', 'language', 'publisher_country'):
            if field not in record or record[field] is not None and not isinstance(record[field], str):
                raise AdapterError('Invalid record field: ' + field, 'validate')
        classification = record.get('classification')
        if not isinstance(classification, dict) or not isinstance(classification.get('method'), str) or classification.get('status') not in ('classified', 'unknown') or not isinstance(classification.get('evidence'), list) or any(not isinstance(value, str) for value in classification['evidence']):
            raise AdapterError('Invalid classification evidence', 'validate')


_PUBLICATION_WINDOW = None
_PUBLICATION_IDENTITY = None
_PUBLICATION_FAILURE = None
_PUBLICATION_RETRY_JITTER = 0


@contextlib.contextmanager
def publication_http_guard(deadline):
    """Bound one reviewed Unix/Python call, including waits and body reads."""
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "ITIMER_REAL")
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "getitimer")
        or not hasattr(signal, "SIGALRM")
    ):
        raise AdapterError("Publication timer is unavailable", "publish")
    active, repeating = signal.getitimer(signal.ITIMER_REAL)
    if active or repeating:
        raise AdapterError("An existing timer prevents publication", "publish")
    end = min(deadline, time.monotonic() + 30)
    remaining = end - time.monotonic()
    if remaining <= 0:
        raise AdapterError(
            "Publication deadline exhausted", "publish"
        )
    previous = signal.getsignal(signal.SIGALRM)

    def exhausted(signum, frame):
        raise AdapterError("Publication call deadline exhausted", "publish")

    try:
        signal.signal(signal.SIGALRM, exhausted)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield end
        if time.monotonic() >= end:
            raise AdapterError("Publication call deadline exhausted", "publish")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def prove_publication_window():
    """Prove and freeze the exact current hosted job's remaining time."""
    global _PUBLICATION_WINDOW, _PUBLICATION_IDENTITY
    run = os.environ.get("GITHUB_RUN_ID", "")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")
    head = os.environ.get("GITHUB_SHA", "")
    workflow = (
        "YouthOpps/data-pipeline/.github/workflows/fetch-"
        + SOURCE_ID
        + ".yml@refs/heads/main"
    )
    identity = (run, attempt, head, workflow)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_JOB") != "collect"
        or os.environ.get("GITHUB_WORKFLOW_REF") != workflow
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(r"[1-9][0-9]{0,19}", run)
        or not re.fullmatch(r"[1-9][0-9]{0,9}", attempt)
        or not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise AdapterError(
            "Exact hosted publication context required", "publish"
        )
    if _PUBLICATION_WINDOW is not None:
        if identity != _PUBLICATION_IDENTITY:
            raise AdapterError("Publication job identity changed", "publish")
        return _PUBLICATION_WINDOW
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AdapterError(
            "Job deadline read credential is required", "publish"
        )
    url = (
        "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/"
        + run
        + "/attempts/"
        + attempt
        + "/jobs?per_page=100&deadline_observation="
        + str(time.time_ns())
    )
    wall_anchor = time.time()
    anchor = time.monotonic()
    with publication_http_guard(anchor + 30) as call_end:
        status, headers, body = request_bytes(
            url,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
            },
            deadline=call_end,
        )
    if status != 200:
        raise AdapterError(
            "GitHub job deadline context returned HTTP " + str(status),
            "publish",
            status,
        )

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous job context")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Invalid job context constant")

    try:
        dates = headers.get_all("Date", [])
        ages = headers.get_all("Age", [])
        if len(dates) != 1 or ages not in ([], ["0"]):
            raise ValueError("Untrusted job context time")
        if not re.fullmatch(
            r"[A-Za-z]{3}, [0-9]{2} [A-Za-z]{3} [0-9]{4} "
            r"[0-9]{2}:[0-9]{2}:[0-9]{2} GMT",
            dates[0],
        ):
            raise ValueError("Ambiguous HTTP Date")
        server = parsedate_to_datetime(dates[0])
        if server.tzinfo is None or server.utcoffset().total_seconds() != 0:
            raise ValueError("Expected UTC server time")
        if (
            not wall_anchor - 2
            <= server.timestamp()
            <= (wall_anchor + time.monotonic() - anchor + 2)
        ):
            raise ValueError("Stale or inconsistent server time")
        if len(body) > 65536:
            raise ValueError("Oversized job context")
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        if (
            not isinstance(decoded, dict)
            or type(decoded.get("total_count")) is not int
            or decoded["total_count"] != 1
            or not isinstance(decoded.get("jobs"), list)
            or len(decoded["jobs"]) != 1
        ):
            raise ValueError("Incomplete or ambiguous current jobs")
        job = decoded["jobs"][0]
        if (
            not isinstance(job, dict)
            or type(job.get("id")) is not int
            or job["id"] <= 0
            or job.get("name") != "collect"
            or job.get("workflow_name") != "fetch-" + SOURCE_ID
            or type(job.get("run_id")) is not int
            or job["run_id"] != int(run)
            or type(job.get("run_attempt")) is not int
            or job["run_attempt"] != int(attempt)
            or job.get("head_sha") != head
            or job.get("head_branch") != "main"
            or job.get("status") != "in_progress"
            or "conclusion" not in job
            or job["conclusion"] is not None
            or type(job.get("runner_id")) is not int
            or job["runner_id"] <= 0
            or not isinstance(job.get("started_at"), str)
        ):
            raise ValueError("Current running job identity mismatch")
        clock_type = getattr(datetime, "datetime", datetime)
        started = clock_type.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.tzinfo is None or started.utcoffset().total_seconds() != 0:
            raise ValueError("Expected UTC job start")
        elapsed = server.timestamp() - started.timestamp()
        if not 0 <= elapsed < 1800:
            raise ValueError("Job time is inconsistent or exhausted")
        end = anchor + 1800 - elapsed - 2
        if time.monotonic() >= end - 60:
            raise ValueError("No safe publication write window remains")
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RecursionError,
        OverflowError,
        OSError,
    ):
        raise AdapterError(
            "Unusable exact job deadline proof", "publish"
        ) from None
    _PUBLICATION_IDENTITY = identity
    _PUBLICATION_WINDOW = {"write": end - 60, "read": end - 30, "job": end}
    return _PUBLICATION_WINDOW


def publication_window():
    """Reuse one proof or keep its first failure closed without another read."""
    if _PUBLICATION_WINDOW is None and _PUBLICATION_FAILURE is not None:
        raise _PUBLICATION_FAILURE
    try:
        return prove_publication_window()
    except AdapterError as error:
        stop_publication(error)
        raise


def wait_for_publication_retry(attempt):
    """Queue padding after the next write reaches its ordinary pacing gate."""
    global _PUBLICATION_RETRY_JITTER
    if attempt not in (0, 1):
        raise AdapterError("Publication retry limit reached", "publish")
    delay = random.uniform(1, 2) if attempt == 0 else random.uniform(2, 4)
    deadline = publication_window()["write"]
    if deadline - time.monotonic() <= delay:
        raise AdapterError("No publication retry headroom remains", "publish")
    _PUBLICATION_RETRY_JITTER = delay


def stop_publication(error):
    """Keep a conclusive failure from starting another write phase."""
    global _PUBLICATION_FAILURE, _PUBLICATION_RETRY_JITTER
    _PUBLICATION_FAILURE = error
    _PUBLICATION_RETRY_JITTER = 0
    return error


def github_error_context(method, path):
    """Describe only known API routes, without credentials or URL queries."""
    verb = method if method in {"GET", "POST", "PATCH"} else "REQUEST"
    try:
        route = urllib.parse.urlsplit(path).path
    except (TypeError, ValueError):
        route = ""
    if not re.fullmatch(
        r"/(?:git/(?:trees|commits(?:/[0-9a-f]{40})?|ref/heads/main|"
        r"refs/heads/main)|contents/datas/[A-Za-z0-9-]+/"
        r"(?:data|metadata)\.json)",
        route,
    ):
        route = "[unrecognized API route]"
    return f"GitHub {verb} {route}"


def github_error_details(method, path, status, body, *, with_reason=False):
    """Return fixed labels from a bounded, unambiguous GitHub error body."""
    label = "unclassified"
    codes = set()

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Ambiguous GitHub error object")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Invalid GitHub JSON constant")

    try:
        if not isinstance(body, bytes) or len(body) > 16384:
            raise ValueError("Unusable GitHub error body")
        decoded = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_constant,
        )
        if not isinstance(decoded, dict):
            raise ValueError("Expected GitHub error object")
        message = decoded.get("message")
        labels = {
            "Bad credentials": "bad-credentials",
            "Resource not accessible by integration": "integration-permission",
            (
                "Resource not accessible by personal " "access token"
            ): "token-permission",
            "Validation Failed": "validation-failed",
            "Reference update failed": "reference-update-failed",
            "Reference does not exist": "reference-missing",
            "Not Found": "not-found",
        }
        if isinstance(message, str):
            label = labels.get(message, label)
            if (
                method == "PATCH"
                and urllib.parse.urlsplit(path).path == "/git/refs/heads/main"
                and status == 422
                and message == "Update is not a fast forward"
            ):
                label = "non-fast-forward"
        errors = decoded.get("errors")
        if isinstance(errors, list) and len(errors) <= 10:
            allowed = {
                "missing",
                "missing_field",
                "invalid",
                "already_exists",
                "unprocessable",
                "custom",
            }
            for error in errors:
                code = error.get("code") if isinstance(error, dict) else None
                if isinstance(code, str) and code in allowed:
                    codes.add(code)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        label, codes = "unclassified", set()
    detail = "; GitHub error: " + label
    if codes:
        detail += "; codes=" + ",".join(sorted(codes)[:3])
    return (label, detail) if with_reason else detail


def github(method, path, payload=None, missing_ok=False):
    global _PUBLICATION_RETRY_JITTER
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError(
            "DATA_SOURCE_TOKEN is required for publication", "publish"
        )
    if method != "GET" and _PUBLICATION_FAILURE is not None:
        raise _PUBLICATION_FAILURE
    window = publication_window()
    deadline = window["read"] if method == "GET" else window["write"]
    retry_jitter = 0
    if method != "GET":
        retry_jitter = _PUBLICATION_RETRY_JITTER
        _PUBLICATION_RETRY_JITTER = 0
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        with publication_http_guard(deadline) as call_end:
            status, _, body = request_bytes(
                _GITHUB + path,
                method=method,
                headers=headers,
                deadline=call_end,
                retry_jitter=retry_jitter,
                payload=(
                    json.dumps(payload).encode()
                    if payload is not None
                    else None
                ),
            )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            github_error_context(method, path)
            + " connection failed; publication is unconfirmed",
            "publish",
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        reason, detail = github_error_details(
            method, path, status, body, with_reason=True
        )
        error = AdapterError(
            f"{github_error_context(method, path)} returned HTTP {status}"
            + detail,
            "publish",
            status,
        )
        error.github_reason = reason
        raise error
    try:
        return json.loads(body)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AdapterError(
            github_error_context(method, path)
            + " returned unusable JSON; publication is unconfirmed",
            "publish",
            status,
        ) from None


def read_snapshot():
    head = github('GET', '/git/ref/heads/main')['object']['sha']
    tree = github('GET', '/git/commits/' + head)['tree']['sha']
    files = {}
    for name in ('data.json', 'metadata.json'):
        file = github('GET', f'/contents/datas/{SOURCE_ID}/{name}?ref={head}', missing_ok=True)
        if file is not None and file.get('encoding') != 'base64':
            raise AdapterError('Unsupported existing source file encoding', 'publish')
        files[name] = None if file is None else base64.b64decode(file['content']).decode('utf-8')
    if (files['data.json'] is None) != (files['metadata.json'] is None):
        raise AdapterError('Incomplete remote source snapshot; no files changed', 'publish')
    return {'head': head, 'tree': tree, 'files': files}


def publish_files(snapshot, files):
    if _PUBLICATION_FAILURE is not None:
        raise _PUBLICATION_FAILURE
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
        except Exception as error:
            if not isinstance(error, AdapterError):
                error = AdapterError(
                    "Unusable publication response; outcome is unconfirmed",
                    "publish",
                )
            try:
                latest = read_snapshot()
            except Exception as verification:
                detail = (
                    safe_error(verification)
                    if isinstance(verification, AdapterError)
                    else type(verification).__name__
                )
                raise stop_publication(
                    AdapterError(
                        f"Publication attempt {attempt + 1}/3 failed: "
                        f"{safe_error(error)}; outcome verification "
                        f"failed: {detail}",
                        "publish",
                        getattr(verification, "status", None),
                    )
                ) from verification
            if candidate and (
                latest["head"] == candidate or latest["files"] == desired
            ):
                return
            if latest["files"] != expected:
                raise stop_publication(
                    AdapterError(
                        "Source changed concurrently; newer remote data "
                        "was not overwritten",
                        "publish",
                    )
                )
            if getattr(error, "github_reason", None) != "non-fast-forward":
                raise stop_publication(
                    AdapterError(
                        "Publication stopped after a non-retryable failure: "
                        + safe_error(error),
                        "publish",
                        error.status,
                    )
                ) from error
            snapshot = latest
            if attempt == 2:
                raise stop_publication(
                    AdapterError(
                        f"Publication failed after {attempt + 1} attempts; "
                        f"last failure: {safe_error(error)}",
                        "publish",
                        error.status,
                    )
                ) from error
            try:
                wait_for_publication_retry(attempt)
            except AdapterError as limit:
                stop_publication(limit)
                raise


def metadata(attempt, previous, records, error=None):
    result = {"display_name": "Fulbright Germany", 'source': SOURCE_ID, 'source_url': SOURCE_URL, 'website_url': WEBSITE_URL,
              'language': LANGUAGE, 'publisher_country': PUBLISHER_COUNTRY, 'publisher_type': PUBLISHER_TYPE,
              'status': 'fail' if error else 'success', 'last_attempt_at': attempt,
              'last_success_at': previous.get('last_success_at') if error else attempt,
              'last_checked_at': previous.get('last_checked_at') if error else attempt,
              'record_count': len(records),
              'message': (
                  'Collection failed; last-good data preserved' if error
                  else f'Validated refresh retains {len(records)} records'
              ),
              'error': safe_error(error) if error else None}
    if ATTRIBUTION:
        result['attribution'] = ATTRIBUTION
    if error:
        result['failure_stage'] = getattr(error, 'stage', 'parse')
    return result


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


def run_main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Write only this source to YouthOpps/data-source using DATA_SOURCE_TOKEN')
    args = parser.parse_args()
    snapshot = read_snapshot() if args.publish else None
    old = json.loads(snapshot['files']['data.json']) if snapshot and snapshot['files']['data.json'] is not None else []
    previous = json.loads(snapshot['files']['metadata.json']) if snapshot and snapshot['files']['metadata.json'] is not None else {}
    if snapshot and snapshot['files']['data.json'] is not None:
        validate_records(old, allow_empty=True)
        if previous.get('source') != SOURCE_ID:
            raise AdapterError('Remote metadata source mismatch; no files changed', 'publish')
    attempt, failure = utc_now(), None
    try:
        records = collect()
        validate_records(records)
        records = remove_expired_records(records, utc_now())
        prior = {record['id']: record for record in old}
        temporal = {'created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at'}
        for record in records:
            before = prior.get(record['id'], {})
            same = {key: value for key, value in before.items() if key not in temporal} == {key: value for key, value in record.items() if key not in temporal}
            record.update(created_at=before.get('created_at', attempt), first_seen_at=before.get('first_seen_at', attempt),
                          updated_at=before.get('updated_at', attempt) if same else attempt, last_seen_at=attempt, last_checked_at=attempt)
        records.sort(key=lambda record: record['id'])
        records.sort(key=lambda record: record['published_at'] or '', reverse=True)
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
    if args.publish and (
        not failure or snapshot["files"]["data.json"] is not None
    ):
        files = {'metadata.json': json.dumps(outcome, ensure_ascii=False, indent=2) + '\n'}
        if not failure:
            files['data.json'] = json.dumps(records, ensure_ascii=False, indent=2) + '\n'
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(attempt, previous, old, AdapterError('Publication failed: ' + safe_error(error), 'publish'))
            try:
                latest = read_snapshot()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError('No safe previous snapshot for failure reporting', 'publish')
                publish_files(latest, {'metadata.json': json.dumps(outcome, ensure_ascii=False, indent=2) + '\n'})
            except Exception:
                outcome['message'] = 'Publication failed; durable failure status could not be recorded. Remote data was not force-overwritten.'
    elif args.publish:
        outcome['message'] = 'First collection failed; no published source folder created'
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0



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
        "in_flight",
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
    if (
        type(state["schema"]) is not int
        or state["schema"] != _STATE_SCHEMA
        or state["family"] != FAMILY
        or not numeric(state["observed_at"])
        or not numeric(state["not_before"])
        or type(state["blocked"]) is not bool
        or type(state["in_flight"]) is not bool
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
        "in_flight": False,
        "interval": 12.2,
        "page_interval": 30,
        "legacy_rate_history_unknown": True,
    }

def budget_file():
    global _STATE_PATH, _STATE_LOCK, _NEW_STATE_DIRECTORY
    global _STATE_DIRECTORY_IDENTITY
    if _STATE_PATH:
        descriptor = open_state_directory()
        os.close(descriptor)
        return
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    else:
        _NEW_STATE_DIRECTORY = True
    descriptor = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        info = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or identity != (current.st_dev, current.st_ino)
            or _STATE_DIRECTORY_IDENTITY is not None
            and identity != _STATE_DIRECTORY_IDENTITY
        ):
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _STATE_DIRECTORY_IDENTITY = identity
        _STATE_PATH = os.path.join(directory, "state.json")
        _STATE_LOCK = os.path.join(directory, "state.lock")
    finally:
        os.close(descriptor)

def locked_budget():
    global _GUARD_LOCK_ID, _GUARD_CREATED, _JOURNAL_NEW
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
    require_healthy_journal()
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
    """A failed write is sticky and cannot be hidden by final completion."""
    global _PUBLISHER_GATE_FAILED, _PERSISTENCE_FAILED
    global _STATE_TRUSTED, _NEW_STATE_DIRECTORY
    if _PERSISTENCE_FAILED:
        raise AdapterError("Publisher persistence gate is closed", "access")
    _NEW_STATE_DIRECTORY = False
    try:
        validate_budget(state)
        remember_restrictive_state(state)
        journal_commit(state)
    except BaseException:
        _PERSISTENCE_FAILED = True
        _PUBLISHER_GATE_FAILED = True
        _STATE_TRUSTED = False
        with contextlib.suppress(BaseException):
            retain_persistence_failure()
        raise

def publisher_pace():
    """Serialize publisher starts under all retained physical constraints."""
    global _PUBLISHER_INTERVAL
    check_deadline()
    with locked_budget():
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError("Unresolved publisher request", "access")
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
        state["in_flight"] = True
        save_budget(state)

@contextlib.contextmanager
def publisher_attempt(publisher, inventory=False):
    """Retain completion gates around each actual publisher transport."""
    global _PUBLISHER_COMPLETED_MONOTONIC, _PUBLISHER_GATE_FAILED
    global _PAGE_COMPLETED, _LOCAL_START_VERIFIED
    if not publisher:
        yield
        return
    if (
        _COLLECTION_LOCK_FD is None
        or _PUBLISHER_GATE_FAILED
        or _PERSISTENCE_FAILED
    ):
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
        completed = time.monotonic()
        _PUBLISHER_COMPLETED_MONOTONIC = completed
        if inventory:
            _PAGE_COMPLETED = completed
        _PUBLISHER_GATE_FAILED = True
        if _PERSISTENCE_FAILED:
            raise AdapterError("Publisher persistence gate is closed", "access")
        with locked_budget():
            state = load_budget()
            now = time.time()
            state["not_before"] = max(
                state["not_before"],
                now
                + max(_PUBLISHER_INTERVAL, state.get("interval", 12.2), 12.2),
            )
            state["observed_at"] = now
            state["in_flight"] = not _RESPONSE_STATE_READY
            save_budget(state)
        _PUBLISHER_GATE_FAILED = _PERSISTENCE_FAILED

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

def read_workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB
            + path
            + ("&" if "?" in path else "?")
            + "_fresh="
            + str(time.time_ns()),
            headers=workflow_read_headers(),
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
        _OBSERVED_ATTEMPTS[(identity, attempt)] = (run["head_sha"], finished)
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

def read_inert_artifact(identity, artifact_id, digest=None):
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, **workflow_read_headers()},
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
            "head_sha",
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
            or envelope["head_sha"] != _ATTEMPT_HEADS.get((run_id, attempt))
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
        raise AdapterError("Mature family history is missing", "access")
    if latest[:2] in {(x[0], x[1]) for x in _LEGACY_ATTEMPTS}:
        incoming = reviewed_legacy_state(latest)
    else:
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
        now = time.time()
        combined = empty_budget(now)
        combined["not_before"] = max(
            local["not_before"], incoming["not_before"], now + 60
        )
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined["in_flight"] = local["in_flight"] or incoming["in_flight"]
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

@contextlib.contextmanager
def publisher_http_guard(deadline):
    """Bound one reviewed Unix/Python call, including waits and body reads."""
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "ITIMER_REAL")
        or not hasattr(signal, "setitimer")
        or not hasattr(signal, "getitimer")
        or not hasattr(signal, "SIGALRM")
    ):
        raise AdapterError("Publisher timer is unavailable", "access")
    active, repeating = signal.getitimer(signal.ITIMER_REAL)
    if active or repeating:
        raise AdapterError("An existing timer prevents publisher", "access")
    end = min(deadline, time.monotonic() + 30)
    remaining = end - time.monotonic()
    if remaining <= 0:
        raise AdapterError("Publisher deadline exhausted", "access")
    previous = signal.getsignal(signal.SIGALRM)

    def exhausted(signum, frame):
        raise AdapterError("Publisher call deadline exhausted", "access")

    try:
        signal.signal(signal.SIGALRM, exhausted)
        signal.setitimer(signal.ITIMER_REAL, remaining)
        yield end
        if time.monotonic() >= end:
            raise AdapterError("Publisher call deadline exhausted", "access")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

def check_deadline():
    """Bound source IO and waits without claiming a parser-wide alarm."""
    if _PHASE_END is None or time.monotonic() >= _PHASE_END:
        raise AdapterError("Publisher IO/wait deadline exhausted", "access")

def request_timeout(publisher=False):
    check_deadline()
    return min(30, _PHASE_END - time.monotonic())

def public_url(value):
    """Retain the original reviewed HTTPS host boundary."""
    parsed = urllib.parse.urlsplit(value)
    original = urllib.parse.urlsplit(SOURCE_URL)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or host != original.hostname.lower().removeprefix("www.")
        or parsed.port != original.port
    ):
        raise AdapterError("Source leaves reviewed HTTPS host", "access")
    return value

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
def publisher_request_bytes(
    url, interval=6, *, robots=False, inventory=False, deadline=None
):
    """Read one physical source hop; the existing fetcher owns hop limits."""
    global _PUBLISHER_INTERVAL, _RESPONSE_STATE_READY
    _RESPONSE_STATE_READY = False
    if _PERSISTENCE_FAILED or _PUBLISHER_GATE_FAILED or not _STATE_TRUSTED:
        raise AdapterError("Publisher persistence gate is closed", "access")
    public_url(url)
    parsed = urllib.parse.urlsplit(url)
    if robots and (parsed.path != "/robots.txt" or parsed.query or inventory):
        raise AdapterError("Invalid robots request purpose", "access")
    if not numeric(interval) or interval < 0:
        raise AdapterError("Invalid publisher interval", "access")
    _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, interval, 12.2)
    if inventory:
        wait_inventory_page()
    publisher_pace()
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with publisher_attempt(True, inventory):
        call_end = _PHASE_END if deadline is None else min(_PHASE_END, deadline)
        with publisher_http_guard(call_end) as end:
            try:
                journal_preopener()
                response = _OPENER.open(
                    request, timeout=min(30, end - time.monotonic())
                )
            except urllib.error.HTTPError as error:
                response = error
            with (
                header_rate_limit_response(response)
                if True and response.status == 429
                else response
            ):
                status, headers = response.status, response.headers
                record_retry_after(headers.get("Retry-After"))
                if status == 429:
                    retain_rate_limit()
                    stop_rate_limit(response, byte_limit=65536)
                if robots and 400 <= status < 500:
                    _RESPONSE_STATE_READY = True
                    return status, headers, b""
                if not robots and status in (401, 403, 451):
                    retain_publisher_hold()
                    _RESPONSE_STATE_READY = True
                    raise AdapterError(
                        "Publisher content refusal", "access", status
                    )
                declared = headers.get("Content-Length")
                if declared and (
                    not declared.isdigit() or int(declared) > 5_000_000
                ):
                    raise AdapterError("Oversized publisher response", "fetch")
                body = response.read(5_000_001)
                if (
                    len(body) > 5_000_000
                    or declared
                    and len(body) != int(declared)
                ):
                    raise AdapterError(
                        "Incomplete or oversized source", "fetch"
                    )
                if headers.get("Content-Encoding", "identity") != "identity":
                    raise AdapterError("Unsupported source encoding", "fetch")
                if not (robots and 400 <= status < 500):
                    try:
                        reject_visible_challenge(body, headers, status=status)
                    except AdapterError:
                        _RESPONSE_STATE_READY = not _PERSISTENCE_FAILED
                        raise
                _RESPONSE_STATE_READY = not (robots and status == 200)
                if status in (301, 302, 303, 307, 308):
                    destination = urllib.parse.urljoin(
                        url, headers.get("Location", "")
                    )
                    public_url(destination)
                    target = urllib.parse.urlsplit(destination)
                    if robots and (
                        target.path != "/robots.txt" or target.query
                    ):
                        raise AdapterError(
                            "Robots redirect changes purpose", "access"
                        )
                return status, headers, body

def release_collection():
    global _COLLECTION_LOCK_FD, _RESTORED, _LOCAL_START_VERIFIED
    journal_finish()
    _RESTORED = False
    _LOCAL_START_VERIFIED = False

def prepare_collection():
    """Restore before snapshot/proof errors; never reopen failed persistence."""
    global _GUARD_COLLECTION_ID
    global _COLLECTION_STARTED, _COLLECTION_LOCK_FD, _STATE_TRUSTED
    global _PHASE_END, _PAGE_COMPLETED, _PUBLISHER_INTERVAL
    global _LOCAL_START_VERIFIED
    if _PERSISTENCE_FAILED or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate is closed", "access")
    if _RESTORED:
        return
    _LOCAL_START_VERIFIED = False
    _COLLECTION_STARTED = time.time()
    _PHASE_END = time.monotonic() + COLLECTION_TIMEOUT
    if os.environ.get("GITHUB_ACTIONS") != "true":
        verify_local_workflow_pause()
    budget_file()
    require_healthy_journal()
    parent = open_state_directory()
    parent_info = os.fstat(parent)
    parent_id = (parent_info.st_dev, parent_info.st_ino)
    descriptor = None
    acquired_id = None
    try:
        acquired_id = None
        descriptor = os.open(
            "collection.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        acquired_info = os.fstat(descriptor)
        acquired_id = (acquired_info.st_dev, acquired_info.st_ino)
        verify_state_directory(parent)
    except BaseException:
        if descriptor is not None:
            if acquired_id is not None:
                journal_close(descriptor, acquired_id, True)
        raise
    finally:
        try:
            io_close_owned_descriptor(parent, parent_id, True)
        except BaseException:
            if descriptor is not None and acquired_id is not None:
                journal_close(descriptor, acquired_id, True)
            raise
    try:
        info = os.fstat(descriptor)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise OSError("Unsafe collection lock")
        journal_owned(descriptor, acquired_id)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_owned(descriptor, acquired_id)
        _GUARD_COLLECTION_ID = acquired_id
    except BaseException:
        if acquired_id is not None:
            journal_close(descriptor, acquired_id, True)
        raise AdapterError(
            "Exclusive source collection required", "access"
        ) from None
    _COLLECTION_LOCK_FD = descriptor
    restore_family_artifact()
    with locked_budget():
        if load_budget()["in_flight"]:
            raise AdapterError("Unresolved owned publisher request", "access")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        _PHASE_END = min(_PHASE_END, publication_window()["write"] - 120)
    check_deadline()
    with locked_budget():
        state = load_budget()
        state["interval"] = max(state.get("interval", 6), 12.2)
        state["page_interval"] = max(state.get("page_interval", 12), 30)
        state["not_before"] = max(state["not_before"], time.time() + 60)
        state["observed_at"] = time.time()
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"])
        save_budget(state)
    _PAGE_COMPLETED = time.monotonic()
    _STATE_TRUSTED = True

def strict_request_rate(values):
    """Interpret selected REP rates without weakening unsupported restrictions."""
    intervals = [0]
    for value in values:
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(["User-agent: YouthOppsBot", "Request-rate: " + value])
        rate = parser.request_rate("YouthOppsBot")
        if (
            rate is None
            or not numeric(rate.requests)
            or rate.requests <= 0
            or not numeric(rate.seconds)
            or rate.seconds <= 0
        ):
            raise AdapterError("Unsupported robots request rate", "access")
        try:
            interval = rate.seconds / rate.requests + 0.01
        except (ValueError, OverflowError, ZeroDivisionError):
            raise AdapterError(
                "Unsupported robots request rate", "access"
            ) from None
        if not numeric(interval) or interval <= 0:
            raise AdapterError("Unsupported robots request rate", "access")
        intervals.append(interval)
    return max(intervals)

def assess_robots_policy(url, text):
    parsed = urllib.parse.urlsplit(url)
    groups, group = [], {"agents": [], "rules": [], "delay": 0, "rates": []}
    for line in text.splitlines():
        key, separator, value = line.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"] or group["rates"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0, "rates": []}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
        elif key == "request-rate":
            group["rates"].append(value)
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
            retain_publisher_hold()
            raise AdapterError(
                "Robots policy disallows this source endpoint", "access"
            )
    return max(
        [6]
        + [
            max(g["delay"], strict_request_rate(g["rates"]))
            for policy in policies
            for g in policy
        ]
    )

def workflow_read_headers():
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError(
            "Hosted workflow state credentials missing", "access"
        )
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers

def workflow_api(path):
    with publisher_http_guard(_PHASE_END):
        return read_workflow_api(path)

def download_inert_artifact(identity, artifact_id, digest=None):
    with publisher_http_guard(_PHASE_END):
        return read_inert_artifact(identity, artifact_id, digest)

def reviewed_legacy_state(latest):
    """Allow only the explicitly approved exact old zero-artifact frontier."""
    approved = (
        _PRODUCTION_TRANSITION
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else _LOCAL_TRANSITION
    )
    if not isinstance(approved, dict) or set(approved) != {
        "evidence",
        "history_sha256",
        "state",
    }:
        raise AdapterError("Reviewed legacy transition required", "access")
    if approved["history_sha256"] != _LEGACY_HISTORY_SHA256 or not re.fullmatch(
        (
            r"https://github.com/YouthOpps/data-pipeline/issues/270"
            r"#issuecomment-[0-9]+"
        ),
        approved["evidence"],
    ):
        raise AdapterError("Untrusted legacy transition", "access")
    observed = {(i, a, h, t) for (i, a), (h, t) in _OBSERVED_ATTEMPTS.items()}
    if (
        observed != set(_LEGACY_ATTEMPTS)
        or latest[:2] not in _OBSERVED_ATTEMPTS
    ):
        raise AdapterError("Legacy family frontier changed", "access")
    for identity, attempt, head, finished in _LEGACY_ATTEMPTS:
        inventory = workflow_api(
            f"/actions/runs/{identity}/artifacts?per_page=100"
        )
        if (
            inventory.get("total_count") != 0
            or inventory.get("artifacts") != []
        ):
            raise AdapterError("Legacy artifact frontier changed", "access")
    state = validate_budget(json.loads(json.dumps(approved["state"])))
    if not state.get("legacy_rate_history_unknown", True):
        raise AdapterError("Legacy history cannot become known", "access")
    return state

def main():
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        return run_main()
    try:
        prepare_collection()
        return run_main()
    finally:
        try:
            end = time.monotonic() + 30
            if _PUBLICATION_WINDOW is not None:
                end = min(end, _PUBLICATION_WINDOW["job"])
            with publisher_http_guard(end):
                export_family_artifact()
        finally:
            release_collection()

def artifact_parent():
    path = os.environ.get("GERMANY_PACING_ARTIFACT_PATH")
    if not path:
        return None
    configured = os.environ.get("RUNNER_TEMP", "")
    if (
        not os.path.isabs(configured)
        or os.path.realpath(configured) != os.path.abspath(configured)
        or os.path.dirname(os.path.abspath(path)) != configured
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    parent = os.open(configured, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(parent)
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_mode & 0o022
    ):
        os.close(parent)
        raise AdapterError("Unsafe pacing artifact directory", "access")
    return parent, "pacing-state.json"

def verify_artifact_descriptor(parent, name, descriptor):
    info = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_mode & 0o077
        or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise AdapterError("Unsafe owned pacing artifact", "access")

def unlink_owned_artifact(parent, name, descriptor):
    verify_artifact_descriptor(parent, name, descriptor)
    os.unlink(name, dir_fd=parent)
    os.fsync(parent)

def signal_pacing_ready(ready):
    value = os.environ.get("GITHUB_OUTPUT")
    if not value:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Owned workflow output required", "access")
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(configured) or not os.path.isabs(value):
        raise AdapterError("Unsafe workflow output path", "access")
    parts = os.path.relpath(value, configured).split(os.sep)
    if any(name in {"", ".", ".."} for name in parts):
        raise AdapterError(
            "Workflow output leaves runner temporary root", "access"
        )
    descriptors = []
    try:
        parent = os.open(
            configured, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        descriptors.append(parent)
        for name in parts[:-1]:
            parent = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
            descriptors.append(parent)
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise AdapterError("Unsafe output directory", "access")
        fd = os.open(
            parts[-1],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        descriptors.append(fd)
        info = os.fstat(fd)
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe workflow output owner", "access")
        line = b"pacing_ready=true\n" if ready else b"pacing_ready=false\n"
        if os.write(fd, line) != len(line):
            raise AdapterError("Incomplete artifact signal", "access")
        os.fsync(fd)
    finally:
        for fd in reversed(descriptors):
            os.close(fd)

def invalidate_family_artifact():
    journal_failed()
    export_family_artifact()

def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("GERMANY_PACING_ARTIFACT_PATH")
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
    if (
        root != os.path.abspath(configured)
        or os.path.basename(path) != "pacing-state.json"
    ):
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
            if state["in_flight"]:
                raise AdapterError(
                    "Unsettled state cannot be exported", "access"
                )
        if not re.fullmatch(r"[0-9a-f]{40}", os.environ.get("GITHUB_SHA", "")):
            raise AdapterError("Exact export head required", "access")
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "head_sha": os.environ.get("GITHUB_SHA"),
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
                check_deadline()
                journal_verify()
                io_mark_pacing_artifact_ready(parent, root)
                journal_verify()
                io_artifact_descriptor(descriptor, identity)
                io_artifact_descriptor(guard, identity)
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
            io_verify_owned_descriptor(parent, parent_identity, True)
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

def require_healthy_journal():
    parent = open_state_directory()
    try:
        try:
            os.stat("persistence-failed", dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
    finally:
        os.close(parent)
    raise AdapterError(
        "Publisher persistence requires reviewed recovery", "access"
    )

def remember_restrictive_state(state):
    global _RESTRICTIVE_STATE
    if _RESTRICTIVE_STATE is None:
        _RESTRICTIVE_STATE = copy.deepcopy(state)
        return
    known = _RESTRICTIVE_STATE
    known["in_flight"] = state["in_flight"]
    for name in (
        "observed_at",
        "not_before",
        "interval",
        "page_interval",
        "rate_failures",
    ):
        known[name] = max(known.get(name, 0), state.get(name, 0))
    known["blocked"] = known["blocked"] or state["blocked"]
    known["legacy_rate_history_unknown"] = known.get(
        "legacy_rate_history_unknown", True
    ) or state.get("legacy_rate_history_unknown", True)
    known["starts"] = sorted(set(known["starts"] + state["starts"]))[-10:]

def retain_persistence_failure():
    """Make both future processes and stale exports fail closed."""
    errors = []
    try:
        parent = open_state_directory()
        try:
            info = os.fstat(parent)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AdapterError("Unsafe journal directory", "access")
            try:
                fd = os.open(
                    "persistence-failed",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
            except FileExistsError:
                fd = os.open(
                    "persistence-failed",
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
            else:
                try:
                    marker = b"publisher-persistence-failed\n"
                    if os.write(fd, marker) != len(marker):
                        raise OSError("Incomplete failure marker")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                fd = None
            if fd is not None:
                try:
                    info = os.fstat(fd)
                    if (
                        info.st_uid != os.getuid()
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_mode & 0o077
                        or info.st_nlink != 1
                    ):
                        raise AdapterError("Unsafe failure marker", "access")
                finally:
                    os.close(fd)
            os.fsync(parent)
        finally:
            os.close(parent)
    except BaseException:
        errors.append("marker")
    try:
        journal_invalidate()
    except BaseException:
        errors.append("journal")
    try:
        invalidate_family_artifact()
    except BaseException:
        errors.append("export")
    if errors:
        raise AdapterError(
            "Publisher failure retained; cleanup refused", "access"
        )

def verify_state_directory(descriptor):
    """Refuse a replacement namespace, while allowing atomic state saves."""
    info = os.fstat(descriptor)
    current = os.stat(os.path.dirname(_STATE_PATH), follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or _STATE_DIRECTORY_IDENTITY is None
        or (info.st_dev, info.st_ino) != _STATE_DIRECTORY_IDENTITY
        or (current.st_dev, current.st_ino) != _STATE_DIRECTORY_IDENTITY
    ):
        raise AdapterError("Publisher state parent identity changed", "access")

def open_state_directory():
    if _STATE_PATH is None or _STATE_DIRECTORY_IDENTITY is None:
        raise AdapterError("Verified publisher parent required", "access")
    descriptor = os.open(
        os.path.dirname(_STATE_PATH),
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
    )
    try:
        verify_state_directory(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor



_GUARD_PARENT = _GUARD_PARENT_ID = None
_GUARD_FD = _GUARD_ID = _GUARD_VERSION = None
_GUARD_LOCK_ID = None
_GUARD_LOCKS = {}
_GUARD_CREATED = _GUARD_DIRTY = _GUARD_FATAL = False
_GUARD_COLLECTION_ID = None
_GUARD_NEW_DIRECTORY = False
_GUARD_FAILED_STATE = None


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


def journal_preopener():
    """Final publisher authority check, after request construction."""
    try:
        journal_verify()
        if _GUARD_COLLECTION_ID is None or _COLLECTION_LOCK_FD is None:
            raise AdapterError(
                "Publisher collection ownership missing", "access"
            )
        journal_owned(_COLLECTION_LOCK_FD, _GUARD_COLLECTION_ID)
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


def journal_creation_allowed():
    return _NEW_STATE_DIRECTORY


def journal_mark_mature():
    global _NEW_STATE_DIRECTORY
    _NEW_STATE_DIRECTORY = False


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


def io_mark_pacing_artifact_ready(runner_parent, runner_root):
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
    global _RESPONSE_STATE_READY
    try:
        with response:
            yield
    except HeaderRateLimitStop:
        _RESPONSE_STATE_READY = True
        raise
    except BaseException as error:
        error._header429_unassessed = True
        # Latch before any fallible best-effort diagnostics.
        with contextlib.suppress(BaseException):
            journal_failed()
        raise


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'source': SOURCE_ID, 'status': 'fail', 'last_attempt_at': utc_now(),
                          'message': 'Run could not complete; durable status unavailable',
                          'error': safe_error(error), 'failure_stage': getattr(error, 'stage', 'publish')}, ensure_ascii=False))
        sys.exit(1)
