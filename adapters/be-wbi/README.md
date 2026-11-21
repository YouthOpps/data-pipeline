# Wallonie-Bruxelles International

This standalone, standard-library adapter indexes WBI's own public funding,
training and paid-placement information. It publishes attributed titles, basic
facts, source links and short independently written summaries. It does not
collect applications, images, full descriptions, external provider catalogues,
account pages or search results.

Run the sole live, non-publishing test with Python 3.12:

```sh
python -B test_adapter.py
```

Complete raw JSON goes to stdout; diagnostics go to stderr. The original repaired live test and independent full-capture review passed.
The matching production workflow remains disabled pending final durable-state
handoff review and maintainer merge. Local success is not hosted publication
acceptance.

## Inventory and identities

The collector follows every advertised page of the official
[scholarship catalogue](https://www.wbi.be/en/bourses) and
[current calls index](https://www.wbi.be/fr/appels-a-projets).
It checks contiguous page coverage and rejects repeated page fingerprints.
Repeated publisher cards are deduplicated by canonical URL, never used as a
pagination stopping boundary. No reliable incremental change boundary is
published, so each refresh collects the complete selected indexes and details.

Actual captured catalogue pages contained 10 and 7 cards, with overlapping
entries and one duplicate within page 1: 14 distinct programme URLs. The calls
index contained six distinct articles without pagination. The separately
verified [Tournesol programme](https://www.wbi.be/fr/aide-service/beneficier-dun-soutien-projets-cooperation-scientifique-france-programme-hubert-curien)
remains a supplemental frontier because it is an existing supported programme
outside the scholarship filter. Its current publisher node 8641 was fetched
and validated; absence from the filtered catalogue is not closure or expiry.
This defines the source's supported inventory, not every service on WBI's site.
News archives, RSS, external application services and linked documents are not
crawled. Current inventory counts are observations, not hard-coded limits.

All discovered details are retrieved before normalization. The parser binds
one full article's native Drupal node ID to its canonical URL and native
shortlink without requesting node aliases. New single opportunities use the
native ID. Existing genuine programme IDs retain their explicit compatibility
mapping; dates, summaries and classifications are read from current evidence,
not old manually frozen profiles or full-page hashes.

Separate WORLD doctoral/postdoctoral/short awards, Excellence IN long/short
awards, incoming/outgoing Mobility Fund routes and the named Québec tuition
exemption retain distinct identities. The three UNESCO internships are matched
between their named headings and separately published document identifiers;
document URLs identify the calls but are not fetched. Missing or ambiguous
multi-award structure fails before publication. Translation copies, destination
variants, available places and general application forms are not extra awards.

A single call and programme share an identity only when the call links to the
actual fetched canonical programme and both explicit non-null application
deadlines agree. This applies to future native call IDs as well as the current
Louisiana call. Multiple matching programmes or competing calls fail closed.
Unknown dates cannot prove an alias; a different explicit cycle retains its
own native call identity instead of overwriting the programme's closing date.
Louisiana's current call retains its existing programme ID. The separate CIRC
pilot postdoctoral programme remains supported; it is not the refused and
expired SEE internship article.

## Dates, classification and cleanup

Only explicitly stated application dates with a year become deadlines.
Publication dates, interview dates, degree-proof dates and annual yearless
windows never supply a guessed closing year. The current company-internship
programme publishes recurring November 1/May 1 dates without a year; it is a
programme overview with unknown deadline, not an invented next-cycle call.
Tournesol and WORLD also retain unknown annual deadlines. Québec's current
project call gives December 13 without a year, so its normalized date is null.

Current explicit dates include Louisiana December 31, 2026, OIM December 5,
2026, UNESCO November 22, 2026, Morocco December 15, 2026, and the expired Hague
January 31, 2026 call. Date-only deadlines do not assert an unpublished closing
timezone. Cleanup retains the full UTC closing calendar day and expires the
record at the next UTC midnight; offset-aware deadlines use their actual
instant. Missing dates remain unknown. Residence, FWB affiliation and host
countries do not imply citizenship; only the separately described Québec fee
exemption projects explicit Belgian eligibility. Unknown destinations remain
empty instead of inheriting stale country lists.

The exact previously validated CIRC SEE call has the explicit November 13,
2026 deadline and is permanently denied by the retained refusal below. If that
exact URL appears in the index, it receives the documented expired disposition
without fetching it. Malformed URLs and other refused aliases never acquire
that expiry exemption. The historical published CIRC row cannot survive a
successful complete replacement. Its separate pilot scholarship is retained.

The complete nonempty raw collection is validated before expiry filtering.
Creation and first-seen timestamps survive for stable refreshed identities.
Successful atomic publication replaces only this source's data/metadata pair;
it never imports unverified old records as freshly checked opportunities.
A valid collection whose records all expire publishes an empty array with
success metadata. Empty retrieval or fetch/parse/validation/publication failure
preserves last-good data and its success timestamp. Source disappearance, age
and unknown deadlines are never used as evidence of expiry.

## Access, pacing and scheduling

[WBI's legal notice](https://www.wbi.be/fr/mentions-legales-0) permits attributed
textual/numerical information for noncommercial, nonadvertising use. It is not
an unrestricted open licence. Commercial/advertising use or other protected
elements require prior permission from the notice's contact. No contact was
sent. WBI attribution and original source links accompany the minimal index.

Every publisher request sends
`YouthOppsBot/1.0 (+https://youthopps.org/bot/)` with normal TLS and the session
proxy. Applicable current and legacy robots groups must both allow access;
stricter directives remain binding. One durable publisher-family budget covers
robots, details, redirects and retries: at least 12.2 seconds after the previous
physical completion and at most five starts in any rolling 60 seconds.
Inventory exchanges additionally wait at least 30 seconds after the preceding
inventory completion. Stronger retained delays, Retry-After and embargoes win.
A response 429 retains adaptive constraints and stops before body reading;
its fallback cooldown is at least 1,800 seconds and never weakens a stronger
publisher delay. No refusal, close failure or persistence failure is retried
through a fresh allowance.

The source workflow has one daily schedule at 07:11 UTC (`11 7 * * *`), its
own main-branch path-filtered push trigger and manual dispatch. Concurrency
`youthopps-be-wbi-publisher-v1` queues up to 100 pending runs without cancelling
active runs. Local collection and remote publication may not overlap.
The workflow remains disabled during this repair.

## Durable state and exact continuity

The original strict nine-key owned-artifact envelope, state digest, producer
source digest, expiry and exact run/attempt ownership checks remain intact.
Local journal authority binds the original descriptor, parent, inode metadata
and all acknowledged bytes. Every read, write, publisher prewire check and
healthy close must still match that generation. Only a complete fsynced and
read-back write advances authority. Partial or unacknowledged changes stop
access; cleanup never overwrites foreign state. The journal bound is 16 KiB.

All restore and export paths preserve stronger timing/rate constraints by MAX,
unknown legacy history, pending state and refusal events. A pending or unknown
generation is not silently reset. Export retains its original output authority
through cleanup and must fail safely after persistence/close errors.

Issue [#373](https://github.com/YouthOpps/data-pipeline/issues/373) proves the
18 artifactless attempts from `41068492690/1` through `43913201360/1` failed
before publisher access. All 18 logs were inspected: the first failed completed
history validation, followed by 17 missing-newest-state failures. Each exact
single `collect` job completed within 47 seconds. Recovery pins their actual
attempts, heads, jobs, times, source/workflow blobs and empty artifact lists.
The complete ancestry must reach owner `40889063854/1`, artifact `12903619139`,
SHA-256 `10f6fe52c39de27726595d551d4adfeab74685983bc5db204db1583adeeb2028`.

Future no-access proofs require this exact running source, the reviewed
workflow, attempt one, one failed collect job lasting under 58 seconds and no
artifacts. Successful restoration enforces a 60-second monotonic startup floor
before publisher transport; wall-clock changes cannot shorten it. Unknown
source revisions, reruns, longer jobs, extra outputs or incomplete ancestry
fail closed. The local first-wire rescan compares the actual latest history,
including proven skipped attempts, rather than mistaking the selected old
artifact owner for the newest run. Missing ownership alone never permits an
older-state fallback.

Entry, restoration, source, publication, failure and export keep the existing
nonrenewing deadlines. The 1,740-second entry reserves publication/failure/
export tails; source collection has at most 1,200 seconds and consumes the
remaining real budget. Counts do not prove a worst-case timing guarantee.
Publication retains its original GitHub pacing, at most three bounded conflict
attempts, source-pair comparison and non-forced atomic commit behavior.

## Retained CIRC refusal and approved route scope

The original November 16 native attempt at source
`06cc8b0de832b4f055c34a47bf16c435dc75365d0260ee260cd37a8766034086`
failed after robots 200, Louisiana 200 and the CIRC SEE article 403. That
original failure remains a failure. The complete 56,192-byte response contained
WBI's Drupal missing-page text, not a demonstrated publisher-wide challenge.
Its SHA-256 is
`befd4e4a843a70b415e8b99d479820598a322e4b404c8c63e2464e2babe6d917`.
The complete held journal's SHA-256 is
`aa544101765e65c7f3126501cce8e67881062ccbefec77fb1930cd5efab26e0c`.
The original 14 raw persistence witnesses and full failed response remain
retained with [issue evidence](https://github.com/YouthOpps/data-pipeline/issues/373#issuecomment-6924594888).

Reviewed workspace rules PR #37 permits an explicitly scoped retained-refusal
migration. The [source v1 decision](https://github.com/YouthOpps/data-pipeline/issues/373#issuecomment-6925600137)
and [World-only v2 amendment](https://github.com/YouthOpps/data-pipeline/issues/373#issuecomment-6941417638)
are source-committed literals. Schema 4 retains the complete original held
schema-3 bytes, original CIRC event and `blocked=true`. It preserves all MAX
constraints and unknown history within the same publisher family. The v2
extension binds the exact reviewed v1 generation and adds only `/fr/WBI-World`.

Admission is limited to the decision's explicit HTTPS `www.wbi.be` GET paths
and plain-slug programme/call details, with tightly bounded inventory queries.
The refused CIRC slug and node 25108 aliases remain denied regardless of
caller purpose, DNS trailing dot or recursive encoding. Ambiguous encodings,
unknown routes and unexplained state fail closed. `/actualites` returned 301
with `/fr/news` as its literal target during research; that target was never
requested and is outside the scope. This admission stop was not a new refusal.

Every new content 401/403/451, visible refusal or robots denial appends a new
durable globally restrictive event. It cannot be masked by the already-true
legacy Boolean or inherit the old event's approval. Event-union/MAX merge
retains all new events, and an unattributed held input adds a global veto.
Actual private transport controls verify new 403 persistence before body
reading and rejection after a fresh-process restart. No environment string
can approve migration or clear a hold.

## Accepted native evidence and production handoff

The original repaired live test at source SHA-256
`f2b80e070da2f7023e7519e97d25a657de050f78769db3dfa7f9844a5964908e`
passed in 556.562 seconds. All 25 physical responses were HTTP 200, covering
three complete inventory pages and 21 current details. The complete raw result
has 27 records; 26 remain after the explicit expired Hague call is removed.
The actual current website consumer accepted every raw and filtered ID, plus
the actual Hague-derived empty subset. All normalized fields independently
replayed from the complete captured bodies agree with the original result.
The observed physical gap was at least 12.201706 seconds and the inventory gap
at least 30.002241 seconds, with at most five starts per rolling minute.
All 146 native journal witnesses, the original raw responses and records remain
in private RAM with the unmodified original outcome. No automatic retake ran.

Independent review matched the complete sequence across the original failure,
bounded research and successful native run: 321 raw journal witnesses, 57
physical attempts and 46 cached-policy epochs, all with observed settlement.
The approved MAX candidate has 59 actual durable reservation timestamps,
not artificial starts inferred from observer or policy timestamps. It retains
all constraint maxima, unknown legacy history, the complete original CIRC
journal bytes and refusal event, and the World-only extension. Its canonical
SHA-256 is `afb4dfdf4a796db523e1f5c10cac913c1883bb095f476202f91376e1840def10`.

The source-local handoff is activated by the independently reviewed
[maintenance decision](https://github.com/YouthOpps/data-pipeline/issues/373#issuecomment-6942897042)
in `_WBI_HANDOFF_DECISION`. It binds the exact native revision,
complete witness digest and 14,630-byte payload. The original exact modern
owned-artifact decoder must succeed first; the handoff then joins its state by
MAX and event union before local creation/admission. It is never a replacement
for missing, expired or invalid modern ownership. The ancient artifactless
bootstrap cannot substitute for this modern recovery. Mature missing or corrupt
local journals remain fatal. Pending state and additional refusals cannot be
made healthy by the approved historical payload.

Private composed controls exercise original restore, bounded journal writes,
physical/policy paths, rolling pruning, original nine-key owned export and
strict decoding across four future-owner cycles. The first cycle uses the
actual original owned ZIP and 82 inert physical responses, followed by three
25-response cycles. Across 932 actual private journal saves, the maximum
journal is 14,725 bytes; the largest complete exported envelope is 15,538 bytes,
below the unchanged 16,384-byte decoder bound. Startup reservations stabilize
at 64 and the original physical pacing path prunes them to four or five; no
pruning semantics or refusal-history retention were changed. These are offline
capacity/negative controls, not extra publisher or production runs.

The existing workflow already supplies the required credentials/output path
and retains the exact attempt's original owned envelope. No new environment
seed, application command or workflow blob is required. Independent handoff
review passed, including all capacity and negative controls. Maintainer PR merge
and verification of the single automatic hosted publication/site refresh
remain required before issue closure.

An explicit French call for residency applications supplies evidence of an
opportunity even when the subject has no more specific catalogue category.
The original category and publisher article identity remain authoritative.

The residency verification carries its separately reviewed exact native budget
into the first production refresh after validating the newest canonical owned
artifact. The existing #373 MAX floor also applies. Later production attempts
validate the latest owned state and its inherited timing and refusal scope.
The verified native state never substitutes for an absent production archive.
