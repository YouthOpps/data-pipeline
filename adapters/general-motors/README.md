# General Motors early careers

General Motors lists internships, co-ops and graduate programmes through its [early careers site](https://search-careers.gm.com/en/early-careers/). That page links to individual vacancies as well as standing descriptions of its internship and TRACK programmes. A programme overview alone is insufficient evidence of a current application.

## Inventory and verification

The early-careers page links to a search URL containing the Students & Recent Graduates team. An [official response](https://github.com/YouthOpps/ai-workspace/actions/runs/45803204708) showed 617 matching vacancies across multiple teams. The visible briefcase field on each card identifies the actual team. On the first page, only two of 20 cards carried the exact Students & Recent Graduates value. The parser requires this value together with the card's native JR identifier, title and a matching General Motors detail link.

The [pagination observation](https://github.com/YouthOpps/ai-workspace/actions/runs/45804977685) returned the same range and 20 native IDs for the first page and its immediate next link. Both responses reported 1–20 of 617 matching jobs. The collector rejects repeated ranges, duplicated IDs, changes in the total, unpublished or restricted next links and incomplete terminal pages. The current HTML path did not establish complete coverage.

A retained [posting for JR-202624606](https://github.com/YouthOpps/ai-workspace/actions/runs/44921966565) contained a current Apply Now link, a visible posting date and an explicit enrollment requirement. The detail parser checks the visible title, team, JR identifier and posting date against the native JobPosting schema and requires the canonical link and application link to identify the same vacancy. The examined page did not establish a closing date or permission for foreign students to work. The parser keeps those facts unknown. The Workday application site is not queried.

Development and source evidence are tracked in [data-pipeline #254](https://github.com/YouthOpps/data-pipeline/issues/254). Publication and website indexing still require a proven complete source path and the normal producer validation.

## Complete sitemap and resumable detail verification

The [sitemap observation](https://github.com/YouthOpps/ai-workspace/actions/runs/45806188770) returned a standard sitemap with 4,965 entries. Its translated detail URLs accounted for 4,319 entries, representing 617 unique native JR identifiers. Each had exactly one English detail URL and a last-modified timestamp. The English set matched the total reported by the portal. The sitemap has no team field. Generic titles can belong to the student team, so title matching cannot safely exclude roles.

The sitemap reader fails on duplicate English identifiers, a translated requisition without an English counterpart, an unknown JR route, missing timestamp or an unexpected host. Literal non-ASCII source paths are preserved and converted to percent-encoded bytes only for transport. Discovery uses the public sitemap. The site’s saved-job and personalised recommendation services are outside the collection scope.

Detail verification is staged within a single content generation. Each batch verifies the visible and structured identity, title and team before recording a receipt. Changed native URLs and last-modified values invalidate earlier receipts. Removed IDs drop from the candidate set when the sitemap is reread. A closing sitemap must agree with every observed row before the complete result can be released. A new generation starts a full reconciliation. A sitemap timestamp has not been independently proven to capture every eligibility or application change between generations.

A content checkpoint is separate from the publisher request budget. It links to the preceding checkpoint digest, contains per-JR verification results, and must be loaded from an authenticated, exclusively owned artifact. An interrupted batch cannot publish a partial set. The caller must still journal every physical request before opening it and carry forward all starts, cooldowns, holds and unfinished requests across batches.

First production acceptance requires a complete real baseline. At 617 details the 12.2-second minimum alone requires over two hours, excluding response times, registration, policies, and closing reconciliation. Select a batch size that fits the actual time and publisher budget, validate an initial set of different layouts and teams, and review applicable terms. A separately reviewed hosted collector must prove recoverable checkpoint ownership and exclusive pacing, run the single live nonpublishing test, and submit its full output to the website consumer. Only then can a reviewed producer merge, publish its data/metadata pair and be verified against the live catalog, search and sitemaps.
