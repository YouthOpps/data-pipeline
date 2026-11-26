"""Standalone factual index of the shared Yıldız/pladis Workday careers site."""
from html.parser import HTMLParser
import datetime
import contextlib
import signal
import threading
import email.utils
import fcntl
import html
import os
import pathlib
import stat
import tempfile
import time
import urllib.error
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as etree

import base64
import copy
import hashlib
import json
import math
import re
import urllib.parse
import io
import ipaddress
import sys
import zipfile

FAMILY = 'research-yildiz-pladis'
SOURCE = 'yildiz-pladis-careers'
ORIGIN = 'https://pladis.wd3.myworkdayjobs.com'
SITEMAP = ORIGIN + '/pladis_Careers/siteMap.xml'
HELD_SHA256 = 'a9d3c5bf2d02207ec5b7ed96334ad8c8f4c2ab173d3604b8b13634e8c97704cd'
BODY_SHA256 = '3cdc549d2ae80cc100900700437f14d0e7e6ea00121ddcd8f41add02e6919a33'
SCOPE_DECISION = {
    'schema':
        1,
    'source':
        'yildiz-pladis-careers',
    'family':
        'research-yildiz-pladis',
    'revision':
        'pladis379-retained-corporate-origin-v1',
    'held_sha256':
        'a9d3c5bf2d02207ec5b7ed96334ad8c8f4c2ab173d3604b8b13634e8c97704cd',
    'body_sha256':
        '3cdc549d2ae80cc100900700437f14d0e7e6ea00121ddcd8f41add02e6919a33',
    'denied_origins': [
        'https://pladisglobal.com', 'https://www.pladisglobal.com'
    ],
    'service_origin':
        'https://pladis.wd3.myworkdayjobs.com',
    'service_site':
        'pladis_Careers',
    'evidence':
        'https://github.com/YouthOpps/data-pipeline/issues/379#issuecomment-6942223770'
}
NUMERIC = ('completed', 'inventory_completed', 'interval', 'page_interval',
           'strikes', 'until')
ORIGINAL_KEYS = {
    'family', 'starts', *NUMERIC, 'pending', 'blocked', 'legacy_history',
    'hold_evidence', 'research_only', 'native_adapter_acceptance'
}
STATE_KEYS = ORIGINAL_KEYS | {
    'schema', 'retained_scope', 'events', 'observed_at'
}


class ScopeError(ValueError):
    """Unreviewed or unresolved evidence must not authorize requests."""


def require(condition, message):
    if not condition:
        raise ScopeError(message)


def failure_details(error):
    """Expose authored validation messages, never external exception payloads."""
    kind = type(error).__name__
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', kind) is None:
        kind = 'Exception'
    message = 'External exception details withheld'
    if type(error) is ScopeError:
        value = str(error)
        secrets = [
            os.environ.get(name, '')
            for name in ('GITHUB_TOKEN', 'DATA_SOURCE_TOKEN', 'GH_TOKEN')
        ]
        if (re.fullmatch(r'[A-Za-z][A-Za-z0-9 /;.,()-]{0,239}', value) and
                not any(secret and secret in value for secret in secrets)):
            message = value
        else:
            message = 'Validation details withheld'
    return {'type': kind, 'message': message}


def report_failure(stage, error, transport=None):
    """One bounded JSON stderr line; stdout remains the native result only."""
    report = {
        'event': 'refresh_failed',
        'stage': stage,
        **failure_details(error)
    }
    if transport is not None:
        report['request'] = getattr(transport, 'diagnostic', None)
    print(json.dumps(report, ensure_ascii=True), file=sys.stderr, flush=True)


def finite(value):
    return type(value) in (float, int) and math.isfinite(value) and value >= 0


def decode_original(raw):
    require(type(raw) is bytes and len(raw) <= 16384, 'Invalid held bytes')
    require(
        hashlib.sha256(raw).hexdigest() == HELD_SHA256,
        'Unreviewed held journal')
    state = json.loads(raw)
    require(
        set(state) == ORIGINAL_KEYS and state['family'] == FAMILY,
        'Original family/provenance mismatch')
    require(state['blocked'] is True and state['pending'] is False,
            'Original refusal is not settled')
    require(state['legacy_history'] == 'unknown', 'Unknown history lost')
    require(
        len(state['starts']) == 15 and all(map(finite, state['starts'])),
        'Original request history changed')
    return state


def reviewed_decision():
    expected = {
        'schema': 1,
        'source': SOURCE,
        'family': FAMILY,
        'revision': 'pladis379-retained-corporate-origin-v1',
        'held_sha256': HELD_SHA256,
        'body_sha256': BODY_SHA256,
        'denied_origins': [
            'https://pladisglobal.com', 'https://www.pladisglobal.com'
        ],
        'service_origin': ORIGIN,
        'service_site': 'pladis_Careers'
    }
    require(type(SCOPE_DECISION) is dict, 'Scope decision remains OFF')
    candidate = copy.deepcopy(SCOPE_DECISION)
    evidence = candidate.pop('evidence', '')
    require(candidate == expected, 'Changed scope decision')
    require(
        re.fullmatch(
            r'https://github\.com/YouthOpps/data-pipeline/issues/379'
            r'#issuecomment-[1-9][0-9]*', evidence) is not None,
        'Missing public independent decision evidence')
    return copy.deepcopy(SCOPE_DECISION)


def original_event(original):
    return {
        'kind':
            'retained_corporate_refusal',
        'url':
            'https://www.pladisglobal.com/working-pladis',
        'status':
            403,
        'body_sha256':
            BODY_SHA256,
        'started':
            original['starts'][-1],
        'completed':
            original['completed'],
        'source_revision':
            '9f40bc992db8536bd9cd15d7ff60378535759e663df0ae557504b8b42ed5a203'
    }


def migrate(raw):
    """Pure migration proposal: keeps all original bytes and unknown flags."""
    decision = reviewed_decision()
    original = decode_original(raw)
    state = copy.deepcopy(original)
    state.update(schema=1,
                 observed_at=original['completed'],
                 events=[],
                 retained_scope={
                     'decision': decision,
                     'original_base64': base64.b64encode(raw).decode('ascii'),
                     'event': original_event(original)
                 })
    validate(state)
    return state


def validate(state):
    require(
        type(state) is dict and set(state) == STATE_KEYS,
        'Unknown state schema/fields')
    require(state['schema'] == 1 and state['family'] == FAMILY,
            'Wrong source state')
    scope = state['retained_scope']
    require(
        type(scope) is dict and
        set(scope) == {'decision', 'original_base64', 'event'},
        'Missing refusal provenance')
    require(scope['decision'] == reviewed_decision(), 'Unreviewed scope')
    try:
        original = decode_original(
            base64.b64decode(scope['original_base64'], validate=True))
    except (ValueError, TypeError) as error:
        raise ScopeError('Invalid retained original bytes') from error
    require(scope['event'] == original_event(original), 'Refusal event changed')
    require(state['blocked'] is True and type(state['pending']) is bool,
            'Original block erased or unknown pending')
    for key in ('legacy_history', 'hold_evidence', 'research_only',
                'native_adapter_acceptance'):
        require(state[key] == original[key], 'Inherited provenance changed')
    require(finite(state['observed_at']), 'Unsupported observation clock')
    for key in NUMERIC:
        require(
            finite(state[key]) and state[key] >= original[key],
            'Lost retained MAX restriction: ' + key)
    require(
        type(state['strikes']) is int and state['strikes'] <= 40,
        'Unsupported strike counter')
    require(
        type(state['starts']) is list and all(
            finite(value) and value <= state['observed_at']
            for value in state['starts']), 'Invalid physical history')
    require(
        set(original['starts']) <= set(state['starts']),
        'Original physical history discarded')
    require(type(state['events']) is list, 'Unknown event history')
    for event in state['events']:
        require(
            type(event) is dict and
            set(event) == {'kind', 'url', 'status', 'observed_at'},
            'Unresolved event schema')
        require(
            event['kind'] in {
                'content_refusal', 'visible_refusal', 'robots_disallow',
                'unattributed_hold'
            }, 'Unknown restrictive event')
        require(
            type(event['url']) is str and type(event['status']) is int and
            0 <= event['status'] <= 599 and finite(event['observed_at']) and
            event['observed_at'] <= state['observed_at'],
            'Invalid restrictive event')
    return state


def admit(state, url, method='GET'):
    """Only literal observed public Workday routes; corporate remains denied."""
    validate(state)
    require(not state['pending'], 'Unresolved physical transaction')
    require(not state['events'], 'New or unattributed refusal remains global')
    require(type(url) is str and method == 'GET', 'Only public GET admitted')
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        raise ScopeError('Malformed service URL') from None
    require(
        parsed.scheme == 'https' and
        parsed.netloc == 'pladis.wd3.myworkdayjobs.com',
        'Unadmitted service/origin alias')
    require(
        not parsed.query and not parsed.fragment and '%' not in parsed.path and
        '\\' not in parsed.path and ';' not in parsed.path and
        not any(part in {'.', '..'} for part in parsed.path.split('/')),
        'Ambiguous route identity')
    require(
        parsed.path in {'/robots.txt', '/pladis_Careers/siteMap.xml'} or
        re.fullmatch(
            r'/pladis_Careers/job/(?:[A-Za-z0-9-]+/)?'
            r'[A-Za-z0-9_-]+_JR[0-9]+(?:-[0-9]+)?', parsed.path) is not None,
        'Outside observed public service inventory')


def retain_refusal(state, kind, url, status, observed_at):
    """Every future refusal is global, never covered by the old decision."""
    validate(state)
    require(
        finite(observed_at) and observed_at >= state['observed_at'],
        'Clock moved backwards')
    state['observed_at'] = observed_at
    event = dict(kind=kind, url=url, status=status, observed_at=observed_at)
    state['events'].append(event)
    validate(state)


def merge(left, right):
    """Union scoped events, preserve MAX restrictions and pending barriers."""
    validate(left)
    validate(right)
    require(left['retained_scope'] == right['retained_scope'],
            'Conflicting scope provenance')
    result = copy.deepcopy(left)
    for key in (*NUMERIC, 'observed_at'):
        result[key] = max(left[key], right[key])
    result['starts'] = sorted(set(left['starts']) | set(right['starts']))
    result['pending'] = left['pending'] or right['pending']
    result['events'] = [
        json.loads(raw) for raw in sorted({
            json.dumps(event, sort_keys=True)
            for event in left['events'] + right['events']
        })
    ]
    return validate(result)


