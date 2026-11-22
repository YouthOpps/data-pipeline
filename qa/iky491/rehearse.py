"""Check the original IKY owner decoder and handback without a publisher request."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "adapters/gr-iky/adapter.py"
OWNER = (44966003124, 1, "gr-iky")
ARTIFACT = 14732190521
DIGEST = "sha256:a4efc4eeeef9b4f04351f0cb3f35c288a1b194b147f269767b5202c9bcfc34dd"
WORKFLOW = 385743125


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load():
    spec = importlib.util.spec_from_file_location("iky_rehearsal", SOURCE)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    return adapter


def fold(adapter, previous, terminal):
    """Fold authenticated receipts; callers must validate exact run and terminal ACK."""
    for state in (previous, terminal):
        adapter.validate_budget(state)
        require(adapter.scoped_refusal_valid(state), "unrecognized refusal")
    require(terminal["observed_at"] >= previous["observed_at"], "older terminal")
    require(not previous.get("request_pending") and not terminal.get("request_pending"),
            "unresolved reservation")
    combined = adapter.empty_budget(terminal["observed_at"])
    adapter.merge_refusal_scope(combined, previous, terminal)
    for field in ("not_before", "interval", "page_interval", "rate_limit_strikes",
                  "rate_limit_until", "completed"):
        combined[field] = max(combined[field], previous[field], terminal[field])
    # The oldest entries may be pruned only when their policy minute has passed.
    starts = sorted(set(previous["starts"] + terminal["starts"]))
    require(len(starts) <= 10 or starts[-11] <= terminal["observed_at"] - 60,
            "live starts exceed state capacity")
    combined["starts"] = starts[-10:]
    return adapter.validate_budget(combined)


def fixture_decoder(adapter, raw):
    class Redirect(io.BytesIO):
        status = 302
        headers = {"Location": "https://fixture.invalid/original.zip"}
    def open_archive(request, **kwargs):
        require(request.full_url == adapter._WORKFLOW_GITHUB +
                f"/actions/artifacts/{ARTIFACT}/zip", "unexpected archive request")
        return Redirect()
    def read_archive(url, **kwargs):
        require(url == "https://fixture.invalid/original.zip" and kwargs == {"byte_limit": 32768},
                "wrong fixture URL")
        return 200, {}, raw
    with patch.object(adapter._OPENER, "open", open_archive), patch.object(adapter, "request_bytes", read_archive):
        return adapter.download_inert_artifact(OWNER, ARTIFACT, DIGEST)


def hosted_owner(adapter):
    prod = adapter.workflow_api(f"/actions/workflows/{WORKFLOW}")
    require(prod["path"] == ".github/workflows/fetch-gr-iky.yml", "wrong source")
    metadata = adapter.workflow_api(f"/actions/artifacts/{ARTIFACT}")
    require(metadata["workflow_run"]["id"] == OWNER[0] and metadata["digest"] == DIGEST
            and metadata["size_in_bytes"] == 1565 and not metadata["expired"], "changed issuer")
    return adapter.download_inert_artifact(OWNER, ARTIFACT, DIGEST)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    adapter = load()
    original_open = adapter._OPENER.open
    def github_only(req, *args, **kwargs):
        url = urlsplit(req.full_url)
        host = url.hostname or ""
        require(url.scheme == "https" and (host == "api.github.com" or
                (host.startswith("productionresultssa") and host.endswith(".blob.core.windows.net"))),
                "network outside GitHub")
        if host != "api.github.com":
            require(not req.has_header("Authorization"), "forwarded bearer")
        return original_open(req, *args, **kwargs)
    with patch.object(adapter._OPENER, "open", github_only):
        state = fixture_decoder(adapter, args.archive.read_bytes()) if args.archive else hosted_owner(adapter)
    merged = fold(adapter, state, state)
    require(merged["refusal_scope"] == state["refusal_scope"]
            and merged["starts"] == state["starts"], "source scope changed")
    for field in ("blocked", "html_global_hold"):
        require(merged[field] == state[field], "hold changed")
    stronger = dict(state, html_global_hold=True, rate_limit_until=state["observed_at"] + 7200)
    preserved = fold(adapter, stronger, state)
    require(preserved["html_global_hold"] and preserved["rate_limit_until"] == stronger["rate_limit_until"],
            "stronger prior floor lost")
    try:
        fold(adapter, state, dict(state, request_pending=True))
    except ValueError as error:
        require(str(error) == "unresolved reservation", "unexpected rejection")
    else:
        raise ValueError("accepted unresolved request")
    print(json.dumps({"issuer": OWNER, "archive": ARTIFACT, "original_starts": len(state["starts"]),
                      "original_decoder_verified": True, "api_refusal_preserved": True,
                      "pending_rejected": True, "stronger_floor_preserved": True, "publisher_requests": 0}))


if __name__ == "__main__":
    main()
