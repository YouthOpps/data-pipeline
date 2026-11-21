"""Read the exact issue 479 attestation inside the reviewed native job."""
from datetime import datetime
import io
import json
import os
import re
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from authority import API, OPENER, REPOSITORY, USER, api, canonical, digest, matches, require

BRANCH = 'qa/479-germany-state-only-v3'
REF = 'refs/heads/' + BRANCH
WORKFLOW = '.github/workflows/verify-479-state-v3.yml'
FAILED = ((44846595786, 136667720087), (44652934438, 136012035069))
FAILED_HEAD = '11fa75c3e745c5e0d8c12ee8a5c9f8371417d764'


def zip_parts(raw, names):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(len(entries) == len(names) and set(archive.namelist()) == set(names), 'Unexpected custody archive members')
        for member in entries:
            require(member.file_size <= names[member.filename] and not member.flag_bits & 1
                    and not stat.S_ISLNK(member.external_attr >> 16), 'Unsafe custody archive member')
        return {name: archive.read(name) for name in names}


def artifact_bytes(artifact):
    request = urllib.request.Request(API + f'/actions/artifacts/{artifact["id"]}/zip', headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'], 'Cache-Control': 'no-cache',
    })
    try:
        response = OPENER.open(request, timeout=12)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        require(response.status == 302, 'Missing custody artifact redirect')
        target = response.headers['Location']
    uri = urllib.parse.urlsplit(target)
    require(uri.scheme == 'https' and uri.hostname and not uri.username and not uri.password
            and uri.port is None and uri.hostname.endswith(('.blob.core.windows.net', '.githubusercontent.com')),
            'Unexpected custody archive host')
    with OPENER.open(urllib.request.Request(target), timeout=20) as response:
        data = response.read(32769)
        require(response.status == 200 and len(data) == artifact['size_in_bytes'] <= 32768
                and artifact['digest'] == 'sha256:' + digest(data), 'Custody archive integrity failed')
    return data


def verified_attestation(adapter, custody):
    scope = custody.scope['attestation']
    require(isinstance(scope, dict) and set(scope) == {
        'workflow_id', 'sha', 'run_id', 'job_id', 'grant_comment_id', 'artifact_id',
        'archive_sha256', 'provenance_sha256',
    }, 'Actual state attestation is missing')
    for key in ('workflow_id', 'run_id', 'job_id', 'grant_comment_id', 'artifact_id'):
        require(type(scope[key]) is int and scope[key] > 0, 'Invalid attestation identity')
    require(re.fullmatch('[a-f0-9]{40}', scope['sha'])
            and all(re.fullmatch('[a-f0-9]{64}', scope[k]) for k in ('archive_sha256', 'provenance_sha256')),
            'Invalid attestation digest')
    run_id, sha, owner = scope['run_id'], scope['sha'], custody.production
    require(matches(api(f'/actions/workflows/{scope["workflow_id"]}'), {'id': scope['workflow_id'], 'path': WORKFLOW}),
            'Attestation workflow changed')
    runs = api(f'/actions/workflows/{scope["workflow_id"]}/runs?per_page=2')
    require(len(runs['workflow_runs']) == runs['total_count'] == 1 and runs['workflow_runs'][0]['id'] == run_id
            and runs['workflow_runs'][0]['run_attempt'] == 1, 'Attestation reused')
    run = api(f'/actions/runs/{run_id}/attempts/1')
    require(matches(run, {'id': run_id, 'workflow_id': scope['workflow_id'], 'head_sha': sha,
                          'path': WORKFLOW, 'head_branch': BRANCH, 'event': 'push', 'run_attempt': 1,
                          'status': 'completed', 'conclusion': 'success'})
            and matches(run['actor'], USER) and matches(run['triggering_actor'], USER)
            and run['repository']['full_name'] == run['head_repository']['full_name'] == REPOSITORY,
            'State-only run differs')
    jobs = api(f'/actions/runs/{run_id}/attempts/1/jobs?per_page=2')
    require(len(jobs['jobs']) == jobs['total_count'] == 1 and matches(jobs['jobs'][0], {
        'name': 'Attest exact Fulbright Germany history', 'id': scope['job_id'], 'run_id': run_id,
        'run_attempt': 1, 'head_branch': BRANCH, 'head_sha': sha, 'status': 'completed', 'conclusion': 'success',
    }), 'State-only job differs')
    grant = api(f'/issues/comments/{scope["grant_comment_id"]}')
    require(matches(grant['user'], USER) and grant['issue_url'] == API + '/issues/479'
            and grant['id'] == scope['grant_comment_id'] and grant['created_at'] == grant['updated_at'] <= run['created_at']
            and grant['body'] == f'Authorize one state-only Fulbright Germany attestation for {sha} on {REF} under #479. No publisher access or publication.',
            'State-only grant changed')
    listing = api(f'/actions/runs/{run_id}/artifacts?per_page=2')
    require(len(listing['artifacts']) == listing['total_count'] == 1, 'State-only artifact is ambiguous')
    item = listing['artifacts'][0]
    require(item == api(f'/actions/artifacts/{scope["artifact_id"]}') and matches(item, {
        'id': scope['artifact_id'], 'expired': False, 'name': f'germany479-attestation-{run_id}-1',
        'digest': 'sha256:' + scope['archive_sha256'],
    }) and matches(item['workflow_run'], {'id': run_id, 'head_sha': sha, 'head_branch': BRANCH}), 'State-only artifact differs')
    files = zip_parts(artifact_bytes(item), {'original.zip': 463, 'provenance.json': 16384})
    require(digest(files['original.zip']) == owner['archive_sha256']
            and digest(files['provenance.json']) == scope['provenance_sha256'], 'Attested bytes differ')
    provenance = json.loads(files['provenance.json'])
    require(matches(provenance, {
        'repository': REPOSITORY, 'workflow_id': scope['workflow_id'], 'workflow_path': WORKFLOW, 'ref': REF,
        'sha': sha, 'run_id': run_id, 'run_attempt': 1, 'job_id': scope['job_id'],
        'grant_comment_id': scope['grant_comment_id'], 'predecessor_archive_id': owner['artifact'],
        'predecessor_archive_sha256': owner['archive_sha256'],
    }), 'Attested owner differs')
    history = provenance['history']
    expected = [(r, j, FAILED_HEAD) for r, j in FAILED] + [(owner['run'], owner['job'], owner['head'])]
    require(matches(history, {'complete_exact_attempt_jobs': 63, 'strictly_adjacent': True, 'owner_id': owner['id'],
                              'owner_path': owner['path'], 'origin_traffic': 0})
            and [(r['run'], r['job'], r['head']) for r in history['chronology']] == expected
            and history['failed_run_artifact_counts'] == {str(r): 0 for r, _ in FAILED}, 'Attested history differs')
    for run, job_id in FAILED:
        jobs = api(f'/actions/runs/{run}/attempts/1/jobs?per_page=2')
        artifacts = api(f'/actions/runs/{run}/artifacts?per_page=2')
        require(len(jobs['jobs']) == jobs['total_count'] == 1 and matches(jobs['jobs'][0], {
            'id': job_id, 'run_id': run, 'head_sha': FAILED_HEAD, 'head_branch': 'main', 'name': 'collect',
            'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure',
        }) and artifacts['total_count'] == 0 and artifacts['artifacts'] == [], 'Failed source attempt changed')
    original = json.loads(zip_parts(files['original.zip'], {'pacing-state.json': 16384})['pacing-state.json'])
    identity = (owner['run'], 1, adapter.SOURCE_ID)
    adapter._ATTEMPT_HEADS[identity[:2]] = owner['head']
    incoming = adapter.download_inert_artifact(identity, owner['artifact'], 'sha256:' + owner['archive_sha256'])
    require(canonical(incoming) == canonical(original['state']) == canonical(history['prior_budget']),
            'Actual source decoder disagrees with attestation')
    return incoming


