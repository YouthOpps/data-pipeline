# Harno scholarships and academic grants

Source `ee-harno` collects public academic funding from the Estonian Education
and Youth Board. Discovery starts at the [international scholarship entry](https://www.harno.ee/en/scholarships-and-grants/scholarships-studying-and-working-estonia/scholarships-international)
and follows current English/Estonian scholarship navigation, own programme
details, language variants and HTML pagination. The collection is not a fixed
list of profiles and does not compare page content with pinned fingerprints.

## Inventory and boundaries

Research on 14 October 2026 traversed 30 canonical pages and normalized 70
records. Counts are observations, not acceptance constants. The collection
covers the international summer/research/degree schemes, Estophilus, language
courses, Ustus Agur, special-needs support, Kristjan Jaak, EKKAV lecture funding,
five distinct Nordplus subprogrammes, young scholars, EUI doctoral funding,
care-leaver support, Uuri Noorte Heaks, higher-education textbooks and the
publisher's foreign-scholarship panels.

The [foreign-scholarship listing](https://www.harno.ee/valisriikide-stipendiumipakkumised)
contains substantive HTML, including expired offers retained by the publisher.
Its 17 country panels contain separate awards: two Latvian schemes, six Italian
schemes, Czech summer-school and study-mobility funding, three current Swiss
schemes, four older Swiss schemes, four Japanese MEXT awards, thirteen DAAD
programmes, four Hungarian schemes and other country offers. Benefit tiers
within one scheme are retained as conditions rather than invented awards.
Multiple application windows stay structured under the same programme ID.
A listing under closed application rounds is not marked open merely because
its calendar deadline is unknown.

Umbrella navigation, recipient stories, historical Kristjan contracts,
institutional directories, textbook catalogues, application systems and external
provider websites are not separate opportunities or collection endpoints.
The expressly discontinued expatriate-Estonian scheme is excluded. Municipal
service allocations and youth-service operating grants are outside this academic
scholarship source. The Erasmus page is a referral to the external programme,
not a new Harno funding call. Unknown PDF-only conditions are explicitly
identified with their links; their presence never discards substantive HTML
offers or creates invented amounts.

## Current facts, dates and identity

Programme titles, factual clauses, funding amounts, eligibility and application
windows come from current HTML sections. Full material block digests complement
extracted evidence so a substantive change beyond the introduction changes the
record and its publication `updated_at`. Digests are change signals, never
collection gates. The 600 UTF-16-unit summary is a short index; the supporting
evidence consists of short factual clauses, not complete narrative paragraphs.
Long unsegmented conditions are linked and explicitly marked for review rather
than copied wholesale. PDF references are scoped to the offer and shared
conditions that actually link them, not every offer on the same page.

All 13 previously published IDs are retained. Compatibility aliases contain
identifiers only, without pinned dates or funding terms. The three previously
published Kristjan mobility rounds keep their established date-based identities;
new genuine rounds receive their own call identity. Other programmes retain one
stable identity and structured `application_windows`, with a null universal
deadline when several distinct windows are published.

The current language scholarship, Estophilus and both EKKAV lecture awards
have an October 15 to November 15, 2026 window. Estophilus's English translation
still displays a March round; its substantive native Estonian page supplies the
current call, and the discrepancy is disclosed with each language's provenance.
Kristjan's native page likewise provides its actual current round notices.
Separate lecture awards preserve the difference between reimbursed guest travel
and explicitly unreimbursed Estonia-themed lecture travel.

Only explicit application clauses supply deadlines. Project periods, course
dates, result announcements, page updates, birthdays and reimbursement milestones
do not. Yearless and incomplete dates stay unknown; a cross-year opening without
an explicit year is not invented. Explicit Estonian closing clocks use
`Europe/Tallinn`, including DST; unzoned dates stay date-only and retain their
stated clock in evidence. The EUI January deadline has no year and therefore
does not become a fabricated closing date.
An academic-year label cannot supply a missing deadline year. An explicitly
unannounced suboffer deadline does not inherit a different shared deadline;
opening and closing clocks are associated with their own dates.

The degree/exchange programme's actual English citizenship list is parsed and
validated (currently 39 countries). Embedded ISO 3166-1 names, official/common
names and publisher aliases cover 249 codes; these are a normalization taxonomy,
not a fixed eligibility list. Unrecognized citizenship entries fail clearly
instead of silently disappearing. Residence alternatives, host countries and
publisher location never become citizenship restrictions. Country-panel headings
and their destination conditions establish foreign hosts; an expressly
online-only offer has no physical host inferred. Russian/Belarusian exclusions
remain scoped to the international scheme. PDF-only rates stay unverified.

## Access and publication

Current robots and linked official privacy/cookie policies are checked. The
reviewed policies concern personal information and browser services; no general
factual-collection prohibition was found. This is not an open licence to reproduce
protected works. Normal TLS, the identifying YouthOppsBot User-Agent, refusal handling,
same-publisher redirects and complete-document checks remain enabled.

This migration gives every Harno physical exchange, including robots, redirects
and retries, at least 12.2 seconds after the previous response completes and no
more than five starts in any rolling 60 seconds. Actual catalogue, umbrella and
pagination exchanges receive a separate 30-second completion guard; ordinary
programme details and literal robots remain on the physical floor. Longer
applicable robots and Retry-After constraints remain authoritative.

A received 429 persists an increasing cooldown of at least 1,800 seconds and
doubles the current physical and inventory gaps before its body is read, then
stops the collection. Repeated strikes increase the retained constraints;
short valid Retry-After cannot reduce that floor. The original single retry
for fully assessed 502/503/504 remains. Ordinary typed pre-response connection
failures retain their original two-attempt bound. TLS, local storage, partial
body and unresolved policy failures cannot become healthy connection retries.

Complete exact-attempt history and the newest owned state are verified before
collection in both LOCAL and hosted modes. LOCAL additionally requires the
exact own workflow paused before initialization and after startup quiet, before
its first publisher exchange. Missing or corrupt mature state cannot fall back
to an older allowance. The LOCAL marker binds the reviewed first transition.
The hosted marker now binds its separately approved conditional first-run
MAX. Complete genuine
native evidence and its qualified offline expiry projection are described below.

During scope research the unrelated [municipal integration-grant endpoint](https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus)
returned 403. It was requested once through the existing transport, never retried or
accessed through an alternate route. Subsequent distinct academic URLs used the
same unchanged pacing state; no family-wide refusal flag exists in this
transport. The response headers were not retained, so no claim is made about
whether that refusal supplied Retry-After. The refused municipal resource is
outside the academic collection, not a silently dropped scholarship.

```sh
python -B adapters/ee-harno/test_adapter.py
python -B adapters/ee-harno/adapter.py
```

The sole live test retrieves real records and emits complete JSON on stdout,
with diagnostics on stderr. It receives no publication token and writes no
datasets. Development retries reused genuine responses only in process RAM;
the delivered test has no temporary cache hooks or fixtures.

The matching workflow runs on source-filtered main pushes, its daily
schedule and manual dispatch. Its only application command is this adapter's
`--publish`. The repository/main guard, scoped App token and serialized
publication remain unchanged. Publication atomically writes only
`datas/ee-harno/{data,metadata}.json`, preserves last-good data after failure,
creates no source folder on first failure, and refuses conflicting newer
source updates. Metadata preserves the existing cross-run publisher cooldown
(at least 120 seconds after the last response, or longer Retry-After); later
publication runs honor it. No production Action is used for testing.

## Expired-record cleanup


A refresh first retrieves and validates the complete nonempty publisher inventory.
Only then are records with a verified past `deadline` removed from the next
source snapshot, including expired identities from the previous publication.
The existing full replacement does not merge old records back into the result.
Date-only deadlines remain eligible through their closing date. With no
documented publisher timezone, cleanup uses the conservative UTC-12 calendar: the
closing date must have ended globally before removal.
Explicit timezone-aware timestamps expire strictly after the stated instant;
missing deadlines remain eligible; unresolved publisher dates stay null.
Invalid normalized deadlines fail validation and preserve last-good data. Status, record age and
absence from a response are not expiry evidence. Publisher inventory coverage
continues to be checked before filtering.

The refreshed subset and success metadata are published atomically within this
source. If every validated record has expired, success publishes an empty array;
an empty raw retrieval still fails validation. Previously published empty arrays
are valid snapshots. Retrieval, parsing, validation and publication failures
preserve last-good data and its success timestamp; safe failure metadata may
be updated. The sole live test remains non-publishing and verifies the complete
raw inventory. Cleanup and failure boundaries are checked separately without
production execution.

The official language-course page, reviewed 14 October 2026, now publishes
the autumn 2026/27 call with an October 15–November 15, 2026 application
window. The English EKKAV hub and Estonian guest/Estonia-themed lecture
calls independently publish the same autumn window. All three changed
substantive fingerprints and the three affected call recipes were updated
together; the earlier spring edition is replaced by this actual current call.
No hour or publisher timezone is stated for this cutoff.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The migration changes
pacing and durable state as described above; the source identity and original
default GitHub pacing remain preserved. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **04:56 UTC** (`56 4 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Actual inventory hubs, continuations and partitions wait at least 30 seconds
after the preceding inventory response completes. Each physical redirect and
retry retains that purpose, while literal robots use the separate physical
allowance.
This includes taxonomy/listing pages where applicable. The existing native
transport still enforces its physical-request allowance across retries, redirects
and detail requests, with stricter publisher limits and retained backoff taking
precedence. The page guard does not raise the durable request interval for all
subsequent article/detail requests or reset existing budget/refusal state.
Inventory scope, parsing, validation and atomic publication are unchanged.

## Workflow concurrency

`youthopps-ee-harno-publisher-v1` serializes runs of this source. Up to 100
pending runs are retained (`queue: max`); active runs are not cancelled
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

Published metadata uses `display_name` = **Harno** for the visible
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
Source-pair changes stay protected; reference writes keep force:false. These publication-conflict safeguards alone leave source collection, fields,
pacing, schedule and the 30-minute job cap unchanged. The migration described
above has its own pacing scope.
Validation uses inert external controls; no source fetch, GitHub test write or
production invocation is claimed.


## Reviewed migration and finite admission

The original 30-minute job, 04:56 UTC schedule, full bilingual breadth-first
frontier, original test and normal record/publication helpers are retained.
The prior code had no whole collection timer. This migration introduces
stricter entry-frozen 1,200-second source IO/wait admission and 1,740-second
whole admission. History, restoration, snapshot and cross-run cooldown all
debit the same clock. Publication owns at most 180 seconds without borrowing
the reserved 180 seconds for failure reporting or 60 seconds for export;
coordination headroom is shared, never renewed. Authentic current-job proof
may only tighten the remaining allowance. Original publication HTTP alarms
retain ownership; no permanent outer alarm competes with them. These bounds
are admission and IO limits, not a hard arbitrary-parser wall guarantee or a
claim that every possible frontier/retry/stronger policy fits. Exhaustion
preserves last-good data rather than truncating the supported inventory.

Source-local modern state retains unknown legacy access/429 ancestry and the
exact known municipal 403 observation above. No absent old state is treated
as zero history. The original source and GitHub buckets and cross-run metadata
constraints must be recovered and reviewed before a one-time public boundary.
Three exact cancelled old attempts with authenticated empty job inventories
remain in the history; they supply no invented completion or wire timestamp.
Any changed identity, unreviewed empty attempt or missing newest owned state
fails closed. Both initial and later state handoffs require explicit ownership.

A durable pending transaction covers physical reservations and every cached
robots target assessment until all response and policy constraints are saved.
Unresolved transactions cannot be cleared by an older artifact or a fresh
process. Retained original locked descriptors and pinned parent/current
identities protect state mutation and cleanup; a stronger replacement or reused
foreign descriptor is preserved. Persistence/close failures remain sticky and
preserve a primary exception. Attempt-owned output contains inert state only;
safe stale output is invalidated before fallible readiness signal/unlink work.
Responses and records are never uploaded with pacing artifacts.

Offline controls use private inert IO and localhost clocks only. Current full
publisher facts, all discovered links and date precision remain gates for the
later separately released original native test. The existing UTC-12 date-only
cleanup is preserved for factual review, not asserted as a publisher timezone;
any UTC calendar correction needs actual source evidence and narrow review.


When the original breadth-first reader discovers pagination on a previously
unclassified first listing, its logical completion signal is converted to a
wall-clock observation and MAX-retained in durable inventory state before the
continuation can open. This handoff has its own pending assessment transaction;
a persistence failure stops before the next request. Stronger retained gaps,
embargoes and holds remain effective. Ordinary detail requests retain their
physical floor; no new graph edge or blanket listing classification is added.


### Approved first LOCAL transition

The [maintainer's exact LOCAL boundary](https://github.com/YouthOpps/data-pipeline/issues/300#issuecomment-6208100706)
binds the reviewed seed with all 30 known timing observations: 28 actual job
completion upper bounds and two retained original publisher reservations.
The original publisher namespace is present, with its authenticated raw state
retained in provenance; the modern namespace is separately absent. Absence of
the modern state is not zero publisher history or permission to clear holds.
The original municipal-content 403 and unknown unretained access/429 ancestry
remain explicit. The source-specific LOCAL literal was bound for the
separately released complete original live test below. The hosted literal now binds its separate
conditional first-production boundary; authoring this binding does not
launch collection.

The private observer readiness uses the actual source normalizer, timezone-aware
expiry argument and metadata helpers. Synthetic current/expired records pass
the complete-ID consumer checks; wrong record and metadata sources fail. The
original external-wrapper source-validation gap and its narrow repair are
retained separately. Synthetic readiness does not establish live source facts.


### Complete native evidence and qualified source-expiry correction

The one genuine original live test at source `c5f41b39` passed: all 30 canonical
HTML pages (32 BFS entries), 36 physical responses with full headers, 70 raw
records and the original 29 filtered records. All 212 actual successful state
writes and 70 sequential physical/policy transactions were retained. Actual
completion-to-wire spacing was at least 12.2213 seconds; inventory completion
spacing was at least 30.1941 seconds, with at most five starts per rolling
minute. The complete native took 641.61 seconds.

Review of those same complete actual pages found seven own country panels
under the publisher's closed-round heading, one programme whose complete
2025/2026 application windows had all ended, and the Slovak offer's own
statement that applications are currently not accepted. Three pure helpers
now use that source authority: recognized open headings reset prior closed
context; the exact Slovak clause affects only its own section; expiry removes
source-proven closed records or complete supported windows that have all
ended. An expired status alone cannot remove an eligible future record. Closure
evidence must belong to the exact primary source segment; an older related
translation cannot supply it. Aware window boundaries use their full instants;
mixed precision, missing, ambiguous or future windows preserve uncertainty.
No new deadline, year, ID or lifecycle schema is invented.

Date-only cleanup now uses the UTC closing calendar day. All 49 actual
date-only records were checked at closing-day and next-midnight boundaries.
The three genuine aware deadlines retain the explicit publisher wording
23:59 Estonian time; their exact before/equal/after behavior is unchanged.
The complete retained input reproduces all 70 records, with only the Slovak
record's status changed. Corrected filtering retains 20 records and removes
50 actually expired records; that expired subset produces a valid empty
envelope. Full current-consumer replay passes 70/20/0. These are qualified
offline projections of complete genuine inputs, not a second live test or
a live run at the corrected source revision.

The full proposed MAX retains all 174 known timing observations: 28 actual
legacy job-completion upper bounds, two retained original publisher starts
and four observations per each of the 36 real native exchanges. Observations
are not request counts. All numeric constraints, unknown historical access
and 429 ancestry, and the original 120-second metadata tail remain. A future
hosted transition must separately bind its fresh namespace observation while
preserving the complete known MAX; LOCAL state or inodes are not assumed to
exist on a fresh hosted runner. That conditional first-hosted boundary is now approved and bound, as
described below.


### Conditional first hosted MAX boundary

The [maintainer production boundary](https://github.com/YouthOpps/data-pipeline/issues/300#issuecomment-6208578215)
approves the exact full 174-observation MAX and the qualified pure expiry
correction. The hosted full MAX is derived from all 212 actual native
persistence witnesses and retains the 120-second metadata tail, original
LOCAL constraints, municipal-content 403 provenance and unknown historical
access/429 fields. Complete persistence witnesses remain in external receipts.
The unchanged LOCAL literal describes its initial 30-observation transition;
actual newer state is retained through strict continuity and MAX merging.

The first hosted literal expects the original pacing namespace and original
coordination lock to be absent on that fresh runner. This is an exact
conditional observation, not an assertion that LOCAL state is absent: the
actual LOCAL original namespace was present, and its constraints remain in
full provenance. Unexpected hosted legacy state fails the digest check; no
runtime exception, compatibility bypass or generic empty-state fallback was
added. Later mature executions require their newest exact owned state.

Independent review separately accepted the genuine original `c5f41b39` test,
the three pure helper corrections at `e8fed826` using all complete retained
inputs, and this literal-only binding. No second publisher native is claimed.
The maintainer owns enable-before-merge, the single automatic production
refresh and its complete 20-record comparison; this README does not claim
that hosted production has run yet.


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

## Journal generation authority — issue #295

The source keeps its original read/write journal descriptor and verified parent
across budget-lock scopes. Reads compare the complete acknowledged byte and
metadata generation before and after access. A stronger same-inode update,
replacement, missing mature journal or changed descriptor closes access without
adopting or overwriting that generation. Existing pre-opener checks and export
use the same authority. The original 16 KiB journal read bound is unchanged.

Writes serialize in RAM, recheck authority, truncate in place, fsync, complete
short writes, fsync again and compare the complete readback before advancing the
acknowledged generation. An interrupted write can leave invalid/empty state and
sacrifice availability; it cannot authorize a fresh budget or older-state heal.
These are advisory checks, not filesystem CAS or arbitrary same-user isolation.

Owned artifacts and readiness signals use checked immediate writes without a
buffered destructor. Their original descriptors remain available through outer
cleanup, so journal or lifetime-close failures invalidate the retained artifact
before fallible readiness signalling. Cleanup preserves unrelated replacements,
foreign reused descriptors and original body errors. An otherwise healthy close
failure remains fatal. No failure path reopens a newer output for mutation.

The repair changes only journal/output IO and its cleanup. Pending-owner and MAX
schemas, finite migration literals, source graph, parsing, IDs, dates, expiry,
publication, workflow and original test remain unchanged. HARNO's existing
120-second response guard and export strengthening remain intact. External
private controls exercise actual source functions with networking denied and
explicitly synthetic admission/metadata/archive inputs. They are not publisher
or hosted provenance. Historical native success and its qualified expiry
projection remain attributed above and to independent PR #311 review
5543347203 at exact commit `54b4ba96dc1c15e573fc266b450c76cda5b0fb1f`.
Historical raw input is no longer available; no new native collection, complete
factual replay, current-consumer pass, state reset, workflow activation or
production recovery is claimed.

Each successful truncate, short write, and fsync now receives an exact-byte
readback acknowledgement before the next mutation. Failure invalidation also
requires that last acknowledged full generation; an external stronger generation
arriving during a partial write is preserved and makes the run fail closed.
