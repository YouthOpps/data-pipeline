"""Collect Luxembourg cooperation programmes and all linked recruitment calls."""

import argparse
import base64
import contextlib
import datetime
import email.utils
import fcntl
import hashlib
import html
import html.parser
import ipaddress
import io
import math
import stat
import zipfile
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_ID = "lu-luxembourg-cooperation"
SOURCE_URL = "https://cooperation.gouvernement.lu/en/s-engager.html"
WEBSITE_URL = "https://cooperation.gouvernement.lu/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "LU"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Luxembourg Directorate for Development Cooperation and Humanitarian Affairs"

CATEGORIES = (
    "scholarships",
    "internships",
    "volunteering",
    "fellowships",
    "training",
    "competitions",
    "grants",
    "jobs",
    "other",
)
_GITHUB = "https://api.github.com/repos/YouthOpps/data-source"
_USER_AGENT = "YouthOppsBot/1.0 (+https://youthopps.org/bot/)"
_PUBLISHER_GATE_FAILED = False
_ROBOTS_CACHE = {}
_WORKFLOW_GITHUB = "https://api.github.com/repos/YouthOpps/data-pipeline"
FAMILY = "youthopps-lu-luxembourg-cooperation-publisher-v1"
FAMILY_SOURCES = {SOURCE_ID}
_ARTIFACT_NAME = "lu-luxembourg-cooperation-pacing-state"
_RESTORED = _STATE_TRUSTED = False
_BUDGET_INITIALIZING = None
_COLLECTION_LOCK_HELD = False
_PUBLISHER_HOST = "cooperation.gouvernement.lu"


_PAGE_COMPLETED = None


def fetch_inventory_page(fetch, *args, **kwargs):
    """Space logical inventory pages without changing native detail pacing."""
    global _PAGE_COMPLETED
    if _PAGE_COMPLETED is not None:
        while True:
            with budget_state() as state:
                page_interval = state["page_interval"]
            wait = _PAGE_COMPLETED + page_interval - time.monotonic()
            if wait <= 0:
                break
            time.sleep(wait)
    try:
        return fetch(*args, **kwargs)
    finally:
        _PAGE_COMPLETED = time.monotonic()


def _feed_text(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", value or "")).split())


class AdapterError(Exception):
    """Collection/publication failure with a stage and optional HTTP status."""

    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent implicit redirects so each source hop is checked and paced."""

    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)


def utc_now():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def safe_error(error):
    """Return a bounded actionable error without URL queries or credentials."""
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


def numeric(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def empty_budget(now):
    return {
        "schema": 1,
        "family": FAMILY,
        "starts": [],
        "interval": 12.1,
        "until": now,
        "rate_failures": 0,
        "page_interval": 30,
        "blocked": False,
        "observed_at": now,
    }


def validate_budget(state):
    """Validate source-bound inert state; never expire refusals or embargoes."""
    if (
        not isinstance(state, dict)
        or set(state) != set(empty_budget(0))
        or state["schema"] != 1
        or state["family"] != FAMILY
        or not numeric(state["until"])
        or not numeric(state["observed_at"])
        or state["observed_at"] > time.time() + 1
        or not numeric(state["interval"])
        or state["interval"] < 12
        or not numeric(state["page_interval"])
        or state["page_interval"] < 30
        or type(state["rate_failures"]) is not int
        or state["rate_failures"] < 0
        or type(state["blocked"]) is not bool
        or not isinstance(state["starts"], list)
        or len(state["starts"]) > 5
        or any(
            not numeric(t) or t > state["observed_at"] for t in state["starts"]
        )
        or sorted(set(state["starts"])) != state["starts"]
    ):
        raise AdapterError("Invalid retained publisher state", "access")
    return state


def budget_path(host=None):
    global _BUDGET_INITIALIZING
    directory = os.path.join(
        tempfile.gettempdir(), FAMILY + "-" + str(os.getuid())
    )
    try:
        os.mkdir(directory, 0o700)
        _BUDGET_INITIALIZING = True
    except FileExistsError:
        if _BUDGET_INITIALIZING is None:
            _BUDGET_INITIALIZING = False
    info = os.stat(directory, follow_symlinks=False)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AdapterError("Unsafe publisher state directory", "access")
    return os.path.join(directory, "state.json")


@contextlib.contextmanager
def budget_state(host=None):
    """Serialize durable local state updates outside the delivered tree."""
    global _BUDGET_INITIALIZING
    path = budget_path()
    fd = os.open(
        path,
        os.O_RDWR | (os.O_CREAT if _BUDGET_INITIALIZING else 0) | os.O_NOFOLLOW,
        0o600,
    )
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise AdapterError("Unsafe publisher state file", "access")
    with os.fdopen(fd, "r+", encoding="utf-8") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        raw = file.read(16385)
        if len(raw) > 16384:
            raise AdapterError("Oversized publisher state", "access")
        try:
            if not raw and not _BUDGET_INITIALIZING:
                raise ValueError("Mature state is empty")
            state = (
                validate_budget(json.loads(raw))
                if raw
                else empty_budget(time.time())
            )
        except (ValueError, TypeError) as error:
            raise AdapterError("Corrupt publisher state", "access") from error
        try:
            yield state
        finally:
            state["observed_at"] = time.time()
            state["starts"] = [
                t for t in state["starts"] if t > time.time() - 60
            ]
            validate_budget(state)
            file.seek(0)
            file.truncate()
            json.dump(state, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
            _BUDGET_INITIALIZING = False


def pace(url, interval=12):
    """Preserve actual-start history and completion guards across local runs."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    while True:
        with budget_state() as state:
            if state["blocked"]:
                raise AdapterError(
                    "Publisher refusal requires reviewed recovery", "access"
                )
            now = time.time()
            starts = [t for t in state["starts"] if now - t < 60]
            state["starts"] = starts
            state["interval"] = max(12.1, interval, state["interval"])
            ready = max(
                state["until"],
                starts[-1] + state["interval"] if starts else now,
                starts[0] + 60 if len(starts) >= 5 else now,
            )
            if ready <= now:
                state["starts"].append(now)
                return
            if ready - now > 120:
                raise AdapterError(
                    "Publisher backoff exceeds collection wait budget", "access"
                )
        time.sleep(max(0, ready - time.time()))


def http_bytes(
    url, *, headers=None, method="GET", payload=None, limit=5_000_000
):
    """Read bounded nonpublisher HTTP without implicit credential redirects."""
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={**(headers or {}), "User-Agent": _USER_AGENT},
    )
    try:
        response = _OPENER.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read(limit + 1)
        if len(body) > limit:
            raise AdapterError("Response exceeds byte limit", "fetch")
        return response.status, response.headers, body


