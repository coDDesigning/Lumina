# AI Providers

Lumina reaches every AI model through one abstraction, `TextGenerationProvider`
in `services/text_generation.py`. The five generation features — study guide,
quiz, flashcards, prompt generator, and AI tutor — depend on that protocol and
never on a specific vendor.

## Implemented Providers

| `AI_PROVIDER` | Status | Required configuration |
|---|---|---|
| `ollama` | Implemented (default) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` — both defaulted |
| `gemini` | Implemented | `GEMINI_API_KEY` |
| `openai` | Implemented | `OPENAI_API_KEY` |
| `claude` | Implemented | `ANTHROPIC_API_KEY` |

`IMPLEMENTED_AI_PROVIDERS` in `backend/app/config.py` is the authoritative list.
The provider factory reads it rather than restating provider names, so the two
cannot drift apart.

The same rule covers `AI_FALLBACK_PROVIDERS`: a fallback naming an
unimplemented provider is rejected at startup rather than failing the first time
the primary provider errors and the fallback is actually reached.

An unrecognized spelling fails with the list of accepted names, which keeps a
typo (`gemeni`) distinguishable from a genuine provider name.

## Hosted OpenAI Setup

1. Obtain an API key from <https://platform.openai.com>.
2. Configure the environment:

   ```bash
   AI_PROVIDER=openai
   OPENAI_API_KEY=sk-...
   ```

   The default model is `gpt-5.6-terra`. The model catalog supports 1M-token context windows,
   vision input, and structured JSON output via OpenAI's JSON schema generation.

## Hosted Claude (Anthropic) Setup

1. Obtain an API key from <https://console.anthropic.com>.
2. Configure the environment:

   ```bash
   AI_PROVIDER=claude
   ANTHROPIC_API_KEY=sk-ant-...
   ```

   The default model is `claude-sonnet-5`. The model catalog supports 1M-token context windows,
   vision input, and structured JSON output via Anthropic's structured output format schema.

## Multi-Provider Fallback & Resilience

Hosted production deployments can configure multiple providers in priority order to eliminate
single-vendor concentration risk:

```bash
AI_PROVIDER=gemini
AI_FALLBACK_PROVIDERS=openai,claude
```

During transient network drops, rate limits, or upstream 5xx outages, `ReliableTextGenerationProvider`
will automatically retry with exponential backoff and transparently fail over to the next configured
provider. Output attribution truthfully records the provider and model that generated each artifact.

## Self-Hosted Ollama Setup

1. Install Ollama from <https://ollama.com> and start it. A backend process
   running directly on the host can use Ollama's loopback default:

   ```bash
   ollama serve
   ```

   Root Compose reaches the host through `host.docker.internal`, so on Linux
   Ollama must listen on an address reachable from the Docker bridge:

   ```bash
   OLLAMA_HOST=0.0.0.0:11434 ollama serve
   ```

   Restrict port 11434 to the host and Docker bridge with the host firewall; do
   not expose an unauthenticated Ollama listener to the public network.

2. Pull a model that meets the capability bar below.

   ```bash
   ollama pull llama3.1
   ```

3. Configure the backend. Use `localhost` for Python processes on the host and
   `host.docker.internal` for root Compose:

   ```bash
   AI_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.1
   ```

   ```bash
   OLLAMA_BASE_URL=http://host.docker.internal:11434
   ```

   Local models are much slower than a hosted API. `AI_GENERATION_TIMEOUT_SECONDS`
   defaults to 60 and applies to every provider; raise it if generation on your
   hardware takes longer.

4. Start the API, upload a document to a course, wait for its status to reach
   `ready`, then generate:

   ```bash
   curl -X POST http://localhost:8000/api/courses/1/study-guide \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"summary_format": "comprehensive", "topic_focus": "All Topics"}'
   ```

## Required Model Capability

Every generation feature except the AI tutor requires **structured JSON output**
validated against a Pydantic schema. Not every Ollama model can do this
reliably. The configured model must offer:

- **Instruction following** — an instruction-tuned or chat-tuned model. Base
  completion models drift into prose and fail validation.
- **Reliable JSON generation** — the provider sends `"format": "json"` on every
  structured request, which constrains Ollama's decoding, but a weak model still
  emits structurally valid JSON with the wrong fields. Schema validation catches
  this and rejects the generation.
- **A context window large enough for the prompt.** Course material is bounded
  by the per-feature budgets described below, but the window must still hold that
  budget plus the template and the response. A window smaller than the configured
  budget makes the model, rather than the application, decide what to drop.
- **Enough capability for the schema's breadth.** The study-guide schema alone
  requires roughly a dozen populated sections.

`llama3.1` (8B) is the documented known-good baseline. Larger instruction-tuned
models produce better guides; models below roughly 7B parameters frequently fail
schema validation.

`OLLAMA_MODEL` validity is not the same as model availability. Configuration
only checks that the value is a well-formed model tag. A model that is not
pulled on the target Ollama instance passes startup validation and then fails at
generation time with a provider error — pull it first.

## Sampling Options

Ollama's own defaults are tuned for open-ended chat, not for schema-constrained
JSON. The provider therefore sends an explicit `options` block on every request
rather than inheriting them:

| Variable | Default | Bounds | Meaning |
| --- | --- | --- | --- |
| `OLLAMA_TEMPERATURE` | `0.2` | `0.0`-`2.0` | Sampling randomness. Ollama's own default is `0.8`. |
| `OLLAMA_TOP_P` | `0.9` | `0.01`-`1.0` | Nucleus sampling cutoff. |
| `OLLAMA_NUM_CTX` | `8192` | `512`-`131072` | Context window. The prompt and the response share it. |
| `OLLAMA_NUM_PREDICT` | `4096` | `64`-`131072` | Maximum response tokens. May not exceed `OLLAMA_NUM_CTX`. |
| `OLLAMA_REPEAT_PENALTY` | `1.1` | `0.5`-`2.0` | Penalty applied to repeated tokens. |

**Temperature is a correctness setting here, not a style setting.** Every
generation feature except the AI tutor validates model output against a Pydantic
schema, and those schemas carry hard constraints — a multiple-choice question
must have exactly four options. At Ollama's default temperature an 8B model
violates such a constraint often enough that a ten-question quiz rarely survives
validation, and schema failures are deliberately not retried. Measured on
`llama3.1` 8B Q4_K_M, ten-question quizzes, identical prompt:

| Temperature | Quizzes passing schema validation |
| --- | --- |
| `0.8` (Ollama default) | 2 of 5 |
| `0.2` (this default) | 5 of 5 |

The dominant failure at `0.8` was `multiple_choice.options` carrying three
entries instead of four.

`num_ctx` is sent per request, which has two consequences worth knowing. A model
needs no custom Modelfile to run at the intended window, and a server-wide
`OLLAMA_CONTEXT_LENGTH` cannot silently inflate the KV cache — an oversized
window is what pushes a model off the GPU and into system RAM.

Sampling options do not apply to `EMBEDDING_PROVIDER` or `IMAGE_PROVIDER`; both
call different endpoints with different contracts.

## Single-GPU Box Profile

A self-hosted deployment on one consumer GPU — 16 GB system RAM, 8 GB VRAM — is
a supported target. The binding constraint is VRAM: a model that does not fit
entirely in VRAM spills into system RAM and slows down by roughly a factor of
five, which then turns into generation timeouts rather than merely slow answers.

An 8B model at Q4_K_M with an 8192-token window occupies about 5.8 GB loaded,
which fits 8 GB VRAM with room for the desktop. That is the largest practical
model for this profile.

```bash
ollama pull llama3.1
ollama pull nomic-embed-text
```

```bash
AI_PROVIDER=ollama
EMBEDDING_PROVIDER=ollama
OLLAMA_MODEL=llama3.1
OLLAMA_TEMPERATURE=0.2
OLLAMA_NUM_CTX=8192
OLLAMA_NUM_PREDICT=4096

