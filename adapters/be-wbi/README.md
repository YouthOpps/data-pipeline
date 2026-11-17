# Wallonie-Bruxelles International

This independently runnable, standard-library adapter collects WBI's own
published funding, training and paid-placement facts. It does not collect
applications, images, external provider catalogues, account pages or search.

Run the sole non-publishing live test with Python 3.12:

```sh
python -B test_adapter.py
```

The complete actual JSON goes to stdout; diagnostics go to stderr. Publication
is limited to the matching authorized main-branch Action and source folder.

## Reviewed inventory and identity

The education catalogue has two pages (10 and 5 cards). Four research taxonomy
indices supplement it, giving 18 distinct own service leaves. Three advice or
networking services are excluded because they offer no own award. The resulting
21 genuine identities comprise 15 programmes, plus two additional WORLD award
types, incoming/outgoing Mobility Fund routes, an additional Excellence IN type,
the separately named Québec tuition exemption and the current SEE internship.

WORLD doctoral, postdoctoral and short awards have distinct published durations,
renewals and lifetime entitlements despite sharing the same actual URL and form.
Excellence IN long/short awards similarly have distinct award entitlements.
Mobility Fund IN/OUT are institutional grants: FWB institutions submit and receive
funds; incoming foreign staff are beneficiaries, not direct applicants. The
outgoing permanent FWB employment requirement is not a citizenship restriction
on incoming beneficiaries. PHC Tournesol is also institutional cooperation.

The company and Louisiana articles supplement their service pages; they do not
create duplicate awards. The named SEE internship differs from the CIRC pilot
postdoctoral scholarship. Country variants, three Hague places, programme
capacity, yearly rates, language aliases and external directories are not
separate opportunities. The obsolete language alias returns 404; the substantive
current own language leaf is the reviewed source.

## Eligibility, destinations and benefits

FWB affiliation, qualifications and residence are not Belgian citizenship.
Only the separately named Québec fee exemption has explicit Belgian eligibility.
Host countries come from actual programme destinations. WORLD, EXPLORT and the
nonexhaustive international-organisation programme have no artificial complete
country whitelist. Company internships are abroad: WBI says EU27 priority but
includes Belgium, so the known projection contains the other 26 countries; the
source inconsistency and unspecified conditional border destinations remain in
record evidence. Destination countries never become citizenship eligibility.

WORLD's €2,120/month and CIRC's €3,000/month are mobility scholarships, not salary.
WORLD's conditional registration/training limit is €835. WORLD parenthood
extensions are 15/12 months under the stated circumstances; CIRC separately uses
one year per childbirth/adoption. Excellence IN permits pending PhD completion
with timely proof; its July/December proof dates are not application deadlines.
PHC supports up to €2,000/year for two years with published expense conditions.
Hague applicants must have completed some study at an FWB-funded institution.

The Louisiana source conflicts on one-year residence (FWB versus Belgium),
preserved in evidence. Quoted salaries belong explicitly to 2026–27; they are
not presented as a verified 2027–28 pay schedule. Company support is monthly,
with 50% travel support capped at €500. Its current call's research/doctoral
exceptions are preserved rather than inferred to apply to all programmes.

## Dates and change protection

Three actual dated calls close November 1, November 13 and December 31, 2026.
Their raw 23h59/midnight wording lacks a time zone. Deadlines therefore remain
calendar dates: open before that date, unknown on the date, expired after it.
The consumer's date-only sorting convention does not establish a publisher UTC
closing instant. Hague's January 31, 2026 call is collected as expired and removed from a successful refreshed publication.

The other 17 records have no actual call year and remain unknown with null
normalized deadlines. Annual day/month windows, even those specifying UTC+1 or
UTC+2, do not justify inventing a year or closing clock. Event registration,
interview dates and pending-degree proof dates are not award deadlines.

Every required catalogue page, leaf, current article and legal article must be
complete and match the reviewed factual fingerprint. Changes fail closed for
review; no source-year rollover or partial inventory is silently published.
Only the publisher's transient `Last edited il y a N minutes/heures/jours` UI
text is removed before comparison; surrounding substantive text remains.

## Permitted access and publication

