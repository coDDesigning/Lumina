# Lumina AWS infrastructure (Terraform)

Terraform configuration for the hosted production deployment described in
`docs/deployment.md`. It provisions the backend roles from the same image used
by `docker-compose.hosted.yml` and the separate static frontend delivery path:

- VPC with public/private subnets, a single NAT gateway, and route tables;
- ECR repository with immutable tags, scanning, and lifecycle retention;
- separate S3 buckets for uploaded documents and private frontend releases;
- CloudFront with OAC-backed static delivery and uncached `/api` routing to the
  ALB;
- RDS PostgreSQL with pgvector 0.8+, storage autoscaling, and a TLS-only RDS
  Proxy for API/worker connection pooling;
- ECS Fargate services `api` (behind an ALB with ACM TLS and autoscaling) and
  `worker`, plus a one-off `migrate` task definition;
- optional Route53 aliases from the public frontend hostname to CloudFront and
  from a distinct API-origin hostname to the ALB;
- CloudWatch JSON logs, dashboard, worker EMF metrics, alarms, and an SNS topic.

The initial ECS task definitions read runtime secrets from AWS Systems Manager
Parameter Store paths under `/<project>-<environment>/` (for example
`/lumina-production/jwt-secret-key`). The secrets module (SCRUM-94) creates
those parameters; tasks retry until they exist.

## Prerequisites

- Terraform 1.12.2, matching the verified CI installation.
- AWS credentials with permission to create the listed resources.
- An existing regional ACM certificate supplied as `acm_certificate_arn` for
  the ALB. It must cover both `frontend_domain_name` and
  `api_origin_domain_name` during the staged DNS transition; a wildcard or SAN
  certificate is suitable. After cutover only the API-origin name reaches the
  ALB.
- An existing ACM certificate in `us-east-1` for `frontend_domain_name`,
  supplied as `cloudfront_certificate_arn`.
- DNS control for both names. When `route53_zone_id` is empty, create the
  records with the external DNS provider.
- A GitHub `production` environment whose deployment branch policy permits
  `main` only, configured before applying the OIDC trust policy.
- A private `terraform.tfvars` (never committed) holding
  `bootstrap_admin_email` and any non-default variables.

To persist operational provider-cost estimates, set `ai_model_cost_rates` to
the same versioned JSON contract documented in `docs/ai_providers.md`. Leaving
it empty keeps generations explicitly unpriced.

## Apply

```bash
cd terraform
terraform init \
  -backend-config="bucket=<state-bucket>" \
  -backend-config="key=lumina/terraform.tfstate" \
  -backend-config="region=<region>"
terraform plan
terraform apply
```

Before the first plan that introduces frontend delivery, inspect the existing
DNS state:

```bash
terraform state show 'module.alb.aws_route53_record.app[0]'
```

The checked-in `moved` block transfers that application A record to the
frontend module rather than deleting and recreating it. The first apply keeps
the alias on the ALB because `frontend_dns_cutover` defaults to `false`, while
creating CloudFront, the private bucket, and the distinct API-origin record.
Set `dns_record_name` to the existing record's full hostname so the moved
resource preserves its ForceNew name attribute. Leave it empty only when the
existing record already equals `frontend_domain_name`.
Review the plan to ensure it does not replace the ALB, ECS services, document
bucket, or application DNS record. Saved plans can contain secrets; do not
commit or upload them.

Use a two-phase first rollout so an empty bucket never receives user traffic:

1. Apply with `frontend_dns_cutover = false`.
2. Set the deploy workflow's `FRONTEND_URL` to the `cloudfront_url` output and
   deploy one release, which publishes `current/index.html` and verifies the
   distribution directly.
3. Apply with `frontend_dns_cutover = true`; the managed A record moves to
   CloudFront and its AAAA record is created.
4. Set `FRONTEND_URL` to the public `frontend_url` output for subsequent
   production smoke tests.

Externally managed DNS must follow the same sequence outside Terraform.

## Hosted frontend routing decision

Hosted production uses one browser origin:

```text
app.example.com -> CloudFront
  /assets/*     -> private frontend S3 bucket through OAC
  /api          -> HTTPS ALB origin, caching disabled
  /api/*        -> HTTPS ALB origin, caching disabled
  everything else -> SPA index.html in the frontend bucket

api-origin.example.com -> ALB
```

The distinct API-origin hostname prevents a DNS loop after the public
application hostname moves to CloudFront and gives CloudFront a hostname that
matches the regional ALB certificate. CloudFront forwards API requests with
the managed `AllViewerExceptHostHeader` policy so bearer `Authorization`
headers and query strings reach ECS while the origin receives its own Host
header. A viewer-request function performs SPA rewrites only on the static
default behavior; there is no distribution-wide error rewrite that could turn
an API 401, 404, or 409 into `index.html`. A response-headers policy adds HSTS,
CSP, framing, content-type, and referrer protections on every behavior.

