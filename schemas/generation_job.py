"""What a client is told about a generation it is no longer waiting on."""

import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.models import GenerationJob


def _vendor_of(identifier: str | None) -> str | None:
    """The vendor half of a ``provider:model`` attribution, or nothing.

    Only the vendor is compared. Model spellings drift between the catalogue and
    what a provider reports back, so comparing whole identifiers would announce
    an outage that never happened.
    """
    if not identifier or ":" not in identifier:
        return None
    vendor = identifier.split(":", 1)[0].strip().casefold()
    return vendor or None


def _requested_model_of(job: GenerationJob) -> str | None:
    """The model the run was queued with, read permissively.

    The payload is stored JSON, so a row a newer release can no longer parse
    loses its attribution rather than failing the whole panel.
    """
    try:
        payload = json.loads(job.request_payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    model = payload.get("model")
    return model if isinstance(model, str) and model else None


def _produced_model_of(job: GenerationJob) -> str | None:
    if job.quiz is not None and job.quiz.model_used:
        return job.quiz.model_used
    if job.generated_output is not None and job.generated_output.model_used:
        return job.generated_output.model_used
    return None


GenerationJobStatus = Literal["queued", "running", "succeeded", "failed"]
GenerationJobType = Literal[
    "generate_study_guide", "generate_quiz", "generate_flashcard"
]


class GenerationJobView(BaseModel):
    """One row of the generation panel.

    The result identifiers are deliberately separate rather than one polymorphic
    field: the client opens a study guide and a quiz through different reads,
    and a single ``result_id`` would make it guess which.
    """

    id: int
    job_type: GenerationJobType
    status: GenerationJobStatus
    attempt_count: int
    max_attempts: int
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Present only on a failure, and already reduced to something showable: the
    # service truncates and strips it before it is ever stored.
    error_code: str | None = None
    error_message: str | None = None
    generated_output_id: int | None = None
    quiz_id: int | None = None
    # The model the student asked for, and the one that answered when a vendor
    # outage sent the work elsewhere. ``fallback_model`` is null whenever the
    # chosen vendor answered, or whenever either side is unknown -- silence is
    # the only honest answer when the comparison cannot be made.
    requested_model: str | None = None
    fallback_model: str | None = None

    @classmethod
    def from_job(cls, job: GenerationJob) -> "GenerationJobView":
        requested = _requested_model_of(job)
        produced = _produced_model_of(job)
        requested_vendor = _vendor_of(requested)
        produced_vendor = _vendor_of(produced)

        return cls(
            id=job.id,
            job_type=job.job_type,  # type: ignore[arg-type]
            status=job.status,  # type: ignore[arg-type]
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_code=job.last_error_code,
            error_message=job.last_error_message,
            generated_output_id=job.generated_output_id,
            quiz_id=job.quiz_id,
            requested_model=requested,
            fallback_model=(
                produced
                if requested_vendor is not None
                and produced_vendor is not None
                and requested_vendor != produced_vendor
                else None
            ),
        )


class GenerationJobAccepted(BaseModel):
    """The handle returned the moment work is queued, before anything runs."""

    job_id: int = Field(description="Poll this through the generation job read.")
    status: GenerationJobStatus
