"""University of Vienna's public Non-EU exchange-place inventory.

The programme page publishes eligibility and conditional scholarship funding.
Its regional Mobility Online forms publish specific bilateral exchange places,
including unavailable allocations; these are retained with an unknown status.
All rows are rendered in HTML: DataTables pagination is entirely client-side.
Partner-specific URLs use public form parameters, not session-encrypted detail
links. Identifiers include academic year, programme, institution and study
field.
Reviewed Ottawa allocations always use explicit study-cycle/duration indicators
(even when only one allocation remains) as a semantic
agreement discriminator; changing place quantities never participate in
identity.
No nationality restriction is inferred from enrolment at the University of
Vienna.
The imprint permits attributed noncommercial copies; only factual fields are
normalised here. Sources: https://international.univie.ac.at/en/imprint and the
programme/mobility endpoints below. No authentication or JS execution is needed."""

import argparse
import base64
from collections import Counter, deque
import contextlib
import copy
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import errno
import fcntl
import functools
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import random
import re
import signal
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
from urllib import robotparser
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

SOURCE_ID = "at-university-of-vienna"


SOURCE_URL = (
    "https://international.univie.ac.at/en/"
    "study-abroad-with-erasmus-and-co/non-eu-student-exchange-program"
)


WEBSITE_URL = "https://international.univie.ac.at/en/"


LANGUAGE = "en"


PUBLISHER_COUNTRY = "AT"


PUBLISHER_TYPE = "university"


ATTRIBUTION = "Source: University of Vienna"


PORTAL = "https://mobility.univie.ac.at/mobility/MobilitySearchServlet"


COUNTRIES = {
    "Argentina": "AR",
    "Australia": "AU",
    "Brazil": "BR",
    "Canada": "CA",
    "Chile": "CL",
    "China": "CN",
    "Colombia": "CO",
    "Costa Rica": "CR",
    "India": "IN",
    "Japan": "JP",
    "Lebanon": "LB",
    "Mexico": "MX",
    "Peru": "PE",
    "Singapore": "SG",
    "South Africa": "ZA",
    "South Korea": "KR",
    "Taiwan": "TW",
    "Tanzania": "TZ",
    "Thailand": "TH",
    "USA": "US",
    "United States": "US",
    "United States of America": "US",
    "Uruguay": "UY",
}


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


_BUDGETS = {}


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


