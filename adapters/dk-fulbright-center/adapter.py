"""Discover and parse live Fulbright Denmark funding and educational programmes."""

import argparse
import base64
from datetime import datetime, timezone
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
import xml.etree.ElementTree as ET

SOURCE_ID = "dk-fulbright-center"
SOURCE_URL = (
    "https://fulbrightcenter.dk/go-to-the-us/"
    "fulbright-grants-for-danish-students/apply/"
)
WEBSITE_URL = "https://fulbrightcenter.dk/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "DK"
PUBLISHER_TYPE = "government"
ATTRIBUTION = "Fulbright Denmark — Danish-American Fulbright Commission"


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


def article(root):
    return one(nodes(root, "article"), "complete publisher article")


# Compatibility keys identify existing programmes; no award facts are cached.
_LEGACY_KEYS = {
    "fulbright-grants-for-danish-students": "student-grant",
    "fulbright-co-funded": "arts-co-funded",
    "grants-for-scholars": "danish-scholar",
    "fulbright-for-students": "us-student",
    "scholar-grants-all-disciplines": "us-scholar-all-disciplines",
    "fulbright-distinguished-chair-in-american-studies": "us-scholar-distinguished-american-studies",
    "scholar-grants-community-college": "us-scholar-community-college",
    "fulbright-inter-country-travel-grant": "intercountry-collaborative-visit",
    "book-en-fulbright-forsker": "book-scholar-institutional-lecture",
    "arctic-initiative-iii": "arctic-initiative-iii-2021",
}
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


def own_url(value, base=WEBSITE_URL):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base, value))
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in ("fulbrightcenter.dk", "www.fulbrightcenter.dk")
        or parsed.username
        or parsed.password
        or parsed.query
    ):
        return None
    return "https://fulbrightcenter.dk" + parsed.path.rstrip("/") + "/"


def programme_path(url):
    path = urllib.parse.urlsplit(url).path
    if not path.startswith(
        (
            "/go-to-the-us/",
            "/grantsforamericans/",
            "/scholars/arctic-initiative-iii/",
        )
    ):
        return path in ("/hosts/", "/book-en-fulbright-forsker/")
    if re.search(
        r"/(?:apply|application|alumni|students|other-grants)/$", path
    ):
        return False
    if re.search(
        r"/(?:current|previous)-[^/]*(?:fulbrighter|grantee|recipient)[^/]*/$",
        path,
    ):
        return False
    # Current Arctic IV requires external IIE material whose terms forbid collection.
    return not re.search(r"/fulbright-arctic-initiative-", path)


def discover_urls():
    """Traverse current page-sitemap partitions, not an exact reviewed URL set."""
    pending = [WEBSITE_URL + "sitemap.xml"]
    visited, urls = set(), set()
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if len(visited) > 100:
            raise AdapterError(
                "Publisher sitemap traversal exceeds bound", "parse"
            )
        document = fetch_source(url, min_interval=check_robots(url))
        try:
            root = ET.fromstring(document)
        except ET.ParseError as error:
            raise AdapterError("Invalid publisher sitemap", "parse") from error
        kind = root.tag.rsplit("}", 1)[-1]
        if kind not in ("sitemapindex", "urlset"):
            raise AdapterError("Unknown publisher sitemap document", "parse")
        for entry in root:
            location = entry.find("{*}loc")
            if location is None or not location.text:
                continue
            target = own_url(location.text)
            if not target:
                continue
            if kind == "sitemapindex":
                if re.search(r"/page-sitemap\d*\.xml/$", target):
                    pending.append(target.rstrip("/"))
            elif programme_path(target):
                urls.add(target)
    if not urls:
        raise AdapterError(
            "No programme URLs in current publisher sitemap", "parse"
        )
    return urls