AI_GENERATION_TIMEOUT_SECONDS=180

STUDY_GUIDE_MATERIAL_MAX_CHARS=16000
QUIZ_MATERIAL_MAX_CHARS=16000
FLASHCARD_MATERIAL_MAX_CHARS=16000
AI_TUTOR_MATERIAL_MAX_CHARS=16000
COURSE_QA_MATERIAL_MAX_CHARS=16000
```

The material budgets matter as much as the model choice. The default `120000`
characters is roughly 30k tokens, which exceeds an 8192-token window nearly four
times over; Ollama then truncates the prompt silently, and the model answers
from whatever survived. `16000` characters is roughly 4k tokens, which leaves
room for the prompt template and the response inside the same window. Nothing
cross-validates the two settings, because a Gemini deployment has a 1M-token
window and needs no such reduction.

`AI_GENERATION_TIMEOUT_SECONDS` must be raised from its `60` default. A
twenty-question quiz emits roughly 2,700 tokens, which at this profile's
throughput does not finish inside a minute, and a model that has been idle long
enough to unload pays its load time on top of that.

### Measured Behaviour

`llama3.1` 8B Q4_K_M, fully GPU-resident, prompts built from the real templates
and validated against the real response schemas:

| Feature | Schema-valid | Median latency |
| --- | --- | --- |
| Flashcards | 6 of 6 | 12.6 s |
| Quiz, 10 questions | 6 of 6 | 32.4 s |
| Quiz, 20 questions | 4 of 5 | 69.7 s |

The same twenty-question case at the default `AI_GENERATION_TIMEOUT_SECONDS` of
`60` failed every attempt, all of them `TextGenerationTimeoutError` rather than
schema failures. That setting, not the model, is what makes large quizzes look
broken on this profile.

Twenty questions remains the least reliable request on an 8B model, and the
reason is arithmetic rather than a defect: one malformed question invalidates the
whole response, so per-question compliance compounds across the request. Ten
questions is the comfortable size here. A deployment that needs twenty to be
dependable should run a larger model.

Lowering `OLLAMA_TEMPERATURE` further helps that case: twenty-question quizzes
measured 4 of 5 at `0.2` and 6 of 6 at `0.1`. Those samples are too small to
separate confidently, so the shipped default stays at `0.2`, which is also what
the quiz and study-guide templates declare in their `model_hints`. A deployment
whose main workload is large quizzes can reasonably set `0.1`; one that leans on
the AI tutor should not, since that template asks for `0.4` and prose written at
`0.1` is noticeably flatter.

Quality, as distinct from validity, is bounded by the model. An 8B model writes
tersely: an in-depth AI tutor question returned about 320 tokens, and a study
guide requested at `detail_level=high` returned a 364-character summary with
several sections left thin. This is the model declining to elaborate, not a
truncation — every measured response ended with `done_reason: stop`, never
`length`. Raising `OLLAMA_NUM_PREDICT` does not change it. A deployment with
more VRAM should prefer a larger instruction-tuned model over tuning this one.

## Configuration Validation

`backend/app/config.py` is the only module that reads the environment. No
provider URL, model name, or secret is read anywhere else. Validation happens
once, at import time, so a misconfigured deployment dies in its first second
rather than at the first user click:

| Setting | Rejected when |
|---|---|
| `AI_PROVIDER` | not a recognized name, or recognized but not implemented |
| `OLLAMA_BASE_URL` | empty, whitespace, or not a valid `http://`/`https://` URL with a host (`banana` and `localhost:11434` both fail) |
| `OLLAMA_MODEL` | empty, whitespace, longer than 128 characters, or containing characters outside letters, digits, `. : / - _` |
| `OLLAMA_TEMPERATURE` | not a finite number, or outside `0.0`-`2.0` |
| `OLLAMA_TOP_P` | not a finite number, or outside `0.01`-`1.0` |
| `OLLAMA_NUM_CTX` | not a positive integer, or outside `512`-`131072` |
| `OLLAMA_NUM_PREDICT` | not a positive integer, outside `64`-`131072`, or greater than `OLLAMA_NUM_CTX` |
| `OLLAMA_REPEAT_PENALTY` | not a finite number, or outside `0.5`-`2.0` |
| `AI_FALLBACK_PROVIDERS` | any token is unrecognized, or recognized but not implemented |
| `AI_MODEL_CATALOG` | not valid JSON, empty, contains an unimplemented provider, contains duplicate model names, or a model entry is missing/invalid `model`, `json_mode`, `context_window`, or `vision` metadata |

