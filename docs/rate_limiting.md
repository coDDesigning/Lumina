# Rate limiting

## Purpose

Two abuse surfaces exist independently of credits: unthrottled login/registration
invites credential brute force and bulk free-credit account creation, and AI
generation has no second-line limit behind the credit gate. `utils/rate_limit.py`
closes both with one mechanism. Where this document and the code disagree, the
code is the bug.

## Mechanism

One `rate_limit_buckets` row per key. A key is a fixed-length SHA-256 digest of
the identifying dimension: `login:ip:<digest>`, `login:account:<digest>`,
`register:ip:<digest>`, `verification:ip:<digest>`,
`password_reset:ip:<digest>`, `generation:user:<digest>`. Raw identifiers
(IP addresses, emails) are never stored in the table. Each request atomically
bumps its key's counter (`SELECT ... FOR UPDATE` on PostgreSQL; `BEGIN IMMEDIATE`
on SQLite, so behavior is identical on both engines) and compares it to the
configured limit for the current fixed window. A window rollover resets the row
in place instead of inserting a new one, so the table's size tracks active keys,
not request volume.

Rate limits are checked as FastAPI dependencies (or, for login, explicitly at
the top of the route before password verification) — a rejection raises
`TooManyRequestsException` before the route body runs, so a throttled AI
generation request never reaches `CreditService.charge` and a throttled login
never reaches `verify_password`.

A rejection is always `429` with a `Retry-After` header and an `X-Error-Code`
of `login_rate_limited`, `registration_rate_limited`,
`verification_rate_limited`, `password_reset_rate_limited`, or
`generation_rate_limited` — distinct from
`provider_rate_limited`, which is the
AI provider's own limit surfaced through `utils/ai_errors.py`. Neither the
status code nor the body reveals whether the rate-limited account exists.
`Retry-After` is also exposed through CORS so cross-origin clients can read it.

## Login policy

Login uses two independent controls:

- **Per-IP (`login:ip:<digest>`)** — counts *every* login attempt regardless
  of outcome. Limits brute-force volume from a single network location.
- **Per-account (`login:account:<digest>`)** — counts *only failed*
  authentication attempts. Successful logins clear the account bucket, so a
  legitimate user who mistypes their password a few times is never locked out by
  an attacker's traffic. Unknown accounts still perform a dummy bcrypt check so
  the timing and bucket behavior are identical; the per-account response is
  indistinguishable for known and unknown addresses.

## Progressive lockout

Only the `login:account:<digest>` key opts into progressive lockout; IP,
registration, verification, password-reset, and generation keys reject for the
rest of the current window and nothing more. On the first failed-auth violation,
the account is locked for `RATE_LIMIT_LOCKOUT_BASE_SECONDS`. Each further
violation while the previous lockout is still recent doubles the duration, capped
at `RATE_LIMIT_LOCKOUT_MAX_SECONDS`. A request arriving during an active lockout
is rejected immediately without bumping the window counter, so retrying during a
lockout does not itself extend it. Only a full window with no failed attempts
clears the violation streak.

This is deliberately a login-only mechanism: registration has no notion of "the
account" to lock (an email either isn't registered yet or already exists), and
generation abuse is already bounded by the credit balance itself.

## Email verification and password reset

`verification:ip:<digest>` covers both `POST /api/auth/verify-email` and
`POST /api/auth/verify-email/resend` with one key, because they are two halves
of the same abuse: resends are outbound mail somebody else pays for, and
redemptions are guesses at a token. The key is the IP rather than the account on
purpose — the address in a resend request is unauthenticated and an attacker
picks it freely, so an account key would let them lock a victim out of verifying
their own address. See [authentication hardening](authentication.md).

`password_reset:ip:<digest>` throttles `POST /api/auth/reset-password` per IP.

## Generation policy

All interactive AI generation routes share one per-user bucket:
`generation:user:<digest>`. The `feature` dimension (study_guide, quiz,
flashcard, etc.) is retained only as a telemetry label and does not multiply the
allowance. A local throttle never reaches the provider or `CreditService.charge`,
so rejected requests leave the credit balance, ledger, `ai_usage_logs`, and
generated outputs untouched.

The following routes are rate-limited under this policy:

