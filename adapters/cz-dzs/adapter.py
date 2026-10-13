"""Discover and collect published ESC grants and educational offers."""

import argparse
import base64
import contextlib
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import io
import ipaddress
import json
import math
import os
import re
import signal
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

SOURCE_ID = "cz-dzs"
SOURCE_URL = (
    "https://www.dzs.cz/program/evropsky-sbor-solidarity/projekty-granty"
)
WEBSITE_URL = "https://www.dzs.cz/en"
LANGUAGE = "cs"
PUBLISHER_COUNTRY = "CZ"
PUBLISHER_TYPE = "government"
ATTRIBUTION = (
    (
        'Dům zahraniční spolupráce (DZS), Czech National Agency '
        'for International Education and Research; European Comm'
        'ission/EACEA; SALTO-YOUTH https://www.salto-youth.net/'
        ' and the named training organisers'
    )
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
_USER_AGENT = (
    "YouthOpps/1.0 (+https://github.com/YouthOpps/data-pipeline; "
    "public opportunity metadata)"
)
CATEGORIES = {"grants", "training"}
FAMILY = "youthopps-dzs-publisher-v1"
FAMILY_SOURCES = {"cz-study-in-czechia", "cz-dzs"}
COLLECTION_TIMEOUT = 900
RUN_TIMEOUT = 1290
EXPORT_TIMEOUT = 30
_STATE_SCHEMA = 1
_ARTIFACT_NAME = "dzs-pacing-state"
_STATE_PATH = _STATE_LOCK = None
_COLLECTION_LOCK_FD = None
_COLLECTION_STARTED = None
_RESTORED = _PUBLISHING = _STATE_TRUSTED = False
_ROBOTS = {}
_RUN_END = _PHASE_END = None
_PHASE_NAME = None

class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
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
        n
        for n in node.walk()
        if (tag is None or n.tag == tag)
        and (
            class_name is None or class_name in n.attrs.get("class", "").split()
        )
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
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def safe_error(error):
    text = str(error)
    text = re.sub(
        r'https?://[^\s<>"\']+',
        lambda match: urllib.parse.urlunsplit(
            (
                urllib.parse.urlsplit(match[0]).scheme,
                urllib.parse.urlsplit(match[0]).hostname or "",
                urllib.parse.urlsplit(match[0]).path,
                "",
                "",
            )
        ),
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
        if _COLLECTION_LOCK_FD is not None:
            try:
                os.close(_COLLECTION_LOCK_FD)
            except OSError:
                pass
            _COLLECTION_LOCK_FD = None


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

def numeric(value):
    try:
        return (
            type(value) in (int, float)
            and math.isfinite(value) and value >= 0
        )
    except OverflowError:
        return False


def validate_budget(state):
    """Validate inert state without age-expiring a future publisher embargo."""
    if not isinstance(state, dict) or set(state) != {
        "schema", "family", "observed_at", "not_before", "starts", "blocked",
    }:
        raise AdapterError("Invalid publisher pacing state", "access")
    if (type(state["schema"]) is not int or state["schema"] != _STATE_SCHEMA
            or state["family"] != FAMILY
            or not numeric(state["observed_at"])
            or not numeric(state["not_before"])
            or type(state["blocked"]) is not bool
            or not isinstance(state["starts"], list)
            or len(state["starts"]) > 10
            or any(not numeric(t) for t in state["starts"])
            or state["starts"] != sorted(state["starts"])
            or len(set(state["starts"])) != len(state["starts"])
            or any(t > state["observed_at"] for t in state["starts"])):
        raise AdapterError("Invalid publisher pacing history", "access")
    if time.time() + 1 < state["observed_at"]:
        raise AdapterError("Publisher pacing clock moved backwards", "access")
    return state


def empty_budget(now):
    return {
        "schema": _STATE_SCHEMA, "family": FAMILY, "observed_at": now,
        "not_before": now, "starts": [], "blocked": False,
    }


def budget_file():
    global _STATE_PATH, _STATE_LOCK
    if _STATE_PATH:
        return
    directory = os.path.join(tempfile.gettempdir(), FAMILY + (
        "-"
    ) + str(os.getuid()))
    try:
        os.mkdir(directory, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(directory)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise AdapterError("Unsafe publisher pacing directory", "access")
    if info.st_mode & 0o077:
        raise AdapterError("Unsafe publisher pacing permissions", "access")
    _STATE_PATH = os.path.join(directory, "state.json")
    _STATE_LOCK = os.path.join(directory, "state.lock")


def locked_budget():
    budget_file()
    descriptor = os.open(
        _STATE_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    info = os.fstat(descriptor)
    if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077 or info.st_nlink != 1):
        os.close(descriptor)
        raise AdapterError("Unsafe publisher pacing lock", "access")
    file = os.fdopen(descriptor, "a+")
    fcntl.flock(file, fcntl.LOCK_EX)
    return file


def load_budget():
    try:
        fd = os.open(_STATE_PATH, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return empty_budget(time.time())
    with os.fdopen(fd, "rb") as file:
        info = os.fstat(file.fileno())
        if (info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise AdapterError("Unsafe publisher pacing state owner", "access")
        raw = file.read(16385)
    if len(raw) > 16384:
        raise AdapterError("Oversized publisher pacing state", "access")
    try:
        return validate_budget(json.loads(raw))
    except (ValueError, TypeError):
        raise AdapterError("Corrupt publisher pacing state", "access") from None


def save_budget(state):
    validate_budget(state)
    fd, name = tempfile.mkstemp(prefix=(
        "pending-"
    ), dir=os.path.dirname(_STATE_PATH))
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, _STATE_PATH)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def pace():
    (
        "Serialize every publisher request start across the complete "
        "origin set."
    )
    check_deadline()
    with locked_budget():
        state = load_budget()
        if state["blocked"]:
            raise AdapterError((
                "Publisher backoff requires reviewed recovery"
            ), (
                "access"
            ))
        now = time.time()
        starts = [t for t in state["starts"] if t > now - 60]
        target = max(now, state["not_before"])
        if starts:
            target = max(target, starts[-1] + 6)
        if len(starts) == 10:
            target = max(target, starts[0] + 60)
        if target + 120 > time.time() + (
            _PHASE_END - time.monotonic()
        ):
            raise AdapterError("Publisher backoff exceeds phase budget",
                               "access")
        if _COLLECTION_STARTED is not None and (
            target + 120 > _COLLECTION_STARTED + COLLECTION_TIMEOUT
        ):
            raise AdapterError((
                "Publisher backoff exceeds collection budget"
            ), (
                "access"
            ))
        if target > now:
            time.sleep(target - now)
        now = time.time()
        if now + 0.001 < target:
            raise AdapterError((
                "Publisher pacing clock moved backwards"
            ), (
                "access"
            ))
        state["starts"] = [t for t in starts if t > now - 60] + [now]
        state["observed_at"] = now
        save_budget(state)


def record_retry_after(value):
    """Persist the embargo before reporting any refusal or retrieval failure."""
    if value is None:
        return
    with locked_budget():
        state = load_budget()
        now = time.time()
        try:
            if re.fullmatch(r"[0-9]+", value.strip()):
                until = now + int(value)
            else:
                parsed = parsedate_to_datetime(value)
                if parsed.utcoffset() is None:
                    raise ValueError("Missing timezone")
                until = parsed.timestamp()
            if not numeric(until):
                raise ValueError("Invalid embargo")
            state["not_before"] = max(state["not_before"], until)
        except Exception:
            state["blocked"] = True
        state["observed_at"] = now
        save_budget(state)


def request_bytes(url, method="GET", headers=None, payload=None,
                  publisher=False, byte_limit=30000000):
    """Bound response bytes; redirects are explicit and credential-safe."""
    headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    current = url
    for redirect in range(6):
        check_deadline()
        if publisher:
            public_url(current)
            pace()
        request = urllib.request.Request(
            current, data=payload, headers=headers, method=method
        )
        try:
            response = _OPENER.open(request, timeout=request_timeout(publisher))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status = response.status
            response_headers = response.headers
            retry = response.headers.get("Retry-After")
            if publisher:
                record_retry_after(retry)
            declared = response.headers.get("Content-Length")
            if declared and (
                not declared.isdigit() or int(declared) > byte_limit
            ):
                raise AdapterError((
                    "Oversized or invalid response length"
                ), (
                    "fetch"
                ))
            body = response.read(byte_limit + 1)
            if (len(body) > byte_limit
                    or declared and len(body) != int(declared)):
                raise AdapterError("Incomplete or oversized response", "fetch")
            encoding = response.headers.get("Content-Encoding", "identity")
            if encoding != "identity":
                raise AdapterError((
                    "Unsupported response content encoding"
                ), (
                    "fetch"
                ))
            location = response.headers.get("Location")
        if status in (301, 302, 303, 307, 308):
            if not location or redirect == 5:
                raise AdapterError("Invalid redirect chain", "access")
            destination = urllib.parse.urljoin(current, location)
            old, new = (
                urllib.parse.urlsplit(current),
                urllib.parse.urlsplit(destination),
            )
            if publisher:
                public_url(destination)
                if old.scheme == "https" and new.scheme != "https":
                    raise AdapterError((
                        "Publisher redirect transport downgrade"
                    ), (
                        "access"
                    ))
                check_robots(destination)
            else:
                if new.scheme != "https" or new.username or new.password:
                    raise AdapterError((
                        "Unsafe authenticated redirect"
                    ), (
                        "publish"
                    ))
                if new.netloc != old.netloc:
                    headers = {key: value for key, value in headers.items()
                               if key.lower() != "authorization"}
                if urllib.parse.urlsplit(url).hostname == "api.github.com":
                    raise AdapterError((
                        "Unexpected GitHub API redirect"
                    ), (
                        "publish"
                    ))
            current = destination
            continue
        if publisher and status in (401, 403, 429):
            refused = urllib.parse.urlsplit(current)
            provenance = urllib.parse.urlunsplit((
                refused.scheme, refused.hostname or "", refused.path, "", ""))
            raise AdapterError(
                f"Publisher refused access: HTTP {status}: " + provenance,
                "access", status)
        return status, response_headers, body
    raise AdapterError("Unresolved redirect", "access")


def check_robots(url):
    import urllib.robotparser
    parsed = urllib.parse.urlsplit(public_url(url))
    origin = parsed.scheme + "://" + parsed.netloc
    if parsed.path == "/robots.txt":
        return
    if origin not in _ROBOTS:
        status, _, body = request_bytes(origin + "/robots.txt", publisher=True)
        if status == 404:
            _ROBOTS[origin] = None
        elif status == 200:
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(body.decode("utf-8", "strict").splitlines())
            _ROBOTS[origin] = parser
        else:
            raise AdapterError((
                "Publisher robots policy unavailable"
            ), (
                "access"
            ), status)
    parser = _ROBOTS[origin]
    if parser is not None and not parser.can_fetch(_USER_AGENT, url):
        raise AdapterError("Publisher robots excludes required input", "access")
    if parser is not None:
        delay = parser.crawl_delay(_USER_AGENT)
        if delay and delay > 6:
            with locked_budget():
                state = load_budget()
                if state["starts"]:
                    state[(
                        "not_before"
                    )] = max(state[(
                        "not_before"
                    )], state[(
                        "starts"
                    )][-1] + delay)
                    save_budget(state)
        rate = parser.request_rate(_USER_AGENT)
        if rate and rate.seconds / rate.requests > 6:
            raise AdapterError((
                "Stricter publisher request-rate requires review"
            ), (
                "access"
            ))


def fetch_public(url):
    reviewed = public_url(url)
    check_robots(reviewed)
    for attempt in range(2):
        status, headers, body = request_bytes(reviewed, publisher=True)
        if status == 200:
            return status, headers, body
        if status == 503 and attempt == 0:
            with locked_budget():
                state = load_budget()
                now = time.time()
                state["not_before"] = max(state["not_before"], now + 30)
                state["observed_at"] = now
                save_budget(state)
            continue
        parsed = urllib.parse.urlsplit(reviewed)
        provenance = parsed.scheme + "://" + parsed.netloc + parsed.path
        raise AdapterError(
            f"Required publisher input returned HTTP {status}: "
            + provenance, "fetch", status)


def workflow_api(path):
    """Read authenticated workflow metadata without publication permissions."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = request_bytes(
            _WORKFLOW_GITHUB + path,
            headers={"Authorization": "Bearer " + token,
                     "Accept": "application/vnd.github+json"},
        )
        if status != 200:
            raise ValueError("Workflow API status")
        return json.loads(body)
    except Exception:
        raise AdapterError((
            "Workflow pacing state unavailable"
        ), (
            "access"
        )) from None


def current_run_identity():
    if (os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise AdapterError("Unexpected workflow repository", "access")
    values = [os.environ.get(key, "") for key in ((
        "GITHUB_RUN_ID"
    ), (
        "GITHUB_RUN_ATTEMPT"
    ))]
    if any(not re.fullmatch(r"[1-9][0-9]*", value) for value in values):
        raise AdapterError("Invalid workflow run identity", "access")
    return tuple(map(int, values))


def family_source(run):
    path = run.get("path", "")
    for source in FAMILY_SOURCES:
        if path == ".github/workflows/fetch-" + source + ".yml":
            return source
    return None


def completed_identity(run, expected=None):
    """Bind a completed attempt and its chronological publisher interval."""
    try:
        source = family_source(run)
        identity, attempt = run["id"], run["run_attempt"]
        if (not source or type(identity) is not int or identity <= 0
                or type(attempt) is not int or attempt <= 0
                or run["status"] != "completed"
                or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
                or run[(
                    "head_repository"
                )][(
                    "full_name"
                )] != (
                    "YouthOpps/data-pipeline"
                )
                or run["head_branch"] != "main"
                or expected and (identity, attempt, source) != expected):
            raise ValueError("Untrusted completed attempt")
        times = []
        for field in ("run_started_at", "updated_at"):
            value = datetime.fromisoformat(run[field].replace("Z", "+00:00"))
            if value.utcoffset() is None:
                raise ValueError("Missing attempt timezone")
            times.append(value.timestamp())
        began, finished = times
        if not 0 <= began <= finished <= time.time() + 1:
            raise ValueError("Invalid attempt chronology")
        return finished, began, identity, attempt, source
    except Exception:
        raise AdapterError((
            "Untrusted completed family attempt"
        ), (
            "access"
        )) from None


class InventoryRace(AdapterError):
    """A changed advertised total permits a bounded fresh history scan."""


def latest_family_run():
    """Restart only count-drift races, without reusing partial candidates."""
    for attempt in range(3):
        try:
            return scan_family_history()
        except InventoryRace:
            if attempt == 2:
                raise AdapterError(
                    "Workflow inventory remained unstable", "access") from None


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    current_id, current_attempt = current_run_identity()
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        result = workflow_api(f"/actions/runs?per_page=100&page={page}")
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (type(count) is not int or count < 0
                or not isinstance(runs, list) or len(runs) > 100):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace((
                "Workflow inventory changed during restore"
            ), (
                "access"
            ))
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            run_id = run.get("id")
            if (type(run_id) is not int or run_id <= 0
                    or run_id in seen_ids):
                raise AdapterError(
                    "Invalid or duplicate workflow run identity", "access")
            seen_ids.add(run_id)
            source = family_source(run)
            if not source:
                continue
            if (run.get((
                "repository"
            ), {}).get((
                "full_name"
            )) != (
                "YouthOpps/data-pipeline"
            )
                    or run.get("head_repository", {}).get("full_name")
                    != (
                        "YouthOpps/data-pipeline"
                    ) or run.get((
                        "head_branch"
                    )) != (
                        "main"
                    )):
                raise AdapterError((
                    "Workflow repository or branch mismatch"
                ), (
                    "access"
                ))
            identity, attempt = run.get("id"), run.get("run_attempt")
            if type(identity) is not int or type(attempt) is not int:
                raise AdapterError("Invalid family run identity", "access")
            if identity == current_id:
                if attempt != current_attempt:
                    raise AdapterError((
                        "Current workflow attempt mismatch"
                    ), (
                        "access"
                    ))
                if current_attempt > 1:
                    prior = workflow_api((
                        f"/actions/runs/{identity}/attempts/"
                        f"{current_attempt - 1}"
                    ))
                    candidates.append(completed_identity(
                        prior, (identity, current_attempt - 1, source)))
                continue
            if run.get((
                "status"
            )) in ((
                "queued"
            ), (
                "waiting"
            ), (
                "pending"
            ), (
                "requested"
            )):
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError((
                    "Workflow inventory count mismatch"
                ), (
                    "access"
                ))
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError((
                    "Ambiguous family attempt chronology"
                ), (
                    "access"
                ))
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def download_inert_artifact(identity, artifact_id, digest=None):
    (
        "Download a bounded authenticated artifact, stripping "
        "cross-origin auth."
    )
    token = os.environ.get("GITHUB_TOKEN", "")
    url = _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip"
    request = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + token, "User-Agent": _USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
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
        parsed = urllib.parse.urlsplit(destination)
        if (parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.port):
            raise ValueError("Unsafe artifact URL")
        if ("." not in parsed.hostname
                or parsed.hostname.endswith(((
                    ".local"
                ), (
                    ".internal"
                ), (
                    ".localhost"
                )))):
            raise ValueError("Private artifact hostname")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("Literal artifact address")
        # Signed URL is never stored or included in exceptions; no auth header.
        status, _, body = request_bytes(destination, byte_limit=32768)
        if status != 200 or len(body) > 32768:
            raise ValueError("Invalid artifact response")
        if digest is not None:
            if (not isinstance(digest, str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or digest[7:] != hashlib.sha256(body).hexdigest()):
                raise ValueError("Artifact digest mismatch")
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
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema", "repository", "run_id", "run_attempt", "source", "state",
        }:
            raise ValueError("Invalid artifact envelope")
        run_id, attempt, source = identity
        if (type(envelope["schema"]) is not int or envelope["schema"] != 1
                or envelope["repository"] != "YouthOpps/data-pipeline"
                or type(envelope["run_id"]) is not int
                or type(envelope["run_attempt"]) is not int
                or envelope["run_id"] != run_id
                or envelope["run_attempt"] != attempt
                or envelope["source"] != source):
            raise ValueError("Artifact ownership mismatch")
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError((
            "Invalid or unavailable family pacing artifact"
        ), (
            "access"
        )) from None


def restore_family_artifact():
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        raw = os.environ.get("DZS_PACING_BOOTSTRAP", "")
        try:
            bootstrap = json.loads(raw)
            if (set(bootstrap) != {"schema", "family", "not_before", "evidence"}
                    or type(bootstrap["schema"]) is not int
                    or bootstrap["schema"] != 1 or bootstrap["family"] != FAMILY
                    or not numeric(bootstrap["not_before"])
                    or not isinstance(bootstrap["evidence"], str)
                    or not re.fullmatch((
                        "https://github\\.com/YouthOpps/data-pipeline/(?:is"
                        "sues|pull)/[0-9]+(?:#issuecomment-[0-9]+)?"
                    ), bootstrap[(
                        "evidence"
                    )])):
                raise ValueError("Invalid bootstrap")
            incoming = empty_budget(time.time())
            incoming[(
                "not_before"
            )] = max(time.time() + 60, bootstrap[(
                "not_before"
            )])
        except Exception:
            raise AdapterError((
                "Reviewed first-family bootstrap required"
            ), (
                "access"
            )) from None
    else:
        run_id, attempt, source = latest
        expected = _ARTIFACT_NAME + "-" + str(attempt)
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (not isinstance(artifacts, list) or result.get((
            "total_count"
        )) != len(artifacts)
                or len(artifacts) > 100):
            raise AdapterError("Incomplete family artifact inventory", "access")
        matches = [a for a in artifacts if a.get("name") == expected]
        if len(matches) != 1 or matches[0].get("expired") is not False:
            raise AdapterError((
                "Newest family pacing artifact missing or expired"
            ), (
                "access"
            ))
        artifact = matches[0]
        if (type(artifact.get("id")) is not int or artifact["id"] <= 0
                or artifact.get("workflow_run", {}).get("id") != run_id
                or not numeric(artifact.get("size_in_bytes"))
                or artifact["size_in_bytes"] > 32768):
            raise AdapterError("Invalid family artifact binding", "access")
        incoming = download_inert_artifact(latest, artifact[(
            "id"
        )], artifact.get((
            "digest"
        )))
    with locked_budget():
        local = load_budget()
        now = time.time()
        combined = empty_budget(now)
        combined[(
            "not_before"
        )] = max(local[(
            "not_before"
        )], incoming[(
            "not_before"
        )], now + 60)
        combined["blocked"] = local["blocked"] or incoming["blocked"]
        combined[(
            "starts"
        )] = sorted(set(local[(
            "starts"
        )] + incoming[(
            "starts"
        )]))[-10:]
        save_budget(combined)
    _RESTORED, _STATE_TRUSTED = True, True


def export_family_artifact():
    """Best-effort infrastructure state; never contains records or secrets."""
    path = os.environ.get("DZS_PACING_ARTIFACT_PATH")
    if not path or not _STATE_TRUSTED:
        return
    run_id, attempt = current_run_identity()
    with locked_budget():
        state = load_budget()
    envelope = {
        "schema": 1, "repository": "YouthOpps/data-pipeline",
        "run_id": run_id, "run_attempt": attempt, "source": SOURCE_ID,
        "state": state,
    }
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError((
            "Explicit runner temporary directory required"
        ), (
            "access"
        ))
    root = os.path.realpath(configured)
    if os.path.dirname(os.path.abspath(path)) != root:
        raise AdapterError("Unsafe pacing artifact path", "access")
    fd = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "w") as file:
        json.dump(envelope, file, separators=(",", ":"))


def prepare_collection():
    global _COLLECTION_STARTED, _STATE_TRUSTED, _COLLECTION_LOCK_FD
    _COLLECTION_STARTED = time.time()
    budget_file()
    if _COLLECTION_LOCK_FD is None:
        path = os.path.join(os.path.dirname(_STATE_PATH), "collection.lock")
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            if os.fstat(descriptor).st_uid != os.getuid():
                raise OSError("Wrong lock owner")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise AdapterError((
                "Another local family collector is active"
            ), (
                "access"
            )) from None
        _COLLECTION_LOCK_FD = descriptor
    if os.environ.get("GITHUB_ACTIONS") == "true":
        restore_family_artifact()
    _STATE_TRUSTED = True


def document_root(text, complete=True):
    if complete and not re.search(
            r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$",
            text, re.I):
        raise AdapterError("Incomplete public HTML", "parse")
    root = PublisherHTML(text).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(r"just a moment|access denied|attention required|"
                r"checking your browser|verify you are human", title):
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


def validate_records(records):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or not records:
        raise AdapterError(
            "Empty or invalid opportunity collection", "validate"
        )
    ids = set()
    for record in records:
        if (
            not isinstance(record, dict)
            or record.get("source") != SOURCE_ID
            or record.get("source_url") != SOURCE_URL
        ):
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
            if (
                not isinstance(record.get(field), str)
                or not record[field].strip()
            ):
                raise AdapterError("Missing record field: " + field, "validate")
        if record["id"] in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"])
        url = urllib.parse.urlsplit(record["url"])
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
        ):
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
                    elif (
                        datetime.fromisoformat(
                            value.replace("Z", "+00:00")
                        ).tzinfo
                        is None
                    ):
                        raise ValueError()
                except (ValueError, TypeError, AttributeError):
                    raise AdapterError(
                        "Invalid record timestamp: " + field, "validate"
                    )
        for field in ("tags", "host_countries", "eligible_countries"):
            values = record.get(field)
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise AdapterError("Invalid record list: " + field, "validate")
            if field != "tags" and any(
                not re.fullmatch("[A-Z]{2}", value) for value in values
            ):
                raise AdapterError("Invalid country code", "validate")
        categories = record.get("categories")
        if (
            not isinstance(categories, list)
            or not categories
            or any(value not in CATEGORIES for value in categories)
            or len(set(categories)) != len(categories)
            or record.get("category") != categories[0]
        ):
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
        if record.get("summary_language", "en") not in {"en", "cs"}:
            raise AdapterError("Invalid summary language", "validate")
        for field in ("location", "language", "publisher_country"):
            if (
                field not in record
                or record[field] is not None
                and not isinstance(record[field], str)
            ):
                raise AdapterError("Invalid record field: " + field, "validate")
        classification = record.get("classification")
        if (
            not isinstance(classification, dict)
            or not isinstance(classification.get("method"), str)
            or classification.get("status") not in ("classified", "unknown")
            or not isinstance(classification.get("evidence"), list)
            or any(
                not isinstance(value, str)
                for value in classification["evidence"]
            )
        ):
            raise AdapterError("Invalid classification evidence", "validate")


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
            _GITHUB + path, method=method, headers=headers,
            payload=(json.dumps(payload).encode()
                     if payload is not None else None),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError("GitHub connection failed; outcome unconfirmed",
                           "publish") from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(f"GitHub publication HTTP {status}",
                           "publish", status)
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
            raise AdapterError(
                "Unsupported existing source file encoding", "publish"
            )
        files[name] = (
            None
            if file is None
            else base64.b64decode(file["content"]).decode("utf-8")
        )
    if (files["data.json"] is None) != (files["metadata.json"] is None):
        raise AdapterError(
            "Incomplete remote source snapshot; no files changed", "publish"
        )
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
                    "base_tree": snapshot["tree"],
                    "tree": [
                        {
                            "path": f"datas/{SOURCE_ID}/{name}",
                            "mode": "100644",
                            "type": "blob",
                            "content": text,
                        }
                        for name, text in files.items()
                    ],
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
                {"sha": candidate, "force": False},
            )
            return
        except AdapterError:
            latest = read_snapshot()
            if candidate and (
                latest["head"] == candidate or latest["files"] == desired
            ):
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
    """Constrain discovery to public DZS and delegated training routes."""
    try:
        parsed = urllib.parse.urlsplit(value)
        path = urllib.parse.unquote(parsed.path)
        paging = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        allowed_paging = (
            parsed.hostname == "www.dzs.cz"
            and path == "/networking-activities"
            and set(paging) == {"page"}
            and len(paging["page"]) == 1
            and paging["page"][0].isdigit())
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port or parsed.query and not allowed_paging
                or any(part in {".", ".."} for part in path.split("/"))):
            raise ValueError()
        if parsed.hostname == "www.dzs.cz":
            allowed = (path == "/robots.txt"
                       or path.startswith("/program/evropsky-sbor-solidarity")
                       or path.startswith("/udalost/")
                       or path.startswith("/vyzva")
                       or path == "/networking-activities")
        elif parsed.hostname == "www.salto-youth.net":
            allowed = (path == "/robots.txt" or path.startswith(
                "/tools/european-training-calendar/training/"))
        else:
            allowed = False
        if not allowed:
            raise ValueError()
    except ValueError:
        raise AdapterError("Unsafe public source route", "access") from None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def read_html(url):
    _, headers, body = fetch_public(url)
    if headers.get("Content-Type", "").split(";")[0] != "text/html":
        raise AdapterError("Expected public HTML: " + url, "parse")
    return document_root(body.decode("utf-8", "strict"))


def links(root, base):
    for node in nodes(root, "a"):
        href = node.attrs.get("href", "")
        if href:
            yield node.text(), urllib.parse.urljoin(base, href)


def training_link(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.hostname == "www.salto-youth.net" and parsed.path.startswith(
        "/tools/european-training-calendar/training/"))


def read_pages():
    """Follow programme links and every advertised training detail."""
    programme = "https://www.dzs.cz/program/evropsky-sbor-solidarity"
    seeds = [SOURCE_URL, programme, programme + "/vyjezdy-pobyty",
             "https://www.dzs.cz/networking-activities"]
    pages = {"hubs": {}, "events": {}, "delegated": {}, "owners": {}}
    pending = list(seeds)
    while pending:
        url = pending.pop(0)
        if url in pages["hubs"]:
            continue
        if len(pages["hubs"]) >= 40:
            raise AdapterError("Programme discovery limit exceeded", "parse")
        root = read_html(url)
        pages["hubs"][url] = root
        for _, target in links(root, url):
            parsed = urllib.parse.urlsplit(target)
            if parsed.hostname != "www.dzs.cz":
                continue
            if (parsed.path.startswith("/vyzva") and not parsed.query
                    or parsed.path == "/networking-activities"):
                clean = public_url(target)
                if clean not in pages["hubs"] and clean not in pending:
                    pending.append(clean)
    own_urls = {}
    delegated_urls = {}
    for url, root in pages["hubs"].items():
        for _, target in links(root, url):
            parsed = urllib.parse.urlsplit(target)
            if (parsed.hostname == "www.dzs.cz"
                    and parsed.path.startswith("/udalost/")
                    and not parsed.query):
                own_urls[public_url(target)] = url
            elif training_link(target):
                delegated_urls[public_url(target)] = url
    for url, owner in sorted(own_urls.items()):
        root = read_html(url)
        pages["events"][url] = root
        pages["owners"][url] = owner
        for _, target in links(one(nodes(root, "main"), "event main"), url):
            if training_link(target):
                clean = public_url(target)
                delegated_urls[clean] = url
    for url, owner in sorted(delegated_urls.items()):
        pages["delegated"][url] = read_html(url)
        pages["owners"][url] = owner
    print(f"Discovery: {len(pages['hubs'])} programme/call pages; "
          f"{len(pages['events'])} DZS event details; "
          f"{len(pages['delegated'])} delegated details", file=sys.stderr)
    return pages


_MONTHS = {}
for _number, _names in enumerate((
    ("ledna", "leden", "january"), ("února", "únor", "february"),
    ("března", "březen", "march"), ("dubna", "duben", "april"),
    ("května", "květen", "may"), ("června", "červen", "june"),
    ("července", "červenec", "july"), ("srpna", "srpen", "august"),
    ("září", "september"), ("října", "říjen", "october"),
    ("listopadu", "listopad", "november"),
    ("prosince", "prosinec", "december")), 1):
    _MONTHS.update(dict.fromkeys(_names, _number))
_DATE_PATTERN = (
    r"(?P<day>\d{1,2})\.?\s*(?P<month>" + "|".join(
        sorted(_MONTHS, key=len, reverse=True)) +
    r")(?:\s+(?P<year>20\d{2}))?|"
    r"(?P<d>\d{1,2})\.\s*(?P<m>\d{1,2})\.\s*(?P<y>20\d{2})"
)


def published_dates(text, year=None):
    result = []
    for match in re.finditer(_DATE_PATTERN, text, re.I):
        if match["d"]:
            day, month, actual_year = (
                int(match["d"]), int(match["m"]), int(match["y"]))
        else:
            day = int(match["day"])
            month = _MONTHS[match["month"].lower()]
            actual_year = int(match["year"] or year or 0)
        if not actual_year:
            raise AdapterError("Published date has no evidenced year", "parse")
        try:
            result.append(datetime(actual_year, month, day).date().isoformat())
        except ValueError:
            raise AdapterError("Invalid published date", "parse") from None
    return result


# Historical identity aliases only; content always comes from fetched pages.
_EVENT_KEYS = {
    "informacni-webinar-pro-zadatele-o-solidarni-projekty-esc30-0":
        "call-event-2",
    "webinar-tipy-k-zadosti-pro-zajemce-o-solidarni-projekty-esc30-0":
        "call-event-4",
    "informacni-webinar-k-zadosti-o-grant-na-dobrovolnicke-projekty-esc51-0":
        "call-event-8",
    "tosca-volunteering-skoleni-podpora-pro-organizace-v-programu-"
    "evropsky-sbor-solidarity": "tosca",
    "od-napadu-k-projektu-5": "idea",
}
_SALTO_KEYS = {
    "14430": "delegated-1", "15280": "delegated-2",
    "15244": "delegated-3", "15320": "delegated-4",
    "15317": "delegated-5", "15300": "delegated-6",
    "15356": "delegated-7",
}
def short_excerpt(text, limit):
    """Bound quoted plain text in UTF-16 units without splitting a character."""
    text = " ".join(text.split())
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    clipped = text.encode("utf-16-le")[:max(0, limit - 3) * 2].decode(
        "utf-16-le", "ignore")
    return clipped.rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def factual_blocks(root):
    """Read body paragraphs and leaf lists, excluding navigation and contacts."""
    blocks = []

    def visit(node):
        if not isinstance(node, Node):
            return
        classes = node.attrs.get("class", "")
        if (node.tag in {"nav", "aside", "footer", "script", "style"}
                or re.search(r"breadcrumbs|timeline|steps__|post-navigation|"
                             r"entry-meta|description-date|description-tags",
                             classes)):
            return
        if node.tag in {"h2", "h3", "h4"}:
            blocks.append((node.tag, node.text()))
            return
        if (node.tag in {"p", "li"} and not any(
                nodes(node, tag) for tag in {"ul", "ol"})
                or "elementor-icon-box-content" in classes):
            text = node.text()
            if (len(text) >= 30 and "@" not in text
                    and not re.search(r"newsletter|Contact for questions|"
                                      r"All new training offers", text, re.I)):
                blocks.append(("body", text))
            return
        for child in node.children:
            visit(child)

    visit(root)
    return blocks


def describe(record, blocks, language, timing):
    """Index brief publisher description, audience and funding excerpts."""
    bodies = list(dict.fromkeys(text for tag, text in blocks if tag == "body"))
    selected = bodies[:1]
    patterns = (
        r"ve věku|rezident|žijí|určen|eligible|aged|persons|open to|"
        r"applicants|beneficiar|participants.{0,25}(?:from|who)|ηλικ|δικαι",
        r"\d[\d ,.]*\s*(?:eur|€)|hrazen|bezplat|zdarma|"
        r"participation fee|travel costs|travel expenses|budget|"
        r"accommodation|funding|grants|χρηματοδ",
    )
    for pattern in patterns:
        candidates = [text for text in bodies if text not in selected
                      and re.search(pattern, text, re.I)]
        if candidates:
            selected.append(candidates[0])
    record["summary_language"] = language
    for text in selected:
        record["classification"]["evidence"].append(
            f"Publisher excerpt ({language}): " + short_excerpt(text, 240))
    timing = short_excerpt(timing, 240)
    budget = 600 - len(timing.encode("utf-16-le")) // 2 - 1
    if selected:
        allowance = (budget - 3 * (len(selected) - 1)) // len(selected)
        body = " / ".join(short_excerpt(text, allowance) for text in selected)
    else:
        body = {"en": "Publisher description not stated.",
                "cs": "Popis nabídky není uveden.",
                "el": "Δεν αναφέρεται περιγραφή."}[language]
    record["summary"] = short_excerpt((timing + " " + body).strip(), 600)


def opportunity(key, title, url, category, deadline, proof, kind="opportunity"):
    record = make_record(title, url, [category], kind=kind,
                         method="publisher-html",
                         evidence=[ATTRIBUTION, "Publisher: " + url] + proof)
    record["id"] = hashlib.sha256(
        (SOURCE_ID + "|" + key).encode()).hexdigest()[:24]
    record["deadline"] = deadline
    record["summary"] = (
        "Uzávěrka přihlášek: " + deadline + ". " if deadline else
        "Uzávěrka přihlášek není uvedena. "
    )
    now = datetime.now(timezone.utc)
    if deadline and "T" in deadline:
        record["status"] = ("expired" if now >= datetime.fromisoformat(
            deadline.replace("Z", "+00:00")) else "open")
    elif deadline:
        today = now.date().isoformat()
        record["status"] = ("expired" if today > deadline else
                            "open" if today < deadline else "unknown")
    return record


def grant_records(pages):
    records = {}
    # The current call supersedes older hub rounds of the same annual scheme.
    hubs = sorted(pages["hubs"].items(),
                  key=lambda item: "/vyzva" in item[0])
    for url, root in hubs:
        if url != SOURCE_URL and "/vyzva" not in url:
            continue
        annual = {}
        for tab in (n for n in nodes(root, "div")
                    if n.attrs.get("id", "").endswith("-tab")):
            fragment = tab.attrs["id"][:-4]
            if fragment == "quality-label":
                continue
            for timeline in nodes(tab, "div", "timeline"):
                heading = " ".join(n.text() for n in
                                   nodes(timeline, "h3", "timeline__heading"))
                if not re.search(r"Termíny (?:výz|pro podávání)", heading):
                    continue
                year_match = re.search(r"\b20\d{2}\b", heading)
                for item in nodes(timeline, "li", "timeline__item"):
                    title = one(nodes(item, "span", "timeline__item-date"),
                                "grant timeline title").text()
                    if re.search(r"Quality Label|Značka kvality", title, re.I):
                        continue
                    deadlines = published_dates(
                        item.text(), year_match[0] if year_match else None)
                    if not deadlines:
                        raise AdapterError("Grant timeline has no dates: "
                                           + url, "parse")
                    years = {date[:4] for date in deadlines}
                    if len(years) != 1:
                        raise AdapterError(
                            "Mixed annual grant timeline", "parse")
                    year = next(iter(years))
                    scheme = {
                        "solidarni-projekty": "solidarity",
                        "dobrovolnicke-projekty": "volunteering",
                    }.get(fragment)
                    if fragment == "centralizovane-aktivity":
                        if re.search(r"vysokou priorit", title, re.I):
                            scheme = "priority-teams"
                        elif re.search(r"humanitární", title, re.I):
                            scheme = "humanitarian"
                    key = scheme or fragment + "|" + title
                    key += "-" + year
                    canonical = SOURCE_URL + "#" + fragment
                    record = opportunity(
                        key, title, canonical, "grants", max(deadlines),
                        ["Call timeline: " + url + "#" + fragment,
                         "Published deadlines: " + ", ".join(deadlines)],
                        kind=("opportunity" if scheme == "solidarity"
                              else "institutional-grant"))
                    blocks = factual_blocks(tab)
                    if fragment == "centralizovane-aktivity":
                        selected, active = [], False
                        for tag, text in blocks:
                            if tag == "h3":
                                active = bool(re.search(
                                    r"vysokou priorit" if scheme == "priority-teams"
                                    else r"humanitární", text, re.I))
                            if active:
                                selected.append((tag, text))
                        blocks = selected
                    if key in annual:
                        previous = annual[key]
                        deadlines = sorted(set(deadlines + previous[1]))
                        record["deadline"] = max(deadlines)
                        record["classification"]["evidence"] = sorted(set(
                            record["classification"]["evidence"]
                            + previous[0]["classification"]["evidence"]))
                        record["title"] = min(record["title"], previous[0]["title"])
                    annual[key] = (record, deadlines, blocks)
        for key, (record, deadlines, blocks) in annual.items():
            record["status"] = opportunity(
                key, record["title"], record["url"], "grants",
                max(deadlines), [])["status"]
            describe(record, blocks, "cs",
                     "Uzávěrky přihlášek: " + ", ".join(sorted(deadlines)) + ".")
            records[key] = record
    if not records:
        raise AdapterError("No grant timelines discovered", "parse")
    return list(records.values())


def event_record(url, root, owner):
    main = one(nodes(root, "main"), "event main")
    title = one(nodes(main, "h1"), "event title").text()
    if re.search(r"individuální konzultace|on.arrival|mid.term", title, re.I):
        return None
    text = main.text()
    if not re.search(r"webinář|školení|seminář|kurz|workshop|konferenc",
                     text, re.I):
        raise AdapterError("Unclassified advertised event: " + url, "parse")
    times = nodes(main, "time")
    years = {n.attrs.get("datetime", "")[:4] for n in times}
    years.discard("")
    year = next(iter(years)) if len(years) == 1 else None
    # Match only application/registration sentences, never activity dates.
    contexts = re.findall(
        r"(?:Registrac[ei]|registrační formulář|přihlášk[ay]|"
        r"přihlašování|přihlas\w*|Deadline)[^!?]{0,180}?"
        r"(?:do |je )((?:\d{1,2}\.\s*)[^!?]{0,60})", text, re.I)
    deadlines = [date for context in contexts
                 for date in published_dates(context, year)[:1]]
    deadline = max(deadlines) if deadlines else None
    key = _EVENT_KEYS.get(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1],
                          url)
    record = opportunity(key, title, url, "training", deadline,
                         ["Discovered at: " + owner])
    event_dates = sorted({n.attrs.get("datetime", "")[:10] for n in times
                          if re.match(r"20\d{2}-\d{2}-\d{2}",
                                      n.attrs.get("datetime", ""))})
    if event_dates:
        record["summary"] += " Termín akce: " + event_dates[0] + "."
    if re.search(r"\bv (?:Brně|Praze)\b", text):
        record["host_countries"] = ["CZ"]
        record["location"] = "Brno" if "v Brně" in text else "Praha"
    describe(record, factual_blocks(main), "cs", record["summary"])
    return record


def delegated_record(url, root, owner):
    content = one(nodes(root, "div", "training"), "SALTO training detail")
    title = one(nodes(content, "h1"), "training title").text()
    text = content.text()
    closing = re.search(
        r"(?:application deadline(?:\s*\([^)]*\))?"
        r"(?:\s+(?:was|is))?|Apply before)"
        r"[:\s]+(.{0,100})", text, re.I)
    if not closing:
        raise AdapterError("Missing SALTO application deadline: " + url,
                           "parse")
    values = published_dates(closing[1])
    if not values:
        raise AdapterError("Unparseable SALTO application deadline: " + url,
                           "parse")
    deadline = values[0]
    if re.search(r"24\s*h\s*UTC", closing[0], re.I):
        deadline = (datetime.fromisoformat(deadline) +
                    timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    identifier = re.search(r"\.(\d+)/?$", urllib.parse.urlsplit(url).path)
    if not identifier:
        raise AdapterError("Missing SALTO training identity", "parse")
    key = _SALTO_KEYS.get(identifier[1], "salto-" + identifier[1])
    record = opportunity(key, title, url, "training", deadline,
                         ["DZS discovery: " + owner])
    record["language"] = "en"
    record["summary"] = "Application deadline: " + deadline + "."
    organiser = re.search(r"Organiser:\s*(.+?)\s*Contact for questions:",
                          text, re.I)
    if organiser:
        record["classification"]["evidence"].append(
            "Organiser: " + organiser[1])
    if "Applications are closed" in text:
        record["status"] = "expired"
    category = one(nodes(content, "div", "tool-item-category-container"),
                   "training dates/location").text()
    if "|" in category:
        location = category.split("|", 1)[1].strip()
        record["location"] = location
        record["summary"] += " Location: " + location[:100] + "."
        countries = {
            "Greece": "GR", "Slovakia": "SK", "Germany": "DE",
            "Czech Republic": "CZ", "Czechia": "CZ", "Portugal": "PT",
            "Poland": "PL", "Italy": "IT", "France": "FR",
            "Austria": "AT", "Belgium": "BE", "Spain": "ES",
            "Finland": "FI", "Ireland": "IE", "Latvia": "LV",
            "Lithuania": "LT", "Estonia": "EE", "Netherlands": "NL",
            "Romania": "RO", "Slovenia": "SI", "Hungary": "HU",
            "Luxembourg": "LU", "Croatia": "HR", "Serbia": "RS",
            "Bulgaria": "BG", "Cyprus": "CY", "Malta": "MT",
            "Denmark": "DK", "Sweden": "SE", "Norway": "NO",
            "Iceland": "IS", "Türkiye": "TR", "Turkey": "TR",
            "North Macedonia": "MK", "Switzerland": "CH",
            "Slovak Republic": "SK",
        }
        if not re.search(r"online|on.line|virtual", category, re.I):
            hosts = {code for country, code in countries.items()
                     if re.search(r"\b" + re.escape(country) + r"\b",
                                  location, re.I)}
            if len(hosts) == 1:
                record["host_countries"] = sorted(hosts)
    blocks = [("body", node.text()) for node in
              nodes(content, "div", "training-summary")]
    blocks.extend(factual_blocks(content))
    describe(record, blocks, "en", record["summary"])
    record["classification"]["evidence"].append(
        "Advertised closing day: " + values[0])
    return record


def parse_inventory(pages):
    records = grant_records(pages)
    own = {}
    for url, root in pages["events"].items():
        record = event_record(url, root, pages["owners"][url])
        if record:
            own[url] = record
    for url, root in pages["delegated"].items():
        owner = pages["owners"][url]
        record = delegated_record(url, root, owner)
        if owner in own:
            # The linked provider is evidence for the same DZS course.
            parent = own[owner]
            advertised = next(value.removeprefix("Advertised closing day: ")
                              for value in record["classification"]["evidence"]
                              if value.startswith("Advertised closing day: "))
            if parent["deadline"] and parent["deadline"] != advertised:
                raise AdapterError("Conflicting DZS/SALTO closing dates",
                                   "parse")
            parent["deadline"] = record["deadline"]
            parent["status"] = record["status"]
            parent["location"] = record["location"]
            parent["host_countries"] = record["host_countries"]
            parent["classification"]["evidence"].append(
                "Delegated course details: " + url)
            parent["classification"]["evidence"].extend(
                record["classification"]["evidence"])
        else:
            records.append(record)
    records.extend(own.values())
    validate_records(records)
    return records


def collect():
    if _PHASE_END is not None:
        check_deadline()
        return parse_inventory(read_pages())
    with execution():
        with phase("prepublication", 900):
            prepare_collection()
            return parse_inventory(read_pages())


def metadata(attempt, previous, records, error=None, checked_at=None):
    outcome = {
        "source": SOURCE_ID,
        "name": "DZS — European Solidarity Corps",
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "attribution": ATTRIBUTION,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (previous.get("last_success_at")
                            if error else attempt),
        "last_checked_at": (previous.get("last_checked_at")
                            if error else checked_at),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved" if error else
            f"Collected {len(records)} published ESC grants and training offers"
        ),
        "error": safe_error(error) if error else None,
    }
    if error:
        outcome["failure_stage"] = getattr(error, "stage", "parse")
    return outcome


def phase_error(name):
    return AdapterError("Whole " + name + " phase deadline exceeded",
                        "publish" if name == "publication" else "access")


def serialize(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def fresh_records(records, old, attempt, checked):
    prior = {record["id"]: record for record in old}
    temporal = {"created_at", "updated_at", "first_seen_at", "last_seen_at",
                "last_checked_at"}
    for record in records:
        before = prior.get(record["id"], {})
        same = {k: v for k, v in before.items() if k not in temporal} == {
            k: v for k, v in record.items() if k not in temporal
        }
        record.update(
            created_at=before.get("created_at", attempt),
            first_seen_at=before.get("first_seen_at", attempt),
            updated_at=before.get("updated_at", attempt) if same else attempt,
            last_seen_at=attempt, last_checked_at=checked,
        )
    records.sort(key=lambda record: record["id"])
    validate_records(records)


def run_body(publishing):
    attempt = utc_now()
    snapshot = None
    old, previous, records, desired = [], {}, [], None
    failure = None
    try:
        with phase("prepublication", 900):
            prepare_collection()
            if publishing:
                candidate = read_snapshot()
                if candidate["files"]["data.json"] is not None:
                    candidate_records = json.loads(
                        candidate["files"]["data.json"])
                    candidate_metadata = json.loads(
                        candidate["files"]["metadata.json"])
                    validate_records(candidate_records)
                    if (not isinstance(candidate_metadata, dict)
                            or candidate_metadata.get("source") != SOURCE_ID):
                        raise AdapterError("Remote metadata source mismatch",
                                           "publish")
                    old, previous = candidate_records, candidate_metadata
                snapshot = candidate
            records = collect()
            checked = utc_now()
            fresh_records(records, old, attempt, checked)
            outcome = metadata(attempt, previous, records, checked_at=checked)
            desired = {"data.json": serialize(records),
                       "metadata.json": serialize(outcome)}
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
                    elif old and latest["files"] == snapshot["files"]:
                        publish_files(latest, {
                            "metadata.json": serialize(outcome),
                        })
                    else:
                        raise AdapterError("No safe unchanged last-good pair",
                                           "publish")
            except (Exception, PhaseExpired):
                outcome["message"] = (
                    "Run failed; durable outcome unconfirmed. "
                    "No newer source pair was overwritten."
                )
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
                print("Timing artifact export failed; next run fail closed",
                      file=sys.stderr)
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



if __name__ == "__main__":
    sys.exit(main())