Configuration validation deliberately does **not** contact Ollama. Booting the
API must not depend on a model server being up, so reachability is a
generation-time concern.

### Model Catalog

`AI_MODEL_CATALOG` configures the available text-generation models for each
implemented provider. The value is a JSON object keyed by provider name.

Each model entry must provide its model identifier and capability metadata:

```json
{
  "ollama": [
    {
      "model": "llama3.1",
      "json_mode": true,
      "context_window": 8192,
      "vision": false
    },
    {
      "model": "qwen3:8b",
      "json_mode": true,
      "context_window": 32768,
      "vision": false
    }
  ]
}

Each entry requires:

- `model` — non-empty model identifier
- `json_mode` — whether structured JSON generation is supported
- `context_window` — positive integer context-window size
- `vision` — whether visual input is supported

Invalid catalog configuration fails during application startup. Explicit model
selections are validated against the catalog, and a selected model is passed to
the provider request instead of always using the provider's default model.

A model that does not support JSON mode is rejected when a generation path
requires structured JSON output.

## Error Semantics

The provider translates transport and protocol failures into the shared error
taxonomy in `services/text_generation.py`, all subclasses of
`TextGenerationError` and all carrying a telemetry `error_category`:

| Provider condition | Exception | Category | HTTP |
|---|---|---|---|
| Ollama unreachable at the configured base URL | `TextGenerationConnectionError` | `provider_error` | `503` |
| No response within `AI_GENERATION_TIMEOUT_SECONDS` | `TextGenerationTimeoutError` | `timeout` | `504` |
| Ollama answers HTTP 429 | `TextGenerationRateLimitError` | `rate_limit` | `429` |
| All providers busy at `AI_GENERATION_MAX_CONCURRENCY` | `GenerationConcurrencyError` | `rate_limit` | `429` |
| Other non-2xx from Ollama, e.g. model not found | `TextGenerationProviderError` | `provider_error` | `500` |
| Response envelope missing or malformed | `TextGenerationProviderError` | `provider_error` | `500` |
| Generated text empty or whitespace | `TextGenerationEmptyResponseError` | `empty_response` | `500` |
| Generated text not valid JSON | `TextGenerationError` | `invalid_structure` | `500` |
| Valid JSON failing the feature's schema | `<Feature>GenerationError` | `invalid_structure` | `500` |

Unreachable (`503`) is kept distinct from timed out (`504`) and from generation
failures (`500`), so an operator can tell "Ollama is not running" from "the model
is too slow" from "the model produced garbage" without reading a stack trace. An
error body from Ollama is never treated as generated content.

## Retries and Fallback

`ReliableTextGenerationProvider` wraps whatever providers are configured and adds
retries with exponential backoff, a concurrency ceiling, and fallback to the
providers named in `AI_FALLBACK_PROVIDERS`. Ollama participates on the same terms
as Gemini:

- Connection failures and timeouts are classified transient and retried up to
  `AI_GENERATION_MAX_ATTEMPTS` before the next provider is tried.
- Invalid JSON and schema failures are **not** retried. They are deterministic;
  re-asking an identical prompt would only burn time. A weak model producing
  unusable JSON is a configuration problem, not a transient one.
- With `AI_PROVIDER=ollama` and `AI_FALLBACK_PROVIDERS=gemini`, a local model
  that is down falls through to the cloud provider — useful for self-hosted
  setups that keep an API key for emergencies.

## Course Material Context Budget

Every AI feature bounds one request's material to a configured number of
characters. Study guide, quiz, flashcard, AI tutor, and course Q&A read **retrieved**
material through `services/retrieval_material.py`:

| Setting | Default | Bounds | Source |
|---|---|---|---|
| `STUDY_GUIDE_MATERIAL_MAX_CHARS` | `120000` | study guide generation | retrieval |
| `QUIZ_MATERIAL_MAX_CHARS` | `120000` | quiz generation | retrieval |
| `FLASHCARD_MATERIAL_MAX_CHARS` | `120000` | flashcard generation | retrieval |
| `AI_TUTOR_MATERIAL_MAX_CHARS` | `120000` | AI tutor answers | retrieval |
| `COURSE_QA_MATERIAL_MAX_CHARS` | `120000` | course Q&A answers | retrieval |

Retrieval adds two further bounds, applied before the character budget:

| Setting | Default | Bounds |
|---|---|---|
| `RETRIEVAL_CHUNK_LIMIT` | `24` | how many of a course's chunks are ranked per request |
| `RETRIEVAL_MIN_SIMILARITY` | `0.25` | cosine floor (`0.0`-`1.0`) a ranked chunk must clear; `0.0` disables it |

The two knobs are independent by design: the limit bounds relevance and cost, the
character budget bounds the context window. Nothing cross-validates them, because
`budget >= DOCUMENT_CHUNK_SIZE_CHARACTERS` already guarantees at least one
retrieved chunk always fits.

Each budget must be at least `DOCUMENT_CHUNK_SIZE_CHARACTERS`, or startup fails:
a budget smaller than one stored chunk could never assemble any material at all.

The budget covers the course material only, not the fixed prompt template or the
model's own context window, and nothing cross-validates the two. `120000`
characters is roughly 30k tokens: a Gemini deployment absorbs that comfortably,
while an Ollama deployment at the default `OLLAMA_NUM_CTX` of `8192` does not —
Ollama truncates the prompt silently and the model answers from whatever
survived. Lower these budgets whenever the configured context window cannot hold
them; the Single-GPU Box Profile above pairs `8192` with `16000` characters.

Whole-corpus selection is deliberately simple: chunks of `ready` documents are
taken **whole**, ordered by document `created_at`, then document id, then
`chunk_index`, then chunk id, until the next chunk would exceed the budget. The
separators between chunks count against the budget, so the assembled string never
exceeds it. The same course state always produces byte-identical material.

Retrieval-backed selection uses **two distinct orders**, and the distinction
matters. Chunks are *selected* against the character budget in similarity order,
so the budget is never spent on the least relevant material; the retained chunks
are then *emitted* in the same corpus order the whole-corpus assembler uses, so
the prompt still reads as coherent prose. Both orders are total, so identical
course state produces byte-identical material here too.

The response reports coverage rather than implying the whole course was read:

```json
{
  "success": true,
  "data": {
    "study_guide": { "...": "strictly validated model output" },
    "generated_output_id": 12,
    "context_truncated": false,
    "retrieval_narrowed": true,
    "chunks_used": 24,
    "chunks_available": 3000,
    "lowest_similarity": 0.41,
    "highest_similarity": 0.88
  }
}
```

Both flags are derived by the application, never by the model, and they mean
different things:

- `retrieval_narrowed` — retrieval selected a subset of the course. This is the
  normal case for a retrieval-backed feature, not a warning.
- `context_truncated` — the character budget dropped a chunk that retrieval had
  already selected. **This is narrower than the pre-retrieval meaning**, which was
  simply `chunks_used < chunks_available`; under retrieval that inequality holds
  on nearly every request and would make the flag noise.

`services/course_material.py` remains the whole-corpus path for the
profile-knowledge assembly helper. Authorization, provider calls, schema
validation, and persistence deliberately live outside both material modules so
each remaining migration stays localized.

## Public Error Messages

`utils/ai_errors.py` maps every generation failure to a status code and a
**constant** public message. Exception text — which can name hosts, URLs, or
upstream payloads — is logged with a stable `AiErrorCode` and never returned to a
client. `AiErrorCode` classifies the API response and is distinct from the
telemetry `error_category` in the table above, which classifies the provider
condition for `ai_usage_logs`:

| `AiErrorCode` | HTTP | Cause |
|---|---|---|
| `no_ready_material` | `400` | the course has no processed chunks at all |
| `no_relevant_material` | `409` | the course has material, but none matched the request |
| `retrieval_unavailable` | `503` | semantic retrieval could not answer |
| `provider_rate_limited` | `429` | provider rate limit or concurrency ceiling |
| `provider_unavailable` | `503` | the model server could not be reached |
| `provider_timeout` | `504` | no response within the configured timeout |
| `invalid_generated_structure` | `500` | output was not JSON or failed the schema |
| `generation_failed` | `500` | anything else |

## Generated Output Attribution

`GeneratedOutputService.record` is the single canonical writer for `generated_outputs` rows across all generation features (`study_guide`, `quiz`, `flashcards`). Feature services and routes persist through this entrypoint and never construct `GeneratedOutput` models directly.

Rows in `generated_outputs` record `user_id` (the authenticated requester, never
a client-supplied value and never inferred from course ownership) and
`model_used` in `provider:model` form, taken from the metadata of the provider
that actually answered — so a fallback generation attributes the fallback, not
the configured primary. Both columns are nullable only because rows written
before this migration have no truthful value; they are never backfilled with a
guess.


`generation_settings` and `generation_context` record how a row was produced, as
JSON documents carrying a `version` and an `output_type` so other output types can
share the columns later. Settings hold what the user asked for (summary format,
topic focus, length, detail level, mode) plus the retrieval knobs in force;
context holds what retrieval actually produced (chunks ranked, retrieved and used,
the similarity range, and whether the budget truncated). Both are written strictly
through their Pydantic models, so a stored document is always well-formed, and
read back permissively, so a row written by a future schema version still renders
and can never fail a history read. Both are nullable for exactly the same reason
as the columns above, and are never backfilled.

### Reading stored outputs

```
GET /api/courses/{course_id}/generated-outputs             list, newest first, no content
GET /api/courses/{course_id}/generated-outputs/{output_id} one stored output with its content
```

Both are reads, so the administrator override applies: an administrator may read
another owner's history but still cannot generate into their course. An output is
always looked up scoped to its parent course, so an identifier belonging to a
different course is indistinguishable from one that does not exist. Reading never
calls an AI provider.

## Layer Responsibilities

```
config.py            which implemented provider, and is it configured correctly
    ↓
