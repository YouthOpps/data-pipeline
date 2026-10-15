"""Discover and extract official Fulbright Bulgaria awards and training."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import sys
import tempfile
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


def page(url):
    document = fetch_source(url, min_interval=check_robots(url))
    if not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$",
        document,
        re.IGNORECASE,
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    root = PublisherHTML(document).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(
        r"just a moment|access denied|attention required|checking your"
        r" browser",
        title,
    ):
        raise AdapterError("Publisher access challenge", "access")
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
    banner = " ".join(n.text() for n in modules(root)[:5])
    if re.search(
        r"competition is (?:now )?closed|currently"
        r" closed|конкурсът.*(?:приключ|затворен)|fully booked|completed",
        banner,
        re.I,
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
    return {key: page(url) for key, url in PAGES.items()}


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
        records.append(record)
    validate_records(records)
    return records


def collect():
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


def pace(url, interval=6):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
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
            if ready <= now:
                budget["starts"].append(now)
                save_budget(state, budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


def request_bytes(
    url, *, interval=6, method="GET", headers=None, payload=None
):
    state, budget = pace(url, interval)
    request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers=request_headers,
        )
        try:
            response = _OPENER.open(request, timeout=30)
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
                    save_budget(state, budget)
                except (ValueError, TypeError, OverflowError):
                    pass
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            return response.status, response.headers, body
    finally:
        state.close()


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


def main():
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
                        "Run could not complete; durable status unavailable"
                    ),
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
