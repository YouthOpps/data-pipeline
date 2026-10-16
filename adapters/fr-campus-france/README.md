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
family `youthopps-campus-france-publisher-v1`, with an exclusive local family
collection lock as well.

All publisher hosts share the same persisted physical request budget: starts
at least 12.2 seconds apart, no more than five starts per rolling 60 seconds,
with a conservative prior-completion guard and stronger robots/retained limits.
The inventory page guard waits at least 30 seconds after each preceding page
completes; it covers the application home and both complete catalogue requests.
Scripts and legal pages retain the physical budget. The page guard is local to
the serialized collection, not a global cross-machine page-rate claim.

HTTP 429 stops the current run. Both retained physical and page gaps double,
with at least 30 minutes of exponential durable cooldown, or a longer valid
Retry-After/known embargo. Unusable Retry-After values use the timed fallback;
ordinary rate-warning bodies do not create a permanent refusal. A genuine
primary human-verification challenge can retain both a hard hold and timed
backoff. Stricter known state is merged with MAX, never reset. Counter bounds
fail before more traffic rather than overflowing or shortening history.

The phase cap is 15 minutes and the job cap 20 minutes; the publication token
is obtained immediately before the sole application command. Persisted slower
post-429 spacing can exhaust those unchanged caps: automatic completion is not
guaranteed, and failure must retain state and last-good data.

Before publisher access, production restores the newest completed family
attempt's authenticated inert pacing artifact, ranked by exact-attempt jobs'
actual completion, not mutable run IDs or `updated_at`. Queued older-ID reruns
and the current run's preceding attempt remain covered. Repository, source,
branch, head, run/attempt, job inventory and artifact binding are checked;
missing or expired latest state fails closed. First-family bootstrap requires
complete no-prior-family history, independent native QA and a maintainer-approved
full known state, followed by at least 60 seconds of startup quiet. Its marker
is intentionally absent until that approval. The workflow always uploads only
trusted inert pacing state, including after first collection failure.

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
snapshot reads, and trusted pacing is exported in `finally`, preserving failure
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


### Completion persistence and artifact failure

A failed publisher-completion state write makes this process unhealthy. It
refuses further source requests and collection reentry, and cannot export an
apparently valid pacing artifact containing older state. Export invalidates
a safely owned prior artifact through its pinned descriptor before unlinking;
unsafe files, symlinks and hardlinks are preserved and rejected. The new output
descriptor remains open through serialization and any workflow readiness
signal, so a later failure truncates it before attempting removal. No state
history, actual access hold, collection scope or pacing limit is reset.

This conditional safety repair is checked with offline actual-function failure
controls. It does not claim a new publisher collection, fresh complete current
consumer replay, or certification of other legacy pacing/rate-limit behavior.
Historical native proof is reused only for unchanged normal collection, facts,
normalization, validation, expiry and publication functions; retained record
and dependency limits are documented in the linked maintenance issue.

### Current journal generation and owned IO maintenance

The #295 Group C maintenance keeps the existing state schema, seed markers,
source inventory, factual normalization, date precision, expiry, publication
and workflow schedule. Journal reads and writes now use an owned retained
read/write descriptor under the existing stable state.lock. Current parent,
named inode and byte generation are checked before and after IO and again
before opening a publisher request. Writers serialize in RAM and rewrite only
that held inode; a changed current file is refused instead of overwritten.

A genuinely new publisher namespace can be initialized only within the original
validated incoming-state restore. Merely computing a path or finding a
preexisting directory with missing journal/lock files does not grant
initialization. Collection-lock identity is captured at acquisition; journal
and collection releases are attempted independently. Cleanup preserves an
active primary exception and refuses a healthy handoff after an IO failure.
Export keeps the original six-key envelope and upload contract, with acquired
writer/reader/parent identities retained through cleanup. It adds no workflow
readiness-signal dependency.

This intentionally replaces atomic journal replacement with in-place
persistence: a crash or failed write can leave empty or partial bytes and
sacrifice availability. Such bytes are refused rather than reconstructed from a
weaker snapshot. Locks are advisory; these checks are not filesystem
compare-and-swap and do not authenticate an arbitrary same-user writer ignoring
the locks. If both persistence and invalidation fail, this unchanged schema has
no durable pending barrier for a later process; that separate #273 limitation
remains. Existing actual holds are never cleared.

Validation for this maintenance is bounded ordinary private-namespace execution
and whole-source static comparison. All original non-IO functions and
state/seed/refusal literals are unchanged; the narrow main-finally wrapper only
coordinates export and IO cleanup. Earlier complete successful native evidence
remains historical qualification, not a native run of this maintenance. The
original four raw-body services have expired, so no fresh full original-record
or body replay is claimed. A separately pinned current published snapshot can
be checked with the current consumer; it is not proof of current upstream
accessibility or a replacement for the original full native evidence.

The maintenance checks comprise 48 ordinary private contexts across the
four-source group, plus whole-source AST protection. At immutable data snapshot
5e7f5f11aa7698844b54ce30be12f55318425fe0, this source has 378 published
records; the unchanged adapter expiry filter retains 378, and the pinned
consumer accepts the complete published pair, its expiry-filtered pair, a
derived valid-empty pair and failed-metadata last-good retention. These current
published checks do not replay expired original native raw bodies or assess
current publisher content.
