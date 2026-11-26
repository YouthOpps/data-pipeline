"""Read the visible requisition inventory from the official GM English search."""

from dataclasses import dataclass, field
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit


NATIVE_ID = re.compile(r'jr-[0-9]+', re.IGNORECASE)
YOUTH_TEAM = 'Students & Recent Graduates'
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
    summary = re.fullmatch(r'Showing ([0-9,]+) to ([0-9,]+) of ([0-9,]+) matching jobs',
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
        if (not path or (link.scheme and link.scheme != 'https') or
                (link.netloc and link.netloc != 'search-careers.gm.com') or
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
