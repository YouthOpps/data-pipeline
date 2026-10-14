"""Collect reviewed DM student funding and member training."""

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

SOURCE_ID = "dk-dm-and-ma-travel-grant"
SOURCE_URL = "https://dm.dk/students/membership/travel-grant/"
WEBSITE_URL = "https://dm.dk/"
LANGUAGE = "en"
PUBLISHER_COUNTRY = "DK"
PUBLISHER_TYPE = "non-profit"
ATTRIBUTION = "DM Students — DM and MA student membership programmes"


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


MONTHS = {
    name: number
    for number, names in enumerate(
        (
            "january januar jaanuar jaanuari",
            "february februar veebruar veebruari",
            "march marts märts märtsi",
            "april aprill aprilli",
            "may maj mai",
            "june juni juuni",
            "july juli juuli",
            "august augusti",
            "september sept september septemberi septembri",
            "october oktober oktoober oktoobri oktoobrist",
            "november novemberi novembri novembrini",
            "december decemberi detsember detsembri",
        ),
        1,
    )
    for name in names.split()
}
MONTH_PATTERN = "(?:" + "|".join(MONTHS) + ")"
DATE_PATTERN = re.compile(
    r"(?<!\d)(?:(?P<day>\d{1,2})(?:st|nd|rd|th|\.)?\s*(?:of\s+)?"
    r"(?P<month>"
    + MONTH_PATTERN
    + r")|(?P<month_first>"
    + MONTH_PATTERN
    + r")\s+(?P<day_last>\d{1,2})(?:st|nd|rd|th|\.)?)"
    r"(?:\s*,?\s*(?P<year>20\d{2}))?\b|"
    r"(?<!\d)(?P<n_day>\d{1,2})\.(?P<n_month>\d{1,2})\."
    r"(?P<n_year>20\d{2})(?!\d)",
    re.I,
)


def dated_values(text):
    """Resolve years only within one explicit application statement."""
    matches = list(DATE_PATTERN.finditer(text))
    years = {
        match["year"] or match["n_year"]
        for match in matches
        if match["year"] or match["n_year"]
    }
    if not years:
        years = set(re.findall(r"\b20\d{2}\b", text))
    values = []
    for match in matches:
        year = match["year"] or match["n_year"]
        if not year and len(years) == 1:
            year = next(iter(years))
        if not year:
            continue
        month = (
            int(match["n_month"])
            if match["n_month"]
            else MONTHS[(match["month"] or match["month_first"]).lower()]
        )
        day = int(match["day"] or match["day_last"] or match["n_day"])
        try:
            date = datetime(int(year), month, day).date().isoformat()
        except ValueError:
            continue
        values.append((match.start(), match.end(), date))
    return values


