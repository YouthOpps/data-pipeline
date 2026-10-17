"""Collect UCPH scholarship programmes and funded doctoral vacancies."""

import argparse
import base64
import copy
import email.utils
import errno
import io
import math
import contextlib
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

SOURCE_ID = "dk-university-of-copenhagen"
SOURCE_URL = (
    "https://www.ku.dk/studies/admission/"
    "scholarships-for-full-degree-students"
)
WEBSITE_URL = SOURCE_URL
VACANCIES_URL = "https://employment.ku.dk/phd/"
POLICY_URL = "https://about.ku.dk/cookies-and-privacy-policy/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "DK"
PUBLISHER_TYPE = "university"
ATTRIBUTION = "University of Copenhagen"


_PAGE_COMPLETED = None




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




def clipped(text, limit=240):
    """Bound factual excerpts at a word boundary without adding HTML."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def programme(title, fragment, summary, evidence, countries=None):
    record = make_record(
        title,
        SOURCE_URL + "#" + fragment,
        ["scholarships"],
        kind="programme-overview",
        host_countries=["DK"],
        method="official-programme-conditions",
        evidence=evidence,
    )
    record["summary"] = summary
    record["location"] = "University of Copenhagen, Denmark"
    record["eligible_countries"] = countries or []
    return record


# Each reviewed programme is supported by the university's own funding page.
# Conditions are checked against its current paragraph before using own prose.
_FUNDING_PROGRAMMES = {
    "General Sir John Monash Scholarships": (
        "John Monash postgraduate scholarships for Australian citizens",
        "Australian citizens may seek support for taught master's or doctoral "
        "study at leading international universities. UCPH lists this named "
        "postgraduate award, but does not state an amount or current deadline.",
        ["Australian citizens", "master", "doctorates"],
        ["AU"],
        [],
    ),
    "Mackenzie King Open Scholarship": (
        "Mackenzie King Open Scholarship for Canadian graduate students",
        "Canadian students may apply for graduate study in Canada or abroad. "
        "The UCPH programme description directs applicants to the "
        "administrator "
        "for detailed eligibility and dates; no current deadline is stated.",
        ["Canadian students", "graduate studies"],
        ["CA"],
        [],
    ),
    "Becas CONICYT": (
        "CONICYT Becas Chile postgraduate scholarship",
        "Chilean citizens may seek support to start or continue postgraduate "
        "study at highly ranked international universities. The university "
        "page supplies no current application date or award amount.",
        ["Chilean citizens", "post-graduate"],
        ["CL"],
        [],
    ),
    "COLFUTURO loan and scholarship program": (
        "COLFUTURO master's loan with conditional scholarship conversion",
        "Colombian citizens may receive a postgraduate-study loan up to "
        "USD 50,000. A scholarship may cover 50% of the loan subject to "
        "conditions, including returning to Colombia after study and remaining "
        "there for three to five years. This is not an unconditional free "
        "grant; "
        "UCPH gives no current application deadline.",
        ["Colombian citizens", "USD 50,000", "50%", "three to five years"],
        ["CO"],
        ["loan", "conditional scholarship"],
    ),
    "Citadel Capital Scholarship Foundation": (
        "Citadel Capital postgraduate scholarship for Egyptian residents",
        "Egyptian citizens residing in Egypt and younger than 35 may seek "
        "master's-study support abroad in any discipline. Recipients must "
        "return to work in Egypt for at least two years after the programme. "
        "UCPH states no current deadline or monetary amount.",
        ["age of 35", "Egyptian citizen", "residing in Egypt", "two years"],
        ["EG"],
        [],
    ),
    "DAAD Annual Scholarships for Graduates of All Disciplines": (
        "DAAD Annual Scholarships for Graduates of All Disciplines",
        "This named DAAD graduate scholarship supports international study "
        "experience across academic disciplines. UCPH does not publish a "
        "current deadline, amount or nationality condition in this paragraph; "
        "the Germany accordion is not treated as proof of citizenship rules.",
        ["graduates", "all disciplines", "international study"],
        [],
        [],
    ),
    "Friedrich- Ebert-Stiftung (FES)": (
        "Friedrich Ebert Foundation postgraduate scholarships",
        "German citizens pursuing postgraduate study in Germany or at European "
        "universities are described as eligible. Selection considers academic "
        "achievement, social and political involvement, and commitment to "
        "social democratic values. No current application date is published.",
        ["German citizens", "postgraduate", "social and political"],
        ["DE"],
        [],
    ),
    "Inlaks Shivdasani Foundation": (
        "Inlaks Shivdasani postgraduate scholarship",
        "Indian citizens with an undergraduate degree and high achievement may "
        "seek support for postgraduate study at a highly ranked overseas "
        "university. The described award covers tuition, living costs and "
        "one-way travel. UCPH states no current deadline or exact amount.",
        ["Indian citizenship", "undergraduate degree", "one-way travel"],
        ["IN"],
        [],
    ),
    "JN Tata Endowment Loan Scholarships": (
        "J. N. Tata Endowment one-time postgraduate loan scholarship",
        "Indian nationals may apply for a one-time loan scholarship at the "
        "start of full-time postgraduate, PhD or postdoctoral study abroad in "
        "any discipline. This is repayable loan support, not an unconditional "
        "grant. UCPH refers repayment terms and dates to the administrator.",
        [
            "one-time loan",
            "Indian nationals",
            "Postgraduate/Ph.D./Postdoctoral",
        ],
        ["IN"],
        ["repayable loan"],
    ),
    "Lady Meherbai D. Tata Education Trust Scholarships": (
        "Lady Meherbai D. Tata Education Trust scholarship",
        "Female Indian graduates of a recognised Indian university may apply "
        "to continue their education at a recognised institution abroad. "
        "Applications may be made while admission is still pending. UCPH "
        "provides no exact award amount or current application deadline.",
        [
            "female Indian graduates",
            "recognized Indian university",
            "awaiting admission",
        ],
        ["IN"],
        [],
    ),
    "The Indonesian Education Scholarship": (
        "Indonesian Education Scholarship for overseas master's study",
        "Indonesian citizens seeking master's study abroad are described as "
        "eligible, and UCPH is explicitly a listed target university. The "
        "university page directs applicants to the administrator for dates "
        "and further conditions; no current deadline is stated here.",
        ["Indonesian citizens", "Master", "listed target university"],
        ["ID"],
        [],
    ),
    "The CONACYT Mexican Scholarship": (
        "CONACYT Mexican postgraduate scholarship",
        "Mexican citizens may seek the Mexican government's monthly award "
        "for graduate study at an international university. UCPH states "
        "neither a monthly monetary amount nor a current application deadline.",
        ["monthly award", "Mexican citizens", "Mexican government"],
        ["MX"],
        [],
    ),
    "FUNED": (
        "FUNED support for Mexican master's students abroad",
        "Mexican citizens may seek financial support for master's study at "
        "leading international universities. UCPH lists information "
        "technology, "
        "international relations, political science, economics and law among "
        "the subject areas. It does not establish an amount, repayment terms "
        "or current deadline; the support is not asserted to be a free grant.",
        ["Mexican citizens", "Master", "information technology", "law"],
        ["MX"],
        ["funding terms unspecified"],
    ),
    "The Tamim bin Hamad Scholarship": (
        "Tamim bin Hamad overseas scholarship",
        "Qatari citizens may seek Qatar-government scholarship support to "
        "study at a recognised university abroad. UCPH is explicitly on the "
        "approved university list. No award amount or dated current call is "
        "stated on the university page.",
        ["Qatari citizens", "approved universities"],
        ["QA"],
        [],
    ),
    "Global Education Programme": (
        "Global Education Programme postgraduate study funding",
        "The named programme supports full-time overseas postgraduate study "
        "in science, engineering, medicine, education and management. It "
        "covers tuition, travel, insurance, accommodation, meals and "
        "literature. "
        "Recipients must return to Russia and work in their qualification "
        "for at least three years. No nationality rule or current date is "
        "published here; the Russia accordion alone proves neither.",
        ["full-time post-graduate", "three years", "return to Russia"],
        [],
        [],
    ),
    "The Singapore Food Agency (SFA) Postgraduate Scholarships": (
        "Singapore Food Agency postgraduate scholarship",
        "The SFA postgraduate award covers full-term or mid-term master's/PhD "
        "coursework or research at international universities. Listed fields "
        "include agricultural and biological sciences, chemistry, food and "
        "plant science, and toxicology. UCPH provides no award amount, dated "
        "call or explicit citizenship rule; country heading is not "
        "eligibility.",
        ["Master", "PhD", "full-term/mid-term", "Toxicology"],
        [],
        [],
    ),
    "Firstrand Laurie Dippenaar Scholarship": (
        "FirstRand Laurie Dippenaar postgraduate scholarship",
        "South African citizens may seek this named postgraduate scholarship "
        "for study at a recognised university outside South Africa. UCPH "
        "states no current deadline or monetary award amount.",
        ["South African citizens", "postgraduate", "outside of South Africa"],
        ["ZA"],
        [],
    ),
    "The Jean Monnet Scholarship Programme": (
        "Jean Monnet Scholarship Programme for EU-harmonisation study",
        "This programme funds graduate-level academic study connected with "
        "Turkey's EU harmonisation process and the EU acquis. UCPH gives no "
        "current deadline, amount or explicit citizenship rule; the Turkey "
        "accordion is not used to infer applicant nationality.",
        ["graduate level", "EU harmonisation", "EU acquis"],
        [],
        [],
    ),
    "The Aga Khan Foundation": (
        "Aga Khan Foundation postgraduate scholarship programme",
        "Outstanding postgraduate applicants from selected developing "
        "countries who lack other study financing may seek this competitive "
        "award. Support is 50% grant and 50% loan, not a wholly non-repayable "
        "scholarship. UCPH does not identify the complete eligible-country "
        "list or a current application deadline.",
        [
            "select developing countries",
            "no other means",
            "50% grant",
            "50% loan",
        ],
        [],
        ["partial loan"],
    ),
    "Women Techmakers Scholars Program": (
        "Women Techmakers Scholars Program",
        "Applicants identifying as female and studying computer science, "
        "computer engineering or a closely related technical subject are "
        "described as eligible. Selection considers academic performance, "
        "leadership and impact on the women-in-tech community. UCPH supplies "
        "no current application date or award amount.",
        ["identify as female", "computer science", "leadership"],
        [],
        [],
    ),
    "The OFID Scholarship Award": (
        "OFID Scholarship Award for development-related master's study",
        "Students from developing countries pursuing a development-related "
        "master's degree at an internationally recognised university are "
        "described as eligible. The award covers tuition and a monthly "
        "allowance. UCPH supplies no complete country list, monetary amount "
        "or current application date.",
        [
            "developing countries",
            "Master",
            "development related",
            "monthly allowance",
        ],
        [],
        [],
    ),
    "Rotary Scholarship": (
        "Rotary Foundation Global Grants scholarship",
        "Applicants require nomination by a local Rotary club. The source "
        "describes generally rolling applications, with possible local club "
        "deadlines. Club presence in 70 countries is not an applicant-country "
        "list. No exact current deadline or award amount is stated by UCPH.",
        ["Global Grants", "nominated by a local Rotary club", "rolling basis"],
        [],
        [],
    ),
}

_EXCLUDED_FUNDING = {
    "The International Scholarships Program (ISP)": "programme database",
    "Fulbright Commission": "duplicate of the detailed UCPH Fulbright route",
    "The American-Scandinavian Foundation (ASF)": (
        "directory of multiple awards"
    ),
    "Federal Student Loans – Title IV": (
        "UCPH explicitly no longer participates"
    ),
    "Private Lenders": "generic lending and certification advice",
    "Veteran Aid – VA": "UCPH explicitly no longer services this funding",
    "The Gen Foundation": "trust/funder profile without a specific named award",
}


def additional_programmes(main):
    """Cover substantive same-page named programmes without external
    requests.
    """
    sections = [
        n
        for n in nodes(main, "section")
        if n.text().startswith(
            (
                "Scholarships and loans opportunities",
                "Scholarships from external institutions",
            )
        )
    ]
    if len(sections) != 2:
        raise AdapterError("External funding sections changed", "parse")
    found = set()
    records = []
    for section in sections:
        for item in nodes(section, "div", "accordion-item"):
            heading = one(nodes(item, "h2"), "funding accordion heading")
            body = one(nodes(item, "div", "accordion-body"), "funding body")
            blocks = []
            if nodes(body, "h3"):
                current = None
                for child in body.walk():
                    if child.tag == "h3":
                        current = [child.text(), []]
                        blocks.append(current)
                    elif child.tag == "p" and current is not None:
                        current[1].append(child.text())
            else:
                blocks.append([heading.text(), [body.text()]])
            for name, paragraphs in blocks:
                if name in found:
                    raise AdapterError("Repeated funding identity", "parse")
                found.add(name)
                if name in _EXCLUDED_FUNDING:
                    continue
                if name not in _FUNDING_PROGRAMMES:
                    raise AdapterError("Unreviewed named funding programme")
                title, summary, required, countries, tags = _FUNDING_PROGRAMMES[
                    name
                ]
                text = " ".join(paragraphs)
                if any(fact not in text for fact in required):
                    raise AdapterError(
                        "Reviewed funding conditions changed: " + name
                    )
                record = programme(
                    title,
                    heading.attrs["id"],
                    summary,
                    [
                        "UCPH-published programme identity: " + name,
                        "External administrator; only this university's "
                        "published conditions are collected.",
                        "No current dated application call is given here.",
                    ],
                    countries=countries,
                )
                record["id"] = hashlib.sha256(
                    (SOURCE_ID + "|programme|" + name).encode()
                ).hexdigest()[:24]
                record["tags"] = tags
                record["host_countries"] = []
                record["location"] = None
                if "University of Copenhagen" in text:
                    record["host_countries"] = ["DK"]
                    record["location"] = "University of Copenhagen, Denmark"
                elif name == "Mackenzie King Open Scholarship":
                    record["host_countries"] = ["CA"]
                    record["classification"]["evidence"].append(
                        "Canada is an explicit possible study destination; "
                        "other overseas destinations are unspecified."
                    )
                elif name == "Friedrich- Ebert-Stiftung (FES)":
                    record["host_countries"] = ["DE"]
                    record["classification"]["evidence"].append(
                        "Germany is an explicit possible study destination; "
                        "other European destinations are unspecified."
                    )
                records.append(record)
    if found != set(_FUNDING_PROGRAMMES) | set(_EXCLUDED_FUNDING):
        raise AdapterError("Reviewed funding inventory changed", "parse")
    return records


def scholarships(root):
    """Keep actual university study awards, not external funding directories."""
    main = one(nodes(root, "main"), "scholarship main content")
    government = one(
        [
            n
            for n in nodes(main, "section")
            if n.text().startswith("Danish government Scholarships")
        ],
        "Danish Government programme",
    )
    text = government.text()
    required = (
        "automatically",
        "non-EU/EEA",
        "time-limited residence permit",
        "Faculty of Health and Medical Sciences",
        "Faculty of Humanities",
        "Faculty of Social Sciences",
        "Faculty of Science",
        "Faculty of Theology",
        "22 months",
        "90%",
        "60 ECTS",
        "GPA of 10 or higher",
        "maximum three with the same nationality",
        "less than four per year",
        "generally 2-3 per year",
        "September intake",
    )
    if any(term not in text for term in required):
        raise AdapterError("Reviewed scholarship conditions changed", "parse")
    health = one(
        [
            n
            for n in nodes(government, "div", "accordion-item")
            if n.text().startswith("Faculty of Health and Medical Sciences")
        ],
        "Health scholarship conditions",
    )
    science = one(
        [
            n
            for n in nodes(government, "div", "accordion-item")
            if n.text().startswith("Faculty of Science")
        ],
        "Science scholarship conditions",
    )
    if not all(
        "Scholarship for 2" in n.text() and "year students" in n.text()
        for n in (health, science)
    ):
        raise AdapterError("Second-year scholarship cohorts missing", "parse")
    records = [
        programme(
            "UCPH Danish Government Scholarship for MA/MSc admission",
            "danish-government-scholarships",
            "Automatic academic-merit consideration for eligible master's "
            "applicants from outside the EU, EEA and Switzerland with a "
            "time-limited study residence permit. "
            "Awards provide tuition waivers and/or living support. Faculty "
            "rules "
            "vary; SCIENCE requires a first-priority application and "
            "bachelor's "
            "GPA above 90%. No separate scholarship application or dated call "
            "is "
            "published here; financial need does not determine selection.",
            [
                "Eligibility: citizenship outside EU, EEA and Switzerland; "
                "UCPH master's admission and temporary study residence permit.",
                "Exclusions: Danish-citizen rights, SU eligibility and the "
                "specified Aliens Act section 9c/9m residence exception.",
                "HEALTH: academically outstanding applicants; either tuition "
                "alone or tuition and living costs; living support may be "
                "taxed.",
                "HUMANITIES: automatic consideration of eligible MA "
                "applicants.",
                "SOCIAL SCIENCES: September intake only; usually 2–3 "
                "awards/year.",
                "SCIENCE: first-priority MSc admission; bachelor's GPA above "
                "90%; "
                "tuition and living expenses for 22 months. 2025 minimum 98% "
                "is historical.",
                "THEOLOGY: partial tuition waiver only, usually fewer than "
                "4/year; "
                "fees must be paid before possible later reimbursement.",
                "Yearless admission calendars are not scholarship deadlines.",
            ],
        ),
        programme(
            "UCPH HEALTH second-year master's scholarship",
            one(nodes(health, "h2"), "Health heading").attrs["id"],
            "Automatic annual consideration for fee-paying master's students "
            "who completed their first year at UCPH HEALTH. Very limited "
            "one-year awards; the best student may receive tuition and living "
            "support. The faculty expects GPA 10 or higher on the Danish scale "
            "and notifies recipients around mid-September/early October. "
            "No exact current deadline is stated.",
            [
                "Distinct continuing-student cohort, not an admission award.",
                "Publisher expects approximately 2 second-year awards/year; "
                "capacity "
                "does not create separate opportunities.",
                "Source conditions: " + SOURCE_URL + "#" + health.attrs["id"],
            ],
        ),
        programme(
            "UCPH SCIENCE second-year master's scholarship",
            one(nodes(science, "h2"), "Science heading").attrs["id"],
            "Fee-paying UCPH SCIENCE students may receive second-year tuition "
            "and living support after excellent first-year results. At least "
            "60 ECTS must be registered by 1 September; exchange grades "
            "receive "
            "individual assessment. No more than 3 recipients share a "
            "nationality. "
            "Selected students are contacted in early September; fees already "
            "paid for semester 3 are refunded. No dated application call is "
            "stated.",
            [
                "Distinct continuing-student cohort, not an admission award.",
                "2025 recipient GPA 11 or higher is historical, not a current "
                "cutoff.",
                "Source conditions: " + SOURCE_URL + "#" + science.attrs["id"],
            ],
        ),
    ]
    fulbright = one(
        [
            n
            for n in nodes(main, "section")
            if n.text().startswith(
                "Fulbright Scholarship for US full-degree master's students"
            )
        ],
        "UCPH Fulbright study programme",
    )
    if not all(
        term in fulbright.text()
        for term in (
            "US students",
            "1-2 semesters",
            "not given to exchange students",
            "tuition fee",
            "letter of affiliation",
        )
    ):
        raise AdapterError("Reviewed Fulbright study conditions changed")
    records.append(
        programme(
            "Fulbright scholarship for US full-degree or guest students at "
            "UCPH",
            "fulbright-scholarship",
            "UCPH describes Fulbright support for US students taking a full "
            "master's degree or 1–2 semesters as a guest student; exchange "
            "students "
            "are excluded. Apply separately for the award and UCPH study "
            "place. "
            "Full-degree tuition remains payable. The relevant department "
            "supplies an affiliation letter. No current award deadline or "
            "amount "
            "is published on this university page.",
            [
                "Externally administered Fulbright award; only UCPH-published "
                "study-route conditions are collected, not external provider "
                "data.",
                "UCPH International Education cannot sign affiliation letters.",
            ],
            countries=["US"],
        )
    )
    for record in records:
        fragment = urllib.parse.urlsplit(record["url"]).fragment
        if not any(n.attrs.get("id") == fragment for n in nodes(main)):
            raise AdapterError("Programme anchor is missing", "parse")
    records.extend(additional_programmes(main))
    return records


def vacancy_rows(root, url):
    """Read the complete server-rendered official vacancy table."""
    table = one(nodes(root, "table", "vacancies"), "PhD vacancy table")
    rows = []
    for row in nodes(table, "tr"):
        cells = nodes(row, "td")
        if not cells:
            continue
        if len(cells) != 4:
            raise AdapterError("Unknown vacancy table layout", "parse")
        link = one(nodes(cells[0], "a"), "vacancy detail link")
        target = urllib.parse.urljoin(url, link.attrs.get("href", ""))
        parsed = urllib.parse.urlsplit(target)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "employment.ku.dk"
            or parsed.path != "/phd/"
            or parsed.fragment
            or not re.fullmatch(r"show=\d+", parsed.query)
        ):
            raise AdapterError("Unreviewed vacancy detail URL", "parse")
        try:
            date = datetime.strptime(cells[3].text(), "%d-%m-%Y").date()
        except ValueError as error:
            raise AdapterError("Invalid vacancy calendar date") from error
        rows.append(
            (target, link.text(), cells[1].text(), cells[2].text(), date)
        )
    if not rows:
        raise AdapterError("Empty official doctoral inventory", "parse")
    return rows


_MONTHS = (
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
)
_DATE = (
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+(?:"
    + "|".join(_MONTHS)
    + r")\s+20\d{2}|(?:"
    + "|".join(_MONTHS)
    + r")\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+20\d{2}|"
    + r"\d{1,2}\.\d{1,2}\.20\d{2})"
)


def stated_date(value):
    cleaned = re.sub(r"(\d)(?:st|nd|rd|th)\b", r"\1", value)
    cleaned = cleaned.replace(",", "")
    for pattern in ("%d %B %Y", "%B %d %Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            pass
    raise AdapterError("Unsupported explicit application date", "parse")


def application_deadline(body, listing_date):
    """Keep publisher clock/offset; flag competing dates instead of guessing."""
    text = body.text()
    found = []
    for match in re.finditer(
        r"(?:deadline for applications?|application deadline)\s*"
        r"(?:is\s*)?[:]?\s*(?:Sunday\s*)?(" + _DATE + ")",
        text,
        re.I,
    ):
        date = stated_date(match[1])
        remainder = text[match.end() : match.end() + 65]
        clock = re.match(
            r"\s*[,\s]*(?:at\s*)?(\d{1,2})[:.](\d{2})"
            r"\s*(?:pm|PM)?\s*[([]?\s*"
            r"(CET|CEST|GMT\s*\+\s*[12]|UTC)",
            remainder,
        )
        instant = None
        if clock:
            hour, minute = int(clock[1]), int(clock[2])
            zone = re.sub(r"\s", "", clock[3].upper())
            offset = {"CET": 1, "CEST": 2, "GMT+1": 1, "GMT+2": 2, "UTC": 0}[
                zone
            ]
            instant = (
                datetime(
                    date.year,
                    date.month,
                    date.day,
                    hour,
                    minute,
                    tzinfo=timezone(timedelta(hours=offset)),
                )
                .astimezone(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        found.append((date, instant, clipped(match[0] + remainder, 150)))
    dates = {entry[0] for entry in found}
    evidence = list(dict.fromkeys(entry[2] for entry in found))
    if len(dates | {listing_date}) != 1:
        return (
            None,
            "unknown",
            [
                "Conflicting official listing date: "
                + listing_date.isoformat(),
                *evidence,
            ],
        )
    instants = {entry[1] for entry in found if entry[1] is not None}
    if len(instants) > 1:
        return None, "unknown", ["Conflicting publisher clocks", *evidence]
    deadline = next(iter(instants), listing_date.isoformat())
    if instants:
        expired = datetime.fromisoformat(
            deadline.replace("Z", "+00:00")
        ) < datetime.now(timezone.utc)
    else:
        # Index calendar policy, not a publisher-stated Danish timezone.
        # Keep the same conservative closing day as date-only cleanup.
        expired = listing_date < datetime.now(
            timezone(timedelta(hours=-12))
        ).date()
    return (
        deadline,
        "expired" if expired else "open",
        evidence
        or [
            "Official listing application date: "
            + listing_date.isoformat()
            + "; no supported clock/offset.",
        ],
    )


def vacancy(row, root):
    url, listing_title, faculty, department, date = row
    main = one(nodes(root, "main"), "vacancy main content")
    title = one(nodes(main, "h1"), "vacancy heading").text()
    body = one(nodes(main, "div", "vacancy_details_area"), "vacancy body")
    text = body.text()
    if title != listing_title:
        raise AdapterError("Detail title differs from listing title", "parse")
    if not re.search(r"\bPhD\b", title, re.I):
        raise AdapterError("Detail title lacks PhD identity", "parse")
    if not re.search(
        r"salary|stipend|scholarship|appointment and payment", text, re.I
    ):
        raise AdapterError("Detail lacks verified funding evidence", "parse")
    deadline, status, dates = application_deadline(body, date)
    evidence = [
        "Official current PhD listing: " + VACANCIES_URL,
        "Listing faculty: " + faculty,
        *dates,
    ]
    facts = []
    for node in nodes(body):
        if node.tag not in ("p", "li"):
            continue
        paragraph = node.text()
        if (
            re.search(
                r"salary.*(?:starts|approximately)|"
                r"qualifications.*(?:degree|master)|"
                r"(?:must|requires?|required).*degree|"
                r"(?:excellent|fluency|proficiency).*English|"
                r"must not have resided|must not.*doctoral degree",
                paragraph,
                re.I,
            )
            and "tax reductions" not in paragraph
        ):
            facts.append(clipped(paragraph))
    evidence.extend(dict.fromkeys(facts))
    detail_faculties = set(
        re.findall(
            r"Faculty of (?:Science|Health and Medical Sciences|Humanities)",
            text,
            re.I,
        )
    )
    if detail_faculties and faculty.lower() not in {
        value.lower() for value in detail_faculties
    }:
        evidence.append(
            "Faculty discrepancy: detail says "
            + "; ".join(sorted(detail_faculties))
            + "; listing says "
            + faculty
            + "."
        )
    integrated = re.search(r"integrated MSc and PhD programme", text, re.I)
    route = (
        "The notice describes regular and integrated MSc/PhD routes; "
        "their degree and enrolment conditions differ. "
        if integrated
        else "The notice specifies relevant degree and academic requirements. "
    )
    summary = (
        "Funded doctoral recruitment at "
        + department
        + ", UCPH. "
        + route
        + "Application is through the official notice; "
        "consult its subject, language and employment conditions."
    )
    if "MSCA" in text and "must not have resided" in text:
        if not all(term in text for term in ("12 months", "3 years")):
            raise AdapterError("Reviewed MSCA mobility limits changed")
        summary += (
            " MSCA mobility: no more than 12 months of main activity "
            "in Denmark during the 3 years before recruitment."
        )
        if "not already in possession of a doctoral degree" in text:
            evidence.append(
                "MSCA: doctoral degree must not already be held on the first "
                "employment day; mobility is assessed at recruitment."
            )
        if "successfully defended their doctoral thesis" in text:
            evidence.append(
                "MSCA: a successfully defended doctorate also excludes "
                "eligibility before formal award."
            )
        if "Short stays as holidays and compulsory military service" in text:
            evidence.append(
                "MSCA mobility exclusions: short holidays, compulsory "
                "military service and Refugee Convention 1951 circumstances, "
                "as stated in the notice."
            )
    if (
        integrated
        and "without such qualifications will not be considered" in text
    ):
        evidence.append(
            "Publisher describes an integrated route but also says "
            "candidates without regular-route qualifications are not "
            "considered; applicants should clarify that inconsistency."
        )
    record = make_record(
        title,
        url,
        ["fellowships", "jobs"],
        host_countries=["DK"],
        method="official-funded-doctoral-vacancy",
        evidence=evidence,
    )
    record.update(
        summary=clipped(summary, 600),
        deadline=deadline,
        status=status,
        location=department + ", Denmark",
    )
    record["tags"] = ["PhD", "University of Copenhagen"]
    return record


def vacancy_catalog():
    """Read every authoritative listing page before comparing inventories."""
    pending, seen, rows, urls = [VACANCIES_URL], set(), [], set()
    while pending:
        url = pending.pop(0)
        if url in seen:
            continue
        seen.add(url)
        root = fetch_inventory_page(page, url)
        for row in vacancy_rows(root, url):
            if row[0] in urls:
                raise AdapterError("Repeated vacancy across listing pages")
            urls.add(row[0])
            rows.append(row)
        for link in nodes(root, "a"):
            relation = link.attrs.get("rel", "").split()
            if "next" not in relation:
                continue
            target = urllib.parse.urljoin(url, link.attrs.get("href", ""))
            parsed = urllib.parse.urlsplit(target)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "employment.ku.dk"
                or parsed.path != "/phd/"
                or parsed.fragment
            ):
                raise AdapterError("Unreviewed vacancy pagination", "parse")
            if target not in seen:
                pending.append(target)
        if len(seen) > 30:
            raise AdapterError("Excessive vacancy pagination", "parse")
    return rows, len(seen)




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
        if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", record["id"]):
            raise AdapterError("Invalid opportunity identifier", "validate")
        if record["id"].casefold() in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].casefold())
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
        "display_name": "University of Copenhagen",
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


def remove_expired_records(records, checked):
    """Filter the validated replacement, leaving prior publication untouched."""
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise AdapterError("Cleanup clock requires a timezone", "validate")
    current = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
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
    publisher = "ku.dk" if host == "ku.dk" or host.endswith(".ku.dk") else host
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
        budget["interval"] = max(budget["interval"], interval, 6.1)
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
            state, budget = legacy_pace(
                url, interval, start_padding=retry_jitter
            )
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
            try:
                # Hold the family lock through completion and the next-start
                # gate.
                budget["until"] = max(
                    budget["until"], time.time() + budget["interval"]
                )
                legacy_save_budget(state, budget)
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


@contextlib.contextmanager
def legacy_publication_http_guard(deadline):
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


def collect_body():
    """Collect completely; restart once only after verified catalog turnover."""
    _ROBOTS.clear()
    rows = None
    for attempt in range(2):
        records = scholarships(page(SOURCE_URL))
        if rows is None:
            rows, listing_pages = vacancy_catalog()
        for index, row in enumerate(rows, 1):
            # Fetch failures/refusals cannot trigger catalog recovery.
            detail = page(row[0])
            try:
                record = vacancy(row, detail)
            except AdapterError as error:
                identifier = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(row[0]).query
                )["show"][0]
                diagnostic = (
                    f"Doctoral advert show={identifier} ({row[0]}): "
                    + str(error)
                )
                if error.stage != "parse" or attempt:
                    raise AdapterError(diagnostic, error.stage) from error
                print("UCPH: " + safe_error(diagnostic), file=sys.stderr)
                refreshed, refreshed_pages = vacancy_catalog()
                identity = {(item[0], item[1]) for item in rows}
                current = {(item[0], item[1]) for item in refreshed}
                if current == identity:
                    raise AdapterError(
                        diagnostic + "; authoritative catalog unchanged",
                        "parse",
                    ) from error
                print(
                    "UCPH: catalog changed; "
                    "restarting complete collection once",
                    file=sys.stderr,
                )
                rows, listing_pages = refreshed, refreshed_pages
                break
            records.append(record)
            print(
                f"UCPH: inspected doctoral advert {index}/{len(rows)}",
                file=sys.stderr,
            )
        else:
            validate_records(records)
            print(
                f"UCPH: {len(records) - len(rows)} award programmes, "
                f"{len(rows)} doctoral adverts, "
                f"{listing_pages} listing pages",
                file=sys.stderr,
            )
            return records
    raise AdapterError("Complete catalog restart exhausted", "parse")


def run_body(publishing):
    enter_phase("snapshot", 120)
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
    enter_phase(
        "failure" if failure else "publication",
        _FAILURE_SECONDS if failure else _PUBLICATION_SECONDS,
    )
    outcome = metadata(attempt, previous, records, failure)
    if publishing and (
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
    elif publishing:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


FAMILY = "youthopps-dk-university-of-copenhagen-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_IDS = {SOURCE_ID: 378390320}
COLLECTION_TIMEOUT = 1350
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 30
_PUBLICATION_SECONDS = 180
_FAILURE_SECONDS = 180
_PUBLISHER_TIMEOUT = _DEFAULT_HTTP_TIMEOUT = 30
_ARTIFACT_NAME = "dk-university-of-copenhagen-pacing-state"
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
_PUBLISHER_HOSTS = {"ku.dk", "www.ku.dk", "employment.ku.dk"}
_LEGACY_NO_JOB = {
    38001485160: {
        "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "conclusion": "cancelled",
        "created_at": "2026-10-09T22:51:57Z",
        "jobs_sha256": (
            "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f"
        ),
    }
}
_LOCAL_RESEARCH_MIGRATION = {'schema': 1,
 'source': 'dk-university-of-copenhagen',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/326#issuecomment-6215354159',
 'state': {'schema': 3,
           'family': 'youthopps-dk-university-of-copenhagen-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [],
           'research_provenance': {},
           'starts': [1791492402.0,
                      1791492586.0,
                      1791528112.0,
                      1791553545.0,
                      1791613419.0,
                      1791637410.0,
                      1791655852.0,
                      1791669785.0,
                      1791699978.0,
                      1791725444.0,
                      1791757701.0,
                      1791786665.0,
                      1791821121.0,
                      1791853260.0,
                      1791902102.0,
                      1791932922.0,
                      1791955343.0,
                      1791966551.0,
                      1791980528.0,
                      1791999501.0,
                      1792015204.0,
                      1792019363.0,
                      1792046724.0,
                      1792048578.0,
                      1792060419.0,
                      1792060968.7302551,
                      1792060976.7965102,
                      1792060985.0717955,
                      1792061076.0,
                      1792079605.0,
                      1792148627.8874724,
                      1792148687.8857517,
                      1792148702.1589587,
                      1792148702.1605675,
                      1792148703.2783506,
                      1792148703.279129,
                      1792148703.2794766,
                      1792148715.478587,
                      1792148715.482099,
                      1792148715.4836717,
                      1792148716.3826683,
                      1792148716.3886666,
                      1792148716.423934,
                      1792148728.5876288,
                      1792148728.5908477,
                      1792148728.5940032,
                      1792148730.2530158,
                      1792148730.2549624,
                      1792148730.2552605,
                      1792148746.3906221,
                      1792148746.396575,
                      1792148746.3998735,
                      1792148747.6113234,
                      1792148747.614412],
           'interval': 12.2,
           'until': 1792152926.185524,
           'page_gap': 30,
           'completed': 1792148747.614412,
           'inventory_completed': 1792148747.614412,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792152866.185524},
 'state_sha256': '3dde0d8b8fa2ac1d4cbe2e988b634c900abd68f96f36a134a20663cbfc42e466',
 'legacy_state_sha256': 'e54ab76af4e1509d89d9cda5479df47478952993402d380c9101d7047f97fd4d'}
_REVIEWED_NATIVE_MIGRATION = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/326#issuecomment-6216556689',
 'legacy_state_sha256': 'fea4e334fd7dc012aac874a9b0ff68666ebcb63d256e4cce13ed1c20ced67ab4',
 'schema': 1,
 'source': 'dk-university-of-copenhagen',
 'state': {'blocked': False,
           'completed': 1792154998.7735846,
           'family': 'youthopps-dk-university-of-copenhagen-publisher-v1',
           'in_flight': False,
           'interval': 12.2,
           'inventory_completed': 1792154768.6368508,
           'legacy_429_history': 'unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [],
           'legacy_hold_history': 'unlogged_fields_unknown',
           'observed_429_since_transition': 0,
           'observed_at': 1792156674.5193796,
           'page_gap': 30,
           'rate_limit_until': 0,
           'research_provenance': {},
           'schema': 3,
           'starts': [1791492402.0,
                      1791492586.0,
                      1791528112.0,
                      1791553545.0,
                      1791613419.0,
                      1791637410.0,
                      1791655852.0,
                      1791669785.0,
                      1791699978.0,
                      1791725444.0,
                      1791757701.0,
                      1791786665.0,
                      1791821121.0,
                      1791853260.0,
                      1791902102.0,
                      1791932922.0,
                      1791955343.0,
                      1791966551.0,
                      1791980528.0,
                      1791999501.0,
                      1792015204.0,
                      1792019363.0,
                      1792046724.0,
                      1792048578.0,
                      1792060419.0,
                      1792060968.7302551,
                      1792060976.7965102,
                      1792060985.0717955,
                      1792061076.0,
                      1792079605.0,
                      1792148627.8874724,
                      1792148687.8857517,
                      1792148702.1589587,
                      1792148702.1605675,
                      1792148703.2783506,
                      1792148703.279129,
                      1792148703.2794766,
                      1792148715.478587,
                      1792148715.482099,
                      1792148715.4836717,
                      1792148716.3826683,
                      1792148716.3886666,
                      1792148716.423934,
                      1792148728.5876288,
                      1792148728.5908477,
                      1792148728.5940032,
                      1792148730.2530158,
                      1792148730.2549624,
                      1792148730.2552605,
                      1792148746.3906221,
                      1792148746.396575,
                      1792148746.3998735,
                      1792148747.6113234,
                      1792148747.614412,
                      1792154648.5936134,
                      1792154708.5908237,
                      1792154722.7224696,
                      1792154722.7239711,
                      1792154723.5196593,
                      1792154723.5217698,
                      1792154723.5219495,
                      1792154735.7198646,
                      1792154735.7217011,
                      1792154735.7228534,
                      1792154736.939528,
                      1792154736.9453692,
                      1792154736.9867182,
                      1792154749.1441686,
                      1792154749.1471503,
                      1792154749.1487448,
                      1792154751.9092543,
                      1792154751.9118228,
                      1792154751.9119084,
                      1792154766.9453392,
                      1792154766.947948,
                      1792154766.9532988,
                      1792154768.6363757,
                      1792154768.6403215,
                      1792154768.6639836,
                      1792154780.8370118,
                      1792154780.8388252,
                      1792154780.8408008,
                      1792154782.208501,
                      1792154782.2130847,
                      1792154782.2337143,
                      1792154794.4118872,
                      1792154794.4138715,
                      1792154794.4153225,
                      1792154795.7993119,
                      1792154795.8024054,
                      1792154795.8115668,
                      1792154808.0014975,
                      1792154808.0033538,
                      1792154808.0053658,
                      1792154809.122157,
                      1792154809.124658,
                      1792154809.1348815,
                      1792154821.3233397,
                      1792154821.325218,
                      1792154821.326365,
                      1792154822.7909844,
                      1792154822.7936668,
                      1792154822.8087537,
                      1792154834.9926708,
                      1792154834.9944987,
                      1792154834.9990466,
                      1792154835.8494785,
                      1792154835.8514922,
                      1792154835.860838,
                      1792154848.0506036,
                      1792154848.0526998,
                      1792154848.05401,
                      1792154849.029185,
                      1792154849.0320318,
                      1792154849.0417218,
                      1792154861.2309113,
                      1792154861.2334466,
                      1792154861.2347445,
                      1792154861.9966922,
                      1792154861.9986737,
                      1792154862.0085914,
                      1792154874.1977782,
                      1792154874.2010338,
                      1792154874.2025301,
                      1792154875.250144,
                      1792154875.2578173,
                      1792154875.3288214,
                      1792154887.455816,
                      1792154887.4574454,
                      1792154887.4589496,
                      1792154888.5650687,
                      1792154888.5670261,
                      1792154888.5865774,
                      1792154900.766102,
                      1792154900.768692,
                      1792154900.7701814,
                      1792154901.7359576,
                      1792154901.7377665,
                      1792154901.74537,
                      1792154913.9366672,
                      1792154913.9385922,
                      1792154913.939779,
                      1792154915.028555,
                      1792154915.0326872,
                      1792154915.0442183,
                      1792154927.2311854,
                      1792154927.2364812,
                      1792154927.237804,
                      1792154929.9273632,
                      1792154929.9299157,
                      1792154929.9416718,
                      1792154942.1289184,
                      1792154942.1311774,
                      1792154942.1333125,
                      1792154943.333713,
                      1792154943.3369112,
                      1792154943.3517416,
                      1792154955.5360434,
                      1792154955.581028,
                      1792154955.6070933,
                      1792154958.054264,
                      1792154958.0568528,
                      1792154958.0681674,
                      1792154970.255235,
                      1792154970.2575023,
                      1792154970.2594745,
                      1792154972.43934,
                      1792154972.4416578,
                      1792154972.464958,
                      1792154984.640196,
                      1792154984.6427093,
                      1792154984.6482906,
                      1792154985.7450986,
                      1792154985.7471945,
                      1792154985.7594995,
                      1792154997.946606,
                      1792154997.9483576,
                      1792154997.9496827,
                      1792154998.772263,
                      1792154998.774713],
           'until': 1792156734.5193796},
 'state_sha256': '32c4c3331f34cee7bec72b1f2ab1aa5eae5e67441958b86c9524465c93e214ae'}
_LEGACY_ATTEMPTS = {
    (37841564486, 1): {
        'head_sha': (
            '0d55e3127b0c0e62632e9ec47893230a42984c15'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'a085ab99277abbe06c1550a64219b1d0a4d1533b40835505f0ea96b9'
                '70e4ec6a'
            ),
        },
    },
    (37841576693, 1): {
        'head_sha': (
            '0d55e3127b0c0e62632e9ec47893230a42984c15'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '3f73a55bd095483531a4060a606db2098406c458a01b63151ff49af2'
                'd3ba0f6d'
            ),
        },
    },
    (37894622751, 1): {
        'head_sha': (
            '6f1971d163e39e44d46ffd72bec20795c59df105'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'be40a008f1be6c0069f90e012d9b972c9b19c24407e2f3263bc3d913'
                'e08bd5d6'
            ),
        },
    },
    (37938793622, 1): {
        'head_sha': (
            'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '0a1bd52813fc096b20b6f85f21c8ff474fbcb96c0f818ef1cb4cf2ea'
                '6d2975ce'
            ),
        },
    },
    (38001485160, 1): {
        'head_sha': (
            '2d6491ddf2a37047efc7b996f190f648e647c0c6'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c263'
                '3b967b7f'
            ),
        },
    },
    (38030596902, 1): {
        'head_sha': (
            '6e5922277de22b96874c23a705a7d838512c6caf'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'c50923e51a7e29d158ea3cc5260364a6525b0f6e2ae3e8011d17ec91'
                '9a32b217'
            ),
        },
    },
    (38054056690, 1): {
        'head_sha': (
            '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'c76b5cc241b145af5fbf81f2153bfd06667b66fd8dcf7e96fae538cf'
                '7dea03db'
            ),
        },
    },
    (38071840412, 1): {
        'head_sha': (
            '9b155b179283c8fa08cf56c1a83a54bd19d51814'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'cf445aedbb7621d93d51e92e8e629643efbc284206bcb00d7e30e779'
                '1b865193'
            ),
        },
    },
    (38089653723, 1): {
        'head_sha': (
            '10d99f8ef021c092851bf189ff67307865b8760c'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '9ac5267630c581c07a201317e45a75dd599e7708e8485d6fa9acfdb5'
                'a4440b57'
            ),
        },
    },
    (38117894701, 1): {
        'head_sha': (
            '10d99f8ef021c092851bf189ff67307865b8760c'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '662c8f4d765184b1652bf0bf90b59b059fc5acb076c017c4ae1aac7b'
                '67242efc'
            ),
        },
    },
    (38143260166, 1): {
        'head_sha': (
            '10d99f8ef021c092851bf189ff67307865b8760c'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '029f1413a854819a79b9b1a8313d3ee740965b3cc6a5d5fe7820d312'
                '5aa2f482'
            ),
        },
    },
    (38180365691, 1): {
        'head_sha': (
            '10d99f8ef021c092851bf189ff67307865b8760c'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '436b86e2fa8b26428447eb67828ea981dd0e4f0abe5a0ac3b2a90f01'
                'e020f376'
            ),
        },
    },
    (38212659792, 1): {
        'head_sha': (
            '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'ea1c9da521f9255554deee3cc190b4862eeb1af7eec6fcd2acb8f957'
                '6070a9e2'
            ),
        },
    },
    (38274592732, 1): {
        'head_sha': (
            '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'f12cce1ca0db0a3e24febbed80422134c5006d9584deb4f0083e7224'
                '80fb30f1'
            ),
        },
    },
    (38329763471, 1): {
        'head_sha': (
            '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '0dceb3918f5ccde4271cd1aa96e0239f7be193725ef0032a122e2056'
                'bb1bb8ac'
            ),
        },
    },
    (38414205583, 1): {
        'head_sha': (
            '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '451b7342d8be6fa96e4a85c1055c0bf3f1315d3007a0209d771e7872'
                '964fc2e9'
            ),
        },
    },
    (38482105892, 1): {
        'head_sha': (
            '5dff04768f0e558b4d301d5677c5a2c433c3a64b'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '3eb1f9a41ae9e1fde127d5be8cd0839db05f2014c8c13edd08f0d9eb'
                'bf0fe151'
            ),
        },
    },
    (38516017593, 1): {
        'head_sha': (
            '02792bda730b08eeb1eda86a2cd71af80af3cef7'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '9261fc5d1ad5243f807c0e07f19843198a721011b59eb6302aca956a'
                '35571104'
            ),
        },
    },
    (38534647188, 1): {
        'head_sha': (
            '5146041d2628fb6f03c28d7ee7a2d58c3db8f443'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'd7da6f215f9f9d3f375ea683f918f068b4206cefd6e25d7c37c1870d'
                'd18d6abd'
            ),
        },
    },
    (38562918197, 1): {
        'head_sha': (
            '92235b48ad33b9fa377e0736b689010173d7bfab'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '1332e809e267ff325734c147a642b6b2a0231b99001751844ae4c4b7'
                'bcc4cff5'
            ),
        },
    },
    (38607818328, 1): {
        'head_sha': (
            '92235b48ad33b9fa377e0736b689010173d7bfab'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '3a18d213968200f9075befe8761ce8a443b650a7c87292c098896bb9'
                'e75d9a0e'
            ),
        },
    },
    (38643843320, 1): {
        'head_sha': (
            '0ee73c37e42124c79f0c2005d14afc5db2c8e511'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '25be2f93d8a64675d619029b0f42c2149073b36b04828958d4426c84'
                '2b0d4447'
            ),
        },
    },
    (38647934376, 1): {
        'head_sha': (
            'e1a68725fe923582b5640481e29c7166eb77397c'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '6b5be74a1d0ec194e618f4a6359b4d2373ad199eeed1be580fece200'
                'e6da2ee0'
            ),
        },
    },
    (38684236052, 1): {
        'head_sha': (
            '9b97e13c2e2a56ee77700e40202eb6acd2212cf5'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '3b1ee86ade04ec8c6c5ac1649a31f35df5d9e7ee79ae9540d4f1bf7a'
                '9e7ea5e4'
            ),
        },
    },
    (38685026325, 1): {
        'head_sha': (
            '9b97e13c2e2a56ee77700e40202eb6acd2212cf5'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                'a240ab30d1412ca3f82599ec845171e5b7a3c991c039109f5dd4bd39'
                '2dc66068'
            ),
        },
    },
    (38720083718, 1): {
        'head_sha': (
            'a62a88f5cad62f4fb1ef5230358557d37d2b2bba'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '07cd1e290f09c5f127daeae01e817926f970ca80c5527a6163b95738'
                'b43dcc94'
            ),
        },
    },
    (38721260344, 1): {
        'head_sha': (
            'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '765b894050abb80ba40adbbc5b3c4e717773e01d057f4554838a957f'
                'c82227ff'
            ),
        },
    },
    (38763419676, 1): {
        'head_sha': (
            'f77eed642c4544bbeed009440c406064a4f5c6ad'
        ),
        'workflow_id': 378390320,
        'job_pages': {
            1: (
                '80baf48517becf0bf078224de5dc0c5d6ab32d12d8de3d5574aea32f'
                'b6d36c2e'
            ),
        },
    },
}
_LEGACY_LATEST = (38763419676, 1, 'dk-university-of-copenhagen')
_REVIEWED_MATURE_LOCAL_REENTRY = None
_LEGACY_ACCESS_OBSERVATIONS = []




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
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if check_generation:
            journal_verify()
        return guard
    except BaseException as error:
        fail_publisher_cleanup()
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
    named_before = os.stat(directory, follow_symlinks=False)
    expected = (named_before.st_dev, named_before.st_ino)
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
            or identity != expected
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
        if _JOURNAL_RETAINED_FD is not None:
            journal_verify()
            fd = os.dup(_JOURNAL_RETAINED_FD)
            _JOURNAL_NEW = False
        elif (
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
        return validate_budget(json.loads(raw))
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
    if (
        (info.st_dev, info.st_ino) != identity
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
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
    payload = json.dumps(state, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
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
    """Recheck complete continuity after quiet before the first actual wire."""
    global _LOCAL_FIRST_ACCESS_CHECKED
    if _LOCAL_FIRST_ACCESS_CHECKED:
        return
    latest = latest_family_run()
    current = (_SCANNED_HISTORY, _SCANNED_JOB_DIGESTS, _ATTEMPT_HEADS)
    if latest != _RESTORED_LATEST or current != _RESTORED_HISTORY:
        raise AdapterError(
            "Family history changed before publisher access", "access"
        )
    if os.environ.get("GITHUB_ACTIONS") != "true":
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
    """Pin output parent operations and close only that original directory."""
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
        journal_close_directory(descriptor, (before.st_dev, before.st_ino))



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
    """Retain verified readiness authority through all outer cleanup."""
    if value == "true":
        if _PUBLISHER_GATE_FAILED or not _STATE_TRUSTED:
            raise AdapterError(
                "Untrusted publisher cannot signal ready", "access"
            )
        journal_verify()
    path = os.environ["GITHUB_OUTPUT"]
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    with journal_output(descriptor, path, _JOURNAL_SIGNALS) as (output, _):
        output.write(f"pacing_ready={value}\n")
        output.flush()
        os.fsync(output.fileno())



def invalidate_owned_artifact(root, directory):
    """Invalidate original retained outputs before any failure signalling."""
    journal_invalidate_outputs()



def invalidate_family_artifact():
    """Never reopen a newer output pathname during failure cleanup."""
    journal_invalidate_outputs()



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
    path = os.environ.get("UCPH_PACING_ARTIFACT_PATH")
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
            payload = json.dumps(envelope).encode("utf-8")
            temporary = ".owned-pacing-" + os.urandom(16).hex()
            verify_artifact_directory(root, directory)
            descriptor = os.open(
                temporary,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | os.O_NOFOLLOW
                | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            temporary_info = os.fstat(descriptor)
            with journal_output(
                descriptor, os.path.join(root, temporary), _JOURNAL_EXPORTS
            ) as (stream, pin):
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
                    current = os.stat(
                        temporary, dir_fd=directory, follow_symlinks=False
                    )
                    validate_owned_output(current)
                    if (current.st_dev, current.st_ino) != (
                        temporary_info.st_dev,
                        temporary_info.st_ino,
                    ):
                        raise AdapterError(
                            "Temporary artifact changed", "access"
                        )
                    os.unlink(temporary, dir_fd=directory)
                except BaseException:
                    fail_publisher_cleanup()
                    if not primary:
                        raise



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
        with assessed_response_scope():
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
                    # Preserve the original received-429 stop before any body.
                    # Context closure still runs before settlement; its failure
                    # leaves the owner pending and prevents connection retry.
                    response_constraints_saved = True
                    raise AdapterError(
                        "Publisher429; durable adaptive cooldown retained",
                        "access",
                        429,
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
            if challenge:
                response_constraints_saved = True
                if not robots or status == 429:
                    state["blocked"] = True
                raise AdapterError(
                    "Visible publisher verification or refusal", "access"
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
            return status, response.headers, body
    except BaseException as error:
        if publisher and response_received and not response_exit_completed:
            response_constraints_saved = False
            if isinstance(error, (urllib.error.URLError, TimeoutError, OSError)):
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
                f"/actions/workflows/{workflow}/runs"
                f"?branch=main&per_page=100&page={page}"
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
                is_current = identity == current[0]
                if is_current:
                    if (
                        attempt != current[1]
                        or expected_source != SOURCE_ID
                        or run.get("head_sha") != os.environ.get("GITHUB_SHA")
                        or run.get("status") != "in_progress"
                    ):
                        raise AdapterError(
                            "Current family attempt changed", "access"
                        )
                    current_seen = True
                    last_completed = attempt - 1
                else:
                    if run.get("status") != "completed":
                        raise AdapterError(
                            "Another family publisher is not terminal",
                            "access",
                        )
                    last_completed = attempt
                # A rerun is additional history, never a replacement for its
                # older attempts. Exact wrappers and job inventories bind each.
                for number in range(1, last_completed + 1):
                    wrapper = (
                        run
                        if not is_current and number == attempt
                        else workflow_api(
                            f"/actions/runs/{identity}/attempts/{number}"
                        )
                    )
                    expected = (identity, number, expected_source)
                    revision = (run.get("head_sha"), workflow)
                    candidates.append(
                        completed_identity(wrapper, expected, revision)
                    )
                    _SCANNED_HISTORY[(identity, number)] = revision
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
    ordered = sorted(value for value in candidates if value is not None)
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
    if os.environ.get("GITHUB_ACTIONS") == "true":
        authenticate_current_job()
    else:
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
                        "Unsupported applicable robots timing; "
                        "pending retained",
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
    """Tighten entry and tails using the authentic original job proof only."""
    global _RUN_END, _SOURCE_END, _PHASE_END
    if _RUN_END is not None and _PUBLICATION_WINDOW is not None:
        _RUN_END = min(_RUN_END, _PUBLICATION_WINDOW["job"] - 60)
        _SOURCE_END = min(_SOURCE_END, _RUN_END - 390)
        limit = (
            _SOURCE_END
            if _PHASE_NAME in {"restore", "proof", "snapshot", "source"}
            else (
                _RUN_END
                if _PHASE_NAME == "export"
                else (
                    _RUN_END - 210
                    if _PHASE_NAME == "publication"
                    else _RUN_END - EXPORT_TIMEOUT
                )
            )
        )
        _PHASE_END = min(_PHASE_END, limit)
        arm_phase_timer()



def enter_phase(name, seconds):
    """Each phase starts once and cannot borrow the reserved failure tails."""
    global _PHASE_END, _PHASE_NAME
    if _RUN_END is None:
        raise AdapterError("Entry-frozen lifecycle required", "access")
    if _PHASE_NAME == name:
        check_deadline()
        arm_phase_timer()
        return
    limit = (
        _SOURCE_END
        if name in {"restore", "proof", "snapshot", "source"}
        else (
            _RUN_END
            if name == "export"
            else (
                _RUN_END - _FAILURE_SECONDS - EXPORT_TIMEOUT
                if name == "publication"
                else _RUN_END - EXPORT_TIMEOUT
            )
        )
    )
    _PHASE_END = min(time.monotonic() + seconds, limit)
    _PHASE_NAME = name
    check_deadline()
    arm_phase_timer()



@contextlib.contextmanager
def execution():
    """Own one nonrenewing entry clock including all setup and cleanup work."""
    global _RUN_END, _SOURCE_END, _PHASE_END, _PHASE_NAME
    global _COLLECTION_PREPARED, _TIMER_OWNED
    if (
        _RUN_END is not None
        or threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "ITIMER_REAL")
        or signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0)
    ):
        raise AdapterError("An existing timer prevents collection", "access")
    previous = signal.getsignal(signal.SIGALRM)
    start = time.monotonic()
    _RUN_END = start + RUN_TIMEOUT
    _SOURCE_END = min(start + COLLECTION_TIMEOUT, _RUN_END - 390)
    _PHASE_END, _PHASE_NAME = min(start + 180, _SOURCE_END), "restore"
    _COLLECTION_PREPARED = False
    signal.signal(signal.SIGALRM, phase_alarm)
    _TIMER_OWNED = True
    try:
        arm_phase_timer()
        yield
    finally:
        primary = sys.exc_info()[0] is not None
        try:
            try:
                journal_finish()
            finally:
                try:
                    close_collection_lock()
                finally:
                    journal_finish_outputs()
        except BaseException:
            if not primary:
                raise
        finally:
            if signal.getsignal(signal.SIGALRM) is phase_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
            _TIMER_OWNED = False
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
        start_source_phase()
        return collect_body()
    with execution():
        prepare_collection()
        _COLLECTION_PREPARED = False
        start_source_phase()
        return collect_body()



def legacy_namespace_observation():
    """Read original host constraints without creating or adopting a budget."""
    directory = os.path.join(
        tempfile.gettempdir(),
        "dk-university-of-copenhagen-pacing-" + str(os.getuid()),
    )
    try:
        parent = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        return {
            "namespace": "dk-university-of-copenhagen-pacing",
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
            hashlib.sha256(b"ku.dk").hexdigest(): "publisher",
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
            "namespace": "dk-university-of-copenhagen-pacing",
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/326"
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
    if (run_id, attempt) in _LEGACY_ATTEMPTS:
        expected = {
            identity: (value["head_sha"], value["workflow_id"])
            for identity, value in _LEGACY_ATTEMPTS.items()
        }
        expected_jobs = {
            (*identity, page): digest
            for identity, value in _LEGACY_ATTEMPTS.items()
            for page, digest in value["job_pages"].items()
        }
        if (
            latest != _LEGACY_LATEST
            or _SCANNED_HISTORY != expected
            or _SCANNED_JOB_DIGESTS != expected_jobs
        ):
            raise AdapterError(
                "Reviewed complete legacy history changed", "access"
            )
        # Run-scoped inventories must remain exactly artifact-free for this
        # finite bridge; a rerun never silently erases its prior exact jobs.
        for identity in sorted({key[0] for key in _LEGACY_ATTEMPTS}):
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



def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True



def legacy_collection_observation():
    """The original source had no separate collection lifecycle lock."""
    return {"status": "NOT_APPLICABLE_ORIGINAL_NO_COLLECTION_LOCK"}


def authenticate_current_job():
    """Authenticate current hosted job before trusting or creating state."""
    check_deadline()
    path = os.environ.get("UCPH_PACING_ARTIFACT_PATH")
    if (
        not os.environ.get("GITHUB_TOKEN")
        or not path
        or not os.environ.get("GITHUB_OUTPUT")
    ):
        raise AdapterError("Hosted continuity configuration missing", "access")
    checked_artifact_path(path)
    run, attempt = current_run_identity()
    head = os.environ.get("GITHUB_SHA", "")
    workflow = (
        "YouthOpps/data-pipeline/.github/workflows/fetch-"
        + SOURCE_ID + ".yml@refs/heads/main"
    )
    if (
        os.environ.get("GITHUB_JOB") != "collect"
        or os.environ.get("GITHUB_WORKFLOW_REF") != workflow
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or not re.fullmatch(r"[0-9a-f]{40}", head)
    ):
        raise AdapterError("Untrusted current hosted job context", "access")
    value = workflow_api(
        f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = value.get("jobs")
    if (
        type(value.get("total_count")) is not int
        or value["total_count"] != 1
        or not isinstance(jobs, list)
        or len(jobs) != 1
    ):
        raise AdapterError("Incomplete current hosted job", "access")
    job = jobs[0]
    if (
        not isinstance(job, dict)
        or type(job.get("id")) is not int or job["id"] <= 0
        or job.get("name") != "collect"
        or job.get("workflow_name") != "fetch-" + SOURCE_ID
        or type(job.get("run_id")) is not int or job["run_id"] != run
        or type(job.get("run_attempt")) is not int
        or job["run_attempt"] != attempt
        or job.get("head_sha") != head
        or job.get("head_branch") != "main"
        or job.get("status") != "in_progress"
        or "conclusion" not in job or job["conclusion"] is not None
        or type(job.get("runner_id")) is not int or job["runner_id"] <= 0
        or not isinstance(job.get("started_at"), str)
    ):
        raise AdapterError("Current hosted job identity mismatch", "access")
    try:
        started = datetime.fromisoformat(
            job["started_at"].replace("Z", "+00:00")
        )
        if started.tzinfo is None or started.utcoffset().total_seconds() != 0:
            raise ValueError("Current job start must be UTC")
    except (ValueError, TypeError, OverflowError):
        raise AdapterError(
            "Invalid current hosted job start", "access"
        ) from None
    check_deadline()


def start_source_phase():
    """All preparation debits the one entry-frozen prepublication deadline."""
    clamp_publication_window()
    enter_phase("source", COLLECTION_TIMEOUT)


def page(url):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = previous or url == SOURCE_URL
    try:
        check_deadline()
        delay = check_robots(url)
        root = PublisherHTML(fetch_source(url, min_interval=delay)).root
        check_deadline()
        return root
    finally:
        _INVENTORY_ACTIVE = previous


def main():
    global _PUBLISHING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish", action="store_true",
        help="Write only this source using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    _PUBLISHING = args.publish
    with execution():
        proof_complete = not args.publish
        try:
            prepare_collection()
            if args.publish:
                enter_phase("proof", 30)
                publication_window()
                clamp_publication_window()
                proof_complete = True
            return run_body(args.publish)
        finally:
            primary = sys.exc_info()[0] is not None
            if not proof_complete:
                fail_publisher_cleanup()
            try:
                enter_phase("export", EXPORT_TIMEOUT)
                export_family_artifact()
            except BaseException:
                fail_publisher_cleanup()
                if not primary:
                    raise



_TIMER_OWNED = False


def timer_owner():
    """Require the actual source-owned timer; never adopt an outside alarm."""
    if (
        not _TIMER_OWNED
        or threading.current_thread() is not threading.main_thread()
        or signal.getsignal(signal.SIGALRM) is not phase_alarm
    ):
        raise AdapterError("Unowned lifecycle alarm", "access")


def phase_alarm(signum, frame):
    raise PhaseExpired()


def arm_phase_timer(end=None):
    timer_owner()
    limit = min(_RUN_END, _PHASE_END)
    if end is not None:
        limit = min(limit, end)
    remaining = limit - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextlib.contextmanager
def publication_http_guard(deadline):
    """Compose the original 30-second guard with our owned phase alarm."""
    if not _TIMER_OWNED:
        with legacy_publication_http_guard(deadline) as end:
            yield end
        return
    timer_owner()
    end = min(deadline, time.monotonic() + 30, _PHASE_END, _RUN_END)
    arm_phase_timer(end)
    try:
        yield end
        if time.monotonic() >= end:
            raise AdapterError("Publication call deadline exhausted", "publish")
    finally:
        # Restore the same absolute phase deadline, never a fresh allowance.
        primary = sys.exc_info()[0] is not None
        try:
            arm_phase_timer()
        except BaseException:
            if not primary:
                raise


_JOURNAL_RETAINED_FD = _JOURNAL_RETAINED_ID = None
_JOURNAL_GENERATION = None
_JOURNAL_WRITING = False
_JOURNAL_EXPORTS = []
_JOURNAL_SIGNALS = []


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
    info = validate_state_descriptor(
        descriptor, parent, os.path.basename(_STATE_PATH)
    )
    if (
        (info.st_dev, info.st_ino) != _JOURNAL_RETAINED_ID
        or stat.S_IMODE(info.st_mode) != 0o600
        or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
    ):
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
    if (
        len(raw) > 16384
        or len(raw) != after.st_size
        or journal_metadata(before) != journal_metadata(after)
    ):
        raise JournalAuthorityChanged(
            "Journal read generation changed", "access"
        )
    return (*journal_metadata(after), hashlib.sha256(raw).hexdigest()), raw



def journal_acknowledge(expected):
    """Advance own authority only after exact expected-byte readback."""
    global _JOURNAL_GENERATION
    generation, raw = journal_snapshot()
    if raw != expected:
        raise JournalAuthorityChanged(
            "Own journal mutation was superseded", "access"
        )
    _JOURNAL_GENERATION = generation


def journal_verify():
    """Refuse changed bytes or metadata, including between lock scopes."""
    try:
        generation, raw = journal_snapshot()
        if generation != _JOURNAL_GENERATION:
            raise JournalAuthorityChanged(
                "Journal generation changed", "access"
            )
        return raw
    except BaseException as error:
        fail_publisher_cleanup()
        if isinstance(error, JournalAuthorityChanged):
            raise
        raise JournalAuthorityChanged(
            "Journal authority unavailable", "access"
        ) from error


def journal_retain(descriptor):
    """Retain the first actual journal; a later acquisition cannot rebase it."""
    global _JOURNAL_RETAINED_FD, _JOURNAL_RETAINED_ID, _JOURNAL_GENERATION
    if _JOURNAL_RETAINED_FD is not None:
        journal_verify()
        return
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
    ):
        fail_publisher_cleanup()
        raise JournalAuthorityChanged("Unsafe RW journal acquisition", "access")
    retained = os.dup(descriptor)
    identity = (info.st_dev, info.st_ino)
    _JOURNAL_RETAINED_FD, _JOURNAL_RETAINED_ID = retained, identity
    try:
        generation, _ = journal_snapshot()
        if (
            _JOURNAL_GENERATION is not None
            and generation != _JOURNAL_GENERATION
        ):
            raise JournalAuthorityChanged(
                "Closed journal generation changed", "access"
            )
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
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or (info.st_dev, info.st_ino) != pin["identity"]
        or stat.S_IMODE(info.st_mode) != pin["mode"]
        or info.st_nlink not in (0, 1)
        or fcntl.fcntl(pin["descriptor"], fcntl.F_GETFL) & os.O_ACCMODE
        not in (os.O_RDWR, os.O_WRONLY)
    ):
        raise AdapterError("Retained output authority changed", "access")
    if pin["parent"] is not None:
        parent = os.fstat(pin["parent"])
        if (
            not stat.S_ISDIR(parent.st_mode)
            or (parent.st_dev, parent.st_ino) != pin["parent_identity"]
            or parent.st_uid != pin["parent_owner"]
            or stat.S_IMODE(parent.st_mode) != pin["parent_mode"]
        ):
            raise AdapterError("Retained output parent changed", "access")
    elif named:
        raise AdapterError("Output parent was already released", "access")
    if named:
        parent = os.stat(pin["root"], follow_symlinks=False)
        if (parent.st_dev, parent.st_ino) != pin["parent_identity"]:
            raise AdapterError("Output parent pathname changed", "access")
        current = os.stat(
            pin["name"], dir_fd=pin["parent"], follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != pin["identity"]:
            raise AdapterError("Output pathname changed", "access")
    return info


def journal_retain_output(descriptor, path, pins):
    """Keep the original writable output and parent through outer cleanup."""
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
        not in (os.O_RDWR, os.O_WRONLY)
    ):
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
        journal_close_directory(
            parent, (named_parent.st_dev, named_parent.st_ino)
        )
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
            self.descriptor, fcntl.F_GETFL
        ) & os.O_ACCMODE not in (os.O_RDWR, os.O_WRONLY):
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
                if (
                    stat.S_ISREG(info.st_mode)
                    and info.st_uid == os.getuid()
                    and (info.st_dev, info.st_ino)
                    == (named.st_dev, named.st_ino)
                ):
                    identity = (info.st_dev, info.st_ino)
            if (
                identity is None
                or (info.st_dev, info.st_ino) != identity
                or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
                not in (os.O_RDWR, os.O_WRONLY)
            ):
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
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or (info.st_dev, info.st_ino) != identity
        ):
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
        before
    ) != journal_metadata(after):
        raise AdapterError("Owned export readback differs", "access")


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
