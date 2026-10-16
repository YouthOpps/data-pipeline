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
on the complete current inventory. The old 10,800-second collection ceiling
remains an upper bound; the new effective prepublication limit is 9,900 seconds
(165 minutes), inside a once-frozen 10,560-second lifecycle (176 minutes). This
reserves 300 seconds each for publication and failure reporting and 60 seconds
for inert state export, within the unchanged 180-minute hosted job. History
restore and the initial snapshot are included in the prepublication phase.
Absolute phase alarms enforce total wall time; no request or retry renews it.

## Pacing and publication

The workflow preserves its main/repository guard, source-specific push paths,
manual dispatch, daily schedule and serialized family concurrency. It runs
only this adapter's publication command. Readiness-gated artifact upload does
not certify source collection or successful publication.

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

Date-only deadlines remain eligible throughout their UTC closing calendar
day and expire at the next UTC midnight. This shared calendar rule does not
infer a publisher closing clock or timezone. Offset-aware deadlines retain
their explicit instant and are removed only afterwards. The original DZS
native passed before this pure cleanup correction; all actual retained inputs
are replayed offline without another publisher collection.

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
access; restrictive rate and delay directives remain effective. The family identity remains stable; the stronger pacing and continuity
protocol is documented below. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **02:17 UTC** (`17 2 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

The 30-second durable inventory completion guard covers all three catalogue scopes and every advertised AJAX pagination fragment.
It applies to each physical retry and redirect, while literal robots and
programme/event/delegated detail exchanges keep the separate 12.2-second floor.
Stricter publisher policy, retained embargoes and family history take precedence.
No source inventory, parser, ID or publication scope is truncated to fit timing.

## Workflow concurrency

`youthopps-dzs-publisher-v1` serializes runs of this source and `cz-dzs`. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **DZS** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Shared family pacing and continuity (#289)

Both this producer and its sibling use the same `youthopps-dzs-publisher-v1`
namespace, lifecycle lock, modern state schema and inert artifact contract.
They must migrate together. Every physical publisher request, including robots,
redirects, delegated requests and the existing one-time HTTP 503 retry, is at
least 12.2 seconds after the preceding response completes and at most five
starts occur in any rolling minute. Every physical inventory exchange also
waits at least 30 seconds after the previous inventory exchange completes.
Details retain the physical floor; inventory pacing does not globally slow
all later details. Applicable current and legacy REP groups, stricter delays
and request rates remain protective constraints. Applicable finite decimal
timing is retained as a physical floor. Unsupported applicable numeric timing
leaves assessment pending and refuses traffic; malformed directives for
unrelated agents do not impose a new restriction.

HTTP 429 persists adaptive cooldown and doubles both CURRENT physical and
inventory gaps before reading the response body. Invalid Retry-After alone
creates no refusal; a valid longer value cannot shorten existing constraints.
A fully assessed ordinary rate warning stops the job with a timed embargo.
Actual content 401/403/451, primary content challenges and applicable REP
Disallow retain a separate protective hold. Literal robots-only 4xx except 429
mean unavailable REP; TLS, server and rate-limit failures still stop access.
There is no identity switch, immediate 429 retry or automatic hold clearing.
The existing single fully assessed 503 retry waits at least 30 seconds, retaining
stronger timing; no generic connection retry is added.

A durable `in_flight` barrier covers each physical request through headers,
body, policy assessment and successful completion persistence. Cached REP
application has its own zero-wire transaction. An unassessed response,
failed restrictive/completion write or unsettled prior transaction refuses
same-process reentry and a fresh process restoring an older owned state.
Owner/mode/link checks, nonblocking file opens and pinned parent-relative
operations protect the current journal inode. Normal reads and writes use the
retained locked descriptor; a replacement cannot inherit stale lock authority
or have stronger constraints overwritten. Failed export invalidates only
safely owned output before fallible readiness signaling and removal.

Both LOCAL and hosted collection read complete histories of BOTH workflows,
rank actual exact-attempt job completions, include prior rerun attempts and
require the exact newest owned artifact. Modern envelopes bind repository,
source, run, attempt, head and implementation/state digests. Missing, unsafe,
expired or unsettled modern state never falls back to an older success.
LOCAL collection additionally requires both exact workflows manually paused
before state initialization and again after startup quiet, immediately before
the first publisher request. Read-only GitHub access uses the configured
connection; hosted jobs require their supplied workflow credential.

The first migration is a separately approved exact legacy handoff, not a
new empty allowance or generic old-schema conversion. The inspected local
namespace was genuinely absent, which does not establish zero historical
traffic. The root-verified latest legacy artifact belongs to DZS run
38823222698/attempt 1. Known state/timing constraints and all 20 actual job
completion upper bounds are retained with at least 60 seconds startup quiet.
Rate counters begin only since migration; unknown historic 429 history remains
explicit. `legacy_access_history_unknown` is strictly true, with immutable
provenance for the route-unidentified 403 in CZStudy run 37962105063. A later
successful run or legacy `blocked: false` cannot erase that uncertainty.
The later run 38245510407 specifically failed on the now-excluded historical
`http://static.daad.de/robots.txt` dependency; it does not identify the first
403's route. Any guarded reevaluation of current routes requires a separate
explicit maintainer boundary approval, never an assertion of inherited access
permission. DZS LOCAL and Study LOCAL are separately approved and bound.
Both hosted migration markers now bind the reviewed full-family MAX
normalization. Study used the existing DZS family journal and reviewed
post-DZS MAX for its separately released complete original live test.

Original live tests remain sole complete nonpublishing tests. Private failure,
interop, graph and clock controls are not publisher collection evidence.
Implementation validation does not dispatch production Actions. Both sources'
complete original tests must be separately released and serialized under the
family lock; full bodies, headers, stdout and state-save evidence stay in RAM.

The initial production cutover is staged: keep Study in Czechia paused while
DZS creates the first verified modern owned artifact, then enable the companion
only after the family is idle and that newest state is verified. Starting both
from the finite legacy bridge would correctly fail its history guard. LOCAL
validation is also serialized DZS then Study; the companion retains the same
actual journal and full known MAX under a fresh explicit release, never a reset.

Journal and collection-lock cleanup retain explicit ownership of their original
descriptors. A reused foreign descriptor is never closed or truncated, and
failed journal cleanup never opens a newer named replacement. Close failures
are sticky access failures, invalidate safe owned artifact output, and preserve
an existing primary exception. These conditional IO repairs use private fault
controls and retained-input replay; they do not constitute a new live test.


### Reviewed complete-family production handoff

The maintainer approved both hosted literals in
[the complete-family production boundary](https://github.com/YouthOpps/data-pipeline/issues/289#issuecomment-6208060152).
The full shared history retains 1,926 typed timing observations, including the
previous DZS 130 and all 449 Study reservations, opener invocations, physical
starts and response completions. These observations are not request counts.
The explicit normalization retains the 20 observations within the documented
60-second window and preserves all other MAX constraints, unknown access and
429 history, and complete external provenance for the 1,906 aged observations.
Historical pending states settle only through their matching successful durable
response or policy chains. This approval does not clear any future hold or
permit fallback from a missing newest owned state.

The genuine Study original test at source `2440aed8` passed with all three
catalogue scopes: 271 programmes, 258 normalized records, 174 current records
and 84 actually expired records. All 449 real responses and headers, 1,351
successful saves and 450 sequential transactions were retained and audited.
The actual 254 date-only deadlines and four null deadlines introduce no clock
or timezone claims. Measured completion spacing was at least 12.2148 seconds;
29 inventory exchanges were at least 30.0159 seconds apart after completion,
with no more than five physical starts per rolling minute.

The complete measured Study native took 6,503.34 seconds. Adding the full
300-second publication, 300-second failure and 60-second export reserves plus
a nominal 240-second setup allowance leaves about 3,396 seconds within the
180-minute job. This is measured planning headroom, not proof of a future job
start clock or guaranteed setup duration. The original repository-scoped App
token renewal remains in place for collections lasting more than one hour.
The maintainer controls the serialized rollout: DZS first, then the Study
companion using the newest verified modern family state.
