"""HTTP surface for Exam Mode.

Reads follow the ordinary generated-output policy, so an administrator may look
at another owner's analysis or plan. Every write depends on ``OwnedCourse``,
where the write-any predicate is a constant false, so an administrator asking to
analyse or plan in someone else's course gets the same "Course not found" a
stranger does.

Creating a plan writes a row and is therefore a write, even though it spends no
credit and reaches no provider.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from backend.app.models import (
    OUTPUT_TYPE_EXAM_TOPIC_EXAM,
    OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
    OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
    OUTPUT_TYPE_EXAM_TOPIC_SUMMARY,
)
from schemas.citation import Citation
from schemas.exam_mode import (
    RANKING_ENGINE_DETERMINISTIC,
    ExamAnalysisRequest,
    ExamAnalysisResult,
    ExamAnalysisView,
    ExamEntitlementView,
    ExamPlanList,
    ExamPlanRequest,
    ExamPlanTopicView,
    ExamPlanView,
    ExamQuestionPage,
    ExamQuestionView,
    ExamSelectionCarryOver,
    ExamMockExamRequest,
    ExamMockExamResult,
    ExamPlanArtifactRequest,
    ExamReviewSheetDocument,
    ExamReviewSheetResult,
    ExamSimilarQuestionsResult,
    SimilarQuestionRequest,
    ExamSourceInventory,
    ExamTopicArtifactRequest,
    ExamTopicCandidateView,
    ExamTopicGuideDocument,
    ExamTopicGuideResult,
    ExamTopicQuizRequest,
    ExamTopicQuizResult,
    ExamTopicSummaryDocument,
    ExamTopicSummaryResult,
)
from schemas.quiz import QuizView
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.credits import CreditService
from services.exam_artifacts import ExamArtifactError, ExamArtifactService
from services.exam_entitlements import ExamEntitlementService
from services.exam_course_artifacts import (
    MockExamTopicError,
    topic_quotas,
    type_quotas,
    ExamMockExamService,
    ExamReviewSheetService,
)
from services.exam_plan import ExamPlanService
from services.exam_quiz import ExamQuizService
from services.quiz import QuizService
from services.exam_similar_questions import (
    SIMILAR_QUESTION_TYPES,
    ExamSimilarQuestionsService,
)
from services.exam_source_analysis import ExamModeError, ExamSourceAnalysisService
from services.exam_topic_study import ExamTopicStudyService
from services.retrieval_material import RetrievalMaterialError
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ERROR_CODE_HEADER, ai_generation_http_exception
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object
from utils.rate_limit import rate_limit_generation

router = APIRouter(prefix="/api/courses", tags=["Exam Mode"])

FEATURE_ANALYSIS = "exam_topic_analysis"
FEATURE_RESCAN = "exam_topic_analysis_rescan"
FEATURE_PLAN = "exam_plan"
FEATURE_TOPIC_GUIDE = "exam_topic_guide"
FEATURE_TOPIC_SUMMARY = "exam_topic_summary"
FEATURE_TOPIC_PRACTICE = "exam_topic_practice"
FEATURE_TOPIC_EXAM = "exam_topic_exam"
FEATURE_SIMILAR_QUESTIONS = "exam_similar_questions"
FEATURE_MOCK_EXAM = "exam_mock_exam"
FEATURE_REVIEW_SHEET = "exam_review_sheet"

MAX_TOPIC_KEY_LENGTH = 120

# A configuration a paper cannot be built from is the caller's to fix, so it is
# reported as its own stable category rather than as a generation failure.
MOCK_EXAM_CONFIGURATION_ERROR = "mock_exam_configuration_invalid"

MAX_QUESTION_PAGE_SIZE = 100

GENERATION_RESPONSES = {
    400: {"description": "No processed course material is available"},
    401: {"description": "Authentication required"},
    402: {"description": "Insufficient credits"},
    404: {"description": "Course or document not found"},
    409: {
        "description": (
            "No course material matched the request, the course material is not "
            "searchable yet, or a selected document is still being processed"
        )
    },
    422: {"description": "Invalid exam analysis request"},
    429: {"description": "AI provider or per-user generation rate limited"},
    503: {"description": "AI provider or course search unreachable"},
    504: {"description": "AI provider timed out"},
}

READ_RESPONSES = {
    401: {"description": "Authentication required"},
    404: {"description": "Course not found"},
}

TOPIC_GENERATION_RESPONSES = {
    **GENERATION_RESPONSES,
    409: {
        "description": (
            "This course has no exam plan yet, the topic is not one the plan "
            "ranked, no course material matched the request, or the course "
            "material is not searchable yet"
        )
    },
}


def _candidate_views(candidates) -> list[ExamTopicCandidateView]:
    return [
        ExamTopicCandidateView(
            topic_key=candidate.topic_key,
            display_label=candidate.display_label,
            aliases=list(candidate.aliases or []),
            in_syllabus=candidate.in_syllabus,
            in_course_topics=candidate.in_course_topics,
            in_past_exams=candidate.in_past_exams,
            in_material=candidate.in_material,
            discovery_confidence=candidate.discovery_confidence,
            syllabus_weight_percent=candidate.syllabus_weight_percent,
            syllabus_mention_count=candidate.syllabus_mention_count,
            past_exam_question_count=candidate.past_exam_question_count,
            material_chunk_count=candidate.material_chunk_count,
            citations=[
                Citation.model_validate(entry) for entry in (candidate.citations or [])
            ],
        )
        for candidate in candidates
    ]


def _carry_over(db: Session, course_id: int, candidates) -> ExamSelectionCarryOver:
    """What a previous plan's choices mean against this analysis.

    Reported, never applied. A rescan must not re-select anything on the
    student's behalf, so this only says which choices still match, which
    topics are new, and which are no longer supported.
    """
    previous = ExamPlanService.latest_plan(db, course_id)
    if previous is None:
        return ExamSelectionCarryOver()

    stored = (
        parse_json_object(
            previous.content,
            field="content",
            table="generated_outputs",
            row_id=previous.id,
        )
        or {}
    )
    settings_document = (
        parse_json_object(
            previous.generation_settings,
            field="generation_settings",
            table="generated_outputs",
            row_id=previous.id,
        )
        or {}
    )
    selected = settings_document.get("selected_topic_keys")
    priority = settings_document.get("high_priority_topic_keys")
    if not isinstance(selected, list):
        selected = [
            topic.get("topic_key")
            for topic in stored.get("topics", [])
            if isinstance(topic, dict)
        ]
    if not isinstance(priority, list):
        priority = []

    discovered = {candidate.topic_key for candidate in candidates}
    previously = [key for key in selected if isinstance(key, str)]

    return ExamSelectionCarryOver(
        previous_plan_output_id=previous.id,
        preselected_topic_keys=[key for key in previously if key in discovered],
        high_priority_topic_keys=[
            key
            for key in priority
            if isinstance(key, str) and key in discovered and key in previously
        ],
        new_topic_keys=sorted(discovered - set(previously)),
        unsupported_topic_keys=[key for key in previously if key not in discovered],
    )


def _analysis_view(db: Session, course_id: int, output) -> ExamAnalysisView:
    candidates = ExamSourceAnalysisService.load_candidates(db, course_id, output.id)
    stored = (
        parse_json_object(
            output.content,
            field="content",
            table="generated_outputs",
            row_id=output.id,
        )
        or {}
    )
    _, question_total, _ = ExamSourceAnalysisService.load_questions(
        db, course_id, output.id, limit=0, offset=0
    )
    documents = stored.get("documents_analysed")
    return ExamAnalysisView(
        generated_output_id=output.id,
        created_at=output.created_at,
        model_used=output.model_used,
        candidate_count=len(candidates),
        past_exam_question_count=question_total,
        documents_analysed=[
            UUID(value) for value in documents if isinstance(value, str)
        ]
        if isinstance(documents, list)
        else [],
        manual_review_recommended=True,
        topics=_candidate_views(candidates),
        selection_carry_over=_carry_over(db, course_id, candidates),
        coverage=stored.get("coverage")
        if isinstance(stored.get("coverage"), dict)
        else None,
        confidence_notes=str(stored.get("confidence_notes") or ""),
    )


def _plan_view(readout) -> ExamPlanView:
    content = readout.content
    topics = [
        ExamPlanTopicView.model_validate(topic)
        for topic in content.get("topics", [])
        if isinstance(topic, dict)
    ]
    return ExamPlanView(
        generated_output_id=readout.output.id,
        analysis_output_id=int(content.get("analysis_output_id") or 0),
        plan_version=int(content.get("plan_version") or 0),
        supersedes_output_id=content.get("supersedes_output_id"),
        created_at=readout.output.created_at,
        exam_date=content.get("exam_date"),
        days_until_exam=content.get("days_until_exam"),
        selection_mode=str(content.get("selection_mode") or "manual"),
        manual_review_recommended=bool(content.get("manual_review_recommended", True)),
        ranking_engine=str(
            content.get("ranking_engine") or RANKING_ENGINE_DETERMINISTIC
        ),
        ranking_policy_version=int(content.get("ranking_policy_version") or 1),
        configured_weights=content.get("configured_weights") or {},
        effective_weights=content.get("effective_weights") or {},
        signals_available=content.get("signals_available") or {},
        signal_bases=content.get("signal_bases") or {},
        unmapped_mastery_labels=int(content.get("unmapped_mastery_labels") or 0),
        warnings=content.get("warnings") or [],
        topics=topics,
        staleness=readout.staleness,
    )


def _analysis_result(db: Session, course_id: int, generation, output):
    material = generation.material
    return ExamAnalysisResult(
        analysis=_analysis_view(db, course_id, output),
        context_truncated=material.truncated,
        chunks_used=material.chunks_used,
        chunks_available=material.chunks_available,
        retrieval_narrowed=material.retrieval_narrowed,
        lowest_similarity=material.lowest_similarity,
        highest_similarity=material.highest_similarity,
    )


def _run_analysis(
    course,
    request: ExamAnalysisRequest,
    current_user: UserResponse,
    db: Session,
    *,
    rescan: bool,
) -> BaseResponse[ExamAnalysisResult]:
    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="study_guide",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = ExamSourceAnalysisService.analyse(
            db,
            course.id,
            request,
            provider,
            user_id=current_user.id,
            rescan=rescan,
        )
        persisted = ExamSourceAnalysisService.persist(
            db,
            course.id,
            generation,
            user_id=current_user.id,
            rescan=rescan,
        )
    except HTTPException:
        # A rejected source selection is already a considered answer. Passing it
        # through the generation mapper would relabel a 404 as a provider
        # failure and hide what the student has to fix.
        if generation is not None:
            db.rollback()
            CreditService.refund(db, generation.charge_receipt)
        raise
    except (
        TextGenerationError,
        ExamModeError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        if generation is not None:
            db.rollback()
            CreditService.refund(db, generation.charge_receipt)
        raise ai_generation_http_exception(
            exc, feature=FEATURE_RESCAN if rescan else FEATURE_ANALYSIS
        ) from exc

    return BaseResponse(
        success=True,
        message="Exam sources analysed successfully",
        data=_analysis_result(db, course.id, generation, persisted),
    )


@router.get(
    "/{course_id}/exam-mode/sources",
    response_model=BaseResponse[ExamSourceInventory],
    responses=READ_RESPONSES,
)
def list_exam_sources(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamSourceInventory]:
    return BaseResponse(
        success=True,
        message="Exam sources retrieved successfully",
        data=ExamSourceAnalysisService.list_sources(db, course.id),
    )


@router.post(
    "/{course_id}/exam-mode/analysis",
    response_model=BaseResponse[ExamAnalysisResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_ANALYSIS))],
    responses=GENERATION_RESPONSES,
)
def analyse_exam_sources(
    course: OwnedCourse,
    request: ExamAnalysisRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamAnalysisResult]:
    return _run_analysis(course, request, current_user, db, rescan=False)


@router.post(
    "/{course_id}/exam-mode/analysis/rescan",
    response_model=BaseResponse[ExamAnalysisResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_RESCAN))],
    responses=GENERATION_RESPONSES,
)
def rescan_exam_sources(
    course: OwnedCourse,
    request: ExamAnalysisRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamAnalysisResult]:
    """Analyse the selected sources again, leaving every existing plan alone.

    A rescan is its own route rather than a flag because it is priced
    differently, and a price a client must not hardcode has to be readable from
    the credit policy by its own name. It also keeps its own rate-limit bucket,
    so a cheap rescan cannot drain the expensive first analysis.
    """
    return _run_analysis(course, request, current_user, db, rescan=True)


@router.get(
    "/{course_id}/exam-mode/analysis",
    response_model=BaseResponse[ExamAnalysisView],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or exam topic analysis not found"},
    },
)
def get_exam_analysis(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
    output_id: Annotated[int | None, Query(ge=1)] = None,
) -> BaseResponse[ExamAnalysisView]:
    if output_id is not None:
        output = ExamSourceAnalysisService.get_analysis(db, course.id, output_id)
    else:
        output = ExamSourceAnalysisService.latest_analysis(db, course.id)
        if output is None:
            raise NotFoundException(detail="Exam topic analysis not found")

    return BaseResponse(
        success=True,
        message="Exam topic analysis retrieved successfully",
        data=_analysis_view(db, course.id, output),
    )


@router.get(
    "/{course_id}/exam-mode/analysis/{output_id}/questions",
    response_model=BaseResponse[ExamQuestionPage],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or exam topic analysis not found"},
    },
)
def list_past_exam_questions(
    course: AuthorizedCourse,
    output_id: int,
    db: Annotated[Session, Depends(get_db)],
    topic_key: Annotated[str | None, Query(max_length=120)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_QUESTION_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BaseResponse[ExamQuestionPage]:
    analysis = ExamSourceAnalysisService.get_analysis(db, course.id, output_id)
    rows, total, document_ids = ExamSourceAnalysisService.load_questions(
        db,
        course.id,
        analysis.id,
        topic_key=topic_key,
        limit=limit,
        offset=offset,
    )
    return BaseResponse(
        success=True,
        message="Past exam questions retrieved successfully",
        data=ExamQuestionPage(
            analysis_output_id=analysis.id,
            document_ids=document_ids,
            total=total,
            limit=limit,
            offset=offset,
            questions=[
                ExamQuestionView(
                    position=row.position,
                    document_id=row.document_id,
                    page_start=row.page_start,
                    page_end=row.page_end,
                    question_label=row.question_label,
                    question_number=row.question_number,
                    question_text=row.question_text,
                    subparts=row.subparts or [],
                    question_type=row.question_type,
                    difficulty=row.difficulty,
                    marks=row.marks,
                    answer_guidance=row.answer_guidance,
                    marking_points=row.marking_points or [],
                    visual_refs=row.visual_refs or [],
                    topic_key=row.topic_key,
                    topic_mappings=row.topic_mappings or [],
                    citations=[
                        Citation.model_validate(entry)
                        for entry in (row.citations or [])
                    ],
                )
                for row in rows
            ],
        ),
    )


@router.post(
    "/{course_id}/exam-mode/plans",
    response_model=BaseResponse[ExamPlanView],
    dependencies=[Depends(rate_limit_generation(FEATURE_PLAN))],
    responses={
        400: {"description": "The course has no usable exam date"},
        401: {"description": "Authentication required"},
        404: {"description": "Course or exam topic analysis not found"},
        409: {
            "description": (
                "No topic analysis exists, no topic was selected, or a selected "
                "topic is not part of that analysis"
            )
        },
        422: {"description": "Invalid exam plan request"},
        429: {"description": "Per-user generation rate limited"},
    },
)
def create_exam_plan(
    course: OwnedCourse,
    request: ExamPlanRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamPlanView]:
    """Rank the selected topics into a new immutable plan version.

    Deterministic, so no provider is reached and no credit is spent. The
    feature is still rate limited, which is why it appears in the rate-limit
    key space and deliberately not in the credit price table.
    """
    try:
        creation = ExamPlanService.create(
            db, course.id, request, user_id=current_user.id
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise ai_generation_http_exception(exc, feature=FEATURE_PLAN) from exc

    readout = ExamPlanService.readout(db, course.id, creation.output)
    return BaseResponse(
        success=True,
        message="Exam plan created successfully",
        data=_plan_view(readout),
    )


@router.get(
    "/{course_id}/exam-mode/entitlements",
    response_model=BaseResponse[ExamEntitlementView],
    responses=READ_RESPONSES,
)
def list_exam_entitlements(
    course: OwnedCourse,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamEntitlementView]:
    """Name the topics this student has already unlocked in this course.

    Owner-scoped rather than reader-scoped on purpose: what somebody has bought
    is theirs, so the administrator read-any override deliberately stops here.
    An unlock is keyed by (course, student, topic) with no plan identifier, so
    the set answers for every plan version at once.
    """
    unlocked = ExamEntitlementService.unlocked_topic_keys(
        db, course.id, current_user.id
    )
    return BaseResponse(
        success=True,
        message="Exam topic entitlements retrieved successfully",
        data=ExamEntitlementView(unlocked_topic_keys=sorted(unlocked)),
    )


@router.get(
    "/{course_id}/exam-mode/plans",
    response_model=BaseResponse[ExamPlanList],
    responses=READ_RESPONSES,
)
def list_exam_plans(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamPlanList]:
    plans = ExamPlanService.list_plans(db, course.id)
    current = plans[0].id if plans else None
    return BaseResponse(
        success=True,
        message="Exam plans retrieved successfully",
        data=ExamPlanList(
            plans=[
                ExamPlanService.summarize(plan, current_output_id=current)
                for plan in plans
            ],
            current_plan_output_id=current,
        ),
    )


@router.get(
    "/{course_id}/exam-mode/plans/{output_id}",
    response_model=BaseResponse[ExamPlanView],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or exam plan not found"},
    },
)
def get_exam_plan(
    course: AuthorizedCourse,
    output_id: int,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamPlanView]:
    """Return one stored plan exactly as it was written.

    A database read and nothing else: no provider, no retrieval, no embedding,
    no charge, and no write. A plan whose exam date has long passed still
    opens, because it is a study resource rather than a countdown.
    """
    output = ExamPlanService.get_plan(db, course.id, output_id)
    return BaseResponse(
        success=True,
        message="Exam plan retrieved successfully",
        data=_plan_view(ExamPlanService.readout(db, course.id, output)),
    )


# --------------------------------------------------------------- per-topic study


def _topic_artifact(
    course,
    topic_key: str,
    request: ExamTopicArtifactRequest,
    current_user: UserResponse,
    db: Session,
    *,
    output_type: str,
    feature: str,
):
    """Generate one per-topic artifact, or map the refusal to a stable code.

    The topic is resolved before anything is spent, so asking for a topic this
    course never planned costs nothing and says which of the two things is
    wrong: there is no plan, or that is not one of its topics.
    """
    try:
        topic = ExamArtifactService.resolve_topic(
            db, course.id, topic_key, plan_output_id=request.plan_output_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature=feature) from exc

    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="study_guide",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = ExamTopicStudyService.generate(
            db,
            course.id,
            topic,
            provider,
            user_id=current_user.id,
            output_type=output_type,
        )
        return ExamTopicStudyService.persist(
            db,
            course.id,
            generation,
            user_id=current_user.id,
            output_type=output_type,
        ), generation
    except HTTPException:
        _release(db, generation)
        raise
    except (
        TextGenerationError,
        ExamArtifactError,
        ExamModeError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        _release(db, generation)
        raise ai_generation_http_exception(exc, feature=feature) from exc


def _release(db: Session, generation) -> None:
    """Give back whatever a generation took, once it is known to have failed.

    ``ExamArtifactService`` already undoes its own failures. This covers the
    window after a generation succeeds and before its row is written: without
    it a database error while persisting would leave a student charged for an
    artifact that does not exist, and the retry free — the exact outcome
    releasing exists to prevent.

    A per-topic artifact gives back its unlock; a course-level one refunds its
    own charge. Both are safe to call on a generation that already released,
    because the unlock row is gone and a refund is recorded at most once.
    """
    if generation is None:
        return
    if generation.unlock is not None:
        ExamEntitlementService.release(db, generation.unlock)
        return
    db.rollback()
    CreditService.refund(db, generation.charge_receipt)
    try:
        db.commit()
    except Exception:
        db.rollback()


def _stored_document(output, model):
    stored = (
        parse_json_object(
            output.content,
            field="content",
            table="generated_outputs",
            row_id=output.id,
        )
        or {}
    )
    return model.model_validate(stored)


@router.post(
    "/{course_id}/exam-mode/topics/{topic_key}/guide",
    response_model=BaseResponse[ExamTopicGuideResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_TOPIC_GUIDE))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_topic_guide(
    course: OwnedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    request: ExamTopicArtifactRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamTopicGuideResult]:
    persisted, generation = _topic_artifact(
        course,
        topic_key,
        request,
        current_user,
        db,
        output_type=OUTPUT_TYPE_EXAM_TOPIC_GUIDE,
        feature=FEATURE_TOPIC_GUIDE,
    )
    material = generation.material
    return BaseResponse(
        success=True,
        message="Topic study guide generated successfully",
        data=ExamTopicGuideResult(
            guide=persisted.document,
            generated_output_id=persisted.output.id,
            created_at=persisted.output.created_at,
            model_used=persisted.output.model_used,
            credits_charged=persisted.credits_charged,
            context_truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            retrieval_narrowed=material.retrieval_narrowed,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
        ),
    )


@router.get(
    "/{course_id}/exam-mode/topics/{topic_key}/guide",
    response_model=BaseResponse[ExamTopicGuideDocument],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or topic study guide not found"},
    },
)
def get_topic_guide(
    course: AuthorizedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamTopicGuideDocument]:
    output = ExamTopicStudyService.latest(
        db, course.id, OUTPUT_TYPE_EXAM_TOPIC_GUIDE, topic_key=topic_key
    )
    if output is None:
        raise NotFoundException(detail="Topic study guide not found")
    return BaseResponse(
        success=True,
        message="Topic study guide retrieved successfully",
        data=_stored_document(output, ExamTopicGuideDocument),
    )


@router.post(
    "/{course_id}/exam-mode/topics/{topic_key}/summary",
    response_model=BaseResponse[ExamTopicSummaryResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_TOPIC_SUMMARY))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_topic_summary(
    course: OwnedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    request: ExamTopicArtifactRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamTopicSummaryResult]:
    persisted, generation = _topic_artifact(
        course,
        topic_key,
        request,
        current_user,
        db,
        output_type=OUTPUT_TYPE_EXAM_TOPIC_SUMMARY,
        feature=FEATURE_TOPIC_SUMMARY,
    )
    material = generation.material
    return BaseResponse(
        success=True,
        message="Topic summary generated successfully",
        data=ExamTopicSummaryResult(
            summary=persisted.document,
            generated_output_id=persisted.output.id,
            created_at=persisted.output.created_at,
            model_used=persisted.output.model_used,
            credits_charged=persisted.credits_charged,
            context_truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            retrieval_narrowed=material.retrieval_narrowed,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
        ),
    )


@router.get(
    "/{course_id}/exam-mode/topics/{topic_key}/summary",
    response_model=BaseResponse[ExamTopicSummaryDocument],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or topic summary not found"},
    },
)
def get_topic_summary(
    course: AuthorizedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamTopicSummaryDocument]:
    output = ExamTopicStudyService.latest(
        db, course.id, OUTPUT_TYPE_EXAM_TOPIC_SUMMARY, topic_key=topic_key
    )
    if output is None:
        raise NotFoundException(detail="Topic summary not found")
    return BaseResponse(
        success=True,
        message="Topic summary retrieved successfully",
        data=_stored_document(output, ExamTopicSummaryDocument),
    )


# --------------------------------------------------------------- topic quizzes


def _topic_quiz(
    course,
    topic_key: str,
    request: ExamTopicQuizRequest,
    current_user: UserResponse,
    db: Session,
    *,
    output_type: str,
    feature: str,
    message: str,
) -> BaseResponse[ExamTopicQuizResult]:
    """Generate one quiz-backed artifact for a planned topic.

    An examination is served through Exam Mode's own answers-hidden view. The
    ordinary quiz read always exposes the answer, and an examination a student
    can read the answers to is not an examination; grading still reads the rows,
    so nothing downstream changes.
    """
    try:
        topic = ExamArtifactService.resolve_topic(
            db, course.id, topic_key, plan_output_id=request.plan_output_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature=feature) from exc

    question_count = ExamQuizService.resolve_question_count(request.question_count)

    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="quiz",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = ExamQuizService.generate(
            db,
            course.id,
            topic,
            provider,
            user_id=current_user.id,
            output_type=output_type,
            question_count=question_count,
        )
        persisted = ExamQuizService.persist(
            db,
            course.id,
            generation,
            user_id=current_user.id,
            output_type=output_type,
            question_count=question_count,
        )
    except HTTPException:
        _release(db, generation)
        raise
    except (
        TextGenerationError,
        ExamArtifactError,
        ExamModeError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        _release(db, generation)
        raise ai_generation_http_exception(exc, feature=feature) from exc

    hidden = output_type == OUTPUT_TYPE_EXAM_TOPIC_EXAM
    view = ExamQuizService.hide_answers(persisted.view) if hidden else persisted.view
    material = generation.material
    return BaseResponse(
        success=True,
        message=message,
        data=ExamTopicQuizResult(
            quiz=view,
            generated_output_id=persisted.output.id,
            created_at=persisted.output.created_at,
            model_used=persisted.output.model_used,
            credits_charged=persisted.credits_charged,
            answers_hidden=hidden,
            context_truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            retrieval_narrowed=material.retrieval_narrowed,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
        ),
    )


@router.post(
    "/{course_id}/exam-mode/topics/{topic_key}/practice",
    response_model=BaseResponse[ExamTopicQuizResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_TOPIC_PRACTICE))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_topic_practice(
    course: OwnedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    request: ExamTopicQuizRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamTopicQuizResult]:
    return _topic_quiz(
        course,
        topic_key,
        request,
        current_user,
        db,
        output_type=OUTPUT_TYPE_EXAM_TOPIC_PRACTICE,
        feature=FEATURE_TOPIC_PRACTICE,
        message="Practice questions generated successfully",
    )


@router.post(
    "/{course_id}/exam-mode/topics/{topic_key}/exam",
    response_model=BaseResponse[ExamTopicQuizResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_TOPIC_EXAM))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_topic_exam(
    course: OwnedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    request: ExamTopicQuizRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamTopicQuizResult]:
    return _topic_quiz(
        course,
        topic_key,
        request,
        current_user,
        db,
        output_type=OUTPUT_TYPE_EXAM_TOPIC_EXAM,
        feature=FEATURE_TOPIC_EXAM,
        message="Topic exam generated successfully",
    )


# --------------------------------------------------------------- similar questions


@router.post(
    "/{course_id}/exam-mode/topics/{topic_key}/similar-questions",
    response_model=BaseResponse[ExamSimilarQuestionsResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_SIMILAR_QUESTIONS))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_similar_questions(
    course: OwnedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    request: SimilarQuestionRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamSimilarQuestionsResult]:
    """Write fresh questions in the mould of this topic's past ones.

    The originals are resolved and checked before the topic is unlocked, so a
    topic this course has never examined is a conflict naming what to do about
    it rather than a charge for an empty page, and an identifier belonging to
    another course is answered as a missing one.

    The result is served with its answers hidden. This is an assessment a
    student is meant to sit, and the answers arrive through the attempt they
    submit rather than through the page that sets it.
    """
    try:
        topic = ExamArtifactService.resolve_topic(
            db, course.id, topic_key, plan_output_id=request.plan_output_id
        )
        plan = ExamArtifactService.resolve_plan(
            db, course.id, plan_output_id=request.plan_output_id
        )
        originals = ExamSimilarQuestionsService.source_questions(
            db, course.id, topic, requested_ids=request.source_question_ids
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise ai_generation_http_exception(
            exc, feature=FEATURE_SIMILAR_QUESTIONS
        ) from exc

    question_types = (
        tuple(request.requested_question_types)
        if request.requested_question_types
        else SIMILAR_QUESTION_TYPES
    )
    other_topic_keys = frozenset(
        planned.topic_key
        for planned in plan.topics
        if planned.topic_key != topic.topic_key
    )

    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="quiz",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = ExamSimilarQuestionsService.generate(
            db,
            course.id,
            topic,
            provider,
            user_id=current_user.id,
            originals=originals,
            question_count=request.question_count,
            policy=request.difficulty_policy,
            question_types=question_types,
        )
        persisted = ExamSimilarQuestionsService.persist(
            db,
            course.id,
            generation,
            user_id=current_user.id,
            originals=originals,
            question_count=request.question_count,
            policy=request.difficulty_policy,
            question_types=question_types,
            other_topic_keys=other_topic_keys,
            generation_request_id=(
                str(request.request_id) if request.request_id is not None else None
            ),
        )
    except HTTPException:
        _release(db, generation)
        raise
    except (
        TextGenerationError,
        ExamArtifactError,
        ExamModeError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        _release(db, generation)
        raise ai_generation_http_exception(
            exc, feature=FEATURE_SIMILAR_QUESTIONS
        ) from exc

    material = generation.material
    return BaseResponse(
        success=True,
        message="Similar questions generated successfully",
        data=ExamSimilarQuestionsResult(
            quiz=ExamQuizService.hide_answers(persisted.view),
            generated_output_id=persisted.output.id,
            created_at=persisted.output.created_at,
            model_used=persisted.output.model_used,
            credits_charged=persisted.credits_charged,
            answers_hidden=True,
            source_question_ids=persisted.source_question_ids,
            context_truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            retrieval_narrowed=material.retrieval_narrowed,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
        ),
    )


@router.get(
    "/{course_id}/exam-mode/topics/{topic_key}/similar-questions",
    response_model=BaseResponse[QuizView],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or similar questions not found"},
    },
)
def get_similar_questions(
    course: AuthorizedCourse,
    topic_key: Annotated[str, Path(max_length=MAX_TOPIC_KEY_LENGTH)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[QuizView]:
    """Reopen this topic's most recent similar-question set, answers hidden.

    Served from the quiz rows rather than the history document, because the
    quiz is the assessment. A set generated before these were quizzes has no
    row to serve and is answered as missing rather than half-read from an
    older shape; it remains readable in the course's generated-output history.
    """
    quiz = ExamSimilarQuestionsService.latest_quiz(db, course.id, topic_key=topic_key)
    if quiz is None:
        raise NotFoundException(detail="Similar questions not found")
    return BaseResponse(
        success=True,
        message="Similar questions retrieved successfully",
        data=ExamQuizService.hide_answers(QuizService.build_quiz_view(quiz)),
    )


# --------------------------------------------------------------- whole-plan work


def _resolve_plan(course, request: ExamPlanArtifactRequest, db: Session, feature: str):
    try:
        return ExamArtifactService.resolve_plan(
            db, course.id, plan_output_id=request.plan_output_id
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise ai_generation_http_exception(exc, feature=feature) from exc


@router.post(
    "/{course_id}/exam-mode/mock-exam",
    response_model=BaseResponse[ExamMockExamResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_MOCK_EXAM))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_mock_exam(
    course: OwnedCourse,
    request: ExamMockExamRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamMockExamResult]:
    """Write one paper across the whole plan, weighted by its own ranking.

    Priced on its own rather than under a topic's unlock: a student who paid for
    one topic has not paid for a paper covering twelve. Served with its answers
    hidden, because a paper you can read the answers to is not a mock exam.
    """
    plan = _resolve_plan(course, request, db, FEATURE_MOCK_EXAM)

    requested_mix = (
        [(entry.question_type.value, entry.count) for entry in request.question_mix]
        if request.question_mix is not None
        else None
    )
    if request.question_count is not None:
        question_count = ExamMockExamService.resolve_question_count(
            request.question_count
        )
    elif requested_mix is not None:
        # A mix with no explicit total states the length by itself; deriving it
        # here keeps the two from ever disagreeing.
        question_count = sum(count for _, count in requested_mix)
    else:
        question_count = ExamMockExamService.resolve_question_count(None)

    # The split is decided before a provider is built, so a request that cannot
    # be turned into a paper is refused without spending anything.
    try:
        quotas = topic_quotas(plan, request.topic_keys, question_count)
        types = type_quotas(requested_mix, question_count)
    except MockExamTopicError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
            headers={ERROR_CODE_HEADER: MOCK_EXAM_CONFIGURATION_ERROR},
        ) from exc

    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="quiz",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = ExamMockExamService.generate(
            db,
            course.id,
            plan,
            provider,
            user_id=current_user.id,
            question_count=question_count,
            quotas=quotas,
            types=types,
        )
        persisted = ExamMockExamService.persist(
            db,
            course.id,
            generation,
            user_id=current_user.id,
            question_count=question_count,
            quotas=quotas,
            types=types,
            duration_minutes=request.duration_minutes,
            generation_request_id=(
                str(request.request_id) if request.request_id is not None else None
            ),
        )
    except HTTPException:
        _release(db, generation)
        raise
    except (
        TextGenerationError,
        ExamArtifactError,
        ExamModeError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        _release(db, generation)
        raise ai_generation_http_exception(exc, feature=FEATURE_MOCK_EXAM) from exc

    material = generation.material
    return BaseResponse(
        success=True,
        message="Mock exam generated successfully",
        data=ExamMockExamResult(
            quiz=ExamQuizService.hide_answers(persisted.view),
            generated_output_id=persisted.output.id,
            created_at=persisted.output.created_at,
            model_used=persisted.output.model_used,
            credits_charged=persisted.credits_charged,
            answers_hidden=True,
            duration_minutes=request.duration_minutes,
            time_limit_seconds=persisted.quiz.time_limit_seconds or 0,
            context_truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            retrieval_narrowed=material.retrieval_narrowed,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
        ),
    )


@router.get(
    "/{course_id}/exam-mode/mock-exam",
    response_model=BaseResponse[QuizView],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or mock exam not found"},
    },
)
def get_mock_exam(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[QuizView]:
    """Reopen this course's most recent mock exam, answers hidden.

    Served from the quiz rows, because the quiz is the paper. A paper is still
    an examination when it is reopened, so the answers stay behind the attempt.
    """
    quiz = ExamMockExamService.latest_quiz(db, course.id)
    if quiz is None:
        raise NotFoundException(detail="Mock exam not found")
    return BaseResponse(
        success=True,
        message="Mock exam retrieved successfully",
        data=ExamQuizService.hide_answers(QuizService.build_quiz_view(quiz)),
    )


@router.post(
    "/{course_id}/exam-mode/review-sheet",
    response_model=BaseResponse[ExamReviewSheetResult],
    dependencies=[Depends(rate_limit_generation(FEATURE_REVIEW_SHEET))],
    responses=TOPIC_GENERATION_RESPONSES,
)
def generate_review_sheet(
    course: OwnedCourse,
    request: ExamPlanArtifactRequest,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamReviewSheetResult]:
    plan = _resolve_plan(course, request, db, FEATURE_REVIEW_SHEET)

    generation = None
    try:
        effective_model = resolve_effective_model(
            request.model,
            current_user.preferred_model,
            required_capability="study_guide",
        )
        try:
            provider = get_text_generation_provider(effective_model=effective_model)
        except TypeError:
            provider = get_text_generation_provider()

        generation = ExamReviewSheetService.generate(
            db, course.id, plan, provider, user_id=current_user.id
        )
        persisted = ExamReviewSheetService.persist(
            db, course.id, generation, user_id=current_user.id
        )
    except HTTPException:
        _release(db, generation)
        raise
    except (
        TextGenerationError,
        ExamArtifactError,
        ExamModeError,
        RetrievalMaterialError,
        Exception,
    ) as exc:
        _release(db, generation)
        raise ai_generation_http_exception(exc, feature=FEATURE_REVIEW_SHEET) from exc

    material = generation.material
    return BaseResponse(
        success=True,
        message="Review sheet generated successfully",
        data=ExamReviewSheetResult(
            review_sheet=persisted.document,
            generated_output_id=persisted.output.id,
            created_at=persisted.output.created_at,
            model_used=persisted.output.model_used,
            credits_charged=persisted.credits_charged,
            context_truncated=material.truncated,
            chunks_used=material.chunks_used,
            chunks_available=material.chunks_available,
            retrieval_narrowed=material.retrieval_narrowed,
            lowest_similarity=material.lowest_similarity,
            highest_similarity=material.highest_similarity,
        ),
    )


@router.get(
    "/{course_id}/exam-mode/review-sheet",
    response_model=BaseResponse[ExamReviewSheetDocument],
    responses={
        **READ_RESPONSES,
        404: {"description": "Course or review sheet not found"},
    },
)
def get_review_sheet(
    course: AuthorizedCourse,
    db: Annotated[Session, Depends(get_db)],
) -> BaseResponse[ExamReviewSheetDocument]:
    output = ExamReviewSheetService.latest(db, course.id)
    if output is None:
        raise NotFoundException(detail="Review sheet not found")
    return BaseResponse(
        success=True,
        message="Review sheet retrieved successfully",
        data=_stored_document(output, ExamReviewSheetDocument),
    )
