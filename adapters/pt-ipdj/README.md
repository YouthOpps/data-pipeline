# IPDJ public opportunities

This standalone standard-library adapter discovers the live official
[programme directory](https://ipdj.gov.pt/programas) and
[news board](https://ipdj.gov.pt/noticias), then parses the linked public HTML.
It accepts ordinary title, text, link and date changes and discovers new entries.
There are no frozen input hashes or embedded opportunity profiles.

## Inventory and limitations

Every programme-directory card is accounted for. Own programme details are read;
delegated sites and administrative registration routes are excluded. The complete
news board delivered by the publisher is inspected, selecting cards that describe
applications, enrolment, scholarships, internships, volunteering or education.
Results and award-delivery reports are excluded. A detail page must contain a
participant/application offer before it becomes a record. The board currently
delivers a finite recent archive; this adapter does not claim all-time news
coverage or discover private search APIs. The public applications-calendar
resource returned an empty response during research and is not treated as an
opportunity inventory. The public online-session calendar is parsed by year and
dated entry, including still-published historical sessions. The PEJENE, PAFRH,
PAAF and academic-scholarship programme sections are also read directly because
they remain public but are absent from the main programme directory.

The previous 245 hard-coded records included individual PDF workcamps, separate
regional sessions and recipient tracks, as well as the online-learning calendar.
Grouped HTML notices are split into explicitly selectable regional sessions,
training modules, award audiences, programme tracks and volunteer actions.
Existing deterministic item keys are retained for matching identities. PDF/DOCX-only
camp details and attachment-only rounds remain coverage limitations; their old
facts are not carried forward as if newly verified. Other pages absent from the
named roots and current directory/news board are not discovered automatically.
Omitting a former identity does not establish cancellation or closure. This
implementation must not be described as preserving the old inventory in full.
No delegated PNED/INR, e-goi, ClubeTOP, private form or login is fetched.

The 2026-10-13 census inspected all 500 delivered news cards and 72 directory
links (two delegated), retrieved 301 distinct own detail URLs plus three roots,
and emitted 275 records. Of the details, 208 supplied offers, 92 supplied no
recognised participant offer, and one returned HTTP 404. Exclusions include
award/result reports, reports on already participating volunteer cohorts,
regulatory/information pages and health services without an opportunity offer.
Closed calls are not excluded simply for being closed. Public consultation
invitations, still-published historical training and programme overviews remain
represented. Unrecognised invitation phrasing remains a heuristic limitation.

Programmatic comparison with the prior 245 profiles retained 198 IDs after
one-to-one legacy migration and found 77 new identities. All 30 prior HTML
suboffers across 11 grouped pages and all 46 calendar identities were retained.
The 47 omissions are 44 workcamp allocations from two document-library PDFs,
the attachment-only `PNDpT-IES - bienio 2023-2025` and `Despacho Programa
Associa-te 2025` rounds, and the news item
`/-/bolsas-jovens-criadores-2025-nas-areas-da-musica-e-literatura-candidaturas-abertas`,
absent from the current delivered news board.
The unavailable `/gabinetes-de-saude-juvenil` page affected no prior identity.
None of these omissions is evidence of cancellation. Individual PDF allocations
cannot be claimed as currently verified through their parent HTML overview.

## Live facts and identity

Titles and short factual extracts remain in Portuguese. Categories are derived
from the actual title and subtitle, with `other` for unsupported classification.
Grant records distinguish institutional beneficiaries when explicitly present in
the page. Host and eligible countries are not inferred from publisher location.

Affirmative application-closing clauses containing a year are parsed each run;
project execution and course end dates do not count as application closings.
Concise funding and eligibility excerpts beyond the introduction are retained in
classification evidence, so those material changes also advance updated time.
Yearless dates remain null unless an unambiguous call edition supplies the year
for an individual grouped offer. Relative, conflicting or event-only dates remain null. Date-only
deadlines stay date-only; no time or timezone is invented. A future closing does
not guarantee available places, so status remains unknown unless closure is
established. Original details and canonical links retain provenance for terms
the parser cannot represent.

Notice IDs use the existing source-plus-canonical-URL hash. Publication preserves
an old ID when URL and title have one unambiguous prior match, or when a URL has
exactly one old and one new record even after a title edit, keeping created and
first-seen times. Changed content advances updated time; every success advances
checked/seen times. Several former participant identities cannot safely inherit
one aggregate notice ID; independently extracted tracks retain their item keys
and receive their own category, kind and classification status. Calendar identities retain the source's existing
title/year/day/month/time key; event times are not application deadlines.

## Access and collection controls

[Robots](https://ipdj.gov.pt/robots.txt), TLS verification and inherited proxy
settings remain enforced. Public discovery is confined to IPDJ's official host;
service, login and administrative registration routes are excluded. Short factual
extracts, attribution and canonical links are published, not whole articles,
images, full manuals or personal application data. The original source review's
document and delegated-site copying restrictions remain respected.

The existing publisher-family pacing state and lock remain in place: at least
six seconds between physical attempts, at most ten starts per rolling minute,
persisted completion/backoff, native startup recovery and stricter robots delays.
Missing mature state or locks, refusals, unavailable required pages and malformed
content fail closed. Timeouts/connection failures and HTTP 503 retain one bounded
retry. HTML requests negotiate gzip to reduce transfer costs; compressed and
decoded bodies are both bounded to 8 MiB. Discovered detail HTTP 404/410 responses
are reported as unavailable, not cancelled; required roots and other HTTP errors
still fail closed. Collection is bounded to 3,100 seconds and the whole process
to 3,490 seconds, with reserved publication, failure and export phases. The job
has 65 minutes for setup and pacing-state handoff. The larger collection budget
accommodates the current 301-detail frontier without relaxing request pacing.
On 2026-10-13, 149 genuine compressed responses had an observed mean completion
interval of 9.15 seconds (including development interruptions): about 46.4 minutes
for 304 retrievals, versus 51.7 minutes allowed for collection. This is an estimate,
not a proven cold-run duration; slower publishers still fail safely at the budget.
Exceeding a discovery ceiling fails rather than returning a partial collection.

## Test and publication

```sh
python -B adapters/pt-ipdj/test_adapter.py
```

The sole live test validates all retrieved records and prints one complete JSON
array on stdout; diagnostics use stderr. It neither publishes nor receives a
publication token or writes collection files. Counts are dynamic. Native local
pacing-state writes remain necessary.

The Action runs only `python -B adapters/pt-ipdj/adapter.py --publish`, retaining
manual dispatch, the schedule, own-path main pushes, repository/main guard,
source-scoped credentials and serialization. The obsolete fixed-URL diagnostic
dispatch was removed. Data and metadata are atomically source-scoped; first-run
failure creates no source pair, and later failure preserves last-good data and
success time. Artifact restore/export and the healthy current-attempt upload
handoff remain unchanged. Live tests do not prove production publication.

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

## Daily automation

The scheduled refresh runs once daily at **22:01 UTC** (`1 22 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force; this source changes only its
daily automation schedule.

## Source display name

Published metadata uses `display_name` = **IPDJ** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.



## Journal ownership maintenance (#295)


Journal reads and commits retain the genuine current read/write file descriptor
under the original flock authority. Parent, current pathname, acquired
identities and observed generations are checked before use, publisher opening
and owned export. Writes serialize in memory, then truncate/fsync and fully
write/read back that original inode. A changed object is preserved and access
stops; no stale pathname is reopened for destructive cleanup.

IO failures latch independently of existing transport flags. Diagnostic state
copies are best effort after the latch. Journal, lock, collection, artifact
writer/readback, output and parent descriptors retain acquisition identity
through cleanup. Both independent lifetime releases are attempted; a primary
error is preserved and a healthy close failure cannot return a healthy owned
export. Genuine verified incoming-state restoration is the only new-journal
initialization authority. Missing mature state is not an empty-state reset.

In-place interruption can sacrifice availability: empty or partial mature state
stops. Advisory locks and observed identity/generation checks are not filesystem
CAS or arbitrary same-user authentication. Existing schemas have no new durable
pending barrier; total persistence plus invalidation failure across restart
remains separate #273 work.

Original facts, IDs, graph, parser, dates, expiry, metadata, publication,
retry/redirect/REP/status interpretation, actual holds, schema, seeds, approval
markers, timing floors, caps, tests and workflow remain unchanged. This is
conditional IO maintenance, not pacing modernization, access approval or hold
recovery.

No complete original successful native/independent full-record chain is
established by this maintenance. Existing published counts or production success
do not replace that missing evidence, and no original raw-RAM availability or
replay is invented.

A separate current immutable PUBLISHED snapshot qualification at
data11536bfa/websiteaa265850 used consumer2fabcc52: 240 published and 240
expiry-filtered records, derived empty and last-good failure cases passed. This
checks published contracts and consumer compatibility, not current upstream
facts, access or original full-inventory completeness.

Validation is limited to bounded ordinary private
entry/restore/request/export/lifecycle and original retry/redirect/legacy cases,
plus whole-source protected business-AST and original envelope/literal
comparison. No advanced descriptor campaign, publisher/native request, actual
journal operation, ZIP download or workflow action is performed by the author.
Independent exact-revision review is required before delivery. Refs #295. Issue
#295 remains open.
