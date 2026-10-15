"""Collect genuine StipendiumPlus funding frameworks and training editions."""

import argparse
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import email.utils
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import signal
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "de-stipendiumplus"
SOURCE_URL = "https://stipendiumplus.de/"
WEBSITE_URL = SOURCE_URL
LANGUAGE = "de"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "nonprofit"
ATTRIBUTION = (
    "StipendiumPlus — Arbeitsgemeinschaft der Begabtenförderungswerke "
    "der Bundesrepublik Deutschland"
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


def article(root):
    return one(
        [n for n in nodes(root, "main") if n.attrs.get("id") == "main-content"],
        "complete public StipendiumPlus article",
    )


def reviewed_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "stipendiumplus.de"
        or parsed.query
        or parsed.username
        or parsed.password
    ):
        raise AdapterError("Unexpected source URL", "parse")
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", "", "")
    )


def academy_2027_facts(root):
    """Cover the complete edition body, excluding verified contact fields."""
    content = article(root)

    def elements(node):
        if any(isinstance(c, str) and c.strip() for c in node.children):
            raise AdapterError("Unexpected edition boundary text", "parse")
        return [c for c in node.children if isinstance(c, Node)]

    blocks = elements(content)
    expected = [
        "block block--bm-medium textteaser clip-lr",
        "block block--bm-medium",
        "block block--bm-medium",
        "block block--tm-larger block--bm-larger block--bp-larger",
    ]
    if len(blocks) != 4 or any(
        n.tag != "div" or n.attrs.get("class") != cls
        for n, cls in zip(blocks, expected)
    ):
        raise AdapterError("Edition article boundaries changed", "parse")
    core = one(
        nodes(content, "div", "block__content--corewrapper"),
        "complete academy programme",
    )
    if elements(blocks[2]) != [core]:
        raise AdapterError("Uncovered academy programme container", "parse")
    hero = one(nodes(blocks[0], "h1"), "edition title")
    if hero.text() != blocks[0].text() or nodes(blocks[0], "a"):
        raise AdapterError("Uncovered edition title content", "parse")
    if [n.text() for n in nodes(blocks[1], "div", "breadcrumb")] != [
        blocks[1].text()
    ]:
        raise AdapterError("Edition breadcrumb boundaries changed", "parse")
    related = one(
        nodes(blocks[3], "div", "othertopics"), "separate related topics"
    )
    if elements(blocks[3]) != [related] or [
        n.text() for n in nodes(related, "h2")
    ] != ["Weitere Themen"]:
        raise AdapterError("Edition related topics changed", "parse")
    children = elements(core)
    tags = [
        "h2",
        "p",
        "p",
        "div",
        "h3",
        "p",
        "p",
        "p",
        "ul",
        "h3",
        "p",
        "ul",
        "div",
        "h3",
        "p",
        "p",
        "h3",
        "p",
        "p",
        "h4",
        "p",
        "p",
    ]
    if [n.tag for n in children] != tags or children[-3].text() != "Kontakt":
        raise AdapterError(
            "Edition material/contact boundaries changed", "parse"
        )
    labels = [
        "Hauptansprechpartner",
        "Ansprechpartnerin für die Organisation im Bildungszentrum Kloster Banz",
    ]
    roles = [
        "– Teamleitung Bildungsprogramm, Studienförderwerk Klaus Murmann "
        "der Stiftung der Deutschen Wirtschaft",
        "– Leiterin Hochschulförderung, Institut der Begabtenförderung "
        "der Hanns-Seidel-Stiftung e.V.",
    ]
    for paragraph, label, role in zip(children[-2:], labels, roles):
        inline = [n for n in paragraph.children if isinstance(n, Node)]
        raw = [
            " ".join(n.split())
            for n in paragraph.children
            if isinstance(n, str) and n.strip()
        ]
        if (
            [n.tag for n in inline] != ["strong", "br", "strong", "br", "a"]
            or inline[0].text() != label
            or raw != [role, "E-Mail:"]
            or any(n.children for n in (inline[1], inline[3]))
            or any(
                isinstance(c, Node) for n in inline[:3:2] for c in n.children
            )
            or not re.fullmatch(
                r"[^\W\d_][\w .’'–-]{1,100}", inline[2].text(), re.UNICODE
            )
            or not inline[4].attrs.get("href", "").startswith("mailto:")
            or not re.fullmatch(r"[^\s@]+@[^\s@]+", inline[4].text())
            or any(isinstance(c, Node) for c in inline[4].children)
        ):
            raise AdapterError("Unverified edition contact-only tail", "parse")
    material = children[:-3]
    text = hero.text() + " " + " ".join(n.text() for n in material)
    routes = sorted(
        {
            urllib.parse.urljoin(PAGES["academy-2027"], n.attrs["href"])
            for section in material
            for n in nodes(section, "a")
            if n.attrs.get("href")
        }
    )
    return text, routes


