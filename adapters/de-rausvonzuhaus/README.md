# Rausvonzuhaus vacancies

This adapter reads the actual public [Last Minute Markt](https://www.rausvonzuhaus.de/lastminute) vacancy cards published by Eurodesk Deutschland / IJAB. The FAQ and other directory pages are not records. Every result links to its original normal `/lastminute/detail/<numeric-id>` page. The numeric publisher ID supplies the stable source identity; title, dates or position in the list do not determine that identity.

## Access and reuse

Reviewed on 8 October 2026: the public [robots policy](https://www.rausvonzuhaus.de/robots.txt) permits the normal listing and detail routes, but disallows `/typo3/` and paths containing `hide_page_frame`. Collection uses the normal listing only, without embedding queries, login, applicant data or external application requests. Requests use the declared YouthOppsBot identity and an ordinary HTML Accept header. Transient HTTP 500/502/503/504 or non-TLS connection failures receive at most one paced retry; HTTP 429 always stops the current attempt. Retry-After also applies to allowed retries. Robots is checked on each run and on content redirects. A complete HTML document and nonempty, valid card inventory are required.

The publisher's [imprint](https://www.rausvonzuhaus.de/impressum) states:

> Soweit im Einzelfall nicht anders geregelt und soweit nicht fremde Rechte betroffen sind, ist die Verbreitung der Inhalte von www.rausvonzuhaus.de über die embedding Funktionen oder in Teilen davon in elektronischer und gedruckter Form für die Arbeitsfelder Internationale Jugendarbeit und Jugendinformation unter der Voraussetzung erwünscht, dass die Quelle (www.rausvonzuhaus.de) genannt wird.

It prohibits commercial distribution without prior written IJAB permission. YouthOpps' intended noncommercial youth information use matches the stated purpose. Records provide attributed partial factual metadata and our own concise summary; they do not reproduce photographs, image credits or full third-party descriptions. Every record and source metadata attribute `rausvonzuhaus.de / Eurodesk Deutschland (IJAB)`. The official contact for questions is `rausvonzuhaus@eurodesk.eu`, with IJAB contact `info@ijab.de` on the imprint.

One literal robots request and one complete listing normally provide the
inventory. This issue #342 candidate uses at least 12.2 seconds between
physical starts and after response completion, at most five starts per rolling
minute, and at least 30 seconds from inventory completion to the next inventory
request. Retries and redirects carry their original purpose. Applicable
stricter robots timing and Retry-After remain effective. Normal operation has
one listing and no detail enrichment; these floors add no pagination or extra
publisher requests.

The original `de-rausvonzuhaus-pacing-<uid>/<sha256(host)>` journal remains retained
predecessor evidence for the finite LOCAL transition. The new source-local schema-3 namespace uses
`youthopps-de-rausvonzuhaus-publisher-v1-<uid>`. Neither a missing mature state
nor an older artifact supplies a fresh allowance. The exact LOCAL declaration
and all 30 historical attempt pins are now bound to the separately reviewed
[ROOT prospective LOCAL approval](https://github.com/YouthOpps/data-pipeline/issues/342#issuecomment-6222714638).
HOSTED now binds the separately approved full MAX and exact workflow maintenance
value described below. The sole native collection, original owned restore and
complete retained MAX have received independent review. Current mature LOCAL
remains PRESENT; no reset or reentry is granted. Future hosted initialization
and production remain separate subsequent operations.

## Inventory and fields

All cards are present in the public HTML; no pagination or external detail enrichment is necessary for the supported fields. Parse the `lmm-item` card boundaries and actual title, programme badge and factual paragraphs. On 8 October 2026, the inspected listing contained 477 distinct numeric IDs; 471 had internally consistent dates: 462 volunteering, seven youth/school exchanges classified as training, and two student internships. Counts are discovered on every run, not hardcoded acceptance targets.

Programme badges include IJFD, weltwärts, workcamps, French-German and other volunteering, European Solidarity Corps, youth encounters, student internships and school exchanges. The adapter retains the publisher's German programme label in tags and classification evidence. Age bounds, publisher host label, project dates and application deadline come directly from each card. Two-letter host codes are a mapping of the explicit country label, including the commonly used `XK` for Kosovo. A host country does not establish citizenship eligibility: `eligible_countries` remains empty. Costs and benefits are unspecified in the list and are not guessed; a reviewed detail example requires a €3,200 contribution, so inclusion does not imply free participation.

The publisher sometimes has a title/location disagreement, for example a Trento title beside a Germany host label. Preserve and attribute the structured label; do not infer a replacement host country from a place name in a title. Each record's classification evidence preserves the actual publisher host label for review. Titles are normalized for whitespace; image copyright credits are excluded.

## Dates and exclusions

Explicit `DD.MM.YYYY` dates become ISO calendar dates. `deadline` is `YYYY-MM-DD` with `deadline_precision: date`, and `start_date`/`end_date` preserve the project interval. The publisher gives no deadline clock or time zone. No closing instant is invented. The website interprets date-only deadlines through the end of the UTC date as a consumer convention, not a publisher statement. Future deadline days on a published vacancy are `open`, past deadline days are `expired`, and the same UTC calendar day is conservatively `unknown` because the closing clock is unspecified. Valid expired opportunities remain in the raw collection before the existing
verified-deadline filter; an ended project alone is not an exclusion.

Exclude internally contradictory ranges or an application deadline after the stated project end, and report the number to stderr. Do not repair a year by inference. Six inspected cards require this exclusion:

| Publisher ID | Project start | Project end | Application deadline |
|---|---|---|---|
| 2527 | 2025-09-01 | 2026-08-31 | 2026-10-10 |
| 7418 | 2026-09-01 | 2026-08-31 | 2026-10-15 |
| 2521 | 2025-09-01 | 2026-08-31 | 2026-10-26 |
| 3226 | 2025-08-15 | 2026-08-15 | 2027-02-12 |
| 6995 | 2026-09-01 | 2027-03-01 | 2027-05-31 |
| 7660 | 2027-03-01 | 2027-09-01 | 2027-10-31 |

Every deadline exceeds its stated project end; 7418 additionally reverses start and end. These are excluded for contradiction, rather than merely for being old. If a source corrects the dates, the card is included automatically. New unsupported card structures, age/date formats, programme labels or country labels fail collection rather than silently discarding part of the inventory. Duplicate IDs or URLs and incomplete HTML also fail validation.

## Run and publication

From the data-pipeline repository:

```sh
python -B adapters/de-rausvonzuhaus/adapter.py
python -B adapters/de-rausvonzuhaus/test_adapter.py
```

The single live test collects actual nonempty records, validates them, and emits the complete JSON document on stdout. Diagnostics go to stderr. It has no publication credentials and writes no output files. The folder operates independently using Python's standard library; its test imports only its own adapter in addition to standard modules.

The matching `fetch-de-rausvonzuhaus.yml` runs after changes to its own adapter/workflow are merged to `main`, supports manual dispatch and runs once daily at 14:33 UTC. A repository/main guard, minimal permissions, data-source-only GitHub App token and source-family publication concurrency constrain the Action. Its sole application command uses `adapter.py --publish`; tests and formatting never run in production.

Production starts after maintainer merge. It atomically publishes only `datas/de-rausvonzuhaus/data.json` and `metadata.json` using a conflict-safe Git commit. First-run failure creates no source folder. After a successful publication, later failure preserves last-good data and the last successful timestamp and records sanitized failure metadata when possible. The success timestamp advances only after a validated nonempty retrieval and its filtered result are successfully published. Failed metadata publication is reported rather than claimed to have been saved. This adapter does not alter any other source or create a catalog.

## Expired-record cleanup

A refresh first retrieves and validates the complete nonempty publisher inventory.
Only then are records with a verified past `deadline` removed from the next
source snapshot, including expired identities from the previous publication.
The existing full replacement does not merge old records back into the result.
Date-only deadlines remain eligible through their closing date. With no
documented publisher timezone, cleanup uses the conservative UTC-12 calendar: the
closing date must have ended globally before removal.
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
access; restrictive rate and delay directives remain effective. The header
remains unchanged; issue #342 modernizes durable state and pacing as described
below. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **14:33 UTC** (`33 14 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The collector reads every card from the single complete listing. The new
30-second inventory-completion guard also applies if a retry or redirect needs
another inventory request. Literal robots requests use the physical gap. No
detail traversal, first-known stop or card count cap is introduced.

## Workflow concurrency

`youthopps-de-rausvonzuhaus-publisher-v1` serializes runs of this source. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Historical schema-1 transition evidence (before issue #342)

The following section records the earlier schema-1 implementation and its
15 October observations. It does not qualify this new schema-3 candidate or
authorize another source run.

A publisher 429 ends this attempt before reading its body or retrying. Before
stopping it increments the new-since-transition rate-failure counter, doubles
the currently retained physical interval and retains at least 30 minutes of
backoff, doubled after repeated failures. A finite positive representable
numeric or timezone-aware HTTP-date Retry-After can extend that embargo.
Malformed, negative, zero, overflow or unsupported timing does not create or
clear an access hold; the exponential fallback remains. Existing stronger
embargoes, completion history, intervals and genuine refusal holds merge by
MAX/OR. A later attempt fails before publisher access until the embargo ends.

Actual content 401/403, a clear primary visible denial/challenge, or a genuine
robots Disallow remains a durable hold. Ordinary CAPTCHA scripts, navigation,
footer or hidden decorations are not a visible primary refusal. Verified
robots-only 400–499 except 429 is unavailable REP rules, rather than evidence
of forbidden content. Robots 429, server failures, TLS failures and network
failures do not become unrestricted access. Normal TLS verification and the
bot identity remain enabled; no browser impersonation or workaround is used.

The reviewed pre-artifact history has 28 first attempts across 18 heads:
23 successful publications, four cancellations with no jobs, and one later
publication failure after a genuine 461-card / 455-retained collection. The
latest possible publisher-access upper bound is the last job completion,
2026-10-15T10:44:50Z. All logs and exact job/attempt/head identities were read.
They do not expose old physical starts, response Retry-After headers or
successful internal rate retries. These values are unknown, never recovered
or asserted zero. `legacy_rate_history_unknown` remains explicit; the new
counter measures only failures observed since the approved transition.

The first transition requires the maintainer's exact public approval and
not-before/state receipt, pinned to all 28 old attempts and their actual jobs.
It preserves any discovered local budget without changing its path, conserves
known job-completion upper bounds and adds at least 60 seconds of startup
quiet. Bootstrap approval placeholders fail before publisher access. Any new
executed attempt makes this one-time legacy transition ineligible. Thereafter
the newest completed attempt's owned artifact is mandatory; missing, expired,
corrupt or ambiguous state fails before access, rather than using an older
artifact or fresh allowance. Older-ID queued reruns retain their exact prior
completed attempts, ordered by immutable attempt-job completion, with head,
repository, branch and source checks. The earlier implementation treated
jobless cancellations as zero-request. Issue #342 retains their exact cancelled
pins while historical request, policy and completion ancestry remains unknown.

Restore precedes even publication snapshot/auth work; finally exports trusted
state while the collection lock remains held if that work fails. Source data
and metadata still publish atomically through the existing source-only CAS.
The existing 30-minute job and one-hour precreated publication token remain.
A 1,290-second absolute lifecycle bounds prepublication to 900 seconds,
publication/failure reporting to 180 seconds and final state export to 30
seconds, with socket timeouts clipped to their remaining phase. Limits never
renew the absolute deadline. The genuine normal source frontier remains two
GETs (robots and one complete listing), without per-detail or PDF requests.
Maintainer review, an approved sole native full collection/current consumer
gate and separate production observation are required before delivery claims.

Final IDs retain their existing deterministic native-ID digest mapping. The
validator checks the website's alphanumeric/hyphen grammar and rejects IDs
that collide case-insensitively; no valid source identity is renamed.

On 15 October 2026, the sole complete native test inspected all 464 advertised
cards and retained 456 valid native identities; eight cards had internally
inconsistent project/application dates under the unchanged parser rule. The
two genuine publisher requests both returned HTTP 200 with the declared bot
identity. The run took 82.15 seconds including startup quiet; actual starts
were 13.13 seconds apart, with at most two in a rolling minute. The complete
456 raw, 456 expiry-filtered and derived-empty envelopes passed the current
website consumer in RAM. These observed counts are evidence, not collection
caps. This native success does not assert hosted publication; production
requires separate approval of the complete retained native pacing MAX state.

## Source display name

Published metadata uses `display_name` = **Eurodesk Deutschland** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


## Issue #342 durable pacing implementation

This implementation preserves the complete normal VacancyCards reader,
card IDs, countries, age ranges, programme categories, calendar precision,
UTC-day status and conservative UTC-12 expiry filter. A complete nonempty raw
inventory remains mandatory; expiry may then legitimately publish `[]`.
Historical 456-card results above are neither a current count nor a capacity
bound. The sole 16 October native collection retained 455 records; the separate
independent review verified every source fact and all durable/physical evidence.

Every logical publisher policy owner has a durable nonce and pending barrier
before transport or policy assessment. Each physical reservation has a
separate request nonce. Only its matching assessed response or supported
pre-response network retry can complete that request, and only the enclosing
owner can settle the logical barrier. A response is complete only after its
context closes successfully; a close failure retains the pending request and
prevents a retry, even after a complete HTTP 200 body. Partial bodies, storage
failures, unassessed policy or numeric failures stay sticky; there is no later same-run
opener. HEAD/primary denial, selected REP Disallow and publisher content refusal
retain a hold. Generic hidden/footer decorations do not prove refusal.

An observed 429 doubles BOTH the current retained physical and inventory gaps,
keeps any stronger constraint, and durably retains at least 1,800 seconds times
the observed exponential strike factor or a longer valid Retry-After. It stops
before any body read and ends the current run. Invalid Retry-After alone does
not create a refusal or erase backoff. Counters describe newly observed events;
old rate ancestry remains explicitly unknown. Other unavailable historical
policy, headers and pending ancestry must be qualified in external evidence,
not asserted zero or settled from absent old schema fields.

Applicable current bot-specific groups, or their wildcard fallback, are
selected independently of retained legacy-specific groups. Numeric timing is
the MAX of equally applicable selected directives, including finite decimals.
Malformed selected timing fails closed; unrelated malformed groups cannot
suppress access or substitute for policy assessment. Cached REP is reassessed
for each target. The complete document and original parser checks remain.

The first transition binds every authentic exact attempt/job and the newest
owned seven-key archive, immutable producer code and complete known numeric/OR
constraints. The original seven-field owned state is not a modern
schema-3 envelope. Missing or corrupt newest state never falls back to an
older artifact. Modern restored state retains stronger local/owned holds,
nonces, completion times, gaps, embargoes and unknown history.

The implemented LOCAL first-transition route requires a PRESENT original
journal and exact ROOT-approved raw bytes, directory and lock identities.
It holds the original collection authority, verifies bounded complete reads,
and consumes an exclusive, fsynced one-off claim outside the new family leaf
before creating it. The claim survives deletion of that leaf and cannot be
reused by a cold process. This is prospective initialization, not proof of
historical settlement. If the actual original LOCAL directory is absent, this
route refuses; it needs a separate reviewed design and authority. The claim
assumes its trusted ancestor and claim file survive; it does not authenticate
against an independent same-UID writer deleting all authority. Startup quiet
lasts at least 60 seconds. Before the first wire, the source rechecks workflow
serialization, full wrapper history, newest owned metadata, original bytes and
claim, and its current journal generation.

The retained read/write journal FD, current named inode and parent are checked
before reads, writes and wire admission. Commits truncate the same held inode,
write/fsync and verify readback while independently retained lock descriptions
remain held. An interrupted rewrite can leave an unusable empty/partial file;
that deliberately sacrifices automatic recovery to refuse an older allowance.
A future operator recovery needs an exact new reviewed MAX. Pinned artifact
cleanup spans journal and lock close, so an ordinary healthy close failure
cannot return a valid-ready output. Existing primary errors remain primary.

Budget accounting retains the original 900-second shared prepublication
ceiling and 1,290-second absolute entry ceiling inside the unchanged 30-minute
job. Its NEW nonrenewing subdivisions are authentication 30, restoration 180,
initial snapshot 120 and source collection 570 seconds. The source allocation
includes startup quiet and its nested first-wire proof of at most 30 seconds;
it is also clipped by the original entry+900 ceiling and reserved publication
and export time. Added preparation never grants another 900 seconds afterward.
Publication, all CAS attempts and all failure snapshot/metadata verification
share ONE original fixed 180-second window. Export retains 30 seconds; no new
independent failure allowance or competing outer publication alarm is added.
The original per-call timeout remains at most 30 seconds clipped to the phase;
there was no original 120-second admission guard, and none is introduced.

The normal nonpublisher/GitHub path remains UNPACED, with its original source
publication, three CAS attempts and precreated one-hour App token workflow.
There is no imported six- or fifteen-second GitHub loop. Authenticated current
job proof precedes restore and clamps remaining execution to the actual
30-minute job. The daily cron and original test remain unchanged. Publication
`last_seen_at` and `last_checked_at` both retain the original ENTRY clock.

Private ordinary controls exercise synthetic complete cards, genuine-empty
failure, expiry-empty success, original main first/later restoration and the
original publication/fallback body. The four-key
`RAUS_PACING_MAINTENANCE` environment must exactly match the approved HOSTED
declaration. The workflow now delivers the precise reviewed prospective value;
private FIRST/LATER controls parse that actual YAML value unchanged.
Missing or mismatched approval refuses before wire. No offline fixture proves
real publisher facts, authenticated hosted delivery, native fit or resource
capacity. These remain separately reviewed ROOT-only boundaries.

## Exact prospective LOCAL binding for issue #342

The accepted fresh observation covered all 30 attempts, 26 jobs and four
explicitly qualified cancelled jobless rows. The source runtime pin digest is
`94daf53e83ab1fe0c15fc61bb71b87e9ec10e538f84d2ce4c880c7acefe18dca`;
it is distinct from the external RO audit digest. The full witness preserves
58 prior plus 28 fresh typed observations, all 32 unique timing points, both
original native physical starts, all prior public 28 points and the stronger
prior public not-before. No age fold or history truncation is applied; capacity
remains 128. The exact proposed state hash is
`792307f9ce8eb62d22a0be738da0d95c10c8bc438546056b7fb5336affd202d1`.

The six-key LOCAL declaration changes only its evidence field from the actual
accepted candidate to the real ROOT approval URL. Its authentic original
seven-key owned envelope, complete seven-field state, immutable producer and
PRESENT predecessor raw/inode/lock provenance remain exact. New false owner
fields describe prospective first initialization into an ABSENT modern family;
they do not settle unknown legacy policy, completion or owner ancestry. The
outside claim is derived from the complete declaration, including that real
URL, and its exact current filename is `.de-rausvonzuhaus-modern-874b383359dc46c42aac934d5c5099c99d447d3420103e9fbdee6624ed961b39.once`.
No original namespace, claim, journal, publisher request, native test or Action
was executed while binding these LOCAL constants. Subsequent HOSTED approval
and maintenance delivery are recorded below; hosted production remains separate.

## Exact prospective HOSTED binding for issue #342

[ROOT's public HOSTED purpose](https://github.com/YouthOpps/data-pipeline/issues/342#issuecomment-6223878551)
binds the independently accepted complete retained MAX: 434 typed observations,
all 38 known rolling points, and seed SHA256
`7c7a9c871d7fe9ee14406adec72d448349affa7569ef2f1942a0a0056033e23e`.
No point is age-folded or truncated; capacity remains128 and legacy rate ancestry
remains unknown. Earlier LOCAL declaration32 and exact full30 job pins remain
byte unchanged. Stronger public guards, all actual native/current clocks,
intervals, page gaps, strikes, embargoes and visible holds remain conservative.

The six-key HOSTED literal and delivered four-key YAML maintenance JSON share
exact schema/family/evidence/state. HOSTED provenance contains only the original
latest owner, artifact and immutable producer; LOCAL inode/claim authority is
not transplanted to a future GitHub runner. Fresh false INIT fields and quiet
not-before are prospective configuration, not historical owner settlement. The
original runtime must create and settle its own INIT, prove its own absent new
namespace, wait startup quiet and recheck full history before first wire.
Current mature LOCAL remains PRESENT and receives no reentry/reset allowance.

FIRST restoration authenticates the original seven-key schema1 envelope with
complete seven-field legacy state. LATER restoration authenticates the newest
owned seven-key schema1 envelope with modern state3, retaining any stronger
current constraints. Missing/unequal maintenance, changed/newer history, invalid
owner/source/head, unsupported or pending state refuse before publisher access.
The original complete listing/parser/IDs, one daily14:33 UTC schedule, 30-minute
job, permissions, normal publication/token and original test remain unchanged.
Binding and private checks perform no actual source/API/namespace/ZIP/native or
Action operation; production verification remains a separate subsequent step.

## Exhausted connection retries

An exhausted ordinary connection failure still fails collection. When the actual
physical attempt durably recorded that it received no response, its exception
carries the current transaction, request identity, and completion timestamp.
Only an exact match to the healthy retained state permits terminal settlement
and pacing export. This keeps the existing retry limit and all MAX timing
constraints while allowing the next scheduled run to restore its actual state.
Body-read, TLS, response-close, policy, and persistence failures remain pending
and cannot use this settlement. This change does not reconstruct the missing
state from the 21 October 2026 run or authorize an older-artifact fallback.
