variable "name_prefix" {
  type = string
}

variable "ssm_parameters" {
  description = "Runtime secrets as SSM SecureString parameters. Keys must match the ECS task definition references (jwt-secret-key, bootstrap-admin-token, gemini-api-key). Values come from terraform.tfvars; never commit them."
  type        = map(string)
  default     = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}