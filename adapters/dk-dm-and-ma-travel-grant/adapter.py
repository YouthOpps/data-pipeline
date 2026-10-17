"""Collect reviewed DM student funding and member training."""

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

SOURCE_ID = "dk-dm-and-ma-travel-grant"
SOURCE_URL = "https://dm.dk/students/membership/travel-grant/"
WEBSITE_URL = "https://dm.dk/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "DK"
PUBLISHER_TYPE = "non-profit"
ATTRIBUTION = "DM Students — DM and MA student membership programmes"


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


def page(url):
    document = fetch_source(
        url, min_interval=check_robots(url), inventory=is_inventory(url)
    )
    if not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", document, re.I
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    root = PublisherHTML(document).root
    try:
        check_access(root)
    except AdapterError as error:
        if error.stage != "access":
            raise
        retain_publisher_hold()
        raise PolicyStop(str(error), "access") from None
    return root


def main_content(root):
    return one(nodes(root, "main"), "complete public programme content")


MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "january januar jaanuar jaanuari",
            "february februar veebruar veebruari",
            "march marts märts märtsi",
            "april aprill aprilli",
            "may maj mai",
            "june juni juuni",
            "july juli juuli",
            "august augusti",
            "september sept september septemberi septembri",
            "october oktober oktoober oktoobri oktoobrist",
            "november novemberi novembri novembrini",
            "december decemberi detsember detsembri",
        ),
        1,
    )
    for name in names.split()
}
MONTH_PATTERN = "(?:" + "|".join(MONTHS) + ")"
DATE_PATTERN = re.compile(
    r"(?<!\d)(?:(?P<day>\d{1,2})(?:st|nd|rd|th|\.)?\s*(?:of\s+)?"
    r"(?P<month>"
    + MONTH_PATTERN
    + r")|(?P<month_first>"
    + MONTH_PATTERN
    + r")\s+(?P<day_last>\d{1,2})(?:st|nd|rd|th|\.)?)"
    r"(?:\s*,?\s*(?P<year>20\d{2}))?\b|"
    r"(?<!\d)(?P<n_day>\d{1,2})\.(?P<n_month>\d{1,2})\."
    r"(?P<n_year>20\d{2})(?!\d)",
    re.I,
)


def dated_values(text):
    """Resolve years only within one explicit application statement."""
    matches = list(DATE_PATTERN.finditer(text))
    years = {
        match["year"] or match["n_year"]
        for match in matches
        if match["year"] or match["n_year"]
    }
    if not years:
        years = set(re.findall(r"\b20\d{2}\b", text))
    values = []
    for match in matches:
        year = match["year"] or match["n_year"]
        if not year and len(years) == 1:
            year = next(iter(years))
        if not year:
            continue
        month = (
            int(match["n_month"])
            if match["n_month"]
            else MONTHS[(match["month"] or match["month_first"]).lower()]
        )
        day = int(match["day"] or match["day_last"] or match["n_day"])
        try:
            date = datetime(int(year), month, day).date().isoformat()
        except ValueError:
            continue
        values.append((match.start(), match.end(), date))
    return values


def content_blocks(root):
    """Retain semantic text blocks once, including lists and table rows."""
    result = []
    block_tags = {"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def visit(node):
        if node.tag in (
            "script",
            "style",
            "noscript",
            "form",
            "nav",
            "footer",
            "button",
        ):
            return
        if node.attrs.get("id", "").startswith(
            ("accordion--tulemused", "warning-modal")
        ):
            return
        if node.tag == "a" and node.attrs.get("id"):
            result.append(("anchor", node.attrs["id"]))
        if node.tag in block_tags:
            nested = [
                n for n in node.walk() if n is not node and n.tag in block_tags
            ]
            if not nested:
                for anchor in nodes(node, "a"):
                    if anchor.attrs.get("id"):
                        result.append(("anchor", anchor.attrs["id"]))
                text = node.text()
                if text:
                    result.append((node.tag, text))
                return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)
            elif child.strip():
                result.append(("text", " ".join(child.split())))

    visit(root)
    return result


def check_access(root):
    text = root.text()
    if re.search(
        r"(?:scraping|automated (?:access|collection)|text and data mining)"
        r".{0,100}(?:prohibited|not permitted|forbidden)|"
        r"(?:prohibit|forbid|do not permit).{0,100}(?:scraping|automated access)",
        text,
        re.I,
    ):
        raise AdapterError("Publisher restricts automated collection", "access")


def fact_evidence(sections):
    """Keep factual clauses and a full material-content change signature."""
    evidence = [ATTRIBUTION]
    for url, blocks in sections:
        material = "\n".join(text for _, text in blocks)
        evidence.append("Source: " + url)
        evidence.append(
            "HTML content SHA-256: "
            + hashlib.sha256(material.encode()).hexdigest()
        )
        heading = ""
        for tag, text in blocks:
            if tag.startswith("h"):
                heading = text
                continue
            text = re.sub(
                r"[\w.+-]+@[\w.-]+|\[email protected\]",
                "[publisher contact]",
                text,
            )
            if re.search(
                r"eligib|applican|appl(?:y|ication)|deadline|scholarship|grant|"
                r"funding|EUR|DKK|euros?|must|required|exclud|citizen|residen|"
                r"fee|tuition|accommod|reimburse|stipend|taotl|tähtaeg|euro|"
                r"õpp|koda(?:nik|kond)|elamis|hüvit|ansøg|legat|medlem|"
                r"kroner|kr\.|gratis|kurs|mentor|skal|støtte|udgift|kvitter|bilag|CVR|afregn|udlæg|"
                r"book|Teams|Microsoft|Google|Adobe|Office|Windows|LaTeX|Stata|Excel",
                text,
                re.I,
            ):
                evidence.append((heading + ": " if heading else "") + text)
    return list(dict.fromkeys(evidence))


