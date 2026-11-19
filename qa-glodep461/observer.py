"""Verify the original GLODEP collector in RAM and retain derived evidence."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from email.message import Message
from hashlib import sha256
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path.cwd()
QA = Path(__file__).resolve().parent
SOURCE = 'erasmus-mundus-glodep'
ADAPTER = ROOT / 'adapters' / SOURCE
SITE = ROOT / 'website'
WEBSITE_SHA = '2ed1f4f7c16c0a1b65c3e96c78c41c2532a04f23'
CONSUMER_SHA = 'bc9d9f9f83c58061c6d29f3fd3d6b04b56ad1b7503b12a009c2f91e24b4deef3'


def hash_bytes(data):
    return sha256(data).hexdigest()


def required(condition, message):
    if not condition:
        raise ValueError(message)


def load_adapter():
    spec = spec_from_file_location('adapter', ADAPTER / 'adapter.py')
    module = module_from_spec(spec)
    sys.modules['adapter'] = module
    spec.loader.exec_module(module)
    return module


def verify_checkout(binding, adapter):
    _, _, _, head, _ = binding
    require_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True, timeout=15).strip()
    required(head == require_head, 'Native checkout differs')
    for path in (ADAPTER / 'adapter.py', ADAPTER / 'test_adapter.py',
                 QA / 'observer.py', QA / 'consumer.mjs', ROOT / adapter.NATIVE_PATH):
        blob = subprocess.check_output(['git', 'show', f'{head}:{path.relative_to(ROOT)}'], timeout=15)
        required(blob == path.read_bytes(), 'Native checkout input differs')
    actual_site = subprocess.check_output(['git', '-C', str(SITE), 'rev-parse', 'HEAD'],
                                          text=True, timeout=15).strip()
    required(actual_site == WEBSITE_SHA
             and hash_bytes((SITE / 'scripts/data.mjs').read_bytes()) == CONSUMER_SHA,
             'Pinned website consumer differs')


def original_test():
    stdout, stderr = StringIO(), StringIO()
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            runpy.run_path(str(ADAPTER / 'test_adapter.py'), run_name='__main__')
    except Exception as error:
        return {'outcome': 1, 'error': type(error).__name__}, None
    return {'outcome': 0, 'stdout_sha256': hash_bytes(stdout.getvalue().encode()),
            'stderr_sha256': hash_bytes(stderr.getvalue().encode())}, json.loads(stdout.getvalue())


def replay_records(adapter, records):
    checked = datetime.fromisoformat(records[0]['last_checked_at'])
    target = adapter.AUDIT['requests'][-1]
    headers = Message()
    for key, value in target['headers']:
        headers[key] = value
    document = b''.join(target['segments']).decode(headers.get_content_charset() or 'utf-8', 'strict')
    replay = adapter.normalize(adapter.parse_call(document), checked)
    required(records == replay, 'Whole original response replay differs')
    filtered = adapter.expire(records, checked)
    boundary = datetime.fromisoformat(records[0]['deadline']).replace(
        tzinfo=adapter.DATE_ONLY_ZONE) + timedelta(days=1)
    expired = adapter.expire(records, boundary)
    required(not expired, 'Record survived its precise expiry')
    payload = {
        'raw': records, 'rawMetadata': adapter.metadata(checked, records, checked_at=checked),
        'filtered': filtered, 'filteredMetadata': adapter.metadata(checked, filtered, checked_at=checked),
        'expired': expired, 'expiredMetadata': adapter.metadata(boundary, expired, checked_at=boundary),
    }
    consumer = subprocess.run(
        ['node', str(QA / 'consumer.mjs'), str(SITE / 'scripts/data.mjs'), SOURCE],
        input=json.dumps(payload), capture_output=True, text=True, check=True, timeout=25,
    )
    outcome = json.loads(consumer.stdout)
    required(outcome == {'raw': len(records), 'filtered': len(filtered), 'expired': 0,
                         'wrong_source_rejected': True, 'consumer_sha256': CONSUMER_SHA},
             'Current website consumer reported different coverage')
    return outcome


def audit(adapter, terminal):
    first, second = adapter.AUDIT['requests']
    required([first['url'], second['url']] == list(adapter.URLS)
             and (first['status'] == 200 or 400 <= first['status'] < 500 and first['status'] != 429)
             and second['status'] == 200, 'Non-native publisher scope')
    evidence = []
    for event in (first, second):
        transport = event['transport']
        required(transport['statuses'] and transport['statuses'][-1] == event['status']
                 and transport['response_file_closed'] and not transport['opener_disconnected']
                 and event['segments'] and event['segments'][-1] == b''
                 and event.get('error') is None and event.get('read_error') is None
                 and event.get('close_error') is None
                 and event['request_headers'][0][1] == adapter.USER_AGENT,
                 'Publisher response or original request witness differs')
        body = b''.join(event['segments'])
        evidence.append({key: event[key] for key in ('url', 'status', 'request_id', 'start',
                          'completion', 'wall_start', 'wall_completion')}
                        | {'body_sha256': hash_bytes(body), 'response_bytes': len(body)})
    writes = adapter.AUDIT['persistence']
    required(writes and all(write['acknowledged'] and write['bytes'] == write['actual_journal_bytes']
                            for write in writes)
             and writes[-1]['bytes'] == adapter.canonical(terminal), 'Publisher state acknowledgement differs')
    for event in (first, second):
        reserved = [write for write in writes if write['after']['physical_id'] == event['request_id']
                    and write['after']['outcome'] == 'reserved' and write['after']['physical_pending']]
        settled = [write for write in writes if write['after']['completed_physical_id'] == event['request_id']
                   and write['after']['outcome'] == 'response_assessed']
        required(reserved and settled and reserved[0]['after']['target'] == event['url']
                 and reserved[0]['completed_monotonic'] <= event['start'] <= event['completion']
                 <= settled[-1]['monotonic'], 'Original opener and journal order differ')
    required(second['start'] - first['completion'] >= max(12.2, terminal['interval'])
             and terminal['completed_physical_id'] == second['request_id']
             and set(adapter.NATIVE_FLOOR['starts']).issubset(terminal['starts']),
             'Active physical spacing or retained floor differs')
    return {'requests': evidence, 'state_sha256': hash_bytes(adapter.canonical(terminal)),
            'persistence_acknowledgement_sha256': [hash_bytes(write['bytes']) for write in writes]}


def main():
    output = Path(os.environ['RUNNER_TEMP']) / 'glodep461-native-result'
    output.mkdir(mode=0o700)
    published = {'observer_outcome': 1, 'original_outcome': None, 'raw_response_bytes_stored': False}
    try:
        adapter = load_adapter()
        binding = adapter.native_binding()
        verify_checkout(binding, adapter)
        original, records = original_test()
        published.update(original_outcome=original['outcome'], original=original,
                         workflow_id=binding[0], run_id=binding[1], run_attempt=binding[2],
                         head_sha=binding[3], website_sha=WEBSITE_SHA,
                         publisher_requests=len(adapter.AUDIT['requests']),
                         adapter_sha256=hash_bytes((ADAPTER / 'adapter.py').read_bytes()),
                         test_sha256=hash_bytes((ADAPTER / 'test_adapter.py').read_bytes()),
                         native_floor_sha256=adapter.NATIVE_FLOOR_SHA256)
        terminal = adapter.retained_native_state(include_unsettled=True)
        terminal_bytes = adapter.canonical(terminal)
        with (output / 'acknowledged-state.json').open('xb') as artifact:
            artifact.write(terminal_bytes)
            artifact.flush()
            os.fsync(artifact.fileno())
        published.update(state_sha256=hash_bytes(terminal_bytes),
                         pending=terminal['in_flight'] or terminal['physical_pending'],
                         blocked=terminal['blocked'])
        required(original['outcome'] == 0 and not published['pending'] and not published['blocked'],
                 'Original collector did not complete an allowed collection')
        adapter.validate_records(records)
        observed = audit(adapter, terminal)
        consumer = replay_records(adapter, records)
        samples = [{key: record[key] for key in ('id', 'title', 'url', 'deadline', 'category', 'status')}
                   for record in records]
        published.update(observed, consumer=consumer, samples=samples, observer_outcome=0)
    except Exception as error:
        published['observer_error'] = type(error).__name__
    with (output / 'manifest.json').open('x') as artifact:
        json.dump(published, artifact, ensure_ascii=False, indent=2)
    return published['observer_outcome']


if __name__ == '__main__':
    sys.exit(main())