The frontend bucket is not an S3 website and is never public. CloudFront reads
only through OAC. Active files live below `current/`; immutable frontend
archives and sanitized ECS task-definition documents live below
`releases/<commit-sha>/` for paired frontend/backend rollback. Release archives
expire after 180 days and noncurrent versions after 30 days.

The `vector` extension is installed and upgraded by Alembic; it is not a
`shared_preload_libraries` entry. The ECS services start before the SSM
parameters exist; they retry automatically, so run the secrets step
(SCRUM-94) before the first deploy pipeline run.

## Runtime secret paths

| SSM parameter | Purpose |
| --- | --- |
| `/<prefix>/jwt-secret-key` | `JWT_SECRET_KEY`, min 32 characters |
| `/<prefix>/bootstrap-admin-token` | `BOOTSTRAP_ADMIN_TOKEN`, min 32 visible ASCII |
| `/<prefix>/gemini-api-key` | `GEMINI_API_KEY` for hosted AI and embeddings |

Database values are stored in three Secrets Manager entries:

- `<prefix>/runtime-database-url` targets RDS Proxy and is injected into API
  and worker tasks;
- `<prefix>/database-url` targets RDS directly and is injected only into the
  one-shot migrator; and
- `<prefix>/database-credentials` is readable only by RDS Proxy.

Both URLs require TLS. Runtime connection counts are bounded per process by
`DATABASE_POOL_SIZE` plus `DATABASE_MAX_OVERFLOW`, while the proxy reserves a
configurable percentage of RDS connections for administrative and migration
work.

The parameters are created by the secrets module from the `runtime_secrets`
map. Provide the values in a private `terraform.tfvars` (never committed) and
apply:

```hcl
runtime_secrets = {
  "jwt-secret-key"        = "<generated, at least 32 chars>"
  "bootstrap-admin-token" = "<generated, at least 32 visible ASCII>"
  "gemini-api-key"        = "<Gemini API key>"
}
```

Apply `terraform` once after both the infrastructure and secrets modules are
in place; the ECS services retry task starts until the parameters exist.

## Deploy role (GitHub OIDC)

The `github-oidc` module creates the OpenID Connect provider for
`token.actions.githubusercontent.com` and an IAM role
(`<prefix>-github-actions`) that the deploy workflow assumes. The trust policy
accepts only `sts.amazonaws.com` audiences and the exact
`github_environment_name` subject. Configure that GitHub environment to allow
deployments from `main` only before applying the role; the workflow independently
rejects non-`main` refs. The role can publish the frontend, invalidate its
distribution, push to the one ECR repository, register and run ECS task
definitions, update the two services, and pass the two ECS roles only to ECS;
it cannot read runtime secrets. Set the role ARN as `AWS_DEPLOY_ROLE_ARN` on
the production environment and set the Terraform outputs as its variables.

## Outputs used by the deploy pipeline

`ecr_repository_url`, `ecs_cluster_name`, `api_service_name`,
`worker_service_name`, `api_task_definition_family`,
`worker_task_definition_family`, `migrate_task_definition_family`,
`private_subnet_ids_csv`, `ecs_security_group_id`, `frontend_bucket_name`,
`cloudfront_distribution_id`, and `frontend_url` feed the deploy workflow. Use
`cloudfront_url` instead of `frontend_url` for the first pre-cutover deploy. The
workflow uses one commit SHA for the backend image and frontend release, and
does not re-run Terraform. For the first apply the initial task definitions
must still point at an image that exists in ECR; set Terraform's `image_tag` or
seed that image before creating the ECS services.

## Notes

- The ALB health check targets `GET /health/ready` on the container port, the
  same probe used by Compose.
- Set `alarm_email` to subscribe an operator to alarm and recovery events; SNS
  requires the recipient to confirm the subscription.
- API autoscaling is CPU-based target tracking between `api_min_instances` and
  `api_max_instances`. Workers scale between `worker_min_instances` and
  `worker_max_instances` on the maximum oldest queued-job age. PostgreSQL
  `SKIP LOCKED`, claim tokens, and expiring leases make concurrent claims safe;
  self-hosted SQLite/Chroma is not horizontally scaled.
- `terraform destroy` refuses to delete the protected RDS instance and ALB
  until protection is lifted; that is deliberate.
- The state bucket is configured with `backend "s3" {}` and the concrete
  settings come from `-backend-config`; never commit state files.
