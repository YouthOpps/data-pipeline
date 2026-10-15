# DM student funding and member training

Source `dk-dm-and-ma-travel-grant` starts with DM's public
[English](https://dm.dk/students/) and [Danish](https://dm.dk/studerende/)
student pages. It follows current membership, benefit and grant links to
discover student funding and training programmes. The inventory is not a fixed
profile list. Programme headings, conditions, amounts and dated application
windows are extracted from current HTML on every collection.

## Inventory and identity

The 14 October 2026 research traversal inspected 24 discovered pages and
identified five programmes: the travel grant, Activity Fund/Sponsoratpulje,
free classes, online-course access and SLING/ULING/NAKU mentoring. All five
existing published IDs are retained through route-to-identity aliases. These
aliases contain no funding facts. English/Danish versions of the same offer
are combined; their differing conditions remain attributed to their own URLs.
New programme routes receive stable source/route identities. Each programme
retains one ID when dated application windows change or reorder. All explicit
windows are retained in `application_windows`; multiple windows leave the
universal `deadline` unknown. Relevant receipt and CVR guidance from the linked
reimbursement page is included with the Activity Fund's conditions and digest.

The Danish travel page currently supplies the complete next window,
1 November 2026 to 1 February 2027. English yearless recurring dates do not
invent years. Relative instructions such as applying a month before an event
remain conditions, not fabricated calendar deadlines. Unknown closing clocks
and zones stay unknown. Future openings do not imply currently open calls.

Public HTML course catalogues describe the membership entitlement; individual
software topics are not separate funded awards. The JavaScript event calendar
and external course-provider catalogue are outside this HTML collection.
Advice services, membership signup, personal mentor directories, banking,
insurance, consumer discounts, recipient stories and promotional prize draws
are excluded. New relevant programme links within the student membership
scope are followed. Pagination links using `page` or `p` are retained.

Citizenship is never inferred from membership, publisher location or enrollment.
The class programme has a Danish host only when the publisher explicitly names
its Danish cities. Other programmes have no complete host-country whitelist.

## Material updates and access

No content fingerprint is compared with a pinned value. Factual supporting
blocks and current amounts are extracted on each run. A digest of complete
programme blocks also detects changes beyond the introduction, including
conditions not repeated in the short summary. Publication compares all material
record fields, so those changes advance `updated_at`; unchanged records retain
their earlier creation and update timestamps. Source facts remain in attributed
evidence without clipping conditions to fit the 600 UTF-16-unit summary limit.

Reviewed official privacy, cookie and membership-payment policies did not
establish a general factual-collection prohibition. This is not an open-license
claim. Current linked policies and robots are checked on every run. DM's
robots endpoint returned 404 during research, which means an absent robots
file, not a licence. Access challenges, explicit collection prohibitions,
incomplete documents, unsafe redirects and failed required pages abort the run.
Policy wording changes alone do not invalidate programme facts.

Normal verified HTTPS and the identifying YouthOppsBot User-Agent remain enabled.
Publisher requests, robots, redirects and retries share locked local pacing:
at least six seconds between starts and at most ten starts in any rolling
60 seconds, honoring longer Retry-After and robots delays. Separate machines
must not overlap publisher collections. Signup redirects to another host are
not collection inputs; no account, application form or external service is used.

## Run and publication

```sh
python -B adapters/dk-dm-and-ma-travel-grant/test_adapter.py
python -B adapters/dk-dm-and-ma-travel-grant/adapter.py
```

The sole live test collects real records, validates every record and emits one
complete JSON document to stdout. Diagnostics use stderr; tests neither publish
nor write datasets. Parser development reused genuine responses only in RAM;
the delivered test has no cache hooks, fixtures or mocked retrieval.

The existing workflow's only application command is this adapter's `--publish`.
Its source-filtered main push, schedule, manual dispatch, repository guard,
restricted token and serialized publication remain unchanged. Publication writes
only `datas/dk-dm-and-ma-travel-grant/{data,metadata}.json` in one conflict-safe
commit. First failure creates no source folder; later failures preserve last-good
data and success timestamps. Production Actions are not a test harness.

## Expired-record cleanup


The travel round uses its published date-only closing; its closing clock
and timezone are unspecified. Relative or recurring unanchored dates remain null.

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

The scheduled refresh runs once daily at **17:56 UTC** (`56 17 * * *`).
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

`youthopps-dk-dm-and-ma-travel-grant-publisher-v1` serializes runs of this
source. Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Publication error diagnostics

Failed GitHub publication reports retain the HTTP method, a recognized API
route without its query, status and bounded attempt count. If checking the
remote outcome also fails, both failure contexts are retained. Diagnostics do
not include credentials, headers, response bodies or signed URLs. The existing
three-attempt limit, current-head and source-pair conflict checks, atomic writes
and successful collection/publication behavior are unchanged. This diagnostic
maintenance does not establish the underlying cause of older generic failures.

## Source display name

Published metadata uses `display_name` = **DM** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
