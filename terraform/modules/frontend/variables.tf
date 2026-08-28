variable "name_prefix" {
  type = string
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name for frontend release artifacts."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be a valid lowercase S3 bucket name."
  }
}

variable "cloudfront_certificate_arn" {
  description = "Existing ACM certificate ARN for the frontend domain. CloudFront requires the certificate in us-east-1."
  type        = string

  validation {
    condition     = can(regex("^arn:aws[a-zA-Z-]*:acm:us-east-1:[0-9]{12}:certificate/[0-9a-fA-F-]+$", var.cloudfront_certificate_arn))
    error_message = "cloudfront_certificate_arn must be an ACM certificate ARN in us-east-1."
  }
}

variable "frontend_domain_name" {
  description = "Certificate-covered public hostname served by CloudFront."
  type        = string

  validation {
    condition     = length(var.frontend_domain_name) <= 253 && can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.frontend_domain_name))
    error_message = "frontend_domain_name must be a lowercase fully qualified hostname with valid DNS labels."
  }
}

variable "frontend_dns_record_name" {
  description = "Full Route53 frontend record name retained from the legacy ALB alias during state migration. Ignored when Route53 is external."
  type        = string

  validation {
    condition = (
      var.route53_zone_id == "" ||
      trimsuffix(lower(var.frontend_dns_record_name), ".") == trimsuffix(lower(var.frontend_domain_name), ".")
    )
    error_message = "Managed frontend_dns_record_name must be the full frontend_domain_name."
  }
}

variable "api_origin_domain_name" {
  description = "Certificate-covered hostname resolving to the API ALB."
  type        = string

  validation {
    condition = (
      length(var.api_origin_domain_name) <= 253 &&
      can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.api_origin_domain_name)) &&
      lower(var.api_origin_domain_name) != lower(var.frontend_domain_name)
    )
    error_message = "api_origin_domain_name must be a lowercase fully qualified hostname with valid DNS labels and distinct from frontend_domain_name."
  }
}

variable "frontend_dns_cutover" {
  description = "Whether the managed frontend DNS aliases target CloudFront instead of retaining the existing ALB target."
  type        = bool
  default     = false
}

variable "alb_dns_name" {
  description = "Existing ALB DNS name retained until frontend_dns_cutover is enabled."
  type        = string
}

variable "alb_zone_id" {
  description = "Existing ALB Route53 zone id retained until frontend_dns_cutover is enabled."
  type        = string
}

variable "route53_zone_id" {
  description = "Optional Route53 hosted zone id for frontend A and AAAA aliases. Empty leaves DNS external."
  type        = string
  default     = ""

  validation {
    condition     = var.route53_zone_id == "" || can(regex("^Z[A-Z0-9]+$", var.route53_zone_id))
    error_message = "route53_zone_id must be empty or a Route53 hosted zone id beginning with Z."
  }
}

variable "additional_connect_src" {
  description = "Exact extra origins the SPA is permitted to call, such as a separately hosted API host during a cutover. The distribution's own origin is always allowed; entries here must be complete https origins, never scheme wildcards."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for origin in var.additional_connect_src :
      can(regex("^https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+(?::[0-9]{1,5})?$", origin))
    ])
    error_message = "Each additional_connect_src entry must be a complete lowercase https origin such as https://api.example.com, with no path, no wildcard, and no bare scheme."
  }
}

variable "tags" {
  type    = map(string)
  default = {}
}
