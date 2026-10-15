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
        "Bosnien und Herzegowina".split("|"),
        "FR US DE KR IS AT TG EC IN UG TZ TH KH CO RS GE PE GH ZA RW GB AU IE "
        "NO BE CA ES RO CR PT EE FI NZ PL CH KG DK NA BW VN IL FJ DO MW CZ CL "
        "GR NL NP KE PH ID LT IT KZ AL BR BO MZ XK BA".split(),
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


def ensure_publisher_document(document):
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
        with locked_budget(SOURCE_URL) as (state, budget):
            budget["blocked"] = True
            save_budget(state, budget)
        raise AdapterError(
            "Publisher returned a visible access challenge", "access"
        )


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


def collect():
    """Run the complete single-page inventory under one publisher lifecycle."""
    if _RUN_END is not None:
        return collect_inventory()
    with execution():
        try:
            prepare_pacing()
            return collect_inventory()
        finally:
            try:
                begin_phase("export", 30)
                export_pacing()
            finally:
                release_collection()


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
_STATE_TRUSTED = False
_COLLECTION_LOCK = None
_STARTUP_NOT_BEFORE = 0
_RUN_END = _PHASE_END = None
_PHASE_NAME = None
_BOOTSTRAP_APPROVAL = "https://github.com/YouthOpps/data-pipeline/issues/196#issuecomment-6195157525"
_BOOTSTRAP_NOT_BEFORE = 1792076431.5925508
_BOOTSTRAP_NATIVE_STATE = {
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
_LEGACY_HISTORY = {
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


def approved_bootstrap():
    """Require the maintainer's exact transition before the first new access."""
    if (
        not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/196"
            r"#issuecomment-[1-9][0-9]*",
            _BOOTSTRAP_APPROVAL,
        )
        or type(_BOOTSTRAP_NOT_BEFORE) not in (int, float)
        or not math.isfinite(_BOOTSTRAP_NOT_BEFORE)
        or _BOOTSTRAP_NOT_BEFORE <= 0
        or _BOOTSTRAP_NATIVE_STATE is None
    ):
        raise AdapterError("Legacy transition awaits exact approval", "access")
    state = validate_budget(_BOOTSTRAP_NATIVE_STATE)
    state["until"] = max(state["until"], _BOOTSTRAP_NOT_BEFORE)
    state["legacy_rate_history_unknown"] = True
    return state


def legacy_migration(runs):
    """Preserve explicit unknown history; never infer an empty old allowance."""
    if len(runs) != len(_LEGACY_HISTORY) or {run["id"] for run in runs} != set(
        _LEGACY_HISTORY
    ):
        raise AdapterError("Legacy source history changed", "access")
    completions = []
    for run in runs:
        expected = _LEGACY_HISTORY.get(run["id"])
        if (
            expected is None
            or [run["run_attempt"], run["head_sha"], run.get("conclusion")]
            != expected[:3]
            or artifact_inventory(run)
        ):
            raise AdapterError("Untrusted legacy attempt", "access")
        ended = run_completion(run)
        wanted = (
            datetime.fromisoformat(
                expected[3].replace("Z", "+00:00")
            ).timestamp()
            if expected[3] is not None
            else None
        )
        if ended != wanted:
            raise AdapterError("Legacy job chronology changed", "access")
        if ended is not None:
            completions.append(ended)
    state = approved_bootstrap()
    # Completion upper bounds conservatively count known past attempts;
    # they are not reconstructed physical-start measurements or old headers.
    state["starts"] = sorted(set(state["starts"] + completions))
    state["completed_at"] = max(state["completed_at"], max(completions))
    state["until"] = max(
        state["until"], state["completed_at"] + state["interval"]
    )
    return validate_budget(state)


def begin_phase(name, seconds):
    global _PHASE_END, _PHASE_NAME
    _PHASE_NAME = name
    _PHASE_END = min(_RUN_END, time.monotonic() + seconds)
    remaining = _PHASE_END - time.monotonic()
    if remaining <= 0:
        raise AdapterError("Source lifecycle deadline exhausted", name)
    signal.setitimer(signal.ITIMER_REAL, remaining)


def request_timeout():
    if _PHASE_END is None:
        return 30
    remaining = _PHASE_END - time.monotonic()
    if remaining <= 0:
        raise AdapterError("Source phase deadline exhausted", _PHASE_NAME)
    return min(30, remaining)


@contextlib.contextmanager
def execution():
    global _RUN_END, _PHASE_END, _PHASE_NAME
    _RUN_END = time.monotonic() + 1290

    def expired(signum, frame):
        raise AdapterError("Source phase deadline exhausted", _PHASE_NAME)

    previous = signal.signal(signal.SIGALRM, expired)
    try:
        begin_phase("prepublication", 900)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = _PHASE_END = _PHASE_NAME = None


def release_collection():
    global _COLLECTION_LOCK, _STATE_TRUSTED
    if _COLLECTION_LOCK is not None:
        os.close(_COLLECTION_LOCK)
        _COLLECTION_LOCK = None
    _STATE_TRUSTED = False


@contextlib.contextmanager
def locked_budget(url):
    """Keep the original host budget and inode under its existing lock."""
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
    key = hashlib.sha256(host.encode()).hexdigest()
    descriptor = os.open(
        os.path.join(directory, key),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    information = os.fstat(descriptor)
    if (
        not stat.S_ISREG(information.st_mode)
        or information.st_uid != os.getuid()
        or information.st_nlink != 1
        or information.st_mode & 0o077
    ):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher journal", "access")
    state = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read(16385)
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher pacing journal", "access")
        if not raw and os.path.exists(os.path.join(directory, "mature")):
            raise AdapterError("Missing mature publisher journal", "access")
        budget = (
            json.loads(raw)
            if raw
            else {"starts": [], "interval": 6, "until": 0}
        )
        budget = validate_budget(budget)
        yield state, budget
    finally:
        state.close()


def validate_budget(value):
    """Validate inert state, including the original three-field local format."""
    if (
        not isinstance(value, dict)
        or not {"starts", "interval", "until"} <= value.keys()
    ):
        raise AdapterError("Invalid publisher pacing state", "access")
    result = {
        **value,
        "blocked": value.get("blocked", False),
        "completed_at": value.get("completed_at", 0),
        "rate_failures": value.get("rate_failures", 0),
        "legacy_rate_history_unknown": value.get(
            "legacy_rate_history_unknown", True
        ),
    }
    if (
        set(result)
        != {
            "starts",
            "interval",
            "until",
            "blocked",
            "completed_at",
            "rate_failures",
            "legacy_rate_history_unknown",
        }
        or type(result["rate_failures"]) is not int
        or not 0 <= result["rate_failures"] <= 1000000
        or type(result["legacy_rate_history_unknown"]) is not bool
        or type(result["blocked"]) is not bool
        or not isinstance(result["starts"], list)
        or len(result["starts"]) > 128
    ):
        raise AdapterError("Invalid publisher pacing envelope", "access")
    numbers = [
        result["interval"],
        result["until"],
        result["completed_at"],
        *result["starts"],
    ]
    if (
        any(
            type(n) not in (int, float) or not math.isfinite(n) or n < 0
            for n in numbers
        )
        or result["interval"] < 6
    ):
        raise AdapterError("Invalid publisher pacing values", "access")
    if (
        result["starts"] != sorted(set(result["starts"]))
        or any(n > time.time() + 5 for n in result["starts"])
        or result["completed_at"] > time.time() + 5
    ):
        raise AdapterError("Invalid publisher pacing chronology", "access")
    result["interval"] = max(result["interval"], 12.1)
    return result


def workflow_api(path):
    """Read only this repository's authenticated workflow evidence."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    status, _, body = request_bytes(
        "https://api.github.com/repos/YouthOpps/data-pipeline" + path,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
        },
    )
    if status != 200:
        raise AdapterError("Workflow evidence unavailable", "access")
    return json.loads(body)


def workflow_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
        raise AdapterError(
            "Unexpected workflow repository or branch", "access"
        )
    values = [
        os.environ.get(k, "") for k in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")
    ]
    if any(not re.fullmatch(r"[1-9][0-9]*", v) for v in values):
        raise AdapterError("Invalid current workflow identity", "access")
    return tuple(map(int, values))


def trusted_run(run):
    if (
        run.get("repository", {}).get("full_name") != "YouthOpps/data-pipeline"
        or run.get("head_repository", {}).get("full_name")
        != "YouthOpps/data-pipeline"
        or run.get("head_branch") != "main"
        or run.get("path") != ".github/workflows/fetch-" + SOURCE_ID + ".yml"
        or run.get("workflow_id") != 378390311
        or type(run.get("id")) is not int
        or type(run.get("run_attempt")) is not int
        or run["run_attempt"] < 1
        or not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))
    ):
        raise AdapterError("Untrusted source workflow history", "access")
    return run


def previous_attempt(run):
    """Keep completed state hidden by a current or queued rerun attempt."""
    if run["run_attempt"] == 1:
        return None
    prior = trusted_run(
        workflow_api(
            f"/actions/runs/{run['id']}/attempts/{run['run_attempt'] - 1}"
        )
    )
    if (
        prior["id"] != run["id"]
        or prior["run_attempt"] != run["run_attempt"] - 1
        or prior["head_sha"] != run["head_sha"]
        or prior.get("status") != "completed"
    ):
        raise AdapterError("Untrusted prior completed rerun attempt", "access")
    return prior


def workflow_history():
    """Read complete unfiltered source history, including older-ID reruns."""
    current_id, current_attempt = workflow_identity()
    runs, seen, total = [], set(), None
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/workflows/378390311/runs?per_page=100&page={page}"
        )
        entries = result.get("workflow_runs")
        count = result.get("total_count")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(entries, list)
            or len(entries) > 100
            or total is not None
            and total != count
        ):
            raise AdapterError(
                "Changed or invalid source workflow inventory", "access"
            )
        total = count
        for run in entries:
            trusted_run(run)
            if run["id"] in seen:
                raise AdapterError("Duplicate workflow history", "access")
            seen.add(run["id"])
            if run["id"] == current_id:
                if run["run_attempt"] != current_attempt:
                    raise AdapterError(
                        "Current workflow attempt changed", "access"
                    )
                prior = previous_attempt(run)
                if prior is not None:
                    runs.append(prior)
                continue
            if run.get("status") in (
                "queued",
                "waiting",
                "pending",
                "requested",
            ):
                prior = previous_attempt(run)
                if prior is not None:
                    runs.append(prior)
                continue
            if run.get("status") != "completed":
                raise AdapterError(
                    "Another source collection is active", "access"
                )
            runs.append(run)
        if len(seen) == total:
            if current_id not in seen or not runs:
                raise AdapterError(
                    "Missing current or previous workflow evidence", "access"
                )
            return runs
        if len(entries) != 100 or len(seen) > total:
            raise AdapterError(
                "Incomplete source workflow inventory", "access"
            )
    raise AdapterError("Source history exceeds reviewed limit", "access")


def run_completion(run):
    jobs = workflow_api(
        f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100"
    )
    entries = jobs.get("jobs")
    if (
        run.get("status") != "completed"
        or not isinstance(entries, list)
        or len(entries) != jobs.get("total_count")
        or any(
            j.get("status") != "completed"
            or j.get("run_id") != run["id"]
            or j.get("run_attempt") != run["run_attempt"]
            or j.get("head_sha") != run["head_sha"]
            or j.get("head_branch") != "main"
            for j in entries
        )
    ):
        raise AdapterError("Incomplete completed attempt jobs", "access")
    if not entries:
        if run.get("conclusion") == "cancelled":
            return None
        raise AdapterError("Executed source attempt has no jobs", "access")
    try:
        times = [
            datetime.fromisoformat(j["completed_at"].replace("Z", "+00:00"))
            for j in entries
        ]
        if any(t.tzinfo is None for t in times):
            raise ValueError()
        end = max(t.timestamp() for t in times)
        if not 0 < end <= time.time() + 5:
            raise ValueError()
        return end
    except (ValueError, KeyError, TypeError):
        raise AdapterError(
            "Invalid completed job chronology", "access"
        ) from None


def artifact_inventory(run):
    data = workflow_api(f"/actions/runs/{run['id']}/artifacts?per_page=100")
    entries = data.get("artifacts")
    if not isinstance(entries, list) or len(entries) != data.get(
        "total_count"
    ):
        raise AdapterError("Incomplete source artifact inventory", "access")
    return entries


def artifact_budget(run):
    """Require the newest exact attempt's authenticated bounded inert artifact."""
    matches = [
        a
        for a in artifact_inventory(run)
        if a.get("name")
        == "rausvonzuhaus-pacing-state-" + str(run["run_attempt"])
    ]
    if len(matches) != 1:
        raise AdapterError(
            "Newest source attempt lacks its pacing artifact", "access"
        )
    artifact = matches[0]
    if (
        artifact.get("expired") is not False
        or type(artifact.get("id")) is not int
        or artifact.get("workflow_run", {}).get("id") != run["id"]
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= 32768
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
    ):
        raise AdapterError("Invalid source artifact ownership", "access")
    token = os.environ["GITHUB_TOKEN"]
    status, headers, _ = request_bytes(
        "https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/"
        + str(artifact["id"])
        + "/zip",
        headers={"Authorization": "Bearer " + token},
    )
    destination = headers.get("Location", "")
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
        raise AdapterError("Unsafe source artifact redirect", "access")
    # The signed URL is neither logged nor saved; credentials stay at GitHub.
    status, _, archive_bytes = request_bytes(destination)
    try:
        if (
            status != 200
            or len(archive_bytes) > 32768
            or hashlib.sha256(archive_bytes).hexdigest()
            != artifact["digest"][7:]
        ):
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            entries = archive.infolist()
            if len(entries) != 1:
                raise ValueError()
            entry = entries[0]
            if (
                entry.filename != "pacing-state.json"
                or entry.file_size > 16384
                or entry.flag_bits & 1
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError()
            envelope = json.loads(archive.read(entry))
        expected = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "source": SOURCE_ID,
            "run_id": run["id"],
            "run_attempt": run["run_attempt"],
            "head_sha": run["head_sha"],
        }
        if set(envelope) != {*expected, "state"} or any(
            type(envelope[k]) is not type(v) or envelope[k] != v
            for k, v in expected.items()
        ):
            raise ValueError()
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            "Invalid source pacing artifact", "access"
        ) from None


def prepare_pacing():
    """Conserve native local state and restore the newest completed production state."""
    global _STATE_TRUSTED, _COLLECTION_LOCK, _STARTUP_NOT_BEFORE
    if _STATE_TRUSTED:
        return
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    with locked_budget(SOURCE_URL):
        pass
    descriptor = os.open(
        os.path.join(directory, "collection.lock"),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o077
        ):
            raise AdapterError("Unsafe collection lock", "access")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except Exception:
        os.close(descriptor)
        raise AdapterError(
            "Another collector or unsafe collection lock", "access"
        ) from None
    _COLLECTION_LOCK = descriptor
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if hosted:
        runs = workflow_history()
        if {r["id"] for r in runs} == set(_LEGACY_HISTORY):
            incoming = legacy_migration(runs)
        else:
            completed = []
            for run in runs:
                ended = run_completion(run)
                if ended is not None:
                    completed.append(
                        (ended, run["id"], run["run_attempt"], run)
                    )
            completed.sort()
            if not completed or (
                len(completed) > 1 and completed[-1][0] == completed[-2][0]
            ):
                raise AdapterError(
                    "Ambiguous source attempt chronology", "access"
                )
            incoming = artifact_budget(completed[-1][-1])
    else:
        marker = os.path.join(directory, "mature")
        incoming = None if os.path.exists(marker) else approved_bootstrap()
    if incoming is not None:
        with locked_budget(SOURCE_URL) as (state, local):
            for field in (
                "interval",
                "until",
                "completed_at",
                "rate_failures",
            ):
                local[field] = max(local[field], incoming[field])
            local["starts"] = sorted(set(local["starts"] + incoming["starts"]))
            local["blocked"] |= incoming["blocked"]
            local["legacy_rate_history_unknown"] |= incoming[
                "legacy_rate_history_unknown"
            ]
            save_budget(state, local)
    _STARTUP_NOT_BEFORE = time.time() + 60
    marker = os.open(
        os.path.join(directory, "mature"),
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    try:
        info = os.fstat(marker)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise AdapterError("Unsafe publisher maturity marker", "access")
    finally:
        os.close(marker)
    _STATE_TRUSTED = True


def export_pacing():
    """Export only trusted finalized state and signal this step's fresh artifact."""
    path = os.environ.get("RAUS_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
    run_id, attempt = workflow_identity()
    head = os.environ.get("GITHUB_SHA", "")
    directory = os.environ.get("RUNNER_TEMP", "")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", head)
        or not os.path.isabs(directory)
        or os.path.dirname(os.path.abspath(path))
        != os.path.realpath(directory)
    ):
        raise AdapterError("Unsafe source artifact output identity", "access")
    with locked_budget(SOURCE_URL) as (_, state):
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "source": SOURCE_ID,
            "run_id": run_id,
            "run_attempt": attempt,
            "head_sha": head,
            "state": validate_budget(state),
        }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=directory, delete=False
        ) as file:
            temporary = file.name
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        temporary = None
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
                raise AdapterError("Unsafe workflow output file", "access")
            output.write("pacing_ready=true\n")
    finally:
        if temporary:
            os.unlink(temporary)


