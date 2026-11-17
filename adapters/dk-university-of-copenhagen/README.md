# University of Copenhagen opportunities

This standalone adapter collects University of Copenhagen (UCPH) published
scholarship programmes and its complete official funded doctoral vacancy list.
The employment portal extension belongs to the same university publisher;
external provider websites, application systems and credentials are not used.

## Official access and reuse

The issue's historical studies.ku.dk redirect is legitimate: its current
canonical scholarship leaf is
https://www.ku.dk/studies/admission/scholarships-for-full-degree-students .
The current funded PhD inventory is selected from the complete official table
at https://employment.ku.dk/all-vacancies/ . Each notice has a public university
URL with a numeric `show` identifier. The former `/phd/` category page no longer
contains a vacancy table. Historical research of twenty notices on 8 October
2026 does not establish the current count.

The inspected www.ku.dk robots policy permits these public study routes.
The employment and about subdomains return robots HTTP 404 (no published
policy at that endpoint). Policies are checked per origin on each collection;
redirect destinations are checked and no login or external recruitment
application endpoint is requested. Robots availability failures other than
404 stop collection. No anti-bot bypass is used.

The publisher footer links
https://about.ku.dk/cookies-and-privacy-policy/ . The current inspected policy
(last updated 5 January 2026) describes cookies and personal-data handling;
no collection prohibition or explicit permission requirement was found for
these public factual pages. This is not an unrestricted reuse licence.
Summaries use independently written factual descriptions and short supporting
conditions. Images, raw HTML, protected full descriptions and application
account information are not published. University attribution belongs in
metadata; each record carries its source ID, canonical URL and evidence.

## Inventory and identifiers

The government MA/MSc admission scholarship is one central programme. The
16 November captured university page replaces the former faculty-specific
admission conditions and second-year HEALTH/SCIENCE cohorts with centralized
selection, full tuition and 22 months of basic living support. It specifies
eligible citizenship, master's admission and study residence, automatic
consideration and annual university selection of participating programmes.
The removed cohorts and their former GPA/faculty claims are not emitted.
The detailed Fulbright study route is collected from this university's own
page, not from the external administrator.

| University programme/cohort | Actual anchor |
|---|---|
| Danish Government MA/MSc admission scholarship | danish-government-scholarships |
| Fulbright full-degree/guest study route | fulbright-scholarship |

The same university page currently publishes 19 substantive named external funding
programmes. They remain attributed to UCPH's own guidance, with external
administration stated explicitly. No external provider endpoint is fetched.
Their application dates and active call status are unknown here; that is not
proof that the programmes are inactive. Distinct awards sharing a real country
accordion anchor have separate identity-qualified IDs and intentionally share
that real URL; no nonexistent per-award fragments are invented.

All anchors below are on the canonical scholarship page above. Conditions are
checked against the actual corresponding paragraph, not inferred from the
accordion's country label.

| Programme | Actual anchor | Decisive own-page facts |
|---|---|---|
| John Monash | accordion-heading-3813 | Australian citizens; taught master's or doctorate overseas |
| Mackenzie King Open | accordion-heading-3814 | Canadian students; graduate study in Canada or abroad |
| CONICYT Becas Chile | accordion-heading-3815 | Chilean citizens; overseas postgraduate study |
| COLFUTURO | accordion-heading-3816 | Colombian citizens; loan up to USD 50,000; conditional 50% scholarship; return 3–5 years |
| Citadel Capital | accordion-heading-3817 | Egyptian citizen/resident, age under 35; overseas master's; return-work 2 years |
| DAAD Annual Scholarships | accordion-heading-3821 | Named graduate award, all disciplines, international study; nationality not stated |
| Friedrich Ebert Foundation | accordion-heading-3821 | German citizens; postgraduate Germany/Europe; academic and social/political selection |
| Inlaks Shivdasani | accordion-heading-3831 | Indian citizen with undergraduate degree; tuition, living costs and one-way travel |
| J. N. Tata Endowment | accordion-heading-3831 | Indian national; one-time repayable postgraduate/PhD/postdoctoral loan scholarship |
| Lady Meherbai D. Tata | accordion-heading-3831 | Female Indian graduates; recognised home/overseas institution; may await admission |
| CONACYT Mexican Scholarship | accordion-heading-3834 | Mexican citizens; monthly overseas graduate award, amount unspecified |
| FUNED | accordion-heading-3834 | Mexican citizens; overseas master's in listed fields; funding terms unspecified |
| Tamim bin Hamad | accordion-heading-3835 | Qatari citizens; overseas study; UCPH approved university |
| Global Education Programme | accordion-heading-3836 | Named full-time postgraduate funding and listed benefits; return-work Russia 3 years |
| Singapore Food Agency | accordion-heading-3837 | Named full/mid-term master's/PhD award, specified scientific subjects |
| FirstRand Laurie Dippenaar | accordion-heading-3838 | South African citizens; postgraduate study outside South Africa |
| Jean Monnet | accordion-heading-3839 | Graduate study on Turkey's EU harmonisation/EU acquis; citizenship not stated |
| Aga Khan Foundation | accordion-heading-3841 | Outstanding postgraduate applicants, selected developing countries, financial need; 50% grant/50% loan |
| Rotary Global Grants | accordion-heading-3844 | Local club nomination; generally rolling with possible local club deadlines |

