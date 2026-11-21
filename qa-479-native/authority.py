"""Read-only custody for one Germany QA branch and one paused production owner."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'YouthOpps/data-pipeline'
BRANCH = 'qa/479-germany-native'
REF = 'refs/heads/' + BRANCH
WORKFLOW = '.github/workflows/verify-germany479-native.yml'
USER = {'id': 12278069, 'type': 'User'}
API = 'https://api.github.com/repos/' + REPOSITORY
HISTORY_KEYS = ('id', 'run_attempt', 'head_sha', 'status', 'conclusion', 'path',
                'head_branch', 'workflow_id')


class Stop(RuntimeError):
    """The scoped run lacks current evidence of exclusive custody."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, stream, status, message, headers, target):
        return None


OPENER = urllib.request.build_opener(NoRedirect())


def require(condition, label):
    if not condition:
        raise Stop(label)


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def api(path):
    request = urllib.request.Request(API + path, headers={
        'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
        'Accept': 'application/vnd.github+json', 'Cache-Control': 'no-cache, no-store',
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with OPENER.open(request, timeout=12) as response:
        raw = response.read(4_000_001)
        require(response.status == 200 and len(raw) <= 4_000_000, 'Unbounded GitHub read')
    return json.loads(raw)


def matches(row, expected):
    return all(type(row.get(key)) is type(value) and row[key] == value for key, value in expected.items())


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, timeout=15)


def finished_run(path, expected):
    row = api(path)
    require(matches(row, expected), 'Exact run differs')
    require(row['repository']['full_name'] == row['head_repository']['full_name'] == REPOSITORY,
            'Run repository differs')
    return row


class Custody:
    def __init__(self, raw):
        self.raw_scope = raw
        self.scope = json.loads(raw)
        require(self.scope['enabled'] is True and __debug__, 'Germany native scope OFF')
        require('DATA_SOURCE_TOKEN' not in os.environ and os.environ.get('GITHUB_TOKEN'),
                'Wrong credential scope')
        self.head = os.environ.get('GITHUB_SHA', '')
        run = os.environ.get('GITHUB_RUN_ID', '')
        require(re.fullmatch('[a-f0-9]{40}', self.head) and re.fullmatch('[1-9][0-9]*', run),
                'Malformed run scope')
        self.run = int(run)
        self.production = self.scope['production']
        self.workflow_id = self.job_id = self.runner_id = self.grant = None

    def launch(self):
        require(matches(dict(os.environ), {
            'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REF': REF,
            'GITHUB_EVENT_NAME': 'push', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_JOB': 'verify',
            'GITHUB_ACTOR_ID': '12278069', 'GITHUB_WORKFLOW_REF': f'{REPOSITORY}/{WORKFLOW}@{REF}',
            'GITHUB_WORKFLOW_SHA': self.head,
        }), 'Wrong QA environment')
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
        require(matches(event, {'created': True, 'deleted': False, 'before': '0' * 40, 'after': self.head,
                                'ref': REF}) and event['repository']['full_name'] == REPOSITORY
                and matches(event['sender'], USER), 'Wrong original branch creation')
        self.verify_checkout()
        for poll in range(4):
            row = self.active_run()
            if row['status'] == 'in_progress':
                break
            require(row['status'] in {'queued', 'pending', 'waiting', 'requested'}, 'Unexpected QA status')
            time.sleep(poll + 1)
        else:
            raise Stop('QA did not reach active status')
        self.workflow_id = row['workflow_id']
        require(type(self.workflow_id) is int and self.workflow_id > 0 and self.workflow_id != self.production['id'],
                'QA owner conflicts with production')
        meta = api(f'/actions/workflows/{self.workflow_id}')
        require(matches(meta, {'id': self.workflow_id, 'path': WORKFLOW, 'state': 'active'}),
                'QA workflow differs')
        branch = urllib.parse.quote(BRANCH, safe='')
        runs = api(f'/actions/runs?branch={branch}&event=push&per_page=2')
        require(runs['total_count'] == 1 and [r['id'] for r in runs['workflow_runs']] == [self.run],
                'QA branch already used')
        for poll in range(4):
            jobs = api(f'/actions/runs/{self.run}/attempts/1/jobs?per_page=2')
            require(len(jobs['jobs']) == jobs['total_count'] <= 1, 'QA job is not unique')
            if jobs['jobs'] and jobs['jobs'][0]['status'] == 'in_progress':
                break
            time.sleep(poll + 1)
        else:
            raise Stop('QA job did not reach active status')
        job = jobs['jobs'][0]
        require(matches(job, {'run_id': self.run, 'run_attempt': 1, 'head_sha': self.head,
                              'head_branch': BRANCH, 'name': 'Verify Fulbright Germany native'}), 'QA job differs')
        self.job_id, self.runner_id = job['id'], job['runner_id']
        require(type(self.job_id) is int and self.job_id > 0
                and type(self.runner_id) is int and self.runner_id > 0, 'Malformed QA job or runner')
        self.grant = self.verify_grant(row['created_at'])
        self.verify_candidate()
        self.verify_owner()
        self.verify_predecessor()
        return self.receipt()

    def active_run(self):
        row = finished_run(f'/actions/runs/{self.run}/attempts/1', {
            'id': self.run, 'run_attempt': 1, 'head_sha': self.head, 'head_branch': BRANCH,
            'event': 'push', 'path': WORKFLOW,
        })
        require(matches(row['actor'], USER) and matches(row['triggering_actor'], USER), 'Foreign QA actor')
        return row

    def verify_checkout(self):
        require(git('rev-parse', 'HEAD').decode().strip() == self.head, 'Wrong immutable checkout')
        files = [WORKFLOW, 'qa-479-native/scope.json', 'qa-479-native/authority.py', 'qa-479-native/observer.py',
                 'qa-479-native/consumer.mjs', 'qa-479-native/custody_reader.py', 'qa-479-native/anchor.py', 'qa-479-native/escrow.py', 'qa-479-native/reviewer.pem', 'adapters/de-fulbright-germany/adapter.py',
                 'adapters/de-fulbright-germany/test_adapter.py']
        for file in files:
            path = ROOT / file
            require(path.resolve() == path and path.read_bytes() == git('show', f'{self.head}:{file}'),
                    'Immutable checked-out input differs')
        require(git('merge-base', '--is-ancestor', self.scope['candidate'], self.head) == b'',
                'QA does not descend from reviewed PR')
        for name, digest_field in (('adapter.py', 'adapter_sha256'), ('test_adapter.py', 'test_sha256')):
            raw = (ROOT / 'adapters/de-fulbright-germany' / name).read_bytes()
            require(digest(raw) == self.scope[digest_field]
                    and raw == git('show', f'{self.scope["candidate"]}:adapters/de-fulbright-germany/{name}'),
                    'Original adapter or standalone test differs')
        for file, expected in self.scope['website']['files'].items():
            path = ROOT / '_qa/website' / file
            require(path.resolve() == path and digest(path.read_bytes()) == expected, 'Website consumer differs')
        require(digest((ROOT/'qa-479-native/reviewer.pem').read_bytes()) == self.scope['reviewer_sha256'],
                'Reviewer certificate differs')

    def verify_candidate(self):
        issue = api(f'/issues/{self.scope["approval_issue"]}')
        pr = api(f'/pulls/{self.scope["candidate_pr"]}')
        require(issue['state'] == 'open' and pr['state'] == 'open' and pr['draft'] is True
                and pr['head']['sha'] == self.scope['candidate'] and pr['head']['repo']['full_name'] == REPOSITORY
                and pr['base']['ref'] == 'main', 'Original product candidate is no longer held for native QA')

    def verify_grant(self, started):
        issue = self.scope['approval_issue']
        required = (f'Approved Germany native head {self.head}', f'Germany native scope {digest(self.raw_scope)}')
        accepted = []
        for page in range(1, 6):
            rows = api(f'/issues/{issue}/comments?per_page=100&page={page}')
            require(isinstance(rows, list), 'Invalid grant inventory')
            for row in rows:
                lines = (row.get('body') or '').splitlines()
                if not all(lines.count(line) == 1 for line in required):
                    continue
                if (matches(row['user'], USER) and type(row.get('id')) is int and row['id'] > 0
                        and row['issue_url'] == f'{API}/issues/{issue}'
                        and row['html_url'] == f'https://github.com/{REPOSITORY}/issues/{issue}#issuecomment-{row["id"]}'
                        and row['created_at'] == row['updated_at'] <= started):
                    accepted.append(row['html_url'])
            if len(rows) < 100:
                break
        else:
            raise Stop('Grant inventory exceeded its bound')
        require(len(accepted) == 1, 'Missing unique unedited maintainer grant')
        return accepted[0]

    def verify_owner(self):
        owner = self.production
        workflow = api(f'/actions/workflows/{owner["id"]}')
        require(matches(workflow, {'id': owner['id'], 'path': owner['path'], 'state': 'disabled_manually'}),
                'Canonical producer is not manually paused')
        packet = api(f'/actions/workflows/{owner["id"]}/runs?per_page=100&page=1')
        rows = packet['workflow_runs']
        require(len(rows) == packet['total_count'] == owner['history_count'] <= 100, 'Producer history grew')
        require(len({r['id'] for r in rows}) == len(rows), 'Duplicate production owner')
        require(all(r['status'] == 'completed' and r['workflow_id'] == owner['id']
                    and r['path'] == owner['path'] and r['head_branch'] == 'main'
                    and r['repository']['full_name'] == r['head_repository']['full_name'] == REPOSITORY
                    for r in rows), 'Production is still active or foreign')
        reduced = [{key: row[key] for key in HISTORY_KEYS} for row in rows]
        require(digest(canonical(reduced)) == owner['history_sha256'], 'Exact paused production history changed')

    def verify_predecessor(self):
        p = self.production
        ident = p['run']
        finished_run(f'/actions/runs/{ident}/attempts/1', {
            'id': ident, 'run_attempt': 1, 'head_sha': p['head'], 'head_branch': 'main',
            'workflow_id': p['id'], 'path': p['path'], 'status': 'completed', 'conclusion': 'success',
        })
        data = api(f'/actions/runs/{ident}/attempts/1/jobs?per_page=2')
        require(len(data['jobs']) == data['total_count'] == 1, 'Production predecessor job ambiguity')
        job = data['jobs'][0]
        require(matches(job, {'id': p['job'], 'run_id': ident, 'run_attempt': 1, 'name': 'collect',
                              'head_sha': p['head'], 'head_branch': 'main', 'status': 'completed',
                              'conclusion': 'success'}), 'Production predecessor job differs')
        retained = [s for s in job['steps'] if s['name'] == 'Preserve trusted publisher timing']
        require(len(retained) == 1 and retained[0]['conclusion'] == 'success', 'Original state upload unverified')
        data = api(f'/actions/runs/{ident}/artifacts?per_page=2')
        require(data['total_count'] == len(data['artifacts']) == 1, 'Production artifact ambiguity')
        artifact = data['artifacts'][0]
        require(artifact == api(f'/actions/artifacts/{p["artifact"]}'), 'Production artifact endpoints differ')
        require(matches(artifact, {'id': p['artifact'], 'name': 'de-fulbright-germany-pacing-state-1', 'expired': False,
                                   'size_in_bytes': p['archive_size'], 'digest': 'sha256:' + p['archive_sha256']})
                and matches(artifact['workflow_run'], {'id': ident, 'head_sha': p['head'], 'head_branch': 'main'}),
                'Exact initial archive differs')

    def before_publisher(self):
        run = self.active_run()
        require(run['status'] == 'in_progress' and run['workflow_id'] == self.workflow_id,
                'QA no longer owns its running job')
        job = api(f'/actions/jobs/{self.job_id}')
        require(matches(job, {'id': self.job_id, 'run_id': self.run, 'run_attempt': 1,
                              'head_sha': self.head, 'head_branch': BRANCH, 'runner_id': self.runner_id,
                              'status': 'in_progress'}), 'QA active job changed')
        require(api('/git/ref/heads/' + BRANCH)['object']['sha'] == self.head, 'QA branch moved')
        comment = api('/issues/comments/' + self.grant.rsplit('-', 1)[-1])
        require(matches(comment['user'], USER) and comment['html_url'] == self.grant
                and comment['issue_url'] == f'{API}/issues/{self.scope["approval_issue"]}'
                and comment['created_at'] == comment['updated_at'] <= run['created_at']
                and (comment.get('body') or '').splitlines().count(f'Approved Germany native head {self.head}') == 1
                and (comment.get('body') or '').splitlines().count('Germany native scope ' + digest(self.raw_scope)) == 1,
                'Original QA grant changed')
        self.verify_owner()

    def receipt(self):
        return {'repo': REPOSITORY, 'branch': BRANCH, 'head': self.head, 'run': self.run, 'attempt': 1,
                'workflow': self.workflow_id, 'job': self.job_id, 'runner': self.runner_id, 'authority': self.grant,
                'candidate': self.scope['candidate'], 'scope_sha256': digest(self.raw_scope)}
