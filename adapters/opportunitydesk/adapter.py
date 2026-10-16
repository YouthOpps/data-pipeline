"""Collect individual public Opportunity Desk RSS opportunities."""


import argparse
import copy
import errno
import functools
import io
import math
import socket
import ssl
import stat
import zipfile


import base64


import contextlib


import datetime as dt


from datetime import datetime, timedelta, timezone


import email.utils


from email.utils import parsedate_to_datetime


import fcntl


import hashlib


import html


from html.parser import HTMLParser


import ipaddress


import json


import os


import random


import re


import signal


import sys


import tempfile


import threading


import time


import urllib.error


import urllib.parse


import urllib.request


import xml.etree.ElementTree as ET


from zoneinfo import ZoneInfo


SOURCE_ID = "opportunitydesk"


SOURCE_URL = "https://opportunitydesk.org/feed/"


WEBSITE_URL = "https://opportunitydesk.org/"


LANGUAGE = "en"


PUBLISHER_COUNTRY = None


PUBLISHER_TYPE = "aggregator"


ATTRIBUTION = "Opportunity Desk"


_TAG_RULES = (
    ("scholarships", r"\b(?:scholarships?|burs)\b"),
    ("internships", r"\binternships?\b"),
    ("volunteering", r"\bvolunteer(?:ing|s)?\b"),
    ("fellowships", r"\bfellowships?\b"),
    ("training", r"\b(?:training|courses?|workshops?)\b"),
    ("competitions", r"\b(?:competitions?|contests?|awards?)\b"),
    ("grants", r"\bgrants?\b"),
    ("jobs", r"\b(?:jobs?|careers?)\b"),
)


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


