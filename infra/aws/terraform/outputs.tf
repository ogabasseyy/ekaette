output "alb_dns_name" {
  description = "Public ALB DNS endpoint"
  value       = aws_lb.app.dns_name
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = aws_ecs_cluster.app.name
}

output "ecs_service_name" {
  description = "ECS service name"
  value       = aws_ecs_service.app.name
}

output "ecr_repository_url" {
  description = "ECR repository URL"
  value       = aws_ecr_repository.app.repository_url
}

output "dynamodb_tables" {
  description = "Runtime DynamoDB tables"
  value = {
    sessions = aws_dynamodb_table.sessions.name
    registry = aws_dynamodb_table.registry.name
    calls    = aws_dynamodb_table.calls.name
  }
}

output "s3_media_bucket" {
  description = "Media artifact S3 bucket"
  value       = aws_s3_bucket.media.bucket
}

