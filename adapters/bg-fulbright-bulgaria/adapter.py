"""Discover and extract official Fulbright Bulgaria awards and training."""

import argparse
import base64
import contextlib
import copy
import errno
import functools
import io
import math
import socket
import ssl
import stat
import zipfile
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
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_ID = "bg-fulbright-bulgaria"
SOURCE_URL = "https://www.fulbright.bg/en/research-grants-for-bulgarians/"
WEBSITE_URL = "https://www.fulbright.bg/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "BG"
PUBLISHER_TYPE = "government"
ATTRIBUTION = (
    "Fulbright Bulgaria — Bulgarian-American Commission for "
    "Educational Exchange"
)


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            wait = _PAGE_COMPLETED + 30 - time.monotonic()
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


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
            class_name is None
            or class_name in n.attrs.get("class", "").split()
        )
    ]


def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]


def original_page(url):
    document = fetch_source(url, min_interval=check_robots(url))
    if not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$",
        document,
        re.IGNORECASE,
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    root = PublisherHTML(document).root
    return root



def article(root):
    return one(
        [n for n in nodes(root, "div") if n.attrs.get("id") == "main-content"],
        "complete public Fulbright Bulgaria article",
    )


def reviewed_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower()
        not in ("fulbright.bg", "www.fulbright.bg")
        or parsed.username
        or parsed.password
        or parsed.port
        or (parsed.query and parsed.query != "et_blog")
    ):
        raise AdapterError("Unexpected source URL", "parse")
    return urllib.parse.urlunsplit(
        ("https", "www.fulbright.bg", parsed.path or "/", parsed.query, "")
    )


PAGES = {
    "agile": (
        "https://www.fulbright.bg/en/"
        "agile-certified-practitioner-pmi-acp-training/"
    ),
    "alumni": "https://www.fulbright.bg/en/alumni-grants/",
    "conference2": (
        "https://www.fulbright.bg/en/conferences-events/page/2/?et_blog"
    ),
    "conferences": "https://www.fulbright.bg/en/conferences-events/",
    "court": "https://www.fulbright.bg/sofijski-rajonen-sad/",
    "digital2020": (
        "https://www.fulbright.bg/en/digital-presence-online-conference/"
    ),
    "doctoral": "https://www.fulbright.bg/en/non-degree-research-programs-bg/",
    "entry": "https://www.fulbright.bg/en/research-grants-for-bulgarians/",
    "eta-host": "https://www.fulbright.bg/programa-za-uchiteli-po-anglijski/",
    "eta": "https://www.fulbright.bg/en/english-teaching-assistantship/",
    "fis2019": (
        "https://www.fulbright.bg/en/fulbright-international-seminar-2019/"
    ),
    "fis2021": (
        "https://www.fulbright.bg/en/fulbright-international-seminar-2021/"
    ),
    "fis2022-review": "https://www.fulbright.bg/en/fis-2022-round-up/",
    "fis2022": (
        "https://www.fulbright.bg/en/fulbright-international-seminar-2022/"
    ),
    "ftea": "https://www.fulbright.bg/en/grants-for-teachers/",
    "graduate-bg": "https://www.fulbright.bg/en/graduate-students-bg/",
    "graduate-us": "https://www.fulbright.bg/en/graduate-students-usa-3/",
    "home": "https://www.fulbright.bg/en/",
    "humphrey": "https://www.fulbright.bg/en/hubert-humphrey/",
    "incoming": "https://www.fulbright.bg/en/grants-for-us-citizens/",
    "info-sessions": (
        "https://www.fulbright.bg/en/info-sessions-scholar-phd-grants/"
    ),
    "journalists": "https://www.fulbright.bg/zhurnalisti/",
    "law": "https://www.fulbright.bg/en/pravo/",
    "leaders": "https://www.fulbright.bg/en/fulbright-bulgarian-leaders/",
    "math": "https://www.fulbright.bg/en/math-scholars/",
    "mba-alias": "https://www.fulbright.bg/en/biznes-administraciq/",
    "media2023": "https://www.fulbright.bg/en/eta-medialit-seminar-2023/",
    "news": "https://www.fulbright.bg/en/news-and-events/",
    "ngo": "https://www.fulbright.bg/npo-research/",
    "open2023": "https://www.fulbright.bg/en/open-lectures-2023/",
    "open2024": "https://www.fulbright.bg/en/open-lectures-2024/",
    "other": "https://www.fulbright.bg/en/other/",
    "outgoing": "https://www.fulbright.bg/en/grants-for-bulgarian-citizens/",
    "pmp": (
        "https://www.fulbright.bg/en/project-management-professional-pmp-capm/"
    ),
    "privacy": "https://www.fulbright.bg/en/privacy-policy/",
    "scholar-us": (
        "https://www.fulbright.bg/en/grants-for-us-scholars-and-professionals/"
    ),
    "scholar": "https://www.fulbright.bg/en/visiting-scholar-grants-research/",
    "schuman": "https://www.fulbright.bg/en/fulbright-schuman/",
    "specialist": "https://www.fulbright.bg/en/fulbright-specialist-program/",
    "thanks": "https://www.fulbright.bg/en/thanks-to-scandinavia/",
    "opportunity-funds": (
        "https://www.fulbright.bg/en/opportunity-fund-bulgaria/"
    ),
    "group-advising": (
        "https://www.fulbright.bg/en/"
        "educationusa-virtual-group-advising-program/"
    ),
    "privacy-bg": "https://www.fulbright.bg/privacy-policy/",
}

CANONICALS = {
    "agile": [
        "https://www.fulbright.bg/en/"
        "agile-certified-practitioner-pmi-acp-training/"
    ],
    "alumni": ["https://www.fulbright.bg/en/alumni-grants/"],
    "conference2": ["https://www.fulbright.bg/en/conferences-events/"],
    "conferences": ["https://www.fulbright.bg/en/conferences-events/"],
    "court": ["https://www.fulbright.bg/sofijski-rajonen-sad/"],
    "digital2020": [
        "https://www.fulbright.bg/en/digital-presence-online-conference/"
    ],
    "doctoral": [
        "https://www.fulbright.bg/en/non-degree-research-programs-bg/"
    ],
    "entry": ["https://www.fulbright.bg/en/research-grants-for-bulgarians/"],
    "eta-host": [
        "https://www.fulbright.bg/programa-za-uchiteli-po-anglijski/"
    ],
    "eta": ["https://www.fulbright.bg/en/english-teaching-assistantship/"],
    "fis2019": [],
    "fis2021": [
        "https://www.fulbright.bg/en/fulbright-international-seminar-2021/"
    ],
    "fis2022-review": ["https://www.fulbright.bg/en/fis-2022-round-up/"],
    "fis2022": [
        "https://www.fulbright.bg/en/fulbright-international-seminar-2022/"
    ],
    "ftea": ["https://www.fulbright.bg/en/grants-for-teachers/"],
    "graduate-bg": ["https://www.fulbright.bg/en/graduate-students-bg/"],
    "graduate-us": ["https://www.fulbright.bg/en/graduate-students-usa-3/"],
    "home": ["https://www.fulbright.bg/en"],
    "humphrey": ["https://fulbright.bg/en"],
    "incoming": ["https://www.fulbright.bg/en/grants-for-us-citizens/"],
    "info-sessions": [
        "https://www.fulbright.bg/en/info-sessions-scholar-phd-grants/"
    ],
    "journalists": ["https://www.fulbright.bg/zhurnalisti/"],
    "law": ["https://www.fulbright.bg/en/pravo/"],
    "leaders": ["https://www.fulbright.bg/en/fulbright-bulgarian-leaders/"],
    "math": ["https://www.fulbright.bg/en/math-scholars/"],
    "mba-alias": ["https://www.fulbright.bg/en/biznes-administraciq/"],
    "media2023": ["https://www.fulbright.bg/en/eta-medialit-seminar-2023/"],
    "news": ["https://www.fulbright.bg/en/news-and-events/"],
    "ngo": ["https://www.fulbright.bg/npo-research/"],
    "open2023": ["https://www.fulbright.bg/en/open-lectures-2023/"],
    "open2024": ["https://www.fulbright.bg/en/open-lectures-2024/"],
    "other": ["https://www.fulbright.bg/en/other/"],
    "outgoing": ["https://www.fulbright.bg/en/grants-for-bulgarian-citizens/"],
    "pmp": [
        "https://www.fulbright.bg/en/project-management-professional-pmp-capm/"
    ],
    "privacy": ["https://www.fulbright.bg/en/privacy-policy/"],
    "scholar-us": [
        "https://www.fulbright.bg/en/grants-for-us-scholars-and-professionals/"
    ],
    "scholar": [
        "https://www.fulbright.bg/en/visiting-scholar-grants-research/"
    ],
    "schuman": ["https://www.fulbright.bg/en/fulbright-schuman/"],
    "specialist": [
        "https://www.fulbright.bg/en/fulbright-specialist-program/"
    ],
    "thanks": ["https://www.fulbright.bg/en/thanks-to-scandinavia/"],
    "opportunity-funds": [
        "https://www.fulbright.bg/en/opportunity-fund-bulgaria/"
    ],
    "group-advising": [
        "https://www.fulbright.bg/en/"
        "educationusa-virtual-group-advising-program/"
    ],
    "privacy-bg": ["https://www.fulbright.bg/privacy-policy/"],
}

