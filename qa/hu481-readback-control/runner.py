"""Reconcile the retained, authentic completed native; exercise the corrected terminal callback without publisher access."""
import base64,hashlib,importlib.util,io,json,os,sys,time
from pathlib import Path
from urllib.request import Request
from urllib.error import HTTPError
from urllib.parse import urlsplit
from zipfile import ZipFile

here=Path(__file__).resolve().parent
sys.path.insert(0,str(here.parent/"hu481-native"))
import escrow,support
spec=importlib.util.spec_from_file_location("hu481_original",support.SOURCE)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
run=44891843634
gid=136809155537
head="54eb6c66d329ef21a7555e9e5d679c873c1764bf"
repo="YouthOpps/data-pipeline"
sha=lambda b:hashlib.sha256(b).hexdigest()
original=a._OPENER.open
def infra(req,*args,**kwargs):
    u=urlsplit(req.full_url)
    assert u.scheme=="https" and req.get_method()=="GET" and u.port is None and req.get_header("Cookie") is None
    if u.hostname=="api.github.com":assert u.path.startswith("/repos/"+repo+"/actions/")
    else:assert u.hostname.endswith((".blob.core.windows.net",".actions.githubusercontent.com")) and req.get_header("Authorization") is None
    return original(req,*args,**kwargs)
a._OPENER.open=infra
def archive(meta):
    endpoint=f"https://api.github.com/repos/{repo}/actions/artifacts/{meta['id']}/zip"
    req=Request(endpoint,headers={"Authorization":"Bearer "+os.environ["GITHUB_TOKEN"],"User-Agent":"YouthOpps-Hungary-state-audit"})
    try:r=a._OPENER.open(req,timeout=20)
    except HTTPError as exc:r=exc
    with r:assert r.status==302;dest=r.headers["Location"]
    with a._OPENER.open(Request(dest),timeout=20) as r:
        assert r.status==200;body=r.read(4_000_001)
    assert 0<len(body)==meta["size_in_bytes"]<=4_000_000 and "sha256:"+sha(body)==meta["digest"]
    return ZipFile(io.BytesIO(body))
rows=json.loads((here/"original-artifacts.json").read_bytes())
assert len(rows)==15 and len({m["id"] for m in rows})==15
expected={m["name"]:m for m in rows}
with a.execution(),a.phase("restore",180):
    info=a.workflow_api(f"/actions/runs/{run}/attempts/1")
    assert info["head_sha"]==head and info["conclusion"]=="failure" and info["workflow_id"]==425857246
    assert a.workflow_api("/actions/workflows/425857246")["state"]=="disabled_manually"
    job=a.workflow_api(f"/actions/jobs/{gid}")
    assert job["run_id"]==run and job["run_attempt"]==1 and job["head_sha"]==head
    steps={s["name"]:s["conclusion"] for s in job["steps"]}
    assert steps["Run full original Hungary standalone test"]=="failure"
    assert steps["Retain encrypted native result"]==steps["Retain native summary and custody checkpoints"]=="success"
    actual=a.workflow_api(f"/actions/runs/{run}/artifacts?per_page=20")
    assert actual["total_count"]==len(actual["artifacts"])==15
    for meta in actual["artifacts"]:
        pinned=expected[meta["name"]]
        assert all(pinned[k]==meta[k] for k in pinned) and not meta["expired"] and meta["workflow_run"]["head_sha"]==head and meta["workflow_run"]["id"]==run
        with archive(meta) as z:
            if meta["name"]==f"hu481-native-summary-{run}-1":
                facts=json.loads(z.read("summary.json"));terminal=json.loads(z.read("terminal.json"))
                assert "terminal-ack.json" not in z.namelist()
                assert len([n for n in z.namelist() if n.endswith("-ack.json")])==12
                for stem,ack in terminal["remote_checkpoints"].items():
                    expected_name=f"hu481-native-{run}-1-{stem}"
                    assert ack["name"]==expected_name and ack["artifact_id"]==expected[expected_name]["id"]
                    assert "sha256:"+ack["digest"]==expected[expected_name]["digest"]
            elif meta["name"]==f"hu481-native-final-{run}-1":
                assert z.namelist()==["evidence.cms"];final_sha=sha(z.read("evidence.cms"))
            else:
                assert z.namelist()==["receipt.cms"] and len(z.read("receipt.cms"))>0
assert facts["native_exit"]==terminal["native_exit"]==0 and facts["physical_requests"]==terminal["number_opened"]==terminal["settled_count"]==6
assert not facts["pending"] and not terminal["unresolved"] and terminal["observer_error"] is None
assert facts["observer_error"]=="Terminal escrow failure" and final_sha==facts["evidence_sha256"]
assert facts["consumers"]["raw"]["count"]==facts["consumers"]["filtered"]["count"]==4
assert facts["consumers"]["raw"]["ids"]==facts["consumers"]["filtered"]["ids"]
assert facts["consumers"]["empty"]["count"]==0
# The prior observer attempted a terminal REST GET here, outside the original adapter execution.
try:a.request_timeout()
except a.AdapterError:pass
else:raise RuntimeError("Expected the old closed phase to refuse a GET")
current={"run_id":int(os.environ["GITHUB_RUN_ID"]),"run_attempt":1,"head":os.environ["GITHUB_SHA"],"ref":os.environ["GITHUB_REF"]}
result=support.persist_terminal(a,time.monotonic()+110,lambda:escrow.upload(current,"terminal",{"synthetic":True},a.workflow_api,support.seal))
print("Original genuine standalone test exited 0; six actual closed publisher GETs, four raw and four filtered awards; fifteen exact original artifacts verified.",
      "Original wrapper failure preserved. Corrected bounded terminal acknowledgement verified in this no-publisher job.",result["artifact_id"])
