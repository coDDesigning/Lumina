# Frontend System

How Lumina's React frontend is built, and the rules a new screen must follow so the
product keeps one visual and interaction language. Testing conventions live in
[`frontend_testing.md`](frontend_testing.md).

## Stack

React 19 + TypeScript + Vite. Routing is `react-router-dom` 7. Icons are `lucide-react`.
There is no CSS framework, no component library, no state manager and no data-fetching
library — server state lives in feature hooks, UI state in `useState`.

Styling is **CSS Modules over a design-token layer**. Fonts are self-hosted, so a
self-hosted deployment makes no third-party requests.

```
cd frontend
npm ci --no-audit --no-fund
npm run lint     # eslint, scoped to src + vite.config.ts by design
npm test         # vitest
npm run build    # tsc -b && vite build
```

`npm run lint` passing does not mean the build passes — `npm run build` runs `tsc -b`
first, and type errors in test files fail it. Run all three.

## Directory layout

| Path | Holds |
|---|---|
| `src/styles/` | `tokens.css`, `base.css`, `fonts.css` — the only global CSS |
| `src/ui/` | Design-system primitives. No feature knowledge, no API calls. |
| `src/app/` | Shell, routing chrome, theme, error boundary |
| `src/features/<domain>/` | Screens and the hooks/dialogs they own |
| `src/api/` | One module per backend area + `client.ts`, `types.ts`, `errors.ts` |
| `src/hooks/`, `src/lib/` | Cross-feature hooks and helpers |
| `src/components/` | Legacy, mid-migration. Do not add to it. |

Import with the `@/` alias, not deep relative paths.

## Design tokens

`src/styles/tokens.css` is the single source of colour, type, space, radius, elevation,
z-index, breakpoint and motion.

**Components consume roles, never literals.** There is no `#7c4ddb` in a component file;
there is `var(--accent)`. Colour roles are semantic — `--surface`, `--surface-raised`,
`--surface-sunken`, `--text`, `--text-muted`, `--text-subtle`, `--border`, `--accent`,
`--success`, `--warning`, `--destructive`, `--processing`, `--info` — each status role
paired with a `-subtle` background.

Theming has three states, and the token file handles all three:

- bare `:root` defines the complete light palette
- `@media (prefers-color-scheme: dark) { :root:not([data-theme='light']) }` redefines the
  role values for a viewer on system-dark
- `:root[data-theme='dark']` redefines them again so the toggle wins in both directions

Never give a colour its only definition inside a media or `[data-theme]` block, and never
add a colour to a component that is not a token.

Motion tokens zero out under `prefers-reduced-motion`, so a transition written with
`var(--duration-fast)` is automatically respectful.

## The course light

Each course has a hue derived from its id by `courseHue()` in `src/lib/courseLight.ts`,
mapped onto a curated 12-hue table so adjacent courses never collide. `<CourseLight>`
washes a surface with it; `<CourseChip>` is the dot.

It is **identity only**. It never carries status — status has its own roles. There is no
colour column on `courses` and none is needed.

## Primitives

Everything in `src/ui/`. Use them; do not hand-roll an equivalent.

`Alert` · `Badge` · `Brandmark` · `Breath` · `Button` · `Card` · `Checkbox` ·
`ConfirmDialog` · `CourseLight` · `Dialog` · `EmptyState` · `Field` · `IconButton` ·
`Input` / `Textarea` / `Select` · `LinkButton` · `PageHeader` · `Skeleton` · `Spinner` ·
`Switch` · `Tabs` · `ToastProvider`

Rules that are not negotiable:

- **`IconButton` requires `label`.** An icon-only control cannot ship without an
  accessible name, because the type won't compile.
- **Never nest a link in a button or a button in a link.** Use `LinkButton` for a
  navigation control that looks like a button.
- **`Dialog` is the only modal.** It traps focus, restores it to the trigger, closes on
  Escape and locks page scroll. Do not build a backdrop.
- **`ConfirmDialog` is the only destructive confirmation.** No `window.confirm`, no
  bespoke two-step inline confirm. Pass `confirmPhrase` for anything irreversible.
- **The shell owns the single `<main>`.** A page must not render its own.
- Status is never carried by colour alone — a `Badge` always renders its label, and
  status badges pass an icon as a third channel.
- Two controls on one screen must not share an accessible name. "Reset" and "Reset" is a
  bug; "Reset details" and "Reset defaults" is the fix.

## Information architecture

```
/                                  landing (redirects to /dashboard when signed in)
/login  /register                  unauthenticated, no shell

  ── everything below is inside ProtectedRoute > AppShell ──
/dashboard                         course list
/workspaces/:id                    course workspace (chat-first)
/workspaces/:id/progress           progress and topic mastery
/workspaces/:id/settings           course details, generation defaults, danger zone
/workspaces/:id/edit               → redirects to /settings
/settings                          → redirects to /dashboard
/profile                           account
/admin                             admin (rail entry hidden unless role === 'admin')
```