class PrivateJournal:
    """Identity-bound private journal draft; disposable-directory controls only."""

    def __init__(self, directory):
        self.os = os
        self.stat = stat
        self.directory = directory
        self.failed = False
        self.parent = os.open(directory, os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fd = None
        try:
            self.parent_identity = self.identity(os.fstat(self.parent), True)
            self.fd = os.open('state.json',
                              os.O_RDWR | os.O_NOFOLLOW,
                              dir_fd=self.parent)
            self.file_identity = self.identity(os.fstat(self.fd), False)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.acknowledged = self.read_raw()
        except BaseException as error:
            if self.fd is not None:
                os.close(self.fd)
            os.close(self.parent)
            if isinstance(error, BlockingIOError):
                raise ScopeError('Publisher journal already owned') from error
            raise

    def identity(self, value, directory):
        expected_mode = 0o700 if directory else 0o600
        require(
            value.st_uid == self.os.getuid() and
            self.stat.S_IMODE(value.st_mode) == expected_mode,
            'Unsafe journal ownership/mode')
        require(
            self.stat.S_ISDIR(value.st_mode) if directory else
            self.stat.S_ISREG(value.st_mode) and value.st_nlink == 1,
            'Unsafe journal type/link count')
        return value.st_dev, value.st_ino

    def authority(self):
        require(not self.failed, 'Journal failure latched')
        require(
            self.identity(self.os.fstat(self.parent),
                          True) == self.parent_identity,
            'Original parent changed')
        require(
            self.identity(self.os.stat(self.directory, follow_symlinks=False),
                          True) == self.parent_identity,
            'Parent pathname replaced')
        require(
            self.identity(self.os.fstat(self.fd), False) == self.file_identity,
            'Original journal changed')
        require(
            self.identity(
                self.os.stat('state.json',
                             dir_fd=self.parent,
                             follow_symlinks=False),
                False) == self.file_identity, 'Journal pathname replaced')
        info = self.os.fstat(self.fd)
        expected = (f"{self.os.major(info.st_dev):x}:"
                    f"{self.os.minor(info.st_dev):x}:{info.st_ino}")
        locks = pathlib.Path(f'/proc/self/fdinfo/{self.fd}').read_text()
        owned = False
        for line in locks.splitlines():
            fields = line.split()
            if len(fields) != 9 or fields[0] != 'lock:':
                continue
            device, minor, inode = fields[6].split(':')
            identity = f'{int(device, 16):x}:{int(minor, 16):x}:{int(inode)}'
            if (fields[2:6] == ['FLOCK', 'ADVISORY', 'WRITE',
                                str(os.getpid())] and identity == expected and
                    fields[7:] == ['0', 'EOF']):
                owned = True
        require(owned, 'Original exclusive journal lock is not held')

    def read_raw(self):
        self.authority()
        size = self.os.fstat(self.fd).st_size
        require(0 < size <= 1048576, 'Missing/corrupt journal')
        raw = self.os.pread(self.fd, size + 1, 0)
        require(len(raw) == size, 'Unstable journal')
        self.authority()
        return raw

    def load(self):
        raw = self.read_raw()
        require(raw == self.acknowledged, 'Unacknowledged journal mutation')
        return validate(json.loads(raw))

    def save(self, state):
        """Never recover an interrupted write using an old settled snapshot."""
        validate(state)
        raw = json.dumps(state, sort_keys=True, separators=(',', ':')).encode()
        require(len(raw) <= 1048576, 'Journal exceeds defensive storage bound')
        try:
            self.authority()
            require(self.read_raw() == self.acknowledged,
                    'Unacknowledged journal mutation')
            written = self.os.pwrite(self.fd, raw, 0)
            require(written == len(raw), 'Short journal write')
            self.os.ftruncate(self.fd, len(raw))
            self.os.fsync(self.fd)
            self.authority()
            require(self.read_raw() == raw, 'Journal acknowledgement mismatch')
            self.acknowledged = raw
        except BaseException:
            # Only invalidate the verified descriptor while identity is safe.
            # No export path exists in this draft; runtime integration must
            # apply the same ownership checks to its owned export descriptor.
            try:
                self.authority()
                self.os.ftruncate(self.fd, 0)
                self.os.fsync(self.fd)
            finally:
                self.failed = True
            raise

    def close(self):
        self.os.close(self.fd)
        self.os.close(self.parent)


USER_AGENT = 'YouthOppsBot/1.0 (+https://youthopps.org/bot/)'
_OWNED_ALARM = None
_LOCAL_COORDINATION_GUARD = None


class Transport:
    """Scoped physical transport with one nonrenewable 50-minute deadline."""

    def __init__(self, journal):
        self.time = time
        self.journal = journal
        self.startup_until = time.monotonic() + 60
        self.deadline = time.monotonic() + 3000
        self.deadline_interrupted = False
        self.completed_monotonic = None
        self.inventory_monotonic = None
        self.failed = False
        self.robots = None
        self.authority = None

        class NoRedirect(urllib.request.HTTPRedirectHandler):

            def redirect_request(self, *args):
                return None

        self.opener = urllib.request.build_opener(NoRedirect)

    def remaining(self):
        remaining = self.deadline - self.time.monotonic()
        require(remaining > 0, 'Absolute collection deadline exhausted')
        return remaining

    @contextlib.contextmanager
    def deadline_guard(self):
        """Compose only our live alarm, retaining its earliest fixed deadline."""
        global _OWNED_ALARM
        require(threading.current_thread() is threading.main_thread(),
                'Absolute I/O deadline requires the native main thread')
        self.remaining()
        require(not self.deadline_interrupted,
                'Interrupted deadline is not reusable')
        owner = _OWNED_ALARM
        root = owner is None
        if root:
            require(
                signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
                'An unrelated process alarm is already active')
            previous = signal.getsignal(signal.SIGALRM)
            owner = {
                'deadline': self.deadline,
                'cancelling': False,
                'participants': [],
                'handler': None
            }

            def expired(_number, _frame):
                for participant in owner['participants']:
                    participant.deadline_interrupted = True
                    participant.failed = True
                if owner['cancelling']:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    raise ScopeError('Deadline cancellation grace exhausted')
                owner['cancelling'] = True
                signal.setitimer(signal.ITIMER_REAL, 0.25)
                raise ScopeError('Absolute deadline interrupted blocking I/O')

            owner['handler'] = expired
            _OWNED_ALARM = owner
            signal.signal(signal.SIGALRM, expired)
        else:
            remaining, interval = signal.getitimer(signal.ITIMER_REAL)
            require(
                not owner['cancelling'] and remaining > 0 and interval == 0 and
                signal.getsignal(signal.SIGALRM) is owner['handler'] and
                abs(time.monotonic() + remaining - owner['deadline']) < 0.01,
                'Existing alarm is not the live owned deadline')
        owner['participants'].append(self)
        earlier = min(owner['deadline'], self.deadline)
        try:
            if root or earlier < owner['deadline']:
                owner['deadline'] = earlier
                require(earlier > time.monotonic(), 'Owned deadline exhausted')
                signal.setitimer(signal.ITIMER_REAL, earlier - time.monotonic())
            yield
        finally:
            owner['participants'].pop()
            if root:
                # Only the root restores the process alarm. Nested exits never
                # cancel it, extend it, or restart the cancellation grace.
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)
                _OWNED_ALARM = None

    def check(self):
        require(not self.failed and not self.journal.failed,
                'Transport persistence failure latched')
        self.remaining()
        self.journal.authority()
        require(callable(self.authority),
                'Collection coordination unimplemented')
        self.authority()

    def pacing_delay(self, state, inventory):
        target = max(state['until'], state['completed'] + state['interval'])
        if len(state['starts']) >= 5:
            target = max(target, sorted(state['starts'])[-5] + 60.001)
        if inventory:
            target = max(target,
                         state['inventory_completed'] + state['page_interval'])
        monotonic_target = self.startup_until
        if self.completed_monotonic is not None:
            monotonic_target = max(monotonic_target,
                                   self.completed_monotonic + state['interval'])
        if inventory and self.inventory_monotonic is not None:
            monotonic_target = max(
                monotonic_target,
                self.inventory_monotonic + state['page_interval'])
        return max(target - self.time.time(),
                   monotonic_target - self.time.monotonic(), 0)

    def wait(self, state, inventory):
        while True:
            self.check()
            delay = self.pacing_delay(state, inventory)
            if delay <= 0:
                return
            remaining = self.remaining()
            require(delay < remaining,
                    'Pacing exceeds absolute collection deadline')
            self.time.sleep(min(delay, remaining, 60))

    def prewire(self, prior, inventory):
        """Recheck at the physical boundary independently of the wait helper."""
        self.check()
        require(self.time.monotonic() >= self.startup_until,
                'Startup monotonic quiet is incomplete')
        require(
            self.pacing_delay(prior, inventory) <= 0,
            'Physical completion/rolling/inventory floor is incomplete')

    @staticmethod
    def header_values(headers, name):
        return [
            value.strip()
            for key, value in headers.items()
            if key.lower() == name.lower()
        ]

    def body_length(self, headers):
        lengths = self.header_values(headers, 'Content-Length')
        transfers = self.header_values(headers, 'Transfer-Encoding')
        encodings = self.header_values(headers, 'Content-Encoding')
        require(
            len(lengths) <= 1 and len(transfers) <= 1 and
            not (lengths and transfers), 'Ambiguous response framing')
        require(not transfers or transfers == ['chunked'],
                'Unsupported transfer encoding')
        require(not encodings or encodings == ['identity'],
                'Unsupported content encoding')
        if not lengths:
            return None
        require(
            re.fullmatch('[0-9]+', lengths[0]) is not None and
            int(lengths[0]) <= 4194304, 'Invalid/oversized Content-Length')
        return int(lengths[0])

    def request(self, url):
        self.check()
        state = self.journal.load()
        admit(state, url)
        self.diagnostic = {
            'url_sha256': digest(url.encode()),
            'status': None,
            'body_sha256': None
        }
        native_id = re.search(r'_(JR[0-9]+(?:-[0-9]+)?)$', url)
        if native_id and len(native_id.group(1)) <= 80:
            self.diagnostic['native_id'] = native_id.group(1)
        robots = url == ORIGIN + '/robots.txt'
        inventory = url == SITEMAP
        prior = copy.deepcopy(state)
        # A cached policy decision owns a durable pending transaction too;
        # it does not invent an extra physical start or completion.
        state['pending'] = True
        state['observed_at'] = max(state['observed_at'], self.time.time())
        self.journal.save(state)
        assessed = closed = attempted = False
        try:
            with self.deadline_guard():
                if not robots:
                    require(self.robots is not None, 'Robots not assessed')
                    permitted = self.robots.can_fetch('YouthOppsBot', url)
                    require(
                        type(permitted) is bool,
                        'Unassessed cached robots policy')
                    if not permitted:
                        retain_refusal(state, 'robots_disallow', url, 0,
                                       self.time.time())
                        self.journal.save(state)
                        state['pending'] = False
                        self.journal.save(state)
                        raise ScopeError('Retained robots denial')
                self.wait(prior, inventory)
                started = self.time.time()
                state['observed_at'] = started
                original = decode_original(
                    base64.b64decode(state['retained_scope']['original_base64'],
                                     validate=True))
                state['starts'] = sorted(
                    set(original['starts']) | {
                        value for value in state['starts']
                        if value >= started - 60
                    })
                state['starts'].append(started)
                self.journal.save(state)
                request = urllib.request.Request(
                    url,
                    headers={
                        'User-Agent': USER_AGENT,
                        'Accept-Encoding': 'identity'
                    })
                require(
                    request.get_header('User-agent') == USER_AGENT,
                    'Wrong outbound identity')
                self.prewire(prior, inventory)
                timeout = min(45, self.remaining())
                attempted = True
                try:
                    response = self.opener.open(request, timeout=timeout)
                except urllib.error.HTTPError as error:
                    response = error

                class Closure:

                    def __enter__(self):
                        return response.__enter__()

                    def __exit__(self, *args):
                        nonlocal closed
                        result = response.__exit__(*args)
                        closed = True
                        return result

                with Closure():
                    status = response.status
                    self.diagnostic['status'] = status if type(
                        status) is int else None
                    now = self.time.time()
                    state['observed_at'] = now
                    if status == 429:
                        state['interval'] *= 2
                        state['page_interval'] *= 2
                        state['strikes'] += 1
                        state['until'] = max(
                            state['until'],
                            now + 1800 * 2**(state['strikes'] - 1))
                    elif status in (401, 403, 451) and not robots:
                        retain_refusal(state, 'content_refusal', url, status,
                                       now)
                    # Rate limits and status refusals are durable BEFORE body
                    # framing, decoding, challenge assessment or closure can fail.
                    self.journal.save(state)
                    unresolved_retry_after = False
                    for retry_after in self.header_values(
                            response.headers, 'Retry-After'):
                        try:
                            embargo = retry_after_deadline(retry_after, now)
                        except ScopeError:
                            unresolved_retry_after = True
                            continue
                        if embargo is not None:
                            state['until'] = max(state['until'], embargo)
                            # Retain every known MAX in either header order;
                            # an unknown positive value still blocks completion.
                            self.journal.save(state)
                    require(not unresolved_retry_after,
                            'Unrepresentable Retry-After remains pending')
                    declared = self.body_length(response.headers)
                    body = response.read(4194305)
                    require(
                        len(body) <= 4194304 and
                        (declared is None or len(body) == declared),
                        'Incomplete/oversized response remains pending')
                    self.diagnostic['body_sha256'] = digest(body)
                    text = body.decode('utf-8', errors='strict')
                    if visible_refusal(text,
                                       human_only=(robots and
                                                   400 <= status < 500 and
                                                   status != 429)):
                        retain_refusal(state, 'visible_refusal', url, status,
                                       self.time.time())
                        self.journal.save(state)
                        assessed = True
                        raise ScopeError('Visible publisher refusal retained')
                    if status == 429:
                        assessed = True
                        raise ScopeError(
                            'Retained 429 embargo; collection stopped')
                    if robots:
                        policy = urllib.robotparser.RobotFileParser()
                        if status == 200:
                            policy.parse(verified_rep(text, response.headers))
                            delay = policy.crawl_delay('YouthOppsBot')
                            rate = policy.request_rate('YouthOppsBot')
                            if delay:
                                state['interval'] = max(state['interval'],
                                                        delay)
                            if rate:
                                state['interval'] = max(
                                    state['interval'],
                                    rate.seconds / rate.requests)
                        elif 400 <= status < 500:
                            policy.parse(['User-agent: *', 'Allow: /'])
                        else:
                            raise ScopeError(
                                'Unreachable robots remains pending')
                        self.robots = policy
                    assessed = True
                    self.remaining()
                    if not robots and status != 200:
                        raise ScopeError(
                            'Content HTTP failure; no redirect/retry')
                    return status, text
        finally:
            if attempted:
                self.completed_monotonic = self.time.monotonic()
                state['completed'] = state['observed_at'] = self.time.time()
                if inventory:
                    self.inventory_monotonic = self.completed_monotonic
                    state['inventory_completed'] = state['completed']
                if assessed and closed and not self.deadline_interrupted:
                    state['pending'] = False
            if not self.journal.failed:
                try:
                    self.journal.save(state)
                except BaseException:
                    self.failed = True
                    raise
            if self.journal.failed or state['pending']:
                self.failed = True


