"""Current University of Tartu exchange calls and ENLIGHT courses.

Public ut.ee content and robots.txt are the reviewed access route; no login is
needed for collection. Applying uses UT credentials in SoleMOVE, which this
adapter never accesses. The open-calls table gates current inventory; upcoming
competitions and the evergreen scholarship directory are not opportunities.
Partner call years come from explicit dated headings. ENLIGHT's abbreviated
November deadline is anchored to the article's publication year, never today's
year. A date-only deadline uses the index's UTC closing-calendar-day convention.
Individual ENLIGHT course links are preserved without crawling external sites.
"""

import argparse
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import email.utils
import errno
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import signal
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

SOURCE_ID = "ee-university-of-tartu"


SOURCE_URL = "https://ut.ee/en/open-calls"


WEBSITE_URL = "https://ut.ee/en/scholarships-and-other-stipends"


LANGUAGE = "en"


PUBLISHER_COUNTRY = "EE"


PUBLISHER_TYPE = "university"


ATTRIBUTION = "University of Tartu"


_PARTNERS_URL = "https://ut.ee/en/competitions-ut-partner-universities"


_ENLIGHT_URL = "https://ut.ee/en/news/enlight-short-term-blended-mobility-courses"


_COUNTRIES = {"Georgia": "GE", "Japan": "JP", "South Korea": "KR", "USA": "US"}


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
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
    """Read only the publisher's content block, excluding navigation/footer."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)
        blocks = [
            n for n in self.root.walk() if n.attrs.get("id") == "block-utheme-content"
        ]
        if len(blocks) != 1:
            raise AdapterError("Publisher content block changed", "parse")
        self.content = blocks[0]

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


def publisher_page(url, delay):
    """Check every fetched content path against the current robots policy."""
    delay = max(delay, check_robots(url))
    return PublisherHTML(
        fetch_inventory_page(fetch_source, url, min_interval=delay)
    ).content


def date_deadline(value, year=None):
    """Resolve a publisher date, with an explicit contextual year when needed."""
    pattern = r"(\d{1,2}) ([A-Za-z]+)(?: (\d{4}))?"
    match = re.fullmatch(pattern, value.strip())
    if not match or not (match[3] or year):
        raise AdapterError("Deadline lacks a supported calendar year", "parse")
    date = datetime.strptime(f"{match[1]} {match[2]} {match[3] or year}", "%d %B %Y")
    return date.date().isoformat()


def current_rows(content):
    """Select the current-call table, rejecting unhandled inventory changes."""
    section = ""
    tables = []
    for node in content.walk():
        if node.tag == "h3":
            section = node.text()
        elif node.tag == "table" and section == "Competitions currently open":
            tables.append(node)
    if len(tables) != 1:
        raise AdapterError("Current competitions table changed", "parse")
    rows = []
    for row in tables[0].walk():
        if row.tag != "tr":
            continue
        cells = [n for n in row.children if isinstance(n, Node) and n.tag == "td"]
        if not cells:
            continue
        if len(cells) != 4:
            raise AdapterError("Current competition row changed", "parse")
        links = [
            urllib.parse.urljoin(SOURCE_URL, n.attrs.get("href", ""))
            for n in cells[0].walk()
            if n.tag == "a"
        ]
        if len(links) != 1 or links[0] not in (_PARTNERS_URL, _ENLIGHT_URL):
            raise AdapterError("New current call requires source review", "parse")
        rows.append((links[0], cells))
    if not rows or len({url for url, _ in rows}) != len(rows):
        raise AdapterError("Empty or duplicate current call rows", "parse")
    return rows


def finish_record(record, identity, deadline, summary):
    record["id"] = hashlib.sha256(f"{SOURCE_ID}|{identity}".encode()).hexdigest()[:24]
    record["deadline"] = deadline
    record["summary"] = (
        summary if len(summary) <= 600 else summary[:596].rsplit(" ", 1)[0] + "..."
    )
    if deadline is None:
        record["status"] = "unknown"
    else:
        checked = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            expired = deadline < checked.astimezone(timezone.utc).date().isoformat()
        else:
            cutoff = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            expired = cutoff < checked
        record["status"] = "expired" if expired else "open"
    return record


def partner_records(content, cells):
    """Expand the current exchange call into explicitly dated universities."""
    nodes = list(content.walk())
    by_id = {n.attrs["id"]: n for n in nodes if n.attrs.get("id")}
    country = None
    records = []
    for node in nodes:
        if node.tag in ("h2", "h3") and node.text() in _COUNTRIES:
            country = _COUNTRIES[node.text()]
        if node.tag != "button":
            continue
        titles = [
            n.text()
            for n in node.walk()
            if "collapsible__title" in n.attrs.get("class", "").split()
        ]
        if len(titles) != 1:
            continue
        match = re.fullmatch(
            r"(.+) \(application deadline (\d{1,2} [A-Za-z]+ \d{4})\)",
            titles[0],
        )
        if not match:
            continue
        # The current parent listing, not an old accordion, decides inclusion.
        if not match[2].startswith(cells[3].text() + " "):
            continue
        anchor = node.attrs.get("aria-controls")
        body = by_id.get(anchor)
        if not body or not country:
            raise AdapterError("Dated exchange call has no detail or country", "parse")
        title, deadline = match[1], date_deadline(match[2])
        url = _PARTNERS_URL + "#" + anchor
        record = make_record(
            "Exchange studies: " + title,
            url,
            ["training"],
            tags=["exchange", "tuition-waiver"],
            host_countries=[country],
            method="tartu-current-call-and-dated-partner",
            evidence=[SOURCE_URL, url],
        )
        summary = "University of Tartu students only. " + body.text()
        records.append(
            finish_record(
                record, f"exchange|{title}|{match[2][-4:]}", deadline, summary
            )
        )
    # Every named destination in the parent call must be represented.
    names = re.split(
        r",\s*| and ", next(n.text() for n in cells[0].walk() if n.tag == "a")
    )
    for name in names:
        key = name.lower().replace("-", " ").removesuffix(" universities").strip()
        tokens = key.split()
        if not any(
            all(
                token in r["title"].lower().replace("-", " ").split()
                for token in tokens
            )
            for r in records
        ):
            raise AdapterError(
                "Current exchange destination is missing: " + name, "parse"
            )
    if len(records) != len(names):
        raise AdapterError(
            "Exchange detail inventory differs from current call", "parse"
        )
    return records


def enlight_records(content, cells):
    """Expand the publisher's current course list, preserving course UUID URLs."""
    dates = [
        n.text()
        for n in content.walk()
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", n.text())
    ]
    dates = set(dates)
    if len(dates) != 1:
        raise AdapterError("ENLIGHT article publication date changed", "parse")
    published_date = datetime.strptime(dates.pop(), "%d.%m.%Y")
    deadline = date_deadline(cells[3].text(), published_date.year)
    if deadline[:10] < published_date.date().isoformat():
        raise AdapterError("ENLIGHT deadline precedes publication", "parse")
    if "The application deadline is " + cells[3].text() not in content.text():
        raise AdapterError("ENLIGHT parent and article deadlines disagree", "parse")
    host = ""
    records = []
    links_seen = set()
    for node in content.walk():
        if node.tag == "h2":
            host = node.text()
        if node.tag != "h3":
            continue
        links = [n for n in node.walk() if n.tag == "a"]
        if not links:
            continue
        if len(links) != 1:
            raise AdapterError("Course heading has ambiguous links", "parse")
        url = links[0].attrs.get("href", "")
        if (
            not re.fullmatch(
                r"https://courses\.enlight-eu\.org/joint-courses/[0-9a-f-]{36}",
                url,
            )
            or not host
        ):
            raise AdapterError("Unexpected ENLIGHT course URL or host", "parse")
        if url in links_seen:
            raise AdapterError("Duplicate ENLIGHT course", "parse")
        links_seen.add(url)
        record = make_record(
            links[0].text(),
            url,
            ["training"],
            tags=["ENLIGHT", "Erasmus+", "blended-mobility"],
            method="tartu-current-enlight-course-list",
            evidence=[SOURCE_URL, _ENLIGHT_URL, url],
        )
        nodes = list(content.walk())
        position = nodes.index(node)
        details = []
        for sibling in nodes[position + 1 :]:
            if sibling.tag in ("h2", "h3"):
                break
            if sibling.tag == "p":
                detail = sibling.text()
                if detail.startswith(("Level:", "Entry requirements:")):
                    details.append(detail.split("Departmental Coordinator:")[0])
        if not details:
            raise AdapterError("Course eligibility details missing", "parse")
        summary = (
            f"University of Tartu students only. Host: {host}. "
            + " ".join(details)
            + " Erasmus+ mobility grant; apply through UT SoleMOVE."
        )
        records.append(finish_record(record, url, deadline, summary))
    all_course_links = [
        n
        for n in content.walk()
        if n.tag == "a"
        and "courses.enlight-eu.org/joint-courses/" in n.attrs.get("href", "")
    ]
    if not records or len(all_course_links) != len(records):
        raise AdapterError("ENLIGHT course coverage incomplete", "parse")
    return records


def collect_body():
    """Collect only current calls using serialized, paced public requests."""
    content = publisher_page(SOURCE_URL, 6)
    records = []
    for url, cells in current_rows(content):
        details = publisher_page(url, 6)
        if url == _PARTNERS_URL:
            records.extend(partner_records(details, cells))
        else:
            records.extend(enlight_records(details, cells))
    validate_records(records)
    return records


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


class RobotsDisallow(AdapterError):
    """An explicitly applicable publisher robots prohibition."""


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
    """Filter validated deadlines after retrieval, before atomic publication.

    Date-only values use the shared index's UTC closing-calendar-day rule;
    they do not assert a publisher clock or timezone. Aware timestamps retain
    their exact instant. No age, status or missing response implies expiry.
    """
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Cleanup requires an aware check timestamp", "validate")
    latest_day = checked.astimezone(timezone.utc).date()
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
                    cutoff = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
                    expired = cutoff.tzinfo is not None and cutoff < checked
            except ValueError:
                # Unsupported or ambiguous deadlines never authorize removal.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, *, allow_empty=False):
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError("Empty or invalid opportunity collection", "validate")
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
            if not isinstance(record.get(field), str) or not record[field].strip():
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
                        field == "deadline"
                        and isinstance(value, str)
                        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
                    ):
                        datetime.strptime(value, "%Y-%m-%d")
                        continue
                    if (
                        datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo
                        is None
                    ):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError("Invalid record timestamp: " + field, "validate")
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
            or any(not isinstance(value, str) for value in classification["evidence"])
        ):
            raise AdapterError("Invalid classification evidence", "validate")


def github(method, path, payload=None, missing_ok=False):
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError("DATA_SOURCE_TOKEN is required for publication", "publish")
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
            payload=(json.dumps(payload).encode() if payload is not None else None),
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


def read_snapshot_body():
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
            raise AdapterError("Unsupported existing source file encoding", "publish")
        files[name] = (
            None if file is None else base64.b64decode(file["content"]).decode("utf-8")
        )
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish"
        )
    return {"head": head, "tree": tree, "files": files}


def publish_files_body(snapshot, files):
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
                    "Source changed concurrently; newer remote data was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; durable remote outcome could not be updated",
                    "publish",
                )


def metadata(attempt, previous, records, error=None):
    result = {
        "display_name": "University of Tartu",
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (previous.get("last_success_at") if error else attempt),
        "last_checked_at": (previous.get("last_checked_at") if error else attempt),
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


FAMILY = "youthopps-ee-university-of-tartu-publisher-v1"


FAMILY_SOURCES = {SOURCE_ID}


_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"


_WORKFLOW_ID = 378390326


_ARTIFACT_NAME = "tartu-pacing-state"


COLLECTION_TIMEOUT = 1350


RUN_TIMEOUT = 1740


EXPORT_TIMEOUT = 30


_STATE_PATH = _STATE_LOCK = None


_JOURNAL_NEW = False


_COLLECTION_LOCK_FD = None
_COLLECTION_LOCK_ID = None


_RESTORED = _STATE_TRUSTED = False


_PUBLISHER_GATE_FAILED = False


_LAST_KNOWN_STATE = None


_JOURNAL_LOCKS = {}
_JOURNAL_VERSIONS = {}
_JOURNAL_DIRTY = set()


_VERIFIED_REMOTE_CREATE = None


_INITIAL_DIRECTORY_PROVEN = False


_STATE_PARENT_FD = None


_STATE_PARENT_ID = None


_STATE_PARENT_PATH = None


_RUN_END = _PHASE_END = _PHASE_NAME = None


_SCANNED_HISTORY = {}


_SCANNED_JOB_DIGESTS = {}


_ATTEMPT_HEADS = {}


_INVENTORY_ACTIVE = False


_ROBOTS_CONTEXT = None


_INFRASTRUCTURE_UNTIL = {}


_LOCAL_FIRST_ACCESS_CHECKED = False


_RESTORED_LATEST = None


_RESTORED_HISTORY = None


_LOCAL_RESEARCH_MIGRATION = {
    "source": "ee-university-of-tartu",
    "sha256": ("b0b24dc46e82ff237eb5824b3ad902e145e4f604dd75319567c49da119f58eaf"),
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/279"
        "#issuecomment-6203714896"
    ),
}


_REVIEWED_NATIVE_MIGRATION = {
    "schema": 1,
    "source": "ee-university-of-tartu",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/279#issuecomment-6204053214",
    "state": {
        "schema": 3,
        "family": "youthopps-ee-university-of-tartu-publisher-v1",
        "legacy_hold_history": "unlogged_fields_unknown",
        "research_provenance": {
            "kind": "explicit_unknown_legacy_transition",
            "source": "ee-university-of-tartu",
            "legacy_wrapper_count": 11,
            "executed_completion_upper_bounds": 10,
            "exact_cancelled_no_job_count": 1,
            "complete_history_manifest_sha256": "dd42a0939f62bde63c1e343dc2918afd0e532625df473d995232193f496ca945",
            "all_actual_log_hash_manifest_sha256": "5c73fbcf8b9aa11f11a6d53d237b352c9db3d366efd0da027cb86c69873cdbb1",
            "old_local_journal": "AUTHENTIC_ABSENT_NOT_ZERO",
            "legacy_429_history": "unknown",
            "legacy_hold_history": "unlogged_fields_unknown",
            "in_flight_legacy_semantics": "Unknown "
            "old "
            "semantics; "
            "False "
            "proposed "
            "ONLY for "
            "explicitly "
            "reviewed "
            "one-off "
            "transition, "
            "never "
            "old-schema "
            "default",
            "scope": "Complete ten actual success "
            "logs provide no recovered "
            "content refusal; unlogged "
            "headers/strikes remain "
            "unknown. Old job completions "
            "are conservative upper "
            "bounds, not actual request "
            "starts.",
        },
        "starts": [
            1792104166.4833934,
            1792104167.3899724,
            1792104179.590142,
            1792104179.5919755,
            1792104180.4125512,
            1792104180.6398733,
            1792104197.3905509,
            1792104197.3911424,
            1792104197.6323326,
            1792104199.693189,
        ],
        "interval": 12.2,
        "until": 1792104259.8407485,
        "page_gap": 30,
        "completed": 1792104199.6945276,
        "inventory_completed": 1792104199.6945276,
        "blocked": False,
        "in_flight": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": 1792104199.6948047,
    },
    "state_sha256": "2c5dd8f2b5069f1ffceaf9bc8da34d5c2b65c87894e23ecb90925c766525d739",
}


_LEGACY_HEADS = {
    37809621343: "ce76427dcc2bfc744961a6058bac6d0100bea31e",
    37810563514: "10e7dc06e65127013bf2a5ba6c835cac6a749aa8",
    37818503497: "9ab03a510dba38548ed53dd378c797be03547bdf",
    37819106792: "9ab03a510dba38548ed53dd378c797be03547bdf",
    38071840355: "9b155b179283c8fa08cf56c1a83a54bd19d51814",
    38536206685: "3f6ab17f4698de58f7365682a22384cd08788f69",
    38684235995: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38720084006: "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
    38721259407: "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
    38763419353: "f77eed642c4544bbeed009440c406064a4f5c6ad",
    38767785897: "f3b6e313a7680479d083c98d5ed32e94dc44fdc5",
}


