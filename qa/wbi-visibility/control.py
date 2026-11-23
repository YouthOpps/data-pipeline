import importlib.util,os,sys
from unittest.mock import patch
from pathlib import Path
p=Path(sys.argv[1])
spec=importlib.util.spec_from_file_location('wbi',p)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
repo={'full_name':'YouthOpps/data-pipeline'}
old=dict(id=9,run_attempt=1,workflow_id=378390052,repository=repo,head_repository=repo,head_branch='main',path='.github/workflows/fetch-be-wbi.yml',head_sha='b'*40,status='completed')
cur=dict(old,id=10,status='in_progress',head_sha='a'*40)
def response(rows): return {'total_count':len(rows),'workflow_runs':rows}
with patch.dict(os.environ,dict(GITHUB_ACTIONS='true',GITHUB_REPOSITORY=repo['full_name'],GITHUB_REF='refs/heads/main',GITHUB_RUN_ID='10',GITHUB_RUN_ATTEMPT='1',GITHUB_SHA='a'*40)):
 for seq,want,msg in [([[old],[old,cur]],2,''),([[old]]*3,3,'remained unstable'),([[old,dict(cur,head_sha='b'*40)]],1,'Current family attempt changed'),([[old,cur]],1,'')]:
  with patch.object(m,'workflow_api',side_effect=list(map(response,seq))) as api, patch.object(m,'completed_identity',return_value=(50,40,9,1,'be-wbi')):
   try: assert m.latest_family_run()==(9,1,'be-wbi') and not msg
   except m.AdapterError as exc: assert msg and msg in str(exc),(exc,msg)
   assert api.call_count==want
print('PASS four cases: transient omission, permanent omission, conflicting attempt, immediate healthy inventory')
