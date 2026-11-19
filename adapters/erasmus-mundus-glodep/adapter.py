"""Parse the public GLODEP consortium scholarship call."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import re

SOURCE_ID = 'erasmus-mundus-glodep'
SOURCE_URL = 'https://glodep.eu/admission/criteria-and-requirements/'
TITLE = 'GLODEP consortium scholarship applications'
TIMESTAMPS = ('created_at', 'updated_at', 'first_seen_at', 'last_seen_at', 'last_checked_at')
DATE_ONLY_ZONE = timezone(timedelta(hours=-12))
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December')
DATE_TEXT = r'(\d{1,2})(?:st|nd|rd|th)? (' + '|'.join(MONTHS) + r') (20\d{2})'
ROUND = re.compile(r'(1st|2nd) call for applications\s*[–—-]\s*' + DATE_TEXT
                   + r'\s*[–—-]\s*' + DATE_TEXT, re.IGNORECASE)
CYCLE = re.compile(r'Call for applications(?: for intake)? (20\d{2})\s*[/–-]\s*(20\d{2})',
                   re.IGNORECASE)
VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'param', 'source', 'track', 'wbr'))
HIDDEN = frozenset(('aside', 'footer', 'nav', 'noscript', 'script', 'style', 'template'))
BLOCKS = frozenset(('h1', 'h2', 'h3', 'h4', 'li', 'p'))


class SourceFormatError(ValueError):
    """The official call or its eligibility cannot be verified."""


@dataclass(frozen=True)
class Call:
    intake_start: int
    intake_end: int
    opens: date
    deadline: date
    self_paid_opens: date
    self_paid_deadline: date
    application_url: str


class AdmissionsPage(HTMLParser):
    """Collect visible admissions text and official links."""

    def __init__(self):
        super().__init__()
        self.frames = []
        self.articles = 0
        self.blocks = []
        self.canonicals = []
        self.links = []
        self.robots = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'link' and 'canonical' in attrs.get('rel', '').lower().split():
            self.canonicals.append(attrs.get('href'))
        if tag == 'meta' and attrs.get('name', '').lower() in {'robots', 'youthoppsbot'}:
            self.robots.extend(re.split(r'[\s,]+', attrs.get('content', '').lower()))
        if tag in VOID:
            return
        inherited = self.frames[-1][1] if self.frames else False
        muted = (inherited or tag in HIDDEN or 'hidden' in attrs
                 or attrs.get('aria-hidden', '').lower() == 'true'
                 or 'uk-hidden' in attrs.get('class', '').split()
                 or re.search(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)',
                              attrs.get('style', ''), re.IGNORECASE))
        if tag == 'main' and not muted:
            self.articles += 1
        within_main = not muted and any(kind == 'main' for kind, _, _ in self.frames)
        if tag == 'a' and within_main:
            self.links.append(attrs.get('href', ''))
        parts = [] if within_main and tag in BLOCKS else None
        self.frames.append((tag, muted, parts))

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.frames and not self.frames[-1][1]:
            for _, _, parts in self.frames:
                if parts is not None:
                    parts.append(data)

    def handle_endtag(self, tag):
        for position in range(len(self.frames) - 1, -1, -1):
            if self.frames[position][0] == tag:
                for kind, _, parts in self.frames[position:]:
                    if parts is not None:
                        text = ' '.join(''.join(parts).split())
                        if text:
                            self.blocks.append((kind, text))
                del self.frames[position:]
                return


def published_date(fragment):
    day, month, year = fragment
    return date(int(year), MONTHS.index(month.capitalize()) + 1, int(day))


def round_dates(blocks):
    rounds = {}
    for block in blocks:
        match = ROUND.fullmatch(block)
        if match:
            kind, *parts = match.groups()
            try:
                opening, closing = published_date(parts[:3]), published_date(parts[3:])
            except ValueError as error:
                raise SourceFormatError('Invalid date in official admissions calendar') from error
            if kind.lower() in rounds or opening > closing:
                raise SourceFormatError('Ambiguous official admissions round')
            rounds[kind.lower()] = (opening, closing)
    if set(rounds) != {'1st', '2nd'} or rounds['1st'][1] >= rounds['2nd'][0]:
        raise SourceFormatError('Funded and self-paid admissions rounds cannot be separated')
    return rounds


def one_index(rows, pattern):
    matches = [offset for offset, row in enumerate(rows) if pattern.fullmatch(row)]
    if len(matches) != 1:
        raise SourceFormatError('The admissions call or calendar changed')
    return matches[0]


def parse_call(document):
    parser = AdmissionsPage()
    parser.feed(document)
    parser.close()
    if parser.articles != 1 or parser.canonicals != [SOURCE_URL] or set(parser.robots) & {'noindex', 'none'}:
        raise SourceFormatError('Canonical or indexable official admissions page could not be verified')
    rows = [value for _, value in parser.blocks]
    if [value for tag, value in parser.blocks if tag == 'h1'] != ['Criteria and Requirements']:
        raise SourceFormatError('The official admissions page heading changed')
    call = one_index(rows, re.compile(r'Call for applications for intake 20\d{2}/20\d{2}', re.I))
    calendar = one_index(rows, re.compile(r'Admission Calendar', re.I))
    attach = one_index(rows, re.compile(r'Attachments to application', re.I))
    if not call < attach < calendar:
        raise SourceFormatError('Admissions sections changed order')
    match = CYCLE.fullmatch(rows[call])
    assert match is not None
    intake_start, intake_end = map(int, match.groups())
    if intake_end != intake_start + 2:
        raise SourceFormatError('Unsupported intake period')
    call_rows = rows[call + 1:attach]
    official_rounds = round_dates(call_rows)
    matching_cycle = one_index(rows, re.compile(r'Call for applications 20\d{2}/20\d{2}', re.I))
    if matching_cycle <= calendar or CYCLE.fullmatch(rows[matching_cycle]).groups() != match.groups():
        raise SourceFormatError('The eligibility and admissions calendars disagree')
    if round_dates(rows[matching_cycle + 1:]) != official_rounds:
        raise SourceFormatError('The first and second round dates disagree across the official page')
    funded_row = one_index(call_rows, re.compile(r'1st call for applications.*', re.I))
    paid_row = one_index(call_rows, re.compile(r'2nd call for applications.*', re.I))
    funded = ' '.join(call_rows[funded_row + 1:paid_row]).lower()
    paid = ' '.join(call_rows[paid_row + 1:]).lower()
    details = ' '.join(rows).lower()
    if ('applicants seeking emjm scholarship or glodep scholarship' not in funded
            or 'highest-ranked applicants from around the world' not in funded
            or 'self-paid students only' not in paid
            or 'potential unguaranteed emjm scholarship' not in details
            or 'admission to the program will be invited for an online interview' not in details
            or '60 ects (or equivalent) in economics' not in details
            or 'english (c1)' not in details):
        raise SourceFormatError('Official scholarship conditions require renewed review')
    application_urls = {url for url in parser.links if re.fullmatch(
        r'https://stag\.upol\.cz/portal/studium/uchazec/eprihlaska\.html\?pc_lang=en', url)}
    if len(application_urls) != 1:
        raise SourceFormatError('The published application handoff changed')
    opening, closing = official_rounds['1st']
    paid_opening, paid_closing = official_rounds['2nd']
    if not intake_start - 1 <= opening.year <= closing.year <= intake_start:
        raise SourceFormatError('Scholarship dates fall outside the admissions intake')
    return Call(intake_start, intake_end, opening, closing, paid_opening, paid_closing, application_urls.pop())


def checked_time(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError('A timezone-aware check time is required')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def normalize(call, checked_at):
    stamp = checked_time(checked_at)
    reference = checked_at.astimezone(DATE_ONLY_ZONE).date()
    status = 'unknown'
    if reference > call.deadline:
        status = 'expired'
    elif reference >= call.opens:
        status = 'open'
    return [{
        'id': f'{SOURCE_ID}-{call.intake_start}-{call.intake_end}',
        'title': f'{TITLE} {call.intake_start}–{call.intake_end}',
        'source': SOURCE_ID, 'source_url': SOURCE_URL, 'url': SOURCE_URL, 'published_at': None,
        'summary': ('Applicants worldwide may compete for a consortium scholarship in the first admissions round. '
                    'EMJM funding is described as potential. Selection is competitive and shortlisted admitted '
                    'applicants interview online. The programme requires C1 English and at least 60 ECTS or '
                    'equivalent in economics during bachelor’s study. Apply using the official admissions page.'),
        'summary_language': 'en', 'tags': ['erasmus-mundus', 'masters', 'development-policy'],
        'deadline': call.deadline.isoformat(), 'language': 'en', 'kind': 'opportunity',
        'category': 'scholarships', 'categories': ['scholarships'],
        'host_countries': [], 'eligible_countries': [], 'status': status,
        **dict.fromkeys(TIMESTAMPS, stamp),
    }]


def expire(records, checked_at):
    checked_time(checked_at)
    reference = checked_at.astimezone(DATE_ONLY_ZONE).date()
    return [row for row in records if date.fromisoformat(row['deadline']) >= reference]
