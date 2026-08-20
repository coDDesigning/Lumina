from enum import Enum


class GenerationType(str, Enum):
    STUDY_GUIDE = "study_guide"
    QUIZ = "quiz"
    FLASHCARD = "flashcard"
    AI_TUTOR = "ai_tutor"
    PROMPT_GENERATOR = "prompt_generator"
    COURSE_QA = "course_qa"
    QUIZ_GRADING = "quiz_grading"


class ErrorCategory(str, Enum):
    PROVIDER_ERROR = "provider_error"
    INVALID_STRUCTURE = "invalid_structure"
    NO_READY_MATERIAL = "no_ready_material"
    NO_RELEVANT_MATERIAL = "no_relevant_material"
    RETRIEVAL_ERROR = "retrieval_error"
    EMPTY_RESPONSE = "empty_response"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTHENTICATION_ERROR = "authentication_error"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    UNKNOWN_ERROR = "unknown_error"
