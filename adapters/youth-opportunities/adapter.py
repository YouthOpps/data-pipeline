"""Collect the complete publisher-maintained Youth Opportunities browse inventory."""

import argparse
import base64
import contextlib
import datetime
import email.utils
import fcntl
import hashlib
import html
import html.parser
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

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
_USER_AGENT = "YouthOpp/1.0 (+https://github.com/YouthOpps/data-pipeline)"
_ROBOTS_CACHE = {}


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


def utc_now():
    return (datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="milliseconds").replace("+00:00", "Z"))


def safe_error(error):
    """Return a bounded actionable error without URL queries or credentials."""
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


def budget_path(host):
    key = hashlib.sha256((SOURCE_ID + "|" + host).encode()).hexdigest()[:24]
    return os.path.join(tempfile.gettempdir(), "youthopps-" + key + ".budget")


@contextlib.contextmanager
def budget_state(host):
    """Keep request starts and backoff shared across local processes."""
    with open(budget_path(host), "a+", encoding="utf-8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        try:
            file.seek(0)
            text = file.read()
            state = (json.loads(text) if text else {
                "starts": [],
                "interval": 6,
                "until": 0
            })
            yield state
            file.seek(0)
            file.truncate()
            json.dump(state, file)
            file.flush()
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def pace(url, interval=6):
    """Reserve a shared request start and return its normalized budget host."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    host = host.removeprefix("www.")
    while True:
        with budget_state(host) as state:
            now = time.time()
            starts = [start for start in state["starts"] if now - start < 60]
            state["starts"] = starts
            # Retain margin between reservation and actual HTTP call starts.
            state["interval"] = max(6.1, interval, state["interval"])
            ready = max(
                state["until"],
                starts[-1] + state["interval"] if starts else now,
                starts[0] + 60 if len(starts) >= 10 else now,
            )
            if ready <= now:
                state["starts"].append(now)
                return {"host": host}
            wait = ready - now
        time.sleep(wait)


def request_bytes(url, *, interval=6, method="GET", headers=None, payload=None):
    """Return HTTP status, headers and bounded body bytes under shared pacing.

    The optional payload is bytes; interval specifies minimum start spacing.
    Redirects are returned to the caller rather than followed implicitly.
    """
    budget = pace(url, interval)
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "User-Agent": _USER_AGENT,
            **(headers or {})
        },
    )
    try:
        response = _OPENER.open(request, timeout=90)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        retry = response.headers.get("Retry-After")
        if retry:
            try:
                seconds = (
                    float(retry) if re.fullmatch(r"\d+(?:\.\d+)?", retry) else
                    email.utils.parsedate_to_datetime(retry).timestamp() -
                    time.time())
                with budget_state(budget["host"]) as state:
                    state["until"] = max(state["until"],
                                         time.time() + max(0, seconds))
            except (ValueError, TypeError, OverflowError):
                pass
        body = response.read(5_000_001)
        if len(body) > 5_000_000:
            raise AdapterError("Response exceeds 5 MB", "fetch")
        return response.status, response.headers, body


def fetch_source(url, min_interval=6, *, with_headers=False):
    """Read a reviewed HTTPS source with checked, paced redirects and retries.

    Return decoded text, or (decoded text, HTTP headers) with with_headers=True.
    Raise AdapterError when permitted retrieval cannot complete.
    """
    original = urllib.parse.urlsplit(url)

    def checked(value):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if (parsed.scheme != "https" or parsed.username or parsed.password or
                host != (original.hostname or "").lower().removeprefix("www.")
                or parsed.port != original.port):
            raise AdapterError("Source redirect leaves the reviewed HTTPS host",
                               "fetch")
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
                status, headers, body = request_bytes(current,
                                                      interval=min_interval)
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch")
                    current = urllib.parse.urljoin(current, headers["Location"])
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status in (429, 503) and attempt < 2:
                    with budget_state((original.hostname or
                                       "").removeprefix("www.")) as state:
                        state["until"] = max(state["until"], time.time() + 120)
                    break
                if status != 200:
                    raise AdapterError(f"Publisher returned HTTP {status}",
                                       "fetch", status)
                text = body.decode(headers.get_content_charset() or "utf-8")
                return (text, headers) if with_headers else text
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch") from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    """Check endpoint access against current robots and return start spacing."""
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    if robots_url not in _ROBOTS_CACHE:
        try:
            _ROBOTS_CACHE[robots_url] = fetch_source(robots_url)
        except AdapterError as error:
            if error.status == 404:
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
    if (directives and not any(g["agents"] for g in groups) and
            not all(line.startswith("sitemap:") for line in directives)):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    specific = [g for g in groups if "youthopp" in g["agents"]]
    selected = specific or [g for g in groups if "*" in g["agents"]]
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    matches = []
    for g in selected:
        for directive, pattern in g["rules"]:
            if not pattern:
                continue
            expression = ("^" + ".*".join(
                re.escape(part)
                for part in pattern.removesuffix("$").split("*")) +
                          ("$" if pattern.endswith("$") else ""))
            if re.search(expression, path):
                matches.append((
                    len(pattern.replace("*", "").removesuffix("$")),
                    directive == "allow",
                ))
    if matches and not max(matches)[1]:
        raise AdapterError("Robots policy disallows this source endpoint",
                           "access")
    return max([6] + [g["delay"] for g in selected])


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


def validate_records(records, *, allow_empty=False):
    """Validate every record, stable ID, source, date, category and provenance.

    Allow empty lists only for existing snapshots and validated expiry cleanup.
    Fresh collection must produce genuine nonempty records.
    """
    if not isinstance(records, list) or (not records and not allow_empty):
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
        if record["id"] in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"])
        if not re.fullmatch(SOURCE_ID + r":\d+", record["id"]):
            raise AdapterError("Invalid stable publisher identifier",
                               "validate")
        url = urllib.parse.urlsplit(record["url"])
        if url.scheme != "https" or url.netloc != "www.youthop.com":
            raise AdapterError("Unexpected publisher record URL", "validate")
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
                    if (datetime.datetime.fromisoformat(
                            value.replace("Z", "+00:00")).tzinfo is None):
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
        if (not isinstance(record.get("summary"), str) or
                len(record["summary"]) > 600 or
                re.search("<[^>]+>", record["summary"])):
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


def collect():
    """Retrieve the full supported inventory under the shared collection lock."""
    with collection_lock():
        return collect_inventory()


class BrowsePage(html.parser.HTMLParser):
    """Read only opportunity cards and the publisher's pagination controls."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self.pages = {1}
        self.current = None
        self.page_number = None
        self.advertisements = 0
        self.hidden = 0
        self.feed(document)
        self.close()
        if self.current is not None or not self.entries:
            raise AdapterError("Empty or incomplete publisher browse page")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("script", "style"):
            self.hidden += 1
        if tag == "ins" and self.current is not None and "adsbygoogle" in attrs.get(
                "class", "").split():
            self.current["advertisement"] = True
        if tag == "article":
            if self.current is not None:
                raise AdapterError("Nested publisher opportunity cards")
            match = re.fullmatch(r"post-(\d+)", attrs.get("id", ""))
            if attrs.get("id") and not match:
                raise AdapterError("Malformed publisher browse identifier")
            if "post-item" not in attrs.get("class", "").split():
                raise AdapterError("Unexpected publisher browse article")
            self.current = {
                "id": int(match[1]) if match else None,
                "url": None,
                "advertisement": False,
                "text": []
            }
        elif tag == "span" and "page-numbers" in attrs.get("class", "").split():
            self.page_number = []
        elif tag == "a":
            href = attrs.get("href", "")
            if self.current is not None and attrs.get("itemprop") == "url":
                self.current["url"] = href
            if "page-numbers" in attrs.get("class", "").split():
                parsed = urllib.parse.urlsplit(href)
                query = urllib.parse.parse_qs(parsed.query)
                try:
                    if set(query) - {"sf_paged"}:
                        raise ValueError()
                    page = int(query.get("sf_paged", ["1"])[0])
                except (KeyError, ValueError, IndexError) as error:
                    raise AdapterError(
                        "Invalid browse pagination link") from error
                if (parsed.scheme != "https" or
                        parsed.netloc != "www.youthop.com" or
                        parsed.path != "/search/" or page < 1):
                    raise AdapterError("Unexpected browse pagination route")
                self.pages.add(page)

    def handle_data(self, value):
        if self.current is not None and not self.hidden and value.strip():
            self.current["text"].append(value.strip())
        if self.page_number is not None:
            self.page_number.append(value)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        if tag == "span" and self.page_number is not None:
            text = "".join(self.page_number).strip()
            if text.isdecimal():
                self.pages.add(int(text))
            self.page_number = None
        if tag == "article":
            if not self.current:
                raise AdapterError("Unmatched publisher browse article")
            if (self.current["id"] is None and self.current["advertisement"] and
                    not self.current["url"] and not self.current["text"]):
                self.advertisements += 1
            elif self.current["id"] is None or not self.current["url"]:
                raise AdapterError("Browse card has no original post ID or URL")
            else:
                self.entries.append(
                    {key: self.current[key] for key in ("id", "url")})
            self.current = None


class Content(html.parser.HTMLParser):
    """Read actual article text and semantic headings, ignoring executable code."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.headings = []
        self.heading = None
        self.skip = 0
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in ("h1", "h2", "h3", "h4") and not self.skip:
            self.heading = []

    def handle_endtag(self, tag):
        if tag in ("h1", "h2", "h3", "h4") and self.heading is not None:
            self.headings.append(" ".join(" ".join(self.heading).split()))
            self.heading = None
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)
            if self.heading is not None:
                self.heading.append(value)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


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


def publication_date(value):
    """Convert an explicitly GMT WordPress publication date to UTC ISO-8601."""
    try:
        parsed = datetime.datetime.fromisoformat(value)
        if parsed.tzinfo is not None:
            raise ValueError()
        return parsed.replace(tzinfo=datetime.timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
    except (TypeError, ValueError) as error:
        raise AdapterError("Invalid publisher GMT publication date") from error


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
        parsed = datetime.datetime.fromtimestamp(int(value),
                                                 datetime.timezone.utc)
        if not 2000 <= parsed.year <= 2100:
            raise ValueError()
        return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError, OSError) as error:
        raise AdapterError("Invalid publisher Unix deadline") from error


def normalize_entry(entry, card, taxonomy):
    """Cross-check genuine full post content against its card and normalize it."""
    if (not isinstance(entry, dict) or entry.get("id") != card["id"] or
            entry.get("status") != "publish" or entry.get("type") != "post" or
            entry.get("link") != card["url"]):
        raise AdapterError("Publisher detail does not match its browse card")
    parsed = urllib.parse.urlsplit(entry["link"])
    if (parsed.scheme != "https" or parsed.netloc != "www.youthop.com" or
            parsed.query or parsed.fragment):
        raise AdapterError("Unexpected original publisher article URL")
    try:
        title = _feed_text(entry["title"]["rendered"])
        content = Content(entry["content"]["rendered"])
        tags = [taxonomy[key] for key in entry["categories"]]
    except (KeyError, TypeError) as error:
        raise AdapterError("Incomplete publisher opportunity detail") from error
    if not title or not content.text:
        raise AdapterError(
            "Publisher opportunity has no genuine article content")
    # Genuine calls need material eligibility/application evidence from their
    # own article, rather than a title or generic promotional boilerplate.
    if not re.search(
            r"\b(?:eligibilit(?:y|ies)|eligible|applicants?|"
            r"participants?|apply|application)\b", content.text, re.I):
        raise AdapterError("Unresolved non-opportunity browse article: " +
                           entry["link"])
    categories = list(
        dict.fromkeys(
            _TAXONOMY[tag] for tag in tags if tag in _TAXONOMY)) or ["other"]
    record = make_record(
        title,
        entry["link"],
        categories,
        published_at=publication_date(entry["date_gmt"]),
        tags=tags,
        method="publisher-browse-taxonomy-and-article",
        evidence=[entry["link"], SOURCE_URL],
    )
    record["id"] = SOURCE_ID + ":" + str(entry["id"])
    record["deadline"] = deadline_value(entry)
    record["status"] = ("open" if record["deadline"] >= utc_now() else
                        "expired") if record["deadline"] else "unknown"
    record["classification"]["status"] = "classified"
    location = entry.get("location")
    if location is not None:
        if not isinstance(location, str):
            raise AdapterError("Invalid publisher location metadata")
        record["location"] = _feed_text(location) or None
    regions = re.search(
        r"\bEligible Regions\s*:\s*(.*?)"
        r"(?:\bApplication Process\b|\bFurther Queries\b|$)", content.text,
        re.I)
    if regions and regions[1].strip():
        record["summary"] = "Publisher eligible regions: " + regions[1].strip(
        )[:500]
    return record


def collect_inventory():
    """Traverse the complete publisher-maintained Browse Opportunities index."""
    _ROBOTS_CACHE.clear()
    root = WEBSITE_URL + "wp-json/wp/v2/"
    taxonomy = {}
    category_page = 1
    expected_categories = None
    while True:
        entries, headers = read_json(
            root + f"categories?per_page=100&page={category_page}")
        try:
            total, pages = int(headers["X-WP-Total"]), int(
                headers["X-WP-TotalPages"])
            if expected_categories is None:
                expected_categories = (total, pages)
            if expected_categories != (total, pages):
                raise ValueError()
            for entry in entries:
                if entry["id"] in taxonomy:
                    raise ValueError()
                taxonomy[entry["id"]] = html.unescape(entry["name"])
        except (KeyError, TypeError, ValueError) as error:
            raise AdapterError(
                "Invalid or changing publisher taxonomy") from error
        if category_page == pages:
            break
        category_page += 1
    if len(taxonomy) != total:
        raise AdapterError("Incomplete publisher category inventory")
    cards = []
    advertisements = 0
    seen = set()
    page = 1
    expected_pages = expected_size = None
    while True:
        url = SOURCE_URL + (f"?sf_paged={page}" if page > 1 else "")
        listing = BrowsePage(fetch_source(url, check_robots(url)))
        pages = max(listing.pages)
        advertisements += listing.advertisements
        if expected_pages is None:
            expected_pages, expected_size = pages, len(listing.entries)
        if pages != expected_pages:
            raise AdapterError(
                "Publisher browse pagination changed during retrieval")
        if len(listing.entries) > expected_size or page < pages and len(
                listing.entries) != expected_size:
            raise AdapterError("Incomplete publisher browse page")
        for card in listing.entries:
            if card["id"] in seen:
                raise AdapterError("Duplicate publisher browse identifier")
            seen.add(card["id"])
            cards.append(card)
        print(f"Browse page {page}/{pages}: {len(cards)} opportunities",
              file=sys.stderr)
        if page == pages:
            break
        page += 1
    # Detect shifts while collecting the paginated index before detail retrieval.
    first = BrowsePage(fetch_source(SOURCE_URL, check_robots(SOURCE_URL)))
    if first.entries != cards[:expected_size] or max(
            first.pages) != expected_pages:
        raise AdapterError("Publisher browse index changed during pagination")
    records = []
    for index, card in enumerate(cards, 1):
        entry, _ = read_json(root + "posts/" + str(card["id"]))
        records.append(normalize_entry(entry, card, taxonomy))
        if index % 25 == 0 or index == len(cards):
            print(f"Validated {index}/{len(cards)} browse opportunity details",
                  file=sys.stderr)
    print(json.dumps({
        "inventory_scope": "complete-publisher-browse-opportunities",
        "pages": expected_pages,
        "inventory_posts": len(cards),
        "excluded_advertisements": advertisements,
        "retained": len(records)
    }),
          file=sys.stderr)
    validate_records(records)
    return records


def github(method, path, payload=None, missing_ok=False):
    """Call only the configured publication repository with scoped credentials.

    Return the decoded JSON response, or None for an allowed missing resource.
    The optional payload is a JSON-serializable object.
    """
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
            raise AdapterError("Unsupported existing source file encoding",
                               "publish")
        files[name] = (None if file is None else base64.b64decode(
            file["content"]).decode("utf-8"))
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish")
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
        "last_success_at":
            (previous.get("last_success_at") if error else attempt),
        "last_checked_at":
            (previous.get("last_checked_at") if error else attempt),
        "record_count": len(records),
        "inventory_scope": "complete-publisher-browse-opportunities",
        "message": ("Collection failed; last-good data preserved" if error else
                    f"Validated refresh retains {len(records)} records"),
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
        raise AdapterError("Cleanup requires an aware check timestamp",
                           "validate")
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing = datetime.datetime.fromisoformat(deadline).date()
            latest_date = checked.astimezone(
                datetime.timezone(datetime.timedelta(hours=-12))).date()
            expired = latest_date > closing
        elif deadline:
            closing = datetime.datetime.fromisoformat(
                deadline.replace("Z", "+00:00"))
            if closing.tzinfo is not None:
                expired = checked > closing
        if not expired:
            retained.append(record)
    validate_records(retained, allow_empty=True)
    return retained


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
    snapshot = read_snapshot() if args.publish else None
    old = (json.loads(snapshot["files"]["data.json"])
           if snapshot and snapshot["files"]["data.json"] is not None else [])
    previous = (json.loads(snapshot["files"]["metadata.json"]) if snapshot and
                snapshot["files"]["metadata.json"] is not None else {})
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish")
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
                updated_at=(before.get("updated_at", attempt)
                            if same else attempt),
                last_seen_at=attempt,
                last_checked_at=attempt,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(key=lambda record: record["published_at"] or "",
                     reverse=True)
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
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
            ))
        sys.exit(1)
