"""Collect official Harno scholarship and academic mobility opportunities."""

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

SOURCE_ID = "ee-harno"
SOURCE_URL = (
    "https://www.harno.ee/en/scholarships-and-grants/"
    "scholarships-studying-and-working-estonia/"
    "scholarships-international"
)
WEBSITE_URL = SOURCE_URL
LANGUAGE = "en"
PUBLISHER_COUNTRY = "EE"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Estonian Education and Youth Board (Harno)"


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
            class_name is None
            or class_name in n.attrs.get("class", "").split()
        )
    ]


def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]




MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "january januar jaanuar jaanuari",
            "february februar veebruar veebruari veebruariks",
            "march marts märts märtsi märtsiks",
            "april aprill aprilli",
            "may maj mai maiks",
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


def dated_values(text, default_year=None):
    """Resolve years only within one explicit application statement."""
    matches = list(DATE_PATTERN.finditer(text))
    years = {
        match["year"] or match["n_year"]
        for match in matches
        if match["year"] or match["n_year"]
    }
    if not years and default_year:
        years = {default_year}
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
                for link in nodes(node, "a"):
                    href = link.attrs.get("href", "")
                    if re.search(r"\.pdf(?:$|\?)", href, re.I):
                        result.append(("pdf", href))
                return
        if node.tag == "a" and re.search(
            r"\.pdf(?:$|\?)", node.attrs.get("href", ""), re.I
        ):
            result.append(("pdf", node.attrs["href"]))
        for child in node.children:
            if isinstance(child, Node):
                visit(child)
            elif child.strip():
                result.append(("text", " ".join(child.split())))

    visit(root)
    return result


def check_access(root):
    title = " ".join(n.text() for n in nodes(root, "title"))
    if re.search(
        r"access denied|just a moment|attention required|checking your browser",
        title,
        re.I,
    ):
        raise AdapterError("Publisher access challenge", "access")
    text = root.text()
    if re.search(
        r"(?:scraping|automated (?:access|collection)|text and data mining)"
        r".{0,100}(?:prohibited|not permitted|forbidden)|"
        r"(?:prohibit|forbid|do not permit).{0,100}(?:scraping|automated access)",
        text,
        re.I,
    ):
        raise AdapterError(
            "Publisher restricts automated collection", "access"
        )


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
            if tag in ("pdf", "shared-pdf"):
                continue
            if tag.startswith("h"):
                heading = text
                continue
            text = re.sub(
                r"[\w.+-]+@[\w.-]+|\[email protected\]",
                "[publisher contact]",
                text,
            )
            for clause in re.split(r"(?<=[.!?])\s+(?=[A-ZÕÄÖÜŠŽ])|;\s*", text):
                if not re.search(
                    r"eligib|applican|appl(?:y|ied|ication)|deadline|"
                    r"funding|EUR|DKK|euros?|must|required|exclud|citizen|residen|"
                    r"fee|tuition|accommod|reimburse|stipend|taotl|tähtaeg|euro|"
                    r"award|cover|payment|tax|duration|minimum|maximum|benefit|"
                    r"degree|bachelor|master|doctoral|\bPhD\b|enrol|student|"
                    r"nationality|restric|condition|months?|weeks?|travel|"
                    r"nõu|tingimus|saavad|võib|peab|vähemalt|maksimaalselt|"
                    r"tasuta|kaetud|sõidu|majutus|päeva|kuud|aastat|"
                    r"receipt|proof|kuludokumen|dokumend|esitada|maksm|"
                    r"õpp|koda(?:nik|kond)|elamis|hüvit|ansøg|legat|medlem|"
                    r"kroner|kr\.|gratis|kurs|mentor|skal|støtte|udgift|"
                    r"book|Teams|Microsoft|Google|Adobe|Office|Windows|LaTeX|Stata|Excel",
                    clause,
                    re.I,
                ):
                    continue
                if len(clause) > 900:
                    evidence.append(
                        "Additional long-form conditions require review at "
                        + url
                    )
                    continue
                evidence.append((heading + ": " if heading else "") + clause)
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
    for text in evidence:
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


