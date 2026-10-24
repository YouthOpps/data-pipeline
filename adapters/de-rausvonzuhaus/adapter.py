"""Public Rausvonzuhaus vacancies with attributed factual card metadata."""

import argparse
import base64
import contextlib
from datetime import date, datetime, timedelta, timezone
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
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "de-rausvonzuhaus"
SOURCE_URL = "https://www.rausvonzuhaus.de/lastminute"
WEBSITE_URL = "https://www.rausvonzuhaus.de/"
LANGUAGE = "de"
PUBLISHER_COUNTRY = "DE"
PUBLISHER_TYPE = "youth-information-service"
ATTRIBUTION = "rausvonzuhaus.de / Eurodesk Deutschland (IJAB)"
_COUNTRIES = dict(
    zip(
        "Frankreich|USA|Deutschland|Südkorea|Island|Österreich|Togo|Ecuador|"
        "Indien|Uganda|Tansania|Thailand|Kambodscha|Kolumbien|Serbien|Georgien|"
        "Peru|Ghana|Südafrika|Ruanda|Großbritannien|Australien|Irland|Norwegen|"
        "Belgien|Kanada|Spanien|Rumänien|Costa Rica|Portugal|Estland|Finnland|"
        "Neuseeland|Polen|Schweiz|Kirgisistan|Dänemark|Namibia|Botswana|Vietnam|"
        "Israel|Fidschi|Dominikanische Republik|Malawi|Tschechien|Chile|"
        "Griechenland|Niederlande|Nepal|Kenia|Philippinen|Indonesien|Litauen|"
        "Italien|Kasachstan|Albanien|Brasilien|Bolivien|Mosambik|Kosovo|"
        "Bosnien und Herzegowina|Lesotho".split("|"),
        "FR US DE KR IS AT TG EC IN UG TZ TH KH CO RS GE PE GH ZA RW GB AU IE "
        "NO BE CA ES RO CR PT EE FI NZ PL CH KG DK NA BW VN IL FJ DO MW CZ CL "
        "GR NL NP KE PH ID LT IT KZ AL BR BO MZ XK BA LS".split(),
    )
)


class VacancyCards(HTMLParser):
    """Capture factual card elements, omitting images and copyright credits."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.cards = []
        self.card = None
        self.capture = None
        self.document_complete = False
        self.feed(document)
        self.close()
        if (
            self.card is not None
            or not self.cards
            or not self.document_complete
        ):
            raise AdapterError("Incomplete or empty vacancy listing", "parse")

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        classes = attributes.get("class", "").split()
        if tag == "a" and "lmm-item" in classes:
            if self.card is not None:
                raise AdapterError("Nested vacancy card", "parse")
            self.card = {
                "url": attributes.get("href", ""),
                "title": " ".join(attributes.get("data-title", "").split()),
                "paragraphs": [],
                "programme": "",
            }
        if self.card is not None:
            if tag == "p":
                self.capture = (tag, "paragraphs", [])
            elif tag == "span" and "text-meta-head" in classes:
                self.capture = (tag, "programme", [])

    def handle_data(self, value):
        if self.capture is not None:
            self.capture[2].append(value)

    def handle_endtag(self, tag):
        if tag == "html":
            self.document_complete = True
        if self.capture is not None and tag == self.capture[0]:
            _, field, parts = self.capture
            value = " ".join(" ".join(parts).split())
            if field == "paragraphs":
                self.card[field].append(value)
            else:
                self.card[field] = value
            self.capture = None
        if tag == "a" and self.card is not None:
            self.cards.append(self.card)
            self.card = None
            self.capture = None


def calendar_date(value):
    """Parse an explicit publisher calendar date without inventing a clock."""
    try:
        return datetime.strptime(value, "%d.%m.%Y").date()
    except ValueError as error:
        raise AdapterError("Invalid vacancy calendar date", "parse") from error


def card_record(card, today):
    """Normalize a genuine vacancy, excluding inconsistent dates."""
    url = urllib.parse.urljoin(SOURCE_URL, card["url"])
    parsed = urllib.parse.urlsplit(url)
    identity = re.fullmatch(r"/lastminute/detail/(\d+)", parsed.path)
    paragraphs = card["paragraphs"]
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.rausvonzuhaus.de"
        or parsed.query
        or parsed.fragment
        or identity is None
        or len(paragraphs) not in (5, 6)
        or not card["title"]
        or paragraphs[0] != card["title"]
        or not card["programme"]
    ):
        raise AdapterError("Vacancy card structure changed", "parse")
    age = re.fullmatch(r"(\d+)-(\d+) Jahre", paragraphs[1])
    period = re.fullmatch(
        r"(\d{2}\.\d{2}\.\d{4}) bis (\d{2}\.\d{2}\.\d{4})",
        paragraphs[3],
    )
    closing = re.fullmatch(
        r"Bewerbungsschluss: (\d{2}\.\d{2}\.\d{4})", paragraphs[4]
    )
    if age is None or period is None or closing is None:
        raise AdapterError("Unsupported vacancy age or date format", "parse")
    minimum, maximum = map(int, age.groups())
    if not 0 <= minimum <= maximum <= 120:
        raise AdapterError("Invalid vacancy age range", "parse")
    start, end = map(calendar_date, period.groups())
    deadline = calendar_date(closing[1])
    if start > end or deadline > end:
        return None
    programme = card["programme"]
    if "freiwilligendienst" in programme.casefold() or programme in (
        "weltwärts",
        "Workcamp",
        "Europäisches Solidaritätskorps (ESK)",
    ):
        categories = ["volunteering"]
    elif programme in (
        "Jugendbegegnung",
        "Langfristiger individueller Schüleraustausch",
    ):
        categories = ["training"]
    elif programme == "Praktikum für Studierende":
        categories = ["internships"]
    else:
        raise AdapterError(
            "Unsupported vacancy programme: " + programme, "parse"
        )
    country = paragraphs[2].split(",", 1)[0].strip()
    if country not in _COUNTRIES:
        raise AdapterError(
            "Unknown publisher host country: " + country, "parse"
        )
    evidence = [
        "Public vacancy card: " + url,
        "Programme: " + programme,
        "Age: " + paragraphs[1],
        "Publisher host label: " + paragraphs[2],
        "Project dates: " + paragraphs[3],
        paragraphs[4] + "; closing clock and time zone unspecified",
    ]
    record = make_record(
        card["title"],
        url,
        categories,
        tags=[programme],
        host_countries=[_COUNTRIES[country]],
        method="publisher-vacancy-card",
        evidence=evidence,
    )
    record.update(
        id=hashlib.sha256(f"{SOURCE_ID}|{identity[1]}".encode()).hexdigest()[
            :24
        ],
        source_record_id=identity[1],
        attribution=ATTRIBUTION,
        location=paragraphs[2],
        deadline=deadline.isoformat(),
        deadline_precision="date",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        age_min=minimum,
        age_max=maximum,
        status=(
            "open"
            if deadline > today
            else "expired" if deadline < today else "unknown"
        ),
        summary=(
            f"{programme}; {paragraphs[1]}; {paragraphs[2]}. "
            f"Projekt: {paragraphs[3]}. {paragraphs[4]}. "
            "Nationalitätsvoraussetzungen, Kosten und Leistungen sind "
            "in der Angebotskarte nicht angegeben. Quelle: rausvonzuhaus.de."
        ),
    )
    return record


class AccessText(HTMLParser):
    """Read primary visible text without navigation or CAPTCHA decoration."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.title_parts = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        skipped = (
            tag
            in {
                "head",
                "script",
                "style",
                "noscript",
                "footer",
                "header",
                "nav",
                "aside",
                "template",
            }
            or "hidden" in attrs
            or attrs.get("aria-hidden") == "true"
            or bool(
                re.search(
                    r"display\s*:\s*none|visibility\s*:\s*hidden",
                    attrs.get("style", ""),
                    re.I,
                )
            )
            or attrs.get("role")
            in {"navigation", "contentinfo", "complementary", "banner"}
        )
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
            self.stack.append((tag, skipped))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]
                break

    def handle_data(self, data):
        tags = [tag for tag, _ in self.stack]
        if (
            "head" in tags
            and "title" in tags
            and not any(
                tag in {"body", "footer", "nav", "aside", "header", "template"}
                for tag in tags
            )
        ):
            self.title_parts.append(data)
        if not any(skipped for _, skipped in self.stack):
            self.parts.append(data)


def collect_inventory():
    """Read the complete normal HTML list with paced, robots-checked access."""
    delay = check_robots(SOURCE_URL)
    document = fetch_source(SOURCE_URL, min_interval=delay)
    ensure_publisher_document(document)
    cards = VacancyCards(document).cards
    today = datetime.now(timezone.utc).date()
    records, identities = [], set()
    for card in cards:
        if card["url"] in identities:
            raise AdapterError("Duplicate publisher vacancy card", "parse")
        identities.add(card["url"])
        record = card_record(card, today)
        if record is not None:
            records.append(record)
    print(
        f"Publisher cards: {len(cards)}; retained: {len(records)}; "
        f"inconsistent dates: {len(cards) - len(records)}",
        file=sys.stderr,
    )
    validate_records(records)
    return records


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

_PREDECESSOR_APPROVAL = "https://github.com/YouthOpps/data-pipeline/issues/196#issuecomment-6195157525"
_PREDECESSOR_NOT_BEFORE = 1792076431.5925508
_PREDECESSOR_NATIVE_STATE = {
    "starts": [
        1791490097.0,
        1791490152.0,
        1791553597.0,
        1791623050.0,
        1791652913.0,
        1791654955.0,
        1791669556.0,
        1791700032.0,
        1791725496.0,
        1791757753.0,
        1791786722.0,
        1791821173.0,
        1791853098.0,
        1791902164.0,
        1791932975.0,
        1791955396.0,
        1791968445.0,
        1791980585.0,
        1791999334.0,
        1792015256.0,
        1792040333.0,
        1792048638.0,
        1792059946.0,
        1792061090.0,
        1792075676.2659442,
        1792075676.271686,
        1792075689.4015505,
        1792075689.4054656,
    ],
    "interval": 12.1,
    "until": 1792075709.746098,
    "blocked": False,
    "completed_at": 1792075697.6460981,
    "rate_failures": 0,
    "legacy_rate_history_unknown": True,
}


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