def _published(value):
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return (
            parsed.astimezone(dt.timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    except (ValueError, TypeError, OverflowError):
        return None


class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


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
            state = (
                json.loads(text)
                if text
                else {"starts": [], "interval": 6, "until": 0}
            )
            yield state
            file.seek(0)
            file.truncate()
            json.dump(state, file)
            file.flush()
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def legacy_pace(url, interval=6, *, start_padding=0):
    if start_padding:
        padding_until = None
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    host = host.removeprefix("www.")
    while True:
        with budget_state(host) as state:
            now = time.time()
            starts = [start for start in state["starts"] if now - start < 60]
            state["starts"] = starts
            state["interval"] = max(6, interval, state["interval"])
            ready = max(
                state["until"],
                starts[-1] + state["interval"] if starts else now,
                starts[0] + 60 if len(starts) >= 10 else now,
            )
            if start_padding and ready <= now:
                if padding_until is None:
                    padding_until = now + start_padding
                ready = max(ready, padding_until)
            if ready <= now:
                state["starts"].append(now)
                return {"host": host}
            wait = ready - now
        time.sleep(wait)


def legacy_request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    deadline=None,
    retry_jitter=0
):
    received_publisher_429 = False
    try:
        if retry_jitter:
            budget = legacy_pace(url, interval, start_padding=retry_jitter)
        else:
            budget = legacy_pace(url, interval)
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
                    raise AdapterError(
                        "Publication deadline exhausted", "publish"
                    )
                transport_timeout = min(30, remaining)
            response = _OPENER.open(request, timeout=transport_timeout)
        except urllib.error.HTTPError as error:
            response = error
        if (
            response.status == 429
            and urllib.parse.urlsplit(url).hostname != "api.github.com"
        ):
            received_publisher_429 = True
            try:
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        seconds = (
                            float(retry)
                            if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                            else parsedate_to_datetime(retry).timestamp()
                            - time.time()
                        )
                        with budget_state(budget["host"]) as state:
                            state["until"] = max(
                                state["until"], time.time() + max(0, seconds)
                            )
                    except (ValueError, TypeError, OverflowError):
                        pass
            finally:
                response.close()
            raise AdapterError("Publisher returned HTTP 429", "fetch", 429)
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
                    with budget_state(budget["host"]) as state:
                        state["until"] = max(
                            state["until"], time.time() + max(0, seconds)
                        )
                except (ValueError, TypeError, OverflowError):
                    pass
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            return response.status, response.headers, body
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if received_publisher_429:
            raise AdapterError(
                "Publisher returned HTTP 429; response cleanup failed",
                "fetch",
                429,
            ) from error
        raise


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


_COUNTRY_NAMES = {
    "Burundi": "BI",
    "Central African Republic": "CF",
    "Democratic Republic of the Congo": "CD",
    "Djibouti": "DJ",
    "Eritrea": "ER",
    "Ethiopia": "ET",
    "Kenya": "KE",
    "Republic of the Congo": "CG",
    "Rwanda": "RW",
    "Somalia": "SO",
    "South Sudan": "SS",
    "Sudan": "SD",
    "Tanzania": "TZ",
    "Uganda": "UG",
    "Denmark": "DK",
    "United States": "US",
}


def article_countries(article):
    """Separate explicit citizenship lists from evidenced host locations."""
    eligible = []
    hosts = []
    location = None
    for block in article.blocks:
        match = re.search(
            r"citizens of one of the following countries:\s*(.+?)(?:[.—]|$)",
            block,
            re.I,
        )
        if match:
            # Consume the complete list, longest names first. Never substring
            # match Sudan inside South Sudan or Congo inside its two republics.
            names = re.split(r",\s*|\s+and\s+", match.group(1).strip())
            codes = [_COUNTRY_NAMES.get(name.strip()) for name in names]
            if names and all(codes):
                eligible.extend(codes)
    text = article.text
    if any(
        re.search(r"hosting institution|host institution", block, re.I)
        and re.search(r"period in Denmark", block, re.I)
        for block in article.blocks
    ):
        hosts.append("DK")
    if re.search(r"internships?", text, re.I) and re.search(
        r"in Washington, DC", text
    ):
        hosts.append("US")
        location = "Washington, DC, United States"
    if re.search(
        r"Fellows must be located in the NYC Metropolitan area or find "
        r"housing in the NYC Metropolitan area for the duration",
        text,
        re.I,
    ):
        hosts.append("US")
        location = "NYC Metropolitan area, United States"
    return sorted(set(eligible)), sorted(set(hosts)), location


def article_deadline(article):
    """Read only explicit closing instants with an unambiguous UTC zone."""
    pattern = (
        r"\b(?:applications?.{0,100}?will close|applications? close|"
        r"deadline(?: is)?) at\s+(\d{1,2})(?:[.:](\d{2}))?\s*"
        r"(am|pm)?\s+(GMT|UTC)\s+on\s+"
        r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b"
    )
    values = set()
    for match in re.finditer(pattern, article.text, re.I):
        hour, minute, meridiem, _, day, month, year = match.groups()
        hour = int(hour)
        if meridiem:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
        try:
            date = dt.datetime.strptime(
                f"{day} {month} {year}", "%d %B %Y"
            ).replace(
                hour=hour, minute=int(minute or 0), tzinfo=dt.timezone.utc
            )
        except ValueError:
            continue
        values.add(
            date.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
    headers = [
        block for block in article.blocks if block.startswith("Deadline:")
    ]
    header_dates = []
    for block in headers:
        match = re.fullmatch(
            r"Deadline:\s*([A-Za-z]+) (\d{1,2}), (\d{4})", block
        )
        if match:
            try:
                header_dates.append(
                    dt.datetime.strptime(" ".join(match.groups()), "%B %d %Y")
                )
            except ValueError:
                pass
    for match in re.finditer(
        r"Applications must be submitted by ([A-Za-z]+) (\d{1,2}) "
        r"at (\d{1,2}):(\d{2})(AM|PM) ET\b",
        article.text,
        re.I,
    ):
        month, day, hour, minute, meridiem = match.groups()
        if not 1 <= int(hour) <= 12:
            continue
        for header in header_dates:
            if header.strftime("%B").lower() != month.lower():
                continue
            if header.day != int(day):
                continue
            try:
                local = header.replace(
                    hour=int(hour) % 12
                    + (12 if meridiem.lower() == "pm" else 0),
                    minute=int(minute),
                    tzinfo=ZoneInfo("America/New_York"),
                )
                # Reject ambiguous or missing local clocks at DST transitions.
                if local.replace(fold=1).utcoffset() != local.utcoffset():
                    continue
                utc = local.astimezone(dt.timezone.utc)
                if utc.astimezone(local.tzinfo) != local:
                    continue
                values.add(
                    utc.isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    )
                )
            except ValueError:
                continue
    # Conflicting instants require source review, rather than choosing a date.
    return values.pop() if len(values) == 1 else None


def validate_records(records, *, allow_empty=False):
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
                    if (
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


class Article(HTMLParser):
    """Read only the publisher's article, excluding menus and related posts."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.skip = 0
        self.parts = []
        self.blocks = []
        self.block = None
        self.links = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        attributes = dict(attrs)
        if tag == "div":
            if self.depth:
                self.depth += 1
            elif "post-content" in attributes.get("class", "").split():
                self.depth = 1
        if not self.depth:
            return
        if tag in ("p", "h2", "h3", "h4", "li"):
            self.block = []
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if not self.depth:
            return
        if tag in ("p", "h2", "h3", "h4", "li") and self.block is not None:
            self.blocks.append(" ".join(" ".join(self.block).split()))
            self.block = None
        if tag == "div":
            self.depth -= 1

    def handle_data(self, value):
        if self.depth and not self.skip:
            self.parts.append(value)
            if self.block is not None:
                self.block.append(value)

    @property
    def text(self):
        return " ".join(" ".join(self.parts).split())


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


def material_programme_categories(article):
    """Classify a narrowly evidenced training programme with finalist grants."""
    headings = {}
    for name in ("benefits", "eligibility", "application"):
        positions = [
            i
            for i, block in enumerate(article.blocks)
            if block.casefold() == name
        ]
        if len(positions) != 1:
            return []
        headings[name] = positions[0]
    if not (
        headings["benefits"] < headings["eligibility"] < headings["application"]
    ):
        return []
    eligibility = article.blocks[
        headings["eligibility"] + 1 : headings["application"]
    ]
    application = article.blocks[headings["application"] + 1 :]
    if not any(
        re.search(r"\b(?:eligible|eligibility|applicants)\b", block, re.I)
        for block in eligibility
    ) or not any(re.search(r"\bapply\b", block, re.I) for block in application):
        return []
    benefits = article.blocks[
        headings["benefits"] + 1 : headings["eligibility"]
    ]
    courses = [
        block
        for block in benefits
        if re.fullmatch(
            r"Courses: Free access to (?:a range of )?online "
            r"self-paced courses",
            block,
            re.I,
        )
    ]
    workshops = [
        block
        for block in benefits
        if re.fullmatch(r"Talks: Expert talks & workshops on .+", block, re.I)
    ]
    grants = [
        block
        for block in benefits
        if re.fullmatch(
            r"Grant: Top [1-9][0-9]* finalists win one of "
            r"(?:[1-9][0-9]*|one|two|three) \$[1-9][0-9,]* grants",
            block,
            re.I,
        )
    ]
    if len(courses) == len(workshops) == len(grants) == 1:
        return ["training", "grants"]
    return []


def material_conference_grants(article):
    """Recognize the UN fund's conditional representative travel support."""
    blocks = article.blocks
    headings = {}
    for name in (
        "Benefits",
        "Eligibility",
        "Selection Criteria",
        "Application",
    ):
        positions = [i for i, block in enumerate(blocks) if block == name]
        if len(positions) != 1:
            return []
        headings[name] = positions[0]
    if not (
        headings["Benefits"]
        < headings["Eligibility"]
        < headings["Selection Criteria"]
        < headings["Application"]
    ):
        return []
    benefits = blocks[headings["Benefits"] + 1 : headings["Eligibility"]]
    eligibility = blocks[
        headings["Eligibility"] + 1 : headings["Selection Criteria"]
    ]
    selection = blocks[
        headings["Selection Criteria"] + 1 : headings["Application"]
    ]
    application = blocks[headings["Application"] + 1 :]
    sessions = blocks[: headings["Benefits"]]
    funding = [
        block
        for block in benefits
        if re.fullmatch(
            r"The UN Voluntary Fund for Indigenous Peoples will provide, "
            r"in accordance with United Nations rules and procedures, "
            r"a travel arrangement as well as a stipend to cover travel, "
            r"accommodation, and related expenses for selected Indigenous "
            r"representatives attending the abovementioned processes\.",
            block,
        )
    ]
    requirements = (
        any(
            re.search(
                r"session of the UN Permanent Forum on Indigenous Issues "
                r"\(UNPFII\)",
                block,
            )
            for block in sessions
        ),
        any(
            re.search(
                r"session of the Expert Mechanism on the Rights of Indigenous "
                r"Peoples \(EMRIP\)",
                block,
            )
            for block in sessions
        ),
        "Applications are open to Indigenous individuals and "
        "representatives of Indigenous Peoples’ organizations who:"
        in eligibility,
        "Are actively engaged in the promotion and protection of the "
        "rights of Indigenous Peoples;" in eligibility,
        "Require financial support to cover travel, accommodation and "
        "related expenses." in eligibility,
        any(
            re.fullmatch(
                r"Representation: Priority will be given to Indigenous "
                r"representatives who have a clear mandate from their "
                r"communities or organizations\.",
                block,
            )
            for block in selection
        ),
        "Interested candidates must complete the application form "
        "available at:" in application,
    )
    if len(benefits) == len(funding) == 1 and all(requirements):
        return ["grants"]
    return []


def collect_inventory():
    _ROBOTS_CACHE.clear()
    delay = check_robots(SOURCE_URL)
    document = ET.fromstring(fetch_source(SOURCE_URL, min_interval=delay))
    items = document.findall("./channel/item")
    if not items:
        raise AdapterError("Empty or invalid RSS feed")
    records = []
    seen = set()
    for item in items:
        title = _feed_text(item.findtext("title"))
        url = (item.findtext("link") or "").strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname != "opportunitydesk.org":
            raise AdapterError("Unexpected RSS article host")
        if not title or url in seen:
            raise AdapterError("Missing title or duplicate RSS article")
        seen.add(url)
        tags = list(
            dict.fromkeys(
                _feed_text(node.text)
                for node in item.findall("category")
                if _feed_text(node.text)
            )
        )
        article = Article(fetch_source(url, min_interval=check_robots(url)))
        if not article.text:
            raise AdapterError("Publisher article structure changed")
        numbered = [
            block for block in article.blocks if re.match(r"^\d+[.)]\s+", block)
        ]
        editorial = {
            "our blog",
            "blog",
            "general tips",
            "how-to",
            "success stories",
            "young person of the month",
        }
        if editorial.intersection(tag.casefold() for tag in tags):
            print("Excluded editorial article: " + url, file=sys.stderr)
            continue
        if len(numbered) >= 2 and re.search(
            r"\b(?:opportunities|scholarships|internships|jobs)\b", title, re.I
        ):
            print("Excluded multi-opportunity roundup: " + url, file=sys.stderr)
            continue
        # Classification uses exact publisher taxonomy and article evidence,
        # never an opportunity-shaped title alone.
        categories = [
            category
            for category, pattern in _TAG_RULES
            if any(re.fullmatch(pattern, tag, re.I) for tag in tags)
        ]
        text = article.text
        if not re.search(
            r"\b(?:eligibility|eligible|application|apply|"
            r"applicants|deadline)\b",
            text,
            re.I,
        ):
            raise AdapterError("Unresolved non-opportunity RSS article: " + url)
        if "Conferences" in tags and not categories:
            categories = material_conference_grants(article)
            if not categories and re.search(
                r"\b(?:conference|ambassador|participants)\b", text, re.I
            ):
                categories = ["other"]
        if not categories:
            categories = material_programme_categories(article)
        if not categories:
            raise AdapterError("Unreviewed publisher opportunity taxonomy")
        record = make_record(
            title,
            url,
            categories,
            kind="opportunity",
            published_at=_published(item.findtext("pubDate")),
            tags=tags,
            method="publisher-taxonomy-and-article-v2",
            evidence=[url],
        )
        eligible, hosts, location = article_countries(article)
        record["eligible_countries"] = eligible
        record["host_countries"] = hosts
        record["location"] = location
        record["deadline"] = article_deadline(article)
        if record["deadline"]:
            record["status"] = (
                "open" if record["deadline"] > utc_now() else "expired"
            )
        deadlines = [
            block for block in article.blocks if block.startswith("Deadline:")
        ]
        if deadlines:
            record["summary"] = "Publisher-stated " + deadlines[0][:500]
        if categories == ["other"]:
            record["classification"]["status"] = "classified"
        records.append(record)
    validate_records(records)
    return records


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


def original_prove_publication_window():
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


def metadata(attempt, previous, records, error=None):
    result = {
        "display_name": "Opportunity Desk",
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


_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"


FAMILY = "youthopps-opportunitydesk-publisher-v1"


FAMILY_SOURCES = {SOURCE_ID}


_WORKFLOW_ID = 378391285


_ARTIFACT_NAME = "opportunitydesk-pacing-state"


_STATE_SCHEMA = 3


COLLECTION_TIMEOUT = 1200


RUN_TIMEOUT = 1740


EXPORT_TIMEOUT = 30


_STATE_PATH = _STATE_LOCK = None


_STATE_DIR_FD = _STATE_DIR_ID = None


_STATE_INITIALIZING = False
_LOCAL_MATURE_RESTORE = False


_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None


_COLLECTION_LOCK_ID = None


_RESTORED = _PUBLISHING = _STATE_TRUSTED = False


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


_ROBOTS = _ROBOTS_CACHE


_REP_TARGETS = {}


_HISTORY_COMPLETED = []


_HISTORY_JOBLESS = []


_HISTORY_ATTEMPT_PROOFS = {}


_HISTORY_SIGNATURE = _RESTORED_HISTORY_SIGNATURE = None


_ACTIVE_FLIGHT_ID = _COMPLETING_FLIGHT_ID = None


_ACTIVE_REQUEST_ID = None


_TRANSACTION_DEPTH = 0


_REQUEST_FRAMES = []


_FAILED_BUDGET = None


_JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None


_JOURNAL_CREATED = _JOURNAL_DIRTY = False


_JOURNAL_LOCKS = {}


_ACQUIRED_IDS = {}


_REP_PROVENANCE = {}


def nonexport_end():
    """Keep P/F/X inside entry and original exact hosted job cutoffs."""
    if _RUN_END is None:
        raise AdapterError("Frozen execution deadline required", "access")
    end = _RUN_END - EXPORT_TIMEOUT
    if _PUBLISHING and _PUBLICATION_WINDOW is not None:
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
    try:
        signal.signal(signal.SIGALRM, alarm_expired)
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
        with phase("snapshot", 120):
            return original_read_snapshot()
    ensure_failure_phase()
    return original_read_snapshot()


def publish_files(snapshot, files):
    if _RUN_END is None:
        return original_publish_files(snapshot, files)
    if "data.json" not in files:
        ensure_failure_phase()
        return original_publish_files(snapshot, files)
    try:
        with phase("publication", 180):
            return original_publish_files(snapshot, files)
    except PhaseExpired:
        raise AdapterError("Publication phase exhausted", "publish") from None


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


@contextlib.contextmanager
def execution():
    """Freeze entry admission; leave original publication alarms unopposed."""
    global _RUN_END, _ENTRY_MONOTONIC, _PHASE_SEEN, _FAILURE_CONTEXT
    global _INITIAL_SNAPSHOT_ATTEMPTED, _COLLECTION_LOCK_FD
    global _COLLECTION_LOCK_ID
    global _RESTORED, _STATE_TRUSTED, _LOCAL_FIRST_WIRE_VERIFIED
    global _RESTORED_HISTORY_SIGNATURE
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
    _RESTORED_HISTORY_SIGNATURE = None
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
        limit = min(_ENTRY_MONOTONIC + 30, limit - 660)
    elif name == "restore":
        limit -= 480
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
    """Admit only an actually new leaf or the existing original namespace."""
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
        if not _LOCAL_MATURE_RESTORE:
            try:
                os.mkdir(leaf, 0o700, dir_fd=parent)
                created = True
            except FileExistsError:
                pass
        # Modern LOCAL ancestry requires its existing original namespace.
        # Never recreate a missing leaf from a newer owned remote state.

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
            raise AdapterError(
                "Publisher journal generation changed", "access"
            )
        lock = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID:
            raise AdapterError("Publisher journal lock changed", "access")
        return info
    except BaseException:
        journal_failure()
        raise


def retain_journal():
    """Pin one journal after acquiring its stable lock; never retarget it."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_CREATED
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
        identity = _ACQUIRED_IDS[descriptor]
        info = verify_original_descriptor(descriptor, identity)
        _JOURNAL_FD = descriptor
        _JOURNAL_ID = identity
        _JOURNAL_VERSION = journal_version(info)
        verify_journal()
        if _JOURNAL_CREATED:
            os.fsync(_STATE_DIR_FD)
    except BaseException:
        journal_failure()
        raise


def close_journal():
    """Check final authority, then release only the original journal FD."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_LOCK_ID
    global _JOURNAL_CREATED, _JOURNAL_DIRTY
    descriptor, identity = _JOURNAL_FD, _JOURNAL_ID
    primary = sys.exc_info()[1]
    errors = []
    if descriptor is not None:
        try:
            if not _PUBLISHER_GATE_FAILED:
                verify_journal()
        except BaseException as error:
            journal_failure()
            errors.append(error)
        try:
            close_original_descriptor(descriptor, identity)
        except BaseException as error:
            journal_failure()
            errors.append(error)
    _JOURNAL_FD = _JOURNAL_ID = _JOURNAL_VERSION = _JOURNAL_LOCK_ID = None
    _JOURNAL_CREATED = _JOURNAL_DIRTY = False
    if errors and primary is None:
        raise errors[0]


@contextlib.contextmanager
def locked_budget():
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
        before = verify_journal()
        chunks, size = [], 0
        while True:
            chunk = os.pread(_JOURNAL_FD, min(8192, 32769 - size), size)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 32768:
                raise AdapterError("Oversized publisher state", "access")
        raw = b"".join(chunks)
        verify_journal()
        if len(raw) != before.st_size:
            raise AdapterError("Incomplete publisher journal read", "access")
        if len(raw) > 32768:
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


def save_budget(state):
    """Commit through the held inode; interrupted rewrites fail closed."""
    global _STATE_INITIALIZING, _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    global _JOURNAL_CREATED, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Opportunity Desk state persistence is already unhealthy",
            "access",
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
                "Unproved Opportunity Desk completion write", "access"
            )
        payload = json.dumps(state, separators=(",", ":")).encode()
        if len(payload) > 32768 or not _JOURNAL_LOCKS:
            raise AdapterError(
                "Bounded locked journal commit required", "access"
            )
        verify_journal()
        _STATE_INITIALIZING = _JOURNAL_CREATED = False
        _JOURNAL_DIRTY = True
        os.ftruncate(_JOURNAL_FD, 0)
        os.fsync(_JOURNAL_FD)
        verify_journal(False)
        offset = 0
        while offset < len(payload):
            verify_journal(False)
            count = os.pwrite(_JOURNAL_FD, payload[offset:], offset)
            if (
                type(count) is not int
                or not 0 < count <= len(payload) - offset
            ):
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += count
            verify_journal(False)
        os.fsync(_JOURNAL_FD)
        verify_journal(False)
        before = verify_journal(False)
        if os.pread(_JOURNAL_FD, len(payload) + 1, 0) != payload:
            raise AdapterError("Publisher journal bytes changed", "access")
        info = verify_journal(False)
        if journal_version(before) != journal_version(info):
            raise AdapterError(
                "Publisher journal changed during commit read", "access"
            )
        _JOURNAL_VERSION = journal_version(info)
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
        name,
        flags | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=_STATE_DIR_FD,
    )
    identity = expected
    try:
        held = os.fstat(descriptor)
        acquired = (held.st_dev, held.st_ino)
        if expected is None:
            identity = acquired
        current = os.stat(name, dir_fd=_STATE_DIR_FD, follow_symlinks=False)
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
        if _CURRENT_ASSESSMENT_URL is not None:
            state["denied_scopes"] = sorted(
                set(state["denied_scopes"] + [_CURRENT_ASSESSMENT_URL])
            )
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
            self.headings = []
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
                self.stack
                and any(frame[0] in {"h1", "h2", "h3"} for frame in self.stack)
                and not any(self.stack[-1][1:])
            ):
                self.headings.append(data)
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
    text = " ".join(
        " ".join(parser.headings + parser.head_title).lower().split()
    )
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
        anchor = state["last_inventory_completed"]
        if _INVENTORY_COMPLETED_MONOTONIC is None:
            anchor = max(anchor, state["completed"])
        target = anchor + state["page_interval"]
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
            if wait >= _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Inventory gap exceeds phase budget", "access"
                )
            if time.time() + wait > _COLLECTION_STARTED + COLLECTION_TIMEOUT:
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
    """Read real public family metadata using existing authentication."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if os.environ.get("GITHUB_ACTIONS") == "true" and not token:
        raise AdapterError("Hosted workflow token required", "access")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        separator = "&" if "?" in path else "?"
        status, _, body = infrastructure_request_bytes(
            _WORKFLOW_GITHUB
            + path
            + separator
            + "_fresh="
            + str(time.time_ns()),
            headers=headers,
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            "Workflow pacing state unavailable", "access"
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
        if not isinstance(envelope, dict) or set(envelope) != {
            "head_sha",
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "state",
        }:
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
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def active_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded Opportunity Desk execution required", "access"
        )
    return _PHASE_END if _PHASE_END is not None else nonexport_end()


def check_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded Opportunity Desk execution required", "access"
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
    result = workflow_api("/actions/workflows/fetch-opportunitydesk.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path") != ".github/workflows/fetch-opportunitydesk.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "Opportunity Desk workflow is not serialized for this mode",
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
            "fetch-opportunitydesk.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable Opportunity Desk workflow head",
                "access",
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("OPPORTUNITYDESK_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native Opportunity Desk MAX approval required",
                "access",
            )
        return incoming
    try:
        approval = json.loads(raw)
        if (
            not isinstance(approval, dict)
            or set(approval) != {"schema", "family", "evidence", "state"}
            or type(approval["schema"]) is not int
            or approval["schema"] != 1
            or approval["family"] != FAMILY
            or not isinstance(approval["evidence"], str)
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/332"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        marker = (
            _REVIEWED_NATIVE_MIGRATION
            if os.environ.get("GITHUB_ACTIONS") == "true"
            else _LOCAL_RESEARCH_MIGRATION
        )
        if (
            not isinstance(marker, dict)
            or set(marker)
            != {
                "schema",
                "family",
                "evidence",
                "history_sha256",
                "state",
                "legacy_local",
            }
            or marker["schema"] != 1
            or marker["family"] != FAMILY
            or marker["evidence"] != approval["evidence"]
            or marker["state"] != approval["state"]
        ):
            raise ValueError(
                "Maintenance packet differs from reviewed literal"
            )
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native Opportunity Desk approval", "access"
        ) from None


def restored_state():
    """Newest exact artifact is mandatory after the one reviewed legacy set."""
    global _LOCAL_MATURE_RESTORE
    _LOCAL_MATURE_RESTORE = False
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Opportunity Desk family history missing", "access")
    if all(
        legacy_attempt_pin(row[2], row[3], row[5]) is not None
        for row in _HISTORY_COMPLETED
    ):
        return production_maintenance_state(legacy_transition_state())
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
            "Incomplete Opportunity Desk artifact inventory", "access"
        )
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest Opportunity Desk artifact unavailable", "access"
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
            "Invalid Opportunity Desk artifact binding", "access"
        )
    state = production_maintenance_state(
        download_inert_artifact(latest, artifact["id"], artifact["digest"])
    )
    _LOCAL_MATURE_RESTORE = os.environ.get("GITHUB_ACTIONS") != "true"
    return state


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    global _COLLECTION_LOCK_ID, _RESTORED_HISTORY_SIGNATURE
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    workflow_mode_guard()
    current_job_context()
    incoming = restored_state()
    _RESTORED_HISTORY_SIGNATURE = _HISTORY_SIGNATURE
    # No directory, lock or empty durable budget exists before reviewed state.
    if incoming["in_flight"]:
        raise AdapterError(
            "Owned Opportunity Desk artifact is unsettled", "access"
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
                "Local Opportunity Desk flight is unsettled", "access"
            )
        combined = merge_budgets(local, incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        save_budget(combined)
    _RESTORED = _STATE_TRUSTED = True


def collect():
    """Run the unchanged complete programme parser after durable restoration."""
    global _COLLECTION_STARTED
    if "DATA_SOURCE_TOKEN" in os.environ and not _PUBLISHING:
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
            _COLLECTION_STARTED = time.time()
            return collect_source_inventory(SOURCE_URL)
    except PhaseExpired:
        raise AdapterError(
            "Opportunity Desk collection phase exhausted", "fetch"
        ) from None


def export_family_artifact():
    path = os.environ.get("OPPORTUNITYDESK_PACING_ARTIFACT_PATH")
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
            "state": state,
        }

    journal_export_envelope(path, payload, atomic=False, signal=True)


def invalidate_budget():
    """Invalidate only the retained original FD, never a replacement name."""
    if _JOURNAL_FD is None:
        return
    info = os.fstat(_JOURNAL_FD)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink not in (0, 1)
        or (info.st_dev, info.st_ino) != _JOURNAL_ID
        or not _JOURNAL_DIRTY
        and info.st_nlink == 1
        and journal_version(info) != _JOURNAL_VERSION
    ):
        raise AdapterError("Original publisher journal changed", "access")
    os.ftruncate(_JOURNAL_FD, 0)
    os.fsync(_JOURNAL_FD)


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


def current_job_context():
    """Bind the executing source job and retain its actual30-minute deadline."""
    global _RUN_END
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    run_id, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    expected = (run_id, attempt, SOURCE_ID, head)
    run = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(run, expected)
    if (
        run.get("status") != "in_progress"
        or run.get("conclusion") is not None
        or os.environ.get("GITHUB_JOB") != "collect"
    ):
        raise AdapterError(
            "Opportunity Desk current job is not executing", "access"
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
            "Opportunity Desk current job inventory incomplete", "access"
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
        or job.get("conclusion") is not None
        or job.get("workflow_name") != "fetch-opportunitydesk"
        or type(job.get("runner_id")) is not int
        or job["runner_id"] <= 0
    ):
        raise AdapterError(
            "Opportunity Desk current job ownership mismatch", "access"
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
            "Untrusted Opportunity Desk job clock", "access"
        ) from None
    _RUN_END = min(_RUN_END, time.monotonic() + 1800 - max(elapsed, 0) - 2)
    check_deadline()


class PolicyStop(AdapterError):
    """A fully assessed response or durable explicit refusal ends this run."""

    policy_retained = True


def empty_budget(now):
    return {
        "policy_pending": None,
        "policy_receipt": None,
        "response_receipt": None,
        "policy_samples": {},
        "denied_scopes": [],
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
        "legacy_policy_history_unknown": True,
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
        or state["legacy_policy_history_unknown"] is not True
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
        or len(state["starts"]) > 1024
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
            raise AdapterError(
                "Missing pending transaction identity", "access"
            )
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
    if (
        not isinstance(state["denied_scopes"], list)
        or state["denied_scopes"] != sorted(set(state["denied_scopes"]))
        or any(
            not isinstance(url, str) or public_url(url) != url
            for url in state["denied_scopes"]
        )
        or state["denied_scopes"]
        and not state["blocked"]
        or not isinstance(state["policy_samples"], dict)
    ):
        raise AdapterError("Invalid retained source policy scopes", "access")
    for pending in [state["policy_pending"]]:
        validate_policy_receipt(pending, pending=True)
        if pending is not None and (
            not state["in_flight"] or pending["owner"] != state["flight_id"]
        ):
            raise AdapterError(
                "Unsettled policy lacks pending owner", "access"
            )
    for receipt in [
        state["policy_receipt"],
        *state["policy_samples"].values(),
    ]:
        validate_policy_receipt(receipt)
    for origin, receipt in state["policy_samples"].items():
        if receipt["origin"] != origin or receipt["decision"] != "allow":
            raise AdapterError(
                "Invalid non-authoritative policy sample", "access"
            )
    receipt = state["response_receipt"]
    if receipt is not None:
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {
                "owner",
                "request",
                "target",
                "status",
                "body_sha256",
                "body_read",
                "assessment",
            }
            or not hex_identity(receipt["owner"])
            or not hex_identity(receipt["request"])
            or public_url(receipt["target"]) != receipt["target"]
            or type(receipt["status"]) is not int
            or not 100 <= receipt["status"] <= 599
            or type(receipt["body_read"]) is not bool
            or receipt["assessment"] not in ("body", "headers_only_stop")
            or (receipt["body_sha256"] is None) == receipt["body_read"]
            or receipt["body_sha256"] is not None
            and not re.fullmatch(r"[0-9a-f]{64}", receipt["body_sha256"])
        ):
            raise AdapterError("Invalid physical response receipt", "access")
    if state["request_outcome"] == "response_assessed" and (
        receipt is None or receipt["request"] != state["request_id"]
    ):
        raise AdapterError("Unbound completed response receipt", "access")
    return state


def publisher_healthy(state):
    if _PUBLISHER_GATE_FAILED or (
        state["in_flight"] and state["flight_id"] != _ACTIVE_FLIGHT_ID
    ):
        raise AdapterError(
            "Unsettled publisher transaction retained", "access"
        )


def fail_policy(state=None):
    """Never heal an unassessed response, failed calculation or failed save."""
    global _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    _PUBLISHER_GATE_FAILED = True
    if state is not None:
        _FAILED_BUDGET = copy.deepcopy(state)
    elif _FAILED_BUDGET is None:
        with contextlib.suppress(BaseException):
            _FAILED_BUDGET = copy.deepcopy(load_budget())
    with contextlib.suppress(BaseException):
        invalidate_budget()


def begin_transaction(kind):
    global _ACTIVE_FLIGHT_ID
    check_deadline()
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Healthy restored publisher state required", "access"
        )
    with locked_budget():
        state = load_budget()
        if state["in_flight"] or state["blocked"]:
            raise AdapterError(
                "Publisher state requires reviewed recovery", "access"
            )
        now = time.time()
        if state["rate_limit_until"] > now:
            raise AdapterError("Retained timed publisher cooldown", "access")
        if state["rate_limit_strikes"] >= 100:
            raise AdapterError("Publisher rate counter exhausted", "access")
        ready = max(
            state["not_before"], state["completed"] + state["interval"]
        )
        if ready >= now + (active_deadline() - time.monotonic()):
            raise AdapterError(
                "Known publisher guard exceeds phase budget", "access"
            )
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
        # This fsynced owner remains True across original no-response retries,
        # all redirect validation and new/cached robots policy assessment.
        save_budget(state)
        _ACTIVE_FLIGHT_ID = ident


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
                                    state["policy_pending"] is not None
                                    or not state["in_flight"]
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
            wait = max(0, target - time.time())
            if _PUBLISHER_COMPLETED_MONOTONIC is not None:
                wait = max(
                    wait,
                    _PUBLISHER_COMPLETED_MONOTONIC
                    + _PUBLISHER_INTERVAL
                    - time.monotonic(),
                )
            if wait >= active_deadline() - time.monotonic():
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
    reason = (
        error.reason if isinstance(error, urllib.error.URLError) else error
    )
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
    try:
        now = time.time()
        until = retry_after_until(value, now)
        if status != 429 and until is None:
            return
        with locked_budget():
            state = load_budget()
            publisher_healthy(state)
            if status == 429:
                strikes = state["rate_limit_strikes"] + 1
                if strikes > 100:
                    fail_policy(state)
                    raise AdapterError(
                        "Publisher rate counter exhausted", "access"
                    )
                state["rate_limit_strikes"] = strikes
                factor = 2**strikes
                interval = max(state["interval"] * 2, 12.2 * factor)
                page = max(state["page_interval"] * 2, 30 * factor)
                fallback = now + 1800 * 2 ** (strikes - 1)
                if any(
                    not numeric(v) or v > 253402300799
                    for v in (interval, page, fallback)
                ):
                    fail_policy(state)
                    raise AdapterError(
                        "Unsupported observed rate constraint", "access"
                    )
                state.update(
                    rate_limit_strikes=strikes,
                    interval=interval,
                    page_interval=page,
                )
                state["rate_limit_until"] = max(
                    state["rate_limit_until"], fallback, until or 0
                )
            state["not_before"] = max(
                state["not_before"], state["rate_limit_until"], until or 0
            )
            state["observed_at"] = now
            save_budget(state)
    except BaseException:
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
    result["legacy_policy_history_unknown"] = (
        local["legacy_policy_history_unknown"]
        or incoming["legacy_policy_history_unknown"]
    )
    pending = [st for st in (local, incoming) if st["in_flight"]]
    if pending:
        if len({st["flight_id"] for st in pending}) != 1:
            raise AdapterError(
                "Conflicting pending publisher owners", "access"
            )
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
    result["denied_scopes"] = sorted(
        set(local["denied_scopes"] + incoming["denied_scopes"])
    )
    result["policy_samples"] = {
        **incoming["policy_samples"],
        **local["policy_samples"],
    }
    pending_policies = [
        st["policy_pending"]
        for st in (local, incoming)
        if st["policy_pending"] is not None
    ]
    if pending_policies:
        if any(value != pending_policies[0] for value in pending_policies):
            raise AdapterError(
                "Conflicting pending policy assessments", "access"
            )
        result["policy_pending"] = pending_policies[0]
    if pending:
        result["response_receipt"] = owner["response_receipt"]
        result["policy_receipt"] = owner["policy_receipt"]
    result["in_flight"] = bool(pending)
    return validate_budget(result)


def is_publisher(url):
    try:
        return (
            urllib.parse.urlsplit(url).hostname or ""
        ).lower().removeprefix("www.") == "opportunitydesk.org"
    except (TypeError, ValueError):
        return False


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not is_publisher(value)
        or parsed.username
        or parsed.password
    ):
        raise AdapterError(
            "Unreviewed Opportunity Desk source route", "access"
        )
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def is_inventory(url):
    """The complete RSS feed is inventory; article and REP reads are details."""
    return is_publisher(url) and urllib.parse.urlsplit(url).path == "/feed/"


@bounded_infrastructure
def infrastructure_request_bytes(
    url, *, headers=None, method="GET", payload=None, byte_limit=30_000_000
):
    """Bounded workflow/artifact reads do not create a publisher allowance."""
    if is_publisher(url):
        raise AdapterError(
            "Publisher cannot use infrastructure transport", "access"
        )
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={**(headers or {}), "User-Agent": _USER_AGENT},
    )
    try:
        response = _OPENER.open(request, timeout=request_timeout())
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(byte_limit + 1)
        if len(body) > byte_limit:
            raise AdapterError(
                "Infrastructure response exceeds bound", "fetch"
            )
        return response.status, response.headers, body


@durable_transaction("physical")
def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    inventory=None,
    deadline=None,
    retry_jitter=0,
):
    """One physical source attempt; original publication remains GH6+RA."""
    global _LOCAL_FIRST_WIRE_VERIFIED, _CURRENT_ASSESSMENT_URL
    if not is_publisher(url):
        if (
            urllib.parse.urlsplit(url).hostname or ""
        ).lower() != "api.github.com":
            raise AdapterError("Unreviewed publication API host", "publish")
        # Original GitHub call guard owns its timer; never nest an alarm.
        if _RUN_END is not None:
            check_deadline()
            deadline = min(
                active_deadline(),
                deadline if deadline is not None else active_deadline(),
            )
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
        raise AdapterError(
            "Healthy source collection phase required", "access"
        )
    inventory = is_inventory(url) if inventory is None else inventory
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
    _CURRENT_ASSESSMENT_URL = current
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
    if not _LOCAL_FIRST_WIRE_VERIFIED:
        # Full metadata audit has its own per-call bounds, not one 30s alarm.
        verify_family_before_wire()
        _LOCAL_FIRST_WIRE_VERIFIED = True
    request_headers = {
        k: v for k, v in (headers or {}).items() if k.lower() != "user-agent"
    }
    request_headers["User-Agent"] = _USER_AGENT
    request = urllib.request.Request(
        current, method="GET", headers=request_headers
    )
    with publisher_attempt(inventory) as frame, operation_guard():
        if not robots:
            apply_robots_policy(current, _ROBOTS[origin])
        try:
            _CURRENT_ASSESSMENT_URL = current
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
        if response.status == 429:
            try:
                record_retry_after(response.headers.get("Retry-After"), 429)
            except BaseException:
                fail_policy()
                try:
                    response.close()
                except BaseException:
                    fail_policy()
                raise
            try:
                response.close()
            except BaseException:
                fail_policy()
                raise
            response_receipt(current, 429, assessed="headers_only_stop")
            # Only our intentional stop after healthy close is assessed.
            raise PolicyStop("Publisher stopped: HTTP 429", "fetch", 429)
        try:
            with response:
                status, response_headers = response.status, response.headers
                record_retry_after(response_headers.get("Retry-After"), status)
                if not robots and status in (401, 403, 451):
                    retain_publisher_hold()
                body = response.read(5_000_001)
                if len(body) > 5_000_000:
                    raise AdapterError("Response exceeds 5 MB", "fetch")
                length = response_headers.get("Content-Length")
                if length and (
                    not length.isdigit() or int(length) != len(body)
                ):
                    raise AdapterError(
                        "Incomplete source response length", "fetch"
                    )
                if not robots or status in (200, 429):
                    reject_visible_challenge(
                        body, response_headers, challenge_only=status == 429
                    )
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
                if robots and status == 200:
                    groups = parse_robots(body)
                    target_origin, target = _REP_TARGETS.get(
                        origin, (origin, SOURCE_URL)
                    )
                    apply_robots_policy(target, groups)
                    # Preserve the original caller-origin cache. A later content
                    # redirect to an alias must still check that origin's REP.
                    _ROBOTS[target_origin] = groups
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
                response_receipt(current, status, body, assessed="body")
                frame["assessed"] = True

        except BaseException:
            frame["assessed"] = False
            fail_policy()
            raise
        if not robots and status in (401, 403, 451):
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
            raise AdapterError(
                "Robots purpose cannot follow content", "access"
            )

    for attempt in range(2):
        current = url
        try:
            for hop in range(4):
                checked(current)
                if (
                    not robots
                    and urllib.parse.urlsplit(current).path == "/robots.txt"
                ):
                    hop_origin = urllib.parse.urlsplit(current)
                    hop_origin = hop_origin.scheme + "://" + hop_origin.netloc
                    _REP_TARGETS[hop_origin] = (hop_origin, url)
                status, headers, body = request_bytes(
                    current,
                    interval=max(min_interval, 12.2),
                    robots=urllib.parse.urlsplit(current).path
                    == "/robots.txt",
                    inventory=(
                        inventory
                        and urllib.parse.urlsplit(current).path
                        != "/robots.txt"
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
                    if (
                        not robots
                        and urllib.parse.urlsplit(current).path
                        != "/robots.txt"
                    ):
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
                document = body.decode(
                    headers.get_content_charset() or "utf-8"
                )
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
                    "Publisher connection failed: " + safe_error(error),
                    "fetch",
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
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["directives_seen"]:
                groups.append(group)
                group = fresh()
            group["agents"].append(value.lower())
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
        raise AdapterError(
            "Reserved publisher policy owner required", "access"
        )
    pending = policy_begin(url, groups)
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    policies = selected_robots_policies(groups)
    if any(
        group["numeric_invalid"] for policy in policies for group in policy
    ):
        raise AdapterError(
            "Unsupported applicable robots numeric policy", "access"
        )
    delay = max(
        [12.2] + [g["delay"] for selected in policies for g in selected]
    )
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        _PUBLISHER_INTERVAL = max(
            _PUBLISHER_INTERVAL, state["interval"], delay
        )
        state["interval"] = _PUBLISHER_INTERVAL
        state["observed_at"] = time.time()
        save_budget(state)

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
            policy_finish(pending, "deny")
            retain_publisher_hold()
            raise PolicyStop(
                "Robots policy disallows this source endpoint", "access"
            )
    policy_finish(pending, "allow")


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
            production_maintenance_state(empty_budget(time.time()))
            if args.publish:
                with phase("proof", 30):
                    publication_window()
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
        pin = legacy_attempt_pin(run_id, attempt, head)
        if (
            not pin
            or pin["jobs"]
            or attempt != pin["attempt"]
            or head != pin["head"]
            or pin["conclusion"] != "cancelled"
            or exact.get("conclusion") != "cancelled"
        ):
            raise AdapterError("Unreviewed jobless family attempt", "access")
        _HISTORY_ATTEMPT_PROOFS[(run_id, attempt)] = {
            "head": head,
            "conclusion": exact.get("conclusion"),
            "jobs_sha256": hashlib.sha256(
                json.dumps(
                    jobs, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        }
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
    _HISTORY_ATTEMPT_PROOFS[(run_id, attempt)] = {
        "head": head,
        "conclusion": exact.get("conclusion"),
        "jobs_sha256": hashlib.sha256(
            json.dumps(
                sorted(jobs, key=lambda job: job["id"]),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
    }
    return max(finished), min(began), *identity


def family_wrappers():
    """Bounded full inventory: only this exact hosted attempt may run."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    seen, rows, total, current_seen = set(), [], None, False
    for page in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-opportunitydesk.yml/runs"
            + f"?per_page=100&page={page}"
        )
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(runs, list)
            or len(runs) > 100
        ):
            raise AdapterError("Invalid family inventory", "access")
        if total is not None and count != total:
            raise InventoryRace("Family inventory changed", "access")
        total = count
        for run in runs:
            run_id, attempt, source, head = validate_family_identity(run)
            if run_id in seen or attempt > 100:
                raise AdapterError(
                    "Duplicate/unbounded workflow run", "access"
                )
            seen.add(run_id)
            if current and run_id == current[0]:
                if (
                    (run_id, attempt) != current
                    or head != os.environ["GITHUB_SHA"]
                    or run.get("status") != "in_progress"
                ):
                    raise AdapterError(
                        "Current immutable attempt changed", "access"
                    )
                current_seen = True
            elif run.get("status") != "completed":
                raise AdapterError(
                    "Another family publisher is not terminal", "access"
                )
            rows.append(run)
        if len(rows) >= total:
            if len(rows) != total or hosted and not current_seen:
                raise AdapterError("Incomplete family inventory", "access")
            return rows
        if len(runs) != 100:
            raise AdapterError("Truncated family history", "access")
    raise AdapterError("Family history exceeds reviewed bound", "access")


