"""Discover and parse live Fulbright Austria awards and teaching employment."""

import argparse
import base64
import contextlib
import errno
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
import random
import re
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import copy
import functools
import io
import math
import ssl
import stat
import zipfile

SOURCE_ID = "at-fulbright-austria"
SOURCE_URL = (
    "https://www.fulbright.at/programs/in-austria/students/"
    "full-time-study-research-grants"
)
WEBSITE_URL = "https://www.fulbright.at/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "AT"
PUBLISHER_TYPE = "non-profit"
ATTRIBUTION = "Fulbright Austria — Austrian-American Educational Commission"


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style", "noscript"):
            return ""
        parts = []
        for child in self.children:
            parts.append(child.text() if isinstance(child, Node) else child)
        return " ".join(" ".join(parts).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class PublisherHTML(HTMLParser):
    """Small structural parser retaining the publisher's content boundaries."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in (
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
        ):
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
        self.stack[-1].children.append(data)


def nodes(node, tag=None, class_name=None):
    return [
        n
        for n in node.walk()
        if (tag is None or n.tag == tag)
        and (
            class_name is None or class_name in n.attrs.get("class", "").split()
        )
    ]


def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]


def page(url, *, inventory=False):
    document = fetch_source(
        url, min_interval=check_robots(url), inventory=inventory
    )
    if not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", document, re.I
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    root = PublisherHTML(document).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(
        r"just a moment|access denied|attention required|checking your browser",
        title,
    ):
        raise AdapterError("Publisher access challenge", "access")
    return root


def main_content(root):
    return one(nodes(root, "main"), "complete public programme content")


_MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
_MONTH_PATTERN = "(?:" + "|".join(_MONTHS) + ")"


def own_url(value, base=WEBSITE_URL):
    """Normalize public publisher links without admitting external portals."""
    url = urllib.parse.urljoin(base, value)
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.fulbright.at"
        or parsed.username
        or parsed.password
        or parsed.query
    ):
        return None
    return urllib.parse.urlunsplit(
        ("https", parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def sections(content):
    """Keep programme paragraphs under their live headings, excluding people."""
    result = {}
    heading = "Introduction"
    for node in content.walk():
        if node.tag in ("h1", "h2", "h3", "h4"):
            heading = node.text()
            if re.search(
                r"recipients|contact person|related content", heading, re.I
            ):
                break
        elif node.tag in ("p", "li") and node.text():
            text = node.text()
            if "@" not in text and "Contact now" not in text:
                result.setdefault(heading, []).append(text)
    return {
        key: " ".join(dict.fromkeys(values)) for key, values in result.items()
    }


def calendar_dates(text):
    """Read explicit English dates; do not invent a year or a closing hour."""
    expression = (
        rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\s+(20\d{{2}})\b"
        rf"|\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b"
    )
    dates = []
    for match in re.finditer(expression, text, re.I):
        day, month, year = match.group(1, 2, 3)
        if day is None:
            month, day, year = match.group(4, 5, 6)
        try:
            dates.append(
                datetime(int(year), _MONTHS[month.lower()], int(day))
                .date()
                .isoformat()
            )
        except ValueError as error:
            raise AdapterError(
                "Invalid published application date", "parse"
            ) from error
    return dates


def application_window(text):
    match = re.search(r"Application Period\s*:?(.*?)Grant Length", text, re.I)
    if not match:
        return None, None, "unknown"
    period = match[1].strip()
    now = utc_now()
    today = now[:10]
    windows = []
    for part in period.split(";"):
        dates = calendar_dates(part)
        if not dates:
            continue
        end = dates[-1]
        start = dates[0] if len(dates) > 1 else None
        if start is None:
            opening = re.search(
                rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\s*[–—-]", part, re.I
            )
            if opening:
                start = (
                    datetime(
                        int(end[:4]),
                        _MONTHS[opening[2].lower()],
                        int(opening[1]),
                    )
                    .date()
                    .isoformat()
                )
                if start > end:
                    start = (
                        datetime(
                            int(end[:4]) - 1,
                            _MONTHS[opening[2].lower()],
                            int(opening[1]),
                        )
                        .date()
                        .isoformat()
                    )
        clock = re.search(
            r"(?:(\d{1,2}):(\d{2})\s*(?:\(noon\))?|\b(noon))\s*(CET|CEST|UTC|GMT)\b",
            re.split(r"20\d{2}", part)[-1],
            re.I,
        )
        if clock:
            offset = {"CET": 1, "CEST": 2, "UTC": 0, "GMT": 0}[clock[4].upper()]
            end = (
                datetime.fromisoformat(end)
                .replace(
                    hour=12 if clock[3] else int(clock[1]),
                    minute=0 if clock[3] else int(clock[2]),
                    tzinfo=timezone(timedelta(hours=offset)),
                )
                .astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        windows.append((start, end))
    if not windows:
        return period, None, "unknown"

    def remains_open(end):
        if len(end) == 10:
            return end >= today
        return datetime.fromisoformat(
            end.replace("Z", "+00:00")
        ) > datetime.fromisoformat(now.replace("Z", "+00:00"))

    start, end = next(
        (window for window in windows if remains_open(window[1])), windows[-1]
    )
    status = "unknown" if remains_open(end) else "expired"
    if (
        start
        and start <= today
        and remains_open(end)
        and (len(end) > 10 or today < end)
    ):
        status = "open"
    return period, end, status


def programme_record(url, root):
    content = main_content(root)
    title = one(nodes(content, "h1"), "award title").text()
    blocks = sections(content)
    text = content.text().split("Recipients of", 1)[0]
    slug = url.rsplit("/", 1)[-1]
    track = {
        "fulbright-austria-expert-award": "expert",
        "fulbright-prize-in-american-studies": "american-studies-prize",
    }.get(slug, slug)
    institutional = bool(
        re.search(r"Austrian institutions(?: of [\w ,]+)? apply", text, re.I)
    )
    category = "grants" if institutional else "scholarships"
    if "us-teaching-assistant-program" == slug:
        category = "jobs"
    elif "/awards/" in url:
        category = "competitions"
    hosts = []
    if "schuman" not in slug and "/programs/" in url:
        hosts = [
            (
                "US"
                if "/in-the-usa/" in url or "foreign-langauge" in slug
                else "AT"
            )
        ]
    record = make_record(
        title,
        url,
        [category],
        kind="institutional-grant" if institutional else "opportunity",
        host_countries=hosts,
        method="publisher-html",
        evidence=[ATTRIBUTION, url],
    )
    record["id"] = hashlib.sha256(f"{SOURCE_ID}|{track}".encode()).hexdigest()[
        :24
    ]
    period, record["deadline"], record["status"] = application_window(text)
    eligibility = " ".join(
        value
        for key, value in blocks.items()
        if re.search(r"eligib|qualification", key, re.I)
    )
    if not institutional:
        for country, pattern in (
            ("US", r"(?:must have|require[ds]?) US citizenship"),
            ("AT", r"(?:must have|require[ds]?) Austrian citizenship"),
        ):
            if re.search(pattern, eligibility, re.I):
                record["eligible_countries"].append(country)
    if record["deadline"] is None:
        deadlines = []
        for value in blocks.values():
            for sentence in re.split(r"(?<=[.!?])\s+", value):
                if re.search(r"deadline|apply by|submit.*by", sentence, re.I):
                    deadlines.extend(calendar_dates(sentence))
        if len(set(deadlines)) == 1:
            record["deadline"] = deadlines[0]
            if deadlines[0] < utc_now()[:10]:
                record["status"] = "expired"
    pieces = []
    if period:
        pieces.append("Application period: " + period + ".")
    for label, pattern in (
        ("Funding", r"amount.*benefits|^benefits$"),
        ("Eligibility", r"qualification|eligib"),
        (
            "Programme",
            r"grant summary|quick facts|candidate profile|award details",
        ),
    ):
        values = [
            value
            for key, value in blocks.items()
            if re.search(pattern, key, re.I)
        ]
        if values:
            value = values[0]
            if len(value) > 160:
                value = value[:157].rsplit(" ", 1)[0] + "..."
            pieces.append(label + ": " + value)
    if not pieces:
        values = [value for value in blocks.values() if len(value) > 40]
        if not values:
            raise AdapterError(
                "Award has no substantive public description: " + url, "parse"
            )
        pieces.append(values[0][:500])
    summary = " ".join(pieces)
    if len(summary.encode("utf-16-le")) > 1200:
        summary = (
            summary.encode("utf-16-le")[:1188]
            .decode("utf-16-le", errors="ignore")
            .rsplit(" ", 1)[0]
            + "..."
        )
    record["summary"] = summary
    if re.search(
        r"not (?:offered|available).*20\d{2}|currently inactive", text, re.I
    ):
        record["status"] = "unknown"
    records = [record]
    for heading in blocks:
        if (
            heading
            == "Fulbright Specialist in Music, Performing Arts and Visual Arts"
        ):
            arts = make_record(
                heading,
                url,
                ["grants"],
                kind="institutional-grant",
                host_countries=["AT"],
                method="publisher-html",
                evidence=[ATTRIBUTION, url],
            )
            arts["id"] = hashlib.sha256(
                f"{SOURCE_ID}|specialist-arts".encode()
            ).hexdigest()[:24]
            arts["summary"] = " ".join(
                label + ": " + blocks[label]
                for label in (
                    "Length of grant",
                    "Benefits",
                    "Specialization(s)",
                )
                if label in blocks
            )[:600]
            if not arts["summary"]:
                arts["summary"] = blocks[heading][:600]
            records.append(arts)
    return records


def collect_programme():
    sitemap_url = WEBSITE_URL + "sitemap"
    sitemap = page(sitemap_url, inventory=True)
    hubs, awards = set(), set()
    for link in nodes(sitemap, "a"):
        url = own_url(link.attrs.get("href", ""))
        if not url:
            continue
        path = urllib.parse.urlsplit(url).path
        if re.fullmatch(
            r"/programs/in-(?:austria|the-usa)/(?:students|scholars|teaching-assistants)",
            path,
        ):
            hubs.add(url)
        elif path.startswith("/awards/"):
            awards.add(url)
        elif path.startswith("/programs/us-eu-"):
            hubs.add(url)
    if not hubs:
        raise AdapterError(
            "No programme directories in publisher sitemap", "parse"
        )
    for url in sorted(hubs):
        for link in nodes(main_content(page(url, inventory=True)), "a"):
            target = own_url(link.attrs.get("href", ""), url)
            if target and "/grant-details/" in target:
                awards.add(target)
    if not awards:
        raise AdapterError(
            "No awards discovered in programme directories", "parse"
        )
    records = {}
    for url in sorted(awards):
        for record in programme_record(url, page(url)):
            # The publisher links the same EU Schuman programme from both directions.
            if record["id"] in records and not url.endswith(
                "/fulbright-schuman-program"
            ):
                raise AdapterError(
                    "Duplicate discovered award identity: " + url, "validate"
                )
            records[record["id"]] = record
    result = sorted(records.values(), key=lambda record: record["id"])
    validate_records(result)
    return result


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


def legacy_pace(url, interval=6, *, start_padding=0):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
    if start_padding:
        padding_until = None
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
    publisher = host
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
        budget["interval"] = max(budget["interval"], interval, 6)
        while True:
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
                    if len(budget["starts"]) >= 10
                    else now
                ),
            )
            if start_padding and ready <= now:
                if padding_until is None:
                    padding_until = now + start_padding
                ready = max(ready, padding_until)
            if ready <= now:
                budget["starts"].append(now)
                legacy_save_budget(state, budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def legacy_save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


def legacy_request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    deadline=None,
    retry_jitter=0,
):
    if retry_jitter:
        state, budget = legacy_pace(url, interval, start_padding=retry_jitter)
    else:
        state, budget = legacy_pace(url, interval)
    request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers=request_headers,
        )
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
            retry = response.headers.get("Retry-After")
            if retry:
                try:
                    seconds = (
                        float(retry)
                        if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                        else parsedate_to_datetime(retry).timestamp()
                        - time.time()
                    )
                    budget["until"] = max(
                        budget["until"], time.time() + max(0, seconds)
                    )
                    legacy_save_budget(state, budget)
                except (ValueError, TypeError, OverflowError):
                    pass
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            return response.status, response.headers, body
    finally:
        state.close()


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
                    expired = deadline.tzinfo is not None and checked > deadline
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
                raise AdapterError("Missing record field: " + field, "validate")
        if record["id"] in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"])
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
                raise AdapterError("Missing record field: " + field, "validate")
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
def original_publication_http_guard(deadline):
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
    cancelling = False

    def exhausted(signum, frame):
        nonlocal cancelling
        if cancelling:
            signal.setitimer(signal.ITIMER_REAL, 0)
        else:
            cancelling = True
            # Bound response.close() while unwinding the first deadline.
            signal.setitimer(signal.ITIMER_REAL, 0.25)
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

    proof_stage = "response time headers"
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
        proof_stage = "job response JSON"
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
        proof_stage = "exact running job identity"
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
        proof_stage = "job start timestamp"
        clock_type = getattr(datetime, "datetime", datetime)
        started = clock_type.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.tzinfo is None or started.utcoffset().total_seconds() != 0:
            raise ValueError("Expected UTC job start")
        proof_stage = "remaining job deadline"
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
            "Unusable exact job deadline proof: " + proof_stage, "publish"
        ) from None
    _PUBLICATION_IDENTITY = identity
    _PUBLICATION_WINDOW = {"write": end - 60, "read": end - 30, "job": end}
    return _PUBLICATION_WINDOW


def original_publication_window():
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


def original_read_snapshot():
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


def original_publish_files(snapshot, files):
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


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "display_name": "Fulbright Austria",
        "source": SOURCE_ID,
        "name": "Fulbright Austria",
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
                "validated Fulbright Austria "
                "awards and teaching positions"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def original_run_body(publishing):
    args = argparse.Namespace(publish=publishing)
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


_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"

FAMILY = "youthopps-at-fulbright-austria-publisher-v1"

FAMILY_SOURCES = {SOURCE_ID}

_WORKFLOW_ID = 378390042

_ARTIFACT_NAME = "at-fulbright-austria-pacing-state"

_STATE_SCHEMA = 3

COLLECTION_TIMEOUT = 1200

RUN_TIMEOUT = 1740

EXPORT_TIMEOUT = 30

_STATE_PATH = _STATE_LOCK = None

_STATE_DIR_FD = _STATE_DIR_ID = None

_STATE_INITIALIZING = False

_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None
_COLLECTION_LOCK_ID = None

_RESTORED = _PUBLISHING = _STATE_TRUSTED = False

_STARTUP_QUIET_UNTIL = None
_ACQUIRED_IDS = {}
_REP_PROVENANCE = {}
_PUBLISHER_COMPLETED_MONOTONIC = None

_PUBLISHER_INTERVAL = 12.2

_PUBLISHER_GATE_FAILED = False

_LOCAL_FIRST_WIRE_VERIFIED = False

_INVENTORY_COMPLETED_MONOTONIC = None

_RUN_END = _PHASE_END = _PHASE_NAME = None
_ENTRY_MONOTONIC = None
_PHASE_SEEN = set()
_FAILURE_CONTEXT = None
_INITIAL_SNAPSHOT_ATTEMPTED = False
_OPERATION_END = None

_ROBOTS = {}

_REP_TARGETS = {}

_HISTORY_COMPLETED = []

_HISTORY_JOBLESS = []

_ACTIVE_FLIGHT_ID = _COMPLETING_FLIGHT_ID = None

_ACTIVE_REQUEST_ID = None

_TRANSACTION_DEPTH = 0

_REQUEST_FRAMES = []

_FAILED_BUDGET = None
_JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
_JOURNAL_BYTES = None
_JOURNAL_CREATED = _JOURNAL_DIRTY = False
_JOURNAL_LOCKS = {}

_LEGACY_TRANSITION = {
    "schema": 1,
    "family": "youthopps-at-fulbright-austria-publisher-v1",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/287#issuecomment-6205971447",
    "history_sha256": "626f2ea041393237cdc24f66344254fd6a519eb888ba4dbd9960972418e371a0",
    "state": {
        "schema": 3,
        "family": "youthopps-at-fulbright-austria-publisher-v1",
        "observed_at": 1792109551.3858202,
        "not_before": 1792109611.3858202,
        "starts": [
            1791537651.0,
            1791538380.0,
            1791563977.0,
            1791594856.0,
            1791621064.0,
            1791627252.0,
            1791654230.0,
            1791661030.0,
            1791678818.0,
            1791714313.0,
            1791748160.0,
            1791766250.0,
            1791804340.0,
            1791844971.0,
            1791889828.0,
            1791924972.0,
            1791938574.0,
            1791949949.0,
            1791966954.0,
            1791969992.0,
            1791989105.0,
            1792010388.0,
            1792023012.0,
            1792041394.0,
            1792053631.0,
            1792061038.0,
            1792061318.0,
            1792080069.0,
            1792108842.370844,
            1792108842.8444586,
            1792108843.1831381,
            1792108844.4603271,
            1792108844.5020561,
            1792108856.7038445,
            1792108856.7076952,
            1792108857.181325,
            1792108858.7055748,
            1792108858.806768,
            1792108888.8085139,
            1792108888.811815,
            1792108889.3790872,
            1792108891.9251668,
            1792108892.3001637,
            1792108922.3080451,
            1792108922.3119302,
            1792108922.5012023,
            1792108924.143191,
            1792108924.3370132,
            1792108954.339299,
            1792108954.341443,
            1792108954.3561034,
            1792108955.661364,
            1792108955.8392682,
            1792108985.8401618,
            1792108985.8438582,
            1792108986.0266092,
            1792108987.5377815,
            1792108987.737538,
            1792109017.7383735,
            1792109017.7396946,
            1792109017.7544382,
            1792109018.9326515,
            1792109019.0986607,
            1792109049.1015468,
            1792109049.1032975,
            1792109049.6452265,
            1792109051.0075693,
            1792109051.1924734,
            1792109081.1932151,
            1792109081.1942885,
            1792109082.6449049,
            1792109083.8961709,
            1792109084.0485072,
            1792109096.259801,
            1792109096.2612395,
            1792109096.59125,
            1792109097.617447,
            1792109097.7631562,
            1792109109.9787664,
            1792109109.9825573,
            1792109110.2246323,
            1792109111.478568,
            1792109111.6485941,
            1792109123.8602355,
            1792109123.8621519,
            1792109124.2033505,
            1792109125.3340347,
            1792109125.514952,
            1792109137.7240746,
            1792109137.7255657,
            1792109137.9080863,
            1792109138.7000222,
            1792109138.8382242,
            1792109151.0498586,
            1792109151.0561094,
            1792109151.2517495,
            1792109152.9782481,
            1792109153.161619,
            1792109165.3816512,
            1792109165.386699,
            1792109165.7245197,
            1792109166.8215892,
            1792109166.989789,
            1792109179.2008615,
            1792109179.2092226,
            1792109179.3972309,
            1792109180.4390388,
            1792109180.619805,
            1792109192.8298266,
            1792109192.8317795,
            1792109193.0050883,
            1792109194.0407722,
            1792109194.2199314,
            1792109206.4306774,
            1792109206.4316819,
            1792109206.4465404,
            1792109207.4039528,
            1792109207.573806,
            1792109219.784022,
            1792109219.7864234,
            1792109219.8021898,
            1792109220.6368656,
            1792109220.8208594,
            1792109233.0363235,
            1792109233.038227,
            1792109233.2225173,
            1792109234.070516,
            1792109234.2217524,
            1792109246.4322898,
            1792109246.4344726,
            1792109246.450529,
            1792109247.416858,
            1792109247.5930874,
            1792109259.8038542,
            1792109259.805202,
            1792109259.9890969,
            1792109261.1024606,
            1792109261.2741048,
            1792109273.4886136,
            1792109273.490219,
            1792109273.7364724,
            1792109274.8977165,
            1792109275.0443375,
            1792109287.2545683,
            1792109287.260468,
            1792109287.6317542,
            1792109288.7097735,
            1792109288.858092,
            1792109301.067824,
            1792109301.0698297,
            1792109301.309077,
            1792109302.3059072,
            1792109302.482199,
            1792109314.6989732,
            1792109314.7008023,
            1792109315.036252,
            1792109315.8291218,
            1792109316.0039296,
            1792109328.2150843,
            1792109328.2181652,
            1792109328.7969825,
            1792109329.8714924,
            1792109330.018564,
            1792109342.2277877,
            1792109342.2313595,
            1792109342.2499118,
            1792109343.0934691,
            1792109343.2747495,
            1792109355.4833095,
            1792109355.487861,
            1792109355.8251948,
            1792109356.9422617,
            1792109356.9489625,
            1792109369.1562219,
            1792109369.1582782,
            1792109369.3483264,
            1792109371.803035,
            1792109371.9535594,
            1792109384.163381,
            1792109384.1652167,
            1792109384.1815472,
            1792109385.091434,
            1792109385.2568345,
            1792109397.4687095,
            1792109397.4701338,
            1792109397.7954314,
            1792109398.9180455,
            1792109398.9292815,
            1792109411.1406164,
            1792109411.145723,
            1792109411.4666924,
            1792109412.265043,
            1792109412.4372573,
            1792109424.6460402,
            1792109424.6475298,
            1792109424.6634028,
            1792109425.7454665,
            1792109425.9110172,
            1792109438.1325347,
            1792109438.1411705,
            1792109438.3499064,
            1792109439.3894134,
            1792109439.5754364,
            1792109451.7878253,
            1792109451.7893107,
            1792109451.8056908,
            1792109452.5972738,
            1792109452.7344654,
            1792109464.990306,
            1792109465.0156,
            1792109465.185367,
            1792109468.11366,
            1792109468.2389615,
            1792109480.4732704,
            1792109480.4748578,
            1792109480.6523294,
            1792109481.4852853,
            1792109481.6667368,
            1792109493.8772237,
            1792109493.8784404,
            1792109493.9096758,
            1792109494.667886,
            1792109494.8354652,
            1792109507.0457,
            1792109507.0467994,
            1792109507.2237012,
            1792109508.3796582,
            1792109508.5521257,
            1792109520.7651181,
            1792109520.7663283,
            1792109521.2168906,
            1792109522.5699005,
            1792109522.9358468,
            1792109535.1518488,
            1792109535.1578143,
            1792109535.7250628,
            1792109537.1011617,
            1792109537.261913,
            1792109549.5072083,
            1792109549.5217738,
            1792109549.870452,
            1792109551.023751,
            1792109551.198384,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792109551.200767,
        "page_interval": 30,
        "last_inventory_completed": 1792109084.0487263,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "in_flight": False,
        "flight_id": "1b60cef4cb4b1a809c483b4d5d663daa",
        "flight_started": 1792109537.303161,
        "flight_kind": "policy",
        "completed_flight_id": "1b60cef4cb4b1a809c483b4d5d663daa",
        "request_id": "2e9280635ede524ba5d5d40e117ef54f",
        "request_started": 1792109549.5072083,
        "request_inventory": False,
        "completed_request_id": "2e9280635ede524ba5d5d40e117ef54f",
        "request_outcome": "response_assessed",
    },
    "legacy_publisher_files_absent": True,
}

_LEGACY_JOB_PINS = {
    "38763419510": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs": [
            {
                "id": 116520331427,
                "run_id": 38763419510,
                "run_attempt": 1,
                "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T15:50:57Z",
                "completed_at": "2026-10-15T16:01:09Z",
            }
        ],
    },
    "38721259676": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs": [
            {
                "id": 116376106555,
                "run_id": 38721259676,
                "run_attempt": 1,
                "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T10:38:25Z",
                "completed_at": "2026-10-15T10:48:38Z",
            }
        ],
    },
    "38720083801": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs": [
            {
                "id": 116375683381,
                "run_id": 38720083801,
                "run_attempt": 1,
                "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T10:33:41Z",
                "completed_at": "2026-10-15T10:43:58Z",
            }
        ],
    },
    "38706641739": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "id": 116327572118,
                "run_id": 38706641739,
                "run_attempt": 1,
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T08:34:34Z",
                "completed_at": "2026-10-15T08:40:31Z",
            }
        ],
    },
    "38684235887": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "id": 116258855359,
                "run_id": 38684235887,
                "run_attempt": 1,
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T05:10:36Z",
                "completed_at": "2026-10-15T05:16:34Z",
            }
        ],
    },
    "38657854456": {
        "attempt": 1,
        "conclusion": "success",
        "head": "e1a68725fe923582b5640481e29c7166eb77397c",
        "jobs": [
            {
                "id": 116171783381,
                "run_id": 38657854456,
                "run_attempt": 1,
                "head_sha": "e1a68725fe923582b5640481e29c7166eb77397c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T00:04:13Z",
                "completed_at": "2026-10-15T00:10:12Z",
            }
        ],
    },
    "38625563768": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5f82ad39165005553048f1377a0fbd844aaa50cf",
        "jobs": [
            {
                "id": 116089476131,
                "run_id": 38625563768,
                "run_attempt": 1,
                "head_sha": "5f82ad39165005553048f1377a0fbd844aaa50cf",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T20:33:51Z",
                "completed_at": "2026-10-14T20:39:48Z",
            }
        ],
    },
    "38582411891": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "id": 115915454053,
                "run_id": 38582411891,
                "run_attempt": 1,
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T14:39:07Z",
                "completed_at": "2026-10-14T14:45:05Z",
            }
        ],
    },
    "38539130717": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "id": 115777274187,
                "run_id": 38539130717,
                "run_attempt": 1,
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T09:19:54Z",
                "completed_at": "2026-10-14T09:26:32Z",
            }
        ],
    },
    "38534734820": {
        "attempt": 1,
        "conclusion": "success",
        "head": "1e210c2ef6565b5c055bd073490184f0a465096b",
        "jobs": [
            {
                "id": 115757065449,
                "run_id": 38534734820,
                "run_attempt": 1,
                "head_sha": "1e210c2ef6565b5c055bd073490184f0a465096b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T08:29:15Z",
                "completed_at": "2026-10-14T08:35:54Z",
            }
        ],
    },
    "38507918471": {
        "attempt": 1,
        "conclusion": "success",
        "head": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "jobs": [
            {
                "id": 115669556119,
                "run_id": 38507918471,
                "run_attempt": 1,
                "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T03:45:52Z",
                "completed_at": "2026-10-14T03:52:29Z",
            }
        ],
    },
    "38491003662": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115615900756,
                "run_id": 38491003662,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T00:35:06Z",
                "completed_at": "2026-10-14T00:42:54Z",
            }
        ],
    },
    "38465730164": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115531939706,
                "run_id": 38465730164,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-13T20:48:28Z",
                "completed_at": "2026-10-13T20:56:12Z",
            }
        ],
    },
    "38387502955": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115262785566,
                "run_id": 38387502955,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-13T11:02:44Z",
                "completed_at": "2026-10-13T11:10:28Z",
            }
        ],
    },
    "38317492106": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115037501406,
                "run_id": 38317492106,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-12T22:35:08Z",
                "completed_at": "2026-10-12T22:42:51Z",
            }
        ],
    },
    "38241741332": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 114785769458,
                "run_id": 38241741332,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-12T11:17:55Z",
                "completed_at": "2026-10-12T11:25:40Z",
            }
        ],
    },
    "38189078576": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114617724980,
                "run_id": 38189078576,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-12T00:43:05Z",
                "completed_at": "2026-10-12T00:50:50Z",
            }
        ],
    },
    "38169089899": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114557704514,
                "run_id": 38169089899,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-11T19:41:33Z",
                "completed_at": "2026-10-11T19:49:20Z",
            }
        ],
    },
    "38131419340": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114446359587,
                "run_id": 38131419340,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-11T10:17:29Z",
                "completed_at": "2026-10-11T10:25:13Z",
            }
        ],
    },
    "38098396057": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114349018869,
                "run_id": 38098396057,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-11T00:25:53Z",
                "completed_at": "2026-10-11T00:33:38Z",
            }
        ],
    },
    "38079997226": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
        "jobs": [
            {
                "id": 114294773066,
                "run_id": 38079997226,
                "run_attempt": 1,
                "head_sha": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T19:29:26Z",
                "completed_at": "2026-10-10T19:37:10Z",
            }
        ],
    },
    "38071840274": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "jobs": [
            {
                "id": 114272332493,
                "run_id": 38071840274,
                "run_attempt": 1,
                "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T17:36:04Z",
                "completed_at": "2026-10-10T17:43:50Z",
            }
        ],
    },
    "38043748012": {
        "attempt": 1,
        "conclusion": "success",
        "head": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "jobs": [
            {
                "id": 114188996615,
                "run_id": 38043748012,
                "run_attempt": 1,
                "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T10:06:28Z",
                "completed_at": "2026-10-10T10:14:12Z",
            }
        ],
    },
    "38037653006": {
        "attempt": 1,
        "conclusion": "success",
        "head": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
        "jobs": [
            {
                "id": 114171356213,
                "run_id": 38037653006,
                "run_attempt": 1,
                "head_sha": "519c7ea0d4d51ae9d3bf033d69d91bfaa22c72ed",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T08:22:35Z",
                "completed_at": "2026-10-10T08:31:04Z",
            }
        ],
    },
    "38012271414": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "5d603ae26c1b4a23b1002be6eaeabf551d617ca6",
        "jobs": [
            {
                "id": 114094690282,
                "run_id": 38012271414,
                "run_attempt": 1,
                "head_sha": "5d603ae26c1b4a23b1002be6eaeabf551d617ca6",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T01:12:37Z",
                "completed_at": "2026-10-10T01:14:16Z",
            }
        ],
    },
    "37959777822": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "jobs": [
            {
                "id": 113919357463,
                "run_id": 37959777822,
                "run_attempt": 1,
                "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-09T16:31:52Z",
                "completed_at": "2026-10-09T16:39:37Z",
            }
        ],
    },
    "37911134174": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "jobs": [
            {
                "id": 113756292535,
                "run_id": 37911134174,
                "run_attempt": 1,
                "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-09T09:25:12Z",
                "completed_at": "2026-10-09T09:33:00Z",
            }
        ],
    },
    "37909834934": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "jobs": [
            {
                "id": 113752055082,
                "run_id": 37909834934,
                "run_attempt": 1,
                "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-09T09:13:03Z",
                "completed_at": "2026-10-09T09:20:51Z",
            }
        ],
    },
}


def nonexport_end():
    """Keep export and authentic current-job write/read limits unborrowable."""
    if _RUN_END is None:
        raise AdapterError("Frozen execution deadline required", "access")
    end = _RUN_END - EXPORT_TIMEOUT
    if _PUBLICATION_WINDOW is not None:
        end = min(end, _PUBLICATION_WINDOW["write"])
    return end


@contextlib.contextmanager
def operation_guard():
    """Bound complete source/infrastructure I/O, sharing any nested end."""
    global _OPERATION_END
    check_deadline()
    if _OPERATION_END is not None:
        if time.monotonic() >= _OPERATION_END:
            raise PhaseExpired()
        yield
        check_deadline()
        if time.monotonic() >= _OPERATION_END:
            raise PhaseExpired()
        return
    if (
        threading.current_thread() is not threading.main_thread()
        or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)
    ):
        raise AdapterError("Exclusive operation timer required", "access")
    end = min(active_deadline(), time.monotonic() + 30)
    previous = signal.getsignal(signal.SIGALRM)
    _OPERATION_END = end
    cancelling = False

    def exhausted(signum, frame):
        nonlocal cancelling
        # Closure has not completed, even if the body was already assessed.
        # Keep the physical owner pending without inventing a completion.
        for request_frame in _REQUEST_FRAMES:
            request_frame["assessed"] = False
        error = PhaseExpired()
        error._response_unassessed = True
        if cancelling:
            signal.setitimer(signal.ITIMER_REAL, 0)
        else:
            cancelling = True
            signal.setitimer(signal.ITIMER_REAL, 0.25)
        raise error

    try:
        signal.signal(signal.SIGALRM, exhausted)
        signal.setitimer(signal.ITIMER_REAL, end - time.monotonic())
        yield
        check_deadline()
        if time.monotonic() >= end:
            raise PhaseExpired()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _OPERATION_END = None


def bounded_infrastructure(function):
    @functools.wraps(function)
    def bounded(*args, **kwargs):
        with operation_guard():
            return function(*args, **kwargs)

    return bounded


def publication_window():
    """Tighten a copy; never renew or mutate the original job/token proof."""
    original = original_publication_window()
    if _RUN_END is None:
        return original
    check_deadline()
    end = min(active_deadline(), nonexport_end())
    return {
        **original,
        "write": min(original["write"], end),
        "read": min(original["read"], end),
    }


@contextlib.contextmanager
def publication_http_guard(deadline):
    if _RUN_END is not None:
        check_deadline()
        deadline = min(deadline, active_deadline(), _RUN_END)
    with original_publication_http_guard(deadline) as end:
        yield end


def ensure_failure_phase():
    """Own fallback reads AND writes until the unchanged run body exits."""
    global _FAILURE_CONTEXT
    if _PHASE_NAME == "failure":
        check_deadline()
        return
    if _PHASE_NAME is not None or _FAILURE_CONTEXT is not None:
        raise AdapterError("Failure phase cannot renew", "publish")
    context = phase("failure", 180)
    context.__enter__()
    _FAILURE_CONTEXT = context


def read_snapshot():
    global _INITIAL_SNAPSHOT_ATTEMPTED
    if _RUN_END is None:
        return original_read_snapshot()
    if _PHASE_NAME in ("snapshot", "publication", "failure"):
        check_deadline()
        return original_read_snapshot()
    if not _INITIAL_SNAPSHOT_ATTEMPTED:
        _INITIAL_SNAPSHOT_ATTEMPTED = True
        with phase("snapshot", 180):
            return original_read_snapshot()
    ensure_failure_phase()
    return original_read_snapshot()


def publish_files(snapshot, files):
    if _RUN_END is None:
        return original_publish_files(snapshot, files)
    if "data.json" not in files:
        ensure_failure_phase()
        return original_publish_files(snapshot, files)
    with phase("publication", 180):
        return original_publish_files(snapshot, files)


def run_body(publishing):
    """Close the single persistent fallback owner without changing its body."""
    global _FAILURE_CONTEXT
    try:
        return original_run_body(publishing)
    finally:
        if _FAILURE_CONTEXT is not None:
            context, _FAILURE_CONTEXT = _FAILURE_CONTEXT, None
            context.__exit__(*sys.exc_info())


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


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
    """Freeze entry admission; leave original publication alarms unopposed."""
    global _RUN_END, _ENTRY_MONOTONIC, _PHASE_SEEN, _FAILURE_CONTEXT
    global _INITIAL_SNAPSHOT_ATTEMPTED, _COLLECTION_LOCK_FD
    global _COLLECTION_LOCK_ID
    global _RESTORED, _STATE_TRUSTED, _LOCAL_FIRST_WIRE_VERIFIED
    global _STARTUP_QUIET_UNTIL
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
    _STARTUP_QUIET_UNTIL = None
    _ROBOTS.clear()
    _REP_TARGETS.clear()
    _REP_PROVENANCE.clear()
    _PHASE_SEEN = set()
    _FAILURE_CONTEXT = None
    _INITIAL_SNAPSHOT_ATTEMPTED = False
    _ENTRY_MONOTONIC = time.monotonic()
    _RUN_END = _ENTRY_MONOTONIC + RUN_TIMEOUT
    try:
        yield
    finally:
        _RUN_END = _ENTRY_MONOTONIC = None
        primary = sys.exc_info()[1]
        errors = []
        descriptor, identity = _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
        if descriptor is not None:
            try:
                verify_collection()
            except BaseException as error:
                journal_failure()
                errors.append(error)
        _COLLECTION_LOCK_FD = _COLLECTION_LOCK_ID = None
        if descriptor is not None:
            try:
                close_original_descriptor(descriptor, identity)
            except BaseException as error:
                journal_failure()
                errors.append(error)
        try:
            close_journal()
        except BaseException as error:
            journal_failure()
            errors.append(error)
        if _PUBLISHER_GATE_FAILED:
            with contextlib.suppress(BaseException):
                journal_invalidate_export()
            pacing_not_ready()
        if errors and primary is None:
            raise errors[0]


@contextlib.contextmanager
def phase(name, seconds):
    """Admit each phase once without borrowing its downstream reserves."""
    global _PHASE_END, _PHASE_NAME
    if _PHASE_END is not None or _RUN_END is None or name in _PHASE_SEEN:
        raise AdapterError("Invalid or repeated phase transition", "access")
    now = time.monotonic()
    limit = nonexport_end()
    if name == "proof":
        limit = min(_ENTRY_MONOTONIC + 30, limit - 720)
    elif name == "restore":
        limit -= 540
    elif name in ("snapshot", "collection"):
        limit -= 360
    elif name == "publication":
        limit -= 180
    elif name == "failure":
        pass
    elif name == "export":
        limit = _RUN_END
    else:
        raise AdapterError("Unknown execution phase", "access")
    end = min(now + seconds, limit)
    if end <= now:
        raise PhaseExpired()
    _PHASE_NAME, _PHASE_END = name, end
    _PHASE_SEEN.add(name)
    try:
        check_deadline()
        if name == "export":
            with operation_guard():
                yield
        else:
            yield
        check_deadline()
    finally:
        _PHASE_END = _PHASE_NAME = None


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def budget_file():
    """Create hosted state only; retain an existing mature local namespace."""
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    named_parent = os.stat(temporary, follow_symlinks=False)
    parent_id = (named_parent.st_dev, named_parent.st_ino)
    parent = os.open(
        temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    descriptor = identity = None
    try:
        if (os.fstat(parent).st_dev, os.fstat(parent).st_ino) != parent_id:
            raise AdapterError("Temporary parent changed", "access")
        leaf = FAMILY + "-" + str(os.getuid())
        created = False
        if os.environ.get("GITHUB_ACTIONS") == "true":
            try:
                os.mkdir(leaf, 0o700, dir_fd=parent)
                created = True
            except FileExistsError:
                pass
        linked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        identity = (linked.st_dev, linked.st_ino)
        descriptor = os.open(
            leaf,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_nlink < 2
            or (info.st_dev, info.st_ino) != identity
            or (current.st_dev, current.st_ino) != identity
        ):
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _STATE_INITIALIZING = created
        _STATE_DIR_FD, _STATE_DIR_ID = descriptor, identity
        directory = os.path.join(temporary, leaf)
        _STATE_PATH = os.path.join(directory, "state.json")
        _STATE_LOCK = os.path.join(directory, "state.lock")
        verify_state_directory()
    except BaseException:
        journal_failure()
        if descriptor is not None:
            with contextlib.suppress(BaseException):
                close_directory_descriptor(descriptor, identity)
        raise
    finally:
        close_directory_descriptor(parent, parent_id)


def journal_version(info):
    """Describe the bytes and metadata of one held journal generation."""
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


def journal_failure():
    """Close access without adopting a changed named journal."""
    global _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False


def verify_journal(check_generation=True):
    """Require the retained RW inode, current name and trusted generation."""
    try:
        verify_state_directory()
        if _JOURNAL_FD is None:
            raise AdapterError("Publisher journal is not retained", "access")
        info = os.fstat(_JOURNAL_FD)
        current = os.stat(
            "state.json", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != _JOURNAL_ID
            or (current.st_dev, current.st_ino) != _JOURNAL_ID
            or check_generation
            and journal_version(info) != _JOURNAL_VERSION
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        lock = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID:
            raise AdapterError("Publisher journal lock changed", "access")
        if check_generation:
            journal_unchanged()
        return info
    except BaseException:
        journal_failure()
        raise



def retain_journal():
    """Pin one journal after acquiring its stable lock; never retarget it."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_CREATED
    global _JOURNAL_BYTES
    if _JOURNAL_FD is not None:
        verify_journal()
        return
    try:
        try:
            descriptor = secure_file("state.json", os.O_RDWR)
        except FileNotFoundError:
            if not _STATE_INITIALIZING:
                raise AdapterError(
                    "Mature publisher state is missing", "access"
                )
            descriptor = secure_file(
                "state.json", os.O_RDWR | os.O_CREAT | os.O_EXCL
            )
            _JOURNAL_CREATED = True
        info = verify_original_descriptor(
            descriptor, _ACQUIRED_IDS[descriptor]
        )
        _JOURNAL_FD = descriptor
        _JOURNAL_ID = (info.st_dev, info.st_ino)
        _JOURNAL_VERSION = journal_version(info)
        generation, raw = journal_snapshot()
        if generation != _JOURNAL_VERSION:
            raise AdapterError("Initial journal generation changed", "access")
        _JOURNAL_BYTES = raw
        verify_journal()
        if _JOURNAL_CREATED:
            os.fsync(_STATE_DIR_FD)
    except BaseException:
        journal_failure()
        raise



def close_journal():
    """Close our retained descriptor; propagate an unhealthy close."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_LOCK_ID
    global _JOURNAL_CREATED, _JOURNAL_DIRTY
    global _JOURNAL_BYTES
    _JOURNAL_BYTES = None
    descriptor, identity = _JOURNAL_FD, _JOURNAL_ID
    _JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
    _JOURNAL_CREATED = _JOURNAL_DIRTY = False
    if descriptor is not None:
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Publisher journal FD was reused", "access")
            os.close(descriptor)
        except BaseException:
            journal_failure()
            raise



@contextlib.contextmanager
def locked_budget(*, verify_exit=False):
    """Retain the actual stable lock without relabelling policy body errors."""
    global _JOURNAL_LOCK_ID
    budget_file()
    descriptor = identity = None
    try:
        if _JOURNAL_LOCKS:
            original = next(iter(_JOURNAL_LOCKS))
            identity = _JOURNAL_LOCKS[original]
            verify_original_descriptor(original, identity)
            descriptor = os.dup(original)
            verify_original_descriptor(descriptor, identity)
        else:
            descriptor = secure_file(
                "state.lock",
                os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
            )
            identity = _ACQUIRED_IDS[descriptor]
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        verify_original_descriptor(descriptor, identity)
        current = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (
            identity != (current.st_dev, current.st_ino)
            or _JOURNAL_LOCK_ID is not None
            and identity != _JOURNAL_LOCK_ID
        ):
            raise AdapterError("Publisher stable lock changed", "access")
        _JOURNAL_LOCK_ID = identity
        _JOURNAL_LOCKS[descriptor] = identity
        retain_journal()
    except BaseException:
        journal_failure()
        if descriptor is not None and identity is not None:
            with contextlib.suppress(BaseException):
                close_original_descriptor(descriptor, identity)
        raise
    try:
        yield descriptor
        if verify_exit:
            verify_journal()
    except BaseException:
        if verify_exit:
            journal_failure()
        raise
    finally:
        primary = sys.exc_info()[1]
        _JOURNAL_LOCKS.pop(descriptor, None)
        try:
            close_original_descriptor(descriptor, identity)
        except BaseException:
            journal_failure()
            if primary is None:
                raise


def load_budget():
    """Read the retained inode, checking its generation after the read."""
    if not _JOURNAL_LOCKS:
        if _STATE_PATH is None:
            raise AdapterError("Publisher state is not initialized", "access")
        with locked_budget():
            return load_budget()
    try:
        verify_journal()
        _, raw = journal_snapshot()
        verify_journal()
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher state", "access")
        if not raw and _JOURNAL_CREATED and _STATE_INITIALIZING:
            return empty_budget(time.time())
        try:
            return validate_budget(json.loads(raw))
        except (ValueError, TypeError):
            raise AdapterError("Corrupt publisher state", "access") from None
    except BaseException:
        journal_failure()
        raise



def journal_io_authority():
    """Recheck acquired locks and current names without reading generation."""
    try:
        verify_state_directory()
        named = os.stat(
            "state.json", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (named.st_dev, named.st_ino) != _JOURNAL_ID:
            raise AdapterError("Publisher journal name changed", "access")
        held = list(_JOURNAL_LOCKS.items())
        if _COLLECTION_LOCK_FD is not None:
            held.append((_COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID))
        for descriptor, identity in held:
            info = os.fstat(descriptor)
            name = (
                "collection.lock"
                if descriptor == _COLLECTION_LOCK_FD
                else "state.lock"
            )
            current = os.stat(name, dir_fd=_STATE_DIR_FD, follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity
                or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
                != os.O_RDWR
            ):
                raise AdapterError("Acquired publisher lock changed", "access")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)

    except BaseException:
        journal_failure()
        raise


def journal_snapshot():
    """Read only the retained RW inode and one stable bounded generation."""

    def owned():
        info = os.fstat(_JOURNAL_FD)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink not in (0, 1)
            or (info.st_dev, info.st_ino) != _JOURNAL_ID
            or fcntl.fcntl(_JOURNAL_FD, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_RDWR
        ):
            raise AdapterError("Original RW journal changed", "access")
        return journal_version(info)

    journal_io_authority()
    before = owned()
    if before[2] > 16384:
        raise AdapterError("Oversized publisher state", "access")
    previous = None
    for read_pass in range(2):
        raw = b""
        after = before
        while len(raw) <= before[2]:
            journal_io_authority()
            if owned() != before:
                raise AdapterError("Journal read generation changed", "access")
            chunk = os.pread(_JOURNAL_FD, before[2] + 1 - len(raw), len(raw))
            journal_io_authority()
            after = owned()
            if before != after:
                raise AdapterError("Journal read generation changed", "access")
            raw += chunk
            if not chunk:
                break
        if len(raw) != after[2] or read_pass and raw != previous:
            raise AdapterError("Journal read generation changed", "access")
        previous = raw
    return after, raw


def journal_acknowledge(expected):
    """Advance own authority only after exact expected-byte readback."""
    global _JOURNAL_VERSION, _JOURNAL_BYTES
    generation, raw = journal_snapshot()
    if raw != expected:
        raise AdapterError("Own journal mutation was superseded", "access")
    _JOURNAL_VERSION, _JOURNAL_BYTES = generation, raw


def journal_unchanged():
    """Failure cleanup requires the last ACK, with no dirty exemption."""
    generation, raw = journal_snapshot()
    if generation != _JOURNAL_VERSION or raw != _JOURNAL_BYTES:
        raise AdapterError("Original journal generation changed", "access")


def save_budget(state):
    """Commit through the held inode; interrupted rewrites fail closed."""
    global _STATE_INITIALIZING, _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    global _JOURNAL_CREATED, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Fulbright Austria state persistence is already unhealthy", "access"
        )
    try:
        state = validate_budget(state)
        if (
            not state["in_flight"]
            and _ACTIVE_FLIGHT_ID is not None
            and (
                _COMPLETING_FLIGHT_ID != _ACTIVE_FLIGHT_ID
                or state["completed_flight_id"] != _ACTIVE_FLIGHT_ID
            )
        ):
            raise AdapterError(
                "Unproved Fulbright Austria completion write", "access"
            )
        payload = json.dumps(state, separators=(",", ":")).encode()
        if len(payload) > 16384 or not _JOURNAL_LOCKS:
            raise AdapterError(
                "Bounded locked journal commit required", "access"
            )
        verify_journal()
        _STATE_INITIALIZING = _JOURNAL_CREATED = False
        _JOURNAL_DIRTY = True
        journal_unchanged()
        os.ftruncate(_JOURNAL_FD, 0)
        journal_acknowledge(b"")
        verify_journal()
        os.fsync(_JOURNAL_FD)
        journal_acknowledge(b"")
        offset = 0
        while offset < len(payload):
            verify_journal()
            count = os.pwrite(_JOURNAL_FD, payload[offset:], offset)
            if type(count) is not int or not 0 < count <= len(payload) - offset:
                raise AdapterError("Incomplete publisher state write", "access")
            offset += count
            journal_acknowledge(payload[:offset])
        verify_journal()
        os.fsync(_JOURNAL_FD)
        journal_acknowledge(payload)
        verify_journal()
        _JOURNAL_DIRTY = False
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            _FAILED_BUDGET = copy.deepcopy(state)
        with contextlib.suppress(BaseException):
            invalidate_budget()
        raise



def verify_state_directory():
    """Bind the mature leaf to its retained descriptor before each operation."""
    info = os.fstat(_STATE_DIR_FD)
    current = os.stat(os.path.dirname(_STATE_PATH), follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_nlink < 2
        or (info.st_dev, info.st_ino) != _STATE_DIR_ID
        or (current.st_dev, current.st_ino) != _STATE_DIR_ID
    ):
        raise AdapterError(
            "Publisher state directory identity changed", "access"
        )


def secure_file(name, flags):
    """Acquire only a safe named file beneath the original leaf."""
    verify_state_directory()
    try:
        named = os.stat(name, dir_fd=_STATE_DIR_FD, follow_symlinks=False)
        expected = (named.st_dev, named.st_ino)
    except FileNotFoundError:
        expected = None
        if not flags & os.O_CREAT:
            raise
        flags |= os.O_EXCL
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=_STATE_DIR_FD
    )
    identity = expected
    try:
        held = os.fstat(descriptor)
        current = os.stat(name, dir_fd=_STATE_DIR_FD, follow_symlinks=False)
        acquired = (held.st_dev, held.st_ino)
        if (
            expected is not None
            and acquired != expected
            or acquired != (current.st_dev, current.st_ino)
            or not stat.S_ISREG(held.st_mode)
            or held.st_uid != os.getuid()
            or stat.S_IMODE(held.st_mode) != 0o600
            or held.st_nlink != 1
        ):
            raise AdapterError("Unsafe publisher acquisition", "access")
        identity = acquired
        _ACQUIRED_IDS[descriptor] = acquired
        return descriptor
    except BaseException:
        journal_failure()
        if identity is not None:
            with contextlib.suppress(BaseException):
                close_original_descriptor(descriptor, identity)
        raise


def retry_after_until(value, now):
    """Accept only a finite supported positive delay or aware future date."""
    if not isinstance(value, str):
        return None
    try:
        value = value.strip()
        if re.fullmatch(r"[0-9]+", value):
            if len(value) > 10:
                return None
            delay = int(value)
            until = now + delay
        else:
            parsed = parsedate_to_datetime(value)
            if parsed.utcoffset() is None:
                return None
            until = parsed.timestamp()
        if not numeric(until) or not now < until <= 253402300799:
            return None
        return until
    except (ValueError, TypeError, OverflowError):
        return None


def retain_publisher_hold():
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
        state["observed_at"] = time.time()
        save_budget(state)


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
    # A challenge redirect must not escape inspection through a wrong MIME.
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

    class ChallengeText(VisibleText):
        """Keep genuine unsuppressed HEAD titles as separate evidence."""

        def __init__(self):
            super().__init__()
            self.head_title = []
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
            if (
                tag == "head"
                and not self.head_seen
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
                data.strip()
                and self.head_depth is None
                and (not self.stack or not any(self.stack[-1][1:]))
                and not any(
                    frame[0] in {"head", "title"} for frame in self.stack
                )
            ):
                self.body_started = True
            if not any(frame[0] in {"head", "title"} for frame in self.stack):
                super().handle_data(data)
            if (
                self.head_depth is not None
                and not self.body_started
                and len(self.stack) == self.head_depth + 1
                and self.stack[-1][0] == "title"
                and self.stack[-2][0] == "head"
                and not any(self.stack[-1][1:])
            ):
                self.head_title.append(data)

    parser = ChallengeText()
    parser.feed(body.decode("utf-8", "replace"))
    text = " ".join(" ".join(parser.text + parser.head_title).lower().split())
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
    title = " ".join(" ".join(parser.head_title).lower().split())
    title_refusal = re.search(
        r"just a moment|attention required|access denied|captcha", title, re.I
    )
    if any(phrase in text for phrase in phrases) or title_refusal:
        retain_publisher_hold()
        raise PolicyStop("Visible publisher challenge or denial", "access")


def wait_inventory_page():
    """Separate inventory completion from ordinary physical completion."""
    with locked_budget():
        state = load_budget()
        target = state["last_inventory_completed"] + state["page_interval"]
        if _INVENTORY_COMPLETED_MONOTONIC is not None:
            target = max(
                target,
                time.time()
                + max(
                    0,
                    _INVENTORY_COMPLETED_MONOTONIC
                    + state["page_interval"]
                    - time.monotonic(),
                ),
            )
        while target > time.time():
            check_deadline()
            wait = target - time.time()
            if wait + 120 > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Inventory gap exceeds phase budget", "access"
                )
            if (
                time.time() + wait + 120
                > _COLLECTION_STARTED + COLLECTION_TIMEOUT
            ):
                raise AdapterError(
                    "Inventory gap exceeds collection budget", "access"
                )
            time.sleep(wait)


def validate_family_identity(run, expected=None):
    source = family_source(run)
    identity, attempt = run.get("id"), run.get("run_attempt")
    head = run.get("head_sha")
    if (
        run.get("workflow_id") != _WORKFLOW_ID
        or source != SOURCE_ID
        or type(identity) is not int
        or identity <= 0
        or type(attempt) is not int
        or attempt <= 0
        or run.get("repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or not isinstance(head, str)
        or not re.fullmatch(r"[0-9a-f]{40}", head)
        or expected is not None
        and (identity, attempt, source, head) != expected
    ):
        raise AdapterError("Untrusted family run identity", "access")
    return identity, attempt, source, head


def workflow_api(path):
    """Read bounded GitHub metadata with safe, actionable diagnostics."""
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    route = urllib.parse.urlsplit(path).path
    # Never include query strings, response bodies, signed URLs or credentials.
    route = re.sub(r"[^a-zA-Z0-9_./-]", "?", route)[:160]
    detail = "transport unavailable"
    final_status = None
    for attempt in range(3):
        check_deadline()
        retry = False
        final_status = None
        delay = attempt + 1
        try:
            separator = "&" if "?" in path else "?"
            status, response_headers, body = infrastructure_request_bytes(
                _WORKFLOW_GITHUB + path + separator + "_fresh="
                + str(time.time_ns()), headers=headers,
            )
            final_status = status
            detail = "HTTP " + str(status)
            for key in ("X-RateLimit-Remaining", "X-RateLimit-Reset"):
                value = response_headers.get(key, "")
                if (
                    isinstance(value, str)
                    and re.fullmatch(r"[0-9]{1,12}", value)
                ):
                    detail += " " + key + "=" + value
            if status == 200:
                try:
                    return json.loads(body)
                except (ValueError, UnicodeError):
                    detail += " invalid JSON"
            else:
                retry = status in (408, 429, 500, 502, 503, 504)
                retry_after = response_headers.get("Retry-After")
                if retry_after is not None:
                    # Never retry earlier than an advertised server hold.
                    if re.fullmatch(r"[0-9]{1,6}", retry_after):
                        delay = max(delay, int(retry_after))
                    else:
                        retry = False
        except (urllib.error.URLError, OSError) as error:
            detail = "transport " + type(error).__name__
            retry = True
        except AdapterError:
            detail = "bounded infrastructure read rejected"
        if not retry or attempt == 2:
            break
        remaining = min(active_deadline(), _RUN_END) - time.monotonic()
        if delay > 5 or remaining <= delay + 1:
            break
        time.sleep(delay)
    raise AdapterError(
        "Workflow pacing state unavailable: " + route + " " + detail,
        "access", final_status,
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


@bounded_infrastructure
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
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
            **({"Authorization": "Bearer " + token} if token else {}),
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
            or not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com")
            )
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
        status, _, body = infrastructure_request_bytes(
            destination, byte_limit=65536
        )
        if status != 200 or len(body) > 65536:
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
            if entry.file_size > 65536 or entry.flag_bits & 1:
                raise ValueError("Oversized or encrypted state")
            if entry.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("State symlink")
            raw = archive.read(entry)
        envelope = json.loads(raw)
        legacy_envelope = {
            "head_sha",
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "state",
        }
        modern_envelope = legacy_envelope | {"source_sha256", "state_sha256"}
        if not isinstance(envelope, dict) or set(envelope) not in (
            legacy_envelope, modern_envelope
        ):
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source, head = identity
        if (
            type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or envelope["repository"] != "YouthOpps/data-pipeline"
            or type(envelope["run_id"]) is not int
            or type(envelope["run_attempt"]) is not int
            or envelope["run_id"] != run_id
            or envelope["run_attempt"] != attempt
            or envelope["source"] != source
            or envelope["head_sha"] != head
        ):
            raise ValueError("Artifact ownership mismatch")
        state = validate_budget(envelope["state"])
        if set(envelope) == legacy_envelope:
            if (
                (run_id, attempt) != _NO_ACCESS_LAST_OWNER
                or artifact_id != _NO_ACCESS_ARTIFACT[0]
                or digest != _NO_ACCESS_ARTIFACT[1]
                or len(body) != 643
                or not {(identity, 1) for identity in _NO_ACCESS_ATTEMPTS}
                <= set(_NO_ACCESS_BOUNDS)
            ):
                raise ValueError("Unreviewed legacy artifact envelope")
        elif (
            envelope["state_sha256"] != canonical_state_digest(state)
            or envelope["source_sha256"] != authenticated_source_digest(head)
        ):
            raise ValueError("Modern source/state digest mismatch")
        return state
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def checked_artifact_path(path):
    """Validate the configured root before pinning its exact directory inode."""
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    info = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o022
        or info.st_mode & 0o700 != 0o700
    ):
        raise AdapterError("Unsafe pacing artifact directory", "access")
    return root, info


def verify_artifact_directory(root, parent):
    """Require the current pathname to identify the pinned safe directory."""
    current = os.stat(root, follow_symlinks=False)
    held = os.fstat(parent)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o022
        or current.st_mode & 0o700 != 0o700
        or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise AdapterError("Pacing artifact directory changed", "access")


def validate_owned_artifact(info):
    """Refuse unsafe objects without opening a blocking FIFO or changing it."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AdapterError("Unsafe existing pacing artifact", "access")


def verify_artifact_target(path, descriptor):
    """Check the current upload target against the exact open output inode."""
    root, _ = checked_artifact_path(path)
    current = os.stat(path, follow_symlinks=False)
    held = os.fstat(descriptor)
    validate_owned_artifact(current)
    validate_owned_artifact(held)
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        raise AdapterError("Pacing artifact output changed", "access")


def invalidate_artifact(parent, name, root=None):
    """Truncate the verified owned inode before fallible signaling or unlink."""
    if root is not None:
        verify_artifact_directory(root, parent)
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    validate_owned_artifact(before)
    descriptor = os.open(
        name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        validate_owned_artifact(info)
        if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or (
            info.st_dev,
            info.st_ino,
        ) != (current.st_dev, current.st_ino):
            raise AdapterError("Pacing artifact output changed", "access")
        if root is not None:
            verify_artifact_directory(root, parent)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        pacing_not_ready()
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if root is not None:
            verify_artifact_directory(root, parent)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Pacing artifact output changed", "access")
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def artifact_output(path, cleanup_only=False):
    """Pin root and output through readiness and healthy context completion."""
    global _PUBLISHER_GATE_FAILED
    parent = descriptor = None
    try:
        root, before = checked_artifact_path(path)
        parent = os.open(
            root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        held = os.fstat(parent)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Pacing artifact directory changed", "access")
        verify_artifact_directory(root, parent)
        name = os.path.basename(path)
        invalidate_artifact(parent, name, root)
        if cleanup_only:
            yield None
            verify_artifact_directory(root, parent)
            return
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion cannot be exported", "access"
            )
        verify_artifact_directory(root, parent)
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        yield descriptor
        verify_artifact_directory(root, parent)
        verify_artifact_target(path, descriptor)
        os.fsync(descriptor)
        os.fsync(parent)
        verify_artifact_directory(root, parent)
        verify_artifact_target(path, descriptor)
    except BaseException:
        _PUBLISHER_GATE_FAILED = True
        if descriptor is not None:
            # The pinned owned output is invalidated even if its parent moved.
            with contextlib.suppress(Exception):
                validate_owned_artifact(os.fstat(descriptor))
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
        pacing_not_ready()
        if descriptor is not None and parent is not None:
            with contextlib.suppress(Exception):
                verify_artifact_directory(root, parent)
                verify_artifact_target(path, descriptor)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def active_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded Fulbright Austria execution required", "access"
        )
    return _PHASE_END if _PHASE_END is not None else nonexport_end()


def check_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded Fulbright Austria execution required", "access"
        )
    if time.monotonic() >= min(active_deadline(), _RUN_END):
        raise PhaseExpired()