# Exact reviewed pre-artifact history; jobless cancellations made no requests.
_PREDECESSOR_REVIEWED_HISTORY = {
    38721259609: [
        1,
        "cb14a94366e7c9f3a518a6bd9f8a3da5331da39f",
        "failure",
        "2026-10-15T10:44:50Z",
    ],
    38720083560: [
        1,
        "a62a88f5cad62f4fb1ef5230358557d37d2b2bba",
        "success",
        "2026-10-15T10:25:46Z",
    ],
    38685076409: [
        1,
        "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "success",
        "2026-10-15T07:17:18Z",
    ],
    38684235821: [
        1,
        "9b97e13c2e2a56ee77700e40202eb6acd2212cf5",
        "success",
        "2026-10-15T04:58:53Z",
    ],
    38643890833: [
        1,
        "0ee73c37e42124c79f0c2005d14afc5db2c8e511",
        "success",
        "2026-10-14T22:00:56Z",
    ],
    38607818404: [
        1,
        "92235b48ad33b9fa377e0736b689010173d7bfab",
        "success",
        "2026-10-14T17:35:34Z",
    ],
    38562962007: [
        1,
        "92235b48ad33b9fa377e0736b689010173d7bfab",
        "success",
        "2026-10-14T12:23:05Z",
    ],
    38534815307: [
        1,
        "e47a87e43a672b1b6429749d1cef4851ea0dedee",
        "success",
        "2026-10-14T09:00:45Z",
    ],
    38516031206: [
        1,
        "02792bda730b08eeb1eda86a2cd71af80af3cef7",
        "success",
        "2026-10-14T05:23:16Z",
    ],
    38482121562: [
        1,
        "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "success",
        "2026-10-13T23:09:35Z",
    ],
    38414273450: [
        1,
        "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "success",
        "2026-10-13T14:36:04Z",
    ],
    38329759034: [
        1,
        "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "success",
        "2026-10-13T00:58:18Z",
    ],
    38274638821: [
        1,
        "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "success",
        "2026-10-12T16:06:13Z",
    ],
    38212670306: [
        1,
        "5dff04768f0e558b4d301d5677c5a2c433c3a64b",
        "success",
        "2026-10-12T06:32:02Z",
    ],
    38180389681: [
        1,
        "10d99f8ef021c092851bf189ff67307865b8760c",
        "success",
        "2026-10-11T22:29:13Z",
    ],
    38143285062: [
        1,
        "10d99f8ef021c092851bf189ff67307865b8760c",
        "success",
        "2026-10-11T13:31:36Z",
    ],
    38117907382: [
        1,
        "10d99f8ef021c092851bf189ff67307865b8760c",
        "success",
        "2026-10-11T06:27:12Z",
    ],
    38089644539: [
        1,
        "10d99f8ef021c092851bf189ff67307865b8760c",
        "success",
        "2026-10-10T21:59:16Z",
    ],
    38071840296: [
        1,
        "9b155b179283c8fa08cf56c1a83a54bd19d51814",
        "success",
        "2026-10-10T17:55:55Z",
    ],
    38071366817: [
        1,
        "9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4",
        "success",
        "2026-10-10T17:21:53Z",
    ],
    38054082131: [
        1,
        "86312a0c74a6ac3573cbc6b3250d96fbd2dd8488",
        "cancelled",
        None,
    ],
    38040028710: [
        1,
        "c55b8d99378274467f2d9a290967dbe10bc25031",
        "success",
        "2026-10-10T09:04:10Z",
    ],
    38030605791: [
        1,
        "6e5922277de22b96874c23a705a7d838512c6caf",
        "cancelled",
        None,
    ],
    38001501089: [
        1,
        "2d6491ddf2a37047efc7b996f190f648e647c0c6",
        "cancelled",
        None,
    ],
    37939060112: [
        1,
        "a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa",
        "success",
        "2026-10-09T13:46:37Z",
    ],
    37894637408: [
        1,
        "6f1971d163e39e44d46ffd72bec20795c59df105",
        "cancelled",
        None,
    ],
    37837072036: [
        1,
        "1e020bda0a238fcfa5e1d4435dd9ab2f0f4d17a1",
        "success",
        "2026-10-08T20:09:12Z",
    ],
    37837063360: [
        1,
        "1e020bda0a238fcfa5e1d4435dd9ab2f0f4d17a1",
        "success",
        "2026-10-08T20:08:17Z",
    ],
}


def request_bytes(
    url,
    *,
    interval=12.1,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
):
    publisher = urllib.parse.urlsplit(url).hostname in (
        "rausvonzuhaus.de",
        "www.rausvonzuhaus.de",
    )
    state, budget, manager = (
        pace(url, interval) if publisher else (None, None, None)
    )
    try:
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
            retry = response.headers.get("Retry-After")
            if publisher and retry:
                try:
                    now = time.time()
                    if re.fullmatch(r"\d+(?:\.\d+)?", retry):
                        seconds = float(retry)
                    else:
                        instant = parsedate_to_datetime(retry)
                        if instant.tzinfo is None:
                            raise ValueError("Retry-After requires timezone")
                        seconds = instant.timestamp() - now
                    until = now + seconds
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise ValueError("Unusable Retry-After")
                    datetime.fromtimestamp(until, timezone.utc)
                    budget["until"] = max(budget["until"], until)
                    save_budget(state, budget)
                except (ValueError, TypeError, OverflowError, OSError):
                    if response.status != 429:
                        budget["blocked"] = True
                        save_budget(state, budget)
                        raise AdapterError(
                            "Invalid publisher Retry-After", "access"
                        )
            if publisher and response.status == 429:
                budget["rate_failures"] += 1
                budget["interval"] = max(budget["interval"], 12.1) * 2
                budget["until"] = max(
                    budget["until"],
                    time.time() + 1800 * 2 ** (budget["rate_failures"] - 1),
                )
                save_budget(state, budget)
                raise AdapterError(
                    "Publisher rate limit; retained backoff", "access", 429
                )
            if publisher and response.status in (401, 403):
                if not robots:
                    budget["blocked"] = True
                    save_budget(state, budget)
                    raise AdapterError(
                        "Publisher refused content: HTTP "
                        + str(response.status),
                        "access",
                        response.status,
                    )
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            return response.status, response.headers, body
    finally:
        if manager is not None:
            budget["completed_at"] = time.time()
            try:
                save_budget(state, budget)
            finally:
                manager.__exit__(None, None, None)


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


def validate_records(records, *, allow_empty=False):
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError(
            "Empty or invalid opportunity collection", "validate"
        )
    ids, urls = set(), set()
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
        if not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", record["id"]):
            raise AdapterError("Invalid opportunity identifier", "validate")
        if record["id"].casefold() in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].casefold())
        if record["url"] in urls:
            raise AdapterError("Duplicate opportunity URL", "validate")
        urls.add(record["url"])
        identity = record.get("source_record_id", "")
        if (
            not isinstance(identity, str)
            or not re.fullmatch(r"\d+", identity)
            or record["url"] != SOURCE_URL + "/detail/" + identity
            or record["attribution"] != ATTRIBUTION
            or record.get("deadline_precision") != "date"
            or record["eligible_countries"] != []
        ):
            raise AdapterError("Invalid vacancy provenance", "validate")
        dates = []
        for field in ("start_date", "end_date", "deadline"):
            value = record.get(field)
            try:
                if not isinstance(value, str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", value
                ):
                    raise ValueError()
                dates.append(date.fromisoformat(value))
            except ValueError as error:
                raise AdapterError(
                    "Invalid vacancy date", "validate"
                ) from error
        if dates[0] > dates[1] or dates[2] > dates[1]:
            raise AdapterError("Inconsistent vacancy dates", "validate")
        minimum, maximum = record.get("age_min"), record.get("age_max")
        if (
            type(minimum) is not int
            or type(maximum) is not int
            or not 0 <= minimum <= maximum <= 120
        ):
            raise AdapterError("Invalid vacancy age range", "validate")
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
                    if field == "deadline" and re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        date.fromisoformat(value)
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
                    "Source changed concurrently; newer remote data was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; durable remote outcome could not be updated",
                    "publish",
                )


def metadata(attempt, previous, records, error=None):
    result = {
        "display_name": "Eurodesk Deutschland",
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


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    prepare_pacing()
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
    begin_phase("publication", 180)
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
                    "Publication failed; durable failure status could not be recorded. Remote data was not force-overwritten."
                )
    elif args.publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


# Standalone source-local durable pacing. Original normalizer/publication
# definitions above retain their original bytes and supported call contracts.
import copy
import errno
import functools
import socket

original_request_bytes = request_bytes
original_read_snapshot = read_snapshot
original_publish_files = publish_files
original_run = run


# Draft only: copied standalone reviewed primitives, source-specific integration
# and ordinary controls are still being authored. This fragment is not executed.
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"

FAMILY = "youthopps-de-rausvonzuhaus-publisher-v1"

FAMILY_SOURCES = {SOURCE_ID}

_WORKFLOW_ID = 378390311

_ARTIFACT_NAME = "rausvonzuhaus-pacing-state"

_STATE_SCHEMA = 3

COLLECTION_TIMEOUT = 570

RUN_TIMEOUT = 1290

EXPORT_TIMEOUT = 30

_STATE_PATH = _STATE_LOCK = None

_STATE_DIR_FD = _STATE_DIR_ID = None

_STATE_INITIALIZING = False

_COLLECTION_LOCK_FD = _COLLECTION_STARTED = None

_COLLECTION_LOCK_ID = None
_COLLECTION_LOCK_GUARD = None

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

_LEGACY_TRANSITION = {'schema': 1,
 'family': 'youthopps-de-rausvonzuhaus-publisher-v1',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/342#issuecomment-6222714638',
 'history_sha256': '94daf53e83ab1fe0c15fc61bb71b87e9ec10e538f84d2ce4c880c7acefe18dca',
 'state': {'schema': 3,
           'family': 'youthopps-de-rausvonzuhaus-publisher-v1',
           'observed_at': 1792177092.2601087,
           'not_before': 1792177152.2601087,
           'starts': [1791490097.0,
                      1791490152.0,
                      1791553597.0,
                      1791623050.0,
                      1791652913.0,
                      1791654955.0,
                      1791669556.0,
                      1791700032.0,
                      1791725496.0,
                      1791757753.0,
                      1791786722.0,
                      1791821173.0,
                      1791853098.0,
                      1791902164.0,
                      1791932975.0,
                      1791955396.0,
                      1791968445.0,
                      1791980585.0,
                      1791999334.0,
                      1792015256.0,
                      1792040333.0,
                      1792048638.0,
                      1792059946.0,
                      1792061090.0,
                      1792075676.2659442,
                      1792075676.271686,
                      1792075689.4015505,
                      1792075689.4054656,
                      1792077165.0,
                      1792079334.8725433,
                      1792079347.782084,
                      1792079365.0],
           'blocked': False,
           'interval': 12.2,
           'completed': 1792079365.0,
           'page_interval': 30,
           'last_inventory_completed': 1792079365.0,
           'rate_limit_strikes': 0,
           'rate_limit_until': 0,
           'legacy_rate_history_unknown': True,
           'in_flight': False,
           'flight_id': None,
           'flight_started': 0,
           'flight_kind': None,
           'completed_flight_id': None,
           'request_id': None,
           'request_started': 0,
           'request_inventory': False,
           'completed_request_id': None,
           'request_outcome': None},
 'legacy_provenance': {'latest': [38763419562,
                                  1,
                                  'de-rausvonzuhaus',
                                  'f77eed642c4544bbeed009440c406064a4f5c6ad'],
                       'artifact': {'archive_download_url': 'https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11986202933/zip',
                                    'created_at': '2026-10-15T15:49:21Z',
                                    'digest': 'sha256:574e964b7a6ec71428596e550bd2a4a28e32568bfa8634f7563ba01cc4b3567e',
                                    'expired': False,
                                    'expires_at': '2027-01-13T15:47:27Z',
                                    'id': 11986202933,
                                    'name': 'rausvonzuhaus-pacing-state-1',
                                    'node_id': 'MDg6QXJ0aWZhY3QxMTk4NjIwMjkzMw==',
                                    'size_in_bytes': 415,
                                    'updated_at': '2026-10-15T15:49:21Z',
                                    'url': 'https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11986202933',
                                    'workflow_run': {'head_branch': 'main',
                                                     'head_repository_id': 1163601208,
                                                     'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                     'id': 38763419562,
                                                     'repository_id': 1163601208}},
                       'producer_sha256': 'a48b990141a7239a8299a2c41b109a5de59baf4bda6f105215e48c6481c1d5a2',
                       'local': {'temporary_parent': {'dev': '34',
                                                      'ino': '1',
                                                      'uid': '65534',
                                                      'mode': '17407'},
                                 'parent': {'dev': '34',
                                            'ino': '109180',
                                            'uid': '1000',
                                            'mode': '16832',
                                            'links': '2',
                                            'bytes': '100',
                                            'mtime_ns': '1792075616264766857',
                                            'ctime_ns': '1792075616264766857'},
                                 'journal': {'dev': '34',
                                             'ino': '109181',
                                             'uid': '1000',
                                             'mode': '33152',
                                             'links': '1',
                                             'bytes': '210',
                                             'mtime_ns': '1792075697642421935',
                                             'ctime_ns': '1792075697642421935'},
                                 'collection_lock': {'dev': '34',
                                                     'ino': '109182',
                                                     'uid': '1000',
                                                     'mode': '33152',
                                                     'links': '1',
                                                     'bytes': '0',
                                                     'mtime_ns': '1792075616263205854',
                                                     'ctime_ns': '1792075616263205854'},
                                 'raw_sha256': '7836e8ddef2d4e76edcdd574c153b46404c061f44f7354db5e996218e9ce2319'}}}
