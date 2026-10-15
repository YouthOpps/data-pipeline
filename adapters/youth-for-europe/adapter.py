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
import io
import ipaddress
import json
import math
import os
import re
import signal
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

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


_PAGE_COMPLETED = None


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


def fetch_source(url, min_interval=12.2, *, with_headers=False):
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
                    robots=original.path == "/robots.txt",
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(current, headers["Location"])
                    if (
                        original.path == "/robots.txt"
                        and urllib.parse.urlsplit(current).path != "/robots.txt"
                    ):
                        raise AdapterError(
                            "Robots redirect leaves literal robots.txt",
                            "access",
                        )
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status == 503 and attempt < 2:
                    with budget_state(
                        (original.hostname or "").removeprefix("www.")
                    ) as state:
                        state["until"] = max(state["until"], time.time() + 120)
                    break
                if (
                    original.path == "/robots.txt"
                    and 400 <= status < 500
                    and status != 429
                ):
                    return ("", headers) if with_headers else ""
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status}", "fetch", status
                    )
                text = body.decode(headers.get_content_charset() or "utf-8")
                return (text, headers) if with_headers else text
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error), "fetch"
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
            if error.status == 404:
                _ROBOTS_CACHE[robots_url] = ""
            else:
                raise
    text = _ROBOTS_CACHE[robots_url]
    groups, group = [], {"agents": [], "rules": [], "delay": 0, "rate": 0}
    for line in text.splitlines():
        key, separator, value = line.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"] or group["rate"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0, "rate": 0}
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
        elif key == "request-rate":
            match = re.fullmatch(r"([1-9][0-9]*)\s*/\s*([1-9][0-9]*)", value)
            if not match:
                raise AdapterError("Unsupported robots request rate", "access")
            group["rate"] = int(match[2]) / int(match[1])
            if not numeric(group["rate"]):
                raise AdapterError("Invalid robots request rate", "access")
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
            retain_refusal()
            raise AdapterError(
                "Robots policy disallows this source endpoint", "access"
            )
    return max(
        [12.2]
        + [max(g["delay"], g["rate"]) for policy in policies for g in policy]
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
    tree = PublicHtml(fetch_inventory_page(source_text, url)).root
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


def browse_inventory(kind="general", *, closing=False):
    """Collect every counted page, rejecting missing or changing inventories."""
    config, first, cards = explore_page(kind)
    total, pages = first["found_posts"], first["max_num_pages"]
    page_size = first["posts_per_page"]
    expected_pages = (total + page_size - 1) // page_size
    check_inventory_budget(
        max(pages - 1, 0) + (0 if closing else pages), 0 if closing else total
    )
    if pages != expected_pages:
        raise AdapterError("Inconsistent native inventory pagination totals")
    for page in range(2, pages + 1):
        _, result, more = explore_page(kind, page)
        if (
            result["found_posts"] != total
            or result["max_num_pages"] != pages
            or result["posts_per_page"] != first["posts_per_page"]
            or set(cards).intersection(more)
        ):
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
    config, cards = browse_inventory()
    if not cards:
        raise AdapterError(
            "Publisher returned an empty native inventory", "validate"
        )
    page_count = config["ListingTypes"]["general"]["prefetched_results"][
        "max_num_pages"
    ]
    check_inventory_budget(page_count, len(cards))
    records = [
        detail_record(identifier, card) for identifier, card in cards.items()
    ]
    _, closing = browse_inventory(closing=True)
    if closing != cards:
        raise AdapterError("Closing complete native inventory changed", "parse")
    validate_records(records)
    return sorted(
        records, key=lambda record: record["published_at"] or "", reverse=True
    )


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
            github_error_context(method, path)
            + " connection failed; publication is unconfirmed",
            "publish",
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(
            f"{github_error_context(method, path)} returned HTTP {status}",
            "publish",
            status,
        )
    return json.loads(body)


def read_snapshot_body():
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


def publish_files_body(snapshot, files):
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
        except AdapterError as error:
            try:
                latest = read_snapshot()
            except Exception as verification:
                detail = (
                    safe_error(verification)
                    if isinstance(verification, AdapterError)
                    else type(verification).__name__
                )
                raise AdapterError(
                    f"Publication attempt {attempt + 1}/3 failed: "
                    f"{safe_error(error)}; outcome verification failed: {detail}",
                    "publish",
                    getattr(verification, "status", None),
                ) from verification
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
                    f"Publication failed after {attempt + 1} attempts; "
                    f"last failure: {safe_error(error)}",
                    "publish",
                    error.status,
                ) from error