provider factory     hand back that implementation
    ↓
provider             talk to this specific AI system; return plain text
    ↓
feature service      build the prompt; parse JSON; validate the schema
    ↓
database             persist only after every validation passes
```

The provider returns `str` or `dict` and knows nothing about study guides,
quizzes, or flashcards. JSON parsing is shared between providers — including
tolerance for models that wrap output in a markdown code fence — so no
per-provider cleanup logic exists. Schema validation stays in the feature
services, above the provider, which is why malformed model output can never be
persisted.

## Model Catalog and Capability Routing

`GET /api/models` exposes the active model catalog with capability and cost metadata (`capabilities`, `cost_hint`, `description`, `is_local`, and `supports_json`). `resolve_effective_model` resolves models by explicit request override, user preferred model, or deployment default, and validates that the resolved model supports the requested feature capability (e.g. `study_guide`, `quiz`, `flashcard`, `ai_tutor`, `course_qa`, `prompt_generator`). Unsupported task/model requests are rejected cleanly with a `400 Bad Request`. Provider factories (`get_text_generation_provider`) dynamically pass the requested model to concrete providers (`GeminiTextGenerationProvider`, `OllamaTextGenerationProvider`) rather than hardcoding a single static model.

## Telemetry and Privacy

`services/ai_usage_logger.py` records provider, model, token counts, latency,
success, and a stable error category. It never persists prompts, course
material, or generated text. Successful resilient-provider calls carry the
provider and model that actually ran. Prompt generation also supplies its
resolved model identity when a failed call has no provider metadata.

`AI_MODEL_COST_RATES` optionally adds an immutable estimated USD cost to a
token-bearing event. The value is a JSON object with one `version` and exact
`provider:model` entries:

```json
{
  "version": "2026-08-24",
  "models": {
    "gemini:gemini-2.5-flash": {
      "prompt_usd_per_million_tokens": 0.3,
      "completion_usd_per_million_tokens": 2.5
    }
  }
}
```

The estimate and version are saved with the event, so changing rates never
rewrites history. Events without both token splits or an exact configured rate
remain unpriced. `GET /api/admin/ai-costs` reports successful generations in
UTC day buckets by provider, model, and pricing version, including the number of
unpriced generations. These operational estimates are not invoices and retain
the telemetry privacy lifecycle: deleting the related user or course deletes
its usage events.

## Embedding Providers

Embedding generation is a separate seam from text generation. A deployment may
pair a large generative model with a small dedicated embedding model, and the two
call different endpoints with different response contracts, so `EmbeddingProvider`
in `services/embeddings.py` is not a variant of `TextGenerationProvider`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | `ollama` | Implemented: `ollama`, `gemini`. `openai` and `claude` are recognized and fail at startup, exactly as they do for `AI_PROVIDER`. |
| `OLLAMA_EMBEDDING_MODEL` | `nomic-embed-text` | Used with the shared `OLLAMA_BASE_URL`. |
| `GEMINI_EMBEDDING_MODEL` | `text-embedding-004` | Used with the shared `GEMINI_API_KEY`. |
| `EMBEDDING_BATCH_SIZE` | `32` | Texts per provider request, 1-256. |
| `EMBEDDING_TIMEOUT_SECONDS` | `60` | Per-request deadline, 1-300. |

`IMPLEMENTED_EMBEDDING_PROVIDERS` in `backend/app/config.py` is authoritative, and
a test asserts the factory constructs every name in it.

Every provider validates before returning: the response is a list, its length
matches the input, order is preserved, and each vector is non-empty, numeric,
finite, and exactly `EMBEDDING_DIMENSIONS` wide. A malformed or wrong-width
response is a permanent failure, never something that reaches storage.

Text generation resilience (`ReliableTextGenerationProvider`, fallback providers,
retry, concurrency limiting) does not apply here. Embedding retries are the
durable processing job's responsibility: a transient failure requeues the whole
embedding stage rather than retrying inside the provider. See
`docs/vector_storage.md` for the error codes and their retryability.

## Visual Understanding Providers

Image understanding is a dedicated stage of document processing, separate from
text generation and embeddings. When enabled, visual regions detected in PDFs (such
as diagrams, tables, charts, and figures) are cropped, rendered to bounded PNGs,
and passed to an `ImageUnderstandingProvider` in `services/image_understanding.py`.
The resulting semantic descriptions are merged into the page's text with labeled
headers (e.g. `[Diagram]\n...`) and indexed into chunks/embeddings downstream.

| Variable | Default | Meaning |
| --- | --- | --- |
| `IMAGE_PROVIDER` | `none` | Implemented: `none` (disabled), `ollama`, `gemini`. Unset resolves based on `DEPLOYMENT_MODE`. `openai` and `claude` are recognized and fail at startup. |
| `OLLAMA_IMAGE_MODEL` | `llama3.2-vision` | Multimodal model used with the shared `OLLAMA_BASE_URL`. |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash` | Multimodal model used with `GEMINI_API_KEY`. |
| `IMAGE_UNDERSTANDING_TIMEOUT_SECONDS` | `30` | Per-visual deadline, 1-300 seconds. |
| `IMAGE_UNDERSTANDING_MAX_BYTES` | `10485760` | Maximum accepted rendered image size (10 MB). |

