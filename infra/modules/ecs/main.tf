# Sentinel Dashboard — ECS Fargate deployment
#
# Creates:
#   ECR repository       — stores dashboard Docker image
#   ECS cluster          — Fargate launch type
#   ECS task definition  — container config, env vars, IAM roles
#   ECS service          — desired count, VPC config, ALB attachment
#   ALB + listener       — HTTP on port 80, forwards to port 8501
#   Security groups      — ALB (0.0.0.0 → 80) and ECS tasks (ALB sg → 8501)
#   IAM roles            — execution role + task role (DynamoDB, CW, Kinesis)
#   CloudWatch log group — /ecs/sentinel/dashboard

locals {
  name = "${var.project}-${var.environment}"
}

# ── ECR ───────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "dashboard" {
  name                 = "${local.name}-dashboard"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Project = var.project, Environment = var.environment }
}

resource "aws_ecr_lifecycle_policy" "dashboard" {
  repository = aws_ecr_repository.dashboard.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

# ── CloudWatch log group ───────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "dashboard" {
  name              = "/ecs/${var.project}/dashboard"
  retention_in_days = 14
  tags              = { Project = var.project, Environment = var.environment }
}

# ── IAM: task execution role (pulls image, writes task logs) ──────────────────

resource "aws_iam_role" "execution" {
  name = "${local.name}-ecs-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.project }
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ── IAM: task role (what the running app is allowed to do) ────────────────────

resource "aws_iam_role" "task" {
  name = "${local.name}-ecs-task"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = { Project = var.project }
}

resource "aws_iam_role_policy" "task" {
  name = "sentinel-dashboard-permissions"
  role = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # DynamoDB — read/write incidents, validations, PR reviews
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem",
                    "dynamodb:Query", "dynamodb:Scan", "dynamodb:DeleteItem"]
        Resource = ["arn:aws:dynamodb:*:*:table/${var.project}-*",
                    "arn:aws:dynamodb:*:*:table/${var.project}-*/index/*"]
      },
      # Kinesis — emit incident events
      {
        Effect   = "Allow"
        Action   = ["kinesis:PutRecord", "kinesis:PutRecords"]
        Resource = "arn:aws:kinesis:*:*:stream/${var.project}-*"
      },
      # CloudWatch Logs — read service logs for RCA context
      {
        Effect   = "Allow"
        Action   = ["logs:StartQuery", "logs:GetQueryResults",
                    "logs:DescribeLogGroups", "logs:DescribeLogStreams",
                    "logs:FilterLogEvents", "logs:GetLogEvents"]
        Resource = "*"
      },
      # CloudWatch metrics — read for health checks
      {
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"]
        Resource = "*"
      },
    ]
  })
}

# ── Security groups ───────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Sentinel ALB — allow HTTP inbound"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Project = var.project, Name = "${local.name}-alb" }
}

resource "aws_security_group" "tasks" {
  name        = "${local.name}-ecs-tasks"
  description = "Sentinel ECS tasks — allow inbound from ALB only"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8501
    to_port         = 8501
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Project = var.project, Name = "${local.name}-ecs-tasks" }
}

# ── ALB ───────────────────────────────────────────────────────────────────────

resource "aws_lb" "dashboard" {
  name               = "${local.name}-dashboard"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.alb_subnet_ids

  tags = { Project = var.project, Environment = var.environment }
}

resource "aws_lb_target_group" "dashboard" {
  name        = "${local.name}-dashboard"
  port        = 8501
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = { Project = var.project }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.dashboard.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.dashboard.arn
  }
}

# ── ECS cluster ───────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "sentinel" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = { Project = var.project, Environment = var.environment }
}

# ── ECS task definition ───────────────────────────────────────────────────────

resource "aws_ecs_task_definition" "dashboard" {
  family                   = "${local.name}-dashboard"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([
    {
      name      = "dashboard"
      image     = var.image_uri
      essential = true

      portMappings = [
        { containerPort = 8501, hostPort = 8501, protocol = "tcp" }
      ]

      environment = [
        { name = "CLOUDWATCH_LOG_GROUP_PREFIX", value = var.cloudwatch_log_prefix },
        { name = "INCIDENTS_TABLE",             value = var.incidents_table },
        { name = "EVENTS_STREAM",               value = var.events_stream_name },
        { name = "AWS_DEFAULT_REGION",          value = var.aws_region },
        { name = "ANTHROPIC_API_KEY",           value = var.anthropic_api_key },
        { name = "OPENAI_API_KEY",              value = var.openai_api_key },
        { name = "GEMINI_API_KEY",              value = var.gemini_api_key },
        { name = "SLACK_WEBHOOK_URL",           value = var.slack_webhook_url },
        { name = "SENTINEL_API_KEY",            value = var.sentinel_api_key },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.dashboard.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "curl -f http://localhost:8501/health || exit 1"]
        interval    = 30
        timeout     = 10
        retries     = 3
        startPeriod = 60
      }
    }
  ])

  tags = { Project = var.project, Environment = var.environment }
}

# ── ECS service ───────────────────────────────────────────────────────────────

resource "aws_ecs_service" "dashboard" {
  name            = "${local.name}-dashboard"
  cluster         = aws_ecs_cluster.sentinel.id
  task_definition = aws_ecs_task_definition.dashboard.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.ecs_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.dashboard.arn
    container_name   = "dashboard"
    container_port   = 8501
  }

  depends_on = [aws_lb_listener.http]

  # Ignore image tag changes — managed by CI/CD push
  lifecycle {
    ignore_changes = [task_definition]
  }

  tags = { Project = var.project, Environment = var.environment }
}