def request_timeout(publisher=False):
    check_deadline()
    remaining = min(active_deadline(), _RUN_END) - time.monotonic()
    if _OPERATION_END is not None:
        remaining = min(remaining, _OPERATION_END - time.monotonic())
    if remaining <= 0:
        raise PhaseExpired()
    return min(30, remaining)


def workflow_mode_guard():
    """Check workflow state before history, journal creation or access."""
    result = workflow_api("/actions/workflows/fetch-at-fulbright-austria.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-at-fulbright-austria.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "Fulbright Austria workflow is not serialized for this mode",
            "access",
        )
    if hosted:
        current_run_identity()
        head = os.environ.get("GITHUB_SHA", "")
        if (
            not re.fullmatch(r"[0-9a-f]{40}", head)
            or os.environ.get("GITHUB_WORKFLOW_SHA") != head
            or os.environ.get("GITHUB_WORKFLOW_REF")
            != "YouthOpps/data-pipeline/.github/workflows/"
            "fetch-at-fulbright-austria.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable Fulbright Austria workflow head", "access"
            )


_HOSTED_NATIVE_MAX = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/405#issuecomment-6953305768',
 'family': 'youthopps-at-fulbright-austria-publisher-v1',
 'native_evidence': 'https://github.com/YouthOpps/data-pipeline/issues/405#issuecomment-6952734662',
 'native_source_sha256': '68b154764849108006bb6bda4b62e9cebc7e91d476c0b4039f979c8135824914',
 'native_state_sha256': 'a0076937f4a3cf7294ab8e59a5b82fba5fd2c37316f4420d478c163c353bc5f1',
 'schema': 1,
 'source': 'at-fulbright-austria',
 'state': {'blocked': False,
           'completed': 1794929282.3704839,
           'completed_flight_id': 'c8d24e56a67b820bf9da55f50fe1f978',
           'completed_request_id': 'bb3b2a2e8080ec1bc97be46b960b0c22',
           'family': 'youthopps-at-fulbright-austria-publisher-v1',
           'flight_id': 'c8d24e56a67b820bf9da55f50fe1f978',
           'flight_kind': 'policy',
           'flight_started': 1794929267.6617048,
           'in_flight': False,
           'interval': 12.2,
           'last_inventory_completed': 1794928644.9968448,
           'legacy_rate_history_unknown': True,
           'not_before': 1794929294.570484,
           'observed_at': 1794929282.3704839,
           'page_interval': 30,
           'rate_limit_strikes': 0,
           'rate_limit_until': 0,
           'request_id': 'bb3b2a2e8080ec1bc97be46b960b0c22',
           'request_inventory': False,
           'request_outcome': 'response_assessed',
           'request_started': 1794929279.860538,
           'schema': 3,
           'starts': [1792114979.6843238,
                      1792115578.7532814,
                      1792115590.9510272,
                      1792115592.623577,
                      1792115593.181354,
                      1792115653.181354,
                      1792327903.7775905,
                      1792327916.7558966,
                      1792327930.0525205,
                      1792327943.259059,
                      1792327956.234929,
                      1794926160.687577,
                      1794926161.8460443,
                      1794926175.7937508,
                      1794926175.7962532,
                      1794928392.7827308,
                      1794928394.2664964,
                      1794928409.5399675,
                      1794928409.542951,
                      1794928443.6442888,
                      1794928443.6472805,
                      1794928479.6541991,
                      1794928479.658871,
                      1794928512.5533834,
                      1794928512.5573878,
                      1794928544.8254757,
                      1794928544.8300467,
                      1794928577.3754246,
                      1794928577.3798318,
                      1794928611.1929555,
                      1794928611.1953337,
                      1794928643.2352607,
                      1794928643.2393808,
                      1794928657.2104096,
                      1794928657.2224312,
                      1794928671.504371,
                      1794928671.5069056,
                      1794928685.6529796,
                      1794928685.6615365,
                      1794928699.4255219,
                      1794928699.4388864,
                      1794928712.698347,
                      1794928712.7039754,
                      1794928726.2857926,
                      1794928726.2956285,
                      1794928739.9523158,
                      1794928739.9631293,
                      1794928753.7288244,
                      1794928753.7349062,
                      1794928766.9968305,
                      1794928767.0073137,
                      1794928780.954102,
                      1794928780.9646041,
                      1794928796.495899,
                      1794928796.5054636,
                      1794928810.9693992,
                      1794928810.9812982,
                      1794928825.1506722,
                      1794928825.1540797,
                      1794928839.985337,
                      1794928839.989402,
                      1794928854.0041986,
                      1794928854.0071902,
                      1794928869.4228778,
                      1794928869.4347908,
                      1794928882.9046936,
                      1794928882.9217007,
                      1794928896.9904377,
                      1794928896.9983616,
                      1794928911.2632692,
                      1794928911.2733624,
                      1794928927.6198802,
                      1794928927.6229248,
                      1794928940.9977543,
                      1794928941.0019648,
                      1794928954.8838592,
                      1794928954.8894904,
                      1794928969.0424237,
                      1794928969.0514178,
                      1794928982.5340455,
                      1794928982.5374355,
                      1794928995.766673,
                      1794928995.7700913,
                      1794929009.885093,
                      1794929009.8918264,
                      1794929023.88823,
                      1794929023.9063547,
                      1794929039.0778456,
                      1794929039.0881429,
                      1794929053.370646,
                      1794929053.3768249,
                      1794929067.861295,
                      1794929067.865063,
                      1794929083.2792993,
                      1794929083.2881482,
                      1794929097.301659,
                      1794929097.3127737,
                      1794929111.7462595,
                      1794929111.7493184,
                      1794929126.575473,
                      1794929126.5857344,
                      1794929141.2883947,
                      1794929141.2936702,
                      1794929154.4892726,
                      1794929154.4968996,
                      1794929167.9948294,
                      1794929168.006715,
                      1794929183.5750737,
                      1794929183.5891805,
                      1794929197.4217956,
                      1794929197.4319386,
                      1794929211.0155437,
                      1794929211.0183818,
                      1794929224.8819132,
                      1794929224.8849187,
                      1794929238.422056,
                      1794929238.4262109,
                      1794929252.2446663,
                      1794929252.2488575,
                      1794929265.5011764,
                      1794929265.515243,
                      1794929279.860538,
                      1794929279.8637707]},
 'state_sha256': '9582090a1ad7f83788a7ba7eca6b5386ebecfd7147f0b9aceedfb4c4af019afd'}


