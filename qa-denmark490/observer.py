"""Capture the original Denmark native test and preserve its actual journal in custody."""
import base64
import contextlib
import copy
import gzip
import io
from email.message import Message
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time
from types import ModuleType
import urllib.error
import urllib.parse

from authority import Custody, ROOT, canonical, digest, require
from escrow import Escrow
from anchor import openssl, verify_anchor

SOURCE = ROOT / 'adapters/dk-fulbright-center/adapter.py'
TEST = SOURCE.with_name('test_adapter.py')
PACKET = ROOT / 'qa-denmark490'
RECIPIENT = PACKET / 'reviewer.pem'
def json_bytes(value):
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode('ascii')}
    raise TypeError(type(value).__name__)


def upload_cipher(plaintext):
    result = subprocess.run(
        ['openssl', 'cms', '-encrypt', '-binary', '-outform', 'DER', '-aes-256-gcm', '-recip', str(RECIPIENT)],
        input=gzip.compress(plaintext, mtime=0), capture_output=True, timeout=50, check=True,
    )
    require(bool(result.stdout), 'Missing encrypted evidence')
    return result.stdout


def authorized_overlay(raw):
    edits = (
        (b'    workflow_mode_guard()\n    current_job_context()\n    incoming = restored_state()',
         b'    _QA_PUBLISHER_GUARD()\n    incoming = restored_state()'),
        (b'    hosted = os.environ.get("GITHUB_ACTIONS") == "true"\n    current = current_run_identity() if hosted else None',
         b'    hosted = False\n    current = None'),
        (b'        if now < target:\n            raise AdapterError("Publisher clock moved backwards", "access")',
         b'        _QA_BEFORE_RESERVATION(state)\n        now = time.time()\n        if now < target:\n            raise AdapterError("Publisher clock moved backwards", "access")'),
    )
    executable = raw
    for old, new in edits:
        require(executable.count(old) == 1, 'Unrecognized native authority boundary')
        executable = executable.replace(old, new, 1)
    reverse = executable
    for old, new in reversed(edits):
        require(reverse.count(new) == 1, 'Ambiguous reversal of native authority boundary')
        reverse = reverse.replace(new, old, 1)
    require(reverse == raw, 'Native overlay alters original collector')
    return executable


def load_adapter(custody, capture):
    executable = authorized_overlay(SOURCE.read_bytes())
    capture['executable_sha256'] = digest(executable)
    adapter = ModuleType('adapter')
    adapter.__file__ = str(SOURCE)
    def verify_job_clock():
        custody.before_publisher()
        row = custody.active_job()
        from datetime import datetime
        started = datetime.fromisoformat(row['started_at'].replace('Z', '+00:00')).timestamp()
        elapsed = time.time() - started
        require(-1 <= elapsed < 1800 - adapter.EXPORT_TIMEOUT, 'Native job budget exhausted')
        adapter._RUN_END = min(adapter._RUN_END, time.monotonic() + 1800 - max(0, elapsed) - 2)
        adapter.check_deadline()

    adapter._QA_PUBLISHER_GUARD = verify_job_clock
    old = sys.modules.get('adapter')
    require(old is None, 'Unexpected preloaded adapter')
    sys.modules['adapter'] = adapter
    exec(compile(executable, str(SOURCE), 'exec'), adapter.__dict__)
    return adapter


