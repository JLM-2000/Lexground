output "api_url" {
  description = "Base URL of the deployed API."
  value       = "http://${aws_lb.this.dns_name}"
}

output "ecr_repository_url" {
  description = "Push target for API images."
  value       = aws_ecr_repository.api.repository_url
}

output "ecs_cluster_name" {
  description = "Cluster hosting the API service."
  value       = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  description = "Service to update when deploying a new image tag."
  value       = aws_ecs_service.api.name
}

output "database_endpoint" {
  description = "RDS endpoint. Reachable only from the API security group."
  value       = aws_db_instance.this.address
}

output "database_url_secret_arn" {
  description = "Secrets Manager ARN holding the database DSN."
  value       = aws_secretsmanager_secret.database_url.arn
}

output "log_group" {
  description = "CloudWatch log group for API tasks."
  value       = aws_cloudwatch_log_group.api.name
}
