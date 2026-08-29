variable "name_prefix" {
  type = string
}

variable "environment" {
  type = string
}

variable "alarm_email" {
  type    = string
  default = ""
}

variable "alb_arn_suffix" {
  type = string
}

variable "target_group_arn_suffix" {
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

variable "rds_instance_identifier" {
  type = string
}

variable "rds_proxy_name" {
  type = string
}

variable "tags" {
  type = map(string)
}
