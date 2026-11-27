"""Validate the exact state transition and accessibility exception without contacting BMW."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import urllib.request
import zipfile
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("bmw",Path("adapters/de-bmw-group/adapter.py"))
b=importlib.util.module_from_spec(spec)
sys.modules["bmw"]=b
spec.loader.exec_module(b)
token=os.environ["GITHUB_TOKEN"]
repo="https://api.github.com/repos/YouthOpps/data-pipeline"
def get(path):
    headers={"Authorization":"Bearer "+token}
    with urllib.request.urlopen(urllib.request.Request(repo+path,headers=headers),timeout=20) as res:
        return json.load(res)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None
def archive(i,digest):
    uri=repo+f"/actions/artifacts/{i}/zip"
    op=urllib.request.build_opener(NoRedirect)
    try: op.open(urllib.request.Request(uri,headers={"Authorization":"Bearer "+token}),timeout=20)
    except urllib.error.HTTPError as err:
        assert err.code==302
        target=err.headers["Location"]
        err.close()
    assert urllib.parse.urlsplit(target).hostname.endswith((".blob.core.windows.net",".actions.githubusercontent.com"))
    with urllib.request.urlopen(target,timeout=20) as r:data=r.read(65537)
    assert len(data)<=65536 and hashlib.sha256(data).hexdigest()==digest
    return zipfile.ZipFile(io.BytesIO(data))
with archive(15183720280,"6fb6a4b0409d7e67f728da3e9060f9324e319cb09dc4de8ca3d1fbb162f5bca0") as z:
    initial=json.loads(z.read("pacing-state.json"))["state"]
with archive(15183207238,"4f29eb5f7f3b21b84dea6fa2552c37e999b8a16a8c4fb77e36ed0f5238c99a4e") as z:
    research=json.loads(z.read("acknowledged-state.json"))
    wire=json.loads(z.read("requests.json"))
    assert len(wire)==2 and all(row["closed"] and row["status"]==200 for row in wire)
    assert hashlib.sha256(z.read("acknowledged-state.json")).hexdigest()==json.loads(z.read("state-history.json"))[-1]
    assert json.loads(z.read("assessment.json"))["normalized_sha256"]=="79017c4857929269a178c458ac605df7e552947f89218c39e32f4a7d561fb452"
with patch.object(b,"workflow_api",side_effect=get):
    result=b.legal_review_handoff(initial)
assert set(result["starts"])==set(initial["starts"])|set(research["starts"])|{row["start_wall"] for row in wire}
assert len(result["starts"])==9
for k in result:
    if k not in ("starts","completed","until","observed_at"):assert initial[k]==research[k]==result[k]
assert result["completed"]>=max(r["completion_wall"] for r in wire) and result["until"]>=result["completed"]+12.2
def fp(html):return b.policy_fingerprint(b.single(b.NativeHTML("<main>"+html+"</main>").root.find("main"),"policy"))
base="Terms. <a href='/company' target='_blank' aria-describedby='hint'>Compliance%s</a>. Conditions apply."
hint="<span id='hint' class='sr-only'>(opens in a new window)</span>"
assert fp(base%hint)==fp(base%"")
for sample in (base.replace("_blank","_self")%hint,base%hint.replace("id='hint'","id='other'"),
               base%hint.replace("sr-only","notice"),base%hint.replace("window","restricted window"),
               base.replace("Conditions apply","Automated indexing is prohibited")%hint):
    assert fp(sample)!=fp(base%"")
print("Full nine-start MAX reconstructed from authentic GitHub state. Visible or substantively different notice changes rejected.")