_HOSTED_TRANSITION = {'schema': 1,
 'family': 'youthopps-de-rausvonzuhaus-publisher-v1',
 'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/342#issuecomment-6223878551',
 'history_sha256': '94daf53e83ab1fe0c15fc61bb71b87e9ec10e538f84d2ce4c880c7acefe18dca',
 'state': {'blocked': False,
           'completed': 1792179409.4501636,
           'completed_flight_id': None,
           'completed_request_id': None,
           'family': 'youthopps-de-rausvonzuhaus-publisher-v1',
           'flight_id': None,
           'flight_kind': None,
           'flight_started': 0,
           'in_flight': False,
           'interval': 12.2,
           'last_inventory_completed': 1792179408.4824076,
           'legacy_rate_history_unknown': True,
           'not_before': 1792181587.5854242,
           'observed_at': 1792181527.5854242,
           'page_interval': 30,
           'rate_limit_strikes': 0,
           'rate_limit_until': 0,
           'request_id': None,
           'request_inventory': False,
           'request_outcome': None,
           'request_started': 0,
           'schema': 3,
           'starts': [1791490097.0,
                      1791490152.0,
                      1791553597.0,
                      1791623050.0,
                      1791652913.0,
                      1791654955.0,
                      1791669556.0,
                      1791700032.0,
                      1791725496.0,
                      1791757753.0,
                      1791786722.0,
                      1791821173.0,
                      1791853098.0,
                      1791902164.0,
                      1791932975.0,
                      1791955396.0,
                      1791968445.0,
                      1791980585.0,
                      1791999334.0,
                      1792015256.0,
                      1792040333.0,
                      1792048638.0,
                      1792059946.0,
                      1792061090.0,
                      1792075676.2659442,
                      1792075676.271686,
                      1792075689.4015505,
                      1792075689.4054656,
                      1792077165.0,
                      1792079334.8725433,
                      1792079347.782084,
                      1792079365.0,
                      1792179382.1506057,
                      1792179384.5934987,
                      1792179384.5936615,
                      1792179398.5170927,
                      1792179398.9366617,
                      1792179398.9371703]},
 'legacy_provenance': {'latest': [38763419562,
                                  1,
                                  'de-rausvonzuhaus',
                                  'f77eed642c4544bbeed009440c406064a4f5c6ad'],
                       'artifact': {'archive_download_url': 'https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11986202933/zip',
                                    'created_at': '2026-10-15T15:49:21Z',
                                    'digest': 'sha256:574e964b7a6ec71428596e550bd2a4a28e32568bfa8634f7563ba01cc4b3567e',
                                    'expired': False,
                                    'expires_at': '2027-01-13T15:47:27Z',
                                    'id': 11986202933,
                                    'name': 'rausvonzuhaus-pacing-state-1',
                                    'node_id': 'MDg6QXJ0aWZhY3QxMTk4NjIwMjkzMw==',
                                    'size_in_bytes': 415,
                                    'updated_at': '2026-10-15T15:49:21Z',
                                    'url': 'https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/11986202933',
                                    'workflow_run': {'head_branch': 'main',
                                                     'head_repository_id': 1163601208,
                                                     'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                                                     'id': 38763419562,
                                                     'repository_id': 1163601208}},
                       'producer_sha256': 'a48b990141a7239a8299a2c41b109a5de59baf4bda6f105215e48c6481c1d5a2'}}
_LEGACY_JOB_PINS = {'37837063360:1': {'attempt': 1,
                   'head': '1e020bda0a238fcfa5e1d4435dd9ab2f0f4d17a1',
                   'jobs': [{'id': 113516685799,
                             'run_id': 37837063360,
                             'run_attempt': 1,
                             'head_sha': '1e020bda0a238fcfa5e1d4435dd9ab2f0f4d17a1',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-08T20:07:23Z',
                             'completed_at': '2026-10-08T20:08:17Z'}],
                   'conclusion': 'success'},
 '37837072036:1': {'attempt': 1,
                   'head': '1e020bda0a238fcfa5e1d4435dd9ab2f0f4d17a1',
                   'jobs': [{'id': 113517100415,
                             'run_id': 37837072036,
                             'run_attempt': 1,
                             'head_sha': '1e020bda0a238fcfa5e1d4435dd9ab2f0f4d17a1',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-08T20:08:20Z',
                             'completed_at': '2026-10-08T20:09:12Z'}],
                   'conclusion': 'success'},
 '37894637408:1': {'attempt': 1,
                   'head': '6f1971d163e39e44d46ffd72bec20795c59df105',
                   'jobs': [],
                   'conclusion': 'cancelled'},
 '37939060112:1': {'attempt': 1,
                   'head': 'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa',
                   'jobs': [{'id': 113848814921,
                             'run_id': 37939060112,
                             'run_attempt': 1,
                             'head_sha': 'a889a0d2d31ab5dc0dacd3c4d37ff32b341cb5aa',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-09T13:45:48Z',
                             'completed_at': '2026-10-09T13:46:37Z'}],
                   'conclusion': 'success'},
 '38001501089:1': {'attempt': 1,
                   'head': '2d6491ddf2a37047efc7b996f190f648e647c0c6',
                   'jobs': [],
                   'conclusion': 'cancelled'},
 '38030605791:1': {'attempt': 1,
                   'head': '6e5922277de22b96874c23a705a7d838512c6caf',
                   'jobs': [],
                   'conclusion': 'cancelled'},
 '38040028710:1': {'attempt': 1,
                   'head': 'c55b8d99378274467f2d9a290967dbe10bc25031',
                   'jobs': [{'id': 114178248758,
                             'run_id': 38040028710,
                             'run_attempt': 1,
                             'head_sha': 'c55b8d99378274467f2d9a290967dbe10bc25031',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-10T09:03:21Z',
                             'completed_at': '2026-10-10T09:04:10Z'}],
                   'conclusion': 'success'},
 '38054082131:1': {'attempt': 1,
                   'head': '86312a0c74a6ac3573cbc6b3250d96fbd2dd8488',
                   'jobs': [],
                   'conclusion': 'cancelled'},
 '38071366817:1': {'attempt': 1,
                   'head': '9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4',
                   'jobs': [{'id': 114269274148,
                             'run_id': 38071366817,
                             'run_attempt': 1,
                             'head_sha': '9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-10T17:21:01Z',
                             'completed_at': '2026-10-10T17:21:53Z'}],
                   'conclusion': 'success'},
 '38071840296:1': {'attempt': 1,
                   'head': '9b155b179283c8fa08cf56c1a83a54bd19d51814',
                   'jobs': [{'id': 114276084876,
                             'run_id': 38071840296,
                             'run_attempt': 1,
                             'head_sha': '9b155b179283c8fa08cf56c1a83a54bd19d51814',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-10T17:55:03Z',
                             'completed_at': '2026-10-10T17:55:55Z'}],
                   'conclusion': 'success'},
 '38089644539:1': {'attempt': 1,
                   'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                   'jobs': [{'id': 114323275852,
                             'run_id': 38089644539,
                             'run_attempt': 1,
                             'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-10T21:58:25Z',
                             'completed_at': '2026-10-10T21:59:16Z'}],
                   'conclusion': 'success'},
 '38117907382:1': {'attempt': 1,
                   'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                   'jobs': [{'id': 114406883068,
                             'run_id': 38117907382,
                             'run_attempt': 1,
                             'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-11T06:26:20Z',
                             'completed_at': '2026-10-11T06:27:12Z'}],
                   'conclusion': 'success'},
 '38143285062:1': {'attempt': 1,
                   'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                   'jobs': [{'id': 114481873380,
                             'run_id': 38143285062,
                             'run_attempt': 1,
                             'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-11T13:30:47Z',
                             'completed_at': '2026-10-11T13:31:36Z'}],
                   'conclusion': 'success'},
 '38180389681:1': {'attempt': 1,
                   'head': '10d99f8ef021c092851bf189ff67307865b8760c',
                   'jobs': [{'id': 114591999643,
                             'run_id': 38180389681,
                             'run_attempt': 1,
                             'head_sha': '10d99f8ef021c092851bf189ff67307865b8760c',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-11T22:28:23Z',
                             'completed_at': '2026-10-11T22:29:13Z'}],
                   'conclusion': 'success'},
 '38212670306:1': {'attempt': 1,
                   'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                   'jobs': [{'id': 114690894656,
                             'run_id': 38212670306,
                             'run_attempt': 1,
                             'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-12T06:31:08Z',
                             'completed_at': '2026-10-12T06:32:02Z'}],
                   'conclusion': 'success'},
 '38274638821:1': {'attempt': 1,
                   'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                   'jobs': [{'id': 114897975603,
                             'run_id': 38274638821,
                             'run_attempt': 1,
                             'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-12T16:05:23Z',
                             'completed_at': '2026-10-12T16:06:13Z'}],
                   'conclusion': 'success'},
 '38329759034:1': {'attempt': 1,
                   'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                   'jobs': [{'id': 115076669527,
                             'run_id': 38329759034,
                             'run_attempt': 1,
                             'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-13T00:57:25Z',
                             'completed_at': '2026-10-13T00:58:18Z'}],
                   'conclusion': 'success'},
 '38414273450:1': {'attempt': 1,
                   'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                   'jobs': [{'id': 115355569303,
                             'run_id': 38414273450,
                             'run_attempt': 1,
                             'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-13T14:35:06Z',
                             'completed_at': '2026-10-13T14:36:04Z'}],
                   'conclusion': 'success'},
 '38482121562:1': {'attempt': 1,
                   'head': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                   'jobs': [{'id': 115587856342,
                             'run_id': 38482121562,
                             'run_attempt': 1,
                             'head_sha': '5dff04768f0e558b4d301d5677c5a2c433c3a64b',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-13T23:08:45Z',
                             'completed_at': '2026-10-13T23:09:35Z'}],
                   'conclusion': 'success'},
 '38516031206:1': {'attempt': 1,
                   'head': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                   'jobs': [{'id': 115695681664,
                             'run_id': 38516031206,
                             'run_attempt': 1,
                             'head_sha': '02792bda730b08eeb1eda86a2cd71af80af3cef7',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-14T05:22:26Z',
                             'completed_at': '2026-10-14T05:23:16Z'}],
                   'conclusion': 'success'},
 '38534815307:1': {'attempt': 1,
                   'head': 'e47a87e43a672b1b6429749d1cef4851ea0dedee',
                   'jobs': [{'id': 115769201976,
                             'run_id': 38534815307,
                             'run_attempt': 1,
                             'head_sha': 'e47a87e43a672b1b6429749d1cef4851ea0dedee',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-14T08:59:53Z',
                             'completed_at': '2026-10-14T09:00:45Z'}],
                   'conclusion': 'success'},
 '38562962007:1': {'attempt': 1,
                   'head': '92235b48ad33b9fa377e0736b689010173d7bfab',
                   'jobs': [{'id': 115849432630,
                             'run_id': 38562962007,
                             'run_attempt': 1,
                             'head_sha': '92235b48ad33b9fa377e0736b689010173d7bfab',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-14T12:22:11Z',
                             'completed_at': '2026-10-14T12:23:05Z'}],
                   'conclusion': 'success'},
 '38607818404:1': {'attempt': 1,
                   'head': '92235b48ad33b9fa377e0736b689010173d7bfab',
                   'jobs': [{'id': 116003125102,
                             'run_id': 38607818404,
                             'run_attempt': 1,
                             'head_sha': '92235b48ad33b9fa377e0736b689010173d7bfab',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-14T17:34:41Z',
                             'completed_at': '2026-10-14T17:35:34Z'}],
                   'conclusion': 'success'},
 '38643890833:1': {'attempt': 1,
                   'head': '0ee73c37e42124c79f0c2005d14afc5db2c8e511',
                   'jobs': [{'id': 116127873410,
                             'run_id': 38643890833,
                             'run_attempt': 1,
                             'head_sha': '0ee73c37e42124c79f0c2005d14afc5db2c8e511',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-14T22:00:06Z',
                             'completed_at': '2026-10-14T22:00:56Z'}],
                   'conclusion': 'success'},
 '38684235821:1': {'attempt': 1,
                   'head': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                   'jobs': [{'id': 116255335339,
                             'run_id': 38684235821,
                             'run_attempt': 1,
                             'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-15T04:58:01Z',
                             'completed_at': '2026-10-15T04:58:53Z'}],
                   'conclusion': 'success'},
 '38685076409:1': {'attempt': 1,
                   'head': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                   'jobs': [{'id': 116297848001,
                             'run_id': 38685076409,
                             'run_attempt': 1,
                             'head_sha': '9b97e13c2e2a56ee77700e40202eb6acd2212cf5',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-15T07:16:23Z',
                             'completed_at': '2026-10-15T07:17:18Z'}],
                   'conclusion': 'success'},
 '38720083560:1': {'attempt': 1,
                   'head': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                   'jobs': [{'id': 116372205611,
                             'run_id': 38720083560,
                             'run_attempt': 1,
                             'head_sha': 'a62a88f5cad62f4fb1ef5230358557d37d2b2bba',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-15T10:24:53Z',
                             'completed_at': '2026-10-15T10:25:46Z'}],
                   'conclusion': 'success'},
 '38721259609:1': {'attempt': 1,
                   'head': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                   'jobs': [{'id': 116376106244,
                             'run_id': 38721259609,
                             'run_attempt': 1,
                             'head_sha': 'cb14a94366e7c9f3a518a6bd9f8a3da5331da39f',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'failure',
                             'started_at': '2026-10-15T10:41:25Z',
                             'completed_at': '2026-10-15T10:44:50Z'}],
                   'conclusion': 'failure'},
 '38758018782:1': {'attempt': 1,
                   'head': '9e1882fd6073e0794974a1c16115f49175cb4d2b',
                   'jobs': [{'id': 116501528256,
                             'run_id': 38758018782,
                             'run_attempt': 1,
                             'head_sha': '9e1882fd6073e0794974a1c16115f49175cb4d2b',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-15T15:10:51Z',
                             'completed_at': '2026-10-15T15:12:45Z'}],
                   'conclusion': 'success'},
 '38763419562:1': {'attempt': 1,
                   'head': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                   'jobs': [{'id': 116520331505,
                             'run_id': 38763419562,
                             'run_attempt': 1,
                             'head_sha': 'f77eed642c4544bbeed009440c406064a4f5c6ad',
                             'head_branch': 'main',
                             'name': 'collect',
                             'status': 'completed',
                             'conclusion': 'success',
                             'started_at': '2026-10-15T15:47:30Z',
                             'completed_at': '2026-10-15T15:49:25Z'}],
                   'conclusion': 'success'}}
