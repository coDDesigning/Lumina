from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

UNSPECIFIED_COURSE_TITLE = "Unspecified course"
UNSPECIFIED_SUBJECT_AREA = "Unspecified subject area"

MAX_COURSE_TITLE_CHARS = 200
MAX_SUBJECT_AREA_CHARS = 100


class EducationLevel(str, Enum):
    HIGH_SCHOOL = "high_school"
    UNDERGRADUATE = "undergraduate"
    GRADUATE = "graduate"
    PROFESSIONAL_OTHER = "professional_other"
    UNSPECIFIED = "unspecified"


class MaterialKind(str, Enum):
    LECTURE_NOTES = "lecture_notes"
    SLIDES = "slides"
    TEXTBOOK = "textbook"
    SYLLABUS = "syllabus"
    ASSIGNMENT = "assignment"
    PAST_EXAM = "past_exam"
    ARTICLE = "article"
    NOTES = "notes"
    OTHER = "other"
    MIXED = "mixed"
    UNSPECIFIED = "unspecified"


class DocumentMaterialKind(str, Enum):
    LECTURE_NOTES = "lecture_notes"
    SLIDES = "slides"
    TEXTBOOK = "textbook"
    SYLLABUS = "syllabus"
    ASSIGNMENT = "assignment"
    PAST_EXAM = "past_exam"
    ARTICLE = "article"
    NOTES = "notes"
    OTHER = "other"
    UNSPECIFIED = "unspecified"


DOCUMENT_MATERIAL_KINDS: tuple[str, ...] = tuple(
    kind.value for kind in DocumentMaterialKind
)

EDUCATION_LEVEL_DIRECTIVES: dict[EducationLevel, str] = {
    EducationLevel.HIGH_SCHOOL: (
        "Write for a secondary-school learner. Favour foundational clarity, "
        "intuitive explanations, and relatable examples. Define technical terms "
        "on first use and assume no post-secondary prerequisites."
    ),
    EducationLevel.UNDERGRADUATE: (
        "Write for an undergraduate learner. Use standard disciplinary "
        "terminology, define unfamiliar terms where helpful, and balance "
        "conceptual explanation with worked application."
    ),
    EducationLevel.GRADUATE: (
        "Write for an advanced learner. Use precise domain terminology, assume "
        "solid foundational background, and emphasise nuance, methodological "
        "distinctions, and edge cases that the material actually supports."
    ),
    EducationLevel.PROFESSIONAL_OTHER: (
        "Write for a working professional or independent learner. Favour "
        "applied framing and practical relevance, and do not assume any "
        "particular academic curriculum."
    ),
    EducationLevel.UNSPECIFIED: (
        "No particular academic level is known. Use clear, broadly accessible "
        "explanations grounded in the supplied material. Do not assume an "
        "academic tier, and do not ask the learner what level they are."
    ),
}

MATERIAL_KIND_DIRECTIVES: dict[MaterialKind, str] = {
    MaterialKind.LECTURE_NOTES: (
        "The material is lecture notes, so it may be condensed and shorthand."
    ),
    MaterialKind.SLIDES: (
        "The material is presentation slides, so it is abbreviated, "
        "bullet-heavy, and dependent on context that may not be written down."
    ),
    MaterialKind.TEXTBOOK: (
        "The material is textbook content, so favour its definitions, "
        "explanations, and worked examples."
    ),
    MaterialKind.SYLLABUS: (
        "The material is a syllabus, so favour course structure, learning "
        "objectives, topic outline, and assessment information."
    ),
    MaterialKind.ASSIGNMENT: (
        "The material is assignment or problem-set content, so favour the "
        "tasks, requirements, and the reasoning they call for."
    ),
    MaterialKind.PAST_EXAM: (
        "The material is past examination content, so favour the question "
        "styles and the topics it actually assesses."
    ),
    MaterialKind.ARTICLE: (
        "The material is an article or paper, so favour its argument, "
        "evidence, and conclusions."
    ),
    MaterialKind.NOTES: (
        "The material is study notes, so it may be personal, partial, and "
        "unevenly detailed."
    ),
    MaterialKind.OTHER: (
        "The material does not fall into a standard category. Work from what "
        "it actually contains."
    ),
    MaterialKind.MIXED: (
        "The material combines several kinds of source, so its depth and "
        "formatting vary between sections."
    ),
    MaterialKind.UNSPECIFIED: (
        "The kind of material is not recorded. Work from what it actually "
        "contains rather than assuming a format."
    ),
}

GROUNDING_CLAUSE = (
    "This context sets depth, terminology, and assumed background only. It "
    "never overrides the supplied material and never licenses facts the "
    "material does not support."
)


class PromptContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    education_level: EducationLevel = EducationLevel.UNSPECIFIED
    course_title: str = Field(
        default=UNSPECIFIED_COURSE_TITLE, max_length=MAX_COURSE_TITLE_CHARS
    )
    subject_area: str = Field(
        default=UNSPECIFIED_SUBJECT_AREA, max_length=MAX_SUBJECT_AREA_CHARS
    )
    material_kind: MaterialKind = MaterialKind.UNSPECIFIED

    @field_validator("course_title", "subject_area")
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Text fields cannot contain NUL characters")
        return value.strip()

    def as_variables(self) -> dict[str, str]:
        return {
            "EDUCATION_LEVEL": (
                f"{self.education_level.value}. "
                f"{EDUCATION_LEVEL_DIRECTIVES[self.education_level]} "
                f"{GROUNDING_CLAUSE}"
            ),
            "COURSE_TITLE": self.course_title or UNSPECIFIED_COURSE_TITLE,
            "SUBJECT_AREA": self.subject_area or UNSPECIFIED_SUBJECT_AREA,
            "MATERIAL_KIND": (
                f"{self.material_kind.value}. "
                f"{MATERIAL_KIND_DIRECTIVES[self.material_kind]}"
            ),
        }
