# Study in Slovakia — SAIA scholarship catalogue

This source reads the actual English incoming and Slovak outgoing SAIA inventories. The reviewed frontier contains 96 English and 268 Slovak programme leaves. Eleven global programme pairs and the two Lehčí rozběh descriptions are aliases; three Visegrad English menus reconcile to fifteen specific programmes. A generic Italian multi-university directory (835) is physically verified but excluded from programme records. The resulting 348 catalogue identities include standing programme overviews and institutional grants, rather than 348 independently verified open application calls.

Original publisher titles are retained. English summaries have their own `summary_language`; host country, citizenship and institutional residence are separate concepts. Yearless deadlines stay null, and local clocks do not establish timezones. Source conflicts and conditional benefits remain qualified. Programme-specific facts and both complete inventories must match the reviewed profiles before publication; changed or new material stops collection for review instead of publishing a subset.

SAIA's current binding noncommercial terms permit reuse of its processed public information with attribution to **SAIA, n. o. – www.saia.sk** and the time copied. Each summary includes the completed UTC retrieval time. The collector checks the actual English and Slovak legal links and binding terms document, plus each required origin's robots policy, before using programme facts. It does not submit applications or access private accounts.

The English observed `All` selector equals the complete independently exhausted 96-card inventory; a capped result or new pager fails closed. The Slovak public ASP.NET pager is exhausted through its actual terminal page using fresh hidden integrity fields and session cookies. The Slovak `All` probe timed out once and its one authorized retry redirected to the publisher error page; production uses ordinary pagination. Detail URLs come only from the actual listings.

All required SAIA origins share the existing `saia-publisher-pacing-<uid>` budget and canonical `scholarships.sk` key, including the preserved legacy `saia.sk` file. Both files are locked in deterministic order and merged with the strictest interval/backoff. Every publisher GET, POST, redirect and robots request observes at least six seconds between starts and at most ten starts per rolling minute. Refusals and malformed backoff persist a blocked state.

The nominal physical frontier is 415 publisher requests; the hard ceiling is 500. Collection has 270 minutes within a 300-minute whole lifecycle, reserving 30 minutes for validation, publication and state preservation. Hard POSIX alarms cover the complete collection, parsing, credential renewal, compare-and-swap publication, output and state-export phases; socket inactivity timeouts do not extend those limits. Each network operation has at most 60 seconds and must fit its remaining budget. Export has a reserved, bounded two-minute phase within the same absolute lifecycle. The job timeout is 360 minutes. Publication renews the existing repository-scoped GitHub App token before expiry rather than assuming a one-hour token lasts through collection.

The workflow shares `youthopps-saia-publisher-v1` serialization with `sk-national-scholarship-programme`. Legacy source 17 stays disabled after its own scholarships.sk robots refusal; this adapter does not retry that origin. A reviewed one-time migration pins legacy run 37866043357/attempt 1, commit 08df07fee129bbe1c416e0f417df2c727ea423b3, and job completion 2026-10-09T00:41:52Z. It requires a fresh disabled/no-active-run check and more than 24 hours quiet. Legacy arbitrary Retry-After was not exported, so this migration explicitly acknowledges the missing history. Once any source 18 attempt completes, its latest exact run/attempt state artifact is mandatory, including failed or cancelled attempts; missing/corrupt state never falls back to the legacy seed or an older successful run.

The source 18 NSP entries are SAIA's actual permitted database tracks. They do not demonstrate recovery of source 17; reactivating 17 later requires an explicit cross-source deduplication review.

Run the single real, nonpublishing collection test with `python -B adapters/sk-study-in-slovakia/test_adapter.py`. It validates all 348 catalogue records and prints one complete JSON result to stdout, without writing dataset files. The maintained source command is `python -B adapters/sk-study-in-slovakia/adapter.py --publish`; it writes only this source's data and metadata through the standard compare-and-swap publication protocol. A failed collection preserves the existing successful data only after validating the complete owned prior data/metadata pair, including identity, language, counts, outcome, UTC chronology and completed source-copy provenance. Invalid prior pairs receive no writes. Normal TLS verification and configured proxy/CA trust remain enabled.

Licence verification uses each legal page's unique publisher document base to resolve its actual relative PDF link, then verifies the exact reviewed PDF URL and digest. A missing, duplicated or changed base or licence link stops collection.

