"""Index minimal youth-vacancy facts from BMW Group’s public jobfinder."""

import argparse
import zipfile
import stat
import math
import ipaddress
import io
import base64
import contextlib
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

SOURCE_ID = "de-bmw-group"
SOURCE_URL = "https://www.bmwgroup.jobs/en.html"
WEBSITE_URL = "https://www.bmwgroup.jobs/"
LANGUAGE = "und"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "company"
ATTRIBUTION = "Source: BMW Group public jobfinder"
SCRIPT_URL = (
    "https://www.bmwgroup.jobs/etc.clientlibs/grpw-web/comps/jobf"
    "inder/clientlibs/jobfinder.min.js"
)
GLOBAL_TERMS_URL = "https://www.bmwgroup.jobs/en/general/terms-of-use.html"
GLOBAL_LEGAL_URL = "https://www.bmwgroup.jobs/en/general/legal-disclaimer.html"
GB_LEGAL_URL = "https://www.bmwgroup.jobs/gb/en/legal-notes.html"
_ALLOWED_HOSTS = {"www.bmwgroup.jobs"}
REVIEWED_POLICY_HASHES = {
    "https://www.bmwgroup.jobs/en/general/terms-of-use.html": (
        "d8e3f3fbe1d5b2df7d7a7be9243699336209d28c"
        "7af395373b5c6c20c5eaafb8"
    ),
    "https://www.bmwgroup.jobs/en/general/legal-disclaimer.html": (
        "a010b49cb36bf9c0724865c67b9bb8303b07e179"
        "165c744d58e384aee812cddd"
    ),
    "https://www.bmwgroup.jobs/gb/en/legal-notes.html": (
        "488c48814026c3a392811708ce4fb4880966740d"
        "988680798873ceeea36ff57e"
    ),
}
PAGE_SIZE = 50
MAX_INVENTORY = 5000
YOUTH_TYPES = {
    "APPRENTICESHIP": ("Apprenticeship", "training"),
    "INTERNSHIP": ("Internship", "internships"),
    "GRADUATE_JOB": ("Recent Graduate", "jobs"),
    "SUMMER_JOB": ("Talent Programs", "training"),
}
_ROBOTS = {}
_PAGE_COMPLETED = None
_COLLECTION_END = None

_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-bmw-group-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
COLLECTION_TIMEOUT = 1500
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "bmw-group-pacing-state"
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


import email.utils
import errno
import signal
import socket
import ssl

_LOCAL_WORKFLOW_ID = 385601295
_RUN_END = _PHASE_END = _PHASE_NAME = None
_ATTEMPT_HEADS = {}
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 30
ROBOTS_ORIGINS = {"https://www.bmwgroup.jobs"}
_CANCELLING = False

class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


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
                        if field not in ("deadline", "published_at"):
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
    validate_native_records(records)


def validate_native_records(records):
    labels = {value[0]: value[1] for value in YOUTH_TYPES.values()}
    for record in records:
        match = re.fullmatch(r"de-bmw-group-([1-9][0-9]{0,11})", record["id"])
        if (
            not match
            or record["url"]
            != "https://www.bmwgroup.jobs/en/jobfinder/job-description-copy."
            + match[1]
            + ".html"
        ):
            raise AdapterError(
                "Native BMW identity or original URL changed", "validate"
            )
        if (
            not record["tags"]
            or record["tags"][0] not in labels
            or record["categories"] != [labels[record["tags"][0]]]
        ):
            raise AdapterError(
                "Invalid native youth classification", "validate"
            )


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
        "display_name": "BMW Group",
        "source": SOURCE_ID,
        "name": "BMW Group — public youth vacancies",
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
                "validated BMW youth-vacancy records"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def clean(value):
    if not isinstance(value, str):
        raise AdapterError("Invalid publisher text", "parse")
    text = " ".join(unescape(value).split())
    if not text or re.search(r"<[^>]+>", text):
        raise AdapterError("Empty or nonliteral publisher text", "parse")
    return text


class Element:
    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def text(self):
        return " ".join(
            child if isinstance(child, str) else child.text()
            for child in self.children
        )

    def find(self, tag=None, css=None):
        result = []
        for child in self.children:
            if isinstance(child, Element):
                if (tag is None or child.tag == tag) and (
                    css is None or css in child.attrs.get("class", "").split()
                ):
                    result.append(child)
                result.extend(child.find(tag, css))
        return result


class NativeHTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
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

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Element(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def single(values, label):
    if len(values) != 1:
        raise AdapterError("Expected one native " + label, "parse")
    return values[0]


def home_configuration(text):
    root = NativeHTML(text).root
    path = single(root.find(css="grp-jobfinder"), "jobfinder").attrs.get(
        "data-jobfinder-path", ""
    )
    if not re.fullmatch(
        r"/_jcr_content/[A-Za-z0-9_/-]+\.jobfinder_table\.content\.html", path
    ):
        raise AdapterError("Publisher jobfinder path changed", "parse")
    types = {
        n.attrs.get("value")
        for n in root.find("input")
        if n.attrs.get("name") == "jobtype"
        and n.attrs.get("type") == "checkbox"
    }
    if types != set(YOUTH_TYPES) | {"STANDARD"}:
        raise AdapterError("Publisher career taxonomy changed", "parse")
    locations = {}
    for node in root.find("input"):
        attrs = node.attrs
        if attrs.get("name") != "location":
            continue
        match = re.fullmatch(r"([A-Z]{2})/(.+)", attrs.get("value", ""))
        if match:
            label = clean(attrs.get("aria-label")).casefold()
            locations.setdefault(label, set()).add(match[1])
    countries = {
        n.attrs.get("value")
        for n in root.find("input")
        if n.attrs.get("name") == "location"
        and re.fullmatch(r"[A-Z]{2}", n.attrs.get("value", ""))
    }
    area = single(root.find(css="grp-jobfinder-filter-area"), "global filter")
    if area.attrs.get("data-prefilter", ""):
        raise AdapterError("Global inventory acquired a regional prefilter",
                           "parse")
    if not countries:
        raise AdapterError("Publisher country taxonomy missing", "parse")
    # Countries are current filters, not permanent coverage obligations.
    # Unknown or ambiguous native location names retain unknown country flags.
    return SOURCE_URL.removesuffix(".html") + path, locations


def inventory_url(route, offset, country=None):
    filters = ["jobType_" + value for value in YOUTH_TYPES]
    if country:
        filters.append("location_" + country)
    return (
        route
        + "?"
        + urllib.parse.urlencode(
            {
                "filterSearch": ",".join(filters),
                "rowIndex": offset,
                "blockCount": PAGE_SIZE,
            }
        )
    )


def parse_inventory_page(text):
    root = NativeHTML(text).root
    table = single(root.find(css="grp-jobfinder__table"), "inventory table")
    total_text = table.attrs.get("data-counter", "")
    if (
        not re.fullmatch(r"[0-9]+", total_text)
        or not 0 <= int(total_text) <= MAX_INVENTORY
    ):
        raise AdapterError("Invalid or oversized complete inventory", "parse")
    rows = []
    for wrapper in table.find(css="grp-jobfinder__wrapper"):
        identifier = wrapper.attrs.get("data-job-id", "")
        if not re.fullmatch(r"[1-9][0-9]{0,11}", identifier):
            raise AdapterError("Invalid native job identity", "parse")
        cell = single(
            wrapper.find(css="grp-jobfinder-cell-refno"), "job facts"
        )
        link = single(
            wrapper.find("a", "grp-jobfinder__link-jobdescription"),
            "original link",
        )
        expected = "/en/jobfinder/job-description-copy." + identifier + ".html"
        if (
            link.attrs.get("href") != expected
            or clean(cell.text()) != identifier
        ):
            raise AdapterError("Conflicting native job identity/link", "parse")
        facts = tuple(
            clean(cell.attrs.get(key))
            for key in (
                "data-job-title",
                "data-job-location",
                "data-job-legal-entity",
                "data-job-field",
                "data-posting-date",
                "data-job-type",
            )
        )
        if facts[-1] not in {value[0] for value in YOUTH_TYPES.values()}:
            raise AdapterError("Non-youth item in filtered inventory", "parse")
        try:
            datetime.strptime(facts[4], "%Y%m%d")
        except ValueError:
            raise AdapterError(
                "Invalid native publication date", "parse"
            ) from None
        rows.append((identifier, expected) + facts)
    if len({r[0] for r in rows}) != len(rows):
        raise AdapterError("Duplicate native page identities", "parse")
    return int(total_text), rows


def check_inventory_budget(total, passes_left):
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    with locked_budget():
        budget = load_budget()
    interval = max(
        _PAGE_INTERVAL, budget["page_gap"], budget["interval"], 30
    )
    remaining = pages * passes_left - 1
    if (
        _COLLECTION_END is not None
        and remaining * interval + 120 > _COLLECTION_END - time.monotonic()
    ):
        raise AdapterError(
            "Complete traversal cannot fit retained pacing and phase budget",
            "access",
        )


def read_frontier(route, passes_left):
    offset, total, records = 0, None, {}
    while total is None or offset < total:
        observed, rows = parse_inventory_page(
            fetch_inventory_page(fetch_source, inventory_url(route, offset))
        )
        if total is None:
            total = observed
            if total <= 0:
                raise AdapterError("Empty real youth inventory", "parse")
            check_inventory_budget(total, passes_left)
        if observed != total or len(rows) != min(PAGE_SIZE, total - offset):
            raise AdapterError(
                "Publisher inventory moved or pagination truncated", "parse"
            )
        for row in rows:
            if row[0] in records:
                raise AdapterError(
                    "Repeated identity across inventory pages", "parse"
                )
            records[row[0]] = row
        offset += len(rows)
    if len(records) != total:
        raise AdapterError("Incomplete native inventory", "parse")
    return records


def policy_fingerprint(main):
    """Exclude only the screen-reader hint describing a new-window link."""
    for link in main.find("a"):
        if link.attrs.get("target") != "_blank":
            continue
        descriptions = link.attrs.get("aria-describedby", "").split()
        for hint in link.find("span", css="sr-only"):
            if (hint.attrs.get("id") in descriptions
                    and hint.children == ["(opens in a new window)"]):
                hint.children.clear()
    return hashlib.sha256(clean(main.text()).encode()).hexdigest()


def collect_body():
    ("Reconcile two complete native basic-facts frontiers "
     "without detail crawling.")
    global _COLLECTION_END
    if _COLLECTION_LOCK_FD is None:
        prepare_collection()
    _COLLECTION_END = _PHASE_END
    home = fetch_inventory_page(fetch_source, SOURCE_URL)
    route, locations = home_configuration(home)
    script = fetch_source(SCRIPT_URL)
    for literal in (
        "data-jobfinder-path",
        "blockCount",
        "rowIndex",
        "filterSearch",
        "applySearch",
        'this.requestUrl=document.location.href.split(".html")[0]+'
        "this.componentPath",
    ):
        if literal not in script:
            raise AdapterError(
                "Publisher public pagination client changed", "access"
            )
    # These policy pages were reviewed for the narrow factual index. Generic
    # copyright clauses remain unresolved; this is not a legal-clearance claim.
    for url in (GLOBAL_TERMS_URL, GLOBAL_LEGAL_URL, GB_LEGAL_URL):
        policy = NativeHTML(fetch_source(url)).root
        observed_digest = policy_fingerprint(single(policy.find("main"), "policy body"))
        if observed_digest != REVIEWED_POLICY_HASHES[url]:
            raise AdapterError(
                f"Reviewed publisher policy changed at {url} "
                f"(observed SHA-256 {observed_digest}); review required", "access"
            )
    first = read_frontier(route, 2)
    closing = read_frontier(route, 1)
    if first != closing:
        raise AdapterError(
            "Full closing inventory differs; no partial publication", "parse"
        )
    by_label = {value[0]: value[1] for value in YOUTH_TYPES.values()}
    records = []
    for identifier in sorted(first, key=int):
        _, path, title, location, institution, field, date, kind = first[
            identifier
        ]
        countries = locations.get(location.casefold(), set())
        record = make_record(
            title,
            "https://www.bmwgroup.jobs" + path,
            [by_label[kind]],
            published_at=datetime.strptime(date, "%Y%m%d").date().isoformat(),
            tags=[kind, field],
            host_countries=sorted(countries) if len(countries) == 1 else [],
            method="publisher-career-type",
            evidence=["Publisher career type: " + kind],
        )
        record.update(
            id=SOURCE_ID + "-" + identifier,
            location=location,
            institution=institution,
            summary_language="en",
            summary="BMW Group lists a "
            + kind.lower()
            + " opportunity in "
            + location
            + ". Check the original vacancy for application conditions "
            "and availability.",
        )
        records.append(record)
    validate_records(records)
    print(
        json.dumps(
            {
                "source": SOURCE_ID,
                "records": len(records),
                "full_frontiers": 2,
                "unknown_countries": sum(
                    not r["host_countries"] for r in records
                ),
                "scope": "complete-public-youth-basic-facts",
            }
        ),
        file=sys.stderr,
    )
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
        with phase("restore", 120):
            prepare_collection()
    with phase("snapshot", 60):
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
            with phase("publication", 60):
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
                with phase("failure snapshot", 30):
                    latest = read_snapshot()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                with phase("failure publication", 30):
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


def validate_legacy_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) != {
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
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
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


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


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
    return min(60 if publisher else 15, remaining)


def alarm_expired(signum, frame):
    global _CANCELLING
    _CANCELLING = True
    signal.setitimer(signal.ITIMER_REAL, 2)
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    if _CANCELLING:
        signal.setitimer(signal.ITIMER_REAL, 2)
        raise PhaseExpired()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextlib.contextmanager
def execution():
    global _RUN_END, _COLLECTION_LOCK_FD
    if _RUN_END is not None or _CANCELLING:
        raise AdapterError("Nested or cancelled execution refused", "access")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        try:
            # Keep cancellation armed through descriptor/response finalizers.
            # Successful cleanup also has a finite, nonrenewable sub-bound.
            if not _CANCELLING:
                arm_alarm(min(_RUN_END, time.monotonic() + 30))
            try:
                journal_finish()
            finally:
                try:
                    close_collection_lock()
                finally:
                    journal_finish_outputs()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
            _RUN_END = None


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
        style = re.sub(r"/\*.*?\*/", "", values.get("style") or "",
                       flags=re.S).lower()
        declarations, priorities = {}, {}
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                name = name.strip()
                important = bool(re.search(r"!\s*important\s*$", value))
                if priorities.get(name) and not important:
                    continue
                declarations[name] = re.sub(r"\s*!\s*important\s*$", "",
                                            value).strip()
                priorities[name] = important
        explicit_suppressed = ("hidden" in values or
                               (values.get("aria-hidden") or
                                "").strip().lower() == "true" or
                               declarations.get("display") == "none" or
                               declarations.get("visibility") == "hidden")
        if (tag == "head" and not self.head_seen and
                all(name == "html" and not suppressed and not visibility
                    for name, suppressed, visibility in self.stack)):
            self.head_seen = True
            self.head_allowed = not explicit_suppressed
        if (tag == "title" and self.head_allowed and self.stack and
                self.stack[-1][0] == "head" and not explicit_suppressed):
            self.title_depth = len(self.stack) + 1
        suppressed = (tag in {
            "head",
            "title",
            "script",
            "style",
            "template",
            "noscript",
            "footer",
            "nav",
            "aside",
        } or "hidden" in values or (values.get("aria-hidden") or
                                    "").strip().lower() == "true" or
                      declarations.get("display") == "none")
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
        if (self.title_depth is not None and
                len(self.stack) == self.title_depth and
                self.stack[-1][0] == "title"):
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
        ))


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path + ("&" if "?" in path else "?") +
            "_fresh=" + str(time.time_ns()),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
            },
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(("Workflow pacing state unavailable"),
                           ("access")) from None