### Deployment Mode Defaults and Startup Validation

Visual understanding configuration is verified at application and worker startup:

- **Hosted Mode (`DEPLOYMENT_MODE=hosted`)**:
  - When `IMAGE_PROVIDER` is unset or empty, it auto-resolves to `gemini` only if the primary `AI_PROVIDER` is `gemini` and the configured model advertises vision capability in `AI_MODEL_CATALOG` (`vision: true`).
  - If `AI_PROVIDER` is configured for any other provider (e.g. `openai` or `claude`), `IMAGE_PROVIDER` defaults to `none` rather than cross-routing visual crops to Gemini.
  - An explicit or defaulted `IMAGE_PROVIDER=gemini` requires a non-blank `GEMINI_API_KEY`, failing fast at startup if missing.
- **Self-Hosted Mode (`DEPLOYMENT_MODE=self_hosted`)**:
  - When `IMAGE_PROVIDER` is unset or empty, it checks whether the configured `OLLAMA_IMAGE_MODEL` advertises vision capability in `AI_MODEL_CATALOG` (`vision: true`). If vision capability is present, it defaults to `ollama`. If the model does not support vision or is unadvertised, it defaults safely to `none` with an explicit non-fatal startup log.
  - An explicit `IMAGE_PROVIDER=ollama` validates that the configured image model is not a known text-only model (e.g. `llama3.1` or `nomic-embed-text`), failing fast if a text-only model is configured for visual understanding.
