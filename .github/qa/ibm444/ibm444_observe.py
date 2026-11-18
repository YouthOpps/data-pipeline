import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import pathlib
import runpy
import subprocess
import sys
import time
import urllib.error
import urllib.parse

from ibm444_bridge import configure

root = pathlib.Path('adapters/ibm').resolve()
sys.path.insert(0, str(root))
import adapter

assert 'DATA_SOURCE_TOKEN' not in os.environ
helper_root = pathlib.Path(__file__).resolve().parent
scope = json.loads((helper_root / 'scope.json').read_bytes())
assert hashlib.sha256((root / 'adapter.py').read_bytes()).hexdigest() == scope['adapter_sha256']
assert hashlib.sha256((root / 'test_adapter.py').read_bytes()).hexdigest() == scope['test_sha256']
assert scope['consumer_revision'] == 'b8228fdd28e18a5e0cccec007ccfe04052cdfe0a'
certificate = helper_root / 'reviewer-public.pem'
public_cert = certificate.read_bytes()
assert hashlib.sha256(public_cert).hexdigest() == scope['review_public_pem_sha256']
assert 0 < len(public_cert) <= 8192
subprocess.run(
    ['openssl', 'x509', '-in', str(certificate), '-noout', '-checkend', '3600'],
    capture_output=True, check=True, timeout=15,
)
runner_temp = pathlib.Path(os.environ['RUNNER_TEMP'])


def seal(payload):
    return subprocess.run(
        ['openssl', 'cms', '-encrypt', '-binary', '-aes-256-gcm',
         '-outform', 'DER', str(certificate)],
        input=payload, capture_output=True, check=True, timeout=90,
    ).stdout


assert seal(b'IBM #444 encrypted capture preflight')
qa_owner_reads = []
with adapter.execution():
    with adapter.phase('qa-admission', 90):
        configure(adapter, qa_owner_reads, scope)
capture = {'requests': [], 'persistence': [], 'observer_errors': [],
           'purposes': [], 'fetches': {}, 'inventory': None}
original_open = adapter._OPENER.open
original_save = adapter.save_budget
original_execution = adapter.execution
original_request = adapter.request_bytes
original_fetch = adapter.fetch_public
original_pages = adapter.read_pages


def observe_response(response, entry):
    entry['status'] = response.status
    entry['response_headers'] = list(response.headers.items())
    original_read, original_close = response.read, response.close
    def read(*args, **kwargs):
        try:
            segment = original_read(*args, **kwargs)
        except BaseException as error:
            partial = getattr(error, 'partial', b'')
            if isinstance(partial, bytes):
                entry['segments'].append(partial)
            entry['read_error'] = type(error).__name__
            raise
        entry['segments'].append(segment)
        return segment
    def close(*args, **kwargs):
        try:
            return original_close(*args, **kwargs)
        finally:
            if entry['completion'] is None:
                entry['completion'] = time.monotonic()
    response.read, response.close = read, close


def open_observed(request, *args, **kwargs):
    publisher = urllib.parse.urlsplit(request.full_url).hostname in {
        'ibm.com', 'www.ibm.com'}
    if not publisher:
        return original_open(request, *args, **kwargs)
    purpose = capture['purposes'][-1] if capture['purposes'] else None
    entry = {'url': request.full_url, 'start': time.monotonic(),
             'completion': None, 'segments': [], 'purpose': purpose,
             'request_headers': dict(request.header_items())}
    capture['requests'].append(entry)
    try:
        response = original_open(request, *args, **kwargs)
    except urllib.error.HTTPError as error:
        observe_response(error, entry)
        raise
    except BaseException as error:
        entry['completion'] = time.monotonic()
        entry['error'] = type(error).__name__
        raise
    observe_response(response, entry)
    return response


def request_observed(url, *args, **kwargs):
    capture['purposes'].append({
        'publisher': kwargs.get('publisher', False),
        'inventory': kwargs.get('inventory', False),
        'robots': kwargs.get('robots', False)})
    try:
        return original_request(url, *args, **kwargs)
    finally:
        capture['purposes'].pop()


def fetch_observed(url, *args, **kwargs):
    result = original_fetch(url, *args, **kwargs)
    capture['fetches'][url] = result
    return result


