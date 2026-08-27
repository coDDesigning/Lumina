"""Contract for SCRUM-169: exam roadmaps and last-minute review sheets.

Three things are proved here. Ranking and allocation are pure, so every calendar
boundary a student can hit -- an exam today, an exam tomorrow, an exam a year
out -- is a table rather than a branch nobody exercised. Generation is a real
request against a real course, so the plan it stores names material that
resolves and reopens without touching a provider. And regeneration writes a new
version rather than editing the one the student already read.
"""

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import select

import routes.study_guide as study_guide_route
import services.study_guide as study_guide_service
from backend.app.models import (
    Course,
    CourseSettings,
    CourseTopic,
    DocumentChunk,
    GeneratedOutput,
    Quiz,
    QuizQuestion,
)
from schemas.exam_roadmap import (
    ExamRoadmap,
    RoadmapDayKind,
    RoadmapHorizon,
    TopicMaterialStatus,
    TopicSource,
)
from schemas.quiz_attempt import MasteryStatus, TopicMastery
from schemas.study_guide import (
    LAST_MINUTE_REVIEW_OUTPUT_TYPE,
    STUDY_GUIDE_OUTPUT_TYPE,
    StudyGuideRequest,
    StudyGuideResponse,
    SummaryFormat,
    SummaryMode,
)
from services.exam_roadmap import OUTPUT_TYPE
from services.exam_schedule import (
    FINAL_REVIEW_TOPIC_LIMIT,
    MAX_PLAN_DAYS,
    TRIAGE_TOPIC_LIMIT,
    build_schedule,
)
from services.exam_topic_ranking import rank_topics
from services.study_guide import StudyGuideService
from tests.conftest import directional_vector
from tests.generation_fixtures import (
    RecordingProvider,
    persisted_outputs,
    seed_ready_material,
    study_guide_payload,
)
from utils.ai_errors import ERROR_CODE_HEADER, AiErrorCode

TODAY = date(2026, 5, 1)

# cosine([1, 0], [1, s]) is 1/sqrt(1+s**2), so a seed of 4.0 scores 0.24 and
# falls under the default 0.25 relevance floor while 0.0 scores a perfect 1.00.
IRRELEVANT_SEED = 4.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mastery(topic: str, percentage: int, *, answered: int = 10) -> TopicMastery:
    """One entry shaped exactly like the progress aggregate produces."""
    return TopicMastery(
        topic=topic,
        questions_answered=answered,
        questions_correct=round(answered * percentage / 100),
        mastery_percentage=percentage,
        status=(
            MasteryStatus.MASTERED
            if percentage >= 80
            else MasteryStatus.NEEDS_REVIEW
            if percentage < 60
            else MasteryStatus.IN_PROGRESS
        ),
    )


def _ranked(topics: list[str], mastery: list[TopicMastery] | None = None):
    return rank_topics(syllabus_topics=topics, mastery=mastery or [])


def _schedule(
    topics: list[str],
    *,
    days_until_exam: int,
    mastery: list[TopicMastery] | None = None,
    max_topics_per_day: int = 3,
):
    return build_schedule(
        _ranked(topics, mastery),
        today=TODAY,
        exam_date=TODAY + timedelta(days=days_until_exam),
        max_topics_per_day=max_topics_per_day,
    )


def _plan_course(session_factory, course_id: int, *, exam_in_days, topics) -> None:
    """Give a course the exam date and declared topics a roadmap plans from."""
    with session_factory() as session:
        course = session.get(Course, course_id)
        assert course is not None
        course.exam_date = (
            date.today() + timedelta(days=exam_in_days)
            if exam_in_days is not None
            else None
        )
        course.topic_rows = [
            CourseTopic(course_id=course_id, position=position, name=name)
            for position, name in enumerate(topics)
        ]
        session.commit()


def _page_the_chunks(session_factory, course_id: int, pages: list[int]) -> None:
    """Give already-indexed chunks page numbers, so citations carry a page."""
    with session_factory() as session:
        chunks = session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.course_id == course_id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        for chunk, page in zip(chunks, pages, strict=True):
            chunk.page_number = page
            chunk.end_page_number = page
        session.commit()


def _route_queries_by_topic(
    retrieval_env, seeds: dict[str, float], *, default: float
) -> None:
    """Let one topic find material while another finds none.

    The stub embedder answers every query with one vector, which cannot express
    "this course covers graphs but not scheduling". Keying the vector to the
    query text can, and for a roadmap the query text is the topic.
    """
    provider = retrieval_env.provider

    def embed_query(text: str) -> list[float]:
        provider.embed_query_calls.append(text)
        for needle, seed in seeds.items():
            if needle.casefold() in text.casefold():
                return directional_vector(seed)
        return directional_vector(default)

    provider.embed_query = embed_query


def _generate(api, course_id: int, **body):
    return api.client.post(
        f"/api/courses/{course_id}/exam-roadmap",
        json=body or None,
        headers=api.authorization,
    )


