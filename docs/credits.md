# Credit lifecycle and the transaction ledger

## Purpose

Credits meter AI generation. This document is the authoritative statement of how
a balance can rise, how it can fall, what the numbers mean, and how every change
is recorded. Where this document and the code disagree, the code is the bug.

Two rules govern everything below.

**Every balance change is recorded.** For any account with a balance, that
balance equals the sum of that account's `credit_transactions.delta`. A change
without a ledger row, or a ledger row without the matching change, is a defect.

**The ledger is append-only.** Credit transactions are immutable accounting
events. A mistake is corrected by recording an opposing transaction, never by
editing or deleting an existing one.

## Balance-increase mechanisms

These are all of them. There are no others.

| Mechanism | Hosted | Self-hosted | Implemented |
|---|---|---|---|
| Initial grant at registration | Yes | Recorded, not enforced | Yes |
| Monthly grant, trimmed to a ceiling | Yes | No | Yes |
| Administrator credit change, either sign | Yes | No | Yes |
| Purchase with money | No | No | **No** |

**Credit purchase is not supported.** There is no payment provider, no purchase
route, no `PURCHASE` ledger reason, and no billing configuration. Adding one is a
separate workstream, and until it exists nothing in the codebase should imply
otherwise.

## Balance semantics

| Question | Answer |
|---|---|
| Can a balance go negative? | No. A charge that cannot be covered is refused; an adjustment that would cross zero is rejected whole. |
| Do credits expire? | No. |
| Do unused credits roll over? | Yes, up to the ceiling. |
| Is there a maximum? | `CREDIT_MAX_BALANCE` (default 100) bounds **automatic granting only**. It never reduces a balance. |
| What happens at zero? | A generation is refused with HTTP 402 and no provider is called. Quiz grading is the one exception, because it is prepaid; see [What each operation costs](#what-each-operation-costs). Zero is recoverable; see [Recovering an exhausted account](#recovering-an-exhausted-account). |
| When is a charge applied? | Before the provider call, after material assembly. |
| Does every operation cost the same? | No. A quiz containing open-ended questions costs 2 because it prepays its AI grading. |
| When is a refund issued? | When a charge was taken and the generation then failed. |
| What does a null balance mean? | The account is not metered. Administrators have always worked this way. |

## The mechanisms in detail

### Initial grant

A new account receives `CREDIT_INITIAL_GRANT` (default 50) once, at
registration, recorded as `initial_grant` with `actor_type = system`. The row
carries the registration month in `grant_period`, which is what stops a
brand-new account also collecting that same month's periodic grant — 50 in the
first month, not 100.

Administrators are created with a null balance and receive nothing.

### Monthly grant

Each calendar month an account may receive `CREDIT_PERIODIC_GRANT` (default 50),
trimmed so it never carries the balance past `CREDIT_MAX_BALANCE` (default 100):

```
delta = min(CREDIT_PERIODIC_GRANT, CREDIT_MAX_BALANCE - balance)
```

- Balance 12 receives 50, reaching 62.
- Balance 80 receives 20, reaching exactly 100.
- Balance 100 receives nothing, and no row is written.
- Balance 120, reached by an administrator grant, receives nothing and is **not
  reduced**. The ceiling limits granting; it never claws back.

Because a skipped month writes no row, an account at the ceiling becomes
eligible again the moment spending drops it back under.

**There is no scheduler.** The grant is evaluated lazily, when the account's
balance is charged or read. A month therefore cannot be missed because a job
failed to run. The trade-off is deliberate: an account that never returns
accrues nothing until it does.

Granting is idempotent by construction. The unique constraint on
`(user_id, grant_period)` means concurrent requests cannot both grant, however
many race. The loser of that race sees the same quiet skip as a caller who
found the row already there: the duplicate is rejected when the row is
flushed, and the whole attempt is rolled back rather than surfacing.

**Eligibility.** Active, metered, non-banned accounts. A banned account is
skipped and receives nothing for the months it stays banned.

### Administrator credit changes

One endpoint moves a balance in either direction:

```
POST /api/admin/users/{email}/credits
{ "delta": 25, "reason": "support_compensation", "note": "Outage INC-123" }
```

There is no separate grant route and no separate adjust route. They differed
only by the reason the URL implied, and a reason the caller states is more
honest than one inferred from a path. There is also no set-balance operation:
every ledger row is an actual delta, so `delta: 60` is how a balance of 40
becomes 100.

`reason` is mandatory and drawn from exactly these values:

| Reason | Sign | Meaning |
|---|---|---|
| `admin_grant` | Positive only | A deliberate allowance |
| `support_compensation` | Positive only | Goodwill for a failure the platform caused |
| `admin_adjustment` | Either, never zero | A correction |

`note` stays optional and free-form. Keeping it separate from the
machine-readable `reason` is what lets history be aggregated: every
compensation is still findable as a compensation once the prose is forgotten.

The actor is taken from the authenticated administrator and is never read from
the request body, so a manual change always answers who received the credits,
who changed them, when, why, and how much.

The request is refused whole, changing neither the balance nor the ledger,
when:

| Condition | Status |
|---|---|
| Not authenticated | 401 |
| Not an administrator | 403 |
| No account with that email | 404 |
| Zero, non-finite, wrong-signed, or unnamed reason | 422 |
| Metering disabled, unmetered account, or a result below zero | 400 |

`delta` must be finite. JSON admits `Infinity` and `NaN`; a balance must not,
because either passes the below-zero guard and leaves an account that can
never be charged again.

A manual change is **not** bounded by `CREDIT_MAX_BALANCE`, and there is no
ceiling on a single change: it is a deliberate act by a trusted operator, and
the ledger names who made it. A banned account can still be credited, because
unbanning should not also require re-granting.

This is account-level credit administration. It confers no authority over
another owner's course workspace, where administrators remain read-only.

### What each operation costs

`GENERATION_CREDIT_COSTS` in `services/credits.py` is the authoritative table,
and it is served to clients so no interface has to hardcode a price that the
server owns.

| Operation | Cost |
|---|---|
| Study guide | 1 |
| Quiz, no open-ended questions | 1 |
| Quiz containing open-ended questions | 2 |
| Flashcards | 1 |
| AI tutor | 1 |
| Course Q&A | 1 |
| Prompt generator | 1 |

Open-ended answers are graded by the provider, which costs real money every time
an attempt is submitted. That grading is **prepaid at generation**, which is why
such a quiz costs 2 rather than 1.

Charging it at submission instead would mean an exhausted balance could refuse
the grading of an attempt the student had already sat. Blocking there would
throw away their work, and the previous behaviour of degrading instead returned
the attempt with open-ended answers silently ungraded and nothing in the
response to say why. Prepaying removes the choice: **grading can never be
skipped for want of credit**, and every later attempt at the same quiz grades
free.

Consequently `quiz_grading` no longer charges. The reason still exists because
the ledger is append-only and historical rows carry it.

### Charging and refunding

Charging is one atomic conditional update guarded by the balance, so two
simultaneous requests cannot overspend. A refused charge changes nothing at all:
no balance mutation and no ledger row, because the ledger records balance
changes rather than attempts.

A refund carries `refunds_transaction_id` pointing at the charge it reverses,
and that column is unique, so **a charge can be refunded at most once** even if a
failure handler runs twice. Refunds are not trimmed to the ceiling: they return
credit the account already held.

## Recovering an exhausted account

A balance of zero is not a dead end. There are exactly two ways out, and no
others.

**Wait for the next month.** The monthly grant is lazy, so it lands on the
account's next charge or balance read in a new period. Nobody has to intervene
and no scheduler has to have run. The cost of this route is its latency: an
account that spends its allowance early waits out the rest of the month.

**Ask an administrator.** A credit change applies immediately, in any amount,
and names the administrator who made it. This is the support path for an
account that cannot wait, and the only one that works within the month the
allowance ran out.

Neither route rewrites history. Both leave a row explaining the recovery, and
the balance still equals the sum of the deltas on either side of it.

A banned account is not replenished by the first route, so unbanning is what
restores it; the second route still works, because a suspension is not a
statement about what the account is owed.

## Self-hosted deployments

`CREDIT_METERING_ENABLED` defaults to **false** in self-hosted mode and **true**
in hosted mode.

Self-hosted operators supply their own inference, hardware, and storage, so
metering them against a hosted allowance would be arbitrary. When metering is
disabled:

- No charge, refund, periodic grant, or administrator operation takes place.
- No charge or refund rows are written.
- The API reports a null balance, which every existing surface already renders
  as unlimited.
- Administrator credit endpoints return 400.

Exemption is a decision, not a large number. Nobody is issued a balance of
999999999 to fake it.

The stored `users.credits` column is left untouched while metering is off, and
the registration grant is still recorded, so the balance and the ledger stay in
agreement. An operator who later enables metering finds real balances rather
than a reset, and the invariant still holds.

## Abuse controls, and the WP12 dependency

The initial grant is tied to account creation, so **repeated registration is the
standing credit-farming vector**. This ticket does not close it; account
verification (WP12) does. Until verification lands, a hosted deployment's real
protection is that registration is cheap to attempt and the per-account ceiling
bounds what any single account can accumulate.

Controls that do exist:

- The ceiling bounds accumulation per account, so an idle account cannot hoard
  indefinitely.
- The monthly grant is once per account per period, enforced by a database
  constraint rather than by application timing.
- Banned accounts are not replenished.
- Only administrators can grant, and every grant names the administrator.

When verification arrives, the initial grant should move behind it. Nothing in
the ledger's shape needs to change for that.

## The `credit_transactions` table

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | `INTEGER` (PK) | No | |
| `user_id` | `INTEGER` (FK `users.id`) | No | Whose balance moved. Cascades on delete. |
| `delta` | `FLOAT` | No | Signed change. Negative charges, positive credits. |
| `balance_after` | `FLOAT` | No | Balance once this row was applied, written in the same transaction. |
| `reason` | `VARCHAR(40)` | No | Stable category, listed below. |
| `actor_type` | `VARCHAR(20)` | No | `system`, `user`, `admin`, or `migration`. |
| `actor_user_id` | `INTEGER` (FK `users.id`) | Yes | Who acted. `SET NULL` on delete: audit history outlives the actor's account. |
| `actor_label` | `VARCHAR(255)` | Yes | The actor's email at the time, so attribution survives that `SET NULL`. |
| `source_type` | `VARCHAR(50)` | Yes | Which operation caused it, e.g. `study_guide`, `quiz_grading`. |
| `source_id` | `INTEGER` | Yes | The related record, where one exists. |
| `refunds_transaction_id` | `INTEGER` (self-FK) | Yes | **Unique.** The charge this row reverses. |
| `grant_period` | `VARCHAR(7)` | Yes | `YYYY-MM`. Unique per user. |
| `note` | `VARCHAR(500)` | Yes | Human context for a manual change. |
| `created_at` | `DATETIME` | No | UTC. |

### Reasons

| Reason | Sign | Actor | Meaning |
|---|---|---|---|
| `initial_grant` | + | system | Registration allowance |
| `periodic_grant` | + | system | Monthly allowance |
| `generation_charge` | − | user | An AI generation was paid for |
| `generation_refund` | + | system | A paid-for generation failed |
| `admin_grant` | + | admin | Deliberate support grant |
| `support_compensation` | + | admin | Goodwill for a failure the platform caused |
| `admin_adjustment` | ± | admin | Deliberate correction |
| `metering_reset` | ± | system | Re-baseline after an account re-enters metering |
| `migration_reconciliation` | + | migration | Legacy balance baseline |

There is no `PURCHASE`. The enum reflects only the implemented lifecycle.

### Why `metering_reset` exists

Promoting an account to administrator sets its balance to null, and the account
leaves the ledger's scope; no row is written. Demoting it back hands it a fresh
balance that its accumulated deltas no longer sum to. A `metering_reset` row
carries exactly the difference, so the invariant holds again immediately. It is
written only when the sums actually disagree.

## Migration and backfill

The migration adding the table writes one `migration_reconciliation` row per
account with a non-null balance, whose delta is that entire balance.

Zero balances are included: the row records that the account was part of the
migration. Administrators, holding a null balance, are skipped and stay outside
the ledger.

**No history is invented.** A balance of 37 does not prove an initial 50 followed
by 13 charges — there may have been refunds, or direct database edits. A single
truthful `+37 legacy balance reconciled at migration` is the honest baseline, and
everything after it is fully auditable.

`grant_period` is left null on these rows, deliberately, so an existing account
still receives its grant during the migration month rather than being skipped by
the idempotency key.

The backfill is a guarded insert, so re-running it cannot duplicate a baseline.
Downgrading drops the table; that is lossy for audit history by nature, since a
ledger cannot be folded back into one scalar. No balance is disturbed in either
direction.

## Reading a balance and its history

| Endpoint | Who |
|---|---|
| `GET /api/users/me/credits` | The account itself. Evaluates the monthly grant first, and returns the policy alongside the balance. |
| `GET /api/users/me/credit-transactions` | The account itself. Newest first. |
| `GET /api/admin/users/{email}/credit-transactions` | Administrators, read-only. |
| `POST /api/admin/users/{email}/credits` | Administrators. The only manual write. |

`users.credits` remains the fast current balance that product surfaces read. The
ledger is the authority for audit and reconciliation. Summing thousands of rows
to render a header is not the intent.

`GET /api/users/me/credits` is the endpoint a user interface should read, not
`GET /api/auth/me`. Both report the balance, but only this one evaluates the
lazy monthly grant, so a balance taken from the authenticated snapshot can show
a stale zero on the first of the month and never correct itself.

It answers with the balance and the policy needed to explain it:

```json
{
  "credits": 0,
  "metering_enabled": true,
  "monthly_grant": 50.0,
  "balance_cap": 100.0,
  "next_grant_at": "2026-09-01T00:00:00Z",
  "generation_costs": { "study_guide": 1.0, "quiz_open_ended": 2.0 }
}
```

`credits` is null and every policy field is null when the account is not
metered, which is how a client knows to show no credit interface at all rather
than an inert zero. The policy travels with the balance so that an interface can
name the real recovery route and the real price without encoding either itself.

### Telling a refusal apart from any other failure

Every AI route classifies its failures through `utils/ai_errors.py`, and the
resulting code is sent as an `X-Error-Code` header beside the human-readable
`detail`:

```http
HTTP/1.1 402 Payment Required
X-Error-Code: insufficient_credits
```

A client should branch on that code rather than on the status alone: `402` may
one day cover another payment state, and two distinct retrieval failures already
share `409`. The header is the stable contract; the prose in `detail` is not.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `CREDIT_METERING_ENABLED` | `true` hosted, `false` self-hosted | Whether credits are enforced at all |
| `CREDIT_INITIAL_GRANT` | `50.0` | Registration allowance |
| `CREDIT_PERIODIC_GRANT` | `50.0` | Monthly allowance before trimming |
| `CREDIT_MAX_BALANCE` | `100.0` | Ceiling for automatic granting only |

Startup rejects a ceiling below either grant.

## For contributors

`services/credits.py` is the only module permitted to modify `users.credits`.
Every feature charges through `CreditService.charge` and refunds through
`CreditService.refund`; none of them touches the column. A change that
reintroduces `user.credits += ...` anywhere else is a regression, because it
silently breaks the invariant this whole document rests on.
