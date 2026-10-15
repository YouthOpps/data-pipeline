# Slovak National Scholarship Programme

Source `sk-national-scholarship-programme` integrates the National Scholarship
Programme (NSP), administered by SAIA, n. o., and financed by Slovakia's
Ministry of Education, Research, Development and Youth. The assigned
[about page](https://www.scholarships.sk/en/main/o-programe) explicitly supports
two-way mobility. Collection covers all three incoming and all three outgoing
applicant tracks in the genuinely published October 2026 call.

## Official access and permitted reuse

Official `scholarships.sk`, `stipendia.sk` and `saia.sk` public programme pages
returned ordinary HTTPS HTML during the 9 October 2026 review. Their robots
responses were HTTP 200 with empty bodies, a valid policy with no restrictions.
The issue's historical robots/HTTP 403 limitation was not reproduced. Normal
TLS/proxy behavior and the identifying project User-Agent
`YouthOppsBot/1.0 (+https://youthopps.org/bot/)` are preserved.

The actual [SAIA legal page](https://www.saia.sk/en/main/about-us-main/legal-information-and-data-protection/)
links the binding [Slovak website terms](https://www.saia.sk/_user/documents/SAIA/pravne-info/VZP-SAIA-weby2018-SK.pdf)
and an informative [English translation](https://www.saia.sk/_user/documents/SAIA/pravne-info/VZP-SAIA-weby2018-EN.pdf).
Clause 5 permits distribution of public SAIA information for **noncommercial
purposes**, with SAIA indicated as the source and the time of retrieval given.
Income/profit-making use instead requires SAIA's written consent. This is a
conditional permission, not an unrestricted open licence. Output supplies
`SAIA, n. o. – www.saia.sk` attribution in metadata and record evidence, with
UTC `last_checked_at` retrieval clocks in both records and metadata. Summaries
are independently written factual descriptions; numerical benefit tables are
facts, not reproduced protected descriptive prose. No application, personal
scholarship-holder record, contact submission or login route is requested.

Runtime also verifies the binding PDF against its reviewed fingerprint. A
changed/unavailable policy requires another review and fails collection;
the PDF itself is not published. The English translation does not supersede
the binding Slovak conditions. Copyright/footer alone is not treated as an
additional permission gate.

All official SAIA-family origins, robots, redirects and retries share one
process-locked publisher allowance: at least 12.1 seconds between physical starts and
no more than five starts in any rolling 60 seconds. The state directory is publisher-wide (`saia-publisher-pacing-<uid>`); both
legacy `saia.sk` and `scholarships.sk` hash-key inodes are locked in sorted
order and receive the MAX-merged state across `scholarships.sk`, `stipendia.sk`, `saia.sk` and its
subdomains, so the separately implemented SAIA grant database must use the
same key. No cross-folder import/shared framework is required. Separate cloud
machines and production runs must be coordinated, including between sources
17 and 18; serialized publication alone does not grant separate publisher
allowances. Any stricter robots delay or Retry-After is honored. GitHub API
requests have their own host key.

## Current complete inventory

Six records represent actual applicant categories with different eligibility
and funding, not one record per amount, country or capacity:

| Direction | Actual applicant track | Official identity |
| --- | --- | --- |
| Incoming | University students | `foreign-applicants#elapp_stud` |
| Incoming | PhD students | `foreign-applicants#elapp_phdstud` |
| Incoming | University teachers/researchers/artists | `foreign-applicants#elapp_research` |
| Outgoing | Students at Slovak HEIs | `uchadzaci-zo-slovenska/#opuch_stud` |
| Outgoing | PhD students at Slovak HEIs | `uchadzaci-zo-slovenska/#opuch_dokt` |
| Outgoing | Postdoctoral researchers | `uchadzaci-zo-slovenska/#opuch_vysk` |

The [terms index](https://www.scholarships.sk/en/main/programme-terms-and-conditions/)
links the two directions. The English outgoing URL contains `2023`, but the
actual index and directly linked current Slovak conditions establish its
current authority; that URL text never supplies a deadline year. The outgoing
Slovak article supplies additional material conditions absent from the English
summary. No translated language clone or extra umbrella record is emitted.

The actual [English October call](https://www.scholarships.sk/en/news/call-sum-sem-acad-yr-26/27-apply-now)
and [Slovak October call](https://www.stipendia.sk/sk/aktuality/vyzva-ls-ar-26/27-otvorena)
explicitly name **31 October 2026**. The latter addresses both applicants
resident in Slovakia and incoming applicants. Together with the explicit
16:00 CET time, the supported instant is `2026-10-31T15:00:00Z`: literal CET is
fixed UTC+01, rather than an invented summer daylight offset. Annual yearless
April/October terms alone would not justify creating a dated round. Old April
2026 recipient-result announcements are not independently collected offers.

Current start windows are distinct from latest completion dates: students
start 1 February–1 April 2027 and finish by 31 August 2027; the other listed
academic categories start 1 February–31 August and finish by 30 November 2027.
General duration maxima do not override these current-round limits. The
outgoing postdoctoral track is the programme's eligible teacher/researcher
subgroup; the call uses the broader academic labels. No new date is inferred
from a general maximum duration.

Incoming applicants must also deliver the original admission/invitation to
SAIA by 12:00 on the third Slovak working day after the deadline. That is a
secondary document requirement, not another application deadline. Outgoing
applications are electronic only; the incoming postal rule is not applied to
them. No working-day date or noon UTC offset is fabricated.

## Eligibility and funding

Incoming host country is explicitly SK. Citizenship eligibility is any country
**except Slovakia**, not a guessed positive list. Full-degree study already in
Slovakia is excluded; so are applicants who spent at least 15 months in
Slovakia during the preceding 36 months and overlapping publicly financed
scholarship stays. Reapplication normally requires at least two years since
last NSP award/receipt, with exceptional force-majeure consideration.

Students require study abroad at master's level or at least 2.5 completed years
in the same/similar degree, plus accepted Slovak academic mobility; the category
is not PhD funding. General full-semester/trimester durations remain evidence,
subject to the current dates. Funding is EUR620/month. PhD applicants study or
train abroad and require a Slovak host eligible for doctoral training; general
mobility 1–10 months, EUR1025.50/month. These two categories may request the
same distance-based travel component, EUR0–1500, with their scholarship.

Teachers/researchers/artists require invitation by a certified Slovak
non-business research/education institution for 1–10 months. Monthly tiers:
EUR1025.50 without PhD/less than four years' experience; EUR1370 with PhD/less
than ten years; EUR1470 with PhD/more than ten years. The table leaves exactly
four/ten-year equality boundaries unspecified. No-PhD applicants with **more
than four**, not four-or-more, years are normally excluded; special cases with
more than four but at most seven years may be considered at the lowest rate.
PhD study counts as experience, capped at six years for funding determination.
The student/PhD travel component is not extended to this category. Eligible
long-stay non-EU/EEA/Swiss permit holders may receive the published up-to-EUR250
medical-examination reimbursement with an original invoice.

Outgoing eligibility is disjunctive: Slovak citizens irrespective of permanent
residence; EU/EEA/Swiss citizens with residence rights/permanent residence;
third-country nationals with permanent/long-term residence in Slovakia.
Institutional affiliation and residence are not a Slovak-only citizenship
restriction. The positive citizenship list stays unknown. Foreign destinations
are generally worldwide, with Russia stays currently excluded. No host SK tag
or fabricated destination whitelist is inferred from country payment options.

Outgoing students undertake second-level study or thesis-related research;
fourth-year integrated-degree students are also covered. BA/six semesters must
be completed before departure, with return to finish the Slovak degree. PhD
mobility must relate to the doctorate. External-format students in both tracks
must be full-time employees of an eligible home institution and return to it.
Postdoctoral applicants are full-time teachers/researchers at a certified
Slovak non-business institution, normally within ten years of PhD award; special
cases beyond ten but no more than thirteen years may be considered. Research
stay 2–6 months and return to the home institution are required. Full-degree
study abroad, overlapping publicly financed stays and repeat applications have
the documented limitations.

Outgoing living-cost funding is country- and track-specific, not tuition.
The current [monthly table](https://www.stipendia.sk/sk/main/podmienky-pre-predkladanie-ziadosti/uchadzaci-zo-slovenska/vyska-stipendia/)
contains 69 country rows with three real recipient columns. Each outgoing
record retains its actual column's numerical amounts as evidence; destinations
outside the table may be considered with an approved rate. Country rows are
not extra awards or citizenship/host tags. The table references 30 April 2026
and following deadlines under conditions approved 26 August 2026; both dates
are retained in provenance, not substituted for the current application date.
All outgoing tracks have the distance-based EUR0–1500 travel component,
requested with the scholarship and paid at the end.

Excluded: institution-hosting directories/help finding an invitation, payment
and medical reimbursement tiers, prior recipient lists/testimonials, generic
research-in-Slovakia directories, application system pages, archived terms and
language duplicates. These do not establish independent NSP funding awards.

## Integrity, commands and publication

Every required article and actual home-to-call link must match reviewed
conditions fingerprints; HTML must be complete and all real category anchors
present. The funding table must have all 69 distinct country rows and three
correct amount columns. A missing page, denied policy, changed facts, incomplete
markup or changed binding terms fails the whole collection. Navigation, footer
and homepage personal testimonials are outside factual fingerprints. This
source fails closed for review rather than silently publishing stale amounts,
partial directions or a newly inferred year.

```sh
python -B adapters/sk-national-scholarship-programme/test_adapter.py
python -B adapters/sk-national-scholarship-programme/adapter.py
```

The sole live test retrieves genuine records and emits one complete JSON
stdout document; diagnostics use stderr. It has no publication token and no
output files. Default adapter execution is non-publishing. IDs hash source,
direction, recipient track and the source-proven October 2026 round; amount or
title changes do not change the same call's identity. UTC observation clocks
record retrieval; source publication dates remain unknown. Plain summaries are
strictly capped at 600 UTF-16 code units for both fresh and prior records, and
acceptance includes the actual unmodified website `loadData` consumer.

The matching workflow runs on main pushes touching its own folder/workflow,
manual dispatch and once daily at 23:05 UTC. It preserves the repository/
main guard, minimal permissions, App token scoped to `YouthOpps/data-source` and
source-family publication concurrency. Its only application command runs this
standalone adapter with `--publish`.

Publication writes only
`datas/sk-national-scholarship-programme/{data,metadata}.json` atomically.
First failure creates no source folder. Later failure preserves last-good data
and success timestamp, recording failure metadata when safe. Sibling-source
changes may rebase; newer own-source changes cannot be overwritten. Only valid
nonempty successfully published records advance durable success. Test/static
publication mocks are not production publication; maintainers verify after
merge and coordinate the shared SAIA request allowance.

## Production access diagnostic

The cloud author and independent live collections retrieved empty HTTP 200
robots policies and all six opportunities. The first production runs instead
received HTTP 403 from `https://www.scholarships.sk/robots.txt`, before any
programme collection or source publication. These observations establish an
access difference; they do not identify its cause.

Under the reviewed [workspace access policy](https://github.com/YouthOpps/ai-workspace/issues/18),
HTTP 4xx except 429 from a verified robots retrieval, including its reviewed
same-host redirects, means unavailable REP rules. This does not imply content
permission or invent a sitewide Disallow. The robots request retains
`Accept: text/plain, */*;q=0.1`, the exact bot identity, verified TLS and shared
pacing. Actual content 401/403, a clear access challenge or applicable explicit
Disallow remains a retained refusal. Robots/content 429 stops this job and
retains a timed embargo, rather than becoming a permanent permission denial.
TLS/network/5xx failures are never treated as absent robots.

### Durable family state and narrowly reviewed recovery

The [last NSP hosted run](https://github.com/YouthOpps/data-pipeline/actions/runs/37866043357)
at revision `08df07fee129bbe1c416e0f417df2c727ea423b3` failed on
`https://www.scholarships.sk/robots.txt` HTTP 403 before programme collection.
Its complete three-run history is pinned with runs `37865230903` and
`37865212036`. These old NSP jobs exported no state artifact: unlogged historic
Retry-After headers cannot be reconstructed and are not claimed restored.

The sibling [first Study in Slovakia run](https://github.com/YouthOpps/data-pipeline/actions/runs/38070837752)
at `9c27a9e15b9c2fdb6c734952ea8359658fc5cbb4` made its sole publisher
request to robots.txt and retained the resulting HTTP 403. The
[following run](https://github.com/YouthOpps/data-pipeline/actions/runs/38070842559)
restored that refusal and made no content request. Its real
`saia-pacing-state-1` artifact (ID `11676726932`, ZIP SHA-256
`ba99101b643c8c6f298bab6d19b8e091ed5ec5c6f9747d5ddf0176505fbcfdf4`)
contains the known starts, interval, embargo and that robots-only blocked flag.

Only this exact reviewed artifact may reclassify the incoming blocked flag:
both source histories must match all five pinned attempts and revisions, and
the old sibling workflow must still be manually disabled. Unknown local holds
are OR-retained and never cleared. Every known timing constraint from the real
artifact, both local inodes and the complete successful native journal
(`_REVIEWED_MIGRATION_NATIVE`, schema 1, native SHA-256
`2711ed565eddd1d9d4e7900797c76a55d99422e79558a092e42e108beaa10088`)
is MAX-merged, with at least 60 seconds of new
startup quiet time. This explicit policy migration acknowledges the old NSP
header-evidence gap; it is not a generic cache reset or permission bypass.
Actual content refusal after recovery stops retrieval and remains durable.

Each hosted run restores the latest completed attempt across both source
workflows before reading the data-source snapshot or contacting a publisher.
Older-ID reruns and queued reruns' previous completed attempts participate in
completion chronology from authenticated exact-attempt job completion times,
not mutable run `updated_at` metadata. Active competing collectors, missing/incomplete history,
ambiguous chronology, missing/expired/invalid artifacts and wrong source,
repository, attempt or head bindings fail closed. ZIP input is bounded and inert;
authentication is stripped from signed download destinations. New state is
exported even after collection, snapshot or publication failure and uploaded as
`saia-pacing-state-<attempt>` with a repository/source/run/attempt/head-bound
schema-1 envelope. No normalized opportunity records or credentials belong in it.

A 429 at least doubles the already retained physical and page intervals,
including stronger publisher limits, with an exponential count-based floor. Without
a valid Retry-After, the embargo starts at 30 minutes and doubles on repeated
429s; longer publisher embargoes remain in force. An active embargo fails before
another publisher start. After expiry, a bounded normal attempt may proceed at
the slower persisted intervals. Hard access refusals remain distinct. The
completion guard holds the family budget lock through each response and records
the following interval, so actual starts cannot race timestamp reservations.

The shared `collection.lock` rejects overlapping local SAIA collections;
`youthopps-saia-publisher-v1` serializes hosted jobs. Separate machines still
require operator coordination. The old sibling runtime rejects source17
reactivation and cannot consume the new timed-rate state keys; it must remain
disabled until its separately reviewed family migration is merged. This PR does
not activate either workflow or dispatch Actions. Hosted reachability and durable
artifact upload require separate maintainer verification after merge.

## Expired-record cleanup

The current NSP call closes on 31 October 2026 at 16:00 CET, normalized to
2026-10-31T15:00:00Z. The fixed stated CET offset is preserved; original document
delivery and scholarship start/end dates never replace the application cutoff.

The complete fresh inventory is fetched and validated before cleanup. The next
source-scoped atomic data/metadata publication replaces the prior dataset rather
than merging its rows, so expired previously published records cannot survive
that successful replacement. Current records and records with missing or
ambiguous deadlines remain eligible; status alone does not determine expiry.

Offset-aware deadlines expire only after the stated instant. A date-only
deadline with no documented publisher timezone remains eligible through that
date in every civil timezone: cleanup uses the conservative UTC-12 calendar
boundary, without guessing a publisher timezone or an unstated closing hour.
The source recipes and their original deadline fields remain unchanged.

A valid nonempty fresh inventory may produce an empty cleaned dataset; that
empty result is published atomically with success metadata. An empty upstream
collection still fails validation. Fetch, parsing, validation and publication
failures preserve the last-good dataset (including a prior empty success) and
success timestamp; cleanup never performs a separate deletion or writes another source.
The sole live test validates the complete nonpublishing inventory, including
closed records needed to verify source completeness; publication alone applies
the deadline filter after that validation.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. Durable budget
identities are preserved; current pacing and backoff behavior is described
above. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **23:05 UTC** (`5 23 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory, so no inventory-page guard
is exercised. Physical requests retain the publisher pacing, backoff and
restrictions described above; the daily schedule is unchanged.

## Workflow concurrency

`youthopps-saia-publisher-v1` serializes runs of this source and
`sk-study-in-slovakia`. Up to 100 pending runs are retained (`queue: max`);
active runs are not cancelled (`cancel-in-progress: false`). Unrelated publisher
families may collect and publish in parallel. Existing source-only atomic
publication rebases on unrelated repository updates, uses up to three
publication attempts, and never force-overwrites newer data.
This grouping does not enable a disabled workflow; re-enablement belongs to
the separate maintainer handoff after independent QA.
