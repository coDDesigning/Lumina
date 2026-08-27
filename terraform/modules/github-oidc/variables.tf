variable "name_prefix" {
  type = string
}

variable "repository" {
  description = "GitHub repository that assumes the deploy role, e.g. coDDesigning/Lumina."
  type        = string
}

variable "environment_name" {
  description = "GitHub environment whose jobs may assume the deploy role."
  type        = string
}

variable "recovery_environment_name" {
  description = "Unattended GitHub environment whose scheduled jobs may assume the recovery role."
  type        = string
  default     = "production-recovery"
}

variable "ecr_repository_arn" {
  type = string
}

variable "frontend_bucket_arn" {
  type = string
}

variable "cloudfront_distribution_arn" {
  type = string
}

variable "ecs_cluster_name" {
  type = string
}

variable "api_service_name" {
  type = string
}

variable "worker_service_name" {
  type = string
}

variable "task_definition_families" {
  type = list(string)
}

variable "ecs_task_role_arn" {
  type = string
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "restore_task_definition_family" {
  type = string
}

variable "restore_task_role_arn" {
  type = string
}

variable "restore_execution_role_arn" {
  type = string
}

variable "rds_instance_identifier" {
  type = string
}

variable "rds_subnet_group_name" {
  type = string
}

variable "rds_parameter_group_name" {
  type = string
}

variable "rds_option_group_name" {
  type = string
}

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  cluster_arn         = "arn:aws:ecs:${local.region}:${local.account_id}:cluster/${var.ecs_cluster_name}"
  api_svc_arn         = "arn:aws:ecs:${local.region}:${local.account_id}:service/${var.ecs_cluster_name}/${var.api_service_name}"
  worker_svc_arn      = "arn:aws:ecs:${local.region}:${local.account_id}:service/${var.ecs_cluster_name}/${var.worker_service_name}"
  cluster_task_arn    = "arn:aws:ecs:${local.region}:${local.account_id}:task/${var.ecs_cluster_name}/*"
  restore_taskdef_arn = "arn:aws:ecs:${local.region}:${local.account_id}:task-definition/${var.restore_task_definition_family}"

  rds_db_arn              = "arn:aws:rds:${local.region}:${local.account_id}:db:${var.rds_instance_identifier}"
  rds_restore_db_arn      = "arn:aws:rds:${local.region}:${local.account_id}:db:${var.rds_instance_identifier}-restore-*"
  rds_subnet_group_arn    = "arn:aws:rds:${local.region}:${local.account_id}:subgrp:${var.rds_subnet_group_name}"
  rds_parameter_group_arn = "arn:aws:rds:${local.region}:${local.account_id}:pg:${var.rds_parameter_group_name}"
  rds_option_group_arn    = "arn:aws:rds:${local.region}:${local.account_id}:og:${var.rds_option_group_name}"
  rds_managed_snapshots = [
    "arn:aws:rds:${local.region}:${local.account_id}:snapshot:${var.rds_instance_identifier}-predeploy-*",
    "arn:aws:rds:${local.region}:${local.account_id}:snapshot:${var.rds_instance_identifier}-drill-*",
  ]

  taskdef_arns = [
    for family in var.task_definition_families :
    "arn:aws:ecs:${local.region}:${local.account_id}:task-definition/${family}"
  ]
}