def instrument(adapter, custody, capture, escrow):
    original_open = adapter._OPENER.open
    original_download = adapter.download_inert_artifact
    original_write = adapter.save_budget
    floor = custody.production

    def download(identity, artifact, sha256=None, **kw):
        require(identity == (floor['run'], 1, 'dk-fulbright-center', floor['head']) and artifact == floor['artifact']
                and sha256 == 'sha256:' + floor['archive_sha256'] and not kw, 'Unexpected restoration ownership')
        state = original_download(identity, artifact, sha256)
        require(digest(canonical(state)) == floor['state_sha256'] and state['schema'] == 3
                and state['blocked'] is False and state['in_flight'] is False, 'Original state differs')
        capture['source_floor'] = copy.deepcopy(state)
        return state

    def attach(response, event):
        event['status'] = response.status
        event['headers'] = list(response.headers.items())
        original_read, original_close = response.read, response.close

        def read(*args, **kwargs):
            try:
                part = original_read(*args, **kwargs)
            except BaseException as error:
                partial = getattr(error, 'partial', None)
                if isinstance(partial, bytes):
                    event['segments'].append(partial)
                event['read_error'] = type(error).__name__
                raise
            event['segments'].append(part)
            return part

        def close(*args, **kwargs):
            try:
                result = original_close(*args, **kwargs)
            except BaseException as error:
                event['close_error'] = type(error).__name__
                raise
            else:
                if event.get('completion') is None:
                    event['completion'] = time.monotonic()
                    event['wall_completion'] = time.time()
                return result

        response.read = read
        response.close = close

    def opened(request, *args, **kwargs):
        url = request.full_url
        if not adapter.is_publisher(url):
            uri = urllib.parse.urlsplit(url)
            host = uri.hostname or ''
            headers = {key.casefold(): value for key, value in request.header_items()}
            require(uri.scheme == 'https' and not uri.username and not uri.password and not uri.port
                    and request.get_method() == 'GET'
                    and ((host == 'api.github.com' and uri.path.startswith('/repos/YouthOpps/data-pipeline/'))
                         or (host.endswith('.blob.core.windows.net') or host.endswith('.actions.githubusercontent.com'))
                         and 'authorization' not in headers), 'Unexpected infrastructure destination')
            return original_open(request, *args, **kwargs)
        headers = dict((k.casefold(), v) for k, v in request.header_items())
        require(headers.get('user-agent') == adapter._USER_AGENT and not {'authorization','proxy-authorization','cookie'}.intersection(headers),
                'Outbound publisher identity changed')
        require(capture.get('source_floor') is not None, 'Publisher open without predecessor')
        adapter.verify_journal()
        raw = os.pread(adapter._JOURNAL_FD, 65537, 0)
        escrow.require_pending(raw)
        state = json.loads(raw)
        require(state['in_flight'] and state['starts'], 'Physical opener lacks an acknowledged reservation')
        event = {'escrow_sequence': escrow.sequence, 'url': url, 'start': time.monotonic(), 'wall_start': time.time(), 'completion': None,
                 'startup_quiet_until': adapter._STARTUP_QUIET_UNTIL, 'request_headers': headers,
                 'reservation': state['starts'][-1], 'request_id': state['request_id'], 'interval': state['interval'], 'page_gap': state['page_interval'],
                 'segments': [], 'inventory': bool(state['request_inventory'])}
        capture['requests'].append(event)
        try:
            response = original_open(request, *args, **kwargs)
        except urllib.error.HTTPError as error:
            attach(error, event)
            raise
        except BaseException as error:
            event['open_error'] = type(error).__name__
            raise
        attach(response, event)
        return response

    def write(state):
        item = {'intended': copy.deepcopy(state), 'start': time.monotonic()}
        capture['persistence'].append(item)
        try:
            result = original_write(state)
        except BaseException as error:
            item['error'] = type(error).__name__
            raise
        else:
            item['acknowledged'] = True
        finally:
            item['after'] = copy.deepcopy(state)
            item['completion'] = time.monotonic()
            if adapter._JOURNAL_LOCKS:
                try:
                    adapter.verify_journal()
                    descriptor = adapter._JOURNAL_FD
                    size = os.fstat(descriptor).st_size
                    require(0 < size <= 65536, 'Journal capture exceeds original bound')
                    item['journal'] = os.pread(descriptor, size + 1, 0)
                    require(len(item['journal']) == size, 'Journal read raced')
                except Exception as error:
                    capture['observer_errors'].append(('persistence', type(error).__name__))
        # A checkpoint must be emitted after the original durable write and
        # before the collector can proceed to any physical opener.
        raw = item.get('journal')
        try:
            require(item.get('acknowledged') and raw is not None and not adapter._JOURNAL_DIRTY
                    and json.loads(raw) == state, 'Escrow does not match the original acknowledged write')
            item['escrow_sequence'] = escrow.acknowledge(
                raw, pending=state['in_flight'], opener_count=len(capture['requests']))
        except BaseException:
            capture['observer_errors'].append(('escrow', 'JournalCheckpointFailure'))
            raise
        return result


    original_begin = adapter.begin_transaction

    def guarded_begin(kind):
        custody.before_publisher()
        return original_begin(kind)

    def before_reservation(state):
        try:
            custody.before_publisher()
        except Exception as error:
            # Only an empty, exclusively held logical operation can end through
            # the adapter's own policy-only finalizer. A prior unresolved
            # request follows the original failure and custody behavior.
            if (not adapter._PUBLISHER_GATE_FAILED and not adapter._REQUEST_FRAMES
                    and adapter._ACTIVE_REQUEST_ID is None and state['in_flight']
                    and state['flight_id'] == adapter._ACTIVE_FLIGHT_ID
                    and state['request_id'] == state['completed_request_id']
                    and state['request_outcome'] != 'reserved'):
                raise adapter.PolicyStop('Native authorization ended before reservation', 'access') from error
            raise

    adapter.begin_transaction = guarded_begin
    adapter._QA_BEFORE_RESERVATION = before_reservation
    adapter.download_inert_artifact = download
    adapter._OPENER.open = opened
    adapter.save_budget = write


