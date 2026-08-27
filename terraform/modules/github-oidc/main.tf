data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  # Standard GitHub Actions thumbprint (token.actions.githubusercontent.com).
  # Update only if GitHub rotates this certificate.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.repository}:environment:${var.environment_name}"]
    }
  }
}

data "aws_iam_policy_document" "github_recovery_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.repository}:environment:${var.environment_name}"]
    }
  }
}

data "aws_iam_policy_document" "github_reconciler_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.repository}:environment:${var.recovery_environment_name}"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name                 = "${var.name_prefix}-github-actions"
  description          = "Role assumed by the Lumina deploy workflow via GitHub OIDC"
  assume_role_policy   = data.aws_iam_policy_document.github_trust.json
  max_session_duration = 7200
  inline_policy {
    name = "deploy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ECRAuthorization"
          Effect   = "Allow"
          Action   = ["ecr:GetAuthorizationToken"]
          Resource = "*"
        },
        {
          Sid    = "ECRRepository"
          Effect = "Allow"
          Action = [
            "ecr:PutImage",
            "ecr:InitiateLayerUpload",
            "ecr:UploadLayerPart",
            "ecr:CompleteLayerUpload",
            "ecr:BatchGetImage",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
          ]
          Resource = var.ecr_repository_arn
        },
        {
          Sid    = "FrontendObjectRead"
          Effect = "Allow"
          Action = ["s3:GetObject"]
          Resource = [
            "${var.frontend_bucket_arn}/releases/*/frontend.tar.gz",
            "${var.frontend_bucket_arn}/releases/*/task-definitions.json",
          ]
        },
        {
          Sid    = "FrontendCurrentWrite"
          Effect = "Allow"
          Action = [
            "s3:DeleteObject",
            "s3:GetObject",
            "s3:PutObject",
          ]
          Resource = "${var.frontend_bucket_arn}/current/*"
        },
        {
          Sid      = "FrontendCurrentList"
          Effect   = "Allow"
          Action   = ["s3:ListBucket"]
          Resource = var.frontend_bucket_arn
          Condition = {
            StringLike = {
              "s3:prefix" = ["current", "current/*"]
            }
          }
        },
        {
          Sid    = "FrontendReleaseCreate"
          Effect = "Allow"
          Action = ["s3:PutObject"]
          Resource = [
            "${var.frontend_bucket_arn}/releases/*/frontend.tar.gz",
            "${var.frontend_bucket_arn}/releases/*/task-definitions.json",
          ]
          Condition = {
            Null = {
              "s3:if-none-match" = "false"
            }
          }
        },
        {
          Sid    = "FrontendInvalidation"
          Effect = "Allow"
          Action = [
            "cloudfront:CreateInvalidation",
            "cloudfront:GetInvalidation",
          ]
          Resource = var.cloudfront_distribution_arn
        },
        {
          Sid      = "ECSDescribeTaskDefinitions"
          Effect   = "Allow"
          Action   = ["ecs:DescribeTaskDefinition"]
          Resource = "*"
        },
        {
          Sid      = "ECSDescribeServices"
          Effect   = "Allow"
          Action   = ["ecs:DescribeServices"]
          Resource = [local.api_svc_arn, local.worker_svc_arn]
        },
        {
          Sid      = "ECSDescribeTasks"
          Effect   = "Allow"
          Action   = ["ecs:DescribeTasks"]
          Resource = local.cluster_task_arn
        },
        {
          Sid    = "ECSUpdateServices"
          Effect = "Allow"
          Action = ["ecs:UpdateService"]
          Resource = [
            local.api_svc_arn,
            local.worker_svc_arn,
          ]
        },
        {
          Sid      = "ECSRunTask"
          Effect   = "Allow"
          Action   = ["ecs:RunTask"]
          Resource = concat(local.taskdef_arns, [for arn in local.taskdef_arns : "${arn}:*"])
          Condition = {
            ArnEquals = {
              "ecs:cluster" = local.cluster_arn
            }
          }
        },
        {
          Sid      = "ECSRegister"
          Effect   = "Allow"
          Action   = ["ecs:RegisterTaskDefinition"]
          Resource = "*"
        },
        {
          Sid    = "PassTaskRoles"
          Effect = "Allow"
          Action = ["iam:PassRole"]
          Resource = [
            var.ecs_task_role_arn,
            var.ecs_execution_role_arn,
            var.restore_task_role_arn,
            var.restore_execution_role_arn,
          ]
          Condition = {
            StringEquals = {
              "iam:PassedToService" = "ecs-tasks.amazonaws.com"
            }
          }
        },
        {
          Sid      = "RDSRecoveryDescribe"
          Effect   = "Allow"
          Action   = ["rds:DescribeDBSnapshots"]
          Resource = "*"
        },
        {
          Sid      = "RDSRecoveryListTags"
          Effect   = "Allow"
          Action   = ["rds:ListTagsForResource"]
          Resource = local.rds_managed_snapshots
        },
        {
          Sid      = "RDSRecoveryCreateSnapshots"
          Effect   = "Allow"
          Action   = ["rds:CreateDBSnapshot"]
          Resource = concat([local.rds_db_arn], local.rds_managed_snapshots)
          Condition = {
            StringEquals = {
              "aws:RequestTag/ManagedBy" = "LuminaHostedRecovery"
            }
          }
        },
        {
          Sid      = "RDSRecoveryTagManagedResources"
          Effect   = "Allow"
          Action   = ["rds:AddTagsToResource"]
          Resource = local.rds_managed_snapshots
          Condition = {
            StringEquals = {
              "aws:RequestTag/ManagedBy" = "LuminaHostedRecovery"
            }
          }
        },
        {
          Sid      = "RDSRecoveryDeleteManagedSnapshots"
          Effect   = "Allow"
          Action   = ["rds:DeleteDBSnapshot"]
          Resource = local.rds_managed_snapshots
          Condition = {
            StringEquals = {
              "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery"
            }
          }
        },
      ]
    })
  }
  tags = var.tags
}