def install_native(adapter, custody, capture):
    require(custody.grant and custody.job_id and not os.environ.get('DATA_SOURCE_TOKEN'), 'Native authority missing')
    job = api(f'/actions/jobs/{custody.job_id}')
    require(matches(job, {'id': custody.job_id, 'run_id': custody.run, 'head_sha': custody.head,
                          'status': 'in_progress', 'run_attempt': 1}), 'Unverified native collection deadline')
    started = datetime.fromisoformat(job['started_at'].replace('Z', '+00:00')).timestamp()
    remaining = started + 32 * 60 - time.time()
    require(180 < remaining <= 32 * 60 + 1, 'Native job window exhausted or invalid')
    deadline = time.monotonic() + remaining - 60

    def restore():
        require(not adapter._RESTORED, 'Duplicate native restoration')
        custody.before_publisher()
        custody.verify_predecessor()
        incoming = verified_attestation(adapter, custody)
        require(not incoming['blocked'] and not incoming['in_flight'], 'Historical owner unresolved')
        with adapter.locked_budget():
            local = adapter.load_budget()
            require(adapter._NEW_STATE_DIRECTORY and adapter._GUARD_CREATED
                    and adapter.journal_read() == b'' and not local['blocked'] and not local['in_flight'],
                    'Native runner already owns a journal')
            state = dict(incoming)
            now = time.time()
            state.update(observed_at=now, not_before=max(now + 60, incoming['not_before'], local['not_before']),
                         interval=max(incoming['interval'], local['interval']),
                         page_interval=max(incoming['page_interval'], local['page_interval']))
            adapter.save_budget(state)
        capture['attestation'] = dict(custody.scope['attestation'])
        adapter._RESTORED = adapter._STATE_TRUSTED = True

    adapter.restore_family_artifact = restore
    # The original prepare_collection uses only its write cutoff for bounded
    # collection. The test has no publication token or write operation.
    adapter.publication_window = lambda: {'write': deadline}
