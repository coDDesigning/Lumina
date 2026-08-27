mock_provider "aws" {}
mock_provider "random" {}

run "postgresql_maintenance_contract" {
  command = plan

  module {
    source = "./modules/rds"
  }

  variables {
    name_prefix              = "lumina-test"
    vpc_id                   = "vpc-12345678"
    subnet_ids               = ["subnet-11111111", "subnet-22222222"]
    ecs_security_group_id    = "sg-12345678"
    instance_class           = "db.t4g.small"
    allocated_storage_gb     = 20
    max_allocated_storage_gb = 100
    multi_az                 = false
    engine_version           = "16.8"
    database_name            = "lumina"
    username                 = "lumina"
    tags                     = { Environment = "test" }
  }

  assert {
    condition = {
      for parameter in aws_db_parameter_group.this.parameter :
      parameter.name => parameter.value
    }["autovacuum"] == "1"
    error_message = "Hosted PostgreSQL must keep autovacuum enabled."
  }

  assert {
    condition = {
      for parameter in aws_db_parameter_group.this.parameter :
      parameter.name => parameter.value
    }["track_counts"] == "1"
    error_message = "Autovacuum and auto-analyze require track_counts."
  }

  assert {
    condition = {
      for parameter in aws_db_parameter_group.this.parameter :
      parameter.name => parameter.value
    }["autovacuum_naptime"] == "30"
    error_message = "The autovacuum launcher must wake every 30 seconds."
  }
}