def content_blocks(root):
    """Retain semantic text blocks once, including lists and table rows."""
    result = []
    block_tags = {"p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def visit(node):
        if node.tag in (
            "script",
            "style",
            "noscript",
            "form",
            "nav",
            "footer",
            "button",
        ):
            return
        if node.attrs.get("id", "").startswith(
            ("accordion--tulemused", "warning-modal")
        ):
            return
        if node.tag == "a" and node.attrs.get("id"):
            result.append(("anchor", node.attrs["id"]))
        if node.tag in block_tags:
            nested = [
                n for n in node.walk() if n is not node and n.tag in block_tags
            ]
            if not nested:
                for anchor in nodes(node, "a"):
                    if anchor.attrs.get("id"):
                        result.append(("anchor", anchor.attrs["id"]))
                text = node.text()
                if text:
                    result.append((node.tag, text))
                return
        for child in node.children:
            if isinstance(child, Node):
                visit(child)
            elif child.strip():
                result.append(("text", " ".join(child.split())))

    visit(root)
    return result


def check_access(root):
    title = " ".join(n.text() for n in nodes(root, "title"))
    if re.search(
        r"access denied|just a moment|attention required|checking your browser",
        title,
        re.I,
    ):
        raise AdapterError("Publisher access challenge", "access")
    text = root.text()
    if re.search(
        r"(?:scraping|automated (?:access|collection)|text and data mining)"
        r".{0,100}(?:prohibited|not permitted|forbidden)|"
        r"(?:prohibit|forbid|do not permit).{0,100}(?:scraping|automated access)",
        text,
        re.I,
    ):
        raise AdapterError(
            "Publisher restricts automated collection", "access"
        )


def fact_evidence(sections):
    """Keep factual clauses and a full material-content change signature."""
    evidence = [ATTRIBUTION]
    for url, blocks in sections:
        material = "\n".join(text for _, text in blocks)
        evidence.append("Source: " + url)
        evidence.append(
            "HTML content SHA-256: "
            + hashlib.sha256(material.encode()).hexdigest()
        )
        heading = ""
        for tag, text in blocks:
            if tag.startswith("h"):
                heading = text
                continue
            text = re.sub(
                r"[\w.+-]+@[\w.-]+|\[email protected\]",
                "[publisher contact]",
                text,
            )
            if re.search(
                r"eligib|applican|appl(?:y|ication)|deadline|scholarship|grant|"
                r"funding|EUR|DKK|euros?|must|required|exclud|citizen|residen|"
                r"fee|tuition|accommod|reimburse|stipend|taotl|tähtaeg|euro|"
                r"õpp|koda(?:nik|kond)|elamis|hüvit|ansøg|legat|medlem|"
                r"kroner|kr\.|gratis|kurs|mentor|skal|støtte|udgift|kvitter|bilag|CVR|afregn|udlæg|"
                r"book|Teams|Microsoft|Google|Adobe|Office|Windows|LaTeX|Stata|Excel",
                text,
                re.I,
            ):
                evidence.append((heading + ": " if heading else "") + text)
    return list(dict.fromkeys(evidence))


def concise_summary(title, category, deadline, evidence):
    """Build a short factual index; all extracted conditions stay in evidence."""
    summary = title + ". " + category.capitalize() + " programme. "
    summary += (
        ("Application deadline: " + deadline + ". ")
        if deadline
        else "No unambiguous dated application deadline extracted. "
    )
    amounts = []
    for text in (
        clause
        for item in evidence
        for clause in re.split(r"(?<=[.!?])\s+(?=[A-ZÆØÅ])|;", item)
        if re.search(
            r"may apply for funding|annual grant fund|is distributed|"
            r"\bcosts\b|kan søge om støtte|samlede årlige legat|"
            r"fordeles halvårligt|\bkoster\b",
            clause,
            re.I,
        )
    ):
        for match in re.finditer(
            r"(?:EUR|DKK)\s*[\d][\d ,.]*(?:/\w+)?|\d[\d ,.]*(?:euros?|EUR|DKK|kroner|kr\.)",
            text,
            re.I,
        ):
            value = match[0].strip()
            if value not in amounts:
                amounts.append(value)
    if amounts:
        summary += (
            "Published amounts (rates, budgets or costs; see conditions): "
            + ", ".join(amounts)
            + ". "
        )
    summary += "Eligibility, benefits and application conditions are linked to the publisher in the supporting evidence."
    if len(summary.encode("utf-16-le")) // 2 > 600:
        summary = (
            title
            + ". "
            + (
                "Application deadline: " + deadline + ". "
                if deadline
                else "Application deadline not established. "
            )
            + "See publisher-linked evidence for eligibility, benefit rates, budgets and application conditions."
        )
    return summary


ENTRY_URLS = (
    "https://dm.dk/students/",
    "https://dm.dk/studerende/",
)
PROGRAMME_WORDS = re.compile(
    r"grant|fund|legat|sponsor|gratis.*kurs|free.*class|online.*cours|mentor",
    re.I,
)
LEGACY_ROUTES = {
    "/students/membership/travel-grant/": "travel-grant",
    "/studerende/medlemskab/legater-og-sponsorater/rejselegat/": "travel-grant",
    "/students/membership/activity-fund/": "activity-fund",
    "/studerende/medlemskab/legater-og-sponsorater/sponsoratpulje/": "activity-fund",
    "/students/membership/membership-benefits/come-to-free-classes-and-events/": "free-member-classes",
    "/studerende/medlemskab/medlemsfordele/gratis-kurser-og-arrangementer/": "free-member-classes",
    "/studerende/medlemskab/medlemsfordele/gratis-online-kurser/": "free-online-courses",
    "/studerende/medlemskab/medlemsfordele/faglige-medlemsfordele/mentorordning-for-studerende-ved-sling-uling-og-naku/": "sling-uling-naku-mentoring",
}


def own_url(value, base):
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(base, value))
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port
        or (parsed.hostname or "").removeprefix("www.") != "dm.dk"
    ):
        return None
    if re.search(r"\.(?:pdf|docx?|xlsx?|jpg|png|zip)$", parsed.path, re.I):
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if any(key not in ("page", "p") for key in query):
        return None
    return urllib.parse.urlunsplit(
        ("https", "dm.dk", parsed.path, parsed.query, "")
    )