def _roadmap_of(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()["data"]["roadmap"]


def _scheduled_topics(roadmap: dict) -> list[dict]:
    return [topic for day in roadmap["days"] for topic in day["topics"]]


def _stored_roadmaps(session_factory, course_id: int):
    with session_factory() as session:
        return session.scalars(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE,
            )
            .order_by(GeneratedOutput.id)
        ).all()


def _answer_a_quiz_badly(api, course_id: int, topic: str) -> None:
    """Record a real attempt that fails every question tagged with ``topic``."""
    with api.session_factory() as session:
        quiz = Quiz(course_id=course_id, user_id=api.user_id, title="Checkpoint")
        session.add(quiz)
        session.flush()
        session.add_all(
            [
                QuizQuestion(
                    quiz_id=quiz.id,
                    question_index=index,
                    question_text=f"{topic} question {index}?",
                    options=["Option A", "Option B", "Option C", "Option D"],
                    correct_option_index=0,
                    topic=topic,
                    explanation="Option A is correct.",
                )
                for index in range(2)
            ]
        )
        session.commit()
        quiz_id = quiz.id
        question_ids = [
            row.id
            for row in session.scalars(
                select(QuizQuestion)
                .where(QuizQuestion.quiz_id == quiz_id)
                .order_by(QuizQuestion.question_index)
            ).all()
        ]

    submitted = api.client.post(
        f"/api/courses/{course_id}/quizzes/{quiz_id}/attempts",
        json={
            "answers": [
                {"question_id": question_id, "selected_option_index": 2}
                for question_id in question_ids
            ]
        },
        headers=api.authorization,
    )
    assert submitted.status_code in (200, 201), submitted.text


# ---------------------------------------------------------------------------
# Ranking: the plan a schedule consumes
# ---------------------------------------------------------------------------


def test_an_unquizzed_topic_ranks_between_a_weak_and_a_mastered_one() -> None:
    """Never quizzed is not the same fact as quizzed and failed."""
    ranked = _ranked(
        ["Weak", "Unquizzed", "Mastered"],
        [_mastery("Weak", 20), _mastery("Mastered", 100)],
    )

    assert [topic.topic for topic in ranked] == ["Weak", "Unquizzed", "Mastered"]
    assert [topic.priority for topic in ranked] == [0.9, 0.75, 0.5]


def test_a_weaker_declared_topic_outranks_a_stronger_one() -> None:
    ranked = _ranked(
        ["Sorting", "Graphs"],
        [_mastery("Sorting", 90), _mastery("Graphs", 30)],
    )

    assert [topic.topic for topic in ranked] == ["Graphs", "Sorting"]


def test_a_declared_topic_outranks_an_undeclared_one_of_the_same_weakness() -> None:
    """Equal evidence of weakness, unequal evidence of scope."""
    ranked = _ranked(
        ["Declared"],
        [_mastery("Declared", 40), _mastery("Undeclared", 40)],
    )

    declared, undeclared = ranked
    assert declared.topic == "Declared"
    assert declared.source is TopicSource.SYLLABUS
    assert undeclared.topic == "Undeclared"
    assert undeclared.source is TopicSource.QUIZ
    assert declared.priority > undeclared.priority


def test_a_mastered_declared_topic_yields_to_an_undeclared_one_being_failed() -> None:
    """The intended trade: ranking decides order of attack, not what is dropped.

    Nothing is hidden by it. The schedule gives every selected topic a pass
    before any topic gets a second one, so only a horizon too short to hold the
    course at all drops anything.
    """
    ranked = _ranked(
        ["Mastered"],
        [_mastery("Mastered", 100), _mastery("Failing", 0)],
    )

    assert [topic.topic for topic in ranked] == ["Failing", "Mastered"]


def test_ranking_without_any_attempt_falls_back_to_syllabus_order() -> None:
    ranked = _ranked(["Third", "First", "Second"])

    assert [topic.topic for topic in ranked] == ["Third", "First", "Second"]
    assert [topic.syllabus_position for topic in ranked] == [0, 1, 2]
    assert all(topic.mastery_percentage is None for topic in ranked)


def test_mastery_binds_to_a_declared_topic_whatever_its_casing_or_spacing() -> None:
    ranked = _ranked(["Dynamic  Programming"], [_mastery("dynamic programming", 10)])

    assert len(ranked) == 1
    assert ranked[0].source is TopicSource.SYLLABUS
    assert ranked[0].mastery_percentage == 10


def test_blank_and_duplicate_declared_topics_are_dropped() -> None:
    ranked = _ranked(["Graphs", "  ", "graphs", "Trees"])

    assert [topic.topic for topic in ranked] == ["Graphs", "Trees"]


def test_a_topic_known_only_from_a_quiz_is_still_planned() -> None:
    """A student who has been quizzed on something is studying it."""
    ranked = _ranked([], [_mastery("Recurrences", 25)])

    assert [topic.topic for topic in ranked] == ["Recurrences"]
    assert ranked[0].syllabus_position is None
    assert ranked[0].source is TopicSource.QUIZ


# ---------------------------------------------------------------------------
# Schedule allocation
# ---------------------------------------------------------------------------


