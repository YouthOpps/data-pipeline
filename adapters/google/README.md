# Google student and early-career programme information

This source collects 15 substantive Google programme information pages. It does
not collect the worldwide Google vacancy inventory or claim that a programme's
individual applications are currently open. Each record links its actual Google
page and uses an original factual summary of benefits, entry conditions and any
published date conflicts. Publisher country `US` identifies Google LLC; it does
not limit applicant nationality.

The official starting site, <https://buildyourfuture.withgoogle.com/>, directs
visitors through its own literal script to Google's current student site:
<https://www.google.com/about/careers/applications/buildyourfuture/>. Twenty
Google HTML inputs retain the complete visible material, literal links and form
and filter controls, including all hidden cards: one scholarship, three programme
cards, twenty apprenticeship cards and eight early-career cards. Three internship
information tracks are covered without claiming exhaustive vacancies. One paid
apprenticeship overview represents all twenty tracks, rather than creating fake
individual listings from shared search links. Military-transition SkillBridge and
the experienced career-break gCareer route are excluded.

The original redirect shell, its exact reviewed literal script and Google terms
are also required. The collector strictly JSON-decodes the first HTML string of
the observed `ds:0` callback; it never evaluates scripts or calls guessed APIs.
Material changes, missing inputs, access challenges or a changed finite frontier
fail closed. Google's optional outer closing HTML tags are not mistaken for an
incomplete response; the embedded programme document must be complete, and HTTP
length/body limits remain enforced. The original redirect shell and JavaScript may return the observed `gzip`
content encoding. Reviewed publisher routes may
use one complete gzip member; compressed and decoded bodies are each bounded to
eight MiB. Invalid checksums, truncated streams, concatenated members, trailing
bytes and unknown or combined encodings fail closed. Both encoded and decoded
publisher bytes count toward the operation's 64-MiB aggregate byte ceiling;
request, phase and body limits remain unchanged.

Fresh robots policies are checked on all three required origins. The bounded REP
matcher preserves queries and percent octets, supports wildcards and longest
matching rules, and favors Allow on equal specificity. This prevents Python's
standard robotparser from incorrectly treating Google's `Disallow: /?` as a ban
on every path. Actual candidate-preparation, connection and job-result pagination
exclusions remain enforced. Stricter rate extensions require fresh review.

Google terms prohibit automated access contrary to machine-readable instructions.
This source republishes original factual summaries and links, without claiming an
open content license. The Ireland scholarship's complete eligibility is delegated
to IIE; IIE's terms prohibit automated extraction. No IIE facts, forms or FAQs are
collected. The record retains only Google's EUR 5,000/year for two years, selection
basis and October 5, 2026 closing, and explicitly leaves complete minimum
eligibility unverified. Other delegated application, research, account and
administrator sites are links only.

Explicit closed or past calls are marked expired. Yearless recruitment seasons
have no invented deadline. Contradictory or unverified availability stays unknown.
The US Legal Summer Institute's 2027 closing date is retained with its conflicting
cohort and eligibility statements. No timezone, nationality restriction or current
vacancy is inferred.

## Run and publication

Run `python -B adapters/google/test_adapter.py` for one complete live,
non-publishing collection. The test emits all actual records as one JSON document
on stdout. It needs no publication credentials and writes no collection files.
Independent static and live acceptance are required before delivery.

Every physical publisher start, including robots, redirects and the existing
single bounded 503 retry per input, uses one exclusive
`youthopps-google-publisher-v1` family: at least 12.2 seconds after the previous
physical attempt completes and no more than five starts per rolling sixty
seconds. The six inventory hubs are the student root, scholarships, programs,
apprenticeships, internships and early-career pages. Every physical inventory HTML attempt, including each redirect and retry,
waits at least thirty seconds after the previous inventory attempt completes.
Their complete fetch/parse units also retain a thirty-second completion guard;
the three intervening details before early-career naturally contribute elapsed
time. Other details, the redirect shell/script and terms use physical pacing.
Literal robots.txt requests use ordinary physical pacing even within an
inventory redirect chain; they never acquire inventory content purpose.

Local collection locks prevent overlap. All four original research budget files
remain locked and merged without modifying their inodes: interval/embargo use
MAX, refusal uses OR and every known start is retained before access. Missing
legacy rate/page fields become durable `legacy_rate_history_unknown: true`;
a new zero strike counter describes only observations under the new schema.
The full retained union is bounded at 512 timestamps and fails closed rather
than truncating it. Ordinary pacing ages expired starts only after restoration
and the startup guard. Legacy mixed holds never clear automatically.

