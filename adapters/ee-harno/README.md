# Harno scholarships and academic grants

Source `ee-harno` collects public academic funding from the Estonian Education
and Youth Board. Discovery starts at the [international scholarship entry](https://www.harno.ee/en/scholarships-and-grants/scholarships-studying-and-working-estonia/scholarships-international)
and follows current English/Estonian scholarship navigation, own programme
details, language variants and HTML pagination. The collection is not a fixed
list of profiles and does not compare page content with pinned fingerprints.

## Inventory and boundaries

Research on 14 October 2026 traversed 30 canonical pages and normalized 70
records. Counts are observations, not acceptance constants. The collection
covers the international summer/research/degree schemes, Estophilus, language
courses, Ustus Agur, special-needs support, Kristjan Jaak, EKKAV lecture funding,
five distinct Nordplus subprogrammes, young scholars, EUI doctoral funding,
care-leaver support, Uuri Noorte Heaks, higher-education textbooks and the
publisher's foreign-scholarship panels.

The [foreign-scholarship listing](https://www.harno.ee/valisriikide-stipendiumipakkumised)
contains substantive HTML, including expired offers retained by the publisher.
Its 17 country panels contain separate awards: two Latvian schemes, six Italian
schemes, Czech summer-school and study-mobility funding, three current Swiss
schemes, four older Swiss schemes, four Japanese MEXT awards, thirteen DAAD
programmes, four Hungarian schemes and other country offers. Benefit tiers
within one scheme are retained as conditions rather than invented awards.
Multiple application windows stay structured under the same programme ID.
A listing under closed application rounds is not marked open merely because
its calendar deadline is unknown.

Umbrella navigation, recipient stories, historical Kristjan contracts,
institutional directories, textbook catalogues, application systems and external
provider websites are not separate opportunities or collection endpoints.
The expressly discontinued expatriate-Estonian scheme is excluded. Municipal
service allocations and youth-service operating grants are outside this academic
scholarship source. The Erasmus page is a referral to the external programme,
not a new Harno funding call. Unknown PDF-only conditions are explicitly
identified with their links; their presence never discards substantive HTML
offers or creates invented amounts.

## Current facts, dates and identity

Programme titles, factual clauses, funding amounts, eligibility and application
windows come from current HTML sections. Full material block digests complement
extracted evidence so a substantive change beyond the introduction changes the
record and its publication `updated_at`. Digests are change signals, never
collection gates. The 600 UTF-16-unit summary is a short index; the supporting
evidence consists of short factual clauses, not complete narrative paragraphs.
Long unsegmented conditions are linked and explicitly marked for review rather
than copied wholesale. PDF references are scoped to the offer and shared
conditions that actually link them, not every offer on the same page.

All 13 previously published IDs are retained. Compatibility aliases contain
identifiers only, without pinned dates or funding terms. The three previously
published Kristjan mobility rounds keep their established date-based identities;
new genuine rounds receive their own call identity. Other programmes retain one
stable identity and structured `application_windows`, with a null universal
deadline when several distinct windows are published.

The current language scholarship, Estophilus and both EKKAV lecture awards
have an October 15 to November 15, 2026 window. Estophilus's English translation
still displays a March round; its substantive native Estonian page supplies the
current call, and the discrepancy is disclosed with each language's provenance.
Kristjan's native page likewise provides its actual current round notices.
Separate lecture awards preserve the difference between reimbursed guest travel
and explicitly unreimbursed Estonia-themed lecture travel.

Only explicit application clauses supply deadlines. Project periods, course
dates, result announcements, page updates, birthdays and reimbursement milestones
do not. Yearless and incomplete dates stay unknown; a cross-year opening without
an explicit year is not invented. Explicit Estonian closing clocks use
`Europe/Tallinn`, including DST; unzoned dates stay date-only and retain their
stated clock in evidence. The EUI January deadline has no year and therefore
does not become a fabricated closing date.
An academic-year label cannot supply a missing deadline year. An explicitly
unannounced suboffer deadline does not inherit a different shared deadline;
opening and closing clocks are associated with their own dates.

The degree/exchange programme's actual English citizenship list is parsed and
validated (currently 39 countries). Embedded ISO 3166-1 names, official/common
names and publisher aliases cover 249 codes; these are a normalization taxonomy,
not a fixed eligibility list. Unrecognized citizenship entries fail clearly
instead of silently disappearing. Residence alternatives, host countries and
publisher location never become citizenship restrictions. Country-panel headings
and their destination conditions establish foreign hosts; an expressly
online-only offer has no physical host inferred. Russian/Belarusian exclusions
remain scoped to the international scheme. PDF-only rates stay unverified.

## Access and publication

Current robots and linked official privacy/cookie policies are checked. The
reviewed policies concern personal information and browser services; no general
factual-collection prohibition was found. This is not an open licence to reproduce
protected works. Normal TLS, the identifying YouthOpp User-Agent, refusal handling,
same-publisher redirects and complete-document checks remain enabled.

Requests, redirects, retries and robots share a locked local allowance of at
least twelve seconds between Harno starts and no more than ten starts in any
rolling 60 seconds. Longer Retry-After and robots delays remain authoritative.
Only 429/502/503/504 receive the existing bounded retry; 403 is not retried.
Separate machines must not overlap publisher collections.

During scope research the unrelated [municipal integration-grant endpoint](https://www.harno.ee/stipendiumid-ja-toetused/muud-toetusmeetmed/formaal-ja-mitteformaaloppe-loimingu-rakendamise-toetus)
returned 403. It was requested once through the existing transport, never retried or
accessed through an alternate route. Subsequent distinct academic URLs used the
same unchanged pacing state; no family-wide refusal flag exists in this
transport. The response headers were not retained, so no claim is made about
whether that refusal supplied Retry-After. The refused municipal resource is
outside the academic collection, not a silently dropped scholarship.

```sh
python -B adapters/ee-harno/test_adapter.py
python -B adapters/ee-harno/adapter.py
```

The sole live test retrieves real records and emits complete JSON on stdout,
with diagnostics on stderr. It receives no publication token and writes no
datasets. Development retries reused genuine responses only in process RAM;
the delivered test has no temporary cache hooks or fixtures.

The unchanged workflow runs on source-filtered main pushes, its six-hour
schedule and manual dispatch. Its only application command is this adapter's
`--publish`. The repository/main guard, scoped App token and serialized
publication remain unchanged. Publication atomically writes only
`datas/ee-harno/{data,metadata}.json`, preserves last-good data after failure,
creates no source folder on first failure, and refuses conflicting newer
source updates. Metadata preserves the existing cross-run publisher cooldown
(at least 120 seconds after the last response, or longer Retry-After); later
publication runs honor it. No production Action is used for testing.
