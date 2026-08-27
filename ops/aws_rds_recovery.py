"""Safe AWS CLI orchestration for hosted RDS recovery operations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

MANAGED_BY = "LuminaHostedRecovery"
RESTORE_PURPOSE = "restore-drill"
RETENTION_DAYS = 30
RESTORE_MAX_AGE_HOURS = 6

Runner = Callable[..., subprocess.CompletedProcess[str]]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]
POLL_SECONDS = 30.0
SNAPSHOT_WAIT_ATTEMPTS = 120
INSTANCE_WAIT_ATTEMPTS = 120
DELETE_STATE_WAIT_ATTEMPTS = 20
DELETE_REQUEST_ATTEMPTS = 20
DELETE_CONFIRM_ATTEMPTS = 80


class ValidationError(Exception):
    """The requested operation is invalid or fails a safety check."""


class AwsCommandError(Exception):
    """An AWS command failed or returned an unusable response."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValidationError from None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aws_command(*arguments: str) -> list[str]:
    return ["aws", *arguments, "--output", "json", "--no-cli-pager"]


def _run(runner: Runner, arguments: Sequence[str], *, expect_json: bool = True) -> Any:
    try:
        completed = runner(
            list(arguments),
            check=True,
            capture_output=True,
            text=True,
            shell=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AwsCommandError from exc

    if completed.returncode != 0:
        raise AwsCommandError
    if not expect_json:
        return None
    try:
        value = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AwsCommandError from exc
    if not isinstance(value, dict):
        raise AwsCommandError
    return value


def _aws_error_is(exc: AwsCommandError, code: str) -> bool:
    cause = exc.__cause__
    if not isinstance(cause, subprocess.CalledProcessError):
        return False
    output = f"{cause.stdout or ''}\n{cause.stderr or ''}"
    return code in output


def _validate_rds_identifier(value: str, *, maximum: int = 63) -> str:
    if not 1 <= len(value) <= maximum:
        raise ValidationError
    if not re.fullmatch(r"[a-z][a-z0-9-]*", value):
        raise ValidationError
    if value.endswith("-") or "--" in value:
        raise ValidationError
    return value


def _validate_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValidationError
    return value


def _validate_run_id(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise ValidationError
    return value


def _validate_group_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9._:-]{0,254}", value):
        raise ValidationError
    return value


def _validate_security_group(value: str) -> str:
    if not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", value):
        raise ValidationError
    return value


def _validate_restore_target(source: str, target: str) -> None:
    if target == source or not target.startswith(f"{source}-restore-"):
        raise ValidationError


def _snapshot_prefix(source: str, purpose: str) -> str:
    suffix = "predeploy" if purpose == "predeployment" else "drill"
    return f"{source}-{suffix}-"


def _validate_managed_snapshot_identifier(
    source: str,
    snapshot_identifier: str,
    purpose: str | None = None,
) -> str:
    purposes = (purpose,) if purpose is not None else ("predeployment", RESTORE_PURPOSE)
    for candidate in purposes:
        if snapshot_identifier.startswith(_snapshot_prefix(source, candidate)):
            return candidate
    raise ValidationError


def _required_list(response: dict[str, Any], key: str) -> list[Any]:
    value = response.get(key)
    if not isinstance(value, list):
        raise AwsCommandError
    return value


def _single_dict(response: dict[str, Any], key: str) -> dict[str, Any]:
    values = _required_list(response, key)
    if len(values) != 1 or not isinstance(values[0], dict):
        raise AwsCommandError
    return values[0]


def _snapshot(
    runner: Runner, snapshot_identifier: str, source_identifier: str
) -> dict[str, Any]:
    response = _run(
        runner,
        _aws_command(
            "rds",
            "describe-db-snapshots",
            "--db-snapshot-identifier",
            snapshot_identifier,
            "--snapshot-type",
            "manual",
        ),
    )
    snapshot = _single_dict(response, "DBSnapshots")
    if (
        snapshot.get("DBSnapshotIdentifier") != snapshot_identifier
        or snapshot.get("DBInstanceIdentifier") != source_identifier
        or snapshot.get("Status") != "available"
        or snapshot.get("Encrypted") is not True
        or snapshot.get("SnapshotType") != "manual"
    ):
        raise ValidationError
    return snapshot


def _wait_for_snapshot(
    runner: Runner,
    snapshot_identifier: str,
    source_identifier: str,
    *,
    sleeper: Sleeper,
) -> dict[str, Any]:
    for attempt in range(SNAPSHOT_WAIT_ATTEMPTS):
        try:
            response = _run(
                runner,
                _aws_command(
                    "rds",
                    "describe-db-snapshots",
                    "--db-snapshot-identifier",
                    snapshot_identifier,
                    "--snapshot-type",
                    "manual",
                ),
            )
        except AwsCommandError as exc:
            if _aws_error_is(exc, "DBSnapshotNotFound"):
                if attempt + 1 < SNAPSHOT_WAIT_ATTEMPTS:
                    sleeper(POLL_SECONDS)
                    continue
            raise
        snapshot = _single_dict(response, "DBSnapshots")
        if (
            snapshot.get("DBSnapshotIdentifier") != snapshot_identifier
            or snapshot.get("DBInstanceIdentifier") != source_identifier
            or snapshot.get("Encrypted") is not True
            or snapshot.get("SnapshotType") != "manual"
        ):
            raise ValidationError
        status = snapshot.get("Status")
        if status == "available":
            return snapshot
        if status in {"failed", "deleted"} or not isinstance(status, str):
            raise ValidationError
        if attempt + 1 < SNAPSHOT_WAIT_ATTEMPTS:
            sleeper(POLL_SECONDS)
    raise AwsCommandError


def _describe_instance(runner: Runner, identifier: str) -> dict[str, Any]:
    response = _run(
        runner,
        _aws_command(
            "rds", "describe-db-instances", "--db-instance-identifier", identifier
        ),
    )
    instance = _single_dict(response, "DBInstances")
    if instance.get("DBInstanceIdentifier") != identifier:
        raise ValidationError
    return instance


def _describe_instance_optional(
    runner: Runner, identifier: str
) -> dict[str, Any] | None:
    try:
        return _describe_instance(runner, identifier)
    except AwsCommandError as exc:
        if _aws_error_is(exc, "DBInstanceNotFound"):
            return None
        raise


def _wait_for_instance(
    runner: Runner,
    identifier: str,
    *,
    accepted_statuses: set[str],
    sleeper: Sleeper,
    attempts: int = INSTANCE_WAIT_ATTEMPTS,
    missing_is_pending: bool = False,
) -> dict[str, Any]:
    terminal_failures = {
        "inaccessible-encryption-credentials",
        "inaccessible-encryption-credentials-recoverable",
        "incompatible-network",
        "incompatible-option-group",
        "incompatible-parameters",
        "restore-error",
    }
    for attempt in range(attempts):
        try:
            instance = _describe_instance(runner, identifier)
        except AwsCommandError as exc:
            if missing_is_pending and _aws_error_is(exc, "DBInstanceNotFound"):
                if attempt + 1 < attempts:
                    sleeper(POLL_SECONDS)
                    continue
            raise
        status = instance.get("DBInstanceStatus")
        if status in accepted_statuses:
            return instance
        if status in terminal_failures or not isinstance(status, str):
            raise ValidationError
        if attempt + 1 < attempts:
            sleeper(POLL_SECONDS)
    raise AwsCommandError


def _wait_for_instance_deleted(
    runner: Runner,
    identifier: str,
    *,
    sleeper: Sleeper,
) -> None:
    for attempt in range(DELETE_CONFIRM_ATTEMPTS):
        if _describe_instance_optional(runner, identifier) is None:
            return
        if attempt + 1 < DELETE_CONFIRM_ATTEMPTS:
            sleeper(POLL_SECONDS)
    raise AwsCommandError


def _resource_tags(runner: Runner, resource_arn: Any) -> dict[str, str]:
    if not isinstance(resource_arn, str) or not resource_arn:
        raise AwsCommandError
    response = _run(
        runner,
        _aws_command("rds", "list-tags-for-resource", "--resource-name", resource_arn),
    )
    tags: dict[str, str] = {}
    for tag in _required_list(response, "TagList"):
        if not isinstance(tag, dict):
            raise AwsCommandError
        key = tag.get("Key")
        value = tag.get("Value")
        if not isinstance(key, str) or not isinstance(value, str) or key in tags:
            raise AwsCommandError
        tags[key] = value
    return tags


def _require_managed_tags(
    runner: Runner,
    resource: dict[str, Any],
    *,
    arn_key: str,
    purpose: str,
) -> None:
    tags = _resource_tags(runner, resource.get(arn_key))
    if tags.get("ManagedBy") != MANAGED_BY or tags.get("Purpose") != purpose:
        raise ValidationError


def _parse_aws_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def prune_snapshots(
    *,
    source: str,
    retention_days: int,
    purpose: str | None = None,
    runner: Runner = subprocess.run,
    now: Clock = _utc_now,
) -> dict[str, Any]:
    _validate_rds_identifier(source)
    if retention_days != RETENTION_DAYS or purpose not in {
        None,
        "predeployment",
        RESTORE_PURPOSE,
    }:
        raise ValidationError

    response = _run(
        runner,
        _aws_command(
            "rds",
            "describe-db-snapshots",
            "--db-instance-identifier",
            source,
            "--snapshot-type",
            "manual",
        ),
    )
    snapshots = _required_list(response, "DBSnapshots")
    current_time = now()
    if current_time.tzinfo is None:
        raise AwsCommandError
    cutoff = current_time.astimezone(timezone.utc) - timedelta(days=retention_days)
    deleted = 0

    for candidate in snapshots:
        if not isinstance(candidate, dict):
            raise AwsCommandError
        created_at = _parse_aws_time(candidate.get("SnapshotCreateTime"))
        if (
            candidate.get("DBInstanceIdentifier") != source
            or candidate.get("SnapshotType") != "manual"
            or candidate.get("Status") != "available"
            or created_at is None
            or created_at >= cutoff
        ):
            continue
        candidate_identifier = candidate.get("DBSnapshotIdentifier")
        if not isinstance(candidate_identifier, str):
            continue
        try:
            _validate_rds_identifier(candidate_identifier, maximum=255)
            candidate_purpose = _validate_managed_snapshot_identifier(
                source,
                candidate_identifier,
                purpose,
            )
        except ValidationError:
            continue
        tags = _resource_tags(runner, candidate.get("DBSnapshotArn"))
        if (
            tags.get("ManagedBy") != MANAGED_BY
            or tags.get("Purpose") != candidate_purpose
        ):
            continue
        _run(
            runner,
            _aws_command(
                "rds",
                "delete-db-snapshot",
                "--db-snapshot-identifier",
                candidate_identifier,
            ),
        )
        deleted += 1

    return {
        "snapshots_deleted": deleted,
        "snapshots_examined": len(snapshots),
    }


def create_snapshot(
    *,
    source: str,
    snapshot_identifier: str,
    release: str,
    run_id: str,
    run_attempt: int,
    purpose: str,
    retention_days: int,
    runner: Runner = subprocess.run,
    now: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    _validate_rds_identifier(source)
    _validate_rds_identifier(snapshot_identifier, maximum=255)
    _validate_sha(release)
    _validate_run_id(run_id)
    if (
        type(run_attempt) is not int
        or run_attempt < 1
        or purpose not in {"predeployment", RESTORE_PURPOSE}
    ):
        raise ValidationError
    if retention_days != RETENTION_DAYS:
        raise ValidationError
    _validate_managed_snapshot_identifier(source, snapshot_identifier, purpose)

    _run(
        runner,
        _aws_command(
            "rds",
            "create-db-snapshot",
            "--db-instance-identifier",
            source,
            "--db-snapshot-identifier",
            snapshot_identifier,
            "--tags",
            f"Key=ManagedBy,Value={MANAGED_BY}",
            f"Key=Purpose,Value={purpose}",
            f"Key=Release,Value={release}",
            f"Key=WorkflowRun,Value={run_id}",
        ),
    )
    created_snapshot = _wait_for_snapshot(
        runner,
        snapshot_identifier,
        source,
        sleeper=sleeper,
    )
    _require_managed_tags(
        runner,
        created_snapshot,
        arn_key="DBSnapshotArn",
        purpose=purpose,
    )

    retention = prune_snapshots(
        source=source,
        retention_days=retention_days,
        purpose=purpose,
        runner=runner,
        now=now,
    )
    return {"snapshot_identifier": snapshot_identifier, **retention}


def restore(
    *,
    source: str,
    snapshot_identifier: str,
    target: str,
    db_subnet_group: str,
    security_group: str,
    parameter_group: str,
    option_group: str,
    runner: Runner = subprocess.run,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    _validate_rds_identifier(source)
    _validate_rds_identifier(snapshot_identifier, maximum=255)
    _validate_rds_identifier(target)
    _validate_restore_target(source, target)
    _validate_group_name(db_subnet_group)
    _validate_security_group(security_group)
    _validate_group_name(parameter_group)
    _validate_group_name(option_group)
    purpose = _validate_managed_snapshot_identifier(source, snapshot_identifier)
    selected_snapshot = _snapshot(runner, snapshot_identifier, source)
    _require_managed_tags(
        runner,
        selected_snapshot,
        arn_key="DBSnapshotArn",
        purpose=purpose,
    )

    _run(
        runner,
        _aws_command(
            "rds",
            "restore-db-instance-from-db-snapshot",
            "--db-instance-identifier",
            target,
            "--db-snapshot-identifier",
            snapshot_identifier,
            "--db-subnet-group-name",
            db_subnet_group,
            "--vpc-security-group-ids",
            security_group,
            "--db-parameter-group-name",
            parameter_group,
            "--option-group-name",
            option_group,
            "--no-publicly-accessible",
            "--no-deletion-protection",
            "--tags",
            f"Key=ManagedBy,Value={MANAGED_BY}",
            f"Key=Purpose,Value={RESTORE_PURPOSE}",
        ),
    )
    instance = _wait_for_instance(
        runner,
        target,
        accepted_statuses={"available"},
        sleeper=sleeper,
        missing_is_pending=True,
    )
    endpoint = instance.get("Endpoint")
    subnet = instance.get("DBSubnetGroup")
    instance_security_groups = instance.get("VpcSecurityGroups")
    parameter_groups = instance.get("DBParameterGroups")
    option_groups = instance.get("OptionGroupMemberships")
    if (
        instance.get("DBInstanceIdentifier") != target
        or instance.get("DBInstanceStatus") != "available"
        or instance.get("Engine") != "postgres"
        or instance.get("StorageEncrypted") is not True
        or instance.get("PubliclyAccessible") is not False
        or instance.get("DeletionProtection") is not False
        or not isinstance(subnet, dict)
        or subnet.get("DBSubnetGroupName") != db_subnet_group
        or not isinstance(instance_security_groups, list)
        or {
            group.get("VpcSecurityGroupId")
            for group in instance_security_groups
            if isinstance(group, dict)
        }
        != {security_group}
        or not isinstance(parameter_groups, list)
        or {
            group.get("DBParameterGroupName")
            for group in parameter_groups
            if isinstance(group, dict)
        }
        != {parameter_group}
        or not isinstance(option_groups, list)
        or {
            group.get("OptionGroupName")
            for group in option_groups
            if isinstance(group, dict)
        }
        != {option_group}
        or not isinstance(endpoint, dict)
        or not isinstance(endpoint.get("Address"), str)
        or not endpoint["Address"]
    ):
        raise ValidationError
    _require_managed_tags(
        runner,
        instance,
        arn_key="DBInstanceArn",
        purpose=RESTORE_PURPOSE,
    )
    return {"endpoint": endpoint["Address"], "target_identifier": target}


def delete_restore(
    *,
    source: str,
    target: str,
    runner: Runner = subprocess.run,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    _validate_rds_identifier(source)
    _validate_rds_identifier(target)
    _validate_restore_target(source, target)

    try:
        instance = _wait_for_instance(
            runner,
            target,
            accepted_statuses={
                "available",
                "failed",
                "inaccessible-encryption-credentials",
                "inaccessible-encryption-credentials-recoverable",
                "incompatible-create",
                "incompatible-network",
                "incompatible-option-group",
                "incompatible-parameters",
                "incompatible-restore",
                "insufficient-capacity",
                "restore-error",
                "storage-full",
                "upgrade-failed",
            },
            sleeper=sleeper,
            attempts=DELETE_STATE_WAIT_ATTEMPTS,
        )
    except AwsCommandError as exc:
        if _aws_error_is(exc, "DBInstanceNotFound"):
            return {"instances_deleted": 0, "target_identifier": target}
        raise
    tags = _resource_tags(runner, instance.get("DBInstanceArn"))
    if tags.get("ManagedBy") != MANAGED_BY or tags.get("Purpose") != RESTORE_PURPOSE:
        raise ValidationError

    for attempt in range(DELETE_REQUEST_ATTEMPTS):
        try:
            _run(
                runner,
                _aws_command(
                    "rds",
                    "delete-db-instance",
                    "--db-instance-identifier",
                    target,
                    "--skip-final-snapshot",
                ),
            )
            break
        except AwsCommandError as exc:
            if _aws_error_is(exc, "DBInstanceNotFound"):
                return {"instances_deleted": 0, "target_identifier": target}
            if not _aws_error_is(exc, "InvalidDBInstanceState"):
                raise
            if attempt + 1 == DELETE_REQUEST_ATTEMPTS:
                raise
            sleeper(POLL_SECONDS)
    _wait_for_instance_deleted(runner, target, sleeper=sleeper)
    return {"instances_deleted": 1, "target_identifier": target}


def prune_restores(
    *,
    source: str,
    max_age_hours: int,
    runner: Runner = subprocess.run,
    now: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    _validate_rds_identifier(source)
    if max_age_hours != RESTORE_MAX_AGE_HOURS:
        raise ValidationError
    response = _run(runner, _aws_command("rds", "describe-db-instances"))
    instances = _required_list(response, "DBInstances")
    current_time = now()
    if current_time.tzinfo is None:
        raise AwsCommandError
    cutoff = current_time.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    deleted = 0
    deferred = 0
    for candidate in instances:
        if not isinstance(candidate, dict):
            raise AwsCommandError
        identifier = candidate.get("DBInstanceIdentifier")
        created_at = _parse_aws_time(candidate.get("InstanceCreateTime"))
        if (
            not isinstance(identifier, str)
            or not identifier.startswith(f"{source}-restore-")
            or created_at is None
            or created_at >= cutoff
        ):
            continue
        tags = _resource_tags(runner, candidate.get("DBInstanceArn"))
        if (
            tags.get("ManagedBy") != MANAGED_BY
            or tags.get("Purpose") != RESTORE_PURPOSE
        ):
            continue
        if candidate.get("DBInstanceStatus") == "deleting":
            raise AwsCommandError
        if deleted == 1:
            deferred += 1
            continue
        delete_restore(
            source=source,
            target=identifier,
            runner=runner,
            sleeper=sleeper,
        )
        deleted += 1
    return {
        "instances_deferred": deferred,
        "instances_deleted": deleted,
        "instances_examined": len(instances),
    }


def prune_verifiers(
    *,
    cluster: str,
    family: str,
    max_age_hours: int,
    runner: Runner = subprocess.run,
    now: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    _validate_group_name(cluster)
    _validate_group_name(family)
    if max_age_hours != RESTORE_MAX_AGE_HOURS:
        raise ValidationError
    response = _run(
        runner,
        _aws_command(
            "ecs",
            "list-tasks",
            "--cluster",
            cluster,
            "--family",
            family,
            "--desired-status",
            "RUNNING",
            "--max-items",
            "100",
        ),
    )
    task_arns = _required_list(response, "taskArns")
    if not task_arns:
        return {"tasks_deferred": 0, "tasks_examined": 0, "tasks_stopped": 0}
    if any(not isinstance(arn, str) or not arn for arn in task_arns):
        raise AwsCommandError
    described = _run(
        runner,
        _aws_command(
            "ecs",
            "describe-tasks",
            "--cluster",
            cluster,
            "--tasks",
            *task_arns,
            "--include",
            "TAGS",
        ),
    )
    tasks = _required_list(described, "tasks")
    if _required_list(described, "failures"):
        raise AwsCommandError
    current_time = now()
    if current_time.tzinfo is None:
        raise AwsCommandError
    cutoff = current_time.astimezone(timezone.utc) - timedelta(hours=max_age_hours)
    stopped = 0
    deferred = 0
    for task in tasks:
        if not isinstance(task, dict):
            raise AwsCommandError
        task_arn = task.get("taskArn")
        definition = task.get("taskDefinitionArn")
        started_at = _parse_aws_time(task.get("startedAt"))
        tags = {
            tag.get("key"): tag.get("value")
            for tag in task.get("tags", [])
            if isinstance(tag, dict)
            and isinstance(tag.get("key"), str)
            and isinstance(tag.get("value"), str)
        }
        if (
            not isinstance(task_arn, str)
            or not isinstance(definition, str)
            or f"task-definition/{family}:" not in definition
            or started_at is None
            or started_at >= cutoff
            or tags.get("ManagedBy") != MANAGED_BY
            or tags.get("Purpose") != RESTORE_PURPOSE
        ):
            continue
        if stopped == 1:
            deferred += 1
            continue
        _run(
            runner,
            _aws_command(
                "ecs",
                "stop-task",
                "--cluster",
                cluster,
                "--task",
                task_arn,
                "--reason",
                "Lumina stale restore verifier reconciliation",
            ),
        )
        for attempt in range(DELETE_STATE_WAIT_ATTEMPTS):
            task_response = _run(
                runner,
                _aws_command(
                    "ecs",
                    "describe-tasks",
                    "--cluster",
                    cluster,
                    "--tasks",
                    task_arn,
                ),
            )
            stopped_tasks = _required_list(task_response, "tasks")
            if _required_list(task_response, "failures"):
                raise AwsCommandError
            if (
                len(stopped_tasks) == 1
                and isinstance(stopped_tasks[0], dict)
                and stopped_tasks[0].get("lastStatus") == "STOPPED"
            ):
                break
            if attempt + 1 == DELETE_STATE_WAIT_ATTEMPTS:
                raise AwsCommandError
            sleeper(POLL_SECONDS)
        stopped += 1
    return {
        "tasks_deferred": deferred,
        "tasks_examined": len(tasks),
        "tasks_stopped": stopped,
    }


def _parser() -> SafeArgumentParser:
    parser = SafeArgumentParser(prog="aws_rds_recovery")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-snapshot")
    create.add_argument("--source", required=True)
    create.add_argument("--snapshot", required=True)
    create.add_argument("--release", required=True)
    create.add_argument("--run-id", required=True)
    create.add_argument("--run-attempt", required=True, type=int)
    create.add_argument("--purpose", required=True)
    create.add_argument("--retention-days", required=True, type=int)

    prune = commands.add_parser("prune-snapshots")
    prune.add_argument("--source", required=True)
    prune.add_argument("--retention-days", required=True, type=int)

    prune_instances = commands.add_parser("prune-restores")
    prune_instances.add_argument("--source", required=True)
    prune_instances.add_argument("--max-age-hours", required=True, type=int)

    prune_tasks = commands.add_parser("prune-verifiers")
    prune_tasks.add_argument("--cluster", required=True)
    prune_tasks.add_argument("--family", required=True)
    prune_tasks.add_argument("--max-age-hours", required=True, type=int)

    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--source", required=True)
    restore_parser.add_argument("--snapshot", required=True)
    restore_parser.add_argument("--target", required=True)
    restore_parser.add_argument("--db-subnet-group", required=True)
    restore_parser.add_argument("--security-group", required=True)
    restore_parser.add_argument("--parameter-group", required=True)
    restore_parser.add_argument("--option-group", required=True)

    delete = commands.add_parser("delete-restore")
    delete.add_argument("--source", required=True)
    delete.add_argument("--target", required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner = subprocess.run,
    now: Clock = _utc_now,
    sleeper: Sleeper = time.sleep,
) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "create-snapshot":
            result = create_snapshot(
                source=arguments.source,
                snapshot_identifier=arguments.snapshot,
                release=arguments.release,
                run_id=arguments.run_id,
                run_attempt=arguments.run_attempt,
                purpose=arguments.purpose,
                retention_days=arguments.retention_days,
                runner=runner,
                now=now,
                sleeper=sleeper,
            )
        elif arguments.command == "prune-snapshots":
            result = prune_snapshots(
                source=arguments.source,
                retention_days=arguments.retention_days,
                runner=runner,
                now=now,
            )
        elif arguments.command == "prune-restores":
            result = prune_restores(
                source=arguments.source,
                max_age_hours=arguments.max_age_hours,
                runner=runner,
                now=now,
                sleeper=sleeper,
            )
        elif arguments.command == "prune-verifiers":
            result = prune_verifiers(
                cluster=arguments.cluster,
                family=arguments.family,
                max_age_hours=arguments.max_age_hours,
                runner=runner,
                now=now,
                sleeper=sleeper,
            )
        elif arguments.command == "restore":
            result = restore(
                source=arguments.source,
                snapshot_identifier=arguments.snapshot,
                target=arguments.target,
                db_subnet_group=arguments.db_subnet_group,
                security_group=arguments.security_group,
                parameter_group=arguments.parameter_group,
                option_group=arguments.option_group,
                runner=runner,
                sleeper=sleeper,
            )
        else:
            result = delete_restore(
                source=arguments.source,
                target=arguments.target,
                runner=runner,
                sleeper=sleeper,
            )
    except ValidationError:
        print(json.dumps({"error": "validation_or_safety_error"}), file=sys.stderr)
        return 2
    except AwsCommandError:
        print(json.dumps({"error": "aws_command_failed"}), file=sys.stderr)
        return 1
    except Exception:
        print(json.dumps({"error": "unexpected_failure"}), file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