def retry_after_deadline(value, now):
    """Preserve all valid embargoes; an unrepresentable positive value is a hold."""
    if re.fullmatch(r'[0-9]+', value):
        try:
            embargo = now + int(value)
            require(finite(embargo), 'Unrepresentable positive Retry-After')
            datetime.datetime.fromtimestamp(embargo, datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            raise ScopeError(
                'Unrepresentable positive Retry-After remains pending'
            ) from None
        return embargo
    try:
        date = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        return None
    require(date.utcoffset() is not None,
            'Ambiguous Retry-After date remains pending')
    try:
        embargo = date.timestamp()
        require(finite(embargo), 'Unsupported Retry-After date remains pending')
    except (ValueError, OverflowError, OSError):
        raise ScopeError(
            'Unsupported Retry-After date remains pending') from None
    return embargo


def verified_rep(text, headers):
    """Validate an actual REP document before interpreting absent restrictions."""
    types = Transport.header_values(headers, 'Content-Type')
    require(
        len(types) <= 1 and
        (not types or
         types[0].split(';', 1)[0].strip().lower() == 'text/plain'),
        'Robots 200 is not a supported plain-text REP response')
    lines = text.lstrip('\ufeff').splitlines()
    agent = False
    for raw in lines:
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z-]*)\s*:\s*(.*)', line)
        require(match is not None, 'Malformed robots document remains pending')
        name, value = match.group(1).lower(), match.group(2).strip()
        require(
            name in {
                'user-agent', 'allow', 'disallow', 'sitemap', 'crawl-delay',
                'request-rate'
            }, 'Unsupported robots directive remains pending')
        if name == 'user-agent':
            require(re.fullmatch(r'[A-Za-z0-9_*/.-]+', value),
                    'Invalid robots product group')
            agent = True
        elif name == 'sitemap':
            parsed = urllib.parse.urlsplit(value)
            require(
                parsed.scheme in ('http', 'https') and parsed.hostname and
                not parsed.username and not parsed.password,
                'Invalid robots sitemap directive')
        else:
            require(agent, 'Orphan robots rule remains pending')
            if name in ('allow', 'disallow'):
                require(not value or value.startswith('/'),
                        'Unsupported robots path rule')
            elif name == 'crawl-delay':
                require(re.fullmatch(r'[0-9]+', value),
                        'Unsupported robots crawl-delay remains pending')
            else:
                require(re.fullmatch(r'[1-9][0-9]*/[1-9][0-9]*', value),
                        'Unsupported robots request-rate remains pending')
    # A genuinely empty/comment-only REP body carries no rules. HTML, error
    # prose and malformed directives have already failed with pending retained.
    return lines


