resource "aws_db_subnet_group" "this" {
  name       = var.name_prefix
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

resource "aws_security_group" "this" {
  name_prefix = "${var.name_prefix}-rds"
  vpc_id      = var.vpc_id
  description = "Lumina RDS PostgreSQL: allow PostgreSQL from ECS tasks only"
  tags        = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "ingress_postgres" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.this.id
  source_security_group_id = var.ecs_security_group_id
}

resource "aws_security_group_rule" "egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.this.id
}

resource "aws_db_parameter_group" "this" {
  name_prefix = "${var.name_prefix}-pg16"
  family      = "postgres16"
  description = "Lumina PostgreSQL runtime and observability settings"
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }
  parameter {
    name  = "idle_in_transaction_session_timeout"
    value = "60000"
  }
  parameter {
    name  = "work_mem"
    value = "8192"
  }
  parameter {
    name  = "maintenance_work_mem"
    value = "131072"
  }
  parameter {
    name  = "autovacuum_naptime"
    value = "30"
  }
  tags = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "random_password" "master" {
  length  = 32
  special = false
}

resource "aws_db_instance" "this" {
  identifier                      = var.name_prefix
  engine                          = "postgres"
  engine_version                  = var.engine_version
  instance_class                  = var.instance_class
  allocated_storage               = var.allocated_storage_gb
  max_allocated_storage           = var.max_allocated_storage_gb
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = var.database_name
  username                        = var.username
  password                        = random_password.master.result
  parameter_group_name            = aws_db_parameter_group.this.name
  db_subnet_group_name            = aws_db_subnet_group.this.name
  vpc_security_group_ids          = [aws_security_group.this.id]
  multi_az                        = var.multi_az
  backup_retention_period         = 7
  backup_window                   = "03:00-04:00"
  maintenance_window              = "sun:05:00-sun:06:00"
  deletion_protection             = true
  skip_final_snapshot             = false
  copy_tags_to_snapshot           = true
  auto_minor_version_upgrade      = true
  performance_insights_enabled    = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  tags                            = var.tags
}

resource "aws_secretsmanager_secret" "database_url" {
  name        = "${var.name_prefix}/database-url"
  description = "Direct SQLAlchemy DATABASE_URL for migration tasks"
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id     = aws_secretsmanager_secret.database_url.id
  secret_string = "postgresql+psycopg://${var.username}:${random_password.master.result}@${aws_db_instance.this.endpoint}/${var.database_name}?sslmode=require"
}

resource "aws_secretsmanager_secret" "credentials" {
  name        = "${var.name_prefix}/database-credentials"
  description = "Credentials consumed only by RDS Proxy"
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "credentials" {
  secret_id = aws_secretsmanager_secret.credentials.id
  secret_string = jsonencode({
    username = var.username
    password = random_password.master.result
  })
}
