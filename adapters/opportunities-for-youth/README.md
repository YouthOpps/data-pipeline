# Opportunities for Youth adapter

This standalone standard-library adapter reads the complete public post archive
from the publisher's advertised WordPress API, not only its latest ten RSS items.

## Access and reuse evidence

Research on 14 October 2026 found the publisher homepage advertising both its
[WordPress API](https://opportunitiesforyouth.org/wp-json/) and
[RSS feed](https://opportunitiesforyouth.org/feed/). Its
[robots policy](https://opportunitiesforyouth.org/robots.txt) permits public API
and article paths; it disallows administrative and one plugin-data path, which
this adapter does not request. The linked
[privacy policy](https://opportunitiesforyouth.org/privacy-policy/) describes
personal-data practices and provides no separate content reuse licence. This
adapter publishes titles, publisher article links, taxonomy and timestamps with
Opportunities for Youth attribution; it does not reproduce article bodies or
claim a licence to reuse full articles.

## Inventory and exclusions

If API content is empty, the adapter checks that identified canonical article
body, excluding menus and sharing widgets. Articles still lacking genuine body
evidence are excluded explicitly. The real [Branco Weiss article](https://opportunitiesforyouth.org/2026/01/09/branco-weiss-fellowship-eth-zurich-postdoctoral-fellowship-for-innovative-research-2026/)
(post 71002) was empty in both API and canonical article during research; its title alone
is not fabricated into an opportunity. Nonempty API posts require no separate
article requests.

The categories endpoint is paginated until its declared total is reached.
The posts endpoint requests 100 posts per page ordered by ascending publisher
post ID. Every page, total, unique ID and final archive count is checked. Detected changes to declared totals, duplicate IDs or incomplete pages fail
collection rather than publishing a partial archive. The API is not a
transactional content snapshot: edits preserving these counts can occur during
collection.
There is no arbitrary page, date or record cap. The official API reported 5,357
public posts and 28 categories during initial research. RSS pagination also
works, but its default ten-item window is not the supported inventory.

The adapter retains individual calls with application, eligibility, registration,
enrolment, sign-up, nomination, RSVP or submission evidence in genuine publisher content. An
external application anchor is not required: the publisher article itself is
provenance for a materially documented call. Explicit editorial guidance without
an actual call, material multi-call directories/roundups and articles without
participation or genuine body evidence are excluded with diagnostics.

Roundup evidence combines actual collection framing with distinct programme
references or named entries. Separate named calls with independent closing
labels and disjoint references, and early linked calls to distinct publisher
articles, also establish material collections. Repeated links, different tracks
of one common application, numbered benefits and quantities of awards or slots
are not sufficient alone. Conferences and unmapped genuine calls use `other`.
Degree-level tags require funding evidence before classification as scholarships.
Publisher taxonomy is classification evidence, not geographic eligibility:
continent tags never become host or eligible country codes. Canonical fallback
also excludes the publisher's appended newsletter and sharing widgets.

IDs use `opportunities-for-youth-<immutable WordPress post ID>`, surviving
changes to article URLs and titles and matching the website's kebab-ID contract.
Fresh collections reject legacy colon IDs. Existing published snapshots may
contain `opportunities-for-youth:<post ID>`; only snapshot validation accepts
that legacy format. Successful full refresh migrates this source atomically to
hyphen IDs while retaining matching records' original creation/first-seen times.
Failures preserve the unchanged legacy snapshot and last success timestamp.
Duplicate colon/hyphen representations of the same native post are rejected
when reading a snapshot. No data-source files are edited manually.
`published_at` uses the explicitly GMT API date.
Exact publisher-labelled calendar deadline text is retained in summaries when
recognized, without inventing a closing instant. Deadline, location and country
fields remain null or empty because this adapter does not infer them from publisher category geography, titles or unzoned dates.
Consequently archived calls can have unknown status; collection does not claim
that every retained historical call remains open. Article bodies are read only
to establish opportunity and classification evidence and are not published.

## Run and test

```sh
python -B adapters/opportunities-for-youth/adapter.py
python -B adapters/opportunities-for-youth/test_adapter.py
```

The sole live test is unchanged: when source access is permitted, it retrieves
real nonempty records, validates every record and prints one complete JSON array
to stdout. With the current unresolved hold, it fails before publisher access;
it has not been run for this safety-only change. Diagnostics go to stderr. Tests do not
publish, require credentials or create data output files. The folder can be
copied and run independently with Python 3.12 or later.

### Held safety state

Publisher access is **held**. The latest executed hosted job was
[38720084089/1](https://github.com/YouthOpps/data-pipeline/actions/runs/38720084089/attempts/1),
completed on 15 October 2026 at 12:24:38 UTC with HTTP 403. Its log does not
identify the request URL or purpose; robots-only and content refusal cannot be
distinguished. The higher-ID
[successful job 38721259441/1](https://github.com/YouthOpps/data-pipeline/actions/runs/38721259441/attempts/1)
completed earlier, at 10:59:23 UTC. Both used the same declared bot identity
and identical source bytes. This change does not reinterpret or clear that hold.
The workflow remains administratively disabled; code changes never enable it.

The original private publisher journal and collection-lock paths are preserved.
A discovered 30-second physical interval remains the minimum, with stronger
local/artifact values conserved. A private lifecycle lock covers restoration,
collection and final state export. Starts, response/page completion timestamps,
physical and page intervals, embargoes and rate-limit counters merge by MAX or
set union; actual holds and the explicit historical-unknown marker merge by OR.
Response completion also guards the next physical start, even after a request
falls outside the rolling-minute window. At most five starts fit a rolling
minute, although the retained 30-second floor is stricter.

HTTP 429 stops the attempt without retrying. Before stopping it durably doubles
the current physical interval and inventory-page gap (the latter at least
60 seconds on the first rate limit), and retains at least 30 minutes of
exponentially increasing cooldown. Finite, positive, representable numeric or
zoned HTTP-date Retry-After values extend stronger embargoes. Unusable timing
supplies no timing evidence and never manufactures a permanent hold. The
429 timer and increased gaps are saved before a bounded response-body read;
a visible human-verification challenge also retains a hard hold. An unreadable
429 body leaves the timer saved and stops without retrying.
Actual content 401/403, primary visible access challenges and robots Disallow
remain held. Clear HTML challenges are checked before following redirects;
scripts, hidden content, navigation, footers and ordinary CAPTCHA decoration
do not establish a refusal. Robots-purpose classification requires both the
original request and current redirect target to remain `/robots.txt`; a
redirected content refusal is not reclassified as unavailable robots. Verified
robots-only 400–499 responses other than 429 mean unavailable policy, without
clearing the unrelated historical hold. The existing bounded
503 retry retains its separate 120-second fallback; it cannot retry a 429.

The first hosted transition validates all seven pinned historical attempts,
exact immutable heads, conclusions, completed jobs and absence of artifacts.
It seeds a **blocked** state retaining the known 30-second interval, original
local start/embargo and conservative historical job-completion upper bounds.
These upper bounds are not reconstructed physical starts. Historical unlogged
headers and recovered rate-limit counters remain unknown; the zero counter
means only no new observed 429 since this safety transition. There is no
unblocked bootstrap or source-access approval. A separate reviewed change with
new evidence is required to resolve the historical hold.

Every later executed attempt requires the newest trusted owned pacing artifact,
selected by exact-attempt job completion rather than run ID. Queued older-ID
reruns retain their prior completed attempt with the same immutable head.
Missing, mismatched, ambiguous or expired newest state fails closed; older
artifacts and the first transition cannot substitute. Restoration precedes
snapshot/authentication work and trusted state is exported in finally while the
same lifecycle lock is held, including on snapshot failures. New local journals
also seed the held state; mature missing journals fail closed.

### Validation and budget limits

This is held error-path maintenance: no new publisher requests or live test
were run. Prior complete native success is retained as historical evidence,
not proof of current accessibility or compliance of a new normal crawl.
The last successful job covered 5,366 posts over 54 post pages and retained
5,266 records in 17 minutes 22 seconds including publication. Seven complete
historical logs and exact job metadata were inspected; none exported pacing
artifacts or actual request/header ledgers.

The normal inventory page floor remains 12 seconds; this change does **not**
claim the complete normal 12-second physical/five-per-minute/30-second-page
migration fits existing caps. One robots request, one category page, 54 post
pages and one canonical empty-body fallback nominally require 57 physical
requests. At the known 30-second interval, 56 gaps alone consume 28 minutes;
startup quiet, response times and publication require additional headroom.
Blindly raising every normal page gap to 30 seconds also projects beyond the
existing 30-minute job. No job/token cap is raised, archive truncated, known
restriction lowered or identity rotated. A separately evidenced complete,
budget-feasible design and resolution of the later 403 are prerequisites to
resuming source access. Existing complete traversal and factual fields remain
unchanged. Safe incremental refresh cannot be assumed from a known post ID:
older edits, removals and full reconciliation still require proven coverage.

## Expiry cleanup

The current collector leaves deadlines unknown; unzoned calendar labels remain
summary evidence and are not invented closing instants. Unknown deadlines stay
retained; age, status or absence never establishes expiry. Cleanup accepts only
supported validated deadlines with an explicit timezone and compares their
closing instant with the current UTC time. Complete, nonempty raw collection is
validated before filtering. A successful refresh atomically replaces this
source's entire data/metadata pair, so expired old records cannot persist in the
next snapshot. If every validated collected record has expired, the same atomic
refresh publishes an empty dataset with success metadata. Subsequent snapshot
reads accept that empty result; an empty raw retrieval remains a failure.
Fetch, parse, validation or publication failure preserves last-good data and its
success timestamp, including unchanged legacy IDs until migration succeeds.

## Publication

The matching workflow runs on scoped pushes to main, manual dispatch and a
daily schedule, guarded to the owning repository/main and serialized with
other runs of this source. Its sole application command runs this adapter
with `--publish` using the source publication token. Publication updates only
`datas/opportunities-for-youth/{data,metadata}.json` atomically through a Git
commit with non-force, conflict-safe reference updates. Collection failure
before first success creates no source folder. Later failures preserve last-good
data and the last success timestamp, recording sanitized failure metadata when
possible. Tests and implementation validation do not invoke production Actions
or certify production publication.

Existing publications are read at the captured immutable commit. GitHub Contents
responses that omit large inline content (`encoding: none`) are resolved through
the declared immutable Git blob SHA. Both inline and blob reads verify the
source path, file type, declared size, strict base64, Git blob SHA-1 identity and
UTF-8 before accepting a snapshot. Decoded source files are bounded to 20 MiB;
only GET requests for immutable blob SHAs in the owning data-source repository
receive a 32 MiB encoded-response allowance. Publisher responses retain the
5 MB limit. Outgoing data and metadata obey the same 20 MiB UTF-8 byte limit
before any remote write, preventing unreadable publications. An oversized
refresh preserves last-good data and records failure metadata when possible;
an oversized first publication creates no source folder. Invalid, truncated or
oversized snapshots fail without overwriting last-good data. The original source-scoped atomic commit, conflict detection
and failure-metadata behavior remain in place.

## Bot identity

This adapter sends the following identifying HTTP header:

```http
User-Agent: YouthOppsBot/1.0 (+https://youthopps.org/bot/)
```

The [bot information page](https://youthopps.org/bot/) explains the project
and provides contact and indexing-removal information. This identity does not
grant source access or clear retained refusals. Robots checks use the advertised
product token. Applicable current and legacy product groups must both permit
access; restrictive rate and delay directives remain effective. The original durable budget identity is retained; the held safety state above
adds durable adaptive rate-limit handling. Existing
disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **15:13 UTC** (`13 15 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

### Pagination spacing

Inventory page continuations and partitions retain the existing 12-second floor
after the previous page completes, with stronger durable page intervals and
completion timestamps surviving restarts. After 429 the gap is at least
60 seconds and increases on later throttles. Publisher access remains held.
This includes taxonomy/listing pages where applicable. The existing native
transport still enforces its physical-request allowance across retries, redirects
and detail requests, with stricter publisher limits and retained backoff taking
precedence. The page guard does not raise the durable request interval for all
subsequent article/detail requests or reset existing budget/refusal state.
Inventory scope, parsing, validation and atomic publication are unchanged.

## Workflow concurrency

`youthopps-opportunities-for-youth-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Opportunities for Youth** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.


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


## Retained journal generation — issue #295

Journal access now retains the acquired read/write descriptor, parent identity,
original named inode and full acknowledged byte generation across reservations,
completion writes, pre-opener checks and owned export. A changed, missing,
read-only or stronger current journal stops this process without replacing its
bytes. Only a complete fsynced own write and exact readback advance the
acknowledged generation. Short writes are completed; failed writes may leave
invalid partial state and sacrifice availability rather than grant fresh access.
Closure never intentionally closes a reused foreign descriptor; an existing
body error remains primary, while a healthy closure failure is fatal. Fatal
journal uncertainty invalidates only retained owned output descriptors and
readiness. Owned output uses immediate identity-checked writes with no Python
buffer; foreign descriptor reuse cannot flush buffered text during cleanup.
These checks are advisory observations, not filesystem CAS or
protection against arbitrary same-user code racing the process.

Original state schemas, path formulas, collection locks, maturity/recovery seeds,
retained refusals, pacing floors and caps remain unchanged. The private controls
exercise the actual journal, transport, export and lifecycle functions with
network disabled. Earlier complete native evidence retains its historical
qualification above; original raw records are unavailable, and no new native
collection, full-record replay, publisher request or production recovery is
claimed. This IO-only maintenance does not implement the separate durable
pending protocol, release any hold or authorize a workflow run.

The original publisher journal leaf and distinct service budgets remain directly
under the shared temporary parent; the private source directory still contains
the maturity marker. The guard preserves these actual path formulas and the
publisher's 16 KiB read bound. Service journals retain their original unbounded
read contract and do not inherit a private-directory permission assumption.

Owned artifact/readiness descriptors remain retained through the outer run's
publication and service IO, after the inner collection lock is released. A later
fatal journal error can therefore still invalidate that run's owned output.