def pace(url, interval=12.1):
    """Reserve starts while retaining the lock through response completion."""
    manager = locked_budget(url)
    state, budget = manager.__enter__()
    try:
        if budget["blocked"]:
            raise AdapterError(
                "Publisher refusal requires reviewed recovery", "access"
            )
        if (
            budget["rate_failures"] >= 100
            or budget["interval"] > sys.float_info.max / 2
        ):
            raise AdapterError("Rate-limit history requires review", "access")
        if budget["until"] > time.time():
            raise AdapterError(
                "Retained publisher embargo is not complete", "access"
            )
        budget["interval"] = max(budget["interval"], interval, 12.1)
        while True:
            request_timeout()
            now = time.time()
            budget["starts"] = [
                start for start in budget["starts"] if now - start < 60
            ]
            ready = max(
                _STARTUP_NOT_BEFORE,
                budget["until"],
                budget["completed_at"] + budget["interval"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (
                    budget["starts"][0] + 60
                    if len(budget["starts"]) >= 5
                    else now
                ),
            )
            if ready <= now:
                budget["starts"].append(now)
                save_budget(state, budget)
                return state, budget, manager
            time.sleep(min(ready - now, 60))
    except Exception:
        manager.__exit__(*sys.exc_info())
        raise


def save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


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


def fetch_source(url, min_interval=12.1):
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
                    robots=(
                        original.path == "/robots.txt"
                        and urllib.parse.urlsplit(current).path
                        == "/robots.txt"
                    ),
                    headers={
                        "Accept": (
                            "text/plain,*/*;q=0.8"
                            if urllib.parse.urlsplit(current).path
                            == "/robots.txt"
                            else (
                                "text/html,application/xhtml+xml;q=0.9,"
                                "*/*;q=0.8"
                            )
                        )
                    },
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
                        f"Publisher returned HTTP {status} at "
                        + urllib.parse.urlunsplit(
                            (
                                "https",
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
        except AdapterError as error:
            if attempt or error.status not in (500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            cause = (
                error.reason
                if isinstance(error, urllib.error.URLError)
                else error
            )
            if isinstance(cause, ssl.SSLError):
                raise AdapterError(
                    "Publisher TLS verification failed", "access"
                ) from error
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch",
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    origin = (parsed.scheme, parsed.netloc)
    try:
        text = _ROBOTS.get(origin)
        if text is None:
            text = fetch_source(
                urllib.parse.urlunsplit(
                    (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
                )
            )
            _ROBOTS[origin] = text
    except AdapterError as error:
        if (
            error.status is None
            or not 400 <= error.status < 500
            or error.status == 429
        ):
            raise
        # Only this robots retrieval is unavailable, not the content page.
        text = ""
        _ROBOTS[origin] = text
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
            with locked_budget(url) as (state, budget):
                budget["blocked"] = True
                save_budget(state, budget)
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


def main():
    with execution():
        try:
            return run()
        finally:
            try:
                begin_phase("export", 30)
                export_pacing()
            finally:
                release_collection()


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
                    "message": "Run could not complete; durable status unavailable",
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
