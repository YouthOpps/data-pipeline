# grants.at / OeAD

This adapter indexes basic facts about funding programmes in Austria's official
[grants.at catalogue](https://grants.at/en/), operated by OeAD. It is a programme
index, not a list of currently open application rounds. Application availability,
deadlines, citizenship requirements and destination countries remain unknown
unless the publisher supplies them in the indexed fields. A year in a title is
not treated as an expiry date. Readers must check the original programme.

## Public source and completeness

The public page instantiates `STIMAQuery.View("STIMAQueryContainer", 2, 44)` and
loads its own public client from
[ajs.scholarships.at](https://ajs.scholarships.at/JS_1.0/STIMAQuery.min.js).
The adapter uses that client's literal public `GetMetadata` and
`STIMAEntrySearchForTextParameters` routes, template 44 and English language 0.
It does not guess private endpoints, call search logging, or crawl programme
prose and attachments.

Every run obtains the current funding taxonomy, queries all categories together,
and reconciles the complete result against a separate query for every funding
category. The client returns complete arrays rather than a paginated response.
Any missing partition, duplicate native row, inconsistent programme facts,
changed interface or unequal union fails the refresh. There is no record cutoff
or first-known-ID shortcut. Incremental fetching is unsupported: the public
interface provides no proven change cursor, stable modification ordering or
removal stream. Every refresh performs the full inventory scan.

The stable identity is `at-grants-at-<StipFoeID>-<HZGID>`. A programme appearing
under several funding categories becomes one record with all categories retained.
A nonzero publishing-organisation ID does not make a funding programme a directory
entry. Titles, native IDs, funding categories and available institution names come
from the public result rows. Summaries are short original statements of those
facts. Source-language titles are marked `und`; generated summaries are English.
Destination, eligibility and deadline are omitted for records without
programme-specific publisher evidence. Application status stays unknown.

The existing Ernst Mach FH record `at-grants-at-44772-0` includes the deadline
published in its [official OeAD FAQ](https://oead.at/en/study-research-teaching/overview-grants-and-scholarships/ernst-mach-grant/faq-ernst-mach-grant-fachhochschule).
The FAQ explicitly associates the date and CET time with the academic year.
For 2027/28, 1 February 2027 at 23:59 CET becomes
`2027-02-01T22:59:00Z`. An ambiguous or changed date format fails validation
before publication. The record keeps its grants.at identity and link. Its
host country is Austria. The FAQ ties eligibility to bachelor's or master's
enrolment at a university in an eligible country outside Europe and prior
acceptance by an Austrian university of applied sciences. The summary also
records the four completed semesters required for bachelor's students and
the maximum age of 35. Applicant citizenship stays unknown. The separately
scoped Ernst Mach worldwide programme is unaffected.

The first production refresh after this change uses a separately reviewed
native-test journal. The production owner must be paused for that test, and
the temporary test workflow must be retired before the first production
handoff. The handoff verifies the exact attempt, job, immutable source
artifact, and post-test maintainer decision. It merges the original journal
with the newest production state using the existing restriction and history
rules. Later source-owned production states must retain that handoff's
restrictions. A missing or conflicting handoff stops publication.

Programme URLs use the official client's Copy Link format:
`https://grants.at/en/?=<base64(StipFoeID_HZGID_0)>`. The empty query key is the
publisher's own `URLPARAMETER=""` setting. Its detail loader consumes the two
native IDs; the third component is a presentation index and is fixed at zero.

## Access evidence and attribution

Requests identify as `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`.
Only the three literal public hosts `grants.at`, `ajs.scholarships.at` and
`oead.at` are permitted. TLS verification remains enabled. Redirects must stay on
the same reviewed host. Verified robots-retrieval purpose survives those redirects;
ordinary content cannot opt into unavailable-policy handling. The published attribution is
`Source: grants.at / OeAD (© OeAD)` and every record links to its original page.

The [OeAD imprint](https://oead.at/en/imprint-en), Copyright section, states:

> The publication of excerpts of the OeAD website is generally permitted,
> provided that the source is acknowledged (© OeAD).

It separately states:

> Direct access to OeAD databases via external websites is only permitted with
> the OeAD's express permission. Otherwise, a link must be provided to the
> relevant query page on the OeAD website.

That second clause leaves the scope of a separately stored minimal factual link
index uncertain. This implementation does not embed the database widget, copy
programme descriptions or offer a live database query service. It indexes only
basic facts with attribution and native publisher links. This is a bounded
technical interpretation, not a statement of definitive legal permission.
A changed access condition or actual publisher refusal must be reviewed.

`grants.at/robots.txt` allowed these public routes during source research.
The service host's robots endpoint returned 404. Robots-only 4xx responses other
than 429 are treated as unavailable policy, rather than fabricated Disallow
rules. A robots 429, 5xx, invalid HTML response or applicable Disallow stops the
run. Matching advertised and legacy protective groups are checked conservatively;
stronger Crawl-delay and Request-rate limits prevail. Content 401/403 and visible
human-verification/denial pages create a retained hard hold. No alternate identity,
proxy, endpoint or TLS bypass is used to overcome a refusal. A genuine visible
challenge on a content 429 retains both its timed embargo and a hard hold;
an ordinary rate-limit response without a specific human-verification challenge
remains recoverable, including generic request-blocked/rate-limit wording.

## Request budgets and failure state

All three publisher hosts share the serialized durable family
`youthopps-grants-at-publisher-v1`. Physical requests wait at least 12.2 seconds
from the previous response completion, with no more than five starts in a rolling
minute. Inventory requests additionally wait at least 30 seconds from the
previous inventory response completion. Robots requests use the physical floor.
Stronger publisher and retained limits take precedence. The actual opener checks
these restrictions again after acquiring ownership. Restoration establishes a
60-second monotonic quiet period; a wall-clock jump cannot shorten it.

The complete collection has a nonrenewable 900-second phase deadline within a
1740-second overall operation and the existing 30-minute workflow timeout.
Main-thread signal deadlines interrupt slow response bodies and response close;
a 250-millisecond cancellation grace bounds a close interrupted after the first
deadline. Deadline interruption retains unresolved ownership. Neither pagination
nor retries renew the deadline. The daily schedule remains 01:23 UTC.

The last successful run on 5 November 2026 reconciled 1,396 category rows into
1,131 programmes across eight funding partitions in approximately 415 seconds.
Those counts describe that run, not limits. The complete frontier comprises
three evidence pages, robots for the three origins, current metadata, one
all-category query, every current funding partition and the OeAD FAQ when the
Ernst Mach FH native row is present. The FAQ uses the existing OeAD policy and
physical request budget. A larger taxonomy or retained slowdown can
exceed the phase budget; the adapter then preserves last-good data rather than
truncating the inventory.

Before each physical request, the adapter persists a unique pending transaction
and then the actual start reservation. It clears that transaction only after the
bounded body, framing, visible access evidence, applicable robots policy and
response close have been assessed successfully. TLS, partial body, invalid
framing, close, deadline or storage failures cannot become healthy completion
receipts. Cached robots decisions also have durable transaction ownership; they
cannot overwrite an unresolved request. Private directory/file identities,
permissions, inode generations and the actual kernel collection flock are checked
before the opener. State writes retain the owned inode, verify readback and fsync;
failed writes invalidate safely owned bytes instead of resetting state.

A 429 saves doubled physical and inventory limits and the exponential embargo
before reading its bounded body. A first response adds at least 30 minutes, the
second at least 60 minutes, and later responses keep increasing; a stronger valid
Retry-After or retained embargo wins. A genuine visible human-verification page
also creates a retained hard hold. Ordinary rate-limit wording alone remains a
timed restriction. Body or close failure leaves pending ownership alongside the
already saved backoff. Unrepresentable arithmetic/timestamps fail closed with the
pending state retained in memory and no apparently healthy artifact export.
A counter of 100 stops before access. There is no immediate retry.

Visible HTML inspection respects hidden ancestors, script, footer and navigation
suppression. Plain-text human-verification responses are also assessed. On 429,
a qualified initial visible HEAD title such as `Access denied`, `Just a moment`,
`Attention required` or `CAPTCHA` is additional refusal evidence; this bounded
check also recognizes obvious HTML with a missing or incorrect MIME type.
Ordinary content redirects are assessed before following them and must remain on
the reviewed host. Robots-only unavailable-policy handling is restricted to the
literal `/robots.txt` route, including every redirect hop. Existing hard holds
never expire with the rate-limit timer.

## Owned-state continuity repair (#393)

The complete authenticated 34-run history identified the last actual owned state
in run `41990792713`, attempt 1, artifact `13397744160` (407-byte ZIP; SHA-256
`667c19b1a95e9f55a0847e6b6d505d1a04e0bf74f2b1a7d9eff19ec08b2b2226`).
The following twelve failed runs had no artifacts. Their complete job logs,
immutable adapter and workflow revisions, sole workflow command and original
call graph establish restoration failure before publisher access. The first
reported `Workflow pacing state unavailable`; its underlying HTTP/TLS/network
cause was masked by the old implementation and remains unknown. Later runs
reported `Newest family pacing artifact missing or expired`. Their 81–119-second
job durations are not a short-run proof.

The finite exception binds each of those twelve exact job IDs, revisions and
start/completion timestamps, plus the immutable original adapter/workflow hashes.
It does not authorize arbitrary old-code failures. The independently reviewed
[evidence is recorded in issue #393](https://github.com/YouthOpps/data-pipeline/issues/393#issuecomment-6944753581).
For future failures, skipping an artifact-free run requires attempt 1, the exact
current adapter and reviewed workflow bytes, the exact sole failed job and a
complete job duration below 58 seconds. The mandatory real monotonic 60-second
pre-opener floor supplies the positive no-access proof. Unknown, longer or
changed-code gaps stop restoration.

Only that exact original owner may supply the six-key legacy envelope. Its
complete original state is retained under `legacy_owned`, including the actual
starts, completion and restrictions. Schema 2 explicitly retains unknown earlier
history; it does not invent missing requests or receipts. The old final physical
completion supplies a conservative initial inventory floor, not a claimed
inventory observation. Modern state adds pending ownership and completion IDs.
Local and incoming restrictions combine with MAX, refusal OR and start union;
any unresolved owner survives a newer healthy snapshot. Conflicting pending
owners fail closed.

Every subsequent owner requires the newest exact, unexpired artifact, bound to
repository, main branch, run/attempt, immutable producer revision, producer source
hash and canonical state hash. A bounded, digest-verified ZIP must contain only
`pacing-state.json`. Its modern envelope has nine exact keys. Missing, expired,
malformed, mismatched or unavailable newest state never falls back to an older
healthy archive. Authenticated API failures now retain a redacted typed cause.
Infrastructure opens only api.github.com and the reviewed GitHub storage suffixes
`.blob.core.windows.net` and `.actions.githubusercontent.com`. The initial
artifact Location is checked before any destination request. Storage redirects
are rejected without following them; API credentials are never forwarded.
Artifacts contain inert state only, never records or credentials. Healthy durable
pending/refusal state is exportable so the next owner inherits the restriction;
storage failure invalidates safely owned stale export bytes. Symlinks and foreign
files are rejected without modification.

A mature local journal cannot be recreated from a remote archive. Local native
execution is disabled by default and requires independently reviewed exact
state, machine and public issue-evidence bindings, the source workflow paused
with complete terminal history, and a private exclusive one-shot claim that is
never cleared. The actual claim and kernel flock remain required before every
publisher request. No environment variable provides a generic reset or bootstrap.
The source workflow's obsolete bootstrap literal has been removed.

The implementation has inert actual-function controls for legacy and modern ZIP
restoration, strict newest-owner selection, finite/future continuity proofs,
monotonic physical gating, retained 429 state, cached policy ownership, framing,
slow socket bodies/close, write faults and fresh-interpreter refusal, and owned
export roundtrips. These controls are not a new publisher collection. New source
access requires the independent frozen pre-run review and the original full live
test; neither a partial replay nor a synthetic fixture counts as that native run.

## Validation and publication

The sole complete live nonpublishing test remains:

```sh
python -B adapters/at-grants-at/test_adapter.py
```

It performs the complete inventory scan, validates every record and requires a
nonempty raw result. Keep its complete stdout and source bodies in RAM for
independent inspection and the current website consumer. The test receives no
publication credentials. The production command remains:

```sh
python -B adapters/at-grants-at/adapter.py --publish
```

Only the matching main-branch workflow publishes with its short-lived source-scoped
GitHub App token. It atomically updates only `datas/at-grants-at/data.json` and
`metadata.json`. The existing snapshot must be trusted before any failure-status
update. Restoration and collection failures preserve last-good records and the
last successful timestamp while honestly recording the failed refresh. A first
failure creates no placeholder data/metadata pair. This repairs the old placement
of restoration outside the failure-status boundary, which left November 5's
success metadata misleadingly unchanged during later failed Actions.

Valid refreshes replace the complete snapshot, retaining stable creation and
first-seen timestamps for unchanged IDs. Removed publisher records disappear.
Explicit expired deadlines are filtered at their timezone-aware instants;
date-only deadlines without a documented timezone use the latest civil timezone
(UTC−12). Unknown deadlines stay unknown. A nonempty validated raw collection
whose records all expire may publish an atomic empty successful snapshot;
collection failure never substitutes an empty success. Consistent snapshot reads
and compare-and-swap branch updates preserve concurrent source publications.
These parser, identity, expiry and atomic publication rules are unchanged.

Published metadata retains `display_name` **OeAD**. Source IDs, attribution and
legacy names remain unchanged. No full programme descriptions are copied.

The response guard persists known 429 pacing/strike/cooldown and content refusal
status before header parsing, then retains the maximum of all Retry-After fields.
Positive unsupported timing keeps the exact request pending; body or closure
failure cannot settle it. Successful robots 200 requires one explicit text/plain
representation without HTML. Empty/comment-only REP remains valid; applicable
finite fractional Crawl-delay and Request-rate fields retain their numeric MAX,
while unassessed timing remains pending. Local collection pins the existing
journal generation and parent before metadata callbacks. Missing mature local
state cannot recreate a family directory or adopt an older owned artifact.

The hosted native MAX floor is bound to its independently accepted state digest
and the [issue #393 hosted decision](https://github.com/YouthOpps/data-pipeline/issues/393#issuecomment-6948764586).
Local native gates remain closed; the two explicit hosted literals carry this
reviewed production handoff.
The floor contains all 16 genuine native physical starts, both original owned
starts and both obsolete workflow-bootstrap starts:20 observations in total.
It also takes numeric MAX across all 87 successful journal witnesses, the exact
892-byte initial state, old bootstrap constraints and 12 proved administrative
completion bounds. All 26 transient pending owners have exact later settlement
receipts; no unresolved owner is discarded. Original legacy ownership and unknown
history remain intact. The 937-byte final rolling-window journal alone is not
the handoff candidate. Its exact SHA256 is
`7f92136f755a613eeb0800b8c58ed04bca40ae8423247d6f1ea88198f089753c`;
the full 1,275-byte union candidate has canonical SHA256
`eecaaec8f9f2e107d7a3c57e55152299fe562e56b1afc5cd416db9181b41d709`.

Hosted restoration must first obtain and authenticate the actual newest owned
artifact. Missing, expired, mismatched or unresolved current state never falls
back to the committed floor. After that real restoration, the floor is merged
for legacy and healthy future modern owners alike. Export durably reapplies it
after ordinary rolling-window pruning, retaining stronger limits, holds and
pending owners. This is an ongoing conservative lower bound, not a permanent
requirement that the newest owner remain the historical legacy run. The matching
workflow has no obsolete bootstrap environment payload; its application command
and the sole live test remain unchanged. No production execution is certified by
an OFF loader or its private controls.
