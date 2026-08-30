# Operational Runbook: AI Provider Outage & Degradation

## Overview

Lumina routes text generation to whichever vendors are configured:
* **Availability:** a vendor is in play because its credential or endpoint is set (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`). There is no provider or fallback setting to inspect; read the `ai_vendors_available` startup log line to see the derived chain.
* **Embeddings are not affected by a vendor outage.** They are computed in-process, so retrieval and document indexing keep working even when every generation vendor is down.
* **Resilience:** `ReliableTextGenerationProvider` automatically retries transient 5xx / connection errors up to `AI_GENERATION_MAX_ATTEMPTS` (default: 3) with exponential backoff before failing over or surfacing `provider_unavailable`.

---

## 1. Diagnosis

### Symptoms
* AI Generation routes (`/api/courses/{id}/quiz`, `/study-guide`, `/ai-tutor`, `/course-qa`, `/flashcards`, `/prompt-generator`) return `503 Service Unavailable` or `504 Gateway Timeout` with `X-Error-Code: provider_unavailable` or `provider_timeout`.
* Document processor OCR / image understanding or embedding generation fails during extraction with `AI_SERVICE_UNAVAILABLE`.
* CloudWatch ALB Target 5xx alarms trigger.

### Diagnostic Queries

#### CloudWatch Logs Insights:
```sql
fields @timestamp, request_id, http_path, http_status, error_code, exception_type, message
| filter error_code in ["provider_unavailable", "provider_timeout", "AI_SERVICE_UNAVAILABLE"]
| sort @timestamp desc
| limit 50
```

#### Application Log Inspection (JSON):
Search for `exception_type` matching `TextGenerationConnectionError`, `TextGenerationTimeoutError`, `TextGenerationServiceUnavailableError`, `EmbeddingConnectionError`, or `EmbeddingTimeoutError`.

---

## 2. Safe Remediation

### Self-Hosted Environment (Ollama Provider)

1. **Verify Ollama daemon status from host/container network:**
   ```bash
   curl -s http://localhost:11434/api/tags || curl -s http://host.docker.internal:11434/api/tags
   ```

2. **Verify Required Models are Installed on the Ollama Host:**
   ```bash
   ollama list
   ollama pull llama3.1
   ```
   Only the generation model is needed. Embeddings are computed in-process and
   do not depend on Ollama.

3. **Restart the Ollama Host Service (if running as a system service on host):**
   ```bash
   systemctl restart ollama
   ```

4. **Verify Application Readiness:**
   ```bash
   curl -f http://127.0.0.1:8000/health/ready   # or http://127.0.0.1:8080/api/health/ready through the frontend
   ```

### Hosted Environment Multi-Provider Failover (Gemini / OpenAI / Claude)

1. **Check Upstream Provider Status:**
   * Google Cloud: <https://status.cloud.google.com>
   * OpenAI: <https://status.openai.com>
   * Anthropic: <https://status.anthropic.com>

2. **Automatic Multi-Provider Failover:**
   Every configured vendor is already in the chain; there is nothing to enable. `ReliableTextGenerationProvider` attempts the next vendor upon transient failure or exhaustion of retries, which also means an outage of one vendor bills the next — `GET /api/admin/ai-costs` reports that spend per vendor and model.
   Check CloudWatch EMF metric `Lumina/AI ProviderErrors` broken down by `Provider` dimension to identify which provider is degraded.

3. **Manual Primary Provider Failover (during sustained primary outage):**
   If the primary provider suffers a prolonged outage or quota exhaustion:
   * Point `AI_DEFAULT_MODEL` at an available alternate, e.g. `openai:gpt-5.6-terra` or `claude:claude-sonnet-5`.
   * To take a vendor out of the chain entirely, remove its API key; availability follows the credentials.
   * Verify corresponding API keys are populated in SSM Parameter Store:
     * `/<project>-<environment>/openai-api-key`
     * `/<project>-<environment>/anthropic-api-key`
     * `/<project>-<environment>/gemini-api-key`
   * Update task definition environment variables or redeploy ECS services:
     ```bash
     aws ecs update-service --cluster <CLUSTER_NAME> --service <API_SERVICE> --force-new-deployment
     aws ecs update-service --cluster <CLUSTER_NAME> --service <WORKER_SERVICE> --force-new-deployment
     ```

4. **Update Key or Rotate in SSM (if quota/auth failure):**
   ```bash
   aws ssm put-parameter \
     --name "/<project>-<environment>/<provider>-api-key" \
     --value "<NEW_KEY>" \
     --type SecureString \
     --overwrite
   ```
   Then trigger a rolling deployment so ECS tasks pick up the updated parameter:
   ```bash
   aws ecs update-service --cluster <CLUSTER_NAME> --service <API_SERVICE> --force-new-deployment
   aws ecs update-service --cluster <CLUSTER_NAME> --service <WORKER_SERVICE> --force-new-deployment
   ```

---

## 3. Validation

1. **Verify Health Probes:**
   ```bash
   curl -f http://127.0.0.1:8000/health/ready   # or http://127.0.0.1:8080/api/health/ready through the frontend
   ```
   Expected: HTTP 200 `{"status": "ready"}`.

2. **Verify Telemetry Recording:**
   Check that new generation requests produce successful telemetry records in `ai_usage_logs`:
   ```sql
   SELECT id, user_id, generation_type, model_name, latency_ms, error_category, created_at
   FROM ai_usage_logs
   ORDER BY created_at DESC
   LIMIT 5;
   ```

---

## 4. Escalation Criteria

* **Extended Upstream Outage:** If Google Gemini or upstream providers suffer a prolonged outage triggering continuous ALB 5xx alarms, post an operational incident notice and notify stakeholders.
* **Persistent Embedding Failures:** If document extraction jobs fail on embeddings across retries, verify whether the configured fallback embedding model is available and run `python -m workers.embedding_backfill` once the provider recovers.
