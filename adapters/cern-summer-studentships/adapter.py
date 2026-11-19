"""Collect and publish the official CERN Summer Studentship calls."""

import contextlib
import copy
from datetime import date, datetime, timedelta, timezone
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

import argparse
from dataclasses import dataclass
import base64
import io
import sys
import zipfile
from zoneinfo import ZoneInfo

FAMILY = 'youthopps-cern-careers-publisher-v1'
USER_AGENT = 'YouthOppsBot/1.0 (+https://youthopps.org/bot/)'
ROBOTS = 'https://careers.cern/robots.txt'
ENTRY = 'https://careers.cern/programmes/summer-studentship/'
DETAILS = ('https://careers.cern/jobs/cern-summer-student-programme-2026-member-and-non-member-state/', 'https://careers.cern/jobs/cern-openlab-summer-student-programme-2026/')
URLS = (ROBOTS, ENTRY, *DETAILS)



PARENT = Path('/tmp')
MAX_BODY = 8 * 1024 * 1024
MAX_STATE = 128 * 1024
MAX_ARCHIVE = MAX_STATE + 4096
MAX_CLOCK = 253402300799
BODY_SECONDS = 25
CLOCK = time
GATE_FAILED = False
AUDIT = {'requests': [], 'persistence': [], 'errors': []}
_GITHUB = 'https://api.github.com/repos/YouthOpps/data-source'
_PIPELINE = 'https://api.github.com/repos/YouthOpps/data-pipeline'
_WORKFLOW_PATH = '.github/workflows/fetch-cern-summer-studentships.yml'
_ARTIFACT = 'cern-summer-studentships-pacing-state'
_HOSTED_DECISION = 'https://github.com/YouthOpps/data-pipeline/issues/459#issuecomment-7006688911'
_HOSTED_SEED = {'blocked': False,
 'completed': 1795084433.8716445,
 'completed_logical_id': '6417157fa8174e0d89c3ec832caf8619',
 'completed_physical_id': 'd1ade734930d4256957509401a9c128d',
 'family': 'youthopps-cern-careers-publisher-v1',
 'in_flight': False,
 'interval': 12.2,
 'inventory_completed': 1795084368.850461,
 'legacy_policy_history_unknown': True,
 'legacy_rate_history_unknown': True,
 'logical_id': '6417157fa8174e0d89c3ec832caf8619',
 'not_before': 1795084446.0716445,
 'observed_at': 1795084433.8716445,
 'outcome': 'response_assessed',
 'page_interval': 30,
 'physical_id': 'd1ade734930d4256957509401a9c128d',
 'physical_pending': False,
 'physical_started': 1795084433.0221925,
 'policy': {'body_sha256': '7168a82a1778b64d277695b5dec8dfb2fbc13e659dd1d942cd735918dd44d7ad',
            'legacy_allowed': True,
            'new_allowed': True,
            'status': 200,
            'unavailable': False,
            'url': 'https://careers.cern/robots.txt'},
 'rate_limit_strikes': 0,
 'rate_limit_until': 0,
 'rates': [],
 'schema': 1,
 'starts': [1795077957.7913802,
            1795077958.6784432,
            1795077974.3256795,
            1795077975.27699,
            1795079059.4990814,
            1795079061.584115,
            1795079078.4454632,
            1795079080.6437695,
            1795079097.7524688,
            1795079099.880264,
            1795084350.1884792,
            1795084350.1912763,
            1795084350.1913178,
            1795084367.9887776,
            1795084367.9911637,
            1795084367.991199,
            1795084415.0344267,
            1795084415.0368388,
            1795084415.0368688,
            1795084433.0221925,
            1795084433.0247345,
            1795084433.024764],
 'target': 'https://careers.cern/jobs/cern-openlab-summer-student-programme-2026/'}
_HOSTED_LINEAGE = {'additional_start_observations': [1795077957.7913802,
                                   1795077958.6784432,
                                   1795077974.3256795,
                                   1795077975.27699,
                                   1795079059.4990814,
                                   1795079061.584115,
                                   1795079078.4454632,
                                   1795079080.6437695,
                                   1795079097.7524688,
                                   1795079099.880264,
                                   1795084350.1884792,
                                   1795084350.1912763,
                                   1795084350.1913178,
                                   1795084367.9887776,
                                   1795084367.9911637,
                                   1795084367.991199,
                                   1795084415.0344267,
                                   1795084415.0368388,
                                   1795084415.0368688,
                                   1795084433.0221925,
                                   1795084433.0247345,
                                   1795084433.024764],
 'artifact_id': 14520490077,
 'artifact_sha256': 'sha256:e1e6d518ccbbfad64c281415d5ecc210b062dca32d07a7597f6a7fe48ec2394c',
 'head_sha': '3f830aa4577897d602b06c178cccbc2f888401d5',
 'id': 44506400577,
 'state_sha256': '847fb064f79259be4f9bf81c7b107e1937dcd0078f192ac5064a4f4940499865',
 'transfer_sha256': '847fb064f79259be4f9bf81c7b107e1937dcd0078f192ac5064a4f4940499865',
 'workflow_id': 423694485}
_QA_BRANCH = 'qa/cern459-native'
_QA_PATH = '.github/workflows/verify-cern-summer-studentships-459.yml'
_RESEARCH_SEED = {'blocked': False,
 'completed': 1795079100.4813845,
 'completed_logical_id': 'd81414f6bd324339854c4280da2a24c0',
 'completed_physical_id': '673353bf88214af98d19c76b4a39587b',
 'family': 'youthopps-cern-careers-publisher-v1',
 'in_flight': False,
 'interval': 12.2,
 'inventory_completed': 1795077976.2277768,
 'legacy_policy_history_unknown': True,
 'legacy_rate_history_unknown': True,
 'logical_id': 'd81414f6bd324339854c4280da2a24c0',
 'not_before': 1795079112.6813846,
 'observed_at': 1795079100.4813845,
 'outcome': 'response_assessed',
 'page_interval': 30,
 'physical_id': '673353bf88214af98d19c76b4a39587b',
 'physical_pending': False,
 'physical_started': 1795079097.7524688,
 'policy': {'body_sha256': '7168a82a1778b64d277695b5dec8dfb2fbc13e659dd1d942cd735918dd44d7ad',
            'legacy_allowed': True,
            'new_allowed': True,
            'status': 200,
            'unavailable': False,
            'url': 'https://careers.cern/robots.txt'},
 'rate_limit_strikes': 0,
 'rate_limit_until': 0,
 'rates': [],
 'schema': 1,
 'starts': [1795077957.7913802,
            1795077958.6784432,
            1795077974.3256795,
            1795077975.27699,
            1795079059.4990814,
            1795079061.584115,
            1795079078.4454632,
            1795079080.6437695,
            1795079097.7524688,
            1795079099.880264],
 'target': 'https://careers.cern/jobs/cern-openlab-summer-student-programme-2026/'}
_RESEARCH_STATE_SHA = '6fcbe851000723e2fec81a97d6c5aaaf6a79e681fbdf5539f49a298deb0855fc'
_NATIVE_MAINTAINER_ID = 12278069
_ACTIVE_AUTHORITY = None

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


def decision(value):
    return (isinstance(value, str) and re.fullmatch(
        r'https://github\.com/YouthOpps/data-pipeline/issues/459'
        r'#issuecomment-[1-9][0-9]*', value))


def paths():
    leaf = FAMILY + '-' + str(os.getuid())
    return PARENT / leaf, PARENT / (leaf + '-issue459-adopted.claim')


def machine():
    return digest(Path('/etc/machine-id').read_bytes())


def source_sha():
    return digest(Path(__file__).read_bytes())



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
        raise Stop('Wrong CERN publisher family')
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
    if value['policy'] is None:
        raise Stop('Missing established CERN REP receipt')


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
        verify_current_collection()
        self.verify()
        if self.capture_claim is None:
            raise Stop('No exclusive research capture authority')
        raw, expected = self.capture_claim
        named = os.stat('source-collection.claim',
                        dir_fd=self.directory, follow_symlinks=False)
        private_file(named)
        if (identity(named) != expected
                or read_private_claim(self.directory,
                                      'source-collection.claim') != raw):
            raise Stop('Original one-use research claim changed')

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
                or not retained_starts(before, value)
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
        raise Deadline('CERN operation deadline')

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


