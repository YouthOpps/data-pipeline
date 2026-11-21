"""Run the original sole test with retained in-memory evidence and immutable per-opener state."""
import base64
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

import escrow
import support as q

digest = lambda raw: hashlib.sha256(raw).hexdigest()
routes = ["https://fulbright.hu/robots.txt", "https://fulbright.hu/",
          "https://fulbright.hu/robots.txt", "https://fulbright.hu/special-short-term-grants/",
          "https://fulbright.hu/robots.txt", "https://fulbright.hu/fulbright-awards/for-us-citizens/"]
scope = json.loads(q.SCOPE.read_bytes())
q.demand(scope["enabled"] is True and scope["branch"] == q.BRANCH and scope["routes"] == routes,
         "Native validation remains off")
q.demand("DATA_SOURCE_TOKEN" not in os.environ and "HUNGARY_PACING_ARTIFACT_PATH" not in os.environ,
         "Publisher validation cannot carry publication credentials")
head = os.environ["GITHUB_SHA"]
q.demand(re.fullmatch(r"[a-f0-9]{40}", head)
         and subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==head
         and subprocess.check_output(["git","-C",os.environ["HU481_WEBSITE"],"rev-parse","HEAD"],text=True).strip()
             ==scope["website_revision"], "Producer or website checkout differs")
for name, expected in scope["files"].items():
    q.demand(digest((q.CHECKOUT/name).read_bytes()) == expected, "Native input changed: " + name)
subprocess.run(["openssl","x509","-in",str(q.CERTIFICATE),"-checkend","1800","-noout"],check=True)
spec=importlib.util.spec_from_file_location("adapter",q.SOURCE)
a=importlib.util.module_from_spec(spec);sys.modules["adapter"]=a;spec.loader.exec_module(a)
workflow=(q.CHECKOUT/scope["production_workflow_path"]).read_text()
for k in ("HUNGARY_HANDOFF_COLLECTOR_BLOB","HUNGARY_PACING_MAINTENANCE"):
    os.environ[k]=re.search(r"^\s+"+k+r": '([^']+)'$",workflow,re.M)[1]

out=Path(os.environ["RUNNER_TEMP"])/"hu481-native"
out.mkdir(mode=0o700)
identity=None
job_limit=None
opens=[]
journal=[]
restored=False
final=None
pending=False
native_exit=None
diagnostic=None
stdout,stderr=io.StringIO(),io.StringIO()
data_output={}
filter_at=None

def gate():
    q.paused(a,scope)
    q.demand(identity is not None and job_limit is not None,"Native job is unbound")
    q.verify_grant(a,identity,scope)
    a._RUN_END=min(job_limit,a._RUN_END)

def history():
    snapshot=q.paused(a,scope)
    rows=sorted(r for run in snapshot["workflow_runs"] if (r:=a.completed_identity(run)) is not None)
    owner=scope["production_owner"]
    exact=owner["run"],owner["attempt"],a.SOURCE_ID,owner["head"]
    q.demand(len(rows)>=2 and rows[-1][0]>rows[-2][0] and rows[-1][2:]==exact,"Owner history differs")
    a._HISTORY_COMPLETED=rows
    return exact

orig_download=a.download_inert_artifact
def pinned_download(owner,id_,checksum):
    global restored
    pin=scope["production_owner"]
    q.demand(owner==(pin["run"],pin["attempt"],a.SOURCE_ID,pin["head"])
             and id_==pin["artifact"] and checksum==pin["digest"],"Archive owner changed")
    state=orig_download(owner,id_,checksum)
    q.demand(not state["blocked"] and not state["in_flight"] and state["rate_limit_until"]<=time.time(),
             "Restored predecessor is held")
    restored=True
    return state

a.latest_family_run=history
a.download_inert_artifact=pinned_download
a.workflow_mode_guard=gate
a.current_job_context=gate

def ack():
    return q.original_ack(a)

def durable(stem, data):
    q.store(out/(stem+".json"),q.canonical(data))
    receipt=escrow.upload(identity,stem,data,a.workflow_api,q.seal)
    q.store(out/(stem+"-ack.json"),q.canonical(receipt))

original_save=a.save_budget
def save(state):
    global pending,final
    attempt={"time":time.time(),"state":copy.deepcopy(state)}
    journal.append(attempt)
    try:
        result=original_save(state)
        a.verify_journal()
        raw=a._JOURNAL_BYTES
        q.demand(raw and json.loads(raw)==state,"Original durable write differs")
        attempt["raw_base64"]=base64.b64encode(raw).decode()
        attempt["sha256"]=digest(raw)
        final=copy.deepcopy(state)
        if pending and not state["in_flight"]:
            row=opens[-1]
            q.demand(row.get("closed") and state["completed_request_id"]==row["reservation"]["state"]["request_id"]
                     and state["completed_flight_id"]==row["reservation"]["state"]["flight_id"]
                     and state["request_outcome"]=="response_assessed","Settlement remains ambiguous")
            q.demand(state["completed"]>=row["wall_end"] and state["not_before"]>=row["wall_end"]+state["interval"]
                     and state["starts"]==row["reservation"]["state"]["starts"],
                     "Settlement values differ from the physical operation")
            physical={k:row.get(k) for k in ("url","start","wall_start","end","wall_end","closed","status","read_failure","close_failure")}
            durable(f"settled-{len(opens):02}",{"journal":attempt,"physical":physical,
                    "reservation_sha256":row["reservation"]["sha256"]})
            pending=False
        return result
    except BaseException as exc:
        attempt["error"]=type(exc).__name__
        raise
