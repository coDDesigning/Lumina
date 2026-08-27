from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "deploy.yml"
DRILL_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "hosted-restore-drill.yml"
RETENTION_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "hosted-snapshot-retention.yml"
)


def _load_workflow(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _job(workflow: dict) -> dict:
    jobs = workflow["jobs"]
    assert len(jobs) == 1
    return next(iter(jobs.values()))


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_predeployment_snapshot_precedes_migration_and_rollback_skips_it():
    job = _job(_load_workflow(DEPLOY_WORKFLOW))
    step_names = [step["name"] for step in job["steps"]]
    snapshot = _step(job, "Create and verify predeployment snapshot")

    assert step_names.index(snapshot["name"]) < step_names.index(
        "Apply database migrations"
    )
    assert snapshot["if"] == "steps.release.outputs.is_rollback == 'false'"
    credential_refresh = _step(
        job, "Refresh AWS credentials for predeployment snapshot"
    )
    assert credential_refresh["if"] == snapshot["if"]
    assert step_names.index(credential_refresh["name"]) < step_names.index(
        snapshot["name"]
    )
    assert "--purpose predeployment" in snapshot["run"]
    assert "--retention-days 30" in snapshot["run"]
    assert (
        job["env"]["RDS_INSTANCE_IDENTIFIER"] == "${{ vars.RDS_INSTANCE_IDENTIFIER }}"
    )
    registration = _step(job, "Register task definitions")["run"]
    assert "$HOSTED_RESTORE_TASK_DEFINITION" in registration
    assert "hosted_restore: $hosted_restore[0]" in registration


def test_restore_drill_shares_deploy_lock_and_protected_environment():
    deploy = _load_workflow(DEPLOY_WORKFLOW)
    drill = _load_workflow(DRILL_WORKFLOW)

    assert drill["concurrency"] == deploy["concurrency"]
    assert _job(drill)["environment"] == _job(deploy)["environment"] == "production"
    assert drill["permissions"]["id-token"] == "write"
    assert (
        "refs/heads/main" in _step(_job(drill), "Validate drill configuration")["run"]
    )


def test_restore_drill_runs_quarterly_and_accepts_an_existing_snapshot():
    workflow = _load_workflow(DRILL_WORKFLOW)

    assert workflow["on"]["schedule"] == [{"cron": "0 6 1 1,4,7,10 *"}]
    snapshot_input = workflow["on"]["workflow_dispatch"]["inputs"]["snapshot_id"]
    assert snapshot_input["required"] == "false"


def test_snapshot_retention_runs_daily_under_the_production_lock():
    deploy = _load_workflow(DEPLOY_WORKFLOW)
    retention = _load_workflow(RETENTION_WORKFLOW)
    job = _job(retention)

    assert retention["on"]["schedule"] == [{"cron": "0 5 * * *"}]
    assert retention["concurrency"] == deploy["concurrency"]
    assert job["environment"] == "production-recovery"
    prune_step = _step(job, "Prune expired managed snapshots")
    prune = prune_step["run"]
    assert "always()" in prune_step["if"]
    assert "prune-snapshots" in prune
    assert "--retention-days 30" in prune
    stale_restores = _step(job, "Delete stale isolated restore targets")["run"]
    assert "prune-restores" in stale_restores
    assert "--max-age-hours 6" in stale_restores
    stale_verifiers = _step(job, "Stop stale isolated verifier tasks")["run"]
    assert "prune-verifiers" in stale_verifiers
    assert "--max-age-hours 6" in stale_verifiers
    assert "vars.AWS_RECONCILER_ROLE_ARN" in RETENTION_WORKFLOW.read_text(
        encoding="utf-8"
    )
    assert "AWS_DEPLOY_ROLE_ARN" not in RETENTION_WORKFLOW.read_text(encoding="utf-8")


def test_restore_drill_uses_an_isolated_target_and_verifier_command():
    job = _job(_load_workflow(DRILL_WORKFLOW))
    targets = _step(job, "Resolve isolated restore targets")["run"]
    restore = _step(job, "Restore isolated database target")["run"]
    start = _step(job, "Start restore verifier")["run"]
    verify = _step(job, "Wait for restore verifier")["run"]

    assert (
        'restore_target_id="${RDS_INSTANCE_IDENTIFIER}-restore-'
        '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"'
    ) in targets
    assert '"${RDS_INSTANCE_IDENTIFIER}-predeploy-"*' in targets
    assert '"${RDS_INSTANCE_IDENTIFIER}-drill-"*' in targets
    assert "must identify a managed snapshot" in targets
    assert '--target "$RESTORE_TARGET_ID"' in restore
    assert '--db-subnet-group "$RDS_SUBNET_GROUP"' in restore
    assert '--security-group "$RDS_SECURITY_GROUP"' in restore
    assert '--parameter-group "$RDS_PARAMETER_GROUP"' in restore
    assert '--option-group "$RDS_OPTION_GROUP"' in restore
    for argument in (
        '"workers.hosted_restore"',
        '"--target-host"',
        '"--upgrade-schema"',
        '"--verify"',
        '"--output"',
        '"json"',
    ):
        assert argument in start
    assert 'name: "hosted-restore"' in start
    assert "describe-services" in start
    assert "list-task-definitions" in start
    assert "register-task-definition" not in start
    assert "securityGroups=[${RESTORE_VERIFIER_SECURITY_GROUP}]" in start
    assert 'if [ "$exit_code" != "0" ]' in verify
    assert "deadline=$((SECONDS + 3000))" in verify
    assert "timeout 60 aws ecs describe-tasks" in verify
    assert "did not stop within 50 minutes" in verify
    assert "aws ecs wait tasks-stopped" not in verify
    assert "aws ecs stop-task" in verify


def test_restore_cleanup_is_always_guarded_and_source_safe():
    workflow = _load_workflow(DRILL_WORKFLOW)
    job = _job(workflow)
    credential_refresh = _step(job, "Refresh AWS credentials for cleanup")
    cleanup = _step(job, "Delete isolated restore target")
    stop_task = _step(job, "Stop isolated verifier task")
    cleanup_condition = cleanup["if"]

    assert "always()" in credential_refresh["if"]
    assert "always()" in cleanup_condition
    assert "always()" in stop_task["if"]
    assert "aws ecs stop-task" in stop_task["run"]
    assert "steps.cleanup_aws.outcome == 'success'" in cleanup_condition
    assert "restore_target_id != ''" in cleanup_condition
    assert 'RESTORE_TARGET_ID" = "$RDS_INSTANCE_IDENTIFIER' in cleanup["run"]
    assert "delete-restore" in cleanup["run"]
    assert "DBInstanceNotFound" in cleanup["run"]
    assert "Unable to determine whether the restore target exists" in cleanup["run"]

    workflow_text = DRILL_WORKFLOW.read_text(encoding="utf-8")
    assert "aws ecs update-service" not in workflow_text
    assert "aws rds modify-db" not in workflow_text
    assert "aws s3" not in workflow_text
    assert "AWS_DEPLOY_ROLE_ARN" not in workflow_text
    assert "vars.AWS_RECOVERY_ROLE_ARN" in workflow_text