def pages_observed():
    result = original_pages()
    capture['inventory'] = result
    return result


def save_observed(state):
    event = {'before': copy.deepcopy(state)}
    capture['persistence'].append(event)
    try:
        result = original_save(state)
    except BaseException as error:
        event['error'] = type(error).__name__
        raise
    event['after'] = copy.deepcopy(state)
    try:
        if adapter._JOURNAL_FD is not None:
            event['journal'] = os.pread(adapter._JOURNAL_FD, 16385, 0)
    except BaseException as error:
        capture['observer_errors'].append(type(error).__name__)
    return result


@contextlib.contextmanager
def execution_observed():
    with original_execution():
        try:
            yield
        finally:
            primary = sys.exc_info()[0] is not None
            try:
                with adapter.phase('export', adapter.EXPORT_TIMEOUT):
                    adapter.export_family_artifact()
            except BaseException:
                if not primary:
                    raise
                print('Owned-state export also failed', file=sys.stderr)


adapter._OPENER.open = open_observed
adapter.request_bytes = request_observed
adapter.fetch_public = fetch_observed
adapter.read_pages = pages_observed
adapter.save_budget = save_observed
adapter.execution = execution_observed
stdout, stderr = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
    try:
        runpy.run_path(str(root / 'test_adapter.py'), run_name='__main__')
    except SystemExit as error:
        outcome = error.code
    except BaseException as error:
        outcome = type(error).__name__
        print(type(error).__name__, file=sys.stderr)
    else:
        outcome = 0
summary = {}
if outcome == 0:
    records = json.loads(stdout.getvalue())
    adapter.validate_records(records)
    try:
        requests = capture['requests']
        assert requests and all(
            r['completion'] is not None for r in requests), (
            'Incomplete physical response ledger')
        assert all(r['completion'] >= r['start'] for r in requests), (
            'Nonmonotonic physical completion')
        assert all(r['purpose'] and r['purpose']['publisher']
                   for r in requests), (
            'Unknown physical publisher purpose')
        assert all({k.lower(): v for k, v in r['request_headers'].items()}
                   .get('user-agent') == adapter._USER_AGENT
                   for r in requests), (
            'Incorrect actual bot identity')
        gaps = [right['start'] - left['completion']
                for left, right in zip(requests, requests[1:])]
        maximum = max(sum(point['start'] - 60 < r['start'] <= point['start']
                          for r in requests) for point in requests)
        inventory_requests = [r for r in requests if r['purpose']['inventory']]
        page_gaps = [right['start'] - left['completion'] for left, right in
                     zip(inventory_requests, inventory_requests[1:])]
        assert all(gap >= 12.2 for gap in gaps) and maximum <= 5, (
            'Physical pacing or rolling allowance violation')
        assert all(gap >= 30 for gap in page_gaps), 'Inventory pacing violation'
        inventory = capture['inventory']
        assert inventory and set(r['url'] for r in records) == set(
            inventory['programmes']), 'Incomplete actual programme frontier'
        assert all(url in capture['fetches']
                   for url in inventory['documents']), (
            'Uncaptured actual source document')
        assert all(doc['url'] in inventory['documents'] for record in records
                   for doc in record['source_documents']), (
            'Missing actual record provenance')
        original_clock = adapter.utc_now
        adapter.utc_now = lambda: records[0]['last_checked_at']
        try:
            replay = adapter.parse_inventory(inventory)
        finally:
            adapter.utc_now = original_clock
        temporal = {'created_at', 'updated_at', 'first_seen_at', 'last_seen_at',
                    'last_checked_at'}
        material = lambda rows: {row['id']: {key: value for key, value in
                    row.items() if key not in temporal} for row in rows}
        assert material(replay) == material(records), (
            'Captured graph replay differs from native output')
        consumer = pathlib.Path(os.environ['RUNNER_TEMP']) / 'ibm-consumer.mjs'
        consumer.write_text(r'''import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const consumerUrl=pathToFileURL(process.env.IBM444_CONSUMER_FILE);
const {loadData}=await import(consumerUrl);
let input=''; for await (const part of process.stdin) input += part;
const value=JSON.parse(input), source='ibm', base='/ram/datas';
if(value.metadata.source!==source || value.records.some(r=>r.source!==source))
  throw new Error('Observer source identity mismatch');
const files=new Map([
  [`${base}/${source}/data.json`,JSON.stringify(value.records)],
  [`${base}/${source}/metadata.json`,JSON.stringify(value.metadata)]]);
fs.lstat=async file=>{
  if(file!==base&&!files.has(file))throw Error('Unexpected path');
  return {isSymbolicLink:()=>false};};
fs.readdir=async file=>{if(file!==base)throw Error('Unexpected root');
  return [{name:source,isSymbolicLink:()=>false,isDirectory:()=>true}];};
fs.readFile=async file=>{if(!files.has(file))throw Error('Missing input');
  return files.get(file);};
const result=await loadData(base);
const ids=result.opportunities.map(r=>r.id).sort();
if(JSON.stringify(ids)!==JSON.stringify(value.records.map(r=>r.id).sort()))
  throw Error('Incomplete consumer IDs');
console.log(JSON.stringify({count:ids.length,ids}));
''')
        stamp = adapter.utc_now()
        filtered = adapter.remove_expired_records(records, stamp)
        results = {}
        for name, rows in [('raw', records), ('filtered', filtered),
                           ('valid_empty', [])]:
            adapter.validate_records(rows, allow_empty=not rows)
            envelope = {'records': rows, 'metadata': adapter.metadata(
                stamp, {}, rows, checked_at=stamp)}
            result = subprocess.run(['node', str(consumer)],
                input=json.dumps(envelope), text=True, capture_output=True,
                timeout=30)
            assert result.returncode == 0, result.stderr[-500:]
            results[name] = json.loads(result.stdout)
        summary = {'consumer': results, 'expired_actual_count':
            len(records) - len(filtered), 'source_documents': sorted(
            inventory['documents']), 'complete_graph_replay': True,
            'minimum_physical_completion_gap': min(gaps) if gaps else None,
            'maximum_starts_rolling60': maximum,
            'minimum_inventory_completion_gap': min(page_gaps)
            if page_gaps else None, 'actual_bot_identity_verified': True}
    except BaseException as error:
        capture['observer_errors'].append(type(error).__name__)
