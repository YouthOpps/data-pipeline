"""Nestle's public, counted Internships/University Jobs catalogue.

This source covers the publisher's category, not all Nestle vacancies worldwide.
Only attributed facts and original summaries are published. Application and
account endpoints, including the robots-disallowed /services/ API, are excluded.
"""

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
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
import zipfile

SOURCE_ID = "nestle"
SOURCE_URL = "https://jobdetails.nestle.com/go/InternshipsUniversity-Jobs/3034301/"
WEBSITE_URL = "https://www.nestle.com/jobs/students-graduates"
HOST = "jobdetails.nestle.com"
FAMILY = "youthopps-nestle-publisher-research-v1"
USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
ATTRIBUTION = "Nestle official Internships/University Jobs catalogue"
REPOSITORY = "YouthOpps/data-pipeline"
WORKFLOW_PATH = ".github/workflows/fetch-nestle.yml"
ARTIFACT_NAME = "nestle-pacing-state"
DATA_REPOSITORY = "YouthOpps/data-source"
RUN_SECONDS = 3000
COLLECTION_SECONDS = 2700
MAX_STATE_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 4 * 1024 * 1024
BOOTSTRAP_EVIDENCE = "https://github.com/YouthOpps/data-pipeline/issues/372#issuecomment-6924734579"
RESEARCH_JOURNAL_SHA256 = "9ec1297b856fc4c22df2a4081d7fba92a8996912e60c893398b2f23948959990"
RESEARCH_PROVENANCE = "First authorized research; no existing Nestle adapter or research journal found"
WORKFLOW_SHA256 = "ba077f2d6119ad0caccf3a50c8d40fb6276ba06ff9ce17866fe139e709163215"
BOOTSTRAP_STATE = {
    'blocked': False,
    'completed': 1794849735.4920633,
    'family': 'youthopps-nestle-publisher-research-v1',
    'generation': 0,
    'interval': 12.2,
    'inventory_completed': 1794849647.2996705,
    'not_before': 0,
    'page_interval': 30.0,
    'pending': None,
    'policies': {
        'jobdetails.nestle.com': [{
            'rules':
                'User-agent: *\n'
                'Disallow: /applybutton/\n'
                'Disallow: /talentcommunity/\n'
                'Disallow: /mobile/talentcommunity/\n'
                'Disallow: /emailsubscribe/\n'
                'Disallow: /email/image/\n'
                'Disallow: /services/\n'
                'Disallow: /preapply/\n'
                'Disallow: /error\n'
                'Disallow: /unsubscribe/\n'
                'Disallow: /reset/',
            'sha256':
                '91669efc2767d124199e1d848655af18d64ac71e61ae1227be7b6aa866f03caf',
            'status':
                200
        }],
        'www.nestle.com': [{
            'rules':
                '#\r\n'
                '# robots.txt\r\n'
                '#\r\n'
                '# This file is to prevent the crawling and indexing of '
                'certain parts\r\n'
                '# of your site by web crawlers and spiders run by '
                'sites like Yahoo!\r\n'
                '# and Google. By telling these "robots" where not to '
                'go on your site,\r\n'
                '# you save bandwidth and server resources.\r\n'
                '#\r\n'
                '# This file will be ignored unless it is at the root '
                'of your host:\r\n'
                '# Used:    http://example.com/robots.txt\r\n'
                '# Ignored: http://example.com/site/robots.txt\r\n'
                '#\r\n'
                '# For more information about the robots.txt standard, '
                'see:\r\n'
                '# http://www.robotstxt.org/robotstxt.html\r\n'
                '\r\n'
                'User-agent: *\r\n'
                '# Content-Signal: declares how crawlers may use this '
                'content - ai-train=no (no AI model training), '
                'search=yes (allow search indexing), ai-input=yes '
                '(allow use as input for AI features).\r\n'
                'Content-Signal: ai-train=no, search=yes, '
                'ai-input=yes\r\n'
                '# CSS, JS, Images\r\n'
                'Allow: /core/*.css$\r\n'
                'Allow: /core/*.css?\r\n'
                'Allow: /core/*.js$\r\n'
                'Allow: /core/*.js?\r\n'
                'Allow: /core/*.gif\r\n'
                'Allow: /core/*.jpg\r\n'
                'Allow: /core/*.jpeg\r\n'
                'Allow: /core/*.png\r\n'
                'Allow: /core/*.svg\r\n'
                'Allow: /profiles/*.css$\r\n'
                'Allow: /profiles/*.css?\r\n'
                'Allow: /profiles/*.js$\r\n'
                'Allow: /profiles/*.js?\r\n'
                'Allow: /profiles/*.gif\r\n'
                'Allow: /profiles/*.jpg\r\n'
                'Allow: /profiles/*.jpeg\r\n'
                'Allow: /profiles/*.png\r\n'
                'Allow: /profiles/*.svg\r\n'
                '# Directories\r\n'
                'Disallow: /core/\r\n'
                'Disallow: /profiles/\r\n'
                '# Files\r\n'
                'Disallow: /README.txt\r\n'
                'Disallow: /web.config\r\n'
                '# Paths (clean URLs)\r\n'
                'Disallow: /admin/\r\n'
                'Disallow: /comment/reply/\r\n'
                'Disallow: /filter/tips/\r\n'
                'Disallow: /node/add/\r\n'
                'Disallow: /jobs/search/\r\n'
                'Disallow: /user/register/\r\n'
                'Disallow: /user/password/\r\n'
                'Disallow: /user/login/\r\n'
                'Disallow: /user/logout/\r\n'
                '# Paths (no clean URLs)\r\n'
                'Disallow: /index.php/admin/\r\n'
                'Disallow: /index.php/comment/reply/\r\n'
                'Disallow: /index.php/filter/tips/\r\n'
                'Disallow: /index.php/node/add/\r\n'
                'Disallow: /index.php/search/\r\n'
                'Disallow: /index.php/user/password/\r\n'
                'Disallow: /index.php/user/register/\r\n'
                'Disallow: /index.php/user/login/\r\n'
                'Disallow: /index.php/user/logout/\r\n'
                'Disallow: /node/*\r\n'
                'Disallow: /taxonomy/term/*\r\n'
                '# Disallow Ajax loaded pages\r\n'
                'Disallow: /feedback-form\r\n'
                'Disallow: /report-concern\r\n'
                '# Disallow brand search page\r\n'
                'Disallow: /brands/brandssearchlist\r\n'
                'Disallow: /cdn-cgi\r\n'
                'Disallow: /node\n'
                '# Simple sitemap\n'
                'Sitemap: https://www.nestle.com/sitemap.xml',
            'sha256':
                '5a57587cee18376a2490e358325f978f8763c99d4f446fe06c077a3ba4c1269a',
            'status':
                200
        }]
    },
    'schema': 1,
    'starts': [
        1794849611.7576716, 1794849644.841143, 1794849668.8047931,
        1794849681.595636, 1794849708.6015742, 1794849733.3677218
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
        raise AdapterError("Untrusted Nestle state identity", "access")
    for key in ("completed", "inventory_completed", "interval", "page_interval",
                "not_before"):
        if not numeric(value[key]):
            raise AdapterError("Unsupported Nestle state clock", "access")
    if (value["interval"] < 12.2 or value["page_interval"] < 30 or
            not isinstance(value["starts"], list) or
            any(not numeric(v) for v in value["starts"]) or
            value["starts"] != sorted(set(value["starts"]))):
        raise AdapterError("Invalid retained Nestle pacing", "access")
    pending = value["pending"]
    if pending is not None and (
            not isinstance(pending, dict) or
            set(pending) != {"id", "url", "inventory", "started"} or
            not re.fullmatch(r"[0-9a-f]{32}", pending.get("id", "")) or
            type(pending.get("inventory")) is not bool or
            not numeric(pending.get("started")) or
            not isinstance(pending.get("url"), str)):
        raise AdapterError("Invalid pending Nestle owner", "access")
    if not isinstance(value["policies"], dict):
        raise AdapterError("Invalid retained robots history", "access")
    for host, policies in value["policies"].items():
        if host not in {HOST, "www.nestle.com", "www.nestlejobs.com"
                       } or not isinstance(policies, list):
            raise AdapterError("Untrusted robots origin", "access")
        for policy in policies:
            if (not isinstance(policy, dict) or
                    set(policy) != {"status", "sha256", "rules"} or
                    type(policy["status"]) is not int or
                    not isinstance(policy["rules"], str) or
                    not re.fullmatch(r"[0-9a-f]{64}", policy["sha256"]) or
                    hashlib.sha256(policy["rules"].encode()).hexdigest()
                    != policy["sha256"]):
                raise AdapterError("Untrusted robots bytes", "access")
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
        raise AdapterError("Conflicting pending Nestle owners", "access")
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
            raise AdapterError("Unsafe Nestle state directory", "access")
        self.parent_id = (info.st_dev, info.st_ino)
        try:
            os.stat("failed", dir_fd=self.parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise AdapterError("Nestle journal has a retained failure marker",
                               "access")
        created = False
        try:
            self.fd = os.open("state.json",
                              os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=self.parent)
        except FileNotFoundError:
            if not new_directory:
                raise AdapterError("Mature Nestle journal is missing", "access")
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
                raise AdapterError("Mature Nestle journal is unavailable",
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
            raise AdapterError("Nestle state exceeds safe byte bound", "access")
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
            raise AdapterError("Nestle journal identity changed", "access")
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
                raise AdapterError("Nestle journal ownership lock was lost",
                                   "access")
        finally:
            os.close(probe)
        if check_bytes and self.ack is not None and self.read() != self.ack:
            raise AdapterError("Nestle journal generation changed", "access")

    def save(self):
        try:
            if self.fatal:
                raise AdapterError("Nestle journal failure is sticky", "access")
            validate_state(self.state)
            self.verify()
            self.state["generation"] += 1
            raw = json.dumps(self.state, sort_keys=True,
                             separators=(",", ":")).encode()
            if len(raw) > MAX_STATE_BYTES:
                raise AdapterError("Nestle state capacity exceeded", "access")
            os.ftruncate(self.fd, 0)
            self.ack = b""
            self.verify()
            while len(self.ack) < len(raw):
                offset = len(self.ack)
                count = os.pwrite(self.fd, raw[offset:], offset)
                if count <= 0:
                    raise OSError("Short Nestle state write")
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
                             b"Unacknowledged Nestle journal mutation\n")
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
    path = os.environ.get("NESTLE_PACING_ARTIFACT_PATH", "")
    parent_info, info = os.fstat(parent), os.fstat(descriptor)
    if (not stat.S_ISDIR(parent_info.st_mode) or
            parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022 or
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
        raise AdapterError("Unsafe pinned Nestle artifact", "access")
    if linked:
        root = os.stat(os.path.dirname(path), follow_symlinks=False)
        entry = os.stat(os.path.basename(path),
                        dir_fd=parent,
                        follow_symlinks=False)
        if ((root.st_dev, root.st_ino)
                != (parent_info.st_dev, parent_info.st_ino) or
            (entry.st_dev, entry.st_ino) != (info.st_dev, info.st_ino)):
            raise AdapterError("Nestle artifact identity changed", "access")


def export_target(create=False):
    global _EXPORT_PIN
    path = os.environ.get("NESTLE_PACING_ARTIFACT_PATH")
    if not path:
        return None
    if _EXPORT_PIN is not None:
        artifact_identity(*_EXPORT_PIN)
        return tuple(os.dup(fd) for fd in _EXPORT_PIN)
    root = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(root) or os.path.dirname(
            os.path.abspath(path)) != os.path.realpath(root):
        raise AdapterError("Unsafe Nestle artifact directory", "access")
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
        raise AdapterError("Untrusted Nestle workflow identity", "access")
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
        raise AdapterError("Incomplete Nestle job inventory", "access")
    job = jobs[0]
    if (type(job.get("id")) is not int or
            job["id"] <= 0 or (job.get("run_id"), job.get("run_attempt"),
                               job.get("head_sha")) != identity or
            job.get("head_branch") != "main" or job.get("name") != "collect" or
            job.get("status") != ("in_progress" if current else "completed")):
        raise AdapterError("Untrusted Nestle source job", "access")
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
    definition = github("/actions/workflows/fetch-nestle.yml", missing=True)
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    if definition is None:
        if hosted or github("/contents/" + WORKFLOW_PATH + "?ref=main",
                            missing=True) is not None:
            raise AdapterError("Nestle workflow definition is unavailable",
                               "access")
        return None, [], None
    if (type(definition.get("id")) is not int or definition["id"] <= 0 or
            definition.get("path") != WORKFLOW_PATH or definition.get("state")
            != ("active" if hosted else "disabled_manually")):
        raise AdapterError("Nestle workflow is not serialized for this mode",
                           "access")
    current = None
    if hosted:
        try:
            current = (int(os.environ["GITHUB_RUN_ID"]),
                       int(os.environ["GITHUB_RUN_ATTEMPT"]),
                       os.environ["GITHUB_SHA"])
        except (KeyError, ValueError):
            raise AdapterError("Missing hosted Nestle identity",
                               "access") from None
        if (os.environ.get("GITHUB_JOB") != "collect" or
                os.environ.get("GITHUB_REPOSITORY") != REPOSITORY or
                os.environ.get("GITHUB_REF") != "refs/heads/main" or
                os.environ.get("GITHUB_WORKFLOW_REF")
                != REPOSITORY + "/" + WORKFLOW_PATH + "@refs/heads/main"):
            raise AdapterError("Unexpected hosted Nestle execution", "access")
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
            raise AdapterError("Unstable Nestle workflow inventory", "access")
        total = count
        for run in runs:
            identity = run_identity(run, definition)
            if identity[0] in seen:
                raise AdapterError("Duplicate Nestle workflow run", "access")
            seen.add(identity[0])
            if identity == current:
                if run.get("status") != "in_progress":
                    raise AdapterError("Current Nestle source is not running",
                                       "access")
                job_identity(run, definition, True)
                found = True
                last = identity[1] - 1
            else:
                if run.get("status") != "completed":
                    raise AdapterError("Another Nestle attempt is active",
                                       "access")
                last = identity[1]
            for attempt in range(1, last + 1):
                exact = github(
                    f"/actions/runs/{identity[0]}/attempts/{attempt}")
                if run_identity(exact, definition) != (
                        identity[0], attempt,
                        identity[2]) or exact.get("status") != "completed":
                    raise AdapterError("Untrusted exact Nestle attempt",
                                       "access")
                rows.append(job_identity(exact, definition))
        if len(seen) == total:
            break
        if len(seen) > total or len(runs) != 100:
            raise AdapterError("Incomplete Nestle workflow history", "access")
        page += 1
        check_time()
    if hosted and not found:
        raise AdapterError("Current Nestle run is absent from history",
                           "access")
    rows.sort(key=lambda row: row["end"], reverse=True)
    if any(a["end"] == b["end"] for a, b in zip(rows, rows[1:])):
        raise AdapterError("Ambiguous Nestle job completion chronology",
                           "access")
    return definition["id"], rows, current


def no_publisher_attempt(row):
    """Prove a same-code failure could not pass the monotonic startup floor."""
    if row["conclusion"] != "failure" or not 0 <= row["end"] - row["start"] < 58:
        return False
    head = row["identity"][2]
    for path, expected in (
        ("adapters/nestle/adapter.py",
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
        raise AdapterError("Untrusted owned Nestle artifact", "access")
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
        raise AdapterError("Unsafe Nestle artifact redirect", "access")
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise AdapterError("Literal artifact host refused", "access")
    status, _, raw = infrastructure(location, limit=MAX_STATE_BYTES)
    if status != 200 or hashlib.sha256(
            raw).hexdigest() != artifact["digest"][7:]:
        raise AdapterError("Nestle artifact digest mismatch", "access")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries
              ) != 1 or entries[0].filename != "pacing-state.json" or entries[
                  0].file_size > MAX_STATE_BYTES or entries[
                      0].flag_bits & 1 or stat.S_ISLNK(
                          entries[0].external_attr >> 16):
            raise AdapterError("Unsafe Nestle artifact archive", "access")
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
        raise AdapterError("Nestle artifact ownership mismatch", "access")
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
            raise AdapterError("Incomplete Nestle artifact inventory", "access")
        matches = [
            a for a in artifacts
            if a.get("name") == ARTIFACT_NAME + "-" + str(attempt)
        ]
        if len(matches) == 1:
            incoming = download_state(row, matches[0], workflow_id)
            break
        if artifacts or not no_publisher_attempt(row):
            raise AdapterError(
                "Newest Nestle state missing without a no-access proof",
                "access")
        boundary = max(boundary, row["end"])
    else:
        if github("/contents/datas/nestle?ref=main",
                  repository=DATA_REPOSITORY,
                  missing=True) is not None:
            raise AdapterError(
                "Published Nestle source has lost its owned history", "access")
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
            raise OSError("Nestle artifact readback mismatch")
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
    if _END is not None or _FAILED:
        raise AdapterError("Nested or failed Nestle execution refused",
                           "access")
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    _END = time.monotonic() + RUN_SECONDS
    _FIRST_WIRE = False
    _ROBOTS.clear()
    previous = signal.getsignal(signal.SIGALRM)

    def alarm(_signum, _frame):
        raise DeadlineExceeded("Nestle runtime expired")

    signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, RUN_SECONDS)
    try:
        invalidate_export()
        incoming = restored_state()
        _JOURNAL = Journal(incoming)
        if _JOURNAL.state["pending"] is not None or _JOURNAL.state["blocked"]:
            raise AdapterError("Nestle retains unresolved source access",
                               "access")
        _QUIET_UNTIL = time.monotonic() + 60
        yield
    finally:
        _COLLECTION_END = None
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
            _END = _QUIET_UNTIL = None
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)


def publisher_url(url, robots=False):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != HOST or
            parsed.username or parsed.password or parsed.port or
            parsed.fragment or robots and
        (parsed.path != "/robots.txt" or parsed.query) or not robots and not (
            parsed.path.startswith("/go/InternshipsUniversity-Jobs/3034301/") or
            re.fullmatch(r"/job/[^/]+/[1-9][0-9]*/", parsed.path))):
        raise AdapterError("Outside reviewed Nestle public routes", "access")
    return url


def pace(inventory):
    state = _JOURNAL.state
    if state["blocked"] or state[
            "pending"] is not None or _FAILED or _JOURNAL.fatal:
        raise AdapterError("Retained Nestle access barrier", "access")
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
                "Retained Nestle delay exceeds collection budget", "access")
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


def visible_refusal(text):
    root = Document(text).root

    def strip(node):
        node.children = [
            c for c in node.children if not isinstance(c, Node) or
            c.tag not in {"nav", "header", "footer", "aside"}
        ]
        for child in node.children:
            if isinstance(child, Node):
                strip(child)

    strip(root)
    return bool(
        re.search(
            r"\b(access denied|verify (?:that )?you are human|unusual traffic|checking your browser|enable cookies to continue)\b",
            root.text(), re.I))


def robots_decision(url):
    policies = _JOURNAL.state["policies"].get(HOST, [])
    if not policies:
        raise AdapterError("Nestle robots provenance missing", "access")
    for policy in policies:
        if policy["status"] != 200:
            continue
        parser = urllib.robotparser.RobotFileParser("https://" + HOST +
                                                    "/robots.txt")
        parser.parse(policy["rules"].splitlines())
        if not parser.can_fetch("YouthOppsBot", url):
            _JOURNAL.state["blocked"] = True
            _JOURNAL.save()
            raise AdapterError("Nestle robots refuses this public target",
                               "access")
        delay = parser.crawl_delay("YouthOppsBot")
        rate = parser.request_rate("YouthOppsBot")
        if delay is not None:
            _JOURNAL.state["interval"] = max(_JOURNAL.state["interval"], delay)
        if rate and rate.requests:
            _JOURNAL.state["interval"] = max(_JOURNAL.state["interval"],
                                             rate.seconds / rate.requests)


def fetch_source(url, inventory=False, robots=False):
    global _FAILED, _FIRST_WIRE, _LAST_COMPLETION, _LAST_INVENTORY_COMPLETION
    publisher_url(url, robots)
    if (_FAILED or _JOURNAL.fatal or _JOURNAL.state["pending"] is not None or
            _JOURNAL.state["blocked"]):
        raise AdapterError("Retained Nestle access barrier", "access")
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
                        "Nestle coordination changed before access", "access")
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
            request = urllib.request.Request(url,
                                             headers={
                                                 "User-Agent": USER_AGENT,
                                                 "Accept-Encoding": "identity"
                                             })
            assert request.get_header("User-agent") == USER_AGENT
            response = None
            received = False
            assessed = False
            redirect_to = None
            try:
                _JOURNAL.verify()
                if time.monotonic() < _QUIET_UNTIL:
                    raise AdapterError(
                        "Monotonic Nestle startup floor has not passed",
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
                    state["not_before"] = max(
                        state["not_before"],
                        retry_after(response.headers.get("Retry-After"), now))
                    if status == 429:
                        state["strikes"] += 1
                        state["interval"] *= 2
                        state["page_interval"] *= 2
                        state["not_before"] = max(
                            state["not_before"],
                            now + 1800 * 2**(state["strikes"] - 1))
                    _JOURNAL.save()
                    body = response.read(MAX_BODY_BYTES + 1)
                    if len(body) > MAX_BODY_BYTES:
                        raise AdapterError(
                            "Nestle response exceeds safe byte bound", "fetch")
                    lengths = response.headers.get_all("Content-Length", [])
                    if lengths:
                        values = [
                            part.strip()
                            for value in lengths
                            for part in value.split(",")
                        ]
                        if (any(not re.fullmatch(r"[0-9]+", value)
                                for value in values) or len(set(values)) != 1 or
                                response.headers.get("Transfer-Encoding") or
                                int(values[0]) != len(body)):
                            raise AdapterError(
                                "Nestle response framing is incomplete or conflicting",
                                "fetch")
                    encoding = "utf-8" if robots else response.headers.get_content_charset(
                    ) or "utf-8"
                    text = body.decode(encoding)
                    refused = visible_refusal(text)
                    if refused or not robots and status in {401, 403}:
                        state["blocked"] = True
                    if robots and status == 200:
                        policy = {
                            "status": status,
                            "sha256": hashlib.sha256(text.encode()).hexdigest(),
                            "rules": text
                        }
                        policies = state["policies"].setdefault(HOST, [])
                        if policy not in policies:
                            policies.append(policy)
                        # Persist all newly observed restrictions while pending.
                        robots_decision(SOURCE_URL)
                        _ROBOTS[HOST] = True
                    elif robots and 400 <= status < 500 and status != 429:
                        policy = {
                            "status": status,
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "rules": text
                        }
                        if policy not in state["policies"].setdefault(HOST, []):
                            state["policies"][HOST].append(policy)
                        _ROBOTS[HOST] = True
                    if status in {301, 302, 303, 307, 308}:
                        redirect_to = urllib.parse.urljoin(
                            url, response.headers.get("Location", ""))
                        publisher_url(redirect_to, robots)
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
                        "Retained Nestle publisher refusal or cooldown",
                        "access")
                if redirect_to:
                    url = redirect_to
                    break
                if robots and 400 <= status < 500:
                    return ""
                if status != 200:
                    raise AdapterError("Nestle returned HTTP " + str(status),
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
                        "Nestle connection failed without a response",
                        "fetch") from None
                _FAILED = True
                invalidate_export()
                raise AdapterError(
                    "Unassessed Nestle transport failure retained",
                    "access") from None
            except BaseException:
                if state[
                        "pending"] is not None or _JOURNAL.fatal or received and not assessed:
                    _FAILED = True
                    invalidate_export()
                raise
        else:
            raise AdapterError("Nestle retries exhausted", "fetch")
    raise AdapterError("Nestle redirect bound exceeded", "fetch")


