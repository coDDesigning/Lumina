mock_provider "aws" {}

variables {
  bucket      = "lumina-test-documents"
  name_prefix = "lumina-test"
  tags        = { Environment = "test" }
}

run "document_backup_contract" {
  command = plan

  module {
    source = "./modules/s3"
  }

  assert {
    condition     = aws_s3_bucket_versioning.this.versioning_configuration[0].status == "Enabled"
    error_message = "Document storage versioning must remain enabled."
  }

  assert {
    condition = one([
      for rule in aws_s3_bucket_lifecycle_configuration.this.rule : rule
      if rule.id == "noncurrent-versions"
    ]).noncurrent_version_expiration[0].noncurrent_days == 90
    error_message = "Noncurrent document versions must expire after 90 days."
  }
}
