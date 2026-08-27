output "security_group_id" {
  value = aws_security_group.this.id
}

output "restore_verifier_security_group_id" {
  value = aws_security_group.restore_verifier.id
}

output "restore_database_security_group_id" {
  value = aws_security_group.restore_database.id
}
