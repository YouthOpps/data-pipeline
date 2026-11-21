"""Replay the exact restrictive owner offline through the original startup decision."""
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys

here=Path(os.environ['RUNNER_TEMP'])/'klagenfurt486-proof'
here.mkdir(mode=0o700)
root=Path(__file__).resolve().parents[1]
adapter=root/'adapters/at-klagenfurt-msc-international-management/adapter.py'
spec=importlib.util.spec_from_file_location('klagenfurt_download',adapter)
source=importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
# Use the existing authenticated GitHub-only downloader and source inventory.
inventory=source.workflow_api('/actions/runs/44653828595/artifacts?per_page=100')
expected=source.workflow_api('/actions/artifacts/14591768127')
assert inventory['total_count']==1 and inventory['artifacts']==[expected]
assert expected['id']==14591768127 and expected['expired'] is False and expected['size_in_bytes']==985
assert expected['digest']=='sha256:f42471f1bf7fea792ceab90e9260dabfb3beb84e21302a613f51851403749b8d'
(here/'klagenfurt-pending-14591768127.zip').write_bytes(source.artifact_bytes(expected['id'], expected['digest']))
(here/'inventory.json').write_text(json.dumps(inventory))
previous=44653828595
head='11fa75c3e745c5e0d8c12ee8a5c9f8371417d764'
owner=(1.0,previous,1,head)
binding=(422232608,44847290777,1,head)
archive=(here/'klagenfurt-pending-14591768127.zip').read_bytes()
assert hashlib.sha256(archive).hexdigest() == 'f42471f1bf7fea792ceab90e9260dabfb3beb84e21302a613f51851403749b8d'
inventory=json.loads((here/'inventory.json').read_bytes())
with ZipFile(io.BytesIO(archive)) as z:
    historical=json.loads(z.read('pacing-state.json'))
origin=subprocess.check_output(['git','-C',str(adapter.parents[2]),'show',head+':adapters/at-klagenfurt-msc-international-management/adapter.py'])
origin_sha=hashlib.sha256(origin).hexdigest()
assert historical['adapter_sha256'] == origin_sha
assert historical['state']['physical_pending'] is historical['state']['in_flight'] is True
assert historical['state']['physical_id']=='e5ab6537b5b3436596862983a9bcf407'

def run(case):
    spec=importlib.util.spec_from_file_location('klagenfurt486',adapter)
    a=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)
    with TemporaryDirectory() as temp, ExitStack() as stack:
        root=Path(temp);out=root/'output';target=root/'pacing-state.json';out.touch(mode=0o600)
        obj=lambda *args,**kwargs: stack.enter_context(patch.object(*args,**kwargs))
        stack.enter_context(patch.dict(os.environ,{'RUNNER_TEMP':temp,'KLAGENFURT_PACING_ARTIFACT_PATH':str(target),'GITHUB_OUTPUT':str(out)}))
        obj(sys,'argv',['adapter.py','--publish']);obj(a,'HOSTED',True)
        obj(a,'workflow_binding',return_value=binding)
        obj(a,'workflow_history',side_effect=[owner,(3,123,1,head) if case=='owner-change' else owner])
        obj(a,'authenticated_source_hash',return_value=origin_sha)
        def api(path):
            assert path == f'/actions/runs/{previous}/artifacts?per_page=100'
            if case=='missing':return {'total_count':0,'artifacts':[]}
            return inventory
        obj(a,'workflow_api',side_effect=api)
        def download(identity,digest):
            assert identity==14591768127 and digest=='sha256:'+hashlib.sha256(archive).hexdigest()
            if case=='corrupt':raise a.Stop('Fixture: corrupt GitHub archive digest')
            return archive
        obj(a,'artifact_bytes',side_effect=download)
        publisher=obj(a,'request',side_effect=AssertionError('unexpected publisher request'))
        publication=obj(a,'publish',side_effect=AssertionError('unexpected published source access'))
        launch=obj(a,'initialize_hosted',side_effect=AssertionError('unexpected collecting journal initialization'))
        if case=='dirty':a.AUDIT['persistence'].append({'acknowledged':False})
        if case=='preexisting':target.write_bytes(b'existing')
        try:a.main()
        except (a.Stop,FileExistsError) as error:
            if case in ('pending','dirty'):assert str(error)=='Newest publisher state has unresolved access restrictions'
        else:raise AssertionError('Expected failed collection')
        for mock in (publisher,publication,launch):mock.assert_not_called()
        if case=='pending':
            assert out.read_bytes()==b'pacing_ready=true\n'
            payload=json.loads(target.read_bytes())
            assert payload['run_id']==binding[1] and payload['run_attempt']==binding[2]
            assert payload['head_sha']==binding[3] and payload['adapter_sha256']==a.own_source_hash()
            assert payload['state_sha256']==historical['state_sha256']
            assert a.canonical(payload['state'])==a.canonical(historical['state'])
        else:assert not out.read_bytes()
        if case in ('missing','corrupt','dirty'):assert not target.exists()
        if case=='preexisting':assert target.read_bytes()==b'existing'
for c in ('pending','missing','corrupt','owner-change','preexisting','dirty'):run(c)
print('PASS actual historical pending bytes, refusal, current export identity; missing/corrupt/changed-owner/collision/dirty state fail closed; zero publisher and data requests.')
