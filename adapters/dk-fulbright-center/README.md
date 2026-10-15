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
Source-scoped GitHub App credentials and global publication serialization are
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
