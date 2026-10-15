# Rausvonzuhaus vacancies

This adapter reads the actual public [Last Minute Markt](https://www.rausvonzuhaus.de/lastminute) vacancy cards published by Eurodesk Deutschland / IJAB. The FAQ and other directory pages are not records. Every result links to its original normal `/lastminute/detail/<numeric-id>` page. The numeric publisher ID supplies the stable source identity; title, dates or position in the list do not determine that identity.

## Access and reuse

Reviewed on 8 October 2026: the public [robots policy](https://www.rausvonzuhaus.de/robots.txt) permits the normal listing and detail routes, but disallows `/typo3/` and paths containing `hide_page_frame`. Collection uses the normal listing only, without embedding queries, login, applicant data or external application requests. Requests use the declared YouthOppsBot identity and an ordinary HTML Accept header. Transient HTTP 500/502/503/504 or non-TLS connection failures receive at most one paced retry; HTTP 429 always stops the current attempt. Retry-After also applies to allowed retries. Robots is checked on each run and on content redirects. A complete HTML document and nonempty, valid card inventory are required.

The publisher's [imprint](https://www.rausvonzuhaus.de/impressum) states:

> Soweit im Einzelfall nicht anders geregelt und soweit nicht fremde Rechte betroffen sind, ist die Verbreitung der Inhalte von www.rausvonzuhaus.de über die embedding Funktionen oder in Teilen davon in elektronischer und gedruckter Form für die Arbeitsfelder Internationale Jugendarbeit und Jugendinformation unter der Voraussetzung erwünscht, dass die Quelle (www.rausvonzuhaus.de) genannt wird.

It prohibits commercial distribution without prior written IJAB permission. YouthOpps' intended noncommercial youth information use matches the stated purpose. Records provide attributed partial factual metadata and our own concise summary; they do not reproduce photographs, image credits or full third-party descriptions. Every record and source metadata attribute `rausvonzuhaus.de / Eurodesk Deutschland (IJAB)`. The official contact for questions is `rausvonzuhaus@eurodesk.eu`, with IJAB contact `info@ijab.de` on the imprint.

One robots request and one listing request normally provide the entire inventory. Publisher requests, retries and redirects are separated by at least 12.1 seconds and limited to five starts per rolling minute, with a completion guard retaining longer intervals even after rolling starts expire. The publisher's stricter robots delay and Retry-After are honored. The original private `de-rausvonzuhaus-pacing-<uid>/<sha256(host)>` journal remains in use. One collection lock spans restore, snapshot, collection, publication and finally export. Production Actions serialize this source; separate machines must not run it simultaneously. Trusted per-attempt workflow artifacts preserve restrictions across hosted runners.

## Inventory and fields

All cards are present in the public HTML; no pagination or external detail enrichment is necessary for the supported fields. Parse the `lmm-item` card boundaries and actual title, programme badge and factual paragraphs. On 8 October 2026, the inspected listing contained 477 distinct numeric IDs; 471 had internally consistent dates: 462 volunteering, seven youth/school exchanges classified as training, and two student internships. Counts are discovered on every run, not hardcoded acceptance targets.

Programme badges include IJFD, weltwärts, workcamps, French-German and other volunteering, European Solidarity Corps, youth encounters, student internships and school exchanges. The adapter retains the publisher's German programme label in tags and classification evidence. Age bounds, publisher host label, project dates and application deadline come directly from each card. Two-letter host codes are a mapping of the explicit country label, including the commonly used `XK` for Kosovo. A host country does not establish citizenship eligibility: `eligible_countries` remains empty. Costs and benefits are unspecified in the list and are not guessed; a reviewed detail example requires a €3,200 contribution, so inclusion does not imply free participation.

The publisher sometimes has a title/location disagreement, for example a Trento title beside a Germany host label. Preserve and attribute the structured label; do not infer a replacement host country from a place name in a title. Each record's classification evidence preserves the actual publisher host label for review. Titles are normalized for whitespace; image copyright credits are excluded.

## Dates and exclusions

Explicit `DD.MM.YYYY` dates become ISO calendar dates. `deadline` is `YYYY-MM-DD` with `deadline_precision: date`, and `start_date`/`end_date` preserve the project interval. The publisher gives no deadline clock or time zone. No closing instant is invented. The website interprets date-only deadlines through the end of the UTC date as a consumer convention, not a publisher statement. Future deadline days on a published vacancy are `open`, past deadline days are `expired`, and the same UTC calendar day is conservatively `unknown` because the closing clock is unspecified. Valid expired opportunities are retained; an ended project alone is not an exclusion.

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
access; restrictive rate and delay directives remain effective. Native
pacing, backoff and durable budget identities remain unchanged. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **14:33 UTC** (`33 14 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. No inventory-page delay is added: all actual cards are already present in the
one listing response. Physical request pacing and publisher restrictions apply
to both robots and the listing, including retries and redirects.

## Workflow concurrency

`youthopps-de-rausvonzuhaus-publisher-v1` serializes runs of this source. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Retained throttling and state transition

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
repository, branch and source checks. Jobless cancellations cannot have made a
publisher request and add no replacement budget.

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
