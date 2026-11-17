"""Collect reviewed ANU direct international degree admissions."""

import argparse
import base64
import copy
import contextlib
from datetime import date, datetime, time as clock_time, timedelta, timezone
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
        self.visible = []
        self.links = []
        self.robots_directives = []
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "meta" and (attrs.get("name") or "").casefold() in {"robots", "youthoppsbot"}:
            self.robots_directives.append(attrs.get("content") or "")
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
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
        if value.strip():
            self.visible.append(" ".join(value.split()))
        for _, _, block in self.frames:
            if block is not None:
                block.append(value)


def one_heading(blocks, tag, label):
    indexes = [
        i for i, block in enumerate(blocks)
        if block == (tag, label)
    ]
    if len(indexes) != 1:
        raise AdapterError("Expected one ANU heading: " + label)
    return indexes[0]


SOURCE_ID = "au-anu-international-admissions"
SOURCE_URL = "https://study.anu.edu.au/apply/international-applications"
REVIEWED_INTAKE_YEAR = 2027
LATEST_DATE_OFFSET = timezone(timedelta(hours=-12))
MONTHS = {
    name: index
    for index, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"), 1)
}
APPLICATION_CLOSE = re.compile(
    r"Applications are now open and will close on the "
    r"([1-9]|[12][0-9]|3[01]) (" + "|".join(MONTHS) + r") (20[0-9]{2})\."
)
ELIGIBILITY = (
    ("p", "You are an international applicant if you are:"),
    ("li", "Not an Australian or New Zealand Citizen"),
    ("li", "Not holding an Australian permanent resident or permanent humanitarian visa"),
    ("li", "Australian Year 12 inside or outside of Australia"),
    ("li", "An International Baccalaureate Diploma OR"),
    ("li", "A New Zealand National Certificate of Educational Achievement (NCEA)"),
)
ROUTE_WORDING = (
    "You apply via Universities Admissions Centre (UAC) International "
    "if you are an International student who is undertaking:"
)
SELECTION_CAVEAT = "Application deadline for programs with additional selection criteria may vary."


def cutoff_time(value):
    return datetime.combine(date.fromisoformat(value), clock_time.max, LATEST_DATE_OFFSET)


def admission_records(document, observed_at):
    """Extract both reviewed direct-application panels with their own deadlines."""
    parser = AdmissionMaterial(document)
    blocks = parser.blocks
    require(len([text for tag, text in blocks if tag == "h1"]) == 1
            and ("h1", "International undergraduate & postgraduate") in blocks,
            "ANU admissions page identity changed")
    index = one_heading(blocks, "p", ELIGIBILITY[0][1])
    require(tuple(blocks[index:index + len(ELIGIBILITY)]) == ELIGIBILITY
            and ROUTE_WORDING in " ".join(parser.visible).strip(),
            "ANU international/UAC eligibility scope changed")
    require(not any(re.search(r"\b(?:noindex|none)\b", value, re.I)
                    for value in parser.robots_directives), "ANU page indexing directive changed")
    require(not any(re.search(r"terms[-_/ ]of[-_/ ]use|acceptable[-_/ ]use|usage[-_/ ]policy",
                              url, re.I) for url in parser.links),
            "ANU admissions has new linked terms; review applicability")
    require(not re.search(
        r"(?:scrap(?:ing|e)|crawl(?:ing|er)|automated (?:access|collection)|data extraction)"
        r".{0,90}(?:prohibit|forbid|not allow|not permit|permission|required)",
        " ".join(parser.visible), re.I),
        "ANU admissions has a new possible automation restriction")
    records = []
    for semester in (1, 2):
        label = f"Semester {semester}, {REVIEWED_INTAKE_YEAR}"
        index = one_heading(blocks, "p", label)
        panel = blocks[index + 1:index + 5]
        require(len(panel) == 4 and panel[0] == ("h2", "Key dates - overview")
                and all(tag == "li" for tag, _ in panel[1:])
                and panel[2][1] == SELECTION_CAVEAT
                and panel[3][1].startswith(f"The acceptance deadline for Semester {semester} "),
                "ANU direct application calendar changed")
        match = APPLICATION_CLOSE.fullmatch(panel[1][1])
        require(match is not None, "ANU direct application closing date is missing")
        day, month, year = match.groups()
        deadline = date(int(year), MONTHS[month], int(day))
        require(deadline.year == REVIEWED_INTAKE_YEAR - (semester == 1)
                and deadline.month == (12 if semester == 1 else 5),
                "ANU semester/date relationship changed")
        uid = f"{SOURCE_ID}-{REVIEWED_INTAKE_YEAR}-semester-{semester}"
        records.append({
            "id": uid,
            "title": f"ANU direct international admission – Semester {semester}, {REVIEWED_INTAKE_YEAR}",
            "source": SOURCE_ID,
            "source_url": SOURCE_URL,
            "url": SOURCE_URL,
            "kind": "opportunity",
            "category": "other",
            "categories": ["other"],
            "language": "en",
            "publisher_country": "AU",
            "host_countries": ["AU"],
            "eligible_countries": [],
            "location": "Canberra, Australia",
            "published_at": None,
            "deadline": deadline.isoformat(),
            "status": "open" if observed_at <= cutoff_time(deadline.isoformat()) else "unknown",
            "tags": ["university admissions", "international students"],
            "summary": (
                "ANU direct applications for international undergraduate and postgraduate "
                "admission. Australian and New Zealand citizens, Australian permanent "
                "residents and permanent humanitarian visa holders are outside this route. "
                "Applicants taking Australian Year 12, the IB Diploma or New Zealand NCEA "
                "are directed to UAC. Courses with additional selection criteria may set "
                "different application deadlines. Offer acceptance is a separate step."
            ),
        })
    return records


