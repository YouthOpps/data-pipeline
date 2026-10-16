"""Discover and collect published ESC grants and educational offers."""

import argparse
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import email.utils
from email.utils import parsedate_to_datetime
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

SOURCE_ID = "cz-dzs"
SOURCE_URL = (
    "https://www.dzs.cz/program/evropsky-sbor-solidarity/projekty-granty"
)
WEBSITE_URL = "https://www.dzs.cz/en"
LANGUAGE = "cs"
PUBLISHER_COUNTRY = "CZ"
PUBLISHER_TYPE = "government"
ATTRIBUTION = (
    "Dům zahraniční spolupráce (DZS), Czech National Agency "
    "for International Education and Research; European Comm"
    "ission/EACEA; SALTO-YOUTH https://www.salto-youth.net/"
    " and the named training organisers"
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {"grants", "training"}
FAMILY = "youthopps-dzs-publisher-v1"
FAMILY_SOURCES = {"cz-study-in-czechia", "cz-dzs"}
COLLECTION_TIMEOUT = 900
RUN_TIMEOUT = 1290
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 3
_ARTIFACT_NAME = "dzs-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_LOCK_CONTEXT = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None

_PAGE_COMPLETED = None


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style"):
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


_OPENER = urllib.request.build_opener(NoRedirect)


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


def fetch_public(url):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(reviewed, publisher=True)
        if status == 200:
            return status, headers, body
        if status == 503 and attempt == 0:
            service_unavailable_backoff()
            continue
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise AdapterError(
            f"Required publisher input returned HTTP {status}: " + provenance,
            "fetch",
            status,
        )


def document_root(text, complete=True):
    if complete and not re.search(
            r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$",
            text, re.I):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(r"just a moment|access denied|attention required|"
                r"checking your browser|verify you are human", title):
        raise AdapterError("Publisher access challenge", "access")
    return root


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
    """Retain date-only deadlines through their UTC closing calendar day."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    retained = []
    for record in records:
        deadline = record["deadline"]
        if deadline is None:
            retained.append(record)
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing_date = datetime.strptime(deadline, "%Y-%m-%d").date()
            expired = (
                checked.astimezone(timezone.utc).date()
                > closing_date
            )
        else:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            expired = checked > closing
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, *, allow_empty=False):
    """Validate all real records against the source and consumer contracts."""
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
        if record.get("summary_language", "en") not in {"en", "cs"}:
            raise AdapterError("Invalid summary language", "validate")
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
    check_deadline()
    token = os.environ.get("DATA_SOURCE_TOKEN", "")
    if not token:
        raise AdapterError("Publication credentials missing", "publish")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, _, body = request_bytes(
            _GITHUB + path, method=method, headers=headers,
            payload=(json.dumps(payload).encode()
                     if payload is not None else None),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError("GitHub connection failed; outcome unconfirmed",
                           "publish") from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication HTTP {status}",
                           "publish", status)
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
        if file is not None and file.get("encoding") == "none":
            sha = file.get("sha", "")
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise AdapterError("Invalid existing blob identity", "publish")
            file = github("GET", "/git/blobs/" + sha)
            if file.get("sha") != sha:
                raise AdapterError("Existing blob identity mismatch", "publish")
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
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        check_deadline()
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


def public_url(value):
    """Constrain discovery to public DZS and delegated training routes."""
    try:
        parsed = urllib.parse.urlsplit(value)
        path = urllib.parse.unquote(parsed.path)
        paging = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        allowed_paging = (
            parsed.hostname == "www.dzs.cz"
            and path == "/networking-activities"
            and set(paging) == {"page"}
            and len(paging["page"]) == 1
            and paging["page"][0].isdigit())
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port or parsed.query and not allowed_paging
                or any(part in {".", ".."} for part in path.split("/"))):
            raise ValueError()
        if parsed.hostname == "www.dzs.cz":
            allowed = (path == "/robots.txt"
                       or path.startswith("/program/evropsky-sbor-solidarity")
                       or path.startswith("/udalost/")
                       or path.startswith("/vyzva")
                       or path == "/networking-activities")
        elif parsed.hostname == "www.salto-youth.net":
            allowed = (path == "/robots.txt" or path.startswith(
                "/tools/european-training-calendar/training/"))
        else:
            allowed = False
        if not allowed:
            raise ValueError()
    except ValueError:
        raise AdapterError("Unsafe public source route", "access") from None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def read_html(url):
    _, headers, body = fetch_public(url)
    if headers.get("Content-Type", "").split(";")[0] != "text/html":
        raise AdapterError("Expected public HTML: " + url, "parse")
    return document_root(body.decode("utf-8", "strict"))


def links(root, base):
    for node in nodes(root, "a"):
        href = node.attrs.get("href", "")
        if href:
            yield node.text(), urllib.parse.urljoin(base, href)


def training_link(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.hostname == "www.salto-youth.net" and parsed.path.startswith(
        "/tools/european-training-calendar/training/"))


def read_pages():
    """Follow programme links and every advertised training detail."""
    programme = "https://www.dzs.cz/program/evropsky-sbor-solidarity"
    seeds = [
        SOURCE_URL,
        programme,
        programme + "/vyjezdy-pobyty",
        "https://www.dzs.cz/networking-activities",
    ]
    pages = {"hubs": {}, "events": {}, "delegated": {}, "owners": {}}
    pending = list(seeds)
    while pending:
        url = pending.pop(0)
        if url in pages["hubs"]:
            continue
        if len(pages["hubs"]) >= 40:
            raise AdapterError("Programme discovery limit exceeded", "parse")
        root = fetch_inventory_page(read_html, url)
        pages["hubs"][url] = root
        for _, target in links(root, url):
            parsed = urllib.parse.urlsplit(target)
            if parsed.hostname != "www.dzs.cz":
                continue
            if (
                parsed.path.startswith("/vyzva")
                and not parsed.query
                or parsed.path == "/networking-activities"
            ):
                clean = public_url(target)
                if clean not in pages["hubs"] and clean not in pending:
                    pending.append(clean)
    own_urls = {}
    delegated_urls = {}
    for url, root in pages["hubs"].items():
        for _, target in links(root, url):
            parsed = urllib.parse.urlsplit(target)
            if (
                parsed.hostname == "www.dzs.cz"
                and parsed.path.startswith("/udalost/")
                and not parsed.query
            ):
                own_urls[public_url(target)] = url
            elif training_link(target):
                delegated_urls[public_url(target)] = url
    for url, owner in sorted(own_urls.items()):
        root = read_html(url)
        pages["events"][url] = root
        pages["owners"][url] = owner
        for _, target in links(one(nodes(root, "main"), "event main"), url):
            if training_link(target):
                clean = public_url(target)
                delegated_urls[clean] = url
    for url, owner in sorted(delegated_urls.items()):
        pages["delegated"][url] = read_html(url)
        pages["owners"][url] = owner
    print(
        f"Discovery: {len(pages['hubs'])} programme/call pages; "
        f"{len(pages['events'])} DZS event details; "
        f"{len(pages['delegated'])} delegated details",
        file=sys.stderr,
    )
    return pages


_MONTHS = {}
for _number, _names in enumerate(
    (
        ("ledna", "leden", "january"),
        ("února", "únor", "february"),
        ("března", "březen", "march"),
        ("dubna", "duben", "april"),
        ("května", "květen", "may"),
        ("června", "červen", "june"),
        ("července", "červenec", "july"),
        ("srpna", "srpen", "august"),
        ("září", "september"),
        ("října", "říjen", "october"),
        ("listopadu", "listopad", "november"),
        ("prosince", "prosinec", "december"),
    ),
    1,
):
    _MONTHS.update(dict.fromkeys(_names, _number))
_DATE_PATTERN = (
    r"(?P<day>\d{1,2})\.?\s*(?P<month>"
    + "|".join(sorted(_MONTHS, key=len, reverse=True))
    + r")(?:\s+(?P<year>20\d{2}))?|"
    r"(?P<d>\d{1,2})\.\s*(?P<m>\d{1,2})\.\s*(?P<y>20\d{2})"
)


def published_dates(text, year=None):
    result = []
    for match in re.finditer(_DATE_PATTERN, text, re.I):
        if match["d"]:
            day, month, actual_year = (
                int(match["d"]), int(match["m"]), int(match["y"]))
        else:
            day = int(match["day"])
            month = _MONTHS[match["month"].lower()]
            actual_year = int(match["year"] or year or 0)
        if not actual_year:
            raise AdapterError("Published date has no evidenced year", "parse")
        try:
            result.append(datetime(actual_year, month, day).date().isoformat())
        except ValueError:
            raise AdapterError("Invalid published date", "parse") from None
    return result


# Historical identity aliases only; content always comes from fetched pages.
_EVENT_KEYS = {
    "informacni-webinar-pro-zadatele-o-solidarni-projekty-esc30-0": "call-event-2",
    "webinar-tipy-k-zadosti-pro-zajemce-o-solidarni-projekty-esc30-0": "call-event-4",
    "informacni-webinar-k-zadosti-o-grant-na-dobrovolnicke-projekty-esc51-0": "call-event-8",
    "tosca-volunteering-skoleni-podpora-pro-organizace-v-programu-"
    "evropsky-sbor-solidarity": "tosca",
    "od-napadu-k-projektu-5": "idea",
}
_SALTO_KEYS = {
    "14430": "delegated-1",
    "15280": "delegated-2",
    "15244": "delegated-3",
    "15320": "delegated-4",
    "15317": "delegated-5",
    "15300": "delegated-6",
    "15356": "delegated-7",
}


def short_excerpt(text, limit):
    """Bound quoted plain text in UTF-16 units without splitting a character."""
    text = " ".join(text.split())
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    clipped = text.encode("utf-16-le")[:max(0, limit - 3) * 2].decode(
        "utf-16-le", "ignore")
    return clipped.rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def factual_blocks(root):
    """Read body paragraphs and leaf lists, excluding navigation and contacts."""
    blocks = []

    def visit(node):
        if not isinstance(node, Node):
            return
        classes = node.attrs.get("class", "")
        if (node.tag in {"nav", "aside", "footer", "script", "style"}
                or re.search(r"breadcrumbs|timeline|steps__|post-navigation|"
                             r"entry-meta|description-date|description-tags",
                             classes)):
            return
        if node.tag in {"h2", "h3", "h4"}:
            blocks.append((node.tag, node.text()))
            return
        if (node.tag in {"p", "li"} and not any(
                nodes(node, tag) for tag in {"ul", "ol"})
                or "elementor-icon-box-content" in classes):
            text = node.text()
            if (len(text) >= 30 and "@" not in text
                    and not re.search(r"newsletter|Contact for questions|"
                                      r"All new training offers", text, re.I)):
                blocks.append(("body", text))
            return
        for child in node.children:
            visit(child)

    visit(root)
    return blocks


def describe(record, blocks, language, timing):
    """Index brief publisher description, audience and funding excerpts."""
    bodies = list(dict.fromkeys(text for tag, text in blocks if tag == "body"))
    selected = bodies[:1]
    patterns = (
        r"ve věku|rezident|žijí|určen|eligible|aged|persons|open to|"
        r"applicants|beneficiar|participants.{0,25}(?:from|who)|ηλικ|δικαι",
        r"\d[\d ,.]*\s*(?:eur|€)|hrazen|bezplat|zdarma|"
        r"participation fee|travel costs|travel expenses|budget|"
        r"accommodation|funding|grants|χρηματοδ",
    )
    for pattern in patterns:
        candidates = [text for text in bodies if text not in selected
                      and re.search(pattern, text, re.I)]
        if candidates:
            selected.append(candidates[0])
    record["summary_language"] = language
    for text in selected:
        record["classification"]["evidence"].append(
            f"Publisher excerpt ({language}): " + short_excerpt(text, 240))
    timing = short_excerpt(timing, 240)
    budget = 600 - len(timing.encode("utf-16-le")) // 2 - 1
    if selected:
        allowance = (budget - 3 * (len(selected) - 1)) // len(selected)
        body = " / ".join(short_excerpt(text, allowance) for text in selected)
    else:
        body = {"en": "Publisher description not stated.",
                "cs": "Popis nabídky není uveden.",
                "el": "Δεν αναφέρεται περιγραφή."}[language]
    record["summary"] = short_excerpt((timing + " " + body).strip(), 600)


def opportunity(key, title, url, category, deadline, proof, kind="opportunity"):
    record = make_record(title, url, [category], kind=kind,
                         method="publisher-html",
                         evidence=[ATTRIBUTION, "Publisher: " + url] + proof)
    record["id"] = hashlib.sha256(
        (SOURCE_ID + "|" + key).encode()).hexdigest()[:24]
    record["deadline"] = deadline
    record["summary"] = (
        "Uzávěrka přihlášek: " + deadline + ". " if deadline else
        "Uzávěrka přihlášek není uvedena. "
    )
    now = datetime.now(timezone.utc)
    if deadline and "T" in deadline:
        record["status"] = ("expired" if now >= datetime.fromisoformat(
            deadline.replace("Z", "+00:00")) else "open")
    elif deadline:
        today = now.date().isoformat()
        record["status"] = ("expired" if today > deadline else
                            "open" if today < deadline else "unknown")
    return record


def grant_records(pages):
    records = {}
    # The current call supersedes older hub rounds of the same annual scheme.
    hubs = sorted(pages["hubs"].items(),
                  key=lambda item: "/vyzva" in item[0])
    for url, root in hubs:
        if url != SOURCE_URL and "/vyzva" not in url:
            continue
        annual = {}
        for tab in (n for n in nodes(root, "div")
                    if n.attrs.get("id", "").endswith("-tab")):
            fragment = tab.attrs["id"][:-4]
            if fragment == "quality-label":
                continue
            for timeline in nodes(tab, "div", "timeline"):
                heading = " ".join(n.text() for n in
                                   nodes(timeline, "h3", "timeline__heading"))
                if not re.search(r"Termíny (?:výz|pro podávání)", heading):
                    continue
                year_match = re.search(r"\b20\d{2}\b", heading)
                for item in nodes(timeline, "li", "timeline__item"):
                    title = one(nodes(item, "span", "timeline__item-date"),
                                "grant timeline title").text()
                    if re.search(r"Quality Label|Značka kvality", title, re.I):
                        continue
                    deadlines = published_dates(
                        item.text(), year_match[0] if year_match else None)
                    if not deadlines:
                        raise AdapterError("Grant timeline has no dates: "
                                           + url, "parse")
                    years = {date[:4] for date in deadlines}
                    if len(years) != 1:
                        raise AdapterError(
                            "Mixed annual grant timeline", "parse")
                    year = next(iter(years))
                    scheme = {
                        "solidarni-projekty": "solidarity",
                        "dobrovolnicke-projekty": "volunteering",
                    }.get(fragment)
                    if fragment == "centralizovane-aktivity":
                        if re.search(r"vysokou priorit", title, re.I):
                            scheme = "priority-teams"
                        elif re.search(r"humanitární", title, re.I):
                            scheme = "humanitarian"
                    key = scheme or fragment + "|" + title
                    key += "-" + year
                    canonical = SOURCE_URL + "#" + fragment
                    record = opportunity(
                        key, title, canonical, "grants", max(deadlines),
                        ["Call timeline: " + url + "#" + fragment,
                         "Published deadlines: " + ", ".join(deadlines)],
                        kind=("opportunity" if scheme == "solidarity"
                              else "institutional-grant"))
                    blocks = factual_blocks(tab)
                    if fragment == "centralizovane-aktivity":
                        selected, active = [], False
                        for tag, text in blocks:
                            if tag == "h3":
                                active = bool(re.search(
                                    r"vysokou priorit" if scheme == "priority-teams"
                                    else r"humanitární", text, re.I))
                            if active:
                                selected.append((tag, text))
                        blocks = selected
                    if key in annual:
                        previous = annual[key]
                        deadlines = sorted(set(deadlines + previous[1]))
                        record["deadline"] = max(deadlines)
                        record["classification"]["evidence"] = sorted(set(
                            record["classification"]["evidence"]
                            + previous[0]["classification"]["evidence"]))
                        record["title"] = min(record["title"], previous[0]["title"])
                    annual[key] = (record, deadlines, blocks)
        for key, (record, deadlines, blocks) in annual.items():
            record["status"] = opportunity(
                key, record["title"], record["url"], "grants",
                max(deadlines), [])["status"]
            describe(record, blocks, "cs",
                     "Uzávěrky přihlášek: " + ", ".join(sorted(deadlines)) + ".")
            records[key] = record
    if not records:
        raise AdapterError("No grant timelines discovered", "parse")
    return list(records.values())


def event_record(url, root, owner):
    main = one(nodes(root, "main"), "event main")
    title = one(nodes(main, "h1"), "event title").text()
    if re.search(r"individuální konzultace|on.arrival|mid.term", title, re.I):
        return None
    text = main.text()
    if not re.search(r"webinář|školení|seminář|kurz|workshop|konferenc",
                     text, re.I):
        raise AdapterError("Unclassified advertised event: " + url, "parse")
    times = nodes(main, "time")
    years = {n.attrs.get("datetime", "")[:4] for n in times}
    years.discard("")
    year = next(iter(years)) if len(years) == 1 else None
    # Match only application/registration sentences, never activity dates.
    contexts = re.findall(
        r"(?:Registrac[ei]|registrační formulář|přihlášk[ay]|"
        r"přihlašování|přihlas\w*|Deadline)[^!?]{0,180}?"
        r"(?:do |je )((?:\d{1,2}\.\s*)[^!?]{0,60})", text, re.I)
    deadlines = [date for context in contexts
                 for date in published_dates(context, year)[:1]]
    deadline = max(deadlines) if deadlines else None
    key = _EVENT_KEYS.get(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1],
                          url)
    record = opportunity(key, title, url, "training", deadline,
                         ["Discovered at: " + owner])
    event_dates = sorted({n.attrs.get("datetime", "")[:10] for n in times
                          if re.match(r"20\d{2}-\d{2}-\d{2}",
                                      n.attrs.get("datetime", ""))})
    if event_dates:
        record["summary"] += " Termín akce: " + event_dates[0] + "."
    if re.search(r"\bv (?:Brně|Praze)\b", text):
        record["host_countries"] = ["CZ"]
        record["location"] = "Brno" if "v Brně" in text else "Praha"
    describe(record, factual_blocks(main), "cs", record["summary"])
    return record


def delegated_record(url, root, owner):
    content = one(nodes(root, "div", "training"), "SALTO training detail")
    title = one(nodes(content, "h1"), "training title").text()
    text = content.text()
    closing = re.search(
        r"(?:application deadline(?:\s*\([^)]*\))?"
        r"(?:\s+(?:was|is))?|Apply before)"
        r"[:\s]+(.{0,100})", text, re.I)
    if not closing:
        raise AdapterError("Missing SALTO application deadline: " + url,
                           "parse")
    values = published_dates(closing[1])
    if not values:
        raise AdapterError("Unparseable SALTO application deadline: " + url,
                           "parse")
    deadline = values[0]
    if re.search(r"24\s*h\s*UTC", closing[0], re.I):
        deadline = (datetime.fromisoformat(deadline) +
                    timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    identifier = re.search(r"\.(\d+)/?$", urllib.parse.urlsplit(url).path)
    if not identifier:
        raise AdapterError("Missing SALTO training identity", "parse")
    key = _SALTO_KEYS.get(identifier[1], "salto-" + identifier[1])
    record = opportunity(key, title, url, "training", deadline,
                         ["DZS discovery: " + owner])
    record["language"] = "en"
    record["summary"] = "Application deadline: " + deadline + "."
    organiser = re.search(r"Organiser:\s*(.+?)\s*Contact for questions:",
                          text, re.I)
    if organiser:
        record["classification"]["evidence"].append(
            "Organiser: " + organiser[1])
    if "Applications are closed" in text:
        record["status"] = "expired"
    category = one(nodes(content, "div", "tool-item-category-container"),
                   "training dates/location").text()
    if "|" in category:
        location = category.split("|", 1)[1].strip()
        record["location"] = location
        record["summary"] += " Location: " + location[:100] + "."
        countries = {
            "Greece": "GR", "Slovakia": "SK", "Germany": "DE",
            "Czech Republic": "CZ", "Czechia": "CZ", "Portugal": "PT",
            "Poland": "PL", "Italy": "IT", "France": "FR",
            "Austria": "AT", "Belgium": "BE", "Spain": "ES",
            "Finland": "FI", "Ireland": "IE", "Latvia": "LV",
            "Lithuania": "LT", "Estonia": "EE", "Netherlands": "NL",
            "Romania": "RO", "Slovenia": "SI", "Hungary": "HU",
            "Luxembourg": "LU", "Croatia": "HR", "Serbia": "RS",
            "Bulgaria": "BG", "Cyprus": "CY", "Malta": "MT",
            "Denmark": "DK", "Sweden": "SE", "Norway": "NO",
            "Iceland": "IS", "Türkiye": "TR", "Turkey": "TR",
            "North Macedonia": "MK", "Switzerland": "CH",
            "Slovak Republic": "SK",
        }
        if not re.search(r"online|on.line|virtual", category, re.I):
            hosts = {code for country, code in countries.items()
                     if re.search(r"\b" + re.escape(country) + r"\b",
                                  location, re.I)}
            if len(hosts) == 1:
                record["host_countries"] = sorted(hosts)
    blocks = [("body", node.text()) for node in
              nodes(content, "div", "training-summary")]
    blocks.extend(factual_blocks(content))
    describe(record, blocks, "en", record["summary"])
    record["classification"]["evidence"].append(
        "Advertised closing day: " + values[0])
    return record


def parse_inventory(pages):
    records = grant_records(pages)
    own = {}
    for url, root in pages["events"].items():
        record = event_record(url, root, pages["owners"][url])
        if record:
            own[url] = record
    for url, root in pages["delegated"].items():
        owner = pages["owners"][url]
        record = delegated_record(url, root, owner)
        if owner in own:
            # The linked provider is evidence for the same DZS course.
            parent = own[owner]
            advertised = next(value.removeprefix("Advertised closing day: ")
                              for value in record["classification"]["evidence"]
                              if value.startswith("Advertised closing day: "))
            if parent["deadline"] and parent["deadline"] != advertised:
                raise AdapterError("Conflicting DZS/SALTO closing dates",
                                   "parse")
            parent["deadline"] = record["deadline"]
            parent["status"] = record["status"]
            parent["location"] = record["location"]
            parent["host_countries"] = record["host_countries"]
            parent["classification"]["evidence"].append(
                "Delegated course details: " + url)
            parent["classification"]["evidence"].extend(
                record["classification"]["evidence"])
        else:
            records.append(record)
    records.extend(own.values())
    validate_records(records)
    return records


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "display_name": "DZS",
        "source": SOURCE_ID,
        "name": "DZS — European Solidarity Corps",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "attribution": ATTRIBUTION,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (previous.get("last_success_at")
                            if error else attempt),
        "last_checked_at": (previous.get("last_checked_at")
                            if error else checked_at),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved" if error else
            f"Collected {len(records)} published ESC grants and training offers"
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


def phase_error(name):
    return AdapterError("Whole " + name + " phase deadline exceeded",
                        "publish" if name == "publication" else "access")


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def fresh_records(records, old, attempt, checked):
    prior = {record["id"]: record for record in old}
    temporal = {"created_at", "updated_at", "first_seen_at", "last_seen_at",
                "last_checked_at"}
    for record in records:
        before = prior.get(record["id"], {})
        same = {k: v for k, v in before.items() if k not in temporal} == {
            k: v for k, v in record.items() if k not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", attempt) if same else attempt,
            last_seen_at=attempt, last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records)


def run_body(publishing):
    attempt = utc_now()
    snapshot = None
    old, previous, records, desired = [], {}, [], None
    failure = None
    try:
        with phase("prepublication", 900, reserve=390):
            prepare_collection()
            if publishing:
                candidate = read_snapshot()
                if candidate["files"]["data.json"] is not None:
                    candidate_records = json.loads(
                        candidate["files"]["data.json"]
                    )
                    candidate_metadata = json.loads(
                        candidate["files"]["metadata.json"]
                    )
                    validate_records(candidate_records, allow_empty=True)
                    if (
                        not isinstance(candidate_metadata, dict)
                        or candidate_metadata.get("source") != SOURCE_ID
                    ):
                        raise AdapterError(
                            "Remote metadata source mismatch", "publish"
                        )
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            fresh_records(records, old, attempt, checked)
            records = remove_expired_records(records, checked)
            validate_records(records, allow_empty=True)
            outcome = metadata(attempt, previous, records, checked_at=checked)
            desired = {
                "data.json": serialize(records),
                "metadata.json": serialize(outcome),
            }
    except PhaseExpired:
        failure = phase_error("prepublication")
    except Exception as error:
        failure = error
    if failure is None and publishing:
        try:
            with phase("publication", 180):
                publish_files(snapshot, desired)
        except PhaseExpired:
            failure = phase_error("publication")
        except Exception as error:
            failure = error
    if failure is not None:
        outcome = metadata(attempt, previous, old, failure)
        if publishing:
            try:
                with phase("failure", 180):
                    if snapshot is None:
                        raise AdapterError(
                            "No validated previous source pair", "publish"
                        )
                    latest = read_snapshot()
                    if desired is not None and latest["files"] == desired:
                        outcome = json.loads(desired["metadata.json"])
                        failure = None
                    elif previous and latest["files"] == snapshot["files"]:
                        publish_files(
                            latest,
                            {
                                "metadata.json": serialize(outcome),
                            },
                        )
                    else:
                        raise AdapterError(
                            "No safe unchanged last-good pair", "publish"
                        )
            except (Exception, PhaseExpired):
                outcome["message"] = (
                    "Run failed; durable outcome unconfirmed. "
                    "No newer source pair was overwritten."
                )
        elif not old:
            outcome["message"] = "Collection failed; no publication attempted"
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure is not None else 0


def run_lifecycle(publishing):
    result = None
    try:
        result = run_body(publishing)
    except PhaseExpired:
        result = 1
    finally:
        try:
            with phase("export", EXPORT_TIMEOUT):
                export_family_artifact()
        except (Exception, PhaseExpired):
            try:
                arm_alarm(_RUN_END)
                print("Timing artifact export failed; next run fail closed",
                      file=sys.stderr)
            except (Exception, PhaseExpired):
                pass
            if result == 0:
                result = 1
    return result


_EFFECTIVE_COLLECTION = 900
_PUBLICATION_SECONDS = 180
_FAILURE_SECONDS = 180
_PUBLISHER_TIMEOUT = 60
_DEFAULT_HTTP_TIMEOUT = 15

# The historical family name and namespace remain stable across both producers.
_WORKFLOW_IDS = {"cz-dzs": 378390058, "cz-study-in-czechia": 378390060}
_LOCAL_RESEARCH_MIGRATION = {'schema': 1,
 'source': 'cz-dzs',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/289#issuecomment-6205576095',
 'state': {'schema': 3,
           'family': 'youthopps-dzs-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [{'run_id': 37962105063,
                                           'run_attempt': 1,
                                           'source': 'cz-study-in-czechia',
                                           'head_sha': '251d29d718aa4d79f4b0b93b8512cb82bb82ce23',
                                           'status': 403,
                                           'route': None,
                                           'purpose': 'unknown',
                                           'log_sha256': 'aa6dd64a4a47e375977919cdd43012e46a5a25a0a78554933b974ccf2f2b6279'},
                                          {'run_id': 38245510407,
                                           'run_attempt': 1,
                                           'source': 'cz-study-in-czechia',
                                           'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                                           'status': 403,
                                           'route': 'http://static.daad.de/robots.txt',
                                           'purpose': 'literal_robots_excluded_historical_document',
                                           'log_sha256': '598ac007b9571a5dd5e409ebef862c8fbff6ca7a5b540687e2c048c3de73a853'}],
           'research_provenance': {'full20_history_raw_sha256': 'a03920e45ebf2c5b8413ca8458379b8c7cace2f4565da2fe53338e31fb86a9ad',
                                   'legacy_state_canonical_sha256': 'a1ff65e13622691949a705631c25893ca73a93dabff59c2d4274c15db7b2dbf5',
                                   'legacy_owned_archive_sha256': '30e404a497e17ed0f3a30021acdfe7ae32ed407177024469fa408d4e5e01c885',
                                   'unscoped_403_log_sha256': 'aa6dd64a4a47e375977919cdd43012e46a5a25a0a78554933b974ccf2f2b6279',
                                   'all20_completion_upper_bounds': [{'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38823222698,
                                                                      'attempt': 1,
                                                                      'head_sha': '77b5911162e0ab635225298393523b409638e6b1',
                                                                      'jobs_sha256': '7c957fb06396b81839b228890feeefeb94c706ccc4085f81a3fadbc325c77c21',
                                                                      'at': 1792106840.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38763419564,
                                                                      'attempt': 1,
                                                                      'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                                      'jobs_sha256': 'ea7e4d16f6d4b8571c3415b9567d0a244443ce4a707f039041ecc52ec07f41ec',
                                                                      'at': 1792082867.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38721259429,
                                                                      'attempt': 1,
                                                                      'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                                                                      'jobs_sha256': '9186da00cec11ba28ce14df864384492a055247bc2edcd025746f3ae6f78a2b4',
                                                                      'at': 1792063446.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38720083895,
                                                                      'attempt': 1,
                                                                      'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                                                                      'jobs_sha256': '63defa8c0e6a3a5f10e09f76550782f2b23e75bce8e5891fe9e503dd6ffc8ef8',
                                                                      'at': 1792060158.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38684235960,
                                                                      'attempt': 1,
                                                                      'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                                                                      'jobs_sha256': 'c9b2ea0f6e0cebe96a620ee3c6d9b178fcddb39acd62a5d997ac2076ddacba42',
                                                                      'at': 1792040531.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38534756577,
                                                                      'attempt': 1,
                                                                      'head_sha': '5ab9b0237b7fa2b043fe276ec43f29e493c685ab',
                                                                      'jobs_sha256': '637a957e51b510e5ce2ab9e5a078e1c7628f3c4f807c6baebd83b4440ad4b22d',
                                                                      'at': 1791966481.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38507918475,
                                                                      'attempt': 1,
                                                                      'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                                                                      'jobs_sha256': 'ca4acc29e241036827b8ae4ab01175ebfbd1bbcae8bd2038646a7459a22fe9a5',
                                                                      'at': 1791949809.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38247977325,
                                                                      'attempt': 1,
                                                                      'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                                                                      'jobs_sha256': 'd65b1480a790a7899015c845525502229c27353825e9b3295a4520324819e11a',
                                                                      'at': 1791810813.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38004213621,
                                                                      'attempt': 1,
                                                                      'head_sha': '029a500dbf22c1d44ae592c667f26f33d9d364c8',
                                                                      'jobs_sha256': '21566ddb8205997ed7b4394d4f0533f47c691cfb9ac47d965673dc4ef825ab86',
                                                                      'at': 1791588578.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-dzs',
                                                                      'run_id': 38003386372,
                                                                      'attempt': 1,
                                                                      'head_sha': '029a500dbf22c1d44ae592c667f26f33d9d364c8',
                                                                      'jobs_sha256': 'c2e54ae6a2143a6f08a780c9dbfa111eebdda6549e55bd3cdb15349b0ecb9011',
                                                                      'at': 1791587992.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38763419357,
                                                                      'attempt': 1,
                                                                      'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                                      'jobs_sha256': 'c9be393842e0f147f74fd009a1d3630b5913cd1cd5de1f6950b15f5cdc71ef84',
                                                                      'at': 1792082613.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38721259427,
                                                                      'attempt': 1,
                                                                      'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                                                                      'jobs_sha256': 'd0ccaff64101f4704d59a6c691b7f37fb4f022da15a201df5cf63d41cb4e8216',
                                                                      'at': 1792066479.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38720083860,
                                                                      'attempt': 1,
                                                                      'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                                                                      'jobs_sha256': '446e80e131e02e803c7acc6dba163aedd020356cb4957f86a85ceb1969aee812',
                                                                      'at': 1792063192.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38684236154,
                                                                      'attempt': 1,
                                                                      'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                                                                      'jobs_sha256': '5e5e655f07b5bee5893af6a68b3b4d579787e11cb09511b8ab5b20eda9083c13',
                                                                      'at': 1792043358.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38536206743,
                                                                      'attempt': 1,
                                                                      'head_sha': '3f6ab17f4698de58f7365682a22384cd08788f69',
                                                                      'jobs_sha256': '5c16863ef20d6771f0f904e842fc75a825e1c018ce0e292a38af0e1ef83480d0',
                                                                      'at': 1791969768.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38507918678,
                                                                      'attempt': 1,
                                                                      'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                                                                      'jobs_sha256': '6efc717093320b5da7e7c2c5441b25bee647d060d5d400f2a6bc728e7d857eb5',
                                                                      'at': 1791952622.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 38245510407,
                                                                      'attempt': 1,
                                                                      'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                                                                      'jobs_sha256': 'aa70fec7e0a9caa2495bcd3c8494702afe5077cab725fc830bd4d1113622cd53',
                                                                      'at': 1791810584.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 37988752436,
                                                                      'attempt': 1,
                                                                      'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                      'jobs_sha256': '313265d7cf0188252a10d488f1f992ca03fd193f478c68c62126c230de854a2d',
                                                                      'at': 1791583155.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 37979963485,
                                                                      'attempt': 1,
                                                                      'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                      'jobs_sha256': '39ed4466a002d3acf4b11c507b60061734ad8e126058522c830f5c32839d17da',
                                                                      'at': 1791578463.0},
                                                                     {'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'source': 'cz-study-in-czechia',
                                                                      'run_id': 37962105063,
                                                                      'attempt': 1,
                                                                      'head_sha': '251d29d718aa4d79f4b0b93b8512cb82bb82ce23',
                                                                      'jobs_sha256': '348ce4b367ccb63e19d5a4f0238874f6bff953d6e93b577f8a0c7140b1376bc6',
                                                                      'at': 1791569256.0}],
                                   'observed_legacy_ten_starts': [1792106779.5384872,
                                                                  1792106785.5385926,
                                                                  1792106791.5387037,
                                                                  1792106797.5387986,
                                                                  1792106803.538898,
                                                                  1792106809.538989,
                                                                  1792106815.5391028,
                                                                  1792106821.5392191,
                                                                  1792106827.5393183,
                                                                  1792106833.5394468],
                                   'local_namespace_evidence': 'local-namespace-readonly.json; '
                                                               'genuinely '
                                                               'ABSENT at '
                                                               'preflight, '
                                                               'not zero '
                                                               'history',
                                   'settlement_boundary': 'in_flight=False '
                                                          'is ONLY '
                                                          'proposed '
                                                          'reviewed first '
                                                          'migration from '
                                                          'exact schema1; '
                                                          'old missing '
                                                          'pending does '
                                                          'not prove '
                                                          'historical '
                                                          'crash-free/settled '
                                                          'requests'},
           'starts': [1791569256.0,
                      1791578463.0,
                      1791583155.0,
                      1791587992.0,
                      1791588578.0,
                      1791810584.0,
                      1791810813.0,
                      1791949809.0,
                      1791952622.0,
                      1791966481.0,
                      1791969768.0,
                      1792040531.0,
                      1792043358.0,
                      1792060158.0,
                      1792063192.0,
                      1792063446.0,
                      1792066479.0,
                      1792082613.0,
                      1792082867.0,
                      1792106779.5384872,
                      1792106785.5385926,
                      1792106791.5387037,
                      1792106797.5387986,
                      1792106803.538898,
                      1792106809.538989,
                      1792106815.5391028,
                      1792106821.5392191,
                      1792106827.5393183,
                      1792106833.5394468,
                      1792106840.0],
           'interval': 12.2,
           'until': 1792109694.5894067,
           'page_gap': 30,
           'completed': 1792106840.0,
           'inventory_completed': 1792106840.0,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792109634.5894067},
 'state_sha256': '716918b0dfafd5aaa0dd15ad36a95e74d9ee5f37d2f5615deee20c2e65404f75',
 'legacy_state_sha256': 'a1ff65e13622691949a705631c25893ca73a93dabff59c2d4274c15db7b2dbf5'}
_REVIEWED_NATIVE_MIGRATION = {'schema': 1,
 'source': 'cz-dzs',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/289#issuecomment-6208060152',
 'state': {'schema': 3,
           'family': 'youthopps-dzs-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [{'run_id': 37962105063,
                                           'run_attempt': 1,
                                           'source': 'cz-study-in-czechia',
                                           'head_sha': '251d29d718aa4d79f4b0b93b8512cb82bb82ce23',
                                           'status': 403,
                                           'route': None,
                                           'purpose': 'unknown',
                                           'log_sha256': 'aa6dd64a4a47e375977919cdd43012e46a5a25a0a78554933b974ccf2f2b6279'},
                                          {'run_id': 38245510407,
                                           'run_attempt': 1,
                                           'source': 'cz-study-in-czechia',
                                           'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                                           'status': 403,
                                           'route': 'http://static.daad.de/robots.txt',
                                           'purpose': 'literal_robots_excluded_historical_document',
                                           'log_sha256': '598ac007b9571a5dd5e409ebef862c8fbff6ca7a5b540687e2c048c3de73a853'}],
           'research_provenance': {'all20_completion_upper_bounds': [{'at': 1792106840.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '77b5911162e0ab635225298393523b409638e6b1',
                                                                      'jobs_sha256': '7c957fb06396b81839b228890feeefeb94c706ccc4085f81a3fadbc325c77c21',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38823222698,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1792082867.0,
                                                                      'attempt': 1,
                                                                      'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                                      'jobs_sha256': 'ea7e4d16f6d4b8571c3415b9567d0a244443ce4a707f039041ecc52ec07f41ec',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38763419564,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1792063446.0,
                                                                      'attempt': 1,
                                                                      'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                                                                      'jobs_sha256': '9186da00cec11ba28ce14df864384492a055247bc2edcd025746f3ae6f78a2b4',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38721259429,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1792060158.0,
                                                                      'attempt': 1,
                                                                      'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                                                                      'jobs_sha256': '63defa8c0e6a3a5f10e09f76550782f2b23e75bce8e5891fe9e503dd6ffc8ef8',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38720083895,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1792040531.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                                                                      'jobs_sha256': 'c9b2ea0f6e0cebe96a620ee3c6d9b178fcddb39acd62a5d997ac2076ddacba42',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38684235960,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1791966481.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '5ab9b0237b7fa2b043fe276ec43f29e493c685ab',
                                                                      'jobs_sha256': '637a957e51b510e5ce2ab9e5a078e1c7628f3c4f807c6baebd83b4440ad4b22d',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38534756577,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1791949809.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                                                                      'jobs_sha256': 'ca4acc29e241036827b8ae4ab01175ebfbd1bbcae8bd2038646a7459a22fe9a5',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38507918475,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1791810813.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                                                                      'jobs_sha256': 'd65b1480a790a7899015c845525502229c27353825e9b3295a4520324819e11a',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38247977325,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1791588578.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '029a500dbf22c1d44ae592c667f26f33d9d364c8',
                                                                      'jobs_sha256': '21566ddb8205997ed7b4394d4f0533f47c691cfb9ac47d965673dc4ef825ab86',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38004213621,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1791587992.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '029a500dbf22c1d44ae592c667f26f33d9d364c8',
                                                                      'jobs_sha256': 'c2e54ae6a2143a6f08a780c9dbfa111eebdda6549e55bd3cdb15349b0ecb9011',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38003386372,
                                                                      'source': 'cz-dzs'},
                                                                     {'at': 1792082613.0,
                                                                      'attempt': 1,
                                                                      'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                                      'jobs_sha256': 'c9be393842e0f147f74fd009a1d3630b5913cd1cd5de1f6950b15f5cdc71ef84',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38763419357,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1792066479.0,
                                                                      'attempt': 1,
                                                                      'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                                                                      'jobs_sha256': 'd0ccaff64101f4704d59a6c691b7f37fb4f022da15a201df5cf63d41cb4e8216',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38721259427,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1792063192.0,
                                                                      'attempt': 1,
                                                                      'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                                                                      'jobs_sha256': '446e80e131e02e803c7acc6dba163aedd020356cb4957f86a85ceb1969aee812',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38720083860,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1792043358.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                                                                      'jobs_sha256': '5e5e655f07b5bee5893af6a68b3b4d579787e11cb09511b8ab5b20eda9083c13',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38684236154,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1791969768.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '3f6ab17f4698de58f7365682a22384cd08788f69',
                                                                      'jobs_sha256': '5c16863ef20d6771f0f904e842fc75a825e1c018ce0e292a38af0e1ef83480d0',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38536206743,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1791952622.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                                                                      'jobs_sha256': '6efc717093320b5da7e7c2c5441b25bee647d060d5d400f2a6bc728e7d857eb5',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38507918678,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1791810584.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                                                                      'jobs_sha256': 'aa70fec7e0a9caa2495bcd3c8494702afe5077cab725fc830bd4d1113622cd53',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 38245510407,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1791583155.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                      'jobs_sha256': '313265d7cf0188252a10d488f1f992ca03fd193f478c68c62126c230de854a2d',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 37988752436,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1791578463.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                      'jobs_sha256': '39ed4466a002d3acf4b11c507b60061734ad8e126058522c830f5c32839d17da',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 37979963485,
                                                                      'source': 'cz-study-in-czechia'},
                                                                     {'at': 1791569256.0,
                                                                      'attempt': 1,
                                                                      'head_sha': '251d29d718aa4d79f4b0b93b8512cb82bb82ce23',
                                                                      'jobs_sha256': '348ce4b367ccb63e19d5a4f0238874f6bff953d6e93b577f8a0c7140b1376bc6',
                                                                      'kind': 'authenticated_actual_job_completion_UPPER_BOUND_NOT_publisher_start',
                                                                      'run_id': 37962105063,
                                                                      'source': 'cz-study-in-czechia'}],
                                   'full20_history_raw_sha256': 'a03920e45ebf2c5b8413ca8458379b8c7cace2f4565da2fe53338e31fb86a9ad',
                                   'legacy_owned_archive_sha256': '30e404a497e17ed0f3a30021acdfe7ae32ed407177024469fa408d4e5e01c885',
                                   'legacy_state_canonical_sha256': 'a1ff65e13622691949a705631c25893ca73a93dabff59c2d4274c15db7b2dbf5',
                                   'local_namespace_evidence': 'local-namespace-readonly.json; '
                                                               'genuinely '
                                                               'ABSENT at '
                                                               'preflight, '
                                                               'not zero '
                                                               'history',
                                   'observed_legacy_ten_starts': [1792106779.5384872,
                                                                  1792106785.5385926,
                                                                  1792106791.5387037,
                                                                  1792106797.5387986,
                                                                  1792106803.538898,
                                                                  1792106809.538989,
                                                                  1792106815.5391028,
                                                                  1792106821.5392191,
                                                                  1792106827.5393183,
                                                                  1792106833.5394468],
                                   'settlement_boundary': 'in_flight=False is '
                                                          'ONLY proposed '
                                                          'reviewed first '
                                                          'migration from '
                                                          'exact schema1; old '
                                                          'missing pending '
                                                          'does not prove '
                                                          'historical '
                                                          'crash-free/settled '
                                                          'requests',
                                   'unscoped_403_log_sha256': 'aa6dd64a4a47e375977919cdd43012e46a5a25a0a78554933b974ccf2f2b6279'},
           'starts': [1792123367.6491356,
                      1792123367.654834,
                      1792123367.6769118,
                      1792123368.0783427,
                      1792123380.2805097,
                      1792123380.2887108,
                      1792123380.3090973,
                      1792123380.6701193,
                      1792123392.8705778,
                      1792123392.874231,
                      1792123392.8958905,
                      1792123395.4589598,
                      1792123407.6594343,
                      1792123407.6633706,
                      1792123407.6832645,
                      1792123408.018209,
                      1792123420.4183292,
                      1792123420.491614,
                      1792123420.5346823,
                      1792123421.8318853],
           'interval': 12.2,
           'until': 1792123486.4542248,
           'page_gap': 30,
           'completed': 1792123421.832108,
           'inventory_completed': 1792117891.0018818,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792123426.4542248},
 'state_sha256': 'a6648eb8ccb25d313145bfddb96d502ede578d841539bb52a013a80e9aa80b90',
 'legacy_state_sha256': 'a1ff65e13622691949a705631c25893ca73a93dabff59c2d4274c15db7b2dbf5'}
_LEGACY_NO_JOB = {}
_JOURNAL_NEW = _INITIAL_DIRECTORY_PROVEN = False
_STATE_PARENT_FD = _STATE_PARENT_ID = _STATE_PARENT_PATH = None
_PUBLISHER_GATE_FAILED = False
_LAST_KNOWN_STATE = None
_JOURNAL_LOCKS = {}
_JOURNAL_GUARDS = {}
_VERIFIED_REMOTE_CREATE = None
_SCANNED_HISTORY = {}
_SCANNED_JOB_DIGESTS = {}
_ATTEMPT_HEADS = {}
_INVENTORY_ACTIVE = False
_ROBOTS_CONTEXT = None
_INFRASTRUCTURE_UNTIL = {}
_LOCAL_FIRST_ACCESS_CHECKED = False
_COLLECTION_PREPARED = False
_RESTORED_LATEST = _RESTORED_HISTORY = None
_PUBLISHER_HOSTS = {
    "www.dzs.cz",
    "www.salto-youth.net",
    "studyin.gov.cz",
    "studujnavs.gov.cz",
}
_LEGACY_ATTEMPTS = {
    38823222698: {
        "source": "cz-dzs",
        "head_sha": "77b5911162e0ab635225298393523b409638e6b1",
        "workflow_id": 378390058,
        "jobs_sha256": "7c957fb06396b81839b228890feeefeb94c706ccc4085f81a3fadbc325c77c21",
    },
    38763419564: {
        "source": "cz-dzs",
        "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "workflow_id": 378390058,
        "jobs_sha256": "ea7e4d16f6d4b8571c3415b9567d0a244443ce4a707f039041ecc52ec07f41ec",
    },
    38721259429: {
        "source": "cz-dzs",
        "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "workflow_id": 378390058,
        "jobs_sha256": "9186da00cec11ba28ce14df864384492a055247bc2edcd025746f3ae6f78a2b4",
    },
    38720083895: {
        "source": "cz-dzs",
        "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "workflow_id": 378390058,
        "jobs_sha256": "63defa8c0e6a3a5f10e09f76550782f2b23e75bce8e5891fe9e503dd6ffc8ef8",
    },
    38684235960: {
        "source": "cz-dzs",
        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "workflow_id": 378390058,
        "jobs_sha256": "c9b2ea0f6e0cebe96a620ee3c6d9b178fcddb39acd62a5d997ac2076ddacba42",
    },
    38534756577: {
        "source": "cz-dzs",
        "head_sha": "5ab9b0237b7fa2b043fe276ec43f29e493c685ab",
        "workflow_id": 378390058,
        "jobs_sha256": "637a957e51b510e5ce2ab9e5a078e1c7628f3c4f807c6baebd83b4440ad4b22d",
    },
    38507918475: {
        "source": "cz-dzs",
        "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "workflow_id": 378390058,
        "jobs_sha256": "ca4acc29e241036827b8ae4ab01175ebfbd1bbcae8bd2038646a7459a22fe9a5",
    },
    38247977325: {
        "source": "cz-dzs",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390058,
        "jobs_sha256": "d65b1480a790a7899015c845525502229c27353825e9b3295a4520324819e11a",
    },
    38004213621: {
        "source": "cz-dzs",
        "head_sha": "029a500dbf22c1d44ae592c667f26f33d9d364c8",
        "workflow_id": 378390058,
        "jobs_sha256": "21566ddb8205997ed7b4394d4f0533f47c691cfb9ac47d965673dc4ef825ab86",
    },
    38003386372: {
        "source": "cz-dzs",
        "head_sha": "029a500dbf22c1d44ae592c667f26f33d9d364c8",
        "workflow_id": 378390058,
        "jobs_sha256": "c2e54ae6a2143a6f08a780c9dbfa111eebdda6549e55bd3cdb15349b0ecb9011",
    },
    38763419357: {
        "source": "cz-study-in-czechia",
        "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "workflow_id": 378390060,
        "jobs_sha256": "c9be393842e0f147f74fd009a1d3630b5913cd1cd5de1f6950b15f5cdc71ef84",
    },
    38721259427: {
        "source": "cz-study-in-czechia",
        "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "workflow_id": 378390060,
        "jobs_sha256": "d0ccaff64101f4704d59a6c691b7f37fb4f022da15a201df5cf63d41cb4e8216",
    },
    38720083860: {
        "source": "cz-study-in-czechia",
        "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "workflow_id": 378390060,
        "jobs_sha256": "446e80e131e02e803c7acc6dba163aedd020356cb4957f86a85ceb1969aee812",
    },
    38684236154: {
        "source": "cz-study-in-czechia",
        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "workflow_id": 378390060,
        "jobs_sha256": "5e5e655f07b5bee5893af6a68b3b4d579787e11cb09511b8ab5b20eda9083c13",
    },
    38536206743: {
        "source": "cz-study-in-czechia",
        "head_sha": "3f6ab17f4698de58f7365682a22384cd08788f69",
        "workflow_id": 378390060,
        "jobs_sha256": "5c16863ef20d6771f0f904e842fc75a825e1c018ce0e292a38af0e1ef83480d0",
    },
    38507918678: {
        "source": "cz-study-in-czechia",
        "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "workflow_id": 378390060,
        "jobs_sha256": "6efc717093320b5da7e7c2c5441b25bee647d060d5d400f2a6bc728e7d857eb5",
    },
    38245510407: {
        "source": "cz-study-in-czechia",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390060,
        "jobs_sha256": "aa70fec7e0a9caa2495bcd3c8494702afe5077cab725fc830bd4d1113622cd53",
    },
    37988752436: {
        "source": "cz-study-in-czechia",
        "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "workflow_id": 378390060,
        "jobs_sha256": "313265d7cf0188252a10d488f1f992ca03fd193f478c68c62126c230de854a2d",
    },
    37979963485: {
        "source": "cz-study-in-czechia",
        "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "workflow_id": 378390060,
        "jobs_sha256": "39ed4466a002d3acf4b11c507b60061734ad8e126058522c830f5c32839d17da",
    },
    37962105063: {
        "source": "cz-study-in-czechia",
        "head_sha": "251d29d718aa4d79f4b0b93b8512cb82bb82ce23",
        "workflow_id": 378390060,
        "jobs_sha256": "348ce4b367ccb63e19d5a4f0238874f6bff953d6e93b577f8a0c7140b1376bc6",
    },
}

_LEGACY_ACCESS_OBSERVATIONS = [
    {
        "run_id": 37962105063,
        "run_attempt": 1,
        "source": "cz-study-in-czechia",
        "head_sha": "251d29d718aa4d79f4b0b93b8512cb82bb82ce23",
        "status": 403,
        "route": None,
        "purpose": "unknown",
        "log_sha256": "aa6dd64a4a47e375977919cdd43012e46a5a25a0a78554933b974ccf2f2b6279",
    },
    {
        "run_id": 38245510407,
        "run_attempt": 1,
        "source": "cz-study-in-czechia",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "status": 403,
        "route": "http://static.daad.de/robots.txt",
        "purpose": "literal_robots_excluded_historical_document",
        "log_sha256": "598ac007b9571a5dd5e409ebef862c8fbff6ca7a5b540687e2c048c3de73a853",
    },
]


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
            type(value) in (int, float)
            and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def empty_budget(now):
    return {
        "schema": 3,
        "family": FAMILY,
        "legacy_hold_history": "unlogged_fields_unknown",
        "legacy_access_history_unknown": True,
        "legacy_access_observations": copy.deepcopy(
            _LEGACY_ACCESS_OBSERVATIONS
        ),
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
        or state["legacy_access_history_unknown"] is not True
        or state["legacy_access_observations"] != _LEGACY_ACCESS_OBSERVATIONS
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


class JournalAuthorityChanged(AdapterError):
    """A replacement journal cannot inherit a stale descriptor's authority."""


def locked_journal_authority():
    """Bind normal reads and writes to the still-locked current journal."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    try:
        collection_lock_authority()
        if len(_JOURNAL_LOCKS) != 1:
            raise ValueError("Exactly one original journal lock required")
        descriptor, identity = next(iter(_JOURNAL_LOCKS.items()))
        guard = _JOURNAL_GUARDS[descriptor]
        parent = state_parent()
        name = os.path.basename(_STATE_PATH)
        for held in (descriptor, guard):
            info = validate_state_descriptor(held, parent, name)
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("Retained journal identity changed")
        # Reacquiring an existing flock on its own open description is safe.
        # A reused FD for another open description conflicts with the guard.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return guard
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        raise JournalAuthorityChanged(
            "Locked current publisher journal changed", "access"
        ) from error


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
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
        raise JournalAuthorityChanged(
            "Publisher journal path changed", "access"
        )
    validate_owned_output(info)
    state_parent(_STATE_PARENT_PATH)
    return info


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _INITIAL_DIRECTORY_PROVEN
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
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
    _STATE_PATH = _STATE_LOCK = os.path.join(directory, "state.json")


def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        if (
            _INITIAL_DIRECTORY_PROVEN
            and not _RESTORED
            and _VERIFIED_REMOTE_CREATE is not None
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
                close_retained_descriptor(
                    fd, (acquired.st_dev, acquired.st_ino)
                )
            except BaseException:
                pass
        raise
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    file = os.fdopen(fd, "r+", closefd=False)
    try:
        fcntl.flock(file.fileno(), fcntl.LOCK_EX)
        info = validate_state_descriptor(file.fileno(), parent, name)
        return BudgetLock(file, info)
    except BaseException:
        fail_publisher_cleanup()
        try:
            file.close()
        except BaseException:
            pass
        try:
            close_retained_descriptor(fd, (info.st_dev, info.st_ino))
        except BaseException:
            pass
        raise


def load_budget():
    authority = locked_journal_authority()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    fd = os.dup(authority)
    with os.fdopen(fd, "rb") as file:
        validate_state_descriptor(file.fileno(), parent, name)
        file.seek(0)
        raw = file.read(16385)
        validate_state_descriptor(file.fileno(), parent, name)
        locked_journal_authority()
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher journal", "access")
    if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
        return validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
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
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if not isinstance(error, JournalAuthorityChanged):
            try:
                locked_journal_authority()
                invalidate_failed_journal()
            except BaseException:
                # Failed cleanup never replaces the original persistence error.
                pass
        raise


def retained_descriptor_info(descriptor, identity):
    """Validate original ownership without adopting a named replacement."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or info.st_nlink not in (0, 1)
    ):
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
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
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
    """Retain owned original descriptors without implicit foreign-FD close."""

    def __init__(self, file, info):
        self.file = file
        self.descriptor = file.fileno()
        self.identity = (info.st_dev, info.st_ino)
        self.guard = os.dup(self.descriptor)
        _JOURNAL_LOCKS[self.descriptor] = self.identity
        _JOURNAL_GUARDS[self.descriptor] = self.guard

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        primary = sys.exc_info()[0] is not None
        if self.descriptor is None:
            return
        descriptor, guard = self.descriptor, self.guard
        self.descriptor = self.guard = None
        _JOURNAL_LOCKS.pop(descriptor, None)
        _JOURNAL_GUARDS.pop(descriptor, None)
        failure = None
        safe = []
        for held in (guard, descriptor):
            try:
                retained_descriptor_info(held, self.identity)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                safe.append(held)
            except BaseException as error:
                failure = failure or error
        try:
            # Flush the unused, non-owning wrapper before closing owned FDs.
            # The wrapper cannot close a numeric FD even if it was reused.
            self.file.close()
        except BaseException as error:
            failure = failure or error
        for held in safe:
            try:
                close_retained_descriptor(held, self.identity)
            except BaseException as error:
                failure = failure or error
        if failure is not None:
            fail_publisher_cleanup()
            if not primary:
                raise failure


