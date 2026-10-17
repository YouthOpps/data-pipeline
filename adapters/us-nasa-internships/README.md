# NASA internships adapter

Collects the official NASA programme overview from
https://www.nasa.gov/learning-resources/internship-programs/.
It checks robots policy before public HTML retrieval and uses the publisher's
canonical URL, structured WebPage title and original publication timestamp.
It does not collect application listings, claim inventory of individual
internships, or infer applicant eligibility. Stable identifiers hash the source
and canonical URL; internship classification and US publisher attribution refer
to this programme overview. No application deadline is currently extracted.

Run `python -B adapter.py` for non-publishing collection metadata or
`python -B test_adapter.py` for the complete validated live JSON collection.
`python -B adapter.py --publish` is reserved for authorized production automation.
Requests retain at least 12.2 seconds from physical completion to the next
physical start, at most five starts per rolling 60 seconds, and any stronger
robots restriction or durable embargo.

NASA's official [Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/)
distinguish NASA informational material from protected identifiers and third-party
material, and require source credit when NASA material is used. This adapter
publishes factual programme metadata with the official NASA source link; it does
not reproduce photographs, branding, media or third-party creative material.
Robots permission is an access check, not a copyright licence. The source link
and US public-institution publisher attribution provide provenance; applicants
must consult NASA's page for current programme terms and eligibility.

## Expired-record cleanup

Successful refreshes validate the complete fetched inventory before removing
records with verified past application deadlines. Offset-aware timestamps are
compared as instants; equality with the closing instant remains eligible.
No official timezone is established for date-only deadlines. Cleanup waits
until the date has ended worldwide (UTC−12), a conservative certainty bound,
not an inferred publisher timezone. Explicit publisher offsets take precedence.
Missing deadlines, ambiguous deadlines represented as null, status labels,
record age and absence from a response are never expiry evidence. The existing
publication contract replaces the complete source inventory; old records are
not merged back into the replacement. Consequently expired old records cannot
survive a successful replacement, including when rediscovered with a verified
past deadline. Current records with missing deadlines remain eligible.

The filtered data (including an empty subset after valid nonempty retrieval)
and success metadata are committed atomically only
under this source's paths. An empty/invalid raw response or fetch, parse,
validation or publication failure preserves last-good data and its success
timestamp; safe failure metadata may be updated. Tests never publish.

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
pacing and backoff restrictions are retained in the source family below.
This migration does not clear refusals or enable a workflow.

## Daily automation

