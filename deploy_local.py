import os
import subprocess
import boto3
import base64
import mimetypes
from pathlib import Path
from dotenv import load_dotenv
import time

load_dotenv()

REGION = 'eu-central-1'
ECR_REPO = '967848862549.dkr.ecr.eu-central-1.amazonaws.com/lumina'
CLUSTER = 'lumina-production'
API_SERVICE = 'lumina-production-api'
WORKER_SERVICE = 'lumina-production-worker'
MIGRATE_TASK = 'lumina-production-migrate'
FRONTEND_BUCKET = 'lumina-production-frontend-967848862549'
CF_DIST_ID = 'E37I24R2GMTA0G'
SUBNETS = ['subnet-0a661a857ff476ce2', 'subnet-032982feebfcaeb57', 'subnet-0b0c3d982bd42d520']
SG = ['sg-0117c38812fa58b10']

print("1. Authenticating with ECR...")
try:
    ecr = boto3.client('ecr', region_name=REGION)
    auth = ecr.get_authorization_token()['authorizationData'][0]
    token = base64.b64decode(auth['authorizationToken']).decode('utf-8')
    user, pwd = token.split(':')
    ep = auth['proxyEndpoint']
    subprocess.run(['docker', 'login', '-u', user, '-p', pwd, ep], check=True)
    try:
        ecr.put_image_tag_mutability(repositoryName='lumina', imageTagMutability='MUTABLE')
        print("  Set ECR repository tag mutability to MUTABLE.")
    except Exception as tag_err:
        print(f"  Note on tag mutability: {tag_err}")
except Exception as e:
    print(f"Error authenticating with ECR: {e}")
    exit(1)

print("\n2. Building & Pushing Backend Docker Image...")
try:
    subprocess.run(['docker', 'build', '-t', f'{ECR_REPO}:latest', '.'], check=True)
    subprocess.run(['docker', 'push', f'{ECR_REPO}:latest'], check=True)
except Exception as e:
    print(f"Error building/pushing Docker image: {e}")
    exit(1)

print("\n3. Building Frontend...")
try:
    subprocess.run(['npm', 'run', 'build'], cwd='frontend', shell=True, check=True)
except Exception as e:
    print(f"  Note on frontend build: {e} (continuing with existing dist)...")

print("\n4. Uploading Frontend to S3...")
try:
    s3 = boto3.client('s3', region_name=REGION)
    dist_dir = Path('frontend/dist')
    for root, dirs, files in os.walk(dist_dir):
        for file in files:
            full_path = Path(root) / file
            s3_key = 'current/' + str(full_path.relative_to(dist_dir)).replace('\\', '/')
            content_type = mimetypes.guess_type(str(full_path))[0] or 'application/octet-stream'
            print(f"  Uploading {s3_key}...")
            s3.upload_file(str(full_path), FRONTEND_BUCKET, s3_key, ExtraArgs={'ContentType': content_type})
except Exception as e:
    print(f"Error uploading to S3: {e}")
    exit(1)

print("\n5. Running Database Migration Task...")
try:
    ecs = boto3.client('ecs', region_name=REGION)
    res = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=MIGRATE_TASK,
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': SUBNETS,
                'securityGroups': SG,
                'assignPublicIp': 'DISABLED'
            }
        }
    )
    task_arn = res['tasks'][0]['taskArn']
    print(f"  Migration task started: {task_arn}")
    print("  Waiting for migration to complete (this might take a few minutes)...")
    waiter = ecs.get_waiter('tasks_stopped')
    waiter.wait(cluster=CLUSTER, tasks=[task_arn])
    print("  Migration finished.")
except Exception as e:
    print(f"Error running migration task: {e}")
    exit(1)

print("\n6. Updating ECS Services...")
try:
    ecs.update_service(cluster=CLUSTER, service=API_SERVICE, forceNewDeployment=True)
    ecs.update_service(cluster=CLUSTER, service=WORKER_SERVICE, forceNewDeployment=True)
    print("  Triggered deployment for API and Worker services.")
    print("  Waiting for services to stabilize (this will take 5-10 minutes)...")
    waiter_svc = ecs.get_waiter('services_stable')
    waiter_svc.wait(cluster=CLUSTER, services=[API_SERVICE, WORKER_SERVICE])
    print("  Services stable.")
except Exception as e:
    print(f"Error updating ECS services: {e}")
    exit(1)

print("\n7. Invalidating CloudFront Cache...")
try:
    cf = boto3.client('cloudfront')
    cf.create_invalidation(
        DistributionId=CF_DIST_ID,
        InvalidationBatch={
            'Paths': {'Quantity': 1, 'Items': ['/*']},
            'CallerReference': str(time.time())
        }
    )
    print("  Invalidation created.")
except Exception as e:
    print(f"Error creating CloudFront invalidation: {e}")
    exit(1)

print("\n=== DEPLOYMENT COMPLETE! ===")
