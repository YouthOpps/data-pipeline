"""Upload one immutable encrypted receipt and verify its GitHub owner before proceeding."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

UPLOAD_REVISION = "ea165f8d65b6e75b540449e92b4886f43607fa02"
UPLOAD_SHA256 = "0165b8a75330f3228f2c7a234b4ff8a107b9139c2b519147f4c8e9fe99b262d8"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def upload(identity, stem, receipt, api, seal):
    require(re.fullmatch(r"(pending|settled)-0[12]", stem), "Unexpected escrow checkpoint")
    name = f"hu481-{identity['run_id']}-{identity['run_attempt']}-{stem}"
    bundle = Path(os.environ["RUNNER_TEMP"]).parent / "_actions/actions/upload-artifact" / UPLOAD_REVISION / "dist/upload/index.js"
    require(hashlib.sha256(bundle.read_bytes()).hexdigest() == UPLOAD_SHA256, "Artifact client differs")
    require(bool(os.getenv("ACTIONS_RUNTIME_TOKEN")) and bool(os.getenv("ACTIONS_RESULTS_URL")),
            "Hosted artifact authority unavailable")

    with tempfile.TemporaryDirectory(prefix="hu481-escrow-") as temp:
        directory = Path(temp)
        cipher = directory / "receipt.cms"
        raw = json.dumps({"identity": identity, "checkpoint": stem, "receipt": receipt},
                         sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        cipher.write_bytes(seal(raw))
        output = directory / "outputs"
        output.touch(mode=0o600, exist_ok=False)
        env = {**os.environ, "GITHUB_OUTPUT": str(output), "INPUT_NAME": name, "INPUT_PATH": str(cipher),
               "INPUT_IF-NO-FILES-FOUND": "error", "INPUT_RETENTION-DAYS": "90",
               "INPUT_COMPRESSION-LEVEL": "0", "INPUT_OVERWRITE": "false", "INPUT_INCLUDE-HIDDEN-FILES": "false"}
        subprocess.run([os.environ["HU481_NODE"], str(bundle)], env=env, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60, check=True)
        values = output.read_text().splitlines()

    def field(key, pattern):
        found = []
        for index, line in enumerate(values[:-2]):
            if line.startswith(key + "<<") and values[index + 2] == line.split("<<", 1)[1]:
                found.append(values[index + 1])
        require(len(found) == 1 and re.fullmatch(pattern, found[0]), "Unverified artifact client output")
        return found[0]

    artifact = int(field("artifact-id", r"[1-9][0-9]*"))
    digest = field("artifact-digest", r"[a-f0-9]{64}")
    row = api(f"/actions/artifacts/{artifact}")
    run = row.get("workflow_run", {})
    require(row.get("id") == artifact and row.get("name") == name and row.get("expired") is False
            and row.get("digest") == "sha256:" + digest and run.get("id") == identity["run_id"]
            and run.get("head_sha") == identity["head"] and "refs/heads/" + str(run.get("head_branch")) == identity["ref"],
            "Remote escrow ownership differs")
    return {"artifact_id": artifact, "digest": digest, "name": name}
