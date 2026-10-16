# Volkswagen AG youth opportunities

This standalone source covers Volkswagen AG's advertised German individual student, internship, doctoral, dual-study, apprenticeship, graduate and legal-trainee vacancies and their English aliases. It does not cover other Volkswagen Group brands or claim worldwide coverage. The HRD apprenticeship portal is excluded: its fresh robots policy disallows the portal, and the adapter never requests its jobfinder body.

Current opportunities are discovered from the literal Volkswagen AG search links advertised by the guarded official German and English career homepages. The adapter follows native pagination while retaining its advertised locale, verifies each page's range and total, rejects duplicate/missing pages and retrieves every individual detail, including excluded experienced/management listings and English aliases. The bounded frontier permits at most 200 German listings and 220 total details. All native company fields must identify Volkswagen AG; locations must explicitly identify Germany. Country-only locations remain country-only, and advertised truncated multi-location rows are resolved completely from the individual detail's native location metadata.

Native `customfield1` supplies the true JobID and stable source-hashed ID. A postal code in a URL's title slug is never a JobID. Native title, canonical URL, company, location and career-level fields must agree with discovery. English aliases are deduplicated by the true JobID and checked against the German opportunity's location and career level. Duplicate responsive descriptions must contain identical material text. Unknown career levels, changed routes, missing descriptions, contradictory facts or incomplete pagination fail the collection. An individual page's explicit German or English posting-expired notice is a withdrawal, so an expired old detail cannot freeze the remaining current inventory; an unexplained missing or inaccessible detail remains a failure.

On October 14, 2026, paced research retrieved seven German listing pages (167 rows), one English listing (nine aliases) and all 176 details. Five German JobIDs were experienced/management roles, one English page was their alias, and a German/English pair explicitly reported posting expiry. The resulting current inventory comprised 161 youth JobIDs: 96 training, 44 internships and 21 jobs. Seven live English aliases matched their German JobIDs. The earlier first-detail failure concerned true JobID **31008**, a doctoral listing whose page now explicitly states that its publication period expired; `34225` is its Baunatal postal code. These counts are research evidence, not constants required by collection.

The original 25 public fields keep their names and types. Current German records contain concise original German factual overviews with `summary_language: "de"`, not loss-prone translations of detailed qualification alternatives. Each overview is at most 600 UTF-16 units and directs readers to verify the official vacancy. It is not a complete eligibility statement. The compatible optional `requirements` string array preserves objective academic/technical criteria and application-document conditions, including required/preferred distinctions, conjunctions/disjunctions, credit thresholds, grades, languages, conditional permits and application materials. Each clause is a factual recap, not a mirror of creative introductions or task paragraphs. Nonspecific subjective personality descriptions are omitted consistently, independently of length. Bounds are 64 clauses and 2,000 UTF-16 units per clause; unparsed material or oversized output fails rather than silently truncating conditions. The current website accepts this extension but does not render it; the summary and official publisher link remain its visible overview. Earlier published 25-field records remain readable.

Pay, material benefits, date windows and title/start conflicts are extracted from explicit publisher clauses. Discovery's visible publication date has calendar-day precision, so `published_at` remains null; the adapter does not invent a publication instant from a display date. Explicit application windows populate `deadline`, status and the overview's opening date. Start dates and any conflicting title year remain explicit. Hidden `validThrough` describes posting lifespan and is not substituted for an application deadline. Germany as publisher or work location does not establish applicant citizenship eligibility; `eligible_countries` remains empty.

After complete nonempty raw retrieval and validation, successful publication
atomically replaces only this source's dataset with the refreshed,
expiry-filtered records and success metadata. Removed old records cannot
persist through that successful replacement. Explicit validated calendar
deadlines remain eligible through that UTC calendar day under the index's
date-only convention. This convention does not assert a publisher timezone or
invent a closing clock. Ambiguous or absent deadlines establish no expiry;
supported aware instants retain their exact closing instant and equality
remains eligible. Valid raw records whose deadlines have all passed produce an
atomic empty successful snapshot; subsequent reads accept that empty snapshot.
Empty retrieval, incomplete parsing, validation failure and publication
failure preserve last-good data and its success timestamp. Cleanup never
writes separately before the successful atomic refresh.

