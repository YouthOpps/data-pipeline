# Fulbright Hungary

Source: `hu-fulbright-hungary`, published by the Hungarian-American Fulbright Commission in Hungary. This standalone adapter uses Python 3.12 or later and the standard library only.

## Official access and attribution

Collection reads the public [award homepage](https://fulbright.hu/), [short-term grants](https://fulbright.hu/special-short-term-grants/) and [U.S. awards](https://fulbright.hu/fulbright-awards/for-us-citizens/), plus linked current Hungarian and joint university award pages. The [robots policy](https://fulbright.hu/robots.txt) reviewed on 8 October 2026 permits access with an empty `Disallow` rule. The official navigation and footer expose no separate terms or reuse policy; ordinary copyright is present. This supports the reviewed public collection route, not a claim of unrestricted redistribution rights. Records preserve publisher attribution, original links and classification evidence. Application forms and external award websites are linked, never crawled or submitted.

The adapter identifies itself as YouthOpp, honors robots and Retry-After, and starts at most ten requests per rolling minute with at least six seconds between requests. Redirect destinations are checked against robots. Local overlapping runs share locked pacing state outside the checkout; production publication is serialized by the workflow. Do not overlap local collection with a production run on another machine.

## Opportunity inventory and exclusions

The latest live inventory contains ten genuine records; this is evidence, not
a required count:

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

Hungarian deadlines retain the existing end-of-Budapest-day normalization in
UTC. English short-term dates without a clock remain date-only; those calls
remain visible throughout their Budapest closing day with unknown status.
Joint award deadlines inherit the current lecturer call's deadline. Rolling
applications have no invented deadline. The U.S. Student Program's approximate
April-to-mid-October window remains in its summary; its exact deadline is null,
boundary-month status is unknown, and it is excluded outside the published
months. Publication and modification times are not inferred from unrelated dates.

## Run and test

From the repository root:

```sh
python -B adapters/hu-fulbright-hungary/adapter.py
python -B adapters/hu-fulbright-hungary/test_adapter.py
```

The sole live test performs real non-publishing collection, validates every record and emits the complete JSON result on stdout with diagnostics on stderr. It receives no publication token and writes no output files. The folder can run independently when copied outside the repository.

On 8 October 2026, author and independent live tests validated all ten records in memory, including unique identities and URLs, publisher evidence, countries and dates. Later collection may legitimately change as calls open or expire.

## Publication

The matching `fetch-hu-fulbright-hungary.yml` workflow runs automatically on pushes to `main` affecting this adapter folder or its own workflow, including maintainer merges. Manual dispatch and a six-hour schedule (`29 0/6 * * *`, UTC) remain available. Repository/main guards, shared publication concurrency and the existing GitHub App's data-source-only contents-write token scope remain enforced.

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
Date-only deadlines remain eligible through their closing date in Europe/Budapest.
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
