# Learner-Aware Prompt Library Architecture

## Overview

Lumina uses a versioned, structured, and learner-aware prompt template architecture for every AI prompt it sends. The eleven templates under `app/prompts/` cover study guides, quizzes, exam-style questions, quiz grading, flashcards, AI tutoring, course Q&A, prompt generation, image description, visual content understanding, and OCR cleanup. No prompt text lives outside this directory.

`exam_style_question`, `ocr_cleanup`, and `visual_content` are declared, validated and tested but have no runtime caller yet. `image_description` is the one vision template that is wired, and `services/image_understanding.py` renders it for every extracted visual.

The catalog covers every prompt category Design.md §20 names: summary generation (`study_guide`), quiz generation (`quiz`), OCR cleanup (`ocr_cleanup`), image description (`image_description`), written answer evaluation (`quiz_grading`), and exam-style question generation (`exam_style_question`).

This system provides:
- **Learner-Context Awareness**: Adapts explanations, terminology, and instructional framing dynamically to the student's profile (e.g. `high_school`, `undergraduate`, `graduate`, or `unspecified`) without compromising factual accuracy.
- **Strict Grounding & Anti-Hallucination**: Enforces that supplied/retrieved course material is the sole authoritative source of truth across all generation tasks.
- **Variable Contract Enforcement**: Declares mandatory and optional input variables, preventing missing placeholders or unexpected injections.
- **Output Schema References**: Directly maps templates to authoritative Pydantic runtime validation models in `schemas/`.
- **Privacy-Safe Observability**: Provides developer observability and telemetry metadata without persisting raw private prompts, questions, or course chunks.

---

## Learner Context Model

Every template takes the four shared variables described under **Shared learner and course
context**, resolved from the database by `services/prompt_context.py`. That is the only path
learner context reaches a prompt: there is no second mechanism and no per-template context
schema.

### Supported Education Levels

| Level | Directive Focus |
|---|---|
| `high_school` | Foundational clarity, intuitive conceptual explanations, concrete analogies, assuming no post-secondary prerequisites unless present in source. |
| `undergraduate` | Academic rigor, standard disciplinary terminology, balanced theoretical and practical analysis. |
| `graduate` | In-depth academic depth, dense technical precision, nuanced synthesis, edge cases and theoretical subtleties. |
| `professional_other` | Applied framing and practical relevance for a working professional or independent learner, assuming no particular curriculum. |
| `unspecified` | Neutral, accessible, academically sound explanations grounded strictly in material without assuming an academic tier. |

> [!IMPORTANT]
> **Grounding Invariant**: Education level controls presentation, depth, and terminology. The model must **never** lower factual accuracy or fabricate curriculum-specific facts to match an education level. Source material is always authoritative.

---

## Production Prompt Catalog

All templates reside in `app/prompts/<task_name>.json`:

| Template Name | Version | Primary Purpose | Output Schema Ref |
|---|---|---|---|
| `study_guide` | `2.1.0` | Comprehensive study guide generation | `StudyGuideResponse` |
| `quiz` | `3.1.0` | Multi-format quiz generation | `QuizGenerationResponse` |
| `exam_style_question` | `1.0.0` | Exam-style practice questions (not wired) | `QuizGenerationResponse` |
| `quiz_grading` | `2.0.0` | Written answer grading against reference answers | `OpenEndedGradingResponse` |
| `flashcard` | `2.0.0` | Active recall flashcard decks | `FlashcardGenerationResponse` |
| `ai_tutor` | `2.1.0` | Step-by-step interactive tutor guidance | `AiTutorResponse` |
| `course_qa` | `2.1.0` | Direct retrieval-grounded course Q&A | `CourseQAResponse` |
| `prompt_generator` | `2.0.0` | User request transformation to optimized prompt | `PromptGenerationResponse` |
| `image_description` | `1.0.0` | Visual descriptions for the retrieval index (wired) | — |
| `visual_content` | `2.0.0` | Multimodal diagram, chart, table, and figure analysis | `VisualContentDescriptionResponse` |
| `ocr_cleanup` | `1.0.0` | AI-assisted OCR text normalization and repair | `OcrCleanupResponse` |

