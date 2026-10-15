# Volkswagen AG youth opportunities

This standalone source covers Volkswagen AG's advertised German individual student, internship, doctoral, dual-study, apprenticeship, graduate and legal-trainee vacancies and their English aliases. It does not cover other Volkswagen Group brands or claim worldwide coverage. The HRD apprenticeship portal is excluded: its fresh robots policy disallows the portal, and the adapter never requests its jobfinder body.

Current opportunities are discovered from the literal Volkswagen AG search links advertised by the guarded official German and English career homepages. The adapter follows native pagination while retaining its advertised locale, verifies each page's range and total, rejects duplicate/missing pages and retrieves every individual detail, including excluded experienced/management listings and English aliases. The bounded frontier permits at most 200 German listings and 220 total details. All native company fields must identify Volkswagen AG; locations must explicitly identify Germany. Country-only locations remain country-only, and advertised truncated multi-location rows are resolved completely from the individual detail's native location metadata.

Native `customfield1` supplies the true JobID and stable source-hashed ID. A postal code in a URL's title slug is never a JobID. Native title, canonical URL, company, location and career-level fields must agree with discovery. English aliases are deduplicated by the true JobID and checked against the German opportunity's location and career level. Duplicate responsive descriptions must contain identical material text. Unknown career levels, changed routes, missing descriptions, contradictory facts or incomplete pagination fail the collection. An individual page's explicit German or English posting-expired notice is a withdrawal, so an expired old detail cannot freeze the remaining current inventory; an unexplained missing or inaccessible detail remains a failure.

On October 14, 2026, paced research retrieved seven German listing pages (167 rows), one English listing (nine aliases) and all 176 details. Five German JobIDs were experienced/management roles, one English page was their alias, and a German/English pair explicitly reported posting expiry. The resulting current inventory comprised 161 youth JobIDs: 96 training, 44 internships and 21 jobs. Seven live English aliases matched their German JobIDs. The earlier first-detail failure concerned true JobID **31008**, a doctoral listing whose page now explicitly states that its publication period expired; `34225` is its Baunatal postal code. These counts are research evidence, not constants required by collection.

The original 25 public fields keep their names and types. Current German records contain concise original German factual overviews with `summary_language: "de"`, not loss-prone translations of detailed qualification alternatives. Each overview is at most 600 UTF-16 units and directs readers to verify the official vacancy. It is not a complete eligibility statement. The compatible optional `requirements` string array preserves objective academic/technical criteria and application-document conditions, including required/preferred distinctions, conjunctions/disjunctions, credit thresholds, grades, languages, conditional permits and application materials. Each clause is a factual recap, not a mirror of creative introductions or task paragraphs. Nonspecific subjective personality descriptions are omitted consistently, independently of length. Bounds are 64 clauses and 2,000 UTF-16 units per clause; unparsed material or oversized output fails rather than silently truncating conditions. The current website accepts this extension but does not render it; the summary and official publisher link remain its visible overview. Earlier published 25-field records remain readable.

Pay, material benefits, date windows and title/start conflicts are extracted from explicit publisher clauses. Discovery's visible publication date has calendar-day precision, so `published_at` remains null; the adapter does not invent a publication instant from a display date. Explicit application windows populate `deadline`, status and the overview's opening date. Start dates and any conflicting title year remain explicit. Hidden `validThrough` describes posting lifespan and is not substituted for an application deadline. Germany as publisher or work location does not establish applicant citizenship eligibility; `eligible_countries` remains empty.

After complete nonempty raw retrieval and validation, successful publication atomically replaces only this source's dataset with the refreshed, expiry-filtered records and success metadata. Removed old records cannot persist through that successful replacement. Explicit validated calendar deadlines are conservatively retained until the date has ended in every civil timezone (UTC-12 boundary), because these clauses do not document a closing timezone; ambiguous or absent deadlines establish no expiry. Valid raw records whose deadlines have all passed produce an atomic empty successful snapshot; subsequent reads accept that empty snapshot. Empty retrieval, incomplete parsing, validation failure and publication failure preserve last-good data and its success timestamp. Cleanup never writes separately before the successful atomic refresh.

