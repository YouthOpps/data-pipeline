"""Read authenticated BMW GitHub history only; deny any publisher transport."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import urllib.request
from unittest.mock import patch

slug = "de-bmw-group"
path = Path("adapters") / slug / "adapter.py"
spec = importlib.util.spec_from_file_location("bmw", path)
bmw = importlib.util.module_from_spec(spec)
sys.modules["bmw"] = bmw
spec.loader.exec_module(bmw)
gap = 45965303770
owner = 45923888649
rows = [(1, 0, owner, 1, slug), (2, 0, gap, 1, slug)]
bmw._ATTEMPT_HEADS.update({(gap,1):"9e63b6308a211ae163194eb5dff7ebd1703f92ca",
                           (owner,1):"d0c2731027b50c6e85928883133c38cfe8153053"})
def metadata(route):
    req = urllib.request.Request("https://api.github.com/repos/YouthOpps/data-pipeline"+route,
        headers={"Authorization":"Bearer "+os.environ["GITHUB_TOKEN"]})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)
with patch.object(bmw,"workflow_api",side_effect=metadata):
    assert bmw.reviewed_preaccess_owner(rows)==(owner,1,slug)
    arc=metadata(f"/actions/runs/{owner}/artifacts")["artifacts"]
    assert len(arc)==1 and arc[0]["id"]==15165713870 and arc[0]["size_in_bytes"]==1132
    assert arc[0]["digest"]=="sha256:d4a398ac805706b549acbbb5496a9d83595b29615bd30d24adbe0f2600aed8f0"
for bad in (rows[:1], rows[::-1], [(1,0,1234,1,slug),*rows]):
    try: bmw.reviewed_preaccess_owner(bad)
    except bmw.AdapterError: pass
    else: raise AssertionError("unrecognized handoff accepted")
print("Exact GitHub job and zero-artifact gap verified. Bound sole predecessor and archive digest. No publisher calls.")
