# Youth Opportunities

Collect every individual announcement in the publisher-maintained
[Browse Opportunities index](https://www.youthop.com/search/), including every
pagination page and every listed call without a deadline. This is the complete
public browse inventory, rather than the homepage's ten latest additions or the
entire historical WordPress archive. The index supplies original article URLs
and publisher post identifiers; the public
[WordPress API](https://www.youthop.com/wp-json/) supplies each corresponding
article's full eligibility/application content and factual metadata.

## Access, provenance and reuse

The official [robots policy](https://www.youthop.com/robots.txt) disallows only
`/wp-admin/` and explicitly allows its AJAX endpoint. The reviewed public browse,
category and post API routes are permitted. Collection rechecks robots and every
redirect target, honors crawl delay and Retry-After, allows up to 90 seconds per
response, and fails closed on access errors. It uses no login, JavaScript execution, CAPTCHA or restricted endpoint.

The [Terms and Policies](https://www.youthop.com/policies) page contains the
publisher's disclaimer and privacy policy. Youth Opportunities describes its
purpose as promoting access to information and directs readers to the original
opportunity provider for complete authoritative information. The company address
is in Bangladesh (`publisher_country=BD`); this does not imply opportunity host
or applicant nationality. No express copyright licence is claimed. Publication
contains attributed factual titles, links, taxonomy, dates, location and short
eligible-region labels, without copying full article prose or images. Publisher
attribution is `Youth Opportunities`; the
[contact page](https://www.youthop.com/contact) is the route for rights inquiries.
No contact submission or permission request was made.

## Inventory and validation

The unfiltered browse page currently exposes 15 opportunity cards plus an
advertising widget per full page, and 28 pages.
The adapter follows all declared `sf_paged` pages without an arbitrary cap,
and excludes only an explicit Google Ads widget with no post ID, original
article URL or visible content, and an `ins.adsbygoogle` marker. It requires
consistent page counts and full intermediate opportunity pages, rejects duplicate
IDs, and rechecks the first page before requesting details to detect a moving
index. Empty pages, changed structure, mismatched details, unknown taxonomy or
unresolved article content fail the complete collection.

The public API's historical archive contains substantially more records than
the maintained browse inventory. Historical announcements absent from the
publisher's browse index are outside this source-backed scope; no publication
age, title year or invented deadline is used to exclude them. All current index
entries are inspected, including no-deadline calls. The API's compact collection
response omits full content even with `_fields` or `_embed`; each individual
`/wp-json/wp/v2/posts/<id>` response supplies the genuine article. The published
batch API rejects GET subrequests, so the adapter does not use write methods to
simulate bulk reads.

Every record must match its browse card's original URL and numeric post ID and
have genuine full article content containing application/eligibility/participant
evidence. Publisher category names supply classification: scholarships and
scholarship degree tracks, fellowships, internships, competitions/awards, grants,
jobs, volunteering, and workshops/summer schools map to existing public
categories. Other genuine
conference, exchange or miscellaneous calls use `other`, `kind=opportunity` and
classified article provenance. Multiple taxonomy memberships remain visible;
incidental mentions in prose do not create categories. There is no title-only
classifier or broad editorial keyword exclusion.

Stable IDs are `youth-opportunities-<positive ASCII publisher post ID>`,
independent of card position, title and taxonomy and compatible with website
page identifiers. Fresh collections reject legacy colon IDs. Existing published
snapshots may contain `youth-opportunities:<post ID>`; only snapshot validation
accepts that legacy form. Successful atomic refresh migrates this source to
hyphen IDs while preserving matching creation/first-seen times. A failed refresh
retains unchanged legacy data and the prior success timestamp. Duplicate legacy
and current representations of the same native post are rejected on read.
Separate publisher posts remain separate announcements even when programme
names repeat; titles are not an identity
merging rule. Tags preserve publisher category names; original
article links provide classification evidence. GMT publication dates come from
`date_gmt` and are explicitly interpreted as UTC.

Affirmative publisher `has-deadline` flags (`true`, `1` or `"1"`) activate
the `deadline` Unix closing instant. These active epochs are independently
corroborated by the publisher's UTC Google Calendar links: for example,
[International Physics Competition 2026](https://www.youthop.com/miscellaneous/join-now-international-physics-competition-2026)
links to `20261101T235900Z`, matching epoch `1793577540`. The adapter preserves
that source-stated instant; it does not convert a calendar-only label into an
invented clock. Unrecognized flags or invalid active epochs fail collection. Disabled or missing
flags (`false`, `null`, `0`, `"0"` or an empty string) leave the deadline null,
including legacy residual epochs. The publisher renders these calls as On Going
and suppresses closing-date/calendar links: the
[U-Save Foundation Fellowship 2023](https://www.youthop.com/fellowships/the-u-save-foundation-fellowship-program-2023)
has a null flag and residual epoch `1686869940`, while its public article/card
has no active closing date. That unused field does not establish expiry. Missing
active closing instants remain null and status unknown; available instants set status
relative to collection time, without guaranteeing provider availability.

Location preserves the source's explicit location metadata. Short summaries
preserve explicit `Eligible Regions` labels from actual article content. Country
code arrays remain empty because the publisher's free-text regions, continents,
worldwide labels and institutional affiliations do not constitute complete
normalized nationality evidence. Empty arrays mean unspecified eligibility,
not an exclusion of applicants; readers must follow the article and original
provider. No host or eligible nationality is inferred from the title, publisher
country, programme name or region visibility.

## Run and publication

```sh
python -B adapters/youth-opportunities/test_adapter.py
python -B adapters/youth-opportunities/adapter.py --publish
```

The sole live test collects real nonempty data, validates every record and prints
one complete JSON list to stdout; progress and test diagnostics go to stderr.
It uses no publication credential, mock, fixture or output file. The initial
browse plus individual details can take about an hour under the request budget;
the workflow allows three hours and has no per-run inventory cap.

All source endpoints, robots, pagination, retries and redirects share at least
six seconds between request starts and at most ten starts in any rolling minute.
The limiter reserves 6.1 seconds to leave a small scheduling margin after the
budget reservation.
A local collection lock serializes overlapping test/production runs, and locked
temporary budget state persists starts and backoff across processes. These state
files contain no retrieved opportunities or credentials. Independently hosted
runs and external research must be coordinated under the same publisher budget.

Production automation alone receives `DATA_SOURCE_TOKEN`. A Git tree/commit
atomically updates only `datas/youth-opportunities/data.json` and `metadata.json`
with a non-forced main ref update. Publication conflicts retry only if this
source has not changed. Matching records preserve first-seen timestamps;
successful refreshes replace the complete source snapshot rather than merging
old records back into it. Supported expired deadlines are removed after the
fresh nonempty collection is validated. A valid retrieval that becomes empty
through expiry can publish an empty successful replacement. Subsequent snapshot
reads accept that valid empty result, while empty raw retrieval remains a
failure. Closing instants use the explicit UTC epoch only; date-only labels do
not become invented instants. Unknown deadlines remain eligible for retention,
without inferring expiry from age, status or absence.

Collection, parsing, validation and publication failures preserve last-good
data and its success timestamp, and save sanitized failure metadata when safe.
First-run failure creates no source folder. Failure to record durable metadata
is explicitly reported. The matching workflow runs once daily at 12:35 UTC, on
changes to this adapter or its own workflow merged to main, and on manual
dispatch. It preserves the repository/main guard, source-scoped credentials and
serialized publication. Production Actions are not used for implementation QA.

The daily schedule reduces repeated full-inventory retrieval from four scheduled
runs to one per day. GitHub may delay scheduled runs, and a source-scoped merge
or manual dispatch can still start an additional run. Each request retains the
existing pacing and stricter publisher limits. This schedule change does not
make collection incremental: known IDs alone do not prove that older calls are
unchanged or that browse ordering is strictly newest-first. A future incremental
implementation needs verified ordering/change coverage and periodic complete
browse reconciliation before it can replace this full-inventory refresh.

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

The scheduled refresh runs once daily at **12:35 UTC** (`35 12 * * *`).
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

The workflow currently creates its installation token before collection; that
token has a one-hour lifetime. The prior successful 417-record run took about
53 minutes. Page-only spacing avoids doubling the 417 detail-request waits, but
adds time to the listing scan and leaves limited publication-token headroom.
Larger inventories or slower responses can still exhaust that existing token
lifetime; the three-hour job timeout does not extend it. No token-renewal
behavior or timeout increase is claimed by this scheduling/pagination change.

## Workflow concurrency

`youthopps-youth-opportunities-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Youth Opportunities** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
