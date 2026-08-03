# PR-Agent Operations

## Purpose

PR-Agent adds an automated first review to Lumina pull requests. It supplements
human review and deterministic Continuous Integration checks; it does not prove
that code compiles, tests pass, migrations work, or a deployment is safe.

The repository uses the open-source PR-Agent GitHub Actions image. A separate
self-hosted webhook server is unnecessary for this five-person, single-repository
project because GitHub can start one isolated reviewer container for each event.

## Security And Data Flow

The workflow reads pull-request metadata, commit messages, changed code, nearby
code context, and the reviewed repository instructions from GitHub. It sends the
relevant prompt content directly to Google Gemini. The open-source PR-Agent
project states that it does not proxy this content through PR-Agent-operated
servers. The team must still accept Google's data-processing terms before
enabling the workflow on this private repository.

The job uses restricted mode and these explicit permissions:

- `contents: read` reads repository and diff content.
- `issues: write` publishes a top-level pull-request comment.
- `pull-requests: write` publishes PR-Agent review findings.

The action cannot push source changes. It is also intentionally separate from
the required CI jobs, so an unavailable model or exhausted quota cannot approve
or block a merge by itself.

The workflow fails its own optional check when the Gemini secret is absent or the
provider request fails. This prevents a misleading green result with no review.
Do not add `PR Agent review` to required branch checks.

This workflow uses the normal `pull_request` event so it can run while `main` is
still an initial placeholder. For same-repository branches, proposed workflow
code can receive Actions secrets. This design therefore assumes every developer
with write access is trusted. Use a dedicated Gemini project and key with a
strict spending or request quota; never reuse Lumina's application Gemini key.

If repository write access is later granted to less-trusted contributors, move
the workflow to `pull_request_target`, keep it free of checkout steps, and first
place the trusted workflow on the default branch.

## One-Time Setup

Install and authenticate GitHub CLI before opening the bootstrap pull request:

```bash
# Open GitHub's browser-based login and select the coDDesigning organization.
gh auth login

# Confirm the account, host, Git protocol, and token scopes now in use.
gh auth status
```

Before marking the bootstrap pull request ready, create a dedicated,
quota-limited Gemini key in Google AI Studio. Add it interactively so the value
is not placed in shell history, source code, `.env`, or `.env.example`:

```bash
# gh securely prompts for the secret value and encrypts it for GitHub Actions.
gh secret set PR_AGENT_GEMINI_API_KEY

# This lists secret names and update times; GitHub never returns secret values.
gh secret list
```

The expected list contains `PR_AGENT_GEMINI_API_KEY`. Do not paste the real key
into an issue, pull-request body, terminal command argument, or chat message.

PR #21 is the bootstrap pull request. Its workflow repeats the security-critical
settings as environment variables because `.pr_agent.toml` is not yet available
on the trusted `dev` branch. After PR #21 is merged, later reviews load the full
policy from `dev`; the next eligible pull request is therefore the first complete
end-to-end validation of the repository configuration.

## Review Behavior

PR-Agent reviews non-draft pull requests targeting `dev` or `main`. It runs when
a pull request is opened, reopened, marked ready, or receives another commit.
Bot events and forked repositories are ignored. GitHub may also postpone the
event while a pull request has merge conflicts.

GitHub may not run a newly introduced workflow on its own bootstrap pull request.
Marking PR #21 ready validates the workflow-level bootstrap settings. The first
guaranteed review using every setting in `.pr_agent.toml` is the next eligible
pull request after SCRUM-50 is merged to `dev` and the Gemini secret exists.

The workflow loads `.pr_agent.toml` from the fixed `dev` branch. This is necessary
while GitHub's default `main` branch contains only the initial placeholder. Never
replace that fixed value with a pull-request branch or `${{ github.head_ref }}`;
an untrusted branch could then redirect model traffic or change review policy.

The PR-Agent check is advisory. Its own failures remain visible for diagnosis,
but the check must not be configured as a required merge gate. Deterministic CI
and human review remain authoritative when the AI provider is unavailable.

## Handling Findings

For every finding, determine what behavior PR-Agent believes is wrong, reproduce
or reason about the failure, and classify it as correct, incorrect, or subjective.
Only then change the code or explain on the review thread why no change is needed.
PR-Agent feedback never replaces the required human approval and local checks.

## Updating PR-Agent

The workflow currently uses PR-Agent `0.41.0` through this immutable image index:

```text
pragent/pr-agent@sha256:3121a7b8e3ab497b4df09dea6a444da8f01a474496c8c2f722828512c52fd266
```

Before upgrading, read the release notes, resolve the new release digest, and
verify its GitHub artifact attestation:

```bash
# Read the multi-platform digest without running the image.
docker buildx imagetools inspect \
  pragent/pr-agent:<version>-github_action \
  --format '{{.Manifest.Digest}}'

# Verify that the immutable image was published from the official repository.
gh attestation verify \
  "oci://index.docker.io/pragent/pr-agent@sha256:<digest>" \
  --repo The-PR-Agent/pr-agent
```

Update the digest and version documentation in one reviewed pull request. Never
replace the digest with mutable references such as `main`, `latest`, or
`github_action`.

## Why Not A Persistent Self-Hosted App

A persistent GitHub App requires a public webhook endpoint, app private key,
webhook secret, monitoring, upgrades, and an always-available compute service.
Those costs do not improve review quality for Lumina today. Reconsider that model
only if several repositories need centralized policy or the team deploys an
always-available private model endpoint.
