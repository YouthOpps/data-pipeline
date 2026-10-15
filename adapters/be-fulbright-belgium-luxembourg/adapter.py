"""Collect complete own-site Fulbright Belgium/Luxembourg award facts."""

import argparse
import base64
import contextlib
import copy
import datetime
import email.utils
import fcntl
import hashlib
import html
import html.parser
import ipaddress
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import zipfile

SOURCE_ID = "be-fulbright-belgium-luxembourg"
WEBSITE_URL = "https://www.fulbright.be/"
SOURCE_URL = WEBSITE_URL + "grants-for-belgian-and-luxembourgish-citizens/"
INCOMING = WEBSITE_URL + "grants-for-american-citizens/"
PARTNERS = SOURCE_URL + "fulbright-partner-awards/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "BE"
PUBLISHER_TYPE = "commission"
ATTRIBUTION = "Fulbright Commission for Belgium and Luxembourg"
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
_ROBOTS = {}
_COLLECTION_STARTED = None
_COLLECTION_LIMIT = 900
_PAGE_GAP = 30.0
_STATE_TRUSTED = False
# Reviewed native publisher state, usable only before any main workflow attempt.
_REVIEWED_BOOTSTRAP = {
    "schema": 1,
    "source": "be-fulbright-belgium-luxembourg",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/21",
    "native_state_sha256": (
        "edf9180ac3624f4a07c2945384b536b69adb91906554625ba9582a73ec990999"
    ),
    "state": {
        "starts": [
            1792067355.0176306,
            1792067369.1558115,
            1792067382.4448342,
            1792067395.2561417,
            1792067408.1223795,
        ],
        "until": 1792067421.4369748,
        "interval": 12.1,
        "page_gap": 30,
        "research_history": {
            "starts": [1792066306.0812669, 1792066314.0928824],
            "until": 1792066322.4191391,
            "historical_holds_retained": [
                "#19 " "upstream " "certificate " "failure",
                "#21 " "upstream " "certificate " "failure",
            ],
        },
    },
}


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


