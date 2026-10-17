"""Collect attributed noncommercial WBI funding opportunities."""

import argparse
import copy
import email.utils
import errno
import io
import math
import socket
import ssl
import stat
import zipfile
import base64
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
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_ID = "be-wbi"
SOURCE_URL = "https://www.wbi.be/en/bourses"
WEBSITE_URL = "https://www.wbi.be/"
LANGUAGE = "fr"
PUBLISHER_COUNTRY = "BE"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Wallonie-Bruxelles International (WBI) – www.wbi.be"


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




def content(root):
    return one(
        [
            n
            for n in nodes(root, "div", "field--name-body")
            if not nodes(n, "a", "printButton")
        ],
        "WBI factual article",
    )


def source_facts(key, root):
    if key.startswith("listing") or key.startswith("category"):
        cards = nodes(root, class_name="search-result")
        if not cards:
            raise AdapterError(
                "Missing complete WBI programme inventory", "parse"
            )
        links = sorted(
            {
                n.attrs.get("href", "")
                for card in cards
                for n in nodes(card, "a")
                if n.attrs.get("href")
            }
        )
        pagers = sorted(
            {
                n.attrs.get("href", "")
                for nav in nodes(root, "nav", "pager")
                for n in nodes(nav, "a")
                if n.attrs.get("href")
            }
        )
        return json.dumps([links, pagers], ensure_ascii=False)
    text = (
        one(nodes(root, "h1"), "WBI article title").text()
        + " "
        + content(root).text()
    )
    text = re.sub(
        r"Last edited il y a \d+ (?:minutes?|heures?|jours?)", "", text
    )
    return " ".join(text.split())


PAGES = {
    "louisiana-call": (
        "https://www.wbi.be/fr/actualites/appel-candidatures-devenir-"
        "formateurtrice-enseignante-louisiane"
    ),
    "circ-call": (
        "https://www.wbi.be/fr/actualites/offre-stage-centre-internat"
        "ional-recherche-cancer-circ"
    ),
    "leaf17": (
        "https://www.wbi.be/fr/aide-service/realiser-stage-entreprise"
        "-letranger"
    ),
    "leaf16": (
        "https://www.wbi.be/fr/aide-service/obtenir-bourse-specialisa"
        "tion-letranger-wbi"
    ),
    "leaf15": (
        "https://www.wbi.be/fr/aide-service/obtenir-bourse-effectuer-"
        "sejour-recherche-letranger-wbi"
    ),
    "leaf14": (
        "https://www.wbi.be/fr/aide-service/obtenir-bourse-dexcellenc"
        "e-suivre-formation-haut-niveau-wallonie-ou-bruxelles"
    ),
    "leaf13": (
        "https://www.wbi.be/fr/aide-service/obtenir-bourse-cadre-du-p"
        "rojet-pilote-entre-wbi-centre-international-recherche-cancer"
    ),
    "leaf12": (
        "https://www.wbi.be/fr/aide-service/etre-soutenu-votre-partic"
        "ipation-aux-initiatives-programmes-europeens-financement"
    ),
    "leaf11": (
        "https://www.wbi.be/fr/aide-service/enterprise-europe-network"
        "-votre-porte-dentree-partenariats-europeens"
    ),
    "leaf10": (
        "https://www.wbi.be/fr/aide-service/effectuer-stage-organisat"
        "ion-internationale-intergouvernementale-dinteret-public"
    ),
    "leaf9": (
        "https://www.wbi.be/fr/aide-service/devenir-formatrice-ou-for"
        "mateur-enseignante-louisiane-wbi"
    ),
    "leaf8": (
        "https://www.wbi.be/fr/aide-service/devenir-auxiliaire-conver"
        "sation-langue-francaise-letranger-wbi"
    ),
    "leaf7": (
        "https://www.wbi.be/fr/aide-service/beneficier-dune-formation"
        "-droit-international-lacademie-droit-international-haye"
    ),
    "leaf6": (
        "https://www.wbi.be/fr/aide-service/beneficier-dun-soutien-vo"
        "s-projets-domaines-recherche-linnovation"
    ),
    "leaf5": (
        "https://www.wbi.be/fr/aide-service/beneficier-dun-soutien-pr"
        "ojets-cooperation-scientifique-france-programme-hubert-curie"
        "n"
    ),
    "leaf4": (
        "https://www.wbi.be/fr/aide-service/beneficier-dun-soutien-fr"
        "ais-mobilite-lors-dune-manifestation-allemagne-france-grand"
    ),
    "leaf3": (
        "https://www.wbi.be/fr/aide-service/beneficier-du-fonds-mobil" "ite-wbi"
    ),
    "leaf2": (
        "https://www.wbi.be/fr/aide-service/apprendre-ou-se-perfectio"
        "nner-langue-etrangere-wbi"
    ),
    "leaf1": (
        "https://www.wbi.be/fr/aide-service/apprendre-metiers-lies-le"
        "xportation-au-developpement-business-linternational-explort"
    ),
    "leaf0": "https://www.wbi.be/fr/WBI-World",
    "category847": "https://www.wbi.be/en/taxonomy/term/847",
    "category846": "https://www.wbi.be/en/taxonomy/term/846",
    "category844": "https://www.wbi.be/en/taxonomy/term/844",
    "category843": "https://www.wbi.be/en/taxonomy/term/843",
    "legal": "https://www.wbi.be/fr/mentions-legales-0",
    "listing2": "https://www.wbi.be/en/bourses?page=1",
    "listing": "https://www.wbi.be/en/bourses",
    "entry": (
        "https://www.wbi.be/fr/actualites/offre-bourse-stage-entrepri"
        "se-etudiants"
    ),
}
CONDITIONS = {
    "louisiana-call": "498830cc23309429651a6545e25007cf297c882d4430a0419bccbbf282ef22f9",
    "circ-call": "af6ddc24951880b08181493ea88089ca00aad25742c912a873293cb8fb85e8d7",
    "leaf17": "63fd26abacff59225f4c547a355b699d64264d04730c38e9cf916c8299fdf345",
    "leaf16": "182d40518ec56dca9e81e13562ee5c3ee073a51d8e9bdd3ed6fe840adbb2d3f1",
    "leaf15": "c75acb19c583508598ebaa310f9f625ee7c0cb93f0c5776ec912860c5cb04a8b",
    "leaf14": "53d6b18ddc9e7cbe7b5d992f3d1e93f9578b2a3829c7d19d745ddde36f8265fe",
    "leaf13": "92dd452fa99f6ba64f114625343c5cec0c9afd604910f5634f184ae433c1aab4",
    "leaf12": "01cb89d55426de2d9aaaa8569ca1ecb7194e8a22aa746f3c6ae27ebc06f736d0",
    "leaf11": "e71080f16fadf02a7bfe2b543a44ecc760184c18d6a280766f01067886c6c2db",
    "leaf10": "b5d998a8679978272517360c6939d622759931008b273d9a22c0257c1d0972df",
    "leaf9": "82febbe3c945596b7192d20a8648e7757500e0b40e38777e61dfc00c032ed171",
    "leaf8": "cb0c937be88da27233a64173d38bba017d6e7089ca406d7068addea9f3cde9aa",
    "leaf7": "296cc74c8c8934df0ed680d55dae07d488cf103498436fc6933290109770f2ef",
    "leaf6": "55dcf38a2239387d1ba6ecf64334aea7af5e372024e8d29efa7939f0df821376",
    "leaf5": "b3af3d49f8f8e5befd00cd9b782950f40ab82e1a627816f6195ff9a106e9670e",
    "leaf4": "a5eb1c8b0d6cdd9d43964a576e1790940d38f36066a4f97843ff268bffec3d33",
    "leaf3": "9de10449e97f7d979dd771f7136025ec8df6336b53eb69333f641f411821f0a3",
    "leaf2": "213d4623aa0b7b952b4c0079c3c30bd2e672b4f865bc1d15380f883257537cae",
    "leaf1": "0df63e3d292e0b9dc324bb3608506be0f3d9c1cc05d6d88f5e9399fef6c5a390",
    "leaf0": "df00f9cf5f33a283f2848ecc9872cd666d01236a79cbd2b6f592d75861d2f1db",
    "category847": "a566b5b255d1097afc38fc3b4d6c4cee59328028e1fb9e599e1250ed30c56928",
    "category846": "16dc4e37d861f8e4bbc9703dddec9606be9cf80d0315dfe8e92c64b1dcd45a85",
    "category844": "655504070f0097104dcc9167087bf195897ba900f1fa1539b83b78b7be7a14dd",
    "category843": "16dc4e37d861f8e4bbc9703dddec9606be9cf80d0315dfe8e92c64b1dcd45a85",
    "legal": "562a601d3df8c4d6127959f72fc51504c3435a2768d00073d76789ab1f9a91b7",
    "listing2": "d3dd8a41b1b73c7e60409fe8127847c400b8bf45d96e3316b8735f4a716b3d92",
    "listing": "6e3ed3826a25d77af1829ca45b42bf3cbde8addbbaff82cdd7cc3cef75e68102",
    "entry": "943ddb44c6ff4cd09196361cb8fe89a04cee05b2542009a6ed5c9de8fa3809b9",
}