def test_the_plan_covers_every_day_from_today_through_the_exam() -> None:
    schedule = _schedule(["A", "B", "C", "D"], days_until_exam=4)

    assert [day.date for day in schedule.days] == [
        TODAY + timedelta(days=offset) for offset in range(5)
    ]
    assert schedule.horizon is RoadmapHorizon.STANDARD
    assert schedule.days_until_exam == 4
    assert schedule.lead_in_days == 0


def test_the_exam_day_is_the_only_final_review() -> None:
    schedule = _schedule(["A", "B", "C", "D"], days_until_exam=4)

    exam_days = [day for day in schedule.days if day.is_exam_day]
    assert len(exam_days) == 1
    assert exam_days[0] is schedule.days[-1]
    assert exam_days[0].kind is RoadmapDayKind.FINAL_REVIEW
    assert exam_days[0].date == TODAY + timedelta(days=4)
    assert len(exam_days[0].topics) <= FINAL_REVIEW_TOPIC_LIMIT


def test_every_scheduled_topic_carries_a_goal_naming_it() -> None:
    schedule = _schedule(
        ["Graphs", "Sorting", "Trees"],
        days_until_exam=5,
        mastery=[_mastery("Graphs", 20), _mastery("Sorting", 95)],
    )

    for day in schedule.days:
        assert day.focus.strip()
        for scheduled in day.topics:
            assert scheduled.goal.strip()
            assert scheduled.topic.topic in scheduled.goal


def test_a_weak_topic_and_a_mastered_topic_get_different_first_pass_goals() -> None:
    schedule = _schedule(
        ["Graphs", "Sorting"],
        days_until_exam=3,
        mastery=[_mastery("Graphs", 20), _mastery("Sorting", 95)],
        max_topics_per_day=1,
    )

    goals = {
        scheduled.topic.topic: scheduled.goal
        for day in schedule.days
        for scheduled in day.topics
        if scheduled.pass_number == 1
    }
    assert "20%" in goals["Graphs"]
    assert "95%" in goals["Sorting"]
    assert goals["Graphs"] != goals["Sorting"]


def test_every_topic_gets_a_first_pass_before_any_topic_gets_a_second() -> None:
    """Coverage before repetition is what stops a weak topic eating the plan."""
    schedule = _schedule(
        ["A", "B", "C", "D", "E", "F"],
        days_until_exam=8,
        mastery=[_mastery("A", 0)],
    )

    seen_repeat = False
    covered: set[str] = set()
    for day in schedule.days:
        for scheduled in day.topics:
            if scheduled.pass_number == 1:
                assert not seen_repeat, (
                    f"{scheduled.topic.topic} got its first pass after a repeat"
                )
                covered.add(scheduled.topic.topic)
            else:
                seen_repeat = True

    assert covered == {"A", "B", "C", "D", "E", "F"}


def test_first_passes_follow_syllabus_order_even_when_priority_does_not() -> None:
    """Selection is by priority; sequencing is by the order the course teaches in.

    A prerequisite is therefore never scheduled after the topic that needs it,
    however weak the later topic is.
    """
    mastery = [_mastery("Dependent", 5), _mastery("Prerequisite", 70)]
    assert _ranked(["Prerequisite", "Dependent"], mastery)[0].topic == "Dependent"

    schedule = _schedule(
        ["Prerequisite", "Dependent"],
        days_until_exam=3,
        mastery=mastery,
        max_topics_per_day=1,
    )

    first_passes = [
        scheduled.topic.topic
        for day in schedule.days
        for scheduled in day.topics
        if scheduled.pass_number == 1
    ]
    assert first_passes == ["Prerequisite", "Dependent"]


def test_no_study_day_exceeds_the_requested_topics_per_day() -> None:
    schedule = _schedule([f"Topic {index}" for index in range(20)], days_until_exam=9)

    study_days = [day for day in schedule.days if not day.is_exam_day]
    assert study_days
    assert all(len(day.topics) <= 3 for day in study_days)


def test_a_horizon_too_short_for_every_topic_defers_the_lowest_priority_ones() -> None:
    schedule = _schedule(
        [f"Topic {index}" for index in range(10)],
        days_until_exam=3,
        mastery=[_mastery("Topic 9", 0)],
        max_topics_per_day=1,
    )

    scheduled = {
        scheduled.topic.topic for day in schedule.days for scheduled in day.topics
    }
    deferred = [topic.topic for topic in schedule.deferred]

    assert len(scheduled) == 3
    assert "Topic 9" in scheduled, "the weakest topic must survive the cut"
    assert set(deferred) & scheduled == set()
    assert len(deferred) == 7
    assert any("did not fit" in note for note in schedule.notes)


def test_a_horizon_wide_enough_defers_nothing_and_says_nothing() -> None:
    schedule = _schedule(["A", "B", "C"], days_until_exam=6)

    assert schedule.deferred == ()
    assert schedule.notes == ()


# ---------------------------------------------------------------------------
# Date boundaries
# ---------------------------------------------------------------------------


