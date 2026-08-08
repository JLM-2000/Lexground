resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public entry point for the API"
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "HTTP from permitted networks"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = var.allowed_ingress_cidrs
  }

  egress {
    description = "Forward to the task subnets"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-alb" }
}

resource "aws_security_group" "api" {
  name        = "${local.name}-api"
  description = "API tasks"
  vpc_id      = aws_vpc.this.id

  egress {
    description = "Provider API and image pulls, via NAT"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name}-api" }
}

# Rules referencing another group are declared separately so the two groups can
# reference each other without a cycle in the dependency graph.
resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  description                  = "Only the load balancer may reach the tasks"
  security_group_id            = aws_security_group.api.id
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "database" {
  name        = "${local.name}-db"
  description = "Postgres, reachable only from the API tasks"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "database_from_api" {
  description                  = "Postgres from the API tasks only"
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.api.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}