def concise_summary(title, category, deadline, evidence):
    """Build a short factual index; all extracted conditions stay in evidence."""
    summary = title + ". " + category.capitalize() + " programme. "
    summary += (
        ("Application deadline: " + deadline + ". ")
        if deadline
        else "No unambiguous dated application deadline extracted. "
    )
    amounts = []
    for text in (
        clause
        for item in evidence
        for clause in re.split(r"(?<=[.!?])\s+(?=[A-ZÆØÅ])|;", item)
        if re.search(
            r"may apply for funding|annual grant fund|is distributed|"
            r"\bcosts\b|kan søge om støtte|samlede årlige legat|"
            r"fordeles halvårligt|\bkoster\b",
            clause,
            re.I,
        )
    ):
        for match in re.finditer(
            r"(?:EUR|DKK)\s*[\d][\d ,.]*(?:/\w+)?|\d[\d ,.]*(?:euros?|EUR|DKK|kroner|kr\.)",
            text,
            re.I,
        ):
            value = match[0].strip()
            if value not in amounts:
                amounts.append(value)
    if amounts:
        summary += (
            "Published amounts (rates, budgets or costs; see conditions): "
            + ", ".join(amounts)
            + ". "
        )
    summary += "Eligibility, benefits and application conditions are linked to the publisher in the supporting evidence."
    if len(summary.encode("utf-16-le")) // 2 > 600:
        summary = (
            title
            + ". "
            + (
                "Application deadline: " + deadline + ". "
                if deadline
                else "Application deadline not established. "
            )
            + "See publisher-linked evidence for eligibility, benefit rates, budgets and application conditions."
        )
    return summary


ENTRY_URLS = (
    "https://dm.dk/students/",
    "https://dm.dk/studerende/",
)
PROGRAMME_WORDS = re.compile(
    r"grant|fund|legat|sponsor|gratis.*kurs|free.*class|online.*cours|mentor",
    re.I,
)
LEGACY_ROUTES = {
    "/students/membership/travel-grant/": "travel-grant",
    "/studerende/medlemskab/legater-og-sponsorater/rejselegat/": "travel-grant",
    "/students/membership/activity-fund/": "activity-fund",
    "/studerende/medlemskab/legater-og-sponsorater/sponsoratpulje/": "activity-fund",
    "/students/membership/membership-benefits/come-to-free-classes-and-events/": "free-member-classes",
    "/studerende/medlemskab/medlemsfordele/gratis-kurser-og-arrangementer/": "free-member-classes",
    "/studerende/medlemskab/medlemsfordele/gratis-online-kurser/": "free-online-courses",
    "/studerende/medlemskab/medlemsfordele/faglige-medlemsfordele/mentorordning-for-studerende-ved-sling-uling-og-naku/": "sling-uling-naku-mentoring",
}


def own_url(value, base):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base, value))
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or (parsed.hostname or "").removeprefix("www.") != "dm.dk"
    ):
        return None
    if re.search(r"\.(?:pdf|docx?|xlsx?|jpg|png|zip)$", parsed.path, re.I):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if any(key not in ("page", "p") for key in query):
        return None
    return urllib.parse.urlunsplit(
        ("https", "dm.dk", parsed.path, parsed.query, "")
    )


def discovery_links(root, url):
    """Follow both languages' member hubs and their programme detail links."""
    for link in nodes(root, "a"):
        target = own_url(link.attrs.get("href", ""), url)
        if not target:
            continue
        path = urllib.parse.urlsplit(target).path
        if path == "/students/membership/":
            # This is the membership signup redirect, not a programme hub.
            continue
        membership = path.startswith(
            ("/students/membership/", "/studerende/medlemskab/")
        )
        excluded = re.search(
            r"insurance|forsikring|membership-dues|kontingent|kontante|financial|bank|meld-dig|join|mentorliste|studiekunde|studiekonto|smil|mofibo|studierabat",
            path,
        )
        if membership and excluded:
            continue
        policy = re.search(
            r"cookie|privatliv|privacy|handelsvilkaar|konkurrencebetingelser",
            path,
        )
        if policy or (membership and not excluded):
            yield target
        elif path == "/om-dm/refusion/":
            yield target
        elif (
            path.startswith(("/students/", "/studerende/"))
            and not re.search(r"/nyheder/|/login|/kontakt|/contact", path)
            and PROGRAMME_WORDS.search(link.text())
            and "medlemskab" in path
        ):
            yield target


def read_pages():
    pending = list(ENTRY_URLS)
    pages = {}
    while pending:
        url = pending.pop(0)
        if url in pages:
            continue
        root = page(url)
        check_access(root)
        pages[url] = root
        for target in discovery_links(root, url):
            if target not in pages and target not in pending:
                pending.append(target)
        if len(pages) + len(pending) > 150:
            raise AdapterError(
                "DM discovery exceeds reviewed student scope", "parse"
            )
    return pages


def programme_blocks(root):
    main = one(nodes(root, "main"), "publisher programme content")
    blocks = content_blocks(main)
    start = next((i for i, (tag, _) in enumerate(blocks) if tag == "h1"), None)
    if start is None:
        raise AdapterError("Missing DM programme heading", "parse")
    result = []
    skip = False
    for tag, text in blocks[start:]:
        if tag in ("h1", "h2", "h3"):
            skip = bool(
                re.search(
                    r"^(?:Contact|Kontakt|Meld dig ind|Bliv medlem|Sarah |På feltarbejde|Mød Sanne)",
                    text,
                )
            )
        if not skip and not re.search(r"^Del artikel:|^Membership$", text):
            result.append((tag, text))
    return result


def application_windows(blocks):
    result = []
    clauses = [
        clause
        for _, text in blocks
        for clause in re.split(r"(?<=[.!?])\s+(?=[A-ZÆØÅ])|;", text)
    ]
    for text in clauses:
        closing = re.search(r"application deadline|ansøgningsfrist", text, re.I)
        period = re.search(
            r"application (?:period|round)|ansøgnings(?:runde|periode)",
            text,
            re.I,
        )
        label = closing or period
        if not label:
            continue
        statement = text[label.end() :]
        matches = list(DATE_PATTERN.finditer(statement))
        if not matches:
            continue
        prefix = statement[: matches[0].start()].strip(" :.,")
        allowed = (
            r"(?:(?:is|on|no later than|senest|er|den|by)\s*)*"
            if closing
            else r"(?:(?:is|will|be|open|from|er|til|forårssemestret|"
            r"efterårssemestret|fra|den)\s*)*"
        )
        if not re.fullmatch(allowed, prefix, re.I):
            continue
        if closing:
            dates = dated_values(matches[0][0])
            if not dates:
                continue
            opening, deadline = None, dates[0][2]
        else:
            if len(matches) != 2:
                continue
            separator = statement[matches[0].end() : matches[1].start()]
            if not re.fullmatch(
                r"\s*(?:[-–—]|to|until|til)\s*", separator, re.I
            ):
                continue
            dates = dated_values(
                statement[matches[0].start() : matches[1].end()]
            )
            if len(dates) != 2:
                continue
            opening, deadline = dates[0][2], dates[1][2]
        if opening and opening > deadline:
            # A cross-year range with an unstated opening year is ambiguous.
            opening = None
        item = (opening, deadline)
        if item not in result:
            result.append(item)
    return result


