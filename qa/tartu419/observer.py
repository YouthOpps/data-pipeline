"""Private in-memory witness for the original Tartu test on GitHub Actions."""
import base64
import copy
import hashlib
import json
import os
import stat
import time
import urllib.error
from urllib.parse import urlsplit

RAM = {"capture": [], "persistence": [], "pages": []}


def pin_inputs():
    raise RuntimeError("Reviewed hosted QA witness is not installed")


def pair_headers(headers):
    return (
        list(headers.raw_items())
        if hasattr(headers, "raw_items")
        else list(headers.items())
    )


class ResponseWitness:
    def __init__(self, wrapped, row):
        self.wrapped, self.row = wrapped, row
        row["status"] = wrapped.status
        row["headers"] = pair_headers(wrapped.headers)

    def __getattr__(self, key):
        return getattr(self.wrapped, key)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def read(self, count=-1):
        segment = {"wall": time.time(), "monotonic": time.monotonic(), "limit": count}
        self.row["segments"].append(segment)
        try:
            body = self.wrapped.read(count)
            segment["base64"] = base64.b64encode(body).decode()
            segment["bytes"] = len(body)
            return body
        except BaseException as error:
            partial = getattr(error, "partial", b"")
            if isinstance(partial, bytes):
                segment["partial_base64"] = base64.b64encode(partial).decode()
            segment["error"] = type(error).__name__ + ": " + str(error)
            raise

    def close(self):
        self.row["close_started"] = {"wall": time.time(), "monotonic": time.monotonic()}
        try:
            self.wrapped.close()
            self.row["closed"] = True
        except BaseException as error:
            self.row["close_error"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            self.row["completed"] = {"wall": time.time(), "monotonic": time.monotonic()}


def journal_authority(adapter, info):
    parent = os.fstat(adapter.state_parent())
    return {
        "path": adapter._STATE_PATH,
        "generation": list(adapter.journal_version(info)),
        "parent": [parent.st_dev, parent.st_ino, parent.st_uid,
                   stat.S_IMODE(parent.st_mode)],
    }


def install_witness(adapter):
    real_open = adapter._OPENER.open

    def opening(request, *args, **kwargs):
        pins = pin_inputs()
        row = {
            "url": request.full_url,
            "method": request.get_method(),
            "request_headers": [
                (k, "<redacted>" if k.lower() == "authorization" else v)
                for k, v in request.header_items()
            ],
            "wall": time.time(),
            "monotonic": time.monotonic(),
            "pins": pins,
            "segments": [],
            "phase": adapter._PHASE_NAME,
            "inventory_page": adapter._INVENTORY_ACTIVE and urlsplit(request.full_url).path != "/robots.txt",
            "journal_owner": None,
        }
        if adapter._JOURNAL_LOCKS:
            descriptor, _ = adapter.retained_journal()
            raw = os.pread(descriptor, 16385, 0)
            adapter.retained_journal()
            state = json.loads(raw)
            row["journal_owner"] = {
                k: copy.deepcopy(state[k]) for k in ("physical", "policy", "logical")
            }
            row["journal_sha256"] = hashlib.sha256(raw).hexdigest()
        RAM["capture"].append(row)
        try:
            response = real_open(request, *args, **kwargs)
            row["boundary"] = "returned response"
        except urllib.error.HTTPError as error:
            row["boundary"] = "raised HTTPError"
            # Preserve the actual exception branch, while witnessing reads/close.
            wrapped = ResponseWitness(error, row)
            error.read = wrapped.read
            # Retain bound original methods on a distinct proxy to avoid recursion.
            wrapped.wrapped = type(
                "ErrorBody",
                (),
                {
                    "read": staticmethod(error.fp.read),
                    "close": staticmethod(error.fp.close),
                },
            )()
            error.close = wrapped.close
            raise
        except BaseException as error:
            row["open_error"] = type(error).__name__ + ": " + str(error)
            row["completed"] = {"wall": time.time(), "monotonic": time.monotonic()}
            raise
        return ResponseWitness(response, row)

    adapter._OPENER.open = opening
    real_write = adapter.write_budget_body

    def writing(state):
        row = {
            "wall": time.time(),
            "monotonic": time.monotonic(),
            "state_before_call": copy.deepcopy(state),
            "acknowledged": False,
        }
        RAM["persistence"].append(row)
        descriptor = None
        try:
            descriptor, _ = adapter.retained_journal()
            row["before_base64"] = base64.b64encode(
                os.pread(descriptor, 16385, 0)
            ).decode()
            result = real_write(state)
            current, before = adapter.retained_journal()
            if current != descriptor:
                raise RuntimeError("Acknowledged journal descriptor changed")
            raw = os.pread(descriptor, 16385, 0)
            current, after = adapter.retained_journal()
            if (
                current != descriptor
                or adapter.journal_version(before) != adapter.journal_version(after)
                or not 0 < len(raw) == before.st_size <= 16384
                or json.loads(raw) != row["state_before_call"]
            ):
                raise RuntimeError("Acknowledged journal generation changed")
            row["authority"] = journal_authority(adapter, after)
            row["after_base64"] = base64.b64encode(raw).decode()
            row["after_sha256"] = hashlib.sha256(raw).hexdigest()
            row["acknowledged"] = True
            return result
        except BaseException as error:
            row["error"] = type(error).__name__ + ": " + str(error)
            if descriptor is not None:
                try:
                    row["partial_base64"] = base64.b64encode(
                        os.pread(descriptor, 16385, 0)
                    ).decode()
                except BaseException as read_error:
                    row["partial_read_error"] = str(read_error)
            raise
        finally:
            row["completed"] = {"wall": time.time(), "monotonic": time.monotonic()}

    adapter.write_budget_body = writing
    real_fetch = adapter.fetch_source

    def fetching(url, *args, **kwargs):
        row = {"url": url, "wall": time.time(), "monotonic": time.monotonic()}
        RAM["pages"].append(row)
        try:
            text = real_fetch(url, *args, **kwargs)
            row["complete_text"] = text
            row["complete"] = True
            return text
        except BaseException as error:
            row["error"] = type(error).__name__ + ": " + str(error)
            raise
        finally:
            row["completed"] = {"wall": time.time(), "monotonic": time.monotonic()}

    adapter.fetch_source = fetching
