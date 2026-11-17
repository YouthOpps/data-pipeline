"""Discover FRSE funding, training, jobs and calls from current public HTML."""

import argparse
import ast
import base64
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import email.utils
from email.utils import parsedate_to_datetime
import errno
import fcntl
import hashlib
from html.parser import HTMLParser
from http.cookies import SimpleCookie
import io
import ipaddress
import json
import math
import os
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
import zipfile

SOURCE_ID = "pl-frse"
SOURCE_URL = "https://www.frse.org.pl/"
WEBSITE_URL = "https://www.frse.org.pl/"
LANGUAGE = "pl"
PUBLISHER_COUNTRY = "PL"
PUBLISHER_TYPE = "education-foundation"
ATTRIBUTION = (
    "Fundacja Rozwoju Systemu Edukacji (FRSE), https://www.frse.org.pl/; "
    "official Erasmus+, European Solidarity Corps and named FRSE programmes")

_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
CATEGORIES = {
    "scholarships",
    "grants",
    "internships",
    "jobs",
    "training",
    "fellowships",
    "competitions",
    "volunteering",
}
FAMILY = "youthopps-frse-publisher-v1"
FAMILY_SOURCES = {"pl-frse"}
_LOCAL_WORKFLOW_ID = 378391590
COLLECTION_TIMEOUT = 1800
RUN_TIMEOUT = 2190
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "frse-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None

_ATTEMPT_HEADS = {}
_PUBLISHER_GATE_FAILED = False


