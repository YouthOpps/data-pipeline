"""Observe the native child only. All publisher bodies stay in its private RAM."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse

if os.getenv("FRSE_NATIVE_OBSERVER") == "1":
    sys.path.insert(0, "/src/adapters/pl-frse")
    import adapter as a
    assert not os.getenv("DATA_SOURCE_TOKEN") and not os.getenv("GITHUB_ACTIONS")
    receipt = Path("/ram/proof.json")
    requests = []
    writes = []
    actual_open = a._OPENER.open
    actual_write = a.write_budget

    def observe_open(request, *args, **kwargs):
        if urllib.parse.urlsplit(request.full_url).hostname not in a._PUBLISHER_HOSTS:
            return actual_open(request, *args, **kwargs)
        item = {"url": request.full_url, "start": time.monotonic(), "parts": [],
                "headers": dict(request.header_items()), "inventory": bool(a._INVENTORY_ACTIVE)
                and urllib.parse.urlsplit(request.full_url).path != "/robots.txt"}
        requests.append(item)
        try:
            response = actual_open(request, *args, **kwargs)
        except urllib.error.HTTPError as error:
            attach(error, item)
            raise
        except BaseException as error:
            item["open_failure"] = type(error).__name__
            raise
        attach(response, item)
        return response

    def attach(response, item):
        item.update(status=response.status, response_headers=list(response.headers.items()))
        read, close = response.read, response.close
        def capture(*args, **kwargs):
            try:
                data = read(*args, **kwargs)
            except BaseException as error:
                partial = getattr(error, "partial", b"")
                if isinstance(partial, bytes):
                    item["parts"].append(partial)
                item["read_failure"] = type(error).__name__
                raise
            item["parts"].append(data)
            if not data or not args or args[0] < 0:
                item["eof"] = True
            return data
        def complete():
            try:
                result = close()
            except BaseException as error:
                item["close_failure"] = type(error).__name__
                raise
            item.setdefault("completion", time.monotonic())
            return result
        response.read, response.close = capture, complete

    def observe_write(state):
        event = {"requested": json.dumps(state, sort_keys=True)}
        writes.append(event)
        try:
            return actual_write(state)
        finally:
            if a._STATE_PATH and Path(a._STATE_PATH).is_file():
                event["persisted"] = Path(a._STATE_PATH).read_bytes()

    a._OPENER.open, a.write_budget = observe_open, observe_write

    @atexit.register
    def summarize():
        # Full authoritative bodies, headers and journal attempts stay in
        # process RAM until this final per-response and persistence audit.
        rows = []
        for item in requests:
            body = b"".join(item["parts"])
            assert item["headers"]["User-agent"] == "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
            headers = {k.lower():v for k,v in item.get("response_headers",[])}
            length = headers.get("content-length")
            eof = bool(item.get("eof") or (length is not None and length.isdecimal()
                                          and len(body) == int(length)))
            rows.append({k:item.get(k) for k in ("url","start","completion","inventory","status",
                                                "read_failure","open_failure","close_failure")})
            rows[-1]["fully_read"] = eof
            rows[-1].update(body_bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        bad_writes = sum(event.get("persisted") is None or
                         json.loads(event["requested"]) != json.loads(event["persisted"])
                         for event in writes)
        receipt.write_text(json.dumps({"requests":rows,"persisted":len(writes),
                                       "invalid_write_count":bad_writes}))
