# AI Providers

Lumina reaches every AI model through one abstraction, `TextGenerationProvider`
in `services/text_generation.py`. The five generation features — study guide,
quiz, flashcards, prompt generator, and AI tutor — depend on that protocol and
never on a specific vendor.

## Implemented Providers

| `AI_PROVIDER` | Status | Required configuration |
|---|---|---|
| `ollama` | Implemented (default) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT_SECONDS` — all defaulted |
| `gemini` | Implemented | `GEMINI_API_KEY` |
| `openai` | Recognized, not implemented | — |
| `claude` | Recognized, not implemented | — |

`IMPLEMENTED_AI_PROVIDERS` in `backend/app/config.py` is the authoritative list.
The provider factory reads it rather than restating provider names, so the two
cannot drift apart.

Selecting `openai` or `claude` fails at startup, not on the first generation
request:

```
ValueError: AI_PROVIDER 'openai' is recognized but not implemented yet.
Implemented providers: gemini, ollama.
```

An unrecognized spelling fails with the list of accepted names, which keeps a
typo (`gemeni`) distinguishable from a genuine roadmap provider (`openai`).

## Self-Hosted Ollama Setup

1. Install Ollama from <https://ollama.com> and start it. It listens on
   `http://localhost:11434` by default.

   ```bash
   ollama serve
   ```

2. Pull a model that meets the capability bar below.

   ```bash
   ollama pull llama3.1
   ```

3. Configure the backend. These are the defaults, so a local Ollama on the
   standard port needs no configuration at all:

   ```bash
   AI_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.1
   OLLAMA_TIMEOUT_SECONDS=300
   ```

4. Start the API, upload a document to a course, wait for its status to reach
   `ready`, then generate:

   ```bash
   curl -X POST http://localhost:8000/api/courses/1/study-guide \
     -H "Authorization: Bearer $TOKEN"
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
- **A context window large enough for the prompt.** The study-guide prompt
  concatenates every ready chunk of a course, so the window must accommodate the
  whole course's extracted text plus the template and the response. Small
  context windows silently truncate the material and produce thin guides.
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
| `OLLAMA_TIMEOUT_SECONDS` | not an integer between 1 and 3600 |

Configuration validation deliberately does **not** contact Ollama. Booting the
API must not depend on a model server being up, so reachability is a
generation-time concern.

## Error Semantics

The provider translates transport and protocol failures into typed application
errors, all subclasses of `TextGenerationError`:

| Provider condition | Exception | HTTP status |
|---|---|---|
| Ollama unreachable at the configured base URL | `TextGenerationConnectionError` | `503` |
| No response within `OLLAMA_TIMEOUT_SECONDS` | `TextGenerationTimeoutError` | `503` |
| Non-2xx status from Ollama, e.g. model not found | `TextGenerationResponseError` | `500` |
| Response envelope missing or malformed | `TextGenerationResponseError` | `500` |
| Generated text empty or whitespace | `TextGenerationResponseError` | `500` |
| Generated text not valid JSON | `TextGenerationResponseError` | `500` |
| Valid JSON failing the feature's schema | `<Feature>GenerationError` | `500` |

Infrastructure failures (`503`) are kept distinct from generation failures
(`500`) so an operator can tell "Ollama is down" from "the model produced
garbage" without reading a stack trace. An HTTP 500 body from Ollama is never
treated as generated content.

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