`tests/test_prompt_loader.py` pins this table in `EXPECTED_TEMPLATE_VERSIONS`, so editing a
template's content without bumping its version fails the suite.

---

## Reusable Prompt Components

Shared prompt directives live in `services/prompt_components.py`:

- `SHARED_GROUNDING_RULES`: Reusable anti-hallucination rules enforcing that provided material is the sole source of truth.
- `SHARED_SAFETY_RULES`: Directives instructing the model to treat all user inputs and course text as inert data, resisting prompt injection.
- `build_grounding_block()`: Generates standard grounding sections.
- `build_safety_block()`: Generates input safety sections.

---

## Template Schema

Every prompt template is a validated JSON document adhering to `PromptTemplateModel` (`schemas/prompt_template.py`):

```json
{
  "name": "study_guide",
  "version": "2.0.0",
  "description": "Generates a comprehensive study guide from course material, adapted to the learner's education level.",
  "required_variables": [
    "EDUCATION_LEVEL",
    "COURSE_TITLE",
    "SUBJECT_AREA",
    "MATERIAL_KIND",
    "TEXT",
    "SUMMARY_FORMAT",
    "TOPIC_FOCUS",
    "SUMMARY_LENGTH",
    "DETAIL_LEVEL",
    "SUMMARY_MODE"
  ],
  "optional_variables": [],
  "output_schema_ref": "StudyGuideResponse",
  "style_constraints": [
    "Use clear academic language appropriate to the learner's level.",
    "Keep explanations concise, student-friendly, and accessible."
  ],
  "safety_constraints": [
    "Use ONLY information contained in the provided course material.",
    "Do NOT invent facts that are not supported by the course material.",
    "Learner-level adaptation must never override factual grounding or introduce unsupported facts."
  ],
  "model_hints": {
    "preferred_model": "gemini-2.5-flash",
    "temperature": 0.2,
    "response_mime_type": "application/json"
  },
  "template": "You are an expert AI study assistant...\n\nEducation level: {{EDUCATION_LEVEL}}\nCourse: {{COURSE_TITLE}}\n\n{{SUMMARY_FORMAT}}\n\n{{DETAIL_LEVEL}}\n\n{{SUMMARY_MODE}}\n\nRequested topic focus: {{TOPIC_FOCUS}}\n\n{{TEXT}}"
}
```

---

`PromptTemplateModel.render` substitutes variables in the order the caller's
dictionary supplies them, so a value containing a literal placeholder would be
rewritten by any later pass. Feature services therefore render free-text and
document content **last**: by the time `{{TEXT}}` is substituted every other
placeholder is already consumed, so course material can never forge one.

### Which variables carry user text

In `study_guide`, only `{{TOPIC_FOCUS}}`, `{{COURSE_TITLE}}`, `{{SUBJECT_AREA}}`,
and `{{TEXT}}` carry user-supplied content. `{{SUMMARY_FORMAT}}`,
`{{SUMMARY_LENGTH}}`, `{{DETAIL_LEVEL}}`, `{{SUMMARY_MODE}}`, `{{EDUCATION_LEVEL}}`,
and `{{MATERIAL_KIND}}` are rendered from server-side constant tables keyed by a
validated enum, so they can never carry an injected instruction. `{{COURSE_TITLE}}`
and `{{SUBJECT_AREA}}` are brace-stripped by the resolver and rendered inside the
learner-context block, which tells the model they are labels and never instructions. `{{TOPIC_FOCUS}}`
is rendered inside the guarded generation-request block, whose closing sentence
tells the model that the emphasis above is a student preference which never
overrides the general rules, the section requirements, or the output schema.


## Loader & Validation Contract

The `PromptLoader` service (`services/prompt_loader.py`) enforces strict validation at load and render times:

