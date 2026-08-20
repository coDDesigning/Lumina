data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = "${var.name_prefix}-rds-proxy"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "secret" {
  name = "read-database-credentials"
  role = aws_iam_role.this.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.credentials_secret_arn
      }
    ]
  })
}

resource "aws_db_proxy" "this" {
  name                   = "${var.name_prefix}-proxy"
  engine_family          = "POSTGRESQL"
  role_arn               = aws_iam_role.this.arn
  vpc_subnet_ids         = var.subnet_ids
  vpc_security_group_ids = var.security_group_ids
  require_tls            = true
  idle_client_timeout    = 1800
  debug_logging          = false
  auth {
    auth_scheme = "SECRETS"
    secret_arn  = var.credentials_secret_arn
    iam_auth    = "DISABLED"
  }
  tags = var.tags
}

resource "aws_db_proxy_default_target_group" "this" {
  db_proxy_name = aws_db_proxy.this.name
  connection_pool_config {
    connection_borrow_timeout    = 120
    max_connections_percent      = var.max_connections_percent
    max_idle_connections_percent = var.max_idle_connections_percent
  }
}

resource "aws_db_proxy_target" "this" {
  db_instance_identifier = var.db_instance_identifier
  db_proxy_name          = aws_db_proxy.this.name
  target_group_name      = aws_db_proxy_default_target_group.this.name
}

resource "aws_secretsmanager_secret" "runtime_database_url" {
  name        = "${var.name_prefix}/runtime-database-url"
  description = "SQLAlchemy DATABASE_URL through RDS Proxy for API and worker tasks"
  tags        = var.tags
}

resource "aws_secretsmanager_secret_version" "runtime_database_url" {
  secret_id     = aws_secretsmanager_secret.runtime_database_url.id
  secret_string = "postgresql+psycopg://${var.username}:${var.password}@${aws_db_proxy.this.endpoint}:5432/${var.database_name}?sslmode=require"
}