_LEGACY_JOB_DIGESTS = {
    37809621343: "bd338e8881b6eb0824776ca99e028166bf114c44e6ed0f98086e3564b30d4677",
    37810563514: "336b8e1e1a637370409999977b24e8353269c8b2e657900bb8fa6a9fe69bef8c",
    37818503497: "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
    37819106792: "00a6ccb0e63588957037895247792d52a867d1b1489c856f6bb6919b98c161a3",
    38071840355: "97eedc7aeab54556057e90b4a681c181a1ac982ad92c38d12ff17fb357e34d9e",
    38536206685: "6c5c5ddd964b825960c44250aa2d0bc02782d10fe43c1035e8c7c251d2f785fa",
    38684235995: "b945eec4174c4a7b1ee1309a439087f67aba30e02ccffd81b01381db1a2e097d",
    38720084006: "9a92710d2629d74e5fa17233fb5132272f4e0bc97bd39f766aba1c35e5b921dc",
    38721259407: "12e047da549a6dea392e240bca436f95e6cc37f0a2d45ac90fb61a2157ff1e4f",
    38763419353: "3efbe10604c86607970814f2e54b9e9560cd98565aa6bc1645cd65752ecb1022",
    38767785897: "d6d960ac3e66c71ed618d0f2b8d9abcbcf3b7ee5b47f9c95499e4e42ad8c1a73",
}


_LEGACY_NO_JOB = {
    37818503497: {
        "conclusion": "cancelled",
        "created_at": "2026-10-08T17:41:23Z",
        "head_sha": "9ab03a510dba38548ed53dd378c797be03547bdf",
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
        "run_attempt": 1,
    }
}


class PhaseExpired(Exception):
    """A nonrenewable deadline with reserved failure-reporting headroom."""

    stage = "runtime"
    status = None

    def __init__(self):
        super().__init__("Nonrenewable runtime phase deadline expired")


class PrimaryText(HTMLParser):
    """Inspect primary visible text and genuine head titles."""

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
        style = re.sub(r"/\*.*?\*/", "", values.get("style") or "", flags=re.S).lower()
        declarations, priorities = {}, {}
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                name = name.strip()
                important = bool(re.search(r"!\s*important\s*$", value))
                if priorities.get(name) and not important:
                    continue
                declarations[name] = re.sub(r"\s*!\s*important\s*$", "", value).strip()
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

    class RefusalText(PrimaryText):
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
            r"\bif you see access denied\s*,?\s*" r"(?:please\s+)?contact support\b",
            " ",
            text,
        )

    units = [parser.parts, parser.head_title_parts] + parser.headlines
    text = " ".join(without_instruction(parts) for parts in units)
    phrases = (
        "verify you are human",
        "verify that you are human",
        "human verification",
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
            (
                r"just a moment|attention required|captcha"
                if challenge_only
                else r"just a moment|attention required|access denied|captcha"
            ),
            explicit_title,
        )
    )


def numeric(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def legacy_empty_budget(now):
    return {
        "schema": 3,
        "family": FAMILY,
        "legacy_hold_history": "unlogged_fields_unknown",
        "research_provenance": {},
        "starts": [],
        "interval": 12.2,
        "until": now + 60,
        "page_gap": 30,
        "completed": 0,
        "inventory_completed": 0,
        "blocked": False,
        "in_flight": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": now,
    }


def validate_legacy_budget(state):
    """Validate retained constraints without resetting unknown history."""
    if not isinstance(state, dict):
        raise AdapterError("Invalid publisher state", "access")
    expected = set(legacy_empty_budget(0))
    if (
        set(state) != expected
        or type(state["schema"]) is not int
        or state["schema"] != 3
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
        or state["legacy_hold_history"] != "unlogged_fields_unknown"
        or not isinstance(state["research_provenance"], dict)
        or type(state["blocked"]) is not bool
        or type(state["in_flight"]) is not bool
        or type(state["observed_429_since_transition"]) is not int
        or not 0 <= state["observed_429_since_transition"] <= 100
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 1000
        or any(not numeric(t) or t > state["observed_at"] for t in state["starts"])
        or state["starts"] != sorted(set(state["starts"]))
        or state["completed"] > state["observed_at"]
        or state["inventory_completed"] > state["observed_at"]
        or state["observed_at"] > time.time() + 1
    ):
        raise AdapterError("Invalid retained publisher constraints", "access")
    return state


def state_parent(directory=None):
    """Pin the verified parent and refuse replacement of its current path."""
    global _STATE_PARENT_FD, _STATE_PARENT_ID, _STATE_PARENT_PATH
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    directory = directory or (
        os.path.dirname(_STATE_PATH) if _STATE_PATH else _STATE_PARENT_PATH
    )
    if not directory or not os.path.isabs(directory):
        raise AdapterError("Publisher state parent unavailable", "access")
    verify_temporary_root(directory)
    fresh = _STATE_PARENT_FD is None
    descriptor = (
        os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if fresh
        else _STATE_PARENT_FD
    )
    try:
        info = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or not stat.S_ISDIR(current.st_mode)
            or current.st_uid != os.getuid()
            or current.st_mode & 0o077
            or identity != (current.st_dev, current.st_ino)
            or not fresh
            and (directory != _STATE_PARENT_PATH or identity != _STATE_PARENT_ID)
        ):
            raise AdapterError("Publisher state parent changed", "access")
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if fresh:
            os.close(descriptor)
        raise
    if fresh:
        _STATE_PARENT_FD = descriptor
        _STATE_PARENT_ID = identity
        _STATE_PARENT_PATH = directory
    return descriptor


def validate_state_descriptor(descriptor, parent, name):
    """Bind a safe current journal inode within the retained parent."""
    info = os.fstat(descriptor)
    validate_owned_output(info)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
        raise AdapterError("Publisher journal path changed", "access")
    state_parent(_STATE_PARENT_PATH)
    return info


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _INITIAL_DIRECTORY_PROVEN
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    if _STATE_PARENT_FD is not None:
        state_parent(directory)
    else:
        try:
            os.mkdir(directory, mode=0o700)
        except FileExistsError:
            pass
        else:
            _INITIAL_DIRECTORY_PROVEN = True
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    state_parent(directory)
    _STATE_PATH = _STATE_LOCK = os.path.join(
        directory, hashlib.sha256(b"ut.ee").hexdigest()
    )


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


def retained_journal(check_generation=True):
    """Use the same live RW descriptor as the exclusive journal lock."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    try:
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError("Publisher persistence gate failed", "access")
        if len(_JOURNAL_LOCKS) != 1:
            raise AdapterError("One live publisher journal lock required", "access")
        descriptor, identity = next(iter(_JOURNAL_LOCKS.items()))
        verify_exclusive_flock(descriptor)
        parent = state_parent()
        info = validate_state_descriptor(
            descriptor, parent, os.path.basename(_STATE_PATH)
        )
        if (
            (info.st_dev, info.st_ino) != identity
            or stat.S_IMODE(info.st_mode) != 0o600
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
            or check_generation
            and journal_version(info) != _JOURNAL_VERSIONS[descriptor]
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        return descriptor, info
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        raise


def locked_budget():
    global _JOURNAL_NEW, _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    fd = None
    identity = None
    try:
        budget_file()
        parent = state_parent()
        name = os.path.basename(_STATE_PATH)
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if (
            _INITIAL_DIRECTORY_PROVEN
            and not _RESTORED
            and (_VERIFIED_REMOTE_CREATE is not None)
        ):
            try:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                before = os.fstat(fd)
                identity = (before.st_dev, before.st_ino)
                _JOURNAL_NEW = True
                os.fsync(parent)
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=parent)
                _JOURNAL_NEW = False
        else:
            fd = os.open(name, flags, dir_fd=parent)
        before = validate_state_descriptor(fd, parent, name)
        identity = (before.st_dev, before.st_ino)
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = validate_state_descriptor(fd, parent, name)
        if (
            (info.st_dev, info.st_ino) != identity
            or stat.S_IMODE(info.st_mode) != 0o600
            or _JOURNAL_LOCKS
        ):
            raise AdapterError("Publisher journal lock changed", "access")
        return BudgetLock(fd, info)
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if fd is not None:
            with contextlib.suppress(OSError):
                info = os.fstat(fd)
                if identity is None or (info.st_dev, info.st_ino) == identity:
                    os.close(fd)
        raise


def load_budget():
    """Read the retained lock FD and reject a changed generation."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    try:
        if not _JOURNAL_LOCKS:
            if _STATE_PATH is None:
                raise AdapterError("Publisher state is not initialized", "access")
            with locked_budget():
                return load_budget()
        descriptor, _ = retained_journal()
        raw = os.pread(descriptor, 16385, 0)
        retained_journal()
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher journal", "access")
        if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
            return validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
        try:
            return validate_budget(json.loads(raw))
        except (ValueError, TypeError):
            raise AdapterError("Corrupt publisher journal", "access") from None

    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        raise


