"""One original paused-owner policy review. Perform no dataset publication."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse

sys.path.insert(0, str(Path("adapters/de-bmw-group").resolve()))
import adapter as b

REPO = "YouthOpps/data-pipeline"
BRANCH = "qa/494-legal-body"
BASE = "e3578569d7bf8d776229d359e0780442c4fd2faf"
OWNER = (45966059718, 1, b.SOURCE_ID)
ARCHIVE = (15183720280, 1133, "sha256:6fb6a4b0409d7e67f728da3e9060f9324e319cb09dc4de8ca3d1fbb162f5bca0")
URLS = ("https://www.bmwgroup.jobs/robots.txt", b.GLOBAL_LEGAL_URL)
ROOT = Path(__file__).resolve().parent
OUT = Path(os.environ["RUNNER_TEMP"]) / "bmw494-evidence"
opened = []
acknowledged = []
def check(ok, reason):
    if not ok:
        raise RuntimeError(reason)

def live_authority():
    b.verify_local_workflow_pause()
    run_id = int(os.environ["GITHUB_RUN_ID"])
    run = b.workflow_api(f"/actions/runs/{run_id}/attempts/1")
    check(run["head_sha"] == os.environ["GITHUB_SHA"] and run["head_branch"] == BRANCH
          and run["path"] == ".github/workflows/qa-bmw494-capture.yml"
          and run["event"] == "push" and run["run_attempt"] == 1 and run["status"] == "in_progress"
          and all(run[x]["full_name"] == REPO for x in ("repository","head_repository"))
          and all(run[x]["id"] == 12278069 for x in ("actor","triggering_actor")), "unexpected research attempt")
    history = b.workflow_api(f"/actions/workflows/{run['workflow_id']}/runs?per_page=2")
    check(history["total_count"] == 1 and [r["id"] for r in history["workflow_runs"]] == [run_id], "research owner not unique")
    newest = b.workflow_api("/actions/workflows/385601295/runs?per_page=1")
    check(newest["workflow_runs"][0]["id"] == OWNER[0] and newest["workflow_runs"][0]["status"] == "completed"
          and newest["workflow_runs"][0]["run_attempt"] == 1 and newest["workflow_runs"][0]["head_sha"] == BASE,
          "production owner moved")

def authorization():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
    head = os.environ["GITHUB_SHA"]
    check(event.get("created") is True and event.get("before") == "0"*40 and event.get("after") == head
          and event.get("ref") == "refs/heads/"+BRANCH and os.environ["GITHUB_RUN_ATTEMPT"] == "1"
          and os.environ["GITHUB_REPOSITORY"] == REPO and not os.environ.get("DATA_SOURCE_TOKEN"), "unexpected scope")
    check(subprocess.check_output(["git","rev-parse","HEAD^"],text=True).strip() == BASE, "producer base moved")
    comments = b.workflow_api("/issues/494/comments?per_page=100")
    grant = "Approved BMW narrow legal-disclaimer research head " + head
    check(any(c["user"]["id"]==12278069 and grant in c["body"].splitlines() for c in comments), "missing exact review grant")
    live_authority()

def history():
    latest = b.scan_family_history(paused_research=True)
    check(latest == OWNER, "newest chronological owner moved")
    arcs = b.workflow_api(f"/actions/runs/{OWNER[0]}/artifacts")["artifacts"]
    check(len(arcs)==1 and (arcs[0]["id"],arcs[0]["size_in_bytes"],arcs[0]["digest"])==ARCHIVE and not arcs[0]["expired"],"archive mismatch")
    return latest

def main():
    OUT.mkdir(mode=0o700)
    opener = b._OPENER.open
    receipts = []
    def persist():
        name = OUT/"requests.json"
        tmp = name.with_suffix(".tmp")
        tmp.write_text(json.dumps(receipts))
        tmp.replace(name)
    class Receipt:
        def __init__(self, response, row):
            self.response, self.row = response, row
        def __getattr__(self, name):
            return getattr(self.response, name)
        def __enter__(self):
            self.response.__enter__()
            return self
        def __exit__(self, *exc):
            try:
                value = self.response.__exit__(*exc)
            except BaseException as error:
                self.row["close_error"] = type(error).__name__
                raise
            else:
                self.row["closed"] = True
                return value
            finally:
                self.row["completion_monotonic"] = time.monotonic()
                self.row["completion_wall"] = time.time()
                if exc[0]:
                    self.row["assessment_error"] = exc[0].__name__
                persist()
    def limited(request, **kw):
        url = request.full_url
        if urllib.parse.urlsplit(url).hostname not in b._PUBLISHER_HOSTS:
            return opener(request, **kw)
        check(len(opened)<len(URLS) and URLS[len(opened)]==url and request.method=="GET"
              and request.get_header("User-agent")==b._USER_AGENT, "unexpected publisher request")
        live_authority()
        opened.append(url)
        row = {"url":url,"reserved":True}
        receipts.append(row)
        persist()
        try:
            row["start_monotonic"] = time.monotonic()
            row["start_wall"] = time.time()
            response = opener(request, **kw)
        except urllib.error.HTTPError as error:
            response = error
        except BaseException as error:
            row["open_error"] = type(error).__name__
            row["error_wall"] = time.time()
            persist()
            raise
        row["status"] = response.status
        persist()
        return Receipt(response,row)
    write = b.write_budget
    def observed(state):
        value = write(state)
        raw = b.journal_verify()
        check(json.loads(raw)==state, "original commitment differs")
        acknowledged.append(hashlib.sha256(raw).hexdigest())
        (OUT/"acknowledged-state.json").write_bytes(raw)
        (OUT/"state-history.json").write_text(json.dumps(acknowledged))
        return value
    b.write_budget = observed
    b._OPENER.open = limited
    b.authenticate_hosted_run = authorization
    b.latest_family_run = history
    with b.execution():
        with b.phase("restore", 120):
            b.prepare_collection()
        with b.phase("collection", b.COLLECTION_TIMEOUT):
            raw = b.fetch_source(b.GLOBAL_LEGAL_URL)
            subprocess.run(["openssl","cms","-encrypt","-binary","-aes256","-outform","DER",
                            "-out",str(OUT/"policy.cms"),str(ROOT/"reviewer.pem")],
                           input=raw.encode(),check=True)
            body = b.clean(b.single(b.NativeHTML(raw).root.find("main"),"policy body").text())
            check(opened==list(URLS), "physical request count differs")
            check(not json.loads(b.journal_verify())["in_flight"],"unsettled source state")
            (OUT/"assessment.json").write_text(json.dumps({"url":b.GLOBAL_LEGAL_URL,"source_head":BASE,
                "normalized_sha256":hashlib.sha256(body.encode()).hexdigest(),"opened":len(opened),
                "final_state_sha256":acknowledged[-1]}))
if __name__=="__main__":
    main()
