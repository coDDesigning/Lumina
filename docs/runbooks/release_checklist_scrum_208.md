# Operational Runbook: SCRUM-208 Release Checklist

## Privacy-Safe Account Hard Deletion & Retained-Data Cleanup

This checklist defines the operational verification and release gate requirements for SCRUM-208 / SCRUM-209,
ensuring privacy-safe account hard deletion, immediate credential and session fencing, and resilient multi-tier
storage, vector, and relational cleanup across both self-hosted and hosted deployment topologies.

---

## 1. Pre-Deployment Release Gate Verification

Before deploying this release to staging or production, verify all automated quality controls pass:

```bash
# 1. Linting and type-checks
python -m ruff check --no-cache .
python -m ruff format --check .
git ls-files '*.py' | xargs -r python -m py_compile

# 2. Database migrations check
python -m alembic upgrade head
python -m alembic current --check-heads
python -m alembic check

# 3. Targeted account deletion and migration test suite
python -m pytest -q -p no:cacheprovider tests/test_account_deletion.py tests/test_database_contract.py tests/test_migrations.py

# 4. Frontend quality and build
cd frontend
npm run lint
npm test
npm run build
```

---

## 2. Feature & Architecture Verification Checklist

### 2.1 Re-Authentication & User-Facing Safety
- [ ] **Account > Security UI**: Accessible danger zone with destructive visual styling, explicit copy describing the scope of permanent deletion, and a button to initiate deletion.
- [ ] **Password Re-Authentication**: User must provide their current password. Incorrect password fails with safe generic error `400 Bad Request` (`X-Error-Code: account_deletion_reauthentication_failed`).
- [ ] **Typed Confirmation**: User must type exact phrase `"DELETE"` into the confirmation dialog; confirmation button remains disabled until both phrase and password are entered.
- [ ] **Protected Initial Administrator**: The initial bootstrap administrator (`is_initial_admin = True`) cannot self-delete (`409 Conflict`, `X-Error-Code: initial_admin_deletion_forbidden`).
- [ ] **Admin Boundary Enforcement**: Self-service deletion is strictly owner-only. An administrator cannot delete another user's account without an explicit authorized product workflow.

### 2.2 Atomic Fencing & Token Invalidation
- [ ] **Atomic Deletion Request**: In a single locked write transaction, setting `User.deletion_requested_at` aligns `User.tokens_valid_after` to the same timestamp.
- [ ] **Immediate Session Invalidation**: All active JWTs for the user are immediately rejected on protected routes (`/api/auth/me`, `/api/users/me/*`).
- [ ] **Login Rejection**: Subsequent login attempts return `401 Unauthorized` (`Incorrect email or password`) without revealing whether the account is tombstoned.
- [ ] **Recovery Token Scrubbing**: Active `email_verification_tokens` and `password_reset_tokens` for the user are deleted immediately in the request transaction; subsequent reset or verify requests fail closed.
- [ ] **BYOK Key Scrubbing**: User-stored encrypted API keys (`encrypted_openai_api_key`, `encrypted_gemini_api_key`, `encrypted_anthropic_api_key`) are cleared immediately.
- [ ] **Stale-Token Re-Registration Protection**: JWTs carry an immutable `uid` claim, and newly created accounts initialize `tokens_valid_after` at creation time, preventing token replay if the email is re-registered.

