"""Read the visible requisition inventory from the official GM English search."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit


NATIVE_ID = re.compile(r'jr-[0-9]+', re.IGNORECASE)
YOUTH_TEAM = 'Students & Recent Graduates'
SEARCH = 'https://search-careers.gm.com/en/jobs/'
FILTER = 'team=Students+%26+Recent+Graduates&pagesize=20'
FIRST_PAGE = SEARCH + '?search=&' + FILTER
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
        'meta', 'param', 'source', 'track', 'wbr'}


class InventoryError(ValueError):
    pass


@dataclass
class Card:
    identifier: str = ''
    href: str = ''
    team: list[str] = field(default_factory=list)
    title: list[str] = field(default_factory=list)
    team_markers: int = 0


class InventoryParser(HTMLParser):
    """Bind a visible team and canonical link to the card's own native ID."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.cards = []
        self.current = None
        self.summary = []
        self.grid = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get('class') or '').split()
        role = None
        if tag == 'p' and 'job-count' in classes:
            role = 'summary'
        elif tag == 'div' and attrs.get('id') == 'js-job-search-results':
            if self.grid:
                raise InventoryError('Duplicate results container')
            self.grid = True
            role = 'results'
        elif self.has('results') and tag == 'div' and 'card-job' in classes:
            if self.current:
                raise InventoryError('Nested job card')
            self.current = Card()
            role = 'card'
        elif self.current:
            if 'js-job' in classes:
                identifier = attrs.get('data-id', '')
                if self.current.identifier or not NATIVE_ID.fullmatch(identifier):
                    raise InventoryError('Invalid job card identity')
                self.current.identifier = identifier.upper()
            elif tag == 'li' and 'list-inline-item' in classes:
                role = 'metadata'
            elif tag == 'use' and (attrs.get('xlink:href') or attrs.get('href', '')).endswith('#briefcase'):
                self.current.team_markers += 1
                if self.current.team_markers != 1:
                    raise InventoryError('Conflicting team markers on a job card')
                for frame in reversed(self.tags):
                    if frame[1] == 'metadata':
                        frame[1] = 'team'
                        break
            elif tag == 'a' and 'stretched-link' in classes:
                if self.current.href:
                    raise InventoryError('Duplicate job card link')
                self.current.href = attrs.get('href', '')
                role = 'title'
        if tag not in VOID:
            self.tags.append([tag, role])

    def has(self, role):
        return any(frame[1] == role for frame in self.tags)

    def handle_endtag(self, tag):
        # The site uses HTML void elements and SVG. Unmatched closing tags
        # outside the search inventory do not define the result structure.
        for index in range(len(self.tags) - 1, -1, -1):
            if self.tags[index][0] == tag:
                removed = self.tags[index:]
                del self.tags[index:]
                if any(frame[1] == 'card' for frame in removed):
                    if self.current.team_markers != 1:
                        raise InventoryError('Missing job card team')
                    self.cards.append(self.current)
                    self.current = None
                return

    def handle_data(self, value):
        if self.has('summary'):
            self.summary.append(value)
        if self.current and self.has('team'):
            self.current.team.append(value)
        if self.current and self.has('title'):
            self.current.title.append(value)


def text(parts):
    return ' '.join(' '.join(parts).split())


def parse_page(body):
    parser = InventoryParser()
    parser.feed(body.decode('utf-8'))
    parser.close()
    number = r'(0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)'
    summary = re.fullmatch(rf'Showing {number} to {number} of {number} matching jobs',
                           text(parser.summary))
    if not summary or not parser.grid or parser.current:
        raise InventoryError('Incomplete search inventory')
    first, last, total = (int(value.replace(',', '')) for value in summary.groups())
    if not 1 <= first <= last <= total or len(parser.cards) != last - first + 1:
        raise InventoryError('Result count does not match the visible cards')
    rows = {}
    for card in parser.cards:
        team, title = text(card.team), text(card.title)
        link = urlsplit(card.href)
        path = re.fullmatch(r'/en/jobs/(jr-[0-9]+)/[^/]+/', link.path, re.IGNORECASE)
        official_link = card.href.startswith('/en/jobs/') or card.href.startswith(
            'https://search-careers.gm.com/en/jobs/')
        if (not official_link or not path or
                link.query or link.fragment or path[1].upper() != card.identifier or
                card.identifier in rows or not team or not title):
            raise InventoryError('Inconsistent requisition, team, title or detail link')
        rows[card.identifier] = {'title': title, 'team': team,
                                 'url': 'https://search-careers.gm.com' + link.path}
    return first, last, total, rows


def reconcile(pages):
    """Require complete contiguous, stable and nonduplicated inventory first."""
    rows = {}
    expected_total = None
    for body in pages:
        first, _, total, page = parse_page(body)
        if expected_total is None:
            expected_total = total
        if total != expected_total or first != len(rows) + 1 or rows.keys() & page.keys():
            raise InventoryError('Inventory changed or a page is missing')
        rows.update(page)
    if not rows or len(rows) != expected_total:
        raise InventoryError('Inventory is incomplete')
    return {key: row for key, row in rows.items() if row['team'] == YOUTH_TEAM}


class PaginationParser(HTMLParser):
    """Read only the pagination navigation's declared immediate successor."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nav = []
        self.next_links = []
        self.restricted = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'nav':
            self.nav.append(attrs.get('aria-label') == 'Pagination')
        if tag == 'meta' and (attrs.get('name') or '').casefold() in ('robots', 'youthoppsbot'):
            self.restricted |= restricts_follow(attrs.get('content') or '')
        if tag == 'a' and self.nav and self.nav[-1]:
            rel = (attrs.get('rel') or '').casefold().split()
            if 'page-link' in (attrs.get('class') or '').split() and 'next' in rel:
                self.next_links.append((attrs.get('href'), 'nofollow' in rel))

    def handle_endtag(self, tag):
        if tag == 'nav' and self.nav:
            self.nav.pop()


def restricts_follow(value):
    return bool({'nofollow', 'none'}.intersection(re.split(r'[,;:\s]+', value.casefold())))


def collect_inventory(fetch_page, max_pages):
    """Fetch only a complete publisher-linked inventory within the supplied budget.

    The caller handles REP, source-family pacing and the response lifecycle. It
    returns (body bytes, response header pairs) after the response has closed.
    """
    url = FIRST_PAGE
    rows = {}
    total = None
    for number in range(1, max_pages + 1):
        body, headers = fetch_page(url)
        first, last, current_total, page = parse_page(body)
        if total is None:
            total = current_total
        if (total > 20 * max_pages or current_total != total or
                first != len(rows) + 1 or last != min(number * 20, total) or rows.keys() & page.keys()):
            raise InventoryError('Catalogue changed, contains duplicates or exceeds complete collection budget')
        rows.update(page)
        nav = PaginationParser()
        nav.feed(body.decode('utf-8'))
        nav.close()
        if last == total:
            if nav.next_links or len(rows) != total:
                raise InventoryError('Final inventory range is inconsistent with navigation')
            return {key: row for key, row in rows.items() if row['team'] == YOUTH_TEAM}
        successor = f'{SEARCH}?page={number + 1}&{FILTER}'
        if (nav.restricted or any(name.casefold() == 'x-robots-tag' and restricts_follow(value)
                                  for name, value in headers) or
                nav.next_links != [(successor + '#results', False)]):
            raise InventoryError('No permitted official link to the next inventory page')
        url = successor
    raise InventoryError('Inventory exceeds complete collection budget')