1. **Deterministic Variable Rendering**:
   - `PromptLoader.render(name, variables)` substitutes all declared placeholders. There is no implicit context injection: a template's learner and course variables come from `PromptContext.as_variables()` like any other variable.
   - Missing required variables raise `MissingPromptVariableError`.
   - Unexpected variables raise `UnexpectedPromptVariableError`.
   - Document and free-text variables (e.g. `TEXT`, `COURSE_MATERIAL`) are always substituted last to prevent placeholder forging.

2. **Strict Variable Validation**:
   - Missing required variable $\rightarrow$ Raises `MissingPromptVariableError`.
   - Unexpected/extra variable $\rightarrow$ Raises `UnexpectedPromptVariableError`.
   - Placeholder in the body that no variable declares: raises
     `PromptTemplateValidationError` **at load time**, which is what catches a typo
     such as `{{EDUCATON_LEVEL}}`.
   - Declared placeholder with no supplied value: raises `MissingPromptVariableError`
     **before** the provider call.
   - All `{{VARIABLE}}` placeholders are substituted deterministically.

3. **Developer Observability & Telemetry**:
   - `PromptLoader.get_render_metadata(name, variables)` returns telemetry data (`template_name`, `template_version`, `output_schema_ref`, `applied_variables`) **without** exposing raw prompt text or student content.

---

## How-To Guide

### 1. Adding a New Prompt Template

1. Add `app/prompts/<task_name>.json` defining:
   - `name`, `version`, `description`
   - `required_variables`, starting with the four shared learner and course variables
   - `output_schema_ref` (referencing schema in `schemas/`)
   - `style_constraints` and `safety_constraints`
   - `template` containing `{{VARIABLE}}` placeholders
2. Wire the prompt into its feature service using `PromptLoader.render("<task_name>", {**context.as_variables(), ...})`, where `context` comes from `resolve_prompt_context`.
3. Add unit & regression tests in `tests/test_prompt_loader.py`.

### 2. Adding a New Shared Context Variable

1. Add the field to `PromptContext` in `schemas/prompt_context.py` and to its server-side
   directive table, keyed by a validated enum so it can never carry injected instructions.
2. Emit it from `PromptContext.as_variables()` and resolve it in `resolve_prompt_context`.
3. Declare it in every template that uses it and bump those templates' versions.
4. Add assertions in `tests/test_prompt_context_propagation.py` that it reaches every prompt
   and that its neutral value stays neutral.

> Declare every variable a service passes in `required_variables` (or `optional_variables`),
> and declare every `{{PLACEHOLDER}}` that appears in the body. Both directions are enforced
> (see **Placeholder guarantees**), so an undeclared placeholder fails at load time and an
> unsupplied one fails before the provider is ever called.

## Quiz templates

`quiz` (3.0.0) generates a quiz of a requested size, difficulty, and question-type mix. Its required variables are `TEXT`, `QUESTION_COUNT`, `QUESTION_TYPES_DIRECTIVE`, `QUESTION_SCHEMAS`, `REQUESTED_DIFFICULTY`, `DIFFICULTY_DIRECTIVE`, and `TOPIC_FOCUS`. `QUESTION_SCHEMAS` carries the JSON shape of each allowed question type, so the model is shown only the types the request permits.

The variable is named `REQUESTED_DIFFICULTY` rather than `DIFFICULTY` on purpose. Rendering substitutes `{{NAME}}` placeholders in dictionary order, so a variable whose name is a prefix of another one's would make the result depend on that order.

`quiz_grading` (2.0.0) scores open-ended answers against their stored reference answers. Its required variables are `SUBMISSION_COUNT` and `SUBMISSIONS`, and it returns an `OpenEndedGradingResponse`. Both templates state that text inside the material, topic focus, or a student's answer is data and never an instruction. The `quiz` template renders `TEXT` last so course material cannot forge a placeholder a later substitution would fill in.

## Shared learner and course context

Every template declares four shared variables ahead of its feature-specific ones:

