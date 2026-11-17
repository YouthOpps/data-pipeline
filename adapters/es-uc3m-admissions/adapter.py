"""Collect the official UC3M international undergraduate early admission route."""

import argparse
import base64
import copy
import contextlib
from datetime import date, datetime, time as clock_time, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from zoneinfo import ZoneInfo

SOURCE_ID = "es-uc3m-admissions"
SOURCE_URL = (
    "https://www.uc3m.es/bachelor-degree/admission/application/"
    "international-students/early-admission"
)
CALENDAR = SOURCE_URL + "/deadline"
CRITERIA = SOURCE_URL + "/what-is"
MADRID = ZoneInfo("Europe/Madrid")
MONTHS = {
    name: index
    for index, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"), 1)
}
DATE_PATTERN = (
    r"([1-9]|[12][0-9]|3[01])(?:st|nd|rd|th)? "
    r"(" + "|".join(MONTHS) + r") ([2][0-9]{3})"
)
WINDOW = re.compile(
    r"The application period is open from the " + DATE_PATTERN
    + r" until " + DATE_PATTERN + r"\.")


class MaterialError(ValueError):
    """The approved facts are incomplete or have changed."""


class AdmissionMaterial(HTMLParser):
    """Collect visible semantic blocks for strict source-local extraction."""

    BLOCK_TAGS = frozenset(("h1", "h2", "p", "li"))
    OMIT_TAGS = frozenset(("script", "style", "template", "nav", "footer", "aside"))
    VOID_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input",
                           "link", "meta", "param", "source", "track", "wbr"))

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.frames = []
        self.blocks = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        style = re.sub(r"\s+", "", (attrs.get("style") or "").casefold())
        hidden = (
            tag in self.OMIT_TAGS or "hidden" in attrs
            or (attrs.get("aria-hidden") or "").casefold() == "true"
            or "display:none" in style or "visibility:hidden" in style
            or (bool(self.frames) and self.frames[-1][1])
        )
        if tag in self.VOID_TAGS:
            if tag == "br" and not hidden:
                self.handle_data(" ")
            return
        block = [] if not hidden and tag in self.BLOCK_TAGS else None
        self.frames.append((tag, hidden, block))

    def handle_endtag(self, tag):
        for index in range(len(self.frames) - 1, -1, -1):
            if self.frames[index][0] != tag:
                continue
            frames = self.frames[index:]
            del self.frames[index:]
            for name, hidden, data in reversed(frames):
                if not hidden and data:
                    self.blocks.append((name, " ".join("".join(data).split())))
            break

    def handle_startendtag(self, tag, attrs):
        if tag in self.VOID_TAGS:
            self.handle_starttag(tag, attrs)
        else:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_data(self, value):
        if self.frames and self.frames[-1][1]:
            return
        for _, _, block in self.frames:
            if block is not None:
                block.append(value)


def one_heading(blocks, tag, label):
    indexes = [
        i for i, block in enumerate(blocks)
        if block == (tag, label)
    ]
    if len(indexes) != 1:
        raise MaterialError("Expected one UC3M heading: " + label)
    return indexes[0]


def section(blocks, label):
    index = one_heading(blocks, "h2", label)
    content = []
    for tag, text in blocks[index + 1:]:
        if tag in ("h1", "h2"):
            break
        content.append(text)
    return content


def application_cycle(document):
    """Return one publisher-labelled application window, with no year guess."""
    blocks = AdmissionMaterial(document).blocks
    one_heading(blocks, "h1", "When can I apply?")
    period = section(blocks, "Application period")
    windows = [m for line in period if (m := WINDOW.fullmatch(line))]
    if len(windows) != 1:
        raise MaterialError("Expected one UC3M application period")
    start_day, start_month, start_year, end_day, end_month, end_year = (
        windows[0].groups()
    )
    start = date(int(start_year), MONTHS[start_month], int(start_day))
    deadline = date(int(end_year), MONTHS[end_month], int(end_day))
    if not 1 <= (deadline - start).days < 366:
        raise MaterialError("Unreasonable UC3M application window")
    rolling = " ".join(section(blocks, "Rolling Admission")).casefold()
    if "places will be filled" not in rolling or "will not be offered" not in rolling:
        raise MaterialError("UC3M rolling availability wording changed")
    return start, deadline


def admission_record(calendar_html, eligibility_html, observed_at):
    """Map the reviewed route once, only if the paired eligibility still fits."""
    start, deadline = application_cycle(calendar_html)
    eligibility = AdmissionMaterial(eligibility_html).blocks
    one_heading(eligibility, "h1", "What is Early Admission and who can apply?")
    candidate = " ".join(section(eligibility, "To whom is directed")).casefold()
    degree = " ".join(section(eligibility, "Which Bachelor Degrees can I apply for?")).casefold()
    require_candidate = (
        "educational systems other than spanish",
        "equivalent to the spanish baccalaureate",
        "outstanding academic record",
        "proof of b2 level",
        "do not live in spain",
    )
    require_degree = ("4u alliance degrees", "international dual bachelor degrees")
    if not (all(text in candidate for text in require_candidate)
            and all(text in degree for text in require_degree)):
        raise MaterialError("UC3M current eligibility or programme exclusions changed")
    observed = tuple(digest(canonical(section(eligibility, heading))) for heading in ("To whom is directed", "Which Bachelor Degrees can I apply for?"))
    if observed != CALENDAR_ELIGIBILITY_SHA:
        raise MaterialError("UC3M eligibility and programme scope need review")
    checked = observed_at.astimezone(MADRID)
    closing = datetime.combine(deadline, clock_time(23, 59, 59), MADRID)
    status = "open" if start <= checked.date() <= deadline else "unknown"
    return {
        "id": f"{SOURCE_ID}-early-admission-{deadline.year}",
        "title": "UC3M international undergraduate early admission",
        "url": SOURCE_URL,
        "source_url": SOURCE_URL,
        "kind": "opportunity",
        "category": "other",
        "categories": ["other"],
        "language": "en",
        "publisher_country": "ES",
        "host_countries": ["ES"],
        "eligible_countries": [],
        "published_at": None,
        "deadline": closing.isoformat(),
        "status": status,
        "tags": ["university admissions", "undergraduate study", "international students"],
        "summary": (
            "Early entry to eligible UC3M bachelor’s and dual degrees for students "
            "from non-Spanish secondary education systems. Equivalent secondary "
            "qualifications, a strong academic record and B2 in the teaching "
            "language are required. Some education systems also have an overseas "
            "residence condition. The 4U Alliance and international dual degrees "
            "are excluded. Individual degrees may close ahead of the final deadline."
        ),
    }