def legacy_pace(url, interval=6):
    host = (
        (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    )
    if host in ("international.univie.ac.at", "mobility.univie.ac.at"):
        host = SOURCE_ID
    budget = _BUDGETS.setdefault(
        host, {"starts": deque(), "interval": 6, "until": 0}
    )
    budget["interval"] = max(budget["interval"], interval, 6)
    while True:
        now = time.monotonic()
        while budget["starts"] and now - budget["starts"][0] >= 60:
            budget["starts"].popleft()
        ready = max(
            budget["until"],
            (
                budget["starts"][-1] + budget["interval"]
                if budget["starts"]
                else now
            ),
            budget["starts"][0] + 60 if len(budget["starts"]) >= 10 else now,
        )
        if ready <= now:
            budget["starts"].append(now)
            return budget
        time.sleep(ready - now)


def legacy_request_bytes(url, *, interval=6, method="GET", headers=None, payload=None):
    received_publisher_429 = False
    try:
        budget = legacy_pace(url, interval)
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={"User-Agent": _USER_AGENT, **(headers or {})},
        )
        try:
            response = _OPENER.open(request, timeout=30)
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
                        budget["until"] = max(
                            budget["until"], time.monotonic() + max(0, seconds)
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
                    budget["until"] = max(
                        budget["until"], time.monotonic() + max(0, seconds)
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


def robots_directive(parser, directive, url=None):
    """Retain native policy and every applicable product group's restriction."""
    import copy

    policies = [parser]
    for entry in parser.entries:
        if entry.applies_to(_USER_AGENT):
            policy = copy.copy(parser)
            policy.entries = [entry]
            policy.default_entry = None
            policies.append(policy)
    if directive == "can_fetch":
        return all(policy.can_fetch(_USER_AGENT, url) for policy in policies)
    values = [
        getattr(policy, directive)(_USER_AGENT) for policy in policies
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    if directive == "crawl_delay":
        return max(values)
    if any(rate.requests <= 0 for rate in values):
        raise AdapterError("Invalid robots request rate", "access")
    return max(values, key=lambda rate: rate.seconds / rate.requests)


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


def metadata(attempt, previous, records, error=None):
    result = {
        "display_name": "University of Vienna",
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


class InventoryHTML(HTMLParser):
    """Extract public form options and all rows of the publisher's inventory."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.options = {}
        self.select = None
        self.option = None
        self.option_text = []
        self.inventory = False
        self.in_body = False
        self.cells = None
        self.cell = None
        self.rows = []
        self.headers = []
        self.flags = []
        self.text = []
        self.skip = 0
        self.row_flags = []
        self.details = []
        self.row_details = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
            if self.inventory:
                self.row_details.append(attributes["href"])
        if tag == "select":
            self.select = attributes.get("name")
            self.options.setdefault(self.select, {})
        if tag == "option" and self.select:
            self.option = attributes.get("value")
            self.option_text = []
        if (
            tag == "table"
            and "datatable" in attributes.get("class", "").split()
        ):
            self.inventory = True
        if self.inventory and tag == "tbody":
            self.in_body = True
        if self.inventory and tag == "tr":
            self.cells = []
            self.row_flags = []
            self.row_details = []
        if self.inventory and tag in ("td", "th"):
            self.cell = []
        if self.inventory and tag == "i" and attributes.get("title"):
            self.row_flags.append(attributes["title"])
        if tag == "br":
            self.handle_data(" ")

    def handle_data(self, data):
        if self.skip:
            return
        self.text.append(data)
        if self.option is not None:
            self.option_text.append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        if tag == "option" and self.option is not None:
            self.options[self.select][self.option] = " ".join(
                "".join(self.option_text).split()
            )
            self.option = None
        if tag == "select":
            self.select = None
        if self.inventory and tag in ("td", "th") and self.cell is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        if self.inventory and tag == "tr" and self.cells:
            if not self.headers:
                self.headers = self.cells
            elif self.in_body:
                self.rows.append(self.cells)
                self.flags.append(self.row_flags)
                self.details.append(self.row_details)
            self.cells = None
        if tag == "tbody":
            self.in_body = False
        if tag == "table" and self.inventory:
            self.inventory = False


class AgreementHTML(HTMLParser):
    """Read explicit qualification checkboxes in public agreement details."""

    LABELS = {
        "First cycle/Bachelor/Undergraduate": "Bachelor",
        "Second cycle/Master/Postgraduate": "Master",
        "Third cycle/Phd/Doctoral": "Doctoral",
        "Jahresplätze": "annual",
        "Semesterplätze": "semester",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.mode = None
        self.parts = []
        self.images = []
        self.pending = None
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "td":
            css = attributes.get("class", "").split()
            self.mode = (
                "label"
                if "colLabel" in css
                else "value" if "dispCol" in css else None
            )
            self.parts = []
            self.images = []
        if tag == "img" and self.mode == "value":
            self.images.append(
                urllib.parse.urlsplit(attributes.get("src", "")).path
            )

    def handle_data(self, data):
        if self.mode:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != "td":
            return
        if self.mode == "label":
            label = " ".join("".join(self.parts).split())
            self.pending = self.LABELS.get(label)
        elif self.mode == "value" and self.pending:
            if (
                len(self.images) != 1
                or self.images[0]
                not in (
                    "/mobility/images/haken3.gif",
                    "/mobility/images/cancel.gif",
                )
                or self.pending in self.values
            ):
                raise AdapterError(
                    "Ambiguous agreement qualification indicator"
                )
            self.values[self.pending] = self.images[0].endswith("/haken3.gif")
            self.pending = None
        self.mode = None


def agreement_signature(links, delay):
    details = [urllib.parse.urljoin(PORTAL, link) for link in links]
    if len(details) != 1:
        raise AdapterError(
            "Expected one public bilateral agreement detail link"
        )
    url = details[0]
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "mobility.univie.ac.at"
        or parsed.path != "/mobility/DispSearchDetailServlet"
    ):
        raise AdapterError("Unexpected public agreement endpoint", "access")
    page = AgreementHTML()
    page.feed(fetch_source(url, min_interval=delay))
    page.close()
    if set(page.values) != set(AgreementHTML.LABELS.values()):
        raise AdapterError(
            "Agreement study-cycle or duration indicators missing"
        )
    signature = "|".join(
        key + "=" + str(page.values[key]) for key in sorted(page.values)
    )
    description = "; ".join(key for key, value in page.values.items() if value)
    if not description:
        raise AdapterError(
            "Agreement has no explicit supported study cycle/duration"
        )
    return signature, description


def parse_page(url, delay):
    page = InventoryHTML()
    page.feed(fetch_source(url, min_interval=delay))
    page.close()
    return page


@contextlib.contextmanager
def collection_lock():
    """Serialize same-source runs, including a pacing gap between processes."""
    path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
    with open(path, "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            time.sleep(6)
            yield
        finally:
            time.sleep(6)
            fcntl.flock(lock, fcntl.LOCK_UN)


def legacy_collect():
    with collection_lock():
        return collect_inventory()


def source_row_closure(flags, checked):
    """Quote an own-row current refusal with a published past period."""
    if not isinstance(flags, list) or len(flags) != 1:
        return None
    text = flags[0]
    if not isinstance(text, str):
        return None
    match = re.fullmatch(
        r"Application is not possible at this moment! "
        r"The application period was "
        r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) - "
        r"(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})",
        text,
    )
    if match is None:
        return None
    try:
        start, end = (
            datetime.strptime(value, "%d.%m.%Y %H:%M:%S")
            for value in match.groups()
        )
        now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # The publisher says unavailable NOW and period WAS. The calendar check
    # guards future/conflicting text; it never assigns the unzoned clock a zone.
    if now.tzinfo is None or start > end:
        return None
    if end.date() > now.astimezone(timezone.utc).date():
        return None
    return "Publisher own-row closure: " + text


def source_record_closed(record, checked):
    """Require the generated proof and this allocation's own table scope."""
    classification = record.get("classification", {})
    evidence = classification.get("evidence", [])
    if (
        record.get("source") != SOURCE_ID
        or record.get("source_url") != SOURCE_URL
        or record.get("status") != "expired"
        or classification.get("method") != "publisher-listing"
        or classification.get("status") != "classified"
        or len(evidence) != 3
        or evidence[0] != SOURCE_URL
    ):
        return False
    prefix = "Publisher own-row closure: "
    if not evidence[2].startswith(prefix):
        return False
    if source_row_closure([evidence[2][len(prefix):]], checked) != evidence[2]:
        return False
    table = urllib.parse.urlsplit(evidence[1])
    allocation = urllib.parse.urlsplit(record.get("url", ""))
    portal = urllib.parse.urlsplit(PORTAL)
    if (
        table[:3] != portal[:3]
        or allocation[:3] != portal[:3]
        or table.fragment
        or allocation.fragment
    ):
        return False
    table_query = urllib.parse.parse_qs(table.query, keep_blank_values=True)
    allocation_query = urllib.parse.parse_qs(
        allocation.query, keep_blank_values=True
    )
    if table_query.get("inst_id_partner") != ["0"]:
        return False
    partner = allocation_query.get("inst_id_partner", [])
    if len(partner) != 1 or partner[0] in ("", "0", "-1"):
        return False
    allocation_query["inst_id_partner"] = ["0"]
    return table_query == allocation_query


def collect_inventory():
    delay = check_robots(SOURCE_URL)
    programme = parse_page(SOURCE_URL, delay)
    text = " ".join(" ".join(programme.text).split())
    if "Non-EU Student Exchange Program" not in text or "students" not in text:
        raise AdapterError("Programme identity or eligibility changed")
    links = sorted(
        {url for url in programme.links if url.startswith(PORTAL + "?")}
    )
    if len(links) != 4:
        raise AdapterError(
            "Expected four official regional exchange inventories"
        )
    records = []
    portal_delay = check_robots(links[0])
    for url in links:
        page = parse_page(url, portal_delay)
        years = page.options.get("studj_id", {})
        valid_years = {
            key: value
            for key, value in years.items()
            if re.fullmatch(r"20\d{2}/20\d{2}", value)
        }
        if not valid_years:
            raise AdapterError("No published academic years in inventory")
        year_id = max(valid_years, key=valid_years.get)
        year = valid_years[year_id]
        source_query = dict(
            urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query)
        )
        if source_query.get("studj_id") != year_id:
            source_query["studj_id"] = year_id
            page = parse_page(
                PORTAL + "?" + urllib.parse.urlencode(source_query),
                portal_delay,
            )
        homes = page.options.get("inst_id_heim", {})
        if len(homes) != 1 or next(iter(homes.values())) != "Universität Wien":
            raise AdapterError("University of Vienna form identity changed")
        partners = {
            name: key
            for key, name in page.options.get("inst_id_partner", {}).items()
            if key not in ("0", "-1")
        }
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        query.update(
            studj_id=year_id,
            inst_id_heim=next(iter(homes)),
            readNew="0",
            kz_search="A",
            org_id="31",
            lcd_id="0",
            inst_id_partner="0",
        )
        result_url = PORTAL + "?" + urllib.parse.urlencode(query)
        if not robot_allowed(result_url):
            raise AdapterError("Unexpected public form destination", "access")
        listing = parse_page(result_url, portal_delay)
        expected = [
            "",
            "",
            "Home institution",
            "Host country",
            "Partner institution",
            "Study field",
            "Program",
            "Teaching language",
            "Total number",
            "Qty. free spots",
            "Coordinator",
        ]
        if listing.headers != expected:
            raise AdapterError(
                "Exchange table schema changed: " + str(listing.headers)
            )
        inventory_text = " ".join(" ".join(page.text).split())
        count = re.search(r"(\d+) Exchange possibilities in", inventory_text)
        if count is None or int(count.group(1)) != len(listing.rows):
            raise AdapterError(
                "Regional inventory count differs from full table"
            )
        duplicate_keys = Counter((row[4], row[5]) for row in listing.rows)
        for cells, flags, detail_links in zip(
            listing.rows, listing.flags, listing.details
        ):
            if (
                len(cells) != len(expected)
                or cells[2] != "University of Vienna"
            ):
                raise AdapterError("Malformed or foreign exchange allocation")
            country, partner, field, programme_name = cells[3:7]
            if country not in COUNTRIES or partner not in partners:
                raise AdapterError(
                    "Unmapped country or partner: " + country + " / " + partner
                )
            if not all(re.fullmatch(r"-?\d+", value) for value in cells[8:10]):
                raise AdapterError("Exchange-place quantities changed")
            partner_query = {**query, "inst_id_partner": partners[partner]}
            partner_url = PORTAL + "?" + urllib.parse.urlencode(partner_query)
            record = make_record(
                partner + " — " + programme_name + " (" + year + ")",
                partner_url,
                ["training"],
                tags=["student-exchange"],
                host_countries=[COUNTRIES[country]],
                method="publisher-listing",
                evidence=[SOURCE_URL, result_url],
            )
            # URLs identify a partner; distinct study-field allocations
            # retain distinct IDs.
            identity = "|".join(
                [
                    SOURCE_ID,
                    query["aust_prog_id"],
                    year_id,
                    partners[partner],
                    field,
                ]
            )
            qualification = ""
            if partner == "University of Ottawa":
                signature, qualification = agreement_signature(
                    detail_links, portal_delay
                )
                identity += "|" + signature
                record["title"] += " — " + qualification
            elif duplicate_keys[(partner, field)] > 1:
                raise AdapterError(
                    "New ambiguous partner allocations require identity review"
                )
            record["id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
            record["summary"] = (
                "University of Vienna outgoing student exchange. "
                f"Academic year: {year}; "
                f'study field: {field or "not specified"}; '
                f"total places: {cells[8]}; free places: {cells[9]}; "
                f'teaching language: {cells[7] or "not specified"}.'
            )
            if qualification:
                record["summary"] += (
                    " Study cycles/duration: " + qualification + "."
                )
            record["location"] = country
            record["attribution"] = ATTRIBUTION
            record["status"] = (
                "open"
                if "Application currently possible" in flags
                and int(cells[9]) > 0
                else "unknown"
            )
            closure = source_row_closure(flags, utc_now())
            if closure is not None:
                record["status"] = "expired"
                record["classification"]["evidence"].append(closure)
            records.append(record)
    validate_records(records)
    return records


def robot_allowed(url):
    """Keep forms on the reviewed endpoint; check robots once per host."""
    return url.startswith(PORTAL + "?")


def remove_expired_records(records, checked):
    """Filter the validated replacement, leaving prior publication untouched."""
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise AdapterError("Cleanup clock requires a timezone", "validate")
    current = []
    for record in records:
        deadline = record.get("deadline")
        expired = source_record_closed(record, checked)
        if isinstance(deadline, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                # Without a documented zone, wait for worldwide day closure.
                expired = (
                    datetime.strptime(deadline, "%Y-%m-%d").date()
                    < now.astimezone(timezone(timedelta(hours=-12))).date()
                )
            else:
                instant = datetime.fromisoformat(
                    deadline.replace("Z", "+00:00")
                )
                expired = instant.tzinfo is not None and instant < now
        if not expired:
            current.append(record)
    # Valid nonempty retrieval may have no remaining eligible records.
    validate_records(current, allow_empty=True)
    return current


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
        not failure
        or (snapshot is not None
            and snapshot["files"]["data.json"] is not None)
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
                    "Publication failed; durable failure status "
                    "could not be recorded. "
                    "Remote data was not force-overwritten."
                )
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"


FAMILY = "youthopps-at-university-of-vienna-publisher-v1"


FAMILY_SOURCES = {SOURCE_ID}


_WORKFLOW_ID = 378390048


_ARTIFACT_NAME = "at-university-of-vienna-pacing-state"


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


_JOURNAL_CREATED = _JOURNAL_DIRTY = False


_JOURNAL_LOCKS = {}


_ACQUIRED_IDS = {}


_REP_PROVENANCE = {}


def nonexport_end():
    """Reserve export within the unchanged thirty-minute job."""
    if _RUN_END is None:
        raise AdapterError("Frozen execution deadline required", "access")
    return _RUN_END - EXPORT_TIMEOUT


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
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
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
        verify_journal()
        raw = os.pread(_JOURNAL_FD, 32769, 0)
        verify_journal()
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
            "University of Vienna state persistence is already unhealthy",
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
                "Unproved University of Vienna completion write", "access"
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
            if type(count) is not int or not 0 < count <= len(payload) - offset:
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
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=_STATE_DIR_FD
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
    """Read real public family metadata using existing authentication."""
    token = os.environ.get("GITHUB_TOKEN", "")
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
            "Bounded University of Vienna execution required", "access"
        )
    return _PHASE_END if _PHASE_END is not None else nonexport_end()


def check_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded University of Vienna execution required", "access"
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
    result = workflow_api(
        "/actions/workflows/fetch-at-university-of-vienna.yml"
    )
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-at-university-of-vienna.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "University of Vienna workflow is not serialized for this mode",
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
            "fetch-at-university-of-vienna.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable University of Vienna workflow head",
                "access",
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("VIENNA_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native University of Vienna MAX approval required",
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
                r"https://github\.com/YouthOpps/data-pipeline/issues/320"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native University of Vienna approval", "access"
        ) from None


def restored_state():
    """Newest exact artifact is mandatory after the one reviewed legacy set."""
    latest = latest_family_run()
    if latest is None:
        raise AdapterError(
            "University of Vienna family history missing", "access"
        )
    if all(
        str(row[2]) in _LEGACY_JOB_PINS
        and row[3] == _LEGACY_JOB_PINS[str(row[2])]["attempt"]
        and row[5] == _LEGACY_JOB_PINS[str(row[2])]["head"]
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
            "Incomplete University of Vienna artifact inventory", "access"
        )
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest University of Vienna artifact unavailable", "access"
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
            "Invalid University of Vienna artifact binding", "access"
        )
    return production_maintenance_state(
        download_inert_artifact(latest, artifact["id"], artifact["digest"])
    )


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    global _COLLECTION_LOCK_ID
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    workflow_mode_guard()
    current_job_context()
    incoming = restored_state()
    # No directory, lock or empty durable budget exists before reviewed state.
    if incoming["in_flight"]:
        raise AdapterError(
            "Owned University of Vienna artifact is unsettled", "access"
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
                "Local University of Vienna flight is unsettled", "access"
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
            return collect_inventory()
    except PhaseExpired:
        raise AdapterError(
            "University of Vienna collection phase exhausted", "fetch"
        ) from None


def export_family_artifact():
    path = os.environ.get("VIENNA_PACING_ARTIFACT_PATH")
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
        or os.environ.get("GITHUB_JOB") != "collect"
    ):
        raise AdapterError(
            "University of Vienna current job is not executing", "access"
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
            "University of Vienna current job inventory incomplete", "access"
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
            "University of Vienna current job ownership mismatch", "access"
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
            "Untrusted University of Vienna job clock", "access"
        ) from None
    _RUN_END = min(_RUN_END, time.monotonic() + 1800 - max(elapsed, 0) - 2)
    check_deadline()


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
        ready = max(state["not_before"], state["completed"] + state["interval"])
        if ready + 120 > now + (active_deadline() - time.monotonic()):
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
            wait = max(0, target - time.time())
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
        ) in {
            "international.univie.ac.at",
            "mobility.univie.ac.at",
        }
    except (TypeError, ValueError):
        return False


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not is_publisher(value)
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError("Unreviewed Vienna source route", "access")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def is_inventory(url):
    """All discovery and regional form attempts, excluding Ottawa details."""
    parsed = urllib.parse.urlsplit(url)
    return (
        (parsed.hostname or "").lower().removeprefix("www.")
        == "international.univie.ac.at"
        and parsed.path == urllib.parse.urlsplit(SOURCE_URL).path
        or (parsed.hostname or "").lower().removeprefix("www.")
        == "mobility.univie.ac.at"
        and parsed.path == "/mobility/MobilitySearchServlet"
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
            raise AdapterError("Infrastructure response exceeds bound", "fetch")
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
        if deadline is not None or retry_jitter:
            raise AdapterError("Unexpected publication options", "publish")
        if _RUN_END is None:
            return legacy_request_bytes(
                url,
                interval=interval,
                method=method,
                headers=headers,
                payload=payload,
            )
        with operation_guard():
            return legacy_request_bytes(
                url,
                interval=interval,
                method=method,
                headers=headers,
                payload=payload,
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
    with publisher_attempt(inventory) as frame, operation_guard():
        if not robots:
            apply_robots_policy(current, _ROBOTS[origin])
        if not _LOCAL_FIRST_WIRE_VERIFIED:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                workflow_mode_guard()
            _LOCAL_FIRST_WIRE_VERIFIED = True
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
        with response:
            status, response_headers = response.status, response.headers
            record_retry_after(response_headers.get("Retry-After"), status)
            if not robots and status in (401, 403, 451):
                retain_publisher_hold()
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            length = response_headers.get("Content-Length")
            if length and (not length.isdigit() or int(length) != len(body)):
                raise AdapterError("Incomplete source response length", "fetch")
            if not robots or status in (200, 429):
                reject_visible_challenge(
                    body, response_headers, challenge_only=status == 429
                )
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
    return max(finished), min(began), *identity


def scan_family_history():
    global _HISTORY_COMPLETED, _HISTORY_JOBLESS
    _HISTORY_JOBLESS = []
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    candidates, seen = [], set()
    total, scanned, current_seen = None, 0, False

    def remember(run, expected=None):
        row = completed_identity(run, expected)
        if row is not None:
            candidates.append(row)

    for page in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-at-university-of-vienna.yml/runs"
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
                if not hosted or run.get("status") not in {
                    "queued",
                    "waiting",
                    "pending",
                    "requested",
                }:
                    raise AdapterError(
                        "Another family collector is active", "access"
                    )
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    remember(prior, (identity, attempt - 1, source, head))
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


def legacy_transition_state():
    approval = (
        _HOSTED_LEGACY_TRANSITION
        if os.environ.get("GITHUB_ACTIONS") == "true"
        else _LOCAL_LEGACY_TRANSITION
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/320"
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
        raise AdapterError("Legacy complete conservative MAX missing", "access")
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
    """RAM ancestry is explicitly unknown; never fabricate old timestamps."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    expected = {
        "mode": "hosted" if hosted else "local",
        "status": "ram_history_unknown",
    }
    if packet != expected or set(packet) != set(expected):
        raise AdapterError("Explicit Vienna RAM ancestry required", "access")
    if hosted and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise AdapterError("Fresh hosted context required", "access")
    return None


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
    path = os.environ.get("VIENNA_PACING_ARTIFACT_PATH")
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


_LOCAL_LEGACY_TRANSITION = {
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/320#issuecomment-6209897139",
    "family": "youthopps-at-university-of-vienna-publisher-v1",
    "history_sha256": "ecb6be0b8a5e6d7f18f84a56ac5e7cbef273d8a4b7f89609b85bfac52c7d55ab",
    "legacy_local": {"mode": "local", "status": "ram_history_unknown"},
    "schema": 1,
    "state": {
        "blocked": False,
        "completed": 1792092308.0,
        "completed_flight_id": None,
        "completed_request_id": None,
        "family": "youthopps-at-university-of-vienna-publisher-v1",
        "flight_id": None,
        "flight_kind": None,
        "flight_started": 0,
        "in_flight": False,
        "interval": 12.2,
        "last_inventory_completed": 1792092308.0,
        "legacy_policy_history_unknown": True,
        "legacy_rate_history_unknown": True,
        "not_before": 1792133452.9402816,
        "observed_at": 1792133392.9402816,
        "page_interval": 30,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "request_id": None,
        "request_inventory": False,
        "request_outcome": None,
        "request_started": 0,
        "schema": 3,
        "starts": [
            1791478882.0,
            1791481560.0,
            1791654900.0,
            1791966289.0,
            1792046074.0,
            1792060672.0,
            1792067264.0,
            1792079517.0,
            1792092308.0,
        ],
    },
}
_HOSTED_LEGACY_TRANSITION = {
    "schema": 1,
    "family": "youthopps-at-university-of-vienna-publisher-v1",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/320#issuecomment-6210831417",
    "history_sha256": "ecb6be0b8a5e6d7f18f84a56ac5e7cbef273d8a4b7f89609b85bfac52c7d55ab",
    "state": {
        "blocked": False,
        "completed": 1792136358.766172,
        "completed_flight_id": "cbfa6256fbf8586b815de9eff6308063",
        "completed_request_id": "54067ece60f8fb4f98a1ed140a1af45f",
        "family": "youthopps-at-university-of-vienna-publisher-v1",
        "flight_id": "cbfa6256fbf8586b815de9eff6308063",
        "flight_kind": "policy",
        "flight_started": 1792136345.146338,
        "in_flight": False,
        "interval": 12.2,
        "last_inventory_completed": 1792136345.1179779,
        "legacy_policy_history_unknown": True,
        "legacy_rate_history_unknown": True,
        "not_before": 1792137291.0558329,
        "observed_at": 1792137231.0558329,
        "page_interval": 30,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "request_id": "54067ece60f8fb4f98a1ed140a1af45f",
        "request_inventory": False,
        "request_outcome": "response_assessed",
        "request_started": 1792136357.3239512,
        "schema": 3,
        "starts": [
            1791478882.0,
            1791481560.0,
            1791654900.0,
            1791966289.0,
            1792046074.0,
            1792060672.0,
            1792067264.0,
            1792079517.0,
            1792092308.0,
            1792136018.6598876,
            1792136019.16775,
            1792136019.3479404,
            1792136020.2387743,
            1792136020.2410235,
            1792136032.4426184,
            1792136032.4467227,
            1792136032.77915,
            1792136033.2420764,
            1792136034.7619298,
            1792136046.9798615,
            1792136046.9863496,
            1792136047.1833787,
            1792136048.1914535,
            1792136048.194307,
            1792136064.7683053,
            1792136064.7708163,
            1792136064.95483,
            1792136066.0348969,
            1792136066.387729,
            1792136096.4242845,
            1792136096.436343,
            1792136096.635696,
            1792136101.7248423,
            1792136102.6809394,
            1792136132.6914647,
            1792136132.6936555,
            1792136132.8777528,
            1792136134.1582372,
            1792136134.515467,
            1792136164.5166674,
            1792136164.5185094,
            1792136164.6939008,
            1792136168.6105626,
            1792136168.9817142,
            1792136181.1875184,
            1792136181.194956,
            1792136181.3983078,
            1792136182.3630636,
            1792136182.5291975,
            1792136194.7325683,
            1792136194.73413,
            1792136194.907255,
            1792136195.8593402,
            1792136196.0235257,
            1792136208.226069,
            1792136208.2279055,
            1792136208.3963532,
            1792136209.9051058,
            1792136210.3955936,
            1792136240.3992643,
            1792136240.4022098,
            1792136240.4247386,
            1792136241.4973407,
            1792136241.8182187,
            1792136271.8202226,
            1792136271.8222222,
            1792136271.8377733,
            1792136273.6264162,
            1792136273.993367,
            1792136303.996577,
            1792136303.9993446,
            1792136304.1839387,
            1792136305.2712693,
            1792136305.624564,
            1792136335.754154,
            1792136337.215474,
            1792136338.086361,
            1792136344.6151748,
            1792136345.1151264,
            1792136357.3239512,
            1792136357.3261197,
            1792136357.659733,
            1792136358.5982807,
            1792136358.7646093,
        ],
    },
    "legacy_local": {"mode": "hosted", "status": "ram_history_unknown"},
}
_LEGACY_JOB_PINS = {
    "37813016141": {
        "attempt": 1,
        "conclusion": "success",
        "head": "4040252d1b74f80e1097aae1e8a086c45edd3db0",
        "jobs": [
            {
                "completed_at": "2026-10-08T17:01:22Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "4040252d1b74f80e1097aae1e8a086c45edd3db0",
                "id": 113434425752,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 37813016141,
                "runner_id": 1000001370,
                "started_at": "2026-10-08T16:59:02Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "37818515076": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9ab03a510dba38548ed53dd378c797be03547bdf",
        "jobs": [
            {
                "completed_at": "2026-10-08T17:46:00Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "9ab03a510dba38548ed53dd378c797be03547bdf",
                "id": 113454164018,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 37818515076,
                "runner_id": 1000001372,
                "started_at": "2026-10-08T17:43:40Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38071840272": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "jobs": [
            {
                "completed_at": "2026-10-10T17:55:00Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
                "id": 114275646759,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38071840272,
                "runner_id": 1000001751,
                "started_at": "2026-10-10T17:52:47Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38534647004": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5146041d2628fb6f03c28d7ee7a2d58c3db8f443",
        "jobs": [
            {
                "completed_at": "2026-10-14T08:24:49Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "5146041d2628fb6f03c28d7ee7a2d58c3db8f443",
                "id": 115754463379,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38534647004,
                "runner_id": 1000002128,
                "started_at": "2026-10-14T08:22:30Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38684235882": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "completed_at": "2026-10-15T06:34:34Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "id": 116283008700,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38684235882,
                "runner_id": 1000002306,
                "started_at": "2026-10-15T06:32:20Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38720084032": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs": [
            {
                "completed_at": "2026-10-15T12:27:44Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                "id": 116420300811,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38720084032,
                "runner_id": 1000002419,
                "started_at": "2026-10-15T12:25:29Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38721259400": {
        "attempt": 1,
        "conclusion": "success",
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs": [
            {
                "completed_at": "2026-10-15T10:37:52Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                "id": 116376105292,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38721259400,
                "runner_id": 1000002361,
                "started_at": "2026-10-15T10:34:45Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38763419595": {
        "attempt": 1,
        "conclusion": "success",
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs": [
            {
                "completed_at": "2026-10-15T15:51:57Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                "id": 116520331630,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38763419595,
                "runner_id": 1000002527,
                "started_at": "2026-10-15T15:48:59Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
    "38793418542": {
        "attempt": 1,
        "conclusion": "success",
        "head": "14502a6d0faae2b93c7d713b5df101d3d53d7dad",
        "jobs": [
            {
                "completed_at": "2026-10-15T19:25:08Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "14502a6d0faae2b93c7d713b5df101d3d53d7dad",
                "id": 116623542157,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38793418542,
                "runner_id": 1000002599,
                "started_at": "2026-10-15T19:22:53Z",
                "status": "completed",
                "workflow_name": "fetch-at-university-of-vienna",
            }
        ],
    },
}


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
