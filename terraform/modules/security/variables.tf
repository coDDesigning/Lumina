variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "alb_security_group_id" {
  type = string
}

variable "tags" {
  type = map(string)
}