def metadata(attempt, previous, records, error=None):
    """Build attributed run metadata, retaining the last success on failure."""
    result = {
        "display_name": "Associazione Joint",
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


def run():
    """Collect, validate and optionally publish one source outcome safely."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source "
        "using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    global _PUBLISHING
    _PUBLISHING = args.publish
    if args.publish:
        with phase("restore", 180):
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
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
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
                updated_at=(
                    before.get("updated_at", attempt) if same else attempt
                ),
                last_seen_at=attempt,
                last_checked_at=attempt,
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


FAMILY = "youthopps-youth-for-europe-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_ARTIFACT_NAME = "yfe-pacing-state"
COLLECTION_TIMEOUT = 1500
RUN_TIMEOUT = 2700
EXPORT_TIMEOUT = 30
_STATE_PATH = _STATE_LOCK = None
_JOURNAL_NEW = False
_COLLECTION_LOCK_FD = None
_RESTORED = _STATE_TRUSTED = _PUBLISHING = False
_RUN_END = _PHASE_END = _PHASE_NAME = None
_SCANNED_HISTORY = {}
_REVIEWED_NATIVE_MIGRATION = {
    "schema": 1,
    "source": "youth-for-europe",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/"
        "issues/201#issuecomment-6195962246"
    ),
    "state": {
        "schema": 1,
        "family": "youthopps-youth-for-europe-publisher-v1",
        "starts": [
            1792010268.781352,
            1792010274.8819366,
            1792010280.9827027,
            1792010287.08339,
            1792010293.1839166,
            1792010299.2845395,
            1792010305.3850808,
            1792010311.485712,
            1792076560.7241564,
            1792076573.3271213,
            1792078112.8737848,
            1792078125.5443416,
            1792078141.4135487,
            1792078154.6426532,
            1792078168.46501,
            1792078168.5312345,
            1792078181.7045743,
            1792078181.7356536,
            1792078195.8028133,
            1792078195.83975,
            1792078209.905788,
            1792078209.9678419,
            1792078223.868928,
            1792078223.8901808,
        ],
        "interval": 12.2,
        "until": 1792078256.588203,
        "page_gap": 30,
        "completed": 1792078226.588203,
        "inventory_completed": 1792078226.588203,
        "blocked": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": 1792078226.588203,
    },
    "state_sha256": (
        "d283961f45b0736dfcb6277acf41ddfd292" "a85548d61bd47c12279c1b22da224"
    ),
}


class PhaseExpired(BaseException):
    """A nonrenewable phase deadline; publisher traffic cannot extend it."""


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def empty_budget(now):
    return {
        "schema": 1,
        "family": FAMILY,
        "starts": [],
        "interval": 12.2,
        "until": now + 60,
        "page_gap": 30,
        "completed": 0,
        "inventory_completed": 0,
        "blocked": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": now,
    }


def validate_budget(state):
    """Upgrade known local fields; historical missing429 fields stay unknown."""
    if not isinstance(state, dict):
        raise AdapterError("Invalid publisher state", "access")
    if set(state) in (
        {"starts", "interval", "until"},
        {
            "starts",
            "interval",
            "until",
            "page_gap",
            "completed",
            "blocked",
            "legacy_429_history",
            "observed_429_since_transition",
            "observed_at",
        },
    ):
        old = state
        state = {
            **empty_budget(time.time()),
            **old,
            "schema": 1,
            "family": FAMILY,
        }
        state["interval"] = max(12.2, old["interval"])
        state["page_gap"] = max(30, old.get("page_gap", 0))
        state["inventory_completed"] = old.get("completed", 0)
    expected = set(empty_budget(0))
    if (
        set(state) != expected
        or type(state["schema"]) is not int
        or state["schema"] != 1
        or state["family"] != FAMILY
        or any(
            not numeric(state[k])
            for k in (
                "interval",
                "until",
                "page_gap",
                "completed",
                "inventory_completed",
                "rate_limit_until",
                "observed_at",
            )
        )
        or state["interval"] < 12.2
        or state["page_gap"] < 30
        or state["legacy_429_history"] != "unknown"
        or type(state["blocked"]) is not bool
        or type(state["observed_429_since_transition"]) is not int
        or not 0 <= state["observed_429_since_transition"] <= 100
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 1000
        or any(
            not numeric(t) or t > state["observed_at"] for t in state["starts"]
        )
        or state["starts"] != sorted(set(state["starts"]))
        or state["completed"] > state["observed_at"]
        or state["inventory_completed"] > state["observed_at"]
        or state["observed_at"] > time.time() + 1
    ):
        raise AdapterError("Invalid retained publisher constraints", "access")
    return state


def budget_file():
    global _STATE_PATH, _STATE_LOCK
    _STATE_PATH = _STATE_LOCK = budget_path("youthforeurope.eu")


def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    flags = os.O_RDWR | os.O_NOFOLLOW
    if os.environ.get("GITHUB_ACTIONS") == "true" and not _RESTORED:
        try:
            fd = os.open(_STATE_PATH, flags | os.O_CREAT | os.O_EXCL, 0o600)
            _JOURNAL_NEW = True
        except FileExistsError:
            fd = os.open(_STATE_PATH, flags)
            _JOURNAL_NEW = False
    else:
        fd = os.open(_STATE_PATH, flags)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    file = os.fdopen(fd, "r+")
    fcntl.flock(file, fcntl.LOCK_EX)
    return file


def load_budget():
    with open(_STATE_PATH, "rb") as file:
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher journal", "access")
    if (
        not raw
        and _JOURNAL_NEW
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and not _RESTORED
    ):
        return empty_budget(time.time())
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher journal", "access") from None


def write_budget(state):
    """Retain the existing legacy journal inode under its original flock."""
    state = validate_budget(state)
    fd = os.open(_STATE_PATH, os.O_WRONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "w") as file:
        file.seek(0)
        json.dump(state, file, separators=(",", ":"))
        file.truncate()
        file.flush()
        os.fsync(file.fileno())


def merge_budget(left, right):
    left, right = validate_budget(left), validate_budget(right)
    result = empty_budget(time.time())
    for key in (
        "until",
        "interval",
        "page_gap",
        "completed",
        "inventory_completed",
        "observed_429_since_transition",
        "rate_limit_until",
    ):
        result[key] = max(left[key], right[key])
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["blocked"] = left["blocked"] or right["blocked"]
    return validate_budget(result)


def remaining():
    check_deadline()
    return min(_RUN_END - EXPORT_TIMEOUT, _PHASE_END) - time.monotonic()


def pace(interval=12.2):
    """Hold the physical journal lock through the response completion."""
    lock = locked_budget()
    try:
        state = load_budget()
        if state["blocked"]:
            raise AdapterError("Retained actual publisher refusal", "access")
        if state["observed_429_since_transition"] >= 100:
            raise AdapterError(
                "Unsupported retained429 history; no traffic", "access"
            )
        state["interval"] = max(state["interval"], interval, 12.2)
        if state["rate_limit_until"] > time.time():
            raise AdapterError(
                "Retained publisher rate-limit embargo", "access", 429
            )
        while True:
            now = time.time()
            recent = [t for t in state["starts"] if now - t < 60]
            ready = max(
                state["until"],
                state["completed"] + state["interval"],
                recent[-1] + state["interval"] if recent else now,
                recent[-5] + 60 if len(recent) >= 5 else now,
            )
            if ready - now + 90 >= remaining():
                raise AdapterError(
                    "Retained pacing exceeds remaining collection budget",
                    "access",
                )
            if ready <= now:
                state["starts"] = recent + [now]
                state["observed_at"] = now
                write_budget(state)
                return lock, state
            time.sleep(min(ready - now, 30))
    except BaseException:
        lock.close()
        raise


def retain_refusal():
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
        state["observed_at"] = time.time()
        write_budget(state)


@contextlib.contextmanager
def budget_state(host):
    if host != "youthforeurope.eu":
        raise AdapterError("Unexpected publisher journal host", "access")
    with locked_budget():
        state = load_budget()
        try:
            yield state
        finally:
            state["observed_at"] = time.time()
            write_budget(state)


def retry_embargo(value):
    """Return finite future timing without mutating malformed input."""
    if value is None:
        return 0
    try:
        value = value.strip()
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
            until = time.time() + float(value)
        else:
            date = email.utils.parsedate_to_datetime(value)
            if date.utcoffset() is None:
                return 0
            until = date.timestamp()
        if not numeric(until) or until <= time.time():
            return 0
        datetime.datetime.fromtimestamp(until, datetime.timezone.utc)
        return until
    except (ValueError, TypeError, OverflowError, OSError, AttributeError):
        return 0


def request_bytes(
    url,
    *,
    interval=12.2,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=5_000_000,
):
    if type(byte_limit) is not int or not 0 < byte_limit <= 5_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError(
            "Robots purpose requires literal robots.txt", "access"
        )
    publisher = urllib.parse.urlsplit(url).hostname in {
        "youthforeurope.eu",
        "www.youthforeurope.eu",
    }
    if publisher and not _STATE_TRUSTED:
        raise AdapterError(
            "Trusted publisher state required before access", "access"
        )
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = pace(interval) if publisher else (None, None)
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={**(headers or {}), "User-Agent": _USER_AGENT},
        )
        if publisher:
            print(
                json.dumps(
                    {
                        "event": "request_start",
                        "url": url,
                        "time": time.time(),
                        "user_agent": request.get_header("User-agent"),
                    }
                ),
                file=sys.stderr,
                flush=True,
            )
        try:
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status
            if publisher:
                state["until"] = max(
                    state["until"],
                    retry_embargo(response.headers.get("Retry-After")),
                )
                if status == 429:
                    strikes = state["observed_429_since_transition"] + 1
                    state["observed_429_since_transition"] = strikes
                    state["interval"] = max(
                        state["interval"] * 2, 12.2 * 2**strikes
                    )
                    state["page_gap"] = max(
                        state["page_gap"] * 2, 30 * 2**strikes
                    )
                    state["until"] = max(
                        state["until"], time.time() + 1800 * 2 ** (strikes - 1)
                    )
                    state["rate_limit_until"] = max(
                        state["rate_limit_until"], state["until"]
                    )
                if status in (401, 403, 451) and not robots:
                    state["blocked"] = True
                state["observed_at"] = time.time()
                write_budget(state)
            try:
                body = response.read(byte_limit + 1)
            except (OSError, urllib.error.URLError) as error:
                if publisher and (
                    status == 429 or status in (401, 403, 451) and not robots
                ):
                    raise AdapterError(
                        (
                            "Publisher refusal/rate-limit body failed; "
                            "retained state"
                        ),
                        "access",
                        status,
                    ) from error
                raise
            if len(body) > byte_limit:
                raise AdapterError("Response exceeds5MB", "fetch")
            if (
                publisher
                and (not robots or status == 429)
                and visible_access_challenge(body)
            ):
                state["blocked"] = True
                raise AdapterError(
                    "Visible publisher content refusal", "access"
                )
            if publisher and status == 429:
                raise AdapterError(
                    "Publisher429; durable adaptive cooldown retained",
                    "access",
                    429,
                )
            return status, response.headers, body
    finally:
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = state["completed"]
                write_budget(state)
                print(
                    json.dumps(
                        {
                            "event": "request_complete",
                            "url": url,
                            "time": time.time(),
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                lock.close()


_INVENTORY_ACTIVE = False


def inventory_guard():
    """Each physical inventory retry/redirect retains the completion gap."""
    with locked_budget():
        state = load_budget()
    while True:
        wait = state["inventory_completed"] + state["page_gap"] - time.time()
        if wait <= 0:
            return
        if wait + 90 >= remaining():
            raise AdapterError(
                "Page guard exceeds remaining collection budget", "access"
            )
        time.sleep(min(wait, 30))


def fetch_inventory_page(fetch, *args, **kwargs):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = True
    try:
        return fetch(*args, **kwargs)
    finally:
        _INVENTORY_ACTIVE = previous


def check_inventory_budget(pages, details):
    with locked_budget():
        state = load_budget()
    requests = pages + details
    physical = requests * (state["interval"] + 90)
    pagination = max(0, pages - 1) * state["page_gap"]
    if max(physical, pagination) + 120 >= remaining():
        raise AdapterError(
            "Complete remaining frontier cannot fit retained timing", "access"
        )


def prepare_collection():
    global _COLLECTION_LOCK_FD, _STATE_TRUSTED
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            raise AdapterError("Unsafe lifecycle lock", "access")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AdapterError(
                "Another publisher collector is active", "access"
            ) from None
        _COLLECTION_LOCK_FD = fd
    budget_file()
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    else:
        with locked_budget():
            state = load_budget()
            state["until"] = max(state["until"], time.time() + 60)
            write_budget(state)
    _STATE_TRUSTED = True


def collect():
    """One complete actual native collection with bounded exclusive access."""
    if _RUN_END is None:
        with execution():
            return collect()
    with phase("collection", COLLECTION_TIMEOUT):
        prepare_collection()
        return collect_inventory()


def read_snapshot():
    if _PHASE_END is not None:
        return read_snapshot_body()
    with phase("snapshot", 180):
        return read_snapshot_body()


def publish_files(snapshot, files):
    if _PHASE_END is not None:
        return publish_files_body(snapshot, files)
    with phase("publication", 180):
        return publish_files_body(snapshot, files)


def main():
    with execution():
        try:
            return run()
        except PhaseExpired:
            print(
                json.dumps(
                    {
                        "source": SOURCE_ID,
                        "status": "fail",
                        "error": "Nonrenewable runtime phase deadline expired",
                        "failure_stage": "runtime",
                        "last_attempt_at": utc_now(),
                    }
                )
            )
            return 1
        finally:
            if os.environ.get("GITHUB_ACTIONS") == "true":
                with phase("export", EXPORT_TIMEOUT):
                    export_family_artifact()


def check_deadline():
    if _PHASE_END is None or _RUN_END is None:
        raise AdapterError("Bounded execution context required", "access")
    if time.monotonic() >= min(_PHASE_END, _RUN_END):
        raise PhaseExpired()


def request_timeout(publisher=False):
    check_deadline()
    remaining = min(_PHASE_END, _RUN_END) - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    return min(90 if publisher else 15, remaining)


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
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
        if _COLLECTION_LOCK_FD is not None:
            try:
                os.close(_COLLECTION_LOCK_FD)
            except OSError:
                pass
            _COLLECTION_LOCK_FD = None


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
        _SCANNED_JOB_DIGESTS[(identity, attempt, page)] = hashlib.sha256(
            json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
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
            value = datetime.datetime.fromisoformat(
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
            or run["workflow_id"] != 384792184
            or revision is not None
            and (run["head_sha"], run["workflow_id"]) != revision
            or expected
            and (identity, attempt, source) != expected
        ):
            raise ValueError("Untrusted completed attempt")
        value = datetime.datetime.fromisoformat(
            run["run_started_at"].replace("Z", "+00:00")
        )
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        finished = attempt_completion(identity, attempt, run["head_sha"])
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
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
    global _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS
    _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS = {}, {}, {}
    current_id, current_attempt = current_run_identity()
    current_seen = False
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/workflows/fetch-youth-for-europe.yml/runs"
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
            ):
                raise AdapterError("Invalid family run identity", "access")
            if identity != current_id:
                _SCANNED_HISTORY[(identity, attempt)] = (
                    run.get("head_sha"),
                    run.get("workflow_id"),
                )
            if identity == current_id:
                if (
                    run.get("head_sha") != os.environ.get("GITHUB_SHA")
                    or run.get("workflow_id") != 384792184
                ):
                    raise AdapterError(
                        "Current workflow head or identity changed", "access"
                    )
                if attempt != current_attempt:
                    raise AdapterError(
                        ("Current workflow attempt mismatch"), ("access")
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
            if not current_seen:
                raise AdapterError(
                    "Current workflow absent from complete history", "access"
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


class InventoryRace(AdapterError):
    """The advertised complete workflow inventory changed during reading."""


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


def export_family_artifact():
    """Atomically export trusted state and signal its availability."""
    path = os.environ.get("YFE_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
    run_id, attempt = current_run_identity()
    configured = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe artifact output path", "access")
    info = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
    ):
        raise AdapterError("Unsafe artifact output directory", "access")
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
    fd, temporary = tempfile.mkstemp(prefix=".yfe-pacing-", dir=root)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(envelope, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        descriptor = os.open(
            os.environ["GITHUB_OUTPUT"],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
        )
        with os.fdopen(descriptor, "a") as output:
            info = os.fstat(output.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise AdapterError("Unsafe workflow output signal", "access")
            output.write("pacing_ready=true\n")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def visible_access_challenge(body):
    """Identify visible refusals while excluding ordinary hidden chrome."""
    message = re.compile(
        r"^(?:(?:please|kindly)\s+)?(?:verify(?: that)? you are human|"
        r"access denied|permission denied|forbidden|just a moment|"
        r"request rejected|"
        r"request denied|request blocked|attention required|"
        r"complete the captcha|"
        r"enable javascript and cookies to continue|checking your browser)"
        r"(?:\s+(?:to continue|before continuing))?"
        r"(?:[.!:,]\s+.*)?[.!:,\s\d-]*$",
        re.I,
    )
    root = PublicHtml(body.decode("utf-8", "replace")).root
    refused = False

    def visible(node):
        nonlocal refused
        style = node.attrs.get("style") or ""
        location = (
            (node.attrs.get("id") or "") + " " + (node.attrs.get("class") or "")
        )
        if (
            node.tag
            in {"script", "style", "noscript", "footer", "form", "nav", "aside"}
            or "hidden" in node.attrs
            or (node.attrs.get("aria-hidden") or "").lower() == "true"
            or re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I
            )
            or re.search(r"(?:^|[\s_-])footer(?:$|[\s_-])", location, re.I)
        ):
            return ""
        text = " ".join(
            visible(child) if isinstance(child, Element) else child
            for child in node.children
        )
        text = " ".join(text.split())
        if message.fullmatch(text):
            refused = True
        return text

    visible(root)
    return refused


_SCANNED_JOB_DIGESTS = {}
_ATTEMPT_HEADS = {}

_LEGACY_HEADS = {
    38634723998: "9dc9dfc87c0a2232ca84e2556fccb1e7aaabc2b8",
    38684236206: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38686043194: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38721259339: "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
    38720083972: "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
}
_LEGACY_JOB_DIGESTS = {
    38634723998: (
        "d9d2987c21d2a428607dbc3467e30302" "54c0d0d153091b2fae43893e198464f1"
    ),
    38684236206: (
        "4c87634627ae73665633afde19f67644" "a7cc0db29ce67ef93ba7e4e39f4630bd"
    ),
    38686043194: (
        "a515f819b698b10bb81939d418d341aa" "01af841643935b3835d8325bbd08f4a3"
    ),
    38721259339: (
        "b6126ddaaee490c51ea9816e8760ba97" "ebacca81077b2e3ca4a1494a9b775f92"
    ),
    38720083972: (
        "b363b3b6c1ed7adaca2c1dc6d7953da7" "5862b64c7dafd79c6c47edcd11f31113"
    ),
}


def restore_family_artifact():
    """Require exact history for the reviewed legacy state handoff."""
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    _STATE_TRUSTED = False
    latest = latest_family_run()
    if latest is None:
        raise AdapterError(
            "Existing source history missing; no empty bootstrap", "access"
        )
    run_id, attempt, source = latest
    if run_id in _LEGACY_HEADS:
        expected = {
            (identity, 1): (head, 384792184)
            for identity, head in _LEGACY_HEADS.items()
        }
        if (
            latest != (38720083972, 1, SOURCE_ID)
            or _SCANNED_HISTORY != expected
        ):
            raise AdapterError(
                "Reviewed complete legacy chronology changed", "access"
            )
        for identity in _LEGACY_HEADS:
            if (
                _SCANNED_JOB_DIGESTS.get((identity, 1, 1))
                != _LEGACY_JOB_DIGESTS[identity]
            ):
                raise AdapterError(
                    "Reviewed exact legacy jobs changed", "access"
                )
            artifacts = workflow_api(
                f"/actions/runs/{identity}/artifacts?per_page=100"
            )
            if (
                artifacts.get("total_count") != 0
                or artifacts.get("artifacts") != []
            ):
                raise AdapterError(
                    "Reviewed legacy artifact inventory changed", "access"
                )
        marker = _REVIEWED_NATIVE_MIGRATION
        if (
            not isinstance(marker, dict)
            or set(marker)
            != {"schema", "source", "evidence", "state", "state_sha256"}
            or marker["schema"] != 1
            or marker["source"] != SOURCE_ID
            or not re.fullmatch(
                (
                    r"https://github\.com/YouthOpps/data-pipeline/"
                    r"issues/201#issuecomment-[0-9]+"
                ),
                marker.get("evidence", ""),
            )
            or hashlib.sha256(
                json.dumps(
                    marker["state"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            != marker["state_sha256"]
        ):
            raise AdapterError(
                "Reviewed full native/local migration state required", "access"
            )
        incoming = validate_budget(marker["state"])
    else:
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or result.get("total_count") != len(artifacts)
            or len(artifacts) > 100
        ):
            raise AdapterError("Incomplete newest artifact inventory", "access")
        matches = [
            a
            for a in artifacts
            if a.get("name") == f"{_ARTIFACT_NAME}-{attempt}"
        ]
        if (
            len(matches) != 1
            or matches[0].get("expired") is not False
            or type(matches[0].get("id")) is not int
            or matches[0]["id"] <= 0
            or matches[0].get("workflow_run", {}).get("id") != run_id
            or matches[0].get("workflow_run", {}).get("head_sha")
            != _ATTEMPT_HEADS.get((run_id, attempt))
            or type(matches[0].get("size_in_bytes")) is not int
            or not 0 < matches[0]["size_in_bytes"] <= 32768
            or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", matches[0].get("digest", "")
            )
        ):
            raise AdapterError(
                "Newest mature attempt lacks exact trusted state", "access"
            )
        incoming = download_inert_artifact(
            latest, matches[0]["id"], matches[0]["digest"]
        )
    with locked_budget():
        local = load_budget()
        combined = merge_budget(local, incoming)
        combined["until"] = max(combined["until"], time.time() + 60)
        write_budget(combined)
    _RESTORED = _STATE_TRUSTED = True


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