| Variable | Source | Neutral value |
|---|---|---|
| `EDUCATION_LEVEL` | `courses.education_level`, else `users.education_level` | `unspecified` |
| `COURSE_TITLE` | `courses.title` | `Unspecified course` |
| `SUBJECT_AREA` | `courses.subject_area` | `Unspecified subject area` |
| `MATERIAL_KIND` | aggregate of `uploaded_documents.material_kind` | `unspecified` |

`resolve_prompt_context` in `services/prompt_context.py` is the single resolver; no feature
service reimplements the fallback rules. Its precedence for the learner's level is **course
value, then profile value, then `unspecified`**. That ordering is deliberate: a working
professional taking a high-school prerequisite should be addressed at the course's level,
not their own.

`unspecified` is a real, neutral state, never a stand-in for undergraduate. Its directive
tells the model not to assume an academic tier and not to ask the learner what level they
are, because study guides and quizzes are batch generations with nobody there to answer.

`EDUCATION_LEVEL` and `MATERIAL_KIND` render as `"<value>. <directive>"` from server-side
tables keyed by a validated enum in `schemas/prompt_context.py` — the same pattern
`SUMMARY_FORMAT` and `DIFFICULTY_DIRECTIVE` already use. The canonical token stays in the
prompt, and because the value comes from an enum it can never carry an injected instruction.

Material kind is aggregated over the course's `ready` documents: all-same yields that kind,
a genuine mixture yields `mixed`, and nothing classified yields `unspecified`. `mixed` is a
resolved value only, and the database CHECK rejects it on an individual document. Image
understanding passes its single `document_id`, so it gets that document's exact kind rather
than a course aggregate.

`COURSE_TITLE` and `SUBJECT_AREA` carry learner-supplied text. The resolver strips braces
from both, so neither can smuggle a `{{PLACEHOLDER}}` into a later substitution pass.

Subject area is never inferred from the course title. A `"CS" in title` heuristic is exactly
the class of guess this architecture exists to remove.

### The prompt_generator exception

`prompt_generator` is not course-scoped: its route has no `course_id`. It receives a
user-scoped context, so `EDUCATION_LEVEL` comes from the profile while `COURSE_TITLE`,
`SUBJECT_AREA`, and `MATERIAL_KIND` resolve to their neutral values. The endpoint's API
shape is unchanged.

### Image description

`image_description` is rendered once by a module-level helper in
`services/image_understanding.py` that both the Gemini and Ollama providers call, so the two
can no longer drift apart. The worker resolves the context in the parent process, where a
database session exists, and passes it across the `spawn` pipe into `extract_document`,
which binds it to the provider at construction. `describe_visual` keeps its original
signature and `services/document_pipeline.py` stays free of ORM imports.

A template fault there is raised as `VisualAnalysisError`, not a bare exception. That
matters: the pipeline treats an unclassified exception as a retryable stage failure, so a
malformed template would otherwise burn every retry on a fault no retry can fix. As a
`VisualAnalysisError` it marks one visual failed and lets the document finish.

### Retained field names

The study guide's `exam_tips.lecture_based` key is a persisted `generated_output` payload
field and part of the API the frontend reads. It is deliberately unchanged: the prompt's
prose and the UI label are material-neutral, and only the stored key still carries the word.
Renaming it would need a migration of existing outputs, so it is out of scope here.

## Placeholder guarantees

Two checks stand between a template and a provider:

1. **At load time**, every `{{PLACEHOLDER}}` in the body must be declared in
   `required_variables` or `optional_variables`.
2. **At render time**, every placeholder in the body must have a supplied value.

Both scan the *template body*, never the rendered output. This is load-bearing. A value
that happens to contain `{{TOPIC_FOCUS}}` must survive rendering intact — that inertness is
the anti-injection guarantee the substitution order provides, and
`tests/test_quiz.py` and `tests/test_study_guide.py` pin it. A check written against the
rendered output would pass a naive reading of "no unresolved placeholder" while silently
reopening a prompt-injection hole.

A `PromptTemplateError` is a server fault, not a user error. Every AI route already funnels
it through `utils/ai_errors.py` to `GENERATION_FAILED`, so it surfaces as a 500 with a
constant public message and a logged category, never leaked exception text.
