# Fulbright Austria

This standalone standard-library adapter discovers awards from the current
[publisher sitemap](https://www.fulbright.at/sitemap) and the six Austrian/US
student, scholar and teaching-assistant directories. It follows their own
`grant-details` links and the sitemap's award pages. New linked awards are
parsed without adding profiles or approving content hashes.

## Extraction and identity

Titles, application periods, funding excerpts and eligibility come from the
retrieved HTML. Multiple application periods select the next closing date;
date-only deadlines do not acquire an invented clock. Explicit CET/CEST/UTC/GMT
closing times retain their UTC instant. Unrecognized or yearless
dates remain null. Opening dates determine open status where available; a
date-only closing day has unknown status. Historical calls stay visible with
expired status. Recipient lists, contacts and related stories are excluded.

Stable IDs retain the previous award-slug keys, including the Expert and
American Studies prize aliases. The two EU Schuman routes share an identity.
The separately described Arts Specialist track is extracted from its own live
section and retains its existing ID. New award slugs generate deterministic
IDs. Existing publication logic preserves first-seen/created timestamps and
advances updated-at only when record content changes.

Countries are restricted to the direction of the programme and explicit
citizenship requirements. Institutional applications do not imply individual
citizenship. Schuman destinations are not expanded into an invented country
list. US teaching-assistant employment remains `jobs`; host projects are
`institutional-grant`, and the thesis prize is a competition.

## Access and limits

The [imprint](https://www.fulbright.at/about/imprint) permits attributed excerpts
apart from multimedia/software and prohibits framing. Records use short factual
excerpts, publisher attribution and official provenance links; no photographs,
recipient biographies, contact information or application data are collected.
The [privacy policy](https://www.fulbright.at/about/privacy) is not an open
content licence. External application portals and provider catalogues are never
crawled or submitted.

The old hard-coded profiles, content hashes and PDF/ZIP byte snapshots have
been removed. This collector extracts HTML, not PDF/ZIP contents. A condition
or opportunity published only inside an attachment is not extracted. Linked
HTML is the supported inventory, not a claim that every attachment or external
catalogue has been parsed. Summaries are bounded excerpts; the linked publisher
page remains authoritative for full conditions. Structural, transport and
record-validation errors still fail the collection instead of emitting an
empty successful publication.

Requests retain robots checks, the identifying YouthOpp user agent, normal
TLS/proxy behavior, Retry-After handling and shared local pacing: at least six
seconds between starts and at most ten starts in a rolling minute, including
redirects and retries. Separate machines must not run overlapping collections.

## Run and publication

`python -B adapters/at-fulbright-austria/test_adapter.py` runs the sole live,
nonpublishing test. It validates every actual record, unique IDs and source
provenance, then prints the complete JSON collection to stdout. Consume that
output in memory; no fixed count or snapshot is an acceptance condition.

The existing source-filtered main-branch workflow, manual dispatch and schedule
are unchanged. Its only application command is the adapter with `--publish`.
Publication requires `DATA_SOURCE_TOKEN`, atomically updates only this source's
data/metadata pair, preserves last-good data on failure and rejects a concurrent
newer same-source snapshot. No production execution is part of local testing.

## Expired-record cleanup

Fulbright Austria preserves explicit CET/CEST/UTC/GMT closing clocks as aware
instants; unzoned calendar closings remain date-only and unclear windows remain
null. This cleanup uses those application fields without interpreting award
periods as deadlines.

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

The scheduled refresh runs once daily at **08:17 UTC** (`17 8 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force; this source changes only its
daily automation schedule.

## Workflow concurrency

`youthopps-at-fulbright-austria-publisher-v1` serializes runs of this source. Up
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

Published metadata uses `display_name` = **Fulbright Austria** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
