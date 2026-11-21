"""Collect the official MSc International Management registration calendar."""

import argparse
import base64
from datetime import date
import io
import sys
import zipfile
from zoneinfo import ZoneInfo
import contextlib
import copy
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import http.client
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


FAMILY = 'youthopps-klagenfurt-publisher-v1'
USER_AGENT = 'YouthOppsBot/1.0 (+https://youthopps.org/bot/)'
ROBOTS = 'https://www.aau.at/robots.txt'
ENTRY = 'https://www.aau.at/en/studien/master-international-management/'
URLS = (ROBOTS, ENTRY)
PARENT = Path('/tmp')
MAX_BODY = 8 * 1024 * 1024
MAX_STATE = 65536
MAX_CLOCK = 253402300799
BODY_SECONDS = 25
CLOCK = time
GATE_FAILED = False
AUDIT = {'requests': [], 'persistence': [], 'errors': []}


class Stop(Exception):
    """A publisher operation cannot safely proceed."""


class Deadline(Stop):
    """A physical operation or its closure exceeded its bound."""


class RateLimited(Stop):
    """A fully assessed timed limit, distinct from an unknown pending wire."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode()


def now():
    value = CLOCK.time()
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= MAX_CLOCK):
        raise Stop('Unsupported current clock')
    return value


SOURCE_ID = "at-klagenfurt-msc-international-management"
SOURCE_URL = ENTRY
WEBSITE_URL = "https://www.aau.at/en/"
DISPLAY_NAME = "University of Klagenfurt"
VIENNA = ZoneInfo("Europe/Vienna")
CENTRAL_OFFSETS = {"CET": timezone(timedelta(hours=1)), "CEST": timezone(timedelta(hours=2))}
TIMESTAMPS = ("created_at", "updated_at", "first_seen_at", "last_seen_at", "last_checked_at")
TITLE = "Master International Management"
REGISTRATION = "Registration period for the special admission procedure"
NUMERIC = r"(\d{2}\.\d{2}\.20\d{2})"
GENERAL = re.compile(NUMERIC + r" to " + NUMERIC)
EARLY = re.compile(r"\(Non-EU- or Non-EEA-citizens: " + NUMERIC + r" to " + NUMERIC + r"\)")
NAMED_REGISTRATION = re.compile(
    r"(\d{1,2} [A-Za-z]+ 20\d{2}) to (\d{1,2} [A-Za-z]+ 20\d{2})"
    r" \((\d{2}:\d{2}) hours (CET|CEST)\)\. " + EARLY.pattern
)
HIDDEN = frozenset({"script", "style", "nav", "footer", "template", "noscript"})
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"})
GROUPS = {"general": "General registration", "non-eu-eea": "Non-EU/EEA registration"}


class AdmissionError(ValueError):
    """The official MSc admission calendar is absent or inconsistent."""


class IndexingDenied(AdmissionError):
    """An applicable publisher directive declines indexing."""


class AdmissionCalendar(HTMLParser):
    """Read visible official headings, calendar blocks, and index directives."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.title = []
        self.blocks = []
        self.index_directives = []
        self.canonical_urls = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = (attrs.get("class") or "").split()
        style = re.sub(r"\s+", "", (attrs.get("style") or "").casefold())
        hidden = (tag in HIDDEN or "hidden" in attrs
                  or (attrs.get("aria-hidden") or "").casefold() == "true"
                  or "display:none" in style or "visibility:hidden" in style)
        parent_hidden, main, block, fragments = self.stack[-1][1:] if self.stack else (
            False, False, None, None
        )
        hidden |= parent_hidden
        main |= tag == "main" or (tag == "div" and attrs.get("id") == "main")
        if tag == "meta" and (attrs.get("name") or "").casefold() in {"robots", "youthoppsbot"}:
            self.index_directives.append(attrs.get("content") or "")
        if tag == "link" and "canonical" in (attrs.get("rel") or "").casefold().split():
            self.canonical_urls.append(attrs.get("href"))
        if not hidden:
            if tag == "h1":
                fragments = []
                self.title.append(fragments)
            if main and tag == "div" and "avia_textblock" in classes:
                block, fragments = [], None
                self.blocks.append(block)
            if block is not None and tag in {"h3", "h4", "p"}:
                fragments = []
                block.append((tag, fragments))
            if tag == "br" and fragments is not None:
                fragments.append(" ")
        if tag not in VOID:
            self.stack.append((tag, hidden, main, block, fragments))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if self.stack:
            _, hidden, _, _, fragments = self.stack[-1]
            if not hidden and fragments is not None:
                fragments.append(text)


def canonical_text(fragments):
    return " ".join("".join(fragments).split())


def denies_indexing(directives, *, headers=False):
    for value in directives:
        applies = True
        for part in value.casefold().split(","):
            directive = part.strip()
            if headers and ":" in directive:
                name, _, content = directive.partition(":")
                if name.strip() in {"max-snippet", "max-video-preview", "max-image-preview",
                                    "unavailable_after"}:
                    continue
                applies = name.strip() in {"*", "youthoppsbot"}
                directive = content.strip()
            if applies and directive in {"noindex", "none"}:
                return True
    return False


def extract_facts(document):
    parser = AdmissionCalendar()
    parser.feed(document)
    parser.close()
    if denies_indexing(parser.index_directives):
        raise IndexingDenied("The programme page declines indexing")
    if [canonical_text(value) for value in parser.title] != [TITLE] or parser.canonical_urls != [ENTRY]:
        raise AdmissionError("The exact programme page was not identified")
    calendars = []
    for block in parser.blocks:
        rows = [(tag, canonical_text(text)) for tag, text in block]
        for index, row in enumerate(rows):
            if row == ("h3", REGISTRATION):
                calendars.append(rows[index + 1:])
    if len(calendars) != 2:
        raise AdmissionError("Both published registration calendars are required")
    standard, detail = sorted(calendars, key=len, reverse=True)
    if [tag for tag, _ in standard] != ["h4", "p"] or [tag for tag, _ in detail] != ["p"]:
        raise AdmissionError("The standard and detailed calendar structure changed")
    general, early, named = (GENERAL.fullmatch(standard[0][1]), EARLY.fullmatch(standard[1][1]),
                             NAMED_REGISTRATION.fullmatch(detail[0][1]))
    if not all((general, early, named)):
        raise AdmissionError("The official registration windows could not be identified")
    read_date = lambda value: datetime.strptime(value, "%d.%m.%Y").date()
    opening, general_close = map(read_date, general.groups())
    early_open, early_close = map(read_date, early.groups())
    named_open, named_close = (datetime.strptime(value, "%d %B %Y").date()
                               for value in named.groups()[:2])
    if (named_open != opening or named_close != general_close or opening != early_open
            or named.groups()[-2:] != early.groups() or not opening < early_close < general_close
            or not opening.year == early_close.year == general_close.year):
        raise AdmissionError("Inconsistent official registration windows")
    clock = datetime.strptime(named[3], "%H:%M").time()
    return opening, general_close, early_close, clock, named[4]