def fetch_inventory_page(fetch, *args, **kwargs):
    """Mark real inventory requests; cached documents perform no transport."""
    return fetch(*args, inventory=True, **kwargs)


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        attrs = list(attrs)
        self.attrs = dict(attrs)
        self.duplicate_attrs = len(self.attrs) != len(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        parts = []
        for child in self.children:
            parts.append(child.text() if isinstance(child, Node) else child)
        return " ".join(" ".join(parts).split())

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class PublisherHTML(HTMLParser):
    """Small structural parser retaining the publisher's content boundaries."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in (
                "area",
                "base",
                "br",
                "col",
                "embed",
                "hr",
                "img",
                "input",
                "link",
                "meta",
                "param",
                "source",
                "track",
                "wbr",
        ):
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def nodes(node, tag=None, class_name=None):
    return [
        n for n in node.walk() if (tag is None or n.tag == tag) and
        (class_name is None or class_name in n.attrs.get("class", "").split())
    ]


def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]


class AdapterError(Exception):

    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):

    def redirect_request(self, request, file, code, message, headers, url):
        return None


def utc_now():
    return (datetime.now(
        timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"))


def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit((
            urllib.parse.urlsplit(match[0]).scheme,
            urllib.parse.urlsplit(match[0]).hostname or "",
            urllib.parse.urlsplit(match[0]).path,
            "",
            "",
        )),
        text,
    )
    text = re.sub(r"(?i)\b(?:bearer|basic)\s+\S+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(token|password|secret|api[_-]?key|authorization)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        text,
    )
    return " ".join(text.split())[:500]


class PhaseExpired(BaseException):
    """A non-renewable whole-phase deadline, never an ordinary HTTP error."""


def check_deadline():
    if _PHASE_END is None or _RUN_END is None:
        raise AdapterError("Bounded execution context required", "access")
    if time.monotonic() >= min(_PHASE_END, _RUN_END):
        raise PhaseExpired()


def request_timeout(publisher=False):
    check_deadline()
    remaining = min(_PHASE_END, _RUN_END) - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    return min(60 if publisher else 15, remaining)


def alarm_expired(signum, frame):
    raise PhaseExpired()


def arm_alarm(deadline):
    """Keep the absolute alarm armed even outside named phases."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PhaseExpired()
    signal.setitimer(signal.ITIMER_REAL, remaining)


@contextlib.contextmanager
def execution():
    global _RUN_END, _COLLECTION_LOCK_FD
    if _RUN_END is not None:
        raise AdapterError("Nested execution context refused", "access")
    previous = signal.getsignal(signal.SIGALRM)
    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise AdapterError("Existing process alarm refused", "access")
    signal.signal(signal.SIGALRM, alarm_expired)
    _RUN_END = time.monotonic() + RUN_TIMEOUT
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        _RUN_END = None
        try:
            journal_finish()
        finally:
            try:
                close_collection_lock()
            finally:
                journal_finish_outputs()


@contextlib.contextmanager
def phase(name, seconds):
    global _PHASE_END, _PHASE_NAME
    if _PHASE_END is not None or _RUN_END is None:
        raise AdapterError("Invalid phase transition", "access")
    _PHASE_NAME = name
    limit = _RUN_END if name == "export" else _RUN_END - EXPORT_TIMEOUT
    _PHASE_END = min(time.monotonic() + seconds, limit)
    try:
        check_deadline()
        arm_alarm(_PHASE_END)
        yield
        check_deadline()
    finally:
        _PHASE_END = _PHASE_NAME = None
        arm_alarm(limit)


_OPENER = urllib.request.build_opener(NoRedirect)


class RateLimitText(HTMLParser):
    """Read visible primary refusals, excluding unrelated ancillary notices."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.parts, self.stack, self.head_title_parts = [], [], []
        self.head_seen = self.head_allowed = False
        self.title_depth = None
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        # HTML block starts close a paragraph in button scope, including its
        # inline descendants. Keep suppression inherited from outer elements.
        if tag in {
                "address",
                "article",
                "aside",
                "blockquote",
                "center",
                "details",
                "dialog",
                "dir",
                "div",
                "dl",
                "fieldset",
                "figcaption",
                "figure",
                "footer",
                "header",
                "hgroup",
                "main",
                "menu",
                "nav",
                "ol",
                "p",
                "search",
                "section",
                "summary",
                "ul",
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
                "pre",
                "listing",
                "hr",
        }:
            for index in range(len(self.stack) - 1, -1, -1):
                ancestor = self.stack[index][0]
                if ancestor == "p":
                    del self.stack[index:]
                    break
                if ancestor in {
                        "applet",
                        "button",
                        "caption",
                        "html",
                        "marquee",
                        "object",
                        "table",
                        "td",
                        "template",
                        "th",
                }:
                    break
        values = {}
        for name, value in attrs:
            values.setdefault(name, value)
        style = re.sub(r"/\*.*?\*/", "", values.get("style") or "",
                       flags=re.S).lower()
        declarations, priorities = {}, {}
        for declaration in style.split(";"):
            name, separator, value = declaration.partition(":")
            if separator:
                name = name.strip()
                important = bool(re.search(r"!\s*important\s*$", value))
                if priorities.get(name) and not important:
                    continue
                declarations[name] = re.sub(r"\s*!\s*important\s*$", "",
                                            value).strip()
                priorities[name] = important
        explicit_suppressed = ("hidden" in values or
                               (values.get("aria-hidden") or
                                "").strip().lower() == "true" or
                               declarations.get("display") == "none" or
                               declarations.get("visibility") == "hidden")
        if (tag == "head" and not self.head_seen and
                all(name == "html" and not suppressed and not visibility
                    for name, suppressed, visibility in self.stack)):
            self.head_seen = True
            self.head_allowed = not explicit_suppressed
        if (tag == "title" and self.head_allowed and self.stack and
                self.stack[-1][0] == "head" and not explicit_suppressed):
            self.title_depth = len(self.stack) + 1
        suppressed = (tag in {
            "head",
            "title",
            "script",
            "style",
            "template",
            "noscript",
            "footer",
            "nav",
            "aside",
        } or "hidden" in values or (values.get("aria-hidden") or
                                    "").strip().lower() == "true" or
                      declarations.get("display") == "none")
        inherited = self.stack[-1][1] if self.stack else False
        visibility = self.stack[-1][2] if self.stack else False
        if declarations.get("visibility") == "hidden":
            visibility = True
        elif declarations.get("visibility") == "visible":
            visibility = False
        if tag not in {
                "area",
                "base",
                "br",
                "col",
                "embed",
                "hr",
                "img",
                "input",
                "link",
                "meta",
                "param",
                "source",
                "track",
                "wbr",
        }:
            self.stack.append((tag, inherited or suppressed, visibility))

    def handle_endtag(self, tag):
        if tag == "title":
            self.title_depth = None
        if tag == "head":
            self.head_allowed = False
        for position in range(len(self.stack) - 1, -1, -1):
            if self.stack[position][0] == tag:
                del self.stack[position:]
                break

    def handle_data(self, value):
        if (self.title_depth is not None and
                len(self.stack) == self.title_depth and
                self.stack[-1][0] == "title"):
            self.head_title_parts.append(value)
        if not self.stack or not any(self.stack[-1][1:]):
            self.parts.append(value)


def visible_access_challenge(body, challenge_only=False):
    """Recognize primary HTML refusals, not arbitrary JS/JSON/PDF strings."""
    document = body.decode("utf-8-sig", "replace")
    if not document.lstrip().startswith("<"):
        return False

    class RefusalText(RateLimitText):
        """Retain already-visible headlines as independent refusal evidence."""

        def __init__(self, value):
            self.headlines = []
            self.heading = None
            super().__init__(value)

        def handle_starttag(self, tag, attrs):
            super().handle_starttag(tag, attrs)
            if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                self.headlines.append([])
                self.heading = (tag, len(self.headlines) - 1)

        def handle_endtag(self, tag):
            super().handle_endtag(tag)
            if self.heading is not None and self.heading[0] == tag:
                self.heading = None

        def handle_data(self, value):
            before = len(self.parts)
            super().handle_data(value)
            if self.heading is not None and len(self.parts) > before:
                self.headlines[self.heading[1]].append(value)

    parser = RefusalText(document)

    # This support instruction describes a possible error, not a refusal.
    # Remove only its own mention; adjacent denials and real HEAD titles stay.
    def without_instruction(parts):
        text = " ".join(" ".join(parts).lower().split())
        return re.sub(
            r"\bif you see access denied\s*,?\s*"
            r"(?:please\s+)?contact support\b",
            " ",
            text,
        )

    units = [parser.parts, parser.head_title_parts] + parser.headlines
    text = " ".join(without_instruction(parts) for parts in units)
    phrases = (
        "verify you are human",
        "verify that you are human",
        "human verification",
        "confirm you are human",
        "confirm that you are human",
        "confirm you are a human",
        "confirm that you are a human",
        "complete a captcha",
        "complete the captcha",
        "enable javascript and cookies to continue",
        "checking your browser",
    )
    if not challenge_only:
        phrases += (
            "access denied",
            "permission denied",
            "request rejected",
            "request denied",
            "request blocked",
            "unusual traffic",
        )
    explicit_title = without_instruction(parser.head_title_parts)
    return any(phrase in text for phrase in phrases) or bool(
        re.search(
            r"just a moment|attention required|access denied|captcha",
            explicit_title,
        ))


def fetch_public(url, *, inventory=False, headers=None):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, response_headers, body = request_bytes(reviewed,
                                                       publisher=True,
                                                       inventory=inventory,
                                                       headers=headers)
        if status == 200:
            return status, response_headers, body
        if status == 503 and attempt == 0:
            with locked_budget():
                state = load_budget()
                now = time.time()
                state["until"] = max(state["until"], now + 30)
                state["observed_at"] = now
                save_budget(state)
            continue
        key = next(
            (key for key, info in ACCESS_POLICIES.items()
             if info["url"] == url),
            "unmapped-public-input",
        )
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise AdapterError(
            f"Required publisher input {key} returned HTTP {status}: " +
            provenance,
            "fetch",
            status,
        )


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path + ("&" if "?" in path else "?") +
            "_fresh=" + str(time.time_ns()),
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
            },
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError(("Workflow pacing state unavailable"),
                           ("access")) from None


def verify_local_workflow_pause(definition=None):
    """Require maintainer coordination before local publisher access."""
    if definition is None:
        definition = workflow_api("/actions/workflows/fetch-" + SOURCE_ID +
                                  ".yml")
    if (not isinstance(definition, dict) or
            type(definition.get("id")) is not int or
            definition["id"] != _LOCAL_WORKFLOW_ID or definition.get("path")
            != ".github/workflows/fetch-" + SOURCE_ID + ".yml" or
            definition.get("state") != "disabled_manually"):
        raise AdapterError(
            "Local collection requires its exact manually paused workflow",
            "access",
        )


def current_run_identity():
    if (os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline" or
            os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [
        os.environ.get(key, "")
        for key in (("GITHUB_RUN_ID"), ("GITHUB_RUN_ATTEMPT"))
    ]
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))


def family_source(run):
    path = run.get("path", "")
    for source in FAMILY_SOURCES:
        if path == ".github/workflows/fetch-" + source + ".yml":
            return source
    return None


def attempt_completion(identity, attempt, head):
    """Use actual exact-attempt job completion, never mutable run updated_at."""
    total, jobs_seen = None, set()
    finished = []
    for page in range(1, 101):
        result = workflow_api(
            f"/actions/runs/{identity}/attempts/{attempt}/jobs"
            f"?per_page=100&page={page}")
        count, jobs = result.get("total_count"), result.get("jobs")
        if (type(count) is not int or count <= 0 or
                not isinstance(jobs, list) or len(jobs) > 100 or
                total is not None and total != count):
            raise ValueError("Incomplete exact-attempt job inventory")
        total = count
        for job in jobs:
            if (not isinstance(job, dict) or type(job.get("id")) is not int or
                    job["id"] <= 0 or job["id"] in jobs_seen or
                    type(job.get("run_id")) is not int or
                    job["run_id"] != identity or
                    type(job.get("run_attempt")) is not int or
                    job["run_attempt"] != attempt or
                    job.get("head_sha") != head or
                    job.get("head_branch") != "main" or
                    job.get("status") != "completed"):
                raise ValueError("Untrusted exact-attempt job")
            jobs_seen.add(job["id"])
            value = datetime.fromisoformat(job["completed_at"].replace(
                "Z", "+00:00"))
            if value.utcoffset() is None:
                raise ValueError("Missing job completion timezone")
            finished.append(value.timestamp())
        if len(jobs_seen) == total:
            return max(finished)
        if len(jobs_seen) > total or len(jobs) != 100:
            raise ValueError("Truncated exact-attempt job inventory")
    raise ValueError("Exact-attempt job inventory exceeds bound")


def completed_identity(run, expected=None, revision=None):
    """Bind a completed attempt and its chronological publisher interval."""
    try:
        source = family_source(run)
        identity, attempt = run["id"], run["run_attempt"]
        if (not source or type(identity) is not int or identity <= 0 or
                type(attempt) is not int or attempt <= 0 or
                run["status"] != "completed" or
                run["repository"]["full_name"] != "YouthOpps/data-pipeline" or
                run[("head_repository")][("full_name")]
                != ("YouthOpps/data-pipeline") or
                run["head_branch"] != "main" or
                not re.fullmatch(r"[0-9a-f]{40}", run.get("head_sha", "")) or
                type(run.get("workflow_id")) is not int or
                run["workflow_id"] <= 0 or revision is not None and
            (run["head_sha"], run["workflow_id"]) != revision or expected and
            (identity, attempt, source) != expected):
            raise ValueError("Untrusted completed attempt")
        value = datetime.fromisoformat(run["run_started_at"].replace(
            "Z", "+00:00"))
        if value.utcoffset() is None:
            raise ValueError("Missing attempt timezone")
        began = value.timestamp()
        finished = attempt_completion(identity, attempt, run["head_sha"])
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        _ATTEMPT_HEADS[(identity, attempt)] = run["head_sha"]
        return finished, began, identity, attempt, source
    except Exception:
        raise AdapterError(("Untrusted completed family attempt"),
                           ("access")) from None


class InventoryRace(AdapterError):
    """A changed advertised total permits a bounded fresh history scan."""


def latest_family_run():
    """Restart only count-drift races, without reusing partial candidates."""
    for attempt in range(3):
        try:
            return scan_family_history()
        except InventoryRace:
            if attempt == 2:
                raise AdapterError("Workflow inventory remained unstable",
                                   "access") from None


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    hosted = os.environ.get("GITHUB_ACTIONS") == "true"
    current_id, current_attempt = (current_run_identity() if hosted else
                                   (None, None))
    definition = workflow_api("/actions/workflows/fetch-" + SOURCE_ID + ".yml")
    expected_path = ".github/workflows/fetch-" + SOURCE_ID + ".yml"
    if (type(definition.get("id")) is not int or definition["id"] <= 0 or
            definition.get("path") != expected_path):
        raise AdapterError("Untrusted family workflow definition", "access")
    if not hosted:
        verify_local_workflow_pause(definition)
    candidates = []
    current_seen = False
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        route = ("/actions/runs" if hosted else
                 ("/actions/workflows/" + str(definition["id"]) + "/runs"))
        result = workflow_api(route + f"?per_page=100&page={page}")
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (type(count) is not int or count < 0 or not isinstance(runs, list) or
                len(runs) > 100):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace(("Workflow inventory changed during restore"),
                                ("access"))
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            run_id = run.get("id")
            if type(run_id) is not int or run_id <= 0 or run_id in seen_ids:
                raise AdapterError("Invalid or duplicate workflow run identity",
                                   "access")
            seen_ids.add(run_id)
            source = family_source(run)
            if not source:
                if not hosted:
                    raise AdapterError("Unexpected local family source",
                                       "access")
                continue
            if (run.get(("repository"), {}).get(
                ("full_name")) != ("YouthOpps/data-pipeline") or
                    run.get("head_repository", {}).get("full_name")
                    != ("YouthOpps/data-pipeline") or run.get(
                        ("head_branch")) != ("main")):
                raise AdapterError(("Workflow repository or branch mismatch"),
                                   ("access"))
            if run.get("workflow_id") != definition["id"]:
                raise AdapterError("Family workflow identity mismatch",
                                   "access")
            identity, attempt = run.get("id"), run.get("run_attempt")
            if (type(identity) is not int or identity <= 0 or
                    type(attempt) is not int or attempt <= 0):
                raise AdapterError("Invalid family run identity", "access")
            if hosted and identity == current_id:
                current_seen = True
                if (run.get("head_sha") != os.environ.get("GITHUB_SHA") or
                        not re.fullmatch(r"[0-9a-f]{40}", run.get(
                            "head_sha", "")) or
                        type(run.get("workflow_id")) is not int or
                        run["workflow_id"] <= 0 or run.get("path")
                        != ".github/workflows/fetch-" + SOURCE_ID + ".yml"):
                    raise AdapterError("Current workflow binding mismatch",
                                       "access")
                if attempt != current_attempt:
                    raise AdapterError(("Current workflow attempt mismatch"),
                                       ("access"))
                if current_attempt > 1:
                    prior = workflow_api((f"/actions/runs/{identity}/attempts/"
                                          f"{current_attempt - 1}"))
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, current_attempt - 1, source),
                            (run.get("head_sha"), run.get("workflow_id")),
                        ))
                continue
            if not hosted and run.get("status") != "completed":
                raise AdapterError(
                    "Local collection requires terminal family history",
                    "access",
                )
            if run.get(("status")) in (
                ("queued"),
                ("waiting"),
                ("pending"),
                ("requested"),
            ):
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}")
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, attempt - 1, source),
                            (run.get("head_sha"), run.get("workflow_id")),
                        ))
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError(("Workflow inventory count mismatch"),
                                   ("access"))
            if hosted and not current_seen:
                raise AdapterError("Current workflow missing from history",
                                   "access")
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(("Ambiguous family attempt chronology"),
                                   ("access"))
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def document_root(text, complete=True):
    if complete and not re.search(
            r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", text, re.I):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(
            r"just a moment|access denied|attention required|"
            r"checking your browser|verify you are human",
            title,
    ):
        raise AdapterError("Publisher access challenge", "access")
    return root


def make_record(
    title,
    url,
    categories,
    kind="opportunity",
    published_at=None,
    tags=None,
    host_countries=None,
    method="editorial-review",
    evidence=None,
):
    now = utc_now()
    return {
        "id": hashlib.sha256(f"{SOURCE_ID}|{url}".encode()).hexdigest()[:24],
        "title": title,
        "url": url,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": published_at,
        "summary": "",
        "tags": tags or [],
        "location": None,
        "deadline": None,
        "language": LANGUAGE,
        "category": categories[0],
        "categories": categories,
        "kind": kind,
        "host_countries": host_countries or [],
        "eligible_countries": [],
        "publisher_country": PUBLISHER_COUNTRY,
        "created_at": now,
        "updated_at": now,
        "first_seen_at": now,
        "last_seen_at": now,
        "last_checked_at": now,
        "status": "unknown",
        "classification": {
            "method": method,
            "status": "unknown" if categories == ["other"] else "classified",
            "evidence": evidence or [],
        },
    }


def validate_records(records, *, allow_empty=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not allow_empty):
        raise AdapterError("Empty or invalid opportunity collection",
                           "validate")
    ids = set()
    for record in records:
        if (not isinstance(record, dict) or record.get("source") != SOURCE_ID or
                record.get("source_url") != SOURCE_URL):
            raise AdapterError("Record source identity mismatch", "validate")
        for field in (
                "id",
                "title",
                "url",
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
        ):
            if (not isinstance(record.get(field), str) or
                    not record[field].strip()):
                raise AdapterError("Missing record field: " + field, "validate")
        if record["id"] in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"])
        url = urllib.parse.urlsplit(record["url"])
        if (url.scheme not in ("http", "https") or not url.hostname or
                url.username or url.password):
            raise AdapterError("Unsafe opportunity URL", "validate")
        for field in (
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
                "published_at",
                "deadline",
        ):
            value = record.get(field)
            if field not in record:
                raise AdapterError("Missing record field: " + field, "validate")
            if value is not None:
                try:
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        if field != "deadline":
                            raise ValueError()
                        datetime.strptime(value, "%Y-%m-%d")
                    elif (datetime.fromisoformat(value.replace("Z",
                                                               "+00:00")).tzinfo
                          is None):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError("Invalid record timestamp: " + field,
                                       "validate")
        for field in ("tags", "host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                    not isinstance(value, str) for value in values):
                raise AdapterError("Invalid record list: " + field, "validate")
            if field != "tags" and any(
                    not re.fullmatch("[A-Z]{2}", value) for value in values):
                raise AdapterError("Invalid country code", "validate")
        categories = record.get("categories")
        if (not isinstance(categories, list) or not categories or
                any(value not in CATEGORIES for value in categories) or
                len(set(categories)) != len(categories) or
                record.get("category") != categories[0]):
            raise AdapterError("Invalid opportunity categories", "validate")
        if record.get("kind") not in (
                "opportunity",
                "programme-overview",
                "institutional-grant",
                "unknown",
        ) or record.get("status") not in ("open", "expired", "unknown"):
            raise AdapterError("Invalid opportunity kind or status", "validate")
        summary = record.get("summary")
        if not isinstance(summary, str) or re.search("<[^>]+>", summary):
            raise AdapterError("Invalid plain summary", "validate")
        length = len(summary.encode("utf-16-le")) // 2
        if length > 600:
            raise AdapterError("Invalid plain summary", "validate")
        for field in ("location", "language", "publisher_country"):
            if (field not in record or record[field] is not None and
                    not isinstance(record[field], str)):
                raise AdapterError("Invalid record field: " + field, "validate")
        if "summary_language" in record and (not isinstance(
                record["summary_language"], str) or not re.fullmatch(
                    r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*",
                    record["summary_language"],
                )):
            raise AdapterError("Invalid summary language", "validate")
        classification = record.get("classification")
        if (not isinstance(classification, dict) or
                not isinstance(classification.get("method"), str) or
                classification.get("status") not in ("classified", "unknown") or
                not isinstance(classification.get("evidence"), list) or
                any(not isinstance(value, str)
                    for value in classification["evidence"])):
            raise AdapterError("Invalid classification evidence", "validate")

    validate_owned_records(records)


def record_identity(url, reference=None):
    """Keep original URL hashes and literal recruitment-reference identities."""
    identity = url + ("|" + reference if reference else "")
    return hashlib.sha256(
        (SOURCE_ID + "|" + identity).encode()).hexdigest()[:24]


def validate_owned_records(records):
    """Reject foreign identities and impossible owned record chronologies."""
    for record in records:
        public_url(record["url"])
        reference = record.get("reference")
        if record["url"] == SOURCE_URL + "praca-w-frse":
            references = re.findall(
                r"\bFRSE/\d+/\d{4}\b",
                reference or " ".join(record["classification"]["evidence"]),
            )
            reference = one(sorted(set(references)), "recruitment reference")
        elif reference is not None:
            raise AdapterError("Unexpected recruitment reference", "validate")
        if record["id"] != record_identity(record["url"], reference):
            raise AdapterError("Unowned FRSE record identity or URL",
                               "validate")
        values = {
            field: datetime.fromisoformat(record[field].replace("Z", "+00:00"))
            for field in (
                "created_at",
                "updated_at",
                "first_seen_at",
                "last_seen_at",
                "last_checked_at",
            )
        }
        if not (values["created_at"] <= values["updated_at"] <=
                values["last_checked_at"] and values["first_seen_at"] <=
                values["last_seen_at"] <= values["last_checked_at"]):
            raise AdapterError("Invalid owned FRSE chronology", "validate")
        if (record["status"] == "expired" and record["deadline"] is None and
                not (record.get("application_closed") is True or
                     (record["kind"] == "institutional-grant" and
                      any("institutional competition closed" in value
                          for value in record["classification"]["evidence"])))):
            raise AdapterError("Missing guarded FRSE call closure", "validate")


def github(method, path, payload=None, missing_ok=False):
    check_deadline()
    token = os.environ.get("DATA_SOURCE_TOKEN", "")
    if not token:
        raise AdapterError("Publication credentials missing", "publish")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    try:
        status, _, body = request_bytes(
            _GITHUB + path,
            method=method,
            headers=headers,
            payload=(json.dumps(payload).encode()
                     if payload is not None else None),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError("GitHub connection failed; outcome unconfirmed",
                           "publish") from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication HTTP {status}", "publish",
                           status)
    return json.loads(body)


def read_snapshot():
    head = github("GET", "/git/ref/heads/main")["object"]["sha"]
    tree = github("GET", "/git/commits/" + head)["tree"]["sha"]
    files = {}
    for name in ("data.json", "metadata.json"):
        file = github(
            "GET",
            f"/contents/datas/{SOURCE_ID}/{name}?ref={head}",
            missing_ok=True,
        )
        if file is not None and file.get("encoding") == "none":
            sha = file.get("sha", "")
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise AdapterError("Invalid existing blob identity", "publish")
            file = github("GET", "/git/blobs/" + sha)
            if file.get("sha") != sha:
                raise AdapterError("Existing blob identity mismatch", "publish")
        if file is not None and file.get("encoding") != "base64":
            raise AdapterError("Unsupported existing source file encoding",
                               "publish")
        files[name] = (None if file is None else base64.b64decode(
            file["content"]).decode("utf-8"))
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish")
    return {"head": head, "tree": tree, "files": files}


def publish_files(snapshot, files):
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
        check_deadline()
        candidate = None
        try:
            tree = github(
                "POST",
                "/git/trees",
                {
                    "base_tree":
                        snapshot["tree"],
                    "tree": [{
                        "path": f"datas/{SOURCE_ID}/{name}",
                        "mode": "100644",
                        "type": "blob",
                        "content": text,
                    } for name, text in files.items()],
                },
            )["sha"]
            candidate = github(
                "POST",
                "/git/commits",
                {
                    "message": f"Update {SOURCE_ID} collection outcome",
                    "tree": tree,
                    "parents": [snapshot["head"]],
                },
            )["sha"]
            github(
                "PATCH",
                "/git/refs/heads/main",
                {
                    "sha": candidate,
                    "force": False
                },
            )
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (latest["head"] == candidate or
                              latest["files"] == desired):
                return
            if latest["files"] != expected:
                raise AdapterError(
                    "Source changed concurrently; "
                    "newer remote data was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; "
                    "durable remote outcome could not be updated",
                    "publish",
                )


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    clean = urllib.parse.urlunsplit(parsed._replace(fragment=""))
    if ("https://" + parsed.netloc not in ROBOTS_ORIGINS or
            parsed.scheme != "https" or parsed.username or parsed.password or
            parsed.port or re.search(r"[\x00-\x20\\]", value) or
            any(part in parsed.path.lower().split("/")
                for part in ("admin", "login", "logout", "api"))):
        raise AdapterError("Unreviewed public source origin or route", "access")
    return clean


def policy_content(root, key):
    """Select the reviewed legal body and its applicable policy links."""
    selectors = {
        "etwinning-policy": ("section", "text_desc_box"),
        "erasmus-policy": ("section", "standard_text_page"),
        "eks-cookies": ("section", "text_page"),
        "selfie-cookies": ("section", "text_page"),
        "edu-rodo": ("section", "text_page"),
        "edu-cookies": ("section", "text_page"),
        "data-policy": ("div", "split_view"),
        "cookies": ("div", "split_view"),
    }
    if key not in selectors:
        raise AdapterError("Unreviewed policy selector", "access")
    main = one(nodes(root, "main"), "policy main")
    tag, name = selectors[key]
    content = one(nodes(main, tag, class_name=name), "policy body")
    if not content.text() or content.duplicate_attrs:
        raise AdapterError("Missing or ambiguous policy body", "access")
    return content


def policy_fingerprint(root, key):
    content = policy_content(root, key)
    facts = json.dumps(
        {
            "text":
                content.text(),
            "links":
                sorted({
                    node.attrs["href"]
                    for node in nodes(content, "a")
                    if node.attrs.get("href")
                }),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(facts.encode()).hexdigest()


def html_fingerprint(body, key=None):
    return policy_fingerprint(document_root(body.decode("utf-8", "strict")),
                              key)


def absolute_link(base, href):
    """Normalize public links without interpreting forms or fetching assets."""
    url = urllib.parse.urljoin(base, href)
    parts = urllib.parse.urlsplit(url)
    host = "www.frse.org.pl" if parts.netloc == "frse.org.pl" else parts.netloc
    scheme = "https" if "https://" + host in ROBOTS_ORIGINS else parts.scheme
    path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/:@-._~")
    path = re.sub(r"/{2,}", "/", path).rstrip("/") or "/"
    query = "" if parts.query.startswith("anchor=") else parts.query
    return urllib.parse.urlunsplit(
        parts._replace(path=path, netloc=host, scheme=scheme, query=query))


def content_blocks(root):
    """Keep complete paragraph/list boundaries, including late-page conditions."""
    result = []

    def visit(node):
        if node.tag in ("script", "style", "nav", "form", "footer"):
            return
        if node.tag in ("p", "li", "h1", "h2", "h3", "h4", "td"):
            text = node.text()
            if text:
                result.append(text)
            return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)

    visit(root)
    return result or ([root.text()] if root.text() else [])


def material_links(root, url):
    return sorted({
        absolute_link(url, node.attrs["href"])
        for node in nodes(root, "a")
        if node.attrs.get("href", "").strip() and urllib.parse.urlsplit(
            absolute_link(url, node.attrs["href"])).scheme in ("http", "https")
    })


def read_html(url, *, inventory=False):
    _, headers, body = fetch_public(url, inventory=inventory)
    if "html" not in headers.get("Content-Type", "").lower():
        raise AdapterError("Expected public HTML: " + url, "parse")
    return document_root(body.decode("utf-8", "strict"))


LISTINGS = {
    "finansowanie-projektow": "grants",
    "wydarzenia-i-szkolenia": "training",
    "konkursy": "competitions",
}
# These are identity aliases, never a store of programme facts.
ALIASES = {
    SOURCE_URL + "finansowanie-projektow/zmiana-ktora-pomagasz-tworzyc":
        SOURCE_URL + "finansowanie-projektow/zmiana-ktora-ty-tworzysz",
}
# Historical discrepancies are unresolved warnings, not replacement facts.
_GOVERNING_CONFLICTS = {
    "zagraniczne-wyjazdy-doskonalenia-kadry":
        "Course-duration limits differ from the previously reviewed programme guide.",
    "erasmus-mundus-design-measures":
        "The overview grant amount differs from the previously reviewed programme guide.",
    "dzialania-jean-monnet-debata-nt-polityk":
        "Network geography and consortium eligibility differ from the previously reviewed guide.",
    "erasmus-mundus-joint-masters":
        "The overview omits governing mobility/residence-country eligibility conditions.",
}
_FACT_PATTERNS = {
    "eligibility":
        r"dla kogo|wiek|pełnolet|wymag|uprawni|wyklucz|nie mog|nie może|skierowan|beneficjent|sympatyk|"
        r"co najmniej|minimum|muszą|musi|uczestni|nauczyciel|student|uczni",
    "funding":
        r"dofinans|stypend|nagrod|honorarium|\beuro\b|\bEUR\b|\bPLN\b|"
        r"\bzł\b|koszt|bezpłat",
    "application":
        r"prześlij|przesłać|zgłosz|zgłasz|wniosk|formularz|aplik|"
        r"rejestr|zapis|termin|\bCV\b|list motyw",
    "requirements":
        r"język|polsku|znaków|słowach|limitu|format|maksymaln|autorski|oryginaln|niepublikowan|nienagradz|jedną pracę|skan|zdjęcie|kategorii",
    "duration":
        r"(?<![\w@])\d{1,3}(?:[–-]\d{1,3})?\s+(?:dni|dzień|godzin\w*|miesi\w*|tygodni\w*|lat|sekund\w*|minut\w*)\b",
}
_CALL_WORDS = re.compile(r"konkurs|nab[oó]r|discovereu", re.I)
_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "września": 9,
    "października": 10,
    "listopada": 11,
    "grudnia": 12,
}
_DATE = re.compile(
    r"(?<!\d)(?:(?P<day>\d{1,2})\s+(?P<month>" + "|".join(_MONTHS) +
    r")\s+(?P<year>20\d{2})|(?P<d>\d{1,2})[./](?P<m>\d{1,2})"
    r"[./](?P<y>20\d{2}))(?!\d)",
    re.I,
)
_CLOSING = re.compile(
    r"(?:zgłosze\w*|wniosk\w*|aplikac\w*|zapis\w*|rejestrac\w*|"
    r"nab[oó]r|nadsyłani\w*|przesyłani\w*)[^.!?;]{0,100}?"
    r"(?:\bdo\b|upływa|kończy się|mija)|"
    r"(?:prześlij|przesłać|nadsyłać)[^.!?;]{0,100}?\bdo\b|"
    r"(?:prace będą przyjmowane|zdjęcie należy dodać)[^.!?;]{0,100}?\bdo\b|"
    r"uczestnicy zgłaszają swój udział.{0,150}?\bw terminie od.{0,60}?\bdo\b|"
    r"konkurs trwa(?:[^.!?;]|\br\.){0,80}?\bdo\b",
    re.I,
)


def closing_dates(blocks):
    """Use only explicit application closings with a complete source year."""
    dates = []
    for block in blocks:
        if re.search(
                r"czas trwania.*projekt|okres realizacji|okres trwałości",
                block,
                re.I,
        ):
            continue
        if re.search(r"planowan|przewidywan|orientacyjn|wstępny termin", block,
                     re.I):
            continue
        for closing in _CLOSING.finditer(block):
            tail = block[closing.end():]
            # A partial date cannot borrow a year from a later event or result.
            match = re.match(r"\s*(?:dnia\s+)?", tail)
            offset = match.end()
            date = _DATE.match(tail, offset)
            if date is None:
                continue
            try:
                if date["day"]:
                    value = datetime(
                        int(date["year"]),
                        _MONTHS[date["month"].lower()],
                        int(date["day"]),
                    )
                else:
                    value = datetime(int(date["y"]), int(date["m"]),
                                     int(date["d"]))
            except ValueError:
                continue
            evidence = block[closing.start():closing.end() + date.end()]
            dates.append({
                "date": value.date().isoformat(),
                "evidence": evidence
            })
    return [
        dict(pair) for pair in dict.fromkeys(tuple(d.items()) for d in dates)
    ]


def page_numbers(root):
    values = {1}
    for node in root.walk():
        for key in ("data-page", "data-pagescount"):
            value = node.attrs.get(key)
            if value is not None:
                if not re.fullmatch(r"[1-9]\d*", value) or int(value) > 100:
                    raise AdapterError(
                        "Invalid or excessive directory pagination")
                values.update(range(1, int(value) + 1))
    return values


def listing_url(slug, page):
    if page == 1:
        return SOURCE_URL + slug
    prefix = "s_" if slug == "wydarzenia-i-szkolenia" else ""
    params = {prefix + "filter_for_who": "0"}
    if prefix:
        params[prefix + "filter_terms"] = "0"
    params.update({
        prefix + "filter_string": "",
        prefix + "filter_send": "filtruj",
        "active_page": str(page),
    })
    return SOURCE_URL + slug + "?" + urllib.parse.urlencode(params)


def calendar_configuration(root):
    """Read the public widget's unfiltered defaults, without executing JS."""
    declarations = []
    for node in nodes(root, "script"):
        script = "".join(
            item for item in node.children if isinstance(item, str))
        declarations.extend(
            re.findall(r"new\s+PioroMyCalendar2\(([^;]+)\);", script))
    expression = one(declarations, "public calendar initialization")
    try:
        values = ast.literal_eval("[" + expression + "]")
    except (ValueError, SyntaxError):
        raise AdapterError("Unsupported calendar initialization",
                           "parse") from None
    if (not isinstance(values, list) or len(values) != 11 or
            values[0] != "/wydarzenia-i-szkolenia" or
            not isinstance(values[2], str) or
            not re.fullmatch(r"[A-Za-z0-9_-]+", values[2]) or
            values[3:7] != ["", "", "", ""] or values[7] != 1 or
            values[9:] != [0, 1] or not isinstance(values[8], list) or
            not values[8] or any(not isinstance(value, str) or
                                 not re.fullmatch(r"[a-z][a-z0-9_]*", value)
                                 for value in values[8]) or
            len(set(values[8])) != len(values[8])):
        raise AdapterError("Public calendar scope changed", "parse")
    key, classes = values[2], values[8]
    fields = []
    for name in classes:
        inputs = nodes(root, class_name=name)
        if not inputs:
            continue
        node = one(inputs, "calendar filter " + name)
        if node.duplicate_attrs:
            raise AdapterError("Ambiguous calendar filter", "parse")
        if node.tag == "select":
            options = nodes(node, "option")
            selected = [
                option for option in options if "selected" in option.attrs
            ]
            if not options or len(selected) > 1 or "multiple" in node.attrs:
                raise AdapterError("Unsupported calendar selection", "parse")
            option = (selected or options[:1])[0]
            if "value" not in option.attrs or option.duplicate_attrs:
                raise AdapterError("Missing calendar filter value", "parse")
            value = option.attrs["value"]
        elif node.tag in ("input", "button"):
            if node.attrs.get("type") in ("password", "file", "checkbox",
                                          "radio"):
                raise AdapterError("Unsupported public calendar filter",
                                   "parse")
            value = node.attrs.get("value", "")
        else:
            raise AdapterError("Unsupported public calendar input", "parse")
        fields.append(("s_" + name, value))
    if not fields:
        raise AdapterError("Public calendar filters are missing", "parse")
    fields.extend((name, "") for name in ("year", "month", "week", "day"))
    endpoint = SOURCE_URL + "pioro/mycalendar2/get-calendar-content/" + key
    return key, endpoint, fields


def calendar_session(headers):
    """Retain only anonymous cookies issued by the public FRSE entry page."""
    values = {}
    for header in headers.get_all("Set-Cookie", []):
        cookies = SimpleCookie()
        try:
            cookies.load(header)
        except Exception:
            raise AdapterError("Invalid anonymous calendar session",
                               "fetch") from None
        for name, cookie in cookies.items():
            if name not in {"PHPSESSID", "front_front2_lang", "frse_session"}:
                continue
            if (name in values or cookie["path"] != "/" or
                    cookie["domain"] not in ("", "www.frse.org.pl",
                                             ".frse.org.pl", "frse.org.pl") or
                    not cookie.value or re.search(r"[\r\n;]", cookie.value)):
                raise AdapterError("Unsafe anonymous calendar cookie", "access")
            if cookie["expires"]:
                try:
                    expires = parsedate_to_datetime(
                        cookie["expires"]).timestamp()
                except (ValueError, TypeError, OverflowError):
                    raise AdapterError("Invalid calendar cookie expiry",
                                       "fetch") from None
                if expires <= time.time():
                    raise AdapterError("Anonymous calendar session expired",
                                       "fetch")
            values[name] = cookie.coded_value
    if not {"PHPSESSID", "frse_session"} <= set(values):
        raise AdapterError("Public calendar session was not established",
                           "fetch")
    return "; ".join(name + "=" + values[name] for name in sorted(values))


def calendar_links(root):
    """Extract every event card's canonical public detail identity."""
    links = set()
    for article in nodes(root, "article"):
        candidates = set()
        for node in nodes(article, "a"):
            href = node.attrs.get("href")
            if not href:
                continue
            link = absolute_link(SOURCE_URL, href)
            parsed = urllib.parse.urlsplit(link)
            if parsed.netloc == "www.frse.org.pl" and parsed.path.startswith(
                    "/wydarzenia-i-szkolenia/"):
                candidates.add(public_url(link))
        links.add(one(sorted(candidates), "calendar card detail URL"))
    if not links:
        raise AdapterError("Empty public calendar inventory", "parse")
    return links


def calendar_page(value, number):
    """Bind active page, full advertised frontier and exact filter context."""
    if (not isinstance(value, dict) or
            not {"params", "ajax_box", "ajax_box_pagination", "views"
                } <= set(value) or not isinstance(value["params"], dict) or
            set(value["params"]) != {"year", "month", "week", "day"} or
            any(item is not None and
                (not isinstance(item, str) or not re.fullmatch(r"\d*", item))
                for item in value["params"].values()) or
            not isinstance(value["ajax_box"], str) or
            not isinstance(value["ajax_box_pagination"], str)):
        raise AdapterError("Invalid public calendar response", "parse")
    root = document_root(value["ajax_box"], complete=False)
    listing = one(nodes(root, class_name="events_list"), "calendar page list")
    links = calendar_links(listing)
    pager = document_root(value["ajax_box_pagination"], complete=False)
    controls = nodes(pager, class_name="js_ajax_box_page_link")
    pages, active = [], []
    for node in controls:
        text = node.attrs.get("data-page", "")
        if (not re.fullmatch(r"[1-9]\d*", text) or node.text() != text or
                node.duplicate_attrs):
            raise AdapterError("Invalid public calendar page identity", "parse")
        page = int(text)
        pages.append(page)
        if "active" in node.attrs.get("class", "").split():
            active.append(page)
    if (active != [number] or sorted(pages) != list(range(1, len(pages) + 1))):
        raise AdapterError("Incomplete or mismatched calendar pager", "parse")
    return links, set(pages), value["params"]


def read_calendar():
    """Reconcile one explicit filter universe plus the static programmes."""
    entry = SOURCE_URL + "wydarzenia-i-szkolenia"
    _, headers, body = fetch_public(entry, inventory=True)
    if "html" not in headers.get("Content-Type", "").lower():
        raise AdapterError("Expected public calendar HTML", "parse")
    root = document_root(body.decode("utf-8", "strict"))
    key, endpoint, fields = calendar_configuration(root)
    main = one(nodes(root, "main"), "calendar directory main")
    dynamic = one(
        nodes(main, class_name="pioromycalendar2_js_ajax_box_" + key),
        "dynamic calendar entry",
    )
    dynamic_nodes = set(dynamic.walk())
    static = one(
        [
            node for node in nodes(main, class_name="events_list")
            if node not in dynamic_nodes
        ],
        "static event programmes",
    )
    links = calendar_links(static)
    request_headers = {
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": calendar_session(headers),
    }
    expected_pages = expected_params = None
    pending, visited, signatures = {1}, set(), set()
    while pending:
        number = min(pending)
        pending.remove(number)
        query = fields + [("active_page", str(number)), ("by", "month")]
        url = endpoint + "?" + "&".join(
            name + "=" + urllib.parse.quote(value, safe="~()*!.'")
            for name, value in query)
        _, _, payload = fetch_public(url,
                                     inventory=True,
                                     headers=request_headers)
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError):
            raise AdapterError("Invalid public calendar JSON",
                               "parse") from None
        fresh, advertised, params = calendar_page(value, number)
        if expected_pages is None:
            expected_pages, expected_params = advertised, params
        elif advertised != expected_pages or params != expected_params:
            raise AdapterError(
                "Public calendar inventory changed during paging", "parse")
        signature = tuple(sorted(fresh))
        if signature in signatures:
            raise AdapterError("Repeated canonical calendar page", "parse")
        signatures.add(signature)
        visited.add(number)
        links.update(fresh)
        pending.update(advertised - visited)
    if visited != expected_pages:
        raise AdapterError("Incomplete public calendar inventory", "parse")
    print(
        f"wydarzenia-i-szkolenia: exhausted {len(visited)} canonical pages "
        f"and reconciled {len(links)} event/programme URLs",
        file=sys.stderr,
    )
    return root, links


