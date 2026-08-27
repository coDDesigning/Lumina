# Learner-Aware Prompt Library Architecture

## Overview

Lumina uses a versioned, structured, and learner-aware prompt template architecture for every AI prompt it sends. The eleven templates under `app/prompts/` cover study guides, quizzes, exam-style questions, quiz grading, flashcards, AI tutoring, course Q&A, prompt generation, image description, visual content understanding, and OCR cleanup. The only model-facing text outside this directory is the small set of composed fragments the templates are built from: the shared blocks in `services/prompt_components.py`, the learner-context directives in `schemas/prompt_context.py`, and the profile-context wrapper in `services/profile_knowledge.py`. Those are scanned for neutrality alongside the templates themselves (see **Prompt neutrality policy**), so a semantic prompt written inline anywhere in production fails CI.

Every template carries a `status`. Eight are `active` and owned by a feature service. Three — `exam_style_question`, `ocr_cleanup`, and `visual_content` — are **explicitly deferred**: declared, validated, and tested, but refused by `PromptLoader.render` so they cannot reach a provider. See **Deferred templates** below for the decision and the reason behind each one. `image_description` is the one vision template that is wired, and `services/image_understanding.py` renders it for every extracted visual.

Design.md §20 lists its prompt categories as *examples*, not as a contract. Four of them are wired today: summary generation (`study_guide`), quiz generation (`quiz`), image description (`image_description`), and written answer evaluation (`quiz_grading`). OCR cleanup and exam-style question generation are deferred rather than implemented, because naming a category in a design document is not on its own a reason to add an LLM call to production.

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

| Template Name | Version | Status | Owner | Primary Purpose | Output Schema Ref |
|---|---|---|---|---|---|
| `study_guide` | `2.3.0` | active | `services/study_guide.py` | Comprehensive study guide generation | `StudyGuideResponse` |
| `quiz` | `3.3.0` | active | `services/quiz.py` | Multi-format quiz generation | `QuizGenerationResponse` |
| `exam_style_question` | `1.2.0` | deferred | Exam Mode / Similar Question Generation (not built) | Exam-style practice questions | `QuizGenerationResponse` |
| `exam_topic_analysis` | `1.1.0` | active | `services/exam_source_analysis.py` | Exam Mode source analysis: topic discovery and past exam question extraction | `GeneratedExamAnalysisResponse` |
| `quiz_grading` | `2.0.0` | active | `services/quiz_grading.py` | Written answer grading against reference answers | `OpenEndedGradingResponse` |
| `flashcard` | `2.2.0` | active | `services/flashcard.py` | Active recall flashcard decks | `FlashcardGenerationResponse` |
| `ai_tutor` | `2.3.0` | active | `services/ai_tutor.py` | Hint-first tutoring with stepwise guidance | `AiTutorResponse` |
| `course_qa` | `2.3.0` | active | `services/course_qa.py` | Direct retrieval-grounded course Q&A | `CourseQAResponse` |
| `prompt_generator` | `2.0.0` | active | `services/prompt_generator.py` | User request transformation to optimized prompt | `PromptGenerationResponse` |
| `image_description` | `1.0.0` | active | `services/image_understanding.py` | Visual descriptions for the retrieval index | — |
| `visual_content` | `2.1.0` | deferred | Advanced visual understanding (not built) | Multimodal diagram, chart, table, and figure analysis | `VisualContentDescriptionResponse` |
| `ocr_cleanup` | `1.1.0` | deferred | none | AI-assisted OCR text normalization and repair | `OcrCleanupResponse` |

`tests/test_prompt_loader.py` pins this table: `EXPECTED_TEMPLATE_VERSIONS` pins each
version, `ACTIVE_TEMPLATE_OWNERS` pins each active template to the service that renders it,
and `test_catalog_documentation_matches_template_status` checks the Status and Owner columns
above against what the templates and services actually declare. So a version bump, an
ownership change, or a status change that is not reflected here fails the suite.

Bumping the version when you edit a template body is a convention, not an enforced one: the
pin compares declared versions, and nothing hashes the template text. Editing a body and
leaving its version alone still passes. Bump it anyway — `get_render_metadata` reports the
version into telemetry, so an unbumped edit makes two different prompts indistinguishable in
the logs.