def family_wrapper_signature(rows):
    return sorted(
        (
            run["id"],
            run["run_attempt"],
            run["head_sha"],
            run.get("status"),
            run.get("conclusion"),
        )
        for run in rows
    )


def scan_family_history():
    """Authenticate every prior attempt and fence the full wrapper set."""
    global _HISTORY_COMPLETED, _HISTORY_JOBLESS, _HISTORY_SIGNATURE
    global _HISTORY_ATTEMPT_PROOFS
    _HISTORY_COMPLETED, _HISTORY_JOBLESS = [], []
    _HISTORY_ATTEMPT_PROOFS, _HISTORY_SIGNATURE = {}, None
    current = (
        current_run_identity()
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else None
    )
    initial = family_wrappers()
    candidates = []
    for run in initial:
        run_id, attempt, source, head = validate_family_identity(run)
        is_current = current is not None and run_id == current[0]
        last_completed = attempt - 1 if is_current else attempt
        for number in range(1, last_completed + 1):
            wrapper = (
                run
                if number == attempt
                else workflow_api(f"/actions/runs/{run_id}/attempts/{number}")
            )
            row = completed_identity(wrapper, (run_id, number, source, head))
            if row is not None:
                candidates.append(row)
    if family_wrapper_signature(initial) != family_wrapper_signature(
        family_wrappers()
    ):
        raise InventoryRace(
            "Family wrappers changed during exact jobs", "access"
        )
    _HISTORY_COMPLETED = sorted(candidates)
    if len(candidates) > 1 and (
        _HISTORY_COMPLETED[-1][0] == _HISTORY_COMPLETED[-2][0]
    ):
        raise AdapterError("Ambiguous actual completion", "access")
    proof = {
        "wrappers": family_wrapper_signature(initial),
        "attempts": [
            [*key, value]
            for key, value in sorted(_HISTORY_ATTEMPT_PROOFS.items())
        ],
    }
    _HISTORY_SIGNATURE = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return _HISTORY_COMPLETED[-1][2:] if candidates else None


