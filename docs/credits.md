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
| Administrator grant | Yes | No | Yes |
| Administrator adjustment (may be positive) | Yes | No | Yes |
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
| What happens at zero? | Generation returns HTTP 402 and no provider is called. |
| When is a charge applied? | Before the provider call, after material assembly. |
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
many race.

**Eligibility.** Active, metered, non-banned accounts. A banned account is
skipped and receives nothing for the months it stays banned.

### Administrator grant and adjustment

| Operation | Endpoint | Sign | Ledger reason |
|---|---|---|---|
| Grant | `POST /api/admin/users/{email}/credits/grant` | Positive only | `admin_grant` |
| Adjust | `POST /api/admin/users/{email}/credits/adjust` | Either | `admin_adjustment` |

Both take an optional `note` for human context, kept separate from the
machine-readable `reason` so history stays aggregatable.

The actor is taken from the authenticated administrator and is never read from
the request body, so a manual change always answers who received the credits,
who changed them, when, why, and how much.

Neither is bounded by `CREDIT_MAX_BALANCE`: both are deliberate acts. Both are
rejected against an account with a null balance, because there is no balance to
change.

This is account-level credit administration. It confers no authority over
another owner's course workspace, where administrators remain read-only.

### Charging and refunding

Charging is one atomic conditional update guarded by the balance, so two
simultaneous requests cannot overspend. A refused charge changes nothing at all:
no balance mutation and no ledger row, because the ledger records balance
changes rather than attempts.

A refund carries `refunds_transaction_id` pointing at the charge it reverses,
and that column is unique, so **a charge can be refunded at most once** even if a
failure handler runs twice. Refunds are not trimmed to the ceiling: they return
credit the account already held.

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
| `GET /api/users/me/credits` | The account itself. Evaluates the monthly grant first. |
| `GET /api/users/me/credit-transactions` | The account itself. Newest first. |
| `GET /api/admin/users/{email}/credit-transactions` | Administrators, read-only. |

`users.credits` remains the fast current balance that product surfaces read. The
ledger is the authority for audit and reconciliation. Summing thousands of rows
to render a header is not the intent.

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
