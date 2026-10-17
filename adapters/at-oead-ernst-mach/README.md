# at-oead-ernst-mach

The reviewed official OeAD (© OeAD attribution retained) programme page is [Ernst Mach Scholarship – Worldwide](https://studyinaustria.at/en/news/article/2026/08/ernst-mach-stipendium-weltweit-bewerbung-bis-1-dezember-2026). The adapter retrieves the reviewed single programme page using public HTML, robots checks and standard-library HTTPS. Stable identifiers derive from the canonical URL. It preserves the existing source attribution, classification and unknown eligibility; incidental page dates do not establish an application deadline. The inventory is exactly this one reviewed programme notice; linked awards and unrelated calls are excluded. Its verified canonical page and exact heading explicitly identify the application deadline as 1 December 2026, so `deadline` is `2026-12-01`. No closing hour or publisher timezone is inferred. Validation permits an exact, valid calendar date only for `deadline`; all other timestamp fields still require explicit timezone-aware instants, and naive clock values fail. Older published records with a null deadline remain compatible.

Access checks use the publisher’s current robots rules before requesting the page, fail on disallow or changed canonical/title, and honor its declared pacing. The output retains only the official title, URL and factual classification; no article body or images are republished. No broader content-reuse licence is claimed.

Run `python -B adapters/at-oead-ernst-mach/test_adapter.py` for the sole real, nonpublishing collection test; it validates and emits the complete collected JSON. Authorized publication uses `python -B adapters/at-oead-ernst-mach/adapter.py --publish`.

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
access; restrictive rate and delay directives remain effective. Native
pacing, backoff and durable budget identities remain unchanged. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **18:51 UTC** (`51 18 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The collector has one reviewed programme detail and no paginated inventory.
The daily schedule is unchanged by the durable publisher-pacing migration below.

## Workflow concurrency

`youthopps-at-oead-ernst-mach-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **OeAD** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


## Durable publisher pacing

All physical publisher requests, including REP retrieval, redirects and the
existing two connection attempts, share a source-local serialized budget. The
minimum gap is 12.2 seconds after a response attempt completes, with at most
five starts per rolling 60 seconds. Stronger declared or retained limits remain
in force. Inventory-purpose transport also has a separate 30-second completion
guard; this adapter currently retrieves only a programme detail, so it does not
add an artificial page delay to that detail. Publisher requests retain the bot
identity above, verified TLS and the reviewed HTTPS host. Same-host redirect
paths remain available; the existing four-hop bound is unchanged.

HTTP 429 durably doubles the current physical and inventory gaps and increases
the strike count before reading any response body. It ends that collection
without another publisher request. The cooldown is at least 30 minutes,
increases exponentially for repeated responses, and retains any longer valid
Retry-After or known embargo. An ordinary 429 creates a timed restriction;
a genuine visible access challenge also retains an access hold. Invalid or
stale Retry-After values alone do not create holds. Actual content refusals
remain retained. Literal robots.txt 4xx responses other than 429 are unavailable
REP rules, while unreachable policies fail closed. A robots redirect cannot
relabel a content request. Current and legacy bot product rules both apply.

Owned restoration validates the current workflow/run/attempt/head and orders
completed attempts by their actual exact-job completion times. It restores the
newest exact owned artifact, with digest and source/head binding; a missing or
invalid newer artifact cannot fall back to an older attempt. Local collection
requires the exact workflow to be manually disabled before history/state
initialization and again after the startup quiet period, immediately before
its first physical request. Local and hosted collection share the same budget
family. Healthy process re-entry repeats restoration and these entry checks;
a persistence-failure latch cannot be reset by re-entry.

The 27 reviewed pre-artifact attempts have authentic completed-job records and
currently empty artifact inventories. Their old process-local RAM allowance
cannot be recovered: historical rate-limit/refusal knowledge remains explicitly
unknown, never a claim of zero prior requests or strikes. The code rejects
initial access until an exact independently reviewed issue-linked transition is
installed. That exception covers only those pinned attempts, preserves their
completion upper bounds and all known stronger constraints, and requires a
fresh 60-second quiet period. Any unrecognized newer attempt requires its owned
state. The first hosted transition additionally requires maintainer approval
of the full known genuine-native MAX; it cannot use an empty generic bootstrap.

The new durable schema records an `in_flight` barrier before every physical
request. It remains true through response restrictions and becomes false only
when all known constraints and the actual completion are durably saved. REP body
parsing and applicable delay/Disallow persistence happen inside that boundary;
cached policy enforcement occurs after reservation and before the next opener.
A fresh
process or owned restoration rejects an unsettled barrier. Merging competing
local/owned states uses OR, so an older completed state cannot clear a newer
unsettled request. A failed write invalidates only the current safely owned
journal inode and retains the stronger attempted constraints in RAM. If journal
invalidation also fails, the pre-request durable barrier still prevents a fresh
process from trusting the older journal.

For native evidence, retain every reservation, response-constraint and completion
save. A final settled barrier requires independent proof matching all actual
requests to their successful durable completions; do not OR every intermediate
healthy in-flight value into the final lifecycle state. Numerical/rate/hold MAX
constraints and all original observations remain preserved. Unsettled current
states cannot be cleared by older completed snapshots. The explicit unknown
27-attempt legacy transition is separate from this observed settlement proof.

Publisher reservation/completion or restrictive-state persistence failures
latch further source access closed for that process. Stale valid pacing state
cannot be exported as healthy. Owned artifact failure cleanup validates and
pins the file descriptor, truncates and syncs it before fallible signalling or
unlink, and preserves unsafe filesystem objects. Failure-signalling or unlink
errors do not make stale state trustworthy.

The existing 30-minute Action cap and 18:51 UTC daily cron remain unchanged.
A single absolute 1,290-second process deadline includes restoration,
collection, publication/failure reporting and a final 30-second export window.
Restoration is bounded by 180 seconds; collection is bounded by 900 seconds and
must stop before the final 180-second publication/failure-reporting reserve and
export window. The original default GitHub publication transport and atomic
publication behavior are retained. These bounds fail closed with last-good
preservation; they do not prove that a complete native collection has passed.


## Reviewed migration evidence (15 October 2026)

The original sole nonpublishing live test passed in 140.55 seconds with two
actual publisher requests, one complete raw programme and one expiry-filtered
record. Independent review checked both full response bodies and headers, all
record facts, nine actual journal persistence events, and the matching durable
pending-to-completed transitions for both requests. The current website
consumer at `80264d7386f5c031196b9ffc5998109c9f969a6f` accepted the complete
raw, actual filtered and derived-empty envelopes (1/1/0). The derived empty case
is a consumer envelope, not evidence of an empty upstream collection.

The [initial local transition approval](https://github.com/YouthOpps/data-pipeline/issues/272#issuecomment-6201692246)
covers only the exact 27 reviewed legacy attempts. The separate
[full native MAX approval](https://github.com/YouthOpps/data-pipeline/issues/272#issuecomment-6201889163)
authorizes the first hosted bridge to retain all 27 legacy completion bounds,
the two actual native requests and every known stronger pacing constraint.
Its 35 timing observations include multiple reservation/wire/completion bounds
for the same requests; they are not 35 physical requests. The settled pending
value is supported by the complete native ledger, while historical rate/refusal
knowledge remains unknown. A new unowned attempt cannot reuse the legacy
exception. The workflow carries the exact approved MAX mapping; production
publication and live-site verification remain separate maintainer checks.


## Unassessed rate-limit policy failure (issue #275)

A 429 response can exceed supported counter, timing or numeric limits before
its increased restrictions are calculated or saved. Such an assessment failure
now makes the publisher process permanently unhealthy and leaves the already
durable pre-request `in_flight` barrier unsettled. Completion cannot replace it
with settled older limits. Further requests, same-process collection reentry,
owned export and fresh-process restoration with an older settled artifact must
refuse this unresolved attempt. Ordinary supported 429 responses still retain
their increased intervals, page spacing and cooldown before reading a body.

Verification uses actual transport functions with an inert opener and private
journals, including counter 99, overflowing valid page spacing, pre-request
physical-spacing rejection, stronger supported limits, and fresh-process
restoration against older settled state. No publisher request or new native
collection is used for this conditional error-path repair. The complete earlier
native result and production comparison from issue #272 remain evidence only
for unchanged collection, parsing, facts, validation, expiry and publication;
this maintenance does not independently certify a new source refresh.


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