def test_original(capture):
    stdout, stderr = io.StringIO(), io.StringIO()
    saved = sys.argv
    try:
        sys.argv = [str(TEST)]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(str(TEST), run_name='__main__')
            except SystemExit as error:
                capture['native_exit'] = int(error.code or 0)
            except BaseException as error:
                capture['native_exit'] = 1
                capture['native_exception'] = {'type': type(error).__name__, 'message': str(error)}
            else:
                capture['native_exit'] = 0
    finally:
        sys.argv = saved
        capture.update(stdout=stdout.getvalue(), stderr=stderr.getvalue())


def final_state(adapter, capture):
    if not capture['persistence'] or adapter._JOURNAL_FD is None or capture.get('source_floor') is None:
        return {'classification': 'no_new_verified_state', 'state_sha256': None}
    with adapter.locked_budget():
        state = adapter.load_budget()
        adapter.verify_journal()
        descriptor = adapter._JOURNAL_FD
        size = os.fstat(descriptor).st_size
        require(0 < size <= 65536, 'Final state is unbounded')
        raw = os.pread(descriptor, size + 1, 0)
        require(len(raw) == size and not adapter._JOURNAL_DIRTY and json.loads(raw) == state,
                'State is not the last durable acknowledgement')
        require(adapter.validate_budget(copy.deepcopy(state)) == state, 'Final budget schema differs')
    require(capture['persistence'][-1].get('acknowledged') is True
            and capture['persistence'][-1].get('journal') == raw, 'Missing original exact journal write')
    capture['owned_state'] = raw
    floor = capture['source_floor']
    require(state['schema'] == 3 and all(not floor[k] or state[k] for k in (
                'legacy_rate_history_unknown', 'legacy_policy_history_unknown'))
            and all(state[k] >= floor[k] for k in ('interval','page_interval','rate_limit_until',
                                                   'rate_limit_strikes','observed_at','completed')),
            'Retained timing or legacy history weakened')
    if state['in_flight'] or state['blocked'] or adapter._PUBLISHER_GATE_FAILED or not adapter._STATE_TRUSTED:
        status = 'unresolved_do_not_resume'
    else:
        status = 'settled_candidate_requires_separate_production_handoff'
    return {'classification': status, 'state_sha256': digest(canonical(state)),
            'journal_sha256': digest(raw), 'pending': state['in_flight'],
            'additional_hold_count': int(state['blocked'])}


