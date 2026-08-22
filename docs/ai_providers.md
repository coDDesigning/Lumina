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
| `openai` | Recognized, not implemented | — |
| `claude` | Recognized, not implemented | — |

`IMPLEMENTED_AI_PROVIDERS` in `backend/app/config.py` is the authoritative list.
The provider factory reads it rather than restating provider names, so the two
cannot drift apart.

The same rule covers `AI_FALLBACK_PROVIDERS`: a fallback naming an
unimplemented provider is rejected at startup rather than failing the first time
the primary provider errors and the fallback is actually reached.

Selecting `openai` or `claude` fails at startup, not on the first generation
request:

```
ValueError: AI_PROVIDER 'openai' is recognized but not implemented yet.
Implemented providers: gemini, ollama.
```

An unrecognized spelling fails with the list of accepted names, which keeps a
typo (`gemeni`) distinguishable from a genuine roadmap provider (`openai`).

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
| `AI_FALLBACK_PROVIDERS` | any token is unrecognized, or recognized but not implemented |

Configuration validation deliberately does **not** contact Ollama. Booting the
API must not depend on a model server being up, so reachability is a
generation-time concern.

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
model's own context window. Lower it for small local models on modest hardware;
`120000` characters is roughly 30k tokens, which the default `llama3.1` context
window accommodates but a slower machine may not want to spend.

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

## Telemetry and Privacy

`services/ai_usage_logger.py` records provider, model, token counts, latency,
success, and a stable error category. It never persists prompts, course
material, or generated text. Provider and model default to whatever
`AI_PROVIDER` selects, so failure telemetry attributes errors to the provider
that actually ran.

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
| `IMAGE_PROVIDER` | `none` | Implemented: `none` (disabled), `ollama`, `gemini`. `openai` and `claude` are recognized and fail at startup. |
| `OLLAMA_IMAGE_MODEL` | `llama3.2-vision` | Multimodal model used with the shared `OLLAMA_BASE_URL`. |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash` | Multimodal model used with `GEMINI_API_KEY`. |
| `IMAGE_UNDERSTANDING_TIMEOUT_SECONDS` | `30` | Per-visual deadline, 1-300 seconds. |
| `IMAGE_UNDERSTANDING_MAX_BYTES` | `10485760` | Maximum accepted rendered image size (10 MB). |

### Error Semantics and Failure Isolation

Image understanding distinguishes between temporary infrastructure failures and
per-visual content failures:

- **Temporary provider failures** (`TemporaryVisualServiceError` for rate limits,
  timeouts, network loss, and 5xx server errors): treated as retryable processing
  errors (`IMAGE_UNDERSTANDING_FAILED`, retryable=True). The worker halts extraction
  and safely requeues the job with backoff.
- **Per-visual non-fatal failures** (`VisualAnalysisError` for unsupported images,
  safety blocks, or provider rejection): recorded per-visual as `FAILED` with
  `error_code="VISUAL_ANALYSIS_FAILED"`. The document extraction continues so that
  other pages and valid text/visuals remain fully processable.
- **Disabled/Not-Configured**: when `IMAGE_PROVIDER=none`, visual regions are marked
  explicitly as `NOT_CONFIGURED` without entering the `understanding_images` pipeline
  stage or pretending visuals were analyzed.

### Privacy Implications and Deployment Modes

- **Self-Hosted (`ollama`)**: Visual regions are rendered and sent to the local
  Ollama instance over the internal network (`OLLAMA_BASE_URL`). No image bytes or
  document contents leave the host.
- **Cloud (`gemini`)**: Visual crops are sent to Google Gemini via the Google GenAI
  SDK. Only the rendered bounding boxes of detected visual regions (not entire PDF
  pages or unrelated documents) are transmitted.
