# Prompt Template Management Architecture

## Overview

Lumina uses a versioned, structured JSON template architecture for all AI prompt generation workflows (Study Guides, Quizzes, Flashcards, AI Tutor Q&A, Prompt Generation, OCR cleanup, etc.).

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
  "version": "1.2.0",
  "description": "Comprehensive university study guide generation based on lecture notes.",
  "required_variables": [
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
    "Use ONLY information contained in the provided lecture notes.",
    "Do NOT invent facts that are not supported by the lecture notes."
  ],
  "model_hints": {
    "preferred_model": "gemini-2.5-flash",
    "temperature": 0.2,
    "response_mime_type": "application/json"
  },
  "template": "You are an expert university teaching assistant...\n\n{{SUMMARY_FORMAT}}\n\n{{DETAIL_LEVEL}}\n\n{{SUMMARY_MODE}}\n\nRequested topic focus: {{TOPIC_FOCUS}}\n\n{{TEXT}}"
}
```

### Variable substitution order

`PromptTemplateModel.render` substitutes variables in the order the caller's
dictionary supplies them, so a value containing a literal placeholder would be
rewritten by any later pass. Feature services therefore render free-text and
document content **last**: by the time `{{TEXT}}` is substituted every other
placeholder is already consumed, so course material can never forge one.

### Which variables carry user text

In `study_guide`, only `{{TOPIC_FOCUS}}` and `{{TEXT}}` carry user-supplied
content. `{{SUMMARY_FORMAT}}`, `{{SUMMARY_LENGTH}}`, `{{DETAIL_LEVEL}}`, and
`{{SUMMARY_MODE}}` are rendered from server-side constant tables keyed by a
validated enum, so they can never carry an injected instruction. `{{TOPIC_FOCUS}}`
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

> Declare every variable a service passes in `required_variables` (or `optional_variables`).
> `render` substitutes only the keys it is given, so an `optional_variables` entry the
> service omits leaves a literal `{{PLACEHOLDER}}` in the prompt sent to the model.

3. **Test the Template**:
   Add test assertions in `tests/test_prompt_loader.py` validating that the template parses, renders variables, and enforces constraints.

## Quiz templates

`quiz` (2.0.0) generates a quiz of a requested size, difficulty, and question-type mix. Its required variables are `TEXT`, `QUESTION_COUNT`, `QUESTION_TYPES_DIRECTIVE`, `QUESTION_SCHEMAS`, `REQUESTED_DIFFICULTY`, `DIFFICULTY_DIRECTIVE`, and `TOPIC_FOCUS`. `QUESTION_SCHEMAS` carries the JSON shape of each allowed question type, so the model is shown only the types the request permits.

The variable is named `REQUESTED_DIFFICULTY` rather than `DIFFICULTY` on purpose. Rendering substitutes `{{NAME}}` placeholders in dictionary order, so a variable whose name is a prefix of another one's would make the result depend on that order.

`quiz_grading` (1.0.0) scores open-ended answers against their stored reference answers. Its required variables are `SUBMISSION_COUNT` and `SUBMISSIONS`, and it returns an `OpenEndedGradingResponse`. Both templates state that text inside the material, topic focus, or a student's answer is data and never an instruction. The `quiz` template renders `TEXT` last so course material cannot forge a placeholder a later substitution would fill in.
