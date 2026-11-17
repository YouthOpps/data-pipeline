# Swedish public-agency trainee and apprenticeship advertisements

This standalone standard-library adapter indexes a defined Sweden-only subset
of Arbetsförmedlingen JobSearch. It collects the complete union of the documented
`trainee=true` and `larling=true` searches with the publisher's observed Sweden
country concept `i46j_HmG_v64`. These API filters indicate phrase-match likelihood,
not verified eligibility. Every candidate is checked for an explicit trainee role/programme or present
applicant/recruitment route in the headline or body, and an actual Swedish
workplace. Compound Swedish trainee titles, named graduate programmes and
explicit conditional apprentice applications are supported without title or
employer whitelists. A role may require prior experience or professional
licensing; it is never advertised here as universally entry-level.
Teacher, coordinator, mentor and other advertisements that merely discuss
apprenticeships are excluded. No age, citizenship, salary or universal
no-experience eligibility is inferred.

The initial source excludes generic student search. Actual research found that
`Student Assistant` can mean experienced school pupil-support staff rather than
a vacancy for a current student. It does not claim all Swedish youth vacancies,
all agency advertisements, or worldwide coverage for any employer. Corporate
career pages, application forms, images and recruiter contacts are not fetched.
The agency experience flag is retained as a source field, not a universal
eligibility guarantee; actual graduate/apprentice advertisements can carry a
true flag. The actual agency employer is preserved, including staffing companies; a client
mentioned in a description does not replace the employing entity.

## Official access and licence evidence

