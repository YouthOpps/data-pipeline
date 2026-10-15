"""Collect Youth for Europe's complete native public opportunity inventory."""

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

SOURCE_ID = "youth-for-europe"
SOURCE_URL = "https://youthforeurope.eu/explore/?type=general&sort=latest"
WEBSITE_URL = "https://youthforeurope.eu/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = None
PUBLISHER_TYPE = "aggregator"
ATTRIBUTION = "Youth for Europe / Associazione Joint"

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
    return max(
        [6] + [g["delay"] for policy in policies for g in policy]
    )


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
        if record.get("attribution") != ATTRIBUTION:
            raise AdapterError("Missing publisher attribution", "validate")
        if not re.fullmatch(SOURCE_ID + r"-\d+", record["id"]):
            raise AdapterError("Invalid stable publisher identifier",
                               "validate")
        url = urllib.parse.urlsplit(record["url"])
        if (url.scheme != "https" or url.netloc != "youthforeurope.eu" or
                not url.path.startswith("/project/")):
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
                    if (field == "deadline" and
                            re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                        datetime.date.fromisoformat(value)
                    elif (datetime.datetime.fromisoformat(
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
    """Retrieve the full inventory under the shared collection lock."""
    with collection_lock():
        return collect_inventory()


class Element:
    """Minimal public HTML tree retaining source text and attributes."""

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []

    def text(self):
        return " ".join(
            child.text() if isinstance(child, Element) else child
            for child in self.children)

    def find(self, predicate):
        result = [self] if predicate(self) else []
        for child in self.children:
            if isinstance(child, Element):
                result.extend(child.find(predicate))
        return result


class PublicHtml(html.parser.HTMLParser):
    """Parse publisher markup without executing JavaScript or loading assets."""

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Element("root")
        self.stack = [self.root]
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source",
                       "track", "wbr"):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def clean_text(value):
    return " ".join(html.unescape(value).split())


def source_text(url):
    """Fetch only after the current robots policy permits the endpoint."""
    return fetch_source(url, check_robots(url))


def explore_page(kind="general", page=1):
    """Read a native server-rendered browse page and its inventory counts."""
    query = urllib.parse.urlencode({"type": kind, "sort": "latest", "pg": page})
    url = WEBSITE_URL + "explore/?" + query
    tree = PublicHtml(source_text(url)).root
    scripts = tree.find(
        lambda node: node.tag == "script" and
        node.attrs.get("id") == "case27-explore-configuration")
    if len(scripts) != 1:
        raise AdapterError("Native opportunity inventory is unavailable")
    config = json.loads(scripts[0].text())
    types = config.get("ListingTypes", {})
    if (config.get("ActiveListingType") != kind or kind not in types or
            types[kind].get("filters", {}).get("page") != page - 1):
        raise AdapterError("Publisher did not honor native browse pagination")
    if kind == "general" and types[kind].get("is_global") is not True:
        raise AdapterError(
            "Native browse no longer covers all opportunity types")
    result = types[kind].get("prefetched_results")
    if not isinstance(result, dict):
        raise AdapterError("Native browse did not provide complete results")
    for field in ("found_posts", "max_num_pages", "page_post_count",
                  "posts_per_page"):
        if type(result.get(field)) is not int or result[field] < 0:
            raise AdapterError("Missing or invalid native inventory count")
    if not result["posts_per_page"]:
        raise AdapterError("Invalid native inventory page size")
    cards = PublicHtml(result.get("html", "")).root.find(
        lambda node: "listing-preview" in node.attrs.get("class", "").split())
    if len(cards) != result["page_post_count"]:
        raise AdapterError("Native inventory card count mismatch")
    parsed = {}
    for card in cards:
        match = re.fullmatch(r"listing-id-(\d+)", card.attrs.get("data-id", ""))
        titles = card.find(lambda node: "listing-preview-title" in
                           node.attrs.get("class", "").split())
        links = card.find(lambda node: node.tag == "a" and
                          node.attrs.get("href", "").startswith(
                              WEBSITE_URL + "project/"))
        if not match or len(titles) != 1 or not links:
            raise AdapterError("Incomplete native opportunity card")
        identifier = match[1]
        title = clean_text(titles[0].text())
        url = links[0].attrs["href"]
        kinds = [value[5:] for value in card.attrs.get("class", "").split()
                 if value.startswith("type-")]
        if (identifier in parsed or not title or len(kinds) != 1 or
                any(link.attrs["href"] != url for link in links)):
            raise AdapterError(
                "Duplicate or inconsistent native opportunity card")
        parsed[identifier] = {"title": title, "url": url, "type": kinds[0]}
    return config, result, parsed


def browse_inventory(kind="general"):
    """Collect every counted page, rejecting missing or changing inventories."""
    config, first, cards = explore_page(kind)
    total, pages = first["found_posts"], first["max_num_pages"]
    page_size = first["posts_per_page"]
    expected_pages = (total + page_size - 1) // page_size
    if pages != expected_pages:
        raise AdapterError("Inconsistent native inventory pagination totals")
    for page in range(2, pages + 1):
        _, result, more = explore_page(kind, page)
        if (result["found_posts"] != total or
                result["max_num_pages"] != pages or
                result["posts_per_page"] != first["posts_per_page"] or
                set(cards).intersection(more)):
            raise AdapterError("Publisher inventory changed during pagination")
        cards.update(more)
    if len(cards) != total:
        raise AdapterError("Incomplete native opportunity inventory")
    return config, cards


def detail_record(identifier, card):
    """Normalize factual labeled fields from the actual publisher detail."""
    tree = PublicHtml(source_text(card["url"])).root
    bodies = tree.find(lambda node: node.tag == "body")
    if (len(bodies) != 1 or "postid-" + identifier not in
            bodies[0].attrs.get("class", "").split() or
            "type-" + card["type"] not in
            bodies[0].attrs.get("class", "").split()):
        raise AdapterError("Opportunity detail identity/type mismatch")
    headings = tree.find(lambda node: node.tag == "h1" and
                         "case27-primary-text" in
                         node.attrs.get("class", "").split())
    if len(headings) != 1 or clean_text(headings[0].text()) != card["title"]:
        raise AdapterError("Opportunity detail title differs from native card")
    links = tree.find(lambda node: node.tag == "link" and
                     node.attrs.get("rel") == "canonical")
    if len(links) != 1 or links[0].attrs.get("href") != card["url"]:
        raise AdapterError("Opportunity canonical URL changed")
    blocks = tree.find(lambda node: any(
        value.startswith("block-field-")
        for value in node.attrs.get("class", "").split()))
    fields = {}
    for block in blocks:
        names = [value.removeprefix("block-field-") for value in
                 block.attrs.get("class", "").split()
                 if value.startswith("block-field-")]
        for name in names:
            if name in fields:
                if name == "job_description":
                    continue
                raise AdapterError("Duplicate labeled opportunity field")
            fields[name] = block
    if "job_description" not in fields:
        raise AdapterError("Opportunity detail has no description field")
    categories = {
        "evs-project": "volunteering",
        "volunteering-try": "volunteering",
        "stage": "internships",
        "youth-exchange": "training",
        "training-course": "training",
        "job": "jobs",
        "au-pair": "jobs",
    }
    category = categories.get(card["type"], "other")
    record = make_record(card["title"], card["url"], [category],
                         method="publisher-native-listing-type",
                         evidence=["Native listing type: " + card["type"],
                                   "Publisher detail: " + card["url"]])
    record["id"] = SOURCE_ID + "-" + identifier
    scripts = tree.find(lambda node: node.tag == "script" and
                        node.attrs.get("type") == "application/ld+json")
    pages = []
    for script in scripts:
        data = json.loads(script.text())
        pages.extend(value for value in data.get("@graph", [])
                     if value.get("@type") == "WebPage" and
                     value.get("url") == card["url"])
    if len(pages) != 1:
        raise AdapterError("Missing publisher detail date provenance")
    record["published_at"] = pages[0].get("datePublished")
    closing = fields.get("application-closing-date")
    if closing:
        dates = closing.find(lambda node: node.tag == "span")
        values = list(dict.fromkeys(clean_text(node.text()) for node in dates
                                   if clean_text(node.text())))
        # Multiple dates/ranges or unzoned times are ambiguous, not expired.
        if len(values) == 1 and re.fullmatch(r"\d{2}/\d{2}/\d{4}", values[0]):
            try:
                date = datetime.datetime.strptime(values[0], "%d/%m/%Y").date()
            except ValueError as error:
                raise AdapterError(
                    "Invalid publisher application closing date") from error
            record["deadline"] = date.isoformat()
            record["classification"]["evidence"].append(
                "Application Closing Date (day/month/year): " + values[0])
    location = fields.get("job_location")
    if location:
        addresses = location.find(lambda node: node.tag == "p")
        values = list(dict.fromkeys(
            clean_text(node.text()) for node in addresses
            if clean_text(node.text())))
        if values:
            record["location"] = "; ".join(values)
            # Match explicit country names in labeled locations only.
            countries = {
                "india": "IN", "indonesia": "ID", "greece": "GR",
                "united kingdom": "GB", "polynésie française": "PF",
                "french polynesia": "PF", "italy": "IT", "france": "FR",
                "germany": "DE", "spain": "ES", "portugal": "PT",
            }
            if not re.search(r"\bremote\b", card["title"], re.I):
                record["host_countries"] = sorted({
                    code for address in values
                    for country, code in countries.items()
                    if re.search(r"(?:^|,)\s*" + re.escape(country) + r"\s*$",
                                 address, re.I)})
    profile = fields.get("volunteer-profile") or fields.get("job-profile")
    if profile and re.search(
            r"Volunteers must be:\s*-18\+\s*-EU Citizen\s*-",
            clean_text(profile.text()), re.I):
        record["eligible_countries"] = [
            "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
            "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
            "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"]
        record["classification"]["evidence"].append(
            "Volunteer Profile explicitly requires EU Citizen (EU-27)")
    return record


def collect_inventory():
    """Discover every counted native global opportunity and genuine detail."""
    _, cards = browse_inventory()
    if not cards:
        raise AdapterError("Publisher returned an empty native inventory",
                           "validate")
    records = [detail_record(identifier, card)
               for identifier, card in cards.items()]
    validate_records(records)
    return sorted(records, key=lambda record: record["published_at"] or "",
                  reverse=True)


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
