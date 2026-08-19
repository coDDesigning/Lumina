output "parameter_names" {
  value = [for p in aws_ssm_parameter.this : p.name]
}

output "parameter_arns" {
  value = { for name, p in aws_ssm_parameter.this : name => p.arn }
}