Source-supported host destinations are deliberately sparse. The Qatari
paragraph says "University of Copenhagen is included in the list of
approved universities." This explicitly supports Denmark. Mackenzie King
allows "graduate studies in Canada or abroad"; Canada is a known possible host,
with other overseas destinations unspecified. FES supports "postgraduate
studies in Germany but also at European universities"; Germany is a known
possible host, with other European destinations unspecified. These are
supported subsets, not exclusive destination lists. Other added programmes
have unknown host countries/location; an applicant nationality or return-work
country is not treated as a study destination. GEP, SFA, DAAD and Jean Monnet
have no positive nationality list because the paragraph does not state one.

Loan terms stay explicit: J. N. Tata is repayable loan support, COLFUTURO has
conditional scholarship conversion, Aga Khan combines grant and loan, and
FUNED's funding/repayment terms are unspecified. The record never presents
these as unconditional free funding.

Exclusions are complete for the inspected funding sections:

| Own-page item | Actual anchor | Reason |
|---|---|---|
| International Scholarships Program | accordion-heading-3814 | Database of multiple programmes |
| Fulbright Commission country entry | accordion-heading-3840 | Duplicate of the richer UCPH Fulbright study FAQ |
| American-Scandinavian Foundation | accordion-heading-3840 | Directory of a range of fellowships/grants/scholarships, no one named award |
| Title IV loans | accordion-heading-3840 | UCPH explicitly no longer participates |
| Private lenders | accordion-heading-3840 | Generic lending/certification advice |
| Veteran Aid | accordion-heading-3840 | UCPH explicitly no longer services the programme |
| Gen Foundation | accordion-heading-3845 | Trust/funder profile without an identifiable named award or substantive selection/application conditions; not claimed inactive |
| Scholarship databases section | section heading | Search/directory advice, not an award |
| Funding-your-studies section | section heading | Budget/tuition guidance, not an award |

The complete scholarship page captured on 16 November yields 21 programme
overviews. This is partial source evidence: the original failed test stopped
before collecting the current PhD inventory. The older 46-record research count
is not a current completeness claim.

Every named block in both substantive funding sections is enumerated. Reviewed
conditions provide optional enrichment only while their supporting text remains
present. New or changed blocks retain minimal original factual prose and unknown
eligibility, host countries and deadlines rather than invented terms. Missing
programmes retire from the next successful full replacement. Empty sections,
missing substantive blocks, duplicate identities or missing/ambiguous programme
links fail safely. Explicit directory, generic-lender and inactive-programme
exclusions above remain in place.

External programme IDs use their actual linked programme URL (without text
fragments), never a new display-name hash. A fixed canonical-URL mapping preserves
all 19 retained legacy IDs, verified against the previously published 45-record
snapshot, Git blob `c7832532dd8536e65edae52c186d7c6d2f842441`. The legacy
algorithm hashed source plus programme name; this repair does not silently
migrate those existing IDs. University anchors remain the public record URLs,
including shared country anchors. A title change at the same linked canonical
programme URL keeps the ID. An unmapped URL for an existing legacy name stops
for identity review instead of silently replacing its published ID. Validation runs after final identity assignment.

Every server-rendered all-vacancies table row is validated, with all next-page
links followed inside the reviewed `/all-vacancies/` path. Only titles explicitly
identifying a PhD role are retained; professor, lecturer and postdoctoral titles
are excluded. A research-assistant role explicitly offering PhD-fellowship
continuation remains included. Every selected public detail page is collected.
Missing, duplicate or malformed advertisements fail the collection rather than
publish a partial inventory. Job identifiers preserve the native numeric `show`
identity: hashes continue to use the historical `/phd/?show=ID` identity while
public URLs point to the actually linked `/all-vacancies/?show=ID` notice. Multiple places or regular/integrated degree routes within one
advertisement remain one opportunity. Listing department and detail conditions
are retained; host Denmark is not applicant nationality.

