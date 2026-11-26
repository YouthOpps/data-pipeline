# General Motors early careers

General Motors lists internships, co-ops and graduate programmes through its [early careers site](https://search-careers.gm.com/en/early-careers/). That page links to individual vacancies as well as standing descriptions of its internship and TRACK programmes. A programme overview alone is insufficient evidence of a current application.

## Inventory and verification

The early-careers page links to a search URL containing the Students & Recent Graduates team. An [official response](https://github.com/YouthOpps/ai-workspace/actions/runs/45803204708) showed 617 matching vacancies across multiple teams. The visible briefcase field on each card identifies the actual team. On the first page, only two of 20 cards carried the exact Students & Recent Graduates value. The parser requires this value together with the card's native JR identifier, title and a matching General Motors detail link.

The [pagination observation](https://github.com/YouthOpps/ai-workspace/actions/runs/45804977685) returned the same range and 20 native IDs for the first page and its immediate next link. Both responses reported 1–20 of 617 matching jobs. The collector rejects repeated ranges, duplicated IDs, changes in the total, unpublished or restricted next links and incomplete terminal pages. The current HTML path did not establish complete coverage.

A retained [posting for JR-202624606](https://github.com/YouthOpps/ai-workspace/actions/runs/44921966565) contained a current Apply Now link, a visible posting date and an explicit enrollment requirement. The detail parser checks the visible title, team, JR identifier and posting date against the native JobPosting schema and requires the canonical link and application link to identify the same vacancy. The examined page did not establish a closing date or permission for foreign students to work. The parser keeps those facts unknown. The Workday application site is not queried.

Development and source evidence are tracked in [data-pipeline #254](https://github.com/YouthOpps/data-pipeline/issues/254). Publication and website indexing still require a proven complete source path and the normal producer validation.
