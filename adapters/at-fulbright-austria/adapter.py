"""Discover and parse live Fulbright Austria awards and teaching employment."""

import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

SOURCE_ID = "at-fulbright-austria"
SOURCE_URL = (
    "https://www.fulbright.at/programs/in-austria/students/"
    "full-time-study-research-grants"
)
WEBSITE_URL = "https://www.fulbright.at/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "AT"
PUBLISHER_TYPE = "non-profit"
ATTRIBUTION = "Fulbright Austria — Austrian-American Educational Commission"


class Node:
    """Minimal HTML tree retaining official links and structural boundaries."""

    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def text(self):
        if self.tag in ("script", "style", "noscript"):
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
            class_name is None
            or class_name in n.attrs.get("class", "").split()
        )
    ]


def one(values, description):
    if len(values) != 1:
        raise AdapterError("Expected one " + description, "parse")
    return values[0]


def page(url):
    document = fetch_source(url, min_interval=check_robots(url))
    if not re.search(
        r"</body\s*>\s*</html\s*>\s*(?:<!--[\s\S]*?-->\s*)*$", document, re.I
    ):
        raise AdapterError("Incomplete publisher document", "parse")
    root = PublisherHTML(document).root
    title = " ".join(n.text() for n in nodes(root, "title")).lower()
    if re.match(
        r"just a moment|access denied|attention required|checking your browser",
        title,
    ):
        raise AdapterError("Publisher access challenge", "access")
    return root


def main_content(root):
    return one(nodes(root, "main"), "complete public programme content")


_MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        1,
    )
}
_MONTH_PATTERN = "(?:" + "|".join(_MONTHS) + ")"


def own_url(value, base=WEBSITE_URL):
    """Normalize public publisher links without admitting external portals."""
    url = urllib.parse.urljoin(base, value)
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.fulbright.at"
        or parsed.username
        or parsed.password
        or parsed.query
    ):
        return None
    return urllib.parse.urlunsplit(
        ("https", parsed.netloc, parsed.path.rstrip("/"), "", "")
    )


def sections(content):
    """Keep programme paragraphs under their live headings, excluding people."""
    result = {}
    heading = "Introduction"
    for node in content.walk():
        if node.tag in ("h1", "h2", "h3", "h4"):
            heading = node.text()
            if re.search(
                r"recipients|contact person|related content", heading, re.I
            ):
                break
        elif node.tag in ("p", "li") and node.text():
            text = node.text()
            if "@" not in text and "Contact now" not in text:
                result.setdefault(heading, []).append(text)
    return {
        key: " ".join(dict.fromkeys(values)) for key, values in result.items()
    }


def calendar_dates(text):
    """Read explicit English dates; do not invent a year or a closing hour."""
    expression = (
        rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\s+(20\d{{2}})\b"
        rf"|\b({_MONTH_PATTERN})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b"
    )
    dates = []
    for match in re.finditer(expression, text, re.I):
        day, month, year = match.group(1, 2, 3)
        if day is None:
            month, day, year = match.group(4, 5, 6)
        try:
            dates.append(
                datetime(int(year), _MONTHS[month.lower()], int(day))
                .date()
                .isoformat()
            )
        except ValueError as error:
            raise AdapterError(
                "Invalid published application date", "parse"
            ) from error
    return dates