A detail parse/verification failure reports its native `show` ID, university
URL and failed guard. Title equality, PhD identity and funding evidence remain
mandatory. On the first such failure, the adapter rechecks the complete paced
authoritative listing. Only a changed set of native URLs and titles permits one
complete restart: scholarship conditions are fetched again and every detail in
the refreshed catalogue is validated. No records from the abandoned attempt
survive. An unchanged catalogue, any second detail failure, malformed listing,
or fetch/access refusal fails closed and preserves the last-good snapshot.
Catalogue replacement is not inferred expiry; deadline cleanup is unchanged.

Investigation of the 14 October failure found the previously first advert
`170070` had an explicit 21:59 UTC deadline; the failed collection began seconds
later. It subsequently disappeared from the complete listing and its detail
returned generic portal content. The original failing response was not retained,
so its exact failed condition remains unconfirmed; this recovery is bounded by
observed catalogue changes rather than an assumed cause or retirement notice.

Final identifiers are stable 24-character hexadecimal hashes of source-qualified
native identities. Validation enforces the website-compatible ASCII alphanumeric
and single-hyphen grammar and rejects case-insensitive duplicates after all
source-specific overrides.

Programme descriptions with no dated current application call retain null
deadlines and unknown status. Academic-merit, citizenship, study residence,
and any currently supported programme conditions come from the publisher. The
Danish Government award excludes citizenship in EU, EEA and Switzerland;
no arbitrary positive country list is invented from that exclusion.

## Dates and source inconsistencies

Yearless admission calendars are not exact scholarship deadlines. Their
February-intake dates are inconsistent, and the adapter does not stamp a year
onto them. Historical 2025 GPA examples are labelled historical, not current
selection cutoffs. Notifications and fee-payment dates are not award deadlines.

Doctoral detail clocks take their explicit published fixed offset: CET is
UTC+1, CEST is UTC+2, and GMT+1/GMT+2 are used literally. October CET wording
is not silently replaced with Copenhagen summer time. A supported calendar
application date without an explicit clock/offset stays `YYYY-MM-DD`; no
end-of-day timestamp is invented. Dates and clocks must agree with the listing.

The sustainable-cropping vacancy `170665` has a listing date 1 November 2026,
while the detailed body says 1 November 2027 at 23:59 GMT+1. It remains a real
funded opportunity with null deadline, unknown status and both dates in
evidence. Two MSCA listings call the faculty HEALTH, while their bodies identify
SCIENCE; that discrepancy is retained. MSCA mobility is assessed at recruitment,
not at the application deadline; main activity in Denmark must not exceed
12 months in the preceding three years, with the published exclusions.
Already awarded or successfully defended doctorates exclude those MSCA posts.
The organometallic notice describes an integrated route despite conflicting
regular-route qualification wording; applicants are directed to clarify it.

## Run and test

```sh
python -B adapters/dk-university-of-copenhagen/adapter.py
python -B adapters/dk-university-of-copenhagen/test_adapter.py
```

Both commands retrieve real records without publishing. The sole live test
validates nonempty complete JSON and prints the entire array to stdout;
diagnostics go to stderr. There are no fixtures, output files or dependencies.
Keep overlapping tests and production runs serialized. Every KU subdomain shares one durable publisher allowance: at least
12.2 seconds after the preceding physical attempt completes and at most five
starts in a rolling minute, including robots, redirects and retries. Stronger
retained constraints take precedence. State is outside the checkout; local
collection requires the exact workflow to be paused before initialization and
again after the startup quiet guard before its first publisher request.

## Publication

The matching workflow runs once daily at 06:37 UTC, on manual dispatch
and pushes to main touching this source folder or its workflow. It uses the
repository/main guard, a token restricted to `YouthOpps/data-source`, minimum
workflow permissions and the source-family publication concurrency group. Its only
application command is `adapter.py --publish`.

Publication atomically updates only
`datas/dk-university-of-copenhagen/{data,metadata}.json`. First-run failure
creates no source folder. Later collection/validation/publication failure
preserves last-good data and its success timestamp, and attempts a sanitized
failure metadata update. Conflict-safe commits retain unrelated sources;
concurrent newer source data is not overwritten. Successful publication of
validated nonempty records advances the success timestamp. Live tests and
static review do not invoke or prove production publication.

## Expired-record cleanup

