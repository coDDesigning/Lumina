from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SCOPE_OVERRIDABLE = "overridable"
SCOPE_CONTAINER_MANAGED = "container_managed"
SCOPE_COMPOSE_MANAGED = "compose_managed"

RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

SettingKind = Literal["boolean", "integer", "float", "enum", "text", "json", "list"]
SettingScope = Literal["overridable", "container_managed", "compose_managed"]
SettingRisk = Literal["low", "medium", "high"]

_Row = tuple[str, str, str, str, str, str | None, float | None, float | None]

CONTAINER_MANAGED_KEYS: frozenset[str] = frozenset(
    {
        "APP_DEBUG",
        "APP_ENV",
        "CHROMA_PERSIST_DIRECTORY",
        "DATABASE_URL",
        "DEPLOYMENT_MODE",
        "EMBEDDING_MODEL_CACHE_DIRECTORY",
        "LUMINA_SUPERVISED_RESTART",
        "LUMINA_WEB_ROOT",
        "OPERATIONAL_LOG_PATH",
        "OPERATIONAL_LOG_PERSISTENCE_ENABLED",
        "STORAGE_BACKEND",
        "SYSTEM_SETTINGS_DIRECTORY",
        "UPLOAD_DIRECTORY",
    }
)

COMPOSE_MANAGED_KEYS: frozenset[str] = frozenset(
    {
        "BACKUP_ARCHIVE_NAME",
        "COMPOSE_PROJECT_NAME",
        "FORWARDED_ALLOW_IPS",
        "LUMINA_BACKUP_DIRECTORY",
        "LUMINA_BIND_ADDRESS",
        "LUMINA_CPU_LIMIT",
        "LUMINA_IMAGE",
        "LUMINA_MEMORY_LIMIT",
        "LUMINA_PORT",
        "LUMINA_TMPFS_SIZE_BYTES",
        "MINIO_CPU_LIMIT",
        "MINIO_MEMORY_LIMIT",
        "POSTGRES_CPU_LIMIT",
        "POSTGRES_MEMORY_LIMIT",
        "WORKER_STOP_GRACE_PERIOD",
    }
)

SECRET_KEYS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "BOOTSTRAP_ADMIN_TOKEN",
        "DATABASE_URL",
        "ENCRYPTION_KEY",
        "GEMINI_API_KEY",
        "JWT_SECRET_KEY",
        "OPENAI_API_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "SMTP_PASSWORD",
    }
)

ENUM_CHOICES: dict[str, tuple[str, ...]] = {
    "APP_ENV": ("development", "staging", "production"),
    "DEPLOYMENT_MODE": ("self_hosted", "hosted"),
    "STORAGE_BACKEND": ("local", "s3"),
    "VECTOR_BACKEND": ("chroma", "pgvector"),
    "WORKER_SHUTDOWN_MODE": ("abort", "drain"),
}

JSON_KEYS: frozenset[str] = frozenset({"AI_MODEL_CATALOG", "AI_MODEL_COST_RATES"})

LIST_KEYS: frozenset[str] = frozenset({"CORS_ALLOWED_ORIGINS", "FORWARDED_ALLOW_IPS"})

CONFIRMATION_KEYS: frozenset[str] = frozenset(
    {
        "DATABASE_URL",
        "ENCRYPTION_KEY",
        "JWT_SECRET_KEY",
        "STORAGE_NAMESPACE",
    }
)

HIGH_RISK_KEYS: frozenset[str] = SECRET_KEYS | frozenset(
    {
        "APP_ENV",
        "BOOTSTRAP_ADMIN_EMAIL",
        "CHROMA_PERSIST_DIRECTORY",
        "COMPOSE_PROJECT_NAME",
        "DEPLOYMENT_MODE",
        "EMBEDDING_MODEL_CACHE_DIRECTORY",
        "LUMINA_BIND_ADDRESS",
        "LUMINA_IMAGE",
        "LUMINA_PORT",
        "LUMINA_WEB_ROOT",
        "OPERATIONAL_LOG_PATH",
        "STORAGE_BACKEND",
        "STORAGE_NAMESPACE",
        "SYSTEM_SETTINGS_DIRECTORY",
        "UPLOAD_DIRECTORY",
        "VECTOR_BACKEND",
    }
)

MEDIUM_RISK_KEYS: frozenset[str] = frozenset(
    {
        "AI_DEFAULT_MODEL",
        "AI_LOG_RAW_RESPONSE_ON_FAILURE",
        "AI_MODEL_CATALOG",
        "AI_MODEL_COST_RATES",
        "APP_DEBUG",
        "APP_PUBLIC_BASE_URL",
        "CORS_ALLOWED_ORIGINS",
        "CREDIT_METERING_ENABLED",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_RECYCLE_SECONDS",
        "DATABASE_POOL_SIZE",
        "EMAIL_VERIFICATION_REQUIRED",
        "EMBEDDING_BACKFILL_PRUNE_ORPHANS",
        "ENABLE_HOSTED_ADS",
        "FORWARDED_ALLOW_IPS",
        "GENERATION_JOB_CONCURRENCY",
        "IMAGE_UNDERSTANDING_ENABLED",
        "LEGAL_POLICIES_ENABLED",
        "LUMINA_CPU_LIMIT",
        "LUMINA_MEMORY_LIMIT",
        "LUMINA_SUPERVISED_RESTART",
        "LUMINA_TMPFS_SIZE_BYTES",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "OPERATIONAL_LOG_PERSISTENCE_ENABLED",
        "PDF_PAGE_WORKERS",
        "PROCESSING_JOB_CONCURRENCY",
        "SECURITY_HEADERS_ENABLED",
        "SECURITY_HSTS_ENABLED",
        "SECURITY_HSTS_MAX_AGE_SECONDS",
        "SMTP_HOST",
        "SYSTEM_RESTART_DRAIN_TIMEOUT_SECONDS",
        "WORKER_SHUTDOWN_MODE",
        "WORKER_STOP_GRACE_PERIOD",
    }
)


@dataclass(frozen=True, slots=True)
class SettingDefinition:
    key: str
    section: str
    label: str
    help: str
    kind: SettingKind
    scope: SettingScope
    risk: SettingRisk
    secret: bool
    choices: tuple[str, ...]
    minimum: float | None
    maximum: float | None
    requires_confirmation: bool
    example: str | None

    @property
    def is_overridable(self) -> bool:
        return self.scope == SCOPE_OVERRIDABLE


SECTION_ORDER: tuple[str, ...] = (
    "Application",
    "Deployment mode (drives DB and document storage selection)",
    "Structured database",
    "Vector storage / document storage",
    "Semantic retrieval",
    "S3-compatible storage (STORAGE_BACKEND=s3)",
    "Embedding generation",
    "Visual understanding / image pipeline",
    "Durable document processing worker",
    "Periodic maintenance & reconciliation",
    "AI course-material context budget",
    "AI providers",
    "Credit lifecycle",
    "Rate limiting",
    "Authentication hardening",
    "Response security headers",
    "Legal policy package",
    "Optional hosted advertising",
)