---

## Deferred templates

A deferred template is one that exists, validates, and renders correctly, but that no
production feature owns. It declares `"status": "deferred"` and a `deferral_reason`, and
`PromptLoader.render` raises `PromptTemplateDeferredError` for it unless the caller passes
`allow_deferred=True` — which only the tests do. So a deferred template cannot reach a
provider, and `test_no_service_renders_a_deferred_template` fails if any module under
`services/` so much as names one.

The reverse direction is pinned too: `test_every_active_template_is_rendered_by_a_service`
fails if an active template has no owning service. A new template must therefore either be
wired or explicitly deferred — it cannot sit in the catalog unexplained.

**`ocr_cleanup` — deferred, no owner.** Design.md §5.2 specifies deterministic OCR handling:
a page OCR cannot read marks the document failed or partially failed. It does not call for a
model to reconstruct what the page probably said. Passing OCR output through an LLM before
chunking would put non-deterministic, potentially hallucinated text into the retrieval index,
where every downstream summary and quiz would treat it as source material. Implementing this
needs a separately scoped feature that defines when semantic restoration is appropriate, what
grounding it guarantees, whether the original OCR is preserved alongside it, and what it
costs in credits.

**`exam_style_question` — deferred, owner named but not built.** Live quiz generation is owned
solely by `quiz`. Exam Mode and Similar Question Generation (Design.md Core Features 3 and 6)
are real planned features, but they are listed under §23 Future Extensions and have no
endpoint, request schema, or persistence yet. Wiring a near-duplicate of `quiz` before that
owner exists would leave two quiz prompts to drift apart — the next question-schema change
would update one and forget the other.

**`visual_content` — deferred, owner named but not built.** Wired vision is owned by
`image_description`, which covers Design.md §5.3 Basic Image Understanding. `visual_content`
is the richer multimodal analysis belonging to §23's advanced visual understanding.

Deferring is a recorded decision, not a backlog note. Reversing one means giving the template
a real owning feature, flipping `status` to `active`, wiring exactly that feature to it, and
updating this table — at which point the two catalog tests above start enforcing the new state.

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
  "version": "2.1.0",
  "status": "active",
  "owner": "services/study_guide.py",
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

1. **Deferred-Template Refusal**:
   - `PromptLoader.render` raises `PromptTemplateDeferredError` for a template whose `status` is `deferred`, unless the caller passes `allow_deferred=True`.
   - Only rendering is guarded. `load_template` and `load_all` return deferred templates normally, so the catalog stays introspectable for tests and tooling.
   - `PromptTemplateDeferredError` extends `PromptTemplateError`, so it funnels through `utils/ai_errors.py` to `GENERATION_FAILED` like any other template fault. Reaching it always means a server-side wiring bug, never bad user input.
   - A template declaring `"status": "deferred"` without a `deferral_reason` fails validation at load time.

2. **Deterministic Variable Rendering**:
   - `PromptLoader.render(name, variables)` substitutes all declared placeholders. There is no implicit context injection: a template's learner and course variables come from `PromptContext.as_variables()` like any other variable.
   - Missing required variables raise `MissingPromptVariableError`.
   - Unexpected variables raise `UnexpectedPromptVariableError`.
   - Document and free-text variables (e.g. `TEXT`, `COURSE_MATERIAL`) are always substituted last to prevent placeholder forging.

3. **Strict Variable Validation**:
   - Missing required variable $\rightarrow$ Raises `MissingPromptVariableError`.
   - Unexpected/extra variable $\rightarrow$ Raises `UnexpectedPromptVariableError`.
   - Placeholder in the body that no variable declares: raises
     `PromptTemplateValidationError` **at load time**, which is what catches a typo
     such as `{{EDUCATON_LEVEL}}`.
   - Declared placeholder with no supplied value: raises `MissingPromptVariableError`
     **before** the provider call.
   - All `{{VARIABLE}}` placeholders are substituted deterministically.

