# ONEK youth funding opportunities

This standalone standard-library adapter discovers opportunities from the
[Youth Board of Cyprus funding website](https://youthfunds.onek.org.cy/en/).
The source entry is the [Youth Initiatives programme](https://youthfunds.onek.org.cy/en/services/youth-initiatives/).

## Access and reuse

Public HTTPS HTML and the calendar's own public listing endpoint are used,
without authentication or application submission. Robots are checked before
access. The publisher reserves copyright; no open licence is claimed.
Records contain official titles, short original factual summaries, attribution,
and publisher links. Applicant details, recipient lists, images and full
publisher prose are not republished. Application portals and private APIs
are not crawled. The linked programme guides remain applicant references;
their historical amounts are not substituted for current call facts.

## Discovery and extraction

Each collection discovers programme links from the programme directory,
extracts application windows from their current tables, and follows every
advertised news pagination link. News cards labelled Calls, Extensions or
Events are inspected. Calls supply titles and deadlines; extensions update
the matching round rather than create duplicate awards. Programme tables
and corresponding notices are reconciled. Beneficiary/result announcements
and retrospective event reports are excluded.

The calendar uses its current form nonce and paginates public listings until
an explicit empty response. Every discovered event detail is inspected.
Publisher-labelled test descriptions are excluded; substantive new events
are collected. Missing structures, ambiguous rounds or extensions, conflicting
deadlines, and nonadvancing/excessive pagination fail collection. Normal
publisher text, dates, new notices, pagination and nonce changes are accepted
without hash updates. There are no frozen input hashes or opportunity profiles.

Round IDs retain the source/programme/year/round SHA256 key convention.
The activity period establishes the edition where an English table's heading
is stale. Joint Green action tracks with one application window share an
identity. Three legacy URL-to-key aliases preserve historical identities
whose notices do not expose a sufficient semantic key. New unmatched
programme/event identities use their stable publisher URL. Newly discovered
programme names and official notice links associate their notices with the
same programme/year/round identity. Lettered rounds and written/numeric
ordinals are parsed rather than defaulted to the first call.

Application dates remain date-only unless the publisher establishes a timezone.
Activity dates do not become deadlines. Future application windows and the
closing day have unknown status; past deadlines are expired. Generic website
Open badges do not override application windows. Citizenship and unstated
destinations remain unknown. Youth Initiatives' stated Cyprus activity scope
is retained. Summaries combine extracted application/activity windows with
brief current HTML description, audience and funding excerpts; corresponding
evidence retains attributed excerpts and programme links. Original English
or Greek text uses `summary_language` `en` or `el`, capped at 600 UTF-16 units.
For example, the programme HTML describes youth/group grants, youth aged
13-35, and eligible travel costs where stated. Unstated amounts, passport
rules and PDF-only conditions are not guessed. Calendar records include
their live body and schedule; absent application deadlines remain unknown.

## Run and publication

```sh
python -B adapters/cy-onek-youth-funds/test_adapter.py
python -B adapters/cy-onek-youth-funds/adapter.py --publish
```

The sole live test performs a complete nonempty collection, validates every
record, uniqueness and provenance, and emits the actual complete JSON on stdout.
Its expected count is dynamic. Keep test stdout in memory; no publication
credentials, data files or production Actions are used for validation.

The persistent `youthopps-onek-publisher-v1` allowance serializes collectors,
with at least six seconds between starts and at most ten per rolling minute.
Robots, pagination, redirects and retries share that allowance. Retry-After
and refusals preserve embargo/blocked state. Production restores the latest
trusted family attempt's timing artifact, including failed/cancelled attempts;
missing newest state cannot fall back to older successful state.

Publication remains source-scoped and atomic at
`datas/cy-onek-youth-funds/{data,metadata}.json`. First failure creates no
source folder. Later failure preserves last-good data and success time,
with failure metadata when safely possible. Previous pairs are validated;
conflict-safe commits avoid overwriting newer source data. Stable IDs preserve
creation and first-seen timestamps; material changes advance updated time.

The existing matching main-push, schedule and manual workflow retains its
main/repository guard, restricted credential, serialized family concurrency,
30-minute job limit and trusted timing artifact. Collection has a 900-second
bound within the 1,290-second lifecycle, including bounded publication,
failure reporting and export phases. Production runs require maintainer
authorization; live collection and static review do not prove publication.
