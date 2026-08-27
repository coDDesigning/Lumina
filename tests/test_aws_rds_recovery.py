from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any

import pytest

from ops import aws_rds_recovery

SNAPSHOT_ID = "lumina-production-predeploy-aaaaaaaa-12345-2"


def command(*arguments: str) -> list[str]:
    return ["aws", *arguments, "--output", "json", "--no-cli-pager"]


class FakeRunner:
    def __init__(
        self, responses: list[dict[str, Any] | None | subprocess.CalledProcessError]
    ) -> None:
        self.responses = responses
        self.commands: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, arguments: list[str], **kwargs: Any):
        self.commands.append(arguments)
        self.kwargs.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, subprocess.CalledProcessError):
            raise response
        stdout = "" if response is None else json.dumps(response)
        return subprocess.CompletedProcess(arguments, 0, stdout=stdout, stderr="")


def snapshot(
    identifier: str = SNAPSHOT_ID,
    source: str = "lumina-production",
    *,
    encrypted: bool = True,
) -> dict[str, Any]:
    return {
        "DBSnapshotIdentifier": identifier,
        "DBInstanceIdentifier": source,
        "Status": "available",
        "Encrypted": encrypted,
        "SnapshotType": "manual",
        "DBSnapshotArn": f"arn:{identifier}",
    }


def restored_instance(*, publicly_accessible: bool = False) -> dict[str, Any]:
    return {
        "DBInstanceIdentifier": "lumina-production-restore-1",
        "DBInstanceArn": "arn:lumina-production-restore-1",
        "DBInstanceStatus": "available",
        "Engine": "postgres",
        "StorageEncrypted": True,
        "PubliclyAccessible": publicly_accessible,
        "DeletionProtection": False,
        "DBSubnetGroup": {"DBSubnetGroupName": "lumina-private"},
        "VpcSecurityGroups": [
            {"VpcSecurityGroupId": "sg-0123456789abcdef0", "Status": "active"}
        ],
        "DBParameterGroups": [
            {
                "DBParameterGroupName": "lumina-production-pg",
                "ParameterApplyStatus": "in-sync",
            }
        ],
        "OptionGroupMemberships": [
            {"OptionGroupName": "default:postgres-16", "Status": "in-sync"}
        ],
        "Endpoint": {"Address": "restore.internal.example"},
    }


@pytest.mark.parametrize(
    "identifier",
    [
        "1database",
        "Database",
        "database-",
        "database--restore",
        "database_name",
        "a" * 64,
    ],
)
def test_restore_rejects_invalid_rds_identifiers_without_calling_aws(identifier):
    runner = FakeRunner([])

    result = aws_rds_recovery.main(
        [
            "restore",
            "--source",
            identifier,
            "--snapshot",
            SNAPSHOT_ID,
            "--target",
            "lumina-production-restore-drill",
            "--db-subnet-group",
            "lumina-private",
            "--security-group",
            "sg-0123456789abcdef0",
            "--parameter-group",
            "lumina-production-pg",
            "--option-group",
            "default:postgres-16",
        ],
        runner=runner,
    )

    assert result == 2
    assert runner.commands == []


