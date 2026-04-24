output "endpoint" {
  value = aws_elasticache_cluster.sentinel.cache_nodes[0].address
}

output "port" {
  value = aws_elasticache_cluster.sentinel.port
}
