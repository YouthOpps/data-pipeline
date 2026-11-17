"""Collect the official Warsaw University of Technology MSc admissions intake."""

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
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
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


FAMILY = 'youthopps-warsaw-tech-publisher-v1'
USER_AGENT = 'YouthOppsBot/1.0 (+https://youthopps.org/bot/)'
ROBOTS = 'https://www.students.pw.edu.pl/robots.txt'
ENTRY = 'https://www.students.pw.edu.pl/index.php/How-to-Apply/Admission-to-M.Sc'
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


CALENDAR = "https://www.students.pw.edu.pl/index.php/How-to-Apply/Admission-to-M.Sc"
MONTHS = {
    month: number for number, month in enumerate(
        ("January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"), 1
    )
}
DATE = r"(\d{1,2}) (" + "|".join(MONTHS) + r") (20\d\d)"
OPENING = re.compile(r"Starting date for IRK registration:\s*" + DATE)
APPLICATION = re.compile(r"Deadline to create a new application:\s*" + DATE)
DOCUMENTS = re.compile(
    r"Deadline to upload the Admission Documents\s*\(for qualified candidates\):\s*" + DATE
)
CLASSES = re.compile(r"Beginning of classes:\s*" + DATE)
HIDDEN = frozenset({"script", "style", "nav", "footer", "template", "noscript"})
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"})
SEPARATORS = frozenset({"h1", "h2", "h3", "p", "li", "br", "div", "td", "tr"})


class AdmissionError(ValueError):
    """The publisher page does not establish the intended intake."""