def checked_time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("A timezone-aware check time is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def day_label(day):
    return f"{day.day} {day.strftime('%B %Y')}"


def end_of_registration_day(day, group):
    next_day = day + timedelta(days=1)
    local = datetime.combine(next_day, datetime.min.time(), VIENNA)
    if group == "non-eu-eea":
        return local
    # The official English page sometimes uses CET during daylight-saving time.
    # Keep the date-only record through both plausible ends of the published date.
    fixed = datetime.combine(next_day, datetime.min.time(), CENTRAL_OFFSETS["CET"])
    return max(local, fixed)


def normalize(facts, checked_at):
    opening, general_close, early_close, clock, zone = facts
    stamp = checked_time(checked_at)
    today = checked_at.astimezone(VIENNA).date()
    result = []
    for group, label in GROUPS.items():
        deadline = general_close if group == "general" else early_close
        label_time = f"The university lists {clock.strftime('%H:%M')} {zone} for the general cutoff."
        eligibility = (f"{label_time} Non-EU/EEA applicants have an earlier deadline."
                       if group == "general" else "This is the earlier window listed for non-EU/EEA applicants.")
        if checked_at >= end_of_registration_day(deadline, group):
            status = "expired"
        elif today < opening:
            status = "unknown"
        elif group == "general" and checked_at >= min(
            datetime.combine(deadline, clock, VIENNA),
            datetime.combine(deadline, clock, CENTRAL_OFFSETS[zone]),
        ):
            status = "unknown"
        else:
            status = "open"
        result.append({
            "id": f"{SOURCE_ID}-{opening.year}-{group}",
            "title": f"University of Klagenfurt MSc International Management {opening.year} ({label})",
            "url": SOURCE_URL, "source": SOURCE_ID, "source_url": SOURCE_URL, "published_at": None,
            "summary": (f"Registration for the special admission procedure runs from {day_label(opening)} "
                        f"to {day_label(deadline)}. {eligibility} Registration is for admission selection. Check the "
                        "university page for application requirements and the closing time."),
            "tags": ["masters", "admissions", "international-students"],
            "location": "Klagenfurt, Austria", "deadline": deadline.isoformat(), "language": "en",
            "category": "other", "categories": ["other"], "kind": "opportunity",
            "host_countries": ["AT"], "eligible_countries": [], "publisher_country": "AT",
            "status": status, "classification": {"method": "publisher-admissions-calendar",
                "status": "unknown", "evidence": [f"Official programme registration calendar, {label.lower()}"]},
            **dict.fromkeys(TIMESTAMPS, stamp),
        })
    return result


def expire(records, checked_at):
    checked_time(checked_at)
    return [record for record in records if checked_at < end_of_registration_day(
        date.fromisoformat(record["deadline"]),
        "non-eu-eea" if record["id"].endswith("-non-eu-eea") else "general",
    )]


def validate_records(records, *, allow_empty=False):
    if type(records) is not list or len(records) > 2 or (not allow_empty and len(records) != 2):
        raise ValueError("Expected both official registration windows")
    groups = set()
    years = set()
    for record in records:
        match = re.fullmatch(rf"{SOURCE_ID}-(20\d{{2}})-(general|non-eu-eea)", record["id"])
        if not (match and match[2] not in groups and record["source"] == SOURCE_ID
                and record["source_url"] == record["url"] == SOURCE_URL and record["kind"] == "opportunity"
                and record["category"] == "other" and record["categories"] == ["other"]
                and record["host_countries"] == ["AT"] and record["eligible_countries"] == []
                and record["publisher_country"] == "AT" and record["status"] in {"open", "expired", "unknown"}
                and type(record["title"]) is str and record["title"].strip()
                and type(record["summary"]) is str and 0 < len(record["summary"].encode("utf-16-le")) // 2 <= 600):
            raise ValueError("Malformed or foreign Klagenfurt registration")
        groups.add(match[2])
        years.add(match[1])
        for field in TIMESTAMPS:
            checked_time(datetime.fromisoformat(record[field].replace("Z", "+00:00")))
        cutoff = date.fromisoformat(record["deadline"])
        if cutoff.isoformat() != record["deadline"] or cutoff.year != int(match[1]):
            raise ValueError("Invalid registration date")
    if len(years) > 1:
        raise ValueError("Registration windows belong to different cycles")


def identity(info):
    return (info.st_dev, info.st_ino)


def version(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
            stat.S_IMODE(info.st_mode), info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def private_file(info):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise Stop('Unsafe owned file')


def parent_safe(info):
    mode = stat.S_IMODE(info.st_mode)
    if (not stat.S_ISDIR(info.st_mode)
            or not (mode == 0o1777 or info.st_uid in (0, os.getuid())
                    and not mode & 0o022)):
        raise Stop('Unsafe temporary parent')


def held_flock(fd):
    """Verify this open-file description, without silently reacquiring."""
    lines = Path('/proc/self/fdinfo/' + str(fd)).read_text().splitlines()
    if not any(line.startswith('lock:') and 'FLOCK' in line
               and 'WRITE' in line for line in lines):
        raise Stop('Original flock was lost')


def checked_close(handles, primary=None):
    errors = []
    for fd, expected in reversed(handles):
        try:
            if identity(os.fstat(fd)) != expected:
                raise Stop('Descriptor identity changed before close')
            os.close(fd)
        except BaseException as error:
            errors.append(error)
    if errors:
        global GATE_FAILED
        GATE_FAILED = True
        AUDIT['errors'].extend('descriptor-close:' + type(e).__name__
                               for e in errors)
        if primary is None:
            raise errors[0]


def validate(value):
    keys = {'schema', 'family', 'observed_at', 'not_before', 'interval',
            'page_interval', 'completed', 'inventory_completed', 'starts',
            'rate_limit_strikes', 'rate_limit_until',
            'legacy_rate_history_unknown', 'legacy_policy_history_unknown',
            'blocked', 'logical_id', 'completed_logical_id', 'physical_id',
            'completed_physical_id', 'physical_started', 'target',
            'in_flight', 'physical_pending', 'outcome', 'rates', 'policy'}
    if type(value) is not dict or set(value) != keys:
        raise Stop('Unknown research state shape')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise Stop('Unknown research state schema')
    if value['family'] != FAMILY:
        raise Stop('Wrong university family')
    numbers = ('observed_at', 'not_before', 'interval', 'page_interval',
               'completed', 'inventory_completed', 'rate_limit_until',
               'physical_started')
    for key in numbers:
        v = value[key]
        if (type(v) not in (int, float) or not math.isfinite(v) or v < 0
                or key not in ('interval', 'page_interval')
                and v > MAX_CLOCK):
            raise Stop('Unsupported research number: ' + key)
    if value['interval'] < 12.2 or value['page_interval'] < 30:
        raise Stop('Research floor was weakened')
    for key in ('legacy_rate_history_unknown',
                'legacy_policy_history_unknown', 'blocked', 'in_flight',
                'physical_pending'):
        if type(value[key]) is not bool:
            raise Stop('Invalid research flag')
    if (not value['legacy_rate_history_unknown']
            or not value['legacy_policy_history_unknown']):
        raise Stop('Historical unknowns cannot be cleared by research')
    strikes = value['rate_limit_strikes']
    if type(strikes) is not int or strikes < 0:
        raise Stop('Invalid rate counter')
    starts = value['starts']
    if (type(starts) is not list or starts != sorted(set(starts))
            or any(type(t) not in (int, float) or not math.isfinite(t)
                   or not 0 <= t <= MAX_CLOCK for t in starts)):
        raise Stop('Invalid timing observations')
    if (any(t > value['observed_at'] for t in starts)
            or value['completed'] > value['observed_at']
            or value['inventory_completed'] > value['completed']
            or value['physical_started'] > value['observed_at']):
        raise Stop('Research timing chronology differs')
    for key in ('logical_id', 'completed_logical_id', 'physical_id',
                'completed_physical_id'):
        v = value[key]
        if v is not None and (not isinstance(v, str)
                              or not re.fullmatch('[0-9a-f]{32}', v)):
            raise Stop('Invalid transaction owner')
    if value['target'] is not None and value['target'] not in URLS:
        raise Stop('Unreviewed target')
    if value['outcome'] not in ('initial_reviewed_state', 'reserved',
                                'response_assessed'):
        raise Stop('Invalid transaction outcome')
    if value['physical_pending'] and not value['in_flight']:
        raise Stop('Physical owner escaped logical owner')
    if value['in_flight'] != (
            value['logical_id'] != value['completed_logical_id']):
        raise Stop('Logical ownership mismatch')
    if value['physical_pending'] != (
            value['physical_id'] != value['completed_physical_id']):
        raise Stop('Physical ownership mismatch')
    if type(value['rates']) is not list or len(value['rates']) > 32:
        raise Stop('Unassessed REP rate capacity')
    for rate in value['rates']:
        if (type(rate) is not dict or set(rate) != {'count', 'period'}
                or type(rate['count']) is not int or rate['count'] < 1
                or type(rate['period']) not in (int, float)
                or not math.isfinite(rate['period'])
                or not 0 < rate['period'] <= MAX_CLOCK):
            raise Stop('Unsupported REP rate')
    if value['policy'] is not None:
        policy = value['policy']
        if (type(policy) is not dict or set(policy) != {
                'url', 'status', 'body_sha256', 'unavailable',
                'new_allowed', 'legacy_allowed'}
                or policy['url'] != ROBOTS
                or type(policy['status']) is not int
                or not 200 <= policy['status'] <= 599
                or not re.fullmatch('[0-9a-f]{64}', policy['body_sha256'])
                or any(type(policy[k]) is not bool for k in
                       ('unavailable', 'new_allowed', 'legacy_allowed'))):
            raise Stop('Untrusted REP provenance')
    return value


class Journal:
    """Original descriptors, flocks, parent and acknowledged bytes."""

    def __init__(self, expected, first_wire_proof):
        self.expected = expected
        self.first_wire_proof = first_wire_proof
        self.handles = []
        self.directory = None
        self.fd = None
        self.ack = None
        self.ack_version = None
        self.capture_claim = None

    def verify_capture(self):
        self.verify()
        if self.capture_claim is None:
            raise Stop('No exclusive research capture authority')
        raw, expected = self.capture_claim
        named = os.stat('collection.claim',
                        dir_fd=self.directory, follow_symlinks=False)
        private_file(named)
        if (identity(named) != expected
                or read_private_claim(self.directory,
                                      'collection.claim') != raw):
            raise Stop('Original one-use collection claim changed')

    def __enter__(self):
        global GATE_FAILED
        primary = None
        try:
            self.parent = os.open(
                PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            p = os.fstat(self.parent)
            parent_safe(p)
            self.parent_id = identity(p)
            self.parent_metadata = (p.st_uid, p.st_gid,
                                    stat.S_IMODE(p.st_mode))
            if (list(self.parent_id) != self.expected['parent_identity']
                    or list(self.parent_metadata)
                    != self.expected['parent_metadata']):
                raise Stop('Initialized parent authority changed')
            self.handles.append((self.parent, self.parent_id))
            leaf, _ = paths()
            self.directory = os.open(
                leaf.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=self.parent)
            d = os.fstat(self.directory)
            self.directory_id = identity(d)
            self.handles.append((self.directory, self.directory_id))
            if list(self.directory_id) != self.expected['directory_identity']:
                raise Stop('Initialized directory was replaced')
            self.files = {}
            for name in ('collection.lock', 'state.lock', 'state.json'):
                fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=self.directory)
                info = os.fstat(fd)
                private_file(info)
                self.files[name] = (fd, identity(info))
                self.handles.append((fd, identity(info)))
                if name != 'state.json':
                    if list(identity(info)) != self.expected['locks'][name]:
                        raise Stop('Initialized lock was replaced')
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = self.files['state.json'][0]
            if list(self.files['state.json'][1]) != self.expected[
                    'state_identity']:
                raise Stop('Initialized journal was replaced')
            self.verify()
            self.read()
            return self
        except BaseException as error:
            GATE_FAILED = True
            primary = error
            raise
        finally:
            if primary is not None:
                checked_close(self.handles, primary)

    def verify(self):
        p = os.fstat(self.parent)
        named = PARENT.lstat()
        parent_safe(p)
        if (identity(p) != identity(named) or identity(p) != self.parent_id
                or (p.st_uid, p.st_gid, stat.S_IMODE(p.st_mode))
                != self.parent_metadata
                or (named.st_uid, named.st_gid,
                    stat.S_IMODE(named.st_mode)) != self.parent_metadata):
            raise Stop('Original temporary parent authority changed')
        leaf, _ = paths()
        d = os.fstat(self.directory)
        named = os.stat(leaf.name, dir_fd=self.parent,
                        follow_symlinks=False)
        if (identity(d) != self.directory_id
                or identity(named) != self.directory_id
                or not stat.S_ISDIR(d.st_mode) or d.st_uid != os.getuid()
                or stat.S_IMODE(d.st_mode) != 0o700
                or named.st_uid != d.st_uid or named.st_gid != d.st_gid
                or stat.S_IMODE(named.st_mode) != 0o700):
            raise Stop('Original family directory authority changed')
        if ([d.st_uid, d.st_gid, stat.S_IMODE(d.st_mode)]
                != self.expected['directory_metadata']):
            raise Stop('Original family metadata changed')
        for name, (fd, expected) in self.files.items():
            info = os.fstat(fd)
            named = os.stat(name, dir_fd=self.directory,
                            follow_symlinks=False)
            private_file(info)
            private_file(named)
            if identity(info) != expected or identity(named) != expected:
                raise Stop('Original journal or lock was replaced')
            if name != 'state.json':
                held_flock(fd)
            elif [info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
                  info.st_nlink] != self.expected['state_metadata']:
                raise Stop('Original state metadata changed')

    def read(self):
        self.verify()
        before = os.fstat(self.fd)
        if not 0 < before.st_size <= MAX_STATE:
            raise Stop('Missing or empty established journal')
        raws = []
        for _ in range(2):
            raw = b''
            while len(raw) < before.st_size:
                self.verify()
                segment = os.pread(
                    self.fd, before.st_size - len(raw), len(raw))
                if not segment:
                    raise Stop('Short established journal read')
                raw += segment
            raws.append(raw)
        self.verify()
        after = os.fstat(self.fd)
        if version(before) != version(after) or raws[0] != raws[1]:
            raise Stop('Journal changed during read')
        if self.ack is not None and (
                self.ack != raws[0] or self.ack_version != version(after)):
            raise Stop('Acknowledged journal generation changed')
        self.state = validate(json.loads(raws[0]))
        self.ack = raws[0]
        self.ack_version = version(after)
        return copy.deepcopy(self.state)

    def invalidate_owned(self, prospective=None):
        try:
            self.verify()
            info = os.fstat(self.fd)
            raw = os.pread(self.fd, MAX_STATE + 1, 0)
            if (self.ack_version is None
                    or raw not in (self.ack, prospective)
                    or version(info) != version(os.fstat(self.fd))):
                return
            os.ftruncate(self.fd, 0)
            os.fsync(self.fd)
        except BaseException as error:
            AUDIT['errors'].append('invalidation:' + type(error).__name__)

    def save(self, value):
        global GATE_FAILED
        validate(value)
        before = self.read()
        monotonic = ('observed_at', 'not_before', 'interval', 'page_interval',
                     'completed', 'inventory_completed', 'rate_limit_strikes',
                     'rate_limit_until')
        if (any(value[k] < before[k] for k in monotonic)
                or before['blocked'] and not value['blocked']
                or not set(before['starts']) <= set(value['starts'])
                or any(rate not in value['rates']
                       for rate in before['rates'])):
            GATE_FAILED = True
            raise Stop('Retained constraints cannot be weakened')
        raw = canonical(value)
        event = {'before': before, 'after': None,
                 'intended': copy.deepcopy(value),
                 'bytes': raw, 'acknowledged': False,
                 'monotonic': CLOCK.monotonic()}
        AUDIT['persistence'].append(event)
        if len(raw) > MAX_STATE:
            GATE_FAILED = True
            raise Stop('Research journal capacity exceeded')
        try:
            self.verify()
            offset = 0
            while offset < len(raw):
                self.verify()
                count = os.pwrite(self.fd, raw[offset:], offset)
                if count <= 0:
                    raise Stop('Short journal write')
                offset += count
            os.ftruncate(self.fd, len(raw))
            os.fsync(self.fd)
            self.verify()
            stored = os.pread(self.fd, MAX_STATE + 1, 0)
            if stored != raw:
                raise Stop('Journal readback differs')
            self.ack = raw
            self.ack_version = version(os.fstat(self.fd))
            self.state = copy.deepcopy(value)
            event['after'] = copy.deepcopy(value)
            event['acknowledged'] = True
            event['completed_monotonic'] = CLOCK.monotonic()
        except BaseException as error:
            GATE_FAILED = True
            event['error'] = type(error).__name__
            # An irreversible capture claim also denies every fresh reentry.
            # Never touch an unrelated or stronger replacement generation.
            self.invalidate_owned(raw)
            raise
        finally:
            event['RAM_state'] = copy.deepcopy(self.state)
            try:
                self.verify()
                before_read = os.fstat(self.fd)
                actual = os.pread(self.fd, MAX_STATE + 1, 0)
                if version(before_read) != version(os.fstat(self.fd)):
                    raise Stop('Post-write capture generation changed')
                event['actual_journal_bytes'] = actual
            except BaseException as capture_error:
                event['actual_journal_unavailable'] = type(
                    capture_error).__name__

    def __exit__(self, kind, error, traceback):
        global GATE_FAILED
        primary = error
        try:
            self.read()
        except BaseException as own:
            GATE_FAILED = True
            if primary is None:
                primary = own
        try:
            checked_close(self.handles, primary)
        except BaseException:
            GATE_FAILED = True
            raise
        if primary is not None and error is None:
            raise primary
        return False


def create_once(parent, name, value):
    raw = canonical(value)
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent)
    expected = identity(os.fstat(fd))
    primary = None
    try:
        offset = 0
        while offset < len(raw):
            count = os.write(fd, raw[offset:])
            if count <= 0:
                raise Stop('Incomplete exclusive claim')
            offset += count
        os.fsync(fd)
        private_file(os.fstat(fd))
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if identity(named) != expected or os.pread(fd, len(raw) + 1, 0) != raw:
            raise Stop('Exclusive claim readback differs')
        os.fsync(parent)
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close([(fd, expected)], primary)
    return raw, expected


@contextlib.contextmanager
def operation_deadline():
    """The repeated cancellation also bounds close during unwinding."""
    old_handler = signal.getsignal(signal.SIGALRM)
    old_timer = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()

    def expired(signum, frame):
        signal.setitimer(signal.ITIMER_REAL, 0.25, 0.25)
        raise Deadline('University operation deadline')

    signal.signal(signal.SIGALRM, expired)
    first = (min(BODY_SECONDS, old_timer[0]) if old_timer[0] > 0
             else BODY_SECONDS)
    signal.setitimer(signal.ITIMER_REAL, first)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        if old_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL,
                             max(0.000001, old_timer[0]
                                 - (time.monotonic() - started)),
                             old_timer[1])


def date_patterns():
    short = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)'
    long = r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)'
    month = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
    clock = r'[0-9]{2}:[0-9]{2}:[0-9]{2}'
    return (short + r', *[0-9]{2} ' + month + r' [0-9]{4,} '
            + clock + r' GMT', long + r', *[0-9]{2}-' + month
            + r'-[0-9]{2} ' + clock + r' GMT', short + ' ' + month
            + r' (?:[0-9]{2}| [0-9]) ' + clock + r' [0-9]{4,}')


