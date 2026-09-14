# Legal policy sources and update process

The public English policy copy is versioned in
`frontend/src/features/legal/legalDocuments.tsx`. Routes, the persistent footer,
and account-creation acknowledgement use that source together with
`backend/app/legal.py`.

| Public route | Source object | Purpose |
| --- | --- | --- |
| `/legal/privacy` | `privacyPolicy` | Hosted/self-hosted privacy and retention notice |
| `/legal/terms` | `termsOfService` | Hosted service terms and content ownership |
| `/legal/acceptable-use` | `acceptableUsePolicy` | Platform, security, IP, and academic-integrity rules |
| `/legal/cookies` | `cookiePolicy` | Cookies, localStorage, and optional-ad consent |
| `/legal/ai-disclosure` | `aiDisclosure` | AI and educational limitations/provider flow |
| `/legal/security` | `securityPolicy` | Private responsible disclosure |
| `/legal/open-source` | `openSourcePolicy` | AGPL and third-party notice links |

The package is switched by `LEGAL_POLICIES_ENABLED` (default `false`, accepts
`true`/`false` or `yes`/`no`). `GET /api/legal/config` reports the flag to the
frontend. The legal footer is rendered only on the landing page and the legal pages, and the short AI
notice only under each "Use my study profile" control. While it is off, `/legal/*`
redirects to `/`, neither is rendered, registration shows no acknowledgement, and
no `policy_acknowledgements` rows are written. While it is on, registration
requires `policies_acknowledged: true` and records the Terms and Privacy
versions. Hosted Terraform sets it to `true`.

Repository-level sources are `LICENSE`, `SECURITY.md`, and
`THIRD_PARTY_NOTICES.md`. Version 1.0 is effective 12 September 2026.

## Change process

1. Compare proposed text against current provider routing, hosted and
   self-hosted storage, email, logging, backup, deletion, browser storage,
   advertising, credits, and security configuration.
2. Update the policy source, effective date, and version. Update
   `backend/app/legal.py` when Terms or Privacy versions change.
3. Decide with legal review whether the change requires notice only or renewed
   Terms agreement. Never represent a Privacy Notice acknowledgement as optional
   consent. Add any optional consent separately and gate the relevant processing.
4. Update `THIRD_PARTY_NOTICES.md` when runtime dependencies, bundled tools,
   fonts, or models change. Run `python scripts/check_legal_artifacts.py`.
5. Run frontend route/link/registration tests, direct-refresh tests for both
   delivery modes, backend migration tests, and the release checklist.
6. Obtain the jurisdiction-specific legal/compliance approval recorded in the
   release checklist before publishing to production.

Material changes must be announced in-product or by email as appropriate. A
material change to user obligations requires renewed agreement where legal
review says it is necessary. Processing that requires consent must obtain a new,
specific choice before it starts.
