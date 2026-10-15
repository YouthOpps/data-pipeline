# INSAIT public opportunities

This standalone standard-library adapter discovers current programme and call pages
from INSAIT's Join INSAIT navigation and both publishers' advertised sitemap trees.
It reads the selected pages on every run. New programme routes and editions are
eligible for discovery; changing text, links, menus, footers and sitemap timestamps
do not require updating a stored content hash or a record inventory in code.

## Collection and scope

Official starting points are [Join INSAIT](https://insait.ai/join-insait/),
[INSAIT's sitemap](https://insait.ai/sitemap_index.xml) and
[EXPLORER's sitemap](https://explorer.insait.ai/sitemap_index.xml).
The programme navigation supplements entries missing from the sitemap. Discovery
includes scholarship, fellowship, internship, research-programme and recruitment
routes, then validates each page's main content and programme title. Person
directories, alumni/results stories, categories and application forms are excluded.
An explicit no-open-positions message contributes no record. SURF announcement
news for the edition on the canonical SURF page is deduplicated against that page.
A missing page, incomplete HTML, ambiguous title or
exceeded traversal limit fails collection rather than publishing a partial result.
New publisher URL conventions may require extending the discovery rules.
Publisher-advertised English language alternatives are followed and deduplicated;
new Bulgarian or EXPLORER programme routes are still eligible for discovery.
News requires an application closing or an explicit invitation to apply; generic
programme publicity and recipient stories do not create duplicate opportunities.
Unrecognized opportunity categories remain `other` rather than stopping updates.

Titles, short factual excerpts, publication dates and explicit application closing
dates come from live HTML. Navigation and footer content do not supply record
facts. Unsupported or yearless closing dates remain null. English dates with an
explicit year are parsed only in application-closing context; EET/EEST/UTC times
preserve their offsets, while date-only closings retain date-only precision.
Multiple different closings are left unknown rather than reduced to an invented
single date. A forthcoming programme remains unknown, even if older dates appear
elsewhere on its page. Eligibility countries are not inferred from language,
citizenship or residence wording. Host countries require source evidence.

Existing programme identities retain their published keys; SURF and EXPLORER
edition keys are derived from current source years. Newly discovered programmes
use their canonical publisher URL. Successful publication preserves first-seen
timestamps, updates changed record content and replaces this source's inventory.
Records no longer discovered are removed on a successful complete collection.

Copyright and cookie notices do not establish an open content licence. Output
contains brief factual excerpts, attribution and original links, not complete
articles, images, application forms or applicant information. Public robots policies
are checked on both origins. Supporting PDF bytes and unrelated contact-link
encodings are not frozen prerequisites for reading current HTML opportunity facts.

## Verification and publication

Run `python -B adapters/bg-insait/test_adapter.py` for the sole nonpublishing
live collection test. It validates every record and prints the complete result
to stdout. Record counts are dynamic. Publication is performed only by the
dedicated workflow with
`python -B adapters/bg-insait/adapter.py --publish`.

Both origins share one durable publisher family and collector lock, with at least
six seconds between request starts and at most ten starts per rolling minute.
Robots, redirects and retries consume the same budget. Retry-After, access refusals,
TLS verification, bounded execution and source-scoped atomic publication retain
the existing lifecycle. Public routes are restricted to the two publisher origins;
private forms and administrative endpoints are excluded.

The collection phase is capped at 900 seconds, publication and failure handling
at 180 seconds each, with a final 30 seconds reserved for timing-state export.
The workflow restores the latest validated family pacing artifact; its historical
bootstrap evidence remains
[recorded on issue 51](https://github.com/YouthOpps/data-pipeline/issues/51#issuecomment-6095058149).
A fetch, parsing, validation or publication failure preserves last-good data and
its last-success timestamp, recording failure metadata when possible.

## Expired-record cleanup

A successful refresh validates the complete nonempty collected inventory, then excludes records whose supported `deadline` has passed before preparing the atomic source data/metadata replacement. Previously published records are used only to preserve matching records' first-seen timestamps; they are never merged back into the replacement. Thus expired old records cannot survive a successful replacement. Missing deadlines, ambiguous clocks, status, age and absence from the response are not expiry evidence.

Explicit timezone-aware deadlines expire strictly after their instant. For date-only deadlines without a documented publisher timezone, cleanup waits until the closing day has ended in every legal timezone: the current date in UTC-12 must be later than the closing date. This conservative boundary retains the full closing day without inventing a publisher timezone; deletion can be delayed up to 26 hours relative to the earliest civil closing-day boundary. Unsupported or ambiguous source clocks remain null deadlines; invalid timestamp fields fail validation before cleanup. If every collected record is expired, the valid nonempty retrieval publishes an empty successful replacement. A later empty upstream retrieval still fails and preserves that last-good pair.

Fetch, parse, validation and publication failures preserve last-good data and success timestamps. Cleanup performs no separate deletion or remote write; publication updates only this source's data/metadata atomically, with the existing concurrent-update protection. The live test collects real inventory without publication; offline boundary checks and static publication review verify cleanup.

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