def robot_policy(journal, body, status, headers):
    if 400 <= status < 500 and status != 429:
        return True, True, True
    if status != 200:
        raise Stop('REP unreachable or redirected; no follow')
    text = body.decode('utf-8-sig', 'strict')
    significant = [line.split('#', 1)[0].strip() for line in text.splitlines()]
    html_types = {'text/html', 'application/xhtml+xml'}
    content_types = {value.split(';', 1)[0].strip().casefold()
                     for value in headers.get_all('Content-Type', [])}
    if content_types & html_types or any(line.startswith('<') for line in significant):
        raise Stop('Unrecognized robots HTML/XML response; no content follow')
    if any(significant) and not any(re.fullmatch(
            r'(?i)(?:user-agent|allow|disallow|sitemap|crawl-delay|request-rate)\s*:.*',
            line) for line in significant):
        raise Stop('Unrecognized robots representation; no content follow')
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
        elif agents and name in ('allow', 'disallow'):
            saw_rule = True
            rules.append((name, value))
        elif agents and name in ('crawl-delay', 'request-rate'):
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
        patterns = []
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
                patterns.append((pattern, len(prefix.replace('*', '')), name == 'allow'))
        token_allowed = True
        for detail in URLS[1:]:
            target = normalize_octets(urllib.parse.urlsplit(detail).path)
            matches = [(length, allow) for pattern, length, allow in patterns if re.search(pattern, target)]
            if matches:
                longest = max(length for length, _ in matches)
                token_allowed = token_allowed and any(allow for length, allow in matches if length == longest)
        permitted.append(token_allowed)
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


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
INFRASTRUCTURE_OPENER = urllib.request.build_opener(NoRedirect())


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
            or urllib.parse.urlsplit(url).hostname != 'careers.cern'):
        raise Stop('Unreviewed CERN URL or external origin')
    robots = url == ROBOTS
    prior = journal.read()
    count = len(AUDIT['requests'])
    if count >= len(URLS) or url != URLS[count]:
        raise Stop('CERN request frontier or order differs')
    if not robots:
        policy = prior['policy']
        if (prior['target'] != URLS[count - 1] or policy is None or policy['url'] != ROBOTS
                or not policy['new_allowed'] or not policy['legacy_allowed']):
            raise Stop('Exact current robots policy is required for CERN content')
    wait_for_request(journal, url == ENTRY)
    state = journal.read()
    verify_before_request(state)
    state = journal.read()
    state.update(logical_id=uuid.uuid4().hex,
                 physical_id=uuid.uuid4().hex, physical_started=now(),
                 target=url, in_flight=True, physical_pending=True,
                 outcome='reserved')
    state['starts'] = sorted(set(
        state['starts'] + [state['physical_started']]))
    state['observed_at'] = max(state['observed_at'], state['physical_started'])
    journal.save(state)
    receipt = {'url': url, 'request_id': state['physical_id'], 'phase': 'reserved',
               'start': None, 'wall_start': None, 'completion': None, 'wall_completion': None,
               'reserved_wall': state['physical_started'], 'reservation_sha256': digest(journal.ack),
               'request_headers': [], 'headers': [], 'segments': []}
    AUDIT['requests'].append(receipt)
    headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity'}
    req = urllib.request.Request(url, headers=headers, method='GET')
    journal.verify()
    journal.verify_capture()
    if dict((k.casefold(), v) for k, v in req.header_items()).get(
            'user-agent') != USER_AGENT:
        raise Stop('Actual outbound bot identity differs')
    receipt.update(start=CLOCK.monotonic(), wall_start=now(), request_headers=list(req.header_items()))
    response = None
    primary = None
    body = b''
    try:
        with operation_deadline():
            try:
                try:
                    opened_wall, opened_monotonic = now(), CLOCK.monotonic()
                    receipt.update(actual_wall_start=opened_wall, actual_start=opened_monotonic,
                                   phase='opener')
                    response = OPENER.open(req, timeout=BODY_SECONDS)
                except urllib.error.HTTPError as error:
                    response = error
                receipt['phase'] = 'headers'
                status = response.status
                if type(status) is not int or not 200 <= status <= 599:
                    raise Stop('Unassessed response status')
                receipt['status'] = status
                known_status(journal, status, robots)
                response_headers = response.headers
                for pair in response_headers.raw_items():
                    receipt['headers'].append(pair)
                receipt['headers_complete'] = True
                retry_after(journal, response_headers)
                length = framing(response_headers)
                receipt['phase'] = 'body'
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
                receipt.update(body_complete=True, phase='assessment')
                challenge(journal, body, response_headers, robots, status)
                if 300 <= status < 400:
                    raise Stop('Redirect captured, never followed')
                if robots and status != 429:
                    new, legacy, unavailable = robot_policy(
                        journal, body, status, response_headers)
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
                        receipt['closed'] = True
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
        state['starts'] = sorted(set(state['starts'] + [
            receipt['wall_start'], receipt['actual_wall_start'],
        ]))
        state.update(completed_physical_id=state['physical_id'],
                     completed_logical_id=state['logical_id'],
                     completed=stamp, observed_at=max(state['observed_at'],
                                                     stamp),
                     in_flight=False, physical_pending=False,
                     outcome='response_assessed')
        state['not_before'] = max(state['not_before'],
                                  stamp + state['interval'])
        if url == ENTRY:
            state['inventory_completed'] = max(state['inventory_completed'],
                                                stamp)
        journal.save(state)
        receipt.update(settled_sha256=digest(journal.ack), phase='settled')
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



def capture(initialization):
    """Gate and latch one-use capture, including all finalizers."""
    global GATE_FAILED
    try:
        return _capture(initialization)
    except BaseException:
        GATE_FAILED = True
        raise

def create_hosted_namespace(retained):
    """Create an ephemeral hosted namespace from an authenticated owned state."""
    verify_current_collection()
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
                raise Stop('Existing CERN publisher authority cannot be reset')
        pending_raw, pending_id = create_once(parent, claim.name, {
            'stage': 'pending', 'source_sha256': source_sha(),
            'history_sha256': _ACTIVE_AUTHORITY['lineage'], 'machine_sha256': machine(),
            'decision': _ACTIVE_AUTHORITY['decision']})
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

        state = validate(copy.deepcopy(retained))
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
                  'history_sha256': _ACTIVE_AUTHORITY['lineage'],
                  'machine_sha256': machine(),
                  'directory_identity': list(directory_id),
                  'directory_metadata': [d.st_uid, d.st_gid,
                                         stat.S_IMODE(d.st_mode)],
                  'parent_identity': list(parent_id),
                  'parent_metadata': list(parent_meta), 'locks': locks,
                  'state_identity': list(state_id),
                  'state_metadata': state_metadata,
                  'initializer_claim_identity': list(pending_id),
                  'state_sha256': digest(state_raw),
                  'decision': _ACTIVE_AUTHORITY['decision'],
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

SOURCE_ID = "cern-summer-studentships"
INVENTORY = ENTRY
SOURCE_URL = INVENTORY
DISPLAY_NAME = "CERN Summer Studentship"
ATTRIBUTION = "CERN Careers"
HOST_ZONE = ZoneInfo("Europe/Zurich")
PROGRAMMES = {
    "SUMM": "Summer Student Programme",
    "SUM-OPENLAB": "OPENLAB Summer Student Programme",
}
REFERENCE = re.compile(r"(SUMM|SUM-OPENLAB)-(20[0-9]{2})")
TITLE = re.compile(
    r"CERN (OPENLAB Summer Student Programme|Summer Student Programme) (20[0-9]{2})"
    r"(?: \(Member and Non-Member State\))?",
    re.IGNORECASE,
)
DEADLINE = re.compile(
    r"\bbefore\s+([0-9]{2}/[0-9]{2}/[0-9]{4})\s+at\s+([0-9]{2}:[0-9]{2})\s*"
    r"\(Geneva Time\)",
    re.IGNORECASE,
)
START = re.compile(r"\bIdeal start date:\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", re.IGNORECASE)
ALLOWANCE = re.compile(
    r"\b([0-9]+)\s+CHF per calendar day net subsistence allowance\b", re.IGNORECASE
)
HIDDEN_TAGS = frozenset(("script", "style", "template", "noscript"))
VOID_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"))