def write_budget(state):
    """Latch every validation, serialization or persistence failure."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED, _LAST_KNOWN_STATE
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        if _LAST_KNOWN_STATE is None:
            _LAST_KNOWN_STATE = copy.deepcopy(state)
        else:
            _LAST_KNOWN_STATE = merge_budget(_LAST_KNOWN_STATE, state)
        return write_budget_body(_LAST_KNOWN_STATE)
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        with contextlib.suppress(BaseException):
            invalidate_failed_journal()
        raise


class BudgetLock:
    """Own one locked raw descriptor without closing a recycled foreign FD."""

    def __init__(self, descriptor, info):
        self.descriptor = descriptor
        self.identity = (info.st_dev, info.st_ino)
        _JOURNAL_LOCKS[descriptor] = self.identity
        _JOURNAL_VERSIONS[descriptor] = journal_version(info)

    def fileno(self):
        if self.descriptor is None:
            raise AdapterError("Publisher lock is closed", "access")
        info = os.fstat(self.descriptor)
        if (info.st_dev, info.st_ino) != self.identity:
            raise AdapterError("Publisher lock FD was reused", "access")
        return self.descriptor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
        descriptor, self.descriptor = self.descriptor, None
        if descriptor is not None:
            _JOURNAL_LOCKS.pop(descriptor, None)
            _JOURNAL_VERSIONS.pop(descriptor, None)
            _JOURNAL_DIRTY.discard(descriptor)
            try:
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != self.identity:
                    raise AdapterError("Publisher lock FD was reused", "access")
                os.close(descriptor)
            except BaseException:
                _STATE_TRUSTED = False
                _PUBLISHER_GATE_FAILED = True
                raise


def invalidate_failed_journal():
    """Invalidate only original owned FDs, never a replacement pathname."""
    for descriptor, identity in list(_JOURNAL_LOCKS.items()):
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink not in (0, 1)
            or (info.st_dev, info.st_ino) != identity
            or descriptor not in _JOURNAL_DIRTY
            and info.st_nlink == 1
            and journal_version(info) != _JOURNAL_VERSIONS[descriptor]
        ):
            raise AdapterError("Original publisher journal changed", "access")
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)


def write_budget_body(state):
    """Commit through the held inode; interrupted rewrites fail closed."""
    global _INITIAL_DIRECTORY_PROVEN, _JOURNAL_NEW, _JOURNAL_COMMITS
    state = validate_budget(state)
    payload = json.dumps(state, separators=(",", ":")).encode()
    if len(payload) > 16384:
        raise AdapterError("Oversized publisher journal commit", "access")
    descriptor, _ = retained_journal()
    _INITIAL_DIRECTORY_PROVEN = _JOURNAL_NEW = False
    _JOURNAL_DIRTY.add(descriptor)
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)
    retained_journal(False)
    offset = 0
    while offset < len(payload):
        retained_journal(False)
        count = os.pwrite(descriptor, payload[offset:], offset)
        if type(count) is not int or not 0 < count <= len(payload) - offset:
            raise AdapterError("Incomplete publisher journal write", "access")
        offset += count
        retained_journal(False)
    os.fsync(descriptor)
    _, before = retained_journal(False)
    raw = os.pread(descriptor, len(payload) + 1, 0)
    _, after = retained_journal(False)
    if raw != payload or journal_version(before) != journal_version(after):
        raise AdapterError("Publisher journal bytes changed", "access")
    _JOURNAL_VERSIONS[descriptor] = journal_version(after)
    _JOURNAL_DIRTY.discard(descriptor)
    _JOURNAL_COMMITS += 1


def merge_budget(left, right):
    """Preserve every retained floor and every unresolved transaction owner."""
    left, right = validate_budget(left), validate_budget(right)
    if left["schema"] != right["schema"]:
        raise AdapterError("Explicit reviewed schema transition required", "access")
    result = copy.deepcopy(left)
    for key in (
        "until",
        "interval",
        "page_gap",
        "completed",
        "inventory_completed",
        "observed_429_since_transition",
        "rate_limit_until",
        "observed_at",
    ):
        result[key] = max(left[key], right[key])
    if left["research_provenance"] != right["research_provenance"]:
        raise AdapterError("Research provenance differs across state", "access")
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["blocked"] = left["blocked"] or right["blocked"]
    result["in_flight"] = left["in_flight"] or right["in_flight"]
    if left["schema"] == 4:
        if left["continuity_anchor"] != right["continuity_anchor"]:
            raise AdapterError("Continuity anchor mismatch", "access")
        for key in ("physical", "policy", "logical"):
            a, b = left[key], right[key]
            if a["pending"] and b["pending"] and a["id"] != b["id"]:
                raise AdapterError("Conflicting retained transaction owners", "access")
            # A later completed receipt may settle only its own exact owner.
            if a["pending"] and b["id"] != a["id"]:
                result[key] = copy.deepcopy(a)
            else:
                result[key] = copy.deepcopy(b)
        result["in_flight"] = (
            result["physical"]["pending"] or result["policy"]["pending"]
        )
    return validate_budget(result)


def remaining():
    check_deadline()
    return min(_RUN_END - EXPORT_TIMEOUT, _PHASE_END) - time.monotonic()


def pace(interval=12.2):
    """Hold the physical journal lock through the response completion."""
    lock = locked_budget()
    try:
        state = load_budget()
        require_modern_state(state)
        if state["in_flight"]:
            raise AdapterError("Unsettled publisher request retained", "access")
        if state["blocked"]:
            raise AdapterError("Retained actual publisher refusal", "access")
        if state["observed_429_since_transition"] >= 100:
            raise AdapterError("Unsupported retained429 history; no traffic", "access")
        state["interval"] = max(state["interval"], interval, 12.2)
        if state["rate_limit_until"] > time.time():
            raise AdapterError("Retained publisher rate-limit embargo", "access", 429)
        while True:
            now = time.time()
            recent = [t for t in state["starts"] if now - t < 60]
            ready = max(
                state["until"],
                state["completed"] + state["interval"],
                recent[-1] + state["interval"] if recent else now,
                recent[-5] + 60 if len(recent) >= 5 else now,
            )
            if ready - now + 30 >= remaining():
                raise AdapterError(
                    "Retained pacing exceeds remaining collection budget",
                    "access",
                )
            if ready <= now:
                state["starts"] = sorted(set(state["starts"] + [now]))
                state["observed_at"] = now
                new_owner(state, "physical")
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


def retry_embargo(value):
    """Parse one Retry-After, including the three complete HTTP-date forms."""
    if value is None:
        return 0
    value = value.strip()
    now = time.time()
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value):
        result = now + float(value)
    else:
        imf = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
        rfc850 = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [0-9]{2}-[A-Z][a-z]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT"
        asctime = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} (?: [1-9]|[12][0-9]|3[01]) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}"
        if not any(re.fullmatch(pattern, value) for pattern in (imf, rfc850, asctime)):
            raise ValueError("Unsupported Retry-After date")
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            if not re.fullmatch(asctime, value):
                raise ValueError("Missing Retry-After timezone")
            parsed = parsed.replace(tzinfo=timezone.utc)
        if re.fullmatch(rfc850, value):
            current = datetime.fromtimestamp(now, timezone.utc)
            if parsed > current.replace(year=current.year + 50):
                parsed = parsed.replace(year=parsed.year - 100)
        result = parsed.timestamp()
    if not numeric(result):
        raise ValueError("Nonfinite Retry-After")
    datetime.fromtimestamp(result, timezone.utc)
    return result if result > now else 0


def legacy_transport_pace(url, interval=6):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    if _STATE_PARENT_FD is not None:
        state_parent(directory)
    else:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    information = os.lstat(directory)
    if (
        not os.path.isdir(directory)
        or os.path.islink(directory)
        or information.st_uid != os.getuid()
        or information.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    parent = state_parent(directory)
    key = hashlib.sha256(host.encode()).hexdigest()
    descriptor = os.open(
        key,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=parent,
    )
    try:
        validate_state_descriptor(descriptor, parent, key)
    except BaseException:
        os.close(descriptor)
        raise
    state = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read()
        budget = json.loads(raw) if raw else {"starts": [], "interval": 6, "until": 0}
        budget["interval"] = max(budget["interval"], interval, 6)
        while True:
            now = time.time()
            budget["starts"] = [start for start in budget["starts"] if now - start < 60]
            ready = max(
                budget["until"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (budget["starts"][0] + 60 if len(budget["starts"]) >= 10 else now),
            )
            if ready <= now:
                budget["starts"].append(now)
                save_transport_budget(state, budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def save_transport_budget(state, budget):
    state_parent(_STATE_PARENT_PATH)
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())
    state_parent(_STATE_PARENT_PATH)


def legacy_request_bytes(url, *, interval=6, method="GET", headers=None, payload=None):
    state, budget = legacy_transport_pace(url, interval)
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={"User-Agent": _USER_AGENT, **(headers or {})},
        )
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retry = response.headers.get("Retry-After")
            if retry:
                try:
                    seconds = (
                        float(retry)
                        if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                        else email.utils.parsedate_to_datetime(retry).timestamp()
                        - time.time()
                    )
                    budget["until"] = max(
                        budget["until"], time.time() + max(0, seconds)
                    )
                    save_transport_budget(state, budget)
                except (ValueError, TypeError, OverflowError):
                    pass
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            return response.status, response.headers, body
    finally:
        state.close()


def infrastructure_pace(url):
    """Honor new metadata embargoes without adding a publication floor."""
    host = urllib.parse.urlsplit(url).hostname
    while True:
        wait = _INFRASTRUCTURE_UNTIL.get(host, 0) - time.time()
        if wait <= 0:
            return
        if wait + 1 >= remaining():
            raise AdapterError("GitHub embargo exceeds phase budget", "access")
        time.sleep(min(wait, 30))


def retain_infrastructure_retry(url, value):
    host = urllib.parse.urlsplit(url).hostname
    _INFRASTRUCTURE_UNTIL[host] = max(
        _INFRASTRUCTURE_UNTIL.get(host, 0), retry_embargo(value)
    )


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


def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=5_000_000,
    infrastructure=False,
):
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED, _PUBLISHER_ENTRIES
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if type(byte_limit) is not int or not 0 < byte_limit <= 5_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError("Robots purpose requires literal robots.txt", "access")
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    publisher = host == "ut.ee" or host.endswith(".ut.ee")
    if not publisher and not infrastructure:
        return legacy_request_bytes(
            url, interval=interval, method=method, headers=headers, payload=payload
        )
    if publisher and infrastructure:
        raise AdapterError("Publisher cannot use metadata purpose", "access")
    if infrastructure:
        infrastructure_pace(url)
    if publisher and not _STATE_TRUSTED:
        raise AdapterError("Trusted publisher state required before access", "access")
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = pace(interval) if publisher else (None, None)
    response = None
    assessed = closed = False
    assessment = None
    status = None
    end = min(_PHASE_END, _RUN_END) if _PHASE_END and _RUN_END else None
    try:
        if publisher:
            require_modern_state(state)
            verify_local_publisher_start()
            state["physical"]["target"] = url
            if robots:
                target = _ROBOTS_CONTEXT["target"] if _ROBOTS_CONTEXT else SOURCE_URL
                new_owner(state, "policy", target)
            write_budget(state)
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={**(headers or {}), "User-Agent": _USER_AGENT},
        )
        if publisher:
            retained_journal()
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
            _PUBLISHER_ENTRIES += 1
        try:
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            ordinary = not isinstance(reason, ssl.SSLError) and (
                isinstance(reason, (ConnectionError, TimeoutError))
                or isinstance(reason, OSError)
                and reason.errno
                in (
                    errno.ECONNABORTED,
                    errno.ECONNREFUSED,
                    errno.ECONNRESET,
                    errno.EHOSTDOWN,
                    errno.EHOSTUNREACH,
                    errno.ENETDOWN,
                    errno.ENETUNREACH,
                    errno.EPIPE,
                    errno.ETIMEDOUT,
                )
            )
            if publisher and ordinary and not robots:
                assessed = closed = True
                assessment = {"kind": "bounded ordinary connection failure"}
            raise
        status = response.status
        # Save CURRENT facts before headers, exponentiation, context entry or body.
        if publisher and status == 429:
            retain_known_429(state)
        if publisher and status in (401, 403, 451) and not robots:
            state["blocked"] = True
            state["observed_at"] = time.time()
            write_budget(state)
        header_fault = False
        try:
            retries = response.headers.get_all("Retry-After", [])
            embargo, header_fault = retry_after_fields(retries)
            if publisher:
                state["until"] = max(state["until"], embargo)
                state["observed_at"] = time.time()
                write_budget(state)
            elif embargo:
                _INFRASTRUCTURE_UNTIL[host] = max(
                    _INFRASTRUCTURE_UNTIL.get(host, 0), embargo
                )
        except BaseException:
            raise
        length = response_framing(response.headers, byte_limit)
        chunks, count = [], 0
        while True:
            check_deadline()
            part = response.read(min(65536, byte_limit + 1 - count))
            check_deadline()
            if not isinstance(part, bytes):
                raise AdapterError("Invalid response body bytes", "access")
            if not part:
                break
            chunks.append(part)
            count += len(part)
            if count > byte_limit:
                raise AdapterError(
                    "Response exceeds byte bound; pending retained", "fetch"
                )
        body = b"".join(chunks)
        if length is not None and count != length:
            raise AdapterError("Truncated framed body; pending retained", "access")
        if header_fault:
            raise AdapterError("Ambiguous Retry-After; positive MAX retained", "access")
        if publisher:
            ordinary_unavailable = robots and 400 <= status < 500 and status != 429
            challenge = visible_access_challenge(
                body, challenge_only=ordinary_unavailable or status == 429
            )
            body_fact = {
                "status": status,
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "url": url,
            }
            if challenge:
                state["blocked"] = True
                state["observed_at"] = time.time()
                write_budget(state)
                # Real refusal assessed, but remains an explicit durable barrier.
            if robots:
                if ordinary_unavailable and not challenge:
                    delay = 12.2
                    policy_fact = {
                        **body_fact,
                        "kind": "REP unavailable",
                        "target": target,
                    }
                elif status == 200 and not challenge:
                    text = body.decode(
                        response.headers.get_content_charset() or "utf-8"
                    )
                    try:
                        delay = apply_robots_policy(text, target, state)
                    except RobotsDisallow:
                        delay = state["interval"]
                    policy_fact = {
                        **body_fact,
                        "kind": "REP assessed",
                        "target": target,
                    }
                else:
                    delay = None
                    policy_fact = {
                        **body_fact,
                        "kind": (
                            "REP explicit refusal" if challenge else "REP HTTP stop"
                        ),
                        "target": target,
                    }
                if _ROBOTS_CONTEXT is not None:
                    _ROBOTS_CONTEXT["delay"] = delay
            assessed = True
            assessment = body_fact
            if robots:
                assessment["policy"] = policy_fact
            if challenge:
                raise AdapterError(
                    "Visible publisher verification or refusal", "access", status
                )
            if state["blocked"]:
                raise AdapterError("Publisher refusal retained", "access", status)
            if status == 429:
                raise AdapterError(
                    "Publisher429; durable adaptive cooldown retained", "access", 429
                )
        return status, response.headers, body
    finally:
        close_error = None
        if response is not None:
            # Keep the original absolute deadline during close, even when header
            # persistence failed before a response context could be entered.
            try:
                if end is not None:
                    arm_alarm(end)
                response.close()
                check_deadline()
                closed = True
            except BaseException as error:
                close_error = error
        if lock is not None:
            try:
                now = time.time()
                state["completed"] = state["observed_at"] = now
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = now
                if (
                    assessed
                    and closed
                    and close_error is None
                    and not _PUBLISHER_GATE_FAILED
                ):
                    if robots and state["policy"]["pending"]:
                        complete_owner(state, "policy", assessment["policy"])
                    complete_owner(state, "physical", assessment)
                write_budget(state)
                if state["in_flight"] or close_error is not None:
                    _STATE_TRUSTED = False
                    _PUBLISHER_GATE_FAILED = True
                print(
                    json.dumps(
                        {
                            "event": "request_complete",
                            "url": url,
                            "time": time.time(),
                            "assessed": assessed and closed and close_error is None,
                        }
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                lock.close()
        if close_error is not None:
            raise AdapterError(
                "Response close failed; pending retained", "access"
            ) from close_error


def inventory_guard():
    """Each physical inventory retry/redirect retains the completion gap."""
    with locked_budget():
        state = load_budget()
    while True:
        wait = state["inventory_completed"] + state["page_gap"] - time.time()
        if wait <= 0:
            return
        if wait + 30 >= remaining():
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


def prepare_collection():
    global _COLLECTION_LOCK_FD, _STATE_TRUSTED, _LOCAL_FIRST_ACCESS_CHECKED
    global _COLLECTION_LOCK_ID, _PUBLISHER_GATE_FAILED, _LOCAL_PRE_RESTORE_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if hosted_qa_mode() and not _RESTORED:
        verify_hosted_qa_identity()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        if _LOCAL_INITIALIZATION_APPROVAL is None or _LOCAL_NATIVE_APPROVAL is None:
            raise AdapterError(
                "Reviewed initializer and first-native remain OFF", "access"
            )
        directory = os.path.join(
            tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
        )
        if not os.path.lexists(directory):
            raise AdapterError(
                "Existing reviewed local family required; no bootstrap", "access"
            )
        verify_local_authority()
        local_workflow_definition()
        if not _RESTORED:
            with locked_budget():
                local = load_budget()
                require_modern_state(local)
                if (
                    local["blocked"]
                    or local["in_flight"]
                    or any(
                        local[k]["pending"] for k in ("physical", "policy", "logical")
                    )
                ):
                    raise AdapterError(
                        "Retained local owner/barrier precedes metadata", "access"
                    )
                _, info = retained_journal()
                _LOCAL_PRE_RESTORE_VERSION = journal_version(info)
                resolve_remote_continuity()
                retained_journal()
    if not _RESTORED:
        # Authentication and finite lineage resolution happen before namespace IO.
        resolve_remote_continuity()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
        fd = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            os.close(fd)
            raise AdapterError("Unsafe lifecycle lock", "access")
        identity = (info.st_dev, info.st_ino)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = os.fstat(fd)
            if (current.st_dev, current.st_ino) != identity:
                raise AdapterError("Collection lock FD was reused", "access")
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            with contextlib.suppress(OSError):
                current = os.fstat(fd)
                if (current.st_dev, current.st_ino) == identity:
                    os.close(fd)
            raise AdapterError(
                "Another or unhealthy publisher collector", "access"
            ) from None
        _COLLECTION_LOCK_FD = fd
        _COLLECTION_LOCK_ID = identity
    if _STATE_TRUSTED and os.environ.get("GITHUB_ACTIONS") == "true":
        return
    _LOCAL_FIRST_ACCESS_CHECKED = False
    restore_family_artifact()
    _STATE_TRUSTED = True


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
    return min(30, remaining)


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


def close_collection_lock():
    """Release only the lifecycle lock identity captured at acquisition."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    descriptor, identity = _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
    _COLLECTION_LOCK_FD = _COLLECTION_LOCK_ID = None
    if descriptor is None:
        return
    try:
        info = os.fstat(descriptor)
        if (
            identity is None
            or (info.st_dev, info.st_ino) != identity
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise AdapterError("Collection lock identity changed", "access")
        os.close(descriptor)
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        raise


@contextlib.contextmanager
def execution():
    global _RUN_END, _STATE_TRUSTED, _PUBLISHER_GATE_FAILED, _EXECUTION_CLOSED, _LAST_RUN_END
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    _LAST_RUN_END = _RUN_END
    _EXECUTION_CLOSED = False
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        errors = []
        for cleanup in (
            lambda: signal.setitimer(signal.ITIMER_REAL, 0),
            lambda: signal.signal(signal.SIGALRM, previous),
            close_collection_lock,
            close_state_parents,
        ):
            try:
                cleanup()
            except BaseException as error:
                errors.append(error)
        _RUN_END = None
        if errors:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            invalidate_family_artifact()
            raise AdapterError(
                "Execution cleanup failed; readiness invalid", "access"
            ) from errors[0]
        if _PUBLISHER_GATE_FAILED:
            invalidate_family_artifact()
        _EXECUTION_CLOSED = True


@contextlib.contextmanager
def phase(name, seconds, reserve=EXPORT_TIMEOUT):
    global _PHASE_END, _PHASE_NAME, _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _PHASE_END is not None or _RUN_END is None:
        raise AdapterError("Invalid phase transition", "access")
    _PHASE_NAME = name
    limit = _RUN_END if name == "export" else _RUN_END - reserve
    _PHASE_END = min(time.monotonic() + seconds, limit)
    try:
        check_deadline()
        arm_alarm(_PHASE_END)
        yield
        check_deadline()
    finally:
        _PHASE_END = _PHASE_NAME = None
        try:
            arm_alarm(limit)
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            raise


def local_workflow_definition():
    """Require the exact publisher workflow paused during local collection."""
    definition = workflow_api(f"/actions/workflows/{_WORKFLOW_ID}")
    if (
        definition.get("id") != _WORKFLOW_ID
        or definition.get("path") != f".github/workflows/fetch-{SOURCE_ID}.yml"
        or definition.get("state") != "disabled_manually"
    ):
        raise AdapterError(
            "Local collection requires its paused publisher workflow", "access"
        )


def reviewed_local_legacy_state():
    """Authenticate the existing reviewed research origin, never reset state."""
    marker = _LOCAL_RESEARCH_MIGRATION
    if (
        not isinstance(marker, dict)
        or set(marker) != {"source", "sha256", "evidence"}
        or marker.get("source") != SOURCE_ID
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/279"
            r"#issuecomment-[0-9]+",
            marker.get("evidence", ""),
        )
    ):
        raise AdapterError("Reviewed local legacy transition missing", "access")
    with locked_budget():
        current = load_budget()
        original = json.dumps(current["research_provenance"], sort_keys=True).encode()
        if hashlib.sha256(original).hexdigest() != marker["sha256"]:
            raise AdapterError("Local legacy research origin changed", "access")
        return current