def read_pages():
    """Exhaust advertised catalogue pages and discovered current call details."""
    pages, candidates, catalogues, supporting = {}, {}, [], {}

    def read(url, *, inventory=False):
        url = public_url(url)
        if url not in pages:
            pages[url] = read_html(url, inventory=inventory)
        return pages[url]

    # Policy review remains separate from changing opportunity facts.
    for key, info in ACCESS_POLICIES.items():
        _, _, body = fetch_public(info["url"])
        if html_fingerprint(body, key) != info["sha256"]:
            raise AdapterError("Publisher access policy changed: " + key,
                               "access")
        pages[info["url"]] = document_root(body.decode("utf-8", "strict"))

    for slug, category in LISTINGS.items():
        if slug == "wydarzenia-i-szkolenia":
            root, links = read_calendar()
            pages[SOURCE_URL + slug] = root
            for link in sorted(links):
                candidates.setdefault(link, category)
            continue
        pending, seen, signatures = {1}, set(), set()
        while pending:
            page = min(pending)
            pending.remove(page)
            url = listing_url(slug, page)
            root = fetch_inventory_page(read, url)
            pending.update(page_numbers(root) - seen - {page})
            seen.add(page)
            main = one(nodes(root, "main"), "directory main")
            if slug == "konkursy":
                cards = nodes(main, class_name="desc_holder")
                if not cards:
                    raise AdapterError("Missing contest catalogue cards")
                signature = tuple(card.text() for card in cards)
                for card in cards:
                    title = one(nodes(card, "h2"), "contest title").text()
                    links = material_links(card, url)
                    catalogues.append((title, links, card))
                    for link in links:
                        if (urllib.parse.urlsplit(link).netloc in {
                                "www.frse.org.pl",
                                "erasmusplus.org.pl",
                                "eks.org.pl",
                                "etwinning.pl",
                                "selfieplus.frse.org.pl",
                                "eduinspiracje.org.pl",
                        } and not link.lower().endswith(".pdf") and
                                not re.search(
                                    r"laurea|/aktualnosci/",
                                    urllib.parse.urlsplit(link).path,
                                )):
                            candidates.setdefault(public_url(link), category)
            else:
                links = {
                    absolute_link(url, node.attrs["href"])
                    for node in nodes(main, "a")
                    if node.attrs.get("href", "").startswith("/" + slug + "/")
                }
                if not links:
                    raise AdapterError("Empty catalogue page: " + url)
                signature = tuple(sorted(links))
                for link in sorted(links):
                    candidates.setdefault(link, category)
            if signature in signatures:
                raise AdapterError("Repeated directory page: " + url)
            signatures.add(signature)
        print(f"{slug}: exhausted {len(seen)} advertised pages",
              file=sys.stderr)

    candidates[SOURCE_URL + "praca-w-frse"] = "jobs"
    # News recommendations expose current rounds omitted by evergreen catalogues.
    homepage = fetch_inventory_page(read, SOURCE_URL)
    for node in nodes(homepage, "a"):
        href = node.attrs.get("href", "")
        if href.startswith("/aktualnosci/") and _CALL_WORDS.search(node.text()):
            candidates.setdefault(absolute_link(SOURCE_URL, href),
                                  "competitions")

    pending = list(candidates)
    visited = set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        if len(visited) >= 180:
            raise AdapterError(
                "Discovered detail inventory exceeds collection bound")
        visited.add(url)
        root = read(url)
        main = one(nodes(root, "main"), "detail main")
        for node in nodes(root, "a"):
            href = node.attrs.get("href", "")
            if not href:
                continue
            link = absolute_link(url, href)
            parsed = urllib.parse.urlsplit(link)
            if "https://" + parsed.netloc not in ROBOTS_ORIGINS:
                continue
            if parsed.path.lower().endswith(".pdf"):
                continue
            related = (parsed.netloc == "www.frse.org.pl" and
                       parsed.path.startswith("/aktualnosci/") and
                       _CALL_WORDS.search(node.text()))
            rules = (re.match(
                r"^(?:szczegółowe zasady|regulamin|zasady konkursu|"
                r"zobacz regulamin)",
                node.text(),
                re.I,
            ) and node in nodes(main, "a") or re.search(
                r"/konkurs-\d{4}$|/obwieszczenie-o-konkursie$", parsed.path) or
                     parsed.fragment and _CALL_WORDS.search(node.text()))
            if not (related or rules):
                continue
            clean = public_url(link)
            if rules:
                supporting.setdefault(url, set()).add(clean)
            if clean not in candidates:
                candidates[clean] = candidates[url]
                pending.append(clean)
    fetch_inventory_page(read, "https://erasmusplus.org.pl/dokumenty")
    return pages, candidates, catalogues, supporting


def detail_content(root):
    """Exclude navigation, related cards and decorative image captions."""
    for name in ("financing_details", "events_details", "news_details"):
        found = nodes(root, class_name=name)
        if found:
            return one(found, name)
    main = one(nodes(root, "main"), "detail main")
    result = Node("article")
    for section in nodes(main, "section"):
        if set(section.attrs.get("class", "").split()) & {
                "section_box",
                "text_page",
                "white_content",
                "details_more",
                "standard_text_page",
                "text_desc_box",
                "about",
                "download",
        }:
            result.children.append(section)
    if not result.children:
        raise AdapterError("Unrecognized substantive page layout")
    return result


def factual_extracts(blocks):
    """Select short attributed factual clauses, not an article or rulebook."""
    facts = {key: [] for key in _FACT_PATTERNS}
    for block in blocks:
        if re.match(r"Załącznik nr", block, re.I):
            break
        block = re.sub(r"^Dla kogo\?\s*", "Dla kogo: ", block, flags=re.I)
        for clause in re.split(r";|(?<=[.!?])\s+(?=[A-ZĄĆĘŁŃÓŚŹŻ])", block):
            for label, pattern in _FACT_PATTERNS.items():
                if label == "duration" and not re.search(
                        r"trwa|długo|okres|maks|minimum|dni|godzin|miesi|tygodni|sekund|minut",
                        clause,
                        re.I,
                ):
                    continue
                covered = -1
                for match in re.finditer(pattern, clause, re.I):
                    if match.end() <= covered:
                        continue
                    start = max(0, match.start() - 70)
                    end = min(len(clause), max(match.end(), start + 230))
                    excerpt = clause[start:end]
                    if start:
                        excerpt = "[...] " + excerpt.split(" ", 1)[-1]
                    if end < len(clause):
                        excerpt = excerpt.rsplit(" ", 1)[0] + " [...]"
                    covered = start + len(
                        excerpt.removeprefix("[...] ").removesuffix(" [...]"))
                    if excerpt and excerpt not in facts[label]:
                        facts[label].append(excerpt)
    return {key: values for key, values in facts.items() if values}


def factual_material(content, url):
    blocks = content_blocks(content)
    links = material_links(content, url)
    material = json.dumps(
        {
            "text": content.text(),
            "links": links
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return {
        "facts": factual_extracts(blocks),
        "links": links,
        "material_sha256": hashlib.sha256(material.encode()).hexdigest(),
    }


def record_from_content(url, title, content, category, reference=None):
    fields = []
    for group in nodes(content, class_name="details_list"):
        labels = nodes(group, class_name="list_left")
        values = nodes(group, class_name="list_right")
        if len(labels) != len(values):
            raise AdapterError("Unpaired programme fields: " + url)
        fields.extend({
            "label": label.text(),
            "text": value.text()
        } for label, value in zip(labels, values))
    blocks = content_blocks(content)
    links = material_links(content, url)
    text = " ".join(blocks)
    categories = [category]
    kind = "opportunity"
    if "/finansowanie-projektow/" in url:
        kind = "institutional-grant"
        if re.search(r"studiuj", title, re.I):
            categories, kind = [
                "scholarships",
                "internships",
            ], "programme-overview"
        elif re.search(r"uczniowie.*praktyk", title, re.I):
            categories, kind = ["internships"], "programme-overview"
        elif re.search(r"wyjedź prowadzić|doskonalenia kadry", title, re.I):
            categories, kind = ["training"], "programme-overview"
        elif re.search(r"zostań wolontariuszem", title, re.I):
            categories, kind = ["volunteering"], "programme-overview"
    if re.search(r"worldskills|euroskills", title, re.I):
        categories, kind = ["competitions"], "programme-overview"
    overview = None
    if category == "training":
        overview = re.search(
            r"[^.!?]*(?:coroczn\w*|co roku|katalog kursów|archiwum nagrań webinariów|"
            r"wg harmonogramu|tegorocznej edycji|edycji Tool Fair w roku \d{4}|"
            r"wszystkie lekcje)[^.!?]*",
            text,
            re.I,
        )
        if overview:
            kind = "programme-overview"
    record = make_record(
        title,
        url,
        categories,
        kind=kind,
        method="publisher-html",
        evidence=[ATTRIBUTION, url],
    )
    record["id"] = record_identity(url, reference)
    if reference:
        record["reference"] = reference
    record["country"] = PUBLISHER_COUNTRY
    if overview:
        record["classification"]["evidence"].append(overview[0].strip())
    record["details"] = factual_material(content, url)
    record["details"]["fields"] = [{
        "label": item["label"],
        "facts": factual_extracts([item["label"] + " " + item["text"]]),
    } for item in fields]
    record["limitations"] = [
        "HTML-reported conditions; linked programme rules can supersede overview text.",
        "PDF-only eligibility, funding and deadlines are not verified by this adapter.",
        "Residence and institutional affiliation are not nationality eligibility.",
    ]
    closings = closing_dates(
        blocks + [item["label"] + " " + item["text"] for item in fields])
    record["application_dates"] = closings
    unique = sorted({entry["date"] for entry in closings})
    if len(unique) == 1 and kind != "programme-overview":
        record["deadline"] = unique[0]
    term = " ".join(
        n.text() for n in nodes(content, class_name="term_box_content"))
    closure = bool(
        re.search(
            r"(?:nab[oó]r|konkurs|przyjmowanie zgłoszeń)(?:\s+w konkursie)?"
            r"(?:\s+\d{4})?\s+"
            r"(?:(?:jest|został|zostało)\s+)?(?:już\s+)?"
            r"(?:zakończon[yae]|zamknięt[yae]|zakończył się)\b",
            term or text,
            re.I,
        ))
    record["application_closed"] = closure and kind != "programme-overview"
    if record["application_closed"]:
        record["status"] = "expired"
    elif record["deadline"]:
        today = datetime.now(timezone.utc).date().isoformat()
        if record["deadline"] < today:
            record["status"] = "expired"
        elif (record["deadline"] > today and re.search(
                r"zapraszamy|prześlij|należy przesłać|prosimy o przesłanie|"
                r"przyjmujemy|trwa nabór|zgłoszenia do|na zgłoszenia czekamy",
                text,
                re.I,
        ) and not re.search(r"nabór (?:rozpocznie|zacznie) się", text, re.I)):
            record["status"] = "open"
    locations = [
        item["text"] for item in fields if item["label"].startswith("Gdzie")
    ]
    if len(locations) == 1 and locations[0].lower() not in (
            "online",
            "internet",
    ):
        record["location"] = locations[0]
    conflict = _GOVERNING_CONFLICTS.get(
        urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1])
    if conflict:
        record["source_conflicts"] = [{
            "status": "unresolved",
            "issue": conflict,
            "affected_fields": ["eligibility", "funding", "duration"],
            "governing_documents": [],
        }]
        record["details"][
            "verification"] = "conflicting-overview-not-governing-conditions"
        record["limitations"].append(
            conflict +
            " Current PDF guide not parsed; do not rely on overview figures.")
    if re.search(r"worldskills|euroskills", title, re.I):
        record["limitations"].append(
            "FRSE programme overview only. Current WorldSkills-specific rules, "
            "eligibility, costs and recruitment dates have not been verified; "
            "the dedicated external origin is not accessed.")
    timing = ("Termin zgłoszeń: " + record["deadline"] + ". " if
              record["deadline"] else "Brak jednoznacznego terminu zgłoszeń. ")
    record["summary"] = ("FRSE; " + categories[0] + ". " + timing + (
        "Warunki strony są sprzeczne lub niepełne względem przewodnika; wymagają weryfikacji."
        if conflict else
        "Warunki udziału i szczegóły: oficjalna strona organizatora."))
    return record


def anchored_offers(content):
    """Split named call headings across the CMS's nested modular wrappers."""
    offers = []
    active = None

    def visit(node):
        nonlocal active
        if (node.tag == "section" and node.attrs.get("id") and
                nodes(node, "h2")):
            active = (
                node.attrs["id"],
                one(nodes(node, "h2"), "suboffer title").text(),
                Node("article"),
            )
            offers.append(active)
            active[2].children.append(node)
            return
        if node.tag in ("p", "li", "h1", "h2", "h3", "h4", "td", "a"):
            if active is not None:
                active[2].children.append(node)
            return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)

    visit(content)
    return offers