@dataclass(frozen=True)
class Opportunity:
    title: str
    reference: str
    url: str

    @property
    def programme(self):
        return self.reference.rsplit("-", 1)[0]

    @property
    def year(self):
        return int(self.reference.rsplit("-", 1)[1])


def finish(chunks):
    return " ".join("".join(chunks).split())


def official_detail_url(value, programme):
    if not isinstance(value, str):
        return False
    url = urllib.parse.urlsplit(value)
    path = {
        "SUMM": r"/jobs/cern-summer-student-programme(?:-[0-9]{4})?-member-and-non-member-state/",
        "SUM-OPENLAB": r"/jobs/cern-openlab-summer-student-programme(?:-[0-9]{4})?/",
    }[programme]
    return (url.scheme == "https" and url.netloc == "careers.cern"
            and re.fullmatch(path, url.path) is not None
            and not url.query and not url.fragment and value == url.geturl())


class Page(HTMLParser):
    """Read visible main content and inventory cards without resolving links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.main = False
        self.main_seen = False
        self.main_closed = False
        self.main_text = []
        self.heading = None
        self.titles = []
        self.card = None
        self.cards = []

    def handle_starttag(self, tag, pairs):
        attributes = dict(pairs)
        hidden = (tag in HIDDEN_TAGS or "hidden" in attributes
                  or (attributes.get("aria-hidden") or "").lower() == "true")
        hidden = hidden or bool(self.stack and self.stack[-1][1])
        if tag not in VOID_TAGS:
            self.stack.append((tag, hidden))
        if hidden:
            return
        if tag == "main":
            if self.main_seen:
                raise ValueError("Multiple visible main elements")
            self.main_seen = self.main = True
        if not self.main:
            return
        self.main_text.append(" ")
        if tag == "h1":
            if self.heading is not None:
                raise ValueError("Nested main heading")
            self.heading = []
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "job-offer-teaser" in classes:
            if self.card is not None:
                raise ValueError("Nested job card")
            self.card = {"url": attributes.get("href"), "title": [], "reference": [],
                         "regions": {}, "seen": set()}
        if self.card is not None:
            regions = self.card["regions"]
            field = {"h3": "title"}.get(tag)
            if tag == "div" and "ref" in classes:
                field = "reference"
            if field:
                if field in self.card["seen"]:
                    raise ValueError("Duplicate job card field")
                self.card["seen"].add(field)
                regions[field] = len(self.stack)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            name, hidden = self.stack[index]
            if name != tag:
                continue
            del self.stack[index:]
            if self.card is not None:
                regions = self.card["regions"]
                for field in tuple(regions):
                    if regions[field] > index:
                        del regions[field]
            if not hidden and self.main:
                self.main_text.append(" ")
                if tag == "h1" and self.heading is not None:
                    self.titles.append(finish(self.heading))
                    self.heading = None
                if self.card is not None and tag == "a":
                    self.cards.append((self.card["url"], finish(self.card["title"]),
                                       finish(self.card["reference"])))
                    self.card = None
                if tag == "main":
                    self.main = False
                    self.main_closed = True
            break

    def handle_startendtag(self, tag, pairs):
        self.handle_starttag(tag, pairs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.main or (self.stack and self.stack[-1][1]):
            return
        self.main_text.append(data)
        if self.heading is not None:
            self.heading.append(data)
        if self.card is not None:
            for field, depth in self.card["regions"].items():
                if depth <= len(self.stack):
                    self.card[field].append(data)


def read_page(body):
    page = Page()
    page.feed(body.decode("utf-8", "strict"))
    page.close()
    if not page.main_closed or page.card is not None or page.heading is not None:
        raise ValueError("Incomplete CERN main content")
    return page


def parse_inventory(body):
    """Use the official main cards and their native reference. Slugs may be stale."""
    page = read_page(body)
    if page.titles != ["Summer Studentship"]:
        raise ValueError("Unexpected CERN programme page")
    opportunities = {}
    routes = set()
    for url, title, reference in page.cards:
        match = REFERENCE.fullmatch(reference)
        if not match:
            continue
        programme, year = match.groups()
        named = TITLE.fullmatch(title)
        if (named is None or named[1].casefold() != PROGRAMMES[programme].casefold()
                or named[2] != year or not official_detail_url(url, programme)):
            raise ValueError("Conflicting official CERN opportunity card")
        candidate = Opportunity(title, reference, url)
        previous = opportunities.get(reference)
        if previous is not None:
            if candidate != previous:
                raise ValueError("CERN reference has conflicting cards")
            continue
        if url in routes:
            raise ValueError("CERN detail route has multiple references")
        routes.add(url)
        opportunities[reference] = candidate
    results = tuple(sorted(opportunities.values(), key=lambda item: item.reference))
    if (len(results) != len(PROGRAMMES)
            or {item.programme for item in results} != PROGRAMMES.keys()
            or len({item.year for item in results}) != 1):
        raise ValueError("Incomplete or mixed-cycle CERN studentship inventory")
    return results


def single_match(pattern, text, field):
    matches = set(pattern.findall(text))
    if len(matches) != 1:
        raise ValueError("Missing or conflicting CERN " + field)
    return matches.pop()


def parse_detail(body, candidate, checked_at):
    """Validate one complete application page and return facts for one candidate."""
    if checked_at.utcoffset() is None:
        raise ValueError("A timezone-aware check time is required")
    page = read_page(body)
    text = " ".join("".join(page.main_text).split())
    if page.titles != [candidate.title]:
        raise ValueError("CERN detail title differs from the scoped inventory")
    if not re.search(rf"(?<![\w-]){re.escape(candidate.reference)}(?![\w-])", text):
        raise ValueError("CERN detail reference differs from the scoped inventory")
    if not re.search(r"\bapply now\b", text, flags=re.IGNORECASE):
        raise ValueError("Current CERN application entry is missing")
    due_date, due_time = single_match(DEADLINE, text, "Geneva deadline")
    deadline = datetime.strptime(f"{due_date} {due_time}", "%d/%m/%Y %H:%M").replace(tzinfo=HOST_ZONE)
    start = datetime.strptime(single_match(START, text, "start date"), "%d/%m/%Y").date()
    if start.year != candidate.year or deadline.date() >= start:
        raise ValueError("CERN detail dates differ from the stated cycle")
    allowance = int(single_match(ALLOWANCE, text, "daily allowance"))
    eligibility = text.casefold().replace("’", "'")
    degree_evidence = (
        re.search(r"\bbachelor(?:'s)? or master(?:'s)? student\b", eligibility)
        and re.search(r"\b(6|six) semesters\b", eligibility)
        and re.search(r"\bremain (?:enrolled|registered)\b", eligibility)
    )
    programme = ("research and technical projects" if candidate.programme == "SUMM"
                 else "computing research projects")
    summary = (f"CERN summer studentship in Geneva for {programme}. "
               f"The official call lists a net allowance of CHF {allowance} per calendar day.")
    if degree_evidence:
        summary += " Applicants need bachelor's or master's studies, six semesters and continued enrolment."
    if candidate.programme == "SUMM" and "(member and non-member state)" in candidate.title.casefold():
        summary += " CERN lists applications from both member and non-member states."
    stamp = checked_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    return {
        "id": f"{SOURCE_ID}-{candidate.reference.lower()}",
        "title": candidate.title,
        "source": SOURCE_ID,
        "source_url": INVENTORY,
        "url": candidate.url,
        "summary": summary,
        "category": "internships",
        "categories": ["internships"],
        "kind": "opportunity",
        "location": "Geneva, Switzerland",
        "host_countries": ["CH"],
        "eligible_countries": [],
        "publisher_country": "CH",
        "attribution": ATTRIBUTION,
        "published_at": None,
        "language": "en",
        "summary_language": "en",
        "status": "open" if checked_at < deadline else "expired",
        "deadline": deadline.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "start_date": start.isoformat(),
        "created_at": stamp,
        "updated_at": stamp,
        "first_seen_at": stamp,
        "last_seen_at": stamp,
        "last_checked_at": stamp,
        "classification": {"method": "publisher-detail", "evidence": [INVENTORY, candidate.url]},
    }


class AdapterError(Exception):
    def __init__(self, message, stage='access', status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


def retained_starts(before, after):
    removed = set(before['starts']) - set(after['starts'])
    period = max(90 * 86400, *(rate['period'] for rate in after['rates']), 60)
    return all(stamp <= after['observed_at'] - period for stamp in removed)


def cutoff_state(state):
    period = max(90 * 86400, *(rate['period'] for rate in state['rates']), 60)
    state['starts'] = [s for s in state['starts'] if s > state['observed_at'] - period]
    return state


def safe_clock():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def request_bytes(url, *, method='GET', headers=None, payload=None, maximum=8 * 1024 * 1024):
    """Receive bounded GitHub infrastructure responses and do not follow redirects."""
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    if (parsed.scheme != 'https' or parsed.netloc != host or parsed.fragment
            or not (host == 'api.github.com'
                    or host.endswith(('.blob.core.windows.net', '.actions.githubusercontent.com')))):
        raise AdapterError('Unexpected GitHub infrastructure URL')
    if host != 'api.github.com' and (headers or method != 'GET' or payload is not None):
        raise AdapterError('Credentials or body refused for storage request')
    request = urllib.request.Request(url, method=method, data=payload,
                                     headers=headers or {})
    try:
        try:
            response = INFRASTRUCTURE_OPENER.open(request, timeout=20)
        except urllib.error.HTTPError as received:
            response = received
        with response:
            body = response.read(maximum + 1)
            if len(body) > maximum:
                raise AdapterError('GitHub infrastructure response exceeds its bound')
            return response.status, response.headers, body
    except (OSError, TimeoutError, ValueError):
        raise AdapterError('GitHub infrastructure response unavailable') from None


def workflow_api(path, allow_missing=False):
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        raise AdapterError('GITHUB_TOKEN is required for pacing chronology')
    status, _, body = request_bytes(_PIPELINE + path, headers={
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
    })
    if allow_missing and status == 404:
        return None
    if status != 200:
        raise AdapterError('GitHub pacing chronology request failed', status=status)
    try:
        return json.loads(body)
    except (ValueError, UnicodeError):
        raise AdapterError('GitHub pacing chronology response malformed') from None


def workflow_definition(allow_missing=False):
    filename = _WORKFLOW_PATH.rsplit('/', 1)[-1]
    value = workflow_api('/actions/workflows/' + filename, allow_missing=allow_missing)
    if value is None:
        return None
    if (value.get('path') != _WORKFLOW_PATH or type(value.get('id')) is not int
            or value['id'] <= 0):
        raise AdapterError('Workflow definition identity differs')
    return value


def verify_run(row, workflow_id, run_id, attempt):
    if (row.get('id') != run_id or row.get('run_attempt') != attempt
            or row.get('workflow_id') != workflow_id or row.get('path') != _WORKFLOW_PATH
            or row.get('head_branch') != 'main'
            or row.get('head_repository', {}).get('full_name') != 'YouthOpps/data-pipeline'
            or row.get('repository', {}).get('full_name') != 'YouthOpps/data-pipeline'
            or not re.fullmatch(r'[0-9a-f]{40}', row.get('head_sha') or '')
            or row.get('event') not in ('push', 'workflow_dispatch', 'schedule')):
        raise AdapterError('Untrusted source workflow run')
    return row


def attempt_and_job(run, attempt, expect_status):
    verified = workflow_api(f"/actions/runs/{run['id']}/attempts/{attempt}")
    verify_run(verified, run['workflow_id'], run['id'], attempt)
    if verified['head_sha'] != run['head_sha'] or verified['status'] != expect_status:
        raise AdapterError('Source attempt head or status differs')
    result = workflow_api(f"/actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")
    jobs = result.get('jobs')
    if type(jobs) is not list or result.get('total_count') != 1 or len(jobs) != 1:
        raise AdapterError('One exact source collect job is required')
    job = jobs[0]
    if (job.get('run_id') != run['id'] or job.get('run_attempt') != attempt
            or job.get('head_sha') != run['head_sha'] or job.get('head_branch') != 'main'
            or job.get('status') != expect_status or job.get('name') != 'collect'):
        raise AdapterError('Foreign or incomplete source collect job')
    return verified, job


def source_at_revision(head):
    path = f'/contents/adapters/{SOURCE_ID}/adapter.py?ref={head}'
    blob = workflow_api(path)
    if blob.get('encoding') != 'base64':
        raise AdapterError('Producer source bytes unavailable')
    content = base64.b64decode(''.join(blob['content'].split()), validate=True)
    signed = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\x00' + content)
    if signed.hexdigest() != blob.get('sha'):
        raise AdapterError('Producer source blob differs')
    return digest(content)


def authenticated_hosted_run():
    if (os.environ.get('GITHUB_REPOSITORY') != 'YouthOpps/data-pipeline'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'
            or not os.environ.get('DATA_SOURCE_TOKEN')):
        raise AdapterError('Hosted source requires its normal repository and publication credentials')
    run_id, attempt = [os.environ.get(key, '') for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')]
    if not all(re.fullmatch(r'[1-9][0-9]*', value) for value in (run_id, attempt)):
        raise AdapterError('Malformed current workflow identity')
    definition = workflow_definition()
    if definition['state'] != 'active':
        raise AdapterError('Hosted source workflow is not active')
    run_id, attempt = int(run_id), int(attempt)
    current = workflow_api(f'/actions/runs/{run_id}/attempts/{attempt}')
    verify_run(current, definition['id'], run_id, attempt)
    attempt_and_job(current, attempt, 'in_progress')
    head = os.environ.get('GITHUB_SHA')
    if (os.environ.get('GITHUB_WORKFLOW_SHA') != head
            or os.environ.get('GITHUB_WORKFLOW_REF')
            != 'YouthOpps/data-pipeline/' + _WORKFLOW_PATH + '@refs/heads/main'):
        raise AdapterError('Exact current workflow reference differs')
    if current['head_sha'] != head or source_at_revision(head) != source_sha():
        raise AdapterError('Current authenticated producer source does not match')
    return current


def inventory(workflow_id):
    """Read the entire bounded workflow chronology, without status filtering."""
    rows = []
    total = None
    for page in range(1, 11):
        result = workflow_api(f'/actions/workflows/{workflow_id}/runs?per_page=100&page={page}')
        batch = result.get('workflow_runs')
        count = result.get('total_count')
        if (type(count) is not int or not 1 <= count <= 1000
                or type(batch) is not list or len(batch) > 100
                or total is not None and total != count):
            raise AdapterError('Workflow chronology incomplete or exceeded its bound')
        total = count
        rows.extend(batch)
        if len(rows) >= total:
            break
        if len(batch) < 100:
            raise AdapterError('Workflow chronology prematurely ended')
    keys = [row.get('id') for row in rows]
    if len(rows) != total or len(set(keys)) != total:
        raise AdapterError('Workflow chronology contains missing or duplicate runs')
    return rows


def job_epoch(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise AdapterError('Unaware exact source job timestamp')
    return dt.timestamp()


def exact_predecessor(current):
    rows = inventory(current['workflow_id'])
    matching = [r for r in rows if r['id'] == current['id']]
    if len(matching) != 1 or matching[0]['run_attempt'] != current['run_attempt']:
        raise AdapterError('Actual current workflow attempt missing from chronology')
    _, active = attempt_and_job(current, current['run_attempt'], 'in_progress')
    started_at = job_epoch(active['started_at'])
    completed = []
    for row in rows:
        verify_run(row, current['workflow_id'], row['id'], row['run_attempt'])
        if row['id'] == current['id']:
            last_attempt = row['run_attempt'] - 1
        elif row['status'] == 'completed':
            last_attempt = row['run_attempt']
        elif row['status'] in ('queued', 'waiting', 'pending', 'requested') and row['run_attempt'] == 1:
            waiting = workflow_api(f"/actions/runs/{row['id']}/attempts/1/jobs?per_page=2")
            if waiting.get('total_count') != 0 or waiting.get('jobs') != []:
                raise AdapterError('Unexpected queued producer job')
            continue
        else:
            raise AdapterError('Another source execution may own publisher access')
        for attempt in range(1, last_attempt + 1):
            run, job = attempt_and_job(row, attempt, 'completed')
            began, ended = job_epoch(job['started_at']), job_epoch(job['completed_at'])
            if not 0 < began <= ended <= started_at:
                raise AdapterError('Exact producer job has ambiguous chronology')
            completed.append((ended, run))
    if len({ended for ended, _ in completed}) != len(completed):
        raise AdapterError('Producer job completion order is ambiguous')
    return max(completed, key=lambda item: item[0])[1] if completed else None


def approved_decision(value):
    return (isinstance(value, str) and re.fullmatch(
        r'https://github\.com/YouthOpps/data-pipeline/issues/459#issuecomment-[1-9][0-9]*', value))


def transfer_qa_state():
    """Transfer only the reviewed exact successful hosted QA owner's state."""
    info = _HOSTED_LINEAGE
    if (not approved_decision(_HOSTED_DECISION) or _HOSTED_SEED is None
            or type(info) is not dict
            or set(info) != {'id', 'workflow_id', 'head_sha', 'artifact_id',
                             'artifact_sha256', 'state_sha256', 'transfer_sha256',
                             'additional_start_observations'}
            or any(type(info[key]) is not int or info[key] <= 0
                   for key in ('id', 'workflow_id', 'artifact_id'))
            or not re.fullmatch(r'[a-f0-9]{40}', info['head_sha'])
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', info['artifact_sha256'])
            or digest(canonical(_HOSTED_SEED)) != info['transfer_sha256']):
        raise AdapterError('Reviewed first hosted exact QA MAX is absent')
    definition = workflow_api(f"/actions/workflows/{info['workflow_id']}")
    if (definition.get('path') != _QA_PATH
            or definition.get('id') != info['workflow_id']
            or definition.get('state') != 'disabled_manually'):
        raise AdapterError('Reviewed QA workflow is not retired')
    current = workflow_api(f"/actions/runs/{info['id']}/attempts/1")
    expected = {
        'id': info['id'], 'run_attempt': 1, 'workflow_id': info['workflow_id'],
        'path': _QA_PATH, 'head_branch': _QA_BRANCH, 'head_sha': info['head_sha'],
        'event': 'push', 'status': 'completed', 'conclusion': 'success',
    }
    if (any(current.get(key) != value for key, value in expected.items())
            or any(current.get(key, {}).get('full_name') != 'YouthOpps/data-pipeline'
                   for key in ('head_repository', 'repository'))):
        raise AdapterError('Reviewed exact QA run differs')
    runs = workflow_api(f"/actions/workflows/{info['workflow_id']}/runs?per_page=2")
    if (runs.get('total_count') != 1 or type(runs.get('workflow_runs')) is not list
            or len(runs['workflow_runs']) != 1
            or any(runs['workflow_runs'][0].get(k) != v for k, v in expected.items())):
        raise AdapterError('Reviewed exact sole QA history differs')
    result = workflow_api(f"/actions/runs/{info['id']}/attempts/1/jobs?per_page=2")
    expected_job = {
        'name': 'verify', 'run_id': info['id'], 'run_attempt': 1,
        'head_sha': info['head_sha'], 'head_branch': _QA_BRANCH,
        'status': 'completed', 'conclusion': 'success',
    }
    jobs = result.get('jobs')
    if (result.get('total_count') != 1 or type(jobs) is not list or len(jobs) != 1
            or any(jobs[0].get(k) != v for k, v in expected_job.items())):
        raise AdapterError('Reviewed exact completed QA job differs')
    state = owned_pacing_state(current, SOURCE_ID + '-qa-state-1', info)
    if digest(canonical(state)) != info['state_sha256']:
        raise AdapterError('Reviewed terminal QA state differs from original bytes')
    # The native journal already acknowledged every review-relevant start.
    # No observation or restrictive field may change in the handoff.
    if state != _HOSTED_SEED or info['additional_start_observations'] != state['starts']:
        raise AdapterError('Reviewed hosted state or acknowledged observations differ')
    return validate(state)