resource "aws_iam_role" "github_recovery" {
  name                 = "${var.name_prefix}-github-recovery"
  description          = "Least-privilege role for Lumina hosted recovery workflows"
  assume_role_policy   = data.aws_iam_policy_document.github_recovery_trust.json
  max_session_duration = 7200
  inline_policy {
    name = "hosted-recovery"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ECSRecoveryDescribe"
          Effect   = "Allow"
          Action   = ["ecs:DescribeServices", "ecs:DescribeTaskDefinition", "ecs:DescribeTasks", "ecs:ListTaskDefinitions", "ecs:ListTasks"]
          Resource = "*"
        },
        {
          Sid      = "ECSRecoveryRun"
          Effect   = "Allow"
          Action   = ["ecs:RunTask"]
          Resource = [local.restore_taskdef_arn, "${local.restore_taskdef_arn}:*"]
          Condition = {
            ArnEquals = { "ecs:cluster" = local.cluster_arn }
          }
        },
        {
          Sid      = "ECSRecoveryStopAndTag"
          Effect   = "Allow"
          Action   = ["ecs:StopTask", "ecs:TagResource"]
          Resource = local.cluster_task_arn
        },
        {
          Sid      = "PassRecoveryTaskRoles"
          Effect   = "Allow"
          Action   = ["iam:PassRole"]
          Resource = [var.restore_task_role_arn, var.restore_execution_role_arn]
          Condition = {
            StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
          }
        },
        {
          Sid      = "RDSRecoveryDescribe"
          Effect   = "Allow"
          Action   = ["rds:DescribeDBInstances", "rds:DescribeDBSnapshots"]
          Resource = "*"
        },
        {
          Sid      = "RDSRecoveryListTags"
          Effect   = "Allow"
          Action   = ["rds:ListTagsForResource"]
          Resource = concat([local.rds_db_arn, local.rds_restore_db_arn], local.rds_managed_snapshots)
        },
        {
          Sid      = "RDSRecoveryCreateSnapshots"
          Effect   = "Allow"
          Action   = ["rds:CreateDBSnapshot"]
          Resource = concat([local.rds_db_arn], local.rds_managed_snapshots)
          Condition = {
            StringEquals = { "aws:RequestTag/ManagedBy" = "LuminaHostedRecovery" }
          }
        },
        {
          Sid      = "RDSRecoveryTagManagedResources"
          Effect   = "Allow"
          Action   = ["rds:AddTagsToResource"]
          Resource = concat([local.rds_restore_db_arn], local.rds_managed_snapshots)
          Condition = {
            StringEquals = { "aws:RequestTag/ManagedBy" = "LuminaHostedRecovery" }
          }
        },
        {
          Sid    = "RDSRecoveryRestoreTemporaryInstances"
          Effect = "Allow"
          Action = ["rds:RestoreDBInstanceFromDBSnapshot"]
          Resource = concat([
            local.rds_restore_db_arn,
            local.rds_subnet_group_arn,
            local.rds_parameter_group_arn,
            local.rds_option_group_arn,
          ], local.rds_managed_snapshots)
          Condition = {
            BoolIfExists = { "rds:PubliclyAccessible" = "false" }
            StringEquals = {
              "aws:RequestTag/ManagedBy" = "LuminaHostedRecovery"
              "aws:RequestTag/Purpose"   = "restore-drill"
            }
          }
        },
        {
          Sid      = "RDSRecoveryDeleteTemporaryInstances"
          Effect   = "Allow"
          Action   = ["rds:DeleteDBInstance"]
          Resource = local.rds_restore_db_arn
          Condition = {
            StringEquals = {
              "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery"
              "aws:ResourceTag/Purpose"   = "restore-drill"
            }
          }
        },
        {
          Sid      = "RDSRecoveryDeleteManagedSnapshots"
          Effect   = "Allow"
          Action   = ["rds:DeleteDBSnapshot"]
          Resource = local.rds_managed_snapshots
          Condition = {
            StringEquals = { "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery" }
          }
        },
      ]
    })
  }
  tags = var.tags
}