TRACKS = [
    {
        "track": "graduate-study",
        "key": "graduate-bg",
        "title": "Fulbright graduate study grants for Bulgarians",
        "categories": ["scholarships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "doctoral-research",
        "key": "doctoral",
        "title": "Fulbright doctoral research grants",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "visiting-scholar",
        "key": "scholar",
        "title": "Fulbright visiting scholar grants",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "bulgarian-leaders",
        "key": "leaders",
        "title": "Fulbright Bulgarian Leaders awards",
        "categories": ["scholarships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "humphrey-fellowship",
        "key": "humphrey",
        "title": "Hubert H. Humphrey fellowships",
        "categories": ["fellowships", "training"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "thanks-to-scandinavia",
        "key": "thanks",
        "title": "Thanks to Scandinavia graduate awards",
        "categories": ["scholarships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "fordham-law",
        "key": "law",
        "title": "Fulbright–Fordham law tuition scholarship",
        "categories": ["scholarships"],
        "hosts": ["US"],
        "kind": "programme-overview",
        "language": "bg",
    },
    {
        "track": "mathematics-research",
        "key": "math",
        "title": "Fulbright mathematics research scholarship",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "court-internship",
        "key": "court",
        "title": "Fulbright Sofia Regional Court overseas internship",
        "categories": ["internships"],
        "hosts": ["US"],
        "kind": "programme-overview",
        "language": "bg",
    },
    {
        "track": "teacher-excellence-achievement",
        "key": "ftea",
        "title": "Fulbright Teaching Excellence and Achievement",
        "categories": ["training", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "schuman-pre-doctoral",
        "key": "schuman",
        "title": "Fulbright-Schuman predoctoral awards — historical2022 call",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "schuman-postdoctoral",
        "key": "schuman",
        "title": "Fulbright-Schuman postdoctoral awards — historical2022 call",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "schuman-innovation",
        "key": "schuman",
        "title": "Fulbright-Schuman Innovation awards — historical2022 call",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "schuman-international-educator",
        "key": "schuman",
        "title": (
            "Fulbright-Schuman international educator awards — historical2022"
            " call"
        ),
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "english-teaching-assistant",
        "key": "eta",
        "title": "Fulbright/ABF English Teaching Assistantship",
        "categories": ["fellowships", "training"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "eta-host-institution",
        "key": "eta-host",
        "title": "Fulbright/ABF English assistant host-institution programme",
        "categories": ["grants", "training"],
        "hosts": ["BG"],
        "kind": "institutional-grant",
        "language": "bg",
    },
    {
        "track": "us-graduate-study",
        "key": "graduate-us",
        "title": "Fulbright US graduate study/research — archived overview",
        "categories": ["scholarships", "grants"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "us-graduate-bg-romania",
        "key": "graduate-us",
        "title": (
            "Fulbright Bulgaria–Romania graduate award — archived overview"
        ),
        "categories": ["scholarships", "grants"],
        "hosts": ["BG", "RO"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "us-graduate-bg-greece",
        "key": "graduate-us",
        "title": (
            "Fulbright Bulgaria–Greece graduate award — archived overview"
        ),
        "categories": ["scholarships", "grants"],
        "hosts": ["BG", "GR"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "us-graduate-archaeology",
        "key": "graduate-us",
        "title": (
            "Fulbright/Balkan Heritage archaeology award — archived overview"
        ),
        "categories": ["scholarships", "grants", "training"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "us-scholar-core",
        "key": "scholar-us",
        "title": "Fulbright US scholar and professional grants",
        "categories": ["grants", "fellowships"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "us-scholar-flex",
        "key": "scholar-us",
        "title": "Fulbright US scholar Flex grants",
        "categories": ["grants", "fellowships"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "specialist-host-project",
        "key": "specialist",
        "title": "Fulbright Specialist institutional host projects",
        "categories": ["grants", "training"],
        "hosts": ["BG"],
        "kind": "institutional-grant",
        "language": "en",
    },
    {
        "track": "specialist-us-professional",
        "key": "specialist",
        "title": "Fulbright Specialist US professional assignment overview",
        "categories": ["fellowships", "training"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "alumni-travel",
        "key": "alumni",
        "title": "Fulbright Bulgaria alumni travel awards",
        "categories": ["grants"],
        "hosts": [],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "alumni-publication",
        "key": "alumni",
        "title": "Fulbright Bulgaria alumni publication awards",
        "categories": ["grants"],
        "hosts": ["BG"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "alumni-open-project",
        "key": "alumni",
        "title": "Fulbright Bulgaria alumni open project awards",
        "categories": ["grants"],
        "hosts": ["BG"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "pmp-course-2026-september",
        "key": "pmp",
        "title": "PMP/CAPM course — September/October2026 edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "pmp-course-2026-october",
        "key": "pmp",
        "title": "PMP/CAPM course — October/November2026 edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "agile-course-2023-october",
        "key": "agile",
        "title": "PMI-ACP preparation — October2023 completed edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "agile-course-2024-february",
        "key": "agile",
        "title": "PMI-ACP preparation — February2024 fully booked edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "civil-society-research",
        "key": "ngo",
        "title": "Fulbright civil-society research grants",
        "categories": ["grants", "fellowships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "bg",
    },
    {
        "track": "oklahoma-mba",
        "key": "mba-alias",
        "title": (
            "Fulbright/University of Oklahoma MBA — historical2025–26 call"
        ),
        "categories": ["scholarships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "journalist-training",
        "key": "journalists",
        "title": "Fulbright explanatory-journalism training overview",
        "categories": ["training", "fellowships"],
        "hosts": ["US"],
        "kind": "programme-overview",
        "language": "bg",
    },
    {
        "track": "international-seminar-2021",
        "key": "fis2021",
        "title": "Fulbright International Seminar2021 — historical edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "international-seminar-2022",
        "key": "fis2022",
        "title": "Fulbright International Seminar2022 — historical edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "programme-overview",
        "language": "en",
    },
    {
        "track": "international-seminar-2019",
        "key": "fis2019",
        "title": "Fulbright International Seminar2019 — historical edition",
        "categories": ["training"],
        "hosts": ["BG"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "opportunity-funds",
        "key": "opportunity-funds",
        "title": "Opportunity Funds Bulgaria application-preparation awards",
        "categories": ["grants", "scholarships"],
        "hosts": ["US"],
        "kind": "opportunity",
        "language": "en",
    },
    {
        "track": "educationusa-group-advising",
        "key": "group-advising",
        "title": "EducationUSA virtual group advising programme",
        "categories": ["training"],
        "hosts": [],
        "kind": "programme-overview",
        "language": "en",
    },
]

POLICY_DIGESTS = {
    "privacy": (
        "0a5ac0b9d39f53470fb124a858a42d727d9f5f81787c75b2fe82232fc37ac52b"
    ),
    "privacy-bg": (
        "f41c43225d36e9326377b5550f3c78e355e08f4b056f09ea5a97dbe86bb407c7"
    ),
}

# Implementation fragment inserted into this source's standalone adapter.

LEGAL_FOOTERS = {
    "en": (
        "Bulgarian-American Commission for Educational Exchange Fulbright ©"
        " 1993-CURRENT_YEAR"
    ),
    "bg": (
        "Българо-американска комисия за образователен обмен Фулбрайт ©"
        " 1993-CURRENT_YEAR"
    ),
}
POLICY_URLS = {
    "https://www.fulbright.bg/en/privacy-policy/",
    "https://www.fulbright.bg/privacy-policy/",
}


def script_links(root):
    """Read inert Divi navigation JSON without executing JavaScript."""
    result = {}
    for script in nodes(root, "script"):
        text = "".join(c for c in script.children if isinstance(c, str))
        matches = re.findall(
            r"var et_link_options_data\s*=\s*(\[.*?\]);", text
        )
        for match in matches:
            try:
                options = json.loads(match)
            except ValueError as error:
                raise AdapterError(
                    "Invalid publisher card navigation", "parse"
                ) from error
            for option in options:
                if (
                    not isinstance(option, dict)
                    or set(option) != {"class", "url", "target"}
                    or not all(isinstance(v, str) for v in option.values())
                    or option["class"] in result
                ):
                    raise AdapterError(
                        "Ambiguous publisher card navigation", "parse"
                    )
                result[option["class"]] = option["url"]
    return result


def legal_facts(key, root):
    """Guard rights evidence independently of mutable opportunity content."""
    footer = one(
        [
            n
            for n in nodes(root, "div")
            if "et_pb_text_9_tb_footer" in n.attrs.get("class", "").split()
        ],
        "publisher copyright footer",
    )
    text = re.sub(r"©\s*1993[-–]\d{4}", "© 1993-CURRENT_YEAR", footer.text())
    language = (
        "bg"
        if key in ("court", "eta-host", "journalists", "ngo", "privacy-bg")
        else "en"
    )
    if text != LEGAL_FOOTERS[language]:
        raise AdapterError("Publisher rights footer changed: " + key, "access")
    policy = set()
    for anchor in nodes(root, "a"):
        href = anchor.attrs.get("href", "")
        if href.startswith(("mailto:", "tel:")):
            continue
        label = anchor.text()
        if re.search(
            r"privacy|terms|policy|reuse|usage|copyright|licen[cs]e|"
            r"политика|условия|авторск",
            href + " " + label,
            re.I,
        ):
            policy.add(urllib.parse.urljoin(PAGES[key], href))
    for class_name, href in script_links(root).items():
        if re.search(
            r"privacy|terms|policy|reuse|usage|copyright|licen[cs]e|"
            r"политика|условия",
            href,
            re.I,
        ):
            policy.add(urllib.parse.urljoin(PAGES[key], href))
    if not policy.issubset(POLICY_URLS):
        raise AdapterError(
            "Unreviewed publisher policy route: " + key, "access"
        )
    for section in nodes(root, "div"):
        if not any(
            "_tb_footer" in c and "section" in c
            for c in section.attrs.get("class", "").split()
        ):
            continue
        for paragraph in nodes(section, "p"):
            if re.search(
                r"re.?publi|permission|copyright|rights"
                r" reserved|licen[cs]e|reuse|reproduction|разрешение|авторск",
                paragraph.text(),
                re.I,
            ):
                raise AdapterError(
                    "Publisher rights notice changed: " + key, "access"
                )
    if key in POLICY_DIGESTS:
        material = (
            article(root).text().replace("\xad", "").replace("\u200b", "")
        )
        if (
            hashlib.sha256(material.encode()).hexdigest()
            != POLICY_DIGESTS[key]
        ):
            raise AdapterError(
                "Publisher privacy policy changed: " + key, "access"
            )
    observed = [
        n.attrs.get("href", "")
        for n in nodes(root, "link")
        if n.attrs.get("rel") == "canonical"
    ]
    if observed != CANONICALS[key]:
        raise AdapterError("Publisher canonical changed: " + key, "parse")


def modules(root):
    """Yield distinct content blocks, retaining their source order."""
    return [
        n
        for n in nodes(article(root), "div")
        if "et_pb_module" in n.attrs.get("class", "").split()
        and not any(
            c in n.attrs.get("class", "").split()
            for c in (
                "et_pb_accordion",
                "et_pb_slider",
                "et_pb_team_member",
                "et_pb_testimonial",
                "et_pb_gallery",
                "et_pb_image",
                "et_pb_video",
                "et_pb_code",
                "et_pb_blog",
            )
        )
    ]


def block(root, heading, optional=False):
    """Select one labelled fact block and its associated disclosure toggle."""
    items = modules(root)
    selected = []
    for i, item in enumerate(items):
        headings = [
            n.text()
            for n in item.walk()
            if n.tag in ("h1", "h2", "h3", "h4", "h5")
        ]
        if any(re.fullmatch(heading, value, re.I) for value in headings):
            text = item.text()
            if (
                i + 1 < len(items)
                and "et_pb_toggle"
                in items[i + 1].attrs.get("class", "").split()
                and "et_pb_accordion_item"
                not in items[i + 1].attrs.get("class", "").split()
            ):
                text += " " + items[i + 1].text()
            if not re.fullmatch(heading, text, re.I):
                selected.append(text)
    if not selected and optional:
        return ""
    return one(selected, "programme block: " + heading)


def matched(pattern, text, description, optional=False):
    values = list(re.finditer(pattern, text, re.I))
    if not values and optional:
        return None
    if len(values) != 1:
        raise AdapterError("Unsupported or ambiguous " + description, "parse")
    return values[0]


def primary(root):
    """Exclude related-programme sidebars from this programme's own facts."""
    text = article(root).text()
    return re.split(
        r"Other [Gg]rants|Other [Ss]cholarships|Други стипендии",
        text,
        maxsplit=1,
    )[0]


def amount(value):
    return f'{int(re.sub(r"[^0-9]", "", value)):,}'


_MONTHS = {
    name.lower(): number
    for number, name in enumerate(
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


def closing_date(value):
    """Parse a date from an explicitly selected application closing block."""
    value = " ".join(value.replace(",", " ").split())
    match = re.fullmatch(
        r"(\d{1,2})(?:st|nd|rd|th)? ([A-Za-z]+) (\d{4})", value, re.I
    )
    if match:
        day, month, year = match.groups()
    else:
        match = re.fullmatch(
            r"([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)? (\d{4})", value, re.I
        )
        if not match:
            raise AdapterError(
                "Unsupported complete application deadline", "parse"
            )
        month, day, year = match.groups()
    month_number = _MONTHS.get(month.lower())
    if month_number is None:
        raise AdapterError("Unsupported application deadline month", "parse")
    try:
        return datetime(int(year), month_number, int(day)).date().isoformat()
    except ValueError as error:
        raise AdapterError(
            "Invalid complete application deadline", "parse"
        ) from error


def deadline_for(key, root):
    """Use only the actual programme application slot, never dates in a hub."""
    own = primary(root)
    date = (
        r"((?:\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s*,?\s*\d{4})|"
        r"(?:[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*\d{4}))"
    )
    patterns = {
        "doctoral": r"Application deadline:\s*" + date,
        "scholar": r"Application deadline:\s*" + date,
        "thanks": r"DEADLINE:\s*" + date,
        "mba-alias": r"Application deadline:\s*" + date,
        "schuman": r"Application deadline:\s*" + date,
        "alumni": r"The next submission deadline is\s*" + date,
        "opportunity-funds": (
            r"Applications sent after the\s*" + date + r"\s*,?\s*deadline"
        ),
        "fis2021": r"Deadline for participant applications is\s*" + date,
    }
    if key == "ftea":
        values = [
            n.text()
            for n in nodes(article(root))
            if n.tag == "div"
            and "dsm-timeline-content" in n.attrs.get("class", "").split()
            and "Application deadline" in n.text()
        ]
        text = one(values, "teacher application deadline")
        value = matched(
            date + r"\s+Application deadline", text, "teacher closing date"
        )[1]
        return closing_date(value)
    if key not in patterns:
        # These supported programmes have absent or yearless application dates.
        # New dated closing slots are not silently accepted as a missing date.
        if re.search(
            r"(?:Application deadline|submission deadline|DEADLINE)\s*:\s*"
            + date,
            own,
            re.I,
        ):
            raise AdapterError("New unsupported closing slot: " + key, "parse")
        return None
    matches = list(re.finditer(patterns[key], own, re.I))
    if key in ("doctoral", "scholar"):
        matches.extend(
            re.finditer(
                r"Deadline for submitting applications for the academic"
                r" 20\d{2}[-–]\d{2} is\s*" + date,
                own,
                re.I,
            )
        )
    if key == "opportunity-funds":
        matches.extend(
            re.finditer(
                date + r"\s+Deadline for submitting full application",
                own,
                re.I,
            )
        )
    if key in (
        "schuman",
        "mba-alias",
        "doctoral",
        "scholar",
        "opportunity-funds",
    ):
        values = {closing_date(m[1]) for m in matches}
        return one(list(values), key + " consistent application closing")
    return closing_date(one(matches, key + " application closing")[1])


def explicit_competition_closure(text):
    """Match an own application closure, never generic degree completion."""
    return re.search(
        r"(?:The\s+)?competition(?:\s+for\s+(?:AY|academic year)\s+"
        r"20\d{2}[-–]\d{2,4})?\s+is\s+(?:now\s+)?closed\b|"
        r"Applications\s+for\s+the\s+.{1,150}?"
        r"\s+are\s+currently\s+closed\b|"
        r"Конкурсът\s+е\s+(?:затворен|приключил)\b",
        text,
        re.I,
    )


def announced_future_reopening(text, checked):
    """Inspect the complete own body for dated reopening, not a short summary."""
    if re.search(
        r"(?:applications|competition)\s+(?:are|is)\s+(?:now\s+)?open\b",
        text,
        re.I,
    ):
        return True
    months = {**_MONTHS, "sept": 9}
    months.update({key[:3]: value for key, value in _MONTHS.items()})
    names = "|".join(sorted(months, key=len, reverse=True))
    for match in re.finditer(
        r"(?:will\s+(?:re)?open|(?:re)?opens?\s+(?:on|from)|"
        r"start\s+accepting\s+applications\s+(?:on|from)|"
        r"rescheduled\s+(?:to|for)|"
        r"(?:ще|щe)\s+отвори)[^.!?]{0,180}",
        text,
        re.I,
    ):
        window = match[0]
        if not re.search(r"\b20\d{2}\b", window):
            # An explicit reopening without a complete year is unresolved;
            # a generic email invitation to hear about a next call is not
            # matched by this dated/announced reopening grammar.
            return True
        parsed_dates = False
        for date in re.finditer(
            r"(?:\b(\d{1,2})\s+(" + names + r")\.?\s*,?\s*(20\d{2})|"
            r"\b(" + names + r")\.?\s+(\d{1,2})\s*,?\s*(20\d{2}))",
            window,
            re.I,
        ):
            day, month, year = (
                date.group(1, 2, 3) if date[1] else (date[5], date[4], date[6])
            )
            try:
                opening = datetime(int(year), months[month.lower()], int(day))
            except (ValueError, KeyError):
                return True
            parsed_dates = True
            if (
                opening.date()
                >= checked.astimezone(timezone(timedelta(hours=-12))).date()
            ):
                return True
        # A possibly future year in an otherwise unsupported announced
        # opening is unresolved; do not invent its month/day precision.
        if not parsed_dates:
            years = re.findall(r"\b20\d{2}\b", window)
            if any(int(year) >= checked.year for year in years):
                return True
    return False


def course_schedule_end(card):
    """Read the last actual calendar day from a complete explicit schedule."""
    match = re.search(r"\b\d+\s+days:\s*(.+?\b20\d{2})\b", card, re.I)
    if not match:
        return None
    schedule = match[1]
    years = re.findall(r"\b20\d{2}\b", schedule)
    if len(years) != 1:
        return None
    year = int(years[0])
    body = schedule[: schedule.rfind(years[0])].strip(" ,")
    months = {**_MONTHS, "sept": 9}
    months.update({key[:3]: value for key, value in _MONTHS.items()})
    names = "|".join(sorted(months, key=len, reverse=True))
    dates = []
    rest = body
    for part in list(
        re.finditer(
            r"\b(" + names + r")\.?\s+(\d{1,2}(?:\s*[-–,]\s*\d{1,2})*)",
            body,
            re.I,
        )
    ):
        try:
            dates.extend(
                datetime(year, months[part[1].lower()], int(day)).date()
                for day in re.findall(r"\d+", part[2])
            )
        except (ValueError, KeyError):
            return None
        rest = rest.replace(part[0], "", 1)
    if not dates:
        part = re.fullmatch(
            r"(\d{1,2}(?:\s*[-–,]\s*\d{1,2})*)\s+(" + names + r")\.?",
            body,
            re.I,
        )
        if not part:
            return None
        try:
            dates = [
                datetime(year, months[part[2].lower()], int(day)).date()
                for day in re.findall(r"\d+", part[1])
            ]
        except (ValueError, KeyError):
            return None
        rest = ""
    if rest.strip(" ,"):
        return None
    return max(dates), match[0]


def own_course_end(track, root):
    """Bind an explicit schedule to this record's own complete edition card."""
    return course_schedule_end(course_card(track, root).text())


def verified_own_closure_evidence(track, root, checked_at):
    """Produce one bounded factual own-closure proof without new record fields."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Closure proof requires an aware clock", "validate")
    text = primary(root)
    if announced_future_reopening(text, checked):
        return None
    url = PAGES[track["key"]]
    if track["key"] in ("pmp", "agile"):
        result = own_course_end(track, root)
        if result is None:
            return None
        end, quote = result
        if checked.astimezone(timezone(timedelta(hours=-12))).date() <= end:
            return None
        return (
            f"Publisher dates this specific edition as ended on {end} ({url}): "
            + quote
        )
    heading = " ".join(n.text() for n in nodes(article(root), "h1"))
    if re.fullmatch(r"Fulbright International Seminar\s+20\d{2}", heading):
        year = int(re.search(r"20\d{2}", heading)[0])
        past = re.search(
            r"seminar that took place in Sofia,\s*Sept\.?\s*(\d{1,2})"
            r"[-–](\d{1,2})\.",
            text,
            re.I,
        )
        if past and re.search(r"\bWe hosted\b", text):
            try:
                end = datetime(year, 9, int(past[2])).date()
                start = datetime(year, 9, int(past[1])).date()
            except ValueError:
                return None
            if (
                start
                <= end
                < checked.astimezone(timezone(timedelta(hours=-12))).date()
            ):
                return (
                    f"Publisher dates this specific edition as ended on {end} "
                    f"({url}): {heading}; {past[0]} We hosted."
                )
    banner = " ".join(n.text() for n in modules(root)[:5])
    closure = explicit_competition_closure(banner)
    if closure and closure[0] in text:
        note = f"Publisher confirms this own competition is closed ({url}): "
        note += closure[0]
        return note if len(note) <= 600 else None
    return None


def verified_own_archival_evidence(track, root, checked_at):
    """Identify a specific past FIS edition from three agreeing own statements."""
    if not re.fullmatch(r"international-seminar-20\d{2}", track["track"]):
        return None
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Archival proof requires an aware clock", "validate")
    heading = " ".join(n.text() for n in nodes(article(root), "h1"))
    match = re.fullmatch(
        r"Fulbright International Seminar\s+(20\d{2})", heading
    )
    if not match:
        return None
    year = int(match[1])
    text = primary(root)
    invitations = list(
        re.finditer(r"participate in the\s+(20\d{2})\s+FIS\b", text, re.I)
    )
    purposes = list(re.finditer(r"\bFIS\s+(20\d{2})\s+enables\b", text))
    invitation_scopes = re.findall(
        r"participate in the\s+([^.;]{1,40}?)\s+FIS\b", text, re.I
    )
    purpose_scopes = re.findall(r"\bFIS\s+([^.;]{1,40}?)\s+enables\b", text)
    if (
        set(invitation_scopes) != {str(year)}
        or set(purpose_scopes) != {str(year)}
        or {int(x[1]) for x in invitations} != {year}
        or {int(x[1]) for x in purposes} != {year}
        or year >= checked.astimezone(timezone(timedelta(hours=-12))).year
        or announced_future_reopening(text, checked)
    ):
        return None
    url = PAGES[track["key"]]
    route = re.fullmatch(
        r"https://www\.fulbright\.bg/en/fulbright-international-seminar-"
        r"(20\d{2})/",
        url,
    )
    if not route or int(route[1]) != year:
        return None
    return (
        f"Publisher identifies this specific archival seminar edition as {year} "
        f"({url}): {heading}; {invitations[0][0]}; {purposes[0][0]}"
    )


def record_has_verified_own_archival_evidence(record, checked):
    """Exclude only exact bounded generated own-edition archival proof."""
    url = record.get("url", "")
    route = re.fullmatch(
        r"https://www\.fulbright\.bg/en/fulbright-international-seminar-"
        r"(20\d{2})/",
        url,
    )
    if (
        record.get("source") != SOURCE_ID
        or url not in PAGES.values()
        or not route
    ):
        return False
    for note in record.get("classification", {}).get("evidence", []):
        if not isinstance(note, str) or len(note) > 600:
            continue
        proof = re.fullmatch(
            r"Publisher identifies this specific archival seminar edition as "
            r"(20\d{2}) \(" + re.escape(url) + r"\): "
            r"Fulbright International Seminar\s+(20\d{2}); "
            r"participate in the\s+(20\d{2})\s+FIS; "
            r"FIS\s+(20\d{2})\s+enables",
            note,
            re.I,
        )
        if proof and {int(x) for x in proof.groups()} == {int(route[1])}:
            return (
                int(route[1])
                < checked.astimezone(timezone(timedelta(hours=-12))).year
            )
    return False


def record_has_verified_own_closure(record, checked):
    """Accept only bounded generated own-source evidence, never status alone."""
    url = record.get("url", "")
    if record.get("source") != SOURCE_ID or url not in PAGES.values():
        return False
    evidence = record.get("classification", {}).get("evidence", [])
    for note in evidence:
        if not isinstance(note, str) or len(note) > 600:
            continue
        prefix = f"Publisher confirms this own competition is closed ({url}): "
        if note.startswith(prefix):
            quote = note[len(prefix) :]
            match = explicit_competition_closure(quote)
            if match and match[0] == quote:
                return True
        match = re.fullmatch(
            r"Publisher dates this specific edition as ended on "
            r"(\d{4}-\d{2}-\d{2}) \(" + re.escape(url) + r"\): (.{1,350})",
            note,
        )
        if match:
            try:
                end = datetime.strptime(match[1], "%Y-%m-%d").date()
            except ValueError:
                continue
            quote = match[2]
            course = re.fullmatch(r"\d+\s+days:\s*.+?\b20\d{2}", quote, re.I)
            seminar = re.fullmatch(
                r"Fulbright International Seminar\s+20\d{2}; "
                r"seminar that took place in Sofia,\s*Sept\.?\s*\d{1,2}"
                r"[-–]\d{1,2}\. We hosted\.",
                quote,
                re.I,
            )
            supported = False
            if course:
                schedule = course_schedule_end(quote)
                supported = schedule is not None and schedule[0] == end
            if seminar:
                year = re.search(r"Seminar\s+(20\d{2});", quote, re.I)
                days = re.search(r"Sept\.?\s*(\d{1,2})[-–](\d{1,2})", quote)
                try:
                    start = datetime(int(year[1]), 9, int(days[1])).date()
                    actual_end = datetime(int(year[1]), 9, int(days[2])).date()
                    supported = start <= actual_end == end
                except (ValueError, TypeError):
                    supported = False
            if (
                supported
                and checked.astimezone(timezone(timedelta(hours=-12))).date()
                > end
            ):
                return True
    return False


def programme_status(root, deadline):
    if deadline:
        checked = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
        closing = datetime.strptime(deadline, "%Y-%m-%d").date()
        latest = checked.astimezone(timezone(timedelta(hours=-12))).date()
        earliest = checked.astimezone(timezone(timedelta(hours=14))).date()
        return (
            "expired"
            if latest > closing
            else "open" if earliest < closing else "unknown"
        )
    checked = datetime.fromisoformat(utc_now().replace("Z", "+00:00"))
    banner = " ".join(n.text() for n in modules(root)[:5])
    text = primary(root)
    closure = explicit_competition_closure(banner)
    if (
        closure
        and closure[0] in text
        and not announced_future_reopening(text, checked)
    ):
        return "expired"
    # Archive labels, event age and enrollment forms cannot establish opening.
    return "unknown"


def applicant_countries(key, root, track):
    """Project explicit passport conditions and preserve source conflicts."""
    text = primary(root)
    if key in ("graduate-bg", "doctoral", "scholar", "thanks", "mba-alias"):
        if re.search(
            r"(?:Applicants must have|Eligibility Requirements.*?) Bulgarian"
            r" citizenship",
            text,
            re.I,
        ):
            return ["BG"]
    if key == "humphrey" and re.search(r"Be Bulgarian citizens", text, re.I):
        return ["BG"]
    if key == "ngo" and re.search(r"българско гражданство", text, re.I):
        return ["BG"]
    if key == "graduate-us" and re.search(
        r"Applicants must be U\.S\. citizens at the time of application",
        text,
        re.I,
    ):
        return ["US"]
    if key == "scholar-us" and re.search(r"U\.S\. citizen", text, re.I):
        return ["US"]
    if key == "eta" and re.search(
        r"U\.S\. or dual \(Bulgarian, European\) citizens", text, re.I
    ):
        return ["US"]
    if key == "specialist" and track == "specialist-us-professional":
        if re.search(
            r"Be a U\.S\. citizen",
            block(
                root,
                "Individuals Interested in Serving as a Fulbright Specialists",
            ),
            re.I,
        ):
            return ["US"]
    if key == "alumni" and re.search(
        r"Bulgarian or US citizens who are alumni", text, re.I
    ):
        return ["BG", "US"]
    return []


def programme_year(root):
    values = re.findall(
        r"(?:AY|Academic year|academic)\s+(20\d{2})[-–](\d{2})",
        primary(root),
        re.I,
    )
    if not values:
        return None
    return values[0][0] + "–" + values[0][1]


DISCLOSURE_HEADINGS = {
    "agile": {
        "Materials and certificate",
        "Consultations",
        "Library",
        "About PMI-ACP®",
    },
    "graduate-us": {
        "Fulbright study / research award",
        "Fulbright Bulgaria-Romania Joint Research Award",
        "Fulbright Bulgaria-Greece Joint Research Award",
        "Eligibility",
        "Candidate profile",
        "Assignment and affiliation",
        "Application",
        "Grant benefits",
    },
    "scholar-us": {
        "Flex Award",
        "Eligibility",
        "Candidate profile",
        "Assignment and affiliation",
        "Grant benefits",
        "Application",
    },
    "specialist": {"Country description"},
}


def own_navigation(key, root):
    """Discover real programme cards including Divi's inert navigation."""
    result = []
    configured = script_links(root)
    for item in modules(root):
        classes = item.attrs.get("class", "").split()
        for class_name in classes:
            if class_name in configured and "et_clickable" in classes:
                if configured[class_name].startswith("#"):
                    labels = {
                        n.text() for n in item.walk() if n.tag in ("h3", "h4")
                    }
                    if not labels or not labels.issubset(
                        DISCLOSURE_HEADINGS.get(key, set())
                    ):
                        raise AdapterError(
                            "Unhandled programme card fragment", "parse"
                        )
                    continue
                result.append(
                    ("Programme card: " + item.text(), configured[class_name])
                )
        for anchor in nodes(item, "a"):
            href = anchor.attrs.get("href", "")
            if href and not href.startswith(("#", "mailto:", "tel:")):
                result.append(
                    (
                        (
                            item.text()
                            if re.fullmatch(
                                r"learn more|read more|more info|\$|",
                                anchor.text(),
                                re.I,
                            )
                            else anchor.text()
                        ),
                        href,
                    )
                )
    for anchor in nodes(root, "a"):
        if any(
            anchor in list(menu.walk())
            for menu in nodes(root, "ul")
            if "menu" in menu.attrs.get("class", "")
        ):
            result.append((anchor.text(), anchor.attrs.get("href", "")))
    return [
        (label, reviewed_url(urllib.parse.urljoin(PAGES[key], href)))
        for label, href in result
        if urllib.parse.urlsplit(
            urllib.parse.urljoin(PAGES[key], href)
        ).hostname
        in ("www.fulbright.bg", "fulbright.bg")
    ]


ROUTE_ALIASES = {
    "/en/visiting-scholar-grants-bg/": "scholar",
    "/en/graduate-students-usa/": "graduate-us",
    "/en/sofijski-rajonen-sad/": "court",
    "/en/media-literacy-for-teachers/": "ftea",
    "/en/opportunity-fund-bulgaria/": "opportunity-funds",
    "/en/konkurs-opportunity-funds-bulgaria/": "opportunity-funds",
    "/alumni-grants/": "alumni",
}
DISCOVERY_ROOTS = tuple(
    key
    for key in PAGES
    if key
    not in (
        "conferences",
        "conference2",
        "news",
        "privacy",
        "privacy-bg",
    )
)


def check_frontier(pages):
    known = {
        urllib.parse.urlsplit(url).path.rstrip("/") + "/": key
        for key, url in PAGES.items()
    }
    known.update(ROUTE_ALIASES)
    found = set()
    for key in DISCOVERY_ROOTS:
        for label, url in own_navigation(key, pages[key]):
            path = urllib.parse.urlsplit(url).path.rstrip("/") + "/"
            if path in known:
                found.add(known[path])
            elif re.search(
                r"Programme"
                r" card:|grant|scholarship|fellowship|internship|award|"
                r"opportunity|competition|advising|training|course|"
                r"стипенди|обучен|конкурс|стаж|семинар",
                label,
                re.I,
            ):
                # Contact, social, advising descriptions and impact stories are
                # not silently treated as unreviewed scholarship applicants.
                if path not in NON_OPPORTUNITY_ROUTES:
                    raise AdapterError(
                        "Unhandled programme route: " + path, "parse"
                    )
    required = {
        track["key"]
        for track in TRACKS
        if track["key"]
        not in (
            "fis2019",
            "fis2021",
            "fis2022",
            "journalists",
            "ngo",
            "mba-alias",
            "law",
        )
    }
    if not required.issubset(found):
        raise AdapterError("Missing programme discovery coverage", "parse")


NON_OPPORTUNITY_ROUTES = {
    "/en/advisory-services/",
    "/en/alumni-achievements/",
    "/en/about-us/",
    "/en/grantees/",
    "/en/contacts/",
    "/en/privacy-policy/",
    "/en/resources-for-us-grantees/",
    "/en/info-sessions-scholar-phd-grants/",
    "/stipendianti/",
}


def news_inventory(root):
    """Detect unhandled programme announcements in current news cards."""
    cards = nodes(article(root), "article", "et_pb_post")
    if not cards:
        raise AdapterError("Missing current news discovery cards", "parse")
    reviewed_reports = {"/en/fulbright-bulgarian-nominees-2026-27/"}
    for card in cards:
        heading = one(
            [n for n in card.walk() if n.tag in ("h2", "h3")],
            "news card heading",
        )
        link = one(nodes(heading, "a"), "news card link")
        path = urllib.parse.urlsplit(reviewed_url(link.attrs["href"])).path
        label = heading.text()
        classes = card.attrs.get("class", "").split()
        if path not in reviewed_reports and (
            re.search(
                r"\b(?:grants?|scholarships?|fellowships?|apply|"
                r"applications?|opportunity|competition|training)\b",
                label,
                re.I,
            )
            or "category-grants" in classes
        ):
            raise AdapterError(
                "Unhandled programme announcement: " + path, "parse"
            )


def check_track_sections(pages):
    """Fail on unhandled extra awards within a supported programme leaf."""
    expected = {
        "graduate-us": {
            "Fulbright study / research award",
            "Fulbright Bulgaria-Romania Joint Research Award",
            "Fulbright Bulgaria-Greece Joint Research Award",
            "Fulbright Study / Research Grant in Archaeology / History",
            "Grant benefits",
        },
        "schuman": {
            "Grants for (Pre)-Doctoral Research (Students)",
            "Grants for Post-Doctoral Research and/or Lecturing (Scholars)",
            "Innovation Award – (Students and Scholars)",
            "International Educator Award (Scholars)",
            "Innovation award",
            "Grant for Scholars in Mathematics",
        },
        "scholar-us": {"Annual awards", "Flex Award", "Grant benefits"},
    }
    alumni = primary(pages["alumni"])
    if re.findall(r"(?<!\d)(\d+)\)\s+[A-Z]", alumni) != ["1", "2", "3"]:
        raise AdapterError("Unhandled alumni award-type inventory", "parse")
    specialist = {
        n.text()
        for item in modules(pages["specialist"])
        for n in item.walk()
        if n.tag == "h5" and n.text() != "More Info"
    }
    if specialist != {
        "Cost share for host insitutions",
        "Application process for host institutions",
        "Individuals Interested in Serving as a Fulbright Specialists",
    }:
        raise AdapterError("Unhandled Specialist applicant section", "parse")
    for key, supported in expected.items():
        actual = set()
        for item in modules(pages[key]):
            if "et_pb_blurb" not in item.attrs.get("class", "").split():
                continue
            for heading in [
                n for n in item.walk() if n.tag in ("h2", "h3", "h4")
            ]:
                if re.search(r"\b(?:awards?|grants?)\b", heading.text(), re.I):
                    actual.add(heading.text())
        if actual - supported:
            raise AdapterError("Unhandled award section: " + key, "parse")


def conference_inventory(pages):
    """Check complete conference pagination and supported seminar slots."""
    actual = set()
    for key in ("conferences", "conference2"):
        for item in nodes(article(pages[key]), "article", "et_pb_post"):
            heading = one(
                [n for n in item.walk() if n.tag in ("h2", "h3")],
                "conference card heading",
            )
            link = one(nodes(heading, "a"), "conference card link")
            actual.add(reviewed_url(link.attrs["href"]))
    expected = {
        PAGES[key]
        for key in (
            "open2024",
            "open2023",
            "media2023",
            "fis2022-review",
            "fis2022",
            "fis2021",
            "digital2020",
            "fis2019",
        )
    }
    excluded = {
        "https://www.fulbright.bg/en/orientation-for-us-grantees/",
        "https://www.fulbright.bg/en/wrap-up-seminar-for-us-grantees-2022/",
    }
    if actual - expected - excluded or not expected.issubset(actual):
        raise AdapterError(
            "Unhandled conference opportunity inventory", "parse"
        )
    for key in ("conferences", "conference2"):
        next_links = [
            reviewed_url(urllib.parse.urljoin(PAGES[key], n.attrs["href"]))
            for n in nodes(article(pages[key]), "a")
            if "Older Entries" in n.text()
        ]
        if next_links != (
            [PAGES["conference2"]] if key == "conferences" else []
        ):
            raise AdapterError("Unhandled conference pagination", "parse")


def consistent(pattern, text, description):
    values = {m.groups() for m in re.finditer(pattern, text, re.I)}
    return one(list(values), description)


def require(pattern, text, description):
    if not re.search(pattern, text, re.I):
        raise AdapterError("Missing supported " + description, "parse")


def summary_for(track, root):
    """Translate current, specifically scoped fields into independent facts."""
    key, identity = track["key"], track["track"]
    text = primary(root)
    if key == "graduate-bg":
        benefit = block(
            root, "What does the Fulbright graduate study grant cover\\?"
        )
        (cap,) = consistent(
            r"up to USD\s*([\d,]+)\s+toward tuition",
            benefit,
            "graduate total cap",
        )
        (months,) = consistent(
            r"one academic year \((\d+) months\)", text, "graduate duration"
        )
        return (
            f"US degree study for {months} months; funding up to USD"
            f" {amount(cap)} toward tuition and living costs. "
            + (
                "Round-trip travel and health coverage are included. "
                if re.search(
                    r"covers travel costs.*health insurance", benefit, re.I
                )
                else ""
            )
            + "Consult the current eligibility and standardized-test"
            " requirements on the programme page."
        )
    if key == "doctoral":
        duration = matched(
            r"funding for (\d+)[-–](\d+) months of research",
            text,
            "doctoral duration",
        )
        support = block(
            root, "What does the Fulbright Visiting Researcher grant cover\\?"
        )
        require(
            r"monthly maintenance.*return flight.*health insurance",
            support,
            "doctoral benefits",
        )
        return (
            "Nondegree US research for enrolled doctoral students, lasting"
            f" {duration[1]}–{duration[2]} months. Destination-based monthly"
            " maintenance, return travel and health coverage are provided. "
            + (
                "A US host invitation is required. "
                if "invitation letter should be issued" in text
                else ""
            )
            + (
                "The source excludes course enrollment by visiting"
                " researchers."
                if "not eligible to enroll in courses" in text
                else ""
            )
        )
    if key == "scholar":
        benefit = block(root, "What does the Fulbright scholar grant cover\\?")
        low, high = consistent(
            r"amount of \$([\d, ]+)\s*[-–]\s*\$([\d, ]+),",
            benefit,
            "scholar monthly maintenance",
        )
        duration = matched(
            r"between (\d+) to (\d+) months", benefit, "scholar duration"
        )
        return (
            "US research or lecturing for scholars for"
            f" {duration[1]}–{duration[2]} months. Monthly maintenance is USD"
            f" {amount(low)}–{amount(high)}. "
            + (
                "Return travel and health coverage are described in the"
                " benefits section. "
                if re.search(r"travel|flight", benefit, re.I)
                else ""
            )
            + "Use the programme page for current degree, host-invitation and"
            " language conditions."
        )
    if key == "leaders":
        own = one(
            [
                n.text()
                for n in modules(root)
                if n.text().startswith("Thanks to a recent funding increase")
            ],
            "Leaders placement description",
        )
        (cap,) = consistent(
            r"Grants offer up to \$([\d,]+)", own, "Leaders funding cap"
        )
        (ranking,) = consistent(
            r"ranked in the top (\d+) graduate programs",
            own,
            "Leaders placement condition",
        )
        return (
            f"Self-placement funding up to USD {amount(cap)} for MA/PhD"
            f" students admitted to a US programme ranked in the top {ranking}"
            " in its field. "
            + (
                "The award can span several academic years. "
                if "spread across multiple academic years" in own
                else ""
            )
            + (
                "Recipients sign a three-year return-to-Bulgaria agreement."
                if "return to Bulgaria for three years" in own
                else ""
            )
        )
    if key == "humphrey":
        own = block(root, "Eligibility Requirements")
        scores = matched(
            r"TOEFL iBT:\s*(\d+) Duolingo English Test:\s*(\d+)"
            r" IELTS:\s*([\d.]+)",
            own,
            "Humphrey English alternatives",
        )
        return (
            "US nondegree leadership development for mid-career"
            f" professionals. Minimum English scores are TOEFL {scores[1]},"
            f" Duolingo {scores[2]} or IELTS {scores[3]}. "
            + (
                "The source requires postdegree full-time professional"
                " experience and management or policy responsibilities. "
                if "full-time professional experience" in own
                and "management or policy responsibilities" in own
                else ""
            )
            + "Academic study and a professional affiliation form part of the"
            " programme."
        )
    if key == "thanks":
        require(r"Thanks to Scandinavia", text, "Foundation award identity")
        return (
            "US graduate award associated with Thanks to Scandinavia."
            " Selection follows the Fulbright and Foundation processes. The"
            " programme-specific additional funding is not quantified; figures"
            " in the generic graduate FAQ do not establish this award’s"
            " amount."
        )
    if key == "law":
        (rate,) = consistent(
            r"(\d+)\s*%\s*от (?:таксата|учебната такса)",
            text,
            "Fordham tuition fraction",
        )
        return (
            f"Fordham Law LLM tuition support in New York: {rate}% of tuition."
            " The source describes direct application to the university and a"
            " bachelor’s-degree requirement. The law specializations are"
            " choices within this award."
        )
    if key == "math":
        support = block(root, "The grant covers")
        (cap,) = consistent(
            r"monthly maintenance allowance of \$([\d,]+)",
            support,
            "mathematics monthly maintenance",
        )
        require(r"University of Miami", text, "mathematics host institution")
        return (
            "Mathematics research at the University of Miami/IMSA, with USD"
            f" {amount(cap)} monthly maintenance. "
            + (
                "Travel, settling support and health coverage are included. "
                if "round trip airfare" in support
                and "health insurance" in support
                else ""
            )
            + "The source specifies early-career PhD and institutional"
            " conditions."
        )
    if key == "court":
        require(
            r"двумесечен стаж.*Федералния окръжен съд на Луизиана",
            text,
            "court internship placement",
        )
        return (
            "Two-month placement at the federal district court in New Orleans"
            " for selected participants in the Sofia Regional Court internship"
            " programme. "
            + (
                "Fulbright supports travel, maintenance and visa costs."
                if re.search(r"средства за пътуване.*издръжка.*виза", text)
                else ""
            )
        )
    if key == "ftea":
        criteria = block(root, "Eligibility Criteria")
        (score,) = consistent(
            r"(?:at least|minimum iBT TOEFL score of)\s*(\d+)",
            criteria,
            "teacher English minimum",
        )
        require(
            r"full-time secondary-level teachers",
            criteria,
            "teacher applicant role",
        )
        return (
            "US professional development for full-time secondary teachers"
            " with a bachelor’s-equivalent degree and teaching experience."
            f" TOEFL minimum: {score}. "
            + (
                "Flights, academic costs, housing, meals and a daily allowance"
                " are supported. "
                if re.search(
                    r"Round-trip airfare, housing, meals, daily allowance,"
                    r" academic fees",
                    text,
                )
                else ""
            )
            + "The application deadline is distinct from interview, nomination"
            " and cohort dates."
        )
    if key == "schuman":
        headings = {
            "schuman-pre-doctoral": (
                r"Grants for \(Pre\)-Doctoral Research \(Students\)"
            ),
            "schuman-postdoctoral": (
                r"Grants for Post-Doctoral Research and/or Lecturing"
                r" \(Scholars\)"
            ),
            "schuman-innovation": (
                r"Innovation Award – \(Students and Scholars\)"
            ),
            "schuman-international-educator": (
                r"International Educator Award \(Scholars\)"
            ),
        }
        section = block(root, headings[identity])
        result = {
            "schuman-pre-doctoral": (
                "Predoctoral US study/research on EU–US relations, policy or"
                " institutions."
            ),
            "schuman-postdoctoral": (
                "Postdoctoral US research or lecturing on EU–US relations,"
                " policy or institutions."
            ),
            "schuman-innovation": (
                "Research on the intersection of policy and technology in"
                " EU–US relations."
            ),
            "schuman-international-educator": (
                "US research for international-education professionals at"
                " European universities."
            ),
        }[identity]
        duration = re.search(r"between (\w+) to (\w+) months", section, re.I)
        if duration:
            result += f" Duration: {duration[1]} to {duration[2]} months."
        if re.search(
            r"letter of affiliation|arranging their own affiliation",
            section,
            re.I,
        ):
            result += " A US institutional affiliation is required."
        if (
            identity == "schuman-innovation"
            and "tenable in any one or two EU Member States" in section
        ):
            result += (
                " The source also mentions one or two EU member states; the"
                " complete venue scope is ambiguous."
            )
        return result
    if key == "eta":
        benefits = block(root, "Grant benefits")
        (stipend,) = consistent(
            r"Monthly stipend\s*[-–]\s*\$([\d,]+) per month",
            benefits,
            "ETA stipend",
        )
        (travel,) = consistent(
            r"Round-trip air travel allowance\s*[-–]\s*\$([\d,]+)",
            benefits,
            "ETA travel allowance",
        )
        (relocation,) = consistent(
            r"Relocation and incidentals allowance\s*[-–]\s*\$([\d,]+)",
            benefits,
            "ETA relocation allowance",
        )
        (language,) = consistent(
            r"Bulgarian language\s*[-–]\s*\$([\d,]+)",
            benefits,
            "ETA language allowance",
        )
        return (
            "English teaching in Bulgarian schools/universities. USD"
            f" {amount(stipend)} monthly, USD {amount(travel)} travel, USD"
            f" {amount(relocation)} relocation and USD {amount(language)}"
            " language support. "
            + (
                "Host-school housing is free; utilities are the grantee’s"
                " responsibility. "
                if re.search(
                    r"Housing is provided.*free of charge.*utility bills",
                    benefits,
                )
                else ""
            )
            + "The citizenship banner has ambiguous dual-citizen wording;"
            " confirm eligibility with the publisher."
        )
    if key == "eta-host":
        low, high = consistent(
            r"между (\d+) и (\d+) часа преподаване седмично",
            text,
            "institutional teaching plan",
        )
        return (
            "Bulgarian schools and universities may host an English teaching"
            f" assistant, providing {low}–{high} teaching hours per week. "
            + (
                "Hosts supply materials, furnished free housing and"
                " mentoring. "
                if "обзаведено и безплатно жилище" in text
                and "учител-ментор" in text
                else ""
            )
            + "The institution applies; beneficiary citizenship is not an"
            " institutional passport condition."
        )
    if key == "graduate-us":
        headings = {
            "us-graduate-study": r"Fulbright study / research award",
            "us-graduate-bg-romania": (
                r"Fulbright Bulgaria-Romania Joint Research Award"
            ),
            "us-graduate-bg-greece": (
                r"Fulbright Bulgaria-Greece Joint Research Award"
            ),
            "us-graduate-archaeology": (
                r"Fulbright Study / Research Grant in Archaeology / History"
            ),
        }
        section = block(root, headings[identity])
        benefits = block(root, "Grant benefits")
        (stipend,) = consistent(
            r"Monthly stipend\s*[-–]\s*\$\s*([\d ]+) per month",
            benefits,
            "US graduate stipend",
        )
        result = {
            "us-graduate-study": "Study/research in Bulgaria.",
            "us-graduate-bg-romania": (
                "Joint comparative study/research in Bulgaria and Romania."
            ),
            "us-graduate-bg-greece": (
                "Joint comparative study/research in Bulgaria and Greece."
            ),
            "us-graduate-archaeology": (
                "Bulgaria archaeology/history placement with the Balkan"
                " Heritage Foundation."
            ),
        }[identity]
        duration = re.search(
            r"(?:Awards are for|award is for|spend up to) (\d+) months",
            section,
            re.I,
        )
        if duration:
            result += f" Duration: {duration[1]} months."
        result += (
            f" Monthly stipend: USD {amount(stipend)}. The source also"
            " describes travel, research and health support."
        )
        if "This content has been archived" in text:
            result += " The page is archived; no current opening is inferred."
        return result
    if key == "scholar-us":
        benefits = block(root, "Grant benefits")
        (stipend,) = consistent(
            r"Award Allowances:\s*\$([\d,]+) per month",
            benefits,
            "US scholar stipend",
        )
        if identity == "us-scholar-flex":
            (stipend,) = consistent(
                r"For Flex grants:\s*\$([\d,]+) per month",
                benefits,
                "Flex monthly stipend",
            )
            section = block(root, "Flex Award")
            require(r"two short segments", section, "Flex visit structure")
            (travel,) = consistent(
                r"For Flex grants: Travel allowance of \$([\d,]+) total for"
                r" all segments",
                benefits,
                "Flex aggregate travel",
            )
            return (
                "Two-segment Bulgaria teaching/research/professional award."
                f" USD {amount(stipend)} monthly and USD {amount(travel)}"
                " travel across all segments. "
                + (
                    "Flex provides no dependent benefits. "
                    if re.search(
                        r"For Flex grants, dependent benefits will not be"
                        r" provided",
                        benefits,
                        re.I,
                    )
                    else ""
                )
                + "Applicants select Flex and provide their timeline."
            )
        travel, relocation = consistent(
            r"Travel allowance of \$([\d,]+) and relocation allowance of"
            r" \$([\d,]+) for grantee",
            benefits,
            "core scholar allowances",
        )
        return (
            "Bulgaria teaching/research/professional award for appropriately"
            f" qualified US applicants. USD {amount(stipend)} monthly, USD"
            f" {amount(travel)} travel and USD {amount(relocation)}"
            " relocation. Dependent support is conditional; consult the"
            " benefits section."
        )
    if key == "specialist":
        parameters = block(root, "Program Parameters")
        low, high = consistent(
            r"range from (\d+) to (\d+) days",
            parameters,
            "Specialist project duration",
        )
        if identity == "specialist-us-professional":
            section = block(
                root,
                "Individuals Interested in Serving as a Fulbright Specialists",
            )
            require(r"Be a U\.S\. citizen", section, "Specialist citizenship")
            return (
                "Experienced US citizens can apply for Specialist roster"
                " consideration and institutional education assignments"
                f" lasting {low}–{high} days. Roster acceptance does not"
                " guarantee a funded project; match and approval are"
                " required."
            )
        costs = block(root, "Cost share for host insitutions")
        require(
            r"lodging, meals, and in-country transportation",
            costs,
            "Specialist host cost share",
        )
        return (
            "Institutional education/training projects hosting a US"
            f" specialist for {low}–{high} days. Hosts cover lodging, meals"
            " and local transportation; federal support covers international"
            " travel, limited health benefits and an honorarium. Yearless"
            " submission dates are not assigned a year."
        )
    if key == "alumni":
        label = {
            "alumni-travel": "Travel Award",
            "alumni-publication": "Publication Award",
            "alumni-open-project": "Open Alumni Awards",
        }[identity]
        require(label, text, "alumni award type")
        (cap,) = consistent(
            r"Maximum award amount:\s*€([\d,]+) per project",
            text,
            "alumni per-project ceiling",
        )
        return (
            f"Fulbright Bulgaria alumni {label.lower()} for Bulgarian or US"
            f" citizen alumni. Maximum EUR {amount(cap)} per project,"
            " including joint proposals. "
            + (
                "Only one award type per alumnus per year is permitted. "
                if "only ONE type of alumni grant per year" in text
                else ""
            )
            + "The rolling-open banner and dated round must be read together."
        )
    if key in ("pmp", "agile"):
        return course_summary(track, root)
    if key == "ngo":
        low, high = consistent(
            r"издръжка в размер на ([\d ]+)\s*[–-]\s*([\d ]+) щатски долара",
            text,
            "NGO monthly maintenance",
        )
        require(
            r"предназначена за ръководители и представители на български"
            r" неправителствени организации",
            text,
            "NGO applicant role",
        )
        return (
            "US civil-society research for Bulgarian NGO"
            f" leaders/representatives, with USD {amount(low)}–{amount(high)}"
            " monthly maintenance. "
            + (
                "A US NGO/university invitation is required. "
                if "официална покана от американска" in text
                else ""
            )
            + "The source’s early-December application closing has no complete"
            " year."
        )
    if key == "mba-alias":
        require(
            r"University of Oklahoma MBA Grant",
            text,
            "Oklahoma programme identity",
        )
        return (
            "Oklahoma Price College MBA funding for the programme’s duration."
            " The leaf’s dated competition is historical despite its open"
            " banner. The generic graduate FAQ cap is not a verified"
            " MBA-specific benefit."
        )
    if key == "journalists":
        criteria = block(root, "Критерии за подбор")
        (level,) = consistent(
            r"ниво (B\d)", criteria, "journalist English level"
        )
        require(
            r"минимум една година профессионален|минимум една година"
            r" професионален",
            criteria,
            "journalist experience",
        )
        return (
            "Two-week US training for practicing journalists with"
            f" professional experience and {level} English. The publisher"
            " funds the training visit. Bulgarian-citizen wording conflicts"
            " with a broader country-origin list, so no passport whitelist is"
            " inferred."
        )
    if key.startswith("fis"):
        require(
            "Fulbright International Seminar " + key[3:],
            article(root).text(),
            "historical seminar identity",
        )
        if key == "fis2021":
            require(r"Participation is free of charge", text, "seminar fee")
            return (
                "Historical hybrid democracy/representation seminar in Sofia"
                " and online. Full participants attend workshops and complete"
                " projects for certification. Participation was free; partial"
                " travel/accommodation support was conditional."
            )
        if key == "fis2022":
            require(r"Rule of Law", text, "seminar subject")
            return (
                "Historical hybrid rule-of-law seminar in Sofia, with distinct"
                " in-person and online participation. Students, civic"
                " activists and professionals are described as participants."
                " No complete application closing or guaranteed travel"
                " allowance is published."
            )
        return (
            "Historical democracy seminar in Sofia and Plovdiv, with"
            " structured lectures, workshops and participant projects."
            " Attendance-country lists do not establish a citizenship"
            " whitelist; no current opening is inferred."
        )
    if key == "opportunity-funds":
        eligibility = block(root, "Student Eligibility")
        (cap,) = consistent(
            r"A maximum of \$([\d,]+) in Opportunity Funds",
            block(root, "Allowable Costs"),
            "Funds per-student ceiling",
        )
        require(
            r"10th or 11th grade students enrolled full-time in Bulgarian high"
            r" schools",
            eligibility,
            "Funds school grades",
        )
        require(
            r"citizen of, or officially recognized refugee in Bulgaria",
            eligibility,
            "Funds citizenship or refugee alternative",
        )
        require(
            r"U\.S\. citizens and permanent residents.*not eligible",
            eligibility,
            "Funds exclusion",
        )
        require(
            r"does not guarantee acceptance or financial aid",
            block(root, "Program Objectives"),
            "Funds conditional outcome",
        )
        return (
            f"Up to USD {amount(cap)} per student for US university"
            " application preparation/costs. Full-time grades 10/11 students"
            " in Bulgarian schools must be Bulgarian citizens or recognized"
            " refugees, with financial need, English and academic/leadership"
            " potential. US citizens/permanent residents are excluded."
            " Admission and university financial aid are not guaranteed."
        )
    if key == "group-advising":
        joining = block(root, "Who can Join\\?")
        require(
            r"open to ALL students in grades 8-12",
            joining,
            "advising school grades",
        )
        require(r"once per week", joining, "advising commitment")
        require(
            r"no guarantees.*accepted", text, "advising conditional outcome"
        )
        return (
            "Structured virtual college-application preparation for grades"
            " 8–12, with weekly advising and reading, writing, testing and"
            " community-service activities. Participation requires commitment;"
            " grade-12 entry timing is yearless. US admission is not"
            " guaranteed. This is training/advising, rather than a"
            " scholarship."
        )
    raise AdapterError("Unsupported opportunity track", "parse")


def course_card(track, root):
    """Select the complete card for one actual dated course edition."""
    key, identity = track["key"], track["track"]
    cards = [
        n
        for n in modules(root)
        if "et_pb_blurb" in n.attrs.get("class", "").split()
        and re.search(
            r"Training \(in-person\)|PMP.*(?:Training|Course)", n.text(), re.I
        )
        and re.search(r"20\d{2}", n.text())
    ]
    if len(cards) != 2:
        raise AdapterError("Unhandled course edition inventory", "parse")
    month, year = {
        "pmp-course-2026-september": ("September", "2026"),
        "pmp-course-2026-october": ("October", "2026"),
        "agile-course-2023-october": ("Oct(?:ober)?", "2023"),
        "agile-course-2024-february": ("Feb(?:ruary)?", "2024"),
    }[identity]
    slot = (
        r"\d+ days:\s*" + month + r"\b.*?\b" + year
        if key == "pmp"
        else r"\d+ days:\s*[^€]*?\b" + month + r"\b\s*,?\s*" + year
    )
    card = one(
        [n for n in cards if re.search(slot, n.text(), re.I)],
        "supported course month/year edition",
    )
    return card


def course_status(track, root):
    if verified_own_closure_evidence(track, root, utc_now()):
        return "expired"
    card = course_card(track, root)
    items = modules(root)
    index = items.index(card)
    following = items[index + 1].text() if index + 1 < len(items) else ""
    return (
        "expired"
        if following.lower() in ("fully booked", "completed")
        else "unknown"
    )


def course_summary(track, root):
    """Extract edition-scoped dates and fees without certification claims."""
    prefix = "PMI-ACP" if track["key"] == "agile" else "PMP"
    text = course_card(track, root).text()
    fee = re.search(
        r"€\s*([\d,]+)\s*(VAT (?:excl\.|incl\.)|incl\.? VAT|including VAT)",
        text,
        re.I,
    )
    schedule = matched(
        r"\d+ days:\s*(.*?20\d{2})", text, "course edition dates"
    )[1]
    facts = (
        f"Paid English {prefix} certification preparation in Sofia. Edition"
        f" schedule: {schedule}. "
    )
    if re.search(r"[€$]|EUR", text) and fee is None:
        raise AdapterError("Unsupported course fee", "parse")
    if fee:
        facts += f"Edition fee: EUR {amount(fee[1])}, {fee[2]}. "
    else:
        facts += (
            "This edition has no published fee; another edition’s price is not"
            " transferred. "
        )
    facts += (
        "The edition dates are event dates, not application deadlines. Exam"
        " eligibility is separate from course enrollment."
    )
    return facts


def read_pages():
    return {key: (fetch_inventory_page(page, url)
                  if key in {"conferences", "conference2"} else page(url))
            for key, url in PAGES.items()}


def parse_inventory(pages):
    if set(pages) != set(PAGES):
        raise AdapterError("Incomplete source evidence", "parse")
    for key, root in pages.items():
        legal_facts(key, root)
    check_frontier(pages)
    conference_inventory(pages)
    check_track_sections(pages)
    news_inventory(pages["news"])
    records = []
    for track in TRACKS:
        key, root = track["key"], pages[track["key"]]
        summary = summary_for(track, root)
        closing = deadline_for(key, root)
        title = track["title"]
        year = programme_year(root)
        if key == "ftea":
            year = matched(
                r"in January or September (20\d{2}) for academic seminars",
                primary(root),
                "teacher programme cohort year",
            )[1]
        if key == "opportunity-funds":
            year = matched(
                r"announce the (20\d{2}) Opportunity Funds",
                primary(root),
                "Funds competition year",
            )[1]
        if year:
            title += " " + year
        record = make_record(
            title,
            PAGES[key],
            track["categories"],
            kind=track["kind"],
            host_countries=track["hosts"],
            method="publisher-field-extraction",
            evidence=[
                ATTRIBUTION,
                "Programme/track: " + track["track"],
                "Current source fields: " + summary,
            ],
        )
        record["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + track["track"]).encode()
        ).hexdigest()[:24]
        record["summary"] = summary
        record["summary_language"] = "en"
        record["eligible_countries"] = applicant_countries(
            key, root, track["track"]
        )
        record["language"] = track["language"]
        record["deadline"] = closing
        record["status"] = (
            course_status(track, root)
            if key in ("pmp", "agile")
            else programme_status(root, closing)
        )
        closure = (
            verified_own_closure_evidence(track, root, utc_now())
            if closing is None
            else None
        )
        if closure:
            record["classification"]["evidence"].append(closure)
            record["status"] = "expired"
        elif closing is None:
            archival = verified_own_archival_evidence(track, root, utc_now())
            if archival:
                record["classification"]["evidence"].append(archival)
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
        r"(?i)\b(token|password|secret|api[_-]?key|"
        r"authorization)\s*[:=]\s*\S+",
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
        (urllib.parse.urlsplit(url).hostname or "")
        .lower()
        .removeprefix("www.")
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



def fetch_source(url, min_interval=6):
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
                    current = urllib.parse.urljoin(
                        current, headers["Location"]
                    )
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status} for "
                        + urllib.parse.urlunsplit(
                            (
                                urllib.parse.urlsplit(current).scheme,
                                urllib.parse.urlsplit(current).hostname or "",
                                urllib.parse.urlsplit(current).path,
                                "",
                                "",
                            )
                        ),
                        "fetch",
                        status,
                    )
                return body.decode(headers.get_content_charset() or "utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch",
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "", "", "")
    )
    if origin not in _ROBOTS:
        try:
            _ROBOTS[origin] = fetch_source(origin + "/robots.txt")
        except AdapterError as error:
            if error.status == 404:
                _ROBOTS[origin] = ""
            else:
                raise
    text = _ROBOTS[origin]
    groups, group = [], {"agents": [], "rules": [], "delay": 0}
    for line in text.splitlines():
        key, separator, value = line.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
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
            raise AdapterError(
                "Robots policy disallows this source endpoint", "access"
            )
    return max(
        [6] + [g["delay"] for policy in policies for g in policy]
    )


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
        expired = (
            record_has_verified_own_closure(record, checked)
            or record_has_verified_own_archival_evidence(record, checked)
        )
        if isinstance(value, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    # An undocumented zone cannot justify an earlier cutoff.
                    closing_date = datetime.strptime(value, "%Y-%m-%d").date()
                    latest_zone = timezone(timedelta(hours=-12))
                    expired = expired or (
                        checked.astimezone(latest_zone).date() > closing_date
                    )
                else:
                    deadline = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = expired or (
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
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
        "display_name": "Fulbright Bulgaria",
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
                f"Validated refresh retains {len(records)} "
                "validated Fulbright Bulgaria "
                "awards and training editions"
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
            "metadata.json": (
                json.dumps(outcome, ensure_ascii=False, indent=2) + "\n"
            )
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
                        "metadata.json": (
                            json.dumps(outcome, ensure_ascii=False, indent=2)
                            + "\n"
                        )
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

FAMILY = "youthopps-bg-fulbright-bulgaria-publisher-v1"

FAMILY_SOURCES = {SOURCE_ID}

_WORKFLOW_ID = 378390054

_ARTIFACT_NAME = "bg-fulbright-bulgaria-pacing-state"

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

_LEGACY_TRANSITION = {
    "schema": 1,
    "family": "youthopps-bg-fulbright-bulgaria-publisher-v1",
    "history_sha256": "93421ddbf148f0883a00b5d140a828c5a9316f215dcc99825dda18596d32207b",
    "state": {
        "schema": 3,
        "family": "youthopps-bg-fulbright-bulgaria-publisher-v1",
        "observed_at": 1792131479.0749724,
        "not_before": 1792131539.0749724,
        "starts": [
            1791518917.0,
            1791519196.0,
            1791560401.0,
            1791591926.0,
            1791622309.0,
            1791622730.0,
            1791623424.0,
            1791655279.0,
            1791658751.0,
            1791676697.0,
            1791710674.0,
            1791746221.0,
            1791763555.0,
            1791798789.0,
            1791843789.0,
            1791885028.0,
            1791923420.0,
            1791936140.0,
            1791965268.0,
            1791967277.0,
            1791986970.0,
            1792004552.0,
            1792013025.0,
            1792020834.0,
            1792047363.0,
            1792050965.0,
            1792060781.6003006,
            1792060787.6005714,
            1792060793.6009066,
            1792060799.6010602,
            1792060805.6011927,
            1792060811.6013246,
            1792060817.6016412,
            1792060832.0,
            1792062219.0,
            1792079814.0,
        ],
        "blocked": False,
        "interval": 12.2,
        "completed": 1792079814.0,
        "page_interval": 30,
        "last_inventory_completed": 1792079814.0,
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
        "parent_identity": [34, 2314, 1000, 448],
        "publisher_identity": [
            34,
            2315,
            179,
            1792060817598517301,
            1792060817598517301,
            1000,
            384,
            1,
        ],
        "raw_sha256": "5a767565a583fb2c251c7eb997fb6bdc9923f411abe7c3082da11c449a0f6f5e",
        "state": {
            "starts": [
                1792060781.6003006,
                1792060787.6005714,
                1792060793.6009066,
                1792060799.6010602,
                1792060805.6011927,
                1792060811.6013246,
                1792060817.6016412,
            ],
            "interval": 6,
            "until": 0,
        },
    },
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/314#issuecomment-6209611751",
}
_HOSTED_TRANSITION = {'schema': 1,
 'family': 'youthopps-bg-fulbright-bulgaria-publisher-v1',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/314#issuecomment-6210529147',
 'history_sha256': '93421ddbf148f0883a00b5d140a828c5a9316f215dcc99825dda18596d32207b',
 'state': {'schema': 3,
           'family': 'youthopps-bg-fulbright-bulgaria-publisher-v1',
           'observed_at': 1792133774.50055,
           'not_before': 1792133834.50055,
           'starts': [1791518917.0,
                      1791519196.0,
                      1791560401.0,
                      1791591926.0,
                      1791622309.0,
                      1791622730.0,
                      1791623424.0,
                      1791655279.0,
                      1791658751.0,
                      1791676697.0,
                      1791710674.0,
                      1791746221.0,
                      1791763555.0,
                      1791798789.0,
                      1791843789.0,
                      1791885028.0,
                      1791923420.0,
                      1791936140.0,
                      1791965268.0,
                      1791967277.0,
                      1791986970.0,
                      1792004552.0,
                      1792013025.0,
                      1792020834.0,
                      1792047363.0,
                      1792050965.0,
                      1792060781.6003006,
                      1792060787.6005714,
                      1792060793.6009066,
                      1792060799.6010602,
                      1792060805.6011927,
                      1792060811.6013246,
                      1792060817.6016412,
                      1792060832.0,
                      1792062219.0,
                      1792079814.0,
                      1792133176.3338442,
                      1792133176.678845,
                      1792133176.897792,
                      1792133177.5023367,
                      1792133177.5068958,
                      1792133189.7076812,
                      1792133189.7127025,
                      1792133189.9373217,
                      1792133190.6932132,
                      1792133190.992776,
                      1792133203.2246392,
                      1792133203.237156,
                      1792133203.6216917,
                      1792133204.0022988,
                      1792133204.0507717,
                      1792133216.292681,
                      1792133216.298042,
                      1792133216.5118618,
                      1792133218.1620321,
                      1792133218.329672,
                      1792133248.355379,
                      1792133248.3589113,
                      1792133248.5847619,
                      1792133248.9068146,
                      1792133248.9965262,
                      1792133261.2180488,
                      1792133261.2261386,
                      1792133261.441872,
                      1792133261.624777,
                      1792133261.848815,
                      1792133274.073865,
                      1792133274.0796056,
                      1792133274.2842715,
                      1792133274.8235466,
                      1792133275.0277154,
                      1792133287.2401552,
                      1792133287.2525556,
                      1792133287.5082104,
                      1792133287.9123714,
                      1792133287.9675333,
                      1792133300.1882918,
                      1792133300.1930754,
                      1792133300.2085268,
                      1792133300.4548414,
                      1792133300.577932,
                      1792133312.792798,
                      1792133312.8325605,
                      1792133312.8664868,
                      1792133313.7713592,
                      1792133314.0660079,
                      1792133326.2842126,
                      1792133326.290238,
                      1792133326.5247173,
                      1792133326.9410641,
                      1792133327.0885394,
                      1792133339.3112671,
                      1792133339.3249593,
                      1792133339.419472,
                      1792133339.754651,
                      1792133339.8980854,
                      1792133352.112478,
                      1792133352.1256921,
                      1792133352.2302892,
                      1792133352.611389,
                      1792133352.7342038,
                      1792133364.9470882,
                      1792133364.9579792,
                      1792133365.0545282,
                      1792133365.221801,
                      1792133365.3562958,
                      1792133377.57124,
                      1792133377.580756,
                      1792133377.6729457,
                      1792133378.3510866,
                      1792133378.3678231,
                      1792133390.5807486,
                      1792133390.5872886,
                      1792133390.676763,
                      1792133390.8814738,
                      1792133391.076505,
                      1792133403.3079486,
                      1792133403.315281,
                      1792133403.3361597,
                      1792133403.6783855,
                      1792133403.7161791,
                      1792133415.9413245,
                      1792133415.9477863,
                      1792133415.9617896,
                      1792133416.8958952,
                      1792133417.1129708,
                      1792133429.336067,
                      1792133429.3423555,
                      1792133429.4300556,
                      1792133429.5986934,
                      1792133429.8084838,
                      1792133442.0311952,
                      1792133442.0351489,
                      1792133442.5623648,
                      1792133442.9490461,
                      1792133443.112676,
                      1792133459.8142786,
                      1792133459.8196213,
                      1792133460.2228858,
                      1792133460.6048281,
                      1792133460.740961,
                      1792133472.9574544,
                      1792133472.9625623,
                      1792133473.200464,
                      1792133473.6736562,
                      1792133473.774405,
                      1792133485.989255,
                      1792133485.9969876,
                      1792133486.26181,
                      1792133486.6163607,
                      1792133486.6918948,
                      1792133498.9230487,
                      1792133498.9280574,
                      1792133499.1537256,
                      1792133499.3082144,
                      1792133499.467217,
                      1792133511.6804595,
                      1792133511.684983,
                      1792133511.9175408,
                      1792133512.1181538,
                      1792133512.2834423,
                      1792133524.503745,
                      1792133524.5087867,
                      1792133524.5960562,
                      1792133524.9266152,
                      1792133525.0343683,
                      1792133537.2493517,
                      1792133537.2534754,
                      1792133537.3387077,
                      1792133537.958833,
                      1792133538.0189621,
                      1792133550.2465012,
                      1792133550.250798,
                      1792133550.3353796,
                      1792133550.4600587,
                      1792133550.576291,
                      1792133562.7885625,
                      1792133562.7972977,
                      1792133562.8837616,
                      1792133563.092562,
                      1792133563.1340861,
                      1792133575.3507755,
                      1792133575.3570848,
                      1792133575.3714952,
                      1792133575.7374206,
                      1792133575.93333,
                      1792133588.152025,
                      1792133588.162178,
                      1792133588.2554474,
                      1792133588.4113567,
                      1792133588.5890808,
                      1792133600.8589604,
                      1792133600.8647306,
                      1792133600.9576705,
                      1792133601.0885391,
                      1792133601.2076936,
                      1792133613.4239693,
                      1792133613.4294052,
                      1792133613.4499016,
                      1792133613.5857375,
                      1792133613.642058,
                      1792133643.6548471,
                      1792133643.666447,
                      1792133643.6866384,
                      1792133643.8148358,
                      1792133643.936475,
                      1792133656.161724,
                      1792133656.17006,
                      1792133656.2605166,
                      1792133656.5669835,
                      1792133656.6493585,
                      1792133668.884948,
                      1792133668.9129617,
                      1792133668.951373,
                      1792133669.515372,
                      1792133669.6943772,
                      1792133681.9208438,
                      1792133681.9312441,
                      1792133682.0289063,
                      1792133682.229839,
                      1792133682.4013643,
                      1792133694.6306376,
                      1792133694.6379366,
                      1792133694.8680758,
                      1792133695.0817847,
                      1792133695.1412296,
                      1792133707.3901825,
                      1792133707.396647,
                      1792133707.4850912,
                      1792133707.6596844,
                      1792133707.7991338,
                      1792133720.0205016,
                      1792133720.0253708,
                      1792133720.0619109,
                      1792133720.2649517,
                      1792133720.3958883,
                      1792133732.6134722,
                      1792133732.621166,
                      1792133732.652058,
                      1792133732.7984784,
                      1792133732.8483498,
                      1792133745.0742764,
                      1792133745.0834024,
                      1792133745.1694627,
                      1792133745.2963657,
                      1792133745.454419,
                      1792133757.6789079,
                      1792133757.689948,
                      1792133757.786407,
                      1792133757.9965656,
                      1792133758.0500243,
                      1792133770.297222,
                      1792133770.30972,
                      1792133770.3462965,
                      1792133770.490744,
                      1792133770.6221168],
           'blocked': False,
           'interval': 12.2,
           'completed': 1792133770.6517603,
           'page_interval': 30,
           'last_inventory_completed': 1792133643.939192,
           'rate_limit_strikes': 0,
           'rate_limit_until': 0,
           'legacy_rate_history_unknown': True,
           'in_flight': False,
           'flight_id': '828ade9d378b70133eb340b614dcb89a',
           'flight_started': 1792133758.1030521,
           'flight_kind': 'policy',
           'completed_flight_id': '828ade9d378b70133eb340b614dcb89a',
           'request_id': 'b6243ae8cf025ccee4c7aee2e6f1b9c1',
           'request_started': 1792133770.297222,
           'request_inventory': False,
           'completed_request_id': 'b6243ae8cf025ccee4c7aee2e6f1b9c1',
           'request_outcome': 'response_assessed'},
 'legacy_provenance_sha256': '960f9cc9a7ae50ae74b79f4a31adfbe61207e5f0348df296440029c46b194469'}
_FIRST_HOSTED_PENDING = False

_LEGACY_JOB_PINS = {
    "37882135443": {
        "attempt": 1,
        "conclusion": "success",
        "head": "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb",
        "jobs": [
            {
                "completed_at": "2026-10-09T04:08:37Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb",
                "id": 113663946422,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 37882135443,
                "started_at": "2026-10-09T04:03:56Z",
                "status": "completed",
            }
        ],
    },
    "37882150145": {
        "attempt": 1,
        "conclusion": "success",
        "head": "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb",
        "jobs": [
            {
                "completed_at": "2026-10-09T04:13:16Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "c96e27b566a8cf8ee2b8f33515b5a027e4b108fb",
                "id": 113665087302,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 37882150145,
                "started_at": "2026-10-09T04:08:39Z",
                "status": "completed",
            }
        ],
    },
    "37952850316": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "jobs": [
            {
                "completed_at": "2026-10-09T15:40:01Z",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
                "id": 113895810774,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 37952850316,
                "started_at": "2026-10-09T15:35:21Z",
                "status": "completed",
            }
        ],
    },
    "38008639066": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "029a500dbf22c1d44ae592c667f26f33d9d364c8",
        "jobs": [
            {
                "completed_at": "2026-10-10T00:25:26Z",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "029a500dbf22c1d44ae592c667f26f33d9d364c8",
                "id": 114083219671,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38008639066,
                "started_at": "2026-10-10T00:20:45Z",
                "status": "completed",
            }
        ],
    },
    "38039085123": {
        "attempt": 1,
        "conclusion": "success",
        "head": "c55b8d99378274467f2d9a290967dbe10bc25031",
        "jobs": [
            {
                "completed_at": "2026-10-10T08:51:49Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "c55b8d99378274467f2d9a290967dbe10bc25031",
                "id": 114175558506,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38039085123,
                "started_at": "2026-10-10T08:47:10Z",
                "status": "completed",
            }
        ],
    },
    "38039092030": {
        "attempt": 1,
        "conclusion": "cancelled",
        "head": "c55b8d99378274467f2d9a290967dbe10bc25031",
        "jobs": [],
    },
    "38039389005": {
        "attempt": 1,
        "conclusion": "success",
        "head": "c55b8d99378274467f2d9a290967dbe10bc25031",
        "jobs": [
            {
                "completed_at": "2026-10-10T08:58:50Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "c55b8d99378274467f2d9a290967dbe10bc25031",
                "id": 114176710837,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38039389005,
                "started_at": "2026-10-10T08:54:11Z",
                "status": "completed",
            }
        ],
    },
    "38040173823": {
        "attempt": 1,
        "conclusion": "success",
        "head": "c55b8d99378274467f2d9a290967dbe10bc25031",
        "jobs": [
            {
                "completed_at": "2026-10-10T09:10:24Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "c55b8d99378274467f2d9a290967dbe10bc25031",
                "id": 114178663044,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38040173823,
                "started_at": "2026-10-10T09:05:45Z",
                "status": "completed",
            }
        ],
    },
    "38071840310": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "jobs": [
            {
                "completed_at": "2026-10-10T18:01:19Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
                "id": 114276265582,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38071840310,
                "started_at": "2026-10-10T17:55:58Z",
                "status": "completed",
            }
        ],
    },
    "38077675430": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
        "jobs": [
            {
                "completed_at": "2026-10-10T18:59:11Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "a97e28475634b9ff61489a11cd6378c9dafc67ae",
                "id": 114287950560,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38077675430,
                "started_at": "2026-10-10T18:54:33Z",
                "status": "completed",
            }
        ],
    },
    "38096574324": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "completed_at": "2026-10-10T23:58:17Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "id": 114343586788,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38096574324,
                "started_at": "2026-10-10T23:53:38Z",
                "status": "completed",
            }
        ],
    },
    "38127979961": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "completed_at": "2026-10-11T09:24:34Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "id": 114436221244,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38127979961,
                "started_at": "2026-10-11T09:19:54Z",
                "status": "completed",
            }
        ],
    },
    "38167019020": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "completed_at": "2026-10-11T19:17:01Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "id": 114551578858,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38167019020,
                "started_at": "2026-10-11T19:12:20Z",
                "status": "completed",
            }
        ],
    },
    "38186427895": {
        "attempt": 1,
        "conclusion": "success",
        "head": "10d99f8ef021c092851bf189ff67307865b8760c",
        "jobs": [
            {
                "completed_at": "2026-10-12T00:05:55Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
                "id": 114609617402,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38186427895,
                "started_at": "2026-10-12T00:01:15Z",
                "status": "completed",
            }
        ],
    },
    "38232255684": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "completed_at": "2026-10-12T09:53:09Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "id": 114754308193,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38232255684,
                "started_at": "2026-10-12T09:48:28Z",
                "status": "completed",
            }
        ],
    },
    "38315661155": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "completed_at": "2026-10-12T22:23:09Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "id": 115032302522,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38315661155,
                "started_at": "2026-10-12T22:18:29Z",
                "status": "completed",
            }
        ],
    },
    "38378632452": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "completed_at": "2026-10-13T09:50:28Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "id": 115233208153,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38378632452,
                "started_at": "2026-10-13T09:45:49Z",
                "status": "completed",
            }
        ],
    },
    "38462408271": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "completed_at": "2026-10-13T20:30:20Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "id": 115521724514,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38462408271,
                "started_at": "2026-10-13T20:25:41Z",
                "status": "completed",
            }
        ],
    },
    "38487362016": {
        "attempt": 1,
        "conclusion": "success",
        "head": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "jobs": [
            {
                "completed_at": "2026-10-14T00:02:20Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
                "id": 115604127222,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38487362016,
                "started_at": "2026-10-13T23:57:43Z",
                "status": "completed",
            }
        ],
    },
    "38532420327": {
        "attempt": 1,
        "conclusion": "success",
        "head": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "jobs": [
            {
                "completed_at": "2026-10-14T08:07:48Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
                "id": 115747177085,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38532420327,
                "started_at": "2026-10-14T08:03:10Z",
                "status": "completed",
            }
        ],
    },
    "38534734859": {
        "attempt": 1,
        "conclusion": "success",
        "head": "1e210c2ef6565b5c055bd073490184f0a465096b",
        "jobs": [
            {
                "completed_at": "2026-10-14T08:41:17Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "1e210c2ef6565b5c055bd073490184f0a465096b",
                "id": 115759690336,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38534734859,
                "started_at": "2026-10-14T08:35:56Z",
                "status": "completed",
            }
        ],
    },
    "38577316876": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "completed_at": "2026-10-14T14:09:30Z",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "id": 115897734976,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38577316876,
                "started_at": "2026-10-14T14:04:51Z",
                "status": "completed",
            }
        ],
    },
    "38619622530": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "jobs": [
            {
                "completed_at": "2026-10-14T19:02:32Z",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
                "id": 116043436871,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38619622530,
                "started_at": "2026-10-14T18:57:52Z",
                "status": "completed",
            }
        ],
    },
    "38638817273": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a3a41a05d4965d11b560f64271c5a6e3af1467be",
        "jobs": [
            {
                "completed_at": "2026-10-14T21:23:45Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "a3a41a05d4965d11b560f64271c5a6e3af1467be",
                "id": 116110007913,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38638817273,
                "started_at": "2026-10-14T21:18:07Z",
                "status": "completed",
            }
        ],
    },
    "38654336148": {
        "attempt": 1,
        "conclusion": "success",
        "head": "e1a68725fe923582b5640481e29c7166eb77397c",
        "jobs": [
            {
                "completed_at": "2026-10-14T23:33:54Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "e1a68725fe923582b5640481e29c7166eb77397c",
                "id": 116160409803,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38654336148,
                "started_at": "2026-10-14T23:28:58Z",
                "status": "completed",
            }
        ],
    },
    "38684236068": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "completed_at": "2026-10-15T06:56:03Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "id": 116289102061,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38684236068,
                "started_at": "2026-10-15T06:51:03Z",
                "status": "completed",
            }
        ],
    },
    "38700825643": {
        "attempt": 1,
        "conclusion": "success",
        "head": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "jobs": [
            {
                "completed_at": "2026-10-15T07:56:05Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
                "id": 116310668256,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38700825643,
                "started_at": "2026-10-15T07:51:08Z",
                "status": "completed",
            }
        ],
    },
    "38720083846": {
        "attempt": 1,
        "conclusion": "success",
        "head": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "jobs": [
            {
                "completed_at": "2026-10-15T11:03:39Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
                "id": 116385074472,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38720083846,
                "started_at": "2026-10-15T10:57:49Z",
                "status": "completed",
            }
        ],
    },
    "38721259420": {
        "attempt": 1,
        "conclusion": "success",
        "head": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "jobs": [
            {
                "completed_at": "2026-10-15T10:40:32Z",
                "conclusion": "success",
                "head_branch": "main",
                "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
                "id": 116376105988,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38721259420,
                "started_at": "2026-10-15T10:34:46Z",
                "status": "completed",
            }
        ],
    },
    "38763419485": {
        "attempt": 1,
        "conclusion": "failure",
        "head": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "jobs": [
            {
                "completed_at": "2026-10-15T15:56:54Z",
                "conclusion": "failure",
                "head_branch": "main",
                "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
                "id": 116520331234,
                "name": "collect",
                "run_attempt": 1,
                "run_id": 38763419485,
                "started_at": "2026-10-15T15:47:30Z",
                "status": "completed",
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
                    path = os.environ.get("BG_FULBRIGHT_PACING_ARTIFACT_PATH")
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
        limit -= 480
    elif name == "restore":
        limit -= 510
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
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_nlink < 2
                or (info.st_dev, info.st_ino) != (linked.st_dev, linked.st_ino)
            ):
                raise AdapterError("Unsafe publisher pacing directory", "access")
            _STATE_INITIALIZING = created
            _STATE_DIR_FD = descriptor
            _STATE_DIR_ID = (info.st_dev, info.st_ino)
            directory = os.path.join(temporary, leaf)
            _STATE_PATH = os.path.join(directory, "state.json")
            _STATE_LOCK = os.path.join(directory, "state.lock")
            verify_state_directory()
        except BaseException:
            journal_failure()
            if _STATE_DIR_FD == descriptor:
                _STATE_DIR_FD = _STATE_DIR_ID = None
                _STATE_PATH = _STATE_LOCK = None
                _STATE_INITIALIZING = False
            with contextlib.suppress(BaseException):
                os.close(descriptor)
            raise
    finally:
        primary_error = sys.exc_info()[0] is not None
        try:
            os.close(parent)
        except BaseException:
            journal_failure()
            if not primary_error:
                raise


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


def save_budget(state):
    """Commit through the held inode; interrupted rewrites fail closed."""
    global _STATE_INITIALIZING, _PUBLISHER_GATE_FAILED, _FAILED_BUDGET
    global _JOURNAL_CREATED, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Fulbright Bulgaria state persistence is already unhealthy",
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
                "Unproved Fulbright Bulgaria completion write", "access"
            )
        payload = json.dumps(state, separators=(",", ":")).encode()
        if len(payload) > 16384 or not _JOURNAL_LOCKS:
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
    verify_state_directory()
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=_STATE_DIR_FD
    )
    try:
        info = os.fstat(descriptor)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe publisher state file", "access")
        return descriptor
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            os.close(descriptor)
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
        raise AdapterError(
            "Bounded Fulbright Bulgaria execution required", "access"
        )
    return _PHASE_END if _PHASE_END is not None else nonexport_end()