PROFILES = [
    {
        "track": "world-doctoral",
        "key": "leaf0",
        "title": "WBI Excellence WORLD — doctoral",
        "category": "scholarships",
        "summary": "At least one year, renewable three times. FWB master (120 "
        "credits), or foreign degree with current FWB university "
        "affiliation. €2,120/month mobility allowance, not salary; "
        "return travel and conditional doctoral registration fees up "
        "to €835. Maximum three applications.",
        "evidence": "Postdoctorate normally within five years; parenthood "
        "extensions: 15 months per childbirth, 12 months for "
        "biological fathers/adoption/long-term foster placement. "
        "One doctoral and one postdoctoral long award lifetime "
        "entitlement. FNRS co-tutelle mobility allowance suspended "
        "during return to Belgium. Annual February 1 (UTC+1), or "
        "short October 1/April 1 (UTC+2), without an actual year or "
        "closing clock.",
        "hosts": [],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "world-postdoctoral",
        "key": "leaf0",
        "title": "WBI Excellence WORLD — postdoctoral",
        "category": "scholarships",
        "summary": "At least one year, renewable once. FWB doctorate, or "
        "foreign degree with current FWB university affiliation. "
        "€2,120/month mobility allowance, not salary; return travel "
        "and conditional postdoctoral training fees up to €835. "
        "Maximum three applications.",
        "evidence": "Postdoctorate normally within five years; parenthood "
        "extensions: 15 months per childbirth, 12 months for "
        "biological fathers/adoption/long-term foster placement. "
        "One doctoral and one postdoctoral long award lifetime "
        "entitlement. FNRS co-tutelle mobility allowance suspended "
        "during return to Belgium. Annual February 1 (UTC+1), or "
        "short October 1/April 1 (UTC+2), without an actual year or "
        "closing clock.",
        "hosts": [],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "world-short",
        "key": "leaf0",
        "title": "WBI Excellence WORLD — short research",
        "category": "scholarships",
        "summary": "One to three months; three years between repeat stays. FWB "
        "master/doctorate, or foreign degree with current FWB "
        "university affiliation. €2,120/month mobility allowance, "
        "not salary; return travel and conditional fee support. "
        "Maximum three applications.",
        "evidence": "Postdoctorate normally within five years; parenthood "
        "extensions: 15 months per childbirth, 12 months for "
        "biological fathers/adoption/long-term foster placement. "
        "One doctoral and one postdoctoral long award lifetime "
        "entitlement. FNRS co-tutelle mobility allowance suspended "
        "during return to Belgium. Annual February 1 (UTC+1), or "
        "short October 1/April 1 (UTC+2), without an actual year or "
        "closing clock. Registration/training fee support up to "
        "€835 is tied to the published doctoral/postdoctoral award "
        "conditions, not a universal short-stay benefit.",
        "hosts": [],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "explort",
        "key": "leaf1",
        "title": "EXPLORT international business training",
        "category": "training",
        "summary": "Joint AWEX/Forem training, placement in a Belgian business "
        "and mission abroad for students or jobseekers with suitable "
        "qualifications or relevant experience and language skills. "
        "International business interest required. Funding amount "
        "and dated application deadline are not specified.",
        "evidence": "",
        "hosts": [],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "language",
        "key": "leaf2",
        "title": "WBI partner language immersion scholarships",
        "category": "scholarships",
        "summary": "Usually one month of language immersion. Normally at least "
        "two years of FWB higher education; Czech Republic allows "
        "one. WBI funds return travel; partners cover tuition, "
        "accommodation and meals. Other public funding cannot be "
        "combined. Destination-specific conditions apply; no dated "
        "call is established.",
        "evidence": "One programme across twelve explicit destinations; "
        "obsolete linked service alias returns 404 and is not used.",
        "hosts": [
            "BG",
            "EE",
            "GR",
            "HU",
            "LV",
            "LT",
            "MA",
            "PL",
            "CZ",
            "SK",
            "TN",
            "TR",
        ],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "mobility-out",
        "key": "leaf3",
        "title": "WBI institutional Mobility Fund — outgoing",
        "category": "grants",
        "summary": "Grant submitted by and paid to an FWB higher education "
        "institution for eligible permanent academic, scientific or "
        "administrative staff travelling to partner institutions. "
        "International travel and essential transfers covered; FNRS "
        "qualified researchers and discretionary exceptions may "
        "qualify.",
        "evidence": "Normally full/part-time indefinite employment; students, "
        "doctoral students, emeritus, non-permanent scientific and "
        "temporary staff excluded. Annual April 10/October 9 have "
        "no published call year. Institutional applicant, not an "
        "individual grant.",
        "hosts": [
            "AR",
            "BR",
            "CL",
            "CO",
            "CU",
            "MX",
            "UY",
            "AU",
            "KH",
            "KR",
            "JP",
            "IN",
            "ID",
            "LA",
            "MY",
            "NZ",
            "PH",
            "CN",
            "SG",
            "TW",
            "TH",
            "VN",
            "DZ",
            "LB",
            "MA",
            "TN",
        ],
        "deadline": None,
        "kind": "institutional-grant",
        "eligible": [],
    },
    {
        "track": "mobility-in",
        "key": "leaf3",
        "title": "WBI institutional Mobility Fund — incoming",
        "category": "grants",
        "summary": "FWB institution applies and receives funding to host "
        "academic, scientific or administrative staff from "
        "accredited Maghreb/Lebanon partner institutions. "
        "Accommodation up to €100/night and subsistence up to "
        "€56/day plus local public transport, for at most 30 days. "
        "International flights excluded.",
        "evidence": (
            "Incoming foreign staff are beneficiaries, not direct grant "
            "applicants. Outgoing indefinite FWB employment conditions "
            "are not imposed as incoming citizenship requirements. "
            "Annual April 10/October 9 have no established call year."
        ),
        "hosts": ["BE"],
        "deadline": None,
        "kind": "institutional-grant",
        "eligible": [],
    },
    {
        "track": "nearby",
        "key": "leaf4",
        "title": "WBI nearby-country cultural mobility support",
        "category": "grants",
        "summary": "Travel support for a manifestation in Germany, France, "
        "Luxembourg or the Netherlands. Individuals/entities "
        "domiciled in Wallonia or Brussels, or a foreign hosting "
        "entity, may apply. French cultural activities require joint "
        "design with CWB Paris. Second-class travel or capped "
        "€0.3751/km reimbursement; funding conditions apply.",
        "evidence": "Relative application windows are not evidence of a dated "
        "open call.",
        "hosts": ["DE", "FR", "LU", "NL"],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "tournesol",
        "key": "leaf5",
        "title": "PHC Tournesol scientific cooperation grant",
        "category": "grants",
        "summary": "An FWB university-led research team and French partner "
        "jointly submit a cooperation project; higher education "
        "co-promoters may participate. Mobility grant up to €2,000 "
        "per year for two years; short missions typically one to two "
        "weeks per year.",
        "evidence": "Lodging up to €200 per night and food/local travel up to "
        "€50 per calendar day under published reimbursement "
        "conditions. Both sides must submit their application. "
        "Funding supports institutional cooperation, not a "
        "citizenship-based personal scholarship.",
        "hosts": ["FR"],
        "deadline": None,
        "kind": "institutional-grant",
        "eligible": [],
    },
    {
        "track": "hague",
        "key": "leaf7",
        "title": "WBI Hague Academy international law scholarships 2026",
        "category": "training",
        "summary": "Three annual scholarships for one three-week public or "
        "private international law course. At least four university "
        "study years or a suitable three-year law degree; English or "
        "French proficiency and some study at an FWB-funded "
        "university required. Tuition, capped travel and housing "
        "contribution covered.",
        "evidence": "Courses July 6–24 and July 27–August 14, 2026. Apply by "
        "January 31, 2026. European travel capped at €250; three "
        "available places are one programme, not three records.",
        "hosts": ["NL"],
        "deadline": "2026-01-31",
        "kind": "opportunity",
        "eligible": [],
    },
    {
        "track": "assistants",
        "key": "leaf8",
        "title": "WBI French conversation assistant placements",
        "category": "jobs",
        "summary": "Paid French conversation assistant placements: host salary "
        "or stipend plus WBI travel. Normally under 35 and at least "
        "one year FWB residence; degree and language requirements "
        "vary by destination. No Belgian citizenship restriction is "
        "inferred.",
        "evidence": "Italy under 30; Switzerland 21–30 or under 35 with second "
        "qualification. Spain permits a foreign-degree route; no "
        "2026–27 Catalonia/Andalusia posts. Annual October–February "
        "15 is yearless. Native French or certified C2; FWB "
        "bachelor/master or completed three study years with degree "
        "proof before departure. Host language: UK/Ireland B1+, "
        "German-speaking Switzerland B1, Spain/Germany A2+, Italy "
        "A1+, Austria A2, Taiwan Chinese A1+ and English B1+. "
        "Germany requires measles vaccination/immunity and relevant "
        "experience; Switzerland future teaching interest and "
        "pedagogical experience. WBI travel cap €1,200 Taiwan/€250 "
        "others; Spain travel support only first20 selected, "
        "prioritising first-time assistants. Stays six to eleven "
        "months; one supported renewal in same country. Criminal "
        "record required before arrival.",
        "hosts": ["AT", "IE", "CH", "DE", "ES", "IT", "GB", "TW"],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "louisiana",
        "key": "leaf9",
        "title": "WBI teaching placements in Louisiana — current call",
        "category": "jobs",
        "summary": "Ten-month Louisiana teaching placements, renewable twice "
        "with exceptional longer renewals. Teaching qualification, "
        "27 months full-time teaching, current teaching post, French "
        "C2+ and English B1+ required. Application deadline December "
        "31, 2026; interviews in late January 2027.",
        "evidence": "Residence conflict: programme requires at least one year "
        "FWB residence, current article says Belgium; preserve both "
        "and seek publisher clarification. Article quotes 2026–27 "
        "salary stages USD52,443/53,632/53,665/54,583/55,602 and "
        "conditional USD6,000/4,000/4,000 bonuses; not a verified "
        "2027–28 schedule. Raw before midnight has no time zone. "
        "December 1 event registration and December 8 information "
        "event are not award deadlines.",
        "hosts": ["US"],
        "deadline": "2026-12-31",
        "kind": "opportunity",
        "eligible": [],
    },
    {
        "track": "oi",
        "key": "leaf10",
        "title": "WBI intergovernmental organisation internships",
        "category": "internships",
        "summary": "One-to-six-month funded internships for master students or "
        "final-year bachelor students or recent FWB graduates within "
        "two years, at most 30 at placement start. €1,200–1,600 "
        "monthly support and travel up to €500. Previous "
        "organisation experience over six weeks excluded.",
        "evidence": "Other support cannot normally be combined; social "
        "allowance exceptions apply. Annual October 15/April 15 "
        "lack a call year. Nonexhaustive organisation directory is "
        "not a host-country whitelist. NGOs are excluded; the host "
        "must be an actual intergovernmental organisation of public "
        "interest.",
        "hosts": [],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "circ-postdoc",
        "key": "leaf13",
        "title": "WBI CIRC pilot postdoctoral scholarship",
        "category": "scholarships",
        "summary": (
            "Research in Lyon for at least one year, renewable once. FWB "
            "doctorate or foreign degree with current FWB university "
            "affiliation; normally within five years of doctorate, "
            "extended one year per childbirth/adoption. €3,000/month "
            "mobility scholarship, not salary; return travel and "
            "conditional insurance/training."
        ),
        "evidence": "Maximum three applications and one award with renewal. "
        "February 1 UTC+1 is yearless; not the distinct 2027 SEE "
        "internship.",
        "hosts": ["FR"],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "excellence-in-long",
        "key": "leaf14",
        "title": "WBI Excellence IN — long postdoctoral",
        "category": "scholarships",
        "summary": "At least one year, renewable once. Foreign doctorate "
        "required, or pending doctorate with timely proof, for "
        "research at an FWB higher education institution. At most 12 "
        "months in Belgium during the previous three years. "
        "€2,120/month mobility allowance, not salary, plus return "
        "travel and insurance.",
        "evidence": "Maximum three applications; incompatible with FNRS "
        "funding. Long award only once with renewal; short repeat "
        "entitlements differ. Annual February 1 or October 1/April "
        "1 without actual year. July 15/December 1/July 1 pending "
        "doctorate proof dates are not application deadlines.",
        "hosts": ["BE"],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "excellence-in-short",
        "key": "leaf14",
        "title": "WBI Excellence IN — short postdoctoral",
        "category": "scholarships",
        "summary": (
            "One to three months, with three years between repeat stays. "
            "Foreign doctorate required, or pending doctorate with "
            "timely proof, for research at an FWB higher education "
            "institution. At most 12 months in Belgium during the "
            "previous three years. €2,120/month mobility allowance, not "
            "salary, plus return travel and insurance."
        ),
        "evidence": "Maximum three applications; incompatible with FNRS "
        "funding. Long award only once with renewal; short repeat "
        "entitlements differ. Annual February 1 or October 1/April "
        "1 without actual year. July 15/December 1/July 1 pending "
        "doctorate proof dates are not application deadlines.",
        "hosts": ["BE"],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "research",
        "key": "leaf15",
        "title": "WBI bilateral research stay scholarships",
        "category": "scholarships",
        "summary": "One-to-six-month research, doctoral/postdoctoral "
        "specialisation or institutional partnership stays. FWB "
        "higher education graduates or final-year students "
        "completing their qualification before departure may apply. "
        "Partner-specific funding and conditions apply; no actual "
        "dated call established.",
        "evidence": "",
        "hosts": [
            "BG",
            "EG",
            "EE",
            "HU",
            "IL",
            "LV",
            "LT",
            "MA",
            "MD",
            "PL",
            "RU",
            "SK",
            "SI",
            "CZ",
        ],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "specialisation",
        "key": "leaf16",
        "title": "WBI bilateral academic specialisation scholarships",
        "category": "scholarships",
        "summary": "Academic-year complementary qualification abroad for FWB "
        "higher education graduates or final-year students "
        "completing their qualification before departure. "
        "Destination-specific funding and requirements apply; no "
        "dated call established. The separately named Québec fee "
        "exemption has distinct requirements.",
        "evidence": "",
        "hosts": [
            "BG",
            "EG",
            "EE",
            "HU",
            "IL",
            "JP",
            "LV",
            "LT",
            "PL",
            "CA",
            "CZ",
            "RU",
            "SK",
            "SI",
            "TN",
        ],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": [],
    },
    {
        "track": "quebec-exemption",
        "key": "leaf16",
        "title": "Québec tuition fee exemption",
        "category": "scholarships",
        "summary": "Separately named fee exemption for Belgian citizens with "
        "the required FWB school or degree certification studying in "
        "Québec. No WBI scholarship application is needed; the "
        "Québec-specific procedure applies. A fee exemption is not a "
        "cash stipend.",
        "evidence": "Belgian citizenship is explicit only for this named "
        "subprogramme and is not propagated to other WBI "
        "affiliation/residence schemes.",
        "hosts": ["CA"],
        "deadline": None,
        "kind": "programme-overview",
        "eligible": ["BE"],
    },
    {
        "track": "company",
        "key": "leaf17",
        "title": "WBI company internship scholarship — January–June 2027",
        "category": "internships",
        "summary": "One-to-three-month curricular company internship abroad in "
        "January–June 2027 for final-cycle FWB university or higher "
        "education students. €1,200/1,400/1,600 per month and 50% "
        "travel support capped at €500. No host salary or other "
        "public funding except social allowance. Apply by November "
        "1, 2026.",
        "evidence": "FWB affiliation, not Belgian citizenship. "
        "Doctoral/research placements excluded subject to actual "
        "article exceptions. Source says EU27 priority although "
        "Belgium contradicts abroad requirement: known destination "
        "projection excludes BE (EU26); unspecified border "
        "countries may qualify subject to budget. Raw 23h59 has no "
        "time zone.",
        "hosts": [
            "AT",
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
        ],
        "deadline": "2026-11-01",
        "kind": "opportunity",
        "eligible": [],
    },
    {
        "track": "circ-see",
        "key": "circ-call",
        "title": "WBI CIRC SEE internship — March–July 2027",
        "category": "internships",
        "summary": "Five-month funded internship at CIRC in Lyon, March–July "
        "2027, for a relevant FWB higher education master student "
        "enrolled throughout the placement, with English "
        "proficiency. €1,400/month and travel support up to €500. "
        "Apply by November 13, 2026.",
        "evidence": "Distinct named SEE internship call, not the CIRC pilot "
        "postdoctoral award. Raw midnight deadline has no time "
        "zone.",
        "hosts": ["FR"],
        "deadline": "2026-11-13",
        "kind": "opportunity",
        "eligible": [],
    },
]


