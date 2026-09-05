# Lumina Operational Runbooks

This directory contains executable operational recovery runbooks for Lumina.
Each runbook states any environment-specific qualification still required; do
not infer live production qualification from repository tests alone.

## Available Runbooks

| Runbook | Scenario | Primary Tools & Commands |
| :--- | :--- | :--- |
| [Stuck Document Processing](stuck_document.md) | Processing jobs stuck in running or failed status, expired worker leases, OCR/extraction timeouts. | `workers.document_processor`, `workers.embedding_backfill` |
| [Stranded Tombstone Purge](stranded_tombstone.md) | Deleted courses (`is_deleted = True`) and deleted documents (`status = 'deleting'`) whose physical storage or vectors failed to delete cleanly. | `python -m workers.course_purge` |
| [AI Provider Outage](provider_outage.md) | Gemini API or local Ollama rate limits, 503/504 errors, network failover, and model recovery. | `/health/ready`, `ai_usage_logs`, ECS/SSM |
| [Self-Hosted Backup & Restore](../self-hosted-backup.md) | Single-host Compose backup and restore operations for SQLite, Chroma, and uploads. | `workers.self_hosted_backup` |
| [Hosted Backup & Restore Drill](hosted-backup-restore.md) | AWS RDS predeployment snapshots and isolated quarterly restore verification. | `ops/aws_rds_recovery.py`, `workers.hosted_restore` |
| [Pre-Demo Verification Checklist](demo_checklist.md) | Timed pre-demo preparation (T-24h), execution verification (T-30m), and in-demo fallback procedures. | `/health/ready`, `scripts.seed_demo`, `workers.document_processor`, `ops/self_hosted_backup.sh` |

For structured logging, trace querying, and CloudWatch metrics contracts, see [Observability Documentation](../observability.md).