def restore_hosted_state(prior):
    if prior is None:
        return transfer_qa_state()
    return owned_pacing_state(prior, f"{_ARTIFACT}-{prior['run_attempt']}")


def owned_pacing_state(prior, name, approved_artifact=None):
    """Restore exact original owner bytes through the real GitHub artifact path."""
    result = workflow_api(f"/actions/runs/{prior['id']}/artifacts?per_page=100")
    artifacts = result.get('artifacts')
    if type(artifacts) is not list or len(artifacts) != result.get('total_count'):
        raise AdapterError('Prior owned artifact inventory incomplete')
    matching = [item for item in artifacts if item.get('name') == name]
    if len(matching) != 1:
        raise AdapterError('Newest exact prior pacing artifact is absent or ambiguous')
    item = matching[0]
    expected = item.get('digest') or ''
    zip_size = item.get('size_in_bytes')
    if (item.get('expired') is not False or type(zip_size) is not int
            or not 0 < zip_size <= MAX_ARCHIVE or not re.fullmatch(r'sha256:[0-9a-f]{64}', expected)
            or type(item.get('id')) is not int or item['id'] <= 0
            or item.get('workflow_run', {}).get('id') != prior['id']
            or item.get('workflow_run', {}).get('head_sha') != prior['head_sha']):
        raise AdapterError('Foreign or unavailable predecessor artifact')
    if approved_artifact and (
            item['id'] != approved_artifact['artifact_id']
            or expected != approved_artifact['artifact_sha256']):
        raise AdapterError('Reviewed QA artifact owner or digest differs')
    url = _PIPELINE + f"/actions/artifacts/{item['id']}/zip"
    code, headers, _ = request_bytes(url, maximum=10000, headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json',
    })
    locations = headers.get_all('Location') or []
    if code != 302 or len(locations) != 1:
        raise AdapterError('Expected exact signed artifact redirect')
    location = urllib.parse.urlsplit(locations[0])
    if not (location.hostname or '').endswith(
            ('.blob.core.windows.net', '.actions.githubusercontent.com')):
        raise AdapterError('Unexpected signed artifact origin')
    code, _, raw = request_bytes(locations[0], maximum=MAX_ARCHIVE)
    if code != 200 or len(raw) != zip_size or 'sha256:' + digest(raw) != expected:
        raise AdapterError('Signed artifact bytes differ')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = archive.infolist()
        if (len(members) != 1 or members[0].filename != 'pacing-state.json'
                or members[0].flag_bits & 1 or not 0 < members[0].file_size <= MAX_STATE
                or stat.S_ISLNK(members[0].external_attr >> 16)):
            raise AdapterError('Invalid pacing ZIP contents')
        envelope = json.loads(archive.read(members[0]))
    expected_owner = {
        'schema': 1, 'repository': 'YouthOpps/data-pipeline', 'source': SOURCE_ID,
        'run_id': prior['id'], 'run_attempt': prior['run_attempt'],
        'head_sha': prior['head_sha'], 'adapter_sha256': source_at_revision(prior['head_sha']),
    }
    if (type(envelope) is not dict
            or set(envelope) != set(expected_owner) | {'state','state_sha256'}
            or any(envelope[key] != value for key, value in expected_owner.items())
            or digest(canonical(envelope['state'])) != envelope['state_sha256']):
        raise AdapterError('Owned pacing envelope differs from authenticated predecessor')
    return validate(envelope['state'])