def invalidate_failed_journal():
    """Invalidate only original held inodes, never a newer named file."""
    state_parent()
    if not _JOURNAL_LOCKS:
        raise AdapterError(
            "No retained journal descriptor after failure", "access"
        )
    held = []
    for descriptor, identity in list(_JOURNAL_LOCKS.items()):
        guard = _JOURNAL_GUARDS[descriptor]
        # Validate every original object before mutating any descriptor.
        for candidate in (guard, descriptor):
            retained_descriptor_info(candidate, identity)
            held.append((candidate, identity))
    for descriptor, identity in held:
        retained_descriptor_info(descriptor, identity)
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        retained_descriptor_info(descriptor, identity)


def write_budget_body(state):
    """Persist one validated state while its original journal lock is held."""
    global _INITIAL_DIRECTORY_PROVEN
    state = validate_budget(state)
    authority = locked_journal_authority()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    fd = os.dup(authority)
    with os.fdopen(fd, "r+") as file:
        info = validate_state_descriptor(file.fileno(), parent, name)
        locked_journal_authority()
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
        locked_journal_authority()


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
    result["legacy_access_history_unknown"] = (
        left["legacy_access_history_unknown"]
        or right["legacy_access_history_unknown"]
    )
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
    return min(
        _PUBLISHER_TIMEOUT if publisher else _DEFAULT_HTTP_TIMEOUT, remaining
    )


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
    global _RUN_END, _COLLECTION_LOCK_FD, _COLLECTION_PREPARED
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _COLLECTION_PREPARED = False
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = None
        close_collection_lock()


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
    if (os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [os.environ.get(key, "") for key in ((
        "GITHUB_RUN_ID"
    ), (
        "GITHUB_RUN_ATTEMPT"
    ))]
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
            or run["workflow_id"] != _WORKFLOW_IDS[source]
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
                    "Workflow inventory remained unstable", "access") from None


