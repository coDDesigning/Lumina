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

variable "tags" {
  type    = map(string)
  default = {}
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = data.aws_region.current.name

  cluster_arn      = "arn:aws:ecs:${local.region}:${local.account_id}:cluster/${var.ecs_cluster_name}"
  api_svc_arn      = "arn:aws:ecs:${local.region}:${local.account_id}:service/${var.ecs_cluster_name}/${var.api_service_name}"
  worker_svc_arn   = "arn:aws:ecs:${local.region}:${local.account_id}:service/${var.ecs_cluster_name}/${var.worker_service_name}"
  cluster_task_arn = "arn:aws:ecs:${local.region}:${local.account_id}:task/${var.ecs_cluster_name}/*"

  taskdef_arns = [
    for family in var.task_definition_families :
    "arn:aws:ecs:${local.region}:${local.account_id}:task-definition/${family}"
  ]
}
