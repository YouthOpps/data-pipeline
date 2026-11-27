"""Run the unmodified native test under the container's read-only maintenance role."""
from pathlib import Path
import json
import os
import subprocess
import sys
import traceback
os.umask(0o077)
try:
    root = Path("/src")
    child = subprocess.run([sys.executable, "-B", "adapters/pl-frse/test_adapter.py"],
                           cwd=root, env={**os.environ, "PYTHONPATH": "/src/.github/qa",
                                         "FRSE_NATIVE_OBSERVER": "1"}, capture_output=True)
    proof = json.loads(Path("/ram/proof.json").read_text())
    rows = proof["requests"]
    assert proof["persisted"] and rows and not proof["invalid_write_count"]
    assert all(r["fully_read"] and not any(r[k] for k in
               ("read_failure","open_failure","close_failure")) for r in rows)
    assert all(row["completion"] is not None for row in rows)
    gaps = [b["start"]-a["completion"] for a,b in zip(rows,rows[1:])]
    inventory = [r for r in rows if r["inventory"]]
    page_gaps = [b["start"]-a["completion"] for a,b in zip(inventory,inventory[1:])]
    assert gaps and min(gaps) >= 12.2
    assert not page_gaps or min(page_gaps) >= 30
    summary = {"physical_requests":len(rows), "write_attempts":proof["persisted"],
               "min_completion_gap":min(gaps), "min_page_gap":min(page_gaps),
               "native_exit_code":child.returncode}
    print(json.dumps(summary), flush=True)
    if child.returncode:
        print(child.stderr.decode(errors="replace")[-3500:], file=sys.stderr)
        raise RuntimeError("Native collection did not pass")
    sys.path.insert(0, "/src/adapters/pl-frse")
    import adapter
    records = json.loads(child.stdout)
    adapter.validate_records(records)
    assert records
    now = adapter.utc_now()
    filtered = adapter.remove_expired_records(records, now)
    cases = {"raw":records,"filtered":filtered}
    retained = {r["id"] for r in filtered}
    expired = [r for r in records if r["id"] not in retained]
    if expired:
        cases["expired_subset"] = adapter.remove_expired_records(expired,now)
        assert not cases["expired_subset"]
    for name, values in cases.items():
        output = Path("/ram/consumer")/name/"pl-frse"
        output.mkdir(parents=True)
        (output/"data.json").write_text(json.dumps(values))
        (output/"metadata.json").write_text(json.dumps(adapter.metadata(now,{},values)))
    print("Native collection raw",len(records),"filtered",len(filtered),flush=True)
except BaseException:
    traceback.print_exc()
    sys.exit(1)