A 429 stops the current run without another publisher request. Before reading
its body, it durably doubles both current physical and inventory intervals,
increases its strike counter, and retains at least thirty minutes of cooldown,
increasing exponentially on subsequent refusals. A valid longer Retry-After
wins; invalid, stale, negative or unsupported headers never create a hard hold
on their own. Generic rate-warning text remains timed. Actual content 401/403/451
or a primary visible human-verification/denial challenge retains a hard hold.
The visible-text guard excludes chrome and explicit hidden DOM, including
implicit paragraph closure; it does not implement full stylesheet rendering.
Literal robots.txt requests and every redirected hop retain their purpose:
robots-only 4xx except 429 means unavailable REP, while robots 429, 5xx and
transport failures stop. Explicit applicable robots restrictions remain stops.

The unchanged normal frontier is 26 physical starts: all 23 guarded inputs in
original order plus three robots origins. A rough estimate from the earlier
271-second collection/publication step is about 497 seconds at the new floors;
this is not proof of fit. The hard caps remain 100 starts, eight MiB per response,
64 MiB aggregate bytes and 900 seconds for prepublication, including history
restoration. The whole operation remains 1290 seconds, with 180-second
publication/failure phases and thirty seconds reserved for inert state export;
the job remains thirty minutes and its daily UTC slot remains 16:09. The App
token is minted immediately before the sole application command. Stronger
restored limits, network delays or post-429 slowdown can exceed these caps;
the run then fails closed and preserves last-good data without relaxing limits.

Both local validation and production restore the newest exact owned family
artifact first. The complete Google workflow history validates repository,
source path, main branch, immutable head, exact attempts and actual completed
jobs. A queued rerun retains its previous completed attempt; missing newest
state fails closed without selecting older state. The historical six-key
Google envelope is accepted only with exact matching attempt/jobs and artifact
head metadata; new envelopes also contain head_sha. Signed downloads use only
reviewed HTTPS artifact storage, with digest, ZIP member and byte bounds.

