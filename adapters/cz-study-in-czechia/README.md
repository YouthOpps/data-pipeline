# Study in Czechia

This standalone standard-library adapter discovers the current public
[Study in Czechia catalogue](https://studyin.gov.cz/scholarships/) on every run.
It follows the incoming, outgoing and compatriot controls and their advertised
anonymous AJAX pagination. Page numbers, scope parameters, duplicate IDs,
terminal navigation and each live advertised total are checked. There is no
fixed programme inventory, result count, reviewed profile or content hash gate.
An incomplete page, inconsistent catalogue or failed required detail aborts
collection instead of publishing a partial replacement.

## Records and source limits

Every discovered programme detail is retrieved. Titles, labelled catalogue host
countries, scholarship types, study levels, institution relationships, duration,
payment amounts/frequency and travel fields come from current portal HTML.
Populated stay or payment fields and eligibility/funding lists are substantive
offer content even when the page has no paragraph elements. Amounts retain their
published currency and payment frequency; no frequency is inferred.
Short attributed
audience, funding and travel excerpts are explicitly incomplete; the adapter
does not redistribute complete descriptions, documents or personal contacts.
Summaries retain bounded live description and intended-audience excerpts rather
than generic catalogue boilerplate. Full eligibility and funding remain on the
linked publisher page. Classification uses explicit current title
and scholarship-type vocabulary; unknown types remain `other`.

When the English detail lacks descriptive prose or an eligibility/funding list,
the adapter follows its advertised `lang="cs"` counterpart on
`studujnavs.gov.cz`, verifying the same programme ID. It does not guess a
translation URL, reuse old translated summaries or fetch external provider
documents. Czech descriptions, eligibility, funding and placement requirements
remain native excerpts with `language: cs`; the English programme title, URL
and stable ID remain unchanged. The Czech detail URL is explicit provenance.
Missing or refused required counterpart retrieval aborts collection. Both
portals' call tables contribute current calendars; conflicting dates for the
same call abort collection rather than silently choosing one translation.

All dated call rows are read as provenance. The selected calendar is the current
call with the nearest closing date, otherwise the nearest future opening, or
the latest historical date. Blank opening or closing cells are unknown; each
known endpoint is retained, and calendars without either date remain evidence
only. A partial calendar with a future closing can be selected by that closing,
but never establishes that applications are currently open. Missing endpoints
can be complemented by the other language; conflicting nonblank dates still
abort collection. A closing before its opening is reported as a
publisher conflict, not repaired. Dates remain date-only, and the closing day
has unknown status. `open` requires both a current calendar and the catalogue's
explicit applications-open label, with both calendar endpoints known. Status
concerns the portal channel, not a
guarantee that a host or external nomination process is open. Programme details
without a valid calendar are `programme-overview`; dated offers are
`opportunity`. Only details with neither descriptive/list content nor populated
stay/payment/travel terms after the available native fallback are excluded.
Titles, affiliation metadata and historical call rows alone do not manufacture
an opportunity. A final diagnostic accounts for every discovered ID as a record,
an explicitly excluded detail or a merged nomination channel, and lists the
native counterparts fetched. No ID is excluded by a frozen programme list.

Country-of-application fields and university affiliation do not establish
citizenship. `eligible_countries` stays empty unless the detail includes the
explicit Czech-citizens-only placement requirement currently supported by the
parser. That rule reads the actual sentence on each run, not a programme ID.
Other eligibility remains in attributed native excerpts; an empty country array
means unverified, not unrestricted. `host_countries` uses the catalogue's location
labels. No publication date is inferred from a call opening or a retrieval date.

Stable IDs retain SHA-256 of `cz-study-in-czechia|programme-<ID>`, truncated to
24 hex characters. The twelve previously consolidated nomination-channel IDs
retain their existing representative when that programme is still present.
Every channel is fetched and its live fields and calendars remain in evidence.
Different channel deadlines produce a null aggregate deadline and unknown
status. New IDs are deterministic; annual calls do not create new identities.
If a representative disappears, a surviving channel has its own programme ID.

External PDF/XLSX documents, provider portals and call-detail pages are not
collection inputs. Advertised PDF/XLSX links remain explicitly unverified
provenance, so changed document editions can update a record without fetching
the external document. This is a portal-based inventory with **incomplete
eligibility and funding**, stated in every record. PDF-only conditions and rounds absent
from the portal cannot be discovered or verified. In particular, no historical
DAAD document is required and no request is made to `static.daad.de`; its robots
403 is not bypassed. Tempus materials remain excluded from retrieval and reuse.
No historic profile facts are carried forward as substitutes for missing live
facts. The former exact document fingerprints and manually reviewed snapshots
have been removed.

## Access and execution

The publisher's public catalogue advertises the navigation endpoints used by
this adapter. Only `https://studyin.gov.cz` and the advertised native counterpart
origin `https://studujnavs.gov.cz` are allowed collection origins.
Robots policy is checked before page access; refusals, challenges and unavailable
required policies fail closed. Public access and privacy/cookie policies are not
treated as an open licence. Attribution and original programme/call links are
preserved. No account, application form, registration or contact operation is
performed. The identifying user agent, TLS verification and inherited proxy/CA
configuration remain enabled.

```sh
python3 -B adapter.py
python3 -B adapter.py --publish
python3 -B test_adapter.py
```

The sole live test performs a complete nonpublishing collection, validates every
record and emits the entire JSON array on stdout. Diagnostics go to stderr.
It requires no publication credentials, writes no dataset, uses no fixture or
mock, and asserts no fixed record count. A run fetches the catalogue pages and
one English detail per discovered programme, advertised Czech details where
needed, plus robots and redirects. Runtime depends
on the current inventory; the existing 180-minute collection/job limits remain.

## Pacing and publication

The existing DZS-family pacing and publication implementation is retained.
`cz-dzs` and this adapter share `youthopps-dzs-publisher-v1`: at least six seconds
between request starts and at most ten starts in each rolling sixty seconds,
including robots, redirects and retries. A locked, owned temporary budget
coordinates local processes, and overlapping collectors are rejected. A single
retry of a 503 waits at least thirty seconds and honors a longer `Retry-After`.
403 and 429 refusals are not retried. Errors omit response bodies, credentials
and URL query strings.

The workflow preserves its main/repository guard, source-specific push paths,
manual dispatch, daily schedule and family concurrency with cancellation
disabled. Its application command is solely `adapter.py --publish`; it does
not run tests. Production execution is not used for implementation validation.

Actions restore the latest completed family attempt's inert pacing artifact,
including failed/cancelled attempts and reruns, with repository/workflow/attempt
and chronology checks. History scans are bounded, duplicate run IDs fail, and
only a bounded JSON ZIP member is read without extracting files. Future backoff
is never aged away. Missing, expired, corrupt or mismatched state fails closed;
no older successful artifact substitutes for it. The initial nonsecret bootstrap
is accepted only when authenticated history proves no preceding family attempt.
Artifact retention is ninety days; runner loss or missing artifacts can require
separately reviewed maintainer recovery. Upload is best effort, not a guarantee.

Publication uses the existing in-memory repository-scoped GitHub App token and
renewal. The initial source snapshot is retained across collection. Data and
metadata are committed atomically, using non-forced compare-and-swap; unrelated
source changes can be rebased, but a newer own-source result is preserved.
Only `datas/cz-study-in-czechia/{data,metadata}.json` may be changed. First-run
failure creates no published folder; subsequent failure retains last-good data
and its success timestamp. Same-ID records retain their first-seen timestamps,
and substantive live changes advance `updated_at` during publication.

Local live validation does not certify production publication or website
delivery. Those remain post-merge checks. Independent review must inspect full
inventory coverage, record provenance, ID continuity and the explicit limits
above before accepting this rewrite.

## Expired-record cleanup

Portal calendar closings remain date-only, with no stated closing timezone.
Merged nomination channels with conflicting closings retain null deadlines.

After a complete nonempty retrieval passes validation, the successful refresh
removes records with verified past application deadlines before preparing the
single atomic data/metadata publication. The next dataset fully replaces the
old dataset; old records are never merged back, including expired old IDs
still returned by the publisher. Status, record age, absence from a response
and missing or ambiguous deadlines do not establish expiry.

For a supported date-only closing without a documented timezone, UTC-12 is
used only as a conservative global certainty bound, not an inferred publisher
timezone: retain the record throughout its possible local closing day, and
remove it only once that date has ended everywhere. Aware timestamps are
retained at the exact cutoff and removed afterwards.

If every validated retrieved record has expired, publish an empty data array
and success metadata atomically, with record_count zero. An empty or invalid
upstream collection still fails. Later runs accept this valid empty prior
pair; failures preserve it and its last-success timestamp, updating only safe
failure metadata. No cleanup write precedes successful collection, and no
other source's files are touched. The live test remains a complete raw
nonpublishing collection; production publication is reviewed statically.

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

The scheduled refresh runs once daily at **02:17 UTC** (`17 2 * * *`).
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
