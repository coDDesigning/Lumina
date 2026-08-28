# Authentication hardening

Password rules, email verification, and the browser response headers that
protect both. Where this document and the code disagree, the code is the bug.

## Password policy

`utils/password_policy.py` is the only definition of an acceptable password, and
every flow that accepts one — registration, the authenticated change endpoint,
and any future reset — validates through `validate_password`. Nothing else may
impose its own length or composition rule, which is what stops the three flows
from drifting apart. `policy_description()` renders the user-facing sentence
from the enforced values, so a client can never display a rule the server does
not apply.

The policy is length-led rather than composition-led, following NIST SP 800-63B.
A password is rejected when it:

- is shorter than `PASSWORD_MIN_LENGTH` (default 12),
- exceeds 72 bytes, which is where bcrypt truncates and stops hashing,
- contains a NUL character or is entirely whitespace,
- appears in the small embedded list of passwords credential stuffing tries
  first,
- is one repeated character, or a run of an alphabet, digit, or keyboard row,
- contains a four-character-or-longer fragment of the account's own name or
  email address, compared after NFKC normalization and case folding.

There is no forced character-class requirement. Composition rules mostly produce
predictable decoration (`Password1!`) while making long passphrases harder to
type, so the floor is raised instead.

### Where it is enforced

| Flow | Endpoint | Identifiers checked against |
| --- | --- | --- |
| Registration | `POST /api/auth/register` | name, email |
| Authenticated change | `PUT /api/users/me/password` | name, email |
| Reset | not implemented yet (WP12-RW3) | must call `validate_password` |

`PUT /api/users/me/password` requires the current password before it will set a
new one, so a stolen session alone cannot take an account over. A wrong current
password and a rejected new password both return `400`; the rejection message is
`policy_description()`, so the user is told the rule rather than being made to
guess it. Passwords persist only as `password_hash` and are never logged.

A password reset flow is deliberately not part of this change. When it is built
it must call `validate_password` rather than repeat the rules, which is the
reason the module exists as a separate seam.

## Email verification

### Why it gates credits

Registration is free and the hosted deployment pays for inference, so an
introductory balance handed to an unverified address is an invitation to create
fifty accounts. Verification also gives the account a recovery identity that is
worth something later. Both goals need the same fact — that somebody reachable
at that address asked for the account — so credits are granted at the moment
that fact is established and nowhere else.

### Lifecycle

1. `POST /api/auth/register` creates the account. When verification is required
   the opening balance is `0.0` and no `INITIAL_GRANT` row is written. A zero
   balance is not a null balance: null means unmetered, zero means metered and
   empty, and the existing `insufficient_credits` path (402) already covers it,
   so no generation route needed changing.
2. A token is minted, its SHA-256 digest stored in `email_verification_tokens`,
   and the plaintext sent by mail. Issuing a token consumes any outstanding one
   for that account, so at most one link per account is live.
3. `POST /api/auth/verify-email` redeems it. The token is claimed by an
   `UPDATE ... WHERE consumed_at IS NULL AND expires_at > now ... RETURNING`, so
   expiry and single use are row state compared inside the claiming statement:
   two clicks on one link cannot both win and nothing has to be scheduled.
4. In that same transaction the account is stamped `email_verified_at` and
   `CreditService.grant_initial_credits` appends the one `INITIAL_GRANT` row. A
   consumed token beside an ungranted balance would leave an account permanently
   empty with no link left to click, so the two commit together or not at all.
5. `POST /api/auth/verify-email/resend` issues a replacement link.

An unverified account can still sign in. It has to: the screen explaining the
zero balance and the control that requests a new link are both behind the
session. `UserResponse.is_email_verified` and the
`email_verification_required` / `is_email_verified` fields on
`GET /api/users/me/credits` are what let a client tell "spent it all" apart from
"never got any", which is the difference between no next action and one.

### Granting exactly once

`grant_initial_credits` refuses if the account already has an `INITIAL_GRANT` or
a `METERING_RESET` row. The second reason matters: a demoted administrator
receives `METERING_RESET` rather than `INITIAL_GRANT`, and without that check
verifying afterwards would hand out a second opening balance.

`CreditService.ensure_current_period_grant` also asks
`may_receive_automatic_grants` before the lazy monthly grant. Without it, an
unverified account would be handed the monthly grant on its next balance read
and the whole gate would be worth nothing. Administrator grants are not gated —
they are a deliberate act by a human who has already decided.

### Tokens

