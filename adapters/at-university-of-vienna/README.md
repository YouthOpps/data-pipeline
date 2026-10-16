# University of Vienna Non-EU exchange places

Source: [University of Vienna International Office](https://international.univie.ac.at/en/study-abroad-with-erasmus-and-co/non-eu-student-exchange-program).

The adapter collects real outgoing exchange allocations from the four public Mobility Online inventories linked by the programme page: Africa/Asia; Australia/Canada/USA; Latin America; and remaining places. It selects each inventory's latest published academic year and checks the full HTML table against the publisher's advertised allocation count. DataTables pagination is client-side; every row is present in the response.

Records preserve the publisher's institution, programme, host country, study field, teaching languages, total places, free places and application-availability indicator. Explicitly available allocations with positive free places are `open`; other allocations have `unknown` status. Negative free-place counts are retained as published. Exchange study is classified as `training`. Scholarships depend on ranking and budget, so the adapter makes no unconditional funding promise or inferred nationality restriction. Recurring date-only programme deadlines do not establish a dated deadline for an individual allocation; `deadline` remains null.

## Access and attribution

Only public read-only HTTPS GET endpoints are used. No login, application submission, external downloads or production execution is required for collection. The programme's robots policy permits these paths; Mobility Online returned 404 for robots.txt during research. The adapter checks robots policy at runtime and fails on denied access. Both publisher hosts share one durable request allowance: at least 12.2 seconds
between physical starts and after the previous response completion, with at
most five starts per rolling minute. Every programme discovery and regional
form/result attempt also waits at least 30 seconds after the previous inventory
response completes. Literal robots requests and Ottawa qualification details
use the physical allowance without becoming inventory requests. Stronger
applicable publisher restrictions remain effective. A source collection lock
serializes the complete run.

The [official imprint](https://international.univie.ac.at/en/imprint) permits attributed noncommercial copying. Records and publication metadata identify `Source: University of Vienna`. Summaries contain factual allocation fields, not copied explanatory articles.

## Stable identity

Identifiers include the source, programme, academic year, partner institution and study field. University of Ottawa has two allocations whose listing fields are otherwise identical. The public agreement details distinguish Bachelor-only from Bachelor/Master allocations through explicit check/cross indicators. All Ottawa allocations always include their study-cycle and duration signature in the identifier, even when only one remains. Place quantities, row order and encrypted detail-link parameters never participate in identity. New ambiguous non-Ottawa allocations fail pending identity review.

Record links use durable public partner-filter form URLs. Transient encrypted agreement links are fetched only to read Ottawa qualification indicators and are not persisted in record identifiers or URLs.

## Run and validate

Python 3.12 or later, standard library only, on a POSIX platform supporting `fcntl` locks:

```sh
python -B adapters/at-university-of-vienna/test_adapter.py
```

The single live test collects genuine records, validates the complete result and emits one JSON document on stdout. Diagnostics go to stderr. It never publishes or writes opportunity data files. The three-file source folder also runs independently when copied outside the repository.

During research the four inventories contained 31, 23, 4 and 32 allocations respectively. These counts are evidence from the reviewed inventory, not hardcoded acceptance limits; runtime checks use the publisher's current counts.

## Publication

The matching `fetch-at-university-of-vienna` Action runs automatically on changes to this adapter folder or its matching workflow on `main`, and also supports manual dispatch. Its sole application command runs this adapter with `--publish`; it does not invoke tests or install dependencies.

Publication writes only `YouthOpps/data-source/datas/at-university-of-vienna/{data,metadata}.json` atomically. First-run failure creates no published source folder. Later failure preserves last-good data and its success timestamp and records failure metadata when possible. Implementation and live testing do not trigger production publication; the automatic Action runs only after maintainer merge.

## Expired-record cleanup

Successful refreshes validate the complete fetched inventory before removing
records with verified past application deadlines. Offset-aware timestamps are
compared as instants; equality with the closing instant remains eligible.
No official timezone is established for date-only deadlines. Cleanup waits
until the date has ended worldwide (UTC−12), a conservative certainty bound,
not an inferred publisher timezone. Explicit publisher offsets take precedence.
Missing deadlines, ambiguous deadlines represented as null, status labels,
record age and absence from a response are never expiry evidence. The existing
publication contract replaces the complete source inventory; old records are
not merged back into the replacement. Consequently expired old records cannot
survive a successful replacement, including when rediscovered with a verified
past deadline. Current records with missing deadlines remain eligible.

The filtered data (including an empty subset after valid nonempty retrieval)
and success metadata are committed atomically only
under this source's paths. An empty/invalid raw response or fetch, parse,
validation or publication failure preserves last-good data and its success
timestamp; safe failure metadata may be updated. Tests never publish.

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

The scheduled refresh runs once daily at **15:48 UTC** (`48 15 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

Every full regional HTML table remains collected; client-side DataTables pages
need no separate HTTP traversal. The inventory guard covers the programme,
initial regional forms, latest-year refetches and complete result forms. Ottawa
agreement details remain separate detail requests. Redirects and permitted
ordinary retries retain their original purpose and all physical accounting.

## Workflow concurrency

`youthopps-at-university-of-vienna-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **University of Vienna** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


## Durable access and transition boundary

The root approved only the finite LOCAL initialization boundary in
[issue 320](https://github.com/YouthOpps/data-pipeline/issues/320#issuecomment-6209897139).
It retains nine exact historical job-completion upper bounds and both unknown
ancestry flags. These bounds are not publisher requests or reconstructed RAM
timestamps. The LOCAL marker is bound; the HOSTED marker remains `None`.
The workflow maintenance evidence/state remain null and fail closed. This is
not a publisher-access release: final observer readiness and a separate root
release are still required before the sole original native test. Later HOSTED
production requires its own reviewed full-native MAX boundary.

The old publisher allowance existed only in process RAM. Its monotonic times
cannot be reconstructed as wall-clock history after the process exits. Both
rate and policy ancestry therefore remain explicitly unknown in every durable
state. An approved finite bridge must retain every known actual constraint;
absence of an old journal does not create a fresh allowance. A modern missing,
empty, corrupt, held or unsettled newest state cannot fall back to older state.
LOCAL collection checks the exact disabled workflow before history/state setup
and again after startup quiet immediately before its first publisher request.
HOSTED restoration requires exact current-run identity and the newest complete
attempt's owned artifact, ordered by actual job completion time.

Each physical request belongs to a durably reserved pending transaction.
Headers, bounded body reads, visible challenges and applicable robots decisions
must be assessed and their restrictions persisted before matching completion
can settle that owner. All persistence or unassessed policy errors are sticky
for this process and reject reentry. A fresh process rejects unresolved state
rather than healing it from an older artifact. Journal writes retain the locked
owned inode and validate its generation and original parent. In-place commits
trade atomic replacement availability for preserving stronger replacement
files: a torn/empty mature journal fails closed. Opportunity publication remains
atomic and source-scoped. Safe stale exports are invalidated through owned
file descriptors before best-effort failure signalling or unlink.

A received 429 durably doubles the CURRENT physical and inventory gaps and
increments its strike before the body is read. Collection stops without a
second publisher request. The embargo is at least 1,800 seconds, exponential
for repeated strikes, even when a valid Retry-After is shorter; a longer valid
value and all stronger known limits win by MAX. An ordinary 429 remains timed;
a genuine visible challenge also holds access. Invalid Retry-After alone does
not invent a refusal. Actual content 401/403/451, visible refusals and applicable
Disallow retain a durable hold. Literal robots-only unavailable-policy handling
cannot relabel content or bypass a denial.

REP evaluates the current YouthOppsBot-specific policy, otherwise wildcard,
and intersects an actual applicable legacy product-specific policy. Per-policy
longest match and Allow ties apply; selected finite decimal delay/rate limits
are combined by MAX. Unrelated groups do not impose a numeric error. Applicable
unsupported numeric policy stays technically pending, without invented consent.
Policy provenance remains scoped to the actual target and response body.

The original two-attempt no-response allowance remains bounded. It applies only
to a typed temporary connection failure inside the actual opener before any
response. TLS, local storage, received HTTP and partial-body errors are not
ordinary connection retries. This deliberately narrows the old broad OSError
catch. Same-host HTTPS and four-hop bounds remain unchanged; every content
redirect is assessed for visible refusal before following it.

The existing job remains 1,800 seconds. The new internal whole allocation is
1,740 seconds, with a collection maximum of 1,200 seconds, restoration up to
180 seconds, initial snapshot up to 120 seconds and nonborrowable publication,
failure and export reserves of 180/180/30 seconds. The failure owner starts
before the first fallback read and never renews. Restoration precedes the
initial data-source snapshot so a snapshot error still reaches pacing export.
These are NEW divisions within the original job, not historical timing proof.
The default GitHub six-second transport and atomic publication body remain
unchanged, with bounded operation wrappers. No full collection/native fit or
production delivery is established by private controls or static review.

The nominal graph is `11 + u + o` physical requests before redirects/retries:
two robots, one programme, four initial regional forms, zero to four latest-year
refetches, four result forms and every actual Ottawa qualification detail.
Neither the former allocation counts nor two historical Ottawa rows bound the
current frontier. Complete native retrieval, all fields and source clauses,
actual expiry subset and current consumer must be independently reviewed after
a separate root release. No new publisher retrieval is claimed here.

### Retained native evidence and own-row closure correction

The first actual nonpublishing original test passed on source `136f9187`:
90 raw allocations, 15 complete physical responses (14 HTTP 200 and one robots
HTTP 404), four complete regional tables, one latest-year refetch and three
Ottawa agreement details. Whole controller time was 415.844 seconds. The exact
original source, receipt and all bodies remain retained; no second collection
was run. The current root-bound three-file consumer accepted raw 90, filtered
90 and actual expired subset zero under that original parser. This result did
not establish that all 90 allocations were currently available.

Actual own-row indicators explicitly refused applications for 31 Africa/Asia
allocations and 32 older remaining-place allocations, each with a published
past application period. The pure correction retains every raw allocation and
ID, changes only those 63 statuses and their existing classification evidence,
and removes them through a generated own-row proof gate. An expired status
alone is insufficient. All 27 explicitly current allocations retain every
non-temporal field. The same retained bodies replay to raw 90, filtered 27,
actual expired subset 63 and derived empty zero in the current consumer.

The proof requires the single exact current refusal, a valid ordered own-row
period and a closing calendar day no later than the UTC reference day. The
publisher's unzoned clock is quoted without inventing a UTC deadline or a year
from the general recurring schedule. Any additional, conflicting or reopening
indicator prevents this proof. A general future remaining-place announcement
does not reopen the selected older allocation. Cleanup also checks the actual
source, classification and allocation's own table URL; status alone or a
foreign scope cannot exclude a record. Full graph, dates, transport, state,
limits, default GitHub behavior and original test remain unchanged.

All 92 actual requested/prewrite/postwrite save chains and 76 pending snapshots
were checked against 15 matching completed flight owners. The full-known MAX
proposal retains 99 typed timing observations, not 99 requests, and the MAX
from 292 constraint witnesses; both unknown ancestry flags remain true. Actual
current namespace and any production boundary require separate root review.
No production success or hosted transition is claimed by this evidence.

### Approved first hosted transition

The maintainer approved the exact HOSTED boundary in
[issue #320](https://github.com/YouthOpps/data-pipeline/issues/320#issuecomment-6210831417).
The six-key HOSTED literal and mandatory four-key workflow maintenance packet
both bind the same approved complete state, compact sorted JSON plus newline
SHA256 `c42c61545814798a5ee8e1a2ed068d3cd899b1d7cb0eeddcdfe891b536634c98`.
The packet retains 84 unique timing values deduplicated from 99 typed
observations and the MAX derived from 292 constraint witnesses, plus the root
observation and 60-second conservative handoff guard. The guard is not a new
publisher request or settlement. Both unknown ancestry flags remain true.

The original publisher budget was RAM-only. The hosted packet therefore says
`ram_history_unknown`; a missing original coordination lock does not prove no
historical restrictions. The existing LOCAL literal remains unchanged. The
root separately observed the settled modern local journal through its actual
shared nonblocking collection/state locks and verified all nine exact completed
legacy attempts/jobs, zero artifacts and the paused source workflow.

The workflow maintenance packet is required for both first and later hosted
restoration. Private integration checks use the exact configured packet with
the delivered preparation and main functions, first finite history and later
exact-owned seven-key artifact restoration. Stronger modern limits survive;
missing, null or mismatched packets refuse before publisher initialization.
This does not establish hosted production success. ROOT alone may enable the
reviewed workflow before merging and assess the sole automatic production run.
The original native source/receipt, pure 90-to-27 qualification and controller
remain unchanged; no additional native collection was run.


## Header-only HTTP 429 stop — issue #328

A received publisher 429 now retains its adaptive constraints and stops before
reading any response body. Both current physical and inventory gaps still
double; the repeated-strike exponential cooldown remains at least 1800 seconds,
and a longer valid Retry-After remains stronger. A short valid Retry-After does
not reduce that fallback. The response context must close successfully before
this stop is assessed; persistence or close failure leaves the original pending
failure gate closed, with no retry or next publisher opener.

This paragraph supersedes earlier 429 body-inspection descriptions. An unread
429 body supplies no new challenge classification; previously retained holds
and all stronger constraints remain. Non-429 response inspection, literal REP
semantics, original parser/frontier/IDs/dates, publication and GitHub pacing,
phase limits, owned-state schemas/seeds and workflow remain unchanged.

Acceptance for this conditional guard uses actual private transport/writer/
completion contexts and exact whole-module AST protection after removing only
the new 429 branch. Existing genuine native evidence retains its original code
and date attribution; this maintenance claims no new publisher collection,
upstream factual review, full native replay or production run. Issue #328
remains open after this three-source pilot.
