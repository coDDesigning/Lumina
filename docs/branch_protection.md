# Branch Protection and Required Status Checks

This document defines the authoritative branch protection specification for
Lumina's primary branches (`main` and `dev`).

> [!IMPORTANT]
> **Policy vs. Live State**: The settings below represent the repository's
> intended protection policy. Because GitHub branch protection is configured
> through repository settings rather than repository source files, live
> enforcement must be verified and updated via GitHub API / administrative access.

---

## 1. Branch Strategy and Flow

Lumina follows a strict two-tier branch promotion model codified in the
`policy` job of [`.github/workflows/ci.yml`](../.github/workflows/ci.yml):

1. **Task Branches $\rightarrow$ `dev`**:
   - All feature, fix, chore, and documentation work must be developed on task
     branches branched from and targeting `dev`.
   - Branch names must adhere to the Jira pattern:
     `^(feature|fix|chore|docs)/SCRUM-[0-9]+-[a-z0-9][a-z0-9._-]*$`
     *(e.g., `feature/SCRUM-14-initial-ci`, `fix/SCRUM-31-course-delete-error`)*.
2. **`dev` $\rightarrow$ `main` (Promotion Only)**:
   - Only `dev` may open a pull request targeting `main`.
   - Direct task branch pull requests to `main` are rejected by CI policy.
   - Direct pushes to `main` are prohibited.

---

## 2. Required Status Checks (Blocking)

GitHub branch protection matches status checks against the **display name**
defined by the job's `name:` attribute in `.github/workflows/ci.yml`, **not** the
internal YAML job key. Protection rules must list the exact context strings
below:

| Job Key in `ci.yml` | Required Status Check Context (Display Name) | Purpose / Gate |
| :--- | :--- | :--- |
| `policy` | **`Branch and PR policy`** | Enforces valid PR target direction and Jira branch naming rules. |
| `repo-quality` | **`Repository quality`** | Verifies required repo files, secret absence, immutable references, UTF-8. |
| `backend-quality` | **`Backend quality and tests`** | Lints, type checks, runs Alembic migrations, runs full backend test suite. |
| `migration-governance` | **`Migration governance`** | Validates SQLite & PostgreSQL forward/backward migration lifecycles. |
| `postgresql-quality` | **`PostgreSQL quality`** | Runs live PostgreSQL suite and shared database contract tests. |
| `container-quality` | **`Container quality`** | Validates Dockerfiles, builds images, runs self-hosted compose integration. |
| `infrastructure-quality` | **`Infrastructure quality`** | Verifies Terraform formatting, initialization, validation, and unit tests. |
| `frontend-quality` | **`Frontend quality and build`** | Runs frontend linting, unit test coverage, and production build. |
| `frontend-e2e` | **`Frontend end-to-end`** | Runs Playwright browser suite across end-to-end user workflows. |

### Status Check Requirements
- **Non-Empty List**: `required_status_checks.contexts` must explicitly contain all 9 display names above.
- **Strict (Up-to-Date Branches)**: `strict: true` is required so pull requests must be tested against the latest base branch HEAD before merging.

---

## 3. Advisory Checks (Non-Blocking)

| Workflow | Job Display Name | Purpose | Status |
| :--- | :--- | :--- | :--- |
| `.github/workflows/pr-agent.yml` | **`PR Agent review`** | Automated AI code review and feedback | **Advisory only** |

As documented in [`docs/pr-agent.md`](pr-agent.md), `PR Agent review` must **never**
be added to the required status checks list. Keeping review automation advisory
ensures upstream model availability or quota issues cannot block deterministic
merges.

---

## 4. Review and Merge Governance

The following protection rules apply to `main` and `dev`:

- **Required Status Checks**: Non-empty list containing the 9 blocking CI display names with `strict: true` (require branch to be up to date before merge).
- **Required Approvals**: At least `1` approving review from a code reviewer (`required_approving_review_count: 1`) on both `main` and `dev`.
- **Dismiss Stale Approvals**: Approvals are automatically dismissed when new commits are pushed (`dismiss_stale_reviews: true`) on both `main` and `dev`.
- **Protection on `main`**: Force pushes and branch deletions are prohibited on `main` (`allow_force_pushes: false`, `allow_deletions: false`).

---

## 5. Live Verification & Audit Instructions

Repository administrators can inspect and capture live branch protection payloads
using the GitHub CLI (`gh`).

### Export Live Payloads (SCRUM-181 Evidence)

```bash
# Capture live protection settings for main
gh api repos/coDDesigning/Lumina/branches/main/protection > main_protection.json

# Capture live protection settings for dev
gh api repos/coDDesigning/Lumina/branches/dev/protection > dev_protection.json
```

### Verification Checklist

When reviewing live protection payloads:

- [ ] `required_status_checks` is present and not `null` on both `main` and `dev`.
- [ ] `required_status_checks.strict` is `true` on both `main` and `dev`.
- [ ] `required_status_checks.contexts` contains all 9 required display names on both `main` and `dev`:
  - `Branch and PR policy`
  - `Repository quality`
  - `Backend quality and tests`
  - `Migration governance`
  - `PostgreSQL quality`
  - `Container quality`
  - `Infrastructure quality`
  - `Frontend quality and build`
  - `Frontend end-to-end`
- [ ] `PR Agent review` is **absent** from `required_status_checks.contexts`.
- [ ] `required_pull_request_reviews.required_approving_review_count` $\ge 1$ on both `main` and `dev`.
- [ ] `required_pull_request_reviews.dismiss_stale_reviews` is `true` on both `main` and `dev`.
- [ ] On `main`, `allow_force_pushes.enabled` is `false` and `allow_deletions.enabled` is `false`.
