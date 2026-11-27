import importlib.util
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("adapter", Path("adapters/lu-luxembourg-cooperation/adapter.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

for response, expected in [
    ((200, {}, b'{"items": []}'), None),
    ((200, {}, b"secret invalid JSON"), "invalid JSON"),
    ((503, {}, b"private"), "HTTP 503"),
    ((429, {}, b"private"), "HTTP 429"),
    ((403, {}, b"private"), "HTTP 403"),
    ((None, {}, b"private"), "invalid status"),
    (OSError("private"), "request failed"),
]:
    with patch.object(mod, "http_bytes", side_effect=response if isinstance(response, Exception) else None, return_value=response) as read:
        try:
            result = mod.workflow_api("/actions/runs?token=private")
        except mod.AdapterError as exc:
            assert expected and str(exc) == "Workflow pacing state unavailable: " + expected, str(exc)
        else:
            assert expected is None and result == {"items": []}
        assert read.call_count == 1
print("Seven resimulation checks passed; no external connections.")