def verify_current_collection():
    if GATE_FAILED or _ACTIVE_AUTHORITY is None or not __debug__:
        raise AdapterError('Current collection authority unavailable')
    if _ACTIVE_AUTHORITY['sha256'] != source_sha():
        raise AdapterError('Collection source bytes changed')


def verify_research_predecessor(seed):
    """Authenticate the exact retired public research run and captured journal."""
    expected_seed = '6fcbe851000723e2fec81a97d6c5aaaf6a79e681fbdf5539f49a298deb0855fc'
    if digest(canonical(seed)) != expected_seed:
        raise AdapterError('Native QA seed differs from the original hosted terminal')
    api = 'https://api.github.com/repos/YouthOpps/ai-workspace'
    workflow = 423612283
    run_id = 44492847737

    def read(path):
        status, _, body = request_bytes(api + path, maximum=49152)
        if status != 200:
            raise AdapterError('Published research run identity is unavailable')
        return json.loads(body)

    first = read('/actions/workflows/423596095')
    if (first.get('id') != 423596095 or first.get('state') != 'disabled_manually'
            or first.get('path') != '.github/workflows/qa-cern-summer-studentship-first-contact.yml'):
        raise AdapterError('Original CERN research publisher owner is not retired')
    definition = read(f'/actions/workflows/{workflow}')
    path = '.github/workflows/qa-cern-summer-studentship-details.yml'
    research = read(f'/actions/runs/{run_id}/attempts/1')
    inventory = read(f'/actions/workflows/{workflow}/runs?per_page=2')
    expected = {
        'id': run_id, 'path': path, 'workflow_id': workflow, 'run_attempt': 1,
        'head_sha': 'cafc7978f3e7cc07f39f022cc985241fdb0f1786',
        'head_branch': 'qa/cern-summer-studentship-details',
        'event': 'push', 'status': 'completed', 'conclusion': 'success',
    }
    runs = inventory.get('workflow_runs')
    if (definition.get('id') != workflow or definition.get('path') != path
            or definition.get('state') != 'disabled_manually'
            or inventory.get('total_count') != 1 or type(runs) is not list or len(runs) != 1
            or any(research.get(k) != v or runs[0].get(k) != v for k, v in expected.items())
            or any(research.get(k, {}).get('full_name') != 'YouthOpps/ai-workspace'
                   for k in ('repository', 'head_repository'))):
        raise AdapterError('Exact retired research owner differs')
    jobs = read(f'/actions/runs/{run_id}/attempts/1/jobs?per_page=2')
    job_rows = jobs.get('jobs')
    expected_job = {'id': 135452567297, 'run_id': run_id, 'head_sha': expected['head_sha'],
                    'name': 'First contact cern459details', 'status': 'completed', 'conclusion': 'success'}
    if (jobs.get('total_count') != 1 or type(job_rows) is not list or len(job_rows) != 1
            or any(job_rows[0].get(key) != value for key, value in expected_job.items())):
        raise AdapterError('Exact retired CERN detail job differs')
    rows = read(f'/actions/runs/{run_id}/artifacts?per_page=2')
    artifacts = rows.get('artifacts')
    if rows.get('total_count') != 1 or type(artifacts) is not list or len(artifacts) != 1:
        raise AdapterError('Original hosted research artifact inventory differs')
    artifact = artifacts[0]
    expected_artifact = {
        'id': 14514165911, 'name': 'detail-research-cern459details-44492847737-1',
        'digest': 'sha256:e138d1f39e109ee134710ab974b6e396dedf75826cb0c90b908473d9083af80f',
        'size_in_bytes': 416051, 'expired': False,
    }
    if (any(artifact.get(k) != v for k, v in expected_artifact.items())
            or artifact.get('workflow_run', {}).get('id') != run_id
            or artifact.get('workflow_run', {}).get('head_sha') != expected['head_sha']):
        raise AdapterError('Original reviewed encrypted evidence artifact differs')


