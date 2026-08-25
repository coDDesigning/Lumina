variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "image_tag" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "alb_target_group_arn" {
  type = string
}

variable "s3_bucket" {
  type = string
}

variable "s3_bucket_arn" {
  type = string
}

variable "runtime_database_url_secret_arn" {
  type = string
}

variable "migration_database_url_secret_arn" {
  type = string
}

variable "bootstrap_admin_email" {
  type = string
}

variable "ai_model_cost_rates" {
  type = string
}

variable "api_cpu" {
  type = number
}

variable "api_memory" {
  type = number
}

variable "api_min_instances" {
  type = number
}

variable "api_max_instances" {
  type = number
}

variable "worker_cpu" {
  type = number
}

variable "worker_memory" {
  type = number
}

variable "worker_min_instances" {
  type = number
}

variable "worker_max_instances" {
  type = number
}

variable "worker_target_queue_age_seconds" {
  type = number
}

variable "migrate_cpu" {
  type = number
}

variable "migrate_memory" {
  type = number
}

variable "tmpfs_size_bytes" {
  type = number
}

variable "tags" {
  type = map(string)
}
