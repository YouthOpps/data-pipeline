"""Collect Fulbright Poland programme calls and institutional exchanges."""

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
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from zoneinfo import ZoneInfo

SOURCE_ID = "pl-fulbright-poland"
SOURCE_URL = "https://fulbright.edu.pl/junior-research/"
WEBSITE_URL = "https://fulbright.edu.pl/"
LANGUAGE = "pl"
PUBLISHER_COUNTRY = "PL"
PUBLISHER_TYPE = "scholarship-commission"
ATTRIBUTION = "Polish-U.S. Fulbright Commission (CC BY-NC 4.0)"
OUTGOING_URL = WEBSITE_URL + "stypendia-do-usa/"
INCOMING_URL = WEBSITE_URL + "amerykanie-w-polsce/"
LICENSE_URL = "https://creativecommons.org/licenses/by-nc/4.0/"


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


def page(url):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = url in {OUTGOING_URL, INCOMING_URL}
    try:
        check_deadline()
        delay = check_robots(url)
        root = PublisherHTML(fetch_source(url, min_interval=delay)).root
        if "CC BY-NC 4.0" not in root.text():
            raise AdapterError("Publisher content licence changed", "access")
        check_deadline()
        return root
    finally:
        _INVENTORY_ACTIVE = previous


_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}
_LOCAL_PATHS = {
    "graduate-student",
    "notre-dame-llm",
    "junior-research",
    "research-stanford",
    "polish-scholar",
    "polish-studies",
    "stem-impact",
    "sir",
    "schuman-award",
    "biolab",
    "topminds",
    "eta",
    "specialist",
    "inter-country",
}
_EU_COUNTRIES = [
    "AT",
    "BE",
    "BG",
    "HR",
    "CY",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IE",
    "IT",
    "LV",
    "LT",
    "LU",
    "MT",
    "NL",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
]


def content(root):
    return one(nodes(root, class_name="entry-content"), "programme content")


def sections(node):
    """Retain distinct publisher heading sections without menu/sidebar text."""
    result = []
    active = None
    for child in node.children:
        if not isinstance(child, Node):
            continue
        if child.tag in ("h2", "h3"):
            active = [child, []]
            result.append(active)
        elif active:
            active[1].append(child)
    return result


