# de-fulbright-germany

The reviewed official Fulbright Germany programme page is [Studienstipendium (Uni und HAW)](https://www.fulbright.de/stipendien/programm/studienstipendium-uni-und-haw). The adapter retrieves the reviewed single programme page using public HTML, robots checks and standard-library HTTPS. Stable identifiers derive from the canonical URL. It preserves the existing source attribution, classification and unknown eligibility; incidental page dates do not establish an application deadline. The inventory is exactly this one standing programme overview; linked awards, unrelated calls and deadline text without validated clock semantics are excluded. `deadline` remains null and the parser accepts only supported timezone-aware timestamp fields.

Access checks use the publisher’s current robots rules before requesting the page, fail on disallow or changed canonical/title, and honor its declared pacing. The output retains only the official title, URL and factual classification; no article body or images are republished. No broader content-reuse licence is claimed.

Run `python -B adapters/de-fulbright-germany/test_adapter.py` for the sole real, nonpublishing collection test; it validates and emits the complete collected JSON. Authorized publication uses `python -B adapters/de-fulbright-germany/adapter.py --publish`.

## Expired-record cleanup

A successful refresh validates the complete nonempty collected inventory, then excludes records whose supported `deadline` has passed before preparing the atomic source data/metadata replacement. Previously published records are used only to preserve matching records' first-seen timestamps; they are never merged back into the replacement. Thus expired old records cannot survive a successful replacement. Missing deadlines, ambiguous clocks, status, age and absence from the response are not expiry evidence.

Explicit timezone-aware deadlines expire strictly after their instant. For date-only deadlines without a documented publisher timezone, cleanup waits until the closing day has ended in every legal timezone: the current date in UTC-12 must be later than the closing date. This conservative boundary retains the full closing day without inventing a publisher timezone; deletion can be delayed up to 26 hours relative to the earliest civil closing-day boundary. Unsupported or ambiguous source clocks remain null deadlines; invalid timestamp fields fail validation before cleanup. If every collected record is expired, the valid nonempty retrieval publishes an empty successful replacement. A later empty upstream retrieval still fails and preserves that last-good pair.

Fetch, parse, validation and publication failures preserve last-good data and success timestamps. Cleanup performs no separate deletion or remote write; publication updates only this source's data/metadata atomically, with the existing concurrent-update protection. The live test collects real inventory without publication; offline boundary checks and static publication review verify cleanup.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The identifying header remains unchanged; current durable pacing and
backoff follow the migration section below. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **19:25 UTC** (`25 19 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The collector has no paginated inventory. Its exact two logical roots remain
one robots document and the assigned programme detail; linked awards are not
collected. The daily schedule is unchanged by the pacing migration.

## Workflow concurrency

`youthopps-de-fulbright-germany-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Fulbright Germany** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Bounded publication error categories

GitHub publication failures retain the known route, HTTP status and bounded
attempt count. A bounded JSON error view emits only fixed known message/code
labels; arbitrary bodies, headers, credentials, URLs and query values are never
copied into diagnostics. The non-fast-forward label requires the exact known
GitHub message on an HTTP 422 PATCH of the main reference; HTTP 422 alone does
not establish a conflict. Malformed, ambiguous or unknown errors remain
unclassified. These diagnostics do not prove the cause of historical failures.

Collection, source fields, access holds, pacing, the existing three publication
attempts, source-pair checks and non-forced atomic writes are unchanged by those
diagnostic categories. Those categories alone add no retry delay, workflow
activation or publication test.

## Bounded publication-conflict retries

Hosted publication proves the exact current job, run attempt, source workflow,
matching head/workflow SHA, main branch, assigned runner and UTC start before
data-source access. A fresh GitHub Date maps the existing 30-minute job bound
to one conservative monotonic deadline; retries cannot renew it. The pinned
App token is minted after job start and expires after one hour, so no token
renewal is needed. Missing or inconsistent proof fails closed.

Every guarded publication call and the current-job proof call on the reviewed
Ubuntu/Python path has a total alarm guard of at most 30 seconds, plus a
remaining-time socket timeout, including its bounded body read. Existing or
unsupported timers stop before transport. Writes and retry waits stop 60
seconds before the job deadline, final outcome reads stop 30 seconds before
it, leaving a cleanup/export allowance. These publication reserves do not
guarantee export after an overlong collection, runner death or an
uninterruptible native call; the existing job timeout remains the outer bound.
No API call receives a fresh deadline.

Only an actual, classified non-fast-forward reference response can retry,
after checking that the desired outcome has not landed and this source pair is
still unchanged. The first two retries add 1–2 and 2–4 seconds after the
ordinary GitHub pacing gate, before reserving the actual request start, within
the existing three attempts and original write deadline. Authentication,
non-CAS and unclassified failures stop without another write attempt. Terminal
publication errors latch a per-process write stop, including later
failure-metadata fallback; bounded read-only outcome checks remain available.
Source-pair changes stay protected; reference writes keep force:false. These publication safeguards preserve factual source collection, fields, the
daily schedule and the 30-minute job cap. Publisher pacing follows the migration
section below.
Validation uses inert external controls; no source fetch, GitHub test write or
production invocation is claimed.


## Durable publisher pacing migration

Every physical publisher request, including robots, redirects and the existing
bounded connection retry, uses at least 12.2 seconds after the previous response
completes and at most five starts per rolling 60 seconds. Stronger retained
limits remain effective. Inventory-purpose requests have a separate retained
30-second completion guard; this assigned programme detail is not pagination.
The two roots and their existing two-attempt/four-hop bounds remain unchanged.
Two logical roots do not guarantee two physical requests.

HTTP 429 stops the collection without retrying that request. Before reading
bounded error evidence, persist an embargo of at least 1,800 seconds, increasing
exponentially with newly observed strikes, any stronger valid Retry-After, and
doubled **current** physical/page gaps. Ordinary rate-limit text stays timed;
genuine visible human-verification or genuine challenge HEAD-title evidence
also retains a hard hold. Malformed or unusable timing alone never creates a
hold. Actual content 401/403/451 and visible refusals remain held. Restrictive
state persists even when the body read fails.

Bounded HTML inspection occurs before redirect follow, including obvious HTML
with missing or incorrect MIME. Hidden, script, footer/navigation and narrowly
identified informational text do not alone create a hold. Literal robots
purpose is checked at each physical hop. A robots redirect cannot relabel
content; robots-only 4xx except 429 mean unavailable REP without clearing prior
holds or inventing Disallow. The original product/legacy robots matching and strict Disallow interpretation
remain. Applicable Request-rate groups use the standard-library REP parser and
retain seconds/requests plus a 0.01-second margin. Current and retained legacy
product-specific constraints, repeated rate directives and crawl delays combine
by their most restrictive effective gap. Physical and page gaps are persisted
while pending; unsupported applicable rates fail closed instead of granting a
weaker allowance.

The old pacing state existed only in process RAM. All 26 reviewed legacy
attempts have exact authenticated job completion history and zero artifacts.
Their completion times are conservative reservations, not measured source
starts. The previous native corpus is unavailable, and the latest final
publication error does not reveal its underlying source retrieval outcome.
New-event count zero means only no strikes observed since this migration;
`legacy_rate_history_unknown` remains true, not a claim of clean lifetime
history. No lost header or refusal is declared absent.

The first transition requires a public maintainer approval bound to the exact
26 run/attempt/head/completion frontier and full typed known-MAX state. Its
initial scope permits only the separately reviewed local native test. An
unknown/newer attempt, changed frontier or unexpected artifact fails closed.
Production requires separate post-native approval of all then-known restrictive
state. Once a modern attempt exists, exact newest owned restoration is mandatory
and a missing newest artifact cannot fall back to the legacy bridge. Modern
artifacts require explicit source/run/attempt/head/digest binding.

Every local collection requires this exact workflow to be manually paused before
history or journal/lock initialization. The fixed ID/path/paused state is checked
again immediately before the first source GET after startup quiet, not per
page. Keep the workflow paused throughout local collection. Local and hosted
state restoration MAX merges intervals, counters and embargoes, bounded unions
the last ten pending starts, and OR merges holds and historical unknown.
Nothing resets the original journal or grants a fresh allowance. A missing
journal in an existing pacing directory is a failure, even with an older owned
artifact. Only a directory created by this process may start an untrusted empty
merge base before its first save; it cannot grant source access. Corrupt,
missing-mature or old-schema state requires reviewed recovery.

Local read-only workflow metadata uses the inherited configured GitHub route;
an explicit token is optional locally and never fabricated. Hosted execution
still requires its workflow token. Signed artifact downloads strip authorization
before the bounded cross-origin read.

A new source **IO and wait** guard is at most 1,500 seconds. Hosted execution
also freezes it below the existing proved publication-write boundary with a
120-second reserve. It is not a parser-wide alarm or a claim that a dedicated
collection phase existed previously. Each source/metadata transport has a
whole-call Unix/Python timer of at most the existing 30 seconds; unsupported or
active timers fail closed. No outer alarm replaces the publication repair.
The 30-minute job, one-hour publication token and existing bounded three-attempt
CAS/own-pair/deadline behavior remain unchanged.

Trusted pacing restores under the collection lock before snapshot/deadline-proof
errors can occur. Main exits always attempt bounded export and release the lock.
State schema 3 requires an explicit `in_flight` flag. A synced pending
reservation precedes every physical request. Response restrictions and REP
assessment (including a stricter crawl delay or Disallow) remain pending until
all restrictions are durably saved; pending state OR merges and blocks new
requests across processes. Only the exact reviewed legacy transition may seed
an explicit false flag. Missing flags and older schemas cannot grant access.
A failed write remains fatal. An interrupted rewrite may leave invalid JSON;
a later process refuses invalid mature state rather than reviving an older
owned artifact. A completion may clear pending only after saving all response
constraints.

The verified journal directory identity stays pinned across the process.
Journal reads, locks, in-place commits and failure cleanup use the retained
parent and original RW journal descriptor. A changed current inode or byte
generation closes access. Failure cleanup does not reopen or mutate a stronger
replacement at the current path.

Any reservation, response restriction or completion persistence failure latches
source access closed, including initialization re-entry. Safely owned stale
artifacts are truncated and synced through validated descriptors before output
signals or unlink. Unsafe owners, permissions, symlinks and hard links are
refused without modification. Failed signals or cleanup cannot make stale state
a healthy handoff. The unchanged native test never receives a publication token
or performs data-source writes; full genuine collection and independent review
remain prerequisites to delivery.

The original nonpublishing native test passed on 15 October 2026: two physical
publisher requests (robots and the complete configured programme page), one raw
record and one current record; the current website consumer also accepted the
valid derived empty envelope. Both complete response bodies and all eight real
state-save chains were retained in private RAM and independently checked.
The original parser, native test, publication safeguards and daily schedule are
unchanged. This is native validation, not a claim of hosted delivery.

[Maintainer production transition approval](https://github.com/YouthOpps/data-pipeline/issues/270#issuecomment-6202857572)
binds the full known restrictive state, including 26 legacy job-completion
upper bounds and eight native reservation/opener/wire/completion observations.
These are 34 observations, not 34 publisher requests. The bounded last-ten
window and all numeric/unknown/hold constraints are retained; the handoff guard
is at least native receipt completion plus 60 seconds, with runtime startup
quiet still enforced. The explicit settled false flag follows successful REP
and response persistence. Modern newest-artifact restoration remains mandatory
after the first owned hosted handoff; no missing-artifact fallback is added.


## Journal integrity maintenance (#295)

This guard-only maintenance retains a genuine RW journal descriptor, its
current named inode and byte generation under the original publisher lock.
Reads are checked before and after access. A commit serializes in RAM, rechecks
authority, then truncates, syncs, writes fully and verifies readback through
the same held inode. Only a successful own commit advances the expected
generation. A changed parent, stronger replacement, missing mature journal or
unexpected generation stops collection before another publisher request.

This accepts an availability tradeoff: interruption may leave empty or partial
JSON. Invalid mature state requires reviewed recovery; it cannot become a fresh
budget or be restored from an older owned artifact. Failure cleanup never
reopens a newer journal pathname for mutation. Locks are advisory, and
generation checks are neither an atomic filesystem compare-and-swap nor
authentication against arbitrary same-UID writers that ignore those locks.

Actual acquired collection, journal, artifact, reader, step-output and parent
descriptors retain identity through use and close. Recycled foreign descriptors
are preserved. A healthy close error is fatal; an active primary exception is
preserved. Owned output is invalidated on a fatal handoff even if readiness
signalling or unlink fails. A previously written true readiness line alone is
not proof of a valid artifact.

The state schema, approved seeds and markers, strict newest-owned history,
source parser, complete frontier, IDs, dates, expiry, network retry behavior,
physical/page pacing, caps, publication and daily schedule are unchanged.
Private ordinary checks cover successful requests, initial modern restoration,
successive exports, no-acquisition exits and the original bounded retry
behavior. No publisher request, new native test, journal migration or
production action was performed for this maintenance.

The complete earlier actual retained records (1 raw / 1 filtered) were replayed
through unchanged validation/expiry and the byte-identical current consumer,
including valid empty and last-good-on-failure cases. Source-content
completeness and factual verification remain the earlier independent native
evidence plus unchanged normal AST; no new native or source-body collection is
claimed.


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


### Mid-commit journal generation protection (#295)

Each successful truncate or partial positional write is acknowledged only after
reading back its exact expected complete bytes and stable inode generation.
The next write, synchronization check and final readback require that last
acknowledgement. A stronger same-inode update during a write is preserved:
the process fails closed, and failure cleanup cannot truncate a changed
generation merely because this writer previously started a commit. Interrupted
owned bytes can still be invalidated through the retained verified descriptor.
This is checked descriptor I/O, not a filesystem compare-and-swap guarantee.

Private actual-function fault controls qualify this bounded I/O repair. Existing
public native evidence retains its original revision and date; no new publisher
requests, original expired raw-body replay, publication or production run are
claimed. Collection, facts, identifiers, deadlines, expiry, state schema,
pending transactions, MAX constraints and workflow behavior are unchanged.
