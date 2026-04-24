resource "aws_elasticache_subnet_group" "sentinel" {
  name       = "${var.project}-cache-subnet-${var.environment}"
  subnet_ids = ["subnet-00000001"]
}

resource "aws_elasticache_cluster" "sentinel" {
  cluster_id           = "${var.project}-redis-${var.environment}"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.0"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.sentinel.name

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Embedding cache (RAG hot path) + incident session state"
  }
}