def request_bytes(
    url, *, interval=12, method="GET", headers=None, payload=None
):
    """Apply publisher-only physical-start and persistent response guards."""
    global _PUBLISHER_GATE_FAILED
    if (urllib.parse.urlsplit(url).hostname or "").removeprefix(
        "www."
    ) != _PUBLISHER_HOST:
        return http_bytes(url, method=method, headers=headers, payload=payload)
    request = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={**(headers or {}), "User-Agent": _USER_AGENT},
    )
    pace(url, interval)
    print(
        json.dumps(
            {
                "event": "request_start",
                "url": url,
                "time": time.time(),
                "user_agent": _USER_AGENT,
            }
        ),
        file=sys.stderr,
        flush=True,
    )
    try:
        try:
            response = _OPENER.open(request, timeout=60)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            with budget_state() as state:
                now = time.time()
                if response.status == 429:
                    state["rate_failures"] += 1
                    state["until"] = max(
                        state["until"],
                        now + 1800 * 2 ** (state["rate_failures"] - 1),
                    )
                    state["interval"] = max(24, state["interval"] * 2)
                    state["page_interval"] = max(60, state["page_interval"] * 2)
                if (
                    response.status in (401, 403, 451)
                    and urllib.parse.urlsplit(url).path != "/robots.txt"
                ):
                    state["blocked"] = True
                retry = response.headers.get("Retry-After")
                if retry:
                    try:
                        until = (
                            now + float(retry)
                            if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                            else email.utils.parsedate_to_datetime(
                                retry
                            ).timestamp()
                        )
                        if not numeric(until):
                            raise ValueError()
                        state["until"] = max(state["until"], until)
                    except (ValueError, TypeError, OverflowError):
                        if response.status != 429:
                            state["blocked"] = True
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds byte limit", "fetch")
            return response.status, response.headers, body
    finally:
        _PUBLISHER_GATE_FAILED = True
        completed = time.time()
        with budget_state() as state:
            state["until"] = max(
                state["until"], completed + max(12.1, interval, state["interval"])
            )
        _PUBLISHER_GATE_FAILED = False
        print(
            json.dumps(
                {"event": "request_complete", "url": url, "time": completed}
            ),
            file=sys.stderr,
            flush=True,
        )


