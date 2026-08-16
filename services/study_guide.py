from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import DocumentChunk, GeneratedOutput, UploadedDocument

from pydantic import ValidationError

from schemas.study_guide import StudyGuideResponse
from services.text_generation import TextGenerationError, TextGenerationProvider


class StudyGuideGenerationError(RuntimeError):
    """Study guide generation failed."""


class StudyGuideService:
    PROMPT_PATH = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "study_guide_prompt.txt"
    )

    @staticmethod
    def get_course_material(db: Session, course_id: int) -> str:
        chunks = db.scalars(
            select(DocumentChunk.text)
            .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
            .where(
                DocumentChunk.course_id == course_id,
                UploadedDocument.status == "ready",
            )
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        ).all()

        return "\n\n".join(text.strip() for text in chunks if text.strip())

    @classmethod
    def build_prompt(cls, course_material: str) -> str:
        prompt_template = cls.PROMPT_PATH.read_text(encoding="utf-8")
        return prompt_template.replace("{{TEXT}}", course_material)

    @classmethod
    def generate(
        cls,
        db: Session,
        course_id: int,
        provider: TextGenerationProvider,
    ) -> StudyGuideResponse:
        course_material = cls.get_course_material(db, course_id)

        if not course_material:
            raise StudyGuideGenerationError("No ready course material is available.")

        prompt = cls.build_prompt(course_material)

        try:
            result = provider.generate_json(prompt)
        except TextGenerationError as exc:
            raise StudyGuideGenerationError("Text generation provider failed.") from exc

        try:
            return StudyGuideResponse.model_validate(result)
        except ValidationError as exc:
            raise StudyGuideGenerationError(
                "Generated study guide has an invalid structure."
            ) from exc

    @staticmethod
    def save_generated_output(
        db: Session,
        course_id: int,
        study_guide: StudyGuideResponse,
    ) -> GeneratedOutput:
        generated_output = GeneratedOutput(
            course_id=course_id,
            output_type="study_guide",
            content=study_guide.model_dump_json(),
        )
        db.add(generated_output)
        db.flush()
        db.refresh(generated_output)
        db.commit()

        return generated_output
