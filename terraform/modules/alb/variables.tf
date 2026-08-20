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

variable "dns_record_name" {
  type    = string
  default = "app"
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