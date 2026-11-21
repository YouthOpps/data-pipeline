"""Gather chronology and retained evidence for the exact closed no-wire incident."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
from zipfile import ZipFile

import os
work=Path(os.environ['RUNNER_TEMP']) / 'germany479-private'
repo='YouthOpps/data-pipeline'
path='.github/workflows/fetch-de-fulbright-germany.yml'
wf=378390061
last, earlier, predecessor=44846595786,44652934438,44435787861
archive_id=14489820002


def gh(key):
    return json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/{key}'],timeout=25))


def extract(row):
    assert row['workflow_id']==wf and row['path']==path and row['status']=='completed'
    assert row['repository']['full_name']==row['head_repository']['full_name']==repo
    assert row['head_branch']=='main' and row['run_attempt']==1
    identity=row['id']
    jobs=gh(f'actions/runs/{identity}/attempts/1/jobs?per_page=100&page=1')
    assert jobs['total_count']==len(jobs['jobs']) and jobs['jobs']
    ended=[]
    for job in jobs['jobs']:
        assert job['run_id']==identity and job['run_attempt']==1 and job['head_sha']==row['head_sha']
        assert job['status']=='completed' and job['head_branch']=='main'
        start,done=(datetime.fromisoformat(job[key]) for key in ('started_at','completed_at'))
        assert start<=done<=datetime.now(timezone.utc)
        ended.append(done)
    selected=max(ended)
    return selected, row, jobs['jobs']


# At the reviewed instant this owner has one complete page and every attempt is 1.
packet=gh(f'actions/workflows/{wf}/runs?per_page=100&page=1')
rows=packet['workflow_runs']
assert packet['total_count']==len(rows)==len({r['id'] for r in rows})==63
with ThreadPoolExecutor(max_workers=6) as workers:
    candidates=sorted(workers.map(extract,rows),key=lambda choice:choice[0],reverse=True)
assert gh(f'actions/workflows/{wf}/runs?per_page=100&page=1')==packet
assert [row['id'] for _,row,_ in candidates[:3]]==[last,earlier,predecessor]
assert candidates[0][0]>candidates[1][0]>candidates[2][0]>candidates[3][0]
for index,(run,job,conclusion,export) in enumerate(((last,136667720087,'failure','skipped'),
                                                    (earlier,136012035069,'failure','skipped'),
                                                    (predecessor,135264177722,'success','success'))):
    timestamp,row,selected=candidates[index]
    exact=gh(f'actions/runs/{run}/attempts/1')
    assert all(exact[k]==row[k] for k in ('id','run_attempt','head_sha','workflow_id','path','head_branch','conclusion','event'))
    assert row['event']=='schedule' and row['conclusion']==conclusion and len(selected)==1
    assert selected[0]['id']==job and selected[0]['name']=='collect' and selected[0]['conclusion']==conclusion
    steps={item['name']:item['conclusion'] for item in selected[0]['steps']}
    assert steps['Collect and publish this source']==conclusion and steps['Preserve trusted publisher timing']==export
expected_head='11fa75c3e745c5e0d8c12ee8a5c9f8371417d764'
source='de-fulbright-germany'
command=f'##[group]Run python -B adapters/{source}/adapter.py --publish'
code=subprocess.check_output(['git','-C',os.environ['GITHUB_WORKSPACE'],'show',f'{expected_head}:adapters/{source}/adapter.py'])
assert hashlib.sha256(code).hexdigest()=='b11eb2dbe0c07b6f792f82a41e67eba1c71350e4c7671b37e073c1d1c23aa538'
assert code.count(b'Workflow inventory remained unstable')==1
log_digests={}
for _,row,jobs in candidates[:2]:
    run=row['id']
    assert row['head_sha']==expected_head
    bad=gh(f'actions/runs/{run}/artifacts?per_page=100')
    assert bad['total_count']==0 and bad['artifacts']==[]
    job=jobs[0]['id']
    raw=(work/f'job-{job}.log').read_bytes()
    log_digests[job]=hashlib.sha256(raw.decode('utf-8-sig').replace('\r\n','\n').encode()).hexdigest()
    lines=raw.decode().splitlines()
    checked=[i for i,line in enumerate(lines) if line.endswith('Z '+expected_head)]
    commands=[i for i,line in enumerate(lines) if line.endswith(command)]
    errors=[i for i,line in enumerate(lines) if 'Workflow inventory remained unstable' in line]
    assert len(checked)==len(commands)==len(errors)==1 and checked[0]<commands[0]<errors[0]
    error=json.loads(lines[errors[0]].split('Z ',1)[1])
    assert error['source']==source and error['status']=='fail' and error['failure_stage']=='access'
    assert error['error']=='Workflow inventory remained unstable'
    assert lines[errors[0]+1].endswith('##[error]Process completed with exit code 1.')
found=gh(f'actions/runs/{predecessor}/artifacts?per_page=100')
assert len(found['artifacts'])==found['total_count']==1
artifact=found['artifacts'][0]
assert artifact==gh(f'actions/artifacts/{archive_id}')
assert artifact['id']==archive_id and artifact['expired'] is False
archive=work/'independent-custody-14489820002.zip'
zip_raw=archive.read_bytes()
assert artifact['size_in_bytes']==len(zip_raw) and artifact['digest']=='sha256:'+hashlib.sha256(zip_raw).hexdigest()
with ZipFile(archive) as reader:
    assert reader.namelist()==['pacing-state.json']
    state= json.loads(reader.read('pacing-state.json'))
assert state['head_sha']==candidates[2][1]['head_sha'] and state['run_id']==predecessor and state['run_attempt']==1
assert state['source']=='de-fulbright-germany' and state['repository']==repo
assert state['state']['schema']==3 and state['state']['blocked'] is False and state['state']['in_flight'] is False
assert state['state']['legacy_rate_history_unknown'] is True
assert gh(f'actions/workflows/{wf}/runs?per_page=100&page=1')==packet
result={'complete_exact_attempt_jobs':len(candidates),'owner_path':path,'owner_id':wf,
        'strictly_adjacent':True,'chronology':[
            {'run':row['id'],'head':row['head_sha'],'job':group[0]['id'],'finished_at':finished.isoformat()}
            for finished,row,group in candidates[:3]],'failed_run_artifact_counts':{last:0,earlier:0},'log_digests':log_digests,
        'prior_artifact':{'id':archive_id,'sha256':artifact['digest'],'bytes':len(zip_raw)},
        'prior_budget':state['state'],'origin_traffic':0}
(work/'two-gap-chronology-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
