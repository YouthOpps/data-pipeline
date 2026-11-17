"""Observe original Google407 transport and acknowledged journal in memory."""

import base64
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode_bytes(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


class Observer:
    def __init__(self, adapter, witness):
        self.adapter = adapter
        self.witness = witness
        self.wires = []
        self.journals = []
        self.pages = {}
        self.archive = None
        self.latest = None
        self.released = False
        self.parent = None
        self.path = None
        self.publisher_urls = set(adapter.INPUTS) | {url + "/robots.txt" for url in adapter.ROBOTS_ORIGINS}
        self.publisher_hosts = {urlsplit(url).hostname for url in self.publisher_urls}
        self.original_open = adapter._OPENER.open
        self.original_save = adapter.save_budget
        self.original_release = adapter.journal_release
        self.original_fetch = adapter.fetch_public
        self.original_download = adapter.download_inert_artifact
        adapter._OPENER.open = self.open
        adapter.save_budget = self.save
        adapter.journal_release = self.release
        adapter.fetch_public = self.fetch
        adapter.download_inert_artifact = self.download

    def open(self, request, *args, **kwargs):
        url = request.full_url
        if url not in self.publisher_urls:
            if urlsplit(url).hostname in self.publisher_hosts:
                raise ValueError("Unexpected original publisher route")
            self.adapter.infrastructure_url(url)
            return self.original_open(request, *args, **kwargs)
        self.witness()
        a = self.adapter
        headers = list(request.header_items())
        if (request.get_method() != "GET"
                or [v for k, v in headers if k.lower() == "user-agent"] != [a._USER_AGENT]
                or any(k.lower() in ("authorization", "cookie", "proxy-authorization") for k, _ in headers)):
            raise ValueError("Unexpected original publisher headers")
        a.journal_verify_collection()
        with a.locked_budget():
            state = json.loads(a.journal_read())
        ack = self.latest
        ident = a._ACTIVE_REQUEST_ID
        if (not ack or ack["state"] != state or not ident or state["request_id"] != ident
                or state["request_outcome"] != "reserved" or not state["in_flight"] or state["blocked"]):
            raise ValueError("Actual opener lacks its original ACKed reservation")
        event = {
            "url": url, "request_id": ident, "request_headers": headers,
            "reservation_sha256": ack["sha256"], "reservation": state["starts"][-1],
            "inventory": a._REQUEST_FRAMES[-1]["inventory"], "quiet_until": a._STARTUP_QUIET_UNTIL,
            "response_headers": [], "segments": [], "closed": False,
        }
        self.wires.append(event)
        event["started_wall"] = time.time()
        event["started"] = time.monotonic()
        try:
            response = self.original_open(request, *args, **kwargs)
        except HTTPError as error:
            self.attach(error, event)
            raise
        except BaseException as error:
            event["open_error"] = type(error).__name__
            event["completed"] = time.monotonic()
            event["completed_wall"] = time.time()
            raise
        self.attach(response, event)
        return response

    def attach(self, response, event):
        read, close = response.read, response.close
        try:
            event["status"] = response.status
            event["response_headers"] = list(response.headers.raw_items())
            event["headers_complete"] = True
        except BaseException as error:
            event["header_error"] = type(error).__name__

        def observed_read(*args, **kwargs):
            try:
                segment = read(*args, **kwargs)
            except BaseException as error:
                partial = getattr(error, "partial", b"")
                if isinstance(partial, bytes) and partial:
                    event["segments"].append(partial)
                event["read_error"] = type(error).__name__
                raise
            event["segments"].append(segment)
            return segment

        def observed_close(*args, **kwargs):
            try:
                result = close(*args, **kwargs)
            except BaseException as error:
                event["close_error"] = type(error).__name__
                raise
            if not event["closed"]:
                event.update(closed=True, completed=time.monotonic(), completed_wall=time.time())
            return result

        response.read, response.close = observed_read, observed_close

    def save(self, state):
        a = self.adapter
        event = {"intended": json.loads(a.canonical_state(state)), "started": time.monotonic(), "ack": False}
        self.journals.append(event)
        try:
            self.original_save(state)
            raw = a.journal_read()
            info = os.fstat(a._JOURNAL_FD)
            generation = a.journal_version(info)
            if json.loads(raw) != state or generation != a.journal_version(os.fstat(a._JOURNAL_FD)):
                raise ValueError("Acknowledged Google journal differs")
            a.journal_verify()
            if self.parent is None:
                self.parent = os.dup(a._STATE_DIR_FD)
                self.path = Path(a._STATE_PATH)
            event.update(ack=True, completed=time.monotonic(), generation=generation,
                         raw=raw, sha256=sha(raw), state=json.loads(raw))
            self.latest = event
        except BaseException as error:
            event["error"] = type(error).__name__
            if a._JOURNAL_FD is not None:
                try:
                    event["observed_bytes_after_error"] = os.pread(a._JOURNAL_FD, 16385, 0)
                except OSError:
                    pass
            event["failed_ram_state"] = a._JOURNAL_FAILED_STATE
            raise

    def verify_current_state(self):
        """Establish current state from the original generation, never from raw ACK alone."""
        if self.parent is None or self.latest is None:
            return False
        a = self.adapter
        identity = lambda info: (info.st_dev, info.st_ino)
        original = identity(os.fstat(self.parent))
        path = self.path
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            if identity(path.parent.lstat()) != original:
                return False
            fd = os.open(path.name, flags, dir_fd=self.parent)
            try:
                raw = os.pread(fd, 16385, 0)
                return (a.journal_version(os.fstat(fd)) == self.latest["generation"]
                        == a.journal_version(os.stat(path.name, dir_fd=self.parent, follow_symlinks=False))
                        and identity(path.parent.lstat()) == original and raw == self.latest["raw"])
            finally:
                os.close(fd)
        except OSError:
            return False

    def release(self):
        self.original_release()
        self.released = self.verify_current_state()

    def close(self):
        if self.parent is not None:
            os.close(self.parent)
            self.parent = None

    def fetch(self, url):
        status, headers, body = self.original_fetch(url)
        self.pages[url] = {"status": status, "headers": list(headers.items()), "body": body,
                           "request_id": self.adapter._LAST_RESPONSE_RECEIPT}
        return status, headers, body

    def download(self, identity, artifact_id, digest=None):
        state = self.original_download(identity, artifact_id, digest)
        self.archive = {"owner": identity, "id": artifact_id, "zip_digest": digest,
                        "decoded_state_sha256": sha(self.adapter.canonical_state(state))}
        return state

    def certify(self):
        """Prove each exact physical opening, close and settled original receipt."""
        a = self.adapter
        if (not self.wires or len(self.wires) != a._REQUEST_STARTS or not self.archive
                or not self.released or not self.verify_current_state()
                or a._JOURNAL_FATAL or not all(row["ack"] for row in self.journals)):
            raise ValueError("Incomplete original transport or journal custody")
        summaries = []
        for index, wire in enumerate(self.wires):
            no_response = "open_error" in wire and not wire["segments"] and not wire.get("headers_complete")
            if (wire["started"] < wire["quiet_until"]
                    or not wire["reservation"] <= wire["started_wall"] <= wire["completed_wall"]
                    or not wire["started"] <= wire["completed"]):
                raise ValueError("Incomplete original physical transport")
            if not no_response and (not wire.get("headers_complete") or not wire.get("closed")
                    or any(wire.get(k) for k in ("open_error", "header_error", "read_error", "close_error"))
                    or len(wire["segments"]) != 1 or type(wire.get("status")) is not int):
                raise ValueError("Incomplete original physical response")
            prior = [row for row in self.journals if row["completed"] <= wire["started"]]
            next_open = self.wires[index + 1]["started"] if index + 1 < len(self.wires) else float("inf")
            outcome = "no_response" if no_response else "response_assessed"
            completed = [row for row in self.journals if wire["completed"] <= row["completed"] <= next_open
                         and row["state"]["completed_request_id"] == wire["request_id"]
                         and row["state"]["request_outcome"] == outcome]
            if not prior or prior[-1]["sha256"] != wire["reservation_sha256"] or not completed:
                raise ValueError("Original reservation or original response settlement is missing")
            body = b"".join(wire["segments"])
            lengths = [value for key, value in wire["response_headers"] if key.lower() == "content-length"]
            if lengths and (len(lengths) != 1 or not lengths[0].isdigit() or int(lengths[0]) != len(body)):
                raise ValueError("Original physical response framing differs")
            summaries.append({"status": wire.get("status"), "outcome": outcome, "url": wire["url"], "body_sha256": sha(body),
                              "bytes": len(body), "request_id": wire["request_id"],
                              "reservation": wire["reservation"], "actual_start": wire["started_wall"],
                              "actual_completion": wire["completed_wall"],
                              "completion_sha256": completed[0]["sha256"]})
        physical = [b["started"] - a0["completed"] for a0, b in zip(self.wires, self.wires[1:])]
        inventory = [wire for wire in self.wires if wire["inventory"]]
        pages = [b["started"] - a0["completed"] for a0, b in zip(inventory, inventory[1:])]
        rolling = max(sum(w["started"] - 60 < p["started"] <= w["started"] for p in self.wires) for w in self.wires)
        if any(gap < 12.2 for gap in physical) or any(gap < 30 for gap in pages) or rolling > 5:
            raise ValueError("Original publisher pacing differs")
        state = self.latest["state"]
        if state["blocked"] or state["in_flight"] or not set(a._GOOGLE407_NATIVE3_FLOOR["starts"]).issubset(state["starts"]):
            raise ValueError("Original terminal state is unresolved or weaker")
        return {"wires": summaries, "minimum_gap": min(physical, default=None),
                "minimum_inventory_gap": min(pages, default=None), "rolling_max": rolling}
