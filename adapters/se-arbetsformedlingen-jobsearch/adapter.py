"""Swedish public-agency trainee and apprenticeship advertisements under CC0."""

import argparse
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import email.utils
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
import zipfile

SOURCE_ID = "se-arbetsformedlingen-jobsearch"
SOURCE_URL = 'https://jobsearch.api.jobtechdev.se/'
WEBSITE_URL = 'https://data.arbetsformedlingen.se/dataservice/jobsearch/'
HOST = 'jobsearch.api.jobtechdev.se'
FAMILY = 'youthopps-se-arbetsformedlingen-jobsearch-v1'
USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
ATTRIBUTION = 'Arbetsformedlingen JobSearch advertisements (CC0); minimal factual index'
REPOSITORY = "YouthOpps/data-pipeline"
WORKFLOW_PATH = ".github/workflows/fetch-se-arbetsformedlingen-jobsearch.yml"
ARTIFACT_NAME = "se-arbetsformedlingen-jobsearch-pacing-state"
DATA_REPOSITORY = "YouthOpps/data-source"
RUN_SECONDS = 3000
COLLECTION_SECONDS = 2700
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 4 * 1024 * 1024
BOOTSTRAP_EVIDENCE = 'https://github.com/YouthOpps/data-pipeline/issues/381#issuecomment-6941733678'
RESEARCH_JOURNAL_SHA256 = 'f89e243727263a8e96f12a597dd2376fd816799cfd1c0e9e2fae266b624f9f42'
RESEARCH_PROVENANCE = 'First independently reviewed public-agency research; all seven known reservations and unknown earlier history retained'
WORKFLOW_SHA256 = '61f0491412d297f99e0cbf7c7023606ba43f2792c89220f7c41151f5d36543c7'
NATIVE_PACING_EVIDENCE = "https://github.com/YouthOpps/data-pipeline/issues/381#issuecomment-6944243876"
NATIVE_PACING_SHA256 = "52e2a042ad46b70ecd6d1d212faf0856f0327c9511fb8bc8bde42835aa56f015"
BOOTSTRAP_STATE = {
    'blocked': False,
    'completed': 1794907047.90674,
    'family': 'youthopps-se-arbetsformedlingen-jobsearch-v1',
    'generation': 66,
    'interval': 12.2,
    'inventory_completed': 1794907047.90674,
    'not_before': 0,
    'page_interval': 30,
    'pending': None,
    'policies': {
        'jobsearch.api.jobtechdev.se': [{
            'rules':
                '{\n'
                '  "tracking_id": '
                '"e0c79bc0-e1ee-11f1-9d20-0a58ac1c1019",\n'
                '  "cause": {\n'
                '    "code": "404",\n'
                '    "message": '
                '"resource_not_found"\n'
                '  }\n'
                '}',
            'sha256':
                '7381c47ef82b8a9556deede107a40acc09ca12a8cfc94eac50542a0d4fdd32fb',
            'status':
                404
        }, {
            'rules':
                '{\n'
                '  "tracking_id": '
                '"ab1c3bf0-e263-11f1-99e6-0a58ac1c0c2d",\n'
                '  "cause": {\n'
                '    "code": "404",\n'
                '    "message": '
                '"resource_not_found"\n'
                '  }\n'
                '}',
            'sha256':
                '1daac8e5605a1c79fab88628e4e39958cf40136b6061583d49af07a12a3ee4a3',
            'status':
                404
        }, {
            'rules':
                '{\n'
                '  "tracking_id": '
                '"7509a030-e267-11f1-9d20-0a58ac1c1019",\n'
                '  "cause": {\n'
                '    "code": "404",\n'
                '    "message": '
                '"resource_not_found"\n'
                '  }\n'
                '}',
            'sha256':
                '5d428d359dce2f5f2410bcd5b5d2c2a76374b8217bef1a85244bdfc2dc18fdf9',
            'status':
                404
        }]
    },
    'schema': 1,
    'starts': [
        1794855077.2574055, 1794855149.6222196, 1794855193.5768247,
        1794856078.124242, 1794856979.3321123, 1794857012.3141708,
        1794857042.968066, 1794905310.8847861, 1794905324.8902187,
        1794905356.6334755, 1794905387.2339604, 1794905418.1445515,
        1794906937.882692, 1794906952.1987157, 1794906984.1454394,
        1794907015.719848, 1794907046.9787118
    ],
    'strikes': 0,
    'unknown_history': True
}

_JOURNAL = None
_END = None
_COLLECTION_END = None
_QUIET_UNTIL = None
_LAST_COMPLETION = None
_LAST_INVENTORY_COMPLETION = None
_FIRST_WIRE = False
_HISTORY = None
_CURRENT_IDENTITY = None
_PUBLISHING = False
_FAILED = False
_DEADLINE_INTERRUPTED = False
_ALARM_HANDLER = None
_ROBOTS = {}
_EXPORT_PIN = None


class AdapterError(Exception):

    def __init__(self, message, stage="parse"):
        super().__init__(message)
        self.stage = stage


class DeadlineExceeded(BaseException):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def utc_now():
    return datetime.now(
        timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_error(error):
    text = re.sub(r"https?://[^\s]+", "[source URL]", str(error))
    text = re.sub(r"(?i)(bearer|token|password|secret)\s*[:= ]\s*\S+",
                  r"\1 [redacted]", text)
    return " ".join(text.split())[:400]


def check_time():
    limit = min(v for v in (_END, _COLLECTION_END) if v is not None)
    if time.monotonic() >= limit:
        raise DeadlineExceeded("Nonrenewable runtime deadline reached")
    return limit - time.monotonic()


def arm_deadline():
    """Arm the earliest fixed phase/run bound, without renewing either."""
    if _DEADLINE_INTERRUPTED:
        signal.setitimer(signal.ITIMER_REAL, 0)
        return
    if _ALARM_HANDLER is not None:
        remaining = min(v for v in (_END, _COLLECTION_END)
                        if v is not None) - time.monotonic()
        signal.setitimer(signal.ITIMER_REAL, max(0.000001, remaining))


@contextlib.contextmanager
def hard_io_deadline():
    """Bound blocking opener/read/closure and its cancellation unwinding."""
    global _FAILED, _DEADLINE_INTERRUPTED
    if threading.current_thread() is not threading.main_thread():
        raise AdapterError("Absolute I/O deadline requires the main thread",
                           "access")
    previous = signal.getsignal(signal.SIGALRM)
    prior_timer = signal.getitimer(signal.ITIMER_REAL)
    if prior_timer[1]:
        raise AdapterError("Periodic process alarm refused", "access")
    interrupted = cancellation_started = False

    def expired(_number, _frame):
        nonlocal interrupted, cancellation_started
        global _FAILED, _DEADLINE_INTERRUPTED
        interrupted = _FAILED = _DEADLINE_INTERRUPTED = True
        if cancellation_started:
            signal.setitimer(signal.ITIMER_REAL, 0)
            raise DeadlineExceeded("Deadline cancellation grace exhausted")
        cancellation_started = True
        signal.setitimer(signal.ITIMER_REAL, 0.25)
        raise DeadlineExceeded(
            "Absolute phase/run deadline interrupted blocking I/O")

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, check_time())
        yield
        if interrupted:
            raise DeadlineExceeded("Deadline interruption cannot be suppressed")
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if not interrupted and prior_timer[0] > 0:
            arm_deadline()


def numeric(value):
    return type(value) in (
        int, float) and math.isfinite(value) and 0 <= value <= 253402300799