def discovery_links(root, url):
    """Follow both languages' member hubs and their programme detail links."""
    for link in nodes(root, "a"):
        target = own_url(link.attrs.get("href", ""), url)
        if not target:
            continue
        path = urllib.parse.urlsplit(target).path
        if path == "/students/membership/":
            # This is the membership signup redirect, not a programme hub.
            continue
        membership = path.startswith(
            ("/students/membership/", "/studerende/medlemskab/")
        )
        excluded = re.search(
            r"insurance|forsikring|membership-dues|kontingent|kontante|financial|bank|meld-dig|join|mentorliste|studiekunde|studiekonto|smil|mofibo|studierabat",
            path,
        )
        if membership and excluded:
            continue
        policy = re.search(
            r"cookie|privatliv|privacy|handelsvilkaar|konkurrencebetingelser",
            path,
        )
        if policy or (membership and not excluded):
            yield target
        elif path == "/om-dm/refusion/":
            yield target
        elif (
            path.startswith(("/students/", "/studerende/"))
            and not re.search(r"/nyheder/|/login|/kontakt|/contact", path)
            and PROGRAMME_WORDS.search(link.text())
            and "medlemskab" in path
        ):
            yield target


def read_pages():
    pending = list(ENTRY_URLS)
    pages = {}
    while pending:
        url = pending.pop(0)
        if url in pages:
            continue
        root = page(url)
        check_access(root)
        pages[url] = root
        for target in discovery_links(root, url):
            if target not in pages and target not in pending:
                pending.append(target)
        if len(pages) + len(pending) > 150:
            raise AdapterError(
                "DM discovery exceeds reviewed student scope", "parse"
            )
    return pages


def programme_blocks(root):
    main = one(nodes(root, "main"), "publisher programme content")
    blocks = content_blocks(main)
    start = next((i for i, (tag, _) in enumerate(blocks) if tag == "h1"), None)
    if start is None:
        raise AdapterError("Missing DM programme heading", "parse")
    result = []
    skip = False
    for tag, text in blocks[start:]:
        if tag in ("h1", "h2", "h3"):
            skip = bool(
                re.search(
                    r"^(?:Contact|Kontakt|Meld dig ind|Bliv medlem|Sarah |På feltarbejde|Mød Sanne)",
                    text,
                )
            )
        if not skip and not re.search(r"^Del artikel:|^Membership$", text):
            result.append((tag, text))
    return result


def application_windows(blocks):
    result = []
    clauses = [
        clause
        for _, text in blocks
        for clause in re.split(r"(?<=[.!?])\s+(?=[A-ZÆØÅ])|;", text)
    ]
    for text in clauses:
        closing = re.search(
            r"application deadline|ansøgningsfrist", text, re.I
        )
        period = re.search(
            r"application (?:period|round)|ansøgnings(?:runde|periode)",
            text,
            re.I,
        )
        label = closing or period
        if not label:
            continue
        statement = text[label.end() :]
        matches = list(DATE_PATTERN.finditer(statement))
        if not matches:
            continue
        prefix = statement[: matches[0].start()].strip(" :.,")
        allowed = (
            r"(?:(?:is|on|no later than|senest|er|den|by)\s*)*"
            if closing
            else r"(?:(?:is|will|be|open|from|er|til|forårssemestret|"
            r"efterårssemestret|fra|den)\s*)*"
        )
        if not re.fullmatch(allowed, prefix, re.I):
            continue
        if closing:
            dates = dated_values(matches[0][0])
            if not dates:
                continue
            opening, deadline = None, dates[0][2]
        else:
            if len(matches) != 2:
                continue
            separator = statement[matches[0].end() : matches[1].start()]
            if not re.fullmatch(
                r"\s*(?:[-–—]|to|until|til)\s*", separator, re.I
            ):
                continue
            dates = dated_values(
                statement[matches[0].start() : matches[1].end()]
            )
            if len(dates) != 2:
                continue
            opening, deadline = dates[0][2], dates[1][2]
        if opening and opening > deadline:
            # A cross-year range with an unstated opening year is ambiguous.
            opening = None
        item = (opening, deadline)
        if item not in result:
            result.append(item)
    return result