### 2.3 Resilient Purge Worker & External Resource Cleanup
- [ ] **Durable Tombstone**: If external cleanup fails or crashes, `User.deletion_requested_at` remains intact. The worker increments `deletion_attempt_count` and records `deletion_last_error_code`.
- [ ] **Active Reader Lease Protection**: If any document is actively held by an unexpired reader (`DocumentGenerationLock`), external deletion is deferred (`account_cleanup_deferred`) until the lease expires.
- [ ] **Storage Erasure**: Course documents and profile documents are deleted from storage (local filesystem directory or S3 bucket).
- [ ] **Vector Erasure**: Document chunks are removed from the vector store (Chroma collections or PostgreSQL pgvector) across both course and profile namespaces.
- [ ] **Relational Cascades**: Deleting the `User` row cascades courses, documents, chunks, pages, visuals, processing jobs, attempts, sessions, progress, and profile knowledge.
- [ ] **User-Authored Artifacts**: Generation jobs, generated outputs, and quizzes authored by the user are deleted.
- [ ] **Audit Anonymization**: In `credit_transactions`, administrator ledger records preserve financial integrity by setting `actor_user_id = NULL` and replacing `actor_label` with `"Deleted administrator"`.
- [ ] **Rate Limit Buckets**: Account-derived rate-limit rows (`generation:user:{id}`, `client_error:user:{id}`, `login:account:{hash}`) are purged.
- [ ] **SQLite Zeroing**: Self-hosted SQLite connections execute with `PRAGMA secure_delete=ON` to zero freed cells.

### 2.4 Bounded Retention & Privacy Notice Alignment
- [ ] **Operational Logs Notice**: Privacy-safe structured operational logs are retained for 30 days (AWS CloudWatch Logs or local SQLite WAL) and expire under bounded retention policies.
- [ ] **Backup Copies Notice**: Database backups (AWS RDS manual snapshots 30 days, self-hosted 7 daily / 4 weekly archives) expire under documented backup retention schedules rather than immediate removal.
- [ ] **Object Version Notice**: Versioned S3 objects transition after 30 days and permanently expire after 90 days.
- [ ] **UI Disclosure**: The Account > Security confirmation dialog and public Privacy Notice explicitly state these bounded retention periods.

---

## 3. Deployment Verification Commands

### 3.1 Self-Hosted Environment (Docker Compose)

1. **Verify Database Migration:**
   ```bash
   docker compose run --rm lumina python -m alembic current --check-heads
   ```
   Expected output: `a2e8c6f14b90 (head)`

2. **Query Pending Account Tombstones:**
   ```bash
   docker compose run --rm lumina python -c "
   import sqlite3
   conn = sqlite3.connect('/data/lumina.db')
   rows = conn.execute('SELECT id, email, deletion_requested_at, deletion_attempt_count, deletion_last_error_code FROM users WHERE deletion_requested_at IS NOT NULL').fetchall()
   print('Pending accounts:', rows)
   conn.close()
   "
   ```

3. **Dry-Run Account Purge:**
   ```bash
   docker compose run --rm lumina python -m workers.course_purge --dry-run
   ```

4. **Live Account Purge:**
   ```bash
   docker compose run --rm lumina python -m workers.course_purge
   ```

5. **Targeted Account Purge by User ID:**
   ```bash
   docker compose run --rm lumina python -m workers.course_purge --user-id <USER_ID>
   ```

### 3.2 Hosted Environment (AWS ECS & RDS)

1. **Verify RDS Pre-Deployment Snapshot:**
   Confirm manual RDS snapshot was created per the 30-day retention rule prior to running database migration `a2e8c6f14b90`.

2. **Run One-Shot Reconciler Task:**
   ```bash
   aws ecs run-task \
     --cluster <CLUSTER_NAME> \
     --task-definition <API_TASK_DEF> \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[<PRIVATE_SUBNETS>],securityGroups=[<APP_SG>],assignPublicIp=DISABLED}" \
     --overrides '{"containerOverrides": [{"name": "api", "command": ["python", "-m", "workers.course_purge"]}]}'
   ```

3. **CloudWatch Metrics & Logs Audit:**
   ```sql
   fields @timestamp, service, event, user_id, duration_ms, message
   | filter service = "maintenance" and event like /account_purge/
   | sort @timestamp desc
   | limit 50
   ```
   Confirm `AccountsExamined`, `AccountsPurged`, and `AccountsFailed` CloudWatch EMF metrics are emitted.