def validate_state(value):
    keys = {
        "schema", "family", "starts", "completed", "inventory_completed",
        "interval", "page_interval", "not_before", "strikes", "blocked",
        "pending", "unknown_history", "policies", "generation"
    }
    if (not isinstance(value, dict) or set(value) != keys or
            type(value["schema"]) is not int or value["schema"] != 1 or
            value["family"] != FAMILY or value["unknown_history"] is not True or
            type(value["blocked"]) is not bool or
            type(value["strikes"]) is not int or
            not 0 <= value["strikes"] <= 100 or
            type(value["generation"]) is not int or value["generation"] < 0):
        raise AdapterError("Untrusted JobSearch state identity", "access")
    for key in ("completed", "inventory_completed", "interval", "page_interval",
                "not_before"):
        if not numeric(value[key]):
            raise AdapterError("Unsupported JobSearch state clock", "access")
    if (value["interval"] < 12.2 or value["page_interval"] < 30 or
            not isinstance(value["starts"], list) or
            any(not numeric(v) for v in value["starts"]) or
            value["starts"] != sorted(set(value["starts"]))):
        raise AdapterError("Invalid retained JobSearch pacing", "access")
    pending = value["pending"]
    if pending is not None and (
            not isinstance(pending, dict) or
            set(pending) != {"id", "url", "inventory", "started"} or
            not re.fullmatch(r"[0-9a-f]{32}", pending.get("id", "")) or
            type(pending.get("inventory")) is not bool or
            not numeric(pending.get("started")) or
            not isinstance(pending.get("url"), str)):
        raise AdapterError("Invalid pending JobSearch owner", "access")
    if not isinstance(value["policies"], dict):
        raise AdapterError("Invalid retained robots history", "access")
    for host, policies in value["policies"].items():
        if host not in {
                HOST, "data.arbetsformedlingen.se", "data.jobtechdev.se"
        } or not isinstance(policies, list):
            raise AdapterError("Untrusted robots origin", "access")
        for policy in policies:
            legacy = {"status", "sha256", "rules"}
            unavailable = legacy | {
                "target", "purpose", "disposition", "observed_at"
            }
            if (not isinstance(policy, dict) or
                    set(policy) not in (legacy, unavailable) or
                    type(policy["status"]) is not int or
                    not isinstance(policy["rules"], str) or
                    not re.fullmatch(r"[0-9a-f]{64}", policy["sha256"]) or
                    hashlib.sha256(policy["rules"].encode()).hexdigest()
                    != policy["sha256"]):
                raise AdapterError("Untrusted robots bytes", "access")
            if set(policy) == unavailable:
                if (not 400 <= policy["status"] < 500 or
                        policy["status"] == 429 or
                        policy["target"] != "https://" + host + "/robots.txt" or
                        policy["purpose"] != "robots" or
                        policy["disposition"] != "unavailable" or
                        not numeric(policy["observed_at"]) or
                        visible_refusal(policy["rules"], human_only=True)):
                    raise AdapterError("Untrusted unavailable-REP observation",
                                       "access")
    return value


def merge_state(left, right):
    left, right = validate_state(left), validate_state(right)
    result = copy.deepcopy(left)
    for key in ("completed", "inventory_completed", "interval", "page_interval",
                "not_before", "strikes", "generation"):
        result[key] = max(left[key], right[key])
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["blocked"] = left["blocked"] or right["blocked"]
    pending = [v["pending"] for v in (left, right) if v["pending"] is not None]
    if pending and any(p != pending[0] for p in pending):
        raise AdapterError("Conflicting pending JobSearch owners", "access")
    result["pending"] = copy.deepcopy(pending[0]) if pending else None
    for host, policies in right["policies"].items():
        target = result["policies"].setdefault(host, [])
        for policy in policies:
            if policy not in target:
                target.append(copy.deepcopy(policy))
    return validate_state(result)


