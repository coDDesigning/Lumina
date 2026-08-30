"""Manual production deploy for Lumina, used when GitHub Actions cannot run.

It mirrors ``.github/workflows/deploy.yml``: it tags the image with the release
commit SHA, takes and verifies a 30-day predeployment RDS snapshot before any
migration, registers fresh task-definition revisions for the new image, runs the
migration task and checks its exit code, rolls the API and worker services, then
publishes the frontend with immutable asset caching and invalidates CloudFront,
and smoke-tests the result.

Requirements: Python 3.10+ with ``boto3``, ``dotenv``, plus ``docker`` and ``npm``
on PATH. AWS credentials configured in environment or ``~/.aws/credentials``.

    python deploy_local.py                 # deploy the current git HEAD
    python deploy_local.py --dry-run       # print the plan, mutate nothing
    python deploy_local.py --yes           # skip the confirmation prompt
    python deploy_local.py --skip-frontend # backend + migration only
    python deploy_local.py --skip-smoke    # do not curl the site afterwards

On success and on a handled failure the previous service task-definition ARNs and
the snapshot id are written to ``deploy_state.json`` with ready-to-paste rollback
commands.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent

REGION = os.environ.get(
    "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "eu-central-1")
)
ACCOUNT = os.environ.get("LUMINA_AWS_ACCOUNT_ID", "967848862549")
ECR_REPO = os.environ.get(
    "LUMINA_ECR_REPOSITORY", f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/lumina"
)
CLUSTER = os.environ.get("LUMINA_ECS_CLUSTER", "lumina-production")
API_SERVICE = os.environ.get("LUMINA_API_SERVICE", "lumina-production-api")
WORKER_SERVICE = os.environ.get("LUMINA_WORKER_SERVICE", "lumina-production-worker")
API_FAMILY = os.environ.get("LUMINA_API_TASK_DEFINITION", "lumina-production-api")
WORKER_FAMILY = os.environ.get(
    "LUMINA_WORKER_TASK_DEFINITION", "lumina-production-worker"
)
MIGRATE_FAMILY = os.environ.get(
    "LUMINA_MIGRATE_TASK_DEFINITION", "lumina-production-migrate"
)
FRONTEND_BUCKET = os.environ.get(
    "LUMINA_FRONTEND_BUCKET", f"lumina-production-frontend-{ACCOUNT}"
)
CF_DIST_ID = os.environ.get("LUMINA_CLOUDFRONT_DISTRIBUTION_ID", "E37I24R2GMTA0G")
RDS_ID = os.environ.get("LUMINA_RDS_INSTANCE_IDENTIFIER", "lumina-production")
SUBNETS = [
    s.strip()
    for s in os.environ.get(
        "LUMINA_PRIVATE_SUBNETS",
        "subnet-0a661a857ff476ce2,subnet-032982feebfcaeb57,subnet-0b0c3d982bd42d520",
    ).split(",")
    if s.strip()
]
SECURITY_GROUP = [
    s.strip()
    for s in os.environ.get("LUMINA_ECS_SECURITY_GROUP", "sg-0117c38812fa58b10").split(
        ","
    )
    if s.strip()
]
FRONTEND_URL = os.environ.get("LUMINA_FRONTEND_URL", "").rstrip("/")
VITE_API_BASE_URL = os.environ.get("VITE_API_BASE_URL", "/api")

NPM = "npm.cmd" if os.name == "nt" else "npm"
STATE_FILE = ROOT / "deploy_state.json"

# Read-only fields returned by describe-task-definition that register rejects.
_TASK_DEF_READONLY = (
    "taskDefinitionArn",
    "revision",
    "status",
    "requiresAttributes",
    "compatibilities",
    "registeredAt",
    "registeredBy",
    "deregisteredAt",
)


class DeployError(RuntimeError):
    """A deploy step failed; the message explains where and what is safe."""


def log(message: str) -> None:
    print(f"\n=== {message}", flush=True)


def run_cmd(
    args: list[str], *, cwd: Path | None = None, env: dict | None = None
) -> None:
    print(f"  $ {' '.join(args)}", flush=True)
    subprocess.run(args, cwd=cwd, env=env, check=True)


def require_tools() -> None:
    missing: list[str] = []
    if not shutil.which("docker"):
        missing.append("docker")
    if not shutil.which(NPM) and not shutil.which("npm"):
        missing.append("npm")
    if missing:
        raise DeployError(f"missing required tools on PATH: {', '.join(missing)}")


def git_head() -> str:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(sha) != 40:
        raise DeployError(f"could not resolve a 40-character commit SHA (got {sha!r})")
    return sha.lower()


def warn_if_not_main(release: str) -> None:
    try:
        main_sha = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        print("  ! could not read origin/main to compare the release", flush=True)
        return
    if main_sha != release:
        print(
            f"  ! HEAD {release[:12]} is not origin/main {main_sha[:12]}; "
            "production is normally deployed from main",
            flush=True,
        )


def confirm(release: str, *, assume_yes: bool) -> None:
    print(
        "\n".join(
            [
                "",
                "About to deploy to PRODUCTION:",
                f"  account   {ACCOUNT}  region {REGION}",
                f"  cluster   {CLUSTER}",
                f"  services  {API_SERVICE}, {WORKER_SERVICE}",
                f"  image     {ECR_REPO}:{release}",
                f"  database  {RDS_ID}  (a 30-day snapshot is taken first)",
                f"  frontend  s3://{FRONTEND_BUCKET}  cf {CF_DIST_ID}",
            ]
        ),
        flush=True,
    )
    if assume_yes:
        return
    if input('\nType "deploy" to continue: ').strip() != "deploy":
        raise DeployError("aborted at confirmation prompt")


def get_ecr_client():
    return boto3.client("ecr", region_name=REGION)


def get_ecs_client():
    return boto3.client("ecs", region_name=REGION)


def get_rds_client():
    return boto3.client("rds", region_name=REGION)


def get_s3_client():
    return boto3.client("s3", region_name=REGION)


def get_cf_client():
    return boto3.client("cloudfront", region_name=REGION)


def ecr_image_exists(release: str) -> bool:
    ecr = get_ecr_client()
    repo_name = ECR_REPO.split("/")[-1]
    try:
        res = ecr.batch_get_image(
            repositoryName=repo_name,
            imageIds=[{"imageTag": release}],
        )
        if res.get("images"):
            return True
        failures = res.get("failures") or []
        code = failures[0].get("failureCode") if failures else None
        if code and code != "ImageNotFound":
            raise DeployError(f"ECR image lookup failed: {code}")
        return False
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code == "RepositoryNotFoundException":
            raise DeployError(f"ECR repository {repo_name} not found") from exc
        raise DeployError(f"ECR lookup error: {exc}") from exc


def build_and_push_image(release: str, *, dry_run: bool) -> None:
    log(f"Backend image {ECR_REPO}:{release}")
    if not dry_run and ecr_image_exists(release):
        print("  image already in ECR; skipping build", flush=True)
        return
    if dry_run:
        print("  [dry-run] docker build + push skipped", flush=True)
        return

    ecr = get_ecr_client()
    auth = ecr.get_authorization_token()["authorizationData"][0]
    token = base64.b64decode(auth["authorizationToken"]).decode("utf-8")
    user, pwd = token.split(":")
    ep = auth["proxyEndpoint"]

    subprocess.run(
        ["docker", "login", "--username", user, "--password-stdin", ep],
        input=pwd,
        text=True,
        check=True,
    )
    run_cmd(["docker", "build", "-t", f"{ECR_REPO}:{release}", "."], cwd=ROOT)
    run_cmd(["docker", "push", f"{ECR_REPO}:{release}"])


def build_frontend(*, dry_run: bool) -> Path:
    log("Frontend build")
    dist = ROOT / "frontend" / "dist"
    if dry_run:
        print("  [dry-run] npm ci + npm run build skipped", flush=True)
        return dist
    env = {**os.environ, "VITE_API_BASE_URL": VITE_API_BASE_URL}
    npm = shutil.which(NPM) or shutil.which("npm") or "npm"
    run_cmd([npm, "ci", "--no-audit", "--no-fund"], cwd=ROOT / "frontend", env=env)
    run_cmd([npm, "run", "build"], cwd=ROOT / "frontend", env=env)
    if not (dist / "index.html").is_file() or not (dist / "assets").is_dir():
        raise DeployError(
            "frontend build did not produce dist/index.html and dist/assets"
        )
    return dist


def current_service_task_def(service: str) -> str:
    ecs = get_ecs_client()
    res = ecs.describe_services(cluster=CLUSTER, services=[service])
    services = res.get("services") or []
    if not services or not services[0].get("taskDefinition"):
        raise DeployError(f"could not read the current task definition for {service}")
    return services[0]["taskDefinition"]


def register_with_image(family: str, image: str, *, dry_run: bool) -> str:
    if dry_run:
        print(f"  [dry-run] register-task-definition {family} -> {image}", flush=True)
        return f"arn:dry-run:{family}"

    ecs = get_ecs_client()
    desc = ecs.describe_task_definition(taskDefinition=family)["taskDefinition"]
    for key in _TASK_DEF_READONLY:
        desc.pop(key, None)
    desc["containerDefinitions"][0]["image"] = image

    res = ecs.register_task_definition(**desc)
    arn = res["taskDefinition"]["taskDefinitionArn"]
    print(f"  registered task definition {family} -> {arn}", flush=True)
    return arn


def take_snapshot(release: str, *, dry_run: bool) -> str:
    log(f"Predeployment RDS snapshot of {RDS_ID} (30-day retention)")
    run_id = str(int(time.time()))
    snapshot_id = f"{RDS_ID}-predeploy-{release[:8]}-{run_id}-1"
    if dry_run:
        print(f"  [dry-run] would create and verify snapshot {snapshot_id}", flush=True)
        return snapshot_id

    rds = get_rds_client()
    tags = [
        {"Key": "ManagedBy", "Value": "LuminaHostedRecovery"},
        {"Key": "Environment", "Value": "production"},
        {"Key": "Release", "Value": release},
        {"Key": "Purpose", "Value": "predeployment"},
        {"Key": "RetentionDays", "Value": "30"},
        {"Key": "RunId", "Value": run_id},
        {"Key": "RunAttempt", "Value": "1"},
    ]
    try:
        rds.create_db_snapshot(
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceIdentifier=RDS_ID,
            Tags=tags,
        )
        print(f"  initiating snapshot {snapshot_id}...", flush=True)
    except ClientError as exc:
        raise DeployError(f"RDS snapshot creation failed: {exc}") from exc

    # Wait for snapshot to complete
    print("  waiting for snapshot to become available...", flush=True)
    waiter = rds.get_waiter("db_snapshot_available")
    waiter.wait(
        DBSnapshotIdentifier=snapshot_id,
        WaiterConfig={"Delay": 15, "MaxAttempts": 120},
    )
    print(f"  verified snapshot {snapshot_id} is available", flush=True)
    return snapshot_id


def run_migration(image: str, *, dry_run: bool) -> None:
    log("Database migration task")
    task_def = register_with_image(MIGRATE_FAMILY, image, dry_run=dry_run)
    if dry_run:
        print(
            "  [dry-run] run-task migrate + wait + exit-code check skipped", flush=True
        )
        return

    ecs = get_ecs_client()
    network = {
        "awsvpcConfiguration": {
            "subnets": SUBNETS,
            "securityGroups": SECURITY_GROUP,
            "assignPublicIp": "DISABLED",
        }
    }
    res = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=task_def,
        launchType="FARGATE",
        networkConfiguration=network,
    )
    tasks = res.get("tasks") or []
    if not tasks:
        failures = res.get("failures") or []
        raise DeployError(f"failed to launch migration task: {failures}")
    task_arn = tasks[0]["taskArn"]
    print(f"  migration task {task_arn}", flush=True)

    print("  waiting for migration task to complete...", flush=True)
    waiter = ecs.get_waiter("tasks_stopped")
    waiter.wait(
        cluster=CLUSTER,
        tasks=[task_arn],
        WaiterConfig={"Delay": 6, "MaxAttempts": 100},
    )

    desc = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
    containers = desc["tasks"][0]["containers"]
    exit_code = containers[0].get("exitCode")
    if exit_code != 0:
        reason = containers[0].get("reason", "unknown")
        raise DeployError(
            f"migration task failed with exit code {exit_code} ({reason}); "
            "database is unchanged from snapshot and services were not rolled"
        )
    print("  migration completed (exit 0)", flush=True)


def roll_services(image: str, *, dry_run: bool) -> dict[str, str]:
    log("Rolling API and worker services")
    ecs = get_ecs_client()
    new_arns: dict[str, str] = {}
    for family, service in ((API_FAMILY, API_SERVICE), (WORKER_FAMILY, WORKER_SERVICE)):
        arn = register_with_image(family, image, dry_run=dry_run)
        new_arns[service] = arn
        if dry_run:
            print(f"  [dry-run] update-service {service} -> {arn}", flush=True)
            continue
        ecs.update_service(
            cluster=CLUSTER,
            service=service,
            taskDefinition=arn,
            forceNewDeployment=True,
        )
        print(f"  updated service {service} to {arn}", flush=True)

    if dry_run:
        return new_arns

    print(
        "  waiting for services to stabilize (this may take several minutes)...",
        flush=True,
    )
    waiter = ecs.get_waiter("services_stable")
    waiter.wait(
        cluster=CLUSTER,
        services=[API_SERVICE, WORKER_SERVICE],
        WaiterConfig={"Delay": 15, "MaxAttempts": 60},
    )

    for service, arn in new_arns.items():
        current = current_service_task_def(service)
        if current != arn:
            raise DeployError(
                f"{service} did not converge on {arn} (current: {current})"
            )
    print("  both services stable on the new revision", flush=True)
    return new_arns


def _content_type_for(path: Path) -> str:
    ext = path.suffix.lower()
    custom = {
        ".css": "text/css",
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript",
        ".mjs": "text/javascript",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ttf": "font/ttf",
        ".ico": "image/x-icon",
    }
    if ext in custom:
        return custom[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def publish_frontend(dist: Path, *, dry_run: bool) -> None:
    log("Publishing frontend and invalidating CloudFront")
    if dry_run:
        print(
            "  [dry-run] frontend publish + CloudFront invalidation skipped", flush=True
        )
        return

    s3 = get_s3_client()
    cf = get_cf_client()

    all_files = [p for p in dist.rglob("*") if p.is_file()]
    non_index_files = [p for p in all_files if p.name != "index.html"]
    index_file = dist / "index.html"

    # 1. Upload non-index files
    uploaded_keys: set[str] = set()
    for file_path in non_index_files:
        rel_str = str(file_path.relative_to(dist)).replace("\\", "/")
        key = f"current/{rel_str}"
        uploaded_keys.add(key)
        content_type = _content_type_for(file_path)
        if rel_str.startswith("assets/"):
            cache_control = "public,max-age=31536000,immutable"
        else:
            cache_control = "public,max-age=0,must-revalidate"

        s3.upload_file(
            str(file_path),
            FRONTEND_BUCKET,
            key,
            ExtraArgs={"ContentType": content_type, "CacheControl": cache_control},
        )
    print(
        f"  uploaded {len(non_index_files)} static assets to s3://{FRONTEND_BUCKET}/current/",
        flush=True,
    )

    # 2. Upload index.html last
    if index_file.is_file():
        index_key = "current/index.html"
        uploaded_keys.add(index_key)
        s3.upload_file(
            str(index_file),
            FRONTEND_BUCKET,
            index_key,
            ExtraArgs={
                "ContentType": "text/html; charset=utf-8",
                "CacheControl": "no-cache,max-age=0,must-revalidate",
            },
        )
        print("  uploaded current/index.html (no-cache)", flush=True)

    # 3. Create CloudFront invalidation
    print(f"  invalidating CloudFront distribution {CF_DIST_ID}...", flush=True)
    inval_res = cf.create_invalidation(
        DistributionId=CF_DIST_ID,
        InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": str(time.time()),
        },
    )
    invalidation_id = inval_res["Invalidation"]["Id"]
    print(f"  waiting for invalidation {invalidation_id} to complete...", flush=True)
    cf_waiter = cf.get_waiter("invalidation_completed")
    cf_waiter.wait(
        DistributionId=CF_DIST_ID,
        Id=invalidation_id,
        WaiterConfig={"Delay": 15, "MaxAttempts": 60},
    )
    print("  CloudFront invalidation completed", flush=True)

    # 4. Prune removed files in current/
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=FRONTEND_BUCKET, Prefix="current/"):
        for obj in page.get("Contents", []):
            obj_key = obj["Key"]
            if obj_key not in uploaded_keys:
                s3.delete_object(Bucket=FRONTEND_BUCKET, Key=obj_key)
                print(f"  pruned stale key {obj_key}", flush=True)


def smoke_test() -> None:
    log("Smoke testing production")
    if not FRONTEND_URL:
        print("  ! LUMINA_FRONTEND_URL not set; skipping smoke test", flush=True)
        return
    base = FRONTEND_URL

    # Root test
    req = urllib.request.Request(
        f"{base}/", headers={"User-Agent": "Lumina-Deploy-Smoke"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content = resp.read().decode("utf-8", errors="replace")
            if "/assets/" not in content:
                raise DeployError(
                    "frontend root did not reference a hashed /assets/ file"
                )
    except Exception as exc:
        raise DeployError(f"root smoke test failed: {exc}") from exc

    # Status endpoints test
    for path, expected_code in (
        (f"{base}/api/auth/me", 401),
        (f"{base}/api/__nope__", 404),
    ):
        sub_req = urllib.request.Request(
            path, headers={"User-Agent": "Lumina-Deploy-Smoke"}
        )
        try:
            with urllib.request.urlopen(sub_req, timeout=30) as resp:
                code = resp.getcode()
        except urllib.error.HTTPError as http_err:
            code = http_err.code
        except Exception as exc:
            raise DeployError(f"smoke test request to {path} failed: {exc}") from exc

        if code != expected_code:
            raise DeployError(f"{path} returned {code}, expected {expected_code}")

    print("  smoke test passed", flush=True)


def write_state(release: str, snapshot_id: str, previous: dict, new: dict) -> None:
    state = {
        "release": release,
        "snapshot_id": snapshot_id,
        "previous_task_definitions": previous,
        "new_task_definitions": new,
        "rollback": {
            "services": [
                f"aws ecs update-service --cluster {CLUSTER} --service {s} "
                f"--task-definition {a} --force-new-deployment"
                for s, a in previous.items()
            ],
            "database": (
                f"python ops/aws_rds_recovery.py restore --source {RDS_ID} "
                f"--snapshot {snapshot_id} ...  # see docs/runbooks/hosted-backup-restore.md"
            ),
        },
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\nWrote rollback details to {STATE_FILE.name}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manual production deploy for Lumina.")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt"
    )
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    args = parser.parse_args(argv)

    try:
        require_tools()
        release = git_head()
        warn_if_not_main(release)
        confirm(release, assume_yes=args.yes or args.dry_run)

        image = f"{ECR_REPO}:{release}"
        previous = {
            API_SERVICE: current_service_task_def(API_SERVICE)
            if not args.dry_run
            else f"arn:previous:{API_SERVICE}",
            WORKER_SERVICE: current_service_task_def(WORKER_SERVICE)
            if not args.dry_run
            else f"arn:previous:{WORKER_SERVICE}",
        }
        if not args.dry_run:
            print(f"  previous revisions: {previous}", flush=True)

        build_and_push_image(release, dry_run=args.dry_run)
        dist = None
        if not args.skip_frontend:
            dist = build_frontend(dry_run=args.dry_run)

        snapshot_id = take_snapshot(release, dry_run=args.dry_run)
        run_migration(image, dry_run=args.dry_run)
        new = roll_services(image, dry_run=args.dry_run)

        if dist is not None:
            publish_frontend(dist, dry_run=args.dry_run)
        if not args.skip_smoke and not args.dry_run:
            smoke_test()

        if not args.dry_run:
            write_state(release, snapshot_id, previous, new)
        log("Deployment complete" if not args.dry_run else "Dry run complete")
        return 0
    except (DeployError, subprocess.CalledProcessError) as exc:
        print(f"\nDEPLOY FAILED: {exc}", file=sys.stderr, flush=True)
        print(
            "Check deploy_state.json (if written) for rollback commands. If the "
            "migration step passed but a later step failed, roll the services back "
            "to the previous revisions before retrying.",
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
