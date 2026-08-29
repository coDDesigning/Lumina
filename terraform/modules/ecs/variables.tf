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

variable "cors_allowed_origins" {
  description = "Origins accepted by the API CORS middleware."
  type        = list(string)
  default     = []
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

variable "course_purge_interval_seconds" {
  description = "Interval in seconds between background course purge reconciliation scans."
  type        = number
  default     = 3600
}

variable "embedding_backfill_interval_seconds" {
  description = "Interval in seconds between background embedding backfill reconciliation scans."
  type        = number
  default     = 3600
}

variable "frontend_domain_name" {
  description = "Public hostname the SPA is served from. Verification links point here, because the link is opened by a browser rather than by an API client."
  type        = string
}

variable "email_verification_required" {
  description = "Whether an address must be verified before the account receives its introductory and monthly credits. Turning this off in a hosted deployment reopens credit farming; see docs/authentication.md."
  type        = bool
  default     = true
}

variable "email_from_address" {
  description = "Envelope sender for verification mail. The relay must be authorized to send as this address."
  type        = string
  default     = ""
}

variable "smtp_host" {
  description = "SMTP relay hostname used to deliver verification mail."
  type        = string
  default     = ""
}

variable "smtp_port" {
  description = "SMTP relay port. 587 is the submission port STARTTLS expects."
  type        = number
  default     = 587
}

variable "smtp_username" {
  description = "SMTP login. Empty means the relay authenticates by network identity instead, and no SMTP_PASSWORD parameter is referenced."
  type        = string
  default     = ""
}

variable "smtp_use_tls" {
  description = "Issue STARTTLS after connecting to the relay."
  type        = bool
  default     = true
}

variable "tags" {
  type = map(string)
}

variable "vpc_cidr_block" {
  description = "VPC CIDR block used as trusted proxy range for X-Forwarded-For headers."
  type        = string
}