FIRST_HOSTED_APPROVAL = None

# Temporary branch-only QA authority. These bindings never enter the product branch.
QA_HOSTED_APPROVAL = {'evidence': 'https://github.com/YouthOpps/data-pipeline/issues/423#issuecomment-6962197169', 'original_state_sha256': 'bc4b9a87c67d6707e47a239a0661a08ebfe572cbe1aaaacc6bd1475be8ed75ac', 'transfer_state_sha256': '88df8e11fd95667844e2a598ba64845b78c2b9175c2e660571792e4ef02f98f0'}
_QA_JOB_ID = None
QA_BRANCH = 'issue/423-anu-hosted-qa'
QA_PATH = '.github/workflows/anu-423-qa.yml'
QA_OBSERVER_PATH = ".github/anu-423-qa-observer.py"
QA_CERT_PATH = ".github/anu-423-review.pem"
QA_CERT_SHA256 = 'cb269335b8ee7254cee967a1cedeb4fb87d9077b87f9bd76b0b2142e843c4f6b'
QA_ORIGINAL_STATE_SHA256 = 'bc4b9a87c67d6707e47a239a0661a08ebfe572cbe1aaaacc6bd1475be8ed75ac'
QA_TRANSFER_STATE_SHA256 = '88df8e11fd95667844e2a598ba64845b78c2b9175c2e660571792e4ef02f98f0'
QA_ORIGINAL_STATE = {'blocked': False, 'completed': 1794951458.9994469, 'completed_logical_id': '813275cd21c64f1193e7ecf93150ad44', 'completed_physical_id': '8554d330b1794d2aaa066661ffab1b2e', 'family': 'youthopps-anu-publisher-v1', 'in_flight': False, 'interval': 12.2, 'inventory_completed': 1794951458.9994469, 'legacy_policy_history_unknown': True, 'legacy_rate_history_unknown': True, 'logical_id': '813275cd21c64f1193e7ecf93150ad44', 'not_before': 1794951471.199447, 'observed_at': 1794951458.9994469, 'outcome': 'response_assessed', 'page_interval': 30, 'physical_id': '8554d330b1794d2aaa066661ffab1b2e', 'physical_pending': False, 'physical_started': 1794951455.6009042, 'policy': {'body_sha256': '0007b00b3f4d9afac3dd764bdd5159b3d1532c8b326fb5c280c1948c90df5b46', 'legacy_allowed': True, 'new_allowed': True, 'status': 200, 'unavailable': False, 'url': 'https://study.anu.edu.au/robots.txt'}, 'rate_limit_strikes': 0, 'rate_limit_until': 0, 'rates': [], 'schema': 1, 'starts': [1794951440.7626348, 1794951455.6009042], 'target': 'https://study.anu.edu.au/apply/international-applications'}
QA_PRIOR_OPENER_TIMES = (1794951441.8875847, 1794951456.6981342)
FAMILY = "youthopps-anu-publisher-v1"
ROBOTS = "https://study.anu.edu.au/robots.txt"
URLS = (ROBOTS, SOURCE_URL)
WEBSITE_URL = "https://www.anu.edu.au/"
USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
REPOSITORY = "YouthOpps/data-pipeline"
WORKFLOW_FILE = "fetch-au-anu-international-admissions.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW_FILE
SOURCE_PATH = "adapters/au-anu-international-admissions/adapter.py"
ARTIFACT_NAME = SOURCE_ID + "-pacing-state"
COLLECTION_CLAIM = "issue423-native-collection.claim"
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
        raise Stop('Unknown ANU state shape')
    if type(value['schema']) is not int or value['schema'] != 1:
        raise Stop('Unknown ANU state schema')
    if value['family'] != FAMILY:
        raise Stop('Wrong ANU family')
    numbers = ('observed_at', 'not_before', 'interval', 'page_interval',
               'completed', 'inventory_completed', 'rate_limit_until',
               'physical_started')
    for key in numbers:
        v = value[key]
        if (type(v) not in (int, float) or not math.isfinite(v) or v < 0
                or key not in ('interval', 'page_interval')
                and v > MAX_CLOCK):
            raise Stop('Unsupported ANU number: ' + key)
    if value['interval'] < 12.2 or value['page_interval'] < 30:
        raise Stop('ANU floor was weakened')
    for key in ('legacy_rate_history_unknown',
                'legacy_policy_history_unknown', 'blocked', 'in_flight',
                'physical_pending'):
        if type(value[key]) is not bool:
            raise Stop('Invalid ANU flag')
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
        raise Stop('ANU timing chronology differs')
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
                raise Stop('Existing ANU authority cannot be reset')
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
            or urllib.parse.urlsplit(url).hostname != 'study.anu.edu.au'):
        raise Stop('Unreviewed ANU source URL or other origin')
    robots = url == ROBOTS
    expected = URLS[len(AUDIT['requests'])] if len(AUDIT['requests']) < len(URLS) else None
    if url != expected or not robots and url not in _PERMITTED_TARGETS:
        raise Stop('Strict collection order and current REP permission required')
    inventory = url == SOURCE_URL
    wait_for_request(journal, inventory)
    if not AUDIT['requests'] and _QA_JOB_ID is not None:
        current, _, job_id = authenticated_qa_owner()
        require((current, job_id) == (_HOSTED_OWNER, _QA_JOB_ID),
                "Original QA owner changed during startup quiet")
        journal.verify_capture()
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
    return leaf, leaf.with_name(leaf.name + "-issue423-initialized.claim")


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
            "Retained ANU refusal or pending request")
    return state


