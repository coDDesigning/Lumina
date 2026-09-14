# SCRUM-208 production legal/compliance gate

This checklist is a release gate, not an aspirational policy. Every blocking
item must be resolved with a named owner and evidence before the hosted public
release.

## Engineering verification

- [ ] Public legal routes render without authentication on desktop and mobile.
- [ ] Direct navigation and refresh work through the application SPA fallback
  and the hosted CloudFront rewrite.
- [ ] Legal footer links resolve from the landing page and the legal pages, the only places the footer is shown.
- [ ] Registration exposes Terms and Privacy and records versioned
  acknowledgements; optional ad consent remains separate.
- [ ] `LEGAL_POLICIES_ENABLED=true` is set for the hosted deployment.
- [ ] AdSense or any other non-essential script is absent before consent and can
  be disabled later. `frontend/index.html` carries no ad script.
- [ ] Provider flow is rechecked against Gemini, OpenAI, Anthropic, Ollama,
  failover order, BYOK, local embeddings, and visual-understanding behavior.
- [ ] Hosted storage, eu-central-1 core region, CloudFront, Google SMTP,
  CloudWatch, operational logs, AI telemetry, snapshots, and S3 version expiry
  match the Privacy Notice.
- [ ] Course/document purge behavior and self-hosted backup rotation match the
  deletion and retention text.
- [ ] `LICENSE`, `SECURITY.md`, and `THIRD_PARTY_NOTICES.md` ship in public and
  container artifacts; dependency notice check passes.

## Publication blockers requiring owner/legal decisions

- [ ] Establish and publish the hosted service's formal legal entity/controller
  name and a valid postal address. “coDDesigning” is currently a project group,
  not a confirmed legal entity; `info@lumina-study.com` is only an email address.
- [ ] Name the final legal/compliance reviewer and record approval for Turkish
  KVKK requirements and, where applicable, GDPR and European AI/consumer rules.
- [ ] Finalise governing law, venue, controller disclosures, complaint-authority
  wording, liability cap, and mandatory consumer-right language.
- [x] Minimum age set to 16 by the project owner (14 September 2026), without an
  age-verification flow. Legal review must confirm this is sufficient for each
  launch market.
- [ ] Decide whether signed-out session identifiers and used verification/reset
  token hashes get a scheduled purge; the Privacy Notice currently says they are
  kept until the account is deleted.
- [ ] Account hard delete (SCRUM-209) is merged on dev; after merging dev into this
  branch, verify database,
  source storage, vectors, credentials, tokens, logs, retry metadata, and backup
  expiry. Until then, the Privacy Notice truthfully directs hosted requests to
  email rather than claiming an in-product deletion control.
- [ ] Confirm Google SMTP and each hosted AI/advertising/cloud provider's
  contracts, international-transfer mechanism, retention/training settings,
  data-processing terms, and final subprocessor disclosure.
- [ ] Validate the hosted AdSense consent flow for every launch jurisdiction;
  block AdSense where valid consent cannot be obtained.
- [x] Project owner confirmed `AGPL-3.0-only` (14 September 2026). Legal review
  still confirms the coDDesigning copyright notice.

## Approval record

- Release/version:
- Technical reviewer and date:
- Legal/compliance reviewer and date:
- Project owner and date:
- Evidence/decision links:

Production publication is blocked while any item in “Publication blockers” is
unresolved.
