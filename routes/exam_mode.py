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

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from backend.app.database import get_db
from schemas.citation import Citation
from schemas.exam_mode import (
    ExamAnalysisRequest,
    ExamAnalysisResult,
    ExamAnalysisView,
    ExamPlanList,
    ExamPlanRequest,
    ExamPlanTopicView,
    ExamPlanView,
    ExamQuestionPage,
    ExamQuestionView,
    ExamSelectionCarryOver,
    ExamSourceInventory,
    ExamTopicCandidateView,
)
from schemas.response import BaseResponse
from schemas.user import UserResponse
from services.credits import CreditService
from services.exam_plan import ExamPlanService
from services.exam_source_analysis import ExamModeError, ExamSourceAnalysisService
from services.retrieval_material import RetrievalMaterialError
from services.text_generation import (
    TextGenerationError,
    get_text_generation_provider,
    resolve_effective_model,
)
from utils.ai_errors import ai_generation_http_exception
from utils.authorization import AuthorizedCourse, OwnedCourse
from utils.deps import get_current_user
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object
from utils.rate_limit import rate_limit_generation

router = APIRouter(prefix="/api/courses", tags=["Exam Mode"])

FEATURE_ANALYSIS = "exam_topic_analysis"
FEATURE_RESCAN = "exam_topic_analysis_rescan"
FEATURE_PLAN = "exam_plan"

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
    _, question_total = ExamSourceAnalysisService.load_questions(
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
    rows, total = ExamSourceAnalysisService.load_questions(
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
