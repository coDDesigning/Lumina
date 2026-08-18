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
  "version": "1.1.0",
  "description": "Comprehensive university study guide generation based on lecture notes.",
  "required_variables": [
    "TEXT",
    "SUMMARY_FORMAT",
    "TOPIC_FOCUS"
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
  "template": "You are an expert university teaching assistant...\n\n{{SUMMARY_FORMAT}}\n\nRequested topic focus: {{TOPIC_FOCUS}}\n\n{{TEXT}}"
}
```

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