def test_an_exam_today_produces_a_single_last_minute_day() -> None:
    schedule = _schedule(["A", "B"], days_until_exam=0)

    assert schedule.horizon is RoadmapHorizon.ZERO_DAY
    assert len(schedule.days) == 1
    assert schedule.days[0].date == TODAY
    assert schedule.days[0].kind is RoadmapDayKind.LAST_MINUTE
    assert schedule.days[0].is_exam_day is True
    assert [item.topic.topic for item in schedule.days[0].topics] == ["A", "B"]


def test_an_exam_tomorrow_produces_two_last_minute_days() -> None:
    schedule = _schedule(["A", "B"], days_until_exam=1)

    assert schedule.horizon is RoadmapHorizon.ONE_DAY
    assert [day.kind for day in schedule.days] == [
        RoadmapDayKind.LAST_MINUTE,
        RoadmapDayKind.LAST_MINUTE,
    ]
    assert [day.is_exam_day for day in schedule.days] == [False, True]


def test_a_triage_horizon_carries_only_a_readable_shortlist() -> None:
    schedule = _schedule([f"Topic {index}" for index in range(12)], days_until_exam=1)

    assert all(len(day.topics) == TRIAGE_TOPIC_LIMIT for day in schedule.days)
    assert len(schedule.deferred) == 12 - TRIAGE_TOPIC_LIMIT


def test_a_long_horizon_is_capped_and_starts_later() -> None:
    schedule = _schedule(["A", "B", "C"], days_until_exam=365)

    assert schedule.horizon is RoadmapHorizon.LONG
    assert len(schedule.days) == MAX_PLAN_DAYS
    assert schedule.lead_in_days == 366 - MAX_PLAN_DAYS
    assert schedule.starts_on == TODAY + timedelta(days=schedule.lead_in_days)
    assert schedule.days[-1].date == TODAY + timedelta(days=365)
    assert any("starts on" in note for note in schedule.notes)


def test_the_longest_uncapped_horizon_still_starts_today() -> None:
    schedule = _schedule(["A"], days_until_exam=MAX_PLAN_DAYS - 1)

    assert schedule.horizon is RoadmapHorizon.STANDARD
    assert schedule.lead_in_days == 0
    assert schedule.starts_on == TODAY
    assert len(schedule.days) == MAX_PLAN_DAYS


def test_a_past_exam_date_is_refused_rather_than_planned() -> None:
    with pytest.raises(ValueError):
        build_schedule(_ranked(["A"]), today=TODAY, exam_date=TODAY - timedelta(days=1))


def test_a_plan_needs_at_least_one_topic() -> None:
    with pytest.raises(ValueError):
        build_schedule([], today=TODAY, exam_date=TODAY + timedelta(days=3))


# ---------------------------------------------------------------------------
# Generation: a real plan for a real course
# ---------------------------------------------------------------------------


def test_a_roadmap_plans_every_day_and_stores_itself(upload_api, retrieval_env) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=5,
        topics=["Graphs", "Sorting", "Trees"],
    )
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Graph traversal lecture", "Sorting lecture"],
            file_hash="a1" + "0" * 62,
            retrieval_env=retrieval_env,
        )

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    today = date.today()
    assert roadmap["generated_on"] == today.isoformat()
    assert roadmap["days_until_exam"] == 5
    assert len(roadmap["days"]) == 6
    assert [day["date"] for day in roadmap["days"]] == [
        (today + timedelta(days=offset)).isoformat() for offset in range(6)
    ]
    assert roadmap["days"][-1]["is_exam_day"] is True
    assert roadmap["horizon"] == RoadmapHorizon.STANDARD.value
    assert roadmap["roadmap_version"] == 1
    assert roadmap["adapted_from_output_id"] is None
    assert [topic["topic"] for topic in roadmap["ranked_topics"]] == [
        "Graphs",
        "Sorting",
        "Trees",
    ]

    scheduled = _scheduled_topics(roadmap)
    assert scheduled
    assert all(topic["goal"].strip() for topic in scheduled)
    assert all(topic["pass_number"] >= 1 for topic in scheduled)


def test_a_stored_roadmap_records_no_model_and_its_own_output_type(
    upload_api,
) -> None:
    """No provider wrote it, so ``model_used`` is null rather than a placeholder."""
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=4,
        topics=["Graphs"],
    )

    response = _generate(upload_api, upload_api.course_id)
    output_id = response.json()["data"]["generated_output_id"]

    stored = _stored_roadmaps(upload_api.session_factory, upload_api.course_id)
    assert [row.id for row in stored] == [output_id]
    assert stored[0].output_type == OUTPUT_TYPE
    assert stored[0].model_used is None
    assert stored[0].user_id == upload_api.user_id

    detail = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/generated-outputs/{output_id}",
        headers=upload_api.authorization,
    )
    assert detail.status_code == 200, detail.text
    settings_document = detail.json()["data"]["generation_settings"]
    context_document = detail.json()["data"]["generation_context"]
    assert settings_document["output_type"] == OUTPUT_TYPE
    assert settings_document["max_topics_per_day"] == 3
    assert settings_document["roadmap_version"] == 1
    assert context_document["topics_ranked"] == 1
    assert context_document["scheduled_days"] == 5