def test_create_snapshot_uses_exact_commands_and_deletes_only_safe_old_snapshots(
    capsys,
):
    release = "a" * 40
    snapshots = [
        {
            **snapshot("lumina-production-predeploy-old-matching"),
            "SnapshotCreateTime": "2026-06-01T00:00:00Z",
        },
        {
            **snapshot("lumina-production-predeploy-old-wrong-purpose"),
            "SnapshotCreateTime": "2026-06-02T00:00:00Z",
        },
        {
            **snapshot("lumina-production-predeploy-recent-matching"),
            "SnapshotCreateTime": "2026-08-20T00:00:00Z",
        },
        {
            **snapshot("lumina-production-predeploy-old-creating"),
            "SnapshotCreateTime": "2026-06-01T00:00:00Z",
            "Status": "creating",
        },
        {
            **snapshot(
                "other-production-predeploy-old",
                source="other-production",
            ),
            "SnapshotCreateTime": "2026-06-01T00:00:00Z",
        },
    ]
    runner = FakeRunner(
        [
            {"DBSnapshot": snapshot()},
            {"DBSnapshots": [snapshot()]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "predeployment"},
                ]
            },
            {"DBSnapshots": snapshots},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "predeployment"},
                ]
            },
            {"DBSnapshot": snapshot("lumina-production-predeploy-old-matching")},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
        ]
    )

    result = aws_rds_recovery.main(
        [
            "create-snapshot",
            "--source",
            "lumina-production",
            "--snapshot",
            SNAPSHOT_ID,
            "--release",
            release,
            "--run-id",
            "12345",
            "--run-attempt",
            "2",
            "--purpose",
            "predeployment",
            "--retention-days",
            "30",
        ],
        runner=runner,
        now=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc),
    )

    assert result == 0
    assert runner.commands == [
        command(
            "rds",
            "create-db-snapshot",
            "--db-instance-identifier",
            "lumina-production",
            "--db-snapshot-identifier",
            SNAPSHOT_ID,
            "--tags",
            "Key=ManagedBy,Value=LuminaHostedRecovery",
            "Key=Purpose,Value=predeployment",
            f"Key=Release,Value={release}",
            "Key=WorkflowRun,Value=12345",
        ),
        command(
            "rds",
            "describe-db-snapshots",
            "--db-snapshot-identifier",
            SNAPSHOT_ID,
            "--snapshot-type",
            "manual",
        ),
        command(
            "rds",
            "list-tags-for-resource",
            "--resource-name",
            f"arn:{SNAPSHOT_ID}",
        ),
        command(
            "rds",
            "describe-db-snapshots",
            "--db-instance-identifier",
            "lumina-production",
            "--snapshot-type",
            "manual",
        ),
        command(
            "rds",
            "list-tags-for-resource",
            "--resource-name",
            "arn:lumina-production-predeploy-old-matching",
        ),
        command(
            "rds",
            "delete-db-snapshot",
            "--db-snapshot-identifier",
            "lumina-production-predeploy-old-matching",
        ),
        command(
            "rds",
            "list-tags-for-resource",
            "--resource-name",
            "arn:lumina-production-predeploy-old-wrong-purpose",
        ),
    ]
    assert all(
        kwargs
        == {
            "check": True,
            "capture_output": True,
            "text": True,
            "shell": False,
            "timeout": 60,
        }
        for kwargs in runner.kwargs
    )
    assert json.loads(capsys.readouterr().out) == {
        "snapshot_identifier": SNAPSHOT_ID,
        "snapshots_deleted": 1,
        "snapshots_examined": 5,
    }


def test_scheduled_retention_prunes_both_managed_snapshot_purposes(capsys):
    predeploy = {
        **snapshot("lumina-production-predeploy-old"),
        "SnapshotCreateTime": "2026-06-01T00:00:00Z",
    }
    drill = {
        **snapshot("lumina-production-drill-old"),
        "SnapshotCreateTime": "2026-06-01T00:00:00Z",
    }
    runner = FakeRunner(
        [
            {"DBSnapshots": [predeploy, drill]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "predeployment"},
                ]
            },
            {"DBSnapshot": predeploy},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
            {"DBSnapshot": drill},
        ]
    )

    result = aws_rds_recovery.main(
        [
            "prune-snapshots",
            "--source",
            "lumina-production",
            "--retention-days",
            "30",
        ],
        runner=runner,
        now=lambda: datetime(2026, 8, 26, tzinfo=timezone.utc),
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "snapshots_deleted": 2,
        "snapshots_examined": 2,
    }
    delete_commands = [
        invocation
        for invocation in runner.commands
        if "delete-db-snapshot" in invocation
    ]
    assert len(delete_commands) == 2