class InventoryRace(AdapterError):
    """The advertised complete workflow inventory changed during reading."""


def source_revision_digest(head, source):
    """Bind an artifact code digest to its authenticated source revision."""
    if not re.fullmatch(r"[0-9a-f]{40}", head or ""):
        raise ValueError("Invalid source revision")
    if source not in FAMILY_SOURCES:
        raise ValueError("Unexpected family source")
    path = "adapters/" + source + "/adapter.py"
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


def download_inert_artifact(
    identity, artifact_id, digest=None, *, legacy=False
):
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
        if legacy:
            return validate_reviewed_legacy_envelope(envelope, identity)
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
            != source_revision_digest(envelope["head_sha"], source)
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
        raise AdapterError(
            "Source implementation exceeds reviewed bound", "access"
        )
    return hashlib.sha256(content).hexdigest()


@contextlib.contextmanager
def owned_artifact_directory(path):
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
        os.close(descriptor)


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
        info = os.stat(
            "pacing-state.json", dir_fd=directory, follow_symlinks=False
        )
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
    path = os.environ.get("DZS_PACING_ARTIFACT_PATH")
    if not path:
        return
    with owned_artifact_directory(path) as (root, directory):
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
    """Atomically export trusted state; invalidate owned output on failure."""
    path = os.environ.get("DZS_PACING_ARTIFACT_PATH")
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
                raise AdapterError(
                    "Unsettled state cannot be exported", "access"
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
            os.replace(
                temporary,
                "pacing-state.json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary = None
            same_owned_artifact(root, directory, temporary_info)
            workflow_output_signal("true")
        except BaseException:
            invalidate_owned_artifact(root, directory)
            raise
        finally:
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
def physical_request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=30_000_000,
    infrastructure=False,
):
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if type(byte_limit) is not int or not 0 < byte_limit <= 30_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    purpose_target = urllib.parse.urlsplit(url)
    if robots and (
        purpose_target.path != "/robots.txt"
        or purpose_target.query
        or purpose_target.fragment
    ):
        raise AdapterError(
            "Robots purpose requires literal robots.txt", "access"
        )
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    publisher = host in _PUBLISHER_HOSTS
    if not publisher and not infrastructure:
        if byte_limit != 30_000_000:
            raise AdapterError("Invalid legacy transport byte bound", "fetch")
        return legacy_request_bytes(
            url,
            method=method,
            headers=headers,
            payload=payload,
            byte_limit=byte_limit,
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
    if publisher:
        collection_lock_authority()
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = pace(interval) if publisher else (None, None)
    attempted = response_received = response_constraints_saved = False
    retryable_connection_failure = False
    try:
        if publisher:
            collection_lock_authority()
            verify_local_publisher_start()
            locked_journal_authority()
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
        timeout = request_timeout(publisher)
        if publisher:
            collection_lock_authority()
            locked_journal_authority()
        try:
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
            if publisher and not (
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
            ):
                raise AdapterError(
                    "Unclassified connection failure; pending retained",
                    "access",
                ) from error
            retryable_connection_failure = True
            raise
        response_received = True
        with (
            header_rate_limit_response(response)
            if publisher and response.status == 429
            else response
        ):
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
                if status == 429:
                    raise HeaderRateLimitStop(
                        "Publisher stopped: HTTP 429", "access", 429
                    )
            declared = response.headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) > byte_limit
            ):
                raise AdapterError(
                    "Oversized or invalid response length", "fetch"
                )
            if (
                response.headers.get("Content-Encoding", "identity")
                != "identity"
            ):
                raise AdapterError(
                    "Unsupported response content encoding", "fetch"
                )
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
            if (
                len(body) > byte_limit
                or declared
                and len(body) != int(declared)
            ):
                raise AdapterError(
                    "Response exceeds native byte bound", "fetch"
                )
            challenge = (
                publisher
                and not (robots and 400 <= status < 500 and status != 429)
                and visible_access_challenge(body, status == 429)
            )
            if publisher and not (robots and status == 200):
                response_constraints_saved = True
            if challenge:
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
    except HeaderRateLimitStop:
        # A completed response exit precedes assessment.
        response_constraints_saved = True
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if publisher and response_received:
            response_constraints_saved = False
            raise AdapterError(
                "Received publisher response failed; no connection retry",
                "access",
            ) from error
        raise
    finally:
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if (
                    attempted
                    and not _PUBLISHER_GATE_FAILED
                    and (
                        response_constraints_saved
                        or not response_received
                        and retryable_connection_failure
                    )
                ):
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


