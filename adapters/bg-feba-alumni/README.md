# FEBA Alumni opportunities

The standalone standard-library adapter reads the official public
[FEBA Alumni news API](https://febalumni.org/wp-json/wp/v2/posts) linked to
[the publisher news site](https://febalumni.org/news/). It checks robots
before requests, spaces requests by at least six seconds, honors backoff
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
Production alone uses DATA_SOURCE_TOKEN, atomic source-only Git-tree commits
and nonforce branch updates to publish data and metadata together under
`datas/bg-feba-alumni/`. New records replace the prior collection; matching
IDs retain first-seen/created timestamps. Failed retrieval, validation or
publication preserves last-good data and its successful timestamp; a safe
subsequent failure records metadata only, and first failure creates no folder.

## Expired-record cleanup

Supported Bulgarian application-closing title dates remain date-only without
an invented closing timezone. Other titles retain null deadlines. Current
publisher titles take precedence over stale URL text: the live scholarship
notice title states 20 October 2026 although its unchanged URL includes
16 October. No deadline is inferred from the URL or article publication date.
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
pacing, backoff and durable budget identities remain unchanged. Existing
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

Inventory page continuations and partitions wait at least 12 seconds after the
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
