# Youth for Europe

Standalone factual discovery from [Youth for Europe](https://youthforeurope.eu/), operated by Associazione Joint. The current official [global Explore inventory](https://youthforeurope.eu/explore/?type=general&sort=latest) replaces the assigned `/all-the-opportunities/` URL, which currently serves a page-not-found template despite HTTP 200.

## Access and reuse evidence

Reviewed on 14 October 2026: [robots.txt](https://youthforeurope.eu/robots.txt) has only MSNBot/bingbot five-second crawl delays, no rules excluding this adapter. The linked [privacy policy](https://youthforeurope.eu/privacy-policy/) concerns personal data, accounts and cookies; it contains no automated-access prohibition. Homepage, browse and account navigation disclosed no separate general reuse terms. No open content license is claimed. The adapter publishes attributed titles, original publisher links, native IDs/types, and explicitly labeled factual dates/locations/eligibility; summaries stay empty, and descriptions, images, personal contact details and downloadable documents are not republished. It does not submit forms, register accounts, load tracking assets or retrieve third-party resources.

The advertised [REST index](https://youthforeurope.eu/wp-json/) exposes no public project collection. The advertised [RSS feed](https://youthforeurope.eu/feed/) contains blog articles and testimonials, not the opportunity inventory. Collection therefore uses the public native global browse and public project details. Robots are checked before every endpoint; denials, HTTP errors, broken native markup and incomplete pagination fail closed. Requests, redirects and retries share persistent source/host pacing and process serialization: at least 12.2 seconds between actual physical starts and at most five starts per rolling minute. Retry-After and stricter robots delays remain binding. The original publisher journal path/inode and lifecycle lock remain in use; state is extended, never discarded to obtain a fresh allowance.

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
pacing and backoff use the current stricter limits documented below; the durable budget identity is preserved. Existing
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

Inventory page continuations and partitions wait at least 30 seconds after the
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

## Durable pacing migration and bounded runtime

This issue preserves the complete global browse and every individual detail,
with all existing labeled factual fields, stable IDs, expiry rules and atomic
source publication. After collecting details, a second complete browse must
match the first exact native IDs, titles, URLs and types. Count/page consistency,
duplicates or changed closing coverage fail rather than publish a partial index.
No safe incremental cursor is established, so every run reconciles the full scope.

The approved bounded research on 15 October 2026 fetched only current robots and
one global browse page (both 200), confirming one page with six distinct projects
and a native page size of 20. These are observations, never hardcoded limits.
The complete current path is approximately nine base requests: robots, both
browse frontiers and all six details. Future counts and pages are read from the
publisher; stronger retained timing and complete remaining traversal must fit
the phase budget before proceeding.

The nonrenewable whole runtime is 45 minutes, collection 25 minutes, each publication
phase 180 seconds and final trusted-state export 30 seconds. Normal publisher calls
retain the existing 90 second timeout within the remaining phase; GitHub reads
are bounded separately. The unchanged 180 minute job is an outer infrastructure
cap, not a token lifetime. These internal deadlines keep the current measured
frontier comfortably within the pre-minted one-hour App token; no late-token
staging, scope reduction or job-cap increase is introduced. Slow/repeated-failure
or enlarged frontiers fail safely when full work cannot fit. Daily 05:40 UTC is
unchanged. Local and hosted publisher work must be serialized across machines.

Every 429 stops the current collection after persisting at least 30 minutes of
cooldown, exponentially increasing for new repeated observations, with any
longer valid Retry-After or known embargo retained. Both the current physical
interval and current inventory gap double, including already stricter values.
Malformed, negative, nonfinite or stale timing cannot poison state; fallback
still applies. An active retained 429 embargo fast-fails without new traffic;
after expiry a bounded retry retains the slower gaps. A 429 alone is timed,
not a permanent content refusal. Supported 503/transport retry policy remains
bounded by the same nonrenewable phase. Every physical inventory retry or
redirect also waits the retained page gap after the preceding response/error
completes; detail requests retain their separate physical budget. Bounded body
inspection precedes content redirects and the 429 error: a clear human-verification
429 retains both timer and hard hold, while ordinary rate text remains timed.
Body-read failure cannot erase an already retained 429 timer. Robots purpose is
limited to literal `/robots.txt` at every hop, including redirects. Actual content 401/403/451, clear primary
human-verification/refusal and valid robots Disallow retain a hard hold.
Robots-only 4xx except 429 is unavailable REP rather than a content refusal;
robots 5xx/network/TLS failure does not become unrestricted permission. Normal
TLS, final advertised User-Agent, proxy and authentication headers are preserved.

The original local journal had only starts, interval and until. Its known eight
starts remain preserved through the approved two-request research extension,
which retained inode 1097 and added both actual new starts. The richer state
records completions, inventory timing, actual hard holds and newly observed 429
strikes; `legacy_429_history: unknown` explicitly distinguishes unlogged past
state from the new counter. Missing old fields do not establish that past
Retry-After, strikes or refusals were absent. No old headers are claimed restored.

Five original hosted attempts have no artifacts. Their complete exact run/head,
job and log history is reviewed. Actual exact-job completion makes
[run 38720083972](https://github.com/YouthOpps/data-pipeline/actions/runs/38720083972)
the latest successful legacy attempt, after the higher-ID
[run 38721259339](https://github.com/YouthOpps/data-pipeline/actions/runs/38721259339)
failed earlier during publication. Run IDs and mutable update times do not
establish chronology. The first hosted transition requires all five exact
legacy identities and job inventories, zero original artifacts, and a separate
maintainer-approved full actual native/local state marker. The [maintainer-approved full-state marker](https://github.com/YouthOpps/data-pipeline/issues/201#issuecomment-6195962246)
carries canonical state SHA-256
`d283961f45b0736dfcb6277acf41ddfd292a85548d61bd47c12279c1b22da224`.
Its 24 conservative old/research/reservation/wire events represent nine native
physical requests plus known prior history, not 24 native requests. The original
native journal SHA-256 is
`9410451e53ea7c957d482cb5a8efd1f2c263b671ff353ef31157d233d17c18f3`;
the carried absolute completion/page guard is 1792078256.588203. Runtime adds
fresh startup quiet and preserves all stronger constraints. This state-only
approval does not enable or dispatch production.
Unknown/changed history or missing marker is not a fresh bootstrap allowance.

After the migration, the newest completed exact attempt must have its own
trusted owned artifact; missing/corrupt/expired latest state never falls back to
an older attempt or the legacy marker. Queued/current older-ID reruns retain
exact prior-attempt source/head/workflow binding and actual job chronology.
Restore occurs under the original lifecycle lock before source-snapshot reads
or publisher access. Full numeric restrictions merge by MAX, starts by union
and actual refusals by OR, with at least 60 seconds of startup quiet. Trusted
state exports atomically in finally, including snapshot/auth/collection failure,
while preserving failure exit status. Artifacts contain no records or secrets.

The sole complete nonpublishing native test on 15 October 2026 passed at
source revision SHA-256 `05428dde79bad163f5dff22708b5ac5f6c58fb133a55cf7ea7ac936eb0404496`:
six actual raw records, five retained after expiry, and a controlled valid empty
envelope all passed the actual current website consumer in RAM. Nine physical
requests had a measured minimum gap of about 12.671 seconds, at most five starts per rolling minute,
and a 94.731-second first-to-closing inventory completion guard. All actual
source bodies and complete stdout were retained in private RAM, without datasets
or publication credentials in files. The original journal inode 1097 survived.
A subsequent artifact-only transport parameter correction, import ordering and
prose spacing do not change the default publisher path; complete actual-response
replay and independent owned-ZIP transport controls qualify that evidence reuse.
Independent final code acceptance remains required; maintainer state approval
and local success do not certify hosted publication.


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
