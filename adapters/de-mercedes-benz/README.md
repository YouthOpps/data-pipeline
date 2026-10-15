# Mercedes-Benz public youth vacancies

Collects the official public job-search API linked by
https://jobs.mercedes-benz.com/en using the publisher's explicit youth
career-level codes 19–27, EN search language and ascending PositionID order.
All matching current language variants are retrieved; records group only an
identical publisher PositionID. Separate J000 identifiers are never merged.
Stable IDs remain SHA-256 of `de-mercedes-benz|mercedes-public-position|<PositionID>`
(first 24 hex characters), retaining existing published identity semantics.

## Inventory and source facts

The older reviewed snapshot contained 1,396 variants/492 identities. A paced
2026-10-14 official research response reports 1,459 variants and new identities
including J000233950/J000233952. Publishing old known recipes would omit current
opportunities. The adapter therefore discovers current totals and complete
100-row windows with three-row overlaps, repeats the full traversal, and requires
identical complete descriptor maps and exact unique variant totals before
normalizing. Structural validation replaces the obsolete fixed count/window frontier.
The 461 complete groups whose variant membership and full material hashes
match the prior review retain their original substantive factual summaries.
Fifty-four new/changed groups receive reviewed original summaries and complete
original factual condition evidence. Each recipe remains bound to exact current
descriptor hashes: unreviewed new/changed qualifications fail safely, rather than
being published with generic or stale summaries. Current robots and full
policy/entry-condition material guards remain. Inventories above 1,552 variants fail safely because
complete discovery would exceed the bounded physical request allowance.

Actual career-level labels and titles classify internships as internships,
working-student/holiday employment and thesis placements as jobs under the
existing source classification, doctorates as jobs and vocational/dual
study as training (with explicitly reviewed role-specific exceptions). Work placement alone does not establish an internship:
explicit working-student titles stay jobs. Hybrid internship/working-student
routes remain explicit. Classification evidence contains all variant URLs,
verified descriptor hashes, date facts and original factual condition summaries
covering actual eligibility, alternatives, application documents, mandatory and
preferred skills, timing, contract/pay and conditional benefits. Creative task
prose, recruitment invitations, contact details and wholesale qualification text
are not republished. The summary stays an original substantive English factual
summary; readers must consult the linked publisher notice.

### Current production block

