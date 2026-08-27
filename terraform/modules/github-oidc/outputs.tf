output "role_arn" {
  value = aws_iam_role.github_actions.arn
}

output "recovery_role_arn" {
  value = aws_iam_role.github_recovery.arn
}

output "reconciler_role_arn" {
  value = aws_iam_role.github_reconciler.arn
}

output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github.arn
}
