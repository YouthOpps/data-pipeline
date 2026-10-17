# INSAIT public opportunities

This standalone standard-library adapter discovers current programme and call pages
from INSAIT's Join INSAIT navigation and both publishers' advertised sitemap trees.
It reads the selected pages on every run. New programme routes and editions are
eligible for discovery; changing text, links, menus, footers and sitemap timestamps
do not require updating a stored content hash or a record inventory in code.

## Collection and scope

Official starting points are [Join INSAIT](https://insait.ai/join-insait/),
[INSAIT's sitemap](https://insait.ai/sitemap_index.xml) and
[EXPLORER's sitemap](https://explorer.insait.ai/sitemap_index.xml).
The programme navigation supplements entries missing from the sitemap. Discovery
includes scholarship, fellowship, internship, research-programme and recruitment
routes, then validates each page's main content and programme title. Person
directories, alumni/results stories, categories and application forms are excluded.
An explicit no-open-positions message contributes no record. SURF announcement
news for the edition on the canonical SURF page is deduplicated against that page.
A missing page, incomplete HTML, ambiguous title or
exceeded traversal limit fails collection rather than publishing a partial result.
New publisher URL conventions may require extending the discovery rules.
Publisher-advertised English language alternatives are followed and deduplicated;
new Bulgarian or EXPLORER programme routes are still eligible for discovery.
News requires an application closing or an explicit invitation to apply; generic
programme publicity and recipient stories do not create duplicate opportunities.
Unrecognized opportunity categories remain `other` rather than stopping updates.

Titles, short factual excerpts, publication dates and explicit application closing
dates come from live HTML. Navigation and footer content do not supply record
facts. Unsupported or yearless closing dates remain null. English dates with an
explicit year are parsed only in application-closing context; EET/EEST/UTC times
preserve their offsets, while date-only closings retain date-only precision.
Multiple different closings are left unknown rather than reduced to an invented
single date. A forthcoming programme remains unknown, even if older dates appear
elsewhere on its page. Eligibility countries are not inferred from language,
citizenship or residence wording. Host countries require source evidence.

Existing programme identities retain their published keys; SURF and EXPLORER
edition keys are derived from current source years. Newly discovered programmes
use their canonical publisher URL. Successful publication preserves first-seen
timestamps, updates changed record content and replaces this source's inventory.
Records no longer discovered are removed on a successful complete collection.

Copyright and cookie notices do not establish an open content licence. Output
contains brief factual excerpts, attribution and original links, not complete
articles, images, application forms or applicant information. Public robots policies
are checked on both origins. Supporting PDF bytes and unrelated contact-link
encodings are not frozen prerequisites for reading current HTML opportunity facts.

## Verification and publication

Run `python -B adapters/bg-insait/test_adapter.py` for the sole nonpublishing
live collection test. It validates every record and prints the complete result
to stdout. Record counts are dynamic. Publication is performed only by the
dedicated workflow with
`python -B adapters/bg-insait/adapter.py --publish`.

Both origins share one durable publisher family and collector lock. Issue #336's
HOSTED marker and shared seed are bound to ROOT public purpose 6223222979 after
independent verification of the complete genuine native run and retained MAX.
Mature LOCAL remains unreleased; its historical finite absence capability is
unchanged. No repeat native collection or mature LOCAL reentry is authorized.
The approved prospective HOSTED transition and remaining production/data/site
verification gates are described below.

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
access; restrictive rate and delay directives remain effective. The identity alone does not relax
pacing, backoff or durable budget identities. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **04:33 UTC** (`33 4 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

The navigation hub and every advertised sitemap are inventory inputs. Their
physical requests, redirects and original conditional retries now use at least
30 seconds after the preceding inventory response completes. Other publisher
requests use at least 12.2 seconds after response completion; all share at most
five starts per rolling 60 seconds. Stronger stored restrictions take priority.
Discovery and the cumulative programme/alias traversal remain complete.

## Workflow concurrency

`youthopps-insait-publisher-v1` serializes runs of this source. Up to 100
pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **INSAIT** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

### Historical journal integrity maintenance

The following describes the prior #295 IO-only stage. The subsequent #336
protocol and budget changes are described in the current stage below.

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

## Offline modern pacing implementation — issue #336

This stage preserves the complete dynamic graph: Join INSAIT navigation, both
advertised sitemap trees, all selected opportunity routes and English language
alternates. The original 64 sitemap, 50000 selected-route and cumulative 100
programme/alias bounds remain. There is no new aggregate request cap or known-ID
stop. Six-hop redirects, the original two-attempt HTTP 503 behavior, 30000000
bytes per response and default 15-second GitHub requests remain. Ordinary
connection failures still receive no retry. Publication retains three no-force
CAS attempts, source-only writes and last-good failure handling.

A received 429 persists doubled CURRENT physical and inventory intervals, the
next observed strike and the maximum of existing holds, 1800-second exponential
backoff and any longer valid Retry-After before reading any body. It then safely
closes the response and stops this collection. Failed persistence or any close
exception leaves the transaction unresolved and the runtime gate sticky. No
same-collection retry or backoff reset is permitted. Genuine content 401/403/451
and primary challenges retain a durable hold. Literal REP 4xx except 429 may be
unavailable without inventing a global content ban. An older scoped target denial
survives unavailable, stale or inapplicable policy; fresh applicable permission
may supersede that decision while preserving its historical witness and all
stronger numeric constraints. Content holds never clear through REP permission.

REP selection intersects exact current-specific groups (or wildcard if absent)
with separate exact legacy YouthOpp restrictions. Decimal delay/rate constraints
are assessed durably. Policy redirects retain literal robots.txt purpose; an
in-progress origin guard prevents recursive policy dependencies. Every target,
including cached decisions and redirect targets, is assessed under a durable
owner. The last exact current receipt must be committed before that owner can
settle. Historical allowed witnesses are nonauthoritative canonical-min samples
per origin, never cached permission or settlement authority; scoped denials and
all restrictive MAX/unknown flags survive joins.

The modern schema is 3, with durable in-flight/owner and pending-policy fields,
physical/inventory completion bounds, current gaps, strike/embargo provenance and
exact settlement receipts. Policy tuples omit only origin/target information
reconstructed from their map key; actual receipt fields retain raw-body hashes.
The original 16384-byte state/envelope and 32768-byte archive bounds remain.
A full ordinary 167-assessment allowed graph fits without retaining every allowed
sample. Denials remain lossless by scoped target; accumulated denial history can
exceed the byte bound and then fails closed, rather than dropping evidence or
raising a bound. This is not a lifetime-capacity or actual-frontier fit claim.

The existing retained-descriptor journal generation and original-inode cleanup
remain. In-place interrupted writes can sacrifice availability; empty, partial,
missing-mature or pending state refuses restoration, including an older owned
artifact in a fresh process. Advisory locks do not authenticate arbitrary same-UID
writers ignoring locks. Three distinct literals separately gate mature LOCAL,
HOSTED and the reviewed full legacy seed. The HOSTED marker and shared seed are now bound to ROOT public
purpose 6223222979; mature LOCAL remains None. A fourth historical capability
separately gates one exact prospective first LOCAL initialization. ROOT
read-only evidence observed the family absent, not an empty or settled historical state;
the authenticated old six-field artifact and all nine exact terminal jobs are
retained separately. No generic missing-mature reset or historical bootstrap-env
fallback is added.
Schema 1 migration needs exact public owner/digest/producer/history and LOCAL
inode/raw pins where applicable, explicit full timing-ledger age-fold approval
and retained unknown rate/policy/completion ancestry. Legacy pending absence is
not observed settlement. Existing holds cannot be erased by migration.

A complete fresh workflow-scoped history authenticates every exact attempt/job,
actual completed_at chronology, newest owned artifact, head and immutable producer
source. Queued/active conflicts, incomplete jobs, missing/expired newest artifacts
or changed prefixes refuse; no older fallback is used. LOCAL additionally requires
the matching workflow paused before namespace access. Genuine hosted context uses
the actual job/ref/head and configured GITHUB_TOKEN. Fresh ownership is checked
again under both held locks before initialization, and after startup quiet before
first wire. Initialization persists a prospective pending owner and verifies its
matching commit before clearing it. Startup quiet is at least 60 seconds.

The approved advance allocation is source/prepublication 4200 seconds, including
restore and the original publication snapshot, then publication 180, failure 180
and export 30 within a nonrenewing 4590-second whole run. The workflow budget is
85 minutes (5100 seconds), retaining 510 seconds outer headroom. Unconditional
120-second admission remains inside both source and phase limits, even with zero
wait or after fresh metadata. The ordinary upper-frontier model is not an actual
count: its nominal 3344.4 seconds leaves 855.6 seconds before unmodeled overhead.
The permitted redirect/retry amplification cannot be guaranteed to fit; timeout
fails the complete collection and preserves last-good data, never a partial graph.

Long hosted publication requires complete WEBSITE_APP_ID,
WEBSITE_APP_INSTALLATION_ID and WEBSITE_APP_PRIVATE_KEY configuration before any
publisher request, even when DATA_SOURCE_TOKEN was initially supplied. The actual
workflow passes these fields explicitly. Each GitHub publication operation checks
cached token expiry and lazily renews a data-source-only contents-write App token
with a five-minute reserve, including the initial snapshot, late publication,
failure handling and CAS retries. Minting consumes the existing current phase.
The workflow's initial token and historical bootstrap environment remain present;
the latter does not authorize a modern transition. External static compatibility
requires explicit INSAIT_STATIC_TOKEN_EXPIRES_AT, is forbidden for hosted mode,
and adds a shorter nonrenewing expiry-minus-300 deadline; it cannot claim the full
hosted allocation. No tokens, private keys or JWTs enter pacing state or output.

All 24 record fields, titles, IDs, category and language rules, direct factual
excerpts and expiry semantics remain. Changed updated_at and last_seen use entry
attempt; last_checked uses collection end. Date-only cleanup waits through UTC-12,
and remove_expired_records receives the original ISO timestamp contract. The sole
original live unittest is unchanged. Its accepted genuine baseline run emitted
nine records, seven current and two actually expired, with 39 publisher openers
and 277 requested journal saves. The final HOSTED binding changes only two
literals; no repeat native run or mature LOCAL reentry is authorized. Private
controls alone do not establish native facts or current consumer acceptance.

## Finite prospective LOCAL capability: offline implementation only

Issue #336 offline implementation approval 6216075303 implements the reviewed
finite design; it grants no actual initialization, age fold or publisher access.
At that offline implementation stage, all four literals remained None.
Executable binding then still required the exact reviewed known clauses and
fresh ROOT public purpose, unknown-ancestry, age-fold, ancestor, nonce and
claim-absence clauses. Those prerequisites applied before executable
binding/access, rather than preventing the default-null offline implementation.

The retained full ledger has 19 typed observations: ten legacy retained starts
and nine actual job-completion upper bounds. These are not 19 requests. All 19
are strictly older than the explicit proposed observation-minus-60 cutoff; no
blind timing tail is discarded. The prospective 572-byte seed has SHA256
79bab972f66c2ec1eabaf0ac41807dcae59ba9bf2ffdca7668b1c8ed4451cdf2.
Its three unknown flags remain true. Prospective false pending and empty policy
maps establish no historical settlement or absence of throttling. The original
six-key envelope lacks head/source hashes; immutable producer and authenticated
run/job/artifact bindings supply separate evidence without invented fields.

Before creating the family, initialization exclusively creates an owned 0600,
source-specific nonce claim outside the removable family leaf under retained,
exactly pinned original /tmp ancestors. The claim binds the complete public
marker, seed, owner, history and absence observation. File fsync, complete
readback and ancestor fsync precede exclusive family mkdir. A present or partial
claim consumes the grant; family-leaf deletion cannot reuse it. No cleanup removes
the claim or repairs an interrupted/partial family. This protection assumes the
outside claim survives; it does not authenticate arbitrary same-UID deletion.

Both genuinely new exclusive locks must be held before unconditional fresh
paused/no-active history and exact owner/producer verification. Initialization
then writes a unique pending owner and only its matching verified settlement.
Partial, empty, existing or mature directories are never adopted. At least 60
seconds of startup quiet and a fresh exact owner/ancestor/claim check precede
first wire. Claims remain held and checked through that boundary. LOCAL never
exempts a running Action as its native test; HOSTED retains only its separately
authenticated exact current-job allowance and cannot use this fourth capability.

Private mapped-path ordinary controls exercise the actual capability and journal
functions with synthetic metadata, real private locks and two commits, including
cold same-grant refusal after private leaf deletion. They neither read an actual
product namespace nor replace the unchanged sole genuine live test. Public ROOT
prospective purpose/agefold and executable binding, observer readiness, current
consumer proof, actual facts and complete native fit remain separate gates.

## ROOT finite LOCAL purpose boundary; native execution still unreleased

ROOT public issue #336 comment 6216672353 now approves prospective first LOCAL
initialization, all three legacy unknowns and the explicit strict 19-point age
fold. At that earlier boundary, only the fourth LOCAL absence literal was
bound and the mature LOCAL, HOSTED and legacy-seed literals remained None.
The later HOSTED/shared-seed binding is documented below; mature LOCAL remains
None. All implementation functions, the original test, workflow
and phase/pacing limits are unchanged by this binding.

The marker exactly preserves ROOT fresh observation proof 7198c5c2 and its
ancestor/family/nonce-claim absence evidence. Its 20 keys differ from the
observed nonexecutable draft only by the public evidence permalink and three
explicit true acknowledgements. Seed79bab remains the original reviewed
prospective state; fresh helper observation is not invented
response-completion evidence. Runtime initialization must occur at or after
fresh absence time1792157537.9010937, then raises its observed_at and embargo
to actual now/now+60 before matching commit.

Private binding checks validate the exact literal and actual ROOT
nonce/ancestor values without reading or writing those actual objects. An
integrated original prepare_collection fixture projects those pins onto
private ancestors and uses a synthetic incoming legacy-state hash explicitly
in both fixture marker and baseline. This avoids reading/reconstructing old
raw state; it is not a claim that the complete genuine grant has been
exercised against actual storage. Bounded no-grant, pre-observation-clock and
hosted refusals also pass.

That historical public LOCAL purpose/age-fold boundary did not itself authorize
native execution or publisher access. The later separately released original
native run and independent evidence are distinct from this historical capability.
The HOSTED literal author has created no claim or family.


## Approved prospective HOSTED transition

ROOT public issue [#336 purpose 6223222979](https://github.com/YouthOpps/data-pipeline/issues/336#issuecomment-6223222979)
binds only `_REVIEWED_HOSTED_TRANSITION` and `_REVIEWED_LEGACY_SEED`. The mature
LOCAL marker remains None and the historical finite LOCAL-absence capability is
byte-identical. No implementation function, original live test, workflow, source
graph, record field, expiry rule or phase budget changes in this binding.

The complete retained MAX witness contains 2,616 typed observations and 97 unique
rolling points; these counts do not represent publisher requests. All 97 points
are strictly older than cutoff 1792178799.1337228, the actual observation
1792178859.1337228 minus the unchanged 60-second window. No boundary or unaged
point is removed. The complete acknowledged witness SHA256 is
24a1e1701ec2cea03ea2c862b48acfeee764413213a2e8f085b6d6a0dabf89dc;
its independently accepted full-MAX receipt is
829d48f7954a1850007216c6d8311371b3bed1f475329f208478257f39dd673e.

The source-specific eleven-key HOSTED marker separately retains the exact
original ten old-owned starts and nine completed-job upper bounds in
`age_fold.timings`: 19 points with SHA256
cb09c82b2ff046e9588a2d37873f79d7cf32dd86f085a6ac3cbe4d43eace4ebf.
The larger 97-point retained union does not replace that source contract.
HOSTED `local_pins` is None; mature LOCAL presence is not HOSTED absence.

The prospective shared seed SHA256 is
d7b8009889e399f110419eb2719657affe44e778734f467da546576e96af4b32.
It preserves every retained scalar and scoped policy constraint, the stronger
not-before embargo and all three unknown ancestry flags. Its false pending
owner and cleared current-owner receipts describe a future HOSTED INIT, not
past settlement. Runtime still authenticates exact owned artifacts and fresh
complete history, acquires genuine current ownership and its namespace locks,
commits and verifies its own pending INIT, and waits for startup quiet. Durable
content/rate holds and any present namespace conflict still refuse access.

Offline FIRST/LATER controls execute the exact decode and restore functions
with in-memory metadata and journals. FIRST uses an explicitly synthetic old
six-key schema-1 envelope; LATER uses the original nine-key schema-2 envelope
and retains stronger modern constraints. Missing public purpose, changed seed,
unknown-flag removal, pending state, wrong head/history and substituted larger
marker timings refuse. These fixtures are not an additional native run or
actual namespace operation. Exact reverse of the two literals restores accepted
source SHA256 096d4ffe7e1dd59d9f3d0dd81355445c3180182ba56c9d7ce85cda39a0c99ad4
byte for byte and as an AST. The unchanged workflow remains one daily staggered
run, 85 minutes, with its original App-token fields and source-only command.
Production artifacts, published data and the current website consumer remain
separate post-merge verification gates.


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