def production_maintenance_state(incoming):
    """Merge native restrictions after exact newest-owned restoration only."""
    incoming = validate_budget(incoming)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return incoming
    approval = _HOSTED_NATIVE_MAX
    try:
        if (
            not isinstance(approval, dict)
            or set(approval) != {
                "schema", "family", "source", "evidence", "state",
                "native_evidence", "native_source_sha256", "native_state_sha256", "state_sha256",
            }
            or type(approval["schema"]) is not int
            or approval["schema"] != 1
            or approval["family"] != FAMILY
            or approval["source"] != SOURCE_ID
            or approval["native_evidence"] !=
            "https://github.com/YouthOpps/data-pipeline/issues/405"
            "#issuecomment-6952734662"
            or approval["native_source_sha256"] !=
            "68b154764849108006bb6bda4b62e9cebc7e91d476c0b4039f979c8135824914"
            or approval["native_state_sha256"] !=
            "a0076937f4a3cf7294ab8e59a5b82fba5fd2c37316f4420d478c163c353bc5f1"
            or not isinstance(approval["evidence"], str)
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/405"
                r"#issuecomment-[1-9][0-9]*", approval["evidence"],
            )
        ):
            raise ValueError("Unreviewed full-native MAX decision")
        state = validate_budget(approval["state"])
        if canonical_state_digest(state) != approval["state_sha256"]:
            raise ValueError("Reviewed full-native MAX bytes changed")
        return merge_budgets(incoming, state, time.time())
    except Exception:
        raise AdapterError(
            "Reviewed source-committed Austria native MAX is OFF or invalid",
            "access",
        ) from None



