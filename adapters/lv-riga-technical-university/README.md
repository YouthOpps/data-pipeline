# Riga Technical University funding opportunities

This source collects RTU's substantive published doctoral awards, mobility
funding routes, Latvian state scholarship information and postdoctoral call.
The publisher is Riga Technical University; facts retain its attribution and
canonical URLs. Collection does not infer Latvian citizenship from the host.

## Inventory and eligibility

The doctoral [funding page](https://www.rtu.lv/en/studies/doctoral-studies/scholarships-and-grants)
has six funding sections and a separate FAQ. The parser checks the complete
reviewed section inventory and rejects missing, duplicate or unfamiliar
sections. The FAQ is evidence, not an award. Tuition discounts exclude
state-funded students and RTU employees. The EUR 140 monthly scholarship has
state-funded study, academic progress and study-start/statutory conditions.
RTU doctoral grants and Recovery and Resilience Mechanism grants explicitly
have no announced application round; they remain genuine programmes with
unknown application status and no deadline. RRM funding is EUR 1292 monthly
including employer social security at 0.5 FTE for at least 12 months, plus up
to EUR 500 monthly research expenses.

The doctoral Erasmus umbrella is represented by four actual linked funding
routes rather than an extra duplicate: European study, traineeship, staff
mobility and partner-country study. European study requires average 6.75;
traineeship and partner-country study require 6.25. European study and
traineeship specify bachelor semester three/master semester two; partner
country study does not specify the master semester-two condition. European
study lasts 2–12 months, partner-country study 3–12 months. Student funding
only partly covers costs. Trainees must secure a host; academic leave excludes
the Erasmus scholarship, whereas employer payment above EUR 600 monthly
excludes only Latvian state co-financing. Staff funding covers at most five
working days and excludes payment for travel days without work. Recurring
month/day windows have no stated year, so do not become invented deadlines.

The ERDF doctoral fourth selection is one actual call, checked against its
canonical notice. Cohort numbers, available places, historical FAQs, mirror
notices and regulations for another selection do not create extra records.
Its deadline is 14 October 2026, source clock 16:59 without a stated timezone.
The record uses `2026-10-14` and preserves the clock separately and in its
summary. No Riga offset is invented. A same-day date-only deadline has unknown
status; the website's end-of-UTC-day expiration convention does not establish
a publisher closing instant.

The postdoctoral fifth selection closes 29 October 2026 at 23:59 EET, preserved
as `2026-10-29T23:59:00+02:00`. Its 26 October access-request milestone is
separate evidence, not the application deadline. Eligibility includes Latvian
or foreign researchers with a doctorate obtained within ten years and RIS3
research. The published support includes up to EUR 130419 over 24 months.

The Latvian state scholarship page names academic year 2026/27 but also retains
2025 application text. It remains a programme with no exact deadline and
unknown status. Bilateral/reciprocal-country eligibility is described without
inventing an exhaustive country list. This reviewed inventory currently yields
11 identities, but collection discovers and validates source sections instead
of asserting a fixed record count. Programme identities and published call
selection identifiers produce stable source-specific IDs.

## Access and reuse

Public English HTML is read with an identifying YouthOpp user agent and normal
TLS verification. Live robots rules are honored, including ordinary CRLF
syntax, redirected paths and any stricter crawl delay. Application platforms,
search, disallowed application pages and the disallowed Latvian privacy redirect
are not collected. No login, application submission, email decoding or protected
PDF retrieval is needed.

RTU's published intellectual-property management policy concerns creation and
use of works in employment, study and cooperation relationships. It does not
state a blanket prohibition on factual public-page collection, and does not
grant a blanket open-content licence. Records use attributed facts and short
original summaries, without reproducing images or entire publisher prose.
Research evidence is cached outside the repository in
`/tmp/youthopps-source-triage/16`; that cache is not a runtime dependency.

Requests, redirects and bounded transient retries share a source-specific
cross-process lock and durable pacing state outside the repository: at least
twelve seconds between physical starts and at most five starts per rolling
minute. A conservative completion-to-next-start guard prevents reservation
timing drift. Robots
and Retry-After requirements can impose longer waits. Parser or policy changes
fail the collection rather than publish a partial result.

## Run and publication

```sh
python -B adapters/lv-riga-technical-university/test_adapter.py
python -B adapters/lv-riga-technical-university/adapter.py
```

The single live test emits the complete JSON record array on stdout and test
diagnostics on stderr. It needs no publication credentials and writes no output
files. The adapter's ordinary command also collects without publication.

The matching workflow runs only in the upstream repository on main, after
changes to this adapter or its workflow, by manual dispatch, and once daily at 12:59 UTC. Its narrowly scoped GitHub App token targets only `data-source`.
Publication uses the adapter's `--publish` command and source-family publication
concurrency. A single atomic commit updates only this source's data and metadata.
A first failed collection creates no source folder; later failures preserve
last good data and its durable success timestamp while recording failure
metadata. The success timestamp advances only after successful publication of
validated refreshes, including an all-expired successful empty result. Local
tests do not publish or run Actions.

## Robots availability and production recovery

[RFC9309 section 2.3.1.3](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3)
permits treating an unavailable robots file separately from content refusal.
Only robots retrieval 400–499, except 429, caches an absent policy for that
collection, including 401, 403, 404 and 410.
An actual Disallow, applicable legacy product rule or stricter publisher limit
still applies. Content 401/403/451 and a recognized challenge/refusal page stop
collection and durably retain the refusal. Robots 429, 5xx and TLS/network
failure are not converted to an empty policy. No browser impersonation,
credentials workaround or content-status fallback is used. Robots absence is
neither a reuse licence nor a requirement for universal written permission.

All responses preserve valid Retry-After. A 429 stops the attempt without an
immediate unchanged retry and records at least 30 minutes of backoff, doubled
for subsequent rate failures (with a bounded exponent); physical request
spacing also doubles durably. Stricter Retry-After wins. A later attempt fails
fast during the retained embargo, and can try normally afterwards with the
slower interval; a timed 429 alone is not a permanent access ban. Unusable
429 Retry-After (malformed, nonpositive, nonfinite or outside the supported date
range) uses that timed fallback without creating or clearing an access hold.
Unusable Retry-After on other statuses is ignored without inventing an access
hold or masking the actual response status. Valid embargo persistence failures
still stop collection; they are not classified as malformed headers. Tests
and production use the same original
host budget path, MAX interval/embargo/completion history and OR refusal merge.
A process collection lock serializes local collectors; the existing Action
concurrency serializes hosted runs. State is never cleared to recover access.

The four historical failed hosted attempts are 37849862305, 37849876657,
37850591097 and 37850601262, all attempt 1. The last two logs identify
`https://www.rtu.lv/robots.txt` HTTP 403; the first two omit the endpoint but
fail at the original first robots operation. Their pinned heads, exact job
completion times and zero pacing artifacts are checked before the one-time
migration. The latest job ended 2026-10-08T22:00:50Z. Old response headers and
arbitrary Retry-After were not exported: seven quiet days do not prove unknown
historical backoff was zero. The user-authorized robots-only recovery requires
independent review and maintainer acceptance of this explicitly incomplete
legacy history before the first hosted attempt. The code requires all four
exact attempts unchanged and at least 24 hours quiet; it conserves any existing
local embargo/refusal rather than substituting a cleared budget.

After any new completed attempt, including failed, cancelled or rerun attempts,
the newest exact attempt's trusted `rtu-pacing-state-<attempt>` artifact is
mandatory. Complete unfiltered source history is checked; an older-ID rerun
cannot be skipped. Missing, expired, corrupt or mismatched state fails closed,
without an older successful-artifact or first-migration fallback. Authenticated
metadata, zip digest/size/member checks and repository/source/run/attempt/head
bindings constrain the inert state; artifact storage receives no GitHub token.
Export happens after finalized state and sets a fresh step output only on
success, so the always-run upload cannot publish stale untrusted output.

Keep the workflow disabled while this change is reviewed and merged. The
separate maintainer verifies the merged main SHA, unchanged old history and
quiet period, then explicitly enables and starts one recovery attempt. Inspect
its source pair, complete logs and pacing artifact before claiming production
or website delivery. Any actual content refusal remains a held state; no blind
rerun or state reset is allowed. The daily 12:59 UTC cron, repository/main guard,
source-only token, 30-minute job limit and publication queue remain unchanged.
The one-hour publication token has ample room for the bounded nine-request
native collection and state restore; job timeout does not extend token expiry.

## Expired-record cleanup

Postdoctoral timestamps retain the publisher's explicit EET (+02:00) offset.
The doctoral date-only closing and unstated-zone local clock remain date-only;
the local clock cannot authorize an earlier inferred instant.

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

The scheduled refresh runs once daily at **12:59 UTC** (`59 12 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force. There are no inventory-page
continuations requiring an additional 30-second transition guard.

## Workflow concurrency

`youthopps-lv-riga-technical-university-publisher-v1` serializes runs of this
source. Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Redirect access checks

Robots-only handling is limited to the literal `/robots.txt` route on every
hop. A robots redirect to a content route stops before requesting that target;
its response is never treated as unavailable REP or a replacement robots policy.
Existing refusal holds, retained rate limits and source publication behavior
remain in force. This conditional-path correction does not clear an access hold
or certify a new publisher collection.

## Source display name

Published metadata uses `display_name` = **Riga Technical University** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Conditional header safety maintenance

[Issue 229](https://github.com/YouthOpps/data-pipeline/issues/229) changes only
unusable Retry-After handling and the actual content451 denial branch. Valid
longer embargoes, exponential 429 fallback, doubled current physical spacing,
existing refusal/artifact/recovery identity and all successful parsing, facts,
validation, expiry and publication remain unchanged. Literal robots451 retains
the existing unavailable-REP treatment. No workflow or timing floor changes.
This is not a page-gap/schema migration: the existing RTU state has no durable
inventory page-interval or legacy-rate-unknown field, and an absent historical
counter is not proof of lifetime zero rate limits.

The genuine earlier native test retrieved eleven raw records, ten after expiry,
and the then-current consumer accepted 11/10/0. Its receipt did not record the
exact tested-source SHA or discovery dependency hash. Frozen author revision
`c3177140b4b7fea41a303bfdbc6178be54e2c1bb` (source `8e7b779c`)
has prior independent conditional-reuse acceptance; it is not represented as
the exact original tested bytes. Full native records/bodies are no longer
retained. The current consumer `2fabcc52` differs from historical `7f23d601`;
no byte-identical whole-current-consumer or fresh full-record replay is claimed.
This maintenance uses that explicitly limited historical qualification plus
unchanged normal-path AST and actual-function inert error controls.

Production [38737282568](https://github.com/YouthOpps/data-pipeline/actions/runs/38737282568)
failed on actual content403 and retained its owned blocked-state artifact.
That hold remains controlling. No publisher request, new native collection,
state reset, workflow enable/dispatch or production recovery accompanies this
conditional maintenance. Source issue16 remains blocked independently.
