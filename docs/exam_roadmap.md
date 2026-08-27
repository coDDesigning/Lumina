# Exam Roadmaps and Last-Minute Review

Status: accepted (SCRUM-169).

## Decision

`courses.exam_date` gets a planning consumer. A student with an exam date, a set
of declared topics, and a quiz history receives a day-by-day plan from today
through the exam: which topics to study on which day, what to do with each one,
and which of their own documents to study it from. A separate `last_minute`
summary mode produces the artifact that plan ends in — a review sheet, stored
and reopened as its own thing rather than as a study guide with a different
tone.

## What the roadmap is made of

Four modules, in order of dependence, none of which reaches past its own job:

| Module | Owns | Reads |
| --- | --- | --- |
| `services/exam_topic_ranking.py` | which topics matter most | nothing (pure) |
| `services/exam_schedule.py` | which day each topic lands on | nothing (pure) |
| `services/exam_roadmap.py` | the database and retrieval seam | course, mastery, material |
| `routes/exam_roadmap.py` | authorization, rate limit, error contract | — |

Ranking and allocation are pure functions. Every calendar boundary a student can
hit is therefore a table in `tests/test_exam_roadmap.py` rather than a branch
discovered in production.

## Ranking

Two signals, weighted equally.

**Importance** is whether the course declares the topic. A topic on the syllabus
is in scope by the owner's own statement and scores `1.0`; a topic that exists
only because a quiz question was tagged with it scores `0.6` — evidence of study,
not a declaration of scope. Syllabus *position* is deliberately not read as
importance. `course_topics.position` records the order topics are taught in, not
how heavily they are examined, and turning a sequence into a weighting would
invent a fact the student never supplied. Position is used for sequencing, which
is what it actually means.

**Weakness** is `(100 - mastery) / 100`, taken from the mastery the progress
aggregate already computes. A topic that has never been quizzed has no mastery,
which is not the same as a mastery of zero: it is ranked at a neutral `0.5`, so a
course with no attempt at all falls back to importance and syllabus order instead
of pretending every topic is failing.

`priority = 0.5 * importance + 0.5 * weakness`. Of two declared topics the weaker
ranks first; of two equally weak topics the declared one ranks first. A declared
topic the student has already mastered can fall behind an undeclared topic they
are failing, and that is the intended trade — ranking decides the order of
attack, not what gets dropped.

## Allocation

* The plan runs from today through the exam date inclusive, so an exam today is a
  one-day plan rather than an error.
* A horizon of one day or less is **triage**: every day carries the same short
  list of the highest-priority topics, because there is no time for a second pass
  to mean anything.
* Otherwise the exam day itself is a final review and the days before it are
  study days. **Coverage comes first**: every selected topic gets one pass, spread
  evenly, before any topic gets a second. That is what stops a weak topic from
  eating the whole plan and hiding a topic the syllabus declares.
* **Selection is by priority, sequencing is by syllabus position.** When the
  horizon cannot hold every topic, the ones it holds are the highest-priority
  ones, but they are still studied in the order the course teaches them, so a
  prerequisite is never scheduled after the topic that needs it.
* Days left over after coverage cycle the ranked order again, highest priority
  first, so the weakest and most important topics collect the most passes.
* A horizon longer than `MAX_PLAN_DAYS` (90) is capped: the plan starts later and
  says so. The days before it are reported rather than filled with invented work.
* Topics that do not fit the remaining days are returned as `deferred_topics`
  with a reason, never silently dropped.

| Horizon | `horizon` | Shape |
| --- | --- | --- |
| Exam today | `zero_day` | one `last_minute` day, which is also the exam day |
| Exam tomorrow | `one_day` | two `last_minute` days |
| 2–89 days | `standard` | study days, then a `final_review` on the exam day |
| 90+ days | `long` | the final 90 days, starting after a reported lead-in |
| Exam already passed | — | `409 exam_date_passed` |

## Materials and citations

Each distinct scheduled topic is resolved once against the course's own material
through `services/retrieval_material.py`, and the resulting citations are stored
denormalized on the topic exactly as a study guide stores them, so reopening a
roadmap resolves its sources with no provider call. `materials` collapses those
citations into the documents and page ranges the topic should be read from; like
a citation, it is never a link, so it survives the deletion of the document it
names.

Retrieval is enrichment here, not substance. A topic the material does not answer
is still scheduled and says why, because a plan that names the gap is better than
one that refuses to exist:

| `material_status` | Means |
| --- | --- |
| `resolved` | citations and documents are attached |
| `no_match` | the course has indexed material, but none of it clears the similarity floor for this topic |
| `not_indexed` | the course has ready chunks with no vectors — fix with `python -m workers.embedding_backfill` |
| `no_material` | the course has no processed material at all |
| `not_requested` | the request set `include_materials: false` |

An embedding or vector-store *failure* is different: it is transient and would
silently produce a material-less plan that looks permanent, so it fails the
request through the usual `X-Error-Code` contract.

## Persistence and versioning

A roadmap is stored through `GeneratedOutputService.record` under output type
`exam_roadmap`, like every other generated artifact. No new table, no new
migration.

No text-generation provider is called and no credit is charged: the schedule is
derived from the course's own topics, exam date, and quiz history. Two things
follow. `model_used` is stored as null, which is the truthful value for a row no
model produced rather than a gap waiting to be backfilled. And regeneration is
cheap enough to be the adaptation mechanism: `POST` again after new quiz results
and the new plan reflects the new mastery, carrying `roadmap_version` one higher
and `adapted_from_output_id` pointing at the plan it supersedes. Earlier versions
are never rewritten — a student can reopen the plan they were working from
yesterday. Version numbering is per student, like the history it belongs to.

## Errors

All three planning refusals are `409` with an `X-Error-Code` header, because each
one is a fact about the course the student can fix:

| Code | Cause |
| --- | --- |
| `exam_date_required` | the course has no exam date |
| `exam_date_passed` | the exam date is in the past |
| `exam_topics_required` | the course declares no topics and has no quiz history to infer them from |

## Last-minute review

`SummaryMode.LAST_MINUTE` reuses the study-guide pipeline unchanged: same
retrieval, same citations, same validation, same canonical persistence. It
differs in two places. Its retrieval query is widened with revision terms rather
than exam-preparation terms, and its directive asks for a review sheet — the
definitions, formulas, distinctions, and procedure steps a student must be able
to reproduce — rather than a lesson.

It is stored under its own output type, `last_minute_review`, because a student
asks for it separately, opens it separately, and should see it listed separately
from the guides they studied from. `schemas/study_guide.py::output_type_for` is
the single place a summary mode becomes an output type. A course whose
`CourseSettings.study_mode` is "Last Minute" gets review sheets without asking
for the mode on every request.

## Related

* [Citations](citations.md) — how a passage becomes a source reference.
* [Vector storage](vector_storage.md) — the retrieval this reads through.
* [Database](database.md) — `courses.exam_date`, `course_topics`, and
  `generated_outputs`.