class PhaseExpired(BaseException):
    """An absolute phase limit cannot be retried as an HTTP error."""


def local_workflow_definition():
    """Both shared-state producers must be paused before local access."""
    for source, identity in _WORKFLOW_IDS.items():
        value = workflow_api(f"/actions/workflows/{identity}")
        if (
            value.get("id") != identity
            or value.get("path") != f".github/workflows/fetch-{source}.yml"
            or value.get("state") != "disabled_manually"
        ):
            raise AdapterError(
                "Local collection requires both family workflows paused",
                "access",
            )


def scan_family_history():
    """Read both complete producer histories and rank exact job completion."""
    global _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS
    _SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS = {}, {}, {}
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else (None, None)
    if not hosted:
        local_workflow_definition()
    seen, candidates, current_seen = set(), [], False
    for expected_source, workflow in _WORKFLOW_IDS.items():
        total, count = None, 0
        for page in range(1, 101):
            value = workflow_api(
                f"/actions/workflows/{workflow}/runs?branch=main&per_page=100&page={page}"
            )
            size, runs = value.get("total_count"), value.get("workflow_runs")
            if (
                type(size) is not int
                or size < 0
                or not isinstance(runs, list)
                or len(runs) > 100
            ):
                raise AdapterError("Invalid family run inventory", "access")
            if total is not None and size != total:
                raise InventoryRace("Family inventory changed", "access")
            total = size
            count += len(runs)
            for run in runs:
                identity, attempt = run.get("id"), run.get("run_attempt")
                if (
                    type(identity) is not int
                    or identity <= 0
                    or identity in seen
                    or type(attempt) is not int
                    or attempt <= 0
                    or family_source(run) != expected_source
                    or run.get("workflow_id") != workflow
                    or run.get("repository", {}).get("full_name")
                    != "YouthOpps/data-pipeline"
                    or run.get("head_repository", {}).get("full_name")
                    != "YouthOpps/data-pipeline"
                    or run.get("head_branch") != "main"
                ):
                    raise AdapterError(
                        "Untrusted family run identity", "access"
                    )
                seen.add(identity)
                if identity == current[0]:
                    if (
                        attempt != current[1]
                        or expected_source != SOURCE_ID
                        or run.get("head_sha") != os.environ.get("GITHUB_SHA")
                        or run.get("status")
                        not in (
                            "in_progress",
                            "queued",
                            "pending",
                            "waiting",
                            "requested",
                        )
                    ):
                        raise AdapterError(
                            "Current family attempt changed", "access"
                        )
                    current_seen = True
                    if attempt > 1:
                        prior = workflow_api(
                            f"/actions/runs/{identity}/attempts/{attempt - 1}"
                        )
                        candidates.append(
                            completed_identity(
                                prior,
                                (identity, attempt - 1, expected_source),
                                (run["head_sha"], workflow),
                            )
                        )
                    continue
                _SCANNED_HISTORY[(identity, attempt)] = (
                    run.get("head_sha"),
                    workflow,
                )
                if not hosted and run.get("status") != "completed":
                    raise AdapterError(
                        "Local family history is not terminal", "access"
                    )
                if run.get("status") in (
                    "queued",
                    "waiting",
                    "requested",
                    "pending",
                ):
                    if attempt > 1:
                        prior = workflow_api(
                            f"/actions/runs/{identity}/attempts/{attempt - 1}"
                        )
                        candidates.append(
                            completed_identity(
                                prior,
                                (identity, attempt - 1, expected_source),
                                (run["head_sha"], workflow),
                            )
                        )
                    continue
                if run.get("status") != "completed":
                    raise AdapterError(
                        "Another family publisher is active", "access"
                    )
                candidates.append(completed_identity(run))
            if count >= total:
                if count != total:
                    raise AdapterError(
                        "Family inventory count mismatch", "access"
                    )
                break
            if len(runs) != 100:
                raise AdapterError("Incomplete family inventory", "access")
        else:
            raise AdapterError(
                "Family inventory exceeds bounded metadata scan", "access"
            )
    if hosted and not current_seen:
        raise AdapterError(
            "Current attempt missing from complete family history", "access"
        )
    ordered = sorted(candidates)
    if not ordered:
        raise AdapterError("Existing publisher history missing", "access")
    if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
        raise AdapterError("Ambiguous actual family completion", "access")
    return ordered[-1][2:]


