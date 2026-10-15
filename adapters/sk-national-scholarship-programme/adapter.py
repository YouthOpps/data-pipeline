"""Collect official National Scholarship Programme mobility awards."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import ipaddress
import io
import json
import math
import os
import re
import sys
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "sk-national-scholarship-programme"
SOURCE_URL = "https://www.scholarships.sk/en/main/o-programe"
WEBSITE_URL = (
    "https://www.scholarships.sk/en/main/programme-terms-and-conditions/"
)
LANGUAGE = "en"
PUBLISHER_COUNTRY = "SK"
PUBLISHER_TYPE = "nonprofit"
ATTRIBUTION = "SAIA, n. o. – www.saia.sk"


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


PAGES = {
    "about": SOURCE_URL,
    "home": "https://www.scholarships.sk/en",
    "terms": WEBSITE_URL,
    "incoming": (
        "https://www.scholarships.sk/en/main/programme-terms-and-conditions/"
        "foreign-applicants"
    ),
    "outgoing": (
        "https://www.scholarships.sk/en/main/programme-terms-and-conditions/"
        "applicants-from-slovakia-2023"
    ),
    "call": (
        "https://www.scholarships.sk/en/news/"
        "call-sum-sem-acad-yr-26/27-apply-now"
    ),
    "outgoing-detail": (
        "https://www.stipendia.sk/sk/main/podmienky-pre-predkladanie-ziadosti/"
        "uchadzaci-zo-slovenska/"
    ),
    "outgoing-rates": (
        "https://www.stipendia.sk/sk/main/podmienky-pre-predkladanie-ziadosti/"
        "uchadzaci-zo-slovenska/vyska-stipendia/"
    ),
    "slovak-home": "https://www.stipendia.sk/sk/",
    "outgoing-call": (
        "https://www.stipendia.sk/sk/aktuality/" "vyzva-ls-ar-26/27-otvorena"
    ),
    "legal": (
        "https://www.saia.sk/en/main/about-us-main/"
        "legal-information-and-data-protection/"
    ),
}
TERMS_PDF = (
    "https://www.saia.sk/_user/documents/SAIA/pravne-info/"
    "VZP-SAIA-weby2018-SK.pdf"
)
CONDITIONS = {
    "about": "5b18d8ed26c3a175025571cc4377e8a26c63d98abf40331ac35d6a81ebeed0e2",
    "home": "226e794e4b96e6e602d3614dd855ddd2aa2091f9267ef2f3465a143e161a8ba9",
    "terms": "4d9401831c800cd2b2316d6bb44cfc58dba2848f0f60dc43eb3671ad24dcb2fa",
    "incoming": "548adc0641f83da5e2dfcbecb1cb576d10c41223e8cb6081815563b86244ae7c",
    "outgoing": "6511ca9fdb6a5d90a026c9ff462d8359466bfe64c64a50383175249f840b23ba",
    "call": "d1c42a6e310c67867cd7128900d7324e29a0ad93a99aee987e68e66f9cbc9256",
    "outgoing-detail": "08a279bb22d6b27a4fc15f443c26e0e12bd289a6a9b01b35fbcfad35a46fe68f",
    "outgoing-rates": "21259996c085d5421b853bf6f332c734b95ea9a7780e41c7a0830c0f8367c82c",
    "slovak-home": "ee62390ddd153a60bc1daf540953ac615bd1a7f6091d23357f6f1e7f8bc600b8",
    "outgoing-call": "ccdb2736ab17038b28943181dd9251e6280c346ed83301353d419ccb64790c9c",
    "legal": "62d8dbf97292692b0313261c796b72d88558752f810be76e10f4bdb6a191fdb9",
}
TERMS_HASH = "79426e09e2fa247e7a3e7dcfee669ad298b9eace24576c0e198582a90a8bb826"

DEADLINE = "2026-10-31T15:00:00Z"


def content(root):
    """Select the actual article, excluding menus and personal testimonials."""
    return one(nodes(root, "div", "content-sm"), "NSP factual article")


def source_facts(key, root):
    if key in ("home", "slovak-home"):
        target = PAGES["call" if key == "home" else "outgoing-call"]
        base = one(nodes(root, "base"), "publisher base URL").attrs.get("href")
        link = one(
            [
                n
                for n in nodes(root, "a")
                if urllib.parse.urljoin(base, n.attrs.get("href", "")) == target
            ],
            "current dated call link",
        )
        return link.text() + " " + target
    if key == "legal":
        return one(
            [n for n in root.walk() if n.attrs.get("id") == "contentcol"],
            "SAIA terms article",
        ).text()
    return content(root).text()


def check_terms_policy():
    """Verify the reviewed binding public terms, without publishing the PDF."""
    delay = check_robots(TERMS_PDF)
    status, _, document = request_bytes(TERMS_PDF, interval=delay)
    if status != 200 or hashlib.sha256(document).hexdigest() != TERMS_HASH:
        raise AdapterError(
            "SAIA binding terms changed or unavailable", "access"
        )


def read_pages():
    """Read the complete two-direction programme and current call evidence."""
    pages = {}
    for key, url in PAGES.items():
        root = page(url)
        if (
            hashlib.sha256(source_facts(key, root).encode()).hexdigest()
            != CONDITIONS[key]
        ):
            raise AdapterError("NSP source facts changed: " + key, "parse")
        pages[key] = root
    check_terms_policy()
    return pages


def add_track(
    records,
    pages,
    direction,
    track,
    anchor,
    title,
    summary,
    facts,
    detail,
    extra=(),
):
    """Retain one actual applicant category within the source-proven round."""
    key = "incoming" if direction == "incoming" else "outgoing-detail"
    root = content(pages[key])
    text = root.text()
    if not any(n.attrs.get("id") == anchor for n in root.walk()):
        raise AdapterError("Missing actual applicant category anchor", "parse")
    evidence = []
    for fact in facts:
        match = re.search(re.escape(fact), text, re.IGNORECASE)
        if not match:
            raise AdapterError("Missing NSP category fact: " + track, "parse")
        evidence.append(text[match.start() : match.end()])
    evidence.extend(detail)
    evidence.extend(extra)
    evidence.extend(
        [
            "Actual 2026/27 call: "
            + PAGES["call" if direction == "incoming" else "outgoing-call"],
            "Deadline 31 October 2026 at 16:00 CET (fixed UTC+01); "
            "application deadline is distinct from document delivery "
            "milestones.",
            "Source attribution: " + ATTRIBUTION + "; retrieval time is "
            "last_checked_at (UTC), also retained in source metadata.",
        ]
    )
    record = make_record(
        title,
        PAGES[key] + "#" + anchor,
        ["scholarships"],
        host_countries=["SK"] if direction == "incoming" else [],
        evidence=evidence,
    )
    record["id"] = hashlib.sha256(
        f"{SOURCE_ID}|{direction}|{track}|2026-10-31".encode()
    ).hexdigest()[:24]
    record["summary"] = summary
    record["deadline"] = DEADLINE
    record["status"] = (
        "expired"
        if datetime.now(timezone.utc)
        >= datetime.fromisoformat(DEADLINE.replace("Z", "+00:00"))
        else "open"
    )
    records.append(record)


def outgoing_funding(pages):
    """Keep country-specific amount cells with their real applicant columns."""
    table = one(
        nodes(content(pages["outgoing-rates"]), "table"),
        "outgoing monthly funding table",
    )
    rows = []
    for row in nodes(table, "tr"):
        cells = [
            n.text()
            for n in row.children
            if isinstance(n, Node) and n.tag in ("td", "th")
        ]
        if len(cells) == 4:
            if not all(re.fullmatch(r"[\d ]+ EUR", x) for x in cells[1:]):
                raise AdapterError(
                    "Invalid outgoing monthly funding row", "parse"
                )
            rows.append(cells)
    if len(rows) != 69 or len({r[0] for r in rows}) != 69:
        raise AdapterError(
            "Changed outgoing monthly funding inventory", "parse"
        )
    return [
        [
            "Published monthly rate (" + row[0] + "): " + row[column]
            for row in rows
        ]
        for column in (1, 2, 3)
    ]


def parse_inventory(pages):
    """Normalize all six genuine tracks; changed required facts fail closed."""
    if set(pages) != set(PAGES):
        raise AdapterError("Incomplete two-way NSP inventory", "parse")
    for key, root in pages.items():
        if (
            hashlib.sha256(source_facts(key, root).encode()).hexdigest()
            != CONDITIONS[key]
        ):
            raise AdapterError("NSP source facts changed: " + key, "parse")
    records = []
    incoming_common = [
        "Citizens of any country except Slovakia; no invented positive "
        "citizenship whitelist. Full-degree students in Slovakia excluded.",
        "Exclude applicants who spent at least 15 months in Slovakia during "
        "the prior 36 months and overlapping publicly financed scholarship "
        "stays. Reapplication requires at least two years since last NSP "
        "award/receipt; exceptional force-majeure cases may be considered.",
        "Apply online; original admission/invitation must reach SAIA by noon "
        "on the third Slovak working day after the application deadline. "
        "This secondary delivery requirement is not the application deadline.",
        "Non-EU/EEA/Swiss scholars staying over 90 days who need a temporary "
        "residence permit may receive up to EUR250 reimbursement for the "
        "required medical examination/certificate, with original invoice.",
    ]
    add_track(
        records,
        pages,
        "incoming",
        "student",
        "elapp_stud",
        "NSP incoming university student scholarship – October 2026",
        "Students enrolled outside Slovakia: MA level or at least 2.5 years "
        "completed in the same/similar degree; accepted Slovak academic "
        "mobility, not PhD or full-degree study in Slovakia. EUR620/month "
        "plus optional travel EUR0–1500. Current stay starts February1–April1 "
        "2027, ends by August31; general longer durations may not fit. "
        "Non-Slovak citizenship; 15/36-month residence and public-funding "
        "exclusions, two-year repeat rule. Apply October31,2026 at16:00 CET.",
        ("at least 2.5 years", "620 EUR", "except the citizens of Slovakia"),
        incoming_common
        + [
            "General student durations: one/two full semesters (4–5/9–10 "
            "months) or one–three full trimesters (3–4/6–7/9–10 months); "
            "the current call's February–August 2027 window still governs.",
            "Travel is requested with the scholarship and paid at the end; "
            "distance-based EUR0/50/100/250/350/500/750/1100/1500 tiers "
            "are one component, not separate grants.",
        ],
    )
    add_track(
        records,
        pages,
        "incoming",
        "phd",
        "elapp_phdstud",
        "NSP incoming PhD mobility scholarship – October 2026",
        "PhD study/training outside Slovakia; accepted by a Slovak HEI or "
        "eligible doctoral research institution for 1–10-month academic "
        "mobility. EUR1025.50/month plus optional travel EUR0–1500. Current "
        "stay starts February1–August31,2027, ends by November30. Non-Slovak "
        "citizenship; full-degree study in Slovakia, 15/36-month prior "
        "residence and overlapping public awards excluded; two-year repeat "
        "rule. Apply October31,2026 at16:00 CET.",
        (
            "scientific training takes place outside Slovakia",
            "1025,50 EUR",
            "1 – 10 months",
        ),
        incoming_common
        + [
            "PhD travel grant requested with the scholarship and paid at "
            "the end; distance-based EUR0–1500. Host must be eligible to "
            "carry out a doctoral programme, not simply any institution.",
        ],
    )
    add_track(
        records,
        pages,
        "incoming",
        "academic-artist",
        "elapp_research",
        "NSP incoming teacher/researcher/artist scholarship – October 2026",
        "Invited by certified Slovak non-business research/education host "
        "for 1–10 months. Monthly EUR1025.50 no PhD/under4 years, EUR1370 "
        "PhD/under10 or1470 PhD/over10; equality boundaries unstated. "
        "No PhD/over4 "
        "normally excluded; special over4–7-year cases possible. Non-Slovak "
        "citizenship; prior15/36-month residence/public-award exclusions "
        "and two-year repeat rule. Start February1–August31,2027; finish "
        "November30. Apply October31,2026 at16:00 CET.",
        (
            "valid certificate of eligibility",
            "not a business company",
            "more than 4 years",
            "not more than 7 years",
            "maximum of 6 years",
        ),
        incoming_common
        + [
            "Monthly tiers: no PhD and less than four years' experience "
            "EUR1025.50; PhD and less than ten years EUR1370; PhD and more "
            "than ten years EUR1470. Exactly four/ten year boundaries are "
            "not specified in the table, so no unsupported rate is selected.",
            "No-PhD candidates with more than four years are normally "
            "excluded; administrator/selection-committee discretion for "
            "more than four but at most seven years, at the lowest rate. "
            "PhD studies count as work experience, capped at six years "
            "when calculating the monthly funding tier.",
            "The published student/PhD travel allowance does not apply "
            "to the incoming teacher/researcher/artist category.",
        ],
    )
    funding = outgoing_funding(pages)
    outgoing_common = [
        "Eligible: Slovak citizens irrespective of permanent residence; "
        "EU/EEA/Swiss citizens with residence rights/permanent residence; "
        "third-country nationals with permanent/long-term residence in "
        "Slovakia. These are alternative citizenship/residence routes, "
        "not a Slovak-only citizenship whitelist.",
        "Foreign destinations are generally worldwide; Russia stays are "
        "currently prohibited. Country-specific funding rates are benefit "
        "options, not a closed host-country whitelist or proof of citizenship.",
        "Country-specific monthly support covers living expenses, not "
        "tuition; travel grant EUR0–1500 requested together with the "
        "scholarship and paid with the final installment. No tier clones.",
        "Full degree study abroad and overlapping publicly financed "
        "stays are excluded. Repeat applications require at least two "
        "years since last NSP award/receipt, with exceptional force-majeure "
        "consideration. Return obligations and host acceptance apply.",
        "Current call dated 2026 explicitly covers applicants resident "
        "in Slovakia. Student starts February1–April1,2027, completion "
        "by August31; other listed academics start February1–August31, "
        "completion by November30. General duration maxima do not "
        "override the current round's dates.",
    ]
    for index, track, anchor, title, summary, facts, detail in (
        (
            0,
            "student",
            "opuch_stud",
            "student",
            "MA or fourth-year integrated-degree students at Slovak "
            "HEIs: foreign "
            "study "
            "(one semester/trimester to one academic year) or thesis-related "
            "research/artistic stay (3–6 months). Complete BA/six semesters "
            "before departure and return to finish the Slovak degree. "
            "External students require full-time home-institution employment. "
            "Country-specific living support plus travel; "
            "residence/citizenship "
            "alternatives, not Slovak-only. Russia excluded. Current dates "
            "constrain general maxima. Deadline October31,2026 at16:00 CET.",
            ("diplomovej práce", "6 semestrov", "na plný pracovný úväzok"),
            [
                "Final-year BA applicants must enroll in the Slovak "
                "second-level "
                "degree before departure. External/part-time-format applicants "
                "must also be full-time employees of an eligible home "
                "institution "
                "and return to it after the stay."
            ],
        ),
        (
            1,
            "phd",
            "opuch_dokt",
            "PhD",
            "PhD students at Slovak HEIs, including eligible external "
            "education institutions, may undertake foreign study/research/"
            "artistic mobility for1–10months related to their doctorate. "
            "External-format students require full-time eligible home "
            "employment and return. Country-specific living support, not "
            "tuition, plus travel EUR0–1500; citizenship/residence "
            "alternatives "
            "apply. Russia excluded; current2027 dates constrain duration. "
            "Apply October31,2026 at16:00 CET.",
            ("doktorandi", "1 – 10 mesiacov", "na plný pracovný úväzok"),
            [
                "The foreign study/research/artistic stay must relate to the "
                "doctoral programme and its focus. External-format students "
                "must be full-time employees of an eligible home institution "
                "and return to it."
            ],
        ),
        (
            2,
            "postdoc",
            "opuch_vysk",
            "postdoctoral",
            "Full-time teachers/researchers at certified Slovak non-business "
            "institutions, PhD awarded at most10years ago; exceptional cases "
            ">10–13years possible. Foreign research stay2–6months for career "
            "development, with required home return. Country-specific living "
            "support, not tuition, plus travel EUR0–1500. "
            "Citizenship/residence "
            "alternatives, not Slovak-only; Russia excluded. Current2027 dates "
            "constrain duration. Apply October31,2026 at16:00 CET.",
            ("postdoktorandi", "nie je obchodnou spoločnosťou", "13 rokov"),
            [
                "PhD awarded no more than ten years before the deadline; "
                "administrator/selection-committee discretion may admit more "
                "than ten but no more than thirteen years. Full-time eligible "
                "home employment and return to the home institution required."
            ],
        ),
    ):
        add_track(
            records,
            pages,
            "outgoing",
            track,
            anchor,
            "NSP outgoing " + title + " scholarship – October 2026",
            summary,
            facts,
            outgoing_common + detail,
            funding[index],
        )
    validate_records(records)
    return records


def collect():
    """Collect all genuine current two-direction NSP scholarship tracks."""
    prepare_collection()
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
_RUN_STARTED = time.monotonic()
_STATE_TRUSTED = False
_COLLECTION_LOCK = None
_FAMILY_SOURCES = {SOURCE_ID, "sk-study-in-slovakia"}
_SCANNED_HISTORY = set()
_LEGACY_ATTEMPTS = {
    (37866043357, 1, SOURCE_ID),
    (37865230903, 1, SOURCE_ID),
    (37865212036, 1, SOURCE_ID),
    (38070837752, 1, "sk-study-in-slovakia"),
    (38070842559, 1, "sk-study-in-slovakia"),
}
_LEGACY_HEADS = {
    37866043357: "08df07fee129bbe1c416e0f417df2c727ea423b3",
    37865230903: "d658ce5e28d2684f974556809bbe50d585762139",
    37865212036: "d658ce5e28d2684f974556809bbe50d585762139",
    38070837752: "9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4",
    38070842559: "9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4",
}
_REVIEWED_MIGRATION_NATIVE = {
    "schema": 1,
    "source": "sk-national-scholarship-programme",
    "native_state_sha256": "2711ed565eddd1d9d4e7900797c76a55d99422e79558a092e42e108beaa10088",
    "state": {
        "starts": [
            1792068831.0775485,
            1792068844.5045803,
            1792068858.3347805,
            1792068871.552941,
            1792068885.146197,
        ],
        "interval": 12.1,
        "until": 1792068898.5773716,
        "blocked": False,
        "observed_at": 1792068885.146197,
        "page_gap": 30,
    },
}
_LEGACY_ARTIFACT = (
    "sha256:ba99101b643c8c6f298bab6d19b8e091ed5ec5c6f9747d5ddf0176505fbcfdf4"
)


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


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(budget):
    allowed = {
        "starts",
        "interval",
        "until",
        "blocked",
        "observed_at",
        "rate_limit_until",
        "rate_limit_count",
        "page_gap",
    }
    if (
        not isinstance(budget, dict)
        or not {"starts", "interval", "until"} <= budget.keys()
        or budget.keys() - allowed
        or not isinstance(budget["starts"], list)
        or len(budget["starts"]) > 1000
        or any(not numeric(t) or t > time.time() + 1 for t in budget["starts"])
        or budget["starts"] != sorted(set(budget["starts"]))
        or not numeric(budget["interval"])
        or budget["interval"] < 6
        or not numeric(budget["until"])
        or type(budget.get("blocked", False)) is not bool
        or not numeric(budget.get("observed_at", 0))
        or budget.get("observed_at", 0) > time.time() + 1
    ):
        raise AdapterError("Invalid shared publisher budget", "access")
    for key in ("rate_limit_until", "rate_limit_count", "page_gap"):
        if key in budget and not numeric(budget[key]):
            raise AdapterError("Invalid publisher rate-limit history", "access")
    if "rate_limit_count" in budget and (
        type(budget["rate_limit_count"]) is not int
        or budget["rate_limit_count"] > 512
    ):
        raise AdapterError("Invalid publisher rate-limit count", "access")
    return budget


def lifecycle_remaining():
    remaining = 25 * 60 - (time.monotonic() - _RUN_STARTED)
    if remaining <= 0:
        raise AdapterError("Publisher lifecycle budget exhausted", "access")
    return remaining


def merge_budget(local, incoming):
    merged = {**local, **incoming}
    merged["starts"] = sorted(set(local["starts"] + incoming["starts"]))
    for key in (
        "interval",
        "until",
        "observed_at",
        "page_gap",
        "rate_limit_until",
        "rate_limit_count",
    ):
        if key in local or key in incoming:
            merged[key] = max(local.get(key, 0), incoming.get(key, 0))
    merged["blocked"] = local.get("blocked", False) or incoming.get(
        "blocked", False
    )
    return validate_budget(merged)


def publisher_host(url):
    host = (
        (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    )
    return host in (
        "scholarships.sk",
        "stipendia.sk",
        "saia.sk",
    ) or host.endswith(".saia.sk")


def open_budget(url):
    """Lock both legacy SAIA inodes in the sibling adapter's sorted order."""
    directory = os.path.join(
        tempfile.gettempdir(), "saia-publisher-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    information = os.lstat(directory)
    if (
        not stat.S_ISDIR(information.st_mode)
        or information.st_uid != os.getuid()
        or information.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    keys = ("saia.sk", "scholarships.sk") if publisher_host(url) else (host,)
    files = []
    budget = {
        "starts": [],
        "interval": 6,
        "until": 0,
        "blocked": False,
        "observed_at": time.time(),
    }
    try:
        for key in sorted(hashlib.sha256(k.encode()).hexdigest() for k in keys):
            fd = os.open(
                os.path.join(directory, key),
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
            ):
                os.close(fd)
                raise AdapterError("Unsafe publisher pacing inode", "access")
            file = os.fdopen(fd, "r+", encoding="utf-8")
            files.append(file)
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    lifecycle_remaining()
                    time.sleep(0.25)
            raw = file.read(16385)
            if len(raw) > 16384:
                raise AdapterError("Oversized publisher pacing state", "access")
            try:
                prior = validate_budget(json.loads(raw)) if raw else budget
            except (ValueError, TypeError):
                raise AdapterError(
                    "Invalid publisher pacing JSON", "access"
                ) from None
            budget = merge_budget(budget, prior)
        return files, validate_budget(budget)
    except Exception:
        for file in reversed(files):
            file.close()
        raise


def save_budget(files, budget):
    validate_budget(budget)
    for file in files:
        file.seek(0)
        json.dump(budget, file, separators=(",", ":"))
        file.truncate()
        file.flush()
        os.fsync(file.fileno())


def close_budget(files):
    for file in reversed(files):
        file.close()


def pace(url, interval=6):
    files, budget = open_budget(url)
    try:
        if budget["blocked"]:
            raise AdapterError(
                "Retained publisher refusal requires review", "access"
            )
        source = publisher_host(url)
        if source and (
            budget.get("rate_limit_count", 0) >= 512
            or budget["interval"] > sys.float_info.max / 2
            or budget.get("page_gap", 30) > sys.float_info.max / 2
        ):
            raise AdapterError(
                "Rate-limit numeric range requires review", "access", 429
            )
        if source and budget.get("rate_limit_until", 0) > time.time():
            raise AdapterError(
                "Retained publisher rate-limit pause", "access", 429
            )
        budget["interval"] = max(
            budget["interval"], interval, 12.1 if source else 6.1
        )
        if source:
            budget["page_gap"] = max(budget.get("page_gap", 30), 30)
        allowance = 5 if source else 10
        while True:
            now = time.time()
            budget["starts"] = [t for t in budget["starts"] if now - t < 60]
            ready = max(
                budget["until"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (
                    budget["starts"][-allowance] + 60
                    if len(budget["starts"]) >= allowance
                    else now
                ),
            )
            if ready <= now:
                budget["starts"].append(now)
                budget["observed_at"] = now
                save_budget(files, budget)
                return files, budget
            if ready - now >= lifecycle_remaining():
                raise AdapterError(
                    "Publisher embargo exceeds run budget", "access"
                )
            save_budget(files, budget)
            time.sleep(min(ready - now, 30))
    except Exception:
        close_budget(files)
        raise


def retain_refusal():
    files, budget = open_budget(SOURCE_URL)
    try:
        budget["blocked"] = True
        budget["observed_at"] = time.time()
        save_budget(files, budget)
    finally:
        close_budget(files)


def visible_access_challenge(body):
    """Identify explicit rendered refusals without treating footer scripts as gates."""
    message = re.compile(
        r"^(?:(?:please|kindly)\s+)?(?:verify(?: that)? you are human|"
        r"access denied|forbidden|just a moment|request rejected|attention required)"
        r"(?:\s+(?:to continue|before continuing))?"
        r"(?:[.!:,]\s+.*)?[.!:,\s\d-]*$",
        re.I,
    )
    root = PublisherHTML(body.decode("utf-8", "replace")).root
    refused = False

    def visible(node):
        nonlocal refused
        style = node.attrs.get("style") or ""
        location = (
            (node.attrs.get("id") or "") + " " + (node.attrs.get("class") or "")
        )
        if (
            node.tag
            in {"script", "style", "noscript", "footer", "form", "nav", "aside"}
            or "hidden" in node.attrs
            or (node.attrs.get("aria-hidden") or "").lower() == "true"
            or re.search(
                r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I
            )
            or re.search(r"(?:^|[\s_-])footer(?:$|[\s_-])", location, re.I)
        ):
            return ""
        text = " ".join(
            visible(child) if isinstance(child, Node) else child
            for child in node.children
        )
        text = " ".join(text.split())
        if message.fullmatch(text):
            refused = True
        return text

    visible(root)
    return refused


def request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=5_000_000,
):
    state, budget = pace(url, interval)
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    if parsed.path == "/robots.txt" and (
        host in ("scholarships.sk", "stipendia.sk", "saia.sk")
        or host.endswith(".saia.sk")
    ):
        request_headers["Accept"] = "text/plain, */*;q=0.1"
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers=request_headers,
        )
        if request.get_header("User-agent") != _USER_AGENT:
            raise AdapterError("Crawler identity changed", "access")
        try:
            response = _OPENER.open(
                request, timeout=min(30, lifecycle_remaining())
            )
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retry = response.headers.get("Retry-After")
            retry_seconds = None
            if retry:
                try:
                    if re.fullmatch(r"\d+(?:\.\d+)?", retry):
                        seconds = float(retry)
                    else:
                        instant = parsedate_to_datetime(retry)
                        if instant.tzinfo is None:
                            raise ValueError("Retry-After requires a timezone")
                        seconds = instant.timestamp() - time.time()
                    deadline = time.time() + seconds
                    if (
                        not math.isfinite(seconds)
                        or seconds <= 0
                        or not math.isfinite(deadline)
                    ):
                        raise ValueError("Unusable Retry-After")
                    datetime.fromtimestamp(deadline, timezone.utc)
                    retry_seconds = seconds
                    budget["until"] = max(budget["until"], deadline)
                    save_budget(state, budget)
                except (ValueError, TypeError, OverflowError, OSError):
                    pass
            if publisher_host(url) and response.status == 429:
                count = budget.get("rate_limit_count", 0) + 1
                multiplier = 2**count
                budget["rate_limit_count"] = count
                budget["interval"] = max(
                    budget["interval"] * 2, 12.1 * multiplier
                )
                budget["page_gap"] = max(
                    budget.get("page_gap", 30) * 2, 30 * multiplier
                )
                pause = (
                    retry_seconds
                    if retry_seconds is not None
                    else 1800 * multiplier / 2
                )
                budget["rate_limit_until"] = max(
                    budget.get("rate_limit_until", 0), time.time() + pause
                )
                budget["until"] = max(
                    budget["until"], budget["rate_limit_until"]
                )
                save_budget(state, budget)
                raise AdapterError(
                    "Publisher rate limited collection", "access", 429
                )
            if (
                publisher_host(url)
                and response.status in (401, 403)
                and not robots
            ):
                budget["blocked"] = True
                save_budget(state, budget)
            body = response.read(byte_limit + 1)
            if len(body) > byte_limit:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            if (
                publisher_host(url)
                and not robots
                and response.status == 200
                and visible_access_challenge(body)
            ):
                budget["blocked"] = True
                save_budget(state, budget)
                raise AdapterError(
                    "Publisher returned an access challenge", "access"
                )
            return response.status, response.headers, body
    finally:
        budget["until"] = max(budget["until"], time.time() + budget["interval"])
        try:
            save_budget(state, budget)
        finally:
            close_budget(state)


def fetch_source(url, min_interval=6, *, robots=False):
    original = urllib.parse.urlsplit(url)
    if robots and original.path != "/robots.txt":
        raise AdapterError("Robots retrieval requires robots.txt", "access")

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
                    current, interval=min_interval, robots=robots
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
                    continue
                if robots and 400 <= status < 500 and status != 429:
                    return ""
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
                    "Publisher connection failed: " + safe_error(error), "fetch"
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin not in _ROBOTS:
        _ROBOTS[origin] = fetch_source(origin + "/robots.txt", robots=True)
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
        elif key == "request-rate":
            rate = re.fullmatch(r"([1-9]\d*)/([1-9]\d*)([smhd]?)", value)
            if not rate:
                raise AdapterError("Unsupported robots request rate", "access")
            unit = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[rate[3]]
            group["delay"] = max(
                group["delay"], int(rate[2]) * unit / int(rate[1])
            )
        elif key == "crawl-delay":
            try:
                group["delay"] = max(group["delay"], float(value))
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
            retain_refusal()
            raise AdapterError(
                "Robots policy disallows this source endpoint", "access"
            )
    return max([12.1] + [g["delay"] for policy in policies for g in policy])


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
                    expired = deadline.tzinfo is not None and checked > deadline
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


def workflow_api(path):
    """Read only this workflow's authenticated Actions metadata."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            "https://api.github.com/repos/YouthOpps/data-pipeline" + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 200:
            raise ValueError("Workflow API unavailable")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            "Workflow pacing state unavailable", "access"
        ) from None


def current_run_identity():
    """Bind infrastructure state to the authorized main publisher attempt."""
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "")
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    ]
    if any(not re.fullmatch(r"[1-9]\d*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))


def completed_attempt(run, expected=None, expected_head=None):
    """Validate completed chronology, including older-ID reruns."""
    try:
        identity, attempt = run["id"], run["run_attempt"]
        if (
            type(identity) is not int
            or identity <= 0
            or type(attempt) is not int
            or attempt <= 0
            or identity in _LEGACY_HEADS
            and (
                run["head_sha"] != _LEGACY_HEADS[identity]
                or run.get("conclusion") != "failure"
            )
            or run["status"] != "completed"
            or family_source(run) is None
            or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_branch"] != "main"
            or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            or expected
            and (identity, attempt) != expected
            or expected_head is not None
            and run["head_sha"] != expected_head
        ):
            raise ValueError("Untrusted workflow attempt")
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100"
        )
        jobs = result.get("jobs")
        if (
            not isinstance(jobs, list)
            or not jobs
            or len(jobs) > 100
            or result.get("total_count") != len(jobs)
            or len({job.get("id") for job in jobs}) != len(jobs)
        ):
            raise ValueError("Missing or incomplete exact-attempt jobs")
        if len([job for job in jobs if job.get("name") == "collect"]) != 1:
            raise ValueError("Ambiguous publisher job")
        started, completed = [], []
        for job in jobs:
            if (
                type(job.get("id")) is not int
                or job["id"] <= 0
                or job.get("run_id") != identity
                or job.get("run_attempt", attempt) != attempt
                or job.get("head_sha") != run["head_sha"]
                or job.get("status") != "completed"
            ):
                raise ValueError("Untrusted exact-attempt job binding")
            for field, values in (
                ("started_at", started),
                ("completed_at", completed),
            ):
                value = datetime.fromisoformat(
                    job[field].replace("Z", "+00:00")
                )
                if value.tzinfo is None:
                    raise ValueError("Missing job chronology timezone")
                values.append(value.timestamp())
            if not 0 <= started[-1] <= completed[-1] <= time.time() + 1:
                raise ValueError("Invalid publisher job duration")
        began, finished = min(started), max(completed)
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid exact-attempt job chronology")
        return (
            finished,
            began,
            identity,
            attempt,
            run["head_sha"],
            family_source(run),
        )
    except Exception:
        raise AdapterError(
            "Untrusted completed workflow attempt", "access"
        ) from None


def family_source(run):
    for source in _FAMILY_SOURCES:
        if run.get("path") == ".github/workflows/fetch-" + source + ".yml":
            return source
    return None


def latest_family_attempt():
    """Read both complete source histories, including queued/current reruns."""
    global _SCANNED_HISTORY
    current_id, current_attempt = current_run_identity()
    found_current, candidates, identities = False, [], set()
    for source in sorted(_FAMILY_SOURCES):
        total, seen = None, set()
        for page in range(1, 101):
            result = workflow_api(
                f"/actions/workflows/fetch-{source}.yml/runs?per_page=100&page={page}"
            )
            count, runs = result.get("total_count"), result.get("workflow_runs")
            if (
                type(count) is not int
                or count < 0
                or not isinstance(runs, list)
                or len(runs) > 100
                or total is not None
                and count != total
            ):
                raise AdapterError(
                    "Incomplete or racing family history", "access"
                )
            total = count
            for run in runs:
                identity, attempt = run.get("id"), run.get("run_attempt")
                if (
                    type(identity) is not int
                    or identity <= 0
                    or identity in seen
                    or type(attempt) is not int
                    or attempt <= 0
                    or family_source(run) != source
                    or run.get("repository", {}).get("full_name")
                    != "YouthOpps/data-pipeline"
                    or run.get("head_repository", {}).get("full_name")
                    != "YouthOpps/data-pipeline"
                ):
                    raise AdapterError(
                        "Invalid publisher family history", "access"
                    )
                seen.add(identity)
                if run.get("head_branch") != "main":
                    continue
                if identity == current_id:
                    found_current = True
                    if source != SOURCE_ID or attempt != current_attempt:
                        raise AdapterError(
                            "Current family attempt mismatch", "access"
                        )
                    if attempt == 1:
                        continue
                elif run.get("status") == "completed":
                    candidate = completed_attempt(run)
                    candidates.append(candidate)
                    identities.add((identity, attempt, source))
                    continue
                elif run.get("status") not in {
                    "queued",
                    "waiting",
                    "pending",
                    "requested",
                }:
                    raise AdapterError(
                        "Another SAIA publisher is active", "access"
                    )
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    if family_source(prior) != source:
                        raise AdapterError(
                            "Prior family attempt source mismatch", "access"
                        )
                    candidate = completed_attempt(
                        prior, (identity, attempt - 1), run["head_sha"]
                    )
                    candidates.append(candidate)
                    identities.add((identity, attempt - 1, source))
            if len(seen) == total:
                break
            if len(seen) > total or len(runs) != 100:
                raise AdapterError("Incomplete family history page", "access")
        else:
            raise AdapterError(
                "Family history exceeds reviewed bound", "access"
            )
    if not found_current or not candidates:
        raise AdapterError(
            "Required current/prior family history missing", "access"
        )
    ordered = sorted(candidates)
    if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
        raise AdapterError("Ambiguous family attempt chronology", "access")
    _SCANNED_HISTORY = identities
    return ordered[-1]


def download_publisher_artifact(identity, artifact):
    """Download one bounded ZIP with no cross-origin authorization."""
    _, _, run_id, attempt, head, source = identity
    try:
        status, headers, _ = request_bytes(
            "https://api.github.com/repos/YouthOpps/data-pipeline/"
            "actions/artifacts/" + str(artifact["id"]) + "/zip",
            headers={
                "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 302:
            raise ValueError("Artifact redirect missing")
        destination = headers.get("Location", "")
        parsed = urllib.parse.urlsplit(destination)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port
            or parsed.username
            or parsed.password
            or not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com")
            )
        ):
            raise ValueError("Unsafe artifact location")
        status, _, body = request_bytes(destination)
        if status != 200 or len(body) > 32768:
            raise ValueError("Invalid artifact response")
        digest = artifact.get("digest")
        if (
            digest is not None
            and digest != "sha256:" + hashlib.sha256(body).hexdigest()
        ):
            raise ValueError("Artifact digest mismatch")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if (
                len(entries) != 1
                or entries[0].filename != "pacing-state.json"
                or entries[0].file_size > 16384
                or entries[0].flag_bits & 1
                or entries[0].external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise ValueError("Invalid artifact members")
            envelope = json.loads(archive.read(entries[0]))
        if (
            not isinstance(envelope, dict)
            or type(envelope.get("schema")) is not int
            or type(envelope.get("run_id")) is not int
            or type(envelope.get("run_attempt")) is not int
            or envelope.get("schema") != 1
            or envelope.get("repository") != "YouthOpps/data-pipeline"
            or envelope.get("source") != source
            or envelope.get("run_id") != run_id
            or envelope.get("run_attempt") != attempt
            or envelope.get("head_sha") != head
        ):
            raise ValueError("Artifact attempt ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            "Exact publisher state artifact unavailable", "access"
        ) from None


def restore_family_state():
    """Restore real family state; only the pinned robots403 hold may migrate."""
    global _STATE_TRUSTED
    latest = latest_family_attempt()
    _, _, run_id, attempt, head, source = latest
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if (
        not isinstance(artifacts, list)
        or result.get("total_count") != len(artifacts)
        or len(artifacts) > 100
    ):
        raise AdapterError("Incomplete family artifact inventory", "access")
    exact = [
        a for a in artifacts if a.get("name") == f"saia-pacing-state-{attempt}"
    ]
    if (
        len(exact) != 1
        or exact[0].get("expired") is not False
        or type(exact[0].get("id")) is not int
        or exact[0].get("workflow_run", {}).get("id") != run_id
        or type(exact[0].get("size_in_bytes")) is not int
        or not 0 < exact[0]["size_in_bytes"] <= 32768
    ):
        raise AdapterError("Latest family attempt lacks exact state", "access")
    artifact = exact[0]
    incoming = download_publisher_artifact(latest, artifact)
    if (run_id, attempt, source) == (38070842559, 1, "sk-study-in-slovakia"):
        if (
            _SCANNED_HISTORY != _LEGACY_ATTEMPTS
            or head != "9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4"
            or artifact.get("digest") != _LEGACY_ARTIFACT
            or workflow_api(
                "/actions/workflows/fetch-sk-study-in-slovakia.yml"
            ).get("state")
            != "disabled_manually"
        ):
            raise AdapterError(
                "Pinned robots-only migration evidence changed", "access"
            )
        # Both recorded18 attempts stopped at the same robots403 before any
        # content. Their pinned artifact is real history, not a new allowance.
        incoming = dict(incoming)
        incoming["blocked"] = False
        marker = _REVIEWED_MIGRATION_NATIVE
        if (
            marker.get("schema") != 1
            or marker.get("source") != SOURCE_ID
            or not re.fullmatch(
                r"[0-9a-f]{64}", marker.get("native_state_sha256", "")
            )
        ):
            raise AdapterError(
                "Reviewed native migration handoff is missing", "access"
            )
        incoming = merge_budget(incoming, validate_budget(marker["state"]))
    files, local = open_budget(SOURCE_URL)
    try:
        combined = merge_budget(local, incoming)
        combined["interval"] = max(combined["interval"], 12.1)
        combined["page_gap"] = max(combined.get("page_gap", 30), 30)
        combined["until"] = max(combined["until"], time.time() + 60)
        save_budget(files, combined)
    finally:
        close_budget(files)
    _STATE_TRUSTED = True


def export_family_state():
    if not _STATE_TRUSTED:
        return
    run_id, attempt = current_run_identity()
    files, budget = open_budget(SOURCE_URL)
    try:
        save_budget(files, budget)
    finally:
        close_budget(files)
    head = os.environ.get("GITHUB_SHA", "")
    path, root = os.environ.get(
        "SAIA_PACING_ARTIFACT_PATH", ""
    ), os.environ.get("RUNNER_TEMP", "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", head)
        or not root
        or not os.path.isabs(root)
        or os.path.dirname(os.path.abspath(path)) != os.path.realpath(root)
    ):
        raise AdapterError(
            "Invalid family artifact destination/binding", "access"
        )
    envelope = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "source": SOURCE_ID,
        "run_id": run_id,
        "run_attempt": attempt,
        "head_sha": head,
        "state": budget,
    }
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        info = os.fstat(output.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
        ):
            raise AdapterError("Unsafe family artifact inode", "access")
        json.dump(envelope, output)
        output.flush()
        os.fsync(output.fileno())


def prepare_collection():
    global _COLLECTION_LOCK
    if _COLLECTION_LOCK is None:
        files, budget = open_budget(SOURCE_URL)
        try:
            save_budget(files, budget)
            directory = os.path.join(
                tempfile.gettempdir(),
                "saia-publisher-pacing-" + str(os.getuid()),
            )
        finally:
            close_budget(files)
        fd = os.open(
            os.path.join(directory, "collection.lock"),
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            os.close(fd)
            raise AdapterError("Unsafe family collection lock", "access")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise AdapterError(
                "Another local SAIA collection is active", "access"
            ) from None
        _COLLECTION_LOCK = fd


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
                "validated two-way NSP "
                f"scholarship tracks"
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
    if args.publish and os.environ.get("GITHUB_ACTIONS") == "true":
        prepare_collection()
        restore_family_state()
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
    finally:
        export_family_state()
