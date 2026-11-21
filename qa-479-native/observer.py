"""Capture the original Fulbright Germany native test and preserve its actual journal in custody."""
import base64
import contextlib
import gzip
import io
import json
import os
import re
from pathlib import Path
import importlib.util
import unittest
import subprocess
import sys
import time
import urllib.error
import urllib.parse

from authority import Custody, ROOT, canonical, digest, require
from escrow import Escrow
from anchor import openssl, verify_anchor

SOURCE = ROOT / 'adapters/de-fulbright-germany/adapter.py'
TEST = SOURCE.with_name('test_adapter.py')
PACKET = ROOT / 'qa-479-native'
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



def original_case():
    spec = importlib.util.spec_from_file_location('germany_native_case', TEST)
    case = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(case)
    return case


def test_original(case, capture):
    stdout, stderr = io.StringIO(), io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(case)
    require(suite.countTestCases() == 1, 'Unexpected native case inventory')
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = unittest.TextTestRunner(stream=stderr).run(suite)
    capture.update(stdout=stdout.getvalue(), stderr=stderr.getvalue(), native_exit=int(not result.wasSuccessful()))


class ArchiveOpener:
    """Admit one authenticated predecessor download and its exact signed redirect."""
    endpoint = 'https://api.github.com/repos/YouthOpps/data-pipeline/actions/artifacts/14489820002/zip'

    def __init__(self, opener):
        self.opener = opener
        self.started = False
        self.destination = None

    def open(self, request, *args, **kwargs):
        require(request.get_method() == 'GET' and request.data is None, 'Unexpected archive method')
        headers = {k.lower(): v for k, v in request.header_items()}
        if request.full_url == self.endpoint:
            require(not self.started and headers.get('authorization') == 'Bearer ' + os.environ['GITHUB_TOKEN'],
                    'Unexpected archive authority')
            self.started = True
            try:
                response = self.opener(request, *args, **kwargs)
            except urllib.error.HTTPError as error:
                self.record_redirect(error)
                raise
            self.record_redirect(response)
            return response
        require(self.destination is not None and request.full_url == self.destination
                and not {'authorization', 'cookie', 'proxy-authorization'}.intersection(headers),
                'Unauthorized artifact redirect')
        self.destination = None
        return self.opener(request, *args, **kwargs)

    def record_redirect(self, response):
        value = response.headers.get('Location', '')
        parsed = urllib.parse.urlsplit(value)
        require(response.status == 302 and parsed.scheme == 'https' and not parsed.username
                and not parsed.password and parsed.port is None and not parsed.fragment
                and re.fullmatch(r'productionresultssa[0-9]+\.blob\.core\.windows\.net', parsed.hostname or '')
                and parsed.path.startswith('/actions-results/') and bool(parsed.query),
                'Unrecognized signed GitHub artifact destination')
        self.destination = value


def instrument(adapter, custody, capture, escrow):
    original_open = adapter._OPENER.open
    archive = ArchiveOpener(original_open)
    original_save = adapter.save_budget
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
        host = urllib.parse.urlsplit(url).hostname
        if host != 'www.fulbright.de':
            return archive.open(request, *args, **kwargs)
        require(request.get_method() == 'GET' and request.data is None,
                'Unexpected adapter transport outside authorized publisher GETs')
        require(url in {adapter.SOURCE_URL, 'https://www.fulbright.de/robots.txt'},
                'URL outside exact native collection scope')
        headers = {k.casefold(): v for k, v in request.header_items()}
        require(headers.get('user-agent') == adapter._USER_AGENT and 'authorization' not in headers,
                'Publisher identity changed')
        raw = capture['last_journal']
        require(capture.pop('pending_authority', None) == digest(raw), 'Missing current custody reservation')
        escrow.require_pending(raw)
        state = json.loads(raw)
        require(state['in_flight'] is True and state['starts'], 'Missing durable reservation')
        event = {'url': url, 'start': time.monotonic(), 'wall_start': time.time(), 'completion': None,
                 'interval': state['interval'], 'segments': [], 'headers_sent': headers,
                 'checkpoint': escrow.sequence}
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

    def save(state):
        previous = capture.get('last_journal')
        reservation = state['in_flight'] and previous is not None and not json.loads(previous)['in_flight']
        if reservation:
            # This is after publisher_pace has waited and before its durable
            # reservation. A revoked grant leaves the last settled state intact.
            custody.before_publisher()
        original_save(state)
        raw = adapter.journal_read()
        require(json.loads(raw) == state, 'Durable write differs')
        escrow.acknowledge(raw, pending=state['in_flight'], opener_count=len(capture['requests']))
        capture['last_journal'] = raw
        if reservation:
            capture['pending_authority'] = digest(raw)

    adapter._OPENER.open = opened
    adapter.save_budget = save