def validate_reviewed_legacy_envelope(envelope, identity):
    """No generic old schema acceptance: exact known legacy attempt only."""
    if identity != (38823222698, 1, "cz-dzs"):
        raise ValueError("Unreviewed legacy owner")
    if (
        not isinstance(envelope, dict)
        or set(envelope)
        != {"schema", "repository", "run_id", "run_attempt", "source", "state"}
        or type(envelope["schema"]) is not int
        or envelope["schema"] != 1
        or envelope["repository"] != "YouthOpps/data-pipeline"
        or envelope["run_id"] != identity[0]
        or type(envelope["run_id"]) is not int
        or envelope["run_attempt"] != 1
        or type(envelope["run_attempt"]) is not int
        or envelope["source"] != identity[2]
    ):
        raise ValueError("Untrusted legacy envelope")
    state = envelope["state"]
    if (
        not isinstance(state, dict)
        or set(state)
        != {
            "schema",
            "family",
            "observed_at",
            "not_before",
            "starts",
            "blocked",
        }
        or type(state["schema"]) is not int
        or state["schema"] != 1
        or state["family"] != FAMILY
        or type(state["blocked"]) is not bool
        or not numeric(state["observed_at"])
        or not numeric(state["not_before"])
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 10
        or state["starts"] != sorted(set(state["starts"]))
        or any(
            not numeric(t) or t > state["observed_at"] for t in state["starts"]
        )
        or state["observed_at"] > time.time() + 1
    ):
        raise ValueError("Invalid legacy constraints")
    return state


