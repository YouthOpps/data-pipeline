# TalTech Development Fund scholarships

Source `ee-taltech` collects the complete [official Autumn 2026 scholarship call](https://taltech.ee/arengufond/stipendiumid/2026-sugis) published by the Tallinn University of Technology Development Fund. Python 3.12 or later and the standard library are sufficient. The [English scholarship directory](https://taltech.ee/en/scholarships) is a publisher reference, not an opportunity record or an authoritative current competition calendar.

## Access and reuse evidence

On 8 October 2026 the official seasonal page and [robots policy](https://taltech.ee/robots.txt) both returned HTTP 200 without login or an access challenge. Robots permit public pages but disallow `/api/*`. The complete award descriptions are already present in the server-rendered HTML, so the adapter does not access the disallowed API or execute/download application code. Original official statute PDFs are retained as links; they are not fetched. Application forms, student systems and applicant information are not accessed.

The linked [privacy policy](https://taltech.ee/en/privacy-policy) describes the university's processing of personal data and does not prohibit this reviewed public award collection route. No separate public reuse license or automated-collection prohibition was found in the reviewed official navigation, footer and policy. This does not assert unrestricted redistribution rights. Records retain the source ID, original statute URL and all supporting publisher anchors; metadata supplies the human-readable attribution.

Requests send the exact YouthOppsBot identity below, respect robots on fetched pages and redirects, and retain stronger Retry-After constraints. Publisher physical starts are at least 12.2 seconds apart and at most five in a rolling minute. Each physical inventory request, including redirects and connection retries, waits at least 30 seconds after the previous inventory response completes. Literal robots requests retain separate physical accounting. Local overlapping processes share the original host-hashed locked journal outside the checkout; local and hosted collection must not overlap across machines.

## Complete inventory and deduplication

The live inventory reviewed on 8 October 2026 contains 92 genuine scholarship cards and six FAQ cards. It covers the special competition, all-study-level awards, economics, IT, science, engineering subject groups, the Maritime Academy, and lecturer/researcher awards. There is no additional pagination: the complete accordion content is present in the fetched HTML.

The 92 award cards produce 81 distinct records. Nine repeated-statute groups have identical complete award descriptions after only Unicode, whitespace and punctuation-spacing normalization: the Marine Engineers Union, IT College, Swedbank, VKG bachelor's, Foreign Intelligence Service bachelor's, Wise, VKG master's, Foreign Intelligence Service master's and TREV-2. These account for eleven repeated cards. Their full descriptions explicitly preserve the shared allocation and eligibility across faculties; for example, Swedbank's one shared call describes both faculty allocations. Every duplicate publisher anchor is retained as evidence and every section label as a tag. Matching statute links alone do not justify merging: conflicting descriptions, deadlines, categories or nationality eligibility fail collection.

Different bachelor's/master's statutes remain distinct. The two Metrosert awards also remain distinct, including the explicitly named internship award. Secondary older PDF links, FAQ/application-document examples, homepage/directory metadata and general event/news descriptions are excluded. The English Development Fund overview still described the expired Spring 2026 competition when reviewed; the assigned official Estonian Autumn 2026 page takes precedence. Other general university aid programmes belong to different application routes and are outside this seasonal call's inventory.

## Identity, deadlines and eligibility

Stable IDs derive from source ID, the publisher's academic-season heading and the normalized original statute URL. Original URLs remain unchanged; source evidence points to the actual HTML accordion headings. Empty collections, duplicate IDs, unsafe URLs, invalid dates/countries/categories and ambiguous repeated awards fail validation.

The main competition explicitly accepts applications from 30 September through 19 October 2026. Its 73 distinct regular awards use `2026-10-19` as their date-only deadline. The source states no closing clock or timezone, so no timestamp is invented. The shared index date-only convention retains these records through the whole closing UTC calendar day. Peep Sürje has an explicit individual 12 October deadline, which overrides the calendar. The other seven special-competition awards lack a reliable individual deadline, so their deadlines remain null and their status unknown. Their mention of `30.09–20.10` concerns exclusion from the main competition's counting rules; it is not treated as their application deadline. Dated records become expired after their deadline rather than remaining perpetually open.

All records are scholarships; the Metrosert internship scholarship also carries the internships category. Estonia is the study/host country. Estonian nationality is recorded only where the award body explicitly requires it; university enrollment, local study or the publisher's country do not imply citizenship eligibility. Full academic, allocation and application conditions remain available through the original statute and publisher links. Publication dates are not invented from unrelated page dates; collection freshness timestamps are UTC.

## Run and live test

From the repository root:

```sh
python -B adapters/ee-taltech/adapter.py
python -B adapters/ee-taltech/test_adapter.py
```

The folder also works independently outside the repository. Its sole live test retrieves real nonempty data, validates every record and emits the complete result as one JSON document on stdout, with diagnostics on stderr. It receives no publication credentials and writes no output files. The implementation live test validated all 81 records in memory, including unique IDs/URLs, all 92 award-card coverage, duplicate provenance and special deadlines.

## Publication

The matching `fetch-ee-taltech.yml` Action runs automatically on main pushes affecting this adapter folder or its own workflow, including maintainer merges. Manual dispatch and a daily schedule (18:28 UTC) are available. The repository/main guard, source-family publication serialization, read-only pipeline permissions and the existing GitHub App's data-source-only contents-write token scope are retained. The sole application command is:

```sh
python -B adapters/ee-taltech/adapter.py --publish
```

Publication updates only `YouthOpps/data-source/datas/ee-taltech/{data,metadata}.json`, atomically and with conflict-safe reference updates. Successful publication of validated nonempty records advances the durable success timestamp. Later failures preserve last-good data and its success timestamp and record sanitized failure metadata when possible; failure before the first success creates no source folder. Tests never publish. Production publication requires maintainer merge and was not invoked during implementation.

## Expired-record cleanup

Date-only deadlines remain `YYYY-MM-DD` and expire only after their closing UTC calendar day. For example, a 19 October deadline remains eligible at `2026-10-19T23:59:59Z` and expires at the next UTC midnight. This index convention does not assert a publisher closing clock or timezone. Supported offset-aware timestamps retain their exact instant.

After complete nonempty collection and validation, the refresh removes only
records with a validated deadline strictly before the successful check time.
Missing or ambiguous publisher deadlines remain normalized as null; status, age
and disappearance are never used as expiry evidence. Invalid normalized dates
fail validation and preserve last-good data. The source uses complete replacement rather than merging
old records, so expired previously published rows cannot survive a successful
refresh, including when they are still present in the publisher response.
Creation and first-seen history is retained for refreshed eligible identities.

The retained subset and success metadata are published in one source-scoped
atomic commit. A validated nonempty raw collection whose records all expire
publishes `[]` with success and a zero count; an empty upstream collection still
fails. A previously successful empty subset remains a valid last-good snapshot.
Fetch, parse, validation and publication failures preserve last-good data and
its success timestamp; only safe failure metadata may change. The sole live test
continues to emit the complete unfiltered publisher collection without publishing.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The publisher
family and per-host durable namespace identities are preserved. The pacing and
backoff changes are documented in the durable pacing section below. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **18:28 UTC** (`28 18 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The collector has one complete server-rendered inventory and no pagination.
Its physical inventory redirects and connection retries nevertheless receive the
30-second completion guard; literal robots requests keep the separate 12.2-second
physical floor and any stricter applicable policy. Neither statute PDFs nor
additional detail pages are fetched.

## Workflow concurrency

`youthopps-ee-taltech-publisher-v1` serializes runs of this source. Up to 100
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

Published metadata uses `display_name` = **TalTech Development Fund** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Durable local and hosted publisher state

The original `/tmp/ee-taltech-pacing-<uid>/<sha256-host>` namespace remains in
use. The previously absent local state is not treated as zero history. The
one-off transition requires separately reviewed, explicit maintainer approval
of all 30 historical wrappers, all 28 actual executed-job logs and conservative
completion upper bounds. Two precisely pinned old cancelled attempts contain
no executed jobs: they remain in the full provenance manifest and are excluded
only from executed-job chronology, without invented completion timestamps.
No new or unpinned empty job inventory is exempt.

Historical 429, response-header and hold fields remain explicitly unknown.
Observed counters begin only at the reviewed transition; they do not assert
zero historical rate limits. Restrictive known state is merged by numeric MAX,
request-time union and hold OR. A fresh runner cannot reset a stronger interval,
embargo or refusal. Subsequent local and hosted attempts require the newest
exact completed attempt's authenticated owned artifact, with run, attempt,
source, main branch, head and state/code digest binding. Missing, corrupt,
expired or mismatched newest state fails closed without an older fallback.

Local collection uses the configured GitHub transport for verified read-only
workflow and source-revision metadata. It sends Authorization only when an
existing `GITHUB_TOKEN` is present; hosted Actions require that token. Never
provide `DATA_SOURCE_TOKEN` to its live test.
The exact source workflow must remain manually paused, all source work must be
terminal, and both conditions are checked again after the startup quiet period
immediately before the first publisher request. Re-entry in the same process
refreshes this evidence. A missing journal in an initialized namespace fails even when an older owned
artifact is available. Only a namespace newly created by this process may
initialize from verified latest owned state; the creation proof is cleared
before the first journal fsync, even if that durability attempt fails. An existing directory containing only old
GitHub transport state is not evidence of an unused publisher allowance.

A 429 increases both retained physical spacing and inventory gaps, as well as
its repeated-strike cooldown. The increased state is persisted before any body
read. At least 30 minutes of exponential backoff remains in effect, together
with any longer valid Retry-After. Ordinary rate warnings stay timed; genuine
visible verification or refusal also retains a hold. Actual content denials
remain held. Literal robots-only 4xx except 429 mean unavailable REP; unreachable
policies and actual content refusals are not converted to permission.

Any reservation, restrictive-state or completion persistence failure keeps the
process closed, including collection re-entry. Export invalidates safely owned
stale output through a validated identity-checked file descriptor before error
signalling or deletion. Individually or jointly failed signalling and deletion
cannot leave valid stale JSON. Unsafe owners, permissions, links and changed
file identities are rejected without modifying the unsafe objects.

## Bounded execution and evidence scope

The daily 18:28 UTC schedule and existing 30-minute Action cap are unchanged.
The adapter has a nonrenewable 1,740-second execution bound and a latest
collection end of 1,350 seconds from execution start, reserving 240 seconds for
publication, 120 for failure reporting and 30 for state export. Snapshot and
restore operations also have bounded phases. Every request timeout is at most
30 seconds and the remaining phase allowance. A timeout preserves last-good
data and never publishes partial inventory.

The two nominal roots can amplify to 64 physical attempts through the unchanged
connection-retry, redirect and uncached robots graph. Worst-case socket latency
and stricter policy may exceed the cap; the adapter then fails closed rather
than raising limits, shortening waits or truncating inventory. Historical
81-distinct-award diagnostics and the prior 80 published records are evidence
from their actual observation times, not a fixed current count. The separately
released original test genuinely passed on 15 October 2026 at source `edf22529`:
two physical publisher requests, all 81 raw awards and 80 current awards. The
complete original response, all records and all eight actual state saves remain
in private RAM. That actual page proved 74 deadlines were calendar dates only.
The subsequent narrow date normalization is validated by offline replay of that
entire actual page and all records, the current website consumer and UTC closing
day controls. This is qualified reuse of the genuine collection, not a claim
that the corrected source ran a second live test or reached production.
Complete collection remains necessary because no verified publisher cursor or
older-update/removal feed establishes a safe incremental boundary.

The new modern budget uses schema 3 with an explicit `in_flight` transaction
field; older schemas or a missing field never silently default to settled.
Before a physical request it durably records `true`. Known response constraints
are saved before body reads. Only actual completion after those saves, without
a persistence failure, may settle the request to `false`. Pending local or
newest owned state fails closed, and restrictive merging uses OR so an older
settled artifact cannot clear it. The explicit unknown-legacy seed may begin
settled only through the separate one-off maintainer approval.

The validated original journal descriptor is also retained until release. Any
persistence failure latches the process closed and attempts to invalidate stale
journal JSON through that descriptor. No subsequent completion write can reopen
it. If invalidation and later writes both fail, the already durable pre-wire
pending field still prevents a fresh process from restoring an older settled
allowance. Stronger known constraints remain in memory for review. Full native
handoff may mark historical requests settled only when the complete actual
reservation, wire, response and successful completion-save chain proves it.

A successful robots response stays pending through assessment of the target's
applicable current and legacy rules and durable retention of any stricter delay
or explicit denial. A supplied policy applied to another target has a separate
policy transaction before assessment; it does not fabricate a physical request.
Invalid or unassessed policy remains unsettled. Only successfully persisted
policy constraints allow settlement. This keeps the existing uncached request
graph and does not add publisher calls.

The original data-source GitHub transport retains its six-second/ten-start
window and valid Retry-After state in its original per-host namespace. New
Actions metadata and owned-artifact transport use a separate bounded embargo
gate, honoring valid Retry-After before another infrastructure request without
adding a six-second delay to each of the historical metadata reads. Publisher
requests cannot select that infrastructure purpose.

The verified pacing parent directory is retained by descriptor and device/inode
identity. Renaming or replacing its current path, including a symlink to another
owned directory, is refused. Journal creation, locking, reads and writes use
relative names under that retained descriptor; checks also surround completed
IO. A legitimate current journal inode replacement inside the same parent is
supported. Cleanup cannot follow a changed parent into an unrelated safe-looking
file. The legacy data-source GitHub budget shares this parent protection while
retaining its original normal pacing and Retry-After behavior.


## Reviewed production transition

[Maintainer approval](https://github.com/YouthOpps/data-pipeline/issues/271#issuecomment-6203012520)
binds the full known modern state to the qualified original live collection and
subsequent four-function date-only correction. It retains 28 historical executed
job-completion upper bounds and eight native reservation/opener/wire/completion
observations: 36 observations, not 36 publisher requests. All eight actual
successful state-save chains and stronger known in-memory numeric constraints
are included. Historical unlogged refusals, headers and rate-limit counts remain
unknown; the new strike count starts only at this reviewed transition. The
explicit settled flag is supported by both complete response and policy-save
chains, and the handoff guard is at least native receipt completion plus 60
seconds. Modern attempts must restore their newest exact owned artifact; no
missing-artifact fallback is introduced.

The original native-tested source is `edf22529`; the corrected date-only
normalizer `89f69f9c` was checked offline against the complete actual input, all
81 awards and the actual 80 current awards, with UTC closing-day boundaries.
Only the reviewed production-state literal is added afterward. This evidence
does not claim another live collection, hosted publication or site delivery.