def http_date(value, reference):
    form = next((i for i, p in enumerate(date_patterns())
                 if re.fullmatch(p, value, re.I)), None)
    if form is None:
        return None
    parsed = parsedate_to_datetime(value)
    if form == 1:
        ref = datetime.fromtimestamp(reference, timezone.utc)
        year = ref.year // 100 * 100 + int(
            re.search(r'-([0-9]{2}) ', value)[1])
        parsed = parsed.replace(year=year)
        lower = (ref.year - 50, ref.month, ref.day, ref.hour,
                 ref.minute, ref.second)
        upper = (ref.year + 50, ref.month, ref.day, ref.hour,
                 ref.minute, ref.second)
        parts = lambda d: (d.year, d.month, d.day, d.hour, d.minute, d.second)
        if parts(parsed) <= lower:
            parsed = parsed.replace(year=year + 100)
        if parts(parsed) > upper:
            parsed = parsed.replace(year=parsed.year - 100)
    if parsed.utcoffset() is None:
        if form != 2:
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.utcoffset() != timedelta(0):
        return None
    return parsed.timestamp()


def known_status(journal, status, robots):
    state = journal.read()
    if status in (401, 403) and not robots:
        state['blocked'] = True
        journal.save(state)
    if status != 429:
        return
    stamp = now()
    state['rate_limit_strikes'] += 1
    unsupported = False
    for key, floor in [('interval', 12.2), ('page_interval', 30)]:
        doubled = state[key] * 2
        if math.isfinite(doubled):
            state[key] = max(doubled, floor)
        else:
            unsupported = True
    state['rate_limit_until'] = max(state['rate_limit_until'], stamp + 1800)
    state['not_before'] = max(state['not_before'], state['rate_limit_until'])
    journal.save(state)
    if unsupported or state['rate_limit_strikes'] > 28:
        raise Stop('Unrepresentable repeated rate limit; pending retained')
    until = stamp + 1800 * 2 ** (state['rate_limit_strikes'] - 1)
    if not math.isfinite(until) or until > MAX_CLOCK:
        raise Stop('Unrepresentable rate clock; pending retained')
    state['rate_limit_until'] = max(state['rate_limit_until'], until)
    state['not_before'] = max(state['not_before'], until)
    journal.save(state)


def retry_after(journal, headers):
    date = '(?:' + '|'.join(date_patterns()) + ')'
    state = journal.read()
    stamp = now()
    untils = []
    unsupported = False
    for field in headers.get_all('Retry-After', []):
        if not isinstance(field, str):
            continue
        value = field.strip()
        values = [value]
        if ',' in value and not re.fullmatch(date, value, re.I):
            values = [m[1] for m in re.finditer(
                r'(?:^|,\s*)(' + date + r'|[0-9]+)(?=\s*(?:,|$))',
                value, re.I)]
        for item in values:
            if re.fullmatch('[0-9]+', item):
                digits = item.lstrip('0') or '0'
                if len(digits) > 12:
                    unsupported = True
                    continue
                until = stamp + int(digits)
            else:
                until = http_date(item, stamp)
                if until is None:
                    continue
            if not math.isfinite(until) or until > MAX_CLOCK:
                unsupported = True
            elif until > stamp:
                untils.append(until)
    if untils:
        state['not_before'] = max(state['not_before'], *untils)
        state['rate_limit_until'] = max(state['rate_limit_until'], *untils)
        journal.save(state)
    if unsupported:
        raise Stop('Unrepresentable positive Retry-After clock; pending retained')


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        style = re.sub(r'\s+', '', (attrs.get('style') or '').lower())
        hidden = (tag in ('script', 'style', 'template', 'nav', 'aside',
                          'footer') or 'hidden' in attrs
                  or (attrs.get('aria-hidden') or '').lower() == 'true'
                  or 'display:none' in style or 'visibility:hidden' in style)
        if tag not in ('br', 'img', 'hr', 'meta', 'link', 'input', 'source',
                       'area', 'base', 'col', 'embed', 'param', 'track',
                       'wbr'):
            self.stack.append((tag, hidden or any(
                x[1] for x in self.stack)))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, value):
        if not any(x[1] for x in self.stack):
            self.text.append(value)