- **Text-Only and Disabled Deployments**:
  - `IMAGE_PROVIDER=none` is always a valid non-fatal configuration across all deployment modes. When disabled, the visual understanding stage is bypassed: detected visual regions and document pages are recorded truthfully as `not_configured`, and standard text extraction, OCR, chunking, and embedding proceed normally.

### Supported Formats and Extraction Pipeline

- **Document Formats**: Visual extraction applies strictly to PDF documents (`.pdf`). Plain text documents (`.txt`, `.md`) do not contain visual elements and are marked `not_applicable`.
- **Detected Visual Elements**: PyMuPDF detects vector drawings, diagrams, embedded raster images, flowcharts, and structured figures.
- **Image Crops**: Bounding boxes of detected visual elements are cropped and rendered as standard PNG images (`image/png`), bounded by `IMAGE_UNDERSTANDING_MAX_BYTES` (default 10 MB).
- **Enrichment and Retrieval**: Generated visual descriptions are merged into the canonical page text and chunk text with section identifiers (e.g., `[Diagram]\n<description>`). Chunks containing visual descriptions are embedded and indexed into the vector store, allowing semantic queries to retrieve diagram and visual content.

### Error Semantics, Partial Success, and Rollup

Image understanding distinguishes between temporary infrastructure failures and per-visual content failures:

- **Temporary Provider Failures** (`TemporaryVisualServiceError` for rate limits, timeouts, network loss, or 5xx server errors):
  Treated as retryable processing errors (`IMAGE_UNDERSTANDING_FAILED`, retryable=True). The worker halts extraction and safely requeues the job with backoff.
- **Per-Visual Failures** (`VisualAnalysisError` for unsupported images, safety filter blocks, or provider-specific rejections):
  Recorded per-visual as `analysis_status="failed"` with `error_code="VISUAL_ANALYSIS_FAILED"`. The document extraction continues so other pages and valid text/visuals remain fully processable.
- **Partial-Success Behavior**:
  When a document contains multiple visual pages or visual regions where some succeed and some fail, the document status rolls up truthfully to `partial`. Successful visual descriptions are indexed and retrievable, while failed visuals are isolated without failing the whole document.
- **Document-Level Visual Status Rollup**:
  Exposed via API as `UploadedDocument.visual_analysis_status`:
  - `not_applicable`: Non-PDF documents or PDFs with no detected visual elements.
  - `pending`: Document is actively processing or pending extraction.
  - `not_configured`: Visual elements exist, but `IMAGE_PROVIDER=none` or visual analysis was unconfigured.
  - `completed`: All detected visual elements were successfully analyzed and indexed.
  - `partial`: Mixed outcomes (e.g., some succeeded and some failed, or some succeeded and some not configured).
  - `failed`: All relevant visual elements failed analysis.

