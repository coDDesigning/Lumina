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

variable "rds_multi_az" {
  description = "Use a Multi-AZ RDS deployment."
  type        = bool
  default     = false
}

variable "rds_engine_version" {
  description = "PostgreSQL engine version. 16.3 or newer bundles the pgvector extension."
  type        = string
  default     = "16.3"
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
  description = "Route53 hosted zone id for the application DNS alias. Empty disables the record."
  type        = string
  default     = ""
}

variable "dns_record_name" {
  description = "DNS record created under route53_zone_id, e.g. 'app' for app.example.com."
  type        = string
  default     = "app"
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

variable "runtime_secrets" {
  description = "Runtime secrets as SSM SecureString parameters under /<project>-<environment>/. Keys must match the ECS task definition references: jwt-secret-key, bootstrap-admin-token, gemini-api-key. Supply values through terraform.tfvars; never commit them."
  type        = map(string)
  default     = {}
  sensitive   = true
}