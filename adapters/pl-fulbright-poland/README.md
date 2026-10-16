# Fulbright Poland

Collect the Polish-U.S. Fulbright Commission's current programme inventory from
[programmes to the USA](https://fulbright.edu.pl/stypendia-do-usa/) and
[Americans in Poland](https://fulbright.edu.pl/amerykanie-w-polsce/), including
the linked own-publisher programme pages and named partner awards. The assigned
[Junior Research source](https://fulbright.edu.pl/junior-research/) remains the
record source identity. This is programme/call coverage, not a news archive or a
worldwide Fulbright catalogue.

## Access, licence and attribution

The previously reviewed public [robots policy](https://fulbright.edu.pl/robots.txt)
permitted these routes; `/wp-admin/` remained excluded. This migration makes no
new access claim before its separately approved original live test. Each live collection checks robots and
each redirect before following it. No application portal, authentication,
CAPTCHA, PDF, form submission or restricted route is needed.

The publisher footer states that, unless otherwise marked, website contents
are available under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
Collection checks that the licence statement remains present. Attribute the
Polish-U.S. Fulbright Commission, link the original programme and licence, and
use the output noncommercially under that licence. This adapter supplies factual
metadata and its own short summaries. It does not reproduce full explanatory
articles, images or third-party content. Linked US Scholar partner titles and
programme facts come from the Commission's own inventory; the external partner
pages are original destination links, not an externally crawled dataset or a
claim that their full content shares this licence. No permission request was
submitted. Published source metadata includes `license`, `license_url`, original
Commission attribution, and `changes` identifying factual selection,
normalization and YouthOpps-written summaries, so recipients receive the
licence and modification indication alongside the source records.

## Complete programme inventory and distinctions

The previously reviewed inventory contained 29 programme/call records; this
is historical coverage evidence, not a fixed current record or HTTP count:

- Fourteen own-publisher detail pages (18 records after the five named
  Schuman variants replace their parent overview): Graduate Student, Notre Dame LL.M,
  Junior Research, Stanford Research, Polish Scholar, Polish Studies, STEM
  Impact, Scholar-in-Residence, outgoing Schuman, BioLAB, TopMinds, and the
  institutional ETA, Specialist and Inter-Country calls.
- Five incoming individual programme sections: US Scholar, US Student, English
  Teaching Assistant, Specialist and US-citizen Schuman.
- Six explicitly named US Scholar partner awards at Polish host institutions.
  These are real separately named programmes, not clones of funded places.

The general US Scholar programme remains distinct from its named partner
awards, as published by the Commission. Incoming individual ETA/Specialist
opportunities and Polish host-institution applications have different applicants
and original section/detail links. Institutional ETA, Specialist, Inter-Country and Scholar-in-Residence use
`kind=institutional-grant`; they are not direct student applications. A US
university submits Scholar-in-Residence requests; Polish nominee recruitment
occurs conditionally if the administrator asks the Commission for candidates.
No current individual nomination call is claimed. Incoming undated programmes,
named partner programmes, TopMinds and the five Schuman variants use
`kind=programme-overview`; current dated rounds retain `kind=opportunity`. BioLAB is one
internship programme across four host institutions. Counts of scholarships,
visits and host places never produce additional records.

Discovery uses the two official inventories' actual linked programme paths.
Programme URLs are normalized to the reviewed canonical own detail paths;
`graduate-student-award` navigation aliases do not produce duplicates. The
reviewed Program TopMinds navigation links topminds.pl; its substantive local
Commission explanation is requested through `/topminds/`, which redirects to
the historical canonical `/topminds-2025/`, without external crawling. Its
programme information is retained as an overview; no 2026 recruitment round is
invented.
Each
programme page's current title and conditions are read independently. There is
no programme-list pagination on these inventories. News, previous results,
webinars, alumni stories, fundraising, application systems, language duplicates
and the global US awards search are excluded. Incoming programme and six partner
facts are already substantive on the Commission's page, so no external global
catalogue crawl is performed. Unknown dates do not remove real programmes.

As researched on 8 October 2026, six calls are open, four are closed, and nineteen
have unresolved yearless/undated calendars. These are observations, not stored
records or fixed expected status counts. All programme data is retrieved live.
The public outgoing overview still says STEM closes 31 October, while the actual
2027–28 STEM programme page explicitly says **30 October 2026 at 15:00 Polish
time**. The current detailed programme notice is authoritative; the adapter does
not copy the stale overview date. Graduate/LL.M/Polish Scholar remain genuine
2027–28 expired calls; planned 2028–29 recruitment is not invented as a new call.

## Dates, eligibility and identity

Explicit Polish closing day/month/year is preserved as `YYYY-MM-DD` when no
clock is given, such as the institutional Specialist deadline, 8 January 2027.
A stated clock with “Polish time” uses `Europe/Warsaw` for that actual date:
13 October 2026 at 15:00 becomes 13:00 UTC, while 30 October at 15:00 becomes
14:00 UTC after the seasonal offset changes. BioLAB's 1 February 2027 at 15:00
also becomes 14:00 UTC. No midnight or end-of-day cutoff is invented. Complete
current notices take precedence over older FAQ/calendar text. Date-only records
are unresolved on the closing day; explicit closed notices remain expired.

Yearless annual windows in Schuman and Inter-Country and the yearless TopMinds
October deadline retain null deadlines and unknown status. A linked historical
edition is not treated as a dated current call. Scholar-in-Residence has real
programme/eligibility facts but delegates dates to its administrator; its dated
status remains unknown.

Polish or US citizenship requirements are separated from physical host country,
institutional affiliation, residence and work/visa authorization. Polish
citizenship is required where the detail actually says so; dual Polish-US
citizenship and US permanent residence exclusions are preserved in the summary.
BioLAB explicitly does not require Polish citizenship, but excludes US citizens
and US permanent residents. Applicants must maintain active Polish
master's/doctoral student status; non-Polish applicants must additionally have
completed a bachelor's degree in Poland. Institutional host calls
do not gain a citizenship restriction. Four named outgoing Schuman variants require EU citizenship, represented by
the 27 EU country codes. The separate NATO Security Studies variant requires
NATO citizenship and a PhD; US citizenship/dual-US citizenship and US permanent
residency are excluded. Its eligibility therefore lists 31 NATO countries
without the US. Each actual named variant has its original heading anchor.
Innovation explicitly describes research in one or two EU states, so no
specific host-country list is invented; US-citizen Schuman research in
the EU has no specific host-country list, so none is invented. TopMinds' Polish
institution/student description does not establish a citizenship restriction.

Stable identifiers use the first 24 SHA-256 hexadecimal characters of
`pl-fulbright-poland|<programme URL>`. US Scholar programme facts preserve PhD/terminal degree and 3–10 months. US
Student requires a bachelor's degree before the grant, before PhD, and a host
invitation letter; ETA requires a bachelor's, before PhD, and explicitly needs
no invitation letter.

Incoming sections use stable original
publisher anchors; partner awards retain their original individually linked
URLs. Award capacity, order, deadlines and academic-cycle title changes do not
change identity. Source URLs in classification evidence provide provenance.

## Test, schedule and publication

```sh
python -B adapters/pl-fulbright-poland/test_adapter.py
python -B adapters/pl-fulbright-poland/adapter.py --publish
```

The only test performs real non-publishing collection, validates all records,
and prints the complete JSON list to stdout; diagnostics go to stderr. It uses
no mocks, fixtures, output files or publication credentials. The matching
workflow publishes on its own adapter/workflow changes merged to `main`, manual
dispatch, and once daily at 21:30 UTC, with repository/main guards and a source
App token. Its sole application command executes this adapter with `--publish`.
Production Actions are not implementation tests.

Publisher robots, pages, redirects and retries share at least 12.2 seconds
from response completion to the next physical start and at most five starts
per rolling minute. Each physical exchange of the two inventory roots also
waits 30 seconds after the previous inventory exchange completes; programme
details remain on the physical floor. Stricter applicable REP and retained
`Retry-After` constraints dominate. A 429 increases both CURRENT gaps, retains
an exponential cooldown of at least 30 minutes, and stops the collection;
its state is saved before the bounded response body is read. A genuine access
refusal or human-verification challenge separately retains an access hold.
Malformed timing alone does not invent a hold.

An owned pending transaction spans every physical request and all body,
licence and REP assessment, including cached target-policy decisions. Failed
assessment or persistence remains unresolved across process restarts; older
owned state cannot clear it. Original normal typed pre-response connection
retries remain bounded to two attempts. Response/body, TLS, storage and policy
failures are not healthy retries. The original default GitHub transport keeps
its six-second starts, ten-start rolling allowance and Retry-After behavior;
it does not share the publisher's new physical interval.

LOCAL collection requires the exact workflow manually paused before state
initialization and again after startup quiet, before its first publisher wire.
Both LOCAL and hosted modes read complete exact-attempt history and restore
the newest source/head/code/state-bound owned artifact; missing mature state
fails closed. The separately approved finite legacy transition retains unknown
historical access and 429 ancestry. An absent old namespace is not zero history
or a fresh allowance. The modern publisher namespace is separate from the
preserved default GitHub host files. The LOCAL migration literal is bound to the separately reviewed
[initial boundary](https://github.com/YouthOpps/data-pipeline/issues/293#issuecomment-6207427595).
The hosted marker is separately bound to the later
[full native MAX approval](https://github.com/YouthOpps/data-pipeline/issues/293#issuecomment-6207729527).
Neither literal activates a workflow or establishes a hosted publication result.

Success atomically replaces only
`datas/pl-fulbright-poland/{data,metadata}.json`. Later failures preserve last-good
records and the success timestamp, recording failure metadata when safe.
First-run failure creates no published source folder. Failure to persist status
is explicitly reported. The source README is local; root README is unchanged.

## Expired-record cleanup

A refresh first retrieves and validates the complete nonempty publisher inventory.
Only then are records with a verified past `deadline` removed from the next
source snapshot, including expired identities from the previous publication.
The existing full replacement does not merge old records back into the result.
Date-only deadlines remain eligible through their full closing UTC calendar
date; no publisher end-of-day clock or timezone is invented.
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
access. Each current identity selects its explicit policy or wildcard fallback;
an explicit legacy group adds its restrictions without displacing that current
policy. Applicable finite decimal delays and request rates remain effective;
unsupported selected numeric policy leaves assessment unresolved. Unrelated
bots' malformed timing is not applied. Publisher pacing and durable state are
updated as described above; source identity and default GitHub behavior remain
preserved. Existing disabled workflows remain disabled.

## Daily automation

The scheduled refresh runs once daily at **21:30 UTC** (`30 21 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The two inventory roots have no supported continuation pages, but both are
listing requests and receive the separate inventory-completion guard. Current
root navigation must still account for every supported programme path; a new
programme route requires coverage review rather than silent truncation. No
complete publisher delta/removal signal is established, so a complete supported
refresh remains necessary. The daily schedule itself is unchanged.

## Workflow concurrency

`youthopps-pl-fulbright-poland-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.

## Source display name

Published metadata uses `display_name` = **Fulbright Poland** for the visible
institution label. Existing source identifiers, legacy names and attribution
notices remain unchanged. Both success and failure metadata emit this label;
older snapshots without it remain valid.

## Bounded publication error categories

GitHub publication failures retain the known route, HTTP status and bounded
attempt count. A bounded JSON error view emits only fixed known message/code
labels; arbitrary bodies, headers, credentials, URLs and query values are never
copied into diagnostics. The non-fast-forward label requires the exact known
GitHub message on an HTTP 422 PATCH of the main reference; HTTP 422 alone does
not establish a conflict. Malformed, ambiguous or unknown errors remain
unclassified. These diagnostics do not prove the cause of historical failures.

Collection, source fields, access holds, pacing, the existing three publication
attempts, source-pair checks and non-forced atomic writes are unchanged by those
diagnostic categories. Those categories alone add no retry delay, workflow
activation or publication test.

## Bounded publication-conflict retries

Hosted publication proves the exact current job, run attempt, source workflow,
matching head/workflow SHA, main branch, assigned runner and UTC start before
data-source access. A fresh GitHub Date maps the existing 30-minute job bound
to one conservative monotonic deadline; retries cannot renew it. The pinned
App token is minted after job start and expires after one hour, so no token
renewal is needed. Missing or inconsistent proof fails closed.

Every guarded publication call and the current-job proof call on the reviewed
Ubuntu/Python path has a total alarm guard of at most 30 seconds, plus a
remaining-time socket timeout, including its bounded body read. Existing or
unsupported timers stop before transport. Writes and retry waits stop 60
seconds before the job deadline, final outcome reads stop 30 seconds before
it, leaving a cleanup/export allowance. These publication reserves do not
guarantee export after an overlong collection, runner death or an
uninterruptible native call; the existing job timeout remains the outer bound.
No API call receives a fresh deadline.

Only an actual, classified non-fast-forward reference response can retry,
after checking that the desired outcome has not landed and this source pair is
still unchanged. The first two retries add 1–2 and 2–4 seconds after the
ordinary GitHub pacing gate, before reserving the actual request start, within
the existing three attempts and original write deadline. Authentication,
non-CAS and unclassified failures stop without another write attempt. Terminal
publication errors latch a per-process write stop, including later
failure-metadata fallback; bounded read-only outcome checks remain available.
Source-pair changes stay protected; reference writes keep force:false. Those
publication-conflict safeguards alone do not change source collection,
fields, pacing, schedule or the 30-minute job cap. The pacing migration below
has its own separately described scope.
Validation uses inert external controls; no source fetch, GitHub test write or
production invocation is claimed.


## Migration boundaries and finite admission

The original workflow job remains 30 minutes. The prior adapter had no whole
source collection timer; this migration introduces stricter 1,200-second source
IO/wait admission and 1,740-second whole admission, frozen once before history
restoration and the data-source snapshot. Publication and failure reporting each
have a bounded 180-second owner, with 60 seconds for export and 120 seconds of
coordination margin. Actual current-job proof may only tighten these bounds.
These are admission limits and bounded calls, not a claim of hard total parser
wall time or a guarantee that every possible redirect/retry/REP delay fits.
No inventory is truncated or cap raised to force success.

The original publication guard retains its own bounded HTTP alarms. No outer
permanent SIGALRM competes with it. The failure owner begins before any fallback
snapshot, and original atomic three-attempt CAS checks, token identity/window,
last-good preservation and safe failure metadata remain in force. A transient
snapshot failure still exports trusted publisher state. Attempt-owned uploads
contain only inert pacing state, never records, responses or credentials.

Only genuine new namespaces may initialize the explicitly reviewed legacy MAX.
Original/current locked descriptor identity is checked around IO and before
publisher access. Cleanup does not close a reused foreign descriptor or mutate
a stronger replacement generation. Artifact output is safely invalidated before
fallible signal/unlink cleanup; close failures are sticky and preserve a primary
exception. Private controls remain outside the delivered tree. No source
requests, original native test or production Action are part of offline work.

The one original native test at `7eef97a4` passed and emitted all 29 records
from 18 genuine physical responses. Its external observer then failed because
it passed a string to the unchanged datetime expiry API; that failure receipt
is preserved. A separately authorized offline supplement used the correct
datetime contract on all actual retained inputs, reproducing all 29 records
and their fields, with 23 current records, with the actual six expired records yielding an empty subset.
No source request or native test was repeated.

The actual Specialist notice states 8 January 2027 without a clock or timezone;
ETA likewise supplies only 31 January 2026. The original Warsaw date-only
helpers could remove a record during the closing UTC day. The
[reviewed pure correction](https://github.com/YouthOpps/data-pipeline/issues/293#issuecomment-6207613896)
uses UTC calendar status and expiry for date-only values. All eight explicitly
supported zoned instants, 19 null dates, normalized fields and full graph stay
unchanged. The correction is an offline projection of the complete actual
retained inputs, not a second live native claim.


The reviewed production state preserves all 100 known timing observations,
including 28 historical job-completion upper bounds and actual reservation,
opener, wire and response-completion observations from the sole native. These
are not 100 physical requests: the native made 18. All 104 actual save snapshots
and 34 assessed pending transactions were reviewed; historical access and 429
uncertainty remain explicit. Pending false follows those successful settlement
chains rather than blindly discarding historical pending states. The stronger
receipt/final-observation quiet bound is retained. Production and website
verification remain separate maintainer steps; no author dispatch is made.
