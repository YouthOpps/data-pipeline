# Google student and early-career programme information

This source collects 15 substantive Google programme information pages. It does
not collect the worldwide Google vacancy inventory or claim that a programme's
individual applications are currently open. Each record links its actual Google
page and uses an original factual summary of benefits, entry conditions and any
published date conflicts. Publisher country `US` identifies Google LLC; it does
not limit applicant nationality.

The official starting site, <https://buildyourfuture.withgoogle.com/>, directs
visitors through its own literal script to Google's current student site:
<https://www.google.com/about/careers/applications/buildyourfuture/>. Twenty
Google HTML inputs retain the complete visible material, literal links and form
and filter controls, including all hidden cards: one scholarship, three programme
cards, twenty apprenticeship cards and eight early-career cards. Three internship
information tracks are covered without claiming exhaustive vacancies. One paid
apprenticeship overview represents all twenty tracks, rather than creating fake
individual listings from shared search links. Military-transition SkillBridge and
the experienced career-break gCareer route are excluded.

The original redirect shell, its exact reviewed literal script and Google terms
are also required. The collector strictly JSON-decodes the first HTML string of
the observed `ds:0` callback; it never evaluates scripts or calls guessed APIs.
Material changes, missing inputs, access challenges or a changed finite frontier
fail closed. Google's optional outer closing HTML tags are not mistaken for an
incomplete response; the embedded programme document must be complete, and HTTP
length/body limits remain enforced. The original redirect shell and JavaScript may return the observed `gzip`
content encoding. Reviewed publisher routes may
use one complete gzip member; compressed and decoded bodies are each bounded to
eight MiB. Invalid checksums, truncated streams, concatenated members, trailing
bytes and unknown or combined encodings fail closed. Both encoded and decoded
publisher bytes count toward the operation's 64-MiB aggregate byte ceiling;
request, phase and body limits remain unchanged.

Fresh robots policies are checked on all three required origins. The bounded REP
matcher preserves queries and percent octets, supports wildcards and longest
matching rules, and favors Allow on equal specificity. This prevents Python's
standard robotparser from incorrectly treating Google's `Disallow: /?` as a ban
on every path. Actual candidate-preparation, connection and job-result pagination
exclusions remain enforced. Stricter rate extensions require fresh review.

Google terms prohibit automated access contrary to machine-readable instructions.
This source republishes original factual summaries and links, without claiming an
open content license. The Ireland scholarship's complete eligibility is delegated
to IIE; IIE's terms prohibit automated extraction. No IIE facts, forms or FAQs are
collected. The record retains only Google's EUR 5,000/year for two years, selection
basis and October 5, 2026 closing, and explicitly leaves complete minimum
eligibility unverified. Other delegated application, research, account and
administrator sites are links only.

Explicit closed or past calls are marked expired. Yearless recruitment seasons
have no invented deadline. Contradictory or unverified availability stays unknown.
The US Legal Summer Institute's 2027 closing date is retained with its conflicting
cohort and eligibility statements. No timezone, nationality restriction or current
vacancy is inferred.

## Run and publication

Run `python -B adapters/google/test_adapter.py` for one complete live,
non-publishing collection. The test emits all actual records as one JSON document
on stdout. It needs no publication credentials and writes no collection files.
Independent static and live acceptance are required before delivery.

Every physical publisher start, including robots, redirects and a single bounded
503 retry, uses one exclusive `youthopps-google-publisher-v1` family: at least six
seconds between starts and no more than ten per rolling sixty seconds. Local
collection locks prevent overlap. All four original Google research pacing files
are locked and merged without modifying their inodes; interval and embargo use
MAX, refusal uses OR and starts use union. If the union exceeds ten retained
entries, the embargo conservatively waits until every merged start ages. The
collector performs an initial sixty-second wait and retains permanent refusal
state for HTTP 401/403/429. There is no automatic refusal reset or TLS bypass.

The normal complete input set requires 26 physical starts (23 inputs plus three
robots). Its theoretical minimum is 210 seconds: sixty-second startup plus 25
six-second intervals, before network and parsing time. The hard cap is 100 starts,
eight MiB per response and 900 seconds for prepublication. The whole operation is
1290 seconds, with 180-second publication and failure phases and thirty seconds
reserved for inert pacing-state export; the job has thirty minutes. The
publication App token is minted immediately before the sole application command.

The first production bootstrap needs a concrete maintainer-reviewed history and
pacing receipt, supplied as `GOOGLE_PACING_BOOTSTRAP`. Until that explicit workflow
binding is added, first production access fails closed. Subsequent runs restore
the newest completed family attempt's authenticated inert artifact, including
reruns and failures. Artifact restoration never resets a prior refusal or embargo.

Only `datas/google/data.json` and `datas/google/metadata.json` can be published.
Success replaces both in one source-scoped compare-and-swap commit and preserves
all siblings. Existing source pairs are fully validated before publisher access.
A first-run failure creates no source folder or empty/failure pair. Later failures
preserve every last-good data byte and success/check timestamp, updating safe
failure metadata only when the complete prior owned pair remains unchanged.
Record creation/first-seen times survive matching identities; update time changes
only with substantive fields. Last-seen uses collection START, and every record's
last-checked and metadata's last-checked use actual collection END.

Each physical publisher attempt persists completion plus the native interval,
including failed opens, body processing and response closure. A monotonic gate
also enforces the elapsed interval; longer inherited intervals and embargoes
remain effective. The exclusive family collector preserves a full 60-second
startup embargo after legacy/artifact restoration to age abandoned attempts.
A failed completion-state save prevents any further publisher attempt.

## Expired-record cleanup

Google retains supported dated Summer of Code, US Legal Summer Institute and
Google Ireland scholarship application closings. Null deadlines and conflicting
programme status wording never authorize removal.

The complete fresh inventory is fetched and validated before cleanup. The next
source-scoped atomic data/metadata publication replaces the prior dataset rather
than merging its rows, so expired previously published records cannot survive
that successful replacement. Current records and records with missing or
ambiguous deadlines remain eligible; status alone does not determine expiry.

Offset-aware deadlines expire only after the stated instant. A date-only
deadline with no documented publisher timezone remains eligible through that
date in every civil timezone: cleanup uses the conservative UTC-12 calendar
boundary, without guessing a publisher timezone or an unstated closing hour.
The source recipes and their original deadline fields remain unchanged.

A valid nonempty fresh inventory may produce an empty cleaned dataset; that
empty result is published atomically with success metadata. An empty upstream
collection still fails validation. Fetch, parsing, validation and publication
failures preserve the last-good dataset (including a prior empty success) and
success timestamp; cleanup never performs a separate deletion or writes another source.
The sole live test validates the complete nonpublishing inventory, including
closed records needed to verify source completeness; publication alone applies
the deadline filter after that validation.

On 14 October 2026, the reviewed original shell at
https://buildyourfuture.withgoogle.com/ returned HTTP 200 with Content-Encoding
gzip: its 96,098 decoded bytes matched the existing material fingerprint. The
bounded standard-library gzip decoder now applies to every reviewed publisher
route. Wire and decoded byte limits/accounting, fingerprints, robots checks and
transport policy remain enforced; unsupported encodings still fail with the
reviewed route in evidence.