def restored_state():
    """Restore the actual modern owner; legacy approvals are not fallback."""
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Fulbright Austria family history missing", "access")
    if all(
        str(row[2]) in _LEGACY_JOB_PINS
        and row[3] == _LEGACY_JOB_PINS[str(row[2])]["attempt"]
        and row[5] == _LEGACY_JOB_PINS[str(row[2])]["head"]
        for row in _HISTORY_COMPLETED
    ):
        raise AdapterError(
            "Established Fulbright Austria requires modern state", "access"
        )
    historical = {
        key: row for key, row in _NO_ACCESS_BOUNDS.items()
        if key[0] in _NO_ACCESS_ATTEMPTS
    }
    if historical:
        if set(historical) != {
            (identity, 1) for identity in _NO_ACCESS_ATTEMPTS
        }:
            raise AdapterError(
                "Reviewed no-access ancestry incomplete", "access"
            )
        if (
            latest[:2] != _NO_ACCESS_LAST_OWNER
            and _HISTORY_COMPLETED[-1][0]
            <= max(row[0] for row in historical.values())
        ):
            raise AdapterError("Actual post-gap owner missing", "access")
    run_id, attempt, source, head = latest
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or len(artifacts) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(artifacts)
        or any(not isinstance(a, dict) for a in artifacts)
    ):
        raise AdapterError(
            "Incomplete Fulbright Austria artifact inventory", "access"
        )
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest Fulbright Austria artifact unavailable", "access"
        )
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != head
        or not numeric(artifact.get("size_in_bytes"))
        or artifact["size_in_bytes"] > 65536
        or not isinstance(artifact.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"])
    ):
        raise AdapterError(
            "Invalid Fulbright Austria artifact binding", "access"
        )
    if (run_id, attempt) == _NO_ACCESS_LAST_OWNER:
        if (
            not {(identity, 1) for identity in _NO_ACCESS_ATTEMPTS}
            <= set(_NO_ACCESS_BOUNDS)
            or (artifact["id"], artifact["digest"]) != _NO_ACCESS_ARTIFACT
            or artifact["size_in_bytes"] != 643
        ):
            raise AdapterError(
                "Reviewed no-access ancestry incomplete", "access"
            )
    return production_maintenance_state(
        preserve_no_access_bounds(
            download_inert_artifact(latest, artifact["id"], artifact["digest"])
        )
    )


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    global _COLLECTION_LOCK_ID, _STARTUP_QUIET_UNTIL
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    if os.environ.get("GITHUB_ACTIONS") != "true":
        require_existing_local_state()
    workflow_mode_guard()
    current_job_context()
    incoming = restored_state()
    # No directory, lock or empty durable budget exists before reviewed state.
    if incoming["in_flight"]:
        raise AdapterError(
            "Owned Fulbright Austria artifact is unsettled", "access"
        )
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        descriptor = secure_file(
            "collection.lock",
            os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
        )
        identity = _ACQUIRED_IDS[descriptor]
        try:
            verify_original_descriptor(descriptor, identity)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Collection lock FD was reused", "access")
        except BaseException:
            journal_failure()
            with contextlib.suppress(BaseException):
                close_original_descriptor(descriptor, identity)
            raise
        _COLLECTION_LOCK_ID = identity
        _COLLECTION_LOCK_FD = descriptor
    with locked_budget():
        now = time.time()
        local = load_budget()
        if local["in_flight"]:
            raise AdapterError(
                "Local Fulbright Austria flight is unsettled", "access"
            )
        combined = merge_budgets(local, incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        save_budget(combined)
    _COLLECTION_STARTED = time.time()
    _STARTUP_QUIET_UNTIL = time.monotonic() + 60
    _RESTORED = _STATE_TRUSTED = True


def collect():
    """Run the unchanged complete programme parser after durable restoration."""
    if os.environ.get("DATA_SOURCE_TOKEN") and not _PUBLISHING:
        raise AdapterError(
            "Nonpublishing collection refuses publication token", "access"
        )
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RUN_END is None:
        with execution():
            return collect()
    if not _RESTORED:
        with phase("restore", 180):
            prepare_collection()
    try:
        with phase("collection", COLLECTION_TIMEOUT):
            return collect_programme()
    except PhaseExpired:
        raise AdapterError(
            "Fulbright Austria collection phase exhausted", "fetch"
        ) from None


def export_family_artifact():
    path = os.environ.get("AUSTRIA_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        journal_invalidate_export()
        pacing_not_ready()
        return

    def payload():
        run_id, attempt = current_run_identity()
        with locked_budget():
            state = load_budget()
            if state["in_flight"]:
                raise AdapterError(
                    "Unsettled publisher owner cannot export", "access"
                )
        return {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "head_sha": os.environ["GITHUB_SHA"],
            "source_sha256": authenticated_source_digest(
                os.environ["GITHUB_SHA"], executing=True
            ),
            "state_sha256": canonical_state_digest(state),
            "state": state,
        }

    journal_export_envelope(path, payload, atomic=False, signal=True)


def invalidate_budget():
    """Invalidate only the original last-acknowledged generation."""
    if _JOURNAL_FD is None:
        return
    journal_unchanged()
    os.ftruncate(_JOURNAL_FD, 0)
    journal_acknowledge(b"")
    journal_unchanged()
    os.fsync(_JOURNAL_FD)
    journal_acknowledge(b"")



def selected_robots_policies(groups):
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [g for g in groups if agent in g["agents"]]
    wildcard = [g for g in groups if "*" in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    # Current identity selection must not be displaced by a legacy grant.
    return [current or wildcard] + ([legacy] if legacy else [])


def workflow_output_signal(ready):
    """Use the retained owned-output guard for step readiness."""
    journal_output_signal("true" if ready else "false")


def pacing_not_ready():
    """Best effort signal; pinned artifact truncation always precedes this."""
    with contextlib.suppress(Exception):
        workflow_output_signal(False)


def observe_current_run(expected):
    """Bound transient run propagation without extending any job deadline."""
    run_id, attempt, _, _ = expected
    path = f"/actions/runs/{run_id}/attempts/{attempt}"
    for observation in range(4):
        check_deadline()
        run = workflow_api(path)
        validate_family_identity(run, expected)
        status = run.get("status")
        if status == "in_progress":
            return run
        if status not in {"queued", "requested", "waiting", "pending"}:
            raise AdapterError(
                "Current run is terminal or has an unknown status", "access"
            )
        if observation == 3:
            raise AdapterError(
                "Current run remained nonexecuting after four observations",
                "access",
            )
        check_deadline()
        time.sleep(2)
        check_deadline()
    raise AssertionError("Unreachable current run observation")


def current_job_context():
    """Bind the executing source job and retain its actual30-minute deadline."""
    global _RUN_END
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    run_id, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    expected = (run_id, attempt, SOURCE_ID, head)
    run = observe_current_run(expected)
    if (
        run.get("status") != "in_progress"
        or os.environ.get("GITHUB_JOB") != "collect"
    ):
        raise AdapterError(
            "Fulbright Austria current job is not executing", "access"
        )
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        type(result.get("total_count")) is not int
        or result["total_count"] != 1
        or not isinstance(jobs, list)
        or len(jobs) != 1
    ):
        raise AdapterError(
            "Fulbright Austria current job inventory incomplete", "access"
        )
    job = jobs[0]
    if (
        not isinstance(job, dict)
        or type(job.get("id")) is not int
        or job["id"] <= 0
        or job.get("run_id") != run_id
        or job.get("run_attempt") != attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("name") != "collect"
        or job.get("status") != "in_progress"
    ):
        raise AdapterError(
            "Fulbright Austria current job ownership mismatch", "access"
        )
    try:
        started = datetime.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.utcoffset() is None:
            raise ValueError("Missing job timezone")
        elapsed = time.time() - started.timestamp()
        if not -1 <= elapsed < 1800 - EXPORT_TIMEOUT:
            raise ValueError("Job budget exhausted")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AdapterError(
            "Untrusted Fulbright Austria job clock", "access"
        ) from None
    _RUN_END = min(_RUN_END, time.monotonic() + 1800 - max(elapsed, 0) - 2)
    check_deadline()


class PreRequestDeferral(AdapterError):
    """A verified healthy timed guard reserved no publisher transaction."""


class PolicyStop(AdapterError):
    """A fully assessed response or durable explicit refusal ends this run."""

    policy_retained = True


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA,
        "family": FAMILY,
        "observed_at": now,
        "not_before": 0,
        "starts": [],
        "blocked": False,
        "interval": 12.2,
        "completed": 0,
        "page_interval": 30,
        "last_inventory_completed": 0,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "legacy_rate_history_unknown": True,
        "in_flight": False,
        "flight_id": None,
        "flight_started": 0,
        "flight_kind": None,
        "completed_flight_id": None,
        "request_id": None,
        "request_started": 0,
        "request_inventory": False,
        "completed_request_id": None,
        "request_outcome": None,
    }


def validate_budget(state):
    if (
        not isinstance(state, dict)
        or set(state) != set(empty_budget(0))
        or type(state.get("schema")) is not int
        or state["schema"] != _STATE_SCHEMA
        or state["family"] != FAMILY
        or type(state["blocked"]) is not bool
        or state["legacy_rate_history_unknown"] is not True
        or type(state["in_flight"]) is not bool
        or type(state["request_inventory"]) is not bool
    ):
        raise AdapterError("Untrusted publisher state schema", "access")
    fields = (
        "observed_at",
        "not_before",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_until",
        "flight_started",
        "request_started",
    )
    if any(not numeric(state[k]) or state[k] > 253402300799 for k in fields):
        raise AdapterError("Unsupported publisher state clock", "access")
    if (
        state["interval"] < 12.2
        or state["page_interval"] < 30
        or type(state["rate_limit_strikes"]) is not int
        or not 0 <= state["rate_limit_strikes"] <= 100
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 512
        or any(not numeric(t) or t > 253402300799 for t in state["starts"])
        or state["starts"] != sorted(set(state["starts"]))
    ):
        raise AdapterError("Invalid publisher pacing constraints", "access")
    for key in (
        "flight_id",
        "completed_flight_id",
        "request_id",
        "completed_request_id",
    ):
        if state[key] is not None and (
            not isinstance(state[key], str)
            or not re.fullmatch(r"[0-9a-f]{32}", state[key])
        ):
            raise AdapterError(
                "Invalid publisher transaction identity", "access"
            )
    if (state["flight_id"] is None) != (state["flight_kind"] is None):
        raise AdapterError("Missing publisher owner kind", "access")
    if state["flight_kind"] not in (None, "policy", "physical"):
        raise AdapterError("Invalid publisher transaction kind", "access")
    if state["request_outcome"] not in (
        None,
        "reserved",
        "response_assessed",
        "no_response",
        "policy_only",
    ):
        raise AdapterError("Invalid request assessment state", "access")
    if state["in_flight"]:
        if state["flight_id"] is None or state["flight_kind"] is None:
            raise AdapterError("Missing pending transaction identity", "access")
    elif (
        state["flight_id"] != state["completed_flight_id"]
        or state["flight_started"] > state["completed"]
        or state["request_id"] != state["completed_request_id"]
        or state["request_started"] > state["completed"]
        or state["request_outcome"] == "reserved"
    ):
        raise AdapterError(
            "Unproved publisher transaction completion", "access"
        )
    return state


def publisher_healthy(state):
    if _PUBLISHER_GATE_FAILED or (
        state["in_flight"] and state["flight_id"] != _ACTIVE_FLIGHT_ID
    ):
        raise AdapterError("Unsettled publisher transaction retained", "access")


def fail_policy(state=None):
    """Retain unassessed response ownership without healing receipts."""
    global _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    _PUBLISHER_GATE_FAILED = True
    if state is not None:
        _FAILED_BUDGET = copy.deepcopy(state)
    elif _FAILED_BUDGET is None:
        with contextlib.suppress(BaseException):
            _FAILED_BUDGET = copy.deepcopy(load_budget())
    error = sys.exc_info()[1]
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if (
            getattr(error, "_header429_unassessed", False)
            or getattr(error, "_response_unassessed", False)
        ):
            try:
                verify_journal()
                retained = load_budget()
                if (
                    retained["in_flight"]
                    and retained["flight_id"] == _ACTIVE_FLIGHT_ID
                ):
                    return
            except BaseException:
                pass
            break
        error = error.__context__
    with contextlib.suppress(BaseException):
        invalidate_budget()


def begin_transaction(kind):
    global _ACTIVE_FLIGHT_ID
    check_deadline()
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Healthy restored publisher state required", "access"
        )
    deferred = None
    with locked_budget(verify_exit=True):
        state = load_budget()
        if state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Publisher state requires reviewed recovery", "access"
            )
        now = time.time()
        if state["rate_limit_strikes"] >= 100:
            raise AdapterError("Publisher rate counter exhausted", "access")
        ready = max(state["not_before"], state["completed"] + state["interval"])
        if state["rate_limit_until"] > now:
            deferred = "Retained timed publisher cooldown"
        elif ready + 120 > now + (active_deadline() - time.monotonic()):
            deferred = "Known publisher guard exceeds phase budget"
        if deferred is None:
            ident = os.urandom(16).hex()
            if ident == state["flight_id"]:
                raise AdapterError(
                    "Publisher transaction identity collision", "access"
                )
            state.update(
                in_flight=True,
                flight_id=ident,
                flight_started=now,
                flight_kind=kind,
                observed_at=now,
            )
            # Keep the owner across retries, redirects and REP assessment.
            save_budget(state)
            _ACTIVE_FLIGHT_ID = ident
    if deferred is not None:
        # A failed generation check or lock close above remains a hard error.
        # Only a clean, unreserved timed decision can retain exportable state.
        raise PreRequestDeferral(deferred, "access")