Successful refreshes validate the complete fetched inventory before removing
records with verified past application deadlines. Offset-aware timestamps are
compared as instants; equality with the closing instant remains eligible.
No official timezone is established for date-only deadlines. Cleanup waits
until the date has ended worldwide (UTC−12), a conservative certainty bound,
not an inferred publisher timezone. Explicit publisher offsets take precedence.
Missing deadlines, ambiguous deadlines represented as null, status labels,
record age and absence from a response are never expiry evidence. The existing
publication contract replaces the complete source inventory; old records are
not merged back into the replacement. Consequently expired old records cannot
survive a successful replacement, including when rediscovered with a verified
past deadline. Current records with missing deadlines remain eligible.

The filtered data (including an empty subset after valid nonempty retrieval)
and success metadata are committed atomically only
under this source's paths. An empty/invalid raw response or fetch, parse,
validation or publication failure preserves last-good data and its success
timestamp; safe failure metadata may be updated. Tests never publish.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The current
migration changes publisher pacing and state continuity as described below;
the identifying header and public source identities remain stable. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **06:37 UTC** (`37 6 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

The scholarship inventory and all doctoral listing pages, including every
redirected or retried inventory hop, wait at least 30 seconds after the
preceding inventory physical completion. Literal robots and doctoral details
retain the separate physical completion guard. Detail links do not promote
all later requests to a longer persistent interval. Stronger retained page
gaps remain effective.

## Workflow concurrency

`youthopps-dk-university-of-copenhagen-publisher-v1` serializes runs of this
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

Published metadata uses `display_name` = **University of Copenhagen** for the visible
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
App action mints its scoped token after setup. Under its documented default
one-hour lifetime the unchanged 30-minute job bound has static token headroom;
no observed expiry or token renewal is claimed. Missing or inconsistent proof
fails closed.

Every guarded publication call and the current-job proof call on the reviewed
Ubuntu/Python path has a total alarm guard of at most 30 seconds, plus a
remaining-time socket timeout, including its bounded body read. Unowned or
unsupported timers stop before transport. The source-owned lifecycle alarm
composes with the original publication call guard without renewing either
deadline. Writes and retry waits stop 60 seconds before the job deadline,
final outcome reads stop 30 seconds before it, leaving a cleanup/export
allowance. These publication reserves do not guarantee export after an
overlong collection, runner death or an uninterruptible native call; the
existing job timeout remains the outer bound. No API call receives a fresh
deadline.

Only an actual, classified non-fast-forward reference response can retry,
after checking that the desired outcome has not landed and this source pair is
still unchanged. The first two retries add 1–2 and 2–4 seconds after the
ordinary GitHub pacing gate, before reserving the actual request start, within
the existing three attempts and original write deadline. Authentication,
non-CAS and unclassified failures stop without another write attempt. Terminal
publication errors latch a per-process write stop, including later
failure-metadata fallback; bounded read-only outcome checks remain available.
Source-pair changes stay protected; reference writes keep force:false. Those
publication-conflict safeguards preserve source fields, the schedule and the
30-minute job cap. The publisher migration below changes pacing and adds
stricter lifecycle limits. Validation uses inert external controls; no source
fetch, GitHub test write or production invocation is claimed.

## Durable pacing migration (#326)

The complete scholarship and doctoral graph, the one bounded catalogue-change
restart, record identifiers, source clauses, date extraction and the original
single live test remain unchanged. No publisher change feed or ordering proof
supports a safe incremental shortcut, so every collection reconciles the full
current inventory. Historical counts in this README are reviewed snapshots,
not a current fixed count or an early-stop rule.

Each physical publisher reservation is durable and pending through response
headers, bounded body assessment, closure and applicable policy persistence.
Cached robots decisions have their own pending policy transaction, even when
no wire request occurs. A received 429 doubles BOTH CURRENT retained physical
and inventory gaps, saves its strike count and maximum embargo before reading
a body. It then assesses the bounded body and stops the collection; a visible
verification challenge retains a durable access hold as well as the cooldown.
Body-read and response-close failures retain the pending owner and cannot enter
the connection retry. The embargo
includes at least 1,800 seconds, exponentially increased for observed repeat
strikes, the maximum of every observed Retry-After field and all stronger
retained constraints. Known status restrictions are saved before header parsing;
positive unrepresentable timing retains pending state. Successful robots200
requires one explicit plain-text representation and no HTML body, including
valid-looking policy lines embedded in HTML. Valid empty and comment-only plain
policies remain supported. Local collection requires an existing retained family
namespace; whole-directory loss cannot bootstrap an older remote budget.
The original journal inode and byte generation remain pinned across metadata
restoration; replacement or in-place modification stops before publisher access.
Infrastructure traffic is restricted to the GitHub API and supported signed
artifact-storage origins. Artifact targets are checked before any destination
request, and storage requests carry neither API authorization nor cookies.
Technical body, TLS, storage or policy assessment failures retain unresolved
pending state and prevent a retry or healthy export. Ordinary typed
pre-response connection failures retain the original two-attempt bound. A
technical failure is not a publisher denial.

Literal robots 4xx other than 429 means unavailable REP rules, not permission
asserted by the publisher or a content-access refusal. Content refusals and
visible primary challenges stop and remain held. Current product-specific
rules, or their applicable wildcard fallback, intersect any actual legacy
product-specific restrictions; unrelated agents do not impose new rules.
Applicable finite delays and request rates retain their strictest constraints,
including cached target assessments. Invalid Retry-After alone creates no
hold.

The original host journal remains under
`/tmp/dk-university-of-copenhagen-pacing-UID/`, with hashed `ku.dk` and
optional `api.github.com` names. Its raw constraints are never assumed absent
or reset. The new family namespace is
`/tmp/youthopps-dk-university-of-copenhagen-publisher-v1-UID/state.json`, with
a separate source lifecycle lock. New state explicitly preserves unknown old
refusal and rate-limit ancestry. Every future owned artifact binds the exact
repository, source, run, attempt, head, implementation hash and state hash.
Every prior attempt is authenticated from its own wrapper and complete job
inventory; other queued or active wrappers stop access. Fresh complete history
is checked again after quiet before the first physical request. The newest
applicable attempt must supply its exact authenticated state; missing or
incomplete modern state cannot fall back to an older allowance.

ROOT first approved the finite LOCAL transition in issue #326,
comment 6213563204.
The exact 28 prior attempts and complete job-page digests are now pinned.
That initial MAX retained 30 timing observations, including authentic legacy
starts and job completion upper bounds, with unknown prior access and refusal
history. These observations are not 30 publisher requests. At that initial
stage HOSTED and mature reentry were unset. That binding granted no native or
production release; ROOT separately controls access after observer and current
state readiness. The
matching workflow supplies `UCPH_PACING_ARTIFACT_PATH`; there is no hidden
mandatory maintenance environment variable. Only a trusted, settled state
signals `pacing_ready=true` for the exact-attempt upload. Failure cleanup
invalidates a safe owned stale envelope before signalling failure; unsafe
objects remain untouched. Descriptor ownership, current named inode and pinned
parent checks prevent a weaker stale journal from replacing stronger state.

The new entry clock is 1,740 seconds within the unchanged 1,800-second job. A
NEW 1,350-second prepublication ceiling includes history, authenticated
restoration, current-job proof, prior snapshot, startup quiet, collection and
parsing; it is not a retained historical source cap. Publication, failure
reporting and export reserve 180, 180 and 30 seconds respectively. Failure
ownership begins before fallback snapshot reads. Setup and API latency debit
the same entry budget; no retry or phase call renews it. The original
authentic job proof can only tighten these bounds. Publication keeps its
original GitHub 6.1-second pacing, Retry-After, body bounds and three
classified CAS attempts. An unowned alarm is rejected; one source-owned phase
timer composes with the existing 30-second publication call guards.

The older 43-record native receipt used a different source hash and observed
21 physical requests. Its optimistic new physical span is at least 304 seconds
including startup quiet, with additional inventory waits, setup and body time.
It supports retaining the existing job allocation, not a current full-graph
fit claim. Dynamic listings/details and the bounded complete restart must
finish or fail closed; no first-known-ID truncation or partial success is
allowed. The latest retained failed Action 38763419676 reported a publication
HTTP 422; its precise API cause and initial collection success are unproven.
No new genuine native or production success is claimed for this offline code.
The existing date-only UTC-12 cleanup is preserved pending actual-source
review and a separately authorized factual correction.


### Exact historical cancellation and first native failure

The first separately released original native attempt used source `c9b7db80`
and remains FAIL: receipt SHA-256
`6235909494561722c64e789c5e0c19aa513737deff500eabe0e87943aae39313`
records one unsuccessful original test, zero publisher responses and zero
state-save calls. It stopped during historical metadata assessment; no source
retrieval or corrected native success is inferred from that attempt.

ROOT separately approved only the existing `_LEGACY_NO_JOB` literal in
[issue #326](https://github.com/YouthOpps/data-pipeline/issues/326),
comment 6213925496.
The exact historical run 38001485160, attempt 1, is completed/cancelled at head
`2d6491ddf2a37047efc7b996f190f648e647c0c6`, created
`2026-10-09T22:51:57Z`, with the authenticated complete empty job inventory
SHA-256 `b512fdf501ffbdb48c1ff8a334798223dc87ae5ec6e009140ec9c2633b967b7f`.
It remains in the full history as a no-executed-job observation; no job or
publisher completion is fabricated from created/updated timestamps. Only
this exact wrapper/attempt/head/conclusion/created time/job digest is excluded
from executed-completion ranking. An unknown or changed empty-job attempt
still fails closed.

That cancellation-only correction kept every source function, original test,
workflow, LOCAL/HOSTED/reentry literals, approved seed and budget unchanged.
Ordinary controls use the retained authentic cancelled wrapper and
empty job payload via private API stubs, plus exact mismatch negatives. The
original FAIL receipt, original tested source and RAM keeper are preserved.
No retake, publisher request, namespace access, workflow action, production
approval or corrected-run execution is granted by this binding. ROOT must
review current continuity and issue a separate explicit execution release.


### Second observer failure and stronger current LOCAL boundary

The second separately released original test used source `86d3c7e6` and also
remains FAIL. It received four responses (200, 200, robots404, 200) and completed
14 successful state-save calls. Its 26 partial award records and 17 catalogue
date tuples do not establish a complete source refresh. The external observer
could not serialize real `datetime.date` catalogue fields and stopped before
the first PhD detail. The third observer fixes only captured-copy encoding and
formal failure receipts; it does not change source parsing or data objects.

A subsequent read-only check also failed its metadata acknowledgement because
Node rounded large nanosecond generation integers. That failed check remains
preserved. A separately reviewed external correction retains canonical metadata
JSON bytes with base64, SHA-256 and length; no tolerance or reconstructed digits
replace exact generation evidence. ROOT alone repeated the read-only check.

ROOT approved the stronger LOCAL literal in
[comment 6215354159](https://github.com/YouthOpps/data-pipeline/issues/326#issuecomment-6215354159).
The fresh complete 28-attempt/job history and zero-artifact inventories bracket
an unchanged, settled mature journal and the original legacy publisher state.
The bound MAX now retains all 54 timing observations: the original 30 plus six
kinds of timing evidence for each of four actual physical requests. These are
not 54 requests. Every retained save witness, stronger constraint, historical
access/429/hold unknown and fresh quiet guard remains represented; no history
was aged away or truncated. The compact state SHA-256 is
`3dde0d8b8fa2ac1d4cbe2e988b634c900abd68f96f36a134a20663cbfc42e466`.

At that binding stage, only the existing LOCAL literal and README changed.
The ordinary mature restore already merged existing and incoming constraints;
no new reentry exception or namespace reset was introduced. Source functions,
original test, workflow, HOSTED and unused mature-reentry markers were unchanged.
The two native failures and failed read-only check remain immutable. That
stronger binding granted no third test or publisher access: ROOT still had to
provide fresh consumer, resource and exact initial-object checks and a separate
public third original-native execution release. The partial collection did not
establish production success.


The third original native test passed on immutable source `6dba24c2`, after two
separately preserved failed attempts. All 21 retained responses yielded the
complete 26 award overviews and 17 doctoral adverts (43 records); all fields and
65 successful persistence steps were checked from private RAM. The 180 retained
timing observations include the earlier 54; they are not 180 source requests.

A subsequent pure correction aligns only date-only application status with the
existing conservative UTC−12 cleanup calendar. This is an index policy, not a
publisher-stated Danish timezone. Four calendar dates, twelve explicit-offset
instants and twenty-seven null deadlines keep their original precision; the
conflicting doctoral advert remains unknown. Closing-day and next-day controls
plus all retained inputs qualify this helper correction. The current 43 records
remain unchanged; no second execution of the successful original native test
was made for this new helper. All 26 award overviews remain unknown rather than
being asserted as currently open calls. Transport, full graph, publication,
limits, migration literals, workflow and the original test are unchanged.


### Approved finite first HOSTED continuity

[ROOT approval](https://github.com/YouthOpps/data-pipeline/issues/326#issuecomment-6216556689)
binds the existing six-key HOSTED migration literal to the full 180 known timing
observations and the fresh read-only continuity guard. Its compact state hash is
`32c4c3331f34cee7bec72b1f2ab1aa5eae5e67441958b86c9524465c93e214ae`.
The observations include the earlier 54 plus six clocks for each of 21 actual
third-test responses; they are not 180 publisher requests. Unknown access, rate
and hold histories remain explicit; no observations were aged away or truncated.

The actual LOCAL mature journal and original publisher state remain separate
from the prospective first HOSTED original-namespace absence contract. The latter
has compact hash
`fea4e334fd7dc012aac874a9b0ff68666ebcb63d256e4cce13ed1c20ced67ab4`;
no LOCAL inode authority transfers to a fresh runner. The original source had no
separate collection lock. Existing LOCAL and unused mature-reentry literals are
unchanged. The actual marker keys include `source`, not a donor `family` field.

This source has no maintenance environment reader or four-key maintenance
packet. The workflow remains byte-for-byte unchanged, including the existing
publication token and `UCPH_PACING_ARTIFACT_PATH` configuration. Original startup
authenticates and restores continuity before its data-source snapshot. Genuine
third native success remains attributed to immutable `6dba24c2`; the subsequent
pure UTC−12 calendar-status correction is qualified by the retained-input replay
described above. No further source/native run or production success is asserted
by this literal binding. ROOT owns controlled enable/merge, the sole automatic
source Action and artifact/site delivery after independent configuration review.

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
publication, workflow and original test remain unchanged. External private
controls exercise actual source functions with networking denied and explicitly
synthetic admission/metadata/archive inputs. They are not publisher or hosted
provenance. Historical native success and its qualified expiry projection remain
attributed above and to independent PR #341 review 5548637554 at exact commit
`3865398a250d029a79941c3058bda8e1e30be7f0`. Historical raw input is no longer
available; no new native collection, complete factual replay, current-consumer
pass, state reset, workflow activation or production recovery is claimed.

Each successful truncate, short write, and fsync now receives an exact-byte
readback acknowledgement before the next mutation. Failure invalidation also
requires that last acknowledged full generation; an external stronger generation
arriving during a partial write is preserved and makes the run fail closed.

## Missing-state continuity recovery (#374)

The 18 completed attempts from run `41063528294` through `43906352876`
failed before publisher transport. Their complete job logs show the first
`Untrusted completed family attempt` failure and 17 subsequent
`Newest state missing; no older fallback` failures. Recovery pins every exact
run, attempt, head, job ID, start/completion timestamp, source blob and workflow
blob. It requires the entire contiguous reviewed set to reach owned run
`40883890576/1`, artifact `12902046316`, ZIP SHA-256
`c0183d9a1c12a7bc53bca75bbdb672eb2a1b64af0449bf74d8af19e9ec4fa166`.
Every skipped run must still have a complete, empty artifact inventory.

Future recovery is narrower than general artifact fallback: the failed run must
be attempt 1, contain exactly one completed failed `collect` job lasting less
than 58 seconds, and use the exact current adapter bytes plus the reviewed
workflow blob. The actual publisher transport now waits at least 60 monotonic
seconds after successful restoration, before reserving a publisher start; it
checks that guard again immediately before opening the request. Wall-clock
changes cannot shorten this first-access floor. Unknown revisions, reruns,
longer jobs, unexpected artifacts and incomplete evidence stop collection.

The original nine-field artifact envelope decoder remains authoritative,
including producer source and state hashes. Restoring the selected owned state
preserves all MAX constraints, pending/refusal flags and unknown history. Job
completion only adds a conservative quiet bound, never fabricated request
receipts. The last observed family identity and complete history remain those
of the newest attempt, so the existing first-access recheck detects new work.

This repair does not certify a successful collection. The last owned run
reached the scholarship page but failed with `Reviewed funding inventory
changed`; its journal is valid independently of that parser failure. The
workflow is paused pending independent pre-run review and one fully observed
native test. Any initial native failure and actual inputs must be retained for
parser diagnosis before another separately reviewed attempt. Offline controls
verify exact recovery, rejection cases, the original owned ZIP decoder, MAX
preservation and the monotonic clock guard; they do not replace native evidence.


### First recovery native outcome and semantic repair

The first original test on source SHA-256
`c91bdeafe8d9d2bd3037d487b2d2b7770838a98cd374d0e9d4cbc4f977b20da9`
failed after two successful publisher responses with
`Reviewed scholarship conditions changed`. Its original FAIL is immutable;
complete response bodies, headers, physical timings, eight persistence attempts
and stdout/stderr remain in the private RAM observer. No observer error occurred.
The actual page revealed the central scholarship policy change and removal of
Indonesian Education Scholarship, OFID and Women Techmakers from the external
inventory. Offline replay of that complete page verifies the new 21-record
scholarship subset, current consumer envelopes and stable retained identities.
It does not certify the unvisited doctoral catalogue or a complete live test.
A separate pre-run review is required before another genuine collection.


### Official doctoral inventory route recovery

The second original native test on source `8925bf0e` passed scholarship parsing
but failed at the missing `/phd/` table. Its complete four-response capture and
original FAIL remain separately preserved. A bounded research pass followed the
already observed official `/all-vacancies/` link and found 37 complete table rows,
including seven explicit doctoral titles, with no next-page link. All seven
observed links carry the same native numeric `show` identity and the current
`/all-vacancies/` route. This is a category-route change, not removal of doctoral
coverage or proof that no doctoral opportunities exist.

The old category page also links Jobnet. During scoped research, Jobnet's
`robots.txt` returned a 307 redirect to an authentication route. The original
robots-purpose guard stopped before that redirect; neither authentication nor
Jobnet job content was requested. That research outcome and all pacing history
are retained. Jobnet is not included in the delivered collector: the complete
same-university all-vacancies route supplies the required inventory.

The seven current detail pages require real observation and a separately
reviewed complete native test before acceptance. No previous FAIL is relabelled
as a pass, and no partial scholarship result replaces the published snapshot.


### Current detail evidence and midnight deadlines

The bounded same-publisher research pass obtained all seven actual current
selected detail pages and retained their complete bodies privately in RAM.
Six describe direct PhD recruitment. The seventh, `show=172290`, is an initial
one-year research-assistant appointment with a possible funded PhD transition
subject to evaluation and mutual agreement. It is classified as a job pathway,
not an immediate fellowship; its own title and conditional-route evidence remain
visible.

That notice explicitly states `29 November 2026, 24:00 CET`. End-of-day 24:00
is normalized to midnight of the following local day, then converted using the
publisher's explicit offset: `2026-11-29T23:00:00.000Z`. Invalid hours/minutes
are rejected; explicit AM/PM clocks are normalized before conversion. The
listing's original calendar day remains the comparison date, so ordinary 24:00
does not create a false date conflict. Unsupported zones are not invented.

Combined offline replay of the complete actual scholarship page, complete
37-row inventory and all seven selected detail responses yields 28 validated
records. The actual current website consumer accepts all 28 raw and filtered
records and a valid empty envelope. All 19 previously published doctoral ID
formulas were checked against the actual old snapshot. This combined research
replay is not an original native PASS; both earlier original FAILs remain
unchanged and a separately reviewed complete native collection is still needed.


## Accepted native result and modern production handoff (#374)

The third original nonpublishing test passed on source SHA-256
`acf5aaa62dad84f3d7731d8fec957a7767b5cfedfad37d053112ec3242b9f813`
in 323.623 seconds. Independent QA accepted all 28 records: 21 programme
overviews, six direct doctoral roles and one research-assistant job with a
conditional PhD pathway. Every one of the 37 inventory rows and all seven
selected details was covered. Complete input replay matched substantive output;
the current website consumer accepted exact raw28/current28/valid-empty0 IDs.
No actual records in that successful collection were expired. The earlier two
original FAILs remain distinct and preserved.

The complete 11-response ledger and 35 persistence attempts passed independent
checks: at least 12.201670 seconds after completion before the next request,
at most five starts per rolling minute, and 30.004168 seconds between the two
inventory requests after completion. Crawler identity, startup quiet, journal
bytes and retained MAX/unknown flags passed without observer errors. See the
[accepted native evidence](https://github.com/YouthOpps/data-pipeline/issues/374#issuecomment-6925929088).

The matching workflow supplies `UCPH_PACING_MAINTENANCE`, a byte-exact,
SHA-256-pinned modern handoff envelope. It binds that accepted source and
original test, the real final-state file, the original modern owner
`40883890576/1`, artifact `12902046316`, and the canonical approved state digest
`db54ed71544a88bb07a1b60eff7ebea6fe5fda8de234334488faba075cecca99`.
The seed retains all five native/research final MAX states, their observed durable
reservations and all 28 actual physical starts: 58 distinct timestamps. It adds
no fabricated requests. Settled final outcomes determine pending/refusal flags;
intermediate pending reservations are not mistaken for unresolved requests.
The robots-only Jobnet authentication redirect remains documented above and in
retained research evidence; Jobnet is not a runtime publisher for this adapter.

The original nine-field owned-artifact decoder must succeed before this seed is
considered. The loader then uses only the existing MAX, union and OR merge, so
stronger remote constraints, refusals, pending state and unknown history survive.
Missing or altered hosted approval fails before publisher access. A legacy-only
history cannot replace the required modern owned artifact. The finite original
18-attempt proof keeps its historical workflow binding; future short-attempt
proofs accept only the reviewed workflow that includes this seed.

The accepted native source is preserved byte-for-byte outside the repository.
Collection, publisher transport, parsing, expiry, metadata and publication
functions remain unchanged by this handoff. Focused offline controls cover the
new loader through the actual original ZIP decoder, corrupt ownership/seed
refusals, stronger state, pending-state refusal and unchanged local behavior.
This qualifies the accepted native evidence for reuse without another publisher
run. Final configuration review and the authorized post-merge publication remain
separate from the successful nonpublishing test.