FUNDING_WORDS = re.compile(
    r"scholarship|grant|stipend|toetus|rahastus|nordplus|erasmus", re.I
)
DETAIL_WORDS = re.compile(
    r"taotlemine|tingimused|suurus|maksmine|dokumen|tulemus|valik|aruandlus|tagasi|kohustus|additional|lisainfo|applying|application|payment|amount|general|üldinfo|kogemus|kokkutulek|veebist",
    re.I,
)
LEGACY_IDS = {
    "summerandwinter": ["9c335c7274ade4d27fe84718"],
    "researchersandacademic": ["b83cfbb0dfd31c22d7214ed5"],
    "degreeandexchange": ["bffcdd50b7368c8156543249"],
    "estophilus": ["edfbff2aa9918bf33620fc1a"],
    "language": ["864e5317a5867bd78144cff4"],
    "agur": ["faa8ba066a793bec8fa3c705"],
    "special": ["60886a73130f74e0daa44502"],
    "valisopingute_stipendium": ["6dea9b9354b852101e7c87f9"],
    "opirande_stipendium": [
        "a3910f205b2a01d3aa8ff239",
        "c5ed1ee0fa27835265cbd090",
        "ac6868c762e4aab548eaf0f0",
    ],
    "kulalisloeng": ["55cc19a79b17e25063064a41"],
    "eesti-teemaline": ["c9f1aeb357052c34cc96d51e"],
}
# ISO 3166-1 names and publisher aliases, not an eligibility snapshot.
COUNTRY_NAMES = {
    "Afghanistan": "AF",
    "Albania": "AL",
    "Algeria": "DZ",
    "American Samoa": "AS",
    "Andorra": "AD",
    "Angola": "AO",
    "Anguilla": "AI",
    "Antarctica": "AQ",
    "Antigua and Barbuda": "AG",
    "Arab Republic of Egypt": "EG",
    "Argentina": "AR",
    "Argentine Republic": "AR",
    "Armenia": "AM",
    "Aruba": "AW",
    "Australia": "AU",
    "Austria": "AT",
    "Azerbaijan": "AZ",
    "Bahamas": "BS",
    "Bahrain": "BH",
    "Bangladesh": "BD",
    "Barbados": "BB",
    "Belarus": "BY",
    "Belgium": "BE",
    "Belize": "BZ",
    "Benin": "BJ",
    "Bermuda": "BM",
    "Bhutan": "BT",
    "Bolivarian Republic of Venezuela": "VE",
    "Bolivia": "BO",
    "Bolivia, Plurinational State of": "BO",
    "Bonaire, Sint Eustatius and Saba": "BQ",
    "Bosnia and Herzegovina": "BA",
    "Botswana": "BW",
    "Bouvet Island": "BV",
    "Brazil": "BR",
    "British Indian Ocean Territory": "IO",
    "British Virgin Islands": "VG",
    "Brunei Darussalam": "BN",
    "Bulgaria": "BG",
    "Burkina Faso": "BF",
    "Burundi": "BI",
    "Cabo Verde": "CV",
    "Cambodia": "KH",
    "Cameroon": "CM",
    "Canada": "CA",
    "Cayman Islands": "KY",
    "Central African Republic": "CF",
    "Chad": "TD",
    "Chile": "CL",
    "China": "CN",
    "Christmas Island": "CX",
    "Cocos (Keeling) Islands": "CC",
    "Colombia": "CO",
    "Commonwealth of Dominica": "DM",
    "Commonwealth of the Bahamas": "BS",
    "Commonwealth of the Northern Mariana Islands": "MP",
    "Comoros": "KM",
    "Congo": "CG",
    "Congo, The Democratic Republic of the": "CD",
    "Cook Islands": "CK",
    "Costa Rica": "CR",
    "Croatia": "HR",
    "Cuba": "CU",
    "Curaçao": "CW",
    "Cyprus": "CY",
    "Czech Republic": "CZ",
    "Czechia": "CZ",
    "Côte d'Ivoire": "CI",
    "Democratic People's Republic of Korea": "KP",
    "Democratic Republic of Sao Tome and Principe": "ST",
    "Democratic Republic of Timor-Leste": "TL",
    "Democratic Socialist Republic of Sri Lanka": "LK",
    "Denmark": "DK",
    "Djibouti": "DJ",
    "Dominica": "DM",
    "Dominican Republic": "DO",
    "Eastern Republic of Uruguay": "UY",
    "Ecuador": "EC",
    "Egypt": "EG",
    "El Salvador": "SV",
    "Equatorial Guinea": "GQ",
    "Eritrea": "ER",
    "Estonia": "EE",
    "Eswatini": "SZ",
    "Ethiopia": "ET",
    "Falkland Islands (Malvinas)": "FK",
    "Faroe Islands": "FO",
    "Federal Democratic Republic of Ethiopia": "ET",
    "Federal Democratic Republic of Nepal": "NP",
    "Federal Republic of Germany": "DE",
    "Federal Republic of Nigeria": "NG",
    "Federal Republic of Somalia": "SO",
    "Federated States of Micronesia": "FM",
    "Federative Republic of Brazil": "BR",
    "Fiji": "FJ",
    "Finland": "FI",
    "France": "FR",
    "French Guiana": "GF",
    "French Polynesia": "PF",
    "French Republic": "FR",
    "French Southern Territories": "TF",
    "Gabon": "GA",
    "Gabonese Republic": "GA",
    "Gambia": "GM",
    "Georgia": "GE",
    "Germany": "DE",
    "Ghana": "GH",
    "Gibraltar": "GI",
    "Grand Duchy of Luxembourg": "LU",
    "Great Britain": "GB",
    "Greece": "GR",
    "Greenland": "GL",
    "Grenada": "GD",
    "Guadeloupe": "GP",
    "Guam": "GU",
    "Guatemala": "GT",
    "Guernsey": "GG",
    "Guinea": "GN",
    "Guinea-Bissau": "GW",
    "Guyana": "GY",
    "Haiti": "HT",
    "Hashemite Kingdom of Jordan": "JO",
    "Heard Island and McDonald Islands": "HM",
    "Hellenic Republic": "GR",
    "Holy See (Vatican City State)": "VA",
    "Honduras": "HN",
    "Hong Kong": "HK",
    "Hong Kong Special Administrative Region of China": "HK",
    "Hungary": "HU",
    "Iceland": "IS",
    "Independent State of Papua New Guinea": "PG",
    "Independent State of Samoa": "WS",
    "India": "IN",
    "Indonesia": "ID",
    "Iran": "IR",
    "Iran, Islamic Republic of": "IR",
    "Iraq": "IQ",
    "Ireland": "IE",
    "Islamic Republic of Afghanistan": "AF",
    "Islamic Republic of Iran": "IR",
    "Islamic Republic of Mauritania": "MR",
    "Islamic Republic of Pakistan": "PK",
    "Isle of Man": "IM",
    "Israel": "IL",
    "Italian Republic": "IT",
    "Italy": "IT",
    "Jamaica": "JM",
    "Japan": "JP",
    "Jersey": "JE",
    "Jordan": "JO",
    "Kazakhstan": "KZ",
    "Kenya": "KE",
    "Kingdom of Bahrain": "BH",
    "Kingdom of Belgium": "BE",
    "Kingdom of Bhutan": "BT",
    "Kingdom of Cambodia": "KH",
    "Kingdom of Denmark": "DK",
    "Kingdom of Eswatini": "SZ",
    "Kingdom of Lesotho": "LS",
    "Kingdom of Morocco": "MA",
    "Kingdom of Norway": "NO",
    "Kingdom of Saudi Arabia": "SA",
    "Kingdom of Spain": "ES",
    "Kingdom of Sweden": "SE",
    "Kingdom of Thailand": "TH",
    "Kingdom of Tonga": "TO",
    "Kingdom of the Netherlands": "NL",
    "Kiribati": "KI",
    "Korea, Democratic People's Republic of": "KP",
    "Korea, Republic of": "KR",
    "Kuwait": "KW",
    "Kyrgyz Republic": "KG",
    "Kyrgyzstan": "KG",
    "Lao People's Democratic Republic": "LA",
    "Laos": "LA",
    "Latvia": "LV",
    "Lebanese Republic": "LB",
    "Lebanon": "LB",
    "Lesotho": "LS",
    "Liberia": "LR",
    "Libya": "LY",
    "Liechtenstein": "LI",
    "Lithuania": "LT",
    "Luxembourg": "LU",
    "Macao": "MO",
    "Macao Special Administrative Region of China": "MO",
    "Madagascar": "MG",
    "Malawi": "MW",
    "Malaysia": "MY",
    "Maldives": "MV",
    "Mali": "ML",
    "Malta": "MT",
    "Marshall Islands": "MH",
    "Martinique": "MQ",
    "Mauritania": "MR",
    "Mauritius": "MU",
    "Mayotte": "YT",
    "Mexico": "MX",
    "Micronesia, Federated States of": "FM",
    "Moldova": "MD",
    "Moldova, Republic of": "MD",
    "Monaco": "MC",
    "Mongolia": "MN",
    "Montenegro": "ME",
    "Montserrat": "MS",
    "Morocco": "MA",
    "Mozambique": "MZ",
    "Myanmar": "MM",
    "Namibia": "NA",
    "Nauru": "NR",
    "Nepal": "NP",
    "Netherlands": "NL",
    "New Caledonia": "NC",
    "New Zealand": "NZ",
    "Nicaragua": "NI",
    "Niger": "NE",
    "Nigeria": "NG",
    "Niue": "NU",
    "Norfolk Island": "NF",
    "North Korea": "KP",
    "North Macedonia": "MK",
    "Northern Mariana Islands": "MP",
    "Norway": "NO",
    "Oman": "OM",
    "Pakistan": "PK",
    "Palau": "PW",
    "Palestine, State of": "PS",
    "Panama": "PA",
    "Papua New Guinea": "PG",
    "Paraguay": "PY",
    "People's Democratic Republic of Algeria": "DZ",
    "People's Republic of Bangladesh": "BD",
    "People's Republic of China": "CN",
    "Peru": "PE",
    "Philippines": "PH",
    "Pitcairn": "PN",
    "Plurinational State of Bolivia": "BO",
    "Poland": "PL",
    "Portugal": "PT",
    "Portuguese Republic": "PT",
    "Principality of Andorra": "AD",
    "Principality of Liechtenstein": "LI",
    "Principality of Monaco": "MC",
    "Puerto Rico": "PR",
    "Qatar": "QA",
    "Republic of Albania": "AL",
    "Republic of Angola": "AO",
    "Republic of Armenia": "AM",
    "Republic of Austria": "AT",
    "Republic of Azerbaijan": "AZ",
    "Republic of Belarus": "BY",
    "Republic of Benin": "BJ",
    "Republic of Bosnia and Herzegovina": "BA",
    "Republic of Botswana": "BW",
    "Republic of Bulgaria": "BG",
    "Republic of Burundi": "BI",
    "Republic of Cabo Verde": "CV",
    "Republic of Cameroon": "CM",
    "Republic of Chad": "TD",
    "Republic of Chile": "CL",
    "Republic of Colombia": "CO",
    "Republic of Costa Rica": "CR",
    "Republic of Croatia": "HR",
    "Republic of Cuba": "CU",
    "Republic of Cyprus": "CY",
    "Republic of Côte d'Ivoire": "CI",
    "Republic of Djibouti": "DJ",
    "Republic of Ecuador": "EC",
    "Republic of El Salvador": "SV",
    "Republic of Equatorial Guinea": "GQ",
    "Republic of Estonia": "EE",
    "Republic of Fiji": "FJ",
    "Republic of Finland": "FI",
    "Republic of Ghana": "GH",
    "Republic of Guatemala": "GT",
    "Republic of Guinea": "GN",
    "Republic of Guinea-Bissau": "GW",
    "Republic of Guyana": "GY",
    "Republic of Haiti": "HT",
    "Republic of Honduras": "HN",
    "Republic of Iceland": "IS",
    "Republic of India": "IN",
    "Republic of Indonesia": "ID",
    "Republic of Iraq": "IQ",
    "Republic of Kazakhstan": "KZ",
    "Republic of Kenya": "KE",
    "Republic of Kiribati": "KI",
    "Republic of Korea": "KR",
    "Republic of Latvia": "LV",
    "Republic of Liberia": "LR",
    "Republic of Lithuania": "LT",
    "Republic of Madagascar": "MG",
    "Republic of Malawi": "MW",
    "Republic of Maldives": "MV",
    "Republic of Mali": "ML",
    "Republic of Malta": "MT",
    "Republic of Mauritius": "MU",
    "Republic of Moldova": "MD",
    "Republic of Mozambique": "MZ",
    "Republic of Myanmar": "MM",
    "Republic of Namibia": "NA",
    "Republic of Nauru": "NR",
    "Republic of Nicaragua": "NI",
    "Republic of North Macedonia": "MK",
    "Republic of Palau": "PW",
    "Republic of Panama": "PA",
    "Republic of Paraguay": "PY",
    "Republic of Peru": "PE",
    "Republic of Poland": "PL",
    "Republic of San Marino": "SM",
    "Republic of Senegal": "SN",
    "Republic of Serbia": "RS",
    "Republic of Seychelles": "SC",
    "Republic of Sierra Leone": "SL",
    "Republic of Singapore": "SG",
    "Republic of Slovenia": "SI",
    "Republic of South Africa": "ZA",
    "Republic of South Sudan": "SS",
    "Republic of Suriname": "SR",
    "Republic of Tajikistan": "TJ",
    "Republic of Trinidad and Tobago": "TT",
    "Republic of Tunisia": "TN",
    "Republic of Türkiye": "TR",
    "Republic of Uganda": "UG",
    "Republic of Uzbekistan": "UZ",
    "Republic of Vanuatu": "VU",
    "Republic of Yemen": "YE",
    "Republic of Zambia": "ZM",
    "Republic of Zimbabwe": "ZW",
    "Republic of the Congo": "CG",
    "Republic of the Gambia": "GM",
    "Republic of the Marshall Islands": "MH",
    "Republic of the Niger": "NE",
    "Republic of the Philippines": "PH",
    "Republic of the Sudan": "SD",
    "Romania": "RO",
    "Russian Federation": "RU",
    "Rwanda": "RW",
    "Rwandese Republic": "RW",
    "Réunion": "RE",
    "Saint Barthélemy": "BL",
    "Saint Helena, Ascension and Tristan da Cunha": "SH",
    "Saint Kitts and Nevis": "KN",
    "Saint Lucia": "LC",
    "Saint Martin (French part)": "MF",
    "Saint Pierre and Miquelon": "PM",
    "Saint Vincent and the Grenadines": "VC",
    "Samoa": "WS",
    "San Marino": "SM",
    "Sao Tome and Principe": "ST",
    "Saudi Arabia": "SA",
    "Senegal": "SN",
    "Serbia": "RS",
    "Seychelles": "SC",
    "Sierra Leone": "SL",
    "Singapore": "SG",
    "Sint Maarten (Dutch part)": "SX",
    "Slovak Republic": "SK",
    "Slovakia": "SK",
    "Slovenia": "SI",
    "Socialist Republic of Viet Nam": "VN",
    "Solomon Islands": "SB",
    "Somalia": "SO",
    "South Africa": "ZA",
    "South Georgia and the South Sandwich Islands": "GS",
    "South Korea": "KR",
    "South Sudan": "SS",
    "Spain": "ES",
    "Sri Lanka": "LK",
    "State of Israel": "IL",
    "State of Kuwait": "KW",
    "State of Qatar": "QA",
    "Sudan": "SD",
    "Sultanate of Oman": "OM",
    "Suriname": "SR",
    "Svalbard and Jan Mayen": "SJ",
    "Sweden": "SE",
    "Swiss Confederation": "CH",
    "Switzerland": "CH",
    "Syria": "SY",
    "Syrian Arab Republic": "SY",
    "Taiwan": "TW",
    "Taiwan, Province of China": "TW",
    "Tajikistan": "TJ",
    "Tanzania": "TZ",
    "Tanzania, United Republic of": "TZ",
    "Thailand": "TH",
    "Timor-Leste": "TL",
    "Togo": "TG",
    "Togolese Republic": "TG",
    "Tokelau": "TK",
    "Tonga": "TO",
    "Trinidad and Tobago": "TT",
    "Tunisia": "TN",
    "Turkey": "TR",
    "Turkmenistan": "TM",
    "Turks and Caicos Islands": "TC",
    "Tuvalu": "TV",
    "Türkiye": "TR",
    "Uganda": "UG",
    "Ukraine": "UA",
    "Union of the Comoros": "KM",
    "United Arab Emirates": "AE",
    "United Kingdom": "GB",
    "United Kingdom of Great Britain and Northern Ireland": "GB",
    "United Mexican States": "MX",
    "United Republic of Tanzania": "TZ",
    "United States": "US",
    "United States Minor Outlying Islands": "UM",
    "United States of America": "US",
    "Uruguay": "UY",
    "Uzbekistan": "UZ",
    "Vanuatu": "VU",
    "Venezuela": "VE",
    "Venezuela, Bolivarian Republic of": "VE",
    "Viet Nam": "VN",
    "Vietnam": "VN",
    "Virgin Islands of the United States": "VI",
    "Virgin Islands, British": "VG",
    "Virgin Islands, U.S.": "VI",
    "Wallis and Futuna": "WF",
    "Western Sahara": "EH",
    "Yemen": "YE",
    "Zambia": "ZM",
    "Zimbabwe": "ZW",
    "the State of Eritrea": "ER",
    "the State of Palestine": "PS",
    "Åland Islands": "AX",
}
HOST_NAMES = {
    "Slovakkia": "SK",
    "Läti": "LV",
    "Itaalia": "IT",
    "Tšehhi": "CZ",
    "Ameerika Ühendriigid": "US",
    "Šveits": "CH",
    "Jaapan": "JP",
    "Hiina": "CN",
    "Bulgaaria": "BG",
    "India": "IN",
    "Kreeka": "GR",
    "Poola": "PL",
    "Prantsusmaa": "FR",
    "Saksamaa": "DE",
    "Ungari": "HU",
}


def own_url(value, base):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base, value))
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or (parsed.hostname or "").removeprefix("www.") != "harno.ee"
    ):
        return None
    if (
        re.search(
            r"\.(?:pdf|docx?|xlsx?|pptx?|jpg|png|zip)$", parsed.path, re.I
        )
        or parsed.path.startswith(
            ("/sites/", "/cdn-cgi/", "/otsing", "/search", "/user", "/admin")
        )
        or "@" in parsed.path
    ):
        return None
    if any(
        key not in ("page",) for key in urllib.parse.parse_qs(parsed.query)
    ):
        return None
    return urllib.parse.urlunsplit(
        ("https", "www.harno.ee", parsed.path.rstrip("/"), parsed.query, "")
    )


def article(root):
    return one(nodes(root, "main"), "Harno main content")


def discovery_links(root, url):
    """Follow current funding navigation, detail links and HTML pagination."""
    for link in nodes(root, "a"):
        target = own_url(link.attrs.get("href", ""), url)
        if not target:
            continue
        path = urllib.parse.urlsplit(target).path
        label = link.text()
        if path in ("/en", "/en/Youth-field-digital-leap"):
            continue
        if path.endswith("/summer-and-winter-schools"):
            # The English umbrella retains this obsolete synonym alongside
            # its current explicit scholarship link.
            target = (
                target.rsplit("/", 1)[0] + "/scholarships-summer-and-winter"
            )
        # This source covers academic scholarships and higher-education grants.
        # Municipal service allocations and youth-service operating grants are
        # separate publisher programmes, not student or academic opportunities.
        if re.search(
            r"/muud-toetusmeetmed/|/noortevaldkonna-toetused/|/huvihariduse-|/noorte-tervistav",
            path,
        ):
            continue
        if path.startswith(
            ("/uudised/", "/en/news/", "/events/", "/sundmused/")
        ):
            continue
        if "/scholarships-and-grants/" in path or FUNDING_WORDS.search(label):
            yield target
        elif label in ("EST", "ENG") or link.attrs.get("rel") == "next":
            yield target
        elif urllib.parse.urlsplit(target).query and "?page=" in target:
            yield target
        elif re.search(
            r"Processing of personal data|Cookies|Isikuandmete|Küpsised",
            label,
            re.I,
        ):
            yield target


def canonical_url(root, fallback):
    canonical = [
        n.attrs.get("href")
        for n in nodes(root, "link")
        if n.attrs.get("rel") == "canonical"
    ]
    return own_url(canonical[0], fallback) if canonical else fallback