def verify_local_publisher_start():
    """Recheck remote continuity after the local startup quiet guard."""
    global _LOCAL_FIRST_ACCESS_CHECKED
    qa = hosted_qa_mode()
    if (os.environ.get("GITHUB_ACTIONS") == "true" and not qa) or _LOCAL_FIRST_ACCESS_CHECKED:
        return
    if qa:
        verify_hosted_qa_identity()
    latest = latest_family_run()
    current = (_SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS)
    if latest != _RESTORED_LATEST or current != _RESTORED_HISTORY:
        raise AdapterError(
            "Family history changed before local publisher access", "access"
        )
    local_workflow_definition()
    if not qa:
        verify_observer_readiness(_LOCAL_NATIVE_APPROVAL)
    _LOCAL_FIRST_ACCESS_CHECKED = True


def workflow_api(path):
    """Read verified workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError("Workflow state credentials missing", "access")
    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache, no-store",
        "Pragma": "no-cache",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    nonce = urllib.parse.urlencode({"state_read": time.time_ns()})
    path += ("&" if "?" in path else "?") + nonce
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers=headers,
            infrastructure=True,
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
        raise MetadataReadUnavailable(
            ("Workflow pacing state unavailable"), ("access")
        ) from None


def current_run_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "") for key in (("GITHUB_RUN_ID"), ("GITHUB_RUN_ATTEMPT"))
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
    """Use exact-attempt job completion rather than mutable run updates."""
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
        if count == 0 and jobs == []:
            marker = _LEGACY_NO_JOB.get(identity)
            if (
                page != 1
                or attempt != 1
                or not marker
                or marker["head_sha"] != head
                or _SCANNED_JOB_DIGESTS[(identity, attempt, page)]
                != marker["jobs_sha256"]
            ):
                raise ValueError("Unreviewed empty exact-attempt jobs")
            return None
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
            value = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00"))
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
            or run[("head_repository")][("full_name")] != ("YouthOpps/data-pipeline")
            or run["head_branch"] != "main"
            or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
            or type(run.get("workflow_id")) is not int
            or run["workflow_id"] != _WORKFLOW_ID
            or revision is not None
            and (run["head_sha"], run["workflow_id"]) != revision
            or expected
            and (identity, attempt, source) != expected
        ):
            raise ValueError("Untrusted completed attempt")
        if identity in _LEGACY_NO_JOB and attempt == 1:
            marker = _LEGACY_NO_JOB[identity]
            if (
                run["head_sha"] != marker["head_sha"]
                or run.get("conclusion") != marker["conclusion"]
                or run.get("created_at") != marker["created_at"]
            ):
                raise ValueError("Reviewed cancelled attempt changed")
            finished = attempt_completion(identity, attempt, run["head_sha"])
            if finished is not None:
                raise ValueError("Reviewed no-job attempt now contains jobs")
            _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
            return None
        value = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        finished = attempt_completion(identity, attempt, run["head_sha"])
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
        return finished, began, identity, attempt, source
    except MetadataReadUnavailable:
        raise
    except Exception:
        raise AdapterError(("Untrusted completed family attempt"), ("access")) from None


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
    """Select completed chronology, including older-ID rerun attempts."""
    global _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS
    global _ORDERED_ATTEMPTS
    _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS = {}, {}, {}
    # The QA run is a distinct owner. Scan only the paused producer history.
    hosted = os.environ.get("GITHUB_ACTIONS") == "true" and not hosted_qa_mode()
    current_id, current_attempt = current_run_identity() if hosted else (None, None)
    if not hosted:
        local_workflow_definition()
    current_seen = False
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        route = (
            "/actions/workflows/fetch-ee-university-of-tartu.yml/runs"
            if hosted
            else f"/actions/workflows/{_WORKFLOW_ID}/runs"
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
                    raise AdapterError("Unexpected local family workflow", "access")
                continue
            if not hosted and run.get("status") != "completed":
                raise AdapterError(
                    "Local collection requires terminal family history",
                    "access",
                )
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
                    or run.get("workflow_id") != _WORKFLOW_ID
                ):
                    raise AdapterError(
                        "Current workflow head or identity changed", "access"
                    )
                if attempt != current_attempt:
                    raise AdapterError(
                        ("Current workflow attempt mismatch"), ("access")
                    )
                current_seen = True
                candidates.extend(
                    completed_attempt_inventory(run, include_current=False)
                )
                continue
            if run.get(("status")) in (
                ("queued"),
                ("waiting"),
                ("pending"),
                ("requested"),
            ):
                candidates.extend(
                    completed_attempt_inventory(run, include_current=False)
                )
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.extend(completed_attempt_inventory(run, include_current=True))
        if scanned >= total:
            if scanned != total:
                raise AdapterError(("Workflow inventory count mismatch"), ("access"))
            if hosted and not current_seen:
                raise AdapterError(
                    "Current workflow absent from complete history", "access"
                )
            if not candidates:
                return None
            ordered = sorted(c for c in candidates if c is not None)
            if not ordered:
                raise AdapterError("No trusted executed family attempt", "access")
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(("Ambiguous family attempt chronology"), ("access"))
            _ORDERED_ATTEMPTS = ordered
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


class InventoryRace(AdapterError):
    """The advertised complete workflow inventory changed during reading."""


def source_revision_digest(head):
    """Bind an artifact code digest to its authenticated source revision."""
    if not re.fullmatch(r"[0-9a-f]{40}", head or ""):
        raise ValueError("Invalid source revision")
    path = "adapters/" + SOURCE_ID + "/adapter.py"
    result = workflow_api("/contents/" + path + "?ref=" + head)
    if result.get("path") != path or result.get("encoding") != "base64":
        raise ValueError("Unexpected source revision response")
    content = base64.b64decode(result["content"], validate=False)
    if len(content) != result.get("size") or len(content) > 5_000_000:
        raise ValueError("Invalid source revision size")
    blob = b"blob " + str(len(content)).encode() + b"\0" + content
    if hashlib.sha1(blob).hexdigest() != result.get("sha"):
        raise ValueError("Source revision blob mismatch")
    return hashlib.sha256(content).hexdigest()


def download_inert_artifact(identity, artifact_id, digest=None):
    ("Download a bounded authenticated artifact, stripping " "cross-origin auth.")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token and os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError("Workflow state credentials missing", "access")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    infrastructure_pace(url)
    try:
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retain_infrastructure_retry(url, response.headers.get("Retry-After"))
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
        status, _, body = request_bytes(
            destination, byte_limit=32768, infrastructure=True
        )
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
        if isinstance(envelope, dict) and envelope.get("kind") == _RECEIPT_PROTOCOL:
            return validate_no_effect_receipt(envelope, identity)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema",
            "repository",
            "run_id",
            "run_attempt",
            "source",
            "head_sha",
            "adapter_sha256",
            "state_sha256",
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
            or envelope["adapter_sha256"]
            != source_revision_digest(envelope["head_sha"])
            or envelope["state_sha256"]
            != hashlib.sha256(
                json.dumps(
                    envelope["state"], sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        ):
            raise ValueError("Artifact ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            ("Invalid or unavailable family pacing artifact"), ("access")
        ) from None


def source_file_digest():
    """Hash this standalone implementation without retaining its contents."""
    descriptor = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        current = os.stat(__file__, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe implementation file", "access")
        content = stream.read(5_000_001)
        current = os.stat(__file__, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Implementation file changed", "access")
    if len(content) > 5_000_000:
        raise AdapterError("Source implementation exceeds reviewed bound", "access")
    return hashlib.sha256(content).hexdigest()


@contextlib.contextmanager
def owned_artifact_directory(path):
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    """Pin the verified directory for all relative artifact operations."""
    root, before = checked_artifact_path(path)
    descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    try:
        held = os.fstat(descriptor)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Artifact directory changed", "access")
        verify_artifact_directory(root, descriptor)
        yield root, descriptor
    finally:
        try:
            os.close(descriptor)
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            raise


def verify_artifact_directory(root, descriptor):
    """Reject replacement parents without following their contents."""
    held = os.fstat(descriptor)
    current = os.stat(root, follow_symlinks=False)
    if (
        not stat.S_ISDIR(current.st_mode)
        or current.st_uid != os.getuid()
        or current.st_mode & 0o022
        or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise AdapterError("Artifact directory changed", "access")


def owned_artifact_info(root, directory):
    """Inspect only the current safely owned artifact in the pinned parent."""
    verify_artifact_directory(root, directory)
    try:
        info = os.stat("pacing-state.json", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None
    validate_owned_output(info)
    return info


def same_owned_artifact(root, directory, expected):
    """Prevent replacing or unlinking an independently replaced file."""
    current = owned_artifact_info(root, directory)
    if current is None or expected is None:
        same = current is None and expected is None
    else:
        same = (current.st_dev, current.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        )
    if not same:
        raise AdapterError("Owned artifact identity changed", "access")


def workflow_output_signal(value):
    """Append readiness only to a nonblocking, verified regular signal."""
    descriptor = os.open(
        os.environ["GITHUB_OUTPUT"],
        os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
    )
    with os.fdopen(descriptor, "a") as output:
        info = os.fstat(output.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise AdapterError("Unsafe workflow output signal", "access")
        output.write(f"pacing_ready={value}\n")
        output.flush()
        os.fsync(output.fileno())


def invalidate_owned_artifact(root, directory):
    """Invalidate safe JSON before fallible signal and unlink operations."""
    stale = owned_artifact_info(root, directory)
    descriptor = None
    if stale is not None:
        descriptor = os.open(
            "pacing-state.json",
            os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            held = os.fstat(descriptor)
            validate_owned_output(held)
            if (held.st_dev, held.st_ino) != (stale.st_dev, stale.st_ino):
                raise AdapterError("Owned artifact identity changed", "access")
            same_owned_artifact(root, directory, stale)
        except BaseException:
            os.close(descriptor)
            raise
    errors = []
    try:
        if descriptor is not None:
            try:
                os.ftruncate(descriptor, 0)
                os.fsync(descriptor)
            except BaseException as error:
                errors.append(error)
        try:
            workflow_output_signal("false")
        except BaseException as error:
            errors.append(error)
    finally:
        if descriptor is not None:
            os.close(descriptor)
            try:
                same_owned_artifact(root, directory, stale)
                os.unlink("pacing-state.json", dir_fd=directory)
            except BaseException as error:
                errors.append(error)
    if errors:
        raise AdapterError("Owned state invalidation failed closed", "access")


def invalidate_family_artifact():
    """Invalidate safely owned stale state before readiness signal IO."""
    path = os.environ.get("TARTU_PACING_ARTIFACT_PATH")
    if not path or _EXPORTED_ID is None:
        return
    with owned_artifact_directory(path) as (root, directory):
        same_owned_artifact(root, directory, _EXPORTED_ID)
        invalidate_owned_artifact(root, directory)


def validate_owned_output(info):
    """Refuse unsafe output before reading or modifying it."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe owned artifact output", "access")


def checked_artifact_path(path):
    """Validate this attempt's private output directory and basename."""
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
    return root, info