def native_scope():
    return digest(canonical({'state_sha256': _RESEARCH_STATE_SHA, 'urls': URLS}))


def verify_native_grant(current):
    """Find the sole unchanged maintainer approval for the exact actual run."""
    expected = (f"Approved CERN native head {current['head_sha']}",
                f"CERN native scope {native_scope()}")
    run_created = current.get('created_at')
    utc_second = r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z'
    if not isinstance(run_created, str) or not re.fullmatch(utc_second, run_created):
        raise AdapterError('Unverifiable CERN native run creation time')
    grants = []
    for page in range(1, 6):
        rows = workflow_api(f'/issues/459/comments?per_page=100&page={page}')
        if type(rows) is not list:
            raise AdapterError('Native approval comment inventory is incomplete')
        for comment in rows:
            user = comment.get('user') or {}
            if user.get('id') != _NATIVE_MAINTAINER_ID or user.get('type') != 'User':
                continue
            lines = (comment.get('body') or '').splitlines()
            if not all(line in lines for line in expected):
                continue
            key = comment.get('id')
            created = comment.get('created_at')
            url = comment.get('html_url')
            if (type(key) is not int or key <= 0 or not approved_decision(url)
                    or url != f'https://github.com/YouthOpps/data-pipeline/issues/459#issuecomment-{key}'
                    or comment.get('issue_url') != _PIPELINE + '/issues/459'
                    or not isinstance(created, str) or not re.fullmatch(utc_second, created)
                    or created >= run_created or created != comment.get('updated_at')
                    or any(lines.count(line) != 1 for line in expected)):
                raise AdapterError('Exact native grant is late, edited or malformed')
            grants.append(url)
        if len(rows) < 100:
            break
    else:
        raise AdapterError('Native approval comment inventory exceeded its bound')
    if len(grants) != 1:
        raise AdapterError('Exact unique CERN native grant is unavailable')
    return grants[0]