def read_pages():
    global _PAGE_COMPLETED
    pending = [SOURCE_URL]
    pages = {}
    visited = set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        paginated = "page" in urllib.parse.parse_qs(
            urllib.parse.urlsplit(url).query)
        root = fetch_inventory_page(page, url) if paginated else page(url)
        if any("page" in urllib.parse.parse_qs(
                urllib.parse.urlsplit(target).query)
               for target in discovery_links(root, url)):
            _PAGE_COMPLETED = time.monotonic()
        check_access(root)
        visited.add(url)
        canonical = canonical_url(root, url)
        if not canonical:
            raise AdapterError("Unsafe Harno canonical URL", "parse")
        visited.add(canonical)
        pages[canonical] = root
        for target in discovery_links(root, canonical):
            if target not in visited and target not in pending:
                pending.append(target)
        if len(visited) + len(pending) > 180:
            raise AdapterError(
                "Harno funding discovery exceeds reviewed scope", "parse"
            )
    return pages


def page_identity(url):
    path = urllib.parse.urlsplit(url).path
    if re.search(r"estophil", path):
        return "estophilus"
    if re.search(
        r"summer-and-winter|eesti-keele(?:-ja-kultuuri-suve)?$", path
    ):
        return "language"
    if re.search(r"ustus-agur", path):
        return "agur"
    if re.search(r"special-needs|erivajadusega", path):
        return "special"
    if re.search(r"scholarships-international|/riikliku", path):
        return "international"
    if re.search(r"kristjan", path):
        return "kristjan"
    if re.search(
        r"academic-studies-estonian|eesti-keele-ja-kultuuri-akadeemiline", path
    ):
        return "ekkav"
    if re.search(r"scholarship-program-aimed|valiseestlaste", path):
        return "expatriate"
    return path


def programme_sections(root, identity):
    blocks = content_blocks(article(root))
    title = next((text for tag, text in blocks if tag == "h1"), None)
    if not title:
        raise AdapterError("Missing Harno programme title", "parse")
    blocks = blocks[
        next(i for i, (tag, _) in enumerate(blocks) if tag == "h1") :
    ]
    if identity == "/valisriikide-stipendiumipakkumised":
        return foreign_sections(root)
    if identity == "/nordplus":
        result = []
        panels = [
            n
            for n in article(root).walk()
            if n.attrs.get("id", "").startswith("accordion--nordplus")
        ]
        common = blocks[
            : next(
                (
                    i
                    for i, (_, text) in enumerate(blocks)
                    if text.startswith("Nordplus juunior")
                ),
                len(blocks),
            )
        ]
        for panel in panels:
            anchor = panel.attrs["id"]
            heading = one(
                [
                    n.text()
                    for n in root.walk()
                    if n.attrs.get("id")
                    == anchor.replace("accordion--", "accordion-title--")
                ],
                "Nordplus subprogramme title",
            )
            result.append((anchor, heading, common + content_blocks(panel)))
        return result
    # Archived contracts, recipient biographies and page-update dates are not calls.
    cleaned = []
    skip = False
    for tag, text in blocks:
        if tag == "anchor" and text == "kraadiope_2023":
            break
        if re.search(
            r"^(?:Last updated:|Viimati uuendatud|Stay tuned!|Püsi kursis!)",
            text,
        ):
            break
        if tag.startswith("h"):
            skip = bool(
                re.search(
                    r"(?:aasta stipendiaadid|Veebist|kogemuslugusid)",
                    text,
                    re.I,
                )
            )
        if not skip:
            cleaned.append((tag, text))
    blocks = cleaned
    anchors = [
        (i, text)
        for i, (tag, text) in enumerate(blocks)
        if tag == "anchor"
        and (
            text in LEGACY_IDS
            or (
                i + 1 < len(blocks)
                and blocks[i + 1][0].startswith("h")
                and FUNDING_WORDS.search(blocks[i + 1][1])
                and not DETAIL_WORDS.search(blocks[i + 1][1])
            )
        )
    ]
    if anchors:
        common = []
        if identity == "international":
            common = [
                (tag, text)
                for tag, text in blocks[: anchors[0][0]]
                if re.search(r"Russian Federation|Belarus", text)
            ]
        sections = []
        notices = {}
        if identity == "kristjan":
            active = None
            for tag, text in blocks[: anchors[0][0]]:
                if text.startswith(
                    "Kristjan Jaagu nimelise õpirände stipendiumiga"
                ):
                    active = "opirande_stipendium"
                elif text.startswith(
                    "Kristjan Jaagu nimelise välisõpingute stipendiumiga"
                ):
                    active = "valisopingute_stipendium"
                elif text.startswith("Kui te ei leidnud") or tag.startswith(
                    "h"
                ):
                    active = None
                if active:
                    notices.setdefault(active, []).append((tag, text))
        for index, (start, anchor) in enumerate(anchors):
            end = (
                anchors[index + 1][0]
                if index + 1 < len(anchors)
                else len(blocks)
            )
            section = blocks[start + 1 : end]
            heading = next(
                (text for tag, text in section if tag in ("h1", "h2", "h3")),
                title,
            )
            sections.append(
                (anchor, heading, common + notices.get(anchor, []) + section)
            )
        return sections
    starts = [
        (i, text)
        for i, (tag, text) in enumerate(blocks)
        if tag in ("h2", "h3")
        and FUNDING_WORDS.search(text)
        and not DETAIL_WORDS.search(text)
    ]
    if len(starts) > 1:
        return [
            (
                re.sub(r"\W+", "-", heading.lower()).strip("-"),
                heading,
                blocks[
                    start : (
                        starts[index + 1][0]
                        if index + 1 < len(starts)
                        else len(blocks)
                    )
                ],
            )
            for index, (start, heading) in enumerate(starts)
        ]
    return [(identity, title, blocks)]


def foreign_sections(root):
    """Retain country panels and every separately named HTML award within them."""
    sections = []
    closed = False
    for node in article(root).walk():
        if node.tag == "h2" and node.text() in (
            "Käimasolevad taotlusvoorud", "Lõppenud taotlusvoorud"
        ):
            closed = node.text() == "Lõppenud taotlusvoorud"
        anchor = node.attrs.get("id", "")
        if not anchor.startswith("accordion--") or not nodes(
            root, class_name="paragraph--type--accordion-item"
        ):
            continue
        labels = [
            n.text()
            for n in root.walk()
            if n.attrs.get("id")
            == anchor.replace("accordion--", "accordion-title--")
        ]
        if not labels:
            continue
        country = labels[0]
        blocks = content_blocks(node)
        starts = []
        for index, (tag, text) in enumerate(blocks):
            enumerated = bool(re.match(r"^\d+\.\s+.*stipendium", text, re.I))
            if not enumerated and (
                DETAIL_WORDS.search(text)
                or re.search(
                    r"^(?:Üldtingimused|Stipendiumi suurus|Stipendiume pakutakse|.*pakub.*järgmisi|.*pakub.*õppeaastaks)",
                    text,
                    re.I,
                )
            ):
                continue
            heading = (
                tag in ("h3", "h4")
                and len(text) > 20
                and not re.search(
                    r"võivad kandideerida|saavad kandideerida|võimalik taotleda|taotlusvoorud",
                    text,
                )
            )
            short_name = (
                tag == "p"
                and len(text) < 130
                and re.search(
                    r"^(?:Stipendium tasemeõppeks|Teadusstipendium|PhD-stipendium|Kunstistipendium)",
                    text,
                )
            )
            benefit_track = tag == "p" and bool(
                re.match(
                    r"^(?:Tšehhi keele ja kultuuri suvekursuste stipendiumi saajatele|tasemeõppe üliõpilaste õpirände stipendiumid)",
                    text,
                )
            )
            award_list = tag == "li" and bool(
                re.search(r"\(.*Scholarship\s*\)", text)
            )
            if (
                heading
                or short_name
                or enumerated
                or award_list
                or benefit_track
            ):
                label = re.sub(r"^\d+\.\s*", "", text)
                if enumerated:
                    label = label.split(". ", 1)[0]
                if benefit_track:
                    label = re.split(r" saajatele|:", label, maxsplit=1)[0]
                starts.append((index, label))
        common_end = next(
            (
                i
                for i, (_, text) in enumerate(blocks)
                if re.search(r"^(?:Üldtingimused|Dokumentide esitamine)", text)
            ),
            len(blocks),
        )
        # Numbered offer lists share the paragraphs following their final item.
        if starts and all(
            re.match(r"^\d+\.", blocks[i][1]) or blocks[i][0] == "li"
            for i, _ in starts
        ):
            common_end = starts[-1][0] + 1
            while common_end < len(blocks) and blocks[common_end][0] == "pdf":
                common_end += 1
        common = (
            [
                ("shared-pdf" if tag == "pdf" else "shared", text)
                for tag, text in blocks[: starts[0][0]] + blocks[common_end:]
            ]
            if starts
            else []
        )
        if starts and any(
            re.match(r"^tasemeõppe üliõpilaste õpirände stipendiumid", label)
            for _, label in starts
        ):
            shared_links = [
                block for block in common if block[0] == "shared-pdf"
            ]
            common = [
                ("shared", text)
                for tag, text in blocks
                if tag != "pdf"
                and not text.startswith(
                    (
                        "Tšehhi keele ja kultuuri suvekursuste stipendiumi saajatele",
                        "tasemeõppe üliõpilaste õpirände stipendiumid",
                    )
                )
            ] + shared_links
        inherited = (
            [
                (
                    "context",
                    "Publisher lists this call under closed application rounds.",
                )
            ]
            if closed
            else []
        )
        if not starts:
            title = (
                country
                + ": "
                + next(
                    (text for tag, text in blocks if tag == "p"), country
                ).split(". ", 1)[0]
            )
            if len(title) > 250:
                title = country + " - riikidevaheline stipendium"
            sections.append((anchor, title, inherited + blocks))
            continue
        for index, (start, title) in enumerate(starts):
            end = min(
                common_end,
                (
                    starts[index + 1][0]
                    if index + 1 < len(starts)
                    else len(blocks)
                ),
            )
            selected = inherited + common + blocks[start:end]
            sections.append(
                (
                    anchor
                    + ":"
                    + re.sub(r"\W+", "-", title.lower()).strip("-"),
                    country + ": " + title,
                    selected,
                )
            )
    return sections


