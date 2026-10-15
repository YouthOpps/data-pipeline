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

Requests use the identifying YouthOpp user agent, ordinary TLS/proxy settings,
current robots rules and Retry-After. Starts remain at least six seconds apart
and at most ten per rolling minute across pages, robots, redirects and retries.
Local overlapping runs share a lock outside the checkout; runs on different
machines must not overlap. Only own public pages are fetched, never application
forms, portals, biography directories or external provider catalogues.

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

The unchanged source workflow runs on filtered main-branch pushes, its schedule
and manual dispatch. Its sole application command is `adapter.py --publish`.
Source-scoped GitHub App credentials and source-family publication serialization are
preserved. Publication atomically updates only this source's records/metadata;
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

The scheduled refresh runs once daily at **11:31 UTC** (`31 11 * * *`).
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
attempts, source-pair checks and non-forced atomic writes are unchanged. This
change adds no retry delay, workflow activation or publication test.
