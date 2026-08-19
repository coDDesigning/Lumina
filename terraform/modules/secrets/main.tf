resource "aws_ssm_parameter" "this" {
  for_each = var.ssm_parameters

  name        = "/${var.name_prefix}/${each.key}"
  description = "Lumina runtime secret consumed by ECS task definitions"
  type        = "SecureString"
  value       = each.value
  tags        = var.tags
}