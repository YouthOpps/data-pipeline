# FRSE public programmes and calls

Standalone source `pl-frse` for [Fundacja Rozwoju Systemu Edukacji](https://www.frse.org.pl/), the Polish Erasmus+ and European Solidarity Corps national agency. The source keeps the publisher's Polish titles and original factual summaries: Polish for the existing catalogue and English for the four anniversary contests. No login, application form submission, applicant data, images or copied publication documents are collected or published.

## Access and reuse

The reviewed public funding, training, jobs and competition pages link the applicable programme rules. Collection checks robots for each of the six required official origins: `www.frse.org.pl`, `erasmusplus.org.pl`, `eks.org.pl`, `etwinning.pl`, `selfieplus.frse.org.pl` and `eduinspiracje.org.pl`. It also retrieves the reviewed [FRSE data policy](https://www.frse.org.pl/rodo), cookie policies and programme-specific policies. Privacy notices describe personal-data processing; they do not grant a content licence. Publication consists of original factual summaries, official links and FRSE attribution. It makes no open-licence claim and does not republish PDF documents, photographs or copyrighted prose. The public contact is `kontakt@frse.org.pl`; no permission request or other message was sent.

The 2026 [Erasmus+ programme documents](https://erasmusplus.org.pl/dokumenty) include both current English and Polish guides and the Official Journal call. The English guide governs language discrepancies. Programme eligibility, consortium rules, residence requirements, benefits and deadlines are checked against these current required inputs, rather than copied from an older directory description. Dedicated eTwinning, Selfie+, EDUinspirator, publishing, school-award and BCU rules are also required.

## Complete reviewed scope

The finite reviewed partition contains **57 records** from **94 required inputs**: 75 HTML documents and 19 PDF documents. Six robots policies bring the ordinary successful collection to 100 physical requests. All required inputs must match their reviewed material fingerprint or PDF bytes before any records can be returned or published. The sole verified eTwinning newsletter CSRF token value is normalized only after its exact hidden-input format, unique position and newsletter form structure are validated; all policy prose, links and other control values remain guarded. Directory pagination uses the publisher's public history query and explicit page controls: three funding pages, three training pages and two competition pages. No guessed filters, private APIs or further pages are requested.

| Inventory | Genuine identities |
| --- | ---: |
| Funding catalogue: 30 leaves, including one solidarity-programme alias | 29 |
| Substantive training, webinars and educational programmes | 14 |
| Recruitment references FRSE/27/2026, /25/2026, /24/2026 and /23/2026 | 4 |
| DiscoverEU, national eTwinning, supplementary professional-school award, Selfie+ 2025, EDUinspirator 2025 and the publishing call | 6 |
| Four distinct public Erasmus+ 40th-anniversary competitions | 4 |

Funding covers higher-education teaching/study, school mobility and partnerships, vocational learner/staff mobility and partnerships, sport staff and partnerships, two innovation-alliance actions, Jean Monnet teaching and policy networks, higher-education capacity building, Erasmus Mundus Joint Masters and Design Measures, adult education mobility and partnerships, youth exchanges/participation/partnerships, BCU and regional lifelong-learning infrastructure, EKS solidarity projects and volunteering, and Polish–Lithuanian/Polish–Ukrainian youth exchanges. The young-group and supporting-organisation views of solidarity projects are one identity.

Training includes the Switzerland/UK webinars, Słupsk education conference, Eurodesk Eurolekcje, eTwinning courses and webinars, Adult Education Forum, Education Congress, Mobile Education Centre, FRSE Information Day, Tool Fair, TEDxWarsaw Youth and Jean Monnet/Erasmus Mundus webinars. The own UK news article is an alias of the same webinar. Future event dates do not establish a registration deadline or an open application status.

Excluded material includes generic celebrations and reading-promotion days, the charity relay, the award gala, alumnus/winner biographies and duplicate programme views. EITA's national-agency nomination and EDUinspiracje/European eTwinning nomination routes are not independent public applicant calls. Media competition material describes an invitation-only round. WorldSkills/EuroSkills and its national qualifier remain outside this implementation because essential current dedicated rules and robots could not be verified; the adapter never accesses that origin. Raw collection preserves closed substantive calls with their actual historical identities; website migration does not create a new opportunity. The next published subset excludes calls with verified past deadlines; missing or ambiguous closing dates are not used to infer expiry.

Fixed places, job grades, grant amounts, categories, countries, durations, project stages and school-award fields do not create additional records. Jobs use one record per actual FRSE recruitment reference, including the two vacancies in FRSE/27/2026.

## Dates, identity and eligibility

- Stable IDs hash `pl-frse|<original canonical URL>`; the shared jobs URL additionally includes the literal `FRSE/<number>/2026` reference. Alias pages remain guarded provenance inputs. Foreign URLs and unrecognized ID/URL pairs cannot be accepted as previous FRSE data.
- Exact Official Journal deadlines explicitly use Brussels time, with the actual date's daylight-saving offset. Unzoned job and public-call clocks remain date-only; no Warsaw/UTC zone is inferred. Multiple institutional variants without a universal deadline retain a null deadline.
- Explicit guarded institutional-call closure permits `expired` even without one precise deadline. Participant programme overviews remain `unknown`: a closed institution grant round does not close every student, staff or volunteer recruitment. Event dates, programme execution periods and 1970 placeholders are not application deadlines.
- Legal residence, institution establishment and school/project affiliation are not nationality filters. `eligible_countries` stays empty unless passport-nationality eligibility is actually verified. Host countries/location are populated only for a proven physical destination, not automatically from the publisher's country. The legacy `country` field identifies the Polish publisher.
- The current VET staff guide limits courses to **2–10 days**, despite an old own-page 2–30-day statement. EMJM requires two physical semester-length periods in two countries, both different from residence at enrolment, with at least one EU/associated country. EMDM's current English and Polish guides specify **EUR 60,000**, despite an old own-page EUR 55,000 claim. Jean Monnet's current external network is EU–India, not the old Canada/USA description. These conflicts fail closed if the required sources change.
- DiscoverEU autumn 2026 uses legal residence and birth in 2008; the UK's future programme return does not add UK residents to that round. eTwinning permits each teacher up to four projects and applies previous-winner/project exclusions. School/Selfie awards are noncash competitions, not institutional grants. Publishing requires original unpublished work, excludes AI-generated manuscripts and other concurrent publication processes, and applies actual selection, licence and remuneration terms.

Records use actual aware UTC freshness timestamps, including one collection-completion timestamp for all `last_checked_at` values. Existing `created_at`/`first_seen_at` and unchanged semantic `updated_at` values survive successful refreshes. Previous record and metadata chronologies must be coherent before retrieval or failure reporting.

## Collection and publication

Run the sole live nonpublishing test:

```sh
python -B adapters/pl-frse/test_adapter.py
```

It emits the complete actual 57-record JSON on stdout, with unittest diagnostics on stderr. It does not publish, receive publication credentials or create dataset files. Publisher tests and production must use the same FRSE allowance and run sequentially.

The publisher allowance permits at most ten starts per rolling minute and at least six seconds between all origins, redirects and retries, with a 60-second initial floor. Stronger robots limits and known legacy intervals are preserved. Secure owned pacing state uses family `youthopps-frse-publisher-v1`; migration keeps the legacy pacing file intact, unions recent starts, takes maximum intervals/embargoes and ORs known refusal flags. It never clears an embargo or fabricates an unknown refusal. HTTP headers are processed before body validation, including malformed or distant `Retry-After`. Refusals, unsafe redirects, incomplete responses, changed policy/terms or changed material/frontier prevent publication. A single bounded 503 retry may occur; there is no whole-collection retry.

Preparation has an absolute 1,800-second bound, including both preparation gates and a 120-second request-start guard. Total execution is 2,190 seconds, with a 2,160-second operational cap, 180 seconds each for success publication and failure reporting, and 30 seconds reserved for trusted timing-state export. These limits accommodate the 654-second normal pacing floor without promising completion for every slow response or embargo. The 45-minute job leaves setup/upload headroom; the immediately minted one-hour App token exceeds the total execution budget.

Production's sole application command is `python -B adapters/pl-frse/adapter.py --publish`. The matching main-push workflow publishes only `YouthOpps/data-source/datas/pl-frse/{data,metadata}.json`, atomically and with compare-and-swap protection for siblings and newer source data. First failure creates no source folder. Later failure preserves last-good records and success/check timestamps, reporting failure metadata only when the unchanged validated pair can safely be updated. Success metadata uses run start for `last_attempt_at`/`last_success_at` and collection completion for `last_checked_at`.

Actions restore the latest complete trusted source-family artifact and never replace a missing artifact with an empty budget. The first production family run uses a concrete maintainer-reviewed `FRSE_PACING_BOOTSTRAP`, linked to the issue evidence for complete family history and the preserved, idle local pacing states. It supplies only a conservative earliest start bound after the reviewed states have no outstanding embargo, stronger interval or refusal; it never substitutes for an existing family artifact. Timing artifacts contain only inert pacing state, never records or credentials; cancellation and failed export keep the next run fail-closed.

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

## Current events-page review

On 2026-10-14 the official events index whole-material fingerprint changed.
Reviewed current page content still lists the supported UK youth webinar,
Słupsk conference, TEDxWarsaw Youth and existing cyclical programme routes.
All eight index-only event routes remain the documented exclusions: generic
ErasmusDays/European Youth Week/reading promotion, charity relay, awards gala,
and the separately blocked WorldSkills routes. The reviewed events-index frontier contains these supported and excluded
routes; all supported event detail routes remain required source inputs.
The calendar's visible month/day controls are part of this guarded snapshot;
its fingerprint is refreshed only after current whole-page facts/frontier review.

The remainder of the required source material was reviewed after the first live
run revealed additional drift: 35 inputs from DiscoverEU through the UK webinar.
Four HTML fingerprints changed (DiscoverEU, eTwinning call, Professional School
call and the ESC documents index). Current DiscoverEU birth-2008/legal-residence
conditions and unzoned 15 October noon deadline, eTwinning eligible applicants/
quality badge/15 January deadline, and the school's four sector routes/15 October
15:00 deadline still match the existing factual recipes. The associated school
and eTwinning regulation binaries are unchanged. The ESC index still links the
current 2026 guide, whose binary is unchanged; no newer guide is silently used.
The other 31 inputs, including Erasmus/ESC guides, publishing rules/ethics,
privacy/cookies, Słupsk programme and UK webinar, match their prior exact guards.
Changed whole-page guards were refreshed after this factual/linked-material
review, without changing eligibility recipes or inferring expiry from sidebars.

## Four current anniversary competitions

The reviewed [official announcement](https://www.frse.org.pl/aktualnosci/40-lat-eramus-4-konkursy-miliony-historii) advertises four genuine public submissions. Each keeps its distinct anchored identity on the [official competition hub](https://erasmusplus.org.pl/konkursy-40-lat-erasmus): `#40-slow`, `#zyczenia-urodzinowe`, `#konkurs-literacki` and `#before-after`. The announcement, hub and all four linked PDF regulations are required exact guarded inputs. They supplement the existing 53 unchanged identities.

The 40-word and literary contests require adult past/current Erasmus+ participants; birthday wishes additionally admit institutional beneficiaries and supporters. Before/after admits past/current participants. Birthday and before/after minors enter through a guardian or beneficiary institution. The 40-word limit excludes the title; the literary 2,200–2,600-character limit includes spaces and title. Birthday video is at most 20 seconds with mandatory 1080×1920 / 9:16 format, or an A4 card. Before/after video is at most 30 seconds; vertical format is recommended.

Three calls close on 11 December 2026; literature closes on 31 January 2027. These are date-only application deadlines; neither a closing hour nor timezone is inferred. Entry is free and awards are conditional in-kind prizes up to PLN 500 gross, with no cash substitution. One original unpublished/unawarded entry, provider/jury/close-family exclusions, rights/image consents and the unpaid indefinite worldwide nonexclusive promotional licence are material conditions in original factual evidence. Polish contest operations do not establish nationality restrictions or a physical host destination. The adapter publishes factual paraphrases and links, not the regulations or creative source prose.
