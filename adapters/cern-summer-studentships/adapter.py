"""Parse the official CERN Summer Studentship inventory and opportunity details."""

from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


SOURCE = "cern-summer-studentships"
INVENTORY = "https://careers.cern/programmes/summer-studentship/"
HOST_ZONE = ZoneInfo("Europe/Zurich")
PROGRAMMES = {
    "SUMM": "Summer Student Programme",
    "SUM-OPENLAB": "OPENLAB Summer Student Programme",
}
REFERENCE = re.compile(r"(SUMM|SUM-OPENLAB)-(20[0-9]{2})")
TITLE = re.compile(
    r"CERN (OPENLAB Summer Student Programme|Summer Student Programme) (20[0-9]{2})"
    r"(?: \(Member and Non-Member State\))?",
    re.IGNORECASE,
)
DEADLINE = re.compile(
    r"\bbefore\s+([0-9]{2}/[0-9]{2}/[0-9]{4})\s+at\s+([0-9]{2}:[0-9]{2})\s*"
    r"\(Geneva Time\)",
    re.IGNORECASE,
)
START = re.compile(r"\bIdeal start date:\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", re.IGNORECASE)
ALLOWANCE = re.compile(
    r"\b([0-9]+)\s+CHF per calendar day net subsistence allowance\b", re.IGNORECASE
)
HIDDEN_TAGS = frozenset(("script", "style", "template", "noscript"))
VOID_TAGS = frozenset(("area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"))


@dataclass(frozen=True)
class Opportunity:
    title: str
    reference: str
    url: str

    @property
    def programme(self):
        return self.reference.rsplit("-", 1)[0]

    @property
    def year(self):
        return int(self.reference.rsplit("-", 1)[1])


def finish(chunks):
    return " ".join("".join(chunks).split())


def official_detail_url(value):
    if not isinstance(value, str):
        return False
    url = urlsplit(value)
    return (url.scheme == "https" and url.netloc == "careers.cern"
            and re.fullmatch(r"/jobs/cern-[a-z0-9-]+/", url.path) is not None
            and not url.query and not url.fragment and value == url.geturl())


