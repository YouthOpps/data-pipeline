# Australian National University international admissions

The adapter indexes the two reviewed 2027 direct international coursework intakes from ANU's [official international applications page](https://study.anu.edu.au/apply/international-applications). The publisher's original public robots response admitted this route for the YouthOpps bot and retained legacy product token. The reviewed page publishes factual application information without an observed applicable automation or indexing restriction.

IDs contain the source slug, intake year and semester. Each record uses the published date-only application closing date and links to the ANU application guidance. The source provides no closing time or opening dates for these intakes. Programs with additional selection criteria can set different dates, and offer acceptance has a separate deadline.

The direct route excludes Australian and New Zealand citizens and holders of Australian permanent residency or a permanent humanitarian visa. ANU routes international applicants taking Australian Year 12, the International Baccalaureate Diploma or New Zealand NCEA through UAC. The adapter covers the two reviewed direct intake panels. Degree-level listings, UAC, submission portals and future intake years fall outside this reviewed inventory.

Each run reads robots rules before collecting the single calendar page. The page provides no reviewed change feed or incremental boundary, so the adapter verifies both intake panels on each successful refresh. It stops when publisher access changes, pacing ownership fails, or the page stops identifying both reviewed dates. A failed refresh preserves prior records and their success timestamp. A successful refresh applies date-only expiry before publishing its source data and metadata atomically.

GitHub Actions runs `python -B adapters/au-anu-international-admissions/adapter.py --publish` for default-branch production. The isolated nonpublishing QA workflow runs the sole live test in `test_adapter.py` with the same collection path. The owned pacing artifact carries forward the previous authorized attempt's state.