def export_family_artifact():
    """Atomically export trusted state; invalidate only our output generation."""
    global _EXPORTED_ID
    path = os.environ.get("TARTU_PACING_ARTIFACT_PATH")
    if hosted_qa_mode():
        raise AdapterError("QA must use distinct nonproducer state evidence", "access")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    with owned_artifact_directory(path) as (root, directory):
        prior = owned_artifact_info(root, directory)
        if prior is not None:
            raise AdapterError(
                "Existing artifact output cannot be overwritten", "access"
            )
        temporary = None
        temporary_info = None
        try:
            with locked_budget():
                run_id, attempt = current_run_identity()
                state = load_budget()
                if state["in_flight"] or state.get("logical", {}).get("pending", False):
                    raise AdapterError("Unsettled state cannot be exported", "access")
                envelope = {
                    "schema": 1,
                    "repository": "YouthOpps/data-pipeline",
                    "run_id": run_id,
                    "run_attempt": attempt,
                    "source": SOURCE_ID,
                    "head_sha": os.environ.get("GITHUB_SHA"),
                    "adapter_sha256": source_file_digest(),
                    "state_sha256": hashlib.sha256(
                        json.dumps(
                            state, sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                    "state": state,
                }
                if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
                    raise AdapterError("Invalid artifact source revision", "access")
                temporary = ".owned-pacing-" + os.urandom(16).hex()
                verify_artifact_directory(root, directory)
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                    | os.O_NONBLOCK,
                    0o600,
                    dir_fd=directory,
                )
                temporary_info = os.fstat(descriptor)
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(envelope, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                same_owned_artifact(root, directory, prior)
                retained_journal()
                os.replace(
                    temporary,
                    "pacing-state.json",
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
                temporary = None
                _EXPORTED_ID = temporary_info
                same_owned_artifact(root, directory, temporary_info)
                retained_journal()
                workflow_output_signal("true")
                retained_journal()
        except BaseException:
            if _EXPORTED_ID is not None:
                same_owned_artifact(root, directory, _EXPORTED_ID)
                invalidate_owned_artifact(root, directory)
            raise
        finally:
            try:
                if temporary is not None and temporary_info is not None:
                    verify_artifact_directory(root, directory)
                    current = os.stat(
                        temporary, dir_fd=directory, follow_symlinks=False
                    )
                    validate_owned_output(current)
                    if (current.st_dev, current.st_ino) != (
                        temporary_info.st_dev,
                        temporary_info.st_ino,
                    ):
                        raise AdapterError("Temporary artifact changed", "access")
                    os.unlink(temporary, dir_fd=directory)

            except BaseException:
                raise


def restore_family_artifact():
    """Merge authenticated continuity into the original owned journal."""
    global _RESTORED, _STATE_TRUSTED, _RESTORED_LATEST, _RESTORED_HISTORY
    global _VERIFIED_REMOTE_CREATE
    if _RESTORED and os.environ.get("GITHUB_ACTIONS") == "true":
        return
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if not hosted:
        verify_local_authority()
    incoming, latest = resolve_remote_continuity()
    if hosted_qa_mode():
        if latest != (44169842563, 1, SOURCE_ID) or len(_SCANNED_HISTORY) != 45:
            raise AdapterError("Exact paused production owner history changed", "access")
        incoming = merge_budget(incoming, validate_hosted_qa_approval())
        _VERIFIED_REMOTE_CREATE = incoming
    elif hosted:
        if _HOSTED_PRODUCER_APPROVAL is None:
            raise AdapterError(
                "Reviewed native/producer approval remains OFF", "access"
            )
        incoming = merge_budget(incoming, validate_hosted_approval())
        _VERIFIED_REMOTE_CREATE = incoming
    else:
        verify_local_initialization(incoming)
    try:
        with locked_budget():
            local = load_budget()
            require_modern_state(local)
            combined = merge_budget(local, incoming)
            if combined["in_flight"] or any(
                combined[k]["pending"] for k in ("physical", "policy", "logical")
            ):
                raise AdapterError("Unsettled newest publisher state", "access")
            combined["until"] = max(combined["until"], time.time() + 60)
            combined["observed_at"] = time.time()
            write_budget(combined)
    finally:
        _VERIFIED_REMOTE_CREATE = None
    _RESTORED_LATEST = latest
    _RESTORED_HISTORY = (
        copy.deepcopy(_SCANNED_HISTORY),
        copy.deepcopy(_SCANNED_JOB_DIGESTS),
        copy.deepcopy(_ATTEMPT_HEADS),
    )
    _RESTORED = _STATE_TRUSTED = True


def fetch_source(url, min_interval=12.2):
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
                literal_robots = urllib.parse.urlsplit(current).path == "/robots.txt"
                if original.path == "/robots.txt" and not literal_robots:
                    raise AdapterError("Robots redirect leaves robots.txt", "access")
                status, headers, body = request_bytes(
                    current, interval=min_interval, robots=literal_robots
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
                if literal_robots and 400 <= status < 500 and status != 429:
                    return ""
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


def robots_policy(text, url):
    """Assess the unchanged source-local REP selection and matching rules."""
    parsed = urllib.parse.urlsplit(url)
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
        elif key == "request-rate":
            match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", value)
            if not match:
                raise AdapterError("Invalid robots request rate", "access")
            group["rate"] = int(match[2]) + 0.01
            if not numeric(group["rate"]):
                raise AdapterError("Unsupported robots request rate", "access")
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
                        re.escape(part) for part in pattern.removesuffix("$").split("*")
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
            raise RobotsDisallow(
                "Robots policy disallows this source endpoint", "access"
            )
    return max(
        [12.2] + [max(g["delay"], g["rate"]) for policy in policies for g in policy]
    )


def apply_robots_policy(text, url, state=None):
    """Keep policy ownership pending until the physical close is assessed."""
    if state is None:
        with locked_budget():
            current = load_budget()
            require_modern_state(current)
            if current["in_flight"]:
                raise AdapterError("Unsettled publisher transaction", "access")
            new_owner(current, "policy", url)
            write_budget(current)
            try:
                delay = apply_robots_policy(text, url, current)
            except RobotsDisallow:
                complete_owner(
                    current,
                    "policy",
                    {
                        "kind": "supplied REP disallow",
                        "body_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    },
                )
                write_budget(current)
                raise
            complete_owner(
                current,
                "policy",
                {
                    "kind": "supplied REP assessed",
                    "body_sha256": hashlib.sha256(text.encode()).hexdigest(),
                },
            )
            write_budget(current)
            return delay
    require_modern_state(state)
    if not state["policy"]["pending"] or state["policy"]["target"] != url:
        raise AdapterError("Exact pending REP owner required", "access")
    try:
        delay = robots_policy(text, url)
    except RobotsDisallow:
        state["blocked"] = True
        state["observed_at"] = time.time()
        write_budget(state)
        raise
    state["interval"] = max(state["interval"], delay)
    state["observed_at"] = time.time()
    write_budget(state)
    return delay


def check_robots(url):
    global _ROBOTS_CONTEXT
    parsed = urllib.parse.urlsplit(url)
    previous = _ROBOTS_CONTEXT
    context = {"target": url, "delay": None}
    _ROBOTS_CONTEXT = context
    try:
        try:
            text = fetch_source(
                urllib.parse.urlunsplit(
                    (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
                )
            )
        except AdapterError as error:
            if error.status == 404:
                return 12.2
            raise
        if context["delay"] is not None:
            return context["delay"]
        # A supplied/cached policy still needs its own durable transaction.
        return apply_robots_policy(text, url)
    finally:
        _ROBOTS_CONTEXT = previous


def read_snapshot():
    if _PHASE_END is not None:
        return read_snapshot_body()
    with phase("snapshot", 120):
        return read_snapshot_body()


def publish_files(snapshot, files):
    if _PHASE_END is not None:
        return publish_files_body(snapshot, files)
    with phase("publication", 240):
        return publish_files_body(snapshot, files)


def collect():
    """Keep logical collection ownership until every physical receipt is settled."""
    if _RUN_END is None:
        with execution():
            return collect()
    with phase("collection", COLLECTION_TIMEOUT, reserve=390):
        prepare_collection()
        with locked_budget():
            state = load_budget()
            require_modern_state(state)
            if state["logical"]["pending"]:
                raise AdapterError("Unsettled logical collection retained", "access")
            new_owner(state, "logical")
            write_budget(state)
        try:
            records = collect_body()
        except BaseException as error:
            if explicit_semantic_guard(error):
                settle_logical("compiled semantic guard")
            raise
        settle_logical("collection complete")
        return records


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    if hosted_qa_mode():
        raise AdapterError("Use the original nonpublishing QA test", "access")
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
    attempt, failure = (utc_now(), None)
    try:
        records = collect()
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
                key: value for key, value in before.items() if key not in temporal
            } == {key: value for key, value in record.items() if key not in temporal}
            record.update(
                created_at=before.get("created_at", attempt),
                first_seen_at=before.get("first_seen_at", attempt),
                updated_at=(before.get("updated_at", attempt) if same else attempt),
                last_seen_at=attempt,
                last_checked_at=attempt,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(key=lambda record: record["published_at"] or "", reverse=True)
        records = remove_expired_records(records, utc_now())
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = (error, old)
    outcome = metadata(attempt, previous, records, failure)
    if args.publish and (not failure or snapshot["files"]["data.json"] is not None):
        files = {
            "metadata.json": json.dumps(outcome, ensure_ascii=False, indent=2) + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        try:
            publish_files(snapshot, files)
        except Exception as error:
            with phase("failure", 120):
                failure = error
                outcome = metadata(
                    attempt,
                    previous,
                    old,
                    AdapterError("Publication failed: " + safe_error(error), "publish"),
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
                        "Publication failed; durable failure status could not be recorded. Remote data was not force-overwritten."
                    )
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


def main():
    metadata_failure = None
    try:
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
                if os.environ.get("GITHUB_ACTIONS") == "true" and _STATE_TRUSTED:
                    with phase("export", EXPORT_TIMEOUT):
                        export_family_artifact()
    except MetadataReadUnavailable as error:
        metadata_failure = error
    if metadata_failure is not None:
        with receipt_export_deadline():
            export_metadata_no_effect_receipt(metadata_failure)
        raise metadata_failure


class HeaderRateLimitStop(AdapterError):
    """A header-only HTTP 429 stop assessed after safe closure."""


@contextlib.contextmanager
def header_rate_limit_response(response):
    """Retain existing barriers when closing an HTTP 429 fails."""
    global _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    try:
        with response:
            yield
    except HeaderRateLimitStop:
        raise
    except BaseException as error:
        error._header429_unassessed = True
        _PUBLISHER_GATE_FAILED = True
        _STATE_TRUSTED = False
        raise


# Issue 419: these are activation gates, not defaults or bootstrap permissions.
_LOCAL_INITIALIZATION_APPROVAL = None
_LOCAL_NATIVE_APPROVAL = None
_HOSTED_PRODUCER_APPROVAL = None
_HOSTED_QA_APPROVAL = {'state': {'blocked': False,
           'completed': 1793297116.5691018,
           'family': 'youthopps-ee-university-of-tartu-publisher-v1',
           'in_flight': False,
           'interval': 12.2,
           'inventory_completed': 1793297116.5691018,
           'legacy_429_history': 'unknown',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'observed_429_since_transition': 0,
           'observed_at': 1794947411.0,
           'page_gap': 30,
           'rate_limit_until': 0,
           'research_provenance': {'all_actual_log_hash_manifest_sha256': '5c73fbcf8b9aa11f11a6d53d237b352c9db3d366efd0da027cb86c69873cdbb1',
                                   'complete_history_manifest_sha256': 'dd42a0939f62bde63c1e343dc2918afd0e532625df473d995232193f496ca945',
                                   'exact_cancelled_no_job_count': 1,
                                   'executed_completion_upper_bounds': 10,
                                   'in_flight_legacy_semantics': 'Unknown old semantics; False '
                                                                 'proposed ONLY for explicitly '
                                                                 'reviewed one-off transition, '
                                                                 'never old-schema default',
                                   'kind': 'explicit_unknown_legacy_transition',
                                   'legacy_429_history': 'unknown',
                                   'legacy_hold_history': 'unlogged_fields_unknown',
                                   'legacy_wrapper_count': 11,
                                   'old_local_journal': 'AUTHENTIC_ABSENT_NOT_ZERO',
                                   'scope': 'Complete ten actual success logs provide no recovered '
                                            'content refusal; unlogged headers/strikes remain '
                                            'unknown. Old job completions are conservative upper '
                                            'bounds, not actual request starts.',
                                   'source': 'ee-university-of-tartu'},
           'schema': 4,
           'starts': [1792104166.4833934,
                      1792104167.3899724,
                      1792104179.590142,
                      1792104179.5919755,
                      1792104180.4125512,
                      1792104180.6398733,
                      1792104197.3905509,
                      1792104197.3911424,
                      1792104197.6323326,
                      1792104199.693189,
                      1792434260.4229486,
                      1792434278.223312,
                      1792434290.9225087,
                      1792434308.7228436,
                      1792513551.378799,
                      1792513569.1790934,
                      1792513582.0890417,
                      1792513599.889297,
                      1792600910.2536979,
                      1792600928.0540497,
                      1792600940.897707,
                      1792600958.6980371,
                      1792687374.4581275,
                      1792687392.25813,
                      1792687405.0141888,
                      1792687422.8144817,
                      1792773171.1150548,
                      1792773188.9154086,
                      1792773201.656748,
                      1792773219.4571614,
                      1792855647.5008998,
                      1792855665.3011847,
                      1792855678.1224592,
                      1792855695.9227967,
                      1792942687.2392924,
                      1792942705.0396755,
                      1792942717.835842,
                      1792942735.6361632,
                      1793038724.2248912,
                      1793038737.0437832,
                      1793122301.3047464,
                      1793122314.019934,
                      1793210204.7744994,
                      1793210218.0599513,
                      1793297101.902864,
                      1793297114.8489358],
           'until': 1794947531.0,
           'continuity_anchor': '762dce04f4a42f32f4c151dc4ee299679463eda71fbf7251806498905d0cdd6f',
           'legacy_transaction_history': 'owner_and_receipts_unlogged_unknown',
           'physical': {'id': None,
                        'pending': False,
                        'started': None,
                        'receipt': None,
                        'target': None},
           'policy': {'id': None,
                      'pending': False,
                      'started': None,
                      'receipt': None,
                      'target': None},
           'logical': {'id': None,
                       'pending': False,
                       'started': None,
                       'receipt': None,
                       'target': None}},
 'state_sha256': '067f27f670b0fcaf197f6ea93f7502d50c105aa097eeecf045ca3f8636015643',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/419#issuecomment-6961752511'}
_HOSTED_QA_WITNESS = None
_ORDERED_ATTEMPTS = []
_REMOTE_CONTINUITY = None
_TEMP_ROOT_FD = None
_TEMP_ROOT_PIN = None
_EXECUTION_CLOSED = False
_LAST_RUN_END = None
_PUBLISHER_ENTRIES = 0
_JOURNAL_COMMITS = 0
_EXPORTED_ID = None
_RECEIPT_PROTOCOL = "tartu-metadata-no-effect-v1"


class MetadataReadUnavailable(AdapterError):
    """Only metadata transport/status/JSON availability, never identity doubt."""


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def unknown_owner():
    return {
        "id": None,
        "pending": False,
        "started": None,
        "receipt": None,
        "target": None,
    }


def validate_budget(state):
    if not isinstance(state, dict):
        raise AdapterError("Invalid retained publisher state", "access")
    if state.get("schema") == 3:
        return validate_legacy_budget(state)
    additions = {
        "continuity_anchor",
        "legacy_transaction_history",
        "physical",
        "policy",
        "logical",
    }
    if (
        state.get("schema") != 4
        or set(state) != set(legacy_empty_budget(0)) | additions
    ):
        raise AdapterError("Invalid typed publisher state schema", "access")
    old = {
        key: copy.deepcopy(value)
        for key, value in state.items()
        if key not in additions
    }
    old["schema"] = 3
    validate_legacy_budget(old)
    if (
        state["continuity_anchor"] != _CONTINUITY_ANCHOR
        or state["legacy_transaction_history"] != "owner_and_receipts_unlogged_unknown"
    ):
        raise AdapterError("Untrusted typed publisher continuity", "access")
    for key in ("physical", "policy", "logical"):
        owner = state[key]
        if not isinstance(owner, dict) or set(owner) != set(unknown_owner()):
            raise AdapterError("Invalid transaction owner shape", "access")
        if type(owner["pending"]) is not bool:
            raise AdapterError("Invalid transaction pending marker", "access")
        if owner["id"] is None:
            if owner != unknown_owner():
                raise AdapterError("Unknown legacy owner was invented", "access")
        else:
            if (
                not isinstance(owner["id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", owner["id"])
                or not numeric(owner["started"])
                or owner["started"] > state["observed_at"]
                or not isinstance(owner["target"], str)
                or owner["pending"]
                and owner["receipt"] is not None
                or not owner["pending"]
                and not isinstance(owner["receipt"], dict)
            ):
                raise AdapterError("Invalid transaction ownership", "access")
            if owner["receipt"] is not None:
                receipt = owner["receipt"]
                if (
                    set(receipt) != {"owner", "completed", "assessment"}
                    or receipt["owner"] != owner["id"]
                    or not numeric(receipt["completed"])
                    or not owner["started"]
                    <= receipt["completed"]
                    <= state["observed_at"]
                    or not isinstance(receipt["assessment"], dict)
                ):
                    raise AdapterError("Invalid matching transaction receipt", "access")
    if state["in_flight"] != (
        state["physical"]["pending"] or state["policy"]["pending"]
    ):
        raise AdapterError("Physical and policy pending markers disagree", "access")
    return state


def reviewed_upgrade(old):
    """One explicit transition: genuine old state plus the complete known MAX."""
    validate_legacy_budget(old)
    full = merge_budget(old, copy.deepcopy(_HISTORICAL_MAX))
    if full["in_flight"] or full["blocked"]:
        raise AdapterError("Retained legacy barrier cannot be upgraded away", "access")
    full["schema"] = 4
    full["continuity_anchor"] = _CONTINUITY_ANCHOR
    full["legacy_transaction_history"] = "owner_and_receipts_unlogged_unknown"
    for key in ("physical", "policy", "logical"):
        full[key] = unknown_owner()
    return validate_budget(full)


def new_owner(state, key, target=""):
    if state[key]["pending"]:
        raise AdapterError("Prior transaction owner remains pending", "access")
    state[key] = {
        "id": os.urandom(16).hex(),
        "pending": True,
        "started": time.time(),
        "receipt": None,
        "target": target,
    }
    state["in_flight"] = state["physical"]["pending"] or state["policy"]["pending"]
    state["observed_at"] = time.time()


def complete_owner(state, key, assessment):
    owner = state[key]
    if not owner["pending"] or owner["id"] is None:
        raise AdapterError("Exact pending transaction owner required", "access")
    now = time.time()
    owner["receipt"] = {
        "owner": owner["id"],
        "completed": now,
        "assessment": assessment,
    }
    owner["pending"] = False
    state["in_flight"] = state["physical"]["pending"] or state["policy"]["pending"]
    state["observed_at"] = now


def require_modern_state(state):
    validate_budget(state)
    if state["schema"] != 4:
        raise AdapterError("Reviewed typed state transition required", "access")


def verify_temporary_root(directory):
    """Retain the original temporary-root inode, including shared sticky /tmp."""
    global _TEMP_ROOT_FD, _TEMP_ROOT_PIN
    root = os.path.dirname(directory)
    if root != os.path.abspath(tempfile.gettempdir()):
        raise AdapterError("Unexpected publisher temporary ancestry", "access")
    fresh = _TEMP_ROOT_FD is None
    descriptor = (
        os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if fresh
        else _TEMP_ROOT_FD
    )
    try:
        held = os.fstat(descriptor)
        named = os.stat(root, follow_symlinks=False)
        pin = (
            held.st_dev,
            held.st_ino,
            held.st_uid,
            held.st_gid,
            stat.S_IMODE(held.st_mode),
        )
        if (
            not stat.S_ISDIR(held.st_mode)
            or (held.st_dev, held.st_ino) != (named.st_dev, named.st_ino)
            or pin
            != (
                named.st_dev,
                named.st_ino,
                named.st_uid,
                named.st_gid,
                stat.S_IMODE(named.st_mode),
            )
            or held.st_mode & 0o022
            and not held.st_mode & stat.S_ISVTX
            or not fresh
            and pin != _TEMP_ROOT_PIN
        ):
            raise AdapterError("Publisher temporary ancestry changed", "access")
    except BaseException:
        if fresh:
            os.close(descriptor)
        raise
    if fresh:
        _TEMP_ROOT_FD, _TEMP_ROOT_PIN = descriptor, pin


def close_state_parents():
    global _STATE_PARENT_FD, _TEMP_ROOT_FD
    errors = []
    for name in ("_STATE_PARENT_FD", "_TEMP_ROOT_FD"):
        descriptor = globals()[name]
        if descriptor is None:
            continue
        expected = (
            _STATE_PARENT_ID if name == "_STATE_PARENT_FD" else _TEMP_ROOT_PIN[:2]
        )
        try:
            if name == "_STATE_PARENT_FD":
                state_parent(_STATE_PARENT_PATH)
            else:
                verify_temporary_root(_STATE_PARENT_PATH)
        except BaseException as error:
            errors.append(error)
        try:
            held = os.fstat(descriptor)
            if (held.st_dev, held.st_ino) != expected:
                raise AdapterError(
                    "Retained parent FD reused; foreign FD untouched", "access"
                )
            os.close(descriptor)
        except BaseException as error:
            errors.append(error)
        globals()[name] = None
    if errors:
        raise AdapterError("Publisher parent close failed", "access") from errors[0]


def verify_exclusive_flock(descriptor):
    # /proc reports locks associated with this held open file description.
    # Reacquisition would hide an intervening unlock, so it is deliberately absent.
    with open(f"/proc/self/fdinfo/{descriptor}", encoding="ascii") as stream:
        lines = stream.read(8193)
    if len(lines) > 8192 or not any(
        re.fullmatch(
            r"lock:\s+[0-9]+: FLOCK\s+ADVISORY\s+WRITE\s+[0-9]+\s+[^ ]+\s+0 EOF", line
        )
        for line in lines.splitlines()
    ):
        raise AdapterError("Original publisher exclusive flock was lost", "access")


def artifact_inventory(identity):
    run, attempt, source = identity
    result = workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or result.get("total_count") != len(artifacts)
        or len(artifacts) > 100
        or any(not isinstance(a, dict) for a in artifacts)
    ):
        raise AdapterError("Incomplete exact artifact inventory", "access")
    matches = [a for a in artifacts if a.get("name") == f"{_ARTIFACT_NAME}-{attempt}"]
    if len(matches) > 1:
        raise AdapterError("Ambiguous exact owned artifacts", "access")
    if not matches:
        return None
    a = matches[0]
    if (
        a.get("expired") is not False
        or type(a.get("id")) is not int
        or a["id"] <= 0
        or a.get("workflow_run", {}).get("id") != run
        or a.get("workflow_run", {}).get("head_sha")
        != _ATTEMPT_HEADS.get((run, attempt))
        or type(a.get("size_in_bytes")) is not int
        or not 0 < a["size_in_bytes"] <= 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", a.get("digest", ""))
    ):
        raise AdapterError("Untrusted exact owned artifact", "access")
    return a


def resolve_remote_continuity():
    global _REMOTE_CONTINUITY
    if _REMOTE_CONTINUITY is not None:
        return copy.deepcopy(_REMOTE_CONTINUITY)
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Existing source history missing; no bootstrap", "access")
    historical = {(int(k), 1): v for k, v in _HISTORICAL_INVENTORY.items()}
    if not set(historical).issubset(_SCANNED_HISTORY):
        raise AdapterError("Complete reviewed 45-attempt ancestry missing", "access")
    for key, pin in historical.items():
        if (
            _SCANNED_HISTORY[key] != (pin["head"], _WORKFLOW_ID)
            or _SCANNED_JOB_DIGESTS.get((*key, 1)) != pin["jobs_sha256"]
        ):
            raise AdapterError("Reviewed immutable ancestry changed", "access")
    for candidate in reversed(_ORDERED_ATTEMPTS):
        identity = candidate[2:]
        run, attempt, source = identity
        artifact = artifact_inventory(identity)
        if artifact is None:
            exact = workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
            if exact.get("total_count") != 0 or exact.get("artifacts") != []:
                raise AdapterError(
                    "Reviewed missing-owner artifact inventory changed", "access"
                )
            gap = _GAP_PINS.get(str(run))
            if (
                not gap
                or attempt != 1
                or _ATTEMPT_HEADS.get((run, attempt)) != gap["head"]
                or candidate[:2] != (gap["finished"], gap["began"])
                or source_revision_digest(gap["head"]) != _HISTORICAL_SOURCE_SHA
            ):
                raise AdapterError(
                    "Newest mature attempt lacks exact trusted state", "access"
                )
            if source_workflow_revision_digest(gap["head"]) != _HISTORICAL_WORKFLOW_SHA:
                raise AdapterError("Reviewed gap workflow changed", "access")
            continue
        incoming = download_inert_artifact(identity, artifact["id"], artifact["digest"])
        if isinstance(incoming, dict) and incoming.get("kind") == _RECEIPT_PROTOCOL:
            validate_no_effect_receipt(incoming, identity)
            continue
        if incoming["schema"] == 3:
            if (
                identity != (40927206756, 1, SOURCE_ID)
                or artifact["id"] != 12921456341
                or artifact["digest"]
                != "sha256:66987781077f23b7b8a65dc252cb3bb0f8e8cb449f8a000e6fec6e9ba2d4a77f"
                or source_revision_digest(_ATTEMPT_HEADS[(run, attempt)])
                != _HISTORICAL_SOURCE_SHA
            ):
                raise AdapterError(
                    "Unreviewed legacy state transition refused", "access"
                )
            incoming = reviewed_upgrade(incoming)
        _REMOTE_CONTINUITY = (incoming, latest)
        return copy.deepcopy(_REMOTE_CONTINUITY)
    raise AdapterError("No exact authenticated owned state remains", "access")


def validate_no_effect_receipt(receipt, identity):
    expected = {
        "kind",
        "source",
        "repository",
        "run_id",
        "run_attempt",
        "head_sha",
        "adapter_sha256",
        "stage",
        "publisher_entries",
        "journal_commits",
        "namespace_absent",
        "execution_closed",
        "prior_allowance",
    }
    run, attempt, source = identity
    if (
        set(receipt) != expected
        or receipt["kind"] != _RECEIPT_PROTOCOL
        or receipt["source"] != source
        or receipt["repository"] != "YouthOpps/data-pipeline"
        or type(receipt["run_id"]) is not int
        or type(receipt["run_attempt"]) is not int
        or (receipt["run_id"], receipt["run_attempt"]) != (run, attempt)
        or receipt["head_sha"] != _ATTEMPT_HEADS[(run, attempt)]
        or receipt["adapter_sha256"] != source_revision_digest(receipt["head_sha"])
        or receipt["stage"] != "metadata_transport_status_json_before_namespace"
        or type(receipt["publisher_entries"]) is not int
        or receipt["publisher_entries"] != 0
        or type(receipt["journal_commits"]) is not int
        or receipt["journal_commits"] != 0
        or receipt["namespace_absent"] is not True
        or receipt["execution_closed"] is not True
        or receipt["prior_allowance"] is not None
    ):
        raise AdapterError("Invalid authenticated metadata no-effect receipt", "access")
    return receipt


def no_effect_namespace_absence():
    """No local owner or outside authority may be hidden by a no-state receipt."""
    directory = tempfile.gettempdir()
    original = local_identity(directory, True)
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in (
            SOURCE_ID + "-pacing-" + str(os.getuid()),
            SOURCE_ID + "-authority-" + str(os.getuid()),
            SOURCE_ID + ".lock",
        ):
            try:
                os.stat(name, dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            return None
        if local_identity(directory, True) != original:
            raise AdapterError("No-effect receipt parent changed", "access")
        return original
    finally:
        os.close(fd)


def export_metadata_no_effect_receipt(error):
    global _EXPORTED_ID
    """A typed error is insufficient: all negative physical facts must hold."""
    if (
        type(error) is not MetadataReadUnavailable
        or _HOSTED_PRODUCER_APPROVAL is None
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or not _EXECUTION_CLOSED
        or _PUBLISHER_GATE_FAILED
        or _PUBLISHER_ENTRIES
        or _JOURNAL_COMMITS
        or _STATE_PATH is not None
        or _JOURNAL_LOCKS
        or _STATE_TRUSTED
        or _RESTORED
    ):
        return
    validate_hosted_approval()
    absence = no_effect_namespace_absence()
    if absence is None:
        return
    run, attempt = current_run_identity()
    receipt = {
        "kind": _RECEIPT_PROTOCOL,
        "source": SOURCE_ID,
        "repository": "YouthOpps/data-pipeline",
        "run_id": run,
        "run_attempt": attempt,
        "head_sha": os.environ.get("GITHUB_SHA"),
        "adapter_sha256": source_file_digest(),
        "stage": "metadata_transport_status_json_before_namespace",
        "publisher_entries": 0,
        "journal_commits": 0,
        "namespace_absent": True,
        "execution_closed": True,
        "prior_allowance": None,
    }
    # Write only an absent output: never hide an older/stronger/untrusted object.
    path = os.environ.get("TARTU_PACING_ARTIFACT_PATH")
    if not path:
        return
    with owned_artifact_directory(path) as (root, parent):
        if owned_artifact_info(root, parent) is not None:
            raise AdapterError(
                "No-effect receipt cannot replace existing output", "access"
            )
        fd = os.open(
            "pacing-state.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        identity = os.fstat(fd)
        _EXPORTED_ID = identity
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(receipt, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            same_owned_artifact(root, parent, identity)
            os.fsync(parent)
            if no_effect_namespace_absence() != absence:
                raise AdapterError(
                    "Local owner appeared during receipt export", "access"
                )
            workflow_output_signal("true")
            same_owned_artifact(root, parent, identity)
            if no_effect_namespace_absence() != absence:
                raise AdapterError(
                    "Local owner appeared after receipt signal", "access"
                )
        except BaseException:
            same_owned_artifact(root, parent, identity)
            invalidate_owned_artifact(root, parent)
            raise


def explicit_semantic_guard(error):
    if type(error) is not AdapterError or error.stage != "parse":
        return False
    traceback = error.__traceback__
    while traceback is not None and traceback.tb_next is not None:
        traceback = traceback.tb_next
    return (
        traceback is not None
        and traceback.tb_frame.f_code.co_filename == __file__
        and (traceback.tb_frame.f_code.co_name, traceback.tb_lineno)
        in _SEMANTIC_GUARD_SITES
    )


def settle_logical(reason):
    with locked_budget():
        state = load_budget()
        if (
            not _STATE_TRUSTED
            or _PUBLISHER_GATE_FAILED
            or state["in_flight"]
            or state["physical"]["pending"]
            or state["policy"]["pending"]
            or state["blocked"]
        ):
            raise AdapterError(
                "Unassessed collection cannot settle logical owner", "access"
            )
        complete_owner(state, "logical", {"reason": reason})
        write_budget(state)


_HISTORICAL_SOURCE_SHA = (
    "ff397c279a1efdfbea4c541e988c09c4044d3f317305fa0b7cf1a3340d6bc086"
)

_HISTORICAL_INVENTORY = {
    "37818503497": {
        "head": "9ab03a510dba38548ed53dd378c797be03547bdf",
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
    },
    "37809621343": {
        "head": "ce76427dcc2bfc744961a6058bac6d0100bea31e",
        "jobs_sha256": "bd338e8881b6eb0824776ca99e028166bf114c44e6ed0f98086e3564b30d4677",
    },
    "37810563514": {
        "head": "10e7dc06e65127013bf2a5ba6c835cac6a749aa8",
        "jobs_sha256": "336b8e1e1a637370409999977b24e8353269c8b2e657900bb8fa6a9fe69bef8c",
    },
    "37819106792": {
        "head": "9ab03a510dba38548ed53dd378c797be03547bdf",
        "jobs_sha256": "00a6ccb0e63588957037895247792d52a867d1b1489c856f6bb6919b98c161a3",
    },
    "38071840355": {
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "jobs_sha256": "97eedc7aeab54556057e90b4a681c181a1ac982ad92c38d12ff17fb357e34d9e",
    },
    "38536206685": {
        "head": "3f6ab17f4698de58f7365682a22384cd08788f69",
        "jobs_sha256": "6c5c5ddd964b825960c44250aa2d0bc02782d10fe43c1035e8c7c251d2f785fa",
    },
    "38684235995": {
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs_sha256": "b945eec4174c4a7b1ee1309a439087f67aba30e02ccffd81b01381db1a2e097d",
    },
    "38721259407": {
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs_sha256": "12e047da549a6dea392e240bca436f95e6cc37f0a2d45ac90fb61a2157ff1e4f",
    },
    "38720084006": {
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs_sha256": "9a92710d2629d74e5fa17233fb5132272f4e0bc97bd39f766aba1c35e5b921dc",
    },
    "38763419353": {
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs_sha256": "3efbe10604c86607970814f2e54b9e9560cd98565aa6bc1645cd65752ecb1022",
    },
    "38767785897": {
        "head": "f3b6e313a7680479d083c98d5ed32e94dc44fdc5",
        "jobs_sha256": "d6d960ac3e66c71ed618d0f2b8d9abcbcf3b7ee5b47f9c95499e4e42ad8c1a73",
    },
    "38821357518": {
        "head": "a25494982049b2027604537310b6445cdc023d8c",
        "jobs_sha256": "72f916ec98bdda2bf95f87f9375dc2df099638e7f4f7fab652a71f20a649f5d3",
    },
    "38929530947": {
        "head": "1fa4b2b243f6ba69ed99ebd9c7ef840427ae9790",
        "jobs_sha256": "9e474e497a255aa589d6022130b4fc1c28ae2e2071ee912912f9b60bd356c1c5",
    },
    "39043151554": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "49a1891b2f6628ded1332f795f2162ea497e09d7f4c7b5c02e04bcb646724063",
    },
    "39140423301": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "fa3fe3ac3a3f38f4f2589b9dbda52edc5117460a62fdac9c4cc17818db9e0c3d",
    },
    "39301978499": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "4745f50d8d1b71557c63600f93f402813668092a39379c61baec83abf4c23cfb",
    },
    "39457097334": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "28fb02651c98826e4f3a91bee595217b0fa2f4b421090687d85f66c803dc18a4",
    },
    "39636814751": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "cf8f6771edf57991d7cb3aeda345c8be9a601a0c7b95fc29ca34dfb2fd128fc6",
    },
    "39815992465": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "5684ad30b5fe5466c081646462735f08c53c0a64068d7387172490947f644ee4",
    },
    "39987421277": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "ba0e7fb11b4c118a39c7f009975a5f6f21d39817975012e47af716de00c4b236",
    },
    "40106918101": {
        "head": "46fe9e001ccd2f684b6d4941dbb69bcbc63f598b",
        "jobs_sha256": "c3cb5a37c69ffa9a76135f6823431cd751be6d5b2da5f1ff6165679a448ca3bd",
    },
    "40207729690": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "8217daffd0c08877dfe7cf85c227e939faaa54448e47192db02a224c9f7f738e",
    },
    "40376401654": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "e85d980521fcc7d197dd94ba2fbc1e34aba7f9494f54b6f9ab95790237d4a40b",
    },
    "40551215561": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "3e5b1d1fe613708ab6a3f1e1e156ab4e5d4f74d1fbec944b3b0cc0b8e9dc0f5d",
    },
    "40740267759": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "49402ebabcdd978cb622d2f3173555f0392b91a2e7dda6a7fa1cf7ed655cbd85",
    },
    "40927206756": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "3b1e338b25df1ed1755122c3076faa9f08477b77eb91d9dfead5df0ab7136625",
    },
    "41101153888": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "87594a96abc340e324b16b1ab39a6e1d33b3148fab803ca8abf61ceab83cdd8b",
    },
    "41221728466": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "b6a1caa51362571c8c780cdb3337dc548293a83675c63839acac275e043288f6",
    },
    "41332653824": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "e2a51039d7a6bf720d42540c63c65f914351adcc8a9e28635322df4201262160",
    },
    "41528236758": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "391ec7e3c6c951b366f44f60c8cf2a38d653be683040d6f18666a9721ab83ecb",
    },
    "41700055118": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "147cef8082f76fd827d49835e25bdc70ff60b016f5e3d1ee819a8c815f7eceb5",
    },
    "41896557386": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "43eeeca9be6866535ab74ac648db8c20329d9d26414f66cb7ddba9067bf52055",
    },
    "42094802456": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "47d396a6956129e98dff9f1732eac74ac3de40ec236231bb949f2ae69d1b8747",
    },
    "42275139897": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "e5c5d23f0d708c2251490ad8c15c6a41f005dffbd102c1b447e6f4500c3affce",
    },
    "42396408633": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "ce756e53c386797d714d09fcee8cadfd87eea00bf4f4e0305004867acff07adb",
    },
    "42505954078": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "d7d7687d6b3b135fc5c45bd3d362758423e19682169bb0e0138b00c6feaa77b0",
    },
    "42707327330": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "60a94c06e18b98fc846bb70571bc3a5abc2e71380ea94fbe3124c4a6f7036af2",
    },
    "42884453082": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "d51c0b2852a0e362bf19a7eb592ff656110ed1d3eaa74ef08886a4aee3f83cef",
    },
    "43085656986": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "007354e68cf6285ab0c71c407c23f9b111f067c68d9cb76cbe335cc1f8bc273a",
    },
    "43291539123": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "fb1dc7d63ba9d9571580e7312c5971b5e21209d48b1781cfbf3251ceeaf7c51d",
    },
    "43490325427": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "a2a7239f432612086f0cf5c8594b5db2120e322848eb4ccd49a26343c8cedffb",
    },
    "43622479706": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "ac663c570bcbe60d7e80c14c6b7a9a170976536baf8a052b67a8f23728e22a4b",
    },
    "43747089713": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "jobs_sha256": "4fdc1dd238b8df501389f2479ae92346a2e21c9f1670edaad4a0136f63657813",
    },
    "43962316198": {
        "head": "9e8e59d64d479829912bc343fc23b526f6519aea",
        "jobs_sha256": "93fc7b43fc80b574a488a7ea443d21c36a3adf1af85f1c1f47d1eea88604d338",
    },
    "44169842563": {
        "head": "32f5bb7ea4994872888459988f23e7cdf1818b91",
        "jobs_sha256": "a9d1c4b7cffe184c761f4bfa977638130319260b507a9cbd1bc3526100510450",
    },
}

