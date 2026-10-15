"""FEBA Alumni: explicit opportunity calls from its public WordPress API."""
import contextlib
import datetime as dt
import html
import json
import random
import re
import signal
import threading
import urllib.parse

SOURCE_ID = "bg-feba-alumni"
SOURCE_URL = "https://febalumni.org/news/"
WEBSITE_URL = "https://febalumni.org/"
LANGUAGE = "bg/en"
PUBLISHER_COUNTRY = "BG"
PUBLISHER_TYPE = "unknown"
ATTRIBUTION = "FEBA Alumni"
_API_URL = "https://febalumni.org/wp-json/wp/v2/posts"
_RELEVANT = re.compile(r"стипенд|scholarship|академи|академия|обучени|certificate|сертификат|training|стаж|internship|volunteer|добровол", re.I)
_EXCLUDE = re.compile(r"победител|наградени|резултатите от|приключи|връчени|awarded|winners|отличи|партньори на представянето|проведе се|се проведе", re.I)
_CALL = re.compile(r"обявява конкурс|краен срок|кандидатств|възможност за|записван|покана за кандидатстване|прием на кандидатури|applications? open|apply now|open call", re.I)
_CATEGORIES = (
    ("scholarships", r"стипенд|scholarship"),
    ("training", r"академи|обучени|certificate|сертификат|training"),
    ("internships", r"стаж|internship"),
    ("volunteering", r"volunteer|добровол"),
)


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            wait = _PAGE_COMPLETED + 12 - time.monotonic()
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


def title_deadline(title):
    """Read only explicit application-closing dates from current call titles."""
    application_context = re.search(
        r"кандидатств|прием на кандидат|записван|конкурс.*стипенд",
        title,
        re.I,
    )
    if not application_context:
        return None
    matches = re.findall(
        r"краен срок\s*(?:за кандидатстване\s*)?[:–-]?\s*"
        r"(?:до\s*)?(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)",
        title,
        re.I,
    )
    if len(matches) != len(re.findall(r"краен срок", title, re.I)):
        return None
    dates = set()
    for day, month, year in matches:
        try:
            dates.add(dt.date(int(year), int(month), int(day)).isoformat())
        except ValueError:
            return None
    return dates.pop() if len(dates) == 1 else None