Only the SHA-256 digest is stored, so a database read cannot verify anybody and
a leaked backup hands over no live links. The plaintext exists once, in the
message that carries it, and must never be logged. `EMAIL_VERIFICATION_TOKEN_TTL_HOURS`
(default 24, maximum 168) bounds how long a leaked link is worth anything;
single use applies regardless of expiry.

A redeem or resend that fails says nothing about which of "unknown token",
"already used", and "expired" it was — distinguishing them would confirm that a
guessed token once existed. The resend endpoint answers identically for an
unknown address, an already verified one, and a genuine resend, so it cannot be
used to enumerate accounts. Both endpoints share one per-IP rate limit key,
`verification:ip:{ip}`; see [rate limiting](rate_limiting.md).

### Delivery

`services/email_delivery.py` is the seam. `SmtpEmailSender` uses the standard
library, optionally issuing STARTTLS and logging in; `UnconfiguredEmailSender`
raises, which is what a deployment gets when it has not supplied a relay. Only
the exception type is ever logged — never the address, subject, or body.

Delivery happens after the token is committed, so a relay that accepts a message
cannot race a link the database has not stored yet. A send that fails does not
undo the registration: the account exists, the token is usable, and the response
says to request a new link. That is what makes resend a real remedy rather than
a second chance at the same failure.

### Self-hosted behavior

`EMAIL_VERIFICATION_REQUIRED` defaults to `true` in hosted mode and `false` in
self-hosted mode, and can be set explicitly either way.

A self-hosted operator supplies their own inference, so `CREDIT_METERING_ENABLED`
is off for them and there are no introductory credits to farm. Requiring an SMTP
relay before anyone could use their own deployment would be a cost with no
matching benefit. So by default a self-hosted registration creates an account
with `email_verified_at` left null and a null (unmetered) balance, and the
verification endpoints report that this deployment does not verify addresses:
`POST /api/auth/verify-email/resend` returns `409`.

A self-hosted operator who wants verification anyway — a shared instance with
untrusted users, say — sets `EMAIL_VERIFICATION_REQUIRED=true` and supplies
`APP_PUBLIC_BASE_URL`, `EMAIL_FROM_ADDRESS`, and `SMTP_HOST`. Startup fails with
a message naming whichever of the three is missing, because a deployment that
gates credits on a link it cannot send would create accounts nobody could finish.

Existing accounts are not back-filled. Every account that registered before this
change reached its balance without proving anything, and stamping a verification
timestamp onto them would be a claim the database cannot support. Turning
verification on for an existing hosted deployment therefore leaves those
accounts unverified; they keep the balance they already have, and
`grant_initial_credits` will not grant them a second one when they verify.

### Trying it locally

Verification can be exercised without a relay. `scripts/dev_mail_catcher.py` is
an SMTP sink that accepts anything on `127.0.0.1:1025` and appends it to
`data/maildrop.txt`:

```bash
python scripts/dev_mail_catcher.py
```

With it running, start the API with verification on and pointed at it:

```
EMAIL_VERIFICATION_REQUIRED=true
APP_PUBLIC_BASE_URL=http://localhost:5173
EMAIL_FROM_ADDRESS=info@study-lumina.com
SMTP_HOST=127.0.0.1
SMTP_PORT=1025
SMTP_USE_TLS=false
CREDIT_METERING_ENABLED=true
```

`APP_PUBLIC_BASE_URL` is the SPA's own origin, because that is where the link
has to land; the Vite dev server proxies `/api` to the backend, so nothing else
has to change. `CREDIT_METERING_ENABLED` is optional and only makes the withheld
grant visible: without it a self-hosted balance is null either way.

Register through the SPA, read `data/maildrop.txt`, and open the link it
contains. The body is quoted-printable, so a long link is split across lines
with a trailing `=`; join the pieces and drop the `=` before pasting, or let a
mail client do it. The account exists and can sign in from the moment it
registers — what the link releases is the credits.

The first account of an empty database becomes the administrator, and
administrators are unmetered. Register a second account to watch a balance go
from `0.0` to `CREDIT_INITIAL_GRANT`.

## Response security headers

### Boundary

`backend/app/security_headers.py` attaches the headers at the application
boundary. The hosted CloudFront distribution sets equivalents of all four
already, but it is not the only boundary Lumina runs behind: a self-hosted
Compose deployment has no CDN at all, and the hosted stack answers on the ALB
hostname directly until DNS is cut over. Setting them in the application means
the guarantee travels with the software rather than with one topology.

The middleware never overwrites a header that is already present, and the
CloudFront policy sets its own with `override = true`, so where the CDN is in
front its values win and there is exactly one answer per header either way.

