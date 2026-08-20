data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  log_group  = "/ecs/${var.name_prefix}"
  ssm_base   = "/${var.name_prefix}"

  ssm_paths = {
    jwt_secret_key        = "${local.ssm_base}/jwt-secret-key"
    bootstrap_admin_token = "${local.ssm_base}/bootstrap-admin-token"
    gemini_api_key        = "${local.ssm_base}/gemini-api-key"
  }

  common_env = [
    { name = "APP_ENV", value = "production" },
    { name = "APP_DEBUG", value = "false" },
    { name = "DEPLOYMENT_MODE", value = "hosted" },
    { name = "STORAGE_BACKEND", value = "s3" },
    { name = "STORAGE_NAMESPACE", value = var.environment },
    { name = "S3_BUCKET", value = var.s3_bucket },
    { name = "S3_REGION", value = var.region },
    { name = "VECTOR_BACKEND", value = "pgvector" },
    { name = "EMBEDDING_PROVIDER", value = "gemini" },
    { name = "GEMINI_EMBEDDING_MODEL", value = "text-embedding-004" },
    { name = "AI_PROVIDER", value = "gemini" },
    { name = "EMBEDDING_BATCH_SIZE", value = "32" },
    { name = "EMBEDDING_TIMEOUT_SECONDS", value = "60" },
    { name = "MAX_UPLOAD_SIZE_BYTES", value = "52428800" },
    { name = "MAX_REQUEST_SIZE_BYTES", value = "1048576" },
    { name = "MAX_CONCURRENT_DOCUMENT_VALIDATIONS", value = "2" },
    { name = "UPLOAD_REQUEST_TIMEOUT_SECONDS", value = "300" },
    { name = "MAX_DOCUMENTS_PER_COURSE", value = "1000" },
    { name = "MAX_COURSE_STORAGE_BYTES", value = "2147483648" },
    { name = "MAX_PDF_PAGES", value = "500" },
    { name = "MAX_PDF_PAGE_PIXELS", value = "40000000" },
    { name = "MAX_PDF_TOTAL_PIXELS", value = "100000000" },
    { name = "MAX_PDF_CONTENT_STREAM_BYTES", value = "5242880" },
    { name = "MAX_PDF_DRAWING_OPERATIONS", value = "100000" },
    { name = "PROCESSING_JOB_LEASE_SECONDS", value = "60" },
    { name = "PROCESSING_JOB_MAX_ATTEMPTS", value = "3" },
    { name = "PROCESSING_JOB_POLL_SECONDS", value = "1.0" },
    { name = "PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS", value = "300" },
    { name = "MAX_EXTRACTED_CHARACTERS", value = "2000000" },
    { name = "MAX_DOCUMENT_CHUNKS", value = "1000" },
    { name = "OCR_LANGUAGE", value = "eng" },
    { name = "OCR_DPI", value = "300" },
    { name = "OCR_MIN_TEXT_CHARACTERS", value = "20" },
    { name = "DOCUMENT_CHUNK_SIZE_CHARACTERS", value = "1200" },
    { name = "DOCUMENT_CHUNK_OVERLAP_CHARACTERS", value = "200" },
    { name = "AI_GENERATION_TIMEOUT_SECONDS", value = "60" },
    { name = "AI_GENERATION_MAX_ATTEMPTS", value = "3" },
    { name = "AI_GENERATION_BACKOFF_BASE_SECONDS", value = "1.0" },
    { name = "AI_GENERATION_BACKOFF_MAX_SECONDS", value = "10.0" },
    { name = "AI_GENERATION_MAX_CONCURRENCY", value = "10" },
    { name = "STUDY_GUIDE_MATERIAL_MAX_CHARS", value = "120000" },
    { name = "QUIZ_MATERIAL_MAX_CHARS", value = "120000" },
    { name = "FLASHCARD_MATERIAL_MAX_CHARS", value = "120000" },
    { name = "AI_TUTOR_MATERIAL_MAX_CHARS", value = "120000" },
    { name = "COURSE_QA_MATERIAL_MAX_CHARS", value = "120000" },
  ]

  app_secrets = [
    { name = "DATABASE_URL", valueFrom = var.runtime_database_url_secret_arn },
    { name = "JWT_SECRET_KEY", valueFrom = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_paths.jwt_secret_key}" },
    { name = "BOOTSTRAP_ADMIN_TOKEN", valueFrom = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_paths.bootstrap_admin_token}" },
    { name = "GEMINI_API_KEY", valueFrom = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_paths.gemini_api_key}" },
  ]

  migrate_secrets = [
    { name = "DATABASE_URL", valueFrom = var.migration_database_url_secret_arn },
    { name = "JWT_SECRET_KEY", valueFrom = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_paths.jwt_secret_key}" },
    { name = "BOOTSTRAP_ADMIN_TOKEN", valueFrom = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_paths.bootstrap_admin_token}" },
    { name = "GEMINI_API_KEY", valueFrom = "arn:aws:ssm:${var.region}:${local.account_id}:parameter${local.ssm_paths.gemini_api_key}" },
  ]

  api_env = concat(local.common_env, [
    { name = "BOOTSTRAP_ADMIN_EMAIL", value = var.bootstrap_admin_email },
  ])

  worker_env = concat(local.common_env, [
    { name = "BOOTSTRAP_ADMIN_EMAIL", value = "worker@example.com" },
  ])

  container_base = {
    essential              = true
    image                  = "${var.ecr_repository_url}:${var.image_tag}"
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    linuxParameters = {
      capabilities = {
        drop = ["ALL"]
      }
      initProcessEnabled = true
      tmpfs = [
        {
          containerPath = "/tmp"
          size          = var.tmpfs_size_bytes
          mountOptions  = ["rw", "noexec", "nosuid", "nodev"]
        }
      ]
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = local.log_group
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "lumina"
      }
    }
  }

  api_container = merge(local.container_base, {
    name         = "api"
    environment  = local.api_env
    secrets      = local.app_secrets
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
  })

  worker_container = merge(local.container_base, {
    name        = "worker"
    environment = local.worker_env
    secrets     = local.app_secrets
    command     = ["python", "-m", "workers.document_processor"]
    stopTimeout = 120
  })

  migrate_container = merge(local.container_base, {
    name        = "migrate"
    environment = local.common_env
    secrets     = local.migrate_secrets
    command     = ["sh", "-c", "python -m alembic upgrade head && python -m alembic current --check-heads && python -m alembic check"]
  })
}
