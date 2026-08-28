"""Turning one analysis plus the student's choices into an immutable exam plan.

A plan is the record of a decision: these were the sources, this was the
mastery, this was the policy, and this is why each topic landed where it did.
Nothing rewrites one. A later plan supersedes an earlier plan by reference, so
the reasoning a student actually studied from stays readable after the sources,
the scores, and the exam itself have moved on.

No provider is involved. Discovery already happened and is already persisted;
ordering is arithmetic over those stored values, which is why creating a plan
costs no credit and reopening one is a database read.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import (
    OUTPUT_TYPE_EXAM_PLAN,
    Course,
    ExamTopicCandidate,
    GeneratedOutput,
)
from schemas.exam_mode import (
    SELECTION_MODE_ALL_DISCOVERED,
    ExamPlanDocument,
    ExamPlanGenerationContext,
    ExamPlanGenerationSettings,
    ExamPlanRequest,
    ExamPlanStaleness,
    ExamPlanSummary,
)
from services.exam_ranking import (
    BASE_WEIGHTS,
    RANKING_POLICY_VERSION,
    RankingResult,
    SignalAvailability,
    TopicSignals,
    derive_availability,
    rank_topics,
)
from services.exam_source_analysis import (
    ExamModeError,
    ExamSourceAnalysisService,
)
from services.exam_staleness import (
    UNTAGGED_TOPIC,
    build_fingerprint,
    compare_fingerprints,
    mastery_rows,
    requires_rescan,
)
from services.exam_topics import (
    TOPIC_KEY_VERSION,
    build_topic_index,
    match_topic_key,
    syllabus_positions,
)
from services.generated_output import GeneratedOutputService
from utils.ai_errors import (
    EXAM_ANALYSIS_REQUIRED_MESSAGE,
    EXAM_DATE_MISSING_MESSAGE,
    EXAM_DATE_NOT_FUTURE_MESSAGE,
    EXAM_TOPIC_NOT_DISCOVERED_MESSAGE,
    EXAM_TOPIC_SELECTION_REQUIRED_MESSAGE,
    ExamAnalysisRequiredError,
    ExamDateMissingError,
    ExamDateNotFutureError,
    ExamTopicNotDiscoveredError,
    ExamTopicSelectionRequiredError,
)
from utils.exceptions import NotFoundException
from utils.json_documents import parse_json_object

EXAM_PLAN_NOT_FOUND = "Exam plan not found"

WARNING_NO_SYLLABUS = "no_syllabus_evidence"
WARNING_NO_PAST_EXAMS = "no_past_exam_evidence"
WARNING_NO_MASTERY = "no_mastery_evidence"
WARNING_SPARSE_MATERIAL = "sparse_material_coverage"
WARNING_UNMAPPED_MASTERY = "unmapped_mastery_labels"


class ExamPlanAnalysisMissingError(ExamModeError, ExamAnalysisRequiredError):
    """No completed topic analysis exists for this course."""


class ExamPlanExamDateMissingError(ExamModeError, ExamDateMissingError):
    """The course has no exam date, so there is nothing to plan towards."""


class ExamPlanExamDateNotFutureError(ExamModeError, ExamDateNotFutureError):
    """The course exam date has passed, so a first plan cannot be started."""


class ExamPlanTopicSelectionRequiredError(
    ExamModeError, ExamTopicSelectionRequiredError
):
    """No topic was selected, so there is nothing to rank."""


class ExamPlanTopicNotDiscoveredError(ExamModeError, ExamTopicNotDiscoveredError):
    """A selected topic does not belong to the analysis it was chosen from."""


@dataclass(frozen=True)
class ExamPlanCreation:
    """One persisted plan and the document it was written from."""

    output: GeneratedOutput
    document: ExamPlanDocument


@dataclass(frozen=True)
class ExamPlanReadout:
    """One stored plan as it is read back, with staleness computed fresh."""

    output: GeneratedOutput
    content: dict
    staleness: ExamPlanStaleness


class ExamPlanService:
    @staticmethod
    def latest_plan(db: Session, course_id: int) -> GeneratedOutput | None:
        return db.scalar(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_PLAN,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
            .limit(1)
        )

    @staticmethod
    def list_plans(db: Session, course_id: int) -> Sequence[GeneratedOutput]:
        return db.scalars(
            select(GeneratedOutput)
            .where(
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_PLAN,
            )
            .order_by(GeneratedOutput.created_at.desc(), GeneratedOutput.id.desc())
        ).all()

    @staticmethod
    def get_plan(db: Session, course_id: int, output_id: int) -> GeneratedOutput:
        """Load one plan scoped to its course, or deny without disclosure.

        The course predicate lives in the same statement as the identifier, so
        a plan belonging to another course is indistinguishable from one that
        does not exist.
        """
        output = db.scalar(
            select(GeneratedOutput).where(
                GeneratedOutput.id == output_id,
                GeneratedOutput.course_id == course_id,
                GeneratedOutput.output_type == OUTPUT_TYPE_EXAM_PLAN,
            )
        )
        if output is None:
            raise NotFoundException(detail=EXAM_PLAN_NOT_FOUND)
        return output

    @staticmethod
    def resolve_analysis(
        db: Session, course_id: int, analysis_output_id: int | None
    ) -> GeneratedOutput:
        if analysis_output_id is not None:
            return ExamSourceAnalysisService.get_analysis(
                db, course_id, analysis_output_id
            )
        analysis = ExamSourceAnalysisService.latest_analysis(db, course_id)
        if analysis is None:
            raise ExamPlanAnalysisMissingError(EXAM_ANALYSIS_REQUIRED_MESSAGE)
        return analysis

    @staticmethod
    def build_signals(
        db: Session,
        course_id: int,
        candidates: Sequence[ExamTopicCandidate],
        *,
        selected_topic_keys: Sequence[str],
        high_priority_topic_keys: Sequence[str],
        mastery_user_id: int,
        syllabus: str | None,
    ) -> tuple[tuple[TopicSignals, ...], SignalAvailability, int]:
        """Assemble the ranking inputs for the topics the student selected.

        Mastery is matched onto candidates by the same canonical key the
        analysis used, so a label a model wrote into a quiz months ago can
        still find its topic. A label that matches nothing is counted and
        reported rather than attributed to whichever topic looks closest.
        """
        selected = list(dict.fromkeys(selected_topic_keys))
        priority = set(high_priority_topic_keys)
        by_key = {candidate.topic_key: candidate for candidate in candidates}

        index = build_topic_index(candidates)
        positions = syllabus_positions(candidates, syllabus)

        totals: dict[str, list[int]] = {}
        unmapped = 0
        for label, correct in mastery_rows(db, course_id, mastery_user_id):
            if label == UNTAGGED_TOPIC:
                continue
            key = match_topic_key(label, index)
            if key is None:
                unmapped += 1
                continue
            bucket = totals.setdefault(key, [0, 0])
            bucket[0] += 1
            bucket[1] += int(correct)

        signals: list[TopicSignals] = []
        for key in selected:
            candidate = by_key[key]
            answered, correct = totals.get(key, (0, 0))
            signals.append(
                TopicSignals(
                    topic_key=candidate.topic_key,
                    display_label=candidate.display_label,
                    is_high_priority=key in priority,
                    in_syllabus=candidate.in_syllabus,
                    in_course_topics=candidate.in_course_topics,
                    syllabus_weight_percent=candidate.syllabus_weight_percent,
                    syllabus_mention_count=candidate.syllabus_mention_count,
                    syllabus_position=positions.get(key, _MAX_POSITION),
                    past_exam_question_count=candidate.past_exam_question_count,
                    past_exam_marks_total=candidate.past_exam_marks_total,
                    material_chunk_count=candidate.material_chunk_count,
                    material_character_count=candidate.material_character_count,
                    mastery_questions_answered=answered,
                    mastery_questions_correct=correct,
                )
            )

        return tuple(signals), derive_availability(signals), unmapped

    @classmethod
    def create(
        cls,
        db: Session,
        course_id: int,
        request: ExamPlanRequest,
        *,
        user_id: int,
    ) -> ExamPlanCreation:
        """Rank the selected topics and persist one new immutable plan version.

        No provider, no retrieval, no credit. Everything consumed here was
        already written by the analysis this plan names.
        """
        analysis = cls.resolve_analysis(db, course_id, request.analysis_output_id)
        candidates = ExamSourceAnalysisService.load_candidates(
            db, course_id, analysis.id
        )

        selected = list(request.selected_topic_keys)
        if request.selection_mode == SELECTION_MODE_ALL_DISCOVERED and not selected:
            # Automatic means every discovered topic, not a guessed cut-off and
            # not a second provider call to ask which ones to drop.
            selected = [candidate.topic_key for candidate in candidates]
        if not selected:
            raise ExamPlanTopicSelectionRequiredError(
                EXAM_TOPIC_SELECTION_REQUIRED_MESSAGE
            )

        known = {candidate.topic_key for candidate in candidates}
        if any(key not in known for key in selected):
            raise ExamPlanTopicNotDiscoveredError(EXAM_TOPIC_NOT_DISCOVERED_MESSAGE)

        priority = [key for key in request.high_priority_topic_keys if key in known]

        course = db.get(Course, course_id)
        exam_date = course.exam_date if course is not None else None
        cls._require_startable_exam_date(db, course_id, exam_date)

        signals, availability, unmapped = cls.build_signals(
            db,
            course_id,
            candidates,
            selected_topic_keys=selected,
            high_priority_topic_keys=priority,
            mastery_user_id=user_id,
            syllabus=course.syllabus if course is not None else None,
        )
        ranking = rank_topics(signals, availability)

        previous = cls.latest_plan(db, course_id)
        plan_version = 1
        if previous is not None:
            stored = parse_json_object(
                previous.content,
                field="content",
                table="generated_outputs",
                row_id=previous.id,
            )
            plan_version = int((stored or {}).get("plan_version", 0) or 0) + 1

        fingerprint = build_fingerprint(
            db,
            course_id,
            analysis_output_id=analysis.id,
            mastery_user_id=user_id,
            topic_index=build_topic_index(candidates),
            selected_topic_keys=selected,
            high_priority_topic_keys=priority,
        )

        document = ExamPlanDocument(
            plan_version=plan_version,
            supersedes_output_id=previous.id if previous is not None else None,
            analysis_output_id=analysis.id,
            exam_date=exam_date,
            days_until_exam=_days_until(exam_date),
            selection_mode=request.selection_mode,
            manual_review_recommended=True,
            ranking_policy_version=RANKING_POLICY_VERSION,
            configured_weights=dict(BASE_WEIGHTS),
            effective_weights=ranking.effective_weights,
            signals_available=ranking.signals_available,
            signal_bases=ranking.signal_bases,
            unmapped_mastery_labels=unmapped,
            warnings=_warnings(ranking, unmapped),
            topics=[topic.as_dict() for topic in ranking.topics],
            fingerprint=fingerprint,
        )

        applied_settings = ExamPlanGenerationSettings(
            analysis_output_id=analysis.id,
            selected_topic_keys=selected,
            high_priority_topic_keys=priority,
            selection_mode=request.selection_mode,
            ranking_policy_version=RANKING_POLICY_VERSION,
            topic_key_version=TOPIC_KEY_VERSION,
        )
        applied_context = ExamPlanGenerationContext(
            ranking_policy_version=RANKING_POLICY_VERSION,
            analysis_output_id=analysis.id,
            analysis_model_used=analysis.model_used,
            analysis_created_at=analysis.created_at,
            candidates_available=len(candidates),
            topics_ranked=len(ranking.topics),
            unmapped_mastery_labels=unmapped,
            configured_weights=dict(BASE_WEIGHTS),
            effective_weights=ranking.effective_weights,
            signals_available=ranking.signals_available,
            signal_bases=ranking.signal_bases,
        )

        output = GeneratedOutputService.record(
            db,
            course_id=course_id,
            user_id=user_id,
            output_type=OUTPUT_TYPE_EXAM_PLAN,
            content=document.model_dump_json(),
            # Truthfully null: Python produced this row. The analysis it was
            # built from carries the model that actually generated anything.
            model_used=None,
            generation_settings=applied_settings.model_dump_json(),
            generation_context=applied_context.model_dump_json(),
        )
        return ExamPlanCreation(output=output, document=document)

    @classmethod
    def _require_startable_exam_date(
        cls, db: Session, course_id: int, exam_date: date | None
    ) -> None:
        """Gate only the first plan on a usable exam date.

        Once a course has a plan, the date has served its purpose. A plan is a
        study resource and does not expire with the exam it was built for, so
        neither reopening one nor superseding one is blocked by the date having
        passed.
        """
        if cls.latest_plan(db, course_id) is not None:
            return
        if exam_date is None:
            raise ExamPlanExamDateMissingError(EXAM_DATE_MISSING_MESSAGE)
        if exam_date <= _today():
            raise ExamPlanExamDateNotFutureError(EXAM_DATE_NOT_FUTURE_MESSAGE)

    @classmethod
    def readout(
        cls,
        db: Session,
        course_id: int,
        output: GeneratedOutput,
        *,
        include_staleness: bool = True,
    ) -> ExamPlanReadout:
        """Read one stored plan back. Writes nothing and calls nothing.

        The stored document is parsed permissively and deliberately not
        revalidated, so a plan written by an older version still renders
        instead of failing the read.
        """
        content = (
            parse_json_object(
                output.content,
                field="content",
                table="generated_outputs",
                row_id=output.id,
            )
            or {}
        )
        staleness = ExamPlanStaleness()
        if include_staleness:
            staleness = cls.staleness(db, course_id, content)
        return ExamPlanReadout(output=output, content=content, staleness=staleness)

    @staticmethod
    def staleness(
        db: Session, course_id: int, content: Mapping[str, object]
    ) -> ExamPlanStaleness:
        """Compare a stored plan's fingerprint against the course as it is now.

        Nothing is written and no provider is reached. The mastery comparison
        uses the user recorded in the fingerprint rather than the reader, so an
        administrator reading an owner's plan does not see every read reported
        as new quiz results.
        """
        stored = content.get("fingerprint")
        if not isinstance(stored, dict):
            return ExamPlanStaleness()

        analysis_output_id = stored.get("analysis_output_id")
        mastery_user_id = stored.get("mastery_user_id")
        if not isinstance(analysis_output_id, int) or not isinstance(
            mastery_user_id, int
        ):
            return ExamPlanStaleness()

        candidates = ExamSourceAnalysisService.load_candidates(
            db, course_id, analysis_output_id
        )
        selected = stored.get("selected_topic_keys") or []
        priority = stored.get("high_priority_topic_keys") or []
        current = build_fingerprint(
            db,
            course_id,
            analysis_output_id=analysis_output_id,
            mastery_user_id=mastery_user_id,
            topic_index=build_topic_index(candidates),
            selected_topic_keys=selected if isinstance(selected, list) else [],
            high_priority_topic_keys=priority if isinstance(priority, list) else [],
        )
        reasons = compare_fingerprints(stored, current)
        return ExamPlanStaleness(
            is_stale=bool(reasons),
            requires_rescan=requires_rescan(reasons),
            stale_reasons=list(reasons),
        )

    @staticmethod
    def summarize(
        output: GeneratedOutput, *, current_output_id: int | None
    ) -> ExamPlanSummary:
        stored = (
            parse_json_object(
                output.content,
                field="content",
                table="generated_outputs",
                row_id=output.id,
            )
            or {}
        )
        topics = stored.get("topics")
        exam_date = stored.get("exam_date")
        return ExamPlanSummary(
            generated_output_id=output.id,
            analysis_output_id=int(stored.get("analysis_output_id") or 0),
            plan_version=int(stored.get("plan_version") or 0),
            supersedes_output_id=stored.get("supersedes_output_id"),
            created_at=output.created_at,
            exam_date=date.fromisoformat(exam_date)
            if isinstance(exam_date, str)
            else None,
            topic_count=len(topics) if isinstance(topics, list) else 0,
            selection_mode=str(stored.get("selection_mode") or "manual"),
            is_current=output.id == current_output_id,
        )


_MAX_POSITION = 2**62


def _today() -> date:
    """Today in UTC, the way every other dated decision in this system is made.

    Reading the server's local date would move the future-exam gate by a day
    depending on where the process happens to run.
    """
    return datetime.now(timezone.utc).date()


def _days_until(exam_date: date | None) -> int | None:
    """The planning horizon at generation time, or nothing when undated.

    Recorded so a later roadmap knows how much time the plan was built for.
    It is never recomputed on read: a stored plan describes the horizon it had,
    not the one the calendar has now.
    """
    if exam_date is None:
        return None
    return (exam_date - _today()).days


def _warnings(ranking: RankingResult, unmapped: int) -> list[str]:
    """What the plan could not see, stated rather than left to be inferred."""
    warnings: list[str] = []
    available = ranking.signals_available
    if not available.get("syllabus"):
        warnings.append(WARNING_NO_SYLLABUS)
    if not available.get("past_exam"):
        warnings.append(WARNING_NO_PAST_EXAMS)
    if not available.get("mastery"):
        warnings.append(WARNING_NO_MASTERY)
    if not available.get("material"):
        warnings.append(WARNING_SPARSE_MATERIAL)
    elif any(not topic.has_any_evidence for topic in ranking.topics):
        warnings.append(WARNING_SPARSE_MATERIAL)
    if unmapped:
        warnings.append(WARNING_UNMAPPED_MASTERY)
    return warnings