def qa_workflow_binding(*, admission=True):
    """Accept one reviewed GitHub push to the isolated original-test workflow."""
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('GITHUB_REPOSITORY') != 'YouthOpps/data-pipeline'
            or 'DATA_SOURCE_TOKEN' in os.environ
            or os.environ.get('GITHUB_REF') != 'refs/heads/' + _QA_BRANCH
            or os.environ.get('GITHUB_EVENT_NAME') != 'push'
            or os.environ.get('GITHUB_RUN_ATTEMPT') != '1'
            or os.environ.get('GITHUB_JOB') != 'verify'
            or os.environ.get('GITHUB_ACTOR_ID') != '12278069'):
        raise AdapterError('Exact GitHub-only native QA authority is OFF')
    head = os.environ.get('GITHUB_SHA', '')
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if (not re.fullmatch(r'[0-9a-f]{40}', head)
            or not re.fullmatch(r'[1-9][0-9]*', run_id)
            or os.environ.get('GITHUB_WORKFLOW_SHA') != head
            or os.environ.get('GITHUB_WORKFLOW_REF')
            != 'YouthOpps/data-pipeline/' + _QA_PATH + '@refs/heads/' + _QA_BRANCH):
        raise AdapterError('Exact QA job identity differs')
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    if (event.get('created') is not True or event.get('deleted') is not False
            or event.get('ref') != 'refs/heads/' + _QA_BRANCH
            or event.get('before') != '0' * 40 or event.get('after') != head
            or event.get('repository', {}).get('full_name') != 'YouthOpps/data-pipeline'
            or event.get('sender', {}).get('id') != _NATIVE_MAINTAINER_ID
            or event.get('sender', {}).get('type') != 'User'):
        raise AdapterError('Native QA requires one exact first branch creation')
    run_id = int(run_id)
    current = workflow_api(f'/actions/runs/{run_id}/attempts/1')
    workflow_id = current.get('workflow_id')
    if type(workflow_id) is not int or workflow_id <= 0:
        raise AdapterError('Missing native QA workflow identity')
    definition = workflow_api(f'/actions/workflows/{workflow_id}')
    if (definition.get('id') != workflow_id or definition.get('path') != _QA_PATH
            or (admission and definition.get('state') != 'active')):
        raise AdapterError('Native QA workflow definition differs')
    immutable_workflow = workflow_api(f'/contents/{_QA_PATH}?ref={head}')
    if (immutable_workflow.get('path') != _QA_PATH
            or not re.fullmatch(r'[0-9a-f]{40}', immutable_workflow.get('sha') or '')):
        raise AdapterError('Exact immutable QA workflow missing')
    expected = {
        'id': run_id, 'run_attempt': 1, 'workflow_id': workflow_id,
        'path': _QA_PATH, 'head_branch': _QA_BRANCH,
        'head_sha': head, 'event': 'push', 'status': 'in_progress',
    }
    if (any(current.get(key) != value for key, value in expected.items())
            or any(current.get(key, {}).get('full_name') != 'YouthOpps/data-pipeline'
                   for key in ('head_repository', 'repository'))
            or any((current.get(key, {}).get('id'), current.get(key, {}).get('type'))
                   != (_NATIVE_MAINTAINER_ID, 'User') for key in ('actor', 'triggering_actor'))):
        raise AdapterError('Current exact QA run differs')
    if admission:
        inventory_result = workflow_api(f'/actions/workflows/{workflow_id}/runs?per_page=2')
        runs = inventory_result.get('workflow_runs')
        if (inventory_result.get('total_count') != 1 or type(runs) is not list
                or len(runs) != 1 or any(runs[0].get(key) != value
                                        for key, value in expected.items())):
            raise AdapterError('One exact non-repeatable QA run is required')
    result = workflow_api(f'/actions/runs/{run_id}/attempts/1/jobs?per_page=2')
    jobs = result.get('jobs')
    expected_job = {
        'name': 'verify', 'run_id': run_id, 'run_attempt': 1,
        'head_sha': head, 'head_branch': _QA_BRANCH, 'status': 'in_progress',
    }
    if (result.get('total_count') != 1 or type(jobs) is not list
            or len(jobs) != 1 or any(jobs[0].get(key) != value
                                    for key, value in expected_job.items())):
        raise AdapterError('Expected one current verified native QA job')
    if source_at_revision(head) != source_sha():
        raise AdapterError('Running QA source differs from immutable GitHub source')
    if admission:
        if workflow_definition(allow_missing=True) is not None:
            raise AdapterError('Production workflow already owns publisher allowance')
        verify_research_predecessor(_RESEARCH_SEED)
    decision = verify_native_grant(current)
    return {'id': run_id, 'run_attempt': 1, 'head_sha': head,
            'workflow_id': workflow_id, 'decision': decision}


def qa_authority():
    current = qa_workflow_binding()
    seed = validate(copy.deepcopy(_RESEARCH_SEED))
    if seed['blocked'] or seed['in_flight'] or seed['physical_pending']:
        raise AdapterError('Reviewed predecessor still has an unresolved restriction')
    return {
        'kind': 'qa', 'sha256': source_sha(), 'current': current,
        'decision': current['decision'], 'lineage': _RESEARCH_STATE_SHA,
    }, seed


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
        if file is None:
            files[name] = None
            continue
        expected = file.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40}", expected):
            raise AdapterError(
                "Invalid existing source blob identity", "publish"
            )
        if file.get("encoding") != "base64":
            file = github("GET", "/git/blobs/" + expected)
        if file.get("encoding") != "base64" or file.get("sha") != expected:
            raise AdapterError("Unsupported existing source blob", "publish")
        try:
            raw = base64.b64decode(
                re.sub(r"\s+", "", file["content"]), validate=True
            )
            actual = hashlib.sha1(
                b"blob " + str(len(raw)).encode() + b"\0" + raw
            ).hexdigest()
            if actual != expected:
                raise ValueError("Source blob digest mismatch")
            files[name] = raw.decode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise AdapterError(
                "Invalid existing source blob bytes", "publish"
            ) from None
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

def hosted_authority():
    current = authenticated_hosted_run()
    owner = exact_predecessor(current)
    retained = restore_hosted_state(owner)
    if retained['blocked'] or retained['in_flight'] or retained['physical_pending']:
        raise AdapterError('Newest owned publisher state is unresolved')
    stamp = now()
    retained['observed_at'] = max(retained['observed_at'], stamp)
    retained['not_before'] = max(retained['not_before'], stamp + 60)
    return {
        'kind': 'hosted', 'sha256': source_sha(), 'current': current,
        'owner': owner, 'decision': _HOSTED_DECISION,
        'lineage': digest(canonical(retained)),
    }, retained


def parse_aware(value):
    if not isinstance(value, str):
        raise ValueError('Expected an offset-aware CERN timestamp')
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError('Expected an offset-aware CERN timestamp')
    return stamp


def validate_records(records, *, published=False):
    if type(records) is not list or len(records) > len(PROGRAMMES) or (not records and not published):
        raise ValueError('Unexpected CERN record cardinality')
    programmes = set()
    cycles = set()
    destinations = set()
    for row in records:
        if type(row) is not dict or not isinstance(row.get('id'), str):
            raise ValueError('Missing CERN record identity')
        identity_match = re.fullmatch(r'cern-summer-studentships-(summ|sum-openlab)-(20[0-9]{2})', row['id'])
        title_match = TITLE.fullmatch(row.get('title') or '')
        if identity_match is None or title_match is None:
            raise ValueError('Malformed CERN title or native identity')
        programme, cycle = identity_match[1].upper(), identity_match[2]
        if (programme in programmes or row.get('source') != SOURCE_ID or row.get('source_url') != ENTRY
                or not official_detail_url(row.get('url'), programme) or row['url'] in destinations
                or not published and row['url'] not in DETAILS
                or title_match[1].casefold() != PROGRAMMES[programme].casefold() or title_match[2] != cycle
                or row.get('host_countries') != ['CH'] or row.get('eligible_countries') != []
                or row.get('publisher_country') != 'CH' or row.get('status') not in ('open', 'expired')
                or row.get('category') != 'internships' or row.get('categories') != ['internships']
                or row.get('attribution') != ATTRIBUTION or row.get('location') != 'Geneva, Switzerland'
                or not isinstance(row.get('summary'), str) or not 1 <= len(row['summary']) <= 600):
            raise ValueError('Unexpected CERN record facts or scope')
        programmes.add(programme)
        cycles.add(cycle)
        destinations.add(row['url'])
        start = date.fromisoformat(row.get('start_date') or '')
        if start.year != int(cycle) or parse_aware(row['deadline']).date() >= start:
            raise ValueError('CERN dates disagree with the native cycle')
        for field in ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at'):
            parse_aware(row[field])
    if len(cycles) > 1 or not published and programmes != PROGRAMMES.keys():
        raise ValueError('Incomplete or mixed-cycle CERN collection')


def remove_expired_records(records, checked_at):
    checked = parse_aware(checked_at)
    return [row for row in records if checked < parse_aware(row['deadline'])]


def extract_records(document, details, checked):
    candidates = parse_inventory(document)
    if {candidate.url for candidate in candidates} != set(DETAILS) or set(details) != set(DETAILS):
        raise ValueError('The links differ from the reviewed CERN detail frontier')
    records = [parse_detail(details[candidate.url], candidate, checked) for candidate in candidates]
    validate_records(records)
    return records


_HOSTED_EXPORT = None


def verify_before_request(state):
    """Refresh workflow proof after the initial wait and before the socket."""
    authority = _ACTIVE_AUTHORITY
    if authority['kind'] == 'qa':
        if qa_workflow_binding() != authority['current']:
            raise AdapterError('QA workflow ownership changed before access')
    else:
        current = authenticated_hosted_run()
        if current != authority['current'] or exact_predecessor(current) != authority['owner']:
            raise AdapterError('Initial hosted ownership changed before access')
    if now() < max(state['not_before'], state['rate_limit_until']):
        raise AdapterError('Publisher time regressed during workflow verification')



