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

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

mock_provider "random" {}

variables {
  bootstrap_admin_email      = "admin@example.com"
  acm_certificate_arn        = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555"
  frontend_domain_name       = "app.example.com"
  api_origin_domain_name     = "api-origin.example.com"
  cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555"
  frontend_bucket_name       = "lumina-production-frontend-123456789012"
  s3_bucket_name             = "lumina-production-documents-123456789012"
}

run "root_recovery_outputs_and_wiring" {
  command = plan

  override_module {
    target = module.rds
    outputs = {
      credentials_secret_arn  = "arn:aws:secretsmanager:us-east-1:123456789012:secret:lumina-production/database-credentials"
      database_name           = "lumina"
      database_url_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:lumina-production/database-url"
      endpoint                = "lumina-production.example.us-east-1.rds.amazonaws.com:5432"
      instance_identifier     = "lumina-production"
      password                = "mock-password"
      parameter_group_name    = "lumina-production-pg16"
      option_group_name       = "default:postgres-16"
      security_group_id       = "sg-0123456789abcdef0"
      subnet_group_name       = "lumina-production"
      username                = "lumina"
    }
  }

  override_module {
    target = module.secrets
    outputs = {
      parameter_names = []
    }
  }

  override_module {
    target = module.security
    outputs = {
      security_group_id                  = "sg-0123456789abcdef0"
      restore_database_security_group_id = "sg-11111111111111111"
      restore_verifier_security_group_id = "sg-22222222222222222"
    }
  }

  assert {
    condition     = output.rds_instance_identifier == module.rds.instance_identifier && output.rds_instance_identifier == "lumina-production"
    error_message = "The root RDS identifier output must come directly from the RDS module."
  }

  assert {
    condition     = output.rds_subnet_group_name == module.rds.subnet_group_name && output.rds_subnet_group_name == "lumina-production"
    error_message = "The root DB subnet group output must come directly from the RDS module."
  }

  assert {
    condition     = output.rds_security_group_id == module.security.restore_database_security_group_id && output.rds_security_group_id == "sg-11111111111111111"
    error_message = "Restore databases must use the isolated recovery security group."
  }

  assert {
    condition     = output.restore_verifier_security_group_id == module.security.restore_verifier_security_group_id && output.restore_verifier_security_group_id == "sg-22222222222222222"
    error_message = "Restore verifier tasks must use the isolated recovery security group."
  }

  assert {
    condition     = output.rds_parameter_group_name == module.rds.parameter_group_name && output.rds_parameter_group_name == "lumina-production-pg16"
    error_message = "The root RDS parameter group output must come directly from the RDS module."
  }

  assert {
    condition     = output.rds_option_group_name == module.rds.option_group_name && output.rds_option_group_name == "default:postgres-16"
    error_message = "The root RDS option group output must come directly from the RDS module."
  }
}
