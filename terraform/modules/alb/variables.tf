variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "acm_certificate_arn" {
  type = string
}

variable "route53_zone_id" {
  type    = string
  default = ""
}

variable "api_origin_domain_name" {
  description = "Certificate-covered API origin hostname that resolves to the ALB."
  type        = string

  validation {
    condition     = length(var.api_origin_domain_name) <= 253 && can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$", var.api_origin_domain_name))
    error_message = "api_origin_domain_name must be a lowercase fully qualified hostname with valid DNS labels."
  }
}

variable "environment" {
  type = string
}

variable "tags" {
  type = map(string)
}

locals {
  tg_prefix = substr(replace(var.name_prefix, "-", ""), 0, 6)
}