def verify_local_workflow_pause(definition=None):
    """Require maintainer coordination before local publisher access."""
    if definition is None:
        definition = workflow_api("/actions/workflows/fetch-" + SOURCE_ID +
                                  ".yml")
    if (not isinstance(definition, dict) or
            type(definition.get("id")) is not int or
            definition["id"] != _LOCAL_WORKFLOW_ID or definition.get("path")
            != ".github/workflows/fetch-" + SOURCE_ID + ".yml" or
            definition.get("state") != "disabled_manually"):
        raise AdapterError(
            "Local collection requires its exact manually paused workflow",
            "access",
        )


def current_run_identity():
    if (os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline" or
            os.environ.get("GITHUB_REF") != "refs/heads/main"):
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
            f"?per_page=100&page={page}")
        count, jobs = result.get("total_count"), result.get("jobs")
        if (type(count) is not int or count <= 0 or
                not isinstance(jobs, list) or len(jobs) > 100 or
                total is not None and total != count):
            raise ValueError("Incomplete exact-attempt job inventory")
        total = count
        for job in jobs:
            if (not isinstance(job, dict) or type(job.get("id")) is not int or
                    job["id"] <= 0 or job["id"] in jobs_seen or
                    type(job.get("run_id")) is not int or
                    job["run_id"] != identity or
                    type(job.get("run_attempt")) is not int or
                    job["run_attempt"] != attempt or
                    job.get("head_sha") != head or
                    job.get("head_branch") != "main" or
                    job.get("status") != "completed"):
                raise ValueError("Untrusted exact-attempt job")
            jobs_seen.add(job["id"])
            value = datetime.fromisoformat(job["completed_at"].replace(
                "Z", "+00:00"))
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
        if (not source or type(identity) is not int or identity <= 0 or
                type(attempt) is not int or attempt <= 0 or
                run["status"] != "completed" or
                run["repository"]["full_name"] != "YouthOpps/data-pipeline" or
                run[("head_repository")][("full_name")]
                != ("YouthOpps/data-pipeline") or
                run["head_branch"] != "main" or
                not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")) or
                type(run.get("workflow_id")) is not int or
                run["workflow_id"] <= 0 or revision is not None and
            (run["head_sha"], run["workflow_id"]) != revision or expected and
            (identity, attempt, source) != expected):
            raise ValueError("Untrusted completed attempt")
        value = datetime.fromisoformat(run["run_started_at"].replace(
            "Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        finished = attempt_completion(identity, attempt, run["head_sha"])
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
        return finished, began, identity, attempt, source
    except Exception:
        raise AdapterError(("Untrusted completed family attempt"),
                           ("access")) from None


class InventoryRace(AdapterError):
    """A changed advertised total permits a bounded fresh history scan."""


def latest_family_run():
    """Restart only count-drift races, without reusing partial candidates."""
    for attempt in range(3):
        try:
            return scan_family_history()
        except InventoryRace:
            if attempt == 2:
                raise AdapterError("Workflow inventory remained unstable",
                                   "access") from None


def reviewed_preaccess_owner(ordered):
    """Restore the exact prior owner of the reviewed pre-access timeout (#494)."""
    gap_id, prior_id = 45965303770, 45923888649
    if (len(ordered) < 2 or ordered[-1][2:] != (gap_id, 1, SOURCE_ID)
            or ordered[-2][2:] != (prior_id, 1, SOURCE_ID)
            or _ATTEMPT_HEADS.get((gap_id, 1)) != "9e63b6308a211ae163194eb5dff7ebd1703f92ca"
            or _ATTEMPT_HEADS.get((prior_id, 1)) != "d0c2731027b50c6e85928883133c38cfe8153053"
            or len(ordered) > 2 and ordered[-2][0] == ordered[-3][0]):
        raise AdapterError("Reviewed BMW pre-access lineage differs", "access")
    jobs = workflow_api(f"/actions/runs/{gap_id}/attempts/1/jobs?per_page=100")
    expected_job = {"id": 140392445847, "run_id": gap_id, "run_attempt": 1,
                    "head_sha": _ATTEMPT_HEADS[(gap_id, 1)], "status": "completed",
                    "conclusion": "failure", "name": "collect", "head_branch": "main"}
    if (jobs.get("total_count") != 1 or len(jobs.get("jobs", [])) != 1
            or any(jobs["jobs"][0].get(k) != v for k, v in expected_job.items())):
        raise AdapterError("Reviewed BMW pre-access job differs", "access")
    empty = workflow_api(f"/actions/runs/{gap_id}/artifacts?per_page=100")
    if empty.get("total_count") != 0 or empty.get("artifacts") != []:
        raise AdapterError("Reviewed BMW pre-access artifacts differ", "access")
    return ordered[-2][2:]


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current_id, current_attempt = (current_run_identity() if hosted else
                                   (None, None))
    definition = workflow_api("/actions/workflows/fetch-" + SOURCE_ID + ".yml")
    expected_path = ".github/workflows/fetch-" + SOURCE_ID + ".yml"
    if (type(definition.get("id")) is not int or definition["id"] <= 0 or
            definition.get("path") != expected_path):
        raise AdapterError("Untrusted family workflow definition", "access")
    if not hosted:
        verify_local_workflow_pause(definition)
    candidates = []
    current_seen = False
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        route = "/actions/workflows/" + str(definition["id"]) + "/runs"
        result = workflow_api(route + f"?per_page=100&page={page}")
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (type(count) is not int or count < 0 or not isinstance(runs, list) or
                len(runs) > 100):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace(("Workflow inventory changed during restore"),
                                ("access"))
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            run_id = run.get("id")
            if type(run_id) is not int or run_id <= 0 or run_id in seen_ids:
                raise AdapterError("Invalid or duplicate workflow run identity",
                                   "access")
            seen_ids.add(run_id)
            source = family_source(run)
            if not source:
                raise AdapterError("Unexpected source in BMW workflow history",
                                   "access")
            if (run.get(("repository"), {}).get(
                ("full_name")) != ("YouthOpps/data-pipeline") or
                    run.get("head_repository", {}).get("full_name")
                    != ("YouthOpps/data-pipeline") or run.get(
                        ("head_branch")) != ("main")):
                raise AdapterError(("Workflow repository or branch mismatch"),
                                   ("access"))
            if run.get("workflow_id") != definition["id"]:
                raise AdapterError("Family workflow identity mismatch",
                                   "access")
            identity, attempt = run.get("id"), run.get("run_attempt")
            if (type(identity) is not int or identity <= 0 or
                    type(attempt) is not int or attempt <= 0):
                raise AdapterError("Invalid family run identity", "access")
            if hosted and identity == current_id:
                current_seen = True
                if (run.get("head_sha") != os.environ.get("GITHUB_SHA") or
                        not re.fullmatch(r"[0-9a-f]{40}", run.get(
                            "head_sha", "")) or
                        type(run.get("workflow_id")) is not int or
                        run["workflow_id"] <= 0 or run.get("path")
                        != ".github/workflows/fetch-" + SOURCE_ID + ".yml"):
                    raise AdapterError("Current workflow binding mismatch",
                                       "access")
                if attempt != current_attempt:
                    raise AdapterError(("Current workflow attempt mismatch"),
                                       ("access"))
                if current_attempt > 1:
                    prior = workflow_api((f"/actions/runs/{identity}/attempts/"
                                          f"{current_attempt - 1}"))
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, current_attempt - 1, source),
                            (run.get("head_sha"), run.get("workflow_id")),
                        ))
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
                        f"/actions/runs/{identity}/attempts/{attempt - 1}")
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, attempt - 1, source),
                            (run.get("head_sha"), run.get("workflow_id")),
                        ))
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError(("Workflow inventory count mismatch"),
                                   ("access"))
            if hosted and not current_seen:
                raise AdapterError("Current workflow missing from history",
                                   "access")
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(("Ambiguous family attempt chronology"),
                                   ("access"))
            if ordered[-1][2:] == (45965303770, 1, SOURCE_ID):
                return reviewed_preaccess_owner(ordered)
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


_OPENER = urllib.request.build_opener(NoRedirect)

# Source-local durable transport. The same publisher namespace/collection lock
# remains authoritative; no runtime import of another adapter is permitted.
_COLLECTION_LOCK_CONTEXT = None
_INFRASTRUCTURE_UNTIL = {}
_INITIAL_DIRECTORY_PROVEN = False
_INVENTORY_ACTIVE = False
_JOURNAL_EXPORTS = []
_JOURNAL_GENERATION = None
_JOURNAL_GUARDS = {}
_JOURNAL_LOCKS = {}
_JOURNAL_NEW = False
_JOURNAL_RETAINED_FD = _JOURNAL_RETAINED_ID = None
_JOURNAL_SIGNALS = []
_JOURNAL_WRITING = False
_LAST_KNOWN_STATE = None
_LEGACY_ACCESS_OBSERVATIONS = [
]
_PUBLISHER_HOSTS = {
    urllib.parse.urlsplit(url).hostname for url in ROBOTS_ORIGINS
}
_ROBOTS_CONTEXT = None
_STARTUP_QUIET_UNTIL = None
_STATE_PARENT_FD = _STATE_PARENT_ID = _STATE_PARENT_PATH = None
_VERIFIED_REMOTE_CREATE = None
_LOCAL_FIRST_ACCESS_CHECKED = False


def numeric(value):
    try:
        return (type(value) in (int, float) and math.isfinite(value) and
                value >= 0)
    except OverflowError:
        return False


def empty_budget(now):
    return {
        "schema":
            3,
        "family":
            FAMILY,
        "legacy_hold_history":
            "unlogged_fields_unknown",
        "legacy_access_history_unknown":
            True,
        "legacy_rate_history_unknown":
            True,
        "legacy_access_observations":
            copy.deepcopy(_LEGACY_ACCESS_OBSERVATIONS),
        "research_provenance": {},
        "starts": [],
        "interval":
            12.2,
        "until":
            now + 60,
        "page_gap":
            30,
        "completed":
            0,
        "inventory_completed":
            0,
        "blocked":
            False,
        "in_flight":
            False,
        "legacy_429_history":
            "unknown",
        "observed_429_since_transition":
            0,
        "rate_limit_until":
            0,
        "observed_at":
            now,
    }