def test_scheduled_reconciliation_deletes_only_stale_managed_restores(capsys):
    stale = {
        **restored_instance(),
        "InstanceCreateTime": "2026-08-25T00:00:00Z",
    }
    runner = FakeRunner(
        [
            {
                "DBInstances": [
                    stale,
                    {
                        **restored_instance(),
                        "DBInstanceIdentifier": "lumina-production-restore-recent",
                        "InstanceCreateTime": "2026-08-26T10:00:00Z",
                    },
                    {
                        **restored_instance(),
                        "DBInstanceIdentifier": "other-production-restore-stale",
                        "InstanceCreateTime": "2026-08-25T00:00:00Z",
                    },
                    {
                        **restored_instance(),
                        "DBInstanceIdentifier": "lumina-production-restore-deleting",
                        "DBInstanceStatus": "deleting",
                        "InstanceCreateTime": "2026-08-26T10:00:00Z",
                    },
                ]
            },
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
            {"DBInstances": [stale]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
            {"DBInstance": {}},
            subprocess.CalledProcessError(
                255, [], stderr="DBInstanceNotFound: restore target is gone"
            ),
        ]
    )

    result = aws_rds_recovery.main(
        [
            "prune-restores",
            "--source",
            "lumina-production",
            "--max-age-hours",
            "6",
        ],
        runner=runner,
        now=lambda: datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "instances_deferred": 0,
        "instances_deleted": 1,
        "instances_examined": 4,
    }
    assert sum("delete-db-instance" in command for command in runner.commands) == 1


def test_scheduled_reconciliation_stops_only_stale_managed_verifiers(capsys):
    cluster = "lumina-production"
    family = "lumina-production-hosted-restore"
    stale_arn = f"arn:aws:ecs:us-east-1:123456789012:task/{cluster}/stale"
    recent_arn = f"arn:aws:ecs:us-east-1:123456789012:task/{cluster}/recent"
    definition = f"arn:aws:ecs:us-east-1:123456789012:task-definition/{family}:7"
    tags = [
        {"key": "ManagedBy", "value": "LuminaHostedRecovery"},
        {"key": "Purpose", "value": "restore-drill"},
    ]
    runner = FakeRunner(
        [
            {"taskArns": [stale_arn, recent_arn]},
            {
                "failures": [],
                "tasks": [
                    {
                        "taskArn": stale_arn,
                        "taskDefinitionArn": definition,
                        "startedAt": "2026-08-25T00:00:00Z",
                        "tags": tags,
                    },
                    {
                        "taskArn": recent_arn,
                        "taskDefinitionArn": definition,
                        "startedAt": "2026-08-26T10:00:00Z",
                        "tags": tags,
                    },
                ],
            },
            {"task": {}},
            {
                "failures": [],
                "tasks": [{"taskArn": stale_arn, "lastStatus": "STOPPED"}],
            },
        ]
    )

    result = aws_rds_recovery.main(
        [
            "prune-verifiers",
            "--cluster",
            cluster,
            "--family",
            family,
            "--max-age-hours",
            "6",
        ],
        runner=runner,
        now=lambda: datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out) == {
        "tasks_deferred": 0,
        "tasks_examined": 2,
        "tasks_stopped": 1,
    }
    assert next(
        invocation for invocation in runner.commands if "stop-task" in invocation
    ) == command(
        "ecs",
        "stop-task",
        "--cluster",
        cluster,
        "--task",
        stale_arn,
        "--reason",
        "Lumina stale restore verifier reconciliation",
    )


@pytest.mark.parametrize(
    ("source", "encrypted"),
    [("different-production", True), ("lumina-production", False)],
)
def test_restore_rejects_wrong_source_or_unencrypted_snapshot(source, encrypted):
    runner = FakeRunner(
        [{"DBSnapshots": [snapshot(source=source, encrypted=encrypted)]}]
    )

    result = aws_rds_recovery.main(
        [
            "restore",
            "--source",
            "lumina-production",
            "--snapshot",
            SNAPSHOT_ID,
            "--target",
            "lumina-production-restore-drill",
            "--db-subnet-group",
            "lumina-private",
            "--security-group",
            "sg-0123456789abcdef0",
            "--parameter-group",
            "lumina-production-pg",
            "--option-group",
            "default:postgres-16",
        ],
        runner=runner,
    )

    assert result == 2
    assert len(runner.commands) == 1


def test_restore_uses_private_flags_and_validates_restored_instance(capsys):
    runner = FakeRunner(
        [
            {"DBSnapshots": [snapshot()]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "predeployment"},
                ]
            },
            {"DBInstance": {"DBInstanceIdentifier": "lumina-production-restore-1"}},
            {"DBInstances": [restored_instance()]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
        ]
    )

    result = aws_rds_recovery.main(
        [
            "restore",
            "--source",
            "lumina-production",
            "--snapshot",
            SNAPSHOT_ID,
            "--target",
            "lumina-production-restore-1",
            "--db-subnet-group",
            "lumina-private",
            "--security-group",
            "sg-0123456789abcdef0",
            "--parameter-group",
            "lumina-production-pg",
            "--option-group",
            "default:postgres-16",
        ],
        runner=runner,
    )

    assert result == 0
    assert runner.commands == [
        command(
            "rds",
            "describe-db-snapshots",
            "--db-snapshot-identifier",
            SNAPSHOT_ID,
            "--snapshot-type",
            "manual",
        ),
        command(
            "rds",
            "list-tags-for-resource",
            "--resource-name",
            f"arn:{SNAPSHOT_ID}",
        ),
        command(
            "rds",
            "restore-db-instance-from-db-snapshot",
            "--db-instance-identifier",
            "lumina-production-restore-1",
            "--db-snapshot-identifier",
            SNAPSHOT_ID,
            "--db-subnet-group-name",
            "lumina-private",
            "--vpc-security-group-ids",
            "sg-0123456789abcdef0",
            "--db-parameter-group-name",
            "lumina-production-pg",
            "--option-group-name",
            "default:postgres-16",
            "--no-publicly-accessible",
            "--no-deletion-protection",
            "--tags",
            "Key=ManagedBy,Value=LuminaHostedRecovery",
            "Key=Purpose,Value=restore-drill",
        ),
        command(
            "rds",
            "describe-db-instances",
            "--db-instance-identifier",
            "lumina-production-restore-1",
        ),
        command(
            "rds",
            "list-tags-for-resource",
            "--resource-name",
            "arn:lumina-production-restore-1",
        ),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "endpoint": "restore.internal.example",
        "target_identifier": "lumina-production-restore-1",
    }


