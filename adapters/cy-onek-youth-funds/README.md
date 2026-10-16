# ONEK youth funding opportunities

This standalone standard-library adapter discovers opportunities from the
[Youth Board of Cyprus funding website](https://youthfunds.onek.org.cy/en/).
The source entry is the [Youth Initiatives programme](https://youthfunds.onek.org.cy/en/services/youth-initiatives/).

## Access and reuse

Public HTTPS HTML and the calendar's own public listing endpoint are used,
without authentication or application submission. Robots are checked before
access. The publisher reserves copyright; no open licence is claimed.
Records contain official titles, short original factual summaries, attribution,
and publisher links. Applicant details, recipient lists, images and full
publisher prose are not republished. Application portals and private APIs
are not crawled. The linked programme guides remain applicant references;
their historical amounts are not substituted for current call facts.

## Discovery and extraction

Each collection discovers programme links from the programme directory,
extracts application windows from their current tables, and follows every
advertised news pagination link. News cards labelled Calls, Extensions or
Events are inspected. Calls supply titles and deadlines; extensions update
the matching round rather than create duplicate awards. Programme tables
and corresponding notices are reconciled. Beneficiary/result announcements
and retrospective event reports are excluded.

The calendar uses its current form nonce and paginates public listings until
an explicit empty response. Every discovered event detail is inspected.
Publisher-labelled test descriptions are excluded; substantive new events
are collected. Missing structures, ambiguous rounds or extensions, conflicting
deadlines, and nonadvancing/excessive pagination fail collection. Normal
publisher text, dates, new notices, pagination and nonce changes are accepted
without hash updates. There are no frozen input hashes or opportunity profiles.

Round IDs retain the source/programme/year/round SHA256 key convention.
The activity period establishes the edition where an English table's heading
is stale. Joint Green action tracks with one application window share an
identity. Three legacy URL-to-key aliases preserve historical identities
whose notices do not expose a sufficient semantic key. New unmatched
programme/event identities use their stable publisher URL. Newly discovered
programme names and official notice links associate their notices with the
same programme/year/round identity. Lettered rounds and written/numeric
ordinals are parsed rather than defaulted to the first call.

Application dates remain date-only unless the publisher establishes a timezone.
Activity dates do not become deadlines. Future application windows and the
closing day have unknown status; past deadlines are expired. Generic website
Open badges do not override application windows. Citizenship and unstated
destinations remain unknown. Youth Initiatives' stated Cyprus activity scope
is retained. Summaries combine extracted application/activity windows with
brief current HTML description, audience and funding excerpts; corresponding
evidence retains attributed excerpts and programme links. Original English
or Greek text uses `summary_language` `en` or `el`, capped at 600 UTF-16 units.
For example, the programme HTML describes youth/group grants, youth aged
13-35, and eligible travel costs where stated. Unstated amounts, passport
rules and PDF-only conditions are not guessed. Calendar records include
their live body and schedule; absent application deadlines remain unknown.

## Run and publication

```sh
python -B adapters/cy-onek-youth-funds/test_adapter.py
python -B adapters/cy-onek-youth-funds/adapter.py --publish
```

The sole live test performs a complete nonempty collection, validates every
record, uniqueness and provenance, and emits the actual complete JSON on stdout.
Its expected count is dynamic. Keep test stdout in memory; no publication
credentials, data files or production Actions are used for validation.

The persistent `youthopps-onek-publisher-v1` allowance serializes collectors,
with at least 12.2 seconds from each physical completion to the next start and
at most five starts per rolling minute. Robots, pagination, redirects, the
calendar POST and original first 503 retry share that allowance. A received 429
durably doubles CURRENT physical and inventory gaps and stops before any
response-body read. It
retains at least 1,800 seconds of exponential cooldown even with a shorter valid
Retry-After. Longer headers and stronger retained limits remain effective.
Content 401/403/451 and genuine visible challenges retain a separate access
hold; literal robots unavailable responses and malformed headers alone do not
invent such a hold. Production restores the latest
trusted family attempt's timing artifact, including failed/cancelled attempts;
missing newest state cannot fall back to older successful state.

Publication remains source-scoped and atomic at
`datas/cy-onek-youth-funds/{data,metadata}.json`. First failure creates no
source folder. Later failure preserves last-good data and success time,
with failure metadata when safely possible. Previous pairs are validated;
conflict-safe commits avoid overwriting newer source data. Stable IDs preserve
creation and first-seen timestamps; material changes advance updated time.

The existing matching main-push, schedule and manual workflow retains its
main/repository guard, restricted credential, serialized family concurrency,
trusted timing artifact. The upfront issue 325 allocation raises the previous
900-second prepublication/1,290-second entry/30-minute job bounds to 2,400 seconds,
2,790 seconds and 55 minutes. Prepublication includes restoration, current-job
proof, prior snapshot, complete collection and parsing. Publication 180 seconds,
failure 180 seconds and export 30 seconds remain nonborrowable, preserving the
original 510-second outer job margin. The original 120-second publisher request
admission headroom and 60/15-second publisher/infrastructure socket bounds
remain. One phase alarm governs execution; no competing publication timer or
new GitHub six-second gate is introduced. The workflow mints its App token after
setup; a usual one-hour lifetime exceeds the 55-minute job by five minutes, but
this static arithmetic is not an actual expiry or complete-graph fit proof.
Production runs require maintainer
authorization; live collection and static review do not prove publication.

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
publisher identity and stronger retained backoff remain effective. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **20:34 UTC** (`34 20 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Inventory attempts wait at least 30 seconds after the preceding inventory
completion, including their physical redirects and original retries. This
covers the programme directory, every programme table partition, each news
listing, calendar form and calendar POST listing including the required empty
termination. News notices, event details and literal robots remain on their
separate physical budget; links alone do not turn a detail into inventory.
The entire dynamic graph remains mandatory: programme links, every advertised
news page (40-page safety limit), selected notices, up to 100 calendar POST pages
with explicit empty termination, and all event details. Exhausted pagination,
missing structures or a budget failure cannot publish a partial result. No
aggregate request/byte cap or first-known-record stop has been added. Existing
per-response 30 MB and six-hop bounds remain; GET retries only the original first
HTTP503 and calendar POST gains no generic retry.

The current full graph and total body volume are unknown. Even the enlarged
allocation cannot guarantee every graph allowed by safety bounds will fit.
Complete genuine collection and full consumer/body/state evidence require a
separate root-authorized native; the old seven published records and successful
Action wrapper do not establish that proof.

Schema 3 journals retain pending logical owners through every physical request,
body read and new/cached REP assessment. Any failed restrictive calculation or
write remains sticky; successful matching completion alone settles the owner.
Safe retained current inodes and parent identities protect state/export writes;
unsafe objects are refused. Strict newest owned identity, attempt/head/digest
and MAX constraints are restored before access; no fallback after a newer
missing artifact is permitted. The existing schema 1 family is not treated as
lost RAM or an absent modern namespace. Its one exact in-place transition
requires separately approved complete old history, newest owned state, local
raw bytes/inodes/locks and full known MAX. Unknown rate ancestry is explicit;
other missing historical policy provenance stays externally unknown.

At the initial LOCAL-binding stage, HOSTED was unset and a separate native
release was still required. The old ONEK_PACING_BOOTSTRAP reference is historical
schema 1 evidence, not authority to initialize schema 3. Any future first-HOSTED
approval must also bind the matching four-key ONEK_PACING_MAINTENANCE workflow
environment. Both first and later-owned actual-main paths and missing/mismatched
configuration refusals must be qualified before production. LOCAL requires a
paused workflow/no overlap and a fresh check after quiet before first wire.
No actual state, publisher request, native test or Action was performed for this
offline implementation.

## Workflow concurrency

`youthopps-onek-publisher-v1` serializes runs of this source. Up to 100 pending
runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Youth Board of Cyprus** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

### Historical #295 journal integrity maintenance

The following evidence and scope describe the earlier #295 IO-only patch,
before this #325 schema 3 migration. At that stage #295 remained open. That
source-local journal integrity maintenance retained an owned read/write journal
descriptor under the existing
separate state.lock. It checks the verified parent, current journal/lock names
and byte generation before and after IO and immediately before a publisher
opener. A replaced or stronger changed journal is refused instead of
overwritten; a separate process-local fatal flag cannot be cleared by a later
healthy finalizer.

Only the original verified incoming-state restore can authorize genuinely new
publisher-directory initialization. An existing directory with missing or
invalid mature state does not become a fresh budget. IO writes serialize in
RAM, then truncate, sync, completely write, sync and verify the original held
inode. In-place interruption may leave empty or partial bytes and sacrifice
availability; such state is rejected. Advisory locks and observed generation
checks are not filesystem compare-and-swap or authentication of arbitrary
same-user writers ignoring locks.

Acquired collection, state, artifact writer/reader and parent descriptors
retain their actual identities through cleanup. Both independent
collection/journal releases are attempted, an active primary exception is
preserved, and healthy close failure refuses a valid owned export. The current
named artifact parent and collection lock are checked. The original artifact
envelope and source-specific readiness contract remain; sources without
readiness signaling gain no new output dependency.

That earlier patch retained every original schema field, seed/approval literal,
parser, complete inventory, normalizer, ID, date/expiry rule, metadata/publication
function, request timing, retry/redirect/status/REP interpretation and workflow. In
particular it did not raise the legacy six-second physical floor or modernize
rate handling. Its schema 1 had no durable pending barrier; total persistence
plus invalidation failure across a process restart was a separate #273 gap.
This #325 migration supersedes those timing/schema statements with the physical
12.2-second completion floor, inventory 30-second floor and durable schema 3
owners described above.

Acceptance evidence for that earlier patch was explicitly limited to existing
byte-matched private journal findings, bounded ordinary private contexts and
static business-AST protection. Original full native acceptance has not been
established by this maintenance, and no expired or available original raw RAM
replay is invented. A pinned current published snapshot/current consumer check,
when provided, is separate evidence; it does not prove the original full raw
inventory, current publisher facts or current access permission. No publisher
collection was performed for that #295 IO-only patch; it does not qualify the
complete genuine native still required for #325.

### Prospective LOCAL initialization and original owned format

The finite schema 1 compatibility path validates the original six-key envelope;
its producer did not include `head_sha`. A separately approved owner, job,
artifact digest and immutable producer binding supply that provenance. The
adapter verifies the producer through the existing repository contents API
route at the exact approved head, including decoded source and Git blob hashes.
LOCAL uses the configured proxy route with an optional explicit token; HOSTED
retains its existing current-job token prerequisite. It
never follows `download_url`. Modern seven-key owned artifacts remain strict;
a missing or invalid newest artifact cannot fall back to an older format.

A genuinely absent LOCAL family directory needs an exact prospective grant,
including a retained ancestor identity, nonce, owned state and history pins.
An owned private ancestor must have mode 0700. The actual mapped-owner sticky
`/tmp` requires exact approved owner/mode/device/inode pins and a separately
retained, approved `/` to named `tmp` identity chain; other writable parents
are refused.
Newest owned state alone does not authorize recreating that directory. Before
creating it, the adapter exclusively creates and syncs a source-owned one-off
consumption record outside the family directory. Existing or interrupted claims
refuse reuse, including in a cold process after the family directory is removed.
This protection assumes the trusted ancestor and claim survive; it does not
protect against an independent same-user writer deleting either authority.

The new directory and both lock files must be exclusively created. With both
locks held and checked, two durable matching commits establish a new pending
initialization and its completion before collection becomes trusted. This is
`PROSPECTIVE_FIRST_LOCAL_INITIALIZATION` only: schema 1's missing pending field
never proves historical settlement. Partial directories, missing mature files
and unsettled state refuse automatic recovery. Known timing and hold constraints
remain MAX/OR; rate-history ancestry stays explicitly unknown, and other lost
historical policy ancestry is qualified externally rather than invented fields.

At that finite-initialization offline stage, HOSTED and the separate modern
LOCAL initializer were unset. Its evidence consisted of bounded private
fixtures, including one cold-process grant-reuse case, with no actual claim,
family initialization, publisher request or native collection at that stage.

Response-less opener failures preserve their original exception and remain
unassessed and sticky. The legacy source retries only its first HTTP 503; this
migration adds no DNS, connection, timeout, TLS or storage-error retry.


### Exact prospective LOCAL bridge

ROOT approved the exact LOCAL-only bridge in
https://github.com/YouthOpps/data-pipeline/issues/325#issuecomment-6213562887.
It binds all ten original attempts/jobs, the authenticated original six-key owned
artifact and immutable producer, all twenty known timing observations, the exact
mapped-owner sticky ancestor chain and the once-only outside-family claim.
The prospective seed is not historical settlement or lifetime-zero proof;
`legacy_rate_history_unknown` stays true and other lost ancestry stays qualified
as unknown. Binding the literal performs no claim or journal initialization.
At that first LOCAL stage, HOSTED remained unapproved. The separate modern
LOCAL initializer remains unset.
The original source, test, workflow and observer algorithms are unchanged by this
binding. A separate ROOT public release and fresh readiness checks are required
before the one complete original nonpublishing native test.


### First native failure and original date dependencies

The separately released original nonpublishing test failed after retrieving
21 complete HTTP 200 responses and finishing discovery of three programmes,
two news pages, twelve notices and an explicitly empty calendar result. The
parser then raised NameError because this migration had omitted the original
_MONTHS and _DATE_PATTERN module assignments used by unchanged dates(). No
records were emitted; this is not a complete native or consumer success.

The two assignments are restored exactly from the immutable original source.
All other module AST, date interpretation, transport, budgets, transition
literals, original test and workflow remain unchanged. Complete retained-body
replay is offline semantic QA and cannot relabel the failed original test.
Its 168 exact persisted saves, 21 requests and 22 matched owner settlements
remain restrictive continuity evidence, including the consumed outside claim.
No fresh initialization or automatic native retry is authorized by this fix.


### Finite mature LOCAL reentry — initial offline stage

At that offline stage, a separate default-null
MATURE_LOCAL_LEGACY_OWNED_REENTRY declaration supported
a reviewed existing schema3 journal while the authentic newest owned artifact
still uses the old six-key schema1 format. It cannot authorize HOSTED or missing
LOCAL state. The original ABSENT grant and its consumed outside claim remain
unchanged; this gate never creates a parent, initializes state or consumes a
claim again. Its runtime authority begins null and ends with the execution.

A future ROOT declaration must bind complete old history, immutable producer
and owned identity, the exact current parent/state/locks/claim, and the full
known MAX with individually matched nonce/request settlement evidence. The
finite proof names the first failed attempt's 22 settled owners and 21 physical
requests; these are prior evidence identities, not next-collection limits. Initial
bytes are checked again under the original collection and state locks before
the ordinary merge/write. After quiet, fresh exact metadata and the surviving
claim are checked with our resulting retained journal generation and current
name, rather than incorrectly requiring obsolete pre-restore bytes.

Missing, pending, changed or stronger unapproved state fails closed. Existing
completed flight/request identities and outcomes are preserved; no blanket
pending reset or inferred historical settlement is permitted. That offline
implementation supplied no actual pins, public reentry literal or execution
grant. Original failed-native evidence and its keeper remain immutable.


### Exact existing mature LOCAL bridge

ROOT approved this source-constant-only declaration in
https://github.com/YouthOpps/data-pipeline/issues/325#issuecomment-6215098088.
It binds the actual existing schema3 parent, journal, both lock names and the
surviving consumed outside claim. The full 125 known timing observations and
MAX derived from 526 state witnesses remain intact without age folding.
The first failed attempt supplies 22 individually settled owner identities
and 21 request identities; existing completed IDs and outcomes are retained.
All ten old attempts and the authentic six-key owned producer remain pinned.

Binding performs no namespace read/write, claim consumption, initialization or
publisher access. That binding left the original ABSENT grant, HOSTED null
declaration and separate LOCAL initializer unchanged. At that source-constant
stage, the corrected observer release remained unauthorized pending fresh
consumer/resource checks and a separate ROOT original-native execution grant.
The first native failure and its original source, receipt and keeper remain
immutable; its retained-body replay is offline semantic QA, not a native pass.


### Corrected native and exact HOSTED binding

The separately approved corrected original test and observer passed on source
f23e7393, with 21 complete HTTP 200 responses and all 14 raw records. Seven
records remain current; the seven actually expired records filter to an empty
set. Full retained-body facts and current-consumer checks passed 14/7/0. The
167 successful save pairs prove 21 new settled owners, with no initialization
or consumed-claim reuse. The first failed attempt remains separate evidence.

ROOT approved the exact production binding in
https://github.com/YouthOpps/data-pipeline/issues/325#issuecomment-6215811228.
The six-key HOSTED declaration and matching four-key ONEK_PACING_MAINTENANCE
collect environment carry the same full MAX and evidence. All 230 known timing
observations and the MAX derived from 1,053 state witnesses are preserved,
including 43 individually settled owners and 42 actual requests across both
attempts. These observations are not 230 publisher requests. Unknown old
policy, pending and 429 ancestry remains qualified; the supported rate-history
unknown flag stays true.

Only that literal, workflow environment and this documentation changed after
the genuine corrected native. LOCAL declarations, the consumed claim, all
source functions, original test, budgets, full graph, daily schedule, App token
step and publication behavior remain unchanged. Before production, original
main must pass first-legacy and later-modern tests with the delivered workflow
environment, including missing, null, mismatched, held and pending negatives.
Binding is not production execution or site delivery. ROOT owns the separate
enable-before-merge step and sole automatic production collection.
