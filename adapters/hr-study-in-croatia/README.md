# Study in Croatia adapter

Collects the official Agency for Mobility and EU Programmes portal's bounded published scholarship and training inventory. Original English programme/call titles, short authored factual summaries and official own links are retained. Output belongs only to `datas/hr-study-in-croatia/{data,metadata}.json`.

## Reviewed inventory

Twenty public HTML inputs cover the funding/exchange/learning pages, five terminal news pages with thirty distinct cards, six material news leaves, source identity and legal/FAQ pages. Twelve distinct identities comprise five calls and seven programme overviews:

- Incoming Erasmus+, CEEPUS and bilateral scholarship frameworks, with their stated institutional/country conditions and unknown current closing/amounts.
- Croatian-heritage diaspora scholarship overview for 2025/26: EUR175 monthly for ten months, full-time public higher-education students studying in Croatia, Bosnia and Herzegovina, Montenegro, Kosovo, North Macedonia or Serbia. The 2,000 places are capacity. Heritage/living abroad does not establish a Croatian-passport whitelist. Electronic and paper documentation are both required; further selection/closing needs the official call.
- Distinct historical bilateral calls for 2023/24 and 2024/25. Public accredited institutions and study/research mobility periods are preserved, including the July/August exclusion. The 2023 closing is 31 March 2023. The 2024 wording is "before 5 April 2024"; its exclusive/inclusive cutoff is not resolved, so no inclusive UTC-day deadline is invented.
- Croatia/North Macedonia education cooperation for 29 September 2024–31 December 2028: domestic tuition conditions for North Macedonian citizens entering full degrees in Croatia, plus up to 24 annual advanced-study exchange scholarships and summer language-course scholarships. Domestic conditions do not guarantee free tuition; the nationality condition on the tuition measure is not a blanket restriction on all awards.
- Zagreb's 2026/27 BioMedMath second application round, dentistry exam windows and medicine online-exam call. Application fees are distinguished from tuition or awards. Dentistry's two mode-specific closings remain in one summary with no single invented programme-wide deadline.
- A beginner Croatian e-learning course and Rijeka's Croatian language/culture/civilisation school. Online learning has no physical host; the school's June/July programme period is not an application closing or a year-specific call.

Generic provider/course directories, six Croaticum catalogue formats, special enrolment quotas, testimonials, statistics, fairs, brochures, apps/resources and unlisted job matching are excluded. Country allocations, examination modes, institution lists and capacity do not create cloned awards. External administrator/application links are routing references; this adapter does not scrape external catalogues or submit forms.

All twelve summaries are English and fit 580 JavaScript UTF-16 units without truncation. Unknown closings and passport eligibility remain unknown. Three unambiguous dated closings are expired; nine records retain unknown availability. Historical editions do not establish a new current call. The news leaves incorrectly display today's date; their six exactly identified display-date fields are ignored, while original indexed dates, every material body/date/amount/condition and literal destination remain guarded. Timezone-less historical publication dates do not become invented UTC timestamps.

## Access and guards

The actual robots policy allows public routes. Portal identity, privacy and cookie statements were reviewed; no factual-summary permission requirement was observed there. The footer Content Disclaimer is a 200 soft404, not a valid licence or permission ban. No open licence is asserted. Use original summaries, source credit and official links; do not copy full articles, images, recipient datasets or application documents.

Each runtime freshly checks the one origin's robots. All physical publisher
requests, including robots, redirects and retries, share
`youthopps-studyincroatia-publisher-v1`: at least 12.2 seconds after the previous
physical response completes and at most five starts in any rolling 60 seconds.
The five news inventory pages additionally wait at least 30 seconds after the
previous physical inventory attempt completes, including redirects and the
existing one bounded HTTP 503 retry. Separate detail and policy requests retain
the physical floor. Stronger known limits, embargoes and actual refusals remain.
No alternate identity, route or transport is attempted after refusal.

HTTP 429 stops the run without retrying that request. Persist at least 1,800
seconds of cooldown, exponentially increasing for each newly observed 429, and
any longer valid Retry-After or known embargo before reading bounded error
HTML. Double the current retained physical and page intervals, preserving
stronger limits. Ordinary rate-limit wording creates a timed restriction;
genuine visible human-verification or challenge HEAD-title evidence retains
both the timer and a hard hold. Malformed, stale or unusable Retry-After alone
never creates a hard hold. Genuine content 401/403/451 and visible challenges
remain held; body-read failure retains already persisted restrictions.

