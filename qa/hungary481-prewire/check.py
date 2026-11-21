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
        assert u.path.startswith("/repos/YouthOpps/data-pipeline/actions/") or u.path.startswith("/repos/YouthOpps/data-pipeline/contents/") or u.path=="/repos/YouthOpps/data-pipeline/issues/comments/7061138411"
    else:assert u.hostname.endswith((".blob.core.windows.net",".actions.githubusercontent.com")) and req.get_header("Authorization") is None
    return orig(req,*args,**kwargs)
a._OPENER.open=github_only
canonical=lambda x:json.dumps(x,sort_keys=True,separators=(",",":")).encode()
digest=lambda x:hashlib.sha256(canonical(x)).hexdigest()
with a.execution(),a.phase("restore",180):
    assert a.workflow_api("/actions/workflows/378774441")["state"]=="disabled_manually"
    # This independent read-only workflow is outside the canonical producer inventory.
    # Scan every real completed producer. No current production job exists to exclude.
    os.environ.pop("GITHUB_ACTIONS")
    g=a.workflow_api("/issues/comments/7061138411");print(g.get("issue_url"),g.get("author_association"),g.get("user",{}).get("id"),g.get("created_at"),g.get("updated_at"),hashlib.sha256(g.get("body","").encode()).hexdigest())
    state=a.restored_state()
    assert a._HISTORY_COMPLETED[-1][2:]==a._HU481_PREWIRE
    assert len(a._HISTORY_COMPLETED)==70 and len(a._HISTORY_JOBLESS)==1
    assert len(state["starts"])==179 and digest(state)=="822c8b19eb08c67160293c09b3427b68baac25b6f0b4c881315714d13b150e17"
    print("Restored full actual 71-attempt chronology and both approved floors, 179 starts; zero publisher requests.", digest(state))
