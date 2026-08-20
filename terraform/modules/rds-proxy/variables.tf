variable "name_prefix" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "credentials_secret_arn" {
  type = string
}

variable "db_instance_identifier" {
  type = string
}

variable "database_name" {
  type = string
}

variable "username" {
  type = string
}

variable "password" {
  type      = string
  sensitive = true
}

variable "max_connections_percent" {
  type = number
}

variable "max_idle_connections_percent" {
  type = number
}

variable "tags" {
  type = map(string)
}
