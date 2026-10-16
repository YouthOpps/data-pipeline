# Fulbright Denmark

This standalone standard-library adapter traverses the publisher's current
[sitemap index](https://fulbrightcenter.dk/sitemap.xml) and every declared page
sitemap partition. It discovers public Danish and American programme pages,
the historical Arctic III framework, host awards and Book a Scholar. Own links
on those pages extend discovery. Recommendation-card changes and new programme
URLs no longer have to match a frozen URL list.

## Live extraction

The collector reads current programme titles, academic years, award sections,
funding, eligibility and deadline fields from HTML. Joint grants, Inger P.
Davis recipient groups, Crown awards, Thanks to Scandinavia, Schuman tracks,
maritime awards and Specialist applicant roles have separate records based on
their live headings and descriptions. It follows an explicitly linked shared
core-student application route for a dependent programme's deadline.

Dates require a published year. Explicit noon CET is normalized to 11:00 UTC;
date-only values stay date-only, and missing/yearless/ambiguous dates stay null.
Historical and closed programmes are not silently discarded. Countries use
the programme direction and explicit eligibility wording; institutional hosts
do not imply nationality requirements for individuals.

Small compatibility maps retain the former identity keys for the same awards.
They contain no cached titles, years, eligibility or funding. New programme
and track identities derive deterministically from the URL and heading.
Publication preserves first-seen and created timestamps for surviving IDs and
advances updated-at only when normalized content changes.

## Access and exclusions

Publisher requests use the identifying YouthOppsBot user agent and ordinary
TLS/proxy settings. Physical starts and previous-completion-to-next-start gaps
are at least 12.2 seconds, with at most five starts per rolling minute. Every
physical sitemap inventory attempt, including redirects and ordinary retries,
also waits 30 seconds after the preceding inventory attempt completes. Literal
robots requests use physical pacing; programme and shared-deadline detail pages
are not relabelled as inventory. Stronger publisher policy and retained state
always take precedence. LOCAL and hosted runs share the source-family queue
and durable-state protocol; local collection requires the workflow to remain
paused through the quiet period and final pre-wire check. Only own public
pages are fetched, never forms, portals, biographies or external catalogues.

The current Arctic IV page still requires external material under
[IIE terms](https://www.iie.org/terms-and-conditions/) that prohibit automated
collection. That route remains excluded; the own historical Arctic III page
is distinct. No external IIE collection or permission outreach is performed.

The publisher's privacy notice is not an open content licence. Records carry
publisher attribution and original evidence links, with short factual funding,
calendar and eligibility excerpts rather than biographies or complete pages.

The former static profiles and exact page, sitemap and PDF hashes are removed.
PDFs are not parsed by this standard-library HTML collector. Opportunities or
conditions available only in attachments are outside the extracted inventory;
the output does not claim coverage of those documents or external catalogues.
Bounded summaries are not complete application guidance. Missing required HTML
structures, access failures and invalid/empty collections still fail safely.

## Run and publication

`python -B adapters/dk-fulbright-center/test_adapter.py` runs the sole live,
nonpublishing test and emits every validated record as one JSON document on
stdout. The caller validates complete stdout in memory, without writing data
files. Counts may change with the publisher's inventory.

The source workflow retains filtered main-branch pushes, its schedule
and manual dispatch. Its sole application command is `adapter.py --publish`.
Source-scoped GitHub App credentials and source-family publication
serialization are preserved. Publication atomically updates only this source's records/metadata;
failures preserve last-good data, and same-source conflicts reject overwrite.
Local live tests do not certify production publication.

## Expired-record cleanup

Fulbright Denmark preserves supported application dates and explicit closing
clock/zone evidence. Yearless, conflicting and unverified programme closings
remain null; award duration and host milestones are not expiry fields.

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
success timestamp; cleanup never performs a separate deletion or writes
another source.
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
pacing and backoff preserve all known restrictions through durable MAX/OR
restoration. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **11:31 UTC** (`31 11 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

The actual sitemap reader supplies inventory purpose explicitly. Each physical
inventory content attempt waits at least 30 seconds from the previous inventory
completion; retries and redirects retain that purpose. Robots remain distinct.
The original 100-sitemap traversal and 150-primary-programme guards remain.
Explicitly shared Core Student deadline dependencies can require additional
pages outside the primary programme cache, so those guards are not a total
physical-request guarantee. Full discovery and every dependent detail remain
required; no record cap, early-known shortcut or listing-depth reduction is
introduced. Native elapsed time and complete current frontier still require a
separately authorized full original test.

## Workflow concurrency

`youthopps-dk-fulbright-center-publisher-v1` serializes runs of this source. Up
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

Published metadata uses `display_name` = **Fulbright Denmark** for the visible
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

## Durable pacing migration (#305)

This implementation remains nonpublishing preparation. The maintainer approved
only the first LOCAL transition in [decision 6208426057](https://github.com/YouthOpps/data-pipeline/issues/305#issuecomment-6208426057).
The separate HOSTED transition is also now approved; see the decision below.
The approved LOCAL state preserves all
28 executed-job completion bounds and two actual old local pacing starts;
these 30 observations are not 30 known publisher requests. The actual original
publisher bucket is present and pinned by parent/file identity and exact raw
hash. Its stored interval is 6 seconds and its stored embargo is zero; unknown
historical rate-limit and REP-policy observations remain explicitly unknown.
The approved conservative state retains 12.2-second physical spacing and a
30-second inventory gap without claiming recovered lifetime counters. Read-only
recovery observed 31 exact completed attempts, including three pinned jobless
cancellations, and zero artifacts. Prior two-page diagnostics and publication
HTTP 422 logs are not complete native proof or proof of publisher denial.
No source access, native fit, publication or deployed result is certified by
these private controls. A separate exact maintainer native release is required.

Every LOCAL and hosted preparation restores the newest exact owned artifact
before publisher trust and before the initial data-source snapshot. The sole
first-transition route requires an explicit mode-specific public approval of
all exact legacy attempts, unknown history and a safe observation of the old
publisher bucket; missing artifacts do not imply a fresh allowance. LOCAL
approval binds the original bucket identity and bytes or its observed absence.
Hosted first transition requires an explicitly verified publisher bucket
absence on the GitHub-hosted runner. The legacy api.github.com bucket remains
separate and unchanged. Both old and incoming restrictions are merged by
numeric MAX, hold OR and durable unknown-history markers. A zero new-observation
counter never means zero lifetime rate failures. Mature missing, corrupt or
unsettled state cannot be healed from an older artifact or initial approval.

Before any physical source wire, an outer policy owner and physical request ID
are durably saved. The owner covers cached and fresh REP selection, restrictive
policy/numeric assessment, redirects, retries and complete body inspection.
Only assessed matching completions can settle it. Every failed reservation,
restrictive calculation/write or completion write is sticky; later source entry
and healthy export refuse. Journal writes retain a locked original RW inode,
verify its current name and generation, serialize before mutation, handle short
writes and fsync. This deliberately trades atomic-rename availability for
replacement safety: interruption can leave an empty/torn mature journal, which
fails closed rather than restoring weaker historical state.

A 429 whose body read and response close completed already stopped the old
same-run loop. The corrected error path now persists the counter, physical gap,
inventory gap and cooldown before reading
the response body. Each observed throttle doubles the current gaps and retains
at least the exponential 12.2/30-second floors and 1,800-second cooldown, plus
any longer valid Retry-After. Unusable headers never create a hard hold;
unsupported derived constraints remain unsettled. Actual content 401/403/451,
clear visible challenges and applicable REP Disallow retain hard holds. A
literal robots 4xx other than 429 means unavailable REP, without converting it
to content permission. REP selects current-product groups, else wildcard, and
also intersects an actually present legacy-product group. Longest path wins
within each selected policy and Allow wins equal specificity. Applicable finite
decimal crawl-delay/request-rate constraints retain their maximum; unsupported
applicable numerics stop technically, while unrelated groups do not constrain
this identity.

Two ordinary attempts and four redirect hops remain. The old broad transport
exception catch is narrowed to actual-opener-started, no-response temporary
connection/timeout/EAI_AGAIN failures. TLS, storage, received HTTP and partial-
body failures gain no retry. This is a deliberate error-path correction;
normal successful graph, facts, calendar precision and cleanup are unchanged.

The original 30-minute job, publication222 proof, token boundaries, GitHub
six-second/rolling-ten pacing, Retry-After and three CAS attempts remain.
New allocations are a 1,740-second whole execution and up to 1,200 seconds for
collection, dynamically clamped by the original job/write/read deadlines.
These are newly introduced source allocations, not previous baseline caps.
Proof/restore/initial snapshot receive at most 30/180/120 seconds, subject to
remaining reserves. Success publication and one nonrenewing failure owner each
reserve 180 seconds, with 30 seconds for export. Failure ownership starts before
the first fallback snapshot and covers all later fallback reads, writes and
outcome handling. No competing outer alarm is installed over the unchanged
per-call publication alarms. No Harno-style publisher cooldown is introduced;
the snapshot allocation is unrelated to source request pacing.

Owned artifact output retains original directory/file identities through
writing, readback, readiness signaling and close. Failure invalidates only the
verified original owned output before best-effort false signaling; unrelated
or unsafe replacements are preserved. The seven-key source/run/attempt/head
owned envelope and digest-bound newest ZIP restore are mandatory after the
first transition. Artifact uploads require the actual readiness output. This
private-runner trust boundary does not authenticate an arbitrary simultaneous
same-UID process forging an envelope after all final checks. No guarantee is
made for process death, unrecoverable filesystem failure or external tampering.

Later native readiness requires a detached 12-hour RAM keeper started before
the marker and original test; all headers, bodies, available partial bytes,
stdout, attempted saves and persisted snapshots remain in private RAM. The
controller rejects publication credential presence even when empty and binds
all three current consumer dependencies. Offline checks must use the original
DK expiry/metadata helper contracts and prove positive and wrong-source
consumer routing before the sole authorized original native run. Private
fixtures, current publication pairs and expired historical RAM do not replace
that complete source-native proof.

### Actual retained native qualification (16 October 2026)

The separately released original nonpublishing test passed once on source
`cb1ea33a`: 32 raw records and 24 complete publisher responses, with all actual
headers, bodies, stdout and 212 exact persisted-save byte snapshots retained in
private RAM. Whole observed duration was 453.934 seconds; minimum wire-start
spacing was 13.709 seconds, minimum prior-response-completion spacing was
12.240 seconds, and at most five starts occurred per rolling minute. The two
physical sitemap attempts retained a 30.197-second completion guard. This is
one genuine full source graph, including its extra shared-deadline routes.

Actual-clause review found three closed entries still retained by the original
filter: Arctic Initiative III explicitly says its competition is closed, and
the two Danish Inger P. Davis tracks explicitly say their listed 2023/2024
deadline passed; their advertised next 2024/2025 cycle is also historical.
A small pure closure helper now requires that programme's own publisher-bound
application clause rather than relying on an expired status or missing date.
The parser assesses the full source clause before assigning a closed status.
Cleanup requires that assessed status and its independently scoped closure; a
truncated summary cannot turn a correctly unknown reopening into a closure.
It retains the six announced 2028/2029 programmes as unknown, the two American
Inger tracks with externally delegated deadlines as unknown, and undated or
rolling cases. A different cycle's closure and an actual or undated reopening
announcement do not establish that the indexed programme is closed.

Two summary scopes were corrected using the same retained source bodies: the
Thanks to Scandinavia Master's track no longer treats the following music
scholarship heading as its award, and the institutional Specialist track uses
its host-institution eligibility rather than the individual specialist clause.
All 32 IDs, collection routes and other non-temporal fields are unchanged;
only the two Danish status values and these two summaries change. The full
collector was replayed from all retained programme/sitemap bodies; all 24
physical response hashes and headers were also inspected. The current consumer
qualifies 32 raw, 29 filtered, the actual three-entry closed subset and a
separately derived empty fixture. This offline replay is not a second native
run; the immutable original test and observer both remain PASS with 32/32.

Every original save's exact bytes/hash were checked. The 165 pending save
observations settle through 46 matched logical owners; policy-only owners are
not additional publisher requests. The proposed full known-state MAX retains
150 conservative timing observations and the MAX derived from 427 state
witnesses, not 150 source requests. Historical rate-limit and REP-policy unknown flags remain true.
The separate decision below authorizes that exact first HOSTED transition.
Production remains unverified.


### First HOSTED transition approval

The maintainer approved the exact six-key first-HOSTED marker in
[decision 6208791185](https://github.com/YouthOpps/data-pipeline/issues/305#issuecomment-6208791185).
Its full known-state MAX is `3b72d2c1` using sorted compact JSON plus LF; the
separate exact runtime-history digest `78a7d50b` uses sorted compact JSON with
no LF. It retains all 150 conservative timing observations and the MAX derived
from 427 constraint witnesses, the 12.2/30-second floors, stronger embargo and
both historical unknown flags. These observations are not 150 physical requests.

The HOSTED packet requires a real GitHub-hosted context and verifies absence of
the original publisher bucket on that host. It carries no LOCAL inode values;
unexpected existing publisher state fails rather than being cleared. Both
initial transitions are source-specific and pinned to the 31 reviewed exact
legacy attempts. After a modern family attempt, the newest exact owned pacing
artifact remains mandatory; initial approval cannot heal missing or unsettled
mature state. The original workflow, test and native observer are unchanged.
The maintainer owns activation, merge and the sole automatic production run.
No production publication or website delivery is claimed here.