def fetch_source(url, min_interval=12, *, with_headers=False):
    """Read a reviewed HTTPS source with checked, paced redirects and retries.

    Return decoded text, or (decoded text, HTTP headers) with with_headers=True.
    Raise AdapterError when permitted retrieval cannot complete.
    """
    original = urllib.parse.urlsplit(url)

    def checked(value):
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or host != (original.hostname or "").lower().removeprefix("www.")
            or parsed.port != original.port
        ):
            raise AdapterError(
                "Source redirect leaves the reviewed HTTPS host", "fetch"
            )
        if host == "localhost" or host.endswith(".localhost"):
            raise AdapterError("Public HTTPS source required", "fetch")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise AdapterError("Public HTTPS source required", "fetch")
        except ValueError:
            pass

    for attempt in range(3):
        current = url
        try:
            for hop in range(4):
                checked(current)
                status, headers, body = request_bytes(
                    current, interval=min_interval
                )
                if status in (301, 302, 303, 307, 308):
                    if hop == 3 or not headers.get("Location"):
                        raise AdapterError(
                            "Invalid or excessive source redirects", "fetch"
                        )
                    current = urllib.parse.urljoin(current, headers["Location"])
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status in (429, 503):
                    raise AdapterError(
                        f"Publisher returned HTTP {status}; backoff retained",
                        "fetch",
                        status,
                    )
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status}", "fetch", status
                    )
                text = body.decode(headers.get_content_charset() or "utf-8")
                return (text, headers) if with_headers else text
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error), "fetch"
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    """Check endpoint access against current robots and return start spacing."""
    parsed = urllib.parse.urlsplit(url)
    robots_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/robots.txt", "", "")
    )
    if robots_url not in _ROBOTS_CACHE:
        try:
            _ROBOTS_CACHE[robots_url] = fetch_source(robots_url)
        except AdapterError as error:
            if (
                error.status is not None
                and 400 <= error.status < 500
                and error.status != 429
            ):
                _ROBOTS_CACHE[robots_url] = ""
            else:
                raise
    text = _ROBOTS_CACHE[robots_url]
    groups, group = [], {"agents": [], "rules": [], "delay": 0}
    for line in text.splitlines():
        key, separator, value = line.split("#", 1)[0].strip().partition(":")
        if not separator:
            continue
        key, value = key.lower().strip(), value.strip()
        if key == "user-agent":
            if group["rules"] or group["delay"]:
                groups.append(group)
                group = {"agents": [], "rules": [], "delay": 0}
            group["agents"].append(value.lower())
        elif key in ("allow", "disallow"):
            group["rules"].append((key, value))
        elif key == "crawl-delay":
            try:
                group["delay"] = float(value)
                if not 0 <= group["delay"] <= 60:
                    raise ValueError()
            except ValueError:
                raise AdapterError("Unsupported robots crawl delay", "access")
    groups.append(group)
    directives = [
        line.split("#", 1)[0].strip().lower()
        for line in text.splitlines()
        if line.split("#", 1)[0].strip()
    ]
    if (
        directives
        and not any(g["agents"] for g in groups)
        and not all(line.startswith("sitemap:") for line in directives)
    ):
        raise AdapterError("Robots policy is unavailable or invalid", "access")
    agent = _USER_AGENT.split("/", 1)[0].lower()
    specific = [g for g in groups if agent in g["agents"]]
    legacy = [g for g in groups if "youthopp" in g["agents"]]
    policies = [policy for policy in (specific, legacy) if policy]
    if not policies:
        policies = [[g for g in groups if "*" in g["agents"]]]
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
    for selected in policies:
        matches = []
        for g in selected:
            for directive, pattern in g["rules"]:
                if not pattern:
                    continue
                expression = (
                    "^"
                    + ".*".join(
                        re.escape(part)
                        for part in pattern.removesuffix("$").split("*")
                    )
                    + ("$" if pattern.endswith("$") else "")
                )
                if re.search(expression, path):
                    matches.append(
                        (
                            len(pattern.replace("*", "").removesuffix("$")),
                            directive == "allow",
                        )
                    )
        if matches and not max(matches)[1]:
            with budget_state() as state:
                state["blocked"] = True
            raise AdapterError(
                "Robots policy disallows this source endpoint", "access"
            )
    return max([12] + [g["delay"] for policy in policies for g in policy])


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
    """Build the public record envelope with factual source provenance."""
    now = utc_now()
    return {
        "id": SOURCE_ID
        + "-"
        + hashlib.sha256(f"{SOURCE_ID}|{url}".encode()).hexdigest()[:24],
        "title": title,
        "url": url,
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "published_at": published_at,
        "summary": "",
        "attribution": ATTRIBUTION,
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
    """Validate every record, stable ID, source, date, category and provenance.

    Allow empty lists only for existing snapshots and validated expiry cleanup.
    Fresh collection must produce genuine nonempty records.
    """
    if not isinstance(records, list) or (not records and not allow_empty):
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
        if record["id"].lower() in ids:
            raise AdapterError("Duplicate opportunity identifier", "validate")
        ids.add(record["id"].lower())
        if record.get("attribution") != ATTRIBUTION:
            raise AdapterError("Missing publisher attribution", "validate")
        if not re.fullmatch(SOURCE_ID + r"-[a-f0-9]{24}", record["id"]):
            raise AdapterError(
                "Invalid stable publisher identifier", "validate"
            )
        url = urllib.parse.urlsplit(record["url"])
        if (
            url.scheme != "https"
            or url.netloc != "cooperation.gouvernement.lu"
            or not url.path.startswith("/en/s-engager")
        ):
            raise AdapterError("Unexpected publisher record URL", "validate")
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
                    if field == "deadline" and re.fullmatch(
                        r"\d{4}-\d{2}-\d{2}", value
                    ):
                        datetime.date.fromisoformat(value)
                    elif (
                        datetime.datetime.fromisoformat(
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
        if (
            not isinstance(record.get("summary"), str)
            or len(record["summary"]) > 600
            or re.search("<[^>]+>", record["summary"])
        ):
            raise AdapterError("Invalid plain summary", "validate")
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


@contextlib.contextmanager
def collection_lock():
    """Hold one source lifecycle lock, including restore/snapshot/export."""
    if _PUBLISHER_GATE_FAILED:
        raise AdapterError("Publisher completion persistence failed", "access")
    global _COLLECTION_LOCK_HELD
    if _COLLECTION_LOCK_HELD:
        yield
        return
    path = os.path.join(os.path.dirname(budget_path()), "collection.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        os.close(fd)
        raise AdapterError("Unsafe source lifecycle lock", "access")
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _COLLECTION_LOCK_HELD = True
        try:
            time.sleep(12.1)
            yield
        finally:
            _COLLECTION_LOCK_HELD = False
            time.sleep(12.1)
            fcntl.flock(lock, fcntl.LOCK_UN)


def collect():
    """Retrieve the full inventory under the shared collection lock."""
    global _STATE_TRUSTED
    with collection_lock():
        if os.environ.get("GITHUB_ACTIONS") == "true":
            restore_family_artifact()
        _STATE_TRUSTED = True
        return collect_inventory()


class Element:
    """Minimal tree for explicit publisher content and native navigation."""

    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return " ".join(
            " ".join(
                child.text() if isinstance(child, Element) else child
                for child in self.children
            ).split()
        )


class PublicHTML(html.parser.HTMLParser):
    """Parse public HTML without scripts, browser state or remote assets."""

    def __init__(self, document):
        super().__init__(convert_charrefs=True)
        self.root = Element()
        self.stack = [self.root]
        self.feed(document)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, attrs)
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


def one(nodes, description):
    if len(nodes) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return nodes[0]


def page(url, inventory=False):
    """Read permitted source markup and reject soft refusals or error pages."""

    def retrieve():
        return fetch_source(url, min_interval=check_robots(url))

    document = fetch_inventory_page(retrieve) if inventory else retrieve()
    parsed = PublicHTML(document)
    visible = parsed.root.text()
    if (
        re.search(
            r"verify (?:that )?you are human|confirm (?:that )?you are (?:a )?human|unusual traffic|access (?:denied|blocked)|request (?:has been )?(?:blocked|denied)",
            visible,
            re.I,
        )
        or "cf-chl-" in document
    ):
        with budget_state() as state:
            state["blocked"] = True
        raise AdapterError(
            "Publisher returned an explicit content challenge", "access"
        )
    if not re.search(r"</body\s*>\s*</html\s*>\s*$", document, re.I):
        raise AdapterError("Incomplete publisher HTML", "fetch")
    root = one(
        [node for node in parsed.root.walk() if node.tag == "main"],
        "publisher main region",
    )
    title = one(
        [node for node in root.walk() if node.tag == "h1"], "publisher title"
    ).text()
    if not title or re.search(
        r"access denied|not found|captcha|forbidden", title, re.I
    ):
        raise AdapterError(
            "Publisher did not return the opportunity page", "fetch"
        )
    return root, title


# These are source-specific semantic mappings, checked against live content.
# They describe actual standing programmes, not fabricated currently-open calls.
_PROGRAMMES = {
    "Internships at the Directorate for Development Cooperation and Humanitarian Affairs": (
        "directorate-internships",
        "internships",
        [],
        (
            "Paid internship positions",
            "3 to 6 months",
            "Master’s level",
            "Luxembourg citizen or resident",
            "English and French",
            "understand and speak Luxembourgish",
        ),
        "Paid 3–6 month placements for Master's students with Luxembourg citizenship or residence. English, French and spoken Luxembourgish are required. Availability varies.",
    ),
    "Internships at ENDA Santé": (
        "enda-sante-internships",
        "internships",
        ["SN"],
        ("Dakar, Senegal", "travel expenses", "internship report"),
        "Internships in Dakar, Senegal. Luxembourg contributes to travel costs when the intern submits a detailed report; other eligibility and availability are unspecified.",
    ),
    "The Junior Technical Assistant Programme (JTA/ATJ)": (
        "jta",
        "jobs",
        [],
        (
            "Lux-Development",
            "not having exceeded the age of 30",
            "resident in Luxembourg for five years",
            "Each May",
        ),
        "LuxDev early-career placements for post-secondary graduates aged 30 or younger. Luxembourg citizenship or five years' residence is required. Recruitment uses a separate annual call.",
    ),
    "The Junior Professional Officer Programme (JPO/JEA)": (
        "jpo",
        "jobs",
        [],
        (
            "Master",
            "at least two years",
            "not being over 32 years",
            "resident in Luxembourg for five years",
            "recruitment is carried out yearly",
        ),
        "UN placements for Master's graduates with at least two years' experience. Luxembourg citizenship or five years' residence and age conditions apply; specific vacancies govern selection.",
    ),
    "The UN Volunteer Program (UNV)": (
        "unv",
        "volunteering",
        [],
        (
            "UNV Youth Internationals",
            "UNV Specialists",
            "Bachelor",
            "not being more than 26 years old",
            "no professional experience is required",
        ),
        "Standing UN volunteer programme with youth and specialist routes. The youth route requires a Bachelor's degree and no work experience; nationality or residence rules depend on the route. Current calls govern actual selection.",
    ),
    "The Junior Professional Programme in delegation (JPD)": (
        "jpd",
        "jobs",
        [],
        (
            "European Union",
            "Master",
            "no more than one year",
            "Every two years in January",
            "having been in Luxembourg for 5 years",
        ),
        "EU delegation placements for Master's graduates with Luxembourg nationality or five years' residence and no more than one year of EU-institution experience. Separate recruitment calls govern availability.",
    ),
    "The Voluntary Cooperation Service (VCS)": (
        "vcs",
        "volunteering",
        [],
        (
            "6 and 12 months",
            "18 and 30 years old",
            "Luxembourg resident",
            "not having the status of a student or employee",
            "subsistence allowance",
            "social insurance coverage",
        ),
        "Supported 6–12 month development volunteering for Luxembourg residents aged 18–30 who are neither students nor employees when applying. A subsistence allowance and social insurance are provided.",
    ),
}
_EXCLUDED = {
    "Volunteering within NGOs in Luxembourg": "generic NGO directory referral",
    "Professional involvement with NGOs in Luxembourg": "external jobs referral",
    "Development cooperation leave": "leave/reimbursement for existing workers",
}
_CALLS = (
    "https://cooperation.gouvernement.lu/en/s-engager/appels-a-candidature.html"
)


def programme_records(root):
    """Extract every substantive advertised programme and explicit exclusion."""
    records, seen = [], set()
    for node in root.walk():
        if (
            node.tag != "details"
            or "cmp-accordion__item" not in node.attrs.get("class", "").split()
        ):
            continue
        heading = one(
            [
                item
                for item in node.walk()
                if item.tag == "h3"
                and "cmp-accordion__header"
                in item.attrs.get("class", "").split()
            ],
            "programme heading",
        ).text()
        if heading in seen:
            raise AdapterError("Duplicate programme heading", "validate")
        seen.add(heading)
        if heading in _EXCLUDED:
            continue
        if heading not in _PROGRAMMES:
            raise AdapterError(
                "Unclassified new programme: " + heading, "parse"
            )
        identity, category, countries, markers, summary = _PROGRAMMES[heading]
        text = node.text()
        if any(marker not in text for marker in markers):
            raise AdapterError("Programme facts changed: " + heading, "parse")
        links = [
            item.attrs.get("href", "")
            for item in node.walk()
            if item.tag == "a"
        ]
        if not any(
            link.startswith("mailto:sengager.cooperation@")
            or link.startswith("https://www.volontaires.lu/")
            or ("luxdev" in link.lower() or "lux-development" in link.lower())
            for link in links
        ):
            raise AdapterError(
                "Programme application route is missing", "parse"
            )
        anchor = node.attrs.get("id")
        if not anchor or not re.fullmatch(r"[A-Za-z0-9_-]+", anchor):
            raise AdapterError("Programme anchor is unavailable", "parse")
        record = make_record(
            heading,
            SOURCE_URL + "#" + anchor,
            [category],
            kind="programme-overview",
            host_countries=countries,
            method="publisher-programme",
            evidence=[SOURCE_URL],
        )
        # Identity follows the stable programme mechanism, not mutable headings or anchors.
        record["id"] = (
            SOURCE_ID
            + "-"
            + hashlib.sha256(("programme|" + identity).encode()).hexdigest()[
                :24
            ]
        )
        record["summary"] = summary
        record["tags"] = ["standing-programme", "availability-unconfirmed"]
        if countries == ["LU"]:
            record["location"] = "Luxembourg"
        elif countries == ["SN"]:
            record["location"] = "Dakar, Senegal"
        records.append(record)
    if set(_PROGRAMMES) - seen:
        raise AdapterError(
            "Advertised programme inventory is incomplete", "parse"
        )
    return records


def call_links(root, parent):
    """Read native subsection navigation; never fabricate search parameters."""
    menus = [
        node
        for node in root.walk()
        if node.tag == "ul" and node.attrs.get("id") == "localnav"
    ]
    if len(menus) > 1:
        raise AdapterError(
            "Ambiguous recruitment subsection navigation", "parse"
        )
    result = []
    for menu in menus:
        for node in menu.walk():
            if node.tag != "a" or not node.attrs.get("href"):
                continue
            url = urllib.parse.urljoin(parent, node.attrs["href"])
            parsed = urllib.parse.urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "cooperation.gouvernement.lu"
                or parsed.query
                or not parsed.path.startswith(
                    "/en/s-engager/appels-a-candidature/"
                )
            ):
                raise AdapterError("Unreviewed recruitment subsection", "parse")
            if url in result:
                raise AdapterError("Duplicate recruitment subsection", "parse")
            result.append(url)
    # Any future pagination must be reviewed, not silently truncated.
    for node in root.walk():
        if node.tag == "a" and (
            "next" in node.attrs.get("rel", "").split()
            or re.search(r"pagination|pager", node.attrs.get("class", ""), re.I)
        ):
            raise AdapterError("Recruitment pagination changed", "parse")
    return result


def recruitment_record(root, title, url):
    """Record a genuine call as one call, without inventing individual posts."""
    text = root.text()
    if "Recruitment Notice" not in text:
        raise AdapterError("Recruitment page lacks an actual notice", "parse")
    if not re.search(
        r"United Nations Volunteers|Junior Professional|Junior Technical|internship|volunteer",
        text,
        re.I,
    ):
        raise AdapterError(
            "Call youth/programme relevance needs review", "parse"
        )
    deadlines = re.findall(
        r"application deadline of\s+(\d{1,2} [A-Za-z]+ \d{4})", text
    )
    if not deadlines or len(set(deadlines)) != 1:
        raise AdapterError("Missing or ambiguous recruitment deadline", "parse")
    try:
        deadline = (
            datetime.datetime.strptime(deadlines[0], "%d %B %Y")
            .date()
            .isoformat()
        )
    except ValueError as error:
        raise AdapterError(
            "Unsupported recruitment deadline", "parse"
        ) from error
    applications = [
        node.attrs.get("href", "")
        for node in root.walk()
        if node.tag == "a"
        and node.attrs.get("href", "").startswith("https://app.unv.org/")
    ]
    if not applications or not all(
        term in text
        for term in (
            "Associate",
            "Specialist",
            "Luxembourg nationals",
            "five years",
            "Benin",
            "Cabo Verde",
            "Costa Rica",
            "Laos",
            "Rwanda",
            "Senegal",
        )
    ):
        raise AdapterError("Call facts or application routes changed", "parse")
    record = make_record(
        title, url, ["volunteering"], method="publisher-call", evidence=[url]
    )
    record["deadline"] = deadline
    record["summary"] = (
        "Grouped UN volunteer recruitment for Associate and Specialist roles. "
        "Applicants must have Luxembourg nationality, five years' Luxembourg "
        "residence, or nationality of Benin, Cabo Verde, Costa Rica, Laos, Rwanda "
        "or Senegal. Individual role criteria remain on the official application site."
    )
    record["tags"] = ["grouped-recruitment-call"]
    # Residency is an alternative to nationality, so no exclusive citizenship list.
    record["eligible_countries"] = []
    closing = datetime.date.fromisoformat(deadline)
    current = datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=-12))
    ).date()
    record["status"] = "expired" if current > closing else "open"
    return record