def reviewed_migration_state(legacy):
    """Require the explicit two-producer controlled-boundary approval."""
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
            "source",
            "evidence",
            "state",
            "state_sha256",
            "legacy_state_sha256",
        }
        or marker["schema"] != 1
        or type(marker["schema"]) is not int
        or marker["source"] != SOURCE_ID
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/289#issuecomment-[0-9]+",
            marker.get("evidence", ""),
        )
        or hashlib.sha256(
            json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        != marker["legacy_state_sha256"]
        or hashlib.sha256(
            json.dumps(
                marker["state"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        != marker["state_sha256"]
    ):
        raise AdapterError(
            "Explicit reviewed family migration missing", "access"
        )
    state = validate_budget(copy.deepcopy(marker["state"]))
    if (
        state["legacy_access_history_unknown"] is not True
        or state["research_provenance"].get("unscoped_403_log_sha256")
        != _LEGACY_ACCESS_OBSERVATIONS[0]["log_sha256"]
    ):
        raise AdapterError(
            "Known historical access uncertainty not retained", "access"
        )
    state["until"] = max(state["until"], legacy["not_before"], time.time() + 60)
    state["observed_at"] = max(
        state["observed_at"], legacy["observed_at"], time.time()
    )
    state["starts"] = sorted(set(state["starts"] + legacy["starts"]))
    state["blocked"] = state["blocked"] or legacy["blocked"]
    return validate_budget(state)


def restore_family_artifact():
    """One exact reviewed v1 handoff, then strict newest modern owned state."""
    global _RESTORED, _STATE_TRUSTED, _RESTORED_LATEST, _RESTORED_HISTORY
    global _VERIFIED_REMOTE_CREATE
    _STATE_TRUSTED = False
    latest = latest_family_run()
    run_id, attempt, source = latest
    if run_id in _LEGACY_ATTEMPTS:
        expected = {
            (i, 1): (v["head_sha"], v["workflow_id"])
            for i, v in _LEGACY_ATTEMPTS.items()
        }
        if latest != (38823222698, 1, "cz-dzs") or _SCANNED_HISTORY != expected:
            raise AdapterError(
                "Reviewed complete legacy family changed", "access"
            )
        for identity, value in _LEGACY_ATTEMPTS.items():
            if (
                _SCANNED_JOB_DIGESTS.get((identity, 1, 1))
                != value["jobs_sha256"]
            ):
                raise AdapterError(
                    "Reviewed exact legacy jobs changed", "access"
                )
    value = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = value.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or value.get("total_count") != len(artifacts)
        or len(artifacts) > 100
    ):
        raise AdapterError(
            "Incomplete newest family artifact inventory", "access"
        )
    matches = [
        a for a in artifacts if a.get("name") == f"{_ARTIFACT_NAME}-{attempt}"
    ]
    if len(matches) != 1:
        raise AdapterError(
            "Newest family state missing; no older fallback", "access"
        )
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("expired") is not False
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha")
        != _ATTEMPT_HEADS.get((run_id, attempt))
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
    ):
        raise AdapterError(
            "Newest owned family artifact binding invalid", "access"
        )
    if run_id in _LEGACY_ATTEMPTS:
        if (
            artifact["id"] != 12012799357
            or artifact["digest"]
            != "sha256:30e404a497e17ed0f3a30021acdfe7ae32ed407177024469fa408d4e5e01c885"
        ):
            raise AdapterError("Reviewed legacy artifact changed", "access")
        legacy = download_inert_artifact(
            latest, artifact["id"], artifact["digest"], legacy=True
        )
        incoming = reviewed_migration_state(legacy)
    else:
        incoming = download_inert_artifact(
            latest, artifact["id"], artifact["digest"]
        )
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


def prepare_collection():
    """Verify both producers before any family state initialization."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT
    global _LOCAL_FIRST_ACCESS_CHECKED
    global _COLLECTION_STARTED, _COLLECTION_PREPARED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        local_workflow_definition()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(tempfile.gettempdir(), FAMILY + ".lock")
        descriptor = info = None
        try:
            descriptor = os.open(
                path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            info = os.fstat(descriptor)
            validate_owned_output(info)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            retained_descriptor_info(descriptor, (info.st_dev, info.st_ino))
            current = os.stat(path, follow_symlinks=False)
            validate_owned_output(current)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError("Lifecycle acquisition changed")
        except BaseException:
            fail_publisher_cleanup()
            if descriptor is not None and info is not None:
                try:
                    close_retained_descriptor(
                        descriptor, (info.st_dev, info.st_ino)
                    )
                except BaseException:
                    pass
            raise AdapterError(
                "Unsafe or active shared family lifecycle lock", "access"
            ) from None
        try:
            guard = os.dup(descriptor)
        except BaseException:
            fail_publisher_cleanup()
            try:
                close_retained_descriptor(
                    descriptor, (info.st_dev, info.st_ino)
                )
            except BaseException:
                pass
            raise
        _COLLECTION_LOCK_FD = descriptor
        _COLLECTION_LOCK_CONTEXT = (
            descriptor, guard, (info.st_dev, info.st_ino), path
        )
    collection_lock_authority()
    _LOCAL_FIRST_ACCESS_CHECKED = False
    _COLLECTION_STARTED = time.time()
    restore_family_artifact()
    _COLLECTION_PREPARED = True


def save_budget(state):
    """Every legacy caller uses the same failure-sticky modern write."""
    return write_budget(state)


def applicable_robots_timing(text):
    """Assess selected raw timing directives that stdlib cannot represent."""
    groups, agents, directives, state = [], [], [], 0
    for original in text.splitlines():
        if not original:
            if state == 2:
                groups.append((agents, directives))
            agents, directives, state = [], [], 0
        line = original.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = urllib.parse.unquote(value.strip())
        if key == "user-agent":
            if state == 2:
                groups.append((agents, directives))
                agents, directives = [], []
            agents.append(value.lower())
            state = 1
        elif state and key in {
            "allow", "disallow", "crawl-delay", "request-rate"
        }:
            if key in {"crawl-delay", "request-rate"}:
                directives.append((key, value))
            state = 2
    if state == 2:
        groups.append((agents, directives))
    product = _USER_AGENT.split("/", 1)[0].lower()
    selected = [
        values for names, values in groups
        if any(name != "*" and name in product for name in names)
    ]
    if not selected:
        selected = [values for names, values in groups if "*" in names]
    delay = 12.2
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    try:
        for values in selected:
            for key, value in values:
                if key == "crawl-delay":
                    if not re.fullmatch(decimal, value):
                        raise ValueError("Unsupported crawl delay")
                    seconds = float(value)
                    if not numeric(seconds):
                        raise ValueError("Invalid crawl delay")
                else:
                    match = re.fullmatch(
                        r"([0-9]+)\s*/\s*(" + decimal + r")", value
                    )
                    if not match:
                        raise ValueError("Unsupported request rate")
                    requests = int(match[1])
                    period = float(match[2])
                    if requests <= 0 or not numeric(period) or period <= 0:
                        raise ValueError("Invalid request rate")
                    seconds = period / requests + 0.01
                if not numeric(seconds):
                    raise ValueError("Unsupported timing magnitude")
                datetime.fromtimestamp(time.time() + seconds, timezone.utc)
                delay = max(delay, seconds)
    except (ValueError, OverflowError, OSError):
        raise AdapterError(
            "Unsupported applicable robots timing; assessment incomplete",
            "access",
        ) from None
    return delay


def robots_policy(text, url):
    """Preserve native parsed group rules and retain stricter directives."""
    import urllib.robotparser

    # Preserve native agent/rule grouping while timing is checked separately.
    # Stdlib ignores decimals and can overflow on an unrelated numeric field.
    policy_lines = []
    for line in text.splitlines():
        field = line.split("#", 1)[0].split(":", 1)
        if len(field) == 2 and field[0].strip().lower() in {
            "crawl-delay", "request-rate"
        }:
            policy_lines.append("Crawl-delay: 0")
        else:
            policy_lines.append(line)
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(policy_lines)
    if not robots_directive(parser, "can_fetch", url):
        raise RobotsDisallow("Robots excludes required current input", "access")
    return applicable_robots_timing(text)


def check_robots(url):
    """Cache source-local REP while guarding every target policy decision."""
    global _ROBOTS_CONTEXT
    parsed = urllib.parse.urlsplit(public_url(url))
    if (
        parsed.path == "/robots.txt"
        and not parsed.query
        and not parsed.fragment
    ):
        return 12.2
    origin = parsed.scheme + "://" + parsed.netloc
    if origin in _ROBOTS:
        text = _ROBOTS[origin]
        return 12.2 if text is None else apply_robots_policy(text, url)
    previous = _ROBOTS_CONTEXT
    context = {"target": url, "delay": None}
    _ROBOTS_CONTEXT = context
    try:
        status, headers, body = request_bytes(
            origin + "/robots.txt", publisher=True
        )
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
            return 12.2
        if status != 200:
            raise AdapterError(
                "Required robots policy unavailable", "access", status
            )
        text = body.decode("utf-8", "strict")
        _ROBOTS[origin] = text
        return (
            context["delay"]
            if context["delay"] is not None
            else apply_robots_policy(text, url)
        )
    finally:
        _ROBOTS_CONTEXT = previous


def service_unavailable_backoff():
    """Keep the one native503 retry and its assessed policy transaction."""
    with locked_budget():
        state = load_budget()
        if state["in_flight"] or state["blocked"]:
            raise AdapterError("Unsettled publisher state", "access")
        state["in_flight"] = True
        write_budget(state)
        state["until"] = max(state["until"], time.time() + 30)
        state["observed_at"] = time.time()
        write_budget(state)
        state["in_flight"] = False
        write_budget(state)


class RobotsDisallow(AdapterError):
    """A genuine applicable REP restriction, retained independently."""


def request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
    infrastructure=False,
):
    """Preserve ordinary GitHub transport and guard each publisher hop."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if infrastructure:
        if host in _PUBLISHER_HOSTS:
            raise AdapterError(
                "Publisher cannot use metadata transport", "access"
            )
        return physical_request_bytes(
            url,
            method=method,
            headers=headers,
            payload=payload,
            byte_limit=byte_limit,
            infrastructure=True,
        )
    if not publisher and host not in _PUBLISHER_HOSTS:
        return legacy_request_bytes(
            url,
            method=method,
            headers=headers,
            payload=payload,
            byte_limit=byte_limit,
        )
    current = public_url(url)
    for redirect in range(6):
        parsed = urllib.parse.urlsplit(current)
        literal_robots = (
            parsed.path == "/robots.txt"
            and not parsed.query
            and not parsed.fragment
        )
        status, response_headers, body = physical_request_bytes(
            current,
            interval=12.2,
            method=method,
            headers=headers,
            payload=payload,
            robots=literal_robots,
            byte_limit=byte_limit,
        )
        location = response_headers.get("Location")
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = public_url(urllib.parse.urljoin(current, location))
            next_target = urllib.parse.urlsplit(destination)
            if literal_robots and (
                next_target.path != "/robots.txt"
                or next_target.query
                or next_target.fragment
            ):
                raise AdapterError(
                    "Robots redirect leaves the literal policy route", "access"
                )
            check_robots(destination)
            current = destination
            continue
        if not literal_robots and status in (401, 403, 451):
            raise AdapterError(
                f"Publisher refused access: HTTP {status}", "access", status
            )
        return status, response_headers, body
    raise AdapterError("Unresolved redirect", "access")


def legacy_request_bytes(
    url,
    method="GET",
    headers=None,
    payload=None,
    publisher=False,
    byte_limit=30000000,
):
    """Bound response bytes; redirects are explicit and credential-safe."""
    if (
        publisher
        or (urllib.parse.urlsplit(url).hostname or "").lower()
        in _PUBLISHER_HOSTS
    ):
        raise AdapterError(
            "Publisher requires durable physical transport", "access"
        )
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if (
            urllib.parse.urlsplit(current).hostname or ""
        ).lower() in _PUBLISHER_HOSTS:
            raise AdapterError(
                "Publisher requires durable physical transport", "access"
            )
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        try:
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status
            response_headers = response.headers
            retry = response.headers.get("Retry-After")
            declared = response.headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) > byte_limit
            ):
                raise AdapterError(
                    ("Oversized or invalid response length"), ("fetch")
                )
            body = response.read(byte_limit + 1)
            if (
                len(body) > byte_limit
                or declared
                and len(body) != int(declared)
            ):
                raise AdapterError("Incomplete or oversized response", "fetch")
            encoding = response.headers.get("Content-Encoding", "identity")
            if encoding != "identity":
                raise AdapterError(
                    ("Unsupported response content encoding"), ("fetch")
                )
            location = response.headers.get("Location")
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            old, new = (
                urllib.parse.urlsplit(current),
                urllib.parse.urlsplit(destination),
            )
            if new.scheme != "https" or new.username or new.password:
                raise AdapterError(
                    ("Unsafe authenticated redirect"), ("publish")
                )
            if new.netloc != old.netloc:
                headers = {
                    key: value
                    for key, value in headers.items()
                    if key.lower() != "authorization"
                }
            if urllib.parse.urlsplit(url).hostname == "api.github.com":
                raise AdapterError(
                    ("Unexpected GitHub API redirect"), ("publish")
                )
            current = destination
            continue
        return status, response_headers, body
    raise AdapterError("Unresolved redirect", "access")


def collect():
    """Enter the complete original graph with fresh family continuity."""
    global _COLLECTION_PREPARED
    if _PHASE_END is not None:
        check_deadline()
        if not _COLLECTION_PREPARED:
            prepare_collection()
        _COLLECTION_PREPARED = False
        return parse_inventory(read_pages())
    with execution():
        with phase("prepublication", _EFFECTIVE_COLLECTION, reserve=390):
            prepare_collection()
            _COLLECTION_PREPARED = False
            return parse_inventory(read_pages())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        with execution():
            return run_lifecycle(args.publish)
    except PhaseExpired:
        return 1


class HeaderRateLimitStop(AdapterError):
    """A header-only HTTP 429 stop assessed after safe closure."""


@contextlib.contextmanager
def header_rate_limit_response(response):
    """Retain existing barriers when closing an HTTP 429 fails."""
    try:
        with response:
            yield
    except HeaderRateLimitStop:
        raise
    except BaseException as error:
        error._header429_unassessed = True
        # Latch before any fallible best-effort diagnostics.
        with contextlib.suppress(BaseException):
            fail_publisher_cleanup()
        raise


if __name__ == "__main__":
    sys.exit(main())
