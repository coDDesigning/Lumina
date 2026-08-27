resource "aws_security_group" "this" {
  name_prefix = "${var.name_prefix}-ecs"
  vpc_id      = var.vpc_id
  description = "Lumina ECS tasks and RDS Proxy"
  tags        = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "ingress_http" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.this.id
  source_security_group_id = var.alb_security_group_id
}

resource "aws_security_group_rule" "ingress_postgres_self" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.this.id
  source_security_group_id = aws_security_group.this.id
}

resource "aws_security_group_rule" "egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.this.id
}

resource "aws_security_group" "restore_verifier" {
  name_prefix = "${var.name_prefix}-restore-verifier"
  vpc_id      = var.vpc_id
  description = "Isolated hosted restore verifier tasks"
  tags        = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "restore_verifier_egress" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.restore_verifier.id
}

resource "aws_security_group" "restore_database" {
  name_prefix = "${var.name_prefix}-restore-database"
  vpc_id      = var.vpc_id
  description = "Isolated hosted restore database targets"
  tags        = var.tags
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_security_group_rule" "restore_database_ingress" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.restore_database.id
  source_security_group_id = aws_security_group.restore_verifier.id
}
