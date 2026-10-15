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
