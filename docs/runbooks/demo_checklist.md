# Operational Runbook: Pre-Demo Verification Checklist

This runbook establishes the formal pre-demo operational checklist and in-demo fallback procedures for Lumina.
It guarantees deterministic demo execution across both `self_hosted` and `hosted` deployment modes.

---

## 1. Phase 1: T-24h Preparation

Execute these steps 24 hours prior to the scheduled demonstration to establish a stable, fully seeded baseline.

### 1.1 Pin Deployment Mode and Storage Topology

Ensure the target deployment mode is explicitly configured:

* **Self-Hosted Mode (`DEPLOYMENT_MODE=self_hosted`)**:
  * Verify `STORAGE_BACKEND=local`.
  * Verify `VECTOR_BACKEND=chroma`.
  * Confirm database is SQLite (`DATABASE_URL=sqlite:////data/lumina.db` or local path).
* **Hosted Mode (`DEPLOYMENT_MODE=hosted`)**:
  * Verify `STORAGE_BACKEND=s3` with `S3_BUCKET_NAME` and AWS credentials.
  * Verify `VECTOR_BACKEND=pgvector`.
  * Confirm database is PostgreSQL (`DATABASE_URL=postgresql+psycopg://...`).

### 1.2 Confirm AI Provider Credentials and Model Names

Verify that the primary and fallback providers are reachable and configured with valid models and keys.

1. **Self-Hosted Setup (Local Ollama)**:
   ```bash
   # Verify Ollama service is running and models are downloaded
   curl -s http://localhost:11434/api/tags
   ollama pull llama3.1
   ```
   Ensure `.env` sets:
   * `OLLAMA_BASE_URL=http://localhost:11434` (this is what makes `ollama:*` models available)
   * `OLLAMA_MODEL=llama3.1`

   Embeddings need nothing here: they are computed in-process.

2. **Hosted Setup (Gemini / Multi-Provider Cloud)**:
   Ensure environment variables or AWS SSM Parameter Store parameters are populated:
   * `GEMINI_API_KEY=<valid-gemini-api-key>` (this alone makes `gemini:*` models available)
   * `AI_DEFAULT_MODEL=gemini:gemini-3.6-flash` (optional; otherwise the first available vendor wins)
   * `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` only if you accept that a Gemini outage bills them

### 1.3 Raise Generation Timeout Defaults for Local Models

When using self-hosted local LLMs on CPU or hardware-constrained laptops, raise timeout thresholds to prevent request aborts during live generation:

```bash
# In .env or container environment
AI_GENERATION_TIMEOUT_SECONDS=180
DEFAULT_EMBEDDING_TIMEOUT_SECONDS=120
AI_GENERATION_MAX_ATTEMPTS=3
```

### 1.4 Apply and Verify Database Migrations

Bring the database schema to the latest canonical revision:

```bash
# Execute migrations to Alembic head
python -m alembic upgrade head

# Confirm database is aligned with single canonical head
python -m alembic current --check-heads
python -m alembic check
```

Expected output from `current --check-heads`: The active head revision matching `alembic/versions/` (e.g. `b88c7483c27d (head)`).

### 1.5 Seed Deterministic Demo Data

Run the demo seeder to provision pre-configured learner accounts, courses, representative syllabus documents, ready-made flashcards, and sample quiz attempts:

```bash
python -m scripts.seed_demo
```

Expected output:
* Demo admin account: `admin@example.com`
* Demo learner account: `student@example.com`
* Sample course (e.g. `Distributed Systems 101` or `Introduction to Computer Architecture`) with processed documents in `ready` state, pre-generated study guides, and existing quiz history.

### 1.6 Take Pre-Demo Backup Snapshot

Create a verified restore point prior to demo rehearsals.

* **Self-Hosted Environment**:
  ```bash
  export LUMINA_BACKUP_DIRECTORY=/tmp/lumina-demo-backups
  sh ops/self_hosted_backup.sh
  ```
  Refer to [Self-Hosted Backup & Restore](../self-hosted-backup.md) for full snapshot details.

* **Hosted Environment**:
  Create an on-demand RDS database snapshot:
  ```bash
  aws rds create-db-snapshot \
    --db-instance-identifier lumina-demo-db \
    --db-snapshot-identifier lumina-demo-pre-demo-snapshot
  ```
  Refer to [Hosted Backup & Restore Drill](hosted-backup-restore.md) for hosted backup procedures.

