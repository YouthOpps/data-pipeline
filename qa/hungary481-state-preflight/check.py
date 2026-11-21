"""Read only the exact original GitHub archives; do not enter collection or initialize publisher state."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

repo = Path(__file__).resolve().parents[2]
p = repo / "adapters/hu-fulbright-hungary/adapter.py"
spec=importlib.util.spec_from_file_location("hu",p)
a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
workflow=(repo/".github/workflows/fetch-hu-fulbright-hungary.yml").read_text()
for k in ("HUNGARY_HANDOFF_COLLECTOR_BLOB","HUNGARY_PACING_MAINTENANCE"):
    os.environ[k]=re.search(r"^\s+"+k+r": '([^']+)'$",workflow,re.M)[1]
assert "DATA_SOURCE_TOKEN" not in os.environ
original=a._OPENER.open
def infra(request,*args,**kwargs):
    u=urlsplit(request.full_url)
    assert request.get_method()=="GET" and u.scheme=="https" and u.port is None
    if u.hostname=="api.github.com":
        assert u.path.startswith("/repos/YouthOpps/data-pipeline/actions/")
    else:
        assert u.hostname.endswith((".blob.core.windows.net",".actions.githubusercontent.com"))
        assert request.get_header("Authorization") is None and request.get_header("Cookie") is None
    return original(request,*args,**kwargs)
a._OPENER.open=infra
with a.execution(),a.phase("restore",180):
    registered=a.workflow_api("/actions/workflows/378774441")
    assert registered["state"]=="disabled_manually"
    inventory=a.workflow_api("/actions/workflows/378774441/runs?per_page=100")
    rows=sorted((r["id"],r["run_attempt"],r["head_sha"],r["workflow_id"],r["status"]) for r in inventory["workflow_runs"])
    assert len(rows)==inventory["total_count"]==69 and all(r[-1]=="completed" for r in rows)
    assert hashlib.sha256(json.dumps(rows,separators=(",",":")).encode()).hexdigest()=="c0c4773a0f4e8339b62f223a12bf467f1ecc217f910e16c3b64aaa05462d08eb"
    original_state=a.download_inert_artifact(a._HU481_PRODUCER,14676763124,
        "sha256:2a606102fcf91190a0b4fe704e45d1ba899a6112a800c13daa7d77c89b9b4a77")
    owned=(a._HU481_PRODUCER,14676763124,"sha256:2a606102fcf91190a0b4fe704e45d1ba899a6112a800c13daa7d77c89b9b4a77")
    result=a.production_maintenance_state(original_state,owner=owned)
    raw=json.dumps(result,sort_keys=True,separators=(",",":")).encode()
    assert hashlib.sha256(raw).hexdigest()==a._HU481_FLOOR_DIGEST
    print(f"PASS. Authenticated retired job and five immutable artifacts. Preserved {len(result['starts'])} starts. "
          f"Confirmed reviewed floor {a._HU481_FLOOR_DIGEST}. Publisher opens: zero.")
