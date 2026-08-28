variable "region" {
  description = "AWS region for all Lumina resources."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment; becomes part of resource names, SSM paths, and STORAGE_NAMESPACE."
  type        = string
  default     = "production"
}

variable "project" {
  description = "Project name used for resource naming."
  type        = string
  default     = "lumina"
}

variable "vpc_cidr" {
  description = "CIDR block for the Lumina VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "Availability zones for the VPC subnets."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b", "us-east-1c"]
}

variable "bootstrap_admin_email" {
  description = "Bootstrap administrator email injected into the API container."
  type        = string
}

variable "ai_model_cost_rates" {
  description = "Versioned provider:model USD-per-million-token rates serialized as AI_MODEL_COST_RATES JSON. Empty leaves generations explicitly unpriced."
  type        = string
  default     = ""
}

variable "api_cpu" {
  description = "Fargate CPU units for the API service."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate memory (MiB) for the API service."
  type        = number
  default     = 1024
}

variable "api_min_instances" {
  description = "Minimum desired API tasks."
  type        = number
  default     = 1
}

variable "api_max_instances" {
  description = "Maximum API tasks under CPU autoscaling."
  type        = number
  default     = 4
}

variable "worker_cpu" {
  description = "Fargate CPU units for the document processor service."
  type        = number
  default     = 512
}

variable "worker_memory" {
  description = "Fargate memory (MiB) for the document processor service."
  type        = number
  default     = 1024
}

variable "worker_min_instances" {
  description = "Minimum document worker tasks. Keep at least one to publish queue metrics."
  type        = number
  default     = 1
}

variable "worker_max_instances" {
  description = "Maximum document worker tasks under queue-age autoscaling."
  type        = number
  default     = 4
}

variable "worker_target_queue_age_seconds" {
  description = "Target oldest queued-job age used by worker autoscaling."
  type        = number
  default     = 30
}

variable "migrate_cpu" {
  description = "Fargate CPU units for the one-off migration task."
  type        = number
  default     = 256
}

variable "migrate_memory" {
  description = "Fargate memory (MiB) for the one-off migration task."
  type        = number
  default     = 512
}

variable "tmpfs_size_bytes" {
  description = "Size of the /tmp tmpfs for each task, mirroring LUMINA_TMPFS_SIZE_BYTES."
  type        = number
  default     = 268435456
}

variable "rds_instance_class" {
  description = "RDS instance class for the Lumina database."
  type        = string
  default     = "db.t4g.small"
}

variable "rds_allocated_storage_gb" {
  description = "Allocated storage for the Lumina database."
  type        = number
  default     = 20
}

variable "rds_max_allocated_storage_gb" {
  description = "Maximum RDS storage autoscaling limit."
  type        = number
  default     = 100
}

variable "rds_multi_az" {
  description = "Use a Multi-AZ RDS deployment."
  type        = bool
  default     = false
}

variable "rds_engine_version" {
  description = "PostgreSQL engine version qualified with pgvector 0.8 or newer."
  type        = string
  default     = "16.8"
}

variable "rds_proxy_max_connections_percent" {
  description = "Maximum percentage of RDS connections available to RDS Proxy."
  type        = number
  default     = 80
}

variable "rds_proxy_max_idle_connections_percent" {
  description = "Maximum percentage of idle database connections retained by RDS Proxy."
  type        = number
  default     = 40
}

variable "rds_database_name" {
  description = "Database name created inside the RDS instance."
  type        = string
  default     = "lumina"
}

variable "rds_username" {
  description = "Master username for the Lumina database."
  type        = string
  default     = "lumina"
}

variable "acm_certificate_arn" {
  description = "ARN of the ACM certificate for the ALB HTTPS listener."
  type        = string
}

variable "route53_zone_id" {
  description = "Route53 hosted zone id for frontend and API-origin aliases. Empty leaves DNS external."
  type        = string
  default     = ""

  validation {
    condition     = var.route53_zone_id == "" || can(regex("^Z[A-Z0-9]+$", var.route53_zone_id))
    error_message = "route53_zone_id must be empty or a Route53 hosted zone id beginning with Z."
  }
}