class Page(HTMLParser):
    """Read visible main content and inventory cards without resolving links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.main = False
        self.main_seen = False
        self.main_closed = False
        self.main_text = []
        self.heading = None
        self.titles = []
        self.card = None
        self.cards = []

    def handle_starttag(self, tag, pairs):
        attributes = dict(pairs)
        hidden = (tag in HIDDEN_TAGS or "hidden" in attributes
                  or (attributes.get("aria-hidden") or "").lower() == "true")
        hidden = hidden or bool(self.stack and self.stack[-1][1])
        if tag not in VOID_TAGS:
            self.stack.append((tag, hidden))
        if hidden:
            return
        if tag == "main":
            if self.main_seen:
                raise ValueError("Multiple visible main elements")
            self.main_seen = self.main = True
        if not self.main:
            return
        self.main_text.append(" ")
        if tag == "h1":
            if self.heading is not None:
                raise ValueError("Nested main heading")
            self.heading = []
        classes = (attributes.get("class") or "").split()
        if tag == "a" and "job-offer-teaser" in classes:
            if self.card is not None:
                raise ValueError("Nested job card")
            self.card = {"url": attributes.get("href"), "title": [], "reference": [],
                         "regions": {}, "seen": set()}
        if self.card is not None:
            regions = self.card["regions"]
            field = {"h3": "title"}.get(tag)
            if tag == "div" and "ref" in classes:
                field = "reference"
            if field:
                if field in self.card["seen"]:
                    raise ValueError("Duplicate job card field")
                self.card["seen"].add(field)
                regions[field] = len(self.stack)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            name, hidden = self.stack[index]
            if name != tag:
                continue
            del self.stack[index:]
            if self.card is not None:
                regions = self.card["regions"]
                for field in tuple(regions):
                    if regions[field] > index:
                        del regions[field]
            if not hidden and self.main:
                self.main_text.append(" ")
                if tag == "h1" and self.heading is not None:
                    self.titles.append(finish(self.heading))
                    self.heading = None
                if self.card is not None and tag == "a":
                    self.cards.append((self.card["url"], finish(self.card["title"]),
                                       finish(self.card["reference"])))
                    self.card = None
                if tag == "main":
                    self.main = False
                    self.main_closed = True
            break

    def handle_startendtag(self, tag, pairs):
        self.handle_starttag(tag, pairs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if not self.main or (self.stack and self.stack[-1][1]):
            return
        self.main_text.append(data)
        if self.heading is not None:
            self.heading.append(data)
        if self.card is not None:
            for field, depth in self.card["regions"].items():
                if depth <= len(self.stack):
                    self.card[field].append(data)


def read_page(body):
    page = Page()
    page.feed(body.decode("utf-8", "strict"))
    page.close()
    if not page.main_closed or page.card is not None or page.heading is not None:
        raise ValueError("Incomplete CERN main content")
    return page


def parse_inventory(body):
    """Use the official main cards and their native reference. Slugs may be stale."""
    page = read_page(body)
    if page.titles != ["Summer Studentship"]:
        raise ValueError("Unexpected CERN programme page")
    opportunities = {}
    routes = set()
    for url, title, reference in page.cards:
        match = REFERENCE.fullmatch(reference)
        if not match:
            continue
        programme, year = match.groups()
        named = TITLE.fullmatch(title)
        if (named is None or named[1].casefold() != PROGRAMMES[programme].casefold()
                or named[2] != year or not official_detail_url(url)):
            raise ValueError("Conflicting official CERN opportunity card")
        candidate = Opportunity(title, reference, url)
        previous = opportunities.get(reference)
        if previous is not None:
            if candidate != previous:
                raise ValueError("CERN reference has conflicting cards")
            continue
        if url in routes:
            raise ValueError("CERN detail route has multiple references")
        routes.add(url)
        opportunities[reference] = candidate
    results = tuple(sorted(opportunities.values(), key=lambda item: item.reference))
    if (len(results) != len(PROGRAMMES)
            or {item.programme for item in results} != PROGRAMMES.keys()
            or len({item.year for item in results}) != 1):
        raise ValueError("Incomplete or mixed-cycle CERN studentship inventory")
    return results


def single_match(pattern, text, field):
    matches = set(pattern.findall(text))
    if len(matches) != 1:
        raise ValueError("Missing or conflicting CERN " + field)
    return matches.pop()


def parse_detail(body, candidate, checked_at):
    """Validate one complete application page and return facts for one candidate."""
    if checked_at.utcoffset() is None:
        raise ValueError("A timezone-aware check time is required")
    page = read_page(body)
    text = " ".join("".join(page.main_text).split())
    if page.titles != [candidate.title]:
        raise ValueError("CERN detail title differs from the scoped inventory")
    if not re.search(rf"(?<![\w-]){re.escape(candidate.reference)}(?![\w-])", text):
        raise ValueError("CERN detail reference differs from the scoped inventory")
    if not re.search(r"\bapply now\b", text, flags=re.IGNORECASE):
        raise ValueError("Current CERN application entry is missing")
    due_date, due_time = single_match(DEADLINE, text, "Geneva deadline")
    deadline = datetime.strptime(f"{due_date} {due_time}", "%d/%m/%Y %H:%M").replace(tzinfo=HOST_ZONE)
    start = datetime.strptime(single_match(START, text, "start date"), "%d/%m/%Y").date()
    if start.year != candidate.year or deadline.date() >= start:
        raise ValueError("CERN detail dates differ from the stated cycle")
    allowance = int(single_match(ALLOWANCE, text, "daily allowance"))
    eligibility = text.casefold().replace("’", "'")
    degree_evidence = (
        re.search(r"\bbachelor(?:'s)? or master(?:'s)? student\b", eligibility)
        and re.search(r"\b(6|six) semesters\b", eligibility)
        and re.search(r"\bremain (?:enrolled|registered)\b", eligibility)
    )
    programme = ("research and technical projects" if candidate.programme == "SUMM"
                 else "computing research projects")
    summary = (f"CERN summer studentship in Geneva for {programme}. "
               f"The official call lists a net allowance of CHF {allowance} per calendar day.")
    if degree_evidence:
        summary += " Applicants need bachelor's or master's studies, six semesters and continued enrolment."
    if candidate.programme == "SUMM" and "(member and non-member state)" in candidate.title.casefold():
        summary += " CERN lists applications from both member and non-member states."
    stamp = checked_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    return {
        "id": f"{SOURCE}-{candidate.reference.lower()}",
        "title": candidate.title,
        "source": SOURCE,
        "source_url": INVENTORY,
        "url": candidate.url,
        "summary": summary,
        "category": "internship",
        "categories": ["internship"],
        "kind": "opportunity",
        "location": "Geneva, Switzerland",
        "host_countries": ["CH"],
        "eligible_countries": [],
        "language": "en",
        "summary_language": "en",
        "status": "open" if checked_at < deadline else "expired",
        "deadline": deadline.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "start_date": start.isoformat(),
        "created_at": stamp,
        "updated_at": stamp,
        "first_seen_at": stamp,
        "last_seen_at": stamp,
        "last_checked_at": stamp,
        "classification": {"method": "publisher-detail", "evidence": [INVENTORY, candidate.url]},
    }
