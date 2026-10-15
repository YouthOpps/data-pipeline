# NASA internships adapter

Collects the official NASA programme overview from
https://www.nasa.gov/learning-resources/internship-programs/.
It checks robots policy before public HTML retrieval and uses the publisher's
canonical URL, structured WebPage title and original publication timestamp.
It does not collect application listings, claim inventory of individual
internships, or infer applicant eligibility. Stable identifiers hash the source
and canonical URL; internship classification and US publisher attribution refer
to this programme overview. No application deadline is currently extracted.

Run `python -B adapter.py` for non-publishing collection metadata or
`python -B test_adapter.py` for the complete validated live JSON collection.
`python -B adapter.py --publish` is reserved for authorized production automation.
Requests are paced at least six seconds apart and robots delays are honored.

NASA's official [Images and Media Usage Guidelines](https://www.nasa.gov/nasa-brand-center/images-and-media/)
distinguish NASA informational material from protected identifiers and third-party
material, and require source credit when NASA material is used. This adapter
publishes factual programme metadata with the official NASA source link; it does
not reproduce photographs, branding, media or third-party creative material.
Robots permission is an access check, not a copyright licence. The source link
and US public-institution publisher attribution provide provenance; applicants
must consult NASA's page for current programme terms and eligibility.

## Expired-record cleanup

Successful refreshes validate the complete fetched inventory before removing
records with verified past application deadlines. Offset-aware timestamps are
compared as instants; equality with the closing instant remains eligible.
No official timezone is established for date-only deadlines. Cleanup waits
until the date has ended worldwide (UTC−12), a conservative certainty bound,
not an inferred publisher timezone. Explicit publisher offsets take precedence.
Missing deadlines, ambiguous deadlines represented as null, status labels,
record age and absence from a response are never expiry evidence. The existing
publication contract replaces the complete source inventory; old records are
not merged back into the replacement. Consequently expired old records cannot
survive a successful replacement, including when rediscovered with a verified
past deadline. Current records with missing deadlines remain eligible.

The filtered data (including an empty subset after valid nonempty retrieval)
and success metadata are committed atomically only
under this source's paths. An empty/invalid raw response or fetch, parse,
validation or publication failure preserves last-good data and its success
timestamp; safe failure metadata may be updated. Tests never publish.

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

`youthopps-us-nasa-internships-publisher-v1` serializes runs of this source. Up
to 100 pending runs are retained (`queue: max`); active runs are not cancelled
(`cancel-in-progress: false`). Unrelated publisher families may collect and
publish in parallel. Existing source-only atomic publication rebases on
unrelated repository updates, uses up to three publication attempts, and never
force-overwrites newer data.
