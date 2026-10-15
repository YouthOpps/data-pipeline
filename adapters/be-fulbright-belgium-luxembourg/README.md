# Fulbright Belgium and Luxembourg

This source indexes award facts published by the Commission for Educational
Exchange between the United States, Belgium, and Luxembourg on
<https://www.fulbright.be/>. The stable entry is the
[Belgian/Luxembourgish awards directory](https://www.fulbright.be/grants-for-belgian-and-luxembourgish-citizens/).
The issue's original [2027–28 announcement](https://www.fulbright.be/news/2027-28-competition-open/)
confirms that this is a current competition, not a generic directory integration.

## Access, attribution and scope

Normal verified HTTPS works. On 15 October 2026 both official robots routes
returned HTTP 404, replacing the earlier recorded upstream certificate failure.
The adapter retrieves robots during every complete collection; HTTP 4xx other
than 429 means the REP file is unavailable, consistent with RFC 9309 section
2.3.1.3. It never treats a denied award page as an unavailable robots file.
HTTP 5xx, transport/TLS failure, explicit robots disallow and rate-limit responses
remain failures. Applicable new and legacy product groups must independently
permit access; the strongest applicable crawl delay/request rate is retained.

The published privacy and cookie notices concern visitor data; the footer states
all rights reserved. No broad content licence is claimed, and no source-specific
automation prohibition was identified on these routes during development. The
index contains titles, factual conditions, amounts, dates, original links and
brief original English summaries. It does not redistribute full descriptions,
images, testimonials, contact details, applicant data or application forms.
Original commission/university terms remain authoritative.

Every physical HTTP request declares:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot page](https://youthopps.org/bot/) describes the crawler and human-reviewed
contact/removal process. This identity never clears a prior refusal or backoff.

The complete own-site inventory currently yields **42 records**:

- Four outgoing tracks: graduate study, doctoral research, postdoctoral
  research/lecturing, and SUSI secondary-educator institutes.
- Five incoming routes: U.S. student study/research, scholar research/lecturing,
  English Teaching Assistantships, Inter-Country Lecturing, and Specialist
  institutional projects.
- All 31 named LL.M. partner-institution sections, including their actual
  amount/tuition conditions. These are automatic consideration within Fulbright
  selection, not 31 separate guaranteed grants. University admission is separate.
- The two explicitly named Harvard Boas awards: Graduate Study and International
  Legal Studies, with distinct full/need-based partial funding.

The [partner directory](https://www.fulbright.be/grants-for-belgian-and-luxembourgish-citizens/fulbright-partner-awards/)
and the two citizenship directories are read completely. Literal own-site award
links are followed; a new unsupported track fails for review rather than being
silently omitted. Partner sections are discovered from actual headings, not a
fixed list of institutions or stored descriptions. The initial outgoing directory
is checked again before accepting a complete collection.

External Schuman, IIE/U.S. Student, U.S. Scholar and university catalogs are
explicitly delegated sources; their separate worldwide award inventories are
not claimed as part of this `fulbright.be` source. Their official links remain on
the commission's original pages. Generic study advice, news, testimonials,
alumni profiles, donation pages and FAQ pages do not become opportunities.

## Identity and factual interpretation

IDs use the source slug plus the commission's numeric WordPress page ID. The
31 partner records also use each actual Elementor heading component's native
ID; Harvard's two named programmes have fixed `graduate-study` and
`international-legal-studies` suffixes. Identity never depends on row order or a
mutable display title. Final IDs are ASCII alphanumeric kebab-case and duplicates
are rejected case-insensitively after all native assignments.

Outgoing citizen eligibility is BE/LU; SUSI specifically requires Belgian
citizenship. Incoming student/scholar/ETA routes concern U.S. citizens. The two
institutional project routes do not pretend that host location is individual
citizenship eligibility. Destination and publisher country remain separate.
Postdoctoral and institutional routes are identified accurately rather than
advertised as unrestricted student applications. Programme overviews retain
unknown availability when no current dated application channel is established.

The current outgoing directory explicitly names the 2027–28 cycle and
1 December 2026. Each of the three degree/research pages independently states
noon CET: **2026-12-01T11:00:00.000Z**. Harvard's current 2027–28 application
channel states the same calendar date, but no explicit timezone; its deadline
is therefore **2026-12-01**, without an inferred clock or offset. The year comes from the dated
banner and matching current application label, never collection time alone.
SUSI, incoming annual periods and undated partner offers do not inherit this
closing. In particular, “April till mid-October” without a year is not a precise
current deadline. Rolling institutional routes have no fabricated closing date.

The short original summaries identify the programme and core eligibility;
partner funding figures are parsed from their actual source sections.
All full conditions and separate university deadlines must
still be checked at the original publisher. No image or copied long description
is included. Summary length is validated at the website's 600 UTF-16-unit limit.

## Schedule, pacing and complete refresh

The workflow runs daily at **08:50 UTC** (`50 8 * * *`), a fixed randomly selected
slot distinct from the existing source, website and workspace sync timetable.
Main merges affecting this source also start publication, and manual dispatch is
retained. The repository/main guard and serialized source queue remain active.

A shared, locked `fulbright.be` publisher-family budget spans www/non-www names,
requests, explicit redirects, retries and local overlapping runs. Request starts
are at least 12.1 seconds apart, with at most 5 in a rolling minute; the lock
remains held through response completion and the following gap. Stronger robots
limits and Retry-After are honored. Required-page 401/403 refusals persist.
A 429 stops the current job and durably records its Retry-After embargo; absent
a valid header, the pause starts at 30 minutes and doubles on subsequent 429s.
Each 429 also doubles the currently retained physical and page gaps, including
already stricter publisher limits, and doubles the no-header fallback cooldown.
Longer valid Retry-After is retained. Malformed, nonpositive, nonfinite or
unrepresentable timing is ignored before budget mutation; a 429 uses its
existing exponential fallback. Existing content holds and stronger embargoes
are never cleared.
A later job can retry after the embargo with the slower retained spacing;
there is no immediate same-job retry or identity change.
Robots-only unavailable 4xx does not become a general content-access refusal.

The three complete award-directory partitions and the closing inventory check
have a separate completion-to-next-page guard of at least 30 seconds within a
serialized collection. It does not promote all detail requests to a permanent
30-second native interval. Individual award, policy and redirect requests keep
the native allowance. No JavaScript, assets, applications or external catalogs
are retrieved. The collection limit is 15 minutes and the Action limit is
30 minutes; the observed inventory is small enough for the one-hour publication
token without increasing caps or weakening completeness.

The workflow authenticates its own complete workflow history and restores only
the exact latest prior run-attempt's inert publisher-state artifact. It never
falls back to an older cache. Missing, expired, ambiguous or invalid state after
any observed attempt fails closed for administrator recovery, including before
the first successful publication. An authenticated first-ever run can bootstrap
only if no source snapshot exists. Trusted timing is restored before the
GitHub data snapshot read, so a transient GitHub failure still retains state.
A new-source seed remains marked pending and cannot make publisher requests
until snapshot absence is confirmed; an existing source rejects that seed.
Bootstrap preserves every field of the
reviewed genuine native publisher journal (including prior starts, stricter
limits, embargoes and historical diagnostic notes), MAX-merges local state,
and adds a startup quiet period of at least 60 seconds. Its reviewed marker
and native-journal SHA are embedded source-local; no fresh allowance replaces
known state. State is retained after success or failure;
a failed state upload makes the next attempt fail closed. The artifact contains
only publisher timing/access state, no normalized opportunities or credentials.
Clear HTTP200 access challenges also create a retained access hold. Recovery
never authorizes clearing an access hold without resolving its cause.
During development the existing diagnostic history/backoff is handed over under
the same publisher allowance; historical TLS evidence is retained.

No verified change feed or modified-since semantics cover older edits and
removals. Therefore every run fully reconciles the own-site inventory; there is
no unsafe known-ID stop, publication-date cutoff, delta watermark or extra cache
of normalized opportunities.

## Validation and publication

Run the sole live, non-publishing test:

```sh
python -B adapters/be-fulbright-belgium-luxembourg/test_adapter.py
```

It retrieves and validates the complete nonempty raw inventory and emits one
JSON document to stdout, with diagnostics on stderr. It never reads publication
credentials or writes data files. The adapter/test/README folder is independently
copyable and uses only Python's standard library.

Production uses only:

```sh
python -B adapters/be-fulbright-belgium-luxembourg/adapter.py --publish
```

After complete raw validation, supported passed application deadlines are removed
from the replacement dataset. Explicit offsets retain their real instant;
unknown date-only zones wait until the date has ended worldwide (UTC−12).
Unspecified or ambiguous deadlines, old publication dates and upstream absence
are not inferred expiry. A valid nonempty raw refresh can publish an empty
successful array when every dated record has expired; later reads accept that
empty snapshot. Raw empty/incomplete/invalid retrieval remains failure.

Data and success metadata are committed atomically only to
`datas/be-fulbright-belgium-luxembourg/{data,metadata}.json`. Success replaces this
source's complete prior dataset, so old expired records cannot persist. Existing
creation/first-seen timestamps are retained. Conflicts rebase only if this
source's prior pair is unchanged; another source is never modified. Fetch,
parse, validation or publication failure preserves last-good data and the last
success timestamp; a first failure creates no published source folder. Tests and
implementation review never invoke production publication.
