output "endpoint" {
  value = aws_db_proxy.this.endpoint
}

output "name" {
  value = aws_db_proxy.this.name
}

output "runtime_database_url_secret_arn" {
  value = aws_secretsmanager_secret.runtime_database_url.arn
}
