# Lumina

Lumina is an open-source, student-first AI study workspace that you host
yourself. You add your course material, and Lumina extracts and indexes it, then
answers questions, writes study guides, generates and grades quizzes, and tracks
what you have actually learned — all grounded in your own documents.

With local storage and Ollama, and with no external provider or fallback
configured, document processing and AI inference can remain on the self-hosted
operator's infrastructure. External providers receive relevant content when
they are configured.

## What you can do

- Create a course workspace and add PDF sources to it.
- Have those sources extracted, chunked, and semantically indexed automatically.
- Ask questions about a course and get answers cited back to the page they came
  from.
- Generate study guides, flashcards, and practice quizzes from your material.
- Sit a quiz, have it graded, and see your progress and weak topics over time.
- Plan revision with Exam Mode: ranked topics, mock exams, and a study roadmap.

## Quickstart

This is the complete path from an empty directory to your first generated quiz.
It needs no clone, no build, no Node.js, and no CORS configuration: Compose pulls
the prebuilt image from `ghcr.io/coddesigning/lumina`.

### Prerequisites

| Requirement | Notes |
| --- | --- |
| Docker Engine or Docker Desktop | Supplies the whole stack. |
| Docker Compose v2.20 or newer | `docker compose version` must succeed. |
| [Ollama](https://ollama.com) | Runs the AI models locally. |
| x86-64 or ARM64 host | Linux, macOS, or Windows. |
| Disk space | Roughly 10 GB for models, plus room for your documents. |

Generation speed depends heavily on RAM and VRAM. The profile below targets
16 GB of system RAM and 8 GB of VRAM; a smaller machine still works, but answers
arrive more slowly.

### 1. Download the Compose file

Lumina runs from a directory holding two files. Keep this directory: it is
where every later `docker compose` command runs.

**Linux / macOS**

```bash
mkdir lumina && cd lumina
curl -fsSLO https://raw.githubusercontent.com/coDDesigning/Lumina/main/docker-compose.yml
curl -fsSLO https://raw.githubusercontent.com/coDDesigning/Lumina/main/.env.example
```

**Windows PowerShell**

```powershell
New-Item -ItemType Directory lumina | Out-Null; Set-Location lumina
Invoke-WebRequest https://raw.githubusercontent.com/coDDesigning/Lumina/main/docker-compose.yml -OutFile docker-compose.yml
Invoke-WebRequest https://raw.githubusercontent.com/coDDesigning/Lumina/main/.env.example -OutFile .env.example
```

To work on Lumina itself, clone the repository instead; see
[Development and tests](#development-and-tests).

### 2. Install and configure Ollama

Lumina needs one model from Ollama, for generation:

```bash
ollama pull qwen3.5:9b        # generates study guides and quizzes, and reads diagrams
```

Semantic search needs nothing from Ollama. Embeddings are computed inside the
application on CPU, so your material is indexed without an embedding endpoint
and without leaving the machine.

The containers reach Ollama on the host rather than inside Compose:

- **Docker Desktop (macOS, Windows):** the default
  `OLLAMA_BASE_URL=http://host.docker.internal:11434` already works.
- **Linux:** Ollama must listen on an address the Docker bridge can reach:

  ```bash
  OLLAMA_HOST=0.0.0.0:11434 ollama serve
  ```

> **Restrict port 11434 to your host and the Docker bridge with a firewall
> rule.** Ollama has no authentication, so anything able to reach that port can
> use your models and read the prompts you send them. Never expose it to the
> public internet.

### 3. Create your `.env`

**Linux / macOS**

```bash
install -m 0600 .env.example .env
```

**Windows PowerShell**

```powershell
Copy-Item .env.example .env
```

`.env` holds your secrets. Keep it private, and never commit it.

### 4. Set the values that have no safe default

| Setting | What it is |
| --- | --- |
| `COMPOSE_PROJECT_NAME` | Names the durable data volume. **Keep it stable forever** — changing it points Lumina at a new, empty volume. |
| `JWT_SECRET_KEY` | Signs login tokens. At least 32 characters. |

Generate the secret rather than inventing it:

**Linux / macOS**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

**Windows PowerShell**

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`token_urlsafe` avoids `$`, which Compose would otherwise try to interpolate; if
a value you choose does contain `$`, single-quote it in `.env`.

### 5. Configure the local model profile

The shipped defaults are sized for a hosted model with a very large context
window. A local 8B model has an 8,192-token window, and the default material
budget of 126,000 characters is roughly 31,500 tokens — about four times too
large. Ollama would silently truncate the prompt and answer from whatever
survived. Set these in `.env`:

```bash
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_NUM_CTX=8192

# Local models are far slower than a hosted API. A twenty-question quiz does not
# finish inside the 60-second default.
AI_GENERATION_TIMEOUT_SECONDS=180
AI_GENERATION_OVERALL_TIMEOUT_SECONDS=300

# Roughly 4,000 tokens, which leaves room for the prompt template and the answer
# inside the same 8,192-token window. This one ceiling caps every per-feature
# material budget (study guide, quiz, flashcard, tutor, Q&A, and all of Exam
# Mode), so a local profile stays one setting instead of thirteen.
MATERIAL_MAX_CHARS_CEILING=16000
```

These values are measured rather than estimated;
[`docs/ai_providers.md`](docs/ai_providers.md) has the benchmark and the
reasoning.

### 6. Start Lumina

```bash
docker compose up --detach --wait --wait-timeout 600
docker compose ps --all
```

The first run pulls the prebuilt image, `ghcr.io/coddesigning/lumina:latest`,
for `linux/amd64` or `linux/arm64`. It carries the embedding model baked in, so
the running container never needs the network for it. Later runs reuse the
pulled image and take seconds.

To build the image from your checkout instead, add `--build`. That takes around
ten minutes the first time, most of it downloading the embedding model.

`lumina` and `lumina-worker` should both be healthy. Confirm the stack is serving:

```bash
curl --fail http://127.0.0.1:10312/health/ready
```

```json
{"status":"ready"}
```

### 7. Open Lumina and create the administrator

<http://127.0.0.1:10312>

Register through the sign-up form. The first account created on a fresh
deployment becomes the administrator; everyone who registers after it is an
ordinary user.

The password must be at least 8 characters and at most 72 bytes, must not be a
common password or a simple repeated or sequential pattern, and must not contain
your name or the local part of your email address.

> Create the administrator before you let anyone else reach Lumina. If you set
> `LUMINA_BIND_ADDRESS=0.0.0.0` before registering, whoever registers first
> becomes the administrator. To reserve that account for one address instead,
> set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_TOKEN` together; see
> [`docs/deployment.md`](docs/deployment.md#protected-administrator-bootstrap).

### 8. Create your first course and quiz

1. Select **New course** and give it a name.
2. Open the course.
3. Select **Add Sources** and upload a PDF of up to 50 MB.
4. Wait until the course reports **Sources ready**. A short text PDF takes
   seconds; a long scanned one needs OCR and takes minutes.
5. Select **Make something**, then **Practice quiz**.
6. Choose how many questions and the difficulty, then generate.
7. Answer the questions and submit.
8. Review your score, the explanations, and the citations back to your material.

### If something goes wrong

| Symptom | Cause and fix |
| --- | --- |
| `up` says the env file is not found | You skipped step 3. Lumina reads its whole configuration from `.env`; copy `.env.example` over. |
| `lumina` exits straight away | A setting is missing or malformed. `docker compose logs lumina` names the variable. |
| `up` says the port is already allocated | Something else holds `LUMINA_PORT`. Change it in `.env`, or stop the other program. |
| The page loads but every request fails | An earlier version of this stack may still be running. `docker compose down --remove-orphans` (never `--volumes`), then `up` again. |
| A source stays in processing | Extraction or OCR is still running. Watch `docker compose logs --follow lumina-worker`. |
| A source reaches failed | The PDF is encrypted, corrupt, or beyond the configured page and size limits. |
| Generation says the provider is unreachable | Ollama is not running, or `OLLAMA_BASE_URL` is wrong for your platform. Check from inside the stack with `docker compose exec lumina python -c "import os, urllib.request; print(urllib.request.urlopen(os.environ['OLLAMA_BASE_URL'] + '/api/tags', timeout=5).status)"`. |
| Generation says the model is missing | Run the `ollama pull` command from step 2, then `ollama list` to confirm. |
| Material is not indexed | Chunks exist but their vectors do not. Run `docker compose run --rm --no-deps lumina-worker python -m workers.embedding_backfill`. |
| No relevant material | Retrieval found nothing above the similarity floor. Widen the topic, or add a source that covers it. |
| Generation times out | Raise `AI_GENERATION_TIMEOUT_SECONDS` and `AI_GENERATION_OVERALL_TIMEOUT_SECONDS`, or ask for fewer questions. A model that does not fit entirely in VRAM runs roughly five times slower. |

## Stop and update

```bash
docker compose stop                 # pause; nothing is lost
docker compose down                 # remove containers; named volumes are kept
docker compose up --detach --wait --wait-timeout 600
```

`up` starts whatever image is already on the machine. To update, fetch the
Compose file and the image together, so the two cannot disagree:

```bash
curl -fsSLO https://raw.githubusercontent.com/coDDesigning/Lumina/main/docker-compose.yml
docker compose pull lumina
docker compose up --detach --wait --wait-timeout 600
```

`latest` follows `main`. To stay on one release, set `LUMINA_IMAGE` in `.env` to
`ghcr.io/coddesigning/lumina:<full commit SHA>`, and download the Compose file
from that same commit. If you run a build of your own clone instead, use
`docker compose up --build` after every `git pull` so the image matches the
code.

> `docker compose down --volumes` **permanently deletes your database, uploaded
> documents, and search index.** There is no undo. Use it only when you intend
> to destroy the deployment.

### Changing the port

`LUMINA_PORT` in `.env` is the whole address. Change it and run `up` again:
nothing needs rebuilding, because the interface asks for `/api` on whatever
origin served it. Set `LUMINA_BIND_ADDRESS=0.0.0.0` to reach Lumina from
another machine on your network.

If you put a TLS reverse proxy in front, set `FORWARDED_ALLOW_IPS` to that
proxy's address and turn on `SECURITY_HSTS_ENABLED`. Leave `FORWARDED_ALLOW_IPS`
unset otherwise: it tells the API whose `X-Forwarded-For` header to believe, and
a caller allowed to set its own would also be choosing its own rate-limit
identity.

## Data and backups

Everything durable lives in one named volume, `lumina-data`: the SQLite
database, your uploaded documents, and the Chroma vector index.

All three must be captured as a single consistent set. Copying a live SQLite
file or Chroma directory is not a supported backup — the supported wrapper stops
the writing services first. Run it from the directory that holds
`docker-compose.yml`:

```bash
curl -fsSL --create-dirs -o ops/self_hosted_backup.sh https://raw.githubusercontent.com/coDDesigning/Lumina/main/ops/self_hosted_backup.sh
export LUMINA_BACKUP_DIRECTORY=/mnt/lumina-backups
sudo install -d -o 10001 -g 10001 -m 0700 "${LUMINA_BACKUP_DIRECTORY}"
sh ops/self_hosted_backup.sh
```

A backup archive contains personal data and password hashes, so store it on
encrypted, off-host storage. The complete restore and rollback procedure is in
[`docs/self-hosted-backup.md`](docs/self-hosted-backup.md).

## Development and tests

Working on Lumina itself needs the repository:

```bash
git clone https://github.com/coDDesigning/Lumina.git
cd Lumina
```

The commands below mirror [`.github/workflows/ci.yml`](.github/workflows/ci.yml), which is
the executable source of truth.

### Backend

Python 3.12, with hash-locked dependencies:

```bash
python -m pip install --require-hashes --only-binary=:all: --requirement requirements-dev.txt
python -m pip check
python -m ruff check --no-cache .
python -m ruff format --check .
python -m pytest -q -p no:cacheprovider tests
```

### Frontend

Node 22.22.0:

```bash
cd frontend
npm ci --no-audit --no-fund
npm run lint
npm run test:coverage
npm run build
npx playwright install chromium
npm run test:e2e
```

`npm run lint` passing does not mean `npm run build` passes: the build also
type-checks the test and browser suites. Run both.

### Container stack

```bash
docker compose config --quiet
docker compose -f docker-compose.hosted.yml config --quiet
docker compose up --build --detach --wait --wait-timeout 600
```

Without `--build`, Compose runs the published image instead of your changes.

One image carries both halves: its first build stage compiles the interface,
and the API serves the result beside `/api` from a single port. `docker build`
alone builds it; `VITE_API_BASE_URL` is a `--build-arg`, not a runtime setting.
The published image is built with `/api`.

`.github/workflows/publish-image.yml` publishes that image to
`ghcr.io/coddesigning/lumina` on every push to `main`, for `linux/amd64` and
`linux/arm64`, tagged `latest` and with the full commit SHA.

## Deployment modes

| Mode | Database | Documents | Vectors | Interface |
| --- | --- | --- | --- | --- |
| `self_hosted` (default) | SQLite | Local filesystem | Chroma | Served by the API from the same origin |
| `hosted` | PostgreSQL | S3-compatible | pgvector | S3 and CloudFront in AWS; served by the API locally |

The self-hosted stack is single-host and single-worker by design. See
[`docs/deployment.md`](docs/deployment.md) for the full topology, the routing
contract, and production configuration.

## Further documentation

| Topic | Document |
| --- | --- |
| Deployment, topologies, health probes | [`docs/deployment.md`](docs/deployment.md) |
| Backup and restore | [`docs/self-hosted-backup.md`](docs/self-hosted-backup.md) |
| AI providers and the local model profile | [`docs/ai_providers.md`](docs/ai_providers.md) |
| Authentication and account security | [`docs/authentication.md`](docs/authentication.md) |
| Rate limiting | [`docs/rate_limiting.md`](docs/rate_limiting.md) |
| Database, migrations, and workers | [`docs/database.md`](docs/database.md) |
| Vector storage and embeddings | [`docs/vector_storage.md`](docs/vector_storage.md) |
| Citations | [`docs/citations.md`](docs/citations.md) |
| Credits | [`docs/credits.md`](docs/credits.md) |
| Exam Mode and study roadmap | [`docs/exam_roadmap.md`](docs/exam_roadmap.md) |
| Frontend architecture | [`docs/frontend_system.md`](docs/frontend_system.md) |
| Frontend testing | [`docs/frontend_testing.md`](docs/frontend_testing.md) |
| Prompt templates | [`docs/prompt_templates.md`](docs/prompt_templates.md) |
| Observability | [`docs/observability.md`](docs/observability.md) |
| AI usage telemetry | [`docs/ai_usage_telemetry.md`](docs/ai_usage_telemetry.md) |
| Dependencies | [`docs/dependencies.md`](docs/dependencies.md) |
| Legal policy sources and update process | [`docs/legal/README.md`](docs/legal/README.md) |
| Operational runbooks | [`docs/runbooks/`](docs/runbooks/) |
| Branch protection and status checks | [`docs/branch_protection.md`](docs/branch_protection.md) |
| PR-Agent | [`docs/pr-agent.md`](docs/pr-agent.md) |

## Licence and security

Copyright © 2026 coDDesigning contributors. Lumina is licensed under the
[GNU Affero General Public License version 3 only](LICENSE) (`AGPL-3.0-only`).
Third-party components retain their own terms; see
[Third-Party Notices](THIRD_PARTY_NOTICES.md).

Report suspected vulnerabilities privately as described in the
[Security Policy](SECURITY.md). Do not open a public issue for an exploitable
weakness.