def test_reopening_a_roadmap_reaches_no_provider(upload_api, retrieval_env) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=3,
        topics=["Graphs", "Sorting"],
    )
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Graph traversal lecture"],
            file_hash="a2" + "0" * 62,
            retrieval_env=retrieval_env,
        )

    generated = _generate(upload_api, upload_api.course_id)
    roadmap = _roadmap_of(generated)
    output_id = generated.json()["data"]["generated_output_id"]
    embeddings_after_generation = len(retrieval_env.provider.embed_query_calls)

    reopened = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/generated-outputs/{output_id}",
        headers=upload_api.authorization,
    )

    assert reopened.status_code == 200, reopened.text
    content = reopened.json()["data"]["content"]
    assert ExamRoadmap.model_validate(content) == ExamRoadmap.model_validate(roadmap)
    assert len(retrieval_env.provider.embed_query_calls) == embeddings_after_generation


def test_every_scheduled_topic_names_material_it_can_be_studied_from(
    upload_api, retrieval_env
) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=4,
        topics=["Graphs", "Sorting"],
    )
    with upload_api.session_factory() as session:
        document = seed_ready_material(
            session,
            upload_api.course_id,
            ["Graph traversal lecture", "Sorting lecture"],
            file_hash="a3" + "0" * 62,
            retrieval_env=retrieval_env,
        )
        document_id = str(document.id)
    _page_the_chunks(upload_api.session_factory, upload_api.course_id, [7, 8])

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert roadmap["materials_available"] is True
    for topic in _scheduled_topics(roadmap):
        assert topic["material_status"] == TopicMaterialStatus.RESOLVED.value
        assert topic["citations"], topic["topic"]
        assert all(
            citation["document_id"] == document_id for citation in topic["citations"]
        )
        assert all(citation["document_label"] for citation in topic["citations"])
        assert topic["materials"] == [
            {
                "document_id": document_id,
                "document_label": topic["citations"][0]["document_label"],
                "page_start": 7,
                "page_end": 8,
            }
        ]


def test_a_topic_the_material_never_covers_is_still_scheduled(
    upload_api, retrieval_env
) -> None:
    """A plan that names the gap beats a plan that refuses to exist."""
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=4,
        topics=["Graphs", "Scheduling"],
    )
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Graph traversal lecture"],
            file_hash="a4" + "0" * 62,
            retrieval_env=retrieval_env,
        )
    _route_queries_by_topic(retrieval_env, {"Graphs": 0.0}, default=IRRELEVANT_SEED)

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    scheduled = _scheduled_topics(roadmap)
    statuses = {topic["topic"]: topic["material_status"] for topic in scheduled}
    assert statuses["Graphs"] == TopicMaterialStatus.RESOLVED.value
    assert statuses["Scheduling"] == TopicMaterialStatus.NO_MATCH.value

    unmatched = [topic for topic in scheduled if topic["topic"] == "Scheduling"]
    assert unmatched
    assert all(topic["citations"] == [] for topic in unmatched)
    assert all(topic["goal"].strip() for topic in unmatched)


def test_a_course_with_no_material_still_gets_a_plan(upload_api, retrieval_env) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=3,
        topics=["Graphs"],
    )

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert roadmap["materials_available"] is False
    assert len(roadmap["days"]) == 4
    assert all(
        topic["material_status"] == TopicMaterialStatus.NO_MATERIAL.value
        for topic in _scheduled_topics(roadmap)
    )
    assert any("no processed material" in note for note in roadmap["notes"])
    assert retrieval_env.provider.embed_query_calls == []


def test_material_that_was_never_indexed_is_reported_as_an_indexing_gap(
    upload_api, retrieval_env
) -> None:
    """A relevance miss and an indexing gap need different remedies."""
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=3,
        topics=["Graphs"],
    )
    with upload_api.session_factory() as session:
        document = seed_ready_material(
            session,
            upload_api.course_id,
            ["Graph traversal lecture"],
            file_hash="a5" + "0" * 62,
            retrieval_env=retrieval_env,
        )
        retrieval_env.store.delete_document_vectors(session, document.id)
        session.commit()

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert roadmap["materials_available"] is True
    assert all(
        topic["material_status"] == TopicMaterialStatus.NOT_INDEXED.value
        for topic in _scheduled_topics(roadmap)
    )


def test_a_plan_can_be_asked_for_without_materials_at_all(
    upload_api, retrieval_env
) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=3,
        topics=["Graphs"],
    )
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Graph traversal lecture"],
            file_hash="a6" + "0" * 62,
            retrieval_env=retrieval_env,
        )

    roadmap = _roadmap_of(
        _generate(upload_api, upload_api.course_id, include_materials=False)
    )

    assert retrieval_env.provider.embed_query_calls == []
    assert all(
        topic["material_status"] == TopicMaterialStatus.NOT_REQUESTED.value
        for topic in _scheduled_topics(roadmap)
    )


