mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name = "us-east-1"
    }
  }
}

variables {
  name_prefix                    = "lumina-production"
  repository                     = "coDDesigning/Lumina"
  environment_name               = "production"
  ecr_repository_arn             = "arn:aws:ecr:us-east-1:123456789012:repository/lumina"
  frontend_bucket_arn            = "arn:aws:s3:::lumina-production-frontend"
  cloudfront_distribution_arn    = "arn:aws:cloudfront::123456789012:distribution/E1234567890"
  ecs_cluster_name               = "lumina-production"
  api_service_name               = "lumina-production-api"
  worker_service_name            = "lumina-production-worker"
  task_definition_families       = ["lumina-production-api", "lumina-production-worker", "lumina-production-migrate"]
  ecs_task_role_arn              = "arn:aws:iam::123456789012:role/lumina-production-task"
  ecs_execution_role_arn         = "arn:aws:iam::123456789012:role/lumina-production-execution"
  restore_task_definition_family = "lumina-production-hosted-restore"
  restore_task_role_arn          = "arn:aws:iam::123456789012:role/lumina-production-restore-task"
  restore_execution_role_arn     = "arn:aws:iam::123456789012:role/lumina-production-restore-execution"
  rds_instance_identifier        = "lumina-production"
  rds_subnet_group_name          = "lumina-production"
  rds_parameter_group_name       = "lumina-production-pg16"
  rds_option_group_name          = "default:postgres-16"
  tags                           = { Environment = "production" }
}

