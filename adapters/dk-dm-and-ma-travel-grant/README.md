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

Both language versions supply the explicit application CTA window,
1 November 2026 to 1 February 2027. Beginning-of-clause “Apply here during the
period” and “Ansøg i perioden” labels feed the existing `application_windows`
and `deadline` fields. Application opening does not become an event or programme
start date. Yearless recurring examples do not invent years. Relative instructions such as applying a month before an event
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

## Durable pacing and historical evidence

The #291 migration introduced schema3 pending ownership and the original
source-local pacing namespace. Its earlier native result remains attributed to
source `9cebb8cb` and
[its historical qualification](https://github.com/YouthOpps/data-pipeline/issues/291#issuecomment-6207347601).
That original first-hosted bridge is retired. The exact #408 continuity chain
and modern owned-artifact contracts below govern the present implementation.

The complete bilingual discovery remains mandatory, with a150-page overflow
failure and no partial publication. A durable pending owner covers physical
access, robots and cached restrictions, redirects, body assessment and policy
checks. Unknown or failed persistence cannot be repaired by a later completion
or an older remote artifact. The original journal generation and both lock
descriptions remain authoritative across reads, writes and cleanup.

The exact newest completed attempt, immutable main head, actual jobs, artifact
owner and archive digest govern restoration. Any unknown missing artifact or
nonterminal sibling refuses; there is no general older-run fallback. LOCAL
mode requires the workflow paused and an already established owned journal.
Hosted publication proves its original token/job window before restoration,
then reserves bounded snapshot, collection, publication, failure and export
phases. The existing30-minute job,1200-second collection,1740-second run and
30-second export limits remain unchanged. Each operation includes its full
body and close under a bounded deadline.

## Received-response constraints (#408)

The historical header-only stop from #328 is retired. A received429 first
persists doubled **current** physical and inventory gaps, the next strike and
at least the exponential1800-second embargo, before header/context handling.
All supported Retry-After fields contribute their maximum; unsupported positive
or ambiguous constraints keep the request pending. The bounded response body
is assessed for a genuine visible challenge before settlement, and a challenge
retains its separate durable access hold. Known content401/403/451 also retain
that hold before response entry or header parsing. Literal robots-only4xx
unavailability keeps its existing distinct purpose policy.

A200 robots response requires explicit text/plain, valid plain REP structure,
and supported timing declarations. HTML fragments, malformed rules or unknown
numeric bounds remain pending. Ordinary HTTP chunked transfer remains supported
through the actual standard-library decoder. Ambiguous Content-Length/transfer
coding, unsupported coding, incomplete bodies and failed closure cannot settle
healthy. Operation and publication deadlines include bounded cancellation of a
close that stalls while unwinding a body timeout.

Verified pending journal bytes retain stronger acknowledged constraints on an
unassessed response failure; this never adopts a replaced generation or heals
an unresolved owner. Actual persistence and authority failures remain sticky.

### Acknowledged journal mutations (#295)

A dirty write no longer authorizes later writes or failure invalidation by
itself. Each successful truncate, partial write, and fsync receives a stable
readback matching the exact expected partial bytes. The next mutation checks
the last acknowledged full metadata generation. Failure cleanup also checks
that generation through the original writable descriptor, preserving an
external stronger same-inode state. The existing 16 KiB journal bound remains.

These are source-local IO fences, not filesystem compare-and-swap. A failed
own partial write can leave an unavailable journal; the run fails closed.
Normal policy, pending/MAX state, retries, discovery, parsing, identifiers,
dates, expiry, publication, tests, and workflows are unchanged. Validation
uses isolated actual IO functions with denied networking and explicitly
synthetic admission metadata; it does not claim a new native collection.

Historical native evidence remains attributed to source `9cebb8cb25461c1bedf23d63b8a50d442d46037528ffa1a200f4f39f694fb7a6`
and its [public qualification](https://github.com/YouthOpps/data-pipeline/issues/291#issuecomment-6207347601).
Those earlier IO controls alone did not establish new publisher facts. The
separate #408 actual input and full native evidence are recorded below.

Snapshot reads now recheck actual acquired collection/state locks and current
parent/journal names after reading, before acknowledging a generation or
mutating state during save or cleanup. A collection lock lost during read
fails before another journal mutation. Ordinary private budget-only scopes
still work when no collection lock has been acquired; this grants no source
access and does not change the original collection/pre-opener gates.

### Journal snapshot follow-up

Each acknowledged journal generation retains its bounded exact bytes as well as
its metadata. Reads, subsequent mutations, and failure cleanup reject changed
bytes even when an equal-size write has colliding timestamp metadata. Initial
acquisition captures that byte acknowledgement; closing the journal clears it.
Snapshot reads accumulate legal short reads in two bounded matching passes,
checking authority and generation before and after every read. This also detects
a timestamp-colliding change to a prefix already read during the first pass.
The 16 KiB size bound remains; these checks are not filesystem compare-and-swap. These private
IO controls do not claim a fresh publisher collection or current source facts.

### Healthy pre-request deferrals (#408)

A fully restored, settled publisher journal may contain a valid future timed
restriction that cannot fit the current phase. Admission now records that
normal decision without reserving a transaction, verifies the retained journal
on clean lock exit, and only then raises `PreRequestDeferral`. The unchanged
export path can preserve the exact healthy state for the next scheduled run.
Neither the guard nor its unknown rate history is cleared.

Pending transactions, retained refusals, exhausted counters, journal or lock
replacement, generation changes, and failed lock closure remain hard failures.
The typed deferral is raised outside the lock only after those checks succeed;
it is not a general exception exemption in the journal context manager. Normal
admission still durably reserves its transaction before publisher access.

The original failure in run41240102691 is supported by the exact owned guard
from run41132970149 and the immutable pre-request admission callgraph. Later
missing-artifact attempts require their own exact finite continuity proof.
This correction alone does not authorize older-artifact fallback, real state
initialization, publisher requests, workflow activation or publication. The
current hosted handoff is OFF; historical and current native evidence retain
their own source/date attribution. No production or current-index success is
claimed by an offline control.

### Exact continuity and initial local state (#408)

The only exceptional legacy archive accepted is artifact13014865082 from
run41132970149/attempt1/head5fd9fa82d5d2d5f0871aba653376765a6e00c897:
625-byte ZIP SHA256
`fac722c920432ba96f19f769053b2041abce2c688e86e7c639ef21b8664350b9`.
The seventeen missing exact attempts are admitted only as a complete pinned
chain with their actual job identities, immutable source/workflow and absence
of artifacts reverified. The first original future guard exceeded even its
entire possible run budget before transaction reservation; the following
sixteen failed newest-artifact restoration. An unexpected attempt, rerun,
changed job, code, workflow or artifact refuses this exceptional chain.
There is no generic older-owner fallback. The obsolete first-hosted empty-state
bootstrap is removed; historical jobless-cancel pins remain solely for accurate
ancestry inventory.

New exports use nine exact envelope keys, binding authenticated executing
adapter bytes and a canonical state digest in addition to run/attempt/source/
head ownership. Modern restore verifies both digests. GitHub API reads cannot
redirect into a source host; signed archive reads admit only the reviewed
GitHub Azure/Actions storage suffixes without credentials or explicit ports,
strip authorization, and do not follow subsequent redirects.

Local collection requires an existing nonempty owned journal before remote
metadata restoration, retaining its original directory, file generation and
bytes across that restoration. Whole-family disappearance, empty state,
replacement, pending ownership and unsafe paths cannot create a fresh allowance.
The first local state requires a separately accepted exclusive initializer;
that is not part of ordinary adapter startup. The approved initial state retained all169
known timing values from the actual owner and every historical workflow state,
including the assessed503 guard and unknown rate history. Administrative bounds
from the seventeen no-access attempts add embargo evidence, not invented
physical starts or response-completion receipts.

The original collection/parser/identities/expiry, sole test, daily workflow and
1200-second collection/1740-second run bounds remain unchanged. Private tests
exercise the real journal, transport, framing, timers, artifact decoder/export,
complete61-run metadata and current consumer. The actual #408 full native
result is separately qualified below; hosted handoff and live-index delivery
still require their own acceptance and observed production result.

Literal robots responses retain their status-only unavailable exemption, but
visible human challenges are assessed in every received body, including 3xx,
401/403/451, 404 and 5xx responses. An actual challenge persists the family hold.
The original temporary parent descriptor, path identity, owner/group and mode
remain bound throughout local state operations; replacement, symlink substitution
or metadata changes refuse. A legitimate shared sticky `/tmp` is supported
without assuming it is owned by the collector user.

If received-status constraint persistence fails before response context entry,
the response is explicitly closed under the same bounded operation deadline.
The primary failure and pending state survive a secondary close failure; no
assessment or completion is invented. The exclusive local initializer applies
the same parent metadata safety and continuity checks before any state creation.

### Actual native and OFF hosted transfer (#408)

The first original test failed in owned-artifact restoration before any
publisher request or journal write. Its unchanged3740-byte initial state and
failure evidence are retained. The observer's artifact selector was corrected;
no initializer was rerun and no history was reset.

The second original test passed on source `0dccf3dd` and observer `7642200f`:
26 actual responses,24 complete documents,177 exact acknowledged journal
witnesses and five records accepted by the current website consumer. All five
published identities were preserved. The result is independently qualified in
[the native acceptance](https://github.com/YouthOpps/data-pipeline/issues/408#issuecomment-6955295989).
No actual expired subset existed; synthetic empty readiness is labelled as such.

The third original test passed on source `38e01164` and observer `3834b840`:
26 actual responses, 24 complete documents, 177 acknowledged journal witnesses
and five current records, including the explicit travel application window.
[Independent native acceptance](https://github.com/YouthOpps/data-pipeline/issues/408#issuecomment-6956397136)
confirms the new collection output; the earlier attempts remain distinct.

The source-committed hosted floor retains 273 unique known observations:
the prior 221 plus all 26 new reservation times and 26 new wire starts. Every
journal starts array and before/after request_started value is included.
Its canonical 5693-byte state digest is
`acf2a8540990fcb91be684f12dc5d7348416f200a2b291349aadab3344871fc2`.
The old workflow environment seed is removed only after this full union is
retained. The [final producer decision](https://github.com/YouthOpps/data-pipeline/issues/408#issuecomment-6956597173)
activates `_HOSTED_HANDOFF_EVIDENCE` after independent handoff acceptance.
The real newest owned archive must still be decoded before the floor is merged.
Native evidence alone cannot activate hosted collection. Stronger incoming
MAX, pending owners, refusal holds and unknown history survive every merge.
The new immutable workflow identity is separate from the old workflow blob
used exclusively to prove the seventeen historical missing attempts.

HTTP Retry-After accepts complete IMF-fixdate, RFC850 and asctime forms.
Only valid asctime grammar implies UTC. RFC850 follows HTTP's fifty-year rule.
Recognized components of combined fields retain their full MAX, while ambiguous
combined framing remains pending. Invalid prefixes and unrepresentable positive
values cannot silently authorize earlier access. Actual native responses had
no Retry-After fields; the date correction is independently exercised offline.

Actual `main --publish` controls cover31/62/179-second restores and healthy
export under an already active parent exception. Publication proof precedes
restore. An export's own fsync or terminal file/parent close fault revokes
readiness, retains the stop and invalidates only the original output generation;
an unrelated parent exception does not poison a healthy export. Publication and
live indexing remain unverified until the separate hosted release completes.

The #408 factual audit checked all24 held documents. The explicit bilingual
application CTA changes only the existing travel record's application window,
deadline, status and summary; the five stable identities and other programme
facts remain unchanged. Footer, navigation, aside and complementary content do
not supply programme windows. Additional primary application windows are
retained together rather than reduced to an invented universal deadline.
This semantic correction changes collection output: the earlier `0dccf3dd`
native success is not relabelled as a success for the new parser. The separately
reviewed third original full native run confirms this semantic correction.
