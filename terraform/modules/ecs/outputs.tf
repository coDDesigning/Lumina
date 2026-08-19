output "ecs_security_group_id" {
  value = aws_security_group.this.id
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "api_service_name" {
  value = aws_ecs_service.api.name
}

output "worker_service_name" {
  value = aws_ecs_service.worker.name
}

output "api_task_definition_family" {
  value = aws_ecs_task_definition.api.family
}

output "worker_task_definition_family" {
  value = aws_ecs_task_definition.worker.family
}

output "migrate_task_definition_family" {
  value = aws_ecs_task_definition.migrate.family
}

output "ssm_paths" {
  description = "SSM parameter paths the ECS tasks read at start. Created by the secrets module."
  value       = local.ssm_paths
}