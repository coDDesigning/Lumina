"""Every Exam Mode prompt states one rule, and the rule its caller chose.

Two defect classes are pinned here, both of which reached production as prose
in a template body that contradicted a value the service substitutes. A prompt
that states two quantities, or names a type the flow cannot store, does not
degrade gracefully: the response is refused whole and the student has paid for
a generation that was never going to validate.
"""

import services.exam_course_artifacts as exam_course_artifacts
import services.exam_quiz as exam_quiz
import services.exam_similar_questions as exam_similar_questions
from schemas.exam_mode import SimilarQuestionDifficultyPolicy
from schemas.prompt_context import EducationLevel, MaterialKind, PromptContext
from schemas.quiz import QuizQuestionType
from services.exam_artifacts import PlannedExam, PlannedTopic
from services.prompt_loader import PromptLoader

CONTEXT = PromptContext(
    education_level=EducationLevel.UNDERGRADUATE,
    course_title="Algorithms",
    subject_area="Computer Science",
    material_kind=MaterialKind.LECTURE_NOTES,
)

TOPIC = PlannedTopic(
    plan_output_id=1,
    analysis_output_id=1,
    topic_key="graph-traversal",
    display_label="Graph Traversal",
    rank=1,
    priority_band="high",
    is_high_priority=False,
    mastery_percentage=None,
    document_ids=(),
)

PLAN = PlannedExam(
    plan_output_id=1,
    analysis_output_id=1,
    exam_date=None,
    days_until_exam=None,
    topics=(TOPIC,),
    document_ids=(),
)

ALL_TYPES = tuple(QuizQuestionType)

OPEN_ENDED_NOTE = 'use "open_ended" for it'


# --------------------------------------------------------------- type vocabulary


def _topic_quiz_prompt(kind: exam_quiz.ExamQuizKind) -> str:
    style = "Past question style" if kind.hide_answers else None
    return exam_quiz._build_prompt(
        kind, "material", TOPIC, CONTEXT, question_count=5, style=style
    )


def test_a_topic_quiz_prompt_names_a_type_only_when_the_flow_can_store_it() -> None:
    """The prompt's vocabulary is exactly the flow's, in both directions.

    Practice excludes open_ended and topic exams exclude true_false, so a
    template naming a type its caller never passes asks the model to guess a
    JSON shape it was never shown -- and every question member forbids extra
    fields, so the guess fails validation and takes the whole set with it.
    """
    for kind in exam_quiz.KINDS.values():
        rendered = _topic_quiz_prompt(kind)
        allowed = set(kind.question_types)

        for question_type in ALL_TYPES:
            mentioned = f'"{question_type.value}"' in rendered or (
                f"- {question_type.value}:" in rendered
            )
            assert mentioned is (question_type in allowed), (
                f"{kind.output_type} prompt mentions {question_type.value}: "
                f"{mentioned}, allowed: {question_type in allowed}"
            )


def test_a_topic_quiz_prompt_never_claims_a_type_count_it_does_not_render() -> None:
    for kind in exam_quiz.KINDS.values():
        rendered = _topic_quiz_prompt(kind)

        assert "these four types" not in rendered
        assert "four types" not in rendered


def test_the_written_work_rule_appears_only_where_open_ended_is_offered() -> None:
    """The sentence that caused the defect, now bound to the type it names."""
    practice = _topic_quiz_prompt(exam_quiz.PRACTICE)
    topic_exam = _topic_quiz_prompt(exam_quiz.EXAM)

    assert QuizQuestionType.OPEN_ENDED not in exam_quiz.PRACTICE.question_types
    assert OPEN_ENDED_NOTE not in practice

    assert QuizQuestionType.OPEN_ENDED in exam_quiz.EXAM.question_types
    assert OPEN_ENDED_NOTE in topic_exam


def test_the_mock_exam_prompt_still_carries_the_written_work_rule() -> None:
    """A mock exam passes all four types, so the rule is correct there."""
    rendered = exam_course_artifacts._mock_prompt(
        "material",
        PLAN,
        CONTEXT,
        question_count=8,
        style="Past question style",
        quotas=(),
        types=(),
    )

    assert OPEN_ENDED_NOTE in rendered


# ------------------------------------------------------- similar-question rules


def _similar_prompt(
    *,
    original_count: int,
    question_count: int,
    policy: SimilarQuestionDifficultyPolicy,
) -> str:
    originals = "\n".join(
        f"{number}. Original question." for number in range(1, original_count + 1)
    )
    return exam_similar_questions._build_prompt(
        "material",
        TOPIC,
        CONTEXT,
        originals=originals,
        original_count=original_count,
        question_count=question_count,
        policy=policy,
        question_types=exam_similar_questions.SIMILAR_QUESTION_TYPES,
    )


def test_the_similar_prompt_states_one_quantity_when_the_counts_differ() -> None:
    """The default interaction: one source ticked, five questions requested.

    The per-original rule and the requested count are separate inputs, so the
    body may not assert one of them. build_quiz_data refuses a set whose length
    is not the requested count, which made the contradiction a paid failure.
    """
    rendered = _similar_prompt(
        original_count=1,
        question_count=5,
        policy=SimilarQuestionDifficultyPolicy.MATCH_SOURCE,
    )

    assert "Write one new question for each numbered original" not in rendered
    assert (
        "Write 5 new questions in total, drawn from the 1 numbered original,"
        in rendered
    )
    assert "Generate exactly 5 questions." in rendered


def test_the_per_original_rule_survives_when_the_counts_match() -> None:
    rendered = _similar_prompt(
        original_count=3,
        question_count=3,
        policy=SimilarQuestionDifficultyPolicy.MATCH_SOURCE,
    )

    assert "Write one new question for each numbered original" in rendered
    assert "new questions in total" not in rendered


def test_a_pinned_difficulty_removes_the_match_the_original_level_rule() -> None:
    """Both halves of the difficulty contradiction, body and constraints.

    PromptLoader appends every declared style constraint to the rendered
    prompt, so a constraint claiming to match the original's level contradicts
    a pinned policy exactly the way the body bullet did.
    """
    pinned = _similar_prompt(
        original_count=2,
        question_count=2,
        policy=SimilarQuestionDifficultyPolicy.HARD,
    )

    assert exam_similar_questions.MATCH_SOURCE_LEVEL_RULE not in pinned
    assert 'Level: write at the "hard" difficulty' in pinned
    assert 'Write every question at "hard" difficulty' in pinned
    assert "Match the original's skill, level, and shape" not in pinned


def test_matching_the_source_keeps_the_same_level_rule() -> None:
    rendered = _similar_prompt(
        original_count=2,
        question_count=2,
        policy=SimilarQuestionDifficultyPolicy.MATCH_SOURCE,
    )

    assert exam_similar_questions.MATCH_SOURCE_LEVEL_RULE in rendered
    assert "Level: write at the" not in rendered


def test_the_similar_template_declares_the_rules_it_now_substitutes() -> None:
    template = PromptLoader.load_template("exam_style_question")

    assert "SOURCE_MAPPING_RULE" in template.required_variables
    assert "LEVEL_RULE" in template.required_variables
    assert "Same level:" not in template.template
    assert "Write one new question for each" not in template.template