def durable_transaction(kind):
    def decorate(function):
        @functools.wraps(function)
        def invoke(url, *args, **kwargs):
            global _TRANSACTION_DEPTH, _ACTIVE_FLIGHT_ID
            global _COMPLETING_FLIGHT_ID
            if not is_publisher(url):
                return function(url, *args, **kwargs)
            if _PUBLISHER_GATE_FAILED:
                raise AdapterError(
                    "Publisher policy failure is sticky", "access"
                )
            outer = _TRANSACTION_DEPTH == 0
            if outer:
                begin_transaction(kind)
            _TRANSACTION_DEPTH += 1
            settled = False
            try:
                result = function(url, *args, **kwargs)
                settled = True
                return result
            except PolicyStop:
                settled = not _PUBLISHER_GATE_FAILED
                raise
            finally:
                _TRANSACTION_DEPTH -= 1
                if outer:
                    try:
                        if not settled or _PUBLISHER_GATE_FAILED:
                            fail_policy()
                            if settled:
                                raise AdapterError(
                                    "Unhealthy policy cannot return data",
                                    "access",
                                )
                        else:
                            with locked_budget():
                                state = load_budget()
                                publisher_healthy(state)
                                if (
                                    not state["in_flight"]
                                    or state["flight_id"] != _ACTIVE_FLIGHT_ID
                                ):
                                    raise AdapterError(
                                        "Publisher owner changed", "access"
                                    )
                                now = time.time()
                                state.update(
                                    in_flight=False,
                                    completed_flight_id=_ACTIVE_FLIGHT_ID,
                                    completed=max(state["completed"], now),
                                    observed_at=now,
                                )
                                state["not_before"] = max(
                                    state["not_before"],
                                    state["completed"] + state["interval"],
                                )
                                _COMPLETING_FLIGHT_ID = _ACTIVE_FLIGHT_ID
                                save_budget(state)
                            _ACTIVE_FLIGHT_ID = None
                    except BaseException:
                        fail_policy()
                        raise
                    finally:
                        _COMPLETING_FLIGHT_ID = None

        return invoke

    return decorate


def pace():
    global _PUBLISHER_INTERVAL, _ACTIVE_REQUEST_ID
    if _PUBLISHER_GATE_FAILED or _ACTIVE_FLIGHT_ID is None:
        raise AdapterError(
            "Reserved publisher policy transaction required", "access"
        )
    check_deadline()
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        if not state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Publisher access state is not healthy", "access"
            )
        now = time.time()
        if (
            state["rate_limit_until"] > now
            or state["rate_limit_strikes"] >= 100
        ):
            raise AdapterError("Publisher cooldown/counter retained", "access")
        starts = [t for t in state["starts"] if t > now - 60]
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"], 12.2)
        target = max(
            now, state["not_before"], state["completed"] + _PUBLISHER_INTERVAL
        )
        if starts:
            target = max(target, starts[-1] + _PUBLISHER_INTERVAL)
        if len(starts) >= 5:
            target = max(target, starts[-5] + 60)
        while True:
            check_deadline()
            wait = max(
                0,
                target - time.time(),
                _STARTUP_QUIET_UNTIL - time.monotonic()
                if _STARTUP_QUIET_UNTIL is not None else 0,
            )
            if _PUBLISHER_COMPLETED_MONOTONIC is not None:
                wait = max(
                    wait,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                )
            if wait + 120 > active_deadline() - time.monotonic():
                raise AdapterError(
                    "Publisher pacing exceeds remaining phase", "access"
                )
            if wait <= 0:
                break
            time.sleep(wait)
        now = time.time()
        if now < target:
            raise AdapterError("Publisher clock moved backwards", "access")
        ident = os.urandom(16).hex()
        if ident == state["request_id"]:
            raise AdapterError("Physical request identity collision", "access")
        state.update(
            starts=[t for t in starts if t > now - 60] + [now],
            interval=_PUBLISHER_INTERVAL,
            observed_at=now,
            request_id=ident,
            request_started=now,
            request_outcome="reserved",
        )
        state["not_before"] = max(state["not_before"], now + state["interval"])
        save_budget(state)
        _ACTIVE_REQUEST_ID = ident


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


@contextlib.contextmanager
def publisher_attempt(inventory=False):
    global _PUBLISHER_COMPLETED_MONOTONIC, _INVENTORY_COMPLETED_MONOTONIC
    global _ACTIVE_REQUEST_ID
    frame = {"started": False, "received": False, "assessed": False}
    _REQUEST_FRAMES.append(frame)
    ordinary_no_response = False
    try:
        yield frame
    except PolicyStop:
        frame["assessed"] = True
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        ordinary_no_response = (
            frame["started"]
            and not frame["received"]
            and retryable_connection_error(error)
            and not isinstance(error, urllib.error.HTTPError)
            and not isinstance(getattr(error, "reason", error), ssl.SSLError)
            and not _PUBLISHER_GATE_FAILED
        )
        raise
    finally:
        if not _REQUEST_FRAMES or _REQUEST_FRAMES.pop() is not frame:
            fail_policy()
        if not frame["assessed"] and not ordinary_no_response:
            fail_policy()
        if frame["started"]:
            _PUBLISHER_COMPLETED_MONOTONIC = time.monotonic()
        if not _PUBLISHER_GATE_FAILED:
            try:
                with locked_budget():
                    state = load_budget()
                    publisher_healthy(state)
                    if state["request_id"] != _ACTIVE_REQUEST_ID:
                        raise AdapterError(
                            "Physical request identity changed", "access"
                        )
                    now = time.time()
                    state.update(
                        completed=now,
                        observed_at=now,
                        completed_request_id=_ACTIVE_REQUEST_ID,
                        request_outcome=(
                            "policy_only"
                            if not frame["started"]
                            else (
                                "response_assessed"
                                if frame["assessed"]
                                else "no_response"
                            )
                        ),
                    )
                    state["not_before"] = max(
                        state["not_before"], now + state["interval"]
                    )
                    if inventory and frame["started"]:
                        state["last_inventory_completed"] = now
                        _INVENTORY_COMPLETED_MONOTONIC = time.monotonic()
                    # Physical completion NEVER clears the enclosing owner.
                    save_budget(state)
                _ACTIVE_REQUEST_ID = None
            except BaseException:
                fail_policy()
                raise


