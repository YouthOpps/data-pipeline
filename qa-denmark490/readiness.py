"""Read only GitHub evidence; the actual publisher remains outside this test."""
import importlib.util
import json
import os
from pathlib import Path
import urllib.parse
from unittest.mock import patch

from authority import Custody, Stop, api, canonical, digest, HISTORY_KEYS
from observer import SOURCE, PACKET, authorized_overlay

def require(value, text):
    if not value:
        raise RuntimeError(text)

scope_raw=(PACKET/'scope.json').read_bytes()
scope=json.loads(scope_raw)
require(scope['enabled'] is False and digest(SOURCE.read_bytes())==scope['adapter_sha256'],'Expected exact OFF source')
try:
    Custody(scope_raw)
except Stop as exc:
    require('scope OFF' in str(exc), 'Unexpected refusal')
else:
    raise RuntimeError('OFF unexpectedly armed')
compile(authorized_overlay(SOURCE.read_bytes()), str(SOURCE), 'exec')
p=scope['production']
runs=api(f"/actions/workflows/{p['id']}/runs?per_page=100")
require(len(runs['workflow_runs'])==runs['total_count']==p['history_count'],'Changed inventory')
require(digest(canonical([{k:r[k] for k in HISTORY_KEYS} for r in runs['workflow_runs']]))==p['history_sha256'],'Changed source history')
require(all(r['status']=='completed' and r['path']==p['path'] and r['head_branch']=='main' for r in runs['workflow_runs']),'Current source overlap')
custody=object.__new__(Custody)
custody.scope=scope
custody.production=p
custody.verify_candidate()
custody.verify_predecessor()
spec=importlib.util.spec_from_file_location('denmark_preflight',SOURCE)
adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
original_open=adapter._OPENER.open
seen=[]
def checked(request, **kwargs):
    parsed=urllib.parse.urlsplit(request.full_url)
    headers={k.lower():v for k,v in request.header_items()}
    require(parsed.scheme=='https' and request.get_method()=='GET','Unexpected protocol')
    if len(seen)==0:
        require(parsed.netloc=='api.github.com' and parsed.path==f"/repos/YouthOpps/data-pipeline/actions/artifacts/{p['artifact']}/zip",'Unexpected archive source')
    else:
        require(len(seen)==1 and parsed.hostname.endswith('.blob.core.windows.net') and 'authorization' not in headers,'Unexpected redirected owner')
    seen.append(parsed.hostname)
    return original_open(request,**kwargs)
with patch.object(adapter._OPENER,'open',checked), adapter.execution():
    with adapter.phase('restore',180):
        state=adapter.download_inert_artifact((p['run'],1,adapter.SOURCE_ID,p['head']),p['artifact'],'sha256:'+p['archive_sha256'])
        require(not state['in_flight'] and not state['blocked'] and digest(canonical(state))==p['state_sha256'],'Changed restored state')
        historical=adapter.production_maintenance_state(state)
        require(set(state['starts'])<=set(historical['starts']) and set(adapter._HOSTED_NATIVE_MAX['state']['starts'])<=set(historical['starts']),'Lost prior floor')
require(len(seen)==2,'Unexpected infrastructure request count')
print(json.dumps(dict(source='dk-fulbright-center',pr=490,production_run=p['run'],artifact=p['artifact'],
    owned_starts=len(state['starts']),merged_original_floor_starts=len(historical['starts']),
    source_sha256=scope['adapter_sha256'],archive_sha256=p['archive_sha256'],actual_publisher_opens=0,
    original_source_active=api(f"/actions/workflows/{p['id']}")['state'],native_grant=False)))
