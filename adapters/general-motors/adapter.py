"""Read the visible requisition inventory from the official GM English search."""

from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
import json
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


class DetailError(ValueError):
    pass


JOB_PATH = re.compile(r'/en/jobs/(jr-[0-9]+)/[a-z0-9-]+/')
ENROLLMENT = re.compile(r'\b(currently (?:pursuing|enrolled)|must be (?:pursuing|enrolled))\b', re.I)
WORK_RIGHTS = re.compile(r'\b(?:visa sponsorship|immigration sponsorship|work authori[sz]ation|'
                         r'authori[sz]ed to work|sponsor(?:ship)? (?:for a )?(?:work|employment) visa)\b', re.I)


def normalized(parts):
    return ' '.join(''.join(parts).split())


class DetailParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.canonical = []
        self.scripts = []
        self.detail_ids = []
        self.titles = []
        self.teams = []
        self.posting_dates = []
        self.meta = []
        self.statements = []
        self.apply = []

    def active(self, role):
        return any(role == frame[1] for frame in self.stack)

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        classes = (attrs.get('class') or '').split()
        role, parts = None, None
        if tag == 'link' and 'canonical' in (attrs.get('rel') or '').split():
            self.canonical.append(attrs.get('href'))
        if tag == 'div' and attrs.get('id') == 'js-job-detail':
            self.detail_ids.append((attrs.get('data-id') or '').upper())
            role = 'detail'
        elif tag == 'section' and 'hero-job' in classes:
            role = 'hero'
        elif self.active('hero') and tag == 'h1' and 'hero-heading' in classes:
            role, parts = 'title', []
            self.titles.append(parts)
        elif self.active('hero') and tag == 'p' and 'hero-sub-heading' in classes:
            role, parts = 'team', []
            self.teams.append(parts)
        elif self.active('hero') and tag == 'ul' and 'job-meta' in classes:
            role = 'meta'
        elif self.active('meta') and tag == 'li':
            role, parts = 'meta_item', []
            self.meta.append(parts)
        elif self.active('meta_item') and tag == 'time':
            self.posting_dates.append(attrs.get('datetime'))
        elif self.active('detail') and tag == 'article' and 'cms-content' in classes:
            role = 'article'
        elif self.active('article') and tag in ('p', 'li'):
            role, parts = 'statement', []
            self.statements.append(parts)
        elif self.active('detail') and tag == 'a' and attrs.get('id') == 'js-apply-external':
            role, parts = 'apply', []
            self.apply.append((attrs.get('href'), parts))
        elif tag == 'script' and attrs.get('type') == 'application/ld+json':
            role, parts = 'schema', []
            self.scripts.append(parts)
        if tag == 'br':
            self.handle_data(' ')
        if tag not in VOID:
            self.stack.append((tag, role, parts))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                return

    def handle_data(self, value):
        for tag, role, parts in reversed(self.stack):
            if tag in ('style', 'script') and role != 'schema':
                return
            if parts is not None:
                parts.append(value)
                return


def job_schemas(value):
    if isinstance(value, list):
        for member in value:
            yield from job_schemas(member)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if types == 'JobPosting' or (isinstance(types, list) and 'JobPosting' in types):
            yield value
        if '@graph' in value:
            yield from job_schemas(value['@graph'])


def timestamp(value):
    if not isinstance(value, str):
        raise DetailError('Missing posting timestamp')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        date.fromisoformat(value)
    else:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise DetailError('Ambiguous local timestamp')
    return value


def parse_detail(body, url, identifier, title):
    """Return verified facts only. Missing eligibility and deadlines stay unknown."""
    ref = urlsplit(url)
    path = JOB_PATH.fullmatch(ref.path)
    if (not NATIVE_ID.fullmatch(identifier) or identifier != identifier.upper() or not path or path[1].upper() != identifier
            or ref.scheme != 'https' or ref.netloc != 'search-careers.gm.com'
            or ref.query or ref.fragment):
        raise DetailError('Unexpected source reference')
    page = DetailParser()
    page.feed(body.decode('utf-8'))
    page.close()
    schemas = [job for chunk in page.scripts for job in job_schemas(json.loads(''.join(chunk)))]
    if (page.canonical != [url] or page.detail_ids != [identifier] or len(schemas) != 1
            or [normalized(t) for t in page.titles] != [title]
            or [normalized(t) for t in page.teams] != [YOUTH_TEAM]):
        raise DetailError('Visible identity or source team does not match inventory')
    job = schemas[0]
    if (job.get('identifier') != identifier or job.get('title') != title or job.get('url') != url
            or job.get('mainEntityOfPage') != url or job.get('hiringOrganization', {}).get('name') != 'General Motors'):
        raise DetailError('Structured posting identity mismatch')
    meta = [normalized(item) for item in page.meta]
    if meta.count('Job Requisition ' + identifier) != 1:
        raise DetailError('Visible requisition mismatch')
    posted = timestamp(job.get('datePosted'))
    if page.posting_dates != [posted[:10]]:
        raise DetailError('Visible posted date disagrees with structured posting')
    if len(page.apply) != 1 or normalized(page.apply[0][1]) != 'Apply Now':
        raise DetailError('Current application link is unverified')
    apply_url = page.apply[0][0] or ''
    apply_ref = urlsplit(apply_url)
    if (apply_ref.scheme != 'https' or apply_ref.netloc != 'generalmotors.wd5.myworkdayjobs.com'
            or not re.fullmatch(r'/Careers_GM/job/[^/?#]+/[^/?#]+_' + identifier + '/apply', apply_ref.path)
            or apply_ref.query or apply_ref.fragment):
        raise DetailError('Application link does not identify this official requisition')
    facts = [normalized(item) for item in page.statements]
    deadline = timestamp(job['validThrough']) if job.get('validThrough') else None
    return {'identifier': identifier, 'title': title, 'url': url, 'apply_url': apply_url,
            'posted': posted, 'deadline': deadline,
            'enrollment_evidence': [line for line in facts if ENROLLMENT.search(line)],
            'work_authorization_evidence': [line for line in facts if WORK_RIGHTS.search(line)]}