def content_sections(root):
    result = []
    heading, values = "Introduction", []
    for node in article(root).walk():
        if node.tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            if values or heading != "Introduction":
                result.append((heading, " ".join(dict.fromkeys(values))))
            heading, values = node.text(), []
            if re.search(
                r"related grants|read about previous|useful shortcuts|explore the universities|meet the co-lead scholars",
                heading,
                re.I,
            ):
                return result
        elif node.tag in ("p", "li"):
            text = node.text()
            if text and "@" not in text:
                values.append(text)
    if values:
        result.append((heading, " ".join(dict.fromkeys(values))))
    return result


def deadline_value(text):
    """Normalize a stated year/date and only explicitly unambiguous CET clocks."""
    months = "(?:" + "|".join(_MONTHS) + ")"
    matches = list(
        re.finditer(
            rf"\b({months})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b|\b(\d{{1,2}})\s+({months})\s+(20\d{{2}})\b",
            text,
            re.I,
        )
    )
    dates = []
    for match in matches:
        month, day, year = match.group(1, 2, 3)
        if month is None:
            day, month, year = match.group(4, 5, 6)
        value = datetime(int(year), _MONTHS[month.lower()], int(day))
        tail = text[match.end() : match.end() + 65]
        if re.search(r"(?:noon|12[:.]00).*\bCET\b", tail, re.I):
            dates.append(
                value.replace(hour=11, tzinfo=timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
        else:
            dates.append(value.date().isoformat())
    return dates[0] if len(set(dates)) == 1 else None


def track_key(url, label):
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    value = label.lower()
    if slug == "fulbright-joint-grants":
        for word, key in (
            ("florida", "joint-florida-poly"),
            ("columbia", "joint-columbia-gsas"),
            ("new york", "joint-nyu-gsas"),
            ("chicago", "joint-chicago-physical-sciences"),
            ("pittsburgh", "joint-pittsburgh-gspia"),
        ):
            if word in value:
                return key
    if slug == "fulbright-schuman":
        prefix = (
            "schuman-incoming-"
            if "/grantsforamericans/" in url
            else "schuman-"
        )
        if "post" in value:
            return prefix + "postdoctoral"
        if "pre" in value or "student" in value:
            return prefix + "pre-doctoral"
        if "educator" in value:
            return prefix + "international-educators"
    if "crown-prince" in slug:
        if "mid-career" in value:
            return "crown-midcareer-mpa"
        if "full master" in value:
            return "crown-full-mpp-mpa"
    if slug == "thanks-to-scandinavia":
        if re.fullmatch(r"(?:\d+\s+)?victor borge music scholarships?", value):
            return "thanks-victor-borge-music"
        if re.fullmatch(
            r"(?:\d+\s+)?scholarships? for a master['’]s degree", value
        ):
            return "thanks-general-degree"
    if "inger-p-davis" in slug:
        country = (
            "danish"
            if "danish" in value
            else "us" if "u.s." in value else None
        )
        role = (
            "student"
            if "student" in value
            else "scholar" if "scholar" in value else None
        )
        if country and role:
            return "inger-" + country + "-" + role
    if slug == "hosts":
        if "digital transformation" in value:
            return "us-scholar-maritime-digital-transformation"
        if "maritime research" in value:
            return "us-scholar-maritime-research-education-development"
    if slug == "fulbright-specialist-grant-program":
        return (
            "specialist-individual-roster-assignment"
            if label == "Specialists"
            else "specialist-institutional-project"
        )
    return _LEGACY_KEYS.get(
        slug,
        url + ("#" + re.sub(r"\W+", "-", value).strip("-") if label else ""),
    )


def variants(url, root, blocks):
    """Discover multiple awards from the publisher's labelled grant sections."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    if (
        slug in ("fulbright-joint-grants", "thanks-to-scandinavia")
        or "inger-p-davis" in slug
    ):
        active, result = False, []
        for heading, text in blocks:
            if re.search(
                r"^Available (?:Grants|Scholarships)$", heading, re.I
            ):
                active = True
            elif heading == "How":
                active = False
            elif active and heading:
                result.append((heading, text))
        return result
    if "crown-prince" in slug:
        return [
            (heading, text)
            for heading, text in blocks
            if re.match(r"\d.*scholarship", heading, re.I)
        ]
    if slug == "fulbright-schuman":
        labels = []
        for node in article(root).walk():
            if node.tag not in ("a", "p"):
                continue
            title = node.text()
            if (
                re.fullmatch(
                    r"(?:Scholars|Students)\s*[–-].*Doctoral Research.*|International Educators",
                    title,
                    re.I,
                )
                and title not in labels
            ):
                labels.append(title)
        return [(label, "") for label in labels]
    if slug == "hosts":
        result = []
        for node in nodes(article(root), "p"):
            text = node.text()
            if "Maritime" in text and ":" in text:
                title, facts = text.split(":", 1)
                for label in title.split(" & Maritime"):
                    if label.startswith(" Research"):
                        label = "Maritime" + label
                    result.append((label, facts.strip()))
        return result
    if slug == "fulbright-specialist-grant-program":
        text = " ".join(value for _, value in blocks)
        result = [
            (
                "Host institutions",
                " ".join(
                    value
                    for heading, value in blocks
                    if "Project activities" == heading
                ),
            )
        ]
        eligibility = re.search(r"For specialists:.*?(?=For |$)", text)
        if eligibility:
            result.append(("Specialists", eligibility[0]))
        return result
    return [("", "")]


def parse_programme(url, root, pages):
    blocks = content_sections(root)
    if not blocks:
        raise AdapterError("Missing programme content: " + url, "parse")
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    if slug in (
        "go-to-the-us",
        "grantsforamericans",
        "fulbright-for-scholars",
    ):
        return []
    if not any(
        re.search(
            r"Academic year|Deadline|Eligibility|Available Grants|Who can apply",
            key,
            re.I,
        )
        for key, _ in blocks
    ) and slug not in ("hosts", "book-en-fulbright-forsker"):
        raise AdapterError(
            "Programme lacks recognizable award fields: " + url, "parse"
        )
    page_titles = nodes(root, "title")
    title = page_titles[0].text() if page_titles else blocks[0][0]
    title = re.split(r"\s+[–|-]\s+Fulbright", title)[0].strip()
    if not title:
        raise AdapterError("Programme title missing", "parse")
    fields = {heading: text for heading, text in blocks}
    deadlines = " ".join(value for key, value in blocks if "Deadline" in key)
    evidence = [ATTRIBUTION, url]
    # Follow explicitly shared application routes for dates, never a cached cycle.
    if re.search(
        r"follow.*deadline.*Core Fulbright Student Grant", deadlines, re.I
    ):
        # Some publisher links use an obsolete alias or uninformative link text.
        candidates = [
            candidate
            for candidate in pages
            if track_key(candidate, "") == "student-grant"
        ]
        if not candidates:
            for link in nodes(article(root), "a"):
                target = own_url(link.attrs.get("href", ""), url)
                if target and link.text() == "Core Fulbright Student Grant":
                    if target not in pages:
                        pages[target] = page(target)
                    candidates.append(target)
        if len(set(candidates)) == 1:
            target = candidates[0]
            parent = content_sections(pages[target])
            deadlines = " ".join(
                value for key, value in parent if "Deadline" in key
            )
            evidence.append(target)
    variants_found = variants(url, root, blocks)
    if not variants_found:
        raise AdapterError("No funding tracks in programme: " + url, "parse")
    results = []
    for label, facts in variants_found:
        hosts = ["US"] if "/go-to-the-us/" in url else ["DK"]
        if (
            "arctic" in slug
            or "schuman" in slug
            and "/grantsforamericans/" in url
        ):
            hosts = []
        if "inger-p-davis" in slug:
            hosts = ["US"] if "Danish" in label else ["DK"]
        institutional = (
            slug == "fulbright-specialist-grant-program"
            and label == "Host institutions"
        )
        category = (
            "grants"
            if institutional or "inter-country" in slug
            else "scholarships"
        )
        kind = "institutional-grant" if institutional else "opportunity"
        if slug == "book-en-fulbright-forsker":
            category, kind = "training", "programme-overview"
        record = make_record(
            title + (" - " + label if label else ""),
            url,
            [category],
            kind=kind,
            host_countries=hosts,
            method="publisher-html",
            evidence=evidence,
        )
        record["id"] = hashlib.sha256(
            f"{SOURCE_ID}|{track_key(url, label)}".encode()
        ).hexdigest()[:24]
        relevant_deadlines = deadlines
        if "inger-p-davis" in slug:
            relevant_deadlines = fields.get(
                (
                    "Deadline for Danes"
                    if "Danish" in label
                    else "Deadline for Americans"
                ),
                "",
            )
        record["deadline"] = deadline_value(relevant_deadlines)
        if record["deadline"]:
            now = utc_now() if len(record["deadline"]) > 10 else utc_now()[:10]
            record["status"] = (
                "expired" if record["deadline"] < now else "unknown"
            )
            if len(record["deadline"]) > 10 and record["deadline"] > now:
                record["status"] = "open"
        elif re.search(r"closed", relevant_deadlines, re.I):
            record["status"] = "expired"
        eligibility = " ".join(
            value
            for key, value in blocks
            if re.search(
                r"eligib|Requirements|Citizenship|Who can apply", key, re.I
            )
        )
        if not institutional:
            if re.search(
                r"must have Danish citizenship|Students with Danish citizenship",
                eligibility,
                re.I,
            ):
                record["eligible_countries"] = ["DK"]
            elif re.search(
                r"(?:must be|be a|for) (?:U\.S\.|US|American) citizen",
                eligibility,
                re.I,
            ):
                record["eligible_countries"] = ["US"]
        pieces = []
        cycle = fields.get("Academic year")
        if cycle:
            pieces.append("Academic year: " + cycle + ".")
        if facts:
            pieces.append("Award: " + facts[:210] + ".")
        for heading in ("Amount", "Benefits", "Duration"):
            if heading in fields and not (facts and heading == "Amount"):
                value = fields[heading]
                if slug == "fulbright-schuman" and heading == "Benefits":
                    if "Students" in label:
                        value = re.split(r"Scholars:", value)[0]
                    elif "Scholars" in label:
                        value = value.partition("Scholars:")[2] or value
                    else:
                        continue
                pieces.append(heading + ": " + value[:170] + ".")
        if relevant_deadlines:
            pieces.append("Deadline: " + relevant_deadlines[:130] + ".")
        if eligibility:
            pieces.append("Eligibility: " + eligibility[:180] + ".")
        if not pieces:
            pieces = [next((value for _, value in blocks if value), "")[:500]]
        record["summary"] = (
            " ".join(pieces)
            .encode("utf-16-le")[:1196]
            .decode("utf-16-le", errors="ignore")
        )
        results.append(record)
    return results


def collect():
    urls = discover_urls()
    pages = {}
    pending = sorted(urls)
    while pending:
        url = pending.pop(0)
        if url in pages:
            continue
        if len(pages) >= 150:
            raise AdapterError("Programme discovery exceeds bound", "parse")
        pages[url] = page(url)
        for link in nodes(article(pages[url]), "a"):
            target = own_url(link.attrs.get("href", ""), url)
            if (
                target
                and programme_path(target)
                and target not in pages
                and target not in pending
            ):
                pending.append(target)
    records = []
    for url, root in sorted(pages.items()):
        records.extend(parse_programme(url, root, pages))
    validate_records(records)
    return sorted(records, key=lambda record: record["id"])


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
                f"Collected {len(records)} validated Fulbright Denmark "
                "funding and educational programmes"
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
        validate_records(old)
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
    except Exception as error:
        failure, records = error, old
    outcome = metadata(
        attempt,
        previous,
        records,
        failure,
        checked_at=None if failure else checked_at,
    )
    if args.publish and (not failure or old):
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
                if not old or latest["files"] != snapshot["files"]:
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
