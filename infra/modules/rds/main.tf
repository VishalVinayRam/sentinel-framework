resource "aws_db_subnet_group" "sentinel" {
  name       = "${var.project}-subnet-group-${var.environment}"
  subnet_ids = ["subnet-00000001", "subnet-00000002"]  # Floci mock subnets

  tags = {
    Project = var.project
  }
}

resource "aws_db_instance" "sentinel" {
  identifier        = "${var.project}-postgres-${var.environment}"
  engine            = "postgres"
  engine_version    = "15.4"
  instance_class    = "db.t3.micro"
  allocated_storage = 20

  db_name  = "sentinel"
  username = "sentinel_admin"
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.sentinel.name
  skip_final_snapshot    = true
  publicly_accessible    = false
  deletion_protection    = false

  tags = {
    Project     = var.project
    Environment = var.environment
    Purpose     = "Read model (CQRS) + pgvector for RAG knowledge base"
  }
}