---

## 2. Phase 2: T-30m Verification

Execute this exact sequence of commands 30 minutes before demo start. Every command must produce the specified expected output.

```
+-----------------------------------------------------------------------------------+
|                            T-30m Verification Flow                                |
|                                                                                   |
|  [1. Health Probes] --> [2. Worker Liveness] --> [3. Document Ingestion]          |
|                                                              |                    |
|  [6. Progress Dashboard] <-- [5. Quiz & Attempt] <-- [4. Summary Generation]      |
|           |                                                                       |
|  [7. Admin Authorization Gate] --> Ready for Demo                                 |
+-----------------------------------------------------------------------------------+
```

### Step 1: Health Probes

Verify API and downstream dependency readiness.

* **Command**:
  ```bash
  # Self-Hosted / Direct API:
  curl -s -f http://127.0.0.1:8000/health/ready

  # Through Frontend Reverse Proxy:
  curl -s -f http://127.0.0.1:8080/api/health/ready
  ```
* **Expected Output**:
  ```json
  {"status": "ready"}
  ```
* **HTTP Status**: `200 OK`

### Step 2: Document Worker Liveness

Confirm the asynchronous document processing worker is running and capable of leasing jobs.

* **Self-Hosted (Local / Compose)**:
  ```bash
  # Run worker readiness check
  python -m workers.document_processor --check
  ```
  *Expected Output*: Exit code `0` (Worker readiness check succeeded).
  If using Docker Compose:
  ```bash
  docker compose ps worker
  ```
  *Expected Output*: `worker` service status is `Up` / `healthy`.

* **Hosted (AWS ECS)**:
  ```bash
  aws ecs describe-services \
    --cluster lumina-prod-cluster \
    --services lumina-worker-service \
    --query 'services[0].{runningCount:runningCount,desiredCount:desiredCount,status:status}'
  ```
  *Expected Output*: `runningCount` equals `desiredCount` and `status` is `ACTIVE`.

### Step 3: Test Document Upload & Ingestion to Ready

Upload a sample test document and verify automatic text extraction, chunking, and embedding generation.

1. **Obtain Learner Authentication Token**:
   ```bash
   TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
     -H "Content-Type: application/json" \
     -d '{"email": "student@example.com", "password": "StudentPassword123!"}' \
     | jq -r '.access_token')
   ```

2. **Upload Document**:
   ```bash
   DOC_RESP=$(curl -s -X POST http://127.0.0.1:8000/api/courses/1/documents \
     -H "Authorization: Bearer $TOKEN" \
     -F "file=@tests/fixtures/sample_lecture.pdf")
   DOC_ID=$(echo "$DOC_RESP" | jq -r '.id')
   ```
   *Expected HTTP Status*: `201 Created` or `200 OK` with `status: "uploaded"`.

3. **Verify Transition to Ready**:
   ```bash
   # Poll until status is ready (typically 2-10s)
   curl -s -X GET http://127.0.0.1:8000/api/courses/1/documents/$DOC_ID \
     -H "Authorization: Bearer $TOKEN" | jq '{id: .id, status: .status}'
   ```
   *Expected Output*:
   ```json
   {
     "id": "<DOC_ID>",
     "status": "ready"
   }
   ```

### Step 4: Summary Generation (Study Guide)

Trigger semantic retrieval and AI study guide generation for a course topic.