_RAW: tuple[_Row, ...] = (
    (
        "APP_ENV",
        "Application",
        "Application environment",
        "Development | staging | production",
        "text",
        "development",
        None,
        None,
    ),
    (
        "APP_DEBUG",
        "Application",
        "Debug mode",
        "True locally, ALWAYS false in production",
        "boolean",
        "true",
        None,
        None,
    ),
    (
        "CORS_ALLOWED_ORIGINS",
        "Application",
        "Allowed browser origins",
        "Comma-separated exact browser origins. Empty disables cross-origin access, "
        "and empty is the supported configuration: one container serves the interface "
        "and /api on one origin, so nothing is cross-origin. Each entry must read "
        "exactly as a browser sends it -- no trailing slash, no path, and no default "
        "port (https://app.example.com, never https://app.example.com:443).",
        "text",
        None,
        None,
        None,
    ),
    (
        "COMPOSE_PROJECT_NAME",
        "Application",
        "Compose project name",
        "Names the Compose project and, with it, the lumina-data volume. Changing it "
        "on an existing deployment points Lumina at an empty volume.",
        "text",
        "lumina",
        None,
        None,
    ),
    (
        "LUMINA_IMAGE",
        "Application",
        "Container image",
        "The image Compose pulls. Unset means ghcr.io/coddesigning/lumina:latest, "
        "built from main. A full commit SHA in place of latest pins one release.",
        "text",
        "ghcr.io/coddesigning/lumina:latest",
        None,
        None,
    ),
    (
        "LUMINA_PORT",
        "Application",
        "Published port",
        "The single published port: one container serves the interface and /api "
        "together, so this is the whole address. After startup Lumina is at "
        "http://127.0.0.1:10312. Change it here alone -- nothing needs rebuilding, "
        "because the interface calls /api on whatever origin served it.",
        "integer",
        "10312",
        None,
        None,
    ),
    (
        "LUMINA_BIND_ADDRESS",
        "Application",
        "Bind address",
        "Published on every interface by default, so other devices on your network "
        "can reach Lumina immediately. Set 127.0.0.1 to keep it on this machine only, "
        "or a specific address (e.g. a Tailscale address) to limit it to that "
        "network. Restrict /health/* at the edge if this machine is exposed beyond a "
        "trusted network.",
        "text",
        "0.0.0.0",
        None,
        None,
    ),
    (
        "FORWARDED_ALLOW_IPS",
        "Application",
        "Forwarded allow IPs",
        "Addresses whose X-Forwarded-For the API believes. Unset by default, and that "
        "is the safe value: nothing sits between a browser and the container, so no "
        "forwarded address is trusted and per-IP rate limits key on the real peer. "
        "Set this ONLY to the address of a TLS reverse proxy you run yourself. Any "
        "wider value lets a caller send its own X-Forwarded-For and so choose its own "
        "rate-limit identity, which defeats the login and registration limits below.",
        "text",
        None,
        None,
        None,
    ),
    (
        "LUMINA_WEB_ROOT",
        "Application",
        "Web root",
        "Where the built interface lives. The image sets this to the build output it "
        "baked in; leave it unset outside a container to serve the API alone.",
        "text",
        None,
        None,
        None,
    ),
    (
        "LUMINA_TMPFS_SIZE_BYTES",
        "Application",
        "Container /tmp size",
        "Must hold two spooled copies of each concurrent maximum-size upload, plus "
        "overhead.",
        "integer",
        "268435456",
        None,
        None,
    ),
    (
        "LUMINA_CPU_LIMIT",
        "Application",
        "CPU limit",
        "Ceilings each container runs under. Compose applies these to a plain `docker "
        "compose up` as well, not only under Swarm. The API and the worker share "
        "these two: embedding and OCR are the memory-hungry step, so a host with less "
        "to spare lowers them together.",
        "integer",
        "4",
        None,
        None,
    ),
    (
        "LUMINA_MEMORY_LIMIT",
        "Application",
        "Memory limit",
        "Memory ceiling for each container, in Docker units such as 4G or 512M. "
        "Extraction and embedding are the hungry stages.",
        "text",
        "4G",
        None,
        None,
    ),
    (
        "POSTGRES_CPU_LIMIT",
        "Application",
        "Postgres CPU limit",
        "docker-compose.hosted.yml only, for the database and object store it runs "
        "beside the application. The self-hosted stack has neither.",
        "integer",
        "2",
        None,
        None,
    ),
    (
        "POSTGRES_MEMORY_LIMIT",
        "Application",
        "Postgres memory limit",
        "Memory ceiling for that PostgreSQL container.",
        "text",
        "2G",
        None,
        None,
    ),
    (
        "MINIO_CPU_LIMIT",
        "Application",
        "Minio CPU limit",
        "CPU ceiling for the MinIO container in the hosted topology.",
        "integer",
        "1",
        None,
        None,
    ),
    (
        "MINIO_MEMORY_LIMIT",
        "Application",
        "Minio memory limit",
        "Memory ceiling for that MinIO container.",
        "text",
        "1G",
        None,
        None,
    ),
    (
        "LUMINA_BACKUP_DIRECTORY",
        "Application",
        "Lumina backup directory",
        "Host directory for encrypted/off-host backup handling; never place it in the "
        "lumina-data volume. Override the archive name for every scheduled backup.",
        "text",
        "./backups",
        None,
        None,
    ),
    (
        "BACKUP_ARCHIVE_NAME",
        "Application",
        "Backup archive name",
        "File name given to the archive inside LUMINA_BACKUP_DIRECTORY. Give each "
        "scheduled run its own name or the previous archive is overwritten.",
        "text",
        "lumina-backup.tar.gz",
        None,
        None,
    ),
    (
        "JWT_SECRET_KEY",
        "Application",
        "JWT secret key",
        "Compose .env values containing $ must be single-quoted to prevent "
        "interpolation. Required in production and hosted mode. Development may use "
        "an ephemeral key.",
        "text",
        None,
        None,
        None,
    ),
    (
        "ENCRYPTION_KEY",
        "Application",
        "Encryption key",
        "Optional dedicated secret for encrypting user-provided BYOK API keys at "
        "rest. When unset, the encryption key is securely derived from "
        "JWT_SECRET_KEY.",
        "text",
        None,
        None,
        None,
    ),
    (
        "BOOTSTRAP_ADMIN_EMAIL",
        "Application",
        "Bootstrap admin email",
        "Optional when self-hosted: leave both empty and the first account to "
        "register becomes the administrator. Set both to reserve that account for one "
        "address, which then registers once with the token as X-Bootstrap-Token. "
        "Hosted mode requires both. Prefer a generated URL-safe token without $.",
        "text",
        None,
        None,
        None,
    ),
    (
        "BOOTSTRAP_ADMIN_TOKEN",
        "Application",
        "Bootstrap admin token",
        "Proof required from the reserved address when it registers, sent as the "
        "X-Bootstrap-Token header. At least 32 visible ASCII characters.",
        "text",
        None,
        None,
        None,
    ),
    (
        "SYSTEM_SETTINGS_DIRECTORY",
        "Application",
        "Override store directory",
        "SYSTEM_SETTINGS_DIRECTORY holds the administrator-managed configuration "
        "overrides, the last-known-good revision and the restart request state. It "
        "must be on durable storage, so the container pins it to "
        "/data/system-settings. It is never itself overridable: the override store "
        "cannot be moved by an override it would have to read from the new location "
        "to find.",
        "text",
        "./data/system-settings",
        None,
        None,
    ),
    (
        "LUMINA_SUPERVISED_RESTART",
        "Application",
        "Supervised restart",
        "Whether a supervisor restarts this process after it exits. The container "
        "image sets it true because Compose declares restart: unless-stopped. Where "
        "it is false, Lumina refuses the in-product restart instead of exiting into "
        "nothing.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "SYSTEM_RESTART_DRAIN_TIMEOUT_SECONDS",
        "Application",
        "Restart drain timeout",
        "How long a requested restart waits for in-flight document and generation "
        "jobs to finish before it proceeds, 0-3600. Work still running at the "
        "deadline is returned to the queue without spending an attempt, so nothing is "
        "lost.",
        "integer",
        "120",
        0,
        3600,
    ),
    (
        "DEPLOYMENT_MODE",
        "Deployment mode (drives DB and document storage selection)",
        "Deployment mode",
        "self_hosted -> SQLite + local filesystem document storage hosted -> "
        "PostgreSQL + S3-compatible storage; production requires s3",
        "text",
        "self_hosted",
        None,
        None,
    ),
    (
        "DATABASE_URL",
        "Structured database",
        "Database URL",
        "Production self_hosted SQLite must use an absolute path with an existing "
        "parent. self_hosted development example: sqlite:///./data/lumina.db hosted "
        "example: postgresql+psycopg://USER:PASSWORD@HOST:5432/lumina",
        "text",
        "sqlite:///./data/lumina.db",
        None,
        None,
    ),
    (
        "DATABASE_POOL_SIZE",
        "Structured database",
        "Database pool size",
        "Hosted PostgreSQL runtime pool. With RDS Proxy these values bound each "
        "API/worker process; total possible clients are replicas * (size + overflow).",
        "integer",
        "5",
        1,
        20,
    ),
    (
        "DATABASE_MAX_OVERFLOW",
        "Structured database",
        "Database max overflow",
        "Extra connections a process may open beyond the pool when it is saturated, "
        "0-20. Pool plus overflow is the per-process ceiling.",
        "integer",
        "5",
        None,
        None,
    ),
    (
        "DATABASE_POOL_RECYCLE_SECONDS",
        "Structured database",
        "Database pool recycle seconds",
        "Seconds before an idle pooled connection is discarded and reopened, 60-3600. "
        "Keep it under any proxy or database idle timeout.",
        "integer",
        "900",
        60,
        3600,
    ),
    (
        "OPERATIONAL_LOG_PATH",
        "Structured database",
        "Operational log path",
        "Self-hosted API and worker processes append sanitized operational events to "
        "this dedicated SQLite file. The container overrides it to the shared /data "
        "volume. Hosted deployments ignore the path and read the fixed CloudWatch log "
        "group configured by infrastructure instead.",
        "text",
        "./data/operational-logs.db",
        None,
        None,
    ),
    (
        "OPERATIONAL_LOG_PERSISTENCE_ENABLED",
        "Structured database",
        "Operational log persistence enabled",
        "Compose overrides this to true. Leave false for an ordinary development "
        "checkout unless durable local diagnostics are deliberately wanted.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "OPERATIONAL_LOG_RETENTION_DAYS",
        "Structured database",
        "Operational log retention days",
        "Days an operational event is kept before the background trim deletes it, "
        "1-366.",
        "integer",
        "30",
        1,
        366,
    ),
    (
        "OPERATIONAL_LOG_MAX_RECORDS",
        "Structured database",
        "Operational log max records",
        "Hard ceiling on stored events, 10000-10000000. The oldest rows are trimmed "
        "first, so this bounds the file even if retention has not elapsed.",
        "integer",
        "500000",
        10000,
        10000000,
    ),
    (
        "OPERATIONAL_LOG_QUERY_TIMEOUT_SECONDS",
        "Structured database",
        "Operational log query timeout seconds",
        "Budget for one administrator log query, 1-30. A heavy filter is refused "
        "rather than allowed to hold the database open.",
        "integer",
        "10",
        1,
        30,
    ),
    (
        "OPERATIONAL_LOG_CLOUDWATCH_GROUP",
        "Structured database",
        "CloudWatch log group",
        "Hosted only. Never accept a log group from an API request.",
        "text",
        "/ecs/lumina-production",
        None,
        None,
    ),
    (
        "OPERATIONAL_LOG_CLOUDWATCH_REGION",
        "Structured database",
        "CloudWatch region",
        "AWS region that log group lives in. Hosted only.",
        "text",
        "us-east-1",
        None,
        None,
    ),
    (
        "VECTOR_BACKEND",
        "Vector storage / document storage",
        "Vector backend",
        "VECTOR_BACKEND selects where chunk embeddings live. It defaults to the "
        "backend that matches DATABASE_URL: pgvector on PostgreSQL, chroma on SQLite. "
        "Setting pgvector with a non-PostgreSQL DATABASE_URL fails at startup. "
        "CHROMA_PERSIST_DIRECTORY holds the self-hosted vector collection and must be "
        "on durable storage; losing it means re-running the embedding backfill. "
        "CHROMA_PERSIST_DIRECTORY and UPLOAD_DIRECTORY must be absolute when the "
        "local backend or chroma vectors are used in production; hosted + s3 ignores "
        "both. Provision the production UPLOAD_DIRECTORY before application startup.",
        "text",
        "chroma",
        None,
        None,
    ),
    (
        "CHROMA_PERSIST_DIRECTORY",
        "Vector storage / document storage",
        "Chroma persist directory",
        "Where the self-hosted Chroma collection is written. Must be durable storage: "
        "losing it means re-running the embedding backfill.",
        "text",
        "./data/chroma",
        None,
        None,
    ),
    (
        "RETRIEVAL_CHUNK_LIMIT",
        "Semantic retrieval",
        "Retrieval chunk limit",
        "RETRIEVAL_CHUNK_LIMIT is how many of a course's chunks semantic retrieval "
        "ranks for one generation request. RETRIEVAL_MIN_SIMILARITY is the cosine "
        "floor (0.0-1.0) below which a ranked chunk is discarded; when nothing clears "
        "the floor the request is rejected without calling the AI provider. Set the "
        "floor to 0.0 to disable it.",
        "integer",
        "24",
        1,
        200,
    ),
    (
        "RETRIEVAL_MIN_SIMILARITY",
        "Semantic retrieval",
        "Retrieval min similarity",
        "Cosine floor a ranked chunk must clear, 0.0-1.0. Nothing above the floor "
        "means the request is refused rather than answered from weak material. Set "
        "0.0 to disable.",
        "float",
        "0.25",
        0.0,
        1.0,
    ),
    (
        "STORAGE_BACKEND",
        "Semantic retrieval",
        "Storage backend",
        "STORAGE_BACKEND selects where uploaded documents live: local -> filesystem "
        "under UPLOAD_DIRECTORY (self-hosted) s3 -> S3-compatible bucket; hosted "
        "production requires s3",
        "text",
        "local",
        None,
        None,
    ),
    (
        "STORAGE_NAMESPACE",
        "Semantic retrieval",
        "Storage namespace",
        "Prefix every stored object key carries, 1-40 characters of A-Z a-z 0-9 . _ "
        "-. Changing it orphans existing documents; they are not moved.",
        "text",
        "self-hosted",
        None,
        None,
    ),
    (
        "UPLOAD_DIRECTORY",
        "Semantic retrieval",
        "Upload directory",
        "Where uploaded documents are written under the local storage backend. Must "
        "be durable storage and absolute in production.",
        "text",
        "./data/uploads",
        None,
        None,
    ),
    (
        "S3_BUCKET",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "S3 bucket",
        "S3_BUCKET must exist; the hosted Compose creates it via minio-init. Without "
        "S3_ENDPOINT_URL (real AWS), S3_REGION is required. Static credentials are "
        "optional on AWS (prefer IAM roles); MinIO requires both keys.",
        "text",
        "lumina",
        None,
        None,
    ),
    (
        "S3_REGION",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "S3 region",
        "Bucket region. Required when no endpoint URL is set, which is the real-AWS "
        "case.",
        "text",
        "us-east-1",
        None,
        None,
    ),
    (
        "S3_ENDPOINT_URL",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "S3 endpoint URL",
        "Address of an S3-compatible service such as MinIO. Leave it unset for real "
        "AWS.",
        "text",
        "http://minio:9000",
        None,
        None,
    ),
    (
        "S3_ACCESS_KEY_ID",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "S3 access key ID",
        "Static access key. Optional on AWS, where an IAM role is preferred; MinIO "
        "requires it. Set both keys or neither.",
        "text",
        None,
        None,
        None,
    ),
    (
        "S3_SECRET_ACCESS_KEY",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "S3 secret access key",
        "Secret paired with that access key. Single-quote it if it contains $.",
        "text",
        None,
        None,
        None,
    ),
    (
        "S3_FORCE_PATH_STYLE",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "S3 force path style",
        "Whether to address buckets as a path rather than a subdomain. MinIO needs "
        "true; real AWS does not.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "MAX_UPLOAD_SIZE_BYTES",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max upload size bytes",
        "Largest document a student may upload. Enforced before the body is read, so "
        "an oversized file is refused rather than buffered.",
        "integer",
        "52428800",
        None,
        None,
    ),
    (
        "MAX_REQUEST_SIZE_BYTES",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max request size bytes",
        "Ceiling on an ordinary JSON request body. Uploads are bounded separately by "
        "MAX_UPLOAD_SIZE_BYTES.",
        "integer",
        "1048576",
        None,
        None,
    ),
    (
        "MAX_CONCURRENT_DOCUMENT_VALIDATIONS",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max concurrent document validations",
        "How many uploads may be validated at once before the rest wait. Validation "
        "reads bytes, so this bounds memory during a burst.",
        "integer",
        "2",
        None,
        None,
    ),
    (
        "PDF_PAGE_WORKERS",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "PDF page workers",
        "Pages of one PDF processed in parallel inside a single extraction. "
        "Multiplies with PROCESSING_JOB_CONCURRENCY for total CPU processes.",
        "integer",
        "4",
        1,
        None,
    ),
    (
        "UPLOAD_REQUEST_TIMEOUT_SECONDS",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Upload request timeout seconds",
        "Total deadline for upload admission and body receipt.",
        "integer",
        "300",
        1,
        300,
    ),
    (
        "MAX_DOCUMENTS_PER_COURSE",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max documents per course",
        "How many documents one course may hold.",
        "integer",
        "1000",
        None,
        None,
    ),
    (
        "MAX_COURSE_STORAGE_BYTES",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max course storage bytes",
        "Total stored bytes one course may hold across all of its documents.",
        "integer",
        "2147483648",
        None,
        None,
    ),
    (
        "MAX_PDF_PAGES",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max PDF pages",
        "Pages a single PDF may contain before it is refused, so one enormous file "
        "cannot occupy a worker slot indefinitely.",
        "integer",
        "500",
        None,
        None,
    ),
    (
        "MAX_PDF_PAGE_PIXELS",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max PDF page pixels",
        "Pixel ceiling for one rendered PDF page. A decompression bomb is refused "
        "here rather than during rasterisation.",
        "integer",
        "40000000",
        None,
        None,
    ),
    (
        "MAX_PDF_TOTAL_PIXELS",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max PDF total pixels",
        "Pixel ceiling summed across every page of one PDF.",
        "integer",
        "200000000",
        None,
        None,
    ),
    (
        "MAX_PDF_CONTENT_STREAM_BYTES",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max PDF content stream bytes",
        "Ceiling on a single decompressed PDF content stream, which is the other half "
        "of the decompression-bomb guard.",
        "integer",
        "16777216",
        None,
        None,
    ),
    (
        "MAX_PDF_DRAWING_OPERATIONS",
        "S3-compatible storage (STORAGE_BACKEND=s3)",
        "Max PDF drawing operations",
        "Drawing operations one PDF page may issue. A page that exceeds it is "
        "pathological rather than large.",
        "integer",
        "100000",
        None,
        None,
    ),
    (
        "EMBEDDING_MODEL_CACHE_DIRECTORY",
        "Embedding generation",
        "Embedding model cache directory",
        "Embeddings are computed in this process by fastembed (ONNX, CPU) and are "
        "completely independent of which vendor answers a generation request: the "
        "same vectors are produced whether this deployment talks to Gemini, to "
        "Ollama, or to nothing. Ollama is therefore no longer required for a document "
        "to become ready. There is no model setting: the model is pinned in "
        "backend/app/embedding_models.py because the stored vector width, its CHECK "
        "constraints and its HNSW indexes are built for exactly that model, so "
        "changing it is an Alembic revision rather than an environment edit. Only "
        "where the ONNX weights live varies. Container images bake them at build time "
        "and never reach the network at runtime; a checkout downloads them once with "
        "`python scripts/fetch_embedding_model.py`.",
        "text",
        "./data/embedding-models",
        None,
        None,
    ),
    (
        "EMBEDDING_BATCH_SIZE",
        "Embedding generation",
        "Embedding batch size",
        "Texts per forward pass, 1-256.",
        "integer",
        "32",
        1,
        256,
    ),
    (
        "IMAGE_UNDERSTANDING_ENABLED",
        "Visual understanding / image pipeline",
        "Image understanding enabled",
        "There is no image provider setting. Descriptions of diagrams, tables and "
        "charts are extracted once by the background worker and stored on the "
        "document, where every reader of that course shares them, so this can never "
        "be a per-user choice. The deployment uses the first vision-capable model of "
        "the first available vendor that has an image implementation (gemini or "
        "ollama); with none, visual extraction is skipped and recorded as "
        "not_configured. Describing a visual is a paid call per image, so a "
        "deployment can decline it without giving up the vendor that would otherwise "
        "answer.",
        "boolean",
        "true",
        None,
        None,
    ),
    (
        "IMAGE_UNDERSTANDING_TIMEOUT_SECONDS",
        "Visual understanding / image pipeline",
        "Image understanding timeout seconds",
        "Budget for describing one visual, 1-300. Exceeding it records that visual as "
        "failed without failing the document.",
        "integer",
        "180",
        1,
        300,
    ),
    (
        "IMAGE_UNDERSTANDING_MAX_BYTES",
        "Visual understanding / image pipeline",
        "Image understanding max bytes",
        "Largest visual sent to the model, 1KiB-50MiB. Anything bigger is skipped "
        "rather than resized.",
        "integer",
        "10485760",
        1024,
        None,
    ),
    (
        "IMAGE_UNDERSTANDING_INLINE_MAX_VISUALS",
        "Visual understanding / image pipeline",
        "Image understanding inline max visuals",
        "How many visuals an extraction attempt describes before deferring the rest "
        "to the background describe_visuals job. Describing one visual costs tens of "
        "seconds, so this must stay small relative to "
        "PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS: a document whose visuals all had to "
        "be described inline could never finish within one attempt.",
        "integer",
        "2",
        1,
        None,
    ),
    (
        "PROCESSING_JOB_LEASE_SECONDS",
        "Durable document processing worker",
        "Processing job lease seconds",
        "How long a claimed document job stays leased to one worker slot, 5-86400. "
        "Once it expires another slot may recover the job, so this bounds how long a "
        "killed worker can strand work.",
        "integer",
        "60",
        5,
        86400,
    ),
    (
        "PROCESSING_JOB_MAX_ATTEMPTS",
        "Durable document processing worker",
        "Processing job max attempts",
        "Attempts a document job gets before it is recorded as failed, 1-100. A "
        "shutdown that returns a job to the queue does not spend an attempt.",
        "integer",
        "3",
        1,
        100,
    ),
    (
        "PROCESSING_JOB_POLL_SECONDS",
        "Durable document processing worker",
        "Processing job poll seconds",
        "Seconds a worker slot waits between polls while the document queue is empty.",
        "float",
        "1.0",
        None,
        None,
    ),
    (
        "PROCESSING_JOB_ATTEMPT_TIMEOUT_SECONDS",
        "Durable document processing worker",
        "Processing job attempt timeout seconds",
        "Wall-clock budget for one document extraction attempt, 1-86400. An attempt "
        "that exceeds it is abandoned and retried while attempts remain.",
        "integer",
        "300",
        1,
        86400,
    ),
    (
        "PROCESSING_JOB_CONCURRENCY",
        "Durable document processing worker",
        "Processing job concurrency",
        "Documents processed at once by one worker process. Each slot needs two "
        "database connections, so hosted deployments must keep 2 * "
        "PROCESSING_JOB_CONCURRENCY + 1 within DATABASE_POOL_SIZE plus "
        "DATABASE_MAX_OVERFLOW. Maximum 6.",
        "integer",
        "2",
        1,
        None,
    ),
    (
        "PROCESSING_JOB_MAX_ACTIVE_PER_USER",
        "Durable document processing worker",
        "Processing job max active per user",
        "Course and profile uploads share this per-account active-job ceiling. It "
        "counts extraction jobs only: a background describe_visuals job runs for "
        "minutes to hours and must not keep its owner from uploading.",
        "integer",
        "1",
        1,
        None,
    ),
    (
        "DESCRIBE_VISUALS_ATTEMPT_TIMEOUT_SECONDS",
        "Durable document processing worker",
        "Describe visuals attempt timeout seconds",
        "Visual description runs as its own job after a document is ready, and its "
        "progress is checkpointed per visual, so one long attempt is cheaper than "
        "many short ones: every retry re-pays the PDF parse, OCR and chunking that "
        "the attempt also performs.",
        "integer",
        "1800",
        1,
        86400,
    ),
    (
        "DESCRIBE_VISUALS_MAX_ACTIVE_PER_USER",
        "Durable document processing worker",
        "Describe visuals max active per user",
        "Visual-description jobs one account may have running at once, 1-10. Counted "
        "separately from extraction so a long description cannot block an upload.",
        "integer",
        "1",
        1,
        None,
    ),
    (
        "VISUAL_DESCRIPTION_SWEEP_INTERVAL_SECONDS",
        "Durable document processing worker",
        "Visual description sweep interval seconds",
        "How often the worker looks for ready documents still carrying a visual that "
        "nothing described, including documents processed while visual analysis was "
        "switched off. Each document is queued once. Set to 0 to disable the sweep.",
        "float",
        "900",
        None,
        None,
    ),
    (
        "GENERATION_JOB_LEASE_SECONDS",
        "Durable document processing worker",
        "Generation job lease seconds",
        "Durable AI generation uses separate slots in the same worker process. A "
        "student's third active request remains queued until one of the first two "
        "ends.",
        "integer",
        "120",
        5,
        86400,
    ),
    (
        "GENERATION_JOB_MAX_ATTEMPTS",
        "Durable document processing worker",
        "Generation job max attempts",
        "Attempts a backgrounded generation gets before it is recorded as failed, "
        "1-100.",
        "integer",
        "2",
        1,
        100,
    ),
    (
        "GENERATION_JOB_POLL_SECONDS",
        "Durable document processing worker",
        "Generation job poll seconds",
        "Seconds a worker slot waits between polls while the generation queue is "
        "empty.",
        "float",
        "1.0",
        None,
        None,
    ),
    (
        "GENERATION_JOB_ATTEMPT_TIMEOUT_SECONDS",
        "Durable document processing worker",
        "Generation job attempt timeout seconds",
        "Wall-clock budget for one backgrounded generation attempt, 1-86400.",
        "integer",
        "600",
        1,
        86400,
    ),
    (
        "GENERATION_JOB_CONCURRENCY",
        "Durable document processing worker",
        "Generation job concurrency",
        "Generation claim slots inside one worker process, 1-6. Each slot costs a job "
        "connection plus a heartbeat connection from the database pool.",
        "integer",
        "2",
        1,
        None,
    ),
    (
        "GENERATION_JOB_MAX_ACTIVE_PER_USER",
        "Durable document processing worker",
        "Generation job max active per user",
        "Backgrounded generations one account may have running at once, 1-10.",
        "integer",
        "2",
        1,
        None,
    ),
    (
        "WORKER_SHUTDOWN_MODE",
        "Durable document processing worker",
        "Worker shutdown mode",
        "Stop grace per mode: abort needs 60s; drain the largest attempt timeout + "
        "45s.",
        "text",
        "abort",
        None,
        None,
    ),
    (
        "WORKER_STOP_GRACE_PERIOD",
        "Durable document processing worker",
        "Worker stop grace period",
        "How long Compose waits for the worker to stop before killing it. Read by "
        "Compose, not the application: abort needs about 60s, drain needs the largest "
        "attempt timeout plus 45s.",
        "text",
        "60s",
        None,
        None,
    ),
    (
        "MAX_EXTRACTED_CHARACTERS",
        "Durable document processing worker",
        "Max extracted characters",
        "Characters kept from one document. Text past the ceiling is dropped, so a "
        "single enormous file cannot fill the database.",
        "integer",
        "2000000",
        None,
        None,
    ),
    (
        "MAX_DOCUMENT_CHUNKS",
        "Durable document processing worker",
        "Max document chunks",
        "Chunks one document may produce. Each chunk costs one stored embedding.",
        "integer",
        "1000",
        None,
        None,
    ),
    (
        "OCR_LANGUAGE",
        "Durable document processing worker",
        "OCR language",
        "Tesseract language packs to load, plus-joined such as eng+tur. Only packs "
        "installed in the image can be named; the image ships eng.",
        "text",
        "eng",
        None,
        None,
    ),
    (
        "OCR_DPI",
        "Durable document processing worker",
        "OCR resolution (DPI)",
        "Resolution a page is rasterised at before OCR. Higher reads small type "
        "better and costs proportionally more time and memory.",
        "integer",
        "300",
        None,
        None,
    ),
    (
        "OCR_MIN_TEXT_CHARACTERS",
        "Durable document processing worker",
        "OCR min text characters",
        "Characters a page must already contain to be treated as digital text. Below "
        "it the page is treated as a scan and sent to OCR.",
        "integer",
        "20",
        None,
        None,
    ),
    (
        "DOCUMENT_CHUNK_SIZE_CHARACTERS",
        "Durable document processing worker",
        "Document chunk size characters",
        "Target size of one retrievable chunk. It bounds how much context a single "
        "retrieval hit can carry.",
        "integer",
        "1200",
        None,
        None,
    ),
    (
        "DOCUMENT_CHUNK_OVERLAP_CHARACTERS",
        "Durable document processing worker",
        "Document chunk overlap characters",
        "Characters repeated between neighbouring chunks so a sentence split across "
        "the boundary is still retrievable. Must be below the chunk size.",
        "integer",
        "200",
        None,
        None,
    ),
    (
        "COURSE_PURGE_INTERVAL_SECONDS",
        "Periodic maintenance & reconciliation",
        "Course purge interval seconds",
        "Automatic background cleanup and vector synchronization. Set an interval to "
        "0 to disable that maintenance cycle in the background worker. "
        "COURSE_PURGE_INTERVAL_SECONDS: seconds between scans for stranded tombstoned "
        "courses.",
        "float",
        "3600",
        None,
        None,
    ),
    (
        "COURSE_PURGE_OPERATION_TIMEOUT_SECONDS",
        "Periodic maintenance & reconciliation",
        "Course purge operation timeout seconds",
        "COURSE_PURGE_OPERATION_TIMEOUT_SECONDS: PostgreSQL lock/statement timeout "
        "budget given to the delete of one course (vectors, documents, chunks, "
        "quizzes). Widens the shared engine's default 5s cap so a large course's "
        "purge is not aborted mid-transaction by QueryCanceled.",
        "float",
        "300",
        1.0,
        86400.0,
    ),
    (
        "EMBEDDING_BACKFILL_INTERVAL_SECONDS",
        "Periodic maintenance & reconciliation",
        "Embedding backfill interval seconds",
        "EMBEDDING_BACKFILL_INTERVAL_SECONDS: seconds between scans for missing "
        "vectors.",
        "float",
        "3600",
        None,
        None,
    ),
    (
        "EMBEDDING_BACKFILL_BATCH_SIZE",
        "Periodic maintenance & reconciliation",
        "Embedding backfill batch size",
        "Chunks the backfill embeds per pass. Larger batches finish sooner and hold "
        "more memory while they run.",
        "integer",
        "64",
        None,
        None,
    ),
    (
        "EMBEDDING_BACKFILL_PRUNE_ORPHANS",
        "Periodic maintenance & reconciliation",
        "Embedding backfill prune orphans",
        "Whether the backfill also deletes vectors whose chunk no longer exists.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "AI_USAGE_RETENTION_DAYS",
        "Periodic maintenance & reconciliation",
        "AI usage retention days",
        "AI_USAGE_RETENTION_DAYS: masked AI-usage telemetry older than this is "
        "deleted by the background worker on the interval below. "
        "AI_USAGE_CLEANUP_INTERVAL_SECONDS is the seconds between those retention "
        "scans; set it to 0 to disable the cycle (a one-off `python -m "
        "workers.ai_usage_cleanup` still works).",
        "integer",
        "90",
        None,
        None,
    ),
    (
        "AI_USAGE_CLEANUP_BATCH_SIZE",
        "Periodic maintenance & reconciliation",
        "AI usage cleanup batch size",
        "Telemetry rows deleted per pass, so a long retention change cannot hold one "
        "long transaction open.",
        "integer",
        "1000",
        None,
        None,
    ),
    (
        "AI_USAGE_CLEANUP_INTERVAL_SECONDS",
        "Periodic maintenance & reconciliation",
        "AI usage cleanup interval seconds",
        "Seconds between retention scans; 0 disables the cycle and leaves `python -m "
        "workers.ai_usage_cleanup` as the manual path.",
        "float",
        "86400",
        None,
        None,
    ),
    (
        "MATERIAL_MAX_CHARS_CEILING",
        "AI course-material context budget",
        "Material max chars ceiling",
        "Maximum characters of course material placed into one generation request, "
        "per feature. Each value must be at least DOCUMENT_CHUNK_SIZE_CHARACTERS. "
        "Study guide, quiz, AI tutor, course Q&A, and exam analysis head each "
        "retrieved passage with a citation key, which spends part of the budget, so "
        "they carry a wider one than flashcards, which emit no citations. See "
        "docs/citations.md. MATERIAL_MAX_CHARS_CEILING caps every per-feature budget "
        "below to this many characters when set (must also be >= "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS). Set it once for a small local context "
        "window instead of lowering each budget.",
        "integer",
        "16000",
        None,
        None,
    ),
    (
        "STUDY_GUIDE_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Study guide material max chars",
        "Character budget for study guide generation. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "QUIZ_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Quiz material max chars",
        "Character budget for quiz generation. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "FLASHCARD_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Flashcard material max chars",
        "Character budget for flashcard generation. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "120000",
        None,
        None,
    ),
    (
        "AI_TUTOR_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "AI tutor material max chars",
        "Character budget for the AI tutor. At least DOCUMENT_CHUNK_SIZE_CHARACTERS, "
        "and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "COURSE_QA_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Course Q&A material max chars",
        "Character budget for course Q&A. At least DOCUMENT_CHUNK_SIZE_CHARACTERS, "
        "and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_ANALYSIS_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam analysis material max chars",
        "Character budget for exam source analysis. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_PAST_PAPER_MAX_CHARS",
        "AI course-material context budget",
        "Exam past paper max chars",
        "Character budget for reading a past paper. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_TOPIC_GUIDE_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam topic guide material max chars",
        "Character budget for an exam topic guide. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_TOPIC_SUMMARY_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam topic summary material max chars",
        "Character budget for an exam topic summary. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "60000",
        None,
        None,
    ),
    (
        "EXAM_TOPIC_QUIZ_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam topic quiz material max chars",
        "Character budget for an exam topic quiz. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_SIMILAR_QUESTIONS_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam similar questions material max chars",
        "Character budget for similar-question generation. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_MOCK_EXAM_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam mock exam material max chars",
        "Character budget for mock exam generation. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_REVIEW_SHEET_MATERIAL_MAX_CHARS",
        "AI course-material context budget",
        "Exam review sheet material max chars",
        "Character budget for the exam review sheet. At least "
        "DOCUMENT_CHUNK_SIZE_CHARACTERS, and clamped by MATERIAL_MAX_CHARS_CEILING.",
        "integer",
        "126000",
        None,
        None,
    ),
    (
        "EXAM_MOCK_EXAM_QUESTION_COUNT",
        "AI course-material context budget",
        "Exam mock exam question count",
        "Questions in a generated mock exam, 1-20. Allocation reserves one question "
        "per requested topic, so a paper too short to cover them all is refused.",
        "integer",
        "20",
        1,
        20,
    ),
    (
        "EXAM_QUIZ_DEFAULT_QUESTION_COUNT",
        "AI course-material context budget",
        "Exam quiz default question count",
        "Questions in a practice quiz or topic exam when the student names no count, "
        "1-20.",
        "integer",
        "10",
        1,
        20,
    ),
    (
        "AI_DEFAULT_MODEL",
        "AI providers",
        "Default AI model",
        "A vendor is available because its credential or endpoint is configured, and "
        "for no other reason. There is no provider selector: set a key and its models "
        "appear in GET /api/models; unset it and they are gone. GEMINI_API_KEY -> "
        "gemini:* models OPENAI_API_KEY -> openai:* models ANTHROPIC_API_KEY -> "
        "claude:* models OLLAMA_BASE_URL -> ollama:* models Startup fails if this "
        "leaves no model at all. A placeholder is not a comment here: any non-blank "
        "value advertises that vendor, so leave a key empty rather than filling in an "
        "example. See docs/ai_providers.md. There is no fallback setting either. "
        "Every available vendor joins the chain, the selected model's vendor first "
        "and the rest in the fixed order ollama, gemini, openai, claude. Configuring "
        "a paid key therefore accepts that an outage of one vendor bills the next "
        "one; GET /api/admin/ai-costs reports the spend per vendor and model. "
        "Optional deployment default, an exact provider:model id from GET "
        "/api/models. Unset, it is the first model of the first available vendor in "
        "that same fixed order - local first, because a local model costs nothing and "
        "sends no course material anywhere. A user's preferred_model overrides it; a "
        "request's explicit model overrides both.",
        "text",
        None,
        None,
        None,
    ),
    (
        "AI_MODEL_CATALOG",
        "AI providers",
        "AI model catalog",
        "Optional JSON model catalog keyed by provider. When set it is authoritative: "
        "a vendor whose credential is configured but which the catalog does not list "
        "is a startup error rather than a silent omission.",
        "text",
        None,
        None,
        None,
    ),
    (
        "AI_MODEL_COST_RATES",
        "AI providers",
        "AI model cost rates",
        "Per-million-token prices used to estimate spend, as the JSON object shown "
        "above. Empty means generations are recorded without a cost estimate.",
        "text",
        None,
        None,
        None,
    ),
    (
        "AI_GENERATION_TIMEOUT_SECONDS",
        "AI providers",
        "AI generation timeout seconds",
        "Per-request deadline for one generation, every provider. Raise it for local "
        "models on modest hardware; they are far slower than a hosted API.",
        "integer",
        "60",
        1,
        300,
    ),
    (
        "AI_GENERATION_MAX_ATTEMPTS",
        "AI providers",
        "AI generation max attempts",
        "Attempts one provider call gets before the chain moves to the next vendor, "
        "1-10.",
        "integer",
        "3",
        1,
        10,
    ),
    (
        "AI_GENERATION_BACKOFF_BASE_SECONDS",
        "AI providers",
        "AI generation backoff base seconds",
        "First wait between provider retries. It doubles each attempt up to the "
        "maximum below.",
        "float",
        "1.0",
        None,
        None,
    ),
    (
        "AI_GENERATION_BACKOFF_MAX_SECONDS",
        "AI providers",
        "AI generation backoff max seconds",
        "Ceiling on that doubling wait. Must be at least the base.",
        "float",
        "10.0",
        None,
        None,
    ),
    (
        "AI_GENERATION_MAX_CONCURRENCY",
        "AI providers",
        "AI generation max concurrency",
        "Provider calls in flight at once across the process, 1-100. It is the main "
        "guard against a vendor rate limit.",
        "integer",
        "10",
        1,
        100,
    ),
    (
        "OLLAMA_BASE_URL",
        "AI providers",
        "Ollama base URL",
        "Self-hosted Ollama. The model must already be pulled on that instance and "
        "must reliably emit JSON; docs/ai_providers.md lists the capability bar. "
        "Ollama has no default any more, because a default would mean every "
        "deployment always claims a local server. Setting this is what makes ollama:* "
        "models appear. Root Compose reaches an Ollama process on the host through "
        "this name; use http://localhost:11434 when running Python directly. On "
        "Linux, start Ollama with OLLAMA_HOST=0.0.0.0:11434 and firewall the port to "
        "the Docker bridge.",
        "text",
        "http://host.docker.internal:11434",
        None,
        None,
    ),
    (
        "OLLAMA_MODEL",
        "AI providers",
        "Ollama model",
        "One model answers both text generation and image understanding, so it must "
        "be multimodal for visual analysis to work. A text-only model is declared "
        'through AI_MODEL_CATALOG with "vision": false, which switches visual '
        "analysis off truthfully instead of sending it images it cannot read.",
        "text",
        "qwen3.5:9b",
        None,
        None,
    ),
    (
        "OLLAMA_TEMPERATURE",
        "AI providers",
        "Ollama temperature",
        "Sampling options sent with every Ollama request. Ollama's own defaults are "
        "tuned for chat, not for schema-constrained JSON; temperature 0.8 makes a "
        'small model violate hard constraints such as "exactly four options" often '
        "enough to fail whole generations. See the single-GPU box profile in "
        "docs/ai_providers.md.",
        "float",
        "0.2",
        0.0,
        2.0,
    ),
    (
        "OLLAMA_TOP_P",
        "AI providers",
        "Ollama top p",
        "Nucleus sampling cutoff, 0.01-1.0. Lower keeps the model to its most likely "
        "tokens.",
        "float",
        "0.9",
        0.01,
        1.0,
    ),
    (
        "OLLAMA_NUM_CTX",
        "AI providers",
        "Ollama num ctx",
        "num_ctx is sent per request, so the model does not need a custom Modelfile "
        "and a server-wide OLLAMA_CONTEXT_LENGTH cannot silently inflate the KV "
        "cache. The prompt and the response share this window.",
        "integer",
        "8192",
        512,
        131072,
    ),
    (
        "OLLAMA_NUM_PREDICT",
        "AI providers",
        "Ollama num predict",
        "Most tokens one response may generate, 64-131072. Must not exceed "
        "OLLAMA_NUM_CTX.",
        "integer",
        "4096",
        64,
        131072,
    ),
    (
        "OLLAMA_REPEAT_PENALTY",
        "AI providers",
        "Ollama repeat penalty",
        "How hard repeated tokens are discouraged, 0.5-2.0. Above 1.0 penalises "
        "repetition.",
        "float",
        "1.1",
        0.5,
        2.0,
    ),
    (
        "OLLAMA_THINK",
        "AI providers",
        "Ollama think",
        "Thinking-capable models such as qwen3.5 reason before answering unless told "
        "not to. With format=json that reasoning can consume the whole turn and leave "
        "an empty response, so it is off unless explicitly enabled.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "GEMINI_API_KEY",
        "AI providers",
        "Gemini api key",
        "Google Gemini credential. Setting it is what makes the vendor available; "
        "there is no separate provider switch.",
        "text",
        None,
        None,
        None,
    ),
    (
        "OPENAI_API_KEY",
        "AI providers",
        "Openai api key",
        "OpenAI credential. Setting it is what makes the vendor available.",
        "text",
        None,
        None,
        None,
    ),
    (
        "ANTHROPIC_API_KEY",
        "AI providers",
        "Anthropic api key",
        "Anthropic credential. Setting it is what makes the vendor available.",
        "text",
        None,
        None,
        None,
    ),
    (
        "AI_GENERATION_OVERALL_TIMEOUT_SECONDS",
        "AI providers",
        "AI generation overall timeout seconds",
        "Overall deadline for the entire generation request including all retries and "
        "fallbacks. Must be less than ALB idle timeout (120s). Default 110s.",
        "integer",
        "110",
        1,
        300,
    ),
    (
        "AI_GRADING_OVERALL_TIMEOUT_SECONDS",
        "AI providers",
        "AI grading overall timeout seconds",
        "Whole-request budget for grading one attempt's open-ended answers, 1-55. It "
        "sits below the window a hosted database leaves a transaction idle, so a slow "
        "grader costs marks rather than the student's answers.",
        "integer",
        "45",
        1,
        55,
    ),
    (
        "AI_LOG_RAW_RESPONSE_ON_FAILURE",
        "AI providers",
        "AI log raw response on failure",
        "When a generation fails, the logs always describe the model's response by "
        "size, digest, top-level key names and the fields validation rejected. Turn "
        "this on to add a truncated copy of the response text itself. It is study "
        "content, so leave it off unless you are actively diagnosing a failure.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "CREDIT_METERING_ENABLED",
        "Credit lifecycle",
        "Credit metering enabled",
        "Full policy in docs/credits.md. Credits meter hosted AI generation; "
        "self-hosted operators supply their own inference, so metering is off for "
        "them. Unset, this follows DEPLOYMENT_MODE: true when hosted, false when "
        "self_hosted. The value below is spelled out to match this template's "
        "DEPLOYMENT_MODE=self_hosted -- change it with the mode, or delete the line "
        "to track the mode automatically. While false nothing is charged, refunded, "
        "or granted, no charge or refund rows are written, and the API reports a null "
        "balance rather than a frozen number.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "CREDIT_INITIAL_GRANT",
        "Credit lifecycle",
        "Credit initial grant",
        "Granted once at registration, recorded as an INITIAL_GRANT ledger row.",
        "float",
        "50.0",
        None,
        None,
    ),
    (
        "CREDIT_PERIODIC_GRANT",
        "Credit lifecycle",
        "Credit periodic grant",
        "Granted at most once per calendar month, lazily on the next charge or "
        "balance read. The grant is trimmed so it never carries a balance past "
        "CREDIT_MAX_BALANCE.",
        "float",
        "50.0",
        None,
        None,
    ),
    (
        "CREDIT_MAX_BALANCE",
        "Credit lifecycle",
        "Credit max balance",
        "Ceiling for automatic granting only. It never reduces a balance, and "
        "deliberate administrator grants and adjustments are not capped by it.",
        "float",
        "100.0",
        0.0,
        None,
    ),
    (
        "RATE_LIMIT_LOGIN_MAX_ATTEMPTS",
        "Rate limiting",
        "Rate limit login max attempts",
        "Full policy in docs/rate_limiting.md. Per-IP and per-account login attempts "
        "allowed within the window before a 429; the account dimension additionally "
        "locks out progressively (see RATE_LIMIT_LOCKOUT_* below).",
        "integer",
        "10",
        None,
        None,
    ),
    (
        "RATE_LIMIT_LOGIN_WINDOW_SECONDS",
        "Rate limiting",
        "Rate limit login window seconds",
        "Window those sign-in attempts are counted over.",
        "integer",
        "300",
        None,
        None,
    ),
    (
        "RATE_LIMIT_REGISTER_MAX_ATTEMPTS",
        "Rate limiting",
        "Rate limit register max attempts",
        "Per-IP registration attempts allowed within the window before a 429.",
        "integer",
        "5",
        None,
        None,
    ),
    (
        "RATE_LIMIT_REGISTER_WINDOW_SECONDS",
        "Rate limiting",
        "Rate limit register window seconds",
        "Window those registrations are counted over.",
        "integer",
        "3600",
        None,
        None,
    ),
    (
        "RATE_LIMIT_GENERATION_MAX_ATTEMPTS",
        "Rate limiting",
        "Rate limit generation max attempts",
        "Per-user, per-feature AI generation requests allowed within the window "
        "before a 429. Checked ahead of CreditService.charge, so a throttled request "
        "never spends credit.",
        "integer",
        "30",
        None,
        None,
    ),
    (
        "RATE_LIMIT_GENERATION_WINDOW_SECONDS",
        "Rate limiting",
        "Rate limit generation window seconds",
        "Window those generation requests are counted over.",
        "integer",
        "3600",
        None,
        None,
    ),
    (
        "RATE_LIMIT_LOCKOUT_BASE_SECONDS",
        "Rate limiting",
        "Rate limit lockout base seconds",
        "Progressive lockout for repeated account-login violations: the first lockout "
        "lasts the base duration, each further violation while still recently locked "
        "doubles it, capped at the max.",
        "integer",
        "30",
        None,
        None,
    ),
    (
        "RATE_LIMIT_LOCKOUT_MAX_SECONDS",
        "Rate limiting",
        "Rate limit lockout max seconds",
        "Ceiling on that doubling lockout. Must be at least the base.",
        "integer",
        "1800",
        None,
        None,
    ),
    (
        "RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS",
        "Rate limiting",
        "Rate limit verification max attempts",
        "Per-IP email verification attempts (redeem and resend combined) allowed "
        "within the window before a 429. Keyed by IP rather than by address so an "
        "attacker cannot lock a victim out of verifying their own account.",
        "integer",
        "5",
        None,
        None,
    ),
    (
        "RATE_LIMIT_VERIFICATION_WINDOW_SECONDS",
        "Rate limiting",
        "Rate limit verification window seconds",
        "Window those verification sends are counted over.",
        "integer",
        "3600",
        None,
        None,
    ),
    (
        "RATE_LIMIT_PASSWORD_RESET_MAX_ATTEMPTS",
        "Rate limiting",
        "Rate limit password reset max attempts",
        "Per-IP password reset request attempts allowed within the window before a "
        "429.",
        "integer",
        "5",
        None,
        None,
    ),
    (
        "RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS",
        "Rate limiting",
        "Rate limit password reset window seconds",
        "Window those reset requests are counted over.",
        "integer",
        "3600",
        None,
        None,
    ),
    (
        "CLIENT_ERROR_MAX_REPORTS",
        "Rate limiting",
        "Client error max reports",
        "Authenticated browser crash reports per user in one fixed window.",
        "integer",
        "20",
        1,
        1000,
    ),
    (
        "CLIENT_ERROR_WINDOW_SECONDS",
        "Rate limiting",
        "Client error window seconds",
        "Window those browser error reports are counted over, 1-3600.",
        "integer",
        "60",
        1,
        3600,
    ),
    (
        "PASSWORD_MIN_LENGTH",
        "Authentication hardening",
        "Password min length",
        "Full policy in docs/authentication.md. Minimum password length, enforced "
        "identically in registration and password change. Length leads the policy and "
        "no character composition is demanded. NIST SP 800-63B floor is 8; bcrypt "
        "truncates at 72 bytes, so values above 64 are rejected at startup.",
        "integer",
        "8",
        None,
        None,
    ),
    (
        "EMAIL_VERIFICATION_REQUIRED",
        "Authentication hardening",
        "Email verification required",
        "Whether an address must be proven reachable before the account receives its "
        "introductory and monthly credits. Unset, this follows DEPLOYMENT_MODE: true "
        "when hosted, false when self_hosted. Turning it on requires "
        "APP_PUBLIC_BASE_URL, EMAIL_FROM_ADDRESS, and SMTP_HOST, because a deployment "
        "that cannot deliver the link would create accounts nobody could finish.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "EMAIL_VERIFICATION_TOKEN_TTL_HOURS",
        "Authentication hardening",
        "Email verification token TTL hours",
        "How long an issued verification link stays redeemable. Every link is "
        "single-use regardless; this bounds how long a leaked one is worth anything.",
        "integer",
        "24",
        1,
        168,
    ),
    (
        "PASSWORD_RESET_TOKEN_TTL_MINUTES",
        "Authentication hardening",
        "Password reset token TTL minutes",
        "How long an issued password reset link stays redeemable.",
        "integer",
        "60",
        1,
        None,
    ),
    (
        "ACCESS_TOKEN_EXPIRE_MINUTES",
        "Authentication hardening",
        "Access token expire minutes",
        "Access token (JWT) lifespan. After expiration, a new login is required.",
        "integer",
        "60",
        1,
        None,
    ),
    (
        "APP_PUBLIC_BASE_URL",
        "Authentication hardening",
        "Public base URL",
        "Origin the verification link points at, without a trailing path. This is the "
        "address a user's browser reaches, which is the SPA rather than the API. "
        "Example: https://app.example.com",
        "text",
        None,
        None,
        None,
    ),
    (
        "EMAIL_FROM_ADDRESS",
        "Authentication hardening",
        "Email from address",
        "Envelope sender for verification mail. Use an address the SMTP relay is "
        "authorized to send as, or the relay will reject the message. The hosted "
        "deployment sends as info@lumina-study.com; a self-hosted one must use its "
        "own.",
        "text",
        None,
        None,
        None,
    ),
    (
        "SMTP_HOST",
        "Authentication hardening",
        "SMTP host",
        "SMTP relay. SMTP_USERNAME and SMTP_PASSWORD are optional; supply both or "
        "neither. SMTP_USE_TLS issues STARTTLS after connecting, which is what port "
        "587 expects; disable it only for a relay on the same private network.",
        "text",
        None,
        None,
        None,
    ),
    (
        "SMTP_PORT",
        "Authentication hardening",
        "SMTP port",
        "Port for the SMTP host, 1-65535. 587 is submission with STARTTLS.",
        "integer",
        "587",
        1,
        65535,
    ),
    (
        "SMTP_USERNAME",
        "Authentication hardening",
        "SMTP username",
        "SMTP account name. Username and password are both set or both empty; one "
        "without the other is refused at startup.",
        "text",
        None,
        None,
        None,
    ),
    (
        "SMTP_PASSWORD",
        "Authentication hardening",
        "SMTP password",
        "SMTP account password. Single-quote it if it contains $.",
        "text",
        None,
        None,
        None,
    ),
    (
        "SMTP_USE_TLS",
        "Authentication hardening",
        "SMTP uses TLS",
        "Whether STARTTLS is negotiated on the connection. Leave it on unless the "
        "relay is on this host.",
        "boolean",
        "true",
        None,
        None,
    ),
    (
        "SMTP_TIMEOUT_SECONDS",
        "Authentication hardening",
        "SMTP timeout seconds",
        "Budget for one SMTP conversation, 1-120. Exceeding it fails the send without "
        "failing the request that triggered it.",
        "integer",
        "10",
        1,
        120,
    ),
    (
        "SECURITY_HEADERS_ENABLED",
        "Response security headers",
        "Security headers enabled",
        "Applied by the application to every response, including the ones middleware "
        "returns before a route runs. Existing values are never overwritten, so a "
        "proxy or CDN in front of the API stays authoritative for the headers it "
        "already sets. Disable only when a boundary you control sets all of them.",
        "boolean",
        "true",
        None,
        None,
    ),
    (
        "SECURITY_HSTS_ENABLED",
        "Response security headers",
        "HSTS enabled",
        "HSTS is a promise the browser remembers for a year, so it defaults on only "
        "in hosted mode, where TLS is known to terminate in front of the API. A "
        "self-hosted deployment behind a TLS reverse proxy should set it to true; one "
        "served over plain HTTP must leave it false or browsers will refuse to reach "
        "it.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "SECURITY_HSTS_MAX_AGE_SECONDS",
        "Response security headers",
        "HSTS maximum age",
        "How long a browser should refuse plain HTTP for this host once it has seen "
        "the header. Only set a long value once TLS is permanent.",
        "integer",
        "31536000",
        None,
        None,
    ),
    (
        "LEGAL_POLICIES_ENABLED",
        "Legal policy package",
        "Legal policies enabled",
        "Publishes /legal/* pages, the legal footer, AI disclosure notices, and the "
        "registration Terms/Privacy acknowledgement. Accepts true/false or yes/no.",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "ENABLE_HOSTED_ADS",
        "Optional hosted advertising",
        "Hosted advertising enabled",
        "ENABLE_HOSTED_ADS controls the optional privacy-preserving advertising path. "
        "Strictly forbidden in self_hosted mode (setting true raises a startup "
        "error). In hosted mode, defaults to false (kill-switch ready).",
        "boolean",
        "false",
        None,
        None,
    ),
    (
        "HOSTED_ADS_PROVIDER",
        "Optional hosted advertising",
        "Hosted ads provider",
        "Which ad network serves the slots. Hosted deployments only.",
        "text",
        "ethicalads",
        None,
        None,
    ),
    (
        "HOSTED_ADS_PUBLISHER_ID",
        "Optional hosted advertising",
        "Hosted ads publisher ID",
        "Publisher identifier handed to that network. Hosted deployments only.",
        "text",
        "lumina",
        None,
        None,
    ),
)