def application_windows(blocks):
    """Extract explicit closing windows; event and project dates stay prose."""
    result = []
    blocks = [
        block for block in blocks if block[0] not in ("pdf", "shared-pdf")
    ]
    local = [block for block in blocks if block[0] != "shared"]
    unresolved = any(
        re.search(
            r"deadline|application|taotlus|tähtaeg|kandideerimis",
            text,
            re.I,
        )
        and re.search(
            r"not (?:yet )?(?:announced|known|published|confirmed)|"
            r"to be announced|\bTBA\b|teadmata|täpsustamisel|"
            r"(?:ei ole|pole).{0,30}(?:teada|avaldatud)|selgumisel",
            text,
            re.I,
        )
        for _, text in local
    )
    if unresolved:
        return []
    if len(local) != len(blocks):
        own_windows = application_windows(local)
        if any(
            re.search(
                r"deadline|application (?:period|round)|taotlustäht|"
                r"taotluste.{0,40}tähtaeg|kandideerimis.{0,30}tähtaeg|"
                r"taotlusvoor.{0,40}(?:avaneb|toimub|avatud)",
                text,
                re.I,
            )
            and DATE_PATTERN.search(text)
            for _, text in local
        ):
            return own_windows
        return own_windows or application_windows(
            [("p", text) for tag, text in blocks if tag == "shared"]
        )
    round_year = None
    statements = []
    for tag, text in blocks:
        context = False
        for clause in re.split(r"(?<=[.!?])\s+(?=[A-ZÕÄÖÜŠŽ0-9])|;", text):
            if re.search(
                r"application|taotlus|taotluste|kandideerimis", clause, re.I
            ):
                context = True
            if context and re.match(
                r"^\d{1,2}\.?\s*" + MONTH_PATTERN, clause, re.I
            ):
                clause = "Taotlustähtaeg: " + clause
            statements.append((tag, clause))
    for tag, text in statements:
        if re.search(
            r"20\d{2}\. aasta taotlusvoorud", text, re.I
        ) and not DATE_PATTERN.search(text):
            round_year = re.search(r"20\d{2}", text)[0]
            continue
        is_application = bool(
            re.search(
                r"application (?:period|deadline|round)|application.{0,100}(?:submitted|until)|applications? (?:must|should|can|will|are)|"
                r"(?:apply|submit).{0,50}(?:by|until|before)|taotlusvoor.{0,50}(?:avatud|toimub|avaneb|täht)|taotlustäht|"
                r"taotlus(?:te|e|\b).*?(?:täht|esita)|taotlemis.{0,20}tähta|"
                r"kandideerimis.{0,20}tähtaeg|taotlusi.{0,60}hiljemalt|"
                r"taotluse.{0,60}hiljemalt|taotluse.{0,80}hiljemalt|dokumentide.{0,100}hiljemalt",
                text,
                re.I,
            )
        )
        call_year = re.search(
            r"\b(20\d{2})\.\s*aasta\s+.{0,80}?taotlusvoor", text, re.I
        )
        inherited_year = call_year[1] if call_year else None
        if (
            round_year
            and tag in ("li", "tr", "p")
            and re.search(r"\d{1,2}\.\s*\w+\s+kuni\s+\d", text)
        ):
            is_application = True
            inherited_year = round_year
        elif round_year and dated_values(text) and not is_application:
            round_year = None
        if not is_application or re.search(
            r"after.{0,30}deadline|45 days|45 päeva|aruand|report|decision|projektid peavad lõppema|määratakse stipendiume perioodiks",
            text,
            re.I,
        ):
            continue
        text = re.split(
            r"lähetusteks|\b(?:for\s+)?(?:trips?|projects?|courses?|studies)\s+"
            r"(?:that\s+)?(?:start(?:s|ing)?|begin(?:s|ning)?|end(?:s|ing)?)\b|"
            r"stipendiumi kasutamise algus|projekt(?:i|ide) algus|õppeperiood algab",
            text,
            maxsplit=1,
            flags=re.I,
        )[0]
        dates = dated_values(text, inherited_year)
        if not dates:
            continue
        if len(dates) > 1:
            separator = text[dates[0][1] : dates[1][0]]
            is_range = len(dates) == 2 and re.fullmatch(
                r"\s*(?:(?:at|kell)\s+\d{1,2}[:.]\d{2}\s*)?"
                r"(?:[-–—]|to|until|kuni|(?:ning|ja) kestab kuni)\s*",
                separator,
                re.I,
            )
            if not is_range:
                if re.search(
                    r"deadline|täht|hiljemalt|(?:submit|apply).{0,50}\bby\b",
                    text[: dates[0][0]],
                    re.I,
                ):
                    dates = dates[:1]
                else:
                    continue
        if (
            len(dates) == 1
            and re.search(r"from|alates|open|avatud", text, re.I)
            and not re.search(
                r"until|by|täht|kuni|deadline|\bto\b", text, re.I
            )
        ):
            continue
        deadline = dates[-1][2]
        opening = dates[0][2] if len(dates) > 1 else None
        if opening and opening > deadline:
            opening = None
        if re.search(r"Estonian time", text, re.I):
            clock = re.search(r"\b(\d{1,2}):(\d{2})\b", text[dates[-1][1] :])
            if clock is None:
                clock = re.search(
                    r"(?:until|by|kuni|hiljemalt)\s+(?:at\s+|kell\s+)?"
                    r"(\d{1,2}):(\d{2})\s*(?:on\s*)?$",
                    text[: dates[-1][0]],
                    re.I,
                )
            if clock:
                from zoneinfo import ZoneInfo

                moment = datetime.fromisoformat(deadline).replace(
                    hour=int(clock[1]),
                    minute=int(clock[2]),
                    tzinfo=ZoneInfo("Europe/Tallinn"),
                )
                deadline = (
                    moment.astimezone(timezone.utc)
                    .isoformat(timespec="seconds")
                    .replace("+00:00", "Z")
                )
        value = (opening, deadline)
        if value not in result:
            result.append(value)
    # Repeated procedural wording can omit the opening or clock from the same call.
    combined = {}
    for opening, deadline in result:
        day = deadline[:10]
        before = combined.get(day, (None, deadline))
        combined[day] = (
            opening or before[0],
            deadline if "T" in deadline else before[1],
        )
    return sorted(combined.values(), key=lambda item: item[1])


def eligible_countries(blocks):
    result = []
    active = False
    expected_list = False
    names = {name.casefold(): code for name, code in COUNTRY_NAMES.items()}
    for tag, text in blocks:
        if re.search(
            r"countries whose citizenship|whose citizenship is", text, re.I
        ):
            active = True
            expected_list = True
        elif active and text.casefold() in names:
            result.append(names[text.casefold()])
        elif active and any(
            re.match(re.escape(name) + r"\b", text, re.I)
            for name in COUNTRY_NAMES
        ):
            remainder = text
            occurrences = []
            for name in sorted(COUNTRY_NAMES, key=len, reverse=True):
                pattern = re.compile(
                    r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.I
                )
                occurrences.extend(
                    (match.start(), COUNTRY_NAMES[name])
                    for match in pattern.finditer(remainder)
                )
                remainder = pattern.sub(
                    lambda match: " " * len(match[0]), remainder
                )
            remainder = re.sub(r"\b(?:and|or)\b|&", "", remainder, flags=re.I)
            if remainder.strip(" ,;."):
                raise AdapterError(
                    "Unrecognized citizenship list entry", "parse"
                )
            result.extend(code for _, code in sorted(occurrences))
        elif active and tag == "li":
            raise AdapterError("Unrecognized citizenship list entry", "parse")
        elif active and tag not in ("pdf", "shared-pdf"):
            active = False
    if expected_list and not result:
        raise AdapterError("Missing citizenship eligibility entries", "parse")
    return list(dict.fromkeys(result))


def parse_inventory(pages):
    groups = {}
    for url, root in pages.items():
        identity = page_identity(url)
        title = next((n.text() for n in nodes(article(root), "h1")), "")
        if (
            FUNDING_WORDS.search(title)
            or identity in LEGACY_IDS
            or identity in ("international", "kristjan", "ekkav", "expatriate")
        ):
            groups.setdefault(identity, []).append((url, root))
    records = []
    for identity, variants in groups.items():
        variants.sort(
            key=lambda item: (
                ("/en/" in item[0])
                if identity in ("kristjan", "ekkav", "estophilus")
                else ("/en/" not in item[0])
            )
        )
        url, root = variants[0]
        sections = programme_sections(root, identity)
        full_text = article(root).text()
        if re.search(
            r"no application rounds|alates 2026.{0,80}(?:ei toimu|lõpet)",
            full_text,
            re.I,
        ):
            continue
        if identity == "/opirande-stipendiumid":
            continue
        for anchor, title, blocks in sections:
            if (
                identity in ("international", "kristjan", "ekkav")
                and anchor == identity
            ):
                # These are umbrellas linking substantive programme sections.
                continue
            text = " ".join(value for _, value in blocks)
            if not re.search(
                r"appl|taotl|kandid|stipend|rahast|toetus", text, re.I
            ):
                continue
            windows = application_windows(blocks)
            source_anchor = anchor.split(":", 1)[0]
            record_url = url + (
                "#" + source_anchor
                if any(n.attrs.get("id") == source_anchor for n in root.walk())
                else ""
            )
            evidence_sections = [(record_url, blocks)]
            for related_url, related_root in variants[1:]:
                related = programme_sections(related_root, identity)
                matching = [
                    (related_url, other_blocks)
                    for other_anchor, _, other_blocks in related
                    if other_anchor == anchor
                ]
                if matching:
                    evidence_sections.extend(matching)
            evidence = fact_evidence(evidence_sections)
            if identity == "estophilus" and len(variants) > 1:
                evidence.append(
                    "The Estonian programme page supplies the current application round; the English translation may retain an older round. Each version is attributed separately."
                )
            attachments = sorted(
                {
                    urllib.parse.urljoin(section_url, value)
                    for section_url, section_blocks in evidence_sections
                    for tag, value in section_blocks
                    if tag in ("pdf", "shared-pdf")
                }
            )
            if attachments:
                evidence.append(
                    "Linked PDF conditions are not parsed; HTML coverage is retained. PDF-only amounts or conditions remain unverified: "
                    + " ".join(attachments)
                )
            category = (
                "grants"
                if re.search(r"rahastus|toetus|grant", title, re.I)
                and not re.search(r"stipend|scholar", title, re.I)
                else "scholarships"
            )
            calls = (
                windows
                if anchor == "opirande_stipendium"
                else [windows[0] if len(windows) == 1 else (None, None)]
            )
            for index, (opening, deadline) in enumerate(
                calls or [(None, None)]
            ):
                record = make_record(
                    title,
                    record_url,
                    [category],
                    evidence=list(evidence),
                    method="publisher-html",
                )
                aliases = LEGACY_IDS.get(anchor, [])
                if anchor == "opirande_stipendium":
                    previous_calls = dict(
                        zip(
                            ("2026-03-02", "2026-05-31", "2026-10-31"), aliases
                        )
                    )
                    record["id"] = (
                        previous_calls.get(deadline)
                        or hashlib.sha256(
                            (
                                SOURCE_ID + "|" + anchor + "|" + str(deadline)
                            ).encode()
                        ).hexdigest()[:24]
                    )
                else:
                    record["id"] = (
                        aliases[0]
                        if aliases
                        else hashlib.sha256(
                            (
                                SOURCE_ID + "|" + identity + "|" + anchor
                            ).encode()
                        ).hexdigest()[:24]
                    )
                record["deadline"] = deadline
                record["application_windows"] = [
                    {"opening": start, "deadline": end}
                    for start, end in (
                        [(opening, deadline)]
                        if anchor == "opirande_stipendium"
                        else windows
                    )
                ]
                record["language"] = "en" if "/en/" in url else "et"
                record["kind"] = (
                    "institutional-grant"
                    if re.search(
                        r"(?:rahastust võivad taotleda väliskõrgkoolid|omavalitsus|juriidili)",
                        text,
                        re.I,
                    )
                    else "opportunity" if deadline else "programme-overview"
                )
                record["eligible_countries"] = eligible_countries(blocks)
                if re.search(
                    r"(?:stud(?:y|ies|ying)|stay|research|courses?).{0,50}in Estonia|Estonian (?:university|higher education institution)",
                    text,
                    re.I,
                ) and identity not in ("kristjan", "ekkav"):
                    record["host_countries"] = ["EE"]
                if identity == "/valisriikide-stipendiumipakkumised":
                    host = HOST_NAMES.get(title.split(":", 1)[0])
                    if host and "ainult veebipõhiselt" not in text:
                        record["host_countries"] = [host]
                    if any(
                        value.strip().lower() == "eesti kodanik"
                        for _, value in blocks
                    ):
                        record["eligible_countries"] = ["EE"]
                elif "Õpingud toimuvad Itaalias" in text:
                    record["host_countries"] = ["IT"]
                elif identity in (
                    "estophilus",
                    "language",
                    "special",
                ) and re.search(r"Eestis|Estonia", text):
                    record["host_countries"] = ["EE"]
                if "Stipendiumile hetkel taotlusi vastu ei võeta" in text:
                    record["status"] = "expired"
                elif deadline and utc_now()[:10] > deadline[:10]:
                    record["status"] = "expired"
                elif (
                    deadline
                    and opening
                    and opening <= utc_now()[:10] < deadline[:10]
                ):
                    record["status"] = "open"
                elif (
                    "Publisher lists this call under closed application rounds."
                    in text
                ):
                    record["status"] = "expired"
                record["summary"] = concise_summary(
                    title, category, deadline, evidence
                )
                records.append(record)
    validate_records(records)
    return records


def collect_body():
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
_PUBLISHER_NEXT_AT = 0
_TRANSIENT = (429, 502, 503, 504)


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
        (urllib.parse.urlsplit(url).hostname or "")
        .lower()
        .removeprefix("www.")
    )
    interval = max(interval, 12 if host == "harno.ee" else 6)
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
    publisher = (
        "harno.ee"
        if host == "harno.ee" or host.endswith(".harno.ee")
        else host
    )
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
    global _PUBLISHER_NEXT_AT
    if retry_jitter:
        state, budget = legacy_pace(url, interval, start_padding=retry_jitter)
    else:
        state, budget = legacy_pace(url, interval)
    publisher = (urllib.parse.urlsplit(url).hostname or "").removeprefix(
        "www."
    ) == "harno.ee"
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
            if publisher and response.status in _TRANSIENT:
                budget["until"] = max(budget["until"], time.time() + 60)
                legacy_save_budget(state, budget)
            try:
                body = response.read(5_000_001)
                if len(body) > 5_000_000:
                    raise AdapterError("Response exceeds 5 MB", "fetch")
                return response.status, response.headers, body
            finally:
                if publisher:
                    _PUBLISHER_NEXT_AT = max(
                        _PUBLISHER_NEXT_AT, time.time() + 120, budget["until"]
                    )
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
    """Validate every collected or persisted record against the contract."""
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


