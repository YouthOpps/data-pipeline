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

The shared publisher allowance retains at most ten request starts per rolling minute, at least six seconds between starts across origins, redirects and retries, and a 60-second preparation floor. Secure family pacing state, collection locks, legacy migration, stronger limits, embargoes, refusal flags and trusted Action artifacts remain unchanged. Refusals and unsafe redirects stop collection. There is one bounded 503 retry, not a whole-collection retry.

## Validation and publication

```sh
python -B adapters/pl-frse/test_adapter.py
```

The sole live, nonpublishing test validates all actual records and emits the complete JSON on stdout, with diagnostics on stderr. Responses and records stay in memory. Checks in that same test perturb a late field of genuine retrieved HTML, verify freshness and distinguish full closing dates from unknown/projected/event/project dates. These checks do not substitute fixtures for real retrieval. The test receives no publication credential and writes no dataset files.

Preparation retains its absolute 1,800-second bound and request-start guard. Total execution remains bounded to 2,190 seconds, with separate publication/failure phases and reserved pacing export. An incomplete or oversized frontier fails rather than publishing partial data.

Production remains `python -B adapters/pl-frse/adapter.py --publish`, atomically publishing only `YouthOpps/data-source/datas/pl-frse/{data,metadata}.json` with conflict detection. First failure creates no folder. Later failure preserves last-good records and success timestamps, updating failure metadata only for an unchanged validated pair. The matching workflow, bootstrap and pacing-artifact handling are unchanged. Tests do not exercise publication; production Actions are not used as tests. Independent review is required before PR handoff.

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
