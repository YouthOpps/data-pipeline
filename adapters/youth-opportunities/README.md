# Youth Opportunities

Indexes the complete publisher-maintained [Browse Opportunities inventory](https://www.youthop.com/search/). Every current page and every native call ID is reconciled with the official public compact WordPress API. The homepage's latest posts and the entire historical WordPress archive are different inventories. There is no age filter, record cap, first-known-ID stop, or title-year exclusion.

## Factual scope and access

This is a minimal factual index: canonical title/link, all publisher category memberships, visible Browse location, supported closing instant/status, attribution and an original brief summary. It copies no full articles, images or eligibility prose. It does not infer age or nationality from titles, publisher location or a broad region label. Country-code filters remain empty when complete normalized country evidence is unavailable.

Bounded normal-TLS bot discovery on 15 October 2026 verified robots, the literal current Browse page and a compact public GET restricted to known native IDs. The current first page contained 15 genuine cards and declared 28 pages. These observations are not a hardcoded total: the native full scan must prove its actual current terminal page, total and unique IDs afresh.

The compact route `https://www.youthop.com/wp-json/wp/v2/posts?include=<native-ids>&per_page=100` supplies native IDs, titles, one primary category, region and `has-deadline`/`deadline` fields. Browse semantic cards supply canonical URLs, the complete `category-<slug>` memberships, titles, locations and explicit closing instants. Fresh taxonomy pages map every category slug and primary ID/name. The adapter cross-checks titles, taxonomy and active closing instants across these representations and reconciles exactly every requested/returned ID, with publisher API page capacity 100, not a total-inventory limit. It fetches **no individual article details**.

The visible Browse card is the authoritative location display. A missing visible location stays null/unknown; compact `region` never fills it or supplies country-code filters. A bounded fresh paired observation on 15 October 2026 found native ID 57093, [FIELD REPRESENTATIVE – Stephen Lewis Foundation 2026](https://www.youthop.com/miscellaneous/field-representative-stephen-lewis-foundation-2026), displayed as `Multiple Region` in its semantic Place/name span while the same ID/title's compact `region` was `Multiple Regions`. Requiring these display strings to be identical falsely rejected a genuine call. This fresh diagnostic explains the mapping defect; it is not a recovered payload or a successful retake of the earlier failed full native test. All other ID/title/taxonomy/deadline and completeness guards remain enforced.

This deliberately replaces the former rich detail path. Compact/Browse responses do not establish publication dates or full eligibility: new `published_at` is null, previous eligibility prose is replaced with an original short summary of freshly verified classification/location/deadline availability, and unproved eligibility stays unknown. Old rich fields are never copied into fresh records or called newly checked. Existing IDs and creation/first-seen history remain preserved. The ordinary native field validator retains valid legacy snapshots, while fresh factual validation rejects stale rich fields.

Robots is checked for the actual bot product token, retaining applicable legacy product-specific restrictions and stronger delays. Exact source requests, including research, robots and redirects, use `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`. The [bot page](https://youthopps.org/bot/) identifies the project. Reviewed routes are public and require no login or JavaScript execution. Normal TLS is always verified. The [publisher policies](https://www.youthop.com/policies) provide disclaimer/privacy information; no express copyright licence or publisher endorsement is asserted. No contact submission or permission request was made.

Robots-only 4xx except 429 mean unavailable REP, including checked same-host redirects. Robots 429, server/network/TLS failures and applicable rules remain stopping constraints. Actual content 401/403/451 or explicit primary-content challenges persist a protective hold. Visible challenges are checked on every content response before following redirects or handling status/backoff. Ordinary footer/navigation/aside/noscript/script/template notices, hidden DOM text and raw challenge-script markers alone are not proof of a real content refusal.

## Inventory, identity and dates

Traverse every fresh taxonomy partition and declared Browse page, requiring consistent page counts and full intermediate native pages. Exclude only an explicit Google Ads widget with no native ID, canonical URL or visible text. Native cards must be published posts with one semantic title, all category slugs and an original canonical URL. Duplicate IDs, unknown/missing taxonomy, partial pages, inconsistent source facts or a changing first-page recheck fail the whole refresh.

Request compact batches only for the exact freshly collected IDs. Require exact native-ID set equality, matching titles and primary taxonomy, consistent terminal API headers, complete category memberships and matching active closing facts. Missing, extra or duplicate compact entries fail; order may differ. At the earlier 417-card size this requires five compact batches instead of 417 article requests: about 36 source requests including robots, taxonomy, all 28 pages and the recheck, rather than 448. This is a planning example, not a fixed count or a claim that the final native test has passed.

IDs remain `youth-opportunities-<positive ASCII native post ID>`. Snapshot reads alone may accept legacy colon IDs; fresh data and publication reject them. A successful complete atomic refresh migrates matching legacy identities while preserving creation/first-seen times. Mixed-format duplicates fail, and failed refreshes leave old data unchanged.

Affirmative `has-deadline` flags (`true`, `1`, `"1"`) activate the publisher Unix closing instant, which must agree with the Browse card's aware `startDate`. Unknown flags or malformed active epochs fail. Disabled/missing flags (`false`, null, `0`, `"0"`, empty string) mean no active deadline, even when an old residual epoch exists; a conflicting displayed closing instant fails. Null/ambiguous deadlines stay unknown and are not expired by age. For example, the current compact/Browse representations for [International Physics Competition 2026](https://www.youthop.com/miscellaneous/join-now-international-physics-competition-2026) agree on `1793577540`, or `2026-11-01T23:59:00Z`. Region/location labels do not establish applicant nationality.

Complete daily reconciliation remains necessary: no publisher guarantee covers older edits, reopening, removals or ordering through an incremental watermark. Fewer physical requests come from a compact complete representation, not stopping early or dropping old calls.

## Pacing and retained state

All physical publisher starts are at least 12.1 seconds apart, with at most five per rolling minute. Every physical inventory attempt—taxonomy, Browse including its recheck, compact batches, checked redirects and same-page retries—starts at least 30 seconds after the preceding inventory attempt completes. This separate guard does not permanently increase detail spacing. Stronger existing limits win; completion extends quiet time.

A 429 stops the current run, persists at least 1,800 seconds of exponential cooldown and honors a valid longer numeric/HTTP-date Retry-After. Each strike doubles the **currently retained** physical/page gaps: 12.1/30 becomes 24.2/60; already stricter 100/300 becomes 200/600. Unusable, stale, negative or unrepresentable Retry-After never establishes an access refusal on any status; existing stronger embargoes and independently proved denials remain. A real visible challenge accompanying a 429 retains both the cooldown and protective hold. Later bounded ordinary attempts retain slower pacing.

Private owned no-follow journals and lifecycle locks serialize local work. Hosted runs restore the strict latest actually completed family attempt's source/run/attempt-bound inert artifact before source/snapshot reads, and export trusted state in finally. Older-ID queued reruns retain their exact preceding completed attempt; chronology uses attempt-scoped job completion, never mutable run update times. The current exact run/attempt must be present, and completed jobs must explicitly match their run ID, attempt, immutable head and branch. Restoration takes MAX of limits/history/backoff and OR of real holds, plus 60 seconds of startup quiet. Missing/corrupt/mismatched/ambiguous state fails closed. An existing legacy native ledger is rejected intact pending reviewed migration, never bypassed with a fresh allowance.

The sole initial pre-artifact transition is pinned to exactly seven reviewed completed attempts. Latest attempt **38763419724/1**, head `f77eed642c4544bbeed009440c406064a4f5c6ad`, was cancelled before execution: its sole job 116520331641 completed at **15 October 2026 15:49:53 UTC**, with runner ID zero, no steps, no artifact and an empty attempt-log archive. Runtime restoration checks the exact seven run/attempt/head identities and the canonical full latest jobs-inventory digest, not merely an older successful run. The last publisher-executing prior attempt, 38720084082/1, ended at 12:23:41 after collecting all 417 calls and then failing publication on a source-pair conflict; newer-ID success 38721259301 ended earlier at 11:34:33. The newly cancelled attempt is included rather than silently skipped.

Pre-migration hosted rate headers and strike history could not be recovered. `rate_failures` therefore counts only newly observed strikes, never lifetime zero history. A typed `legacy_rate_history_unknown` flag starts true; old states missing the field upgrade to unknown/true. MAX/OR merging, same-run handoffs and every mature pacing artifact preserve that unknown history. An explicit false value in another valid state cannot clear a known true flag. Initial transition approval must carry true and the full retained actual native MAX state, plus the latest cancellation/quiet boundary. The [maintainer’s exact seven-attempt full-MAX approval](https://github.com/YouthOpps/data-pipeline/issues/188#issuecomment-6196810011) binds this initial workflow transition to that reviewed state and the latest cancellation/quiet boundary. Every different/subsequent completed attempt requires its exact artifact, never a fallback to this historical transition.

## Run, budget and publication

```sh
python -B adapters/youth-opportunities/test_adapter.py
python -B adapters/youth-opportunities/adapter.py --publish
```

The sole native test performs complete real nonempty collection, validates every record and prints one complete JSON document to stdout, with diagnostics on stderr. It has no publication credential, mock or dataset output files. Development never dispatches production Actions.

The daily slot remains **12:35 UTC** (`35 12 * * *`), with scoped main-merge/manual triggers and the same publisher-family queue. The job cap remains **180 minutes**. Collection permits at most 160 minutes, reserves at least 20 minutes of actual job budget for state/token/publication, and checks complete remaining workload using retained physical/page gaps and observed response margin. No caps are raised and no successful partial result is allowed. Historical size estimates only bound planning; the fresh source traversal determines real scope. Slower retained limits that cannot fit fail closed before an unfinishable crawl.

Variable Browse response times and future inventory growth can consume a one-hour token minted before collection. The source-local `YO_PHASE=prepare/publish` flow therefore mints its source-scoped App installation token only **after** collection. Prepare restores pacing and reads a public immutable source-pair baseline without a publication credential, then stages one private 0600, no-follow ephemeral RUNNER_TEMP outcome bound to repository/source/main/run/attempt/head/code hash, completion/freshness, bounded count/body and digest. It contains no credential. Fetch failure stages an explicit failure, not partial successful records.

Publish revalidates the whole actual raw collection and exact handoff identity, reads a fresh snapshot and rejects source-pair changes since the baseline while allowing unrelated repository changes. It evaluates expiry at actual publication time, retains matching creation/first-seen and truthful collection check times, and atomically updates only `datas/youth-opportunities/{data,metadata}.json` through the optimistic non-forced Git ref path. Temporary handoffs are always removed, never uploaded or committed; the only artifact contains inert publisher state.

Complete validated raw data is cleaned of expired old/current records before successful atomic replacement. All-expired valid raw data produces a readable empty successful snapshot. Empty/partial raw retrieval fails. Fetch/token/publish failure preserves last-good data and last-success time; when possible, an explicit prepared fetch failure records failure metadata but still fails the job. First failure creates no source folder. Missing/invalid handoffs cannot write data. Trusted pacing is exported even after snapshot/auth/token/publication failure; inability to save failure status is reported honestly.

## Source display name

Published metadata uses `display_name` = **Youth Opportunities** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


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