The shell is a left rail (bottom bar under 48rem). Every page renders its own
`<PageHeader>` with breadcrumbs; the last crumb is the current location, is never a link,
and carries `aria-current="page"`. A course-scoped page passes `courseId` so the header
carries that course's light — **a student must never lose track of which course they are
operating in.**

## The workspace

Chat-first: sources rail, conversation, study-tools rail. The conversation has exactly two
threads, because that is what the backend models — `course_qa` and `ai_tutor` are separate
conversation types and a thread id from one is a 404 in the other. Do not add a third tab
that renders the same thread with different copy.

## AI interaction conventions

Every AI route returns an `X-Error-Code` header. **Branch on that header, never on the
status code** — 409 is already shared by two distinct retrieval failures, and the `detail`
prose is explicitly not a stable contract. Codes to handle distinctly:

`no_ready_material` · `no_relevant_material` · `material_not_indexed` ·
`retrieval_unavailable` · `provider_unavailable` · `provider_timeout` ·
`provider_rate_limited` · `invalid_generated_structure` · `insufficient_credits` ·
`generation_failed`

Use `describeGenerationError()` and `isInsufficientCredits()` from `src/api/errors.ts`.

**Retrieval provenance:** `retrieval_narrowed` (retrieval chose a subset — the normal case
since retrieval landed) and `context_truncated` (the budget dropped a passage retrieval had
already selected — an actual loss) mean different things and must never be collapsed into
one message.

## Credits

- Read the balance from `GET /api/users/me/credits`, **never** `/auth/me` — only the
  former evaluates the lazy monthly grant.
- `credits === null` means **unmetered**. Render no credit UI at all. A zero is not a
  stand-in; zero and "not applicable" mean opposite things. Administrators and every
  self-hosted deployment live in this state permanently.
- Never hardcode a price. `generation_costs` is served live. A quiz containing open-ended
  questions costs `quiz_open_ended`, not `quiz`, so derive cost from the requested
  `question_types`.

## Documents

Status lifecycle: `uploaded → processing → ready | failed`, plus a `deleting` tombstone.
Seven internal stages become plain phrases; the words "job", "worker", "chunk" and
"embedding" never reach a student.

Three rules the UI enforces so nobody discovers them through an error:

- **Retry exists only in the `failed` state.** Anything else is a 409.
- **Delete is blocked while queued, running, or locked by an in-flight generation.**
  Disable it with a reason rather than surfacing the 409 afterwards.
- **A duplicate upload is a notice, not a failure.** It returns 200 with
  `duplicate: true` and the existing row.

## Quiz

- Quiz reads **always include the correct answer**. There is no server-side take mode, so
  hiding it during an attempt is entirely the frontend's job.
- `score` is a **0.0–1.0 fraction**; `mastery_percentage` is 0–100.
- An answer the model could not grade returns `is_correct: null` and `score: null`. It is
  excluded from the denominator and must read as **"not scored"**, never as wrong.
- Attempt validation failures are **400, not 422**.

## Honesty rules

These are product rules, not style preferences.

- **No screen shows fabricated data.** If the backend cannot produce a number, the number
  is absent — not zero, not a placeholder. Every course card once read "0 sources" forever
  because the mapper hardcoded an empty array; that is the failure mode to avoid.
- **No control claims an outcome it does not produce.** A settings page that said
  "Preferences saved locally" while persisting nothing is worse than no message.
- **Name what is not built.** The landing page lists unbuilt capabilities as prominently as
  built ones.
- **Label a value as what the API returns.** Average score is "average score", not "course
  progress". `quizzes_completed` is the attempt count. `completion` is the average score,
  not coverage.

## States every screen owes the user

Loading (a skeleton shaped like the content, not a lone spinner), initial empty (an
invitation with the next action, not an apology), populated, processing, failed with a
recovery route, unauthorised, not-found, and success confirmation.

A course you do not own returns the same 404 as one that does not exist — deliberately, so
ids cannot be probed. Do not distinguish them in the UI.

Actions that cannot act on anything are **absent**, not disabled.

## Responsive

Breakpoint tokens: 30 / 48 / 64 / 80 rem. The workspace goes three columns → two (drops the
tools rail) → one (drops the sources rail). The rail becomes a bottom bar under 48rem.
Wide content scrolls inside its own container; the page body never scrolls sideways.

## Accessibility baseline

WCAG 2.1 AA. Semantic HTML, one `<h1>` per screen, no skipped heading levels, labels
associated by id, visible focus from `:focus-visible`, focus trapped and restored in every
dialog, accessible names on every icon-only control, `aria-live` for async status changes
(uploads, processing transitions, arriving answers), no colour-only status, and
`prefers-reduced-motion` respected through the motion tokens.

## Adding a new screen

1. Route it in `src/App.tsx` inside `ProtectedRoute > AppShell`.
2. Create `src/features/<domain>/<Name>Page.tsx` + `<Name>Page.module.css`.
3. Render `<PageHeader>` with breadcrumbs, and `courseId` if course-scoped.
4. Build from `src/ui` primitives. Consume tokens; write no literal colour.
5. Call `useDocumentTitle()`.
6. Handle loading, empty, error and permission states deliberately.
7. Test behaviour — roles and user-visible text, not classes.