def legacy_pin_items():
    """Normalize exact run/attempt pins without latest-attempt substitution."""
    result = {}
    for key, pin in _LEGACY_JOB_PINS.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[1-9][0-9]*(?::[1-9][0-9]*)?", key)
            or not isinstance(pin, dict)
            or set(pin) != {"attempt", "head", "conclusion", "jobs"}
            or type(pin["attempt"]) is not int
            or not 1 <= pin["attempt"] <= 100
            or not isinstance(pin["head"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", pin["head"])
            or not isinstance(pin["jobs"], list)
            or len(pin["jobs"]) > 100
        ):
            raise AdapterError("Untrusted exact legacy pin", "access")
        parts = key.split(":")
        identity = (int(parts[0]), pin["attempt"])
        if len(parts) == 2 and int(parts[1]) != pin["attempt"]:
            raise AdapterError("Legacy attempt key mismatch", "access")
        if identity in result:
            raise AdapterError("Duplicate normalized legacy attempt", "access")
        result[identity] = pin
    return result


def legacy_attempt_pin(run_id, attempt, head):
    pin = legacy_pin_items().get((run_id, attempt))
    return pin if pin is not None and pin["head"] == head else None


def verify_family_before_wire():
    """Recheck all restored ancestry after quiet, immediately before wire."""
    try:
        if _RESTORED_HISTORY_SIGNATURE is None:
            raise AdapterError("Restored family proof missing", "access")
        workflow_mode_guard()
        current_job_context()
        latest_family_run()
        if _HISTORY_SIGNATURE != _RESTORED_HISTORY_SIGNATURE:
            raise AdapterError("Family history changed before wire", "access")
        workflow_mode_guard()
    except BaseException:
        journal_failure()
        raise


def legacy_transition_state():
    approval = (
        _REVIEWED_NATIVE_MIGRATION
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else _LOCAL_RESEARCH_MIGRATION
    )
    if not isinstance(approval, dict) or set(approval) != {
        "schema",
        "family",
        "evidence",
        "history_sha256",
        "state",
        "legacy_local",
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
        or not isinstance(approval["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/332"
            r"#issuecomment-[1-9][0-9]*",
            approval["evidence"],
        )
        or approval["history_sha256"] != digest
        or len(_HISTORY_COMPLETED) + len(_HISTORY_JOBLESS)
        != len(_LEGACY_JOB_PINS)
    ):
        raise AdapterError("Untrusted legacy transition approval", "access")
    pins = legacy_pin_items()
    actual = {(row[2], row[3]): row for row in _HISTORY_COMPLETED}
    jobless = {(row[0], row[1]): row for row in _HISTORY_JOBLESS}
    if set(actual) | set(jobless) != set(pins) or set(actual) & set(jobless):
        raise AdapterError("Legacy exact attempt set changed", "access")
    bounds = []
    checked_artifacts = set()
    for (run_id, attempt), pin in pins.items():
        proof = {
            "head": pin["head"],
            "conclusion": pin["conclusion"],
            "jobs_sha256": hashlib.sha256(
                json.dumps(
                    sorted(pin["jobs"], key=lambda job: job["id"]),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        }
        if _HISTORY_ATTEMPT_PROOFS.get((run_id, attempt)) != proof:
            raise AdapterError("Legacy full exact job proof changed", "access")
        if not pin["jobs"]:
            if jobless.get((run_id, attempt)) != (
                run_id,
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
                run_id,
                pin["attempt"],
                SOURCE_ID,
                pin["head"],
            )
            if actual.get((run_id, attempt)) != expected:
                raise AdapterError(
                    "Legacy actual job bounds changed", "access"
                )
            bounds.extend(ends)
        jobs = workflow_api(
            f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"
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
        if run_id not in checked_artifacts:
            artifacts = workflow_api(
                f"/actions/runs/{run_id}/artifacts?per_page=100"
            )
            if (
                artifacts.get("total_count") != 0
                or artifacts.get("artifacts") != []
            ):
                raise AdapterError(
                    "Legacy artifact provenance changed", "access"
                )
            checked_artifacts.add(run_id)
    legacy = verify_legacy_local(approval["legacy_local"])
    state = validate_budget(approval["state"])
    if (
        not state["legacy_rate_history_unknown"]
        or state["in_flight"]
        or not set(bounds).issubset(state["starts"])
        or state["completed"] < max(bounds)
        or state["last_inventory_completed"] < max(bounds)
        or state["not_before"] < max(bounds) + 60
    ):
        raise AdapterError(
            "Legacy complete conservative MAX missing", "access"
        )
    if legacy is not None:
        if (
            state["interval"] < max(12.2, legacy["interval"])
            or state["not_before"] < legacy["until"]
            or not set(legacy["starts"]).issubset(state["starts"])
            or legacy.get("blocked", False)
            and not state["blocked"]
        ):
            raise AdapterError(
                "Legacy local stronger constraints missing", "access"
            )
    return state


def verify_legacy_local(packet):
    """Read the exact original publisher file and separate collection lock."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    mode = "hosted" if hosted else "local"
    if (
        not isinstance(packet, dict)
        or set(packet) != {"mode", "publisher", "collection"}
        or packet["mode"] != mode
        or hosted
        and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise AdapterError("Exact legacy mode observations required", "access")
    parent_path = tempfile.gettempdir()
    before = os.stat(parent_path, follow_symlinks=False)
    parent_id = (before.st_dev, before.st_ino)
    parent = os.open(
        parent_path,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    states = []
    try:

        def current_parent():
            held = os.fstat(parent)
            named = os.stat(parent_path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(held.st_mode)
                or (held.st_dev, held.st_ino) != parent_id
                or (named.st_dev, named.st_ino) != parent_id
            ):
                raise AdapterError("Legacy temporary parent changed", "access")

        current_parent()
        names = {
            "publisher": os.path.basename(budget_path("opportunitydesk.org")),
            "collection": "opportunitydesk.lock",
        }
        for kind, name in names.items():
            observed = packet[kind]
            if not isinstance(observed, dict) or observed.get(
                "status"
            ) not in ("absent", "present"):
                raise AdapterError(
                    "Exact legacy file observation required", "access"
                )
            if observed["status"] == "absent":
                if set(observed) != {"status"}:
                    raise AdapterError("Invalid absent observation", "access")
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    current_parent()
                    continue
                raise AdapterError("Legacy file is no longer absent", "access")
            if hosted or set(observed) != {
                "status",
                "identity",
                "raw_sha256",
                "raw_bytes",
            }:
                raise AdapterError(
                    "Invalid legacy present observation", "access"
                )
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            identity = tuple(observed["identity"])
            try:

                def original():
                    current_parent()
                    info = os.fstat(descriptor)
                    named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (
                        (info.st_dev, info.st_ino) != identity
                        or (named.st_dev, named.st_ino) != identity
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) not in (0o600, 0o644)
                    ):
                        raise AdapterError(
                            "Legacy file authority changed", "access"
                        )
                    return journal_version(info)

                prior = original()
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                original()
                chunks = []
                size = 0
                while True:
                    chunk = os.read(descriptor, min(32769 - size, 8192))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 32768:
                        raise AdapterError(
                            "Legacy file exceeds bound", "access"
                        )
                raw = b"".join(chunks)
                if (
                    original() != prior
                    or len(raw) != observed["raw_bytes"]
                    or hashlib.sha256(raw).hexdigest()
                    != observed["raw_sha256"]
                ):
                    raise AdapterError(
                        "Exact legacy raw bytes changed", "access"
                    )
                if kind == "publisher":
                    state = json.loads(raw)
                    if (
                        not isinstance(state, dict)
                        or set(state) != {"starts", "interval", "until"}
                        or not numeric(state["interval"])
                        or state["interval"] < 6
                        or not numeric(state["until"])
                        or not isinstance(state["starts"], list)
                        or len(state["starts"]) > 10
                        or any(not numeric(t) for t in state["starts"])
                        or state["starts"] != sorted(set(state["starts"]))
                    ):
                        raise AdapterError(
                            "Invalid legacy publisher state", "access"
                        )
                    states.append(state)
            finally:
                close_legacy_descriptor(descriptor, identity)
        current_parent()
    finally:
        close_legacy_descriptor(parent, parent_id, True)
    return states[0] if states else None


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
                    raise AdapterError(
                        "Workflow output path changed", "access"
                    )

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
                raise AdapterError(
                    "Incomplete pacing artifact write", "access"
                )
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
    path = os.environ.get("OPPORTUNITYDESK_PACING_ARTIFACT_PATH")
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


def collect_source_inventory(url):
    """Keep the original full read and source-policy validation pending."""
    return collect_inventory()


collect_source_inventory = durable_transaction("policy")(
    collect_source_inventory
)


@contextlib.contextmanager
def publication_http_guard(deadline):
    """Clamp the unchanged original call guard without competing alarms."""
    if _RUN_END is not None:
        deadline = min(deadline, active_deadline())
    with original_publication_http_guard(deadline) as end:
        yield end


def prove_publication_window():
    """Preserve original job proof and bind its immutable read cutoff."""
    global _RUN_END
    window = original_prove_publication_window()
    if _RUN_END is not None:
        _RUN_END = min(_RUN_END, window["read"])
    return window


_LOCAL_RESEARCH_MIGRATION = {
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/332#issuecomment-6214861381",
    "family": "youthopps-opportunitydesk-publisher-v1",
    "history_sha256": "750a4fa70f95217bb87c364447ed391f42e86498a51a342655547d867730e5ce",
    "legacy_local": {
        "collection": {"status": "absent"},
        "mode": "local",
        "publisher": {"status": "absent"},
    },
    "schema": 1,
    "state": {
        "blocked": False,
        "completed": 1792079604.0,
        "completed_flight_id": None,
        "completed_request_id": None,
        "denied_scopes": [],
        "family": "youthopps-opportunitydesk-publisher-v1",
        "flight_id": None,
        "flight_kind": None,
        "flight_started": 0,
        "in_flight": False,
        "interval": 12.2,
        "last_inventory_completed": 1792079604.0,
        "legacy_policy_history_unknown": True,
        "legacy_rate_history_unknown": True,
        "not_before": 1792151366.4542053,
        "observed_at": 1792151306.4542053,
        "page_interval": 30,
        "policy_pending": None,
        "policy_receipt": None,
        "policy_samples": {},
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "request_id": None,
        "request_inventory": False,
        "request_outcome": None,
        "request_started": 0,
        "response_receipt": None,
        "schema": 3,
        "starts": [
            1791477296.0,
            1791488702.0,
            1791488820.0,
            1791527006.0,
            1791552920.0,
            1791585981.0,
            1791612307.0,
            1791624915.0,
            1791625155.0,
            1791636386.0,
            1791653391.0,
            1791669278.0,
            1791698585.0,
            1791724145.0,
            1791756637.0,
            1791785606.0,
            1791820065.0,
            1791852780.0,
            1791901570.0,
            1791932273.0,
            1791954374.0,
            1791969292.0,
            1791979802.0,
            1791998630.0,
            1792014998.0,
            1792040119.0,
            1792046193.0,
            1792060703.0,
            1792067390.0,
            1792079604.0,
        ],
    },
}
_REVIEWED_NATIVE_MIGRATION = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/332#issuecomment-6215873880',
 'family': 'youthopps-opportunitydesk-publisher-v1',
 'history_sha256': '750a4fa70f95217bb87c364447ed391f42e86498a51a342655547d867730e5ce',
 'legacy_local': {'collection': {'status': 'absent'},
                  'mode': 'hosted',
                  'publisher': {'status': 'absent'}},
 'schema': 1,
 'state': {'blocked': False,
           'completed': 1792152751.610135,
           'completed_flight_id': '4228ac85d495d507bb06a1db3c25e1a7',
           'completed_request_id': 'd97b8d2f2a1845db8d1514f37675ad91',
           'denied_scopes': [],
           'family': 'youthopps-opportunitydesk-publisher-v1',
           'flight_id': '4228ac85d495d507bb06a1db3c25e1a7',
           'flight_kind': 'policy',
           'flight_started': 1792152478.019536,
           'in_flight': False,
           'interval': 12.2,
           'last_inventory_completed': 1792152605.0002131,
           'legacy_policy_history_unknown': True,
           'legacy_rate_history_unknown': True,
           'not_before': 1792154945.9386294,
           'observed_at': 1792154885.9386294,
           'page_interval': 30,
           'policy_pending': None,
           'policy_receipt': {'body_sha256': '8fa3036c68bfcbd32365f6225d24333264093b7cd38d306e106b4dbdc934fd5b',
                              'decision': 'allow',
                              'nonce': 'b163ba2bbbcfe3827ac6de472e8f4276',
                              'origin': 'https://opportunitydesk.org',
                              'owner': '4228ac85d495d507bb06a1db3c25e1a7',
                              'target': 'https://opportunitydesk.org/2026/10/15/hmu-fellowship-in-ideas-2027/'},
           'policy_samples': {'https://opportunitydesk.org': {'body_sha256': '8fa3036c68bfcbd32365f6225d24333264093b7cd38d306e106b4dbdc934fd5b',
                                                              'decision': 'allow',
                                                              'nonce': 'b163ba2bbbcfe3827ac6de472e8f4276',
                                                              'origin': 'https://opportunitydesk.org',
                                                              'owner': '4228ac85d495d507bb06a1db3c25e1a7',
                                                              'target': 'https://opportunitydesk.org/2026/10/15/hmu-fellowship-in-ideas-2027/'}},
           'rate_limit_strikes': 0,
           'rate_limit_until': 0,
           'request_id': 'd97b8d2f2a1845db8d1514f37675ad91',
           'request_inventory': False,
           'request_outcome': 'response_assessed',
           'request_started': 1792152750.7069058,
           'response_receipt': {'assessment': 'body',
                                'body_read': True,
                                'body_sha256': '317d2b81cfb65eed731881779ad3ba97628b6fc0fa9ec9bcd7b7efe969901d2d',
                                'owner': '4228ac85d495d507bb06a1db3c25e1a7',
                                'request': 'd97b8d2f2a1845db8d1514f37675ad91',
                                'status': 200,
                                'target': 'https://opportunitydesk.org/2026/10/15/hmu-fellowship-in-ideas-2027/'},
           'schema': 3,
           'starts': [1791477296.0,
                      1791488702.0,
                      1791488820.0,
                      1791527006.0,
                      1791552920.0,
                      1791585981.0,
                      1791612307.0,
                      1791624915.0,
                      1791625155.0,
                      1791636386.0,
                      1791653391.0,
                      1791669278.0,
                      1791698585.0,
                      1791724145.0,
                      1791756637.0,
                      1791785606.0,
                      1791820065.0,
                      1791852780.0,
                      1791901570.0,
                      1791932273.0,
                      1791954374.0,
                      1791969292.0,
                      1791979802.0,
                      1791998630.0,
                      1792014998.0,
                      1792040119.0,
                      1792046193.0,
                      1792060703.0,
                      1792067390.0,
                      1792079604.0,
                      1792152538.0158782,
                      1792152565.7147186,
                      1792152565.7152228,
                      1792152565.721678,
                      1792152566.5808086,
                      1792152566.5863755,
                      1792152566.5876887,
                      1792152596.5873275,
                      1792152596.5890553,
                      1792152596.5930917,
                      1792152596.5948925,
                      1792152604.9943717,
                      1792152604.9975522,
                      1792152605.0002131,
                      1792152617.1982803,
                      1792152617.2018144,
                      1792152617.2088008,
                      1792152617.2120683,
                      1792152629.8363757,
                      1792152629.8828855,
                      1792152629.8884952,
                      1792152642.0867693,
                      1792152642.0930743,
                      1792152642.0996802,
                      1792152642.1021557,
                      1792152647.532653,
                      1792152647.565431,
                      1792152647.5701149,
                      1792152659.7683632,
                      1792152659.7718506,
                      1792152659.7788827,
                      1792152659.7809403,
                      1792152661.0065079,
                      1792152661.0259767,
                      1792152661.0328507,
                      1792152673.2314646,
                      1792152673.2359016,
                      1792152673.2419448,
                      1792152673.2468712,
                      1792152674.049008,
                      1792152674.0785341,
                      1792152674.0986552,
                      1792152686.2969124,
                      1792152686.2998834,
                      1792152686.3054254,
                      1792152686.307248,
                      1792152686.7242644,
                      1792152686.7488477,
                      1792152686.7522945,
                      1792152698.9516277,
                      1792152698.9576435,
                      1792152698.9632804,
                      1792152698.96554,
                      1792152699.7432654,
                      1792152699.7974153,
                      1792152699.801811,
                      1792152712.0005424,
                      1792152712.0035272,
                      1792152712.013202,
                      1792152712.0177875,
                      1792152712.7079568,
                      1792152712.737121,
                      1792152712.743185,
                      1792152724.9426227,
                      1792152724.956942,
                      1792152724.9634662,
                      1792152724.968503,
                      1792152725.3442144,
                      1792152725.4705894,
                      1792152725.4805138,
                      1792152737.6783955,
                      1792152737.6800952,
                      1792152737.684431,
                      1792152737.6862733,
                      1792152738.378622,
                      1792152738.4902904,
                      1792152738.5087228,
                      1792152750.7069058,
                      1792152750.7094073,
                      1792152750.7143223,
                      1792152750.7174757,
                      1792152751.445544,
                      1792152751.5721872,
                      1792152751.5854123]}}
_LEGACY_JOB_PINS = {
    "37801002187:1": {
        "attempt": 1,
        "conclusion": "cancelled",
        "head": "e10cefdc2e0f593b726e6954e12495bbcd3cf0ab",
        "jobs": [],
    },
    "37809644701:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "ce76427dcc2bfc744961a6058bac6d0100bea31e",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/113423395796",
                "completed_at": "2026-10-08T16:34:56Z",
                "conclusion": "success",
                "created_at": "2026-10-08T16:34:05Z",
                "head_branch": "main",
                "head_sha": "ce76427dcc2bfc744961a6058bac6d0100bea31e",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/37809644701/job/113423395796",
                "id": 113423395796,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaaI-r1A",
                "run_attempt": 1,
                "run_id": 37809644701,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/37809644701",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001368,
                "runner_name": "GitHub Actions 1000001368",
                "started_at": "2026-10-08T16:34:07Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-08T16:34:09Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-08T16:34:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:11Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-08T16:34:09Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:11Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-08T16:34:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:12Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-08T16:34:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:54Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-08T16:34:12Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:54Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-08T16:34:54Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:54Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-08T16:34:54Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:54Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-08T16:34:54Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T16:34:54Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-08T16:34:54Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/113423395796",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "37834028746:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "dfde460c43865b8beb059788348252f54b1c4da5",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/113506379400",
                "completed_at": "2026-10-08T19:45:02Z",
                "conclusion": "success",
                "created_at": "2026-10-08T19:43:07Z",
                "head_branch": "main",
                "head_sha": "dfde460c43865b8beb059788348252f54b1c4da5",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/37834028746/job/113506379400",
                "id": 113506379400,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAabYHmiA",
                "run_attempt": 1,
                "run_id": 37834028746,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/37834028746",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001394,
                "runner_name": "GitHub Actions 1000001394",
                "started_at": "2026-10-08T19:43:08Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-08T19:43:09Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-08T19:43:09Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:43:10Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-08T19:43:09Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:43:10Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-08T19:43:10Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:43:11Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-08T19:43:10Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:00Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-08T19:43:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:00Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-08T19:45:00Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:01Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-08T19:45:00Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:01Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-08T19:45:01Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:01Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-08T19:45:01Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/113506379400",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "37834039120:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "dfde460c43865b8beb059788348252f54b1c4da5",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/113507203323",
                "completed_at": "2026-10-08T19:47:00Z",
                "conclusion": "success",
                "created_at": "2026-10-08T19:45:03Z",
                "head_branch": "main",
                "head_sha": "dfde460c43865b8beb059788348252f54b1c4da5",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/37834039120/job/113507203323",
                "id": 113507203323,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAabY54-w",
                "run_attempt": 1,
                "run_id": 37834039120,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/37834039120",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001395,
                "runner_name": "GitHub Actions 1000001395",
                "started_at": "2026-10-08T19:45:05Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-08T19:45:07Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-08T19:45:06Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:07Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-08T19:45:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:07Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-08T19:45:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:45:08Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-08T19:45:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:46:57Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-08T19:45:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:46:58Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-08T19:46:57Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:46:58Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-08T19:46:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:46:58Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-08T19:46:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-08T19:46:58Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-08T19:46:58Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/113507203323",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "37893104304:1": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/113698232213",
                "completed_at": "2026-10-09T06:23:26Z",
                "conclusion": "failure",
                "created_at": "2026-10-09T06:21:29Z",
                "head_branch": "main",
                "head_sha": "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/37893104304/job/113698232213",
                "id": 113698232213,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaePFXlQ",
                "run_attempt": 1,
                "run_id": 37893104304,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/37893104304",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001508,
                "runner_name": "GitHub Actions 1000001508",
                "started_at": "2026-10-09T06:21:31Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-09T06:21:32Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-09T06:21:31Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:21:33Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-09T06:21:32Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:21:33Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-09T06:21:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:21:34Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-09T06:21:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:23:23Z",
                        "conclusion": "failure",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-09T06:21:34Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:23:24Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-09T06:23:23Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:23:24Z",
                        "conclusion": "skipped",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-09T06:23:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:23:24Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-09T06:23:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T06:23:24Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-09T06:23:24Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/113698232213",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "37937675734:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/113843728201",
                "completed_at": "2026-10-09T13:35:20Z",
                "conclusion": "success",
                "created_at": "2026-10-09T13:33:22Z",
                "head_branch": "main",
                "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/37937675734/job/113843728201",
                "id": 113843728201,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAagZ1vSQ",
                "run_attempt": 1,
                "run_id": 37937675734,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/37937675734",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001545,
                "runner_name": "GitHub Actions 1000001545",
                "started_at": "2026-10-09T13:33:24Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-09T13:33:25Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-09T13:33:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:33:26Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-09T13:33:25Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:33:26Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-09T13:33:26Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:33:27Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-09T13:33:26Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:35:17Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-09T13:33:27Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:35:17Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-09T13:35:17Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:35:17Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-09T13:35:17Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:35:17Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-09T13:35:17Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T13:35:17Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-09T13:35:17Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/113843728201",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38000905862:1": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114058442507",
                "completed_at": "2026-10-09T22:46:21Z",
                "conclusion": "failure",
                "created_at": "2026-10-09T22:45:19Z",
                "head_branch": "main",
                "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38000905862/job/114058442507",
                "id": 114058442507,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAajmm3Cw",
                "run_attempt": 1,
                "run_id": 38000905862,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38000905862",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001586,
                "runner_name": "GitHub Actions 1000001586",
                "started_at": "2026-10-09T22:45:20Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-09T22:45:22Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-09T22:45:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:45:22Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-09T22:45:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:45:22Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-09T22:45:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:45:23Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-09T22:45:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:46:19Z",
                        "conclusion": "failure",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-09T22:45:23Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:46:19Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-09T22:46:19Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:46:19Z",
                        "conclusion": "skipped",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-09T22:46:19Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:46:20Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-09T22:46:19Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-09T22:46:20Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-09T22:46:20Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114058442507",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38029628291:1": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "6e5922277de22b96874c23a705a7d838512c6caf",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114147644084",
                "completed_at": "2026-10-10T06:05:07Z",
                "conclusion": "failure",
                "created_at": "2026-10-10T06:03:44Z",
                "head_branch": "main",
                "head_sha": "6e5922277de22b96874c23a705a7d838512c6caf",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38029628291/job/114147644084",
                "id": 114147644084,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAak7rStA",
                "run_attempt": 1,
                "run_id": 38029628291,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38029628291",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001626,
                "runner_name": "GitHub Actions 1000001626",
                "started_at": "2026-10-10T06:03:46Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-10T06:03:48Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-10T06:03:46Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:03:49Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-10T06:03:48Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:03:49Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-10T06:03:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:03:50Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-10T06:03:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:05:04Z",
                        "conclusion": "failure",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-10T06:03:50Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:05:05Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-10T06:05:04Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:05:05Z",
                        "conclusion": "skipped",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-10T06:05:05Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:05:05Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-10T06:05:05Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T06:05:05Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-10T06:05:05Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114147644084",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38041798825:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114183350100",
                "completed_at": "2026-10-10T09:35:15Z",
                "conclusion": "success",
                "created_at": "2026-10-10T09:33:18Z",
                "head_branch": "main",
                "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38041798825/job/114183350100",
                "id": 114183350100,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaldunVA",
                "run_attempt": 1,
                "run_id": 38041798825,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38041798825",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001710,
                "runner_name": "GitHub Actions 1000001710",
                "started_at": "2026-10-10T09:33:20Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-10T09:33:22Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-10T09:33:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:33:22Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-10T09:33:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:33:22Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-10T09:33:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:33:23Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-10T09:33:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:35:13Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-10T09:33:23Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:35:13Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-10T09:35:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:35:13Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-10T09:35:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:35:13Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-10T09:35:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:35:13Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-10T09:35:13Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114183350100",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38042032609:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114184025754",
                "completed_at": "2026-10-10T09:39:15Z",
                "conclusion": "success",
                "created_at": "2026-10-10T09:37:18Z",
                "head_branch": "main",
                "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38042032609/job/114184025754",
                "id": 114184025754,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaleX2mg",
                "run_attempt": 1,
                "run_id": 38042032609,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38042032609",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001711,
                "runner_name": "GitHub Actions 1000001711",
                "started_at": "2026-10-10T09:37:19Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-10T09:37:22Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-10T09:37:20Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:37:22Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-10T09:37:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:37:23Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-10T09:37:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:37:23Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-10T09:37:23Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:39:13Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-10T09:37:23Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:39:13Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-10T09:39:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:39:13Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-10T09:39:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:39:14Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-10T09:39:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T09:39:14Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-10T09:39:14Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114184025754",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38053069827:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114215961235",
                "completed_at": "2026-10-10T12:46:26Z",
                "conclusion": "success",
                "created_at": "2026-10-10T12:44:27Z",
                "head_branch": "main",
                "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38053069827/job/114215961235",
                "id": 114215961235,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAal81Ckw",
                "run_attempt": 1,
                "run_id": 38053069827,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38053069827",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001726,
                "runner_name": "GitHub Actions 1000001726",
                "started_at": "2026-10-10T12:44:29Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-10T12:44:31Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-10T12:44:30Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:44:33Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-10T12:44:31Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:44:33Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-10T12:44:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:44:34Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-10T12:44:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:46:24Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-10T12:44:34Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:46:24Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-10T12:46:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:46:24Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-10T12:46:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:46:24Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-10T12:46:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T12:46:24Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-10T12:46:24Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114215961235",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38071840297:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114270659994",
                "completed_at": "2026-10-10T17:29:51Z",
                "conclusion": "success",
                "created_at": "2026-10-10T17:27:55Z",
                "head_branch": "main",
                "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38071840297/job/114270659994",
                "id": 114270659994,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAamw_lmg",
                "run_attempt": 1,
                "run_id": 38071840297,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38071840297",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001740,
                "runner_name": "GitHub Actions 1000001740",
                "started_at": "2026-10-10T17:27:57Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-10T17:27:58Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-10T17:27:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:27:59Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-10T17:27:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:27:59Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-10T17:27:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:28:00Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-10T17:27:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:29:49Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-10T17:28:00Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:29:49Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-10T17:29:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:29:50Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-10T17:29:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:29:50Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-10T17:29:50Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T17:29:50Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-10T17:29:50Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114270659994",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38089291882:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114322224170",
                "completed_at": "2026-10-10T21:54:38Z",
                "conclusion": "success",
                "created_at": "2026-10-10T21:52:38Z",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38089291882/job/114322224170",
                "id": 114322224170,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaniK0Kg",
                "run_attempt": 1,
                "run_id": 38089291882,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38089291882",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001846,
                "runner_name": "GitHub Actions 1000001846",
                "started_at": "2026-10-10T21:52:40Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-10T21:52:42Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-10T21:52:41Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:52:43Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-10T21:52:42Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:52:43Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-10T21:52:43Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:52:44Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-10T21:52:43Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:54:34Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-10T21:52:44Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:54:35Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-10T21:54:34Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:54:35Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-10T21:54:35Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:54:35Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-10T21:54:35Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-10T21:54:35Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-10T21:54:35Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114322224170",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38116678211:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114402819958",
                "completed_at": "2026-10-11T06:03:05Z",
                "conclusion": "success",
                "created_at": "2026-10-11T06:01:06Z",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38116678211/job/114402819958",
                "id": 114402819958,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaovB_dg",
                "run_attempt": 1,
                "run_id": 38116678211,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38116678211",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001880,
                "runner_name": "GitHub Actions 1000001880",
                "started_at": "2026-10-11T06:01:08Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-11T06:01:10Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-11T06:01:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:01:11Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-11T06:01:10Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:01:12Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-11T06:01:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:01:12Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-11T06:01:12Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:03:02Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-11T06:01:12Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:03:02Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-11T06:03:02Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:03:03Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-11T06:03:02Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:03:03Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-11T06:03:03Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T06:03:03Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-11T06:03:03Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114402819958",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38141901079:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114477291223",
                "completed_at": "2026-10-11T13:09:05Z",
                "conclusion": "success",
                "created_at": "2026-10-11T13:07:05Z",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38141901079/job/114477291223",
                "id": 114477291223,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAap2DW1w",
                "run_attempt": 1,
                "run_id": 38141901079,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38141901079",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001903,
                "runner_name": "GitHub Actions 1000001903",
                "started_at": "2026-10-11T13:07:07Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-11T13:07:09Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-11T13:07:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:07:10Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-11T13:07:09Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:07:11Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-11T13:07:10Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:07:11Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-11T13:07:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:09:02Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-11T13:07:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:09:02Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-11T13:09:02Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:09:02Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-11T13:09:02Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:09:02Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-11T13:09:02Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T13:09:02Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-11T13:09:02Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114477291223",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38179225840:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114588029707",
                "completed_at": "2026-10-11T22:10:37Z",
                "conclusion": "success",
                "created_at": "2026-10-11T22:08:37Z",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38179225840/job/114588029707",
                "id": 114588029707,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAarfqTCw",
                "run_attempt": 1,
                "run_id": 38179225840,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38179225840",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001931,
                "runner_name": "GitHub Actions 1000001931",
                "started_at": "2026-10-11T22:08:39Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-11T22:08:41Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-11T22:08:40Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:08:43Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-11T22:08:41Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:08:43Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-11T22:08:43Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:08:43Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-11T22:08:43Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:10:33Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-11T22:08:43Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:10:34Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-11T22:10:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:10:34Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-11T22:10:34Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:10:34Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-11T22:10:34Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-11T22:10:34Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-11T22:10:34Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114588029707",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38211330971:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114685946091",
                "completed_at": "2026-10-12T06:13:26Z",
                "conclusion": "success",
                "created_at": "2026-10-12T06:11:28Z",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38211330971/job/114685946091",
                "id": 114685946091,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAas9Co6w",
                "run_attempt": 1,
                "run_id": 38211330971,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38211330971",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001964,
                "runner_name": "GitHub Actions 1000001964",
                "started_at": "2026-10-12T06:11:30Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-12T06:11:32Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-12T06:11:31Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:11:33Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-12T06:11:32Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:11:33Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-12T06:11:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:11:33Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-12T06:11:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:13:23Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-12T06:11:34Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:13:24Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-12T06:13:23Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:13:24Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-12T06:13:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:13:24Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-12T06:13:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T06:13:24Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-12T06:13:24Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114685946091",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38272590450:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/114890222007",
                "completed_at": "2026-10-12T15:47:45Z",
                "conclusion": "success",
                "created_at": "2026-10-12T15:45:45Z",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38272590450/job/114890222007",
                "id": 114890222007,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAav_2ptw",
                "run_attempt": 1,
                "run_id": 38272590450,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38272590450",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000001989,
                "runner_name": "GitHub Actions 1000001989",
                "started_at": "2026-10-12T15:45:47Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-12T15:45:49Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-12T15:45:48Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:45:50Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-12T15:45:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:45:50Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-12T15:45:50Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:45:51Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-12T15:45:50Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:47:41Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-12T15:45:51Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:47:41Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-12T15:47:41Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:47:41Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-12T15:47:41Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:47:42Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-12T15:47:41Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-12T15:47:42Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-12T15:47:42Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/114890222007",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38329267783:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115075031270",
                "completed_at": "2026-10-13T00:53:00Z",
                "conclusion": "success",
                "created_at": "2026-10-13T00:51:04Z",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38329267783/job/115075031270",
                "id": 115075031270,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAaywGg5g",
                "run_attempt": 1,
                "run_id": 38329267783,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38329267783",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002013,
                "runner_name": "GitHub Actions 1000002013",
                "started_at": "2026-10-13T00:51:05Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-13T00:51:07Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-13T00:51:06Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:51:07Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-13T00:51:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:51:08Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-13T00:51:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:51:08Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-13T00:51:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:52:58Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-13T00:51:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:52:58Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-13T00:52:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:52:58Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-13T00:52:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:52:58Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-13T00:52:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T00:52:58Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-13T00:52:58Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115075031270",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38413029127:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115350047046",
                "completed_at": "2026-10-13T14:26:10Z",
                "conclusion": "success",
                "created_at": "2026-10-13T14:24:10Z",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38413029127/job/115350047046",
                "id": 115350047046,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAa22YJRg",
                "run_attempt": 1,
                "run_id": 38413029127,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38413029127",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002045,
                "runner_name": "GitHub Actions 1000002045",
                "started_at": "2026-10-13T14:24:12Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-13T14:24:14Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-13T14:24:13Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:24:15Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-13T14:24:14Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:24:15Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-13T14:24:15Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:24:16Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-13T14:24:15Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:26:07Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-13T14:24:16Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:26:07Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-13T14:26:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:26:08Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-13T14:26:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:26:08Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-13T14:26:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T14:26:08Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-13T14:26:08Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115350047046",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38481019293:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115583338444",
                "completed_at": "2026-10-13T22:57:53Z",
                "conclusion": "success",
                "created_at": "2026-10-13T22:55:55Z",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38481019293/job/115583338444",
                "id": 115583338444,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAa6U3HzA",
                "run_attempt": 1,
                "run_id": 38481019293,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38481019293",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002077,
                "runner_name": "GitHub Actions 1000002077",
                "started_at": "2026-10-13T22:55:57Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-13T22:55:59Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-13T22:55:58Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:56:00Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-13T22:55:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:56:00Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-13T22:56:00Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:56:01Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-13T22:56:00Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:57:51Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-13T22:56:01Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:57:51Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-13T22:57:51Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:57:51Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-13T22:57:51Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:57:52Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-13T22:57:51Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-13T22:57:52Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-13T22:57:52Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115583338444",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38514675747:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115690682278",
                "completed_at": "2026-10-14T05:06:14Z",
                "conclusion": "success",
                "created_at": "2026-10-14T05:04:17Z",
                "head_branch": "main",
                "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38514675747/job/115690682278",
                "id": 115690682278,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAa77O3pg",
                "run_attempt": 1,
                "run_id": 38514675747,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38514675747",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002113,
                "runner_name": "GitHub Actions 1000002113",
                "started_at": "2026-10-14T05:04:19Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-14T05:04:20Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-14T05:04:19Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:04:21Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-14T05:04:20Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:04:21Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-14T05:04:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:04:22Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-14T05:04:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:06:11Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-14T05:04:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:06:12Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-14T05:06:11Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:06:12Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-14T05:06:12Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:06:12Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-14T05:06:12Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T05:06:12Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-14T05:06:12Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115690682278",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38536227298:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115774414274",
                "completed_at": "2026-10-14T09:14:52Z",
                "conclusion": "success",
                "created_at": "2026-10-14T09:12:53Z",
                "head_branch": "main",
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38536227298/job/115774414274",
                "id": 115774414274,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAa9LFdwg",
                "run_attempt": 1,
                "run_id": 38536227298,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38536227298",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002158,
                "runner_name": "GitHub Actions 1000002158",
                "started_at": "2026-10-14T09:12:55Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-14T09:12:57Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-14T09:12:56Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:12:59Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-14T09:12:57Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:12:59Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-14T09:12:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:13:00Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-14T09:12:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:14:49Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-14T09:13:00Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:14:50Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-14T09:14:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:14:50Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-14T09:14:50Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:14:50Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-14T09:14:50Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T09:14:50Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-14T09:14:50Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115774414274",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38561543606:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115843564300",
                "completed_at": "2026-10-14T12:10:02Z",
                "conclusion": "success",
                "created_at": "2026-10-14T12:08:03Z",
                "head_branch": "main",
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38561543606/job/115843564300",
                "id": 115843564300,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAa-NCDDA",
                "run_attempt": 1,
                "run_id": 38561543606,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38561543606",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002172,
                "runner_name": "GitHub Actions 1000002172",
                "started_at": "2026-10-14T12:08:05Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-14T12:08:07Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-14T12:08:06Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:08:08Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-14T12:08:07Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:08:08Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-14T12:08:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:08:09Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-14T12:08:08Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:09:59Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-14T12:08:09Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:09:59Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-14T12:09:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:09:59Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-14T12:09:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:09:59Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-14T12:09:59Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T12:09:59Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-14T12:09:59Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115843564300",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38606009159:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/115996955744",
                "completed_at": "2026-10-14T17:23:50Z",
                "conclusion": "success",
                "created_at": "2026-10-14T17:21:47Z",
                "head_branch": "main",
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38606009159/job/115996955744",
                "id": 115996955744,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbAfUUYA",
                "run_attempt": 1,
                "run_id": 38606009159,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38606009159",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002196,
                "runner_name": "GitHub Actions 1000002196",
                "started_at": "2026-10-14T17:21:52Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-14T17:21:54Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-14T17:21:53Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:21:55Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-14T17:21:54Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:21:55Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-14T17:21:55Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:21:56Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-14T17:21:55Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:23:46Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-14T17:21:56Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:23:46Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-14T17:23:46Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:23:47Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-14T17:23:46Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:23:47Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-14T17:23:47Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T17:23:47Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-14T17:23:47Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/115996955744",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38643121669:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116125673890",
                "completed_at": "2026-10-14T21:56:38Z",
                "conclusion": "success",
                "created_at": "2026-10-14T21:54:38Z",
                "head_branch": "main",
                "head_sha": "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38643121669/job/116125673890",
                "id": 116125673890,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbCaEpog",
                "run_attempt": 1,
                "run_id": 38643121669,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38643121669",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002235,
                "runner_name": "GitHub Actions 1000002235",
                "started_at": "2026-10-14T21:54:41Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-14T21:54:43Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-14T21:54:42Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:54:44Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-14T21:54:43Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:54:44Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-14T21:54:44Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:54:45Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-14T21:54:44Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:56:35Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-14T21:54:45Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:56:35Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-14T21:56:35Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:56:35Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-14T21:56:35Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:56:36Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-14T21:56:35Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-14T21:56:36Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-14T21:56:36Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116125673890",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38683833263:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "e1a68725fe923582b5640481e29c7166eb77397c",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116254080182",
                "completed_at": "2026-10-15T04:55:19Z",
                "conclusion": "success",
                "created_at": "2026-10-15T04:53:22Z",
                "head_branch": "main",
                "head_sha": "e1a68725fe923582b5640481e29c7166eb77397c",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38683833263/job/116254080182",
                "id": 116254080182,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbEUh8tg",
                "run_attempt": 1,
                "run_id": 38683833263,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38683833263",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002277,
                "runner_name": "GitHub Actions 1000002277",
                "started_at": "2026-10-15T04:53:24Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-15T04:53:25Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-15T04:53:24Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:53:26Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-15T04:53:25Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:53:26Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-15T04:53:26Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:53:27Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-15T04:53:26Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:55:17Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-15T04:53:27Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:55:17Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-15T04:55:17Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:55:17Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-15T04:55:17Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:55:18Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-15T04:55:17Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T04:55:18Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-15T04:55:18Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116254080182",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38684236013:1": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116283744316",
                "completed_at": "2026-10-15T06:36:33Z",
                "conclusion": "success",
                "created_at": "2026-10-15T06:34:35Z",
                "head_branch": "main",
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38684236013/job/116283744316",
                "id": 116283744316,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbEw0gPA",
                "run_attempt": 1,
                "run_id": 38684236013,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38684236013",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002307,
                "runner_name": "GitHub Actions 1000002307",
                "started_at": "2026-10-15T06:34:37Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-15T06:34:39Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-15T06:34:38Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:34:40Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-15T06:34:39Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:34:40Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-15T06:34:40Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:34:41Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-15T06:34:40Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:36:30Z",
                        "conclusion": "success",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-15T06:34:41Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:36:31Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-15T06:36:30Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:36:31Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-15T06:36:31Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:36:31Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-15T06:36:31Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T06:36:31Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-15T06:36:31Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116283744316",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38720084103:1": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116421625488",
                "completed_at": "2026-10-15T12:29:50Z",
                "conclusion": "failure",
                "created_at": "2026-10-15T12:28:31Z",
                "head_branch": "main",
                "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38720084103/job/116421625488",
                "id": 116421625488,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbG0UGkA",
                "run_attempt": 1,
                "run_id": 38720084103,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38720084103",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002421,
                "runner_name": "GitHub Actions 1000002421",
                "started_at": "2026-10-15T12:28:34Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-15T12:28:37Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-15T12:28:35Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:28:38Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-15T12:28:37Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:28:38Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-15T12:28:38Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:28:39Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-15T12:28:38Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:29:47Z",
                        "conclusion": "failure",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-15T12:28:39Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:29:48Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-15T12:29:47Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:29:48Z",
                        "conclusion": "skipped",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-15T12:29:48Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:29:48Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-15T12:29:48Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T12:29:48Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-15T12:29:48Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116421625488",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38721259685:1": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116376106374",
                "completed_at": "2026-10-15T10:38:23Z",
                "conclusion": "failure",
                "created_at": "2026-10-15T10:34:44Z",
                "head_branch": "main",
                "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38721259685/job/116376106374",
                "id": 116376106374,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbGI51hg",
                "run_attempt": 1,
                "run_id": 38721259685,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38721259685",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002372,
                "runner_name": "GitHub Actions 1000002372",
                "started_at": "2026-10-15T10:34:46Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-15T10:34:47Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-15T10:34:46Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:34:48Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-15T10:34:47Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:34:48Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-15T10:34:48Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:34:49Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-15T10:34:48Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:38:21Z",
                        "conclusion": "failure",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-15T10:34:49Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:38:21Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-15T10:38:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:38:21Z",
                        "conclusion": "skipped",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-15T10:38:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:38:22Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-15T10:38:21Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T10:38:22Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-15T10:38:22Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116376106374",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
    "38763419618:1": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs": [
            {
                "check_run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/check-runs/116520331562",
                "completed_at": "2026-10-15T15:53:24Z",
                "conclusion": "failure",
                "created_at": "2026-10-15T15:47:28Z",
                "head_branch": "main",
                "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                "html_url": "https://github.com/YouthOpps/data-pipeline/actions/runs/38763419618/job/116520331562",
                "id": 116520331562,
                "labels": ["ubuntu-latest"],
                "name": "collect",
                "node_id": "CR_kwDORVslOM8AAAAbIScpKg",
                "run_attempt": 1,
                "run_id": 38763419618,
                "run_url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/runs/38763419618",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
                "runner_id": 1000002525,
                "runner_name": "GitHub Actions 1000002525",
                "started_at": "2026-10-15T15:47:30Z",
                "status": "completed",
                "steps": [
                    {
                        "completed_at": "2026-10-15T15:47:31Z",
                        "conclusion": "success",
                        "name": "Set up job",
                        "number": 1,
                        "started_at": "2026-10-15T15:47:30Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:47:32Z",
                        "conclusion": "success",
                        "name": "Run actions/checkout@v4",
                        "number": 2,
                        "started_at": "2026-10-15T15:47:31Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:47:32Z",
                        "conclusion": "success",
                        "name": "Run " "actions/setup-python@v5",
                        "number": 3,
                        "started_at": "2026-10-15T15:47:32Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:47:33Z",
                        "conclusion": "success",
                        "name": "Create data-source " "publication token",
                        "number": 4,
                        "started_at": "2026-10-15T15:47:32Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:53:22Z",
                        "conclusion": "failure",
                        "name": "Collect and publish this " "source",
                        "number": 5,
                        "started_at": "2026-10-15T15:47:33Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:53:22Z",
                        "conclusion": "success",
                        "name": "Post Create data-source " "publication token",
                        "number": 8,
                        "started_at": "2026-10-15T15:53:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:53:22Z",
                        "conclusion": "skipped",
                        "name": "Post Run " "actions/setup-python@v5",
                        "number": 9,
                        "started_at": "2026-10-15T15:53:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:53:22Z",
                        "conclusion": "success",
                        "name": "Post Run " "actions/checkout@v4",
                        "number": 10,
                        "started_at": "2026-10-15T15:53:22Z",
                        "status": "completed",
                    },
                    {
                        "completed_at": "2026-10-15T15:53:22Z",
                        "conclusion": "success",
                        "name": "Complete job",
                        "number": 11,
                        "started_at": "2026-10-15T15:53:22Z",
                        "status": "completed",
                    },
                ],
                "url": "https://api.github.com/repos/YouthOpps/data-pipeline/actions/jobs/116520331562",
                "workflow_name": "fetch-opportunitydesk",
            }
        ],
    },
}


# Receipts are target/body observations, never an authorization cache.
_CURRENT_ASSESSMENT_URL = None


def receipt_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def policy_begin(url, groups):
    """Reserve a cached or fresh policy calculation before using its result."""
    url = public_url(url)
    parsed = urllib.parse.urlsplit(url)
    origin = parsed.scheme + "://" + parsed.netloc
    provenance = _REP_PROVENANCE.get(origin)
    body = groups[0]["body_sha256"] if groups else None
    if provenance is not None:
        body = provenance["body_sha256"]
    if body is None:
        raise AdapterError("Actual REP body provenance required", "access")
    if any(group["body_sha256"] != body for group in groups):
        raise AdapterError("Cached REP body provenance changed", "access")
    pending = {
        "owner": _ACTIVE_FLIGHT_ID,
        "target": url,
        "body_sha256": body,
        "origin": origin,
        "nonce": os.urandom(16).hex(),
    }
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        if state["policy_pending"] is not None:
            raise AdapterError("Unsettled target policy calculation", "access")
        state["policy_pending"] = pending
        save_budget(state)
    return pending


def policy_finish(pending, decision):
    """Commit only the exact same owner, target and REP-body calculation."""
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        if (
            pending["owner"] != _ACTIVE_FLIGHT_ID
            or state["policy_pending"] != pending
        ):
            raise AdapterError("Policy assessment identity changed", "access")
        receipt = {**pending, "decision": decision}
        state["policy_receipt"] = receipt
        state["policy_pending"] = None
        if decision == "deny":
            state["denied_scopes"] = sorted(
                set(state["denied_scopes"] + [pending["target"]])
            )
            state["blocked"] = True
        else:
            state["policy_samples"][pending["origin"]] = receipt
        save_budget(state)


def response_receipt(url, status, body=None, *, assessed):
    """Bind an assessed physical response to its durable request and owner."""
    with locked_budget():
        state = load_budget()
        publisher_healthy(state)
        state["response_receipt"] = {
            "owner": _ACTIVE_FLIGHT_ID,
            "request": state["request_id"],
            "target": url,
            "status": status,
            "body_sha256": (
                hashlib.sha256(body).hexdigest() if body is not None else None
            ),
            "body_read": body is not None,
            "assessment": assessed,
        }
        save_budget(state)


def hex_identity(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value)


def validate_policy_receipt(value, *, pending=False):
    if value is None:
        return
    expected = {"owner", "target", "body_sha256", "origin", "nonce"}
    if not pending:
        expected.add("decision")
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or not hex_identity(value["owner"])
        or not hex_identity(value["nonce"])
        or not isinstance(value["body_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", value["body_sha256"])
        or public_url(value["target"]) != value["target"]
        or value["origin"]
        != (
            urllib.parse.urlsplit(value["target"]).scheme
            + "://"
            + urllib.parse.urlsplit(value["target"]).netloc
        )
        or not pending
        and value["decision"] not in ("allow", "deny")
    ):
        raise AdapterError("Invalid target/body policy receipt", "access")


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
