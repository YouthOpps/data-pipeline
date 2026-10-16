# Opportunity Desk

Collect the complete current [official RSS
feed](https://opportunitydesk.org/feed/) window, preserving each genuine
individual announcement's original article URL, title, publication timestamp
and publisher taxonomy. This is a feed-scoped inventory, not a crawl of the
entire archive or every programme linked from an article. An earlier reviewed
feed exposed ten recent posts and short descriptions, without
`content:encoded`; that historical observation is not a current count or a
collection cap. The window changes as the publisher adds posts.

## Access and attribution

The earlier reviewed public [robots
policy](https://opportunitydesk.org/robots.txt) permitted the feed and article
routes; that observation does not grant current access. Collection rechecks the
current policy and checks every redirected endpoint before following it. No
login, JavaScript, CAPTCHA or restricted endpoint is used. The publisher's
[disclaimer](https://opportunitydesk.org/disclaimer/) describes a free
information resource, warns that its inventory is nonexhaustive and directs
readers to the original provider for authoritative current information. That
statement is not an express copyright licence. This adapter publishes
attributed factual titles, links, taxonomy and short factual deadline labels,
without copying article prose, images or full descriptions. The [contact
page](https://opportunitydesk.org/contact/) is the publisher's route for rights
inquiries. No permission request or contact submission was made as part of
implementation.

## Inventory and quality decisions

Every RSS item is inspected against its public article body, specifically the
`post-content` element; menus, related posts and scripts are excluded. Feed
tags provide classification, and actual application/eligibility/deadline
content is required before an item becomes an opportunity. Unsupported taxonomy
or changed article markup fails collection rather than publishing guessed
records.

Editorial taxonomy (`Our Blog`, `Blog`, `General Tips`, `How-To`, `Success
Stories`, `Young Person of the Month`) is excluded. Multi-opportunity roundups
are also excluded: plural opportunity inventory in the title must be
corroborated by multiple numbered article entries. The October 7, 2026
scholarship roundup contains 27 separate numbered calls and is not one
scholarship. Its children are outside this feed's individual-announcement
scope; the adapter does not expand that roundup or claim complete historical
coverage. Exclusions are logged to stderr. A feed consisting only of exclusions
is a failed empty collection.

Awards and contests map to `competitions`; the actual Global Teacher Prize and
Global Schools Prize articles corroborate their `Awards` taxonomy with prize,
eligibility and application sections. Training, scholarships, fellowships,
internships, volunteering, grants and jobs use the corresponding exact
taxonomy. If category tags are absent, the reviewed programme fallback requires
uniquely ordered Benefits, Eligibility and Application sections in the actual
article. Explicit free online self-paced courses, expert workshops and
competitive finalist grants must all appear as separate benefit statements
before the call can be classified as `training` and `grants`. This handles the
MENA Regenerative Agriculture Venture Programme announcement without assuming
every applicant receives its conditional top-three finalist grants. A title,
continent tags, incidental funding mention or incomplete benefit evidence
cannot trigger the fallback; unresolved items still fail the entire collection.

The UN Voluntary Fund for Indigenous Peoples call uses `Conferences` taxonomy
but offers conditional financial support. Its grant classification requires the
actual UNPFII and EMRIP session evidence, explicit selected-representative
travel arrangements and stipend, Indigenous applicant eligibility, financial
need, community mandate and application-form instructions. Both supported UN
sessions remain one application announcement; the record does not promise
funding to all applicants or infer nationality from Indigenous identity or
meeting locations. Missing or contradictory material evidence remains an
unresolved failure.

A genuine conference/ambassador call with only `Conferences` taxonomy uses the
existing `other` category with `kind=opportunity` and classified provenance,
not an unknown kind. The reviewed Hamburg Sustainability Conference Youth
Ambassador article has explicit applications, eligibility, funded participation
and a deadline. It may fall outside the moving feed window in a later run.

Classification evidence is the actual article URL, not unattached tag text.
Identifiers remain the first 24 SHA-256 hexadecimal characters of
`opportunitydesk|<original article URL>`. Tag ordering, award quantities and
feed position do not affect identity. Feed duplicates or unexpected article
hosts fail collection. Separate programmes bundled in one genuine application
call remain one announcement, such as Stanford CISAC's fellowship tracks.

Country eligibility uses explicit, completely recognized citizenship lists in
article eligibility text. Hosts use narrowly supported physical-location text:
Washington, DC internships, a required NYC fellowship stay, and an explicit
Denmark host-institution stay. Work authorization does not imply citizenship;
Danish institutional association does not imply mandatory Danish citizenship.
Names, continent tags and worldwide visibility do not establish eligibility or
hosts. Other unsupported country fields remain empty/null. RSS publication
dates retain their explicit timezone. Article calendar-only deadlines and
rolling/ongoing statements are preserved as short factual summary labels.
Explicit application-closing dates with a clock and GMT/UTC zone become precise
`deadline` timestamps. Explicit ET submission clocks use America/New_York with
a matching dated deadline header; DST transitions are handled by the regional
timezone, and ambiguous/nonexistent clocks are rejected. Their status follows
that instant; conflicting instants remain unresolved. Calendar-only or unzoned
dates retain null deadlines. Otherwise status remains unknown rather than
guessing a closing instant or guaranteeing that an aggregator's open wording is
current.

## Run and publication

```sh
python -B adapters/opportunitydesk/test_adapter.py
python -B adapters/opportunitydesk/adapter.py --publish
```

The single live test retrieves and validates genuine nonempty records and
prints one complete JSON list to stdout; diagnostics go to stderr. It has no
mocks, fixtures, output files or publication token. Publication is for
authorized production automation only, using `DATA_SOURCE_TOKEN`.

Publisher requests now use the source-local durable protocol described below.
The original GitHub publication transport retains its separate six-second, ten-
start rolling-minute gate and valid `Retry-After` handling. The workflow
serializes this source's publication; LOCAL and hosted collections also require
the reviewed complete family history and source-specific continuity boundary.

A successful publication atomically replaces only
`datas/opportunitydesk/data.json` and `metadata.json`, using a Git tree/commit
and non-forced ref update. Conflicts retry only when this source is unchanged.
Prior last-good records and success timestamps survive collection or
publication failure; failure metadata is saved when safe. First-run failure
creates no source folder. Failure to durably record metadata is reported
explicitly in logs.

The matching workflow runs on changes to this adapter or its own workflow on
`main` and once daily at 01:48 UTC, preserves manual dispatch and the
repository/main guard, and executes only this adapter's publication command. No
production Action is used as an implementation test.

## Expired-record cleanup

A successful refresh validates the complete nonempty collected inventory, then
excludes records whose supported `deadline` has passed before preparing the
atomic source data/metadata replacement. Previously published records are used
only to preserve matching records' first-seen timestamps; they are never merged
back into the replacement. Thus expired old records cannot survive a successful
replacement. Missing deadlines, ambiguous clocks, status, age and absence from
the response are not expiry evidence.

Explicit timezone-aware deadlines expire strictly after their instant. For
date-only deadlines without a documented publisher timezone, cleanup waits
until the closing day has ended in every legal timezone: the current date in
UTC-12 must be later than the closing date. This conservative boundary retains
the full closing day without inventing a publisher timezone; deletion can be
delayed up to 26 hours relative to the earliest civil closing-day boundary.
Unsupported or ambiguous source clocks remain null deadlines; invalid timestamp
fields fail validation before cleanup. If every collected record is expired,
the valid nonempty retrieval publishes an empty successful replacement. A later
empty upstream retrieval still fails and preserves that last-good pair.

Fetch, parse, validation and publication failures preserve last-good data and
success timestamps. Cleanup performs no separate deletion or remote write;
publication updates only this source's data/metadata atomically, with the
existing concurrent-update protection. The live test collects real inventory
without publication; offline boundary checks and static publication review
verify cleanup.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project and
provides contact and indexing-removal information. This identity does not grant
source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The identity
and original fact parser remain preserved; the new publisher pacing and durable
state are described below. No workflow activation is authorized by this offline
implementation.

## Daily automation

The scheduled refresh runs once daily at **01:48 UTC** (`48 1 * * *`). This
fixed time was selected randomly within a staggered daily timetable; it is not
randomized again on each run. Main-merge and manual triggers remain available,
so this is one scheduled run per day, not a hard limit on all runs. GitHub can
delay scheduled events; the existing publication concurrency serializes source
jobs when they queue. Existing disabled workflows remain disabled until their
access hold is separately resolved.

The complete current feed is the inventory operation and article reads are
details. The earlier daily-schedule change did not alter traffic policy; the
source-local migration below now strengthens request and inventory pacing.

## Workflow concurrency

`youthopps-opportunitydesk-publisher-v1` serializes runs of this source. Up to
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
three-attempt limit, current-head and source-pair conflict checks, atomic
writes and successful collection/publication behavior are unchanged. This
diagnostic maintenance does not establish the underlying cause of older generic
failures.

## Source display name

Published metadata uses `display_name` = **Opportunity Desk** for the visible
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
attempts, source-pair checks and non-forced atomic writes are unchanged by
those diagnostic categories. Those categories alone add no retry delay,
workflow activation or publication test.

## Bounded publication-conflict retries

Hosted publication proves the exact current job, run attempt, source workflow,
matching head/workflow SHA, main branch, assigned runner and UTC start before
data-source access. A fresh GitHub Date maps the existing 30-minute job bound
to one conservative monotonic deadline; retries cannot renew it. The pinned App
token is minted after job start and expires after one hour, so no token renewal
is needed. Missing or inconsistent proof fails closed.

Every guarded publication call and the current-job proof call on the reviewed
Ubuntu/Python path has a total alarm guard of at most 30 seconds, plus a
remaining-time socket timeout, including its bounded body read. Existing or
unsupported timers stop before transport. Writes and retry waits stop 60
seconds before the job deadline, final outcome reads stop 30 seconds before it,
leaving a cleanup/export allowance. These publication reserves do not guarantee
export after an overlong collection, runner death or an uninterruptible native
call; the existing job timeout remains the outer bound. No API call receives a
fresh deadline.

Only an actual, classified non-fast-forward reference response can retry, after
checking that the desired outcome has not landed and this source pair is still
unchanged. The first two retries add 1–2 and 2–4 seconds after the ordinary
GitHub pacing gate, before reserving the actual request start, within the
existing three attempts and original write deadline. Authentication, non-CAS
and unclassified failures stop without another write attempt. Terminal
publication errors latch a per-process write stop, including later failure-
metadata fallback; bounded read-only outcome checks remain available. Source-
pair changes stay protected; reference writes keep force:false. Those
publication-conflict safeguards alone did not change collection, fields,
pacing, schedule or the 30-minute job cap. The migration below changes only the
separately documented source transport and phase admission. Validation uses
inert external controls; no source fetch, GitHub test write or production
invocation is claimed.


### Earlier received-429 maintenance

The earlier #307 maintenance already stopped a received publisher HTTP 429
before reading its body and kept a close failure from the ordinary retry. Its
default GitHub path was preserved. That earlier local JSON/flush-only
maintenance did not add adaptive gaps, an exponential minimum hold, fsync,
pending ownership or hosted continuity. No new observed publisher 429 incident
is claimed by either change.

## Source-local durable pacing migration (#332)

This offline implementation preserves the complete original RSS reader, every
article fetch before exclusion, source IDs and fields, article date parsing,
expiry, the original sole live test and atomic publication/CAS bodies. There is
no count cap, first-known stop or fitting prefix. A healthy no-redirect feed
with N items requires REP, the feed and all N articles; aliases, redirects and
permitted retries can add physical requests. An empty or all-excluded feed
remains invalid. No current count, complete genuine native/body chain, fresh
source facts or actual full-duration fit is established here.

All physical publisher starts require a durable pending owner, at least 12.2
seconds after actual completion and at most five starts in any rolling 60
seconds. Feed operations, including every redirect or eligible retry, also
respect at least 30 seconds from inventory completion. The first feed retains
the stronger current completion gap. Details and literal REP retain physical
pacing; they are not all promoted to inventory. Restoration adds a nonrenewable
60-second quiet period and rechecks the complete history after pacing before
the first physical wire in both modes.

A received 429 durably increments the migration-era strike count, doubles BOTH
CURRENT physical and inventory gaps, and retains the maximum of all existing
holds, valid longer `Retry-After` and at least 1,800 seconds times the strike
multiplier. It then closes and stops before any body read. Any unsuccessful
close, response/body assessment, arithmetic or persistence leaves pending and
sticky failure. An invalid `Retry-After` alone grants no additional hold.
Literal REP 4xx except 429 is unavailable policy, not a content denial; known
denials and stronger timing remain. REP 5xx, TLS or unsupported policy never
creates an allow. Applicable finite decimal Crawl-delay/Request-rate values are
retained before completion. Current-specific-or-wildcard groups and any actual
legacy-specific groups are selected independently, then intersected.

Every fresh or cached target assessment durably binds its actual owner, nonce,
target URL and REP body digest to the resulting decision before the owner can
settle. Each assessed physical response has a request/owner/target receipt; a
header-only 429 receipt explicitly claims no body bytes. Retained per-origin
allow samples are non-authoritative: each real target is assessed again. Denial
scopes join losslessly, alongside all stronger numeric MAX and unknown history
flags. No denied scope or feed item is truncated to fit. The 32 KiB state and
bounded artifact reader fail closed if their evidence cannot fit; these storage
bounds do not define a smaller source frontier.

The ordinary connection loop retains its actual maximum of TWO attempts. Retry
permission is intentionally narrower than the old broad exception tuple: only
an actual opener-started, typed temporary pre-response network fault can retry.
TLS/certificate errors, permanent or untyped DNS errors, storage faults,
received-body failures and close/policy failures remain unassessed and cannot
retry. Original default GitHub transport, valid Retry-After, six-second pacing,
authentic current-job proof, 30-second call guard and three classified CAS
attempts remain unchanged except narrow deadline composition.

The new publisher namespace is
`/tmp/youthopps-opportunitydesk-publisher-v1-UID`, with retained 0600 journal,
stable state lock and a lock for the full collection. Verified parent/current inode and generation checks surround
reads, writes and prewire admission. Writes use the original retained RW inode,
serialize before mutation, fsync and read back the exact bytes. This retained
in-place crash tradeoff is explicit: an interrupted or corrupt mature journal
is held, and an older owned ZIP cannot repair it. Failure cleanup mutates only
retained original authority. Guarded exports invalidate a safe owned stale
artifact before signal I/O; unsafe or unrelated objects do not become cleanup
authority.

The original legacy publisher path is the bare normalized-host budget file
`/tmp/youthopps-<first24 SHA256(opportunitydesk|opportunitydesk.org)>.budget`;
its JSON file itself holds flock, with starts/interval/until and flush only.
The original separate collection lock is `/tmp/opportunitydesk.lock`. There was
no old publisher directory or separate old state.lock. A finite LOCAL
transition needs exact read-only observations of both original paths; explicit
absence means unobserved ancestry, never zero requests. A fresh hosted runner
requires its separately approved absence boundary. Unknown rate and policy
ancestry remain true; the strike count describes only since-migration events.

At initial offline delivery, LOCAL and HOSTED finite literals were None, the
workflow identity was unset and exact legacy attempt pins were empty.
The earlier LOCAL boundary preceded the HOSTED approval described below.
Complete workflow wrappers, ALL actual attempts and jobs, completion chronology
and exact artifact ownership are required. Other queued/nonterminal publishers,
missing mature state or missing/expired/corrupt newest modern artifacts fail
closed without an older fallback. Modern LOCAL restoration also refuses a
wholly absent family namespace; a newer owned remote state is not authority
to recreate it. Ordinary fresh HOSTED runner restoration remains supported.
New owned envelopes bind seven exact keys:
schema, repository, run ID, attempt, source, head SHA and state. The matching
workflow's four-key `OPPORTUNITYDESK_PACING_MAINTENANCE` packet is mandatory
for both first and later HOSTED entry; its evidence/state now match the
separate ROOT HOSTED boundary below. Missing or invalid approval stops before
state creation, publication proof or data-source snapshots.

The original 1,800-second job and 01:48 UTC schedule remain. This migration
introduces NEW, stricter 1,740-second whole-entry and 1,200-second source
limits; the baseline had neither dedicated limit. Entry is frozen once before
setup proof/history/restore/snapshot. Restore up to 180 and initial snapshot up
to 120 debit that same lease. Source admission reserves publication 180,
failure 180 and export 30; publication cannot borrow failure/export. Failure
begins before the first fallback read or failure metadata write and never
renews. Authentic original job write/read cutoffs further clamp every phase. No
outer SIGALRM competes with the original publication call guard. Per-operation
source I/O is bounded, while arbitrary CPU/runner-death fit is not guaranteed.
Longer feeds, bodies, redirects, stronger REP or a 429 can exhaust the budget;
the result is failure with last-good data, never a smaller collected prefix.

Private ordinary controls exercise actual full-reader synthetic RSS/article
inputs, first/later workflow/main/owned ZIP restoration, authentic proof and
original GitHub/CAS composition, receipts, selected REP, typed no-response
retry, failed persistence, strict mature state, expiry/helper ISO contracts,
source-specific consumer negatives and real-clock inert pacing. Synthetic
fixtures are outside the adapter directory and do not establish actual source
facts. No publisher, actual namespace, GitHub API, owned artifact download,
workflow mutation, original live test or native test is used for this offline
qualification. Future root continuity, one separately released original native
and complete retained factual/date review remain prerequisites to access and
production approval.


## Approved finite LOCAL continuity boundary (#332)

[ROOT approval](https://github.com/YouthOpps/data-pipeline/issues/332#issuecomment-6214861381)
binds workflow `378391285`, all 31 exact historical attempts and full job pins,
and the six-key LOCAL research migration. Thirty actual job completion
upperbounds are conservative timing observations; the one jobless cancellation
contributes no fabricated time. All artifact inventories were empty. Original
publisher, GitHub, collection and modern namespaces were absent/UNKNOWN, not
proof of zero earlier rate, hold or policy history. Both unknown flags remain
true; false/null/count-zero fields are prospective, not historical settlement.

The approved state has compact NO-LF SHA-256
`71934fa468e4493e538ed2fdaa1eeaacea441f9955a67dd8b418906878d0020a`.
Its exact source-local absence contract separately names publisher and
collection objects; no donor directory contract or generic reset is used.
Missing mature LOCAL state remains fail-closed. HOSTED was unset at this
earlier LOCAL-only boundary; its subsequent approval is described below.

All source functions, business flow, original test, workflow, pacing, limits
and publication remain unchanged by this literal binding. This approval does
not release a native run or publisher access. ROOT must separately bind fresh
full history, initial objects, current consumer and resource clocks, then
release the one original native test. No original native evidence is claimed
by this binding.


The first original native test passed on source `1add0ca0`: all ten RSS articles
were fetched before two roundup exclusions, retaining eight records. All 12
responses and 120 exact persistence steps were reviewed from private RAM. The
observed response-completion spacing was at least 12.2145 seconds; one inventory
request supplies no empirical inter-inventory timing claim. All eight deadlines
remain unknown: future publisher deadline headers are preserved in summaries.
APDD mixes a 2027 header with 2026/2027 programme text; WWF has an explicit ET
closing clause beyond the original narrow parser. These qualifications do not
establish that every call is currently open or authorize invented dates.

ROOT's [HOSTED approval](https://github.com/YouthOpps/data-pipeline/issues/332#issuecomment-6215873880)
binds the full 114 known timing observations and both unknown-history flags.
Those observations include 30 ancestral job bounds and seven actual clocks for
each of 12 requests; they are not 114 publisher requests. The HOSTED literal and
required four-key `OPPORTUNITYDESK_PACING_MAINTENANCE` workflow value use the same
reviewed state and evidence. The earlier LOCAL literal remains unchanged; its
actual mature inode is not transferred to a fresh HOSTED runner. The 1,200-second
source and 1,740-second entry bounds remain intentionally stricter allocations
within the original 30-minute job. Production execution and delivery are separate
ROOT-controlled steps; this configuration does not assert hosted completion.
