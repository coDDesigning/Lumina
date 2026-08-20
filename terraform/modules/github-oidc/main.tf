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
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.repository}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.name_prefix}-github-actions"
  description        = "Role assumed by the Lumina deploy workflow via GitHub OIDC"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
  inline_policy {
    name = "deploy"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Sid    = "ECR"
          Effect = "Allow"
          Action = [
            "ecr:GetAuthorizationToken",
            "ecr:BatchGetImage",
            "ecr:BatchCheckLayerAvailability",
            "ecr:GetDownloadUrlForLayer",
            "ecr:PutImage",
            "ecr:InitiateLayerUpload",
            "ecr:UploadLayerPart",
            "ecr:CompleteLayerUpload",
          ]
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
          Sid    = "ECSDeploy"
          Effect = "Allow"
          Action = [
            "ecs:DescribeServices",
            "ecs:DescribeTasks",
            "ecs:RunTask",
            "ecs:UpdateService",
          ]
          Resource = [
            local.cluster_arn,
            local.api_svc_arn,
            local.worker_svc_arn,
          ]
        },
        {
          Sid    = "ECSTaskDefinitions"
          Effect = "Allow"
          Action = [
            "ecs:DescribeTaskDefinition",
            "ecs:RunTask",
          ]
          Resource = concat(local.taskdef_arns, [for arn in local.taskdef_arns : "${arn}:*"])
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
          ]
        },
      ]
    })
  }
  tags = var.tags
}