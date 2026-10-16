"""Collect genuine official Study in Denmark funding frameworks."""

import argparse
from collections import Counter, deque
import copy
import errno
import functools
import math
import socket
import ssl
import stat
from urllib import robotparser
import zipfile
import base64
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import gzip
import hashlib
from html.parser import HTMLParser
import io
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

SOURCE_ID = "dk-study-in-denmark"
SOURCE_URL = "https://studyindenmark.dk/study-options/scholarships"
WEBSITE_URL = "https://studyindenmark.dk/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "DK"
PUBLISHER_TYPE = "government"
ATTRIBUTION = (
    "Study in Denmark — Danish Ministry of Science, Higher Education "
    "and Digital Affairs"
)


_PAGE_COMPLETED = None




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
    delay = check_robots(url)
    document = fetch_source(url, min_interval=delay)
    if not re.search(
        r"</body\s*>\s*(?:</html\s*>\s*)?$", document, re.IGNORECASE
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    return PublisherHTML(document).root


def view(root):
    return one(
        [n for n in root.walk() if n.attrs.get("id") == "view"],
        "complete public Study in Denmark article",
    )


def normalize_state_json(raw):
    """Replace unquoted JS undefined; preserve actual text inside strings."""
    parts, quoted, escaped, index = [], False, False, 0
    while index < len(raw):
        char = raw[index]
        if quoted:
            parts.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
        if raw.startswith("undefined", index):
            before = raw[index - 1] if index else ""
            end = index + len("undefined")
            after = raw[end] if end < len(raw) else ""
            if before == ":" and after in (",", "}"):
                parts.append("null")
                index = end
                continue
        parts.append(char)
        index += 1
    return "".join(parts)


def public_state(root):
    """Read actual public SSR state without requesting internal endpoints."""
    scripts = [
        "".join(c for c in n.children if isinstance(c, str))
        for n in nodes(root, "script")
    ]
    matches = [
        re.fullmatch(r"window\.__data=(.*);", s, re.DOTALL)
        for s in scripts
        if s.startswith("window.__data=")
    ]
    match = one(matches, "public SSR inventory state")
    if not match:
        raise AdapterError("Missing complete public inventory state", "parse")
    raw = normalize_state_json(match.group(1))
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as error:
        raise AdapterError(
            "Invalid public SSR inventory state", "parse"
        ) from error


def source_facts(key, root):
    """Retain every material condition; omit unrelated call-to-action counts."""
    if (
        "Danish Ministry of Science, Higher Education and Digital Affairs"
        not in root.text()
    ):
        raise AdapterError("Publisher attribution changed", "parse")
    policy_links = [
        n
        for n in nodes(root, "a")
        if re.search(r"copyright|terms|reuse|licen[sc]e", n.text(), re.I)
    ]
    if policy_links:
        raise AdapterError(
            "New linked publisher policy requires review", "access"
        )
    state = public_state(root)
    data = state["content"]["data"]
    if data.get("rights") not in (None, ""):
        raise AdapterError(
            "New publisher rights statement requires review", "access"
        )
    if key in ("home", "study-options", "institutions", "news", "events"):
        items = data.get("items", [])
        if not isinstance(items, list):
            raise AdapterError(
                "Missing complete source navigation inventory", "parse"
            )
        if key == "news" and data.get("items_total") != len(items):
            raise AdapterError("Incomplete actual news inventory", "parse")
        inventory = sorted(
            (
                item.get("@type"),
                item.get("@id"),
                item.get("title"),
                item.get("description"),
            )
            for item in items
            if item.get("@type") != "Image"
        )
        if key == "news":
            totals = [
                value.get("total")
                for value in state["querystringsearch"]["subrequests"].values()
            ]
            if totals != [len(inventory)]:
                raise AdapterError("News inventory total mismatch", "parse")
        if key == "events":
            totals = [
                v.get("total")
                for v in state["querystringsearch"]["subrequests"].values()
            ]
            if totals != [0]:
                raise AdapterError(
                    "New current events require award review", "parse"
                )
        return json.dumps([data.get("title"), inventory], ensure_ascii=False)
    text = view(root).text()
    if key != "privacy":
        if "Find Your Study Programme" not in text:
            raise AdapterError(
                "Changed article boundary requires review", "parse"
            )
        text = text.split("Find Your Study Programme", 1)[0].strip()
    return text


def sitemap_facts(document, *, funding_only=True):
    """Strictly validate complete public XML before selecting source routes."""
    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        raise AdapterError("Malformed source sitemap XML", "parse") from error
    document_type = root.tag.rsplit("}", 1)[-1]
    if document_type.lower() in (
        "error",
        "challenge",
        "captcha",
        "accessdenied",
        "forbidden",
    ):
        raise AdapterError(
            "Publisher returned a refusal/challenge document", "access"
        )
    expected = "urlset" if funding_only else "sitemapindex"
    if document_type != expected:
        raise AdapterError("Unexpected source sitemap document type", "parse")
    urls = [
        e.text for e in root.iter() if e.tag.endswith("}loc") or e.tag == "loc"
    ]
    if not urls:
        raise AdapterError("Source sitemap has zero locations", "parse")
    invalid, classes = 0, []
    for url in urls:
        if not isinstance(url, str):
            invalid += 1
            if len(classes) < 3:
                classes.append("non-string:missing-host")
            continue
        try:
            parsed = urllib.parse.urlsplit(url)
            valid = (
                url.startswith(WEBSITE_URL)
                and parsed.scheme == "https"
                and parsed.netloc == "studyindenmark.dk"
                and not parsed.query
                and not parsed.fragment
                and not re.search(r"\s", url)
            )
            scheme = (
                parsed.scheme
                if parsed.scheme in ("https", "http")
                else "other-scheme"
            )
            host = (
                "expected-host"
                if parsed.hostname == "studyindenmark.dk"
                else "other-host" if parsed.hostname else "missing-host"
            )
        except ValueError:
            valid, scheme, host = False, "invalid-scheme", "invalid-host"
        if not valid:
            invalid += 1
            if len(classes) < 3:
                classes.append(scheme + ":" + host)
    if invalid:
        raise AdapterError(
            f"Invalid sitemap locations: total={len(urls)}, invalid={invalid}, "
            + "classes="
            + ",".join(classes),
            "parse",
        )
    if len(urls) != len(set(urls)):
        raise AdapterError("Duplicate source sitemap route", "parse")
    if not funding_only:
        return sorted(urls)
    return sorted(
        u
        for u in urls
        if "/portal/" not in u
        and not re.search(r"\.(?:jpe?g|png|gif|webp|pdf)$", u, re.I)
        and re.search(
            r"scholar|phd|grant|stipend|fund|terms|legal|copyright|privacy",
            u,
            re.I,
        )
    )


def read_sitemap(url):
    interval = check_robots(url)
    status, _, body = fetch_inventory_page(
        request_bytes, url, interval=interval)
    if status != 200:
        raise AdapterError(
            f"Publisher sitemap returned HTTP {status}", "fetch", status
        )
    if body.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
                body = stream.read(5_000_001)
        except (OSError, EOFError) as error:
            raise AdapterError(
                "Malformed compressed sitemap", "parse"
            ) from error
    if len(body) > 5_000_000:
        raise AdapterError("Expanded sitemap exceeds 5 MB", "parse")
    if re.search(rb"<(?:!doctype\s+html|html\b)", body, re.I):
        raise AdapterError(
            "Publisher returned HTML/challenge instead of XML", "access"
        )
    return body


def read_child_inventory():
    """Allow one delayed same-endpoint recovery for invalid XML metadata."""
    for attempt in range(2):
        try:
            facts = sitemap_facts(read_sitemap(WEBSITE_URL + "sitemap1.xml.gz"))
        except AdapterError as error:
            if error.stage != "parse" or attempt:
                raise
            print(
                "Invalid child sitemap metadata; one paced recovery: "
                + safe_error(error),
                file=sys.stderr,
            )
            time.sleep(30)
            continue
        # Valid changed policy/funding is substantive change, never retry it.
        if facts != REVIEWED_FUNDING_POLICY_ROUTES:
            raise AdapterError(
                "Publisher funding/policy route inventory changed", "parse"
            )
        return


PAGES = {
    "entry": "https://studyindenmark.dk/study-options/scholarships",
    "home": "https://studyindenmark.dk/",
    "study-options": "https://studyindenmark.dk/study-options",
    "institutions": (
        "https://studyindenmark.dk/study-options/higher-education-ins"
        "titutions"
    ),
    "privacy": "https://studyindenmark.dk/privacy",
    "news": "https://studyindenmark.dk/news",
    "events": "https://studyindenmark.dk/events",
    "tuition": (
        "https://studyindenmark.dk/study-options/tuition-fees-and-sch"
        "olarships"
    ),
    "application": "https://studyindenmark.dk/study-options/how-to-apply",
    "study-types": "https://studyindenmark.dk/study-options/what-can-i-study",
    "phd": (
        "https://studyindenmark.dk/study-options/what-can-i-study/phd"
        "-and-research"
    ),
    "masters": (
        "https://studyindenmark.dk/study-options/what-can-i-study/mas"
        "ters-programmes"
    ),
    "exchange": (
        "https://studyindenmark.dk/study-options/what-can-i-study/exc"
        "hange-programmes-and-summer-schools"
    ),
    "summer": (
        "https://studyindenmark.dk/study-options/what-can-i-study/sum"
        "mer-schools"
    ),
}

CONDITIONS = {
    "entry": "0e0e939d53a0107ce2ecd1621dd703b04d6c99be32c66750c3d6051fedf3a604",
    "home": "2154077b0f205614615336939fbe58b312dd46dbbc5697b79727e525fd772c0a",
    "study-options": "1ac07442182f4e086aece5427c5f8f27f929d506bdb91c7ed28f8d74c333f151",
    "institutions": "198334006f31921de75bc8c7e401d4e3beabb5a19603a2f18dd124620fee1b8a",
    "privacy": "8eee2ddc905d7aaaebf0bb2a11246b7ea74106f2aa0f499075a0926789cef0ea",
    "news": "6b7eb74dd4909c00b911fc0df9975792432f179a22f9f06814926178cce7a378",
    "events": "1b0b5107f236abc5523e4be8e2a571ed9597c73a03bd0c728b94f3cea0d2fa08",
    "tuition": "af3b10592236d7cb3c1456fe071c533fdca739f5c1535387fa407222f61c2253",
    "application": "e935d90f388c6a3c1a6d423ec7a1407d6dcb132dfc7f6943b91cc93ca12821d9",
    "study-types": "13e94bb6180d0cd602f8082dbd91e438e20d9bb76df3d4ae7bce2f36e49eac9d",
    "phd": "479e1e4a17f35d589cb09e975fe385b0abf8fad746df9465f1acc244120f9dec",
    "masters": "6cb135e7d7b630f52d1760a069e5784f64b040eb259f06cc1b6085405c5a1ce1",
    "exchange": "539be0c965f42bcd7d2067a4338cdbfbb4c92f9e8194b1560de22561ffa92665",
    "summer": "e8971bc0bd3734862a80c3d15102daa7d2a9ce1a75cec724c8f8f60337849268",
}

REVIEWED_FUNDING_POLICY_ROUTES = [
    "https://studyindenmark.dk/privacy",
    "https://studyindenmark.dk/study-options/scholarships",
    "https://studyindenmark.dk/study-options/tuition-fees-and-scholarships",
    "https://studyindenmark.dk/study-options/what-can-i-study/phd-and-research",
]

PROFILES = [
    {
        "track": "government",
        "key": "entry",
        "title": "Danish government scholarships for non-EU/EEA students",
        "categories": ["scholarships"],
        "summary": "Limited scholarships selected by Danish universities for "
        "highly qualified full-degree students: full/partial "
        "tuition waiver and/or living-cost grant. Requires "
        "citizenship outside EU/EEA/Switzerland, full-degree "
        "enrolment and a time-limited Danish education residence "
        "permit. No universal dated call or fixed amount stated.",
        "evidence": "Excluded: artistic higher-education admission; legal "
        "claim to Danish-citizen rights; specified "
        "admission-time Aliens Act §9c(1) permit as child of "
        "foreign parent holding §9m permit and citizenship "
        "outside EU/EEA; SU grant eligibility. The narrow "
        "child/parent clause is not a ban on all residents. "
        "Intro mentions Switzerland ambiguously; explicit "
        "eligibility bullet excludes it. Negative citizenship "
        "condition is preserved without inventing an exhaustive "
        "positive whitelist. Each university administers limited "
        "annual allocations and selects recipients; allocations "
        "are not distinct awards.",
        "hosts": ["DK"],
        "kind": "programme-overview",
    },
    {
        "track": "nordplus",
        "key": "entry",
        "title": "Nordplus higher education mobility scholarships",
        "categories": ["scholarships"],
        "summary": "Students enrolled at a Nordic or Baltic higher education "
        "institution may study in another Nordic or Baltic "
        "country as part of their degree. Contact the home "
        "university or national educational agency. Funding "
        "amount and dated application call are not specified.",
        "evidence": "Affiliation is not citizenship. Published destination "
        "region is Nordic/Baltic, without an exhaustive named "
        "country list; neither DK-only hosting nor a complete "
        "national whitelist is inferred. One framework, not a "
        "record per country.",
        "hosts": [],
        "kind": "programme-overview",
    },
    {
        "track": "erasmus",
        "key": "entry",
        "title": "Erasmus higher education exchange scholarships",
        "categories": ["scholarships"],
        "summary": "Study abroad for two to twelve months as part of higher "
        "education in the home country. Own source describes "
        "students from EU/EEA and Switzerland. The own exchange "
        "page confirms incoming study in Denmark, with prior "
        "enrolment at a higher education institution in the "
        "student’s country of residence.",
        "evidence": "Known Danish hosting: "
        "https://studyindenmark.dk/study-options/what-can-i-study/exc"
        "hange-programmes-and-summer-schools "
        ". Denmark is not the scheme’s only worldwide "
        "destination; source scholarship paragraph says abroad "
        "more broadly. Students from a region is not an "
        "unconditional citizenship whitelist. Consult home "
        "institution/national agency for precise eligibility and "
        "application procedure; no universal dated call or fixed "
        "grant amount. Do not turn bilateral exchange advice "
        "into unnamed award clones.",
        "hosts": ["DK"],
        "kind": "programme-overview",
    },
    {
        "track": "erasmus-mundus",
        "key": "entry",
        "title": "Erasmus Mundus / Joint Master Degree scholarships",
        "categories": ["scholarships"],
        "summary": "Scholarships for specific joint master’s courses offered "
        "by a Danish institution and another European university "
        "or college. Own source includes EU/EEA and non-EU/EEA "
        "students. Contact the individual master’s course for "
        "funding conditions and application procedures; no "
        "universal dated call or amount.",
        "evidence": "Danish institution establishes a known DK host; the "
        "other European partner country is unspecified, so DK is "
        "not presented as an exhaustive joint host list. Course "
        "catalogue links do not create synthetic current course "
        "awards.",
        "hosts": ["DK"],
        "kind": "programme-overview",
    },
    {
        "track": "fulbright",
        "key": "entry",
        "title": "Fulbright Denmark study and research grants",
        "categories": ["scholarships", "fellowships"],
        "summary": "An American scholar or postgraduate student at master’s "
        "or PhD level may apply for an academic year of study "
        "and/or research in Denmark. Visit Fulbright Denmark for "
        "selection and application procedures. Own overview gives "
        "no current dated call or award amount.",
        "evidence": "American is the publisher’s exact eligibility "
        "descriptor; this overview does not define a "
        "passport/citizenship or residence test, so no "
        "unsupported positive citizenship list is invented. This "
        "own substantive framework overview does not replicate "
        "the external provider’s individual call catalogue.",
        "hosts": ["DK"],
        "kind": "programme-overview",
    },
    {
        "track": "su",
        "key": "entry",
        "title": "Danish State Educational Support (SU)",
        "categories": ["scholarships"],
        "summary": "Educational support is generally awarded to Danish "
        "residents. International students may apply for equal "
        "status under Danish rules or EU law; individual "
        "conditions apply. No fixed award amount, universal "
        "application deadline or unconditional grant entitlement "
        "is stated.",
        "evidence": "Residence and equal-status conditions are not DK-only "
        "citizenship. Do not assume every foreign student "
        "qualifies, and do not merge SU with the separate "
        "government scholarship that excludes SU-eligible "
        "applicants.",
        "hosts": ["DK"],
        "kind": "programme-overview",
    },
    {
        "track": "industrial-phd",
        "key": "phd",
        "title": "Industrial PhD grant",
        "categories": ["grants", "jobs"],
        "summary": "Commercial research with employment by a private company "
        "and university PhD enrolment. Find a company and "
        "university supervisor; the company applies to Innovation "
        "Fund Denmark for the grant on the candidate’s behalf. "
        "After funding success, apply for Graduate School "
        "enrolment. Salary follows the company agreement; no "
        "grant amount or dated call stated.",
        "evidence": "The private company is grant applicant; employed PhD "
        "candidate is beneficiary, not direct grant applicant. "
        "Own Danish university context establishes DK host. "
        "General PhD admission normally requires a relevant "
        "recognised master’s and good English, "
        "institution-dependent; generic 3+5/4+4 routes are not "
        "unconditional eligibility for this grant. DKK32,567 "
        "average University of Copenhagen faculty-funded salary "
        "and DKK50,000 general tuition fee are not this grant’s "
        "benefits/costs. No university job-bank, degree or "
        "third-party award clones.",
        "hosts": ["DK"],
        "kind": "institutional-grant",
    },
]


def read_pages():
    pages = {key: page(url) for key, url in PAGES.items()}
    index = sitemap_facts(
        read_sitemap(WEBSITE_URL + "sitemap.xml.gz"), funding_only=False
    )
    if index != [WEBSITE_URL + "sitemap1.xml.gz"]:
        raise AdapterError("Publisher sitemap index changed", "parse")
    read_child_inventory()
    return pages


def parse_inventory(pages):
    if set(pages) != set(PAGES):
        raise AdapterError("Incomplete source evidence", "parse")
    for key, root in pages.items():
        digest = hashlib.sha256(source_facts(key, root).encode()).hexdigest()
        if digest != CONDITIONS[key]:
            raise AdapterError(
                "Source facts/inventory changed: " + key, "parse"
            )
    records = []
    for profile in PROFILES:
        record = make_record(
            profile["title"],
            PAGES[profile["key"]],
            profile["categories"],
            kind=profile["kind"],
            host_countries=profile["hosts"],
            evidence=[ATTRIBUTION, profile["evidence"]],
        )
        record["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + profile["track"]).encode()
        ).hexdigest()[:24]
        record["summary"] = profile["summary"]
        records.append(record)
    validate_records(records)
    return records


def collect_inventory():
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
        tempfile.gettempdir(), "dk-study-in-denmark-pacing-" + str(os.getuid())
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
    publisher = "studyindenmark.dk" if host == "studyindenmark.dk" else host
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
    received_publisher_429 = False
    try:
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
                                budget["until"], time.time() + max(0, seconds)
                            )
                            legacy_save_budget(state, budget)
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


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "display_name": "Danish Ministry of Science, Higher Education and Digital Affairs",
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
                f"Validated refresh retains {len(records)} validated "
                "Study in Denmark "
                f"funded opportunities"
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
        checked_at = utc_now()
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
                last_checked_at=checked_at,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
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


