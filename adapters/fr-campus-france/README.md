# CampusBourses / Campus France

This adapter indexes minimal public financial-support programme facts from
[CampusBourses](https://campusbourses.campusfrance.org/?lang=en). It publishes
individual programme overviews, not the homepage or a directory record. A
programme's presence does not establish a currently open application round.

## Public access and factual scope

The public application names `js/ilangular.b81726ae.js`. Its literal `iwConfig`
names `https://bourses-api.campusfrance.org/`; `programWS.getPrograms` retrieves
`sgetgrants/en` anonymously. The public client's share function constructs
`https://campusbourses.campusfrance.org/ws/cfgrant.php?id=<bourseId>_en`.
A real share page confirms its programme title and native canonical programme
link. No private endpoint, authentication, browser impersonation or bypass is
used. The adapter checks the reviewed application script and support taxonomy
before collection.

Output contains native IDs, factual titles, native support-type labels,
original share URLs and an independently written short summary. Publisher
`synthese`, descriptions, images and detailed conditions are not copied.
Titles can remain French when the English catalogue provides a fallback;
title language is `und`, while the original summary is English.

Research on 15 October 2026 returned HTTP 200 for the public application,
script, catalogue and programme share page. UI and API robots endpoints returned
404, classified as unavailable REP rules, without inventing a disallow. Main-site
robots returned 200 and permitted the reviewed legal route. Actual content
401/403 and visible primary human-verification/denial messages retain a hard
hold. All requests use exactly
`YouthOppsBot/1.0 (+https://youthopps.org/bot/)`, normal TLS and the configured
network proxy. Identity rotation and access-protection bypass are prohibited.

### Terms evidence and unresolved scope

The [main-site CGU](https://www.campusfrance.org/fr/conditions-generales-d-utilisation)
expressly identifies `https://www.campusfrance.org/fr` as the « Site » and its
services. Under **PROPRIÉTÉ INTELLECTUELLE** it says:

> Les bases de données figurant sur le Site sont protégées par les articles
> L.341-1 et suivants du Code de la propriété intellectuelle et est sanctionnée
> toute extraction ou réutilisation qualitativement ou quantitativement
> substantielle du contenu des bases de données.

Under **LIENS**:

> Les Utilisateurs du Site ne peuvent établir de lien profond en direction de
> ce Site sans l’autorisation préalable et écrite de Campus France.

The applicability of those main-site clauses to this sibling CampusBourses
service remains unresolved. Complete catalogue extraction would matter if that
database restriction applies; public technical access is not a reusable licence.
No express CampusBourses-specific automated factual-index prohibition or
applicable reuse licence was found in the reviewed public application. The
user/maintainer authorized this bounded minimal factual index under the project's
current policy; this is neither publisher authorization nor legal clearance.
The main legal page's normalized main-body fingerprint is checked each run;
changes stop collection for review. A clearly applicable restriction or actual
content refusal must be preserved.

## Inventory, facts and reconciliation

The anonymous response supplies its complete `count` and `programs` array;
the public client filters and paginates that array locally. No server continuation
or known-ID early stop is used. Two complete inventories must have equal
membership, titles and selected support types. Every array must equal its
advertised count, have unique positive native IDs and recognized support types;
empty, malformed, moved or oversized inventories fail without partial output.
The 5,000-item safety bound fails rather than truncates.

The native `IwType` taxonomy includes Study, Research, Internship, Travel and
partial fee waivers. All entries are financial support programmes: research
support is categorized as `grants`, other support as `scholarships`, with both
when native Study and Research types coexist. An internship scholarship does
not assert the existence of a separate internship vacancy. No regional filter
or arbitrary sample excludes programmes.

Stable IDs are `fr-campus-france-<bourseId>`. Final validation binds each ID to
its exact source share URL and rejects case-insensitive duplicate IDs. No
record identity depends on title or inventory position.

The catalogue's `endAt` has not been proved to be an application deadline;
`updatedAt` is not a publication date. Both published/deadline fields remain
null, status remains `unknown`, and eligibility, destination countries,
location and funder institution remain unknown. Publisher country FR describes
Campus France, not programme destination or eligible nationality. No unsupported
inference is made from titles or `countryListId`.

There is no proven change feed or safe modified-since/removal boundary. Every
refresh therefore retrieves and reconciles the complete current catalogue.
Per-record check/seen timestamps reflect successful collection; creation and
first-seen timestamps survive unchanged records. Supported expired deadlines
are removed only after successful full validation and before atomic publication.
Ambiguous or missing deadlines never imply expiry. A valid all-expired dataset
publishes empty success; failed/empty retrieval preserves last-good records and
the previous success timestamp.

## Pacing, schedule and durable state

Daily schedule: **14:38 UTC**, plus filtered main-branch merge and manual
triggers. GitHub can delay schedules. Production is serialized by publisher
family `youthopps-campus-france-publisher-v1`, with an exclusive local collection
lock and stable journal lock. Both actual flock and Linux OFD ownership are
verified before publisher access.

The schema-3 journal records logical policy ownership and each physical request's
reservation, actual close and assessed completion. A request is admitted only
from a settled owned generation. Publisher starts remain at least 12.2 seconds
apart, no more than five per rolling minute, with stronger retained constraints
and a 30-second inventory completion guard. All publisher origins share that
budget. Literal REP routes are assessed before content, using the current and
retained legacy product tokens. An unavailable ordinary REP 4xx does not invent
an allow rule or a content denial; human challenges and applicable restrictions
remain barriers. Successful REP bodies require the reviewed plain-text grammar.

A received 429 durably doubles both gaps and retains at least 30 minutes of
exponential cooldown before parsing additional constraints or reading the body.
IMF-fixdate, RFC850 and asctime dates are accepted; ambiguous combined fields
retain any independently known longer limit and leave the owner unresolved.
Unsupported arithmetic, framing, incomplete bodies, unassessed policy and
read/close failures prevent healthy settlement. Applicable content refusals and
human challenges retain their hold. Legacy rate/policy uncertainty remains
explicitly unknown and is never cleared by this migration.

The collection cap remains 900 seconds; the job remains 20 minutes. The bounded
process reserves publication and export time, proves the current hosted job
before long restoration, and applies repeating deadlines through response close.
Retained slow spacing may exhaust these caps. An exhausted budget is a failure,
not permission to shorten a guard or retry the source.

Before publisher access, the full workflow-specific run/attempt/job history is
validated and the newest qualified owned state is authenticated. Issue #417
permits only the exact 24 immutable pre-request failures between the last owned
artifact and this migration. Their exact job, head, source/workflow and retained
log pins are checked; an unexpected attempt, artifact or changed owner blocks
recovery. The first historical failure occurred at completed-identity metadata
validation. Its hidden underlying API subcause is unknown.

The original 427-byte owned archive from run `40005091304/1` is accepted only
with its exact owner, archive/state digests and head. The conservative migration
floor joins all 11 historical owned states, all seven historical workflow seed
states, and 24 job-end plus 120-second administrative bounds: **47** distinct
observations. These bounds do not pretend to be additional publisher requests.
Stronger current state, pending owners, holds and both unknown-history flags are
preserved. Missing, empty or malformed established local state cannot bootstrap
itself. A separate source/machine/state-bound exclusive initializer and distinct
outside zero-exit success witness require maintainer review before first local
use; one-use initialization/native claims cannot be reset by the adapter.

Future owned envelopes bind head, immutable adapter bytes and exact state bytes.
A narrowly typed pre-request metadata-failure receipt carries **no state or
allowance**. It is emitted only before any possible source open or journal
mutation, with healthy infrastructure closure and independently proved absence of the whole
local family before and after export under the same temporary parent. An existing
family object, including unknown bytes, pending work or stronger retained state,
blocks a zero-state receipt. A future consumer must prove the
full chronology back to an authenticated older owned state. Missing evidence,
unknown gaps or a possible publisher path cannot use that receipt.

Only the two explicit complete financial-support taxonomy review guards may
settle an otherwise clean semantic deferral. Their exact script-response receipt,
logical/physical owners, durable generation, locks, constraints and close must
all be verified. Generic parser errors, changed routes/legal scope/frontiers,
retained holds, pending physical work and IO failures remain conservative.
A failed factual review preserves last-good records while healthy owned pacing
can still be exported; it cannot silently reset the publisher journal.

## Run and publication

```sh
python -B adapters/fr-campus-france/test_adapter.py
python -B adapters/fr-campus-france/adapter.py
```

The sole live test requires a complete nonempty real collection, validates it
and prints the entire actual JSON array to stdout without writing records or
publishing. It refuses `DATA_SOURCE_TOKEN` before importing the adapter.

Only the maintainer-merged workflow runs:

```sh
python -B adapters/fr-campus-france/adapter.py --publish
```

Publication changes only
`YouthOpps/data-source/datas/fr-campus-france/{data,metadata}.json`, atomically
and with source-scoped GitHub App credentials. Restore/trust happens before
snapshot reads, and owned pacing or an eligible typed receipt is exported in `finally`, preserving failure
exit status. First failure creates no placeholder pair; later failure preserves
exact last-good data and success time, recording failure metadata when possible.
The metadata attribution identifies CampusBourses / Campus France. Tests and
static QA do not certify production publication.

## Validation evidence

The complete native non-publishing test on 15 October 2026 passed with **378**
real programme records, two matching complete inventories and no duplicate IDs.
The current website consumer accepted all 378 raw records, all 378 records after
expiry filtering, and the valid filtered-empty envelope in RAM. Actual wire
instrumentation observed eight publisher requests, a minimum 12.986-second
physical start gap, at most five starts per rolling minute and at least
30.049 seconds from inventory completion to the next inventory request.
Every actual output title, ID, share URL and support-type label was also compared
against the retained complete real catalogue response.

An earlier native attempt stopped safely on main-site robots HTTP 503; a bounded
normal diagnostic recovered HTTP 200 with unchanged rules before the successful
full retry. That failure is retained as a failure, not presented as successful
collection. Robots 5xx/network errors stop the run; no unrestricted fallback is
invented. Supplemental zero-publisher controls cover malformed counts, duplicate
IDs, changed types, full-state MAX restoration, redirects, retained denial,
recoverable/adaptive 429, exact-attempt chronology, expiry boundaries, atomic
empty success, first/later failure preservation and finally-export exit status.
These controls do not constitute production publication evidence.

## Redirect access checks

A visible publisher verification or denial body is checked before following a
content redirect, including HTTP 3xx responses. An ordinary redirect without a
challenge continues through the existing host, TLS, pacing and retry guards.
Robots-only handling is limited to the literal `/robots.txt` route on every
hop. A robots redirect to a content route stops before requesting that target;
its response is never treated as unavailable REP or a replacement robots policy.
Existing refusal holds, retained rate limits and source publication behavior
remain in force. This conditional-path correction does not clear an access hold
or certify a new publisher collection.

## Source display name

Published metadata uses `display_name` = **Campus France** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Hidden page content

Challenge detection reads visible content and preserves nested suppression.
Hidden attributes, `aria-hidden="true"`, inline display/visibility declarations and
script/footer/navigation decorations do not create an access hold. Block starts
close an open paragraph in HTML button scope, without losing suppression from
an outer hidden ancestor. This is a bounded text inspector, not a full HTML5
renderer or external stylesheet engine. Explicit
visible child visibility and void elements retain their DOM behavior. Genuine
primary verification or denial text still stops content access before a redirect,
including HTTP 200, 302 and human-verification 429 responses. Existing holds and
rate-limit budgets remain retained; this correction does not clear old state or
certify a new publisher collection.

## HTTP 429 HEAD refusal handling

On HTTP 429 only, a genuine initial, unsuppressed HEAD title containing
`Access denied`, `Just a moment`, `Attention required` or `CAPTCHA` retains
an access hold alongside the existing adaptive cooldown. Ordinary rate-limit
warnings remain timed restrictions. Hidden, footer/navigation, nested, second
or post-body HEAD titles do not supply this additional refusal evidence.
A bounded obvious-HTML prefix also qualifies this 429-only check when the
response MIME is absent or wrong; ordinary successful-response handling is
unchanged. Existing access holds, stronger embargoes, retained state and request/page
spacing remain preserved.

This conditional maintenance uses actual-function inert transport controls
and qualified prior complete native evidence; it does not claim a new live
collection or alter source facts, identifiers, expiry, publication, caps or
schedules. No publisher request or production Action validates this fix.

For publisher rate/denial errors, the existing adaptive 429 state and actual
content 401/403/451 holds are saved before the bounded response read; a failed
save stops the request path. Thus a body-read failure cannot defer the first
durable cooldown save. Literal robots 451 remains unavailable REP, while
robots 429 keeps its timed backoff. Existing scoped refusal distinctions,
including any reviewed API-only annotation, are retained.


## Current continuity maintenance — issue #417

Owned journal reads accumulate legal short reads and verify both metadata and
acknowledged bytes. Writes acknowledge each truncate/partial-write generation;
a changed current inode or observed stronger in-place update is preserved and
refused. Original retained descriptors, parent identity and actual lock owners
are checked before each request and export. Own close failures propagate even
when a parent exception is active. Unsafe foreign paths are preserved; an
unhealthy export is invalidated only through the original acquired authority.
These checks are advisory and observational, not filesystem compare-and-swap.

The earlier #295/#328/#361 repairs and their original qualification remain
historical context. This migration supersedes their legacy schema, six-key-only
export and header-only 429 descriptions. It does not identify the hidden
historical metadata subcause, certify renewed publisher access, or treat the
historical 378-record collection as fresh production proof.

OFF readiness uses actual functions with disposable private journals and inert
HTTPResponse producers: all 24 immutable prewire boundaries, the real 427-byte
archive through returned and HTTPError 302s, full family ownership/MAX history,
both taxonomy guards and conservative peers, all three HTTP-date forms, framing,
transport/body/close/persistence faults, original main proof/restore/export,
source-bound one-use initialization and outside success/launch boundaries.
Current website-consumer positive, empty, expired and wrong-source readiness
initially used explicitly synthetic records. The first original test invocation
stopped on extra harness arguments before its test method could run. The
unchanged source and sole test were then exercised with the corrected observer,
which tracked the adapter module imported by the test itself.

The original non-publishing test on 17 November 2026 collected 379 records in
eight complete physical responses and five logical reads. The website consumer
accepted all 379 current records. Full replay of the captured logical bodies
reproduced the material records, and all 19 acknowledged journal writes matched
their requested bytes. No actual record in this collection was expired; the
observer also exercised the valid empty consumer case. The minimum physical and
inventory gaps were 12.206 and 30.009 seconds, with at most five starts in a
rolling minute.

The hosted floor preserves 63 distinct observations: the prior 47, eight
durable reservations, and eight actual physical starts. The retained local
journal has four rolling starts after pruning; it is not the source of the full
history by itself. This floor retains existing unknown history, the strongest
scalar constraints and the last settled owner. The local initializer grant is
disabled in the hosted change. Production requires a separate maintainer
decision and the usual oldest-to-newest ownership checks against the latest
authenticated predecessor.