def test_restore_rejects_public_restored_instance():
    runner = FakeRunner(
        [
            {"DBSnapshots": [snapshot()]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "predeployment"},
                ]
            },
            {"DBInstance": {}},
            {"DBInstances": [restored_instance(publicly_accessible=True)]},
        ]
    )

    result = aws_rds_recovery.main(
        [
            "restore",
            "--source",
            "lumina-production",
            "--snapshot",
            SNAPSHOT_ID,
            "--target",
            "lumina-production-restore-1",
            "--db-subnet-group",
            "lumina-private",
            "--security-group",
            "sg-0123456789abcdef0",
            "--parameter-group",
            "lumina-production-pg",
            "--option-group",
            "default:postgres-16",
        ],
        runner=runner,
    )

    assert result == 2


def test_restore_rejects_unmanaged_snapshot_without_calling_aws():
    runner = FakeRunner([])

    result = aws_rds_recovery.main(
        [
            "restore",
            "--source",
            "lumina-production",
            "--snapshot",
            "other-manual-snapshot",
            "--target",
            "lumina-production-restore-1",
            "--db-subnet-group",
            "lumina-private",
            "--security-group",
            "sg-0123456789abcdef0",
            "--parameter-group",
            "lumina-production-pg",
            "--option-group",
            "default:postgres-16",
        ],
        runner=runner,
    )

    assert result == 2
    assert runner.commands == []


def test_restore_rejects_snapshot_without_matching_managed_tags():
    runner = FakeRunner(
        [
            {"DBSnapshots": [snapshot()]},
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
        ]
    )

    result = aws_rds_recovery.main(
        [
            "restore",
            "--source",
            "lumina-production",
            "--snapshot",
            SNAPSHOT_ID,
            "--target",
            "lumina-production-restore-1",
            "--db-subnet-group",
            "lumina-private",
            "--security-group",
            "sg-0123456789abcdef0",
            "--parameter-group",
            "lumina-production-pg",
            "--option-group",
            "default:postgres-16",
        ],
        runner=runner,
    )

    assert result == 2
    assert len(runner.commands) == 2