def record_retry_after(value, status=None):
    """Persist adaptive 429 constraints before stronger headers."""
    try:
        values = value if isinstance(value, (list, tuple)) else [value]
        now = time.time()
        with locked_budget():
            state = load_budget()
            publisher_healthy(state)
            if status == 429:
                strikes = state["rate_limit_strikes"] + 1
                if strikes > 100:
                    raise AdapterError("Rate counter exhausted", "access")
                # Keep each representable stronger constraint even when a
                # different exponential constraint cannot fit this schema.
                unsupported = False
                constraints = (
                    ("interval", state["interval"] * 2),
                    ("page_interval", state["page_interval"] * 2),
                    ("rate_limit_until", now + 1800),
                    ("interval", 12.2 * 2 ** strikes),
                    ("page_interval", 30 * 2 ** strikes),
                    ("rate_limit_until", now + 1800 * 2 ** (strikes - 1)),
                )
                for key, constraint in constraints:
                    if not numeric(constraint) or constraint > 253402300799:
                        unsupported = True
                    else:
                        state[key] = max(state[key], constraint)
                state.update(rate_limit_strikes=strikes, observed_at=now)
                state["not_before"] = max(
                    state["not_before"], state["rate_limit_until"]
                )
                save_budget(state)
                if unsupported:
                    raise AdapterError("Unrepresentable rate hold", "access")
            header_error = None
            supported = []
            date_pattern = (
                r"[A-Za-z]{3},\s*[0-9]{1,2}\s+[A-Za-z]{3}\s+"
                r"[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2}\s+GMT"
            )
            for raw in values:
                if isinstance(raw, str) and "," in raw and not re.fullmatch(
                    date_pattern, raw.strip()
                ):
                    # Preserve every independently recognized positive MAX,
                    # while ambiguous framing still prevents settlement.
                    supported.extend(match[1] for match in re.finditer(
                        r"(?:^|,\s*)(" + date_pattern
                        + r"|[0-9]+)(?=\s*(?:,|$))", raw.strip()
                    ))
                    header_error = AdapterError(
                        "Ambiguous Retry-After list", "access"
                    )
                else:
                    supported.append(raw)
            for raw in supported:
                try:
                    if not isinstance(raw, str):
                        continue
                    text = raw.strip()
                    if re.fullmatch(r"[0-9]+", text):
                        if len(text) > 12:
                            raise AdapterError(
                                "Unrepresentable Retry-After delay", "access"
                            )
                        until = now + int(text)
                        if not numeric(until) or until > 253402300799:
                            raise AdapterError(
                                "Unrepresentable Retry-After delay", "access"
                            )
                    else:
                        until = retry_after_until(text, now)
                        if until is None and re.match(
                            r"^[A-Za-z]{3},\s*[0-9]{1,2}\s+[A-Za-z]{3}\s+"
                            r"[0-9]{4,}\s+[0-9:]+\s+GMT$", text
                        ):
                            try:
                                parsed = parsedate_to_datetime(text)
                                if parsed.utcoffset() is None:
                                    raise ValueError()
                                parsed.timestamp()
                            except (ValueError, TypeError, OverflowError):
                                raise AdapterError(
                                    "Unrepresentable Retry-After date", "access"
                                ) from None
                    if until is not None and until > now:
                        state["rate_limit_until"] = max(
                            state["rate_limit_until"], until
                        )
                        state["not_before"] = max(state["not_before"], until)
                        state["observed_at"] = now
                        save_budget(state)
                except AdapterError as error:
                    header_error = error
            if header_error is not None:
                raise header_error
    except BaseException as error:
        error._response_unassessed = True
        fail_policy()
        raise



def merge_budgets(local, incoming, now):
    local, incoming = validate_budget(local), validate_budget(incoming)
    result = copy.deepcopy(
        max((local, incoming), key=lambda st: st["completed"])
    )
    for key in (
        "observed_at",
        "not_before",
        "interval",
        "completed",
        "page_interval",
        "last_inventory_completed",
        "rate_limit_strikes",
        "rate_limit_until",
    ):
        result[key] = max(local[key], incoming[key])
    result["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    result["blocked"] = local["blocked"] or incoming["blocked"]
    result["legacy_rate_history_unknown"] = (
        local["legacy_rate_history_unknown"]
        or incoming["legacy_rate_history_unknown"]
    )
    pending = [st for st in (local, incoming) if st["in_flight"]]
    if pending:
        if len({st["flight_id"] for st in pending}) != 1:
            raise AdapterError("Conflicting pending publisher owners", "access")
        owner = max(pending, key=lambda st: st["observed_at"])
        for key in (
            "flight_id",
            "flight_started",
            "flight_kind",
            "completed_flight_id",
            "request_id",
            "request_started",
            "request_inventory",
            "completed_request_id",
            "request_outcome",
        ):
            result[key] = owner[key]
    result["in_flight"] = bool(pending)
    return validate_budget(result)


def is_publisher(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix(
            "www."
        ) == "fulbright.at"
    except (TypeError, ValueError):
        return False


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower()
        not in {"fulbright.at", "www.fulbright.at"}
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError(
            "Unreviewed Fulbright Austria source route", "access"
        )
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def is_inventory(url):
    """The sitemap and discovered directory pages are physical inventories."""
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return (
        path == "/sitemap"
        or re.fullmatch(
            r"/programs/in-(?:austria|the-usa)/"
            r"(?:students|scholars|teaching-assistants)",
            path,
        )
        is not None
        or path.startswith("/programs/us-eu-")
        and "/grant-details/" not in path
    )


@bounded_infrastructure
def infrastructure_request_bytes(
    url, *, headers=None, method="GET", payload=None, byte_limit=30_000_000
):
    """Bounded workflow/artifact reads do not create a publisher allowance."""
    if is_publisher(url):
        raise AdapterError(
            "Publisher cannot use infrastructure transport", "access"
        )
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    signed_storage = host.endswith(
        (".blob.core.windows.net", ".actions.githubusercontent.com")
    )
    if (
        parsed.scheme != "https" or parsed.username or parsed.password
        or parsed.port or parsed.fragment
        or method != "GET" or payload is not None
        or not (
            url.startswith(_WORKFLOW_GITHUB + "/")
            or signed_storage
        )
    ):
        raise AdapterError("Unreviewed infrastructure destination", "access")
    request_headers = dict(headers or {})
    if signed_storage:
        request_headers = {
            key: value for key, value in request_headers.items()
            if key.lower() != "authorization"
        }
    request = urllib.request.Request(
        url, method="GET",
        headers={**request_headers, "User-Agent": _USER_AGENT},
    )
    try:
        response = _OPENER.open(request, timeout=request_timeout())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise AdapterError("Infrastructure response exceeds bound", "fetch")
        return response.status, response.headers, body


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
@durable_transaction("physical")
def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    inventory=False,
    deadline=None,
    retry_jitter=0,
):
    """One physical source attempt; original publication remains GH6+RA."""
    global _LOCAL_FIRST_WIRE_VERIFIED
    if not is_publisher(url):
        if (
            urllib.parse.urlsplit(url).hostname or ""
        ).lower() != "api.github.com":
            raise AdapterError("Unreviewed publication API host", "publish")
        return legacy_request_bytes(
            url,
            interval=interval,
            method=method,
            headers=headers,
            payload=payload,
            deadline=deadline,
            retry_jitter=retry_jitter,
        )
    if deadline is not None or retry_jitter:
        raise AdapterError(
            "Publication options cannot alter source access", "access"
        )
    check_deadline()
    if (
        _PHASE_NAME != "collection"
        or not _STATE_TRUSTED
        or _PUBLISHER_GATE_FAILED
    ):
        raise AdapterError("Healthy source collection phase required", "access")
    current = public_url(url)
    parsed = urllib.parse.urlsplit(current)
    if (
        method != "GET"
        or payload is not None
        or not numeric(interval)
        or robots
        and (parsed.path != "/robots.txt" or inventory)
    ):
        raise AdapterError("Invalid source physical request purpose", "access")
    if not robots and parsed.path == "/robots.txt":
        raise AdapterError(
            "Literal REP requires verified robots purpose", "access"
        )
    origin = parsed.scheme + "://" + parsed.netloc
    if not robots and origin not in _ROBOTS:
        check_robots(current)
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        state["interval"] = max(state["interval"], interval, 12.2)
        state["request_inventory"] = bool(inventory)
        state["observed_at"] = time.time()
        save_budget(state)
    if inventory:
        wait_inventory_page()
    pace()
    request_headers = {
        k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"
    }
    request_headers["User-Agent"] = _USER_AGENT
    request = urllib.request.Request(
        current, method="GET", headers=request_headers
    )
    if (
        _STARTUP_QUIET_UNTIL is None
        or time.monotonic() < _STARTUP_QUIET_UNTIL
    ):
        raise AdapterError("Startup quiet period not satisfied", "access")
    with publisher_attempt(inventory) as frame, operation_guard():
        if not robots:
            apply_robots_policy(current, _ROBOTS[origin])
        if not _LOCAL_FIRST_WIRE_VERIFIED:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                workflow_mode_guard()
            _LOCAL_FIRST_WIRE_VERIFIED = True
        if (
            _STARTUP_QUIET_UNTIL is None
            or time.monotonic() < _STARTUP_QUIET_UNTIL
        ):
            raise AdapterError("Publisher startup quiet incomplete", "access")
        try:
            verify_journal()
            verify_collection()
            frame["started"] = True
            response = _OPENER.open(request, timeout=request_timeout(True))
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if isinstance(getattr(error, "reason", error), ssl.SSLError):
                raise AdapterError(
                    "Publisher TLS verification/transport failed", "fetch"
                ) from None
            raise
        frame["received"] = True
        # Persist known response constraints before fallible header/context IO.
        try:
            if response.status == 429:
                record_retry_after([], 429)
            if not robots and response.status in (401, 403, 451):
                retain_publisher_hold()
        except BaseException as primary:
            primary._response_unassessed = True
            try:
                # Still under the same non-renewable operation deadline.
                response.close()
            except BaseException as close_error:
                primary._response_close_error = type(close_error).__name__
            raise
        with header_rate_limit_response(response):
            status, response_headers = response.status, response.headers
            record_retry_after(
                response_headers.get_all("Retry-After", []),
                None if status == 429 else status
            )
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            lengths = response_headers.get_all("Content-Length", [])
            if (
                len(lengths) > 1
                or lengths and response_headers.get_all("Transfer-Encoding", [])
                or lengths and (
                    not lengths[0].isdigit() or int(lengths[0]) != len(body)
                )
            ):
                raise AdapterError("Incomplete source response length", "fetch")
            if not robots or status in (200, 429):
                reject_visible_challenge(
                    body, response_headers, challenge_only=status == 429
                )
            if robots and status == 200:
                assess_robots_response(origin, body, response_headers)
            if robots:
                caller_origin, target = _REP_TARGETS.get(
                    origin, (origin, SOURCE_URL)
                )
                _REP_PROVENANCE[caller_origin] = {
                    "response_url": current,
                    "status": status,
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "body_bytes": len(body),
                    "target": target,
                }
            frame["assessed"] = True
            if status == 429 or not robots and status in (401, 403, 451):
                raise PolicyStop(
                    "Publisher stopped: HTTP " + str(status), "access", status
                )
            return status, response_headers, body


@durable_transaction("policy")
def fetch_source(url, min_interval=6, *, evidence=None, inventory=None):
    """Preserve original retry/hop bounds and complete response evidence."""
    original = urllib.parse.urlsplit(url)
    robots = original.path == "/robots.txt"
    inventory = not robots and (
        is_inventory(url) if inventory is None else inventory
    )

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
        public_url(value)
        if robots and parsed.path != "/robots.txt":
            raise AdapterError("Robots purpose cannot follow content", "access")

    for attempt in range(2):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(
                    current,
                    interval=max(min_interval, 12.2),
                    robots=robots,
                    inventory=inventory,
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(current, headers["Location"])
                    checked(current)
                    if not robots:
                        min_interval = max(min_interval, check_robots(current))
                    else:
                        old_origin = original.scheme + "://" + original.netloc
                        next_origin = (
                            urllib.parse.urlsplit(current).scheme
                            + "://"
                            + urllib.parse.urlsplit(current).netloc
                        )
                        _REP_TARGETS[next_origin] = _REP_TARGETS[old_origin]
                    continue
                if status != 200:
                    raise PolicyStop(
                        f"Publisher returned HTTP {status}", "fetch", status
                    )
                document = body.decode(headers.get_content_charset() or "utf-8")
                if evidence is not None:
                    parsed = urllib.parse.urlsplit(current)
                    evidence.update(
                        url=urllib.parse.urlunsplit(
                            (
                                parsed.scheme,
                                parsed.hostname or "",
                                parsed.path,
                                "",
                                "",
                            )
                        ),
                        status=status,
                        bytes=len(body),
                        sha256=hashlib.sha256(body).hexdigest(),
                    )
                return document
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if _PUBLISHER_GATE_FAILED or not retryable_connection_error(error):
                raise
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error), "fetch"
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


@durable_transaction("policy")
def check_robots(url):
    """Preserve per-origin REP caching and protect every target decision."""
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if origin not in _ROBOTS:
        _REP_TARGETS[origin] = (origin, url)
        try:
            fetch_source(origin + "/robots.txt", min_interval=12.2)
        except PolicyStop as error:
            if (
                error.status is None
                or not 400 <= error.status < 500
                or error.status == 429
            ):
                raise
            _ROBOTS.setdefault(origin, [])
        if origin not in _ROBOTS:
            raise AdapterError(
                "Robots response target policy was not assessed", "access"
            )
    apply_robots_policy(url, _ROBOTS[origin])
    return max(
        [12.2]
        + [
            g["delay"]
            for policy in selected_robots_policies(_ROBOTS[origin])
            for g in policy
        ]
    )


def parse_robots(body):
    """Tie policy groups and numeric errors to actual REP bytes."""
    text = body.decode("utf-8", "strict")
    if re.search(r"<\s*/?\s*[A-Za-z!][^>]*>", text):
        raise AdapterError("Markup is not a robots document", "access")
    digest = hashlib.sha256(body).hexdigest()

    def fresh():
        return {
            "agents": [],
            "rules": [],
            "delay": 0,
            "numeric_invalid": False,
            "directives_seen": False,
            "body_sha256": digest,
        }

    groups, group = [], fresh()
    directives = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        directives.append(line.lower())
        key, separator, value = line.partition(":")
        if not separator:
            raise AdapterError("Malformed robots line", "access")
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if not value or not re.fullmatch(r"[A-Za-z0-9_*/.-]+", value):
                raise AdapterError("Malformed robots agent", "access")
            if group["directives_seen"]:
                groups.append(group)
                group = fresh()
            group["agents"].append(value.lower())
        elif (
            key in ("allow", "disallow", "crawl-delay", "request-rate")
            and not group["agents"]
        ):
            raise AdapterError("Robots directive lacks agent", "access")
        elif key in ("allow", "disallow"):
            group["directives_seen"] = True
            group["rules"].append((key, value))
        elif key in ("crawl-delay", "request-rate"):
            group["directives_seen"] = True
            try:
                decimal = r"(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
                if key == "crawl-delay":
                    if not re.fullmatch(decimal, value):
                        raise ValueError("Malformed delay")
                    delay = float(value)
                else:
                    match = re.fullmatch(f"({decimal})/({decimal})", value)
                    if not match:
                        raise ValueError("Malformed rate")
                    requests, seconds = map(float, match.groups())
                    if (
                        not numeric(requests)
                        or requests <= 0
                        or not numeric(seconds)
                        or seconds <= 0
                    ):
                        raise ValueError("Unsupported rate")
                    delay = seconds / requests
                if not numeric(delay) or delay > 253402300799:
                    raise ValueError("Unsupported rate clock")
                group["delay"] = max(group["delay"], delay)
            except (ValueError, TypeError, OverflowError, ZeroDivisionError):
                group["numeric_invalid"] = True
    groups.append(group)
    if (
        directives
        and not any(g["agents"] for g in groups)
        and not all(line.startswith("sitemap:") for line in directives)
    ):
        raise AdapterError("Robots policy unavailable or invalid", "access")
    return groups


def apply_robots_policy(url, groups):
    global _PUBLISHER_INTERVAL
    if _ACTIVE_FLIGHT_ID is None or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Reserved publisher policy owner required", "access")
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    policies = selected_robots_policies(groups)
    if any(group["numeric_invalid"] for policy in policies for group in policy):
        raise AdapterError(
            "Unsupported applicable robots numeric policy", "access"
        )
    for selected in policies:
        matches = []
        for group in selected:
            for action, pattern in group["rules"]:
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
                            action == "allow",
                        )
                    )
        if matches and not max(matches)[1]:
            retain_publisher_hold()
            raise PolicyStop(
                "Robots policy disallows this source endpoint", "access"
            )
    delay = max(
        [12.2] + [g["delay"] for selected in policies for g in selected]
    )
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        _PUBLISHER_INTERVAL = max(_PUBLISHER_INTERVAL, state["interval"], delay)
        state["interval"] = _PUBLISHER_INTERVAL
        state["observed_at"] = time.time()
        save_budget(state)