def source_facts(key, root):
    """Retain complete substantive facts and finite own-route inventory."""
    canonical = one(
        [
            n.attrs.get("href", "")
            for n in nodes(root, "link")
            if n.attrs.get("rel") == "canonical"
        ],
        "publisher canonical",
    )
    if canonical != PAGES[key]:
        raise AdapterError("Publisher canonical changed", "parse")
    content = article(root)
    text = content.text().replace("\xad", "").replace("\u200b", "")
    if key == "imprint":
        # Rights are material. Personal addresses and contacts are not records.
        marker = "Haftung für Inhalte"
        if marker not in text:
            raise AdapterError("Publisher rights evidence missing", "access")
        publisher = re.search(r"Herausgeber (.*?) Technischer Betreiber:", text)
        if not publisher:
            raise AdapterError("Publisher attribution missing", "access")
        text = publisher[1] + " " + text[text.index(marker) :]
    elif key == "academy-2027":
        text, edition_routes = academy_2027_facts(root)
    elif key == "academy-2025":
        text = re.sub(
            r"Ansprechpartner:innen bei der Studienstiftung .*?"
            r"Warum das zählt",
            "Warum das zählt",
            text,
        )
    text = " ".join(text.split())
    links = set()
    for anchor in nodes(root, "a"):
        href = anchor.attrs.get("href", "")
        if not href or href.startswith(("mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(PAGES[key], href)
        parsed = urllib.parse.urlsplit(url)
        if (parsed.hostname or "").lower().removeprefix("www.") == (
            "stipendiumplus.de"
        ):
            if parsed.path.startswith("/wp-content/"):
                # Media/PDFs are not copied or fetched as award identities.
                continue
            links.add(reviewed_url(url))
    if key == "academy-2027":
        links = set(edition_routes)
    policies = set()
    for anchor in nodes(root, "a"):
        label = anchor.text().lower().replace("\xad", "")
        if re.search(
            r"impressum|datenschutz|copyright|lizenz|nutzungsbedingungen"
            r"|nutzungsrechte|rechtliche hinweise|agb|terms|licen[cs]e",
            label,
        ):
            policies.add(
                urllib.parse.urljoin(PAGES[key], anchor.attrs.get("href", ""))
            )
    if policies != {WEBSITE_URL + "impressum/", WEBSITE_URL + "datenschutz/"}:
        raise AdapterError("Publisher policy navigation changed", "access")
    return json.dumps(
        {
            "text": text,
            "own_routes": sorted(links),
            "policy_routes": sorted(policies),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


PAGES = {
    "academy-2025": (
        "https://stipendiumplus.de/die-werke/gemeinsame-sommerakademie/"
        "die-gemeinsame-sommerakademie-2025/"
    ),
    "academy-2027": (
        "https://stipendiumplus.de/die-werke/gemeinsame-sommerakademie/"
        "die-gemeinsame-sommerakademie-2027/"
    ),
    "academy": (
        "https://stipendiumplus.de/die-werke/gemeinsame-sommerakademie/"
    ),
    "chin-kobe": (
        "https://stipendiumplus.de/die-werke/china-kompetenz-projekt-ch"
        "in-kobe/"
    ),
    "doctoral-application": (
        "https://stipendiumplus.de/promovierende/wie-und-wann-bewerbe-i"
        "ch-mich/"
    ),
    "doctoral-benefits": (
        "https://stipendiumplus.de/promovierende/was-bietet-mir-ein-sti"
        "pendium/"
    ),
    "doctoral-eligibility": (
        "https://stipendiumplus.de/promovierende/was-sollte-ich-mitbrin" "gen/"
    ),
    "doctoral-faq": (
        "https://stipendiumplus.de/promovierende/haeufig-gestellte-frag" "en/"
    ),
    "doctoral-initiative": (
        "https://stipendiumplus.de/promovierende/die-promovierenden-ini"
        "tiative/"
    ),
    "doctoral": "https://stipendiumplus.de/promovierende/",
    "home": "https://stipendiumplus.de/",
    "imprint": "https://stipendiumplus.de/impressum/",
    "nie-wieder": (
        "https://stipendiumplus.de/die-werke/nie-wieder-gemeinsam-gegen"
        "-antisemitismus-fur-eine-plurale-gesellschaft/"
    ),
    "schools": "https://stipendiumplus.de/fuer-schulen-und-hochschulen/",
    "student-application": (
        "https://stipendiumplus.de/studierende/wie-und-wann-bewerbe-ich"
        "-mich/"
    ),
    "student-benefits": (
        "https://stipendiumplus.de/studierende/was-bietet-mir-ein-stipe"
        "ndium/"
    ),
    "student-eligibility": (
        "https://stipendiumplus.de/studierende/was-sollte-ich-mitbringe" "n/"
    ),
    "student-faq": (
        "https://stipendiumplus.de/studierende/haeufig-gestellte-fragen" "/"
    ),
    "students": "https://stipendiumplus.de/studierende/",
    "trainees": "https://stipendiumplus.de/auszubildende/",
    "works": "https://stipendiumplus.de/die-werke/",
}

CONDITIONS = {
    "academy-2025": (
        "93fe77c950f7b6dd5d15092f9757f90d1343c6fe991e69fc1c9809d846354214"
    ),
    "academy-2027": (
        "92f851910a2a8cd5350bcfcc00df2b31ad88d9f998df15a6f1c5b01b384555aa"
    ),
    "academy": (
        "21f28c4dfe00b844471cc76e75caba98059d51e093a3bdf1b032744fbf61349e"
    ),
    "chin-kobe": (
        "0fcd20063ce7f5c9904c137aa01514243c0850064a05de5392ed52719a7b56ff"
    ),
    "doctoral-application": (
        "94aa55fec117152f979733b41075bee6e8c88ef351f0fca37f53d86bb6c7cc2a"
    ),
    "doctoral-benefits": (
        "6c1fe7f2cfadacf257ab76d1bdc911ba72e1d2407caa7467d9e0d23d38489300"
    ),
    "doctoral-eligibility": (
        "97d008aa33782ba951786fa885a9747f9c4f1e5bd3db3a024a7fc25d1fb5785c"
    ),
    "doctoral-faq": (
        "933e1fb23fc2da7315295739f32efa129a24d1258414c8d1957bf77f34c4ce34"
    ),
    "doctoral-initiative": (
        "227f49b082067c6a15e1913595054950ab57711210c7981c193a75642d6b127c"
    ),
    "doctoral": (
        "dc03561316696dc7fc86ddaee0690b1c5dc50863e4d9536d4c4ad06ec2206a54"
    ),
    "home": (
        "0e424b2a5796ded0390cac5bbdca7ba0e51d18c40a217bdddbf1fb658236ebdf"
    ),
    "imprint": (
        "4851709bf682479990a3d4df48db8de8177b01c3706240f075f32529db41c151"
    ),
    "nie-wieder": (
        "e7315dc3f88085edb6e0ae13467cc6d1d00bcc1bf1fac8646cbcaf68c79bae2c"
    ),
    "schools": (
        "6e9152d486cbcc3d2660c7a0a85d8280aa96010db2f4b6ec388ba8bfc092f2ed"
    ),
    "student-application": (
        "7ddb49ab744eb6f7bae56d4ed33451e3fea80baf7c5eb4f540cb6f6cc0c67339"
    ),
    "student-benefits": (
        "7d55d5fb437dcc893df4b96e69560acb5c9eddc6bd53a415c39843485c3dbca4"
    ),
    "student-eligibility": (
        "8106b2f2304c6e709f2f15500970227443c176083ed146ac41df2b348a05e945"
    ),
    "student-faq": (
        "e6f8824d5ff30cb9c3f515698ac298e423824784972c93b9e71a1858878be0d6"
    ),
    "students": (
        "26796517e59f6d538126d3aa71e8fcca144806afe4364cc2a6a23f13e5c8b252"
    ),
    "trainees": (
        "4fd361d7f42038ce51adbb3d2d26f8e05a094d230c62916c8ef437c30d2b408a"
    ),
    "works": (
        "56a6c207ceb4397d6fd6fb3504f37af38c76143c74c27c970df73f9a8a38c579"
    ),
}

PROFILES = [
    {
        "track": "student-scholarships",
        "key": "student-benefits",
        "title": "Begabtenförderungswerke student scholarships",
        "categories": ["scholarships"],
        "hosts": [],
        "kind": "programme-overview",
        "summary": (
            "Member-administered study scholarships offer €300 monthly "
            "independent of income plus means-tested maintenance up to "
            "€855 monthly. Recognized HEI enrollment, performance, "
            "social engagement and member values matter. Overseas "
            "support is possible; member deadlines vary, with no "
            "central application."
        ),
        "evidence": (
            "The 13 BMFTR-supported works are administrators, not 13 "
            "cloned awards. Monthly €300 study-cost allowance is "
            "income-independent; maintenance up to €855/month "
            "considers parental and own income under BAföG alignment. "
            "Nonrepayable and taxfree, duration follows BAföG maximum. "
            "Conditional insurance: health up to €102 and care €35 if "
            "not insured through parents; family €155/month and "
            "childcare €160 per child. Overseas study, language "
            "courses and internships, including beginners abroad, may "
            "receive travel/tuition/overseas supplements. "
            "State-recognized university/FH enrollment and formal "
            "admission required; Abitur not mandatory. "
            "Good-to-very-good performance, no mandatory 1.0, social "
            "engagement, curiosity/exchange and democratic member "
            "values; no exclusive discipline or political-party/church "
            "affiliation. Member emphasis varies. Limited side work is "
            "possible with member advice; no doctoral hourly limit "
            "borrowed. Direct member applications may be fixed or "
            "rolling, not one central/open call. No expressed "
            "exclusive study-host country or citizenship whitelist; "
            "German publisher and BAföG basis do not prove actual "
            "venue. Conditions: https://stipendiumplus.de/studierende/w"
            "as-sollte-ich-mitbringen/ ; https://stipendiumplus.de/stud"
            "ierende/wie-und-wann-bewerbe-ich-mich/ ; "
            "https://stipendiumplus.de/studierende/haeufig-gestellte-fr"
            "agen/"
        ),
    },
    {
        "track": "doctoral-scholarships",
        "key": "doctoral-benefits",
        "title": "Begabtenförderungswerke doctoral scholarships",
        "categories": ["scholarships"],
        "hosts": ["DE"],
        "kind": "programme-overview",
        "summary": (
            "Doctoral scholarships provide €1,750 monthly including "
            "€100 research support, normally for 3.5 years. Any "
            "nationality can apply for a doctorate at a German HEI; "
            "overseas exceptions require German citizenship or an "
            "Abitur obtained in Germany. Strong supervised research "
            "and engagement "
            "matter; member deadlines vary."
        ),
        "evidence": (
            "€1750/month consists of €1650 maintenance plus €100 "
            "research, not €1850; taxfree. Conditional health "
            "insurance up to €100/month, child/overseas support. "
            "Normal maximum 3.5 years, possible 4.5 with children; "
            "birth during funding extends by 12 months and mothers may "
            "request 3 additional maternity months, not an "
            "unconditional combined maximum. Side work alternatives: "
            "up to quarter research/teaching post at "
            "HEI/extra-university research institution OR up to "
            "5h/week outside academia. All disciplines, very good "
            "degree performance without fixed top grade/NC, innovative "
            "scientifically grounded project with reliable "
            "supervision/clear affiliation, engagement/interests and "
            "member values. Prior student funding not required. More "
            "than 4 years since last degree needs justification: "
            "pregnancy/childcare, long illness or caring for relative "
            "with at least Pflegegrad3 may count; ordinary job, "
            "Referendariat or Vikariat do not. No purely "
            "educational-only scholarship, double financial or "
            "ideational funding, or extra degree such as "
            "MBA/psychotherapy training. Prior scholarship counts "
            "toward maximum, university employment does not. Medical "
            "doctorate during study excluded; only after second state "
            "examination. Second PhD generally excluded except "
            "medical-study doctorate followed by scientific doctorate "
            "in another field. Multiple member applications allowed "
            "but only one eventual funder; individual deadlines. Any "
            "nationality for German-HEI doctorate; overseas doctorates "
            "only reasoned exceptions with German citizenship OR "
            "Abitur obtained in Germany, not a DE-only overall "
            "citizenship list. Cotutelle generally possible; known DE "
            "host is not exclusive. Conditions: "
            "https://stipendiumplus.de/promovierende/was-sollte-ich-mit"
            "bringen/ ; https://stipendiumplus.de/promovierende/wie-und"
            "-wann-bewerbe-ich-mich/ ; https://stipendiumplus.de/promov"
            "ierende/haeufig-gestellte-fragen/"
        ),
    },
    {
        "track": "summer-academy-2027",
        "key": "academy-2027",
        "title": (
            "Gemeinsame Sommerakademie 2027 — coexistence and common " "good"
        ),
        "categories": ["training"],
        "hosts": ["DE"],
        "kind": "opportunity",
        "summary": (
            "Training in Bad Staffelstein, Germany, on 5–10 September "
            "2027 explores coexistence, individuality and common good "
            "through interdisciplinary dialogue. For funded members of "
            "the 13 works and SBB; ten funded scholars per Werk. "
            "Programme and registration are expected from mid-February "
            "2027 through each foundation or SBB. No exact opening day "
            "or application deadline is stated; September dates are "
            "the event dates."
        ),
        "evidence": (
            "Actual distinct 2027 edition, not an evergreen duplicate "
            "or invented annual call. Bad Staffelstein near Bamberg "
            "establishes DE host. Event dates 5–10 September2027 are "
            "not application dates; no time-of-day/zone or "
            "precise opening day proved. Programme and registration "
            "are announced for mid-February 2027 through each foundation "
            "or SBB; framework programme follows in July 2027. "
            "Existing funded scholars of 13 "
            "works plus SBB; ten funded scholars per Werk is stated, "
            "without a stated SBB allocation. "
            "sdw and Hanns-Seidel organize "
            "with other works/SBB. Scientific and personal "
            "perspectives, interdisciplinary working groups, "
            "democratic responsibility and plural coexistence. No "
            "common payment amount or nationality condition. Parent "
            "edition inventory: https://stipendiumplus.de/die-werke/gem"
            "einsame-sommerakademie/"
        ),
    },
    {
        "track": "summer-academy-2025",
        "key": "academy-2025",
        "title": (
            "Gemeinsame Sommerakademie 2025 — historical "
            "debate-culture training"
        ),
        "categories": ["training"],
        "hosts": ["DE"],
        "kind": "opportunity",
        "summary": (
            "Historical training edition held in Heidelberg, Germany, "
            "on 17–22 August 2025. Funded members of the 13 works and "
            "SBB explored democratic debate and plural worldviews in "
            "eleven working groups. This is historical event timing, "
            "not a current registration offer or an application "
            "deadline."
        ),
        "evidence": (
            "Actually linked separate 2025 edition, not guessed "
            "archive/evergreen clone. Heidelberg DE, "
            "event17–22August2025, explicitly historical; no invented "
            "closed application state or deadline from past event "
            "dates. Studienstiftung organized with 13 works/SBB, ten "
            "funded members per Werk; the SBB allocation is not stated. "
            "Eleven concrete "
            "participant working groups span democratic "
            "debate/ambiguity, extremism prevention, remembrance, "
            "media/digital change, social partnership/tariff "
            "negotiations, Betzavta/dialogue/political outrage, "
            "working-world debate and courage to speak. Curriculum is "
            "substantive own HTML, not merely a portrait or impact "
            "story; no eleven working-group/provider clones or copied "
            "PDF. No precise registration dates, time/zone, payment or "
            "nationality rule. Parent: https://stipendiumplus.de/die-we"
            "rke/gemeinsame-sommerakademie/"
        ),
    },
    {
        "track": "chin-kobe",
        "key": "chin-kobe",
        "title": "CHIN-KoBe China competence programme",
        "categories": ["training"],
        "hosts": ["DE", "CN", "TW"],
        "kind": "programme-overview",
        "summary": (
            "China competence training for funded members of the 13 "
            "works and SBB, particularly suitable for STEM, economics "
            "and teacher-training students. Modules include language "
            "courses in Bochum, China-in-Europe seminars, a China "
            "study trip and a Taiwan academy. No current module "
            "deadline is published."
        ),
        "evidence": (
            "Hans-Böckler-led funded-member programme; especially "
            "suitable fields are not mandatory exclusive eligibility. "
            "LSI Bochum language courses establish DE, China study "
            "trip CN and Taiwan academy TW as known nonexclusive "
            "module hosts; not every module is hosted in all three and "
            "China-in-Europe does not justify an EU country whitelist. "
            "Language/culture/politics/society and intercultural "
            "professional cooperation. One programme, no "
            "country/module clones or external administrator "
            "catalogue. No own current module date, amount, "
            "nationality or unrestricted public admission."
        ),
    },
    {
        "track": "nie-wieder",
        "key": "nie-wieder",
        "title": (
            "Nie wieder!? — antisemitism awareness and plural society "
            "training"
        ),
        "categories": ["training"],
        "hosts": [],
        "kind": "programme-overview",
        "summary": (
            "ELES-led training invites funded scholars of the 13 works "
            "to multi-day seminars, workshops and reflection on "
            "antisemitism and plural democratic society. Public "
            "evening gatherings are a separate audience. The own "
            "source states no training venue country or current "
            "application deadline."
        ),
        "evidence": (
            "Existing funded scholars of 13 works receive expert "
            "talks, sensitization training, workshops/reflection and "
            "in-person seminars; public evenings allow interested "
            "public/civil society but do not open scholar-only "
            "training to everyone. ELES-led common educational "
            "programme, not a bare external directory. No own venue "
            "country: German publisher/administrator/patron "
            "affiliation is not host evidence. No precise "
            "event/application dates, payment or citizenship "
            "criterion; no external programme catalogue."
        ),
    },
    {
        "track": "aufstiegsstipendium",
        "key": "student-benefits",
        "title": (
            "Aufstiegsstipendium for experienced vocational " "professionals"
        ),
        "categories": ["scholarships"],
        "hosts": [],
        "kind": "programme-overview",
        "summary": (
            "A federal scholarship supports a first academic "
            "higher-education degree for people with vocational "
            "qualifications and several years of practical experience, "
            "full time or alongside work. SBB administers this "
            "separately described programme. Own-source amounts, host "
            "countries and deadlines are unspecified."
        ),
        "evidence": (
            "Own substantive named paragraph establishes actual "
            "federal first-degree funding, not a bare partner "
            "logo/link. Vocational training and several years "
            "practical experience, full time or alongside employment. "
            "SBB cooperation administrator. Shared student-benefits "
            "URL still distinct genuine named identity. Equivalent "
            "apprentice-page paragraph deduplicated: "
            "https://stipendiumplus.de/auszubildende/. No borrowed "
            "undergraduate/doctoral monetary rates, duration, "
            "residence, nationality or host inference, external SBB "
            "collection or admission of all current apprentices "
            "without completed qualifications/experience."
        ),
    },
    {
        "track": "apprentice-scholarships",
        "key": "trainees",
        "title": (
            "Begabtenförderungswerke apprentice scholarships and "
            "educational support"
        ),
        "categories": ["scholarships"],
        "hosts": [],
        "kind": "programme-overview",
        "summary": (
            "Financial and educational support for people in "
            "state-recognized vocational training with social interest "
            "and engagement, across all occupational fields. "
            "Mentoring, workshops, exchange and guidance accompany "
            "member-dependent scholarships or cooperation routes. No "
            "common rates or central deadline are stated."
        ),
        "evidence": (
            "Genuine common funding cohort on substantive own "
            "apprentice page, not an invented central cash entitlement "
            "or 13 directory clones. Current state-recognized "
            "vocational training and social interest/engagement; all "
            "occupations eligible, examples "
            "craft/care/administration/technology are not separate "
            "tracks. Financial and educational support, "
            "mentoring/workshops/exchange/personal guidance; overseas "
            "opportunities possible. Members vary between own "
            "scholarships and cooperation with SBB or "
            "vocational-education providers. Missing common "
            "rate/central application does not erase genuine support; "
            "amount/duration/uniform date unspecified. No "
            "citizenship/ordinary host expressed: German publisher "
            "does not imply hostDE. Do not borrow student/doctoral "
            "rates or distinct Aufstiegsstipendium "
            "completed-vocational/experience/first-degree conditions."
        ),
    },
]


def read_pages():
    return {key: page(url) for key, url in PAGES.items()}


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
                    current = urllib.parse.urljoin(current, headers["Location"])
                    if (
                        original.path == "/robots.txt"
                        and urllib.parse.urlsplit(current).path != "/robots.txt"
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
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
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


def remove_expired_records(records, checked_at):
    """Filter validated deadlines without guessing missing semantics."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    retained = []
    for record in records:
        deadline = record["deadline"]
        if deadline is None:
            retained.append(record)
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing_date = datetime.strptime(deadline, "%Y-%m-%d").date()
            expired = (
                checked.astimezone(timezone(timedelta(hours=-12))).date()
                > closing_date
            )
        else:
            closing = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
            expired = checked > closing
        if not expired:
            retained.append(record)
    return retained


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


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "display_name": "StipendiumPlus",
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
                f"Collected {len(records)} validated StipendiumPlus "
                "funding frameworks and training editions"
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
        validate_records(records)
        records = remove_expired_records(records, checked_at)
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


FAMILY = "youthopps-de-stipendiumplus-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_ID = 378390314
_ARTIFACT_NAME = "stipendium-pacing-state"
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
    "source": "de-stipendiumplus",
    "sha256": "f589dad03a40c20357ec0326817aec70d18ee979ede62fdab109e184286f3b30",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/216#issuecomment-6198500856",
}
_REVIEWED_NATIVE_MIGRATION = {
    "schema": 1,
    "source": "de-stipendiumplus",
    "evidence": "https://github.com/YouthOpps/data-pipeline/issues/216#issuecomment-6200363489",
    "state": {
        "schema": 1,
        "family": "youthopps-de-stipendiumplus-publisher-v1",
        "legacy_hold_history": "unlogged_fields_unknown",
        "research_provenance": {
            "completed": 1792082528.8075395,
            "interval": 12.2,
            "inventory_completed": 1792082528.8075395,
            "known_blocked": False,
            "legacy_429_history": "unknown",
            "legacy_hold_history": "unlogged_fields_unknown",
            "observed_429_since_research": 0,
            "observed_at": 1792081867.0291288,
            "page_gap": 30,
            "public_approval": "https://github.com/YouthOpps/data-pipeline/issues/216#issuecomment-6197156025",
            "rate_limit_until": 0,
            "research_approval_at": 1792082126.5447962,
            "research_routes_limit": 2,
            "scope": "Exactly two fixed research "
            "GETs: literal robots then the "
            "named content URL; not native "
            "or runtime/production clearance",
            "starts": [
                1791514759.0,
                1791514925.0,
                1791560087.0,
                1791591605.0,
                1791622904.0,
                1791653762.0,
                1791658301.0,
                1791676219.0,
                1791710278.0,
                1791745893.0,
                1791763079.0,
                1791798261.0,
                1791843507.0,
                1791884716.0,
                1791923137.0,
                1791935639.0,
                1791964882.0,
                1791967877.0,
                1791986503.0,
                1792004005.0,
                1792020414.0,
                1792046542.0,
                1792050666.0,
                1792060886.0,
                1792061816.0,
                1792079456.0,
                1792082496.5014098,
                1792082527.5133653,
            ],
            "until": 1792082496.5006566,
        },
        "starts": [
            1791514759,
            1791514925,
            1791560087,
            1791591605,
            1791622904,
            1791653762,
            1791658301,
            1791676219,
            1791710278,
            1791745893,
            1791763079,
            1791798261,
            1791843507,
            1791884716,
            1791923137,
            1791935639,
            1791964882,
            1791967877,
            1791986503,
            1792004005,
            1792020414,
            1792046542,
            1792050666,
            1792060886,
            1792061816,
            1792079456,
            1792082496.5014098,
            1792082527.5133653,
            1792086993.2399786,
            1792086993.496219,
            1792087006.5118136,
            1792087006.6920593,
            1792087020.3238657,
            1792087020.5342987,
            1792089843.1857624,
            1792089856.4928813,
            1792089869.6885421,
            1792089869.7400453,
            1792089883.1124878,
            1792089883.155881,
            1792089896.3897212,
            1792089896.5967638,
            1792089910.0584393,
            1792089910.2175121,
            1792089926.4943104,
            1792089926.6102955,
            1792089939.8077438,
            1792089939.8474839,
            1792089952.7233448,
            1792089952.8450143,
            1792089966.0831516,
            1792089966.27119,
            1792089979.6387126,
            1792089979.8276992,
            1792089992.832247,
            1792089993.021899,
            1792090023.9668984,
            1792090024.0071468,
            1792090037.3066807,
            1792090037.34857,
            1792090050.3226926,
            1792090050.3446527,
            1792090063.2736957,
            1792090063.3211658,
            1792090076.2996578,
            1792090076.3715873,
            1792090089.4386885,
            1792090089.5153067,
            1792090102.6971388,
            1792090102.8943481,
            1792090116.0470374,
            1792090116.0937877,
            1792090128.9284863,
            1792090128.9742432,
            1792090159.9517882,
            1792090160.0214148,
            1792090191.0388677,
            1792090191.236112,
            1792091425.689836,
            1792091439.9647868,
            1792091452.9694278,
            1792091453.1504903,
            1792091466.5554798,
            1792091466.7465131,
            1792091479.756487,
            1792091479.9525201,
            1792091493.0583029,
            1792091493.1319532,
            1792091506.270597,
            1792091506.544834,
            1792091519.2279506,
            1792091519.2695887,
            1792091532.1193244,
            1792091532.308803,
            1792091545.4445724,
            1792091545.4824023,
            1792091558.3299117,
            1792091558.4023988,
            1792091571.280499,
            1792091571.2989247,
            1792091601.7627087,
            1792091601.802433,
            1792091614.9695187,
            1792091614.9884703,
            1792091627.8896677,
            1792091627.9187925,
            1792091641.058172,
            1792091641.1839824,
            1792091654.5237896,
            1792091655.240606,
            1792091668.4803452,
            1792091668.8749545,
            1792091683.3765395,
            1792091683.922027,
            1792091697.8570952,
            1792091697.9542644,
            1792091710.9402978,
            1792091711.0828295,
            1792091741.6748395,
            1792091741.8222334,
            1792091773.4411523,
            1792091774.065331,
        ],
        "interval": 12.2,
        "until": 1792091425.6895905,
        "page_gap": 30,
        "completed": 1792091775.5285249,
        "inventory_completed": 1792091775.5285249,
        "blocked": False,
        "legacy_429_history": "unknown",
        "observed_429_since_transition": 0,
        "rate_limit_until": 0,
        "observed_at": 1792091775.5285249,
    },
    "state_sha256": "633cd86e7f449db6263ae663e72b814deb92972ab67bf65d0ceb80b3daa9c9da",
}
_LEGACY_HEADS = {
    38763419441: "f77eed642c4544bbeed009440c406064a4f5c6ad",
    38721259482: "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
    38720083793: "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
    38700510893: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38684236085: "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
    38653837611: "e1a68725fe923582b5640481e29c7166eb77397c",
    38618605220: "92235b48ad33b9fa377e0736b689010173d7bfab",
    38576438250: "92235b48ad33b9fa377e0736b689010173d7bfab",
    38534756461: "5ab9b0237b7fa2b043fe276ec43f29e493c685ab",
    38531894521: "02792bda730b08eeb1eda86a2cd71af80af3cef7",
    38486627081: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38461968437: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38378235161: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38315360853: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38231517230: "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
    38186063569: "10d99f8ef021c092851bf189ff67307865b8760c",
    38166770863: "10d99f8ef021c092851bf189ff67307865b8760c",
    38127705412: "10d99f8ef021c092851bf189ff67307865b8760c",
    38096238126: "10d99f8ef021c092851bf189ff67307865b8760c",
    38077251090: "a97e28475634b9ff61489a11cd6378c9dafc67ae",
    38071840293: "9b155b179283c8fa08cf56c1a83a54bd19d51814",
    38039511417: "c55b8d99378274467f2d9a290967dbe10bc25031",
    38008389254: "029a500dbf22c1d44ae592c667f26f33d9d364c8",
    37952434991: "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
    37876969199: "2949f02832f81f132d9e802f0aa2b673b7d1680b",
    37876948677: "2949f02832f81f132d9e802f0aa2b673b7d1680b",
}
_LEGACY_JOB_DIGESTS = {
    38763419441: "e43a1bc4eeffe8cb89e461eb6344cbf112fe3e2c9687803d0202e2fa903d13cb",
    38721259482: "7ceec010c8413bf8e41280eca50c9e5eebad0363c15af092eae5eb085f0c1b56",
    38720083793: "c925eb8630d0635e806c5f340bd35d282ad00b79064e29847ea8bea0fa5f0d69",
    38700510893: "8a6911ee8d0152dd2e05677fa8b78d61b02ae7a04268899ea40cbcdeada798ad",
    38684236085: "76abbfa0533ff14394049a9d6895b278d5ded510de287fad10095c9e13225c9e",
    38653837611: "fd8db11948230a1133ce225dd5387ec34c91bbb2b6664da5fae990eee8810c9b",
    38618605220: "7ba06d301d40614096e35435dcec9b55699c64a1dc68bc6e6843caacc38d6849",
    38576438250: "271579ca0a897e94ba05f816dea4afe1b30e8620d883c7b8965a831d99304cc9",
    38534756461: "5207b40d5f622f7756e75acd4055f143bd1e55e5b57bf2bb6d35e8ca9a826794",
    38531894521: "33fe944d5fa8f58838adceb092f8dd34a78565b0224361755963df35941e3bf2",
    38486627081: "b1d90b016fd5e5d937e1c688f1132daca59527e1b758f32883ce34d2a1fcd621",
    38461968437: "53793b145f9e6673a5ce8bdeda8ad809341094eaf95755e55d6f7fabb19ff721",
    38378235161: "bfe2a0ab7889996192cbe457edc0ffb7ba145c166fe08652117168b458c6f376",
    38315360853: "5d776446022f3ef44a8c92f090a9f8d68a000130aadbe1ae37d827498eeabfc6",
    38231517230: "fe8a555b43e0de44ff7a6efb850340f83043bfe3c06c2aa11e3dbf5fdd2d3e22",
    38186063569: "77b5bba16dbd4cf7b332e5cbbc409af25615dab4955916848a48601ed1edeb71",
    38166770863: "6eddefaba8006b97881efed1bb4541edec994d1260c485e2a52ff2aeef19cf9d",
    38127705412: "7b497a528352b67b34a19b47224d53c2b36c37b1a7afc41074813c24017e46ca",
    38096238126: "6057a23013bfbe2ae0ecb4849933eb5cc12ae40d6ff5a77dbb5fb227d2003e55",
    38077251090: "5ad420b9142e0a39e0032ccd55a3237bb4c0cb01ff5d270edaccd18e7fb0102b",
    38071840293: "7bba0b5de90054629a542ea4a2d3bd7df68b4480d0da500f28a4e67c76c06c67",
    38039511417: "072c70a765d76c7e87c71e10233ba81c2016fa8c44f6edd21e19a6b323198105",
    38008389254: "231db7c5ffeb9f9c4947613edf078fb54ed8b5dd9de5eea8867efc36596638ba",
    37952434991: "588353b57d28b81d2257a1a02b75061425c81d8c41326c184db466046174be0d",
    37876969199: "d83f48cc7fe77b8c1ca4c479639908561fcd7b6272f46bd69f85bf12925e1d4b",
    37876948677: "be07e68e77eb3fd88a69d9c6e18993ac747dd0da39919a8798493253f046180b",
}
_INVENTORY_URLS = {
    PAGES[key]
    for key in ("academy", "doctoral", "home", "students", "trainees", "works")
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
                r"issues/216#issuecomment-[0-9]+"
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
    global _STATE_PATH, _STATE_LOCK
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    _STATE_PATH = _STATE_LOCK = os.path.join(
        directory, hashlib.sha256(b"stipendiumplus.de").hexdigest()
    )



def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    flags = os.O_RDWR | os.O_NOFOLLOW
    if os.environ.get("GITHUB_ACTIONS") == "true" and not _RESTORED:
        try:
            fd = os.open(_STATE_PATH, flags | os.O_CREAT | os.O_EXCL, 0o600)
            _JOURNAL_NEW = True
        except FileExistsError:
            fd = os.open(_STATE_PATH, flags)
            _JOURNAL_NEW = False
    else:
        fd = os.open(_STATE_PATH, flags)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    file = os.fdopen(fd, "r+")
    fcntl.flock(file, fcntl.LOCK_EX)
    return file


def load_budget():
    with open(_STATE_PATH, "rb") as file:
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher journal", "access")
    if (
        not raw
        and _JOURNAL_NEW
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and not _RESTORED
    ):
        return empty_budget(time.time())
    try:
        value = json.loads(raw)
        if isinstance(value, dict) and "schema" not in value:
            value = migrate_local_research(value, raw)
        return validate_budget(value)
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher journal", "access") from None


def write_budget(state):
    """Retain the existing legacy journal inode under its original flock."""
    state = validate_budget(state)
    fd = os.open(_STATE_PATH, os.O_WRONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "w") as file:
        file.seek(0)
        json.dump(state, file, separators=(",", ":"))
        file.truncate()
        file.flush()
        os.fsync(file.fileno())


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
    publisher = host == "stipendiumplus.de" or host.endswith(
        ".stipendiumplus.de"
    )
    if publisher and (not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        raise AdapterError(
            "Trusted publisher state required before access", "access"
        )
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    lock, state = pace(interval) if publisher else (None, None)
    try:
        if publisher:
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
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        with response:
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
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = state["completed"]
                try:
                    write_budget(state)
                except BaseException:
                    _STATE_TRUSTED = False
                    _PUBLISHER_GATE_FAILED = True
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
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(tempfile.gettempdir(), SOURCE_ID + ".lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            raise AdapterError("Unsafe lifecycle lock", "access")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise AdapterError(
                "Another publisher collector is active", "access"
            ) from None
        _COLLECTION_LOCK_FD = fd
    if _STATE_TRUSTED and os.environ.get("GITHUB_ACTIONS") == "true":
        return
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
        if _COLLECTION_LOCK_FD is not None:
            try:
                os.close(_COLLECTION_LOCK_FD)
            except OSError:
                pass
            _COLLECTION_LOCK_FD = None


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
            r"https://github\.com/YouthOpps/data-pipeline/issues/216#issuecomment-[0-9]+",
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


def workflow_api(path):
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
            "/actions/workflows/fetch-de-stipendiumplus.yml/runs"
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
    with open(__file__, "rb") as stream:
        content = stream.read(5_000_001)
    if len(content) > 5_000_000:
        raise AdapterError(
            "Source implementation exceeds reviewed bound", "access"
        )
    return hashlib.sha256(content).hexdigest()


def invalidate_family_artifact():
    """Remove only an owned stale output after publisher persistence failed."""
    path = os.environ.get("STIPENDIUM_PACING_ARTIFACT_PATH")
    if not path:
        return
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
    stale = False
    try:
        info = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe stale artifact output", "access")
        stale = True
    descriptor = os.open(
        os.environ["GITHUB_OUTPUT"], os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
    )
    with os.fdopen(descriptor, "a") as output:
        info = os.fstat(output.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise AdapterError("Unsafe workflow output signal", "access")
        stale_descriptor = None
        if stale:
            stale_descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
            stale_info = os.fstat(stale_descriptor)
            if (
                not stat.S_ISREG(stale_info.st_mode)
                or stale_info.st_uid != os.getuid()
                or stale_info.st_nlink != 1
            ):
                os.close(stale_descriptor)
                raise AdapterError("Unsafe stale artifact output", "access")
        try:
            if stale_descriptor is not None:
                os.ftruncate(stale_descriptor, 0)
                os.fsync(stale_descriptor)
            output.write("pacing_ready=false\n")
            output.flush()
            os.fsync(output.fileno())
        finally:
            if stale_descriptor is not None:
                os.close(stale_descriptor)
                os.unlink(path)


def export_family_artifact():
    """Atomically export trusted state and signal its availability."""
    path = os.environ.get("STIPENDIUM_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    run_id, attempt = current_run_identity()
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
            json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "state": state,
    }
    if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
        raise AdapterError("Invalid artifact source revision", "access")
    fd, temporary = tempfile.mkstemp(prefix=".stipendium-pacing-", dir=root)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(envelope, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        descriptor = os.open(
            os.environ["GITHUB_OUTPUT"],
            os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW,
        )
        with os.fdopen(descriptor, "a") as output:
            info = os.fstat(output.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
            ):
                raise AdapterError("Unsafe workflow output signal", "access")
            output.write("pacing_ready=true\n")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore_family_artifact():
    """Require exact history for the reviewed legacy state handoff."""
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
            latest != (38763419441, 1, SOURCE_ID)
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
                        r"issues/216#issuecomment-[0-9]+"
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
    with locked_budget():
        local = load_budget()
        combined = merge_budget(local, incoming)
        combined["until"] = max(combined["until"], time.time() + 60)
        write_budget(combined)
    _RESTORED_LATEST = latest
    _RESTORED_HISTORY = (
        copy.deepcopy(_SCANNED_HISTORY),
        copy.deepcopy(_SCANNED_JOB_DIGESTS),
        copy.deepcopy(_ATTEMPT_HEADS),
    )
    _RESTORED = _STATE_TRUSTED = True



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