The bounded HTML guard runs before following any redirect, including obvious
HTML served with a missing or incorrect MIME type. Hidden content, scripts,
unrelated footer/navigation and narrowly identified informational instructions
do not alone establish a challenge. Literal robots purpose is checked at every
physical hop; a robots redirect cannot turn content into robots. Robots-only
4xx responses except 429 mean unavailable REP without inventing Disallow or
clearing existing holds. Valid explicit robots restrictions remain effective.

Old schema-1 artifacts remain readable. Optional interval, page interval,
new-event count and typed historical-unknown fields retain all known starts,
embargoes and holds. Missing old counters mean no events observed since this
migration, not zero lifetime events. Restoration MAX merges every timing/count,
bounded unions the last ten starts, and OR merges holds and historical unknown.
Unsupported state fails closed without resetting the original journal. If
physical completion cannot be persisted, export refuses that state and
invalidates any safely owned stale artifact instead of presenting incomplete
restrictions as a valid handoff.

The material guard excludes only the reviewed random `DidYouKnowCnt` trivia sidebar, outside material content with its exact three-child shape, and the six plain `NewsOsobnaDatum` display dates. Literal links inside trivia remain guarded. Unknown counts, attributes, placement, date markup or unrelated text/link changes fail closed for fresh review. No PDF or guessed pager/API is required.

## Collection and publication

```sh
python -B adapters/hr-study-in-croatia/test_adapter.py
python -B adapters/hr-study-in-croatia/adapter.py --publish
```

The sole live test performs one real nonpublishing collection, validates all twelve records and prints complete JSON to stdout. Hold stdout in memory; do not save normalized records or provide publication credentials to tests. The matching workflow's sole application command publishes this source's own pair atomically.

Twenty HTML inputs plus one nominal robots request preserve the complete
reviewed frontier. A 375.2-second nominal pacing estimate includes startup quiet
and inventory spacing before response/history overhead; the previous actual
collection step was 255 seconds. These are planning evidence, not current
source or runtime acceptance. Redirects, retries or stronger retained limits
can exceed the unchanged cap and then fail closed; no input is truncated.
The complete prepublication phase, including history/artifact restore, remains
capped at 900 seconds with the existing 120-second request headroom guard.
Publication and conditional failure reconciliation each have 180 seconds.
Whole-process cap remains 1290 seconds, nonexport work 1260 seconds, with up to
30 remaining seconds for trusted state export. POSIX main-thread absolute
alarms cover reads, sleep, parse, output and cleanup; expired phases do not
renew. The collector lock stays held through bounded export. The unchanged
30-minute workflow mints its one-hour App token immediately before the command.

Local and hosted collection require complete authenticated family history and
the newest exact owned artifact before any publisher request. Exact-attempt
jobs establish completion chronology, including reruns, with explicit
workflow/source/head/attempt and main-branch binding. Hosted history must
contain its exact current run; local history rejects every active or queued
family attempt. Missing, expired, mismatched or incomplete newest state fails
closed, with no older fallback or mature bootstrap. Artifact digest, source,
run and attempt are checked before restrictive MAX/OR restoration into the
original family journal under its existing locks. A missing local journal
requires the newest owned state and does not establish a new allowance.

Local collection additionally requires the maintainer to pause this exact
workflow. Its fixed ID, own workflow path and `disabled_manually` state are
freshly checked before history and again immediately before the first publisher
GET after startup quiet. A changed state stops before that request; this is not
per-page GitHub polling. Keep the workflow paused for the entire local run.
The old first-family workflow marker cannot replace mature history. First collection failure creates no source folder. Later failures preserve last-good data and success time, with sanitized durable failure status only when safe. Guarded compare-and-swap protects siblings and newer/uncertain source pairs. Successful collection records attempt start as `last_success_at` and completion as `last_checked_at`. Before collection or writes, an existing source pair must have coherent identity, status, error, record count and aware timestamps; every record must share its metadata check time. Invalid prior pairs receive no failure metadata write.

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
access; restrictive rate and delay directives remain effective. The advertised identity and durable family identity remain unchanged; current
pacing and backoff follow the access section above. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **16:45 UTC** (`45 16 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Each physical news inventory hop waits at least 30 seconds after the previous
inventory attempt finishes, including redirects and the bounded existing 503
retry. Detail and policy pages remain separate under the physical floor.
Inventory scope, all twenty fingerprints, twelve record identities, parsing,
validation, expiry and atomic publication are unchanged.

## Workflow concurrency

`youthopps-studyincroatia-publisher-v1` serializes runs of this source. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Agency for Mobility and EU Programmes** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
