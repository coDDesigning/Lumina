mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }
}

variables {
  name_prefix                       = "lumina-production"
  environment                       = "production"
  region                            = "us-east-1"
  ecr_repository_url                = "123456789012.dkr.ecr.us-east-1.amazonaws.com/lumina"
  image_tag                         = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  private_subnet_ids                = ["subnet-11111111111111111"]
  ecs_security_group_id             = "sg-11111111111111111"
  alb_target_group_arn              = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/lumina/1234567890abcdef"
  s3_bucket                         = "lumina-production-documents"
  s3_bucket_arn                     = "arn:aws:s3:::lumina-production-documents"
  runtime_database_url_secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:runtime"
  migration_database_url_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:migration"
  bootstrap_admin_email             = "admin@example.com"
  ai_model_cost_rates               = "{}"
  api_cpu                           = 512
  api_memory                        = 1024
  api_min_instances                 = 1
  api_max_instances                 = 2
  worker_cpu                        = 512
  worker_memory                     = 1024
  worker_min_instances              = 1
  worker_max_instances              = 2
  worker_target_queue_age_seconds   = 60
  migrate_cpu                       = 512
  migrate_memory                    = 1024
  tmpfs_size_bytes                  = 64
  frontend_domain_name              = "app.example.com"
  email_from_address                = "no-reply@example.com"
  smtp_host                         = "smtp.example.com"
  tags                              = { Environment = "production" }
}

run "read_only_restore_verifier" {
  command = plan

  module {
    source = "./modules/ecs"
  }

  assert {
    condition = toset(flatten([
      for statement in jsondecode(one(aws_iam_role.restore_task.inline_policy).policy).Statement : statement.Action
    ])) == toset(["s3:GetObject"])
    error_message = "The restore verifier task role must be read-only for document objects."
  }

  assert {
    condition = one([
      for statement in jsondecode(one(aws_iam_role.restore_execution.inline_policy).policy).Statement : statement.Resource
    ]) == ["arn:aws:secretsmanager:us-east-1:123456789012:secret:migration"]
    error_message = "The restore execution role may read only the migration database URL secret."
  }

  assert {
    condition = toset([
      for secret in jsondecode(aws_ecs_task_definition.hosted_restore.container_definitions)[0].secrets : secret.name
    ]) == toset(["DATABASE_URL"])
    error_message = "The hosted restore container must receive no application secrets."
  }
}

run "hosted_tasks_can_deliver_verification_links" {
  command = plan

  module {
    source = "./modules/ecs"
  }

  assert {
    condition = alltrue([
      for definition in [
        aws_ecs_task_definition.api,
        aws_ecs_task_definition.worker,
        aws_ecs_task_definition.migrate,
        aws_ecs_task_definition.hosted_restore,
      ] :
      length([
        for variable in jsondecode(definition.container_definitions)[0].environment : variable
        if variable.name == "SMTP_HOST" && variable.value != ""
      ]) == 1
    ])
    error_message = "Every hosted task loads the same configuration module, so all of them need the mail settings to start."
  }

  assert {
    condition = one([
      for variable in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment : variable.value
      if variable.name == "APP_PUBLIC_BASE_URL"
    ]) == "https://app.example.com"
    error_message = "Verification links must point at the browser-facing frontend host."
  }

  assert {
    condition = one([
      for variable in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment : variable.value
      if variable.name == "EMAIL_VERIFICATION_REQUIRED"
    ]) == "true"
    error_message = "Hosted deployments must verify an address before granting credits."
  }

  assert {
    condition = length([
      for secret in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].secrets : secret
      if secret.name == "SMTP_PASSWORD"
    ]) == 0
    error_message = "A relay with no login must not make task definitions depend on an SMTP password parameter."
  }
}