def collect_inventory():
    """Collect all advertised programmes and every native linked call page."""
    global _PAGE_COMPLETED
    _PAGE_COMPLETED = None
    root, title = page(SOURCE_URL, inventory=True)
    if title != "Getting involved":
        raise AdapterError("Source programme hub changed", "parse")
    records = programme_records(root)
    links = [
        urllib.parse.urljoin(SOURCE_URL, node.attrs.get("href", ""))
        for node in root.walk()
        if node.tag == "a"
    ]
    if _CALLS not in links:
        raise AdapterError("Official recruitment hub link is missing", "parse")
    pending, visited = [_CALLS], set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            raise AdapterError("Recruitment navigation cycle", "parse")
        visited.add(url)
        call, heading = page(url, inventory=True)
        children = call_links(call, url)
        if children:
            pending.extend(children)
        elif url != _CALLS:
            records.append(recruitment_record(call, heading, url))
        elif "Calls for application" not in heading:
            raise AdapterError("Official calls hub changed", "parse")
    validate_records(records)
    return records


def current_run_identity():
    if (
        os.environ.get("GITHUB_REPOSITORY") != "YouthOpps/data-pipeline"
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
    ):
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


def run_completion(run):
    """Order exact completed attempts by their actual job completion times."""
    identity, attempt = run["id"], run["run_attempt"]
    response = workflow_api(
        f"/actions/runs/{identity}/attempts/{attempt}/jobs?per_page=100"
    )
    jobs = response.get("jobs")
    if (
        not isinstance(jobs, list)
        or not jobs
        or len(jobs) > 100
        or type(response.get("total_count")) is not int
        or response["total_count"] != len(jobs)
    ):
        raise AdapterError(
            "Incomplete completed-attempt job inventory", "access"
        )
    completed, began = [], []
    for job in jobs:
        try:
            if (
                not isinstance(job, dict)
                or job.get("status") != "completed"
                or job.get("run_id", identity) != identity
                or job.get("run_attempt", attempt) != attempt
                or job.get("head_sha", run["head_sha"]) != run["head_sha"]
            ):
                raise ValueError()
            end = datetime.datetime.fromisoformat(
                job["completed_at"].replace("Z", "+00:00")
            )
            if (
                end.utcoffset() is None
                or not 0 <= end.timestamp() <= time.time() + 1
            ):
                raise ValueError()
            start = datetime.datetime.fromisoformat(
                job.get("started_at", job["completed_at"]).replace(
                    "Z", "+00:00"
                )
            )
            if (
                start.utcoffset() is None
                or not 0 <= start.timestamp() <= end.timestamp()
            ):
                raise ValueError()
            completed.append(end.timestamp())
            began.append(start.timestamp())
        except (ValueError, TypeError, AttributeError, KeyError):
            raise AdapterError(
                "Invalid actual job completion chronology", "access"
            ) from None
    return max(completed), min(began)