def collect(*, publishing=False):
    """Collect complete current CERN inventory and both official detail pages."""
    global _ACTIVE_AUTHORITY, _HOSTED_EXPORT
    if GATE_FAILED or AUDIT['requests'] or _ACTIVE_AUTHORITY is not None:
        raise AdapterError('Collection cannot reenter publisher authority')
    authorize = hosted_authority if publishing else qa_authority
    authority, retained = authorize()
    _ACTIVE_AUTHORITY = authority
    expected = create_hosted_namespace(retained)
    error = None
    records = None
    with Journal(expected) as journal:
        start = journal.read()
        if start['blocked'] or start['in_flight'] or start['physical_pending']:
            raise AdapterError('Original publisher journal is unresolved')
        journal.capture_claim = create_once(journal.directory, 'source-collection.claim', {
            'schema': 1, 'source_sha256': source_sha(),
            'initial_state_sha256': digest(journal.ack),
            'decision': authority['decision'],
        })
        stamp = now()
        start['observed_at'] = max(start['observed_at'], stamp)
        start['not_before'] = max(start['not_before'], stamp + 60)
        journal.save(cutoff_state(start))
        try:
            request(journal, ROBOTS)
            document = request(journal, ENTRY)
            candidates = parse_inventory(document)
            if {candidate.url for candidate in candidates} != set(DETAILS):
                raise AdapterError('CERN detail links changed; review the current frontier')
            details = {url: request(journal, url) for url in DETAILS}
            records = extract_records(document, details, datetime.now(timezone.utc))
        except Exception as exc:
            error = exc
        final = journal.read()
    _HOSTED_EXPORT = (authority, expected, final, journal.capture_claim)
    if error is not None:
        raise error
    return records


def metadata(attempt, previous, records, *, checked_at, failed=False):
    return {
        'source': SOURCE_ID, 'display_name': DISPLAY_NAME,
        'source_url': SOURCE_URL, 'website_url': 'https://careers.cern/',
        'language': 'en', 'publisher_type': 'organization', 'publisher_country': 'CH',
        'attribution': ATTRIBUTION, 'status': 'fail' if failed else 'success',
        'record_count': len(records), 'last_attempt_at': attempt,
        'last_success_at': previous.get('last_success_at') if failed else checked_at,
        'last_checked_at': previous.get('last_checked_at') if failed else checked_at,
        'message': ('Collection failed. The last successful dataset is retained.' if failed
                    else 'Collected and checked the official CERN summer studentship calls'),
        'failure_stage': 'collection' if failed else None,
        'error': 'Source could not produce a validated programme announcement' if failed else None,
    }


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + '\n'


def publish():
    """Replace the source pair atomically, or retain valid prior records."""
    attempt = safe_clock()
    snapshot = read_snapshot()
    old = json.loads(snapshot['files']['data.json'] or '[]')
    previous = json.loads(snapshot['files']['metadata.json'] or '{}')
    validate_records(old, published=True)
    if previous and (previous.get('source') != SOURCE_ID or previous.get('source_url') != SOURCE_URL):
        raise AdapterError('Unexpected prior source identity', 'publish')
    try:
        records = collect(publishing=True)
        prior_records = {row['id']: row for row in old}
        transient = ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at')
        for row in records:
            prior = prior_records.get(row['id'])
            if prior is None:
                continue
            row['created_at'] = prior['created_at']
            row['first_seen_at'] = prior['first_seen_at']
            if all(prior.get(key) == value for key, value in row.items() if key not in transient):
                row['updated_at'] = prior['updated_at']
        checked_at = safe_clock()
        records = remove_expired_records(records, checked_at)
        validate_records(records, published=True)
    except Exception:
        if snapshot['files']['metadata.json'] is not None:
            latest = read_snapshot()
            if latest['files'] == snapshot['files']:
                publish_files(latest, {'metadata.json': json_text(
                    metadata(attempt, previous, old, checked_at=attempt, failed=True)
                )})
        raise
    publish_files(snapshot, {
        'data.json': json_text(records),
        'metadata.json': json_text(metadata(attempt, previous, records, checked_at=checked_at)),
    })


def retained_result():
    """Read the exact terminal acknowledged state under its original custody."""
    if _HOSTED_EXPORT is None:
        return
    authority, expected, retained, claim = _HOSTED_EXPORT
    current = authority['current']
    if (qa_workflow_binding(admission=False) if authority['kind'] == 'qa'
            else authenticated_hosted_run()) != current:
        raise AdapterError('Export ownership changed')
    with Journal(expected) as journal:
        if journal.ack != canonical(retained):
            raise AdapterError('Acknowledged retained state changed')
        raw, inode = claim
        if (identity(os.stat('source-collection.claim', dir_fd=journal.directory,
                             follow_symlinks=False)) != inode
                or read_private_claim(journal.directory, 'source-collection.claim') != raw):
            raise AdapterError('Exclusive collection claim changed')
    if (not AUDIT['persistence']
            or not AUDIT['persistence'][-1]['acknowledged']
            or AUDIT['persistence'][-1].get('actual_journal_bytes') != canonical(retained)):
        raise AdapterError('Terminal journal lacks its original acknowledgement')
    return current, retained


def export_pacing(owned=None):
    """Export closed acknowledged state for the exact owning run, even if blocked."""
    result = retained_result()
    if result is None:
        return
    if owned is not None and owned != result:
        raise AdapterError('Terminal ownership changed before export')
    current, retained = result
    root = Path(os.environ['RUNNER_TEMP'])
    target = Path(os.environ['CERN_PACING_ARTIFACT_PATH'])
    if root.resolve() != root or target != root / 'pacing-state.json':
        raise AdapterError('Unexpected pacing artifact path')
    envelope = {
        'schema': 1, 'repository': 'YouthOpps/data-pipeline', 'source': SOURCE_ID,
        'run_id': current['id'], 'run_attempt': current['run_attempt'],
        'head_sha': current['head_sha'], 'adapter_sha256': source_sha(),
        'state': validate(retained), 'state_sha256': digest(canonical(retained)),
    }
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_id = identity(os.fstat(parent))
    primary = None
    try:
        parent_safe(os.fstat(parent))
        if identity(root.lstat()) != parent_id:
            raise AdapterError('Pacing path replaced')
        contents, inode = create_once(parent, target.name, envelope)
        if (identity(root.lstat()) != parent_id or identity(target.lstat()) != inode
                or target.read_bytes() != contents):
            raise AdapterError('Pacing artifact changed')
    except BaseException as exc:
        primary = exc
        raise
    finally:
        checked_close([(parent, parent_id)], primary)
    output = Path(os.environ['GITHUB_OUTPUT'])
    fd = os.open(output, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    info = os.fstat(fd)
    output_id = identity(info)
    primary = None
    try:
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022
                or output.parent != root / '_runner_file_commands'
                or not output.name.startswith('set_output_')
                or identity(output.lstat()) != output_id):
            raise AdapterError('Workflow output changed')
        value = b'pacing_ready=true\n'
        if os.write(fd, value) != len(value):
            raise AdapterError('Short workflow output write')
        os.fsync(fd)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        checked_close([(fd, output_id)], primary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if not args.publish:
        print(json.dumps(collect(), ensure_ascii=False))
        return
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise AdapterError('Publication requires the owning Actions execution')
    if not approved_decision(_HOSTED_DECISION) or _HOSTED_LINEAGE is None or _HOSTED_SEED is None:
        raise AdapterError('Production activation is OFF')
    try:
        publish()
    finally:
        export_pacing()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        phases = [{'phase': item['phase'], 'response_status': item.get('status'),
                   'body_complete': item.get('body_complete', False), 'closed': item.get('closed', False),
                   'settled': item.get('settled_sha256') is not None} for item in AUDIT['requests']]
        print('CERN summer studentship collection or publication failed', file=sys.stderr)
        print(json.dumps({'publisher_phases': phases}), file=sys.stderr)
        sys.exit(1)