def read_pages():
    """Read every required hub, leaf, current call and legal article."""
    return {key: (fetch_inventory_page(page, url)
                  if key.startswith("listing") else page(url))
            for key, url in PAGES.items()}


def parse_inventory(pages):
    """Normalize reviewed genuine identities; reject changed source facts."""
    if set(pages) != set(PAGES):
        raise AdapterError("Incomplete WBI source evidence", "parse")
    for key, root in pages.items():
        digest = hashlib.sha256(source_facts(key, root).encode()).hexdigest()
        if digest != CONDITIONS[key]:
            raise AdapterError(
                "WBI facts or inventory changed: " + key, "parse"
            )
    records = []
    today = datetime.now(timezone.utc).date().isoformat()
    for profile in PROFILES:
        record = make_record(
            profile["title"],
            PAGES[profile["key"]],
            [profile["category"]],
            kind=profile["kind"],
            host_countries=profile["hosts"],
            evidence=[ATTRIBUTION, profile["evidence"]],
        )
        record["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + profile["track"]).encode()
        ).hexdigest()[:24]
        record["summary"] = profile["summary"]
        record["eligible_countries"] = profile["eligible"]
        record["deadline"] = profile["deadline"]
        if profile["deadline"]:
            record["status"] = (
                "open"
                if today < profile["deadline"]
                else "expired" if today > profile["deadline"] else "unknown"
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
        tempfile.gettempdir(), "wbi-publisher-pacing-" + str(os.getuid())
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
    publisher = "wbi.be" if host == "wbi.be" else host
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
                checked.astimezone(timezone.utc).date()
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


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "display_name": "Wallonie-Bruxelles International",
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
                f"Collected {len(records)} validated WBI "
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
    enter_phase(
        "failure" if failure else "publication",
        _FAILURE_SECONDS if failure else _PUBLICATION_SECONDS,
    )
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
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
FAMILY = "youthopps-be-wbi-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_WORKFLOW_IDS = {SOURCE_ID: 378390052}
COLLECTION_TIMEOUT = 1200
RUN_TIMEOUT = 1740
EXPORT_TIMEOUT = 30
_PUBLICATION_SECONDS = 180
_FAILURE_SECONDS = 180
_PUBLISHER_TIMEOUT = _DEFAULT_HTTP_TIMEOUT = 30
_ARTIFACT_NAME = "be-wbi-pacing-state"
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
_JOURNAL_ACK = None
_JOURNAL_WRITE_ACK = None
_OWNED_EXPORT = None
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
_PUBLISHER_HOSTS = {"wbi.be", "www.wbi.be"}
_LEGACY_NO_JOB = {}
_LOCAL_RESEARCH_MIGRATION = {
    'schema': 1,
    'source': 'be-wbi',
    'evidence': (
        'https://github.com/YouthOpps/data-pipeline/issues/312#issuecomment-6'
        '209076636'
    ),
    'state': {
        'schema': 3,
        'family': 'youthopps-be-wbi-publisher-v1',
        'legacy_hold_history': 'unlogged_fields_unknown',
        'legacy_access_history_unknown': True,
        'legacy_access_observations': [],
        'research_provenance': {
            'status': 'ROOT_READONLY_FULL27_JOBS_LEGACY_METADATA_MAX_PROPOSAL',
            'fresh_history_sha256': (
                '0de816068b6903c6ddfa4de9b65bf85fe37c8c96e4f9d5d91c35753c0515'
                'f2a6'
            ),
            'complete_attempts': 27,
            'executed_job_completion_upper_bounds': [
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38763419493,
                    'attempt': 1,
                    'job_id': 116520331183,
                    'time': 1792080109.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38721259612,
                    'attempt': 1,
                    'job_id': 116376106162,
                    'time': 1792061043.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38720084129,
                    'attempt': 1,
                    'job_id': 116422193478,
                    'time': 1792067623.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38699535766,
                    'attempt': 1,
                    'job_id': 116307469573,
                    'time': 1792050360.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38684235840,
                    'attempt': 1,
                    'job_id': 116255573779,
                    'time': 1792040693.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38653185962,
                    'attempt': 1,
                    'job_id': 116156655980,
                    'time': 1792020080.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38617479722,
                    'attempt': 1,
                    'job_id': 116036182946,
                    'time': 1792003582.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38575119501,
                    'attempt': 1,
                    'job_id': 115890068272,
                    'time': 1791985998.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38534756435,
                    'attempt': 1,
                    'job_id': 115762998186,
                    'time': 1791967710.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38530521163,
                    'attempt': 1,
                    'job_id': 115741034251,
                    'time': 1791964188.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38486156395,
                    'attempt': 1,
                    'job_id': 115600197042,
                    'time': 1791935337.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38461567531,
                    'attempt': 1,
                    'job_id': 115517770389,
                    'time': 1791922833.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38377089028,
                    'attempt': 1,
                    'job_id': 115228079907,
                    'time': 1791884178.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38315043136,
                    'attempt': 1,
                    'job_id': 115029551063,
                    'time': 1791843201.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38229534500,
                    'attempt': 1,
                    'job_id': 114745307716,
                    'time': 1791797195.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38185405300,
                    'attempt': 1,
                    'job_id': 114606538585,
                    'time': 1791762474.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38166116917,
                    'attempt': 1,
                    'job_id': 114548924389,
                    'time': 1791745380.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38126916113,
                    'attempt': 1,
                    'job_id': 114433083194,
                    'time': 1791709531.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38095753116,
                    'attempt': 1,
                    'job_id': 114341175439,
                    'time': 1791675744.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38076801414,
                    'attempt': 1,
                    'job_id': 114285369452,
                    'time': 1791657905.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38071840281,
                    'attempt': 1,
                    'job_id': 114274950679,
                    'time': 1791654764.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38038572606,
                    'attempt': 1,
                    'job_id': 114174088776,
                    'time': 1791621750.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 38007470104,
                    'attempt': 1,
                    'job_id': 114079480609,
                    'time': 1791590908.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37981135658,
                    'attempt': 1,
                    'job_id': 113991645483,
                    'time': 1791574671.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37909980312,
                    'attempt': 1,
                    'job_id': 113754795259,
                    'time': 1791537895.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37869499633,
                    'attempt': 1,
                    'job_id': 113624886485,
                    'time': 1791509380.0,
                },
                {
                    'kind': (
                        'actual_job_completion_UPPER_BOUND_not_publisher_wire'
                    ),
                    'run_id': 37869478325,
                    'attempt': 1,
                    'job_id': 113623998633,
                    'time': 1791509172.0,
                },
            ],
            'original_legacy_observation': {
                'namespace': 'wbi-publisher-pacing',
                'status': 'ABSENT_NOT_ZERO_HISTORY',
                'collection_lock_before_entry': {
                    'status': 'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE',
                },
            },
            'metadata_sha256': (
                '0648ac28a5e1e5f46f77a699ba546c2cc6d274483113784687ea3b6e3be0'
                '8292'
            ),
            'data_source_pin': 'a6713e419a1fdca3299d60ce81080d4d3223bc07',
            'unknown_constraints': (
                'unlogged historical publisher access/refusal/429 headers rem'
                'ain unknown; zero observed strikes only after explicit trans'
                'ition'
            ),
            'baseline_collection_lock': (
                'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE'
            ),
        },
        'starts': [
            1791509172.0,
            1791509380.0,
            1791537895.0,
            1791574671.0,
            1791590908.0,
            1791621750.0,
            1791654764.0,
            1791657905.0,
            1791675744.0,
            1791709531.0,
            1791745380.0,
            1791762474.0,
            1791797195.0,
            1791843201.0,
            1791884178.0,
            1791922833.0,
            1791935337.0,
            1791964188.0,
            1791967710.0,
            1791985998.0,
            1792003582.0,
            1792020080.0,
            1792040693.0,
            1792050360.0,
            1792061043.0,
            1792067623.0,
            1792080109.0,
        ],
        'interval': 12.2,
        'until': 1792129880.7685463,
        'page_gap': 30,
        'completed': 1792080109.0,
        'inventory_completed': 1792080109.0,
        'blocked': False,
        'in_flight': False,
        'legacy_429_history': 'unknown',
        'observed_429_since_transition': 0,
        'rate_limit_until': 0,
        'observed_at': 1792129820.7685463,
    },
    'state_sha256': (
        '0b24d5f6acea76920c2ff79612500b0092ccfcf8c8717ae0591bc1a5cbad54f7'
    ),
    'legacy_state_sha256': (
        'b82af02c6f8ed2bb125b7d104ba6c9c89fc1f85ee141655120daf5860f2cd609'
    ),
}
_REVIEWED_NATIVE_MIGRATION = {'schema': 1,
 'source': 'be-wbi',
 'evidence': (
                'https://github.com/YouthOpps/data-pipeli'
                'ne/issues/312#issuecomment-6209686521'
            ),
 'state': {'schema': 3,
           'family': 'youthopps-be-wbi-publisher-v1',
           'legacy_hold_history': 'unlogged_fields_unknown',
           'legacy_access_history_unknown': True,
           'legacy_access_observations': [],
           'research_provenance': {'baseline_collection_lock': 'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE',
                                   'complete_attempts': 27,
                                   'data_source_pin': (
                                                         'a6713e419a1fdca3299d60ce81080d4d3223bc07'
                                                     ),
                                   'executed_job_completion_upper_bounds': [{'attempt': 1,
                                                                             'job_id': 116520331183,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38763419493,
                                                                             'time': 1792080109.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116376106162,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38721259612,
                                                                             'time': 1792061043.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116422193478,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38720084129,
                                                                             'time': 1792067623.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116307469573,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38699535766,
                                                                             'time': 1792050360.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116255573779,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38684235840,
                                                                             'time': 1792040693.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116156655980,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38653185962,
                                                                             'time': 1792020080.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 116036182946,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38617479722,
                                                                             'time': 1792003582.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115890068272,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38575119501,
                                                                             'time': 1791985998.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115762998186,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38534756435,
                                                                             'time': 1791967710.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115741034251,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38530521163,
                                                                             'time': 1791964188.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115600197042,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38486156395,
                                                                             'time': 1791935337.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115517770389,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38461567531,
                                                                             'time': 1791922833.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115228079907,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38377089028,
                                                                             'time': 1791884178.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 115029551063,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38315043136,
                                                                             'time': 1791843201.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114745307716,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38229534500,
                                                                             'time': 1791797195.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114606538585,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38185405300,
                                                                             'time': 1791762474.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114548924389,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38166116917,
                                                                             'time': 1791745380.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114433083194,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38126916113,
                                                                             'time': 1791709531.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114341175439,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38095753116,
                                                                             'time': 1791675744.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114285369452,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38076801414,
                                                                             'time': 1791657905.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114274950679,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38071840281,
                                                                             'time': 1791654764.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114174088776,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38038572606,
                                                                             'time': 1791621750.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 114079480609,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 38007470104,
                                                                             'time': 1791590908.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113991645483,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37981135658,
                                                                             'time': 1791574671.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113754795259,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37909980312,
                                                                             'time': 1791537895.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113624886485,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37869499633,
                                                                             'time': 1791509380.0},
                                                                            {'attempt': 1,
                                                                             'job_id': 113623998633,
                                                                             'kind': (
                                                                                        'actual_job_completion_UPPER_BOUND_not_pu'
                                                                                        'blisher_wire'
                                                                                    ),
                                                                             'run_id': 37869478325,
                                                                             'time': 1791509172.0}],
                                   'fresh_history_sha256': (
                                                              '0de816068b6903c6ddfa4de9b65bf85fe37c8c96'
                                                              'e4f9d5d91c35753c0515f2a6'
                                                          ),
                                   'metadata_sha256': (
                                                         '0648ac28a5e1e5f46f77a699ba546c2cc6d27448'
                                                         '3113784687ea3b6e3be08292'
                                                     ),
                                   'original_legacy_observation': {'collection_lock_before_entry': {'status': 'NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE'},
                                                                   'namespace': (
                                                                                   'wbi-publisher-pacing'
                                                                               ),
                                                                   'status': (
                                                                                'ABSENT_NOT_ZERO_HISTORY'
                                                                            )},
                                   'status': (
                                                'ROOT_READONLY_FULL27_JOBS_LEGACY_METADAT'
                                                'A_MAX_PROPOSAL'
                                            ),
                                   'unknown_constraints': 'unlogged '
                                                          'historical '
                                                          'publisher '
                                                          'access/refusal/429 '
                                                          'headers remain '
                                                          'unknown; zero '
                                                          'observed '
                                                          'strikes only '
                                                          'after explicit '
                                                          'transition'},
           'starts': [1791509172.0,
                      1791509380.0,
                      1791537895.0,
                      1791574671.0,
                      1791590908.0,
                      1791621750.0,
                      1791654764.0,
                      1791657905.0,
                      1791675744.0,
                      1791709531.0,
                      1791745380.0,
                      1791762474.0,
                      1791797195.0,
                      1791843201.0,
                      1791884178.0,
                      1791922833.0,
                      1791935337.0,
                      1791964188.0,
                      1791967710.0,
                      1791985998.0,
                      1792003582.0,
                      1792020080.0,
                      1792040693.0,
                      1792050360.0,
                      1792061043.0,
                      1792067623.0,
                      1792080109.0,
                      1792131384.1868145,
                      1792131397.2941773,
                      1792131397.7312076,
                      1792131398.9309628,
                      1792131411.1312177,
                      1792131411.1340926,
                      1792131411.8506842,
                      1792131413.9158933,
                      1792131426.1177025,
                      1792131426.1215372,
                      1792131426.7412431,
                      1792131429.487292,
                      1792131441.689956,
                      1792131441.6922927,
                      1792131442.1870565,
                      1792131444.2191887,
                      1792131456.421561,
                      1792131456.4304988,
                      1792131456.4472606,
                      1792131458.3707855,
                      1792131470.5730934,
                      1792131470.5762203,
                      1792131471.067965,
                      1792131473.4885182,
                      1792131485.6901727,
                      1792131485.6959174,
                      1792131485.869367,
                      1792131488.435011,
                      1792131500.6375291,
                      1792131500.6458335,
                      1792131500.8216357,
                      1792131503.1257904,
                      1792131515.328046,
                      1792131515.3312814,
                      1792131516.0695753,
                      1792131518.5898871,
                      1792131530.7952716,
                      1792131530.7991614,
                      1792131531.49404,
                      1792131533.5803432,
                      1792131545.7885568,
                      1792131545.800235,
                      1792131546.3913147,
                      1792131549.0843418,
                      1792131561.287793,
                      1792131561.2931323,
                      1792131561.8584912,
                      1792131563.9234233,
                      1792131576.126344,
                      1792131576.1506567,
                      1792131576.6575992,
                      1792131578.3168917,
                      1792131590.5200226,
                      1792131590.5386977,
                      1792131590.7138426,
                      1792131592.302015,
                      1792131604.5053082,
                      1792131604.5149686,
                      1792131604.6985579,
                      1792131607.2117317,
                      1792131619.4176486,
                      1792131619.423902,
                      1792131619.867172,
                      1792131621.7006733,
                      1792131633.9030821,
                      1792131633.9063962,
                      1792131633.920456,
                      1792131635.6245553,
                      1792131647.8270695,
                      1792131647.8328147,
                      1792131648.0059683,
                      1792131649.6347728,
                      1792131661.8368022,
                      1792131661.8399518,
                      1792131662.3815768,
                      1792131664.3887634,
                      1792131676.5904834,
                      1792131676.5990784,
                      1792131676.618145,
                      1792131677.985494,
                      1792131690.1868813,
                      1792131690.1901367,
                      1792131690.371414,
                      1792131692.1443071,
                      1792131704.3459175,
                      1792131704.3486452,
                      1792131704.379663,
                      1792131707.1474388,
                      1792131737.1495957,
                      1792131737.1522133,
                      1792131737.3169506,
                      1792131739.4805417,
                      1792131769.483775,
                      1792131769.4887035,
                      1792131770.1759398,
                      1792131776.1453888,
                      1792131806.148205,
                      1792131806.150804,
                      1792131806.5195644,
                      1792131814.1475723,
                      1792131826.3496315,
                      1792131826.3524706,
                      1792131827.0639372,
                      1792131832.4803247,
                      1792131844.681524,
                      1792131844.684324,
                      1792131845.094238,
                      1792131856.5225348,
                      1792131886.5307398,
                      1792131886.5356104,
                      1792131887.0230234,
                      1792131896.9577084,
                      1792131909.187787,
                      1792131909.2189054,
                      1792131910.2108521,
                      1792131918.9069145],
           'interval': 12.2,
           'until': 1792131979.5071602,
           'page_gap': 30,
           'completed': 1792131918.9082556,
           'inventory_completed': 1792131896.9610837,
           'blocked': False,
           'in_flight': False,
           'legacy_429_history': 'unknown',
           'observed_429_since_transition': 0,
           'rate_limit_until': 0,
           'observed_at': 1792131919.5071602},
 'state_sha256': (
                    '276362215bf8752eedbdbb50a9da0466c6307092'
                    'f8036ec67a9480fe8db1cfdd'
                ),
 'legacy_state_sha256': (
                           'b82af02c6f8ed2bb125b7d104ba6c9c89fc1f85e'
                           'e141655120daf5860f2cd609'
                       )}
