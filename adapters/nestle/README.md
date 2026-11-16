# Nestlé internships and university jobs

This standalone standard-library adapter indexes the publisher's counted
[Internships/University Jobs category](https://jobdetails.nestle.com/go/InternshipsUniversity-Jobs/3034301/).
It does not claim all Nestlé youth opportunities worldwide: functional-category
graduate programmes, separate national recruitment sites and the US early-career
catalogue are outside this first source. The category count is observed on each
run, never hardcoded. Explicit internship/apprenticeship title markers determine
the normalized type; otherwise a university-category vacancy remains `other`
rather than inventing its employment type.

Every public pagination link needed for the advertised count is followed. Each
unique numeric posting ID gets its public detail page; duplicate desktop/mobile
links must agree. Category ranges, counts, IDs, detail titles, locations and
canonical identities must reconcile. A second complete inventory must match
before publication. Changes or incomplete scans fail without replacing good data.

Records contain attributed factual titles, employer/location, posting date,
native `nestle-<posting-id>` IDs and canonical links, plus an original short
summary. Descriptions, images, recruiter contacts and application forms are not
republished. A location is not nationality eligibility. Structured `validThrough`
is accepted only with an explicit timezone; programme start dates are not
application deadlines. Successful full refreshes remove vanished listings and
unambiguously expired records, including an empty filtered result; empty raw
retrieval is an error. Original creation/first-seen timestamps survive refreshes,
while updated timestamps change only when material facts change.

## Access and retained state

Research on 2026-11-16 used exactly
`YouthOppsBot/1.0 (+https://youthopps.org/bot/)`. The same identity is used by
production and the native test. The observed jobdetails robots rules permit the
category/detail routes and disallow `/services/`, `/talentcommunity/`,
`/applybutton/` and other application/account routes; none are fetched. There is
no guessed ATS API. [Terms](https://www.nestle.com/info/tc) and
[copyright](https://www.nestle.com/info/copyright), reached through the publisher's
footer, were reviewed. No explicit automated-access ban was found; their general
reproduction restrictions are addressed by minimal factual indexing and original
summaries. This is operational review, not blanket permission to reproduce content.

The existing `youthopps-nestle-publisher-research-v1` family is retained, including
all six research reservations, policy rules and explicit unknown-history marker.
The original research journal SHA-256 is
`9ec1297b856fc4c22df2a4081d7fba92a8996912e60c893398b2f23948959990`;
[the exact migration provenance](https://github.com/YouthOpps/data-pipeline/issues/372#issuecomment-6924734579)
is also bound by source-local immutable constants.
Bootstrap is solely for this previously unpublished source and contains the
exact independently validated final native-run state: 86 retained starts,
generation 554, all research/native MAX constraints and policy history. Its
acknowledged journal SHA-256 is
`419dc23539dc7bc693b60d70abcb1e2f3ff00ebc7d5ea83c94f71f2d77ce4215`.
It is not a reset path.
Actual job chronology and exact run/attempt/head-owned artifact digests determine
the latest durable state; missing newer state fails closed. The only skip is a
failed job shorter than 58 seconds whose immutable adapter/workflow bytes match
this version and therefore cannot have passed its 60-second monotonic wire gate.
Job metadata, workflow state and current run identity are rechecked before access.

Publisher requests are sequential with a minimum 12.2-second completion-to-start
gap, at most five starts per rolling minute, and at least 30 seconds between
inventory completion and next inventory start. Stricter retained robots rules
and cooldowns win. A 429 doubles the current physical and inventory intervals,
persists its strike and MAX Retry-After/exponential minimum 1,800-second embargo
before reading its body, and stops collection. Challenges and unassessed
transport/storage failures retain a barrier. There is no identity rotation or
silent fresh-budget recovery. A private owner-only locked journal survives local
restarts; missing or corrupt mature state fails closed.

## Execution and publication

Run the sole complete native test with
`python -B adapters/nestle/test_adapter.py`. It refuses publication credentials,
prints the complete nonempty raw record array to stdout and unittest diagnostics
to stderr. Local execution requires this new workflow to be absent, or an
existing workflow to be manually paused; checks occur before initialization and
again after quiet time before first source access. Do not reset its journal or
run another collector in the same publisher family.

The matching Action runs only `python -B adapters/nestle/adapter.py --publish`
daily at 13:43 UTC, after a relevant main-branch merge or on manual dispatch.
It serializes the whole publisher family and retains state for 90 days. The
50-minute nonrenewable runtime, 45-minute collection ceiling and 55-minute job
limit leave publication/export and one-hour app-token headroom. A larger
catalogue that cannot fit fails safely, without truncating coverage.

Publication atomically updates only `datas/nestle/data.json` and
`datas/nestle/metadata.json` through one non-force Git tree/commit update.
Unrelated sources are preserved across bounded ref conflicts; newer Nestlé
content is never overwritten. Later failures preserve the previous data and
success timestamp and update failure metadata when publication remains possible.
A failed first run creates no source folder. The data token is never sent to
publisher hosts or redirected artifact hosts.

Implementation tracking: [data-pipeline#372](https://github.com/YouthOpps/data-pipeline/issues/372).
## Accepted native evidence

On 2026-11-16, the original nonpublishing test passed and independent QA checked
all 34 raw records against all 34 detail bodies and both complete four-page
inventories. All 43 HTTP responses were successful; all 4,355,999 returned bytes
and 299 journal/RAM witnesses were retained privately in RAM. The actual minimum
physical completion gap was 12.200781948 seconds, inventory gap 30.000918757
seconds, and maximum rolling-minute starts five.

The current website consumer accepted all 34 raw and 34 expiry-filtered records;
there were no actually expired records. A separately derived valid-empty envelope
also passed. This does not claim that empty source retrieval succeeded. Two prior
original failures (explicit UTC deadline formatting and nested PostalAddress)
remain recorded independently; their state is included in the retained MAX.

[Exact code, observer and state provenance](https://github.com/YouthOpps/data-pipeline/issues/372#issuecomment-6925723220)
binds this acceptance. The final bootstrap changes only retained state and
provenance, without changing the tested collection/parser functions. Native
acceptance is not a claim that production publication has already occurred.