def challenge(journal, body, headers, robots, status):
    encoding = headers.get_content_charset() or 'utf-8'
    text = body.decode(encoding, 'strict')
    if '<' in text:
        visible = Visible()
        visible.feed(text)
        text = ' '.join(visible.text)
    elif robots:
        # REP comments are not a visible human verification page.
        text = '\n'.join(x for x in text.splitlines()
                         if not x.lstrip().startswith('#'))
    text = ' '.join(text.casefold().split())
    phrases = ['verify you are human', 'verify that you are human',
               'complete the captcha', 'checking your browser',
               'enable javascript and cookies to continue']
    if not robots and status != 429:
        phrases += ['access denied', 'permission denied', 'request denied',
                    'request blocked', 'unusual traffic']
    if any(p in text for p in phrases):
        state = journal.read()
        state['blocked'] = True
        journal.save(state)
        raise Stop('Actual visible publisher challenge or denial')
    return text


def framing(headers):
    lengths = headers.get_all('Content-Length', [])
    transfers = headers.get_all('Transfer-Encoding', [])
    if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
        raise Stop('Ambiguous response framing')
    if transfers and transfers[0].strip().casefold() != 'chunked':
        raise Stop('Unsupported transfer coding')
    if lengths:
        if not re.fullmatch('[0-9]+', lengths[0].strip()):
            raise Stop('Invalid content length')
        length = int(lengths[0])
        if length > MAX_BODY:
            raise Stop('Body exceeds the bounded research scope')
        return length
    return None


def robot_policy(journal, body, status):
    if 400 <= status < 500 and status != 429:
        return True, True, True
    if status != 200:
        raise Stop('REP unreachable or redirected; no follow')
    text = body.decode('utf-8-sig', 'strict')
    groups, agents, rules, directives = [], [], [], []
    saw_rule = False
    for line in text.splitlines():
        line = line.split('#', 1)[0].strip()
        if not line or ':' not in line:
            continue
        name, value = (p.strip() for p in line.split(':', 1))
        name = name.casefold()
        if name == 'user-agent':
            if not value:
                raise Stop('Unassessed empty REP product token')
            if saw_rule:
                groups.append((agents, rules, directives))
                agents, rules, directives, saw_rule = [], [], [], False
            agents.append(value.casefold())
        elif agents:
            saw_rule = True
            if name in ('allow', 'disallow'):
                rules.append((name, value))
            elif name in ('crawl-delay', 'request-rate'):
                directives.append((name, value))
    if agents:
        groups.append((agents, rules, directives))
    state = journal.read()
    unassessed = []
    permitted = []
    for token in ('youthoppsbot', 'youthopps'):
        exact = [(max(len(a) for a in g[0] if a != '*'
                      and token.startswith(a)), g) for g in groups
                 if any(a != '*' and token.startswith(a) for a in g[0])]
        if exact:
            longest = max(x[0] for x in exact)
            selected = [g for n, g in exact if n == longest]
        else:
            selected = [g for g in groups if '*' in g[0]]
        matches = []
        for _, group_rules, limits in selected:
            for name, value in limits:
                try:
                    if name == 'crawl-delay':
                        delay = float(value)
                        if not math.isfinite(delay) or delay < 0:
                            raise Stop('Unassessed REP delay')
                        state['interval'] = max(state['interval'], delay)
                    else:
                        match = re.fullmatch(
                            r'([0-9]+)\s*/\s*([0-9]+)', value)
                        if not match:
                            raise Stop('Unassessed REP request rate')
                        count, period = (int(x) for x in match.groups())
                        if count < 1 or not 0 < period <= MAX_CLOCK:
                            raise Stop('Unrepresentable REP request rate')
                        rate = {'count': count, 'period': period}
                        if rate not in state['rates']:
                            state['rates'].append(rate)
                        state['interval'] = max(
                            state['interval'], period / count)
                except (Stop, ValueError, OverflowError):
                    unassessed.append(name)
            for name, value in group_rules:
                if not value:
                    continue
                end = value.endswith('$')
                prefix = normalize_octets(value[:-1] if end else value)
                pattern = '^' + re.escape(prefix).replace(r'\*', '.*')
                if end:
                    pattern += '$'
                if re.search(pattern, normalize_octets(
                        urllib.parse.urlsplit(ENTRY).path)):
                    matches.append((len(prefix.replace('*', '')),
                                    name == 'allow'))
        if matches:
            length = max(x[0] for x in matches)
            permitted.append(any(allow for n, allow in matches if n == length))
        else:
            permitted.append(True)
    if not all(permitted):
        state['blocked'] = True
    journal.save(state)
    if unassessed:
        raise Stop('Unassessed REP directive; known limits retained')
    return permitted[0], permitted[1], False


def normalize_octets(value):
    """RFC9309 octet matching, without decoding reserved URL separators."""
    unreserved = ('abcdefghijklmnopqrstuvwxyz'
                  'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~')

    def percent(match):
        char = chr(int(match[1], 16))
        return char if char in unreserved else '%' + match[1].upper()

    value = re.sub(r'%([0-9a-fA-F]{2})', percent, value)
    return ''.join(char if ord(char) < 128 else ''.join(
        '%' + format(byte, '02X') for byte in char.encode('utf-8'))
                   for char in value)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WitnessResponse(http.client.HTTPResponse):
    """Record the observed status-line boundary and response-file closure."""

    def __init__(self, *args, witness, **kwargs):
        self.witness = witness
        super().__init__(*args, **kwargs)

    def _read_status(self):
        try:
            version, status, reason = super()._read_status()
        except http.client.RemoteDisconnected:
            self.witness["status_line_eof"] = True
            raise
        self.witness["statuses"].append(status)
        return version, status, reason

    def close(self):
        super().close()
        self.witness["response_file_closed"] = self.isclosed()


class WitnessConnection(http.client.HTTPSConnection):
    """Use the existing TLS path and retain positively observed close calls."""

    def __init__(self, host, *, witness, **kwargs):
        self.witness = witness
        super().__init__(host, **kwargs)

    def response_class(self, *args, **kwargs):
        return WitnessResponse(*args, witness=self.witness, **kwargs)

    def getresponse(self):
        # Scope all recorded status and close events to the origin response.
        # Connecting through an existing proxy may already have parsed CONNECT.
        self.witness.update(statuses=[], status_line_eof=False,
                            response_file_closed=False, connection_closed=False)
        return super().getresponse()

    def close(self):
        super().close()
        self.witness["connection_closed"] = self.sock is None


class WitnessHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        witness = getattr(request, "_source_transport_witness", None)
        if witness is None:
            return super().https_open(request)

        def connection(host, **kwargs):
            return WitnessConnection(host, witness=witness, **kwargs)

        return self.do_open(connection, request, context=self._context)


OPENER = urllib.request.build_opener(NoRedirect(), WitnessHTTPSHandler())



def wait_for_request(journal, inventory):
    while True:
        if GATE_FAILED:
            raise Stop('Publisher gate is latched closed')
        state = journal.read()
        if state['blocked'] or state['in_flight'] or state['physical_pending']:
            raise Stop('Retained refusal or unresolved transaction')
        stamp = now()
        boundary = max(state['not_before'], state['rate_limit_until'],
                       state['completed'] + state['interval'])
        if inventory:
            boundary = max(boundary, state['inventory_completed']
                           + state['page_interval'])
        rates = [{'count': 5, 'period': 60}] + state['rates']
        for rate in rates:
            starts = [x for x in state['starts']
                      if x > stamp - rate['period']]
            if len(starts) >= rate['count']:
                boundary = max(boundary,
                               starts[-rate['count']] + rate['period'])
        delay = boundary - stamp
        if delay <= 0:
            return
        if delay > 300:
            raise Stop('Retained timing exceeds bounded research scope')
        CLOCK.sleep(min(delay, 1))


