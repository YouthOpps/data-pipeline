# University of Coimbra undergraduate admissions

This source indexes the public [University of Coimbra admissions calendar for its Brazil audience](https://brasil.uc.pt/). Each record represents an application round for the published academic year. The 2027/28 source lists three ordinary rounds and an extraordinary round. Verify the degree, places, entrance requirements and application instructions on the university page.

| Round | Opens | Application deadline |
| --- | --- | --- |
| First | 14 December 2026 | 26 January 2027 |
| Second | 15 February 2027 | 14 April 2027 |
| Third | 19 May 2027 | 19 July 2027 |
| Extraordinary | 6 August 2027 | 13 August 2027 |

The adapter takes dates from the `Candidatura` rows of the undergraduate calendar. It uses Europe/Lisbon calendar dates and retains a phase through its deadline date. Future rounds and the active extraordinary round use an unknown status because availability can depend on the degree. Portugal is the host country. The Brazil-facing site does not establish nationality eligibility, so the records leave eligible countries unspecified. The catalogue covers round dates. Postgraduate rounds, results, enrolment, individual degree listings, forms and embedded content are outside scope.

## Publisher access

The only publisher requests are `https://brasil.uc.pt/robots.txt` and `https://brasil.uc.pt/`. Requests use `YouthOppsBot/1.0 (+https://youthopps.org/bot/)` and preserve the stricter of the current and legacy applicable robots rules. The [original GitHub Actions research](https://github.com/YouthOpps/ai-workspace/actions/runs/44181500355/job/134386706311) assessed both exact resources, with HTTP 200 and no applicable observed barrier on the public page. The adapter follows the workspace policy for unavailable robots, and retains denials, challenges, indexing restrictions, request pacing and timed limits in the publisher's owned state. It follows no redirects and opens no additional origins.

## Test and publication

The sole collection test is `python -B adapters/pt-coimbra-brazil-admissions/test_adapter.py`. Run it in its reviewed nonpublishing GitHub Actions job. The test validates the complete four-round calendar and returns its records as JSON. The [original nonpublishing test](https://github.com/YouthOpps/data-pipeline/actions/runs/44185888446/job/134402172685) validated all four records with the site consumer and preserved the publisher history from first contact.

The source workflow runs daily at 19:46 UTC and supports scoped main-branch changes and manual dispatch. It serializes this publisher's collection and restores the newest verified pacing state. Publication atomically updates the source's data and metadata. A failed collection preserves the last successful data. A successful refresh removes expired rounds.