def test_the_requested_topics_per_day_is_honoured_and_recorded(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=4,
        topics=[f"Topic {index}" for index in range(8)],
    )

    response = _generate(upload_api, upload_api.course_id, max_topics_per_day=2)
    roadmap = _roadmap_of(response)

    study_days = [day for day in roadmap["days"] if not day["is_exam_day"]]
    assert all(len(day["topics"]) <= 2 for day in study_days)

    output_id = response.json()["data"]["generated_output_id"]
    detail = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/generated-outputs/{output_id}",
        headers=upload_api.authorization,
    )
    assert detail.json()["data"]["generation_settings"]["max_topics_per_day"] == 2


def test_an_out_of_range_topics_per_day_is_rejected(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=4,
        topics=["Graphs"],
    )

    response = _generate(upload_api, upload_api.course_id, max_topics_per_day=99)

    assert response.status_code == 422, response.text
    assert _stored_roadmaps(upload_api.session_factory, upload_api.course_id) == []


# ---------------------------------------------------------------------------
# Date boundaries, as a student meets them
# ---------------------------------------------------------------------------


def test_an_exam_tomorrow_is_planned_rather_than_refused(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=1,
        topics=["Graphs", "Sorting"],
    )

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert roadmap["horizon"] == RoadmapHorizon.ONE_DAY.value
    assert len(roadmap["days"]) == 2
    assert all(
        day["kind"] == RoadmapDayKind.LAST_MINUTE.value for day in roadmap["days"]
    )
    assert _scheduled_topics(roadmap)


def test_an_exam_today_is_planned_rather_than_refused(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=0,
        topics=["Graphs"],
    )

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert roadmap["horizon"] == RoadmapHorizon.ZERO_DAY.value
    assert roadmap["days_until_exam"] == 0
    assert len(roadmap["days"]) == 1
    assert roadmap["days"][0]["is_exam_day"] is True


def test_a_long_horizon_is_capped_and_reported_to_the_student(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=200,
        topics=["Graphs", "Sorting"],
    )

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert roadmap["horizon"] == RoadmapHorizon.LONG.value
    assert roadmap["scheduled_days"] == MAX_PLAN_DAYS
    assert roadmap["lead_in_days"] == 201 - MAX_PLAN_DAYS
    assert roadmap["starts_on"] != roadmap["generated_on"]
    assert roadmap["notes"]


def test_a_course_without_an_exam_date_says_so(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=None,
        topics=["Graphs"],
    )

    response = _generate(upload_api, upload_api.course_id)

    assert response.status_code == 409, response.text
    assert response.headers[ERROR_CODE_HEADER] == AiErrorCode.EXAM_DATE_REQUIRED.value
    assert _stored_roadmaps(upload_api.session_factory, upload_api.course_id) == []


def test_an_exam_date_that_has_passed_says_so(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=-1,
        topics=["Graphs"],
    )

    response = _generate(upload_api, upload_api.course_id)

    assert response.status_code == 409, response.text
    assert response.headers[ERROR_CODE_HEADER] == AiErrorCode.EXAM_DATE_PASSED.value
    assert _stored_roadmaps(upload_api.session_factory, upload_api.course_id) == []


def test_a_course_with_nothing_to_plan_says_so(upload_api) -> None:
    """No declared topics and no attempt is a course to fill in, not an error."""
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=5,
        topics=[],
    )

    response = _generate(upload_api, upload_api.course_id)

    assert response.status_code == 409, response.text
    assert response.headers[ERROR_CODE_HEADER] == AiErrorCode.EXAM_TOPICS_REQUIRED.value
    assert _stored_roadmaps(upload_api.session_factory, upload_api.course_id) == []


def test_a_course_with_no_declared_topics_can_still_plan_from_quiz_results(
    upload_api,
) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=5,
        topics=[],
    )
    _answer_a_quiz_badly(upload_api, upload_api.course_id, "Recurrences")

    roadmap = _roadmap_of(_generate(upload_api, upload_api.course_id))

    assert [topic["topic"] for topic in roadmap["ranked_topics"]] == ["Recurrences"]
    assert roadmap["attempts_considered"] == 1
    assert all(
        topic["source"] == TopicSource.QUIZ.value
        for topic in _scheduled_topics(roadmap)
    )


# ---------------------------------------------------------------------------
# Regeneration: adapt forward, never rewrite backwards
# ---------------------------------------------------------------------------