def final_state(adapter, capture):
    with adapter.locked_budget():
        raw = adapter.journal_read()
        state = adapter.load_budget()
        require(raw == capture['last_journal'] and json.loads(raw) == state, 'Final journal changed')
    require(state['legacy_rate_history_unknown'] and state['interval'] >= 12.2
            and state['page_interval'] >= 30, 'Original limits differ')
    capture['owned_state'] = raw
    settled = not (state['blocked'] or state['in_flight'] or adapter._GUARD_FATAL
                   or adapter._PUBLISHER_GATE_FAILED or not adapter._STATE_TRUSTED)
    return {'classification': 'settled_candidate_requires_separate_production_handoff' if settled
            else 'unresolved_do_not_resume', 'state_sha256': digest(canonical(state)),
            'journal_sha256': digest(raw), 'pending': state['in_flight']}


def consume(adapter, records, stamp):
    adapter.validate_records(records, allow_empty=not records)
    metadata = adapter.metadata(stamp, {}, records)
    payload = canonical({'records': records, 'metadata': metadata})
    raw = subprocess.check_output(['node', str(PACKET / 'consumer.mjs')], input=payload, timeout=30)
    result = json.loads(raw)
    require(result['ids'] == sorted(r['id'] for r in records)
            and result['count'] == len(records) and result['sources'] == [adapter.SOURCE_ID],
            'Actual website rejected original record set')
    return result


def assess(adapter, capture):
    raw = json.loads(capture['stdout'])
    stamp = adapter.utc_now()
    require(isinstance(raw, list) and len(raw) == 1, 'Unexpected native inventory')
    requests = capture['requests']
    require([r['url'] for r in requests] == ['https://www.fulbright.de/robots.txt', adapter.SOURCE_URL],
            'Unexpected physical request inventory')
    for row in requests:
        require(row['completion'] is not None and row['completion'] >= row['start'],
                'Incomplete physical response evidence')
    require(requests[1]['start'] - requests[0]['completion'] >= requests[1]['interval'],
            'Publisher completion gap too short')
    return {'raw': consume(adapter, raw, stamp),
            'filtered': consume(adapter, adapter.remove_expired_records(raw, stamp), stamp)}
def write_once(path, raw):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(raw)
        file.flush()
        os.fsync(file.fileno())


def main():
    output = Path(os.environ['RUNNER_TEMP']) / 'germany479-native'
    output.mkdir(mode=0o700)
    capture = {'requests': [], 'persistence': [], 'observer_errors': [], 'native_exit': None}
    proof = {'classification': 'not_started', 'installed_authority': None, 'publisher_requests': 0,
             'publisher_pause_must_be_retained': True}
    custody = escrow = None
    try:
        custody = Custody((PACKET / 'scope.json').read_bytes())
        proof['installed_authority'] = custody.launch()
        upload_cipher(b'Fulbright Germany 479 encryption test')
        private, anchor = verify_anchor(custody)
        proof['escrow_anchor'] = anchor
        escrow = Escrow(proof['installed_authority'], upload_cipher,
                        lambda data: openssl('dgst','-sha256','-sign',str(private),input=data))
        from custody_reader import install_native
        case = original_case()
        adapter = case.adapter
        install_native(adapter, custody, capture)
        instrument(adapter, custody, capture, escrow)
        try:
            test_original(case, capture)
            proof['state'] = final_state(adapter, capture)
            if capture['native_exit'] == 0:
                proof['facts'] = assess(adapter, capture)
        finally:
            adapter.release_collection()
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
