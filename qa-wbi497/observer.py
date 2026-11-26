"""Capture the original WBI native test and preserve its actual journal in custody."""
import base64
import contextlib
import copy
import gzip
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import ModuleType
import urllib.error
import urllib.parse

from authority import Custody, ROOT, canonical, digest, require
from escrow import Escrow
from anchor import openssl, verify_anchor

SOURCE = ROOT / 'adapters/be-wbi/adapter.py'
PACKET = ROOT / 'qa-wbi497'
RECIPIENT = PACKET / 'reviewer.pem'
TARGET = 'https://www.wbi.be/fr/actualites/emploi-chargee-projet-wallonie-bruxelles-campus'


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
        (b'    hosted = os.environ.get("GITHUB_ACTIONS") == "true"\n    current = current_run_identity() if hosted else (None, None)',
         b'    hosted = False\n    current = (None, None)'),
        (b'    if (\n        os.environ.get("GITHUB_ACTIONS") == "true"\n        or _LOCAL_FIRST_ACCESS_CHECKED\n    ):\n        return\n    latest = latest_family_run()',
         b'    _QA_PUBLISHER_GUARD()\n    if _LOCAL_FIRST_ACCESS_CHECKED:\n        return\n    latest = latest_family_run()'),
        (b'        authenticate_current_job()\n    else:\n        local_workflow_definition()',
         b'        _QA_PUBLISHER_GUARD()\n    else:\n        local_workflow_definition()'),
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
    adapter._QA_PUBLISHER_GUARD = custody.before_publisher
    old = sys.modules.get('adapter')
    require(old is None, 'Unexpected preloaded adapter')
    sys.modules['adapter'] = adapter
    exec(compile(executable, str(SOURCE), 'exec'), adapter.__dict__)
    return adapter


def instrument(adapter, custody, capture, escrow):
    original_open = adapter._OPENER.open
    original_download = adapter.download_inert_artifact
    original_write = adapter.write_budget
    floor = custody.production

    def download(identity, artifact, sha256=None, **kw):
        require(identity == (floor['run'], 1, 'be-wbi') and artifact == floor['artifact']
                and sha256 == 'sha256:' + floor['archive_sha256'] and not kw, 'Unexpected restoration ownership')
        state = original_download(identity, artifact, sha256)
        require(digest(canonical(state)) == floor['state_sha256'] and state['schema'] == 4
                and state['blocked'] is True and state['in_flight'] is False
                and not state['refusal_scope']['additional_holds'], 'Inherited refusal scope differs')
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

    allowed = ('https://www.wbi.be/robots.txt', TARGET)
    require(list(allowed) == custody.scope['allowed'] and TARGET == custody.scope['target']
            and custody.scope['node'] == '25163', 'Evidence route scope differs')
    original_physical = adapter.physical_request_bytes
    def scoped_physical(url, *args, **kwargs):
        if not kwargs.get('infrastructure') or urllib.parse.urlsplit(url).hostname in adapter._PUBLISHER_HOSTS:
            count = len(capture['requests'])
            require(not kwargs.get('infrastructure') and count < len(allowed) and url == allowed[count]
                    and kwargs.get('method', 'GET') == 'GET', 'Unapproved publisher operation')
        return original_physical(url, *args, **kwargs)
    adapter.physical_request_bytes = scoped_physical

    def opened(request, *args, **kwargs):
        url = request.full_url
        parts = urllib.parse.urlsplit(url)
        if parts.hostname not in adapter._PUBLISHER_HOSTS:
            require(parts.scheme == 'https' and not parts.username and not parts.password and
                    (parts.hostname == 'api.github.com' or
                     re.fullmatch(r'productionresultssa[0-9]+\.blob\.core\.windows\.net', parts.hostname or '')),
                    'Foreign opener outside GitHub custody')
            return original_open(request, *args, **kwargs)
        count = len(capture['requests'])
        require(count < len(allowed) and url == allowed[count] and request.get_method() == 'GET',
                'Unexpected final physical URL')
        custody.before_publisher()
        headers = dict((k.casefold(), v) for k, v in request.header_items())
        require(headers.get('user-agent') == adapter._USER_AGENT and 'authorization' not in headers,
                'Outbound publisher identity changed')
        require(capture.get('source_floor') is not None, 'Publisher open without predecessor')
        raw = adapter._JOURNAL_ACK[1]
        escrow.require_pending(raw)
        state = json.loads(raw)
        require(state['in_flight'] and state['starts'], 'Physical opener lacks an acknowledged reservation')
        event = {'escrow_sequence': escrow.sequence, 'url': url, 'start': time.monotonic(), 'wall_start': time.time(), 'completion': None,
                 'startup_quiet_until': adapter._STARTUP_QUIET_UNTIL, 'request_headers': headers,
                 'reservation': state['starts'][-1], 'interval': state['interval'], 'page_gap': state['page_gap'],
                 'segments': [], 'inventory': bool(adapter._INVENTORY_ACTIVE)}
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
                    descriptor = adapter.locked_journal_authority()
                    size = os.fstat(descriptor).st_size
                    require(0 <= size <= 16384, 'Journal capture exceeds original bound')
                    item['journal'] = os.pread(descriptor, size + 1, 0)
                    require(len(item['journal']) == size, 'Journal read raced')
                except Exception as error:
                    capture['observer_errors'].append(('persistence', type(error).__name__))
        # A checkpoint must be emitted after the original durable write and
        # before the collector can proceed to any physical opener.
        raw = item.get('journal')
        try:
            require(item.get('acknowledged') and raw is not None and adapter._JOURNAL_ACK[1] == raw
                    and json.loads(raw) == state, 'Escrow does not match the original acknowledged write')
            item['escrow_sequence'] = escrow.acknowledge(
                raw, pending=state['in_flight'], opener_count=len(capture['requests']))
        except BaseException:
            capture['observer_errors'].append(('escrow', 'JournalCheckpointFailure'))
            raise
        return result


    adapter.download_inert_artifact = download
    adapter._OPENER.open = opened
    adapter.write_budget = write


