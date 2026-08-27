mock_provider "aws" {}
mock_provider "random" {}

variables {
  name_prefix              = "lumina-test"
  vpc_id                   = "vpc-0123456789abcdef0"
  subnet_ids               = ["subnet-11111111111111111", "subnet-22222222222222222"]
  ecs_security_group_id    = "sg-11111111111111111"
  instance_class           = "db.t4g.small"
  allocated_storage_gb     = 20
  max_allocated_storage_gb = 100
  multi_az                 = false
  engine_version           = "16.8"
  database_name            = "lumina"
  username                 = "lumina"
  tags                     = { Environment = "test" }
}

run "rds_backup_contract" {
  command = plan

  module {
    source = "./modules/rds"
  }

  assert {
    condition     = aws_db_instance.this.backup_retention_period == 7
    error_message = "RDS automated backups must retain seven days of recovery points."
  }

  assert {
    condition     = aws_db_instance.this.storage_encrypted
    error_message = "RDS storage must remain encrypted."
  }

  assert {
    condition     = aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot && aws_db_instance.this.copy_tags_to_snapshot
    error_message = "RDS deletion protection, final snapshot creation, and snapshot tag copying must remain enabled."
  }

  assert {
    condition     = output.subnet_group_name == aws_db_subnet_group.this.name
    error_message = "The RDS module must expose the DB subnet group used by recovery restores."
  }

}
