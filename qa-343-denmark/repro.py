"""Exercise actual DK source transport, retry caller and frames with inert storage and wire."""
import contextlib
import email.message
import importlib.util
import json
import sys
import socket
from unittest.mock import patch

def deny_wire(*_args, **_kwargs):
    raise AssertionError("This check permits no socket connection")
socket.socket.connect = deny_wire
socket.socket.connect_ex = deny_wire

spec = importlib.util.spec_from_file_location("dk", sys.argv[1])
dk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dk)
fault = sys.argv[2] if len(sys.argv) > 2 else "close"
fault, _, code = fault.partition(":")
code = int(code or "200")
events = []
class Response:
    status = code
    headers = email.message.Message()
    def __enter__(self):
        if fault == "enter":
            raise ConnectionResetError("inert enter failure")
        return self
    def close(self):
        events.append("release")
    def read(self, _):
        events.append("read")
        if fault in ("read", "both"):
            raise ConnectionResetError("inert read failure")
        return b"complete"
    def __exit__(self, *_):
        events.append("close")
        if fault in ("close","both") and events.count("open") == 1:
            raise ConnectionResetError("inert post-read close")
class Opener:
    def open(self,*a,**kw):
        events.append("open")
        if fault == "prewire" and events.count("open") == 1:
            raise ConnectionResetError("inert pre-response")
        response = Response()
        if code == 302:
            response.headers = email.message.Message()
            response.headers["Location"] = dk.SOURCE_URL
            if events.count("open") > 1:
                response.status = 200
        return response
with contextlib.ExitStack() as stack:
    state = dict(interval=12.2, not_before=0, request_id=None, in_flight=True, flight_id=None, completed=0)
    defaults = dict(_OPENER=Opener(), _PHASE_NAME="collection", _STATE_TRUSTED=True, _LOCAL_FIRST_WIRE_VERIFIED=True, _STARTUP_QUIET_UNTIL=0, _ROBOTS={"https://fulbrightcenter.dk":None}, locked_budget=contextlib.nullcontext, operation_guard=contextlib.nullcontext, load_budget=lambda:state, check_robots=lambda *_:12.2, request_timeout=lambda *_:1)
    for name in ("pace","save_budget","verify_journal","verify_collection","check_deadline","publisher_healthy","apply_robots_policy","record_retry_after","reject_visible_challenge","begin_transaction","invalidate_budget"):
        defaults[name] = lambda *a,**kw:None
    for key,val in defaults.items():
        stack.enter_context(patch.object(dk,key,val))
    try:
        result=dk.fetch_source(dk.SOURCE_URL,inventory=False)
    except BaseException as exc:
        result=type(exc).__name__
print(json.dumps({"status":code,"mode":fault,"events":events,"result":result,"gate_failed":dk._PUBLISHER_GATE_FAILED,"outcome":state.get("request_outcome")}))