def closing_date(text):
    """Require an explicit closing day, Polish month and year."""
    match = re.search(
        r"\bdo (\d{1,2}) (" + "|".join(_MONTHS) + r") (20\d{2})\b",
        text,
        re.I,
    )
    if not match:
        return None
    day, month, year = match.groups()
    date = datetime(int(year), _MONTHS[month.lower()], int(day))
    clock = re.search(
        r"(?:godziny|godz\.)\s*(\d{1,2}):(\d{2}) czasu polskiego",
        text,
        re.I,
    )
    if clock:
        local = date.replace(
            hour=int(clock.group(1)),
            minute=int(clock.group(2)),
            tzinfo=ZoneInfo("Europe/Warsaw"),
        )
        return (
            local.astimezone(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
    return date.date().isoformat()


def programme_deadline(body):
    # Current opening/closure notices take priority over old FAQ/calendar text.
    paragraphs = [n.text() for n in nodes(body, "p") if n.text()]
    for text in paragraphs[:3]:
        if re.search(r"wniosk|zgłoszen|[Nn]abór", text):
            result = closing_date(text)
            if result:
                return result
    for heading, children in sections(body):
        if not heading.text().startswith("Termin składania wniosków"):
            continue
        for child in children:
            text = child.text()
            if re.search(r"wniosk|[Nn]abór|rekrut", text):
                result = closing_date(text)
                if result:
                    return result
    return None


def status_for(deadline, text):
    if re.search(r"Nabór (?:został zakończony|jest zamknięty)", text):
        return "expired"
    if deadline:
        today = datetime.now(timezone.utc).date().isoformat()
        if len(deadline) == 10:
            # A calendar date is not a fabricated end-of-day timestamp.
            if deadline < today:
                return "expired"
            if deadline == today:
                return "unknown"
        elif deadline <= utc_now():
            return "expired"
        if re.search(r"Nabór jest otwarty|[Nn]abór.*(?:trwa|przyjm)", text):
            return "open"
    return "unknown"


def local_programme(root, path):
    body = content(root)
    title = one(nodes(root, "h1"), "programme title").text()
    text = body.text()
    url = WEBSITE_URL + path + "/"
    institutional = path in ("eta", "specialist", "inter-country", "sir")
    categories = ["grants"] if institutional else ["scholarships"]
    if path == "biolab":
        categories = ["internships"]
    elif path == "topminds":
        categories = ["training"]
    record = make_record(
        title,
        url,
        categories,
        kind="institutional-grant" if institutional else "opportunity",
        method="publisher-programme-content",
        evidence=[url],
    )
    if path == "topminds":
        record["kind"] = "programme-overview"
    record["deadline"] = programme_deadline(body)
    record["status"] = status_for(record["deadline"], text)
    # Only positive requirements populate citizenship, never residency or names.
    if path not in ("biolab", "sir") and re.search(
        r"polskim obywatelstw|polskie obywatelstwo|polski[emgo]+ obywatelstw",
        text,
        re.I,
    ):
        record["eligible_countries"] = ["PL"]
    if path == "sir":
        record["host_countries"] = ["US"]
    elif institutional and re.search(r"polskich|w Polsce|terytorium RP", text):
        record["host_countries"] = ["PL"]
    elif path == "topminds":
        record["language"] = "en"
    elif re.search(r"\bUSA\b|Stanach Zjednoczonych", text):
        record["host_countries"] = ["US"]
    summary = (
        "Official programme; individual degree, affiliation and "
        "application conditions apply."
    )
    if institutional:
        summary = (
            "Institutional application for hosting an academic visitor in "
            "Poland; this is not an individual student application."
        )
    if path == "sir":
        summary = (
            "A US university submits the institutional request, not an "
            "individual. Polish nominees require Polish citizenship; "
            "nominee recruitment occurs only if the administrator requests "
            "candidates. No current individual nomination call is identified."
        )
    elif path == "biolab":
        summary = (
            "One research internship across four US institutions. Polish "
            "citizenship is not required; US citizenship or permanent "
            "residency disqualifies applicants. Active master's/doctoral "
            "status in a Polish institution must be maintained; non-Polish "
            "applicants must also have completed a bachelor's in Poland."
        )
    elif path == "topminds":
        summary = (
            "Mentoring programme for doctoral candidates and final-year "
            "master's students in Polish institutions. The published "
            "closing day has no explicit year (October 29, 16:00 CEST)."
        )
    if record["eligible_countries"] == ["PL"]:
        summary += " Polish citizenship is required."
    if re.search(
        r"(?:brak|nie masz) podwójnego obywatelstwa polsko-amerykańskiego",
        text,
        re.I,
    ):
        summary += (
            " Dual Polish-US citizenship and US permanent residency are "
            "excluded."
        )
    for heading, children in sections(body):
        if not heading.text().startswith("Długość"):
            continue
        duration = " ".join(n.text() for n in children)
        interval = re.search(r"od (\d+) do (\d+) (miesięcy|tygodni)", duration)
        single = re.search(r"okres (\d+) miesięcy", duration)
        if interval:
            unit = "months" if interval.group(3) == "miesięcy" else "weeks"
            summary += (
                f" Published duration: {interval.group(1)}–"
                f"{interval.group(2)} {unit}."
            )
        elif single:
            summary += f" Published duration: {single.group(1)} months."
        elif "Jeden rok akademicki" in duration:
            summary += " Published duration: one academic year."
        elif "pierwszy rok studiów" in duration:
            summary += " Initial funding is for the first study year."
        break
    if record["deadline"]:
        summary += " Published closing date: " + record["deadline"] + "."
    elif path in ("sir", "inter-country"):
        summary += (
            " No complete dated closing instant is published in the "
            "reviewed programme section."
        )
    record["summary"] = summary
    return record


_NATO_COUNTRIES_EXCLUDING_US = [
    "AL",
    "BE",
    "BG",
    "CA",
    "HR",
    "CZ",
    "DK",
    "EE",
    "FI",
    "FR",
    "DE",
    "GR",
    "HU",
    "IS",
    "IT",
    "LV",
    "LT",
    "LU",
    "ME",
    "NL",
    "MK",
    "NO",
    "PL",
    "PT",
    "RO",
    "SK",
    "SI",
    "ES",
    "SE",
    "TR",
    "GB",
]


def schuman_variants(root):
    """Preserve five named variants with their separate qualifications."""
    body = content(root)
    text = body.text()
    if not re.search(r"państw członkowskich NATO", text):
        raise AdapterError("Schuman variant eligibility changed", "parse")
    records = []
    for heading, children in sections(body):
        title = heading.text()
        if heading.tag != "h3" or not title.startswith("Fulbright"):
            continue
        anchor = heading.attrs.get("id")
        if not anchor:
            raise AdapterError("Missing Schuman variant identity", "parse")
        variant = " ".join(n.text() for n in children)
        url = WEBSITE_URL + "schuman-award/#" + anchor
        record = make_record(
            title,
            url,
            ["scholarships"],
            kind="programme-overview",
            method="publisher-named-schuman-variant",
            evidence=[url],
        )
        record["eligible_countries"] = sorted(_EU_COUNTRIES)
        record["host_countries"] = ["US"]
        if "NATO Security" in title:
            record["eligible_countries"] = sorted(_NATO_COUNTRIES_EXCLUDING_US)
            summary = (
                "NATO citizens with a PhD undertake three months of "
                "NATO-related research/teaching at a US academic "
                "institution."
            )
        elif title == "Fulbright Schuman Doctoral Research Award":
            summary = (
                "EU citizens with at least a bachelor's degree undertake "
                "doctoral study/research in the US. Projects must address "
                "EU-US relations or EU policy and at least two EU member "
                "states."
            )
        elif "Post-Doctoral" in title:
            summary = (
                "EU citizens with a PhD undertake US research/teaching "
                "addressing EU-US relations or EU policy and at least two "
                "EU member states."
            )
        elif "International Educator" in title:
            summary = (
                "EU citizens with at least a bachelor's degree and "
                "international-education employment at a European "
                "university conduct US research; a US affiliation letter is "
                "required."
            )
        elif "Innovation" in title:
            record["host_countries"] = []
            summary = (
                "EU citizens, with or without a PhD, research the "
                "policy-technology interface and EU-US relations. This "
                "variant states research in one or two EU member states, "
                "without naming specific host countries."
            )
        else:
            raise AdapterError("Unreviewed Schuman variant", "parse")
        if not variant:
            raise AdapterError("Missing substantive Schuman variant", "parse")
        record["summary"] = summary + (
            " US citizenship or US permanent residency disqualifies "
            "applicants. The annual December 1 deadline has no explicit "
            "year."
        )
        records.append(record)
    if len(records) != 5:
        raise AdapterError("Schuman named variant inventory changed", "parse")
    return records


def incoming_programmes(root):
    body = content(root)
    records = []
    for heading, children in sections(body):
        title = heading.text()
        if not title.startswith("Fulbright"):
            continue
        text = " ".join(n.text() for n in children)
        links = [a for child in children for a in nodes(child, "a")]
        anchor = heading.attrs.get("id")
        if not anchor:
            raise AdapterError("Missing incoming programme anchor", "parse")
        url = INCOMING_URL + "#" + anchor
        record = make_record(
            title,
            url,
            ["scholarships"],
            kind="programme-overview",
            method="publisher-incoming-programme",
            evidence=[url],
        )
        if re.search(
            r"obywatel[ie].*Stanów Zjednoczonych|obywatelstwem amerykańskim",
            text,
        ):
            record["eligible_countries"] = ["US"]
        if re.search(r"w Polsce|do Polski", text):
            record["host_countries"] = ["PL"]
        record["summary"] = (
            "Published individual exchange programme. Qualifications and "
            "institutional invitation requirements depend on the programme; "
            "no dated deadline is stated here."
        )
        if "U.S. Scholar" in title:
            record["summary"] = (
                "US citizens with a PhD or terminal degree undertake 3–10 "
                "months of teaching/research in Poland. The annual "
                "February–September calendar has no exact current year."
            )
        elif "U.S. Student" in title:
            record["summary"] = (
                "US citizens with a bachelor's degree before the grant, "
                "but before a PhD, undertake 9–10 months of research in "
                "Poland. A Polish host-institution invitation letter is "
                "required. No dated application round is stated here."
            )
        elif "English Teaching Assistant" in title:
            record["summary"] = (
                "US citizens with a bachelor's degree, before a PhD, teach "
                "English in Poland for nine months. No invitation letter "
                "is required; the Commission allocates host universities. "
                "The institutional host application is a separate call."
            )
        if "Specialist" in title:
            record["summary"] = (
                "American specialist visits to invited Polish institutions for "
                "14–42 days. Host-institution application is a separate call. "
                "No dated individual deadline is stated here."
            )
        if "Schuman" in title:
            record["summary"] = (
                "US-citizen research in the European Union; the reverse "
                "EU-citizen US research call has its own programme page. No "
                "complete dated deadline or specific host-country list is "
                "stated here."
            )
        records.append(record)
        if "U.S. Scholar" in title:
            for link in links:
                target = link.attrs.get("href", "")
                if (
                    not target.startswith(
                        "https://fulbrightscholars.org/award/"
                    )
                    or not link.text()
                ):
                    continue
                partner = make_record(
                    link.text().removesuffix(" NEW"),
                    target,
                    ["scholarships"],
                    kind="programme-overview",
                    host_countries=["PL"],
                    method="publisher-named-partner-programme",
                    evidence=[url, target],
                )
                partner["eligible_countries"] = record["eligible_countries"]
                partner["summary"] = (
                    "Named Polish partner award in the US Scholar programme; "
                    "US "
                    "citizenship and a PhD/terminal degree apply; 3–10 months "
                    "of teaching/research. No dated "
                    "deadline is stated by this publisher section."
                )
                records.append(partner)
    if len(records) < 5:
        raise AdapterError("Incoming programme inventory changed", "parse")
    return records




def collect_body():
    with collection_lock():
        outgoing = page(OUTGOING_URL)
        incoming = page(INCOMING_URL)
        # Discover the publisher's actual programme navigation/footer URLs,
        # not quantities of award places or unrelated news/application systems.
        paths = set()
        for root in (outgoing, incoming):
            for link in nodes(root, "a"):
                url = urllib.parse.urljoin(
                    WEBSITE_URL, link.attrs.get("href", "")
                )
                parsed = urllib.parse.urlsplit(url)
                path = parsed.path.strip("/")
                if (
                    parsed.hostname in ("topminds.pl", "www.topminds.pl")
                    and link.text().casefold() == "program topminds"
                ):
                    # The navigation links the official programme site; its
                    # substantive Commission explanation has this own route.
                    paths.add("topminds")
                if (
                    parsed.hostname == "fulbright.edu.pl"
                    and path in _LOCAL_PATHS
                ):
                    paths.add(path)
        if paths != _LOCAL_PATHS:
            raise AdapterError(
                "Programme navigation inventory changed", "parse"
            )
        records = []
        for path in sorted(paths):
            root = page(WEBSITE_URL + path + "/")
            if path == "schuman-award":
                records.extend(schuman_variants(root))
            else:
                records.append(local_programme(root, path))
        records.extend(incoming_programmes(incoming))
        validate_records(records)
        print(
            f"Fulbright Poland: {len(paths)} own programme pages, "
            f"{len(records)} total programme/call records",
            file=sys.stderr,
        )
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
    publisher = SOURCE_ID if host == "fulbright.edu.pl" else host
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
    retry_jitter=0
):
    if retry_jitter:
        state, budget = legacy_pace(url, interval, start_padding=retry_jitter)
    else:
        state, budget = legacy_pace(url, interval)
    try:
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


def fetch_source(url, min_interval=6, *, policy_response=False):
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
                status, headers, body = request_bytes(
                    current, interval=min_interval
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    destination = urllib.parse.urljoin(
                        current, headers["Location"]
                    )
                    checked(destination)
                    target = urllib.parse.urlsplit(destination)
                    if original.path == "/robots.txt" and (
                        target.path != "/robots.txt"
                        or target.query
                        or target.fragment
                    ):
                        raise AdapterError(
                            "Robots redirect leaves literal policy route",
                            "access",
                        )
                    current = destination
                    min_interval = max(min_interval, check_robots(current))
                    continue
                if policy_response:
                    return status, headers, body
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


def remove_expired_records(records, now=None):
    """Filter a validated refresh; never mutate the prior published snapshot."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()
    retained = []
    for record in records:
        value = record.get("deadline")
        expired = False
        if isinstance(value, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    expired = datetime.fromisoformat(value).date() < today
                else:
                    closing = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = closing.tzinfo is not None and closing < now
            except ValueError:
                # Ambiguous deadlines never establish expiry.
                pass
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


_PUBLICATION_WINDOW = None
_PUBLICATION_IDENTITY = None
_PUBLICATION_FAILURE = None
_PUBLICATION_RETRY_JITTER = 0


@contextlib.contextmanager
def publication_http_guard(deadline):
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
        "display_name": "Fulbright Poland",
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
    result["license"] = "CC BY-NC 4.0"
    result["license_url"] = LICENSE_URL
    result["changes"] = (
        "Factual selection and normalization; YouthOpps-written summaries."
    )
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def run_body(publishing):
    snapshot = read_snapshot() if publishing else None
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
        records = remove_expired_records(records)
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
    enter_phase(
        "failure" if failure else "publication",
        _FAILURE_SECONDS if failure else _PUBLICATION_SECONDS,
    )
    outcome = metadata(attempt, previous, records, failure)
    if publishing and (not failure or previous):
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
            enter_phase("failure", _FAILURE_SECONDS)
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
    elif publishing:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0




# Source-local state is separate from the preserved default GitHub namespace.
FAMILY = "youthopps-pl-fulbright-poland-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_IDS = {SOURCE_ID: 378997972}
COLLECTION_TIMEOUT = 1200
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 60
_PUBLICATION_SECONDS = 180
_FAILURE_SECONDS = 180
_PUBLISHER_TIMEOUT = _DEFAULT_HTTP_TIMEOUT = 30
_ARTIFACT_NAME = "pl-fulbright-poland-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = _COLLECTION_LOCK_CONTEXT = None
_COLLECTION_STARTED = None
_LEGACY_COLLECTION_OBSERVATION = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_RUN_END = _PHASE_END = _SOURCE_END = None
_PHASE_NAME = None
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
_PUBLISHER_HOSTS = {"fulbright.edu.pl", "www.fulbright.edu.pl"}
_LEGACY_NO_JOB = {}
_LOCAL_RESEARCH_MIGRATION = {'schema': 1,
 'source': 'pl-fulbright-poland',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/293#issuecomment-6207427595',
 'state': {'schema': 3,
           'family': 'youthopps-pl-fulbright-poland-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [{'run_id': 38763419096,
                                           'run_attempt': 1,
                                           'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                           'source': 'pl-fulbright-poland',
                                           'stage': 'publish',
                                           'access_scope': 'antecedent_unlogged_unknown_not_a_proven_content_hold',
                                           'log_sha256': '6516eabc845db06bf538d3f279a0b11002007f9d7c6f01b049523920d90c3571'}],
           'research_provenance': {'status': 'PROPOSAL_ONLY_no_current_namespace_observation_or_access',
                                   'complete_legacy_attempts': 28,
                                   'completion_upper_bounds': [{'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38825421994,
                                                                'run_attempt': 1,
                                                                'time': 1792108055.0,
                                                                'jobs_sha256': '4740a6000a6c788d5771d530a4c599fde53284c958ad7a3b3151023bb3c29894'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38763419096,
                                                                'run_attempt': 1,
                                                                'time': 1792079648.0,
                                                                'jobs_sha256': '9cacdf54fd7f99c87fb5746629a74c1a0b79f390c044124a8d6c60aa70f2057a'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38721259467,
                                                                'run_attempt': 1,
                                                                'time': 1792060927.0,
                                                                'jobs_sha256': '1196264ed6167144329a16df248a85b86aa03728239f8370d284e9f76f9c4692'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38720083607,
                                                                'run_attempt': 1,
                                                                'time': 1792060134.0,
                                                                'jobs_sha256': 'af73f6b5f23b6b7779bd6573ef9dfa2678c4f3cc55ad7f087cd1568a93f35b0c'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38685495484,
                                                                'run_attempt': 1,
                                                                'time': 1792049481.0,
                                                                'jobs_sha256': 'b94db75da7ca52945a8d02f07dfacd94ada63ef6e31ecbcfc693a97d61df01cb'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38684236155,
                                                                'run_attempt': 1,
                                                                'time': 1792047508.0,
                                                                'jobs_sha256': 'bcdc8a2dff8e05247c9bc49aa54a9bf74493250231be48cac5f14dd71afbf0d7'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38644094315,
                                                                'run_attempt': 1,
                                                                'time': 1792015447.0,
                                                                'jobs_sha256': '3a289b818e4f3bf315b5385b51e30af84b440e14e9c1f4a59824a042078a55d9'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38608073731,
                                                                'run_attempt': 1,
                                                                'time': 1791999692.0,
                                                                'jobs_sha256': 'c72703089114aa88e17e9a30c9617be3e9c336ca22214fff978bb4e974ceb14f'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38563380367,
                                                                'run_attempt': 1,
                                                                'time': 1791980780.0,
                                                                'jobs_sha256': 'a0a05c66cb1a2726200a2c744ecfa973953eeb898b300c1ca39cd9d04d853b16'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38534815209,
                                                                'run_attempt': 1,
                                                                'time': 1791968248.0,
                                                                'jobs_sha256': '4e0562e71587d398971ff009743bf913c454d6290b1958e91b84e856fccca265'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38516460936,
                                                                'run_attempt': 1,
                                                                'time': 1791955608.0,
                                                                'jobs_sha256': '362297aafe9b4bd60caf347a138cfbe126bb9e4daf6ac02180ef9277e42dcf10'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38482292637,
                                                                'run_attempt': 1,
                                                                'time': 1791933167.0,
                                                                'jobs_sha256': 'bd45d5fc1c1e124b571ff5a3c17e6220ed4a260feaeeff36ff6333807aa4cad7'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38414598116,
                                                                'run_attempt': 1,
                                                                'time': 1791902359.0,
                                                                'jobs_sha256': 'f5b9321cf6c1db4d6cbbd05bc1b042436cb76ab55cdf7cb48ab6308edc6779ce'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38329858046,
                                                                'run_attempt': 1,
                                                                'time': 1791853449.0,
                                                                'jobs_sha256': '5cab1031afa02212961c8b9e2e228f5eab56bfa066ee9d90e64cb6974dafe279'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38275155904,
                                                                'run_attempt': 1,
                                                                'time': 1791821375.0,
                                                                'jobs_sha256': 'b1aee9249dbec5988f1947b4e07640ba8380874a32755ac949960fd94b4a9bb7'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38212988856,
                                                                'run_attempt': 1,
                                                                'time': 1791786916.0,
                                                                'jobs_sha256': 'e853752a54a8e104d259bb64da9368545a5510253f20eb76ab81d2e2ce83aa2d'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38180538545,
                                                                'run_attempt': 1,
                                                                'time': 1791757949.0,
                                                                'jobs_sha256': '75c9c36c3f747c6b200ccd4f86bc1739cac60b90e52195fe890d780f04da54d3'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38143532468,
                                                                'run_attempt': 1,
                                                                'time': 1791725687.0,
                                                                'jobs_sha256': '8e67abe29d0fdbc85e53fe5ccd3c8660271387f016ce856927f3ccdac61ccf7d'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38118099239,
                                                                'run_attempt': 1,
                                                                'time': 1791700225.0,
                                                                'jobs_sha256': '567db425a80c7ba59207780a566b5532e70517b75abff3003c1ec5d16256795b'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38089704891,
                                                                'run_attempt': 1,
                                                                'time': 1791669969.0,
                                                                'jobs_sha256': '8efaa6b04283d37e1725ddda9092de3006919f27c9be290f3ab527a3e8aa4e0e'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38071840307,
                                                                'run_attempt': 1,
                                                                'time': 1791655668.0,
                                                                'jobs_sha256': 'fca0859b1567662298ded1fa7381ed20bc73090e59b830b1e24f892b4a3acb90'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38054248675,
                                                                'run_attempt': 1,
                                                                'time': 1791637551.0,
                                                                'jobs_sha256': '565341e8a6ff61d05a2c42899050f5a5f0de936906757b6ef2dbb488c7c7b5f7'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38030743591,
                                                                'run_attempt': 1,
                                                                'time': 1791613561.0,
                                                                'jobs_sha256': '707f5964ccfe740982611831913aa565522f2448f44450e946d3eb86dd9239a7'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 38001660202,
                                                                'run_attempt': 1,
                                                                'time': 1791586584.0,
                                                                'jobs_sha256': '81723cd8be6a2f3ba4076799619e46891be25f5875f76b210cc0cac87a684e12'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 37973829042,
                                                                'run_attempt': 1,
                                                                'time': 1791570813.0,
                                                                'jobs_sha256': '34e17efff1f9b4aaa5449accb82b3cf225647a738fc0d282a236406dcc3322a2'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 37894981123,
                                                                'run_attempt': 1,
                                                                'time': 1791528322.0,
                                                                'jobs_sha256': '321f93e4f2d8211fceb9cefbeedfc3ac977fa911ae79bfbb46cd69bde5b0d04f'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 37846330041,
                                                                'run_attempt': 1,
                                                                'time': 1791494838.0,
                                                                'jobs_sha256': 'af308dab17be1e5dda3dc8e37253ac8fa4b6bf06d13cb3df71195d7afb650cc2'},
                                                               {'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_id': 37846319809,
                                                                'run_attempt': 1,
                                                                'time': 1791494697.0,
                                                                'jobs_sha256': 'a77107dba1fe82f6f73d174ee3dd59110971f1f01ec725937d3ea32286d1aff4'}],
                                   'saved_history_sha256': '932ff7255910e3f7aaf2a2015e373b4a406c08c5c8ab52082a25255951de68dd',
                                   'known_publish_failure_not_proven_source_denial': [{'run_id': 38763419096,
                                                                                       'run_attempt': 1,
                                                                                       'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                                                       'source': 'pl-fulbright-poland',
                                                                                       'stage': 'publish',
                                                                                       'access_scope': 'antecedent_unlogged_unknown_not_a_proven_content_hold',
                                                                                       'log_sha256': '6516eabc845db06bf538d3f279a0b11002007f9d7c6f01b049523920d90c3571'}],
                                   'missing_original_local_state': 'Saved '
                                                                   'earlier '
                                                                   'absence '
                                                                   'is not '
                                                                   'fresh '
                                                                   'absence, '
                                                                   'zero '
                                                                   'history, '
                                                                   'or '
                                                                   'permission; '
                                                                   'ROOT must '
                                                                   'recover '
                                                                   'any '
                                                                   'current '
                                                                   'raw '
                                                                   'constraints '
                                                                   'and '
                                                                   'replace '
                                                                   'this '
                                                                   'proposal '
                                                                   'if '
                                                                   'present.',
                                   'initial_in_flight_false': 'Conditional '
                                                              'proposed '
                                                              'first-generation '
                                                              'value only '
                                                              'after exact '
                                                              'no-owned chain '
                                                              '+ actual '
                                                              'current ABSENT '
                                                              'evidence + '
                                                              'separate '
                                                              'public '
                                                              'approval; not '
                                                              'generic '
                                                              'oldschema '
                                                              'conversion.'},
           'starts': [1791494697.0,
                      1791494838.0,
                      1791528322.0,
                      1791570813.0,
                      1791586584.0,
                      1791613561.0,
                      1791637551.0,
                      1791655668.0,
                      1791669969.0,
                      1791700225.0,
                      1791725687.0,
                      1791757949.0,
                      1791786916.0,
                      1791821375.0,
                      1791853449.0,
                      1791902359.0,
                      1791933167.0,
                      1791955608.0,
                      1791968248.0,
                      1791980780.0,
                      1791999692.0,
                      1792015447.0,
                      1792047508.0,
                      1792049481.0,
                      1792060134.0,
                      1792060927.0,
                      1792079648.0,
                      1792108055.0],
           'interval': 12.2,
           'until': 1792121190.924445,
           'page_gap': 30,
           'completed': 1792108055.0,
           'inventory_completed': 0,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792121130.924445},
 'state_sha256': '4608d3e2044c7146294c728066e5cffc843a3d0ca56d206b442c7591d3898871',
 'legacy_state_sha256': '9ad5a5b5a691bc134dd01414d03b48543c007b04f7050ceab72940c3c95c27ba'}
_REVIEWED_NATIVE_MIGRATION = {'schema': 1,
 'source': 'pl-fulbright-poland',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/293#issuecomment-6207729527',
 'state': {'schema': 3,
           'family': 'youthopps-pl-fulbright-poland-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [{'run_id': 38763419096,
                                           'run_attempt': 1,
                                           'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                           'source': 'pl-fulbright-poland',
                                           'stage': 'publish',
                                           'access_scope': 'antecedent_unlogged_unknown_not_a_proven_content_hold',
                                           'log_sha256': '6516eabc845db06bf538d3f279a0b11002007f9d7c6f01b049523920d90c3571'}],
           'research_provenance': {'complete_legacy_attempts': 28,
                                   'completion_upper_bounds': [{'jobs_sha256': '4740a6000a6c788d5771d530a4c599fde53284c958ad7a3b3151023bb3c29894',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38825421994,
                                                                'time': 1792108055.0},
                                                               {'jobs_sha256': '9cacdf54fd7f99c87fb5746629a74c1a0b79f390c044124a8d6c60aa70f2057a',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38763419096,
                                                                'time': 1792079648.0},
                                                               {'jobs_sha256': '1196264ed6167144329a16df248a85b86aa03728239f8370d284e9f76f9c4692',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38721259467,
                                                                'time': 1792060927.0},
                                                               {'jobs_sha256': 'af73f6b5f23b6b7779bd6573ef9dfa2678c4f3cc55ad7f087cd1568a93f35b0c',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38720083607,
                                                                'time': 1792060134.0},
                                                               {'jobs_sha256': 'b94db75da7ca52945a8d02f07dfacd94ada63ef6e31ecbcfc693a97d61df01cb',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38685495484,
                                                                'time': 1792049481.0},
                                                               {'jobs_sha256': 'bcdc8a2dff8e05247c9bc49aa54a9bf74493250231be48cac5f14dd71afbf0d7',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38684236155,
                                                                'time': 1792047508.0},
                                                               {'jobs_sha256': '3a289b818e4f3bf315b5385b51e30af84b440e14e9c1f4a59824a042078a55d9',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38644094315,
                                                                'time': 1792015447.0},
                                                               {'jobs_sha256': 'c72703089114aa88e17e9a30c9617be3e9c336ca22214fff978bb4e974ceb14f',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38608073731,
                                                                'time': 1791999692.0},
                                                               {'jobs_sha256': 'a0a05c66cb1a2726200a2c744ecfa973953eeb898b300c1ca39cd9d04d853b16',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38563380367,
                                                                'time': 1791980780.0},
                                                               {'jobs_sha256': '4e0562e71587d398971ff009743bf913c454d6290b1958e91b84e856fccca265',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38534815209,
                                                                'time': 1791968248.0},
                                                               {'jobs_sha256': '362297aafe9b4bd60caf347a138cfbe126bb9e4daf6ac02180ef9277e42dcf10',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38516460936,
                                                                'time': 1791955608.0},
                                                               {'jobs_sha256': 'bd45d5fc1c1e124b571ff5a3c17e6220ed4a260feaeeff36ff6333807aa4cad7',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38482292637,
                                                                'time': 1791933167.0},
                                                               {'jobs_sha256': 'f5b9321cf6c1db4d6cbbd05bc1b042436cb76ab55cdf7cb48ab6308edc6779ce',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38414598116,
                                                                'time': 1791902359.0},
                                                               {'jobs_sha256': '5cab1031afa02212961c8b9e2e228f5eab56bfa066ee9d90e64cb6974dafe279',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38329858046,
                                                                'time': 1791853449.0},
                                                               {'jobs_sha256': 'b1aee9249dbec5988f1947b4e07640ba8380874a32755ac949960fd94b4a9bb7',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38275155904,
                                                                'time': 1791821375.0},
                                                               {'jobs_sha256': 'e853752a54a8e104d259bb64da9368545a5510253f20eb76ab81d2e2ce83aa2d',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38212988856,
                                                                'time': 1791786916.0},
                                                               {'jobs_sha256': '75c9c36c3f747c6b200ccd4f86bc1739cac60b90e52195fe890d780f04da54d3',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38180538545,
                                                                'time': 1791757949.0},
                                                               {'jobs_sha256': '8e67abe29d0fdbc85e53fe5ccd3c8660271387f016ce856927f3ccdac61ccf7d',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38143532468,
                                                                'time': 1791725687.0},
                                                               {'jobs_sha256': '567db425a80c7ba59207780a566b5532e70517b75abff3003c1ec5d16256795b',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38118099239,
                                                                'time': 1791700225.0},
                                                               {'jobs_sha256': '8efaa6b04283d37e1725ddda9092de3006919f27c9be290f3ab527a3e8aa4e0e',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38089704891,
                                                                'time': 1791669969.0},
                                                               {'jobs_sha256': 'fca0859b1567662298ded1fa7381ed20bc73090e59b830b1e24f892b4a3acb90',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38071840307,
                                                                'time': 1791655668.0},
                                                               {'jobs_sha256': '565341e8a6ff61d05a2c42899050f5a5f0de936906757b6ef2dbb488c7c7b5f7',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38054248675,
                                                                'time': 1791637551.0},
                                                               {'jobs_sha256': '707f5964ccfe740982611831913aa565522f2448f44450e946d3eb86dd9239a7',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38030743591,
                                                                'time': 1791613561.0},
                                                               {'jobs_sha256': '81723cd8be6a2f3ba4076799619e46891be25f5875f76b210cc0cac87a684e12',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 38001660202,
                                                                'time': 1791586584.0},
                                                               {'jobs_sha256': '34e17efff1f9b4aaa5449accb82b3cf225647a738fc0d282a236406dcc3322a2',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 37973829042,
                                                                'time': 1791570813.0},
                                                               {'jobs_sha256': '321f93e4f2d8211fceb9cefbeedfc3ac977fa911ae79bfbb46cd69bde5b0d04f',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 37894981123,
                                                                'time': 1791528322.0},
                                                               {'jobs_sha256': 'af308dab17be1e5dda3dc8e37253ac8fa4b6bf06d13cb3df71195d7afb650cc2',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 37846330041,
                                                                'time': 1791494838.0},
                                                               {'jobs_sha256': 'a77107dba1fe82f6f73d174ee3dd59110971f1f01ec725937d3ea32286d1aff4',
                                                                'kind': 'legacy_job_completion_UPPER_BOUND_not_wire',
                                                                'run_attempt': 1,
                                                                'run_id': 37846319809,
                                                                'time': 1791494697.0}],
                                   'initial_in_flight_false': 'Conditional '
                                                              'proposed '
                                                              'first-generation '
                                                              'value only '
                                                              'after exact '
                                                              'no-owned chain '
                                                              '+ actual '
                                                              'current ABSENT '
                                                              'evidence + '
                                                              'separate '
                                                              'public '
                                                              'approval; not '
                                                              'generic '
                                                              'oldschema '
                                                              'conversion.',
                                   'known_publish_failure_not_proven_source_denial': [{'access_scope': 'antecedent_unlogged_unknown_not_a_proven_content_hold',
                                                                                       'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                                                       'log_sha256': '6516eabc845db06bf538d3f279a0b11002007f9d7c6f01b049523920d90c3571',
                                                                                       'run_attempt': 1,
                                                                                       'run_id': 38763419096,
                                                                                       'source': 'pl-fulbright-poland',
                                                                                       'stage': 'publish'}],
                                   'missing_original_local_state': 'Saved '
                                                                   'earlier '
                                                                   'absence '
                                                                   'is not '
                                                                   'fresh '
                                                                   'absence, '
                                                                   'zero '
                                                                   'history, '
                                                                   'or '
                                                                   'permission; '
                                                                   'ROOT must '
                                                                   'recover '
                                                                   'any '
                                                                   'current '
                                                                   'raw '
                                                                   'constraints '
                                                                   'and '
                                                                   'replace '
                                                                   'this '
                                                                   'proposal '
                                                                   'if '
                                                                   'present.',
                                   'saved_history_sha256': '932ff7255910e3f7aaf2a2015e373b4a406c08c5c8ab52082a25255951de68dd',
                                   'status': 'PROPOSAL_ONLY_no_current_namespace_observation_or_access'},
           'starts': [1791494697.0,
                      1791494838.0,
                      1791528322.0,
                      1791570813.0,
                      1791586584.0,
                      1791613561.0,
                      1791637551.0,
                      1791655668.0,
                      1791669969.0,
                      1791700225.0,
                      1791725687.0,
                      1791757949.0,
                      1791786916.0,
                      1791821375.0,
                      1791853449.0,
                      1791902359.0,
                      1791933167.0,
                      1791955608.0,
                      1791968248.0,
                      1791980780.0,
                      1791999692.0,
                      1792015447.0,
                      1792047508.0,
                      1792049481.0,
                      1792060134.0,
                      1792060927.0,
                      1792079648.0,
                      1792108055.0,
                      1792121663.9034252,
                      1792121677.059594,
                      1792121677.09071,
                      1792121677.6818442,
                      1792121689.882332,
                      1792121689.885375,
                      1792121689.941511,
                      1792121690.9644248,
                      1792121720.9719543,
                      1792121720.974983,
                      1792121721.0340803,
                      1792121721.7338529,
                      1792121733.9382553,
                      1792121733.9424603,
                      1792121733.999512,
                      1792121734.8259625,
                      1792121747.030604,
                      1792121747.035838,
                      1792121747.0737696,
                      1792121748.1034763,
                      1792121760.3101466,
                      1792121760.3149197,
                      1792121760.3713565,
                      1792121761.2142103,
                      1792121773.4344034,
                      1792121773.4382994,
                      1792121773.451705,
                      1792121773.9917383,
                      1792121786.1946893,
                      1792121786.1979082,
                      1792121786.2142985,
                      1792121787.170272,
                      1792121799.3765712,
                      1792121799.3793104,
                      1792121799.3957698,
                      1792121800.011462,
                      1792121812.2274034,
                      1792121812.2638319,
                      1792121812.3018022,
                      1792121813.2880583,
                      1792121825.4985867,
                      1792121825.5023289,
                      1792121825.5150998,
                      1792121826.2697592,
                      1792121838.474278,
                      1792121838.4793723,
                      1792121838.5132432,
                      1792121839.2041829,
                      1792121851.4095483,
                      1792121851.4136093,
                      1792121851.4693847,
                      1792121852.8179045,
                      1792121865.0207522,
                      1792121865.0248156,
                      1792121865.0765276,
                      1792121865.9510608,
                      1792121878.1654472,
                      1792121878.1688468,
                      1792121878.1998177,
                      1792121878.9158063,
                      1792121891.1203043,
                      1792121891.1234546,
                      1792121891.32141,
                      1792121892.1216416,
                      1792121904.3368316,
                      1792121904.343638,
                      1792121904.5375807,
                      1792121904.9411154,
                      1792121917.1414466,
                      1792121917.1533437,
                      1792121917.2122831,
                      1792121918.0031056],
           'interval': 12.2,
           'until': 1792121979.153753,
           'page_gap': 30,
           'completed': 1792121918.0212018,
           'inventory_completed': 1792121721.7380989,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792121919.153753},
 'state_sha256': 'd9b39b257ff5de9338eeb410c64b3a4ef07911aa00b74b0241bc060d59d56d93',
 'legacy_state_sha256': '9ad5a5b5a691bc134dd01414d03b48543c007b04f7050ceab72940c3c95c27ba'}
_LEGACY_ATTEMPTS = {
    38825421994: {
        "source": "pl-fulbright-poland",
        "head_sha": "77b5911162e0ab635225298393523b409638e6b1",
        "workflow_id": 378997972,
        "jobs_sha256": "4740a6000a6c788d5771d530a4c599fde53284c958ad7a3b3151023bb3c29894",
    },
    38763419096: {
        "source": "pl-fulbright-poland",
        "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "workflow_id": 378997972,
        "jobs_sha256": "9cacdf54fd7f99c87fb5746629a74c1a0b79f390c044124a8d6c60aa70f2057a",
    },
    38721259467: {
        "source": "pl-fulbright-poland",
        "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "workflow_id": 378997972,
        "jobs_sha256": "1196264ed6167144329a16df248a85b86aa03728239f8370d284e9f76f9c4692",
    },
    38720083607: {
        "source": "pl-fulbright-poland",
        "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "workflow_id": 378997972,
        "jobs_sha256": "af73f6b5f23b6b7779bd6573ef9dfa2678c4f3cc55ad7f087cd1568a93f35b0c",
    },
    38685495484: {
        "source": "pl-fulbright-poland",
        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "workflow_id": 378997972,
        "jobs_sha256": "b94db75da7ca52945a8d02f07dfacd94ada63ef6e31ecbcfc693a97d61df01cb",
    },
    38684236155: {
        "source": "pl-fulbright-poland",
        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "workflow_id": 378997972,
        "jobs_sha256": "bcdc8a2dff8e05247c9bc49aa54a9bf74493250231be48cac5f14dd71afbf0d7",
    },
    38644094315: {
        "source": "pl-fulbright-poland",
        "head_sha": "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
        "workflow_id": 378997972,
        "jobs_sha256": "3a289b818e4f3bf315b5385b51e30af84b440e14e9c1f4a59824a042078a55d9",
    },
    38608073731: {
        "source": "pl-fulbright-poland",
        "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "workflow_id": 378997972,
        "jobs_sha256": "c72703089114aa88e17e9a30c9617be3e9c336ca22214fff978bb4e974ceb14f",
    },
    38563380367: {
        "source": "pl-fulbright-poland",
        "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "workflow_id": 378997972,
        "jobs_sha256": "a0a05c66cb1a2726200a2c744ecfa973953eeb898b300c1ca39cd9d04d853b16",
    },
    38534815209: {
        "source": "pl-fulbright-poland",
        "head_sha": "e47a87e43a672b1b6429749d1cef4851ea0dedee",
        "workflow_id": 378997972,
        "jobs_sha256": "4e0562e71587d398971ff009743bf913c454d6290b1958e91b84e856fccca265",
    },
    38516460936: {
        "source": "pl-fulbright-poland",
        "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "workflow_id": 378997972,
        "jobs_sha256": "362297aafe9b4bd60caf347a138cfbe126bb9e4daf6ac02180ef9277e42dcf10",
    },
    38482292637: {
        "source": "pl-fulbright-poland",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378997972,
        "jobs_sha256": "bd45d5fc1c1e124b571ff5a3c17e6220ed4a260feaeeff36ff6333807aa4cad7",
    },
    38414598116: {
        "source": "pl-fulbright-poland",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378997972,
        "jobs_sha256": "f5b9321cf6c1db4d6cbbd05bc1b042436cb76ab55cdf7cb48ab6308edc6779ce",
    },
    38329858046: {
        "source": "pl-fulbright-poland",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378997972,
        "jobs_sha256": "5cab1031afa02212961c8b9e2e228f5eab56bfa066ee9d90e64cb6974dafe279",
    },
    38275155904: {
        "source": "pl-fulbright-poland",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378997972,
        "jobs_sha256": "b1aee9249dbec5988f1947b4e07640ba8380874a32755ac949960fd94b4a9bb7",
    },
    38212988856: {
        "source": "pl-fulbright-poland",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378997972,
        "jobs_sha256": "e853752a54a8e104d259bb64da9368545a5510253f20eb76ab81d2e2ce83aa2d",
    },
    38180538545: {
        "source": "pl-fulbright-poland",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378997972,
        "jobs_sha256": "75c9c36c3f747c6b200ccd4f86bc1739cac60b90e52195fe890d780f04da54d3",
    },
    38143532468: {
        "source": "pl-fulbright-poland",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378997972,
        "jobs_sha256": "8e67abe29d0fdbc85e53fe5ccd3c8660271387f016ce856927f3ccdac61ccf7d",
    },
    38118099239: {
        "source": "pl-fulbright-poland",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378997972,
        "jobs_sha256": "567db425a80c7ba59207780a566b5532e70517b75abff3003c1ec5d16256795b",
    },
    38089704891: {
        "source": "pl-fulbright-poland",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378997972,
        "jobs_sha256": "8efaa6b04283d37e1725ddda9092de3006919f27c9be290f3ab527a3e8aa4e0e",
    },
    38071840307: {
        "source": "pl-fulbright-poland",
        "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "workflow_id": 378997972,
        "jobs_sha256": "fca0859b1567662298ded1fa7381ed20bc73090e59b830b1e24f892b4a3acb90",
    },
    38054248675: {
        "source": "pl-fulbright-poland",
        "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "workflow_id": 378997972,
        "jobs_sha256": "565341e8a6ff61d05a2c42899050f5a5f0de936906757b6ef2dbb488c7c7b5f7",
    },
    38030743591: {
        "source": "pl-fulbright-poland",
        "head_sha": "6e5922277de22b96874c23a705a7d838512c6caf",
        "workflow_id": 378997972,
        "jobs_sha256": "707f5964ccfe740982611831913aa565522f2448f44450e946d3eb86dd9239a7",
    },
    38001660202: {
        "source": "pl-fulbright-poland",
        "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "workflow_id": 378997972,
        "jobs_sha256": "81723cd8be6a2f3ba4076799619e46891be25f5875f76b210cc0cac87a684e12",
    },
    37973829042: {
        "source": "pl-fulbright-poland",
        "head_sha": "251d29d718aa4d79f4b0b93b8512cb82bb82ce23",
        "workflow_id": 378997972,
        "jobs_sha256": "34e17efff1f9b4aaa5449accb82b3cf225647a738fc0d282a236406dcc3322a2",
    },
    37894981123: {
        "source": "pl-fulbright-poland",
        "head_sha": "6f1971d163e39e44d46ffd72bec20795c59df105",
        "workflow_id": 378997972,
        "jobs_sha256": "321f93e4f2d8211fceb9cefbeedfc3ac977fa911ae79bfbb46cd69bde5b0d04f",
    },
    37846330041: {
        "source": "pl-fulbright-poland",
        "head_sha": "805dab9f07dda3986d78c0209420b195f6a85265",
        "workflow_id": 378997972,
        "jobs_sha256": "af308dab17be1e5dda3dc8e37253ac8fa4b6bf06d13cb3df71195d7afb650cc2",
    },
    37846319809: {
        "source": "pl-fulbright-poland",
        "head_sha": "805dab9f07dda3986d78c0209420b195f6a85265",
        "workflow_id": 378997972,
        "jobs_sha256": "a77107dba1fe82f6f73d174ee3dd59110971f1f01ec725937d3ea32286d1aff4",
    },
}
_LEGACY_ACCESS_OBSERVATIONS = [
    {
        "run_id": 38763419096,
        "run_attempt": 1,
        "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "source": "pl-fulbright-poland",
        "stage": "publish",
        "access_scope": "antecedent_unlogged_unknown_not_a_proven_content_hold",
        "log_sha256": "6516eabc845db06bf538d3f279a0b11002007f9d7c6f01b049523920d90c3571",
    }
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
            type(value) in (int, float) and math.isfinite(value) and value >= 0
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
    epochs = (
        "until",
        "completed",
        "inventory_completed",
        "rate_limit_until",
        "observed_at",
    )
    if (
        any(not supported_epoch(state[key]) for key in epochs)
        or any(not supported_epoch(value) for value in state["starts"])
        or not supported_epoch(time.time() + state["interval"])
        or not supported_epoch(time.time() + state["page_gap"])
    ):
        raise AdapterError("Unsupported retained clock magnitude", "access")
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
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        if _LAST_KNOWN_STATE is None:
            _LAST_KNOWN_STATE = copy.deepcopy(state)
        else:
            _LAST_KNOWN_STATE = merge_budget(_LAST_KNOWN_STATE, state)
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
                    "Workflow inventory remained unstable", "access"
                ) from None



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
    path = os.environ.get("PL_FULBRIGHT_PACING_ARTIFACT_PATH")
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
    path = os.environ.get("PL_FULBRIGHT_PACING_ARTIFACT_PATH")
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
        raise AdapterError(
            "Explicit infrastructure transport required", "access"
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
                or isinstance(reason, socket.gaierror)
                and reason.errno == socket.EAI_AGAIN
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
        with response:
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
            if challenge:
                response_constraints_saved = True
                if not robots or status == 429:
                    state["blocked"] = True
                raise AdapterError(
                    "Visible publisher verification or refusal", "access"
                )
            if publisher and not robots and status == 200:
                decoded = body.decode(
                    response.headers.get_content_charset() or "utf-8", "strict"
                )
                if "CC BY-NC 4.0" not in PublisherHTML(decoded).root.text():
                    raise AdapterError(
                        "Publisher content licence changed", "access"
                    )
            if publisher and not (robots and status == 200):
                response_constraints_saved = True
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
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if publisher and response_received:
            response_constraints_saved = False
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
                    if not primary:
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
                "Local collection requires its exact workflow paused",
                "access",
            )



def scan_family_history():
    """Read complete producer history and rank exact job completion."""
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



def prepare_collection():
    """Verify the source producer before any family state initialization."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT
    global _LOCAL_FIRST_ACCESS_CHECKED, _LEGACY_COLLECTION_OBSERVATION
    global _COLLECTION_STARTED, _COLLECTION_PREPARED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        local_workflow_definition()
    if _COLLECTION_LOCK_FD is None:
        _LEGACY_COLLECTION_OBSERVATION = legacy_collection_observation()
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
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
                "Unsafe or active publisher lifecycle lifecycle lock", "access"
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
            descriptor,
            guard,
            (info.st_dev, info.st_ino),
            path,
        )
    collection_lock_authority()
    _LOCAL_FIRST_ACCESS_CHECKED = False
    _COLLECTION_STARTED = time.time()
    restore_family_artifact()
    _COLLECTION_PREPARED = True



def save_budget(state):
    """Every legacy caller uses the same failure-sticky modern write."""
    return write_budget(state)






class RobotsDisallow(AdapterError):
    """A genuine applicable REP restriction, retained independently."""




def public_url(url):
    """Keep source traffic on the original reviewed HTTPS publisher alias."""
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or host not in _PUBLISHER_HOSTS
        or parsed.port not in (None, 443)
    ):
        raise AdapterError("Source leaves reviewed public HTTPS host", "access")
    return url