* **Command**:
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/courses/1/study-guide \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"topic": "Core Architecture Concepts"}'
  ```
* **Expected Output**:
  * HTTP Status: `200 OK`
  * JSON payload containing structured Markdown content, headings, key takeaways, and source citations (`citations: [...]`).
  * `X-Error-Code` header is absent.

### Step 5: Quiz Generation and Graded Attempt Submission

Generate a dynamic quiz, submit an attempt, and verify automated evaluation.

1. **Generate Quiz**:
   ```bash
   QUIZ_RESP=$(curl -s -X POST http://127.0.0.1:8000/api/courses/1/quiz \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"topic": "System Design", "question_count": 3}')
   QUIZ_ID=$(echo "$QUIZ_RESP" | jq -r '.id')
   ```
   *Expected Output*: HTTP 200 with list of questions, answer options, and citations.

2. **Submit Graded Attempt**:
   ```bash
   curl -s -X POST "http://127.0.0.1:8000/api/courses/1/quizzes/$QUIZ_ID/attempts" \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"answers": [{"question_id": 1, "selected_option_index": 0}]}'
   ```
   *Expected Output*: HTTP 200/201 with `score`, `graded_count`, and explanation feedback.

### Step 6: Progress Dashboard & Weak Topic Accuracy

Verify that the dashboard correctly derives course status, mastery levels, and weak topic diagnostics.

* **Command**:
  ```bash
  curl -s -X GET http://127.0.0.1:8000/api/progress \
    -H "Authorization: Bearer $TOKEN" | jq '.courses[] | {course_id: .course_id, status: .status, average_score: .average_score}'
  ```
* **Expected Output**:
  * HTTP Status: `200 OK`
  * Course status correctly computed as `practiced` or `mastered` (not hardcoded to zero; derived at read time from attempts).
  * `GET /api/courses/1/progress` returns accuracy rates and weak topic breakdowns.

### Step 7: Admin Endpoint Authorization Check

Verify strict role-based access control by asserting that learner tokens cannot reach administrative routes.

* **Command**:
  ```bash
  curl -s -w "\nHTTP_STATUS:%{http_code}\n" -X GET http://127.0.0.1:8000/api/admin/users \
    -H "Authorization: Bearer $TOKEN"
  ```
* **Expected Output**:
  * Response body: `{"detail": "Forbidden"}` or role requirement failure.
  * Extracted HTTP Status: `HTTP_STATUS:403`

---

## 3. Phase 3: In-Demo Fallbacks & Environment Hygiene

Procedures to handle live edge cases and presenter device standards.

### 3.1 AI Provider Outage & Failover Chain

If the primary AI provider experiences degradation or rate limiting during a live presentation:

1. **Automatic Failover**: Lumina's `ReliableTextGenerationProvider` automatically attempts every other vendor whose credential is configured.
2. **Pre-Seeded Content**: If all live AI generation fails, navigate to pre-generated study guides, flashcards, and quizzes created during Phase 1 (`python -m scripts.seed_demo`).
3. **Operational Runbook**: Follow the detailed recovery instructions in [AI Provider Outage Runbook](provider_outage.md).

### 3.2 Stuck Document Ingestion

If a live document upload remains in `processing` or fails:

1. **Run Instant Lease Recovery**:
   ```bash
   # Self-Hosted
   docker compose run --rm worker python -m workers.document_processor --once

   # Hosted ECS Task
   aws ecs run-task --cluster lumina-prod-cluster --task-definition lumina-worker-task \
     --overrides '{"containerOverrides": [{"name": "worker", "command": ["python", "-m", "workers.document_processor", "--once"]}]}'
   ```
2. **Operational Runbook**: Follow diagnostic steps in [Stuck Document Processing Runbook](stuck_document.md).

### 3.3 Mid-Session Clean State Reset

To reset all interactive attempt history and user modifications between demo sessions without dropping seed accounts:

* **Command**:
  ```bash
  python -m scripts.seed_demo --reset
  ```
* **Expected Behavior**:
  * Clears transient quiz attempts, conversation turns, and newly uploaded test files.
  * Preserves default seed accounts (`student@example.com`, `admin@example.com`) and baseline courses in clean ready state.

### 3.4 Presentation Environment & Viewport Specifications

Configure presenter display and browser environment to match standard UI layout tokens:

* **Target Browser**: Google Chrome (latest evergreen release) or Chromium-based browser.
* **Display Resolution**: 1920 × 1080 (16:9 1080p full desktop) or 1440 × 900 minimum desktop resolution.
* **Operating System Scaling**: 100% DPI scaling (disable OS display scaling magnification).
* **Browser Zoom Level**: 100% (`Ctrl + 0` / `Cmd + 0`).
* **Theme Preference**: Ensure consistent system theme (Light mode recommended for projectors; Dark mode supported via UI toggle).
* **Browser Hygiene**:
  * Use a clean dedicated browser profile or incognito window to prevent extension interference (e.g. ad blockers modifying DOM).
  * Clear `localStorage` and application cache prior to starting the demo presentation.