[WBI's legal notice](https://www.wbi.be/fr/mentions-legales-0) permits attributed
textual/numerical information for noncommercial, nonadvertising use. This is not
an unrestricted open licence. Own English factual summaries and source links
are published with WBI attribution; images, framing and full prose are excluded.
Commercial/advertising use or other protected elements require prior permission
from `multimedia[@]wbi.be` under the actual notice. No publisher contact is sent.

Robots is checked before each endpoint, including redirects. All WBI endpoints
share one source-family cross-process budget: at least 12.2 seconds after the
previous physical response completes and at most five starts per rolling
60 seconds, including robots, retries and redirects. Stricter applicable REP
and retained Retry-After limits take precedence. The identifying user agent, normal TLS and
session proxy remain enabled. Remote publishers must not overlap locally run
WBI tests; the publication Action uses this source's serialized group.

Atomic, non-forced commits update only this source's data/metadata pair. First
failure creates no source folder. Later failure preserves the last good data and
success timestamp, recording sanitized failure metadata when safe. Concurrent
sibling commits can be rebased; newer data from this same source is never
force-overwritten.

## Expired-record cleanup

The source does not establish a reliable application timezone for every
date-only deadline. Cleanup retains the full closing UTC calendar day and
expires that date at the following UTC midnight, matching record status and
the shared calendar-date contract. This is a normalization convention, not
an assertion of a publisher closing clock or timezone. Offset-aware deadlines
continue to use their explicit instant.

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
access; restrictive rate and delay directives remain effective. The advertised
identity is preserved; the source-local pacing and continuity changes are
described below. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **07:11 UTC** (`11 7 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Each of the two education catalogue pages and four research taxonomy pages
is inventory. Every physical exchange for those six inputs, including a
redirect or permitted connection retry, waits at least 30 seconds after the
previous inventory exchange completes. Robots and service/article details
retain the physical 12.2-second floor. The inventory guard does not promote
all detail pages or change the fixed 28-input reader. Stronger retained page
and physical gaps always take precedence.

## Workflow concurrency

`youthopps-be-wbi-publisher-v1` serializes runs of this source. Up to 100
pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Wallonie-Bruxelles International** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Bounded publication error categories

GitHub publication failures retain the known route, HTTP status and bounded
attempt count. A bounded JSON error view emits only fixed known message/code
labels; arbitrary bodies, headers, credentials, URLs and query values are never
copied into diagnostics. The non-fast-forward label requires the exact known
GitHub message on an HTTP 422 PATCH of the main reference; HTTP 422 alone does
not establish a conflict. Malformed, ambiguous or unknown errors remain
unclassified. These diagnostics do not prove the cause of historical failures.

Collection, source fields, access holds, pacing, the existing three publication
attempts, source-pair checks and non-forced atomic writes are unchanged by those
diagnostic categories. Those categories alone add no retry delay, workflow
activation or publication test.

## Bounded publication-conflict retries

Hosted publication proves the exact current job, run attempt, source workflow,
matching head/workflow SHA, main branch, assigned runner and UTC start before
data-source access. A fresh GitHub Date maps the existing 30-minute job bound
to one conservative monotonic deadline; retries cannot renew it. The pinned
App token is minted after job start and expires after one hour, so no token
renewal is needed. Missing or inconsistent proof fails closed.

Every guarded publication call and the current-job proof call on the reviewed
Ubuntu/Python path has a total alarm guard of at most 30 seconds, plus a
remaining-time socket timeout, including its bounded body read. Existing or
unsupported timers stop before transport. Writes and retry waits stop 60
seconds before the job deadline, final outcome reads stop 30 seconds before
it, leaving a cleanup/export allowance. These publication reserves do not
guarantee export after an overlong collection, runner death or an
uninterruptible native call; the existing job timeout remains the outer bound.
No API call receives a fresh deadline.

Only an actual, classified non-fast-forward reference response can retry,
after checking that the desired outcome has not landed and this source pair is
still unchanged. The first two retries add 1–2 and 2–4 seconds after the
ordinary GitHub pacing gate, before reserving the actual request start, within
the existing three attempts and original write deadline. Authentication,
non-CAS and unclassified failures stop without another write attempt. Terminal
publication errors latch a per-process write stop, including later
failure-metadata fallback; bounded read-only outcome checks remain available.
Source-pair changes stay protected; reference writes keep force:false. Source
collection, fields, pacing, schedule and the 30-minute job cap were unchanged
by those publication-conflict safeguards. Validation used inert controls; no
source fetch, GitHub test write or production invocation is claimed.


## Source-local pacing and continuity migration (#312)

The complete fixed reader still retrieves all 28 required inputs and keeps the
same 21 reviewed profiles, source-fact guards, identifiers, parser, date/status
rules and expiry helper. No partial snapshot, request-count truncation or
invented rollover is permitted. The source does not expose a verified change
feed or complete conditional reconciliation, so a known programme is not a
safe collection stop. The original sole live test is unchanged.

Before reading a publisher body, a received HTTP 429 durably records its
observed counter, doubles the CURRENT retained physical and inventory gaps,
and retains the maximum of the existing embargo, valid Retry-After and an
exponential cooldown beginning at 1,800 seconds. It stops this collection.
An ordinary fully assessed rate response is a timed restriction; unresolved
body or policy assessment remains pending rather than becoming an invented
publisher denial. Invalid Retry-After alone creates no hold.

A schema-3 source-local journal records pending before a physical request or
cached target-policy assessment. Pending settles only after actual body,
access and REP constraints and completion have been persisted. Received-body,
TLS, local storage and unknown opener errors remain unresolved and stop.
The original two attempts for typed ordinary pre-response connection failure
remain; there is no new 503 retry. Current-identity REP groups use their own
specific-or-wildcard selection, intersected with actual legacy youthopp groups
when present. Applicable finite decimal Crawl-delay and Request-rate retain
the strongest limits; unsupported applicable directives stop assessment.

Writes use the retained locked current journal descriptor and verified parent.
Persistence or cleanup failure is sticky, preserves stronger known state,
refuses reuse and suppresses healthy owned export. Safe owned output is
invalidated before fallible output signalling; unsafe objects remain untouched.
The modern journal is separate from the original `wbi-publisher-pacing-UID`
namespace. The baseline collector had no collection lock; the new lifecycle
lock must not be described as recovered historical ownership.

Every collection entry first checks complete fresh family workflow history,
exact attempts/jobs and newest owned state. Hosted entry authenticates the
real current repository, main branch, source workflow/head, run/attempt and
single running collect job before trusting or initializing modern state.
The matching hosted workflow must supply the read credential and owned-state
output configuration; missing required configuration stops before state init.
Local entry requires the actual source workflow to be manually paused before
initialization and again before the first publisher exchange after startup
quiet. Mature missing or invalid newest owned state fails without older
fallback. Envelopes bind source, repository, run, attempt, head and adapter
and state hashes; the workflow retains the exact attempt's owned state even
on a recoverable failure when safe export succeeds.

The 27 recovered historical attempts have complete exact jobs and empty owned
artifact inventories. They do not establish historical zero calls, refusals
or 429s. No complete prior native evidence chain has been located. Legacy
local and modern namespaces were independently checked read-only by the
maintainer and were genuinely absent; this is not zero historical activity.
The LOCAL literal now binds the exact full-27-job MAX and unknown-history
state approved in [the finite LOCAL boundary](https://github.com/YouthOpps/data-pipeline/issues/312#issuecomment-6209076636).
The HOSTED literal now binds the separately approved full native MAX under
[the conditional first-HOSTED boundary](https://github.com/YouthOpps/data-pipeline/issues/312#issuecomment-6209686521).
The original LOCAL literal remains unchanged; newer actual local state is
retained by the existing continuity rules. These configuration approvals do
not enable a workflow or perform publication.
All known job-completion upper bounds and unlogged access/rate uncertainty
remain retained; the observed-since-migration counter is not a lifetime
counter. No generic empty-state bootstrap or automatic hold clearing exists.

### One bounded entry and preserved publication

The existing 1,800-second job remains the outer bound. New, stricter admission
limits freeze one 1,740-second entry before metadata work. Restore and its
current-job authentication have at most 180 seconds. The ORIGINAL 30-second
publication proof follows restore before a data-source snapshot or publisher
access; proof failure permits neither publisher access nor healthy export.
The snapshot owner has at most 120 seconds. All setup consumes the same entry.
The source owner has at most 1,200 seconds and stops early enough to reserve
180 seconds for publication, 180 for failure reporting and 30 for export.
Phase deadlines do not renew; failure ownership starts before the first
fallback read. The original authentic job proof can only tighten entry,
source and tail limits. No outer alarm competes with the existing per-call
publication alarm. These are admission and bounded IO limits, not a claim
that arbitrary parsing or every worst-case route fits the job.

The fixed graph has a nominal 29 physical requests with one cached robots
response; actual redirects/retries can add exchanges. The loose 1,792-request
uncached policy envelope cannot be certified to fit. Full collection must
finish within its real remaining budget or preserve last-good data. The
original GitHub six-second pacing, Retry-After, three bounded publication
attempts, source-pair checks and non-forced atomic CAS bodies are unchanged.

Offline controls use private files, fabricated response IO and inert owned
ZIPs. Synthetic source-bound consumer readiness checks the actual ISO-string
expiry and metadata contracts, including wrong-record and wrong-metadata
source rejection. The native test and observer remain separately attributed;
these controls are not current publisher facts, a genuine native pass or
production validation. No publisher request, actual namespace write,
workflow activation or publication is part of this offline implementation.

### Original native and qualified calendar correction

The separately released original test at source `a7ab192a` passed once, with
all 28 inputs, 29 complete physical responses and 21 raw records. Its
617.195-second measured interval, six inventory requests and 170 successful
byte-verified saves are retained with the original receipt and private RAM.
The actual result was 20 current records; the one genuinely expired Hague
record also proves the derived empty subset. This measured local execution
does not establish a hosted publication or a worst-case duration guarantee.

The retained source clauses support four calendar deadlines and 17 null
deadlines. Louisiana and the company and CIRC SEE calls publish closing
clocks without a timezone; the calendar projection preserves that uncertainty.
Yearless recurring application dates remain null; event and proof deadlines
do not supply application years. After the genuine test, only the pure
date-only cleanup branch was aligned from UTC−12 to UTC calendar closure.
Offline replay of every actual input preserves all 21 records and their
fields, with the current 20/one-expired result. Closing-day, next-midnight,
offset, aware-instant and null controls qualify this correction; the
corrected helper was not executed in a second native collection.
The original receipt and controller remain unchanged.

Full native MAX proposal retains all 27 ancestral job-completion upper bounds
and 116 actual timing observations, not 143 publisher requests. All 170
requested and exact before/after byte witnesses plus initial, final and
stronger RAM constraints support the proposal. Its 56 sequential boolean
pending scopes cover 29 physical exchanges and 27 policy-only assessments.
This schema has no persisted request nonce or owner identity; settlement is
qualified by the actual sequential byte/save ledger and collection lock.
Legacy access and rate uncertainty remain retained. The separately approved
HOSTED configuration is bound below; production publication remains a
separate maintainer-controlled gate.

### Conditional first-HOSTED full-MAX binding

The HOSTED literal binds 143 timing observations and the reviewed full MAX
`27636221`, including all 27 actual ancestral job-completion upper bounds.
It is derived from every initial, requested, before/after and final raw-byte
witness and stronger RAM constraints, with the reviewed quiet guard. The
original LOCAL literal still represents the earlier 27-bound initialization;
it is not relabelled as the later full native MAX. All 170 raw persistence
witnesses remain in retained private evidence, not copied into the literal.

The first hosted bridge requires the exact reviewed 27-attempt legacy
history, no owned artifacts, genuinely absent modern state and the original
legacy-namespace ABSENT observation `b82af02c`. It does not transfer the
LOCAL journal inode: the actual local modern journal is present, inode
481247 and raw SHA starting `2cde1a78`. On subsequent runs, the newest exact
modern owned artifact is mandatory. Missing mature local state, missing or
expired newest ownership, active coordination and unresolved pending state
remain fail-closed; no older-state fallback or generic reset is introduced.

The actual matching workflow supplies the required GitHub read token and
artifact path; GitHub supplies its output and runner-temporary paths. No
separate maintenance environment variable is required by this binding. The
reviewed composed main checks cover both the first finite legacy bridge and
later owned continuity plus missing-configuration negatives. These are
private ordinary controls, not successful hosted publication evidence. The
maintainer alone enables the workflow before merge and verifies the sole
automatic source run, full current records and resulting owned state.


## Header-only HTTP 429 stop — issue #328

A received publisher 429 now retains its adaptive constraints and stops before
reading any response body. Both current physical and inventory gaps still
double; the repeated-strike exponential cooldown remains at least 1800 seconds,
and a longer valid Retry-After remains stronger. A short valid Retry-After does
not reduce that fallback. The response context must close successfully before
this stop is assessed; persistence or close failure leaves the original pending
failure gate closed, with no retry or next publisher opener.

This paragraph supersedes earlier 429 body-inspection descriptions. An unread
429 body supplies no new challenge classification; previously retained holds
and all stronger constraints remain. Non-429 response inspection, literal REP
semantics, original parser/frontier/IDs/dates, publication and GitHub pacing,
phase limits, owned-state schemas/seeds and workflow remain unchanged.

Acceptance for this conditional guard uses actual private transport/writer/
completion contexts and exact whole-module AST protection after removing only
the new 429 branch. Existing genuine native evidence retains its original code
and date attribution; this maintenance claims no new publisher collection,
upstream factual review, full native replay or production run. Issue #328
remains open after this three-source pilot.


## Exact journal generation authority — issue #295

A validated complete read now acknowledges the original journal descriptor,
pinned parent, metadata generation and all accepted bytes. Every later read,
write, final publisher prewire check and healthy lifecycle close must still
match that acknowledgement. Only this process's fully written, fsynced and
read-back commit advances it. An unseen same-inode replacement, including a
stronger hold or pending state, stops the run without overwriting its bytes.
The existing 16 KiB reader bound, Boolean pending schema, RAM/remote MAX merge,
reviewed legacy boundary and newest-owned restoration markers stay unchanged.

Writes use verified unbuffered descriptors with complete short-write handling.
An interrupted in-place journal commit may leave invalid partial state; cleanup
invalidates only the exact last generation written by this process. This is a
fail-closed availability tradeoff, not an atomic filesystem compare-and-swap.
A changed generation cannot be adopted by failure cleanup. No retry or later
publisher opener is allowed after a persistence or ownership failure.

Export retains the original output and parent descriptors until lifecycle
cleanup. Failure truncates the verified original before fallible readiness or
unlink operations, preserving an independently replaced named output and any
foreign descriptor. Healthy collection/output close failures are fatal; an
existing primary error survives cleanup. If acquisition's first identity query
fails, an unverified descriptor is retained until process exit instead of being
blindly closed. This bounded terminal retention does not permit another attempt
or a healthy export.

The scoped maintenance uses actual private read/write, transport, strict ZIP
restore, legacy bridge, export and lifecycle functions with synthetic metadata
and network denied. It preserves the original native-test and business-function
ASTs, source facts, IDs, expiry rules, workflows and publication contract.
Historical native results retain their original code/date attribution in the
preceding sections; their raw input RAM is no longer available in this session.
No fresh publisher collection, native replay or production run is claimed here.

## Proven pre-access continuity — issue #373

The 18 completed attempts from run `41068492690/1` through
`43913201360/1` failed before publisher access. Review of all 18 job logs
identified the first completed-history validation failure and the following
17 missing-newest-state failures. Each failed single `collect` job completed
within 47 seconds. Recovery pins their exact attempts, revisions, job IDs,
start/completion timestamps, original source blob and workflow blob. Their
complete artifact inventories must be empty. The complete chain must lead to
actual owner `40889063854/1`, artifact `12903619139`, SHA-256
`10f6fe52c39de27726595d551d4adfeab74685983bc5db204db1583adeeb2028`.
The original nine-field envelope, producer digest, state digest, expiry and
owned-state validation remain mandatory.

Future artifactless failures are eligible only when authenticated source bytes
match this exact running implementation, the workflow matches the reviewed
blob, and attempt one has exactly one failed `collect` job lasting less than
58 seconds. Successful restoration establishes a 60-second monotonic startup
floor before every publisher transport can proceed. Wall-clock changes cannot
shorten this floor. A missing artifact alone never proves absence of access;
unknown code, reruns, longer jobs, extra outputs and incomplete historical
chains stop recovery.

Skipped job completions supply conservative timing bounds only. Actual starts,
completion receipts, pending state, refusals, adaptive gaps and unknown legacy
history remain intact, and stronger local constraints still merge by MAX. The
local first-access rescan compares against the actual newest history, including
all proven skipped attempts. The original business parser, sole native test,
publication contract and workflow are unchanged.

Private offline controls exercised all 18 actual job/revision proofs, the
original actual ZIP decoder, invalid-chain/code/job/artifact negatives,
stronger hold/pending/backoff preservation, and a forward wall-clock jump.
Synthetic future-current-code controls demonstrate the guard algorithm only;
they are not actual hosted-run evidence. No new publisher collection or
production success is claimed by these controls. Independent pre-run review,
a complete RAM capture and the existing genuine native test remain required
before enabling publication. The last owned run failed on a publisher read
timeout, so recovered continuity alone does not establish publisher health.

### First original native result — November 16, 2026

The single independently approved original native run failed after 211.946
seconds on producer SHA-256
`06cc8b0de832b4f055c34a47bf16c435dc75365d0260ee260cd37a8766034086`
and private observer SHA-256
`5b0fd6a67d063aa5b243fb74b73c581dcad2c9119dce2a3f2959906bfdf7a8ff`.
Owned-state recovery succeeded through the original decoder. The three actual
publisher responses were robots 200, Louisiana call 200, and CIRC call 403.
The last URL was
`https://www.wbi.be/fr/actualites/offre-stage-centre-international-recherche-cancer-circ`.
Its captured publisher Drupal page says “Oups! Page introuvable” and
“Cette page n’existe plus ou a été déplacée”. No visible bot challenge was
identified in that response. The retained CIRC call deadline was November 13;
this evidence suggests a removed or unpublished call, without proving the
publisher's reason for returning HTTP 403.

Complete private RAM evidence contains all 135,891 response bytes and all 14
raw journal persistence witnesses, with zero observer errors. Exact bot
identification, the monotonic startup floor, a minimum 12.206-second
completion-to-next-start gap and at most three starts per rolling minute were
independently verified. Only one of 28 required source pages completed. No
normalized collection, actual consumer acceptance or production success is
claimed. The healthy final journal retains `blocked=true`, `in_flight=false`;
it is not a permission to retry. The workflow remains disabled, with no second
publisher attempt or publication. Source expiry and the removed-page behavior
require a separately reviewed repair or permitted access route before further
collection. This failed native result supersedes the earlier pending-native
status for issue #373; the offline controls remain offline evidence only.

### Disabled offline repair implementation

A subsequent offline-only extension adds a concrete dynamic inventory path and
an inert route-retention proposal builder. `_REVIEWED_WBI_AUTHORIZATIONS` is
empty: no environment value can activate either path. No workflow supplies an
authorization. The proposal builder validates the exact held journal and actual
403 body hashes, retains the original raw journal bytes and every state field,
and returns `effective_global_hold=true`. It neither writes a migrated journal
nor authorizes an opener. Known CIRC slug, apex/www and node-25108 aliases have
an additional unconditional prewire denial.

The gated inventory path follows all advertised supported catalogue pager
links, normalizes the first-page alias, checks contiguous page indices, and
uses the existing inventory and physical transport guards. It excludes only
the precisely identified previously validated expired CIRC call; unknown or
ambiguous deadlines are retained. Supported single-call pages extract a minimal
current factual record and explicit application deadline, preserving reviewed
existing IDs. Missing structures, multiple awards and unverified categories
fail closed instead of supplying stale manual descriptions.

This extension is incomplete for production. Current complete call inventory
markup, multi-award programmes and the three UNESCO calls have not been fully
captured or implemented. It must not be merged or enabled as a working source.
Offline controls replay the actual Louisiana page and removed CIRC page, and
use clearly labelled synthetic inventories solely for pagination/exclusion
controls. They prove neither complete current discovery nor a passing native
collection. The original live test and workflow remain unchanged. No publisher
requests or actual state changes accompanied this extension.

The local adapter contract's content-403 rule and robots-only hold migration
exception remain applicable. A maintainer scope decision must explicitly
resolve that rule boundary before any effective route-scoped migration; an
approval string is not a policy change. Broad repair authority supports the
concrete proposal and review, but does not make the current robots-only text
cover this content response. No publisher-wide indexing prohibition is inferred.

### Retained-refusal runtime migration awaiting source review

Workspace rules PR #37 now permits a separately reviewed effective refusal
scope; the preceding robots-only limitation describes the earlier rule state.
No WBI source decision is yet activated: `_WBI_SCOPE_DECISION` remains `None`.
Migration revision `wbi373-content-scope-v1` is proposed for independent review.
It binds the exact original held journal, CIRC response body and failed-native
revision documented above. Schema 4 retains the complete original schema-3
journal bytes and the original refusal event. The `blocked` Boolean stays true.
Exact-schema validation and the original artifact envelope remain mandatory.

The proposed unrelated public scope is HTTPS on `www.wbi.be`, GET only:
`/robots.txt`, `/en/bourses`, the already reviewed taxonomy terms 843, 844, 846
and 847, `/fr/appels-a-projets`, `/actualites`, `/fr/actualites`, `/fr/rss-feeds`,
and plain-slug details under `/fr/actualites/` and `/fr/aide-service/`.
Only a single nonnegative `page` query is admitted on inventory paths; detail
queries, fragments, percent-encoded or dot-segment paths, alternative origins,
authentication routes, unknown endpoints and known CIRC aliases fail closed.
An advertised new feed endpoint still requires additional scope review. This
scope proposal does not permit any request before independent acceptance.

Restoration migrates only the exact reviewed held journal generation while
holding the original journal descriptor. All original constraints and unknown
history survive; merge uses MAX and refusal-event union in the same family.
An unproved blocked input adds a globally restrictive event. New content
401/403/451, visible refusals and robots denials append additional events;
these cannot inherit the CIRC event's reviewed scope. Any additional event
stops subsequent access globally. Interrupted writes latch the original
failure gate closed. The exact original artifact decoder is unchanged.

Offline controls exercise migration/admission/event union, denied and ambiguous
aliases, original durable writes in private directories, and interrupted
migration persistence. A separate actual-transport control receives a synthetic
403, observes the new event durably before reading its body, and proves a fresh
module with the same old approval cannot open another request. These controls
perform no publisher requests and do not alter the actual held journal. The
new source scope, complete dynamic inventory, multi-call parsing, genuine native
collection and production handoff still require their respective reviews.

The maintainer's exact source-scope decision is now recorded at
https://github.com/YouthOpps/data-pipeline/issues/373#issuecomment-6925600137
and embedded in `_WBI_SCOPE_DECISION`. It supersedes the earlier pending-decision
status and permits only independently reviewed bounded research within the
named scope, after the committed-decision recheck. Dynamic inventory remains
separately gated and incomplete; this decision supplies no complete native or
production acceptance. The original failure and refused CIRC event remain.

### Proposed exact World-route scope v2

The independently checked four-root research retained all original scope
provenance and made no new refusal. Its four responses were robots 200,
programme catalogue 200, current calls 200 and `/actualites` 301. The literal
redirect target `/fr/news` was not requested because it was outside the
approved scope. All 23 raw journal witnesses, pacing and final schema-4 state
were independently checked. The resulting actual journal SHA-256 is
`271667f2f3958af5cc217f1143cf3fe56d53c585fab620bcaf2aff2cf1459c53`.

The catalogue's ten actual first-page cards advertise page 1 and the exact
programme URL `/fr/WBI-World`, already recorded as legacy `PAGES['leaf0']`.
The current calls page contains six article cards with no pager. Parsing now
uses each actual main view and card layout, including the World link; it does
not include footer news. This validates those two captured pages only, not the
remaining catalogue page, complete detail coverage or multi-call normalization.

Proposed migration `wbi373-content-scope-v2` adds only `/fr/WBI-World`, without
queries or alternate origins. `/fr/news` and further feed discovery are not
needed for this amendment and remain outside the active scope. The amendment
is disabled until its own committed independent approval is present.
First application binds the exact current v1 journal generation and original
v1 decision digest. It adds a durable extension record while retaining the v1
decision, original held bytes/event, all MAX fields and unknown history.
Additional holds, pending requests, changed generations or extra paths reject
expansion. V1/v2 merges retain the extension and refusal events in either order.
The proposed state is 13,548 bytes within the unchanged 16,384-byte journal
bound; actual private-writer and interrupted-write controls pass. No actual
family state or publisher requests accompanied this offline amendment work.

The exact World-only amendment is now recorded at
https://github.com/YouthOpps/data-pipeline/issues/373#issuecomment-6941417638
and committed in `_WBI_SCOPE_EXTENSION`. Its reviewed source change is limited
to the approval literal. After independent literal recheck, it permits the
bounded remaining catalogue-page/current-six-calls/World research; no news or
feed scope is added. It does not establish full parser or native acceptance.