def check_collection_authority(journal):
    require(is_hosted() and _HOSTED_OWNER is not None,
            "Original hosted authority is missing")
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


def run_identity(run, workflow_id, path=WORKFLOW_PATH, branch="main", repository=REPOSITORY):
    require(run.get("workflow_id") == workflow_id and run.get("path") == path
            and run.get("head_branch") == branch
            and run.get("repository", {}).get("full_name") == repository
            and run.get("head_repository", {}).get("full_name") == repository
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
            "Exact ANU job ownership differs")
    return job


def epoch(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "Invalid exact job timestamp")
    return parsed.timestamp()



QA_RESEARCH_REPOSITORY = "YouthOpps/ai-workspace"
QA_RESEARCH_PATH = ".github/workflows/qa-admissions-first-contacts.yml"
QA_RESEARCH_WORKFLOW_ID = 421894642
QA_RESEARCH_ARCHIVE_SHA = "957c127b1292b0bbd235b3dfe6bad07cfd16f528943a48e2595e12ef33440e41"
QA_RESEARCH_ATTEMPTS = (
    (44180326464, "605d9e309a31946e7fb3dcfc26231e8af09481bb",
     "qa/admission-first-contacts-422-423-425", 134382629498, "failure"),
    (44181500355, "10ce0a118260620221eef81aae5de2f13f972161",
     "qa/admission-first-contacts-422-423-425-r2", 134386706146, "success"),
)


def research_api(path):
    url = "https://api.github.com/repos/" + QA_RESEARCH_REPOSITORY + path
    status, _, body = infrastructure_request_bytes(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    })
    require(status == 200, "Exact ANU first-contact metadata unavailable")
    return json.loads(body)