run "hosted_recovery_policy" {
  command = plan

  module {
    source = "./modules/github-oidc"
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Action
      if statement.Sid == "RDSRecoveryDescribe"
    ])) == toset(["rds:DescribeDBInstances", "rds:DescribeDBSnapshots"])
    error_message = "Recovery may describe only DB instances and snapshots through the global RDS list APIs."
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "RDSRecoveryCreateSnapshots"
      ])) == toset([
      "arn:aws:rds:us-east-1:123456789012:db:lumina-production",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-predeploy-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-drill-*",
    ])
    error_message = "Snapshot creation must be limited to the production DB and managed predeploy/drill names."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement
      if statement.Sid == "RDSRecoveryCreateSnapshots"
    ]).Condition.StringEquals["aws:RequestTag/ManagedBy"] == "LuminaHostedRecovery"
    error_message = "Snapshot creation must require the Lumina managed-resource tag."
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "RDSRecoveryRestoreTemporaryInstances"
      ])) == toset([
      "arn:aws:rds:us-east-1:123456789012:db:lumina-production-restore-*",
      "arn:aws:rds:us-east-1:123456789012:subgrp:lumina-production",
      "arn:aws:rds:us-east-1:123456789012:pg:lumina-production-pg16",
      "arn:aws:rds:us-east-1:123456789012:og:default:postgres-16",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-predeploy-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-drill-*",
    ])
    error_message = "Restores must use managed snapshots, the production subnet group, and temporary restore identifiers."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement
      if statement.Sid == "RDSRecoveryRestoreTemporaryInstances"
    ]).Condition.BoolIfExists["rds:PubliclyAccessible"] == "false"
    error_message = "Recovery restores must not be publicly accessible."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement
      if statement.Sid == "RDSRecoveryRestoreTemporaryInstances"
      ]).Condition.StringEquals == {
      "aws:RequestTag/ManagedBy" = "LuminaHostedRecovery"
      "aws:RequestTag/Purpose"   = "restore-drill"
    }
    error_message = "Recovery restores must be tagged as managed drill targets at creation."
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "RDSRecoveryTagManagedResources"
      ])) == toset([
      "arn:aws:rds:us-east-1:123456789012:db:lumina-production-restore-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-predeploy-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-drill-*",
    ])
    error_message = "Tagging must be limited to managed snapshots and temporary restore DBs."
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "RDSRecoveryListTags"
      ])) == toset([
      "arn:aws:rds:us-east-1:123456789012:db:lumina-production",
      "arn:aws:rds:us-east-1:123456789012:db:lumina-production-restore-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-predeploy-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-drill-*",
    ])
    error_message = "Tag reads must be limited to the production DB and managed recovery resources."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "RDSRecoveryDeleteTemporaryInstances"
    ]) == "arn:aws:rds:us-east-1:123456789012:db:lumina-production-restore-*"
    error_message = "DeleteDBInstance must apply only to temporary restore DBs."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement
      if statement.Sid == "RDSRecoveryDeleteTemporaryInstances"
      ]).Condition.StringEquals == {
      "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery"
      "aws:ResourceTag/Purpose"   = "restore-drill"
    }
    error_message = "Temporary DB deletion must require both recovery safety tags."
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "RDSRecoveryDeleteManagedSnapshots"
      ])) == toset([
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-predeploy-*",
      "arn:aws:rds:us-east-1:123456789012:snapshot:lumina-production-drill-*",
    ])
    error_message = "Snapshot retention may delete only managed predeploy and drill snapshots."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement
      if statement.Sid == "RDSRecoveryDeleteManagedSnapshots"
    ]).Condition.StringEquals["aws:ResourceTag/ManagedBy"] == "LuminaHostedRecovery"
    error_message = "Snapshot retention must require the Lumina managed-resource tag."
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Action
      if anytrue([for action in statement.Action : startswith(action, "rds:")])
      ])) == toset([
      "rds:AddTagsToResource",
      "rds:CreateDBSnapshot",
      "rds:DeleteDBInstance",
      "rds:DeleteDBSnapshot",
      "rds:DescribeDBInstances",
      "rds:DescribeDBSnapshots",
      "rds:ListTagsForResource",
      "rds:RestoreDBInstanceFromDBSnapshot",
    ])
    error_message = "Recovery must not gain wildcard, modify, proxy-target, secret-read, or other RDS permissions."
  }

  assert {
    condition = alltrue(flatten([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : [
        for action in statement.Action : !startswith(action, "secretsmanager:")
      ]
    ]))
    error_message = "The deploy role must not read database secrets."
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Action
      ])) == toset([
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTaskDefinitions",
      "ecs:ListTasks",
      "ecs:RunTask",
      "ecs:StopTask",
      "ecs:TagResource",
      "iam:PassRole",
      "rds:AddTagsToResource",
      "rds:CreateDBSnapshot",
      "rds:DeleteDBInstance",
      "rds:DeleteDBSnapshot",
      "rds:DescribeDBInstances",
      "rds:DescribeDBSnapshots",
      "rds:ListTagsForResource",
      "rds:RestoreDBInstanceFromDBSnapshot",
    ])
    error_message = "The recovery role must contain only recovery orchestration permissions."
  }

  assert {
    condition = toset(one([
      for statement in jsondecode(one(aws_iam_role.github_recovery.inline_policy).policy).Statement : statement.Resource
      if statement.Sid == "PassRecoveryTaskRoles"
      ])) == toset([
      "arn:aws:iam::123456789012:role/lumina-production-restore-task",
      "arn:aws:iam::123456789012:role/lumina-production-restore-execution",
    ])
    error_message = "Recovery may pass only the read-only verifier task roles."
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(one(aws_iam_role.github_reconciler.inline_policy).policy).Statement : statement.Action
      ])) == toset([
      "ecs:DescribeTasks",
      "ecs:ListTasks",
      "ecs:StopTask",
      "rds:DeleteDBInstance",
      "rds:DeleteDBSnapshot",
      "rds:DescribeDBInstances",
      "rds:DescribeDBSnapshots",
      "rds:ListTagsForResource",
    ])
    error_message = "The unattended reconciler must have deletion-only recovery permissions."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.github_reconciler.inline_policy).policy).Statement : statement
      if statement.Sid == "ECSReconciliationStop"
      ]).Condition.StringEquals == {
      "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery"
      "aws:ResourceTag/Purpose"   = "restore-drill"
    }
    error_message = "The unattended reconciler may stop only tagged verifier tasks."
  }
}
