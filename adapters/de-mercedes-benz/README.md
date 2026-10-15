# Mercedes-Benz public youth vacancies

This standalone adapter indexes the official public job-search API linked by
https://jobs.mercedes-benz.com/en. It uses the publisher's youth career-level
codes 19–27, EN search language and ascending PositionID order. It retrieves all
matching current language variants, grouping only the same publisher PositionID.
Separate J000 identifiers are never merged.

## Inventory and factual output

Each traversal discovers the current total and reads every 100-row window with a
three-row overlap. A second complete traversal must have the same total, variant
IDs and complete descriptors before normalization. Duplicate conflicting variants,
missing pages, changed totals or material changes between traversals fail without
publishing partial data. This checks consistency across two observations; it does
not claim an atomic publisher snapshot. The existing bound of 1,552 variants and
42 physical requests remains in force.

Frozen per-vacancy recipes and material hashes no longer supply the output or
require manual updates when current vacancies change. Each live record contains
its title, original URL, location, actual host countries and the publisher's
career-level labels. A short original English template explains the listing type
and directs readers to the original notice for role-specific eligibility and
application conditions. Descriptions, creative task prose, qualification sections,
images, applicant details and contact information are not republished. Full API
material is used only in memory for structural validation and SHA-256 freshness
provenance. Additional entry-condition URLs are retained when actually linked by
a descriptor; this index does not reproduce or independently summarize all their
conditions. Source attribution and all current variant URLs remain explicit.

Career labels and titles classify apprenticeships/dual study as training,
internships as internships, and working-student/holiday/thesis/doctoral placements
as jobs. Explicit hybrid working-student/internship listings retain both
categories. A working-student title does not become an internship merely because
the career label also describes placements. Country arrays describe advertised
host locations, never citizenship eligibility. Publication-language numeric codes
do not establish prose language, so language stays unknown. Date-only API
publication/start/end fields remain provenance, not invented application deadlines.

IDs preserve the existing SHA-256 namespace
`de-mercedes-benz|mercedes-public-position|<PositionID>` (first 24 hex characters).
Newly discovered positions use the same rule. Final IDs, uniqueness, complete
records and source ownership are validated after normalization. A complete scan
remains necessary: no documented modified-since/removal feed or durable change
cursor establishes safe coverage of edits, reopening and removals. No known-ID
pagination shortcut or incremental watermark is used.

## Access, robots and retained recovery

