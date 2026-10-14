"""Collect official Harno scholarship and academic mobility opportunities."""

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
    delay = check_robots(url)
    document = fetch_source(url, min_interval=delay)
    if not re.search(
        r"</body\s*>\s*(?:</html\s*>\s*)?$", document, re.IGNORECASE
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    return PublisherHTML(document).root


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
    pending = [SOURCE_URL]
    pages = {}
    visited = set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        root = page(url)
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
        if node.tag == "h2" and node.text() == "Lõppenud taotlusvoorud":
            closed = True
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
                if deadline and utc_now()[:10] > deadline[:10]:
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
_USER_AGENT = "YouthOpp/1.0 (+https://github.com/YouthOpps/data-pipeline)"


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
    global _PUBLISHER_NEXT_AT
    state, budget = pace(url, interval)
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
            if publisher and response.status in _TRANSIENT:
                budget["until"] = max(budget["until"], time.time() + 60)
                save_budget(state, budget)
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
                if status in _TRANSIENT and attempt == 0:
                    break
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
    specific = [g for g in groups if "youthopp" in g["agents"]]
    selected = specific or [g for g in groups if "*" in g["agents"]]
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
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
    return max([6] + [g["delay"] for g in selected])


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


def wait_publisher_cooldown(previous):
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
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = utc_now(), None
    try:
        if args.publish:
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