The [official API root](https://jobsearch.api.jobtechdev.se/) advertised
[its Swagger specification](https://jobsearch.api.jobtechdev.se/swagger.json).
The actual version 1.37.0 metadata states **Ads are licensed under CC0**, linking
to [CC0](https://creativecommons.org/publicdomain/zero/1.0/deed.sv). This labels
the advertisement data, rather than merely licensing API software. The official
[dataset](https://data.arbetsformedlingen.se/dataset/job-ads/) and
[service](https://data.arbetsformedlingen.se/dataservice/jobsearch/) were also
found through primary indexed evidence. The documentation host's controlled
robots request returned an upstream/proxy 503; that host remains unreachable,
not an allow-all policy. The separate API host's literal robots request returned
404, classified under the selected RFC 9309 unavailable-REP policy for that host
only. A genuine content refusal or human-verification challenge still stops
access; CC0 does not waive access restrictions or rate limits.

This is independently licensed public-agency data. No restricted Siemens Energy
portal data is imported; its separate research history and terms hold remain
untouched. Public factual output contains original titles, agency links,
employer/location, supported dates and independently written short summaries.
Full licensed descriptions, logos and contact details are not republished.
Historical accounts of former apprentices or a company merely employing
apprentices do not establish a current applicant route. Explicit negative
apprentice/trainee recruitment and support-staff contexts remain excluded.

The exact identity is `YouthOppsBot/1.0 (+https://youthopps.org/bot/)` for every
physical request, including robots, redirects and retries. Applicable protective
legacy product rules and stronger publisher limits are retained.

## Complete inventory and facts

The API's `resdet=full` search hit inherits the complete `JobAd` model, so an
extra company or agency detail request is unnecessary for those same facts.
Each query uses the documented maximum 100 records per page, every offset
required by its observed total, and requested `sort=id`. No numeric ordering,
first-known-ID stop, publication-age expiry or fixed current count is assumed.
Malformed envelopes, overlapping pages, missing results, conflicting duplicate
advertisements and a changed closing scan fail without publishing partial data.
The complete candidate identities and facts must match a second full scan.

The documented maximum offset is 2,000. A query that cannot be retrieved
completely within that public API window fails with an actionable error rather
than truncating; a future reviewed partition strategy would be required. No
verified older-update/removal cursor exists for this filtered scope, so each
run performs full reconciliation instead of a publication-date-only delta.

Stable IDs are `se-arbetsformedlingen-jobsearch-<agency-id>`, independently bound
to the agency's supplied `https://arbetsformedlingen.se/platsbanken/annonser/`
link. The native agency identity is used, never an employer's mutable title or
record order. Per-record source documents are the actual full-detail API page
URLs that supplied the advertisement.

Explicit timezone-bearing dates retain their actual instant in UTC. An API
calendar date, or the calendar portion of its timezone-unspecified timestamp,
remains a date-only value; no Swedish timezone or UTC instant is invented.
Unknown-zone deadlines remain open through that date's end in UTC-12/AoE.
An explicit removed flag or a supported passed deadline excludes the record
from the next successful atomic replacement. Empty raw collection is an error;
a valid nonempty raw collection may filter to a successful empty publication.
Failed fetch, parse, validation or publication preserves last-good data and its
success timestamp. Creation/first-seen dates survive refreshes; updated dates
change only when material facts change.

## Retained pacing and execution

[Issue #381](https://github.com/YouthOpps/data-pipeline/issues/381) records the
actual CC0/API evidence, scoped research and selection decisions. Bootstrap is
only for this previously unpublished agency source and preserves all seven
known research reservations plus the latest original native pacing MAX,
completion/page floors, actual API robots bytes, MAX cooldown/strikes and explicit
unknown older history. The native pacing digest/evidence binds the exact
stronger handoff; it is independent of semantic record acceptance. Its source-local
research journal digest binds that handoff. This is not a reset of an
established source or a claim that unknown history was empty.

The source family is `youthopps-se-arbetsformedlingen-jobsearch-v1`. A locked,
owner-only durable journal survives local restarts. Hosted attempts restore the
newest exact owned state with authenticated workflow/head/attempt/job ordering
and archive digest checks. Missing newer state fails closed. A same-code,
reviewed-workflow failed first attempt below 58 seconds can be proven to precede the
mandatory 60-second monotonic first-wire floor; it cannot create a fresh
allowance. Reruns with missing owned state are not skipped. Stronger holds, cooldowns, pending transactions and unknown provenance
survive restoration. Coordination is checked before initialization and again
before the first physical request after quiet time.

Physical attempts wait at least 12.2 seconds after prior completion, at most
five starts occur per rolling minute, and every inventory request waits at
least 30 seconds after prior inventory completion. Redirects and retries retain
both applicable budgets. A received 429 doubles current physical/page intervals
and durably records its strike and MAX Retry-After/exponential cooldown of at
least 1,800 seconds before body reading, then stops collection. Unassessed or
persistence failures retain a barrier and cannot export stale healthy state.

Run the sole actual nonpublishing test with
`python -B adapters/se-arbetsformedlingen-jobsearch/test_adapter.py`.
It refuses `DATA_SOURCE_TOKEN` and emits the complete nonempty raw records as
one JSON document on stdout, diagnostics on stderr. The workflow must be absent
or explicitly paused for local collection; do not run overlapping collectors.
The private access journal is necessary retained state, not a dataset output.

The matching Action runs on relevant main-branch pushes, manual dispatch and
daily at **20:41 UTC**, with serialized source publication. Its application
command is only `python -B adapters/se-arbetsformedlingen-jobsearch/adapter.py
--publish`. The 50-minute run/45-minute collection ceilings and 55-minute job
limit reserve headroom for publication/state export and the one-hour scoped app
token. The earliest fixed phase/run alarm interrupts blocking opener, body reads
and response closure. A bounded cancellation grace interrupts a stalled close;
it never extends collection. Expiry retains the pending barrier, latches access
closed and invalidates stale export, including after restart. An oversized or
slower full inventory fails safely, without truncation.

Publication atomically updates only this source's `data.json` and
`metadata.json` pair in `data-source/datas/se-arbetsformedlingen-jobsearch/`,
with bounded conflict handling that preserves other sources and never overwrites
a newer same-source snapshot. First-run failure creates no published folder.
Native and consumer acceptance are separate from production/index verification.

The second original native test collected both complete queries and their closing
scans: 36 candidates yielded 30 supported opportunities and six support/history
exclusions. Independent replay and the current website consumer accepted all
30 exact identities; derived valid-empty publication accepted zero. The final
bootstrap preserves all 17 known reservations and generation 66, including
research and both native executions. Production publication and live indexing
remain separate verification gates.

Unavailable-REP observations retain the actual current response digest, literal
robots target, status, purpose and observation time. Only older explicitly typed
healthy unavailable observations for that exact target/status are compacted on
a fresh read. Legacy raw observations, protective 200 rules, held/challenge
evidence and all pacing MAX constraints remain intact. This bounds normal
tracking-ID-only 404 growth without substituting an old digest for fresh policy
evidence. Conservative state merges do not discard observations before a fresh
read; duplicate Retry-After headers retain their strongest deadline.
