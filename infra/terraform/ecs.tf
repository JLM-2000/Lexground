resource "aws_ecr_repository" "api" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 20 images; older tags are unreachable rollback targets anyway."
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

data "aws_iam_policy_document" "task_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.task_assume_role.json
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = compact([
      aws_secretsmanager_secret.database_url.arn,
      try(aws_secretsmanager_secret.anthropic_api_key[0].arn, ""),
    ])
  }
}

resource "aws_iam_role_policy" "read_secrets" {
  name   = "${local.name}-read-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.task_assume_role.json
}

locals {
  api_secrets = concat(
    [{ name = "LEXGROUND_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn }],
    var.anthropic_api_key == "" ? [] : [{
      name      = "LEXGROUND_ANTHROPIC_API_KEY"
      valueFrom = aws_secretsmanager_secret.anthropic_api_key[0].arn
    }],
  )
}

resource "aws_ecs_task_definition" "api" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name      = "api"
    image     = var.api_image
    essential = true

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    environment = [
      { name = "LEXGROUND_EMBEDDING_BACKEND", value = "fastembed" },
    ]

    secrets = local.api_secrets

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request,sys,json; r=json.load(urllib.request.urlopen('http://localhost:8000/health/ready')); sys.exit(0 if r['status']=='ok' else 1)\""]
      interval    = 15
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.api.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_ecs_service" "api" {
  name            = local.name
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 90

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_lb_listener.http]
}
