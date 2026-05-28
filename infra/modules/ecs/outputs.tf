output "dashboard_url" {
  description = "ALB DNS name — use this as SENTINEL_DASHBOARD_URL for the CloudWatch alarm receiver Lambda."
  value       = "http://${aws_lb.dashboard.dns_name}"
}

output "alb_dns_name" {
  value = aws_lb.dashboard.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL. Push your image here before terraform apply."
  value       = aws_ecr_repository.dashboard.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.sentinel.name
}

output "ecs_service_name" {
  value = aws_ecs_service.dashboard.name
}
