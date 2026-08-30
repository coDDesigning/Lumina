"""What a client is told about a generation it is no longer waiting on."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.models import GenerationJob

GenerationJobStatus = Literal["queued", "running", "succeeded", "failed"]
GenerationJobType = Literal["generate_study_guide", "generate_quiz"]


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

    @classmethod
    def from_job(cls, job: GenerationJob) -> "GenerationJobView":
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
        )


class GenerationJobAccepted(BaseModel):
    """The handle returned the moment work is queued, before anything runs."""

    job_id: int = Field(description="Poll this through the generation job read.")
    status: GenerationJobStatus