def parse_inventory(pages):
    documents, candidates, catalogues, supporting = pages
    records = {}
    excluded = []
    attachments = set().union(*supporting.values()) if supporting else set()
    primary_rules = "https://eduinspiracje.org.pl/eduinspirator/regulamin"
    support_roots = {
        "https://selfieplus.frse.org.pl/",
        "https://selfieplus.frse.org.pl/o-selfie",
        "https://eduinspiracje.org.pl/eduinspirator/o-konkursie",
        "https://etwinning.pl/konkursy/konkursy-polskie",
        SOURCE_URL + "wydawnictwo",
        SOURCE_URL + "wydawnictwo/regulamin",
    }
    for url in sorted(candidates, key=lambda value: (value in ALIASES, value)):
        category = candidates[url]
        root = documents[url]
        if url in support_roots or (
                url in attachments and url != primary_rules and
            ("regulamin" in urllib.parse.urlsplit(url).path or
             "lista-plikow" in url)):
            excluded.append(
                (url, "supporting catalogue or rules, not another call"))
            continue
        if category == "jobs":
            boxes = nodes(root, class_name="module_faq_box")
            references = set()
            for box in boxes:
                found = sorted(
                    set(re.findall(r"\bFRSE/\d+/\d{4}\b", box.text())))
                if not found:
                    continue
                reference = one(found, "job reference")
                if reference in references:
                    raise AdapterError("Repeated job reference")
                references.add(reference)
                title_nodes = nodes(box, class_name="title")
                title = one(title_nodes, "job title").text()
                record = record_from_content(url, title, box, category,
                                             reference)
                records[record["id"]] = record
            if not references:
                raise AdapterError("No current recruitment references")
            continue
        content = detail_content(root)
        text = content.text()
        if re.search(
                r"nie ma możliwości samodzielnego zgłoszenia|"
                r"nie wymaga samodzielnego zgłoszenia|"
                r"nie muszą ubiegać się o nagrodę|"
                r"do udziału w konkursie są zapraszane osoby wytypowane|"
                r"pierwszym etapem jest przyznanie przez nas nominacji",
                text,
                re.I,
        ):
            excluded.append((url, "publisher explicitly requires nomination"))
            continue
        specialized = any(
            nodes(root, class_name=name)
            for name in ("financing_details", "events_details", "news_details"))
        titles = nodes(content, "h2") if specialized else nodes(root, "h1")
        if url == primary_rules:
            titles = nodes(content, "h2")
        if not titles:
            raise AdapterError("Missing opportunity title: " + url)
        title = titles[0].text()
        if not title:
            raise AdapterError("Empty opportunity title: " + url)
        if re.search(
                r"bieg erasmusa|gala nagród|erasmus\s*days|"
                r"dzień autorów|europejski tydzień młodzieży",
                title,
                re.I,
        ):
            excluded.append((url, "celebration, ceremony or charity activity"))
            continue
        offers = anchored_offers(content)
        if offers:
            for anchor, offer_title, offer in offers:
                record = record_from_content(url + "#" + anchor, offer_title,
                                             offer, category)
                records[record["id"]] = record
            continue
        # A call announcement linking individually named sections is a parent,
        # not an additional fifth competition.
        links = material_links(content, url)
        if any(
                urllib.parse.urlsplit(link).fragment and
                public_url(link) in documents and
                anchored_offers(detail_content(documents[public_url(link)]))
                for link in links
                if "https://" +
                urllib.parse.urlsplit(link).netloc in ROBOTS_ORIGINS):
            excluded.append(
                (url, "parent of separately collected anchored calls"))
            continue
        canonical = ALIASES.get(url, url)
        if url.endswith("/wydawnictwo/obwieszczenie-o-konkursie"):
            category = "grants"
            title = one(
                [
                    label for label, targets, _ in catalogues
                    if any("/wydawnictwo" in target for target in targets)
                ],
                "publishing call catalogue title",
            )
        record = record_from_content(canonical, title, content, category)
        if record["id"] in records:
            prior = records[record["id"]]
            prior["details"].setdefault("aliases", []).append({
                "url": url,
                "details": record["details"]
            })
        else:
            records[record["id"]] = record
    for record in records.values():
        url = record["url"].split("#", 1)[0]
        linked = set(supporting.get(url, set())) - {url}
        # These mappings attach evergreen context to the current discovered
        # round while retaining the already published canonical identities.
        if urllib.parse.urlsplit(url).netloc == "selfieplus.frse.org.pl":
            linked.add("https://selfieplus.frse.org.pl/o-selfie")
        elif url == primary_rules:
            linked.add("https://eduinspiracje.org.pl/eduinspirator/o-konkursie")
        elif "/wydawnictwo/obwieszczenie-o-konkursie" in url:
            linked.add(SOURCE_URL + "wydawnictwo/regulamin")
        elif "nasz-projekt-etwinning" in url:
            linked.add("https://etwinning.pl/konkursy/konkursy-polskie")
        record["details"]["supporting"] = [{
            "url": link,
            **factual_material(detail_content(documents[link]), link),
        } for link in sorted(linked) if link in documents]
        if record.get("source_conflicts"):
            index = "https://erasmusplus.org.pl/dokumenty"
            guides = [
                link for link in material_links(documents[index], index)
                if re.search(r"programme-guide-20\d{2}.*\.pdf$", link, re.I)
            ]
            if not guides:
                raise AdapterError(
                    "Current governing programme-guide links missing")
            year = max(
                re.search(r"programme-guide-(20\d{2})", link, re.I)[1]
                for link in guides)
            current = [
                link for link in guides
                if "programme-guide-" + year in link.lower()
            ]
            record["source_conflicts"][0]["governing_documents"] = [
                index
            ] + sorted(current)
            record["classification"]["evidence"].extend([index] +
                                                        sorted(current))
    result = list(records.values())
    validate_records(result)
    print(
        json.dumps(
            {
                "detail_pages": len(candidates),
                "records": len(result),
                "excluded": excluded,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return result


def collect():
    if _PHASE_END is not None:
        check_deadline()
        return parse_inventory(read_pages())
    with execution():
        with phase("prepublication", COLLECTION_TIMEOUT):
            attempt = utc_now()
            prepare_collection()
            records = parse_inventory(read_pages())
            fresh_records(records, [], attempt, utc_now())
            return records


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "display_name": "FRSE",
        "source": SOURCE_ID,
        "name": "FRSE — funding, training, jobs and awards",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "attribution": ATTRIBUTION,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at":
            (previous.get("last_success_at") if error else attempt),
        "last_checked_at":
            (previous.get("last_checked_at") if error else checked_at),
        "record_count": len(records),
        "message":
            ("Collection failed; last-good data preserved" if error else
             f"Validated refresh retains {len(records)} FRSE opportunities"),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


def validate_prior_metadata(previous, records):
    """Accept failure writes only after the complete owned last-good pair."""
    if (not isinstance(previous, dict) or not {
            "source",
            "name",
            "source_url",
            "website_url",
            "language",
            "publisher_country",
            "publisher_type",
            "attribution",
            "status",
            "last_attempt_at",
            "last_success_at",
            "last_checked_at",
            "record_count",
            "message",
            "error",
    } <= previous.keys() or previous.get("name")
            != "FRSE — funding, training, jobs and awards" or
            previous.get("publisher_type") != PUBLISHER_TYPE or
            previous.get("attribution") != ATTRIBUTION or
            previous.get("source") != SOURCE_ID or
            previous.get("source_url") != SOURCE_URL or
            previous.get("website_url") != WEBSITE_URL or
            not isinstance(previous.get("message"), str) or
            previous.get("status") not in {"success", "fail"} or
            type(previous.get("record_count")) is not int or
            previous["record_count"] != len(records) or
            previous.get("language") != LANGUAGE or
            previous.get("publisher_country") != PUBLISHER_COUNTRY or
        (previous.get("status") == "success" and
         previous.get("error") is not None) or
        (previous.get("status") == "fail" and
         (not isinstance(previous.get("error"), str) or
          not previous["error"].strip() or
          not isinstance(previous.get("failure_stage"), str) or
          not previous["failure_stage"].strip()))):
        raise AdapterError("Invalid previous source metadata contract",
                           "publish")
    timestamps = {}
    try:
        for field in ("last_attempt_at", "last_success_at", "last_checked_at"):
            value = previous.get(field)
            if not isinstance(value, str):
                raise ValueError()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError()
            timestamps[field] = parsed
        if timestamps["last_success_at"] > timestamps["last_checked_at"]:
            raise ValueError()
        if previous["status"] == "success":
            if timestamps["last_attempt_at"] != timestamps["last_success_at"]:
                raise ValueError()
        elif timestamps["last_attempt_at"] < timestamps["last_checked_at"]:
            raise ValueError()
        for record in records:
            parsed = datetime.fromisoformat(record["last_checked_at"].replace(
                "Z", "+00:00"))
            if (parsed.tzinfo is None or
                    parsed != timestamps["last_checked_at"]):
                raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AdapterError("Invalid previous metadata timestamps",
                           "publish") from None


def phase_error(name):
    return AdapterError(
        "Whole " + name + " phase deadline exceeded",
        "publish" if name == "publication" else "access",
    )


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def fresh_records(records, old, attempt, checked):
    prior = {record["id"]: record for record in old}
    temporal = {
        "created_at",
        "updated_at",
        "first_seen_at",
        "last_seen_at",
        "last_checked_at",
    }
    for record in records:
        before = prior.get(record["id"], {})
        same = {
            k: v for k, v in before.items() if k not in temporal
        } == {
            k: v for k, v in record.items() if k not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", attempt) if same else attempt,
            last_seen_at=attempt,
            last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records, allow_empty=True)


def remove_expired_records(records, checked):
    """Filter the validated replacement, leaving prior publication untouched."""
    validate_records(records)
    now = datetime.fromisoformat(checked.replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise AdapterError("Cleanup clock requires a timezone", "validate")
    current = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if isinstance(deadline, str):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                # Without a documented zone, wait for worldwide day closure.
                expired = (datetime.strptime(deadline, "%Y-%m-%d").date()
                           < now.astimezone(timezone(
                               timedelta(hours=-12))).date())
            else:
                instant = datetime.fromisoformat(deadline.replace(
                    "Z", "+00:00"))
                expired = instant.tzinfo is not None and instant < now
        if not expired:
            current.append(record)
    # Valid nonempty retrieval may have no remaining eligible records.
    validate_records(current, allow_empty=True)
    return current


def run_body(publishing):
    attempt = utc_now()
    snapshot = None
    old, previous, records, desired = [], {}, [], None
    failure = None
    try:
        with phase("prepublication", COLLECTION_TIMEOUT):
            prepare_collection()
            if publishing:
                candidate = read_snapshot()
                if candidate["files"]["data.json"] is not None:
                    candidate_records = json.loads(
                        candidate["files"]["data.json"])
                    candidate_metadata = json.loads(
                        candidate["files"]["metadata.json"])
                    validate_records(candidate_records, allow_empty=True)
                    validate_prior_metadata(candidate_metadata,
                                            candidate_records)
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            records = remove_expired_records(records, checked)
            fresh_records(records, old, attempt, checked)
            outcome = metadata(attempt, previous, records, checked_at=checked)
            desired = {
                "data.json": serialize(records),
                "metadata.json": serialize(outcome),
            }
    except PhaseExpired:
        failure = phase_error("prepublication")
    except Exception as error:
        failure = error
    if failure is None and publishing:
        try:
            with phase("publication", 180):
                publish_files(snapshot, desired)
        except PhaseExpired:
            failure = phase_error("publication")
        except Exception as error:
            failure = error
    if failure is not None:
        outcome = metadata(attempt, previous, old, failure)
        if publishing:
            try:
                with phase("failure", 180):
                    if snapshot is None:
                        raise AdapterError("No validated previous source pair",
                                           "publish")
                    latest = read_snapshot()
                    if desired is not None and latest["files"] == desired:
                        outcome = json.loads(desired["metadata.json"])
                        failure = None
                    elif (snapshot["files"]["data.json"] is not None and
                          latest["files"] == snapshot["files"]):
                        publish_files(
                            latest,
                            {
                                "metadata.json": serialize(outcome),
                            },
                        )
                    else:
                        raise AdapterError("No safe unchanged last-good pair",
                                           "publish")
            except (Exception, PhaseExpired):
                outcome["message"] = (
                    "Run failed; durable outcome unconfirmed. "
                    "No newer source pair was overwritten.")
        elif not old:
            outcome["message"] = "Collection failed; no publication attempted"
    arm_alarm(_RUN_END - EXPORT_TIMEOUT)
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure is not None else 0


def run_lifecycle(publishing):
    result = None
    try:
        result = run_body(publishing)
    except PhaseExpired:
        result = 1
    finally:
        try:
            with phase("export", EXPORT_TIMEOUT):
                export_family_artifact()
        except (Exception, PhaseExpired):
            try:
                arm_alarm(_RUN_END)
                print(
                    "Timing artifact export failed; next run fail closed",
                    file=sys.stderr,
                )
            except (Exception, PhaseExpired):
                pass
            if result == 0:
                result = 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        with execution():
            return run_lifecycle(args.publish)
    except PhaseExpired:
        return 1


ROBOTS_ORIGINS = [
    "https://eduinspiracje.org.pl",
    "https://eks.org.pl",
    "https://erasmusplus.org.pl",
    "https://etwinning.pl",
    "https://selfieplus.frse.org.pl",
    "https://www.frse.org.pl",
]

ACCESS_POLICIES = {
    "etwinning-policy": {
        "url":
            "https://etwinning.pl/klauzula-rodo",
        "format":
            "html",
        "sha256":
            "ad1d077225592a200ef0c4b027c2c0c8f6419a1676a41b32e6e98c82d39511c7",
    },
    "erasmus-policy": {
        "url":
            "https://erasmusplus.org.pl/ochrona-danych-osobowych-w-programie-erasmus-2021-2027",
        "format":
            "html",
        "sha256":
            "075762ac2bd97b3e05bab61400399fdbb68c7ebf2598adb79085c9103d28e826",
    },
    "eks-cookies": {
        "url":
            "https://eks.org.pl/pliki-cookies",
        "format":
            "html",
        "sha256":
            "1b254c885f5486b4ed19afb6403df320592e9da80acaffcf676b743e58083af9",
    },
    "selfie-cookies": {
        "url":
            "https://selfieplus.frse.org.pl/pliki-cookies",
        "format":
            "html",
        "sha256":
            "fbfbec993f5ada3723a9948c8b8e43a107bb384b3f8cfec1c041cad3f9a09f6f",
    },
    "edu-rodo": {
        "url":
            "https://eduinspiracje.org.pl/rodo",
        "format":
            "html",
        "sha256":
            "65e51c255257535831d2cc2b157a0d7b4584eb2f5caca28290969c16081ab3da",
    },
    "edu-cookies": {
        "url":
            "https://eduinspiracje.org.pl/pliki-cookies",
        "format":
            "html",
        "sha256":
            "1e46a71bc622c675ff41d6c2e8bbb1aa092770dee96b2c0920ebfb3cd1b7865b",
    },
    "data-policy": {
        "url":
            "https://www.frse.org.pl/rodo",
        "format":
            "html",
        "sha256":
            "801a0f900ef0a76a0be5ea8209ba0b3ba6582c567075a9f76ac79dd3bc7203e2",
    },
    "cookies": {
        "url":
            "https://www.frse.org.pl/pliki-cookies",
        "format":
            "html",
        "sha256":
            "1d9fecbed93387fac66917822db032ea4d54d5e0c3b206c3db7f2a7a4b21e225",
    },
}


def validate_legacy_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) - {
            "interval",
            "page_interval",
            "rate_failures",
            "legacy_rate_history_unknown",
    } != {
            "schema",
            "family",
            "observed_at",
            "not_before",
            "starts",
            "blocked",
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
    if (type(state["schema"]) is not int or state["schema"] != _STATE_SCHEMA or
            state["family"] != FAMILY or not numeric(state["observed_at"]) or
            not numeric(state["not_before"]) or
            type(state["blocked"]) is not bool or
            not numeric(state.get("interval", 6)) or
            state.get("interval", 6) < 6 or
            not isinstance(state["starts"], list) or
            len(state["starts"]) > 10 or
            any(not numeric(t) for t in state["starts"]) or
            state["starts"] != sorted(state["starts"]) or
            len(set(state["starts"])) != len(state["starts"]) or
            any(t > state["observed_at"] for t in state["starts"])):
        raise AdapterError("Invalid publisher pacing history", "access")
    if (type(state.get("legacy_rate_history_unknown", True)) is not bool or
            type(state.get("rate_failures", 0)) is not int or
            not 0 <= state.get("rate_failures", 0) <= 100 or
            not numeric(state.get("page_interval", 12)) or
            state.get("page_interval", 12) < 12):
        raise AdapterError("Invalid adaptive publisher limits", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    state.setdefault("legacy_rate_history_unknown", True)
    return state


# Source-local durable transport. The same publisher namespace/collection lock
# remains authoritative; no runtime import of another adapter is permitted.
_COLLECTION_LOCK_CONTEXT = None
_INFRASTRUCTURE_UNTIL = {}
_INITIAL_DIRECTORY_PROVEN = False
_INVENTORY_ACTIVE = False
_JOURNAL_EXPORTS = []
_JOURNAL_GENERATION = None
_JOURNAL_GUARDS = {}
_JOURNAL_LOCKS = {}
_JOURNAL_NEW = False
_JOURNAL_RETAINED_FD = _JOURNAL_RETAINED_ID = None
_JOURNAL_SIGNALS = []
_JOURNAL_WRITING = False
_LAST_KNOWN_STATE = None
_LEGACY_ACCESS_OBSERVATIONS = []
_PUBLISHER_HOSTS = {
    urllib.parse.urlsplit(url).hostname for url in ROBOTS_ORIGINS
}
_ROBOTS_CONTEXT = None
_STARTUP_QUIET_UNTIL = None
_STATE_PARENT_FD = _STATE_PARENT_ID = _STATE_PARENT_PATH = None
_VERIFIED_REMOTE_CREATE = None
_LOCAL_FIRST_ACCESS_CHECKED = False


def numeric(value):
    try:
        return (type(value) in (int, float) and math.isfinite(value) and
                value >= 0)
    except OverflowError:
        return False


def empty_budget(now):
    return {
        "schema":
            3,
        "family":
            FAMILY,
        "legacy_hold_history":
            "unlogged_fields_unknown",
        "legacy_access_history_unknown":
            True,
        "legacy_rate_history_unknown":
            True,
        "legacy_access_observations":
            copy.deepcopy(_LEGACY_ACCESS_OBSERVATIONS),
        "research_provenance": {},
        "starts": [],
        "interval":
            12.2,
        "until":
            now + 60,
        "page_gap":
            30,
        "completed":
            0,
        "inventory_completed":
            0,
        "blocked":
            False,
        "in_flight":
            False,
        "legacy_429_history":
            "unknown",
        "observed_429_since_transition":
            0,
        "rate_limit_until":
            0,
        "observed_at":
            now,
    }


def validate_budget(state):
    """Validate retained constraints without resetting unknown history."""
    if not isinstance(state, dict):
        raise AdapterError("Invalid publisher state", "access")
    expected = set(empty_budget(0))
    if (set(state) != expected or type(state["schema"]) is not int or
            state["schema"] != 3 or state["family"] != FAMILY or
            any(not numeric(state[k]) for k in (
                "interval",
                "until",
                "page_gap",
                "completed",
                "inventory_completed",
                "rate_limit_until",
                "observed_at",
            )) or state["interval"] < 12.2 or state["page_gap"] < 30 or
            state["legacy_429_history"] != "unknown" or
            state["legacy_hold_history"] != "unlogged_fields_unknown" or
            state["legacy_access_history_unknown"] is not True or
            state["legacy_rate_history_unknown"] is not True or
            state["legacy_access_observations"] != _LEGACY_ACCESS_OBSERVATIONS
            or not isinstance(state["research_provenance"], dict) or
            type(state["blocked"]) is not bool or
            type(state["in_flight"]) is not bool or
            type(state["observed_429_since_transition"]) is not int or
            not 0 <= state["observed_429_since_transition"] <= 100 or
            not isinstance(state["starts"], list) or
            len(state["starts"]) > 1000 or
            any(not numeric(t) or t > state["observed_at"]
                for t in state["starts"]) or
            state["starts"] != sorted(set(state["starts"])) or
            state["completed"] > state["observed_at"] or
            state["inventory_completed"] > state["observed_at"] or
            state["observed_at"] > time.time() + 1):
        raise AdapterError("Invalid retained publisher constraints", "access")
    epochs = (
        "until",
        "completed",
        "inventory_completed",
        "rate_limit_until",
        "observed_at",
    )
    if (any(not supported_epoch(state[key]) for key in epochs) or
            any(not supported_epoch(value) for value in state["starts"]) or
            not supported_epoch(time.time() + state["interval"]) or
            not supported_epoch(time.time() + state["page_gap"])):
        raise AdapterError("Unsupported retained clock magnitude", "access")
    return state


class JournalAuthorityChanged(AdapterError):
    """A replacement journal cannot inherit a stale descriptor's authority."""


def locked_journal_authority(check_generation=True):
    """Bind each operation to the original locked RW byte generation."""
    try:
        collection_lock_authority()
        if len(_JOURNAL_LOCKS) != 1:
            raise ValueError("Exactly one original journal lock required")
        descriptor, identity = next(iter(_JOURNAL_LOCKS.items()))
        guard = _JOURNAL_GUARDS[descriptor]
        for held in (descriptor, guard):
            info = journal_identity(held)
            if (info.st_dev, info.st_ino) != identity:
                raise ValueError("Retained journal identity changed")
        verify_retained_flock(descriptor, _STATE_PATH)
        if check_generation:
            journal_verify()
        return guard
    except BaseException as error:
        fail_publisher_cleanup()
        raise JournalAuthorityChanged(
            "Locked current publisher journal changed", "access") from error


def state_parent(directory=None):
    """Pin the verified parent and refuse replacement of its current path."""
    global _STATE_PARENT_FD, _STATE_PARENT_ID, _STATE_PARENT_PATH
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    directory = directory or (os.path.dirname(_STATE_PATH)
                              if _STATE_PATH else _STATE_PARENT_PATH)
    if not directory or not os.path.isabs(directory):
        raise AdapterError("Publisher state parent unavailable", "access")
    fresh = _STATE_PARENT_FD is None
    named_before = os.stat(directory, follow_symlinks=False)
    expected = (named_before.st_dev, named_before.st_ino)
    descriptor = (os.open(directory, os.O_RDONLY | os.O_DIRECTORY |
                          os.O_NOFOLLOW) if fresh else _STATE_PARENT_FD)
    try:
        info = os.fstat(descriptor)
        current = os.stat(directory, follow_symlinks=False)
        identity = (info.st_dev, info.st_ino)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
                info.st_mode & 0o077 or not stat.S_ISDIR(current.st_mode) or
                current.st_uid != os.getuid() or current.st_mode & 0o077 or
                identity != expected or
                identity != (current.st_dev, current.st_ino) or not fresh and
            (directory != _STATE_PARENT_PATH or identity != _STATE_PARENT_ID)):
            raise AdapterError("Publisher state parent changed", "access")
    except BaseException:
        _STATE_TRUSTED = False
        _PUBLISHER_GATE_FAILED = True
        if fresh:
            journal_close_directory(descriptor, expected)
        raise
    if fresh:
        _STATE_PARENT_FD = descriptor
        _STATE_PARENT_ID = identity
        _STATE_PARENT_PATH = directory
    return descriptor


def validate_state_descriptor(descriptor, parent, name):
    """Bind a safe current journal inode within the retained parent."""
    info = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
        raise JournalAuthorityChanged("Publisher journal path changed",
                                      "access")
    validate_owned_output(info)
    state_parent(_STATE_PARENT_PATH)
    return info


def budget_file():
    global _STATE_PATH, _STATE_LOCK, _INITIAL_DIRECTORY_PROVEN
    directory = os.path.join(tempfile.gettempdir(),
                             FAMILY + "-" + str(os.getuid()))
    if _STATE_PARENT_FD is not None:
        state_parent(directory)
    else:
        try:
            os.mkdir(directory, mode=0o700)
        except FileExistsError:
            pass
        else:
            _INITIAL_DIRECTORY_PROVEN = True
    info = os.stat(directory, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o077):
        raise AdapterError("Unsafe publisher state directory", "access")
    state_parent(directory)
    _STATE_PATH = _STATE_LOCK = os.path.join(directory, "state.json")


def locked_budget():
    global _JOURNAL_NEW
    budget_file()
    parent = state_parent()
    name = os.path.basename(_STATE_PATH)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        if _JOURNAL_RETAINED_FD is not None:
            journal_verify()
            fd = os.dup(_JOURNAL_RETAINED_FD)
            _JOURNAL_NEW = False
        elif (_INITIAL_DIRECTORY_PROVEN and not _RESTORED and
              _VERIFIED_REMOTE_CREATE is not None):
            try:
                fd = os.open(name,
                             flags | os.O_CREAT | os.O_EXCL,
                             0o600,
                             dir_fd=parent)
                _JOURNAL_NEW = True
            except FileExistsError:
                fd = os.open(name, flags, dir_fd=parent)
                _JOURNAL_NEW = False
        else:
            fd = os.open(name, flags, dir_fd=parent)
    except BaseException:
        fail_publisher_cleanup()
        raise
    acquired = None
    try:
        acquired = os.fstat(fd)
        info = validate_state_descriptor(fd, parent, name)
    except BaseException:
        fail_publisher_cleanup()
        if acquired is not None:
            try:
                close_retained_descriptor(fd,
                                          (acquired.st_dev, acquired.st_ino))
            except BaseException:
                pass
        raise
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o077):
        os.close(fd)
        raise AdapterError("Unsafe publisher journal", "access")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = validate_state_descriptor(fd, parent, name)
        return BudgetLock(fd, info)
    except BaseException:
        fail_publisher_cleanup()
        try:
            close_retained_descriptor(fd, (info.st_dev, info.st_ino))
        except BaseException:
            pass
        raise


def load_budget():
    """Read only the acknowledged current generation under its real lock."""
    locked_journal_authority()
    raw = journal_verify()
    if not raw and _JOURNAL_NEW and _VERIFIED_REMOTE_CREATE is not None:
        return validate_budget(copy.deepcopy(_VERIFIED_REMOTE_CREATE))
    try:
        return decode_local_budget(raw)
    except (ValueError, TypeError) as error:
        fail_publisher_cleanup()
        raise AdapterError("Corrupt publisher journal", "access") from error


def write_budget(state):
    """Latch before cleanup; never invalidate a stronger external generation."""
    global _LAST_KNOWN_STATE
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    try:
        if _LAST_KNOWN_STATE is None:
            _LAST_KNOWN_STATE = copy.deepcopy(state)
        else:
            _LAST_KNOWN_STATE = merge_budget(_LAST_KNOWN_STATE, state)
        return write_budget_body(state)
    except BaseException as error:
        fail_publisher_cleanup()
        if not isinstance(error, JournalAuthorityChanged) or _JOURNAL_WRITING:
            with contextlib.suppress(BaseException):
                invalidate_failed_journal()
        raise


def retained_descriptor_info(descriptor, identity):
    """Validate original ownership without adopting a named replacement."""
    info = os.fstat(descriptor)
    if ((info.st_dev, info.st_ino) != identity or
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR
            or info.st_nlink not in (0, 1)):
        raise AdapterError("Retained descriptor ownership changed", "access")
    return info


def close_retained_descriptor(descriptor, identity):
    """Never close a descriptor reused for an unrelated object."""
    retained_descriptor_info(descriptor, identity)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.close(descriptor)


def fail_publisher_cleanup():
    """Cleanup failure stays fatal across later collection/export entries."""
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    _STATE_TRUSTED = False
    _PUBLISHER_GATE_FAILED = True
    try:
        invalidate_family_artifact()
    except BaseException:
        # Unsafe objects remain untouched; the sticky gate still forbids export.
        pass


def collection_lock_authority():
    """Require the acquired lifecycle descriptors and current named inode."""
    if _COLLECTION_LOCK_FD is None:
        return
    try:
        descriptor, guard, identity, path = _COLLECTION_LOCK_CONTEXT
        if descriptor != _COLLECTION_LOCK_FD:
            raise ValueError("Lifecycle descriptor changed")
        for held in (descriptor, guard):
            retained_descriptor_info(held, identity)
            verify_retained_flock(held, path)
        current = os.stat(path, follow_symlinks=False)
        validate_owned_output(current)
        if (current.st_dev, current.st_ino) != identity:
            raise ValueError("Lifecycle path changed")
    except BaseException as error:
        fail_publisher_cleanup()
        raise AdapterError("Shared lifecycle lock changed", "access") from error


def close_collection_lock():
    """Release only acquired lifecycle FDs and preserve a primary error."""
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT
    primary = sys.exc_info()[0] is not None
    context = _COLLECTION_LOCK_CONTEXT
    descriptor = _COLLECTION_LOCK_FD
    _COLLECTION_LOCK_FD = _COLLECTION_LOCK_CONTEXT = None
    if descriptor is None:
        return
    failure = None
    if context is None or context[0] != descriptor:
        failure = AdapterError("Lifecycle ownership unavailable", "access")
    else:
        original, guard, identity, path = context
        safe = []
        for held in (guard, original):
            try:
                retained_descriptor_info(held, identity)
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                safe.append(held)
            except BaseException as error:
                failure = failure or error
        for held in safe:
            try:
                close_retained_descriptor(held, identity)
            except BaseException as error:
                failure = failure or error
    if failure is not None:
        fail_publisher_cleanup()
        if not primary:
            raise failure


class BudgetLock:
    """Keep one original RW authority across scopes and verified closes."""

    def __init__(self, descriptor, info):
        self.descriptor = descriptor
        self.identity = (info.st_dev, info.st_ino)
        self.guard = os.dup(self.descriptor)
        _JOURNAL_LOCKS[self.descriptor] = self.identity
        _JOURNAL_GUARDS[self.descriptor] = self.guard
        try:
            journal_retain(self.guard)
            locked_journal_authority()
        except BaseException:
            _JOURNAL_LOCKS.pop(self.descriptor, None)
            _JOURNAL_GUARDS.pop(self.descriptor, None)
            with contextlib.suppress(BaseException):
                close_retained_descriptor(self.guard, self.identity)
            raise

    def fileno(self):
        locked_journal_authority()
        return self.descriptor

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        primary = sys.exc_info()[0] is not None
        if self.descriptor is None:
            return
        descriptor, guard = self.descriptor, self.guard
        failure = None
        try:
            if not _PUBLISHER_GATE_FAILED:
                locked_journal_authority()
        except BaseException as error:
            failure = error
        self.descriptor = self.guard = None
        _JOURNAL_LOCKS.pop(descriptor, None)
        _JOURNAL_GUARDS.pop(descriptor, None)
        for held in (guard, descriptor):
            try:
                close_retained_descriptor(held, self.identity)
            except BaseException as error:
                fail_publisher_cleanup()
                failure = failure or error
        try:
            if _JOURNAL_RETAINED_FD is not None:
                journal_identity(_JOURNAL_RETAINED_FD)
                fcntl.flock(_JOURNAL_RETAINED_FD, fcntl.LOCK_UN)
        except BaseException as error:
            fail_publisher_cleanup()
            failure = failure or error
        if failure is not None and not primary:
            raise failure


def invalidate_failed_journal():
    """Invalidate only a verified own commit, never an external generation."""
    if not _JOURNAL_LOCKS:
        raise AdapterError("Original journal lock unavailable", "access")
    journal_verify()
    locked_journal_authority()
    journal_identity(_JOURNAL_RETAINED_FD)
    os.ftruncate(_JOURNAL_RETAINED_FD, 0)
    journal_acknowledge(b"")
    locked_journal_authority()
    os.fsync(_JOURNAL_RETAINED_FD)
    journal_acknowledge(b"")


def write_budget_body(state):
    """Acknowledge each own mutation before allowing another mutation."""
    global _INITIAL_DIRECTORY_PROVEN, _JOURNAL_WRITING
    state = validate_budget(state)
    payload = json.dumps(state, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    if len(payload) > 16384:
        raise AdapterError("Oversized publisher journal", "access")
    locked_journal_authority()
    _JOURNAL_WRITING = True
    os.ftruncate(_JOURNAL_RETAINED_FD, 0)
    journal_acknowledge(b"")
    locked_journal_authority()
    os.fsync(_JOURNAL_RETAINED_FD)
    journal_acknowledge(b"")
    offset = 0
    while offset < len(payload):
        locked_journal_authority()
        count = os.pwrite(_JOURNAL_RETAINED_FD, payload[offset:], offset)
        if type(count) is not int or not 0 < count <= len(payload) - offset:
            raise AdapterError("Incomplete publisher journal write", "access")
        offset += count
        journal_acknowledge(payload[:offset])
    locked_journal_authority()
    os.fsync(_JOURNAL_RETAINED_FD)
    journal_acknowledge(payload)
    locked_journal_authority()
    _JOURNAL_WRITING = False
    _INITIAL_DIRECTORY_PROVEN = False


def merge_budget(left, right):
    left, right = validate_budget(left), validate_budget(right)
    result = empty_budget(time.time())
    for key in (
            "until",
            "interval",
            "page_gap",
            "completed",
            "inventory_completed",
            "observed_429_since_transition",
            "rate_limit_until",
    ):
        result[key] = max(left[key], right[key])
    if (left["research_provenance"] and right["research_provenance"] and
            left["research_provenance"] != right["research_provenance"]):
        raise AdapterError("Research provenance differs across state", "access")
    result["research_provenance"] = (left["research_provenance"] or
                                     right["research_provenance"])
    result["starts"] = sorted(set(left["starts"] + right["starts"]))
    result["legacy_access_history_unknown"] = (
        left["legacy_access_history_unknown"] or
        right["legacy_access_history_unknown"])
    result["blocked"] = left["blocked"] or right["blocked"]
    result["in_flight"] = left["in_flight"] or right["in_flight"]
    return validate_budget(result)


def remaining():
    check_deadline()
    return min(_RUN_END - EXPORT_TIMEOUT, _PHASE_END) - time.monotonic()


def pace(interval=12.2):
    """Hold the physical journal lock through the response completion."""
    lock = locked_budget()
    try:
        state = load_budget()
        if state["in_flight"]:
            raise AdapterError("Unsettled publisher request retained", "access")
        if state["blocked"]:
            raise AdapterError("Retained actual publisher refusal", "access")
        if state["observed_429_since_transition"] >= 100:
            raise AdapterError("Unsupported retained429 history; no traffic",
                               "access")
        state["interval"] = max(state["interval"], interval, 12.2)
        if state["rate_limit_until"] > time.time():
            raise AdapterError("Retained publisher rate-limit embargo",
                               "access", 429)
        while True:
            now = time.time()
            recent = [t for t in state["starts"] if now - t < 60]
            ready = max(
                state["until"],
                state["completed"] + state["interval"],
                recent[-1] + state["interval"] if recent else now,
                recent[-5] + 60 if len(recent) >= 5 else now,
            )
            if ready - now + 120 >= remaining():
                raise AdapterError(
                    "Retained pacing exceeds remaining collection budget",
                    "access",
                )
            if ready <= now:
                state["starts"] = recent + [now]
                state["observed_at"] = now
                state["in_flight"] = True
                write_budget(state)
                return lock, state
            time.sleep(min(ready - now, 30))
    except BaseException:
        lock.close()
        raise


def retry_embargo(value):
    """Retain supported future timing; never discard positive overflow."""
    if value is None:
        return 0
    if not isinstance(value, str):
        raise AdapterError("Unsupported Retry-After field", "access")
    value = value.strip()
    numeric_form = re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
    if numeric_form:
        try:
            delay = float(value)
            until = time.time() + delay
            if not numeric(delay) or not supported_epoch(until):
                raise ValueError("Unsupported positive duration")
        except (ValueError, OverflowError, OSError):
            raise AdapterError("Unsupported positive Retry-After duration",
                               "access") from None
        return until if delay > 0 and until > time.time() else 0
    # Numeric-looking positive extensions cannot establish an unbounded grant.
    if (re.fullmatch(
            r"\+?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
            r"(?:[eE][+-]?[0-9]+)?", value) or
            value.lower() in {"inf", "+inf", "infinity", "+infinity"}):
        raise AdapterError("Unsupported positive Retry-After syntax", "access")
    try:
        date = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        # An explicitly out-of-range future year is restrictive but cannot
        # be represented in this runtime's durable timestamp schema.
        year = re.search(r"\b[0-9]{1,2} [A-Za-z]{3} ([0-9]{5,})\b", value)
        if year:
            raise AdapterError("Unsupported Retry-After date", "access")
        return 0
    if date.utcoffset() is None:
        return 0
    try:
        until = date.timestamp()
        if not supported_epoch(until):
            raise ValueError("Unsupported date timestamp")
    except (ValueError, OverflowError, OSError):
        raise AdapterError("Unsupported Retry-After date", "access") from None
    return until if until > time.time() else 0


def retry_after_constraints(headers):
    """Inspect every observed field, retaining MAX even alongside overflow."""
    if hasattr(headers, "get_all"):
        values = headers.get_all("Retry-After") or []
    else:
        values = [
            value for key, value in headers.items()
            if key.lower() == "retry-after"
        ]
    maximum, unsupported = 0, False
    for value in values:
        # Combined numeric fields contain no date comma ambiguity.
        parts = (value.split(",") if isinstance(value, str) and re.fullmatch(
            r"\s*[0-9]+(?:\.[0-9]+)?"
            r"(?:\s*,\s*[0-9]+(?:\.[0-9]+)?)+\s*", value) else [value])
        if (parts == [value] and isinstance(value, str) and "," in value and
                re.search(r"[0-9]|inf", value, re.I) and
                not re.fullmatch(r"[A-Za-z]+,[^,]+", value)):
            # A combined date/duration field is not a single HTTP date.
            # Do not let permissive date parsing discard a restrictive suffix.
            unsupported = True
            continue
        for part in parts:
            try:
                maximum = max(maximum, retry_embargo(part))
            except AdapterError:
                unsupported = True
    return maximum, unsupported


def infrastructure_pace(url):
    """Honor new metadata embargoes without adding a publication floor."""
    host = urllib.parse.urlsplit(url).hostname
    while True:
        wait = _INFRASTRUCTURE_UNTIL.get(host, 0) - time.time()
        if wait <= 0:
            return
        if wait + 1 >= remaining():
            raise AdapterError("GitHub embargo exceeds phase budget", "access")
        time.sleep(min(wait, 30))


def retain_infrastructure_retry(url, value):
    host = urllib.parse.urlsplit(url).hostname
    _INFRASTRUCTURE_UNTIL[host] = max(_INFRASTRUCTURE_UNTIL.get(host, 0),
                                      retry_embargo(value))


def inventory_guard():
    """Each physical inventory retry/redirect retains the completion gap."""
    with locked_budget():
        state = load_budget()
    while True:
        wait = state["inventory_completed"] + state["page_gap"] - time.time()
        if wait <= 0:
            return
        if wait + 30 >= remaining():
            raise AdapterError("Page guard exceeds remaining collection budget",
                               "access")
        time.sleep(min(wait, 30))


def source_file_digest():
    """Hash this standalone implementation without retaining its contents."""
    descriptor = os.open(__file__, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        current = os.stat(__file__, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                info.st_nlink != 1 or info.st_mode & 0o022 or
            (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise AdapterError("Unsafe implementation file", "access")
        content = stream.read(5_000_001)
        current = os.stat(__file__, follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Implementation file changed", "access")
    if len(content) > 5_000_000:
        raise AdapterError("Source implementation exceeds reviewed bound",
                           "access")
    return hashlib.sha256(content).hexdigest()


@contextlib.contextmanager
def owned_artifact_directory(path):
    """Pin output parent operations and close only that original directory."""
    root, before = checked_artifact_path(path)
    descriptor = os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        held = os.fstat(descriptor)
        if (held.st_dev, held.st_ino) != (before.st_dev, before.st_ino):
            raise AdapterError("Artifact directory changed", "access")
        verify_artifact_directory(root, descriptor)
        yield root, descriptor
    finally:
        journal_close_directory(descriptor, (before.st_dev, before.st_ino))


def verify_artifact_directory(root, descriptor):
    """Reject replacement parents without following their contents."""
    held = os.fstat(descriptor)
    current = os.stat(root, follow_symlinks=False)
    if (not stat.S_ISDIR(current.st_mode) or current.st_uid != os.getuid() or
            current.st_mode & 0o022 or
        (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
        raise AdapterError("Artifact directory changed", "access")


def owned_artifact_info(root, directory):
    """Inspect only the current safely owned artifact in the pinned parent."""
    verify_artifact_directory(root, directory)
    try:
        info = os.stat("pacing-state.json",
                       dir_fd=directory,
                       follow_symlinks=False)
    except FileNotFoundError:
        return None
    validate_owned_output(info)
    return info


def same_owned_artifact(root, directory, expected):
    """Prevent replacing or unlinking an independently replaced file."""
    current = owned_artifact_info(root, directory)
    if current is None or expected is None:
        same = current is None and expected is None
    else:
        same = (current.st_dev, current.st_ino) == (
            expected.st_dev,
            expected.st_ino,
        )
    if not same:
        raise AdapterError("Owned artifact identity changed", "access")


def workflow_output_signal(value):
    """Retain verified readiness authority through all outer cleanup."""
    if value == "true":
        if _PUBLISHER_GATE_FAILED or not _STATE_TRUSTED:
            raise AdapterError("Untrusted publisher cannot signal ready",
                               "access")
        journal_verify()
    path = os.environ["GITHUB_OUTPUT"]
    descriptor = os.open(
        path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
    with journal_output(descriptor, path, _JOURNAL_SIGNALS) as (output, _):
        output.write(f"pacing_ready={value}\n")
        output.flush()
        os.fsync(output.fileno())


def invalidate_family_artifact():
    """Never reopen a newer output pathname during failure cleanup."""
    journal_invalidate_outputs()


def validate_owned_output(info):
    """Refuse unsafe output before reading or modifying it."""
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o077):
        raise AdapterError("Unsafe owned artifact output", "access")


def checked_artifact_path(path):
    """Validate this attempt's private output directory and basename."""
    configured = os.environ.get("RUNNER_TEMP", "")
    if not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (os.path.dirname(os.path.abspath(path)) != root or
            os.path.basename(path) != "pacing-state.json"):
        raise AdapterError("Unsafe artifact output path", "access")
    info = os.stat(root, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            info.st_mode & 0o022):
        raise AdapterError("Unsafe artifact output directory", "access")
    return root, info


def export_family_artifact():
    """Atomically export trusted state; invalidate owned output on failure."""
    path = os.environ.get("FRSE_PACING_ARTIFACT_PATH")
    if not path:
        return
    if not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED:
        invalidate_family_artifact()
        return
    with owned_artifact_directory(path) as (root, directory):
        prior = owned_artifact_info(root, directory)
        temporary = None
        temporary_info = None
        try:
            run_id, attempt = current_run_identity()
            with locked_budget():
                state = load_budget()
            if state["in_flight"]:
                raise AdapterError("Unsettled state cannot be exported",
                                   "access")
            envelope = {
                "schema":
                    1,
                "repository":
                    "YouthOpps/data-pipeline",
                "run_id":
                    run_id,
                "run_attempt":
                    attempt,
                "source":
                    SOURCE_ID,
                "head_sha":
                    os.environ.get("GITHUB_SHA"),
                "adapter_sha256":
                    source_file_digest(),
                "state_sha256":
                    hashlib.sha256(
                        json.dumps(state, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest(),
                "state":
                    state,
            }
            if not re.fullmatch(r"[0-9a-f]{40}", envelope["head_sha"] or ""):
                raise AdapterError("Invalid artifact source revision", "access")
            payload = json.dumps(envelope).encode("utf-8")
            temporary = ".owned-pacing-" + os.urandom(16).hex()
            verify_artifact_directory(root, directory)
            descriptor = os.open(
                temporary,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW |
                os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            temporary_info = os.fstat(descriptor)
            with journal_output(descriptor, os.path.join(root, temporary),
                                _JOURNAL_EXPORTS) as (stream, pin):
                stream.write(payload.decode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            journal_verify()
            same_owned_artifact(root, directory, prior)
            os.replace(
                temporary,
                "pacing-state.json",
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
            temporary = None
            pin["name"] = "pacing-state.json"
            same_owned_artifact(root, directory, temporary_info)
            journal_verify_output_bytes(pin, payload)
            journal_verify()
            workflow_output_signal("true")
        except BaseException:
            fail_publisher_cleanup()
            raise
        finally:
            primary = sys.exc_info()[0] is not None
            if temporary is not None and temporary_info is not None:
                try:
                    verify_artifact_directory(root, directory)
                    current = os.stat(temporary,
                                      dir_fd=directory,
                                      follow_symlinks=False)
                    validate_owned_output(current)
                    if (current.st_dev, current.st_ino) != (
                            temporary_info.st_dev,
                            temporary_info.st_ino,
                    ):
                        raise AdapterError("Temporary artifact changed",
                                           "access")
                    os.unlink(temporary, dir_fd=directory)
                except BaseException:
                    fail_publisher_cleanup()
                    if not primary:
                        raise


def apply_robots_policy(text, url, state=None):
    """Keep policy application pending until restrictive state is durable."""
    if state is None:
        with locked_budget() as lock:
            current = load_budget()
            if current["in_flight"]:
                raise AdapterError("Unsettled publisher transaction", "access")
            current["in_flight"] = True
            write_budget(current)
            try:
                delay = apply_robots_policy(text, url, current)
            except RobotsDisallow:
                current["in_flight"] = False
                write_budget(current)
                raise
            current["in_flight"] = False
            write_budget(current)
            return delay
    try:
        delay = robots_policy(text, url)
    except RobotsDisallow:
        state["blocked"] = True
        state["observed_at"] = time.time()
        write_budget(state)
        raise
    state["interval"] = max(state["interval"], delay)
    state["observed_at"] = time.time()
    write_budget(state)
    return delay


def physical_request_bytes(
    url,
    *,
    interval=6,
    method="GET",
    headers=None,
    payload=None,
    robots=False,
    byte_limit=5_000_000,
    infrastructure=False,
):
    global _STATE_TRUSTED, _PUBLISHER_GATE_FAILED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate failed", "access")
    if type(byte_limit) is not int or not 0 < byte_limit <= 30_000_000:
        raise AdapterError("Invalid response byte bound", "fetch")
    purpose_target = urllib.parse.urlsplit(url)
    if robots and (purpose_target.path != "/robots.txt" or
                   purpose_target.query or purpose_target.fragment):
        raise AdapterError("Robots purpose requires literal robots.txt",
                           "access")
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    publisher = host in _PUBLISHER_HOSTS
    if not publisher and not infrastructure:
        raise AdapterError("Explicit infrastructure transport required",
                           "access")
    if infrastructure:
        if publisher:
            raise AdapterError("Publisher cannot use metadata purpose",
                               "access")
        infrastructure_pace(url)
    if publisher and (not _STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        raise AdapterError("Trusted publisher state required before access",
                           "access")
    if publisher:
        collection_lock_authority()
    if publisher and _INVENTORY_ACTIVE and not robots:
        inventory_guard()
    if publisher:
        wait_startup_quiet()
    lock, state = pace(interval) if publisher else (None, None)
    attempted = response_received = response_constraints_saved = False
    retryable_connection_failure = False
    response_exit_completed = False

    @contextlib.contextmanager
    def assessed_response_scope():
        # Only a completed __exit__ proves response closure. An intentional
        # assessed stop is distinct from an exception raised by closure itself.
        nonlocal response_exit_completed
        response.__enter__()
        try:
            yield
        except BaseException:
            suppressed = response.__exit__(*sys.exc_info())
            response_exit_completed = True
            if not suppressed:
                raise
        else:
            response.__exit__(None, None, None)
            response_exit_completed = True

    try:
        if publisher:
            collection_lock_authority()
            verify_local_publisher_start()
            locked_journal_authority()
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers={
                **(headers or {}), "User-Agent": _USER_AGENT
            },
        )
        if publisher:
            print(
                json.dumps({
                    "event": "request_start",
                    "url": url,
                    "time": time.time(),
                    "user_agent": request.get_header("User-agent"),
                }),
                file=sys.stderr,
                flush=True,
            )
        timeout = request_timeout(publisher)
        if publisher:
            collection_lock_authority()
            locked_journal_authority()
        try:
            if publisher and (_STARTUP_QUIET_UNTIL is None or
                              time.monotonic() < _STARTUP_QUIET_UNTIL):
                raise AdapterError("Publisher startup quiet changed", "access")
            attempted = True
            response = _OPENER.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            if publisher and isinstance(reason, ssl.SSLError):
                raise AdapterError(
                    "Publisher TLS verification failed; pending retained",
                    "access",
                ) from error
            if publisher and not (isinstance(reason,
                                             (ConnectionError, TimeoutError)) or
                                  isinstance(reason, socket.gaierror) and
                                  reason.errno == socket.EAI_AGAIN or
                                  isinstance(reason, OSError) and
                                  reason.errno in (
                                      errno.ECONNABORTED,
                                      errno.ECONNREFUSED,
                                      errno.ECONNRESET,
                                      errno.EHOSTDOWN,
                                      errno.EHOSTUNREACH,
                                      errno.ENETDOWN,
                                      errno.ENETUNREACH,
                                      errno.EPIPE,
                                      errno.ETIMEDOUT,
                                  )):
                raise AdapterError(
                    "Unclassified connection failure; pending retained",
                    "access",
                ) from error
            retryable_connection_failure = True
            raise
        response_received = True
        with assessed_response_scope():
            status = response.status
            if infrastructure:
                retain_infrastructure_retry(url,
                                            response.headers.get("Retry-After"))
            if publisher:
                if status == 429:
                    strikes = state["observed_429_since_transition"] + 1
                    state["observed_429_since_transition"] = strikes
                    state["interval"] = max(state["interval"] * 2,
                                            12.2 * 2**strikes)
                    state["page_gap"] = max(state["page_gap"] * 2,
                                            30 * 2**strikes)
                    state["until"] = max(state["until"],
                                         time.time() + 1800 * 2**(strikes - 1))
                    state["rate_limit_until"] = max(state["rate_limit_until"],
                                                    state["until"])
                if status in (401, 403, 451) and not robots:
                    state["blocked"] = True
                # Known status restrictions are durable before parsing any
                # possibly unsupported or duplicated Retry-After field.
                state["observed_at"] = time.time()
                write_budget(state)
                embargo, unsupported = retry_after_constraints(response.headers)
                if embargo > state["until"]:
                    state["until"] = embargo
                    if status == 429:
                        state["rate_limit_until"] = max(
                            state["rate_limit_until"], embargo)
                    state["observed_at"] = time.time()
                    write_budget(state)
                if unsupported:
                    raise AdapterError(
                        "Unsupported Retry-After; pending state retained",
                        "access", status)
            if publisher and status == 429:
                byte_limit = min(byte_limit, 65536)
            declared = response.headers.get("Content-Length")
            if declared and (not declared.isdigit() or
                             int(declared) > byte_limit):
                raise AdapterError("Oversized or invalid response length",
                                   "fetch")
            if (response.headers.get("Content-Encoding", "identity")
                    != "identity"):
                raise AdapterError("Unsupported response content encoding",
                                   "fetch")
            try:
                body = response.read(byte_limit + 1)
            except Exception as error:
                if publisher:
                    raise AdapterError(
                        "Publisher body incomplete; pending state retained",
                        "access",
                        status,
                    ) from error
                raise
            if (len(body) > byte_limit or
                    declared and len(body) != int(declared)):
                raise AdapterError("Response exceeds native byte bound",
                                   "fetch")
            challenge = (publisher and
                         not (robots and 400 <= status < 500 and status != 429)
                         and visible_access_challenge(body, status == 429))
            if challenge:
                response_constraints_saved = True
                if not robots or status == 429:
                    state["blocked"] = True
                raise AdapterError("Visible publisher verification or refusal",
                                   "access")
            if publisher and not (robots and status == 200):
                response_constraints_saved = True
            if publisher and robots and status == 200:
                response_constraints_saved = False
                target = (_ROBOTS_CONTEXT["target"]
                          if _ROBOTS_CONTEXT is not None else SOURCE_URL)
                text = robots_response_text(response.headers, body)
                try:
                    delay = apply_robots_policy(text, target, state)
                except RobotsDisallow:
                    response_constraints_saved = True
                    raise
                if _ROBOTS_CONTEXT is not None:
                    _ROBOTS_CONTEXT["delay"] = delay
                response_constraints_saved = True
            if publisher and status == 429:
                raise AdapterError(
                    "Publisher429; assessed durable cooldown retained",
                    "access", 429)
            return status, response.headers, body
    except BaseException as error:
        if publisher and response_received and not response_exit_completed:
            response_constraints_saved = False
            if isinstance(error,
                          (urllib.error.URLError, TimeoutError, OSError)):
                raise AdapterError(
                    "Received publisher response failed; no connection retry",
                    "access",
                ) from error
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        if lock is not None:
            try:
                state["completed"] = state["observed_at"] = time.time()
                if (attempted and not _PUBLISHER_GATE_FAILED and
                    (response_constraints_saved or
                     not response_received and retryable_connection_failure)):
                    state["in_flight"] = False
                if _INVENTORY_ACTIVE and not robots:
                    state["inventory_completed"] = state["completed"]
                try:
                    write_budget(state)
                    if state["in_flight"]:
                        _STATE_TRUSTED = False
                        _PUBLISHER_GATE_FAILED = True
                except BaseException:
                    _STATE_TRUSTED = False
                    _PUBLISHER_GATE_FAILED = True
                    if not primary:
                        raise
                print(
                    json.dumps({
                        "event": "request_complete",
                        "url": url,
                        "time": time.time(),
                    }),
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                lock.close()


class RobotsDisallow(AdapterError):
    """A genuine applicable REP restriction, retained independently."""


def robots_response_text(headers, body):
    """Validate successful REP representation before settling its owner."""
    values = (headers.get_all("Content-Type") or [] if hasattr(
        headers, "get_all") else [
            value for key, value in headers.items()
            if key.lower() == "content-type"
        ])
    if (len(values) != 1 or not isinstance(values[0], str) or
            values[0].partition(";")[0].strip().lower() != "text/plain"):
        raise AdapterError(
            "Successful robots MIME is not unambiguous text/plain; pending retained",
            "access")
    text = body.decode(headers.get_content_charset() or "utf-8")
    active = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    if "<!--" in active or re.search(r"<\s*[!?/]?[A-Za-z][^>]*>", active):
        raise AdapterError("HTML is not a robots policy; pending retained",
                           "access")
    return text


def selected_robots_policies(groups):
    """A legacy grant cannot displace current identity's wildcard policy."""
    agent = _USER_AGENT.split("/", 1)[0].lower()
    current = [
        g for g in groups
        if any(token != "*" and token in agent for token in g["agents"])
    ]
    wildcard = [g for g in groups if "*" in g["agents"]]
    legacy = [g for g in groups if "youthopps" in g["agents"]]
    return [current or wildcard] + ([legacy] if legacy else [])


def robots_groups(text):
    """Retain native allow/disallow rules with raw selected timing fields."""
    groups = []
    group = {"agents": [], "rules": [], "timing": []}
    has_directive = False
    for original in text.splitlines():
        key, separator, value = original.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if has_directive:
                groups.append(group)
                group = {"agents": [], "rules": [], "timing": []}
                has_directive = False
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
            has_directive = True
        elif key in ("crawl-delay", "request-rate"):
            group["timing"].append((key, value))
            has_directive = True
    groups.append(group)
    directives = [
        line.split("#", 1)[0].strip().lower()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (directives and not any(g["agents"] for g in groups) and
            not all(line.startswith("sitemap:") for line in directives)):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    return groups


def robots_policy(text, url):
    """Use the original native rule matcher and strict selected numeric MAX."""
    parsed = urllib.parse.urlsplit(url)
    policies = selected_robots_policies(robots_groups(text))
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    delay = 12.2
    decimal = r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    for selected in policies:
        matches = []
        for group in selected:
            for directive, pattern in group["rules"]:
                if not pattern:
                    continue
                expression = ("^" + ".*".join(
                    re.escape(part)
                    for part in pattern.removesuffix("$").split("*")) +
                              ("$" if pattern.endswith("$") else ""))
                if re.search(expression, path):
                    matches.append((
                        len(pattern.replace("*", "").removesuffix("$")),
                        directive == "allow",
                    ))
            for key, value in group["timing"]:
                try:
                    if key == "crawl-delay":
                        if not re.fullmatch(decimal, value):
                            raise ValueError()
                        seconds = float(value)
                    else:
                        match = re.fullmatch(
                            r"([0-9]+)\s*/\s*(" + decimal + r")", value)
                        if not match:
                            raise ValueError()
                        count, period = int(match[1]), float(match[2])
                        if count <= 0 or not numeric(period) or period <= 0:
                            raise ValueError()
                        seconds = period / count + 0.01
                    if not numeric(seconds):
                        raise ValueError()
                    datetime.fromtimestamp(time.time() + seconds, timezone.utc)
                    delay = max(delay, seconds)
                except (ValueError, OverflowError, OSError):
                    raise AdapterError(
                        "Unsupported applicable robots timing; "
                        "pending retained",
                        "access",
                    ) from None
        if matches and not max(matches)[1]:
            raise RobotsDisallow("Robots policy disallows this source endpoint",
                                 "access")
    return delay


def check_robots(url):
    """Apply every cached target policy within its durable pending scope."""
    global _ROBOTS_CONTEXT
    parsed = urllib.parse.urlsplit(public_url(url))
    if (parsed.path == "/robots.txt" and not parsed.query and
            not parsed.fragment):
        return 12.2
    origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    if origin in _ROBOTS:
        text = _ROBOTS[origin]
        return 12.2 if text is None else apply_robots_policy(text, url)
    previous = _ROBOTS_CONTEXT
    context = {"target": url, "delay": None}
    _ROBOTS_CONTEXT = context
    try:
        status, headers, body = fetch_source(origin + "/robots.txt",
                                             policy_response=True)
        if 400 <= status < 500 and status != 429:
            _ROBOTS[origin] = None
            return 12.2
        if status != 200:
            raise AdapterError("Required robots policy unavailable", "access",
                               status)
        text = body.decode(headers.get_content_charset() or "utf-8", "strict")
        _ROBOTS[origin] = text
        return (context["delay"] if context["delay"] is not None else
                apply_robots_policy(text, url))
    finally:
        _ROBOTS_CONTEXT = previous


def supported_epoch(value):
    """Unsupported finite timestamps are not valid durable timing states."""
    if not numeric(value):
        return False
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return False
    return True


def journal_metadata(info):
    """Identify the complete acknowledged metadata generation."""
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_uid,
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
    )


def journal_identity(descriptor):
    """Keep the original RW inode beneath its original verified parent."""
    parent = state_parent()
    info = validate_state_descriptor(descriptor, parent,
                                     os.path.basename(_STATE_PATH))
    if ((info.st_dev, info.st_ino) != _JOURNAL_RETAINED_ID or
            stat.S_IMODE(info.st_mode) != 0o600 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR):
        raise JournalAuthorityChanged("Original RW journal changed", "access")
    return info


def journal_snapshot():
    """Read the existing16KiB contract without adopting a post-read change."""
    before = journal_identity(_JOURNAL_RETAINED_FD)
    chunks = []
    offset = 0
    while offset <= 16384:
        block = os.pread(_JOURNAL_RETAINED_FD, 16385 - offset, offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    raw = b"".join(chunks)
    after = journal_identity(_JOURNAL_RETAINED_FD)
    if (len(raw) > 16384 or len(raw) != after.st_size or
            journal_metadata(before) != journal_metadata(after)):
        raise JournalAuthorityChanged("Journal read generation changed",
                                      "access")
    return (*journal_metadata(after), hashlib.sha256(raw).hexdigest()), raw


def journal_acknowledge(expected):
    """Advance own authority only after exact expected-byte readback."""
    global _JOURNAL_GENERATION
    generation, raw = journal_snapshot()
    if raw != expected:
        raise JournalAuthorityChanged("Own journal mutation was superseded",
                                      "access")
    _JOURNAL_GENERATION = generation


def journal_verify():
    """Refuse changed bytes or metadata, including between lock scopes."""
    try:
        generation, raw = journal_snapshot()
        if generation != _JOURNAL_GENERATION:
            raise JournalAuthorityChanged("Journal generation changed",
                                          "access")
        return raw
    except BaseException as error:
        fail_publisher_cleanup()
        if isinstance(error, JournalAuthorityChanged):
            raise
        raise JournalAuthorityChanged("Journal authority unavailable",
                                      "access") from error


def journal_retain(descriptor):
    """Retain the first actual journal; a later acquisition cannot rebase it."""
    global _JOURNAL_RETAINED_FD, _JOURNAL_RETAINED_ID, _JOURNAL_GENERATION
    if _JOURNAL_RETAINED_FD is not None:
        journal_verify()
        return
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR):
        fail_publisher_cleanup()
        raise JournalAuthorityChanged("Unsafe RW journal acquisition", "access")
    retained = os.dup(descriptor)
    identity = (info.st_dev, info.st_ino)
    _JOURNAL_RETAINED_FD, _JOURNAL_RETAINED_ID = retained, identity
    try:
        generation, _ = journal_snapshot()
        if (_JOURNAL_GENERATION is not None and
                generation != _JOURNAL_GENERATION):
            raise JournalAuthorityChanged("Closed journal generation changed",
                                          "access")
        _JOURNAL_GENERATION = generation
    except BaseException:
        fail_publisher_cleanup()
        _JOURNAL_RETAINED_FD = None
        with contextlib.suppress(BaseException):
            close_retained_descriptor(retained, identity)
        raise


def journal_finish():
    """Verify lifetime authority before releasing only the retained owned FD."""
    global _JOURNAL_RETAINED_FD
    if _JOURNAL_RETAINED_FD is None:
        return
    primary = sys.exc_info()[0] is not None
    descriptor = _JOURNAL_RETAINED_FD
    failure = None
    try:
        if not _PUBLISHER_GATE_FAILED:
            journal_verify()
    except BaseException as error:
        failure = error
    try:
        close_retained_descriptor(descriptor, _JOURNAL_RETAINED_ID)
    except BaseException as error:
        fail_publisher_cleanup()
        failure = failure or error
    finally:
        _JOURNAL_RETAINED_FD = None
    if failure is not None and not primary:
        raise failure


def journal_output_info(pin, named=False):
    """Verify a retained output without following a replacement pathname."""
    info = os.fstat(pin["descriptor"])
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
        (info.st_dev, info.st_ino) != pin["identity"] or
            stat.S_IMODE(info.st_mode) != pin["mode"] or
            info.st_nlink not in (0, 1) or
            fcntl.fcntl(pin["descriptor"], fcntl.F_GETFL) & os.O_ACCMODE
            not in (os.O_RDWR, os.O_WRONLY)):
        raise AdapterError("Retained output authority changed", "access")
    if pin["parent"] is not None:
        parent = os.fstat(pin["parent"])
        if (not stat.S_ISDIR(parent.st_mode) or
            (parent.st_dev, parent.st_ino) != pin["parent_identity"] or
                parent.st_uid != pin["parent_owner"] or
                stat.S_IMODE(parent.st_mode) != pin["parent_mode"]):
            raise AdapterError("Retained output parent changed", "access")
    elif named:
        raise AdapterError("Output parent was already released", "access")
    if named:
        parent = os.stat(pin["root"], follow_symlinks=False)
        if (parent.st_dev, parent.st_ino) != pin["parent_identity"]:
            raise AdapterError("Output parent pathname changed", "access")
        current = os.stat(pin["name"],
                          dir_fd=pin["parent"],
                          follow_symlinks=False)
        if (current.st_dev, current.st_ino) != pin["identity"]:
            raise AdapterError("Output pathname changed", "access")
    return info


def journal_retain_output(descriptor, path, pins):
    """Keep the original writable output and parent through outer cleanup."""
    info = os.fstat(descriptor)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
            info.st_nlink != 1 or info.st_mode & 0o022 or
            fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
            not in (os.O_RDWR, os.O_WRONLY)):
        raise AdapterError("Unsafe output acquisition", "access")
    root = os.path.dirname(path)
    named_parent = os.stat(root, follow_symlinks=False)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    retained = None
    try:
        parent_info = os.fstat(parent)
        if (parent_info.st_dev, parent_info.st_ino) != (
                named_parent.st_dev,
                named_parent.st_ino,
        ):
            raise AdapterError("Output parent acquisition changed", "access")
        retained = os.dup(descriptor)
        pin = dict(
            descriptor=retained,
            identity=(info.st_dev, info.st_ino),
            mode=stat.S_IMODE(info.st_mode),
            parent=parent,
            root=root,
            parent_identity=(parent_info.st_dev, parent_info.st_ino),
            parent_owner=parent_info.st_uid,
            parent_mode=stat.S_IMODE(parent_info.st_mode),
            name=os.path.basename(path),
        )
        journal_output_info(pin, named=True)
        pins.append(pin)
        return pin
    except BaseException:
        if retained is not None:
            with contextlib.suppress(BaseException):
                held = os.fstat(retained)
                if (held.st_dev, held.st_ino) == (info.st_dev, info.st_ino):
                    os.close(retained)
        journal_close_directory(parent,
                                (named_parent.st_dev, named_parent.st_ino))
        raise


class JournalOutput:
    """Write immediately after identity checks; never buffer foreign-FD IO."""

    def __init__(self, descriptor, pin):
        self.descriptor = descriptor
        self.pin = pin

    def fileno(self):
        journal_output_info(self.pin, named=True)
        info = os.fstat(self.descriptor)
        if (info.st_dev, info.st_ino) != self.pin["identity"] or fcntl.fcntl(
                self.descriptor, fcntl.F_GETFL) & os.O_ACCMODE not in (
                    os.O_RDWR, os.O_WRONLY):
            raise AdapterError("Output FD was reused", "access")
        return self.descriptor

    def write(self, text):
        raw = text.encode("utf-8")
        offset = 0
        while offset < len(raw):
            count = os.write(self.fileno(), raw[offset:])
            if type(count) is not int or not 0 < count <= len(raw) - offset:
                raise AdapterError("Incomplete owned output write", "access")
            offset += count
        self.fileno()
        return len(text)

    def flush(self):
        self.fileno()


@contextlib.contextmanager
def journal_output(descriptor, path, pins):
    """Close only the acquired output while preserving any body exception."""
    identity = None
    try:
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        pin = journal_retain_output(descriptor, path, pins)
        output = JournalOutput(descriptor, pin)
        yield output, pin
        output.flush()
    except BaseException:
        fail_publisher_cleanup()
        raise
    finally:
        primary = sys.exc_info()[0] is not None
        try:
            info = os.fstat(descriptor)
            if identity is None:
                named = os.stat(path, follow_symlinks=False)
                if (stat.S_ISREG(info.st_mode) and
                        info.st_uid == os.getuid() and
                    (info.st_dev, info.st_ino) == (named.st_dev, named.st_ino)):
                    identity = (info.st_dev, info.st_ino)
            if (identity is None or (info.st_dev, info.st_ino) != identity or
                    fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE
                    not in (os.O_RDWR, os.O_WRONLY)):
                raise AdapterError("Output FD ownership changed", "access")
            os.close(descriptor)
        except BaseException:
            fail_publisher_cleanup()
            if not primary:
                raise


def journal_invalidate_outputs():
    """Invalidate owned artifacts before fallible readiness signalling."""
    errors = []
    for pin in _JOURNAL_EXPORTS:
        if pin["descriptor"] is None:
            continue
        try:
            journal_output_info(pin)
            os.ftruncate(pin["descriptor"], 0)
            os.fsync(pin["descriptor"])
        except BaseException as error:
            errors.append(error)
    for pin in _JOURNAL_SIGNALS:
        if pin["descriptor"] is None:
            continue
        try:
            journal_output_info(pin)
            raw = b"pacing_ready=false\n"
            offset = 0
            while offset < len(raw):
                journal_output_info(pin)
                count = os.write(pin["descriptor"], raw[offset:])
                if type(count) is not int or not 0 < count <= len(raw) - offset:
                    raise AdapterError("Readiness write incomplete", "access")
                offset += count
            os.fsync(pin["descriptor"])
        except BaseException as error:
            errors.append(error)
    if errors:
        raise AdapterError("Retained output invalidation failed", "access")


def journal_finish_outputs():
    """Keep output authority through all earlier lifecycle closes."""
    primary = sys.exc_info()[0] is not None
    failures = []
    for pin in _JOURNAL_SIGNALS + _JOURNAL_EXPORTS:
        for key, identity in (
            ("parent", pin["parent_identity"]),
            ("descriptor", pin["identity"]),
        ):
            descriptor = pin[key]
            if descriptor is None:
                continue
            try:
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != identity:
                    raise AdapterError("Output cleanup FD changed", "access")
                os.close(descriptor)
            except BaseException as error:
                if key == "parent":
                    pin[key] = None
                fail_publisher_cleanup()
                failures.append(error)
            finally:
                pin[key] = None
    if failures and not primary:
        raise failures[0]


def journal_close_directory(descriptor, identity):
    """Preserve foreign recycled parent FDs and the original body failure."""
    primary = sys.exc_info()[0] is not None
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or
            (info.st_dev, info.st_ino) != identity):
            raise AdapterError("Original directory FD changed", "access")
        os.close(descriptor)
    except BaseException:
        fail_publisher_cleanup()
        if not primary:
            raise


def journal_verify_output_bytes(pin, payload):
    """Prove complete owned export bytes before signalling readiness."""
    before = journal_output_info(pin, named=True)
    chunks = []
    offset = 0
    while offset <= len(payload):
        block = os.pread(pin["descriptor"], len(payload) + 1 - offset, offset)
        if not block:
            break
        chunks.append(block)
        offset += len(block)
    after = journal_output_info(pin, named=True)
    if b"".join(chunks) != payload or journal_metadata(
            before) != journal_metadata(after):
        raise AdapterError("Owned export readback differs", "access")


def save_budget(state):
    """Retain the original source helper interface under modern ownership."""
    return write_budget(state)


def decode_local_budget(raw):
    """Only the exact reviewed settled research journal may migrate locally."""
    value = json.loads(raw)
    if value.get("schema") == 3:
        return validate_budget(value)
    if (os.environ.get("GITHUB_ACTIONS") == "true" or
            hashlib.sha256(raw).hexdigest() != _LEGACY_LOCAL_STATE_SHA256):
        raise AdapterError("Unreviewed legacy local publisher state", "access")
    validate_legacy_budget(value)
    return reviewed_research_transition()


def reviewed_research_transition():
    state = copy.deepcopy(_RESEARCH_TRANSITION)
    digest = hashlib.sha256(
        json.dumps(state, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    if digest != _RESEARCH_TRANSITION_SHA256:
        raise AdapterError("Reviewed research transition changed", "access")
    return validate_budget(state)


def legacy_owned_state(value):
    """Convert only the authentic latest six-key owner, preserving unknowns."""
    digest = hashlib.sha256(
        json.dumps(value, sort_keys=True,
                   separators=(",", ":")).encode()).hexdigest()
    if digest != _LEGACY_OWNED_STATE_SHA256:
        raise AdapterError("Legacy owned publisher state changed", "access")
    value = validate_legacy_budget(value)
    result = empty_budget(value["observed_at"])
    result.update(
        observed_at=value["observed_at"],
        until=value["not_before"],
        interval=max(value["interval"], 12.2),
        page_gap=max(value["page_interval"], 30),
        starts=list(value["starts"]),
        blocked=value["blocked"],
        completed=value["observed_at"],
        inventory_completed=value["observed_at"],
        observed_429_since_transition=value["rate_failures"],
        rate_limit_until=value["not_before"],
        legacy_rate_history_unknown=value["legacy_rate_history_unknown"],
    )
    return validate_budget(result)


def source_revision_digest(head, source):
    if source != SOURCE_ID or not re.fullmatch(r"[0-9a-f]{40}", head or ""):
        raise AdapterError("Invalid producer source revision", "access")
    value = workflow_api(f"/contents/adapters/{source}/adapter.py?ref={head}")
    if (not isinstance(value, dict) or value.get("type") != "file" or
            value.get("path") != f"adapters/{source}/adapter.py" or
            value.get("encoding") != "base64" or
            type(value.get("size")) is not int or
            not 0 < value["size"] <= 5_000_000):
        raise AdapterError("Invalid immutable producer source", "access")
    try:
        raw = base64.b64decode("".join(value["content"].split()), validate=True)
    except (KeyError, ValueError, TypeError):
        raise AdapterError("Invalid immutable source bytes", "access") from None
    if len(raw) != value["size"]:
        raise AdapterError("Incomplete immutable producer source", "access")
    return hashlib.sha256(raw).hexdigest()


def download_inert_artifact(identity, artifact_id, digest=None):
    """Keep the original authenticated ZIP boundary and strict owner decoder."""
    token = os.environ.get("GITHUB_TOKEN", "")
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != 302:
                raise ValueError("Expected artifact redirect")
            destination = response.headers.get("Location", "")
            response.read(1001)
        infrastructure_url(destination, storage_only=True)
        status, _, body = request_bytes(destination, byte_limit=32768)
        if (status != 200 or len(body) > 32768 or not isinstance(digest, str) or
                not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or
                digest[7:] != hashlib.sha256(body).hexdigest()):
            raise ValueError("Invalid or mismatched artifact response")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "pacing-state.json":
                raise ValueError("Unexpected artifact members")
            entry = entries[0]
            if entry.file_size > 16384 or entry.flag_bits & 1:
                raise ValueError("Oversized or encrypted state")
            if entry.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("State symlink")
            raw = archive.read(entry)
        envelope = json.loads(raw)
        common = {
            "schema", "repository", "run_id", "run_attempt", "source", "state"
        }
        modern = common | {"head_sha", "adapter_sha256", "state_sha256"}
        if not isinstance(envelope, dict) or set(envelope) not in (common,
                                                                   modern):
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source = identity
        if (type(envelope["schema"]) is not int or envelope["schema"] != 1 or
                envelope["repository"] != "YouthOpps/data-pipeline" or
                type(envelope["run_id"]) is not int or
                type(envelope["run_attempt"]) is not int or
                envelope["run_id"] != run_id or
                envelope["run_attempt"] != attempt or
                envelope["source"] != source or source != SOURCE_ID):
            raise ValueError("Artifact ownership mismatch")
        if set(envelope) == common:
            if (identity != (43918525358, 1, SOURCE_ID) or
                    artifact_id != 14252313258 or
                    digest != "sha256:" + _LEGACY_OWNED_ZIP_SHA256 or
                    _ATTEMPT_HEADS.get(
                        (run_id, attempt)) != _LEGACY_OWNED_HEAD):
                raise ValueError("Unreviewed legacy owner")
            return legacy_owned_state(envelope["state"])
        head = _ATTEMPT_HEADS.get((run_id, attempt))
        if (envelope["head_sha"] != head or
                envelope["adapter_sha256"] != source_revision_digest(
                    head, source) or
                envelope["state_sha256"] != hashlib.sha256(
                    json.dumps(envelope["state"],
                               sort_keys=True,
                               separators=(",", ":")).encode()).hexdigest()):
            raise ValueError("Modern producer or state digest mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError("Invalid or unavailable family pacing artifact",
                           "access") from None


def production_maintenance_state(incoming):
    """Production remains closed until final native MAX handoff is reviewed."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return incoming
    raise AdapterError("Final native publisher MAX handoff is not yet approved",
                       "access")


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED, _VERIFIED_REMOTE_CREATE
    latest = latest_family_run()
    if latest is None:
        raise AdapterError("Mature family history is missing", "access")
    run_id, attempt, source = latest
    expected = _ARTIFACT_NAME + "-" + str(attempt)
    result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list) or result.get("total_count") != len(
            artifacts) or len(artifacts) > 100:
        raise AdapterError("Incomplete family artifact inventory", "access")
    matches = [item for item in artifacts if item.get("name") == expected]
    if len(matches) != 1 or matches[0].get("expired") is not False:
        raise AdapterError("Newest family pacing artifact missing or expired",
                           "access")
    artifact = matches[0]
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or
            artifact.get("workflow_run", {}).get("id") != run_id or
            artifact.get("workflow_run", {}).get("head_sha")
            != _ATTEMPT_HEADS.get(
                (run_id, attempt)) or (run_id, attempt) not in _ATTEMPT_HEADS or
            not re.fullmatch(r"sha256:[0-9a-f]{64}",
                             artifact.get("digest") or "") or
            type(artifact.get("size_in_bytes")) is not int or
            not 0 < artifact["size_in_bytes"] <= 32768):
        raise AdapterError("Invalid family artifact binding", "access")
    incoming = download_inert_artifact(latest, artifact["id"],
                                       artifact["digest"])
    incoming = production_maintenance_state(incoming)
    incoming = merge_budget(incoming, reviewed_research_transition())
    _VERIFIED_REMOTE_CREATE = copy.deepcopy(incoming)
    with locked_budget():
        local = load_budget()
        combined = merge_budget(local, incoming)
        if combined["in_flight"]:
            raise AdapterError("Unsettled publisher request retained", "access")
        combined["until"] = max(combined["until"], time.time() + 60)
        combined["observed_at"] = time.time()
        write_budget(combined)
    _RESTORED = _STATE_TRUSTED = True


def verify_local_publisher_start():
    global _LOCAL_FIRST_ACCESS_CHECKED
    if os.environ.get(
            "GITHUB_ACTIONS") == "true" or _LOCAL_FIRST_ACCESS_CHECKED:
        return
    verify_local_workflow_pause()
    _LOCAL_FIRST_ACCESS_CHECKED = True


def wait_startup_quiet():
    if _STARTUP_QUIET_UNTIL is None:
        raise AdapterError("Publisher startup quiet is unproved", "access")
    while time.monotonic() < _STARTUP_QUIET_UNTIL:
        check_deadline()
        time.sleep(max(0, min(1, _STARTUP_QUIET_UNTIL - time.monotonic())))


def authenticate_hosted_run():
    run_id, attempt = current_run_identity()
    value = workflow_api(f"/actions/runs/{run_id}/attempts/{attempt}")
    head = os.environ.get("GITHUB_SHA")
    if (value.get("id") != run_id or value.get("run_attempt") != attempt or
            value.get("head_sha") != head or
            value.get("head_branch") != "main" or
            value.get("workflow_id") != _LOCAL_WORKFLOW_ID or
            family_source(value) != SOURCE_ID or
            source_revision_digest(head, SOURCE_ID) != source_file_digest()):
        raise AdapterError("Untrusted current producer implementation",
                           "access")
    result = workflow_api(
        f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    jobs = result.get("jobs")
    if result.get("total_count") != 1 or not isinstance(jobs,
                                                        list) or len(jobs) != 1:
        raise AdapterError("Current producer job is missing or ambiguous",
                           "access")
    job = jobs[0]
    if (job.get("run_id") != run_id or job.get("run_attempt") != attempt or
            job.get("head_sha") != head or job.get("head_branch") != "main" or
            job.get("name") != "collect" or job.get("status") != "in_progress"):
        raise AdapterError("Untrusted current collect job", "access")


def require_existing_local_state():
    """Retain the exact mature local journal across remote metadata reads."""
    directory = os.path.join(tempfile.gettempdir(),
                             FAMILY + "-" + str(os.getuid()))
    try:
        # Pin the existing parent before budget_file can consider creation.
        state_parent(directory)
        budget_file()
        # Keep the original RW descriptor and acknowledged generation after
        # this scope releases its flock. Later restoration cannot adopt an
        # older named replacement or a changed in-place generation.
        with locked_budget():
            state = load_budget()
            if state["in_flight"]:
                raise AdapterError("Local publisher request is unsettled",
                                   "access")
    except BaseException:
        fail_publisher_cleanup()
        raise


def prepare_collection():
    global _COLLECTION_LOCK_FD, _COLLECTION_LOCK_CONTEXT, _COLLECTION_STARTED
    global _LOCAL_FIRST_ACCESS_CHECKED, _STARTUP_QUIET_UNTIL, _RESTORED
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher persistence gate is sticky", "access")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        authenticate_hosted_run()
    else:
        require_existing_local_state()
        verify_local_workflow_pause()
    budget_file()
    if _COLLECTION_LOCK_FD is not None:
        raise AdapterError("Nested publisher collection refused", "access")
    parent = state_parent()
    path = os.path.join(_STATE_PARENT_PATH, "collection.lock")
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if _INITIAL_DIRECTORY_PROVEN:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = info = None
    try:
        descriptor = os.open("collection.lock", flags, 0o600, dir_fd=parent)
        info = os.fstat(descriptor)
        validate_owned_output(info)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat("collection.lock",
                          dir_fd=parent,
                          follow_symlinks=False)
        if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Publisher lifecycle changed during acquisition")
        guard = os.dup(descriptor)
    except BaseException:
        fail_publisher_cleanup()
        if descriptor is not None and info is not None:
            with contextlib.suppress(BaseException):
                close_retained_descriptor(descriptor,
                                          (info.st_dev, info.st_ino))
        raise AdapterError("Unsafe or active publisher lifecycle",
                           "access") from None
    _COLLECTION_LOCK_FD = descriptor
    _COLLECTION_LOCK_CONTEXT = (descriptor, guard, (info.st_dev, info.st_ino),
                                path)
    collection_lock_authority()
    _LOCAL_FIRST_ACCESS_CHECKED = False
    _COLLECTION_STARTED = time.time()
    _STARTUP_QUIET_UNTIL = time.monotonic() + 60
    _RESTORED = False
    restore_family_artifact()
    preserve_legacy_pacing()


def infrastructure_url(url, *, storage_only=False):
    """Keep every metadata hop outside publisher transports and policies."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname or ""
        storage = any(
            host.endswith(suffix) and host != suffix[1:]
            for suffix in (".blob.core.windows.net",
                           ".actions.githubusercontent.com"))
        if (not isinstance(url, str) or any(ord(c) <= 32 for c in url) or
                parsed.scheme != "https" or parsed.username is not None or
                parsed.password is not None or parsed.netloc != host or
                parsed.port is not None or parsed.fragment or
                host.endswith(".") or not re.fullmatch(
                    r"[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?)*",
                    host) or
                not (storage or not storage_only and host == "api.github.com")):
            raise ValueError("Unsupported infrastructure host")
    except (TypeError, ValueError):
        raise AdapterError("Untrusted GitHub infrastructure endpoint",
                           "access") from None
    return parsed


def infrastructure_request_bytes(url,
                                 method="GET",
                                 headers=None,
                                 payload=None,
                                 byte_limit=30000000):
    """Retain the existing bounded GitHub API and signed-ZIP transport."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    initial = infrastructure_url(url)
    storage_only = initial.hostname != "api.github.com"
    if storage_only:
        headers = {
            k: v
            for k, v in headers.items()
            if k.lower() not in ("authorization", "cookie")
        }
    current = url
    for redirect in range(6):
        check_deadline()
        infrastructure_url(current, storage_only=storage_only)
        request = urllib.request.Request(current,
                                         data=payload,
                                         headers=headers,
                                         method=method)
        try:
            response = _OPENER.open(request, timeout=request_timeout())
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, response_headers = response.status, response.headers
            location = response_headers.get("Location")
            declared = response_headers.get("Content-Length")
            if declared and (not declared.isdigit() or
                             int(declared) > byte_limit):
                raise AdapterError("Oversized or invalid response length",
                                   "fetch")
            body = response.read(byte_limit + 1)
            if len(body
                  ) > byte_limit or declared and len(body) != int(declared):
                raise AdapterError("Incomplete or oversized response", "fetch")
            if response_headers.get("Content-Encoding",
                                    "identity") != "identity":
                raise AdapterError("Unsupported response content encoding",
                                   "fetch")
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            if not storage_only:
                raise AdapterError("Unexpected GitHub API redirect", "publish")
            destination = urllib.parse.urljoin(current, location)
            infrastructure_url(destination, storage_only=True)
            headers = {
                k: v
                for k, v in headers.items()
                if k.lower() not in ("authorization", "cookie")
            }
            current = destination
            continue
        return status, response_headers, body
    raise AdapterError("Unresolved infrastructure redirect", "access")


def request_bytes(url,
                  method="GET",
                  headers=None,
                  payload=None,
                  publisher=False,
                  byte_limit=30000000,
                  robots=False,
                  inventory=False):
    """Preserve FRSE's call contract while each source hop owns pending state."""
    global _INVENTORY_ACTIVE
    if not publisher:
        return infrastructure_request_bytes(url, method, headers, payload,
                                            byte_limit)
    current = public_url(url)
    if method != "GET" or payload is not None:
        raise AdapterError("Invalid public publisher request method", "access")
    for redirect in range(6):
        check_deadline()
        parsed = urllib.parse.urlsplit(public_url(current))
        if robots and (parsed.path != "/robots.txt" or parsed.query or
                       inventory):
            raise AdapterError("Invalid publisher robots purpose", "access")
        if any(k.lower() == "cookie" for k in (headers or {})) and (
                current != url or parsed.netloc != "www.frse.org.pl" or
                not re.fullmatch(
                    r"/pioro/mycalendar2/get-calendar-content/[A-Za-z0-9_-]+",
                    parsed.path)):
            raise AdapterError(
                "Anonymous calendar session escaped its public route", "access")
        previous = _INVENTORY_ACTIVE
        _INVENTORY_ACTIVE = inventory and not robots
        try:
            status, response_headers, body = physical_request_bytes(
                current,
                method=method,
                headers=headers,
                payload=payload,
                robots=robots,
                byte_limit=byte_limit,
            )
        finally:
            _INVENTORY_ACTIVE = previous
        if not robots and status in (401, 403, 451):
            raise AdapterError(f"Publisher refused access: HTTP {status}",
                               "access", status)
        if status in (301, 302, 303, 307, 308):
            location = response_headers.get("Location")
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            new = urllib.parse.urlsplit(public_url(destination))
            if robots and (new.path != "/robots.txt" or new.query):
                raise AdapterError("Invalid publisher redirect purpose",
                                   "access")
            if not robots:
                check_robots(destination)
            current = destination
            continue
        return status, response_headers, body
    raise AdapterError("Unresolved publisher redirect", "access")


def fetch_source(url, min_interval=6, *, policy_response=False):
    """The modern REP reader uses the unchanged bounded source transport."""
    return request_bytes(url,
                         publisher=True,
                         robots=policy_response,
                         byte_limit=30000000)


def preserve_legacy_pacing():
    """Lock bound legacy state through read and merge, retaining its inode."""
    descriptors = []
    try:
        parent = os.open(
            tempfile.gettempdir(),
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        descriptors.append(parent)
        for name in ("youthopps-source-triage", "37"):
            try:
                directory = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent,
                )
            except FileNotFoundError:
                return
            descriptors.append(directory)
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise AdapterError("Unsafe legacy pacing directory", "access")
            parent = directory
        try:
            descriptor = os.open("pacing.json",
                                 os.O_RDONLY | os.O_NOFOLLOW,
                                 dir_fd=parent)
        except FileNotFoundError:
            return
        descriptors.append(descriptor)
        info = os.fstat(descriptor)
        if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or
                info.st_mode & 0o077 or info.st_nlink != 1):
            raise AdapterError("Unsafe legacy FRSE pacing state", "access")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AdapterError("Legacy FRSE collector active",
                               "access") from None
        raw = os.read(descriptor, 16385)
        try:
            legacy = json.loads(raw)
            if (len(raw) > 16384 or not isinstance(legacy, dict) or
                    not {"starts", "interval", "until"} <= set(legacy) or
                    set(legacy) - {"starts", "interval", "until", "blocked"} or
                    not numeric(legacy["until"]) or
                    not numeric(legacy["interval"]) or legacy["interval"] < 6 or
                    type(legacy.get("blocked", False)) is not bool or
                    not isinstance(legacy["starts"], list) or
                    len(legacy["starts"]) > 10 or
                    any(not numeric(value) for value in legacy["starts"]) or
                    legacy["starts"] != sorted(set(legacy["starts"])) or
                    any(value > time.time() + 1 for value in legacy["starts"])):
                raise ValueError("Invalid legacy timing history")
        except (ValueError, TypeError, KeyError):
            raise AdapterError("Invalid legacy FRSE pacing state",
                               "access") from None
        with locked_budget():
            state = load_budget()
            state["interval"] = max(state.get("interval", 6),
                                    legacy["interval"])
            state["until"] = max(state["until"], legacy["until"])
            state["blocked"] = state["blocked"] or legacy.get("blocked", False)
            state["starts"] = sorted(set(state["starts"] + legacy["starts"]))
            if state["starts"]:
                state["until"] = max(
                    state["until"],
                    state["starts"][-1] + state["interval"],
                )
            state["observed_at"] = time.time()
            save_budget(state)

    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


_LEGACY_LOCAL_STATE_SHA256 = '65b884a839e7682b478ea5f6cd5f7dc62eb8ebd57746922e489fb68cb8ac56b6'
_RESEARCH_TRANSITION_SHA256 = '48d17551b5437d62817c1c0bdf8f2cd434a92f859b7d03367011259953dbf553'
_LEGACY_OWNED_STATE_SHA256 = 'd2fb0f31e5d16a9585f0c54ff0f0ec3c81ca69b703116dcbb016de5825b489f9'
_LEGACY_OWNED_ZIP_SHA256 = '6ec4a6eab273ecfc173b2d442521a169ee8d8a272f4cb8b3fb9b3c0e13f92469'
_LEGACY_OWNED_HEAD = 'dd15bee13fc772ecbf6d72fa19ea02f0f6ffd31a'
_RESEARCH_TRANSITION = {
    'blocked': False,
    'completed': 1794903508.6156049,
    'family': 'youthopps-frse-publisher-v1',
    'in_flight': False,
    'interval': 12.2,
    'inventory_completed': 1794903508.615189,
    'legacy_429_history': 'unknown',
    'legacy_access_history_unknown': True,
    'legacy_access_observations': [],
    'legacy_hold_history': 'unlogged_fields_unknown',
    'legacy_rate_history_unknown': True,
    'observed_429_since_transition': 0,
    'observed_at': 1794903508.6156049,
    'page_gap': 30,
    'rate_limit_until': 1794903520.815605,
    'research_provenance': {
        'assessed_http500': {
            'body_sha256':
                '598f6e8a8b393bc86654cbbda78ac42c88bd8fc2bfd86dadc95863722c1f7116',
            'capture':
                8843,
            'closed_response':
                True,
            'historical_one_use_guard_pending_preserved':
                True
        },
        'captures': [{
            'controller_sha256':
                'ba0a3ed96fa19bc1857bfd55ffc5ccf9c787796aacde75606d88f712699262a1',
            'guard_pending_retained':
                False,
            'outcome':
                'success',
            'physical_requests':
                4,
            'port':
                8840,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }, {
            'controller_sha256':
                '988d2452c01ddc80e10906743f1f1761096155751e5bafccad6b2b118f82bc90',
            'guard_pending_retained':
                False,
            'outcome':
                'success',
            'physical_requests':
                2,
            'port':
                8841,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }, {
            'controller_sha256':
                '0be4b5c2b45f26adfed06ab044e6d92aaf9eecde64c3353b7015a768a94ab7f6',
            'guard_pending_retained':
                False,
            'outcome':
                'success',
            'physical_requests':
                2,
            'port':
                8842,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }, {
            'controller_sha256':
                'd242515056aa0c9a1e40f214c31684076c9a74898cfff6b8f1fa0ecf7b2faddb',
            'guard_pending_retained':
                True,
            'outcome':
                'AdapterError',
            'physical_requests':
                2,
            'port':
                8843,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }, {
            'controller_sha256':
                '63aa134abc9d0c72a9d1c5214661184d59dac2fe6b83ff811e1df992739d6c23',
            'guard_pending_retained':
                False,
            'outcome':
                'success',
            'physical_requests':
                3,
            'port':
                8844,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }, {
            'controller_sha256':
                '1a0602a6db9aeab3626b834553d6b1b624f3b4966596fe08aaa535072a988145',
            'guard_pending_retained':
                False,
            'outcome':
                'success',
            'physical_requests':
                4,
            'port':
                8845,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }, {
            'controller_sha256':
                '4008a71f4bcd1bbd7a6a47cfb2cc00adf33c8166a2969f850f291a7c202ee4cc',
            'guard_pending_retained':
                False,
            'outcome':
                'success',
            'physical_requests':
                5,
            'port':
                8846,
            'source_sha256':
                '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e'
        }],
        'issue':
            'https://github.com/YouthOpps/data-pipeline/issues/384',
        'legacy_local_sha256':
            '65b884a839e7682b478ea5f6cd5f7dc62eb8ebd57746922e489fb68cb8ac56b6',
        'original_source_sha256':
            '62d601cd7e4a3dc4b8afe5e6bec3419e9feda9d28f3c65b8b801121b00deb93e',
        'owned_artifact_id':
            14252313258,
        'owned_run_attempt':
            1,
        'owned_run_id':
            43918525358,
        'owned_zip_sha256':
            '6ec4a6eab273ecfc173b2d442521a169ee8d8a272f4cb8b3fb9b3c0e13f92469'
    },
    'schema': 3,
    'starts': [
        1794850471.5614612, 1794850502.9309325, 1794901180.4157813,
        1794901180.990424, 1794901194.6395948, 1794901194.6403072,
        1794901209.4050107, 1794901209.4067895, 1794901223.6452804,
        1794901223.645775, 1794901511.919073, 1794901512.5181725,
        1794901526.215107, 1794901526.2156405, 1794901815.2002075,
        1794901816.6339424, 1794901829.9469776, 1794901829.948365,
        1794902038.829045, 1794902039.9847674, 1794902054.21241,
        1794902054.2131186, 1794902525.3217409, 1794902526.422226,
        1794902539.8254902, 1794902539.8260634, 1794902554.0170557,
        1794902554.0195885, 1794903000.3604517, 1794903001.6298578,
        1794903015.208314, 1794903015.2096913, 1794903028.5587125,
        1794903028.5592196, 1794903041.9237428, 1794903041.9248865,
        1794903396.2923243, 1794903397.4153707, 1794903410.9364877,
        1794903410.9375012, 1794903443.3047173, 1794903443.3051763,
        1794903475.2894254, 1794903475.2904384, 1794903507.1021514,
        1794903507.1030905
    ],
    'until': 1794903520.815605
}


def verify_retained_flock(descriptor, path):
    """Prove the original open-file description still holds its flock."""
    held = os.fstat(descriptor)
    probe = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        current = os.fstat(probe)
        validate_owned_output(current)
        if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
            raise AdapterError("Locked object changed", "access")
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another open-file description must hold the lock. Prove it is
            # ours, rather than a competing collector that acquired a gap.
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            raise AdapterError("Original publisher lock was released", "access")
    finally:
        os.close(probe)


if __name__ == "__main__":
    raise SystemExit(main())