def pace(url, interval=6):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
    global _PAGE_GAP
    host = (
        (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    )
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    information = os.lstat(directory)
    if (
        not os.path.isdir(directory)
        or os.path.islink(directory)
        or information.st_uid != os.getuid()
        or information.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    publisher = (
        "fulbright.be" if host in {"fulbright.be", "www.fulbright.be"} else host
    )
    key = hashlib.sha256(publisher.encode()).hexdigest()
    descriptor = os.open(
        os.path.join(directory, key),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    state = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read()
        budget = (
            json.loads(raw)
            if raw
            else {"starts": [], "interval": 6, "until": 0}
        )
        source = publisher == "fulbright.be"
        budget["interval"] = max(
            budget["interval"], interval, 12.1 if source else 6.1
        )
        if source:
            if budget.get("bootstrap_pending"):
                raise AdapterError(
                    "Publisher bootstrap awaits snapshot confirmation", "access"
                )
            budget["page_gap"] = max(budget.get("page_gap", 30), 30)
            _PAGE_GAP = max(_PAGE_GAP, budget["page_gap"])
            if budget.get("rate_limit_until", 0) > time.time():
                raise AdapterError(
                    "Publisher rate-limit pause remains active", "access", 429
                )
        if publisher == "fulbright.be" and budget.get("blocked"):
            raise AdapterError(
                "Retained publisher refusal requires review", "access"
            )
        while True:
            if (
                publisher == "fulbright.be"
                and _COLLECTION_STARTED is not None
                and time.monotonic() - _COLLECTION_STARTED > _COLLECTION_LIMIT
            ):
                raise AdapterError(
                    "Complete collection exceeded its time budget", "access"
                )
            now = time.time()
            budget["starts"] = [
                start for start in budget["starts"] if now - start < 60
            ]
            ready = max(
                budget["until"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (
                    budget["starts"][0] + 60
                    if len(budget["starts"]) >= (5 if source else 10)
                    else now
                ),
            )
            if ready <= now:
                budget["starts"].append(now)
                save_budget(state, budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


def request_bytes(url, *, interval=6, method="GET", headers=None, payload=None):
    state, budget = pace(url, interval)
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={"User-Agent": _USER_AGENT, **(headers or {})},
        )
        if request.get_header("User-agent") != _USER_AGENT:
            raise AdapterError("Crawler identity changed", "access")
        try:
            response = _OPENER.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retry = response.headers.get("Retry-After")
            retry_seconds = None
            if retry:
                try:
                    if re.fullmatch(r"\d+(?:\.\d+)?", retry):
                        seconds = float(retry)
                    else:
                        instant = email.utils.parsedate_to_datetime(retry)
                        if instant.tzinfo is None:
                            raise ValueError("Retry-After requires a timezone")
                        seconds = instant.timestamp() - time.time()
                    until = time.time() + seconds
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise ValueError("Unusable Retry-After")
                    datetime.datetime.fromtimestamp(
                        until, datetime.timezone.utc
                    )
                    retry_seconds = seconds
                    budget["until"] = max(budget["until"], until)
                    save_budget(state, budget)
                except (ValueError, TypeError, OverflowError, OSError):
                    pass
            parsed = urllib.parse.urlsplit(url)
            if (
                parsed.hostname in {"www.fulbright.be", "fulbright.be"}
                and response.status == 429
            ):
                # Persist stricter spacing as well as the embargo. A later job
                # may retry normally after expiry; this job always stops now.
                count = budget.get("rate_limit_count", 0) + 1
                budget["rate_limit_count"] = count
                multiplier = 2**count
                budget["interval"] = max(
                    budget["interval"] * 2, 12.1 * multiplier
                )
                budget["page_gap"] = max(
                    budget.get("page_gap", 30) * 2, 30 * multiplier
                )
                pause = (
                    retry_seconds
                    if retry_seconds is not None
                    else 1800 * multiplier / 2
                )
                budget["rate_limit_until"] = max(
                    budget.get("rate_limit_until", 0), time.time() + pause
                )
                budget["until"] = max(
                    budget["until"], budget["rate_limit_until"]
                )
                save_budget(state, budget)
                raise AdapterError(
                    "Publisher rate limited access; collection stopped",
                    "access",
                    429,
                )
            if (
                parsed.hostname in {"www.fulbright.be", "fulbright.be"}
                and response.status in {401, 403}
                and not (
                    parsed.path == "/robots.txt"
                    and response.status in {401, 403}
                )
            ):
                budget["blocked"] = {
                    "status": response.status,
                    "observed_at": utc_now(),
                    "path": parsed.path,
                }
                save_budget(state, budget)
                raise AdapterError(
                    "Publisher refused required access",
                    "access",
                    response.status,
                )
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            if (
                parsed.hostname in {"www.fulbright.be", "fulbright.be"}
                and response.status == 200
                and (
                    re.search(
                        rb"<title[^>]*>\s*(?:access denied|forbidden|"
                        rb"just a moment|attention required)",
                        body,
                        re.I,
                    )
                    or (
                        re.search(rb"<html\b", body, re.I)
                        and re.search(
                            rb"cf-chl-|h-captcha|g-recaptcha|"
                            rb"verify (?:that )?you are human|"
                            rb"automated access is prohibited",
                            body,
                            re.I,
                        )
                        and not re.search(
                            rb"data-elementor-type=[\"']wp-(?:post|page)[\"']",
                            body,
                            re.I,
                        )
                    )
                )
            ):
                budget["blocked"] = {
                    "status": 200,
                    "observed_at": utc_now(),
                    "path": parsed.path,
                    "reason": "Explicit access challenge or refusal",
                }
                save_budget(state, budget)
                raise AdapterError(
                    "Publisher returned an access challenge", "access", 200
                )
            return response.status, response.headers, body
    finally:
        try:
            # Hold the family lock through completion and the next-start gate.
            budget["until"] = max(
                budget["until"], time.time() + budget["interval"]
            )
            save_budget(state, budget)
        finally:
            state.close()


def fetch_source(url, min_interval=6):
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
    """Honor current and legacy product restrictions, preserving all limits."""
    parsed = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin not in _ROBOTS:
        status, _, body = request_bytes(origin + "/robots.txt")
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
        elif status == 200:
            text = body.decode("utf-8", "strict")
            if re.search(r"<html|<!doctype", text, re.I):
                raise AdapterError(
                    "Robots returned HTML instead of REP", "access"
                )
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(text.splitlines())
            _ROBOTS[origin] = parser
        else:
            raise AdapterError(
                "Robots could not be retrieved", "access", status
            )
    parser = _ROBOTS[origin]
    if parser is None:
        return 12.1
    policies = [parser]
    for entry in parser.entries:
        if entry.applies_to(_USER_AGENT):
            isolated = copy.copy(parser)
            isolated.entries = [entry]
            isolated.default_entry = None
            policies.append(isolated)
    if not all(p.can_fetch(_USER_AGENT, url) for p in policies):
        raise AdapterError("Robots disallows a required award page", "access")
    intervals = [12.1]
    for policy in policies:
        delay = policy.crawl_delay(_USER_AGENT)
        if delay is not None:
            intervals.append(delay)
        rate = policy.request_rate(_USER_AGENT)
        if rate is not None:
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
        if not re.fullmatch(
            SOURCE_ID + r"-[1-9]\d*(?:-[a-z0-9]+)*", record["id"]
        ):
            raise AdapterError(
                "Invalid stable publisher identifier", "validate"
            )
        url = urllib.parse.urlsplit(record["url"])
        if (
            url.scheme != "https"
            or url.netloc != "www.fulbright.be"
            or not url.path.startswith(
                (
                    "/grants-for-belgian-and-luxembourgish-citizens/",
                    "/grants-for-american-citizens/",
                )
            )
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
            or len(record["summary"].encode("utf-16-le")) // 2 > 600
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
    """Serialize publisher runs and retain pacing gaps across processes."""
    path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
    with open(path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            time.sleep(6)
            yield
        finally:
            time.sleep(6)
            fcntl.flock(lock, fcntl.LOCK_UN)


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            wait = _PAGE_COMPLETED + _PAGE_GAP - time.monotonic()
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


def collect():
    """Run the full non-publishing collection under the source family lock."""
    global _COLLECTION_STARTED
    with collection_lock():
        _COLLECTION_STARTED = time.monotonic()
        try:
            return collect_inventory()
        finally:
            _COLLECTION_STARTED = None


class Element:
    """Retain visible publisher text, native IDs and literal links."""

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.parent = parent
        self.children = []

    def text(self):
        if self.tag in {"script", "style", "noscript"}:
            return ""
        return " ".join(
            child.text() if isinstance(child, Element) else child
            for child in self.children
        )

    def find(self, predicate):
        result = [self] if predicate(self) else []
        for child in self.children:
            if isinstance(child, Element):
                result.extend(child.find(predicate))
        return result


class PublicHtml(html.parser.HTMLParser):
    """Parse public HTML without JavaScript, assets or external entities."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Element("root")
        self.stack = [self.root]
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
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
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def clean_text(value):
    return " ".join(html.unescape(value).split())


def one(items, name):
    if len(items) != 1:
        raise AdapterError("Missing or ambiguous publisher " + name)
    return items[0]


def own_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.fulbright.be"
        or parsed.username
        or parsed.password
        or parsed.query
    ):
        raise AdapterError("Unreviewed publisher URL", "access")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", "")
    )


def source_page(url):
    """Retrieve a complete page only after its live REP policy is checked."""
    text = fetch_source(url, min_interval=check_robots(url))
    if not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I
    ):
        raise AdapterError("Incomplete publisher HTML", "fetch")
    root = PublicHtml(text).root
    page = one(
        root.find(
            lambda n: n.attrs.get("data-elementor-type")
            in {"wp-post", "wp-page"}
        ),
        "content page",
    )
    native = page.attrs.get("data-elementor-id", "")
    if not re.fullmatch(r"[1-9]\d*", native):
        raise AdapterError("Missing stable publisher page ID")
    heading = one(page.find(lambda n: n.tag == "h1"), "page title")
    title = clean_text(heading.text())
    if not title or not clean_text(page.text()):
        raise AdapterError("Empty publisher content")
    canonical = one(
        root.find(
            lambda n: n.tag == "link" and n.attrs.get("rel") == "canonical"
        ),
        "canonical",
    )
    return {
        "native": native,
        "root": page,
        "title": title,
        "url": own_url(canonical.attrs.get("href", "")),
        "text": clean_text(page.text()),
    }


def literal_links(page):
    return list(
        dict.fromkeys(
            urllib.parse.urljoin(page["url"], n.attrs["href"])
            for n in page["root"].find(lambda n: n.tag == "a")
            if n.attrs.get("href")
        )
    )


def current_cycle(page):
    """Use the current commission banner's explicit cohort and closing year."""
    match = re.search(
        r"(20\d{2})[-–](\d{2}) Application Call open until "
        r"(\d{1,2}) ([A-Za-z]+) (20\d{2})\b",
        page["text"],
        re.I,
    )
    if not match or int(match[2]) != (int(match[1]) + 1) % 100:
        raise AdapterError("Current commission application cycle is unclear")
    date = datetime.datetime.strptime(
        " ".join(match.group(i) for i in [3, 4, 5]), "%d %B %Y"
    ).date()
    return f"{match[1]}-{match[2]}", date


def commission_deadline(page, cycle, closing):
    """Combine the dated current banner with the track's explicit CET clock."""
    text = page["text"]
    if f"Start Application ({cycle})" not in text or not re.search(
        r"December 1.*?noon.*?CET", text, re.I
    ):
        raise AdapterError("Current award deadline or CET clock changed")
    if (closing.month, closing.day) != (12, 1):
        raise AdapterError("Current banner and award closing day conflict")
    return (
        datetime.datetime.combine(
            closing,
            datetime.time(12),
            datetime.timezone(datetime.timedelta(hours=1)),
        )
        .astimezone(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def funded_record(
    page,
    identity,
    title,
    category,
    summary,
    countries,
    eligible,
    deadline=None,
    kind="programme-overview",
):
    record = make_record(
        title,
        page["url"],
        [category],
        kind=kind,
        host_countries=countries,
        method="official-commission-award",
        evidence=[
            SOURCE_URL,
            page["url"],
            "Facts from the commission's own award page; external "
            "award conditions and admission remain authoritative.",
        ],
    )
    record["id"] = SOURCE_ID + "-" + identity
    record["summary"] = summary
    record["summary_language"] = "en"
    record["eligible_countries"] = eligible
    record["deadline"] = deadline
    if deadline:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            today = (
                datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=-12))
                )
                .date()
                .isoformat()
            )
            record["status"] = "expired" if today > deadline else "open"
        else:
            record["status"] = "expired" if utc_now() > deadline else "open"
    return record


def require_terms(page, patterns):
    for pattern in patterns:
        if not re.search(pattern, page["text"], re.I):
            raise AdapterError("Required award facts changed: " + page["url"])


def outgoing_record(page, cycle, closing):
    slug = (
        urllib.parse.urlsplit(page["url"]).path.rstrip("/").rsplit("/", 1)[-1]
    )
    if slug == "graduate-studies-pre-doctoral-research":
        require_terms(
            page,
            [
                r"bachelor",
                r"Belgian or Luxembourgish citizenship",
            ],
        )
        summary = (
            "Graduate study award in the U.S. for Belgian or Luxembourgish "
            "citizens with at least a bachelor's degree. The commission lists "
            "funding, eligibility exclusions, required documents and separate "
            "host-admission conditions at the official application link."
        )
        category = "scholarships"
    elif slug == "doctoral-research":
        require_terms(
            page,
            [
                r"bachelor",
                r"doctoral research",
            ],
        )
        summary = (
            "Doctoral research award in the U.S. for Belgian or Luxembourgish "
            "citizens with at least a bachelor's degree. Consult the official "
            "page for current funding, research eligibility, host affiliation "
            "and application requirements."
        )
        category = "fellowships"
    elif slug == "post-doctoral-research":
        require_terms(
            page,
            [
                r"PhD diploma before departure",
            ],
        )
        summary = (
            "Postdoctoral research or lecturing award in the U.S. for Belgian "
            "or Luxembourgish citizens; a PhD before departure is required. "
            "The official page provides current funding, eligibility, "
            "host-affiliation and application conditions."
        )
        category = "fellowships"
    elif slug == "teachers":
        require_terms(
            page,
            [
                r"Belgian citizenship",
                r"experienced secondary school teacher",
                r"Not a U.S. citizen or permanent resident",
            ],
        )
        return funded_record(
            page,
            page["native"],
            page["title"],
            "training",
            "U.S. institute opportunity for experienced Belgian "
            "secondary-school "
            "educators. U.S. citizens and permanent residents are excluded. "
            "Consult the official page for funding, J-1 eligibility and "
            "application conditions. No year-specific closing date is given.",
            ["US"],
            ["BE"],
        )
    else:
        raise AdapterError("New outgoing award needs classification: " + slug)
    require_terms(page, [r"Belgian or Luxembourgish citizenship"])
    return funded_record(
        page,
        page["native"],
        page["title"] + " " + cycle,
        category,
        summary,
        ["US"],
        ["BE", "LU"],
        commission_deadline(page, cycle, closing),
        kind="opportunity",
    )


def incoming_record(page):
    slug = (
        urllib.parse.urlsplit(page["url"]).path.rstrip("/").rsplit("/", 1)[-1]
    )
    if slug == "students":
        require_terms(
            page,
            [
                r"at least a bachelor",
            ],
        )
        summary = (
            "Study or research grants in Belgium or Luxembourg for "
            "American students with at least a bachelor's degree. The "
            "official commission page provides current eligibility, "
            "funding, host-affiliation and application conditions. No "
            "year-specific closing date is supplied."
        )
        category, kind, eligible = "scholarships", "programme-overview", ["US"]
    elif slug == "scholars":
        require_terms(
            page,
            [
                r"holding a PhD",
                r"appropriately qualified professionals",
            ],
        )
        summary = (
            "Research or lecturing grants in Belgium or Luxembourg for "
            "American PhD holders or appropriately qualified "
            "professionals. Consult the official page for current "
            "funding, affiliation and application conditions. No "
            "year-specific closing date is supplied."
        )
        category, kind, eligible = "fellowships", "programme-overview", ["US"]
    elif slug == "eta":
        require_terms(
            page,
            [
                r"English Teaching Assistant",
                r"American students",
            ],
        )
        summary = (
            "English Teaching Assistant grants for American students in "
            "Belgium or Luxembourg. Consult the official commission page "
            "and its application links for current country-specific "
            "eligibility, funding and host conditions. No year-specific "
            "closing date is supplied."
        )
        category, kind, eligible = "fellowships", "programme-overview", ["US"]
    elif slug == "inter-country":
        require_terms(
            page,
            [
                r"rolling basis",
            ],
        )
        summary = (
            "Institutional travel support for Belgian or Luxembourgish "
            "hosts inviting a U.S. Fulbright scholar already in Europe. "
            "Applications are considered on a rolling basis. The official"
            " page explains host responsibilities, eligible visits, "
            "available funding and application conditions."
        )
        category, kind, eligible = "grants", "institutional-grant", []
    elif slug == "specialist-program":
        require_terms(
            page,
            [
                r"applications are accepted year-round",
            ],
        )
        summary = (
            "Institutional project support in Belgium or Luxembourg "
            "through the Fulbright Specialist Program. Applications are "
            "accepted year-round. Consult the official page for eligible "
            "projects, specialist matching, approvals, funding and host "
            "responsibilities."
        )
        category, kind, eligible = "grants", "institutional-grant", []
    else:
        raise AdapterError("New incoming award needs classification: " + slug)
    if eligible == ["US"]:
        require_terms(page, [r"American (?:students|scholars)"])
    return funded_record(
        page,
        page["native"],
        page["title"],
        category,
        summary,
        ["BE", "LU"],
        eligible,
        kind=kind,
    )


def partner_benefit(text):
    """Normalize only the publisher's explicit amount/tuition terms."""
    amounts = list(dict.fromkeys(re.findall(r"\$([\d,]+)", text)))
    if "Tuition Equalization" in text and amounts:
        return "Tuition equalization support worth about USD" + amounts[0]
    if "grant minimum" in text and amounts:
        return "At least USD" + amounts[0] + ", potentially near full tuition"
    percentages = re.findall(r"(\d{1,3})%", text)
    if percentages:
        percent = percentages[0]
        if not 0 < int(percent) <= 100:
            raise AdapterError("Invalid partner tuition percentage")
        if percent == "75" and amounts:
            return "About 75% tuition support (about USD" + amounts[0] + ")"
        return percent + "% tuition support"
    if re.search(r"half (?:of )?the tuition|half the tuition", text, re.I):
        return "Half-tuition support"
    if len(amounts) == 2 and "between" in text:
        return "USD" + amounts[0] + "–" + amounts[1] + " support"
    if len(amounts) == 1:
        qualifier = "About " if re.search(r"approximately\s*\$", text) else ""
        count = "Two grants of " if re.search(r"two \$", text) else ""
        return qualifier + count + "USD" + amounts[0] + " support"
    if not amounts and re.search(r"merit and need-based", text, re.I):
        return "Merit- and need-based tuition reduction; amount not stated"
    raise AdapterError("Partner funding needs review")


def partner_records(page):
    """Expand every actual LL.M. institution section, never article snippets."""
    sections, active, current = [], False, None
    for node in page["root"].find(lambda n: n.tag in {"h2", "p"}):
        if node.tag == "h2":
            title = clean_text(node.text())
            if title == "LL.M. Awards":
                active = True
                continue
            if not active:
                continue
            widget = node
            while (
                widget
                and widget.attrs.get("data-widget_type") != "heading.default"
            ):
                widget = widget.parent
            native = widget.attrs.get("data-id", "") if widget else ""
            if not re.fullmatch(r"[a-f0-9]+", native):
                raise AdapterError("Missing stable partner component ID")
            current = {"title": title, "native": native, "paragraphs": []}
            sections.append(current)
        elif active and current:
            current["paragraphs"].append(clean_text(node.text()))
    if not sections:
        raise AdapterError("No current partner award sections")
    records = []
    for section in sections:
        text = " ".join(section["paragraphs"])
        if not re.search(r"LL\.M\.?|Master of Laws", text):
            raise AdapterError("Incomplete partner degree information")
        benefit = partner_benefit(text)
        law = (
            "A first law degree from outside the U.S. is required. "
            if re.search(
                r"first degree in law.*non-U\.S\.|first degree in law.*foreign",
                text,
            )
            else "Check the university's degree/admission requirements. "
        )
        extra = ""
        if "General LL.M in Ithaca" in text:
            extra = "General LL.M. in Ithaca only. "
        if "Dispute Resolution" in text:
            extra = (
                "Dispute Resolution tracks, including International "
                "Commercial Arbitration. "
            )
        if "any of their three" in text:
            extra = "Any of the university's three LL.M. degrees. "
        summary = (
            benefit
            + " for LL.M. study at "
            + section["title"]
            + ". "
            + law
            + extra
            + "BE/LU applicants are considered through Fulbright selection; "
            "separate university admission is required. This is partner "
            "support, not a separate automatic award. Current university "
            "deadlines and conditions must be checked at the source."
        )
        records.append(
            funded_record(
                page,
                page["native"] + "-" + section["native"],
                "Fulbright LL.M. partner award — " + section["title"],
                "scholarships",
                summary,
                ["US"],
                ["BE", "LU"],
            )
        )
    return records


def boas_records(page, cycle, deadline):
    require_terms(
        page,
        [
            r"Frank Boas Scholarship for Graduate Study",
            r"Frank Boas Scholarship (?:in|for) International Legal Studies",
            r"full scholarship",
            r"partial scholarship based on financial need",
            r"bachelor",
            r"Belgian or Luxembourgish citizenship",
            r"Start Application \(" + re.escape(cycle),
        ],
    )
    common = (
        " Belgian or Luxembourgish citizens with at least a bachelor's degree "
        "may apply. Separate Harvard admission and commission selection "
        "conditions apply; consult the official page for current eligibility, "
        "funding and application requirements."
    )
    return [
        funded_record(
            page,
            page["native"] + "-graduate-study",
            "Frank Boas Scholarship for Graduate Study at Harvard " + cycle,
            "scholarships",
            "A full scholarship for graduate study at Harvard." + common,
            ["US"],
            ["BE", "LU"],
            deadline,
            "opportunity",
        ),
        funded_record(
            page,
            page["native"] + "-international-legal-studies",
            "Frank Boas Scholarship for International Legal Studies " + cycle,
            "scholarships",
            "Partial Harvard Law School support based on financial need."
            + common,
            ["US"],
            ["BE", "LU"],
            deadline,
            "opportunity",
        ),
    ]


def grant_links(page, prefix):
    links = []
    for value in literal_links(page):
        parsed = urllib.parse.urlsplit(value)
        if parsed.hostname == "www.fulbright.be" and parsed.path.startswith(
            prefix
        ):
            clean = own_url(value)
            if clean.rstrip("/") not in {
                page["url"].rstrip("/"),
                PARTNERS.rstrip("/"),
            }:
                links.append(clean)
    return list(dict.fromkeys(links))


def check_policy(page):
    """Review crawler restrictions separately from visitor privacy terms."""
    if re.search(
        r"(?:automated|scrap(?:ing|er)|crawling|data mining).{0,160}"
        r"(?:prohibit|not permitted|not allowed|prior written permission)",
        page["text"],
        re.I,
    ):
        raise AdapterError(
            "Publisher automation conditions require review", "access"
        )


def collect_inventory():
    """Refresh every own-site award, excluding delegated external catalogs."""
    _ROBOTS.clear()
    outgoing = fetch_inventory_page(source_page, SOURCE_URL)
    incoming = fetch_inventory_page(source_page, INCOMING)
    partners = fetch_inventory_page(source_page, PARTNERS)
    cycle, closing = current_cycle(outgoing)
    pages = {}
    for url in grant_links(
        outgoing, "/grants-for-belgian-and-luxembourgish-citizens/"
    ):
        page = source_page(url)
        pages[page["url"]] = page
    if not pages:
        raise AdapterError("No outgoing award details")
    records = [outgoing_record(page, cycle, closing) for page in pages.values()]
    dated = {r["deadline"] for r in records if r["deadline"]}
    if len(dated) != 1:
        raise AdapterError("Commission award clocks conflict")
    deadline = closing.isoformat()
    for url in grant_links(incoming, "/grants-for-american-citizens/"):
        page = source_page(url)
        records.append(incoming_record(page))
    records.extend(partner_records(partners))
    highlighted = grant_links(
        partners,
        "/grants-for-belgian-and-luxembourgish-citizens/"
        "fulbright-partner-awards/",
    )
    if len(highlighted) != 1:
        raise AdapterError("Partner highlighted inventory changed")
    records.extend(boas_records(source_page(highlighted[0]), cycle, deadline))
    for url in [
        WEBSITE_URL + "privacy-policy/",
        WEBSITE_URL + "cookie-policy/",
    ]:
        check_policy(source_page(url))
    refreshed = fetch_inventory_page(source_page, SOURCE_URL)
    if current_cycle(refreshed) != (cycle, closing) or set(
        grant_links(
            refreshed, "/grants-for-belgian-and-luxembourgish-citizens/"
        )
    ) != set(
        grant_links(outgoing, "/grants-for-belgian-and-luxembourgish-citizens/")
    ):
        raise AdapterError("Commission inventory changed during collection")
    validate_records(records)
    print(
        json.dumps(
            {
                "own_awards": len(records),
                "cycle": cycle,
                "delegated_catalogue": "fulbrightschuman.eu excluded",
                "mode": "complete own-site refresh",
            }
        ),
        file=sys.stderr,
    )
    return sorted(records, key=lambda record: record["id"])


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
        "inventory_scope": "complete-fulbright.be-own-awards",
        "name": "Fulbright Belgium and Luxembourg",
        "description": "Commission-published outgoing, incoming and partner "
        "award information; delegated external catalogs excluded.",
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


def workflow_api(path):
    """Read only this workflow's authenticated Actions metadata."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            "https://api.github.com/repos/YouthOpps/data-pipeline" + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 200:
            raise ValueError("Workflow API unavailable")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            "Workflow pacing state unavailable", "access"
        ) from None


def current_run_identity():
    """Bind infrastructure state to the authorized main publisher attempt."""
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "")
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    ]
    if any(not re.fullmatch(r"[1-9]\d*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))


def completed_attempt(run, expected=None, expected_head=None):
    """Validate completed chronology, including older-ID reruns."""
    try:
        identity, attempt = run["id"], run["run_attempt"]
        if (
            type(identity) is not int
            or identity <= 0
            or type(attempt) is not int
            or attempt <= 0
            or run["status"] != "completed"
            or run["path"] != ".github/workflows/fetch-" + SOURCE_ID + ".yml"
            or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_branch"] != "main"
            or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            or expected
            and (identity, attempt) != expected
            or expected_head is not None
            and run["head_sha"] != expected_head
        ):
            raise ValueError("Untrusted workflow attempt")
        times = [
            datetime.datetime.fromisoformat(run[field].replace("Z", "+00:00"))
            for field in ("run_started_at", "updated_at")
        ]
        if any(value.tzinfo is None for value in times):
            raise ValueError("Missing chronology timezone")
        began, finished = [value.timestamp() for value in times]
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        return finished, began, identity, attempt, run["head_sha"]
    except Exception:
        raise AdapterError(
            "Untrusted completed workflow attempt", "access"
        ) from None


def latest_publisher_attempt():
    """Scan the entire own-workflow history; refuse missing or racing pages."""
    current_id, current_attempt = current_run_identity()
    total = None
    seen = set()
    candidates = []
    found_current = False
    workflow = urllib.parse.quote("fetch-" + SOURCE_ID + ".yml", safe="")
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/workflows/{workflow}/runs?per_page=100&page={page}"
        )
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(runs, list)
            or len(runs) > 100
            or total is not None
            and count != total
        ):
            raise AdapterError(
                "Incomplete or changed workflow history", "access"
            )
        total = count
        for run in runs:
            identity, attempt = run.get("id"), run.get("run_attempt")
            if (
                type(identity) is not int
                or identity <= 0
                or identity in seen
                or type(attempt) is not int
                or attempt <= 0
                or run.get("repository", {}).get("full_name")
                != "YouthOpps/data-pipeline"
                or run.get("head_repository", {}).get("full_name")
                != "YouthOpps/data-pipeline"
                or run.get("path")
                != ".github/workflows/fetch-" + SOURCE_ID + ".yml"
            ):
                raise AdapterError(
                    "Invalid workflow history ownership", "access"
                )
            seen.add(identity)
            if run.get("head_branch") != "main":
                # The workflow job guard prevents source access on other refs.
                continue
            if identity == current_id:
                found_current = True
                if attempt != current_attempt or run.get(
                    "head_sha"
                ) != os.environ.get("GITHUB_SHA"):
                    raise AdapterError(
                        "Current workflow attempt mismatch", "access"
                    )
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(
                        completed_attempt(
                            prior, (identity, attempt - 1), run["head_sha"]
                        )
                    )
                continue
            if run.get("status") in {
                "queued",
                "waiting",
                "pending",
                "requested",
            }:
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(
                        completed_attempt(
                            prior, (identity, attempt - 1), run["head_sha"]
                        )
                    )
                continue
            if run.get("status") != "completed":
                raise AdapterError(
                    "Another publisher attempt is active", "access"
                )
            candidates.append(completed_attempt(run))
        if len(seen) == total:
            if not found_current:
                raise AdapterError(
                    "Current workflow missing from history", "access"
                )
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(
                    "Ambiguous latest attempt chronology", "access"
                )
            return ordered[-1] if ordered else None
        if len(seen) > total or len(runs) != 100:
            raise AdapterError("Incomplete workflow history", "access")
    raise AdapterError("Workflow history exceeds reviewed bound", "access")


def validate_publisher_budget(value):
    """Validate inert native state without executing artifact content."""

    def number(item):
        return type(item) in (int, float) and math.isfinite(item) and item >= 0

    if (
        not isinstance(value, dict)
        or not isinstance(value.get("starts"), list)
        or len(value["starts"]) > 100
        or any(not number(item) for item in value["starts"])
        or value["starts"] != sorted(value["starts"])
        or not number(value.get("interval"))
        or value["interval"] < 12
        or not number(value.get("until"))
        or not number(value.get("page_gap"))
        or value["page_gap"] < 30
    ):
        raise AdapterError("Invalid publisher pacing state", "access")
    for key in ("rate_limit_until", "rate_limit_count"):
        if key in value and not number(value[key]):
            raise AdapterError("Invalid publisher rate-limit state", "access")
    if (
        "rate_limit_count" in value
        and type(value["rate_limit_count"]) is not int
    ):
        raise AdapterError("Invalid publisher rate-limit count", "access")
    if (
        "bootstrap_pending" in value
        and type(value["bootstrap_pending"]) is not bool
    ):
        raise AdapterError("Invalid bootstrap confirmation state", "access")
    blocked = value.get("blocked")
    if blocked is not None and (
        not isinstance(blocked, dict)
        or blocked.get("status") not in {200, 401, 403}
    ):
        raise AdapterError("Invalid retained access hold", "access")
    return value


def download_publisher_artifact(identity, artifact):
    """Download one bounded ZIP with no cross-origin authorization."""
    _, _, run_id, attempt, head = identity
    try:
        status, headers, _ = request_bytes(
            "https://api.github.com/repos/YouthOpps/data-pipeline/"
            "actions/artifacts/" + str(artifact["id"]) + "/zip",
            headers={
                "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 302:
            raise ValueError("Artifact redirect missing")
        destination = headers.get("Location", "")
        parsed = urllib.parse.urlsplit(destination)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port
            or parsed.username
            or parsed.password
            or not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com")
            )
        ):
            raise ValueError("Unsafe artifact location")
        status, _, body = request_bytes(destination)
        if status != 200 or len(body) > 32768:
            raise ValueError("Invalid artifact response")
        digest = artifact.get("digest")
        if (
            digest is not None
            and digest != "sha256:" + hashlib.sha256(body).hexdigest()
        ):
            raise ValueError("Artifact digest mismatch")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if (
                len(entries) != 1
                or entries[0].filename != "pacing-state.json"
                or entries[0].file_size > 16384
                or entries[0].flag_bits & 1
                or entries[0].external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise ValueError("Invalid artifact members")
            envelope = json.loads(archive.read(entries[0]))
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("schema")) is not int
            or type(envelope.get("run_id")) is not int
            or type(envelope.get("run_attempt")) is not int
            or envelope.get("schema") != 1
            or envelope.get("repository") != "YouthOpps/data-pipeline"
            or envelope.get("source") != SOURCE_ID
            or envelope.get("run_id") != run_id
            or envelope.get("run_attempt") != attempt
            or envelope.get("head_sha") != head
        ):
            raise ValueError("Artifact attempt ownership mismatch")
        return validate_publisher_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            "Exact publisher state artifact unavailable", "access"
        ) from None


def publisher_state_path():
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    info = os.lstat(directory)
    if (
        not os.path.isdir(directory)
        or os.path.islink(directory)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    return os.path.join(directory, hashlib.sha256(b"fulbright.be").hexdigest())


def restore_publisher_state():
    """Restore exact latest attempt, or authenticate genuinely new source."""
    global _STATE_TRUSTED, _PAGE_GAP
    latest = latest_publisher_attempt()
    if latest is None:
        if (
            _REVIEWED_BOOTSTRAP.get("schema") != 1
            or _REVIEWED_BOOTSTRAP.get("source") != SOURCE_ID
            or not re.fullmatch(
                r"[0-9a-f]{64}",
                _REVIEWED_BOOTSTRAP.get("native_state_sha256", ""),
            )
        ):
            raise AdapterError(
                "Reviewed native bootstrap is unavailable", "access"
            )
        incoming = copy.deepcopy(
            validate_publisher_budget(_REVIEWED_BOOTSTRAP["state"])
        )
        incoming["until"] = max(incoming["until"], time.time() + 60)
        incoming["bootstrap_pending"] = True
    else:
        _, _, run_id, attempt, _ = latest
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or result.get("total_count") != len(artifacts)
            or len(artifacts) > 100
        ):
            raise AdapterError(
                "Incomplete publisher artifact inventory", "access"
            )
        expected = f"fulbright-publisher-state-{run_id}-{attempt}"
        exact = [item for item in artifacts if item.get("name") == expected]
        if (
            len(exact) != 1
            or exact[0].get("expired") is not False
            or type(exact[0].get("id")) is not int
            or exact[0].get("workflow_run", {}).get("id") != run_id
            or type(exact[0].get("size_in_bytes")) is not int
            or not 0 < exact[0]["size_in_bytes"] <= 32768
        ):
            raise AdapterError(
                "Latest publisher state missing or invalid", "access"
            )
        incoming = download_publisher_artifact(latest, exact[0])
    descriptor = os.open(
        publisher_state_path(), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "r+", encoding="utf-8") as state:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read()
        local = (
            validate_publisher_budget(json.loads(raw))
            if raw
            else incoming.copy()
        )
        combined = {**local, **incoming}
        combined["starts"] = sorted(set(local["starts"] + incoming["starts"]))
        for key in (
            "interval",
            "until",
            "page_gap",
            "rate_limit_until",
            "rate_limit_count",
        ):
            if key in local or key in incoming:
                combined[key] = max(local.get(key, 0), incoming.get(key, 0))
        if local.get("blocked"):
            combined["blocked"] = local["blocked"]
        save_budget(state, combined)
        _PAGE_GAP = max(_PAGE_GAP, combined["page_gap"])
    _STATE_TRUSTED = True


def confirm_publisher_bootstrap(first_publication):
    """Require absent published source before any seeded publisher request."""
    descriptor = os.open(publisher_state_path(), os.O_RDWR | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "r+", encoding="utf-8") as state:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        budget = validate_publisher_budget(json.load(state))
        if budget.get("bootstrap_pending"):
            if not first_publication:
                raise AdapterError(
                    "Existing source cannot bootstrap publisher history",
                    "access",
                )
            budget["bootstrap_pending"] = False
            save_budget(state, budget)


def export_publisher_state():
    """Export only inert infrastructure state after every trusted outcome."""
    if not _STATE_TRUSTED:
        return
    run_id, attempt = current_run_identity()
    path = os.environ.get("FULBRIGHT_PACING_ARTIFACT_PATH", "")
    root = os.environ.get("RUNNER_TEMP", "")
    if (
        not root
        or not os.path.isabs(root)
        or os.path.dirname(os.path.abspath(path)) != os.path.realpath(root)
    ):
        raise AdapterError("Unsafe publisher artifact path", "access")
    descriptor = os.open(publisher_state_path(), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, encoding="utf-8") as state:
        fcntl.flock(state.fileno(), fcntl.LOCK_SH)
        budget = validate_publisher_budget(json.load(state))
    envelope = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "source": SOURCE_ID,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": os.environ.get("GITHUB_SHA"),
        "state": budget,
    }
    if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
        raise AdapterError("Invalid current workflow revision", "access")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(envelope, target)


def main():
    """Collect, validate and optionally publish one source outcome safely."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source "
        "using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    if args.publish and os.environ.get("GITHUB_ACTIONS") == "true":
        restore_publisher_state()
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
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    if args.publish and os.environ.get("GITHUB_ACTIONS") == "true":
        confirm_publisher_bootstrap(snapshot["files"]["data.json"] is None)
    if args.publish and snapshot["files"]["data.json"] is not None:
        directory = os.path.join(
            tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
        )
        publisher_state = os.path.join(
            directory, hashlib.sha256(b"fulbright.be").hexdigest()
        )
        if not os.path.isfile(publisher_state):
            raise AdapterError(
                "Published source requires its retained publisher budget; "
                "restore the exact workflow artifact instead of "
                "resetting access state",
                "access",
            )
    attempt, failure = utc_now(), None
    try:
        records = collect()
        validate_records(records)
        checked = utc_now()
        records = remove_expired_records(records, checked)
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
                created_at=before.get("created_at", checked),
                first_seen_at=before.get("first_seen_at", checked),
                updated_at=(
                    before.get("updated_at", checked) if same else checked
                ),
                last_seen_at=checked,
                last_checked_at=checked,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
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
    finally:
        export_publisher_state()
