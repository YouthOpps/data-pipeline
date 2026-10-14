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
