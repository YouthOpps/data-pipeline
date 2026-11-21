"""Create the exact incident attestation in its single approved GitHub Actions job."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = 'YouthOpps/data-pipeline'
REF = 'refs/heads/qa/479-germany-state-only-v3'
WORKFLOW = '.github/workflows/verify-479-state-v3.yml'
PRODUCTION = '.github/workflows/fetch-de-fulbright-germany.yml'
DIGEST = 'd4d4c9280a122f4e33e0242cb40fa682d69d8f801de63cbdd6e9663eb35835c1'
LOGS = {
    136667720087: '30c8c976c61ef20a126c335a27ba0f3726745f42d712271ec412acdd06c9d336',
    136012035069: 'e54ece509346c60258c188f44033b655e4afd47630165bd20ffe9f9ad4596dee',
}


def gh(path):
    return subprocess.check_output(['gh', 'api', f'repos/{REPO}/{path}'], timeout=60)


def api(path):
    return json.loads(gh(path))


def guard():
    env = os.environ
    run_id = int(env['GITHUB_RUN_ID'])
    sha = env['GITHUB_SHA']
    assert env['GITHUB_REPOSITORY'] == REPO and env['GITHUB_REF'] == REF
    assert env['GITHUB_EVENT_NAME'] == 'push' and env['GITHUB_RUN_ATTEMPT'] == '1'
    assert env['GITHUB_WORKFLOW_REF'] == f'{REPO}/{WORKFLOW}@{REF}'
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_bytes())
    assert event['created'] is True and event['deleted'] is False
    assert event['before'] == '0' * 40 and event['after'] == sha
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == sha
    unsuccessful = [
        (44889794525, 136802938151, 'b406143031ea19d9bcb4822cd8a249925ed56c4f', '.github/workflows/verify-479-state.yml'),
        (44889919336, 136803305481, '8fe99d5bedd0b01a1349c398619b3aec420bc32b', '.github/workflows/verify-479-state-v2.yml'),
    ]
    for old_id, job_id, old_sha, path in unsuccessful:
        prior = api(f'actions/runs/{old_id}/attempts/1')
        assert prior['head_sha'] == old_sha and prior['path'] == path
        assert prior['status'] == 'completed' and prior['conclusion'] == 'failure' and prior['run_attempt'] == 1
        old_jobs = api(f'actions/runs/{old_id}/attempts/1/jobs')
        assert old_jobs['total_count'] == len(old_jobs['jobs']) == 1 and old_jobs['jobs'][0]['id'] == job_id
        assert api(f'actions/runs/{old_id}/artifacts')['total_count'] == 0
    source = api('actions/workflows/378390061')
    assert (source['id'], source['path'], source['state']) == (378390061, PRODUCTION, 'disabled_manually')
    me = api(f'actions/runs/{run_id}/attempts/1')
    assert me['id'] == run_id and me['path'] == WORKFLOW and me['head_sha'] == sha
    assert me['head_branch'] == REF.removeprefix('refs/heads/') and me['run_attempt'] == 1
    assert me['repository']['full_name'] == me['head_repository']['full_name'] == REPO
    assert me['status'] == 'in_progress' and me['event'] == 'push'
    history = api(f'actions/workflows/{me["workflow_id"]}/runs?per_page=100')
    assert history['total_count'] == 1 and [x['id'] for x in history['workflow_runs']] == [run_id]
    jobs = api(f'actions/runs/{run_id}/attempts/1/jobs?per_page=100')
    assert len(jobs['jobs']) == jobs['total_count'] == 1
    job = jobs['jobs'][0]
    assert job['name'] == 'Attest exact Fulbright Germany history'
    assert job['head_sha'] == sha and job['run_id'] == run_id and job['run_attempt'] == 1
    assert job['status'] == 'in_progress'
    expected = f'Authorize one state-only Fulbright Germany attestation for {sha} on {REF} under #479. No publisher access or publication.'
    issue = api('issues/479')
    assert issue['state'] == 'open' and issue['comments'] <= 100
    comments = api('issues/479/comments?per_page=100')
    assert len(comments) == issue['comments']
    grants = [c for c in comments if c['body'] == expected]
    assert len(grants) == 1
    grant = grants[0]
    assert grant['created_at'] == grant['updated_at'] and grant['created_at'] <= me['created_at']
    assert grant['user']['login'] == 'fmarslan' and grant['user']['type'] == 'User'
    # Association labels depend on the viewer's organization visibility.
    # The independently verified repository administrator is this immutable user.
    assert grant['user']['id'] == 12278069
    for who in ('actor', 'triggering_actor'):
        assert me[who]['id'] == 12278069 and me[who]['type'] == 'User'
    assert grant['issue_url'] == f'https://api.github.com/repos/{REPO}/issues/479'
    assert grant['html_url'] == f'https://github.com/{REPO}/issues/479#issuecomment-{grant["id"]}'
    return dict(repository=REPO, workflow_id=me['workflow_id'], workflow_path=WORKFLOW,
                ref=REF, sha=sha, run_id=run_id, run_attempt=1, job_id=job['id'],
                grant_comment_id=grant['id'])


def main():
    os.umask(0o077)
    scope = guard()
    temp = Path(os.environ['RUNNER_TEMP'])
    private = temp / 'germany479-private'
    private.mkdir(mode=0o700)
    for job, digest in LOGS.items():
        raw = gh(f'actions/jobs/{job}/logs')
        normalized = raw.decode('utf-8-sig').replace('\r\n', '\n').encode()
        assert hashlib.sha256(normalized).hexdigest() == digest
        (private / f'job-{job}.log').write_bytes(raw)
    archive = gh('actions/artifacts/14489820002/zip')
    assert hashlib.sha256(archive).hexdigest() == DIGEST
    (private / 'independent-custody-14489820002.zip').write_bytes(archive)
    subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('audit.py'))], check=True)
    history = json.loads((private / 'two-gap-chronology-evidence.json').read_bytes())
    assert guard() == scope
    evidence = temp / 'germany479-evidence'
    evidence.mkdir(mode=0o700)
    (evidence / 'original.zip').write_bytes(archive)
    (evidence / 'provenance.json').write_text(json.dumps({
        **scope, 'predecessor_archive_id': 14489820002,
        'predecessor_archive_sha256': DIGEST, 'history': history,
    }, sort_keys=True))


if __name__ == '__main__':
    main()