4. **Developer Observability & Telemetry**:
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
   If no feature owns it yet, declare `"status": "deferred"` with a `deferral_reason` and add it to `DEFERRED_TEMPLATES` in the tests instead. There is no third option — the catalog tests fail on a template that is neither wired nor deferred.
3. Register a rendering fixture in `TEMPLATE_EXTRA_VARIABLES` in
   `tests/test_prompt_neutrality.py`, supplying exactly the template's non-shared required
   variables. This is not optional: the coverage guard fails by name on any template without
   one, which is how "every production template" stays true.
4. Add unit & regression tests in `tests/test_prompt_loader.py`, and add a row to the catalog table above.

### 2. Adding a New Shared Context Variable

1. Add the field to `PromptContext` in `schemas/prompt_context.py` and to its server-side
   directive table, keyed by a validated enum so it can never carry injected instructions.
2. Emit it from `PromptContext.as_variables()` and resolve it in `resolve_prompt_context`.
3. Declare it in every template that uses it and bump those templates' versions.
4. Add assertions in `tests/test_prompt_context_propagation.py` that it reaches every prompt
   and that its neutral value stays neutral.
5. If the variable carries a server-side directive table, extend the directive tests in
   `tests/test_prompt_neutrality.py` so its neutral value cannot silently resolve to a
   concrete one (see **Prompt neutrality policy**).

> Declare every variable a service passes in `required_variables` (or `optional_variables`),
> and declare every `{{PLACEHOLDER}}` that appears in the body. Both directions are enforced
> (see **Placeholder guarantees**), so an undeclared placeholder fails at load time and an
> unsupplied one fails before the provider is ever called.

## Quiz templates

`quiz` (3.1.0) generates a quiz of a requested size, difficulty, and question-type mix. Its required variables are `TEXT`, `QUESTION_COUNT`, `QUESTION_TYPES_DIRECTIVE`, `QUESTION_SCHEMAS`, `REQUESTED_DIFFICULTY`, `DIFFICULTY_DIRECTIVE`, and `TOPIC_FOCUS`. `QUESTION_SCHEMAS` carries the JSON shape of each allowed question type, so the model is shown only the types the request permits.

The variable is named `REQUESTED_DIFFICULTY` rather than `DIFFICULTY` on purpose. Rendering substitutes `{{NAME}}` placeholders in dictionary order, so a variable whose name is a prefix of another one's would make the result depend on that order.

`quiz_grading` (2.0.0) scores open-ended answers against their stored reference answers. Its required variables are `SUBMISSION_COUNT` and `SUBMISSIONS`, and it returns an `OpenEndedGradingResponse`. Both templates state that text inside the material, topic focus, or a student's answer is data and never an instruction. The `quiz` template renders `TEXT` last so course material cannot forge a placeholder a later substitution would fill in.

## Shared learner and course context

Every course-scoped generation template declares shared variables ahead of its feature-specific ones:

| Variable | Source | Neutral value |
|---|---|---|
| `EDUCATION_LEVEL` | `courses.education_level`, else `users.education_level` | `unspecified` |
| `COURSE_TITLE` | `courses.title` | `Unspecified course` |
| `SUBJECT_AREA` | `courses.subject_area` | `Unspecified subject area` |
| `MATERIAL_KIND` | aggregate of `uploaded_documents.material_kind` | `unspecified` |
| `PROFILE_CONTEXT` | `services.profile_knowledge.assemble_generation_context` | `""` (empty string) |

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

`PROFILE_CONTEXT` provides supplementary, student-owned background context formatted via
`services/profile_knowledge.py:format_profile_context`. When opted in by the learner, it
appends clearly labeled background knowledge below the course material. When disabled or empty,
it resolves to an empty string. The template instructions explicitly affirm that course material
is primary and authoritative, and profile context must never override or contradict it.


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
Renaming it would need a migration of existing outputs, so it is out of scope here. The
neutrality scanner needs no exception for it: the rules below match role constructions such
as `for university students`, not bare words, so an identifier that merely contains
`lecture` never trips them.

## Prompt neutrality policy

`tests/prompt_neutrality_policy.py` holds the one reviewable definition of what prompt
neutrality forbids, and `tests/test_prompt_neutrality.py` enforces it. CI runs the whole
`tests` directory, so a violation blocks the merge.