- `POST /api/courses/{course_id}/study-guide`
- `POST /api/courses/{course_id}/quiz`
- `POST /api/courses/{course_id}/flashcards`
- `POST /api/courses/{course_id}/ai-tutor`
- `POST /api/courses/{course_id}/qa`
- `POST /api/prompt-generator`
- `POST /api/courses/{course_id}/reverse-quiz`
- `POST /api/courses/{course_id}/exam-roadmap`
- `POST /api/courses/{course_id}/exam-mode/analysis`
- `POST /api/courses/{course_id}/exam-mode/analysis/rescan`
- `POST /api/courses/{course_id}/exam-mode/topics/{topic_key}/guide`
- `POST /api/courses/{course_id}/exam-mode/topics/{topic_key}/summary`
- `POST /api/courses/{course_id}/exam-mode/topics/{topic_key}/practice`
- `POST /api/courses/{course_id}/exam-mode/topics/{topic_key}/exam`
- `POST /api/courses/{course_id}/exam-mode/topics/{topic_key}/similar-questions`
- `POST /api/courses/{course_id}/exam-mode/mock-exam`
- `POST /api/courses/{course_id}/exam-mode/review-sheet`

Routes that do not call a text-generation provider are excluded:

- `POST /api/courses/{course_id}/exam-mode/plans` (deterministic, no provider)

Conditional grading calls (open-ended answers in quiz attempts) consume the
generation budget only when a nonblank open-ended answer is about to call the
provider. Option-based and short-answer-only submissions do not consume it.

Asynchronous document processing (uploads, retries, profile documents) and
worker-side embedding/extraction jobs have separate abuse policies and do not
share this interactive request bucket.

## Observability

Every local rejection emits a privacy-safe structured event and a CloudWatch EMF
metric (`Lumina/API` namespace, `RateLimitRejections` counter). Dimensions are
limited to stable values: `Control` (e.g., `login_ip`, `login_account`,
`generation_user`), `ErrorCode`, and optional `Feature`. No IP address, email,
bucket key, user ID, or account-existence signal is logged. Request-completion
logs include the response `X-Error-Code` header so operators can correlate
throttle events with traffic patterns.

## Stale bucket pruning

When a new bucket is created, any existing row whose `window_start` is older
than twice the longest configured window/lockout and whose `locked_until` has
lapsed is removed. This bounds the table to active keys without a dedicated
cleanup worker.

## Configuration

All twelve settings live in `backend/app/config.py` and are documented with
their defaults in `.env.example`:

- `RATE_LIMIT_LOGIN_MAX_ATTEMPTS`
- `RATE_LIMIT_LOGIN_WINDOW_SECONDS`
- `RATE_LIMIT_REGISTER_MAX_ATTEMPTS`
- `RATE_LIMIT_REGISTER_WINDOW_SECONDS`
- `RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS`
- `RATE_LIMIT_VERIFICATION_WINDOW_SECONDS`
- `RATE_LIMIT_GENERATION_MAX_ATTEMPTS`
- `RATE_LIMIT_GENERATION_WINDOW_SECONDS`
- `RATE_LIMIT_LOCKOUT_BASE_SECONDS`
- `RATE_LIMIT_LOCKOUT_MAX_SECONDS`
- `RATE_LIMIT_PASSWORD_RESET_MAX_ATTEMPTS`
- `RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS`

There is no deployment-mode gating: both hosted and self-hosted instances are
throttled identically, since a self-hosted operator's own account is exactly as
exposed to credential-stuffing bots as a hosted one.

Supported deployments propagate these settings:

- Self-hosted Docker Compose: `docker-compose.yml`
- Hosted Docker Compose: `docker-compose.hosted.yml`
- Hosted AWS ECS: Terraform `module.ecs` task definition environment

## Proxy trust

Hosted deployments run behind an ALB. Uvicorn is started with
`--proxy-headers --forwarded-allow-ips <VPC_CIDR>`, so `request.client.host`
resolves to the original client address. Self-hosted deployments default to
trusting only `127.0.0.1`; operators placing a reverse proxy in front of the
API container must set `FORWARDED_ALLOW_IPS` to that proxy's CIDR. The
application never parses `X-Forwarded-For` itself — trusting untrusted headers
would allow spoofing.
