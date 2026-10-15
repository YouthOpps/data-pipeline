# BMW Group public youth vacancies

This adapter indexes minimal facts from the official [global career page](https://www.bmwgroup.jobs/en.html): native vacancy IDs, titles, original links, publication calendar dates, locations, legal employers, job fields and publisher career types. Summaries are brief original wording. It does not copy job descriptions, images or marketing prose, and does not collect applications or personal information.

## Inventory and original links

The page's own `jobfinder.min.js` builds the literal public HTML continuation route from `data-jobfinder-path`, with `filterSearch`, `rowIndex` and `blockCount`. The reviewed route returned the requested 50 rows without truncation; using 50 reduces requests compared with the UI's five-row default. The parser validates every expected page length, advertised total, unique native ID and canonical link. It does not guess a private API.

The four native youth filters are Apprenticeship, Internship, Recent Graduate and Talent Programs. The publisher's General career category is excluded. Dual study and vocational training follow the publisher's Apprenticeship classification; no title keyword is treated as independent proof of applicant eligibility. Working students follow the publisher's own career type. No region is excluded. UK and Ireland are retained through the same global inventory: bounded source research found 131 UK-filtered and one Ireland-filtered youth vacancies out of 691 global youth vacancies. These are dated research observations, not fixed expected counts.

Every run traverses the complete filtered inventory twice and requires exactly equal native basic facts across both frontiers. A changing total, duplicate, missing row, changed taxonomy or mismatch stops the refresh. The 5,000-item ceiling is a fail-closed safety bound, never a truncation point. There is no proven update/removal feed or stable change cursor. A publication-date ordering cannot cover older edits/removals, so every run retains complete reconciliation rather than stopping at a known ID.

IDs are `de-bmw-group-<native numeric job ID>`, validated case-insensitively after native assignment. Original links use the exact publisher-provided `/en/jobfinder/job-description-copy.<ID>.html` path. Actual sampled Singapore and Ireland pages confirmed the native ID/title/publication-date/location correspondence. Individual rich descriptions are not needed for this factual index and are not crawled during collection.

Source publication dates remain calendar dates; no midnight or publisher timezone is invented. Deadlines, application status and citizenship eligibility remain unknown. Destination countries are assigned only when the exact native location label maps to one country in the publisher's own location taxonomy. Ambiguous or missing mappings remain empty. In particular, the current taxonomy associates “Dublin” with both GB and IE; neither the city name nor employer name is used to resolve that ambiguity. Such records remain included with their literal location. Titles use `und`; generated summaries are English. Publisher country DE identifies the source operator, not vacancy location or applicant citizenship.

## Access and publisher conditions

All physical publisher requests use exactly `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`, normal inherited proxy settings and verified TLS. Only `www.bmwgroup.jobs` is allowed; redirects must remain on that host. Current robots returned HTTP200 with only a sitemap declaration. Advertised and applicable legacy protective product groups remain enforced if present. Stronger Crawl-delay/Request-rate limits prevail. Robots-only 4xx except 429 means unavailable REP; robots 429/5xx/TLS failures stop. Content 401/403, visible challenges and genuine denial bodies retain hard holds. No identity change or access workaround is used.

The [global terms](https://www.bmwgroup.jobs/en/general/terms-of-use.html) identify local career-page responsibility and the applicant tracking system. The [global disclaimer](https://www.bmwgroup.jobs/en/general/legal-disclaimer.html) reserves copyright in expressive objects and grants no intellectual-property licence. The [UK recruitment legal notes](https://www.bmwgroup.jobs/gb/en/legal-notes.html), which cover the shared UK/Ireland page, state:

> No person may copy, modify, transmit, distribute, display, reproduce, publish, license or create works from any part of this material or otherwise use it for any public or commercial use without the prior express written permission of BMW.

They also state that the website is for personal use only. These clauses are real; their application to a separately written minimal factual title/date/location/link index remains unresolved. The approved product scope permits that bounded factual indexing without treating absence of a licence or generic protected-material wording as a categorical prohibition on all facts. This is not legal clearance or a claim of publisher permission. No distinct express automated-collection or factual-metadata prohibition was found in the reviewed bodies. All three reviewed policy bodies are fingerprinted; a changed body stops for review rather than silently treating new conditions as accepted. Attribution and direct native source links remain in output.

## Pacing and retained state

One shared serialized family, `youthopps-bmw-group-publisher-v1`, controls all publisher requests. Physical starts are at least 12.2 seconds apart, with at most five starts in a rolling 60 seconds and a conservative wait after the previous response completes. Inventory pages, including the preloaded home inventory, wait at least 30 seconds after the preceding page completes. This separate page guard does not slow every policy/detail request to 30 seconds. Stronger persisted limits prevail.

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

## Redirect access checks

A visible publisher verification or denial body is checked before following a
content redirect, including HTTP 3xx responses. An ordinary redirect without a
challenge continues through the existing host, TLS, pacing and retry guards.
Robots-only handling is limited to the literal `/robots.txt` route on every
hop. A robots redirect to a content route stops before requesting that target;
its response is never treated as unavailable REP or a replacement robots policy.
Existing refusal holds, retained rate limits and source publication behavior
remain in force. This conditional-path correction does not clear an access hold
or certify a new publisher collection.

## Source display name

Published metadata uses `display_name` = **BMW Group** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Hidden page content

Challenge detection reads visible content and preserves nested suppression.
Hidden attributes, `aria-hidden="true"`, inline display/visibility declarations and
script/footer/navigation decorations do not create an access hold. Block starts
close an open paragraph in HTML button scope, without losing suppression from
an outer hidden ancestor. This is a bounded text inspector, not a full HTML5
renderer or external stylesheet engine. Explicit
visible child visibility and void elements retain their DOM behavior. Genuine
primary verification or denial text still stops content access before a redirect,
including HTTP 200, 302 and human-verification 429 responses. Existing holds and
rate-limit budgets remain retained; this correction does not clear old state or
certify a new publisher collection.

## HTTP 429 HEAD refusal handling

On HTTP 429 only, a genuine initial, unsuppressed HEAD title containing
`Access denied`, `Just a moment`, `Attention required` or `CAPTCHA` retains
an access hold alongside the existing adaptive cooldown. Ordinary rate-limit
warnings remain timed restrictions. Hidden, footer/navigation, nested, second
or post-body HEAD titles do not supply this additional refusal evidence.
A bounded obvious-HTML prefix also qualifies this 429-only check when the
response MIME is absent or wrong; ordinary successful-response handling is
unchanged. Existing access holds, stronger embargoes, retained state and request/page
spacing remain preserved.

This conditional maintenance uses actual-function inert transport controls
and qualified prior complete native evidence; it does not claim a new live
collection or alter source facts, identifiers, expiry, publication, caps or
schedules. No publisher request or production Action validates this fix.

For publisher rate/denial errors, the existing adaptive 429 state and actual
content 401/403/451 holds are saved before the bounded response read; a failed
save stops the request path. Thus a body-read failure cannot defer the first
durable cooldown save. Literal robots 451 remains unavailable REP, while
robots 429 keeps its timed backoff. Existing scoped refusal distinctions,
including any reviewed API-only annotation, are retained.


### Completion persistence and artifact failure

A failed publisher-completion state write makes this process unhealthy. It
refuses further source requests and collection reentry, and cannot export an
apparently valid pacing artifact containing older state. Export invalidates
a safely owned prior artifact through its pinned descriptor before unlinking;
unsafe files, symlinks and hardlinks are preserved and rejected. The new output
descriptor remains open through serialization and any workflow readiness
signal, so a later failure truncates it before attempting removal. No state
history, actual access hold, collection scope or pacing limit is reset.

This conditional safety repair is checked with offline actual-function failure
controls. It does not claim a new publisher collection, fresh complete current
consumer replay, or certification of other legacy pacing/rate-limit behavior.
Historical native proof is reused only for unchanged normal collection, facts,
normalization, validation, expiry and publication functions; retained record
and dependency limits are documented in the linked maintenance issue.