# These gates are populated only with separately reviewed complete actual state.
NATIVE_APPROVAL = None
FIRST_HOSTED_APPROVAL = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/421#issuecomment-6961760998', 'qa_adapter_sha256': 'f9b2a6061f37d088b7f41ec1955ae05268ba6a49e6d49e10c6b27f90fcaddf06', 'qa_artifact': [14372616219, 'sha256:c64de37569c753812b99229ba3e9b29b4a91737bea7bb511bc15a37103ca0725', 928], 'qa_job_completed_at': '2026-11-17T21:32:17Z', 'qa_job_id': 134383169725, 'qa_owner': [44180481985, 1, '53dcecca7532b6d8faca5370dcfb39cbf0cb900a'], 'qa_state_sha256': '929e39967fdcad727a5eed65423b6b19a65345e177a19e708d0d7ab427194694', 'qa_workflow_id': 421895368, 'qa_workflow_sha256': '81025e865e9c19fe92e9cd0a648820bd92737bb97b67083176101770df5f8abb', 'supplementary_starts': [1794948717.610327, 1794948731.150642, 1794949024.1788306, 1794949055.6464286, 1794951091.397184, 1794951104.2192929, 1794951117.4717236, 1794951130.7271497], 'transfer_state_sha256': '8d7e5f8e3c40438b62ec1912c48e6581ece57d43850d7290286787d61db1bb79'}
CALENDAR_ELIGIBILITY_SHA = (
    '59d4215b6de9ada6f43bc199e14bceb7a240a55825b0fe0f0d783744e1f96851',
    'c4538d21a05e8d0061ef44e334df88eeb483d7e9615c59d3781a5c8a2f71965b',
)
POLICY_TEXT_SHA = '4d3fca3c2506a9422894f3967df73167b507da20b2de7de6f3eabd3270383d51'

FAMILY = "youthopps-uc3m-publisher-v1"
ROBOTS = "https://www.uc3m.es/robots.txt"
POLICY = ("https://www.uc3m.es/inicio/informacion-legal/"
          "condiciones-generales-uso-portal-uc3m")
URLS = (ROBOTS, POLICY, CALENDAR, CRITERIA)
WEBSITE_URL = "https://www.uc3m.es/"
USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
REPOSITORY = "YouthOpps/data-pipeline"
WORKFLOW_FILE = "fetch-es-uc3m-admissions.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW_FILE
SOURCE_PATH = "adapters/es-uc3m-admissions/adapter.py"
ARTIFACT_NAME = SOURCE_ID + "-pacing-state"
COLLECTION_CLAIM = "issue421-native-collection.claim"
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_API = "https://api.github.com/repos/" + REPOSITORY
PARENT = Path("/tmp")
MAX_BODY = 8 * 1024 * 1024
MAX_STATE = 32768
MAX_CLOCK = 253402300799
BODY_SECONDS = 25
CLOCK = time
GATE_FAILED = False
AUDIT = {"requests": [], "persistence": [], "errors": []}
_PERMITTED_TARGETS = set()
_NATIVE_PROOF = None
_HOSTED_OWNER = None
_COLLECTION_STARTED = False


class Stop(Exception):
    """The exact research attempt cannot proceed."""


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
        raise Stop('Unknown UC3M state shape')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise Stop('Unknown UC3M state schema')
    if value['family'] != FAMILY:
        raise Stop('Wrong UC3M family')
    numbers = ('observed_at', 'not_before', 'interval', 'page_interval',
               'completed', 'inventory_completed', 'rate_limit_until',
               'physical_started')
    for key in numbers:
        v = value[key]
        if (type(v) not in (int, float) or not math.isfinite(v) or v < 0
                or key not in ('interval', 'page_interval')
                and v > MAX_CLOCK):
            raise Stop('Unsupported UC3M number: ' + key)
    if value['interval'] < 12.2 or value['page_interval'] < 30:
        raise Stop('UC3M floor was weakened')
    for key in ('legacy_rate_history_unknown',
                'legacy_policy_history_unknown', 'blocked', 'in_flight',
                'physical_pending'):
        if type(value[key]) is not bool:
            raise Stop('Invalid UC3M flag')
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
        raise Stop('UC3M timing chronology differs')
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

    def __init__(self, expected):
        self.expected = expected
        self.handles = []
        self.directory = None
        self.fd = None
        self.ack = None
        self.ack_version = None
        self.capture_claim = None

    def verify_capture(self):
        check_collection_authority(self)
        self.verify()
        if self.capture_claim is None:
            raise Stop('No exclusive collection authority')
        raw, expected = self.capture_claim
        named = os.stat(COLLECTION_CLAIM,
                        dir_fd=self.directory, follow_symlinks=False)
        private_file(named)
        if (identity(named) != expected
                or read_private_claim(self.directory,
                                      COLLECTION_CLAIM) != raw):
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
        prior_starts = before['starts']
        if (value['physical_id'] != before['physical_id']
                and value['physical_pending'] and value['outcome'] == 'reserved'
                and value['physical_started'] >= before['observed_at']):
            retention = max([86400, *(limit['period'] for limit in value['rates'])])
            cutoff = value['physical_started'] - retention
            prior_starts = [start for start in prior_starts if start >= cutoff]
        if (any(value[k] < before[k] for k in monotonic)
                or before['blocked'] and not value['blocked']
                or not set(prior_starts) <= set(value['starts'])
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
        raise Deadline('Publisher operation deadline')

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


def robot_policy(journal, body, status, target):
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
                        urllib.parse.urlsplit(target).path)):
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
            raise Stop('Retained timing exceeds collection budget')
        CLOCK.sleep(min(delay, 1))


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