class AdmissionText(HTMLParser):
    """Capture visible text and link targets without following them."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.fragments = []
        self.links = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        style = re.sub(r"\s+", "", attributes.get("style") or "").casefold()
        hidden = (
            tag in HIDDEN or "hidden" in attributes
            or (attributes.get("aria-hidden") or "").casefold() == "true"
            or "display:none" in style or "visibility:hidden" in style
            or bool(self.stack and self.stack[-1][1])
        )
        if not hidden and tag in SEPARATORS:
            self.fragments.append(" ")
        if not hidden and tag == "a" and attributes.get("href"):
            self.links.append(urllib.parse.urljoin(CALENDAR, attributes["href"]))
        if tag not in VOID:
            self.stack.append((tag, hidden))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            name, hidden = self.stack[index]
            if name == tag:
                del self.stack[index:]
                if not hidden and tag in SEPARATORS:
                    self.fragments.append(" ")
                return

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.fragments.append(data)


def one_date(pattern, text):
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise AdmissionError("The February intake date is missing or ambiguous")
    day, month, year = matches[0]
    return date(int(year), MONTHS[month], int(day))


def extract_facts(document):
    """Parse the one reviewed intake, keeping the four dates distinct."""
    parser = AdmissionText(document)
    text = " ".join("".join(parser.fragments).split())
    heading = re.search(r"Admission to M\.Sc\.\s+FEBRUARY INTAKE\b", text)
    if heading is None:
        raise AdmissionError("The reviewed MSc February intake is missing")
    scope = text[heading.end():].split("STEP 1:", 1)
    if len(scope) != 2:
        raise AdmissionError("The reviewed intake boundaries changed")
    start = one_date(OPENING, scope[0])
    deadline = one_date(APPLICATION, scope[0])
    documents = one_date(DOCUMENTS, scope[0])
    classes = one_date(CLASSES, scope[0])
    if not start < deadline < documents < classes or classes.month != 2:
        raise AdmissionError("Intake dates are inconsistent")
    if "Bachelor's Degree in a similar field" not in scope[1]:
        raise AdmissionError("The reviewed prior-degree requirement changed")
    if not any(
        (parts := urllib.parse.urlsplit(link)).scheme in {"http", "https"}
        and parts.netloc == "irk.pw.edu.pl" and not parts.username
        for link in parser.links
    ):
        raise AdmissionError("The official application route is missing")
    return {
        "id": f"pl-warsaw-tech-msc-admissions-february-{classes.year}",
        "published_opens": start.isoformat(),
        "application_deadline": deadline.isoformat(),
        "qualified_documents_deadline": documents.isoformat(),
        "classes_start": classes.isoformat(),
        "official_source_url": CALENDAR,
    }


SOURCE_ID = "pl-warsaw-tech-msc-admissions"
SOURCE_URL = "https://www.students.pw.edu.pl/index.php/How-to-Apply/Admission-to-M.Sc"
WEBSITE_URL = "https://www.students.pw.edu.pl/"
WARSAW = ZoneInfo("Europe/Warsaw")
TIMESTAMPS = ("created_at", "updated_at", "first_seen_at", "last_seen_at", "last_checked_at")
DISPLAY_NAME = "Warsaw University of Technology International Students Office"


def checked_time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("A timezone-aware check time is required")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def day_label(day):
    return day.strftime("%d %B %Y").lstrip("0")


def normalize(facts, checked_at):
    """Use the initial application cutoff rather than the later upload cutoff."""
    stamp = checked_time(checked_at)
    opening = date.fromisoformat(facts["published_opens"])
    deadline = date.fromisoformat(facts["application_deadline"])
    documents = date.fromisoformat(facts["qualified_documents_deadline"])
    classes = date.fromisoformat(facts["classes_start"])
    current_day = checked_at.astimezone(WARSAW).date()
    if current_day > deadline:
        status = "expired"
    elif current_day >= opening:
        status = "open"
    else:
        status = "unknown"
    return {
        "id": facts["id"],
        "title": f"February {classes.year} MSc intake for international applicants",
        "url": SOURCE_URL,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": None,
        "summary": (
            f"Warsaw University of Technology accepts applications for its February "
            f"{classes.year} international MSc intake from {day_label(opening)}. "
            f"Applicants need a bachelor's degree in a related field. Qualified "
            f"applicants submit the separate admission documents by {day_label(documents)}. "
            f"Classes begin on {day_label(classes)}. Check the university's requirements "
            f"for the chosen programme and any special procedures."
        ),
        "tags": ["masters", "admissions", "international-students"],
        "location": None,
        "deadline": deadline.isoformat(),
        "language": "en",
        "category": "other",
        "categories": ["other"],
        "kind": "opportunity",
        "host_countries": ["PL"],
        "eligible_countries": [],
        "publisher_country": "PL",
        "status": status,
        "classification": {
            "method": "publisher-admissions-calendar",
            "status": "unknown",
            "evidence": ["February MSc intake published by the International Students Office"],
        },
        **dict.fromkeys(TIMESTAMPS, stamp),
    }


def expire(records, checked_at):
    """Remove an intake only after its published date has ended in Poland."""
    checked_time(checked_at)
    today = checked_at.astimezone(WARSAW).date()
    return [record for record in records if date.fromisoformat(record["deadline"]) >= today]


def validate_records(records, *, allow_empty=False):
    if type(records) is not list or len(records) > 1 or (not allow_empty and not records):
        raise ValueError("Expected the one complete MSc intake")
    for record in records:
        if not (
            record["source"] == SOURCE_ID
            and record["source_url"] == record["url"] == SOURCE_URL
            and re.fullmatch(r"pl-warsaw-tech-msc-admissions-february-20\d\d", record["id"])
            and record["category"] == "other"
            and record["host_countries"] == ["PL"]
            and record["status"] in {"open", "expired", "unknown"}
            and type(record["title"]) is str and record["title"].strip()
            and type(record["summary"]) is str
            and len(record["summary"].encode("utf-16-le")) // 2 <= 600
        ):
            raise ValueError("Malformed or foreign admission opportunity")
        for field in TIMESTAMPS:
            value = record[field]
            checked_time(datetime.fromisoformat(value.replace("Z", "+00:00")))
        if date.fromisoformat(record["deadline"]).isoformat() != record["deadline"]:
            raise ValueError("Invalid admission cutoff")


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
    fields = headers.get_all('Retry-After', [])
    date = '(?:' + '|'.join(date_patterns()) + ')'
    ambiguous = False
    state = journal.read()
    stamp = now()
    untils = []
    for field in fields:
        if not isinstance(field, str):
            ambiguous = True
            continue
        value = field.strip()
        values = [value]
        if ',' in value and not re.fullmatch(date, value, re.I):
            ambiguous = True
            values = [m[1] for m in re.finditer(
                r'(?:^|,\s*)(' + date + r'|[0-9]+)(?=\s*(?:,|$))',
                value, re.I)]
        for value in values:
            try:
                if re.fullmatch('[0-9]+', value):
                    if len(value) > 12:
                        raise ValueError('Unsupported positive delay')
                    until = stamp + int(value)
                else:
                    until = http_date(value, stamp)
                    if until is None:
                        # Invalid fields create no hold or weaker allowance.
                        if value:
                            ambiguous = True
                        continue
                if not math.isfinite(until) or until > MAX_CLOCK:
                    raise ValueError('Unsupported positive clock')
                if until > stamp:
                    untils.append(until)
            except (ValueError, OverflowError, TypeError):
                ambiguous = True
    if untils:
        state['not_before'] = max(state['not_before'], *untils)
        state['rate_limit_until'] = max(state['rate_limit_until'], *untils)
        journal.save(state)
    if ambiguous:
        raise Stop('Unassessed Retry-After framing; pending retained')


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        style = re.sub(r'\s+', '', attrs.get('style', '').lower())
        hidden = (tag in ('script', 'style', 'template', 'nav', 'aside',
                          'footer') or 'hidden' in attrs
                  or attrs.get('aria-hidden', '').lower() == 'true'
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


OPENER = urllib.request.build_opener(NoRedirect())


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
            or urllib.parse.urlsplit(url).hostname != 'www.students.pw.edu.pl'):
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
    return PARENT / leaf, PARENT / (leaf + "-issue426-initialized.claim")


REPOSITORY = "YouthOpps/data-pipeline"
API = "https://api.github.com"
WORKFLOW_PATH = ".github/workflows/fetch-" + SOURCE_ID + ".yml"
WORKFLOW_FILE = WORKFLOW_PATH.rsplit("/", 1)[1]
ARTIFACT_NAME = SOURCE_ID + "-pacing-state"
ADAPTER_PATH = f"adapters/{SOURCE_ID}/adapter.py"
NATIVE_LOCAL = None
FIRST_HOSTED = None
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


def native_workflow_guard():
    """The initial local collection requires the future workflow to be absent."""
    if HOSTED or os.environ.get("DATA_SOURCE_TOKEN"):
        raise Stop("Native collection refuses a production context")
    if workflow_api("/actions/workflows/" + WORKFLOW_FILE, allow_missing=True) is not None:
        raise Stop("A hosted workflow already owns the publisher allowance")


def native_authority():
    """Bind the only local continuation to original first-contact identities."""
    native_workflow_guard()
    if (
        type(NATIVE_LOCAL) is not dict
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/426#issuecomment-[1-9][0-9]*",
            NATIVE_LOCAL["review_evidence"],
        )
        or NATIVE_LOCAL["review_evidence"].endswith("-6960751347")
    ):
        raise Stop("Native collection authority is disabled")
    if digest(Path("/etc/machine-id").read_bytes()) != NATIVE_LOCAL["machine_sha256"]:
        raise Stop("Original publisher machine differs")
    proof = Path(NATIVE_LOCAL["initializer_proof"])
    raw = proof.read_bytes()
    if digest(raw) != NATIVE_LOCAL["initializer_proof_sha256"]:
        raise Stop("Changed original initialization proof")
    initialized = json.loads(raw)
    if (initialized["initializer_exit"] != 0 or initialized["initializer_stderr"] != ""
            or initialized["schema"] != 1):
        raise Stop("Unverified original initializer result")
    result = initialized["result"]
    if result["family"] != FAMILY or result["machine_sha256"] != NATIVE_LOCAL["machine_sha256"]:
        raise Stop("Changed original first-contact authority")
    return result


def open_native(journal, expected):
    leaf, claim = paths()
    proof = read_private_claim(journal.parent, claim.name)
    current = os.stat(claim.name, dir_fd=journal.parent, follow_symlinks=False)
    if json.loads(proof) != expected or list(identity(current)) != expected["initializer_claim_identity"]:
        raise Stop("Original first-contact claim differs")
    prior = read_private_claim(journal.directory, "issue426-phaseA-capture.claim")
    if digest(prior) != NATIVE_LOCAL["previous_capture_claim_sha256"]:
        raise Stop("Original capture claim differs")
    if digest(journal.ack) != NATIVE_LOCAL["state_sha256"]:
        raise Stop("Original research state is not the approved generation")


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
    blob = workflow_api("/contents/" + WORKFLOW_PATH + "?ref=" + head)
    if not isinstance(blob.get("sha"), str):
        raise Stop("Production workflow has no immutable blob")
    if authenticated_source_hash(head) != own_source_hash():
        raise Stop("Executing adapter differs from immutable head")
    return workflow["id"], int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]), head


def authenticated_source_hash(head):
    info = workflow_api("/contents/" + ADAPTER_PATH + "?ref=" + head)
    if info.get("encoding") != "base64" or info.get("path") != ADAPTER_PATH:
        raise Stop("Cannot authenticate immutable adapter bytes")
    body = base64.b64decode("".join(info["content"].split()), validate=True)
    if len(body) != info["size"]:
        raise Stop("Incomplete immutable adapter bytes")
    return digest(body)


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
    if (
        record["expired"] is not False or type(record["id"]) is not int or record["id"] < 1
        or record["workflow_run"]["id"] != run_id or record["workflow_run"]["head_sha"] != head
        or not re.fullmatch("sha256:[0-9a-f]{64}", record.get("digest") or "")
    ):
        raise Stop("Newest exact artifact provenance differs")
    body = artifact_bytes(record["id"], record["digest"])
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


def hosted_floor(binding, owner):
    """Use a reviewed actual native floor only on the first hosted attempt."""
    if owner is not None:
        return owned_state(owner)
    if type(FIRST_HOSTED) is not dict or binding[2] != 1:
        raise Stop("No reviewed first-hosted state")
    evidence = FIRST_HOSTED.get("review_evidence", "")
    if not re.fullmatch(r"https://github\.com/YouthOpps/data-pipeline/issues/426#issuecomment-[1-9][0-9]*", evidence):
        raise Stop("First hosted use needs a reviewed native handoff")
    state = validate(FIRST_HOSTED["state"])
    if digest(canonical(state)) != FIRST_HOSTED["state_sha256"]:
        raise Stop("Reviewed native publisher state differs")
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


CI_QA = {'review_evidence': 'https://github.com/YouthOpps/data-pipeline/issues/426#issuecomment-6961652618', 'branch': 'qa/426-warsaw-5b7b9756cb9263cc', 'state': {'blocked': False, 'completed': 1794949068.7484996, 'completed_logical_id': '47d8cd0ce6634407b688beafc7b84fc6', 'completed_physical_id': 'c877511649c740d28b575018caa8b020', 'family': 'youthopps-warsaw-tech-publisher-v1', 'in_flight': False, 'interval': 12.2, 'inventory_completed': 1794949068.7484996, 'legacy_policy_history_unknown': True, 'legacy_rate_history_unknown': True, 'logical_id': '47d8cd0ce6634407b688beafc7b84fc6', 'not_before': 1794949080.9484997, 'observed_at': 1794949068.7484996, 'outcome': 'response_assessed', 'page_interval': 30, 'physical_id': 'c877511649c740d28b575018caa8b020', 'physical_pending': False, 'physical_started': 1794949066.881894, 'policy': {'body_sha256': '8bea1cd85dbaf24bc48309ab59d241a13d12531a7fa6b957282b7569415b843b', 'legacy_allowed': True, 'new_allowed': True, 'status': 404, 'unavailable': True, 'url': 'https://www.students.pw.edu.pl/robots.txt'}, 'rate_limit_strikes': 0, 'rate_limit_until': 0, 'rates': [], 'schema': 1, 'starts': [1794949050.1421688, 1794949066.881894], 'target': 'https://www.students.pw.edu.pl/index.php/How-to-Apply/Admission-to-M.Sc'}, 'state_sha256': '04ca8d483820e9b5373ccc97c45d333c632054012865c3f9ec4e8ba1e993b96c', 'research_observations': [{'request_id': 'fc1c1dc3980040c790b32f88ff8649f0', 'target': 'https://www.students.pw.edu.pl/robots.txt', 'reservation': 1794949050.1421688, 'pre_opener_wall': 1794949050.1462028}, {'request_id': 'c877511649c740d28b575018caa8b020', 'target': 'https://www.students.pw.edu.pl/index.php/How-to-Apply/Admission-to-M.Sc', 'reservation': 1794949066.881894, 'pre_opener_wall': 1794949066.8839767}]}
NATIVE_TEST_EXPORT = None


def ci_qa_binding():
    """Admit exactly one authenticated first attempt in a separate QA workflow."""
    config = CI_QA
    if (
        not __debug__
        or not HOSTED
        or os.environ.get("DATA_SOURCE_TOKEN")
        or type(config) is not dict
        or not re.fullmatch(
            r"https://github\.com/YouthOpps/data-pipeline/issues/426#issuecomment-[1-9][0-9]*",
            config.get("review_evidence", ""),
        )
        or config["review_evidence"].endswith("-6960751347")
    ):
        raise Stop("Native GitHub Actions authority is disabled")
    branch = config["branch"]
    if not re.fullmatch(r"qa/426-warsaw-[a-f0-9]{16}", branch):
        raise Stop("Unexpected native GitHub Actions branch")
    head = os.environ.get("GITHUB_SHA", "")
    run_id = int(os.environ.get("GITHUB_RUN_ID", "0"))
    path = ".github/workflows/verify-" + SOURCE_ID + ".yml"
    if (
        not re.fullmatch(r"[a-f0-9]{40}", head)
        or run_id <= 0
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != "refs/heads/" + branch
        or os.environ.get("GITHUB_EVENT_NAME") != "push"
        or os.environ.get("GITHUB_RUN_ATTEMPT") != "1"
        or os.environ.get("GITHUB_WORKFLOW_SHA") != head
        or os.environ.get("GITHUB_WORKFLOW_REF")
        != REPOSITORY + "/" + path + "@refs/heads/" + branch
    ):
        raise Stop("Native GitHub Actions attempt context differs")
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if (event.get("created") is not True or event.get("before") != "0" * 40
            or event.get("after") != head or event.get("ref") != "refs/heads/" + branch):
        raise Stop("Native workflow does not own the original branch creation")
    if workflow_api("/actions/workflows/" + WORKFLOW_FILE, allow_missing=True) is not None:
        raise Stop("A production workflow already owns the publisher allowance")
    exact = workflow_api(f"/actions/runs/{run_id}/attempts/1")
    workflow_id = exact.get("workflow_id")
    if type(workflow_id) is not int or workflow_id <= 0:
        raise Stop("Invalid immutable native workflow identifier")
    required = {
        "id": run_id, "run_attempt": 1, "head_sha": head,
        "head_branch": branch, "path": path, "event": "push", "status": "in_progress",
    }
    if (
        any(exact.get(key) != value for key, value in required.items())
        or exact["repository"]["full_name"] != REPOSITORY
        or exact["head_repository"]["full_name"] != REPOSITORY
    ):
        raise Stop("Native exact run does not own the configured branch")
    workflow = workflow_api(f"/actions/workflows/{workflow_id}")
    if workflow.get("path") != path or workflow.get("state") != "active":
        raise Stop("Native workflow is changed or disabled")
    inventory = workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100")
    rows = inventory["workflow_runs"]
    if (
        type(inventory.get("total_count")) is not int
        or inventory["total_count"] != 1 or len(rows) != 1
        or any(rows[0].get(key) != value for key, value in required.items())
        or rows[0].get("workflow_id") != workflow_id
    ):
        raise Stop("Native workflow does not have exactly one original attempt")
    jobs = workflow_api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100")
    rows = jobs["jobs"]
    if (
        type(jobs.get("total_count")) is not int or jobs["total_count"] != 1
        or len(rows) != 1 or rows[0]["status"] != "in_progress"
        or rows[0]["head_sha"] != head or rows[0]["run_id"] != run_id
        or rows[0]["run_attempt"] != 1 or rows[0]["name"] != "verify"
    ):
        raise Stop("Exact nonpublishing native job is ambiguous")
    if workflow_api("/git/ref/heads/" + branch)["object"]["sha"] != head:
        raise Stop("Native branch moved during preflight")
    if authenticated_source_hash(head) != own_source_hash():
        raise Stop("Native source differs from its immutable commit")
    for filename in (
        f"adapters/{SOURCE_ID}/test_adapter.py",
        path,
        ".github/qa/warsaw426-observer.py",
        ".github/qa/warsaw426-consumer.mjs",
        ".github/qa/warsaw426-review.pem",
    ):
        response = workflow_api("/contents/" + filename + "?ref=" + head)
        if response["path"] != filename or response["encoding"] != "base64":
            raise Stop("Native test inputs lack immutable bytes")
        raw = base64.b64decode("".join(response["content"].split()), validate=True)
        if len(raw) != response["size"] or raw != Path(filename).read_bytes():
            raise Stop("Native test inputs differ from immutable commit")
    return workflow_id, run_id, 1, head, rows[0]["id"]


def ci_qa_authority():
    """Retain the complete confirmed research boundary on an isolated QA runner."""
    binding = ci_qa_binding()
    state = validate(copy.deepcopy(CI_QA["state"]))
    if digest(canonical(state)) != CI_QA["state_sha256"]:
        raise Stop("Native source state is not its accepted exact research floor")
    if state["blocked"] or state["in_flight"] or state["physical_pending"]:
        raise Stop("Pending or blocked publisher state cannot leave research ownership")
    research = CI_QA.get("research_observations")
    if (
        type(research) is not list
        or len(research) != len(URLS)
        or any(type(item) is not dict for item in research)
        or [item.get("target") for item in research] != list(URLS)
        or [item.get("reservation") for item in research] != state["starts"]
        or any(
            not re.fullmatch("[a-f0-9]{32}", item.get("request_id", ""))
            or type(item.get("pre_opener_wall")) not in (int, float)
            or not item["reservation"] <= item["pre_opener_wall"] <= state["completed"]
            for item in research
        )
    ):
        raise Stop("The exact research timing receipts differ from the retained original state")
    expected = initialize_hosted(state)

    def verify():
        if ci_qa_binding() != binding:
            raise Stop("Original native workflow ownership changed before first wire")

    return binding, expected, verify


def collect(*, publishing=False):
    """Collect the current complete single published MSc admissions intake."""
    global HOSTED_EXPORT, NATIVE_TEST_EXPORT
    if GATE_FAILED or AUDIT["requests"]:
        raise Stop("Collection cannot reenter the original publisher authority")
    qa = not publishing and HOSTED
    if publishing:
        binding, expected, proof = hosted_authority()
    elif qa:
        binding, expected, proof = ci_qa_authority()
    else:
        raise Stop("Original live testing requires the reviewed GitHub Actions job")
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
            document = request(journal, ENTRY).decode("utf-8", "strict")
            checked_at = datetime.now(timezone.utc)
            source = AdmissionText(document)
            visible = " ".join("".join(source.fragments).split())
            if re.search(
                r"\b(?:automated\s+access|scrap(?:e|ing)|crawler|republication)\b",
                visible, re.I,
            ):
                raise Stop("Public admission page requires renewed policy review")
            records = [normalize(extract_facts(document), checked_at)]
            validate_records(records)
        except Exception as error:
            failure = error
        finally:
            state = copy.deepcopy(journal.read())
    if qa:
        NATIVE_TEST_EXPORT = (binding, state, expected, journal.capture_claim)
    elif binding and not GATE_FAILED:
        HOSTED_EXPORT = (binding, state, expected)
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
        "publisher_country": "PL",
        "publisher_type": "university",
        "attribution": "Warsaw University of Technology International Students Office",
        "status": "fail" if failed else "success",
        "last_attempt_at": checked_time(attempt),
        "last_success_at": previous.get("last_success_at") if failed else checked_time(attempt),
        "last_checked_at": previous.get("last_checked_at") if failed else checked_time(checked_at),
        "record_count": len(records),
        "message": ("Collection failed; retained the last successful dataset" if failed
                    else f"Collected and validated {len(records)} current Warsaw Tech intake records"),
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
    if HOSTED_EXPORT is None or GATE_FAILED:
        return
    binding, state, expected = HOSTED_EXPORT
    _, run_id, attempt, head = binding
    if workflow_binding() != binding:
        raise Stop("Production ownership changed before artifact export")
    with Journal(expected, lambda: None) as journal:
        if journal.ack != canonical(state):
            raise Stop("Publisher journal changed before artifact export")
        retained_claim = read_private_claim(journal.directory, "collection.claim")
        if json.loads(retained_claim)["source_sha256"] != own_source_hash():
            raise Stop("Publisher collection authority changed before artifact export")
    runner = Path(os.environ["RUNNER_TEMP"])
    target = Path(os.environ["WARSAW_PACING_ARTIFACT_PATH"])
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
        if (identity(runner.lstat()) != parent_id or identity(target.lstat()) != state_id
                or target.read_bytes() != raw):
            raise Stop("Closed original artifact changed")
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true", help="Publish this source on its owning Action")
    args = parser.parse_args()
    if args.publish and not HOSTED:
        raise Stop("Only the owning GitHub Actions workflow may publish")
    if not args.publish:
        print(json.dumps(collect(), ensure_ascii=False))
        return
    try:
        publish()
    finally:
        export_state()


if __name__ == "__main__":
    try:
        main()
    except Exception as problem:
        print(type(problem).__name__ + ": source collection or publication failed", file=sys.stderr)
        sys.exit(1)
