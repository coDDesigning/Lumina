"""Manual production deploy for Lumina, used only when GitHub Actions cannot run.

It mirrors ``.github/workflows/deploy.yml``: it tags the image with the release
commit SHA, takes and verifies a 30-day predeployment RDS snapshot before any
migration, registers fresh task-definition revisions for the new image, runs the
migration task and checks its exit code, rolls the API and worker services, then
publishes the frontend through the same ``publish-frontend.sh`` the pipeline uses
and smoke-tests the result.

Requirements on PATH: ``aws``, ``docker``, ``npm``, ``bash``, ``jq``. AWS
credentials must be configured for a principal allowed to push to ECR, register
and update ECS services, write the frontend bucket, invalidate CloudFront, and
create RDS snapshots.

    python deploy_local.py                 # deploy the current git HEAD
    python deploy_local.py --dry-run       # print the plan, mutate nothing
    python deploy_local.py --yes           # skip the confirmation prompt
    python deploy_local.py --skip-frontend # backend + migration only
    python deploy_local.py --skip-smoke    # do not curl the site afterwards

On success and on a handled failure the previous service task-definition ARNs and
the snapshot id are written to ``deploy_state.json`` with ready-to-paste rollback
commands. The database is recoverable from the snapshot through
``ops/aws_rds_recovery.py restore`` (docs/runbooks/hosted-backup-restore.md).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

REGION = os.environ.get("AWS_REGION", "eu-central-1")
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
SUBNETS = os.environ.get(
    "LUMINA_PRIVATE_SUBNETS",
    "subnet-0a661a857ff476ce2,subnet-032982feebfcaeb57,subnet-0b0c3d982bd42d520",
)
SECURITY_GROUP = os.environ.get("LUMINA_ECS_SECURITY_GROUP", "sg-0117c38812fa58b10")
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


def run(args: list[str], *, cwd: Path | None = None, env: dict | None = None) -> None:
    print(f"  $ {' '.join(args)}", flush=True)
    subprocess.run(args, cwd=cwd, env=env, check=True)


def aws(*args: str, parse: bool = True):
    command = ["aws", "--region", REGION, "--no-cli-pager", *args]
    if parse:
        command += ["--output", "json"]
    print(f"  $ {' '.join(command)}", flush=True)
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if parse else result.stdout.strip()


def require_tools() -> None:
    missing = [
        tool for tool in ("aws", "docker", "bash", "jq") if not shutil.which(tool)
    ]
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


def ecr_image_exists(release: str) -> bool:
    result = aws(
        "ecr",
        "batch-get-image",
        "--repository-name",
        ECR_REPO.split("/")[-1],
        "--image-ids",
        f"imageTag={release}",
    )
    if result.get("images"):
        return True
    failures = result.get("failures") or []
    code = failures[0].get("failureCode") if failures else None
    if code and code != "ImageNotFound":
        raise DeployError(f"ECR image lookup failed: {code}")
    return False


def build_and_push_image(release: str, *, dry_run: bool) -> None:
    log(f"Backend image {ECR_REPO}:{release}")
    if not dry_run and ecr_image_exists(release):
        print("  image already in ECR; skipping build", flush=True)
        return
    if dry_run:
        print("  [dry-run] docker build + push skipped", flush=True)
        return
    password = subprocess.run(
        ["aws", "ecr", "get-login-password", "--region", REGION],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["docker", "login", "--username", "AWS", "--password-stdin", ECR_REPO],
        input=password,
        text=True,
        check=True,
    )
    run(["docker", "build", "-t", f"{ECR_REPO}:{release}", "."], cwd=ROOT)
    run(["docker", "push", f"{ECR_REPO}:{release}"])


def build_frontend(*, dry_run: bool) -> Path:
    log("Frontend build")
    dist = ROOT / "frontend" / "dist"
    if dry_run:
        print("  [dry-run] npm ci + npm run build skipped", flush=True)
        return dist
    env = {**os.environ, "VITE_API_BASE_URL": VITE_API_BASE_URL}
    npm = shutil.which(NPM) or "npm"
    run([npm, "ci", "--no-audit", "--no-fund"], cwd=ROOT / "frontend", env=env)
    run([npm, "run", "build"], cwd=ROOT / "frontend", env=env)
    if not (dist / "index.html").is_file() or not (dist / "assets").is_dir():
        raise DeployError(
            "frontend build did not produce dist/index.html and dist/assets"
        )
    return dist


def current_service_task_def(service: str) -> str:
    described = aws(
        "ecs",
        "describe-services",
        "--cluster",
        CLUSTER,
        "--services",
        service,
        "--query",
        "services[0].taskDefinition",
    )
    if not described or described == "None":
        raise DeployError(f"could not read the current task definition for {service}")
    return described


def register_with_image(family: str, image: str, *, dry_run: bool) -> str:
    described = aws(
        "ecs",
        "describe-task-definition",
        "--task-definition",
        family,
        "--query",
        "taskDefinition",
    )
    for key in _TASK_DEF_READONLY:
        described.pop(key, None)
    described["containerDefinitions"][0]["image"] = image
    if dry_run:
        print(f"  [dry-run] register-task-definition {family} -> {image}", flush=True)
        return f"arn:dry-run:{family}"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(described, handle)
        path = handle.name
    try:
        return aws(
            "ecs",
            "register-task-definition",
            "--cli-input-json",
            f"file://{path}",
            "--query",
            "taskDefinition.taskDefinitionArn",
            parse=True,
        )
    finally:
        os.unlink(path)


def take_snapshot(release: str, *, dry_run: bool) -> str:
    log(f"Predeployment RDS snapshot of {RDS_ID} (30-day retention)")
    run_id = str(int(time.time()))
    snapshot_id = f"{RDS_ID}-predeploy-{release[:8]}-{run_id}-1"
    if dry_run:
        print(f"  [dry-run] would create and verify snapshot {snapshot_id}", flush=True)
        return snapshot_id
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ops" / "aws_rds_recovery.py"),
            "create-snapshot",
            "--source",
            RDS_ID,
            "--snapshot",
            snapshot_id,
            "--release",
            release,
            "--run-id",
            run_id,
            "--run-attempt",
            "1",
            "--purpose",
            "predeployment",
            "--retention-days",
            "30",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(proc.stdout, flush=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr, flush=True)
        raise DeployError("predeployment snapshot failed; nothing was deployed")
    try:
        verified = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise DeployError("snapshot tool did not confirm a snapshot object") from exc
    if not isinstance(verified, dict):
        raise DeployError("snapshot tool did not confirm a snapshot object")
    print(f"  verified snapshot {snapshot_id}", flush=True)
    return snapshot_id


def run_migration(image: str, *, dry_run: bool) -> None:
    log("Database migration task")
    task_def = register_with_image(MIGRATE_FAMILY, image, dry_run=dry_run)
    if dry_run:
        print(
            "  [dry-run] run-task migrate + wait + exit-code check skipped", flush=True
        )
        return
    network = (
        f"awsvpcConfiguration={{subnets=[{SUBNETS}],"
        f"securityGroups=[{SECURITY_GROUP}],assignPublicIp=DISABLED}}"
    )
    task_arn = aws(
        "ecs",
        "run-task",
        "--cluster",
        CLUSTER,
        "--task-definition",
        task_def,
        "--launch-type",
        "FARGATE",
        "--network-configuration",
        network,
        "--query",
        "tasks[0].taskArn",
    )
    print(f"  migration task {task_arn}", flush=True)
    aws(
        "ecs",
        "wait",
        "tasks-stopped",
        "--cluster",
        CLUSTER,
        "--tasks",
        task_arn,
        parse=False,
    )
    exit_code = aws(
        "ecs",
        "describe-tasks",
        "--cluster",
        CLUSTER,
        "--tasks",
        task_arn,
        "--query",
        "tasks[0].containers[0].exitCode",
    )
    if str(exit_code) != "0":
        raise DeployError(
            f"migration task exited with {exit_code}; the database is unchanged from "
            "the snapshot and no service was rolled"
        )
    print("  migration completed (exit 0)", flush=True)


def roll_services(image: str, *, dry_run: bool) -> dict[str, str]:
    log("Rolling API and worker services")
    new_arns: dict[str, str] = {}
    for family, service in ((API_FAMILY, API_SERVICE), (WORKER_FAMILY, WORKER_SERVICE)):
        arn = register_with_image(family, image, dry_run=dry_run)
        new_arns[service] = arn
        if dry_run:
            print(f"  [dry-run] update-service {service} -> {arn}", flush=True)
            continue
        aws(
            "ecs",
            "update-service",
            "--cluster",
            CLUSTER,
            "--service",
            service,
            "--task-definition",
            arn,
            "--force-new-deployment",
            "--query",
            "service.serviceArn",
        )
    if dry_run:
        return new_arns
    aws(
        "ecs",
        "wait",
        "services-stable",
        "--cluster",
        CLUSTER,
        "--services",
        API_SERVICE,
        WORKER_SERVICE,
        parse=False,
    )
    for service, arn in new_arns.items():
        if current_service_task_def(service) != arn:
            raise DeployError(f"{service} did not converge on {arn}")
    print("  both services stable on the new revision", flush=True)
    return new_arns


def publish_frontend(dist: Path, *, dry_run: bool) -> None:
    log("Publishing frontend and invalidating CloudFront")
    if dry_run:
        print("  [dry-run] publish-frontend.sh skipped", flush=True)
        return
    env = {
        **os.environ,
        "FRONTEND_BUCKET": FRONTEND_BUCKET,
        "FRONTEND_DIST": str(dist),
        "CLOUDFRONT_DISTRIBUTION_ID": CF_DIST_ID,
        "AWS_REGION": REGION,
        "AWS_DEFAULT_REGION": REGION,
    }
    run(["bash", str(ROOT / ".github" / "scripts" / "publish-frontend.sh")], env=env)


def smoke_test() -> None:
    log("Smoke testing production")
    if not FRONTEND_URL:
        print("  ! LUMINA_FRONTEND_URL not set; skipping smoke test", flush=True)
        return
    base = FRONTEND_URL
    root = subprocess.run(
        ["curl", "-sS", "--max-time", "30", f"{base}/"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if "/assets/" not in root:
        raise DeployError("frontend root did not reference a hashed /assets/ file")
    for path, expected in (
        (f"{base}/api/auth/me", "401"),
        (f"{base}/api/__nope__", "404"),
    ):
        status = subprocess.run(
            [
                "curl",
                "-sS",
                "-o",
                os.devnull,
                "-w",
                "%{http_code}",
                "--max-time",
                "30",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if status != expected:
            raise DeployError(f"{path} returned {status}, expected {expected}")
    print("  smoke test passed", flush=True)


def write_state(release: str, snapshot_id: str, previous: dict, new: dict) -> None:
    state = {
        "release": release,
        "snapshot_id": snapshot_id,
        "previous_task_definitions": previous,
        "new_task_definitions": new,
        "rollback": {
            "services": [
                "aws ecs update-service --cluster {c} --service {s} "
                "--task-definition {a} --force-new-deployment".format(
                    c=CLUSTER, s=s, a=a
                )
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
            API_SERVICE: current_service_task_def(API_SERVICE),
            WORKER_SERVICE: current_service_task_def(WORKER_SERVICE),
        }
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