def verify_research_owner():
    workflow = research_api(f"/actions/workflows/{QA_RESEARCH_WORKFLOW_ID}")
    require(workflow.get("id") == QA_RESEARCH_WORKFLOW_ID
            and workflow.get("path") == QA_RESEARCH_PATH
            and workflow.get("state") == "disabled_manually",
            "Exact prior ANU first-contact workflow is not retired")
    listing = research_api(f"/actions/workflows/{QA_RESEARCH_WORKFLOW_ID}/runs?per_page=100")
    entries = listing.get("workflow_runs")
    require(type(entries) is list and len(entries) == listing.get("total_count") == 2
            and [row.get("id") for row in entries] == [
                row[0] for row in reversed(QA_RESEARCH_ATTEMPTS)],
            "ANU first-contact history differs")
    job_ends = []
    for (run_id, head, branch, job_id, conclusion), listed in zip(
            QA_RESEARCH_ATTEMPTS, reversed(entries)):
        run = research_api(f"/actions/runs/{run_id}/attempts/1")
        require(all(row.get("repository", {}).get("full_name") == QA_RESEARCH_REPOSITORY
                    and row.get("event") == "push" and row.get("status") == "completed"
                    and row.get("conclusion") == "failure"
                    and run_identity(row, QA_RESEARCH_WORKFLOW_ID, QA_RESEARCH_PATH, branch,
                                     QA_RESEARCH_REPOSITORY)
                    == (run_id, 1, head) for row in (listed, run)),
                "Exact ANU first-contact run ownership differs")
        listing = research_api(f"/actions/runs/{run_id}/attempts/1/jobs?per_page=100")
        jobs = listing.get("jobs")
        require(type(jobs) is list and len(jobs) == listing.get("total_count") == 3,
                "Exact ANU first-contact job inventory differs")
        exact = [job for job in jobs if job.get("name") == "First contact anu423"]
        require(len(exact) == 1, "Exact ANU first-contact job is missing")
        job = exact[0]
        require(job.get("id") == job_id and job.get("run_id") == run_id
                and job.get("run_attempt") == 1 and job.get("head_sha") == head
                and job.get("status") == "completed" and job.get("conclusion") == conclusion,
                "Exact ANU first-contact job ownership differs")
        started, ended = epoch(job["started_at"]), epoch(job["completed_at"])
        require(0 < started <= ended <= now() + 1
                and (not job_ends or job_ends[-1] < started), "First-contact chronology differs")
        job_ends.append(ended)
    earlier = research_api(f"/actions/runs/{QA_RESEARCH_ATTEMPTS[0][0]}/artifacts?per_page=100")
    require(earlier.get("total_count") == 0 and earlier.get("artifacts") == [],
            "ANU prewire attempt has an unexpected artifact")
    artifact = research_api("/actions/artifacts/14372037550")
    require(artifact.get("id") == 14372037550
            and artifact.get("name") == "first-contact-anu423-44181500355-1"
            and artifact.get("expired") is False and artifact.get("size_in_bytes") == 196953
            and artifact.get("digest") == "sha256:" + QA_RESEARCH_ARCHIVE_SHA
            and artifact.get("workflow_run") == {
                "id": 44181500355, "repository_id": 1410255493,
                "head_repository_id": 1410255493,
                "head_branch": QA_RESEARCH_ATTEMPTS[-1][2],
                "head_sha": QA_RESEARCH_ATTEMPTS[-1][1]},
            "ANU first-contact archive ownership differs")


def reviewed_research_transfer():
    state = healthy_state(copy.deepcopy(QA_ORIGINAL_STATE))
    require(digest(canonical(state)) == QA_ORIGINAL_STATE_SHA256
            and state["outcome"] == "response_assessed" and state["target"] == SOURCE_URL
            and len(state["starts"]) == len(QA_PRIOR_OPENER_TIMES) == 2
            and all(a <= b <= state["completed"]
                    for a, b in zip(state["starts"], QA_PRIOR_OPENER_TIMES)),
            "Exact ANU original acknowledged state differs")
    state["starts"] = sorted(set(state["starts"] + list(QA_PRIOR_OPENER_TIMES)))
    require(digest(canonical(state)) == QA_TRANSFER_STATE_SHA256,
            "ANU conservative physical timing transfer differs")
    return healthy_state(state)