def legacy_wait_publisher_cooldown(previous):
    """Carry publisher cooldown between separate serialized Action runners."""
    global _PUBLISHER_NEXT_AT
    if not previous:
        return
    value = previous.get("publisher_next_request_at")
    if value is None:
        # Old attempt/success clocks mark collection start, not the last request.
        ready = time.time() + 120
    else:
        try:
            if not isinstance(value, str) or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}" r"(?:\.\d{1,6})?Z",
                value,
            ):
                raise ValueError()
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                raise ValueError()
            ready = moment.timestamp()
        except (ValueError, TypeError, OverflowError):
            raise AdapterError(
                "Invalid publisher cooldown timestamp", "access"
            )
    _PUBLISHER_NEXT_AT = max(_PUBLISHER_NEXT_AT, ready)
    while ready > time.time():
        time.sleep(min(ready - time.time(), 60))


def metadata(attempt, previous, records, error=None):
    result = {
        "display_name": "Harno",
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
        "publisher_next_request_at": (
            datetime.fromtimestamp(
                _PUBLISHER_NEXT_AT or time.time() + 120, timezone.utc
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        ),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (f"Collected {len(records)} validated Harno funding records")
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def remove_expired_records(records, now=None):
    """Use source closure and supported calendars without inventing dates."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone.utc).date()
    retained = []
    for record in records:
        evidence = record.get("classification", {}).get("evidence", [])
        own_closure = False
        own_source = False
        for value in evidence:
            if not isinstance(value, str):
                continue
            if value.startswith("Source: "):
                own_source = value == "Source: " + record.get("url", "")
                continue
            if own_source and record.get("status") == "expired" and (
                "Publisher lists this call under closed application rounds."
                in value
                or "Stipendiumile hetkel taotlusi vastu ei võeta" in value
            ):
                own_closure = True
        if own_closure:
            continue
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
        elif value is None:
            windows = record.get("application_windows")
            complete_past = isinstance(windows, list) and bool(windows)
            for window in windows if isinstance(windows, list) else []:
                try:
                    if (
                        not isinstance(window, dict)
                        or set(window) != {"opening", "deadline"}
                    ):
                        raise ValueError("Unresolved application window")
                    end = window.get("deadline")
                    if not isinstance(end, str):
                        raise ValueError("Unresolved closing date")
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                        closing_day = datetime.fromisoformat(end).date()
                        closing_instant = None
                        ended = closing_day < today
                    else:
                        closing = datetime.fromisoformat(
                            end.replace("Z", "+00:00")
                        )
                        if closing.tzinfo is None:
                            raise ValueError("Closing timezone missing")
                        closing_day = closing.date()
                        closing_instant = closing
                        ended = closing < now
                    opening = window.get("opening")
                    if opening is not None:
                        if not isinstance(opening, str):
                            raise ValueError("Unresolved opening date")
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", opening):
                            opening_day = datetime.fromisoformat(
                                opening
                            ).date()
                            opening_instant = None
                        else:
                            start = datetime.fromisoformat(
                                opening.replace("Z", "+00:00")
                            )
                            if start.tzinfo is None:
                                raise ValueError("Opening timezone missing")
                            opening_day = start.date()
                            opening_instant = start
                        if (opening_instant is None) != (
                            closing_instant is None
                        ):
                            raise ValueError("Mixed application precision")
                        if opening_instant is not None:
                            if (
                                opening_instant > closing_instant
                                or opening_instant > now
                            ):
                                raise ValueError(
                                    "Conflicting application times"
                                )
                        elif (
                            opening_day > closing_day or opening_day > today
                        ):
                            raise ValueError("Conflicting application dates")
                    complete_past = complete_past and ended
                except (ValueError, TypeError, OverflowError):
                    complete_past = False
                    break
            expired = complete_past
        if not expired:
            retained.append(record)
    return retained


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
        if publishing:
            wait_publisher_cooldown(previous)
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
FAMILY = "youthopps-ee-harno-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_IDS = {SOURCE_ID: 378390322}
COLLECTION_TIMEOUT = 1200
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 60
_PUBLICATION_SECONDS = 180
_FAILURE_SECONDS = 180
_PUBLISHER_TIMEOUT = _DEFAULT_HTTP_TIMEOUT = 30
_ARTIFACT_NAME = "ee-harno-pacing-state"
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
_PUBLISHER_HOSTS = {"harno.ee", "www.harno.ee"}
_LEGACY_NO_JOB = {
    38054411845: {
        "source": "ee-harno",
        "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "workflow_id": 378390322,
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
        "conclusion": "cancelled",
        "created_at": "2026-10-10T13:05:46Z",
    },
    38001830505: {
        "source": "ee-harno",
        "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "workflow_id": 378390322,
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
        "conclusion": "cancelled",
        "created_at": "2026-10-09T22:55:53Z",
    },
    37895522293: {
        "source": "ee-harno",
        "head_sha": "6f1971d163e39e44d46ffd72bec20795c59df105",
        "workflow_id": 378390322,
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
        "conclusion": "cancelled",
        "created_at": "2026-10-09T06:49:01Z",
    },
}
_LOCAL_RESEARCH_MIGRATION = {'schema': 1,
 'source': 'ee-harno',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/300#issuecomment-6208100706',
 'state': {'schema': 3,
           'family': 'youthopps-ee-harno-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [{'source': 'ee-harno',
                                           'stage': 'historical_content_access',
                                           'access_scope': 'excluded_municipal_resource_actual403_headers_unretained',
                                           'route': 'https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus',
                                           'evidence': 'Original source '
                                                       'README '
                                                       'at2c0b839701b53eaf2df9dee0dcdcb5218f93d3d6',
                                           'retry_after': 'unknown_unretained_not_zero',
                                           'known_typed_family_hold': 'not_recovered_no_automatic_clear'}],
           'research_provenance': {'status': 'UNAPPROVED_EXACT_READONLY_PRESENT_ORIGINAL_NAMESPACE_FULL_HISTORY_METADATA_MAX_PROPOSAL',
                                   'complete_attempts': 31,
                                   'executed_job_completion_upper_bounds': [{'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38763419751,
                                                                             'run_attempt': 1,
                                                                             'time': 1792079989.0,
                                                                             'jobs_sha256': 'dfabdf1c3132f3a1712667d5ae1474ebd04ab875f6ded9f735ed5a9a08fffe5f'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38721259434,
                                                                             'run_attempt': 1,
                                                                             'time': 1792061178.0,
                                                                             'jobs_sha256': '4e7e53aeed9d050ee8a2fe49baaa198b72b3899ca206d20a02b11d2411ae4095'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38720084043,
                                                                             'run_attempt': 1,
                                                                             'time': 1792063514.0,
                                                                             'jobs_sha256': '40cf3e076d4e55436f59441d91bfcfb80aa576fbfcf7c39697a83de3e47dacfc'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38686133959,
                                                                             'run_attempt': 1,
                                                                             'time': 1792050044.0,
                                                                             'jobs_sha256': '3b55cd2d30e0aa96782ee89d40ec5a6b1cfb8246419c6bb62a6309118f731f61'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38684235978,
                                                                             'run_attempt': 1,
                                                                             'time': 1792042131.0,
                                                                             'jobs_sha256': 'b8682043e2bb34f7855f1768aac282442b14348dc26ce0ddc2d9b509fcb9988c'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38644204940,
                                                                             'run_attempt': 1,
                                                                             'time': 1792015910.0,
                                                                             'jobs_sha256': '2cfde8ec105fd5fcbf162c9082dc9c63d822c6e450afac6b720f74c1e84748bb'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38637628378,
                                                                             'run_attempt': 1,
                                                                             'time': 1792012493.0,
                                                                             'jobs_sha256': '93fcf7dcef88e26d3f70b1335a4427023e6207736e74eeff7a3afc9323a487f0'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38608323727,
                                                                             'run_attempt': 1,
                                                                             'time': 1792000079.0,
                                                                             'jobs_sha256': '4f8e142eb0e47e40978e8b41c5bb42846f2632e86205173718b7b6af0a776365'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38564080097,
                                                                             'run_attempt': 1,
                                                                             'time': 1791981091.0,
                                                                             'jobs_sha256': '5fd55bed57d9c479e4b6a0b481d8b38244f96b8914ed3450603248d5bd90c37e'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38534815156,
                                                                             'run_attempt': 1,
                                                                             'time': 1791968104.0,
                                                                             'jobs_sha256': 'b29064d0322647ab446062deab19a5a4bc9b2490854cfbe085f151e4655b7508'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38516930780,
                                                                             'run_attempt': 1,
                                                                             'time': 1791955957.0,
                                                                             'jobs_sha256': 'bff94513a97a2220c0786885abd6734ca8cc94c478c4210fcb9853215095d3fb'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38482332142,
                                                                             'run_attempt': 1,
                                                                             'time': 1791933340.0,
                                                                             'jobs_sha256': '558c67bffb09913cb15228ec83517e631dfff4a9b5dab30f6509bcb01836b138'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38414897653,
                                                                             'run_attempt': 1,
                                                                             'time': 1791902533.0,
                                                                             'jobs_sha256': '511d2c38b99c940021404a363ac37c4a311d487e3c29a438d5f578219476eaca'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38329969297,
                                                                             'run_attempt': 1,
                                                                             'time': 1791853622.0,
                                                                             'jobs_sha256': '84693a59fb4b3fc135b63b94b1069e5a3a210a7da8f4ac6d999671f856f2866a'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38275587583,
                                                                             'run_attempt': 1,
                                                                             'time': 1791821631.0,
                                                                             'jobs_sha256': 'd07720c59c340e5716da7e4d8c9e1faa1466b07e0e55d5fc089962cb4d5b6c1e'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38213427518,
                                                                             'run_attempt': 1,
                                                                             'time': 1791787218.0,
                                                                             'jobs_sha256': '4e772e4957a90c78c911aa93220b52814a441d41585ba31ad743a30ec2e41155'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38180730696,
                                                                             'run_attempt': 1,
                                                                             'time': 1791758124.0,
                                                                             'jobs_sha256': '08b485cd9a4ce5816fb5862793851a080f71a919aec484a2f295fd9de3e4b2b5'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38143722505,
                                                                             'run_attempt': 1,
                                                                             'time': 1791726074.0,
                                                                             'jobs_sha256': '108bfb82b7cac846ab01b6b08deba46ab40e49020c1c1a2fb86d8dfed45457f0'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38118332691,
                                                                             'run_attempt': 1,
                                                                             'time': 1791700451.0,
                                                                             'jobs_sha256': 'd646ca0f381bd144e11fc1f361e4214133e67589ff8eb7ae5f322ef240139fc5'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38089845847,
                                                                             'run_attempt': 1,
                                                                             'time': 1791670141.0,
                                                                             'jobs_sha256': '8409c8fbe6b3f9b34a62849c3b6646a6e24ab52a6f879f616452a54d6840f609'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38072503217,
                                                                             'run_attempt': 1,
                                                                             'time': 1791656738.0,
                                                                             'jobs_sha256': 'd239bf31381aa87627af784dc8aaeba57da592113cc0522497d7472842385386'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38071840339,
                                                                             'run_attempt': 1,
                                                                             'time': 1791655525.0,
                                                                             'jobs_sha256': 'ef169207082e62a42e9dc75a4f4d73cd6898f9758409cb83ca73bec391ebb461'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 38030958953,
                                                                             'run_attempt': 1,
                                                                             'time': 1791613993.0,
                                                                             'jobs_sha256': 'e06b18da4940fed37a144e2a271ad9638beabd4b0a3ca8973ed684f78183a04f'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 37939829836,
                                                                             'run_attempt': 1,
                                                                             'time': 1791554045.0,
                                                                             'jobs_sha256': 'f9dc558b75fa8ec906094f330a5190d4010537018467628dc6de69f23c20bfbb'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 37862344249,
                                                                             'run_attempt': 1,
                                                                             'time': 1791504490.0,
                                                                             'jobs_sha256': '45a83d3273a541ccbfe7943dbcff4194e52347409c48d89f02618ab9c76f60c4'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 37862338709,
                                                                             'run_attempt': 1,
                                                                             'time': 1791504236.0,
                                                                             'jobs_sha256': 'f88f33ecf90b4e09860afe01fb023b333a5e4b492d819817390a795a131f127c'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 37861024227,
                                                                             'run_attempt': 1,
                                                                             'time': 1791503227.0,
                                                                             'jobs_sha256': '9640e043d34888dde06b165637e9750c27e36d92444995198f4e93a33e19d52d'},
                                                                            {'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_id': 37861016969,
                                                                             'run_attempt': 1,
                                                                             'time': 1791503121.0,
                                                                             'jobs_sha256': 'd0a2149135cc76d8c255536d90c41311041e8318a79d0fb7bb29a270f3a0e8af'}],
                                   'exact_no_executed_job_attempts': [{'run_id': 38054411845,
                                                                       'run_attempt': 1,
                                                                       'head_sha': '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488',
                                                                       'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                       'classification': 'exact_cancelled_no_executed_job_no_completion_or_wire_timestamp'},
                                                                      {'run_id': 38001830505,
                                                                       'run_attempt': 1,
                                                                       'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                       'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                       'classification': 'exact_cancelled_no_executed_job_no_completion_or_wire_timestamp'},
                                                                      {'run_id': 37895522293,
                                                                       'run_attempt': 1,
                                                                       'head_sha': '6f1971d163e39e44d46ffd72bec20795c59df105',
                                                                       'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                       'classification': 'exact_cancelled_no_executed_job_no_completion_or_wire_timestamp'}],
                                   'saved_history_sha256': 'c6aa26802bcdb858ffd8071855b0d31671ff6dd8698f0a946bb3a0086d7453f6',
                                   'actual_municipal_content403_retained': [{'source': 'ee-harno',
                                                                             'stage': 'historical_content_access',
                                                                             'access_scope': 'excluded_municipal_resource_actual403_headers_unretained',
                                                                             'route': 'https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus',
                                                                             'evidence': 'Original '
                                                                                         'source '
                                                                                         'README '
                                                                                         'at2c0b839701b53eaf2df9dee0dcdcb5218f93d3d6',
                                                                             'retry_after': 'unknown_unretained_not_zero',
                                                                             'known_typed_family_hold': 'not_recovered_no_automatic_clear'}],
                                   'legacy_raw_constraints': 'Actual original '
                                                             'publisher12sec/two '
                                                             'reservations/until0 '
                                                             'retained; '
                                                             'defaultGitHub '
                                                             'absentunknown; '
                                                             'collectionlock '
                                                             'absentunknown; '
                                                             'modernfamily '
                                                             'absentunknown. '
                                                             'No prior '
                                                             'refusal/429 '
                                                             'absence '
                                                             'inferred.',
                                   'initial_false_scope': 'Only ROOT public '
                                                          'current '
                                                          'academic-frontier '
                                                          'reevaluation '
                                                          'boundary can '
                                                          'authorize proposed '
                                                          'first modern '
                                                          'pendingFalse/blockedFalse '
                                                          'with unknown '
                                                          'ancestry; no known '
                                                          'recovered '
                                                          'typedhold cleared, '
                                                          'no generic '
                                                          'oldschema '
                                                          'conversion.',
                                   'original_publisher_reservations': [{'kind': 'authentic_original_publisher_reservation_not_response',
                                                                        'time': 1792060781.5730114,
                                                                        'inode': 2441,
                                                                        'raw_sha256': 'ccafd37ff398773e48d7639bdd92a035fa0f0fd3c9e96b01c1d3ac6314b3d746'},
                                                                       {'kind': 'authentic_original_publisher_reservation_not_response',
                                                                        'time': 1792060793.573897,
                                                                        'inode': 2441,
                                                                        'raw_sha256': 'ccafd37ff398773e48d7639bdd92a035fa0f0fd3c9e96b01c1d3ac6314b3d746'}],
                                   'exact_cancelled_no_executed_job': [{'run_id': 38054411845,
                                                                        'run_attempt': 1,
                                                                        'head_sha': '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488',
                                                                        'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                        'time_upperbound': None},
                                                                       {'run_id': 38001830505,
                                                                        'run_attempt': 1,
                                                                        'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                        'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                        'time_upperbound': None},
                                                                       {'run_id': 37895522293,
                                                                        'run_attempt': 1,
                                                                        'head_sha': '6f1971d163e39e44d46ffd72bec20795c59df105',
                                                                        'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                        'time_upperbound': None}],
                                   'original_legacy_observation': {'namespace': 'ee-harno-pacing',
                                                                   'status': 'PRESENT_UNKNOWN_ANCESTRY',
                                                                   'parent_inode': 2440,
                                                                   'files': [{'bucket': 'publisher',
                                                                              'name': 'f0e4273c98ffa7b26d168ea420e9c61e4fb587ff86ea5f21886bf83ee763aaf4',
                                                                              'raw_sha256': 'ccafd37ff398773e48d7639bdd92a035fa0f0fd3c9e96b01c1d3ac6314b3d746',
                                                                              'inode': 2441,
                                                                              'state': {'starts': [1792060781.5730114,
                                                                                                   1792060793.573897],
                                                                                        'interval': 12,
                                                                                        'until': 0}}],
                                                                   'collection_lock_before_entry': {'status': 'ABSENT_UNKNOWN'}},
                                   'original_legacy_observation_sha256': '80b7953c95f3062f5c6decea94b9e5cbaeb1810b9de1e1617d614d8b6b4b73cf',
                                   'latest_metadata_cooldown': {'data_source_head': 'c01f19ca7dbb15cd4c337eb3325d70c0f056d153',
                                                                'git_blob_sha': '8f71ef05ac6665d10bcb78f2d22831d28d6db9ae',
                                                                'metadata_raw_sha256': '7705fe8ba7700b2504f4f9207547b32ff8d2491ecaa213d3aaf2e549aefce175',
                                                                'publisher_next_request_at': '2026-10-15T15:57:39.405Z',
                                                                'epoch': 1792079859.405,
                                                                'original_tail_seconds': 120},
                                   'complete_fresh_history_sha256': 'ab971636286ad40d448a4b940feae8812a515a12e6dd8180b5f2f9d0eb3eba2f'},
           'starts': [1791503121.0,
                      1791503227.0,
                      1791504236.0,
                      1791504490.0,
                      1791554045.0,
                      1791613993.0,
                      1791655525.0,
                      1791656738.0,
                      1791670141.0,
                      1791700451.0,
                      1791726074.0,
                      1791758124.0,
                      1791787218.0,
                      1791821631.0,
                      1791853622.0,
                      1791902533.0,
                      1791933340.0,
                      1791955957.0,
                      1791968104.0,
                      1791981091.0,
                      1792000079.0,
                      1792012493.0,
                      1792015910.0,
                      1792042131.0,
                      1792050044.0,
                      1792060781.5730114,
                      1792060793.573897,
                      1792061178.0,
                      1792063514.0,
                      1792079989.0],
           'interval': 12.2,
           'until': 1792124937.660951,
           'page_gap': 30,
           'completed': 1792079989.0,
           'inventory_completed': 0,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792124877.660951},
 'state_sha256': '4bca9e1bc6efc60601d048108b3de1401ac52cae0e91a19b01cf2ac92942733b',
 'legacy_state_sha256': '80b7953c95f3062f5c6decea94b9e5cbaeb1810b9de1e1617d614d8b6b4b73cf'}
_REVIEWED_NATIVE_MIGRATION = {'schema': 1,
 'source': 'ee-harno',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/300#issuecomment-6208578215',
 'state': {'schema': 3,
           'family': 'youthopps-ee-harno-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [{'source': 'ee-harno',
                                           'stage': 'historical_content_access',
                                           'access_scope': 'excluded_municipal_resource_actual403_headers_unretained',
                                           'route': 'https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus',
                                           'evidence': 'Original source '
                                                       'README '
                                                       'at2c0b839701b53eaf2df9dee0dcdcb5218f93d3d6',
                                           'retry_after': 'unknown_unretained_not_zero',
                                           'known_typed_family_hold': 'not_recovered_no_automatic_clear'}],
           'research_provenance': {'actual_municipal_content403_retained': [{'access_scope': 'excluded_municipal_resource_actual403_headers_unretained',
                                                                             'evidence': 'Original '
                                                                                         'source '
                                                                                         'README '
                                                                                         'at2c0b839701b53eaf2df9dee0dcdcb5218f93d3d6',
                                                                             'known_typed_family_hold': 'not_recovered_no_automatic_clear',
                                                                             'retry_after': 'unknown_unretained_not_zero',
                                                                             'route': 'https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus',
                                                                             'source': 'ee-harno',
                                                                             'stage': 'historical_content_access'}],
                                   'complete_attempts': 31,
                                   'complete_fresh_history_sha256': 'ab971636286ad40d448a4b940feae8812a515a12e6dd8180b5f2f9d0eb3eba2f',
                                   'exact_cancelled_no_executed_job': [{'head_sha': '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488',
                                                                        'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                        'run_attempt': 1,
                                                                        'run_id': 38054411845,
                                                                        'time_upperbound': None},
                                                                       {'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                        'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                        'run_attempt': 1,
                                                                        'run_id': 38001830505,
                                                                        'time_upperbound': None},
                                                                       {'head_sha': '6f1971d163e39e44d46ffd72bec20795c59df105',
                                                                        'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                        'run_attempt': 1,
                                                                        'run_id': 37895522293,
                                                                        'time_upperbound': None}],
                                   'exact_no_executed_job_attempts': [{'classification': 'exact_cancelled_no_executed_job_no_completion_or_wire_timestamp',
                                                                       'head_sha': '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488',
                                                                       'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                       'run_attempt': 1,
                                                                       'run_id': 38054411845},
                                                                      {'classification': 'exact_cancelled_no_executed_job_no_completion_or_wire_timestamp',
                                                                       'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                                                                       'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                       'run_attempt': 1,
                                                                       'run_id': 38001830505},
                                                                      {'classification': 'exact_cancelled_no_executed_job_no_completion_or_wire_timestamp',
                                                                       'head_sha': '6f1971d163e39e44d46ffd72bec20795c59df105',
                                                                       'jobs_sha256': 'b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f',
                                                                       'run_attempt': 1,
                                                                       'run_id': 37895522293}],
                                   'executed_job_completion_upper_bounds': [{'jobs_sha256': 'dfabdf1c3132f3a1712667d5ae1474ebd04ab875f6ded9f735ed5a9a08fffe5f',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38763419751,
                                                                             'time': 1792079989.0},
                                                                            {'jobs_sha256': '4e7e53aeed9d050ee8a2fe49baaa198b72b3899ca206d20a02b11d2411ae4095',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38721259434,
                                                                             'time': 1792061178.0},
                                                                            {'jobs_sha256': '40cf3e076d4e55436f59441d91bfcfb80aa576fbfcf7c39697a83de3e47dacfc',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38720084043,
                                                                             'time': 1792063514.0},
                                                                            {'jobs_sha256': '3b55cd2d30e0aa96782ee89d40ec5a6b1cfb8246419c6bb62a6309118f731f61',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38686133959,
                                                                             'time': 1792050044.0},
                                                                            {'jobs_sha256': 'b8682043e2bb34f7855f1768aac282442b14348dc26ce0ddc2d9b509fcb9988c',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38684235978,
                                                                             'time': 1792042131.0},
                                                                            {'jobs_sha256': '2cfde8ec105fd5fcbf162c9082dc9c63d822c6e450afac6b720f74c1e84748bb',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38644204940,
                                                                             'time': 1792015910.0},
                                                                            {'jobs_sha256': '93fcf7dcef88e26d3f70b1335a4427023e6207736e74eeff7a3afc9323a487f0',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38637628378,
                                                                             'time': 1792012493.0},
                                                                            {'jobs_sha256': '4f8e142eb0e47e40978e8b41c5bb42846f2632e86205173718b7b6af0a776365',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38608323727,
                                                                             'time': 1792000079.0},
                                                                            {'jobs_sha256': '5fd55bed57d9c479e4b6a0b481d8b38244f96b8914ed3450603248d5bd90c37e',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38564080097,
                                                                             'time': 1791981091.0},
                                                                            {'jobs_sha256': 'b29064d0322647ab446062deab19a5a4bc9b2490854cfbe085f151e4655b7508',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38534815156,
                                                                             'time': 1791968104.0},
                                                                            {'jobs_sha256': 'bff94513a97a2220c0786885abd6734ca8cc94c478c4210fcb9853215095d3fb',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38516930780,
                                                                             'time': 1791955957.0},
                                                                            {'jobs_sha256': '558c67bffb09913cb15228ec83517e631dfff4a9b5dab30f6509bcb01836b138',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38482332142,
                                                                             'time': 1791933340.0},
                                                                            {'jobs_sha256': '511d2c38b99c940021404a363ac37c4a311d487e3c29a438d5f578219476eaca',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38414897653,
                                                                             'time': 1791902533.0},
                                                                            {'jobs_sha256': '84693a59fb4b3fc135b63b94b1069e5a3a210a7da8f4ac6d999671f856f2866a',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38329969297,
                                                                             'time': 1791853622.0},
                                                                            {'jobs_sha256': 'd07720c59c340e5716da7e4d8c9e1faa1466b07e0e55d5fc089962cb4d5b6c1e',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38275587583,
                                                                             'time': 1791821631.0},
                                                                            {'jobs_sha256': '4e772e4957a90c78c911aa93220b52814a441d41585ba31ad743a30ec2e41155',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38213427518,
                                                                             'time': 1791787218.0},
                                                                            {'jobs_sha256': '08b485cd9a4ce5816fb5862793851a080f71a919aec484a2f295fd9de3e4b2b5',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38180730696,
                                                                             'time': 1791758124.0},
                                                                            {'jobs_sha256': '108bfb82b7cac846ab01b6b08deba46ab40e49020c1c1a2fb86d8dfed45457f0',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38143722505,
                                                                             'time': 1791726074.0},
                                                                            {'jobs_sha256': 'd646ca0f381bd144e11fc1f361e4214133e67589ff8eb7ae5f322ef240139fc5',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38118332691,
                                                                             'time': 1791700451.0},
                                                                            {'jobs_sha256': '8409c8fbe6b3f9b34a62849c3b6646a6e24ab52a6f879f616452a54d6840f609',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38089845847,
                                                                             'time': 1791670141.0},
                                                                            {'jobs_sha256': 'd239bf31381aa87627af784dc8aaeba57da592113cc0522497d7472842385386',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38072503217,
                                                                             'time': 1791656738.0},
                                                                            {'jobs_sha256': 'ef169207082e62a42e9dc75a4f4d73cd6898f9758409cb83ca73bec391ebb461',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38071840339,
                                                                             'time': 1791655525.0},
                                                                            {'jobs_sha256': 'e06b18da4940fed37a144e2a271ad9638beabd4b0a3ca8973ed684f78183a04f',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 38030958953,
                                                                             'time': 1791613993.0},
                                                                            {'jobs_sha256': 'f9dc558b75fa8ec906094f330a5190d4010537018467628dc6de69f23c20bfbb',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 37939829836,
                                                                             'time': 1791554045.0},
                                                                            {'jobs_sha256': '45a83d3273a541ccbfe7943dbcff4194e52347409c48d89f02618ab9c76f60c4',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 37862344249,
                                                                             'time': 1791504490.0},
                                                                            {'jobs_sha256': 'f88f33ecf90b4e09860afe01fb023b333a5e4b492d819817390a795a131f127c',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 37862338709,
                                                                             'time': 1791504236.0},
                                                                            {'jobs_sha256': '9640e043d34888dde06b165637e9750c27e36d92444995198f4e93a33e19d52d',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 37861024227,
                                                                             'time': 1791503227.0},
                                                                            {'jobs_sha256': 'd0a2149135cc76d8c255536d90c41311041e8318a79d0fb7bb29a270f3a0e8af',
                                                                             'kind': 'legacy_actual_job_completion_UPPER_BOUND_not_wire',
                                                                             'run_attempt': 1,
                                                                             'run_id': 37861016969,
                                                                             'time': 1791503121.0}],
                                   'initial_false_scope': 'Only ROOT public '
                                                          'current '
                                                          'academic-frontier '
                                                          'reevaluation '
                                                          'boundary can '
                                                          'authorize proposed '
                                                          'first modern '
                                                          'pendingFalse/blockedFalse '
                                                          'with unknown '
                                                          'ancestry; no known '
                                                          'recovered '
                                                          'typedhold cleared, '
                                                          'no generic '
                                                          'oldschema '
                                                          'conversion.',
                                   'latest_metadata_cooldown': {'data_source_head': 'c01f19ca7dbb15cd4c337eb3325d70c0f056d153',
                                                                'epoch': 1792079859.405,
                                                                'git_blob_sha': '8f71ef05ac6665d10bcb78f2d22831d28d6db9ae',
                                                                'metadata_raw_sha256': '7705fe8ba7700b2504f4f9207547b32ff8d2491ecaa213d3aaf2e549aefce175',
                                                                'original_tail_seconds': 120,
                                                                'publisher_next_request_at': '2026-10-15T15:57:39.405Z'},
                                   'legacy_raw_constraints': 'Actual original '
                                                             'publisher12sec/two '
                                                             'reservations/until0 '
                                                             'retained; '
                                                             'defaultGitHub '
                                                             'absentunknown; '
                                                             'collectionlock '
                                                             'absentunknown; '
                                                             'modernfamily '
                                                             'absentunknown. '
                                                             'No prior '
                                                             'refusal/429 '
                                                             'absence '
                                                             'inferred.',
                                   'original_legacy_observation': {'collection_lock_before_entry': {'status': 'ABSENT_UNKNOWN'},
                                                                   'files': [{'bucket': 'publisher',
                                                                              'inode': 2441,
                                                                              'name': 'f0e4273c98ffa7b26d168ea420e9c61e4fb587ff86ea5f21886bf83ee763aaf4',
                                                                              'raw_sha256': 'ccafd37ff398773e48d7639bdd92a035fa0f0fd3c9e96b01c1d3ac6314b3d746',
                                                                              'state': {'interval': 12,
                                                                                        'starts': [1792060781.5730114,
                                                                                                   1792060793.573897],
                                                                                        'until': 0}}],
                                                                   'namespace': 'ee-harno-pacing',
                                                                   'parent_inode': 2440,
                                                                   'status': 'PRESENT_UNKNOWN_ANCESTRY'},
                                   'original_legacy_observation_sha256': '80b7953c95f3062f5c6decea94b9e5cbaeb1810b9de1e1617d614d8b6b4b73cf',
                                   'original_publisher_reservations': [{'inode': 2441,
                                                                        'kind': 'authentic_original_publisher_reservation_not_response',
                                                                        'raw_sha256': 'ccafd37ff398773e48d7639bdd92a035fa0f0fd3c9e96b01c1d3ac6314b3d746',
                                                                        'time': 1792060781.5730114},
                                                                       {'inode': 2441,
                                                                        'kind': 'authentic_original_publisher_reservation_not_response',
                                                                        'raw_sha256': 'ccafd37ff398773e48d7639bdd92a035fa0f0fd3c9e96b01c1d3ac6314b3d746',
                                                                        'time': 1792060793.573897}],
                                   'saved_history_sha256': 'c6aa26802bcdb858ffd8071855b0d31671ff6dd8698f0a946bb3a0086d7453f6',
                                   'status': 'UNAPPROVED_EXACT_READONLY_PRESENT_ORIGINAL_NAMESPACE_FULL_HISTORY_METADATA_MAX_PROPOSAL'},
           'starts': [1791503121.0,
                      1791503227.0,
                      1791504236.0,
                      1791504490.0,
                      1791554045.0,
                      1791613993.0,
                      1791655525.0,
                      1791656738.0,
                      1791670141.0,
                      1791700451.0,
                      1791726074.0,
                      1791758124.0,
                      1791787218.0,
                      1791821631.0,
                      1791853622.0,
                      1791902533.0,
                      1791933340.0,
                      1791955957.0,
                      1791968104.0,
                      1791981091.0,
                      1792000079.0,
                      1792012493.0,
                      1792015910.0,
                      1792042131.0,
                      1792050044.0,
                      1792060781.5730114,
                      1792060793.573897,
                      1792061178.0,
                      1792063514.0,
                      1792079989.0,
                      1792125498.8819108,
                      1792125515.5226758,
                      1792125515.8932204,
                      1792125517.3344367,
                      1792125529.5347307,
                      1792125529.5396204,
                      1792125529.7892838,
                      1792125530.507509,
                      1792125560.5113091,
                      1792125560.5163624,
                      1792125560.907637,
                      1792125562.0425577,
                      1792125574.2465053,
                      1792125574.2507365,
                      1792125574.5238054,
                      1792125575.891551,
                      1792125588.096313,
                      1792125588.1004627,
                      1792125588.3523765,
                      1792125589.166112,
                      1792125601.3681183,
                      1792125601.3830197,
                      1792125601.4025347,
                      1792125603.0182335,
                      1792125615.2289777,
                      1792125615.232565,
                      1792125615.4172509,
                      1792125617.4923291,
                      1792125629.7190678,
                      1792125629.7342894,
                      1792125629.9181573,
                      1792125631.4861948,
                      1792125643.6883745,
                      1792125643.692583,
                      1792125643.9363017,
                      1792125645.89431,
                      1792125675.8984163,
                      1792125675.9062114,
                      1792125676.1476817,
                      1792125677.1068697,
                      1792125689.3121374,
                      1792125689.3171208,
                      1792125689.507442,
                      1792125689.9732378,
                      1792125702.17664,
                      1792125702.185602,
                      1792125702.1998687,
                      1792125702.9306598,
                      1792125715.136532,
                      1792125715.1463761,
                      1792125715.393047,
                      1792125716.0799108,
                      1792125728.2816849,
                      1792125728.2858555,
                      1792125728.5335045,
                      1792125729.5052185,
                      1792125741.7055385,
                      1792125741.710547,
                      1792125741.958177,
                      1792125743.3267615,
                      1792125755.5312533,
                      1792125755.5365875,
                      1792125755.7787628,
                      1792125757.2863133,
                      1792125769.4885702,
                      1792125769.4933193,
                      1792125769.8453312,
                      1792125770.251312,
                      1792125782.4542646,
                      1792125782.457381,
                      1792125782.6479075,
                      1792125784.5882547,
                      1792125814.593181,
                      1792125814.5988703,
                      1792125814.8541918,
                      1792125816.2934134,
                      1792125828.497484,
                      1792125828.5111747,
                      1792125828.5321732,
                      1792125829.9205656,
                      1792125846.2989216,
                      1792125846.3041666,
                      1792125846.4875891,
                      1792125847.5932207,
                      1792125859.7993124,
                      1792125859.8033876,
                      1792125859.9837158,
                      1792125861.4968038,
                      1792125873.6995077,
                      1792125873.7032208,
                      1792125873.7181947,
                      1792125875.1431477,
                      1792125887.3456817,
                      1792125887.3502152,
                      1792125887.3686326,
                      1792125888.105027,
                      1792125900.3084483,
                      1792125900.3131936,
                      1792125900.5578578,
                      1792125901.4209878,
                      1792125913.623632,
                      1792125913.6290307,
                      1792125913.6564367,
                      1792125914.6185997,
                      1792125926.8208437,
                      1792125926.8273263,
                      1792125926.8538663,
                      1792125927.782851,
                      1792125939.9863129,
                      1792125939.9930503,
                      1792125940.1933277,
                      1792125940.7177532,
                      1792125952.922878,
                      1792125952.928619,
                      1792125953.286385,
                      1792125955.0693142,
                      1792125967.3163674,
                      1792125967.3464112,
                      1792125968.1995492,
                      1792125971.9149303,
                      1792125984.151136,
                      1792125984.1614132,
                      1792125984.4403753,
                      1792125985.9751353,
                      1792125998.17898,
                      1792125998.1858404,
                      1792125998.3747213,
                      1792125999.6971164,
                      1792126011.8996506,
                      1792126011.9030814,
                      1792126012.1533408,
                      1792126013.9134536,
                      1792126026.1163893,
                      1792126026.1195316,
                      1792126026.3140364,
                      1792126027.588719,
                      1792126039.7921956,
                      1792126039.8014927,
                      1792126040.0094864,
                      1792126040.8935997,
                      1792126053.094207,
                      1792126053.1022089,
                      1792126053.3532343,
                      1792126053.8645053],
           'interval': 12.2,
           'until': 1792126173.867645,
           'page_gap': 30,
           'completed': 1792126053.8676512,
           'inventory_completed': 1792126013.916256,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792126055.7660363},
 'state_sha256': 'bc145ccf29f813f928eecab060868e75df89f5e66340b897ea7e675ac343e9a5',
 'legacy_state_sha256': '780f4585f496687ad0137661a4d66b18ebb89d6c8db4bbade3483a8a5daa003e'}
_LEGACY_ATTEMPTS = {
    38763419751: {
        "source": "ee-harno",
        "head_sha": "f77eed642c4544bbeed009440c406064a4f5c6ad",
        "workflow_id": 378390322,
        "jobs_sha256": "dfabdf1c3132f3a1712667d5ae1474ebd04ab875f6ded9f735ed5a9a08fffe5f",
    },
    38721259434: {
        "source": "ee-harno",
        "head_sha": "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "workflow_id": 378390322,
        "jobs_sha256": "4e7e53aeed9d050ee8a2fe49baaa198b72b3899ca206d20a02b11d2411ae4095",
    },
    38720084043: {
        "source": "ee-harno",
        "head_sha": "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "workflow_id": 378390322,
        "jobs_sha256": "40cf3e076d4e55436f59441d91bfcfb80aa576fbfcf7c39697a83de3e47dacfc",
    },
    38686133959: {
        "source": "ee-harno",
        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "workflow_id": 378390322,
        "jobs_sha256": "3b55cd2d30e0aa96782ee89d40ec5a6b1cfb8246419c6bb62a6309118f731f61",
    },
    38684235978: {
        "source": "ee-harno",
        "head_sha": "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "workflow_id": 378390322,
        "jobs_sha256": "b8682043e2bb34f7855f1768aac282442b14348dc26ce0ddc2d9b509fcb9988c",
    },
    38644204940: {
        "source": "ee-harno",
        "head_sha": "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
        "workflow_id": 378390322,
        "jobs_sha256": "2cfde8ec105fd5fcbf162c9082dc9c63d822c6e450afac6b720f74c1e84748bb",
    },
    38637628378: {
        "source": "ee-harno",
        "head_sha": "b52dc217b33e2b7ac3017d3a3a0a38be2b1add61",
        "workflow_id": 378390322,
        "jobs_sha256": "93fcf7dcef88e26d3f70b1335a4427023e6207736e74eeff7a3afc9323a487f0",
    },
    38608323727: {
        "source": "ee-harno",
        "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "workflow_id": 378390322,
        "jobs_sha256": "4f8e142eb0e47e40978e8b41c5bb42846f2632e86205173718b7b6af0a776365",
    },
    38564080097: {
        "source": "ee-harno",
        "head_sha": "92235b48ad33b9fa377e0736b689010173d7bfab",
        "workflow_id": 378390322,
        "jobs_sha256": "5fd55bed57d9c479e4b6a0b481d8b38244f96b8914ed3450603248d5bd90c37e",
    },
    38534815156: {
        "source": "ee-harno",
        "head_sha": "e47a87e43a672b1b6429749d1cef4851ea0dedee",
        "workflow_id": 378390322,
        "jobs_sha256": "b29064d0322647ab446062deab19a5a4bc9b2490854cfbe085f151e4655b7508",
    },
    38516930780: {
        "source": "ee-harno",
        "head_sha": "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "workflow_id": 378390322,
        "jobs_sha256": "bff94513a97a2220c0786885abd6734ca8cc94c478c4210fcb9853215095d3fb",
    },
    38482332142: {
        "source": "ee-harno",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390322,
        "jobs_sha256": "558c67bffb09913cb15228ec83517e631dfff4a9b5dab30f6509bcb01836b138",
    },
    38414897653: {
        "source": "ee-harno",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390322,
        "jobs_sha256": "511d2c38b99c940021404a363ac37c4a311d487e3c29a438d5f578219476eaca",
    },
    38329969297: {
        "source": "ee-harno",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390322,
        "jobs_sha256": "84693a59fb4b3fc135b63b94b1069e5a3a210a7da8f4ac6d999671f856f2866a",
    },
    38275587583: {
        "source": "ee-harno",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390322,
        "jobs_sha256": "d07720c59c340e5716da7e4d8c9e1faa1466b07e0e55d5fc089962cb4d5b6c1e",
    },
    38213427518: {
        "source": "ee-harno",
        "head_sha": "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "workflow_id": 378390322,
        "jobs_sha256": "4e772e4957a90c78c911aa93220b52814a441d41585ba31ad743a30ec2e41155",
    },
    38180730696: {
        "source": "ee-harno",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378390322,
        "jobs_sha256": "08b485cd9a4ce5816fb5862793851a080f71a919aec484a2f295fd9de3e4b2b5",
    },
    38143722505: {
        "source": "ee-harno",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378390322,
        "jobs_sha256": "108bfb82b7cac846ab01b6b08deba46ab40e49020c1c1a2fb86d8dfed45457f0",
    },
    38118332691: {
        "source": "ee-harno",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378390322,
        "jobs_sha256": "d646ca0f381bd144e11fc1f361e4214133e67589ff8eb7ae5f322ef240139fc5",
    },
    38089845847: {
        "source": "ee-harno",
        "head_sha": "10d99f8ef021c092851bf189ff67307865b8760c",
        "workflow_id": 378390322,
        "jobs_sha256": "8409c8fbe6b3f9b34a62849c3b6646a6e24ab52a6f879f616452a54d6840f609",
    },
    38072503217: {
        "source": "ee-harno",
        "head_sha": "9d16247e7af0063836678d7fffd3f850c2aead8c",
        "workflow_id": 378390322,
        "jobs_sha256": "d239bf31381aa87627af784dc8aaeba57da592113cc0522497d7472842385386",
    },
    38071840339: {
        "source": "ee-harno",
        "head_sha": "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "workflow_id": 378390322,
        "jobs_sha256": "ef169207082e62a42e9dc75a4f4d73cd6898f9758409cb83ca73bec391ebb461",
    },
    38054411845: {
        "source": "ee-harno",
        "head_sha": "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "workflow_id": 378390322,
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
    },
    38030958953: {
        "source": "ee-harno",
        "head_sha": "6e5922277de22b96874c23a705a7d838512c6caf",
        "workflow_id": 378390322,
        "jobs_sha256": "e06b18da4940fed37a144e2a271ad9638beabd4b0a3ca8973ed684f78183a04f",
    },
    38001830505: {
        "source": "ee-harno",
        "head_sha": "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "workflow_id": 378390322,
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
    },
    37939829836: {
        "source": "ee-harno",
        "head_sha": "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "workflow_id": 378390322,
        "jobs_sha256": "f9dc558b75fa8ec906094f330a5190d4010537018467628dc6de69f23c20bfbb",
    },
    37895522293: {
        "source": "ee-harno",
        "head_sha": "6f1971d163e39e44d46ffd72bec20795c59df105",
        "workflow_id": 378390322,
        "jobs_sha256": "b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f",
    },
    37862344249: {
        "source": "ee-harno",
        "head_sha": "8ddcdbd1cc626734e1553af904133b17aed80cc2",
        "workflow_id": 378390322,
        "jobs_sha256": "45a83d3273a541ccbfe7943dbcff4194e52347409c48d89f02618ab9c76f60c4",
    },
    37862338709: {
        "source": "ee-harno",
        "head_sha": "8ddcdbd1cc626734e1553af904133b17aed80cc2",
        "workflow_id": 378390322,
        "jobs_sha256": "f88f33ecf90b4e09860afe01fb023b333a5e4b492d819817390a795a131f127c",
    },
    37861024227: {
        "source": "ee-harno",
        "head_sha": "a37bb725f6426fc2376c7eb789db8f00ff3204ac",
        "workflow_id": 378390322,
        "jobs_sha256": "9640e043d34888dde06b165637e9750c27e36d92444995198f4e93a33e19d52d",
    },
    37861016969: {
        "source": "ee-harno",
        "head_sha": "a37bb725f6426fc2376c7eb789db8f00ff3204ac",
        "workflow_id": 378390322,
        "jobs_sha256": "d0a2149135cc76d8c255536d90c41311041e8318a79d0fb7bb29a270f3a0e8af",
    },
}
_LEGACY_ACCESS_OBSERVATIONS = [
    {
        "source": "ee-harno",
        "stage": "historical_content_access",
        "access_scope": "excluded_municipal_resource_actual403_headers_unretained",
        "route": "https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus",
        "evidence": "Original source README "
        "at2c0b839701b53eaf2df9dee0dcdcb5218f93d3d6",
        "retry_after": "unknown_unretained_not_zero",
        "known_typed_family_hold": "not_recovered_no_automatic_clear",
    }
]


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
                if status in (502, 503, 504) and attempt == 0:
                    break
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



def retain_discovered_inventory_completion():
    """Retain the native reader's discovered first-listing completion."""
    if _PAGE_COMPLETED is None:
        return
    check_deadline()
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        raise AdapterError("Trusted inventory state required", "access")
    with locked_budget():
        state = load_budget()
        if state["blocked"] or state["in_flight"]:
            raise AdapterError("Held or unsettled inventory state", "access")
        state["in_flight"] = True
        write_budget(state)
        elapsed = time.monotonic() - _PAGE_COMPLETED
        observed = time.time() - elapsed
        if not numeric(elapsed) or not supported_epoch(observed):
            raise AdapterError("Unusable listing completion clock", "access")
        state["inventory_completed"] = max(
            state["inventory_completed"], observed
        )
        state["observed_at"] = max(state["observed_at"], time.time())
        write_budget(state)
        state["in_flight"] = False
        write_budget(state)


def fetch_inventory_page(fetch, *args, **kwargs):
    global _INVENTORY_ACTIVE
    retain_discovered_inventory_completion()
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
    path = os.environ.get("HARNO_PACING_ARTIFACT_PATH")
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
    path = os.environ.get("HARNO_PACING_ARTIFACT_PATH")
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
                state["until"] = max(state["until"], _PUBLISHER_NEXT_AT)
                write_budget(state)
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
            if publisher and status in (502, 503, 504):
                state["until"] = max(state["until"], time.time() + 60)
                write_budget(state)
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
                document = body.decode(
                    response.headers.get_content_charset() or "utf-8", "strict"
                )
                try:
                    check_access(PublisherHTML(document).root)
                except AdapterError:
                    state["blocked"] = True
                    response_constraints_saved = True
                    raise
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
                global _PUBLISHER_NEXT_AT
                if response_received:
                    _PUBLISHER_NEXT_AT = max(
                        _PUBLISHER_NEXT_AT, time.time() + 120, state["until"]
                    )
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
    limit = (
        _RUN_END
        if name == "export"
        else (
            _RUN_END - _FAILURE_SECONDS - EXPORT_TIMEOUT
            if name == "publication"
            else _RUN_END - EXPORT_TIMEOUT
        )
    )
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
            hashlib.sha256(b"harno.ee").hexdigest(): "publisher",
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/300"
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
            latest != (38763419751, 1, SOURCE_ID)
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



def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True



def is_inventory_url(url):
    parsed = urllib.parse.urlsplit(url)
    path = parsed.path.rstrip("/")
    return (
        "page" in urllib.parse.parse_qs(parsed.query)
        or url == SOURCE_URL
        or page_identity(url)
        in {
            "international",
            "kristjan",
            "ekkav",
            "expatriate",
            "/nordplus",
            "/valisriikide-stipendiumipakkumised",
        }
        or path.endswith(
            (
                "/scholarships-and-grants",
                "/scholarships-studying-and-working-estonia",
                "/stipendiumid-ja-toetused",
            )
        )
    )


def page(url):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    _INVENTORY_ACTIVE = previous or is_inventory_url(url)
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



def wait_publisher_cooldown(previous):
    """Keep the original metadata wait inside the entry-frozen source owner."""
    check_deadline()
    if previous:
        value = previous.get("publisher_next_request_at")
        if value is None:
            waiting = 120
        else:
            # The original function below validates exact representation.
            try:
                waiting = max(
                    0,
                    datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    ).timestamp()
                    - time.time(),
                )
            except (ValueError, TypeError, AttributeError, OverflowError):
                return legacy_wait_publisher_cooldown(previous)
        if waiting >= remaining():
            raise PhaseExpired()
    legacy_wait_publisher_cooldown(previous)
    check_deadline()


if __name__ == "__main__":
    raise SystemExit(main())
