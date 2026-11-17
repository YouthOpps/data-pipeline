# BMW Group public youth vacancies

This adapter indexes minimal facts from the official [global career page](https://www.bmwgroup.jobs/en.html): native vacancy IDs, titles, original links, publication calendar dates, locations, legal employers, job fields and publisher career types. Summaries are brief original wording. It does not copy job descriptions, images or marketing prose, and does not collect applications or personal information.

## Inventory and original links

The page's own `jobfinder.min.js` builds the literal public HTML continuation route from `data-jobfinder-path`, with `filterSearch`, `rowIndex` and `blockCount`. The reviewed route returned the requested 50 rows without truncation; using 50 reduces requests compared with the UI's five-row default. The parser validates every expected page length, advertised total, unique native ID and canonical link. It does not guess a private API.

The four native youth filters are Apprenticeship, Internship, Recent Graduate and Talent Programs. The publisher's General career category is excluded. Dual study and vocational training follow the publisher's Apprenticeship classification; no title keyword is treated as independent proof of applicant eligibility. Working students follow the publisher's own career type. The global page must have no regional prefilter. Country facets are dynamic,
not a promise that GB and IE must always appear. The original fixed GB+IE
assertion stopped collection when IE disappeared from the current taxonomy.
Issue [#391](https://github.com/YouthOpps/data-pipeline/issues/391) research
verified the actually linked global route and the UK page's `location=GB`
prefilter. Its complete current global pass contained 503 youth vacancies;
the UK youth filter returned zero. An empty regional response is not positive
material-subset evidence. The older Dublin native ID 195517 was absent from
that complete current listing. These observations do not establish an expiry
date or independent worldwide coverage beyond the advertised global jobfinder.
No unadvertised IE filter is queried, and country absence alone never deletes
a record. Successful replacement still requires two equal complete frontiers.

Every run traverses the complete filtered inventory twice and requires exactly equal native basic facts across both frontiers. A changing total, duplicate, missing row, changed taxonomy or mismatch stops the refresh. The 5,000-item ceiling is a fail-closed safety bound, never a truncation point. There is no proven update/removal feed or stable change cursor. A publication-date ordering cannot cover older edits/removals, so every run retains complete reconciliation rather than stopping at a known ID.

IDs are `de-bmw-group-<native numeric job ID>`, validated case-insensitively after native assignment. Original links use the exact publisher-provided `/en/jobfinder/job-description-copy.<ID>.html` path. Actual sampled Singapore and Ireland pages confirmed the native ID/title/publication-date/location correspondence. Individual rich descriptions are not needed for this factual index and are not crawled during collection.

Source publication dates remain calendar dates; no midnight or publisher timezone is invented. Deadlines, application status and citizenship eligibility remain unknown. Destination countries are assigned only when the exact native location label maps to one country in the publisher's own location taxonomy. Ambiguous or missing mappings remain empty. Earlier research observed an ambiguous Dublin mapping. Current research has no Dublin facet. Neither city names nor employer names resolve missing or ambiguous country evidence; any such listing remains included with its literal location. Titles use `und`; generated summaries are English. Publisher country DE identifies the source operator, not vacancy location or applicant citizenship.

## Access and publisher conditions

All physical publisher requests use exactly `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`, normal inherited proxy settings and verified TLS. Only `www.bmwgroup.jobs` is allowed; redirects must remain on that host. Current robots returned HTTP200 with only a sitemap declaration. Advertised and applicable legacy protective product groups remain enforced if present. Stronger Crawl-delay/Request-rate limits prevail. Robots-only 4xx except 429 means unavailable REP; robots 429/5xx/TLS failures stop. Content 401/403, visible challenges and genuine denial bodies retain hard holds. No identity change or access workaround is used.

The [global terms](https://www.bmwgroup.jobs/en/general/terms-of-use.html) identify local career-page responsibility and the applicant tracking system. The [global disclaimer](https://www.bmwgroup.jobs/en/general/legal-disclaimer.html) reserves copyright in expressive objects and grants no intellectual-property licence. The [UK recruitment legal notes](https://www.bmwgroup.jobs/gb/en/legal-notes.html), which cover the shared UK/Ireland page, state:

> No person may copy, modify, transmit, distribute, display, reproduce, publish, license or create works from any part of this material or otherwise use it for any public or commercial use without the prior express written permission of BMW.

They also state that the website is for personal use only. These clauses are real; their application to a separately written minimal factual title/date/location/link index remains unresolved. The approved product scope permits that bounded factual indexing without treating absence of a licence or generic protected-material wording as a categorical prohibition on all facts. This is not legal clearance or a claim of publisher permission. No distinct express automated-collection or factual-metadata prohibition was found in the reviewed bodies. All three reviewed policy bodies are fingerprinted; a changed body stops for review rather than silently treating new conditions as accepted. Attribution and direct native source links remain in output.

## Pacing and retained state

One shared serialized family, `youthopps-bmw-group-publisher-v1`, controls all publisher requests. Physical starts are at least 12.2 seconds apart, with at most five starts in a rolling 60 seconds and a conservative wait after the previous response completes. Inventory pages, including the home inventory configuration, wait at least 30 seconds after the preceding page completes. This separate page guard does not slow every policy/detail request to 30 seconds. Stronger persisted limits prevail.

429 stops the current run, doubles both actual retained physical/page gaps and persists at least 30 minutes of cooldown without a valid stronger Retry-After. Repeated 429 increases cooldown exponentially. A timed 429 alone does not create a permanent hard hold: a later bounded run can retry after the embargo at the slower retained pace. Content denials remain hard holds, including a clear human-verification challenge inside a 429 HTML response; its adaptive embargo is retained too. Only finite positive supported Retry-After delays or timezone-aware future dates can extend the embargo; malformed, stale, negative or unrepresentable values preserve the exponential minimum and known MAX state. Cooldown continues to increase beyond the ninth event; unsupported extreme counter states fail before another request rather than overflowing or resetting history. Research history is merged into native state without resetting starts, completion, embargoes or stronger intervals.

Collection has a fixed 25-minute phase, with a 30-minute workflow job and daily 18:05 UTC schedule. A traversal preflight includes both complete frontiers and a 120-second reserve. Slower retained post-429 pacing can make a complete traversal infeasible; the adapter fails closed rather than increasing caps, dropping pages or claiming every later attempt must succeed. The source App token is created before collection; the normal bounded collection remains within its lifetime.

Production restores the newest actual completed family attempt before reading publication data. Exact attempt endpoints and exact jobs establish completion and head identity, including previous completed attempts of queued older-run reruns. Mutable update times do not establish chronology. Missing/expired/ambiguous state fails closed, including failed attempts before first successful publication. Restoration merges the full MAX budget and retained holds; trusted state is exported even if a later snapshot read fails. A first family run requires explicit reviewed full-native-state bootstrap approval and complete workflow-history proof; an empty/default seed is refused. Inert pacing artifacts contain no records or credentials.

## Test, expiry and publication

The sole live test performs a complete real nonpublishing collection and emits the full JSON array to stdout:

```sh
python -B adapters/de-bmw-group/test_adapter.py
```

It refuses inherited publication credentials. Keep the full output in RAM for validation and the actual current website consumer; normalized dataset files do not belong in development workspaces.

The main-branch workflow publishes using:

```sh
python -B adapters/de-bmw-group/adapter.py --publish
```

Only this source's `datas/de-bmw-group/data.json` and `metadata.json` are atomically updated. A complete successful refresh replaces the whole source snapshot, preserving stable creation/first-seen timestamps for existing IDs. Explicit expired deadlines are removed before publication; date-only unknown-zone deadlines remain through the latest civil timezone's end of date, while unknown deadlines imply no expiry. All-expired validated raw data may publish an atomic empty success envelope. A failed/incomplete retrieval preserves last-good data and success time; a first failure creates no placeholder source pair. Large snapshots use digest-verified Git blobs, and compare-and-swap branch updates protect concurrent publications. Native tests do not prove production publication; maintainer merge and actual immutable pair/artifact verification remain separate.

## Durable continuity repair — issue #391

The same publisher family and collection-lock inode remain authoritative.
Schema 3 records a durable `in_flight` reservation before every physical
request and before applying a cached robots policy. It clears that reservation
only after complete response processing, restrictive policy persistence and
successful response closure. A partial read, unknown transport failure, TLS
failure, interrupted close or uncompleted persistence keeps the reservation
or invalidates the current owned journal. Restart cannot substitute a weaker
artifact or a blank state. Current parent, inode, byte generation and the
original held lock are checked throughout writes and cleanup. These are
advisory-lock ownership checks, not filesystem compare-and-swap guarantees.

This repair supersedes the historical header-only 429 stop from #328. The
adaptive cooldown, stronger Retry-After and status-based refusal are durable
before reading at most 64 KiB of a 429 body. Only a completely read, assessed
and closed response can settle its reservation. A genuine visible challenge
retains a hard hold as well as the timed embargo; an incomplete body or close
remains pending. Robots-specific 4xx semantics do not weaken content refusal
handling. Applicable legacy bot groups and stronger REP constraints remain.

Only the exact last authenticated six-key owner, run 43998513268 attempt 1,
artifact 14290823021, can cross the legacy decoder. Its immutable ZIP digest,
producer head and typed state are pinned. The locally migrated journal must
match the exact settled research bytes. The migration retains MAX constraints
from all 41 captured research journal witnesses, the original owned state,
and the union of 22 actual durable starts. Both one-use research guards and
complete capture digests remain in provenance. Missing historical access,
hold and 429 information stays explicitly unknown. No unknown owner, missing
artifact, different legacy journal or pending modern state receives a fallback.

The artifact API redirect is accepted only to an HTTPS subdomain of
`blob.core.windows.net` or `actions.githubusercontent.com`. Every subsequent
storage hop must remain within those supported signed-storage families.
Credentials, explicit ports, trailing-dot hosts, deceptive suffixes and other
public publisher hosts are rejected before their destination request. Ordinary
infrastructure API calls use only `api.github.com` and cannot follow redirects.
Storage requests carry no API authorization header.

New owned artifacts have nine strict keys: the existing ownership fields plus
producer head, adapter digest and state digest. Restoring them authenticates
both the immutable producer source and the complete state. Partial writes,
failed fsync, replacement journals, lost locks and output failures block a
healthy handoff. Private output descriptors remain retained through readiness
signalling and cleanup, so a later failure invalidates only owned output.

The 25-minute collection cap is unchanged. A nonrenewable 29-minute outer run
bounds metadata, collection, publication and export within the existing
30-minute Action. Absolute alarms interrupt slow-trickle bodies; cancellation
also interrupts blocked closure/finalizers, retaining pending ownership.
Local access requires the exact disabled workflow and terminal family history.
The unchanged original native test obtains this lifecycle through `collect()`.

Current offline evidence replays all actual research inputs to 503 records,
using the same captured pages for both parser passes. It does not claim two
live passes, a fresh successful native run, production publication or an
actual expired-to-empty subset. Hosted execution deliberately remains closed
until the final native MAX handoff is reviewed. Historical source facts, IDs,
publication dates, unknown deadlines and the original sole live test remain.

Published metadata uses `display_name` = **BMW Group**; source identifiers,
legacy names and attribution remain unchanged. Success and failure metadata
both retain that label.