_GAP_PINS = {
    "41101153888": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793382254.0,
        "finished": 1793382385.0,
        "job_ids": [124218466145],
    },
    "41221728466": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793465521.0,
        "finished": 1793465542.0,
        "job_ids": [124597506667],
    },
    "41332653824": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793554814.0,
        "finished": 1793554836.0,
        "job_ids": [124925816694],
    },
    "41528236758": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793654740.0,
        "finished": 1793654771.0,
        "job_ids": [125561405833],
    },
    "41700055118": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793732756.0,
        "finished": 1793732788.0,
        "job_ids": [126140652462],
    },
    "41896557386": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793820240.0,
        "finished": 1793820264.0,
        "job_ids": [126807513722],
    },
    "42094802456": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793907128.0,
        "finished": 1793907155.0,
        "job_ids": [127481918943],
    },
    "42275139897": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1793991360.0,
        "finished": 1793991388.0,
        "job_ids": [128089419206],
    },
    "42396408633": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794072455.0,
        "finished": 1794072479.0,
        "job_ids": [128474626092],
    },
    "42505954078": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794159498.0,
        "finished": 1794159526.0,
        "job_ids": [128808518476],
    },
    "42707327330": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794261433.0,
        "finished": 1794261468.0,
        "job_ids": [129467454427],
    },
    "42884453082": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794338490.0,
        "finished": 1794338520.0,
        "job_ids": [130068968161],
    },
    "43085656986": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794424590.0,
        "finished": 1794424619.0,
        "job_ids": [130749226365],
    },
    "43291539123": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794512065.0,
        "finished": 1794512095.0,
        "job_ids": [131438758097],
    },
    "43490325427": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794597273.0,
        "finished": 1794597303.0,
        "job_ids": [132109455760],
    },
    "43622479706": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794678094.0,
        "finished": 1794678123.0,
        "job_ids": [132531002191],
    },
    "43747089713": {
        "head": "5fd9fa82d5d2d5f0871aba653376765a6e00c897",
        "began": 1794767401.0,
        "finished": 1794767437.0,
        "job_ids": [132912692362],
    },
    "43962316198": {
        "head": "9e8e59d64d479829912bc343fc23b526f6519aea",
        "began": 1794865179.0,
        "finished": 1794865217.0,
        "job_ids": [133632675272],
    },
    "44169842563": {
        "head": "32f5bb7ea4994872888459988f23e7cdf1818b91",
        "began": 1794947372.0,
        "finished": 1794947411.0,
        "job_ids": [134345711489],
    },
}

