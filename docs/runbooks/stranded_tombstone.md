# Operational Runbook: Stranded Tombstone Purge

## Overview

Course deletion in Lumina is permanent, owner-only, and executed via `CourseService.hard_delete_course`. When a deletion begins, the course is marked with `Course.is_deleted = True` (tombstoned) to fence active traffic, followed by:
1. Deleting uploaded physical objects from storage (S3 bucket or local directory).
2. Deleting vector embeddings from vector store (pgvector or Chroma).
3. Deleting relational records via SQLAlchemy cascade.

If a network timeout or crash interrupts steps 1 or 2, the course row remains tombstoned (`is_deleted = True`). The background utility `workers.course_purge` reconciles and finishes unfinished deletions idempotently.

Document deletion is the same two-phase erase one level down. `DocumentService.delete_document` and `ProfileDocumentService.delete_document` commit `status = 'deleting'` on the document row, then remove the storage object, then the vectors, then the row. A failure after the tombstone is committed leaves a row that is hidden from every read endpoint, spends no course quota, cannot be resurrected by any job transition, and can only be finished by this same command, which makes a second pass over document tombstones after the course pass.

In production, the running worker automatically executes periodic purge reconciliation scans every `COURSE_PURGE_INTERVAL_SECONDS` (default: 3600 seconds / 1 hour). Stranded tombstones will clear on the next cycle without manual intervention. Standalone daemon execution is also supported via `python -m workers.course_purge --interval-seconds <SECONDS>`.

---

## 1. Diagnosis

### Symptoms
* Admin or operator notices `courses` rows with `is_deleted = True` persisting in the database.
* S3 storage usage or Chroma persistent directory does not decrease after a deletion request.
* `GET /api/courses/{id}` returns 404 to users, but orphaned rows or storage keys linger.

### Diagnostic Queries

#### SQL Check (PostgreSQL or SQLite):
```sql
SELECT id, title, owner_id, is_deleted, created_at, updated_at
FROM courses
WHERE is_deleted = TRUE
ORDER BY updated_at ASC;

SELECT id, course_id, status, created_at, updated_at
FROM uploaded_documents
WHERE status = 'deleting'
ORDER BY updated_at ASC;

SELECT id, user_id, status, created_at, updated_at
FROM profile_documents
WHERE status = 'deleting'
ORDER BY updated_at ASC;
```

#### CloudWatch Logs Insights:
```sql
fields @timestamp, service, event, message
| filter service = "maintenance" or event like /course_purge/
| sort @timestamp desc
```

---

## 2. Safe Remediation

### Self-Hosted Environment

1. **Perform a dry-run first:**
   ```bash
   docker compose run --rm lumina \
     python -m workers.course_purge --dry-run
   ```
   Inspect the summary output: `examined=X purged=0 failed=0`.

2. **Execute the live purge across all tombstoned courses:**
   ```bash
   docker compose run --rm lumina \
     python -m workers.course_purge
   ```

3. **Purge a specific course ID:**
   ```bash
   docker compose run --rm lumina \
     python -m workers.course_purge --course-id <COURSE_ID>
   ```

4. **Purge a specific document tombstone:**
   ```bash
   docker compose run --rm lumina \
     python -m workers.course_purge --document-id <DOCUMENT_ID>
   ```
   A tombstone younger than `--document-grace-seconds` (default 300) is reported
   as examined but left alone, because a delete request that is still running
   holds the same tombstone. Pass `--document-grace-seconds 0` only when no
   delete request for that document is in flight.

### Hosted Environment (AWS ECS)

1. **Execute Dry-Run One-Shot Task:**
   ```bash
   aws ecs run-task \
     --cluster <CLUSTER_NAME> \
     --task-definition <API_TASK_DEF> \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[<PRIVATE_SUBNETS>],securityGroups=[<APP_SG>],assignPublicIp=DISABLED}" \
     --overrides '{"containerOverrides": [{"name": "api", "command": ["python", "-m", "workers.course_purge", "--dry-run"]}]}'
   ```

2. **Execute Live Purge Task:**
   ```bash
   aws ecs run-task \
     --cluster <CLUSTER_NAME> \
     --task-definition <API_TASK_DEF> \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[<PRIVATE_SUBNETS>],securityGroups=[<APP_SG>],assignPublicIp=DISABLED}" \
     --overrides '{"containerOverrides": [{"name": "api", "command": ["python", "-m", "workers.course_purge"]}]}'
   ```

---

## 3. Validation

1. **Verify Database State:**
   ```sql
   SELECT COUNT(*) FROM courses WHERE is_deleted = TRUE;
   SELECT COUNT(*) FROM uploaded_documents WHERE status = 'deleting';
   SELECT COUNT(*) FROM profile_documents WHERE status = 'deleting';
   ```
   Expected: `0` for each.

2. **Verify Storage Cleanup:**
   * Local: Check that `/data/uploads/courses/<COURSE_ID>` directory has been pruned.
   * Hosted S3:
     ```bash
     aws s3 ls s3://<S3_BUCKET>/<STORAGE_NAMESPACE>/courses/<COURSE_ID>/
     ```
     Expected: `None` (empty / prefix deleted).

3. **Verify Vector Store:**
   * PostgreSQL pgvector:
     ```sql
     SELECT COUNT(*) FROM chunk_embeddings WHERE course_id = <COURSE_ID>;
     ```
     Expected: `0`.

---

## 4. Escalation Criteria

* **S3 Access Denied (`403 Forbidden`):** If `storage/s3.py` fails with S3 AccessDenied during batch deletion, verify the ECS Task Execution Role IAM policies for `s3:DeleteObject` and `s3:ListBucket`.
* **Foreign Key Deadlock / Integrity Errors:** If `CourseService.hard_delete_course` fails repeatedly on database deletion, check for uncascaded foreign key references outside the defined 21-table schema.
