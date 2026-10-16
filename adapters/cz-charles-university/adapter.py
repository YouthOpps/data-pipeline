"""Collect official Charles University funding opportunities."""

import argparse
import contextlib
import copy
import email.utils
import io
import math
import signal
import stat
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import ipaddress
import io
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

SOURCE_ID = "cz-charles-university"
SOURCE_URL = "https://cuni.cz/UKEN-927.html"
WEBSITE_URL = "https://cuni.cz/UKEN-1617.html"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "CZ"
PUBLISHER_TYPE = "university"
ATTRIBUTION = "Charles University"


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
    document = (
        fetch_inventory_page(fetch_source, url, min_interval=delay)
        if url in _INVENTORY_URLS
        else fetch_source(url, min_interval=delay)
    )
    if not re.search(
        r"</body\s*>\s*(?:</html\s*>\s*)?$", document, re.IGNORECASE
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    return PublisherHTML(document).root


def content(root, url):
    """Select reviewed publisher content without navigation or tracking code."""
    host = urllib.parse.urlsplit(url).hostname
    if host == "www.mff.cuni.cz":
        return one(
            [node for node in root.walk() if node.attrs.get("id") == "content"],
            "MFF content",
        )
    if host == "fsv.cuni.cz":
        return one(nodes(root, "div", "main-content"), "FSV content")
    if host == "centrumcarolina.cuni.cz":
        return one(nodes(root, "div", "dright"), "programme content")
    return one(nodes(root, "div", "content"), "CU content")


def programme(
    key,
    title,
    url,
    text,
    facts,
    summary,
    hosts=(),
    eligible=(),
    deadline=None,
    opening=None,
    rolling=False,
    category="scholarships",
):
    """Normalize only directly supported, reviewed funding programme facts."""
    evidence = []
    for fact in facts:
        match = re.search(re.escape(fact), text, re.IGNORECASE)
        if not match:
            raise AdapterError("Changed programme facts: " + key, "parse")
        evidence.append(text[match.start() : match.end()])
    record = make_record(
        title,
        url,
        [category],
        kind="opportunity" if deadline else "programme-overview",
        evidence=evidence,
        host_countries=list(hosts),
    )
    record["id"] = hashlib.sha256(f"{SOURCE_ID}|{key}".encode()).hexdigest()[
        :24
    ]
    record["summary"] = summary
    record["eligible_countries"] = list(eligible)
    record["deadline"] = deadline
    today = datetime.now(timezone.utc).date().isoformat()
    if deadline:
        record["status"] = (
            "expired"
            if deadline < today
            else "unknown" if opening and opening > today else "open"
        )
    elif rolling:
        record["status"] = "open"
    return record


def read_pages():
    """Read the bounded, publicly linked scholarship and mobility inventory."""
    urls = {
        "mobility": SOURCE_URL,
        "hub": WEBSITE_URL,
        "point": "https://cuni.cz/UKEN-924.html",
        "havel": "https://cuni.cz/UKEN-2329.html",
        "international": "https://cuni.cz/UKEN-2497.html",
        "foundations": "https://cuni.cz/UKEN-2528.html",
        "barrande": "https://cuni.cz/UKEN-2330.html",
        "jasso": "https://cuni.cz/UKEN-2338.html",
        "mext": "https://cuni.cz/UKEN-2339.html",
        "germany": "https://cuni.cz/UKEN-2396.html",
        "erasmus": "https://cuni.cz/UKEN-2366.html",
        "rates": "https://cuni.cz/UKEN-2583.html",
        "travel": "https://cuni.cz/UKEN-2584.html",
        "supplement": "https://cuni.cz/UKEN-2585.html",
        "global": "https://cuni.cz/UKEN-2361.html",
        "carolina": "https://centrumcarolina.cuni.cz/CAENG-70.html",
        "acute": "https://cuni.cz/UKEN-1398.html",
        "mff": "https://www.mff.cuni.cz/en/admissions/scholarships",
        "fsv": (
            "https://fsv.cuni.cz/en/admissions/"
            "scholarships-funding-and-fees/development-scholarships"
        ),
    }
    base = "https://www.mff.cuni.cz/en/admissions/scholarships/"
    for key, slug in (
        ("cs", "cstf"),
        ("math", "prague-mathematics-tuition-fee"),
        ("physics", "prague-physics-tuition-fee"),
        ("linguistics", "computational-linguistics"),
    ):
        urls[key] = (
            base
            + slug
            + (
                "-scholarship-2026"
                if key == "linguistics"
                else "-scholarships-2026"
            )
        )
    pages = {key: (url, content(page(url), url)) for key, url in urls.items()}
    pages["german-call"] = german_document(pages["germany"])
    return pages


# These data are reviewed factual summaries, not copied publisher descriptions.
INTERNATIONAL = (
    (
        "aktion",
        "Aktion",
        (
            "MA/PhD final-thesis mobility to Austria for 1–5 months; "
            "EUR1300 monthly. Application months March/October have no "
            "stated year."
        ),
        ("MA and PhD students", "EUR 1,300 per month"),
        ("AT",),
    ),
    (
        "daad-summer",
        "DAAD summer-course scholarship",
        (
            "Approximately one-month course in Germany with "
            "course/accommodation covered and a travel allowance; "
            "yearless October application deadline."
        ),
        ("participation in a summer course", "accommodation covered"),
        ("DE",),
    ),
    (
        "daad-masters",
        "DAAD Study Scholarships: Master’s Degree Programmes in All Fields",
        (
            "Full/partial Master’s study in Germany for 10–24 months; "
            "EUR992 monthly plus travel/study allowance; November has "
            "no stated application year."
        ),
        ("10–24 months", "EUR 992 per month"),
        ("DE",),
    ),
    (
        "daad-research",
        "DAAD Research Grants",
        (
            "Research projects in Germany for doctoral candidates "
            "(2–12 months) or postdoctoral researchers (2–6 months); "
            "EUR1400 monthly plus travel allowance, yearless November "
            "deadline."
        ),
        ("2–12 months", "2–6 months", "EUR 1,400 per month"),
        ("DE",),
    ),
    (
        "daad-cotutelle",
        "DAAD Research Grants for Doctoral Candidates within COTUTELLE",
        (
            "Doctoral cotutelle research in Germany for 7–24 months, "
            "EUR1400 monthly; yearless November application deadline."
        ),
        ("within COTUTELLE", "7–24 months"),
        ("DE",),
    ),
    (
        "future-fund",
        "Czech-German Future Fund study scholarship",
        (
            "BA/MA/PhD humanities/social-science Czech–German research "
            "in Germany for 1–2 semesters. BA EUR650/month, MA/PhD "
            "EUR900/month plus matching one-time materials/travel "
            "contribution; January deadline is yearless."
        ),
        ("Czech-German topics", "EUR 900 per month", "EUR 650 per month"),
        ("DE",),
    ),
    (
        "btha-postgraduate",
        "BTHA postgraduate study funding",
        (
            "Consecutive Master’s, doctoral/postdoctoral study at "
            "Bavarian public universities; EUR992/month for one "
            "academic year, renewable twice (maximum three years); "
            "December deadline has no year."
        ),
        (
            "Funding for Postgraduate Study Programmes",
            "EUR 992 per month",
            "maximum total of 3 years",
        ),
        ("DE",),
    ),
    (
        "btha-summer",
        "BTHA language-course and summer-school funding",
        (
            "Approximately one-month Bavarian summer/language course "
            "with participation and accommodation covered; March "
            "deadline has no year."
        ),
        (
            "Language Courses and Summer Schools in Bavaria",
            "accommodation covered",
        ),
        ("DE",),
    ),
    (
        "gfps",
        "GFPS study and internship scholarship",
        (
            "BA/MA/PhD applicants no older than30; German "
            "study-project stay or internship (three work days/week, "
            "remaining time study). EUR650/month up to five months; "
            "May/October application months are yearless."
        ),
        (
            "not be older than 30 years",
            "EUR 650 per month",
            "maximum of 5 months",
        ),
        ("DE",),
    ),
    (
        "kaad",
        "KAAD scholarship",
        (
            "MA/PhD study/research in Germany requires church/social "
            "engagement, interreligious openness and B1 German. "
            "Master’s up to24 months, doctorate up to36, doctoral "
            "research up to12; actual-cost funding, amount "
            "unspecified; recommended six-month application lead."
        ),
        (
            "church and social engagement",
            "minimum level of B1",
            "up to 36 months",
        ),
        ("DE",),
    ),
    (
        "cern",
        "CERN Doctoral Student Programme",
        (
            "Doctoral dissertation/employment in Switzerland for12–36 "
            "months in applied physics, engineering or computing; "
            "publisher indicative CHF3891/month plus benefits. No "
            "current-cycle deadline stated."
        ),
        ("Applied Physics, Engineering, or Computing", "CHF 3,891 per month"),
        ("CH",),
    ),
    (
        "barrande",
        "Barrande Fellowship",
        (
            "PhD mobility to France: cotutelle up to three years (five "
            "funded months/year), or1–3-month research stay. "
            "Non-Francophone applicants may participate with "
            "supervisor agreement to English. Current hub quotes "
            "EUR1690/1704 monthly, older linked detail quotes EUR1500; "
            "both claims retained, no definitive amount selected. "
            "January deadline is yearless."
        ),
        ("EUR 1,690 per month", "EUR 1,704 per month", "non-Francophone"),
        ("FR",),
    ),
    (
        "ceepus",
        "CEEPUS mobility scholarship",
        (
            "BA/MA/PhD exchange in Central/South-Eastern Europe: "
            "thesis1–2 months, semester3–10 months, study visit3–5 "
            "days or summer school6–30 days. Host-paid amounts and "
            "deadlines vary; no exact country list inferred."
        ),
        ("Central and South-Eastern Europe", "amounts vary"),
        (),
    ),
    (
        "fulbright",
        "Fulbright study and research funding",
        (
            "MA/PhD study, internship or research in the United "
            "States; full degrees or short stays up to12 months. "
            "Academic excellence and civic engagement emphasized; "
            "programme-specific amounts/deadlines unstated."
        ),
        ("civic engagement", "up to 12 months"),
        ("US",),
    ),
)
FOUNDATIONS = (
    (
        "sophia",
        "Sophia Foundation study funding",
        (
            "BA/MA study abroad, primarily economics/law but other "
            "disciplines eligible. Up to CZK15000 one-time "
            "expense-based funding; Czech application, multiple "
            "application rounds with no dates stated."
        ),
        ("Sophia Foundation",),
        (),
    ),
    (
        "education-foundation",
        "Foundation for the Development of Education scholarship",
        (
            "BA/MA applicants up to25 whose Erasmus/other-exchange "
            "funding is insufficient; technical/medical fields "
            "preferred. CZK10000–20000, apply two months before "
            "departure; no fixed date stated."
        ),
        ("Foundation for the Development of Education",),
        (),
    ),
    (
        "bakala",
        "Bakala Foundation scholarship",
        (
            "Excellent BA/MA applicants under34; amount depends on "
            "family finances and destination. Yearless November "
            "application month."
        ),
        ("Bakala Foundation",),
        (),
    ),
    (
        "bader",
        "Bader Scholarship",
        (
            "PhD history/theory of art/architecture support for "
            "international study, internship, travel, equipment or "
            "literature; USD12500 award, usually January without a "
            "stated year. Three awards are capacity, not separate "
            "opportunities."
        ),
        ("Bader Scholarship",),
        (),
    ),
    (
        "experentia",
        "Experentia chemistry scholarship",
        (
            "Organic/bioorganic/medicinal chemistry overseas research "
            "up to12 months for applicants up to35 with recent "
            "doctoral qualification. Publisher groups PhD/postdoctoral "
            "applicants but also states recent-PhD completion; "
            "qualification ambiguity retained. March has no stated "
            "year."
        ),
        ("Experentia",),
        (),
    ),
    (
        "sylff",
        "SYLFF doctoral scholarship",
        (
            "Full-time social-science/humanities PhD applicants "
            "through third year, with societal-impact leadership "
            "potential. USD12500/year includes international mobility; "
            "usually late April without a year, apply through faculty "
            "coordinators."
        ),
        ("SYLFF",),
        (),
    ),
    (
        "visegrad",
        "Visegrad scholarship",
        (
            "Master’s/postgraduate citizens of "
            "Czechia/Hungary/Poland/Slovakia studying in another "
            "Visegrad country or explicitly listed "
            "eastern/southeastern partners. EUR3500 per semester "
            "for1–2 semesters; April has no stated year, own-country "
            "study excluded."
        ),
        ("Visegrad Fund",),
        (),
    ),
)


COUNTRY_NAMES = {
    "United Arab Emirates": "AE",
    "Andorra": "AD",
    "Afghanistan": "AF",
    "Antigua and Barbuda": "AG",
    "Albania": "AL",
    "Armenia": "AM",
    "Angola": "AO",
    "Argentina": "AR",
    "Australia": "AU",
    "Azerbaijan": "AZ",
    "Bosnia and Herzegovina": "BA",
    "Barbados": "BB",
    "Bangaldesh": "BD",
    "Belgium": "BE",
    "Burkina Faso": "BF",
    "Bulgaria": "BG",
    "Bahrain": "BH",
    "Burundi": "BI",
    "Benin": "BJ",
    "Brunei": "BN",
    "Bolivia": "BO",
    "Brazil": "BR",
    "Bahamas": "BS",
    "Bhutan": "BT",
    "Botswana": "BW",
    "Belarus": "BY",
    "Belize": "BZ",
    "Canada": "CA",
    "Democratic Republic of Congo": "CD",
    "Democratic Republic of the Congo": "CD",
    "Central African Republic": "CF",
    "Republic of Congo": "CG",
    "Switzerland": "CH",
    "Republic of Côte d'Ivoire": "CI",
    "Cook Islands": "CK",
    "Chile": "CL",
    "Cameroon": "CM",
    "China": "CN",
    "Colombia": "CO",
    "Costa Rica": "CR",
    "Cuba": "CU",
    "Cape Verde": "CV",
    "Cyprus": "CY",
    "Czech Republic": "CZ",
    "Germany": "DE",
    "Djibouti": "DJ",
    "Denmark": "DK",
    "Dominica": "DM",
    "Algeria": "DZ",
    "Ecuador": "EC",
    "Estonia": "EE",
    "Egypt": "EG",
    "Eritrea": "ER",
    "Spain": "ES",
    "Ethiopia": "ET",
    "Finland": "FI",
    "Fiji": "FJ",
    "Federated States of Micronesia": "FM",
    "Faroe Islands": "FO",
    "France": "FR",
    "Gabon": "GA",
    "United Kingdom": "GB",
    "Grenada": "GD",
    "Georgia": "GE",
    "Ghana": "GH",
    "Gambia": "GM",
    "Guinea": "GN",
    "Equatorial Guinea": "GQ",
    "Greece": "GR",
    "Guatemala": "GT",
    "Guinea-Bissau": "GW",
    "Guyana": "GY",
    "Hong Kong": "HK",
    "Honduras": "HN",
    "Croatia": "HR",
    "Haiti": "HT",
    "Hungary": "HU",
    "Indonesia": "ID",
    "Ireland": "IE",
    "Israel": "IL",
    "India": "IN",
    "Iraq": "IQ",
    "Iran": "IR",
    "Iceland": "IS",
    "Italy": "IT",
    "Jamaica": "JM",
    "Jordan": "JO",
    "Japan": "JP",
    "Kenya": "KE",
    "Kyrgyzstan": "KG",
    "Cambodia": "KH",
    "Kiribati": "KI",
    "Comoros": "KM",
    "Saint Kitts and Nevis": "KN",
    "Democratic People's Republic of Korea": "KP",
    "Kuwait": "KW",
    "Lao People's Democratic Republic": "LA",
    "Lebanon": "LB",
    "Saint Lucia": "LC",
    "Liechtenstein": "LI",
    "Sri Lanka": "LK",
    "Liberia": "LR",
    "Lesotho": "LS",
    "Lithuania": "LT",
    "Luxembourg": "LU",
    "Latvia": "LV",
    "Libya": "LY",
    "Morocco": "MA",
    "Monaco": "MC",
    "Moldova": "MD",
    "Montenegro": "ME",
    "Madagascar": "MG",
    "Marshall Islands": "MH",
    "North Macedonia": "MK",
    "Mali": "ML",
    "Myanmar": "MM",
    "Mongolia": "MN",
    "Macao": "MO",
    "Mauritania": "MR",
    "Malta": "MT",
    "Mauritius": "MU",
    "Maldives": "MV",
    "Malawi": "MW",
    "Mexico": "MX",
    "Malaysia": "MY",
    "Mozambique": "MZ",
    "Namibia": "NA",
    "Niger": "NE",
    "Nigeria": "NG",
    "Nicaragua": "NI",
    "Netherlands": "NL",
    "Norway": "NO",
    "Nepal": "NP",
    "Nauru": "NR",
    "Niue": "NU",
    "New Zealand": "NZ",
    "Oman": "OM",
    "Panama": "PA",
    "Peru": "PE",
    "Papua New Guinea": "PG",
    "Philippines": "PH",
    "Pakistan": "PK",
    "Poland": "PL",
    "Palestine": "PS",
    "Portugal": "PT",
    "Palau": "PW",
    "Paraguay": "PY",
    "Qatar": "QA",
    "Romania": "RO",
    "Serbia": "RS",
    "Territory of Russia recognized under international law": "RU",
    "Rwanda": "RW",
    "Saudi Arabia": "SA",
    "Solomon Islands": "SB",
    "Seychelles": "SC",
    "Sudan": "SD",
    "Sweden": "SE",
    "Singapore": "SG",
    "Slovenia": "SI",
    "Slovakia": "SK",
    "Sierra Leone": "SL",
    "San Marino": "SM",
    "Senegal": "SN",
    "Somalia": "SO",
    "Suriname": "SR",
    "South Sudan": "SS",
    "Saint Thomas and Principe": "ST",
    "El Salvador": "SV",
    "Syria": "SY",
    "Swaziland": "SZ",
    "Chad": "TD",
    "Togo": "TG",
    "Thailand": "TH",
    "Tajikistan": "TJ",
    "East Timor": "TL",
    "Turkmenistan": "TM",
    "Tunisia": "TN",
    "Tonga": "TO",
    "Turkey": "TR",
    "Trinidad and Tobago": "TT",
    "Tuvalu": "TV",
    "Taiwan": "TW",
    "Tanzania": "TZ",
    "Territory of Ukraine recognized under international law": "UA",
    "Uganda": "UG",
    "United States of America": "US",
    "Uruguay": "UY",
    "Uzbekistan": "UZ",
    "Vatican City State": "VA",
    "Saint Vincent and the Grenadines": "VC",
    "Venezuela": "VE",
    "Vietnam": "VN",
    "Vanuatu": "VU",
    "Samoa": "WS",
    "Kosovo": "XK",
    "Yemen": "YE",
    "South Africa": "ZA",
    "Zambia": "ZM",
    "Zimbabwe": "ZW",
    "Austria": "AT",
}


def named_countries(names, excluded=()):
    """Normalize actual destination names, retaining declared exclusions."""
    countries = set()
    for name in names:
        name = name.strip().rstrip(".")
        if name not in COUNTRY_NAMES:
            raise AdapterError("Unreviewed destination name: " + name, "parse")
        code = COUNTRY_NAMES[name]
        if code not in excluded:
            countries.add(code)
    return sorted(countries)


def erasmus_hosts(pages):
    """Read both the traditional and international actual destination lists."""
    rate_node = pages["rates"][1]
    table = one(
        [
            item
            for item in nodes(rate_node, "table")
            if "Group of countries" in item.text()
        ],
        "Erasmus destination rate table",
    )
    groups = []
    for row in nodes(table, "tr"):
        cells = nodes(row, "td")
        if cells and "," in cells[0].text():
            groups.extend(cells[0].text().split(","))
    standard = named_countries(groups)
    if len(standard) != 33:
        raise AdapterError("Changed Erasmus rate-group inventory", "parse")
    global_text = pages["global"][1].text()
    start = "Partner countries of Region 14: "
    middle = "Partner countries in the region 13: "
    last = "Partner countries from region 1–12: "
    try:
        part = global_text.split(start, 1)[1]
        a, part = part.split(middle, 1)
        b, part = part.split(last, 1)
        c, _ = part.split(" ATTENTION!", 1)
    except (IndexError, ValueError) as error:
        raise AdapterError(
            "Changed international destination list", "parse"
        ) from error
    international = named_countries(
        (a + "," + b + "," + c).split(","), excluded=("BY", "RU")
    )
    return standard, international


REVIEWED_CONDITIONS = {
    "hub": ("5255299491cb7b43b89fad7f9e088bb0a914ddbb123cb469f707113afff43883"),
    "point": (
        "12bc51e5a885417939faf9c98f09cc260aab9c9f386f7cdd3bc41646f066c80f"
    ),
    "havel": (
        "34125e878d31e1f1509118599d3f3e6941caf209b400f5849c27d0f4fe0dddee"
    ),
    "international": (
        "01a034298c6997f0a3479f84e6f4b7741817c66d540b87a78e04b5a40e5df034"
    ),
    "foundations": (
        "692497332ce71bbe9476c2501a6d6f2bb7ca9e41a25c476162d027b6e4852835"
    ),
    "barrande": (
        "a4befd9d13543684d5d7ef630a775106a438f4d39e7abae69a97ef1fff00b766"
    ),
    "jasso": (
        "71fec10dce9c6aad65e0347ff6123de309fd40b11c929413da742657e6dde13c"
    ),
    "mext": (
        "3da5a9b61c0562aa00dd8b5a3ebe6bde9bdca64aea3ceace9ca439005fa86f76"
    ),
    "germany": (
        "17a034abc608a556b4f5594999780eea6df072e4283e7e8e93c45d5e242b20aa"
    ),
    "erasmus": (
        "e24f4436caaa6d9fc5bdd28ce2c2fd2b1d5ab318e7f20b729565a8f05a45a7ff"
    ),
    "rates": (
        "1c8f180efbf5a22f1d4f3458cc5cdb6beb4252f24585f4b63d7e3b5c9ffaf8d6"
    ),
    "travel": (
        "d72fb332ba656ffe5b441e8b0595ac7c377051d6ab94209beed81997f5533838"
    ),
    "supplement": (
        "458b944fc51a7de3e23e14e86e61ba2dc5e6d4875dd472d68d066ebde822de2a"
    ),
    "global": (
        "e2071e5eac6dc7ac364f21af523b70fb2f37fe9f6ddab307f36508fca162023e"
    ),
    "carolina": (
        "9f41f062d6153de1832d1b5220a55e746c97753a3a408d7e3127c8b72dbb4042"
    ),
    "acute": (
        "fb39f44d5f9df587f85dd09f1a05651c3492f22c388690293f6d0044d4cd3ab0"
    ),
    "mff": ("13fdac2620fa5c0f29f283a46fa767c6f65d933505d080bdf7dc9d98c3e54624"),
    "fsv": ("81582bed2f125c6dcaea478a2fdf6166c378eb4aa2f4fd8fa7eca4a002cbee3a"),
    "cs": ("e3aba2b8cbe82ad391cde69a2af96d416f3d3d434fe7330b082d16a6032de305"),
    "math": (
        "9f67c4336d1d007b32b78e01e62c8ed2e93d981b2be63a1fe051e97543c39498"
    ),
    "physics": (
        "9e571bf01d72b4de4e4ce78b03d2fb92b5698cac9071b0c71fcd604a112700a3"
    ),
    "linguistics": (
        "75cefe115574bb8f20bb37e113a92819b261583eeb6b065ecdb4831b2025d06b"
    ),
}


MOBILITY_CONDITIONS = {
    "completion": (
        "8f28594cbad1a2ea43ddb279bf902b83997e3e665efb42b7a568f1cb0485257c"
    ),
    "conditions": (
        "f78a868ce186e0bfeba9d2ad11f8c07f70d7cd44d4cd65ae7462179543a20723"
    ),
    "dates": (
        "1430c39546be6133a2b333334f15b173f901d65efe38e26234874d0790c3cc9b"
    ),
    "eligibility": (
        "a842654d130f594f6844d27ee5b2e91f783f6c0b6abe0fb9c3e2dbb38f1b6cb2"
    ),
}


def mobility_sections(node, url):
    """Retain complete material sections within the reviewed page layout."""
    body = one(nodes(node, "div", "node-content"), "Mobility Fund body")
    layout = [child for child in node.children if not isinstance(child, str)]
    if (
        len(layout) != 3
        or any(
            isinstance(child, str) and child.strip() for child in node.children
        )
        or layout[0].tag != "h1"
        or layout[0].text() != "Mobility Fund"
        or layout[1] is not body
        or "content-footer" not in layout[2].attrs.get("class", "").split()
    ):
        raise AdapterError("Mobility Fund content boundary changed", "parse")
    sections = [
        child
        for child in body.children
        if not isinstance(child, str)
        and child.tag == "div"
        and "overflowHidden" in child.attrs.get("class", "").split()
    ]
    if len(sections) != 5:
        raise AdapterError("Mobility Fund section inventory changed", "parse")

    def empty_anchor(child, identity):
        return (
            child.tag == "div"
            and child.attrs == {"class": "anchorDiv", "id": identity}
            and all(
                isinstance(part, str) and not part.strip()
                for part in child.children
            )
        )

    elements = [child for child in body.children if not isinstance(child, str)]
    if len(elements) != 10 or any(
        not empty_anchor(elements[index * 2], identity)
        or elements[index * 2 + 1] is not sections[index]
        for index, identity in enumerate(("1", "6", "7", "3", "4"))
    ):
        raise AdapterError("Mobility Fund section boundaries changed", "parse")
    for child in body.children:
        if isinstance(child, str):
            unexpected = child.strip()
        else:
            unexpected = child not in sections and child.text()
        if unexpected:
            raise AdapterError("Unexpected Mobility Fund content", "parse")
    expected = (
        ("eligibility", []),
        (
            "dates",
            [
                ("h2", "Important Deadlines"),
                ("h3", "Autumn Round of the CU Mobility Fund"),
                ("h3", "Spring Round of the CU Mobility Fund"),
            ],
        ),
        (
            "conditions",
            [
                ("h2", "Application and Selection Procedure"),
                ("h3", "Application Attachments"),
                ("h3", "Changes to a Submitted Application"),
                ("h3", "Evaluation of Applications"),
                ("h3", "Financial Conditions"),
                ("h3", "Results and Payment of the Scholarship"),
                ("h3", "Changes to or Cancellation of Mobility"),
            ],
        ),
        (
            "completion",
            [("h2", "Obligations After Completion of the Mobility")],
        ),
        (
            "contacts",
            [
                ("h2", "Contacts"),
                ("h3", "University Mobility Fund administrator:"),
            ],
        ),
    )
    result = {}
    for section, (key, headings) in zip(sections, expected):
        actual = [
            (child.tag, child.text())
            for child in section.walk()
            if child.tag in ("h1", "h2", "h3", "h4", "h5", "h6")
        ]
        if actual != headings:
            raise AdapterError(
                "Mobility Fund headings changed: " + key, "parse"
            )
        if key == "contacts":
            if any(
                isinstance(child, str) and child.strip()
                for child in section.children
            ):
                raise AdapterError(
                    "Unexpected Mobility Fund contact text", "parse"
                )
            children = [
                child
                for child in section.children
                if not isinstance(child, str)
            ]
            if [child.tag for child in children] != [
                "h2",
                "p",
                "br",
                "div",
                "div",
            ]:
                raise AdapterError(
                    "Mobility Fund contact boundary changed", "parse"
                )
            if children[1].text() != (
                "If you have any questions, your first point of contact is "
                "the faculty Mobility Fund administrator ."
            ):
                raise AdapterError(
                    "Mobility Fund contact purpose changed", "parse"
                )
            if (
                not empty_anchor(children[3], "5")
                or children[2].children
                or children[-1].attrs != {"class": "overflowHidden"}
                or any(
                    isinstance(child, str) and child.strip()
                    for child in children[-1].children
                )
            ):
                raise AdapterError(
                    "Unexpected Mobility Fund contact container", "parse"
                )
            personal = [
                child
                for child in children[-1].children
                if not isinstance(child, str)
            ]
            if [child.tag for child in personal] != [
                "h3",
                "p",
                "p",
                "p",
                "p",
                "p",
                "br",
            ]:
                raise AdapterError(
                    "Mobility Fund contact tail changed", "parse"
                )
            if (
                personal[2].text() != "International Relations Office"
                or personal[3].text() != "Rectorate of CU"
                or not re.fullmatch(r"Tel\.: [0-9 +()-]+", personal[4].text())
                or personal[5].text()
                or personal[6].children
            ):
                raise AdapterError(
                    "Unexpected Mobility Fund contact tail", "parse"
                )
            continue
        links = sorted(
            {
                (
                    anchor.text(),
                    urllib.parse.urljoin(url, anchor.attrs["href"]),
                )
                for anchor in nodes(section, "a")
                if anchor.attrs.get("href")
            }
        )
        result[key] = json.dumps(
            {"text": section.text(), "links": links},
            ensure_ascii=False,
            sort_keys=True,
        )
    return result


def parse_inventory(pages):
    """Collect genuine programmes and calls from bounded own-publisher pages."""
    for key, expected in REVIEWED_CONDITIONS.items():
        text = pages[key][1].text().split("Last change:")[0]
        if hashlib.sha256(text.encode()).hexdigest() != expected:
            raise AdapterError(
                "Publisher conditions changed; review source page: " + key,
                "parse",
            )
    mobility_url, mobility_node = pages["mobility"]
    for key, text in mobility_sections(mobility_node, mobility_url).items():
        if (
            hashlib.sha256(text.encode()).hexdigest()
            != MOBILITY_CONDITIONS[key]
        ):
            raise AdapterError(
                "Mobility Fund conditions changed; review section: " + key,
                "parse",
            )
    records = []

    def add(page_key, key, title, facts, summary, **options):
        url, node = pages[page_key]
        result = programme(
            key, title, url, node.text(), facts, summary, **options
        )
        records.append(result)
        return result

    mobility = add(
        "mobility",
        "mobility-autumn-2026",
        "Charles University Mobility Fund – Autumn 2026",
        ("October 30, 2026, 2:00 p.m.", "October 1, 2026", "Mobility Fund"),
        (
            "Autumn 2026 partial mobility funding for full-time CU BA/MA/PhD "
            "students within standard duration +1 year. Study/internship "
            "≥30 days; short PhD ≤29 days; medical Master’s 21–29 days. "
            "Stays 1 Nov 2026–30 Sep 2027. No retrospective, Erasmus+, "
            "inter-university scholarship or GAUK funding. Acceptance and "
            "academic approval required; study B2 with exemptions. Long "
            "stays: CZK 15,000/month Europe or 20,000 outside, cap 70,000; "
            "short rates vary. Central deadline 30 Oct 2026 at 14:00, zone "
            "unstated (date-only); faculty 20–30 Oct."
        ),
        deadline="2026-10-30",
        opening="2026-10-01",
    )
    mobility["classification"]["evidence"].append(
        "Detailed eligibility and benefits: "
        + (
            "Autumn2026 partial mobility grant for full-time CU "
            "BA/MA/PhD students within standard study period plus one "
            "year. One application call covers study/internship of at "
            "least30 days, short PhD mobility up to29 days and medical "
            "Master’s traineeships21–29 days. Stays "
            "November2026–September2027; no retrospective funding or "
            "concurrent Erasmus+, inter-university scholarship or GAUK "
            "funding. Category C needs an event programme and acceptance "
            "of a contribution for active participation. Payment is not "
            "before December31,2026. Acceptance and academic "
            "approval required; study category requires B2 language "
            "evidence with stated exceptions. Indicative monthly "
            "support CZK15000 Europe/20000 outside, maximum70000 for "
            "long stays; short categories CZK10000–20000 "
            "Europe/15000–25000 outside. Faculty "
            "deadlines20–30October; central deadline October30,2026 "
            "at14:00, timezone unstated; date-only normalized."
        )
    )
    add(
        "hub",
        "doctoral-income",
        "Charles University doctoral studying income",
        ("1.2", "full-time"),
        (
            "Full-time doctoral study during the standard four-year "
            "period qualifies for doctoral studying income of at "
            "least1.2 times the Czech minimum wage. Income may combine "
            "scholarship and salary; exact wage/amount not inferred."
        ),
        hosts=("CZ",),
    )
    add(
        "hub",
        "stars",
        "STARS doctoral scholarship",
        ("STARS scholarship", "talented PhD students"),
        (
            "Charles University’s own scholarship page identifies a "
            "Faculty of Science scholarship for talented PhD students. "
            "Amount, application dates and further conditions are not "
            "stated on this accessible page. Linked STARS detail has a "
            "bot-verification gate and is not accessed by the "
            "collector."
        ),
    )
    add(
        "hub",
        "gi-bill",
        "GI Bill at Charles University Faculty of Social Sciences",
        ("GI Bill", "U.S. veterans only"),
        (
            "Charles University explicitly states that its Faculty of "
            "Social Sciences participates in GI Bill for U.S. veterans "
            "only. Veteran status is the supported condition; "
            "citizenship, funding amount and application dates are "
            "unspecified. This is a limited programme overview, not a "
            "claim of an open application call."
        ),
    )
    add(
        "havel",
        "havel",
        "Václav Havel Bursary",
        ("repression",),
        (
            "International students whose studies are hindered by "
            "oppressive regimes may seek CU financial support. The "
            "detailed page requires admission/enrolment in a CU degree "
            "programme; the general hub also mentions foundation "
            "programmes. Contributions to Czech-language preparation "
            "may be possible. Evidence of repression and translated "
            "documents required. Application dates March31/October31 "
            "have no stated year; amount unknown."
        ),
        hosts=("CZ",),
    )
    add(
        "fsv",
        "fsv-2026-27",
        "FSV UK SCHOLARS scholarship 2026/2027",
        ("April 30, 2026", "75 000"),
        (
            "One-time approximately CZK75000 living-cost support for "
            "financially disadvantaged fee-paying Faculty of Social "
            "Sciences BA/MA students. Apply as incoming student, BA "
            "year1/2 or MA year1 for the next year. No tuition waiver; "
            "renewal needs a new application. English application, "
            "motivation up to1000 words and academic recommendation "
            "required; programme application/payment also due "
            "April30,2026. Payment after winter registration and "
            "tuition settlement, no later than December2026 subject to "
            "conditions."
        ),
        deadline="2026-04-30",
        hosts=("CZ",),
    )
    for key, title, summary, facts, hosts in INTERNATIONAL:
        add("international", key, title, facts, summary, hosts=hosts)
    for key, title, summary, facts, hosts in FOUNDATIONS:
        item = add("foundations", key, title, facts, summary, hosts=hosts)
        if key == "visegrad":
            item["eligible_countries"] = ["CZ", "HU", "PL", "SK"]
            item["host_countries"] = [
                "AL",
                "AM",
                "AZ",
                "BA",
                "BY",
                "CZ",
                "GE",
                "HU",
                "MD",
                "ME",
                "MK",
                "PL",
                "RS",
                "SK",
                "UA",
                "XK",
            ]
    add(
        "jasso",
        "jasso",
        "JASSO scholarship",
        ("80,000",),
        (
            "Students admitted to a Japanese partner university may "
            "receive JPY80000/month for a stay up to12 months. "
            "Selection occurs during the host application process; no "
            "fixed current deadline stated."
        ),
        hosts=("JP",),
    )
    add(
        "mext",
        "mext",
        "MEXT partner-university scholarship",
        ("117,000",),
        (
            "Japanese government support for a one-year stay at "
            "selected partner universities beginning October of the "
            "following academic year. JPY117000/month plus travel "
            "support; December/January annual announcements are "
            "yearless. One-student partner capacity is not a separate "
            "scholarship."
        ),
        hosts=("JP",),
    )
    add(
        "erasmus",
        "erasmus",
        "Erasmus+ student mobility funding",
        ("scholarship",),
        (
            "Partial living-cost funding for approved Erasmus+ student "
            "mobility, subject to a participant agreement. Long "
            "study/traineeship grants depend on destination "
            "(EUR540–660 or690–810/month); short mobility is EUR79/day "
            "for days1–14 and56/day for days15–30. Current2026/27 "
            "distance-based travel rates rangeEUR28–1735 standard "
            "or56–1735 green; green travel needs an environmentally "
            "friendly journey in at least one direction. No generic "
            "application deadline stated."
        ),
    )
    add(
        "supplement",
        "erasmus-supplement",
        "Erasmus+ supplementary scholarship",
        ("limited opportunities",),
        (
            "Additional conditional mobility funding for students with "
            "special needs or limited opportunities, including "
            "disability, financial disadvantage, children, dietary "
            "restrictions or orphan’s pension. Supporting "
            "documentation is required; no fixed amount/deadline "
            "stated."
        ),
    )
    add(
        "global",
        "erasmus-international",
        "Erasmus+ international mobility scholarship",
        ("700 EUR/month", "60 days"),
        (
            "CU-enrolled accredited BA/MA/PhD students may apply "
            "through faculty selection for funded2–12-month mobility "
            "where traditional Erasmus is unavailable. Total Erasmus "
            "participation maximum12 months per cycle, or24 for "
            "undivided Master’s. Region13/14 study EUR660/month and "
            "traineeship810; regions1–12 EUR700/month. No travel to "
            "Belarus/Russia allowed despite their appearance in the "
            "regional list; no fixed application date stated."
        ),
    )
    add(
        "acute",
        "acute",
        "Bursary for Students in Acute Difficulties",
        ("CZK 100,000", "throughout the calendar year"),
        (
            "Discretionary emergency assistance for CU students when "
            "other assistance is unavailable or insufficient. "
            "Circumstances include bereavement, dependent "
            "care/parenthood, health issues, disasters/accidents or "
            "crime. Applications with explanation/evidence may go to "
            "dean and Rector; lump-sum amount depends on need, "
            "combined annual maximumCZK100000. Rector applications "
            "accepted throughout the calendar year; no automatic "
            "entitlement."
        ),
        rolling=True,
    )
    add(
        "carolina",
        "ukraine-emergency",
        "Bursary for Students in Acute Difficulties related to Ukraine",
        ("crisis in Ukraine", "monthly income and expenses"),
        (
            "Emergency support related to the Ukraine crisis requires "
            "a signed posted application describing monthly income, "
            "expenses and intended use. Financial/residence evidence "
            "requested; affidavit possible if documents unavailable. "
            "No amount, fixed application date or citizenship "
            "requirement stated."
        ),
    )
    add(
        "carolina",
        "accommodation",
        "Charles University accommodation bursary",
        ("Accommodation bursaries", "at most two incidents"),
        (
            "Requested support for uninterrupted full-time students "
            "not studying as foreign-language self-payers, with at "
            "most two academic failures and within standard study "
            "duration plus one year. Basic support requires residence "
            "outside the teaching district (outside Prague when "
            "studying there); only one bursary for concurrent "
            "programmes. No amount/current deadline stated."
        ),
    )
    add(
        "carolina",
        "hlavka",
        "Josef, Marie, and Zdenka Hlávka Foundation Scholarship",
        ("not automatically available", "standard period"),
        (
            "Conditional support for students already included in the "
            "foundation’s programme for student-citizens permanently "
            "living abroad. Enrollment in the relevant study unit and "
            "standard programme duration required. Not automatically "
            "available; exact nationality, amount and deadline "
            "unstated."
        ),
    )
    add(
        "carolina",
        "eu4belarus",
        "EU4Belarus scholarship",
        ("Belarusian students", "2020–2022"),
        (
            "For Belarusian students displaced by repression "
            "during2020–2022 who began university study in Lithuania, "
            "Latvia, Czechia or Poland no earlier than2020/2021. No "
            "current application dates or benefit amounts stated on "
            "CU’s page."
        ),
        hosts=("LT", "LV", "CZ", "PL"),
        eligible=("BY",),
    )
    for key, title, field in (
        (
            "cs-ba",
            "Computer Science Tuition Fee Scholarship – Bachelor’s 2026",
            "Bachelor",
        ),
        (
            "cs-ma",
            "Computer Science Tuition Fee Scholarship – Master’s 2026",
            "Master",
        ),
        (
            "math",
            "Prague Mathematics Tuition Fee Scholarship 2026",
            "Mathematics",
        ),
        ("physics", "Prague Physics Tuition Fee Scholarship 2026", "Physics"),
        (
            "linguistics",
            "Computational Linguistics Scholarship 2026",
            "Language Technologies",
        ),
    ):
        source = "cs" if key.startswith("cs-") else key
        add(
            source,
            "mff-" + key,
            title,
            ("2026", field),
            "Academic-excellence award for applicants to the English-taught "
            + field
            + " programme at CU Mathematics and Physics starting2026/27. "
            + "Tuition coverage EUR4200/year for EU students or7100 otherwise, "
            + (
                "for three Bachelor’s years. "
                if key == "cs-ba"
                else "for two Master’s years. "
            )
            + "English application documents include CV, motivation, "
            + "transcripts and two recommendations, due April30,2026. "
            + (
                "No other external living-allowance scholarship/grant; "
                if key == "linguistics"
                else (
                    "New entrants only; no other "
                    "scholarship/maintenance grant; "
                )
            )
            + "continued support requires full-time study and progress."
            + (
                " CZK120000 living allowance/year; first-semester progress "
                "may reduce support."
                if key == "linguistics"
                else ""
            ),
            hosts=("CZ",),
            deadline="2026-04-30",
        )
    add(
        "mff",
        "mff-math-phd",
        "Mathematics PhD financial support programme",
        ("20000 CZK", "25000 CZK"),
        (
            "Active mathematics PhD students are offered guaranteed "
            "monthly tax-exempt incomeCZK20000; projects/teaching may "
            "increase income, typical total approximately25000. "
            "Contact department or vice-dean; no fixed application "
            "date stated."
        ),
        hosts=("CZ",),
    )
    add(
        "mff",
        "mff-cs-phd",
        "Computer Science PhD employment programme",
        ("20,000 CZK", "12,500 CZK", "separate application"),
        (
            "Computer Science PhD students supervised in the faculty "
            "may apply separately for academic-assistant employment. "
            "Minimum salaryCZK20000/month plus tax-free stipend12500; "
            "project participation can increase income. "
            "Published2024/25 median project increment11000 is "
            "historical context, not guaranteed current benefit. No "
            "fixed application deadline stated."
        ),
        hosts=("CZ",),
        category="jobs",
    )
    standard_hosts, global_hosts = erasmus_hosts(pages)
    for record in records:
        if record["title"] == "Erasmus+ student mobility funding":
            record["host_countries"] = standard_hosts
        elif record["title"] == "Erasmus+ international mobility scholarship":
            record["host_countries"] = global_hosts
    records.extend(german_programmes(pages))
    validate_records(records)
    return records


def collect_inventory():
    """Return one complete validated, non-publishing collection."""
    return parse_inventory(read_pages())


def german_document(germany):
    """Read the actually linked application notice without opening logins."""
    url, node = germany
    link = one(
        [
            link
            for link in nodes(node, "a")
            if link.text().startswith("Announcement of monthly scholarships")
        ],
        "Germany monthly call notice",
    )
    target = urllib.parse.urljoin(url, link.attrs.get("href", ""))
    parsed = urllib.parse.urlsplit(target)
    if parsed.hostname != "cuni.cz" or not parsed.path.endswith(".docx"):
        raise AdapterError("Unexpected Germany application notice", "parse")
    delay = check_robots(target)
    status, _, body = request_bytes(target, interval=delay)
    if status != 200:
        raise AdapterError(
            "Germany application notice unavailable", "fetch", status
        )
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 2_000_000:
                raise AdapterError("Oversized Germany notice", "parse")
            root = ET.fromstring(archive.read(info))
        namespace = (
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        )
        paragraphs = []
        for paragraph in root.iter(namespace + "p"):
            paragraphs.append(
                "".join(
                    item.text or "" for item in paragraph.iter(namespace + "t")
                )
            )
        plain = " ".join(" ".join(paragraphs).split())
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise AdapterError(
            "Invalid Germany application notice", "parse"
        ) from error
    document = Node()
    document.children = [plain]
    return target, document


def german_programmes(pages):
    """Preserve individual university funding routes and actual notice dates."""
    url, root = pages["germany"]
    notice_url, notice = pages["german-call"]
    text = notice.text()
    deadline = re.search(r"8\s*th\s+December\s+2025", text)
    if not deadline or "2026" not in text:
        raise AdapterError("Changed Germany monthly application cycle", "parse")
    sections = nodes(root, "div", "overflowHidden")
    if len(sections) != 12:
        raise AdapterError("Changed Germany funding inventory", "parse")
    definitions = (
        (
            4,
            "2",
            "Saarland",
            "semester",
            "934 EUR",
            None,
            "BA/MA semester scholarship EUR934/month; one or two places.",
        ),
        (
            4,
            "2",
            "Saarland",
            "doctoral",
            "1,200 EUR",
            None,
            (
                "Doctoral mobility for several weeks up to three months in "
                "January–December2027, EUR1200/month; one or two places."
            ),
        ),
        (
            5,
            "3",
            "Düsseldorf",
            "semester",
            "934 EUR",
            None,
            (
                "BA/MA semester scholarship EUR934/month; supervisor "
                "confirmation required, one place."
            ),
        ),
        (
            6,
            "5",
            "Hamburg",
            "semester",
            "1,000 EUR",
            None,
            (
                "BA/MA/doctoral semester scholarship EUR1000/month; winter "
                "five months or summer four, three places."
            ),
        ),
        (
            6,
            "5",
            "Hamburg",
            "monthly",
            "1,300 EUR",
            "2025-12-08",
            (
                "One-month scholarships: doctoral EUR1300/month (four "
                "places), academic staff EUR2000 (six places)."
            ),
        ),
        (
            7,
            "13",
            "Heidelberg",
            "semester",
            "700 EUR",
            None,
            (
                "BA/MA/doctoral semester support EUR700/month for five "
                "months, optional pre-semester intensive German; two "
                "places."
            ),
        ),
        (
            7,
            "13",
            "Heidelberg",
            "monthly",
            "1,300 EUR",
            "2025-12-08",
            "Doctoral one-month scholarship EUR1300, one place.",
        ),
        (
            8,
            "7",
            "Cologne",
            "monthly",
            "1,300 EUR",
            "2025-12-08",
            (
                "One-month doctoral EUR1300 or academic staff EUR2000 "
                "scholarship; one place per cohort."
            ),
        ),
        (
            9,
            "14",
            "Bonn",
            "monthly",
            "1,200 EUR",
            "2025-12-08",
            (
                "One-month doctoral EUR1200 scholarship (two places) or "
                "academic staff EUR2000 (one place)."
            ),
        ),
        (
            10,
            "15",
            "Regensburg",
            "monthly",
            "1,300 EUR",
            "2025-12-08",
            (
                "Doctoral analytical-chemistry scholarship for CU Faculty "
                "of Science EUR1300/month, one place."
            ),
        ),
    )
    result = []
    for index, anchor, university, route, amount, due, facts in definitions:
        summary = (
            "Charles University bilateral funded mobility to "
            + university
            + ", Germany. "
            + facts
            + (
                " Medical and Pharmacy faculties excluded. CV, motivation, "
                "academic acceptance/invitation and study-specific "
                "documents required; apply through the university system. "
            )
            + (
                (
                    "Actual attached2026 notice requires applications by "
                    "December8,2025 despite the hub’s stale 'currently open' "
                    "statement; retained expired."
                )
                if due
                else (
                    "The hub states a February2026 call-publication milestone, "
                    "not an application deadline; deadline unknown."
                )
            )
        )
        record = programme(
            "germany-" + university.lower() + "-" + route,
            university + " " + route + " mobility scholarship",
            url + "#" + anchor,
            sections[index].text(),
            [amount],
            summary,
            hosts=("DE",),
            deadline=due,
        )
        if due:
            record["classification"]["evidence"].append(
                "Application notice: " + notice_url + "; " + deadline.group()
            )
        result.append(record)
    return result


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
                    current,
                    interval=min_interval,
                    robots=original.path == "/robots.txt",
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(
                        current, headers["Location"]
                    )
                    if (
                        original.path == "/robots.txt"
                        and urllib.parse.urlsplit(current).path
                        != "/robots.txt"
                    ):
                        raise AdapterError(
                            "Robots redirect leaves literal path", "access"
                        )
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if (
                    original.path == "/robots.txt"
                    and 400 <= status < 500
                    and status != 429
                ):
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
        elif key == "crawl-delay":
            try:
                group["delay"] = float(value)
                if not 0 <= group["delay"] <= 60:
                    raise ValueError()
            except ValueError:
                raise AdapterError("Unsupported robots crawl delay", "access")
        elif key == "request-rate":
            match = re.fullmatch(r"([1-9]\d*)\s*/\s*([1-9]\d*)", value)
            try:
                if not match:
                    raise ValueError()
                spacing = int(match[2]) / int(match[1]) + 0.01
                if not math.isfinite(spacing) or spacing > 60:
                    raise ValueError()
                group["rate"] = max(group["rate"], spacing)
            except (ValueError, OverflowError):
                raise AdapterError("Unsupported robots request rate", "access")
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
        [12.2]
        + [g["delay"] for policy in policies for g in policy]
        + [g["rate"] for policy in policies for g in policy]
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


def remove_expired_records(records, now=None):
    """Filter a validated refresh; never mutate the prior published snapshot."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=-12))).date()
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


def validate_records(records, *, legacy_snapshot=False, allow_empty=False):
    """Validate fresh output strictly; old summary bounds are read-only."""
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
        length = (
            len(summary)
            if legacy_snapshot
            else len(summary.encode("utf-16-le")) // 2
        )
        if length > (1200 if legacy_snapshot else 600):
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
        "display_name": "Charles University",
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
            else (
                f"Validated refresh retains {len(records)} accessible records; "
                "UJOP excluded because its robots policy was unavailable"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


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
        validate_records(old, legacy_snapshot=True, allow_empty=True)
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


FAMILY = "youthopps-cz-charles-university-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_ID = 378390057
_ARTIFACT_NAME = "charles-pacing-state"
COLLECTION_TIMEOUT = 1260
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 30
_STATE_PATH = _STATE_LOCK = None
_JOURNAL_NEW = False
_COLLECTION_LOCK_FD = None
_RESTORED = _STATE_TRUSTED = False
_PUBLISHER_GATE_FAILED = False
_RUN_END = _PHASE_END = _PHASE_NAME = None
_SCANNED_HISTORY = {}
_SCANNED_JOB_DIGESTS = {}
_ATTEMPT_HEADS = {}
_INVENTORY_ACTIVE = False
_LOCAL_FIRST_ACCESS_CHECKED = False
_RESTORED_LATEST = None
_RESTORED_HISTORY = None
_LOCAL_RESEARCH_MIGRATION = {
    "source": "cz-charles-university",
    "sha256": (
        "74d83d700f0f14fd3ca134025eb3ea91fc006873ffaf535c4195f8de4391bd24"
    ),
    "evidence": (
        "https://github.com/YouthOpps/data-pipeline/"
        "issues/215#issuecomment-6198092119"
    ),
}
_REVIEWED_NATIVE_MIGRATION = {
    "schema": 1,
    "source": "cz-charles-university",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/215#issuecomment-6198680829",
    "state": {
        "schema": 1,
        "family": "youthopps-cz-charles-university-publisher-v1",
        "legacy_hold_history": "unlogged_fields_unknown",
        "research_provenance": {
            "completed": 1792082436.4999557,
            "interval": 12.2,
            "inventory_completed": 1792082436.4999557,
            "known_blocked": False,
            "legacy_429_history": "unknown",
            "legacy_hold_history": "unlogged_fields_unknown",
            "observed_429_since_research": 0,
            "observed_at": 1792081867.0269678,
            "page_gap": 30,
            "public_approval": "https://github.com/YouthOpps/data-pipeline/issues/215#issuecomment-6197155257",
            "rate_limit_until": 0,
            "research_approval_at": 1792082124.2556894,
            "research_routes_limit": 2,
            "scope": "Exactly two fixed research GETs: literal robots then the named content URL; not native or runtime/production clearance",
            "starts": [
                1791499488.0,
                1791499696.0,
                1791500253.0,
                1791500282.0,
                1791501069.0,
                1791501280.0,
                1791528940.0,
                1791554422.0,
                1791586791.0,
                1791613818.0,
                1791637762.0,
                1791656062.0,
                1791670348.0,
                1791700660.0,
                1791725898.0,
                1791758332.0,
                1791787427.0,
                1791821842.0,
                1791853831.0,
                1791902742.0,
                1791933547.0,
                1791956168.0,
                1791968660.0,
                1791981300.0,
                1791999902.0,
                1792016119.0,
                1792041033.0,
                1792050151.0,
                1792061133.0,
                1792062551.0,
                1792079695.0,
                1792082403.68792,
                1792082435.048107,
            ],
            "until": 1792082403.6876302,
        },
        "starts": [
            1791499488.0,
            1791499696.0,
            1791500253.0,
            1791500282.0,
            1791501069.0,
            1791501280.0,
            1791528940.0,
            1791554422.0,
            1791586791.0,
            1791613818.0,
            1791637762.0,
            1791656062.0,
            1791670348.0,
            1791700660.0,
            1791725898.0,
            1791758332.0,
            1791787427.0,
            1791821842.0,
            1791853831.0,
            1791902742.0,
            1791933547.0,
            1791956168.0,
            1791968660.0,
            1791981300.0,
            1791999902.0,
            1792016119.0,
            1792041033.0,
            1792050151.0,
            1792061133.0,
            1792062551.0,
            1792079695.0,
            1792082403.68792,
            1792082435.048107,
            1792085844.8375547,
            1792085860.561184,
            1792085874.3956506,
            1792085887.8921204,
            1792085901.5061314,
            1792085914.6146972,
            1792085946.3754158,
            1792085960.2784717,
            1792085973.3949318,
            1792085986.6824198,
            1792085999.9698634,
            1792086030.763764,
            1792086044.0388134,
            1792086057.014136,
            1792086070.7203474,
            1792086083.948355,
            1792086097.902581,
            1792086111.8056035,
            1792086125.890781,
            1792086140.370376,
            1792086154.8301926,
            1792086171.1603298,
            1792086184.693963,
            1792086198.4803386,
            1792086212.0959446,
            1792086212.4505982,
            1792086225.4937854,
            1792086225.6821012,
            1792086239.5749424,
            1792086239.7594118,
            1792086254.4196303,
            1792086254.6046622,
            1792086267.465988,
            1792086267.6526423,
        ],
        "interval": 12.2,
        "until": 1792086417.0952573,
        "page_gap": 30,
        "completed": 1792086269.196212,
        "inventory_completed": 1792086158.769083,
        "blocked": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": 1792086269.196212,
    },
    "state_sha256": "da145c9d034440865cdb32b68b64537ea4029c04d023246d80b2ecb7291115da",
}
_LEGACY_HEADS = {
    38763419764: "f77eed642c4544bbeed009440c406064a4f5c6ad",
    38721259498: "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
    38720083952: "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
    38686152910: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38684235900: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38644213164: "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
    38608283563: "92235b48ad33b9fa377e0736b689010173d7bfab",
    38564139230: "92235b48ad33b9fa377e0736b689010173d7bfab",
    38534815485: "e47a87e43a672b1b6429749d1cef4851ea0dedee",
    38516940138: "02792bda730b08eeb1eda86a2cd71af80af3cef7",
    38482337248: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38414922668: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38329970820: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38275600138: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38213448036: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38180739213: "10d99f8ef021c092851bf189ff67307865b8760c",
    38143720221: "10d99f8ef021c092851bf189ff67307865b8760c",
    38118339094: "10d99f8ef021c092851bf189ff67307865b8760c",
    38089846162: "10d99f8ef021c092851bf189ff67307865b8760c",
    38071840462: "9b155b179283c8fa08cf56c1a83a54bd19d51814",
    38054415438: "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
    38030957411: "6e5922277de22b96874c23a705a7d838512c6caf",
    38001843384: "2d6491ddf2a37047efc7b996f190f648e647c0c6",
    37940530936: "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
    37895523513: "6f1971d163e39e44d46ffd72bec20795c59df105",
    37857659437: "ee14fb046bc0bdd2eca39905f8d0869e5f13add6",
    37857657577: "ee14fb046bc0bdd2eca39905f8d0869e5f13add6",
    37856616810: "f2950abb0345d6adac107a3b1c3f7b591e575b07",
    37856613168: "f2950abb0345d6adac107a3b1c3f7b591e575b07",
    37855026642: "21417cb46680509ae2fdb3a3fb8d5bfc18af7d38",
    37855022965: "21417cb46680509ae2fdb3a3fb8d5bfc18af7d38",
}
_LEGACY_JOB_DIGESTS = {
    38763419764: (
        "e58216734a5a67a83e6662755ba6d1429c8821797d1bfe28a0089580a69fb632"
    ),
    38721259498: (
        "1573f77fbbab58bfe6938f7a6579e2723ee316f71f3fa1a3b36c356db1760af0"
    ),
    38720083952: (
        "8ec60e0d4dbb2e4a82097934d9d91746481a05e55f6d11a35cf2aeab88b25f7d"
    ),
    38686152910: (
        "d233b727762f911f93aff9489792f831de3ad8a748f7108aa16e5b5916c886fd"
    ),
    38684235900: (
        "0caed5e01ead6a1cac6e82e2f072ca409e0e646976eda3d8db67310909593219"
    ),
    38644213164: (
        "ab029e7d55785f2bebc50196bef95f76b1d0797fc388dc591132f8ea6f566774"
    ),
    38608283563: (
        "221bf40133f7159d98086b63e48457fefd0d0387e65c87948764dc17f6ee05d2"
    ),
    38564139230: (
        "6446de30aa6f5128d3b17c6b48446067f77e6822b62d90e2c3dab13c9a337bdf"
    ),
    38534815485: (
        "e0ce509397e2f14ba85f94590d067cbcc041d7d97362723f0850975e80eab770"
    ),
    38516940138: (
        "2b78baf6cc0a57334302a5cc3b8321fa4f7d84f7afcb02b7f5bce7a25a7f631e"
    ),
    38482337248: (
        "55580f17dbbabb9b3b1650eb030cc1256a35733d14de1417b2d97a08c4e5a965"
    ),
    38414922668: (
        "f446360307b9aaf78606702137be7af08fc325a2e09bcf33c6ffa30f06fe0d5b"
    ),
    38329970820: (
        "dc400111e5b5e624c73570133ae51e320dab5e31f6dfc28e51dad8f70996ba6a"
    ),
    38275600138: (
        "323def359604b144aaba05305ce0b325d4a7a8ad536e78462f1041322f61fc2e"
    ),
    38213448036: (
        "14aaeca764824f805e268799ddde27f5ea8e5cd14248ee4613e8dc38809e610d"
    ),
    38180739213: (
        "6eb9b45a8029ae4cf9a858fabcccb9a84674d735b9fd48554baa09a79314ac3d"
    ),
    38143720221: (
        "d4f18f41c4f24212376386b2fbe8120f557063949fa0a9ce1eebcf2383bb8452"
    ),
    38118339094: (
        "4403c2fd02cefbc26d450de274726c660827de7a9d9b427fbd64f287cb99ea5c"
    ),
    38089846162: (
        "00bb7ec2a844b28f2603fbeab4d73d790845fe7496b0dbe8d5d7de233bb9d4bf"
    ),
    38071840462: (
        "3bcb9eb99e6c9225e9a9e483945ed79ec9c69d5edaaeb0f8868823d6391f1d6d"
    ),
    38054415438: (
        "4305eb22e7cebbff3e140c2d97b6acc9394ff704ec5e57fc88e00f1ff3f51761"
    ),
    38030957411: (
        "b51c323a8c149706cec32f671910fd66646659146fca3abb161cc56cac98e66b"
    ),
    38001843384: (
        "08009422132a3476bb69e87f8f0267466ba296791330d6cc1367c3c6c4873b23"
    ),
    37940530936: (
        "fe47941d275934812c6a4d5d730e63b938a8fe9b82b0f3383638a49a1fa63e1c"
    ),
    37895523513: (
        "c68b24acfaa7700be60304c4ebfead6f6e220b69b0d46440da6fd7a482d5e376"
    ),
    37857659437: (
        "f7a808a19aad50211b0823deff402b8da12dea586d149f8ea989fa25e1e82dac"
    ),
    37857657577: (
        "6ae5251b3e3fabe9c2d1b608c4f38cb8fd577d62fdc33d2eca2f898b833dc08e"
    ),
    37856616810: (
        "831353757438eefdd15a0bd072bbad1ff627e92656d9b9c5e3fd444d237f9bf0"
    ),
    37856613168: (
        "c28fe4b47aedd49969e0d2fa1acea9f4daa8b90096df24d311205b619bab9b9c"
    ),
    37855026642: (
        "ef6d6780693fdc16375b39f6dc408c57364bca0c41e1262b2c0b495e90f921f1"
    ),
    37855022965: (
        "3f1088776cc87b5d64ffeb9366c57e7820f5e9ffdd5610b332f8641c3ecae9f2"
    ),
}
_INVENTORY_URLS = {
    WEBSITE_URL,
    "https://cuni.cz/UKEN-2497.html",
    "https://cuni.cz/UKEN-2528.html",
    "https://cuni.cz/UKEN-2396.html",
    "https://cuni.cz/UKEN-2366.html",
    "https://centrumcarolina.cuni.cz/CAENG-70.html",
    "https://www.mff.cuni.cz/en/admissions/scholarships",
}


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
            r"\bif you see access denied\s*,?\s*(?:please\s+)?contact support\b",
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
    return (
        any(phrase in text for phrase in phrases)
        or bool(
            re.search(
                r"just a moment|attention required|access denied|captcha",
                explicit_title,
            )
        )
    )


def migrate_local_research(value, raw):
    marker = _LOCAL_RESEARCH_MIGRATION
    if (
        not isinstance(marker, dict)
        or marker.get("source") != SOURCE_ID
        or marker.get("sha256") != hashlib.sha256(raw).hexdigest()
        or not re.fullmatch(
            (
                r"https://github\.com/YouthOpps/data-pipeline/"
                r"issues/215#issuecomment-[0-9]+"
            ),
            marker.get("evidence", ""),
        )
    ):
        raise AdapterError(
            "Reviewed native research transition required", "access"
        )
    if (
        value.get("known_blocked", True) is not False
        or value.get("observed_429_since_research") != 0
        or value.get("legacy_hold_history") != "unlogged_fields_unknown"
        or value.get("legacy_429_history") != "unknown"
    ):
        raise AdapterError(
            "Research refusal/backoff needs independent review", "access"
        )
    result = empty_budget(time.time())
    result["research_provenance"] = copy.deepcopy(value)
    for key in (
        "starts",
        "until",
        "completed",
        "inventory_completed",
        "interval",
        "page_gap",
        "rate_limit_until",
    ):
        result[key] = copy.deepcopy(value[key])
    result["interval"] = max(result["interval"], 12.2)
    result["page_gap"] = max(result["page_gap"], 30)
    result["until"] = max(result["until"], time.time() + 60)
    return validate_budget(result)


class PhaseExpired(BaseException):
    """A nonrenewable phase deadline; publisher traffic cannot extend it."""


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def empty_budget(now):
    return {
        "schema": 1,
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
        or state["schema"] != 1
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


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _STATE_INITIALIZING
    if _STATE_PATH:
        return
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    created = False
    try:
        os.mkdir(directory, 0o700)
        created = True
    except FileExistsError:
        pass
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    filename = hashlib.sha256(b"cuni.cz").hexdigest()
    _STATE_PATH = os.path.join(directory, filename)
    _STATE_LOCK = os.path.join(directory, filename)
    _STATE_INITIALIZING = created


def locked_budget():
    """Lock the retained original journal authority for a budget transaction."""
    return JournalLease()


def load_budget():
    try:
        raw = journal_read()
        if not raw and _JOURNAL_NEW and _JOURNAL_BOOTSTRAP_ALLOWED:
            return empty_budget(time.time())
        value = json.loads(raw)
        if isinstance(value, dict) and "schema" not in value:
            value = migrate_local_research(value, raw)
        return validate_budget(value)
    except BaseException:
        journal_failure()
        raise


def write_budget(state):
    """Commit only through the retained, locked original RW descriptor."""
    journal_write(state)


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
        raise AdapterError(
            "Research provenance differs across state", "access"
        )
    result["research_provenance"] = (
        left["research_provenance"] or right["research_provenance"]
    )
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["blocked"] = left["blocked"] or right["blocked"]
    return validate_budget(result)


def remaining():
    check_deadline()
    return min(_RUN_END - EXPORT_TIMEOUT, _PHASE_END) - time.monotonic()


def pace(interval=12.2):
    """Hold the physical journal lock through the response completion."""
    lock = locked_budget()
    try:
        state = load_budget()
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
def request_bytes(
    url,
    *,
    interval=12.2,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=5_000_000,
):
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if type(byte_limit) is not int or not 0 < byte_limit <= 5_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    if robots and urllib.parse.urlsplit(url).path != "/robots.txt":
        raise AdapterError(
            "Robots purpose requires literal robots.txt", "access"
        )
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    publisher = host == "cuni.cz" or host.endswith(".cuni.cz")
    if publisher and (not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        raise AdapterError(
            "Trusted publisher state required before access", "access"
        )
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = pace(interval) if publisher else (None, None)
    try:
        if publisher and os.environ.get("GITHUB_ACTIONS") != "true":
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
            if publisher:
                journal_before_wire()
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        with (
            header_rate_limit_response(response)
            if publisher and response.status == 429
            else response
        ):
            status = response.status
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
            if publisher and status == 429:
                raise AdapterError(
                    "Publisher429; durable adaptive cooldown retained",
                    "access",
                    429,
                )
            return status, response.headers, body
    finally:
        primary = sys.exc_info()[1]
        if lock is not None:
            try:
                if not _JOURNAL_FATAL:
                    state["completed"] = state["observed_at"] = time.time()
                    if _INVENTORY_ACTIVE and not robots:
                        state["inventory_completed"] = state["completed"]
                    write_budget(state)
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
                elif primary is None:
                    raise AdapterError(
                        "Publisher journal gate is sticky", "access"
                    )
            except BaseException:
                journal_failure()
                if primary is None:
                    raise
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
    if _JOURNAL_FATAL or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher journal gate is sticky", "access")
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
        fd = journal_lifecycle_open(path, True)
        try:
            journal_descriptor(fd, _JOURNAL_ACQUIRED_IDS[fd])
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            journal_bind_collection(fd, _JOURNAL_ACQUIRED_IDS[fd])
        except BaseException as error:
            journal_failure()
            with contextlib.suppress(BaseException):
                journal_close_acquired(fd, _JOURNAL_ACQUIRED_IDS.get(fd))
            if isinstance(error, OSError):
                raise AdapterError(
                    "Another publisher collector is active", "access"
                ) from None
            raise
        _COLLECTION_LOCK_FD = fd
    if _STATE_TRUSTED and os.environ.get("GITHUB_ACTIONS") == "true":
        return
    if os.environ.get("GITHUB_ACTIONS") != "true":
        _LOCAL_FIRST_ACCESS_CHECKED = False
    budget_file()
    restore_family_artifact()
    _STATE_TRUSTED = True


def collect():
    """One complete actual native collection with bounded exclusive access."""
    if _RUN_END is None:
        with execution():
            return collect()
    with phase("collection", COLLECTION_TIMEOUT):
        prepare_collection()
        return collect_inventory()


def read_snapshot():
    if _PHASE_END is not None:
        return read_snapshot_body()
    with phase("snapshot", 180):
        return read_snapshot_body()


def publish_files(snapshot, files):
    if _PHASE_END is not None:
        return publish_files_body(snapshot, files)
    with phase("publication", 180):
        return publish_files_body(snapshot, files)


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
        journal_release()


@contextlib.contextmanager
def phase(name, seconds):
    global _PHASE_END, _PHASE_NAME
    if _PHASE_END is not None or _RUN_END is None:
        raise AdapterError("Invalid phase transition", "access")
    _PHASE_NAME = name
    limit = _RUN_END if name == "export" else _RUN_END - EXPORT_TIMEOUT
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/215#issuecomment-[0-9]+",
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


def local_workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    nonce = urllib.parse.urlencode({"state_read": time.time_ns()})
    path += ("&" if "?" in path else "?") + nonce
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache, no-store",
                "Pragma": "no-cache",
            },
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(
            ("Workflow pacing state unavailable"), ("access")
        ) from None


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return local_workflow_api(path)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
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
            "/actions/workflows/fetch-cz-charles-university.yml/runs"
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
            ordered = sorted(candidates)
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
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
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
        status, _, body = request_bytes(destination, byte_limit=32768)
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


def validate_owned_output(info):
    """Refuse unsafe output before reading or modifying it."""
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe owned artifact output", "access")


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
    """Write readiness using acquisition-bound output descriptors."""
    journal_output_signal(value)


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
    path = os.environ.get("CHARLES_PACING_ARTIFACT_PATH")
    if not path:
        return
    with owned_artifact_directory(path) as (root, directory):
        invalidate_owned_artifact(root, directory)


def export_family_artifact():
    """Export the unchanged owned envelope under retained journal authority."""
    path = os.environ.get("CHARLES_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED or _JOURNAL_FATAL:
        journal_invalidate_export()
        return

    def payload():
        run_id, attempt = current_run_identity()
        with locked_budget():
            state = load_budget()
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
        return envelope

    journal_export_envelope(
        path,
        payload,
        atomic=True,
        signal=True,
    )


def restore_family_artifact():
    """Require exact history for the reviewed legacy state handoff."""
    global _JOURNAL_BOOTSTRAP_ALLOWED
    global _RESTORED, _STATE_TRUSTED, _RESTORED_LATEST, _RESTORED_HISTORY
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
            latest != (38763419764, 1, SOURCE_ID)
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
                        r"issues/215#issuecomment-[0-9]+"
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
    _JOURNAL_BOOTSTRAP_ALLOWED = (
        _STATE_INITIALIZING
        and not _JOURNAL_SAVE_ATTEMPTED
        and os.environ.get("GITHUB_ACTIONS") == "true"
    )
    with locked_budget():
        local = load_budget()
        combined = merge_budget(local, incoming)
        combined["until"] = max(combined["until"], time.time() + 60)
        write_budget(combined)
    if os.environ.get("GITHUB_ACTIONS") != "true":
        _RESTORED_LATEST = latest
        _RESTORED_HISTORY = (
            copy.deepcopy(_SCANNED_HISTORY),
            copy.deepcopy(_SCANNED_JOB_DIGESTS),
            copy.deepcopy(_ATTEMPT_HEADS),
        )
    _RESTORED = _STATE_TRUSTED = True


_STATE_INITIALIZING = False
_JOURNAL_FD = None
_JOURNAL_ID = None
_JOURNAL_VERSION = None
_JOURNAL_PARENT_FD = None
_JOURNAL_PARENT_ID = None
_JOURNAL_PATH = None
_JOURNAL_LOCK_FD = None
_JOURNAL_LOCK_ID = None
_JOURNAL_COLLECTION_ID = None
_JOURNAL_DEPTH = 0
_JOURNAL_FATAL = False
_JOURNAL_DIRTY = False
_JOURNAL_SAVE_ATTEMPTED = False
_JOURNAL_BOOTSTRAP_ALLOWED = False
_JOURNAL_NEW = False
_JOURNAL_FAILED_STATE = None


def journal_version(info):
    """Identify observable file generations without adopting a new inode."""
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def journal_descriptor(descriptor, identity, directory=False, unlinked=False):
    info = os.fstat(descriptor)
    if (
        (info.st_dev, info.st_ino) != identity
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or directory
        and not stat.S_ISDIR(info.st_mode)
        or not directory
        and (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink not in ((0, 1) if unlinked else (1,))
        )
    ):
        raise AdapterError("Original publisher descriptor changed", "access")
    return info


def journal_parent():
    journal_descriptor(_JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, True)
    named = os.stat(os.path.dirname(_JOURNAL_PATH), follow_symlinks=False)
    if (named.st_dev, named.st_ino) != _JOURNAL_PARENT_ID:
        raise AdapterError("Original publisher directory changed", "access")


def journal_poison():
    """Invalidate the held original only; preserve unrelated generations."""
    if _JOURNAL_FD is None:
        return
    journal_parent()
    info = journal_descriptor(_JOURNAL_FD, _JOURNAL_ID, unlinked=True)
    if not _JOURNAL_DIRTY and journal_version(info) != _JOURNAL_VERSION:
        raise AdapterError("Stronger publisher generation preserved", "access")
    os.ftruncate(_JOURNAL_FD, 0)
    os.fsync(_JOURNAL_FD)


def journal_failure(candidate=None):
    """Latch every journal authority or persistence failure across reentry."""
    global _JOURNAL_FATAL, _PUBLISHER_GATE_FAILED, _STATE_TRUSTED
    global _JOURNAL_FAILED_STATE
    _JOURNAL_FATAL = _PUBLISHER_GATE_FAILED = True
    _STATE_TRUSTED = False
    if candidate is not None:
        with contextlib.suppress(BaseException):
            _JOURNAL_FAILED_STATE = copy.deepcopy(candidate)
    with contextlib.suppress(BaseException):
        journal_poison()


def journal_close_descriptor(descriptor, identity, directory=False):
    """Release our acquisition, preserving a primary error and foreign FD."""
    primary = sys.exc_info()[1]
    try:
        journal_descriptor(descriptor, identity, directory, unlinked=True)
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def journal_verify(generation=True):
    try:
        if _JOURNAL_FATAL:
            raise AdapterError("Publisher journal gate is sticky", "access")
        journal_parent()
        info = journal_descriptor(_JOURNAL_FD, _JOURNAL_ID)
        named = os.stat(
            os.path.basename(_JOURNAL_PATH),
            dir_fd=_JOURNAL_PARENT_FD,
            follow_symlinks=False,
        )
        if (named.st_dev, named.st_ino) != _JOURNAL_ID or (
            generation and journal_version(info) != _JOURNAL_VERSION
        ):
            raise AdapterError("Publisher journal generation changed", "access")
        if _JOURNAL_LOCK_FD is not None:
            journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
            if _JOURNAL_LOCK_ID != _JOURNAL_ID:
                lock = os.stat(
                    "state.lock",
                    dir_fd=_JOURNAL_PARENT_FD,
                    follow_symlinks=False,
                )
                if (lock.st_dev, lock.st_ino) != _JOURNAL_LOCK_ID:
                    raise AdapterError("Publisher state lock changed", "access")
        return info
    except BaseException:
        journal_failure()
        raise


def journal_acquire():
    """Retain only a fully verified original RW inode and lock acquisition."""
    global _JOURNAL_FD, _JOURNAL_ID, _JOURNAL_VERSION, _JOURNAL_PATH
    global _JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, _JOURNAL_NEW
    global _JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID
    if _JOURNAL_FATAL:
        raise AdapterError("Publisher journal gate is sticky", "access")
    if _JOURNAL_FD is not None:
        journal_verify()
        return
    path = journal_file_path()
    _JOURNAL_PATH = path
    parent, parent_id = journal_open_directory(os.path.dirname(path))
    _JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID = parent, parent_id
    try:
        expected = None
        try:
            descriptor, identity = journal_open_original(
                parent, os.path.basename(path), os.O_RDWR, expected
            )
        except FileNotFoundError:
            if not (
                _STATE_INITIALIZING
                and _JOURNAL_BOOTSTRAP_ALLOWED
                and not _JOURNAL_SAVE_ATTEMPTED
            ):
                raise AdapterError(
                    "Mature publisher state is missing", "access"
                )
            descriptor, identity = journal_open_original(
                parent,
                os.path.basename(path),
                os.O_RDWR | os.O_CREAT | os.O_EXCL,
            )
            _JOURNAL_NEW = True
        info = journal_descriptor(descriptor, identity)
        _JOURNAL_FD, _JOURNAL_ID = descriptor, identity
        _JOURNAL_VERSION = journal_version(info)
        journal_verify()
        _JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID = descriptor, identity
        journal_verify()
    except BaseException:
        journal_failure()
        raise


@contextlib.contextmanager
def journal_locked():
    global _JOURNAL_DEPTH
    journal_acquire()
    if _JOURNAL_DEPTH == 0:
        try:
            fcntl.flock(_JOURNAL_LOCK_FD, fcntl.LOCK_EX)
        except BaseException:
            journal_failure()
            raise
    _JOURNAL_DEPTH += 1
    try:
        journal_verify()
        yield
    finally:
        primary = sys.exc_info()[1]
        _JOURNAL_DEPTH -= 1
        if _JOURNAL_DEPTH == 0:
            try:
                journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
                fcntl.flock(_JOURNAL_LOCK_FD, fcntl.LOCK_UN)
                journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
            except BaseException:
                journal_failure()
                if primary is None:
                    raise


def journal_read():
    journal_acquire()
    before = journal_verify()
    try:
        raw = os.pread(_JOURNAL_FD, 16385, 0)
        after = journal_verify()
        if (
            journal_version(before) != journal_version(after)
            or len(raw) > 16384
        ):
            raise AdapterError(
                "Publisher journal changed during read", "access"
            )
        return raw
    except BaseException:
        journal_failure()
        raise


def journal_write(state):
    global _JOURNAL_VERSION, _JOURNAL_DIRTY, _JOURNAL_SAVE_ATTEMPTED
    global _JOURNAL_NEW, _STATE_INITIALIZING
    try:
        validate_budget(state)
        raw = json.dumps(state, separators=(",", ":")).encode()
        if not raw or len(raw) > 16384:
            raise AdapterError("Oversized publisher journal", "access")
        journal_verify()
        _JOURNAL_SAVE_ATTEMPTED = True
        _JOURNAL_DIRTY = True
        os.ftruncate(_JOURNAL_FD, 0)
        os.fsync(_JOURNAL_FD)
        offset = 0
        while offset < len(raw):
            journal_verify(False)
            written = os.pwrite(_JOURNAL_FD, raw[offset:], offset)
            journal_verify(False)
            if written <= 0:
                raise AdapterError(
                    "Incomplete publisher journal write", "access"
                )
            offset += written
        journal_verify(False)
        os.ftruncate(_JOURNAL_FD, len(raw))
        journal_verify(False)
        os.fsync(_JOURNAL_FD)
        before = journal_verify(False)
        actual = os.pread(_JOURNAL_FD, 16385, 0)
        after = journal_verify(False)
        if actual != raw or journal_version(before) != journal_version(after):
            _JOURNAL_DIRTY = False
            raise AdapterError("Publisher journal readback changed", "access")
        _JOURNAL_VERSION = journal_version(after)
        _JOURNAL_DIRTY = False
        _JOURNAL_NEW = False
        _STATE_INITIALIZING = False
    except BaseException:
        _JOURNAL_SAVE_ATTEMPTED = True
        journal_failure(state)
        raise


def journal_bind_collection(descriptor, expected=None):
    global _JOURNAL_COLLECTION_ID
    try:
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        if expected is not None and identity != expected:
            raise AdapterError(
                "Lifecycle FD changed after acquisition", "access"
            )
        _JOURNAL_COLLECTION_ID = expected if expected is not None else identity
        journal_descriptor(descriptor, _JOURNAL_COLLECTION_ID)
    except BaseException:
        journal_failure()
        raise


def journal_verify_collection():
    journal_descriptor(_COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID)

    named = os.stat(
        os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock"),
        follow_symlinks=False,
    )
    if (named.st_dev, named.st_ino) != _JOURNAL_COLLECTION_ID:
        raise AdapterError("Original lifecycle lock changed", "access")


def journal_before_wire():
    try:
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError("Publisher completion gate is sticky", "access")
        journal_verify()
        journal_verify_collection()
    except BaseException:
        journal_failure()
        raise


def journal_release():
    """Attempt independent releases; never clear the sticky failure gate."""
    global _JOURNAL_FD, _JOURNAL_LOCK_FD, _JOURNAL_PARENT_FD
    global _COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID
    global _JOURNAL_ID, _JOURNAL_LOCK_ID, _JOURNAL_PARENT_ID
    primary = sys.exc_info()[1]
    errors = []
    releases = [
        (_COLLECTION_LOCK_FD, _JOURNAL_COLLECTION_ID, False),
        (_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID, False),
        (_JOURNAL_FD, _JOURNAL_ID, False),
        (_JOURNAL_PARENT_FD, _JOURNAL_PARENT_ID, True),
    ]
    seen = set()
    for descriptor, identity, directory in releases:
        if descriptor is None or descriptor in seen:
            continue
        seen.add(descriptor)
        try:
            journal_close_descriptor(descriptor, identity, directory)
        except BaseException as error:
            errors.append(error)
    _COLLECTION_LOCK_FD = _JOURNAL_COLLECTION_ID = None
    _JOURNAL_FD = _JOURNAL_LOCK_FD = _JOURNAL_PARENT_FD = None
    _JOURNAL_ID = _JOURNAL_LOCK_ID = _JOURNAL_PARENT_ID = None
    if _JOURNAL_FATAL:
        with contextlib.suppress(BaseException):
            journal_invalidate_export()
    if errors and primary is None:
        raise errors[0]


class JournalLease:
    """Preserve the existing immediate-lock and explicit-close interface."""

    def __init__(self):
        self.context = journal_locked()
        self.context.__enter__()
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *failure):
        if not self.closed:
            self.closed = True
            return self.context.__exit__(*failure)

    def close(self):
        self.__exit__(*sys.exc_info())

    def fileno(self):
        journal_descriptor(_JOURNAL_LOCK_FD, _JOURNAL_LOCK_ID)
        return _JOURNAL_LOCK_FD


def journal_file_path():
    budget_file()
    return _STATE_PATH


def journal_invalidate_export():
    path = os.environ.get("CHARLES_PACING_ARTIFACT_PATH")
    if path:
        journal_invalidate_owned_output(path)


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
            journal_close_acquired(parent, identity)
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
        if len(raw) > 16384:
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
        actual = os.pread(guard, 16385, 0)
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
        failed = primary is not None or _JOURNAL_FATAL
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
        if failed or errors or _JOURNAL_FATAL:
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


def journal_close_acquired(descriptor, expected):
    """Rollback only an actual named acquisition, never an unknown FD."""
    if expected is None:
        return
    primary = sys.exc_info()[1]
    try:
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != expected:
            raise AdapterError("Acquired descriptor was recycled", "access")
        os.close(descriptor)
    except BaseException:
        journal_failure()
        if primary is None:
            raise


def journal_open_directory(path):
    before = os.stat(path, follow_symlinks=False)
    expected = (before.st_dev, before.st_ino)
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    try:
        info = journal_descriptor(descriptor, expected, True)
        current = os.stat(path, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != expected:
            raise AdapterError(
                "Publisher parent changed at acquisition", "access"
            )
        return descriptor, expected
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            journal_close_acquired(descriptor, expected)
        raise


def journal_open_original(parent, name, flags, expected=None):
    """Validate both named generations before publishing an FD as authority."""
    try:
        before = os.stat(name, dir_fd=parent, follow_symlinks=False)
        previous = (before.st_dev, before.st_ino)
    except FileNotFoundError:
        previous = None
        if not flags & os.O_CREAT:
            raise
        flags |= os.O_EXCL
    if expected is not None and previous != expected:
        raise AdapterError("Expected publisher inode changed", "access")
    descriptor = os.open(
        name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent
    )
    safe_close = previous
    try:
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        identity = (named.st_dev, named.st_ino)
        if previous is not None and identity != previous:
            raise AdapterError("Publisher name changed during open", "access")
        journal_descriptor(descriptor, identity)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != identity:
            raise AdapterError(
                "Publisher file changed at acquisition", "access"
            )
        safe_close = identity
        _JOURNAL_ACQUIRED_IDS[descriptor] = identity
        return descriptor, identity
    except BaseException:
        journal_failure()
        with contextlib.suppress(BaseException):
            journal_close_acquired(descriptor, safe_close)
        raise


_JOURNAL_ACQUIRED_IDS = {}


def journal_lifecycle_open(path, create):
    """Capture the actual collection acquisition before flock or cleanup."""
    directory = os.path.dirname(path)
    named = os.stat(directory, follow_symlinks=False)
    parent_id = (named.st_dev, named.st_ino)
    parent = os.open(
        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    descriptor = identity = None
    try:
        held = os.fstat(parent)
        current = os.stat(directory, follow_symlinks=False)
        if (held.st_dev, held.st_ino) != parent_id or (
            current.st_dev,
            current.st_ino,
        ) != parent_id:
            raise AdapterError("Lifecycle parent changed during open", "access")
        flags = os.O_RDWR | (os.O_CREAT if create else 0)
        descriptor, identity = journal_open_original(
            parent, os.path.basename(path), flags
        )
        return descriptor
    except BaseException:
        journal_failure()
        if descriptor is not None:
            with contextlib.suppress(BaseException):
                journal_close_acquired(descriptor, identity)
        raise
    finally:
        primary = sys.exc_info()[1]
        try:
            journal_close_acquired(parent, parent_id)
        except BaseException:
            if descriptor is not None:
                with contextlib.suppress(BaseException):
                    journal_close_acquired(descriptor, identity)
            if primary is None:
                raise


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