_LEGACY_ATTEMPTS = {38763419493: {'source': 'be-wbi',
               'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '753baa98eb042cbedd26a2d683b5ac9a'
                   'd09a4a8ce68d0aacccc4ef0fac87b268'
               )},
 38721259612: {'source': 'be-wbi',
               'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'a4ae2f6d3b4aae9f62d625890c6ca3e3'
                   '1b7e4ae45334394a1d3187b97ba6bc08'
               )},
 38720084129: {'source': 'be-wbi',
               'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'ec0514ea60ed1014378ee08398fa7e7a'
                   'fd90ea0551b728482a8c04ea670fa1ad'
               )},
 38699535766: {'source': 'be-wbi',
               'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '1a206d1e62f7c0425ba7a80919a65d21'
                   '36be4a937df01698c955f6f2b779482b'
               )},
 38684235840: {'source': 'be-wbi',
               'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '7ec02ef594b073d7bfaf039390a62659'
                   'e5a2b250a5ae48da23f2787b27f9d7e4'
               )},
 38653185962: {'source': 'be-wbi',
               'head_sha': 'e1a68725fe923582b5640481e29c7166eb77397c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '1c94060acdf67bc8067e19db53d396d1'
                   'a9c2e1680be8000813be715d709319e3'
               )},
 38617479722: {'source': 'be-wbi',
               'head_sha': '92235b48ad33b9fa377e0736b689010173d7bfab',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'a8425f6b8e4c8451777a69a5830e0f4d'
                   '4156b8075df28f8f2966578312fb83a0'
               )},
 38575119501: {'source': 'be-wbi',
               'head_sha': '92235b48ad33b9fa377e0736b689010173d7bfab',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '482e11ead7c931a65334c02ef0995da2'
                   'c1df8b7e954dcdd4c16737c64bd7e438'
               )},
 38534756435: {'source': 'be-wbi',
               'head_sha': '5ab9b0237b7fa2b043fe276ec43f29e493c685ab',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '7b0c30f937078a7191e55f3823b1c469'
                   '03df8cca2cc2482545a3a24ced8e70e1'
               )},
 38530521163: {'source': 'be-wbi',
               'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '6a18abbc6e3d75af4eb583f6ba6b5523'
                   'bea7954a65d3664f2936b4c4bb5f2c52'
               )},
 38486156395: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'f39ef67191adf98892662f3c12f74745'
                   'b9afc3009c946d2f0f8d2a9af6036563'
               )},
 38461567531: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'dabb9aaf0368383c674ef49116f5e277'
                   '882fbb9551d136794aa42523932743f2'
               )},
 38377089028: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '854273b8fb280a26826fe2cb959e73a7'
                   '86e42f92920f3983afeedf8d8a971588'
               )},
 38315043136: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '3d25cc7b4077337eb973f097306acc99'
                   'ec29ca95b11f13a935a4679c7c84c660'
               )},
 38229534500: {'source': 'be-wbi',
               'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '90667da7b6a22423c1eba8362657c5fc'
                   'c4d540d14154ed8c5af67bb50aef8b2c'
               )},
 38185405300: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '629211b568b24863db7927fa9d7f7a52'
                   '0b2cd4d82bdd6365bebf237cb6ba7a86'
               )},
 38166116917: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '53690b5c50664d725d6f1864f0574472'
                   '7c4270971d5796b95cfa964701bc80f2'
               )},
 38126916113: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '96c683b3b960b09a8be17a9019500659'
                   '70bb2a13651858781a4c1b4ee6a762d6'
               )},
 38095753116: {'source': 'be-wbi',
               'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '14acc7eae3a85c7e4c410734bdab52e2'
                   '142052f438ce639638060c96802df041'
               )},
 38076801414: {'source': 'be-wbi',
               'head_sha': 'a97e28475634b9ff61489a11cd6378c9dafc67ae',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'fa5c791779a0a4d385a5719482ac7aeb'
                   'da77818898f8e099f3544c8c1e2b6aec'
               )},
 38071840281: {'source': 'be-wbi',
               'head_sha': '9b155b179283c8fa08cf56c1a83a54bd19d51814',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '827ee9b1dfb15474ea8534ee3549e4a8'
                   '0eba9b46ff68b0953aba0a43ca977491'
               )},
 38038572606: {'source': 'be-wbi',
               'head_sha': '68868a743f2ae9dcb55de9443c2ea7fcd8714696',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'e5b5aaf0ddac91dfe9dd22172a954c4d'
                   '2390cf63e51db9f63a22c781d4dd9e41'
               )},
 38007470104: {'source': 'be-wbi',
               'head_sha': '029a500dbf22c1d44ae592c667f26f33d9d364c8',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'b01693933b17e1847411b310b4541d7c'
                   '5d1cc04d225b0e18279ca95868360c89'
               )},
 37981135658: {'source': 'be-wbi',
               'head_sha': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'c755c6451a841dc5a5153981fd12249c'
                   'a21a528c35b2b128a61527acfd6415fd'
               )},
 37909980312: {'source': 'be-wbi',
               'head_sha': 'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   'bed1529633de78ebcc230e6f46b72d5f'
                   'afc26549e285cd4573cf728a07dce528'
               )},
 37869499633: {'source': 'be-wbi',
               'head_sha': '7026281e1d457a019aaf0f75f6fbc9b3fe70d3ad',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '994e8dae54a77a8354f06bfbd2615593'
                   '343019c3e5ad60efb0ea42fbe923773f'
               )},
 37869478325: {'source': 'be-wbi',
               'head_sha': '7026281e1d457a019aaf0f75f6fbc9b3fe70d3ad',
               'workflow_id': 378390052,
               'jobs_sha256': (
                   '0979a7f4b8a2d028ea707a1d6e056044'
                   'fb5ae05f8daf4bb4722186bede867438'
               )}}
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