def initialize_hosted(seed, owner):
    """Restore verified owned state or the reviewed first hosted seed."""
    if owner != _HOSTED_OWNER or not owner:
        raise Stop("An authenticated hosted owner is required")
    leaf, claim = paths()
    handles = []
    primary = None
    try:
        parent = os.open(PARENT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(parent)
        parent_safe(info)
        parent_id = identity(info)
        parent_meta = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        handles.append((parent, parent_id))

        def verify_parent():
            p, named = os.fstat(parent), PARENT.lstat()
            if (identity(p) != parent_id or identity(named) != parent_id
                    or (p.st_uid, p.st_gid, stat.S_IMODE(p.st_mode))
                    != parent_meta
                    or (named.st_uid, named.st_gid,
                        stat.S_IMODE(named.st_mode)) != parent_meta):
                raise Stop('Initialization parent changed')
            parent_safe(p)

        verify_parent()
        for name in (leaf.name, claim.name):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise Stop('Existing UC3M authority cannot be reset')
        pending_raw, pending_id = create_once(parent, claim.name, {
            'stage': 'pending', 'source_sha256': source_sha(),
            'owner': owner,
            'source': SOURCE_ID})
        verify_parent()
        os.mkdir(leaf.name, 0o700, dir_fd=parent)
        directory = os.open(leaf.name, os.O_RDONLY | os.O_DIRECTORY
                            | os.O_NOFOLLOW, dir_fd=parent)
        directory_id = identity(os.fstat(directory))
        handles.append((directory, directory_id))

        def verify_directory():
            verify_parent()
            d = os.fstat(directory)
            named = os.stat(leaf.name, dir_fd=parent,
                            follow_symlinks=False)
            if (identity(d) != directory_id or identity(named) != directory_id
                    or not stat.S_ISDIR(d.st_mode)
                    or d.st_uid != os.getuid()
                    or stat.S_IMODE(d.st_mode) != 0o700
                    or (named.st_uid, named.st_gid,
                        stat.S_IMODE(named.st_mode))
                    != (d.st_uid, d.st_gid, stat.S_IMODE(d.st_mode))):
                raise Stop('Initialization directory authority changed')

        state = healthy_state(seed)
        state_raw = canonical(validate(state))
        state_id = None
        locks = {}
        state_metadata = None
        for name, raw in [('collection.lock', b''), ('state.lock', b''),
                          ('state.json', state_raw)]:
            verify_directory()
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL
                         | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            expected = identity(os.fstat(fd))
            handles.append((fd, expected))
            private_file(os.fstat(fd))
            offset = 0
            while offset < len(raw):
                count = os.write(fd, raw[offset:])
                if count <= 0:
                    raise Stop('Initialization write incomplete')
                offset += count
            os.fsync(fd)
            if os.pread(fd, len(raw) + 1, 0) != raw:
                raise Stop('Initialization readback differs')
            if name == 'state.json':
                state_id = expected
                i = os.fstat(fd)
                state_metadata = [i.st_uid, i.st_gid,
                                  stat.S_IMODE(i.st_mode), i.st_nlink]
            else:
                locks[name] = list(expected)
        os.fsync(directory)
        verify_directory()
        d = os.fstat(directory)
        result = {'schema': 1, 'family': FAMILY,
                  'source_sha256': source_sha(),
                  'owner': owner,
                  'directory_identity': list(directory_id),
                  'directory_metadata': [d.st_uid, d.st_gid,
                                         stat.S_IMODE(d.st_mode)],
                  'parent_identity': list(parent_id),
                  'parent_metadata': list(parent_meta), 'locks': locks,
                  'state_identity': list(state_id),
                  'state_metadata': state_metadata,
                  'initializer_claim_identity': list(pending_id),
                  'state_sha256': digest(state_raw),
                  'source': SOURCE_ID,
                  'publisher_requests': 0}
        fd = os.open(claim.name, os.O_RDWR | os.O_NOFOLLOW,
                     dir_fd=parent)
        expected = identity(os.fstat(fd))
        handles.append((fd, expected))
        private_file(os.fstat(fd))
        named = os.stat(claim.name, dir_fd=parent, follow_symlinks=False)
        if (expected != pending_id or identity(named) != pending_id
                or os.pread(fd, len(pending_raw) + 1, 0) != pending_raw):
            raise Stop('Original initialization claim changed')
        raw = canonical(result)
        if os.pwrite(fd, raw, 0) != len(raw):
            raise Stop('Initialization claim write incomplete')
        os.ftruncate(fd, len(raw))
        os.fsync(fd)
        if os.pread(fd, len(raw) + 1, 0) != raw:
            raise Stop('Initialization claim differs')
        os.fsync(parent)
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close(handles, primary)
    return result


def _request(journal, url):
    """Exactly one physical request; no retry or redirect path exists."""
    global GATE_FAILED
    if not isinstance(journal, Journal):
        raise Stop('Original owned journal authority required')
    journal.verify_capture()
    if (url not in URLS
            or urllib.parse.urlsplit(url).hostname != 'www.uc3m.es'):
        raise Stop('Unreviewed UC3M source URL or other origin')
    robots = url == ROBOTS
    expected = URLS[len(AUDIT['requests'])] if len(AUDIT['requests']) < len(URLS) else None
    if url != expected or not robots and url not in _PERMITTED_TARGETS:
        raise Stop('Strict collection order and current REP permission required')
    inventory = url == CALENDAR
    wait_for_request(journal, inventory)
    state = journal.read()
    state.update(logical_id=uuid.uuid4().hex,
                 physical_id=uuid.uuid4().hex, physical_started=now(),
                 target=url, in_flight=True, physical_pending=True,
                 outcome='reserved')
    # Keep all observations that can still constrain a current rate.
    retention = max([86400, *(limit['period'] for limit in state['rates'])])
    state['starts'] = [value for value in state['starts']
                       if value >= state['physical_started'] - retention]
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
                for pair in response_headers.raw_items():
                    receipt['headers'].append(pair)
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
                    policies = {target: robot_policy(journal, body, status, target)
                                for target in URLS[1:]}
                    state = journal.read()
                    state['policy'] = {
                        'url': ROBOTS, 'status': status, 'body_sha256': digest(body),
                        'unavailable': all(value[2] for value in policies.values()),
                        'new_allowed': all(value[0] for value in policies.values()),
                        'legacy_allowed': all(value[1] for value in policies.values()),
                    }
                    if not state['policy']['new_allowed'] or not state['policy']['legacy_allowed']:
                        state['blocked'] = True
                    else:
                        _PERMITTED_TARGETS.update(policies)
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
        if inventory:
            state['inventory_completed'] = max(state['inventory_completed'],
                                                stamp)
        journal.save(state)
        if status == 429:
            raise RateLimited('Assessed rate limit; cooldown retained')
        if state['blocked']:
            raise Stop('Explicit current REP refusal retained')
        return status, response_headers, body
    except BaseException as error:
        if not isinstance(error, RateLimited):
            GATE_FAILED = True
        receipt['error'] = type(error).__name__
        raise


class AdapterError(Exception):

    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit((
            urllib.parse.urlsplit(match[0]).scheme,
            urllib.parse.urlsplit(match[0]).hostname or "",
            urllib.parse.urlsplit(match[0]).path,
            "",
            "",
        )),
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return " ".join(text.split())[:500]


def github(method, path, payload=None, missing_ok=False):
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError("DATA_SOURCE_TOKEN is required for publication",
                           "publish")
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
            payload=(json.dumps(payload).encode()
                     if payload is not None else None),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; publication is unconfirmed",
            "publish") from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication API returned HTTP {status}",
                           "publish", status)
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
        if file is None:
            files[name] = None
            continue
        expected = file.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", expected):
            raise AdapterError("Invalid existing source blob identity",
                               "publish")
        if file.get("encoding") != "base64":
            file = github("GET", "/git/blobs/" + expected)
        if file.get("encoding") != "base64" or file.get("sha") != expected:
            raise AdapterError("Unsupported existing source blob", "publish")
        try:
            raw = base64.b64decode(re.sub(r"\s+", "", file["content"]),
                                   validate=True)
            actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" +
                                  raw).hexdigest()
            if actual != expected:
                raise ValueError("Source blob digest mismatch")
            files[name] = raw.decode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise AdapterError("Invalid existing source blob bytes",
                               "publish") from None
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish")
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
                    "base_tree":
                        snapshot["tree"],
                    "tree": [{
                        "path": f"datas/{SOURCE_ID}/{name}",
                        "mode": "100644",
                        "type": "blob",
                        "content": text,
                    } for name, text in files.items()],
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
                {
                    "sha": candidate,
                    "force": False
                },
            )
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (latest["head"] == candidate or
                              latest["files"] == desired):
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


