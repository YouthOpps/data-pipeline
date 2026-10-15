# Youth for Europe

Standalone factual discovery from [Youth for Europe](https://youthforeurope.eu/), operated by Associazione Joint. The current official [global Explore inventory](https://youthforeurope.eu/explore/?type=general&sort=latest) replaces the assigned `/all-the-opportunities/` URL, which currently serves a page-not-found template despite HTTP 200.

## Access and reuse evidence

Reviewed on 14 October 2026: [robots.txt](https://youthforeurope.eu/robots.txt) has only MSNBot/bingbot five-second crawl delays, no rules excluding this adapter. The linked [privacy policy](https://youthforeurope.eu/privacy-policy/) concerns personal data, accounts and cookies; it contains no automated-access prohibition. Homepage, browse and account navigation disclosed no separate general reuse terms. No open content license is claimed. The adapter publishes attributed titles, original publisher links, native IDs/types, and explicitly labeled factual dates/locations/eligibility; summaries stay empty, and descriptions, images, personal contact details and downloadable documents are not republished. It does not submit forms, register accounts, load tracking assets or retrieve third-party resources.

The advertised [REST index](https://youthforeurope.eu/wp-json/) exposes no public project collection. The advertised [RSS feed](https://youthforeurope.eu/feed/) contains blog articles and testimonials, not the opportunity inventory. Collection therefore uses the public native global browse and public project details. Robots are checked before every endpoint; denials, HTTP errors, broken native markup and incomplete pagination fail closed. Requests, redirects and retries share persistent source/host pacing and process serialization: at least 6.1 seconds between starts and at most ten starts per rolling minute; Retry-After and stricter robots delays are honored. The temporary pacing state survives separate local invocations and is never reset by collection.

## Inventory and provenance

The browser's `case27-explore-configuration` supplies `is_global`, selected type/page, native card HTML, total record count, total pages and page size. All counted pages are collected using the browser's one-based `pg` parameter; duplicate IDs, missing cards, inconsistent totals or ignored pages are failures. Global browse covers every native type, including international volunteering even though it has no separate tab. During research the native type tabs contained ESC 1, internships 2, youth exchanges/jobs/training/au-pair 0; global browse contained all six projects, including three international volunteering projects. These are observations, not pinned IDs or inventory hashes. New projects are discovered automatically. Blog posts, testimonials, informational programme pages, advertising and forms are excluded because they are not native opportunity cards.

IDs are `youth-for-europe-<publisher postid>` (compatible with downstream safe-ID paths) and remain stable across URL changes. Each detail must match the native card's ID, title, type and canonical URL. Detail JSON-LD supplies publication time. Categories use native types: ESC/international volunteering → volunteering; internships → internships; youth exchanges/training courses → training; jobs/au-pair → jobs; unfamiliar types → other with unknown classification.

Only labeled application closing dates in `DD/MM/YYYY` become deadlines. Starting/ending dates are never substituted. Date ranges, multiple closing dates and ambiguous values stay unknown. Explicit labeled location country names support host codes; unfamiliar country names stay unspecified. Remote opportunities retain the publisher's listed address as location but have no inferred host country. A volunteer profile explicitly requiring EU citizenship supports the EU-27 eligibility set; general European/non-French European wording, age requirements or silence do not imply a country list. Publisher country is unspecified because the reviewed pages do not explicitly establish it. No unsupported deadline, age or geographic fact is invented.

## Expiry and publication

Raw collection must be complete, valid and nonempty before any expiry filtering. A date-only deadline remains open through its closing day. The publisher supplies no application timezone, so cleanup waits until that date has ended in UTC−12, the last civil offset; no publisher timezone or exact closing instant is asserted. Missing or ambiguous deadlines remain eligible for retention.

A successful refresh atomically replaces only `datas/youth-for-europe/data.json` and `metadata.json`; expired previously published records cannot survive that replacement. All-expired valid raw collection produces an atomic empty dataset and success metadata. Subsequent snapshot reads accept that empty publication, while an empty raw retrieval remains a failure. First failure creates no source folder; later fetch/parse/validation/publication failures preserve last-good records and success timestamp. Failure metadata is attempted only when a prior source snapshot exists. Conflict handling never force-overwrites a newer source snapshot. Production publication is authorized by the maintainer merge, not by a collection test.

## Run and validate

Python 3.12+, standard library only; the folder can be copied and run independently.

```sh
python -B adapters/youth-for-europe/test_adapter.py
python -B adapters/youth-for-europe/adapter.py
```

The sole live test collects real nonempty records and emits the complete validated JSON array on stdout; test diagnostics go to stderr. It neither requires publication credentials nor writes datasets. The ordinary adapter command prints run metadata without publication. The matching main-push/manual/daily workflow's only application command is:

```sh
python -B adapters/youth-for-europe/adapter.py --publish
```

`DATA_SOURCE_TOKEN` is required only for publication and is scoped to the data-source repository. Do not invoke production Actions as an implementation test.

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

The scheduled refresh runs once daily at **05:40 UTC** (`40 5 * * *`).
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

`youthopps-youth-for-europe-publisher-v1` serializes runs of this source. Up to
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

Published metadata uses `display_name` = **Associazione Joint** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
