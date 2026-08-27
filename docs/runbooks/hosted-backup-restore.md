# Hosted Backup and Restore Drill

## Purpose and scope

This runbook covers Lumina's hosted AWS recovery control: 30-day manual RDS
snapshots and a quarterly restore drill. The drill restores a snapshot to a new,
run-scoped RDS instance, upgrades that isolated database with the image currently
deployed by the API service, and verifies it without cutting production traffic
over.

The selected recoverable scope is deliberately narrower than complete
point-in-time reconstruction:

- PostgreSQL data and pgvector embeddings in the selected RDS snapshot.
- Current S3 objects only.
- Active database-to-object references only.
- Rows that are deleting or tombstoned are excluded from object verification.

The control does not provide production cutover, exact historical S3 version
recovery, cross-region or cross-account recovery, automatic database rollback,
or recovery of objects that were already absent when the drill ran. RDS restore
does not mutate the production database, RDS Proxy, ECS services, or S3.

## GitHub production environment

Restrict the GitHub `production` environment to `main`, require the configured
approvers, and keep `AWS_DEPLOY_ROLE_ARN` as its only workflow secret. Configure
`AWS_RECOVERY_ROLE_ARN` as a variable; the drill assumes that separate,
least-privilege role. Both
`.github/workflows/deploy.yml` and
`.github/workflows/hosted-restore-drill.yml` use the `deploy-production`
concurrency group, so deployment, rollback, and restore verification cannot run
at the same time.

Set these exact environment variables:

| GitHub variable | Terraform source | Format |
| --- | --- | --- |
| `AWS_REGION` | root `aws_region` output | Region name |
| `RDS_INSTANCE_IDENTIFIER` | root `rds_instance_identifier` output | RDS instance identifier |
| `RDS_SUBNET_GROUP` | root `rds_subnet_group_name` output | DB subnet group name |
| `RDS_SECURITY_GROUP` | root `rds_security_group_id` output | RDS security group ID |
| `RDS_PARAMETER_GROUP` | root `rds_parameter_group_name` output | Production DB parameter group name |
| `RDS_OPTION_GROUP` | root `rds_option_group_name` output | Production DB option group name |
| `ECS_CLUSTER` | root `ecs_cluster_name` output | ECS cluster name |
| `API_SERVICE` | root `api_service_name` output | Deployed API service name |
| `HOSTED_RESTORE_TASK_DEFINITION` | root `hosted_restore_task_definition_family` output | Read-only verifier task family |
| `PRIVATE_SUBNETS` | root `private_subnet_ids_csv` output | Comma-separated subnet IDs |
| `RESTORE_VERIFIER_SECURITY_GROUP` | root `restore_verifier_security_group_id` output | Isolated verifier security group ID |
| `AWS_RECOVERY_ROLE_ARN` | root `github_recovery_role_arn` output | Recovery role ARN |
| `AWS_RECONCILER_ROLE_ARN` | root `github_reconciler_role_arn` output | Unattended deletion-only role ARN |
| `AWS_DEPLOY_ROLE_ARN` | root `github_actions_role_arn` output | Environment secret |

Read these exact values with `terraform output`; do not infer a security group
or subnet group from a display name. Preserve the GitHub variable names when
rotating or reprovisioning infrastructure.

Create a second `production-recovery` environment with no required approver so
the scheduled reconciler runs unattended. Copy only `AWS_REGION`,
`RDS_INSTANCE_IDENTIFIER`, `ECS_CLUSTER`, `HOSTED_RESTORE_TASK_DEFINITION`, and
`AWS_RECONCILER_ROLE_ARN` into it. Its OIDC trust and shared concurrency lock keep
it main-only and serialize it with deployments and drills.

The protected recovery OIDC role can orchestrate only managed snapshots, run/stop the
dedicated verifier task, and create/delete tagged restore targets. The verifier
task has a separate execution role that reads only the migration URL secret and
a task role that can only read document objects. Separate task/database security
groups permit PostgreSQL only from the verifier. Recovery cannot modify the
production instance or proxy, update ECS services, or write/delete S3 objects.
The unattended reconciler cannot create or run resources, pass roles, read
secrets, or tag resources; it can only inspect and delete already-tagged recovery
resources.

## Predeployment snapshot

Every non-rollback production deployment creates and waits for a verified
manual snapshot before `Apply database migrations`:

```text
<source>-predeploy-<sha8>-<run-id>-<run-attempt>
```

The snapshot identifier records the run ID and attempt.
`ops/aws_rds_recovery.py` tags it with the full release SHA, workflow run ID,
purpose, and managed-resource marker. The required `--retention-days 30` policy
prunes expired managed snapshots of the same source and purpose. The protected
`Hosted snapshot retention` workflow runs daily and prunes expired managed
predeployment and drill snapshots even when no deployment or drill runs. Snapshot
creation or verification failure blocks migration. A release rollback skips
snapshot creation and never downgrades or automatically restores the database;
schema changes must remain backward compatible.

