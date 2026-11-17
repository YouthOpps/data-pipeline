"""Observe the original VW transport and write acknowledgements in RAM."""
import base64
from hashlib import sha256
import json
import os
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(raw):
    return sha256(raw).hexdigest()


def encode(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    raise TypeError("Unsupported capture value")


class Observer:
    def __init__(self, adapter, witness):
        self.adapter = adapter
        self.witness = witness
        self.journals = []
        self.wires = []
        self.finished = False
        self.finished_generation = None
        self.parent_fd = None
        self.parent_identity = None
        self.last_ack = None
        self.context = None
        self.original_open = adapter._OPENER.open
        self.original_commit = adapter.journal_commit
        self.original_finish = adapter.journal_finish
        self.publisher_hosts = {
            urlsplit(item["url"]).hostname for item in adapter.INPUTS.values()
        }
        adapter._OPENER = self
        adapter.journal_commit = self.commit
        adapter.journal_finish = self.finish

    def commit(self, state):
        adapter = self.adapter
        row = {"acknowledged": False, "started_monotonic": time.monotonic(),
               "intended": json.loads(json.dumps(state))}
        self.journals.append(row)
        try:
            self.original_commit(state)
            version, raw = adapter.journal_snapshot()
            require(version == adapter._GUARD_VERSION and json.loads(raw) == state,
                    "Accepted bytes differ from the original write")
            row.update(acknowledged=True, generation=version, raw=raw,
                       sha256=digest(raw), ack_monotonic=time.monotonic(),
                       parent_identity=adapter._GUARD_PARENT_ID,
                       collection_identity=adapter._GUARD_COLLECTION_ID)
            self.last_ack = row
            if self.parent_fd is None:
                parent = os.dup(adapter.journal_parent())
                info = os.fstat(parent)
                self.parent_fd = parent
                self.parent_identity = (info.st_dev, info.st_ino)
            require(self.parent_identity == row["parent_identity"],
                    "Original acknowledged state parent changed")
        except BaseException as error:
            row["error"] = type(error).__name__
            adapter.journal_failed()
            raise

    def open(self, request, *, timeout):
        adapter = self.adapter
        url = request.full_url
        if urlsplit(url).hostname not in self.publisher_hosts:
            return self.original_open(request, timeout=timeout)
        self.witness()
        require(request.get_method() == "GET" and request.data is None
                and request.get_header("User-agent") == adapter._USER_AGENT
                and request.get_header("Accept-encoding") == "identity",
                "Unexpected original publisher transport")
        adapter._journal_preopener_base()
        generation, raw = adapter.journal_snapshot()
        ack = self.last_ack
        state = json.loads(raw)
        adapter.protocol_current_owner(state)
        require(ack is not None and ack["generation"] == generation and ack["raw"] == raw
                and not state["blocked"] and state["starts"]
                and ack["intended"]["observed_at"] == state["starts"][-1],
                "Publisher opener lacks its preceding durable reservation")
        if self.wires:
            require(self.wires[-1]["reservation"] < state["starts"][-1],
                    "Detected repeated publisher reservation")
        if self.context is None:
            self.context = adapter._PROTOCOL_CONTEXT
        event = {
            "url": url, "method": request.get_method(),
            "request_headers": request.header_items(), "owner": state["flight_id"],
            "reservation": state["starts"][-1], "reservation_ack_sha256": ack["sha256"],
            "reservation_generation": generation, "segments": [], "headers": [],
            "headers_complete": False, "read_finished": False, "closed": False,
            "status": None,
        }
        self.wires.append(event)
        event["started_wall"] = time.time()
        event["started_monotonic"] = time.monotonic()
        try:
            response = self.original_open(request, timeout=timeout)
        except HTTPError as error:
            response = error
        except BaseException as error:
            event["open_error"] = type(error).__name__
            raise
        event["status"] = response.status
        read, close = response.read, response.close
        try:
            for name, value in response.headers.raw_items():
                event["headers"].append((name, value))
            event["headers_complete"] = True
        except BaseException as error:
            # Preserve the original 429/access handling before rejecting the body.
            event["header_error"] = type(error).__name__

        def observed_read(*args, **kwargs):
            require(event["headers_complete"], "Publisher headers were partially captured")
            try:
                part = read(*args, **kwargs)
            except BaseException as error:
                partial = getattr(error, "partial", None)
                if isinstance(partial, bytes):
                    event["segments"].append(partial)
                event["read_error"] = type(error).__name__
                raise
            event["segments"].append(part)
            if part == b"":
                event["read_finished"] = True
            return part

        def observed_close():
            try:
                close()
            except BaseException as error:
                event["close_error"] = type(error).__name__
                raise
            if not event["closed"]:
                event["closed"] = True
                event["completed_wall"] = time.time()
                event["completed_monotonic"] = time.monotonic()

        response.read, response.close = observed_read, observed_close
        return response

    def finish(self):
        try:
            self.original_finish()
            if not self.adapter._GUARD_FATAL:
                self.finished_generation = self.verify_released()
                self.finished = self.finished_generation is not None
        except BaseException:
            self.adapter.journal_failed()
            raise

    def verify_released(self):
        if self.parent_fd is None or self.last_ack is None:
            return None
        adapter = self.adapter
        path = adapter._STATE_PATH
        expected = self.last_ack
        parent = os.stat(os.path.dirname(path), follow_symlinks=False)
        held = os.fstat(self.parent_fd)
        require((parent.st_dev, parent.st_ino) == (held.st_dev, held.st_ino)
                == self.parent_identity == expected["parent_identity"],
                "Original journal parent changed after release")
        name = os.path.basename(path)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.parent_fd)
        try:
            before = adapter.journal_generation(os.fstat(fd))
            raw = os.read(fd, 16385)
            after = adapter.journal_generation(os.fstat(fd))
            current = adapter.journal_generation(os.stat(name, dir_fd=self.parent_fd, follow_symlinks=False))
            again = os.stat(os.path.dirname(path), follow_symlinks=False)
            require(before == after == current == expected["generation"] and raw == expected["raw"]
                    and (again.st_dev, again.st_ino) == self.parent_identity,
                    "Last acknowledgement and released original bytes differ")
            collection = os.stat("collection.lock", dir_fd=self.parent_fd, follow_symlinks=False)
            require((collection.st_dev, collection.st_ino) == expected["collection_identity"]
                    and collection.st_nlink == 1 and collection.st_uid == os.getuid(),
                    "Original collection custody changed after release")
            return current
        finally:
            os.close(fd)

    def close(self):
        if self.parent_fd is not None:
            os.close(self.parent_fd)
            self.parent_fd = None

    def derive_wires(self):
        """Match each physical open to its independently retained settlement."""
        rows = []
        for position, event in enumerate(self.wires):
            next_start = (self.wires[position + 1]["started_monotonic"]
                          if position + 1 < len(self.wires) else float("inf"))
            require(event["headers_complete"] and event["read_finished"] and event["closed"]
                    and not any(event.get(k) for k in ("open_error", "header_error", "read_error", "close_error"))
                    and event["reservation"] <= event["started_wall"] <= event["completed_wall"],
                    "Original publisher receipt is incomplete")
            prior = [
                ack for ack in self.journals if ack["acknowledged"]
                and ack["ack_monotonic"] <= event["started_monotonic"]
            ]
            require(prior and prior[-1]["sha256"] == event["reservation_ack_sha256"]
                    and prior[-1]["generation"] == event["reservation_generation"]
                    and prior[-1]["intended"]["starts"][-1] == event["reservation"],
                    "Exact reservation acknowledgement disappeared")
            settled = [
                ack for ack in self.journals if ack["acknowledged"]
                and event["completed_monotonic"] <= ack["ack_monotonic"] < next_start
                and ack["intended"]["in_flight"]
                and ack["intended"]["flight_id"] == event["owner"]
                and ack["intended"]["completed_at"] >= event["completed_wall"]
            ]
            require(settled, "No exact physical completion acknowledgement")
            body = b"".join(event["segments"])
            rows.append({
                "url_sha256": digest(event["url"].encode()), "status": event["status"],
                "bytes": len(body), "body_sha256": digest(body),
                "reservation": event["reservation"], "actual_start": event["started_wall"],
                "actual_completion": event["completed_wall"],
                "reservation_ack_sha256": event["reservation_ack_sha256"],
                "completion_ack_sha256": settled[0]["sha256"],
            })
        return rows
