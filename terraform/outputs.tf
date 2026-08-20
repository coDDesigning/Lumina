output "ecr_repository_url" {
  description = "ECR repository for the Lumina image."
  value       = module.ecr.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name."
  value       = module.ecs.cluster_name
}

output "ecs_security_group_id" {
  description = "Security group attached to ECS tasks."
  value       = module.ecs.ecs_security_group_id
}

output "api_service_name" {
  description = "ECS service name for the API."
  value       = module.ecs.api_service_name
}

output "worker_service_name" {
  description = "ECS service name for the document processor."
  value       = module.ecs.worker_service_name
}

output "api_task_definition_family" {
  description = "Task definition family for the API. The deploy pipeline registers new revisions against it."
  value       = module.ecs.api_task_definition_family
}

output "worker_task_definition_family" {
  description = "Task definition family for the worker. The deploy pipeline registers new revisions against it."
  value       = module.ecs.worker_task_definition_family
}

output "migrate_task_definition_family" {
  description = "Task definition family for the one-off migration task run by the deploy pipeline."
  value       = module.ecs.migrate_task_definition_family
}

output "alb_dns_name" {
  description = "DNS name of the application load balancer."
  value       = module.alb.dns_name
}

output "s3_bucket" {
  description = "Name of the document storage bucket."
  value       = module.s3.bucket
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN holding the DATABASE_URL."
  value       = module.rds.database_url_secret_arn
}

output "database_endpoint" {
  description = "RDS endpoint."
  value       = module.rds.endpoint
}

output "github_actions_role_arn" {
  description = "IAM role the deploy workflow assumes via GitHub OIDC. Set as AWS_DEPLOY_ROLE_ARN on the production environment."
  value       = module.github_oidc.role_arn
}

output "runtime_ssm_parameters" {
  description = "SSM parameter names created from runtime_secrets."
  value       = module.secrets.parameter_names
}