def consume(adapter, records, stamp):
    adapter.validate_records(records, published=not records)
    metadata = adapter.metadata(stamp, {}, records, checked_at=stamp)
    response = subprocess.run(['node', str(PACKET / 'consumer.mjs')], input=canonical({
        'records': records, 'metadata': metadata,
    }), capture_output=True, timeout=30, check=True)
    result = json.loads(response.stdout)
    require(result['count'] == len(records) and result['ids'] == sorted(r['id'] for r in records)
            and result['sources'] == ['dk-fulbright-center'], 'Consumer did not admit the exact original records')
    return result


def assess(adapter, capture):
    records = json.loads(capture['stdout'])
    adapter.validate_records(records)
    require(records and len({r['id'] for r in records}) == len(records), 'Incomplete original output')
    events = capture['requests']
    require(events and all(x['status'] == 200 and x['completion'] is not None and
                           'read_error' not in x and 'close_error' not in x for x in events),
            'Native collection lacks clean actual responses')
    gaps = [b['start']-a['completion'] for a,b in zip(events,events[1:])]
    require(gaps and all(gap >= events[i+1]['interval'] for i,gap in enumerate(gaps)),
            'Observed requests violated physical pacing')
    require(all(r['wall_start'] >= r['reservation'] and r['start'] >= r['startup_quiet_until']
                and r['request_id'] and any(w.get('acknowledged') and
                w['after']['completed_request_id'] == r['request_id'] and
                w['after']['request_outcome'] == 'response_assessed' for w in capture['persistence'])
                for r in events), 'A real request lacks its original completion receipt')
    inventory = [r for r in events if r['inventory']]
    require(all(b['start'] - a['completion'] >= b['page_gap'] for a,b in zip(inventory,inventory[1:]))
            and all(sum(r['start']-60 < u['start'] <= r['start'] for u in events) <= 5 for r in events),
            'Inventory pacing or rolling start limit exceeded')
    documents = {}
    for event in events:
        if urllib.parse.urlsplit(event['url']).path == '/robots.txt':
            continue
        headers = Message()
        for key, value in event['headers']:
            headers[key] = value
        document = b''.join(event['segments']).decode(headers.get_content_charset() or 'utf-8')
        require(event['url'] not in documents or documents[event['url']] == document,
                'One publisher URL delivered inconsistent bodies')
        documents[event['url']] = document
    spec = spec_from_file_location('denmark490_inert_replay', SOURCE)
    replay = module_from_spec(spec)
    spec.loader.exec_module(replay)
    replay.utc_now = lambda: records[0]['last_checked_at']
    replay.check_robots = lambda url: 12.2
    visited = set()
    def captured_fetch(url, **kwargs):
        visited.add(url)
        return documents[url]
    def no_access(*args, **kwargs):
        raise AssertionError('Native replay cannot make network calls')
    replay._OPENER.open = no_access
    replay.fetch_source = captured_fetch
    replay.fetch_inventory_page = lambda fetch, *args, **kwargs: fetch(*args, inventory=True, **kwargs)
    def material(rows):
        volatile = {'created_at','updated_at','first_seen_at','last_seen_at','last_checked_at'}
        return {r['id']: {k:v for k,v in r.items() if k not in volatile} for r in rows}
    require(material(replay.collect_programme()) == material(records) and visited == set(documents),
            'Full genuine parser frontier or output differs on independent replay')
    stamp = adapter.utc_now()
    visible = adapter.remove_expired_records(records, stamp)
    results = {'raw': consume(adapter, records, stamp), 'filtered': consume(adapter, visible, stamp)}
    expired = [record for record in records if record not in visible]
    if expired:
        empty = adapter.remove_expired_records(expired, stamp)
        require(empty == [], 'All-expired actual subset did not become empty')
        results['valid_empty'] = consume(adapter, empty, stamp)
    capture['consumer'] = results
    return {'records': len(records), 'filtered_records': len(visible),
            'actual_responses': len(events), 'complete_replayed_documents': len(documents), 'minimum_gap': min(gaps)}

