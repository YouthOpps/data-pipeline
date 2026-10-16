# Fulbright Hungary

Source: `hu-fulbright-hungary`, published by the Hungarian-American Fulbright Commission in Hungary. This standalone adapter uses Python 3.12 or later and the standard library only.

## Official access and attribution

Collection reads the public [award homepage](https://fulbright.hu/), [short-term grants](https://fulbright.hu/special-short-term-grants/) and [U.S. awards](https://fulbright.hu/fulbright-awards/for-us-citizens/), plus linked current Hungarian and joint university award pages. The [robots policy](https://fulbright.hu/robots.txt) reviewed on 8 October 2026 permits access with an empty `Disallow` rule. The official navigation and footer expose no separate terms or reuse policy; ordinary copyright is present. This supports the reviewed public collection route, not a claim of unrestricted redistribution rights. Records preserve publisher attribution, original links and classification evidence. Application forms and external award websites are linked, never crawled or submitted.

The adapter sends `YouthOppsBot/1.0 (+https://youthopps.org/bot/)` on every publisher request. It retains at least 12.2 seconds from physical response completion to the next physical attempt and at most five starts per rolling minute. Each physical inventory request additionally waits at least 30 seconds after the preceding inventory attempt completes, including redirects and failed attempts. The three root catalogue pages are inventory; linked individual award and joint-award pages are details. Literal robots reads use the normal physical guard, including when interposed between inventory redirects. Stronger retained publisher constraints always win. GitHub publication keeps its separate existing six-second pacing and valid Retry-After handling.

An actual publisher 429 ends collection immediately without a same-run retry. Before reading its body, the journal doubles both CURRENT retained physical and inventory gaps and retains at least 1,800 seconds of exponentially increasing cooldown, any longer valid Retry-After, and all older stronger constraints. Missing, malformed, stale, nonfinite or unsupported Retry-After alone creates no permanent refusal. Content 401/403/451, clear visible human-verification challenges and applicable robots Disallow remain durable stops. Literal robots 4xx other than 429 are unavailable REP; a stricter policy already observed in the run is retained. TLS failures, unread or partial responses, unassessed policy errors and failed state writes stop collection with an unsettled transaction.

Do not overlap local and hosted collection. LOCAL preparation requires the real workflow disabled and no active family jobs, restores actual history before creating local state, waits at least 60 seconds, and rechecks the disabled definition immediately before the first physical request. Production uses the existing source-family concurrency and immutable current-run/job/head checks.

## Opportunity inventory and exclusions

The previously published inventory contains ten genuine records; this is historical evidence, not a required count or a new migration test:

- Three active Hungarian calls: lecturer, researcher and Foreign Language Teaching Assistant (FLTA).
- Two joint lecturer awards at the University of Texas at Austin and Indiana University, explicitly matching the current parent call's academic cycle and application route.
- Four rolling grants: conference travel for Hungarian alumni, Specialist hosting, Intercountry Lecturing hosting and Outreach Lecturing Fund travel. Hosting grants are classified as institutional grants.
- One dated U.S. Student Program call for postgraduate study in Hungary.

The collector identifies homepage award cards by their titles and deadline
fields, without depending on the layout's section ID. It reads current detail
deadlines and explicit citizenship requirements. Short-term sections can contain
both rolling and newly dated calls; supported future dates are collected using
their live title, description and official information link. Expired calls,
future announcements, recipient lists and discontinued programmes are excluded.
Unrecognized date formats fail explicitly. The undated Pittsburgh description
has no verified current academic cycle. MTA support supplements an existing
award rather than providing a separate application. Other undated U.S.
navigation entries are descriptions without current calls. External pages,
PDFs and application forms are not parsed, so document-only calls and conditions
are outside this HTML collection.

## Identity, dates and eligibility

Identifiers derive from source ID and original URL; dated calls also include the published academic cycle, so recurring awards retain distinct identities across cycles. Every record contains the source ID, source URL and evidence links; metadata supplies the human-readable publisher attribution. Empty collections, duplicate IDs, invalid URLs, dates, categories or countries fail validation.

Hungarian citizenship is recorded only when the official current award detail explicitly requires it. Joint lecturer eligibility and deadline come from their verified shared parent application route. Institutional grants do not imply individual nationality eligibility. Host countries distinguish U.S. study/travel from institutional hosting in Hungary.

Hungarian and English deadlines without a verified UTC offset retain their calendar date. A published clock such as the actual FLTA `23:59` is preserved in a short factual summary note when its time zone is not stated; no Budapest zone or end-of-minute seconds are invented. Explicit numeric-offset or UTC timestamps retain their exact instant and stated minute/second precision. Date-only calls remain eligible through their UTC closing date, with unknown status on that day, and expire on the following UTC day. Homepage/detail selection, rolling-call selection and final cleanup use this same rule. Joint awards inherit the verified parent deadline and its closing-day status, including the parent's published-clock note where applicable. Rolling applications have no invented deadline. The U.S. Student Program's approximate April-to-mid-October window remains in its summary; its exact deadline stays null, boundary-month status stays unknown, and month selection uses UTC consistently. Publication and modification times are not inferred from unrelated dates.

## Run and test

From the repository root:

```sh
python -B adapters/hu-fulbright-hungary/adapter.py
python -B adapters/hu-fulbright-hungary/test_adapter.py
```

The sole live test performs real non-publishing collection, validates every record and emits the complete JSON result on stdout with diagnostics on stderr. It receives no publication token and writes no output files. The folder can run independently when copied outside the repository.

On 8 October 2026, author and independent live tests validated all ten records in memory, including unique identities and URLs, publisher evidence, countries and dates. Later collection may legitimately change as calls open or expire.

## Publication

The matching `fetch-hu-fulbright-hungary.yml` workflow runs automatically on pushes to `main` affecting this adapter folder or its own workflow, including maintainer merges. Manual dispatch and a daily schedule (17:12 UTC) remain available. Repository/main guards, source-family publication concurrency and the existing GitHub App's data-source-only contents-write token scope remain enforced.

The production command is exclusively:

```sh
python -B adapters/hu-fulbright-hungary/adapter.py --publish
```

Only `YouthOpps/data-source/datas/hu-fulbright-hungary/{data,metadata}.json` are published, atomically and with conflict-safe reference updates. Successful publication after validated nonempty retrieval advances the durable success timestamp. Later failures preserve last-good data and its success timestamp while recording sanitized failure metadata when possible; a failure before first success creates no source folder. Tests never publish. Production publication was reviewed statically and was not invoked during implementation or QA.

## Homepage parse diagnostics

A homepage with no identifiable award cards fails closed. Repeated responsive
cards are deduplicated by their source URL. The sanitized error records the
final HTTPS page URL without query or fragment, HTTP
status, and raw response byte length and SHA-256. These values fingerprint the
already received response; no HTML, response headers or additional requests are
retained or introduced. They cannot reconstruct a missing historical response
or establish that an unexpected document was a bot refusal. A section ID or
benign layout change alone no longer causes a failure.

The 11 October 2026 parse failure in [run 38142601920](https://github.com/YouthOpps/data-pipeline/actions/runs/38142601920)
had no retained response artifact. Earlier and later runs on the same revision
succeeded, so its specific cause remains unknown. A genuine fetch, parse or
validation failure preserves last-good records and the prior success timestamp.

## Expired-record cleanup

A refresh first retrieves and validates the complete nonempty publisher inventory.
Only then are records with a verified past `deadline` removed from the next
source snapshot, including expired identities from the previous publication.
The existing full replacement does not merge old records back into the result.
Date-only deadlines remain eligible through their UTC closing date.
Explicit timezone-aware timestamps expire strictly after the stated instant;
missing deadlines remain eligible; unresolved publisher dates stay null.
Invalid normalized deadlines fail validation and preserve last-good data. Status, record age and
absence from a response are not expiry evidence. Publisher inventory coverage
continues to be checked before filtering.

The refreshed subset and success metadata are published atomically within this
source. If every validated record has expired, success publishes an empty array;
an empty raw retrieval still fails validation. Previously published empty arrays
are valid snapshots. Retrieval, parsing, validation and publication failures
preserve last-good data and its success timestamp; safe failure metadata may
be updated. The sole live test remains non-publishing and verifies the complete
raw inventory. Cleanup and failure boundaries are checked separately without
production execution.

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
publisher restrictions remain effective. The durable migration below does not clear retained refusals. Existing disabled workflows remain disabled until a maintainer separately authorizes recovery.

## Daily automation

The scheduled refresh runs once daily at **17:12 UTC** (`12 17 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The collector has three root inventory pages and follows their original dynamic award and joint-award traversal. The inventory guard also applies to each physical redirect or retry of those roots. Individual detail pages retain ordinary physical pacing.

## Workflow concurrency

`youthopps-hu-fulbright-hungary-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
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

Published metadata uses `display_name` = **Fulbright Hungary** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Durable pacing migration (issue 278)

The original live test is unchanged. The first separately approved nonpublishing native test passed once on source `da3842b4`, yielding six raw records from eight complete HTTP 200 responses. Its external controller then failed after the test because a derived expiry check passed an ISO string where the existing helper expects an aware datetime. That original FAIL receipt remains preserved. An offline supplement from the same retained full responses/stdout used the correct fixture type and passed current-consumer raw 6 / filtered 6 / valid empty 0, all eight request/owner settlements and all 56 saved-state witnesses. No source collection was repeated to repair that fixture.

That old-code native does not certify the corrected date-domain frontier. It ran on UTC 15 October after local Budapest midnight and incorrectly skipped the lecturer/researcher closing-day cards before fetching their details. The actual retained homepage proves those two additional current detail routes must now be traversed. Their first-run detail bodies, and any joint pages linked from them, were not fetched and are not represented as actual evidence. Offline boundary/parser controls use explicitly synthetic missing-detail fixtures. A separately approved complete original native on the corrected code remains required; no production recovery or readiness is claimed.

The legacy implementation used host-keyed numeric journals under `/tmp/hu-fulbright-hungary-pacing-<uid>` and no owned Actions artifact. Read-only recovery of the two exact code-derived `fulbright.hu`/`www.fulbright.hu` entries found that directory absent. Absence does not prove zero restrictions or zero 429 history. The proposed one-off transition pins all 32 exact historical attempts: 31 executed jobs contribute conservative actual-job completion upper bounds, while one cancelled attempt had no jobs and contributes no invented timestamp. All 32 artifact inventories were empty. Lost old starts, response headers and rate counters remain explicitly unknown; a new counter zero means no newly observed modern 429 only.

No generic bootstrap or old-schema default exists. The maintainer approved the initial typed unknown-history LOCAL transition, then publicly approved the exact first-native full-state bridge at https://github.com/YouthOpps/data-pipeline/issues/278#issuecomment-6203785465. `_LEGACY_TRANSITION` contains only that six-key, exact-history bridge. Production needs its separate full-state approval; the LOCAL bridge does not authorize production. Any newly appearing old publisher entry requires fresh provenance review, rather than being ignored or reset. Once modern runs exist, the newest actual completed attempt is mandatory: exact jobs and head/branch identity establish chronology, including completed prior attempts of queued reruns. Missing, expired, malformed, unowned or digest-mismatched newest artifacts fail closed without older fallback. Local state is merged with the exact owned state using stronger numeric constraints, event union and refusal/unknown OR. An unsettled local or owned transaction cannot be cleared by an older settled artifact.

The modern family is `youthopps-hu-fulbright-hungary-publisher-v1`, with owner-only local directory `/tmp/youthopps-hu-fulbright-hungary-publisher-v1-<uid>`, stable `state.lock`/`collection.lock` and atomic `state.json`. Schema 3 records a durable pending policy owner before any physical request, then distinct physical request identities. The owner spans original healthy no-response retries, redirects, new/cached REP assessment and all response constraints. Physical completion alone cannot settle that owner. Settlement requires the same healthy owner after every applicable assessment and write succeeds. Cached policy-only transactions are distinguishable from physical requests.

Any failed reservation, restriction, calculation or completion save latches a process failure; later request, lifecycle reentry and valid artifact export are refused. Safe invalidation addresses only the CURRENT regular, singly linked, owner-only journal inside the verified original directory; a substituted parent or unsafe object is preserved. Even if invalidation also fails, the pre-wire fsynced pending record makes a fresh process reject the old path and older artifact. Artifact failure handling truncates a safely owned descriptor before best-effort readiness signaling/unlinking; unsafe files, symlinks and hardlinks are not altered. Export never substitutes a fabricated fresh state.

The workflow adds read-only Actions access and attempt-specific owned state upload only after `pacing_ready=true`; its one application command, daily 17:12 UTC schedule, publication token scope and 30-minute job cap remain unchanged. Internal bounds are 900 seconds collection, 1,290 seconds whole adapter execution and 30 seconds export, with restoration before source snapshot reads and export in `finally`. GitHub setup/token time still consumes the original job budget. Exhaustion fails closed and preserves last-good data, without increasing a cap or truncating coverage.

Nominal physical requests are `2 * (3 + A + J)`, where `A` is the number of current homepage award detail fetches and `J` includes every original joint-detail fetch, including repeated or later rejected links. Each page retains its fresh REP read. The original two ordinary connection attempts and four-hop traversal remain bounded locally, but there is no new hard cap on dynamic award membership. The historical 134- and 218-second collect/publish steps are not new-paced wire measurements or a fit guarantee. A genuine complete native run is required before any readiness claim; stronger future backoff may prevent completion under the unchanged caps.

The date-domain correction is based on actual retained publisher evidence: the FLTA detail states `Pályázat beadási határidő: 2026. október 31. 23:59` without a verified zone, while the old helper emitted a precise Budapest-derived UTC timestamp. The corrected representation is calendar-only with the factual clock/zone-unknown note above. It also changes UTC closing-day membership, so the first native's six records are old-code evidence only. All transport, durable-state, historical identity, publication and original-test code remains unchanged by this date correction; the six affected date-domain functions and three small date helpers are independently reviewed separately.


## Complete native evidence and owned export guard

The corrected date selection completed the unchanged original native test with
10 raw and 10 filtered records from 18 actual HTTP responses. The full current
consumer accepted 10 raw, 10 filtered and zero derived valid-empty records. All
actual response headers, bodies, stdout and 126 journal saves remain in private
RAM for independent review. This complete run includes both eligible October 15
programme details, every advertised joint detail and the reviewed Pittsburgh
exclusion. The earlier six-record original test also passed; its external
controller failed afterward on a datetime fixture. That original failure receipt
and its separate offline supplement remain preserved.

After the complete corrected native run, a private I/O reproduction found that
replacing the upload directory could leave readiness true for an unrelated
current-path file. Only the owned export helpers changed afterward: they bind
the checked directory inode before pinning, verify the current output inode
before and after readiness and on healthy completion, and invalidate the pinned
safe output before fallible failure signaling or unlink. Untrusted early exits
also remove safely owned stale output. Unsafe files, symlinks, hardlinks, FIFOs
and unrelated replacement directories remain untouched. Artifact and workflow
signal opens are nonblocking. Actual response and record replay qualifies the
unchanged collection, date interpretation and publication path; no second crawl
was used to qualify this conditional export correction.

The combined production-state proposal retains 161 conservative timing
observations and 372 constraint witnesses across both native attempts and the
legacy evidence. These are observations, not 161 publisher requests: the two
actual tests made eight and eighteen physical requests. Unknown historical rate
state remains explicit. First production still requires maintainer approval of
the exact full-state maintenance envelope and independent final configuration
review; this evidence does not authorize an extra publisher run.

The job's private `RUNNER_TEMP` and execution context are trusted against an
independent process running as the same user and forging replacement envelopes.
A retained negative compound test replaces the directory after a successful
`pacing_ready=true`, supplies an unrelated valid weaker envelope, and makes every
subsequent false-signal write fail. Export correctly raises, the pinned original
is zero bytes and the unrelated replacement remains untouched, but the last
signal stays true. This compound case is not reported as a passing upload-safety
guarantee. An independent same-user writer can replace data after any final
in-process check; the guard does not authenticate that writer. No workflow gate
was weakened to hide this limit or discard legitimate 429 failure-state
artifacts. Ordinary parent/inode changes fail closed, with pinned safe-output
invalidation preceding independent best-effort false signaling and cleanup.


## First production maintenance approval

The maintainer separately approved the complete combined state at
https://github.com/YouthOpps/data-pipeline/issues/278#issuecomment-6204168955.
The workflow carries that exact four-key maintenance envelope, preserving the
full state with canonical SHA-256 `133cd4f0e855106a61d3a7dec8ebcc7f137d554bdbea943b239cc86aa409feac`.
The runtime still restores exact family history, merges stronger local/owned
constraints and applies fresh startup quiet. This approval does not erase the
explicit export trust-boundary negative above. The maintainer owns enable-before-
merge and the single automatic production run; production and site delivery are
unverified until their actual receipts are checked.

### Journal generation maintenance (#292)

Publisher state reads, writes and failure cleanup use the same retained RW
journal descriptor under the existing stable lock. The current verified parent,
name, inode and generation are checked before and after IO, before the publisher
opener, and while exporting state. A replaced journal is refused; cleanup may
invalidate only the original owned descriptor and preserves a replacement's
bytes. Validation, serialization and persistence failures remain sticky, so no
later opener, re-entry or valid owned artifact can reuse the failed allowance.

The maintainer explicitly approved replacing this journal's atomic rename with
an in-place, locked commit: serialize first, truncate and fsync the original FD,
handle short writes, then fsync and verify the complete bytes. A crash or IO
fault can leave empty or partial state and reduce availability. A fresh process
must refuse that mature state, even with an older artifact; it cannot bootstrap
or recover a weaker allowance. Actual source-data publication remains atomic.
The checks do not claim to authenticate arbitrary same-UID writers that ignore
locks and rewrite the same inode.

This conditional maintenance preserves the state/artifact schemas, physical and
page pacing, historical MAX/OR constraints, source frontier, dates, record
facts, publication and workflow. Private actual-function fault controls and replay of
all retained 18 responses and 10 native records qualify the change without a
new publisher crawl. The retained native has 10 records; the later live dataset
has six after the UTC closing-day filter. These are distinct outcomes.

Offline implementation approval: [issue #292, maintainer decision](https://github.com/YouthOpps/data-pipeline/issues/292#issuecomment-6205456320).

Journal and collection-lock release failures also latch failure. Otherwise
healthy contexts raise so the existing owned-artifact cleanup invalidates output even
after readiness was signaled. An already-active body error is preserved; a
recycled unrelated descriptor is neither closed nor modified. Collection lock
identity is captured by the actual acquisition path, not inferred at cleanup.


## Header-only HTTP 429 stop — issue #328

A received publisher HTTP 429 now stops after the existing header constraints
are retained, before reading any response body. The response must close
successfully before this intentional stop is assessed. A close or persistence
failure follows the existing failure handling; they cannot establish a healthy
collection. Previously retained holds, numeric MAX constraints, Retry-After
and unknown history remain. An unread 429 body provides no new challenge
classification.

This paragraph supersedes earlier 429 body-inspection descriptions. This
conditional repair preserves non-429 responses, REP selection, the full
collection/parser/IDs/dates/expiry, GitHub error-body handling, publication,
caps, workflow, state schema and finite migration markers. It does not add
missing pending ownership or upgrade a legacy source to a modern continuity
protocol.

Private ordinary transport/state controls and protected normal-AST comparisons
qualify this change. Earlier genuine native evidence retains its original code
and date attribution; no new publisher collection, current factual review,
complete native replay or production run is claimed. The maintainer alone owns
workflow/state coordination and the maintenance rollout for issue #328.
