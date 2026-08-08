resource "random_password" "database" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
  tags       = { Name = local.name }
}

# pgvector ships with RDS Postgres 15.2+; no custom extension packaging needed.
# It still has to be enabled per-database, which `lexground init-db` does.
resource "aws_db_parameter_group" "this" {
  name_prefix = "${local.name}-"
  family      = "postgres16"

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
    # Static parameters only take effect after a reboot, so changing this
    # replaces the group and requires an instance restart.
    apply_method = "pending-reboot"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "this" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16.4"

  instance_class        = var.db_instance_class
  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 4
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "lexground"
  username = "lexground"
  password = random_password.database.result
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.database.id]
  parameter_group_name   = aws_db_parameter_group.this.name
  publicly_accessible    = false

  multi_az                = var.environment == "prod"
  backup_retention_period = 7
  backup_window           = "02:00-03:00"
  maintenance_window      = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot   = true

  deletion_protection       = var.db_deletion_protection
  skip_final_snapshot       = !var.db_deletion_protection
  final_snapshot_identifier = var.db_deletion_protection ? "${local.name}-final" : null

  performance_insights_enabled    = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  auto_minor_version_upgrade      = true

  tags = { Name = local.name }
}

resource "aws_secretsmanager_secret" "database_url" {
  name_prefix = "${local.name}-database-url-"
  description = "SQLAlchemy async DSN for the Lexground index"
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql+asyncpg://%s:%s@%s:%d/%s",
    aws_db_instance.this.username,
    random_password.database.result,
    aws_db_instance.this.address,
    aws_db_instance.this.port,
    aws_db_instance.this.db_name,
  )
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  count = var.anthropic_api_key == "" ? 0 : 1

  name_prefix = "${local.name}-anthropic-key-"
  description = "Provider API key for answer synthesis and the groundedness judge"
}

resource "aws_secretsmanager_secret_version" "anthropic_api_key" {
  count = var.anthropic_api_key == "" ? 0 : 1

  secret_id     = aws_secretsmanager_secret.anthropic_api_key[0].id
  secret_string = var.anthropic_api_key
}