def _request(journal, url):
    """Exactly one physical request; no retry or redirect path exists."""
    global GATE_FAILED
    if not isinstance(journal, Journal):
        raise Stop('Original owned journal authority required')
    journal.verify_capture()
    if (url not in URLS
            or urllib.parse.urlsplit(url).hostname != 'www.aau.at'):
        raise Stop('Unreviewed university URL or external origin')
    robots = url == ROBOTS
    prior = journal.read()
    if robots and AUDIT['requests']:
        raise Stop('Current collection already obtained REP')
    if not robots:
        policy = prior['policy']
        if (prior['target'] != ROBOTS or policy is None
                or not policy['new_allowed'] or not policy['legacy_allowed']
                or len(AUDIT['requests']) != 1):
            raise Stop('Exact current policy is required for the entry')
    wait_for_request(journal, not robots)
    if robots:
        journal.first_wire_proof()
    state = journal.read()
    state.update(logical_id=uuid.uuid4().hex,
                 physical_id=uuid.uuid4().hex, physical_started=now(),
                 target=url, in_flight=True, physical_pending=True,
                 outcome='reserved')
    state['starts'] = sorted(set(
        state['starts'] + [state['physical_started']]))
    state['observed_at'] = max(state['observed_at'], state['physical_started'])
    journal.save(state)
    headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'}
    req = urllib.request.Request(url, headers=headers, method='GET')
    journal.verify()
    journal.verify_capture()
    if dict((k.casefold(), v) for k, v in req.header_items()).get(
            'user-agent') != USER_AGENT:
        raise Stop('Actual outbound bot identity differs')
    receipt = {'url': url, 'request_id': state['physical_id'],
               'start': CLOCK.monotonic(), 'wall_start': now(),
               'completion': None, 'wall_completion': None,
               'request_headers': list(req.header_items()), 'headers': [],
               'segments': []}
    receipt['transport'] = {
        "statuses": [], "status_line_eof": False, "response_file_closed": False,
        "connection_closed": False, "opener_disconnected": False,
    }
    req._source_transport_witness = receipt['transport']
    AUDIT['requests'].append(receipt)
    response = None
    primary = None
    body = b''
    try:
        with operation_deadline():
            try:
                try:
                    response = OPENER.open(req, timeout=BODY_SECONDS)
                except urllib.error.HTTPError as error:
                    response = error
                except http.client.RemoteDisconnected:
                    receipt['transport']['opener_disconnected'] = True
                    raise
                status = response.status
                if type(status) is not int or not 200 <= status <= 599:
                    raise Stop('Unassessed response status')
                receipt['status'] = status
                known_status(journal, status, robots)
                response_headers = response.headers
                receipt['headers'] = list(response_headers.items())
                retry_after(journal, response_headers)
                length = framing(response_headers)
                while True:
                    try:
                        segment = response.read(
                            min(65536, MAX_BODY + 1 - len(body)))
                    except BaseException as error:
                        partial = getattr(error, 'partial', b'')
                        if isinstance(partial, bytes) and partial:
                            receipt['segments'].append(partial)
                        receipt['read_error'] = type(error).__name__
                        raise
                    if not isinstance(segment, bytes):
                        raise Stop('Unassessed response byte type')
                    receipt['segments'].append(segment)
                    body += segment
                    if len(body) > MAX_BODY:
                        raise Stop('Body exceeded research bound')
                    if not segment:
                        break
                if length is not None and len(body) != length:
                    raise Stop('Incomplete declared response body')
                encoding = response_headers.get('Content-Encoding', '')
                if encoding.casefold().strip() not in ('', 'identity'):
                    raise Stop('Unassessed encoding; raw body retained')
                challenge(journal, body, response_headers, robots, status)
                if 300 <= status < 400:
                    raise Stop('Redirect captured, never followed')
                if robots and status != 429:
                    new, legacy, unavailable = robot_policy(
                        journal, body, status)
                    state = journal.read()
                    state['policy'] = {
                        'url': ROBOTS, 'status': status,
                        'body_sha256': digest(body),
                        'unavailable': unavailable, 'new_allowed': new,
                        'legacy_allowed': legacy}
                    if not new or not legacy:
                        state['blocked'] = True
                    journal.save(state)
                elif not robots and status not in (200, 429):
                    raise Stop('Unexpected content status; no retry')
            except BaseException as error:
                primary = error
                raise
            finally:
                if response is not None:
                    try:
                        response.close()
                    except BaseException as error:
                        receipt['close_error'] = type(error).__name__
                        GATE_FAILED = True
                        if primary is None:
                            raise
                receipt['completion'] = CLOCK.monotonic()
                try:
                    receipt['wall_completion'] = now()
                except BaseException as error:
                    receipt['completion_clock_error'] = type(error).__name__
                    GATE_FAILED = True
                    if primary is None:
                        raise
        state = journal.read()
        stamp = now()
        state.update(completed_physical_id=state['physical_id'],
                     completed_logical_id=state['logical_id'],
                     completed=stamp, observed_at=max(state['observed_at'],
                                                     stamp),
                     in_flight=False, physical_pending=False,
                     outcome='response_assessed')
        state['not_before'] = max(state['not_before'],
                                  stamp + state['interval'])
        if not robots:
            state['inventory_completed'] = max(state['inventory_completed'],
                                                stamp)
        journal.save(state)
        if status == 429:
            raise RateLimited('Assessed rate limit; cooldown retained')
        if state['blocked']:
            raise Stop('Explicit current REP refusal retained')
        return body
    except BaseException as error:
        if not isinstance(error, RateLimited):
            GATE_FAILED = True
        receipt['error'] = type(error).__name__
        raise


def request(journal, url):
    """Latch every pre-opener or received-response failure in this process."""
    global GATE_FAILED
    try:
        return _request(journal, url)
    except BaseException:
        GATE_FAILED = True
        raise


def read_private_claim(parent, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=parent)
    primary = None
    expected = identity(os.fstat(fd))
    try:
        info = os.fstat(fd)
        private_file(info)
        raw = os.pread(fd, MAX_STATE + 1, 0)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (version(info) != version(os.fstat(fd))
                or identity(named) != expected or len(raw) > MAX_STATE):
            raise Stop('Claim changed during read')
        return raw
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close([(fd, expected)], primary)



def paths():
    leaf = FAMILY + "-" + str(os.getuid())
    return PARENT / leaf, PARENT / (leaf + "-issue442-initialized.claim")


REPOSITORY = "YouthOpps/data-pipeline"
API = "https://api.github.com"
WORKFLOW_PATH = ".github/workflows/fetch-" + SOURCE_ID + ".yml"
WORKFLOW_FILE = WORKFLOW_PATH.rsplit("/", 1)[1]
WORKFLOW_SHA256 = "9bb59b30401df986534f7610208574c9136e06681d50ebfe09613be8270775d6"
REVIEWED_QA_RUN = 44245821168
REVIEWED_QA_WORKFLOW = 422227671
REVIEWED_QA_JOB = 134604404648
REVIEWED_QA_ARTIFACT = 14401981609
REVIEWED_QA_HEAD = '1aa4872adfb2a702e86e48c1236ea3318bcaabbe'
REVIEWED_QA_BRANCH = 'qa/442-klagenfurt-native-5b50d7988e79a41e'
REVIEWED_QA_PATH = '.github/workflows/verify-at-klagenfurt-msc-international-management.yml'
REVIEWED_QA_ZIP_DIGEST = 'sha256:b87c7d7bed6a9bdbe79d88c88124cd3687149ded72b4f86d30c185ae78f321bb'
REVIEWED_QA_FLOOR_SHA256 = 'ae576d6d53f6859e7645af83bc0a8399a30788fdb06b8c7c7892e00e27aab094'
ARTIFACT_NAME = SOURCE_ID + "-pacing-state"
ADAPTER_PATH = f"adapters/{SOURCE_ID}/adapter.py"
FIRST_HOSTED = {'review_evidence': 'https://github.com/YouthOpps/data-pipeline/issues/442#issuecomment-6970279235', 'review_packet_sha256': '4db5275cca57cbffa42e3e5aaded1ef99c5a603fa8656c3c51c2056ebb3d6197', 'state_sha256': 'ae576d6d53f6859e7645af83bc0a8399a30788fdb06b8c7c7892e00e27aab094', 'state': {'blocked': False, 'completed': 1794980491.99347, 'completed_logical_id': 'f5c28eb5d7b34ba0b3be0820d00715b6', 'completed_physical_id': 'd9308b9a378a45fe805cbb51affd3f25', 'family': 'youthopps-klagenfurt-publisher-v1', 'in_flight': False, 'interval': 12.2, 'inventory_completed': 1794980491.99347, 'legacy_policy_history_unknown': True, 'legacy_rate_history_unknown': True, 'logical_id': 'f5c28eb5d7b34ba0b3be0820d00715b6', 'not_before': 1794980504.19347, 'observed_at': 1794980491.99347, 'outcome': 'response_assessed', 'page_interval': 30, 'physical_id': 'd9308b9a378a45fe805cbb51affd3f25', 'physical_pending': False, 'physical_started': 1794980490.894975, 'policy': {'body_sha256': '39bc1e5aa4eaf60bfd21e02483fbdea266a8a2bfbf04c3e4449086dffd819549', 'legacy_allowed': True, 'new_allowed': True, 'status': 200, 'unavailable': False, 'url': 'https://www.aau.at/robots.txt'}, 'rate_limit_strikes': 0, 'rate_limit_until': 0, 'rates': [], 'schema': 1, 'starts': [1794979669.9900095, 1794979671.975397, 1794979685.025137, 1794979686.6471522, 1794980478.036729, 1794980478.0384722, 1794980490.894975, 1794980490.8967896], 'target': 'https://www.aau.at/en/studien/master-international-management/'}}
HOSTED = os.environ.get("GITHUB_ACTIONS") == "true"


HOSTED_EXPORT = None


def own_source_hash():
    return digest(Path(__file__).read_bytes())