OPENER = urllib.request.build_opener(NoRedirect())


def paths():
    leaf = PARENT / f"{FAMILY}-{os.getuid()}"
    return leaf, leaf.with_name(leaf.name + "-issue421-initialized.claim")


def machine():
    return digest(Path("/etc/machine-id").read_bytes())


def source_sha():
    return digest(Path(__file__).read_bytes())


def require(condition, message):
    if not condition:
        raise Stop(message)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_hosted():
    return os.environ.get("GITHUB_ACTIONS") == "true"


def healthy_state(state):
    validate(state)
    require(not (state["blocked"] or state["physical_pending"] or state["in_flight"]),
            "Retained UC3M refusal or pending request")
    return state


def read_lineage_row(path, expected):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    primary = None
    old = os.fstat(fd)
    try:
        private_file(old)
        named = path.lstat()
        require(identity(old) == identity(named) == tuple(expected["identity"]),
                "Original UC3M lineage identity changed")
        require([old.st_uid, old.st_gid, stat.S_IMODE(old.st_mode)] == expected["metadata"],
                "Original UC3M lineage metadata changed")
        raw = os.pread(fd, MAX_STATE + 1, 0)
        require(version(old) == version(os.fstat(fd)) == version(path.lstat())
                and len(raw) == old.st_size and digest(raw) == expected["sha256"],
                "Original UC3M lineage bytes changed")
        return raw
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close([(fd, identity(old))], primary)


