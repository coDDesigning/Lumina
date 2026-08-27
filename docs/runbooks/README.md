# Lumina Operational Runbooks

This directory contains executable, production-tested operational recovery runbooks for Lumina.

## Available Runbooks

| Runbook | Scenario | Primary Tools & Commands |
| :--- | :--- | :--- |
| [Stuck Document Processing](stuck_document.md) | Processing jobs stuck in running or failed status, expired worker leases, OCR/extraction timeouts. | `workers.document_processor`, `workers.embedding_backfill` |
| [Stranded Tombstone Course Purge](stranded_tombstone.md) | Deleted courses (`is_deleted = True`) whose physical storage or vectors failed to delete cleanly. | `python -m workers.course_purge` |
| [AI Provider Outage](provider_outage.md) | Gemini API or local Ollama rate limits, 503/504 errors, network failover, and model recovery. | `/health/ready`, `ai_usage_logs`, ECS/SSM |
| [Self-Hosted Backup & Restore](../self-hosted-backup.md) | Single-host Compose backup and restore operations for SQLite, Chroma, and uploads. | `workers.self_hosted_backup` |

For structured logging, trace querying, and CloudWatch metrics contracts, see [Observability Documentation](../observability.md).
