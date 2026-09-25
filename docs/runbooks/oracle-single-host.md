# Oracle Cloud Single-Host Production

Production (`lumina-study.com`) runs the hosted topology from
`docker-compose.hosted.yml` on one Oracle Cloud Always Free VM, with
`docker-compose.oracle.yml` layered on top. The AWS topology in `terraform/`
and its workflows (`deploy.yml`, `hosted-restore-drill.yml`,
`hosted-snapshot-retention.yml`) are retained but disabled in GitHub; they do
not target a live account.

## Topology

| Component | Where |
| --- | --- |
| VM | Ubuntu 24.04 aarch64, `VM.Standard.A1.Flex` 4 OCPU / 24 GB, user `ubuntu` |
| Application | `~/Lumina` checkout at a detached release SHA; `db`, `minio`, `migrate`, `lumina`, `lumina-worker` |
| Object storage | Versity S3 Gateway serving the `minio` service name on the `lumina_minio-data` volume |
| Configuration | `~/Lumina/.env`, mode `600`, never committed |
| TLS and routing | Caddy (systemd), `/etc/caddy/Caddyfile`, proxies to `127.0.0.1:10312` |
| DNS | Squarespace: `A @` to the VM public IP, `CNAME www` to the apex |
| Backups | `ops/oracle/backup.sh` before every deploy, and its installed copy `~/bin/lumina-backup` nightly at 03:30 from cron, into `~/backups`, 7-day retention |

Ingress is limited to TCP 22, 80, and 443 in both the VCN security list and
the VM's iptables. The application port is bound to `127.0.0.1`, which is why
`.env` sets `FORWARDED_ALLOW_IPS=*`: only Caddy can reach it.

## Deploy pipeline

`.github/workflows/deploy-oracle.yml` runs on every push to `main` and on
manual dispatch. It connects with a dedicated key whose `authorized_keys`
entry is pinned to `~/bin/lumina-deploy`
(`command="/home/ubuntu/bin/lumina-deploy",restrict`), so the key can deploy a
commit on `origin/main` and do nothing else. `ops/oracle/deploy.sh` fetches,
checks out the SHA, builds the image, backs up, runs `up --wait` (migrations
run in the `migrate` service), and checks `/health/ready`. The workflow then
smoke-tests the public site.

The `production` environment needs:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `ORACLE_DEPLOY_KEY` | Private half of the deploy key |
| Variable | `ORACLE_HOST` | VM public IP |
| Variable | `ORACLE_USER` | `ubuntu` |
| Variable | `ORACLE_KNOWN_HOSTS` | `ssh-keyscan -t ed25519 <host>` output |
| Variable | `FRONTEND_URL` | `https://lumina-study.com` |

`deploy.sh` runs from `~/bin`, not the checkout, so a release cannot rewrite
the script mid-run. Reinstall both scripts after changing them:

```bash
cd ~/Lumina
install -m 755 ops/oracle/deploy.sh ~/bin/lumina-deploy
install -m 755 ops/oracle/backup.sh ~/bin/lumina-backup
# crontab: 30 3 * * * /home/ubuntu/bin/lumina-backup >> /home/ubuntu/backups/backup.log 2>&1
```

## Operations

Deploy or roll back by hand to any commit already on `main`:

```bash
~/bin/lumina-deploy <40-character-sha>
```

Rolling back re-runs `migrate` at the older commit, which does not downgrade
the schema; restore the pre-deploy backup if a migration must be undone.

Inspect the stack:

```bash
cd ~/Lumina
docker compose -f docker-compose.hosted.yml -f docker-compose.oracle.yml ps
docker compose -f docker-compose.hosted.yml -f docker-compose.oracle.yml logs --tail 100 lumina lumina-worker
curl -fsS http://127.0.0.1:10312/health/ready
```

Restore a database backup (stops writers first):

```bash
cd ~/Lumina
C="docker compose -f docker-compose.hosted.yml -f docker-compose.oracle.yml"
$C stop lumina lumina-worker
docker exec -i lumina-db-1 pg_restore -U postgres -d lumina --clean --if-exists < ~/backups/db-<stamp>.dump
$C start lumina lumina-worker
```

Restore documents from `~/backups/docs-<stamp>.tgz` by extracting it into the
`lumina_minio-data` volume while `minio` is stopped.

## Known limits

- Backups live on the same VM. Copy `~/backups` off the host periodically.
- Oracle may reclaim Always Free instances that stay idle for seven days.
  Keep off-host backups current so the stack can be rebuilt from this runbook.
- There is one replica of every service and no zero-downtime rollout; the API
  restarts during each deploy.
