# StipendiumPlus

This standalone standard-library adapter collects eight genuine funding or
training identities from StipendiumPlus's own substantive public pages. The
publisher is the Arbeitsgemeinschaft der Begabtenförderungswerke der
Bundesrepublik Deutschland; its thirteen members administer distinct procedures.
The source is not thirteen cloned provider awards or a central application.

| Identity | Source-backed host projection | Application deadline/status |
| --- | --- | --- |
| Student scholarships | Unspecified; overseas support exists | Null / unknown |
| Doctoral scholarships | Germany, with qualified overseas exceptions | Null / unknown |
| Summer academy 2027 | Bad Staffelstein, Germany | Null / unknown |
| Historical summer academy 2025 | Heidelberg, Germany | Null / unknown |
| CHIN-KoBe | Known modules in Germany, China and Taiwan; nonexclusive | Null / unknown |
| Nie wieder!? | Unspecified venue | Null / unknown |
| Aufstiegsstipendium | Unspecified | Null / unknown |
| Apprentice scholarships/educational support | Unspecified; overseas possibilities | Null / unknown |

Student funding includes €300 monthly independent of income and conditional
maintenance up to €855 monthly considering parental and own income. Recognized
HEI enrollment, formal admission, performance, engagement and member values are
preserved. No Abitur or political-party/church membership is universally required.
Conditional insurance, family/childcare, nonrepayable/taxfree treatment and foreign
study support remain explicit in record evidence. The amounts are per-recipient
benefits, not consortium budgets or guaranteed combined income for every person.

Doctoral €1,750 monthly includes €100 research support, rather than adding it.
Any citizenship is eligible for a doctorate at a German HEI. Reasoned overseas
exceptions require German citizenship **or** an Abitur obtained in Germany;
cotutelle is generally possible. That condition is not a DE-only overall
citizenship whitelist. Record evidence retains degree-delay exceptions, medical
and second-doctorate limits, supervision/engagement, member procedures, side-work
alternatives, child/maternity extensions, prior-support accounting, and exclusions
of double funding, purely educational funding or an additional degree. The normal
3.5-year and child-related 4.5-year maximums are conditional, not blanket promises.

The upcoming academy's 5–10 September 2027 and historical academy's 17–22 August
2025 are **event dates**, not application deadlines or proof of open registration.
The current 2027 edition announces programme information and registration from
**mid-February 2027** through each foundation or SBB, with the framework programme
in July. That month-level statement does not establish an exact opening day,
current registration status or an application deadline; typed dates stay null.
The historical edition has its own actually linked dated/located public leaf,
funded-member audience and eleven concrete curriculum groups. Its title and
record evidence clearly identify it as historical. There is no evergreen academy
clone, guessed annual edition, capacity-based award or working-group clone.

CHIN-KoBe is for funded members of the thirteen works and SBB; especially suitable
fields are not exclusive admission rules. Its country projection represents known
programme modules, not every session's complete venue set. Nie wieder's scholar
seminars are distinct from public evenings. Aufstiegsstipendium is a separately
named federal first-degree scholarship for vocationally qualified professionals
with several years of experience; its own substantive paragraph shares the
student-benefits URL. No ordinary student/doctoral rates or nationality/host rules
are borrowed. Apprentice support is a genuine common cohort for people in
state-recognized vocational training with engagement, with member-dependent
scholarships or cooperation routes and mentoring/workshops/exchange. Missing
common amounts or a central application do not erase this real framework.

## Access and bounded source completeness

The adapter reads twenty-one complete own HTML pages: the home, imprint, all five
actual audience/member navigation hubs, student and doctoral benefits/eligibility/
application/FAQ leaves, academy parent and its actually linked 2027/2025 editions,
CHIN-KoBe, Nie wieder and the doctoral representation initiative. This finite
frontier identifies all actual own shared funding/training programmes without an
external SBB/ELES/CHIN-KoBe catalogue, thirteen member-provider crawl, guessed
archive years or sitemap-wide discovery. Member-fit advice, values, scholar
portraits, schools' advice/materials/ambassador outreach and the elected doctoral
representation initiative are not separate opportunities. The trainee page's
substantive framework and repeated Aufstiegsstipendium paragraph are handled
separately and deduplicated.

