# AI Usage Telemetry and Privacy Policy

## Purpose

Lumina records structured, operational telemetry for AI generation activities (Study Guides, Quizzes, Flashcards, AI Tutor Q&A, and Prompt Generation) to monitor provider latency, token usage, cost accounting, feature adoption, and system error rates across supported deployment models.

## Privacy & Safety Guarantees

Lumina implements strict privacy-safe logging controls:

1. **No Raw Content**: Under no circumstances does Lumina store raw prompts, extracted document chunks, student-submitted text, or raw model output responses in the `ai_usage_logs` telemetry table.
2. **No Secrets or Credentials**: API keys, auth headers, and session tokens are never persisted or logged.
3. **Categorical Error Reporting**: Exception traces and error strings that could potentially contain fragments of student prompts or internal content are **not** stored. Errors are mapped strictly to fixed, enumerated error categories:
   - `provider_error`
   - `invalid_structure`
   - `no_ready_material`
   - `empty_response`
   - `timeout`
   - `rate_limit`
   - `authentication_error`
   - `unknown_error`
4. **Best-Effort & Fault-Isolated Telemetry**: Telemetry logging failures (e.g. temporary database lock or constraint issue) are trapped and logged as warnings; they never abort or corrupt the primary user operation and never leak internal database diagnostics to client endpoints.

## Telemetry Schema (`ai_usage_logs`)

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | `INTEGER` (PK) | No | Auto-incrementing identifier |
| `user_id` | `INTEGER` (FK `users.id`) | No | User who initiated the AI interaction (cascades on delete) |
| `course_id` | `INTEGER` (FK `courses.id`) | Yes | Course context if applicable (cascades on delete) |
| `generation_type` | `VARCHAR(50)` | No | AI feature (`study_guide`, `quiz`, `flashcard`, `ai_tutor`, `prompt_generator`) |
| `provider` | `VARCHAR(50)` | No | Model provider backend (e.g., `gemini`) |
| `model` | `VARCHAR(100)` | No | Model identifier (e.g., `gemini-2.5-flash`) |
| `prompt_tokens` | `INTEGER` | Yes | Token count for input (prompt) when exposed by provider |
| `completion_tokens` | `INTEGER` | Yes | Token count for output (completion) when exposed by provider |
| `total_tokens` | `INTEGER` | Yes | Total token count when exposed by provider |
| `latency_ms` | `INTEGER` | Yes | Provider response duration in milliseconds |
| `success` | `BOOLEAN` | No | Boolean flag indicating whether the generation succeeded |
| `error_category` | `VARCHAR(50)` | Yes | Stable, high-level categorical error code for failed attempts |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | No | UTC timestamp when generation occurred |

## Data Retention

- **Self-Hosted Mode**: Telemetry records are maintained locally within the deployment's SQLite/PostgreSQL database. Records are cascaded automatically upon user or course deletion.
- **Retention Schedule**: Telemetry data older than 90 days may be periodically purged or aggregated for reporting without impacting core student artifacts (`generated_outputs`, `quizzes`, etc.).