def completed_identity(run, expected=None, expected_head=None):
    """Bind a completed attempt to its source, immutable head and actual jobs."""
    try:
        source = family_source(run)
        identity, attempt = run["id"], run["run_attempt"]
        if (
            not source
            or type(identity) is not int
            or identity <= 0
            or type(attempt) is not int
            or attempt <= 0
            or run["status"] != "completed"
            or run["repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_repository"]["full_name"] != "YouthOpps/data-pipeline"
            or run["head_branch"] != "main"
            or not isinstance(run.get("head_sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            or expected is not None
            and (identity, attempt, source) != expected
            or expected_head is not None
            and run["head_sha"] != expected_head
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError):
        raise AdapterError(
            "Untrusted completed source attempt", "access"
        ) from None
    finished, began = run_completion(run)
    return finished, began, identity, attempt, source


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
                    "Workflow inventory remained unstable", "access"
                ) from None


def scan_family_history():
    """Select newest completed chronology, including older-ID rerun attempts."""
    current_id, current_attempt = current_run_identity()
    candidates = []
    total, scanned = None, 0
    seen_ids = set()
    for page in range(1, 101):
        result = workflow_api(f"/actions/runs?per_page=100&page={page}")
        count, runs = result.get("total_count"), result.get("workflow_runs")
        if (
            type(count) is not int
            or count < 0
            or not isinstance(runs, list)
            or len(runs) > 100
        ):
            raise AdapterError("Invalid workflow run inventory", "access")
        if total is None:
            total = count
        elif total != count:
            raise InventoryRace(
                ("Workflow inventory changed during restore"), ("access")
            )
        scanned += len(runs)
        for run in runs:
            if not isinstance(run, dict):
                raise AdapterError("Invalid workflow run entry", "access")
            run_id = run.get("id")
            if type(run_id) is not int or run_id <= 0 or run_id in seen_ids:
                raise AdapterError(
                    "Invalid or duplicate workflow run identity", "access"
                )
            seen_ids.add(run_id)
            source = family_source(run)
            if not source:
                continue
            if (
                run.get(("repository"), {}).get(("full_name"))
                != ("YouthOpps/data-pipeline")
                or run.get("head_repository", {}).get("full_name")
                != ("YouthOpps/data-pipeline")
                or run.get(("head_branch")) != ("main")
            ):
                raise AdapterError(
                    ("Workflow repository or branch mismatch"), ("access")
                )
            identity, attempt = run.get("id"), run.get("run_attempt")
            if (
                type(identity) is not int
                or identity <= 0
                or type(attempt) is not int
                or attempt <= 0
                or not isinstance(run.get("head_sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
            ):
                raise AdapterError("Invalid family run identity", "access")
            if identity == current_id:
                if attempt != current_attempt:
                    raise AdapterError(
                        ("Current workflow attempt mismatch"), ("access")
                    )
                if current_attempt > 1:
                    prior = workflow_api(
                        (
                            f"/actions/runs/{identity}/attempts/"
                            f"{current_attempt - 1}"
                        )
                    )
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, current_attempt - 1, source),
                            expected_head=run["head_sha"],
                        )
                    )
                continue
            if run.get(("status")) in (
                ("queued"),
                ("waiting"),
                ("pending"),
                ("requested"),
            ):
                if attempt > 1:
                    prior = workflow_api(
                        f"/actions/runs/{identity}/attempts/{attempt - 1}"
                    )
                    candidates.append(
                        completed_identity(
                            prior,
                            (identity, attempt - 1, source),
                            expected_head=run["head_sha"],
                        )
                    )
                continue
            if run.get("status") != "completed":
                raise AdapterError("Another family run is active", "access")
            candidates.append(completed_identity(run))
        if scanned >= total:
            if scanned != total:
                raise AdapterError(
                    ("Workflow inventory count mismatch"), ("access")
                )
            if not candidates:
                return None
            ordered = sorted(candidates)
            if len(ordered) > 1 and ordered[-1][0] == ordered[-2][0]:
                raise AdapterError(
                    ("Ambiguous family attempt chronology"), ("access")
                )
            return ordered[-1][2:]
        if len(runs) != 100:
            raise AdapterError("Incomplete workflow inventory", "access")
    raise AdapterError("Workflow inventory exceeds reviewed bound", "access")