Production has not published this source. The first GitHub-hosted attempt
[`38086095380`](https://github.com/YouthOpps/data-pipeline/actions/runs/38086095380)
received HTTP 403 from `https://group.mercedes-benz.com/robots.txt`. Attempt
[`38086095524`](https://github.com/YouthOpps/data-pipeline/actions/runs/38086095524)
restored that durable refusal and stopped before further publisher requests.
The workflow is manually disabled; its YAML triggers below do not mean it is
currently scheduled. See the [recorded recovery requirements](https://github.com/YouthOpps/data-pipeline/issues/115#issuecomment-6102178365)
and [follow-up review](https://github.com/YouthOpps/data-pipeline/issues/140).

No API key is required by the existing public search protocol. A missing key
is not the diagnosed failure, and HTTP 403 alone does not prove a robots
`Disallow` or a legal prohibition. Earlier successful cloud collections do not
establish access from a GitHub-hosted runner.

Recovery remains blocked pending a reviewed official access route, an approved
reachable runner, or an official feed. The distinct public API origin is a
candidate for review, not an automatic fallback: the persisted refusal covers
the entire publisher family, and the group pages below supply terms and
additional eligibility facts. An API-only replacement needs evidence of
permitted access, complete current inventory and authoritative coverage of
those facts before an explicit reviewed recovery can proceed. It must not
clear the refusal, rename the family, replace the latest artifact with the
bootstrap, rotate identity, or silently omit qualification requirements.

This access review does not certify the frozen inventory as current. After
permitted access is established, the snapshot checks described above also
need replacement with live extraction and pagination, followed by a complete
non-publishing collection and independent review. Re-enabling the workflow or
refreshing hashes alone is not a repair.

The [provider notice](https://group.mercedes-benz.com/provider/) reserves rights in creative text, images, media and page arrangements and gives specific terms precedence. The [careers terms](https://group.mercedes-benz.com/careers/general-terms-conditions.html) do not expressly prohibit the reviewed bounded public discovery and minimal original factual summaries. Account, upload and moderation conditions are not an authorization to access private applicant data. The implementation follows fresh actual robots policies for the API and group origins. The API's observed ordinary robots 404 is treated as an absent policy, never as a way around a refusal. HTTP 401, 403 or 429 remains a durable family block.

Country arrays describe actual API host locations, not nationality eligibility.
No eligible-country restriction is inferred. API publication-language code 2 is preferred for variant selection.
Only previously verified prose languages are retained; new recipes may leave
record language unknown instead of treating numeric API labels as proof of
the prose language.
All date-only publication/start/end facts remain in provenance; `published_at`
and `deadline` stay null because notice publication lifetime and employment start
are not verified application deadlines. Potential timing conflicts remain visible
in complete variant evidence rather than being silently reconciled.

## Access and rights

Fresh official API and group robots policies precede retrieval. Ordinary API
robots 404 means no robots policy; HTTP 401/403/429 remains a durable family
block and is never bypassed. The 2026-10-14 whole-page rights/condition review refreshed changed policy
fingerprints after checking actual current facts; all documented numerical school
and student-duration requirements, alternatives and residence/document clauses
remain present. The official
[provider notice](https://group.mercedes-benz.com/provider/) reserves rights in
creative material and the
[careers terms](https://group.mercedes-benz.com/careers/general-terms-conditions.html)
do not expressly prohibit bounded public factual indexing. This is not a blanket
content-reuse licence: photographs, branding, media and creative task prose are
excluded. Publisher provenance and original summaries remain explicit.

Four publisher-linked entry-condition pages remain guarded and are identified
in evidence only for descriptors that actually reference them:

- [Dual-study applications](https://group.mercedes-benz.com/karriere/schueler/duales-studium/bewerbung/): technical Abitur average at most 2.7/mathematics at least 8 points; business average at most 2.5/mathematics at least 10 points; Fachhochschulreife average and mathematics at most 2.0 plus DHBW aptitude test. These alternatives stay distinct.
- [Working students](https://group.mercedes-benz.com/karriere/studenten/werkstudenten/): excellent grades, regular study duration plus at most three semesters, outstanding examinations and 7–20 weekly hours, relevant internships preferred.
- [Internships](https://group.mercedes-benz.com/karriere/studenten/praktikum/): study documentation, voluntary-internship duration, degree-gap alternatives and applicable residence/work documents; accommodation depends on location/capacity.
- [Theses](https://group.mercedes-benz.com/karriere/studenten/abschlussarbeit/): above-average grades, relevant internships and applicable study/residence documents.

## Runtime, pacing and publication

Run `python -B adapter.py` for non-publishing run metadata or
`python -B test_adapter.py` for the actual complete validated live JSON collection.
The sole live test rejects publication credentials and never writes source data.
`python -B adapter.py --publish` is reserved for authorized production automation.
Python standard library only; the folder runs independently when copied.

The unchanged secure family pacing enforces at least six seconds between physical
starts, ten starts per rolling minute and a 60-second startup embargo. Two robots,
six policy/condition pages and at most 32 search windows consume at most 40 nominal
starts; the hard cap remains 42 including retries/redirects. At most two attempts
retry typed transient failures/HTTP503 with a persisted 30-second embargo.
Stricter publisher limits and durable family blocks remain enforced. Responses
are bounded to 8 MiB each/64 MiB aggregate. Prepublication is bounded to 1,800
seconds, publication 240, failure metadata 120 and pacing export 120; the absolute
process ceiling remains 2,280 seconds. Missing latest completed family pacing
artifacts block production instead of resetting request history. Existing
source-scoped App authentication, fresh healthy artifact export, schedule,
manual dispatch and scoped main-push workflow are preserved.

Publication atomically commits only this source's data/metadata, uses nonforce
compare-and-update and preserves a concurrently newer pair. Matching records
retain first-seen/creation times; substantive current evidence advances updated
time. A fetch/parse/validation/publication failure preserves last-good data and
success timestamps and records safe failure metadata when possible. First failure
creates no source folder. Tests/static review do not prove production delivery.

## Expired-record cleanup

Complete raw retrieval must validate as nonempty before deadline filtering.
Supported explicit offset timestamps expire strictly after their closing instant;
date-only deadlines, if supported by the source validator, wait until the date
ends worldwide in UTC−12 when no official publisher timezone is established.
Missing/ambiguous deadlines, status labels, record age and absence from a response
are never expiry evidence. Current API notice lifetimes are not application
deadlines. A successful full replacement never merges old records back, so
expired old entries cannot survive it. All-expired validated raw retrieval
publishes an empty successful replacement; later retrieval failures preserve
that empty successful pair and its success timestamp. Cleanup has no separate
remote deletion and occurs only in the next atomic source publication.

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