def authenticated_qa_owner():
    approval = QA_HOSTED_APPROVAL
    require(type(approval) is dict and is_hosted() and __debug__
            and "DATA_SOURCE_TOKEN" not in os.environ
            and os.environ.get("GITHUB_REPOSITORY") == REPOSITORY
            and os.environ.get("GITHUB_REF") == "refs/heads/" + QA_BRANCH
            and os.environ.get("GITHUB_EVENT_NAME") == "push"
            and os.environ.get("GITHUB_WORKFLOW_REF") ==
                REPOSITORY + "/" + QA_PATH + "@refs/heads/" + QA_BRANCH,
            "Canonical GitHub-hosted original QA is unavailable")
    require(re.fullmatch(
        r"https://github\.com/YouthOpps/data-pipeline/issues/423#issuecomment-[1-9][0-9]*",
        approval["evidence"],
    ), "Exact ANU hosted QA decision required")
    require(approval == {"evidence": approval["evidence"],
                         "original_state_sha256": QA_ORIGINAL_STATE_SHA256,
                         "transfer_state_sha256": QA_TRANSFER_STATE_SHA256},
            "Exact reviewed ANU research state digests required")
    verify_research_owner()
    source_state = reviewed_research_transfer()
    current = (int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]),
               os.environ["GITHUB_SHA"])
    require(current[1] == 1, "The reviewed first QA attempt cannot be repeated")
    run = workflow_api(f"/actions/runs/{current[0]}/attempts/{current[1]}")
    workflow_id = run.get("workflow_id")
    require(type(workflow_id) is int and workflow_id > 0
            and run.get("event") == "push" and run.get("status") == "in_progress"
            and run_identity(run, workflow_id, QA_PATH, QA_BRANCH) == current,
            "Original exact QA run identity differs")
    workflow = workflow_api(f"/actions/workflows/{workflow_id}")
    require(workflow.get("id") == workflow_id and workflow.get("path") == QA_PATH
            and workflow.get("state") == "active", "Exact QA workflow is not active")
    history = workflow_api(f"/actions/workflows/{workflow_id}/runs?per_page=100")
    rows = history.get("workflow_runs")
    require(type(rows) is list and len(rows) == history.get("total_count") == 1
            and run_identity(rows[0], workflow_id, QA_PATH, QA_BRANCH) == current,
            "Hosted QA family authority was already consumed")
    job = exact_job(current)
    require(job["status"] == "in_progress", "Original exact QA job is not executing")
    branch = workflow_api("/git/ref/heads/" + QA_BRANCH)
    require(branch.get("ref") == "refs/heads/" + QA_BRANCH
            and branch.get("object", {}).get("type") == "commit"
            and branch["object"].get("sha") == current[2],
            "Original QA branch no longer identifies the executing head")
    status, _, _ = infrastructure_request_bytes(
        _WORKFLOW_API + "/contents/" + WORKFLOW_PATH + "?ref=main",
        headers=github_headers("GITHUB_TOKEN"))
    require(status == 404, "Competing production source workflow exists")
    root = Path(__file__).resolve().parents[2]
    require(digest((root / QA_CERT_PATH).read_bytes()) == QA_CERT_SHA256
            and immutable_file(current[2], QA_CERT_PATH) == (root / QA_CERT_PATH).read_bytes(),
            "Decision-bound public review certificate changed")
    require(immutable_file(current[2], SOURCE_PATH) == Path(__file__).read_bytes()
            and immutable_file(current[2], QA_PATH) == (root / QA_PATH).read_bytes()
            and immutable_file(current[2], SOURCE_PATH.replace("adapter.py", "test_adapter.py"))
                == Path(__file__).with_name("test_adapter.py").read_bytes()
            and immutable_file(current[2], QA_OBSERVER_PATH)
                == (root / QA_OBSERVER_PATH).read_bytes(),
            "Executable QA bytes differ from immutable inputs")
    return current, source_state, job["id"]


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


def owned_state(owner):
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
    expected = artifact_envelope(owner, envelope["state"],
                                 digest(immutable_file(head, SOURCE_PATH)))
    require(expected == envelope, "Owned state payload identity differs")
    return healthy_state(envelope["state"])


def hosted_seed(history):
    if history:
        return owned_state(history[0])
    approval = FIRST_HOSTED_APPROVAL
    require(type(approval) is dict and re.fullmatch(
        r"https://github\.com/YouthOpps/data-pipeline/issues/423#issuecomment-[1-9][0-9]*",
        approval["evidence"]), "First hosted native handoff is OFF")
    require(digest(canonical(approval["state"])) == approval["state_sha256"],
            "Reviewed hosted native state digest differs")
    return healthy_state(approval["state"])


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
    path = Path(os.environ["ANU_PACING_ARTIFACT_PATH"])
    parent = Path(os.environ["RUNNER_TEMP"])
    require(path.is_absolute() and path == parent / "pacing-state.json"
            and parent == parent.resolve(), "Unexpected ANU artifact path")
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