def validate_budget(state):
    """Validate retained constraints without resetting unknown history."""
    if not isinstance(state, dict):
        raise AdapterError("Invalid publisher state", "access")
    expected = set(empty_budget(0))
    if (set(state) != expected or type(state["schema"]) is not int or
            state["schema"] != 3 or state["family"] != FAMILY or
            any(not numeric(state[k]) for k in (
                "interval",
                "until",
                "page_gap",
                "completed",
                "inventory_completed",
                "rate_limit_until",
                "observed_at",
            )) or state["interval"] < 12.2 or state["page_gap"] < 30 or
            state["legacy_429_history"] != "unknown" or
            state["legacy_hold_history"] != "unlogged_fields_unknown" or
            state["legacy_access_history_unknown"] is not True or
            state["legacy_rate_history_unknown"] is not True or
            state["legacy_access_observations"] != _LEGACY_ACCESS_OBSERVATIONS
            or not isinstance(state["research_provenance"], dict) or
            type(state["blocked"]) is not bool or
            type(state["in_flight"]) is not bool or
            type(state["observed_429_since_transition"]) is not int or
            not 0 <= state["observed_429_since_transition"] <= 100 or
            not isinstance(state["starts"], list) or
            len(state["starts"]) > 1000 or
            any(not numeric(t) or t > state["observed_at"]
                for t in state["starts"]) or
            state["starts"] != sorted(set(state["starts"])) or
            state["completed"] > state["observed_at"] or
            state["inventory_completed"] > state["observed_at"] or
            state["observed_at"] > time.time() + 1):
        raise AdapterError("Invalid retained publisher constraints", "access")
    epochs = (
        "until",
        "completed",
        "inventory_completed",
        "rate_limit_until",
        "observed_at",
    )
    if (any(not supported_epoch(state[key]) for key in epochs) or
            any(not supported_epoch(value) for value in state["starts"]) or
            not supported_epoch(time.time() + state["interval"]) or
            not supported_epoch(time.time() + state["page_gap"])):
        raise AdapterError("Unsupported retained clock magnitude", "access")
    return state


class JournalAuthorityChanged(AdapterError):
    """A replacement journal cannot inherit a stale descriptor's authority."""


def locked_journal_authority(check_generation=True):
    """Bind each operation to the original locked RW byte generation."""
    try:
        collection_lock_authority()
        if len(_JOURNAL_LOCKS) != 1:
            raise ValueError("Exactly one original journal lock required")
        descriptor, identity = next(iter(_JOURNAL_LOCKS.items()))
        guard = _JOURNAL_GUARDS[descriptor]
        for held in (descriptor, guard):
            info = journal_identity(held)
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("Retained journal identity changed")
        verify_retained_flock(descriptor, _STATE_PATH)
        if check_generation:
            journal_verify()
        return guard
    except BaseException as error:
        fail_publisher_cleanup()
        raise JournalAuthorityChanged(
            "Locked current publisher journal changed", "access") from error


def state_parent(directory=None):
    """Pin the verified parent and refuse replacement of its current path."""
    global _STATE_PARENT_FD, _STATE_PARENT_ID, _STATE_PARENT_PATH
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    directory = directory or (os.path.dirname(_STATE_PATH)
                              if _STATE_PATH else _STATE_PARENT_PATH)
    if not directory or not os.path.isabs(directory):
        raise AdapterError("Publisher state parent unavailable", "access")
    fresh = _STATE_PARENT_FD is None
    named_before = os.stat(directory, follow_symlinks=False)
    expected = (named_before.st_dev, named_before.st_ino)
    descriptor = (os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                          os.O_NOFOLLOW) if fresh else _STATE_PARENT_FD)
    try:
        info = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or not stat.S_ISDIR(current.st_mode) or
                current.st_uid != os.getuid() or current.st_mode & 0o077 or
                identity != expected or
                identity != (current.st_dev, current.st_ino) or not fresh and
            (directory != _STATE_PARENT_PATH or identity != _STATE_PARENT_ID)):
            raise AdapterError("Publisher state parent changed", "access")
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if fresh:
            journal_close_directory(descriptor, expected)
        raise
    if fresh:
        _STATE_PARENT_FD = descriptor
        _STATE_PARENT_ID = identity
        _STATE_PARENT_PATH = directory
    return descriptor


def validate_state_descriptor(descriptor, parent, name):
    """Bind a safe current journal inode within the retained parent."""
    info = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
        raise JournalAuthorityChanged("Publisher journal path changed",
                                      "access")
    validate_owned_output(info)
    state_parent(_STATE_PARENT_PATH)
    return info


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _INITIAL_DIRECTORY_PROVEN
    directory = os.path.join(tempfile.gettempdir(),
                             FAMILY + "-" + str(os.getuid()))
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
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o077):
        raise AdapterError("Unsafe publisher state directory", "access")
    state_parent(directory)
    _STATE_PATH = _STATE_LOCK = os.path.join(directory, "state.json")


def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        if _JOURNAL_RETAINED_FD is not None:
            journal_verify()
            fd = os.dup(_JOURNAL_RETAINED_FD)
            _JOURNAL_NEW = False
        elif (_INITIAL_DIRECTORY_PROVEN and not _RESTORED and
              _VERIFIED_REMOTE_CREATE is not None):
            try:
                fd = os.open(name,
                             flags | os.O_CREAT | os.O_EXCL,
                             0o600,
                             dir_fd=parent)
                _JOURNAL_NEW = True
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=parent)
                _JOURNAL_NEW = False
        else:
            fd = os.open(name, flags, dir_fd=parent)
    except BaseException:
        fail_publisher_cleanup()
        raise
    acquired = None
    try:
        acquired = os.fstat(fd)
        info = validate_state_descriptor(fd, parent, name)
    except BaseException:
        fail_publisher_cleanup()
        if acquired is not None:
            try:
                close_retained_descriptor(fd,
                                          (acquired.st_dev, acquired.st_ino))
            except BaseException:
                pass
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o077):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = validate_state_descriptor(fd, parent, name)
        return BudgetLock(fd, info)
    except BaseException:
        fail_publisher_cleanup()
        try:
            close_retained_descriptor(fd, (info.st_dev, info.st_ino))
        except BaseException:
            pass
        raise


def load_budget():
    """Read only the acknowledged current generation under its real lock."""
    locked_journal_authority()
    raw = journal_verify()
    if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
        return validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
    try:
        return decode_local_budget(raw)
    except (ValueError, TypeError) as error:
        fail_publisher_cleanup()
        raise AdapterError("Corrupt publisher journal", "access") from error


def write_budget(state):
    """Latch before cleanup; never invalidate a stronger external generation."""
    global _LAST_KNOWN_STATE
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        if _LAST_KNOWN_STATE is None:
            _LAST_KNOWN_STATE = copy.deepcopy(state)
        else:
            _LAST_KNOWN_STATE = merge_budget(_LAST_KNOWN_STATE, state)
        return write_budget_body(state)
    except BaseException as error:
        fail_publisher_cleanup()
        if not isinstance(error, JournalAuthorityChanged) or _JOURNAL_WRITING:
            with contextlib.suppress(BaseException):
                invalidate_failed_journal()
        raise


def retained_descriptor_info(descriptor, identity):
    """Validate original ownership without adopting a named replacement."""
    info = os.fstat(descriptor)
    if ((info.st_dev, info.st_ino) != identity or
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
            or info.st_nlink not in (0, 1)):
        raise AdapterError("Retained descriptor ownership changed", "access")
    return info


def close_retained_descriptor(descriptor, identity):
    """Never close a descriptor reused for an unrelated object."""
    retained_descriptor_info(descriptor, identity)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(descriptor)


def fail_publisher_cleanup():
    """Cleanup failure stays fatal across later collection/export entries."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    _STATE_TRUSTED = False
    _PUBLISHER_GATE_FAILED = True
    try:
        invalidate_family_artifact()
    except BaseException:
        # Unsafe objects remain untouched; the sticky gate still forbids export.
        pass


def collection_lock_authority():
    """Require the acquired lifecycle descriptors and current named inode."""
    if _COLLECTION_LOCK_FD is None:
        return
    try:
        descriptor, guard, identity, path = _COLLECTION_LOCK_CONTEXT
        if descriptor != _COLLECTION_LOCK_FD:
            raise ValueError("Lifecycle descriptor changed")
        for held in (descriptor, guard):
            retained_descriptor_info(held, identity)
            verify_retained_flock(held, path)
        current = os.stat(path, follow_symlinks=False)
        validate_owned_output(current)
        if (current.st_dev, current.st_ino) != identity:
            raise ValueError("Lifecycle path changed")
    except BaseException as error:
        fail_publisher_cleanup()
        raise AdapterError("Shared lifecycle lock changed", "access") from error


def close_collection_lock():
    """Release only acquired lifecycle FDs and preserve a primary error."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT
    primary = sys.exc_info()[0] is not None
    context = _COLLECTION_LOCK_CONTEXT
    descriptor = _COLLECTION_LOCK_FD
    _COLLECTION_LOCK_FD = _COLLECTION_LOCK_CONTEXT = None
    if descriptor is None:
        return
    failure = None
    if context is None or context[0] != descriptor:
        failure = AdapterError("Lifecycle ownership unavailable", "access")
    else:
        original, guard, identity, path = context
        safe = []
        for held in (guard, original):
            try:
                retained_descriptor_info(held, identity)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                safe.append(held)
            except BaseException as error:
                failure = failure or error
        for held in safe:
            try:
                close_retained_descriptor(held, identity)
            except BaseException as error:
                failure = failure or error
    if failure is not None:
        fail_publisher_cleanup()
        if not primary:
            raise failure


class BudgetLock:
    """Keep one original RW authority across scopes and verified closes."""

    def __init__(self, descriptor, info):
        self.descriptor = descriptor
        self.identity = (info.st_dev, info.st_ino)
        self.guard = os.dup(self.descriptor)
        _JOURNAL_LOCKS[self.descriptor] = self.identity
        _JOURNAL_GUARDS[self.descriptor] = self.guard
        try:
            journal_retain(self.guard)
            locked_journal_authority()
        except BaseException:
            _JOURNAL_LOCKS.pop(self.descriptor, None)
            _JOURNAL_GUARDS.pop(self.descriptor, None)
            with contextlib.suppress(BaseException):
                close_retained_descriptor(self.guard, self.identity)
            raise

    def fileno(self):
        locked_journal_authority()
        return self.descriptor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        primary = sys.exc_info()[0] is not None
        if self.descriptor is None:
            return
        descriptor, guard = self.descriptor, self.guard
        failure = None
        try:
            if not _PUBLISHER_GATE_FAILED:
                locked_journal_authority()
        except BaseException as error:
            failure = error
        self.descriptor = self.guard = None
        _JOURNAL_LOCKS.pop(descriptor, None)
        _JOURNAL_GUARDS.pop(descriptor, None)
        for held in (guard, descriptor):
            try:
                close_retained_descriptor(held, self.identity)
            except BaseException as error:
                fail_publisher_cleanup()
                failure = failure or error
        try:
            if _JOURNAL_RETAINED_FD is not None:
                journal_identity(_JOURNAL_RETAINED_FD)
                fcntl.flock(_JOURNAL_RETAINED_FD, fcntl.LOCK_UN)
        except BaseException as error:
            fail_publisher_cleanup()
            failure = failure or error
        if failure is not None and not primary:
            raise failure


def invalidate_failed_journal():
    """Invalidate only a verified own commit, never an external generation."""
    if not _JOURNAL_LOCKS:
        raise AdapterError("Original journal lock unavailable", "access")
    journal_verify()
    locked_journal_authority()
    journal_identity(_JOURNAL_RETAINED_FD)
    os.ftruncate(_JOURNAL_RETAINED_FD, 0)
    journal_acknowledge(b"")
    locked_journal_authority()
    os.fsync(_JOURNAL_RETAINED_FD)
    journal_acknowledge(b"")


