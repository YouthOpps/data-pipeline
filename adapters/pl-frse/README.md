# FRSE public programmes and calls

Standalone `pl-frse` adapter for [Fundacja Rozwoju Systemu Edukacji](https://www.frse.org.pl/), the Polish Erasmus+ and European Solidarity Corps national agency.

## Discovery and coverage

Each collection exhausts the advertised pages of the public [funding](https://www.frse.org.pl/finansowanie-projektow), [events](https://www.frse.org.pl/wydarzenia-i-szkolenia) and [competitions](https://www.frse.org.pl/konkursy) catalogues using their public history queries. Repeated, empty or excessive pagination fails collection. Funding and event details, current homepage call announcements, related call announcements and linked HTML rules are read dynamically. The general news archive and external aggregators are not enumerated.

Jobs are separated by actual FRSE recruitment reference. Named competition anchors become separate opportunities, including the four [Erasmus+ anniversary calls](https://erasmusplus.org.pl/konkursy-40-lat-erasmus), with their independently applicable conditions and dates. Supporting catalogues and regulations are attached to actual calls rather than published as duplicate opportunities. Nomination-only awards, winner pages, charity activities and generic celebrations are excluded with diagnostics. The two available WorldSkills HTML overviews are retained with explicit external-condition limits; no WorldSkills origin is accessed.

Full substantive HTML stays in process memory. `details` publishes categorized short attributed factual extracts, field labels, official links and a digest of complete material, including later conditions. It does not reproduce full articles or rulebooks. Changes beyond the introduction affect the digest and semantic freshness; matching factual clauses also affect extracted fields. Summaries are original templates, not publisher introduction excerpts. Discovery and validation impose no fixed opportunity count. Extracts remain attributed publisher text; no new licence or comprehensive reuse permission is claimed.

## Dates and identities

- Existing IDs hash `pl-frse|<canonical URL>`. Jobs append the literal `FRSE/<number>/<year>` reference; named suboffers include the publisher's fragment. The solidarity-project supporting-organisation view is attached to the original young-group identity, whose canonical page always supplies the title and primary fields.
- Only explicit complete application closing dates are normalized. Partial/yearless dates, event dates, project periods, result announcements and projected schedules do not fabricate deadlines. Multiple different closings stay in `application_dates`, with a null universal deadline.
- Field labels remain part of date context: project duration/completion fields, including BCU's project completion date, cannot supply an application deadline. Recurring course catalogues, annual events, schedules and links to historical editions are classified as programme overviews from their current HTML evidence, not promoted to dated calls.
- Unzoned public-call clocks remain date-only. No timezone is invented; a date-only closing is not expired on that day. Future dates alone do not establish open status. Future closure wording does not establish completed closure. Participant overviews do not inherit the institution's grant closure.
- Publisher country remains Poland. Residence, institution location and affiliation are not passport-nationality filters. Unknown destination and nationality eligibility remain unspecified.
- PDF links are retained, but their contents are not parsed or reproduced. PDF-only conditions and deadlines remain unverified. HTML overview amounts and conditions are source-reported and may be superseded by governing guides; former manually reviewed PDF facts are not silently treated as current. Each record states these limits.
- Successful refresh preserves `created_at`, `first_seen_at`, and unchanged semantic `updated_at`. Every record receives the same collection-completion `last_checked_at`.

### Governing conflicts and migration

Four historically identified conflicts remain explicitly unresolved, not corrected using frozen facts: VET staff courses (overview 2–30 versus previously reviewed guide 2–10 days), EMDM (overview EUR 55,000 versus guide EUR 60,000), Jean Monnet policy networks (Canada/USA versus EU–India), and EMJM's omitted residence-country mobility conditions. Each affected record carries `source_conflicts`, an explicit warning that its overview is not governing evidence, and current guide links discovered from the official documents index. The adapter does not claim that the historical guide facts remain current.

Compared with the former 53 manually summarized records, HTML-only extraction deliberately leaves PDF-only deadlines, benefits and eligibility unverified, does not infer destination countries, and replaces hand-authored factual summaries with live factual extracts and templated summaries. Governing conflicts are not silently resolved in favour of HTML. WorldSkills additions are only FRSE overviews: their record-level limits explicitly leave dedicated eligibility, costs and recruitment dates unverified. No current dedicated-rule verification is claimed.

## Access and reuse

Requests use the six previously reviewed official origins: `www.frse.org.pl`, `erasmusplus.org.pl`, `eks.org.pl`, `etwinning.pl`, `selfieplus.frse.org.pl`, and `eduinspiracje.org.pl`. Robots checks precede access. Privacy/cookie policy guards remain separate from changing opportunity facts; changed legal text or applicable links still require review. Missing or ambiguous legal containers fail closed. Navigation, footers and newsletter forms outside those containers are not access-policy inputs. Privacy notices are not content licences. Records retain source links and FRSE attribution; no photographs, PDFs, applicant submissions or completed forms are published. No permission request or other message was sent.

The scoped policy migration used eight genuine responses that first matched all previous reviewed whole-page hashes. Within the unique `main`, legal selectors are `section.text_desc_box` (eTwinning), `section.standard_text_page` (Erasmus+), `section.text_page` (EKS, Selfie+, EDUinspiracje), and `div.split_view` (FRSE). FRSE's latter container includes the adjacent programme-specific privacy links, not just its `section.about` prose. Scoped hashes bind complete legal text and literal links; no blind whole-page fingerprint refresh was used.

The shared publisher allowance retains at most five physical request starts
per rolling minute, at least 12.2 seconds between starts across origins,
redirects and retries, and a 60-second preparation floor. The same secure
family pacing namespace, collection locks, legacy migration, stronger limits,
embargoes and refusal flags are preserved. Trusted Action state now also
retains future adaptive 429 restrictions. Refusals and unsafe redirects stop
collection. There is one bounded 503 retry, not a whole-collection retry.

## Validation and publication

```sh
python -B adapters/pl-frse/test_adapter.py
```

The sole live, nonpublishing test validates all actual records and emits the complete JSON on stdout, with diagnostics on stderr. Responses and records stay in memory. Checks in that same test perturb a late field of genuine retrieved HTML, verify freshness and distinguish full closing dates from unknown/projected/event/project dates. These checks do not substitute fixtures for real retrieval. The test receives no publication credential and writes no dataset files.

Preparation retains its absolute 1,800-second bound and request-start guard. Total execution remains bounded to 2,190 seconds, with separate publication/failure phases and reserved pacing export. An incomplete or oversized frontier fails rather than publishing partial data.

Production remains `python -B adapters/pl-frse/adapter.py --publish`,
atomically publishing only
`YouthOpps/data-source/datas/pl-frse/{data,metadata}.json` with conflict
detection. First failure creates no folder. Later failure preserves last-good
records and success timestamps, updating failure metadata only for an
unchanged validated pair. The matching workflow is unchanged; its old
bootstrap value grants no fallback for this mature family. The newest-attempt
rules below apply. Tests do not exercise publication; production Actions are
not used as tests. Independent review is required before PR handoff.

## Expired-record cleanup


Successful refreshes validate the complete fetched inventory before removing
records with verified past application deadlines. Offset-aware timestamps are
compared as instants; equality with the closing instant remains eligible.
No official timezone is established for date-only deadlines. Cleanup waits
until the date has ended worldwide (UTC−12), a conservative certainty bound,
not an inferred publisher timezone. Explicit publisher offsets take precedence.
Missing deadlines, ambiguous deadlines represented as null, status labels,
record age and absence from a response are never expiry evidence. The existing
publication contract replaces the complete source inventory; old records are
not merged back into the replacement. Consequently expired old records cannot
survive a successful replacement, including when rediscovered with a verified
past deadline. Current records with missing deadlines remain eligible.

The filtered data (including an empty subset after valid nonempty retrieval)
and success metadata are committed atomically only
under this source's paths. An empty/invalid raw response or fetch, parse,
validation or publication failure preserves last-good data and its success
timestamp; safe failure metadata may be updated. Tests never publish.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the
advertised product token. Applicable current and legacy product groups must
both permit access; restrictive rate and delay directives remain effective.
The physical/page limits are described below; backoff and durable family
identities are retained. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **07:45 UTC** (`45 7 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

All advertised catalogue pages, homepage call discovery and the official
document index wait at least 30 seconds after the preceding physical inventory
response completes. Redirect hops and the existing same-page transient retry
retain this purpose and wait; a 429 never makes another same-run source
request. Cached documents make no physical request or completion update.
Separate detail, policy and robots requests retain the physical floor without
a permanent 30-second detail interval. Stronger retained page gaps take
precedence; the first inventory clock after restoration conservatively covers
that gap across restarts. Complete discovery, parsing, validation and atomic
publication remain unchanged.

## Workflow concurrency

`youthopps-frse-publisher-v1` serializes runs of this source. Up to 100 pending
runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **FRSE** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Conditional rate-limit and state maintenance (#220)

Issue #220 changed conditional rate-limit handling and state restoration while
retaining the older normal pacing floors. Issue #224 replaces those floors
with 12.2 seconds, five starts per rolling minute and a 30-second physical
inventory completion guard. Source identity, factual parsing, validation,
complete frontiers, collection/job/token caps and daily schedules are
retained.

A future ordinary HTTP 429 stops the current run. Before reading at most 64
KiB of error evidence, retain at least 1,800 seconds of cooldown, doubling on
each new observed 429, or any longer valid Retry-After/known embargo. Double
the **current** retained physical and inventory intervals with minimums of
12.2 and 30 seconds. Both intervals, the observed new-event count, typed
historical-unknown flag and embargo survive failure/export/restoration.
Completion of the error read retains the stricter physical/page completion
guard too. Existing stronger constraints remain MAX. Unusable negative, zero,
stale, malformed or out-of-range 429 timing is not a new access hold, on any
status. Genuine content refusals remain independent hard holds; valid longer
timing is retained on every status.

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

The source-local visible-text reader also inspects bounded HTML before
redirect follow or ordinary response processing. A genuine visible
human-verification/CAPTCHA challenge or genuine challenge HEAD title retains a
hard hold as well as the timer. Ordinary rate-limit wording, hidden content,
scripts, unrelated footer/navigation/aside notices and a fake body TITLE do
not alone create a hold. The normal source DOM parser is unchanged. A
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

Issue #220 validation reused genuine successful native evidence for unchanged
factual behavior; those checks did not establish new physical pacing. The
then-current record consumer and dependency are byte-identical to the current
consumer. Current actual source records and metadata pass complete RAM
loadData checks, with actual filtering, derived expiry/empty controls and
last-good failure preservation separately identified. Original complete native
stdout was not saved or reconstructed from later publication. Actual mature
owned ZIP state was restored through the real downloader/decoder into isolated
temporary state; no live publisher family state was written. Error/chronology
controls are inert and are not historical publisher responses or measured
physical network gaps.

FRSE's prior genuine complete native collection contained sixty records; its
current published snapshot contains fifty-four. The original sixty were fully
validated by the identical record validator at collection time. This change
replays the actual current fifty-four through complete loadData; it does not
claim that all sixty original native records are still retained for replay.

Content HTTP 401/403/451 retains a hard hold independently of Retry-After. For
literal robots.txt retrieval only, every HTTP 4xx except 429 is unavailable
REP and creates no new content hold. Old true/unknown holds remain unchanged.
Robots purpose is checked on each physical hop; a redirect outside literal
`/robots.txt` is rejected before following it. Valid explicit robots rules,
stricter delays and rate restrictions remain effective.

## Physical pacing migration (#224)

The existing secure family path and exclusive collector lock retain all known
request history, embargoes, stronger physical/page intervals, observed new-429
counts, historical-unknown markers and genuine holds. Authentic older 6/12
state remains readable; effective floors are raised with MAX before access.
The bounded legacy ten-entry journal is retained, while the fifth-most-recent
start gates any new request when at least five are still inside a minute.
Completion gates protect actual transport starts, including failed reads and
redirects, rather than relying on reservations alone. New restrictions survive
failure and exact newest-owned-artifact restoration; no older fallback or
fresh allowance is introduced.

Bounded actual HTML is inspected before any redirect follow, including obvious
HTML with a missing or wrong MIME. Ordinary JS/JSON/PDF text is not
interpreted as HTML. The accepted visible-text algorithm excludes
hidden/style/script and unrelated chrome, and distinguishes informational
support wording from a real visible title or primary refusal. A genuine
challenge 429 retains both timer and hold; ordinary rate limiting stays timed.
Body-read failure retains saved timers. Factual HTML parsing and public record
fields are unchanged.

The existing hard collection/run/job limits and one-hour pre-minted App token
are unchanged. Larger complete frontiers, slow responses or stronger retained
backoff can exhaust a cap; such a run fails and preserves last-good data
rather than truncating, raising a cap or resetting state. Earlier
native/consumer receipts establish historical unchanged factual coverage, not
current 12.2/30 timing. This candidate requires independent pre-access review
and a separately released full current native test with complete RAM
response/stdout and consumer evidence before merge; no new native or
production result is claimed here.

### Local and hosted restoration

Every process restores fresh complete source workflow history and the newest
exact owned ZIP before any publisher request, using read-only `GITHUB_TOKEN`
access to Actions metadata/artifacts. Local collection requires all family
runs to be terminal; a queued or active attempt stops it. Hosted collection
retains mandatory current-run/attempt/head validation and exact completed
prior-attempt chronology. Missing mature history, a newer missing/expired
artifact or an identity/digest mismatch fails closed without bootstrap or
older fallback. The existing exclusive collection and state locks protect the
restrictive MAX merge into the original journal. A stronger hosted
hold/backoff therefore also blocks a later local test. Native tests need the
read-only state credential but never a publication token; they still run the
unchanged sole live test.

Local collection also requires the exact source workflow to be manually paused
by the maintainer. Its immutable workflow ID, own file path and
`disabled_manually` state are freshly checked before history and immediately
before the first publisher request after startup quiet. A changed/enabled
workflow stops before that request. This is a first-request coordination guard,
not per-page GitHub polling; maintain the pause for the complete local run.
Hosted workflow behavior and the daily schedule are unchanged.

A failed physical-completion persistence write blocks further source access and
refuses export. Any safely owned stale artifact is invalidated instead of
handing incomplete restrictions to the next runner. This error-only guard does
not change a successful collection or a healthy state export.

## Journal ownership maintenance (#295)

Publisher journal reads and writes retain the acquired original RW descriptor,
parent identity and file generation. They check that authority again before
transport and export. A changed name, generation, lock or descriptor fails the
publisher gate; cleanup never adopts or truncates the stronger replacement.
Serialization, write, fsync and release failures remain sticky for the process.
Cleanup attempts independent original descriptors and preserves a prior error.
Safe owned artifact output is invalidated before best-effort signaling or
unlink; foreign descriptors and unsafe existing output are preserved.

Journal updates now write through the retained, locked inode instead of
replacing it by pathname. This deliberately trades atomic journal availability
for original inode ownership: an interrupted update may leave an empty or
partial mature journal, which fails closed. It cannot be healed from an older
hosted artifact. The actual data publication still uses its existing atomic
GitHub transaction.

Only an actually newly created publisher namespace, during its existing verified
latest-owned restoration, can initialize neutral state for the incoming MAX
merge. An existing directory with missing or empty state fails closed. GitHub
publication transport does not establish publisher namespace creation proof.

This maintenance preserves source facts, frontier, dates, expiry rules, schemas,
seeds, history pins, pacing limits, retries, publication and workflow settings.
Validation uses actual functions with inert transport and private journals, plus
prior accepted native evidence for unchanged collection functions. It does not
certify a new crawl, production run or every legacy runtime contract. Artifact
output assumes the existing trusted private job/temporary-directory boundary; it
cannot authenticate an arbitrary concurrent same-UID writer forging output.

The earlier native acceptance remains historical evidence. This patch does not
claim a fresh full-body replay from expired RAM or a new publisher request.

Reference: https://github.com/YouthOpps/data-pipeline/issues/295

These existing schemas do not add a durable pending-transaction marker here.
If both the restrictive write and original-inode invalidation fail, a restart
still needs the separate #273 protocol correction. This journal-ownership patch
does not claim to resolve that broader historical persistence limitation.
