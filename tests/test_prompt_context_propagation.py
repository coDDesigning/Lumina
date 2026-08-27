import pytest

from prompt_neutrality_policy import (
    assert_no_level_or_discipline_fallback,
    assert_only_the_supplied_level_directive,
)
from backend.app.models import DocumentChunk, UploadedDocument
from schemas.prompt_context import (
    EducationLevel,
    MaterialKind,
    PromptContext,
)
from services.ai_tutor import AiTutorService
from services.course_qa import CourseQAService
from services.exam_artifacts import PlannedTopic
import services.exam_quiz as exam_quiz
from services.exam_question_extraction import PastExamExtractionService
from services.exam_source_analysis import ExamSourceAnalysisService
from services.exam_topic_study import GUIDE_SPEC, SUMMARY_SPEC
from services.flashcard import FlashcardService
from services.prompt_generator import PromptGeneratorService
from services.quiz import QuizService
from services.quiz_grading import QuizGradingService
from services.study_guide import StudyGuideService
from schemas.quiz import QuizDifficulty, QuizQuestionType, QuizRequest
from schemas.exam_mode import ExamAnalysisRequest
from schemas.study_guide import StudyGuideRequest

HIGH_SCHOOL_CONTEXT = PromptContext(
    education_level=EducationLevel.HIGH_SCHOOL,
    course_title="AP Biology",
    subject_area="Biology",
    material_kind=MaterialKind.TEXTBOOK,
)
HIGH_SCHOOL_EXPECTED = ("high_school", "AP Biology", "Biology", "textbook")

GRADUATE_CONTEXT = PromptContext(
    education_level=EducationLevel.GRADUATE,
    course_title="Advanced Macroeconomics",
    subject_area="Economics",
    material_kind=MaterialKind.SLIDES,
)
GRADUATE_EXPECTED = ("graduate", "Advanced Macroeconomics", "Economics", "slides")


def _study_guide_prompt(context: PromptContext) -> str:
    request = StudyGuideRequest(
        summary_format="comprehensive", topic_focus="All Topics"
    )
    return StudyGuideService.build_prompt("material", request, context=context)


def _quiz_prompt(context: PromptContext) -> str:
    return QuizService.build_prompt(
        "material",
        QuizRequest(
            question_count=2,
            difficulty=QuizDifficulty.MEDIUM,
            question_types=[QuizQuestionType.MULTIPLE_CHOICE],
            topic_focus="All Topics",
        ),
        context=context,
    )


def _flashcard_prompt(context: PromptContext) -> str:
    return FlashcardService.build_prompt("material", context=context)


def _ai_tutor_prompt(context: PromptContext) -> str:
    return AiTutorService.build_prompt("material", "A question", context=context)


def _course_qa_prompt(context: PromptContext) -> str:
    return CourseQAService.build_prompt("material", "A question", context=context)


def _prompt_generator_prompt(context: PromptContext) -> str:
    return PromptGeneratorService.build_prompt("Write me a prompt", context=context)


def _exam_topic_analysis_prompt(context: PromptContext) -> str:
    return ExamSourceAnalysisService.build_prompt(
        "material",
        ExamAnalysisRequest(topic_focus="All Topics"),
        declared_topics=["Graph Traversal"],
        syllabus="Week 1 Graph Traversal",
        context=context,
    )


def _past_exam_extraction_prompt(context: PromptContext) -> str:
    return PastExamExtractionService.build_prompt("paper", context=context)


_PLANNED_TOPIC = PlannedTopic(
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


def _exam_topic_guide_prompt(context: PromptContext) -> str:
    return GUIDE_SPEC.build_prompt("material", _PLANNED_TOPIC, context)


def _exam_topic_summary_prompt(context: PromptContext) -> str:
    return SUMMARY_SPEC.build_prompt("material", _PLANNED_TOPIC, context)


def _exam_topic_practice_prompt(context: PromptContext) -> str:
    return exam_quiz._build_prompt(
        exam_quiz.PRACTICE,
        "material",
        _PLANNED_TOPIC,
        context,
        question_count=10,
        style=None,
    )


def _exam_topic_exam_prompt(context: PromptContext) -> str:
    return exam_quiz._build_prompt(
        exam_quiz.EXAM,
        "material",
        _PLANNED_TOPIC,
        context,
        question_count=10,
        style="Past question style",
    )


BUILDERS = {
    "study_guide": _study_guide_prompt,
    "quiz": _quiz_prompt,
    "flashcard": _flashcard_prompt,
    "ai_tutor": _ai_tutor_prompt,
    "course_qa": _course_qa_prompt,
    "prompt_generator": _prompt_generator_prompt,
    "exam_topic_analysis": _exam_topic_analysis_prompt,
    "past_exam_question_extraction": _past_exam_extraction_prompt,
    "exam_topic_guide": _exam_topic_guide_prompt,
    "exam_topic_summary": _exam_topic_summary_prompt,
    "exam_topic_practice": _exam_topic_practice_prompt,
    "exam_topic_exam": _exam_topic_exam_prompt,
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (HIGH_SCHOOL_CONTEXT, HIGH_SCHOOL_EXPECTED),
        (GRADUATE_CONTEXT, GRADUATE_EXPECTED),
    ],
    ids=["high_school_biology", "graduate_economics"],
)
def test_every_generation_prompt_carries_the_shared_context(
    name, context, expected
) -> None:
    prompt = BUILDERS[name](context)

    for value in expected:
        assert value in prompt, (name, value)
    assert_only_the_supplied_level_directive(
        prompt, context.education_level, f"the {name} prompt"
    )
    assert "{{" not in prompt


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_no_generation_prompt_defaults_a_learner_to_university(name) -> None:
    prompt = BUILDERS[name](PromptContext())

    assert "unspecified" in prompt
    assert_no_level_or_discipline_fallback(prompt, f"the {name} prompt")
    assert_only_the_supplied_level_directive(
        prompt, EducationLevel.UNSPECIFIED, f"the {name} prompt"
    )
    assert "{{" not in prompt