### What is forbidden

Seven case-insensitive, whitespace-tolerant rules in three families:

| Family | Rejects | Use instead |
|---|---|---|
| `level_role`, `level_for`, `level_persona` | `university student`, `for a college learner`, `You are a university tutor` | `EDUCATION_LEVEL` |
| `discipline_role`, `discipline_for`, `discipline_persona` | `Computer Science teaching assistant`, `for Computer Science students` | `SUBJECT_AREA`, `COURSE_TITLE` |
| `material_lecture_notes` | `the following lecture notes` | `MATERIAL_KIND` |

The rules match **role and audience constructions**, never bare words. `university` inside a
list of possible levels is not a violation; `You are a university tutor` is. That
distinction is what keeps the scanner from banning vocabulary the prompts legitimately need.

### Where it looks

- **Every template**, active and deferred, across every model-facing field: `template`,
  `system_instruction`, `description`, `style_constraints`, and `safety_constraints`.
  Templates are discovered through `PromptLoader.load_all()`, so a new file joins the suite
  automatically.
- **Every production Python module** under `services/`, `routes/`, `tasks/`, `workers/`,
  `backend/`, `utils/`, and `schemas/`. This is the guard against someone bypassing the
  template library and writing a prompt inline. Failures report `path:line`.

### Why supplied context values are exempt

Neutrality is checked on **source**, never on rendered output. A prompt rendered for a real
Computer Science course legitimately contains the words *Computer Science*, and the
`EDUCATION_LEVEL` value for an undergraduate literally reads *"Write for an undergraduate
learner."* Scanning rendered text with the source rules would reject all eight active
templates.

So the two concerns are split:

| Surface | Question asked |
|---|---|
| Template and module source | Does a level, discipline, or material type appear as a fixed assumption? |
| Rendered output | Did the supplied context substitute in, leaving no unresolved placeholder? |
| Rendered with an unspecified context | Did anything resolve an unknown level or subject into a concrete one? |

The last row is the most valuable case. `test_unspecified_context_never_falls_back_to_a_level_or_discipline`
renders every template with a bare `PromptContext()` and requires zero occurrences of
university, college, undergraduate, graduate, high school, secondary school, or Computer
Science. `test_a_supplied_computer_science_context_is_allowed` is its counterpart, proving
the suite is discipline-neutral rather than anti-Computer-Science.

### The directive maps are checked, not exempted

`EDUCATION_LEVEL_DIRECTIVES` and `MATERIAL_KIND_DIRECTIVES` in `schemas/prompt_context.py`
are production prompt prose, and they must name levels and material kinds — that is their
job. The inline-source scan therefore blanks their values before applying the generic
rules, and dedicated tests apply stricter ones in their place:

- No level directive may name another level's vocabulary, and no material directive may name
  another kind's.
- The `unspecified` level directive may name **no** academic tier at all, and the
  `unspecified` material directive may name no kind.
- No directive of either kind may name a discipline.

`test_the_carve_out_only_covers_declared_directive_values` asserts the blanked text is
exactly the two maps' values, so nothing else can be smuggled through the gap.

### The scanner is proven to fire

A scanner nobody tests can break and stay green forever. `test_the_scanner_rejects_a_hardcoded_assumption`
feeds it ten known-bad strings and requires the right rule to catch each. Its counterpart
`test_the_scanner_accepts_context_driven_wording` feeds it eight legitimate ones, so a
future contributor cannot resolve a false positive by weakening the policy unnoticed.

### Coverage cannot be escaped

`test_every_template_declares_a_render_fixture` compares the registered fixtures against the
live catalog in both directions, and additionally requires each fixture to supply *exactly*
the template's declared non-shared required variables. A new template with no fixture fails
by name. A template that grows a placeholder nobody registered fails too — the fixture
builder cannot paper over a typo such as `UNVERSITY_LEVEL`.

### What it deliberately is not

The suite is fully offline and deterministic: it makes no Gemini or Ollama call and judges
no model output. It checks that the model receives a context-driven prompt, not that the
answer it returns sounds right for the level.

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