def source_document(journal):
    _request(journal, ROBOTS)
    _, headers, body = _request(journal, SOURCE_URL)
    require(headers.get_content_type() in {"text/html", "application/xhtml+xml"}
            and not any(re.search(r"\b(?:noindex|none)\b", value, re.I)
                        for value in headers.get_all("X-Robots-Tag", [])),
            "ANU content type or indexing directive changed")
    return body.decode(headers.get_content_charset() or "utf-8", "strict")


def collect():
    """Perform one complete source collection while retaining publisher state."""
    global _COLLECTION_STARTED, _HOSTED_OWNER, _QA_JOB_ID
    require(is_hosted() and not _COLLECTION_STARTED and not GATE_FAILED,
            "Original ANU hosted collection unavailable or already consumed")
    _COLLECTION_STARTED = True
    require(os.environ.get("GITHUB_REF") == "refs/heads/" + QA_BRANCH,
            "Temporary ANU collection only permits the exact QA branch")
    _HOSTED_OWNER, source_state, _QA_JOB_ID = authenticated_qa_owner()
    source_state["not_before"] = max(source_state["not_before"], now() + 60)
    source_state["observed_at"] = max(source_state["observed_at"], now())
    authority = initialize_hosted(source_state, _HOSTED_OWNER)
    journal = Journal(authority)
    sealed, failure = None, None
    try:
        with journal:
            state = healthy_state(journal.read())
            journal.capture_claim = create_once(
                journal.directory, COLLECTION_CLAIM,
                {"source": SOURCE_ID, "source_sha256": source_sha(),
                 "initial_state_sha256": digest(canonical(state)), "owner": _HOSTED_OWNER})
            try:
                records = admission_records(source_document(journal), datetime.now(timezone.utc))
                stamp = utc_now()
                for row in records:
                    row.update(created_at=stamp, updated_at=stamp, first_seen_at=stamp,
                               last_seen_at=stamp, last_checked_at=stamp)
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
    require(type(records) is list and (allow_empty or len(records) == 2)
            and len(records) <= 2, "Incomplete ANU direct admission inventory")
    seen = set()
    for row in records:
        require(type(row) is dict and re.fullmatch(
            r"au-anu-international-admissions-2027-semester-[12]", row.get("id", ""))
            and row["id"] not in seen and row.get("source") == SOURCE_ID
            and row.get("source_url") == SOURCE_URL and row.get("url") == SOURCE_URL
            and row.get("kind") == "opportunity"
            and row.get("category") == "other" and row.get("categories") == ["other"]
            and row.get("host_countries") == ["AU"] and row.get("publisher_country") == "AU"
            and row.get("eligible_countries") == [] and row.get("status") in ("open", "unknown")
            and isinstance(row.get("title"), str) and row["title"].strip()
            and isinstance(row.get("summary"), str) and 0 < len(row["summary"]) <= 600,
            "Invalid or duplicate ANU factual record")
        seen.add(row["id"])
        deadline = date.fromisoformat(row["deadline"])
        semester = int(row["id"][-1])
        require(row["deadline"] == deadline.isoformat()
                and deadline.year == REVIEWED_INTAKE_YEAR - (semester == 1)
                and deadline.month == (12 if semester == 1 else 5),
                "Invalid ANU cutoff or intake identity")


def remove_expired_records(records, checked):
    instant = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    require(instant.tzinfo is not None, "ANU expiry check requires a timezone")
    return [row for row in records if instant <= cutoff_time(row["deadline"])]


def metadata(attempt, previous, records, error=None):
    value = {"source": SOURCE_ID, "source_url": SOURCE_URL, "website_url": WEBSITE_URL,
             "publisher_country": "AU", "publisher_type": "university", "language": "en",
             "display_name": "Australian National University", "status": "success" if error is None else "fail",
             "message": ("Validated official direct admission refresh" if error is None
                         else "Collection failed; last-good data preserved"),
             "last_attempt_at": attempt, "last_success_at": (
                 attempt if error is None else previous.get("last_success_at")),
             "last_checked_at": (attempt if error is None else previous.get("last_checked_at")),
             "record_count": len(records), "error": safe_error(error) if error else None}
    if error:
        value["failure_stage"] = getattr(error, "stage", "access")
    return value


def run_publication():
    raise AdapterError("Temporary ANU QA cannot publish")


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
    except (Stop, AdapterError, OSError, ValueError) as error:
        print(safe_error(error), file=sys.stderr)
        result = 1
    sys.exit(result)