The scheduled refresh runs once daily at **06:11 UTC** (`11 6 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The collector has no paginated inventory. The assigned programme page is a
detail page, so its redirects retain ordinary physical spacing; literal robots
requests do too. Any actual inventory attempt must use the separate 30-second
completion guard, including every physical redirect. The schedule is unchanged.

## Workflow concurrency

`youthopps-us-nasa-internships-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **NASA** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


## Durable pacing and interrupted requests

The family is `youthopps-us-nasa-internships-publisher-v1`. Schema 3 records an
immutable flight identifier and durably writes `in_flight: true` before any
publisher transport. Response restrictions are saved while that flag remains
true: Retry-After, 429 slowdown, content 401/403/451, visible challenges, and
new or cached robots decisions. Only the matching healthy completion may write
false. Every failed state save is sticky for this process; completion cleanup
cannot overwrite it. The strongest failed constraints are retained in memory.
A fresh process refuses an unsettled local journal or newest owned artifact;
merging an older complete artifact cannot clear pending state.

A state-write failure also attempts to invalidate only the safely owned current
journal inode through its validated descriptor. Pending state remains the
protection if cleanup itself fails. Existing unsafe owners, modes, symlinks or
hardlinks are preserved and rejected. Artifact output holds its owned descriptor
through serialization and readiness signaling, truncating before unlink or a
false signal on failure. A failed unlink or readiness write cannot leave valid
JSON on that descriptor. The workflow uploads only a successfully signaled
owned artifact, and missing newest state never falls back to older state.

A 429 ends this collection without an immediate retry. Both persisted physical
and inventory intervals double from their current values, with at least an
1800-second exponentially increasing cooldown and any longer valid Retry-After.
An unrepresentable observed 429 slowdown leaves the flight unsettled and the
process unusable; completion cannot discard that response constraint.
Malformed, nonfinite, negative, stale or unsupported Retry-After alone adds no
access hold. Ordinary rate-warning text remains timed; genuine visible human
verification or a genuine unsuppressed refusal HEAD title retains a hold.
Literal robots 4xx other than 429 means unavailable REP; content 401/403/451,
robots Disallow, actual challenges and stronger native policy still stop.
Current and legacy bot groups must both permit access. No identity switch,
verification bypass or alternative source route is used.

## Exact legacy transition and local serialization

The old adapter kept monotonic pacing only in process memory. Its six recovered
completed attempts have no owned state artifacts. Their exact job starts and
completions are conservative historical bounds, not reconstructed HTTP starts
or Retry-After. Schema 3 therefore retains
`legacy_rate_history_unknown: true`; a new observed rate counter never claims
lifetime zero. No authentic older durable schema is silently upgraded.

The one-off legacy transition is disabled until an explicit maintainer approval
binds the exact six run/head/job records and full conservative state. It is not
a generic empty-family bootstrap. A new run, rerun, changed job or unexpected
artifact invalidates that exception. After the first modern run, the newest
exact owned artifact, digest, main head and attempt/job chronology are mandatory,
including a previous completed attempt of an older queued rerun.

Every local execution first requires the actual workflow to be paused and the
fresh complete family history to contain no active or queued collector. History
and reviewed state are read before creating a journal. Stable source locks
serialize local callers. After startup quiet, local execution rechecks the
actual disabled workflow immediately before its first publisher request.
Production validates the exact executing main job and
head, and restores state before the data-source snapshot. A first production
handoff separately requires maintainer approval of the full native MAX; it may
not omit the local test's requests, restrictions or unfinished flights.

## Time limits and verification status

The existing job limit remains 30 minutes. Stricter internal limits are 900
seconds for collection, 1290 for the whole execution and 30 reserved for export;
the actual hosted job start can only shorten them. The publication token is
still created before the source command. The complete original reader has two
assigned HTTP roots: robots and one programme HTML document. Redirects and the
legacy bounded ordinary-network attempts may add physical requests; two roots
is not a measured two-request claim. No 429, actual denial, challenge, TLS or
state-write failure is retried. Stronger retained pacing can make a later run
fail closed within these unchanged outer limits; scope is never truncated.

The migration is not yet approved for publisher access or production. Offline
controls use inert responses and private journals. Historical success timings
are partial evidence only; they do not certify the new spacing or fresh current
consumer behavior. A separately released sole original native test must retain
all actual headers, bodies, available partial bytes, stdout, records and every
saved state in private RAM, and pass independent current-consumer checks.
Normal programme parsing, identifiers, expiry and publication error handling
remain unchanged. No fresh source request or live journal write was used to
prepare this implementation.

## Native evidence and production approval: 15 October 2026

The preceding verification status records the pre-access stage. The maintainer
subsequently approved the exact local transition in
[comment 6202384346](https://github.com/YouthOpps/data-pipeline/issues/269#issuecomment-6202384346)
and released one original native test in
[comment 6202421418](https://github.com/YouthOpps/data-pipeline/issues/269#issuecomment-6202421418).
That unchanged test passed: one programme record from two complete HTTP 200
responses. Its 85-second duration includes restoration and startup quiet.
Physical starts were 12.558 seconds apart; response completion to the next
physical start was 12.355 seconds, with two starts in the rolling minute.
No inventory request was needed. All actual headers, bodies, stdout and nine
saved budget snapshots were retained in private RAM.

The external controller then exited with failure in its artificial expired
fixture: a date-only deadline was rejected by the unchanged timestamp validator.
That failed controller receipt remains preserved. An offline supplement replayed
the same retained actual output with a valid expired timestamp on a derived
copy, without another source request or native test. Every actual record field
matched the original parser on the genuine HTML; the current website consumer
accepted one raw record, one expiry-filtered record and an empty derived subset.
All three consumer dependencies were bound. Independent review accepted the
original test and qualified offline supplement; the controller itself was not
relabeled successful.

The maintainer separately approved the exact full-state first-production handoff
in [comment 6202558732](https://github.com/YouthOpps/data-pipeline/issues/269#issuecomment-6202558732).
Its 21 state witnesses preserve nine save arguments, nine persisted states,
the original legacy state, the terminal state and the pre-wire state. The 14
conservative timing observations comprise six historical job completion bounds,
two reservations, two actual wire starts, two response-open observations and two
response completions. These observations describe two physical requests. Four
pending snapshots were settled only by their two matching actual requests,
complete responses and durable completion writes. Legacy rate history remains
unknown; no absent historical counter is interpreted as lifetime zero.

The workflow carries only that approved full-state envelope. Runtime restoration
keeps all stronger constraints and at least 60 seconds of fresh startup quiet.
Production still requires the maintainer-controlled enable-before-merge and its
single automatic source run, followed by owned-state and published-pair checks.
The native evidence and configuration approval do not establish production or
website delivery success. Normal parsing, identifiers, expiry and publication
behavior remain unchanged; the supplement and state review made no additional
publisher requests.


## Journal integrity maintenance (#295)

This guard-only maintenance retains a genuine RW journal descriptor, its
current named inode and byte generation under the original publisher lock.
Reads are checked before and after access. A commit serializes in RAM, rechecks
authority, then truncates, syncs, writes fully and verifies readback through
the same held inode. Only a successful own commit advances the expected
generation. A changed parent, stronger replacement, missing mature journal or
unexpected generation stops collection before another publisher request.

This accepts an availability tradeoff: interruption may leave empty or partial
JSON. Invalid mature state requires reviewed recovery; it cannot become a fresh
budget or be restored from an older owned artifact. Failure cleanup never
reopens a newer journal pathname for mutation. Locks are advisory, and
generation checks are neither an atomic filesystem compare-and-swap nor
authentication against arbitrary same-UID writers that ignore those locks.

Actual acquired collection, journal, artifact, reader, step-output and parent
descriptors retain identity through use and close. Recycled foreign descriptors
are preserved. A healthy close error is fatal; an active primary exception is
preserved. Owned output is invalidated on a fatal handoff even if readiness
signalling or unlink fails. A previously written true readiness line alone is
not proof of a valid artifact.

The state schema, approved seeds and markers, strict newest-owned history,
source parser, complete frontier, IDs, dates, expiry, network retry behavior,
physical/page pacing, caps, publication and daily schedule are unchanged.
Private ordinary checks cover successful requests, initial modern restoration,
successive exports, no-acquisition exits and the original bounded retry
behavior. No publisher request, new native test, journal migration or
production action was performed for this maintenance.

The complete earlier actual retained records (1 raw / 1 filtered) were replayed
through unchanged validation/expiry and the byte-identical current consumer,
including valid empty and last-good-on-failure cases. Source-content
completeness and factual verification remain the earlier independent native
evidence plus unchanged normal AST; no new native or source-body collection is
claimed.

The original NASA unittest passed. Its external controller subsequently failed
a derived expired-date fixture; that original FAIL receipt remains preserved.
The earlier independently accepted corrected offline supplement and actual
retained original stdout support the qualified record replay, without
relabelling that controller result.


## Header-only HTTP 429 stop — issue #328

A received publisher HTTP 429 now stops after the existing header constraints
are retained, before reading any response body. The response must close
successfully before this intentional stop is assessed. A close or persistence
failure follows the existing failure handling; they cannot establish a healthy
collection. Previously retained holds, numeric MAX constraints, Retry-After
and unknown history remain. An unread 429 body provides no new challenge
classification.

This paragraph supersedes earlier 429 body-inspection descriptions. This
conditional repair preserves non-429 responses, REP selection, the full
collection/parser/IDs/dates/expiry, GitHub error-body handling, publication,
caps, workflow, state schema and finite migration markers. It does not add
missing pending ownership or upgrade a legacy source to a modern continuity
protocol.

Private ordinary transport/state controls and protected normal-AST comparisons
qualify this change. Earlier genuine native evidence retains its original code
and date attribution; no new publisher collection, current factual review,
complete native replay or production run is claimed. The maintainer alone owns
workflow/state coordination and the maintenance rollout for issue #328.


### Interrupted journal writes

Each in-place truncate and partial write is acknowledged only after its exact
bounded bytes and full inode generation are checked. The next mutation and
failure cleanup require that same acknowledgement. A stronger same-inode update
observed during a write is preserved; the process latches publisher access
closed instead of continuing or truncating that update. These checks retain the
existing 16 KiB journal bound, state schema, MAX restrictions and publication
behavior. They detect observed generation changes; they are not filesystem
compare-and-swap operations.
