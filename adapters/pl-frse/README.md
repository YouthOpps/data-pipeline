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

The shared publisher allowance retains at most ten request starts per rolling minute, at least six seconds between starts across origins, redirects and retries, and a 60-second preparation floor. The same secure family pacing namespace, collection locks, legacy migration, stronger limits, embargoes and refusal flags are preserved. Trusted Action state now also retains future adaptive 429 restrictions. Refusals and unsafe redirects stop collection. There is one bounded 503 retry, not a whole-collection retry.

## Validation and publication

```sh
python -B adapters/pl-frse/test_adapter.py
```

The sole live, nonpublishing test validates all actual records and emits the complete JSON on stdout, with diagnostics on stderr. Responses and records stay in memory. Checks in that same test perturb a late field of genuine retrieved HTML, verify freshness and distinguish full closing dates from unknown/projected/event/project dates. These checks do not substitute fixtures for real retrieval. The test receives no publication credential and writes no dataset files.

Preparation retains its absolute 1,800-second bound and request-start guard. Total execution remains bounded to 2,190 seconds, with separate publication/failure phases and reserved pacing export. An incomplete or oversized frontier fails rather than publishing partial data.

Production remains `python -B adapters/pl-frse/adapter.py --publish`, atomically publishing only `YouthOpps/data-source/datas/pl-frse/{data,metadata}.json` with conflict detection. First failure creates no folder. Later failure preserves last-good records and success timestamps, updating failure metadata only for an unchanged validated pair. The matching workflow and bootstrap are unchanged; the stricter newest-attempt artifact rules below apply. Tests do not exercise publication; production Actions are not used as tests. Independent review is required before PR handoff.

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
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. Native
pacing, backoff and durable budget identities remain unchanged. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **07:45 UTC** (`45 7 * * *`).
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

FRSE's prior genuine complete native collection contained sixty records; its
current published snapshot contains fifty-four. The original sixty were fully
validated by the identical record validator at collection time. This change
replays the actual current fifty-four through complete loadData; it does not
claim that all sixty original native records are still retained for replay.

Content HTTP 451 retains a hard hold independently of Retry-After. A literal
robots.txt HTTP 451 is unavailable REP, without clearing an old hold; this
narrow status correction does not claim a complete legacy robots migration.
