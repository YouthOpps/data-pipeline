"""Execute the exact nonpublishing UC3M test and check its complete RAM capture."""

import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from email.message import Message
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "adapters/es-uc3m-admissions/adapter.py"
WEBSITE = ROOT / ".qa-website"
WEBSITE_REVISION = "fe54fa839233f34973991584567408acae269487"
EXPECTED_BRANCH = "refs/heads/issue/421-uc3m-hosted-qa"
CERTIFICATE = ROOT / ".github/uc3m-421-review.pem"
CONSUMER = """
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const {loadData} = await import(pathToFileURL(resolve('.qa-website/scripts/data.mjs')));
let input = '';
for await (const part of process.stdin) input += part;
const {records,metadata} = JSON.parse(input);
const source = 'es-uc3m-admissions';
if(metadata.source !== source || records.some(record=>record.source!==source)) {
  throw new Error('Observer source identity mismatch');
}
const root = '/ram/datas';
const files = new Map([
  [root + '/' + source + '/data.json',JSON.stringify(records)],
  [root + '/' + source + '/metadata.json',JSON.stringify(metadata)],
]);
fs.lstat = async file => {
  if(file !== root && !files.has(file)) throw new Error('Unexpected observer path');
  return {isSymbolicLink:()=>false};
};
fs.readdir = async file => {
  if(file !== root) throw new Error('Unexpected observer root');
  return [{name:source,isSymbolicLink:()=>false,isDirectory:()=>true}];
};
fs.readFile = async file => {
  if(!files.has(file)) throw new Error('Missing observer input');
  return files.get(file);
};
const result = await loadData(root);
const expected = records.map(record=>record.id).sort();
const actual = result.opportunities.map(record=>record.id).sort();
if(JSON.stringify(expected)!==JSON.stringify(actual)) throw new Error('Observer ID mismatch');
console.log(JSON.stringify({count:actual.length,ids:actual,sources:result.sources.map(item=>item.id)}));
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def consume(records, metadata, *, positive=True):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", CONSUMER],
        cwd=ROOT,
        input=json.dumps({"records": records, "metadata": metadata}, ensure_ascii=False),
        encoding="utf-8", capture_output=True, timeout=25, check=False,
    )
    if positive:
        if result.returncode:
            raise RuntimeError("Actual website consumer rejected the native result")
        return json.loads(result.stdout)
    if result.returncode == 0:
        raise RuntimeError("Wrong-source negative accepted by the observer consumer boundary")
    return {"rejected": True}


def consume_complete(adapter, raw):
    records = json.loads(raw)
    adapter.validate_records(records)
    stamp = adapter.utc_now()
    filtered = adapter.remove_expired_records(records, stamp)
    wrong = adapter.metadata(stamp, {}, records)
    wrong["source"] = "unreviewed-source"
    return {
        "raw": consume(records, adapter.metadata(stamp, {}, records)),
        "current": consume(filtered, adapter.metadata(stamp, {}, filtered)),
        "valid_empty": consume([], adapter.metadata(stamp, {}, [])),
        "wrong_source": consume(records, wrong, positive=False),
    }


def checked_audit(adapter):
    rows, receipts = [], []
    for event in adapter.AUDIT["requests"]:
        body = b"".join(event["segments"])
        fields = {key: event.get(key) for key in (
            "url", "request_id", "start", "completion", "wall_start", "wall_completion",
            "actual_start", "actual_wall_start", "status", "error", "read_error", "close_error",
        )}
        fields.update(body_bytes=len(body), body_sha256=sha(body),
                      header_pairs=len(event["headers"]),
                      header_sha256=sha(adapter.canonical(event["headers"])),
                      recorded_user_agent=[
                          value for key, value in event["request_headers"]
                          if key.casefold() == "user-agent"])
        rows.append(fields)
    for event in adapter.AUDIT["persistence"]:
        expected = event.get("bytes")
        observed = event.get("actual_journal_bytes")
        receipts.append({
            "acknowledged": event["acknowledged"],
            "exact": isinstance(expected, bytes) and expected == observed,
            "sha256": sha(expected) if isinstance(expected, bytes) else None,
            "physical_id": event.get("intended", {}).get("physical_id"),
            "completed_physical_id": event.get("intended", {}).get("completed_physical_id"),
            "physical_pending": event.get("intended", {}).get("physical_pending"),
            "physical_started": event.get("intended", {}).get("physical_started"),
            "acknowledged_at": event.get("completed_monotonic"),
            "interval": event.get("before", {}).get("interval"),
            "retained_prior_completion": event.get("before", {}).get("completed"),
        })
    return rows, receipts


def instrument_opener(adapter):
    original = adapter.OPENER.open

    def checked_open(request, *args, **kwargs):
        if request.full_url in adapter.URLS:
            event = adapter.AUDIT["requests"][-1]
            if (event["url"] != request.full_url or "actual_start" in event
                    or request.get_header("User-agent") != adapter.USER_AGENT):
                raise RuntimeError("Exact original opener ownership differs")
            event["actual_wall_start"] = adapter.now()
            event["actual_start"] = adapter.CLOCK.monotonic()
        elif not request.full_url.startswith("https://api.github.com/repos/YouthOpps/data-pipeline/"):
            raise RuntimeError("Unexpected exact QA opener origin")
        return original(request, *args, **kwargs)

    adapter.OPENER.open = checked_open


def certify(adapter, rows, receipts):
    if (tuple(row["url"] for row in rows) != adapter.URLS
            or any(row["error"] or row["read_error"] or row["close_error"]
                   or row["completion"] is None or row["actual_start"] is None
                   or row["url"] != adapter.ROBOTS and row["body_bytes"] <= 0
                   or row["recorded_user_agent"] != [adapter.USER_AGENT] for row in rows)):
        raise RuntimeError("Incomplete original four-wire capture")
    for row in rows:
        writes = [x for x in receipts if x["physical_id"] == row["request_id"]]
        reserved = [x for x in writes if x["physical_pending"] and x["acknowledged"] and x["exact"]]
        assessed = [x for x in writes if not x["physical_pending"] and x["acknowledged"] and x["exact"]
                    and x["completed_physical_id"] == row["request_id"]]
        if not reserved or len(assessed) != 1:
            raise RuntimeError("Missing original physical ownership transition")
        first = reserved[0]
        if (not first["acknowledged_at"] <= row["start"] <= row["actual_start"]
                <= row["completion"] <= assessed[0]["acknowledged_at"]
                or not first["physical_started"] <= row["wall_start"] <= row["actual_wall_start"]
                <= row["wall_completion"]
                or row["actual_wall_start"] < first["retained_prior_completion"] + first["interval"]):
            raise RuntimeError("Original reservation, opener, and response closure order differs")
    starts = [row["actual_start"] for row in rows]
    if (any(b["actual_start"] - a["completion"] < 12.2 for a, b in zip(rows, rows[1:]))
            or rows[2]["actual_wall_start"] < (
                adapter.QA_HOSTED_APPROVAL["state"]["inventory_completed"]
                + adapter.QA_HOSTED_APPROVAL["state"]["page_interval"]
            )
            or max(sum(current - 60 < earlier <= current for earlier in starts)
                   for current in starts) > 5):
        raise RuntimeError("Original physical pacing check failed")


def replay_actual_bodies(adapter, records):
    def decode(event):
        headers = Message()
        for key, value in event["headers"]:
            headers[key] = value
        body = b"".join(event["segments"])
        return body, headers

    responses = adapter.AUDIT["requests"]
    policy, policy_headers = decode(responses[1])
    if sha(adapter.policy_text(policy, policy_headers).encode()) != adapter.POLICY_TEXT_SHA:
        raise RuntimeError("Actual policy replay differs")
    calendar, criteria = (decode(response) for response in responses[2:])
    fields = [body.decode(headers.get_content_charset() or "utf-8", "strict")
              for body, headers in (calendar, criteria)]
    stamp = records[0]["last_checked_at"]
    replay = adapter.admission_record(*fields, datetime.fromisoformat(stamp))
    replay.update(source=adapter.SOURCE_ID, location="Madrid, Spain", created_at=stamp,
                  updated_at=stamp, first_seen_at=stamp, last_seen_at=stamp, last_checked_at=stamp)
    if [replay] != records:
        raise RuntimeError("Actual original body replay differs from the full native output")


def encoded_bytes(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


def preserve_evidence(adapter, report, stdout, stderr):
    evidence = {
        "report": report, "audited_original_transport": adapter.AUDIT,
        "original_stdout": stdout, "original_stderr": stderr,
    }
    plaintext = json.dumps(evidence, sort_keys=True, allow_nan=False, default=encoded_bytes).encode()
    ciphertext = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-outform", "DER", "-aes-256-gcm",
         str(CERTIFICATE)],
        input=plaintext, capture_output=True, timeout=30, check=True,
    ).stdout
    output = Path(os.environ["RUNNER_TEMP"]) / "uc3m-qa-evidence.cms"
    with output.open("xb") as file:
        file.write(ciphertext)
    report["encrypted_evidence_sha256"] = sha(ciphertext)
    report["plaintext_evidence_sha256"] = sha(plaintext)
    report["review_certificate_sha256"] = sha(CERTIFICATE.read_bytes())


def load_adapter():
    spec = importlib.util.spec_from_file_location("adapter", SOURCE)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def main():
    if (os.environ.get("GITHUB_ACTIONS") != "true"
            or os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
            or os.environ.get("GITHUB_REF") != EXPECTED_BRANCH
            or "DATA_SOURCE_TOKEN" in os.environ):
        raise RuntimeError("Exact GitHub Actions QA scope required")
    revision = subprocess.check_output(
        ["git", "-C", str(WEBSITE), "rev-parse", "HEAD"], text=True, timeout=10).strip()
    if revision != WEBSITE_REVISION:
        raise RuntimeError("Original website consumer revision differs")
    test = SOURCE.with_name("test_adapter.py")
    report = {"head": os.environ["GITHUB_SHA"], "website_revision": revision,
              "source_sha256": sha(SOURCE.read_bytes()), "test_sha256": sha(test.read_bytes()),
              "original_outcome": None, "observer_outcome": 1}
    adapter = load_adapter()
    if sha(CERTIFICATE.read_bytes()) != adapter.QA_CERT_SHA256:
        raise RuntimeError("Decision-bound public review certificate differs")
    instrument_opener(adapter)
    sys.modules["adapter"] = adapter
    out, err = io.StringIO(), io.StringIO()
    try:
        saved = sys.argv
        try:
            sys.argv = [str(test)]
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    runpy.run_path(str(test), run_name="__main__")
                    report["original_outcome"] = 0
                except SystemExit as error:
                    report["original_outcome"] = int(error.code or 0)
        finally:
            sys.argv = saved
        if report["original_outcome"] != 0:
            raise RuntimeError("Original nonpublishing test failed")
        rows, receipts = checked_audit(adapter)
        certify(adapter, rows, receipts)
        report["consumer"] = consume_complete(adapter, out.getvalue())
        report["records"] = json.loads(out.getvalue())
        replay_actual_bodies(adapter, report["records"])
        report["complete_original_body_replay"] = True
        report["observer_outcome"] = 0
    except BaseException as error:
        report["error_type"] = type(error).__name__
        report["error"] = str(error) if type(error) is RuntimeError else "Original test evidence unavailable"
    finally:
        report["requests"], report["persistence"] = checked_audit(adapter)
        report["original_stdout_sha256"] = sha(out.getvalue().encode("utf-8"))
        report["original_stderr_sha256"] = sha(err.getvalue().encode("utf-8"))
        report["state_artifact_present"] = Path(os.environ["UC3M_PACING_ARTIFACT_PATH"]).exists()
        preserve_evidence(adapter, report, out.getvalue(), err.getvalue())
        path = Path(os.environ["RUNNER_TEMP"]) / "uc3m-qa-observations.json"
        with path.open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, sort_keys=True)
    print(json.dumps({key: report[key] for key in ("original_outcome", "observer_outcome",
                                                "state_artifact_present", "source_sha256")}))
    return report["observer_outcome"]


if __name__ == "__main__":
    raise SystemExit(main())
