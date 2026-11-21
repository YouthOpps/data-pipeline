"""Read the actual first/second Hungary handback through the candidate adapter; no publisher credential or traffic."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

tree=Path(__file__).resolve().parents[2]
p=tree/"adapters/hu-fulbright-hungary/adapter.py"
sp=importlib.util.spec_from_file_location("hu",p);a=importlib.util.module_from_spec(sp);sp.loader.exec_module(a)
wf=(tree/".github/workflows/fetch-hu-fulbright-hungary.yml").read_text()
for k in ("HUNGARY_PACING_MAINTENANCE","HUNGARY_HANDOFF_COLLECTOR_BLOB","HUNGARY_NATIVE_HANDOFF","HUNGARY_NATIVE_HANDOFF_COLLECTOR_BLOB"):
    os.environ[k]=re.search(r"^\s+"+k+r": '([^']+)'$",wf,re.M)[1]
assert "DATA_SOURCE_TOKEN" not in os.environ
orig=a._OPENER.open
def github_only(req,*args,**kwargs):
    u=urlsplit(req.full_url)
    assert req.get_method()=="GET" and u.scheme=="https" and u.port is None and req.get_header("Cookie") is None
    if u.hostname=="api.github.com":
        assert u.path.startswith("/repos/YouthOpps/data-pipeline/actions/") or u.path.startswith("/repos/YouthOpps/data-pipeline/contents/")
    else:assert u.hostname.endswith((".blob.core.windows.net",".actions.githubusercontent.com")) and req.get_header("Authorization") is None
    return orig(req,*args,**kwargs)
a._OPENER.open=github_only
canonical=lambda x:json.dumps(x,sort_keys=True,separators=(",",":")).encode()
digest=lambda x:hashlib.sha256(canonical(x)).hexdigest()
with a.execution(),a.phase("restore",180):
    assert a.workflow_api("/actions/workflows/378774441")["state"]=="disabled_manually"
    runs=a.workflow_api("/actions/workflows/378774441/runs?per_page=100")
    rows=sorted((r["id"],r["run_attempt"],r["head_sha"],r["workflow_id"],r["status"]) for r in runs["workflow_runs"])
    assert all(r[4]=="completed" for r in rows) and runs["total_count"]==len(rows)==70 and digest(rows)=="d41416411b5a2e9117e7d0ee508a2914851e484152ed374c0f20bc1613db93e1"
    own=(44890448073,1,a.SOURCE_ID,"5d509a0f117020ab8d2ed30e1b538c24d4fb21b3")
    prior=a._HU481_PRODUCER
    def exact_job(run,attempt,job):
        info=a.workflow_api(f"/actions/runs/{run}/attempts/{attempt}/jobs?per_page=2")
        assert info["total_count"]==len(info["jobs"])==1 and info["jobs"][0]["id"]==job and info["jobs"][0]["conclusion"]=="success"
        j=info["jobs"][0]
        return tuple(a.datetime.fromisoformat(j[k].replace("Z","+00:00")).timestamp() for k in ("completed_at","started_at"))
    a._HISTORY_COMPLETED=[(*exact_job(prior[0],1,136626199314),*prior),(*exact_job(own[0],1,136804891141),*own)]
    archive="sha256:635af08d695299322c4d17a529566e2121d6916838d98078ed4d542fe249e1a0"
    incoming=a.download_inert_artifact(own,14701479702,archive)
    older=a.production_maintenance_state(incoming,owner=(own,14701479702,archive))
    accepted=a.adopt_completed_hu481_native(older,own,14701479702)
    packet=json.loads(os.environ["HUNGARY_NATIVE_HANDOFF"])
    assert digest(older)==packet["original_merged_sha256"]
    assert digest(accepted)==packet["floor_digest"]
    print("Confirmed two-layer original source handback, actual retired native and exact authentic records; zero publisher opens.",
          "starts",len(accepted["starts"]),"digest",digest(accepted))