_HISTORICAL_MAX = {
    "blocked": False,
    "completed": 1793297116.5691018,
    "family": "youthopps-ee-university-of-tartu-publisher-v1",
    "in_flight": False,
    "interval": 12.2,
    "inventory_completed": 1793297116.5691018,
    "legacy_429_history": "unknown",
    "legacy_hold_history": "unlogged_fields_unknown",
    "observed_429_since_transition": 0,
    "observed_at": 1794947411.0,
    "page_gap": 30,
    "rate_limit_until": 0,
    "research_provenance": {
        "all_actual_log_hash_manifest_sha256": "5c73fbcf8b9aa11f11a6d53d237b352c9db3d366efd0da027cb86c69873cdbb1",
        "complete_history_manifest_sha256": "dd42a0939f62bde63c1e343dc2918afd0e532625df473d995232193f496ca945",
        "exact_cancelled_no_job_count": 1,
        "executed_completion_upper_bounds": 10,
        "in_flight_legacy_semantics": "Unknown old semantics; False proposed ONLY for explicitly reviewed one-off transition, never old-schema default",
        "kind": "explicit_unknown_legacy_transition",
        "legacy_429_history": "unknown",
        "legacy_hold_history": "unlogged_fields_unknown",
        "legacy_wrapper_count": 11,
        "old_local_journal": "AUTHENTIC_ABSENT_NOT_ZERO",
        "scope": "Complete ten actual success logs provide no recovered content refusal; unlogged headers/strikes remain unknown. Old job completions are conservative upper bounds, not actual request starts.",
        "source": "ee-university-of-tartu",
    },
    "schema": 3,
    "starts": [
        1792104166.4833934,
        1792104167.3899724,
        1792104179.590142,
        1792104179.5919755,
        1792104180.4125512,
        1792104180.6398733,
        1792104197.3905509,
        1792104197.3911424,
        1792104197.6323326,
        1792104199.693189,
        1792434260.4229486,
        1792434278.223312,
        1792434290.9225087,
        1792434308.7228436,
        1792513551.378799,
        1792513569.1790934,
        1792513582.0890417,
        1792513599.889297,
        1792600910.2536979,
        1792600928.0540497,
        1792600940.897707,
        1792600958.6980371,
        1792687374.4581275,
        1792687392.25813,
        1792687405.0141888,
        1792687422.8144817,
        1792773171.1150548,
        1792773188.9154086,
        1792773201.656748,
        1792773219.4571614,
        1792855647.5008998,
        1792855665.3011847,
        1792855678.1224592,
        1792855695.9227967,
        1792942687.2392924,
        1792942705.0396755,
        1792942717.835842,
        1792942735.6361632,
        1793038724.2248912,
        1793038737.0437832,
        1793122301.3047464,
        1793122314.019934,
        1793210204.7744994,
        1793210218.0599513,
        1793297101.902864,
        1793297114.8489358,
    ],
    "until": 1794947531.0,
}

_CONTINUITY_ANCHOR = "762dce04f4a42f32f4c151dc4ee299679463eda71fbf7251806498905d0cdd6f"


def retain_known_429(state):
    now = time.time()
    # Persist independent facts separately: no later exponent/header/body fault
    # can erase the observed status, CURRENT doubling or minimum embargo.
    state["observed_429_since_transition"] += 1
    state["until"] = max(state["until"], now + 1800)
    state["rate_limit_until"] = max(state["rate_limit_until"], now + 1800)
    state["observed_at"] = now
    write_budget(state)
    unsupported = False
    for field in ("interval", "page_gap"):
        doubled = state[field] * 2
        if not numeric(doubled):
            unsupported = True
            continue
        state[field] = doubled
        state["observed_at"] = time.time()
        write_budget(state)
    if unsupported:
        raise AdapterError(
            "Current429 doubling unrepresentable; pending retained", "access", 429
        )
    strikes = state["observed_429_since_transition"]
    state["interval"] = max(state["interval"], 12.2 * 2**strikes)
    state["page_gap"] = max(state["page_gap"], 30 * 2**strikes)
    state["until"] = max(state["until"], now + 1800 * 2 ** (strikes - 1))
    state["rate_limit_until"] = max(state["rate_limit_until"], state["until"])
    state["observed_at"] = time.time()
    write_budget(state)


def retry_after_fields(values):
    maximum, fault = 0, False
    if not isinstance(values, list):
        raise ValueError("Invalid Retry-After field collection")
    for value in values:
        try:
            maximum = max(maximum, retry_embargo(value))
        except (ValueError, TypeError, OverflowError, OSError, AttributeError):
            fault = True
            # Salvage each complete date and numeric member, without splitting
            # the weekday comma of IMF/RFC850 dates into invented durations.
            pattern = (
                r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT|"
                r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [0-9]{2}-[A-Z][a-z]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT|"
                r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} (?: [1-9]|[12][0-9]|3[01]) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}|"
                r"(?<![0-9A-Za-z:.-])[0-9]+(?:\.[0-9]+)?(?![0-9A-Za-z:.-])"
            )
            for match in re.finditer(pattern, str(value)):
                try:
                    maximum = max(maximum, retry_embargo(match.group()))
                except (ValueError, TypeError, OverflowError, OSError, AttributeError):
                    pass
    return maximum, fault


def response_framing(headers, limit):
    lengths = headers.get_all("Content-Length", [])
    transfers = headers.get_all("Transfer-Encoding", [])
    if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
        raise AdapterError("Ambiguous response framing; pending retained", "access")
    if transfers and transfers[0].strip().lower() != "chunked":
        raise AdapterError("Unsupported response transfer coding", "access")
    if not lengths:
        return None
    value = lengths[0].strip()
    if not re.fullmatch(r"[0-9]+", value) or int(value) > limit:
        raise AdapterError("Invalid bounded response length", "access")
    return int(value)


def source_workflow_revision_digest(head):
    path = f".github/workflows/fetch-{SOURCE_ID}.yml"
    result = workflow_api("/contents/" + path + "?ref=" + head)
    if result.get("path") != path or result.get("encoding") != "base64":
        raise AdapterError("Unexpected immutable workflow content", "access")
    content = base64.b64decode(result["content"])
    if len(content) != result.get("size") or len(content) > 65536:
        raise AdapterError("Invalid immutable workflow size", "access")
    blob = b"blob " + str(len(content)).encode() + b"\0" + content
    if hashlib.sha1(blob).hexdigest() != result.get("sha"):
        raise AdapterError("Immutable workflow blob mismatch", "access")
    return hashlib.sha256(content).hexdigest()


_HISTORICAL_WORKFLOW_SHA = (
    "c0ac75ce22b0aa138d4d24f356ae177340081b468c32cc7d4c1979496ea82a7b"
)


def local_machine_identity():
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as stream:
        boot = stream.read(100).strip()
    if not re.fullmatch(r"[0-9a-f-]{36}", boot):
        raise AdapterError("Local machine identity unavailable", "access")
    return {"boot_id": boot, "uid": os.getuid(), "hostname": os.uname().nodename}


def validate_initialization_packet(packet):
    expected = {
        "schema",
        "source",
        "adapter_sha256",
        "machine",
        "nonce",
        "state",
        "state_sha256",
        "observer_port",
        "evidence",
    }
    if (
        not isinstance(packet, dict)
        or set(packet) != expected
        or packet["schema"] != 1
        or packet["source"] != SOURCE_ID
        or packet["adapter_sha256"] != source_file_digest()
        or packet["machine"] != local_machine_identity()
        or not isinstance(packet["nonce"], str)
        or not re.fullmatch(r"[0-9a-f]{32}", packet["nonce"])
        or packet["state_sha256"] != canonical_digest(packet["state"])
        or packet["observer_port"] != 8894
        or not isinstance(packet["evidence"], str)
        or not packet["evidence"].startswith(
            "https://github.com/YouthOpps/data-pipeline/issues/419"
        )
    ):
        raise AdapterError("Exact reviewed initializer packet required", "access")
    require_modern_state(packet["state"])
    if (
        merge_budget(packet["state"], reviewed_upgrade(copy.deepcopy(_HISTORICAL_MAX)))
        != packet["state"]
    ):
        raise AdapterError("Initializer omits retained historical MAX", "access")
    if (
        packet["state"]["blocked"]
        or packet["state"]["in_flight"]
        or any(packet["state"][k]["pending"] for k in ("physical", "policy", "logical"))
    ):
        raise AdapterError("Initializer cannot settle a retained barrier", "access")
    return packet


def local_authority_directory():
    """One fixed sibling survives a moved or lost publisher family."""
    return os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-authority-" + str(os.getuid())
    )


def local_identity(path, directory=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino):
            raise AdapterError("Local original object changed", "access")
        if directory:
            if not stat.S_ISDIR(info.st_mode):
                raise AdapterError("Original local directory required", "access")
        else:
            validate_owned_output(info)
        return {
            "path": path,
            "identity": [info.st_dev, info.st_ino],
            "uid": info.st_uid,
            "mode": stat.S_IMODE(info.st_mode),
        }
    finally:
        os.close(fd)


def write_local_authority(name, value):
    """Exclusive outside authority is never recreated or used as a reset."""
    directory = local_authority_directory()
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        original = local_identity(directory, True)
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        fd = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        if os.write(fd, raw) != len(raw):
            raise AdapterError("Short outside authority write", "access")
        os.fsync(fd)
        if os.pread(fd, len(raw) + 1, 0) != raw:
            raise AdapterError("Outside authority readback differs", "access")
        os.fsync(parent)
        if local_identity(directory, True) != original:
            raise AdapterError("Outside authority directory changed", "access")
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def read_local_authority(name):
    directory = local_authority_directory()
    before = local_identity(directory, True)
    path = os.path.join(directory, name)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        validate_owned_output(info)
        raw = os.pread(fd, 16385, 0)
        current = os.stat(path, follow_symlinks=False)
        if (
            len(raw) > 16384
            or journal_version(info) != journal_version(current)
            or journal_version(info) != journal_version(os.fstat(fd))
            or local_identity(directory, True) != before
        ):
            raise AdapterError("Outside authority generation changed", "access")
        return raw
    finally:
        os.close(fd)


