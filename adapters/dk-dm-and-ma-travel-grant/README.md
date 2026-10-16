# DM student funding and member training

Source `dk-dm-and-ma-travel-grant` starts with DM's public
[English](https://dm.dk/students/) and [Danish](https://dm.dk/studerende/)
student pages. It follows current membership, benefit and grant links to
discover student funding and training programmes. The inventory is not a fixed
profile list. Programme headings, conditions, amounts and dated application
windows are extracted from current HTML on every collection.

## Inventory and identity

The 14 October 2026 research traversal inspected 24 discovered pages and
identified five programmes: the travel grant, Activity Fund/Sponsoratpulje,
free classes, online-course access and SLING/ULING/NAKU mentoring. All five
existing published IDs are retained through route-to-identity aliases. These
aliases contain no funding facts. English/Danish versions of the same offer
are combined; their differing conditions remain attributed to their own URLs.
New programme routes receive stable source/route identities. Each programme
retains one ID when dated application windows change or reorder. All explicit
windows are retained in `application_windows`; multiple windows leave the
universal `deadline` unknown. Relevant receipt and CVR guidance from the linked
reimbursement page is included with the Activity Fund's conditions and digest.

The Danish travel page currently supplies the complete next window,
1 November 2026 to 1 February 2027. English yearless recurring dates do not
invent years. Relative instructions such as applying a month before an event
remain conditions, not fabricated calendar deadlines. Unknown closing clocks
and zones stay unknown. Future openings do not imply currently open calls.

Public HTML course catalogues describe the membership entitlement; individual
software topics are not separate funded awards. The JavaScript event calendar
and external course-provider catalogue are outside this HTML collection.
Advice services, membership signup, personal mentor directories, banking,
insurance, consumer discounts, recipient stories and promotional prize draws
are excluded. New relevant programme links within the student membership
scope are followed. Pagination links using `page` or `p` are retained.

Citizenship is never inferred from membership, publisher location or enrollment.
The class programme has a Danish host only when the publisher explicitly names
its Danish cities. Other programmes have no complete host-country whitelist.

## Material updates and access

No content fingerprint is compared with a pinned value. Factual supporting
blocks and current amounts are extracted on each run. A digest of complete
programme blocks also detects changes beyond the introduction, including
conditions not repeated in the short summary. Publication compares all material
record fields, so those changes advance `updated_at`; unchanged records retain
their earlier creation and update timestamps. Source facts remain in attributed
evidence without clipping conditions to fit the 600 UTF-16-unit summary limit.

Reviewed official privacy, cookie and membership-payment policies did not
establish a general factual-collection prohibition. This is not an open-license
claim. Current linked policies and robots are checked on every run. DM's
robots endpoint returned 404 during research, which means an absent robots
file, not a licence. Access challenges, explicit collection prohibitions,
incomplete documents, unsafe redirects and failed required pages abort the run.
Policy wording changes alone do not invalidate programme facts.

Normal verified HTTPS and the identifying YouthOppsBot User-Agent remain enabled.
Publisher requests, robots, redirects and retries share locked local pacing:
at least 12.2 seconds from each completed attempt to the next physical start,
and at most five starts in a rolling 60 seconds. Stronger robots and retained
limits remain effective. The two entry pages, member/grant/benefit directory
pages and supported `page`/`p` pagination use a separate 30-second completion
guard on every physical inventory attempt, including redirects and retries.
Grant details, policy documents and reimbursement guidance remain ordinary
physical requests; links in a document alone do not make it an inventory. Separate machines
must not overlap publisher collections. Signup redirects to another host are
not collection inputs; no account, application form or external service is used.

## Run and publication

```sh
python -B adapters/dk-dm-and-ma-travel-grant/test_adapter.py
python -B adapters/dk-dm-and-ma-travel-grant/adapter.py
```

The sole live test collects real records, validates every record and emits one
complete JSON document to stdout. Diagnostics use stderr; tests neither publish
nor write datasets. Parser development reused genuine responses only in RAM;
the delivered test has no cache hooks, fixtures or mocked retrieval.

The existing workflow's only application command is this adapter's `--publish`.
Its source-filtered main push, schedule, manual dispatch, repository guard,
restricted token and serialized publication remain unchanged. Publication writes
only `datas/dk-dm-and-ma-travel-grant/{data,metadata}.json` in one conflict-safe
commit. First failure creates no source folder; later failures preserve last-good
data and success timestamps. Production Actions are not a test harness.

## Expired-record cleanup


The travel round uses its published date-only closing; its closing clock
and timezone are unspecified. Relative or recurring unanchored dates remain null.

After a complete nonempty retrieval passes validation, the successful refresh
removes records with verified past application deadlines before preparing the
single atomic data/metadata publication. The next dataset fully replaces the
old dataset; old records are never merged back, including expired old IDs
still returned by the publisher. Status, record age, absence from a response
and missing or ambiguous deadlines do not establish expiry.

For a supported date-only closing without a documented timezone, UTC-12 is
used only as a conservative global certainty bound, not an inferred publisher
timezone: retain the record throughout its possible local closing day, and
remove it only once that date has ended everywhere. Aware timestamps are
retained at the exact cutoff and removed afterwards.

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
access; restrictive rate and delay directives remain effective. Native
pacing, backoff and durable budget identities remain unchanged. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **17:56 UTC** (`56 17 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The complete dynamic traversal retains supported pagination links. Only actual
entry, category/listing and continuation requests use the inventory guard;
the daily schedule remains unchanged.

## Workflow concurrency

`youthopps-dk-dm-and-ma-travel-grant-publisher-v1` serializes runs of this
source. Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Publication error diagnostics

Failed GitHub publication reports retain the HTTP method, a recognized API
route without its query, status and bounded attempt count. If checking the
remote outcome also fails, both failure contexts are retained. Diagnostics do
not include credentials, headers, response bodies or signed URLs. The existing
three-attempt limit, current-head and source-pair conflict checks, atomic writes
and successful collection/publication behavior are unchanged. This diagnostic
maintenance does not establish the underlying cause of older generic failures.

## Source display name

Published metadata uses `display_name` = **DM** for the visible
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
Source-pair changes stay protected; reference writes keep force:false. Source
collection, fields, pacing, schedule and the 30-minute job cap are unchanged.
Validation uses inert external controls; no source fetch, GitHub test write or
production invocation is claimed.

## Durable pacing migration (issue #291)

This migration is currently offline and unqualified for publisher access or
production. The original test, full two-root breadth-first discovery, explicit
150-page overflow failure, bilingual records, identifiers, dates and publication
conflict behavior are preserved. The implementation has no manual programme
profiles, inventory truncation or pagination stop based on an existing ID.
Complete collection remains necessary because the publisher has no verified
incremental reconciliation contract.

Schema 3 stores a durable pending owner before physical access. Redirects,
robots parsing, cached restrictions, body reads and page access-policy checks
remain inside that owner until all restrictions and completion are saved.
Every storage or unassessed policy/calculation failure latches a process stop;
a new process refuses an unsettled journal. Failed persistence cannot be healed
by a later completion or an older hosted artifact. The original journal is
invalidated only through its verified current inode and parent, with the pending
reservation providing protection when cleanup itself fails.

A 429 stops the run without same-run retry. It doubles both current persisted
physical and inventory gaps and retains at least 30 minutes of exponentially
increasing cooldown, honoring a longer valid Retry-After. Invalid, stale or
unsupported Retry-After alone never invents a permanent refusal. Actual content
401/403/451, genuine visible challenges and explicit applicable restrictions
retain a hold. Literal robots 4xx except 429 is unavailable REP; it does not
clear content refusals or stronger rate constraints.

The exact latest completed attempt, immutable head, main branch, actual job
completion, artifact owner and archive digest govern mature restoration. Queued
reruns retain their preceding completed attempt rather than silently falling
back to an older run. LOCAL mode requires the workflow paused, fresh no-active
history before namespace creation and another definition check after quiet time
before the first request. Restore precedes source-pair snapshot access; final
artifact export remains attempted on later failures. Neither a missing legacy
field nor a missing artifact establishes zero request, refusal or rate history.

The original namespace and known `dm.dk`, `www.dm.dk` and GitHub host files are
read without creation, with shared nonblocking locks and current parent/name/FD
identity checks after reading. Both publisher aliases normalize to `dm.dk` in
the current original code; a separate old alias file is still checked. The
known publisher file retains ten old reservations and its original inode;
missing counter/completion fields are explicitly unknown. A one-off exact
maintainer-approved LOCAL bridge is now bound; separate native and
production releases remain pending.
The original GitHub file path and successful six-second/default Retry-After
behavior remain separate from publisher constraints.

The existing job timeout remains 30 minutes and cron remains 17:56 UTC. New
entry-frozen source I/O/wait admission is at most 1200 seconds within a
1740-second overall admission limit, with 180-second publication and failure
reserves plus 30 seconds for export. These are stricter admission guards, not
a claim that an uninterruptible parser is hard-bounded. Each physical source
call, including its body read, has a total guard of at most 30 seconds and the
remaining source allowance. Original hosted publication timers, exact-job
proof, three attempts, CAS jitter and token/deadline checks remain unchanged;
no unrelated outer alarm interferes with them. Slow setup, a larger current
frontier, stronger restored pacing or backoff may prevent completion. Preserve
last-good data rather than raise limits or omit inputs; live fit remains
unverified until a separately released complete original test succeeds.

Artifact export validates its private runner directory and exact output inode
through held descriptors. On failure it invalidates its safely owned original
FD and attempts both an independent false signal and unlink. Unsafe ownership,
mode, symlinks, hard links, FIFOs and unrelated replacement objects are refused
and preserved. The private job directory is trusted against an independent
same-UID process forging replacement envelopes after successful publication;
finite in-process checks do not authenticate such an adversarial writer.

All implementation controls use private journals, synthetic responses and
external harnesses. They do not establish current publisher facts, full live
coverage, actual timing fit or production readiness. No publisher request,
native test, actual family journal write or workflow activation is authorized
by those controls.

The publisher journal is rewritten through one retained, locked read/write
descriptor. A changed parent, name, inode or generation closes access rather
than adopting or overwriting a replacement. This deliberately favors safety
over crash availability: interruption during an in-place rewrite can leave an
empty or partial journal. A later process then refuses collection and older
artifacts cannot substitute for that invalid local state; operator review is
required. Cleanup targets only the retained original descriptor.

Execution admits proof, restoration, snapshot, source collection, primary
publication, failure reporting and export against one fixed entry clock.
The source phase is at most 1,200 seconds and also limited by remaining
headroom for publication (180 seconds), failure reporting (180 seconds) and
export (30 seconds); restoration and snapshot time cannot be borrowed back.
Failure reporting begins before its first fallback snapshot and retains one
fixed end across subsequent reads, write and verification. Original job/token
proof, GitHub retry, CAS and publication bodies remain unchanged. These are
admission and complete-call I/O bounds within the original 30-minute job,
not a claim that arbitrary parser work is timed or the complete live graph
will fit. The genuine complete native passed on source `9cebb8cb`; the
first-hosted correction is qualified by the retained-body replay below.

The original two connection attempts remain available only for typed network
faults before a response, including temporary DNS resolution (`EAI_AGAIN`).
HTTP responses do not enter that retry path. Storage failures, TLS failures,
permanent DNS errors and opaque errors fail closed; unresolved response or
policy work retains pending state (or an invalidated journal), so a second
request or an older artifact cannot silently restore access.

Current bot robots rules select their own specific group or wildcard before
retained legacy-specific restrictions are applied. A legacy allow cannot
suppress a current wildcard denial or longer delay, while a current specific
grant is not overridden by the historical wildcard. Cached policies keep the
same target-specific assessment.

Each state-lock context retains a private duplicate of the original locked
file description. Replacing an exposed descriptor with a newly opened handle
to the same inode cannot release the private flock or authorize a write. The
context verifies both descriptions before cleanup, refuses further work on
lost authority, and preserves unrelated reused descriptors. These are private
regressions for the confirmed authority defect, not live-access evidence.

The maintainer approved only the exact initial local transition in
https://github.com/YouthOpps/data-pipeline/issues/291#issuecomment-6206821242.
Its reviewed seed retains 38 known timing observations, actual original-host
constraints and unknown historical rate-limit state. Missing known aliases
remain absent observations rather than proof of no past restrictions. The
original namespace is checked read-only; a newly appeared modern namespace
or changed history/provenance refuses this transition. This literal approval
is not a native-test, production or workflow-execution release. A separately
bound original test and later production review remain required.

The complete original nonpublishing test passed on immutable source `9cebb8cb`:
26 physical responses, the full two-root 24-route catalogue and all five
records (five retained after expiry), with actual current consumer 5/5/0.
Every one of 177 persisted state afterimages and its attempted state was
retained; 24 logical owners settled through matching actual completion. No
new collection was made to qualify the later first-hosted compatibility fix.

A fresh hosted runner cannot reproduce the old local directory/inode pins.
The maintainer approved the exact first-hosted transfer and full native MAX in
https://github.com/YouthOpps/data-pipeline/issues/291#issuecomment-6207347601.
The narrow exception requires the actual existing job/publication proof,
matching immutable main workflow/head, publishing credentials, all 28 exact
old jobs with zero artifacts, and the exact approved state envelope. Its
modern namespace must be absent and exclusively newly created before any
journal writes. Existing or stronger current state refuses this first-state
exception. LOCAL still checks the actual original files; later hosted runs
still require the newest exact owned artifact, with stronger constraints
retained. This transfers retained provenance rather than claiming the old
files were read or had zero history on the hosted runner.

The approved state digest is
`50edb877d1db2b20865dc322221fddcc860251023a0e2e7c0b25f5a497d586a0`.
It retains 168 conservative timing observations (not 168 requests), all known
MAX constraints, unknown historical rate state and matching owner settlement
proof. No observation normalization or schema-bound increase was needed.
Production execution and website delivery require separate maintainer review
and verification after the working PR; no production success is claimed here.


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