def journal_generation(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_gid, info.st_nlink, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


def journal_snapshot(descriptor):
    """Read the complete bounded bytes without adopting a new generation."""
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    before = validate_state_descriptor(descriptor, parent, name)
    raw = os.pread(descriptor, 16385, 0)
    after = validate_state_descriptor(descriptor, parent, name)
    if (len(raw) > 16384 or len(raw) != after.st_size
            or journal_generation(before) != journal_generation(after)):
        raise JournalAuthorityChanged("Publisher read changed", "access")
    return journal_generation(after), raw


def require_journal_snapshot(descriptor, expected):
    if expected is None or journal_snapshot(descriptor) != expected:
        raise JournalAuthorityChanged(
            "Publisher journal acknowledgement changed", "access"
        )


def acknowledge_journal_write(descriptor, raw):
    """Track only bytes observed after our own completed write syscall."""
    global _JOURNAL_WRITE_ACK
    current = journal_snapshot(descriptor)
    if current[1] != raw:
        raise JournalAuthorityChanged("Publisher write changed", "access")
    _JOURNAL_WRITE_ACK = current


def verify_current_journal():
    """Recheck the retained acknowledgement across close and reopen."""
    if _JOURNAL_ACK is not None:
        with locked_budget():
            locked_journal_authority()


def locked_journal_authority(check_generation=True):
    """Require the original RW flock and last acknowledged full bytes."""
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
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if check_generation:
            require_journal_snapshot(guard, _JOURNAL_ACK)
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
    """Bind the original safe read/write inode in its verified parent."""
    info = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if ((info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_RDWR):
        raise JournalAuthorityChanged(
            "Publisher journal path or RW descriptor changed", "access"
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
    """Acknowledge a first validated read; never adopt later unseen bytes."""
    global _JOURNAL_ACK
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        authority = locked_journal_authority(False)
        snapshot = journal_snapshot(authority)
        if _JOURNAL_ACK is not None:
            require_journal_snapshot(authority, _JOURNAL_ACK)
        raw = snapshot[1]
        if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
            state = validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
        else:
            try:
                state = validate_budget(json.loads(raw))
            except (ValueError, TypeError):
                raise AdapterError(
                    "Corrupt publisher journal", "access"
                ) from None
        require_journal_snapshot(authority, snapshot)
        if _JOURNAL_ACK is None:
            _JOURNAL_ACK = snapshot
        locked_journal_authority()
        return state
    except BaseException:
        fail_publisher_cleanup()
        raise



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
                locked_journal_authority(False)
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
        or fcntl.fcntl(descriptor, fcntl.F_GETFL)
        & os.O_ACCMODE != os.O_RDWR
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
    if descriptor is None:
        return
    failure = None
    if not _PUBLISHER_GATE_FAILED:
        try:
            collection_lock_authority()
            verify_current_journal()
        except BaseException as error:
            failure = error
    _COLLECTION_LOCK_FD = _COLLECTION_LOCK_CONTEXT = None
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
        failure = None
        safe = []
        if not _PUBLISHER_GATE_FAILED:
            try:
                locked_journal_authority()
            except BaseException as error:
                failure = error
        _JOURNAL_LOCKS.pop(descriptor, None)
        _JOURNAL_GUARDS.pop(descriptor, None)
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
    """Invalidate only our still-current last observed owned generation."""
    authority = locked_journal_authority(False)
    expected = _JOURNAL_WRITE_ACK or _JOURNAL_ACK
    require_journal_snapshot(authority, expected)
    os.ftruncate(authority, 0)
    os.fsync(authority)
    validate_state_descriptor(
        authority, state_parent(), os.path.basename(_STATE_PATH)
    )



def write_budget_body(state):
    """Commit only acknowledged bytes through the retained original FD."""
    global _INITIAL_DIRECTORY_PROVEN, _JOURNAL_ACK, _JOURNAL_WRITE_ACK
    state = validate_budget(state)
    raw = json.dumps(state, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher journal commit", "access")
    authority = locked_journal_authority()
    _JOURNAL_WRITE_ACK = _JOURNAL_ACK
    os.ftruncate(authority, 0)
    acknowledge_journal_write(authority, b"")
    os.fsync(authority)
    require_journal_snapshot(authority, _JOURNAL_WRITE_ACK)
    offset = 0
    while offset < len(raw):
        require_journal_snapshot(authority, _JOURNAL_WRITE_ACK)
        written = os.pwrite(authority, raw[offset:], offset)
        if type(written) is not int or not 0 < written <= len(raw) - offset:
            raise AdapterError("Incomplete publisher journal write", "access")
        offset += written
        acknowledge_journal_write(authority, raw[:offset])
    os.fsync(authority)
    require_journal_snapshot(authority, _JOURNAL_WRITE_ACK)
    _JOURNAL_ACK = _JOURNAL_WRITE_ACK
    _JOURNAL_WRITE_ACK = None
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
    """Pin the original parent and make otherwise healthy close errors fatal."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    root, before = checked_artifact_path(path)
    descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    )
    held = None
    try:
        held = os.fstat(descriptor)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Artifact directory changed", "access")
        verify_artifact_directory(root, descriptor)
        yield root, descriptor
    finally:
        primary = sys.exc_info()[1]
        try:
            if held is None:
                raise AdapterError("Unverified output parent", "access")
            owned_export_parent(descriptor, (held.st_dev, held.st_ino))
            os.close(descriptor)
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            with contextlib.suppress(BaseException):
                invalidate_retained_export()
            if primary is None:
                raise



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
    if _OWNED_EXPORT is not None:
        invalidate_retained_export()
        return
    path = os.environ.get("WBI_PACING_ARTIFACT_PATH")
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



def owned_export_file(descriptor, identity):
    info = os.fstat(descriptor)
    if ((info.st_dev, info.st_ino) != identity
            or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink not in (0, 1)
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_WRONLY):
        raise AdapterError("Retained output descriptor changed", "access")
    return info


def owned_export_parent(descriptor, identity):
    info = os.fstat(descriptor)
    if ((info.st_dev, info.st_ino) != identity
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            != os.O_RDONLY):
        raise AdapterError("Retained output parent changed", "access")


def invalidate_retained_export():
    """Invalidate original verified output before signal/unlink can fail."""
    if _OWNED_EXPORT is None:
        return
    root, directory, parent_id, descriptor, identity = _OWNED_EXPORT
    owned_export_file(descriptor, identity)
    os.ftruncate(descriptor, 0)
    os.fsync(descriptor)
    errors = []
    try:
        workflow_output_signal("false")
    except BaseException as error:
        errors.append(error)
    try:
        owned_export_parent(directory, parent_id)
        current = os.stat(
            "pacing-state.json", dir_fd=directory, follow_symlinks=False
        )
        validate_owned_output(current)
        if (current.st_dev, current.st_ino) == identity:
            os.unlink("pacing-state.json", dir_fd=directory)
    except FileNotFoundError:
        pass
    except BaseException as error:
        errors.append(error)
    if errors:
        raise AdapterError("Retained output invalidation failed", "access")


def close_retained_export():
    """Release independent known descriptors without hiding body errors."""
    global _OWNED_EXPORT, _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _OWNED_EXPORT is None:
        return
    root, directory, parent_id, descriptor, identity = _OWNED_EXPORT
    primary = sys.exc_info()[1]
    errors = []
    try:
        owned_export_parent(directory, parent_id)
        os.close(directory)
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        errors.append(error)
        with contextlib.suppress(BaseException):
            invalidate_retained_export()
    try:
        owned_export_file(descriptor, identity)
        os.close(descriptor)
    except BaseException as error:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        errors.append(error)
        with contextlib.suppress(BaseException):
            invalidate_retained_export()
    _OWNED_EXPORT = None
    if errors and primary is None:
        raise errors[0]


def retain_owned_export(root, directory, descriptor, info):
    """Transfer only a fully verified handoff; keep old authority on error."""
    global _OWNED_EXPORT
    identity = (info.st_dev, info.st_ino)
    owned_export_file(descriptor, identity)
    verify_artifact_directory(root, directory)
    same_owned_artifact(root, directory, info)
    parent = os.dup(directory)
    parent_info = os.fstat(parent)
    parent_id = (parent_info.st_dev, parent_info.st_ino)
    try:
        owned_export_parent(parent, parent_id)
        close_retained_export()
        _OWNED_EXPORT = (root, parent, parent_id, descriptor, identity)
    except BaseException:
        with contextlib.suppress(BaseException):
            owned_export_parent(parent, parent_id)
            os.close(parent)
        raise


def write_owned_artifact(root, directory, name, envelope):
    """Use unbuffered verified output; never flush to a reused numeric FD."""
    raw = json.dumps(envelope).encode()
    descriptor = identity = None
    primary = None
    keep = False
    try:
        verify_artifact_directory(root, directory)
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            | os.O_NONBLOCK, 0o600, dir_fd=directory
        )
        info = os.fstat(descriptor)
        validate_owned_output(info)
        identity = (info.st_dev, info.st_ino)
        offset = 0
        while offset < len(raw):
            verify_artifact_directory(root, directory)
            held = os.fstat(descriptor)
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            validate_owned_output(held)
            if ((held.st_dev, held.st_ino) != identity
                    or (named.st_dev, named.st_ino) != identity
                    or fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
                    != os.O_WRONLY):
                raise AdapterError("Owned output descriptor changed", "access")
            count = os.write(descriptor, raw[offset:])
            if type(count) is not int or not 0 < count <= len(raw) - offset:
                raise AdapterError("Incomplete owned output write", "access")
            offset += count
        os.fsync(descriptor)
        verify_artifact_directory(root, directory)
        held = os.fstat(descriptor)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        validate_owned_output(held)
        if ((held.st_dev, held.st_ino) != identity
                or (named.st_dev, named.st_ino) != identity):
            raise AdapterError("Owned output inode changed", "access")
        keep = True
        return held, descriptor
    except BaseException as error:
        primary = error
        raise
    finally:
        if descriptor is not None and not keep:
            try:
                held = os.fstat(descriptor)
                validate_owned_output(held)
                if (identity is None
                        or (held.st_dev, held.st_ino) != identity
                        or fcntl.fcntl(descriptor, fcntl.F_GETFL)
                        & os.O_ACCMODE != os.O_WRONLY):
                    raise AdapterError("Owned output close changed", "access")
                os.close(descriptor)
            except BaseException:
                if primary is None:
                    raise


def export_family_artifact():
    """Atomically export trusted state; invalidate owned output on failure."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    path = os.environ.get("WBI_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    with owned_artifact_directory(path) as (root, directory):
        prior = owned_artifact_info(root, directory)
        temporary = None
        temporary_info = None
        output_fd = None
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
            temporary_info, output_fd = write_owned_artifact(
                root, directory, temporary, envelope
            )
            verify_current_journal()
            same_owned_artifact(root, directory, prior)
            os.replace(
                temporary,
                "pacing-state.json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary = None
            same_owned_artifact(root, directory, temporary_info)
            retain_owned_export(
                root, directory, output_fd, temporary_info
            )
            output_fd = None
            verify_current_journal()
            workflow_output_signal("true")
        except BaseException:
            _STATE_TRUSTED = False
            _PUBLISHER_GATE_FAILED = True
            with contextlib.suppress(BaseException):
                invalidate_family_artifact()
            raise
        finally:
            primary = sys.exc_info()[1]
            if output_fd is not None:
                try:
                    owned_export_file(
                        output_fd, (temporary_info.st_dev,
                                    temporary_info.st_ino)
                    )
                    os.close(output_fd)
                except BaseException:
                    fail_publisher_cleanup()
                    if primary is None:
                        raise
            try:
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
                        raise AdapterError(
                            "Temporary artifact changed", "access"
                        )
                    os.unlink(temporary, dir_fd=directory)
            except BaseException:
                _STATE_TRUSTED = False
                _PUBLISHER_GATE_FAILED = True
                with contextlib.suppress(BaseException):
                    invalidate_family_artifact()
                if primary is None:
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



class HeaderRateLimitStop(AdapterError):
    """A durable header-only429 whose response context closed successfully."""


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
                if status == 429:
                    raise HeaderRateLimitStop(
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
            if publisher and status == 429:
                raise AdapterError(
                    "Publisher429; durable adaptive cooldown retained",
                    "access",
                    429,
                )
            return status, response.headers, body
    except HeaderRateLimitStop:
        # Only a successfully closed response is fully assessed.
        response_constraints_saved = True
        raise
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
    """Only tighten the entry-frozen lifecycle against its actual job proof."""
    global _RUN_END, _SOURCE_END, _PHASE_END
    if _RUN_END is not None and _PUBLICATION_WINDOW is not None:
        _RUN_END = min(_RUN_END, _PUBLICATION_WINDOW["job"] - 60)
        _SOURCE_END = min(_SOURCE_END, _RUN_END - 390)
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
    _SOURCE_END = _RUN_END - 390
    _PHASE_END, _PHASE_NAME = (
        min(time.monotonic() + 180, _SOURCE_END - 30), "restore"
    )
    _COLLECTION_PREPARED = False
    try:
        yield
    finally:
        try:
            try:
                close_collection_lock()
            finally:
                close_retained_export()
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
        tempfile.gettempdir(), "wbi-publisher-pacing-" + str(os.getuid())
    )
    try:
        parent = os.open(
            directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
    except FileNotFoundError:
        return {
            "namespace": "wbi-publisher-pacing",
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
            hashlib.sha256(b"wbi.be").hexdigest(): "publisher",
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
            "namespace": "wbi-publisher-pacing",
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
            r"https://github\.com/YouthOpps/data-pipeline/issues/312"
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
            latest != (38763419493, 1, SOURCE_ID)
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



def legacy_collection_observation():
    """The exact original WBI collector has no coordination lock."""
    return {"status": "NO_ORIGINAL_COLLECTION_LOCK_IN_BASELINE"}


def authenticate_current_job():
    """Authenticate current hosted job before trusting or creating state."""
    check_deadline()
    path = os.environ.get("WBI_PACING_ARTIFACT_PATH")
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
    """One source owner reserves original job proof and publication tails."""
    global _SOURCE_END, _PHASE_END, _PHASE_NAME
    if _PHASE_NAME != "source":
        _SOURCE_END = min(
            _SOURCE_END, time.monotonic() + COLLECTION_TIMEOUT,
            _RUN_END - _PUBLICATION_SECONDS - _FAILURE_SECONDS
            - EXPORT_TIMEOUT,
        )
        _PHASE_END, _PHASE_NAME = _SOURCE_END, "source"
    clamp_publication_window()
    check_deadline()


def page(url):
    global _INVENTORY_ACTIVE
    previous = _INVENTORY_ACTIVE
    inventory = {
        value for key, value in PAGES.items()
        if key.startswith("listing") or key.startswith("category")
    }
    _INVENTORY_ACTIVE = previous or url in inventory
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


if __name__ == "__main__":
    raise SystemExit(main())