variable "frontend_domain_name" {
  description = "Certificate-covered public hostname served by CloudFront."
  type        = string
}

variable "dns_record_name" {
  description = "Existing full Route53 frontend record name. Empty uses frontend_domain_name."
  type        = string
  default     = ""
}

variable "api_origin_domain_name" {
  description = "Certificate-covered hostname resolving directly to the API ALB."
  type        = string
}

variable "cloudfront_certificate_arn" {
  description = "ARN of the existing us-east-1 ACM certificate for frontend_domain_name."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-zA-Z-]*:acm:us-east-1:[0-9]{12}:certificate/[0-9a-fA-F-]+$", var.cloudfront_certificate_arn))
    error_message = "cloudfront_certificate_arn must be an ACM certificate ARN in us-east-1."
  }
}

variable "frontend_bucket_name" {
  description = "Globally unique frontend bucket name. Empty derives one from project, environment, and AWS account id."
  type        = string
  default     = ""
}

variable "frontend_dns_cutover" {
  description = "When true, move the managed frontend DNS aliases from the ALB to CloudFront after current/index.html is published."
  type        = bool
  default     = false
}

variable "frontend_additional_connect_src" {
  description = "Exact extra https origins the SPA may call, beyond the CloudFront origin that serves both it and the API. Leave empty unless a browser call genuinely leaves the distribution."
  type        = list(string)
  default     = []
}

variable "email_verification_required" {
  description = "Whether hosted accounts must prove their address before receiving credits. See docs/authentication.md. Requires email_from_address and smtp_host."
  type        = bool
  default     = true

  validation {
    condition     = !var.email_verification_required || (trimspace(var.email_from_address) != "" && trimspace(var.smtp_host) != "")
    error_message = "email_verification_required needs email_from_address and smtp_host; otherwise new accounts could never be granted credits."
  }
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
  description = "SMTP relay port."
  type        = number
  default     = 587
}

variable "smtp_username" {
  description = "SMTP login. Empty means the relay authenticates by network identity; otherwise supply the password as the smtp-password entry of runtime_secrets."
  type        = string
  default     = ""
}

variable "smtp_use_tls" {
  description = "Issue STARTTLS after connecting to the relay."
  type        = bool
  default     = true
}

variable "s3_bucket_name" {
  description = "Name of the document storage bucket. Must be globally unique."
  type        = string
  default     = "lumina-production-documents"
}

variable "image_tag" {
  description = "ECR image tag registered into the initial task definitions. The deploy pipeline registers newer revisions."
  type        = string
  default     = "latest"
}

variable "github_repository" {
  description = "GitHub repository that assumes the deploy role via OIDC."
  type        = string
  default     = "coDDesigning/Lumina"
}

variable "github_environment_name" {
  description = "GitHub environment whose deployment jobs may assume the deploy role."
  type        = string
  default     = "production"

  validation {
    condition     = length(trimspace(var.github_environment_name)) > 0 && !strcontains(var.github_environment_name, ":")
    error_message = "github_environment_name must be non-empty and must not contain a colon."
  }
}

variable "cors_allowed_origins" {
  description = "Origins accepted by the API CORS middleware. Empty disables cross-origin access."
  type        = list(string)
  default     = []
}

variable "runtime_secrets" {
  description = "Runtime secrets as SSM SecureString parameters under /<project>-<environment>/. Keys must match the ECS task definition references: jwt-secret-key, bootstrap-admin-token, gemini-api-key, openai-api-key, anthropic-api-key, and smtp-password when smtp_username is set. Supply values through terraform.tfvars; never commit them."
  type        = map(string)
  default     = {}
  sensitive   = true
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

variable "alarm_email" {
  description = "Optional email subscribed to production alarms. Confirmation is required."
  type        = string
  default     = ""
}
