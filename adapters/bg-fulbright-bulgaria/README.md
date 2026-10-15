# Fulbright Bulgaria

This standalone standard-library adapter discovers the Bulgarian-American Commission for Educational Exchange’s own awards and structured training programmes. Its primary source is [the research-grants directory](https://www.fulbright.bg/en/research-grants-for-bulgarians/). Current source research on 14 October 2026 covered 43 complete official pages: the home and applicant directories, every supported programme leaf, the two-page conference inventory, current news cards, and both language versions of the privacy policy.

The current collection has 39 genuine opportunities: the 37 existing award/applicant tracks and dated training editions, the newly opened [Opportunity Funds competition](https://www.fulbright.bg/en/opportunity-fund-bulgaria/), and the [structured EducationUSA virtual advising programme](https://www.fulbright.bg/en/educationusa-virtual-group-advising-program/). The latter is training with a weekly curriculum and participation requirements, classified as a programme overview rather than a scholarship. Scholarship capacity, course modules, country examples, grantee stories, orientation and enrichment for already selected participants, advisory provider lists and general one-to-one advising do not create opportunities.

The homepage changed after the original 9 October research: the 2027 Opportunity Funds competition opened on 12 October and closes on 6 December 2026. The previous combined homepage fingerprint blocked collection before extraction and omitted this new grant. All 39 other original reviewed pages still matched the prior material evidence during diagnosis. The implementation now uses current labelled programme and track blocks, rather than whole-page opportunity fingerprints or fixed editorial narratives. Numeric benefits, applicant citizenship, supported complete application dates and edition schedules come from their own source blocks. Summaries are independently written English facts; `summary_language` is `en` even for Bulgarian original pages. Unsupported or ambiguous facts remain unspecified.

Discovery includes normal links, menu routes and the publisher’s inert `et_link_options_data` JSON for Divi cards; it never executes JavaScript. Exact reviewed aliases map to the actual programme leaves. Both conference listing pages must remain complete, and new unsupported programme routes, award sections, course editions, conference pages or current news calls fail the collection for review. The news check covers current cards, not every historical news story. Personal biographies, testimonials, galleries, contact forms and page presentation are not opportunity facts. Changing an ordinary contact-form arithmetic question no longer invalidates the opportunity inventory; no question is answered and no form is submitted.

Stable IDs remain the SHA-256 prefix of the source slug and the existing applicant-track identity. Separate Schuman variants, four US graduate awards, core/Flex scholar benefits, individual/host Specialist and teaching-assistant roles, three alumni award types and four actual paid course editions retain their identities. Amounts are taken from the relevant benefit or edition block: monthly maintenance, total funding caps and course fees remain distinct; core benefits are not transferred to Flex, and one course edition’s fee is not transferred to another. A new or ambiguous field shape fails instead of supplying a stale amount.

Citizenship is projected only from an explicit source requirement. Affiliation, venue, a Bulgarian publisher or participant origins do not establish nationality. Opportunity Funds accepts Bulgarian citizens **or recognized refugees** enrolled full-time in Bulgarian schools in grades 10/11; therefore its eligible-country array stays empty. Its summary preserves financial need, English/academic requirements, the exclusion of US citizens/permanent residents, and the absence of any guarantee of university admission or financial aid. The journalist page’s Bulgarian-citizen banner conflicts with a broader origin-country list, so its array remains empty. The ETA dual-citizen wording stays qualified. Institutional hosts have no inferred passport condition.

Only complete dates from an explicit programme application-closing slot become deadlines. Event dates, degree-proof dates, interview/nomination milestones and university admission dates do not. Yearless and ambiguous closing dates remain null. Historical dated closings remain historical even when a stale open banner appears. A homepage Leaders card has a February 2026 date while the leaf describes another academic round and a month-only February 2025 comparison entry; the adapter does not combine these conflicting rounds into a new leaf deadline. Archived US graduate pages and historical seminars do not establish current application openings.

## Access and rights

Requests use the identifying YouthOpp user agent, inherited proxy and verified TLS. Native robots checks apply to every endpoint and redirect. Requests, robots, retries and redirects share the existing publisher/apex/www pacing allowance: at least six seconds between starts and at most ten starts in every rolling 60 seconds. A native file lock serializes overlapping local callers and retains Retry-After state. Production runs are serialized; independent machines must not overlap publisher requests. Access refusals, challenges, unsafe redirects, incomplete HTML and disallowed endpoints fail the collection.

Publisher robots disallows PDF requests. No PDF, alternate-path bypass, applicant form submission or private application data is used. Course HTML supplies the facts; an external exam-information document is unnecessary.

Legal protection is independent of mutable opportunity text and navigation. Exact reviewed English/Bulgarian copyright footers, substantive rights notices and policy destinations are checked on every page, including Divi footer links. Both complete privacy-policy articles have separate material-text guards. Only the copyright end year is normalized. A changed legal footer, notice, privacy article or new terms/policy destination fails closed. The policies were updated on 16 March 2026 and concern personal-data handling; they grant no open content licence. Output contains independently written factual discovery summaries, attribution and original public URLs. Publisher prose, images, contacts, applicant information and grantee stories are not republished.

## Run and publication

Run the single actual nonpublishing live test from this folder:

```sh
python -B test_adapter.py
```

It retrieves the complete supported source, validates every nonempty actual record and emits the entire collection as one JSON document on stdout; diagnostics go to stderr. It writes no collected-record file and uses no publication credential. Validate the entire output in memory. Source retrieval, parsing or validation failure produces no partial successful collection.

The matching workflow runs on relevant pushes to main, manual dispatch and its existing schedule. Its application command is only `adapter.py --publish`. A source-scoped GitHub App token authorizes publication of `datas/bg-fulbright-bulgaria/{data,metadata}.json` in `YouthOpps/data-source`. Data and success metadata are replaced atomically; a sibling-source ref race is retried without force, while a newer same-source snapshot is preserved. Tests do not invoke Actions or publication. Live test success certifies local collection, not production publication or website delivery.

## Expired-record cleanup

The complete fresh nonempty collection is validated before filtering. Successful publication replaces the source dataset rather than merging old rows, so expired previously published entries cannot survive that successful replacement. No other source is changed.

Offset-aware deadlines expire only after their instant. A date-only closing with no documented publisher timezone stays eligible through that date in every civil timezone; cleanup uses the conservative UTC-12 calendar boundary. Missing or ambiguous dates, record age, absence and an expired status alone do not justify expiry.

All expired records from a valid nonempty refresh may leave an empty dataset. That empty result and success metadata are published atomically, and subsequent snapshot reads accept it. Empty upstream retrieval remains a failure. Fetch, parsing, validation and publication failure preserve the last-good dataset and success timestamp, including a previous empty success. First-run failure creates no published source folder. Failure metadata is source-scoped and does not claim durable success when metadata publication failed.

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

The scheduled refresh runs once daily at **02:56 UTC** (`56 2 * * *`).
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

## Workflow concurrency

`youthopps-bg-fulbright-bulgaria-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Fulbright Bulgaria** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
