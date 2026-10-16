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
with at least six seconds between starts and at most ten per rolling minute.
Robots, pagination, redirects and retries share that allowance. Retry-After
and refusals preserve embargo/blocked state. Production restores the latest
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
30-minute job limit and trusted timing artifact. Collection has a 900-second
bound within the 1,290-second lifecycle, including bounded publication,
failure reporting and export phases. Production runs require maintainer
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
pacing, backoff and durable budget identities remain unchanged. Existing
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

Inventory page continuations and partitions wait at least 12 seconds after the
previous inventory page finishes before fetching the next page in this process.
This includes taxonomy/listing pages where applicable. The existing native
transport still enforces its physical-request allowance across retries, redirects
and detail requests, with stricter publisher limits and retained backoff taking
precedence. The page guard does not raise the durable request interval for all
subsequent article/detail requests or reset existing budget/refusal state.
Inventory scope, parsing, validation and atomic publication are unchanged.

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

### Journal integrity maintenance

Refs #295. Issue #295 remains open. This source-local journal integrity
maintenance retains an owned read/write journal descriptor under the existing
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

Every original schema field, seed/approval literal, parser, complete inventory,
normalizer, ID, date/expiry rule, metadata/publication function, request
timing, retry/redirect/status/REP interpretation and workflow is retained. In
particular this maintenance does not raise the legacy six-second physical floor
or modernize rate handling. These schemas have no durable pending barrier:
total persistence plus invalidation failure across a process restart remains a
separate #273 gap.

Acceptance evidence for this maintenance is explicitly limited to existing
byte-matched private journal findings, bounded ordinary private contexts and
static business-AST protection. Original full native acceptance has not been
established by this maintenance, and no expired or available original raw RAM
replay is invented. A pinned current published snapshot/current consumer check,
when provided, is separate evidence; it does not prove the original full raw
inventory, current publisher facts or current access permission. No publisher
collection is performed for this IO-only patch.
