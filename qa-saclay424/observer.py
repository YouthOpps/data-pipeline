"""Verify the original hosted PhD Track test and seal its complete evidence in RAM."""

import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path.cwd()
QA_DIR = ROOT / 'qa-saclay424'
SLUG = 'fr-paris-saclay-phd-track'
MODULE_DIR = ROOT / 'adapters' / SLUG
CERTIFICATE = QA_DIR / 'review-cert.pem'
WEBSITE_SHA = '493e53db44e0d00979f6da9e3b876e9bdb9b8f63'
CERT_SHA = 'c8bfa742ffcd2638a1926c8068b58c3f0cc9bcda7077b95ab3ac8a8ac5b01531'
DATA_MJS_SHA = 'bc9d9f9f83c58061c6d29f3fd3d6b04b56ad1b7503b12a009c2f91e24b4deef3'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def serial(value):
    if isinstance(value, bytes):
        return {'base64': base64.b64encode(value).decode('ascii')}
    raise TypeError('Unexpected original capture type ' + type(value).__name__)


def load_adapter():
    spec = importlib.util.spec_from_file_location('adapter', MODULE_DIR / 'adapter.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def consume(adapter, records, checked, *, wrong_source=False):
    metadata = adapter.metadata(checked, {}, records, checked_at=checked)
    if wrong_source:
        metadata['source'] = 'unapproved-source'
    result = subprocess.run(
        ['node', str(QA_DIR / 'consumer.mjs')],
        input=json.dumps({'records': records, 'metadata': metadata}, ensure_ascii=False),
        text=True, capture_output=True, timeout=30, check=False,
    )
    if wrong_source:
        assert result.returncode != 0, 'Wrong-source envelope was accepted'
        return {'rejected': True}
    if result.returncode != 0:
        raise RuntimeError('Current website consumer rejected the original output')
    observed = json.loads(result.stdout)
    if observed['ids'] != sorted(row['id'] for row in records) or observed['sources'] != [SLUG]:
        raise ValueError('Exact website consumer identity differs')
    return observed


def observed_collect(adapter):
    stdout, stderr = io.StringIO(), io.StringIO()
    old_argv, old_module = sys.argv, sys.modules.get('adapter')
    problem = None
    try:
        sys.argv = [str(MODULE_DIR / 'test_adapter.py')]
        sys.modules['adapter'] = adapter
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            runpy.run_path(sys.argv[0], run_name='__main__')
    except SystemExit as outcome:
        code = outcome.code
        result = int(code) if isinstance(code, int) else int(code is not None)
    except BaseException as error:
        result = 1
        problem = type(error).__name__
    else:
        result = 0
    finally:
        sys.argv = old_argv
        if old_module is None:
            sys.modules.pop('adapter', None)
        else:
            sys.modules['adapter'] = old_module
    return {'exit': result, 'error_type': problem,
            'stdout': stdout.getvalue(), 'stderr': stderr.getvalue()}


def audit_receipts(adapter, terminal_state):
    """Prove original sane custody, not fabricated attempts or empty acknowledgements."""
    wires = adapter.AUDIT['requests']
    writes = adapter.AUDIT['persistence']
    seed = adapter._QA_APPROVAL['seed']
    if (not writes or len(wires) != len(adapter.URLS)
            or [wire['url'] for wire in wires] != list(adapter.URLS)):
        raise ValueError('Original transport or acknowledged journal is incomplete')
    prior = seed
    last = None
    committed = []
    for event in writes:
        expected = adapter.canonical(event['after'])
        if (event['before'] != prior or event['intended'] != event['after']
                or event['RAM_state'] != event['after']
                or event['acknowledged'] is not True
                or expected != event['bytes'] or expected != event.get('actual_journal_bytes')
                or not event['monotonic'] <= event['completed_monotonic']
                or (last is not None and last > event['monotonic'])):
            raise ValueError('Original journal custody or acknowledgement differs')
        prior = event['after']
        last = event['completed_monotonic']
        committed.append(digest(expected))
    if (prior != terminal_state or any(terminal_state[key] for key in
            ('blocked', 'in_flight', 'physical_pending'))
            or not set(seed['starts']).issubset(terminal_state['starts'])
            or any(terminal_state[key] != seed[key] for key in
                   ('legacy_rate_history_unknown', 'legacy_policy_history_unknown'))):
        raise ValueError('Terminal journal does not preserve reviewed source history')
    observations = []
    previous = None
    for wire in wires:
        target, request_id = wire['url'], wire['request_id']
        segments = wire['segments']
        ua = [value for name, value in wire['request_headers'] if name.lower() == 'user-agent']
        status = wire['status']
        if (ua != [adapter.USER_AGENT] or not all(wire.get(key) is True for key in
                ('headers_complete', 'body_complete', 'closed'))
                or any(wire.get(key) is not None for key in
                       ('close_error', 'read_error', 'completion_clock_error', 'error'))
                or not segments or segments[-1] != b'' or b'' in segments[:-1]
                or (target == adapter.ENTRY and status != 200)
                or (target == adapter.ROBOTS and not (status == 200 or 400 <= status < 429
                                                      or 429 < status < 500))):
            raise ValueError('Original response bytes or closure do not qualify')
        body = b''.join(segments)
        reserved = [item for i, item in enumerate(writes)
                    if committed[i] == wire['reservation_sha256']
                    and item['before']['physical_id'] != request_id
                    and item['after']['target'] == target
                    and item['after']['physical_id'] == request_id
                    and item['after']['outcome'] == 'reserved'
                    and item['after']['physical_pending'] is True
                    and item['after']['in_flight'] is True]
        settled = [item for i, item in enumerate(writes)
                   if committed[i] == wire['settled_sha256']
                   and item['before']['physical_id'] == request_id
                   and item['before']['physical_pending'] is True
                   and item['after']['target'] == target
                   and item['after']['physical_id'] == request_id
                   and item['after']['completed_physical_id'] == request_id
                   and item['after']['outcome'] == 'response_assessed'
                   and not item['after']['in_flight']
                   and not item['after']['physical_pending']]
        if len(reserved) != 1 or len(settled) != 1:
            raise ValueError('Exact physical opener lacks a reserve and settle acknowledgement')
        reserve, settle = reserved[0], settled[0]
        started, completed = wire['actual_start'], wire['completion']
        wall_start, wall_complete = wire['actual_wall_start'], wire['wall_completion']
        reservation = wire['reserved_wall']
        if (reservation != reserve['after']['physical_started']
                or reservation not in terminal_state['starts']
                or not reservation <= wall_start <= wall_complete <= settle['after']['completed']
                or not reserve['completed_monotonic'] <= started <= completed <= settle['monotonic']):
            raise ValueError('Actual reserve, ACK, opener, close and settle order differs')
        if target == adapter.ROBOTS:
            policy = settle['after']['policy']
            if (policy['status'] != status or policy['body_sha256'] != digest(body)
                    or not policy['new_allowed'] or not policy['legacy_allowed']
                    or policy['unavailable'] != (status != 200)):
                raise ValueError('Original REP assessment differs from exact received bytes')
        if previous and started - previous[0] < max(12.2, previous[1]):
            raise ValueError('Actual original physical spacing failed')
        previous = completed, settle['after']['interval']
        observations.append({
            'id': request_id, 'url': target, 'status': status, 'closed': True,
            'bytes': len(body), 'body_sha256': digest(body),
            'response_headers_sha256': digest(adapter.canonical(wire['headers'])),
            'reservation_wall': reservation, 'actual_wall_start': wall_start,
            'actual_start': started, 'actual_completion': completed,
            'reservation_sha256': wire['reservation_sha256'],
            'settled_sha256': wire['settled_sha256'],
        })
    if len({wire['id'] for wire in observations}) != len(wires) or (
            observations[-1]['id'] != terminal_state['completed_physical_id']
            or observations[-1]['settled_sha256'] != committed[-1]):
        raise ValueError('Exact final closure and terminal custody differ')
    starts = [wire['actual_wall_start'] for wire in observations]
    rolling = max(sum(stamp - 60 < seen <= stamp for seen in [*seed['starts'], *starts])
                  for stamp in starts)
    if rolling > 5:
        raise ValueError('Original collector exceeded rolling publisher budget')
    return observations, committed, rolling


def assess(adapter, original, terminal):
    records = json.loads(original['stdout'])
    adapter.validate_records(records)
    wires, ack, rolling = audit_receipts(adapter, terminal)
    checked = records[0]['last_checked_at']
    replay = adapter.extract_records(
        b''.join(adapter.AUDIT['requests'][1]['segments']),
        adapter.datetime.fromisoformat(records[0]['created_at']),
    )
    if replay != records:
        raise ValueError('Complete original body replay differs from original stdout')
    eligible = adapter.remove_expired_records(records, checked)
    return {
        'qualified_original_scope': True, 'all_wire_receipts': wires,
        'acknowledgements': ack, 'actual_rolling_max': rolling,
        'complete_original_body_replay': True,
        'records': records, 'stdout_sha256': digest(original['stdout'].encode()),
        'consumer': {
            'raw': consume(adapter, records, checked),
            'filtered': consume(adapter, eligible, checked),
            'synthetic_valid_empty': consume(adapter, [], checked),
            'synthetic_wrong_source': consume(adapter, records, checked, wrong_source=True),
            'actual_expired_subset_exists': len(eligible) < len(records),
        },
    }


def seal(raw):
    result = subprocess.run(
        ['openssl', 'cms', '-encrypt', '-binary', '-outform', 'DER',
         '-aes-256-gcm', str(CERTIFICATE)],
        input=raw, capture_output=True, timeout=30, check=True,
    )
    if not result.stdout:
        raise RuntimeError('Missing authenticated ciphertext')
    return result.stdout


def save(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def main():
    output = Path(os.environ['RUNNER_TEMP']) / 'saclay424-native-evidence'
    output.mkdir(mode=0o700)
    report = {
        'schema': 1, 'repository': os.environ.get('GITHUB_REPOSITORY'),
        'run_id': os.environ.get('GITHUB_RUN_ID'), 'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
        'head_sha': os.environ.get('GITHUB_SHA'), 'original_outcome': None,
        'observer_outcome': 1, 'publisher_opener_entries': 0,
    }
    try:
        adapter = load_adapter()
        current = adapter.qa_workflow_binding()
        head = subprocess.check_output(
            ['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], timeout=15, text=True).strip()
        if head != current['head_sha']:
            raise ValueError('Native QA immutable checkout differs')
        for path in (MODULE_DIR / 'adapter.py', MODULE_DIR / 'test_adapter.py',
                     QA_DIR / 'observer.py', QA_DIR / 'consumer.mjs', CERTIFICATE,
                     ROOT / adapter._QA_PATH):
            relative = path.relative_to(ROOT)
            blob = subprocess.check_output(
                ['git', '-C', str(ROOT), 'show', f'{head}:{relative}'], timeout=15)
            if blob != path.read_bytes():
                raise ValueError('Original QA checkout blob differs')
        website = ROOT / 'website'
        actual_head = subprocess.check_output(
            ['git', '-C', str(website), 'rev-parse', 'HEAD'], timeout=20, text=True).strip()
        if (actual_head != WEBSITE_SHA
                or digest((website / 'scripts/data.mjs').read_bytes()) != DATA_MJS_SHA
                or digest(CERTIFICATE.read_bytes()) != CERT_SHA):
            raise ValueError('Pinned website or evidence certificate differs')
        seal(b'SACLAY424 original-evidence recipient preflight')
    except Exception as error:
        report['preflight_error'] = type(error).__name__
        save(output / 'manifest.json', json.dumps(report, indent=2).encode())
        return 1

    original = observed_collect(adapter)
    report.update(
        original_outcome=original['exit'], original_error=original['error_type'],
        publisher_opener_entries=sum(wire.get('actual_start') is not None for wire in adapter.AUDIT['requests']),
        adapter_sha256=adapter.source_sha(),
        test_sha256=digest((MODULE_DIR / 'test_adapter.py').read_bytes()),
        website_sha=WEBSITE_SHA, website_consumer_sha256=DATA_MJS_SHA,
        certificate_sha256=CERT_SHA, seed_sha256=adapter._QA_APPROVAL['seed_sha256'],
    )
    owned = None
    try:
        owned = adapter.retained_result()
        if owned is None or owned[0] != current:
            raise RuntimeError('Original run has no retained exact terminal state')
        report['terminal_state_sha256'] = digest(adapter.canonical(owned[1]))
        adapter.export_pacing(owned)
    except Exception as error:
        report['state_error'] = type(error).__name__
    checks = {}
    if original['exit'] == 0:
        try:
            if owned is None:
                raise ValueError('Successful original test lacks original journal custody')
            checks = assess(adapter, original, owned[1])
            report.update(checks)
        except Exception as error:
            report['evidence_error'] = type(error).__name__
    evidence = {
        'original_test': original, 'original_audit': adapter.AUDIT,
        'owned_terminal_state': owned[1] if owned is not None else None,
        'ownership': current, 'checks': checks,
    }
    try:
        raw = json.dumps(evidence, default=serial, sort_keys=True,
                         ensure_ascii=False, allow_nan=False).encode()
        ciphertext = seal(raw)
        save(output / 'evidence.cms', ciphertext)
        report['plaintext_sha256'] = digest(raw)
        report['encrypted_sha256'] = digest(ciphertext)
    except Exception as error:
        report['encrypted_evidence_error'] = type(error).__name__
    if (original['exit'] == 0 and checks and 'state_error' not in report
            and 'evidence_error' not in report and 'encrypted_sha256' in report):
        report['observer_outcome'] = 0
    save(output / 'manifest.json', json.dumps(report, indent=2, ensure_ascii=False).encode())
    return report['observer_outcome']


if __name__ == '__main__':
    sys.exit(main())