LECTURE_NOTES_CONTEXT = PromptContext(
    education_level=EducationLevel.UNDERGRADUATE,
    course_title="Introduction to Sociology",
    subject_area="Sociology",
    material_kind=MaterialKind.LECTURE_NOTES,
)


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_lecture_wording_comes_only_from_the_material_kind(name) -> None:
    prompt = BUILDERS[name](LECTURE_NOTES_CONTEXT)

    assert "lecture_notes" in prompt
    assert "The material is lecture notes" in prompt
    assert "{{" not in prompt


def test_quiz_grading_prompt_carries_the_shared_context(model_graph) -> None:
    prompt = QuizGradingService.build_prompt([], context=HIGH_SCHOOL_CONTEXT)

    for value in HIGH_SCHOOL_EXPECTED:
        assert value in prompt
    assert_only_the_supplied_level_directive(
        prompt, EducationLevel.HIGH_SCHOOL, "the quiz_grading prompt"
    )
    assert "{{" not in prompt


def test_generation_resolves_course_context_end_to_end(
    db_session, model_graph, retrieval_env
) -> None:
    model_graph.course.title = "AP Biology"
    model_graph.course.subject_area = "Biology"
    model_graph.course.education_level = EducationLevel.HIGH_SCHOOL.value
    document = UploadedDocument(
        original_file_name="chapter.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="d" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="chapter.txt",
        status="ready",
        material_kind=MaterialKind.TEXTBOOK.value,
    )
    chunk = DocumentChunk(
        document=document,
        course=model_graph.course,
        chunk_index=0,
        page_number=None,
        text="Mitochondria produce ATP.",
    )
    db_session.add(document)
    db_session.add(chunk)
    db_session.flush()
    retrieval_env.index(db_session, document, [chunk], seeds=[0.1])
    db_session.commit()

    captured: list[str] = []

    class CapturingProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            captured.append(prompt)
            return {
                "deck_title": "Deck",
                "card_count": 1,
                "flashcards": [
                    {
                        "card_number": 1,
                        "difficulty": "Easy",
                        "front": "What makes ATP?",
                        "back": "Mitochondria.",
                    }
                ],
            }

    FlashcardService.generate(db_session, model_graph.course.id, CapturingProvider())

    assert len(captured) == 1
    prompt = captured[0]
    for value in HIGH_SCHOOL_EXPECTED:
        assert value in prompt
    assert_only_the_supplied_level_directive(
        prompt, EducationLevel.HIGH_SCHOOL, "the flashcard prompt"
    )
    assert "{{" not in prompt


def test_legacy_course_reaches_the_provider_as_unspecified(
    db_session, model_graph, retrieval_env
) -> None:
    document = UploadedDocument(
        original_file_name="legacy.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="e" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="legacy.txt",
        status="ready",
    )
    chunk = DocumentChunk(
        document=document,
        course=model_graph.course,
        chunk_index=0,
        page_number=None,
        text="Some legacy material.",
    )
    db_session.add(document)
    db_session.add(chunk)
    db_session.flush()
    retrieval_env.index(db_session, document, [chunk], seeds=[0.1])
    db_session.commit()

    captured: list[str] = []

    class CapturingProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            captured.append(prompt)
            return {
                "deck_title": "Deck",
                "card_count": 1,
                "flashcards": [
                    {
                        "card_number": 1,
                        "difficulty": "Easy",
                        "front": "Front",
                        "back": "Back",
                    }
                ],
            }

    FlashcardService.generate(db_session, model_graph.course.id, CapturingProvider())

    prompt = captured[0]
    assert "unspecified" in prompt
    assert "Unspecified subject area" in prompt
    assert_no_level_or_discipline_fallback(prompt, "the flashcard prompt")


def test_a_template_fault_never_reaches_the_provider(
    db_session, model_graph, retrieval_env, monkeypatch
) -> None:
    document = UploadedDocument(
        original_file_name="notes.txt",
        file_type="txt",
        mime_type="text/plain",
        file_size=10,
        file_hash="f" * 64,
        uploader=model_graph.user,
        course=model_graph.course,
        storage_provider="local:test",
        storage_key="notes.txt",
        status="ready",
    )
    chunk = DocumentChunk(
        document=document,
        course=model_graph.course,
        chunk_index=0,
        page_number=None,
        text="Material.",
    )
    db_session.add(document)
    db_session.add(chunk)
    db_session.flush()
    retrieval_env.index(db_session, document, [chunk], seeds=[0.1])
    db_session.commit()

    calls: list[str] = []

    class UncalledProvider:
        def generate_json(self, prompt: str) -> dict[str, object]:
            calls.append(prompt)
            raise AssertionError("Provider must not be reached")

    monkeypatch.setattr(
        PromptContext, "as_variables", lambda self: {"EDUCATION_LEVEL": "unspecified"}
    )

    with pytest.raises(Exception):
        FlashcardService.generate(db_session, model_graph.course.id, UncalledProvider())

    assert calls == []