Material article facts, complete own-route inventories, canonical public source
URLs and policy navigation are reviewed fingerprints. For the 2027 academy, the
projection covers its verified title and complete programme body, including all
registration, audience, organization and venue paragraphs and material links.
Verified contact-only fields, breadcrumbs, related cards and footer presentation
are excluded through checked structural boundaries. New substantive text or
containers fail closed. Apart from the reviewed 2027 edition and academy parent,
the other nineteen page guards remain unchanged. Missing or changed required
pages, new routes or rights links fail closed for review; collection never
silently publishes a partial subset. Both apex and `www` links count as the
same publisher frontier; an unreviewed `www` navigation alias fails closed
rather than silently disappearing or changing canonical record URLs.
Images, scripts, styles, PDF media and
personal contact presentation are not opportunity facts. They are neither fetched
as alternative access nor copied into records.

The public robots policy disallows `/wp-admin/`, with an AJAX exception; the
adapter uses ordinary permitted public HTML rather than an invented AJAX/private
endpoint. Normal identifying User-Agent, inherited proxy and verified TLS remain
in use. Shared publisher-family locks retain the same local journal identity and
require physical starts at least 12.2 seconds apart and at most five in a rolling
minute, including literal robots, details, redirects and retries. The six actual
inventory hubs (home, works, academy, students, doctoral and trainees) additionally
require 30 seconds from each physical inventory response completion to the next
inventory request, including retries and redirects. Detail pages keep the normal
physical floor. Stricter applicable publisher directives remain effective.

An HTTP 429 stops the current collection, durably doubles retained physical and
inventory gaps and increases the exponential cooldown (at least 30 minutes),
while preserving longer valid Retry-After or existing embargoes. Timed rate limits
allow only a later bounded attempt after expiry. Actual content refusal or human
verification retains a separate hold. Literal robots-only 4xx responses other
than 429 represent unavailable REP, without clearing content holds. Invalid
Retry-After alone creates no refusal. Production and local collection must not
overlap; the source publication Action serializes hosted attempts.

The imprint protects copyrighted works and requires written consent for uses
beyond copyright limits; it permits copies only for private noncommercial use.
This is not an open licence or a stated blanket ban on independently authored
facts. Output contains original English factual summaries, attribution and source
links, with necessary numeric eligibility/event facts. No publisher descriptions,
images, raw HTML, PDFs, personal contact details or applicant information are
published. Changed access or rights evidence requires review.

## Execution and publication

Run the sole actual live nonpublishing collection test with:

```sh
python -B adapters/de-stipendiumplus/test_adapter.py
```

It validates nonempty real records and emits the complete JSON on stdout, with
diagnostics on stderr. It receives no publication credentials and creates no
record output files. The two Python files run when copied to an independent
folder. Source notes are confined to this folder; the repository root README is
unchanged.

The main-only source Action executes only:

```sh
python -B adapters/de-stipendiumplus/adapter.py --publish
```

Atomic non-forced publication touches only
`datas/de-stipendiumplus/{data,metadata}.json`. First failure creates no source
folder. Later failure preserves last-good data and success time, with failure
metadata when possible. A sibling-source conflict may be rebased; a newer update
to this source is never overwritten. Live tests and static checks do not certify
production or live-site appearance; the separate authorized maintainer performs
merge, source/site Actions and complete published-record verification.

## Expired-record cleanup

The source does not establish a reliable application timezone for every date-only deadline. Cleanup therefore uses UTC−12 as a conservative certainty bound: a calendar deadline expires only once its closing date has ended in every civil timezone. This does not invent a publisher closing clock. Offset-aware deadlines use their explicit instant.

After complete nonempty collection and validation, the refresh removes only
records with a validated deadline strictly before the successful check time.
Missing or ambiguous publisher deadlines remain normalized as null; status, age
and disappearance are never used as expiry evidence. Invalid normalized dates
fail validation and preserve last-good data. The source uses complete replacement rather than merging
old records, so expired previously published rows cannot survive a successful
refresh, including when they are still present in the publisher response.
Creation and first-seen history is retained for refreshed eligible identities.