ledger = [{key: value for key, value in request.items() if key not in {
    'segments', 'response_headers', 'request_headers'}}
    for request in capture['requests']]
owner_assertions_pass = len(qa_owner_reads) >= 2 and all(
    proof == qa_owner_reads[0] for proof in qa_owner_reads
)
checks = {
    'native_outcome': outcome,
    'requests': ledger,
    'persistence_attempts': len(capture['persistence']),
    'observer_errors': capture['observer_errors'],
    'checks': summary,
    'canonical_and_qa_owner_assertions_pass': owner_assertions_pass,
    'workflow_scope': qa_owner_reads,
    'consumer_revision': 'b8228fdd28e18a5e0cccec007ccfe04052cdfe0a',
    'adapter_sha256': hashlib.sha256((root / 'adapter.py').read_bytes()).hexdigest(),
    'test_sha256': hashlib.sha256((root / 'test_adapter.py').read_bytes()).hexdigest(),
    'capture_lifetime': 'RAM and encrypted short-retention review artifact',
}
print(json.dumps(checks, ensure_ascii=False))


def encode_memory_bytes(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


private_evidence = {
    'verified_scope': qa_owner_reads,
    'original_test_stdout': stdout.getvalue(),
    'original_test_stderr': stderr.getvalue(),
    'physical_requests': capture['requests'],
    'state_mutations_and_readbacks': capture['persistence'],
    'outcome': checks,
}
sealed = seal(json.dumps(private_evidence, default=encode_memory_bytes).encode())
assert sealed
path = runner_temp / 'ibm444-encrypted-native-evidence.der'
fd = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT, 0o600)
with os.fdopen(fd, 'wb') as audit:
    audit.write(sealed)
    audit.flush()
    os.fsync(audit.fileno())

raise SystemExit(
    0 if outcome == 0 and not capture['observer_errors'] and owner_assertions_pass else 1
)
