# Fulbright Bulgaria

This standalone standard-library adapter discovers the Bulgarian-American Commission for Educational Exchange’s own awards and structured training programmes. Its primary source is [the research-grants directory](https://www.fulbright.bg/en/research-grants-for-bulgarians/). Current source research on 14 October 2026 covered 43 complete official pages: the home and applicant directories, every supported programme leaf, the two-page conference inventory, current news cards, and both language versions of the privacy policy.

The complete raw collection has 39 reviewed programme/edition tracks: the 37 existing award/applicant tracks and dated training editions, the newly opened [Opportunity Funds competition](https://www.fulbright.bg/en/opportunity-fund-bulgaria/), and the [structured EducationUSA virtual advising programme](https://www.fulbright.bg/en/educationusa-virtual-group-advising-program/). The latter is training with a weekly curriculum and participation requirements, classified as a programme overview rather than a scholarship. Scholarship capacity, course modules, country examples, grantee stories, orientation and enrichment for already selected participants, advisory provider lists and general one-to-one advising do not create opportunities.

The homepage changed after the original 9 October research: the 2027 Opportunity Funds competition opened on 12 October and closes on 6 December 2026. The previous combined homepage fingerprint blocked collection before extraction and omitted this new grant. All 39 other original reviewed pages still matched the prior material evidence during diagnosis. The implementation now uses current labelled programme and track blocks, rather than whole-page opportunity fingerprints or fixed editorial narratives. Numeric benefits, applicant citizenship, supported complete application dates and edition schedules come from their own source blocks. Summaries are independently written English facts; `summary_language` is `en` even for Bulgarian original pages. Unsupported or ambiguous facts remain unspecified.

Discovery includes normal links, menu routes and the publisher’s inert `et_link_options_data` JSON for Divi cards; it never executes JavaScript. Exact reviewed aliases map to the actual programme leaves. Both conference listing pages must remain complete, and new unsupported programme routes, award sections, course editions, conference pages or current news calls fail the collection for review. The news check covers current cards, not every historical news story. Personal biographies, testimonials, galleries, contact forms and page presentation are not opportunity facts. Changing an ordinary contact-form arithmetic question no longer invalidates the opportunity inventory; no question is answered and no form is submitted.

Stable IDs remain the SHA-256 prefix of the source slug and the existing applicant-track identity. Separate Schuman variants, four US graduate awards, core/Flex scholar benefits, individual/host Specialist and teaching-assistant roles, three alumni award types and four actual paid course editions retain their identities. Amounts are taken from the relevant benefit or edition block: monthly maintenance, total funding caps and course fees remain distinct; core benefits are not transferred to Flex, and one course edition’s fee is not transferred to another. A new or ambiguous field shape fails instead of supplying a stale amount.

Citizenship is projected only from an explicit source requirement. Affiliation, venue, a Bulgarian publisher or participant origins do not establish nationality. Opportunity Funds accepts Bulgarian citizens **or recognized refugees** enrolled full-time in Bulgarian schools in grades 10/11; therefore its eligible-country array stays empty. Its summary preserves financial need, English/academic requirements, the exclusion of US citizens/permanent residents, and the absence of any guarantee of university admission or financial aid. The journalist page’s Bulgarian-citizen banner conflicts with a broader origin-country list, so its array remains empty. The ETA dual-citizen wording stays qualified. Institutional hosts have no inferred passport condition.

Only complete dates from an explicit programme application-closing slot become deadlines. Event dates, degree-proof dates, interview/nomination milestones and university admission dates do not. Yearless and ambiguous closing dates remain null. Historical dated closings remain historical even when a stale open banner appears. A homepage Leaders card has a February 2026 date while the leaf describes another academic round and a month-only February 2025 comparison entry; the adapter does not combine these conflicting rounds into a new leaf deadline. Archived US graduate pages and historical seminars do not establish current application openings.

## Access and rights

Requests use the identifying YouthOppsBot user agent, inherited proxy and
verified TLS. Robots checks apply to every endpoint and redirect. Publisher,
apex/www, robots, retries and redirects share completion-based pacing: at least
12.2 seconds after the preceding completion, at most five starts per60 seconds,
and an additional30-second completion guard for inventory content. The durable
journal serializes callers and retains stronger Retry-After, adaptive intervals,
refusals and unsettled pending ownership. Independent machines must not overlap
publisher requests. Access refusals, challenges, unsafe redirects, incomplete
HTML and disallowed endpoints fail collection. Production remains separately
gated until root approval and owned-state handoff.

Publisher robots disallows PDF requests. No PDF, alternate-path bypass, applicant form submission or private application data is used. Course HTML supplies the facts; an external exam-information document is unnecessary.

Legal protection is independent of mutable opportunity text and navigation. Exact reviewed English/Bulgarian copyright footers, substantive rights notices and policy destinations are checked on every page, including Divi footer links. Both complete privacy-policy articles have separate material-text guards. Only the copyright end year is normalized. A changed legal footer, notice, privacy article or new terms/policy destination fails closed. The policies were updated on 16 March 2026 and concern personal-data handling; they grant no open content licence. Output contains independently written factual discovery summaries, attribution and original public URLs. Publisher prose, images, contacts, applicant information and grantee stories are not republished.

## Run and publication

Run the single actual nonpublishing live test from this folder:

```sh
python -B test_adapter.py
```

It retrieves the complete supported source, validates every nonempty actual record and emits the entire collection as one JSON document on stdout; diagnostics go to stderr. It writes no collected-record file and uses no publication credential. Validate the entire output in memory. Source retrieval, parsing or validation failure produces no partial successful collection.

The matching workflow runs on relevant pushes to main, manual dispatch and its existing schedule. Its application command is only `adapter.py --publish`. A source-scoped GitHub App token authorizes publication of `datas/bg-fulbright-bulgaria/{data,metadata}.json` in `YouthOpps/data-source`. Data and success metadata are replaced atomically; a sibling-source ref race is retried without force, while a newer same-source snapshot is preserved. Tests do not invoke Actions or publication. Live test success certifies local collection, not production publication or website delivery.

## Expired-record cleanup

The complete fresh nonempty collection is validated before filtering. Successful publication replaces the source dataset rather than merging old rows, so expired previously published entries cannot survive that successful replacement. No other source is changed.

Offset-aware deadlines expire only after their instant. A date-only closing with no documented publisher timezone stays eligible through that date in every civil timezone; cleanup uses the conservative UTC-12 calendar boundary. Missing or ambiguous dates, record age, absence and an expired status alone do not justify expiry.

All expired records from a valid nonempty refresh may leave an empty dataset. That empty result and success metadata are published atomically, and subsequent snapshot reads accept it. Empty upstream retrieval remains a failure. Fetch, parsing, validation and publication failure preserve the last-good dataset and success timestamp, including a previous empty success. First-run failure creates no published source folder. Failure metadata is source-scoped and does not claim durable success when metadata publication failed.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The earlier
identity-only maintenance preserved native pacing/backoff; the issue314
migration below strengthens those floors and state continuity. Existing
disabled workflows remain disabled until root separately enables production.

## Daily automation

The scheduled refresh runs once daily at **02:56 UTC** (`56 2 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Inventory page continuations and partitions wait at least30 seconds after the
previous inventory completion before the next physical inventory attempt.
This includes taxonomy/listing pages where applicable. The existing native
transport still enforces its physical-request allowance across retries, redirects
and detail requests, with stricter publisher limits and retained backoff taking
precedence. The page guard does not raise the durable request interval for all
subsequent article/detail requests or reset existing budget/refusal state.
Inventory scope, parsing, validation and atomic publication are unchanged.

## Workflow concurrency

`youthopps-bg-fulbright-bulgaria-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Fulbright Bulgaria** for the visible
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


### Received publisher 429 stops before body assessment

A received publisher HTTP 429 now retains the original valid `Retry-After`
MAX and source-local persistence before rejecting the response without reading
its body. A response or pacing-state cleanup error after that status is known
remains nonretryable, rather than entering the ordinary connection retry.
The default `api.github.com` publication response path is unchanged. Normal
responses, the existing two-attempt connection bound, discovery, records, REP,
pacing limits, state schema, deadlines and schedules are unchanged.

This is a narrow error-path correction, not a modern pacing migration. It
does not add adaptive gap doubling, a minimum exponential 1,800-second hold,
pending ownership or hosted owned-state continuity. Existing storage limits
and unlogged historical access remain; a failed persistence operation cannot
be described as a durably retained hold. Validation uses bounded private
ordinary controls and exact normal-function AST comparison. No new publisher
request or genuine native run is claimed for this maintenance change.

At that maintenance stage the adapter retained only its original local host
JSON, lock and fsync behavior, without a hosted owned-state handoff.


### Offline durable pacing migration — issue 314

This candidate preserves the original 43 reviewed inputs, 39 programme tracks,
complete reader, factual normalization, IDs, deadline handling and original
publication functions. Eight exact entry/catalogue pages receive an inventory
purpose; programme and document pages remain ordinary detail requests even
when their content links to another page. Inventory purpose follows physical
content redirects and retry attempts. Literal robots requests use ordinary
physical pacing, with the original per-origin REP cache preserved.

Each physical publisher attempt waits at least 12.2 seconds after the prior
attempt completes, with at most five retained starts in 60 seconds. Inventory
content attempts additionally wait at least 30 seconds after the prior
inventory completion. A received 429 durably doubles the CURRENT physical and
inventory intervals, retains the stronger exponential minimum 1,800-second
embargo or valid longer Retry-After, and stops this collection before body
assessment. Invalid Retry-After alone adds no permanent hold. Genuine visible
access denials, applicable REP restrictions and content 401/403/451 retain a
hold; literal REP unavailable statuses remain distinct. The redundant old
all-title fallback is removed: hidden/footer titles cannot override the
transport's genuine HEAD and visible primary-content assessment.

The new schema-3 journal reserves a durable pending owner before transport.
Policy assessment and cached REP enforcement settle only after every response
restriction is safely retained. Any persistence or policy assessment failure
stays sticky through finally, reentry and export. Safe current-inode journal
and owned artifact operations retain directory/file identities, use bounded
nonblocking reads and preserve unrelated unsafe objects. Healthy collection
and publication continue to use the original two-attempt/four-hop source
bounds and default GitHub six-second pacing, Retry-After, CAS, jitter and
publication-window contracts. The original test remains unchanged.

One monotonic entry admission ends no later than 1,740 seconds, additionally
clamped by the authentic executing job's original 1,800-second limit. New
restore (180), proof (30), initial snapshot (120), source (1,200), publication
(180), failure (180) and export (30) phase maxima are not additive promises of
fit. Each phase is admitted once and clamps to remaining time after downstream
reserves; publication and failure cannot borrow export's 30 seconds. The same
failure end begins before the first fallback snapshot and governs every
subsequent fallback read/write. Original per-call publication alarms remain
in charge without an outer competing publication alarm. These are stricter
new allocations, not preexisting collection caps or demonstrated native fit.
The daily 02:56 UTC cron and 30-minute job timeout remain unchanged.

Hosted execution first authenticates the exact current job read-only, before
trusted restoration or any default GitHub host-bucket write. The original
publication proof runs only after restoration. Both first hosted transfer and
later owned restoration require the separately approved exact four-key
BG_FULBRIGHT_PACING_MAINTENANCE workflow environment envelope; the six-key
hosted declaration does not substitute for this environment. Latest exact
workflow/run/attempt/head/digest-owned state is mandatory, with no older-state
fallback; stronger current holds, counters, pending and intervals survive MAX.
LOCAL execution checks the paused workflow before restoration and again after
startup quiet before the first wire request.

The cached legacy observation is PRESENT: seven source-host starts, physical
interval six seconds and raw hash 5a767565a583fb2c251c7eb997fb6bdc9923f411abe7c3082da11c449a0f6f5e.
Recovered metadata covers all 30 workflow wrappers, 29 exact completed jobs
and one jobless cancellation, with zero hosted artifacts. These bounds do not
recover lost historical headers or establish a lifetime zero rate counter.
The proposed finite transition retains unknown history, all known starts and
job-completion upper bounds, stronger restrictions and a fresh quiet guard.
Root must freshly bind actual legacy parent/file identities and exact bytes;
this offline implementation has not touched the actual journal.

The exact six-key LOCAL bridge is now root-approved and bound; the separate
HOSTED declaration remains None. The production maintenance environment remains
absent. A separately authorized original native execution was still required
at that binding stage; this LOCAL bridge was not that release. A subsequent
production boundary remains separate. Private inert tests cover the complete
original reader and both hosted main routes with synthetic approved fixtures, missing-envelope
negatives, actual timestamp/helper contracts and source-specific current
consumer cases. Retained earlier research HTML reproduces the original 39
records; this is not a recovered full native response/header/timing chain.
At that offline implementation stage no new publisher request, original native
PASS, production handoff or runtime fit had been claimed. A genuine original
native required a separate root release and full private RAM evidence. Any first
failure retains diagnostics for root review before a separately authorized
repeat; there is no automatic retake.


Ordinary initial acquisition errors now latch the failed publisher gate before
best-effort cleanup of newly opened child-directory, secure-file and collection
lock descriptors. A secondary cleanup close error preserves the active primary
exception; a healthy parent-close failure is propagated. When the very first
identity read fails, cleanup relies only on the newly acquired descriptor's
ordinary ownership provenance. This is not an arbitrary same-user replacement
or descriptor-reuse guarantee, and no such expanded fault campaign is claimed.

The separate native observer starts its private 12-hour RAM keeper before the
first actual initial-state capture. It streams safely verified exact legacy
bytes and observed modern absence, each attempted save's prewrite raw bytes,
returned raw bytes, and the prewire journal bytes, alongside actual response
headers, delivered bodies/available partials and stdout. Raw empty or corrupt
bytes remain evidence when safely observable. Successful native acceptance
requires complete identity-verified raw captures for all successful saves;
missing or failed capture cannot become PASS. Primary source exceptions remain
primary when observation also fails. Only observations actually delivered to
the keeper survive process death; future or never-returned bytes are unknown.
Those observer checks were private ordinary fixtures and did not themselves
execute a source native.


The finite LOCAL declaration is exactly the state and legacy provenance approved
at [root's issue314 boundary](https://github.com/YouthOpps/data-pipeline/issues/314#issuecomment-6209611751).
Root freshly checked all30 terminal workflow wrappers,29 exact completed jobs,
one jobless cancellation, zero artifacts, paused workflow and no active overlap.
Root also read-only reverified the exact PRESENT seven-start legacy raw bytes,
parent/file identities and genuinely absent modern namespace. This author binding
changes only the declaration and this README, not any function, test, workflow,
controller, keeper or consumer code. It neither clears holds nor initializes a
journal, performs a publisher request or authorizes a native/production run.


### Genuine native and qualified pure closure correction

Root separately authorized and launched exactly one original native on source
`0468fc35` at [execution release6209728580](https://github.com/YouthOpps/data-pipeline/issues/314#issuecomment-6209728580).
It passed one original test: all43 reviewed HTML inputs and one robots response,
39 raw tracks,28 records after original deadline-only cleanup, and a derived
empty case. All44 complete responses/headers and306 actual successful save
pairs were retained in private RAM. Actual43 nonce owners settled for44 physical
requests. Measured completion spacing was at least12.2374 seconds, inventory
completion spacing at least30.0445 seconds, with maximumfive starts per minute.
This proves that original run's source collection, not hosted production.

The full actual source review also identified nine own closed or specifically
ended editions among the original28. Five programmes have explicit own
competition-closed banners (graduate study, Leaders, Humphrey, mathematics and
civil-society); generic email invitations do not announce a reopened round. Two
PMI-ACP cards name their2023/2024 editions and immediately following Completed
or Fully Booked labels. The particular PMP September/October2026 edition ends
October4, despite a stale Ongoing label; the separate October30–November8 edition
remains unknown/current. FISI2019 is a dated particular seminar described as
having taken place, not an annual-programme cutoff inferred from its title.
FISI2022 has no concrete own event end or closing date. Its own heading,
participation invitation and stated purpose all identify the specific2022
seminar edition. A distinct archival eligibility proof excludes that edition;
its raw status stays unknown and its deadline stays null. This is not a claim
of confirmed closure. Conflicting, unresolved or future edition statements,
annual programme history and a URL year alone do not establish this proof.

A post-native pure correction preserves all39 raw IDs and the nonempty guard.
It adds bounded factual own-closure/ended-edition evidence to the existing
classification.evidence of those nine tracks and distinct archival evidence
for the2022 seminar; no new public field or global
ID map is added. Cleanup recognizes the exact generated own-source evidence,
with matching canonical URL and independently checked calendar end for a dated
edition. Status alone, generic degree completion, footer/related-programme
closures, past winner/testimonial years and annual yearless dates do not retire
a record. The generic completed-the-day status match is narrowed. The actual
mathematics and past PMP edition raw statuses become expired; all other actual
public fields remain unchanged except those ten added evidence notes.

The complete own body is inspected for announced reopening beyond any short
summary; unsupported possibly future reopening remains unresolved. Existing
UTC−12 certainty and UTC+14 status boundaries are preserved. Event-end evidence
never becomes an invented application deadline, timezone or time. All nine
retired raw deadlines remain null. The exact actual43 inputs replay to39 raw,
18 current and21 removed (nine own closed, one archival and11
calendar-expired). Each actual excluded subset filters to valid empty; a genuinely empty raw collection
still fails. All remaining18 public facts match the original native exactly.
The full original test/response/state history remains attributed to0468; no
second native or publisher request was made for this pure qualification.

At that pure-replay stage, HOSTED initialization and production were unapproved,
and both the HOSTED marker and maintenance environment remained absent. The
full-known native MAX was not modified by the factual cleanup correction.

The separate production boundary is now approved and bound at
https://github.com/YouthOpps/data-pipeline/issues/314#issuecomment-6210529147.
The exact six-key HOSTED declaration and matching four-key
BG_FULBRIGHT_PACING_MAINTENANCE workflow environment preserve full-known state
7608276489506948a6d550e58425c4537fa7c7d7c034c67809adedd5ee69709e:
256 conservative timing observations and MAX from963 state witnesses, not256
requests. LOCAL provenance and its original approval remain unchanged. Both
first-hosted and later-owned main routes require the complete matching workflow
configuration; missing or mismatched configuration fails before collection.
The39 raw/18 current pure qualification remains an offline replay of the sole
original0468 native; no second native occurred. Hosted execution, owned artifact
and website delivery remain pending the maintainer's separate merge/run review.


## Header-only HTTP 429 stop — issue #328

A received publisher 429 now retains its adaptive constraints and stops before
reading any response body. Both current physical and inventory gaps still
double; the repeated-strike exponential cooldown remains at least 1800 seconds,
and a longer valid Retry-After remains stronger. A short valid Retry-After does
not reduce that fallback. The response context must close successfully before
this stop is assessed; persistence or close failure leaves the original pending
failure gate closed, with no retry or next publisher opener.

This paragraph supersedes earlier 429 body-inspection descriptions. An unread
429 body supplies no new challenge classification; previously retained holds
and all stronger constraints remain. Non-429 response inspection, literal REP
semantics, original parser/frontier/IDs/dates, publication and GitHub pacing,
phase limits, owned-state schemas/seeds and workflow remain unchanged.

Acceptance for this conditional guard uses actual private transport/writer/
completion contexts and exact whole-module AST protection after removing only
the new 429 branch. Existing genuine native evidence retains its original code
and date attribution; this maintenance claims no new publisher collection,
upstream factual review, full native replay or production run. Issue #328
remains open after this three-source pilot.

### Hosted job observation failures

Before publisher-state restoration, the exact current run identity remains
mandatory. A queued, requested, waiting or pending run is observed at most four
times with two-second gaps using fresh GitHub metadata. Every observation must
retain the expected source, workflow, run, attempt and commit identity; terminal
or unknown statuses fail immediately. Existing execution and job-start deadlines
are never extended. Job metadata is still validated exactly once; this change
does not add retries to the separate publication deadline proof. The bounded
run observation tolerates transient status propagation without claiming
that the historical failed responses were captured or proven stale.

Deadline-proof failures identify the rejected validation stage without logging
credentials or response bodies. Missing newest pacing artifacts still fail
closed; this change does not recover an earlier artifact, reset request history,
clear publisher holds or certify production recovery. See issue #358.
