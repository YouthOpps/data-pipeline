# IKY financial programme facts

Indexes minimal facts from the Greek State Scholarships Foundation’s
[scholarship catalogue](https://www.iky.gr/ypotrofies/). This is a programme
overview index. It does not claim that a programme currently accepts
applications.

## Access and reuse scope

The [site terms, §3 Άδεια Χρήσης](https://www.iky.gr/oroi-proypotheseis-xrisis-istoselidas-iky/)
reserve use of protected site content, including partial or summarized content,
without prior permission. The Greek clause says prior permission, not explicitly
written permission. Applicability to a bare factual title, basic publication
field and original-link index is unresolved; this adapter does not claim legal
clearance or an open licence. It excludes descriptions, announcements, images,
PDFs and publisher-written summaries. Its English summary is our own short
statement about the catalogue entry.

Seven bounded research requests preceded implementation. The public catalogue
and one literal programme HTML page returned 200. A documented WordPress pages
API request returned **401**; its underlying cause/body was not retained. That
exact refusal is preserved. The API, the entire `/wp-json/` namespace and all
query-based routes including `rest_route` remain prohibited. There is no API
fallback, retry, authentication, alternate host, cache or identity workaround.

A source-local, separately reviewed migration may annotate only the exact
original held snapshot. It retains `blocked=true`, the original full state,
original raw bytes, exact refused URI and scoped-review evidence. It never
clears an unknown or mixed publisher hold. Only the previously advertised
literal financial programme HTML paths, the catalogue and robots route are
eligible. Every physical publisher GET and every redirect target is checked
before transport. New catalogue URLs stop collection for review. Any future
HTML 401/403 or genuine challenge produces a global HTML hold. Ordinary 429
remains a timed slowdown; an actual human-verification challenge additionally
retains the global hold. Unknown/incompatible state remains refused.

The [maintainer’s scoped HTML review](https://github.com/YouthOpps/data-pipeline/issues/40#issuecomment-6195556550)
released one non-publishing native collection after independent design and
runtime review. That complete test passed with 29 genuine programme records,
two identical complete catalogue frontiers, and the actual website consumer
accepting 29 raw, 29 expiry-filtered and zero valid all-expired records.
Thirty-two measured physical starts used the advertised identity, with minimum
12.5336-second spacing and at most five starts per rolling minute. The catalogue
completion-to-closing-read gap was 404.744 seconds. No protected API, query,
asset or unadvertised programme route was requested during that test.

After that live test, only the denial-text reader’s hidden DOM handling was
corrected. All 32 retained actual response bodies preserve the same old/new
guard decisions. Inventory, normalization, pacing and publication are unchanged;
no second publisher crawl was performed. Attribute and inline-CSS checks are not
a full external-stylesheet rendering engine.

The [first-production MAX approval](https://github.com/YouthOpps/data-pipeline/issues/40#issuecomment-6196309193)
binds the complete known budget and original scoped API refusal after a fresh
complete workflow-history review found no earlier IKY family run. The seed
retains all stronger observed timing constraints, measured rolling starts,
original raw refusal evidence and the mandatory fresh 60-second startup quiet.
Earlier policy-only observations remain evidence; missing historical headers
are not invented. Production execution and live website visibility remain
unverified until the maintainer’s merge and resulting Action complete.

## Inventory and factual provenance

The unique `nav.ypot-mn` menu supplies eight financial programme branches.
`Προβολή/Δημοσιότητα` and category/menu placeholders are excluded. Repeated
programme links across funding branches are unioned before retrieval. Each
programme must have a unique native `h1`, native `body.page-id-*` identifier and
matching canonical URL. IDs are `gr-iky-<native-page-id>`.

Only a canonical-bound `WebPage.datePublished` with an explicit timezone is
used as publication time. Modification dates are not publication dates.
Application status, deadlines, host countries and citizenship eligibility stay
unknown unless separately proved; publisher country `GR` is not eligibility.
Native menu branches classify scholarship programmes and financial/award
support. All records have `kind=programme-overview`.

The complete catalogue is read before and after all approved programme pages;
any difference stops publication. Safe incremental coverage is unproved: page
modification time does not establish complete membership or deletions. A known
programme cannot be a pagination stopping rule. No RSS, post archive, PDF or
unapproved endpoint substitutes for the catalogue.

## Pacing, state and publication

The advertised user agent is `YouthOppsBot/1.0 (+https://youthopps.org/bot/)`.
Requests use normal TLS validation. Publisher starts are serialized at least
12.2 seconds apart, with at most five starts per rolling minute. Complete
catalogue reads additionally wait at least 30 seconds after the preceding
inventory completion. Stronger robots, Retry-After, retained interval and
embargo constraints win.

The family remains `youthopps-iky-publisher-v1`, using the same state path and
stable state/collection lock inodes. Normal atomic JSON state replacement may
rotate the JSON inode. No new namespace or fresh-budget reset releases a hold.
429 ends the current run, retains an initial minimum 30-minute cooldown that
grows exponentially, and doubles both retained physical and inventory gaps.
Malformed Retry-After uses that fallback. Later runs fast-fail until the known
embargo ends. Repeated slowdown can exceed the unchanged 15-minute collection
budget; completion is not guaranteed and no partial inventory is published.

Production must restore the newest actual completed family attempt using exact
attempt jobs, including prior attempts of queued older reruns. Missing, expired,
ambiguous or incompatible state fails closed. First production requires a
separate approved full native MAX bootstrap; the exact approved state and
public evidence are configured in the matching workflow.
Trusted state is exported even when collection fails. Source data and metadata
publish atomically to only this source’s pair. Collection failure preserves
last-good records, and a successful refresh removes expired old records.

## Commands

Run without `DATA_SOURCE_TOKEN` for the one complete live, non-publishing test:

```sh
python -B adapters/gr-iky/test_adapter.py
```

The test writes the genuine complete records JSON only to stdout. Keep it in
RAM; do not save a normalized dataset. Importing the test with a publication
token present refuses before collection.

Authorized production uses only:

```sh
python -B adapters/gr-iky/adapter.py --publish
```

The matching workflow uses a staggered daily schedule at **20:11 UTC**, manual
dispatch and source-filtered pushes to main, with serialized family concurrency.
Its application command is only the authorized production command above.
The public source metadata uses `display_name="IKY"` on success and failure;
the source ID remains `gr-iky`. The current website consumer replay accepts all
29 actual native records, filtered/empty cases and last-good failure metadata.
This replay is not another publisher collection. The maintainer owns merge and
first-production verification.

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
