# Luxembourg development cooperation opportunities

This standalone source indexes the Directorate for Development Cooperation and Humanitarian Affairs' [Getting involved hub](https://cooperation.gouvernement.lu/en/s-engager.html), its substantive advertised programmes, and every subsection of the linked [Calls for application hub](https://cooperation.gouvernement.lu/en/s-engager/appels-a-candidature.html). It does not describe itself as a catalogue of all Luxembourg or partner-agency vacancies.

## Access and factual reuse

Reviewed 15 October 2026: the official [robots file](https://cooperation.gouvernement.lu/robots.txt) permits these query-free paths for `YouthOppsBot`. The wildcard rules prohibit most query/search URLs; the adapter does not use those routes. Separately named crawler restrictions do not grant this agent another crawler's identity. The [legal notice](https://cooperation.gouvernement.lu/en/support/aspects-legaux.html), Intellectual property / Documents of the text type, allows viewing, downloading and printing text without prior request. It requires prior agreement for uses outside its grants and says the government website must not be integrated into another portal without express authorization. Those broad conditions are acknowledged; no open licence or permission to copy full descriptions is claimed. The user authorized an independent factual index; the integration clause is not treated as an explicit ban on every title, basic fact or ordinary link. Output contains publisher titles, original short factual summaries and direct source links, without embedded government pages, copied long descriptions, images, personal contacts or application documents. No publisher contact, account, credential, form submission or challenge bypass is used.

Content HTTP 401/403/451 and explicit human-verification challenges retain a local refusal requiring reviewed recovery. Actual content errors, TLS failures, invalid markup and incomplete coverage fail collection. An unavailable robots file with any HTTP 4xx except 429 alone is treated as unavailable REP rather than a content refusal; actual robots disallow rules remain binding. A robots or content HTTP 429 stops the current collection, retains at least 30 minutes of backoff, and doubles the retained physical interval and page interval. Later 429s double those intervals again and exponentially increase the minimum embargo (30, 60, 120 minutes, etc.). Longer publisher `Retry-After`, robots delays and existing embargoes take precedence. A timed 429 is not a permanent refusal; a later ordinary attempt after its embargo must still use the slower retained limits. A malformed, negative or overflowing Retry-After on 429 is unusable timing evidence: the exponential minimum, stronger retained embargo and increased current intervals still apply without inventing a permanent content refusal. Invalid retry instructions on other responses continue to fail closed.

## Complete source inventory

The live hub advertises seven concrete programmes with substantive descriptions and application mechanisms. Each becomes a `programme-overview`, with `status: unknown` and no invented deadline. Standing programme information does not establish that recruitment is currently open:

| Programme | Indexed facts and scope |
|---|---|
| Directorate internships | Paid 3–6 month placements for Master's students; Luxembourg citizenship or residence and specified languages. Availability-dependent; placement country remains unspecified. |
| ENDA Santé internships | Dakar, Senegal; travel contribution conditional on an internship report. Other eligibility and availability remain unspecified. |
| Junior Technical Assistant (JTA/ATJ) | LuxDev employment/training mechanism for post-secondary graduates aged up to 30; nationality/residence conditions and separate annual calls. |
| Junior Professional Officer (JPO/JEA) | UN employment mechanism for Master's graduates with experience; nationality/residence conditions. Its age wording is inconsistent (introductory “under 32” versus “not being over 32”); no exact age is asserted. Specific vacancies govern selection. |
| UN Volunteer programme (UNV) | Youth and specialist funding mechanisms with substantive eligibility. Actual current calls govern selection; standing youth information is not imposed on a later Associate/Specialist call. |
| Junior Professionals in Delegation (JPD) | EU delegation placement mechanism; Master's qualification and nationality/residence/experience conditions; separate periodic calls. |
| Voluntary Cooperation Service (VCS) | Supported 6–12 month volunteering for Luxembourg residents aged 18–30 who are neither students nor employees; subsistence and social insurance support. |

All programme accordions are examined. The three explicit exclusions are generic NGO volunteering referrals, external NGO job referrals, and cooperation leave/reimbursement for existing workers. They are not individual youth opportunities. A new or changed substantive programme, missing application route or changed supported fact stops collection for review instead of silently reducing coverage. The adapter checks every summary's supporting publisher markers on every run; these are semantic mappings, not cached records or a fixed count substituted for retrieval.

The actual Calls hub currently has one native subsection, [UN Volunteers (UNV/VNU)](https://cooperation.gouvernement.lu/en/s-engager/appels-a-candidature/UNV.html). Its recruitment notice describes four Associate/Specialist positions and a 17 August 2026 application deadline. It is indexed as **one grouped call**, not four fabricated individual vacancies. Nationality or Luxembourg residence alternatives are summarized without turning a residence alternative into an exclusive citizenship list. Individual job descriptions and criteria remain on the linked UNV application site and are not invented. The grouped call is distinct from the standing UNV programme; its expired deadline does not prove that the general programme ceased.

Native subsection links are followed recursively without an arbitrary record/page cap. Unexpected pagination, unclassified calls, navigation cycles or unsupported deadlines fail closed; they are not truncated. No search parameters or hidden APIs are invented. No sitemap, news article, brochure, directory, external partner board or ministry homepage is promoted to an opportunity. Incremental early stopping is not justified: the source supplies neither a durable change feed nor a documented order/change boundary covering older edits, so every run retrieves the complete hub and its native calls inventory.

## Identity, pacing and schedule

Every source request, including robots and redirects, uses exactly `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`, normal TLS verification and the same retained local budget. Local collection runs serialize under a source lock; the production workflow has a single source concurrency queue with cancellation disabled. Production and local acceptance runs must not overlap across machines; a local lock alone cannot coordinate separate runners.

Physical source starts are at least 12.1 seconds apart and at most five may start in any rolling 60 seconds. A completion guard covers failed connections as well as responses; reservation timestamps alone are not considered proof. Inventory pages (the initial hub, calls index and native child sections) additionally wait at least 30 seconds after the preceding inventory response completes. The page guard remains separate from the physical-request interval. The current source needs four source requests including robots, not one request per standing programme.

The daily fixed schedule is **23:30 UTC**, cron `30 23 * * *`, randomly selected once from free slots in the source/site/workspace timetable. Merge-path and manual triggers remain enabled; daily scheduling is not a one-run-per-day cap. GitHub may delay scheduled runs. The collection job is bounded at 30 minutes, comfortably above the measured complete current collection; the publication token is created once for that job and normally lasts 60 minutes. No timeout/request cap is raised to conceal incomplete collection.

Publisher state lives outside the adapter tree in a private temporary directory and retains request starts, completion/backoff deadline, rate-limit strikes, physical/page intervals and protective refusals. Production restores the exact newest completed source attempt's inert artifact, including failed attempts and the exact preceding completed attempt of queued older-ID reruns. Chronology comes from the exact attempt’s completed jobs, not mutable workflow `updated_at`; prior attempts must share their wrapper’s immutable commit. Missing, expired, malformed, wrongly bound or incomplete history/artifacts stop before publisher access; there is no fallback to an older artifact or empty cache. The first-ever source run alone needs a reviewed bootstrap covering the final native journal, and adds at least 60 seconds of startup spacing. Restoration takes the maximum limits/strikes/embargo and the union of protective refusals. Trusted state is restored before any publication-snapshot/authentication read, and the same lifecycle lock is held through snapshot, collection and final state export. State is exported on snapshot failure as well as collection failure or success; it contains infrastructure data, no opportunity output or credentials. Production artifacts are retained for 90 days; expiration requires reviewed recovery rather than resetting history.

## Validation, expiry and publication

Stable programme identities use the named programme mechanisms (`jta`, `jpo`, `unv`, `jpd`, `vcs`, and the two institutional internship routes), independent of mutable titles/generated accordion anchors. Call identity uses its canonical publisher URL. Public IDs have the source prefix plus a deterministic digest. Final IDs and the entire nonempty collection are validated after assignment; case-insensitive duplicates are rejected. Programme links target current publisher anchors. Host country is included only for the explicit Dakar placement; other programme duty stations and eligible-country arrays remain unknown. No general publisher country is misrepresented as a programme's host or an applicant nationality requirement.

Only an explicitly labeled application deadline is used. Last-modified timestamps, May/January recruitment descriptions and programme durations are not deadlines or evidence of current openness. The publisher supplies no closing timezone for the grouped call, so a date-only deadline remains retained until that date has ended everywhere (UTC−12); no exact closing instant is claimed. Undated standing programmes remain retained.

Raw collection must be complete, valid and nonempty before expiry filtering. A successful complete replacement removes expired old records as well as expired fresh ones and atomically writes only `datas/lu-luxembourg-cooperation/{data,metadata}.json`. The observed raw result has eight records; the expired grouped UNV call is removed, leaving seven standing programmes. These are observations, not pinned counts. All-expired valid raw input publishes an empty success pair and permits subsequent empty-snapshot reads; empty raw retrieval remains a failure. Fetch/parse/validation/publication failure preserves last-good data and its success timestamp; before first success, failure creates no source folder. Source-scoped optimistic commits never force-overwrite a newer source snapshot or alter other sources. Tests do not publish, and collection success does not certify production publication.

## Run

Python 3.12+, standard library only; the folder works independently when copied.

```sh
python -B adapters/lu-luxembourg-cooperation/test_adapter.py
python -B adapters/lu-luxembourg-cooperation/adapter.py
```

The sole live test retrieves the complete genuine source, validates every record and emits one complete JSON array on stdout, with request/test diagnostics on stderr. It does not require publication credentials or save records. The ordinary adapter command prints run metadata. The workflow's sole application command is:

```sh
python -B adapters/lu-luxembourg-cooperation/adapter.py --publish
```

`DATA_SOURCE_TOKEN` is required only for publication. `GITHUB_TOKEN` reads trusted source-run/artifact metadata; first-run bootstrap and runner artifact paths are infrastructure configuration in the source workflow. Offline in-memory scenario evidence stays outside the delivered tree and does not substitute for the sole native collection test or current website-consumer checks.

The response-completion guard retains the current physical interval even when
it exceeds the rolling-minute history window. Pruning old start timestamps
never shortens a stronger retained interval; an attempt that cannot fit its
wait budget stops before publisher access.

## Source display name

Published metadata uses `display_name` = **Luxembourg Development Cooperation** for the visible
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


## Durable persistence prototype (issue #273)

The first implementation stage adds a source-local schema-3 pending transaction
before physical publisher access and before cached REP assessment. Settlement
follows the durable header/body/REP decisions. Policy-only transactions do not
count as physical requests. Target-specific REP observations retain canonical
target hashes, safe origins/paths, physical robots-body hashes, parser decisions
and delays/rates; they do not fabricate a global publisher ban. Existing actual
holds remain retained. State failures stay fatal, and safely owned current state
and stale export files are invalidated where storage permits. An earlier durable
pending record protects restart when cleanup storage is entirely unavailable.

This is an offline protocol stage, not a production transition. The public
prepare path refuses access until exact mature owned-state and local-state
compatibility is implemented and independently approved. Missing old fields do
not mean settled history. Legacy rate-limit uncertainty stays explicit; observed
new counters do not reconstruct unavailable historical responses. No publisher
crawl, fresh complete native pass, production action, or workflow change is
claimed by this maintenance stage. Existing source facts, inventory, expiry,
publication, timing floors and workflow schedule are preserved.

The unchanged `fetch_source` loop retains its two effective attempts after an
ordinary connection failure that occurred inside the opener before any response.
Its outer pending owner spans those attempts; every original physical
reservation and completion guard remains active. HTTPError counts as a
received response.
Post-response partial reads, policy/namespace/state failures, TLS failures and
unassessed response calculations remain sticky and cannot trigger another wire
attempt. This deliberately tightens the previous broad error retry in those
classified cases; it does not claim identical behavior for all failed reads.
Permitted www/bare robots redirects retain the final physical bytes' digest
under the original policy-cache origin without changing the redirect frontier.

Actual unavailable REP responses bind an explicit empty-policy digest.
Successful responses retain their physical byte digest even when decoded policy
is empty.
Missing or empty local publisher state is refused rather than initialized from
unreviewed defaults, including low-level state access.

## Guarded legacy compatibility (issue #273)

The LOCAL and HOSTED boundaries are separately bound to the maintainer review
[6204933912](https://github.com/YouthOpps/data-pipeline/issues/273#issuecomment-6204933912).
Their exact schema-4 seed includes the complete successful LOCAL native state.
This is a Luxembourg-only pilot; issue #273 remains open for other sources.
The finite bridge accepts only this source's exact reviewed schema-1 local and
newest owned inputs; missing pending fields never prove historical settlement.
The approved full MAX seed retains old holds and explicit legacy rate/policy
history uncertainty. Missing historical fields do not prove clean history.
A newer attempt, changed producing head/jobs/artifact or modern local state
invalidates the old legacy fallback. A different owner with schema-1 data fails.
Modern owned schema-4 states use strict validation and strongest/MAX merging.

LOCAL retains the original parent (device 34, inode 82812) and in-place state
inode 82813. The successful one-off absent-lock transition created collection
lock (device 34, inode 339092); subsequent LOCAL use requires that exact mature
lock, safe permissions and nonblocking lifecycle/state coordination. It never
recreates a missing or interrupted mature namespace. A changed or competing
object is preserved and requires review.

HOSTED is separate: authenticated GitHub run/job metadata must match the current
repository, main ref, source workflow and revision; an environment flag alone
is insufficient. A fresh github-hosted namespace is created exclusively only
after the exact newest owned ZIP has passed ownership/digest checks. LOCAL
absence cannot use this route. Metadata binding does not claim credential scope
or cryptographic runtime attestation. The first physical request follows at
least sixty seconds of monotonic startup quiet and every stronger old guard,
then repeats fresh coordination/history checks. Same-process reentry also
rechecks coordination. No publisher requests are added by policy-only events.

Private controls exercise the actual decoder using explicitly synthetic ZIPs
containing the already root-decoded envelopes, with fixture-specific digests.
They do not claim a second download of the genuine archives, a fresh full
publisher collection, actual migration or production readiness. Original source
facts, IDs, pagination, expiry, publication requests, publisher pacing and caps
remain unchanged; the new coordination adds fail-closed prerequisites. The
independently reviewed implementation and successful native evidence below are
bound to the separate public full-state boundary before production.

## Bounded policy history (issue #273)

The adapter uses schema 4. Its exact finite schema-1 bridge uses the approved
full native MAX seed with policy-history uncertainty retained. Schema 3 is not
accepted through generic defaults or an inferred missing-field conversion.
HOSTED authenticates the exact newest legacy owned attempt before its first
conversion; a newer applicable attempt invalidates that old fallback. Later
modern owned state follows strict validation and MAX joining.

One whole, canonical-min historical allowed witness is retained per approved
source origin. This is an arbitrary historical sample, never the latest policy,
permission, an assessment owner or evidence that a pending event completed.
Absence means unknown. All scoped denial witnesses remain lossless, separately
from numeric MAX restrictions, holds and unknown/conflict flags. A new denial
cannot replace another denial or silently disappear to satisfy a size bound.

Every actual fresh or cached target assessment first persists a distinct
`policy_pending` owner under its durable outer transaction. The unchanged REP
parser still assesses that exact target. Its complete matching current
decision and all restrictions are durably committed while the assessment is
still pending, independently of which historical sample was selected. A
separate commit clears the assessment; the outer transaction can settle only
with its own matching current receipt. Physical and logical outer owners also
retain the expected last nested assessment receipt and compare the whole
durable record before settling. The pinned proposed seed is an independent
MAX/OR input, including its unknown flags and lossless policy restrictions.
Pending owners, divergent pending joins and observed conflicts require review.
Policy-only events add no physical starts.

Typed input is rejected when a retained allowed witness, scoped denial or
current receipt records a physical delay/rate above its global interval. Such
inconsistent evidence is not silently recomputed or converted into healthy
input. Correct normal assessment commits already retain this MAX.

Allowed target/body churn does not grow the historical sample list. Scoped
denials remain bounded at 32; local JSON and the actual owned-state envelope
remain limited to 16 KiB, and archive downloads to 32 KiB. Prospective writes
and exports check those unchanged byte bounds. Exceptional denial growth or
large IRIs can exceed them: the complete collection then fails with a
pending/sticky gate and full attempted decision retained in RAM for review. No
route, denial or target is truncated, and a fresh process cannot use an older
owned ZIP to clear that unsettled state. There is no claim that unlimited
denial history can fit a fixed-size artifact.

Private actual-function controls cover fresh/cached/redirected and unavailable
REP, exact raw-body provenance, current receipt ownership, persistence and
cleanup faults, fresh-process older-state refusal, bounded churn and lossless
joins. These are isolated journals and inert transport/metadata fixtures, not a
publisher crawl, real state transition or production certification. Source
facts, IDs, parser rules, collection frontier, pacing floors, retry bounds,
publication requests and workflow schedules remain unchanged.

## Successful native evidence for this pilot

The original unchanged nonpublishing test completed once successfully: all four
actual HTTP 200 responses (robots and the three complete source pages) produced
eight raw records. Seven standing programmes retain unknown availability and
null deadlines. The grouped UNV call has the actual date-only deadline
2026-08-17 and is expired, leaving seven filtered records. The complete current
website consumer accepted 8 raw, 7 filtered, 0 derived expiry-empty and 7
last-good records after failure. The derived expiry fixture is not a claim
that the seven standing programmes actually expired.

All 33 attempted state saves have matching persisted bytes. The reviewed full
MAX includes the prior seed and all captured constraints and explicit unknowns;
14 retained start observations are not 14 physical requests. The actual minimum
physical completion gap was 12.314 seconds, logical inventory completion gap
30.190 seconds, and maximum rolling-minute starts three. Physical redirect
requests retain the existing independent transport guard; the page metric is
for original logical page completion. The new collection lock and complete
state were independently checked before public approval. This native evidence
is not a production or live website verification; those follow the merge.

## Journal ownership maintenance (#295)

Publisher journal reads and writes retain the acquired original RW descriptor,
parent identity and file generation. They check that authority again before
transport and export. A changed name, generation, lock or descriptor fails the
publisher gate; cleanup never adopts or truncates the stronger replacement.
Serialization, write, fsync and release failures remain sticky for the process.
Cleanup attempts independent original descriptors and preserves a prior error.
Safe owned artifact output is invalidated before best-effort signaling or
unlink; foreign descriptors and unsafe existing output are preserved.

The existing in-place journal format and transaction semantics remain in place.
The guard binds reads, writes and release to the original acquisition rather
than trusting a later pathname or recycled descriptor.

The existing schema4 exact LOCAL/hosted bridge and approval checks are
unchanged. Initial raw-state reads and bridge releases now retain their
acquisition identity and generation; compatibility cleanup preserves an
unrelated recycled FD.

This maintenance preserves source facts, frontier, dates, expiry rules, schemas,
seeds, history pins, pacing limits, retries, publication and workflow settings.
Validation uses actual functions with inert transport and private journals, plus
prior accepted native evidence for unchanged collection functions. It does not
certify a new crawl, production run or every legacy runtime contract. Artifact
output assumes the existing trusted private job/temporary-directory boundary; it
cannot authenticate an arbitrary concurrent same-UID writer forging output.

The retained genuine native RAM was also replayed without source requests: all
four responses, eight raw facts, seven filtered facts and all 33 saved states;
the accepted consumer handled raw/filtered/empty/last-good cases as 8/7/0/7.

Reference: https://github.com/YouthOpps/data-pipeline/issues/295


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
