# University of Zagreb public opportunities

This standalone standard-library adapter discovers current University of Zagreb
calls and extracts programme tables from official HTML. Publisher text, dates,
links, table rows and counts may change between runs. There is no frozen content
hash manifest or embedded opportunity profile list.

## Inventory and discovery

- The [scholarship hub](https://www.unizg.hr/studiji-i-studiranje/upisi-stipendije-priznavanja/stipendije/)
  supplies its current named scholarship categories.
- The [lifelong-learning catalogue](https://www.unizg.hr/o-sveucilistu/sveuciliste-jucer-danas-sutra/osiguravanje-kvalitete/cjelozivotno-obrazovanje/baza-programa-cjelozivotnog-obrazovanja/pregled-programa-czo-po-sastavnicama/)
  supplies faculty links. Every discovered faculty programme table is parsed,
  including newly added programmes and changed duration, language and fees.
- All linked pagination is followed for student calls, staff calls, both other
  scholarship listings and international summer schools. Notices published in
  the current or previous calendar year, or whose titles name the current or next
  year, are retrieved in full. Undated cards without an explicitly historical
  edition are also inspected. Results-only titles are excluded. This is a
  recent-edition policy, not an all-time archive. This retains announcements made
  before a new academic year and recently closed calls.
- Each substantive vacancy section in the
  [Rectorate page](https://www.unizg.hr/o-sveucilistu/dokumenti-i-javnost-informacija/natjecaji/rektorat-natjecaji/)
  supplies a vacancy and its stable HTML anchor. Relative application windows are
  not converted into invented closing dates.
- The [research-call page](https://www.unizg.hr/istrazivanje/istrazivanje-i-inovacije/financiranje-istrazivanja/otvoreni-natjecaji/)
  supplies its substantive calls, including still-published closed research calls.

The previous implementation emitted 398 manually reviewed identities, including
PDF-derived faculty/host allocations and participant tracks. Live HTML collection
does **not** establish equivalent coverage of those document-only facts. Grouped
HTML notices are not evidence that their former allocations disappeared. PDF-only
mobility allocations, private application documents, external faculty catalogues,
and open-course lists supplied only in attachments are not parsed. These are coverage
limitations, not assertions that the underlying opportunities no longer exist.

The 13 October 2026 full-data comparison against the former profiles found every
prior HTML source URL represented: 412 live records from 209 HTML responses,
including 294 of 398 legacy identities after one-to-one migration. Targeted live
revalidation subsequently replaced eight grouped notices with 20 suboffers,
restoring all 19 legacy HTML track identities across Mainz doctoral/postdoctoral
awards, Erasmus staff activities, two academic-mobility rounds, Taiwan degree/
Mandarin awards, Azrieli doctoral/postdoctoral awards and two summer-school course
lists. Combined teaching/training is an additional separately listed activity.
All 20 records were validated in memory; this was not another full crawl.
Eight neighbouring notices were also checked live. Seven retain their single-offer
behaviour; VIA Summer School additionally exposes four named HTML courses with a
shared August 2026 period. Those courses are now separate programme overviews,
while the original VIA umbrella overview remains for identity continuity. Its
month-only event period is not converted into an application deadline.

Holding the remainder of the earlier full inventory fixed, the eight replacements
and four additional VIA courses project 428 records, 313 retained legacy identities
and 115 new identities. This is subset reconciliation, not a newly measured
full-crawl count. The remaining 85 unmatched
legacy identities are document-only: 31 bilateral PDF calls and 54 faculty/host
allocations across three Erasmus notices. The aggregate notices remain discoverable,
but their attachments are not evidence of extractable HTML allocations. No static
allocation counts, funding or participant requirements are restored as live facts.

## Facts and identity

Original Croatian titles and short attributed factual extracts are retained.
Programme rows supply structured facts in their summaries. Application deadlines
are parsed only from affirmative application-closing clauses with an explicit year.
Concise funding and eligibility excerpts beyond the introduction are retained in
classification evidence, so those material changes also advance updated time.
Conflicting or unsupported dates remain null. Publication dates, event dates,
mobility windows and relative deadlines are not silently used as application
deadlines. Date-only closings remain date-only; no timezone is invented. A future
deadline alone does not prove that places remain available, so status remains
unknown unless closure is established. Countries and eligibility are left unset
where the live content does not substantiate them.

Suboffers are extracted from current paragraphs, labelled mobility categories,
activity lists, scholarship sections and dated course list items. Their summaries
and deadlines use their own sections; shared application clauses are applied only
to the relevant outgoing tracks. Institutional mobility announcements do not
inherit candidates' online deadlines or the subsequent institutional handoff date.
Azrieli closing dates remain distinct; summer-school event dates are not closings.
An announced future application season alone does not create an additional call.
The old detailed funding/eligibility facts that exist only in attachments remain
unavailable even for restored HTML identities. Restoration of IDs is not a claim
of full field parity, and geographic/nationality lists are not copied from profiles.

IDs use the existing source hash namespace: canonical notice URL, faculty URL
plus programme name, scholarship URL plus category letter, or vacancy URL plus
section anchor. Publication preserves an existing ID for an unambiguous matching
URL and title, or a URL with exactly one old and one new record even if its title
changed, along with created/first-seen timestamps. Changed live facts advance
updated time; every successful collection advances checked/seen time. Several old
allocation records cannot safely be relabelled as one aggregated notice.
For split HTML offers, identity-only aliases preserve established track suffixes;
academic cycles are parsed from the current text where they belong to the identity.
New labelled categories, staff activities and courses receive deterministic keys
from their live labels rather than a fixed inventory. No old profile supplies facts.

## Access and publication

[Robots](https://www.unizg.hr/robots.txt) is checked before publisher access.
Discovery is restricted to the named official HTML route families. No private
forms, search endpoints, recipient lists or delegated sites are fetched. TLS and
proxy settings are preserved. Requests retain the publisher-family lock, the
six-second minimum spacing, rolling ten-start limit, retry/backoff handling and
the bounded collection deadline. Missing required pages, malformed listings,
invalid records or exhausted safety limits fail collection instead of publishing
a partial result.

The University's [public-information rights page](https://www.unizg.hr/o-sveucilistu/dokumenti-i-javnost-informacija/pravo-na-pristup-informacijama/)
describes Article 31 information reuse and possible justified conditions; it is
not a blanket copyright licence. Output includes attribution, canonical links,
short factual extracts and programme facts, not full articles, images or personal
application records. See also the University's
[privacy information](https://www.unizg.hr/o-sveucilistu/dokumenti-i-javnost-informacija/sluzbenici-za-zastitu-osobnih-podataka/).

Run the sole live nonpublishing test:

```sh
python -B adapters/hr-university-of-zagreb/test_adapter.py
```

It validates every record and prints one complete JSON array to stdout, with
diagnostics on stderr. Counts are dynamic. It requires no publication token and
does not write collected data files. Local pacing state remains necessary.

The Action's application command remains
`python -B adapters/hr-university-of-zagreb/adapter.py --publish`. Only this source's
data and metadata are committed atomically. First-run failure creates no source
pair; later failure preserves last-good records and success time while recording
failure metadata when safe. The workflow retains its schedule, own-path push
trigger, main/repository guard, scoped token and serialized pacing-state handoff.
Live tests do not establish production publication success.

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

The scheduled refresh runs once daily at **09:18 UTC** (`18 9 * * *`).
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