def application_window(text):
    match = re.search(r"Application Period\s*:?(.*?)Grant Length", text, re.I)
    if not match:
        return None, None, "unknown"
    period = match[1].strip()
    now = utc_now()
    today = now[:10]
    windows = []
    for part in period.split(";"):
        dates = calendar_dates(part)
        if not dates:
            continue
        end = dates[-1]
        start = dates[0] if len(dates) > 1 else None
        if start is None:
            opening = re.search(
                rf"\b(\d{{1,2}})\s+({_MONTH_PATTERN})\s*[–—-]", part, re.I
            )
            if opening:
                start = (
                    datetime(
                        int(end[:4]),
                        _MONTHS[opening[2].lower()],
                        int(opening[1]),
                    )
                    .date()
                    .isoformat()
                )
                if start > end:
                    start = (
                        datetime(
                            int(end[:4]) - 1,
                            _MONTHS[opening[2].lower()],
                            int(opening[1]),
                        )
                        .date()
                        .isoformat()
                    )
        clock = re.search(
            r"(?:(\d{1,2}):(\d{2})\s*(?:\(noon\))?|\b(noon))\s*(CET|CEST|UTC|GMT)\b",
            re.split(r"20\d{2}", part)[-1],
            re.I,
        )
        if clock:
            offset = {"CET": 1, "CEST": 2, "UTC": 0, "GMT": 0}[
                clock[4].upper()
            ]
            end = (
                datetime.fromisoformat(end)
                .replace(
                    hour=12 if clock[3] else int(clock[1]),
                    minute=0 if clock[3] else int(clock[2]),
                    tzinfo=timezone(timedelta(hours=offset)),
                )
                .astimezone(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )
        windows.append((start, end))
    if not windows:
        return period, None, "unknown"

    def remains_open(end):
        if len(end) == 10:
            return end >= today
        return datetime.fromisoformat(
            end.replace("Z", "+00:00")
        ) > datetime.fromisoformat(now.replace("Z", "+00:00"))

    start, end = next(
        (window for window in windows if remains_open(window[1])), windows[-1]
    )
    status = "unknown" if remains_open(end) else "expired"
    if (
        start
        and start <= today
        and remains_open(end)
        and (len(end) > 10 or today < end)
    ):
        status = "open"
    return period, end, status


def programme_record(url, root):
    content = main_content(root)
    title = one(nodes(content, "h1"), "award title").text()
    blocks = sections(content)
    text = content.text().split("Recipients of", 1)[0]
    slug = url.rsplit("/", 1)[-1]
    track = {
        "fulbright-austria-expert-award": "expert",
        "fulbright-prize-in-american-studies": "american-studies-prize",
    }.get(slug, slug)
    institutional = bool(
        re.search(r"Austrian institutions(?: of [\w ,]+)? apply", text, re.I)
    )
    category = "grants" if institutional else "scholarships"
    if "us-teaching-assistant-program" == slug:
        category = "jobs"
    elif "/awards/" in url:
        category = "competitions"
    hosts = []
    if "schuman" not in slug and "/programs/" in url:
        hosts = [
            (
                "US"
                if "/in-the-usa/" in url or "foreign-langauge" in slug
                else "AT"
            )
        ]
    record = make_record(
        title,
        url,
        [category],
        kind="institutional-grant" if institutional else "opportunity",
        host_countries=hosts,
        method="publisher-html",
        evidence=[ATTRIBUTION, url],
    )
    record["id"] = hashlib.sha256(f"{SOURCE_ID}|{track}".encode()).hexdigest()[
        :24
    ]
    period, record["deadline"], record["status"] = application_window(text)
    eligibility = " ".join(
        value
        for key, value in blocks.items()
        if re.search(r"eligib|qualification", key, re.I)
    )
    if not institutional:
        for country, pattern in (
            ("US", r"(?:must have|require[ds]?) US citizenship"),
            ("AT", r"(?:must have|require[ds]?) Austrian citizenship"),
        ):
            if re.search(pattern, eligibility, re.I):
                record["eligible_countries"].append(country)
    if record["deadline"] is None:
        deadlines = []
        for value in blocks.values():
            for sentence in re.split(r"(?<=[.!?])\s+", value):
                if re.search(r"deadline|apply by|submit.*by", sentence, re.I):
                    deadlines.extend(calendar_dates(sentence))
        if len(set(deadlines)) == 1:
            record["deadline"] = deadlines[0]
            if deadlines[0] < utc_now()[:10]:
                record["status"] = "expired"
    pieces = []
    if period:
        pieces.append("Application period: " + period + ".")
    for label, pattern in (
        ("Funding", r"amount.*benefits|^benefits$"),
        ("Eligibility", r"qualification|eligib"),
        (
            "Programme",
            r"grant summary|quick facts|candidate profile|award details",
        ),
    ):
        values = [
            value
            for key, value in blocks.items()
            if re.search(pattern, key, re.I)
        ]
        if values:
            value = values[0]
            if len(value) > 160:
                value = value[:157].rsplit(" ", 1)[0] + "..."
            pieces.append(label + ": " + value)
    if not pieces:
        values = [value for value in blocks.values() if len(value) > 40]
        if not values:
            raise AdapterError(
                "Award has no substantive public description: " + url, "parse"
            )
        pieces.append(values[0][:500])
    summary = " ".join(pieces)
    if len(summary.encode("utf-16-le")) > 1200:
        summary = (
            summary.encode("utf-16-le")[:1188]
            .decode("utf-16-le", errors="ignore")
            .rsplit(" ", 1)[0]
            + "..."
        )
    record["summary"] = summary
    if re.search(
        r"not (?:offered|available).*20\d{2}|currently inactive", text, re.I
    ):
        record["status"] = "unknown"
    records = [record]
    for heading in blocks:
        if (
            heading
            == "Fulbright Specialist in Music, Performing Arts and Visual Arts"
        ):
            arts = make_record(
                heading,
                url,
                ["grants"],
                kind="institutional-grant",
                host_countries=["AT"],
                method="publisher-html",
                evidence=[ATTRIBUTION, url],
            )
            arts["id"] = hashlib.sha256(
                f"{SOURCE_ID}|specialist-arts".encode()
            ).hexdigest()[:24]
            arts["summary"] = " ".join(
                label + ": " + blocks[label]
                for label in (
                    "Length of grant",
                    "Benefits",
                    "Specialization(s)",
                )
                if label in blocks
            )[:600]
            if not arts["summary"]:
                arts["summary"] = blocks[heading][:600]
            records.append(arts)
    return records


def collect():
    sitemap_url = WEBSITE_URL + "sitemap"
    sitemap = page(sitemap_url)
    hubs, awards = set(), set()
    for link in nodes(sitemap, "a"):
        url = own_url(link.attrs.get("href", ""))
        if not url:
            continue
        path = urllib.parse.urlsplit(url).path
        if re.fullmatch(
            r"/programs/in-(?:austria|the-usa)/(?:students|scholars|teaching-assistants)",
            path,
        ):
            hubs.add(url)
        elif path.startswith("/awards/"):
            awards.add(url)
        elif path.startswith("/programs/us-eu-"):
            hubs.add(url)
    if not hubs:
        raise AdapterError(
            "No programme directories in publisher sitemap", "parse"
        )
    for url in sorted(hubs):
        for link in nodes(main_content(page(url)), "a"):
            target = own_url(link.attrs.get("href", ""), url)
            if target and "/grant-details/" in target:
                awards.add(target)
    if not awards:
        raise AdapterError(
            "No awards discovered in programme directories", "parse"
        )
    records = {}
    for url in sorted(awards):
        for record in programme_record(url, page(url)):
            # The publisher links the same EU Schuman programme from both directions.
            if record["id"] in records and not url.endswith(
                "/fulbright-schuman-program"
            ):
                raise AdapterError(
                    "Duplicate discovered award identity: " + url, "validate"
                )
            records[record["id"]] = record
    result = sorted(records.values(), key=lambda record: record["id"])
    validate_records(result)
    return result


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
_USER_AGENT = "YouthOpp/1.0 (+https://github.com/YouthOpps/data-pipeline)"


class AdapterError(Exception):
    def __init__(self, message, stage="parse", status=None):
        super().__init__(message)
        self.stage, self.status = stage, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, url):
        return None