def verify_local_authority():
    """Before metadata, reject copied packets and replaced original objects."""
    native = _LOCAL_NATIVE_APPROVAL
    if not isinstance(native, dict):
        raise AdapterError("Exact native outside authority required", "access")
    wanted = native.get("outside_proof_sha256")
    if (
        not isinstance(wanted, str)
        or not re.fullmatch(r"[0-9a-f]{64}", wanted)
        or os.environ.get("TARTU419_OBSERVED_INIT_PROOF_SHA256") != wanted
    ):
        raise AdapterError(
            "ROOT-observed initializer exit-zero proof required", "access"
        )
    raw = read_local_authority("process-success.json")
    proof = json.loads(raw)
    if (
        hashlib.sha256(raw).hexdigest() != wanted
        or type(proof.get("initializer_exit")) is not int
        or proof["initializer_exit"] != 0
        or proof.get("initializer_stderr") != ""
        or proof.get("initialization_sha256")
        != canonical_digest(_LOCAL_INITIALIZATION_APPROVAL)
        or proof.get("source_sha256") != source_file_digest()
    ):
        raise AdapterError("Original initializer process witness differs", "access")
    authority = proof["result"]
    if (
        authority.get("initialization_sha256")
        != canonical_digest(_LOCAL_INITIALIZATION_APPROVAL)
        or authority.get("source_sha256") != source_file_digest()
        or json.loads(read_local_authority("authority.json")) != authority
    ):
        raise AdapterError("Original initialization authority differs", "access")
    for value in authority["objects"].values():
        if local_identity(value["path"], value["directory"]) != {
            k: value[k] for k in ("path", "identity", "uid", "mode")
        }:
            raise AdapterError("Original initialized namespace changed", "access")
    launch_raw = read_local_authority("native-process-once.json")
    launch = json.loads(launch_raw)
    pin = json.loads(os.environ.get("TARTU419_LAUNCH_AUTHORITY", "null"))
    info = local_identity(
        os.path.join(local_authority_directory(), "native-process-once.json")
    )
    if (
        not isinstance(pin, dict)
        or set(pin) != {"sha256", "identity"}
        or pin["sha256"] != hashlib.sha256(launch_raw).hexdigest()
        or pin["identity"] != info["identity"]
    ):
        raise AdapterError("Original outside native claim changed", "access")
    if (
        launch.get("pid") != os.getpid()
        or launch.get("initialization_proof_sha256") != wanted
        or launch.get("source_sha256") != source_file_digest()
    ):
        raise AdapterError("Exact outside native process owner required", "access")
    return authority


def initialize_reviewed_local_state(packet):
    """Explicit OFF-by-default initializer; never an ordinary runtime fallback."""
    global _VERIFIED_REMOTE_CREATE
    if (
        _LOCAL_INITIALIZATION_APPROVAL is None
        or packet != _LOCAL_INITIALIZATION_APPROVAL
    ):
        raise AdapterError("Local initialization approval remains OFF", "access")
    validate_initialization_packet(packet)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        raise AdapterError("Local initializer cannot run hosted", "access")
    local_workflow_definition()
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    if os.path.lexists(directory):
        raise AdapterError("Initializer refuses existing family; no reset", "access")
    authority_directory = local_authority_directory()
    os.mkdir(authority_directory, 0o700)
    write_local_authority(
        "initializer-once.json",
        {
            "initialization_sha256": canonical_digest(packet),
            "source_sha256": source_file_digest(),
        },
    )
    collection_path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
    descriptor = os.open(
        collection_path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _VERIFIED_REMOTE_CREATE = copy.deepcopy(packet["state"])
    try:
        with locked_budget():
            state = load_budget()
            write_budget(state)
        parent = state_parent()
        descriptor = os.open(
            "reviewed-initializer.json",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(descriptor, "w") as stream:
            json.dump(packet, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
        objects = {}
        for key, path, is_directory in (
            ("temporary_parent", tempfile.gettempdir(), True),
            ("authority", authority_directory, True),
            ("family", directory, True),
            ("journal", _STATE_PATH, False),
            ("receipt", os.path.join(directory, "reviewed-initializer.json"), False),
            ("collection_lock", collection_path, False),
        ):
            objects[key] = dict(
                local_identity(path, is_directory), directory=is_directory
            )
        authority = {
            "schema": 1,
            "initialization_sha256": canonical_digest(packet),
            "source_sha256": source_file_digest(),
            "objects": objects,
        }
        write_local_authority("authority.json", authority)
        return authority
    finally:
        _VERIFIED_REMOTE_CREATE = None


def verify_local_initialization(incoming):
    global _LOCAL_INIT_PACKET
    if _LOCAL_INITIALIZATION_APPROVAL is None or _LOCAL_NATIVE_APPROVAL is None:
        raise AdapterError(
            "Reviewed local initializer and first native remain OFF", "access"
        )
    packet = validate_initialization_packet(_LOCAL_INITIALIZATION_APPROVAL)
    native = _LOCAL_NATIVE_APPROVAL
    if (
        not isinstance(native, dict)
        or set(native)
        != {
            "source",
            "initializer_sha256",
            "adapter_sha256",
            "machine",
            "nonce",
            "observer_port",
            "controller_sha256",
            "test_sha256",
            "outside_proof_sha256",
            "activation_evidence",
        }
        or native["source"] != SOURCE_ID
        or native["initializer_sha256"] != canonical_digest(packet)
        or native["adapter_sha256"] != source_file_digest()
        or native["machine"] != local_machine_identity()
        or native["nonce"] != packet["nonce"]
        or native["observer_port"] != 8894
        or not isinstance(native["activation_evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/419#issuecomment-[1-9][0-9]*",
            native["activation_evidence"],
        )
        or native["activation_evidence"] == packet["evidence"]
    ):
        raise AdapterError("Exact first-native approval required", "access")
    verify_local_authority()
    # Open existing family and exact receipt before metadata quiet/first wire.
    with locked_budget():
        local = load_budget()
        if (
            _LOCAL_PRE_RESTORE_VERSION is not None
            and journal_version(retained_journal()[1]) != _LOCAL_PRE_RESTORE_VERSION
        ):
            raise AdapterError("Local generation changed across metadata", "access")
        if (
            local["blocked"]
            or local["in_flight"]
            or any(local[k]["pending"] for k in ("physical", "policy", "logical"))
        ):
            raise AdapterError("Retained local owner precedes native claim", "access")
        if merge_budget(local, incoming) != local:
            raise AdapterError("Local initialized state omits remote MAX", "access")
        parent = state_parent()
        descriptor = os.open(
            "reviewed-initializer.json",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent,
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            validate_owned_output(before)
            raw = stream.read(16385)
            current = os.stat(
                "reviewed-initializer.json", dir_fd=parent, follow_symlinks=False
            )
            if (
                len(raw) > 16384
                or journal_version(before) != journal_version(current)
                or json.loads(raw) != packet
            ):
                raise AdapterError("Local initialization receipt changed", "access")
        verify_observer_readiness(native)
        # Original nonpublishing test can claim this namespace only once.
        descriptor = os.open(
            "native-" + packet["nonce"],
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        with os.fdopen(descriptor, "w") as stream:
            json.dump(native, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
        _LOCAL_INIT_PACKET = copy.deepcopy(packet)


_LOCAL_INIT_PACKET = None
_LOCAL_PRE_RESTORE_VERSION = None


def verify_observer_readiness(native):
    """Require the exact current-process RAM witness before consuming native claim."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        "http://127.0.0.1:8894/status", headers={"Cache-Control": "no-store"}
    )
    with opener.open(request, timeout=min(3, request_timeout())) as response:
        if response.status != 200:
            raise AdapterError("Native observer unavailable", "access")
        raw = response.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Native observer status oversized", "access")
    status = json.loads(raw)
    if (
        not isinstance(status, dict)
        or status.get("source_sha256") != source_file_digest()
        or status.get("test_sha256") != native["test_sha256"]
        or status.get("controller_sha256") != native["controller_sha256"]
        or status.get("pid") != os.getpid()
        or status.get("native_started") is not True
        or status.get("complete") is not False
        or status.get("errors") != []
    ):
        raise AdapterError("Exact current native RAM observer required", "access")
    return status


def hosted_qa_mode():
    """Recognize only a deliberately approved nonpublishing QA derivative."""
    return _HOSTED_QA_APPROVAL is not None


def validate_hosted_qa_approval():
    approval = _HOSTED_QA_APPROVAL
    if (
        not isinstance(approval, dict)
        or set(approval) != {"state", "state_sha256", "evidence"}
        or approval["state_sha256"] != "067f27f670b0fcaf197f6ea93f7502d50c105aa097eeecf045ca3f8636015643"
        or approval["state_sha256"] != canonical_digest(approval["state"])
        or not isinstance(approval["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/419#issuecomment-[1-9][0-9]*",
            approval["evidence"],
        )
    ):
        raise AdapterError("Exact Tartu QA seed approval remains OFF", "access")
    state = validate_budget(approval["state"])
    require_modern_state(state)
    if (
        state["blocked"]
        or state["in_flight"]
        or any(state[k]["pending"] for k in ("physical", "policy", "logical"))
        or merge_budget(state, reviewed_upgrade(copy.deepcopy(_HISTORICAL_MAX))) != state
    ):
        raise AdapterError("Reviewed QA seed omits retained publisher constraints", "access")
    return state


def verify_hosted_qa_identity():
    """Authenticate the one real QA job without giving it production authority."""
    validate_hosted_qa_approval()
    repo = "YouthOpps/data-pipeline"
    branch = "qa/tartu419-native"
    path = ".github/workflows/qa-tartu419-native.yml"
    expected = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": repo,
        "GITHUB_REF": "refs/heads/" + branch,
        "GITHUB_WORKFLOW_REF": f"{repo}/{path}@refs/heads/{branch}",
        "GITHUB_EVENT_NAME": "push",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_JOB": "native",
    }
    if (
        any(os.environ.get(key) != value for key, value in expected.items())
        or "DATA_SOURCE_TOKEN" in os.environ
        or "TARTU_PACING_ARTIFACT_PATH" in os.environ
        or _HOSTED_PRODUCER_APPROVAL is not None
        or _LOCAL_INITIALIZATION_APPROVAL is not None
        or _LOCAL_NATIVE_APPROVAL is not None
        or not callable(_HOSTED_QA_WITNESS)
    ):
        raise AdapterError("Exact isolated GitHub QA job required", "access")
    run_text, head = os.environ.get("GITHUB_RUN_ID", ""), os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[1-9][0-9]*", run_text) or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise AdapterError("Invalid GitHub QA identity", "access")
    run = int(run_text)
    evidence = _HOSTED_QA_APPROVAL["evidence"]
    comment = workflow_api("/issues/comments/" + evidence.split("#issuecomment-")[1])
    if (
        comment.get("html_url") != evidence
        or comment.get("author_association") not in {"OWNER", "MEMBER", "COLLABORATOR"}
        or f"Approved Tartu QA head {head}" not in comment.get("body", "").splitlines()
    ):
        raise AdapterError("Committed QA head has no exact maintainer grant", "access")
    status = workflow_api(f"/actions/runs/{run}/attempts/1")
    if (
        status.get("id") != run
        or status.get("run_attempt") != 1
        or status.get("event") != "push"
        or status.get("status") != "in_progress"
        or status.get("path") != path
        or status.get("head_sha") != head
        or status.get("head_branch") != branch
        or status.get("repository", {}).get("full_name") != repo
        or status.get("head_repository", {}).get("full_name") != repo
        or type(status.get("workflow_id")) is not int
        or status["workflow_id"] <= 0
        or status["workflow_id"] == _WORKFLOW_ID
    ):
        raise AdapterError("GitHub QA attempt verification failed", "access")
    workflow = status["workflow_id"]
    definition = workflow_api(f"/actions/workflows/{workflow}")
    inventory = workflow_api(f"/actions/workflows/{workflow}/runs?per_page=100")
    runs = inventory.get("workflow_runs")
    if (
        definition.get("id") != workflow or definition.get("path") != path
        or inventory.get("total_count") != 1 or not isinstance(runs, list) or len(runs) != 1
        or runs[0].get("id") != run or runs[0].get("run_attempt") != 1
        or runs[0].get("head_sha") != head
    ):
        raise AdapterError("QA workflow already has another attempt or owner", "access")
    jobs = workflow_api(f"/actions/runs/{run}/attempts/1/jobs?per_page=100")
    values = jobs.get("jobs")
    if (
        jobs.get("total_count") != 1 or not isinstance(values, list) or len(values) != 1
        or values[0].get("run_id") != run or values[0].get("run_attempt") != 1
        or values[0].get("head_sha") != head or values[0].get("status") != "in_progress"
        or values[0].get("name") != "native"
        or type(values[0].get("id")) is not int or values[0]["id"] <= 0
    ):
        raise AdapterError("Exact active QA job was not proved", "access")
    if source_revision_digest(head) != source_file_digest():
        raise AdapterError("Original QA source bytes differ from the GitHub head", "access")
    _HOSTED_QA_WITNESS()
    return {"workflow_id": workflow, "run_id": run, "run_attempt": 1,
            "head_sha": head, "ref": expected["GITHUB_REF"], "job_id": values[0]["id"]}


def validate_hosted_approval():
    approval = _HOSTED_PRODUCER_APPROVAL
    if not isinstance(approval, dict) or set(approval) != {
        "source",
        "state",
        "state_sha256",
        "native_evidence",
        "activation_evidence",
    }:
        raise AdapterError(
            "Exact reviewed native and producer approval remains OFF", "access"
        )
    if approval["source"] != SOURCE_ID or approval["state_sha256"] != canonical_digest(
        approval["state"]
    ):
        raise AdapterError("Hosted native state binding invalid", "access")
    for key in ("native_evidence", "activation_evidence"):
        if not isinstance(approval[key], str) or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/419#issuecomment-[0-9]+",
            approval[key],
        ):
            raise AdapterError(
                "Reviewed native and activation evidence required", "access"
            )
    state = validate_budget(approval["state"])
    require_modern_state(state)
    if (
        state["blocked"]
        or state["in_flight"]
        or any(state[k]["pending"] for k in ("physical", "policy", "logical"))
    ):
        raise AdapterError(
            "Stronger unexported native barrier forbids new allowance/receipt", "access"
        )
    if merge_budget(state, reviewed_upgrade(copy.deepcopy(_HISTORICAL_MAX))) != state:
        raise AdapterError("Hosted native approval omits full retained MAX", "access")
    return state


@contextlib.contextmanager
def receipt_export_deadline():
    """Reserve a bounded suffix of the original run, after clean execution exit."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _LAST_RUN_END is None or not _EXECUTION_CLOSED:
        raise AdapterError(
            "Original bounded run required for no-effect export", "access"
        )
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing receipt export alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    own_error = None
    try:
        arm_alarm(min(_LAST_RUN_END, time.monotonic() + EXPORT_TIMEOUT))
        yield
    except BaseException as error:
        own_error = error
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        raise
    finally:
        errors = []
        # Any own output/parent/close error revokes only this generation.
        if own_error is not None or _PUBLISHER_GATE_FAILED:
            try:
                invalidate_family_artifact()
            except BaseException as error:
                errors.append(error)
        for cleanup in (
            lambda: signal.setitimer(signal.ITIMER_REAL, 0),
            lambda: signal.signal(signal.SIGALRM, previous),
        ):
            try:
                cleanup()
            except BaseException as error:
                errors.append(error)
        if errors:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            # A failed alarm restoration cannot leave a true readiness receipt.
            try:
                invalidate_family_artifact()
            finally:
                raise AdapterError(
                    "No-effect export cleanup failed", "access"
                ) from errors[0]


def completed_attempt_inventory(run, *, include_current):
    """A no-effect rerun cannot conceal an earlier unexported attempt."""
    identity, attempt = run["id"], run["run_attempt"]
    if type(attempt) is not int or not 1 <= attempt <= 100:
        raise AdapterError("Attempt inventory exceeds reviewed bound", "access")
    source = family_source(run)
    revision = (run.get("head_sha"), run.get("workflow_id"))
    candidates = []
    for number in range(1, attempt + int(include_current)):
        value = (
            run
            if number == attempt
            else workflow_api(f"/actions/runs/{identity}/attempts/{number}")
        )
        _SCANNED_HISTORY[(identity, number)] = revision
        candidates.append(
            completed_identity(value, (identity, number, source), revision)
        )
    return candidates


_SEMANTIC_GUARD_SITES = frozenset(
    {
        ("partner_records", 242),
        ("partner_records", 274),
        ("enlight_records", 293),
        ("enlight_records", 299),
        ("enlight_records", 344),
        ("enlight_records", 312),
        ("date_deadline", 155),
        ("enlight_records", 321),
        ("current_rows", 186),
        ("current_rows", 170),
        ("current_rows", 189),
        ("current_rows", 179),
        ("enlight_records", 358),
        ("enlight_records", 323),
        ("partner_records", 278),
        ("enlight_records", 297),
    }
)

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
                    "message": "Run could not complete; durable status unavailable",
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
