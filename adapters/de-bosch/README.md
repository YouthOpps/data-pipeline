# Bosch career programme overviews

This adapter covers 18 independently reviewed student and graduate programme
identities on permitted official Bosch pages. It is a bounded programme overview,
not the worldwide job inventory. Current vacancies, enrolment and deadlines are
not established: every overview has unknown status and no application deadline.

## Coverage and identity

Global Junior Managers Program; six India programmes; Singapore Junior Managers
Program, PhD, internships and final theses; one shared students@bosch identity;
three US graduate/internship/co-op programmes; Canada's Graduate Specialist
Program; Africa's Graduate Experience Program and South African learnership
framework. The last two are named frameworks, not invented current courses.

Country JMP pages repeating generic global text are aliases. India and Singapore
have distinct entry terms. US internship and co-op sections share a page but have
different stable identities. students@bosch conditionally considers STEM students
and can invite strong interns; this does not establish a universal STEM-only rule.
Host-country information is separate from nationality eligibility. The US graduate
programme requires indefinite US work authorization and offers no future
sponsorship; citizenship is not inferred.

Known English job/country routes disallowed by robots are excluded without an
identity or alternate-language workaround. Japan's robots route returned 302 to a
404 page; no body or redirect was followed, and the programme remains excluded.
One premature prohibited jobs request during research was retained unused; its
body supplies no programme facts. Ordinary copyright reservations provide no
licence; the adapter uses short original factual summaries and direct attribution,
without copied protected prose, images or vacancy content.

## Collection and safety

The fixed finite frontier has 49 nominal physical requests: 27 full accepted
programme, parent and legal documents; six allowed-origin robots files; sixteen
excluded-country robots files only. Full visible prose, links and controls are
fingerprinted. Material, entry-condition, legal or frontier change fails the whole
collection for review instead of publishing stale recipes or a partial subset.
Every origin's fresh robots is inspected before a dependent document.

Standard-library-only operation uses normal verified TLS and inherited proxy/CA
configuration. One persistent Bosch publisher family serializes all endpoints,
robots, tests, production starts and redirects: at least six seconds between starts
and at most ten in any rolling sixty seconds. Each physical attempt, including
response closure and errors, preserves its completion plus the native interval
as the next-start boundary; a monotonic gate enforces elapsed time. The exclusive
collector preserves a sixty-second startup embargo and all stronger intervals.
Refusal and Retry-After restrictions
are durable and sticky. Legacy research inodes remain read-only and locked;
modern/legacy restrictions combine with maximum embargo/interval and logical-OR
refusal. Historical eleven-entry research state is preserved honestly and migrated
with a conservative sixty-second hold, never reset.

Collection is capped at 64 starts, 8 MiB per response, 64 MiB overall and 900
seconds. Publication is capped at 900 seconds, pacing export at 120 seconds, and
the whole job at 2520 seconds. Exhaustion fails; no truncated collection succeeds.

## Running and publication

Run the sole actual nonpublishing test with
`python -B adapters/de-bosch/test_adapter.py`. It emits the complete actual JSON
on stdout, diagnostics on stderr, and writes no normalized dataset. It requires
no publication credentials. Static/offline checks do not establish a live pass.

Production uses only `python -B adapters/de-bosch/adapter.py --publish` after merge.
It atomically publishes only `datas/de-bosch/{data,metadata}.json` in data-source.
A first failure creates no folder; later failures preserve last-good data and
successful timestamps while recording safe failure metadata when possible.
Unchanged substantive records retain their update timestamps; last-seen uses
actual collection start and last-checked uses completion. Current availability
remains unknown rather than falsely marking these programme overviews open.

The workflow includes the concrete first-family bootstrap recorded in the
[reviewed history and pacing handoff](https://github.com/YouthOpps/data-pipeline/issues/119#issuecomment-6101428656).
It applies only when exhaustive history finds no previously completed family
attempt and cannot override a refusal or stronger interval. Once a completed
family attempt exists, its newest owned, validated inert pacing artifact is
mandatory, including after failure. Missing, expired or refused state cannot
permit fresh bootstrap or an older fallback. The workflow uploads only healthy,
validated pacing state authorized by the current collection step.

## Review evidence

Issue: https://github.com/YouthOpps/data-pipeline/issues/119 . Complete precode,
all 18 full-field factual recipes, full metadata/current-consumer memory proof,
country exclusions and legacy preservation were independently accepted. Source,
offline adversarial checks and two separate actual collections still require
independent validation before PR/release; production and website visibility require
post-merge evidence. No live collection or publication is claimed by this file.

Pacing artifacts are exported only after healthy durable state has been verified. The uploader requires a positive output from the current collection step, using GitHub Actions’ fresh per-step output file. A failed completion-state write cannot authorize upload of an older artifact.

## Expired-record cleanup

The currently reviewed Bosch programme overviews have null application
deadlines. They remain eligible; general programme age and vacancy availability
never become fabricated cutoffs.

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

The scheduled refresh runs once daily at **10:58 UTC** (`58 10 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force; this source changes only its
daily automation schedule.

## Source display name

Published metadata uses `display_name` = **Bosch** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

### Journal integrity maintenance

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

Bosch additionally preserves the original two optional read-only legacy journal
paths, their exclusive locks, exact legacy schema/eleven-start allowance and
MAX merge rules. It records genuine acquisition identities and file generations
without writing legacy bytes or changing missing-path behavior. The existing
interval field, six-second fallback and startup quiet period remain unchanged.