def check_deadline():
    if _RUN_END is None:
        raise AdapterError(
            "Bounded Fulbright Bulgaria execution required", "access"
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
    result = workflow_api("/actions/workflows/fetch-bg-fulbright-bulgaria.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path")
        != ".github/workflows/fetch-bg-fulbright-bulgaria.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "Fulbright Bulgaria workflow is not serialized for this mode",
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
            "fetch-bg-fulbright-bulgaria.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable Fulbright Bulgaria workflow head",
                "access",
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("BG_FULBRIGHT_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native Fulbright Bulgaria MAX approval required", "access"
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
                r"https://github\.com/YouthOpps/data-pipeline/issues/314"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native Fulbright Bulgaria approval", "access"
        ) from None


def restored_state():
    """Newest exact artifact is mandatory after the one reviewed legacy set."""
    latest = latest_family_run()
    if latest is None:
        raise AdapterError(
            "Fulbright Bulgaria family history missing", "access"
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
            "Incomplete Fulbright Bulgaria artifact inventory", "access"
        )
    matches = [
        a
        for a in artifacts
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError(
            "Newest Fulbright Bulgaria artifact unavailable", "access"
        )
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
        raise AdapterError(
            "Invalid Fulbright Bulgaria artifact binding", "access"
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
            "Owned Fulbright Bulgaria artifact is unsettled", "access"
        )
    budget_file()
    if _FIRST_HOSTED_PENDING and not _STATE_INITIALIZING:
        raise AdapterError(
            "First-hosted namespace was not exclusively created", "access"
        )
    if _COLLECTION_LOCK_FD is None:
        descriptor = secure_file(
            "collection.lock",
            os.O_RDWR | (os.O_CREAT if _STATE_INITIALIZING else 0),
        )
        try:
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
        except BaseException:
            journal_failure()
            with contextlib.suppress(BaseException):
                os.close(descriptor)
            raise
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
                "Another Fulbright Bulgaria collector is active", "access"
            ) from None
        _COLLECTION_LOCK_ID = identity
        _COLLECTION_LOCK_FD = descriptor
    with locked_budget():
        now = time.time()
        local = load_budget()
        if local["in_flight"]:
            raise AdapterError(
                "Local Fulbright Bulgaria flight is unsettled", "access"
            )
        combined = merge_budgets(local, incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        save_budget(combined)
    _COLLECTION_STARTED = time.time()
    _RESTORED = _STATE_TRUSTED = True


def collect():
    """Run the unchanged complete programme parser after durable restoration."""
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
            return collect_programme()
    except PhaseExpired:
        raise AdapterError(
            "Fulbright Bulgaria collection phase exhausted", "fetch"
        ) from None


def export_family_artifact():
    path = os.environ.get("BG_FULBRIGHT_PACING_ARTIFACT_PATH")
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
                "Unsettled Fulbright Bulgaria flight cannot be exported",
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
    if (
        not os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GITHUB_WORKFLOW_REF")
        != (
            "YouthOpps/data-pipeline/.github/workflows/fetch-"
            + SOURCE_ID
            + ".yml@refs/heads/main"
        )
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise AdapterError("Untrusted current hosted workflow proof", "access")
    expected = (run_id, attempt, SOURCE_ID, head)
    run = observe_current_run(expected)
    if (
        run.get("status") != "in_progress"
        or os.environ.get("GITHUB_JOB") != "collect"
    ):
        raise AdapterError(
            "Fulbright Bulgaria current job is not executing", "access"
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
            "Fulbright Bulgaria current job inventory incomplete", "access"
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
        or job.get("workflow_name") != "fetch-" + SOURCE_ID
        or "conclusion" not in job
        or job.get("conclusion") is not None
        or type(job.get("runner_id")) is not int
        or job["runner_id"] <= 0
    ):
        raise AdapterError(
            "Fulbright Bulgaria current job ownership mismatch", "access"
        )
    try:
        started = datetime.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.utcoffset() != timedelta(0):
            raise ValueError("Missing UTC job timezone")
        elapsed = time.time() - started.timestamp()
        if not -1 <= elapsed < 1800 - EXPORT_TIMEOUT:
            raise ValueError("Job budget exhausted")
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AdapterError(
            "Untrusted Fulbright Bulgaria job clock", "access"
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
        ) == "fulbright.bg"
    except (TypeError, ValueError):
        return False


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower()
        not in {"fulbright.bg", "www.fulbright.bg"}
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError(
            "Unreviewed Fulbright Bulgaria source route", "access"
        )
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


_INVENTORY_KEYS = frozenset(
    {
        "home",
        "entry",
        "incoming",
        "outgoing",
        "other",
        "news",
        "conferences",
        "conference2",
    }
)
_INVENTORY_ACTIVE = False


def is_inventory(url):
    """Classify only exact original collection/discovery page purposes."""
    return url in {PAGES[key] for key in _INVENTORY_KEYS}


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
        with response:
            status, response_headers = response.status, response.headers
            record_retry_after(response_headers.get("Retry-After"), status)
            if status == 429:
                # PolicyStop is assessed only after the response closes safely.
                raise PolicyStop(
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
        (_INVENTORY_ACTIVE or is_inventory(url))
        if inventory is None
        else inventory
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
    if args.publish and os.environ.get("GITHUB_ACTIONS") != "true":
        raise AdapterError("Hosted publication context required", "publish")
    with execution():
        try:
            # Read-only job authentication occurs inside prepare before restore;
            # original proof writes its legacy GitHub bucket only AFTER restore.
            with phase("restore", 180):
                prepare_collection()
            if args.publish:
                with phase("proof", 30):
                    window = original_publication_window()
                _RUN_END = min(_RUN_END, window["read"])
                check_deadline()
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
            "/actions/workflows/fetch-bg-fulbright-bulgaria.yml/runs"
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
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return hosted_transition_state()
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/314"
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
    original = verify_legacy_original(approval["legacy_original"])
    state = validate_budget(approval["state"])
    if (
        not set(original["starts"]).issubset(state["starts"])
        or state["interval"] < original["interval"]
        or state["not_before"] < original["until"]
        or not state["legacy_rate_history_unknown"]
        or state["in_flight"]
        or not set(bounds).issubset(state["starts"])
        or state["completed"] < max(bounds)
        or state["last_inventory_completed"] < max(bounds)
        or state["not_before"] < max(bounds) + 60
    ):
        raise AdapterError("Legacy complete conservative MAX missing", "access")
    return state


@durable_transaction("policy")
def page(url):
    """Keep original document/rights assessment pending on its exact purpose."""
    global _INVENTORY_ACTIVE
    keys = [key for key, known in PAGES.items() if known == url]
    if len(keys) != 1:
        raise AdapterError("Unreviewed complete source page", "access")
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = keys[0] in _INVENTORY_KEYS
    try:
        root = original_page(url)
        legal_facts(keys[0], root)
        return root
    finally:
        _INVENTORY_ACTIVE = previous


def verify_legacy_original(expected):
    """Read only the exact approved old publisher inode, retaining all facts."""
    if not isinstance(expected, dict) or set(expected) != {
        "parent_identity",
        "publisher_identity",
        "raw_sha256",
        "state",
    }:
        raise AdapterError("Finite legacy publisher pins required", "access")
    parent_path = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    name = hashlib.sha256(b"fulbright.bg").hexdigest()
    parent = descriptor = None

    def identity(info):
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

    try:
        before = os.stat(parent_path, follow_symlinks=False)
        if (
            not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o700
            or [
                before.st_dev,
                before.st_ino,
                before.st_uid,
                stat.S_IMODE(before.st_mode),
            ]
            != expected["parent_identity"]
        ):
            raise ValueError("Legacy parent changed")
        parent = os.open(
            parent_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        if (os.fstat(parent).st_dev, os.fstat(parent).st_ino) != (
            before.st_dev,
            before.st_ino,
        ):
            raise ValueError("Legacy parent changed")
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or list(identity(info)) != expected["publisher_identity"]
            or identity(info)
            != identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
        ):
            raise ValueError("Legacy publisher changed")
        raw = os.read(descriptor, 16385)
        if (
            len(raw) > 16384
            or hashlib.sha256(raw).hexdigest() != expected["raw_sha256"]
        ):
            raise ValueError("Legacy bytes changed")
        after = os.stat(parent_path, follow_symlinks=False)
        if (
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or identity(os.fstat(descriptor)) != identity(info)
            or identity(os.stat(name, dir_fd=parent, follow_symlinks=False))
            != identity(info)
        ):
            raise ValueError("Legacy generation changed during read")
        state = json.loads(raw)
        if (
            state != expected["state"]
            or set(state) != {"starts", "interval", "until"}
            or not isinstance(state["starts"], list)
            or len(state["starts"]) > 512
            or any(not numeric(x) for x in state["starts"])
            or not numeric(state["interval"])
            or state["interval"] < 6
            or not numeric(state["until"])
        ):
            raise ValueError("Unsupported actual legacy constraints")
        return state
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise AdapterError(
            "Approved actual legacy publisher unavailable", "access"
        ) from error
    finally:
        for fd in (descriptor, parent):
            if fd is not None:
                os.close(fd)


def hosted_transition_state():
    """Require a separate exact full-native first-hosted approval, never pins."""
    global _FIRST_HOSTED_PENDING
    approval = _HOSTED_TRANSITION
    if not isinstance(approval, dict) or set(approval) != {
        "schema",
        "family",
        "evidence",
        "history_sha256",
        "state",
        "legacy_provenance_sha256",
    }:
        raise AdapterError("Separate first-hosted approval required", "access")
    digest = hashlib.sha256(
        json.dumps(
            _LEGACY_JOB_PINS, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    local = _LEGACY_TRANSITION
    if (
        not isinstance(local, dict)
        or set(local)
        != {
            "schema",
            "family",
            "evidence",
            "history_sha256",
            "state",
            "legacy_original",
        }
        or type(local["schema"]) is not int
        or local["schema"] != 1
        or local["family"] != FAMILY
        or local["history_sha256"] != digest
        or not isinstance(local["legacy_original"], dict)
        or not isinstance(local["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/314"
            r"#issuecomment-[1-9][0-9]*",
            local["evidence"],
        )
    ):
        raise AdapterError("Original LOCAL provenance required", "access")
    provenance = hashlib.sha256(
        json.dumps(
            local["legacy_original"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if (
        type(approval["schema"]) is not int
        or approval["schema"] != 1
        or approval["family"] != FAMILY
        or approval["history_sha256"] != digest
        or approval["legacy_provenance_sha256"] != provenance
        or not isinstance(approval["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/314"
            r"#issuecomment-[1-9][0-9]*",
            approval["evidence"],
        )
    ):
        raise AdapterError("Untrusted first-hosted approval", "access")
    # Fresh actual current-job auth has already completed before restored_state.
    current_run_identity()
    if not os.environ.get("GITHUB_TOKEN"):
        raise AdapterError("Authentic hosted metadata token required", "access")
    actual = {str(row[2]): row for row in _HISTORY_COMPLETED}
    jobless = {str(row[0]): row for row in _HISTORY_JOBLESS}
    if set(actual) | set(jobless) != set(_LEGACY_JOB_PINS):
        raise AdapterError("First-hosted legacy history changed", "access")
    bounds = []
    for key, pin in _LEGACY_JOB_PINS.items():
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
            raise AdapterError("First-hosted exact jobs changed", "access")
        artifacts = workflow_api(f"/actions/runs/{key}/artifacts?per_page=100")
        if (
            artifacts.get("total_count") != 0
            or artifacts.get("artifacts") != []
        ):
            raise AdapterError("First-hosted history has owned state", "access")
        bounds.extend(
            datetime.fromisoformat(
                j["completed_at"].replace("Z", "+00:00")
            ).timestamp()
            for j in pin["jobs"]
        )
    state = validate_budget(approval["state"])
    legacy_seed = validate_budget(local["state"])
    envelope = json.loads(
        os.environ.get("BG_FULBRIGHT_PACING_MAINTENANCE", "null")
    )
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"schema", "family", "evidence", "state"}
        or type(envelope["schema"]) is not int
        or envelope["schema"] != 1
        or envelope["family"] != FAMILY
        or envelope["evidence"] != approval["evidence"]
        or envelope["state"] != state
        or state["in_flight"]
        or not state["legacy_rate_history_unknown"]
        or legacy_seed["in_flight"]
        or legacy_seed["blocked"]
        and not state["blocked"]
        or any(
            state[key] < legacy_seed[key]
            for key in (
                "observed_at",
                "not_before",
                "interval",
                "completed",
                "page_interval",
                "last_inventory_completed",
                "rate_limit_strikes",
                "rate_limit_until",
            )
        )
        or not set(legacy_seed["starts"]).issubset(state["starts"])
        or not set(
            bounds + local["legacy_original"]["state"]["starts"]
        ).issubset(state["starts"])
        or state["interval"] < local["legacy_original"]["state"]["interval"]
        or state["not_before"] < local["legacy_original"]["state"]["until"]
    ):
        raise AdapterError("Exact full-native hosted MAX missing", "access")
    path = os.path.join(tempfile.gettempdir(), FAMILY + "-" + str(os.getuid()))
    if _STATE_PATH is not None or os.path.lexists(path):
        raise AdapterError("First-hosted namespace is not absent", "access")
    _FIRST_HOSTED_PENDING = True
    return state


if __name__ == "__main__":
    raise SystemExit(main())