The user's current factual-indexing instruction permits ordinary public factual
indexing when no clear restriction applies. The official
[provider notice](https://group.mercedes-benz.com/provider/) reserves rights in
creative text, images and arrangements. The
[careers terms](https://group.mercedes-benz.com/careers/general-terms-conditions.html)
and four publisher-linked student/school entry-condition pages are retrieved
before vacancy traversal. Missing substantive HTML, a challenge or an explicit
automated-access restriction stops collection. Current content is reviewed as
current content rather than compared with a frozen whole-page hash. No blanket
content licence or permission to access private applicant systems is claimed.

Both the API and Group origin receive a fresh robots request. An HTTP 400–499 response other than 429 **only at those exact robots URLs**
means an unavailable REP file,
under the user's policy and RFC 9309 section 2.3.1.3. It is not interpreted as
`Disallow`. HTTP 200 robots rules are enforced for YouthOppsBot and applicable
legacy YouthOpps product rules, retaining the stricter restrictions. HTML or a
recognizable challenge is not accepted as a valid robots policy. Visible
challenges inside otherwise normal primary content are also retained as hard
holds before parsing succeeds; ordinary CAPTCHA script/footer mentions are not
classified as access challenges. A content 403,
401, explicit applicable robots prohibition, challenge or TLS verification
failure remains a stop. HTTP 429 stops the run and retains timed rate backoff,
as detailed below. Retry-After and stronger pacing restrictions remain.
There is no alternate origin, identity rotation, browser impersonation or TLS
bypass.

The original hosted failure was a 403 from
`https://group.mercedes-benz.com/robots.txt`, in
[run 38086095380](https://github.com/YouthOpps/data-pipeline/actions/runs/38086095380).
[Run 38086095524](https://github.com/YouthOpps/data-pipeline/actions/runs/38086095524)
then restored the same hold and made no publisher request. Both authenticated
pacing artifacts were downloaded with normal TLS and checked against their
GitHub artifact digests and complete logs. The recovery accepts only these exact
canonical state SHA-256 values:

- `b649b04a88505deef9e072371668796671411de359a099f826b6344e06bd0668`
- `80f02fd04e978032f090e20cfe003593524f854e2adc32de79f503e3a0f79b93`

Only their robots-only blocked flag is migrated; the original request starts,
not-before timestamp and interval are preserved, and recovery provenance is
retained through later merges/artifacts. Unknown or modified blocked states,
including mixed/content/challenge/rate-limit holds, cannot match and remain
blocked. The latest actual family artifact remains mandatory after any family run;
the obsolete first-family bootstrap is not a recovery mechanism. Queued reruns
of older run IDs contribute their exact previous completed attempt. Every
candidate is bound to its exact attempt jobs and immutable head SHA and ranked
by actual job completion,
not a mutable run update timestamp. Missing or ambiguous attempt/job evidence
fails closed instead of restoring an older budget. See
[issue #115](https://github.com/YouthOpps/data-pipeline/issues/115) for the original
hold and the scoped recovery authorization/evidence.

## Runtime and publication

Run `python -B adapters/de-mercedes-benz/test_adapter.py` from the repository, or
copy this folder and run `python -B test_adapter.py`. The sole live test retrieves
and validates the complete nonempty raw collection, emits its actual full JSON on
stdout, receives no publication credential and writes no dataset. Standard library
only. Diagnostics go to stderr. `python -B adapter.py --publish` is reserved for
separately authorized production automation.

The secure publisher-family lock and budget retain at least twelve seconds between
physical requests, including a completion-to-next-start guard, and no more than
five starts per rolling minute. A 60-second startup floor, stronger limits, history
merging and durable refusals remain. Inventory windows, including the transition
between complete traversals, wait at least 30 seconds after the preceding page
finishes; this guard does not promote every detail/policy request to a permanent
longer interval. Two robots, six policy/condition pages and at most 32 windows fit
the unchanged 42-start cap including redirects/retries. There are at most two
attempts for typed transient failures/HTTP 503, with retained 30-second backoff.
Content HTTP 429 stops the current run without creating a permanent access
block. It durably doubles both the physical interval and inventory page gap.
Without a usable Retry-After it saves an embargo of at least 30 minutes, doubled for each
subsequent recorded 429; counters, both gaps and embargo survive local/artifact
merges by MAX. A later bounded run may resume only after that embargo at the
retained slower pacing. Explicit Retry-After and stronger restrictions remain
authoritative. Malformed, nonpositive, nonfinite or unrepresentable 429 timing
is unusable: it uses the exponential fallback without creating or clearing a
hard hold. Invalid backoff on other responses retains protective failure
behavior. Existing unknown/mixed hard holds are never cleared this way.
Responses remain bounded to 8 MiB each and 64 MiB total.

Prepublication remains bounded to 1,800 seconds, publication 240, failure metadata
120 and pacing export 120; the whole process ceiling remains 2,280 seconds. The
45-minute job limit and one-hour source-scoped App token cover these phases;
complete live timing must confirm actual headroom. No cap is raised for recovery. If stronger retained pacing exhausts the fixed
phase, collection fails while preserving last-good data; it never relaxes that
pacing or claims a partial collection succeeded. The current 32-window inventory
needs at least 31 × 60 seconds between pages after a first 429 doubles the page
gap, which cannot fit the unchanged 30-minute collection phase with startup,
policy requests and reserved time. Once the first current total is known, a
lower-bound check fails early when the remaining traversal is provably infeasible.
Timed backoff does not promise that every subsequent attempt can complete.

Publication atomically updates only
`datas/de-mercedes-benz/{data,metadata}.json` with nonforce compare-and-update.
Concurrent newer source pairs are preserved. Matching IDs retain creation and
first-seen clocks; actual descriptor changes advance semantic freshness. A failed
collection, validation or publication preserves the last-good data and success
time, recording failure metadata only when safe. A first failure creates no source
folder. Pacing restoration and trust happen before the snapshot read; later
snapshot/auth failures still reach the finally-protected pacing artifact export,
so a zero-publisher-request failure does not discard the restored history. Tests
and static publication checks do not certify production delivery.

## Expired-record cleanup

Complete raw retrieval must validate as nonempty before filtering. Supported
explicit timestamp deadlines expire strictly after their closing instant. Current
API publication lifetimes and employment start dates are not application deadlines;
unknown dates, status labels, record age and absence are not expiry evidence.
Successful publication uses the complete filtered replacement, so no old expired
records are merged back. A valid all-expired raw collection produces an empty
successful pair; subsequent published-snapshot reads accept that empty result.
Retrieval failure preserves the last-good pair, including a valid empty one.

## Automation and recovery handoff

The source keeps its daily slot **19:59 UTC** (`59 19 * * *`), scoped main-push and
manual triggers, source-family serialization, source-scoped App token and pacing
artifact export. The remote workflow remains disabled until a separate maintainer
accepts the change, merges it and explicitly performs production recovery. No
production Action is invoked as an implementation test. That maintainer must
verify current latest-family artifact restoration, successful source-pair delivery
and the website consumer before claiming production recovery.

Every publisher request uses exactly:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) provides project information
and indexing-removal contact details. Changing this identity never clears holds.

## Source display name

Published metadata uses `display_name` = **Mercedes-Benz** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.