class Journal:
    """One pinned private inode, held exclusively through the whole collection."""

    def __init__(self, incoming):
        self.fd = self.parent = None
        try:
            self.initialize(incoming)
        except BaseException:
            self.close()
            raise

    def initialize(self, incoming):
        self.fatal = False
        self.fd = self.parent = None
        self.ack = None
        self.path = os.path.join(tempfile.gettempdir(),
                                 FAMILY + "-" + str(os.getuid()))
        new_directory = False
        try:
            os.mkdir(self.path, 0o700)
            new_directory = True
        except FileExistsError:
            pass
        self.parent = os.open(self.path,
                              os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(self.parent)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise AdapterError("Unsafe JobSearch state directory", "access")
        self.parent_id = (info.st_dev, info.st_ino)
        try:
            os.stat("failed", dir_fd=self.parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdapterError(
                "JobSearch journal has a retained failure marker", "access")
        created = False
        try:
            self.fd = os.open("state.json",
                              os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=self.parent)
        except FileNotFoundError:
            if not new_directory:
                raise AdapterError("Mature JobSearch journal is missing",
                                   "access")
            self.fd = os.open("state.json",
                              os.O_RDWR | os.O_CREAT | os.O_EXCL |
                              os.O_NOFOLLOW,
                              0o600,
                              dir_fd=self.parent)
            created = True
        self.identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.verify(False)
        raw = self.read()
        self.ack = raw
        if created:
            if raw:
                raise AdapterError("New journal was unexpectedly populated",
                                   "access")
            self.state = copy.deepcopy(validate_state(incoming))
        else:
            try:
                local = validate_state(json.loads(raw))
            except (ValueError, TypeError):
                raise AdapterError("Mature JobSearch journal is unavailable",
                                   "access") from None
            self.state = merge_state(local, incoming)
        self.save()
        os.fsync(self.parent)

    def read(self):
        parts, offset = [], 0
        while offset <= MAX_STATE_BYTES:
            part = os.pread(self.fd, min(65536, MAX_STATE_BYTES + 1 - offset),
                            offset)
            if not part:
                break
            parts.append(part)
            offset += len(part)
        if offset > MAX_STATE_BYTES:
            raise AdapterError("JobSearch state exceeds safe byte bound",
                               "access")
        return b"".join(parts)

    def verify(self, check_bytes=True):
        parent = os.fstat(self.parent)
        linked_parent = os.stat(self.path, follow_symlinks=False)
        info = os.fstat(self.fd)
        linked = os.stat("state.json",
                         dir_fd=self.parent,
                         follow_symlinks=False)
        if ((parent.st_dev, parent.st_ino) != self.parent_id or
            (linked_parent.st_dev, linked_parent.st_ino) != self.parent_id or
                parent.st_uid != os.getuid() or
                stat.S_IMODE(parent.st_mode) != 0o700 or
            (info.st_dev, info.st_ino) != self.identity or
            (linked.st_dev, linked.st_ino) != self.identity or
                not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
            raise AdapterError("JobSearch journal identity changed", "access")
        probe = os.open("state.json",
                        os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                        dir_fd=self.parent)
        try:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
                raise AdapterError("JobSearch journal ownership lock was lost",
                                   "access")
        finally:
            os.close(probe)
        if check_bytes and self.ack is not None and self.read() != self.ack:
            raise AdapterError("JobSearch journal generation changed", "access")

    def save(self):
        try:
            if self.fatal:
                raise AdapterError("JobSearch journal failure is sticky",
                                   "access")
            validate_state(self.state)
            self.verify()
            self.state["generation"] += 1
            raw = json.dumps(self.state, sort_keys=True,
                             separators=(",", ":")).encode()
            if len(raw) > MAX_STATE_BYTES:
                raise AdapterError("JobSearch state capacity exceeded",
                                   "access")
            os.ftruncate(self.fd, 0)
            self.ack = b""
            self.verify()
            while len(self.ack) < len(raw):
                offset = len(self.ack)
                count = os.pwrite(self.fd, raw[offset:], offset)
                if count <= 0:
                    raise OSError("Short JobSearch state write")
                self.ack += raw[offset:offset + count]
                self.verify()
            os.fsync(self.fd)
            self.verify()
        except BaseException:
            self.fatal = True
            # The original pinned inode is invalidated before any fallible
            # marker or workflow-output operation. Even a fully written but
            # unacknowledged settlement cannot become a fresh-process budget.
            try:
                info = os.fstat(self.fd)
                if ((info.st_dev, info.st_ino) == self.identity and
                        info.st_uid == os.getuid() and info.st_nlink == 1 and
                        stat.S_ISREG(info.st_mode) and
                        stat.S_IMODE(info.st_mode) == 0o600):
                    os.ftruncate(self.fd, 0)
                    os.fsync(self.fd)
            except BaseException:
                pass
            try:
                marker = os.open("failed",
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                 os.O_NOFOLLOW,
                                 0o600,
                                 dir_fd=self.parent)
                try:
                    os.write(marker,
                             b"Unacknowledged JobSearch journal mutation\n")
                    os.fsync(marker)
                finally:
                    os.close(marker)
                os.fsync(self.parent)
            except BaseException:
                pass
            invalidate_export()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.parent is not None:
            os.close(self.parent)
            self.parent = None


def output_ready(ready):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid(
            ) or info.st_nlink != 1:
                raise AdapterError("Unsafe workflow output", "access")
            os.write(descriptor,
                     ("pacing_ready=" + str(ready).lower() + "\n").encode())
        finally:
            os.close(descriptor)


def artifact_identity(parent, descriptor, linked=True):
    path = os.environ.get("JOBTECH_PACING_ARTIFACT_PATH", "")
    parent_info, info = os.fstat(parent), os.fstat(descriptor)
    if (not stat.S_ISDIR(parent_info.st_mode) or
            parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022 or
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise AdapterError("Unsafe pinned JobSearch artifact", "access")
    if linked:
        root = os.stat(os.path.dirname(path), follow_symlinks=False)
        entry = os.stat(os.path.basename(path),
                        dir_fd=parent,
                        follow_symlinks=False)
        if ((root.st_dev, root.st_ino)
                != (parent_info.st_dev, parent_info.st_ino) or
            (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)):
            raise AdapterError("JobSearch artifact identity changed", "access")


def export_target(create=False):
    global _EXPORT_PIN
    path = os.environ.get("JOBTECH_PACING_ARTIFACT_PATH")
    if not path:
        return None
    if _EXPORT_PIN is not None:
        artifact_identity(*_EXPORT_PIN)
        return tuple(os.dup(fd) for fd in _EXPORT_PIN)
    root = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(root) or os.path.dirname(
            os.path.abspath(path)) != os.path.realpath(root):
        raise AdapterError("Unsafe JobSearch artifact directory", "access")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            descriptor = os.open(os.path.basename(path), flags, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                os.close(parent)
                return None
            descriptor = os.open(os.path.basename(path),
                                 flags | os.O_CREAT | os.O_EXCL,
                                 0o600,
                                 dir_fd=parent)
        artifact_identity(parent, descriptor)
        _EXPORT_PIN = (parent, descriptor)
        return tuple(os.dup(fd) for fd in _EXPORT_PIN)
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)
        raise


def invalidate_export():
    """Invalidate original owned bytes before fallible workflow signaling.

    A replaced path is never opened as a substitute for our already pinned inode.
    """
    target = tuple(
        os.dup(fd) for fd in _EXPORT_PIN) if _EXPORT_PIN else export_target()
    if target:
        parent, descriptor = target
        try:
            artifact_identity(parent, descriptor, linked=False)
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            os.close(parent)
    output_ready(False)


def infrastructure(url,
                   method="GET",
                   payload=None,
                   token=None,
                   limit=MAX_STATE_BYTES):
    """Bounded infrastructure transport; authentication never follows redirects."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json"
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url,
                                     data=body,
                                     headers=headers,
                                     method=method)
    try:
        response = _OPENER.open(request, timeout=min(30, check_time()))
    except urllib.error.HTTPError as error:
        response = error
    with response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise AdapterError("Infrastructure response exceeds byte bound",
                               "access")
        return response.status, dict(response.headers.items()), data


def github(path,
           repository=REPOSITORY,
           method="GET",
           payload=None,
           token=None,
           missing=False):
    status, _, body = infrastructure(
        "https://api.github.com/repos/" + repository + path,
        method,
        payload,
        token if token is not None else os.environ.get("GITHUB_TOKEN"),
    )
    if missing and status == 404:
        return None
    if status not in {200, 201}:
        raise AdapterError("GitHub infrastructure returned HTTP " + str(status),
                           "access")
    try:
        return json.loads(body)
    except ValueError:
        raise AdapterError("Invalid GitHub JSON", "access") from None


def run_identity(run, definition):
    if (not isinstance(run, dict) or
            run.get("workflow_id") != definition["id"] or
            run.get("path") != WORKFLOW_PATH or
            run.get("head_branch") != "main" or
            run.get("repository", {}).get("full_name") != REPOSITORY or
            run.get("head_repository", {}).get("full_name") != REPOSITORY or
            type(run.get("id")) is not int or run["id"] <= 0 or
            type(run.get("run_attempt")) is not int or
            run["run_attempt"] <= 0 or
            not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", ""))):
        raise AdapterError("Untrusted JobSearch workflow identity", "access")
    return run["id"], run["run_attempt"], run["head_sha"]


def parse_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset(
    ) is None or not 0 <= parsed.timestamp() <= time.time() + 1:
        raise ValueError("Invalid timestamp")
    return parsed.timestamp()


def job_identity(run, definition, current=False):
    identity = run_identity(run, definition)
    result = github(
        f"/actions/runs/{identity[0]}/attempts/{identity[1]}/jobs?per_page=100")
    jobs = result.get("jobs")
    if type(result.get("total_count")
           ) is not int or result["total_count"] != 1 or not isinstance(
               jobs, list) or len(jobs) != 1:
        raise AdapterError("Incomplete JobSearch job inventory", "access")
    job = jobs[0]
    if (type(job.get("id")) is not int or
            job["id"] <= 0 or (job.get("run_id"), job.get("run_attempt"),
                               job.get("head_sha")) != identity or
            job.get("head_branch") != "main" or job.get("name") != "collect" or
            job.get("status") != ("in_progress" if current else "completed")):
        raise AdapterError("Untrusted JobSearch source job", "access")
    start = parse_timestamp(job["started_at"])
    end = None if current else parse_timestamp(job["completed_at"])
    if end is not None and end < start:
        raise AdapterError("Invalid job chronology", "access")
    return {
        "identity": identity,
        "start": start,
        "end": end,
        "job": job["id"],
        "conclusion": job.get("conclusion")
    }


def history():
    definition = github(
        "/actions/workflows/fetch-se-arbetsformedlingen-jobsearch.yml",
        missing=True)
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if definition is None:
        if hosted or github("/contents/" + WORKFLOW_PATH + "?ref=main",
                            missing=True) is not None:
            raise AdapterError("JobSearch workflow definition is unavailable",
                               "access")
        return None, [], None
    if (type(definition.get("id")) is not int or definition["id"] <= 0 or
            definition.get("path") != WORKFLOW_PATH or definition.get("state")
            != ("active" if hosted else "disabled_manually")):
        raise AdapterError("JobSearch workflow is not serialized for this mode",
                           "access")
    current = None
    if hosted:
        try:
            current = (int(os.environ["GITHUB_RUN_ID"]),
                       int(os.environ["GITHUB_RUN_ATTEMPT"]),
                       os.environ["GITHUB_SHA"])
        except (KeyError, ValueError):
            raise AdapterError("Missing hosted JobSearch identity",
                               "access") from None
        if (os.environ.get("GITHUB_JOB") != "collect" or
                os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or
                os.environ.get("GITHUB_REF") != "refs/heads/main" or
                os.environ.get("GITHUB_WORKFLOW_REF")
                != REPOSITORY + "/" + WORKFLOW_PATH + "@refs/heads/main"):
            raise AdapterError("Unexpected hosted JobSearch execution",
                               "access")
    rows, seen, total, found = [], set(), None, False
    page = 1
    while True:
        result = github(
            f"/actions/workflows/{definition['id']}/runs?per_page=100&page={page}"
        )
        runs = result.get("workflow_runs")
        count = result.get("total_count")
        if type(count) is not int or count < 0 or not isinstance(
                runs, list) or len(
                    runs) > 100 or total is not None and count != total:
            raise AdapterError("Unstable JobSearch workflow inventory",
                               "access")
        total = count
        for run in runs:
            identity = run_identity(run, definition)
            if identity[0] in seen:
                raise AdapterError("Duplicate JobSearch workflow run", "access")
            seen.add(identity[0])
            if identity == current:
                if run.get("status") != "in_progress":
                    raise AdapterError(
                        "Current JobSearch source is not running", "access")
                job_identity(run, definition, True)
                found = True
                last = identity[1] - 1
            else:
                if run.get("status") != "completed":
                    raise AdapterError("Another JobSearch attempt is active",
                                       "access")
                last = identity[1]
            for attempt in range(1, last + 1):
                exact = github(
                    f"/actions/runs/{identity[0]}/attempts/{attempt}")
                if run_identity(exact, definition) != (
                        identity[0], attempt,
                        identity[2]) or exact.get("status") != "completed":
                    raise AdapterError("Untrusted exact JobSearch attempt",
                                       "access")
                rows.append(job_identity(exact, definition))
        if len(seen) == total:
            break
        if len(seen) > total or len(runs) != 100:
            raise AdapterError("Incomplete JobSearch workflow history",
                               "access")
        page += 1
        check_time()
    if hosted and not found:
        raise AdapterError("Current JobSearch run is absent from history",
                           "access")
    rows.sort(key=lambda row: row["end"], reverse=True)
    if any(a["end"] == b["end"] for a, b in zip(rows, rows[1:])):
        raise AdapterError("Ambiguous JobSearch job completion chronology",
                           "access")
    return definition["id"], rows, current


def no_publisher_attempt(row):
    """Prove a same-code failure could not pass the monotonic startup floor."""
    if type(row["identity"][1]) is not int or row["identity"][1] != 1 or row[
            "conclusion"] != "failure" or not 0 <= row["end"] - row[
                "start"] < 58:
        return False
    head = row["identity"][2]
    for path, expected in (
        ("adapters/se-arbetsformedlingen-jobsearch/adapter.py",
         hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),
        (WORKFLOW_PATH, WORKFLOW_SHA256),
    ):
        item = github("/contents/" + path + "?ref=" + head)
        if item.get("encoding") != "base64" or not isinstance(
                item.get("content"), str):
            return False
        actual = hashlib.sha256(base64.b64decode(item["content"])).hexdigest()
        if actual != expected:
            return False
    return True


def download_state(row, artifact, workflow_id):
    identity = row["identity"]
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or
            artifact.get("expired") is not False or
            artifact.get("workflow_run", {}).get("id") != identity[0] or
            artifact.get("workflow_run", {}).get("head_sha") != identity[2] or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest",
                                                                  ""))):
        raise AdapterError("Untrusted owned JobSearch artifact", "access")
    status, headers, _ = infrastructure(
        f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip",
        token=os.environ.get("GITHUB_TOKEN"),
        limit=4096,
    )
    location = next((v for k, v in headers.items() if k.lower() == "location"),
                    "")
    parsed = urllib.parse.urlsplit(location)
    if (status != 302 or parsed.scheme != "https" or not parsed.hostname or
            parsed.username or parsed.password or parsed.port or
            not parsed.hostname.endswith(
                (".blob.core.windows.net", ".actions.githubusercontent.com"))):
        raise AdapterError("Unsafe JobSearch artifact redirect", "access")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise AdapterError("Literal artifact host refused", "access")
    status, _, raw = infrastructure(location, limit=MAX_STATE_BYTES)
    if status != 200 or hashlib.sha256(
            raw).hexdigest() != artifact["digest"][7:]:
        raise AdapterError("JobSearch artifact digest mismatch", "access")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries
              ) != 1 or entries[0].filename != "pacing-state.json" or entries[
                  0].file_size > MAX_STATE_BYTES or entries[
                      0].flag_bits & 1 or stat.S_ISLNK(
                          entries[0].external_attr >> 16):
            raise AdapterError("Unsafe JobSearch artifact archive", "access")
        envelope = json.loads(archive.read(entries[0]))
    expected = {
        "schema", "repository", "source", "family", "workflow_id", "run_id",
        "run_attempt", "head_sha", "state"
    }
    if (not isinstance(envelope, dict) or set(envelope) != expected or
            type(envelope["schema"]) is not int or envelope["schema"] != 1 or
            any(
                type(envelope[k]) is not int
                for k in ("workflow_id", "run_id", "run_attempt")) or
            envelope["repository"] != REPOSITORY or
            envelope["source"] != SOURCE_ID or envelope["family"] != FAMILY or
            envelope["workflow_id"] != workflow_id or
        (envelope["run_id"], envelope["run_attempt"],
         envelope["head_sha"]) != identity):
        raise AdapterError("JobSearch artifact ownership mismatch", "access")
    return validate_state(envelope["state"])


def restored_state():
    global _HISTORY, _CURRENT_IDENTITY
    _HISTORY = history()
    workflow_id, rows, _CURRENT_IDENTITY = _HISTORY
    boundary = 0
    for row in rows:
        run_id, attempt, _ = row["identity"]
        result = github(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if type(result.get("total_count")) is not int or not isinstance(
                artifacts, list) or result["total_count"] != len(artifacts):
            raise AdapterError("Incomplete JobSearch artifact inventory",
                               "access")
        matches = [
            a for a in artifacts
            if a.get("name") == ARTIFACT_NAME + "-" + str(attempt)
        ]
        if len(matches) == 1:
            incoming = download_state(row, matches[0], workflow_id)
            break
        if artifacts or not no_publisher_attempt(row):
            raise AdapterError(
                "Newest JobSearch state missing without a no-access proof",
                "access")
        boundary = max(boundary, row["end"])
    else:
        if github("/contents/datas/se-arbetsformedlingen-jobsearch?ref=main",
                  repository=DATA_REPOSITORY,
                  missing=True) is not None:
            raise AdapterError(
                "Published JobSearch source has lost its owned history",
                "access")
        incoming = copy.deepcopy(validate_state(BOOTSTRAP_STATE))
    incoming = copy.deepcopy(incoming)
    if boundary:
        incoming["not_before"] = max(
            incoming["not_before"],
            boundary + max(incoming["interval"], incoming["page_interval"]))
    return validate_state(incoming)


def export_state():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    invalidate_export()
    if _JOURNAL is None or _JOURNAL.fatal or _FAILED or _CURRENT_IDENTITY is None:
        return
    _JOURNAL.verify()
    identity = _CURRENT_IDENTITY
    envelope = {
        "schema": 1,
        "repository": REPOSITORY,
        "source": SOURCE_ID,
        "family": FAMILY,
        "workflow_id": _HISTORY[0],
        "run_id": identity[0],
        "run_attempt": identity[1],
        "head_sha": identity[2],
        "state": validate_state(_JOURNAL.state)
    }
    raw = json.dumps(envelope, sort_keys=True).encode()
    parent, descriptor = export_target(True)
    try:
        os.ftruncate(descriptor, 0)
        offset = 0
        while offset < len(raw):
            artifact_identity(parent, descriptor)
            count = os.pwrite(descriptor, raw[offset:], offset)
            if count <= 0:
                raise OSError("Short artifact write")
            offset += count
        os.fsync(descriptor)
        os.fsync(parent)
        artifact_identity(parent, descriptor)
        _JOURNAL.verify()
        if os.pread(descriptor, len(raw) + 1, 0) != raw:
            raise OSError("JobSearch artifact readback mismatch")
    except BaseException:
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        raise
    finally:
        os.close(descriptor)
        os.close(parent)
    try:
        output_ready(True)
    except BaseException:
        invalidate_export()
        raise


@contextlib.contextmanager
def execution():
    global _JOURNAL, _END, _QUIET_UNTIL, _FIRST_WIRE, _COLLECTION_END, _EXPORT_PIN
    global _FAILED, _DEADLINE_INTERRUPTED, _ALARM_HANDLER
    if _END is not None or _FAILED:
        raise AdapterError("Nested or failed JobSearch execution refused",
                           "access")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _END = time.monotonic() + RUN_SECONDS
    _FIRST_WIRE = False
    _ROBOTS.clear()
    previous = signal.getsignal(signal.SIGALRM)

    cancellation_started = False

    def alarm(_signum, _frame):
        nonlocal cancellation_started
        global _FAILED, _DEADLINE_INTERRUPTED
        _FAILED = _DEADLINE_INTERRUPTED = True
        if cancellation_started:
            signal.setitimer(signal.ITIMER_REAL, 0)
            raise DeadlineExceeded("Runtime cancellation grace exhausted")
        cancellation_started = True
        signal.setitimer(signal.ITIMER_REAL, 0.25)
        raise DeadlineExceeded("Nonrenewable phase/run deadline expired")

    _ALARM_HANDLER = alarm
    signal.signal(signal.SIGALRM, alarm)
    arm_deadline()
    try:
        invalidate_export()
        incoming = restored_state()
        _JOURNAL = Journal(incoming)
        if _JOURNAL.state["pending"] is not None or _JOURNAL.state["blocked"]:
            raise AdapterError("JobSearch retains unresolved source access",
                               "access")
        _QUIET_UNTIL = time.monotonic() + 60
        yield
    finally:
        _COLLECTION_END = None
        if _DEADLINE_INTERRUPTED:
            signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            export_state()
        finally:
            if _JOURNAL is not None:
                _JOURNAL.close()
            _JOURNAL = None
            if _EXPORT_PIN is not None:
                for descriptor in _EXPORT_PIN:
                    os.close(descriptor)
                _EXPORT_PIN = None
            _END = _QUIET_UNTIL = _ALARM_HANDLER = None
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def publisher_url(url, robots=False):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc != HOST or parsed.username or
            parsed.password or parsed.fragment):
        raise AdapterError("Outside the reviewed public JobSearch API",
                           "access")
    if robots:
        if parsed.path != "/robots.txt" or parsed.query:
            raise AdapterError("Robots purpose is not a literal policy target",
                               "access")
        return url
    pairs = urllib.parse.parse_qsl(parsed.query,
                                   keep_blank_values=True,
                                   strict_parsing=True)
    values = dict(pairs)
    kinds = set(values) & {"trainee", "larling"}
    if (parsed.path != "/search" or len(pairs) != len(values) or
            len(kinds) != 1 or set(values)
            != kinds | {"country", "limit", "offset", "sort", "resdet"} or
            values[next(iter(kinds))] != "true" or
            values["country"] != SWEDEN_CONCEPT or values["limit"] != "100" or
            values["sort"] != "id" or values["resdet"] != "full" or
            not re.fullmatch(r"0|[1-9][0-9]*", values["offset"]) or
            not 0 <= int(values["offset"]) <= 2000 or
            int(values["offset"]) % 100):
        raise AdapterError(
            "Outside the complete Sweden trainee/apprentice queries", "access")
    return url


def pace(inventory):
    state = _JOURNAL.state
    if state["blocked"] or state[
            "pending"] is not None or _FAILED or _JOURNAL.fatal:
        raise AdapterError("Retained JobSearch access barrier", "access")
    while True:
        check_time()
        wall = time.time()
        target = max(state["not_before"],
                     state["completed"] + state["interval"])
        if len(state["starts"]) >= 5:
            target = max(target, state["starts"][-5] + 60.001)
        wait = max(0, target - wall, _QUIET_UNTIL - time.monotonic())
        if _LAST_COMPLETION is not None:
            wait = max(wait,
                       _LAST_COMPLETION + state["interval"] - time.monotonic())
        if inventory:
            wait = max(
                wait,
                state["inventory_completed"] + state["page_interval"] - wall)
            if _LAST_INVENTORY_COMPLETION is not None:
                wait = max(
                    wait, _LAST_INVENTORY_COMPLETION + state["page_interval"] -
                    time.monotonic())
        if wait <= 0:
            return
        if wait + 30 >= check_time():
            raise AdapterError(
                "Retained JobSearch delay exceeds collection budget", "access")
        time.sleep(min(wait, 1))


def retry_after(value, now):
    if not value:
        return 0
    try:
        seconds = float(value)
        return now + seconds if numeric(seconds) else 0
    except ValueError:
        try:
            result = email.utils.parsedate_to_datetime(value)
            return max(
                0, result.timestamp()) if result.utcoffset() is not None else 0
        except (ValueError, TypeError, OverflowError):
            return 0


class Node:

    def __init__(self, tag, attrs=None):
        self.tag, self.attrs, self.children = tag, attrs or {}, []

    def nodes(self, predicate):
        result = [self] if predicate(self) else []
        for child in self.children:
            if isinstance(child, Node):
                result.extend(child.nodes(predicate))
        return result

    def text(self):
        if self.tag in {
                "script", "style", "template", "noscript"
        } or self.attrs.get(
                "aria-hidden") == "true" or "display:none" in self.attrs.get(
                    "style", "").replace(" ", ""):
            return ""
        return " ".join(" ".join(c.text() if isinstance(c, Node) else c
                                 for c in self.children).split())


class Document(HTMLParser):
    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"
    }

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.stack = [self.root]
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag and tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def has_class(node, name):
    return name in node.attrs.get("class", "").split()


def visible_refusal(text, human_only=False):
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        errors = ("error", "errors", "message", "detail", "cause", "challenge")
        total, hits = payload.get("total"), payload.get("hits")
        if (not any(key in payload for key in errors) and
                isinstance(total, dict) and type(total.get("value")) is int and
                total["value"] >= 0 and isinstance(hits, list) and
                total["value"] >= len(hits) and all(
                    isinstance(hit, dict) and isinstance(hit.get("id"), str) and
                    isinstance(hit.get("headline"), str) for hit in hits)):
            return False
        primary = " ".join(
            str(payload[key]) for key in errors if key in payload)
        if primary:
            text = primary
    root = Document(text).root

    def strip(node):
        node.children = [
            child for child in node.children if not isinstance(child, Node) or
            child.tag not in {"nav", "header", "footer", "aside"}
        ]
        for child in node.children:
            if isinstance(child, Node):
                strip(child)

    strip(root)
    pattern = r"\b(verify (?:that )?you are (?:a )?human|human verification required|complete (?:the )?captcha|checking your browser|enable (?:javascript and )?cookies to continue)\b"
    if not human_only:
        pattern = r"\b(access denied|unusual traffic)\b|" + pattern
    return bool(re.search(pattern, root.text(), re.I))


def response_length(headers):
    lengths = headers.get_all("Content-Length", [])
    transfer = headers.get_all("Transfer-Encoding", [])
    if len(lengths) > 1 or len(transfer) > 1 or lengths and transfer:
        raise AdapterError("Duplicate or conflicting API response framing",
                           "fetch")
    if transfer and transfer[0].strip().lower() != "chunked":
        raise AdapterError("Unsupported response transfer encoding", "fetch")
    if lengths:
        if not re.fullmatch(r"[0-9]+", lengths[0]) or int(
                lengths[0]) > MAX_BODY_BYTES:
            raise AdapterError("Unsupported response Content-Length", "fetch")
        return int(lengths[0])
    return None


def valid_robots(text, headers):
    lines = [line.split("#", 1)[0].strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if ("html" in headers.get("Content-Type", "").lower() or any(
            "<" in line or ">" in line or ":" not in line for line in lines) or
            lines and not any(
                re.match(r"(?i)^(user-agent|sitemap)\s*:", line)
                for line in lines)):
        raise AdapterError("Robots200 is not a verified REP document", "access")


def retain_robot_observation(url, status, body, text, refused, observed_at):
    """Keep raw legacy evidence and a bounded fresh unavailable disposition."""
    publisher_url(url, robots=True)
    policy = {
        "status": status,
        "sha256": hashlib.sha256(body).hexdigest(),
        "rules": text
    }
    policies = _JOURNAL.state["policies"].setdefault(HOST, [])
    if 400 <= status < 500 and status != 429 and not refused:
        policy.update(target=url,
                      purpose="robots",
                      disposition="unavailable",
                      observed_at=observed_at)
        # Only this explicit new record form may replace its older same-status
        # unavailable observation. Legacy raw blobs, real rules and held bodies
        # are preserved; clock/MAX/pending/unknown state is not compacted.
        if not _JOURNAL.state["blocked"]:
            policies[:] = [
                old for old in policies
                if not (old.get("disposition") == "unavailable" and old.get(
                    "target") == url and old.get("purpose") == "robots" and
                        old["status"] == status and
                        not visible_refusal(old["rules"], human_only=True))
            ]
    if policy not in policies:
        policies.append(policy)
    # This is the ACTUAL current response, never a previous response's digest.
    _ROBOTS[HOST] = {"target": url, "purpose": "robots", "observation": policy}
    return policy


def robots_decision(url):
    policies = _JOURNAL.state["policies"].get(HOST, [])
    if not policies:
        raise AdapterError("JobSearch robots provenance missing", "access")
    for policy in policies:
        if policy["status"] != 200:
            continue
        parser = urllib.robotparser.RobotFileParser("https://" + HOST +
                                                    "/robots.txt")
        parser.parse(policy["rules"].splitlines())
        products = ["YouthOppsBot"]
        if any(agent.casefold() == "youthopps"
               for entry in parser.entries
               for agent in entry.useragents):
            products.append("YouthOpps")
        for product in products:
            if not parser.can_fetch(product, url):
                _JOURNAL.state["blocked"] = True
                _JOURNAL.save()
                raise AdapterError("Robots refuses the actual API target",
                                   "access")
            delay = parser.crawl_delay(product)
            rate = parser.request_rate(product)
            if delay is not None:
                _JOURNAL.state["interval"] = max(_JOURNAL.state["interval"],
                                                 delay)
            if rate and rate.requests:
                _JOURNAL.state["interval"] = max(_JOURNAL.state["interval"],
                                                 rate.seconds / rate.requests)


def fetch_source(url, inventory=False, robots=False):
    global _FAILED, _FIRST_WIRE, _LAST_COMPLETION, _LAST_INVENTORY_COMPLETION
    publisher_url(url, robots)
    if (_FAILED or _JOURNAL.fatal or _JOURNAL.state["pending"] is not None or
            _JOURNAL.state["blocked"]):
        raise AdapterError("Retained JobSearch access barrier", "access")
    if not robots and HOST not in _ROBOTS:
        fetch_source("https://" + HOST + "/robots.txt", robots=True)
    for redirect in range(4):
        publisher_url(url, robots)
        for attempt in range(3):
            if not robots:
                # A cached-policy decision is a durable transaction of its own;
                # it is not a physical request or a renewed rolling allowance.
                _JOURNAL.state["pending"] = {
                    "id": uuid.uuid4().hex,
                    "url": url,
                    "inventory": inventory,
                    "started": time.time()
                }
                _JOURNAL.save()
                robots_decision(url)
                _JOURNAL.save()
                _JOURNAL.state["pending"] = None
                _JOURNAL.save()
            pace(inventory)
            if not _FIRST_WIRE:
                if history() != _HISTORY:
                    raise AdapterError(
                        "JobSearch coordination changed before access",
                        "access")
                _FIRST_WIRE = True
            state = _JOURNAL.state
            state["pending"] = {
                "id": uuid.uuid4().hex,
                "url": url,
                "inventory": inventory,
                "started": time.time()
            }
            state["starts"].append(state["pending"]["started"])
            state["starts"] = sorted(set(state["starts"]))
            _JOURNAL.save()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "identity",
                    "X-Feature-Disable-Smart-Freetext": "true"
                })
            assert request.get_header("User-agent") == USER_AGENT
            assert request.get_header(
                "X-feature-disable-smart-freetext") == "true"
            response = None
            received = False
            assessed = False
            redirect_to = None
            try:
                with hard_io_deadline():
                    _JOURNAL.verify()
                    if time.monotonic() < _QUIET_UNTIL:
                        raise AdapterError(
                            "Monotonic JobSearch startup floor has not passed",
                            "access")
                    try:
                        response = _OPENER.open(request,
                                                timeout=min(30, check_time()))
                    except urllib.error.HTTPError as error:
                        response = error
                    received = True
                    with response:
                        status = response.status
                        now = time.time()
                        for value in response.headers.get_all(
                                "Retry-After", []):
                            state["not_before"] = max(state["not_before"],
                                                      retry_after(value, now))
                        if status == 429:
                            state["strikes"] += 1
                            state["interval"] *= 2
                            state["page_interval"] *= 2
                            state["not_before"] = max(
                                state["not_before"],
                                now + 1800 * 2**(state["strikes"] - 1))
                        _JOURNAL.save()
                        length = response_length(response.headers)
                        if response.headers.get(
                                "Content-Encoding",
                                "identity").lower() != "identity":
                            raise AdapterError(
                                "Unsupported compressed API response", "fetch")
                        body = response.read(MAX_BODY_BYTES + 1)
                        if len(body
                              ) > MAX_BODY_BYTES or length is not None and len(
                                  body) != length:
                            raise AdapterError(
                                "Incomplete or excessive API response body",
                                "fetch")
                        encoding = "utf-8" if robots else response.headers.get_content_charset(
                        ) or "utf-8"
                        text = body.decode(encoding)
                        refused = visible_refusal(text,
                                                  human_only=robots and
                                                  status != 200)
                        if refused or not robots and status in {401, 403}:
                            state["blocked"] = True
                        if robots and status == 200:
                            valid_robots(text, response.headers)
                            retain_robot_observation(url, status, body, text,
                                                     refused, now)
                            robots_decision(search_url(QUERY_KINDS[0], 0))
                        elif robots and 400 <= status < 500 and status != 429:
                            retain_robot_observation(url, status, body, text,
                                                     refused, now)
                        if status in {301, 302, 303, 307, 308}:
                            redirect_to = urllib.parse.urljoin(
                                url, response.headers.get("Location", ""))
                            publisher_url(redirect_to, robots)
                            before_target = urllib.parse.urlsplit(url)
                            after_target = urllib.parse.urlsplit(redirect_to)
                            if (before_target.path != after_target.path or
                                    sorted(
                                        urllib.parse.parse_qsl(
                                            before_target.query)) != sorted(
                                                urllib.parse.parse_qsl(
                                                    after_target.query))):
                                raise AdapterError(
                                    "Redirect changes the counted query semantics",
                                    "access")
                        _JOURNAL.save()
                        assessed = True
                completed = time.time()
                _LAST_COMPLETION = time.monotonic()
                state["completed"] = max(state["completed"], completed)
                if inventory:
                    state["inventory_completed"] = max(
                        state["inventory_completed"], completed)
                    _LAST_INVENTORY_COMPLETION = _LAST_COMPLETION
                state["pending"] = None
                _JOURNAL.save()
                if status == 429 or refused or state["blocked"]:
                    raise AdapterError(
                        "Retained JobSearch publisher refusal or cooldown",
                        "access")
                if redirect_to:
                    url = redirect_to
                    break
                if robots and 400 <= status < 500:
                    return ""
                if status != 200:
                    raise AdapterError("JobSearch returned HTTP " + str(status),
                                       "fetch")
                return text
            except (urllib.error.URLError, OSError, TimeoutError) as error:
                cause = getattr(error, "reason", error)
                ordinary = not received and not isinstance(
                    cause, ssl.SSLError) and not _JOURNAL.fatal
                if ordinary:
                    completed = time.time()
                    _LAST_COMPLETION = time.monotonic()
                    state["completed"] = max(state["completed"], completed)
                    if inventory:
                        state["inventory_completed"] = max(
                            state["inventory_completed"], completed)
                        _LAST_INVENTORY_COMPLETION = _LAST_COMPLETION
                    state["pending"] = None
                    _JOURNAL.save()
                    if attempt < 2:
                        continue
                    raise AdapterError(
                        "JobSearch connection failed without a response",
                        "fetch") from None
                _FAILED = True
                invalidate_export()
                raise AdapterError(
                    "Unassessed JobSearch transport failure retained",
                    "access") from None
            except BaseException:
                if state[
                        "pending"] is not None or _JOURNAL.fatal or received and not assessed:
                    _FAILED = True
                    invalidate_export()
                raise
        else:
            raise AdapterError("JobSearch retries exhausted", "fetch")
    raise AdapterError("JobSearch redirect bound exceeded", "fetch")


SWEDEN_CONCEPT = "i46j_HmG_v64"
QUERY_KINDS = ("trainee", "larling")
PAGE_SIZE = 100
_TEMPORAL_FIELDS = {
    "created_at", "first_seen_at", "updated_at", "last_seen_at",
    "last_checked_at"
}
_TRAINEE = re.compile(r"\btrainee[^\W\d_]*\b", re.I)
_APPRENTICE = re.compile(r"\b[^\W\d_]*lärling(?:ar)?\b|\bapprentice(?:ship)?\b",
                         re.I)
_STAFF = re.compile(
    r"\b(?:koordinator|samordnare|handledare|specialpedagog|lärare|utbildare|"
    r"instruktör|mentor|instructor|coordinator|trainer)\b", re.I)


def canonical_ad(url):
    """Bind the agency's public link to its own immutable advertisement ID."""
    if not isinstance(url, str):
        raise AdapterError("Agency advertisement link is not a string")
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(r"/platsbanken/annonser/([1-9][0-9]*)/?", parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "arbetsformedlingen.se" or
            parsed.username or parsed.password or parsed.query or
            parsed.fragment or not match):
        raise AdapterError("Outside the documented agency advertisement links")
    return match.group(
        1
    ), "https://arbetsformedlingen.se/platsbanken/annonser/" + match.group(1)


def search_url(kind, offset):
    if kind not in QUERY_KINDS or type(
            offset) is not int or not 0 <= offset <= 2000:
        raise AdapterError("Unsupported documented JobSearch page")
    return "https://" + HOST + "/search?" + urllib.parse.urlencode(
        [(kind, "true"), ("country", SWEDEN_CONCEPT), ("limit", str(PAGE_SIZE)),
         ("offset", str(offset)), ("sort", "id"), ("resdet", "full")])


def parse_page(text, offset, expected_total=None):
    try:
        page = json.loads(text)
    except (ValueError, TypeError) as error:
        raise AdapterError("JobSearch returned invalid JSON") from error
    total = page.get("total") if isinstance(page, dict) else None
    hits = page.get("hits") if isinstance(page, dict) else None
    if (not isinstance(total, dict) or type(total.get("value")) is not int or
            total["value"] < 0 or not isinstance(hits, list) or
            any(key in page
                for key in ("error", "errors", "message", "detail", "cause"))):
        raise AdapterError("JobSearch count/hit envelope changed")
    total = total["value"]
    if expected_total is not None and total != expected_total:
        raise AdapterError("JobSearch count changed within the complete scan")
    if total and (total - 1) // PAGE_SIZE * PAGE_SIZE > 2000:
        raise AdapterError(
            "Complete query exceeds the documented offset window; no truncation"
        )
    if len(hits) != min(PAGE_SIZE, max(0, total - offset)):
        raise AdapterError("Incomplete JobSearch inventory page")
    seen = set()
    for hit in hits:
        if (not isinstance(hit, dict) or not isinstance(hit.get("id"), str) or
                not re.fullmatch(r"[1-9][0-9]*", hit.get("id", "")) or
                hit["id"] in seen or not isinstance(hit.get("headline"), str) or
                not hit["headline"].strip() or
                not isinstance(hit.get("description"), dict) or
                not isinstance(hit["description"].get("text"), str) or
                not isinstance(hit.get("employer"), dict) or
                isinstance(hit.get("employer"), dict) and
                hit["employer"].get("name") is not None and
                not isinstance(hit["employer"]["name"], str) or
                hit.get("removed") is not None and
                type(hit["removed"]) is not bool or
                canonical_ad(hit.get("webpage_url"))[0] != hit["id"]):
            raise AdapterError(
                "Malformed or duplicate full JobSearch advertisement")
        seen.add(hit["id"])
    return total, hits


def advertisement_facts(ad):
    # Relevance belongs to a search, rather than to the advertisement itself.
    return {key: value for key, value in ad.items() if key != "relevance"}


def collect_inventory():
    candidates = {}
    for kind in QUERY_KINDS:
        offset, total, seen = 0, None, set()
        while total is None or offset < total:
            url = search_url(kind, offset)
            total, hits = parse_page(fetch_source(url, inventory=True), offset,
                                     total)
            for ad in hits:
                identity = ad["id"]
                if identity in seen:
                    raise AdapterError(
                        "JobSearch pages overlap; complete coverage unproven")
                seen.add(identity)
                facts = advertisement_facts(ad)
                if identity in candidates and candidates[identity][
                        "facts"] != facts:
                    raise AdapterError(
                        "Conflicting advertisement across the complete query union"
                    )
                item = candidates.setdefault(identity, {
                    "facts": facts,
                    "documents": set(),
                    "queries": set()
                })
                item["documents"].add(url)
                item["queries"].add(kind)
            offset += PAGE_SIZE
        if len(seen) != total:
            raise AdapterError(
                "JobSearch advertised total was not completely retrieved")
    return candidates


def role_decision(ad):
    """Recognize a present applicant route, not a global keyword or history."""
    title = " ".join(ad["headline"].split())
    text = " ".join(ad["description"]["text"].split())

    def decision(kind, reason, evidence=""):
        return {"kind": kind, "reason": reason, "evidence": evidence[:240]}

    support_compound = re.search(
        r"\b(?:trainee(?:program(?:met)?)?|lärlings?)(?:koordinator|samordnare|"
        r"handledare|utbildare|mentor|ansvarig)\b", title, re.I)
    programme_staff = re.search(
        r"\b(?:chef|ansvarig|manager|coordinator|handledare)\s+(?:för|for|of)\s+.*\btrainee"
        r"|\btrainee(?:program(?:met)?|[ -]+program(?:me)?)\s+(?:manager|coordinator|chef|ansvarig|handledare)\b",
        title, re.I)
    if _STAFF.search(title) or support_compound or programme_staff:
        return decision(None, "support-staff headline", title)

    roles = {
        "trainee":
            r"trainee[^\W\d_]*",
        "apprenticeship":
            r"(?:[^\W\d_]*lärling(?:ar|s(?:plats|anställning))?|apprentice(?:s|ships?)?)"
    }

    def negated(kind):
        pattern = (
            r"\b(?:ej|inte|inga|ingen|no|not)\s+"
            r"(?:(?:emot|några|någon|en|a|an|accepting|recruiting|taking)\s+){0,2}"
            + roles[kind] + r"\b")
        return re.search(pattern, title + " " + text, re.I)

    if not negated("trainee"):
        match = _TRAINEE.search(title)
        if match:
            return decision("trainee", "explicit trainee-role headline", title)
        trainee_frames = (
            r"\bas (?:a|an) trainee[^\W\d_]*\b[^.!?]{0,120}\byou (?:will|can|get|are|start)\b",
            r"\bsom trainee[^\W\d_]*\b[^.!?]{0,100}\b(?:kommer|får|erbjuds|ska) du\b",
            r"\b(?:tjänsten|anställningen|rollen)\b[^.!?]{0,60}\b(?:inleds|börjar|startar)\b[^.!?]{0,45}\bsom trainee[^\W\d_]*\b",
            r"\b(?:the role|the position|you)\b[^.!?]{0,60}\b(?:starts?|begins?)\b[^.!?]{0,40}\bas (?:a |an )?trainee\b",
        )
        for pattern in trainee_frames:
            match = re.search(pattern, text, re.I)
            if match:
                return decision("trainee",
                                "direct current applicant trainee route",
                                match.group())

    if not negated("apprenticeship"):
        match = _APPRENTICE.search(title)
        if match:
            return decision("apprenticeship",
                            "explicit apprentice-role headline", title)
        for match in re.finditer(
                r"\b(?:söker|anställer|rekryterar)\b[^.!?]{0,110}\blärling(?:ar)?\b",
                text, re.I):
            span = match.group()
            mentoring = re.search(
                r"\b(?:handleda|mentorera|supervise|mentor|teach)\b", span,
                re.I)
            if not _STAFF.search(span) and not mentoring and not re.search(
                    r"\b(?:tidigare|före detta)\b", span, re.I):
                return decision("apprenticeship",
                                "direct apprentice recruitment", span)
        apprentice_frames = (
            r"\b(?:du|eller)\s*:?\s*vill bli lärling\b",
            r"\bdu\b[^.!?]{0,70}\b(?:börjar|börja|arbeta|jobba|anställas)\b[^.!?]{0,40}\bsom [^\W\d_]*lärling\b",
            r"\b(?:det finns|vi erbjuder|du har) (?:en )?möjlighet\b[^.!?]{0,70}\b(?:ansöka|söka)\b[^.!?]{0,45}\bsom [^\W\d_]*lärling\b",
        )
        for pattern in apprentice_frames:
            match = re.search(pattern, text, re.I)
            if match:
                return decision("apprenticeship",
                                "direct current applicant apprentice route",
                                match.group())
        match = re.search(
            r"\b(?:om|när) du (?:söker|ansöker (?:om|till)) (?:en )?[^\W\d_]*lärlingsplats\b",
            text, re.I)
        if match:
            return decision("apprenticeship",
                            "conditional apprenticeship application route",
                            match.group())
    return decision(None,
                    "no explicit current trainee/apprentice applicant route")


def role_kind(ad):
    return role_decision(ad)["kind"]


def swedish_locations(ad):
    primary = ad.get("workplace_address")
    addresses = ad.get("workplace_addresses")
    if addresses is None:
        addresses = []
    if not isinstance(addresses, list) or any(
            not isinstance(x, dict) for x in addresses):
        raise AdapterError("Unsupported JobSearch workplace-address shape")
    if primary is not None:
        if not isinstance(primary, dict):
            raise AdapterError("Unsupported primary workplace address")
        addresses = addresses + [primary]
    locations = set()
    for address in addresses:
        concept = address.get("country_concept_id")
        label = address.get("country")
        code = address.get("country_code")
        if concept == SWEDEN_CONCEPT:
            if label not in ("Sverige", "Sweden") or code not in ("199", None):
                raise AdapterError("Conflicting explicit Sweden country facts")
            place = address.get("city") or address.get(
                "municipality") or "Sweden"
            if not isinstance(place, str) or not place.strip():
                raise AdapterError("Invalid Sweden workplace location")
            locations.add(" ".join(place.split()))
    return sorted(locations)


def publisher_date(value):
    """Preserve an explicit date when the API does not identify a timezone."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdapterError("Unsupported publisher date type")
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.fromisoformat(value).date().isoformat()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value):
            raise ValueError()
        if parsed.utcoffset() is None:
            return parsed.date().isoformat()
        return parsed.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z")
    except ValueError as error:
        raise AdapterError("Invalid JobSearch calendar date") from error


def deadline_time(value):
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        # Unknown-zone calendar dates remain open through the end of AoE.
        date = datetime.fromisoformat(value)
        return date.replace(hour=23,
                            minute=59,
                            second=59,
                            microsecond=999000,
                            tzinfo=timezone(timedelta(hours=-12)))
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise AdapterError("Deadline timestamp has no supported timezone")
    return parsed


def normalize_ad(item, checked):
    ad = item["facts"]
    kind = role_kind(ad)
    locations = swedish_locations(ad)
    if kind is None or not locations:
        return None
    native_id, url = canonical_ad(ad["webpage_url"])
    name = (ad.get("employer") or {}).get("name")
    employer = " ".join(name.split()) if name else None
    if len(locations) == 1:
        location = locations[0] if locations[
            0] == "Sweden" else locations[0] + ", Sweden"
    else:
        location = str(len(locations)) + " advertised Swedish locations"
    label = "Trainee opportunity" if kind == "trainee" else "Advertised apprenticeship route"
    provider = "advertised by " + employer if employer else "published through Arbetsformedlingen"
    summary = f"{label} {provider} in {location}."
    experience = ad.get("experience_required")
    if experience is not None and type(experience) is not bool:
        raise AdapterError("Unsupported experience-requirement field")
    summary += " See the official advertisement for education, experience and programme eligibility."
    if len(summary) > 600:
        raise AdapterError("Factual source summary exceeds the public contract")
    deadline = publisher_date(ad.get("application_deadline"))
    return {
        "id":
            SOURCE_ID + "-" + native_id,
        "source":
            SOURCE_ID,
        "title":
            " ".join(ad["headline"].split()),
        "url":
            url,
        "category":
            "training",
        "kind":
            kind,
        "summary":
            summary,
        "summary_language":
            "en",
        "employer":
            employer,
        "locations":
            locations,
        "host_countries": ["SE"],
        "eligible_countries": [],
        "deadline":
            deadline,
        "published_at":
            publisher_date(ad.get("publication_date")),
        "status":
            "expired" if ad.get("removed") is True or
            deadline is not None and deadline_time(deadline)
            < datetime.fromisoformat(checked.replace("Z", "+00:00")) else
            "open" if ad.get("removed") is False else "unknown",
        "created_at":
            checked,
        "updated_at":
            checked,
        "first_seen_at":
            checked,
        "last_seen_at":
            checked,
        "last_checked_at":
            checked,
        "attribution":
            ATTRIBUTION,
        "source_documents":
            sorted(item["documents"]),
        "agency_experience_required":
            experience,
    }


def validate_records(records, allow_empty=False):
    if not isinstance(records, list) or not records and not allow_empty:
        raise AdapterError("JobSearch returned an empty or invalid collection",
                           "validate")
    seen = set()
    for record in records:
        if (not isinstance(record, dict) or record.get("source") != SOURCE_ID or
                not isinstance(record.get("id"), str) or not re.fullmatch(
                    re.escape(SOURCE_ID) + r"-[1-9][0-9]*", record.get(
                        "id", "")) or record["id"].lower() in seen or
                canonical_ad(record.get("url"))[0]
                != record["id"][len(SOURCE_ID) + 1:] or
                not isinstance(record.get("title"), str) or
                not record["title"].strip() or
                record.get("category") != "training" or
                record.get("kind") not in ("trainee", "apprenticeship") or
                not isinstance(record.get("summary"), str) or
                not 0 < len(record["summary"]) <= 600 or
                record.get("status") not in ("open", "expired", "unknown") or
                record.get("attribution") != ATTRIBUTION or
                record.get("host_countries") != ["SE"] or
                record.get("eligible_countries") != [] or
                record.get("summary_language") != "en" or
                record.get("agency_experience_required") is not None and
                type(record["agency_experience_required"]) is not bool or
                not isinstance(record.get("source_documents"), list) or
                not record["source_documents"] or
                len(set(record["source_documents"])) != len(
                    record["source_documents"]) or
                record.get("employer") is not None and
            (not isinstance(record["employer"], str) or
             not record["employer"].strip())):
            raise AdapterError(
                "Invalid agency record identity or factual contract",
                "validate")
        seen.add(record["id"].lower())
        for document in record["source_documents"]:
            publisher_url(document)
        for field in ("deadline", "published_at"):
            value = record.get(field)
            if value is not None and publisher_date(value) != value:
                raise AdapterError("Invalid normalized publisher date",
                                   "validate")
        for field in _TEMPORAL_FIELDS:
            value = record.get(field)
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.utcoffset() is None:
                    raise ValueError()
            except (TypeError, ValueError, AttributeError) as error:
                raise AdapterError("Invalid agency observation timestamp",
                                   "validate") from error


def remove_expired_records(records, checked):
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.utcoffset() is None:
        raise AdapterError("Expiry reference clock requires a timezone",
                           "validate")
    result = [
        r for r in records if r["status"] != "expired" and
        (r["deadline"] is None or deadline_time(r["deadline"]) >= now)
    ]
    validate_records(result, allow_empty=True)
    return result


def collect():
    global _COLLECTION_END
    if not _PUBLISHING and "DATA_SOURCE_TOKEN" in os.environ:
        raise AdapterError(
            "Native JobSearch test refuses publication credentials", "access")
    if _END is None:
        with execution():
            return collect()
    _COLLECTION_END = min(_END - 240, time.monotonic() + COLLECTION_SECONDS)
    arm_deadline()
    try:
        inventory = collect_inventory()
        checked = utc_now()
        records = [
            record for item in inventory.values()
            if (record := normalize_ad(item, checked)) is not None
        ]
        closing = collect_inventory()
        if inventory != closing:
            raise AdapterError(
                "Complete JobSearch candidate identities/facts changed during reconciliation"
            )
        records.sort(key=lambda r: r["id"])
        validate_records(records)
        return records
    finally:
        _COLLECTION_END = None
        arm_deadline()


def metadata(attempt, previous, records, error=None):
    return {
        "source":
            SOURCE_ID,
        "display_name":
            "Arbetsformedlingen Sweden trainee and apprentice vacancies",
        "source_url":
            SOURCE_URL,
        "website_url":
            WEBSITE_URL,
        "publisher_country":
            "SE",
        "attribution":
            ATTRIBUTION,
        "status":
            "fail" if error else "success",
        "last_attempt_at":
            attempt,
        "last_success_at":
            previous.get("last_success_at") if error else attempt,
        "last_checked_at":
            previous.get("last_checked_at") if error else attempt,
        "record_count":
            len(records),
        "message":
            "Collection failed; last-good data preserved" if error else
            f"Validated complete agency scope retains {len(records)} vacancies",
        "error":
            safe_error(error) if error else None,
        **({
            "failure_stage": getattr(error, "stage", "runtime")
        } if error else {})
    }


def read_snapshot(token):
    ref = github("/git/ref/heads/main", DATA_REPOSITORY, token=token)
    head = ref["object"]["sha"]
    commit = github("/git/commits/" + head, DATA_REPOSITORY, token=token)
    files = {}
    for name in ("data.json", "metadata.json"):
        result = github("/contents/datas/se-arbetsformedlingen-jobsearch/" +
                        name + "?ref=" + head,
                        DATA_REPOSITORY,
                        token=token,
                        missing=True)
        files[name] = None if result is None else base64.b64decode(
            result["content"]).decode()
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError("Incomplete published JobSearch pair", "publish")
    return {"head": head, "tree": commit["tree"]["sha"], "files": files}


def publish(snapshot, files, token):
    entries = []
    for name, content in files.items():
        blob = github("/git/blobs", DATA_REPOSITORY, "POST", {
            "content": content,
            "encoding": "utf-8"
        }, token)
        entries.append({
            "path": "datas/se-arbetsformedlingen-jobsearch/" + name,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"]
        })
    for _ in range(3):
        current = read_snapshot(token)
        if current["files"] != snapshot["files"]:
            raise AdapterError(
                "Newer JobSearch source pair exists; no overwrite", "publish")
        tree = github("/git/trees", DATA_REPOSITORY, "POST", {
            "base_tree": current["tree"],
            "tree": entries
        }, token)
        commit = github(
            "/git/commits", DATA_REPOSITORY, "POST", {
                "message":
                    "Update se-arbetsformedlingen-jobsearch collection outcome",
                "tree":
                    tree["sha"],
                "parents": [current["head"]]
            }, token)
        status, _, _ = infrastructure(
            "https://api.github.com/repos/" + DATA_REPOSITORY +
            "/git/refs/heads/main", "PATCH", {
                "sha": commit["sha"],
                "force": False
            }, token)
        if status == 200:
            return
        if status not in {409, 422}:
            raise AdapterError("JobSearch atomic publication failed", "publish")
    raise AdapterError("JobSearch publication conflicts exceeded bound",
                       "publish")


def run(publishing):
    token = os.environ.get("DATA_SOURCE_TOKEN") if publishing else None
    if publishing and not token:
        raise AdapterError("Publication token missing", "publish")
    snapshot = read_snapshot(token) if publishing else None
    old = json.loads(
        snapshot["files"]["data.json"]
    ) if snapshot and snapshot["files"]["data.json"] is not None else []
    previous = json.loads(
        snapshot["files"]["metadata.json"]
    ) if snapshot and snapshot["files"]["metadata.json"] is not None else {}
    validate_records(old, allow_empty=True)
    if previous and previous.get("source") != SOURCE_ID:
        raise AdapterError("Prior JobSearch metadata identity mismatch",
                           "publish")
    attempt = utc_now()
    try:
        records = remove_expired_records(collect(), utc_now())
        prior = {r["id"]: r for r in old}
        temporal = {
            "created_at", "first_seen_at", "updated_at", "last_seen_at",
            "last_checked_at"
        }
        for record in records:
            before = prior.get(record["id"], {})
            same = {
                k: v for k, v in record.items() if k not in temporal
            } == {
                k: v for k, v in before.items() if k not in temporal
            }
            record["created_at"] = before.get("created_at",
                                              record["created_at"])
            record["first_seen_at"] = before.get("first_seen_at",
                                                 record["first_seen_at"])
            if same:
                record["updated_at"] = before["updated_at"]
        records.sort(key=lambda r: r["id"])
        validate_records(records, allow_empty=True)
        outcome = metadata(attempt, previous, records)
    except Exception as error:
        outcome = metadata(attempt, previous, old, error)
        if publishing and snapshot["files"]["data.json"] is not None:
            publish(
                snapshot, {
                    "metadata.json":
                        json.dumps(outcome, ensure_ascii=False, indent=2) + "\n"
                }, token)
        print(json.dumps(outcome, ensure_ascii=False))
        return 1
    if publishing:
        publish(
            snapshot, {
                "data.json":
                    json.dumps(records, ensure_ascii=False, indent=2) + "\n",
                "metadata.json":
                    json.dumps(outcome, ensure_ascii=False, indent=2) + "\n"
            }, token)
    print(json.dumps(outcome, ensure_ascii=False))
    return 0


def main():
    global _PUBLISHING
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    _PUBLISHING = args.publish
    try:
        with execution():
            return run(args.publish)
    except (Exception, DeadlineExceeded) as error:
        print(
            json.dumps({
                "source": SOURCE_ID,
                "status": "fail",
                "error": safe_error(error),
                "message": "Run failed; durable outcome unconfirmed"
            }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
