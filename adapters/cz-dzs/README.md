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

Date-only deadlines remain eligible throughout their UTC closing calendar
day and expire at the next UTC midnight. This shared calendar rule does not
infer a publisher closing clock or timezone. Offset-aware deadlines retain
their explicit instant and are removed only afterwards. The original DZS
native passed before this pure cleanup correction; all actual retained inputs
are replayed offline without another publisher collection.

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
access; restrictive rate and delay directives remain effective. The family identity remains stable; the stronger pacing and continuity
protocol is documented below. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **20:59 UTC** (`59 20 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

The 30-second durable inventory completion guard covers all four programme/call/NET seed hubs and every discovered call/NET hub.
It applies to each physical retry and redirect, while literal robots and
programme/event/delegated detail exchanges keep the separate 12.2-second floor.
Stricter publisher policy, retained embargoes and family history take precedence.
No source inventory, parser, ID or publication scope is truncated to fit timing.

## Workflow concurrency

`youthopps-dzs-publisher-v1` serializes runs of this source and
`cz-study-in-czechia`. Up to 100 pending runs are retained (`queue: max`);
active runs are not cancelled (`cancel-in-progress: false`). Unrelated publisher
families may collect and publish in parallel. Existing source-only atomic
publication rebases on unrelated repository updates, uses up to three
publication attempts, and never force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **DZS** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Shared family pacing and continuity (#289)

Both this producer and its sibling use the same `youthopps-dzs-publisher-v1`
namespace, lifecycle lock, modern state schema and inert artifact contract.
They must migrate together. Every physical publisher request, including robots,
redirects, delegated requests and the existing one-time HTTP 503 retry, is at
least 12.2 seconds after the preceding response completes and at most five
starts occur in any rolling minute. Every physical inventory exchange also
waits at least 30 seconds after the previous inventory exchange completes.
Details retain the physical floor; inventory pacing does not globally slow
all later details. Applicable current and legacy REP groups, stricter delays
and request rates remain protective constraints. Applicable finite decimal
timing is retained as a physical floor. Unsupported applicable numeric timing
leaves assessment pending and refuses traffic; malformed directives for
unrelated agents do not impose a new restriction.

HTTP 429 persists adaptive cooldown and doubles both CURRENT physical and
inventory gaps before reading the response body. Invalid Retry-After alone
creates no refusal; a valid longer value cannot shorten existing constraints.
A fully assessed ordinary rate warning stops the job with a timed embargo.
Actual content 401/403/451, primary content challenges and applicable REP
Disallow retain a separate protective hold. Literal robots-only 4xx except 429
mean unavailable REP; TLS, server and rate-limit failures still stop access.
There is no identity switch, immediate 429 retry or automatic hold clearing.
The existing single fully assessed 503 retry waits at least 30 seconds, retaining
stronger timing; no generic connection retry is added.

A durable `in_flight` barrier covers each physical request through headers,
body, policy assessment and successful completion persistence. Cached REP
application has its own zero-wire transaction. An unassessed response,
failed restrictive/completion write or unsettled prior transaction refuses
same-process reentry and a fresh process restoring an older owned state.
Owner/mode/link checks, nonblocking file opens and pinned parent-relative
operations protect the current journal inode. Normal reads and writes use the
retained locked descriptor; a replacement cannot inherit stale lock authority
or have stronger constraints overwritten. Failed export invalidates only
safely owned output before fallible readiness signaling and removal.

Both LOCAL and hosted collection read complete histories of BOTH workflows,
rank actual exact-attempt job completions, include prior rerun attempts and
require the exact newest owned artifact. Modern envelopes bind repository,
source, run, attempt, head and implementation/state digests. Missing, unsafe,
expired or unsettled modern state never falls back to an older success.
LOCAL collection additionally requires both exact workflows manually paused
before state initialization and again after startup quiet, immediately before
the first publisher request. Read-only GitHub access uses the configured
connection; hosted jobs require their supplied workflow credential.

The first migration is a separately approved exact legacy handoff, not a
new empty allowance or generic old-schema conversion. The inspected local
namespace was genuinely absent, which does not establish zero historical
traffic. The root-verified latest legacy artifact belongs to DZS run
38823222698/attempt 1. Known state/timing constraints and all 20 actual job
completion upper bounds are retained with at least 60 seconds startup quiet.
Rate counters begin only since migration; unknown historic 429 history remains
explicit. `legacy_access_history_unknown` is strictly true, with immutable
provenance for the route-unidentified 403 in CZStudy run 37962105063. A later
successful run or legacy `blocked: false` cannot erase that uncertainty.
The later run 38245510407 specifically failed on the now-excluded historical
`http://static.daad.de/robots.txt` dependency; it does not identify the first
403's route. Any guarded reevaluation of current routes requires a separate
explicit maintainer boundary approval, never an assertion of inherited access
permission. Both LOCAL markers are separately approved and bound; both
hosted markers now bind the reviewed full-family MAX normalization.

Original live tests remain sole complete nonpublishing tests. Private failure,
interop, graph and clock controls are not publisher collection evidence.
Implementation validation does not dispatch production Actions. Both sources'
complete original tests must be separately released and serialized under the
family lock; full bodies, headers, stdout and state-save evidence stay in RAM.

The initial production cutover is staged: keep Study in Czechia paused while
DZS creates the first verified modern owned artifact, then enable the companion
only after the family is idle and that newest state is verified. Starting both
from the finite legacy bridge would correctly fail its history guard. LOCAL
validation is also serialized DZS then Study; the companion retains the same
actual journal and full known MAX under a fresh explicit release, never a reset.

Journal and collection-lock cleanup retain explicit ownership of their original
descriptors. A reused foreign descriptor is never closed or truncated, and
failed journal cleanup never opens a newer named replacement. Close failures
are sticky access failures, invalidate safe owned artifact output, and preserve
an existing primary exception. These conditional IO repairs use private fault
controls and retained-input replay; they do not constitute a new live test.


### Reviewed complete-family production handoff

The maintainer approved both hosted literals in
[the complete-family production boundary](https://github.com/YouthOpps/data-pipeline/issues/289#issuecomment-6208060152).
The full shared history retains 1,926 typed timing observations, including the
previous DZS 130 and all 449 Study reservations, opener invocations, physical
starts and response completions. These observations are not request counts.
The explicit normalization retains the 20 observations within the documented
60-second window and preserves all other MAX constraints, unknown access and
429 history, and complete external provenance for the 1,906 aged observations.
Historical pending states settle only through their matching successful durable
response or policy chains. This approval does not clear any future hold or
permit fallback from a missing newest owned state.

The genuine Study original test at source `2440aed8` passed with all three
catalogue scopes: 271 programmes, 258 normalized records, 174 current records
and 84 actually expired records. All 449 real responses and headers, 1,351
successful saves and 450 sequential transactions were retained and audited.
The actual 254 date-only deadlines and four null deadlines introduce no clock
or timezone claims. Measured completion spacing was at least 12.2148 seconds;
29 inventory exchanges were at least 30.0159 seconds apart after completion,
with no more than five physical starts per rolling minute.

The complete measured Study native took 6,503.34 seconds. Adding the full
300-second publication, 300-second failure and 60-second export reserves plus
a nominal 240-second setup allowance leaves about 3,396 seconds within the
180-minute job. This is measured planning headroom, not proof of a future job
start clock or guaranteed setup duration. The original repository-scoped App
token renewal remains in place for collections lasting more than one hour.
The maintainer controls the serialized rollout: DZS first, then the Study
companion using the newest verified modern family state.


## Header-only HTTP 429 stop — issue #328

A received publisher HTTP 429 now stops after the existing header constraints
are retained, before reading any response body. The response must close
successfully before this intentional stop is assessed. A close or persistence
failure follows the existing failure handling; they cannot establish a healthy
collection. Previously retained holds, numeric MAX constraints, Retry-After
and unknown history remain. An unread 429 body provides no new challenge
classification.

This paragraph supersedes earlier 429 body-inspection descriptions. This
conditional repair preserves non-429 responses, REP selection, the full
collection/parser/IDs/dates/expiry, GitHub error-body handling, publication,
caps, workflow, state schema and finite migration markers. It does not add
missing pending ownership or upgrade a legacy source to a modern continuity
protocol.

Private ordinary transport/state controls and protected normal-AST comparisons
qualify this change. Earlier genuine native evidence retains its original code
and date attribution; no new publisher collection, current factual review,
complete native replay or production run is claimed. The maintainer alone owns
workflow/state coordination and the maintenance rollout for issue #328.