def capture_article(adapter, capture):
    try:
        adapter.prepare_collection()
        adapter.start_source_phase()
        root = adapter.page(TARGET)
        node, title, body = adapter.wbi_article(root, TARGET)
        require(node == '25163' and title.strip() and body.text().strip(),
                'Original WBI article parser did not verify the pinned node')
        capture['article'] = {'node': node, 'title': title, 'text': body.text()}
        capture['native_exit'] = 0
    except BaseException as error:
        capture['native_exit'] = 1
        capture['article_failure'] = type(error).__name__

def final_state(adapter, capture):
    if not capture['persistence'] or adapter._JOURNAL_ACK is None or capture.get('source_floor') is None:
        return {'classification': 'no_new_verified_state', 'state_sha256': None}
    with adapter.locked_budget():
        state = adapter.load_budget()
        descriptor = adapter.locked_journal_authority()
        size = os.fstat(descriptor).st_size
        require(0 < size <= 16384, 'Final state is unbounded')
        raw = os.pread(descriptor, size + 1, 0)
        require(len(raw) == size and adapter._JOURNAL_ACK[1] == raw and json.loads(raw) == state,
                'State is not the last durable acknowledgement')
        require(adapter.validate_budget(copy.deepcopy(state)) == state, 'Final budget schema differs')
    require(capture['persistence'][-1].get('acknowledged') is True
            and capture['persistence'][-1].get('journal') == raw, 'Missing original exact journal write')
    capture['owned_state'] = raw
    scope = state['refusal_scope']
    floor = capture['source_floor']
    require(state['schema'] == 4 and state['blocked'] is True and state['legacy_access_history_unknown'] is True
            and scope['decision'] == floor['refusal_scope']['decision']
            and scope['extensions'] == floor['refusal_scope']['extensions']
            and scope['initial_refusal'] == floor['refusal_scope']['initial_refusal']
            and scope['original_journal_base64'] == floor['refusal_scope']['original_journal_base64']
            and all(state[key] >= floor[key] for key in ('until', 'interval', 'page_gap', 'rate_limit_until',
                                                        'completed', 'inventory_completed', 'observed_429_since_transition')),
            'Publisher scope or retained timing weakened')
    if state['in_flight'] or adapter._PUBLISHER_GATE_FAILED or not adapter._STATE_TRUSTED:
        status = 'unresolved_do_not_resume'
    elif scope['additional_holds']:
        status = 'additional_hold_do_not_resume'
    else:
        status = 'settled_candidate_requires_separate_production_handoff'
    return {'classification': status, 'state_sha256': digest(canonical(state)),
            'journal_sha256': digest(raw), 'pending': state['in_flight'],
            'additional_hold_count': len(scope['additional_holds'])}


def assess(capture):
    rows = capture['requests']
    require([row['url'] for row in rows] == ['https://www.wbi.be/robots.txt', TARGET]
            and rows[1]['status'] == 200 and all(row['completion'] is not None for row in rows)
            and not any(row.get(key) for row in rows for key in ('open_error','read_error','close_error'))
            and rows[1]['start'] - rows[0]['completion'] >= max(12.2, rows[1]['interval'])
            and rows[0]['start'] >= rows[0]['startup_quiet_until']
            and capture['article']['node'] == '25163', 'Incomplete or unpaced role evidence')
    return {'node': '25163', 'body_sha256': digest(b''.join(rows[1]['segments'])),
            'requests': len(rows), 'classification': 'awaiting_independent_review'}

def write_once(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def main():
    output = Path(os.environ['RUNNER_TEMP']) / 'wbi497-native'
    output.mkdir(mode=0o700)
    capture = {'requests': [], 'persistence': [], 'observer_errors': [], 'native_exit': None}
    proof = {'classification': 'not_started', 'installed_authority': None, 'publisher_requests': 0,
             'publisher_pause_must_be_retained': True}
    custody = escrow = None
    try:
        custody = Custody((PACKET / 'scope.json').read_bytes())
        proof['installed_authority'] = custody.launch()
        subprocess.run(['openssl', 'x509', '-in', str(RECIPIENT), '-checkend', '86400', '-noout'],
                       check=True, timeout=10, capture_output=True)
        upload_cipher(b'WBI 497 bound encryption fixture')
        private, anchor = verify_anchor(custody)
        proof['escrow_anchor'] = anchor
        escrow = Escrow(proof['installed_authority'], upload_cipher,
                        lambda data: openssl('dgst','-sha256','-sign',str(private),input=data))
        adapter = load_adapter(custody, capture)
        instrument(adapter, custody, capture, escrow)
        with adapter.execution():
            capture_article(adapter, capture)
            try:
                proof['state'] = final_state(adapter, capture)
            except Exception as error:
                capture['observer_errors'].append(('state', type(error).__name__))
                proof['state'] = {'classification': 'unverified_do_not_resume', 'state_sha256': None}
            if capture['native_exit'] == 0:
                try:
                    proof['facts'] = assess(capture)
                except Exception as error:
                    capture['observer_errors'].append(('article', type(error).__name__, str(error)))
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