def canonical_job(url):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(SOURCE_URL, url))
    clean = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, "", ""))
    publisher_url(clean)
    match = re.fullmatch(r"/job/[^/]+/([1-9][0-9]*)/", parsed.path)
    if match is None:
        raise AdapterError("Invalid Nestle posting identity", "validate")
    return match[1], clean


def parse_catalog(text, url):
    root = Document(text).root
    labels = root.nodes(lambda n: has_class(n, "pagination-label-row"))
    if not labels:
        raise AdapterError("Nestle catalogue count label missing")
    label = " ".join(n.text() for n in labels)
    match = re.search(
        r"Results\s+([0-9,]+)\s*[–-]\s*([0-9,]+)\s+of\s+([0-9,]+)\s+Page\s+(\d+)\s+of\s+(\d+)",
        label)
    if match is None:
        raise AdapterError("Nestle catalogue pagination changed")
    first, last, total, page, pages = (
        int(v.replace(",", "")) for v in match.groups())
    if not 1 <= first <= last <= total or not 1 <= page <= pages:
        raise AdapterError("Invalid or fallback Nestle catalogue range")
    rows = root.nodes(lambda n: n.tag == "tr" and has_class(n, "data-row"))
    if len(rows) != last - first + 1:
        raise AdapterError("Nestle row count does not match advertised range")
    records = {}
    for row in rows:
        anchors = row.nodes(
            lambda n: n.tag == "a" and has_class(n, "jobTitle-link"))
        choices = {
            (canonical_job(n.attrs.get("href", "")), n.text()) for n in anchors
        }
        if len(choices) != 1:
            raise AdapterError("Conflicting Nestle mobile/desktop job identity")
        ((identifier, canonical), title), = choices
        if not title or identifier in records:
            raise AdapterError("Empty or duplicate Nestle catalogue job")
        location = row.nodes(
            lambda n: n.tag == "td" and has_class(n, "colLocation"))
        dates = row.nodes(lambda n: n.tag == "td" and has_class(n, "colDate"))
        if len(location) != 1 or len(dates) != 1:
            raise AdapterError("Nestle catalogue factual columns missing")
        records[identifier] = {
            "id": identifier,
            "url": canonical,
            "title": title,
            "location": location[0].text(),
            "date": dates[0].text()
        }
    links = {}
    for anchor in root.nodes(lambda n: n.tag == "a"):
        value = anchor.text()
        target = urllib.parse.urljoin(url, anchor.attrs.get("href", ""))
        if value.isdigit() and urllib.parse.urlsplit(target).path.startswith(
                urllib.parse.urlsplit(SOURCE_URL).path):
            number = int(value)
            publisher_url(target)
            if number in links and links[number] != target:
                raise AdapterError("Conflicting Nestle pagination link")
            links[number] = target
    return {
        "first": first,
        "last": last,
        "total": total,
        "page": page,
        "pages": pages,
        "links": links,
        "records": records
    }


