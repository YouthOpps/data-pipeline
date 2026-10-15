# Arts Council Malta

This standalone adapter indexes the official [Funding Programmes](https://artscouncilmalta.gov.mt/en/apply-now/) and [Opportunities](https://artscouncilmalta.gov.mt/en/opportunities/) listings. It publishes only `datas/mt-arts-council/{data,metadata}.json`. The source is an independent factual index: original titles, publisher links, explicit listing states, supported basic classifications and short original summaries. It does not copy descriptions, images, application documents or personal contacts.

## Complete inventory, reduced field depth

Every declared funding page is retrieved, following its literal `data-next-page` link until its native `data-page` reaches the stable `data-max-page`. The reviewed first page declares eight pages. The last template may still advertise a ninth link; a link beyond the explicit maximum is not a further declared page. A changed maximum during a scan, a missing/invalid continuation, a cycle, duplicate final identities or an empty inventory fails the collection instead of truncating it. There is no fixed record count or arbitrary page cap.

The opportunity hub embeds its entire inventory in `.publications .element-item` cards. On 15 October 2026, all 84 cards were present in the initial response, with contiguous native `data-relevance` positions 1–84. The observed own `script.js` contains two active load-more handlers (opportunities and funded projects). Both increase a visible counter and slice existing Isotope `filteredItems`; they reveal hidden elements without fetching another result page. The adapter follows that exact script link and verifies both handlers and reveal functions on every collection. Unsupported network continuation or changed loader semantics fails collection. It never invents an API or search URL.

This replaces the former 219-request material/qualification audit. The new inventory covers every native listing page and the complete preloaded opportunity hub, while collecting fewer fields per listing. It does not fetch 130 detail pages or their PDFs and does not retain the old 138 hardcoded profiles, module/annual-edition expansions, qualification recipes or document fingerprints. A grouped programme listing remains one programme overview; it does not become multiple independently described modules, places or awards. The prior 138-record count is not the new count or evidence of current collection.

Each retained record represents a concrete programme/call card and its literal individual publisher URL. Generic funding guidance, generic opportunity/call directories, fiscal deductions, ordinary film/symposium attendance, an external call-catalogue referral and umbrella initiative news are explicit exclusions. The named training/grant rows under the umbrella remain independently indexed. The known Artivisti announcement crosspost is represented by its funding canonical; the alternate URL remains provenance. Changed known crosspost titles fail review rather than silently collapsing a new call. New genuine cards are collected dynamically; no fixed-count shortcut or first-known-record early stop is used. No safe incremental update boundary is established, so each run performs the complete listing scan.

Identity uses `mt-arts-council-` plus a deterministic digest of the exact canonical individual URL. It does not depend on card position, mutable titles or generated presentation IDs. Funding cards must also retain a valid native `post-<id>` marker. Case-insensitive duplicate final IDs are rejected. The index can contain a standing programme and a distinct explicitly advertised call, without manufacturing one record per capacity place.

## Dates, closure and unknown fields

Only a funding card's explicitly labeled application `Deadline:` and complete day-month-year value becomes a deadline. Unlabeled card dates, opportunity `data-date` publication dates, event dates, programme duration and inferred execution years are not application cutoffs. A supported day-only deadline remains a date; its closing time and zone are unknown. Cleanup conservatively retains that date until it has ended everywhere, using the UTC−12 calendar boundary.

The publisher's explicit “closed for applications” class or literal trailing “closed” title is retained as a closure fact in the summary/provenance and status. It never creates an invented passed deadline. An explicit open label is also recorded as the publisher's statement, not a guarantee of admission or funding. Explicitly source-closed records remain visible in the complete raw audit but are excluded from publication even when their cutoff is future or missing; the original cutoff field is not changed. Other opportunities have unknown current availability. A future date does not overwrite an express source closure, and old publication dates alone do not imply expiry.

No eligibility, benefits, destination, citizenship requirement or qualification is inferred from the publisher's Maltese location or the title. Host/eligible-country arrays remain empty and location stays null. Title language remains unknown; summaries are English. The source publisher country is MT. Classification uses the funding-list context or clear title words for jobs, training and traineeships; other classifications remain unknown. Original links carry the provider's full terms and current application details.

## Access and reviewed recovery

The official listing routes returned genuine HTTP 200 with the advertised bot identity on 15 October 2026; `robots.txt` returned HTTP 404. Under the selected [RFC 9309 unavailable policy](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3), verified robots-only HTTP 400–499 except 429 means unavailable REP rules. This does not establish a reuse licence or permission to bypass actual content restrictions. Valid applicable new/legacy product rules and stronger crawl limits remain binding. Robots 5xx, TLS/DNS/network failure or invalid policy do not become empty permission. The source's ordinary rights notices are acknowledged; this output is limited to independent basic facts and links, with no claimed open licence or copied full material.

The prior hosted failure was specifically the first robots request: [run 38073040828](https://github.com/YouthOpps/data-pipeline/actions/runs/38073040828), attempt 1, at head `a97e28475634b9ff61489a11cd6378c9dafc67ae`. Its owned artifact 11677184845 has digest `sha256:38476379c439f6e907b2ee8787595ce1b3731f0c7add08477d0d1f1cceffaffa` and exactly one native start. [Run 38073085228](https://github.com/YouthOpps/data-pipeline/actions/runs/38073085228), attempt 1 at the same head, retained that same start and refused before new source traffic. Its artifact 11678320011 has digest `sha256:7509f5ad862fe2460a925db4275942e4e766fcc809737e5751173f6b9d3bffb6`.

The separately reviewed robots migration is limited to that exact original two-attempt chain, with the two additional proven zero-publisher failures described below, complete source history, immutable heads and both owned full artifact digests/states. It reclassifies only the inherited robots-only refusal bit. All actual history, maximum embargoes and any stronger local limits/refusals remain retained; an unknown or mixed hold is not cleared. The known latest embargo was 1791654520.8156075, not an assumed zero. The first migration also carries the reviewed native/research guard 1792070136.1917381 (the MAX of actual completion plus retained physical/page spacing and embargo). This is a conservative infrastructure handoff, not a reset of unknown state. A fresh startup adds at least 60 seconds of quiet. Any new completed attempt requires its own newest trusted artifact; this migration cannot substitute for a missing, expired, corrupt or untrusted later artifact. A changed original history fails closed. The former first-family bootstrap is removed because this family already has production history.

The first merge delivery then failed before publisher access: [push 38745452175](https://github.com/YouthOpps/data-pipeline/actions/runs/38745452175) and [manual 38745452432](https://github.com/YouthOpps/data-pipeline/actions/runs/38745452432), both attempt 1 at `e60eab85cf26eb1bfa6b0c9eaa93d8f13924b5fc`. The push saw the queued manual attempt and correctly refused the original two-run migration; the manual then correctly refused the push's missing artifact. Neither reached collection or generated publisher timing state. Recovery excludes only these two failures after fresh validation of exact repository/source, workflow, event, attempt, head, failure conclusion, complete job inventory hash, complete job-log hash and expected terminal error, and an empty artifact inventory. These checks apply both to chronology selection and the migration's complete-history guard. Changed or unavailable evidence fails closed; any unknown later attempt remains subject to normal strict artifact restoration. This is not a general missing-artifact fallback.

The reviewed native journal has SHA-256 `75f52de2a8b2e7d613cb039cb5f22bdf61bb6b56c5c870dcdb3deec5817d8785`. Recovery carries its complete starts, observed/completion timestamps, physical/page intervals, repeated-429 count, embargo and refusal bit, alongside both original owned states. Numeric restrictions use MAX, retained starts use their union, and live local refusals remain binding; only the two exactly proven robots-only bits are reclassified. The journal had no native refusal or repeated 429. The absolute page guard remains 1792070136.1917381, and hosted startup adds at least 60 seconds. [Maintainer approval](https://github.com/YouthOpps/data-pipeline/issues/25#issuecomment-6194427011) binds this full-state handoff. Native collection, normalization, cleanup and publication behavior are unchanged; the complete 120/74 evidence below is reused without a new publisher crawl. Offline actual-function controls cover the recovery and fail-closed branches, not production success.

The separate maintainer enables the disabled workflow before merging the accepted recovery, first confirming no queued/active run. Only the resulting automatic merge push is intended. An unexpected old-head attempt requires provenance review; do not pair enablement with an additional manual dispatch or ignore a new missing artifact.

Actual content HTTP 401/403/451, clear visible human-verification/refusal and genuine robots Disallow retain a hard hold requiring reviewed recovery. Primary/body visible text is checked even outside expected cards and in long documents; footer, navigation, aside, noscript, script and semantic template decorations are excluded. Ordinary captcha JavaScript is not a content refusal. Redirects stop before an unreviewed destination; normal TLS verification is never disabled. A timed 429 ends the current attempt, retains at least 30 minutes of backoff (exponentially increasing on subsequent failures), and doubles the current physical and page intervals, including already stricter restored intervals. Longer valid Retry-After wins. Later attempts fast-fail during the embargo; once it expires, a normal bounded retry still uses the slower retained limits. On 429, malformed Retry-After is unusable timing evidence: the durable exponential minimum still applies without inventing a permanent content refusal. Unsupported retry instructions on other responses fail closed. A 429 alone is not a permanent content refusal.

## Pacing, schedule and publication

All source research and collection requests use exactly:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The existing source family `youthopps-artscouncilmalta-publisher-v1`, private local journal and lock are preserved. Legacy state upgrades retain starts, embargo and refusal while increasing the physical floor to 12.1 seconds and the rolling allowance to at most five actual starts per 60 seconds. A completion guard covers errors and body reads as well as successful responses. Every successive inventory page, including the first preloaded opportunity hub after funding pagination, waits at least 30 seconds after the previous inventory completes. Robots and the loader asset share the physical budget without being promoted into inventory pagination. Durable 429 adaptation applies to both intervals. Local collection and production for this publisher must not overlap across machines; local locking alone cannot serialize separate runners.

The existing daily **04:00 UTC** schedule (`0 4 * * *`), merge-path/manual triggers, repository/main guard, source credentials, serialized queue, 50-minute job and one-hour token lifetime are unchanged. The complete smaller listing scan fits the existing 2,100-second prepublication/collection and 2,490-second whole-run caps without extending them. Publication/failure phases remain bounded at 180 seconds and trusted export at 30 seconds. Production restores state before snapshot/authentication reads, keeps its lifecycle lock through export, and scans exact completed attempt jobs chronologically, including prior completed attempts hidden by queued older-ID reruns. Missing or ambiguous history/artifacts stops before publisher access. Finalized state is exported atomically, including on failures; a fresh output signal alone enables artifact upload. Artifacts contain infrastructure state, not records or credentials.

Fresh raw retrieval must be complete, valid and nonempty before expiry filtering. A successful refresh replaces this source atomically, removing expired old and fresh records together. Current records and ambiguous/missing deadlines remain eligible unless the publisher explicitly marks applications closed. Validated explicit source closure excludes a record from publication independently of its cutoff, without fabricating a passed deadline. A valid all-expired collection may publish an empty success pair; subsequent empty snapshots remain readable. Empty/partial/invalid upstream retrieval fails. Failure preserves last-good data and success time; before first success it creates no source folder. Source-scoped optimistic writes do not overwrite a newer source pair or another source.

## Run and acceptance

Python 3.12/POSIX, standard library only. The folder runs independently when copied.

```sh
python -B adapters/mt-arts-council/test_adapter.py
python -B adapters/mt-arts-council/adapter.py
```

The single native test performs a complete genuine nonpublishing collection, validates all records and prints one complete JSON array on stdout. Diagnostics go to stderr; it does not save datasets or use publication credentials. The workflow's sole application command is `python -B adapters/mt-arts-council/adapter.py --publish`. DATA_SOURCE_TOKEN is used only for source publication, and GITHUB_TOKEN reads trusted workflow artifacts. The complete native test on 15 October 2026 passed in 340.404 seconds: 47 funding cards and 84 opportunity cards yielded 120 raw records after 10 explicit exclusions and one Artivisti crosspost merge; final explicit-closure/deadline cleanup of the complete retained actual source yields 74. These are observed counts, not acceptance caps. Eleven actual source requests had a minimum physical start gap of 12.931 seconds, at most three starts per rolling minute, and a minimum inventory completion gap of 30.001 seconds. The actual current website consumer accepted all 120 raw, 74 filtered and a valid derived-empty result in memory. Conditional state/access guards were subsequently checked against every retained actual source response without new publisher requests; every non-temporal record field remained identical. Independent QA is required before PR delivery. No production enable, dispatch, source publication or website success is certified by local collection; a separate maintainer owns those gates.

## Source display name

Published metadata uses `display_name` = **Arts Council Malta** for the visible
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
