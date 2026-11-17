# FEBA Alumni opportunities

The standalone standard-library adapter reads the official public
[FEBA Alumni news API](https://febalumni.org/wp-json/wp/v2/posts) linked to
[the publisher news site](https://febalumni.org/news/). It checks robots
before requests, spaces physical requests by at least 12.2 seconds, honors backoff
and fails on access refusals. Public access is not an open licence;
records retain FEBA attribution and original notice links rather than
redistributing article content.

The inventory is paginated in batches of 100 through the terminal short
page, with a 25-page safety cap that fails rather than publishes partial
results. Explicit application-call titles in scholarship, training,
internship and volunteering categories are included; outcomes, winners,
concluded events and unrelated news are excluded. Stable identifiers use
the source slug and canonical publisher notice URL. Published WordPress
UTC timestamps are retained when valid. Application deadlines are parsed only from explicit `краен срок` calendar
dates in current titles that establish application, enrolment or scholarship
competition context. Dates are validated, and different closing dates in one
title remain ambiguous/null. Publication age, URL date text and event dates
never establish a cutoff.
Host country BG identifies the supported local offer context; citizenship
eligibility is not inferred and eligible-country arrays remain empty.

Run from this folder:

```sh
python -B adapter.py
python -B test_adapter.py
python -B adapter.py --publish
```

The sole live test collects and validates real nonempty records and emits
the complete JSON array without publication or publication credentials.
Local collection requires an existing reviewed source-family journal. A missing
family, journal or empty journal is not initialized from an older remote owner.
First use on another machine requires a separately reviewed administrator seed
with exact owned-state provenance; there is no environment bootstrap override.
Hosted jobs retain their existing exact-owner restoration before creating a
fresh private run directory.
Production alone uses DATA_SOURCE_TOKEN, atomic source-only Git-tree commits
and nonforce branch updates to publish data and metadata together under
`datas/bg-feba-alumni/`. New records replace the prior collection; matching
IDs retain first-seen/created timestamps. Failed retrieval, validation or
publication preserves last-good data and its successful timestamp; a safe
subsequent failure records metadata only, and first failure creates no folder.

## Expired-record cleanup

### Continuity recovery (#377)

The 30 October 2026 job `124263659344` failed its GitHub deadline-context
request with HTTP 502 before restoration or publisher access. Sixteen later
exact attempts failed during restoration. Recovery recognizes only those
seventeen independently reviewed run/job/head/time identities and their
complete empty artifact inventories. One job lasted 62 seconds; its original
restoration failure, rather than a short-job assumption, establishes no access.
The exact last actual owner is `40941478211/1`, artifact `12926738749`, SHA256
`d6948a41e63b42e5a394e631d45836046405431521a8303f6958e04c67cb17e4`.

All retained restrictions, pending ownership and unknown history remain
authoritative. Omitted jobs contribute conservative administrative completion
bounds, never invented physical starts or response receipts. Missing that
actual owner cannot fall back to an older modern artifact or the retired
first-state migration. A later authenticated actual owner remains authoritative.

An enforced monotonic 60-second startup floor now supplements the existing
wall-clock pacing constraints and is checked again immediately before an
opener. Future missing-state failures qualify only with one complete failed
first-attempt `collect` job lasting strictly less than 58 seconds, a complete
zero-artifact inventory, the exact reviewed producer workflow and immutable
adapter bytes identical to the authenticated current collector. Unknown code,
reruns, long jobs, extra jobs and incomplete outcomes remain errors. Changing
the first-wire guard requires new independent proof. This is not a generic
older-artifact fallback or permission to reset established local state.

The current repair retains the existing complete API scan; no publisher-backed
delta ordering or removal contract establishes a safe early-stop boundary.
The original parser, source inventory, supported deadlines and atomic expiry
publication remain unchanged. On 16 November 2026, the original sole native
test passed with 13 genuine records from all 242 unique WordPress posts over
three pages. The actual current consumer accepted those 13 raw records, the
actual zero-record expiry-filtered pair and the valid-empty envelope. All 13
raw records had explicit past deadlines; zero current records is a successful
filtered result, not empty or failed retrieval. Complete captured-page replay,
all response bodies and all 26 persistence journals passed independent review.
Physical completion gaps were at least 12.201883815 seconds, inventory gaps
at least 30.005394673 seconds and rolling starts at most three per minute.

The original pre-access observer failure remains separate from that genuine
passing run. It used the wrong archive-limit assertion and made no publisher
requests or state changes; only the external observer was corrected before
the passing test. [Exact native and independent evidence](https://github.com/YouthOpps/data-pipeline/issues/377#issuecomment-6925959688)
records both outcomes. No production test or data-source write was used.

The #377 maintenance literal preserves the old #299 approved MAX, the actual
owned archive and all actually persisted native start points, plus the newest
settled local receipt. Its 54 retained points preserve all four native request
reservations, rather than only the final rolling window; actual opener timing
was checked separately. Stronger incoming
restrictions, pending ownership and unknown history still win through the
original merge. Publication and live indexing are verified separately after
the reviewed merge; the source stays paused until that handoff is accepted.

Supported Bulgarian application-closing title dates remain date-only without
an invented closing timezone. Other titles retain null deadlines. Current
publisher titles take precedence over stale URL text: the freshmen scholarship
notice title states 20 October 2026 although its unchanged URL includes
16 October. The OMV scholarship title states 1 November 2026. These dates
were inspected in the complete 16 November capture. No deadline is inferred
from the URL or article publication date.
Null/ambiguous values remain eligible; supported aware timestamps, if supplied
in a validated record, use their stated offsets.

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

## Reviewed editorial exclusion

The publisher's Rumen Petkov notice is an alumni interview, with the explicit
body label `Разговор с`, personal career questions and retrospective study
experiences. Its generic title phrase about an opportunity for training and
development is not an application call. Such publisher-labelled interviews
are excluded when their title has no explicit application, enrolment or
closing-call language; a genuine titled call is retained even if its body also
includes an interview. This uses current source context, never the 2023 date
or any invented expiry. The live API content field is required and validated
for candidate posts so missing body evidence cannot silently bypass this check.

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
pacing and backoff use the source-local durable family below. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **14:12 UTC** (`12 14 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Inventory page continuations and partitions wait at least 30 seconds after the
previous inventory page finishes before fetching the next page in this process.
This includes taxonomy/listing pages where applicable. The existing native
transport still enforces its physical-request allowance across retries, redirects
and detail requests, with stricter publisher limits and retained backoff taking
precedence. The page guard does not raise the durable request interval for all
subsequent article/detail requests or reset existing budget/refusal state.
Inventory scope, parsing, validation and atomic publication are unchanged.

## Workflow concurrency

`youthopps-bg-feba-alumni-publisher-v1` serializes runs of this source. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
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

Published metadata uses `display_name` = **FEBA Alumni** for the visible
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

## Durable pacing migration (offline candidate)

The source-local schema-3 journal retains publisher constraints across runs.
Every physical attempt waits at least 12.2 seconds after the previous physical
completion, with at most five starts in a rolling minute. Each actual posts
inventory attempt, including its redirects and retries, additionally waits
30 seconds after the previous inventory completion. Literal robots requests
use the physical allowance; this does not impose 30 seconds on every request.
Stronger restored intervals and applicable REP timing remain effective.

A 429 stops this collection without a same-run retry, doubles both current
physical and inventory gaps, and retains at least the exponential 1800-second
cooldown or a longer valid Retry-After. Invalid, stale or nonfinite header
values alone create no permanent refusal. Actual content refusal or visible
verification remains held; literal robots unavailability is separate.
Pending ownership is durably written before transport. Any restriction,
policy-calculation or persistence failure remains sticky, including across
completion cleanup; unsettled restored ownership refuses further requests.
Writes retain the verified original journal inode and update it in place.
An interrupted rewrite may leave an invalid journal, which later attempts
refuse even if an older artifact exists. This deliberately trades crash
availability for preserving restrictions; it does not reconstruct a fresh
budget from missing or corrupt state. Recovery needs a separately reviewed
known-MAX boundary. Unsafe or unrelated replacement objects remain untouched.

Hosted runs require exact current-job/head proof and the newest exact owned
artifact, with no fallback to an older successful run. LOCAL runs require the
workflow paused and no active family attempt, restore fresh history before
any source request, and recheck the pause after startup quiet. Missing old
RAM-only budgets and absent artifacts do not establish a zero-rate history.
An exact reviewed transition with historical job upper bounds and unknown
legacy constraints is required. Its exact LOCAL marker is now approved and
bound as described below; separate native and production releases remain
pending at that offline stage. The genuine native and subsequent pure-date
qualification are described below; production remains separately gated.

The existing 30-minute job remains unchanged. New source-I/O admission of
1200 seconds and whole-run admission of 1740 seconds are stricter allocations
within that job, dynamically clamped for current-job proof and nonborrowable
publication, failure-reporting and export reserves. They do not guarantee that
a large or slow catalogue fits. Original GitHub retry/CAS semantics and
publication bodies remain unchanged; no competing outer publication alarm
extends their existing limits.

Both completeness guards remain strict: the complete posts sequence
must reach a short page within the original 25-page bound, and eligible raw
records must be nonempty. A full 25th page fails rather than truncating.
Only successful expiry filtering/publication may yield a valid empty result.
The API content field remains necessary for editorial exclusions; full post
text is not copied into the normalized summaries. No safe incremental stop
is inferred from an old record or article date.

The future original live test requires a separately approved exact code,
legacy state and fresh paused-family boundary. Full actual response headers,
bodies and available partial bytes, stdout and every attempted/persisted state
must remain in private RAM. Any first failure retains diagnostics for root
review; another complete attempt requires a separate explicit release. There
is no automatic full-test retry or authorization to activate this workflow.

The maintainer approved the exact initial LOCAL bridge in
https://github.com/YouthOpps/data-pipeline/issues/299#issuecomment-6208089574.
It retains all 28 actual completed-job upper bounds and unknown legacy RAM
history; the cancelled jobless attempt has no invented request timestamp.
This binds the reviewed transition only. The first complete original test subsequently ran under the separate execution
release linked below. This LOCAL approval alone did not authorize it.


## Genuine native and retained-response deadline qualification

The separate execution release was
https://github.com/YouthOpps/data-pipeline/issues/299#issuecomment-6208150769.
The original, unchanged test passed on source `e0c7ca3a`: four complete HTTP
200 responses contained robots plus three WordPress pages of 100, 100 and
39 posts, matching the advertised 239 posts and three pages. It produced
13 eligible raw records and five after the original expiry filter. Actual
completion gaps were at least 12.255 seconds, inventory completion gaps at
least 30.207 seconds, and at most three requests started in a rolling minute.
All headers, bodies, stdout and 26 exact persisted-save pairs remain private
RAM evidence. No normalized dataset was saved for this review.

A pure deadline correction was then qualified offline against those same
239 posts and all 13 original records, without another publisher collection.
Explicit full-year application/registration closing clauses now supply three
previously missing calendar dates: 30 June 2026, 17 August 2026 and
27 November 2022. Spaced publisher digits and Bulgarian month names retain
the actual year. No local timezone or closing instant is inferred. Every
recognized closing clause must parse; an additional malformed, unsupported
or invalid clause leaves the deadline unknown and forbids closed-conflict
exclusion. Event or interview dates alone do not establish application expiry.

One post has conflicting explicit closing dates: its title says 20 May 2024
and its body says 20 May 2025. The correction does not choose a year. It
excludes the normalized post only when all supported conflicting closing
days have certainly ended under the existing UTC-12 certainty rule. A future
or uncertain conflicting clause retains the record with an unknown deadline.
The full original post and the original 13/five outcome remain in the private
native evidence. The qualified correction yields 12 normalized records and
one current record; its 11 actual expired records filter to a valid empty
subset. The current consumer accepts the complete 12/one/zero cases.

The collector still requires eligible raw calls and a complete short terminal
page within the original 25-page bound. If every eligible call is excluded
only for confirmed, conflicting dates that are all past, an explicit typed
collection-local proof carries the page counts, eligible count, original
closing clauses and aware reference clock. Initial validation rechecks that
proof before accepting the empty result; the unchanged publication body can
then publish an empty success. An empty catalogue, unproven empty result,
missing page or full 25th page still fails. No new record fields or hidden
global record map carry this evidence.

The genuine native pacing state is proposed for a separately approved
production handoff, retaining all known historical constraints, four matched
request-owner settlements and unknown legacy rate history. The pure date
qualification does not claim another native run or authorize production.


The maintainer approved the exact full-known production MAX in
https://github.com/YouthOpps/data-pipeline/issues/299#issuecomment-6208380160.
The workflow binds only that four-key approval envelope. It retains all 48
known timing observations (28 historical job upper bounds plus 20 observations
from four actual native requests) and the MAX derived from all 56 state
witnesses, unknown legacy rate
history and four independently matched ownership settlements. These are
conservative observations, not 48 requests; no timing history was truncated.
Current held or stronger owned/local constraints still win through MAX/OR.

The original native result remains 13 raw/five filtered. The later pure-date
qualification uses the same complete 239-post responses and yields 12
normalized/one current, with 11 actual expired records filtering to zero.
Initial validation accepts an all-closed empty collection only with its
rechecked typed collection-local proof. Production execution and live delivery
remain separate from the native and offline qualification recorded here.


## Historical header-only HTTP 429 stop — issue #328

At that revision, a received HTTP 429 stopped after the header constraints
are retained, before reading any response body. The response must close
successfully before this intentional stop is assessed. A close or persistence
failure follows the existing failure handling; they cannot establish a healthy
collection. Previously retained holds, numeric MAX constraints, Retry-After
and unknown history remain. An unread 429 body provides no new challenge
classification.

Issue #409 below supersedes that header-only behavior with bounded body and
closure assessment. The historical conditional repair preserved non-429
responses, REP selection, the full
collection/parser/IDs/dates/expiry, GitHub error-body handling, publication,
caps, workflow, state schema and finite migration markers. It does not add
missing pending ownership or upgrade a legacy source to a modern continuity
protocol.

Private ordinary transport/state controls and protected normal-AST comparisons
qualify this change. Earlier genuine native evidence retains its original code
and date attribution; no new publisher collection, current factual review,
complete native replay or production run is claimed. The maintainer alone owns
workflow/state coordination and the maintenance rollout for issue #328.


The first merged production attempt, `44037339739/1`, failed while reading
GitHub workflow metadata before `main()` entered `run_body()`. Its full log
reported `Workflow pacing state unavailable`; the sole collect job ran for
66 seconds and exported no artifact. This does not establish an HTTP status,
rate limit, or publisher failure. The exact immutable head, source/workflow
blobs, job identity and timestamps, and empty artifact inventory are separately
reviewed pre-access evidence; this attempt is not covered by the generic
58-second proof. It continues from the same actual owned artifact and the
54-point local MAX approval, retaining all completion bounds and restrictions.

Metadata GETs now try at most three times for transport errors and selected
transient HTTP statuses, within the existing execution/phase deadlines.
Server Retry-After holds are respected; unsupported or longer holds stop
without a premature retry. Permanent HTTP failures and invalid JSON stop.
Failure messages expose the bounded API route, HTTP status, numeric rate-limit
metadata, or transport exception class. They omit query strings, response
bodies, credentials and signed locations. These diagnostics establish the
actual failure on a later attempt instead of guessing its cause. Publisher
collection, expiry decisions, and the reviewed native result are unchanged.


The actionable diagnostic from attempt `44039351512/1` established HTTP 404
for the adapter contents route, with 4,554 API requests still available.
Authenticated inspection confirms that old failed eight-second run
`37776255637/1` at commit `18acb393d6f8a8baa4eb36a33678017e51943c40`
predates this adapter file. Optional generic short-job eligibility previously
tried to read that absent file and aborted restoration. The earlier offline
metadata fixture incorrectly supplied a matching file for every unknown
historical head; it now models this real historical 404 explicitly.

Only the optional short-job code proof treats HTTP 404 as a negative proof.
That historical run remains in the complete ancestry and is never omitted;
a newest run with the same missing-file condition still blocks missing-state
restoration. Required code proofs, other HTTP statuses and transport failures
remain errors. The second exact production failure has its own immutable
head/source/workflow/job/time/empty-artifact binding; its 62-second duration
is not treated as a generic short job. Strict hosted restoration across all
63 completed actual attempts retains all 19 no-access completion bounds,
the actual owned artifact and the complete 54-point MAX seed. No new publisher
collection is claimed by these offline controls.

## Healthy pre-request deferral — issue #409

A retained cooldown or a known publisher guard that exceeds the remaining
collection budget stops before creating a policy owner or physical request.
These two expected timing decisions now leave the existing journal unchanged
and eligible for owned export. The decision is raised only after a clean lock
exit verifies the held journal generation and closes both lock descriptions.
Previously it crossed the lock's broad failure handler, marking an otherwise
healthy journal untrusted and suppressing its artifact.

This does not shorten the cooldown, clear pending/refused state, reset unknown
history or admit another request. Pending owners, refusals, exhausted counters,
journal replacement or in-place changes, persistence errors and lock-close
failures retain the original fail-closed behavior. Successful admission still
durably reserves the original policy owner before collection proceeds.

The source-local repair also closes the required response-assessment gaps:

- A received 429 saves its strike, doubled current physical/page gaps and
  exponential fallback before response entry or header access. All repeated
  and supported combined numeric `Retry-After` fields contribute their MAX.
  Known stronger values survive an unsupported positive constraint; that
  response remains pending instead of being treated as assessed.
  IMF-fixdate, RFC 850 and UTC asctime HTTP dates remain supported. Mixed
  date/numeric fields retain every recognizable MAX before an ambiguous field
  stops assessment; accepting an email parser's prefix never proves completion.
- The bounded body is inspected before a 429 stop. Ordinary throttling remains
  timed; visible verification also retains a hold. Known content 401/403/451
  holds are saved before response entry or headers. Literal robots-only 4xx
  unavailable handling remains distinct. Every received REP body is inspected
  for visible verification, including a 403/404 body. A status-only unavailable
  REP response, including an ordinary access-denied title, is not a global
  content refusal.
  The known strike, current doubled gaps and minimum 1,800-second embargo are
  committed before full exponential floors are calculated. An unrepresentable
  larger floor remains pending; response closure is still attempted and bounded
  when status persistence or calculation stops before response entry.
- Successful robots responses need one explicit `text/plain` representation;
  HTML tags are not accepted as policy. Empty and comment-only plaintext
  policies remain valid, and applicable fractional delays are preserved.
- A received response is completed only after assessed constraints and actual
  response closure. Body, entry, header and close errors retain verified
  pending ownership and deny process reentry and fresh local access. Journal
  generation or persistence failures keep their original invalidation path.
  Repeating cancellation bounds response closure after a body timeout, for
  source, infrastructure and the original publication HTTP guard.
- Publisher framing accepts a single content length or a sole chunked transfer
  without content length. Chunked bodies are decoded by the standard transport
  and retain the five-MB body bound. Duplicate length/transfer fields, combined
  transfer and length, and unsupported or stacked codings remain unassessed.
  The original temporary parent descriptor, name, owner and mode are pinned
  along with the owned leaf; a renamed parent or symlink cannot substitute for
  it. A legitimate shared sticky parent with mode 01777 remains supported.

Collection, parser, IDs, dates, expiry, metadata, atomic publication, state
schema and workflow are unchanged. Private controls use synthetic settled
state with actual transport, journal, namespace and export functions; they do
not claim a new native run or current publisher facts. All four complete
successful responses from the earlier native run are separately assessed by
the new transport without publisher access. Full retained-page comparison
against the unchanged main collection path preserves 13 raw records and the
actual zero-record expiry-filtered/current-consumer result. That is reuse of
retained evidence with its original revision/date, not a new live collection.
Post-merge production verification remains separate.