def api_bytes(repo, path, *, method="GET", payload=None, token_name="GITHUB_TOKEN",
              allow_missing=False, max_size=2_000_000):
    """Read or update only the exact GitHub repository and use no redirects."""
    if repo not in (REPOSITORY, "YouthOpps/data-source") or not path.startswith("/"):
        raise Stop("Untrusted GitHub API route")
    token = os.environ.get(token_name)
    if not token:
        raise Stop("Missing GitHub credential for " + token_name)
    request = urllib.request.Request(
        API + "/repos/" + repo + path,
        data=None if payload is None else canonical(payload),
        method=method,
        headers={"Authorization": "Bearer " + token, "User-Agent": USER_AGENT,
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"},
    )
    try:
        response = OPENER.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    except urllib.error.URLError:
        raise Stop("GitHub connection failed") from None
    with response:
        if response.status == 404 and allow_missing:
            return None
        if response.status not in (200, 201):
            raise Stop(f"GitHub API returned HTTP {response.status}")
        body = response.read(max_size + 1)
    if len(body) > max_size:
        raise Stop("Oversized GitHub response")
    return json.loads(body)


def workflow_api(path, *, allow_missing=False):
    return api_bytes(REPOSITORY, path, allow_missing=allow_missing)


def workflow_binding():
    """Authenticate the executing source and workflow at the immutable head."""
    if not HOSTED or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise Stop("Hosted guard requires the owning GitHub repository")
    head = os.environ.get("GITHUB_SHA", "")
    if os.environ.get("GITHUB_REF") != "refs/heads/main" or not re.fullmatch("[0-9a-f]{40}", head):
        raise Stop("Invalid current immutable hosted head")
    if (os.environ.get("GITHUB_WORKFLOW_SHA") != head
            or os.environ.get("GITHUB_WORKFLOW_REF")
            != REPOSITORY + "/" + WORKFLOW_PATH + "@refs/heads/main"):
        raise Stop("Invalid current immutable workflow reference")
    workflow = workflow_api("/actions/workflows/" + WORKFLOW_FILE)
    if workflow["path"] != WORKFLOW_PATH or workflow["state"] != "active" or type(workflow["id"]) is not int:
        raise Stop("Production workflow differs")
    if authenticated_blob_hash(WORKFLOW_PATH, head) != WORKFLOW_SHA256:
        raise Stop("Production workflow bytes differ from the reviewed definition")
    if authenticated_source_hash(head) != own_source_hash():
        raise Stop("Executing adapter differs from immutable head")
    run_id = int(os.environ["GITHUB_RUN_ID"])
    attempt = int(os.environ["GITHUB_RUN_ATTEMPT"])
    workflow_id = workflow["id"]
    if min(run_id, attempt, workflow_id) <= 0:
        raise Stop("Production workflow lacks its exact run identity")
    endpoint = f"/actions/runs/{run_id}/attempts/{attempt}"
    current = workflow_api(endpoint)
    check_run(current, workflow_id, run_id, attempt, head)
    if current["status"] != "in_progress":
        raise Stop("The original production attempt is not running")
    jobs = workflow_api(endpoint + "/jobs?per_page=100")
    if jobs.get("total_count") != 1 or len(jobs["jobs"]) != 1:
        raise Stop("The original production collect job is not unique")
    job = jobs["jobs"][0]
    if (type(job.get("id")) is not int or job["id"] <= 0
            or job.get("name") != "collect" or job.get("run_id") != run_id
            or job.get("run_attempt") != attempt or job.get("head_sha") != head
            or job.get("head_branch") != "main" or job.get("status") != "in_progress"):
        raise Stop("The original production collect job differs")
    return workflow_id, run_id, attempt, head


def authenticated_blob_hash(path, head):
    info = workflow_api("/contents/" + path + "?ref=" + head)
    if (info.get("encoding") != "base64" or info.get("path") != path
            or info.get("type") != "file" or not re.fullmatch(r"[0-9a-f]{40}", info.get("sha") or "")):
        raise Stop("Cannot authenticate immutable source-owned bytes")
    body = base64.b64decode("".join(info["content"].split()), validate=True)
    if len(body) != info["size"]:
        raise Stop("Incomplete immutable source-owned bytes")
    return digest(body)


def authenticated_source_hash(head):
    return authenticated_blob_hash(ADAPTER_PATH, head)


def check_run(row, workflow_id, run_id, attempt, head):
    if (
        type(row.get("id")) is not int or row["id"] != run_id
        or type(row.get("run_attempt")) is not int or row["run_attempt"] != attempt
        or row["head_sha"] != head or row["head_branch"] != "main"
        or row["workflow_id"] != workflow_id or row["path"] != WORKFLOW_PATH
        or row["repository"]["full_name"] != REPOSITORY
        or row["head_repository"]["full_name"] != REPOSITORY
    ):
        raise Stop("Untrusted exact source workflow attempt")


def workflow_history(binding):
    """Order terminal producer attempts by original exact-attempt job completion."""
    workflow_id, current_id, current_attempt, current_head = binding
    paths = f"/actions/workflows/{workflow_id}/runs?per_page=100&page="
    complete = []
    scanned = set()
    count = None
    for page in range(1, 101):
        result = workflow_api(paths + str(page))
        total, rows = result["total_count"], result["workflow_runs"]
        if type(total) is not int or total < 1 or type(rows) is not list or len(rows) > 100:
            raise Stop("Invalid source workflow inventory")
        if count is not None and count != total:
            raise Stop("Source workflow changed during restore")
        count = total
        for row in rows:
            run_id = row["id"]
            attempt = row["run_attempt"]
            head = row["head_sha"]
            if run_id in scanned:
                raise Stop("Duplicate source workflow run")
            scanned.add(run_id)
            check_run(row, workflow_id, run_id, attempt, head)
            if run_id == current_id:
                if (attempt, head) != (current_attempt, current_head):
                    raise Stop("Current attempt/head differs")
                attempt -= 1
            elif row["status"] != "completed":
                raise Stop("A parallel source attempt is not terminal")
            if attempt:
                endpoint = f"/actions/runs/{run_id}/attempts/{attempt}"
                exact = workflow_api(endpoint)
                check_run(exact, workflow_id, run_id, attempt, head)
                if exact["status"] != "completed":
                    raise Stop("Prior exact source attempt is not terminal")
                jobs = workflow_api(endpoint + "/jobs?per_page=100")
                if type(jobs["total_count"]) is not int or not 0 < jobs["total_count"] == len(jobs["jobs"]) <= 100:
                    raise Stop("Incomplete prior exact-attempt jobs")
                ends = []
                seen_jobs = set()
                for job in jobs["jobs"]:
                    if (type(job["id"]) is not int or job["id"] in seen_jobs
                            or job["run_id"] != run_id or job["run_attempt"] != attempt
                            or job["head_sha"] != head or job["head_branch"] != "main"
                            or job["status"] != "completed"):
                        raise Stop("Invalid prior source job provenance")
                    seen_jobs.add(job["id"])
                    begin = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00")).timestamp()
                    end = datetime.fromisoformat(job["completed_at"].replace("Z", "+00:00")).timestamp()
                    if not 0 <= begin <= end <= time.time() + 1:
                        raise Stop("Invalid prior job chronology")
                    ends.append(end)
                complete.append((max(ends), run_id, attempt, head))
        if len(scanned) >= count:
            if len(scanned) != count or current_id not in scanned:
                raise Stop("The complete list does not contain the current source attempt")
            complete.sort()
            if len(complete) > 1 and complete[-1][0] == complete[-2][0]:
                raise Stop("Ambiguous source ownership chronology")
            return complete[-1] if complete else None
        if len(rows) != 100:
            raise Stop("Truncated source workflow history")
    raise Stop("Source workflow inventory exceeds the metadata bound")


def artifact_bytes(artifact_id, expected):
    """Retrieve the owned public GitHub redirect with no credential crossover."""
    token = os.environ.get("GITHUB_TOKEN")
    request = urllib.request.Request(
        API + "/repos/" + REPOSITORY + f"/actions/artifacts/{artifact_id}/zip",
        headers={"Authorization": "Bearer " + token, "User-Agent": USER_AGENT},
    )
    try:
        response = OPENER.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        targets = response.headers.get_all("Location", [])
        response.read(1001)
        if response.status != 302 or len(targets) != 1:
            raise Stop("Owned state did not produce one artifact redirect")
    target = urllib.parse.urlsplit(targets[0])
    if (
        target.scheme != "https" or target.username is not None or not target.hostname
        or target.port is not None or target.fragment or not target.hostname.endswith(
            (".blob.core.windows.net", ".actions.githubusercontent.com")
        )
    ):
        raise Stop("Unexpected owned state artifact authority")
    request = urllib.request.Request(targets[0], headers={"User-Agent": USER_AGENT})
    try:
        with OPENER.open(request, timeout=20) as response:
            if response.status != 200:
                raise Stop("Owned state artifact retrieval failed")
            body = response.read(131073)
    except urllib.error.URLError:
        raise Stop("Owned state artifact transport failed") from None
    if len(body) > 131072 or expected != "sha256:" + digest(body):
        raise Stop("Owned state artifact bytes differ")
    return body


def owned_state(owner):
    _, run_id, attempt, head = owner
    inventory = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    rows = inventory["artifacts"]
    if type(inventory["total_count"]) is not int or not 0 < inventory["total_count"] == len(rows) <= 100:
        raise Stop("Newest exact attempt has no owned state inventory")
    matches = [row for row in rows if row["name"] == f"{ARTIFACT_NAME}-{attempt}"]
    if len(matches) != 1:
        raise Stop("Newest exact attempt has no unique owned state")
    record = matches[0]
    owner = record["workflow_run"]
    size = record["size_in_bytes"]
    if (
        record["expired"] is not False or type(record["id"]) is not int or record["id"] < 1
        or type(size) is not int or not 0 < size <= 131072
        or owner["id"] != run_id or owner["head_sha"] != head or owner["head_branch"] != "main"
        or owner["repository_id"] != 1163601208 or owner["head_repository_id"] != 1163601208
        or not re.fullmatch("sha256:[0-9a-f]{64}", record.get("digest") or "")
    ):
        raise Stop("Newest exact artifact provenance differs")
    body = artifact_bytes(record["id"], record["digest"])
    if len(body) != size:
        raise Stop("Newest exact artifact size differs")
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = archive.infolist()
        if len(entries) != 1 or entries[0].filename != "pacing-state.json":
            raise Stop("Unexpected owned state ZIP members")
        entry = entries[0]
        if entry.file_size > MAX_STATE + 1024 or entry.flag_bits & 1 or entry.external_attr >> 16 & 0o170000 == 0o120000:
            raise Stop("Unexpected owned state ZIP member")
        state_envelope = json.loads(archive.read(entry))
    expected = {"schema": 1, "repository": REPOSITORY, "source": SOURCE_ID, "run_id": run_id,
                "run_attempt": attempt, "head_sha": head, "adapter_sha256": authenticated_source_hash(head)}
    if set(state_envelope) != set(expected) | {"state", "state_sha256"}:
        raise Stop("Unexpected owned state envelope")
    if any(type(state_envelope[key]) is not type(value) or state_envelope[key] != value
           for key, value in expected.items()):
        raise Stop("Newest exact owned state has foreign provenance")
    state = validate(state_envelope["state"])
    if digest(canonical(state)) != state_envelope["state_sha256"]:
        raise Stop("Newest exact owned state has changed bytes")
    return state


def verify_reviewed_qa_owner():
    """Verify the exact retired nonpublishing GitHub Actions test behind the first floor."""
    workflow = workflow_api(f"/actions/workflows/{REVIEWED_QA_WORKFLOW}")
    if (workflow.get("id") != REVIEWED_QA_WORKFLOW or workflow.get("path") != REVIEWED_QA_PATH
            or workflow.get("state") != "disabled_manually"):
        raise Stop("The exact reviewed native verification workflow must be retired")
    inventory = workflow_api(f"/actions/workflows/{REVIEWED_QA_WORKFLOW}/runs?per_page=100")
    if inventory.get("total_count") != 1 or len(inventory["workflow_runs"]) != 1:
        raise Stop("The reviewed native workflow does not have one original run")
    endpoint = f"/actions/runs/{REVIEWED_QA_RUN}/attempts/1"
    attempt = workflow_api(endpoint)
    expected = {
        "id": REVIEWED_QA_RUN, "workflow_id": REVIEWED_QA_WORKFLOW, "run_attempt": 1,
        "head_sha": REVIEWED_QA_HEAD, "head_branch": REVIEWED_QA_BRANCH, "path": REVIEWED_QA_PATH,
        "status": "completed", "conclusion": "success", "event": "push",
    }
    for row in (attempt, inventory["workflow_runs"][0]):
        if (any(row.get(key) != value for key, value in expected.items())
                or row["repository"]["full_name"] != REPOSITORY
                or row["head_repository"]["full_name"] != REPOSITORY):
            raise Stop("The exact reviewed native attempt no longer matches")
    jobs = workflow_api(endpoint + "/jobs?per_page=100")
    expected_job = {
        "id": REVIEWED_QA_JOB, "name": "verify", "run_id": REVIEWED_QA_RUN, "run_attempt": 1,
        "head_sha": REVIEWED_QA_HEAD, "status": "completed", "conclusion": "success",
    }
    if (jobs.get("total_count") != 1 or len(jobs["jobs"]) != 1
            or any(jobs["jobs"][0].get(key) != value for key, value in expected_job.items())):
        raise Stop("The exact reviewed native job no longer matches")
    artifacts = workflow_api(f"/actions/runs/{REVIEWED_QA_RUN}/artifacts?per_page=100")
    if artifacts.get("total_count") != 1 or len(artifacts["artifacts"]) != 1:
        raise Stop("The reviewed native artifact owner is not unique")
    artifact = artifacts["artifacts"][0]
    if workflow_api(f"/actions/artifacts/{REVIEWED_QA_ARTIFACT}") != artifact:
        raise Stop("The retired QA artifact reports inconsistent metadata")
    owner = artifact["workflow_run"]
    if (artifact.get("id") != REVIEWED_QA_ARTIFACT or artifact.get("name") != "klagenfurt442-native-attempt-1"
            or artifact.get("expired") is not False or artifact.get("digest") != REVIEWED_QA_ZIP_DIGEST
            or owner["id"] != REVIEWED_QA_RUN or owner["head_sha"] != REVIEWED_QA_HEAD
            or owner["head_branch"] != REVIEWED_QA_BRANCH
            or owner["repository_id"] != 1163601208 or owner["head_repository_id"] != 1163601208):
        raise Stop("The exact reviewed original native artifact no longer matches")


def verify_first_production_grant(binding, evidence, packet_sha, floor_sha):
    comment_id = evidence.rsplit("-", 1)[-1]
    comment = workflow_api("/issues/comments/" + comment_id)
    exact = workflow_api(f"/actions/runs/{binding[1]}/attempts/1")
    required = (
        "Approved Klagenfurt production packet " + packet_sha,
        "Klagenfurt production floor " + floor_sha,
        f"Klagenfurt production native run {REVIEWED_QA_RUN}/1",
    )
    author = comment.get("user") or {}
    created = comment.get("created_at")
    lines = (comment.get("body") or "").splitlines()
    if (type(comment.get("id")) is not int or comment["id"] != int(comment_id)
            or author.get("id") != 12278069 or author.get("type") != "User"
            or comment.get("html_url") != evidence or comment.get("issue_url")
            != API + "/repos/YouthOpps/data-pipeline/issues/442"
            or not isinstance(created, str) or created != comment.get("updated_at")
            or created > exact["created_at"] or any(lines.count(value) != 1 for value in required)):
        raise Stop("First production publisher use lacks the exact original maintainer grant")


def hosted_floor(binding, owner):
    """Use a reviewed actual native floor only on the first hosted attempt."""
    if owner is not None:
        return owned_state(owner)
    if type(FIRST_HOSTED) is not dict or binding[2] != 1:
        raise Stop("No reviewed first-hosted state")
    evidence = FIRST_HOSTED.get("review_evidence", "")
    if (
        not re.fullmatch(r"https://github\.com/YouthOpps/data-pipeline/issues/442#issuecomment-[1-9][0-9]*", evidence)
        or evidence.endswith(("-6969970616", "-6970185955"))
    ):
        raise Stop("First hosted use needs its own reviewed production handoff")
    state = validate(FIRST_HOSTED["state"])
    if digest(canonical(state)) != FIRST_HOSTED["state_sha256"] or FIRST_HOSTED["state_sha256"] != REVIEWED_QA_FLOOR_SHA256:
        raise Stop("Reviewed native publisher state differs")
    packet_sha = FIRST_HOSTED.get("review_packet_sha256")
    if not isinstance(packet_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", packet_sha):
        raise Stop("Missing reviewed production packet identity")
    verify_first_production_grant(binding, evidence, packet_sha, REVIEWED_QA_FLOOR_SHA256)
    verify_reviewed_qa_owner()
    return state


def initialize_hosted(state):
    """Create ephemeral runner ownership only after immutable workflow proof."""
    leaf, _ = paths()
    handles = []
    primary = None
    try:
        parent = os.open(PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent_info = os.fstat(parent)
        handles.append((parent, identity(parent_info)))
        parent_safe(parent_info)
        if identity(parent_info) != identity(PARENT.lstat()):
            raise Stop("Temporary parent identity changed")
        os.mkdir(leaf.name, 0o700, dir_fd=parent)
        directory = os.open(leaf.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        directory_info = os.fstat(directory)
        handles.append((directory, identity(directory_info)))
        values = {}
        for name, body in (("state.json", canonical(state)), ("state.lock", b""), ("collection.lock", b"")):
            am, ident = create_once(directory, name, json.loads(body)) if body else create_empty(directory, name)
            if am != body:
                raise Stop("Initial original journal differs")
            values[name] = list(ident)
        if (identity(os.stat(leaf.name, dir_fd=parent, follow_symlinks=False)) != identity(directory_info)
                or identity(PARENT.lstat()) != identity(parent_info)):
            raise Stop("Owned state namespace changed")
        os.fsync(parent)
        return {"directory_identity": list(identity(directory_info)),
                "directory_metadata": [directory_info.st_uid, directory_info.st_gid, stat.S_IMODE(directory_info.st_mode)],
                "state_metadata": [os.getuid(), os.getgid(), 0o600, 1],
                "parent_identity": list(identity(parent_info)),
                "parent_metadata": [parent_info.st_uid, parent_info.st_gid, stat.S_IMODE(parent_info.st_mode)],
                "locks": {name: values[name] for name in ("state.lock", "collection.lock")},
                "state_identity": values["state.json"]}
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close(handles, primary)


def create_empty(directory, name):
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    identity_before = identity(os.fstat(fd))
    primary = None
    try:
        private_file(os.fstat(fd))
        os.fsync(fd)
        os.fsync(directory)
        if os.read(fd, 1) or identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != identity_before:
            raise Stop("Initial lock changed")
        return b"", identity_before
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close([(fd, identity_before)], primary)


def hosted_authority():
    binding = workflow_binding()
    owner = workflow_history(binding)
    state = copy.deepcopy(hosted_floor(binding, owner))
    if state["blocked"] or state["in_flight"] or state["physical_pending"]:
        raise Stop("Newest state has unresolved access restrictions")
    now_value = now()
    state["observed_at"] = max(state["observed_at"], now_value)
    state["not_before"] = max(state["not_before"], now_value + 60)
    identity_proof = initialize_hosted(state)

    def verify():
        if workflow_binding() != binding or workflow_history(binding) != owner:
            raise Stop("Workflow ownership changed before first physical request")

    return binding, identity_proof, verify


def collect(*, publishing=False):
    """Collect both official registration windows."""
    global HOSTED_EXPORT
    if GATE_FAILED or AUDIT["requests"]:
        raise Stop("Collection cannot reenter the original publisher authority")
    if not publishing:
        raise Stop("Nonpublishing collection requires its reviewed GitHub Actions attempt")
    binding, expected, proof = hosted_authority()
    journal = Journal(expected, proof)
    records = None
    failure = None
    with journal:
        if journal.state["blocked"] or journal.state["in_flight"] or journal.state["physical_pending"]:
            raise Stop("Publisher state has unresolved access restrictions")
        journal.capture_claim = create_once(journal.directory, "collection.claim", {
            "source_sha256": own_source_hash(),
            "state_sha256": digest(journal.ack),
            "binding": binding,
        })
        settled = journal.read()
        stamp = now()
        settled["observed_at"] = max(settled["observed_at"], stamp)
        settled["not_before"] = max(settled["not_before"], stamp + 60)
        journal.save(settled)
        try:
            request(journal, ROBOTS)
            raw = request(journal, ENTRY)
            headers = Message()
            for key, value in AUDIT["requests"][-1]["headers"]:
                headers[key] = value
            if headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise Stop("Unexpected MSc admissions document type")
            if denies_indexing(headers.get_all("X-Robots-Tag", []), headers=True):
                raise IndexingDenied("The publisher declined indexing of the MSc admissions response")
            document = raw.decode(headers.get_content_charset() or "utf-8", "strict")
            checked_at = datetime.now(timezone.utc)
            page = Visible()
            page.feed(document)
            page.close()
            visible = " ".join(" ".join(page.text).split())
            if re.search(
                r"\b(?:automated\s+access|scrap(?:e|ing)|crawler|republication|automatisiert(?:e[nr]?)?\s+(?:abruf|erfassung|zugriff))\b",
                visible, re.I,
            ):
                raise IndexingDenied("Public admission page requires renewed policy review")
            records = normalize(extract_facts(document), checked_at)
            validate_records(records)
        except IndexingDenied as error:
            hold = journal.read()
            hold["blocked"] = True
            journal.save(hold)
            failure = error
        except Exception as error:
            failure = error
        finally:
            state = copy.deepcopy(journal.read())
    if binding:
        HOSTED_EXPORT = (binding, state, expected, journal.capture_claim)
    if failure is not None:
        raise failure
    return records


def data_api(path, *, method="GET", payload=None, allow_missing=False):
    return api_bytes("YouthOpps/data-source", path, method=method, payload=payload,
                     token_name="DATA_SOURCE_TOKEN", allow_missing=allow_missing)


def read_snapshot():
    head = data_api("/git/ref/heads/main")["object"]["sha"]
    tree = data_api(f"/git/commits/{head}")["tree"]["sha"]
    files = {}
    for name in ("data.json", "metadata.json"):
        file = data_api(f"/contents/datas/{SOURCE_ID}/{name}?ref={head}", allow_missing=True)
        if file is not None and (file["encoding"] != "base64"
                or file["path"] != f"datas/{SOURCE_ID}/{name}"):
            raise Stop("Incomplete or foreign source snapshot")
        files[name] = None if file is None else base64.b64decode(file["content"]).decode("utf-8")
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise Stop("Incomplete source snapshot")
    return head, tree, files


def publish_files(snapshot, files):
    """Commit exactly scoped data and metadata together using a non-forced ref."""
    head, tree, previous = snapshot
    staged = []
    for name, value in files.items():
        if name not in {"data.json", "metadata.json"}:
            raise Stop("Unexpected source publication file")
        encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if previous[name] == encoded:
            continue
        blob = data_api("/git/blobs", method="POST", payload={
            "content": base64.b64encode(encoded.encode()).decode(),
            "encoding": "base64",
        })
        staged.append({"path": f"datas/{SOURCE_ID}/{name}", "mode": "100644",
                       "type": "blob", "sha": blob["sha"]})
    if not staged:
        return
    updated = data_api("/git/trees", method="POST", payload={"base_tree": tree, "tree": staged})
    commit = data_api("/git/commits", method="POST", payload={
        "message": f"Refresh {SOURCE_ID}",
        "tree": updated["sha"], "parents": [head],
    })
    data_api("/git/refs/heads/main", method="PATCH",
             payload={"sha": commit["sha"], "force": False})


def publish_checked(snapshot, files):
    """Retry only when an unrelated source advanced the shared branch."""
    for attempt in range(3):
        try:
            publish_files(snapshot, files)
            return
        except Stop:
            current = read_snapshot()
            contents = current[2]
            expected = {**snapshot[2], **{
                name: json.dumps(value, ensure_ascii=False, indent=2) + "\n"
                for name, value in files.items()
            }}
            if contents == expected:
                return
            if attempt == 2 or current[0] == snapshot[0] or contents != snapshot[2]:
                raise
            snapshot = current


def metadata(attempt, previous, records, *, checked_at, failure=None):
    failed = failure is not None
    return {
        "display_name": DISPLAY_NAME,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": "en",
        "publisher_country": "AT",
        "publisher_type": "university",
        "attribution": DISPLAY_NAME,
        "status": "fail" if failed else "success",
        "last_attempt_at": checked_time(attempt),
        "last_success_at": previous.get("last_success_at") if failed else checked_time(attempt),
        "last_checked_at": previous.get("last_checked_at") if failed else checked_time(checked_at),
        "record_count": len(records),
        "message": ("Collection failed; retained the last successful dataset" if failed
                    else f"Collected and validated {len(records)} current Klagenfurt MSc selection records"),
        "failure_stage": "collection" if failed else None,
        "error": "The source could not produce a validated current intake" if failed else None,
    }


def bound_snapshot(snapshot):
    files = snapshot[2]
    if files["data.json"] is None:
        return [], {}
    records = json.loads(files["data.json"])
    previous = json.loads(files["metadata.json"])
    validate_records(records, allow_empty=True)
    if previous["source"] != SOURCE_ID or previous["source_url"] != SOURCE_URL:
        raise Stop("Source metadata provenance differs")
    return records, previous


def publish():
    """Publish a complete valid refresh or preserve the previous dataset."""
    snapshot = read_snapshot()
    old, previous = bound_snapshot(snapshot)
    attempt = datetime.now(timezone.utc)
    try:
        records = collect(publishing=True)
        by_id = {record["id"]: record for record in old}
        for record in records:
            before = by_id.get(record["id"])
            if before is None:
                continue
            same = all(before.get(key) == value for key, value in record.items()
                       if key not in TIMESTAMPS)
            record["created_at"] = before["created_at"]
            record["first_seen_at"] = before["first_seen_at"]
            if same:
                record["updated_at"] = before["updated_at"]
        checked_at = datetime.now(timezone.utc)
        visible = expire(records, checked_at)
        validate_records(visible, allow_empty=True)
        publish_checked(snapshot, {"data.json": visible, "metadata.json": metadata(
            attempt, previous, visible, checked_at=checked_at)})
    except Exception as failure:
        if snapshot[2]["data.json"] is not None:
            original = snapshot[2]
            latest = read_snapshot()
            if latest[2] == original:
                publish_checked(latest, {"metadata.json": metadata(
                    attempt, previous, old, checked_at=attempt, failure=failure)})
        raise


def export_state():
    """Create the exact owned artifact after closure, with no overwrite path."""
    if HOSTED_EXPORT is None:
        return
    binding, state, expected, claim = HOSTED_EXPORT
    if workflow_binding() != binding:
        raise Stop("Production ownership changed before artifact export")
    encoded_state = canonical(state)
    with Journal(expected, lambda: None) as journal:
        if journal.ack != encoded_state:
            raise Stop("Publisher journal changed before artifact export")
        journal.capture_claim = claim
        journal.verify_capture()
        marker = json.loads(claim[0])
        if marker["source_sha256"] != own_source_hash() or marker["binding"] != list(binding):
            raise Stop("Publisher collection authority changed before artifact export")
        writes = AUDIT["persistence"]
        if (not writes or not writes[-1]["acknowledged"]
                or writes[-1].get("actual_journal_bytes") != encoded_state):
            raise Stop("Publisher journal lacks exact original acknowledgements")
    write_owned_state(binding, state)


def write_owned_state(binding, state, *, restrictive_owner=None):
    """Write the verified budget in the current attempt's owned envelope."""
    _, run_id, attempt, head = binding
    runner = Path(os.environ["RUNNER_TEMP"])
    target = Path(os.environ["KLAGENFURT_PACING_ARTIFACT_PATH"])
    if runner.resolve() != runner or target != runner / "pacing-state.json":
        raise Stop("Owned artifact path differs")
    payload = {
        "schema": 1, "repository": REPOSITORY, "source": SOURCE_ID,
        "run_id": run_id, "run_attempt": attempt, "head_sha": head,
        "adapter_sha256": own_source_hash(), "state": validate(state),
        "state_sha256": digest(canonical(state)),
    }
    parent = os.open(runner, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_id = identity(os.fstat(parent))
    primary = None
    try:
        parent_safe(os.fstat(parent))
        if identity(runner.lstat()) != parent_id:
            raise Stop("Original artifact parent differs")
        raw, state_id = create_once(parent, target.name, payload)
        if restrictive_owner is not None:
            # No source request or publication was made. Only the latest verified
            # predecessor may pass on its still-restrictive state to this job.
            if workflow_binding() != binding or workflow_history(binding) != restrictive_owner:
                raise Stop("Production ownership changed before restrictive state export")
        descriptor = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        descriptor_identity = identity(os.fstat(descriptor))
        try:
            before = os.fstat(descriptor)
            private_file(before)
            if (identity(before) != state_id or os.pread(descriptor, len(raw) + 1, 0) != raw
                    or version(before) != version(os.fstat(descriptor))
                    or identity(runner.lstat()) != parent_id
                    or identity(os.stat(target.name, dir_fd=parent, follow_symlinks=False)) != state_id):
                raise Stop("Closed original artifact changed")
        finally:
            checked_close([(descriptor, descriptor_identity)], sys.exc_info()[1])
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close([(parent, parent_id)], primary)
    output = Path(os.environ["GITHUB_OUTPUT"])
    fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise Stop("Invalid owned workflow output")
        if os.write(fd, b"pacing_ready=true\n") != len(b"pacing_ready=true\n"):
            raise Stop("Incomplete pacing artifact confirmation")
        os.fsync(fd)
    finally:
        os.close(fd)


def require_settled_production():
    """Authenticate the producer and its pacing floor before opening the data-source repository."""
    binding = workflow_binding()
    owner = workflow_history(binding)
    state = hosted_floor(binding, owner)
    if any(state[key] for key in ("blocked", "in_flight", "physical_pending")):
        if owner is not None and not GATE_FAILED and not AUDIT["requests"] and not AUDIT["persistence"]:
            write_owned_state(binding, state, restrictive_owner=owner)
        raise Stop("Newest publisher state has unresolved access restrictions")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Publish this source on its owning Action")
    args = parser.parse_args()
    if args.publish and not HOSTED:
        raise Stop("Only the owning GitHub Actions workflow may publish")
    if not args.publish:
        print(json.dumps(collect(), ensure_ascii=False))
        return
    require_settled_production()
    try:
        publish()
    finally:
        export_state()


if __name__ == "__main__":
    try:
        main()
    except Exception as problem:
        for request in AUDIT["requests"]:
            # Preserve observed protocol boundaries and exact request identity for recovery.
            # This diagnostic excludes response bodies and headers.
            observation = {key: request.get(key) for key in (
                "request_id", "url", "status", "transport", "wall_start", "wall_completion",
                "start", "completion", "error", "read_error", "close_error", "completion_clock_error",
            )}
            print("Publisher transport " + canonical(observation).decode(), file=sys.stderr)
        print(type(problem).__name__ + ": source collection or publication failed", file=sys.stderr)
        sys.exit(1)