def main():
    global _PUBLISHING, _RUN_END
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish", action="store_true", help="Publish only this source"
    )
    args = parser.parse_args()
    _PUBLISHING = args.publish
    with execution():
        try:
            if args.publish:
                with phase("proof", 30):
                    window = original_publication_window()
                _RUN_END = min(_RUN_END, window["read"])
                check_deadline()
            with phase("restore", 180):
                prepare_collection()
            return run_body(args.publish)
        finally:
            with phase("export", EXPORT_TIMEOUT):
                export_family_artifact()


def completed_identity(run, expected=None):
    """Use exact immutable attempts/jobs; retain the pinned jobless cancel."""
    identity = validate_family_identity(run, expected)
    run_id, attempt, source, head = identity
    exact = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(exact, identity)
    if exact.get("status") != "completed":
        raise AdapterError("Prior family attempt is incomplete", "access")
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = result.get("jobs")
    if (
        not isinstance(jobs, list)
        or len(jobs) > 100
        or type(result.get("total_count")) is not int
        or result["total_count"] != len(jobs)
    ):
        raise AdapterError("Incomplete exact family jobs", "access")
    if not jobs:
        pin = _LEGACY_JOB_PINS.get(str(run_id))
        if (
            not pin
            or pin["jobs"]
            or attempt != pin["attempt"]
            or head != pin["head"]
            or pin["conclusion"] != "cancelled"
            or exact.get("conclusion") != "cancelled"
        ):
            raise AdapterError("Unreviewed jobless family attempt", "access")
        _HISTORY_JOBLESS.append(identity)
        return None
    began, finished, seen = [], [], set()
    try:
        for job in jobs:
            if (
                type(job.get("id")) is not int
                or job["id"] <= 0
                or job["id"] in seen
                or type(job.get("run_id")) is not int
                or job["run_id"] != run_id
                or type(job.get("run_attempt")) is not int
                or job["run_attempt"] != attempt
                or job.get("head_sha") != head
                or job.get("head_branch") != "main"
                or job.get("status") != "completed"
            ):
                raise ValueError("Invalid exact attempt binding")
            seen.add(job["id"])
            times = [
                datetime.fromisoformat(job[k].replace("Z", "+00:00"))
                for k in ("started_at", "completed_at")
            ]
            if any(value.utcoffset() is None for value in times):
                raise ValueError("Missing job timezone")
            start, end = (value.timestamp() for value in times)
            if not 0 <= start <= end <= time.time() + 1:
                raise ValueError("Invalid actual job chronology")
            began.append(start)
            finished.append(end)
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AdapterError(
            "Untrusted completed family jobs", "access"
        ) from None
    row = (max(finished), min(began), *identity)
    if expected is None and proven_no_access_attempt(exact, jobs):
        _NO_ACCESS_BOUNDS[(run_id, attempt)] = row
    return row


def scan_family_history():
    global _HISTORY_COMPLETED, _HISTORY_JOBLESS
    _HISTORY_JOBLESS = []
    _NO_ACCESS_BOUNDS.clear()
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    candidates, seen = [], set()
    total, scanned, current_seen = None, 0, False

    def remember(run, expected=None):
        row = completed_identity(run, expected)
        if row is not None and (row[2], row[3]) not in _NO_ACCESS_BOUNDS:
            candidates.append(row)

    for page in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-at-fulbright-austria.yml/runs"
            + f"?per_page=100&page={page}"
        )
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(runs, list)
            or len(runs) > 100
        ):
            raise AdapterError("Invalid family workflow inventory", "access")
        if total is None:
            total = count
        elif count != total:
            raise InventoryRace(
                "Family history changed during restore", "access"
            )
        scanned += len(runs)
        for run in runs:
            identity, attempt, source, head = validate_family_identity(run)
            if identity in seen:
                raise AdapterError("Duplicate workflow run", "access")
            seen.add(identity)
            if current and identity == current[0]:
                if (identity, attempt) != current or head != os.environ[
                    "GITHUB_SHA"
                ]:
                    raise AdapterError(
                        "Current immutable attempt mismatch", "access"
                    )
                current_seen = True
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    remember(prior, (identity, attempt - 1, source, head))
                continue
            if run.get("status") != "completed":
                if not hosted or not queued_sibling_without_access(run):
                    raise AdapterError(
                        "Another family collector is active", "access"
                    )
                continue
            remember(run)
        if scanned >= total:
            if scanned != total or hosted and not current_seen:
                raise AdapterError("Incomplete family run inventory", "access")
            _HISTORY_COMPLETED = sorted(candidates)
            if (
                len(candidates) > 1
                and _HISTORY_COMPLETED[-1][0] == _HISTORY_COMPLETED[-2][0]
            ):
                raise AdapterError(
                    "Ambiguous actual family chronology", "access"
                )
            return _HISTORY_COMPLETED[-1][2:] if candidates else None
        if len(runs) != 100:
            raise AdapterError("Truncated family history", "access")
    raise AdapterError(
        "Family history exceeds reviewed metadata bound", "access"
    )