def collect():
    delay = check_robots(_API_URL)
    records = {}
    for page in range(1, 26):
        query = urllib.parse.urlencode({"per_page": 100, "page": page, "_fields": "id,date_gmt,link,title,content"})
        posts = json.loads(fetch_inventory_page(
            fetch_source, _API_URL + "?" + query, min_interval=delay))
        if not isinstance(posts, list):
            raise ValueError("Unexpected FEBA API payload")
        for post in posts:
            if not isinstance(post, dict) or not isinstance(post.get("title"), dict):
                raise ValueError("Invalid FEBA post structure")
            title = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", post["title"].get("rendered", ""))).split())
            if not _RELEVANT.search(title) or not _CALL.search(title) or _EXCLUDE.search(title):
                continue
            content = post.get("content")
            if not isinstance(content, dict) or not isinstance(
                content.get("rendered"), str
            ):
                raise ValueError("Invalid FEBA post content")
            body = " ".join(
                html.unescape(
                    re.sub(r"<[^>]*>", " ", content["rendered"])
                ).split()
            )
            editorial = re.search(r"(?:разговор с|интервю с)\s", body, re.I)
            explicit_call = re.search(
                r"кандидатств|прием на кандидат|записван|краен срок|"
                r"applications? open|apply now|open call",
                title,
                re.I,
            )
            if editorial and not explicit_call:
                print(
                    "Excluded publisher-labelled interview without a call: "
                    + title,
                    file=sys.stderr,
                )
                continue
            url = post.get("link")
            if not isinstance(url, str):
                continue
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" or (parsed.hostname or "").removeprefix("www.") != "febalumni.org" or parsed.username or parsed.password or parsed.port not in (None, 443) or not re.match(r"^/\d{4}/\d{2}/\d{2}/", parsed.path):
                continue
            category = next((name for name, pattern in _CATEGORIES if re.search(pattern, title, re.I)), None)
            if not category:
                continue
            published = None
            date = post.get("date_gmt")
            if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", date):
                try:
                    published = dt.datetime.fromisoformat(date).replace(tzinfo=dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                except ValueError:
                    pass
            record = make_record(title, url, [category], published_at=published, tags=[category], host_countries=["BG"], method="feba-publisher-api", evidence=[url])
            record["deadline"] = title_deadline(title)
            records[record["id"]] = record
        if len(posts) < 100:
            if not records:
                raise ValueError("FEBA returned no eligible opportunity records")
            return list(records.values())
    raise ValueError("FEBA source exceeds safe pagination limit")


# All transport, output and publication code below belongs to this adapter.
# Python standard library only; no imports from another adapter or project module.
import argparse
import base64
import hashlib
import ipaddress
import os
import re
import sys
import time
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

CATEGORIES = ('scholarships', 'internships', 'volunteering', 'fellowships', 'training', 'competitions', 'grants', 'jobs', 'other')
_GITHUB = 'https://api.github.com/repos/YouthOpps/data-source'
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
_BUDGETS = {}


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
    retry_jitter=0
):
    if retry_jitter:
        budget = pace(url, interval, start_padding=retry_jitter)
    else:
        budget = pace(url, interval)
    request = urllib.request.Request(url, data=payload, method=method, headers={'User-Agent': _USER_AGENT, **(headers or {})})
    try:
        transport_timeout = 30
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AdapterError(
                    "Publication deadline exhausted", "publish"
                )
            transport_timeout = min(30, remaining)
        response = _OPENER.open(request, timeout=transport_timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        retry = response.headers.get('Retry-After')
        if retry:
            try:
                seconds = float(retry) if re.fullmatch(r'\d+(?:\.\d+)?', retry) else parsedate_to_datetime(retry).timestamp() - time.time()
                budget['until'] = max(budget['until'], time.monotonic() + max(0, seconds))
            except (ValueError, TypeError, OverflowError):
                pass
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise AdapterError('Response exceeds 5 MB', 'fetch')
        return response.status, response.headers, body


def fetch_source(url, min_interval=6):
    original = urllib.parse.urlsplit(url)
    def checked(value):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or '').lower().removeprefix('www.')
        if parsed.scheme != 'https' or parsed.username or parsed.password or host != (original.hostname or '').lower().removeprefix('www.') or parsed.port != original.port:
            raise AdapterError('Source redirect leaves the reviewed HTTPS host', 'fetch')
        if host == 'localhost' or host.endswith('.localhost'):
            raise AdapterError('Public HTTPS source required', 'fetch')
        try:
            if not ipaddress.ip_address(host).is_global:
                raise AdapterError('Public HTTPS source required', 'fetch')
        except ValueError:
            pass
    for attempt in range(2):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(current, interval=min_interval)
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get('Location'):
                        raise AdapterError('Invalid or excessive source redirects', 'fetch')
                    current = urllib.parse.urljoin(current, headers['Location'])
                    continue
                if status != 200:
                    raise AdapterError(f'Publisher returned HTTP {status}', 'fetch', status)
                return body.decode(headers.get_content_charset() or 'utf-8')
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError('Publisher connection failed: ' + safe_error(error), 'fetch') from error
    raise AdapterError('Publisher could not be read', 'fetch')


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    try:
        text = fetch_source(urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/robots.txt', '', '')))
    except AdapterError as error:
        if error.status == 404:
            return 6
        raise
    groups, group = [], {'agents': [], 'rules': [], 'delay': 0}
    for line in text.splitlines():
        key, separator, value = line.split('#', 1)[0].strip().partition(':')
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == 'user-agent':
            if group['rules'] or group['delay']:
                groups.append(group)
                group = {'agents': [], 'rules': [], 'delay': 0}
            group['agents'].append(value.lower())
        elif key in ('allow', 'disallow'):
            group['rules'].append((key, value))
        elif key == 'crawl-delay':
            try:
                group['delay'] = float(value)
                if not 0 <= group['delay'] <= 60:
                    raise ValueError()
            except ValueError:
                raise AdapterError('Unsupported robots crawl delay', 'access')
    groups.append(group)
    directives = [line.split('#', 1)[0].strip().lower() for line in text.splitlines() if line.split('#', 1)[0].strip()]
    if directives and not any(g['agents'] for g in groups) and not all(line.startswith('sitemap:') for line in directives):
        raise AdapterError('Robots policy is unavailable or invalid', 'access')
    agent = _USER_AGENT.split("/", 1)[0].lower()
    specific = [g for g in groups if agent in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    policies = [policy for policy in (specific, legacy) if policy]
    if not policies:
        policies = [[g for g in groups if "*" in g["agents"]]]
    path = parsed.path + ('?' + parsed.query if parsed.query else '')
    for selected in policies:
        matches = []
        for g in selected:
            for directive, pattern in g['rules']:
                if not pattern:
                    continue
                expression = (
                    '^' + '.*'.join(
                        re.escape(part)
                        for part in pattern.removesuffix('$').split('*')
                    ) + ('$' if pattern.endswith('$') else '')
                )
                if re.search(expression, path):
                    matches.append((
                        len(pattern.replace('*', '').removesuffix('$')),
                        directive == 'allow',
                    ))
        if matches and not max(matches)[1]:
            raise AdapterError(
                'Robots policy disallows this source endpoint', 'access'
            )
    return max(
        [6] + [g["delay"] for policy in policies for g in policy]
    )


def make_record(title, url, categories, kind='opportunity', published_at=None, tags=None, host_countries=None, method='editorial-review', evidence=None):
    now = utc_now()
    return {'id': hashlib.sha256(f'{SOURCE_ID}|{url}'.encode()).hexdigest()[:24], 'title': title,
            'url': url, 'source': SOURCE_ID, 'source_url': SOURCE_URL, 'published_at': published_at,
            'summary': '', 'tags': tags or [], 'location': None, 'deadline': None, 'language': LANGUAGE,
            'category': categories[0], 'categories': categories, 'kind': kind, 'host_countries': host_countries or [],
            'eligible_countries': [], 'publisher_country': PUBLISHER_COUNTRY,
            'created_at': now, 'updated_at': now, 'first_seen_at': now, 'last_seen_at': now, 'last_checked_at': now,
            'status': 'unknown', 'classification': {'method': method, 'status': 'unknown' if categories == ['other'] else 'classified', 'evidence': evidence or []}}


def remove_expired_records(records, checked_at):
    """Filter validated deadlines after retrieval, before atomic publication.

    Unknown publisher zones use UTC-12 only as a global certainty bound:
    a date remains eligible until its closing day has ended everywhere.
    No age, status, missing response or unstated local clock implies expiry.
    """
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    latest_day = checked.astimezone(timezone(timedelta(hours=-12))).date()
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if isinstance(deadline, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                    closing_day = datetime.strptime(deadline, "%Y-%m-%d").date()
                    expired = closing_day < latest_day
                else:
                    cutoff = datetime.fromisoformat(
                        deadline.replace("Z", "+00:00")
                    )
                    expired = cutoff.tzinfo is not None and cutoff < checked
            except ValueError:
                # Unsupported or ambiguous deadlines never authorize removal.
                pass
        if not expired:
            retained.append(record)
    return retained


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
                    if field == "deadline" and re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        dt.date.fromisoformat(value)
                    elif datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    ).tzinfo is None:
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
    result = {"display_name": "FEBA Alumni", 'source': SOURCE_ID, 'source_url': SOURCE_URL, 'website_url': WEBSITE_URL,
              'language': LANGUAGE, 'publisher_country': PUBLISHER_COUNTRY, 'publisher_type': PUBLISHER_TYPE,
              'status': 'fail' if error else 'success', 'last_attempt_at': attempt,
              'last_success_at': previous.get('last_success_at') if error else attempt,
              'last_checked_at': previous.get('last_checked_at') if error else attempt,
              'record_count': len(records),
              'message': (
                  'Collection failed; last-good data preserved'
                  if error
                  else f'Validated refresh retains {len(records)} records'
              ),
              'error': safe_error(error) if error else None}
    if ATTRIBUTION:
        result['attribution'] = ATTRIBUTION
    if error:
        result['failure_stage'] = getattr(error, 'stage', 'parse')
    return result


def main():
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
        prior = {record['id']: record for record in old}
        temporal = {'created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at'}
        for record in records:
            before = prior.get(record['id'], {})
            same = {key: value for key, value in before.items() if key not in temporal} == {key: value for key, value in record.items() if key not in temporal}
            record.update(created_at=before.get('created_at', attempt), first_seen_at=before.get('first_seen_at', attempt),
                          updated_at=before.get('updated_at', attempt) if same else attempt, last_seen_at=attempt, last_checked_at=attempt)
        records.sort(key=lambda record: record['id'])
        records.sort(key=lambda record: record['published_at'] or '', reverse=True)
        records = remove_expired_records(records, utc_now())
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
                    snapshot['files']['data.json'] is None
                    or latest['files'] != snapshot['files']
                ):
                    raise AdapterError('No safe previous snapshot for failure reporting', 'publish')
                publish_files(latest, {'metadata.json': json.dumps(outcome, ensure_ascii=False, indent=2) + '\n'})
            except Exception:
                outcome['message'] = 'Publication failed; durable failure status could not be recorded. Remote data was not force-overwritten.'
    elif args.publish:
        outcome['message'] = 'First collection failed; no published source folder created'
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'source': SOURCE_ID, 'status': 'fail', 'last_attempt_at': utc_now(),
                          'message': 'Run could not complete; durable status unavailable',
                          'error': safe_error(error), 'failure_stage': getattr(error, 'stage', 'publish')}, ensure_ascii=False))
        sys.exit(1)
