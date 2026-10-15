# de-fulbright-germany

The reviewed official Fulbright Germany programme page is [Studienstipendium (Uni und HAW)](https://www.fulbright.de/stipendien/programm/studienstipendium-uni-und-haw). The adapter retrieves the reviewed single programme page using public HTML, robots checks and standard-library HTTPS. Stable identifiers derive from the canonical URL. It preserves the existing source attribution, classification and unknown eligibility; incidental page dates do not establish an application deadline. The inventory is exactly this one standing programme overview; linked awards, unrelated calls and deadline text without validated clock semantics are excluded. `deadline` remains null and the parser accepts only supported timezone-aware timestamp fields.

Access checks use the publisher’s current robots rules before requesting the page, fail on disallow or changed canonical/title, and honor its declared pacing. The output retains only the official title, URL and factual classification; no article body or images are republished. No broader content-reuse licence is claimed.

Run `python -B adapters/de-fulbright-germany/test_adapter.py` for the sole real, nonpublishing collection test; it validates and emits the complete collected JSON. Authorized publication uses `python -B adapters/de-fulbright-germany/adapter.py --publish`.

## Expired-record cleanup

A successful refresh validates the complete nonempty collected inventory, then excludes records whose supported `deadline` has passed before preparing the atomic source data/metadata replacement. Previously published records are used only to preserve matching records' first-seen timestamps; they are never merged back into the replacement. Thus expired old records cannot survive a successful replacement. Missing deadlines, ambiguous clocks, status, age and absence from the response are not expiry evidence.

Explicit timezone-aware deadlines expire strictly after their instant. For date-only deadlines without a documented publisher timezone, cleanup waits until the closing day has ended in every legal timezone: the current date in UTC-12 must be later than the closing date. This conservative boundary retains the full closing day without inventing a publisher timezone; deletion can be delayed up to 26 hours relative to the earliest civil closing-day boundary. Unsupported or ambiguous source clocks remain null deadlines; invalid timestamp fields fail validation before cleanup. If every collected record is expired, the valid nonempty retrieval publishes an empty successful replacement. A later empty upstream retrieval still fails and preserves that last-good pair.

Fetch, parse, validation and publication failures preserve last-good data and success timestamps. Cleanup performs no separate deletion or remote write; publication updates only this source's data/metadata atomically, with the existing concurrent-update protection. The live test collects real inventory without publication; offline boundary checks and static publication review verify cleanup.

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

## Daily automation

The scheduled refresh runs once daily at **19:25 UTC** (`25 19 * * *`).
This fixed time was selected randomly within a staggered daily timetable; it
is not randomized again on each run. Main-merge and manual triggers remain
available, so this is one scheduled run per day, not a hard limit on all runs.
GitHub can delay scheduled events; the existing publication concurrency
serializes source jobs when they queue. Existing disabled workflows remain
disabled until their access hold is separately resolved.

The current collector has no paginated inventory to delay. Its existing request
pacing and publisher restrictions remain in force; this source changes only its
daily automation schedule.