FAMILY = "youthopps-dk-study-in-denmark-publisher-v1"


FAMILY_SOURCES = {SOURCE_ID}


_WORKFLOW_ID = 378390319


_ARTIFACT_NAME = "dk-study-in-denmark-pacing-state"


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
            "Study in Denmark state persistence is already unhealthy",
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
                "Unproved Study in Denmark completion write", "access"
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
            "Bounded Study in Denmark execution required", "access"
        )
    return _PHASE_END if _PHASE_END is not None else nonexport_end()


def check_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded Study in Denmark execution required", "access"
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
    result = workflow_api("/actions/workflows/fetch-dk-study-in-denmark.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-dk-study-in-denmark.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "Study in Denmark workflow is not serialized for this mode",
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
            "fetch-dk-study-in-denmark.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable Study in Denmark workflow head",
                "access",
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("STUDY_DENMARK_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native Study in Denmark MAX approval required",
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
                r"https://github\.com/YouthOpps/data-pipeline/issues/327"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native Study in Denmark approval", "access"
        ) from None


def restored_state():
    """Newest exact artifact is mandatory after the one reviewed legacy set."""
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Study in Denmark family history missing", "access")
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
            "Incomplete Study in Denmark artifact inventory", "access"
        )
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest Study in Denmark artifact unavailable", "access"
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
            "Invalid Study in Denmark artifact binding", "access"
        )
    return production_maintenance_state(
        download_inert_artifact(latest, artifact["id"], artifact["digest"])
    )


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
            "Owned Study in Denmark artifact is unsettled", "access"
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
                "Local Study in Denmark flight is unsettled", "access"
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
            "Study in Denmark collection phase exhausted", "fetch"
        ) from None


