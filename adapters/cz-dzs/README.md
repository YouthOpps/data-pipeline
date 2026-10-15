# DZS European Solidarity Corps

This standalone standard-library adapter collects grants and educational
offers advertised by [Dům zahraniční spolupráce (DZS)](https://www.dzs.cz/en).
Its source entry is the [ESC grant page](https://www.dzs.cz/program/evropsky-sbor-solidarity/projekty-granty).

## Official access and reuse

The adapter uses public programme, call, participant and
[NET pages](https://www.dzs.cz/networking-activities), plus their directly
linked public DZS events and SALTO training details. Robots are checked
before access; the DZS robots shell wrapper is inert text, never executed.
Search, filtered event indexes, private APIs and application forms are not
crawled. It does not authenticate or submit applications.

Records use official titles, brief original factual summaries and explicit
publisher/discovery links. No open licence is claimed for DZS content, and
privacy/statutory notices are not treated as content licences.
[SALTO's legal notice](https://www.salto-youth.net/about/legal-notice/)
welcomes attributed educational circulation subject to individual and
third-party rights; commercial dissemination requires written permission.
The adapter does not republish photographs or full publisher descriptions.

## Dynamic inventory

Every run reads the ESC grant, programme and participant pages and NET
listing, following advertised NET pagination. Current call links are
discovered from those pages. Grant records
come from the actual application-deadline timelines inside grant tabs;
annual rounds remain one scheme identity per year. Quality Label
accreditations are excluded. Current call timelines supersede the same
annual scheme's older hub timeline.

All advertised DZS event details and delegated SALTO training links from
this frontier are fetched. A course's delegated page enriches its DZS
record rather than duplicating the course. NET entries create records
from their actual SALTO heading, deadline and location. Individual
consultation appointments and mandatory on-arrival/mid-term cohorts are
excluded. New links and changed deadlines, titles and descriptions no
longer require profile/hash changes. Missing structures, ambiguous dates,
conflicting provider dates and incomplete retrieval fail collection.

This is the published ESC programme/call/NET frontier, not every historical
DZS event, all DZS programmes or the entire SALTO catalogue. The forbidden
filtered event index is deliberately excluded. PDF-only offers and material
conditions available only in PDFs are not extracted by this HTML parser;
the adapter does not pretend to parse documents by checking their bytes.
New document-only schemes need a supported official HTML representation or
a separately implemented document parser. No historical guide amounts
are copied into current records.

## Identity, dates and summaries

Annual grant IDs retain the existing source/scheme/year keys. An
identity-only mapping preserves the previously published DZS event and
SALTO numeric training IDs. These aliases contain no titles, deadlines,
summaries or opportunity inventory; new DZS URLs and SALTO training
numbers produce deterministic IDs. The existing freshness merge preserves
creation/first-seen times and advances updated time when content changes.

Czech and English dates are parsed from deadline timelines and registration
sentences. An omitted event year is resolved only from the event's explicit
dated HTML time element. Activity dates and selection dates are not treated
as application deadlines. SALTO's explicit 24h UTC closing convention is
normalized to next-day midnight; linked DZS dates are compared with the
advertised day before normalization. Annual scheme rows aggregate all
published rounds and deterministically select the latest deadline, retaining
every round in evidence and the summary. Unzoned dates stay date-only. The current
HTML does not establish the old PDF-reviewed grant timezone precision, so
grant deadlines are reported as dates instead of copying historic clocks.
The closing day is unknown for date-only deadlines. Missing application
deadlines remain null/unknown; an explicit SALTO closed state is respected.

Official Czech/English titles and source language are retained. Summaries
combine normalized application/event dates with brief current HTML excerpts
of description, audience and funding, capped at 600 UTF-16 units. Original
Czech and English excerpts use `summary_language` `cs` and `en` respectively;
separate attributed evidence retains selected excerpts. Missing HTML body
facts are explicitly unknown, not reconstructed from PDFs. Examples include
the solidarity call's five-person team aged 18-30 and 630 EUR/month, and
Od napadu k projektu's covered accommodation, meals and travel. Conditional
SALTO participation fees remain conditional, not universal expense coverage.
SALTO locations
supply known host countries; unstated countries and passport eligibility
remain unknown rather than inferred from residence or programme affiliation.
In particular, the parsed solidarity-project HTML establishes Czech
residence; its previously guide-reviewed physical host is now left unknown.

## Test and publication

```sh
python -B adapters/cz-dzs/test_adapter.py
python -B adapters/cz-dzs/adapter.py --publish
```

The sole live test retrieves real nonempty records, validates every record,
uniqueness and provenance, and emits the complete JSON to stdout. Counts
are dynamic. Keep that stdout in memory. Tests use no publication token,
write no collected dataset and do not invoke production Actions.

The adapter retains its standalone shared-family pacing controls:
`youthopps-dzs-publisher-v1` serializes it with `cz-study-in-czechia`.
Every publisher request start, including robots, redirects, retries and
delegated details, is at least six seconds apart and limited to ten per
rolling minute. Retry-After and persistent embargoes remain enforced.
Overlapping local runs are rejected by the family lock. Production restores
the newest trusted completed family attempt's artifact, including failures
and cancellations; missing state cannot fall back to an older success.

Publication remains atomic and limited to
`datas/cz-dzs/{data,metadata}.json`. First failure creates no source folder.
Later failure preserves last-good data and success time, with failure
metadata when safe. Source-scoped conflict checks avoid overwriting newer
data. The existing matching main-push/manual/scheduled workflow keeps its
repository/main guard, restricted credential, serialized family concurrency
and timing artifact export.

The 900-second collection phase is inside the existing 1,290-second
lifecycle with bounded publication, failure reporting and export; the job
limit remains 30 minutes. Collection tests and static publication review
do not establish production publication. Maintainer production execution
is separate from local implementation.

## Expired-record cleanup

The source does not establish a reliable application timezone for every date-only deadline. Cleanup therefore uses UTC−12 as a conservative certainty bound: a calendar deadline expires only once its closing date has ended in every civil timezone. This does not invent a publisher closing clock. Offset-aware deadlines use their explicit instant.

After complete nonempty collection and validation, the refresh removes only
records with a validated deadline strictly before the successful check time.
Missing or ambiguous publisher deadlines remain normalized as null; status, age
and disappearance are never used as expiry evidence. Invalid normalized dates
fail validation and preserve last-good data. The source uses complete replacement rather than merging
old records, so expired previously published rows cannot survive a successful
refresh, including when they are still present in the publisher response.
Creation and first-seen history is retained for refreshed eligible identities.

The retained subset and success metadata are published in one source-scoped
atomic commit. A validated nonempty raw collection whose records all expire
publishes `[]` with success and a zero count; an empty upstream collection still
fails. A previously successful empty subset remains a valid last-good snapshot.
Fetch, parse, validation and publication failures preserve last-good data and
its success timestamp; only safe failure metadata may change. The sole live test
continues to emit the complete unfiltered publisher collection without publishing.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. Native
pacing, backoff and durable budget identities remain unchanged. Existing
disabled workflows remain disabled.
