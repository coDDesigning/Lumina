# Prompt Template Management Architecture

## Overview

Lumina uses a versioned, structured JSON template architecture for every AI prompt it sends. The nine templates under `app/prompts/` cover study guides, quizzes, quiz grading, flashcards, AI tutoring, course Q&A, prompt generation, image description, and OCR cleanup. No prompt text lives outside this directory.

`ocr_cleanup` is declared and tested but has no runtime caller yet: the pipeline records an `ocr` extraction method but performs no LLM cleanup step. Wiring one is a separate change.

This replaces ad-hoc plain-text templates with structured templates that explicitly declare:
- **Required & Optional Input Variables** (preventing silent rendering failures)
- **Output Schema Reference** (linking templates to authoritative Pydantic runtime models)
- **Style Constraints** (formatting and tone instructions)
- **Safety & Truthfulness Constraints** (anti-hallucination rules)
- **Model Hints** (temperature, preferred model, response format)

## Template Schema

All templates are stored under `app/prompts/<task_name>.json` and must adhere to the `PromptTemplateModel` schema:

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
    "Use clear academic English.",
    "Keep explanations concise and student-friendly."
  ],
  "safety_constraints": [
    "Use ONLY information contained in the provided course material.",
    "Do NOT invent facts that are not supported by the course material."
  ],
  "model_hints": {
    "preferred_model": "gemini-2.5-flash",
    "temperature": 0.2,
    "response_mime_type": "application/json"
  },
  "template": "You are an expert teaching assistant...\n\n{{SUMMARY_FORMAT}}\n\n{{DETAIL_LEVEL}}\n\n{{SUMMARY_MODE}}\n\nRequested topic focus: {{TOPIC_FOCUS}}\n\n{{TEXT}}"
}
```

### Variable substitution order

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


## Loader & Validation Rules

The `PromptLoader` service (`services/prompt_loader.py`) enforces strict validation at load and render times:

1. **Fail-Fast Syntax & Metadata Check**:
   - Validates JSON format (`PromptTemplateSyntaxError` on malformed JSON).
   - Validates schema structure (`PromptTemplateValidationError` on missing fields).
   - Verifies template existence (`PromptTemplateNotFoundError` on missing files).

2. **Strict Variable Validation**:
   - Missing required variable $\rightarrow$ Raises `MissingPromptVariableError`.
   - Unexpected/extra variable $\rightarrow$ Raises `UnexpectedPromptVariableError`.
   - Placeholder in the body that no variable declares: raises
     `PromptTemplateValidationError` **at load time**, which is what catches a typo
     such as `{{EDUCATON_LEVEL}}`.
   - Declared placeholder with no supplied value: raises `MissingPromptVariableError`
     **before** the provider call.
   - All `{{VARIABLE}}` placeholders are substituted deterministically.

3. **Authoritative Runtime Output Validation**:
   - Pydantic models in `schemas/` remain the single source of truth for runtime validation of the LLM JSON response.

## Adding a New Prompt Template

To add a new AI generation task:

1. **Create the Template File**:
   Add `app/prompts/<new_task>.json` declaring `name`, `version`, `required_variables`, `output_schema_ref`, and `template`.

2. **Render in Service**:
   ```python
   from services.prompt_loader import PromptLoader

   prompt = PromptLoader.render("new_task", {"REQUIRED_VAR": value})
   ```

> Declare every variable a service passes in `required_variables` (or `optional_variables`),
> and declare every `{{PLACEHOLDER}}` that appears in the body. Both directions are enforced
> (see **Placeholder guarantees**), so an undeclared placeholder fails at load time and an
> unsupplied one fails before the provider is ever called.

3. **Test the Template**:
   Add test assertions in `tests/test_prompt_loader.py` validating that the template parses, renders variables, and enforces constraints.

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
