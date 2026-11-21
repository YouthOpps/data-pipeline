"""Exercise the production research escrow with a synthetic journal, without any publisher code."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

source = Path(__file__).resolve().parents[1] / "hungary481-homepage"
sys.path.insert(0, str(source))
from escrow import upload

repo = "YouthOpps/data-pipeline"
identity = {
    "run_id": int(os.environ["GITHUB_RUN_ID"]), "run_attempt": 1, "head": os.environ["GITHUB_SHA"],
    "ref": "refs/heads/qa/hungary481-escrow-infrastructure-probe",
}
assert os.environ["GITHUB_REF"] == identity["ref"] and os.environ["GITHUB_RUN_ATTEMPT"] == "1"


def api(route):
    assert route.startswith("/actions/artifacts/") and route.split("/")[-1].isdecimal()
    request = Request("https://api.github.com/repos/" + repo + route,
                      headers={"Authorization": "Bearer " + os.environ["GITHUB_TOKEN"]})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def seal(raw):
    return subprocess.check_output(["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
                                    "-recip", str(source / "reviewer.pem")], input=raw)


for phase in ("pending-01", "settled-01"):
    result = upload(identity, phase, {"synthetic": True, "phase": phase}, api, seal)
    print(json.dumps(result))
