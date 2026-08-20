# Lumina AWS infrastructure (Terraform)

Terraform configuration for the hosted production deployment described in
`docs/deployment.md` (SCRUM-92). It provisions the full AWS topology for the
same image used by `docker-compose.hosted.yml`:

- VPC with public/private subnets, a single NAT gateway, and route tables;
- ECR repository with immutable tags, scanning, and lifecycle retention;
- S3 bucket for uploaded documents (versioned, encrypted, deny-insecure-TLS);
- RDS PostgreSQL with pgvector 0.8+, storage autoscaling, and a TLS-only RDS
  Proxy for API/worker connection pooling;
- ECS Fargate services `api` (behind an ALB with ACM TLS and autoscaling) and
  `worker`, plus a one-off `migrate` task definition;
- an optional Route53 alias to the ALB.

The initial ECS task definitions read runtime secrets from AWS Systems Manager
Parameter Store paths under `/<project>-<environment>/` (for example
`/lumina-production/jwt-secret-key`). The secrets module (SCRUM-94) creates
those parameters; tasks retry until they exist.

## Prerequisites

- Terraform 1.9 or newer (`terraform` is not required on the machine that runs
  CI; validate with the pinned `hashicorp/terraform` image from Docker).
- AWS credentials with permission to create the listed resources.
- An existing ACM certificate for the HTTPS listener
  (`acm_certificate_arn`), and optionally a Route53 hosted zone
  (`route53_zone_id`).
- A private `terraform.tfvars` (never committed) holding
  `bootstrap_admin_email` and any non-default variables.

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
accepts only `sts.amazonaws.com` audiences and only the `main` branch of
`github_repository`. The role can push to ECR, register and run ECS task
definitions, update the two services, and pass the two ECS roles; it cannot
read the runtime secrets. Set the role ARN as the `AWS_DEPLOY_ROLE_ARN`
secret on the `production` environment, and the Terraform outputs as the
deploy workflow environment variables (see `docs/deployment.md`).

## Outputs used by the deploy pipeline

`ecr_repository_url`, `ecs_cluster_name`, `api_service_name`,
`worker_service_name`, `api_task_definition_family`,
`worker_task_definition_family`, `migrate_task_definition_family`, and
`alb_dns_name` feed the SCRUM-93 deploy workflow. The workflow registers new
task definition revisions with the image tag of the commit it builds; it does
not re-run Terraform. For the first rollout the task definitions must point at
an image that exists in ECR: either set `image_tag` to the first deployed
SHA at apply time, or push a `latest`-tagged image manually before the first
deploy.

## Notes

- The ALB health check targets `GET /health/ready` on the container port, the
  same probe used by Compose.
- API autoscaling is CPU-based target tracking between `api_min_instances` and
  `api_max_instances`. The worker stays at a single task: it is a durable
  single-consumer job processor.
- `terraform destroy` refuses to delete the protected RDS instance and ALB
  until protection is lifted; that is deliberate.
- The state bucket is configured with `backend "s3" {}` and the concrete
  settings come from `-backend-config`; never commit state files.