Official access and reuse remain guarded. Fresh robots for [jobs.volkswagen-group.com](https://jobs.volkswagen-group.com/robots.txt), [volkswagen-karriere.de](https://www.volkswagen-karriere.de/robots.txt) and [HRD](https://www.hrd-portal.com/robots.txt) precede documents. Both official career homepages and their linked [career terms](https://www.volkswagen-karriere.de/de/nutzungsbedingungen.html) and [job-portal terms](https://jobs.volkswagen-group.com/content/Nutzungsbedingungen/?locale=de_DE) retain their previously reviewed complete material/frontier fingerprints. October 14 research confirmed all seven policy/home inputs unchanged. The May 2026 career and January 2026 portal terms prohibit commercial systematic use without consent; that commercial qualifier remains material. This adapter collects minimal original noncommercial factual recaps and direct official opportunity links, without creative description mirrors, images, applications, profiles or guessed APIs.

Every physical publisher attempt shares one durable family budget across endpoints, retries and redirects: starts are at least six seconds apart, completion-to-next-attempt spacing is also at least six seconds, and no more than ten starts occur in a rolling minute. Stronger native intervals, MAX embargoes, union starts and sticky refusal survive restarts. Mature missing or replaced state fails closed; no fresh state substitutes for known production history. Local collectors hold an exclusive family lock; overlapping production/research runs require coordination under the same allowance. Only 502/503/504 receive one bounded retry after at least 30 seconds and any stronger Retry-After. A 401/403/429 refusal is persisted before body reading. Caps remain 240 starts, 8 MiB per response and 64 MiB total. The absolute run budget remains 4,320 seconds, with collection at most 2,400 seconds, bounded publication/failure phases and 120 seconds reserved for pacing export.

The newest completed family attempt's validated inert pacing artifact is mandatory once any family history exists, including failed attempts. Missing/expired newest artifacts cannot trigger fresh bootstrap or older fallback. The original first-production bootstrap remains bound to the [issue #117 handoff](https://github.com/YouthOpps/data-pipeline/issues/117#issuecomment-6102086900), exhaustive unfiltered history and secure MAX/OR conservation. It applies only when history proves no completed family attempt exists. Artifact export requires healthy durably finalized state and a positive signal through the current step's fresh output file; stale state cannot be uploaded after failed completion persistence.

Every GitHub publication request obtains or renews a repository-scoped installation token through the configured App. The matching workflow discovers the `YouthOpps/data-source` installation using `actions/create-github-app-token@v2`, supplies its authoritative installation ID and preserves native JIT renewal; it does not pass the action's temporary token as a static fallback. Atomic source-scoped CAS writes affect only `datas/volkswagen-group/{data,metadata}.json`. Initial failure creates no folder; later failure preserves validated last-good data and success/check timestamps when safe failure metadata can be written. Creation/first-seen/last-seen use attempt start, last-checked uses successful end, and updated-at advances only on a material change. The daily schedule is staggered with other sources. Push routing, manual dispatch, repository/main guard and serialized family publication remain unchanged. Maintainer merge starts production; implementation does not dispatch Actions or publish.

Run the sole real nonpublishing test from the repository root:

```sh
python -B adapters/volkswagen-group/test_adapter.py
```

It retrieves and validates the entire nonempty current collection, emits its complete JSON on stdout and sends diagnostics to stderr. It needs no publication credentials or third-party packages. A copied adapter folder runs independently with the Python standard library. Live evidence must be consumed and validated in memory without writing dataset files. Publication behavior is reviewed without invoking production.

Issue: [#161](https://github.com/YouthOpps/data-pipeline/issues/161). Historical source and durable-state decisions: [#117](https://github.com/YouthOpps/data-pipeline/issues/117). Independent acceptance and maintainer post-merge publication/site validation remain required; research and nonpublishing tests do not claim production publication.

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

The scheduled refresh runs once daily at **00:10 UTC** (`10 0 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Inventory page continuations and partitions wait at least 12 seconds after the
previous inventory page finishes before fetching the next page in this process.
This includes taxonomy/listing pages where applicable. The existing native
transport still enforces its physical-request allowance across retries, redirects
and detail requests, with stricter publisher limits and retained backoff taking
precedence. The page guard does not raise the durable request interval for all
subsequent article/detail requests or reset existing budget/refusal state.
Inventory scope, parsing, validation and atomic publication are unchanged.

## Source display name

Published metadata uses `display_name` = **Volkswagen** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