def export_family_artifact():
    path = os.environ.get("STUDY_DENMARK_PACING_ARTIFACT_PATH")
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
            "Study in Denmark current job is not executing", "access"
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
            "Study in Denmark current job inventory incomplete", "access"
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
            "Study in Denmark current job ownership mismatch", "access"
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
            "Untrusted Study in Denmark job clock", "access"
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
        frame["assessed"] = not _PUBLISHER_GATE_FAILED
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
        ) == "studyindenmark.dk"
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
        raise AdapterError("Unreviewed Study in Denmark source route", "access")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def is_inventory(url):
    """Exact original discovery units; article details retain physical pace."""
    parsed = urllib.parse.urlsplit(url)
    inventory = {
        urllib.parse.urlsplit(PAGES[key]).path
        for key in (
            "entry",
            "home",
            "study-options",
            "institutions",
            "news",
            "events",
            "study-types",
        )
    } | {"/sitemap.xml.gz", "/sitemap1.xml.gz"}
    return is_publisher(url) and parsed.path in inventory


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


class PublisherResponse:
    """Keep received-response failures outside the connection retry path."""

    _failure = None

    def __init__(self, response, publisher=True, frame=None):
        self.response = response
        self.publisher = publisher
        self.frame = frame

    @classmethod
    def check(cls, publisher=True):
        if publisher and cls._failure is not None:
            raise AdapterError(
                "Publisher response failure is sticky", "access"
            ) from cls._failure

    def fail(self, error):
        if not self.publisher:
            return
        if type(self)._failure is None:
            type(self)._failure = error
        if self.frame is not None:
            self.frame["assessed"] = False
        try:
            fail_policy()
        except BaseException:
            pass

    def __enter__(self):
        if not self.publisher:
            return self.response.__enter__()
        try:
            return self.response.__enter__()
        except BaseException as error:
            self.fail(error)
            # A failed enter still owns the received response. Keep its error
            # primary if releasing that response also fails.
            try:
                self.response.close()
            except BaseException:
                pass
            if self.publisher and isinstance(
                error, (OSError, urllib.error.URLError)
            ):
                raise AdapterError(
                    "Publisher response entry failed", "access"
                ) from error
            raise

    def __exit__(self, kind, error, traceback):
        if not self.publisher:
            return self.response.__exit__(kind, error, traceback)
        try:
            suppressed = self.response.__exit__(kind, error, traceback)
        except BaseException as close_error:
            self.fail(error if error is not None else close_error)
            if error is None:
                if self.publisher:
                    raise AdapterError(
                        "Publisher response close failed", "access"
                    ) from close_error
                raise
            if isinstance(error, AdapterError) and error.status == 429:
                # Keep the intentional stop primary, with the failed release
                # retained as its cause rather than a healthy assessment.
                error._header429_unassessed = True
                raise error from close_error
            # Preserve the body failure instead of the secondary close error.
            if self.publisher and isinstance(
                error, (OSError, urllib.error.URLError)
            ):
                raise AdapterError(
                    "Publisher response body failed", "access"
                ) from error
            return False
        if error is not None and not isinstance(error, AdapterError):
            self.fail(error)
            if self.publisher and isinstance(
                error, (OSError, urllib.error.URLError)
            ):
                raise AdapterError(
                    "Publisher response body failed", "access"
                ) from error
        return suppressed


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
    global _LOCAL_FIRST_WIRE_VERIFIED
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
        raise AdapterError("Healthy source collection phase required", "access")
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
            verify_journal()
            verify_collection()
            frame["started"] = True
            PublisherResponse.check()
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
            # Only our intentional stop after healthy close is assessed.
            raise PolicyStop("Publisher stopped: HTTP 429", "fetch", 429)
        with PublisherResponse(response, frame=frame):
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
                json.dumps(jobs, sort_keys=True, separators=(",", ":")).encode()
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
            "/actions/workflows/fetch-dk-study-in-denmark.yml/runs"
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
                raise AdapterError("Duplicate/unbounded workflow run", "access")
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/327"
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
                raise AdapterError("Legacy actual job bounds changed", "access")
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
    """Read an exact approved old publisher bucket without creating it."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    mode = "hosted" if hosted else "local"
    if not isinstance(packet, dict) or packet.get("mode") != mode:
        raise AdapterError("Mode-bound legacy observation required", "access")
    status = packet.get("status")
    allowed = {"mode", "status"}
    if not hosted and status != "directory_absent":
        allowed |= {"parent"}
    if status == "present":
        allowed |= {"journal", "raw_sha256", "raw_bytes"}
    if (
        set(packet) != allowed
        or status not in {"directory_absent", "publisher_absent", "present"}
        or hosted
        and status != "publisher_absent"
    ):
        raise AdapterError("Invalid legacy observation schema", "access")
    if hosted and os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise AdapterError("Fresh hosted context required", "access")
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    try:
        named = os.stat(directory, follow_symlinks=False)
    except FileNotFoundError:
        if status in {"directory_absent", "publisher_absent"} and (
            hosted or status == "directory_absent"
        ):
            return None
        raise AdapterError(
            "Approved legacy parent disappeared", "access"
        ) from None
    if status == "directory_absent":
        raise AdapterError("Legacy parent is no longer absent", "access")
    parent_id = (named.st_dev, named.st_ino)
    if (
        not stat.S_ISDIR(named.st_mode)
        or named.st_uid != os.getuid()
        or stat.S_IMODE(named.st_mode) != 0o700
        or not hosted
        and packet["parent"] != list(parent_id)
    ):
        raise AdapterError("Legacy parent observation changed", "access")
    parent = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    descriptor = identity = None
    try:

        def parent_current():
            info = os.fstat(parent)
            current = os.stat(directory, follow_symlinks=False)
            if (
                (info.st_dev, info.st_ino) != parent_id
                or (current.st_dev, current.st_ino) != parent_id
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise AdapterError(
                    "Legacy parent changed during read", "access"
                )

        parent_current()
        name = hashlib.sha256(b"studyindenmark.dk").hexdigest()
        try:
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            if status == "publisher_absent":
                parent_current()
                return None
            raise AdapterError(
                "Approved legacy publisher bucket missing", "access"
            ) from None
        if status != "present":
            raise AdapterError(
                "Unreviewed legacy publisher bucket appeared", "access"
            )
        expected = packet["journal"]
        if (
            not isinstance(expected, list)
            or len(expected) != 2
            or any(type(v) is not int or v < 0 for v in expected)
            or (before.st_dev, before.st_ino) != tuple(expected)
        ):
            raise AdapterError("Legacy publisher identity changed", "access")
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        identity = tuple(expected)

        def original():
            parent_current()
            held = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (
                (held.st_dev, held.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity
                or not stat.S_ISREG(held.st_mode)
                or held.st_uid != os.getuid()
                or stat.S_IMODE(held.st_mode) != 0o600
                or held.st_nlink != 1
            ):
                raise AdapterError(
                    "Legacy publisher authority changed", "access"
                )
            return held

        prior = original()
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        original()
        raw = os.read(descriptor, 32769)
        after = original()
        if (
            journal_version(prior) != journal_version(after)
            or type(packet["raw_bytes"]) is not int
            or not 0 < packet["raw_bytes"] <= 32768
            or len(raw) != packet["raw_bytes"]
            or not isinstance(packet["raw_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", packet["raw_sha256"])
            or hashlib.sha256(raw).hexdigest() != packet["raw_sha256"]
        ):
            raise AdapterError("Exact legacy publisher bytes changed", "access")
        state = json.loads(raw)
        if (
            not isinstance(state, dict)
            or not {"starts", "interval", "until"}.issubset(state)
            or set(state) - {"starts", "interval", "until", "blocked"}
            or not numeric(state["interval"])
            or state["interval"] < 6
            or not numeric(state["until"])
            or state["until"] > 253402300799
            or state["interval"] > 253402300799
            or type(state.get("blocked", False)) is not bool
            or not isinstance(state["starts"], list)
            or len(state["starts"]) > 10
            or any(not numeric(v) or v > 253402300799 for v in state["starts"])
            or state["starts"] != sorted(set(state["starts"]))
        ):
            raise AdapterError(
                "Untrusted legacy publisher restrictions", "access"
            )
        return state
    finally:
        primary = sys.exc_info()[1]
        errors = []
        if descriptor is not None and identity is not None:
            try:
                close_legacy_descriptor(descriptor, identity)
            except BaseException as error:
                errors.append(error)
        try:
            close_legacy_descriptor(parent, parent_id, True)
        except BaseException as error:
            errors.append(error)
        if errors and primary is None:
            raise errors[0]


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
    path = os.environ.get("STUDY_DENMARK_PACING_ARTIFACT_PATH")
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


def fetch_inventory_page(fetch, *args, **kwargs):
    """Physical inventory purpose is enforced durably inside request_bytes."""
    return fetch(*args, **kwargs)


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


_LOCAL_LEGACY_TRANSITION = {
    "schema": 1,
    "family": "youthopps-dk-study-in-denmark-publisher-v1",
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/issues/327#issuecomment"
        "-6213730922"
    ),
    "history_sha256": (
        "36230108096442d22f8648d60d2e8ec8e0ca3a72e4ca17745a08752c756470be"
    ),
    "state": {
        "schema": 3,
        "family": "youthopps-dk-study-in-denmark-publisher-v1",
        "observed_at": 1792146348.2589598,
        "not_before": 1792146408.2589598,
        "starts": [
            1791512447.0,
            1791512618.0,
            1791538519.0,
            1791574810.0,
            1791591300.0,
            1791622448.0,
            1791654553.0,
            1791658134.0,
            1791675923.0,
            1791709974.0,
            1791745715.0,
            1791762764.0,
            1791797898.0,
            1791843338.0,
            1791884495.0,
            1791922972.0,
            1791935473.0,
            1791964622.0,
            1791969172.0,
            1791986331.0,
            1792003823.0,
            1792020249.0,
            1792042450.0,
            1792050496.0,
            1792060886.0,
            1792061225.0,
            1792079652.0,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792079652.0,
        "page_interval": 30,
        "last_inventory_completed": 1792079652.0,
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
    },
    "legacy_local": {
        "mode": "local",
        "status": "directory_absent",
    },
}
_HOSTED_LEGACY_TRANSITION = {
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/327#issuecomment-6214619603",
    "family": "youthopps-dk-study-in-denmark-publisher-v1",
    "history_sha256": "36230108096442d22f8648d60d2e8ec8e0ca3a72e4ca17745a08752c756470be",
    "legacy_local": {"mode": "hosted", "status": "publisher_absent"},
    "schema": 1,
    "state": {
        "blocked": False,
        "completed": 1792148433.3697789,
        "completed_flight_id": "9522a7825067c1f071a46155b065f350",
        "completed_request_id": "c55d3426d87e6a589190b53a21b10f8e",
        "family": "youthopps-dk-study-in-denmark-publisher-v1",
        "flight_id": "9522a7825067c1f071a46155b065f350",
        "flight_kind": "policy",
        "flight_started": 1792148018.9562902,
        "in_flight": False,
        "interval": 12.2,
        "last_inventory_completed": 1792148431.658386,
        "legacy_policy_history_unknown": True,
        "legacy_rate_history_unknown": True,
        "not_before": 1792150100.2470775,
        "observed_at": 1792150040.2470775,
        "page_interval": 30,
        "rate_limit_strikes": 0,
        "rate_limit_until": 0,
        "request_id": "c55d3426d87e6a589190b53a21b10f8e",
        "request_inventory": True,
        "request_outcome": "response_assessed",
        "request_started": 1792148430.353523,
        "schema": 3,
        "starts": [
            1791512447.0,
            1791512618.0,
            1791538519.0,
            1791574810.0,
            1791591300.0,
            1791622448.0,
            1791654553.0,
            1791658134.0,
            1791675923.0,
            1791709974.0,
            1791745715.0,
            1791762764.0,
            1791797898.0,
            1791843338.0,
            1791884495.0,
            1791922972.0,
            1791935473.0,
            1791964622.0,
            1791969172.0,
            1791986331.0,
            1792003823.0,
            1792020249.0,
            1792042450.0,
            1792050496.0,
            1792060886.0,
            1792061225.0,
            1792079652.0,
            1792148078.9533362,
            1792148104.8406699,
            1792148104.8422296,
            1792148105.5759268,
            1792148117.7765877,
            1792148117.7802382,
            1792148117.7823892,
            1792148119.7591364,
            1792148149.7683485,
            1792148149.7721283,
            1792148149.7739372,
            1792148151.7989542,
            1792148181.8165355,
            1792148181.8200421,
            1792148181.8223696,
            1792148183.6710532,
            1792148213.6802664,
            1792148213.6841779,
            1792148213.6880767,
            1792148216.2218199,
            1792148228.465275,
            1792148231.7989163,
            1792148232.53702,
            1792148245.1024752,
            1792148258.4049585,
            1792148258.4071941,
            1792148258.4091108,
            1792148260.545423,
            1792148290.5802946,
            1792148290.5825093,
            1792148290.5839107,
            1792148291.886799,
            1792148304.0949204,
            1792148304.102868,
            1792148304.1068294,
            1792148305.4461205,
            1792148317.6593502,
            1792148317.6627917,
            1792148317.6642103,
            1792148319.0833418,
            1792148331.2920501,
            1792148331.298469,
            1792148331.3009331,
            1792148333.0377493,
            1792148345.263529,
            1792148345.2664971,
            1792148345.2684128,
            1792148346.8241396,
            1792148359.0502698,
            1792148359.0693548,
            1792148359.0726573,
            1792148360.4593155,
            1792148372.6669562,
            1792148372.6692326,
            1792148372.6706836,
            1792148374.1376119,
            1792148386.3429136,
            1792148386.3548,
            1792148386.3595123,
            1792148387.666594,
            1792148399.8755858,
            1792148399.8784413,
            1792148399.87971,
            1792148400.1518226,
            1792148430.353523,
            1792148430.626486,
            1792148430.6488929,
            1792148431.642742,
        ],
    },
}
_LEGACY_JOB_PINS = {
    "37873949490:1": {
        "attempt": 1,
        "head": "0504bf9a93d6e1060c45b80e551f0e33785d15b7",
        "conclusion": "success",
        "jobs": [
            {
                "id": 113638077973,
                "run_id": 37873949490,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/37873949490"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAadVt2FQ",
                "head_sha": (
                    "0504bf9a93d6e1060c45b80e551f0e33785d15b7"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/113638077973"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/37873949490/job/113638077973"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-09T02:18:32Z",
                "started_at": "2026-10-09T02:18:33Z",
                "completed_at": "2026-10-09T02:20:47Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-09T02:18:34Z",
                        "completed_at": "2026-10-09T02:18:35Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-09T02:18:35Z",
                        "completed_at": "2026-10-09T02:18:35Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-09T02:18:35Z",
                        "completed_at": "2026-10-09T02:18:36Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-09T02:18:36Z",
                        "completed_at": "2026-10-09T02:18:36Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-09T02:18:36Z",
                        "completed_at": "2026-10-09T02:20:45Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-09T02:20:45Z",
                        "completed_at": "2026-10-09T02:20:45Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-09T02:20:45Z",
                        "completed_at": "2026-10-09T02:20:45Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-09T02:20:45Z",
                        "completed_at": "2026-10-09T02:20:45Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-09T02:20:45Z",
                        "completed_at": "2026-10-09T02:20:45Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/113638077973"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001469,
                "runner_name": "GitHub Actions 1000001469",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "37873965035:1": {
        "attempt": 1,
        "head": "0504bf9a93d6e1060c45b80e551f0e33785d15b7",
        "conclusion": "success",
        "jobs": [
            {
                "id": 113638662187,
                "run_id": 37873965035,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/37873965035"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAadWRgKw",
                "head_sha": (
                    "0504bf9a93d6e1060c45b80e551f0e33785d15b7"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/113638662187"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/37873965035/job/113638662187"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-09T02:20:47Z",
                "started_at": "2026-10-09T02:21:25Z",
                "completed_at": "2026-10-09T02:23:38Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-09T02:21:26Z",
                        "completed_at": "2026-10-09T02:21:26Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-09T02:21:26Z",
                        "completed_at": "2026-10-09T02:21:27Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-09T02:21:27Z",
                        "completed_at": "2026-10-09T02:21:27Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-09T02:21:27Z",
                        "completed_at": "2026-10-09T02:21:28Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-09T02:21:28Z",
                        "completed_at": "2026-10-09T02:23:36Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-09T02:23:36Z",
                        "completed_at": "2026-10-09T02:23:36Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-09T02:23:36Z",
                        "completed_at": "2026-10-09T02:23:37Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-09T02:23:37Z",
                        "completed_at": "2026-10-09T02:23:37Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-09T02:23:37Z",
                        "completed_at": "2026-10-09T02:23:37Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/113638662187"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001470,
                "runner_name": "GitHub Actions 1000001470",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "37911421377:1": {
        "attempt": 1,
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "conclusion": "success",
        "jobs": [
            {
                "id": 113759068809,
                "run_id": 37911421377,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/37911421377"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAafJGiiQ",
                "head_sha": (
                    "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/113759068809"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/37911421377/job/113759068809"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-09T09:33:01Z",
                "started_at": "2026-10-09T09:33:03Z",
                "completed_at": "2026-10-09T09:35:19Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-09T09:33:04Z",
                        "completed_at": "2026-10-09T09:33:05Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-09T09:33:05Z",
                        "completed_at": "2026-10-09T09:33:06Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-09T09:33:06Z",
                        "completed_at": "2026-10-09T09:33:06Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-09T09:33:06Z",
                        "completed_at": "2026-10-09T09:33:07Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-09T09:33:07Z",
                        "completed_at": "2026-10-09T09:35:16Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-09T09:35:16Z",
                        "completed_at": "2026-10-09T09:35:16Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-09T09:35:16Z",
                        "completed_at": "2026-10-09T09:35:16Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-09T09:35:16Z",
                        "completed_at": "2026-10-09T09:35:16Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-09T09:35:16Z",
                        "completed_at": "2026-10-09T09:35:16Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/113759068809"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001536,
                "runner_name": "GitHub Actions 1000001536",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "37981496349:1": {
        "attempt": 1,
        "head": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "conclusion": "success",
        "jobs": [
            {
                "id": 113992975563,
                "run_id": 37981496349,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/37981496349"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAaioLEyw",
                "head_sha": (
                    "2d6491ddf2a37047efc7b996f190f648e647c0c6"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/113992975563"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/37981496349/job/113992975563"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-09T19:37:52Z",
                "started_at": "2026-10-09T19:37:54Z",
                "completed_at": "2026-10-09T19:40:10Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-09T19:37:55Z",
                        "completed_at": "2026-10-09T19:37:56Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-09T19:37:56Z",
                        "completed_at": "2026-10-09T19:37:57Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-09T19:37:57Z",
                        "completed_at": "2026-10-09T19:37:57Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-09T19:37:57Z",
                        "completed_at": "2026-10-09T19:37:58Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-09T19:37:58Z",
                        "completed_at": "2026-10-09T19:40:07Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-09T19:40:07Z",
                        "completed_at": "2026-10-09T19:40:07Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-09T19:40:07Z",
                        "completed_at": "2026-10-09T19:40:07Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-09T19:40:07Z",
                        "completed_at": "2026-10-09T19:40:07Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-09T19:40:07Z",
                        "completed_at": "2026-10-09T19:40:07Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/113992975563"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001566,
                "runner_name": "GitHub Actions 1000001566",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38008057163:1": {
        "attempt": 1,
        "head": "029a500dbf22c1d44ae592c667f26f33d9d364c8",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114081352174,
                "run_id": 38008057163,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38008057163"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAaj8dJ7g",
                "head_sha": (
                    "029a500dbf22c1d44ae592c667f26f33d9d364c8"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114081352174"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38008057163/job/114081352174"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T00:12:46Z",
                "started_at": "2026-10-10T00:12:47Z",
                "completed_at": "2026-10-10T00:15:00Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T00:12:48Z",
                        "completed_at": "2026-10-10T00:12:48Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T00:12:48Z",
                        "completed_at": "2026-10-10T00:12:49Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T00:12:49Z",
                        "completed_at": "2026-10-10T00:12:49Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T00:12:49Z",
                        "completed_at": "2026-10-10T00:12:50Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T00:12:50Z",
                        "completed_at": "2026-10-10T00:14:58Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-10T00:14:58Z",
                        "completed_at": "2026-10-10T00:14:58Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-10T00:14:58Z",
                        "completed_at": "2026-10-10T00:14:58Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T00:14:58Z",
                        "completed_at": "2026-10-10T00:14:59Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T00:14:59Z",
                        "completed_at": "2026-10-10T00:14:59Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114081352174"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001600,
                "runner_name": "GitHub Actions 1000001600",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38039171690:1": {
        "attempt": 1,
        "head": "c55b8d99378274467f2d9a290967dbe10bc25031",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114176334444,
                "run_id": 38039171690,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38039171690"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAalXCabA",
                "head_sha": (
                    "c55b8d99378274467f2d9a290967dbe10bc25031"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114176334444"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38039171690/job/114176334444"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T08:51:49Z",
                "started_at": "2026-10-10T08:51:51Z",
                "completed_at": "2026-10-10T08:54:08Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T08:51:52Z",
                        "completed_at": "2026-10-10T08:51:54Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T08:51:54Z",
                        "completed_at": "2026-10-10T08:51:54Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T08:51:54Z",
                        "completed_at": "2026-10-10T08:51:55Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T08:51:55Z",
                        "completed_at": "2026-10-10T08:51:55Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T08:51:55Z",
                        "completed_at": "2026-10-10T08:54:04Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-10T08:54:04Z",
                        "completed_at": "2026-10-10T08:54:05Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-10T08:54:05Z",
                        "completed_at": "2026-10-10T08:54:05Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T08:54:05Z",
                        "completed_at": "2026-10-10T08:54:05Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T08:54:05Z",
                        "completed_at": "2026-10-10T08:54:05Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114176334444"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001683,
                "runner_name": "GitHub Actions 1000001683",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38071840306:1": {
        "attempt": 1,
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114274364687,
                "run_id": 38071840306,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38071840306"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAam0htDw",
                "head_sha": (
                    "9b155b179283c8fa08cf56c1a83a54bd19d51814"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114274364687"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38071840306/job/114274364687"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T17:46:14Z",
                "started_at": "2026-10-10T17:46:16Z",
                "completed_at": "2026-10-10T17:49:13Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T17:46:17Z",
                        "completed_at": "2026-10-10T17:46:18Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T17:46:18Z",
                        "completed_at": "2026-10-10T17:46:19Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T17:46:19Z",
                        "completed_at": "2026-10-10T17:46:19Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T17:46:19Z",
                        "completed_at": "2026-10-10T17:46:20Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T17:46:20Z",
                        "completed_at": "2026-10-10T17:49:10Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-10T17:49:10Z",
                        "completed_at": "2026-10-10T17:49:11Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-10T17:49:11Z",
                        "completed_at": "2026-10-10T17:49:11Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T17:49:11Z",
                        "completed_at": "2026-10-10T17:49:11Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T17:49:11Z",
                        "completed_at": "2026-10-10T17:49:11Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114274364687"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001747,
                "runner_name": "GitHub Actions 1000001747",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38077142285:1": {
        "attempt": 1,
        "head": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114286377954,
                "run_id": 38077142285,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38077142285"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAam_-74g",
                "head_sha": (
                    "a97e28475634b9ff61489a11cd6378c9dafc67ae"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114286377954"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38077142285/job/114286377954"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T18:46:35Z",
                "started_at": "2026-10-10T18:46:37Z",
                "completed_at": "2026-10-10T18:48:54Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T18:46:38Z",
                        "completed_at": "2026-10-10T18:46:39Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T18:46:39Z",
                        "completed_at": "2026-10-10T18:46:40Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T18:46:40Z",
                        "completed_at": "2026-10-10T18:46:41Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T18:46:41Z",
                        "completed_at": "2026-10-10T18:46:41Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T18:46:41Z",
                        "completed_at": "2026-10-10T18:48:50Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-10T18:48:50Z",
                        "completed_at": "2026-10-10T18:48:51Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-10T18:48:51Z",
                        "completed_at": "2026-10-10T18:48:51Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T18:48:51Z",
                        "completed_at": "2026-10-10T18:48:51Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T18:48:51Z",
                        "completed_at": "2026-10-10T18:48:51Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114286377954"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001784,
                "runner_name": "GitHub Actions 1000001784",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38095986137:1": {
        "attempt": 1,
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114341865361,
                "run_id": 38095986137,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38095986137"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAan05nkQ",
                "head_sha": (
                    "10d99f8ef021c092851bf189ff67307865b8760c"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114341865361"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38095986137/job/114341865361"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-10T23:43:04Z",
                "started_at": "2026-10-10T23:43:07Z",
                "completed_at": "2026-10-10T23:45:23Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-10T23:43:08Z",
                        "completed_at": "2026-10-10T23:43:09Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-10T23:43:09Z",
                        "completed_at": "2026-10-10T23:43:10Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-10T23:43:10Z",
                        "completed_at": "2026-10-10T23:43:10Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-10T23:43:10Z",
                        "completed_at": "2026-10-10T23:43:11Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-10T23:43:11Z",
                        "completed_at": "2026-10-10T23:45:19Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-10T23:45:19Z",
                        "completed_at": "2026-10-10T23:45:20Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-10T23:45:20Z",
                        "completed_at": "2026-10-10T23:45:20Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-10T23:45:20Z",
                        "completed_at": "2026-10-10T23:45:20Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-10T23:45:20Z",
                        "completed_at": "2026-10-10T23:45:20Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114341865361"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001866,
                "runner_name": "GitHub Actions 1000001866",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38127431628:1": {
        "attempt": 1,
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114434603360,
                "run_id": 38127431628,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38127431628"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAapNV5YA",
                "head_sha": (
                    "10d99f8ef021c092851bf189ff67307865b8760c"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114434603360"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38127431628/job/114434603360"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-11T09:10:35Z",
                "started_at": "2026-10-11T09:10:37Z",
                "completed_at": "2026-10-11T09:12:54Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-11T09:10:38Z",
                        "completed_at": "2026-10-11T09:10:40Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-11T09:10:40Z",
                        "completed_at": "2026-10-11T09:10:41Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-11T09:10:41Z",
                        "completed_at": "2026-10-11T09:10:41Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-11T09:10:41Z",
                        "completed_at": "2026-10-11T09:10:42Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-11T09:10:42Z",
                        "completed_at": "2026-10-11T09:12:51Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-11T09:12:51Z",
                        "completed_at": "2026-10-11T09:12:51Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-11T09:12:51Z",
                        "completed_at": "2026-10-11T09:12:51Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-11T09:12:51Z",
                        "completed_at": "2026-10-11T09:12:52Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-11T09:12:52Z",
                        "completed_at": "2026-10-11T09:12:52Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114434603360"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001894,
                "runner_name": "GitHub Actions 1000001894",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38166598552:1": {
        "attempt": 1,
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114550330868,
                "run_id": 38166598552,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38166598552"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAaq7tV9A",
                "head_sha": (
                    "10d99f8ef021c092851bf189ff67307865b8760c"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114550330868"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38166598552/job/114550330868"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-11T19:06:19Z",
                "started_at": "2026-10-11T19:06:21Z",
                "completed_at": "2026-10-11T19:08:35Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-11T19:06:22Z",
                        "completed_at": "2026-10-11T19:06:23Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-11T19:06:23Z",
                        "completed_at": "2026-10-11T19:06:24Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-11T19:06:24Z",
                        "completed_at": "2026-10-11T19:06:24Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-11T19:06:24Z",
                        "completed_at": "2026-10-11T19:06:24Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-11T19:06:24Z",
                        "completed_at": "2026-10-11T19:08:33Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-11T19:08:33Z",
                        "completed_at": "2026-10-11T19:08:33Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-11T19:08:33Z",
                        "completed_at": "2026-10-11T19:08:33Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-11T19:08:33Z",
                        "completed_at": "2026-10-11T19:08:33Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-11T19:08:33Z",
                        "completed_at": "2026-10-11T19:08:33Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114550330868"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001917,
                "runner_name": "GitHub Actions 1000001917",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38185777201:1": {
        "attempt": 1,
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114607658617,
                "run_id": 38185777201,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38185777201"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAaryYWeQ",
                "head_sha": (
                    "10d99f8ef021c092851bf189ff67307865b8760c"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114607658617"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38185777201/job/114607658617"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-11T23:50:27Z",
                "started_at": "2026-10-11T23:50:29Z",
                "completed_at": "2026-10-11T23:52:44Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-11T23:50:30Z",
                        "completed_at": "2026-10-11T23:50:31Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-11T23:50:31Z",
                        "completed_at": "2026-10-11T23:50:32Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-11T23:50:32Z",
                        "completed_at": "2026-10-11T23:50:32Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-11T23:50:32Z",
                        "completed_at": "2026-10-11T23:50:33Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-11T23:50:33Z",
                        "completed_at": "2026-10-11T23:52:41Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-11T23:52:41Z",
                        "completed_at": "2026-10-11T23:52:42Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-11T23:52:42Z",
                        "completed_at": "2026-10-11T23:52:42Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-11T23:52:42Z",
                        "completed_at": "2026-10-11T23:52:42Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-11T23:52:42Z",
                        "completed_at": "2026-10-11T23:52:42Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114607658617"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001944,
                "runner_name": "GitHub Actions 1000001944",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38230925109:1": {
        "attempt": 1,
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "conclusion": "success",
        "jobs": [
            {
                "id": 114749909862,
                "run_id": 38230925109,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38230925109"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAat6CrZg",
                "head_sha": (
                    "5dff04768f0e558b4d301d5677c5a2c433c3a64b"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/114749909862"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38230925109/job/114749909862"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-12T09:35:58Z",
                "started_at": "2026-10-12T09:36:00Z",
                "completed_at": "2026-10-12T09:38:18Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-12T09:36:01Z",
                        "completed_at": "2026-10-12T09:36:02Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-12T09:36:02Z",
                        "completed_at": "2026-10-12T09:36:05Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-12T09:36:05Z",
                        "completed_at": "2026-10-12T09:36:05Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-12T09:36:05Z",
                        "completed_at": "2026-10-12T09:36:06Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-12T09:36:06Z",
                        "completed_at": "2026-10-12T09:38:15Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-12T09:38:15Z",
                        "completed_at": "2026-10-12T09:38:15Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-12T09:38:15Z",
                        "completed_at": "2026-10-12T09:38:15Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-12T09:38:15Z",
                        "completed_at": "2026-10-12T09:38:15Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-12T09:38:15Z",
                        "completed_at": "2026-10-12T09:38:15Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/114749909862"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001974,
                "runner_name": "GitHub Actions 1000001974",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38315250750:1": {
        "attempt": 1,
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115030674195,
                "run_id": 38315250750,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38315250750"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAayFzLEw",
                "head_sha": (
                    "5dff04768f0e558b4d301d5677c5a2c433c3a64b"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115030674195"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38315250750/job/115030674195"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-12T22:13:21Z",
                "started_at": "2026-10-12T22:13:23Z",
                "completed_at": "2026-10-12T22:15:38Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-12T22:13:23Z",
                        "completed_at": "2026-10-12T22:13:24Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-12T22:13:24Z",
                        "completed_at": "2026-10-12T22:13:25Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-12T22:13:25Z",
                        "completed_at": "2026-10-12T22:13:26Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-12T22:13:26Z",
                        "completed_at": "2026-10-12T22:13:26Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-12T22:13:26Z",
                        "completed_at": "2026-10-12T22:15:36Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-12T22:15:36Z",
                        "completed_at": "2026-10-12T22:15:36Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-12T22:15:36Z",
                        "completed_at": "2026-10-12T22:15:36Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-12T22:15:36Z",
                        "completed_at": "2026-10-12T22:15:36Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-12T22:15:36Z",
                        "completed_at": "2026-10-12T22:15:36Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115030674195"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000001999,
                "runner_name": "GitHub Actions 1000001999",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38377862160:1": {
        "attempt": 1,
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115230655632,
                "run_id": 38377862160,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38377862160"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa1EhEkA",
                "head_sha": (
                    "5dff04768f0e558b4d301d5677c5a2c433c3a64b"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115230655632"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38377862160/job/115230655632"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-13T09:39:18Z",
                "started_at": "2026-10-13T09:39:20Z",
                "completed_at": "2026-10-13T09:41:35Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-13T09:39:21Z",
                        "completed_at": "2026-10-13T09:39:22Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-13T09:39:22Z",
                        "completed_at": "2026-10-13T09:39:23Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-13T09:39:23Z",
                        "completed_at": "2026-10-13T09:39:23Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-13T09:39:23Z",
                        "completed_at": "2026-10-13T09:39:23Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-13T09:39:23Z",
                        "completed_at": "2026-10-13T09:41:32Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-13T09:41:32Z",
                        "completed_at": "2026-10-13T09:41:33Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-13T09:41:33Z",
                        "completed_at": "2026-10-13T09:41:33Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-13T09:41:33Z",
                        "completed_at": "2026-10-13T09:41:33Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-13T09:41:33Z",
                        "completed_at": "2026-10-13T09:41:33Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115230655632"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002027,
                "runner_name": "GitHub Actions 1000002027",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38461804180:1": {
        "attempt": 1,
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115519433874,
                "run_id": 38461804180,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38461804180"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa5X6skg",
                "head_sha": (
                    "5dff04768f0e558b4d301d5677c5a2c433c3a64b"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115519433874"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38461804180/job/115519433874"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-13T20:20:34Z",
                "started_at": "2026-10-13T20:20:36Z",
                "completed_at": "2026-10-13T20:22:52Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-13T20:20:37Z",
                        "completed_at": "2026-10-13T20:20:38Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-13T20:20:38Z",
                        "completed_at": "2026-10-13T20:20:39Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-13T20:20:39Z",
                        "completed_at": "2026-10-13T20:20:39Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-13T20:20:39Z",
                        "completed_at": "2026-10-13T20:20:40Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-13T20:20:40Z",
                        "completed_at": "2026-10-13T20:22:49Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-13T20:22:49Z",
                        "completed_at": "2026-10-13T20:22:50Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-13T20:22:50Z",
                        "completed_at": "2026-10-13T20:22:50Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-13T20:22:50Z",
                        "completed_at": "2026-10-13T20:22:50Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-13T20:22:50Z",
                        "completed_at": "2026-10-13T20:22:50Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115519433874"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002067,
                "runner_name": "GitHub Actions 1000002067",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38486461563:1": {
        "attempt": 1,
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115601334216,
                "run_id": 38486461563,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38486461563"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa6mBfyA",
                "head_sha": (
                    "5dff04768f0e558b4d301d5677c5a2c433c3a64b"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115601334216"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38486461563/job/115601334216"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-13T23:48:58Z",
                "started_at": "2026-10-13T23:48:59Z",
                "completed_at": "2026-10-13T23:51:13Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-13T23:49:00Z",
                        "completed_at": "2026-10-13T23:49:01Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-13T23:49:01Z",
                        "completed_at": "2026-10-13T23:49:02Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-13T23:49:02Z",
                        "completed_at": "2026-10-13T23:49:02Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-13T23:49:02Z",
                        "completed_at": "2026-10-13T23:49:02Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-13T23:49:02Z",
                        "completed_at": "2026-10-13T23:51:11Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-13T23:51:11Z",
                        "completed_at": "2026-10-13T23:51:11Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-13T23:51:11Z",
                        "completed_at": "2026-10-13T23:51:12Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-13T23:51:12Z",
                        "completed_at": "2026-10-13T23:51:12Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-13T23:51:12Z",
                        "completed_at": "2026-10-13T23:51:12Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115601334216"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002090,
                "runner_name": "GitHub Actions 1000002090",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38531456515:1": {
        "attempt": 1,
        "head": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115744074831,
                "run_id": 38531456515,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38531456515"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa8uJsTw",
                "head_sha": (
                    "02792bda730b08eeb1eda86a2cd71af80af3cef7"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115744074831"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38531456515/job/115744074831"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T07:54:42Z",
                "started_at": "2026-10-14T07:54:44Z",
                "completed_at": "2026-10-14T07:57:02Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T07:54:45Z",
                        "completed_at": "2026-10-14T07:54:46Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T07:54:46Z",
                        "completed_at": "2026-10-14T07:54:49Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T07:54:49Z",
                        "completed_at": "2026-10-14T07:54:49Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T07:54:49Z",
                        "completed_at": "2026-10-14T07:54:50Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T07:54:50Z",
                        "completed_at": "2026-10-14T07:56:58Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-14T07:56:58Z",
                        "completed_at": "2026-10-14T07:56:59Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-14T07:56:59Z",
                        "completed_at": "2026-10-14T07:56:59Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T07:56:59Z",
                        "completed_at": "2026-10-14T07:56:59Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T07:56:59Z",
                        "completed_at": "2026-10-14T07:56:59Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115744074831"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002123,
                "runner_name": "GitHub Actions 1000002123",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38536227269:1": {
        "attempt": 1,
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115773491742,
                "run_id": 38536227269,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38536227269"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa9KNKHg",
                "head_sha": (
                    "92235b48ad33b9fa377e0736b689010173d7bfab"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115773491742"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38536227269/job/115773491742"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T09:10:36Z",
                "started_at": "2026-10-14T09:10:37Z",
                "completed_at": "2026-10-14T09:12:52Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T09:10:38Z",
                        "completed_at": "2026-10-14T09:10:39Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T09:10:39Z",
                        "completed_at": "2026-10-14T09:10:40Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T09:10:40Z",
                        "completed_at": "2026-10-14T09:10:40Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T09:10:40Z",
                        "completed_at": "2026-10-14T09:10:41Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T09:10:41Z",
                        "completed_at": "2026-10-14T09:12:50Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-14T09:12:50Z",
                        "completed_at": "2026-10-14T09:12:50Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-14T09:12:50Z",
                        "completed_at": "2026-10-14T09:12:50Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T09:12:50Z",
                        "completed_at": "2026-10-14T09:12:50Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T09:12:50Z",
                        "completed_at": "2026-10-14T09:12:50Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115773491742"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002157,
                "runner_name": "GitHub Actions 1000002157",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38576100625:1": {
        "attempt": 1,
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "conclusion": "success",
        "jobs": [
            {
                "id": 115893488738,
                "run_id": 38576100625,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38576100625"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAa-8pMYg",
                "head_sha": (
                    "92235b48ad33b9fa377e0736b689010173d7bfab"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/115893488738"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38576100625/job/115893488738"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T13:56:33Z",
                "started_at": "2026-10-14T13:56:35Z",
                "completed_at": "2026-10-14T13:58:51Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T13:56:36Z",
                        "completed_at": "2026-10-14T13:56:37Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T13:56:37Z",
                        "completed_at": "2026-10-14T13:56:38Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T13:56:38Z",
                        "completed_at": "2026-10-14T13:56:38Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T13:56:38Z",
                        "completed_at": "2026-10-14T13:56:39Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T13:56:39Z",
                        "completed_at": "2026-10-14T13:58:48Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-14T13:58:48Z",
                        "completed_at": "2026-10-14T13:58:49Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-14T13:58:49Z",
                        "completed_at": "2026-10-14T13:58:49Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T13:58:49Z",
                        "completed_at": "2026-10-14T13:58:49Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T13:58:49Z",
                        "completed_at": "2026-10-14T13:58:49Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/115893488738"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002182,
                "runner_name": "GitHub Actions 1000002182",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38618230338:1": {
        "attempt": 1,
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "conclusion": "success",
        "jobs": [
            {
                "id": 116038723427,
                "run_id": 38618230338,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38618230338"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbBHJnYw",
                "head_sha": (
                    "92235b48ad33b9fa377e0736b689010173d7bfab"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116038723427"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38618230338/job/116038723427"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T18:48:05Z",
                "started_at": "2026-10-14T18:48:07Z",
                "completed_at": "2026-10-14T18:50:23Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T18:48:08Z",
                        "completed_at": "2026-10-14T18:48:09Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T18:48:09Z",
                        "completed_at": "2026-10-14T18:48:11Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T18:48:11Z",
                        "completed_at": "2026-10-14T18:48:11Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T18:48:11Z",
                        "completed_at": "2026-10-14T18:48:12Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T18:48:12Z",
                        "completed_at": "2026-10-14T18:50:20Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-14T18:50:20Z",
                        "completed_at": "2026-10-14T18:50:21Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-14T18:50:21Z",
                        "completed_at": "2026-10-14T18:50:21Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T18:50:21Z",
                        "completed_at": "2026-10-14T18:50:21Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T18:50:21Z",
                        "completed_at": "2026-10-14T18:50:21Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116038723427"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002206,
                "runner_name": "GitHub Actions 1000002206",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38653596696:1": {
        "attempt": 1,
        "head": "e1a68725fe923582b5640481e29c7166eb77397c",
        "conclusion": "success",
        "jobs": [
            {
                "id": 116158007089,
                "run_id": 38653596696,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38653596696"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbC46HMQ",
                "head_sha": (
                    "e1a68725fe923582b5640481e29c7166eb77397c"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116158007089"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38653596696/job/116158007089"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-14T23:21:50Z",
                "started_at": "2026-10-14T23:21:52Z",
                "completed_at": "2026-10-14T23:24:09Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-14T23:21:53Z",
                        "completed_at": "2026-10-14T23:21:54Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-14T23:21:54Z",
                        "completed_at": "2026-10-14T23:21:56Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-14T23:21:56Z",
                        "completed_at": "2026-10-14T23:21:56Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-14T23:21:56Z",
                        "completed_at": "2026-10-14T23:21:57Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-14T23:21:57Z",
                        "completed_at": "2026-10-14T23:24:06Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-14T23:24:06Z",
                        "completed_at": "2026-10-14T23:24:06Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-14T23:24:06Z",
                        "completed_at": "2026-10-14T23:24:06Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-14T23:24:06Z",
                        "completed_at": "2026-10-14T23:24:06Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-14T23:24:06Z",
                        "completed_at": "2026-10-14T23:24:07Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116158007089"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002258,
                "runner_name": "GitHub Actions 1000002258",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38684236005:1": {
        "attempt": 1,
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "conclusion": "success",
        "jobs": [
            {
                "id": 116264890988,
                "run_id": 38684236005,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38684236005"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbEe1ybA",
                "head_sha": (
                    "9b97e13c2e2a56ee77700e40202eb6acd2212cf5"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116264890988"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38684236005/job/116264890988"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T05:31:51Z",
                "started_at": "2026-10-15T05:31:53Z",
                "completed_at": "2026-10-15T05:34:10Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T05:31:54Z",
                        "completed_at": "2026-10-15T05:31:55Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T05:31:55Z",
                        "completed_at": "2026-10-15T05:31:56Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T05:31:56Z",
                        "completed_at": "2026-10-15T05:31:56Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T05:31:56Z",
                        "completed_at": "2026-10-15T05:31:57Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T05:31:57Z",
                        "completed_at": "2026-10-15T05:34:06Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-15T05:34:06Z",
                        "completed_at": "2026-10-15T05:34:07Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-15T05:34:07Z",
                        "completed_at": "2026-10-15T05:34:07Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T05:34:07Z",
                        "completed_at": "2026-10-15T05:34:07Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T05:34:07Z",
                        "completed_at": "2026-10-15T05:34:07Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116264890988"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002302,
                "runner_name": "GitHub Actions 1000002302",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38700185196:1": {
        "attempt": 1,
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "conclusion": "success",
        "jobs": [
            {
                "id": 116308768429,
                "run_id": 38700185196,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38700185196"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbFIr2rQ",
                "head_sha": (
                    "9b97e13c2e2a56ee77700e40202eb6acd2212cf5"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116308768429"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38700185196/job/116308768429"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T07:46:01Z",
                "started_at": "2026-10-15T07:46:03Z",
                "completed_at": "2026-10-15T07:48:16Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T07:46:03Z",
                        "completed_at": "2026-10-15T07:46:04Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T07:46:04Z",
                        "completed_at": "2026-10-15T07:46:05Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T07:46:05Z",
                        "completed_at": "2026-10-15T07:46:05Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T07:46:05Z",
                        "completed_at": "2026-10-15T07:46:06Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T07:46:06Z",
                        "completed_at": "2026-10-15T07:48:14Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-15T07:48:14Z",
                        "completed_at": "2026-10-15T07:48:14Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-15T07:48:14Z",
                        "completed_at": "2026-10-15T07:48:14Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T07:48:14Z",
                        "completed_at": "2026-10-15T07:48:15Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T07:48:15Z",
                        "completed_at": "2026-10-15T07:48:15Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116308768429"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002327,
                "runner_name": "GitHub Actions 1000002327",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38720083768:1": {
        "attempt": 1,
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "conclusion": "success",
        "jobs": [
            {
                "id": 116379711116,
                "run_id": 38720083768,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38720083768"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbGMV2jA",
                "head_sha": (
                    "a62a88f5cad62f4fb1ef5230358557d37d2b2bba"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116379711116"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38720083768/job/116379711116"
                ),
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-10-15T10:43:58Z",
                "started_at": "2026-10-15T10:44:00Z",
                "completed_at": "2026-10-15T10:47:05Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T10:44:01Z",
                        "completed_at": "2026-10-15T10:44:02Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T10:44:02Z",
                        "completed_at": "2026-10-15T10:44:04Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T10:44:04Z",
                        "completed_at": "2026-10-15T10:44:04Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T10:44:04Z",
                        "completed_at": "2026-10-15T10:44:04Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 5,
                        "started_at": "2026-10-15T10:44:04Z",
                        "completed_at": "2026-10-15T10:47:02Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-15T10:47:02Z",
                        "completed_at": "2026-10-15T10:47:02Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 9,
                        "started_at": "2026-10-15T10:47:02Z",
                        "completed_at": "2026-10-15T10:47:02Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T10:47:02Z",
                        "completed_at": "2026-10-15T10:47:02Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T10:47:02Z",
                        "completed_at": "2026-10-15T10:47:03Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116379711116"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002393,
                "runner_name": "GitHub Actions 1000002393",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38721259225:1": {
        "attempt": 1,
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "conclusion": "failure",
        "jobs": [
            {
                "id": 116376105413,
                "run_id": 38721259225,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38721259225"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbGI5xxQ",
                "head_sha": (
                    "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116376105413"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38721259225/job/116376105413"
                ),
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-10-15T10:34:44Z",
                "started_at": "2026-10-15T10:34:46Z",
                "completed_at": "2026-10-15T10:41:26Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T10:34:47Z",
                        "completed_at": "2026-10-15T10:34:48Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T10:34:48Z",
                        "completed_at": "2026-10-15T10:34:49Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T10:34:49Z",
                        "completed_at": "2026-10-15T10:34:49Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T10:34:49Z",
                        "completed_at": "2026-10-15T10:34:50Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "failure",
                        "number": 5,
                        "started_at": "2026-10-15T10:34:50Z",
                        "completed_at": "2026-10-15T10:41:23Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-15T10:41:23Z",
                        "completed_at": "2026-10-15T10:41:23Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "skipped",
                        "number": 9,
                        "started_at": "2026-10-15T10:41:23Z",
                        "completed_at": "2026-10-15T10:41:23Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T10:41:23Z",
                        "completed_at": "2026-10-15T10:41:23Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T10:41:23Z",
                        "completed_at": "2026-10-15T10:41:23Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116376105413"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002362,
                "runner_name": "GitHub Actions 1000002362",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
        ],
    },
    "38763419182:1": {
        "attempt": 1,
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "conclusion": "failure",
        "jobs": [
            {
                "id": 116520330619,
                "run_id": 38763419182,
                "workflow_name": "fetch-dk-study-in-denmark",
                "head_branch": "main",
                "run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/runs/38763419182"
                ),
                "run_attempt": 1,
                "node_id": "CR_kwDORVslOM8AAAAbISclew",
                "head_sha": (
                    "f77eed642c4544bbeed009440c406064a4f5c6ad"
                ),
                "url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/a"
                    "ctions/jobs/116520330619"
                ),
                "html_url": (
                    "https://github.com/YouthOpps/data-pipeline/actions/run"
                    "s/38763419182/job/116520330619"
                ),
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-10-15T15:47:28Z",
                "started_at": "2026-10-15T15:47:31Z",
                "completed_at": "2026-10-15T15:54:12Z",
                "name": "collect",
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 1,
                        "started_at": "2026-10-15T15:47:32Z",
                        "completed_at": "2026-10-15T15:47:34Z",
                    },
                    {
                        "name": "Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 2,
                        "started_at": "2026-10-15T15:47:34Z",
                        "completed_at": "2026-10-15T15:47:35Z",
                    },
                    {
                        "name": "Run actions/setup-python@v5",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 3,
                        "started_at": "2026-10-15T15:47:35Z",
                        "completed_at": "2026-10-15T15:47:35Z",
                    },
                    {
                        "name": (
                            "Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 4,
                        "started_at": "2026-10-15T15:47:35Z",
                        "completed_at": "2026-10-15T15:47:36Z",
                    },
                    {
                        "name": (
                            "Collect and publish this source"
                        ),
                        "status": "completed",
                        "conclusion": "failure",
                        "number": 5,
                        "started_at": "2026-10-15T15:47:36Z",
                        "completed_at": "2026-10-15T15:54:09Z",
                    },
                    {
                        "name": (
                            "Post Create data-source publication token"
                        ),
                        "status": "completed",
                        "conclusion": "success",
                        "number": 8,
                        "started_at": "2026-10-15T15:54:09Z",
                        "completed_at": "2026-10-15T15:54:09Z",
                    },
                    {
                        "name": (
                            "Post Run actions/setup-python@v5"
                        ),
                        "status": "completed",
                        "conclusion": "skipped",
                        "number": 9,
                        "started_at": "2026-10-15T15:54:09Z",
                        "completed_at": "2026-10-15T15:54:09Z",
                    },
                    {
                        "name": "Post Run actions/checkout@v4",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 10,
                        "started_at": "2026-10-15T15:54:09Z",
                        "completed_at": "2026-10-15T15:54:10Z",
                    },
                    {
                        "name": "Complete job",
                        "status": "completed",
                        "conclusion": "success",
                        "number": 11,
                        "started_at": "2026-10-15T15:54:10Z",
                        "completed_at": "2026-10-15T15:54:10Z",
                    },
                ],
                "check_run_url": (
                    "https://api.github.com/repos/YouthOpps/data-pipeline/c"
                    "heck-runs/116520330619"
                ),
                "labels": [
                    "ubuntu-latest",
                ],
                "runner_id": 1000002515,
                "runner_name": "GitHub Actions 1000002515",
                "runner_group_id": 0,
                "runner_group_name": "GitHub Actions",
            },
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