def reviewed_native_context():
    approval = NATIVE_APPROVAL
    require(type(approval) is dict and not is_hosted() and __debug__
            and "DATA_SOURCE_TOKEN" not in os.environ,
            "Reviewed local collection is OFF")
    require(machine() == approval["machine"] and
            re.fullmatch(r"https://github\.com/YouthOpps/data-pipeline/issues/421#issuecomment-[1-9][0-9]*",
                         approval["evidence"]),
            "Reviewed local UC3M authority differs")
    require(os.environ.get("UC3M421_APPROVED_NATIVE_SHA256") == source_sha(),
            "Original native source binding missing")
    proof_raw = read_lineage_row(approval["initialization"], approval["inputs"][approval["initialization"]])
    proof = json.loads(proof_raw)
    require(type(proof["initializer_exit"]) is int and proof["initializer_exit"] == 0
            and not proof["initializer_stderr"],
            "Original initializer outcome was not a success")
    for path, row in approval["inputs"].items():
        read_lineage_row(path, row)
    status, _, _ = infrastructure_request_bytes(
        _WORKFLOW_API + "/actions/workflows/" + WORKFLOW_FILE,
        headers=github_headers("GITHUB_TOKEN"))
    require(status == 404, "Unexpected hosted workflow authority exists")
    return proof["result"]


def check_collection_authority(journal):
    if is_hosted():
        require(_HOSTED_OWNER is not None, "Original hosted authority is missing")
    else:
        require(NATIVE_APPROVAL is not None and _NATIVE_PROOF == journal.expected
                and os.environ.get("UC3M421_APPROVED_NATIVE_SHA256") == source_sha(),
                "Original local authority is missing")
        for path, row in NATIVE_APPROVAL["inputs"].items():
            read_lineage_row(path, row)
    journal.verify()


def github_headers(token_name):
    token = os.environ.get(token_name, "")
    require(token and "\r" not in token and "\n" not in token,
            "Required GitHub authority is unavailable")
    return {"Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10"}


