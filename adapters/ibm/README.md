# IBM early-career programme information

This adapter discovers early-career programmes from the official [IBM careers site](https://www.ibm.com/careers). It follows the current entry-level hub, every card in its "Our programs" section, public next-page links, and programme links in the substantive article bodies. Titles, eligibility, funding statements, programme features, durations and tracks come from the current HTML, not stored profiles or expected page hashes. The currently advertised programmes are internships, apprenticeships, co-op, the Entry-Level Program and Sales Accelerator; this is observed coverage, not a fixed count.

Programme overviews have unknown availability and no inferred closing date. Only explicitly labelled application closings produce deadline information. Complete ISO or English day/month/year dates can populate `deadline`; partial, unknown or conflicting dates remain null with the original closing statements retained in `deadline_text`. Every closing clause must resolve before a single deadline is asserted, so an unknown second-track closing keeps the combined deadline null. Event dates, project starts and article dates are not application deadlines, including when a project start shares a sentence with an application closing. These pages do not establish a worldwide vacancy inventory.

## Programme boundaries

- Internships: degree-pursuing students, real project work, mentoring and learning resources. The supporting co-op comparison describes 12-week summer internships; this is not a universal duration for all global internships. Employee testimonials do not establish current host countries or applicant eligibility.
- Apprenticeships: full-time earn-and-learn for people without a four-year bachelor's degree in the field pursued who have domain knowledge. No numerical pay, universal programme duration or country-specific admission rules are asserted.
- Co-op: 16 weeks, full-time and on-site at an IBM office, for enrolled college/university students in software or sales. A subsequent permanent role depends on performance and an offer.
- Entry-Level Program: global structured onboarding, training, certifications, hands-on work and mentoring, for recent graduates, career switchers and nontraditional backgrounds. The less-than-one-year experience description is typical, not a universal hard cutoff. Named occupational areas are not separate invented vacancies.
- Sales Accelerator: fresh graduates, two tracks and a two-year growth plan. The Sales track explicitly includes an assignment in Valencia, Spain, or Dublin, Ireland; these physical destinations do not imply citizenship eligibility or the location of every programme role. Client Engineering and Sales remain one advertised programme, not cloned vacancy calls.

The US publisher country identifies IBM, not a passport requirement. All nationality eligibility lists remain empty; textual conditions are retained without inventing citizenship rules. Actual location, pay and selection conditions depend on individual vacancies that this source does not collect. Funding statements, including apprenticeship earn-and-learn, remain separate from numeric salary amounts that the pages do not establish. An empty funding list means no matching explicit statement was found, not that the programme is unpaid.

## Discovery and access

The current discovery graph retrieves nine HTML pages: careers homepage, terms, entry-level hub, internship hub, and five substantive programme articles. The internship explanatory article enriches the existing internship record; it is not a second offer. Its separately scoped summer-duration comparison comes from the current co-op article. The Sales Accelerator article is discovered from the entry-level guide and retains both tracks within one programme. No form is submitted. There is currently no programme-index pagination; literal next links are followed when present, and advertised pagination without a usable next link fails closed. Careers advice, employee stories, generic occupational descriptions, learning directories, external events, talent-network registration and job alerts are excluded.

The parser requires IBM's programme-section and article-body structure and fails on missing substantive coverage. Substantive programme-hub paragraphs also feed eligibility, funding and deadline extraction; navigation, mastheads, footers, breadcrumbs and testimonial containers are excluded. Dynamic requests stay on the official `www.ibm.com/careers` HTML route, excluding vacancy search, plus the legal and robots routes. Publisher redirects remain subject to route checks, robots and the shared budget. PDF-only conditions and external application systems are not parsed, and no claims of complete PDF or vacancy coverage are made. The collected HTML programme information remains available without treating generic directories as job openings.

IDs retain the existing `ibm-` plus first 24 SHA-256 characters of `ibm|<programme URL>` algorithm. Titles can change without changing IDs; no title migration was needed. Each record includes source document URLs and current content digests. These are change evidence, never acceptance pins. Article content and links beyond the introduction affect material comparison and `updated_at`. Complete extracted eligibility, funding, duration and track statements remain in dedicated fields when the 600-character summary cannot contain them all.

The public vacancy-search page rendered an empty client-side shell. Its declared API's robots route redirected to the homepage, so no usable API policy or complete vacancy pagination was established; the API was not called. This is a limitation of the separate vacancy-inventory route, not a claim that all IBM access is prohibited. No guessed API, private applicant route or external ATS substitution is used.

The actual [IBM robots policy](https://www.ibm.com/robots.txt) permits the reviewed careers and legal paths. The [terms](https://www.ibm.com/legal/terms), reached through the actual careers footer, expressly permit robots-compliant crawling. The reviewed scope is noncommercial original minimal facts and official links; this is not a blanket licence to reproduce creative content, images, visitor information or submitted tools.

## Timing, failure and publication

The preserved `youthopps-ibm-careers-publisher-v1` family coordinates all publisher requests, robots, retries and redirects: at least six seconds between starts and at most ten starts in any rolling minute. Existing embargoes, refused-access state and timing history are retained. A sixty-second startup embargo applies after restoration under the exclusive collector lock. Each physical attempt, including failure and response closure, persists its completion plus the stricter native interval. A monotonic gate protects the actual opener from reservation and filesystem timing variation. Request count follows the discovered graph. Hard bounds are 100 physical starts, 8 MiB per response and 64 MiB across publisher response bodies. Known HTTP 401/403 refusals retain block state before body reading; ordinary 429 responses persist timed adaptive backoff before their bounded error body is read; unreviewed redirects stop before following their destination.

True POSIX alarms enforce a single 1,290-second run: prepublication at most 900 seconds, publication and conditional failure handling at most 180 seconds each, with the final 30 seconds reserved for trusted timing export. Nonexport work is clipped at the original 1,260-second deadline. Socket timeouts do not replace the wall-clock bound. Failure reconciliation protects a newer atomic data/metadata pair. A hard process kill cannot guarantee artifact export.

The workflow has one application command, `python -B adapters/ibm/adapter.py --publish`, a thirty-minute job limit, one restricted data-source App token minted immediately before collection and a queued family concurrency group. The workflow includes the concrete first-family bootstrap recorded in the [reviewed history and pacing handoff](https://github.com/YouthOpps/data-pipeline/issues/120#issuecomment-6101430692); it applies only when exhaustive history finds no previously completed family attempt and cannot override a refusal or stronger interval. Once family history exists, the newest completed attempt's validated inert artifact is mandatory, including after failure; a missing artifact never permits fresh bootstrap or an older fallback.

Publication changes only `datas/ibm/data.json` and `datas/ibm/metadata.json` atomically. Before first success, failure creates no source folder. Later failure preserves the validated last-good data and its success/check timestamps when safe metadata publication is possible. Source metadata credits IBM and clearly describes the programme-information scope.

New records use attempt START for creation, first-seen and last-seen, and successful END for last-checked. Material changes advance updated-at to END; unchanged records retain their earlier updated-at and original creation/first-seen values. Tests never receive publication credentials or publish.

## Validation status

The sole live test is `python -B adapters/ibm/test_adapter.py`. It rejects `DATA_SOURCE_TOKEN` before collection, validates every real record and emits the complete actual JSON only on stdout; diagnostics go to stderr. There are no fixtures, publication calls, dataset files or fixed record-count assertions. Read-only `GITHUB_TOKEN` handling for workflow pacing restoration is unchanged. Run the test sequentially with any other IBM collection, preserving its durable pacing state.

Issue #140 author checks compared all five legacy identities with the dynamically discovered inventory: none removed or added. Genuine research responses were retained only in process RAM for parser checks, then discarded before the fresh final live collection. Late article and hub eligibility/funding changes advanced `updated_at`; unchanged records retained it. Partial closing dates, event dates and project starts did not fabricate deadlines. Review corrections cover unresolved second-track timing, a project start in the same sentence as a closing, and hub-only eligibility/funding/deadline statements while excluding navigation. Independent acceptance and the combined issue PR remain separate handoff gates; production and website delivery require the maintainer workflow.

Timing artifacts are uploaded only after this run validates and durably finalizes a fresh healthy envelope and signals readiness through its fresh step output. A failed publisher completion-state save cannot export prior state; owned stale artifacts are invalidated before any new export.

## Expired-record cleanup

The source does not establish a reliable application timezone for every date-only deadline. Cleanup therefore uses UTC−12 as a conservative certainty bound: a calendar deadline expires only once its closing date has ended in every civil timezone. This does not invent a publisher closing clock. Offset-aware deadlines use their explicit instant.

After complete nonempty collection and validation, the refresh removes only
records with a validated deadline strictly before the successful check time.
Missing or ambiguous publisher deadlines remain normalized as null; status, age
and disappearance are never used as expiry evidence. Invalid normalized dates
fail validation and preserve last-good data. The source uses complete replacement rather than merging
old records, so expired previously published rows cannot survive a successful
refresh, including when they are still present in the publisher response.
Creation and first-seen history is retained for refreshed eligible identities.

The retained subset and success metadata are published in one source-scoped
atomic commit. A validated nonempty raw collection whose records all expire
publishes `[]` with success and a zero count; an empty upstream collection still
fails. A previously successful empty subset remains a valid last-good snapshot.
Fetch, parse, validation and publication failures preserve last-good data and
its success timestamp; only safe failure metadata may change. The sole live test
continues to emit the complete unfiltered publisher collection without publishing.

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

The scheduled refresh runs once daily at **13:39 UTC** (`39 13 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Inventory hub pages and their continuations wait at least 12 seconds after the
previous inventory page finishes before fetching the next page in this process.
The existing native transport still enforces its physical-request allowance
across retries, redirects and detail requests, with stricter publisher limits
and retained backoff taking precedence. The page guard does not raise the
durable request interval for subsequent detail requests or reset budget/refusal
state. Inventory scope, validation and atomic publication are unchanged.

## Source display name

Published metadata uses `display_name` = **IBM** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Conditional rate-limit and state maintenance (#220)

The normal collector, factual parsing, validation, default six-second physical
spacing, ten-start rolling limit, twelve-second inventory page guard, source
identity, collection/job/token caps and workflow schedule are unchanged. This
scoped maintenance does not certify those legacy default limits against the
current twelve-second/five-start/thirty-second policy. No new publisher crawl,
source access release, workflow activation or production result is claimed.

A future ordinary HTTP 429 stops the current run. Before reading at most 64 KiB
of error evidence, retain at least 1,800 seconds of cooldown, doubling on each
new observed 429, or any longer valid Retry-After/known embargo. Double the
**current** retained physical and inventory intervals with minimums of twelve
and thirty seconds. Both intervals, the observed new-event count, typed
historical-unknown flag and embargo survive failure/export/restoration.
Completion of the error read retains the stricter physical/page completion guard
too. Existing stronger constraints remain MAX. Unusable negative, zero, stale,
malformed or out-of-range 429 timing is not a new access hold, on any status.
Genuine content refusals remain independent hard holds; valid longer timing is
retained on every status.

The optional schema-1 rate fields extend trusted older state without changing
its family or clearing its blocked flag, starts, interval or embargo. A missing
new-event count means no events observed **since this change**, not proof that
no historical 429 occurred. The durable `legacy_rate_history_unknown` boolean
defaults to true for old/missing state and merges with logical OR through
restore/export, so a later process cannot mistake zero new events for known
complete history. Unknown or mixed old holds remain held and require separate
provenance review. Counts beyond the finite 0–100 range fail closed; a retained
count of 100 stops before another source request. Increased gaps may exhaust
unchanged collection limits; a later complete run is not guaranteed.

Only the new 429 error path uses a source-local visible-text reader. A genuine
visible human-verification/CAPTCHA challenge or genuine challenge HEAD title
retains a hard hold as well as the timer. Ordinary rate-limit wording, hidden
content, scripts, unrelated footer/navigation/aside notices and a fake body
TITLE do not alone create a hold. The normal source DOM parser is unchanged. A
body-read failure retains the saved timer and makes no same-run retry.

Hosted restoration requires the current run with exact workflow/source/head and
attempt identity. Every completed job must explicitly name the main branch.
Completed attempts are ordered by authenticated exact-attempt
job completion, including the previous attempt behind a queued older-ID rerun
and the current run's previous attempt. Missing/partial jobs, mismatched heads,
ambiguous completion ties or absent current runs fail closed. Mutable run
updated_at is not completion evidence. Restore the newest applicable exact owned
artifact, verify run/head/digest/envelope/source/schema, and MAX merge all
restrictions; a missing newest artifact never permits an older fallback or a new
allowance. Existing first-family bootstrap cannot replace mature state.

Validation reuses exact genuine successful native evidence for unchanged factual
behavior; it does not claim a fresh native collection. The then-current record
consumer and dependency are byte-identical to the current consumer. Current
actual source records and metadata pass complete RAM loadData checks, with
actual filtering, derived expiry/empty controls and last-good failure
preservation separately identified. Original complete native stdout was not
saved or reconstructed from later publication. Actual mature owned ZIP state was
restored through the real downloader/decoder into isolated temporary state; no
live publisher family state was written. Error/chronology controls are inert and
are not historical publisher responses or measured physical network gaps.

IBM currently emits five standing programme overviews with unknown availability
and null deadlines. Its source validator correctly rejects invented dated
application deadlines. Generic dated helper controls and a valid empty consumer
envelope do not establish a real all-expired IBM collection; no such event is
claimed. All five current actual records are replayed through the consumer.

Content HTTP 451 retains a hard hold independently of Retry-After. A literal
robots.txt HTTP 451 is unavailable REP, without clearing an old hold; this
narrow status correction does not claim a complete legacy robots migration.