def test_regenerating_after_new_results_updates_mastery_and_keeps_the_old_plan(
    upload_api,
) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=6,
        topics=["Graphs", "Sorting"],
    )

    first = _generate(upload_api, upload_api.course_id)
    first_roadmap = _roadmap_of(first)
    first_id = first.json()["data"]["generated_output_id"]
    assert all(
        topic["mastery_percentage"] is None for topic in first_roadmap["ranked_topics"]
    )
    with upload_api.session_factory() as session:
        stored_first = session.get(GeneratedOutput, first_id)
        assert stored_first is not None
        content_before = stored_first.content

    _answer_a_quiz_badly(upload_api, upload_api.course_id, "Sorting")

    second = _generate(upload_api, upload_api.course_id)
    second_roadmap = _roadmap_of(second)
    second_id = second.json()["data"]["generated_output_id"]

    mastery = {
        topic["topic"]: topic["mastery_percentage"]
        for topic in second_roadmap["ranked_topics"]
    }
    assert mastery == {"Sorting": 0, "Graphs": None}
    assert second_roadmap["ranked_topics"][0]["topic"] == "Sorting"
    assert second_roadmap["attempts_considered"] == 1
    assert second_roadmap["roadmap_version"] == 2
    assert second_roadmap["adapted_from_output_id"] == first_id

    with upload_api.session_factory() as session:
        assert session.get(GeneratedOutput, first_id).content == content_before

    stored = _stored_roadmaps(upload_api.session_factory, upload_api.course_id)
    assert [row.id for row in stored] == [first_id, second_id]


def test_each_regeneration_is_a_new_version_of_its_own(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.course_id,
        exam_in_days=4,
        topics=["Graphs"],
    )

    versions = [
        _roadmap_of(_generate(upload_api, upload_api.course_id))["roadmap_version"]
        for _ in range(3)
    ]

    assert versions == [1, 2, 3]
    assert len(_stored_roadmaps(upload_api.session_factory, upload_api.course_id)) == 3


def test_another_students_roadmaps_are_not_counted_as_versions(authz_api) -> None:
    """Version numbering is per student, like the history it belongs to."""
    _plan_course(
        authz_api.session_factory,
        authz_api.a_course_id,
        exam_in_days=4,
        topics=["Graphs"],
    )

    for _ in range(2):
        owner = authz_api.client.post(
            f"/api/courses/{authz_api.a_course_id}/exam-roadmap",
            headers=authz_api.authorization_a,
        )
        assert owner.status_code == 200, owner.text

    _plan_course(
        authz_api.session_factory,
        authz_api.b_course_id,
        exam_in_days=4,
        topics=["Graphs"],
    )
    other = authz_api.client.post(
        f"/api/courses/{authz_api.b_course_id}/exam-roadmap",
        headers=authz_api.authorization_b,
    )

    assert other.status_code == 200, other.text
    assert other.json()["data"]["roadmap"]["roadmap_version"] == 1
    assert other.json()["data"]["roadmap"]["adapted_from_output_id"] is None


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_another_students_course_cannot_be_planned(authz_api) -> None:
    _plan_course(
        authz_api.session_factory,
        authz_api.a_course_id,
        exam_in_days=5,
        topics=["Graphs"],
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-roadmap",
        headers=authz_api.authorization_b,
    )

    assert response.status_code == 404, response.text
    assert _stored_roadmaps(authz_api.session_factory, authz_api.a_course_id) == []


def test_an_administrator_cannot_plan_someone_elses_course(authz_api) -> None:
    """Administrators read a course; writing one remains the owner's alone."""
    _plan_course(
        authz_api.session_factory,
        authz_api.a_course_id,
        exam_in_days=5,
        topics=["Graphs"],
    )

    response = authz_api.client.post(
        f"/api/courses/{authz_api.a_course_id}/exam-roadmap",
        headers=authz_api.authorization_admin,
    )

    assert response.status_code == 404, response.text
    assert _stored_roadmaps(authz_api.session_factory, authz_api.a_course_id) == []


def test_a_deleted_course_cannot_be_planned(upload_api) -> None:
    _plan_course(
        upload_api.session_factory,
        upload_api.deleted_course_id,
        exam_in_days=5,
        topics=["Graphs"],
    )

    response = _generate(upload_api, upload_api.deleted_course_id)

    assert response.status_code == 404, response.text
    assert (
        _stored_roadmaps(upload_api.session_factory, upload_api.deleted_course_id) == []
    )


def test_planning_requires_authentication(upload_api) -> None:
    response = upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/exam-roadmap"
    )

    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# The last-minute review sheet
# ---------------------------------------------------------------------------


def _install_study_guide_provider(monkeypatch) -> RecordingProvider:
    provider = RecordingProvider(study_guide_payload())
    monkeypatch.setattr(
        study_guide_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: provider,
    )
    return provider


def _request_study_guide(upload_api, **body):
    return upload_api.client.post(
        f"/api/courses/{upload_api.course_id}/study-guide",
        json={"summary_format": "comprehensive", "topic_focus": "All Topics", **body},
        headers=upload_api.authorization,
    )


def test_the_last_minute_query_asks_for_revision_material(model_graph) -> None:
    general = StudyGuideService.build_retrieval_query(
        model_graph.course,
        StudyGuideRequest(
            summary_format=SummaryFormat.COMPREHENSIVE,
            topic_focus="Graphs",
            summary_mode=SummaryMode.GENERAL,
        ),
    )
    last_minute = StudyGuideService.build_retrieval_query(
        model_graph.course,
        StudyGuideRequest(
            summary_format=SummaryFormat.COMPREHENSIVE,
            topic_focus="Graphs",
            summary_mode=SummaryMode.LAST_MINUTE,
        ),
    )

    assert general == "Graphs"
    assert last_minute.startswith("Graphs ")
    assert study_guide_service.LAST_MINUTE_QUERY_TERMS in last_minute
    assert study_guide_service.EXAM_FOCUS_QUERY_TERMS not in last_minute