def _kind_for(key: str, value_kind: str) -> SettingKind:
    if key in ENUM_CHOICES:
        return "enum"
    if key in JSON_KEYS:
        return "json"
    if key in LIST_KEYS:
        return "list"
    return value_kind  # type: ignore[return-value]


def _scope_for(key: str) -> SettingScope:
    if key in CONTAINER_MANAGED_KEYS:
        return SCOPE_CONTAINER_MANAGED
    if key in COMPOSE_MANAGED_KEYS:
        return SCOPE_COMPOSE_MANAGED
    return SCOPE_OVERRIDABLE


def _risk_for(key: str) -> SettingRisk:
    if key in HIGH_RISK_KEYS:
        return RISK_HIGH
    if key in MEDIUM_RISK_KEYS:
        return RISK_MEDIUM
    return RISK_LOW


def _build(row: _Row) -> SettingDefinition:
    key, section, label, help_text, value_kind, example, minimum, maximum = row
    secret = key in SECRET_KEYS
    return SettingDefinition(
        key=key,
        section=section,
        label=label,
        help=help_text,
        kind=_kind_for(key, value_kind),
        scope=_scope_for(key),
        risk=_risk_for(key),
        secret=secret,
        choices=ENUM_CHOICES.get(key, ()),
        minimum=minimum,
        maximum=maximum,
        requires_confirmation=key in CONFIRMATION_KEYS,
        example=None if secret else example,
    )


SETTINGS: tuple[SettingDefinition, ...] = tuple(_build(row) for row in _RAW)

SETTINGS_BY_KEY: dict[str, SettingDefinition] = {
    setting.key: setting for setting in SETTINGS
}

OVERRIDABLE_KEYS: frozenset[str] = frozenset(
    setting.key for setting in SETTINGS if setting.is_overridable
)

ALL_KEYS: frozenset[str] = frozenset(SETTINGS_BY_KEY)


def get(key: str) -> SettingDefinition | None:
    return SETTINGS_BY_KEY.get(key)


def is_overridable(key: str) -> bool:
    setting = SETTINGS_BY_KEY.get(key)
    return setting is not None and setting.is_overridable
