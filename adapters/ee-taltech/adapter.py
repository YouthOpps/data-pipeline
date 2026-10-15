"""Collect the complete public TalTech Development Fund seasonal inventory."""

import argparse
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import email.utils
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
import stat
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

SOURCE_ID = "ee-taltech"
SOURCE_URL = "https://taltech.ee/arengufond/stipendiumid/2026-sugis"
WEBSITE_URL = "https://taltech.ee/en/scholarships"
LANGUAGE = "et/en"
PUBLISHER_COUNTRY = "EE"
PUBLISHER_TYPE = "university"
ATTRIBUTION = "Tallinn University of Technology Development Fund"


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




def normalized(value):
    """Normalize typographic whitespace without changing award conditions."""
    return unicodedata.normalize("NFC", re.sub(r"\s+([,.;:])", r"\1", value))


def calendar_deadline(year, month, day):
    """Retain the publisher's calendar date without inventing a clock."""
    return datetime(year, month, day).date().isoformat()


def first_statute(body):
    """Use the current listed award statute, not older additional PDF links."""
    links = [
        n.attrs.get("href", "")
        for n in nodes(body, "a")
        if urllib.parse.urlsplit(n.attrs.get("href", ""))
        .path.lower()
        .endswith(".pdf")
    ]
    if not links:
        raise AdapterError("Award lacks its original statute link", "parse")
    url = urllib.parse.urlsplit(links[0])
    if (
        url.scheme != "https"
        or url.hostname != "haldus.taltech.ee"
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise AdapterError("Unreviewed award statute URL", "parse")
    return links[0]


def award_summary(body):
    """Retain the award description instead of application boilerplate."""
    paragraphs = [n.text() for n in nodes(body, "p") if n.text()]
    meaningful = [
        text
        for text in paragraphs
        if "eraldi arvestuses" not in text
        and not text.startswith("Tingimuste kohta")
    ]
    if not meaningful:
        raise AdapterError("Award description missing", "parse")
    summary = meaningful[0]
    if len(summary) > 600:
        summary = summary[:599].rsplit(" ", 1)[0] + "…"
    return summary


def parse_inventory(root):
    """Read every scholarship card, excluding the publisher's FAQ section."""
    main = one(nodes(root, "main"), "publisher main content")
    headings = nodes(main, "h2", "subtitle")
    cycle = one(
        [
            n
            for n in headings
            if re.fullmatch(r"STIPENDIUMID 20\d{2} SÜGIS", n.text())
        ],
        "dated scholarship cycle",
    ).text()
    year = int(re.search(r"20\d{2}", cycle)[0])
    schedule = re.search(
        r"30\. september\s*[-–]\s*19\. oktoober\s*[-–]\s*"
        r"stipendiumitele kandideerimine",
        main.text(),
    )
    if not schedule:
        raise AdapterError(
            "Reviewed main application calendar changed", "parse"
        )
    general_deadline = calendar_deadline(year, 10, 19)
    cards, section, faq_count = [], "", 0
    for node in main.walk():
        if (
            node.tag == "h2"
            and "subtitle" in node.attrs.get("class", "").split()
        ):
            section = node.text()
        elif (
            node.tag == "div" and "card" in node.attrs.get("class", "").split()
        ):
            if section == "KKK":
                faq_count += 1
                continue
            if not section or section == cycle:
                raise AdapterError("Unscoped publisher accordion card", "parse")
            button = one(nodes(node, "button"), "award heading")
            body = one(nodes(node, "div", "collapse"), "award body")
            title, text = button.text(), body.text()
            if not re.search(r"stipend|scholarship", title, re.I) or not text:
                raise AdapterError("Non-opportunity award card", "parse")
            anchor = button.attrs.get("id", "")
            if not re.fullmatch(r"p\d+_\d+", anchor):
                raise AdapterError("Award anchor changed", "parse")
            statute = first_statute(body)
            special = section == "Erikonkurss" or "erikonkurss" in title.lower()
            override = re.search(
                r"kandideerimine kuni (\d{1,2})\.(\d{1,2})", title, re.I
            )
            deadline = (
                calendar_deadline(year, int(override[2]), int(override[1]))
                if override
                else None if special else general_deadline
            )
            categories = ["scholarships"]
            if "PRAKTIKA" in title:
                categories.append("internships")
            record = make_record(
                title.removeprefix("UUS! "),
                statute,
                categories,
                host_countries=["EE"],
                method="publisher-award-html",
                evidence=[SOURCE_URL + "#" + anchor, statute],
            )
            key = unicodedata.normalize("NFC", urllib.parse.unquote(statute))
            record["id"] = hashlib.sha256(
                f"{SOURCE_ID}|{cycle}|{key}".encode()
            ).hexdigest()[:24]
            record.update(
                summary=award_summary(body),
                deadline=deadline,
                tags=[cycle, section],
                eligible_countries=(
                    ["EE"]
                    if re.search(
                        r"Eesti (?:Vabariigi )?"
                        r"(?:kodakondsus(?:ega|t)|kodanik)\b",
                        text,
                    )
                    else []
                ),
                status=(
                    "expired"
                    if deadline
                    and datetime.now(timezone.utc).date()
                    > datetime.strptime(deadline, "%Y-%m-%d").date()
                    else (
                        "open"
                        if deadline
                        and datetime.now(timezone.utc).date()
                        >= datetime(year, 9, 30).date()
                        else "unknown"
                    )
                ),
            )
            cards.append((record, normalized(text)))
    if not cards or not faq_count:
        raise AdapterError("Incomplete scholarship/FAQ inventory", "parse")
    records, descriptions = {}, {}
    for record, text in cards:
        key = record["id"]
        if key not in records:
            records[key], descriptions[key] = record, text
            continue
        prior = records[key]
        if descriptions[key] != text or any(
            prior[field] != record[field]
            for field in ("deadline", "categories", "eligible_countries")
        ):
            raise AdapterError(
                "Repeated statute has conflicting allocation or eligibility",
                "parse",
            )
        prior["classification"]["evidence"] = list(
            dict.fromkeys(
                prior["classification"]["evidence"]
                + record["classification"]["evidence"]
            )
        )
        prior["tags"] = list(dict.fromkeys(prior["tags"] + record["tags"]))
        if len(record["title"]) > len(prior["title"]):
            prior["title"] = record["title"]
    print(
        f"TalTech: {len(cards)} published award cards, "
        f"{faq_count} FAQs excluded, {len(records)} distinct awards",
        file=sys.stderr,
    )
    return list(records.values())




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
        .isoformat(timespec="microseconds")
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
    """Filter validated deadlines without guessing missing semantics."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    retained = []
    for record in records:
        deadline = record["deadline"]
        if deadline is None:
            retained.append(record)
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing_date = datetime.strptime(deadline, "%Y-%m-%d").date()
            expired = checked.astimezone(timezone.utc).date() > closing_date
        else:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            expired = checked > closing
        if not expired:
            retained.append(record)
    return retained


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
                    if field == "deadline" and re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        datetime.strptime(value, "%Y-%m-%d")
                        continue
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
                    "Source changed concurrently; "
                    "newer remote data was not overwritten",
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
    result = {
        "display_name": "TalTech Development Fund",
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
            else f"Collected and validated {len(records)} records"
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


FAMILY = "youthopps-ee-taltech-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_ID = 378390324
_ARTIFACT_NAME = "taltech-pacing-state"
COLLECTION_TIMEOUT = 1350
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 30
_STATE_PATH = _STATE_LOCK = None
_JOURNAL_NEW = False
_COLLECTION_LOCK_FD = None
_RESTORED = _STATE_TRUSTED = False
_PUBLISHER_GATE_FAILED = False
_LAST_KNOWN_STATE = None
_JOURNAL_LOCKS = {}
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
    "source": "ee-taltech",
    "sha256": "76dad01d658037e62fc8cb024a7f4a9ffe1a721f479996e375127678860f74bd",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/271#issuecomment-6202700226",
}
_REVIEWED_NATIVE_MIGRATION = {
    "schema": 1,
    "source": "ee-taltech",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/271#issuecomment-6203012520",
    "state": {
        "schema": 3,
        "family": "youthopps-ee-taltech-publisher-v1",
        "legacy_hold_history": "unlogged_fields_unknown",
        "research_provenance": {
            "all28_actual_log_hash_manifest_sha256": "ed6b21a06baf2351ef7b5f58664776c8a4ec83089a4887e8784ea7aa01d4b435",
            "complete_history_manifest_sha256": "f6e9fb7c7f1caa9dd00fb7867f025afad92c3d58827118b48db7f7f046d14e99",
            "exact_cancelled_no_job_count": 2,
            "executed_completion_upper_bounds": 28,
            "in_flight_legacy_semantics": "Unknown "
            "in "
            "legacy "
            "schema; "
            "False "
            "proposed "
            "ONLY as "
            "an "
            "explicit "
            "one-off "
            "reviewed "
            "transition, "
            "no "
            "old-schema "
            "default",
            "kind": "explicit_unknown_legacy_transition",
            "legacy_429_history": "unknown",
            "legacy_hold_history": "unlogged_fields_unknown",
            "legacy_wrapper_count": 30,
            "old_local_journal": "ABSENT_NOT_ZERO",
            "scope": "Completion timestamps are "
            "conservative known bounds, "
            "not physical starts; no "
            "observed legacy publisher "
            "refusal in actual outcome "
            "logs; absent "
            "Retry-After/header history "
            "remains unknown",
            "source": "ee-taltech",
        },
        "starts": [
            1791483333,
            1791483383,
            1791502417,
            1791528158,
            1791570670,
            1791586441,
            1791623472,
            1791656518,
            1791656567,
            1791669605,
            1791700080,
            1791725542,
            1791757804,
            1791786771,
            1791821220,
            1791853308,
            1791902211,
            1791933022,
            1791955444,
            1791967927,
            1791980635,
            1791999549,
            1792015305,
            1792041625,
            1792049335,
            1792060790,
            1792062791,
            1792079785,
            1792100418.0153387,
            1792100434.5584848,
            1792100434.806107,
            1792100436.090769,
            1792100448.2909253,
            1792100448.2930193,
            1792100448.4906554,
            1792100449.9943748,
        ],
        "interval": 12.2,
        "until": 1792100510.2500815,
        "page_gap": 30,
        "completed": 1792100449.9966803,
        "inventory_completed": 1792100449.9966803,
        "blocked": False,
        "in_flight": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": 1792100449.9973006,
    },
    "state_sha256": "1ff97ee889e759c0928d0b094b71c6e280e962e8e99ec352061ce9e70773cbfe",
}
_LEGACY_HEADS = {
    37822771590: "6429b6913fd6bb208ccabb79105336c7e31e16b7",
    37822782827: "6429b6913fd6bb208ccabb79105336c7e31e16b7",
    37860025574: "ee14fb046bc0bdd2eca39905f8d0869e5f13add6",
    37894725339: "6f1971d163e39e44d46ffd72bec20795c59df105",
    37973734791: "251d29d718aa4d79f4b0b93b8512cb82bb82ce23",
    38001567804: "2d6491ddf2a37047efc7b996f190f648e647c0c6",
    38030646434: "6e5922277de22b96874c23a705a7d838512c6caf",
    38040351237: "c55b8d99378274467f2d9a290967dbe10bc25031",
    38054129430: "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
    38071840482: "9b155b179283c8fa08cf56c1a83a54bd19d51814",
    38072501540: "9d16247e7af0063836678d7fffd3f850c2aead8c",
    38089646316: "10d99f8ef021c092851bf189ff67307865b8760c",
    38117959820: "10d99f8ef021c092851bf189ff67307865b8760c",
    38143376503: "10d99f8ef021c092851bf189ff67307865b8760c",
    38180458326: "10d99f8ef021c092851bf189ff67307865b8760c",
    38212754719: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38274801005: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38329781198: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38414350548: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38482182809: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38516134925: "02792bda730b08eeb1eda86a2cd71af80af3cef7",
    38534756543: "5ab9b0237b7fa2b043fe276ec43f29e493c685ab",
    38563053934: "92235b48ad33b9fa377e0736b689010173d7bfab",
    38607913013: "92235b48ad33b9fa377e0736b689010173d7bfab",
    38643953176: "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
    38684235928: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38685145146: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38720084016: "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
    38721259236: "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
    38763419529: "f77eed642c4544bbeed009440c406064a4f5c6ad",
}
_LEGACY_JOB_DIGESTS = {
    37822771590: (
        "f962dfd1791b745eb0e9bdb0dc55ae0d" "51d21b2e7e86fb51e0728dd523062bb7"
    ),
    37822782827: (
        "62f3477507d3f7e44b42f5a7b5ab024a" "e0470c57c2cf143026d3c1978c244716"
    ),
    37860025574: (
        "2d42c0aafc0acb707761a71c9b73391e" "35ab04e40cec4226e451bd285c8f9e5c"
    ),
    37894725339: (
        "561c7152e6c05f6fb239c4f7ef1de81b" "5515aafb0730a90317cbf80cca5297e2"
    ),
    37973734791: (
        "6a6696570fe6551b42fbdc95d3af5595" "ad31f0a13fb39481cd07d1e2121926bd"
    ),
    38001567804: (
        "b517c7a4320e312c2cbbc321ea412f20" "83a22695901530a9608424f6546f8d06"
    ),
    38030646434: (
        "b512fdf501ffbdb48c1ff8a334798223" "dc87ae5ec6e009140ec9c2633b967b7f"
    ),
    38040351237: (
        "e6257fb056b471c8b1c93c85afc30f60" "6b389b10f7e2b2902c059a7a1318c2f2"
    ),
    38054129430: (
        "b512fdf501ffbdb48c1ff8a334798223" "dc87ae5ec6e009140ec9c2633b967b7f"
    ),
    38071840482: (
        "f75381d4b8597a1544503aa76eed2716" "32f4babb4442601d8568379f4b10ca8f"
    ),
    38072501540: (
        "ea47c633838efa72b0eb30c8ae420af0" "6991cd3f38c65895dffcb0c98fed9623"
    ),
    38089646316: (
        "94b8188e1bd77db866f4791c47803661" "932d1e2446bea62eb03401ca7308aeec"
    ),
    38117959820: (
        "e365ca9d66af146b13c21c89fb26d93d" "d868933ec9973613165f663e12102b7c"
    ),
    38143376503: (
        "bcc8858441c6c130880685d1621104df" "a0aef04962b647c350221b9c40aac04b"
    ),
    38180458326: (
        "d702f6f33e445aaa223d1f0bb89f97a6" "8b1efbe81e28833f7362970cc6c3ce64"
    ),
    38212754719: (
        "3dba97b0fa63e53200c491384bc991e4" "181b00ed1f8c64a8ba3283d405e63468"
    ),
    38274801005: (
        "b135da2978d04db82e09cc16004dae63" "c9416d3d6e051c72c1dcd799aa4e0354"
    ),
    38329781198: (
        "58d0143efb5ebc3828cc71e95d464a2f" "8114b2840309e761a0ab204e7c711a1a"
    ),
    38414350548: (
        "3279e5ae2989b6cefd31ddd33c75b148" "0c4142682f1a23fd52230db78793d97d"
    ),
    38482182809: (
        "0591c6ad3a7691b831b4a0d7fccb1299" "dc7443307b81c37ed76ae81b66df6660"
    ),
    38516134925: (
        "5b288a34a85ddb930d4552d05463aebe" "0b9ed6ee2d5cff1aedb00d74a14fafde"
    ),
    38534756543: (
        "6646f6081d2c54a35e237c259ea13c1e" "f52c9d67d83f9b0d6104a21bbd223c3c"
    ),
    38563053934: (
        "8ac5536c9d1a47b18c6ce8a1f7b87833" "7093f53740afcd73e9f039ea23772110"
    ),
    38607913013: (
        "5551f7f7cb5ecf70eb5da89e1bcc9cba" "bff52b9c3aa6c5cab339d4a8c2984be8"
    ),
    38643953176: (
        "fe4a88e133dc112cd824dd84cea90ec6" "1cc9be6d5fa1e1925ec645499fec21d1"
    ),
    38684235928: (
        "7e6db0468025e124728e6afbf05e6333" "b4b2bb7f4f07fcbe649a530f68844dbe"
    ),
    38685145146: (
        "d0c0d3caa68cfc9d6c353ecd75e4f3aa" "b80c9c178e6e8e62d0bb2f42f5d2d914"
    ),
    38720084016: (
        "8cd2af70fba92cbb59083302d41fd6c3" "565a5cc6c7c5fd1d35661f3c85af9d54"
    ),
    38721259236: (
        "ebebc3a2a74722491b2c8ef1be24e733" "d7a1d3897e25ddf5b2917b324b39804c"
    ),
    38763419529: (
        "c27c99d07f945bde5051da6c2bd2b9b4" "6d3585a00d2f987ed2777afc387e88b4"
    ),
}
_LEGACY_NO_JOB = {
    38030646434: {
        "conclusion": "cancelled",
        "created_at": "2026-10-10T06:21:25Z",
        "head_sha": "6e5922277de22b96874c23a705a7d838512c6caf",
        "jobs_sha256": (
            "b512fdf501ffbdb48c1ff8a334798223"
            "dc87ae5ec6e009140ec9c2633b967b7f"
        ),
        "run_attempt": 1,
    },
    38054129430: {
        "conclusion": "cancelled",
        "created_at": "2026-10-10T13:01:23Z",
        "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "jobs_sha256": (
            "b512fdf501ffbdb48c1ff8a334798223"
            "dc87ae5ec6e009140ec9c2633b967b7f"
        ),
        "run_attempt": 1,
    },
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
        )
    )


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def empty_budget(now):
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


def validate_budget(state):
    """Validate retained constraints without resetting unknown history."""
    if not isinstance(state, dict):
        raise AdapterError("Invalid publisher state", "access")
    expected = set(empty_budget(0))
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


def state_parent(directory=None):
    """Pin the verified parent and refuse replacement of its current path."""
    global _STATE_PARENT_FD, _STATE_PARENT_ID, _STATE_PARENT_PATH
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    directory = directory or (
        os.path.dirname(_STATE_PATH) if _STATE_PATH else _STATE_PARENT_PATH
    )
    if not directory or not os.path.isabs(directory):
        raise AdapterError("Publisher state parent unavailable", "access")
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
            and (
                directory != _STATE_PARENT_PATH or identity != _STATE_PARENT_ID
            )
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
        directory, hashlib.sha256(b"taltech.ee").hexdigest()
    )


def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if (
        _INITIAL_DIRECTORY_PROVEN
        and not _RESTORED
        and (
            os.environ.get("GITHUB_ACTIONS") == "true"
            or _VERIFIED_REMOTE_CREATE is not None
        )
    ):
        try:
            fd = os.open(
                name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent
            )
            _JOURNAL_NEW = True
        except FileExistsError:
            fd = os.open(name, flags, dir_fd=parent)
            _JOURNAL_NEW = False
    else:
        fd = os.open(name, flags, dir_fd=parent)
    try:
        info = validate_state_descriptor(fd, parent, name)
    except BaseException:
        os.close(fd)
        raise
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    file = os.fdopen(fd, "r+")
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        return BudgetLock(file, info)
    except BaseException:
        file.close()
        raise


def load_budget():
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    fd = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    with os.fdopen(fd, "rb") as file:
        validate_state_descriptor(file.fileno(), parent, name)
        raw = file.read(16385)
        validate_state_descriptor(file.fileno(), parent, name)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher journal", "access")
    if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
        return validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
    if (
        not raw
        and _JOURNAL_NEW
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and not _RESTORED
    ):
        return empty_budget(time.time())
    try:
        value = json.loads(raw)
        return validate_budget(value)
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher journal", "access") from None


def write_budget(state):
    """Latch and invalidate the original journal on persistence failure."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED, _LAST_KNOWN_STATE
    if _LAST_KNOWN_STATE is None:
        _LAST_KNOWN_STATE = copy.deepcopy(state)
    else:
        _LAST_KNOWN_STATE = merge_budget(_LAST_KNOWN_STATE, state)
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        return write_budget_body(state)
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        invalidate_failed_journal()
        raise


class BudgetLock:
    """Retain the validated original journal descriptor until unlock."""

    def __init__(self, file, info):
        self.file = file
        self.descriptor = file.fileno()
        _JOURNAL_LOCKS[self.descriptor] = (info.st_dev, info.st_ino)

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.descriptor is not None:
            _JOURNAL_LOCKS.pop(self.descriptor, None)
            self.descriptor = None
            self.file.close()


def invalidate_failed_journal():
    """Invalidate stale JSON through retained, validated journal FDs."""
    parent = state_parent()
    if not _JOURNAL_LOCKS:
        raise AdapterError(
            "No retained journal descriptor after failure", "access"
        )
    retained = list(_JOURNAL_LOCKS.items())
    for descriptor, identity in retained:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink not in (0, 1)
            or (info.st_dev, info.st_ino) != identity
        ):
            raise AdapterError("Publisher journal identity changed", "access")
    name = os.path.basename(_STATE_PATH)
    current = os.open(
        name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    try:
        validate_state_descriptor(current, parent, name)
        # Atomic replacement may leave the lock on an older safe inode.
        # Invalidate the current validated journal before those old handles.
        for descriptor in [current] + [item[0] for item in retained]:
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
    finally:
        os.close(current)


def write_budget_body(state):
    """Persist one validated state while its original journal lock is held."""
    global _INITIAL_DIRECTORY_PROVEN
    state = validate_budget(state)
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    fd = os.open(
        name, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
    )
    with os.fdopen(fd, "w") as file:
        info = validate_state_descriptor(file.fileno(), parent, name)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise AdapterError("Unsafe publisher journal write", "access")
        file.seek(0)
        json.dump(state, file, separators=(",", ":"))
        file.truncate()
        file.flush()
        _INITIAL_DIRECTORY_PROVEN = False
        os.fsync(file.fileno())
        validate_state_descriptor(file.fileno(), parent, name)


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
    if (
        left["research_provenance"]
        and right["research_provenance"]
        and left["research_provenance"] != right["research_provenance"]
    ):
        raise AdapterError("Research provenance differs across state", "access")
    result["research_provenance"] = (
        left["research_provenance"] or right["research_provenance"]
    )
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
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
            if ready - now + 30 >= remaining():
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


def retain_refusal():
    with locked_budget():
        state = load_budget()
        state["blocked"] = True
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
        datetime.fromtimestamp(until, timezone.utc)
        return until
    except (ValueError, TypeError, OverflowError, OSError, AttributeError):
        return 0


def legacy_transport_pace(url, interval=6):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
    host = (
        (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    )
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


def legacy_request_bytes(
    url, *, interval=6, method="GET", headers=None, payload=None
):
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
                        else email.utils.parsedate_to_datetime(
                            retry
                        ).timestamp()
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
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if type(byte_limit) is not int or not 0 < byte_limit <= 5_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError(
            "Robots purpose requires literal robots.txt", "access"
        )
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    publisher = host == "taltech.ee" or host.endswith(".taltech.ee")
    if not publisher and not infrastructure:
        if byte_limit != 5_000_000:
            raise AdapterError("Invalid legacy transport byte bound", "fetch")
        return legacy_request_bytes(
            url,
            interval=interval,
            method=method,
            headers=headers,
            payload=payload,
        )
    if infrastructure:
        if publisher:
            raise AdapterError(
                "Publisher cannot use metadata purpose", "access"
            )
        infrastructure_pace(url)
    if publisher and (not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        raise AdapterError(
            "Trusted publisher state required before access", "access"
        )
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = pace(interval) if publisher else (None, None)
    attempted = response_received = response_constraints_saved = False
    try:
        if publisher:
            verify_local_publisher_start()
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
            attempted = True
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            response_received = True
            status = response.status
            if infrastructure:
                retain_infrastructure_retry(
                    url, response.headers.get("Retry-After")
                )
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
                response_constraints_saved = not (robots and status == 200)
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
                and not (robots and 400 <= status < 500 and status != 429)
                and visible_access_challenge(body, status == 429)
            ):
                if not robots or status == 429:
                    state["blocked"] = True
                raise AdapterError(
                    "Visible publisher verification or refusal", "access"
                )
            if publisher and robots and status == 200:
                response_constraints_saved = False
                target = (
                    _ROBOTS_CONTEXT["target"]
                    if _ROBOTS_CONTEXT is not None
                    else SOURCE_URL
                )
                text = body.decode(
                    response.headers.get_content_charset() or "utf-8"
                )
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
                    "Publisher429; durable adaptive cooldown retained",
                    "access",
                    429,
                )
            return status, response.headers, body
    finally:
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if (
                    attempted
                    and not _PUBLISHER_GATE_FAILED
                    and (not response_received or response_constraints_saved)
                ):
                    state["in_flight"] = False
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = state["completed"]
                try:
                    write_budget(state)
                except BaseException:
                    _STATE_TRUSTED = False
                    _PUBLISHER_GATE_FAILED = True
                    raise
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
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        local_workflow_definition()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
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
    if _STATE_TRUSTED and os.environ.get("GITHUB_ACTIONS") == "true":
        return
    _LOCAL_FIRST_ACCESS_CHECKED = False
    budget_file()
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
def phase(name, seconds, reserve=EXPORT_TIMEOUT):
    global _PHASE_END, _PHASE_NAME
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
        arm_alarm(limit)


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
            r"https://github\.com/YouthOpps/data-pipeline/issues/271"
            r"#issuecomment-[0-9]+",
            marker.get("evidence", ""),
        )
    ):
        raise AdapterError("Reviewed local legacy transition missing", "access")
    with locked_budget():
        current = load_budget()
        original = json.dumps(
            current["research_provenance"], sort_keys=True
        ).encode()
        if hashlib.sha256(original).hexdigest() != marker["sha256"]:
            raise AdapterError("Local legacy research origin changed", "access")
        return current