_FIRST_HOSTED_PENDING = False
_INVENTORY_ACTIVE = False


# Source-specific lifecycle integration draft. No top-level execution.
_PREPUBLICATION_END = None
_PUBLICATION_END = None
_EXPORT_END = None
_PREWIRE_PROOF_END = None
_SOURCE_END = None
_INITIAL_SNAPSHOT_ATTEMPTED = False
_ORIGINAL_PREPARED = False
_ARTIFACT_CONTEXT = None


def active_deadline():
    if _RUN_END is None or _PHASE_END is None:
        raise AdapterError("Source execution deadline required", "access")
    return min(_RUN_END, _PHASE_END)


def check_deadline():
    if time.monotonic() >= active_deadline():
        raise AdapterError("Source lifecycle deadline exhausted", _PHASE_NAME)


def request_timeout(publisher=False):
    # The baseline is unpaced for nonpublisher HTTP and clips its30s timeout
    # to the current phase. No donor GH6/15s loop or120s guard is introduced.
    check_deadline()
    return min(30, active_deadline() - time.monotonic())


def alarm_expired(signum, frame):
    raise AdapterError("Source phase deadline exhausted", _PHASE_NAME)


def begin_phase(name, seconds):
    global _PHASE_NAME, _PHASE_END, _PUBLICATION_END, _EXPORT_END
    now = time.monotonic()
    if name == "publication":
        if _PUBLICATION_END is None:
            _PUBLICATION_END = min(now + 180, _RUN_END - 30)
        end = _PUBLICATION_END
    elif name == "export":
        if _EXPORT_END is None:
            _EXPORT_END = min(now + 30, _RUN_END)
        end = _EXPORT_END
    else:
        end = min(now + seconds, _PREPUBLICATION_END, _RUN_END - 210)
    if end <= now:
        raise AdapterError("Source phase deadline exhausted", name)
    _PHASE_NAME, _PHASE_END = name, end
    signal.setitimer(signal.ITIMER_REAL, end - now)


@contextlib.contextmanager
def phase(name, seconds):
    global _PHASE_NAME, _PHASE_END, _SOURCE_END
    if name in _PHASE_SEEN:
        raise AdapterError("Source phase cannot renew", name)
    _PHASE_SEEN.add(name)
    prior = (_PHASE_NAME, _PHASE_END)
    begin_phase(name, seconds)
    if name == "collection":
        _SOURCE_END = _PHASE_END
    try:
        yield
        check_deadline()
    finally:
        _PHASE_NAME, _PHASE_END = prior
        remaining = min(_RUN_END, _PHASE_END) - time.monotonic()
        # Keep the old absolute phase value for API admission. If it has
        # expired, leave only the whole-entry alarm armed long enough for the
        # original run body to enter its one publication/fallback window.
        # This does not grant a fresh prepublication or collection allowance.
        alarm_remaining = (
            remaining if remaining > 0 else _RUN_END - time.monotonic()
        )
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, alarm_remaining))


@contextlib.contextmanager
def operation_guard():
    # One original phase alarm governs I/O. Do not install a competing timer.
    check_deadline()
    yield
    check_deadline()


def bounded_infrastructure(function):
    @functools.wraps(function)
    def bounded(*args, **kwargs):
        with operation_guard():
            return function(*args, **kwargs)

    return bounded


def nonexport_end():
    return _RUN_END - 30


def read_snapshot():
    global _INITIAL_SNAPSHOT_ATTEMPTED
    if _PHASE_NAME == "publication":
        check_deadline()
        return original_read_snapshot()
    if _INITIAL_SNAPSHOT_ATTEMPTED:
        raise AdapterError("Initial snapshot cannot renew", "publish")
    _INITIAL_SNAPSHOT_ATTEMPTED = True
    with phase("snapshot", 120):
        return original_read_snapshot()


def publish_files(snapshot, files):
    if _PHASE_NAME != "publication" or _PUBLICATION_END is None:
        raise AdapterError("Original publication window required", "publish")
    check_deadline()
    # Both successful publication and every metadata-only fallback share this
    # SAME fixed180s deadline, including original fallback snapshot and CAS3.
    return original_publish_files(snapshot, files)