def write_budget_body(state):
    """Acknowledge each own mutation before allowing another mutation."""
    global _INITIAL_DIRECTORY_PROVEN, _JOURNAL_WRITING
    state = validate_budget(state)
    payload = json.dumps(state, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    if len(payload) > 16384:
        raise AdapterError("Oversized publisher journal", "access")
    locked_journal_authority()
    _JOURNAL_WRITING = True
    os.ftruncate(_JOURNAL_RETAINED_FD, 0)
    journal_acknowledge(b"")
    locked_journal_authority()
    os.fsync(_JOURNAL_RETAINED_FD)
    journal_acknowledge(b"")
    offset = 0
    while offset < len(payload):
        locked_journal_authority()
        count = os.pwrite(_JOURNAL_RETAINED_FD, payload[offset:], offset)
        if type(count) is not int or not 0 < count <= len(payload) - offset:
            raise AdapterError("Incomplete publisher journal write", "access")
        offset += count
        journal_acknowledge(payload[:offset])
    locked_journal_authority()
    os.fsync(_JOURNAL_RETAINED_FD)
    journal_acknowledge(payload)
    locked_journal_authority()
    _JOURNAL_WRITING = False
    _INITIAL_DIRECTORY_PROVEN = False


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
    if (left["research_provenance"] and right["research_provenance"] and
            left["research_provenance"] != right["research_provenance"]):
        raise AdapterError("Research provenance differs across state", "access")
    result["research_provenance"] = (left["research_provenance"] or
                                     right["research_provenance"])
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["legacy_access_history_unknown"] = (
        left["legacy_access_history_unknown"] or
        right["legacy_access_history_unknown"])
    result["blocked"] = left["blocked"] or right["blocked"]
    result["in_flight"] = left["in_flight"] or right["in_flight"]
    return validate_budget(result)


def remaining():
    check_deadline()
    return min(_RUN_END - EXPORT_TIMEOUT, _PHASE_END) - time.monotonic()


def pace(interval=12.2):
    """Hold the physical journal lock through the response completion."""
    lock = locked_budget()
    try:
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError("Unsettled publisher request retained", "access")
        if state["blocked"]:
            raise AdapterError("Retained actual publisher refusal", "access")
        if state["observed_429_since_transition"] >= 100:
            raise AdapterError("Unsupported retained429 history; no traffic",
                               "access")
        state["interval"] = max(state["interval"], interval, 12.2)
        if state["rate_limit_until"] > time.time():
            raise AdapterError("Retained publisher rate-limit embargo",
                               "access", 429)
        while True:
            now = time.time()
            recent = [t for t in state["starts"] if now - t < 60]
            ready = max(
                state["until"],
                state["completed"] + state["interval"],
                recent[-1] + state["interval"] if recent else now,
                recent[-5] + 60 if len(recent) >= 5 else now,
            )
            if ready - now + 120 >= remaining():
                raise AdapterError(
                    "Retained pacing exceeds remaining collection budget",
                    "access",
                )
            if ready <= now:
                state["starts"] = recent + [now]
                state["observed_at"] = now
                state["in_flight"] = True
                write_budget(state)
                return lock, state
            time.sleep(min(ready - now, 30))
    except BaseException:
        lock.close()
        raise


def retry_embargo(value):
    """Retain supported future timing; never discard positive overflow."""
    if value is None:
        return 0
    if not isinstance(value, str):
        raise AdapterError("Unsupported Retry-After field", "access")
    value = value.strip()
    numeric_form = re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
    if numeric_form:
        try:
            delay = float(value)
            until = time.time() + delay
            if not numeric(delay) or not supported_epoch(until):
                raise ValueError("Unsupported positive duration")
        except (ValueError, OverflowError, OSError):
            raise AdapterError("Unsupported positive Retry-After duration",
                               "access") from None
        return until if delay > 0 and until > time.time() else 0
    # Numeric-looking positive extensions cannot establish an unbounded grant.
    if (re.fullmatch(r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
                     r"(?:[eE][+-]?[0-9]+)?", value)
            or value.lower() in {"inf", "+inf", "infinity", "+infinity"}):
        raise AdapterError("Unsupported positive Retry-After syntax", "access")
    try:
        date = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        # An explicitly out-of-range future year is restrictive but cannot
        # be represented in this runtime's durable timestamp schema.
        year = re.search(r"\b[0-9]{1,2} [A-Za-z]{3} ([0-9]{5,})\b", value)
        if year:
            raise AdapterError("Unsupported Retry-After date", "access")
        return 0
    if date.utcoffset() is None:
        return 0
    try:
        until = date.timestamp()
        if not supported_epoch(until):
            raise ValueError("Unsupported date timestamp")
    except (ValueError, OverflowError, OSError):
        raise AdapterError("Unsupported Retry-After date", "access") from None
    return until if until > time.time() else 0


def retry_after_constraints(headers):
    """Inspect every observed field, retaining MAX even alongside overflow."""
    if hasattr(headers, "get_all"):
        values = headers.get_all("Retry-After") or []
    else:
        values = [value for key, value in headers.items()
                  if key.lower() == "retry-after"]
    maximum, unsupported = 0, False
    for value in values:
        # Combined numeric fields contain no date comma ambiguity.
        parts = (value.split(",") if isinstance(value, str) and re.fullmatch(
            r"\s*[0-9]+(?:\.[0-9]+)?"
            r"(?:\s*,\s*[0-9]+(?:\.[0-9]+)?)+\s*", value) else [value])
        if (len(parts) == 1 and isinstance(value, str) and "," in value
                and not re.fullmatch(r"[A-Za-z]{3,9},[^,]+", value.strip())):
            # The email date parser accepts prefixes. Do not silently lose a
            # second combined field or guess how ambiguous dates were joined.
            unsupported = True
            continue
        for part in parts:
            try:
                maximum = max(maximum, retry_embargo(part))
            except AdapterError:
                unsupported = True
    return maximum, unsupported


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
    _INFRASTRUCTURE_UNTIL[host] = max(_INFRASTRUCTURE_UNTIL.get(host, 0),
                                      retry_embargo(value))


def inventory_guard():
    """Each physical inventory retry/redirect retains the completion gap."""
    with locked_budget():
        state = load_budget()
    while True:
        wait = state["inventory_completed"] + state["page_gap"] - time.time()
        if wait <= 0:
            return
        if wait + 30 >= remaining():
            raise AdapterError("Page guard exceeds remaining collection budget",
                               "access")
        time.sleep(min(wait, 30))


def source_file_digest():
    """Hash this standalone implementation without retaining its contents."""
    descriptor = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        current = os.stat(__file__, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or info.st_mode & 0o022 or
            (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise AdapterError("Unsafe implementation file", "access")
        content = stream.read(5_000_001)
        current = os.stat(__file__, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Implementation file changed", "access")
    if len(content) > 5_000_000:
        raise AdapterError("Source implementation exceeds reviewed bound",
                           "access")
    return hashlib.sha256(content).hexdigest()


@contextlib.contextmanager
def owned_artifact_directory(path):
    """Pin output parent operations and close only that original directory."""
    root, before = checked_artifact_path(path)
    descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        held = os.fstat(descriptor)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Artifact directory changed", "access")
        verify_artifact_directory(root, descriptor)
        yield root, descriptor
    finally:
        journal_close_directory(descriptor, (before.st_dev, before.st_ino))


def verify_artifact_directory(root, descriptor):
    """Reject replacement parents without following their contents."""
    held = os.fstat(descriptor)
    current = os.stat(root, follow_symlinks=False)
    if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.getuid() or
            current.st_mode & 0o022 or
        (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
        raise AdapterError("Artifact directory changed", "access")


def owned_artifact_info(root, directory):
    """Inspect only the current safely owned artifact in the pinned parent."""
    verify_artifact_directory(root, directory)
    try:
        info = os.stat("pacing-state.json",
                       dir_fd=directory,
                       follow_symlinks=False)
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
    """Retain verified readiness authority through all outer cleanup."""
    if value == "true":
        if _PUBLISHER_GATE_FAILED or not _STATE_TRUSTED:
            raise AdapterError("Untrusted publisher cannot signal ready",
                               "access")
        journal_verify()
    path = os.environ["GITHUB_OUTPUT"]
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    with journal_output(descriptor, path, _JOURNAL_SIGNALS) as (output, _):
        output.write(f"pacing_ready={value}\n")
        output.flush()
        os.fsync(output.fileno())


def invalidate_family_artifact():
    """Never reopen a newer output pathname during failure cleanup."""
    journal_invalidate_outputs()


def validate_owned_output(info):
    """Refuse unsafe output before reading or modifying it."""
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o077):
        raise AdapterError("Unsafe owned artifact output", "access")


def checked_artifact_path(path):
    """Validate this attempt's private output directory and basename."""
    configured = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (os.path.dirname(os.path.abspath(path)) != root or
            os.path.basename(path) != "pacing-state.json"):
        raise AdapterError("Unsafe artifact output path", "access")
    info = os.stat(root, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o022):
        raise AdapterError("Unsafe artifact output directory", "access")
    return root, info


def export_family_artifact():
    """Atomically export trusted state; invalidate owned output on failure."""
    path = os.environ.get("BMW_GROUP_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    with owned_artifact_directory(path) as (root, directory):
        prior = owned_artifact_info(root, directory)
        temporary = None
        temporary_info = None
        try:
            run_id, attempt = current_run_identity()
            with locked_budget():
                state = load_budget()
            if state["in_flight"]:
                raise AdapterError("Unsettled state cannot be exported",
                                   "access")
            envelope = {
                "schema":
                    1,
                "repository":
                    "YouthOpps/data-pipeline",
                "run_id":
                    run_id,
                "run_attempt":
                    attempt,
                "source":
                    SOURCE_ID,
                "head_sha":
                    os.environ.get("GITHUB_SHA"),
                "adapter_sha256":
                    source_file_digest(),
                "state_sha256":
                    hashlib.sha256(
                        json.dumps(state, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest(),
                "state":
                    state,
            }
            if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
                raise AdapterError("Invalid artifact source revision", "access")
            payload = json.dumps(envelope).encode("utf-8")
            temporary = ".owned-pacing-" + os.urandom(16).hex()
            verify_artifact_directory(root, directory)
            descriptor = os.open(
                temporary,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            temporary_info = os.fstat(descriptor)
            with journal_output(descriptor, os.path.join(root, temporary),
                                _JOURNAL_EXPORTS) as (stream, pin):
                stream.write(payload.decode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            journal_verify()
            same_owned_artifact(root, directory, prior)
            os.replace(
                temporary,
                "pacing-state.json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary = None
            pin["name"] = "pacing-state.json"
            same_owned_artifact(root, directory, temporary_info)
            journal_verify_output_bytes(pin, payload)
            journal_verify()
            workflow_output_signal("true")
        except BaseException:
            fail_publisher_cleanup()
            raise
        finally:
            primary = sys.exc_info()[0] is not None
            if temporary is not None and temporary_info is not None:
                try:
                    verify_artifact_directory(root, directory)
                    current = os.stat(temporary,
                                      dir_fd=directory,
                                      follow_symlinks=False)
                    validate_owned_output(current)
                    if (current.st_dev, current.st_ino) != (
                            temporary_info.st_dev,
                            temporary_info.st_ino,
                    ):
                        raise AdapterError("Temporary artifact changed",
                                           "access")
                    os.unlink(temporary, dir_fd=directory)
                except BaseException:
                    fail_publisher_cleanup()
                    if not primary:
                        raise


def robots_response_text(headers, body):
    """An HTTP200 policy needs one explicit plaintext representation."""
    if hasattr(headers, "get_all"):
        types = headers.get_all("Content-Type") or []
    else:
        types = [value for key, value in headers.items()
                 if key.lower() == "content-type"]
    if (len(types) != 1 or not isinstance(types[0], str)
            or types[0].split(";", 1)[0].strip().lower() != "text/plain"):
        raise AdapterError("Unsupported robots representation", "access")
    text = body.decode(headers.get_content_charset() or "utf-8", "strict")
    meaningful = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    if re.search(r"<!--|-->|<!doctype\b|</?[A-Za-z][^>]*>", meaningful, re.I):
        raise AdapterError("HTML is not a robots policy", "access")
    return text


def apply_robots_policy(text, url, state=None):
    """Keep policy application pending until restrictive state is durable."""
    if state is None:
        with locked_budget() as lock:
            current = load_budget()
            if current["in_flight"]:
                raise AdapterError("Unsettled publisher transaction", "access")
            current["in_flight"] = True
            write_budget(current)
            try:
                delay = apply_robots_policy(text, url, current)
            except RobotsDisallow:
                current["in_flight"] = False
                write_budget(current)
                raise
            current["in_flight"] = False
            write_budget(current)
            return delay
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


def physical_request_bytes(
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
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if type(byte_limit) is not int or not 0 < byte_limit <= 30_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    purpose_target = urllib.parse.urlsplit(url)
    if robots and (purpose_target.path != "/robots.txt" or
                   purpose_target.query or purpose_target.fragment):
        raise AdapterError("Robots purpose requires literal robots.txt",
                           "access")
    host = (urllib.parse.urlsplit(url).hostname or "").lower().rstrip(".")
    publisher = host in _PUBLISHER_HOSTS
    if publisher:
        public_url(url)
    if not publisher and not infrastructure:
        raise AdapterError("Explicit infrastructure transport required",
                           "access")
    if infrastructure:
        infrastructure_url(url)
        if host != "api.github.com":
            if method != "GET" or payload is not None:
                raise AdapterError("Signed artifact requires public GET",
                                   "access")
            headers = {key: value for key, value in (headers or {}).items()
                       if key.lower() != "authorization"}
        if publisher:
            raise AdapterError("Publisher cannot use metadata purpose",
                               "access")
        infrastructure_pace(url)
    if publisher and (not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        raise AdapterError("Trusted publisher state required before access",
                           "access")
    if publisher:
        collection_lock_authority()
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    if publisher:
        wait_startup_quiet()
    lock, state = pace(interval) if publisher else (None, None)
    attempted = response_received = response_constraints_saved = False
    retryable_connection_failure = False
    response_exit_completed = False

    @contextlib.contextmanager
    def assessed_response_scope():
        # Only a completed __exit__ proves response closure. An intentional
        # assessed stop is distinct from an exception raised by closure itself.
        nonlocal response_exit_completed
        response.__enter__()
        try:
            yield
        except BaseException:
            suppressed = response.__exit__(*sys.exc_info())
            response_exit_completed = True
            if not suppressed:
                raise
        else:
            response.__exit__(None, None, None)
            response_exit_completed = True

    try:
        if publisher:
            collection_lock_authority()
            verify_local_publisher_start()
            locked_journal_authority()
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                **(headers or {}), "User-Agent": _USER_AGENT
            },
        )
        if publisher:
            print(
                json.dumps({
                    "event": "request_start",
                    "url": url,
                    "time": time.time(),
                    "user_agent": request.get_header("User-agent"),
                }),
                file=sys.stderr,
                flush=True,
            )
        timeout = request_timeout(publisher)
        if publisher:
            collection_lock_authority()
            locked_journal_authority()
        try:
            if publisher and (_STARTUP_QUIET_UNTIL is None or
                              time.monotonic() < _STARTUP_QUIET_UNTIL):
                raise AdapterError("Publisher startup quiet changed", "access")
            attempted = True
            response = _OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            if publisher and isinstance(reason, ssl.SSLError):
                raise AdapterError(
                    "Publisher TLS verification failed; pending retained",
                    "access",
                ) from error
            if publisher and not (isinstance(reason,
                                             (ConnectionError, TimeoutError)) or
                                  isinstance(reason, socket.gaierror) and
                                  reason.errno == socket.EAI_AGAIN or
                                  isinstance(reason, OSError) and
                                  reason.errno in (
                                      errno.ECONNABORTED,
                                      errno.ECONNREFUSED,
                                      errno.ECONNRESET,
                                      errno.EHOSTDOWN,
                                      errno.EHOSTUNREACH,
                                      errno.ENETDOWN,
                                      errno.ENETUNREACH,
                                      errno.EPIPE,
                                      errno.ETIMEDOUT,
                                  )):
                raise AdapterError(
                    "Unclassified connection failure; pending retained",
                    "access",
                ) from error
            retryable_connection_failure = True
            raise
        response_received = True
        with assessed_response_scope():
            status = response.status
            if infrastructure:
                retain_infrastructure_retry(url,
                                            response.headers.get("Retry-After"))
            if publisher:
                if status == 429:
                    strikes = state["observed_429_since_transition"] + 1
                    state["observed_429_since_transition"] = strikes
                    state["interval"] = max(state["interval"] * 2,
                                            12.2 * 2**strikes)
                    state["page_gap"] = max(state["page_gap"] * 2,
                                            30 * 2**strikes)
                    state["until"] = max(state["until"],
                                         time.time() + 1800 * 2**(strikes - 1))
                    state["rate_limit_until"] = max(state["rate_limit_until"],
                                                    state["until"])
                if status in (401, 403, 451) and not robots:
                    state["blocked"] = True
                # Known status restrictions are durable before parsing any
                # possibly unsupported or duplicated Retry-After field.
                state["observed_at"] = time.time()
                write_budget(state)
                embargo, unsupported = retry_after_constraints(response.headers)
                if embargo > state["until"]:
                    state["until"] = embargo
                    if status == 429:
                        state["rate_limit_until"] = max(
                            state["rate_limit_until"], embargo)
                    state["observed_at"] = time.time()
                    write_budget(state)
                if unsupported:
                    raise AdapterError(
                        "Unsupported Retry-After; pending state retained",
                        "access", status)
            if publisher and status == 429:
                byte_limit = min(byte_limit, 65536)
            declared = response.headers.get("Content-Length")
            if declared and (not declared.isdigit() or
                             int(declared) > byte_limit):
                raise AdapterError("Oversized or invalid response length",
                                   "fetch")
            if (response.headers.get("Content-Encoding", "identity")
                    != "identity"):
                raise AdapterError("Unsupported response content encoding",
                                   "fetch")
            try:
                body = response.read(byte_limit + 1)
            except Exception as error:
                if publisher:
                    raise AdapterError(
                        "Publisher body incomplete; pending state retained",
                        "access",
                        status,
                    ) from error
                raise
            if (len(body) > byte_limit or
                    declared and len(body) != int(declared)):
                raise AdapterError("Response exceeds native byte bound",
                                   "fetch")
            challenge = (publisher and
                         not (robots and 400 <= status < 500 and status != 429)
                         and visible_access_challenge(body, status == 429))
            if challenge:
                response_constraints_saved = True
                if not robots or status == 429:
                    state["blocked"] = True
                raise AdapterError("Visible publisher verification or refusal",
                                   "access")
            if publisher and not (robots and status == 200):
                response_constraints_saved = True
            if publisher and robots and status == 200:
                response_constraints_saved = False
                target = (_ROBOTS_CONTEXT["target"]
                          if _ROBOTS_CONTEXT is not None else SOURCE_URL)
                text = robots_response_text(response.headers, body)
                try:
                    delay = apply_robots_policy(text, target, state)
                except RobotsDisallow:
                    response_constraints_saved = True
                    raise
                if _ROBOTS_CONTEXT is not None:
                    _ROBOTS_CONTEXT["delay"] = delay
                response_constraints_saved = True
            if publisher and status == 429:
                raise AdapterError(
                    "Publisher429; assessed durable cooldown retained",
                    "access", 429)
            return status, response.headers, body
    except BaseException as error:
        if publisher and response_received and not response_exit_completed:
            response_constraints_saved = False
            if isinstance(error,
                          (urllib.error.URLError, TimeoutError, OSError)):
                raise AdapterError(
                    "Received publisher response failed; no connection retry",
                    "access",
                ) from error
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if (attempted and not _PUBLISHER_GATE_FAILED and
                    (response_constraints_saved or
                     not response_received and retryable_connection_failure)):
                    state["in_flight"] = False
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = state["completed"]
                try:
                    write_budget(state)
                    if state["in_flight"]:
                        _STATE_TRUSTED = False
                        _PUBLISHER_GATE_FAILED = True
                except BaseException:
                    _STATE_TRUSTED = False
                    _PUBLISHER_GATE_FAILED = True
                    if not primary:
                        raise
                print(
                    json.dumps({
                        "event": "request_complete",
                        "url": url,
                        "time": time.time(),
                    }),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                lock.close()


class RobotsDisallow(AdapterError):
    """A genuine applicable REP restriction, retained independently."""


def selected_robots_policies(groups):
    """A legacy grant cannot displace current identity's wildcard policy."""
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [
        g for g in groups
        if any(token not in {"*", "youthopps"} and token in agent
               for token in g["agents"])
    ]
    wildcard = [g for g in groups if "*" in g["agents"]]
    legacy = [g for g in groups if "youthopps" in g["agents"]]
    # Preserve the original BMW per-entry protective checks as well as
    # the independent legacy product policy. A legacy grant cannot replace
    # the current product's otherwise-applicable wildcard restriction.
    return [[group] for group in (current or wildcard) + legacy]


def robots_groups(text):
    """Retain native allow/disallow rules with raw selected timing fields."""
    groups = []
    group = {"agents": [], "rules": [], "timing": []}
    has_directive = False
    for original in text.splitlines():
        key, separator, value = original.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if has_directive:
                groups.append(group)
                group = {"agents": [], "rules": [], "timing": []}
                has_directive = False
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
            has_directive = True
        elif key in ("crawl-delay", "request-rate"):
            group["timing"].append((key, value))
            has_directive = True
    groups.append(group)
    directives = [
        line.split("#", 1)[0].strip().lower()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (directives and not any(g["agents"] for g in groups) and
            not all(line.startswith("sitemap:") for line in directives)):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    return groups


def robots_policy(text, url):
    """Use the original native rule matcher and strict selected numeric MAX."""
    parsed = urllib.parse.urlsplit(url)
    policies = selected_robots_policies(robots_groups(text))
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    delay = 12.2
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    for selected in policies:
        matches = []
        for group in selected:
            for directive, pattern in group["rules"]:
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
            for key, value in group["timing"]:
                try:
                    if key == "crawl-delay":
                        if not re.fullmatch(decimal, value):
                            raise ValueError()
                        seconds = float(value)
                    else:
                        match = re.fullmatch(
                            r"([0-9]+)\s*/\s*(" + decimal + r")", value)
                        if not match:
                            raise ValueError()
                        count, period = int(match[1]), float(match[2])
                        if count <= 0 or not numeric(period) or period <= 0:
                            raise ValueError()
                        seconds = period / count + 0.01
                    if not numeric(seconds):
                        raise ValueError()
                    datetime.fromtimestamp(time.time() + seconds, timezone.utc)
                    delay = max(delay, seconds)
                except (ValueError, OverflowError, OSError):
                    raise AdapterError(
                        "Unsupported applicable robots timing; "
                        "pending retained",
                        "access",
                    ) from None
        if matches and not max(matches)[1]:
            raise RobotsDisallow("Robots policy disallows this source endpoint",
                                 "access")
    return delay


def check_robots(url):
    """Apply every cached target policy within its durable pending scope."""
    global _ROBOTS_CONTEXT
    parsed = urllib.parse.urlsplit(public_url(url))
    if (parsed.path == "/robots.txt" and not parsed.query and
            not parsed.fragment):
        return 12.2
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin in _ROBOTS:
        text = _ROBOTS[origin]
        return 12.2 if text is None else apply_robots_policy(text, url)
    previous = _ROBOTS_CONTEXT
    context = {"target": url, "delay": None}
    _ROBOTS_CONTEXT = context
    try:
        status, headers, body = fetch_source(origin + "/robots.txt",
                                             policy_response=True)
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
            return 12.2
        if status != 200:
            raise AdapterError("Required robots policy unavailable", "access",
                               status)
        text = body.decode(headers.get_content_charset() or "utf-8", "strict")
        _ROBOTS[origin] = text
        return (context["delay"] if context["delay"] is not None else
                apply_robots_policy(text, url))
    finally:
        _ROBOTS_CONTEXT = previous


def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True


def journal_metadata(info):
    """Identify the complete acknowledged metadata generation."""
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


def journal_identity(descriptor):
    """Keep the original RW inode beneath its original verified parent."""
    parent = state_parent()
    info = validate_state_descriptor(descriptor, parent,
                                     os.path.basename(_STATE_PATH))
    if ((info.st_dev, info.st_ino) != _JOURNAL_RETAINED_ID or
            stat.S_IMODE(info.st_mode) != 0o600 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR):
        raise JournalAuthorityChanged("Original RW journal changed", "access")
    return info


def journal_snapshot():
    """Read the existing16KiB contract without adopting a post-read change."""
    before = journal_identity(_JOURNAL_RETAINED_FD)
    chunks = []
    offset = 0
    while offset <= 16384:
        block = os.pread(_JOURNAL_RETAINED_FD, 16385 - offset, offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    raw = b"".join(chunks)
    after = journal_identity(_JOURNAL_RETAINED_FD)
    if (len(raw) > 16384 or len(raw) != after.st_size or
            journal_metadata(before) != journal_metadata(after)):
        raise JournalAuthorityChanged("Journal read generation changed",
                                      "access")
    return (*journal_metadata(after), hashlib.sha256(raw).hexdigest()), raw


def journal_acknowledge(expected):
    """Advance own authority only after exact expected-byte readback."""
    global _JOURNAL_GENERATION
    generation, raw = journal_snapshot()
    if raw != expected:
        raise JournalAuthorityChanged("Own journal mutation was superseded",
                                      "access")
    _JOURNAL_GENERATION = generation


def journal_verify():
    """Refuse changed bytes or metadata, including between lock scopes."""
    try:
        generation, raw = journal_snapshot()
        if generation != _JOURNAL_GENERATION:
            raise JournalAuthorityChanged("Journal generation changed",
                                          "access")
        return raw
    except BaseException as error:
        fail_publisher_cleanup()
        if isinstance(error, JournalAuthorityChanged):
            raise
        raise JournalAuthorityChanged("Journal authority unavailable",
                                      "access") from error


def journal_retain(descriptor):
    """Retain the first actual journal; a later acquisition cannot rebase it."""
    global _JOURNAL_RETAINED_FD, _JOURNAL_RETAINED_ID, _JOURNAL_GENERATION
    if _JOURNAL_RETAINED_FD is not None:
        journal_verify()
        return
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR):
        fail_publisher_cleanup()
        raise JournalAuthorityChanged("Unsafe RW journal acquisition", "access")
    retained = os.dup(descriptor)
    identity = (info.st_dev, info.st_ino)
    _JOURNAL_RETAINED_FD, _JOURNAL_RETAINED_ID = retained, identity
    try:
        generation, _ = journal_snapshot()
        if (_JOURNAL_GENERATION is not None and
                generation != _JOURNAL_GENERATION):
            raise JournalAuthorityChanged("Closed journal generation changed",
                                          "access")
        _JOURNAL_GENERATION = generation
    except BaseException:
        fail_publisher_cleanup()
        _JOURNAL_RETAINED_FD = None
        with contextlib.suppress(BaseException):
            close_retained_descriptor(retained, identity)
        raise


def journal_finish():
    """Verify lifetime authority before releasing only the retained owned FD."""
    global _JOURNAL_RETAINED_FD
    if _JOURNAL_RETAINED_FD is None:
        return
    primary = sys.exc_info()[0] is not None
    descriptor = _JOURNAL_RETAINED_FD
    failure = None
    try:
        if not _PUBLISHER_GATE_FAILED:
            journal_verify()
    except BaseException as error:
        failure = error
    try:
        close_retained_descriptor(descriptor, _JOURNAL_RETAINED_ID)
    except BaseException as error:
        fail_publisher_cleanup()
        failure = failure or error
    finally:
        _JOURNAL_RETAINED_FD = None
    if failure is not None and not primary:
        raise failure


def journal_output_info(pin, named=False):
    """Verify a retained output without following a replacement pathname."""
    info = os.fstat(pin["descriptor"])
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
        (info.st_dev, info.st_ino) != pin["identity"] or
            stat.S_IMODE(info.st_mode) != pin["mode"] or
            info.st_nlink not in (0, 1) or
            fcntl.fcntl(pin["descriptor"], fcntl.F_GETFL) & os.O_ACCMODE
            not in (os.O_RDWR, os.O_WRONLY)):
        raise AdapterError("Retained output authority changed", "access")
    if pin["parent"] is not None:
        parent = os.fstat(pin["parent"])
        if (not stat.S_ISDIR(parent.st_mode) or
            (parent.st_dev, parent.st_ino) != pin["parent_identity"] or
                parent.st_uid != pin["parent_owner"] or
                stat.S_IMODE(parent.st_mode) != pin["parent_mode"]):
            raise AdapterError("Retained output parent changed", "access")
    elif named:
        raise AdapterError("Output parent was already released", "access")
    if named:
        parent = os.stat(pin["root"], follow_symlinks=False)
        if (parent.st_dev, parent.st_ino) != pin["parent_identity"]:
            raise AdapterError("Output parent pathname changed", "access")
        current = os.stat(pin["name"],
                          dir_fd=pin["parent"],
                          follow_symlinks=False)
        if (current.st_dev, current.st_ino) != pin["identity"]:
            raise AdapterError("Output pathname changed", "access")
    return info


def journal_retain_output(descriptor, path, pins):
    """Keep the original writable output and parent through outer cleanup."""
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o022 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            not in (os.O_RDWR, os.O_WRONLY)):
        raise AdapterError("Unsafe output acquisition", "access")
    root = os.path.dirname(path)
    named_parent = os.stat(root, follow_symlinks=False)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    retained = None
    try:
        parent_info = os.fstat(parent)
        if (parent_info.st_dev, parent_info.st_ino) != (
                named_parent.st_dev,
                named_parent.st_ino,
        ):
            raise AdapterError("Output parent acquisition changed", "access")
        retained = os.dup(descriptor)
        pin = dict(
            descriptor=retained,
            identity=(info.st_dev, info.st_ino),
            mode=stat.S_IMODE(info.st_mode),
            parent=parent,
            root=root,
            parent_identity=(parent_info.st_dev, parent_info.st_ino),
            parent_owner=parent_info.st_uid,
            parent_mode=stat.S_IMODE(parent_info.st_mode),
            name=os.path.basename(path),
        )
        journal_output_info(pin, named=True)
        pins.append(pin)
        return pin
    except BaseException:
        if retained is not None:
            with contextlib.suppress(BaseException):
                held = os.fstat(retained)
                if (held.st_dev, held.st_ino) == (info.st_dev, info.st_ino):
                    os.close(retained)
        journal_close_directory(parent,
                                (named_parent.st_dev, named_parent.st_ino))
        raise


class JournalOutput:
    """Write immediately after identity checks; never buffer foreign-FD IO."""

    def __init__(self, descriptor, pin):
        self.descriptor = descriptor
        self.pin = pin

    def fileno(self):
        journal_output_info(self.pin, named=True)
        info = os.fstat(self.descriptor)
        if (info.st_dev, info.st_ino) != self.pin["identity"] or fcntl.fcntl(
                self.descriptor, fcntl.F_GETFL) & os.O_ACCMODE not in (
                    os.O_RDWR, os.O_WRONLY):
            raise AdapterError("Output FD was reused", "access")
        return self.descriptor

    def write(self, text):
        raw = text.encode("utf-8")
        offset = 0
        while offset < len(raw):
            count = os.write(self.fileno(), raw[offset:])
            if type(count) is not int or not 0 < count <= len(raw) - offset:
                raise AdapterError("Incomplete owned output write", "access")
            offset += count
        self.fileno()
        return len(text)

    def flush(self):
        self.fileno()


@contextlib.contextmanager
def journal_output(descriptor, path, pins):
    """Close only the acquired output while preserving any body exception."""
    identity = None
    try:
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        pin = journal_retain_output(descriptor, path, pins)
        output = JournalOutput(descriptor, pin)
        yield output, pin
        output.flush()
    except BaseException:
        fail_publisher_cleanup()
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        try:
            info = os.fstat(descriptor)
            if identity is None:
                named = os.stat(path, follow_symlinks=False)
                if (stat.S_ISREG(info.st_mode) and
                        info.st_uid == os.getuid() and
                    (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino)):
                    identity = (info.st_dev, info.st_ino)
            if (identity is None or (info.st_dev, info.st_ino) != identity or
                    fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
                    not in (os.O_RDWR, os.O_WRONLY)):
                raise AdapterError("Output FD ownership changed", "access")
            os.close(descriptor)
        except BaseException:
            fail_publisher_cleanup()
            if not primary:
                raise


def journal_invalidate_outputs():
    """Invalidate owned artifacts before fallible readiness signalling."""
    errors = []
    for pin in _JOURNAL_EXPORTS:
        if pin["descriptor"] is None:
            continue
        try:
            journal_output_info(pin)
            os.ftruncate(pin["descriptor"], 0)
            os.fsync(pin["descriptor"])
        except BaseException as error:
            errors.append(error)
    for pin in _JOURNAL_SIGNALS:
        if pin["descriptor"] is None:
            continue
        try:
            journal_output_info(pin)
            raw = b"pacing_ready=false\n"
            offset = 0
            while offset < len(raw):
                journal_output_info(pin)
                count = os.write(pin["descriptor"], raw[offset:])
                if type(count) is not int or not 0 < count <= len(raw) - offset:
                    raise AdapterError("Readiness write incomplete", "access")
                offset += count
            os.fsync(pin["descriptor"])
        except BaseException as error:
            errors.append(error)
    if errors:
        raise AdapterError("Retained output invalidation failed", "access")


def journal_finish_outputs():
    """Keep output authority through all earlier lifecycle closes."""
    primary = sys.exc_info()[0] is not None
    failures = []
    for pin in _JOURNAL_SIGNALS + _JOURNAL_EXPORTS:
        for key, identity in (
            ("parent", pin["parent_identity"]),
            ("descriptor", pin["identity"]),
        ):
            descriptor = pin[key]
            if descriptor is None:
                continue
            try:
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != identity:
                    raise AdapterError("Output cleanup FD changed", "access")
                os.close(descriptor)
            except BaseException as error:
                if key == "parent":
                    pin[key] = None
                fail_publisher_cleanup()
                failures.append(error)
            finally:
                pin[key] = None
    if failures and not primary:
        raise failures[0]


def journal_close_directory(descriptor, identity):
    """Preserve foreign recycled parent FDs and the original body failure."""
    primary = sys.exc_info()[0] is not None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            (info.st_dev, info.st_ino) != identity):
            raise AdapterError("Original directory FD changed", "access")
        os.close(descriptor)
    except BaseException:
        fail_publisher_cleanup()
        if not primary:
            raise


def journal_verify_output_bytes(pin, payload):
    """Prove complete owned export bytes before signalling readiness."""
    before = journal_output_info(pin, named=True)
    chunks = []
    offset = 0
    while offset <= len(payload):
        block = os.pread(pin["descriptor"], len(payload) + 1 - offset, offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    after = journal_output_info(pin, named=True)
    if b"".join(chunks) != payload or journal_metadata(
            before) != journal_metadata(after):
        raise AdapterError("Owned export readback differs", "access")


def save_budget(state):
    """Retain the original source helper interface under modern ownership."""
    return write_budget(state)


def decode_local_budget(raw):
    """Only the exact reviewed settled research journal may migrate locally."""
    value = json.loads(raw)
    if value.get("schema") == 3:
        return validate_budget(value)
    if (os.environ.get("GITHUB_ACTIONS") == "true" or
            hashlib.sha256(raw).hexdigest() != _LEGACY_LOCAL_STATE_SHA256):
        raise AdapterError("Unreviewed legacy local publisher state", "access")
    validate_legacy_budget(value)
    return reviewed_research_transition()


def reviewed_research_transition():
    state = copy.deepcopy(_RESEARCH_TRANSITION)
    digest = hashlib.sha256(
        json.dumps(state, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    if digest != _RESEARCH_TRANSITION_SHA256:
        raise AdapterError("Reviewed research transition changed", "access")
    return validate_budget(state)


def legacy_owned_state(value):
    """Convert only the authentic latest six-key owner, preserving unknowns."""
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    if digest != _LEGACY_OWNED_STATE_SHA256:
        raise AdapterError("Legacy owned publisher state changed", "access")
    value = validate_legacy_budget(value)
    result = empty_budget(value["observed_at"])
    result.update(
        observed_at=value["observed_at"],
        until=value["not_before"],
        interval=max(value["interval"], 12.2),
        page_gap=max(value["page_interval"], 30),
        starts=list(value["starts"]),
        blocked=value["blocked"],
        completed=value["completed"],
        inventory_completed=value["completed"],
        observed_429_since_transition=value["rate_limit_strikes"],
        rate_limit_until=value["rate_limit_until"],
        legacy_rate_history_unknown=True,
    )
    return validate_budget(result)


def source_revision_digest(head, source):
    if source != SOURCE_ID or not re.fullmatch(r"[0-9a-f]{40}", head or ""):
        raise AdapterError("Invalid producer source revision", "access")
    value = workflow_api(f"/contents/adapters/{source}/adapter.py?ref={head}")
    if (not isinstance(value, dict) or value.get("type") != "file" or
            value.get("path") != f"adapters/{source}/adapter.py" or
            value.get("encoding") != "base64" or
            type(value.get("size")) is not int or
            not 0 < value["size"] <= 5_000_000):
        raise AdapterError("Invalid immutable producer source", "access")
    try:
        raw = base64.b64decode("".join(value["content"].split()), validate=True)
    except (KeyError, ValueError, TypeError):
        raise AdapterError("Invalid immutable source bytes", "access") from None
    if len(raw) != value["size"]:
        raise AdapterError("Incomplete immutable producer source", "access")
    return hashlib.sha256(raw).hexdigest()


def download_inert_artifact(identity, artifact_id, digest=None):
    """Keep the original authenticated ZIP boundary and strict owner decoder."""
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
        infrastructure_url(destination, storage_only=True)
        status, _, body = request_bytes(destination, byte_limit=32768)
        if (status != 200 or len(body) > 32768 or not isinstance(digest, str) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or
                digest[7:] != hashlib.sha256(body).hexdigest()):
            raise ValueError("Invalid or mismatched artifact response")
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
        common = {
            "schema", "repository", "run_id", "run_attempt", "source", "state"
        }
        modern = common | {"head_sha", "adapter_sha256", "state_sha256"}
        if not isinstance(envelope, dict) or set(envelope) not in (common,
                                                                   modern):
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source = identity
        if (type(envelope["schema"]) is not int or envelope["schema"] != 1 or
                envelope["repository"] != "YouthOpps/data-pipeline" or
                type(envelope["run_id"]) is not int or
                type(envelope["run_attempt"]) is not int or
                envelope["run_id"] != run_id or
                envelope["run_attempt"] != attempt or
                envelope["source"] != source or source != SOURCE_ID):
            raise ValueError("Artifact ownership mismatch")
        if set(envelope) == common:
            if (identity != (43998513268, 1, SOURCE_ID) or
                    artifact_id != 14290823021 or
                    digest != "sha256:" + _LEGACY_OWNED_ZIP_SHA256 or
                    _ATTEMPT_HEADS.get(
                        (run_id, attempt)) != _LEGACY_OWNED_HEAD):
                raise ValueError("Unreviewed legacy owner")
            return legacy_owned_state(envelope["state"])
        head = _ATTEMPT_HEADS.get((run_id, attempt))
        if (envelope["head_sha"] != head or
                envelope["adapter_sha256"] != source_revision_digest(
                    head, source) or
                envelope["state_sha256"] != hashlib.sha256(
                    json.dumps(envelope["state"],
                               sort_keys=True,
                               separators=(",", ":")).encode()).hexdigest()):
            raise ValueError("Modern producer or state digest mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError("Invalid or unavailable family pacing artifact",
                           "access") from None


def production_maintenance_state(incoming):
    """Require a source-committed decision for the reviewed native MAX."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return incoming
    expected = {
        "kind": "reviewed-bmw-native-max-handoff",
        "issue": 391,
        "native_source_sha256": (
            "62a1a11ef6017d471fb1ceba890bc2f7367"
            "bcfbf086b96606ff37f111c34b44c"),
        "qualified_source_sha256": (
            "6e8d2de0f3e5fcff0d581bce20c01996"
            "adeaa85a990774c4e52c812d4d59d065"),
        "native_final_state_sha256": (
            "ce1046252dfa48ead2d07327562a01593d"
            "301b4fe6cc7dc5e6dfd419e96b4098"),
        "state_sha256": (
            "355179fbae605286be38c3e1cf82a07e314"
            "c40a709ddabbf0064d0b2c268843a"),
    }
    decision = _HOSTED_HANDOFF_DECISION
    if (not isinstance(decision, dict)
            or set(decision) != set(expected) | {"evidence"}
            or any(decision[key] != value for key, value in expected.items())
            or not isinstance(decision["evidence"], str)
            or not re.fullmatch(
                r"https://github\.com/YouthOpps/data-pipeline/issues/391"
                r"#issuecomment-[1-9][0-9]*", decision["evidence"])):
        raise AdapterError("Final native MAX handoff is not approved",
                           "access")
    candidate = copy.deepcopy(_HOSTED_HANDOFF_STATE)
    raw = json.dumps(candidate, sort_keys=True,
                     separators=(",", ":")).encode()
    if (len(raw) > 16384 or hashlib.sha256(raw).hexdigest()
            != expected["state_sha256"]):
        raise AdapterError("Reviewed native MAX handoff changed", "access")
    # The caller already authenticated the newest exact owned artifact.
    # Its newer constraints, actual holds and pending state can only increase.
    return merge_budget(validate_budget(incoming), validate_budget(candidate))


def legal_review_handoff(incoming):
    """Retain actual post-reservation starts and completed research scope (#494)."""
    workflow = workflow_api("/actions/workflows/432522463")
    if (workflow.get("id") != 432522463 or workflow.get("state") != "disabled_manually"
            or workflow.get("path") != ".github/workflows/qa-bmw494-capture.yml"):
        raise AdapterError("BMW policy research owner must remain retired", "access")
    history = workflow_api("/actions/workflows/432522463/runs?per_page=2")
    rows = history.get("workflow_runs", [])
    if history.get("total_count") != 1 or len(rows) != 1:
        raise AdapterError("BMW policy research history changed", "access")
    expected = {"id": 45967296430, "run_attempt": 1, "status": "completed",
                "conclusion": "success", "head_sha": "3162ff3c7404caeeea0ad1769b29b58ba8184826",
                "head_branch": "qa/494-legal-body", "workflow_id": 432522463}
    if any(rows[0].get(key) != value for key, value in expected.items()):
        raise AdapterError("BMW policy research attempt changed", "access")
    artifacts = workflow_api("/actions/runs/45967296430/artifacts?per_page=2")
    rows = artifacts.get("artifacts", [])
    if (artifacts.get("total_count") != 1 or len(rows) != 1
            or rows[0].get("id") != 15183207238 or rows[0].get("expired") is not False
            or rows[0].get("digest") != "sha256:4f29eb5f7f3b21b84dea6fa2552c37e999b8a16a8c4fb77e36ed0f5238c99a4e"):
        raise AdapterError("Reviewed BMW policy archive differs", "access")
    observed = dict(incoming, completed=1795768845.7511, observed_at=1795768845.7511,
                    until=1795768857.9511, starts=[1795768830.2058296, 1795768831.621175,
                                                   1795768844.157197, 1795768845.5530152])
    state = merge_budget(incoming, observed)
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != "340f8e8c3cefa4ad0694f3d13ae6522261b9b28dc3fbb6d5f0c40a8a208109b0":
        raise AdapterError("BMW policy state handoff differs", "access")
    return state


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED, _VERIFIED_REMOTE_CREATE
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Mature family history is missing", "access")
    run_id, attempt, source = latest
    expected = _ARTIFACT_NAME + "-" + str(attempt)
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or result.get("total_count") != len(
            artifacts) or len(artifacts) > 100:
        raise AdapterError("Incomplete family artifact inventory", "access")
    matches = [item for item in artifacts if item.get("name") == expected]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError("Newest family pacing artifact missing or expired",
                           "access")
    artifact = matches[0]
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or
            artifact.get("workflow_run", {}).get("id") != run_id or
            artifact.get("workflow_run", {}).get("head_sha")
            != _ATTEMPT_HEADS.get(
                (run_id, attempt)) or (run_id, attempt) not in _ATTEMPT_HEADS or
            not re.fullmatch(r"sha256:[0-9a-f]{64}",
                             artifact.get("digest") or "") or
            type(artifact.get("size_in_bytes")) is not int or
            not 0 < artifact["size_in_bytes"] <= 32768):
        raise AdapterError("Invalid family artifact binding", "access")
    if latest == (45923888649, 1, SOURCE_ID) and (
            artifact["id"] != 15165713870 or artifact["size_in_bytes"] != 1132
            or artifact["digest"] != "sha256:d4a398ac805706b549acbbb5496a9d83595b29615bd30d24adbe0f2600aed8f0"):
        raise AdapterError("Reviewed November 27 state artifact differs", "access")
    incoming = download_inert_artifact(latest, artifact["id"],
                                       artifact["digest"])
    if latest == (45966059718, 1, SOURCE_ID):
        incoming = legal_review_handoff(incoming)
    incoming = production_maintenance_state(incoming)
    incoming = merge_budget(incoming, reviewed_research_transition())
    _VERIFIED_REMOTE_CREATE = copy.deepcopy(incoming)
    with locked_budget():
        local = load_budget()
        combined = merge_budget(local, incoming)
        if combined["in_flight"]:
            raise AdapterError("Unsettled publisher request retained", "access")
        combined["until"] = max(combined["until"], time.time() + 60)
        combined["observed_at"] = time.time()
        write_budget(combined)
    _RESTORED = _STATE_TRUSTED = True


def verify_local_publisher_start():
    global _LOCAL_FIRST_ACCESS_CHECKED
    if os.environ.get(
            "GITHUB_ACTIONS") == "true" or _LOCAL_FIRST_ACCESS_CHECKED:
        return
    verify_local_workflow_pause()
    _LOCAL_FIRST_ACCESS_CHECKED = True


def wait_startup_quiet():
    if _STARTUP_QUIET_UNTIL is None:
        raise AdapterError("Publisher startup quiet is unproved", "access")
    while time.monotonic() < _STARTUP_QUIET_UNTIL:
        check_deadline()
        time.sleep(max(0, min(1, _STARTUP_QUIET_UNTIL - time.monotonic())))


def authenticate_hosted_run():
    run_id, attempt = current_run_identity()
    value = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    head = os.environ.get("GITHUB_SHA")
    if (value.get("id") != run_id or value.get("run_attempt") != attempt or
            value.get("head_sha") != head or
            value.get("head_branch") != "main" or
            value.get("workflow_id") != _LOCAL_WORKFLOW_ID or
            family_source(value) != SOURCE_ID or
            source_revision_digest(head, SOURCE_ID) != source_file_digest()):
        raise AdapterError("Untrusted current producer implementation",
                           "access")
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    jobs = result.get("jobs")
    if result.get("total_count") != 1 or not isinstance(jobs,
                                                        list) or len(jobs) != 1:
        raise AdapterError("Current producer job is missing or ambiguous",
                           "access")
    job = jobs[0]
    if (job.get("run_id") != run_id or job.get("run_attempt") != attempt or
            job.get("head_sha") != head or job.get("head_branch") != "main" or
            job.get("name") != "collect" or job.get("status") != "in_progress"):
        raise AdapterError("Untrusted current collect job", "access")


def require_existing_local_state():
    """A mature local namespace cannot be recreated from an older artifact."""
    directory = os.path.join(tempfile.gettempdir(),
                             FAMILY + "-" + str(os.getuid()))
    descriptor = info = None
    try:
        # Retain this parent through subsequent startup, preventing a race
        # from turning a removed directory into a new initialization grant.
        parent = state_parent(directory)
        descriptor = os.open("state.json", os.O_RDWR | os.O_NOFOLLOW |
                             os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(descriptor)
        validate_state_descriptor(descriptor, parent, "state.json")
        if not 0 < info.st_size <= 16384:
            raise AdapterError("Existing local journal is empty or oversized",
                               "access")
        budget_file()
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_retain(descriptor)
    except BaseException:
        fail_publisher_cleanup()
        raise
    finally:
        if descriptor is not None and info is not None:
            primary = sys.exc_info()[0] is not None
            try:
                close_retained_descriptor(descriptor,
                                          (info.st_dev, info.st_ino))
            except BaseException:
                fail_publisher_cleanup()
                if not primary:
                    raise


def prepare_collection():
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT, _COLLECTION_STARTED
    global _LOCAL_FIRST_ACCESS_CHECKED, _STARTUP_QUIET_UNTIL, _RESTORED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate is sticky", "access")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        authenticate_hosted_run()
    else:
        require_existing_local_state()
        verify_local_workflow_pause()
    budget_file()
    if _COLLECTION_LOCK_FD is not None:
        raise AdapterError("Nested publisher collection refused", "access")
    parent = state_parent()
    path = os.path.join(_STATE_PARENT_PATH, "collection.lock")
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if _INITIAL_DIRECTORY_PROVEN:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = info = None
    try:
        descriptor = os.open("collection.lock", flags, 0o600, dir_fd=parent)
        info = os.fstat(descriptor)
        validate_owned_output(info)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat("collection.lock",
                          dir_fd=parent,
                          follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Publisher lifecycle changed during acquisition")
        guard = os.dup(descriptor)
    except BaseException:
        fail_publisher_cleanup()
        if descriptor is not None and info is not None:
            with contextlib.suppress(BaseException):
                close_retained_descriptor(descriptor,
                                          (info.st_dev, info.st_ino))
        raise AdapterError("Unsafe or active publisher lifecycle",
                           "access") from None
    _COLLECTION_LOCK_FD = descriptor
    _COLLECTION_LOCK_CONTEXT = (descriptor, guard, (info.st_dev, info.st_ino),
                                path)
    collection_lock_authority()
    _LOCAL_FIRST_ACCESS_CHECKED = False
    _COLLECTION_STARTED = time.time()
    _STARTUP_QUIET_UNTIL = time.monotonic() + 60
    _RESTORED = False
    restore_family_artifact()


def infrastructure_url(value, *, storage_only=False):
    """Admit only GitHub API or its supported signed artifact storage."""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = parsed.hostname or ""
        labels = host.split(".")
        storage = host.endswith((
            ".blob.core.windows.net", ".actions.githubusercontent.com"))
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.netloc.lower() != host
                or parsed.port is not None or parsed.fragment
                or re.search(r"[\x00-\x20\\]", value)
                or any(not re.fullmatch(
                    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                       for label in labels)
                or not (storage or not storage_only
                        and host == "api.github.com")):
            raise ValueError("Untrusted infrastructure destination")
    except (ValueError, TypeError):
        raise AdapterError("Untrusted GitHub infrastructure destination",
                           "access") from None
    return value


def infrastructure_request_bytes(url,
                                 method="GET",
                                 headers=None,
                                 payload=None,
                                 byte_limit=30000000):
    """Retain the existing bounded GitHub API and signed-ZIP transport."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = infrastructure_url(url)
    storage_only = urllib.parse.urlsplit(current).hostname != "api.github.com"
    if storage_only:
        if method != "GET" or payload is not None:
            raise AdapterError("Signed artifact requires public GET", "access")
        headers = {key: value for key, value in headers.items()
                   if key.lower() != "authorization"}
    for redirect in range(6):
        check_deadline()
        infrastructure_url(current, storage_only=storage_only)
        request = urllib.request.Request(current,
                                         data=payload,
                                         headers=headers,
                                         method=method)
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, response_headers = response.status, response.headers
            location = response_headers.get("Location")
            declared = response_headers.get("Content-Length")
            if declared and (not declared.isdigit() or
                             int(declared) > byte_limit):
                raise AdapterError("Oversized or invalid response length",
                                   "fetch")
            body = response.read(byte_limit + 1)
            if len(body
                  ) > byte_limit or declared and len(body) != int(declared):
                raise AdapterError("Incomplete or oversized response", "fetch")
            if response_headers.get("Content-Encoding",
                                    "identity") != "identity":
                raise AdapterError("Unsupported response content encoding",
                                   "fetch")
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            if not storage_only:
                raise AdapterError("Unexpected GitHub API redirect", "publish")
            destination = urllib.parse.urljoin(current, location)
            current = infrastructure_url(destination, storage_only=True)
            continue
        return status, response_headers, body
    raise AdapterError("Unresolved infrastructure redirect", "access")


def verify_retained_flock(descriptor, path):
    """Prove the original open-file description still holds its flock."""
    held = os.fstat(descriptor)
    probe = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(probe)
        validate_owned_output(current)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Locked object changed", "access")
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another open-file description must hold the lock. Prove it is
            # ours, rather than a competing collector that acquired a gap.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise AdapterError("Original publisher lock was released", "access")
    finally:
        os.close(probe)


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or parsed.netloc != "www.bmwgroup.jobs"
            or parsed.username or parsed.password or parsed.port
            or parsed.fragment or re.search(r"[\x00-\x20\\]", value)
            or any(part in {"admin", "login", "logout"}
                   for part in parsed.path.lower().split("/"))):
        raise AdapterError("Unreviewed BMW public origin or route", "access")
    return value


def request_bytes(url, *, method="GET", headers=None, payload=None,
                  interval=12.2, robots=False, byte_limit=30000000):
    if (urllib.parse.urlsplit(url).hostname or "").rstrip(".") in (
            _PUBLISHER_HOSTS):
        return source_request(url, method=method, headers=headers,
                              payload=payload, interval=interval,
                              robots=robots, byte_limit=byte_limit)
    return infrastructure_request_bytes(url, method, headers, payload,
                                        byte_limit)


def source_request(url, *, interval=12.2, method="GET", payload=None,
                   robots=False, headers=None, byte_limit=30000000):
    """Only the advertised public GET routes; each hop owns pending state."""
    current = public_url(url)
    if method != "GET" or payload is not None or headers:
        raise AdapterError("Unreviewed BMW publisher request", "access")
    for hop in range(4):
        parsed = urllib.parse.urlsplit(public_url(current))
        if robots and (parsed.path != "/robots.txt" or parsed.query):
            raise AdapterError("Invalid robots purpose", "access")
        if not robots:
            interval = max(interval, check_robots(current))
        status, response_headers, body = physical_request_bytes(
            current, interval=interval, robots=robots, byte_limit=byte_limit)
        if status in (301, 302, 303, 307, 308):
            location = response_headers.get("Location")
            if not location or hop == 3:
                raise AdapterError("Invalid source redirect", "access")
            current = public_url(urllib.parse.urljoin(current, location))
            continue
        if status != 200 and not robots:
            raise AdapterError("Publisher content returned HTTP " + str(status),
                               "fetch", status)
        return status, response_headers, body
    raise AdapterError("Unresolved source redirect", "access")


def fetch_source(url, min_interval=12.2, *, policy_response=False):
    status, headers, body = source_request(
        url, interval=min_interval, robots=policy_response)
    if policy_response:
        return status, headers, body
    return body.decode(headers.get_content_charset() or "utf-8", "strict")


def fetch_inventory_page(fetch, *args, **kwargs):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = True
    try:
        return fetch(*args, **kwargs)
    finally:
        _INVENTORY_ACTIVE = previous


def collect():
    """Keep the original sole native test contract within absolute deadlines."""
    if _RUN_END is None:
        with execution():
            with phase("collection", COLLECTION_TIMEOUT):
                return collect_body()
    with phase("collection", COLLECTION_TIMEOUT):
        return collect_body()


def main():
    with execution():
        try:
            return run()
        finally:
            primary = sys.exc_info()[1]
            if os.environ.get("GITHUB_ACTIONS") == "true":
                if primary is None:
                    with phase("export", EXPORT_TIMEOUT):
                        export_family_artifact()
                else:
                    with contextlib.suppress(BaseException):
                        with phase("export", EXPORT_TIMEOUT):
                            export_family_artifact()

_LEGACY_LOCAL_STATE_SHA256 = (
    "9139a60e7529137f3e17a9ae8c500cff202ca6dd5ec619f67487ad13def3"
    "ef44"
)

_RESEARCH_TRANSITION_SHA256 = (
    "b05ad0ae9afc98e4d6f88b64b01dde3c32a3ccb353471225fd7e9b05084e"
    "dd78"
)

_LEGACY_OWNED_STATE_SHA256 = (
    "c771c1226ca740dc1be65cdca482f2b586ade78222b56655fdd093168c13"
    "70cc"
)

_LEGACY_OWNED_ZIP_SHA256 = (
    "b9e54c0da0fa70c273f8a8de27f9ac01367bbd7a14e1c9e33dd6dd40ce58"
    "78fa"
)

_LEGACY_OWNED_HEAD = "9e8e59d64d479829912bc343fc23b526f6519aea"

_RESEARCH_TRANSITION = {
    "blocked": False,
    "completed": 1794909778.726673,
    "family": "youthopps-bmw-group-publisher-v1",
    "in_flight": False,
    "interval": 12.2,
    "inventory_completed": 1794909778.7265465,
    "legacy_429_history": "unknown",
    "legacy_access_history_unknown": True,
    "legacy_access_observations": [
    ],
    "legacy_hold_history": "unlogged_fields_unknown",
    "legacy_rate_history_unknown": True,
    "observed_429_since_transition": 0,
    "observed_at": 1794909778.726673,
    "page_gap": 30,
    "rate_limit_until": 0,
    "research_provenance": {
        "8870": {
            "capture": (
                "51cd796ad73a3e5d37a598bd663980b1e8e06e0c"
                "a5605a96fc458ed021c9ede6"
            ),
            "controller_sha256": (
                "1799270817dba548cd3124fecb9736888ed10714"
                "234f5014f47572bd7dd2096e"
            ),
            "guard_sha256": (
                "057c0ade5563b377ef0d1cc603543bd7c855107a"
                "39c6e30fcd095434c6789315"
            ),
            "persistence": (
                "574934238206f621cb2378d02f7cc9c757e14b04"
                "41b527642768d1a6800cf482"
            ),
            "status": (
                "10ee7212318ac629cefbc8d1003003d2abc985b4"
                "676a80d8fa4ab17d85a8c26b"
            ),
        },
        "8871": {
            "capture": (
                "ed0d792553499d4a0096422072656d0b6991fb71"
                "283de2233793e7864cba64a2"
            ),
            "controller_sha256": (
                "ceacd9e8f1356e1caf08b07f3cfe77ed20ccdc6b"
                "1a3503a614061845c2223592"
            ),
            "guard_sha256": (
                "9a4ea0ad58e230b0c90c18a9e8d842cc8258ac5b"
                "48c3e9fc3fc53698ce78689f"
            ),
            "persistence": (
                "76a218e8f7f0aa4101f7c1b7886c90b5588b5f92"
                "785aa89cb408a204d1d200da"
            ),
            "status": (
                "771049999476db61cfa1f6ac34f82f2e60081b68"
                "2afa9dd9acdd6ec5136182aa"
            ),
        },
    },
    "schema": 3,
    "starts": [
        1794880351.84692,
        1794880364.706743,
        1794908253.4563498,
        1794908329.1280534,
        1794908344.823981,
        1794908358.27946,
        1794908371.3389707,
        1794908385.8807216,
        1794908399.3709297,
        1794909341.9703007,
        1794909417.4264784,
        1794909433.2237983,
        1794909466.4986186,
        1794909498.7357147,
        1794909531.4939315,
        1794909564.4820395,
        1794909596.0208871,
        1794909629.2562978,
        1794909662.6185675,
        1794909693.7348237,
        1794909726.2334356,
        1794909759.057816,
    ],
    "until": 1794880351.8468106,
}

_HOSTED_HANDOFF_DECISION = {
    "kind": "reviewed-bmw-native-max-handoff",
    "issue": 391,
    "native_source_sha256": (
        "62a1a11ef6017d471fb1ceba890bc2f7"
        "367bcfbf086b96606ff37f111c34b44c"),
    "qualified_source_sha256": (
        "6e8d2de0f3e5fcff0d581bce20c01996"
        "adeaa85a990774c4e52c812d4d59d065"),
    "native_final_state_sha256": (
        "ce1046252dfa48ead2d07327562a0159"
        "3d301b4fe6cc7dc5e6dfd419e96b4098"),
    "state_sha256": (
        "355179fbae605286be38c3e1cf82a07e"
        "314c40a709ddabbf0064d0b2c268843a"),
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/391"
        "#issuecomment-6947746096"),
}

_HOSTED_HANDOFF_STATE = {
    "blocked": False,
    "completed": 1794912213.1568158,
    "family": "youthopps-bmw-group-publisher-v1",
    "in_flight": False,
    "interval": 12.2,
    "inventory_completed": 1794912213.1568158,
    "legacy_429_history": "unknown",
    "legacy_access_history_unknown": True,
    "legacy_access_observations": [
    ],
    "legacy_hold_history": "unlogged_fields_unknown",
    "legacy_rate_history_unknown": True,
    "observed_429_since_transition": 0,
    "observed_at": 1794912213.1568158,
    "page_gap": 30,
    "rate_limit_until": 0,
    "research_provenance": {
        "8870": {
            "capture": (
                "51cd796ad73a3e5d37a598bd663980b1e8e06e0c"
                "a5605a96fc458ed021c9ede6"
            ),
            "controller_sha256": (
                "1799270817dba548cd3124fecb9736888ed10714"
                "234f5014f47572bd7dd2096e"
            ),
            "guard_sha256": (
                "057c0ade5563b377ef0d1cc603543bd7c855107a"
                "39c6e30fcd095434c6789315"
            ),
            "persistence": (
                "574934238206f621cb2378d02f7cc9c757e14b04"
                "41b527642768d1a6800cf482"
            ),
            "status": (
                "10ee7212318ac629cefbc8d1003003d2abc985b4"
                "676a80d8fa4ab17d85a8c26b"
            ),
        },
        "8871": {
            "capture": (
                "ed0d792553499d4a0096422072656d0b6991fb71"
                "283de2233793e7864cba64a2"
            ),
            "controller_sha256": (
                "ceacd9e8f1356e1caf08b07f3cfe77ed20ccdc6b"
                "1a3503a614061845c2223592"
            ),
            "guard_sha256": (
                "9a4ea0ad58e230b0c90c18a9e8d842cc8258ac5b"
                "48c3e9fc3fc53698ce78689f"
            ),
            "persistence": (
                "76a218e8f7f0aa4101f7c1b7886c90b5588b5f92"
                "785aa89cb408a204d1d200da"
            ),
            "status": (
                "771049999476db61cfa1f6ac34f82f2e60081b68"
                "2afa9dd9acdd6ec5136182aa"
            ),
        },
    },
    "schema": 3,
    "starts": [
        1794880351.84692,
        1794880364.706743,
        1794908253.4563498,
        1794908329.1280534,
        1794908344.823981,
        1794908358.27946,
        1794908371.3389707,
        1794908385.8807216,
        1794908399.3709297,
        1794909341.9703007,
        1794909417.4264784,
        1794909433.2237983,
        1794909466.4986186,
        1794909498.7357147,
        1794909531.4939315,
        1794909564.4820395,
        1794909596.0208871,
        1794909629.2562978,
        1794909662.6185675,
        1794909693.7348237,
        1794909726.2334356,
        1794909759.057816,
        1794911462.91189,
        1794911479.4124148,
        1794911493.6019154,
        1794911507.4474094,
        1794911520.4814122,
        1794911534.2851193,
        1794911547.1791844,
        1794911578.017117,
        1794911609.2857883,
        1794911640.2296615,
        1794911672.4417884,
        1794911704.8564997,
        1794911736.9543202,
        1794911768.982032,
        1794911801.0453973,
        1794911832.957761,
        1794911865.4772708,
        1794911896.9200783,
        1794911928.6767712,
        1794911959.2773402,
        1794911991.035107,
        1794912022.8997748,
        1794912053.635139,
        1794912085.8093624,
        1794912117.7160199,
        1794912149.791737,
        1794912180.4202797,
        1794912212.5132635,
    ],
    "until": 1794911462.9107761,
}


if __name__ == "__main__":
    raise SystemExit(main())