resource "aws_iam_role" "github_reconciler" {
  name                 = "${var.name_prefix}-github-reconciler"
  description          = "Deletion-only role for unattended Lumina recovery reconciliation"
  assume_role_policy   = data.aws_iam_policy_document.github_reconciler_trust.json
  max_session_duration = 7200
  inline_policy {
    name = "hosted-reconciliation"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid      = "ECSReconciliationDescribe"
          Effect   = "Allow"
          Action   = ["ecs:DescribeTasks", "ecs:ListTasks"]
          Resource = "*"
        },
        {
          Sid      = "ECSReconciliationStop"
          Effect   = "Allow"
          Action   = ["ecs:StopTask"]
          Resource = local.cluster_task_arn
          Condition = {
            StringEquals = {
              "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery"
              "aws:ResourceTag/Purpose"   = "restore-drill"
            }
          }
        },
        {
          Sid      = "RDSReconciliationDescribe"
          Effect   = "Allow"
          Action   = ["rds:DescribeDBInstances", "rds:DescribeDBSnapshots"]
          Resource = "*"
        },
        {
          Sid      = "RDSReconciliationListTags"
          Effect   = "Allow"
          Action   = ["rds:ListTagsForResource"]
          Resource = concat([local.rds_restore_db_arn], local.rds_managed_snapshots)
        },
        {
          Sid      = "RDSReconciliationDeleteTemporaryInstances"
          Effect   = "Allow"
          Action   = ["rds:DeleteDBInstance"]
          Resource = local.rds_restore_db_arn
          Condition = {
            StringEquals = {
              "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery"
              "aws:ResourceTag/Purpose"   = "restore-drill"
            }
          }
        },
        {
          Sid      = "RDSReconciliationDeleteManagedSnapshots"
          Effect   = "Allow"
          Action   = ["rds:DeleteDBSnapshot"]
          Resource = local.rds_managed_snapshots
          Condition = {
            StringEquals = { "aws:ResourceTag/ManagedBy" = "LuminaHostedRecovery" }
          }
        },
      ]
    })
  }
  tags = var.tags
}
