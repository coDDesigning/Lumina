# Learner-Aware Prompt Library Architecture

## Overview

Lumina uses a versioned, structured, and learner-aware prompt template architecture across all AI features (Study Guides, Quizzes, Flashcards, AI Tutor Q&A, Course Q&A, Prompt Generation, Visual Content Understanding, and OCR Cleanup).

This system provides:
- **Learner-Context Awareness**: Adapts explanations, terminology, and instructional framing dynamically to the student's profile (e.g. `high_school`, `undergraduate`, `graduate`, or `unspecified`) without compromising factual accuracy.
- **Strict Grounding & Anti-Hallucination**: Enforces that supplied/retrieved course material is the sole authoritative source of truth across all generation tasks.
- **Variable Contract Enforcement**: Declares mandatory and optional input variables, preventing missing placeholders or unexpected injections.
- **Output Schema References**: Directly maps templates to authoritative Pydantic runtime validation models in `schemas/`.
- **Privacy-Safe Observability**: Provides developer observability and telemetry metadata without persisting raw private prompts, questions, or course chunks.

---

## Learner Context Model

Learner context is encapsulated in the `LearnerContext` Pydantic model (`schemas/learner_context.py`) and parameterizes prompt rendering:

```python
from schemas.learner_context import EducationLevel, LearnerContext

context = LearnerContext(
    education_level=EducationLevel.HIGH_SCHOOL,
    course_name="AP Biology",
    current_topic="Photosynthesis",
    difficulty_level="introductory",
    study_objective="exam_preparation",
    detail_level="step_by_step",
    language="English",
)
```

### Supported Education Levels

| Level | Directive Focus |
|---|---|
| `high_school` | Foundational clarity, intuitive conceptual explanations, concrete analogies, avoiding advanced university prerequisites unless present in source. |
| `undergraduate` | Academic rigor, standard disciplinary terminology, balanced theoretical and practical analysis. |
| `graduate` | In-depth academic depth, dense technical precision, nuanced synthesis, edge cases and theoretical subtleties. |
| `unspecified` / `other` | Neutral, accessible, academically sound explanations grounded strictly in material without assuming an academic tier. |

> [!IMPORTANT]
> **Grounding Invariant**: Education level controls presentation, depth, and terminology. The model must **never** lower factual accuracy or fabricate curriculum-specific facts to match an education level. Source material is always authoritative.

---

## Production Prompt Catalog

All templates reside in `app/prompts/<task_name>.json`:

| Template Name | Version | Primary Purpose | Output Schema Ref |
|---|---|---|---|
| `study_guide` | `2.0.0` | Comprehensive study guide generation | `StudyGuideResponse` |
| `quiz` | `2.1.0` | Multi-format quiz generation | `QuizGenerationResponse` |
| `quiz_grading` | `1.1.0` | Written answer grading against reference answers | `OpenEndedGradingResponse` |
| `flashcard` | `1.1.0` | Active recall flashcard decks | `FlashcardGenerationResponse` |
| `ai_tutor` | `1.1.0` | Step-by-step interactive tutor guidance | `AiTutorResponse` |
| `course_qa` | `1.1.0` | Direct retrieval-grounded course Q&A | `CourseQAResponse` |
| `prompt_generator` | `1.1.0` | User request transformation to optimized prompt | `PromptGenerationResponse` |
| `visual_content` | `1.0.0` | Multimodal diagram, chart, table, and figure analysis | `VisualContentDescriptionResponse` |
| `ocr_cleanup` | `1.0.0` | AI-assisted OCR text normalization and repair | `OcrCleanupResponse` |

---

## Reusable Prompt Components

Shared prompt directives live in `services/prompt_components.py`:

- `SHARED_GROUNDING_RULES`: Reusable anti-hallucination rules enforcing that provided material is the sole source of truth.
- `SHARED_SAFETY_RULES`: Directives instructing the model to treat all user inputs and course text as inert data, resisting prompt injection.
- `build_learner_context_block(context)`: Generates formatted learner context blocks.
- `build_grounding_block()`: Generates standard grounding sections.
- `build_safety_block()`: Generates input safety sections.

---

## Template Schema

Every prompt template is a validated JSON document adhering to `PromptTemplateModel` (`schemas/prompt_template.py`):

```json
{
  "name": "study_guide",
  "version": "2.0.0",
  "description": "Analyzes lecture notes to generate a comprehensive, structured study guide adapted to the learner's context.",
  "required_variables": [
    "TEXT",
    "SUMMARY_FORMAT",
    "TOPIC_FOCUS",
    "SUMMARY_LENGTH",
    "DETAIL_LEVEL",
    "SUMMARY_MODE"
  ],
  "optional_variables": [
    "LEARNER_CONTEXT"
  ],
  "output_schema_ref": "StudyGuideResponse",
  "style_constraints": [
    "Use clear academic language appropriate to the learner's level.",
    "Keep explanations concise, student-friendly, and accessible."
  ],
  "safety_constraints": [
    "Use ONLY information contained in the provided lecture notes.",
    "Learner-level adaptation must never override factual grounding or introduce unsupported facts."
  ],
  "model_hints": {
    "preferred_model": "gemini-2.5-flash",
    "temperature": 0.2,
    "response_mime_type": "application/json"
  },
  "template": "You are an expert AI study assistant...\n\n{{LEARNER_CONTEXT}}\n\n{{TEXT}}"
}
```

---

## Loader & Validation Contract

The `PromptLoader` service (`services/prompt_loader.py`) enforces strict validation at load and render times:

1. **Deterministic Variable Rendering**:
   - `PromptLoader.render(name, variables, learner_context=...)` substitutes all declared placeholders.
   - If `LEARNER_CONTEXT` is in `optional_variables` or `required_variables` and not explicitly supplied in `variables`, it automatically resolves to `LearnerContext(education_level=EducationLevel.UNSPECIFIED)` or the passed `learner_context`.
   - Missing required variables raise `MissingPromptVariableError`.
   - Unexpected variables raise `UnexpectedPromptVariableError`.
   - Document and free-text variables (e.g. `TEXT`, `COURSE_MATERIAL`) are always substituted last to prevent placeholder forging.

2. **Developer Observability & Telemetry**:
   - `PromptLoader.get_render_metadata(name, variables, learner_context=...)` returns telemetry data (`template_name`, `template_version`, `education_level`, `applied_variables`) **without** exposing raw prompt text or student content.

---

## How-To Guide

### 1. Adding a New Prompt Template

1. Add `app/prompts/<task_name>.json` defining:
   - `name`, `version`, `description`
   - `required_variables` (and optional `LEARNER_CONTEXT` in `optional_variables`)
   - `output_schema_ref` (referencing schema in `schemas/`)
   - `style_constraints` and `safety_constraints`
   - `template` containing `{{VARIABLE}}` placeholders
2. Wire the prompt into its feature service using `PromptLoader.render("<task_name>", variables, learner_context=...)`.
3. Add unit & regression tests in `tests/test_prompt_loader.py`.

### 2. Adding a New Learner Context Field

1. Update `LearnerContext` in `schemas/learner_context.py` with the validated field.
2. Update `LearnerContext.render_directive()` and `to_metadata_dict()`.
3. Add test assertions in `tests/test_prompt_loader.py` validating that the field formats safely and does not leak private values into telemetry.