### Operational Considerations: Latency, Costs, Hardware, and Privacy

- **Latency**:
  Documents containing multiple diagrams or figures incur sequential or batched visual analysis round trips bounded by `IMAGE_UNDERSTANDING_TIMEOUT_SECONDS` (default 30s) per visual. For visual-heavy documents, this increases total document ingestion time compared to text-only processing.
- **Metered API Costs**:
  In hosted deployments using Gemini, each visual crop sent for analysis consumes multimodal API tokens / requests according to Google Gemini pricing. Self-hosted deployments using Ollama run on local compute with zero external API costs.
- **Self-Hosted Hardware Requirements**:
  Running local multimodal vision models with Ollama requires sufficient GPU VRAM to keep both the vision encoder and LLM resident in GPU memory concurrently. Running under memory pressure, falling back to CPU execution, or frequent model eviction significantly increases document processing latency.
- **Privacy**:
  - **Self-Hosted (`IMAGE_PROVIDER=ollama`)**: Visual crops remain entirely within the local host/network (`OLLAMA_BASE_URL`). No image bytes leave the local infrastructure.
  - **Hosted (`IMAGE_PROVIDER=gemini`)**: Only cropped bounding-box PNGs of detected visual regions are transmitted over TLS to Google's Gemini API; entire PDF files or unrelated document text are not transmitted during the visual stage.
