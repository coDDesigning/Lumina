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

output "hosted_restore_task_definition_family" {
  description = "Read-only task definition family used for hosted restore verification."
  value       = module.ecs.hosted_restore_task_definition_family
}

output "alb_dns_name" {
  description = "DNS name of the application load balancer."
  value       = module.alb.dns_name
}

output "aws_region" {
  description = "AWS region containing the regional Lumina resources."
  value       = var.region
}

output "private_subnet_ids" {
  description = "Private subnet ids used by hosted ECS tasks."
  value       = module.vpc.private_subnet_ids
}

output "private_subnet_ids_csv" {
  description = "Private subnet ids formatted for the deploy workflow's PRIVATE_SUBNETS variable."
  value       = join(",", module.vpc.private_subnet_ids)
}

output "frontend_bucket_name" {
  description = "Private S3 bucket containing frontend current and release prefixes."
  value       = module.frontend.bucket_name
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution invalidated by frontend deployments."
  value       = module.frontend.distribution_id
}

output "cloudfront_distribution_domain_name" {
  description = "AWS-assigned CloudFront distribution hostname."
  value       = module.frontend.distribution_domain_name
}

output "cloudfront_url" {
  description = "CloudFront URL used to verify frontend delivery before the public DNS cutover."
  value       = module.frontend.cloudfront_url
}

output "frontend_domain_name" {
  description = "Public frontend hostname."
  value       = module.frontend.frontend_domain_name
}

output "frontend_url" {
  description = "Public HTTPS URL for the frontend."
  value       = "https://${module.frontend.frontend_domain_name}"
}

output "api_origin_domain_name" {
  description = "Certificate-valid API origin hostname used by CloudFront."
  value       = module.alb.api_origin_domain_name
}

output "s3_bucket" {
  description = "Name of the document storage bucket."
  value       = module.s3.bucket
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN holding the runtime DATABASE_URL through RDS Proxy."
  value       = module.rds_proxy.runtime_database_url_secret_arn
}

output "migration_database_url_secret_arn" {
  description = "Secrets Manager ARN holding the direct RDS URL used only by migrations."
  value       = module.rds.database_url_secret_arn
}

output "database_endpoint" {
  description = "RDS endpoint."
  value       = module.rds.endpoint
}

output "rds_instance_identifier" {
  description = "Identifier of the production RDS instance."
  value       = module.rds.instance_identifier
}

output "rds_subnet_group_name" {
  description = "DB subnet group used by the production RDS instance and temporary recovery restores."
  value       = module.rds.subnet_group_name
}

output "rds_security_group_id" {
  description = "Isolated security group used only by temporary recovery restore databases."
  value       = module.security.restore_database_security_group_id
}

output "restore_verifier_security_group_id" {
  description = "Isolated security group used only by hosted restore verifier tasks."
  value       = module.security.restore_verifier_security_group_id
}

output "rds_parameter_group_name" {
  description = "Production RDS parameter group applied to temporary recovery restores."
  value       = module.rds.parameter_group_name
}

output "rds_option_group_name" {
  description = "Production RDS option group applied to temporary recovery restores."
  value       = module.rds.option_group_name
}

output "rds_proxy_endpoint" {
  description = "TLS-only RDS Proxy endpoint used by API and worker tasks."
  value       = module.rds_proxy.endpoint
}

output "github_actions_role_arn" {
  description = "IAM role the deploy workflow assumes via GitHub OIDC. Set as AWS_DEPLOY_ROLE_ARN on the production environment."
  value       = module.github_oidc.role_arn
}

output "github_recovery_role_arn" {
  description = "Least-privilege IAM role assumed by hosted recovery workflows."
  value       = module.github_oidc.recovery_role_arn
}

output "github_reconciler_role_arn" {
  description = "Deletion-only IAM role assumed by unattended hosted recovery reconciliation."
  value       = module.github_oidc.reconciler_role_arn
}

output "runtime_ssm_parameters" {
  description = "SSM parameter names created from runtime_secrets."
  value       = module.secrets.parameter_names
}

output "alarm_topic_arn" {
  description = "SNS topic receiving production alarm and recovery notifications."
  value       = module.observability.alarm_topic_arn
}

output "operations_dashboard_name" {
  description = "CloudWatch operations dashboard."
  value       = module.observability.dashboard_name
}