def parse_inventory(pages):
    groups = {}
    for url, root in pages.items():
        path = urllib.parse.urlsplit(url).path
        if not path.startswith(
            ("/students/membership/", "/studerende/medlemskab/")
        ):
            continue
        blocks = programme_blocks(root)
        title = next(text for tag, text in blocks if tag == "h1")
        # Umbrellas discover detail programmes but are not separate awards.
        if path.endswith(
            (
                "/membership/",
                "/medlemskab/",
                "/membership-benefits/",
                "/medlemsfordele/",
                "/legater-og-sponsorater/",
                "/professional-member-benefits/",
                "/faglige-medlemsfordele/",
            )
        ):
            continue
        if not PROGRAMME_WORDS.search(title):
            continue
        identity = LEGACY_ROUTES.get(path, path)
        groups.setdefault(identity, []).append((url, blocks))
    records = []
    for identity, sections in groups.items():
        sections.sort(key=lambda item: ("/students/" not in item[0], item[0]))
        url, blocks = sections[0]
        if identity == "activity-fund":
            for guide_url, guide in pages.items():
                if urllib.parse.urlsplit(guide_url).path == "/om-dm/refusion/":
                    guidance = programme_blocks(guide)
                    selected = []
                    relevant = False
                    for tag, value in guidance:
                        if tag.startswith("h"):
                            relevant = bool(
                                re.search(
                                    r"Afregningsblanket for udlæg|For studerende",
                                    value,
                                )
                            )
                        if relevant:
                            selected.append((tag, value))
                    sections.append((guide_url, selected))
        title = next(text for tag, text in blocks if tag == "h1")
        text = " ".join(
            value for _, content in sections for _, value in content
        )
        category = (
            "scholarships"
            if re.search(r"travel grant|rejselegat", title, re.I)
            else (
                "grants"
                if re.search(r"fund|sponsor|legat|grant", title, re.I)
                else "training"
            )
        )
        windows = sorted(
            dict.fromkeys(
                window
                for _, content in sections
                for window in application_windows(content)
            ),
            key=lambda window: (window[1], window[0] or ""),
        )
        evidence = fact_evidence(sections)
        opening, deadline = windows[0] if len(windows) == 1 else (None, None)
        record = make_record(
            title, url, [category], method="publisher-html", evidence=evidence
        )
        record["id"] = hashlib.sha256(
            (SOURCE_ID + "|" + identity).encode()
        ).hexdigest()[:24]
        record["deadline"] = deadline
        record["application_windows"] = [
            {"opening": start, "deadline": end} for start, end in windows
        ]
        record["language"] = "en" if "/students/" in url else "da"
        record["kind"] = (
            "institutional-grant"
            if category == "grants"
            else (
                "programme-overview"
                if category == "training"
                else "opportunity"
            )
        )
        if category == "training" and all(
            city in text
            for city in ("Aalborg", "Aarhus", "Odense", "Copenhagen")
        ):
            record["host_countries"] = ["DK"]
        if deadline and utc_now()[:10] > deadline:
            record["status"] = "expired"
        elif deadline and opening and opening <= utc_now()[:10] < deadline:
            record["status"] = "open"
        record["summary"] = concise_summary(
            title, category, deadline, evidence
        )
        records.append(record)
    validate_records(records)
    return records


def collect():
    return parse_inventory(read_pages())


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


def validate_records(records, *, allow_empty=False):
    """Validate all real records against the source and consumer contracts."""
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
                f"Collected {len(records)} validated DM student "
                "funding and member training programmes"
            )
        ),
        "error": safe_error(error) if error else None,
    }
    if ATTRIBUTION:
        result["attribution"] = ATTRIBUTION
    if error:
        result["failure_stage"] = getattr(error, "stage", "parse")
    return result


def remove_expired_records(records, checked_at):
    """Filter validated deadlines after retrieval, before atomic publication.

    Unknown publisher zones use UTC-12 only as a global certainty bound:
    a date remains eligible until its closing day has ended everywhere.
    No age, status, missing response or unstated local clock implies expiry.
    """
    checked = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    if checked.tzinfo is None:
        raise AdapterError(
            "Cleanup requires an aware check timestamp", "validate"
        )
    latest_day = checked.astimezone(timezone(timedelta(hours=-12))).date()
    retained = []
    for record in records:
        deadline = record.get("deadline")
        expired = False
        if isinstance(deadline, str):
            try:
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", deadline):
                    closing_day = datetime.strptime(deadline, "%Y-%m-%d").date()
                    expired = closing_day < latest_day
                else:
                    cutoff = datetime.fromisoformat(
                        deadline.replace("Z", "+00:00")
                    )
                    expired = cutoff.tzinfo is not None and cutoff < checked
            except ValueError:
                # Unsupported or ambiguous deadlines never authorize removal.
                pass
        if not expired:
            retained.append(record)
    return retained


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
        validate_records(old, allow_empty=True)
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
        records = remove_expired_records(records, utc_now())
        validate_records(records, allow_empty=True)
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
