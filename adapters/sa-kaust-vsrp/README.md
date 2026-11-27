# KAUST Visiting Student Research Program

Indexes individual research internships from the university’s [official project inventory](https://admissions.kaust.edu.sa/internship/search). The [program overview](https://admissions.kaust.edu.sa/study/internships) describes eligibility, funding and the application process. Each record links directly to its official project page.

Collection reads robots.txt and the complete project inventory once per refresh. The server renders all project cards in one HTML response. Sorting and filtering run on the client. The adapter checks the inventory heading, card boundaries, project URLs, unique identifiers and indexing directives. It omits descriptions, eligibility countries, ambiguous publication times and deadlines. Availability follows listing membership. Changes to older projects and removals require full reconciliation.

Daily collection runs at 14:28 UTC. Publisher access shares the original KAUST family state, with at most five starts per rolling minute, at least 12.2 seconds after physical completion, and a 30-second inventory gap. The adapter honors retained holds and rate limits. Retrieval, indexing-policy, validation and publication failures preserve the last successful inventory. Successful replacement excludes stale removed listings and records with proven elapsed deadlines. Omitted deadlines remain eligible for publication.

Run the nonpublishing test through its reviewed GitHub Actions job. The production job writes the source data and metadata together, preserving first-seen timestamps of unchanged records.

The [native GitHub Actions test](https://github.com/YouthOpps/data-pipeline/actions/runs/45963219634) verified the complete inventory with the website consumer and retained its pacing handoff. [Source review and production history](https://github.com/YouthOpps/data-pipeline/issues/504)
