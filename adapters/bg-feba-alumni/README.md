# FEBA Alumni opportunities

The standalone standard-library adapter reads the official public
[FEBA Alumni news API](https://febalumni.org/wp-json/wp/v2/posts) linked to
[the publisher news site](https://febalumni.org/news/). It checks robots
before requests, spaces requests by at least six seconds, honors backoff
and fails on access refusals. Public access is not an open licence;
records retain FEBA attribution and original notice links rather than
redistributing article content.

The inventory is paginated in batches of 100 through the terminal short
page, with a 25-page safety cap that fails rather than publishes partial
results. Explicit application-call titles in scholarship, training,
internship and volunteering categories are included; outcomes, winners,
concluded events and unrelated news are excluded. Stable identifiers use
the source slug and canonical publisher notice URL. Published WordPress
UTC timestamps are retained when valid. Application deadlines are parsed only from explicit `краен срок` calendar
dates in current titles that establish application, enrolment or scholarship
competition context. Dates are validated, and different closing dates in one
title remain ambiguous/null. Publication age, URL date text and event dates
never establish a cutoff.
Host country BG identifies the supported local offer context; citizenship
eligibility is not inferred and eligible-country arrays remain empty.

Run from this folder:

```sh
python -B adapter.py
python -B test_adapter.py
python -B adapter.py --publish
```

The sole live test collects and validates real nonempty records and emits
the complete JSON array without publication or publication credentials.
Production alone uses DATA_SOURCE_TOKEN, atomic source-only Git-tree commits
and nonforce branch updates to publish data and metadata together under
`datas/bg-feba-alumni/`. New records replace the prior collection; matching
IDs retain first-seen/created timestamps. Failed retrieval, validation or
publication preserves last-good data and its successful timestamp; a safe
subsequent failure records metadata only, and first failure creates no folder.

## Expired-record cleanup

Supported Bulgarian application-closing title dates remain date-only without
an invented closing timezone. Other titles retain null deadlines. Current
publisher titles take precedence over stale URL text: the live scholarship
notice title states 20 October 2026 although its unchanged URL includes
16 October. No deadline is inferred from the URL or article publication date.
Null/ambiguous values remain eligible; supported aware timestamps, if supplied
in a validated record, use their stated offsets.

After a complete nonempty retrieval passes validation, the successful refresh
removes records with verified past application deadlines before preparing the
single atomic data/metadata publication. The next dataset fully replaces the
old dataset; old records are never merged back, including expired old IDs
still returned by the publisher. Status, record age, absence from a response
and missing or ambiguous deadlines do not establish expiry.

For a supported date-only closing without a documented timezone, UTC-12 is
used only as a conservative global certainty bound, not an inferred publisher
timezone: retain the record throughout its possible local closing day, and
remove it only once that date has ended everywhere. Aware timestamps are
retained at the exact cutoff and removed afterwards.

If every validated retrieved record has expired, publish an empty data array
and success metadata atomically, with record_count zero. An empty or invalid
upstream collection still fails. Later runs accept this valid empty prior
pair; failures preserve it and its last-success timestamp, updating only safe
failure metadata. No cleanup write precedes successful collection, and no
other source's files are touched. The live test remains a complete raw
nonpublishing collection; production publication is reviewed statically.

## Reviewed editorial exclusion

The publisher's Rumen Petkov notice is an alumni interview, with the explicit
body label `Разговор с`, personal career questions and retrospective study
experiences. Its generic title phrase about an opportunity for training and
development is not an application call. Such publisher-labelled interviews
are excluded when their title has no explicit application, enrolment or
closing-call language; a genuine titled call is retained even if its body also
includes an interview. This uses current source context, never the 2023 date
or any invented expiry. The live API content field is required and validated
for candidate posts so missing body evidence cannot silently bypass this check.

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
