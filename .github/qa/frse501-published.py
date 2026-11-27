"""Check the actual website inputs and outputs for the production collector."""
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

site = "https://youthopps.org"
source = "pl-frse"
prod = os.environ["FRSE_DATA_REVISION"]
site_rev = os.environ["FRSE_SITE_REVISION"]
assert re.fullmatch("[0-9a-f]{40}",prod) and re.fullmatch("[0-9a-f]{40}",site_rev)

def api(path):
    return json.loads(subprocess.check_output(["gh","api",path]))

def fetch(url):
    req=urllib.request.Request(url, headers={"User-Agent":"YouthOppsBot/1.0 (+https://youthopps.org/bot/)",
                                           "Cache-Control":"no-cache"})
    with urllib.request.urlopen(req,timeout=50) as result:
        assert result.status==200
        return result.read()

def live(path):
    url=urllib.parse.urljoin(site,path)
    assert urllib.parse.urlsplit(url).netloc=="youthopps.org"
    return fetch(url)

class Index(HTMLParser):
    def __init__(self): super().__init__(); self.urls=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if "collection" in attrs.get("class","").split() and attrs.get("data-index"):
            self.urls.append(attrs["data-index"])

def index(path):
    source_html=live(path).decode()
    parser=Index(); parser.feed(source_html)
    assert len(parser.urls)==1,path
    return source_html,json.loads(live(parser.urls[0]))

def records(commit,name):
    return json.loads(fetch(f"https://raw.githubusercontent.com/YouthOpps/data-source/{commit}/datas/pl-frse/{name}.json"))

producer=api("repos/YouthOpps/data-pipeline/actions/runs/45976904691/attempts/1")
assert producer["conclusion"]=="success" and producer["head_sha"]=="a9451c5a168699394f80c361230e0af0dd17067d"
pin=api("repos/YouthOpps/youthopps.github.io/contents/data-source?ref="+site_rev)["sha"]
committed=records(prod,"data")
metadata=records(prod,"metadata")
assert committed==records(pin,"data") and metadata==records(pin,"metadata")
assert metadata["source"]==source and metadata["status"]=="success" and metadata["record_count"]==len(committed)
assert metadata["last_attempt_at"]==metadata["last_success_at"]
prior=records("7f3a58fa24e4852c1c04bd53c0b841376f8b9248","data")
current={row["id"]:row for row in committed}
old={row["id"]:row for row in prior}
assert len(current)==len(committed)
for uid in current.keys() & old.keys():
    assert current[uid]["first_seen_at"]==old[uid]["first_seen_at"]
    assert current[uid]["created_at"]==old[uid]["created_at"]
directory=index("/sources/")[1]
matches=[row for row in directory if json.loads(row["search"])["id"]==source]
assert len(matches)==1 and matches[0]["status"]=="success"
assert "source-status active" in matches[0]["html"] and ">Success</span>" in matches[0]["html"]
assert metadata["last_success_at"] in matches[0]["html"]
global_rows=index("/opportunities/")[1]
source_html, source_rows=index("/sources/publisher/pl-frse/")
assert "source-status active" in source_html and metadata["last_success_at"] in source_html
for selected in (source_rows,[r for r in global_rows if r["source"]==source]):
    ids=[r["id"] for r in selected]
    assert len(ids)==len(current) and set(ids)==set(current)
report=json.loads(live("/build-report.json"))
assert {r["id"] for r in report["records"] if r["source"]==source}==set(current)
sitemap=ET.fromstring(live("/sitemap.xml"))
shards=[x.text for x in sitemap.iter() if x.tag.endswith("}loc") and "sitemap-source-pl-frse" in x.text]
assert shards
routes={urllib.parse.urlsplit(x.text).path for shard in shards for x in ET.fromstring(live(shard)).iter()
        if x.tag.endswith("}loc")}
assert routes=={"/opportunity/"+uid+"/" for uid in current}
# Verify every detail route and its actual publisher link, without opening the publisher.
for uid,row in current.items():
    page=html.unescape(live("/opportunity/"+uid+"/").decode())
    assert row["url"] in page and row["title"] in page, uid
uid="8f15069e97deed3bd8ae3e8a"
if uid in current:
    award=current[uid]
    assert award["url"]=="https://eduinspiracje.org.pl/eduinspirator/o-konkursie"
    assert award["kind"]=="programme-overview" and award["status"]=="unknown"
print(json.dumps({"source":source,"data_revision":prod,"site_revision":site_rev,"site_pin":pin,
    "last_success":metadata["last_success_at"],"records":len(current),"new":sorted(current.keys()-old.keys()),
    "removed":sorted(old.keys()-current.keys()),"green_sources_ui":True,"every_detail_original_link":True,
    "global_and_source_index_and_sitemap_exact":True,"edu_award_is_published":uid in current}))
