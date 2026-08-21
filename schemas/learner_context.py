# schemas/learner_context.py
"""Learner context schema for adaptive, learner-aware AI prompt generation.

Enables Lumina prompt templates to adapt explanation depth, terminology,
prerequisite assumptions, and instructional framing to the student's profile
without lowering factual accuracy or fabricating curriculum details.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schemas.prompt_context import EducationLevel

__all__ = ["EducationLevel", "EDUCATION_LEVEL_DIRECTIVES", "LearnerContext"]


EDUCATION_LEVEL_DIRECTIVES: dict[EducationLevel, str] = {
    EducationLevel.HIGH_SCHOOL: (
        "Learner Profile: High-School Level\n"
        "- Prioritize foundational clarity, intuitive explanations, and relatable examples or analogies.\n"
        "- Avoid assuming university-level prerequisites or advanced specialized theory unless explicitly present in the source material.\n"
        "- Define technical terms simply and clearly upon first use."
    ),
    EducationLevel.UNDERGRADUATE: (
        "Learner Profile: Undergraduate Level\n"
        "- Maintain standard academic rigor, analytical depth, and conceptual clarity suited for higher education.\n"
        "- Use standard domain terminology and disciplinary concepts, defining new or complex terms where helpful.\n"
        "- Balance theoretical explanation with practical applications, problem-solving, and structured reasoning."
    ),
    EducationLevel.GRADUATE: (
        "Learner Profile: Graduate / Advanced Level\n"
        "- Provide comprehensive, in-depth, and rigorous academic analysis.\n"
        "- Use advanced domain terminology and assume solid foundational background knowledge supported by the material.\n"
        "- Emphasize nuanced synthesis, methodological distinctions, edge cases, and theoretical subtleties present in the source."
    ),
    EducationLevel.UNSPECIFIED: (
        "Learner Profile: General Learner (Unspecified Level)\n"
        "- Provide clear, accessible, and academically sound explanations.\n"
        "- Ground all explanations directly in the provided material without assuming a specific academic tier or prerequisite background."
    ),
    EducationLevel.PROFESSIONAL_OTHER: (
        "Learner Profile: General Learner\n"
        "- Provide clear, accessible, and academically sound explanations.\n"
        "- Ground all explanations directly in the provided material without assuming a specific academic tier or prerequisite background."
    ),
}


class LearnerContext(BaseModel):
    """Contextual profile of a learner passed to prompt templates."""

    model_config = ConfigDict(extra="forbid")

    education_level: EducationLevel = Field(
        default=EducationLevel.UNSPECIFIED,
        description="Target educational tier for adaptation.",
    )
    course_name: str | None = Field(
        default=None,
        max_length=200,
        description="Optional course or subject name.",
    )
    difficulty_level: str | None = Field(
        default=None,
        max_length=50,
        description="Optional difficulty preference (e.g. beginner, intermediate, advanced).",
    )
    current_topic: str | None = Field(
        default=None,
        max_length=200,
        description="Optional specific topic focus.",
    )
    study_objective: str | None = Field(
        default=None,
        max_length=200,
        description="Optional learning objective or intent (e.g. exam_focused, conceptual_mastery).",
    )
    detail_level: str | None = Field(
        default=None,
        max_length=50,
        description="Optional explanation depth preference.",
    )
    language: str | None = Field(
        default=None,
        max_length=50,
        description="Optional response language or locale.",
    )
    course_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured course metadata (e.g. syllabus, prerequisites).",
    )

    @field_validator(
        "course_name",
        "difficulty_level",
        "current_topic",
        "study_objective",
        "detail_level",
        "language",
    )
    @classmethod
    def reject_nul_characters(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value

    def render_directive(self) -> str:
        """Render the learner context into a deterministic prompt directive block."""
        parts: list[str] = [
            EDUCATION_LEVEL_DIRECTIVES.get(
                self.education_level,
                EDUCATION_LEVEL_DIRECTIVES[EducationLevel.UNSPECIFIED],
            )
        ]

        meta_lines: list[str] = []
        if self.course_name:
            meta_lines.append(f"- Course/Subject: {self.course_name}")
        if self.current_topic:
            meta_lines.append(f"- Current Topic: {self.current_topic}")
        if self.difficulty_level:
            meta_lines.append(f"- Target Difficulty: {self.difficulty_level}")
        if self.study_objective:
            meta_lines.append(f"- Study Objective: {self.study_objective}")
        if self.detail_level:
            meta_lines.append(f"- Preferred Detail: {self.detail_level}")
        if self.language:
            meta_lines.append(f"- Preferred Language: {self.language}")

        if meta_lines:
            parts.append("Context Details:\n" + "\n".join(meta_lines))

        return "\n\n".join(parts)

    def to_metadata_dict(self) -> dict[str, Any]:
        """Return a privacy-safe telemetry/observability dictionary without user content."""
        return {
            "education_level": self.education_level.value,
            "has_course_name": bool(self.course_name),
            "has_topic": bool(self.current_topic),
            "has_difficulty": bool(self.difficulty_level),
            "has_study_objective": bool(self.study_objective),
            "has_detail_level": bool(self.detail_level),
            "has_language": bool(self.language),
        }