_OPENER = urllib.request.build_opener(NoRedirect)
_ROBOTS = {}


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


def pace(url, interval=6):
    """Lock shared publisher pacing state across overlapping local processes.

    State lives outside the checkout. The advisory lock stays held until the
    response and any Retry-After are recorded, so parallel runs cannot race.
    Production uses a serialized Action; separate machines must not overlap.
    """
    host = (
        (urllib.parse.urlsplit(url).hostname or "")
        .lower()
        .removeprefix("www.")
    )
    directory = os.path.join(
        tempfile.gettempdir(), SOURCE_ID + "-pacing-" + str(os.getuid())
    )
    os.makedirs(directory, mode=0o700, exist_ok=True)
    information = os.lstat(directory)
    if (
        not os.path.isdir(directory)
        or os.path.islink(directory)
        or information.st_uid != os.getuid()
        or information.st_mode & 0o077
    ):
        raise AdapterError("Unsafe publisher pacing directory", "access")
    publisher = host
    key = hashlib.sha256(publisher.encode()).hexdigest()
    descriptor = os.open(
        os.path.join(directory, key),
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    state = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(state.fileno(), fcntl.LOCK_EX)
        raw = state.read()
        budget = (
            json.loads(raw)
            if raw
            else {"starts": [], "interval": 6, "until": 0}
        )
        budget["interval"] = max(budget["interval"], interval, 6)
        while True:
            now = time.time()
            budget["starts"] = [
                start for start in budget["starts"] if now - start < 60
            ]
            ready = max(
                budget["until"],
                (
                    budget["starts"][-1] + budget["interval"]
                    if budget["starts"]
                    else now
                ),
                (
                    budget["starts"][0] + 60
                    if len(budget["starts"]) >= 10
                    else now
                ),
            )
            if ready <= now:
                budget["starts"].append(now)
                save_budget(state, budget)
                return state, budget
            time.sleep(min(ready - now, 60))
    except Exception:
        state.close()
        raise


def save_budget(state, budget):
    state.seek(0)
    json.dump(budget, state)
    state.truncate()
    state.flush()
    os.fsync(state.fileno())


def request_bytes(
    url, *, interval=6, method="GET", headers=None, payload=None
):
    state, budget = pace(url, interval)
    request_headers = {"User-Agent": _USER_AGENT, **(headers or {})}
    try:
        request = urllib.request.Request(
            url,
            data=payload,
            method=method,
            headers=request_headers,
        )
        try:
            response = _OPENER.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            retry = response.headers.get("Retry-After")
            if retry:
                try:
                    seconds = (
                        float(retry)
                        if re.fullmatch(r"\d+(?:\.\d+)?", retry)
                        else parsedate_to_datetime(retry).timestamp()
                        - time.time()
                    )
                    budget["until"] = max(
                        budget["until"], time.time() + max(0, seconds)
                    )
                    save_budget(state, budget)
                except (ValueError, TypeError, OverflowError):
                    pass
            body = response.read(5_000_001)
            if len(body) > 5_000_000:
                raise AdapterError("Response exceeds 5 MB", "fetch")
            return response.status, response.headers, body
    finally:
        state.close()


def fetch_source(url, min_interval=6):
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

    for attempt in range(2):
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
                    current = urllib.parse.urljoin(
                        current, headers["Location"]
                    )
                    checked(current)
                    if original.path != "/robots.txt":
                        min_interval = max(min_interval, check_robots(current))
                    continue
                if status != 200:
                    raise AdapterError(
                        f"Publisher returned HTTP {status} for "
                        + urllib.parse.urlunsplit(
                            (
                                urllib.parse.urlsplit(current).scheme,
                                urllib.parse.urlsplit(current).hostname or "",
                                urllib.parse.urlsplit(current).path,
                                "",
                                "",
                            )
                        ),
                        "fetch",
                        status,
                    )
                return body.decode(headers.get_content_charset() or "utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if attempt:
                raise AdapterError(
                    "Publisher connection failed: " + safe_error(error),
                    "fetch",
                ) from error
    raise AdapterError("Publisher could not be read", "fetch")


def check_robots(url):
    parsed = urllib.parse.urlsplit(url)
    origin = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "", "", "")
    )
    if origin not in _ROBOTS:
        try:
            _ROBOTS[origin] = fetch_source(origin + "/robots.txt")
        except AdapterError as error:
            if error.status == 404:
                _ROBOTS[origin] = ""
            else:
                raise
    text = _ROBOTS[origin]
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
    specific = [g for g in groups if "youthopp" in g["agents"]]
    selected = specific or [g for g in groups if "*" in g["agents"]]
    path = parsed.path + ("?" + parsed.query if parsed.query else "")
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
        raise AdapterError(
            "Robots policy disallows this source endpoint", "access"
        )
    return max([6] + [g["delay"] for g in selected])


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


