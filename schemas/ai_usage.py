from enum import Enum


class GenerationType(str, Enum):
    STUDY_GUIDE = "study_guide"
    QUIZ = "quiz"
    FLASHCARD = "flashcard"
    AI_TUTOR = "ai_tutor"
    PROMPT_GENERATOR = "prompt_generator"
    COURSE_QA = "course_qa"


class ErrorCategory(str, Enum):
    PROVIDER_ERROR = "provider_error"
    INVALID_STRUCTURE = "invalid_structure"
    NO_READY_MATERIAL = "no_ready_material"
    EMPTY_RESPONSE = "empty_response"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION_ERROR = "authentication_error"
    UNKNOWN_ERROR = "unknown_error"