def verify_local_publisher_start():
    """Recheck remote continuity after the local startup quiet guard."""
    global _LOCAL_FIRST_ACCESS_CHECKED
    if (
        os.environ.get("GITHUB_ACTIONS") == "true"
        or _LOCAL_FIRST_ACCESS_CHECKED
    ):
        return
    latest = latest_family_run()
    current = (_SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS)
    if latest != _RESTORED_LATEST or current != _RESTORED_HISTORY:
        raise AdapterError(
            "Family history changed before local publisher access", "access"
        )
    local_workflow_definition()
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
            value = datetime.fromisoformat(
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
        value = datetime.fromisoformat(
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
    """Select completed chronology, including older-ID rerun attempts."""
    global _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS
    _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS = {}, {}, {}
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current_id, current_attempt = (
        current_run_identity() if hosted else (None, None)
    )
    if not hosted:
        local_workflow_definition()
    current_seen = False
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        route = (
            "/actions/workflows/fetch-ee-taltech.yml/runs"
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
                    raise AdapterError(
                        "Unexpected local family workflow", "access"
                    )
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
            if hosted and not current_seen:
                raise AdapterError(
                    "Current workflow absent from complete history", "access"
                )
            if not candidates:
                return None
            ordered = sorted(c for c in candidates if c is not None)
            if not ordered:
                raise AdapterError(
                    "No trusted executed family attempt", "access"
                )
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
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
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
            retain_infrastructure_retry(
                url, response.headers.get("Retry-After")
            )
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
    with open(__file__, "rb") as stream:
        content = stream.read(5_000_001)
    if len(content) > 5_000_000:
        raise AdapterError(
            "Source implementation exceeds reviewed bound", "access"
        )
    return hashlib.sha256(content).hexdigest()


def invalidate_family_artifact():
    """Invalidate identity-checked owned output before error signalling."""
    path = os.environ.get("TALTECH_PACING_ARTIFACT_PATH")
    if not path:
        return
    root = checked_artifact_path(path)
    stale = None
    try:
        stale = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        pass
    if stale is not None:
        validate_owned_output(stale)
    descriptor = os.open(
        os.environ["GITHUB_OUTPUT"], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
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
        stale_descriptor = None
        if stale is not None:
            stale_descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
            try:
                held = os.fstat(stale_descriptor)
                validate_owned_output(held)
                if (held.st_dev, held.st_ino) != (stale.st_dev, stale.st_ino):
                    raise AdapterError(
                        "Stale artifact identity changed", "access"
                    )
            except BaseException:
                os.close(stale_descriptor)
                raise
        try:
            if stale_descriptor is not None:
                os.ftruncate(stale_descriptor, 0)
                os.fsync(stale_descriptor)
            output.write("pacing_ready=false\n")
            output.flush()
            os.fsync(output.fileno())
        finally:
            if stale_descriptor is not None:
                os.close(stale_descriptor)
                current = os.stat(path, follow_symlinks=False)
                validate_owned_output(current)
                if (current.st_dev, current.st_ino) != (
                    stale.st_dev,
                    stale.st_ino,
                ):
                    raise AdapterError(
                        "Stale artifact identity changed", "access"
                    )
                os.unlink(path)


def validate_owned_output(info):
    """Refuse unsafe output without touching it."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe owned artifact output", "access")


def checked_artifact_path(path):
    """Validate this attempt's exact private output directory and basename."""
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
    return root


def export_family_artifact():
    """Atomically export trusted state and signal its availability."""
    path = os.environ.get("TALTECH_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    run_id, attempt = current_run_identity()
    root = checked_artifact_path(path)
    try:
        validate_owned_output(os.stat(path, follow_symlinks=False))
    except FileNotFoundError:
        pass
    with locked_budget():
        state = load_budget()
    if state["in_flight"]:
        invalidate_family_artifact()
        raise AdapterError(
            "Unsettled publisher state cannot be exported", "access"
        )
    envelope = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "run_id": run_id,
        "run_attempt": attempt,
        "source": SOURCE_ID,
        "head_sha": os.environ.get("GITHUB_SHA"),
        "adapter_sha256": source_file_digest(),
        "state_sha256": hashlib.sha256(
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "state": state,
    }
    if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
        raise AdapterError("Invalid artifact source revision", "access")
    fd, temporary = tempfile.mkstemp(prefix=".taltech-pacing-", dir=root)
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


def restore_family_artifact():
    """Require exact history for the reviewed legacy state handoff."""
    global _RESTORED, _STATE_TRUSTED, _RESTORED_LATEST, _RESTORED_HISTORY
    global _VERIFIED_REMOTE_CREATE
    if _RESTORED and os.environ.get("GITHUB_ACTIONS") == "true":
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
            (identity, 1): (head, _WORKFLOW_ID)
            for identity, head in _LEGACY_HEADS.items()
        }
        if (
            latest != (38763419529, 1, SOURCE_ID)
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
        if os.environ.get("GITHUB_ACTIONS") != "true":
            incoming = reviewed_local_legacy_state()
        else:
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
                        r"issues/271#issuecomment-[0-9]+"
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
                    "Reviewed full native/local migration state required",
                    "access",
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
    if run_id not in _LEGACY_HEADS:
        _VERIFIED_REMOTE_CREATE = incoming
    try:
        with locked_budget():
            local = load_budget()
            combined = merge_budget(local, incoming)
            combined["until"] = max(combined["until"], time.time() + 60)
            write_budget(combined)
            if combined["in_flight"]:
                raise AdapterError("Unsettled newest publisher state", "access")
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
                literal_robots = (
                    urllib.parse.urlsplit(current).path == "/robots.txt"
                )
                if original.path == "/robots.txt" and not literal_robots:
                    raise AdapterError(
                        "Robots redirect leaves robots.txt", "access"
                    )
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
            raise RobotsDisallow(
                "Robots policy disallows this source endpoint", "access"
            )
    return max(
        [12.2]
        + [max(g["delay"], g["rate"]) for policy in policies for g in policy]
    )


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


def page(url):
    delay = check_robots(url)
    return PublisherHTML(
        fetch_inventory_page(fetch_source, url, min_interval=delay)
    ).root


def collect():
    if _RUN_END is None:
        with execution():
            return collect()
    with phase("collection", COLLECTION_TIMEOUT, reserve=390):
        prepare_collection()
        records = parse_inventory(page(SOURCE_URL))
        validate_records(records)
        return records


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
        validate_records(records)
        records = remove_expired_records(records, utc_now())
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
    if args.publish and (not failure or previous):
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
                with phase("failure", 120):
                    latest = read_snapshot()
                    if not previous or latest["files"] != snapshot["files"]:
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
