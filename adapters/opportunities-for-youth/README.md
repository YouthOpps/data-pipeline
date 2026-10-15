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

The sole test retrieves real nonempty records, validates every record and prints
one complete JSON array to stdout. Diagnostics go to stderr. Tests do not
publish, require credentials or create data output files. The folder can be
copied and run independently with Python 3.12 or later.

Publisher API requests start at least 12 seconds apart, with a shared process
budget and collection lock coordinating local overlapping runs. All source
endpoints, robots requests, retries and redirects obey at most ten starts per
rolling minute and at least six-second spacing; stricter robots directives and
Retry-After are honored. HTTP 429/503 retries add at least two minutes of
backoff and are bounded. The initial six-second traversal received HTTP 429;
the stricter API interval reflects that observed publisher limit. Exhaustive
collection normally takes at least eleven minutes.

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
six-hour schedule, guarded to the owning repository/main and serialized with
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
access; restrictive rate and delay directives remain effective. Native
pacing, backoff and durable budget identities remain unchanged. Existing
disabled workflows remain disabled.

## Workflow concurrency

`youthopps-opportunities-for-youth-publisher-v1` serializes runs of this source.
Up to 100 pending runs are retained (`queue: max`); active runs are not
cancelled (`cancel-in-progress: false`). Unrelated publisher families may
collect and publish in parallel. Existing source-only atomic publication rebases
on unrelated repository updates, uses up to three publication attempts, and
never force-overwrites newer data.