def selected_robots_policies(groups):
    """A legacy grant cannot displace current identity's wildcard policy."""
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [g for g in groups if agent in g["agents"]]
    wildcard = [g for g in groups if "*" in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    return [current or wildcard] + ([legacy] if legacy else [])


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
    if (
        directives
        and not any(g["agents"] for g in groups)
        and not all(line.startswith("sitemap:") for line in directives)
    ):
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
            for key, value in group["timing"]:
                try:
                    if key == "crawl-delay":
                        if not re.fullmatch(decimal, value):
                            raise ValueError()
                        seconds = float(value)
                    else:
                        match = re.fullmatch(
                            r"([0-9]+)\s*/\s*(" + decimal + r")", value
                        )
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
                        "Unsupported applicable robots timing; pending retained",
                        "access",
                    ) from None
        if matches and not max(matches)[1]:
            raise RobotsDisallow(
                "Robots policy disallows this source endpoint", "access"
            )
    return delay


def check_robots(url):
    """Apply every cached target policy within its durable pending scope."""
    global _ROBOTS_CONTEXT
    parsed = urllib.parse.urlsplit(public_url(url))
    if (
        parsed.path == "/robots.txt"
        and not parsed.query
        and not parsed.fragment
    ):
        return 12.2
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin in _ROBOTS:
        text = _ROBOTS[origin]
        return 12.2 if text is None else apply_robots_policy(text, url)
    previous = _ROBOTS_CONTEXT
    context = {"target": url, "delay": None}
    _ROBOTS_CONTEXT = context
    try:
        status, headers, body = fetch_source(
            origin + "/robots.txt", policy_response=True
        )
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
            return 12.2
        if status != 200:
            raise AdapterError(
                "Required robots policy unavailable", "access", status
            )
        text = body.decode(headers.get_content_charset() or "utf-8", "strict")
        _ROBOTS[origin] = text
        return (
            context["delay"]
            if context["delay"] is not None
            else apply_robots_policy(text, url)
        )
    finally:
        _ROBOTS_CONTEXT = previous