def parse_inventory(pages):
    groups = {}
    for url, root in pages.items():
        path = urllib.parse.urlsplit(url).path
        if not path.startswith(
            ("/students/membership/", "/studerende/medlemskab/")
        ):
            continue
        blocks = programme_blocks(root)
        title = next(text for tag, text in blocks if tag == "h1")
        # Umbrellas discover detail programmes but are not separate awards.
        if path.endswith(
            (
                "/membership/",
                "/medlemskab/",
                "/membership-benefits/",
                "/medlemsfordele/",
                "/legater-og-sponsorater/",
                "/professional-member-benefits/",
                "/faglige-medlemsfordele/",
            )
        ):
            continue
        if not PROGRAMME_WORDS.search(title):
            continue
        identity = LEGACY_ROUTES.get(path, path)
        groups.setdefault(identity, []).append((url, blocks))
    records = []
    for identity, sections in groups.items():
        sections.sort(key=lambda item: ("/students/" not in item[0], item[0]))
        url, blocks = sections[0]
        if identity == "activity-fund":
            for guide_url, guide in pages.items():
                if urllib.parse.urlsplit(guide_url).path == "/om-dm/refusion/":
                    guidance = programme_blocks(guide)
                    selected = []
                    relevant = False
                    for tag, value in guidance:
                        if tag.startswith("h"):
                            relevant = bool(
                                re.search(
                                    r"Afregningsblanket for udlæg|For studerende",
                                    value,
                                )
                            )
                        if relevant:
                            selected.append((tag, value))
                    sections.append((guide_url, selected))
        title = next(text for tag, text in blocks if tag == "h1")
        text = " ".join(
            value for _, content in sections for _, value in content
        )
        category = (
            "scholarships"
            if re.search(r"travel grant|rejselegat", title, re.I)
            else (
                "grants"
                if re.search(r"fund|sponsor|legat|grant", title, re.I)
                else "training"
            )
        )
        windows = sorted(
            dict.fromkeys(
                window
                for _, content in sections
                for window in application_windows(content)
            ),
            key=lambda window: (window[1], window[0] or ""),
        )
        evidence = fact_evidence(sections)
        opening, deadline = windows[0] if len(windows) == 1 else (None, None)
        record = make_record(
            title, url, [category], method="publisher-html", evidence=evidence
        )
        record["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + identity).encode()
        ).hexdigest()[:24]
        record["deadline"] = deadline
        record["application_windows"] = [
            {"opening": start, "deadline": end} for start, end in windows
        ]
        record["language"] = "en" if "/students/" in url else "da"
        record["kind"] = (
            "institutional-grant"
            if category == "grants"
            else (
                "programme-overview"
                if category == "training"
                else "opportunity"
            )
        )
        if category == "training" and all(
            city in text
            for city in ("Aalborg", "Aarhus", "Odense", "Copenhagen")
        ):
            record["host_countries"] = ["DK"]
        if deadline and utc_now()[:10] > deadline:
            record["status"] = "expired"
        elif deadline and opening and opening <= utc_now()[:10] < deadline:
            record["status"] = "open"
        record["summary"] = concise_summary(title, category, deadline, evidence)
        records.append(record)
    validate_records(records)
    return records


def collect_programme():
    return parse_inventory(read_pages())


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
        raise AdapterError("Publication deadline exhausted", "publish")
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
        "display_name": "DM",
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
                f"Collected {len(records)} validated DM student "
                "funding and member training programmes"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def remove_expired_records(records, checked_at):
    """Filter validated deadlines after retrieval, before atomic publication.

    Unknown publisher zones use UTC-12 only as a global certainty bound:
    a date remains eligible until its closing day has ended everywhere.
    No age, status, missing response or unstated local clock implies expiry.
    """
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    latest_day = checked.astimezone(timezone(timedelta(hours=-12))).date()
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
                    cutoff = datetime.fromisoformat(
                        deadline.replace("Z", "+00:00")
                    )
                    expired = cutoff.tzinfo is not None and cutoff < checked
            except ValueError:
                # Unsupported or ambiguous deadlines never authorize removal.
                pass
        if not expired:
            retained.append(record)
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
        records = remove_expired_records(records, utc_now())
        validate_records(records, allow_empty=True)
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

FAMILY = "youthopps-dk-dm-and-ma-travel-grant-publisher-v1"

FAMILY_SOURCES = {SOURCE_ID}

_WORKFLOW_ID = 378390315

_ARTIFACT_NAME = "dk-dm-and-ma-travel-grant-pacing-state"

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
_JOURNAL_EXPOSED = {}

_FIRST_HOSTED_TRANSITION = {
    "schema": 1,
    "family": "youthopps-dk-dm-and-ma-travel-grant-publisher-v1",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/291#issuecomment-6207347601",
    "history_sha256": "19369eeebf6cf3e640a87da91dfb5e07a4a3766fdf421286eaee7efed104c4be",
    "legacy_state_sha256": "5897cc7ad8b787818b5e56530a9c6b6822ae075a1b3bc1214a13aecb73571027",
    "native_state_sha256": "50edb877d1db2b20865dc322221fddcc860251023a0e2e7c0b25f5a497d586a0",
}
_FIRST_HOSTED_TRANSITION_USED = False

