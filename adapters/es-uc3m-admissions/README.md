# UC3M international undergraduate early admission

This standalone source indexes the Universidad Carlos III de Madrid English
early admission route for candidates from non-Spanish secondary education
systems. It reads the university's public robots policy and general conditions,
then the official [calendar](https://www.uc3m.es/bachelor-degree/admission/application/international-students/early-admission/deadline)
and [eligibility](https://www.uc3m.es/bachelor-degree/admission/application/international-students/early-admission/what-is)
pages. The source does not traverse application forms, submit data, follow
redirects, duplicate records by degree or collect applicant data. Other UC3M
admission routes are outside this inventory.

Observed on 17 November 2026, the two admissions pages confirmed an opening on
5 October 2026 and calendar cutoff on 2 April 2027. Individual degrees may close
early. Equivalent qualifications, a strong academic record and B2 in the
teaching language apply to this route. Some education systems require residence
outside Spain. 4U Alliance and international dual degrees are excluded. The
record does not infer eligible citizenships or funding. The role of a date-only
cutoff is the end of the stated date in Europe/Madrid. That is an index expiry
convention, not a claim that UC3M accepts applications until a stated hour.

The [general conditions](https://www.uc3m.es/inicio/informacion-legal/condiciones-generales-uso-portal-uc3m)
were reviewed for this exact publisher and public scope. They prohibit
interference and constrain copying and redistribution of protected content.
The narrow review found no express automation or database-extraction restriction
for these factual fields. The adapter produces an original summary and links to
the official route. If the reviewed text, eligibility or scope changes,
collection stops for review.

One stable ID per intake cycle uses the source slug and cutoff year. Each
successful refresh verifies the entire finite inventory. There is no reliable
incremental feed or complete change history. A daily UTC run at 03:43 and the
repository/main guard apply. Publisher state is shared by exact family identity
across robots, terms and admissions. The adapter retains at least 12.2 seconds
between physical completion and next start, five starts per minute and the
separate 30-second inventory gap. Explicit stricter policy and 429 cooldowns
take precedence. Pending attempts and retained refusals prevent new access.

The sole live test is `python -B adapters/es-uc3m-admissions/test_adapter.py`.
It emits one complete JSON result to stdout without publication. The first
accepted original test ran in the temporary nonpublishing GitHub Actions QA
workflow and verified the complete four-response collection, source replay
and live-site consumer. See [the exact QA run](https://github.com/YouthOpps/data-pipeline/actions/runs/44180481985).
The first production attempt verifies that exact terminal QA job, immutable
source and disabled QA workflow, and its owned ZIP before restoring the
reviewed state.
It preserves the eight original wall-clock reservations and the eight
additional timing observations from the original research and GitHub QA
without counting those observations as additional publisher requests.
The hosted production workflow alone invokes `python -B adapters/es-uc3m-admissions/adapter.py --publish`
with the exact canonical job and state ownership checks. Its successful
atomic commit updates only this source's data and metadata, removes expired
records and retains creation and first-seen dates for unchanged IDs. On failure
it preserves the last-good dataset. Under durable state and response-close
authority, the workflow exports an inert original pacing receipt for the next
canonical attempt. A missing or ambiguous newest receipt blocks another access
attempt rather than selecting an older receipt or reseeding.