def test_a_last_minute_review_persists_as_its_own_artifact(
    upload_api, retrieval_env, monkeypatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Revision material for the final hours"],
            file_hash="b1" + "0" * 62,
            retrieval_env=retrieval_env,
        )
    provider = _install_study_guide_provider(monkeypatch)

    response = _request_study_guide(upload_api, summary_mode="last_minute")

    assert response.status_code == 200, response.text
    assert provider.calls == 1
    assert "Requested summary mode: last_minute." in provider.prompt
    assert "review sheet" in provider.prompt

    stored = persisted_outputs(
        upload_api.session_factory, upload_api.course_id, LAST_MINUTE_REVIEW_OUTPUT_TYPE
    )
    assert len(stored) == 1
    settings_document = json.loads(stored[0].generation_settings)
    assert settings_document["summary_mode"] == "last_minute"
    assert settings_document["output_type"] == LAST_MINUTE_REVIEW_OUTPUT_TYPE
    assert (
        persisted_outputs(
            upload_api.session_factory, upload_api.course_id, STUDY_GUIDE_OUTPUT_TYPE
        )
        == []
    )


def test_a_review_sheet_and_a_study_guide_are_kept_apart(
    upload_api, retrieval_env, monkeypatch
) -> None:
    """Same pipeline, same citations, two artifacts a student opens separately."""
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Revision material for the final hours"],
            file_hash="b2" + "0" * 62,
            retrieval_env=retrieval_env,
        )
    _install_study_guide_provider(monkeypatch)

    assert _request_study_guide(upload_api).status_code == 200
    assert (
        _request_study_guide(upload_api, summary_mode="last_minute").status_code == 200
    )

    guides = persisted_outputs(
        upload_api.session_factory, upload_api.course_id, STUDY_GUIDE_OUTPUT_TYPE
    )
    reviews = persisted_outputs(
        upload_api.session_factory, upload_api.course_id, LAST_MINUTE_REVIEW_OUTPUT_TYPE
    )

    assert len(guides) == 1
    assert len(reviews) == 1
    assert guides[0].id != reviews[0].id


def test_a_review_sheet_reopens_without_a_provider_call(
    upload_api, retrieval_env, monkeypatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Revision material for the final hours"],
            file_hash="b3" + "0" * 62,
            retrieval_env=retrieval_env,
        )
    provider = _install_study_guide_provider(monkeypatch)

    generated = _request_study_guide(upload_api, summary_mode="last_minute")
    output_id = generated.json()["data"]["generated_output_id"]

    reopened = upload_api.client.get(
        f"/api/courses/{upload_api.course_id}/generated-outputs/{output_id}",
        headers=upload_api.authorization,
    )

    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["data"]["output_type"] == LAST_MINUTE_REVIEW_OUTPUT_TYPE
    assert StudyGuideResponse.model_validate(reopened.json()["data"]["content"])
    assert provider.calls == 1


def test_course_settings_can_ask_for_last_minute_review_sheets(
    upload_api, retrieval_env, monkeypatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Revision material for the final hours"],
            file_hash="b4" + "0" * 62,
            retrieval_env=retrieval_env,
        )
        session.add(
            CourseSettings(course_id=upload_api.course_id, study_mode="Last Minute")
        )
        session.commit()
    provider = _install_study_guide_provider(monkeypatch)

    response = _request_study_guide(upload_api)

    assert response.status_code == 200, response.text
    assert "Requested summary mode: last_minute." in provider.prompt
    assert (
        len(
            persisted_outputs(
                upload_api.session_factory,
                upload_api.course_id,
                LAST_MINUTE_REVIEW_OUTPUT_TYPE,
            )
        )
        == 1
    )


def test_a_review_sheet_carries_the_same_citations_as_a_study_guide(
    upload_api, retrieval_env, monkeypatch
) -> None:
    with upload_api.session_factory() as session:
        seed_ready_material(
            session,
            upload_api.course_id,
            ["Revision material for the final hours"],
            file_hash="b5" + "0" * 62,
            retrieval_env=retrieval_env,
        )
    provider = RecordingProvider(
        {
            **study_guide_payload(),
            "summary": {"text": "Recall the definitions first.", "citations": ["S1"]},
        }
    )
    monkeypatch.setattr(
        study_guide_route,
        "get_text_generation_provider",
        lambda *args, **kwargs: provider,
    )

    response = _request_study_guide(upload_api, summary_mode="last_minute")

    assert response.status_code == 200, response.text
    citations = response.json()["data"]["study_guide"]["summary"]["citations"]
    assert [citation["key"] for citation in citations] == ["S1"]
    assert citations[0]["document_label"]
