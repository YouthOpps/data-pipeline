# University of Tartu current opportunities

This standalone standard-library adapter collects the official
[open-calls table](https://ut.ee/en/open-calls) and the linked
[dated partner exchange calls](https://ut.ee/en/competitions-ut-partner-universities)
and [ENLIGHT course notice](https://ut.ee/en/news/enlight-short-term-blended-mobility-courses).
It checks publisher robots and permitted HTTPS routes before retrieving public
content, with six-second request spacing, shared local pacing and backoff.
Public access is not an open licence. Original notice links, University of
Tartu attribution and bounded factual summaries are retained. It never signs
in, accesses SoleMOVE applications or crawls external course destinations.

The current-call table determines inventory, excluding upcoming competitions,
evergreen scholarship directories and historical partner accordions. Every
named exchange destination must have a matching explicitly dated detail;
ENLIGHT heading links expand into individual course records, with duplicate
and full-link coverage checks. Unknown current-call routes or changed content
boundaries fail instead of publishing a partial inventory.

Stable IDs hash the source slug and exchange title/year identity or ENLIGHT
course URL, truncated to 24 hex characters. The parent deadline and dated
partner heading must agree; ENLIGHT's abbreviated closing year comes from
its article publication year. Date-only cutoffs are normalized to 23:59:59.999999
in Europe/Tallinn then expressed as UTC, preserving summer/winter offsets.
University student affiliation is not citizenship: eligible-country arrays
remain empty. Exchange host countries come from explicit destination headings;
ENLIGHT named host institutions remain attributed summary evidence without
inferred country codes. Categories use the supported training/exchange offer.

Run from this folder:

```sh
python -B adapter.py
python -B test_adapter.py
python -B adapter.py --publish
```

The sole live test emits the complete nonempty raw collection as JSON, validates
all records and uses no publication credentials or dataset files. Production
alone obtains DATA_SOURCE_TOKEN and publishes this source's data and metadata
atomically under `datas/ee-university-of-tartu/` with nonforce Git-tree updates.
First failure creates no folder; later failure preserves the last-good data
and success timestamp while recording failure metadata when safe. Matching
identities retain creation/first-seen timestamps in the complete replacement.

## Expired-record cleanup

The existing parser expresses publisher date-only closings as the end of
the Europe/Tallinn day, including summer/winter offsets. Cleanup compares
those aware instants; publication or event dates never authorize removal.

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

The scheduled refresh runs once daily at **12:02 UTC** (`2 12 * * *`).
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

`youthopps-ee-university-of-tartu-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.