def infrastructure_request_bytes(url, *, method="GET", headers=None, payload=None,
                                 byte_limit=6_000_000, storage=False):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    require(parsed.scheme == "https" and not parsed.username and not parsed.password
            and not parsed.port and not parsed.fragment, "Invalid infrastructure URL")
    if storage:
        require(method == "GET" and not headers and payload is None and (
            host.endswith(".blob.core.windows.net")
            or host.endswith(".actions.githubusercontent.com")), "Invalid signed artifact origin")
    else:
        require(host == "api.github.com", "Invalid API origin")
    request = urllib.request.Request(
        url, method=method, data=payload, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        response = OPENER.open(request, timeout=20)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read(byte_limit + 1)
        require(len(content) <= byte_limit, "Infrastructure response exceeds byte bound")
        return response.status, response.headers, content


request_bytes = infrastructure_request_bytes


def workflow_api(path):
    status, _, body = infrastructure_request_bytes(
        _WORKFLOW_API + path, headers=github_headers("GITHUB_TOKEN"))
    require(status == 200, "GitHub workflow metadata unavailable")
    payload = json.loads(body)
    require(type(payload) is dict, "Unexpected GitHub workflow response")
    return payload


def immutable_file(head, path):
    require(re.fullmatch("[a-f0-9]{40}", head or ""), "Invalid immutable revision")
    item = workflow_api(f"/contents/{path}?ref={head}")
    require(item.get("type") == "file" and item.get("path") == path and
            item.get("encoding") == "base64" and type(item.get("size")) is int,
            "Incomplete immutable source file identity")
    raw = base64.b64decode("".join(item["content"].split()), validate=True)
    require(len(raw) == item["size"], "Incomplete immutable source bytes")
    return raw


def run_identity(run, workflow_id, path=WORKFLOW_PATH, branch="main"):
    require(run.get("workflow_id") == workflow_id and run.get("path") == path
            and run.get("head_branch") == branch
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and type(run.get("id")) is int and run["id"] > 0
            and type(run.get("run_attempt")) is int and 1 <= run["run_attempt"] <= 100
            and re.fullmatch("[a-f0-9]{40}", run.get("head_sha", "")),
            "Untrusted source workflow attempt")
    return run["id"], run["run_attempt"], run["head_sha"]


def exact_job(owner):
    run, attempt, head = owner
    info = workflow_api(f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100")
    require(info.get("total_count") == 1 and len(info.get("jobs", ())) == 1,
            "Unexpected exact run job cardinality")
    job = info["jobs"][0]
    require(job.get("run_id") == run and job.get("run_attempt") == attempt
            and job.get("head_sha") == head and job.get("name") == "collect"
            and type(job.get("id")) is int and job["id"] > 0,
            "Exact UC3M job ownership differs")
    return job


def epoch(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Invalid exact job timestamp")
    return parsed.timestamp()


def authenticated_history():
    require(is_hosted() and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_REF") == "refs/heads/main", "Noncanonical hosted source")
    current = (int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]),
               os.environ["GITHUB_SHA"])
    workflow = workflow_api("/actions/workflows/" + WORKFLOW_FILE)
    workflow_id = workflow.get("id")
    require(type(workflow_id) is int and workflow_id > 0 and workflow.get("path") == WORKFLOW_PATH
            and workflow.get("state") == "active", "Canonical source workflow is unavailable")
    require(immutable_file(current[2], SOURCE_PATH) == Path(__file__).read_bytes()
            and immutable_file(current[2], WORKFLOW_PATH)
            == (Path(__file__).resolve().parents[2] / WORKFLOW_PATH).read_bytes(),
            "Executable source or workflow differs from immutable inputs")
    ordered, seen, total, found = [], set(), None, False
    for page in range(1, 101):
        response = workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100&page={page}")
        runs, count = response.get("workflow_runs"), response.get("total_count")
        require(type(runs) is list and len(runs) <= 100 and type(count) is int
                and count > 0 and (total is None or total == count), "Incomplete run history")
        total = count
        for row in runs:
            run, last_attempt, _ = run_identity(row, workflow_id)
            require(run not in seen, "Duplicate run history")
            seen.add(run)
            for attempt in range(1, last_attempt + 1):
                actual = workflow_api(f"/actions/runs/{run}/attempts/{attempt}")
                owner = run_identity(actual, workflow_id)
                require(owner[:2] == (run, attempt), "Exact attempt identity changed")
                job = exact_job(owner)
                if owner == current:
                    require(attempt == last_attempt and actual["status"] == "in_progress"
                            and job["status"] == "in_progress", "Current attempt is not executing")
                    found = True
                    continue
                require(actual["status"] == job["status"] == "completed",
                        "Another source attempt is in progress")
                starts, ends = epoch(job["started_at"]), epoch(job["completed_at"])
                require(0 < starts <= ends <= time.time() + 1, "Untrusted exact job chronology")
                ordered.append((ends, owner))
        if len(seen) == total:
            break
        require(len(seen) < total and len(runs) == 100, "Truncated source history")
    require(found and len(seen) == total and
            len({ended for ended, _ in ordered}) == len(ordered),
            "Missing current source attempt or ambiguous source history")
    ordered.sort(reverse=True)
    return current, [owner for _, owner in ordered]


def owned_state(owner, expected_artifact=None, expected_adapter=None):
    run, attempt, head = owner
    result = workflow_api(f"/actions/runs/{run}/artifacts?per_page=100")
    all_rows = result.get("artifacts")
    require(type(all_rows) is list and result.get("total_count") == len(all_rows)
            and len(all_rows) <= 100, "Incomplete artifact inventory")
    rows = [item for item in all_rows if item.get("name") == f"{ARTIFACT_NAME}-{attempt}"]
    require(len(rows) == 1, "Newest attempt lacks exact owned pacing state")
    info = rows[0]
    require(info.get("expired") is False and info["workflow_run"]["id"] == run
            and info["workflow_run"]["head_sha"] == head and type(info["id"]) is int
            and 0 < info["size_in_bytes"] <= MAX_STATE * 2
            and re.fullmatch("sha256:[0-9a-f]{64}", info.get("digest", "")),
            "Newest attempt state artifact ownership differs")
    if expected_artifact is not None:
        require((info["id"], info["digest"], info["size_in_bytes"]) == expected_artifact,
                "Reviewed QA archive identity differs")
    status, headers, _ = infrastructure_request_bytes(
        _WORKFLOW_API + f"/actions/artifacts/{info['id']}/zip",
        headers=github_headers("GITHUB_TOKEN"), byte_limit=16_384)
    locations = headers.get_all("Location", [])
    require(status == 302 and len(locations) == 1, "Invalid exact owned state redirect")
    status, _, raw = infrastructure_request_bytes(
        locations[0], storage=True, byte_limit=MAX_STATE * 2)
    require(status == 200 and len(raw) == info["size_in_bytes"]
            and digest(raw) == info["digest"][7:], "Owned archive bytes differ")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == 1 and entries[0].filename == "pacing-state.json"
                and not entries[0].flag_bits & 1 and not stat.S_ISLNK(entries[0].external_attr >> 16)
                and 0 < entries[0].file_size <= MAX_STATE * 2, "Unexpected owned archive Content")
        envelope = json.loads(archive.read(entries[0]))
    adapter_sha = digest(immutable_file(head, SOURCE_PATH))
    require(expected_adapter is None or adapter_sha == expected_adapter,
            "Reviewed QA native source generation differs")
    expected = artifact_envelope(owner, envelope["state"], adapter_sha)
    require(expected == envelope, "Owned state payload identity differs")
    return healthy_state(envelope["state"])


def hosted_seed(history):
    if history:
        return owned_state(history[0])
    approval = FIRST_HOSTED_APPROVAL
    require(type(approval) is dict and re.fullmatch(
        r"https://github\.com/YouthOpps/data-pipeline/issues/421#issuecomment-[1-9][0-9]*",
        approval["evidence"]), "First hosted native handoff is OFF")
    run, attempt, head = owner = tuple(approval["qa_owner"])
    workflow_id = approval["qa_workflow_id"]
    branch = "issue/421-uc3m-hosted-qa"
    path = ".github/workflows/uc3m-421-qa.yml"
    actual = workflow_api(f"/actions/runs/{run}/attempts/{attempt}")
    require(run_identity(actual, workflow_id, path, branch) == owner
            and actual.get("event") == "push" and actual.get("status") == "completed"
            and actual.get("conclusion") == "success",
            "Reviewed exact QA attempt was not a terminal success")
    workflow = workflow_api(f"/actions/workflows/{workflow_id}")
    require(workflow.get("id") == workflow_id and workflow.get("path") == path
            and workflow.get("state") == "disabled_manually"
            and digest(immutable_file(head, path)) == approval["qa_workflow_sha256"],
            "Reviewed exact QA workflow differs")
    retained = workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100")
    rows = retained.get("workflow_runs")
    require(type(rows) is list and retained.get("total_count") == len(rows) == 1
            and run_identity(rows[0], workflow_id, path, branch) == owner,
            "Reviewed QA one-use history changed")
    job = exact_job(owner)
    require(job.get("id") == approval["qa_job_id"] and job.get("status") == "completed"
            and job.get("conclusion") == "success"
            and job.get("completed_at") == approval["qa_job_completed_at"]
            and 0 < epoch(job["started_at"]) <= epoch(job["completed_at"]) <= time.time() + 1,
            "Reviewed exact QA job did not complete")
    state = owned_state(owner, tuple(approval["qa_artifact"]), approval["qa_adapter_sha256"])
    require(digest(canonical(state)) == approval["qa_state_sha256"],
            "Reviewed exact QA original state differs")
    transfer = copy.deepcopy(state)
    transfer["starts"] = sorted(set(state["starts"]) | set(approval["supplementary_starts"]))
    require(digest(canonical(transfer)) == approval["transfer_state_sha256"],
            "Reviewed QA transfer state differs")
    return healthy_state(transfer)


def artifact_envelope(owner, state, adapter_sha):
    run, attempt, head = owner
    return {"schema": 1, "repository": REPOSITORY, "run_id": run,
            "run_attempt": attempt, "source": SOURCE_ID, "head_sha": head,
            "adapter_sha256": adapter_sha, "state_sha256": digest(canonical(state)),
            "state": validate(state)}


def safe_output_signal(path, ready):
    named_parent = path.parent
    parent = os.open(named_parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    pid = identity(os.fstat(parent))
    handles = [(parent, pid)]
    primary = None
    try:
        pinfo = os.fstat(parent)
        require(identity(named_parent.lstat()) == pid and pinfo.st_uid == os.getuid()
                and not stat.S_IMODE(pinfo.st_mode) & 0o022,
                "GitHub command directory identity changed")
        fd = os.open(path.name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent)
        info = os.fstat(fd)
        fid = identity(info)
        handles.append((fd, fid))
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and not stat.S_IMODE(info.st_mode) & 0o022
                and fid == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                "Unsafe original GitHub output")
        value = b"pacing_ready=true\n" if ready else b"pacing_ready=false\n"
        require(os.write(fd, value) == len(value), "Short GitHub signal write")
        os.fsync(fd)
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close(handles, primary)


def invalidate_export(path, parent_id, file_id):
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    held = identity(os.fstat(parent))
    handles = [(parent, held)]
    primary = None
    try:
        require(held == parent_id == identity(path.parent.lstat()),
                "Replacement artifact directory retained")
        fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(fd)
        fid = identity(info)
        handles.append((fd, fid))
        private_file(info)
        require(fid == file_id == identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)),
                "Replacement artifact retained")
        os.ftruncate(fd, 0)
        os.fsync(fd)
        os.fsync(parent)
    except BaseException as error:
        primary = error
        raise
    finally:
        checked_close(handles, primary)


