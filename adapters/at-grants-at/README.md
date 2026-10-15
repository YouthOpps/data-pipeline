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
No destination, eligibility, deadline or application status is inferred.

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

All publisher hosts share one serialized collection and durable budget family,
`youthopps-grants-at-publisher-v1`. Physical request starts are at least 12.2
seconds apart and no more than five starts occur in a rolling minute. The guard
also waits after the previous response completes. Inventory partitions start at
least 30 seconds after the preceding partition completes. Stronger retained or
publisher limits are never reduced. A restored process waits at least 60 seconds
before publisher traffic. Collection has a 15-minute phase budget; the workflow
has a 30-minute timeout and runs once daily at 01:23 UTC.

A 429 ends the current run without an immediate retry. It durably increases both
physical and partition intervals exponentially and applies at least 30 minutes
of cooldown, at least 60 minutes on a second occurrence, or a longer Retry-After.
Only finite positive Retry-After delays or supported timezone-aware future dates
can add an embargo. Malformed, negative, stale, timezone-free and unrepresentable
values leave the exponential minimum and all stronger known embargoes intact.
Cooldown continues increasing beyond the ninth recorded 429. State counters are
validated in the range 0–100; a counter of 100 stops before publisher traffic
for review, preserving the original history instead of resetting it.
Future runs fast fail during that timed embargo and can retry normally at the
retained slower pace after it expires. A 429 alone does not become an indefinite
hard hold. Other known hard refusals remain held for review. Repeated 429
slowdowns can make the complete inventory exceed the unchanged 15-minute phase.
The adapter then fails closed and preserves last-good data; successful completion
at every retained slower rate is not guaranteed.

Local state is locked and written atomically outside the checkout. Production
restores the newest completed actual family attempt's inert Actions artifact,
including failed attempts before the first successful publication. It validates
repository, main branch, attempt, family, bounded ZIP, schema and digest.
Actual exact-attempt job completion determines the newest attempt, including a
prior completed attempt of an older run ID whose next rerun is queued. Mutable
run update timestamps are not used as completion times. Missing,
expired, inconsistent or unavailable latest state fails closed; it is not treated
as a fresh family. Restored state merges all stronger intervals, starts,
completion times, embargoes and refusals. Artifact upload always runs, but only
successfully restored or explicitly reviewed bootstrap state is trusted.
The first production attempt requires a reviewed full native budget seed with a
public approval evidence URL and complete workflow-history proof. It cannot
reset history using an empty/default seed. Artifacts contain budget state only,
not records, credentials or application data.

## Validation and publication

Run the sole complete live, nonpublishing test from data-pipeline:

```sh
python -B adapters/at-grants-at/test_adapter.py
```

It performs a real complete inventory scan, validates all records, requires a
nonempty result and writes the complete JSON array to stdout. Keep that output in
RAM for inspection and the actual website consumer. The test has no publication
credentials and does not write normalized records to disk.

The production command is:

```sh
python -B adapters/at-grants-at/adapter.py --publish
```

Only the matching main-branch workflow may publish with its short-lived
source-scoped GitHub App token. It atomically updates only
`datas/at-grants-at/data.json` and `metadata.json`. A first failure creates no
placeholder pair. Later failures preserve last-good records and success time,
while recording failure metadata. Valid refreshes replace the complete snapshot;
previous records do not linger after publisher removal. Stable creation and
first-seen timestamps survive refreshes.

Explicit expired deadlines are removed before publication, using exact timezone-aware
instants. A date-only deadline with no documented publisher timezone is retained
through that date in the latest civil timezone (UTC−12), avoiding an earlier
unsupported cutoff. Unknown deadlines stay
unknown. A validated nonempty raw collection whose records all expire may publish
an atomic empty array with success metadata. Collection failures never substitute
an empty successful snapshot. The adapter reads both existing files consistently,
including GitHub's blob route for larger snapshots, and uses a compare-and-swap
branch update to avoid overwriting concurrent source publications.

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

Published metadata uses `display_name` = **OeAD** for the visible
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
