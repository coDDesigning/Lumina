terraform {
  backend "s3" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = local.tags
  }
}

locals {
  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
  name_prefix          = "${var.project}-${var.environment}"
  frontend_bucket_name = var.frontend_bucket_name == "" ? "${var.project}-${var.environment}-frontend-${data.aws_caller_identity.current.account_id}" : var.frontend_bucket_name
}

data "aws_caller_identity" "current" {}

module "vpc" {
  source      = "./modules/vpc"
  name_prefix = local.name_prefix
  vpc_cidr    = var.vpc_cidr
  azs         = var.availability_zones
  tags        = local.tags
}

module "ecr" {
  source = "./modules/ecr"
  name   = var.project
  tags   = local.tags
}

module "s3" {
  source      = "./modules/s3"
  bucket      = var.s3_bucket_name
  name_prefix = local.name_prefix
  tags        = local.tags
}

module "alb" {
  source                 = "./modules/alb"
  name_prefix            = local.name_prefix
  vpc_id                 = module.vpc.vpc_id
  public_subnet_ids      = module.vpc.public_subnet_ids
  acm_certificate_arn    = var.acm_certificate_arn
  route53_zone_id        = var.route53_zone_id
  api_origin_domain_name = var.api_origin_domain_name
  environment            = var.environment
  tags                   = local.tags
}

module "frontend" {
  source                     = "./modules/frontend"
  name_prefix                = local.name_prefix
  bucket_name                = local.frontend_bucket_name
  cloudfront_certificate_arn = var.cloudfront_certificate_arn
  frontend_domain_name       = var.frontend_domain_name
  frontend_dns_record_name   = var.dns_record_name == "" ? var.frontend_domain_name : var.dns_record_name
  api_origin_domain_name     = module.alb.api_origin_domain_name
  frontend_dns_cutover       = var.frontend_dns_cutover
  alb_dns_name               = module.alb.dns_name
  alb_zone_id                = module.alb.zone_id
  route53_zone_id            = var.route53_zone_id
  tags                       = local.tags
}

module "security" {
  source                = "./modules/security"
  name_prefix           = local.name_prefix
  vpc_id                = module.vpc.vpc_id
  alb_security_group_id = module.alb.security_group_id
  tags                  = local.tags
}

module "rds" {
  source                   = "./modules/rds"
  name_prefix              = local.name_prefix
  vpc_id                   = module.vpc.vpc_id
  subnet_ids               = module.vpc.private_subnet_ids
  ecs_security_group_id    = module.security.security_group_id
  instance_class           = var.rds_instance_class
  allocated_storage_gb     = var.rds_allocated_storage_gb
  max_allocated_storage_gb = var.rds_max_allocated_storage_gb
  multi_az                 = var.rds_multi_az
  engine_version           = var.rds_engine_version
  database_name            = var.rds_database_name
  username                 = var.rds_username
  tags                     = local.tags
}

module "rds_proxy" {
  source                       = "./modules/rds-proxy"
  name_prefix                  = local.name_prefix
  subnet_ids                   = module.vpc.private_subnet_ids
  security_group_ids           = [module.security.security_group_id]
  credentials_secret_arn       = module.rds.credentials_secret_arn
  db_instance_identifier       = module.rds.instance_identifier
  database_name                = module.rds.database_name
  username                     = module.rds.username
  password                     = module.rds.password
  max_connections_percent      = var.rds_proxy_max_connections_percent
  max_idle_connections_percent = var.rds_proxy_max_idle_connections_percent
  tags                         = local.tags
}

module "ecs" {
  source                            = "./modules/ecs"
  name_prefix                       = local.name_prefix
  environment                       = var.environment
  region                            = var.region
  ecr_repository_url                = module.ecr.repository_url
  image_tag                         = var.image_tag
  private_subnet_ids                = module.vpc.private_subnet_ids
  ecs_security_group_id             = module.security.security_group_id
  alb_target_group_arn              = module.alb.target_group_arn
  s3_bucket                         = module.s3.bucket
  s3_bucket_arn                     = module.s3.arn
  runtime_database_url_secret_arn   = module.rds_proxy.runtime_database_url_secret_arn
  migration_database_url_secret_arn = module.rds.database_url_secret_arn
  bootstrap_admin_email             = var.bootstrap_admin_email
  ai_model_cost_rates               = var.ai_model_cost_rates
  cors_allowed_origins              = var.cors_allowed_origins
  api_cpu                           = var.api_cpu
  api_memory                        = var.api_memory
  api_min_instances                 = var.api_min_instances
  api_max_instances                 = var.api_max_instances
  worker_cpu                        = var.worker_cpu
  worker_memory                     = var.worker_memory
  worker_min_instances              = var.worker_min_instances
  worker_max_instances              = var.worker_max_instances
  worker_target_queue_age_seconds   = var.worker_target_queue_age_seconds
  migrate_cpu                       = var.migrate_cpu
  migrate_memory                    = var.migrate_memory
  tmpfs_size_bytes                  = var.tmpfs_size_bytes
  tags                              = local.tags
}

module "secrets" {
  source         = "./modules/secrets"
  name_prefix    = local.name_prefix
  ssm_parameters = var.runtime_secrets
  tags           = local.tags
}

module "github_oidc" {
  source                      = "./modules/github-oidc"
  name_prefix                 = local.name_prefix
  repository                  = var.github_repository
  environment_name            = var.github_environment_name
  ecr_repository_arn          = module.ecr.arn
  frontend_bucket_arn         = module.frontend.bucket_arn
  cloudfront_distribution_arn = module.frontend.distribution_arn
  ecs_cluster_name            = module.ecs.cluster_name
  api_service_name            = module.ecs.api_service_name
  worker_service_name         = module.ecs.worker_service_name
  task_definition_families = [
    module.ecs.api_task_definition_family,
    module.ecs.worker_task_definition_family,
    module.ecs.migrate_task_definition_family,
  ]
  ecs_task_role_arn      = module.ecs.task_role_arn
  ecs_execution_role_arn = module.ecs.execution_role_arn
  tags                   = local.tags
}

module "observability" {
  source                  = "./modules/observability"
  name_prefix             = local.name_prefix
  environment             = var.environment
  alarm_email             = var.alarm_email
  alb_arn_suffix          = module.alb.arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  ecs_cluster_name        = module.ecs.cluster_name
  api_service_name        = module.ecs.api_service_name
  worker_service_name     = module.ecs.worker_service_name
  rds_instance_identifier = module.rds.instance_identifier
  rds_proxy_name          = module.rds_proxy.name
  tags                    = local.tags
}

moved {
  from = module.ecs.aws_security_group.this
  to   = module.security.aws_security_group.this
}

moved {
  from = module.alb.aws_route53_record.app[0]
  to   = module.frontend.aws_route53_record.frontend_a[0]
}

moved {
  from = module.ecs.aws_security_group_rule.ingress_http
  to   = module.security.aws_security_group_rule.ingress_http
}

moved {
  from = module.ecs.aws_security_group_rule.egress_all
  to   = module.security.aws_security_group_rule.egress_all
}