Official access and reuse remain guarded. Fresh robots for [jobs.volkswagen-group.com](https://jobs.volkswagen-group.com/robots.txt), [volkswagen-karriere.de](https://www.volkswagen-karriere.de/robots.txt) and [HRD](https://www.hrd-portal.com/robots.txt) precede documents. Both official career homepages and their linked [career terms](https://www.volkswagen-karriere.de/de/nutzungsbedingungen.html) and [job-portal terms](https://jobs.volkswagen-group.com/content/Nutzungsbedingungen/?locale=de_DE) retain their previously reviewed complete material/frontier fingerprints. October 14 research confirmed all seven policy/home inputs unchanged. The May 2026 career and January 2026 portal terms prohibit commercial systematic use without consent; that commercial qualifier remains material. This adapter collects minimal original noncommercial factual recaps and direct official opportunity links, without creative description mirrors, images, applications, profiles or guessed APIs.

The #316 candidate's durable family budget applies at least 12.2 seconds from
physical completion to the next start and at most five starts in a rolling
minute across publisher endpoints, retries and redirects. Inventory hops also
retain the separate 30-second guard. Stronger native intervals, MAX embargoes,
union starts and actual refusal holds survive restarts. Ordinary 429 responses
save a timed adaptive stop; actual content 401/403/451 and genuine challenges
retain access holds. Missing mature or unresolved state fails closed. Caps stay
240 starts, 8 MiB per response and 64 MiB total, with the original 4,320-second
run and 2,400-second collection budgets. The final section describes the exact
protocol and the currently absent access approvals.

The newest exact completed attempt's owned pacing artifact is mandatory,
including failed attempts. Missing/expired newest artifacts never select an
older artifact. The [issue #117 handoff](https://github.com/YouthOpps/data-pipeline/issues/117#issuecomment-6102086900)
is historical first-production evidence, not a modern empty-state grant.
The #316 LOCAL boundary is now bound as documented below; HOSTED and mature
reentry literals remain `None`. The historical
four-key bootstrap environment value cannot initialize this mature source.
Artifact export requires healthy settled state and the current step's positive
output signal; pending or failed persistence cannot upload a healthy envelope.

Every GitHub publication request obtains or renews a repository-scoped installation token through the configured App. The matching workflow discovers the `YouthOpps/data-source` installation using `actions/create-github-app-token@v2`, supplies its authoritative installation ID and preserves native JIT renewal; it does not pass the action's temporary token as a static fallback. Atomic source-scoped CAS writes affect only `datas/volkswagen-group/{data,metadata}.json`. Initial failure creates no folder; later failure preserves validated last-good data and success/check timestamps when safe failure metadata can be written. Creation/first-seen/last-seen use attempt start, last-checked uses successful end, and updated-at advances only on a material change. The daily schedule is staggered with other sources. Push routing, manual dispatch, repository/main guard and serialized family publication remain unchanged. Maintainer merge starts production; implementation does not dispatch Actions or publish.

Run the sole real nonpublishing test from the repository root:

```sh
python -B adapters/volkswagen-group/test_adapter.py
```

It retrieves and validates the entire nonempty current collection, emits its complete JSON on stdout and sends diagnostics to stderr. It needs no publication credentials or third-party packages, but the #316
candidate also requires the separate reviewed continuity and access release.
A copied adapter folder runs independently with the Python standard library. Live evidence must be consumed and validated in memory without writing dataset files. Publication behavior is reviewed without invoking production.

Issue: [#161](https://github.com/YouthOpps/data-pipeline/issues/161). Historical source and durable-state decisions: [#117](https://github.com/YouthOpps/data-pipeline/issues/117). Independent acceptance and maintainer post-merge publication/site validation remain required; research and nonpublishing tests do not claim production publication.

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
family identity and retained stronger restrictions are preserved. The #316
pacing change is described below. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **00:10 UTC** (`10 0 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

In the #316 candidate, inventory continuations and partitions retain at least
30 seconds after the previous inventory completion, including each physical
redirect or original bounded server-error retry. Robots and details retain the
12.2-second physical completion floor without inheriting the inventory-only
page guard. Stronger publisher timing, embargoes and retained backoff take
precedence. Inventory scope, parsing, validation and atomic publication are
unchanged.

## Source display name

Published metadata uses `display_name` = **Volkswagen** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.



## Historical journal ownership maintenance (#295)

This section records the delivered IO-only baseline before #316. Its unchanged
schema/floors and absent-pending notes describe that historical maintenance,
not the later offline pacing candidate described below.

Journal reads and commits retain the genuine current read/write file descriptor
under the original flock authority. Parent, current pathname, acquired
identities and observed generations are checked before use, publisher opening
and owned export. Writes serialize in memory, then truncate/fsync and fully
write/read back that original inode. A changed object is preserved and access
stops; no stale pathname is reopened for destructive cleanup.

IO failures latch independently of existing transport flags. Diagnostic state
copies are best effort after the latch. Journal, lock, collection, artifact
writer/readback, output and parent descriptors retain acquisition identity
through cleanup. Both independent lifetime releases are attempted; a primary
error is preserved and a healthy close failure cannot return a healthy owned
export. Genuine verified incoming-state restoration is the only new-journal
initialization authority. Missing mature state is not an empty-state reset.

In-place interruption can sacrifice availability: empty or partial mature state
stops. Advisory locks and observed identity/generation checks are not filesystem
CAS or arbitrary same-user authentication. Existing schemas have no new durable
pending barrier; total persistence plus invalidation failure across restart
remains separate #273 work.

Original facts, IDs, graph, parser, dates, expiry, metadata, publication,
retry/redirect/REP/status interpretation, actual holds, schema, seeds, approval
markers, timing floors, caps, tests and workflow remain unchanged. This is
conditional IO maintenance, not pacing modernization, access approval or hold
recovery.

Both original optional legacy research paths keep their original read-only
locks, parser, MAX and missing-path behavior. Acquired
path/descriptor/generation checks protect those same inputs. No legacy file is
rewritten and no missing legacy file is interpreted as a fabricated hold or
empty history.

No complete original successful native/independent full-record chain is
established by this maintenance. Existing published counts or production success
do not replace that missing evidence, and no original raw-RAM availability or
replay is invented.

A separate current immutable PUBLISHED snapshot qualification at
data11536bfa/websiteaa265850 used consumer2fabcc52: 158 published and 158
expiry-filtered records, derived empty and last-good failure cases passed. This
checks published contracts and consumer compatibility, not current upstream
facts, access or original full-inventory completeness.

Validation is limited to bounded ordinary private
entry/restore/request/export/lifecycle and original retry/redirect/legacy cases,
plus whole-source protected business-AST and original envelope/literal
comparison. No advanced descriptor campaign, publisher/native request, actual
journal operation, ZIP download or workflow action is performed by the author.
Independent exact-revision review is required before delivery. Refs #295. Issue
#295 remains open.

## Durable publisher pacing migration (#316)

The candidate retains the complete original Volkswagen AG German/English
inventory, pagination, detail and alias reconciliation. IDs, supported facts,
date strings, exclusions, conditional refresh, publication and App renewal are
unchanged. The separately reviewed pure calendar policy correction is described
below. The original live test is unchanged. The matching workflow changes
only its separately approved job timeout, as described below.
The original source revision `94aa76c3` passed one complete nonpublishing
native test under the separate maintainer execution release. Its actual full
inputs and outputs remain immutable. The subsequent three-unit correction
below is qualified by offline replay; no second native is claimed. An older
published count is not a physical request count or a current frontier bound.

Every publisher physical start, including a redirect or the original bounded
server-error retry, uses the same durable family budget: at least 12.2 seconds
from previous physical completion, at most five starts per rolling 60 seconds,
and 30 seconds from prior inventory completion for each physical inventory
hop. Robots and ordinary details use the physical floor. Each collection also
waits at least 60 seconds after startup. A 429 saves its counter, doubles the
CURRENT physical and inventory gaps, and retains at least an exponentially
increasing 1,800-second embargo, or a longer valid `Retry-After`, before
closing the response and stopping without any body read or immediate retry.
Any failed close retains unresolved pending ownership and the sticky gate; a
technical failure is not recast as a publisher denial. An unusable
`Retry-After` alone does not create an access hold on any status.

Actual content 401, 403 or 451 and genuine visible human-verification responses
retain an access hold. Literal robots 4xx other than 429 represents unavailable
REP, including an HTML refusal body; robots 5xx, TLS errors and 429 stop. The
original full material guards and page parser remain additional requirements.
REP selects the exact current product group, or its wildcard fallback, and
independently intersects actual legacy-specific restrictions.
Applicable decimal delays and request rates strengthen the physical interval;
malformed applicable timing stops under a durable unresolved transaction.
Required-target disallow is retained by target hash and fails the complete
collection. It does not invent a global ban on unrelated allowed routes or
turn the excluded HRD portal into a required content route. A retained target
denial survives unavailable, invalid, stale or unscoped replacement REP. A fresh
valid applicable policy allowing that target may supersede it; the historical
denial witness remains. Actual content holds are never cleared by REP Allow.
Cached decisions require current-collection raw-body/parser provenance.

Schema 3 saves an unresolved owner before any publisher transaction, and an
exact current policy target before evaluating either fresh or cached REP.
Its actual owner/body/target/decision receipt must be durably present before
settlement, including a nested redirect assessment. Historical allowed witnesses
are nonauthoritative canonical-min samples, at most one per fixed origin; they
never supply permission, freshness or settlement. Scoped denials, stronger
numeric restrictions, existing holds and historical uncertainty survive joins.
Unresolved state, unsupported arithmetic or the original 16-KiB state/envelope
bound stops access; no denial is dropped and no frontier is shortened to fit.
The original ZIP bound remains 32 KiB.

The finite LOCAL schema-1 transition literal is bound to the explicit reviewed
boundary below. The separately approved HOSTED boundary is documented below;
the mature-LOCAL reentry literal remains `None`. The LOCAL approval binds
complete fresh exact-attempt history and jobs, newest owned artifact digest and
producing source, full known MAX seed, and the exact actual LOCAL parent,
journal and lock bytes and identities. The historical
bootstrap environment value is not authority for modern empty state. LOCAL
absence, a mature missing/corrupt journal, a newer queued attempt, or a missing
newest owned artifact never falls back to older allowance. HOSTED authenticates
the actual standard matching-workflow context and current job before state
initialization; it does not apply LOCAL inode pins to a new runner. Fresh
coordination is checked under exclusive namespace authority before commit and
again after quiet immediately before the first wire. A later settled modern
owned artifact follows the normal strict restore path without a legacy grant.

The original Group E retained RW journal/current-generation, safe owned output
and independent lifecycle cleanup remain in use. Locks are advisory. No claim
is made against an arbitrary same-UID writer ignoring them. A failed in-place
write may sacrifice availability; empty, partial or replaced mature state is
rejected. Durable unresolved state prevents an older owned artifact from
silently restoring fresh allowance. Policy-only transactions do not count as
physical starts.

The original request and byte caps remain: 240 publisher starts, 64 MiB total
and 8 MiB per response. Before any new native run, the maintainer approved
7,200 seconds for collection, a 9,120-second whole entry and a 155-minute job
to accommodate slower traffic. Publication and failure handling retain their
separate 900-second allocations; export retains 120 seconds, with 180 seconds
of outer job headroom. These phases keep the same nonrenewing whole deadline.
GitHub retains its original 15-second socket limit, three CAS attempts and
just-in-time App token renewal; no new credential variable is required.

Original admission still requires 120 seconds of remaining source and phase
time even without a wait, with a current-time check before a publisher wire.
The static zero-response-time model reaches 3,219.4 seconds including that
reserve for the nominal full graph, or 5,712.4 for the broader admitted retry/
redirect envelope. The new source allocation leaves 1,487.6 seconds beyond the
latter planning floor for setup and other costs. This is advance allocation
engineering, not measured complete-native fit or permission for stronger REP
to be ignored. This was an advance planning envelope, not a complete-native fit proof.
The separately released original native subsequently completed as recorded
below; stronger future policies or a different full frontier may still fail
closed within the fixed allocation. No partial success
is published, and any separately authorized original native failure must be
retained without an automatic repeat. The preaccess checks were bounded ordinary private transport, metadata,
inert ZIP and static checks. The later original native and offline correction
have distinct provenance below.

### Reviewed LOCAL boundary and separately released original native

The finite LOCAL transition literal is bound to the maintainer's
[explicit boundary](https://github.com/YouthOpps/data-pipeline/issues/316#issuecomment-6210612968).
Its exact seed SHA256 is
`adefe5478df2084975251190f450c6501d88e659acaa5cd57d12750b07b9b49c`.
The actual retained namespace is PRESENT schema 1, with original raw hash
`63710a5802ab4eb634cfe8f4c3a4f852446614f4479185af235e1157b57194f5`
and exact existing directory/state/lock pins. The separately authenticated
newest owned run is 38850140544/1, artifact 12026041596. Its legacy six-key
envelope has no embedded head/code field; owner metadata and the immutable
producing source establish that qualified provenance. The root cached ZIP was
reused; no second diagnostic download is implied.

The explicit age fold covers only 29 individually verified observations strictly
older than the observed 60-second boundary: nine LOCAL starts, nine owned starts
and eleven completed-job upper bounds. They are not 29 measured requests. Full
unabridged external timing and typed ledgers remain retained; zero unaged
runtime points does not mean zero history. All three historical unknown flags
remain true. Prospective settlement is a specific approved schema transition,
not evidence that absent legacy pending fields proved historical settlement.

The LOCAL literal remains bound to its original approval. Exact fresh history,
owner, pins and MAX checks under the original locks
remain mandatory before transition, followed by fresh post-quiet first-wire
checks. The LOCAL boundary itself did not grant execution. A subsequent separate
maintainer release authorized exactly the original native below. The later
HOSTED approval below is separate; mature-LOCAL reentry remains unset. No
automatic native repeat is authorized, and no actual namespace was modified
while binding either literal.


## Original native and qualified three-unit correction

The maintainer's [separate original-test release](https://github.com/YouthOpps/data-pipeline/issues/316#issuecomment-6210877305)
ran the unchanged test against source `94aa76c3` and observer `7aa07352`.
The original test and controller both passed: 187 complete physical responses,
seven guarded static inputs, eight listing pages and 171 detail responses
produced 158 records, all current at that reference clock. The detail census
includes seven qualifying English aliases and six excluded detail responses
representing five unique non-youth JobIDs. No dynamic detail response was
classified as an explicit withdrawal. One original 503-to-200 bounded retry
shares its assessed owner. All 1,315 persistence calls with exact before/after
bytes and 186 sequential owner settlements remain in private RAM.
The original elapsed time was 3,027 seconds within the approved 7,200-second
collection allocation. This is a LOCAL nonpublishing result, not hosted
publication or a guarantee about a future frontier.

Actual response closure to the next first HTTP send measured at least
12.2049 seconds; inventory response closure to the next inventory send measured
at least 30.0306 seconds, with at most five starts in a rolling minute. The
observer's later physical-context exit includes state/policy bookkeeping and
is not relabelled as response closure. First HTTP send may include proxy
establishment; headers were retained but header-arrival time was not separately
measured. A body-read completion timestamp is only an upper bound on header
availability.

All 95 dated records derive their calendar window from their own primary
application clause, closing on 30 April 2027; the other 63 have no supported
closing window and retain null deadlines. The original UTC-12 comparison would
retain those 95 after the UTC calendar day ended. The working source changes
only `make_record` and `remove_expired_records` to the index's UTC calendar
convention: all 95 remain eligible through 30 April and expire at the following
UTC midnight, while the 63 null records remain. This is not a claim of an
explicit publisher closing timezone or clock. Calendar strings, aware-instant
comparison/equality, ambiguous values and every other field are preserved.

The third changed function, `_transport_bytes`, persists the existing adaptive
429 MAX and stops before any body read. Any response-close exception leaves
the owner unresolved and prevents another request; a successful close permits
only the existing assessed timed stop. No non-429 transport, parser, frontier,
publication, App renewal, phase limit or LOCAL literal changes. The corrected
source replays all 187 retained inputs to the same full 158 current records.
Focused private calendar, offset, null, aware-instant and prebody/close controls
qualify these changes; the corrected source was not used in a second native.

The independently matching full MAX retains 2,475 typed observations and 2,280
unique timing points, not 2,475 requests. Its explicit strict-age proposal keeps
all provenance and stronger numeric/hold constraints, removing only points
strictly older than the fixed cutoff. All old uncertainty flags remain true;
prospective settled state is justified by the complete exact nonce/policy
chain, not by clearing historical pending flags. Full MAX and any normalized
runtime candidate are separately approved by the public HOSTED boundary below.
Neither the actual mature journal nor the original native evidence is rewritten.


## Reviewed first HOSTED continuity boundary

The maintainer's [separate HOSTED approval](https://github.com/YouthOpps/data-pipeline/issues/316#issuecomment-6212633235)
binds the exact nine-key HOSTED literal to the authenticated original owner
`38850140544/1`, head `12f61ef6`, artifact `12026041596` and its SHA-256 digest.
The six-key legacy envelope has no embedded head/code hash; authenticated
GitHub metadata and immutable producer bytes provide that external binding.
HOSTED `local_pins` is null; actual LOCAL inode identities are not transferred
to a future hosted runner. The original LOCAL approval remains unchanged, and
the mature-LOCAL reentry literal stays unset.

Full MAX `d6f43184` and runtime seed `88fda0e9` preserve all 2,475 typed
observations and 2,280 unique historical timing points in the retained evidence.
Only points strictly older than the reviewed cutoff are folded out of runtime
starts; the cap ten is unchanged. All three ancestry-unknown flags remain true.
Exact owner/policy settlements justify the approved settled seed. Fresh hosted
preparation must still retain stronger state, wait at least sixty seconds and
verify current owner/history and policy before its first publisher request.
Missing newest owned state or an unresolved owner stops access.

The matching workflow retains its original four-key
`VOLKSWAGEN_GROUP_PACING_BOOTSTRAP`, App renewal inputs and artifact path. There
is no separate mandatory maintenance environment variable. First legacy and
later modern hosted restores require the actual standard workflow context and
credentials. Offline matching-workflow/main checks qualify those paths; they do
not claim another native collection or hosted production result. ROOT alone
controls activation, the reviewed merge and its first automatic production run.