_LEGACY_TRANSITION = {
    "schema": 1,
    "family": "youthopps-dk-dm-and-ma-travel-grant-publisher-v1",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/291#issuecomment-6206821242",
    "history_sha256": "19369eeebf6cf3e640a87da91dfb5e07a4a3766fdf421286eaee7efed104c4be",
    "state": {
        "schema": 3,
        "family": "youthopps-dk-dm-and-ma-travel-grant-publisher-v1",
        "observed_at": 1792117670.2300403,
        "not_before": 1792117730.2300403,
        "starts": [
            1791531412.0,
            1791531621.0,
            1791562373.0,
            1791593432.0,
            1791625743.0,
            1791653590.0,
            1791659951.0,
            1791677811.0,
            1791712779.0,
            1791747342.0,
            1791765232.0,
            1791801921.0,
            1791844397.0,
            1791887564.0,
            1791924165.0,
            1791937192.0,
            1791968900.0,
            1791969592.0,
            1791988368.0,
            1792005825.0,
            1792009920.922614,
            1792009926.9227595,
            1792009932.9228954,
            1792009938.92303,
            1792009944.9232416,
            1792009950.9233713,
            1792009956.923512,
            1792009962.9236643,
            1792009968.9237669,
            1792009974.9238877,
            1792012685.0,
            1792022247.0,
            1792047012.0,
            1792052201.0,
            1792061270.0,
            1792061462.0,
            1792079696.0,
            1792097864.0,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792097864.0,
        "page_interval": 30,
        "last_inventory_completed": 1792097864.0,
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
    },
    "legacy_original": {
        "directory": "/tmp/dk-dm-and-ma-travel-grant-pacing-1000",
        "parent_inode": 2437,
        "hosts": [
            {
                "host": "dm.dk",
                "key": "bb4e8315d02323f02ee5e326167b627b26ad51bbb10da11797a5a85bd6a52720",
                "status": "PRESENT_SAFE_READONLY",
                "inode": 2438,
                "raw_sha256": "455bb17310c660bf7519d17ef097ea5f8ed091f4ad21717a30240958713d122e",
                "raw_bytes": 235,
                "state": {
                    "starts": [
                        1792009920.922614,
                        1792009926.9227595,
                        1792009932.9228954,
                        1792009938.92303,
                        1792009944.9232416,
                        1792009950.9233713,
                        1792009956.923512,
                        1792009962.9236643,
                        1792009968.9237669,
                        1792009974.9238877,
                    ],
                    "interval": 6,
                    "until": 0,
                },
            },
            {
                "host": "www.dm.dk",
                "key": "678bfad8f48dd6343647dfd56ba68c5cf90c097376f2cb1d0d3b92f7e665750b",
                "status": "ABSENT_NOT_ZERO",
            },
            {
                "host": "api.github.com",
                "key": "5b3097c5c7bd083b180879a83160df0399af68400f6143c8dea50ec79208be32",
                "status": "ABSENT_NOT_ZERO",
            },
        ],
    },
}

_LEGACY_JOB_PINS = {
    "38805709268": {
        "attempt": 1,
        "conclusion": "success",
        "head": "6c014fae13894cb0c3c7db55a114a82489df9264",
        "jobs": [
            {
                "id": 116665881860,
                "run_id": 38805709268,
                "run_attempt": 1,
                "head_sha": "6c014fae13894cb0c3c7db55a114a82489df9264",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T20:54:28Z",
                "completed_at": "2026-10-15T20:57:44Z",
            }
        ],
    },
    "38763419151": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs": [
            {
                "id": 116520329318,
                "run_id": 38763419151,
                "run_attempt": 1,
                "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T15:47:30Z",
                "completed_at": "2026-10-15T15:54:56Z",
            }
        ],
    },
    "38721260315": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs": [
            {
                "id": 116376109197,
                "run_id": 38721260315,
                "run_attempt": 1,
                "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T10:40:46Z",
                "completed_at": "2026-10-15T10:47:50Z",
            }
        ],
    },
    "38720083821": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs": [
            {
                "id": 116380936832,
                "run_id": 38720083821,
                "run_attempt": 1,
                "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T10:47:08Z",
                "completed_at": "2026-10-15T10:51:02Z",
            }
        ],
    },
    "38704135317": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "id": 116319212575,
                "run_id": 38704135317,
                "run_attempt": 1,
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T08:13:30Z",
                "completed_at": "2026-10-15T08:16:41Z",
            }
        ],
    },
    "38684236132": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "id": 116287777478,
                "run_id": 38684236132,
                "run_attempt": 1,
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-15T06:47:02Z",
                "completed_at": "2026-10-15T06:50:12Z",
            }
        ],
    },
    "38656876110": {
        "attempt": 1,
        "conclusion": "success",
        "head": "e1a68725fe923582b5640481e29c7166eb77397c",
        "jobs": [
            {
                "id": 116168613742,
                "run_id": 38656876110,
                "run_attempt": 1,
                "head_sha": "e1a68725fe923582b5640481e29c7166eb77397c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T23:54:16Z",
                "completed_at": "2026-10-14T23:57:27Z",
            }
        ],
    },
    "38637628346": {
        "attempt": 1,
        "conclusion": "success",
        "head": "b52dc217b33e2b7ac3017d3a3a0a38be2b1add61",
        "jobs": [
            {
                "id": 116108567408,
                "run_id": 38637628346,
                "run_attempt": 1,
                "head_sha": "b52dc217b33e2b7ac3017d3a3a0a38be2b1add61",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T21:14:56Z",
                "completed_at": "2026-10-14T21:18:05Z",
            }
        ],
    },
    "38622841457": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "id": 116054443211,
                "run_id": 38622841457,
                "run_attempt": 1,
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T19:20:31Z",
                "completed_at": "2026-10-14T19:23:45Z",
            }
        ],
    },
    "38580968451": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "id": 115910450767,
                "run_id": 38580968451,
                "run_attempt": 1,
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T14:29:30Z",
                "completed_at": "2026-10-14T14:32:48Z",
            }
        ],
    },
    "38536512233": {
        "attempt": 1,
        "conclusion": "success",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "id": 115775922499,
                "run_id": 38536512233,
                "run_attempt": 1,
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T09:16:36Z",
                "completed_at": "2026-10-14T09:19:52Z",
            }
        ],
    },
    "38536206670": {
        "attempt": 1,
        "conclusion": "success",
        "head": "3f6ab17f4698de58f7365682a22384cd08788f69",
        "jobs": [
            {
                "id": 115771003395,
                "run_id": 38536206670,
                "run_attempt": 1,
                "head_sha": "3f6ab17f4698de58f7365682a22384cd08788f69",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T09:04:23Z",
                "completed_at": "2026-10-14T09:08:20Z",
            }
        ],
    },
    "38489223236": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115610192421,
                "run_id": 38489223236,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-14T00:16:36Z",
                "completed_at": "2026-10-14T00:19:52Z",
            }
        ],
    },
    "38464505823": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115527842711,
                "run_id": 38464505823,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-13T20:39:06Z",
                "completed_at": "2026-10-13T20:42:45Z",
            }
        ],
    },
    "38383704500": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115250149838,
                "run_id": 38383704500,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-13T10:29:30Z",
                "completed_at": "2026-10-13T10:32:44Z",
            }
        ],
    },
    "38316930258": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 115035893655,
                "run_id": 38316930258,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-12T22:30:01Z",
                "completed_at": "2026-10-12T22:33:17Z",
            }
        ],
    },
    "38237962908": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "id": 114773219243,
                "run_id": 38237962908,
                "run_attempt": 1,
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-12T10:42:06Z",
                "completed_at": "2026-10-12T10:45:21Z",
            }
        ],
    },
    "38188270894": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114615244507,
                "run_id": 38188270894,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-12T00:30:36Z",
                "completed_at": "2026-10-12T00:33:52Z",
            }
        ],
    },
    "38168450060": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114555803359,
                "run_id": 38168450060,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-11T19:32:26Z",
                "completed_at": "2026-10-11T19:35:42Z",
            }
        ],
    },
    "38130147977": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114442597411,
                "run_id": 38130147977,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-11T09:56:24Z",
                "completed_at": "2026-10-11T09:59:39Z",
            }
        ],
    },
    "38097700305": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "id": 114346952454,
                "run_id": 38097700305,
                "run_attempt": 1,
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-11T00:13:38Z",
                "completed_at": "2026-10-11T00:16:51Z",
            }
        ],
    },
    "38079105063": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
        "jobs": [
            {
                "id": 114292154060,
                "run_id": 38079105063,
                "run_attempt": 1,
                "head_sha": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T19:15:57Z",
                "completed_at": "2026-10-10T19:19:11Z",
            }
        ],
    },
    "38071840292": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "jobs": [
            {
                "id": 114271063420,
                "run_id": 38071840292,
                "run_attempt": 1,
                "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T17:29:54Z",
                "completed_at": "2026-10-10T17:33:10Z",
            }
        ],
    },
    "38042531623": {
        "attempt": 1,
        "conclusion": "success",
        "head": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "jobs": [
            {
                "id": 114185457541,
                "run_id": 38042531623,
                "run_attempt": 1,
                "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T09:45:48Z",
                "completed_at": "2026-10-10T09:49:03Z",
            }
        ],
    },
    "38010540467": {
        "attempt": 1,
        "conclusion": "success",
        "head": "8cd01afc87aa72916fe454fa2b0ecaaae5309a4b",
        "jobs": [
            {
                "id": 114089259214,
                "run_id": 38010540467,
                "run_attempt": 1,
                "head_sha": "8cd01afc87aa72916fe454fa2b0ecaaae5309a4b",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-10T00:47:19Z",
                "completed_at": "2026-10-10T00:50:32Z",
            }
        ],
    },
    "37957060997": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "jobs": [
            {
                "id": 113910141799,
                "run_id": 37957060997,
                "run_attempt": 1,
                "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-09T16:09:37Z",
                "completed_at": "2026-10-09T16:12:53Z",
            }
        ],
    },
    "37900050572": {
        "attempt": 1,
        "conclusion": "success",
        "head": "56c877668e75eb9e03c3e96229f0db1f3d752784",
        "jobs": [
            {
                "id": 113720233143,
                "run_id": 37900050572,
                "run_attempt": 1,
                "head_sha": "56c877668e75eb9e03c3e96229f0db1f3d752784",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-09T07:37:07Z",
                "completed_at": "2026-10-09T07:40:21Z",
            }
        ],
    },
    "37899706612": {
        "attempt": 1,
        "conclusion": "success",
        "head": "56c877668e75eb9e03c3e96229f0db1f3d752784",
        "jobs": [
            {
                "id": 113719135131,
                "run_id": 37899706612,
                "run_attempt": 1,
                "head_sha": "56c877668e75eb9e03c3e96229f0db1f3d752784",
                "head_branch": "main",
                "status": "completed",
                "started_at": "2026-10-09T07:33:33Z",
                "completed_at": "2026-10-09T07:36:52Z",
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
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
    _ROBOTS.clear()
    _REP_TARGETS.clear()
    _PHASE_SEEN = set()
    _FAILURE_CONTEXT = None
    _INITIAL_SNAPSHOT_ATTEMPTED = False
    _ENTRY_MONOTONIC = time.monotonic()
    _RUN_END = _ENTRY_MONOTONIC + RUN_TIMEOUT
    try:
        yield
    finally:
        _RUN_END = _ENTRY_MONOTONIC = None
        primary_error = sys.exc_info()[0] is not None
        try:
            if _COLLECTION_LOCK_FD is not None:
                descriptor, identity = _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID
                _COLLECTION_LOCK_FD = _COLLECTION_LOCK_ID = None
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != identity:
                    raise AdapterError(
                        "Collection lock FD was reused", "access"
                    )
                os.close(descriptor)
        except BaseException:
            journal_failure()
            if not primary_error:
                raise
        finally:
            try:
                close_journal()
            except BaseException:
                journal_failure()
                if not primary_error:
                    raise
            finally:
                if _PUBLISHER_GATE_FAILED:
                    path = os.environ.get("DM_PACING_ARTIFACT_PATH")
                    if path:
                        with contextlib.suppress(BaseException):
                            with artifact_output(path, cleanup_only=True):
                                pass
                    pacing_not_ready()


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
    global _STATE_PATH, _STATE_LOCK, _STATE_DIR_FD, _STATE_DIR_ID
    global _STATE_INITIALIZING
    if _STATE_PATH:
        verify_state_directory()
        return
    temporary = tempfile.gettempdir()
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        leaf = FAMILY + "-" + str(os.getuid())
        created = False
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            pass
        linked = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        descriptor = os.open(
            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_nlink < 2
            or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            os.close(descriptor)
            raise AdapterError("Unsafe publisher pacing directory", "access")
        _STATE_INITIALIZING = created
        _STATE_DIR_FD = descriptor
        _STATE_DIR_ID = (info.st_dev, info.st_ino)
        directory = os.path.join(temporary, leaf)
        _STATE_PATH = os.path.join(directory, "state.json")
        _STATE_LOCK = os.path.join(directory, "state.lock")
        verify_state_directory()
    finally:
        os.close(parent)


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
        verify_journal_locks()
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
        info = os.fstat(descriptor)
        _JOURNAL_FD = descriptor
        _JOURNAL_ID = (info.st_dev, info.st_ino)
        _JOURNAL_VERSION = journal_version(info)
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


def verify_lock_description(descriptor, identity):
    """Verify one FD while its independently retained flock stays held."""
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AdapterError("Publisher lock FD changed", "access")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def verify_journal_locks():
    """Reject reused visible FDs without releasing the private lock pin."""
    for guard, identity in _JOURNAL_LOCKS.items():
        verify_lock_description(guard, identity)
        verify_lock_description(_JOURNAL_EXPOSED[guard], identity)


@contextlib.contextmanager
def locked_budget():
    """Keep an independent original flock description across every context."""
    global _JOURNAL_LOCK_ID
    budget_file()
    descriptor = guard = None
    identity = None
    try:
        if _JOURNAL_LOCKS:
            verify_journal_locks()
            original = next(iter(_JOURNAL_LOCKS))
            descriptor = os.dup(original)
        else:
            descriptor = secure_file(
                "state.lock",
                os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
            )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat(
            "state.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
        )
        if identity != (current.st_dev, current.st_ino) or (
            _JOURNAL_LOCK_ID is not None and identity != _JOURNAL_LOCK_ID
        ):
            raise AdapterError("Publisher journal lock changed", "access")
        guard = os.dup(descriptor)
        _JOURNAL_LOCK_ID = identity
        _JOURNAL_LOCKS[guard] = identity
        _JOURNAL_EXPOSED[guard] = descriptor
        retain_journal()
        yield descriptor
    except BaseException:
        journal_failure()
        raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        errors, safe = [], []
        # Assess BOTH descriptions before closing either: an independent
        # same-inode replacement cannot acquire the original held flock.
        for candidate in (descriptor, guard):
            if candidate is None:
                continue
            try:
                if identity is None:
                    raise AdapterError("Unproved publisher lock FD", "access")
                verify_lock_description(candidate, identity)
                safe.append(candidate)
            except BaseException as error:
                journal_failure()
                errors.append(error)
        if guard is not None:
            _JOURNAL_LOCKS.pop(guard, None)
            _JOURNAL_EXPOSED.pop(guard, None)
        for candidate in safe:
            try:
                info = os.fstat(candidate)
                if (info.st_dev, info.st_ino) != identity:
                    raise AdapterError("Publisher lock FD was reused", "access")
                os.close(candidate)
            except BaseException as error:
                journal_failure()
                errors.append(error)
        if errors and not primary_error:
            raise errors[0]


def load_budget():
    """Read the retained inode, checking its generation after the read."""
    if not _JOURNAL_LOCKS:
        if _STATE_PATH is None:
            raise AdapterError("Publisher state is not initialized", "access")
        with locked_budget():
            return load_budget()
    try:
        verify_journal()
        raw = os.pread(_JOURNAL_FD, 16385, 0)
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
        verify_journal_locks()
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
    raw = os.pread(_JOURNAL_FD, 16385, 0)
    journal_io_authority()
    after = owned()
    if before != after or len(raw) != after[2] or len(raw) > 16384:
        raise AdapterError("Journal read generation changed", "access")
    return after, raw


def journal_acknowledge(expected):
    """Advance own authority only after exact expected-byte readback."""
    global _JOURNAL_VERSION
    generation, raw = journal_snapshot()
    if raw != expected:
        raise AdapterError("Own journal mutation was superseded", "access")
    _JOURNAL_VERSION = generation


def journal_unchanged():
    """Failure cleanup requires the last ACK, with no dirty exemption."""
    generation, _ = journal_snapshot()
    if generation != _JOURNAL_VERSION:
        raise AdapterError("Original journal generation changed", "access")


def save_budget(state):
    """Commit through the held inode; interrupted rewrites fail closed."""
    global _STATE_INITIALIZING, _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    global _JOURNAL_CREATED, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "DM and MA state persistence is already unhealthy", "access"
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
            raise AdapterError("Unproved DM and MA completion write", "access")
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
    verify_state_directory()
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=_STATE_DIR_FD
    )
    info = os.fstat(descriptor)
    if (
        info.st_uid != os.getuid()
        or not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher state file", "access")
    return descriptor


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
            destination, byte_limit=32768
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
        raise AdapterError("Bounded DM and MA execution required", "access")
    return _PHASE_END if _PHASE_END is not None else nonexport_end()


def check_deadline():
    if _RUN_END is None:
        raise AdapterError("Bounded DM and MA execution required", "access")
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
        "/actions/workflows/fetch-dk-dm-and-ma-travel-grant.yml"
    )
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-dk-dm-and-ma-travel-grant.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "DM workflow is not serialized for this mode",
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
            "fetch-dk-dm-and-ma-travel-grant.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable DM workflow head", "access"
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("DM_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError("Full-native DM MAX approval required", "access")
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
                r"https://github\.com/YouthOpps/data-pipeline/issues/291"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native DM approval", "access"
        ) from None


def restored_state():
    """Newest exact artifact is mandatory after the one reviewed legacy set."""
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("DM family history missing", "access")
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
        raise AdapterError("Incomplete DM artifact inventory", "access")
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError("Newest DM artifact unavailable", "access")
    artifact = matches[0]
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != head
        or not numeric(artifact.get("size_in_bytes"))
        or artifact["size_in_bytes"] > 32768
        or not isinstance(artifact.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"])
    ):
        raise AdapterError("Invalid DM artifact binding", "access")
    return production_maintenance_state(
        download_inert_artifact(latest, artifact["id"], artifact["digest"])
    )


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    global _COLLECTION_LOCK_ID, _FIRST_HOSTED_TRANSITION_USED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    _FIRST_HOSTED_TRANSITION_USED = False
    workflow_mode_guard()
    current_job_context()
    incoming = restored_state()
    # No directory, lock or empty durable budget exists before reviewed state.
    if incoming["in_flight"]:
        raise AdapterError("Owned DM artifact is unsettled", "access")
    budget_file()
    if _FIRST_HOSTED_TRANSITION_USED and not _STATE_INITIALIZING:
        raise AdapterError("First-hosted namespace appeared", "access")
    if _COLLECTION_LOCK_FD is None:
        descriptor = secure_file(
            "collection.lock",
            os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
        )
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError("Collection lock FD was reused", "access")
        except BaseException:
            journal_failure()
            with contextlib.suppress(OSError):
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) == identity:
                    os.close(descriptor)
            raise AdapterError(
                "Another DM collector is active", "access"
            ) from None
        _COLLECTION_LOCK_ID = identity
        _COLLECTION_LOCK_FD = descriptor
    with locked_budget():
        now = time.time()
        local = load_budget()
        if local["in_flight"]:
            raise AdapterError("Local DM flight is unsettled", "access")
        combined = merge_budgets(local, incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        save_budget(combined)
    _COLLECTION_STARTED = time.time()
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
        raise AdapterError("DM collection phase exhausted", "fetch") from None


def export_family_artifact():
    path = os.environ.get("DM_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED:
        with artifact_output(path, cleanup_only=True):
            pass
        pacing_not_ready()
        return
    with artifact_output(path) as descriptor, locked_budget():
        run_id, attempt = current_run_identity()
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError(
                "Unsettled DM flight cannot be exported",
                "access",
            )
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "head_sha": os.environ["GITHUB_SHA"],
            "state": state,
        }
        with os.fdopen(os.dup(descriptor), "w") as file:
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        verify_artifact_target(path, descriptor)
        verify_journal()
        workflow_output_signal(True)
        verify_journal()
        verify_artifact_target(path, descriptor)


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
    """Write readiness only to a safe nonblocking regular workflow file."""
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
            raise AdapterError("Unsafe workflow output", "access")
        output.write(f"pacing_ready={str(ready).lower()}\n")
        output.flush()
        os.fsync(output.fileno())


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
        raise AdapterError("DM current job is not executing", "access")
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
        raise AdapterError("DM current job inventory incomplete", "access")
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
        raise AdapterError("DM current job ownership mismatch", "access")
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
        raise AdapterError("Untrusted DM job clock", "access") from None
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
                raise AdapterError("Publisher rate counter exhausted", "access")
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
        ) == "dm.dk"
    except (TypeError, ValueError):
        return False


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in {"dm.dk", "www.dm.dk"}
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError("Unreviewed DM source route", "access")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def is_inventory(url):
    """Only entry, category/listing and supported pagination requests are pages."""
    parsed = urllib.parse.urlsplit(url)
    if any(key in {"page", "p"} for key in urllib.parse.parse_qs(parsed.query)):
        return True
    path = parsed.path.rstrip("/") + "/"
    return path in {
        "/students/",
        "/studerende/",
        "/studerende/medlemskab/",
        "/studerende/medlemskab/legater-og-sponsorater/",
        "/studerende/medlemskab/medlemsfordele/",
        "/students/membership/membership-benefits/",
        "/students/membership/professional-member-benefits/",
        "/studerende/medlemskab/medlemsfordele/faglige-medlemsfordele/",
    }


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
    with publisher_attempt(inventory) as frame, operation_guard():
        if not robots:
            apply_robots_policy(current, _ROBOTS[origin])
        if not _LOCAL_FIRST_WIRE_VERIFIED:
            if os.environ.get("GITHUB_ACTIONS") != "true":
                workflow_mode_guard()
            _LOCAL_FIRST_WIRE_VERIFIED = True
        try:
            frame["started"] = True
            verify_journal()
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
        with (
            header_rate_limit_response(response)
            if True and response.status == 429
            else response
        ):
            status, response_headers = response.status, response.headers
            record_retry_after(response_headers.get("Retry-After"), status)
            if status == 429:
                raise HeaderRateLimitStop(
                    "Publisher stopped: HTTP 429", "access", 429
                )
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
    return _PUBLISHER_INTERVAL


def parse_robots(body):
    text = body.decode("utf-8", "strict")
    groups, group = [], {"agents": [], "rules": [], "delay": 0, "timing": []}
    for raw in text.splitlines():
        key, separator, value = raw.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["timing"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0, "timing": []}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
        elif key in ("crawl-delay", "request-rate"):
            # Raw values in unrelated groups cannot fail our assessment.
            group["timing"].append((key, value))
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
    return groups


def robots_group_delay(group):
    """Validate only selected timing; retain every equally applicable MAX."""
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    delay = group.get("delay", 0)
    try:
        if not numeric(delay):
            raise ValueError("Invalid retained REP delay")
        for key, value in group.get("timing", []):
            if key == "crawl-delay":
                if not re.fullmatch(decimal, value):
                    raise ValueError("Malformed applicable crawl delay")
                seconds = float(value)
            else:
                match = re.fullmatch(
                    r"([0-9]+)\s*/\s*(" + decimal + r")", value
                )
                if not match:
                    raise ValueError("Malformed applicable request rate")
                requests, period = int(match[1]), float(match[2])
                if requests <= 0 or not numeric(period) or period <= 0:
                    raise ValueError("Invalid applicable rate")
                seconds = period / requests + 0.01
            if not numeric(seconds):
                raise ValueError("Unsupported applicable timing")
            datetime.fromtimestamp(time.time() + seconds, timezone.utc)
            delay = max(delay, seconds)
    except (ValueError, TypeError, OverflowError, OSError):
        raise AdapterError(
            "Unsupported applicable robots timing; assessment incomplete",
            "access",
        ) from None
    return delay


def apply_robots_policy(url, groups):
    global _PUBLISHER_INTERVAL
    if _ACTIVE_FLIGHT_ID is None or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Reserved publisher policy owner required", "access")
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    policies = selected_robots_policies(groups)
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
        [12.2]
        + [robots_group_delay(g) for selected in policies for g in selected]
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
            "/actions/workflows/fetch-dk-dm-and-ma-travel-grant.yml/runs"
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


def verify_legacy_original():
    """Read the exact old files through shared locks without creating them."""
    approval = _LEGACY_TRANSITION
    if not isinstance(approval, dict):
        raise AdapterError(
            "Explicit original journal approval required", "access"
        )
    expected = approval.get("legacy_original")
    if not isinstance(expected, dict):
        raise AdapterError("Original journal pins missing", "access")
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    parent = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    observed = {"directory": directory, "hosts": []}
    constraints = []
    try:
        original = os.fstat(parent)

        def parent_identity():
            current = os.stat(directory, follow_symlinks=False)
            if (
                not stat.S_ISDIR(current.st_mode)
                or current.st_uid != os.getuid()
                or stat.S_IMODE(current.st_mode) != 0o700
                or (current.st_dev, current.st_ino)
                != (original.st_dev, original.st_ino)
            ):
                raise AdapterError(
                    "Original publisher parent changed", "access"
                )

        parent_identity()
        observed["parent_inode"] = original.st_ino
        for host in ("dm.dk", "www.dm.dk", "api.github.com"):
            key = hashlib.sha256(host.encode()).hexdigest()
            row = {"host": host, "key": key}
            try:
                descriptor = os.open(
                    key,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=parent,
                )
            except FileNotFoundError:
                row["status"] = "ABSENT_NOT_ZERO"
                observed["hosts"].append(row)
                continue
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                ):
                    raise AdapterError("Unsafe original host journal", "access")
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                raw = os.read(descriptor, 16385)
                if len(raw) > 16384:
                    raise AdapterError(
                        "Original journal exceeds bound", "access"
                    )
                after = os.fstat(descriptor)
                named = os.stat(key, dir_fd=parent, follow_symlinks=False)

                def stable(value):
                    return (
                        value.st_dev,
                        value.st_ino,
                        value.st_size,
                        value.st_mtime_ns,
                        value.st_ctime_ns,
                    )

                if stable(info) != stable(after) or stable(after) != stable(
                    named
                ):
                    raise AdapterError(
                        "Original host journal changed", "access"
                    )
                parent_identity()
                budget = json.loads(raw)
                if (
                    set(budget) != {"starts", "interval", "until"}
                    or not isinstance(budget["starts"], list)
                    or len(budget["starts"]) > 512
                    or any(not numeric(x) for x in budget["starts"])
                    or not numeric(budget["interval"])
                    or not numeric(budget["until"])
                ):
                    raise AdapterError(
                        "Invalid original host constraints", "access"
                    )
                row.update(
                    status="PRESENT_SAFE_READONLY",
                    inode=info.st_ino,
                    raw_sha256=hashlib.sha256(raw).hexdigest(),
                    raw_bytes=len(raw),
                    state=budget,
                )
                if host != "api.github.com":
                    constraints.append(budget)
            finally:
                os.close(descriptor)
            observed["hosts"].append(row)
        parent_identity()
    finally:
        os.close(parent)
    if observed != expected or not constraints:
        raise AdapterError(
            "Original namespace differs from exact approval", "access"
        )
    return constraints


def first_hosted_transition_state(bounds):
    """Transfer exact approved native MAX after all old job/artifact checks."""
    global _FIRST_HOSTED_TRANSITION_USED
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or not _PUBLISHING
        or not os.environ.get("GITHUB_TOKEN")
        or not os.environ.get("DATA_SOURCE_TOKEN")
        or _RUN_END is None
        or _PHASE_NAME != "restore"
        or "proof" not in _PHASE_SEEN
        or _PUBLICATION_WINDOW is None
        or _PUBLICATION_FAILURE is not None
        or _STATE_PATH is not None
        or _STATE_DIR_FD is not None
        or _COLLECTION_LOCK_FD is not None
        or _JOURNAL_FD is not None
        or _RESTORED
    ):
        raise AdapterError("Exact first-hosted DM context required", "access")
    workflow_mode_guard()
    current_job_context()
    run_id, attempt = current_run_identity()
    identity = (
        str(run_id),
        str(attempt),
        os.environ.get("GITHUB_SHA", ""),
        os.environ.get("GITHUB_WORKFLOW_REF", ""),
    )
    if str(run_id) in _LEGACY_JOB_PINS or _PUBLICATION_IDENTITY != identity:
        raise AdapterError("First-hosted publication owner mismatch", "access")
    original_publication_window()
    declaration = _FIRST_HOSTED_TRANSITION
    if (
        not isinstance(declaration, dict)
        or set(declaration)
        != {
            "schema",
            "family",
            "evidence",
            "history_sha256",
            "legacy_state_sha256",
            "native_state_sha256",
        }
        or type(declaration["schema"]) is not int
        or declaration["schema"] != 1
        or declaration["family"] != FAMILY
        or not isinstance(declaration["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/291"
            r"#issuecomment-[1-9][0-9]*",
            declaration["evidence"],
        )
    ):
        raise AdapterError("Explicit first-hosted approval required", "access")
    seed = validate_budget(_LEGACY_TRANSITION["state"])

    def digest(value):
        """Use the reviewed canonical state encoding."""
        return hashlib.sha256(
            (
                json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode()
        ).hexdigest()

    history = hashlib.sha256(
        json.dumps(
            _LEGACY_JOB_PINS, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if (
        declaration["history_sha256"] != history
        or declaration["history_sha256"] != _LEGACY_TRANSITION["history_sha256"]
        or declaration["legacy_state_sha256"] != digest(seed)
        or not bounds
        or not seed["legacy_rate_history_unknown"]
        or seed["in_flight"]
        or not set(bounds).issubset(seed["starts"])
        or seed["completed"] < max(bounds)
        or seed["last_inventory_completed"] < max(bounds)
        or seed["not_before"] < max(bounds) + 60
    ):
        raise AdapterError("First-hosted legacy provenance changed", "access")
    try:
        envelope = json.loads(os.environ.get("DM_PACING_MAINTENANCE", ""))
    except (ValueError, TypeError):
        raise AdapterError(
            "Exact full-native envelope required", "access"
        ) from None
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"schema", "family", "evidence", "state"}
        or type(envelope["schema"]) is not int
        or envelope["schema"] != 1
        or envelope["family"] != FAMILY
        or envelope["evidence"] != declaration["evidence"]
    ):
        raise AdapterError("First-hosted full-native owner mismatch", "access")
    approved = validate_budget(envelope["state"])
    if (
        digest(approved) != declaration["native_state_sha256"]
        or approved["in_flight"]
        or not approved["legacy_rate_history_unknown"]
    ):
        raise AdapterError("First-hosted full-native state differs", "access")
    originals = _LEGACY_TRANSITION["legacy_original"].get("hosts")
    if (
        not isinstance(originals, list)
        or len(originals) != 3
        or {row.get("host") for row in originals if isinstance(row, dict)}
        != {"dm.dk", "www.dm.dk", "api.github.com"}
    ):
        raise AdapterError("First-hosted original provenance missing", "access")
    constraints = []
    for row in originals:
        if row["host"] == "api.github.com" or "state" not in row:
            continue
        old = row["state"]
        if (
            not isinstance(old, dict)
            or set(old) != {"starts", "interval", "until"}
            or not isinstance(old["starts"], list)
            or len(old["starts"]) > 512
            or any(not numeric(at) for at in old["starts"])
            or not numeric(old["interval"])
            or not numeric(old["until"])
        ):
            raise AdapterError(
                "Invalid retained original constraints", "access"
            )
        constraints.append(old)
    combined = merge_budgets(seed, approved, time.time())
    if not constraints or any(
        not set(old["starts"]).issubset(combined["starts"])
        or combined["interval"] < old["interval"]
        or combined["not_before"] < old["until"]
        for old in constraints
    ):
        raise AdapterError(
            "Original DM constraints were not retained", "access"
        )
    namespace = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    if os.path.lexists(namespace):
        raise AdapterError("First-hosted namespace already exists", "access")
    _FIRST_HOSTED_TRANSITION_USED = True
    return combined


def legacy_transition_state():
    approval = _LEGACY_TRANSITION
    if not isinstance(approval, dict) or set(approval) != {
        "schema",
        "family",
        "evidence",
        "history_sha256",
        "state",
        "legacy_original",
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
        or not isinstance(approval["legacy_original"], dict)
        or not isinstance(approval["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/291"
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
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return first_hosted_transition_state(bounds)
    originals = verify_legacy_original()
    state = validate_budget(approval["state"])
    if (
        not state["legacy_rate_history_unknown"]
        or state["in_flight"]
        or not set(bounds).issubset(state["starts"])
        or state["completed"] < max(bounds)
        or state["last_inventory_completed"] < max(bounds)
        or state["not_before"] < max(bounds) + 60
        or any(
            not set(old["starts"]).issubset(state["starts"])
            or state["interval"] < old["interval"]
            or state["not_before"] < old["until"]
            for old in originals
        )
    ):
        raise AdapterError("Legacy complete conservative MAX missing", "access")
    return state


page = durable_transaction("policy")(page)


class HeaderRateLimitStop(PolicyStop):
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
            journal_failure()
        raise


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