## Quarterly drill

The `Hosted restore drill` workflow runs at `06:00 UTC` on January 1, April 1,
July 1, and October 1. It can also be dispatched from `main`. Leave
`snapshot_id` empty to create and retain a new 30-day drill snapshot:

```text
<source>-drill-<sha8>-<run-id>-<run-attempt>
```

Enter an existing Lumina-managed `<source>-predeploy-*` or `<source>-drill-*`
manual snapshot identifier to test that snapshot instead. The workflow rejects
snapshots from another source, unencrypted snapshots, and snapshots without the
managed-purpose tags.
The workflow restores to this isolated target:

```text
<source>-restore-<run-id>-<run-attempt>
```

It reads the image from the API service's deployed task revision, selects the
matching active revision of `HOSTED_RESTORE_TASK_DEFINITION`, and runs container
`hosted-restore` with:

```bash
python -m workers.hosted_restore \
  --target-host ENDPOINT \
  --upgrade-schema \
  --verify \
  --output json
```

The verifier must exit `0`. Its selected object check reads current S3 objects
for active database references and deliberately excludes deleting/tombstoned
rows. It never restores an S3 version and never writes or deletes an object.
An `always()` cleanup verifies the generated target identifier and safety tags,
refreshes AWS credentials, and calls `delete-restore`; it runs even when
migration or verification fails. Deletion waits through transient RDS states,
retries `InvalidDBInstanceState`, and distinguishes a confirmed absent target
from an authorization or API failure. An unconfirmed cleanup is a failed control
requiring operator escalation. The retained manual snapshot is not deleted by
drill cleanup and remains subject to the 30-day snapshot retention policy.

Because GitHub does not guarantee that a cancelled runner executes finalizers,
the protected daily retention workflow is the independent reconciler. Its
`prune-verifiers` and `prune-restores` operations stop/delete only tagged task
and `<source>-restore-*` targets older than the six-hour drill window. A run
stops at most one stale verifier and deletes at most one stale database target
so its deadline remains bounded; additional resources are reported as deferred
and reconciled by later daily runs.

## Operator evidence

The repository tests validate orchestration, IAM shape, Terraform wiring, and
the PostgreSQL verifier contract. Before treating this control as qualified,
run it once in the protected AWS environment and retain evidence that tag-on-
create authorization, RDS group compatibility, isolated network connectivity,
task-role restrictions, and the production data-volume deadline all behaved as
specified.

For each deployment retain the GitHub run URL, full release SHA, run ID and
attempt, predeployment snapshot identifier, snapshot `available` verification,
and migration outcome.

For each scheduled or manual drill retain:

- GitHub run URL, trigger, approver, full SHA, run ID, and run attempt.
- Source snapshot identifier, purpose/retention tags, creation time, and status.
- Isolated restore identifier and endpoint, restore start/available times, and
  elapsed restore time.
- ECS verifier task ARN and task-definition ARN, container exit code, and the
  verifier's JSON summary from CloudWatch Logs.
- The verifier JSON fields `status`, `documents_checked`,
  `ready_documents_checked`, `chunks_checked`, `embeddings_checked`,
  `failure_count`, and `failures`. In particular, retain the `schema_heads` and
  `object_unavailable` failure counts.
- Cleanup command JSON, target deletion status, and confirmation that the
  production DB identifier, proxy target, ECS service task definitions, and S3
  object/version counts did not change.

Link the evidence from the quarterly recovery-control record. Do not include
database credentials, signed S3 URLs, object contents, or secret values.

## Failure and escalation

Treat snapshot creation failure, restore failure, nonzero verifier exit, missing
active S3 object, or incomplete restore cleanup as a failed control. Do not run
migrations after a failed predeployment snapshot, do not cut production over to
a drill target, and do not attempt an automatic database rollback.

1. Preserve the workflow run, CLI JSON, ECS task ARN, and relevant privacy-safe
   CloudWatch events.
2. Confirm the production instance, RDS Proxy, ECS services, and S3 were not
   changed.
3. If cleanup failed, page the platform on-call and delete only the exact
   `<source>-restore-<run-id>-<attempt>` target after a second operator verifies
   both identifiers. Never delete the source or the retained snapshot while
   investigating.
4. Escalate RDS snapshot/restore or IAM failures to the platform owner; escalate
   schema upgrade failures to the database migration owner; escalate missing
   active objects to the storage/data-integrity owner.
5. Block the next production migration until a predeployment snapshot failure is
   resolved. Record remediation and rerun the drill; a verifier failure is not
   closed by cleanup alone.