def write_export(owner, state):
    path = Path(os.environ["UC3M_PACING_ARTIFACT_PATH"])
    parent = Path(os.environ["RUNNER_TEMP"])
    require(path.is_absolute() and path == parent / "pacing-state.json"
            and parent == parent.resolve(), "Unexpected UC3M artifact path")
    output = Path(os.environ["GITHUB_OUTPUT"])
    require(output.is_absolute() and output.parent == parent / "_runner_file_commands"
            and output.parent == output.parent.resolve() and output.name.startswith("set_output_"),
            "Unexpected GitHub output namespace")
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    pinfo = os.fstat(parent_fd)
    parent_id = identity(pinfo)
    handles = [(parent_fd, parent_id)]
    fid = None
    primary = None
    try:
        try:
            require(parent_id == identity(parent.lstat()) and pinfo.st_uid == os.getuid()
                    and not stat.S_IMODE(pinfo.st_mode) & 0o022, "Artifact parent changed")
            raw = canonical(artifact_envelope(owner, state, source_sha()))
            fd = os.open(path.name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                         0o600, dir_fd=parent_fd)
            info = os.fstat(fd)
            fid = identity(info)
            handles.append((fd, fid))
            private_file(info)
            require(os.write(fd, raw) == len(raw), "Short artifact write")
            os.fsync(fd)
            os.fsync(parent_fd)
            require(os.pread(fd, len(raw) + 1, 0) == raw and fid == identity(path.lstat())
                    and parent_id == identity(parent.lstat()), "Artifact bytes or identity changed")
        except BaseException as error:
            primary = error
            raise
        finally:
            checked_close(handles, primary)
        safe_output_signal(output, True)
    except BaseException:
        if fid is not None:
            try:
                invalidate_export(path, parent_id, fid)
            finally:
                safe_output_signal(output, False)
        raise


def policy_text(body, headers):
    charset = headers.get_content_charset() or "utf-8"
    parser = Visible()
    parser.feed(body.decode(charset, "strict"))
    parser.close()
    return " ".join(" ".join(parser.text).split())


def source_bodies(journal):
    _request(journal, ROBOTS)
    _, policy_headers, policy_body = _request(journal, POLICY)
    require(POLICY_TEXT_SHA and digest(policy_text(policy_body, policy_headers).encode())
            == POLICY_TEXT_SHA, "UC3M publisher terms changed")
    _, calendar_headers, calendar = _request(journal, CALENDAR)
    _, criteria_headers, criteria = _request(journal, CRITERIA)
    return (calendar.decode(calendar_headers.get_content_charset() or "utf-8", "strict"),
            criteria.decode(criteria_headers.get_content_charset() or "utf-8", "strict"))