It is plain ASGI rather than `BaseHTTPMiddleware`, and is added after
`RequestSizeLimitMiddleware` so it wraps it — the responses the size limiter
rejects before routing carry the headers too.

### Policy

| Header | Value |
| --- | --- |
| `Content-Security-Policy` | `default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains`, when enabled |

This API returns JSON and owns no browsing context, so `default-src 'none'` is
the accurate policy rather than a strict-looking one: nothing may load, nothing
may frame it, and no form or base tag in a response can be aimed anywhere.

The exception is FastAPI's interactive documentation. `/docs`,
`/docs/oauth2-redirect`, and `/redoc` load Swagger UI and ReDoc from
`https://cdn.jsdelivr.net` with an inline bootstrap script, so those three paths
get a policy naming that host exactly, plus `https://fastapi.tiangolo.com` for
the favicon. `'unsafe-inline'` appears only there and only for script and style.
No policy anywhere uses a scheme or host wildcard.

HSTS is a promise a browser remembers for a year, so it is emitted only where
TLS is known to terminate in front of the API: on by default in hosted mode, off
otherwise. A self-hosted deployment behind a TLS reverse proxy should set
`SECURITY_HSTS_ENABLED=true`; one served over plain HTTP on a LAN must not, or
browsers will refuse to reach it.

### The SPA policy

The CloudFront response headers policy in `terraform/modules/frontend/main.tf`
covers the pages a browser actually renders. Its `connect-src` is `'self'`,
which is sufficient because the SPA and the API are served from the same
distribution: `/api` and `/api/*` are cache behaviors on the same origin. The
previous `connect-src 'self' https:` permitted every host on the internet, which
is not a policy; `terraform/tests/frontend.tftest.hcl` now rejects any directive
containing a scheme or host wildcard.

A deployment that genuinely needs a browser call to leave the distribution adds
the exact origin to `frontend_additional_connect_src`. The variable validates
that each entry is a complete lowercase `https://host[:port]` with no path and
no wildcard.

## Configuration

All of these are documented with their defaults in `.env.example` and read in
`backend/app/config.py`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `PASSWORD_MIN_LENGTH` | `12` | Minimum length, capped at 64 by bcrypt's 72-byte limit |
| `EMAIL_VERIFICATION_REQUIRED` | hosted: `true`, self-hosted: `false` | Whether credits wait for a proven address |
| `EMAIL_VERIFICATION_TOKEN_TTL_HOURS` | `24` | Link lifetime, 1–168 |
| `APP_PUBLIC_BASE_URL` | unset | Origin verification links point at (the SPA, not the API) |
| `EMAIL_FROM_ADDRESS` | unset | Envelope sender |
| `SMTP_HOST` / `SMTP_PORT` | unset / `587` | Relay |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | unset | Set both or neither |
| `SMTP_USE_TLS` | `true` | Issue STARTTLS after connecting |
| `SMTP_TIMEOUT_SECONDS` | `10` | Per-connection deadline, 1–120 |
| `RATE_LIMIT_VERIFICATION_MAX_ATTEMPTS` | `5` | Per-IP redeem and resend attempts per window |
| `RATE_LIMIT_VERIFICATION_WINDOW_SECONDS` | `3600` | That window |
| `SECURITY_HEADERS_ENABLED` | `true` | Attach the four headers |
| `SECURITY_HSTS_ENABLED` | hosted: `true`, otherwise `false` | Emit HSTS |
| `SECURITY_HSTS_MAX_AGE_SECONDS` | `31536000` | HSTS lifetime |

In the hosted AWS deployment the mail settings are supplied by
`terraform/modules/ecs` to every task, because every task loads the same
configuration module and one missing setting fails startup validation rather
than starting without mail. The sender is `info@study-lumina.com`, which is the
default of the `email_from_address` Terraform variable; the relay must be
authorized to send as it or the message is rejected before it leaves.
`SMTP_PASSWORD` is referenced from SSM only when `smtp_username` is set. See
[deployment](deployment.md).

## Related

- [Credits](credits.md) — what the granted balance is for and how it is spent.
- [Rate limiting](rate_limiting.md) — the throttles in front of these endpoints.
- [Deployment](deployment.md) — where the settings are supplied per topology.

## Not in this change

Password reset (WP12-RW3), JWT revocation, and the seven-day access-token
lifetime are separate work. The token lifetime in `utils/security.py` is
unchanged, so verifying an address does not invalidate an existing session and a
password change does not either.