def workflow_api(path):
    """Read workflow history without the publication credential."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise AdapterError("Workflow state credentials missing", "access")
    try:
        status, _, body = http_bytes(
            _WORKFLOW_GITHUB + path,
            headers={
                "Authorization": "Bearer " + token,
                "Accept": "application/vnd.github+json",
            },
        )
        if status != 200:
            raise ValueError()
        return json.loads(body)
    except Exception:
        raise AdapterError(
            "Workflow pacing state unavailable", "access"
        ) from None


def download_inert_artifact(identity, artifact_id, digest=None):
    """Read authenticated source/attempt-bound data; never execute artifacts."""
    try:
        status, headers, _ = http_bytes(
            _WORKFLOW_GITHUB + f"/actions/artifacts/{artifact_id}/zip",
            headers={
                "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
                "Accept": "application/vnd.github+json",
            },
            limit=1024,
        )
        if status != 302:
            raise ValueError()
        target = headers.get("Location", "")
        parsed = urllib.parse.urlsplit(target)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port
            or "." not in parsed.hostname
            or parsed.hostname.endswith((".local", ".internal", ".localhost"))
        ):
            raise ValueError()
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError()
        # Signed storage requests have no Authorization and are never logged.
        status, _, body = http_bytes(target, limit=32768)
        if status != 200:
            raise ValueError()
        if digest is not None and (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or digest[7:] != hashlib.sha256(body).hexdigest()
        ):
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            entries = archive.infolist()
            if len(entries) != 1 or entries[0].filename != "pacing-state.json":
                raise ValueError()
            entry = entries[0]
            if (
                entry.file_size > 16384
                or entry.flag_bits & 1
                or entry.external_attr >> 16 & 0o170000 == 0o120000
            ):
                raise ValueError()
            envelope = json.loads(archive.read(entry))
        run_id, attempt, source = identity
        if (
            not isinstance(envelope, dict)
            or set(envelope)
            != {
                "schema",
                "repository",
                "run_id",
                "run_attempt",
                "source",
                "state",
            }
            or type(envelope["schema"]) is not int
            or envelope["schema"] != 1
            or envelope["repository"] != "YouthOpps/data-pipeline"
            or type(envelope["run_id"]) is not int
            or envelope["run_id"] != run_id
            or type(envelope["run_attempt"]) is not int
            or envelope["run_attempt"] != attempt
            or envelope["source"] != source
        ):
            raise ValueError()
        return validate_budget(envelope["state"])
    except Exception:
        raise AdapterError(
            "Invalid or unavailable source pacing artifact", "access"
        ) from None


def merge_budget(local, incoming, now):
    """Intersect retained limits without weakening refusals or rate history."""
    validate_budget(local)
    validate_budget(incoming)
    merged = empty_budget(now)
    merged["until"] = max(local["until"], incoming["until"], now + 60)
    merged["interval"] = max(local["interval"], incoming["interval"])
    merged["page_interval"] = max(
        local["page_interval"], incoming["page_interval"]
    )
    merged["rate_failures"] = max(
        local["rate_failures"], incoming["rate_failures"]
    )
    merged["blocked"] = local["blocked"] or incoming["blocked"]
    merged["starts"] = sorted(set(local["starts"] + incoming["starts"]))[-5:]
    return validate_budget(merged)


def restore_family_artifact():
    """Require the exact newest completed source attempt, never an older hit."""
    global _RESTORED, _STATE_TRUSTED
    if _RESTORED:
        return
    latest = latest_family_run()
    if latest is None:
        try:
            bootstrap = json.loads(
                os.environ.get("LU_COOPERATION_PACING_BOOTSTRAP", "")
            )
            if (
                set(bootstrap) != {"schema", "family", "not_before", "evidence"}
                or type(bootstrap["schema"]) is not int
                or bootstrap["schema"] != 1
                or bootstrap["family"] != FAMILY
                or not numeric(bootstrap["not_before"])
                or not isinstance(bootstrap["evidence"], str)
                or not re.fullmatch(
                    r"https://github\.com/YouthOpps/data-pipeline/(?:issues|pull)/[0-9]+(?:#issuecomment-[0-9]+)?",
                    bootstrap["evidence"],
                )
            ):
                raise ValueError()
            incoming = empty_budget(time.time())
            incoming["until"] = max(incoming["until"], bootstrap["not_before"])
        except Exception:
            raise AdapterError(
                "Reviewed first-source bootstrap required", "access"
            ) from None
    else:
        run_id, attempt, _ = latest
        result = workflow_api(f"/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = result.get("artifacts")
        if (
            not isinstance(artifacts, list)
            or len(artifacts) > 100
            or result.get("total_count") != len(artifacts)
        ):
            raise AdapterError("Incomplete pacing artifact inventory", "access")
        matches = [
            a
            for a in artifacts
            if a.get("name") == _ARTIFACT_NAME + "-" + str(attempt)
        ]
        if len(matches) != 1 or matches[0].get("expired") is not False:
            raise AdapterError(
                "Latest attempt pacing artifact missing or expired", "access"
            )
        artifact = matches[0]
        if (
            type(artifact.get("id")) is not int
            or artifact["id"] <= 0
            or artifact.get("workflow_run", {}).get("id") != run_id
            or not numeric(artifact.get("size_in_bytes"))
            or artifact["size_in_bytes"] > 32768
        ):
            raise AdapterError("Invalid pacing artifact binding", "access")
        incoming = download_inert_artifact(
            latest, artifact["id"], artifact.get("digest")
        )
    with budget_state() as local:
        merged = merge_budget(local, incoming, time.time())
        local.clear()
        local.update(merged)
    _RESTORED, _STATE_TRUSTED = True, True


def invalidate_artifact(parent, name):
    """Invalidate only a safely owned artifact, before attempting unlink."""
    try:
        descriptor = os.open(
            name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent
        )
    except FileNotFoundError:
        return
    try:
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise AdapterError("Unsafe existing pacing artifact", "access")
        os.ftruncate(descriptor, 0)
        os.fsync(descriptor)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def artifact_output(path):
    """Keep a pinned output descriptor until export and signaling succeed."""
    global _PUBLISHER_GATE_FAILED
    configured = os.environ.get("RUNNER_TEMP", "")
    if not configured or not os.path.isabs(configured):
        raise AdapterError("Runner temporary directory required", "access")
    root = os.path.realpath(configured)
    if (
        os.path.dirname(os.path.abspath(path)) != root
        or os.path.basename(path) != "pacing-state.json"
    ):
        raise AdapterError("Unsafe pacing artifact path", "access")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    name = os.path.basename(path)
    try:
        info = os.fstat(parent)
        if (
            info.st_uid != os.getuid()
            or not stat.S_ISDIR(info.st_mode)
            or info.st_mode & 0o022
            or info.st_mode & 0o700 != 0o700
        ):
            raise AdapterError("Unsafe pacing artifact directory", "access")
        invalidate_artifact(parent, name)
        if _PUBLISHER_GATE_FAILED:
            raise AdapterError(
                "Publisher completion cannot be exported", "access"
            )
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent,
        )
        try:
            yield descriptor
            os.fsync(descriptor)
            os.fsync(parent)
        except BaseException:
            _PUBLISHER_GATE_FAILED = True
            # Keep the exact owned descriptor open until failure cleanup.
            # Truncate before unlink: failed unlink still leaves invalid JSON.
            os.ftruncate(descriptor, 0)
            os.fsync(descriptor)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            info = os.fstat(descriptor)
            if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino):
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
            raise

    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def export_family_artifact():
    """Export healthy state; invalidate owned stale output on failure."""
    path = os.environ.get("LU_COOPERATION_PACING_ARTIFACT_PATH")
    if not path or not (_STATE_TRUSTED or _PUBLISHER_GATE_FAILED):
        return
    with artifact_output(path) as descriptor:
        run_id, attempt = current_run_identity()
        with budget_state() as local:
            state = dict(local)
        envelope = {
            "schema": 1,
            "repository": "YouthOpps/data-pipeline",
            "run_id": run_id,
            "run_attempt": attempt,
            "source": SOURCE_ID,
            "state": state,
        }
        with os.fdopen(os.dup(descriptor), "w") as file:
            json.dump(envelope, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())


def github(method, path, payload=None, missing_ok=False):
    """Call only the configured publication repository with scoped credentials.

    Return the decoded JSON response, or None for an allowed missing resource.
    The optional payload is a JSON-serializable object.
    """
    token = os.environ.get("DATA_SOURCE_TOKEN")
    if not token:
        raise AdapterError(
            "DATA_SOURCE_TOKEN is required for publication", "publish"
        )
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
            payload=(
                json.dumps(payload).encode() if payload is not None else None
            ),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AdapterError(
            "GitHub connection failed; publication is unconfirmed", "publish"
        ) from error
    if status == 404 and missing_ok:
        return None
    if status not in (200, 201):
        raise AdapterError(
            f"GitHub publication API returned HTTP {status}", "publish", status
        )
    return json.loads(body)


def read_snapshot():
    """Read one source data/metadata pair at an exact remote main revision."""
    head = github("GET", "/git/ref/heads/main")["object"]["sha"]
    tree = github("GET", "/git/commits/" + head)["tree"]["sha"]
    files = {}
    for name in ("data.json", "metadata.json"):
        file = github(
            "GET",
            f"/contents/datas/{SOURCE_ID}/{name}?ref={head}",
            missing_ok=True,
        )
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
    """Atomically replace only source files, preserving concurrent updates.

    Files maps data/metadata filenames to complete JSON text. Retries rebase
    only when this source is unchanged; newer source snapshots are preserved.
    """
    expected = snapshot["files"].copy()
    desired = {**expected, **files}
    for attempt in range(3):
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
                    "Source changed concurrently; newer remote data "
                    "was not overwritten",
                    "publish",
                )
            snapshot = latest
            if attempt == 2:
                raise AdapterError(
                    "Publication failed; durable remote outcome "
                    "could not be updated",
                    "publish",
                )


def metadata(attempt, previous, records, error=None):
    """Build attributed run metadata, retaining the last success on failure."""
    result = {
        "display_name": "Luxembourg Development Cooperation",
        "source": SOURCE_ID,
        "source_url": SOURCE_URL,
        "website_url": WEBSITE_URL,
        "language": LANGUAGE,
        "publisher_country": PUBLISHER_COUNTRY,
        "publisher_type": PUBLISHER_TYPE,
        "status": "fail" if error else "success",
        "last_attempt_at": attempt,
        "last_success_at": (
            previous.get("last_success_at") if error else attempt
        ),
        "last_checked_at": (
            previous.get("last_checked_at") if error else attempt
        ),
        "record_count": len(records),
        "inventory_scope": "complete-advertised-youth-programmes-and-linked-calls",
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else f"Validated refresh retains {len(records)} records"
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def remove_expired_records(records, checked_at):
    """Filter verified deadlines after validating the complete fresh inventory.

    Unspecified recruitment closing times use the last civil date (UTC−12).
    """
    validate_records(records)
    checked = datetime.datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if deadline and re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
            closing = datetime.datetime.fromisoformat(deadline).date()
            latest_date = checked.astimezone(
                datetime.timezone(datetime.timedelta(hours=-12))
            ).date()
            expired = latest_date > closing
        elif deadline:
            closing = datetime.datetime.fromisoformat(
                deadline.replace("Z", "+00:00")
            )
            if closing.tzinfo is not None:
                expired = checked > closing
        if not expired:
            retained.append(record)
    validate_records(retained, allow_empty=True)
    return retained


def main():
    """Keep trusted pacing available even when snapshot/authentication fails."""
    global _STATE_TRUSTED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Write only this source to YouthOpps/data-source "
        "using DATA_SOURCE_TOKEN",
    )
    args = parser.parse_args()
    with collection_lock():
        if args.publish and os.environ.get("GITHUB_ACTIONS") == "true":
            restore_family_artifact()
        else:
            with budget_state():
                pass
        _STATE_TRUSTED = True
        try:
            return run_refresh(args.publish)
        finally:
            export_family_artifact()


def run_refresh(publish):
    """Refresh one source under the already-held lifecycle lock."""
    snapshot = read_snapshot() if publish else None
    old = (
        json.loads(snapshot["files"]["data.json"])
        if snapshot and snapshot["files"]["data.json"] is not None
        else []
    )
    previous = (
        json.loads(snapshot["files"]["metadata.json"])
        if snapshot and snapshot["files"]["metadata.json"] is not None
        else {}
    )
    if snapshot and snapshot["files"]["data.json"] is not None:
        validate_records(old, allow_empty=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = utc_now(), None
    try:
        records = collect()
        validate_records(records)
        records = remove_expired_records(records, utc_now())
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
                key: value
                for key, value in before.items()
                if key not in temporal
            } == {
                key: value
                for key, value in record.items()
                if key not in temporal
            }
            record.update(
                created_at=before.get("created_at", attempt),
                first_seen_at=before.get("first_seen_at", attempt),
                updated_at=(
                    before.get("updated_at", attempt) if same else attempt
                ),
                last_seen_at=attempt,
                last_checked_at=attempt,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
        validate_records(records, allow_empty=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(attempt, previous, records, failure)
    if publish and (not failure or snapshot["files"]["data.json"] is not None):
        files = {
            "metadata.json": json.dumps(outcome, ensure_ascii=False, indent=2)
            + "\n"
        }
        if not failure:
            files["data.json"] = (
                json.dumps(records, ensure_ascii=False, indent=2) + "\n"
            )
        try:
            publish_files(snapshot, files)
        except Exception as error:
            failure = error
            outcome = metadata(
                attempt,
                previous,
                old,
                AdapterError(
                    "Publication failed: " + safe_error(error), "publish"
                ),
            )
            try:
                latest = read_snapshot()
                if (
                    snapshot["files"]["data.json"] is None
                    or latest["files"] != snapshot["files"]
                ):
                    raise AdapterError(
                        "No safe previous snapshot for failure reporting",
                        "publish",
                    )
                publish_files(
                    latest,
                    {
                        "metadata.json": json.dumps(
                            outcome, ensure_ascii=False, indent=2
                        )
                        + "\n"
                    },
                )
            except Exception:
                outcome["message"] = (
                    "Publication failed; durable failure status could not be "
                    "recorded. Remote data was not force-overwritten."
                )
    elif publish:
        outcome["message"] = (
            "First collection failed; no published source folder created"
        )
    print(json.dumps(outcome, ensure_ascii=False))
    return 1 if failure else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(
            json.dumps(
                {
                    "source": SOURCE_ID,
                    "status": "fail",
                    "last_attempt_at": utc_now(),
                    "message": "Run could not complete; "
                    "durable status unavailable",
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