@contextlib.contextmanager
def execution():
    global _RUN_END, _ENTRY_MONOTONIC, _PHASE_END, _PHASE_NAME
    global _PREPUBLICATION_END, _PUBLICATION_END, _EXPORT_END, _SOURCE_END
    global _RESTORED, _STATE_TRUSTED, _LOCAL_FIRST_WIRE_VERIFIED
    global _INITIAL_SNAPSHOT_ATTEMPTED, _PHASE_SEEN, _ORIGINAL_PREPARED
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID, _ARTIFACT_CONTEXT
    global _COLLECTION_LOCK_GUARD
    if _RUN_END is not None or signal.getitimer(signal.ITIMER_REAL) != (0, 0):
        raise AdapterError("Exclusive execution and timer required", "access")
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Publisher persistence failure remains sticky", "access"
        )
    _ENTRY_MONOTONIC = time.monotonic()
    _RUN_END = _ENTRY_MONOTONIC + 1290
    _PREPUBLICATION_END = _ENTRY_MONOTONIC + 900
    _PUBLICATION_END = _EXPORT_END = _SOURCE_END = None
    _RESTORED = _STATE_TRUSTED = _LOCAL_FIRST_WIRE_VERIFIED = False
    _INITIAL_SNAPSHOT_ATTEMPTED = _ORIGINAL_PREPARED = False
    _PHASE_SEEN = set()
    _ROBOTS.clear()
    _REP_TARGETS.clear()
    old_handler = signal.signal(signal.SIGALRM, alarm_expired)
    begin_phase("prepublication", 900)
    try:
        yield
    finally:
        primary = sys.exc_info()[1]
        cleanup = None
        collection = (_COLLECTION_LOCK_FD, _COLLECTION_LOCK_GUARD)
        identity = _COLLECTION_LOCK_ID
        _COLLECTION_LOCK_FD = _COLLECTION_LOCK_GUARD = _COLLECTION_LOCK_ID = (
            None
        )
        safe = []
        for descriptor in collection:
            if descriptor is None:
                continue
            try:
                verify_lock_description(descriptor, identity)
                safe.append(descriptor)
            except BaseException as error:
                cleanup = cleanup or error
                journal_failure()
        for descriptor in safe:
            try:
                os.close(descriptor)
            except BaseException as error:
                cleanup = cleanup or error
                journal_failure()
        try:
            close_legacy_authority()
        except BaseException as error:
            cleanup = cleanup or error
            journal_failure()
        try:
            close_journal()
        except BaseException as error:
            cleanup = cleanup or error
            journal_failure()
        finally:
            manager, _ARTIFACT_CONTEXT = _ARTIFACT_CONTEXT, None
            if manager is not None:
                error = primary or cleanup
                if error is None and _PUBLISHER_GATE_FAILED:
                    error = AdapterError(
                        "Publisher export became untrusted", "access"
                    )
                    cleanup = error
                try:
                    manager.__exit__(
                        type(error) if error else None,
                        error,
                        error.__traceback__ if error else None,
                    )
                except BaseException as error:
                    cleanup = cleanup or error
                    journal_failure()
            if _PUBLISHER_GATE_FAILED:
                path = os.environ.get("RAUS_PACING_ARTIFACT_PATH")
                if path:
                    with contextlib.suppress(BaseException):
                        with artifact_output(path, cleanup_only=True):
                            pass
                with contextlib.suppress(BaseException):
                    pacing_not_ready()
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
            _RUN_END = _ENTRY_MONOTONIC = _PHASE_END = _PHASE_NAME = None
        if primary is None and cleanup is not None:
            raise cleanup


def prepare_pacing():
    global _ORIGINAL_PREPARED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Publisher persistence failure remains sticky", "access"
        )
    if _ORIGINAL_PREPARED:
        return
    with phase("authentication", 30):
        workflow_mode_guard()
        current_job_context()
    with phase("restore", 180):
        prepare_collection()
    _ORIGINAL_PREPARED = True


def collect():
    global _COLLECTION_STARTED
    if "DATA_SOURCE_TOKEN" in os.environ and not _PUBLISHING:
        raise AdapterError(
            "Nonpublishing collection refuses publication credentials", "access"
        )
    if _RUN_END is None:
        with execution():
            try:
                return collect()
            finally:
                finish_export()
    prepare_pacing()
    with phase("collection", 570):
        _COLLECTION_STARTED = time.time()
        return collect_inventory()


def main():
    global _PUBLISHING
    _PUBLISHING = "--publish" in sys.argv[1:]
    if _PUBLISHING and os.environ.get("GITHUB_ACTIONS") != "true":
        raise AdapterError("Hosted publication context required", "publish")
    with execution():
        try:
            return original_run()
        finally:
            finish_export()


def finish_export():
    """Keep an existing source/publication exception primary on export error."""
    primary = sys.exc_info()[1]
    try:
        begin_phase("export", 30)
        export_family_artifact()
    except BaseException:
        journal_failure()
        if primary is None:
            raise


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


def numeric(value):
    try:
        return (
            type(value) in (int, float) and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


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
        if _COLLECTION_LOCK_FD is not None:
            verify_lock_description(_COLLECTION_LOCK_GUARD, _COLLECTION_LOCK_ID)
            verify_lock_description(_COLLECTION_LOCK_FD, _COLLECTION_LOCK_ID)
            named = os.stat(
                "collection.lock", dir_fd=_STATE_DIR_FD, follow_symlinks=False
            )
            if (named.st_dev, named.st_ino) != _COLLECTION_LOCK_ID:
                raise AdapterError("Collection lock name changed", "access")
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
    descriptor = None
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
        if descriptor is not None and _JOURNAL_FD != descriptor:
            with contextlib.suppress(BaseException):
                os.close(descriptor)
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
        before = verify_journal()
        raw = bytearray()
        while len(raw) <= 16384:
            part = os.pread(_JOURNAL_FD, min(4096, 16385 - len(raw)), len(raw))
            if not part:
                break
            raw.extend(part)
        verify_journal()
        if len(raw) != before.st_size or len(raw) > 16384:
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
    global _STATE_INITIALIZING, _FAILED_BUDGET
    global _JOURNAL_CREATED, _JOURNAL_DIRTY, _JOURNAL_VERSION
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError(
            "Rausvonzuhaus state persistence is already unhealthy", "access"
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
                "Unproved Rausvonzuhaus completion write", "access"
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
            if wait > _PHASE_END - time.monotonic():
                raise AdapterError(
                    "Inventory gap exceeds phase budget", "access"
                )
            if time.time() + wait > _COLLECTION_STARTED + COLLECTION_TIMEOUT:
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
            if entry.external_attr >> 16 & 0o170000 not in (0, 0o100000):
                raise ValueError("State member is not a regular file")
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
        with contextlib.suppress(BaseException):
            pacing_not_ready()
        if descriptor is not None and parent is not None:
            with contextlib.suppress(Exception):
                verify_artifact_directory(root, parent)
                verify_artifact_target(path, descriptor)
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
        raise
    finally:
        primary = sys.exc_info()[1]
        errors = []
        for candidate in (parent, descriptor):
            if candidate is None:
                continue
            try:
                os.close(candidate)
            except BaseException as error:
                journal_failure()
                errors.append(error)
                if descriptor is not None:
                    with contextlib.suppress(BaseException):
                        os.ftruncate(descriptor, 0)
                        os.fsync(descriptor)
                with contextlib.suppress(BaseException):
                    pacing_not_ready()
        if primary is None and errors:
            raise errors[0]


def workflow_mode_guard():
    """Check workflow state before history, journal creation or access."""
    result = workflow_api("/actions/workflows/fetch-de-rausvonzuhaus.yml")
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        result.get("id") != _WORKFLOW_ID
        or result.get("path") != ".github/workflows/fetch-de-rausvonzuhaus.yml"
        or result.get("state") != ("active" if hosted else "disabled_manually")
    ):
        raise AdapterError(
            "Rausvonzuhaus workflow is not serialized for this mode",
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
            "fetch-de-rausvonzuhaus.yml@refs/heads/main"
        ):
            raise AdapterError(
                "Unexpected immutable Rausvonzuhaus workflow head",
                "access",
            )


def production_maintenance_state(incoming):
    """Preserve full native MAX on the first production family transition."""
    raw = os.environ.get("RAUS_PACING_MAINTENANCE", "")
    if not raw:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise AdapterError(
                "Full-native Rausvonzuhaus MAX approval required", "access"
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
                r"https://github\.com/YouthOpps/data-pipeline/issues/342"
                r"#issuecomment-[1-9][0-9]*",
                approval["evidence"],
            )
        ):
            raise ValueError("Untrusted full-native approval")
        declaration = reviewed_declaration(hosted=True)
        if approval != {
            "schema": 1,
            "family": FAMILY,
            "evidence": declaration["evidence"],
            "state": declaration["state"],
        }:
            raise ValueError("Delivered workflow and HOSTED declaration differ")
        return merge_budgets(incoming, approval["state"], time.time())
    except Exception:
        raise AdapterError(
            "Invalid full-native Rausvonzuhaus approval", "access"
        ) from None


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
    run = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    validate_family_identity(run, expected)
    if (
        run.get("status") != "in_progress"
        or os.environ.get("GITHUB_JOB") != "collect"
    ):
        raise AdapterError(
            "Rausvonzuhaus current job is not executing", "access"
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
            "Rausvonzuhaus current job inventory incomplete", "access"
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
            "Rausvonzuhaus current job ownership mismatch", "access"
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
            "Untrusted Rausvonzuhaus job clock", "access"
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
        or len(state["starts"]) > 128
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
        if ready > now + (active_deadline() - time.monotonic()):
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
            if wait > active_deadline() - time.monotonic():
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
        ) == "rausvonzuhaus.de"
    except (TypeError, ValueError):
        return False


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower()
        not in {"rausvonzuhaus.de", "www.rausvonzuhaus.de"}
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        raise AdapterError("Unreviewed Rausvonzuhaus source route", "access")
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


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
    """One physical publisher attempt; nonpublisher HTTP stays original."""
    global _LOCAL_FIRST_WIRE_VERIFIED
    if not is_publisher(url):
        if robots or inventory:
            raise AdapterError("Source purpose on nonpublisher HTTP", "access")
        return original_request_bytes(
            url, method=method, headers=headers, payload=payload
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
            prewire_coordination()
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
                raise PolicyStop("Publisher stopped: HTTP 429", "access", 429)
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
            if status == 429 or not robots and status in (401, 403, 451):
                raise PolicyStop(
                    "Publisher stopped: HTTP " + str(status), "access", status
                )
        # A complete body alone does not establish healthy transport completion.
        # A response-close failure must retain the pending request and stop retry.
        frame["assessed"] = True
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
                    headers={
                        "Accept": (
                            "text/plain,*/*;q=0.8"
                            if robots
                            else "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"
                        )
                    },
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
        except PolicyStop as error:
            if attempt or error.status not in (500, 502, 503, 504):
                raise
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
        pin = (_LEGACY_JOB_PINS or {}).get(attempt_key(run_id, attempt))
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


# Complete exact-attempt source history. No mutable updated_at ordering.


def attempt_key(run_id, attempt):
    return str(run_id) + ":" + str(attempt)


def scan_family_history():
    global _HISTORY_COMPLETED, _HISTORY_JOBLESS
    _HISTORY_JOBLESS = []
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    seen, candidates = set(), []
    expected_total = None
    current_seen = False
    for page_number in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-"
            + SOURCE_ID
            + ".yml/runs"
            + f"?per_page=100&page={page_number}"
        )
        total, rows = result.get("total_count"), result.get("workflow_runs")
        if (
            type(total) is not int
            or total < 0
            or not isinstance(rows, list)
            or len(rows) > 100
        ):
            raise AdapterError("Invalid complete source history", "access")
        if expected_total is None:
            expected_total = total
        if total != expected_total:
            raise InventoryRace(
                "Source history changed during restore", "access"
            )
        for run in rows:
            run_id, last_attempt, source, head = validate_family_identity(run)
            if run_id in seen:
                raise AdapterError("Duplicate source wrapper", "access")
            seen.add(run_id)
            active_current = current and run_id == current[0]
            if active_current:
                if (run_id, last_attempt) != current or head != os.environ.get(
                    "GITHUB_SHA"
                ):
                    raise AdapterError(
                        "Current source attempt changed", "access"
                    )
                current_seen = True
            elif run.get("status") != "completed":
                if not hosted or run.get("status") not in {
                    "queued",
                    "waiting",
                    "pending",
                    "requested",
                }:
                    raise AdapterError(
                        "Another source collector is active", "access"
                    )
            final_completed = (
                last_attempt
                if run.get("status") == "completed"
                else last_attempt - 1
            )
            if active_current:
                final_completed = last_attempt - 1
            for attempt in range(1, final_completed + 1):
                # Identity projection only. completed_identity performs the
                # genuine exact-attempt and job GETs before accepting it.
                identity_projection = dict(run, run_attempt=attempt)
                row = completed_identity(
                    identity_projection, (run_id, attempt, source, head)
                )
                if row is not None:
                    candidates.append(row)
        if len(seen) == total:
            if hosted and not current_seen:
                raise AdapterError(
                    "Current source absent from full history", "access"
                )
            _HISTORY_COMPLETED = sorted(candidates)
            if (
                len(candidates) > 1
                and _HISTORY_COMPLETED[-1][0] == _HISTORY_COMPLETED[-2][0]
            ):
                raise AdapterError("Ambiguous exact job chronology", "access")
            return _HISTORY_COMPLETED[-1][2:] if candidates else None
        if len(seen) > total or len(rows) != 100:
            raise AdapterError("Truncated complete source history", "access")
    raise AdapterError(
        "Source history exceeds reviewed metadata bound", "access"
    )


