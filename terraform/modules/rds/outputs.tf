output "database_url_secret_arn" {
  value = aws_secretsmanager_secret.database_url.arn
}

output "database_url_secret_name" {
  value = aws_secretsmanager_secret.database_url.name
}

output "endpoint" {
  value = aws_db_instance.this.endpoint
}

output "security_group_id" {
  value = aws_security_group.this.id
}

output "credentials_secret_arn" {
  value = aws_secretsmanager_secret.credentials.arn
}

output "instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "database_name" {
  value = var.database_name
}

output "username" {
  value = var.username
}

output "password" {
  value     = random_password.master.result
  sensitive = true
}