def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    deadline=None,
    retry_jitter=0,
    infrastructure=False,
    byte_limit=5_000_000,
    publisher=False,
):
    """Preserve default GitHub transport; guard each physical source hop."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if infrastructure:
        return physical_request_bytes(
            url,
            method=method,
            headers=headers,
            payload=payload,
            byte_limit=byte_limit,
            infrastructure=True,
        )
    if not publisher and host not in _PUBLISHER_HOSTS:
        clamp_publication_window()
        if _RUN_END is not None:
            check_deadline()
            deadline = min(
                deadline if deadline is not None else _PHASE_END,
                _PHASE_END,
                _RUN_END - EXPORT_TIMEOUT,
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
    parsed = urllib.parse.urlsplit(public_url(url))
    literal = (
        parsed.path == "/robots.txt"
        and not parsed.query
        and not parsed.fragment
    )
    result = physical_request_bytes(
        url,
        interval=max(12.2, interval),
        method=method,
        headers=headers,
        payload=payload,
        robots=literal,
        byte_limit=byte_limit,
    )
    if not literal and result[0] in (401, 403, 451):
        raise AdapterError(
            f"Publisher refused access: HTTP {result[0]}", "access", result[0]
        )
    return result


def clamp_publication_window():
    """Only tighten the entry-frozen lifecycle against its actual job proof."""
    global _RUN_END, _SOURCE_END, _PHASE_END
    if _RUN_END is not None and _PUBLICATION_WINDOW is not None:
        _RUN_END = min(_RUN_END, _PUBLICATION_WINDOW["job"] - 60)
        _SOURCE_END = min(_SOURCE_END, _RUN_END - 540)
        _PHASE_END = min(
            _PHASE_END,
            (
                _SOURCE_END
                if _PHASE_NAME == "source"
                else _RUN_END - EXPORT_TIMEOUT
            ),
        )


def enter_phase(name, seconds):
    """Assign one bounded owner without renewing its existing deadline."""
    global _PHASE_END, _PHASE_NAME
    if _RUN_END is None:
        raise AdapterError("Entry-frozen lifecycle required", "access")
    if _PHASE_NAME == name:
        check_deadline()
        return
    limit = _RUN_END if name == "export" else _RUN_END - EXPORT_TIMEOUT
    _PHASE_END = min(time.monotonic() + seconds, limit)
    _PHASE_NAME = name
    check_deadline()


@contextlib.contextmanager
def execution():
    """Freeze admission once; publication owns its original HTTP alarms."""
    global _RUN_END, _SOURCE_END, _PHASE_END, _PHASE_NAME
    global _COLLECTION_PREPARED
    if _RUN_END is not None:
        raise AdapterError("Nested execution refused", "access")
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    _SOURCE_END = min(time.monotonic() + COLLECTION_TIMEOUT, _RUN_END - 540)
    _PHASE_END, _PHASE_NAME = _SOURCE_END, "source"
    _COLLECTION_PREPARED = False
    try:
        yield
    finally:
        try:
            close_collection_lock()
        finally:
            _RUN_END = _SOURCE_END = _PHASE_END = _PHASE_NAME = None


@contextlib.contextmanager
def collection_lock():
    """The acquired modern lifecycle lock encloses the unchanged collector."""
    collection_lock_authority()
    yield


def collect():
    """Run the complete original graph under one fresh continuity entry."""
    global _COLLECTION_PREPARED
    if _RUN_END is not None:
        check_deadline()
        if not _COLLECTION_PREPARED:
            prepare_collection()
        _COLLECTION_PREPARED = False
        return collect_body()
    with execution():
        prepare_collection()
        _COLLECTION_PREPARED = False
        return collect_body()



def legacy_collection_observation():
    """Observe the original coordination lock before this entry creates it."""
    path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"status": "ABSENT_UNKNOWN"}
    identity = None
    try:
        before = os.fstat(descriptor)
        identity = (before.st_dev, before.st_ino)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
        ):
            raise AdapterError("Unsafe original collection lock", "access")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        raw = os.read(descriptor, 16385)
        after = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
        signature = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if (
            signature(before) != signature(after)
            or signature(after) != signature(current)
            or len(raw) > 16384
        ):
            raise AdapterError("Original collection lock changed", "access")
        return {
            "status": "PRESENT_UNLOCKED_UNKNOWN_ANCESTRY",
            "inode": before.st_ino,
            "bytes": len(raw),
            "raw_sha256": hashlib.sha256(raw).hexdigest(),
        }
    finally:
        # Close only the original acquired object, never a reused foreign FD.
        if identity is not None:
            current = os.fstat(descriptor)
            if identity == (current.st_dev, current.st_ino):
                os.close(descriptor)


def legacy_namespace_observation():
    """Read original host constraints without creating or adopting a budget."""
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    try:
        parent = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        return {
            "namespace": SOURCE_ID + "-pacing",
            "status": "ABSENT_NOT_ZERO_HISTORY",
            "collection_lock_before_entry": _LEGACY_COLLECTION_OBSERVATION,
        }
    try:
        initial = os.fstat(parent)
        named = os.stat(directory, follow_symlinks=False)
        identity = (initial.st_dev, initial.st_ino)
        if (
            not stat.S_ISDIR(initial.st_mode)
            or initial.st_uid != os.getuid()
            or initial.st_mode & 0o077
            or identity != (named.st_dev, named.st_ino)
        ):
            raise AdapterError("Unsafe original pacing parent", "access")
        known = {
            hashlib.sha256(SOURCE_ID.encode()).hexdigest(): "publisher",
            hashlib.sha256(b"api.github.com").hexdigest(): "default_github",
        }
        files = []
        for name in sorted(os.listdir(parent)):
            if name not in known:
                raise AdapterError("Unknown original pacing bucket", "access")
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "rb") as file:
                before = os.fstat(file.fileno())
                validate_owned_output(before)
                fcntl.flock(file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                raw = file.read(16385)
                after = os.fstat(file.fileno())
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                signature = lambda value: (
                    value.st_dev,
                    value.st_ino,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
                if (
                    signature(before) != signature(after)
                    or signature(after) != signature(current)
                    or not raw
                    or len(raw) > 16384
                ):
                    raise AdapterError(
                        "Original pacing bucket changed", "access"
                    )
                state = json.loads(raw)
                if (
                    not isinstance(state, dict)
                    or set(state) != {"starts", "interval", "until"}
                    or not numeric(state["interval"])
                    or state["interval"] < 6
                    or not numeric(state["until"])
                    or not isinstance(state["starts"], list)
                    or len(state["starts"]) > 1000
                    or any(not numeric(value) for value in state["starts"])
                ):
                    raise AdapterError(
                        "Unknown original pacing constraints", "access"
                    )
                files.append(
                    {
                        "bucket": known[name],
                        "name": name,
                        "raw_sha256": hashlib.sha256(raw).hexdigest(),
                        "inode": before.st_ino,
                        "state": state,
                    }
                )
        current = os.stat(directory, follow_symlinks=False)
        if identity != (current.st_dev, current.st_ino):
            raise AdapterError("Original pacing parent replaced", "access")
        return {
            "namespace": SOURCE_ID + "-pacing",
            "status": "PRESENT_UNKNOWN_ANCESTRY",
            "parent_inode": initial.st_ino,
            "files": files,
            "collection_lock_before_entry": _LEGACY_COLLECTION_OBSERVATION,
        }
    finally:
        os.close(parent)


def reviewed_migration_state():
    """Apply only an exact independently reviewed first-generation boundary."""
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
        or type(marker["schema"]) is not int
        or marker["schema"] != 1
        or marker["source"] != SOURCE_ID
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/293"
            r"#issuecomment-[0-9]+",
            marker.get("evidence", ""),
        )
    ):
        raise AdapterError("Explicit reviewed migration missing", "access")
    legacy = legacy_namespace_observation()
    digest = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        digest != marker["legacy_state_sha256"]
        or hashlib.sha256(
            json.dumps(
                marker["state"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        != marker["state_sha256"]
    ):
        raise AdapterError("Reviewed original state changed", "access")
    state = validate_budget(copy.deepcopy(marker["state"]))
    for file in legacy.get("files", []):
        if file["bucket"] == "publisher":
            value = file["state"]
            if (
                state["interval"] < value["interval"]
                or state["until"] < value["until"]
                or not set(value["starts"]) <= set(state["starts"])
            ):
                raise AdapterError(
                    "Original publisher MAX not retained", "access"
                )
    state["until"] = max(state["until"], time.time() + 60)
    state["observed_at"] = max(state["observed_at"], time.time())
    return validate_budget(state)


def restore_family_artifact():
    """Exact reviewed zero-artifact ancestry, then strict newest owned state."""
    global _RESTORED, _STATE_TRUSTED, _RESTORED_LATEST, _RESTORED_HISTORY
    global _VERIFIED_REMOTE_CREATE
    _STATE_TRUSTED = False
    latest = latest_family_run()
    run_id, attempt, source = latest
    if run_id in _LEGACY_ATTEMPTS:
        expected = {
            (identity, 1): (value["head_sha"], value["workflow_id"])
            for identity, value in _LEGACY_ATTEMPTS.items()
        }
        if (
            latest != (38825421994, 1, SOURCE_ID)
            or _SCANNED_HISTORY != expected
        ):
            raise AdapterError(
                "Reviewed complete legacy history changed", "access"
            )
        for identity, value in _LEGACY_ATTEMPTS.items():
            if (
                _SCANNED_JOB_DIGESTS.get((identity, 1, 1))
                != value["jobs_sha256"]
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
                    "Reviewed zero-artifact history changed", "access"
                )
        incoming = reviewed_migration_state()
    else:
        value = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = value.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or value.get("total_count") != len(artifacts)
            or len(artifacts) > 100
        ):
            raise AdapterError("Incomplete newest artifact inventory", "access")
        matches = [
            item
            for item in artifacts
            if item.get("name") == f"{_ARTIFACT_NAME}-{attempt}"
        ]
        if len(matches) != 1:
            raise AdapterError(
                "Newest state missing; no older fallback", "access"
            )
        item = matches[0]
        if (
            type(item.get("id")) is not int
            or item["id"] <= 0
            or item.get("expired") is not False
            or item.get("workflow_run", {}).get("id") != run_id
            or item.get("workflow_run", {}).get("head_sha")
            != _ATTEMPT_HEADS.get((run_id, attempt))
            or type(item.get("size_in_bytes")) is not int
            or not 0 < item["size_in_bytes"] <= 32768
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest", ""))
        ):
            raise AdapterError("Newest artifact binding invalid", "access")
        incoming = download_inert_artifact(latest, item["id"], item["digest"])
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



class PhaseExpired(AdapterError):
    """A fixed admission deadline cannot be handled as a network retry."""

    def __init__(self):
        super().__init__("Fixed lifecycle admission exhausted", "access")


def main():
    global _PUBLISHING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    _PUBLISHING = args.publish
    with execution():
        try:
            # Trusted continuity precedes the original data-source snapshot.
            prepare_collection()
            return run_body(args.publish)
        finally:
            # Export remains possible after a transient snapshot failure.
            primary = sys.exc_info()[0] is not None
            try:
                enter_phase("export", EXPORT_TIMEOUT)
                export_family_artifact()
            except BaseException:
                fail_publisher_cleanup()
                if not primary:
                    raise





def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
