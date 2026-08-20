variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "ecs_security_group_id" {
  type = string
}

variable "instance_class" {
  type = string
}

variable "allocated_storage_gb" {
  type = number
}

variable "multi_az" {
  type = bool
}

variable "engine_version" {
  type = string
}

variable "database_name" {
  type = string
}

variable "username" {
  type = string
}

variable "tags" {
  type = map(string)
}