def current_wrapper_projection():
    """Fresh full wrapper fence; immutable exact jobs were proved at restore."""
    seen, projection = set(), []
    expected_total = None
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current = current_run_identity() if hosted else None
    for page_number in range(1, 101):
        result = workflow_api(
            "/actions/workflows/fetch-"
            + SOURCE_ID
            + ".yml/runs"
            + f"?per_page=100&page={page_number}"
        )
        total, rows = result.get("total_count"), result.get("workflow_runs")
        if (
            type(total) is not int
            or not isinstance(rows, list)
            or len(rows) > 100
        ):
            raise AdapterError("Incomplete current wrapper fence", "access")
        if expected_total is None:
            expected_total = total
        if total != expected_total:
            raise AdapterError("Wrapper fence changed during quiet", "access")
        for row in rows:
            run_id, attempt, source, head = validate_family_identity(row)
            if run_id in seen:
                raise AdapterError("Duplicate current wrapper fence", "access")
            seen.add(run_id)
            status = row.get("status")
            if current and run_id == current[0]:
                if (
                    (run_id, attempt) != current
                    or status != "in_progress"
                    or head != os.environ.get("GITHUB_SHA")
                ):
                    raise AdapterError(
                        "Current hosted wrapper changed", "access"
                    )
            elif status != "completed":
                raise AdapterError(
                    "Another collector overlaps startup", "access"
                )
            projection.append(
                (run_id, attempt, source, head, status, row.get("conclusion"))
            )
        if len(seen) == total:
            if hosted and current[0] not in seen:
                raise AdapterError(
                    "Current job missing from wrapper fence", "access"
                )
            return sorted(projection)
        if len(seen) > total or len(rows) != 100:
            raise AdapterError("Truncated current wrapper fence", "access")
    raise AdapterError("Current wrapper fence exceeds metadata bound", "access")


# Private finite continuity draft. All declarations stay None until ROOT grants.
_LEGACY_PARENT_FD = _LEGACY_LOCK_FD = _LEGACY_PARENT_ID = None
_NEW_NAMESPACE_GRANTED = False
_EXCLUSIVE_INITIALIZATION = False
_RESTORED_OWNER = _RESTORED_ARTIFACT = _RESTORED_WRAPPER_FENCE = None


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def validate_legacy_budget(value):
    required = {"starts", "interval", "until"}
    optional = {
        "blocked",
        "completed_at",
        "rate_failures",
        "legacy_rate_history_unknown",
    }
    if (
        not isinstance(value, dict)
        or not required <= set(value) <= required | optional
    ):
        raise AdapterError("Unreviewed original journal shape", "access")
    if not isinstance(value["starts"], list) or len(value["starts"]) > 128:
        raise AdapterError("Invalid original timing history", "access")
    if value["starts"] != sorted(set(value["starts"])):
        raise AdapterError("Invalid original timing order", "access")
    numbers = [value["interval"], value["until"], *value["starts"]]
    if "completed_at" in value:
        numbers.append(value["completed_at"])
    if any(not numeric(number) or number > 253402300799 for number in numbers):
        raise AdapterError("Invalid original publisher clock", "access")
    if value["interval"] < 6:
        raise AdapterError(
            "Original interval is below original schema", "access"
        )
    if "blocked" in value and type(value["blocked"]) is not bool:
        raise AdapterError("Invalid original hold", "access")
    if "rate_failures" in value and (
        type(value["rate_failures"]) is not int or value["rate_failures"] < 0
    ):
        raise AdapterError("Invalid original strike history", "access")
    if (
        "legacy_rate_history_unknown" in value
        and type(value["legacy_rate_history_unknown"]) is not bool
    ):
        raise AdapterError("Invalid original ancestry", "access")
    return value


def reviewed_declaration(hosted=False):
    declaration = _HOSTED_TRANSITION if hosted else _LEGACY_TRANSITION
    expected = {
        "schema",
        "family",
        "evidence",
        "history_sha256",
        "state",
        "legacy_provenance",
    }
    if not isinstance(declaration, dict) or set(declaration) != expected:
        raise AdapterError(
            "Finite source continuity approval required", "access"
        )
    if (
        type(declaration["schema"]) is not int
        or declaration["schema"] != 1
        or declaration["family"] != FAMILY
        or not isinstance(declaration["evidence"], str)
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/342"
            r"#issuecomment-[1-9][0-9]*",
            declaration["evidence"],
        )
        or not isinstance(_LEGACY_JOB_PINS, dict)
        or not _LEGACY_JOB_PINS
        or declaration["history_sha256"] != canonical_digest(_LEGACY_JOB_PINS)
        or not isinstance(declaration["legacy_provenance"], dict)
    ):
        raise AdapterError(
            "Exact source continuity declaration changed", "access"
        )
    state = validate_budget(copy.deepcopy(declaration["state"]))
    if state["in_flight"]:
        raise AdapterError(
            "Pending history cannot initialize a source", "access"
        )
    predecessor = validate_legacy_budget(_PREDECESSOR_NATIVE_STATE)
    predecessor_bounds = [
        datetime.fromisoformat(row[3].replace("Z", "+00:00")).timestamp()
        for row in _PREDECESSOR_REVIEWED_HISTORY.values()
        if row[3] is not None
    ]
    legacy_MAX_required(state, predecessor, predecessor_bounds)
    if state["not_before"] < _PREDECESSOR_NOT_BEFORE:
        raise AdapterError("Original reviewed guard was weakened", "access")
    return declaration


def exact_legacy_history(declaration):
    actual = {attempt_key(row[2], row[3]): row for row in _HISTORY_COMPLETED}
    jobless = {attempt_key(row[0], row[1]): row for row in _HISTORY_JOBLESS}
    if set(actual) | set(jobless) != set(_LEGACY_JOB_PINS) or set(actual) & set(
        jobless
    ):
        raise AdapterError(
            "Complete original attempt inventory changed", "access"
        )
    bounds = []
    for key, pin in _LEGACY_JOB_PINS.items():
        run_id, key_attempt = key.split(":")
        if not run_id.isdigit() or key_attempt != str(pin["attempt"]):
            raise AdapterError("Untrusted pinned attempt key", "access")
        result = workflow_api(
            f"/actions/runs/{run_id}/attempts/{pin['attempt']}/jobs?per_page=100"
        )
        jobs = result.get("jobs")
        if (
            not isinstance(jobs, list)
            or result.get("total_count") != len(pin["jobs"])
            or len(jobs) != len(pin["jobs"])
        ):
            raise AdapterError("Original exact job inventory changed", "access")
        if any(
            {field: row.get(field) for field in expected} != expected
            for row, expected in zip(
                sorted(jobs, key=lambda row: row.get("id", 0)),
                sorted(pin["jobs"], key=lambda row: row["id"]),
            )
        ):
            raise AdapterError("Original exact job identity changed", "access")
        if pin["jobs"]:
            starts = [
                datetime.fromisoformat(
                    row["started_at"].replace("Z", "+00:00")
                ).timestamp()
                for row in pin["jobs"]
            ]
            ends = [
                datetime.fromisoformat(
                    row["completed_at"].replace("Z", "+00:00")
                ).timestamp()
                for row in pin["jobs"]
            ]
            wanted = (
                max(ends),
                min(starts),
                int(run_id),
                pin["attempt"],
                SOURCE_ID,
                pin["head"],
            )
            if actual.get(key) != wanted:
                raise AdapterError(
                    "Original completion chronology changed", "access"
                )
            bounds.extend(ends)
        elif jobless.get(key) != (
            int(run_id),
            pin["attempt"],
            SOURCE_ID,
            pin["head"],
        ):
            raise AdapterError("Original jobless provenance changed", "access")
    if not bounds:
        raise AdapterError("No actual original job bounds", "access")
    return bounds


def legacy_MAX_required(seed, old, bounds):
    known = set(old["starts"]) | set(bounds)
    if (
        not known <= set(seed["starts"])
        or seed["interval"] < old["interval"]
        or seed["not_before"] < old["until"]
        or bounds
        and seed["completed"] < max(bounds)
        or "completed_at" in old
        and (
            seed["completed"] < old["completed_at"]
            or seed["last_inventory_completed"] < old["completed_at"]
        )
        or bounds
        and seed["last_inventory_completed"] < max(bounds)
        or seed["rate_limit_strikes"] < old.get("rate_failures", 0)
        or old.get("blocked", False)
        and not seed["blocked"]
        or not seed["legacy_rate_history_unknown"]
    ):
        raise AdapterError(
            "Full original conservative constraints missing", "access"
        )


def newest_artifact(latest):
    run_id, attempt, source, head = latest
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
        if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
    ]
    if len(matches) != 1:
        raise AdapterError("Newest exact source state is required", "access")
    artifact = matches[0]
    if (
        artifact.get("expired") is not False
        or type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("workflow_run", {}).get("id") != run_id
        or artifact.get("workflow_run", {}).get("head_sha") != head
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
    ):
        raise AdapterError("Newest artifact ownership changed", "access")
    return artifact


def legacy_producer(provenance, head):
    value = workflow_api(
        "/contents/adapters/" + SOURCE_ID + "/adapter.py?ref=" + head
    )
    try:
        if (
            value["encoding"] != "base64"
            or value["path"] != "adapters/" + SOURCE_ID + "/adapter.py"
        ):
            raise ValueError()
        raw = base64.b64decode(
            re.sub(r"[ \t\r\n]+", "", value["content"]), validate=True
        )
        if (
            len(raw) != value["size"]
            or hashlib.sha256(raw).hexdigest() != provenance["producer_sha256"]
            or hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()
            != value["sha"]
        ):
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise AdapterError(
            "Immutable original producer binding changed", "access"
        ) from None


