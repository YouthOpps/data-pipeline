# Study in Flanders scholarship adapter

Official assigned source: https://www.studyinflanders.be/scholarships/master-mind-scholarships . Publisher: Study in Flanders, operated by VLUHR. This standalone Python standard-library adapter reads the complete finite own scholarship frontier; it does not crawl other publishers or submit applications.

## Access and attribution

Reviewed 10 October 2026: official robots excludes `/cpresources/`, `/vendor/` and `/.env`; all required public routes are permitted. The actual footer links [Privacy Policy](https://www.studyinflanders.be/privacy) and [Cookie Policy](https://www.studyinflanders.be/cookie-policy). These govern personal data/cookies and do not establish a prior-permission requirement or automation ban for factual summaries. Copyright is retained: this is not an open-licence claim. Publish original concise factual summaries, attribution and canonical publisher links, without images, protected full prose or applicant data. No form submission, login, contact or external catalogue access.

Every run retrieves fresh robots and ten exact public HTML inputs: scholarship hub, MasterMind main/eligibility/application/host/payment pages, US funding overview, privacy, cookies and the linked Russian-mobility policy notice. Only one `main` article and its complete substantive text/links are fingerprinted; site navigation, footer copyright year and opaque client chrome are not opportunities. Exact canonical URLs and complete HTML are checked. A material change to facts, policy or the finite inventory stops the whole collection before publication; no partial stale subset is emitted. Unexpected publisher redirects are rejected before destination access, after bounded denial-body inspection.

## Complete inventory and identifiers

Four actual identities: `Master Mind Scholarship` (closed 2026–27 call), `Belgian American Education Foundation Fellowships (B.A.E.F.)`, `Fulbright U.S. Student Program` and `Fulbright U.S. Scholar Program`. The latter two are separately named and have separate application links in the own US overview; their joint activity description is not treated as track-specific eligibility. The three US records share the actual own page without invented anchors, but have distinct stable semantic identifiers prefixed `be-study-in-flanders-`. No programme instance is synthesized from capacity, priority country or host institution.

The hub's external Erasmus Mundus and VLIR-UOS entries and nine institution/provider funding links are referrals, not own substantive schemes. Programme finder, GPA calculation manual, Russia policy news and host/application/payment pages support the primary record; they are not additional opportunities. The own hub/US page have no pagination or load-more controls. Twelve MasterMind institutions are one award framework, not twelve awards.

All original titles and summaries are English; BE is a destination, not eligibility nationality. BAEF eligibility is US citizenship **or** US permanent residence, so no passport-only whitelist is emitted. Its undated own overview states one academic year of advanced study/research, up to ten fellowships, $27,000 for master's/PhD and $31,000 for postdoctoral fellows; it is not evidence of a current external call. Fulbright overviews explicitly require American citizenship and provide no own deadline, amount or detailed track criteria. Their status remains unknown. MasterMind's closed statement supports expired status with a null deadline: no single central public cutoff is supplied. The ten institution dates are not a synthesized central earliest/latest deadline.

Public summaries contain major criteria and payment conflicts within 580 JavaScript UTF-16 code units; complete conditions remain on the linked source. Categories and kind are separate: MasterMind is `opportunity`/scholarships; the three standing records are `programme-overview`, BAEF fellowships and Fulbright scholarships. Null publication/deadline fields are retained when no source date exists. Actual UTC observation timestamps and source attribution are preserved.

## All MasterMind material conditions

60ECTS per academic year,60ECTSone-year or120ECTStwo-year master's; participating Flanders/Brussels host acceptance plus one host nomination (not direct central application), last-degree GPA3.5/4 measured with Scholaro. Prior qualifying degree cannot be from Flemish HEI; previous credit-mobility students eligible. Existing Flemish enrolment ineligible except exact Spring2026 preparatory route to September2026 master's. Preparatory/bridging/distance-learning award programmes excluded. At least half credits at Flemish host; outbound mobility allowed. All nationalities except Russian; JP3/MX3/PS2/US5 reserved priorities not whitelist. Maximum20applications per host not scholarship capacity. Cannot combine other Flemish government/Erasmus+/Erasmus Mundus/BAEF/Fulbright scholarships. Loss may require full tuition; incomplete programme may require grant refund.

English proof <=3years old: university/university-of-sciences IELTS7/TOEFL94/C1 or recognized equivalent; arts IELTS6.5/TOEFL90/B2/equivalent. How-to-apply also permits host exemption letter; no unconditional waiver inferred. Application documents: ID/passport, CV/GPA, transcript and diploma with certified translations when not NL/FR/DE/EN, English motivation/two signed English recommendations, English proof/exemption, photo. Apply to participating host before its date; host preselects then creates Mobility Online file, student completes private-tool deadline, host validates central submission. No tool/form access.

2026–27 grant EUR10225 per academic year plus fee waiver; permitted low tuition EUR136.50 (not zero-guarantee), ordinary housing/insurance/travel coverage not stated. Main notice under Ministry review for2027–28 new applicants, so no future edition or carried-forward new-call benefit. Payment leaf promises same10225 for second year of ALREADY submitted two-year award. First-year4600 arrival/registration (optional2000prior+2600arrival),4100Feb2027,1525completionJuly orresitSept/Oct2027. Second-year>=54ECTS continuation:6125prior,4100Feb2028,1525completion. These second-year listed installments sum11750, conflicting with annual10225; preserve contradiction in details and never silently derive a new benefit. First-year54ECTS failure loses final1525+year2 grant unless granted exception; master's completion required final installment; partial withdrawal refunds (one semester keeps4600example).

Twelve hosts, ONEframework. Ten institution-specific date-only deadlines: Leuven2026-01-15, Antwerp02-01,Ghent04-01,Hasselt03-15,VUB04-01,AP03-01,HOGENT03-20,ErasmusBrussels04-13,KdG03-31,LUCA04-01. AntwerpMaritime/PXL contact-only, no inferred date/clock/zone. Unlisted host ineligible. Preserve table in details/README, no central date calculation.


## Run and validate

```sh
python -B adapters/be-study-in-flanders/test_adapter.py
python -B adapters/be-study-in-flanders/adapter.py --publish
```

The sole test retrieves nonempty real complete records without publishing and emits one complete JSON array on stdout; diagnostics go to stderr. Validate stdout in memory without saving collected records. Copying this folder outside the repository remains supported; Python standard library only. Publication requires `DATA_SOURCE_TOKEN`; collection tests must not receive it.

## Pacing, deadlines and publication

Family `youthopps-studyinflanders-publisher-v1` shares persistent starts,
embargoes and refusals across research, tests and production. Each physical
publisher attempt waits at least 12.2 seconds after the previous attempt
completes; at most five starts may occur in a rolling minute. The three
inventory inputs are the Master Mind overview, US funding overview and
scholarship hub. Each physical inventory attempt waits at least 30 seconds
after the preceding inventory attempt completes, including a failed attempt
or the existing single bounded 503 retry. The other seven detail/policy pages
and literal robots use ordinary physical spacing. Unreviewed redirects remain
rejected; no new route is followed.

A 429 stops the current run without another publisher request. Before reading
its body, save doubled CURRENT physical and inventory gaps, a new-strike
counter and at least 1800 seconds of exponentially increasing embargo; any
longer valid Retry-After or retained MAX remains effective. Invalid, stale,
negative, nonfinite or unsupported Retry-After alone adds no access hold.
Ordinary rate-warning body text remains timed; a genuine primary human
challenge or unsuppressed HEAD refusal title also retains a hold. Actual content 401/403/451 and explicit robots
Disallow retain a durable hold. Literal robots 4xx except 429 means unavailable
REP rules; robots 429/5xx/TLS failures stop. Body guards run before redirect
handling and ignore hidden/script/footer/navigation content, including the
reviewed implicit paragraph-close case. Attribute/inline visibility is not a
full stylesheet renderer. Exact bot identity, normal TLS and existing access
restrictions remain required.

Legacy schema-one state preserves every known start, embargo and hold and
upgrades to typed schema two. Missing legacy rate history is durably marked
unknown; a zero new-strike counter is not evidence of lifetime zero 429s.
Restoration merges every known event without truncation (bounded union of
512; overflow fails), numeric MAX and hold/unknown OR. The actual initial
owned artifact contributes ten retained starts and local state contributes
two older starts. Preparation imposes a fresh sixty-second quiet period;
stronger retained pacing can make the fixed collection budget infeasible,
which fails closed without raising caps, resetting state or cutting inventory.

Collection and both prepublication gates are 900 seconds. Original whole-run cap is 1290 seconds: nonexport operational cap1260, publication180, nonrecursive failure180 and trusted state export30. All phase/read/sleep/parse/output deadlines clip the original absolute alarm; expired phases are not renewed. Source socket timeout60/API15 additionally clip phase time. The Action has a 30-minute job timeout and mints the source-scoped one-hour GitHub App token immediately before its sole application command. Export holds the local collector lock through cleanup.

Production and local tests both require the newest exact completed family
attempt's owned artifact from complete stable bounded history. Exact-attempt
jobs bind immutable head, branch, repository and actual completion chronology;
a queued old-ID rerun retains its preceding completed attempt. The current
hosted run must appear with its exact head and attempt. Validate signed-hop
transport, ZIP digest, envelope, source and state; qualify the actual legacy
six-key envelope with immutable API owner metadata, and export new head-bound
envelopes. Missing newest state fails closed without an older fallback or
first-family bootstrap. Restore and trust state before reading the published
snapshot, and attempt finally-export even after later snapshot/auth failure.
Upload only a freshly finalized, owned state artifact signalled by the sole
application step. Keep the stable local collector lock through export/cleanup.

The exact full-native-MAX maintenance state is
[maintainer-approved](https://github.com/YouthOpps/data-pipeline/issues/225#issuecomment-6199351980)
and bound in the workflow. It supplements, never replaces, mandatory newest
mature artifact/local restoration. All 34 known starts remain: twelve earlier
retained events plus eleven native reservations and eleven measured wire
starts, without coalescing. Numeric MAX, hold/unknown OR and native completion
plus sixty seconds are preserved; runtime additionally waits a fresh sixty
seconds. Root owns re-enabling before merge and the sole automatic production
run; production remains unverified here. The daily UTC cron remains
`21 10 * * *` (10:21 UTC); no extra schedules or manual runs are introduced.

The single main-push/manual/daily workflow publishes only `datas/be-study-in-flanders/{data,metadata}.json`. Success atomically creates/replaces both files through source-scoped non-force compare-and-swap, preserving all sibling sources. Validate prior complete own pair before changes. Initial failures create no folder; later failures preserve last-good data and last-success time, updating only safe failure metadata when possible. Uncertain commits are reconciled against exact desired bytes; newer own pairs are never overwritten. Logs distinguish unconfirmed durable outcome from saved failure. Source metadata is the durable status record after first success; pacing artifacts contain infrastructure state only.

## Expired-record cleanup

A refresh first retrieves and validates the complete nonempty publisher inventory.
Only then are records with a verified past `deadline` removed from the next
source snapshot, including expired identities from the previous publication.
The existing full replacement does not merge old records back into the result.
Date-only deadlines remain eligible through their closing date. With no
documented publisher timezone, cleanup uses the conservative UTC-12 calendar: the
closing date must have ended globally before removal.
Explicit timezone-aware timestamps expire strictly after the stated instant;
missing deadlines remain eligible; unresolved publisher dates stay null.
Invalid normalized deadlines fail validation and preserve last-good data. Status, record age and
absence from a response are not expiry evidence. Publisher inventory coverage
continues to be checked before filtering.

The refreshed subset and success metadata are published atomically within this
source. If every validated record has expired, success publishes an empty array;
an empty raw retrieval still fails validation. Previously published empty arrays
are valid snapshots. Retrieval, parsing, validation and publication failures
preserve last-good data and its success timestamp; safe failure metadata may
be updated. The sole live test remains non-publishing and verifies the complete
raw inventory. Cleanup and failure boundaries are checked separately without
production execution.

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

The scheduled refresh runs once daily at **10:21 UTC** (`21 10 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force; this source changes only its
daily automation schedule.

## Workflow concurrency

`youthopps-studyinflanders-publisher-v1` serializes runs of this source. Up to
100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **VLUHR** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Pacing migration validation

Issue [225](https://github.com/YouthOpps/data-pipeline/issues/225) changes only
source-local transport, durable pacing/artifact handoff and documentation.
The ten exact input URLs/fingerprints/order, four profiles, output facts,
normalization, validation, expiry and atomic publication remain unchanged.
Complete retrieval is necessary because no verified publisher change cursor
or delta-removal semantics exists for these separately fingerprinted pages.

Historical full-native evidence reports four genuine records and all eleven
requests, but its full bodies/stdout are no longer retained. The pre-access
review also consumed the actual current four-record pair through the current
website consumer. Neither historical evidence nor timing estimates certify
this new pacing. Independent inert controls and real read-only newest-owned
artifact/local MAX qualification precede a separately authorized sole complete
native run. That run must retain all actual response headers, complete and
partial bodies, stdout, records, budget snapshots and wire timings in private
RAM, followed by independent all-input/all-record/current-consumer review.
No publication credential or production Action is a test harness.

## Measured validation on 15 October 2026

One genuine complete nonpublishing test passed on the unchanged final source:
fresh robots and all ten reviewed HTML documents, four raw records, four after
supported deadline cleanup and zero in the derived all-expired case. All eleven
actual responses were HTTP 200. Independent review checked every original
fingerprint and all four non-temporal record projections. Actual current
website consumer replay accepted 4 raw, 4 filtered and 0 valid-empty records
with exact IDs, including the freshly changed consumer after the test.

The complete test took 224.608 seconds within the unchanged 900-second cap.
Minimum measured physical completion spacing was 12.36189 seconds, inventory
completion spacing 30.44577 seconds, and at most five starts occurred in any
rolling minute. Three actual inventory hubs and all seven details/policies
used their separately reviewed purpose. The 152 inert access/history/lifecycle
checks, original-reader callable integration and independent extra controls
passed before access; none substituted for the genuine test.

All actual response headers (including duplicate fields), complete bodies,
available incomplete-read partial bytes, full stdout and records remain in
private RAM for review. Twenty-four actual budget saves plus six other state
inputs supply the thirty MAX inputs; this is not thirty actual saves. Initial
local state, before-first-GET raw hashes/inodes and stable locks prove mature
restoration and startup quiet. Missing historical rate counts remain unknown.
No publisher request followed the sole test, no cap was raised and no
production Action was used for validation. Source/test bytes remain unchanged;
only the approved workflow marker and this README changed afterward.


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

This existing state schema has no durable pending field. This IO correction
does not add one or claim cross-process protection when both persistence and
original-inode invalidation fail. That separate protocol migration remains
tracked by #273.

The earlier independently accepted complete native evidence is qualified by
unchanged normal source AST. Its original RAM capture has expired; this
maintenance does not claim a fresh full-record or full-body replay for this
source.


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


### Mid-commit journal generation protection (#295)

Each successful truncate or partial positional write is acknowledged only after
reading back its exact expected complete bytes and stable inode generation.
The next write, synchronization check and final readback require that last
acknowledgement. A stronger same-inode update during a write is preserved:
the process fails closed, and failure cleanup cannot truncate a changed
generation merely because this writer previously started a commit. Interrupted
owned bytes can still be invalidated through the retained verified descriptor.
This is checked descriptor I/O, not a filesystem compare-and-swap guarantee.

Private actual-function fault controls qualify this bounded I/O repair. Existing
public native evidence retains its original revision and date; no new publisher
requests, original expired raw-body replay, publication or production run are
claimed. Collection, facts, identifiers, deadlines, expiry, state schema,
pending transactions, MAX constraints and workflow behavior are unchanged.
