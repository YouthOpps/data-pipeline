"""Verify one baseline regression and the candidate's received-response controls."""
import itertools
import json
from pathlib import Path
import subprocess
import tempfile

source = "adapters/dk-fulbright-center/adapter.py"
test = "qa-343-denmark/repro.py"
with tempfile.TemporaryDirectory() as folder:
    baseline = Path(folder, "baseline.py")
    baseline.write_bytes(subprocess.check_output(["git", "show", "286f436823a6e4d262469a533e2e22b6b429c1db:" + source]))
    result = json.loads(subprocess.check_output(["python", "-B", test, str(baseline), "close:200"]))
    assert result["result"] == "complete" and result["events"].count("open") == 2
cases = list(itertools.product(("close", "healthy", "enter", "read", "both"), (200, 302, 503, 429)))
cases.append(("prewire", 200))
for mode, status in cases:
    result = json.loads(subprocess.check_output(["python", "-B", test, source, f"{mode}:{status}"]))
    print(result)
    if mode in ("close", "enter", "read", "both"):
        assert result["events"].count("open") == 1 and result["gate_failed"]
        assert result["outcome"] != "response_assessed"
    elif mode == "healthy":
        assert not result["gate_failed"]
        assert result["result"] == ("complete" if status in (200, 302) else "PolicyStop")
    else:
        assert result["result"] == "complete" and result["events"].count("open") == 2
print("Verified original regression and 21 candidate controls. Publisher requests: 0")