def legacy_artifact_state(latest, artifact):
    # Keep the original authenticated redirect/two-hop transport. No synthetic
    # cache route or decoded-state injection is part of a native restore.
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    status, response_headers, _ = original_request_bytes(
        _WORKFLOW_GITHUB + "/actions/artifacts/" + str(artifact["id"]) + "/zip",
        headers=headers,
    )
    destination = response_headers.get("Location", "")
    parsed = urllib.parse.urlsplit(destination)
    if (
        status != 302
        or parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or not (parsed.hostname or "").endswith(
            (".blob.core.windows.net", ".githubusercontent.com")
        )
    ):
        raise AdapterError("Unsafe original signed artifact route", "access")
    status, _, archive = original_request_bytes(destination)
    if (
        status != 200
        or len(archive) != artifact["size_in_bytes"]
        or "sha256:" + hashlib.sha256(archive).hexdigest() != artifact["digest"]
    ):
        raise AdapterError("Original owned archive digest changed", "access")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        rows = zipped.infolist()
        if len(rows) != 1:
            raise AdapterError(
                "Unexpected original archive inventory", "access"
            )
        row = rows[0]
        if (
            row.filename != "pacing-state.json"
            or row.file_size > 16384
            or row.flag_bits & 1
            or row.external_attr >> 16 & 0o170000 not in (0, 0o100000)
        ):
            raise AdapterError("Unsafe original owned member", "access")
        with zipped.open(row) as member:
            raw = member.read(16385)
            if len(raw) != row.file_size or len(raw) > 16384 or member.read(1):
                raise AdapterError("Incomplete original owned member", "access")
    envelope = json.loads(raw)
    expected = {
        "schema": 1,
        "repository": "YouthOpps/data-pipeline",
        "run_id": latest[0],
        "run_attempt": latest[1],
        "source": SOURCE_ID,
        "head_sha": latest[3],
    }
    if (
        not isinstance(envelope, dict)
        or set(envelope) != set(expected) | {"state"}
        or any(
            type(envelope[k]) is not type(v) or envelope[k] != v
            for k, v in expected.items()
        )
    ):
        raise AdapterError(
            "Original seven-key owner envelope changed", "access"
        )
    if set(envelope["state"]) != {
        "starts",
        "interval",
        "until",
        "blocked",
        "completed_at",
        "rate_failures",
        "legacy_rate_history_unknown",
    }:
        raise AdapterError(
            "Original producer's complete seven-field state required", "access"
        )
    return validate_legacy_budget(envelope["state"])


def restored_state():
    global _RESTORED_OWNER, _RESTORED_ARTIFACT, _RESTORED_WRAPPER_FENCE
    global _EXCLUSIVE_INITIALIZATION
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Complete source history is required", "access")
    artifact = newest_artifact(latest)
    _RESTORED_OWNER, _RESTORED_ARTIFACT = latest, artifact
    _RESTORED_WRAPPER_FENCE = current_wrapper_projection()
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if (
        isinstance(_LEGACY_JOB_PINS, dict)
        and _LEGACY_JOB_PINS
        and all(
            attempt_key(row[2], row[3]) in _LEGACY_JOB_PINS
            and row[3]
            == _LEGACY_JOB_PINS[attempt_key(row[2], row[3])]["attempt"]
            and row[5] == _LEGACY_JOB_PINS[attempt_key(row[2], row[3])]["head"]
            for row in _HISTORY_COMPLETED
        )
    ):
        declaration = reviewed_declaration(hosted)
        modern_path = os.path.join(
            tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
        )
        if os.path.lexists(modern_path):
            raise AdapterError(
                "First legacy migration requires absent modern namespace",
                "access",
            )
        _EXCLUSIVE_INITIALIZATION = True
        bounds = exact_legacy_history(declaration)
        provenance = declaration["legacy_provenance"]
        if (
            provenance.get("latest") != list(latest)
            or provenance.get("artifact") != artifact
        ):
            raise AdapterError("Reviewed newest legacy owner changed", "access")
        legacy_producer(provenance, latest[3])
        old = legacy_artifact_state(latest, artifact)
        state = validate_budget(copy.deepcopy(declaration["state"]))
        legacy_MAX_required(state, old, bounds)
        if not hosted:
            verify_legacy_local_and_claim(declaration)
        else:
            global _NEW_NAMESPACE_GRANTED
            _NEW_NAMESPACE_GRANTED = True
        return production_maintenance_state(state)
    incoming = download_inert_artifact(
        latest, artifact["id"], artifact["digest"]
    )
    if hosted:
        _NEW_NAMESPACE_GRANTED = True
    return production_maintenance_state(incoming)


# Finite present-legacy migration only. An absent LOCAL predecessor requires a
# separate future reviewed design; no generic missing-parent initializer exists.
_LEGACY_PARENT_FD = _LEGACY_PARENT_ID = None
_LEGACY_LOCK_FD = _LEGACY_LOCK_ID = _LEGACY_LOCK_GUARD = None
_LEGACY_ANCESTOR_FD = _LEGACY_JOURNAL_FD = _LEGACY_CLAIM_FD = None
_LEGACY_AUTHORITY_PROOF = None


def source_file_identity(info):
    return {
        key: str(getattr(info, attribute))
        for key, attribute in (
            ("dev", "st_dev"),
            ("ino", "st_ino"),
            ("uid", "st_uid"),
            ("mode", "st_mode"),
            ("links", "st_nlink"),
            ("bytes", "st_size"),
            ("mtime_ns", "st_mtime_ns"),
            ("ctime_ns", "st_ctime_ns"),
        )
    }