The obsolete timestamp-only first-family bootstrap is removed. This mature
family requires real history. Production additionally requires
`GOOGLE_PACING_MAINTENANCE`, a maintainer-linked full native budget state merged
with the latest owned artifact and local/research MAX. The exact full state is
[maintainer-approved](https://github.com/YouthOpps/data-pipeline/issues/219#issuecomment-6197983326)
and bound in the workflow. It retains all 65 known timestamps, including the
13 prior events and both 26 native reservations and 26 measured wire starts,
without coalescing them. A fresh sixty-second quiet period follows restoration;
no publisher budget is reset. Root owns workflow re-enabling and the merge;
production execution is still unverified here.

Only `datas/google/data.json` and `datas/google/metadata.json` can be published.
Success replaces both in one source-scoped compare-and-swap commit and preserves
all siblings. Existing source pairs are fully validated before publisher access.
A first-run failure creates no source folder or empty/failure pair. Later failures
preserve every last-good data byte and success/check timestamp, updating safe
failure metadata only when the complete prior owned pair remains unchanged.
Record creation/first-seen times survive matching identities; update time changes
only with substantive fields. Last-seen uses collection START, and every record's
last-checked and metadata's last-checked use actual collection END.

Each physical publisher attempt persists completion plus the native interval,
including failed opens, body processing and response closure. A monotonic gate
also enforces the elapsed interval; longer inherited intervals and embargoes
remain effective. The exclusive family collector preserves a full 60-second
startup embargo after legacy/artifact restoration to age abandoned attempts.
A failed completion-state save prevents any further publisher attempt.

## Expired-record cleanup

Google retains supported dated Summer of Code, US Legal Summer Institute and
Google Ireland scholarship application closings. Null deadlines and conflicting
programme status wording never authorize removal.

The complete fresh inventory is fetched and validated before cleanup. The next
source-scoped atomic data/metadata publication replaces the prior dataset rather
than merging its rows, so expired previously published records cannot survive
that successful replacement. Current records and records with missing or
ambiguous deadlines remain eligible; status alone does not determine expiry.

Offset-aware deadlines expire only after the stated instant. A date-only
deadline with no documented publisher timezone remains eligible through that
date in every civil timezone: cleanup uses the conservative UTC-12 calendar
boundary, without guessing a publisher timezone or an unstated closing hour.
The source recipes and their original deadline fields remain unchanged.

A valid nonempty fresh inventory may produce an empty cleaned dataset; that
empty result is published atomically with success metadata. An empty upstream
collection still fails validation. Fetch, parsing, validation and publication
failures preserve the last-good dataset (including a prior empty success) and
success timestamp; cleanup never performs a separate deletion or writes another source.
The sole live test validates the complete nonpublishing inventory, including
closed records needed to verify source completeness; publication alone applies
the deadline filter after that validation.

On 14 October 2026, the reviewed original shell at
https://buildyourfuture.withgoogle.com/ returned HTTP 200 with Content-Encoding
gzip: its 96,098 decoded bytes matched the existing material fingerprint. The
bounded standard-library gzip decoder now applies to every reviewed publisher
route. Wire and decoded byte limits/accounting, fingerprints, robots checks and
transport policy remain enforced; unsupported encodings still fail with the
reviewed route in evidence.

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
budget identities and stronger retained restrictions remain in force. The
pacing and timed-429 behavior above supersede the original floors. Existing
disabled workflows remain disabled until root coordinates their recovery.

Google access also retains the previous YouthOpps policy result, including its
wildcard fallback. A new YouthOppsBot Allow therefore does not override a
previously applicable wildcard Disallow; this intentionally remains blocked.

## Daily automation

The scheduled refresh runs once daily at **16:09 UTC** (`9 16 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Root coordinates re-enabling the
workflow after this validation pause; durable access holds still require
separate reviewed recovery.

The collector has no offset pagination. Its six finite inventory hubs still
use the separate completion-to-next-inventory guard documented above. The
original complete frontier, publisher restrictions and daily slot are retained.

## Source display name

Published metadata uses `display_name` = **Google** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Pacing validation on 15 October 2026

One genuine complete nonpublishing collection passed on the unchanged final
source: 23 guarded inputs plus three robots requests, fifteen raw records and
thirteen records after deadline cleanup. The actual current website consumer
accepted 15 raw, 13 filtered and 0 valid-empty records, with exact ID agreement.
Independent review checked all actual fingerprints and non-temporal fields.
The full native took 462 seconds within the unchanged 900-second cap; minimum
physical completion spacing was 12.21775 seconds, minimum physical inventory
spacing 30.12985 seconds, and at most five starts occurred per rolling minute.
All 131 offline access, artifact, state and lifecycle controls also passed.

Actual response evidence retains bodies, statuses, timing and selected
Content-Type/Content-Encoding headers in private RAM. Full raw headers were
not retained; absence of Retry-After is not inferred from that selection.
Sixty actual budget-save snapshots plus the prior artifact, prior local state
and final native local state supply the 63 MAX inputs. The new strike counter
records only new observations; unknown earlier rate history remains explicit.
No additional publisher requests or production Actions were used for
validation. The maintainer-approved workflow seed and runtime quiet guard
preserve this history before the automatic merge-push production attempt.

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

## Journal ownership maintenance (#295)

Publisher journal reads and writes retain the acquired original RW descriptor,
parent identity and file generation. They check that authority again before
transport and export. A changed name, generation, lock or descriptor fails the
publisher gate; cleanup never adopts or truncates the stronger replacement.
Serialization, write, fsync and release failures remain sticky for the process.
Cleanup attempts independent original descriptors and preserves a prior error.
Safe owned artifact output is invalidated before best-effort signaling or
unlink; foreign descriptors and unsafe existing output are preserved.

Journal updates now write through the retained, locked inode instead of
replacing it by pathname. This deliberately trades atomic journal availability
for original inode ownership: an interrupted update may leave an empty or
partial mature journal, which fails closed. It cannot be healed from an older
hosted artifact. The actual data publication still uses its existing atomic
GitHub transaction.

Only an actually newly created publisher namespace, during its existing verified
latest-owned restoration, can initialize neutral state for the incoming MAX
merge. An existing directory with missing or empty state fails closed. GitHub
publication transport does not establish publisher namespace creation proof.

This maintenance preserves source facts, frontier, dates, expiry rules, schemas,
seeds, history pins, pacing limits, retries, publication and workflow settings.
Validation uses actual functions with inert transport and private journals, plus
prior accepted native evidence for unchanged collection functions. It does not
certify a new crawl, production run or every legacy runtime contract. Artifact
output assumes the existing trusted private job/temporary-directory boundary; it
cannot authenticate an arbitrary concurrent same-UID writer forging output.

The earlier native acceptance remains historical evidence. This patch does not
claim a fresh full-body replay from expired RAM or a new publisher request.

Reference: https://github.com/YouthOpps/data-pipeline/issues/295

These existing schemas do not add a durable pending-transaction marker here.
If both the restrictive write and original-inode invalidation fail, a restart
still needs the separate #273 protocol correction. This journal-ownership patch
does not claim to resolve that broader historical persistence limitation.


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