## Expired-record cleanup

A successful refresh validates the complete nonempty collected inventory, then excludes records whose supported `deadline` has passed before preparing the atomic source data/metadata replacement. Previously published records are used only to preserve matching records' first-seen timestamps; they are never merged back into the replacement. Thus expired old records cannot survive a successful replacement. Missing deadlines, ambiguous clocks, status, age and absence from the response are not expiry evidence.

Explicit timezone-aware deadlines expire strictly after their instant. For date-only deadlines without a documented publisher timezone, cleanup waits until the closing day has ended in every legal timezone: the current date in UTC-12 must be later than the closing date. This conservative boundary retains the full closing day without inventing a publisher timezone; deletion can be delayed up to 26 hours relative to the earliest civil closing-day boundary. Unsupported or ambiguous source clocks remain null deadlines; invalid timestamp fields fail validation before cleanup. If every collected record is expired, the valid nonempty retrieval publishes an empty successful replacement. A later empty upstream retrieval still fails and preserves that last-good pair.

Fetch, parse, validation and publication failures preserve last-good data and success timestamps. Cleanup performs no separate deletion or remote write; publication updates only this source's data/metadata atomically, with the existing concurrent-update protection. The live test collects real inventory without publication; offline boundary checks and static publication review verify cleanup.

The current Slovak inventory preserves all 268 identities (English inventory: 96). Programme [191](https://granty.saia.sk/Pages/ProgramDetail.aspx?Program=191) now adds the administering agencies `(DAAD, MŠVVaM SR, SAV)` to its original title. The complete current detail fields hashed with the previous title reproduce the previous factual fingerprint exactly; only the reviewed listing/detail title and its fingerprint were updated. Eligibility, financial support, yearless June closing and the programme summary remain unchanged.

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

The scheduled refresh runs once daily at **09:52 UTC** (`52 9 * * *`).
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

`youthopps-saia-publisher-v1` serializes runs of this source and
`sk-national-scholarship-programme`. Up to 100 pending runs are retained
(`queue: max`); active runs are not cancelled (`cancel-in-progress: false`).
Unrelated publisher families may collect and publish in parallel. Existing
source-only atomic publication rebases on unrelated repository updates, uses up
to three publication attempts, and never force-overwrites newer data.
This grouping does not enable the disabled legacy scholarship workflow.

## Source display name

Published metadata uses `display_name` = **SAIA** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


## Journal generation integrity (#295)

The two sorted SAIA journal members retain their verified private directory,
actual read/write descriptors and acknowledged byte generations across reopen.
Both members are checked before either is modified and immediately before a
publisher request. A replaced, changed, missing or read-only substituted journal
fails closed instead of adopting a fresh budget or overwriting stronger history.
Commits serialize in memory, verify both members, truncate to zero and fsync,
complete short writes, truncate to exact length, fsync and verify exact readback. A partial two-member commit can leave invalid or unequal files;
this accepted availability failure stops access and does not claim multi-file
atomicity or recover through an older artifact.

Uncertain storage invalidates process readiness and owned output through its
retained verified descriptor before optional pathname cleanup. Collection-lock
and journal cleanup preserve a body exception and never close a foreign reused
descriptor. If the first identity query of a freshly acquired descriptor fails,
that unverified descriptor remains open until process exit; independently verified
parent descriptors are released. This bounded terminal failure preserves the
primary exception and permits no retry, further publisher access or healthy owned
export. Otherwise healthy close failures are fatal, including cleanup after
export. Normal inventory, facts, IDs, expiry, legacy artifact decoding, source
schedules and existing request/backoff limits are unchanged.

This narrow IO maintenance is checked through actual functions on isolated
private files with network denied, including both journal members, inert
transport, owned exporters and lifetime cleanup. Historical genuine collection
evidence is documented in merged PRs [#110](https://github.com/YouthOpps/data-pipeline/pull/110)
and [#186](https://github.com/YouthOpps/data-pipeline/pull/186), as applicable to
the source. Those public immutable review statements and unchanged normal
code provide qualified provenance; expired raw captures are not replayed or
represented as retained. This maintenance performs no publisher crawl, Action
operation, actual journal mutation or data publication and establishes no fresh
native or production success. Existing SAIA workflow holds remain in force.