@pytest.mark.parametrize(
    "target", ["lumina-production", "production", "lumina-production-copy"]
)
def test_delete_restore_refuses_production_or_unsafe_names(target):
    runner = FakeRunner([])

    result = aws_rds_recovery.main(
        [
            "delete-restore",
            "--source",
            "lumina-production",
            "--target",
            target,
        ],
        runner=runner,
    )

    assert result == 2
    assert runner.commands == []


@pytest.mark.parametrize(
    "tags",
    [
        [{"Key": "Purpose", "Value": "restore-drill"}],
        [
            {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
            {"Key": "Purpose", "Value": "predeployment"},
        ],
    ],
)
def test_delete_restore_requires_both_safety_tags(tags):
    runner = FakeRunner(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "lumina-production-restore-1",
                        "DBInstanceArn": "arn:restore-1",
                        "DBInstanceStatus": "available",
                    }
                ]
            },
            {"TagList": tags},
        ]
    )

    result = aws_rds_recovery.main(
        [
            "delete-restore",
            "--source",
            "lumina-production",
            "--target",
            "lumina-production-restore-1",
        ],
        runner=runner,
    )

    assert result == 2
    assert len(runner.commands) == 2


def test_delete_restore_uses_exact_tag_gated_commands(capsys):
    runner = FakeRunner(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "lumina-production-restore-1",
                        "DBInstanceArn": "arn:restore-1",
                        "DBInstanceStatus": "available",
                    }
                ]
            },
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
            {"DBInstance": {}},
            subprocess.CalledProcessError(
                255, [], stderr="DBInstanceNotFound: restore target is gone"
            ),
        ]
    )

    result = aws_rds_recovery.main(
        [
            "delete-restore",
            "--source",
            "lumina-production",
            "--target",
            "lumina-production-restore-1",
        ],
        runner=runner,
    )

    assert result == 0
    assert runner.commands == [
        command(
            "rds",
            "describe-db-instances",
            "--db-instance-identifier",
            "lumina-production-restore-1",
        ),
        command(
            "rds",
            "list-tags-for-resource",
            "--resource-name",
            "arn:restore-1",
        ),
        command(
            "rds",
            "delete-db-instance",
            "--db-instance-identifier",
            "lumina-production-restore-1",
            "--skip-final-snapshot",
        ),
        command(
            "rds",
            "describe-db-instances",
            "--db-instance-identifier",
            "lumina-production-restore-1",
        ),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "instances_deleted": 1,
        "target_identifier": "lumina-production-restore-1",
    }


def test_delete_restore_retries_a_transient_invalid_state():
    sleeps: list[float] = []
    runner = FakeRunner(
        [
            {
                "DBInstances": [
                    {
                        "DBInstanceIdentifier": "lumina-production-restore-1",
                        "DBInstanceArn": "arn:restore-1",
                        "DBInstanceStatus": "available",
                    }
                ]
            },
            {
                "TagList": [
                    {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
                    {"Key": "Purpose", "Value": "restore-drill"},
                ]
            },
            subprocess.CalledProcessError(
                255, [], stderr="InvalidDBInstanceState: transition in progress"
            ),
            {"DBInstance": {}},
            subprocess.CalledProcessError(
                255, [], stderr="DBInstanceNotFound: restore target is gone"
            ),
        ]
    )

    result = aws_rds_recovery.main(
        [
            "delete-restore",
            "--source",
            "lumina-production",
            "--target",
            "lumina-production-restore-1",
        ],
        runner=runner,
        sleeper=sleeps.append,
    )

    assert result == 0
    assert sleeps == [aws_rds_recovery.POLL_SECONDS]
    assert sum("delete-db-instance" in command for command in runner.commands) == 2


def test_aws_failure_output_and_exception_text_are_sanitized(capsys):
    secret = "database-password-and-arn"

    def failing_runner(arguments, **kwargs):
        raise subprocess.CalledProcessError(
            255, arguments, output=secret, stderr=secret
        )

    result = aws_rds_recovery.main(
        [
            "delete-restore",
            "--source",
            "lumina-production",
            "--target",
            "lumina-production-restore-1",
        ],
        runner=failing_runner,
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "aws_command_failed"}
    assert secret not in captured.err