def remove_expired_records(records, checked_at):
    """Filter a validated refresh before its atomic replacement publication."""
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError("Cleanup requires an aware check time", "validate")
    retained = []
    for record in records:
        value = record.get("deadline")
        expired = False
        if isinstance(value, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    # An undocumented zone cannot justify an earlier cutoff.
                    closing_date = datetime.strptime(value, "%Y-%m-%d").date()
                    latest_zone = timezone(timedelta(hours=-12))
                    expired = (
                        checked.astimezone(latest_zone).date() > closing_date
                    )
                else:
                    deadline = datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
                    expired = deadline.tzinfo is not None and checked > deadline
            except (ValueError, TypeError):
                # Missing or ambiguous deadlines never imply expiry.
                pass
        if not expired:
            retained.append(record)
    return retained


def validate_records(records, published=False):
    """Validate all real records against the source and consumer contracts."""
    if not isinstance(records, list) or (not records and not published):
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
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
                raise AdapterError(
                    "Missing record field: " + field, "validate"
                )
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
            raise AdapterError(
                "Invalid opportunity kind or status", "validate"
            )
        summary = record.get("summary")
        if not isinstance(summary, str) or re.search("<[^>]+>", summary):
            raise AdapterError("Invalid plain summary", "validate")
        length = len(summary.encode("utf-16-le")) // 2
        if length > 600:
            raise AdapterError("Invalid plain summary", "validate")
        for field in ("location", "language", "publisher_country"):
            if (
                field not in record
                or record[field] is not None
                and not isinstance(record[field], str)
            ):
                raise AdapterError(
                    "Invalid record field: " + field, "validate"
                )
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


def metadata(attempt, previous, records, error=None, checked_at=None):
    result = {
        "source": SOURCE_ID,
        "name": "Fulbright Austria",
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
            previous.get("last_checked_at")
            if error
            else checked_at
            or max(
                (record["last_checked_at"] for record in records),
                default=attempt,
            )
        ),
        "record_count": len(records),
        "message": (
            "Collection failed; last-good data preserved"
            if error
            else (
                f"Validated refresh retains {len(records)} "
                "validated Fulbright Austria "
                "awards and teaching positions"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        action="store_true",
        help=(
            "Write only this source to YouthOpps/data-source "
            "using DATA_SOURCE_TOKEN"
        ),
    )
    args = parser.parse_args()
    snapshot = read_snapshot() if args.publish else None
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
        validate_records(old, published=True)
        if previous.get("source") != SOURCE_ID:
            raise AdapterError(
                "Remote metadata source mismatch; no files changed", "publish"
            )
    attempt, failure = utc_now(), None
    try:
        records = collect()
        checked_at = utc_now()
        validate_records(records)
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
                last_checked_at=checked_at,
            )
        records.sort(key=lambda record: record["id"])
        records.sort(
            key=lambda record: record["published_at"] or "", reverse=True
        )
        validate_records(records)
        records = remove_expired_records(records, checked_at)
        validate_records(records, published=True)
    except Exception as error:
        failure, records = error, old
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
    if args.publish and (
        not failure or snapshot["files"]["data.json"] is not None
    ):
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
    elif args.publish:
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
                    "message": (
                        "Run could not complete; " "durable status unavailable"
                    ),
                    "error": safe_error(error),
                    "failure_stage": getattr(error, "stage", "publish"),
                },
                ensure_ascii=False,
            )
        )
        sys.exit(1)
