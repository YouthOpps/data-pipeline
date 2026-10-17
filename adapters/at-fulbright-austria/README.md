# Fulbright Austria

This standalone standard-library adapter discovers awards from the current
[publisher sitemap](https://www.fulbright.at/sitemap) and the six Austrian/US
student, scholar and teaching-assistant directories. It follows their own
`grant-details` links and the sitemap's award pages. New linked awards are
parsed without adding profiles or approving content hashes.

## Extraction and identity

Titles, application periods, funding excerpts and eligibility come from the
retrieved HTML. Multiple application periods select the next closing date;
date-only deadlines do not acquire an invented clock. Explicit CET/CEST/UTC/GMT
closing times retain their UTC instant. Unrecognized or yearless
dates remain null. Opening dates determine open status where available; a
date-only closing day has unknown status. Historical calls stay visible with
expired status. Recipient lists, contacts and related stories are excluded.

Stable IDs retain the previous award-slug keys, including the Expert and
American Studies prize aliases. The two EU Schuman routes share an identity.
The separately described Arts Specialist track is extracted from its own live
section and retains its existing ID. New award slugs generate deterministic
IDs. Existing publication logic preserves first-seen/created timestamps and
advances updated-at only when record content changes.

Countries are restricted to the direction of the programme and explicit
citizenship requirements. Institutional applications do not imply individual
citizenship. Schuman destinations are not expanded into an invented country
list. US teaching-assistant employment remains `jobs`; host projects are
`institutional-grant`, and the thesis prize is a competition.

## Access and limits

The [imprint](https://www.fulbright.at/about/imprint) permits attributed excerpts
apart from multimedia/software and prohibits framing. Records use short factual
excerpts, publisher attribution and official provenance links; no photographs,
recipient biographies, contact information or application data are collected.
The [privacy policy](https://www.fulbright.at/about/privacy) is not an open
content licence. External application portals and provider catalogues are never
crawled or submitted.

The old hard-coded profiles, content hashes and PDF/ZIP byte snapshots have
been removed. This collector extracts HTML, not PDF/ZIP contents. A condition
or opportunity published only inside an attachment is not extracted. Linked
HTML is the supported inventory, not a claim that every attachment or external
catalogue has been parsed. Summaries are bounded excerpts; the linked publisher
page remains authoritative for full conditions. Structural, transport and
record-validation errors still fail the collection instead of emitting an
empty successful publication.

Requests use `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`, normal inherited
TLS/proxy verification, and the stricter current/legacy/wildcard robots rules.
Each physical publisher attempt waits at least 12.2 seconds after the preceding
attempt completes, with at most five starts per rolling minute. Every physical
sitemap or programme-directory inventory attempt also waits at least 30 seconds
after the preceding inventory attempt completes, including redirects and bounded
retries. Award detail requests keep the ordinary physical budget. Literal robots
reads use that physical budget independently and remain cached per origin,
matching the original collector. Separate machines must not overlap.

## Run and publication

`python -B adapters/at-fulbright-austria/test_adapter.py` runs the sole live,
nonpublishing test. It validates every actual record, unique IDs and source
provenance, then prints the complete JSON collection to stdout. Consume that
output in memory; no fixed count or snapshot is an acceptance condition.

The existing source-filtered main-branch workflow, manual dispatch and schedule
are unchanged. Its only application command is the adapter with `--publish`.
Publication requires `DATA_SOURCE_TOKEN`, atomically updates only this source's
data/metadata pair, preserves last-good data on failure and rejects a concurrent
newer same-source snapshot. No production execution is part of local testing.

## Expired-record cleanup

Fulbright Austria preserves explicit CET/CEST/UTC/GMT closing clocks as aware
instants; unzoned calendar closings remain date-only and unclear windows remain
null. This cleanup uses those application fields without interpreting award
periods as deadlines.

The complete fresh inventory is fetched and validated before cleanup. The next
source-scoped atomic data/metadata publication replaces the prior dataset rather
than merging its rows, so expired previously published records cannot survive
that successful replacement. Current records and records with missing or
ambiguous deadlines remain eligible; status alone does not determine expiry.

Offset-aware deadlines expire only after the stated instant. A date-only
deadline with no documented publisher timezone remains eligible through that
date in every civil timezone: cleanup uses the conservative UTC-12 calendar
boundary, without guessing a publisher timezone or an unstated closing hour.
The source recipes and their original deadline fields remain unchanged.

A valid nonempty fresh inventory may produce an empty cleaned dataset; that
empty result is published atomically with success metadata. An empty upstream
collection still fails validation. Fetch, parsing, validation and publication
failures preserve the last-good dataset (including a prior empty success) and
success timestamp; cleanup never performs a separate deletion or writes another source.
The sole live test validates the complete nonpublishing inventory, including
closed records needed to verify source completeness; publication alone applies
the deadline filter after that validation.

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

The scheduled refresh runs once daily at **08:17 UTC** (`17 8 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force; this source changes only its
daily automation schedule.

## Workflow concurrency

`youthopps-at-fulbright-austria-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Publication error diagnostics

Failed GitHub publication reports retain the HTTP method, a recognized API
route without its query, status and bounded attempt count. If checking the
remote outcome also fails, both failure contexts are retained. Diagnostics do
not include credentials, headers, response bodies or signed URLs. The existing
three-attempt limit, current-head and source-pair conflict checks, atomic writes
and successful collection/publication behavior are unchanged. This diagnostic
maintenance does not establish the underlying cause of older generic failures.

## Source display name

Published metadata uses `display_name` = **Fulbright Austria** for the visible
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


## Durable pacing migration and evidence gates

The daily UTC schedule remains `17 8 * * *`, with the existing serialized
30-minute job and source-only publication token. The original source had no dedicated collection or whole-adapter phase cap.
The revised candidate freezes at most 1,200 seconds for source I/O/wait admission
and 1,740 seconds for overall admission, further clamped by the original exact
hosted job proof. These are new stricter allocations within the unchanged job,
not proof of complete fit or an uninterruptible parser wall-time guarantee.
Publication, failure handling and export retain separate 180/180/30-second
reserves. Slow restoration or initial snapshot work reduces the source
allowance rather than adding another full 1,200 seconds.
The original full sitemap, six direction/category directory patterns, all
`/programs/us-eu-*` directories, every discovered award and grant detail, Schuman
identity exception and derived arts track remain supported without a fixed
frontier or record-count cap. Sitemap and hub calls explicitly carry inventory
purpose; a detail under a similar URL prefix does not acquire a page delay.
No verified change feed establishes complete additions, edits and removals, so
full reconciliation remains necessary.

Schema-3 family state is stored in a private, verified directory outside the
checkout, with stable state and collection locks. Every publisher or policy
transaction durably records pending ownership before transport or restrictive
assessment. Only successful durable assessment and its matching completion
settle it. Any reservation, numeric/policy assessment, restriction or completion
write error is sticky; safe current-inode invalidation is supplementary, and an
older owned ZIP cannot clear unresolved local state after restart. Parent
identity, regular-file ownership, mode, links and nonblocking FIFO refusals apply
to the journal and owned artifact paths.

A 429 stops the current run before any unchanged retry. It durably doubles the
current physical and inventory gaps and retains at least 1800 seconds of
exponential cooldown or a stronger valid Retry-After. Invalid, stale, malformed
or nonfinite Retry-After alone creates no hold. Content 401/403/451, a genuine
visible challenge or explicit robots refusal retain a hold. Literal robots 4xx
except 429 mean unavailable REP; content is never relabeled as robots across a
redirect. Real ordinary no-response connection retries retain the original two
attempts/four-hop bound, while unread responses and unassessed policy failures
remain pending. GitHub publication keeps its separate original six-second
spacing, Retry-After, bounded jitter and publication-window proof.

Read-only legacy recovery found `/tmp/at-fulbright-austria-pacing-<uid>` absent.
That is not zero history or permission to bootstrap. All 28 exact historical
attempts and executed jobs were inspected; their artifact inventories were
empty. Their actual job completion times provide conservative prior upper
bounds. Lost publisher starts, headers, prior rate events and possibly
underlying collection errors remain unknown. A new strike counter zero means
only no newly observed modern 429. `_LEGACY_TRANSITION` stays unset until the
maintainer approves the exact typed unknown-history state and historical pins.
Any newly appearing legacy publisher entry or changed family history requires
fresh review. The LOCAL workflow must be disabled with no active family runs
before restoration, namespace creation and startup quiet, and is rechecked
immediately before first wire. Hosted restoration requires the exact current
run and newest completed attempt's owned artifact, immutable head/branch/jobs
and digest; no older fallback is allowed. Production needs its separate exact
full-native MAX maintenance approval.

The latest three historical failures expose publication outcomes, including
GitHub PATCH 422 on the newest run, and do not prove publisher denial. The latest
successful collect-and-publish step took 350 seconds and published 13 records;
that is not a physical-frontier count, new-pacing measurement or current native
acceptance. Historical frozen-frontier parse and connection-timeout failures
remain distinct. The publication implementation is preserved rather than
speculatively changed in response to these older outcomes.

Owned export pins the checked directory and safe output inode through readiness
and healthy context completion. On failure the pinned safe output is invalidated
before independent best-effort false signaling and unlink; unrelated or unsafe
replacement objects remain untouched. Private job execution and `RUNNER_TEMP`
are trusted against an independent same-user process forging replacement
artifacts after true readiness while all subsequent false writes fail. Under
that composed boundary case export raises and the original is invalidated, but
an unrelated forged envelope and previous true can remain. This is not an
absolute upload guarantee against a same-user writer, and it does not justify
removing legitimate 429 failure-state artifacts.

Offline private fault, parser-routing and real-clock controls do not establish
publisher access or complete collection. Acceptance still needs one separately
released original full native test, every actual input/header/body and saved
state retained in private RAM, exact current three-file consumer validation,
source date-precision review and independently reviewed full known-state MAX.
No production or extra publisher diagnostic is authorized by this migration.

Stronger retained intervals, repeated 429 slowdowns or a larger complete frontier
may exceed the unchanged collection/job limits. Such attempts stop and preserve
last-good data; this migration promises neither automatic post-429 completion nor
a fixed record count. It does not raise caps or omit discovered awards to fit.

## Revised allocation and first failed native evidence

The first complete original test attempt genuinely failed after 811.619 seconds.
It received 43 complete responses (one robots 404 and 42 content 200), including
the sitemap, all seven hubs and 34 of 45 discovered award pages. Eleven award
pages remained unfetched. No complete normalized JSON or current-consumer PASS
was produced. This was the candidate's remaining-phase admission guard, not
a publisher refusal or rate limit. Its original failed receipt, partial RAM
evidence and complete known state remain preserved. The provisional 900/1290
limits were introduced only by the unmerged candidate; their correction does
not change the baseline's 30-minute job cap or omit discovered programmes.

[Maintainer-approved offline allocation correction](https://github.com/YouthOpps/data-pipeline/issues/287#issuecomment-6205388964)
keeps the full 54-request nominal graph and all existing fields. The fixed whole
end is at most entry plus 1,740 seconds and the original job read deadline.
Nonexport work ends at least 30 seconds earlier and no later than the original
job write deadline. The exact job proof has at most 30 seconds; restoration and
the initial snapshot each have at most 180. Source retrieval ends no later than
the whole end minus 390 seconds, reserving publication 180, failure 180 and
export 30. Publication never consumes the failure or export reserves. All phases
share entry-frozen ends, cannot renew, and may have less than their nominal
maximum when setup or stronger retained constraints consume headroom.

The original publication bodies remain intact behind source-local deadline
dispatch. A copy of the authentic job/token window is further tightened to the
active phase; it is never rewritten or extended. Original GitHub six-second
pacing, Retry-After, bounded calls, CAS jitter, three attempts and terminal
write latch remain unchanged. Ordinary CAS verification snapshots stay in the
success-publication phase. A failure owner begins before the first fallback
snapshot, or before a source-failure metadata-only publication, and stays open
through subsequent reads, metadata writes and verification until the original
run body exits. It receives one fixed deadline, never one allowance per call.

A competing whole-process alarm previously prevented the original publication
call guard from entering. The revised admission model removes that conflict.
Complete physical publisher and workflow/artifact operations retain a total
30-second guard clamped to their phase, sharing a fixed end when nested. The
original publication guard retains its own bounded timer; export has its own
remaining 30-second allowance. Socket, CPU or filesystem stalls can still force
a safe failure or prevent export under the original hard outer job bound.
Neither static allocations nor the first partial retrieval prove that the full
new graph fits; a separately approved complete original test remains required.

The current mature schema-3 journal and stable locks must be retained. The
failed attempt's full known MAX contains 243 conservative timing observations
and 768 constraint witnesses, including 43 actual publisher requests and 84
logical owner settlements; observation counts are not request counts. No
missing field is converted into known zero history. A new exact maintainer
bridge and a separate mature-state observer/release are required before another
native attempt. This offline revision does not authorize such execution or
production, and does not replace the preserved failed receipt with a PASS.

The publisher journal is rewritten through one retained, locked read/write
descriptor. A changed parent, name, inode or generation closes access rather
than adopting or overwriting a replacement. This deliberately favors safety
over crash availability: interruption during an in-place rewrite can leave an
empty or partial journal. A later process then refuses collection and older
artifacts cannot substitute for that invalid local state; operator review is
required. Cleanup targets only the retained original descriptor.

The revised external test observer starts a detached, private twelve-hour RAM
keeper before the original test and streams captured request observations,
response headers, returned body chunks, available error partials, output and
journal-save observations as they arrive. If the controller dies, evidence
already delivered to the keeper remains available. This does not recover
bytes that the transport never returned or observations never delivered to
the keeper. This revised observer has private fault-control qualification;
at that offline stage it had not run another publisher collection.

Root approved the exact mature-state bridge for a separately released revised
original test: https://github.com/YouthOpps/data-pipeline/issues/287#issuecomment-6205971447.
It retains the first failed attempt's full known constraints and 243 timing
observations (not 243 requests), with unknown earlier history still explicit.
The original 43-response attempt remains incomplete and failed; this bridge
is neither a successful collection nor permission to launch another test. The
existing mature journal and locks remain unchanged, and production readiness
requires separate approval after complete full-graph evidence.

The revised genuine original test passed on source `e1d28abb`: all 54 actual
responses were retained, with 45 raw awards and 13 after expiry filtering.
Subsequent exception/REP-only repairs preserve that attribution: typed
pre-response network faults and temporary DNS retain the original two
attempts; storage, TLS, permanent DNS and opaque errors fail closed. Current
bot rules select their own specific group or wildcard independently of
retained legacy-specific restrictions. Private counterexamples qualify those
branches; the actual native REP was 404, so no live suppression of publisher
policy is claimed. Full retained-body/fact/consumer replay qualifies reuse,
without another publisher collection. The earlier incomplete attempt remains
a separate failure. Production approval and verification remain separate.

The maintainer approved the exact production MAX state in
https://github.com/YouthOpps/data-pipeline/issues/287#issuecomment-6206546086.
Its canonical state digest is
`e8015838b2673a4ce99789e90e1f503f2e3e767bf7f64962639ba4b5957a5eac`.
The full audit retains 513 conservative timing observations across both
attempts (97 actual requests), 1,736 constraint witnesses and 190 matching
logical settlements. The unchanged 512-observation validation bound rejects
the unnormalized union. The approved state removes only observations strictly
older than 60 seconds at the mandatory quiet guard, preserving all numeric
MAX constraints, unknown history and complete audit provenance; it does not
truncate by count or increase the schema bound. Runtime startup quiet and
strict newest-owned/local restoration still apply, including stronger holds.
The initial incomplete test remains a failure; the revised complete test
passed at its original E1 source. Private conditional repairs are qualified
by the complete retained-response replay, not another live crawl. This
approval configures production recovery; production and website delivery
are verified separately after the maintainer's merge.


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

Historical native evidence remains attributed to source `e1d28abbbbefb56173f6d886d7b836854e72e4218b2c68ca383529dbfb0d826d`
and its [public qualification](https://github.com/YouthOpps/data-pipeline/issues/287#issuecomment-6206546086).
The present IO-only correction preserves all other decorated function AST and
module constants. Original raw inputs are no longer available here, so these
public historical qualifications and private IO controls do not establish a
fresh native PASS, current full-input replay, or current publisher facts.