a.save_budget=save
original_open=a._OPENER.open
def observe(req,*args,**kwargs):
    global pending
    u=urlsplit(req.full_url)
    if not a.is_publisher(req.full_url):
        allowed=(u.hostname=="api.github.com" and u.path.startswith("/repos/YouthOpps/data-pipeline/")
                 or u.hostname and u.hostname.endswith((".blob.core.windows.net",".actions.githubusercontent.com"))
                 and req.get_header("Authorization") is None)
        q.demand(allowed and req.get_method()=="GET" and req.get_header("Cookie") is None,"Unexpected infrastructure route")
        return original_open(req,*args,**kwargs)
    ix=len(opens)
    q.demand(restored and not pending and ix < len(routes) and req.full_url==routes[ix]
             and req.get_method()=="GET" and req.data is None and req.get_header("User-agent")==a._USER_AGENT
             and req.get_header("Authorization") is None and req.get_header("Cookie") is None,
             "Native request outside authorized scope")
    gate()
    reserve=ack()
    q.demand(reserve["state"]["in_flight"] and reserve["state"]["request_outcome"]=="reserved"
             and reserve["state"]["request_id"]==a._ACTIVE_REQUEST_ID,"Original pending ACK differs")
    durable(f"pending-{ix+1:02}",{"reservation":reserve,"url":req.full_url,"identity":identity})
    gate()
    q.demand(ack()==reserve,"Original reservation changed before opener")
    row={"url":req.full_url,"reservation":reserve,"start":time.monotonic(),"wall_start":time.time(),"segments":[]}
    if opens:q.demand(row["start"]-opens[-1]["end"]>=reserve["state"]["interval"],"Completion spacing insufficient")
    opens.append(row);pending=True
    def attach(response):
        row.update(status=response.status,headers=list(response.headers.raw_items()))
        read,close=response.read,response.close
        def capture(*args,**kwargs):
            try:body=read(*args,**kwargs)
            except BaseException as err:
                partial=getattr(err,"partial",None)
                if isinstance(partial,bytes):row["segments"].append(base64.b64encode(partial).decode())
                row["read_failure"]=type(err).__name__;raise
            row["segments"].append(base64.b64encode(body).decode());return body
        def finish():
            try:result=close()
            except BaseException as err:row["close_failure"]=type(err).__name__;raise
            row.update(closed=True,end=time.monotonic(),wall_end=time.time());return result
        response.read=capture;response.close=finish
        return response
    try:response=original_open(req,*args,**kwargs)
    except HTTPError as exc:attach(exc);raise
    except BaseException as exc:row["open_failure"]=type(exc).__name__;raise
    return attach(response)
a._OPENER.open=observe

def consumer(records,meta):
    result=subprocess.run(["node",str(q.HERE/"consumer.mjs")],input=json.dumps({"records":records,"metadata":meta}),
                          capture_output=True,text=True,check=True,timeout=30)
    return json.loads(result.stdout)
try:
    with a.execution(),a.phase("restore",180):
        identity=q.authenticate(a,scope,head)
        job_limit=a._RUN_END
    with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
        test=q.SOURCE.with_name("test_adapter.py")
        sys.argv=[str(test)]
        try:runpy.run_path(str(test),run_name="__main__")
        except SystemExit as exc:native_exit=exc.code
        else:native_exit=0
    if native_exit==0:
        records=json.loads(stdout.getvalue())
        a.validate_records(records)
        filter_at=datetime.now(timezone.utc).isoformat()
        filtered=a.remove_expired_records(records,datetime.fromisoformat(filter_at))
        stamp=a.utc_now()
        for name,rows in (("raw",records),("filtered",filtered),("empty",[])):
            a.validate_records(rows,allow_empty=not rows)
            data_output[name]=consumer(rows,a.metadata(stamp,{},rows))
        q.demand(len(opens)==len(routes) and not pending and final is not None,"Incomplete source evidence")
except BaseException as exc:
    diagnostic=type(exc).__name__+": "+str(exc)
pending_names={f.name[:10] for f in out.glob("pending-[0-9][0-9].json")}
settled_names={"pending-"+f.name[8:10] for f in out.glob("settled-[0-9][0-9]-ack.json")}
unresolved=bool(pending_names - settled_names)
raw=q.canonical({"identity":identity,"native_exit":native_exit,"observer_error":diagnostic,"stdout":stdout.getvalue(),
                 "stderr":stderr.getvalue(),"opens":opens,"journal":journal,"final":final,"filter_at":filter_at,"consumer":data_output})
if identity is not None:
    terminal={"identity":identity,"number_opened":len(opens),"native_exit":native_exit,
              "settled_count":len(settled_names),"unresolved":unresolved or pending,
              "final_journal":final,"evidence_sha256":digest(raw),
              "request_ids":[r["reservation"]["state"]["request_id"] for r in opens],
              "observer_error":None if diagnostic is None else diagnostic.split(":",1)[0],
              "remote_checkpoints":{f.stem.replace("-ack",""):json.loads(f.read_bytes())
                                    for f in sorted(out.glob("*-ack.json"))}}
    try:durable("terminal",terminal)
    except Exception as exc:
        diagnostic=diagnostic or ("Terminal escrow failure: "+type(exc).__name__)
sealed=q.seal(raw)
q.store(out/"evidence.cms",sealed)
summary={"identity":identity,"native_exit":native_exit,"observer_error":None if not diagnostic else diagnostic.split(":",1)[0],
         "physical_requests":len(opens),"pending":unresolved or pending,"evidence_sha256":digest(sealed),"consumers":data_output}
q.store(out/"summary.json",q.canonical(summary))
print(json.dumps(summary))
raise SystemExit(0 if native_exit==0 and not diagnostic else 1)
