# Rate limiting

## Purpose

Two abuse surfaces exist independently of credits: unthrottled login/registration
invites credential brute force and bulk free-credit account creation, and AI
generation has no second-line limit behind the credit gate. `utils/rate_limit.py`
closes both with one mechanism. Where this document and the code disagree, the
code is the bug.

## Mechanism

One `rate_limit_buckets` row per key. A key is a colon-joined dimension string:
`login:ip:{ip}`, `login:account:{email}`, `register:ip:{ip}`,
`verification:ip:{ip}`, `generation:user:{user_id}:{feature}`. Each request atomically bumps its key's
counter (`SELECT ... FOR UPDATE`, matching `UserService.update_user`'s locking
pattern rather than a database-specific upsert, so behavior is identical on
SQLite and PostgreSQL) and compares it to the configured limit for the current
fixed window. A window rollover resets the row in place instead of inserting a
new one, so the table's size tracks active keys, not request volume.

Rate limits are checked as FastAPI dependencies (or, for login, explicitly at
the top of the route before password verification) — a rejection raises
`TooManyRequestsException` before the route body runs, so a throttled AI
generation request never reaches `CreditService.charge` and a throttled login
never reaches `verify_password`.

A rejection is always `429` with a `Retry-After` header and an `X-Error-Code`
of `login_rate_limited`, `registration_rate_limited`,
`verification_rate_limited`, or `generation_rate_limited` — distinct from
`provider_rate_limited`, which is the
AI provider's own limit surfaced through `utils/ai_errors.py`. Neither the
status code nor the body reveals whether the rate-limited account exists.

## Progressive lockout

Only the `login:account:{email}` key opts into lockout; IP and generation keys
reject for the rest of the current window and nothing more. On the first
violation, the account is locked for `RATE_LIMIT_LOCKOUT_BASE_SECONDS`. Each
further violation while the previous lockout is still recent doubles the
duration, capped at `RATE_LIMIT_LOCKOUT_MAX_SECONDS`. A request arriving during
an active lockout is rejected immediately without bumping the window counter,
so retrying during a lockout does not itself extend it, and an expired lockout
does not by itself forgive the streak either -- only a whole window with no
violation at all does, at the window's natural rollover. This means an
attacker who keeps hammering an account inside one abuse window keeps
escalating even across several individual lockout expiries, while a paced
attacker who never exceeds the per-window limit never accumulates a streak in
the first place, since only exceeding the limit counts as a violation.

This is deliberately a login-only mechanism: registration has no notion of "the
account" to lock (an email either isn't registered yet or already exists), and
generation abuse is already bounded by the credit balance itself.

## Email verification

`verification:ip:{ip}` covers both `POST /api/auth/verify-email` and
`POST /api/auth/verify-email/resend` with one key, because they are two halves
of the same abuse: resends are outbound mail somebody else pays for, and
redemptions are guesses at a token. The key is the IP rather than the account on
purpose — the address in a resend request is unauthenticated and an attacker
picks it freely, so an account key would let them lock a victim out of verifying
their own address. See [authentication hardening](authentication.md).

## Configuration

All ten settings live in `backend/app/config.py` and are documented with
their defaults in `.env.example`: `RATE_LIMIT_LOGIN_MAX_ATTEMPTS`,
`RATE_LIMIT_LOGIN_WINDOW_SECONDS`, `RATE_LIMIT_REGISTER_MAX_ATTEMPTS`,
`RATE_LIMIT_REGISTER_WINDOW_SECONDS`, `RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS`,
`RATE_LIMIT_VERIFICATION_WINDOW_SECONDS`, `RATE_LIMIT_GENERATION_MAX_ATTEMPTS`,
`RATE_LIMIT_GENERATION_WINDOW_SECONDS`, `RATE_LIMIT_LOCKOUT_BASE_SECONDS`,
`RATE_LIMIT_LOCKOUT_MAX_SECONDS`. There is no deployment-mode gating: both
hosted and self-hosted instances are throttled identically, since a self-hosted
operator's own account is exactly as exposed to credential-stuffing bots as a
hosted one.

## Known limitation: proxy trust

`utils/rate_limit.client_ip` reads `request.client.host`, which is the peer
Starlette resolves the TCP connection to. Behind the hosted ALB this is only
the real client's address if the ASGI server is separately configured to trust
and parse `X-Forwarded-For` from that specific proxy (uvicorn `--proxy-headers`
with a trusted-host allowlist). Until that is wired up, every hosted request
shares the ALB's address for per-IP keys, which under-throttles distinct
attackers and, in the worst case, could let one client's traffic push a shared
IP bucket into rejecting everyone else's. Per-account login lockout is
unaffected, since it does not depend on IP. Configuring trusted proxy headers
for the hosted ALB is tracked as follow-up work, not part of this change.