def collect():
    """Perform one complete source collection while retaining publisher state."""
    global _COLLECTION_STARTED, _HOSTED_OWNER, _NATIVE_PROOF
    require(not _COLLECTION_STARTED and not GATE_FAILED, "UC3M collection already consumed")
    _COLLECTION_STARTED = True
    if is_hosted():
        _HOSTED_OWNER, history = authenticated_history()
        source_state = hosted_seed(history)
        source_state["not_before"] = max(source_state["not_before"], now() + 60)
        source_state["observed_at"] = max(source_state["observed_at"], now())
        authority = initialize_hosted(source_state, _HOSTED_OWNER)
    else:
        authority = _NATIVE_PROOF = reviewed_native_context()
    journal = Journal(authority)
    sealed, failure = None, None
    try:
        with journal:
            state = healthy_state(journal.read())
            if not is_hosted():
                require(digest(canonical(state)) == NATIVE_APPROVAL["state_sha256"],
                        "Original reviewed UC3M native journal changed")
            journal.capture_claim = create_once(
                journal.directory, COLLECTION_CLAIM,
                {"source": SOURCE_ID, "source_sha256": source_sha(),
                 "initial_state_sha256": digest(canonical(state)), "owner": _HOSTED_OWNER})
            try:
                calendar, criteria = source_bodies(journal)
                record = admission_record(calendar, criteria, datetime.now(timezone.utc))
                stamp = utc_now()
                record.update(source=SOURCE_ID, location="Madrid, Spain", created_at=stamp,
                              updated_at=stamp, first_seen_at=stamp, last_seen_at=stamp,
                              last_checked_at=stamp)
                records = [record]
                validate_records(records)
            except Exception as error:
                failure = error
            finally:
                # Capture the valid exact receipt/pending state before closure.
                # No output is offered until all journal finalizers succeed.
                sealed = journal.read()
    except BaseException:
        sealed = None
        raise
    if is_hosted():
        write_export(_HOSTED_OWNER, sealed)
    if failure is not None:
        raise failure

    return records


def validate_records(records, *, allow_empty=False):
    require(type(records) is list and (allow_empty or len(records) == 1)
            and len(records) <= 1, "Incomplete UC3M route inventory")
    for row in records:
        require(type(row) is dict and re.fullmatch(
            r"es-uc3m-admissions-early-admission-20[0-9]{2}", row.get("id", ""))
            and row.get("source") == SOURCE_ID and row.get("source_url") == SOURCE_URL
            and row.get("url") == SOURCE_URL and row.get("kind") == "opportunity"
            and row.get("category") == "other" and row.get("categories") == ["other"]
            and row.get("host_countries") == ["ES"] and row.get("publisher_country") == "ES"
            and row.get("eligible_countries") == [] and row.get("status") in ("open", "unknown")
            and isinstance(row.get("title"), str) and row["title"].strip()
            and isinstance(row.get("summary"), str) and 0 < len(row["summary"]) <= 600,
            "Invalid UC3M factual record")
        deadline = datetime.fromisoformat(row["deadline"])
        require(deadline.tzinfo is not None
                and str(deadline.year) == row["id"].rsplit("-", 1)[-1],
                "Invalid UC3M cutoff or cycle identity")


def remove_expired_records(records, checked):
    return [row for row in records if epoch(row["deadline"]) >= epoch(checked)]


def metadata(attempt, previous, records, error=None):
    value = {"source": SOURCE_ID, "source_url": SOURCE_URL, "website_url": WEBSITE_URL,
             "publisher_country": "ES", "publisher_type": "university", "language": "en",
             "display_name": "Universidad Carlos III de Madrid", "status": "success" if error is None else "fail",
             "message": ("Validated official early admission refresh" if error is None
                         else "Collection failed; last-good data preserved"),
             "last_attempt_at": attempt, "last_success_at": (
                 attempt if error is None else previous.get("last_success_at")),
             "last_checked_at": (attempt if error is None else previous.get("last_checked_at")),
             "record_count": len(records), "error": safe_error(error) if error else None}
    if error:
        value["failure_stage"] = getattr(error, "stage", "access")
    return value


def run_publication():
    require(is_hosted() and os.environ.get("DATA_SOURCE_TOKEN"), "Hosted publication only")
    attempt = utc_now()
    failure = None
    try:
        records = collect()
    except (Stop, MaterialError, AdapterError, OSError, ValueError) as error:
        if _HOSTED_OWNER is None:
            raise
        failure = error
    snapshot = read_snapshot()
    data = snapshot["files"]["data.json"]
    previous = json.loads(data) if data is not None else []
    old = json.loads(snapshot["files"]["metadata.json"] or "{}")
    validate_records(previous, allow_empty=True)

    try:
        if failure is not None:
            raise failure
        checked = utc_now()
        records = remove_expired_records(records, checked)
        prior = {row["id"]: row for row in previous}
        for row in records:
            if row["id"] in prior:
                for key in ("created_at", "first_seen_at"):
                    row[key] = prior[row["id"]][key]
        validate_records(records, allow_empty=True)
        payload = {"data.json": json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                   "metadata.json": json.dumps(metadata(attempt, old, records),
                                               ensure_ascii=False, indent=2) + "\n"}
        publish_files(snapshot, payload)
    except (Stop, MaterialError, AdapterError, OSError, ValueError) as error:
        if data is not None:
            outcome = metadata(attempt, old, previous, error)
            publish_files(snapshot, {"metadata.json": json.dumps(outcome, indent=2) + "\n"})
        raise


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--publish", action="store_true")
    args = cli.parse_args()
    if args.publish:
        run_publication()
    else:
        print(json.dumps(collect(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        result = main()
    except (Stop, MaterialError, AdapterError, OSError, ValueError) as error:
        print(safe_error(error), file=sys.stderr)
        result = 1
    sys.exit(result)