def collect_catalog():
    first = parse_catalog(fetch_source(SOURCE_URL, inventory=True), SOURCE_URL)
    if first["first"] != 1 or first["page"] != 1:
        raise AdapterError("Nestle catalogue did not begin at page one")
    records = dict(first["records"])
    links = dict(first["links"])
    expected_first = first["last"] + 1
    for page in range(2, first["pages"] + 1):
        if page not in links:
            raise AdapterError("Nestle advertised a page without a public link")
        current = parse_catalog(fetch_source(links[page], inventory=True),
                                links[page])
        if (current["page"] != page or current["pages"] != first["pages"] or
                current["total"] != first["total"] or
                current["first"] != expected_first or
                set(records).intersection(current["records"])):
            raise AdapterError(
                "Nestle inventory changed or duplicated across pages")
        expected_first = current["last"] + 1
        records.update(current["records"])
        for number, target in current["links"].items():
            if number not in links:
                links[number] = target
    if len(records) != first["total"] or expected_first != first["total"] + 1:
        raise AdapterError("Nestle catalogue coverage is incomplete")
    return records


def parse_detail(text, card):
    root = Document(text).root
    shells = root.nodes(
        lambda n: "schema.org/JobPosting" in n.attrs.get("itemtype", ""))
    if len(shells) != 1:
        raise AdapterError("Nestle public JobPosting scope missing")
    shell = shells[0]
    canonical = root.nodes(
        lambda n: n.tag == "link" and n.attrs.get("rel") == "canonical")
    if len(canonical) != 1 or canonical_job(canonical[0].attrs.get(
            "href", ""))[0] != card["id"]:
        raise AdapterError("Nestle detail/canonical identity mismatch")
    titles = {
        n.text()
        for n in shell.nodes(lambda n: n.attrs.get("itemprop") == "title")
    }
    if not titles:
        titles = {n.text() for n in shell.nodes(lambda n: n.tag == "h1")}
    if titles != {card["title"]}:
        raise AdapterError("Nestle detail title disagrees with catalogue")
    fields = {}
    for node in shell.nodes(
            lambda n: n.tag == "meta" and "itemprop" in n.attrs):
        key, value = node.attrs["itemprop"], node.attrs.get("content", "")
        if key in fields and fields[key] != value:
            raise AdapterError("Conflicting Nestle structured facts")
        fields[key] = value
    if not fields.get("streetAddress") or fields["streetAddress"] != card[
            "location"] or not fields.get("hiringOrganization"):
        raise AdapterError(
            "Nestle detail location/employer missing or conflicting")
    posted = None
    if fields.get("datePosted"):
        try:
            posted = datetime.strptime(
                fields["datePosted"], "%a %b %d %H:%M:%S UTC %Y").replace(
                    tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        except ValueError:
            raise AdapterError("Unsupported Nestle posting date") from None
    deadline = None
    if fields.get("validThrough"):
        try:
            parsed = datetime.fromisoformat(fields["validThrough"].replace(
                "Z", "+00:00"))
            if parsed.utcoffset() is None:
                raise ValueError()
            deadline = parsed.isoformat().replace("+00:00", "Z")
        except ValueError:
            raise AdapterError(
                "Unsupported Nestle structured closing date") from None
    countries = re.findall(r"(?:^|,)\s*([A-Z]{2})(?:\s*,|\s*$)",
                           fields["streetAddress"])
    country = countries[-1] if countries else None
    title = card["title"]
    if re.search(
            r"\b(apprentice|apprenticeship|trainee|graduate program(?:me)?)\b",
            title, re.I):
        category = "training"
    elif re.search(
            r"\b(intern|internship|internships|stage|praktikum|praktikant|staj)\b",
            title, re.I):
        category = "internships"
    else:
        category = "other"
    checked = utc_now()
    record = {
        "id":
            "nestle-" + card["id"],
        "source":
            SOURCE_ID,
        "source_url":
            SOURCE_URL,
        "title":
            title,
        "url":
            canonical_job(canonical[0].attrs["href"])[1],
        "category":
            category,
        "summary":
            "Official Nestle university-career opportunity in " +
            fields["streetAddress"] +
            ". Review the publisher's vacancy for requirements and application details.",
        "status":
            "open",
        "deadline":
            deadline,
        "published_at":
            posted,
        "host_countries": [country] if country else [],
        "eligible_countries": [],
        "location":
            fields["streetAddress"],
        "employer":
            fields["hiringOrganization"],
        "publisher_country":
            "CH",
        "attribution":
            ATTRIBUTION,
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
        "source_documents": [SOURCE_URL, card["url"]]
    }
    validate_records([record])
    return record


def validate_records(records, allow_empty=False):
    if not isinstance(records, list) or not records and not allow_empty:
        raise AdapterError("Nestle returned an empty or invalid catalogue",
                           "validate")
    seen = set()
    for record in records:
        if (not isinstance(record, dict) or record.get("source") != SOURCE_ID or
                record.get("source_url") != SOURCE_URL or
                not re.fullmatch(r"nestle-[1-9][0-9]*", record.get("id", "")) or
                record["id"].lower() in seen or
                canonical_job(record.get("url", ""))[0] != record["id"][7:] or
                not isinstance(record.get("title"), str) or
                not record["title"].strip() or record.get("category")
                not in {"internships", "training", "other"} or
                not isinstance(record.get("summary"), str) or
                len(record["summary"]) > 600 or
                record.get("status") not in {"open", "expired", "unknown"} or
                record.get("attribution") != ATTRIBUTION):
            raise AdapterError("Invalid Nestle record identity or facts",
                               "validate")
        seen.add(record["id"].lower())
        for field in ("host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or len(values) != len(
                    set(values)) or any(not isinstance(v, str) or
                                        not re.fullmatch(r"[A-Z]{2}", v)
                                        for v in values):
                raise AdapterError("Invalid Nestle country facts", "validate")
        for field in ("deadline", "published_at", "created_at", "updated_at",
                      "first_seen_at", "last_seen_at", "last_checked_at"):
            value = record.get(field)
            if value is None and field in {"deadline", "published_at"}:
                continue
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if parsed.utcoffset() is None:
                    raise ValueError()
            except (TypeError, ValueError, AttributeError):
                raise AdapterError("Invalid Nestle date field: " + field,
                                   "validate") from None


def remove_expired_records(records, checked):
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.utcoffset() is None:
        raise AdapterError("Expiry clock requires timezone", "validate")
    current = [
        r for r in records if r["deadline"] is None or
        datetime.fromisoformat(r["deadline"].replace("Z", "+00:00")) >= now
    ]
    validate_records(current, allow_empty=True)
    return current


def collect():
    global _COLLECTION_END
    if not _PUBLISHING and "DATA_SOURCE_TOKEN" in os.environ:
        raise AdapterError("Native Nestle test refuses publication credentials",
                           "access")
    if _END is None:
        with execution():
            return collect()
    _COLLECTION_END = min(_END - 240, time.monotonic() + COLLECTION_SECONDS)
    try:
        cards = collect_catalog()
        records = [
            parse_detail(fetch_source(card["url"]), card)
            for card in cards.values()
        ]
        closing = collect_catalog()
        if cards != closing:
            raise AdapterError(
                "Nestle catalogue changed during full reconciliation")
        validate_records(records)
        return records
    finally:
        _COLLECTION_END = None


def metadata(attempt, previous, records, error=None):
    return {
        "source":
            SOURCE_ID,
        "display_name":
            "Nestle internships and university jobs",
        "source_url":
            SOURCE_URL,
        "website_url":
            WEBSITE_URL,
        "publisher_country":
            "CH",
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
            f"Validated complete category retains {len(records)} vacancies",
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
        result = github("/contents/datas/nestle/" + name + "?ref=" + head,
                        DATA_REPOSITORY,
                        token=token,
                        missing=True)
        files[name] = None if result is None else base64.b64decode(
            result["content"]).decode()
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError("Incomplete published Nestle pair", "publish")
    return {"head": head, "tree": commit["tree"]["sha"], "files": files}


def publish(snapshot, files, token):
    entries = []
    for name, content in files.items():
        blob = github("/git/blobs", DATA_REPOSITORY, "POST", {
            "content": content,
            "encoding": "utf-8"
        }, token)
        entries.append({
            "path": "datas/nestle/" + name,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"]
        })
    for _ in range(3):
        current = read_snapshot(token)
        if current["files"] != snapshot["files"]:
            raise AdapterError("Newer Nestle source pair exists; no overwrite",
                               "publish")
        tree = github("/git/trees", DATA_REPOSITORY, "POST", {
            "base_tree": current["tree"],
            "tree": entries
        }, token)
        commit = github(
            "/git/commits", DATA_REPOSITORY, "POST", {
                "message": "Update nestle collection outcome",
                "tree": tree["sha"],
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
            raise AdapterError("Nestle atomic publication failed", "publish")
    raise AdapterError("Nestle publication conflicts exceeded bound", "publish")


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
        raise AdapterError("Prior Nestle metadata identity mismatch", "publish")
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