def legacy_publisher_files_absent():
    """Read only exact old host entries; never initialize or reset them."""
    parent = os.open(
        tempfile.gettempdir(), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        name = SOURCE_ID + "-pacing-" + str(os.getuid())
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=parent,
            )
        except FileNotFoundError:
            return True
        try:
            info = os.fstat(descriptor)
            linked = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise AdapterError(
                    "Unsafe legacy publisher directory", "access"
                )
            for host in ("fulbright.at",):
                key = hashlib.sha256(host.encode()).hexdigest()
                try:
                    os.stat(key, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                # The reviewed observation was absence, not a zero numeric
                # budget. Any newly present entry needs fresh provenance QA.
                raise AdapterError(
                    "Legacy publisher entry needs review", "access"
                )
            return True
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def legacy_transition_state():
    approval = _LEGACY_TRANSITION
    if not isinstance(approval, dict) or set(approval) != {
        "schema",
        "family",
        "evidence",
        "history_sha256",
        "state",
        "legacy_publisher_files_absent",
    }:
        raise AdapterError(
            "Explicit reviewed legacy transition required", "access"
        )
    digest = hashlib.sha256(
        json.dumps(
            _LEGACY_JOB_PINS, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if (
        type(approval["schema"]) is not int
        or approval["schema"] != 1
        or approval["family"] != FAMILY
        or approval["legacy_publisher_files_absent"] is not True
        or not isinstance(approval["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/287"
            r"#issuecomment-[1-9][0-9]*",
            approval["evidence"],
        )
        or approval["history_sha256"] != digest
        or len(_HISTORY_COMPLETED) + len(_HISTORY_JOBLESS)
        != len(_LEGACY_JOB_PINS)
    ):
        raise AdapterError("Untrusted legacy transition approval", "access")
    actual = {str(row[2]): row for row in _HISTORY_COMPLETED}
    jobless = {str(row[0]): row for row in _HISTORY_JOBLESS}
    if set(actual) | set(jobless) != set(_LEGACY_JOB_PINS) or set(actual) & set(
        jobless
    ):
        raise AdapterError("Legacy exact attempt set changed", "access")
    bounds = []
    for key, pin in _LEGACY_JOB_PINS.items():
        if not pin["jobs"]:
            if jobless.get(key) != (
                int(key),
                pin["attempt"],
                SOURCE_ID,
                pin["head"],
            ):
                raise AdapterError(
                    "Pinned zero-job cancellation changed", "access"
                )
        else:
            starts = [
                datetime.fromisoformat(
                    j["started_at"].replace("Z", "+00:00")
                ).timestamp()
                for j in pin["jobs"]
            ]
            ends = [
                datetime.fromisoformat(
                    j["completed_at"].replace("Z", "+00:00")
                ).timestamp()
                for j in pin["jobs"]
            ]
            expected = (
                max(ends),
                min(starts),
                int(key),
                pin["attempt"],
                SOURCE_ID,
                pin["head"],
            )
            if actual.get(key) != expected:
                raise AdapterError("Legacy actual job bounds changed", "access")
            bounds.extend(ends)
        jobs = workflow_api(
            f"/actions/runs/{key}/attempts/{pin['attempt']}/jobs?per_page=100"
        )
        if (
            jobs.get("total_count") != len(pin["jobs"])
            or not isinstance(jobs.get("jobs"), list)
            or len(jobs["jobs"]) != len(pin["jobs"])
            or any(
                {k: job.get(k) for k in old} != old
                for job, old in zip(
                    sorted(jobs["jobs"], key=lambda j: j.get("id", 0)),
                    sorted(pin["jobs"], key=lambda j: j["id"]),
                )
            )
        ):
            raise AdapterError("Legacy exact jobs changed", "access")
        artifacts = workflow_api(f"/actions/runs/{key}/artifacts?per_page=100")
        if (
            artifacts.get("total_count") != 0
            or artifacts.get("artifacts") != []
        ):
            raise AdapterError("Legacy artifact provenance changed", "access")
    legacy_publisher_files_absent()
    state = validate_budget(approval["state"])
    if (
        not state["legacy_rate_history_unknown"]
        or state["in_flight"]
        or not set(bounds).issubset(state["starts"])
        or state["completed"] < max(bounds)
        or state["last_inventory_completed"] < max(bounds)
        or state["not_before"] < max(bounds) + 60
    ):
        raise AdapterError("Legacy complete conservative MAX missing", "access")
    return state


class HeaderRateLimitStop(PolicyStop):
    """A header-only HTTP 429 stop assessed after safe closure."""


@contextlib.contextmanager
def header_rate_limit_response(response):
    """Settle source responses only after verified body and closure."""
    stop = None
    entered = False
    try:
        with response:
            entered = True
            try:
                yield
            except PolicyStop as error:
                stop = error
    except BaseException as error:
        error._response_unassessed = True
        error._header429_unassessed = True
        with contextlib.suppress(BaseException):
            journal_failure()
        if not entered:
            try:
                response.close()
            except BaseException as close_error:
                error._response_close_error = type(close_error).__name__
        raise
    if stop is not None:
        raise stop


def require_existing_local_state():
    """Pin the mature local journal before any remote restoration."""
    try:
        budget_file()
        with locked_budget():
            state = load_budget()
            if state["in_flight"]:
                raise AdapterError(
                    "Local Fulbright Austria flight is unsettled", "access"
                )
    except BaseException:
        journal_failure()
        raise


def assess_robots_response(origin, body, headers):
    """Verify a complete REP document before settling policy ownership."""
    try:
        types = headers.get_all("Content-Type", [])
        if not types or any(
            value.split(";", 1)[0].strip().lower() != "text/plain"
            for value in types
        ):
            raise AdapterError("Unsupported robots content type", "access")
        groups = parse_robots(body)
        target_origin, target = _REP_TARGETS.get(
            origin, (origin, SOURCE_URL)
        )
        apply_robots_policy(target, groups)
        _ROBOTS[target_origin] = groups
    except PolicyStop:
        raise
    except BaseException as error:
        error._response_unassessed = True
        raise


def close_legacy_descriptor(descriptor, identity, directory=False):
    """Release only an actually acquired legacy FD, preserving its primary."""
    primary = sys.exc_info()[1]
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            raise AdapterError("Legacy descriptor was recycled", "access")
        if directory and not stat.S_ISDIR(info.st_mode):
            raise AdapterError("Legacy parent descriptor changed", "access")
        if not directory and not stat.S_ISREG(info.st_mode):
            raise AdapterError("Legacy journal descriptor changed", "access")
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def output_identity(descriptor, expected, directory=False, unlinked=False):
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != expected
        or info.st_uid != os.getuid()
        or directory
        and (
            not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o022
            or info.st_mode & 0o700 != 0o700
        )
        or not directory
        and (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o022
            or info.st_nlink not in ((0, 1) if unlinked else (1,))
        )
    ):
        raise AdapterError("Original owned output changed", "access")
    return info


def output_close(descriptor, expected, directory=False):
    primary = sys.exc_info()[1]
    try:
        output_identity(descriptor, expected, directory, True)
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def output_parent(parent, root, expected):
    output_identity(parent, expected, True)
    current = os.stat(root, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != expected:
        raise AdapterError("Owned output directory changed", "access")


def output_poison(descriptor, expected):
    info = output_identity(descriptor, expected, unlinked=True)
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise AdapterError("Unsafe owned artifact permissions", "access")
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)


def output_open_parent(path):
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Explicit runner temporary root required", "access")
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    named_root = os.stat(root, follow_symlinks=False)
    expected_root = (named_root.st_dev, named_root.st_ino)
    parent = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    info = os.fstat(parent)
    identity = expected_root
    try:
        output_parent(parent, root, identity)
    except BaseException:
        with contextlib.suppress(BaseException):
            output_close(parent, identity, True)
        raise
    return parent, root, identity


def journal_invalidate_owned_output(path):
    """Invalidate a verified owned inode before fallible cleanup/signals."""
    parent, root, parent_id = output_open_parent(path)
    descriptor = None
    identity = None
    try:
        try:
            named_before = os.stat(
                os.path.basename(path), dir_fd=parent, follow_symlinks=False
            )
            identity = (named_before.st_dev, named_before.st_ino)
            descriptor = os.open(
                os.path.basename(path),
                os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
        except FileNotFoundError:
            return
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            raise AdapterError("Owned invalidation FD changed", "access")
        output_identity(descriptor, identity)
        current = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Owned output identity changed", "access")
        output_parent(parent, root, parent_id)
        output_poison(descriptor, identity)
        output_parent(parent, root, parent_id)
        current = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(os.path.basename(path), dir_fd=parent)
            os.fsync(parent)
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, expected, directory in (
            (descriptor, identity, False),
            (parent, parent_id, True),
        ):
            if fd is not None:
                try:
                    output_close(fd, expected, directory)
                except BaseException as error:
                    errors.append(error)
        if errors and primary is None:
            raise errors[0]


def journal_output_signal(value):
    """Bind fresh step output and all nested descriptors through close."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Fresh workflow output required", "access")
        return
    configured = os.environ.get("RUNNER_TEMP", "")
    root = os.path.realpath(configured)
    if not configured or not os.path.isabs(path):
        raise AdapterError("Unsafe workflow output", "access")
    relative = os.path.relpath(path, root)
    parts = relative.split(os.sep)
    if any(p in ("", ".", "..") for p in parts):
        raise AdapterError("Workflow output outside runner root", "access")
    parent, root, root_id = output_open_parent(
        os.path.join(root, ".output-binding")
    )
    handles = [(parent, root_id, True, None, None)]
    try:
        output_parent(parent, root, root_id)
        for name in parts[:-1]:
            named_before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            expected_before = (named_before.st_dev, named_before.st_ino)
            fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            info = os.fstat(fd)
            identity = expected_before
            if (info.st_dev, info.st_ino) != identity:
                journal_failure()
                raise AdapterError(
                    "Step directory FD changed during open", "access"
                )
            handles.append((fd, identity, True, parent, name))
            output_identity(fd, identity, True)
            parent = fd
        named_before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        expected_before = (named_before.st_dev, named_before.st_ino)
        descriptor = os.open(
            parts[-1],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        identity = expected_before
        if (info.st_dev, info.st_ino) != identity:
            journal_failure()
            raise AdapterError(
                "Workflow output FD changed during open", "access"
            )
        handles.append((descriptor, identity, False, parent, parts[-1]))
        if value == "true" and info.st_size != 0:
            raise AdapterError("Fresh workflow output required", "access")

        def verify():
            output_parent(handles[0][0], root, root_id)
            for fd, expected, directory, base, name in handles[1:]:
                output_identity(fd, expected, directory)
                named = os.stat(name, dir_fd=base, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != expected:
                    raise AdapterError("Workflow output path changed", "access")

        verify()
        line = ("pacing_ready=" + value + "\n").encode()
        if os.write(descriptor, line) != len(line):
            raise AdapterError("Incomplete workflow output signal", "access")
        verify()
        os.fsync(descriptor)
        verify()
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for fd, expected, directory, _, _ in reversed(handles):
            try:
                output_close(fd, expected, directory)
            except BaseException as error:
                errors.append(error)
        if errors and primary is None:
            raise errors[0]


def journal_export_envelope(path, envelope, atomic=False, signal=False):
    """Retain owned output references through finalization and all releases."""
    parent, root, parent_id = output_open_parent(path)
    name = os.path.basename(path)
    handles = []
    named_id = None
    temporary = None
    primary = None
    try:
        try:
            named_before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            old_expected = (named_before.st_dev, named_before.st_ino)
            old = os.open(
                name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            old = None
        if old is not None:
            info = os.fstat(old)
            named_id = old_expected
            named_after = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) != named_id or (
                named_after.st_dev,
                named_after.st_ino,
            ) != named_id:
                journal_failure()
                raise AdapterError(
                    "Prior output changed during acquisition", "access"
                )
            handles.append((old, named_id))
            output_identity(old, named_id)
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise AdapterError("Unsafe old pacing artifact", "access")
            if not atomic:
                output_poison(old, named_id)
                output_parent(parent, root, parent_id)
                named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if (named.st_dev, named.st_ino) != named_id:
                    raise AdapterError("Old artifact was replaced", "access")
                os.unlink(name, dir_fd=parent)
                named_id = None
        check_deadline()
        journal_before_wire()
        if callable(envelope):
            envelope = envelope()
        temporary = ".owned-pacing-" + os.urandom(16).hex() if atomic else name
        descriptor = os.open(
            temporary,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        named_new = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
        identity = (named_new.st_dev, named_new.st_ino)
        if (info.st_dev, info.st_ino) != identity:
            journal_failure()
            raise AdapterError(
                "New output FD changed during acquisition", "access"
            )
        handles.append((descriptor, identity))
        guard = os.dup(descriptor)
        handles.append((guard, identity))
        raw = json.dumps(envelope, separators=(",", ":")).encode()
        if len(raw) > 65536:
            raise AdapterError("Oversized pacing artifact", "access")
        offset = 0
        while offset < len(raw):
            check_deadline()
            output_parent(parent, root, parent_id)
            output_identity(descriptor, identity)
            written = os.pwrite(descriptor, raw[offset:], offset)
            output_identity(descriptor, identity)
            if written <= 0:
                raise AdapterError("Incomplete pacing artifact write", "access")
            offset += written
        output_identity(descriptor, identity)
        os.ftruncate(descriptor, len(raw))
        output_identity(descriptor, identity)
        os.fsync(descriptor)
        before = output_identity(guard, identity)
        actual = os.pread(guard, 65537, 0)
        after = output_identity(guard, identity)
        if actual != raw or journal_version(before) != journal_version(after):
            raise AdapterError("Pacing artifact changed during read", "access")
        output_parent(parent, root, parent_id)
        if atomic:
            try:
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if (
                None if current is None else (current.st_dev, current.st_ino)
            ) != named_id:
                raise AdapterError("Prior pacing artifact changed", "access")
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            temporary = None
        os.fsync(parent)
        journal_before_wire()
        if signal:
            journal_output_signal("true")
        journal_before_wire()
        output_parent(parent, root, parent_id)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        output_identity(descriptor, identity)
        output_identity(guard, identity)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError("Final pacing artifact was replaced", "access")
    except BaseException:
        journal_failure()
        raise
    finally:
        primary = sys.exc_info()[1]
        errors = []
        failed = primary is not None or _PUBLISHER_GATE_FAILED
        if failed:
            for fd, identity in handles:
                with contextlib.suppress(BaseException):
                    output_poison(fd, identity)
        for fd, identity in reversed(handles):
            try:
                output_close(fd, identity)
            except BaseException as error:
                errors.append(error)
        try:
            output_close(parent, parent_id, True)
        except BaseException as error:
            errors.append(error)
        if failed or errors or _PUBLISHER_GATE_FAILED:
            # Reopen only the safely owned current output for the known ID.
            # Existing unsafe/replacement objects must remain untouched.
            with contextlib.suppress(BaseException):
                journal_invalidate_known_output(path, handles, parent_id)
            if signal:
                with contextlib.suppress(BaseException):
                    journal_output_signal("false")
        if errors and primary is None:
            raise errors[0]


def journal_invalidate_known_output(path, handles, expected_parent):
    """Last-close recovery cannot invalidate a different replacement inode."""
    parent, root, parent_id = output_open_parent(path)
    if parent_id != expected_parent:
        output_close(parent, parent_id, True)
        raise AdapterError("Original output parent replaced", "access")
    descriptor = None
    identity = None
    try:
        named_before = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        identity = (named_before.st_dev, named_before.st_ino)
        if identity not in [expected for _, expected in handles]:
            raise AdapterError("Unrelated owned output preserved", "access")
        descriptor = os.open(
            os.path.basename(path),
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != identity:
            raise AdapterError("Owned cleanup FD changed", "access")
        if identity not in [expected for _, expected in handles]:
            raise AdapterError("Unrelated owned output preserved", "access")
        output_parent(parent, root, parent_id)
        output_poison(descriptor, identity)
        output_parent(parent, root, parent_id)
        current = os.stat(
            os.path.basename(path), dir_fd=parent, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) == identity:
            os.unlink(os.path.basename(path), dir_fd=parent)
            os.fsync(parent)
    finally:
        if descriptor is not None:
            output_close(descriptor, identity)
        output_close(parent, parent_id, True)


def journal_invalidate_export():
    path = os.environ.get("AUSTRIA_PACING_ARTIFACT_PATH")
    if path:
        journal_invalidate_owned_output(path)


def verify_original_descriptor(descriptor, expected):
    info = os.fstat(descriptor)
    if (
        expected is None
        or (info.st_dev, info.st_ino) != expected
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AdapterError("Original publisher descriptor changed", "access")
    return info


def close_original_descriptor(descriptor, expected):
    primary = sys.exc_info()[1]
    try:
        verify_original_descriptor(descriptor, expected)
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def verify_collection():
    try:
        verify_state_directory()
        verify_original_descriptor(_COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID)
        current = os.stat(
            "collection.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != _COLLECTION_LOCK_ID:
            raise AdapterError("Original collection lock changed", "access")
    except BaseException:
        journal_failure()
        raise


def journal_before_wire():
    """Fence retained journal and collection authority at the boundary."""
    if _PUBLISHER_GATE_FAILED or not _STATE_TRUSTED:
        raise AdapterError("Publisher state is not trusted", "access")
    verify_journal()
    verify_collection()


def close_directory_descriptor(descriptor, expected):
    """Release an acquired directory without closing a foreign recycled FD."""
    primary = sys.exc_info()[1]
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != expected
        ):
            raise AdapterError(
                "Original directory descriptor changed", "access"
            )
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


# Finite reviewed pre-publisher failures; issue405 retains full log/code proof.
_NO_ACCESS_ATTEMPTS = {
    39279270035: (
        118150361011,
        '2026-10-19T15:36:42Z',
        '2026-10-19T15:38:38Z',
        '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b',
    ),
    39431472492: (
        118657323225,
        '2026-10-20T13:33:51Z',
        '2026-10-20T13:34:32Z',
        '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b',
    ),
    39611287745: (
        119260512030,
        '2026-10-21T13:48:57Z',
        '2026-10-21T13:49:37Z',
        '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b',
    ),
    39790099516: (
        119863559005,
        '2026-10-22T13:48:16Z',
        '2026-10-22T13:48:45Z',
        '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b',
    ),
    39961302167: (
        120439677243,
        '2026-10-23T13:27:08Z',
        '2026-10-23T13:28:14Z',
        '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b',
    ),
    40094906365: (
        120875002312,
        '2026-10-24T12:40:31Z',
        '2026-10-24T12:41:04Z',
        '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b',
    ),
    40196473867: (
        121178268737,
        '2026-10-25T13:11:28Z',
        '2026-10-25T13:12:08Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    40346930345: (
        121656228866,
        '2026-10-26T15:11:53Z',
        '2026-10-26T15:12:36Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    40522038690: (
        122251413088,
        '2026-10-27T14:32:15Z',
        '2026-10-27T14:32:48Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    40712626399: (
        122901687228,
        '2026-10-28T15:06:46Z',
        '2026-10-28T15:07:20Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    40899456854: (
        123537433464,
        '2026-10-29T15:14:05Z',
        '2026-10-29T15:14:48Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    41077111316: (
        124136120860,
        '2026-10-30T15:03:11Z',
        '2026-10-30T15:04:00Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    41210567914: (
        124564476343,
        '2026-10-31T14:32:07Z',
        '2026-10-31T14:32:36Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    41319771228: (
        124887289472,
        '2026-11-01T15:09:29Z',
        '2026-11-01T15:09:58Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    41499968183: (
        125464612219,
        '2026-11-02T18:22:36Z',
        '2026-11-02T18:23:25Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    41672214061: (
        126043280251,
        '2026-11-03T16:14:58Z',
        '2026-11-03T16:15:49Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    41866054164: (
        126700410896,
        '2026-11-04T16:19:27Z',
        '2026-11-04T16:19:36Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    42064817035: (
        127375912654,
        '2026-11-05T16:28:37Z',
        '2026-11-05T16:29:17Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    42247579460: (
        127993728168,
        '2026-11-06T15:55:45Z',
        '2026-11-06T15:56:19Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    42383785375: (
        128436751549,
        '2026-11-07T14:54:29Z',
        '2026-11-07T14:55:17Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    42493672789: (
        128771210148,
        '2026-11-08T15:13:01Z',
        '2026-11-08T15:13:47Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    42677327210: (
        129362119926,
        '2026-11-09T18:48:42Z',
        '2026-11-09T18:49:42Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    42854398421: (
        129963339753,
        '2026-11-10T16:25:36Z',
        '2026-11-10T16:26:38Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    43055694990: (
        130644417772,
        '2026-11-11T16:10:43Z',
        '2026-11-11T16:11:56Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    43262609514: (
        131337602336,
        '2026-11-12T16:48:10Z',
        '2026-11-12T16:49:13Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    43462404241: (
        132012441067,
        '2026-11-13T16:24:29Z',
        '2026-11-13T16:25:38Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    43608233240: (
        132487386906,
        '2026-11-14T15:05:07Z',
        '2026-11-14T15:06:05Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    43731113348: (
        132863527540,
        '2026-11-15T15:39:43Z',
        '2026-11-15T15:40:39Z',
        '5fd9fa82d5d2d5f0871aba653376765a6e00c897',
    ),
    43929966017: (
        133519425315,
        '2026-11-16T18:32:56Z',
        '2026-11-16T18:34:00Z',
        '2d478d93da7a7e52b185d4406d2a08a2970431ea',
    ),
}
_NO_ACCESS_CODE = {
    '2d478d93da7a7e52b185d4406d2a08a2970431ea': (
        '9238f2fc04b9d3097b6066c2440bc71afd9433a7',
        '28eba3e7f45b08af9c0aa552fb814871196fd435',
    ),
    '46fe9e001ccd2f684b6d4941dbb69bcbc63f598b': (
        '953a9b8f43a8a6f2110c80b3320778f93ff2a9e9',
        '28eba3e7f45b08af9c0aa552fb814871196fd435',
    ),
    '5fd9fa82d5d2d5f0871aba653376765a6e00c897': (
        '9238f2fc04b9d3097b6066c2440bc71afd9433a7',
        '28eba3e7f45b08af9c0aa552fb814871196fd435',
    ),
}
_NO_ACCESS_LAST_OWNER = (39128036954, 1)
_NO_ACCESS_ARTIFACT = (
    12137805346,
    "sha256:74dc863d6ef88e269dd0c3867a2e1be89a4c06c51fdf6eb855d452efc99c7f57",
)
_REVIEWED_PRODUCER_WORKFLOW_BLOB = '8e6da7243d60479e29b66057f15b62ee3c62f671'
_NO_ACCESS_BOUNDS = {}
_IMMUTABLE_CODE_PROOFS = set()


def verified_code_identity(
    head, source_blob, workflow_blob, *, optional=False
):
    """Bind immutable source and the sole reviewed production command."""
    key = (head, source_blob, workflow_blob)
    if key in _IMMUTABLE_CODE_PROOFS:
        return True
    for path, expected in (
        ("adapters/at-fulbright-austria/adapter.py", source_blob),
        (".github/workflows/fetch-at-fulbright-austria.yml", workflow_blob),
    ):
        try:
            value = workflow_api("/contents/" + path + "?ref=" + head)
        except AdapterError as error:
            if optional and error.status == 404:
                # An absent historical file cannot establish this proof.
                # Keep the run in ancestry; never omit it or select fallback.
                return False
            raise
        if value.get("sha") != expected:
            return False
    _IMMUTABLE_CODE_PROOFS.add(key)
    return True


def proven_no_access_attempt(run, jobs):
    """Omit only exact reviewed failures or a positively proven short job."""
    if (
        run.get("run_attempt") != 1
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or len(jobs) != 1
        or jobs[0].get("name") != "collect"
        or jobs[0].get("status") != "completed"
        or jobs[0].get("conclusion") != "failure"
    ):
        return False
    pin = _NO_ACCESS_ATTEMPTS.get(run["id"])
    if pin is not None:
        job_id, start, end, head = pin
        if (
            run["head_sha"] != head
            or (jobs[0]["id"], jobs[0]["started_at"],
                jobs[0]["completed_at"]) != (job_id, start, end)
            or not verified_code_identity(
                head, *_NO_ACCESS_CODE[head],
            )
        ):
            raise AdapterError("Reviewed pre-access history changed", "access")
    else:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            return False
        start = datetime.fromisoformat(
            jobs[0]["started_at"].replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            jobs[0]["completed_at"].replace("Z", "+00:00")
        )
        if not 0 <= (end - start).total_seconds() < 58:
            return False
        with open(__file__, "rb") as source_file:
            raw = source_file.read(1_000_001)
        if len(raw) > 1_000_000:
            raise AdapterError("Current source proof exceeds bound", "access")
        prefix = b"blob " + str(len(raw)).encode("ascii") + b"\0"
        current_blob = hashlib.sha1(
            prefix + raw, usedforsecurity=False
        ).hexdigest()
        if not verified_code_identity(
            run["head_sha"], current_blob, _REVIEWED_PRODUCER_WORKFLOW_BLOB,
            optional=True,
        ):
            return False
        current = workflow_api(
            "/contents/adapters/at-fulbright-austria/adapter.py?ref="
            + os.environ["GITHUB_SHA"]
        )
        if current.get("sha") != current_blob:
            raise AdapterError("Executing source provenance differs", "access")
    result = workflow_api(f"/actions/runs/{run['id']}/artifacts?per_page=100")
    if (
        type(result.get("total_count")) is not int
        or not isinstance(result.get("artifacts"), list)
        or result["total_count"] != len(result["artifacts"])
    ):
        raise AdapterError("Incomplete no-access artifact proof", "access")
    if pin is not None and result["total_count"] != 0:
        raise AdapterError(
            "Reviewed pre-access artifact inventory changed", "access"
        )
    return result["total_count"] == 0


def preserve_no_access_bounds(state):
    """Retain administrative completion bounds without inventing receipts."""
    if not _NO_ACCESS_BOUNDS:
        return state
    result = copy.deepcopy(validate_budget(state))
    boundary = max(row[0] for row in _NO_ACCESS_BOUNDS.values())
    result["not_before"] = max(
        result["not_before"],
        boundary + max(result["interval"], result["page_interval"]),
    )
    result["observed_at"] = max(result["observed_at"], boundary)
    result["legacy_rate_history_unknown"] = True
    return validate_budget(result)


def canonical_state_digest(state):
    raw = json.dumps(
        validate_budget(state), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def authenticated_source_digest(head, *, executing=False):
    """Bind a modern owner to authenticated immutable adapter bytes."""
    value = workflow_api(
        "/contents/adapters/at-fulbright-austria/adapter.py?ref=" + head
    )
    if (
        not isinstance(value, dict)
        or value.get("encoding") != "base64"
        or type(value.get("size")) is not int
        or not 0 < value["size"] <= 1_000_000
        or not isinstance(value.get("content"), str)
        or not isinstance(value.get("sha"), str)
    ):
        raise AdapterError("Immutable source evidence unavailable", "access")
    try:
        raw = base64.b64decode(value["content"].replace("\n", ""), validate=True)
    except (ValueError, TypeError):
        raise AdapterError("Immutable source evidence invalid", "access") from None
    blob = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw,
        usedforsecurity=False,
    ).hexdigest()
    if len(raw) != value["size"] or blob != value["sha"]:
        raise AdapterError("Immutable source bytes differ", "access")
    if executing:
        with open(__file__, "rb") as source_file:
            actual = source_file.read(1_000_001)
        if raw != actual:
            raise AdapterError("Executing source differs from owner", "access")
    return hashlib.sha256(raw).hexdigest()


def queued_sibling_without_access(run):
    """Prove a newer empty queue item shares the current immutable serializer."""
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or run.get("status") != "queued"
        or run.get("run_attempt") != 1
        or run.get("conclusion") is not None
    ):
        return False
    queued = validate_family_identity(run)
    current_id, current_attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    current_identity = (current_id, current_attempt, SOURCE_ID, head)
    if queued[0] == current_id:
        return False
    current = workflow_api(
        f"/actions/runs/{current_id}/attempts/{current_attempt}"
    )
    validate_family_identity(current, current_identity)
    if current.get("status") != "in_progress":
        return False
    jobs = workflow_api(
        f"/actions/runs/{current_id}/attempts/{current_attempt}/jobs?per_page=100"
    )
    if (
        type(jobs.get("total_count")) is not int
        or jobs["total_count"] != 1
        or not isinstance(jobs.get("jobs"), list)
        or len(jobs["jobs"]) != 1
    ):
        return False
    job = jobs["jobs"][0]
    if (
        type(job.get("id")) is not int or job["id"] <= 0
        or job.get("run_id") != current_id
        or job.get("run_attempt") != current_attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("name") != "collect"
        or job.get("status") != "in_progress"
    ):
        return False
    try:
        began = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if began.utcoffset() is None or created.utcoffset() is None:
            return False
        if not began < created:
            return False
    except (KeyError, AttributeError, ValueError, TypeError):
        return False
    authenticated_source_digest(head, executing=True)
    with open(__file__, "rb") as source_file:
        raw = source_file.read(1_000_001)
    blob = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw,
        usedforsecurity=False,
    ).hexdigest()
    if not all(verified_code_identity(
        identity_head, blob, _REVIEWED_PRODUCER_WORKFLOW_BLOB, optional=True
    ) for identity_head in (head, queued[3])):
        return False
    exact_path = f"/actions/runs/{queued[0]}/attempts/1"
    exact = workflow_api(exact_path)
    validate_family_identity(exact, queued)
    if any(exact.get(key) != run.get(key) for key in (
        "status", "conclusion", "created_at", "head_sha"
    )):
        return False
    for path, key in (
        (exact_path + "/jobs?per_page=100", "jobs"),
        (f"/actions/runs/{queued[0]}/artifacts?per_page=100", "artifacts"),
    ):
        value = workflow_api(path)
        if type(value.get("total_count")) is not int or (
            value["total_count"] != 0 or value.get(key) != []
        ):
            return False
    again = workflow_api(exact_path)
    validate_family_identity(again, queued)
    if any(again.get(key) != exact.get(key) for key in (
        "status", "conclusion", "created_at", "head_sha"
    )):
        return False
    still_current = workflow_api(
        f"/actions/runs/{current_id}/attempts/{current_attempt}"
    )
    validate_family_identity(still_current, current_identity)
    return still_current.get("status") == "in_progress"


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
