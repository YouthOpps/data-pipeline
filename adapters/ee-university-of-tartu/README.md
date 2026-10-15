# University of Tartu current opportunities

This standalone standard-library adapter collects the official
[open-calls table](https://ut.ee/en/open-calls) and the linked
[dated partner exchange calls](https://ut.ee/en/competitions-ut-partner-universities)
and [ENLIGHT course notice](https://ut.ee/en/news/enlight-short-term-blended-mobility-courses).
It checks publisher robots and permitted HTTPS routes before retrieving public
content. Publisher physical requests wait at least 12.2 seconds after completion,
with at most five starts per rolling minute, shared local state and retained
backoff. Inventory exchanges have the separate 30-second completion guard.
Public access is not an open licence. Original notice links, University of
Tartu attribution and bounded factual summaries are retained. It never signs
in, accesses SoleMOVE applications or crawls external course destinations.

The current-call table determines inventory, excluding upcoming competitions,
evergreen scholarship directories and historical partner accordions. Every
named exchange destination must have a matching explicitly dated detail;
ENLIGHT heading links expand into individual course records, with duplicate
and full-link coverage checks. Unknown current-call routes or changed content
boundaries fail instead of publishing a partial inventory.

Stable IDs hash the source slug and exchange title/year identity or ENLIGHT
course URL, truncated to 24 hex characters. The parent deadline and dated
partner heading must agree; ENLIGHT's abbreviated closing year comes from
its article publication year. The complete actual current hub and ENLIGHT
article state 15 November, with the article dated 8 October 2026; neither
primary content states a closing clock or timezone. The adapter therefore
publishes `2026-11-15` as a calendar date rather than inventing a Tallinn
microsecond cutoff. Date-only status and cleanup use the shared index's UTC
closing-calendar-day convention, not a claim about the publisher's clock.
University student affiliation is not citizenship: eligible-country arrays
remain empty. Exchange host countries come from explicit destination headings;
ENLIGHT named host institutions remain attributed summary evidence without
inferred country codes. Categories use the supported training/exchange offer.

Run from this folder:

```sh
python -B adapter.py
python -B test_adapter.py
python -B adapter.py --publish
```

The sole live test emits the complete nonempty raw collection as JSON, validates
all records and uses no publication credentials or dataset files. Production
alone obtains DATA_SOURCE_TOKEN and publishes this source's data and metadata
atomically under `datas/ee-university-of-tartu/` with nonforce Git-tree updates.
First failure creates no folder; later failure preserves the last-good data
and success timestamp while recording failure metadata when safe. Matching
identities retain creation/first-seen timestamps in the complete replacement.

## Expired-record cleanup

Publisher calendar closings retain date-only precision. The record remains
open and eligible throughout that UTC calendar date; it expires once the next
UTC day begins. Publication or event dates never authorize removal.

After a complete nonempty retrieval passes validation, the successful refresh
removes records with verified past application deadlines before preparing the
single atomic data/metadata publication. The next dataset fully replaces the
old dataset; old records are never merged back, including expired old IDs
still returned by the publisher. Status, record age, absence from a response
and missing or ambiguous deadlines do not establish expiry.

The UTC closing-day convention replaces the old date-only UTC-12 cleanup
grace and the parser's unsupported end-of-Tallinn-day timestamp. A genuinely
supported aware timestamp retains its exact cutoff and is removed afterwards.
Missing or unsupported dates remain unknown; they do not imply expiry.

If every validated retrieved record has expired, publish an empty data array
and success metadata atomically, with record_count zero. An empty or invalid
upstream collection still fails. Later runs accept this valid empty prior
pair; failures preserve it and its last-success timestamp, updating only safe
failure metadata. No cleanup write precedes successful collection, and no
other source's files are touched. The live test remains a complete raw
nonpublishing collection; production publication is reviewed statically.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. Publisher
family and per-host namespace identities are preserved. The durable pacing
migration below changes request spacing and backoff without clearing known
constraints. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **12:02 UTC** (`2 12 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

There is no numbered next-page continuation, but the complete inventory consists
of the open-calls hub and every currently listed permitted child hub: the partner
destination list and/or ENLIGHT course list. Each physical inventory exchange,
including retries and redirects, has the separate 30-second completion guard.
Literal robots requests keep the physical 12.2-second budget, and linked course
and PDF destinations are not fetched. No page or known-ID stop truncates scope.

## Workflow concurrency

`youthopps-ee-university-of-tartu-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **University of Tartu** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


## Durable access state and first transition

All access-state logic belongs to this standalone adapter. Schema 3 explicitly
records an unsettled transaction before a physical request, retains constraints
before a bounded body read and settles only after the complete response and REP
assessment have been successfully persisted. Current and retained legacy robot
groups must independently permit the target; stricter crawl delays and request
rates are retained. Literal robots-only 4xx responses other than 429 are
unavailable rules, while content refusals and genuine visible/HEAD challenges
remain access holds. Hidden/chrome text and informational denial instructions
do not alone create a hold.

HTTP 429 stops the current collection and durably increases both physical and
inventory gaps by doubling their current values, retaining at least the
exponential 30-minute cooldown and any stronger valid Retry-After. Historical
headers and strike counts that were not logged remain unknown. An invalid
header alone does not invent an access hold. The original healthy two-attempt
connection-error retry and four-hop redirect bounds remain; a persistence,
policy, refusal or rate-limit failure cannot use that retry as a bypass.

LOCAL collection requires the exact source workflow manually paused before
history/state initialization and again after startup quiet immediately before
the first publisher request. It reads fresh complete exact-attempt history
without pretending to be a hosted run. Existing configured GitHub read access
may work without a separately exported token; hosted runs require their normal
workflow token. Neither route gives publisher authentication or changes TLS.

The latest applicable completed attempt is selected by authenticated exact job
completion, not mutable run timestamps. New attempts require their exact owned
artifact, head, digest and source binding. Missing or malformed newest state
fails closed; it never falls back to an older allowance. A deleted journal in
a previously initialized directory cannot be recreated from an older artifact.
Only a genuinely new directory can initialize, and its creation proof is
cleared before the first journal fsync even when durability fails.

The original verified parent directory is retained and state IO uses relative
file descriptors with no-follow, ownership, mode, link and identity checks. A
legitimate atomic journal replacement within the same parent remains valid;
failure invalidation checks the current inode as well as retained lock handles.
Unsafe files and FIFO paths are refused without hanging or modifying them.
Any request or restrictive/completion persistence failure is sticky through
same-process re-entry; a pending state prevents fresh-process older-state
recovery. Safely owned stale output is invalidated through validated descriptors
before failure signals, including signal-open/write and unlink errors.

A received response remains pending until its complete bounded body and applicable
policy have been assessed. Partial or failed bodies retain timing and an
unresolved pending transaction, even after a 429 cooldown ends. TLS verification
errors also stop without retry. Only ordinary failures before a response is
received retain the original bounded connection retry. These distinctions are
verified with private actual-function controls, not fresh publisher requests.

The reviewed legacy evidence currently contains 11 terminal workflow wrappers:
10 executed successful jobs and one exact cancelled attempt with no jobs. The
cancelled attempt has no invented job-completion time. All 10 actual logs are
retained privately with hashes, and the old local namespace was inspected
read-only and found absent. Absence is not zero history. The proposed one-off
transition retains all executed completion upper bounds, explicit unlogged
unknowns and a fresh startup quiet guard. The separately approved LOCAL
transition supports the one genuine non-publishing native test; it does not
approve production. The counter starts only at migration, never at zero
historical strikes. Any new or unpinned zero-job attempt fails closed.

## Bounded execution and evidence limits

The existing daily 12:02 UTC slot and 30-minute Action cap are unchanged. A
stronger 1,740-second application bound reserves 240 seconds for publication,
120 for safe failure reporting and 30 for inert-state export after collection
ends no later than 1,350 seconds. Individual network timeouts cannot exceed
30 seconds or the remaining phase. No publisher collection occurs while the
source lacks its separate exact access approval. Production requires its own
reviewed full-native-state transition and maintainer handoff.

A successful supported graph has two or three content units, nominally up to
six publisher requests including robots. The unchanged retries, redirects and
uncached policy checks have a conservative loose bound of 192 physical
attempts. That worst case cannot be guaranteed to fit the job cap; slow or
stricter-policy runs fail closed and retain last-good data rather than raising
limits or dropping inventory. Historical published counts of 17 or 23 are
observations, not a fixed target or evidence that a current full native passed.
Full collection remains necessary because no publisher change cursor or
older-update/removal feed proves a safe incremental boundary. The sole genuine
native test completed on its original frozen source. Independent review and
separate production approval remain delivery gates for this corrected source.

## Genuine native evidence and date-only correction

The maintainer released exactly one original non-publishing native test at
source `a44ca5dd`, after the reviewed LOCAL transition and fresh workflow pause.
It passed with all 17 raw records and all 17 current filtered records. Six real
publisher exchanges include three robots responses, the hub, a literal ENLIGHT
redirect and the final article; the hub currently contains only that ENLIGHT
row. Every one of the 17 UUID course links was represented, without fetching
external course pages. All actual headers, bodies, stdout and 22 successful
state-save snapshots are retained privately for review.

The original test emitted unsupported end-of-Tallinn-day timestamps. After
complete actual source inspection, four pure date/status/validation/cleanup
helpers were corrected and applied to the entire retained input offline. This
is qualified reuse of the genuine original native evidence, not a new live test
at the corrected source hash. All 17 identities and other non-temporal fields
remain identical. UTC closing-day, offset-aware exact-instant and null/invalid
date controls accompany the complete current-consumer replay. No publisher
request or source journal write was made for this pure correction.

The source-local owned-artifact guard additionally pins its original private
output directory and current file identity. All artifact operations are
descriptor-relative, so a replacement parent is refused without truncating
or unlinking unrelated replacement files. Safely owned stale JSON is truncated
and synced before readiness-signal I/O; independent signal and unlink attempts
remain available after cleanup faults. This also covers failures before envelope
creation and after a new envelope is installed. Nonblocking regular-file checks
refuse unsafe files and FIFOs without hanging. These error-only guards reuse the
independently reviewed issue #282 pattern and private actual-function tests;
normal collection, publisher pacing and journal state are unchanged.