The retained subset and success metadata are published in one source-scoped
atomic commit. A validated nonempty raw collection whose records all expire
publishes `[]` with success and a zero count; an empty upstream collection still
fails. A previously successful empty subset remains a valid last-good snapshot.
Fetch, parse, validation and publication failures preserve last-good data and
its success timestamp; only safe failure metadata may change. The sole live test
continues to emit the complete unfiltered publisher collection without publishing.

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
journal identities and existing stricter restrictions remain effective. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **23:45 UTC** (`45 23 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The finite collector has no numbered pagination. Its six genuine inventory hubs
use the separate completion guard described above; static programme detail pages
do not acquire an artificial inventory delay.

## Workflow concurrency

`youthopps-de-stipendiumplus-publisher-v1` serializes runs of this source. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Publication error diagnostics

Failed GitHub publication reports retain the HTTP method, a recognized API
route without its query, status and bounded attempt count. If checking the
remote outcome also fails, both failure contexts are retained. Diagnostics do
not include credentials, headers, response bodies or signed URLs. The existing
three-attempt limit, current-head and source-pair conflict checks, atomic writes
and successful collection/publication behavior are unchanged. This diagnostic
maintenance does not establish the underlying cause of older generic failures.

## Source display name

Published metadata uses `display_name` = **StipendiumPlus** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Reviewed state transition and bounded execution

The source-local adapter owns workflow history, exact completed job/attempt
identity validation, restoration and export of its inert pacing artifact. A
reviewed transition must preserve all known legacy completion upper bounds and
actual research/native journal constraints, unknown historical strike markers,
and a startup quiet guard of at least 60 seconds. Missing, stale or unbound state
fails closed; after the first new attempt, its exact owned artifact is mandatory,
including after a failed attempt. Older artifacts are never a fallback.

State restoration precedes the data-source snapshot read. Trusted state is
exported even if that read or later publication fails. The existing 30-minute
job cap stays unchanged; execution is limited to 29 minutes, collection to 21,
and the final 30 seconds are reserved for state export. The publication-only
GitHub token never enters the genuine nonpublishing native test.

The 2027 fact correction is based on the complete current official page. No
historical HTML body was available, so it does not assert what changed between
the earlier successful collection and the failed fingerprint. Full current
native coverage remains a separate acceptance gate.

The 2027 edition page specifies ten funded scholars per Werk and includes SBB
in the audience. The complete parent academy page explicitly states 140
participants for the 2027 event; the minimal record omits that capacity. Neither
page establishes an SBB allocation, and the record does not infer one.

## Local and hosted state continuity

Every local collection authenticates fresh complete workflow history, including
all actual completed attempts. Its exact workflow ID/path must be manually
paused before history is read and again after the startup quiet guard immediately
before the first publisher request. Queued or active family runs fail closed.
Maintainers must keep that workflow paused until the local collection finishes;
local runs never enable or dispatch it. Repeated collection entries in the same
process repeat these checks rather than trusting an earlier successful restore.

The first local transition is restricted to the exact 26 reviewed legacy runs,
complete jobs and zero-artifact inventories, and to an existing journal whose
complete research provenance matches the approved original bytes. It retains
current holds, counters and stricter timing constraints. It cannot create a fresh
allowance. Once any newer completed hosted attempt exists, local and hosted runs
both require its exact owned artifact, including after failure; a manual seed
cannot substitute for it. Missing or unavailable evidence stops before publisher
access. GitHub workflow reads use authenticated metadata-only access, distinct
from publisher requests; the publication-only DATA_SOURCE_TOKEN is absent from
native tests and publisher requests never carry authentication or cookies.

## Local and hosted continuity

Every local collection checks authenticated, fresh GitHub workflow history and
restores the newest exact owned pacing artifact, just as hosted collection does.
A local invocation does not invent a hosted run identity. It requires this
source's exact workflow to be paused (`disabled_manually`) and all source runs
to be complete; queued or active work fails before publisher access. The pause
and unchanged complete history are checked again after the startup quiet period,
immediately before the first publisher request. A second collection in the same
process repeats these checks and cannot reuse earlier trusted state.

Local execution therefore needs a GitHub credential for read-only workflow and
source-revision metadata operations, supplied securely as `GITHUB_TOKEN`. It does
not need `DATA_SOURCE_TOKEN`; never provide that publication credential to the
native test. A maintainer must keep the source workflow paused throughout local
collection and resume hosted automation afterward. Missing metadata access fails
closed instead of allowing an unverified publisher request.

Before the first migrated hosted attempt, the exact reviewed 26 historical jobs
and their empty artifact inventories are the sole legacy exception. The local
journal must preserve the authenticated original research provenance and all
current stricter timing, rate-limit and refusal fields. This exception never
creates an empty allowance, resets a hold or overrides a newer mature attempt;
a missing newest owned artifact after migration is a failure.

The parent academy guard covers the complete primary article and finite own-route
inventory, including both actually linked 2025 and 2027 editions. Its reviewed
current facts include all 13 Werke, the 2027 coexistence theme, 5–10 September
2027 in Bad Staffelstein, the explicit 140 participants and the sdw/Hanns-Seidel
organization with the other Werke and SBB. The previous parent body was not
retained, so the updated guard does not assert which historical text changed or
claim the difference was merely presentation. Twenty other page guards and the
normal factual parser remain unchanged from the complete retained retrieval.

A failed publisher-completion state write makes the publisher gate fail closed
for the rest of that process, including later collection entries. Export then
invalidates only a safely owned stale output and signals `pacing_ready=false`; it
cannot publish an older completion as this attempt's trusted pacing artifact.
Unsafe output paths are preserved and rejected. A later hosted or local run
therefore cannot fall back when its newest required artifact is missing.

## Complete final native validation

The final original native test passed with all 21 source HTML inputs, eight raw
records and eight current records. The actual current website consumer at
003d056e (`data.mjs` 2fabcc52) accepted all eight raw records, all eight filtered
records and a valid empty envelope. All eight records have unknown deadlines;
the empty envelope is a scenario, not a claim that the source is empty or that
these records have expired.

This run made 22 actual publisher requests including robots.txt, with at least
12.724 seconds between physical starts, at most five in a rolling minute and at
least 30.002 seconds between an inventory response completion and the next
inventory invocation. All actual response bytes, headers, stdout and 68 real
persisted budget snapshots were retained privately for independent review. No
source content or normalized record dataset was written to this repository.

Earlier invocations remain documented failures: one interrupted before any
publisher request, one stopped after three successful requests on an integration
error, and one retrieved all inputs but failed the parent academy guard. The
final run is a separate complete genuine test; retained-body offline replay is
not presented as a successful earlier native test. The complete known state
proposal retains 122 timing observations across historical job bounds, research,
failed and successful native reservations and wire starts. These observations
are not 122 physical requests. Historical refusal and 429 fields remain marked
unknown, and the observed counter covers only the reviewed transition onward.
Production publication and its complete state handoff require a separate
maintainer approval.

The maintainer approved the [complete production state transition](https://github.com/YouthOpps/data-pipeline/issues/216#issuecomment-6200363489)
after independent verification of the complete final native run and all known
state. The first hosted transition is limited to the exact 26 reviewed legacy
attempts; later attempts require their newest exact owned artifact. This approval
preserves the unknown historical fields and all 122 known timing observations;
it does not assert that hosted publication or site deployment has succeeded.

## Owned-state export failure guards

The source-local exporter invalidates safely owned stale JSON through a
validated descriptor before attempting any readiness signal. Signal open,
write or sync errors do not prevent independent cleanup attempts. A failed
export also applies this cleanup when state, run identity, implementation
validation or envelope construction fails, including a failure after a new
envelope replaces the old one. A failed signal together with failed unlink
cannot leave valid owned JSON eligible under a prior ready signal when
truncation succeeds; a failed truncation still attempts the false signal and
unlink. Every cleanup failure remains an error rather than an export success.

Artifact operations use the verified original parent descriptor and current
file identity. Replaced parents, symlinks, hard links, unsafe ownership or
permissions are refused without changing unrelated objects. Nonblocking opens
reject FIFO paths before any read can hang. Implementation hashing likewise
requires a safely owned regular file and does not follow symlinks.

[Issue #282](https://github.com/YouthOpps/data-pipeline/issues/282) verifies
these conditional paths with actual-function controls over private inert files.
Existing complete genuine native evidence qualifies only for the unchanged
ordinary collection and data flow; these controls are neither a new publisher
collection nor production execution. Parser, inventory, identifiers, dates,
publication payloads, tests and scheduled workflow remain unchanged. No retained
publisher hold, cooldown or source journal is reset by this maintenance.