def write_once(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def main():
    output = Path(os.environ['RUNNER_TEMP']) / 'denmark490-native'
    output.mkdir(mode=0o700)
    capture = {'requests': [], 'persistence': [], 'observer_errors': [], 'native_exit': None}
    proof = {'classification': 'not_started', 'installed_authority': None, 'publisher_requests': 0,
             'publisher_pause_must_be_retained': True}
    custody = escrow = None
    try:
        custody = Custody((PACKET / 'scope.json').read_bytes())
        proof['installed_authority'] = custody.launch()
        upload_cipher(b'Denmark 490 bound encryption fixture')
        private, anchor = verify_anchor(custody)
        proof['escrow_anchor'] = anchor
        escrow = Escrow(proof['installed_authority'], upload_cipher,
                        lambda data: openssl('dgst','-sha256','-sign',str(private),input=data))
        adapter = load_adapter(custody, capture)
        instrument(adapter, custody, capture, escrow)
        with adapter.execution():
            test_original(capture)
            try:
                proof['state'] = final_state(adapter, capture)
            except Exception as error:
                capture['observer_errors'].append(('state', type(error).__name__))
                proof['state'] = {'classification': 'unverified_do_not_resume', 'state_sha256': None}
            if capture['native_exit'] == 0:
                try:
                    proof['facts'] = assess(adapter, capture)
                except Exception as error:
                    capture['observer_errors'].append(('consumer', type(error).__name__, str(error)))
    except BaseException as error:
        capture['run_error'] = {'type': type(error).__name__, 'message': str(error)}
        proof['failure_type'] = type(error).__name__
        if proof.get('state', {}).get('classification') == 'settled_candidate_requires_separate_production_handoff':
            proof['state']['classification'] = 'unverified_cleanup_do_not_resume'
        if not capture['requests']:
            proof['failure_reason'] = str(error)
    finally:
        proof['publisher_requests'] = len(capture['requests'])
        proof['native_exit'] = capture['native_exit']
        proof['classification'] = 'held_for_separate_reviewer' if capture['requests'] else 'no_publisher_opener_observed'
        proof['observer_errors'] = [entry[:2] for entry in capture['observer_errors']]
        if escrow is not None:
            try:
                state = proof.get('state', {})
                escrow.finish({'state': state, 'publisher_requests': proof['publisher_requests'],
                               'native_exit': proof['native_exit'], 'failure_type': proof.get('failure_type'),
                               'observer_errors': proof['observer_errors']})
                proof['escrow'] = {'sequence': escrow.sequence, 'terminal_cipher_sha256': escrow.previous,
                                   'last_journal_sha256': escrow.journal_sha256}
            except Exception as error:
                proof['escrow_failure'] = type(error).__name__
        if proof['installed_authority'] is not None:
            plaintext = json.dumps({'capture': capture, 'proof': proof}, default=json_bytes, sort_keys=True,
                                   allow_nan=False).encode()
            try:
                encrypted = upload_cipher(plaintext)
                write_once(output / 'evidence.cms', encrypted)
                proof['encrypted_sha256'] = digest(encrypted)
                proof['plaintext_sha256'] = digest(plaintext)
            except Exception as error:
                proof['encrypted_failure'] = type(error).__name__
        write_once(output / 'manifest.json', canonical(proof))
        print(json.dumps(proof, sort_keys=True))
    return int(not (capture['native_exit'] == 0 and proof.get('facts') and proof.get('encrypted_sha256')
                   and proof.get('escrow') and not proof.get('failure_type') and not proof['observer_errors']
                   and proof.get('state', {}).get('classification') == 'settled_candidate_requires_separate_production_handoff'))


if __name__ == '__main__':
    raise SystemExit(main())