class Page(HTMLParser):
    """Extract actual JobPosting and visible primary text without scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.visible = []
        self.jobs = []
        self.script = None
        self.script_text = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'script':
            self.script = attrs.get('type') == 'application/ld+json'
            self.script_text = []
        hidden = (tag in {
            'script', 'style', 'template', 'nav', 'footer', 'aside', 'noscript'
        } or attrs.get('aria-hidden', '').lower() == 'true' or
                  'hidden' in attrs or bool(
                      re.search(
                          r'(?:display\s*:\s*none|visibility\s*:\s*hidden)',
                          attrs.get('style', ''), re.I)))
        if tag not in {
                'meta', 'link', 'br', 'img', 'input', 'hr', 'source', 'wbr',
                'area', 'base', 'embed', 'param', 'track'
        }:
            self.stack.append((tag, hidden or
                               bool(self.stack and self.stack[-1][1])))

    def handle_endtag(self, tag):
        if tag == 'script' and self.script:
            value = json.loads(''.join(self.script_text))
            candidates = value if isinstance(value, list) else [value]
            for candidate in candidates:
                if isinstance(candidate,
                              dict) and candidate.get('@type') == 'JobPosting':
                    self.jobs.append(candidate)
        if tag == 'script':
            self.script = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if self.script:
            self.script_text.append(text)
        if not self.stack or not self.stack[-1][1]:
            self.visible.append(text)


def visible_refusal(text, human_only=False):
    page = Page()
    page.feed(text)
    visible = ' '.join(' '.join(page.visible).split())
    human = r'\b(?:verify (?:that )?you are human|captcha verification|unusual traffic)\b'
    generic = r'\b(?:access denied|request blocked|automated access (?:is )?(?:denied|prohibited))\b'
    return bool(
        re.search(human if human_only else human + '|' + generic, visible,
                  re.I))


def sitemap_urls(text, state):
    require('<!DOCTYPE' not in text.upper(), 'Unexpected sitemap document type')
    root = etree.fromstring(text)
    require(root.tag == '{http://www.sitemaps.org/schemas/sitemap/0.9}urlset',
            'Sitemap format changed; no silent truncation')
    urls = []
    for position, item in enumerate(root, start=1):
        require(item.tag.endswith('}url'), 'Unexpected sitemap entry')
        locations = item.findall(
            '{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
        require(
            len(locations) == 1 and locations[0].text,
            'Missing/ambiguous sitemap URL')
        url = locations[0].text.strip()
        try:
            admit(state, url)
            require('/pladis_Careers/job/' in url, 'Non-job inventory entry')
            require(url not in urls, 'Duplicate canonical sitemap URL')
        except ScopeError as error:
            fingerprint = hashlib.sha256(url.encode('utf-8')).hexdigest()
            raise ScopeError(
                f'{error}; sitemap entry {position}, SHA256 {fingerprint}'
            ) from error
        urls.append(url)
    require(urls, 'Empty publisher inventory')
    return urls


COUNTRIES = {
    'Türkiye': 'TR',
    'Turkey': 'TR',
    'Netherlands': 'NL',
    'Saudi Arabia': 'SA',
    'United Kingdom': 'GB',
    'United States of America': 'US',
    'United States': 'US',
    'Belgium': 'BE',
    'France': 'FR',
    'Germany': 'DE',
    'Romania': 'RO',
    'Egypt': 'EG',
    'India': 'IN',
    'Canada': 'CA',
    'Australia': 'AU',
    'Ireland': 'IE',
    'United Arab Emirates': 'AE',
    'Kazakhstan': 'KZ',
    'Nigeria': 'NG',
    'Singapore': 'SG'
}


def youth_category(title, description):
    """Classify explicit entry-level titles, including evidenced Dutch stages."""
    if re.search(r'\b(?:senior|sr|head|director|manager|lead|supervisor)\b',
                 title, re.I):
        return None
    if re.search(r'\b(?:intern(?:ship)?|apprentice(?:ship)?|stajyer|staj)\b',
                 title, re.I):
        return 'internships'
    if re.search(r'\b(?:stag(?:i)?air(?:e)?|stage)\b', title, re.I):
        # "Stage" alone can refer to a production stage or a theatre role.
        # Require explicit student/placement facts in the actual job body.
        if re.search(
                r'\b(?:student(?:en)?|hbo|wo|mbo|opleiding|meewerkstage|stageplek)\b',
                description, re.I):
            return 'internships'
        return None
    if re.search(r'\b(?:graduate|trainee|young talent|new talent|junior|jr)\b',
                 title, re.I):
        return 'jobs'
    # Applicant requirements can establish entry-level access even when the
    # title does not. Never use company history or duties mentoring juniors.
    text = html.unescape(re.sub(r'<[^>]*>', ' ', description))
    text = re.sub(r'\s+', ' ', text).strip()
    headings = (r'what (?:do )?you bring\??|bring your experience|'
                r'(?:skills and |skills & )?(?:qualifications|requirements)'
                r'(?: required)?\s*:?|what we are looking for|'
                r'who (?:you are|should apply)|candidate profile')
    stop = (
        r'what we offer|our (?:benefits|values)|best mix of ingredients|'
        r'pladis is an equal|about (?:us|pladis)|key (?:duties|responsibilities)|'
        r'what will (?:be your|this role)|your responsibilities')
    evidence = (
        r'\b0\s*(?:[-–—]|to)\s*[12]\s+years?[’\']?\s+'
        r'(?:of\s+)?(?:relevant\s+|work\s+|professional\s+)?experience\b|'
        r'\bno\s+(?:prior\s+|previous\s+|work\s+)?experience\s+'
        r'(?:is\s+)?(?:required|necessary|needed)\b|'
        r'\b(?:recent|new)\s+graduates?\s+'
        r'(?:are\s+)?(?:welcome|eligible|encouraged to apply)\b')
    for heading in re.finditer(headings, text, re.I):
        section = re.split(stop, text[heading.end():], maxsplit=1,
                           flags=re.I)[0]
        for match in re.finditer(evidence, section, re.I):
            # Scope the assertion to the candidate, rejecting described staff,
            # training duties, quoted history and negated eligibility claims.
            prefix = re.split(r'[.!?;]', section[:match.start()])[-1]
            if re.search(
                    r'\b(?:not|never|cannot|can.t|isn.t|aren.t|mentor|mentoring|'
                    r'manage|managing|train|training|staff|employees|colleagues|'
                    r'team members|historically|previously|formerly)\b', prefix,
                    re.I):
                continue
            suffix = section[match.end():match.end() + 80]
            if re.match(r'\s*(?:is|are|was|were)?\s*(?:not|never)\b', suffix,
                        re.I):
                continue
            return 'jobs'
    return None


def parse_job(text, url, checked_at):
    page = Page()
    page.feed(text)
    require(len(page.jobs) == 1, 'Missing/ambiguous actual JobPosting')
    job = page.jobs[0]
    available = re.findall(r'\bpostingAvailable\s*:\s*(true|false)', text)
    require(len(available) == 1, 'Posting availability not established')
    identifier = job.get('identifier', {}).get('value')
    require(
        type(identifier) is str and
        re.fullmatch(r'JR[0-9]+(?:-[0-9]+)?', identifier),
        'Unsupported native job ID')
    require(
        url.rsplit('_', 1)[-1] == identifier or re.fullmatch(
            re.escape(identifier) + r'-[0-9]+',
            url.rsplit('_', 1)[-1]), 'Native ID/URL mismatch')
    title = html.unescape(job.get('title', '')).strip()
    employer = html.unescape(job.get('hiringOrganization', {}).get('name',
                                                                   '')).strip()
    require(title, 'Missing title fact')
    if available == ['false']:
        return identifier, None
    category = youth_category(title, job.get('description', ''))
    if category is None:
        return identifier, None
    locations = job.get('jobLocation')
    locations = locations if isinstance(locations, list) else [locations]
    countries, localities = set(), set()
    for location in locations:
        require(
            type(location) is dict and type(location.get('address')) is dict,
            'Missing job location')
        address = location['address']
        country = address.get('addressCountry')
        if type(country) is dict:
            country = country.get('name')
        require(country in COUNTRIES, 'Unmapped actual host country')
        countries.add(COUNTRIES[country])
        if address.get('addressLocality'):
            localities.add(address['addressLocality'])
    published = job.get('datePosted')
    require(type(published) is str, 'Missing posting date')
    datetime.date.fromisoformat(published)
    deadline = job.get('validThrough')
    if deadline is not None:
        require(type(deadline) is str, 'Invalid deadline')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', deadline):
            datetime.date.fromisoformat(deadline)
        else:
            require(
                datetime.datetime.fromisoformat(deadline.replace(
                    'Z', '+00:00')).tzinfo is not None,
                'Ambiguous deadline timezone')
    location_text = ', '.join(sorted(localities)) or ', '.join(
        sorted(countries))
    if employer:
        summary = f'{employer} lists {title} in {location_text}. Check the original vacancy for eligibility and application details.'
    else:
        summary = f'{title} in {location_text}, listed on the shared Yıldız Holding/pladis careers site. The hiring employer is not specified in the vacancy metadata; check the original vacancy for employer and eligibility details.'

    require(len(summary) <= 600, 'Factual summary exceeds consumer bound')
    return identifier, {
        'id': SOURCE + '-' + identifier,
        'source': SOURCE,
        'source_url': ORIGIN + '/pladis_Careers',
        'title': title,
        'url': url,
        'summary': summary,
        'summary_language': 'en',
        'organization': employer or None,
        'category': category,
        'categories': [category],
        'host_countries': sorted(countries),
        'eligible_countries': [],
        'tags': [],
        'deadline': deadline,
        'published_at': published,
        'status': 'open',
        **{
            key: checked_at for key in ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at')
        },
    }


def validate_records(records, allow_empty=False):
    require(
        type(records) is list and (records or allow_empty),
        'Empty/incomplete raw collection')
    identifiers = set()
    for record in records:
        identifier = record.get('id')
        require(
            type(identifier) is str and re.fullmatch(
                re.escape(SOURCE) + r'-JR[0-9]+(?:-[0-9]+)?', identifier),
            'Invalid stable final ID')
        require(identifier.lower() not in identifiers, 'Duplicate final ID')
        identifiers.add(identifier.lower())
        require(
            record.get('source') == SOURCE and
            record.get('source_url') == ORIGIN + '/pladis_Careers',
            'Record source mismatch')
        require(
            type(record.get('summary')) is str and
            0 < len(record['summary']) <= 600 and
            type(record.get('title')) is str and record['title'] and
            record.get('category') in {'internships', 'jobs'},
            'Invalid opportunity facts')
        url = urllib.parse.urlsplit(record.get('url', ''))
        require(
            url.scheme == 'https' and
            url.netloc == 'pladis.wd3.myworkdayjobs.com' and
            url.path.startswith('/pladis_Careers/job/') and not url.query and
            not url.fragment, 'Invalid actual opportunity URL')
        require(
            record.get('status') in {'open', 'expired', 'unknown'},
            'Invalid opportunity status')
        for key in ('host_countries', 'eligible_countries'):
            require(
                type(record.get(key)) is list and all(
                    type(code) is str and re.fullmatch('[A-Z]{2}', code)
                    for code in record[key]), 'Invalid country facts')
        for key in ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at',
                    'last_checked_at', 'published_at', 'deadline'):
            value = record[key]
            if value is None:
                require(key == 'deadline', 'Missing actual timestamp')
                continue
            require(type(value) is str, 'Invalid timestamp type')
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
                require(key in {'published_at', 'deadline'},
                        'Missing check time')
                datetime.date.fromisoformat(value)
            else:
                require(
                    datetime.datetime.fromisoformat(value.replace(
                        'Z', '+00:00')).tzinfo is not None,
                    'Timestamp missing timezone')
    return records


def expired(record, now):
    """Date-only deadlines close after the final possible local calendar day."""
    value = record.get('deadline')
    if value is None:
        return False
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        cutoff = datetime.datetime.combine(
            datetime.date.fromisoformat(value), datetime.time.max,
            datetime.timezone(datetime.timedelta(hours=-12)))
    else:
        cutoff = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    return cutoff < now


def successful_refresh(fresh, previous, now):
    """Complete fresh snapshot only; all previous expired items disappear."""
    validate_records(fresh)
    validate_records(previous, allow_empty=True)
    old = {record['id']: record for record in previous}
    result = copy.deepcopy(fresh)
    for record in result:
        if record['id'] in old:
            for field in ('created_at', 'first_seen_at'):
                record[field] = old[record['id']][field]
    result = [record for record in result if not expired(record, now)]
    return validate_records(result, allow_empty=True)


def collect_body(transport):
    """Read every advertised job, then reconcile inventory before returning."""
    transport.collection_stage = 'robots'
    transport.request(ORIGIN + '/robots.txt')
    transport.collection_stage = 'opening_inventory'
    _, inventory = transport.request(SITEMAP)
    urls = sitemap_urls(inventory, transport.journal.load())
    records = []
    observed = {}
    for url in urls:
        transport.collection_stage = 'detail_request'
        _, text = transport.request(url)
        transport.collection_stage = 'detail_parse'
        checked_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        identifier, record = parse_job(text, url, checked_at)
        # Multiple URLs with one native ID require publisher-level evidence;
        # never quietly drop a conflicting location or select an arbitrary URL.
        require(identifier.lower() not in observed,
                'Duplicate native ID needs evidence-based reconciliation')
        observed[identifier.lower()] = url
        if record is not None:
            records.append(record)
    transport.collection_stage = 'closing_inventory'
    _, closing = transport.request(SITEMAP)
    closing_urls = sitemap_urls(closing, transport.journal.load())
    require(
        set(closing_urls) == set(urls),
        'Inventory changed during complete scan; preserve last-good data')
    return validate_records(records)


LOCAL_SEED_MACHINE_SHA256 = '11463ef315e49ed08fa5dcf991991d21efa26dfe0b3fff9a3b5d75d93ec973ab'
LOCAL_STATE_DIRECTORY = '/workspace/work/pladis379-family-state'
LOCAL_ATTEMPT_CONTINUATION = {
    'schema':
        1,
    'attempt':
        4,
    'family':
        'research-yildiz-pladis',
    'revision':
        'pladis379-retained-attempt4-native-v1',
    'purpose':
        'native',
    'previous_state_sha256':
        '285fde3c5f98827eba000b31ada328184e02ff0ab5d90e281450c826ef7de1c2',
    'previous_claim_sha256':
        'a9afa29ab1ea9cf50f2f68810e939ec8705baeb317d3112972bd4e643d06b21e',
    'original_claim_sha256':
        '70ae56037238408bb2d65f703bef4fad3da15af1697a71f3c5be9c83e0c53416',
    'previous_source_sha256':
        'cabf6f643f21874d7cb39957cac45490d5f95174c89626e0144837273b129484',
    'previous_outcome':
        'full-native: outcome 0; semantic omissions subsequently fixed',
    'research_claim_sha256':
        'f1f334b5e7dc3d4bc51039635e95d1f36640edef654219c15c54a988e720c98e',
    'evidence':
        'https://github.com/YouthOpps/data-pipeline/issues/379#issuecomment-6945488909'
}
WORKFLOW_PATH = '.github/workflows/fetch-yildiz-pladis-careers.yml'
PIPELINE_API = 'https://api.github.com/repos/YouthOpps/data-pipeline'


def github_read(path):
    """Read-only infrastructure, explicitly separate from publisher access."""
    token = os.environ.get('GITHUB_TOKEN')
    require(type(token) is str and token, 'GitHub read token required')
    require((path == '' or path.startswith('/')) and '\\' not in path,
            'Unsafe GitHub API path')
    request = urllib.request.Request(
        PIPELINE_API + path,
        headers={
            'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent': USER_AGENT
        })

    class NoRedirect(urllib.request.HTTPRedirectHandler):

        def redirect_request(self, *args):
            return None

    guard = _LOCAL_COORDINATION_GUARD
    require(guard is not None, 'Local coordination requires a fixed phase')
    opener = urllib.request.build_opener(NoRedirect)
    with guard.deadline_guard():
        with opener.open(request, timeout=min(30,
                                              guard.remaining())) as response:
            require(response.status == 200, 'GitHub coordination lookup failed')
            raw = response.read(8388609)
            require(
                len(raw) <= 8388608, 'GitHub coordination response too large')
            return json.loads(raw)


def local_coordination(owner):
    """Bound all initial/recheck API calls together, including body and close."""
    global _LOCAL_COORDINATION_GUARD
    require(_LOCAL_COORDINATION_GUARD is None,
            'Local coordination already active')
    guard = Transport(None)
    guard.deadline = min(owner.deadline, time.monotonic() + 120)
    _LOCAL_COORDINATION_GUARD = guard
    try:
        with guard.deadline_guard():
            return verify_local_coordination()
    finally:
        _LOCAL_COORDINATION_GUARD = None


def verify_local_coordination():
    """Prove exact workflow absence or disabled state; no status stub."""
    repository = github_read('')
    require(
        repository.get('full_name') == 'YouthOpps/data-pipeline' and
        repository.get('default_branch') == 'main',
        'Wrong authenticated repository')
    branch = github_read('/branches/main')
    head = branch.get('commit', {}).get('sha')
    require(
        type(head) is str and re.fullmatch('[0-9a-f]{40}', head),
        'Invalid canonical main head')
    tree = github_read('/git/trees/' + head + '?recursive=1')
    require(
        tree.get('truncated') is False and type(tree.get('tree')) is list,
        'Incomplete canonical tree')
    matching_paths = [
        entry for entry in tree['tree'] if entry.get('path') == WORKFLOW_PATH
    ]
    workflows = []
    page = 1
    while True:
        result = github_read('/actions/workflows?per_page=100&page=' +
                             str(page))
        items = result.get('workflows')
        require(
            type(items) is list and type(result.get('total_count')) is int,
            'Invalid workflow inventory')
        workflows.extend(items)
        if len(workflows) >= result['total_count']:
            require(
                len(workflows) == result['total_count'],
                'Workflow inventory changed')
            break
        require(items, 'Truncated workflow pagination')
        page += 1
    matches = [item for item in workflows if item.get('path') == WORKFLOW_PATH]
    if matching_paths:
        require(
            len(matches) == 1, 'Missing/ambiguous existing workflow identity')
        workflow_id = matches[0].get('id')
        require(type(workflow_id) is int, 'Missing exact workflow ID')
        exact = github_read('/actions/workflows/' + str(workflow_id))
        require(
            exact.get('id') == workflow_id and
            exact.get('path') == WORKFLOW_PATH and
            exact.get('state') == 'disabled_manually',
            'Source workflow is not paused')
        runs = github_read('/actions/workflows/' + str(workflow_id) +
                           '/runs?per_page=100')
        require(
            type(runs.get('workflow_runs')) is list,
            'Missing exact workflow history')
        require(
            all(
                run.get('status') == 'completed'
                for run in runs['workflow_runs']),
            'Source workflow still has an active attempt')
        # Existing remote attempts require complete owned-artifact restoration,
        # which this new-source local path intentionally cannot approximate.
        require(
            runs.get('total_count') == 0,
            'Hosted history exists; owned-state restoration required')
    else:
        require(not matches, 'Workflow history exists despite absent main path')
    return head


def reviewed_local_continuation():
    """Only the independently reviewed next attempt may preserve a prior claim."""
    if LOCAL_ATTEMPT_CONTINUATION is None:
        return None
    value = copy.deepcopy(LOCAL_ATTEMPT_CONTINUATION)
    require(type(value) is dict, 'Invalid local attempt continuation')
    evidence = value.pop('evidence', '')
    expected = {
        'schema':
            1,
        'attempt':
            4,
        'family':
            FAMILY,
        'revision':
            'pladis379-retained-attempt4-native-v1',
        'purpose':
            'native',
        'previous_state_sha256':
            '285fde3c5f98827eba000b31ada328184e02ff0ab5d90e281450c826ef7de1c2',
        'previous_claim_sha256':
            'a9afa29ab1ea9cf50f2f68810e939ec8705baeb317d3112972bd4e643d06b21e',
        'original_claim_sha256':
            '70ae56037238408bb2d65f703bef4fad3da15af1697a71f3c5be9c83e0c53416',
        'previous_source_sha256':
            'cabf6f643f21874d7cb39957cac45490d5f95174c89626e0144837273b129484',
        'previous_outcome':
            'full-native: outcome 0; semantic omissions subsequently fixed',
        'research_claim_sha256':
            'f1f334b5e7dc3d4bc51039635e95d1f36640edef654219c15c54a988e720c98e',
    }
    require(
        type(value.get('schema')) is int and
        type(value.get('attempt')) is int and value == expected and
        re.fullmatch(
            r'https://github\.com/YouthOpps/data-pipeline/issues/379#issuecomment-[1-9][0-9]*',
            evidence) is not None, 'Unreviewed local attempt continuation')
    return copy.deepcopy(LOCAL_ATTEMPT_CONTINUATION)


def retained_claim(journal, name, expected_sha256):
    journal.authority()
    descriptor = os.open(name,
                         os.O_RDONLY | os.O_NOFOLLOW,
                         dir_fd=journal.parent)
    try:
        info = os.fstat(descriptor)
        identity = journal.identity(info, False)
        require(0 < info.st_size <= 8192, 'Unrecognized retained claim size')
        raw = os.pread(descriptor, info.st_size + 1, 0)
        require(
            len(raw) == info.st_size and
            hashlib.sha256(raw).hexdigest() == expected_sha256,
            'Retained prior claim changed')
        current = os.stat(name, dir_fd=journal.parent, follow_symlinks=False)
        require(
            journal.identity(current, False) == identity,
            'Retained prior claim identity changed')
        return identity
    finally:
        os.close(descriptor)


def local_attempt_claim(journal, continuation):
    if continuation is None:
        require(
            hashlib.sha256(journal.acknowledged).hexdigest() ==
            '6ff3fb6d62235e4cfd229b0b59f2b58aed74d94da36bf689656470e829234455',
            'Initial local state generation is no longer current')
        return 'native-claim', None
    state = journal.load()
    require(
        hashlib.sha256(journal.acknowledged).hexdigest() ==
        continuation['previous_state_sha256'],
        'Prior attempt state generation changed')
    require(not state['pending'] and not state['events'],
            'Unresolved restrictions prohibit another local attempt')
    identities = {}
    for name, claim_digest in retained_local_claims(continuation):
        identities[name] = retained_claim(journal, name, claim_digest)
    return 'local-attempt-4', identities


def retained_local_claims(continuation):
    return (('native-claim', continuation['original_claim_sha256']),
            ('local-attempt-2', continuation['research_claim_sha256']),
            ('local-attempt-3', continuation['previous_claim_sha256']))


def prepare_local(purpose='native'):
    """Default-OFF first native handoff; no hosted bootstrap or publication."""
    require(
        os.environ.get('GITHUB_ACTIONS') != 'true',
        'Local native collection cannot run as a hosted job')
    require(not os.environ.get('DATA_SOURCE_TOKEN'),
            'Native collection cannot receive publication credentials')
    reviewed_decision()
    continuation = reviewed_local_continuation()
    require(purpose == (continuation['purpose'] if continuation else 'native'),
            'Local attempt purpose is not independently reviewed')
    require(
        type(LOCAL_SEED_MACHINE_SHA256) is str and hashlib.sha256(
            pathlib.Path('/etc/machine-id').read_bytes()).hexdigest()
        == LOCAL_SEED_MACHINE_SHA256,
        'No independently reviewed machine-bound local seed')
    original_path = pathlib.Path(
        '/workspace/work/research-yildiz-pladis-state.json')
    raw = original_path.read_bytes()
    decode_original(raw)
    lifecycle = Transport(None)
    local_coordination(lifecycle)
    # Installed once by a reviewed local handoff. Runtime never recreates
    # missing state from old research bytes, including after host/tmp loss.
    directory = pathlib.Path(LOCAL_STATE_DIRECTORY)
    require(directory.is_dir(), 'Reviewed local state has not been installed')
    journal = PrivateJournal(str(directory))
    try:
        fcntl.flock(journal.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal.load()
        claim_name, retained_identity = local_attempt_claim(
            journal, continuation)
        # Every local transition is one-shot. Earlier claims remain untouched.
        # This initial native transition is one-shot. The marker survives every
        # transport/journal failure and is never automatically cleared. A later
        # local or hosted handoff requires review of this attempt's exact state.
        claim_fd = os.open(claim_name,
                           os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                           0o600,
                           dir_fd=journal.parent)
        claim = json.dumps(
            {
                'held_sha256':
                    HELD_SHA256,
                'machine_sha256':
                    LOCAL_SEED_MACHINE_SHA256,
                'source_sha256':
                    hashlib.sha256(pathlib.Path(__file__).read_bytes()
                                  ).hexdigest()
            },
            sort_keys=True).encode()
        if continuation is not None:
            payload = json.loads(claim)
            payload['continuation'] = continuation
            claim = json.dumps(payload, sort_keys=True).encode()
        try:
            require(
                os.write(claim_fd, claim) == len(claim), 'Short native claim')
            os.fsync(claim_fd)
            claim_identity = journal.identity(os.fstat(claim_fd), False)
            os.fsync(journal.parent)
        finally:
            os.close(claim_fd)
        transport = Transport(journal)
        transport.deadline = lifecycle.deadline
        verified_first = False

        def authority():
            nonlocal verified_first
            require(original_path.read_bytes() == raw,
                    'Shared research family provenance changed')
            journal.authority()
            if continuation is not None:
                for name, digest in retained_local_claims(continuation):
                    require(
                        retained_claim(journal, name,
                                       digest) == retained_identity[name],
                        'Earlier claim identity changed')
            claim_info = os.stat(claim_name,
                                 dir_fd=journal.parent,
                                 follow_symlinks=False)
            require(
                journal.identity(claim_info, False) == claim_identity,
                'Native attempt claim changed')
            if transport.time.monotonic(
            ) >= transport.startup_until and not verified_first:
                local_coordination(transport)
                verified_first = True

        transport.authority = authority
        return transport
    except BaseException:
        journal.close()
        raise


def collect():
    transport = prepare_local()
    try:
        return collect_body(transport)
    finally:
        transport.journal.close()


def metadata(records, attempt, previous=None, error=None):
    """Prepare source status; only a successful atomic commit may publish it."""
    result = copy.deepcopy(previous) if previous is not None else {
        'source':
            SOURCE,
        'display_name':
            'Yıldız Holding / pladis careers',
        'source_url':
            ORIGIN + '/pladis_Careers',
        'description':
            'Public internship and graduate vacancies from the shared Workday careers site.',
        'last_success_at':
            None,
    }
    result.update(last_attempt_at=attempt)
    if error is None:
        validate_records(records, allow_empty=True)
        result.update(status='success',
                      message=f'{len(records)} current opportunities',
                      last_success_at=attempt,
                      error=None)
        result.pop('failure_stage', None)
        result.pop('error_type', None)
    else:
        result.update(status='fail',
                      message='Refresh failed; last-good data retained',
                      error=failure_details(error)['message'],
                      error_type=failure_details(error)['type'],
                      failure_stage='collection')
    return result


HOSTED_HANDOFF = {
    'schema':
        1,
    'family':
        'research-yildiz-pladis',
    'source':
        'yildiz-pladis-careers',
    'state_base64':
        ('eyJibG9ja2VkIjp0cnVlLCJjb21wbGV0ZWQiOjE3OTQ5MTI5NzkuNjAyNTU2LCJl'
         'dmVudHMiOltdLCJmYW1pbHkiOiJyZXNlYXJjaC15aWxkaXotcGxhZGlzIiwiaG9s'
         'ZF9ldmlkZW5jZSI6eyJib2R5X3NoYTI1NiI6IjNjZGM1NDlkMmFlODBjYzEwMDkw'
         'MDcwMDQzN2YxNGQwZTdlNmVhMDAxMjFkZGNkOGY0MWFkZDAyZTY5MTlhMzMiLCJj'
         'bGFzc2lmaWNhdGlvbiI6IkFjdHVhbCBjb250ZW50IENsb3VkRnJvbnQgUmVxdWVz'
         'dCBibG9ja2VkOyBubyBmdXJ0aGVyIHB1Ymxpc2hlciByZXF1ZXN0cyBtYWRlIiwi'
         'aG9zdCI6Ind3dy5wbGFkaXNnbG9iYWwuY29tIiwic2NvcGVfcmV2aWV3X3JlcXVp'
         'cmVkIjp0cnVlLCJzdGF0dXMiOjQwMywidXJsIjoiaHR0cHM6Ly93d3cucGxhZGlz'
         'Z2xvYmFsLmNvbS93b3JraW5nLXBsYWRpcyJ9LCJpbnRlcnZhbCI6MTIuMiwiaW52'
         'ZW50b3J5X2NvbXBsZXRlZCI6MTc5NDkxMjk3OS42MDI1NTYsImxlZ2FjeV9oaXN0'
         'b3J5IjoidW5rbm93biIsIm5hdGl2ZV9hZGFwdGVyX2FjY2VwdGFuY2UiOmZhbHNl'
         'LCJvYnNlcnZlZF9hdCI6MTc5NDkxMjk3OS42MDI1NTYsInBhZ2VfaW50ZXJ2YWwi'
         'OjMwLCJwZW5kaW5nIjpmYWxzZSwicmVzZWFyY2hfb25seSI6dHJ1ZSwicmV0YWlu'
         'ZWRfc2NvcGUiOnsiZGVjaXNpb24iOnsiYm9keV9zaGEyNTYiOiIzY2RjNTQ5ZDJh'
         'ZTgwY2MxMDA5MDA3MDA0MzdmMTRkMGU3ZTZlYTAwMTIxZGRjZDhmNDFhZGQwMmU2'
         'OTE5YTMzIiwiZGVuaWVkX29yaWdpbnMiOlsiaHR0cHM6Ly9wbGFkaXNnbG9iYWwu'
         'Y29tIiwiaHR0cHM6Ly93d3cucGxhZGlzZ2xvYmFsLmNvbSJdLCJldmlkZW5jZSI6'
         'Imh0dHBzOi8vZ2l0aHViLmNvbS9Zb3V0aE9wcHMvZGF0YS1waXBlbGluZS9pc3N1'
         'ZXMvMzc5I2lzc3VlY29tbWVudC02OTQyMjIzNzcwIiwiZmFtaWx5IjoicmVzZWFy'
         'Y2gteWlsZGl6LXBsYWRpcyIsImhlbGRfc2hhMjU2IjoiYTlkM2M1YmYyZDAyMjA3'
         'ZWM1YjdlZDk2MzM0YWQ4YzhmNGMyYWIxNzNkMzYwNGI4YjEzNjM0ZThjOTc3MDRj'
         'ZCIsInJldmlzaW9uIjoicGxhZGlzMzc5LXJldGFpbmVkLWNvcnBvcmF0ZS1vcmln'
         'aW4tdjEiLCJzY2hlbWEiOjEsInNlcnZpY2Vfb3JpZ2luIjoiaHR0cHM6Ly9wbGFk'
         'aXMud2QzLm15d29ya2RheWpvYnMuY29tIiwic2VydmljZV9zaXRlIjoicGxhZGlz'
         'X0NhcmVlcnMiLCJzb3VyY2UiOiJ5aWxkaXotcGxhZGlzLWNhcmVlcnMifSwiZXZl'
         'bnQiOnsiYm9keV9zaGEyNTYiOiIzY2RjNTQ5ZDJhZTgwY2MxMDA5MDA3MDA0Mzdm'
         'MTRkMGU3ZTZlYTAwMTIxZGRjZDhmNDFhZGQwMmU2OTE5YTMzIiwiY29tcGxldGVk'
         'IjoxNzk0ODQ5NjcxLjc3Mzc4MzQsImtpbmQiOiJyZXRhaW5lZF9jb3Jwb3JhdGVf'
         'cmVmdXNhbCIsInNvdXJjZV9yZXZpc2lvbiI6IjlmNDBiYzk5MmRiODUzNmJkOWNk'
         'MTVkN2ZmNjAzNzg1MzU3NTllNjYzZGYwYWU1NTc1MDRiOGI0MmVkNWEyMDMiLCJz'
         'dGFydGVkIjoxNzk0ODQ5NjcxLjUxNTM0LCJzdGF0dXMiOjQwMywidXJsIjoiaHR0'
         'cHM6Ly93d3cucGxhZGlzZ2xvYmFsLmNvbS93b3JraW5nLXBsYWRpcyJ9LCJvcmln'
         'aW5hbF9iYXNlNjQiOiJld29nSUNKbVlXMXBiSGtpT2lBaWNtVnpaV0Z5WTJndGVX'
         'bHNaR2w2TFhCc1lXUnBjeUlzQ2lBZ0luTjBZWEowY3lJNklGc0tJQ0FnSURFM09U'
         'UTRORGt6TmpjdU1UYzBNekF4TkN3S0lDQWdJREUzT1RRNE5Ea3pPREF1T0RVME56'
         'YzNOaXdLSUNBZ0lERTNPVFE0TkRrME1qVXVOakUzTkRRNExBb2dJQ0FnTVRjNU5E'
         'ZzBPVFF6T0M0Mk5qYzBNakl6TEFvZ0lDQWdNVGM1TkRnME9UUTFPQzQ0TlRjNU16'
         'UTFMQW9nSUNBZ01UYzVORGcwT1RRM01pNDNOVFkzTnpNM0xBb2dJQ0FnTVRjNU5E'
         'ZzBPVFE0TlM0NE1qQXpOVEkyTEFvZ0lDQWdNVGM1TkRnME9UVXhOaTQzTlRZek9U'
         'Y3lMQW9nSUNBZ01UYzVORGcwT1RVek1TNHpPVFl6TkRBMExBb2dJQ0FnTVRjNU5E'
         'ZzBPVFUwTkM0MU5qazFNeXdLSUNBZ0lERTNPVFE0TkRrMU9ERXVNek0zTnpRd055'
         'd0tJQ0FnSURFM09UUTRORGsxT1RRdU1qQTROamd5TXl3S0lDQWdJREUzT1RRNE5E'
         'azJNRFl1T1RNM01qWTRMQW9nSUNBZ01UYzVORGcwT1RZMU9DNDVOekUzTVRNNExB'
         'b2dJQ0FnTVRjNU5EZzBPVFkzTVM0MU1UVXpOQW9nSUYwc0NpQWdJbU52YlhCc1pY'
         'UmxaQ0k2SURFM09UUTRORGsyTnpFdU56Y3pOemd6TkN3S0lDQWlhVzUyWlc1MGIz'
         'SjVYMk52YlhCc1pYUmxaQ0k2SURFM09UUTRORGsxT0RJdU1EQTROVFk0TXl3S0lD'
         'QWlhVzUwWlhKMllXd2lPaUF4TWk0eUxBb2dJQ0p3WVdkbFgybHVkR1Z5ZG1Gc0lq'
         'b2dNekFzQ2lBZ0luTjBjbWxyWlhNaU9pQXdMQW9nSUNKMWJuUnBiQ0k2SURBc0Np'
         'QWdJbkJsYm1ScGJtY2lPaUJtWVd4elpTd0tJQ0FpWW14dlkydGxaQ0k2SUhSeWRX'
         'VXNDaUFnSW14bFoyRmplVjlvYVhOMGIzSjVJam9nSW5WdWEyNXZkMjRpTEFvZ0lD'
         'Sm9iMnhrWDJWMmFXUmxibU5sSWpvZ2V3b2dJQ0FnSW1odmMzUWlPaUFpZDNkM0xu'
         'QnNZV1JwYzJkc2IySmhiQzVqYjIwaUxBb2dJQ0FnSW5WeWJDSTZJQ0pvZEhSd2N6'
         'b3ZMM2QzZHk1d2JHRmthWE5uYkc5aVlXd3VZMjl0TDNkdmNtdHBibWN0Y0d4aFpH'
         'bHpJaXdLSUNBZ0lDSnpkR0YwZFhNaU9pQTBNRE1zQ2lBZ0lDQWlZbTlrZVY5emFH'
         'RXlOVFlpT2lBaU0yTmtZelUwT1dReVlXVTRNR05qTVRBd09UQXdOekF3TkRNM1pq'
         'RTBaREJsTjJVMlpXRXdNREV5TVdSa1kyUTRaalF4WVdSa01ESmxOamt4T1dFek15'
         'SXNDaUFnSUNBaVkyeGhjM05wWm1sallYUnBiMjRpT2lBaVFXTjBkV0ZzSUdOdmJu'
         'UmxiblFnUTJ4dmRXUkdjbTl1ZENCU1pYRjFaWE4wSUdKc2IyTnJaV1E3SUc1dklH'
         'WjFjblJvWlhJZ2NIVmliR2x6YUdWeUlISmxjWFZsYzNSeklHMWhaR1VpTEFvZ0lD'
         'QWdJbk5qYjNCbFgzSmxkbWxsZDE5eVpYRjFhWEpsWkNJNklIUnlkV1VLSUNCOUxB'
         'b2dJQ0p5WlhObFlYSmphRjl2Ym14NUlqb2dkSEoxWlN3S0lDQWlibUYwYVhabFgy'
         'RmtZWEIwWlhKZllXTmpaWEIwWVc1alpTSTZJR1poYkhObENuMD0ifSwic2NoZW1h'
         'IjoxLCJzdGFydHMiOlsxNzk0ODQ5MzY3LjE3NDMwMTQsMTc5NDg0OTM4MC44NTQ3'
         'Nzc2LDE3OTQ4NDk0MjUuNjE3NDQ4LDE3OTQ4NDk0MzguNjY3NDIyMywxNzk0ODQ5'
         'NDU4Ljg1NzkzNDUsMTc5NDg0OTQ3Mi43NTY3NzM3LDE3OTQ4NDk0ODUuODIwMzUy'
         'NiwxNzk0ODQ5NTE2Ljc1NjM5NzIsMTc5NDg0OTUzMS4zOTYzNDA0LDE3OTQ4NDk1'
         'NDQuNTY5NTMsMTc5NDg0OTU4MS4zMzc3NDA3LDE3OTQ4NDk1OTQuMjA4NjgyMywx'
         'Nzk0ODQ5NjA2LjkzNzI2OCwxNzk0ODQ5NjU4Ljk3MTcxMzgsMTc5NDg0OTY3MS41'
         'MTUzNCwxNzk0OTEyOTI3LjQwNjUwMTUsMTc5NDkxMjk0MC4yNTk3NTEzLDE3OTQ5'
         'MTI5NTMuMTA1Njk5LDE3OTQ5MTI5NjUuODI5NTc4NCwxNzk0OTEyOTc4Ljc5MTEx'
         'MTVdLCJzdHJpa2VzIjowLCJ1bnRpbCI6MH0='),
    'state_sha256':
        '70e818be1b4e388632b6cabe676e86fb35906a9bbf30d8d8dbaaddf963bf2f15',
    'native_sha256':
        'fb040db4b181b3cd367caa08c66bcb4099959997d2a04d062fbe0292e559f357',
    'evidence':
        'https://github.com/YouthOpps/data-pipeline/issues/379#issuecomment-6947045123',
}
HOSTED_WORKFLOW_SHA256 = 'ea133de8ca130b62c0bf55b8ca0a99fc73b62629358b71f1cba44a552cd2c2ba'
PIPELINE_REPOSITORY = 'YouthOpps/data-pipeline'
HOSTED_WORKFLOW = '.github/workflows/fetch-' + SOURCE + '.yml'
ARTIFACT_NAME = SOURCE + '-pacing-state'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


class HostedHTTP:
    """Bound GitHub operations separately from the publisher transport."""

    def __init__(self, deadline):
        # Reuse the reviewed absolute alarm/closure guard, not publisher pacing.
        self.guard = Transport(None)
        self.guard.deadline = deadline

    def request(self,
                url,
                token=None,
                method='GET',
                payload=None,
                limit=5_000_000):
        parsed = urllib.parse.urlsplit(url)
        require(
            parsed.scheme == 'https' and parsed.hostname and
            not parsed.username and not parsed.password and not parsed.port and
            not parsed.fragment, 'Unsafe infrastructure destination')
        storage = parsed.hostname.endswith(
            ('.blob.core.windows.net', '.actions.githubusercontent.com'))
        require(parsed.hostname == 'api.github.com' or storage,
                'Unapproved infrastructure origin')
        require(
            not storage or (not token and method == 'GET' and payload is None),
            'Signed storage must receive only an unauthenticated GET')
        headers = {
            'Accept': 'application/vnd.github+json',
            'Cache-Control': 'no-cache, no-store'
        }
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None if payload is None else canonical(payload)
        if data is not None:
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(url,
                                         data=data,
                                         headers=headers,
                                         method=method)
        try:
            with self.guard.deadline_guard():
                try:
                    response = self.guard.opener.open(
                        request, timeout=min(30, self.guard.remaining()))
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    body = response.read(limit + 1)
                    require(len(body) <= limit, 'GitHub response exceeds bound')
                    return response.status, dict(response.headers), body
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise ScopeError(
                'GitHub operation failed; outcome unconfirmed') from None

    def api(self,
            path,
            publication=False,
            method='GET',
            payload=None,
            missing=False):
        repository = 'YouthOpps/data-source' if publication else PIPELINE_REPOSITORY
        token = os.environ.get(
            'DATA_SOURCE_TOKEN' if publication else 'GITHUB_TOKEN')
        require(token, 'Required GitHub credential missing')
        status, _, body = self.request(
            'https://api.github.com/repos/' + repository + path, token, method,
            payload)
        if missing and status == 404:
            return None
        require(status in (200, 201), 'GitHub API rejected operation')
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise ScopeError('GitHub returned invalid JSON') from None


def hosted_contents(http, path, head):
    """Authenticate immutable source/workflow bytes using their Git blob IDs."""
    require(re.fullmatch(r'[0-9a-f]{40}', head or ''), 'Invalid Git revision')
    item = http.api('/contents/' + path + '?ref=' + head)
    require(
        item.get('path') == path and item.get('encoding') == 'base64',
        'Unexpected immutable contents response')
    raw = base64.b64decode(item['content'])
    require(
        type(item.get('size')) is int and item['size'] == len(raw) and
        0 < len(raw) <= 5_000_000, 'Invalid immutable contents size')
    blob = b'blob ' + str(len(raw)).encode() + b'\0' + raw
    require(
        hashlib.sha1(blob).hexdigest() == item.get('sha'),
        'Immutable Git blob mismatch')
    return raw


def hosted_timestamp(value):
    require(type(value) is str, 'Missing workflow timestamp')
    result = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.utcoffset() is not None, 'Naive workflow timestamp')
    result = result.timestamp()
    require(
        finite(result) and result <= time.time() + 1,
        'Invalid workflow timestamp')
    return result


def hosted_pages(http, path, key):
    """Read the entire advertised inventory, rejecting drift or truncation."""
    rows, seen, total, page = [], set(), None, 1
    while True:
        separator = '&' if '?' in path else '?'
        result = http.api(path + separator + f'per_page=100&page={page}')
        count, batch = result.get('total_count'), result.get(key)
        require(
            type(count) is int and count >= 0 and isinstance(batch, list) and
            len(batch) <= 100 and (total is None or total == count),
            'Incomplete or changing GitHub inventory')
        total = count
        for item in batch:
            require(
                isinstance(item, dict) and type(item.get('id')) is int and
                item['id'] > 0 and item['id'] not in seen,
                'Invalid or duplicate GitHub inventory item')
            seen.add(item['id'])
            rows.append(item)
        if len(rows) == total:
            return rows
        require(
            len(rows) < total and len(batch) == 100,
            'Truncated GitHub inventory')
        page += 1


class HostedHistory:
    """Exact authenticated attempts, ordered by actual job completion."""

    def __init__(self, http):
        self.http = http
        require(
            os.environ.get('GITHUB_ACTIONS') == 'true' and
            os.environ.get('GITHUB_REPOSITORY') == PIPELINE_REPOSITORY and
            os.environ.get('GITHUB_REF') == 'refs/heads/main',
            'Unexpected hosted repository or branch')
        values = [
            os.environ.get(k, '')
            for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT')
        ]
        require(all(re.fullmatch(r'[1-9][0-9]*', v) for v in values),
                'Invalid current run identity')
        self.current = tuple(map(int, values))
        self.head = os.environ.get('GITHUB_SHA', '')
        require(
            re.fullmatch(r'[0-9a-f]{40}', self.head) and
            os.environ.get('GITHUB_JOB') == 'collect' and
            os.environ.get('GITHUB_WORKFLOW_REF') == PIPELINE_REPOSITORY + '/' +
            HOSTED_WORKFLOW + '@refs/heads/main' and
            os.environ.get('GITHUB_WORKFLOW_SHA') == self.head,
            'Untrusted hosted job context')
        workflows = hosted_pages(http, '/actions/workflows', 'workflows')
        matches = [w for w in workflows if w.get('path') == HOSTED_WORKFLOW]
        require(
            len(matches) == 1 and matches[0].get('state') == 'active',
            'Missing or ambiguous source workflow')
        self.workflow_id = matches[0]['id']
        self.source_sha = digest(pathlib.Path(__file__).read_bytes())
        require(
            digest(
                hosted_contents(http, 'adapters/' + SOURCE + '/adapter.py',
                                self.head)) == self.source_sha,
            'Current executable differs from authenticated revision')
        require(
            digest(hosted_contents(http, HOSTED_WORKFLOW,
                                   self.head)) == HOSTED_WORKFLOW_SHA256,
            'Unreviewed workflow definition')

    def attempt(self, run_id, attempt, current=False):
        run = self.http.api(f'/actions/runs/{run_id}/attempts/{attempt}')
        require(
            run.get('id') == run_id and run.get('run_attempt') == attempt and
            type(run.get('id')) is int and
            type(run.get('run_attempt')) is int and
            run.get('workflow_id') == self.workflow_id and
            run.get('path') == HOSTED_WORKFLOW and
            run.get('head_branch') == 'main' and run.get(
                'repository', {}).get('full_name') == PIPELINE_REPOSITORY and
            run.get('head_repository',
                    {}).get('full_name') == PIPELINE_REPOSITORY and
            re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', '')),
            'Untrusted exact workflow attempt')
        jobs = hosted_pages(self.http,
                            f'/actions/runs/{run_id}/attempts/{attempt}/jobs',
                            'jobs')
        require(len(jobs) == 1, 'Expected exactly one source collection job')
        job = jobs[0]
        require(
            job.get('run_id') == run_id and
            job.get('run_attempt') == attempt and
            job.get('head_sha') == run['head_sha'] and
            job.get('head_branch') == 'main' and job.get('name') == 'collect',
            'Exact job binding mismatch')
        began = hosted_timestamp(job.get('started_at'))
        if current:
            require(
                run['head_sha'] == self.head and
                run.get('status') == job.get('status') == 'in_progress',
                'Current hosted attempt is not active')
            return {'run': run, 'job': job, 'began': began}
        require(
            run.get('status') == job.get('status') == 'completed',
            'Another family attempt is still active')
        finished = hosted_timestamp(job.get('completed_at'))
        require(began <= finished, 'Invalid job interval')
        return {'run': run, 'job': job, 'began': began, 'finished': finished}

    def queued_sibling(self, listed, current_began):
        """Exclude only a newer, positively jobless first queued attempt."""
        run_id = listed['id']
        require(
            listed.get('status') == 'queued' and
            type(listed.get('run_attempt')) is int and
            listed['run_attempt'] == 1 and run_id != self.current[0],
            'Unproved queued sibling identity')
        run = self.http.api(f'/actions/runs/{run_id}/attempts/1')
        require(
            type(run.get('id')) is int and run['id'] == run_id and
            type(run.get('run_attempt')) is int and run['run_attempt'] == 1 and
            run.get('status') == 'queued' and run.get('conclusion') is None and
            run.get('workflow_id') == self.workflow_id and
            run.get('path') == HOSTED_WORKFLOW and
            run.get('head_branch') == 'main' and run.get(
                'repository', {}).get('full_name') == PIPELINE_REPOSITORY and
            run.get('head_repository',
                    {}).get('full_name') == PIPELINE_REPOSITORY and
            re.fullmatch(r'[0-9a-f]{40}', run.get('head_sha', '')),
            'Untrusted exact queued sibling')
        require(
            all(
                run.get(key) == listed.get(key)
                for key in ('id', 'run_attempt', 'status', 'workflow_id',
                            'path', 'head_sha', 'head_branch', 'created_at')),
            'Queued sibling inventory changed')
        require(
            hosted_timestamp(run.get('created_at')) > current_began,
            'Older queued ancestry requires review')
        require(
            not hosted_pages(self.http,
                             f'/actions/runs/{run_id}/attempts/1/jobs', 'jobs'),
            'Queued sibling already has a job')
        require(
            not hosted_pages(self.http, f'/actions/runs/{run_id}/artifacts',
                             'artifacts'), 'Queued sibling already has outputs')
        require(
            digest(
                hosted_contents(self.http, 'adapters/' + SOURCE + '/adapter.py',
                                run['head_sha'])) == self.source_sha and
            digest(hosted_contents(self.http, HOSTED_WORKFLOW,
                                   run['head_sha'])) == HOSTED_WORKFLOW_SHA256,
            'Queued sibling has different code or serialization')
        # A queued run's run_started_at can equal creation even with zero jobs;
        # it is not a publisher receipt. No completion or pacing is fabricated.
        return {
            key: run.get(key)
            for key in ('id', 'run_attempt', 'status', 'workflow_id', 'path',
                        'head_sha', 'head_branch', 'created_at')
        }

    def scan(self):
        current_began = self.attempt(*self.current, current=True)['began']
        runs = hosted_pages(
            self.http,
            f'/actions/workflows/{self.workflow_id}/runs?branch=main',
            'workflow_runs')
        completed, queued, found = [], [], False
        for run in runs:
            require(
                type(run.get('run_attempt')) is int and run['run_attempt'] > 0,
                'Missing attempt inventory')
            if run['id'] != self.current[0] and run.get('status') == 'queued':
                queued.append(self.queued_sibling(run, current_began))
                continue
            for attempt in range(1, run['run_attempt'] + 1):
                identity = (run['id'], attempt)
                if identity == self.current:
                    found = True
                else:
                    completed.append(self.attempt(*identity))
        require(found, 'Current attempt absent from full history')
        require(all(row['finished'] <= current_began for row in completed),
                'Previous attempt overlaps the current publisher job')
        completed.sort(key=lambda row: (row['finished'], row['began'], row[
            'run']['id'], row['run']['run_attempt']))
        for left, right in zip(completed, completed[1:]):
            require(left['finished'] <= right['began'],
                    'Overlapping publisher attempts require review')
        self.queued_snapshot = sorted(queued, key=lambda row: row['id'])
        return completed

    def no_access(self, row):
        """Mature quiet-floor proof: exact current code failed before 58s."""
        run, job = row['run'], row['job']
        require(
            run['run_attempt'] == 1 and
            run.get('conclusion') == job.get('conclusion') == 'failure' and
            0 <= row['finished'] - row['began'] < 58,
            'Artifactless attempt has no positive preaccess proof')
        require(
            digest(
                hosted_contents(self.http, 'adapters/' + SOURCE + '/adapter.py',
                                run['head_sha'])) == self.source_sha and
            digest(hosted_contents(self.http, HOSTED_WORKFLOW,
                                   run['head_sha'])) == HOSTED_WORKFLOW_SHA256,
            'Artifactless historical code is not the reviewed quiet code')

    def artifact(self, row, item):
        run = row['run']
        require(
            item.get('expired') is False and
            item.get('workflow_run', {}).get('id') == run['id'] and
            item.get('workflow_run', {}).get('head_sha') == run['head_sha'] and
            type(item.get('size_in_bytes')) is int and
            0 < item['size_in_bytes'] <= 32768 and
            re.fullmatch(r'sha256:[0-9a-f]{64}', item.get('digest', '')),
            'Artifact ownership metadata mismatch')
        status, headers, _ = self.http.request(
            'https://api.github.com/repos/' + PIPELINE_REPOSITORY +
            f"/actions/artifacts/{item['id']}/zip",
            os.environ['GITHUB_TOKEN'],
            limit=1000)
        require(status == 302, 'Expected authenticated artifact redirect')
        location = next(
            (v for k, v in headers.items() if k.lower() == 'location'), '')
        parsed = urllib.parse.urlsplit(location)
        require(
            parsed.scheme == 'https' and parsed.hostname and
            '.' in parsed.hostname and not parsed.username and
            not parsed.password and not parsed.port and not parsed.fragment and
            parsed.hostname.endswith(
                ('.blob.core.windows.net', '.actions.githubusercontent.com')),
            'Unsafe artifact destination')
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ScopeError('Literal artifact destination prohibited')
        # Signed destination never receives Authorization or enters diagnostics.
        status, _, body = self.http.request(location, limit=32768)
        require(status == 200 and 'sha256:' + digest(body) == item['digest'],
                'Artifact download digest mismatch')
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            require(
                len(entries) == 1 and
                entries[0].filename == 'pacing-state.json',
                'Unexpected artifact ZIP members')
            entry = entries[0]
            require(
                entry.file_size <= 16384 and not entry.flag_bits & 1 and
                entry.external_attr >> 16 & 0o170000 != 0o120000,
                'Unsafe artifact ZIP entry')
            envelope = json.loads(archive.read(entry))
        require(
            set(envelope) == {
                'schema', 'repository', 'run_id', 'run_attempt', 'source',
                'head_sha', 'adapter_sha256', 'state_sha256', 'state'
            }, 'Invalid state envelope')
        require(
            type(envelope['schema']) is int and envelope['schema'] == 1 and
            envelope['repository'] == PIPELINE_REPOSITORY and
            type(envelope['run_id']) is int and
            type(envelope['run_attempt']) is int and
            (envelope['run_id'],
             envelope['run_attempt']) == (run['id'], run['run_attempt']) and
            envelope['source'] == SOURCE and
            envelope['head_sha'] == run['head_sha'] and
            envelope['adapter_sha256'] == digest(
                hosted_contents(self.http, 'adapters/' + SOURCE + '/adapter.py',
                                run['head_sha'])) and
            envelope['state_sha256'] == digest(canonical(envelope['state'])),
            'Artifact envelope identity mismatch')
        return validate(envelope['state'])

    def restore(self, seed):
        history = self.scan()
        bound = 0
        for row in reversed(history):
            run = row['run']
            artifacts = hosted_pages(self.http,
                                     f"/actions/runs/{run['id']}/artifacts",
                                     'artifacts')
            name = ARTIFACT_NAME + '-' + str(run['run_attempt'])
            matches = [item for item in artifacts if item.get('name') == name]
            if matches:
                require(len(matches) == 1, 'Ambiguous newest owned artifact')
                result = merge(self.artifact(row, matches[0]), seed)
                break
            # Other attempts' artifacts are not substitutes for this attempt.
            require(not artifacts,
                    'Missing attempt artifact amid unknown outputs')
            self.no_access(row)
            bound = max(bound, row['finished'])
        else:
            result = copy.deepcopy(seed)
        if bound:
            result['until'] = max(
                result['until'],
                bound + max(result['interval'], result['page_interval']))
            result['observed_at'] = max(result['observed_at'], bound)
        return validate(result), history


def reviewed_hosted_handoff():
    """The first hosted generation requires an exact reviewed native outcome."""
    marker = HOSTED_HANDOFF
    require(
        isinstance(marker, dict) and set(marker) == {
            'schema', 'family', 'source', 'state_base64', 'state_sha256',
            'native_sha256', 'evidence'
        }, 'Hosted native handoff is disabled')
    require(
        type(marker['schema']) is int and marker['schema'] == 1 and
        marker['family'] == FAMILY and marker['source'] == SOURCE and
        isinstance(marker['evidence'], str) and
        isinstance(marker['native_sha256'], str) and re.fullmatch(
            r'https://github\.com/YouthOpps/data-pipeline/issues/379'
            r'#issuecomment-[0-9]+', marker['evidence']) and
        re.fullmatch(r'[0-9a-f]{64}', marker['native_sha256']),
        'Unreviewed hosted handoff decision')
    raw = base64.b64decode(marker['state_base64'], validate=True)
    require(
        digest(raw) == marker['state_sha256'], 'Native handoff bytes changed')
    state = validate(json.loads(raw))
    require(not state['pending'] and not state['events'],
            'Native handoff is unresolved')
    return state


def workflow_output(ready):
    """Write only a boolean upload gate; state stays in its owned artifact."""
    name = os.environ.get('GITHUB_OUTPUT', '')
    require(name and os.path.isabs(name), 'Missing workflow output file')
    descriptor = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, follow_symlinks=False)
        require(
            stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
            info.st_nlink == 1 and not info.st_mode & 0o022 and
            (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino),
            'Unsafe workflow output file')
        raw = ('pacing_ready=' + ('true' if ready else 'false') + '\n').encode()
        require(
            os.write(descriptor, raw) == len(raw),
            'Short workflow output write')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class OwnedExport:
    """Pin the export parent and file through acknowledgement and invalidation."""

    def __init__(self):
        root = os.environ.get('RUNNER_TEMP', '')
        path = os.environ.get('PLADIS_PACING_ARTIFACT_PATH', '')
        require(
            root and os.path.isabs(root) and
            path == root + '/pacing-state.json',
            'Unexpected hosted artifact path')
        self.root, self.path = root, path
        self.parent = os.open(root,
                              os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fd = None
        self.identity = None
        self.failed = False
        info = os.fstat(self.parent)
        require(info.st_uid == os.getuid() and not info.st_mode & 0o022,
                'Unsafe artifact parent')
        self.parent_identity = (info.st_dev, info.st_ino)
        self.authority()
        require(not os.path.lexists(path), 'Unexpected existing export file')

    def authority(self):
        require(not self.failed, 'Owned export failure latched')
        for info in (os.fstat(self.parent),
                     os.stat(self.root, follow_symlinks=False)):
            require(
                stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and
                not info.st_mode & 0o022 and
                (info.st_dev, info.st_ino) == self.parent_identity,
                'Artifact parent replaced or unsafe')
        if self.fd is not None:
            for info in (os.fstat(self.fd),
                         os.stat('pacing-state.json',
                                 dir_fd=self.parent,
                                 follow_symlinks=False)):
                require(
                    stat.S_ISREG(info.st_mode) and
                    info.st_uid == os.getuid() and
                    stat.S_IMODE(info.st_mode) == 0o600 and
                    info.st_nlink == 1 and
                    (info.st_dev, info.st_ino) == self.identity,
                    'Owned artifact replaced or unsafe')

    def invalidate(self):
        """Invalidate only the held safe inode; never truncate a replacement."""
        try:
            self.authority()
            if self.fd is not None:
                os.ftruncate(self.fd, 0)
                os.fsync(self.fd)
                os.fsync(self.parent)
        finally:
            self.failed = True
            workflow_output(False)

    def write(self, journal, history):
        try:
            self.authority()
            state = journal.load()
            envelope = {
                'schema': 1,
                'repository': PIPELINE_REPOSITORY,
                'run_id': history.current[0],
                'run_attempt': history.current[1],
                'source': SOURCE,
                'head_sha': history.head,
                'adapter_sha256': history.source_sha,
                'state_sha256': digest(canonical(state)),
                'state': state,
            }
            raw = canonical(envelope)
            require(len(raw) <= 16384, 'State exceeds artifact envelope bound')
            self.fd = os.open('pacing-state.json',
                              os.O_RDWR | os.O_CREAT | os.O_EXCL |
                              os.O_NOFOLLOW,
                              0o600,
                              dir_fd=self.parent)
            info = os.fstat(self.fd)
            self.identity = (info.st_dev, info.st_ino)
            self.authority()
            require(os.write(self.fd, raw) == len(raw), 'Short artifact write')
            os.fsync(self.fd)
            os.fsync(self.parent)
            self.authority()
            require(
                os.pread(self.fd,
                         len(raw) + 1, 0) == raw and journal.load() == state,
                'Artifact acknowledgement mismatch')
            workflow_output(True)
            self.authority()
        except BaseException:
            self.invalidate()
            raise


def prepare_hosted(http):
    seed = reviewed_hosted_handoff()
    history = HostedHistory(http)
    state, prior = history.restore(seed)
    prior_queued = copy.deepcopy(history.queued_snapshot)
    # This is a fresh owned local inode restored only from authenticated
    # remote continuity, never a fallback after a local write failure.
    directory = tempfile.mkdtemp(prefix=FAMILY + '-',
                                 dir=os.environ['RUNNER_TEMP'])
    raw = canonical(state)
    descriptor = os.open(os.path.join(directory, 'state.json'),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
    try:
        require(
            os.write(descriptor, raw) == len(raw),
            'Short restored journal write')
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    journal = PrivateJournal(directory)
    try:
        require(journal.read_raw() == raw,
                'Restored journal acknowledgement failed')
        transport = Transport(journal)
        transport.deadline = min(transport.deadline, http.guard.deadline - 120)
        checked = False

        def authority():
            nonlocal checked
            journal.authority()
            if time.monotonic() >= transport.startup_until and not checked:
                require(
                    history.scan() == prior and
                    history.queued_snapshot == prior_queued,
                    'Hosted family history changed before publisher access')
                checked = True

        transport.authority = authority
        return transport, history
    except BaseException:
        journal.close()
        raise


def publication_snapshot(http):
    head = http.api('/git/ref/heads/main', publication=True)['object']['sha']
    require(re.fullmatch(r'[0-9a-f]{40}', head), 'Invalid publication head')
    tree = http.api('/git/commits/' + head, publication=True)['tree']['sha']
    require(re.fullmatch(r'[0-9a-f]{40}', tree), 'Invalid publication tree')
    files = {}
    for name in ('data.json', 'metadata.json'):
        path = 'datas/' + SOURCE + '/' + name
        item = http.api('/contents/' + path + '?ref=' + head,
                        publication=True,
                        missing=True)
        if item is None:
            files[name] = None
            continue
        require(
            item.get('encoding') == 'base64' and item.get('path') == path,
            'Invalid published source contents')
        raw = base64.b64decode(item['content'])
        blob = b'blob ' + str(len(raw)).encode() + b'\0' + raw
        require(
            len(raw) == item.get('size') and
            hashlib.sha1(blob).hexdigest() == item.get('sha'),
            'Published source blob mismatch')
        files[name] = raw.decode()
    require((files['data.json'] is None) == (files['metadata.json'] is None),
            'Incomplete source pair; publication refused')
    return {'head': head, 'tree': tree, 'files': files}


def publish_pair(http, snapshot, files):
    """Commit only this source pair; resolve conflicts without losing neighbors."""
    require(
        set(files) <= {'data.json', 'metadata.json'},
        'Foreign publication path')
    expected = snapshot['files'].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        candidate = None
        try:
            tree = http.api('/git/trees',
                            publication=True,
                            method='POST',
                            payload={
                                'base_tree':
                                    snapshot['tree'],
                                'tree': [{
                                    'path': 'datas/' + SOURCE + '/' + name,
                                    'mode': '100644',
                                    'type': 'blob',
                                    'content': value
                                } for name, value in files.items()]
                            })['sha']
            candidate = http.api('/git/commits',
                                 publication=True,
                                 method='POST',
                                 payload={
                                     'message': 'Update ' + SOURCE,
                                     'tree': tree,
                                     'parents': [snapshot['head']]
                                 })['sha']
            http.api('/git/refs/heads/main',
                     publication=True,
                     method='PATCH',
                     payload={
                         'sha': candidate,
                         'force': False
                     })
            confirmed = publication_snapshot(http)
            require(confirmed['files'] == desired,
                    'Published source pair not confirmed')
            return
        except Exception:
            # An ambiguous PATCH can already have succeeded. Read before any
            # further write and never overwrite a concurrent same-source change.
            latest = publication_snapshot(http)
            if candidate is not None and latest['files'] == desired:
                return
            require(
                latest['files'] == expected and
                latest['head'] != snapshot['head'] and attempt < 2,
                'Publication failed or same-source state changed')
            snapshot = latest


def publish():
    """Bound collection and atomic publication within the App token lifetime."""
    require(sys.argv[1:] == ['--publish'], 'Expected --publish')
    reviewed_hosted_handoff()
    http = HostedHTTP(time.monotonic() + 3300)
    workflow_output(False)
    owned = OwnedExport()
    try:
        transport, history = prepare_hosted(http)
    except Exception as error:
        report_failure('hosted_preparation', error)
        raise ScopeError(
            'Hosted preparation failed; see safe diagnostic') from None
    snapshot = None
    stage = 'publication_snapshot'
    attempt = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        snapshot = publication_snapshot(http)
        previous, previous_meta = [], None
        if snapshot['files']['data.json'] is not None:
            previous = json.loads(snapshot['files']['data.json'])
            previous_meta = json.loads(snapshot['files']['metadata.json'])
            validate_records(previous, allow_empty=True)
            require(
                previous_meta.get('source') == SOURCE and
                previous_meta.get('status') in ('success', 'fail') and
                previous_meta.get('last_success_at'),
                'Invalid last-good source metadata')
        stage = 'collection'
        fresh = collect_body(transport)
        now = datetime.datetime.now(datetime.timezone.utc)
        stage = 'normalization'
        records = successful_refresh(fresh, previous, now)
        status = metadata(records, attempt, previous_meta)
        stage = 'publication'
        publish_pair(
            http, snapshot, {
                'data.json':
                    json.dumps(records, ensure_ascii=False, indent=2) + '\n',
                'metadata.json':
                    json.dumps(status, ensure_ascii=False, indent=2) + '\n'
            })
    except Exception as error:
        if stage == 'collection':
            stage = getattr(transport, 'collection_stage', stage)
        report_failure(stage, error, transport)
        if snapshot is not None and snapshot['files']['data.json'] is not None:
            try:
                previous_meta = json.loads(snapshot['files']['metadata.json'])
                require(
                    previous_meta.get('source') == SOURCE and
                    previous_meta.get('last_success_at'),
                    'Invalid last-good status')
                status = metadata([], attempt, previous_meta, error)
                status['failure_stage'] = stage
                publish_pair(
                    http, snapshot, {
                        'metadata.json':
                            json.dumps(status, ensure_ascii=False, indent=2) +
                            '\n'
                    })
            except Exception as status_error:
                report_failure('failure_metadata_publication', status_error)
        raise ScopeError('Refresh failed; last-good data retained') from None
    finally:
        # Valid unresolved/held states are exported too: the next run must stop
        # on them, not restore a stale settled generation after a new refusal.
        try:
            owned.write(transport.journal, history)
            transport.journal.close()
        except BaseException:
            owned.invalidate()
            raise


if __name__ == '__main__':
    publish()