def verify_legacy_local_and_claim(declaration):
    global _LEGACY_PARENT_FD, _LEGACY_PARENT_ID
    global _LEGACY_LOCK_FD, _LEGACY_LOCK_ID, _LEGACY_LOCK_GUARD
    global _NEW_NAMESPACE_GRANTED
    global _LEGACY_ANCESTOR_FD, _LEGACY_JOURNAL_FD, _LEGACY_CLAIM_FD
    global _LEGACY_AUTHORITY_PROOF
    provenance = declaration["legacy_provenance"].get("local")
    if not isinstance(provenance, dict) or set(provenance) != {
        "temporary_parent",
        "parent",
        "journal",
        "collection_lock",
        "raw_sha256",
    }:
        raise AdapterError("Exact original LOCAL provenance required", "access")
    temporary = tempfile.gettempdir()
    parent = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    original = journal = lock = guard = descriptor = None
    try:
        ancestor = os.fstat(parent)
        ancestor_identity = source_file_identity(ancestor)
        ancestor_keys = {"dev", "ino", "uid", "mode"}
        if (
            set(provenance["temporary_parent"]) != ancestor_keys
            or {key: ancestor_identity[key] for key in ancestor_keys}
            != provenance["temporary_parent"]
        ):
            raise AdapterError("Reviewed original ancestor changed", "access")
        leaf = SOURCE_ID + "-pacing-" + str(os.getuid())
        original = os.open(
            leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        info = os.fstat(original)
        if (
            source_file_identity(info) != provenance["parent"]
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise AdapterError("Original source directory changed", "access")
        if (
            os.stat(leaf, dir_fd=parent, follow_symlinks=False).st_dev,
            os.stat(leaf, dir_fd=parent, follow_symlinks=False).st_ino,
        ) != (info.st_dev, info.st_ino):
            raise AdapterError(
                "Original source directory name changed", "access"
            )
        lock = os.open(
            "collection.lock",
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=original,
        )
        lock_info = os.fstat(lock)
        if (
            source_file_identity(lock_info) != provenance["collection_lock"]
            or not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != os.getuid()
            or stat.S_IMODE(lock_info.st_mode) != 0o600
            or lock_info.st_nlink != 1
        ):
            raise AdapterError("Original collection lock changed", "access")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = os.dup(lock)
        key = hashlib.sha256(b"rausvonzuhaus.de").hexdigest()
        journal = os.open(
            key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=original
        )
        before = os.fstat(journal)
        if (
            source_file_identity(before) != provenance["journal"]
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or before.st_size > 16384
        ):
            raise AdapterError("Original source journal changed", "access")
        fcntl.flock(journal, fcntl.LOCK_SH | fcntl.LOCK_NB)
        raw = bytearray()
        while len(raw) <= 16384:
            part = os.read(journal, min(4096, 16385 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(journal)
        named = os.stat(key, dir_fd=original, follow_symlinks=False)
        if (
            source_file_identity(before) != source_file_identity(after)
            or source_file_identity(named) != source_file_identity(before)
            or len(raw) != before.st_size
            or hashlib.sha256(raw).hexdigest() != provenance["raw_sha256"]
        ):
            raise AdapterError(
                "Original journal bytes/generation changed", "access"
            )
        old = validate_legacy_budget(json.loads(raw))
        legacy_MAX_required(declaration["state"], old, [])
        # The claim survives deletion of the new modern family leaf. It does
        # not claim to survive loss of this trusted ancestor or deliberate
        # same-UID deletion of the external authority file.
        claim = (
            "."
            + SOURCE_ID
            + "-modern-"
            + canonical_digest(declaration)
            + ".once"
        )
        descriptor = os.open(
            claim,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=parent,
        )
        try:
            payload = json.dumps(
                {
                    "purpose": "PROSPECTIVE_FIRST_LOCAL_MODERN_MIGRATION",
                    "evidence": declaration["evidence"],
                    "declaration_sha256": canonical_digest(declaration),
                    "original_raw_sha256": provenance["raw_sha256"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            offset = 0
            while offset < len(payload):
                count = os.write(descriptor, payload[offset:])
                if not 0 < count <= len(payload) - offset:
                    raise AdapterError(
                        "Incomplete one-off migration claim", "access"
                    )
                offset += count
            os.fsync(descriptor)
            if os.pread(descriptor, len(payload) + 1, 0) != payload:
                raise AdapterError("One-off migration claim changed", "access")
            os.fsync(parent)
            claim_info = os.fstat(descriptor)
            validate_owned_artifact(claim_info)
        except BaseException:
            journal_failure()
            raise
        _LEGACY_PARENT_FD, _LEGACY_PARENT_ID = original, (
            info.st_dev,
            info.st_ino,
        )
        _LEGACY_LOCK_FD, _LEGACY_LOCK_GUARD = lock, guard
        _LEGACY_LOCK_ID = (lock_info.st_dev, lock_info.st_ino)
        _LEGACY_ANCESTOR_FD, _LEGACY_JOURNAL_FD = parent, journal
        _LEGACY_CLAIM_FD = descriptor
        _LEGACY_AUTHORITY_PROOF = {
            "temporary": temporary,
            "leaf": leaf,
            "key": key,
            "provenance": copy.deepcopy(provenance),
            "claim": claim,
            "claim_identity": source_file_identity(claim_info),
            "claim_raw": payload,
        }
        original = lock = guard = parent = journal = descriptor = None
        verify_legacy_authority()
        _NEW_NAMESPACE_GRANTED = True
    except BaseException:
        journal_failure()
        raise
    finally:
        primary = sys.exc_info()[1]
        first_error = None
        for descriptor in (descriptor, journal, guard, lock, original, parent):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    journal_failure()
                    first_error = first_error or error
        if primary is None and first_error is not None:
            raise first_error


def close_legacy_authority():
    global _LEGACY_PARENT_FD, _LEGACY_PARENT_ID
    global _LEGACY_LOCK_FD, _LEGACY_LOCK_ID, _LEGACY_LOCK_GUARD
    global _LEGACY_ANCESTOR_FD, _LEGACY_JOURNAL_FD, _LEGACY_CLAIM_FD
    global _LEGACY_AUTHORITY_PROOF
    proof = _LEGACY_AUTHORITY_PROOF
    extras = []
    if proof is not None:
        for descriptor, pin in (
            (_LEGACY_ANCESTOR_FD, proof["provenance"]["temporary_parent"]),
            (_LEGACY_JOURNAL_FD, proof["provenance"]["journal"]),
            (_LEGACY_CLAIM_FD, proof["claim_identity"]),
        ):
            extras.append((descriptor, (int(pin["dev"]), int(pin["ino"]))))
    _LEGACY_ANCESTOR_FD = _LEGACY_JOURNAL_FD = _LEGACY_CLAIM_FD = None
    _LEGACY_AUTHORITY_PROOF = None
    descriptors = [
        (_LEGACY_LOCK_FD, _LEGACY_LOCK_ID),
        (_LEGACY_LOCK_GUARD, _LEGACY_LOCK_ID),
        (_LEGACY_PARENT_FD, _LEGACY_PARENT_ID),
    ] + extras
    _LEGACY_PARENT_FD = _LEGACY_PARENT_ID = None
    _LEGACY_LOCK_FD = _LEGACY_LOCK_ID = _LEGACY_LOCK_GUARD = None
    primary = sys.exc_info()[1]
    errors = []
    for descriptor, identity in descriptors:
        if descriptor is None:
            continue
        try:
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) != identity:
                raise AdapterError(
                    "Original authority descriptor changed", "access"
                )
            os.close(descriptor)
        except BaseException as error:
            journal_failure()
            errors.append(error)
    if errors and primary is None:
        raise errors[0]


def verify_legacy_authority():
    """Rebind retained original and claim bytes after quiet, before wire."""
    proof = _LEGACY_AUTHORITY_PROOF
    if proof is None:
        return
    try:
        pin = proof["provenance"]
        ancestor = source_file_identity(os.fstat(_LEGACY_ANCESTOR_FD))
        named = source_file_identity(
            os.stat(proof["temporary"], follow_symlinks=False)
        )
        for key in pin["temporary_parent"]:
            if (
                ancestor[key] != pin["temporary_parent"][key]
                or named[key] != ancestor[key]
            ):
                raise AdapterError(
                    "Original trusted ancestor changed", "access"
                )
        for descriptor, name, parent, expected in (
            (
                _LEGACY_PARENT_FD,
                proof["leaf"],
                _LEGACY_ANCESTOR_FD,
                pin["parent"],
            ),
            (
                _LEGACY_LOCK_FD,
                "collection.lock",
                _LEGACY_PARENT_FD,
                pin["collection_lock"],
            ),
            (
                _LEGACY_JOURNAL_FD,
                proof["key"],
                _LEGACY_PARENT_FD,
                pin["journal"],
            ),
            (
                _LEGACY_CLAIM_FD,
                proof["claim"],
                _LEGACY_ANCESTOR_FD,
                proof["claim_identity"],
            ),
        ):
            before = source_file_identity(os.fstat(descriptor))
            current = source_file_identity(
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            )
            if before != expected or current != expected:
                raise AdapterError(
                    "Original authority generation changed", "access"
                )
            if descriptor in (_LEGACY_JOURNAL_FD, _LEGACY_CLAIM_FD):
                raw = bytearray()
                while len(raw) <= 16384:
                    part = os.pread(
                        descriptor, min(4096, 16385 - len(raw)), len(raw)
                    )
                    if not part:
                        break
                    raw.extend(part)
                if len(raw) != int(expected["bytes"]):
                    raise AdapterError(
                        "Original authority length changed", "access"
                    )
                if descriptor == _LEGACY_JOURNAL_FD:
                    valid = hashlib.sha256(raw).hexdigest() == pin["raw_sha256"]
                else:
                    valid = raw == proof["claim_raw"]
                if (
                    not valid
                    or source_file_identity(os.fstat(descriptor)) != before
                    or source_file_identity(
                        os.stat(name, dir_fd=parent, follow_symlinks=False)
                    )
                    != before
                ):
                    raise AdapterError(
                        "Original authority bytes changed", "access"
                    )
        if (
            source_file_identity(os.fstat(_LEGACY_LOCK_GUARD))
            != pin["collection_lock"]
        ):
            raise AdapterError("Original lock description changed", "access")
    except BaseException:
        journal_failure()
        raise


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
        present = os.path.lexists(os.path.join(temporary, leaf))
        if not present and not _NEW_NAMESPACE_GRANTED:
            raise AdapterError(
                "Missing mature LOCAL namespace is not a new allowance",
                "access",
            )
        try:
            os.mkdir(leaf, 0o700, dir_fd=parent)
            created = True
        except FileExistsError:
            if _EXCLUSIVE_INITIALIZATION:
                raise AdapterError(
                    "First migration namespace was not exclusively created",
                    "access",
                )
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
                raise AdapterError(
                    "Unsafe publisher pacing directory", "access"
                )
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


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD, _RESTORED
    global _COLLECTION_LOCK_ID, _COLLECTION_LOCK_GUARD
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    if _RESTORED:
        return
    workflow_mode_guard()
    incoming = restored_state()
    # No directory, lock or empty durable budget exists before reviewed state.
    if incoming["in_flight"]:
        raise AdapterError(
            "Owned Rausvonzuhaus artifact is unsettled", "access"
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
                "Another Rausvonzuhaus collector is active", "access"
            ) from None
        try:
            _COLLECTION_LOCK_GUARD = os.dup(descriptor)
        except BaseException:
            journal_failure()
            with contextlib.suppress(BaseException):
                os.close(descriptor)
            raise
        _COLLECTION_LOCK_ID = identity
        _COLLECTION_LOCK_FD = descriptor
    with locked_budget():
        now = time.time()
        local = load_budget()
        if local["in_flight"]:
            raise AdapterError(
                "Local Rausvonzuhaus flight is unsettled", "access"
            )
        combined = merge_budgets(local, incoming, now)
        combined["not_before"] = max(combined["not_before"], now + 60)
        if _STATE_INITIALIZING:
            nonce = os.urandom(16).hex()
            pending = copy.deepcopy(combined)
            pending.update(
                in_flight=True,
                flight_id=nonce,
                flight_started=now,
                flight_kind="policy",
            )
            save_budget(pending)
            combined.update(
                flight_id=nonce,
                flight_started=now,
                flight_kind="policy",
                completed_flight_id=nonce,
                completed=max(combined["completed"], now),
                observed_at=now,
            )
        save_budget(combined)
    _COLLECTION_STARTED = time.time()
    _RESTORED = _STATE_TRUSTED = True


def prewire_coordination():
    """Recheck pause/history after quiet without renewing collection570."""
    global _PREWIRE_PROOF_END
    now = time.monotonic()
    _PREWIRE_PROOF_END = min(now + 30, active_deadline())
    prior = (_PHASE_NAME, _PHASE_END)
    try:
        set_prewire_deadline(_PREWIRE_PROOF_END)
        workflow_mode_guard()
        if current_wrapper_projection() != _RESTORED_WRAPPER_FENCE:
            raise AdapterError(
                "Full wrapper history changed during quiet", "access"
            )
        if os.environ.get("GITHUB_ACTIONS") == "true":
            current_job_context()
        if newest_artifact(_RESTORED_OWNER) != _RESTORED_ARTIFACT:
            raise AdapterError(
                "Newest owned artifact changed during quiet", "access"
            )
        verify_legacy_authority()
        verify_journal()
    finally:
        restore_source_deadline(prior)


def set_prewire_deadline(end):
    global _PHASE_END
    _PHASE_END = min(_PHASE_END, end)
    signal.setitimer(
        signal.ITIMER_REAL, max(0.000001, _PHASE_END - time.monotonic())
    )


def restore_source_deadline(prior):
    global _PHASE_NAME, _PHASE_END
    _PHASE_NAME, _PHASE_END = prior
    signal.setitimer(
        signal.ITIMER_REAL, max(0.000001, active_deadline() - time.monotonic())
    )


# Private integration draft; constants/continuity remain unbound and unexecuted.


def is_inventory(url):
    parsed = urllib.parse.urlsplit(url)
    return (
        is_publisher(url)
        and parsed.path.rstrip("/") == "/lastminute"
        and not parsed.query
    )


@bounded_infrastructure
def infrastructure_request_bytes(
    url, *, headers=None, method="GET", payload=None, byte_limit=5_000_000
):
    if is_publisher(url):
        raise AdapterError("Publisher cannot use infrastructure HTTP", "access")
    status, response_headers, body = original_request_bytes(
        url, method=method, headers=headers, payload=payload
    )
    if len(body) > byte_limit:
        raise AdapterError("Infrastructure response exceeds bound", "fetch")
    return status, response_headers, body


def ensure_publisher_document(document):
    return assess_publisher_document(SOURCE_URL, document)


@durable_transaction("policy")
def assess_publisher_document(url, document):
    # Keep the original visible/HEAD assessment and parser contract while its
    # resulting hold remains pending until durably saved. A footer-only title
    # and generic degree-completion text are not primary denial evidence.
    visible = AccessText(document)
    text = " ".join(visible.parts)
    denial = re.search(
        r"\b(?:verify (?:that )?you are human|access denied|"
        r"checking your browser|complete the (?:security check|captcha)|"
        r"you have been blocked)\b",
        text,
        re.I,
    )
    title_denial = re.fullmatch(
        r"\s*(?:Access denied|Forbidden|Just a moment\.*|"
        r"Attention required\.*)\s*",
        " ".join(visible.title_parts),
        re.I,
    )
    if denial or title_denial:
        retain_publisher_hold()
        raise PolicyStop(
            "Publisher returned a visible access challenge", "access"
        )


def export_family_artifact():
    global _ARTIFACT_CONTEXT
    path = os.environ.get("RAUS_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        with artifact_output(path, cleanup_only=True):
            pass
        pacing_not_ready()
        return
    if _ARTIFACT_CONTEXT is not None:
        raise AdapterError("Pacing export cannot renew", "access")
    manager = artifact_output(path)
    descriptor = manager.__enter__()
    _ARTIFACT_CONTEXT = manager
    # Keep this context pinned until execution closes every journal/lock.
    # A healthy close failure must invalidate the SAME original output FD.
    with locked_budget():
        run_id, attempt = current_run_identity()
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError(
                "Unsettled source state cannot be exported", "access"
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


if __name__ == "__main__":
    raise SystemExit(main())
