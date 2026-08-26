# Frontend System

How Lumina's React frontend is built, and the rules a new screen must follow so the
product keeps one visual and interaction language. Testing conventions live in
[`frontend_testing.md`](frontend_testing.md).

## Stack

React 19 + TypeScript + Vite. Routing is `react-router-dom` 7. Icons are `lucide-react`.
There is no CSS framework, no component library and no state manager. There is no
data-fetching *dependency* either: server state is owned by `src/lib/query/`, a small
in-repo query cache described below. UI state stays in `useState`.

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

## Production API routing

`VITE_API_BASE_URL` is the complete API prefix embedded by Vite at build time.
It defaults to `/api`, which is the hosted production setting: CloudFront serves
the SPA and forwards `/api` and `/api/*` to ECS through the ALB under the same
browser origin. The development server preserves that contract with its `/api`
proxy. Changing an API container environment after the frontend is built does
not alter the bundle.

For a deliberately cross-origin build, use an absolute prefix such as
`https://api.example.com/api`. The API must then set
`CORS_ALLOWED_ORIGINS=https://app.example.com`. Origins are exact, wildcards
are rejected, cookie credential mode is disabled, and the browser continues to
authenticate with the explicit `Authorization: Bearer` header. The backend
exposes `X-Error-Code` so cross-origin clients retain the normal AI error
contract. Empty `CORS_ALLOWED_ORIGINS` installs no CORS middleware and is the
same-origin production default.

## Directory layout

| Path | Holds |
|---|---|
| `src/styles/` | `tokens.css`, `base.css`, `fonts.css` — the only global CSS |
| `src/ui/` | Design-system primitives. No feature knowledge, no API calls. |
| `src/app/` | Shell, routing chrome, theme, error boundary |
| `src/features/<domain>/` | Screens and the hooks/dialogs they own |
| `src/api/` | One module per backend area + `client.ts`, `types.ts`, `errors.ts` |
| `src/hooks/`, `src/lib/` | Cross-feature hooks and helpers |
| `src/components/` | The route guard, document rows, credit chips. Prefer `src/features/`. |

Import with the `@/` alias, not deep relative paths.

There is no page-level or component-level global CSS left. Every component that needs styling
owns a `Name.module.css` beside it, and every value in it comes from a token — a test
(`src/styles/tokenUsage.test.ts`) fails the build if a module references a custom property
nothing defines, because an undefined `var()` fails silently in CSS.

## Design tokens

`src/styles/tokens.css` is the single source of colour, type, space, radius, elevation,
z-index, breakpoint and motion.

**Components consume roles, never literals.** There is no `#1a56c4` in a component file;
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

**A theme branch belongs in `tokens.css` and nowhere else.** `ThemeProvider` removes
`data-theme` entirely when the preference is `system`, so a component rule written as
`[data-theme='dark'] .thing` never fires for a viewer on system-dark who never touched
the toggle — they silently get the light value on a dark surface. The skeleton sheen and
the select chevron both had this bug. Add a role to the token file, define it in all
three blocks, and let the component read one `var()`.

Motion tokens zero out under `prefers-reduced-motion`, so a transition written with
`var(--duration-fast)` is automatically respectful.

**The shapes are square on purpose.** The radius scale runs 2/3/5/8px, not the half-inch
pillows a component library ships with. `--radius-full` is reserved for things that are
genuinely circular — a course dot, a bullet, a switch track — and using it on a chip or a
bar puts the interface back where it started. Surfaces are drawn with a hairline border and
almost no shadow, because an edge reads as deliberate where a glow reads as generic.

**Labels are not shouted.** No `text-transform: uppercase`, and `--tracking-label` is
0.01em. A wall of wide-tracked capitals is the single loudest signal that nobody chose the
typography.

Two tests hold the palette to account: `darkTokens.test.ts` proves the two dark blocks
define exactly the same tokens with exactly the same values, and `contrast.test.ts`
computes WCAG ratios for every text-on-surface and status-on-subtle pair in both themes —
body text at 4.5:1, supporting text at 3:1. Three of the light status colours were too pale
to pass when it was first written, which is precisely the kind of thing an eye lets through.

## The course light

Each course has a hue derived from its id by `courseHue()` in `src/lib/courseLight.ts`,
mapped onto a curated 12-hue table so adjacent courses never collide. `<CourseLight>`
washes a surface with it; `<CourseChip>` is the dot.

It is **identity only**. It never carries status — status has its own roles. There is no
colour column on `courses` and none is needed.

## Primitives

Everything in `src/ui/`. Use them; do not hand-roll an equivalent.

`Alert` · `Badge` · `Brandmark` · `Breath` · `Button` · `Card` · `Checkbox` ·
`ConfirmDialog` · `CourseLight` · `Dialog` · `EmptyState` · `ErrorState` · `Field` ·
`IconButton` · `Input` / `Textarea` / `Select` · `LinkButton` · `MasterDetail` ·
`PageHeader` · `Skeleton` · `Spinner` · `Switch` · `Tabs` · `ToastProvider`

Import them by module — `import { Button } from '@/ui/Button'`. There is no barrel;
`src/ui/index.ts` was deleted because nothing imported it and it had already drifted
out of date.

Rules that are not negotiable:

- **`IconButton` requires `label`.** An icon-only control cannot ship without an
  accessible name, because the type won't compile.
- **`ErrorState` is how a failed operation is reported.** It wraps `Alert` with
  `tone="destructive"` and `live="alert"`, and takes `onRetry` for a retry or
  `actions` for a way out. A screen that reports a load failure with no recovery
  route is incomplete. A plain `Alert` stays correct for a form validation error,
  where the recovery route is the form the user is already looking at.
- **Never nest a link in a button or a button in a link.** Use `LinkButton` for a
  navigation control that looks like a button.
- **`Dialog` is the only modal.** It traps focus, restores it to the trigger, closes on
  Escape and locks page scroll. Do not build a backdrop.
- **`ConfirmDialog` is the only destructive confirmation.** No `window.confirm`, no
  bespoke two-step inline confirm. Pass `confirmPhrase` for anything irreversible.
- **The shell owns the single `<main>`.** A page must not render its own.
- **No two controls in one dialog answer to the same name.** `Dialog` already renders a
  dismiss control called "Close", so a footer button must be called something else —
  "Done", "Not now", "Cancel". Two identical names make the dialog unusable by voice and
  ambiguous by screen reader.
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
/activity                          what you generated and attempted, every course
/courses/:id                       course workspace (chat-first)
/courses/:id/guides/:outputId      one study guide, its own page
/courses/:id/practice/:quizId      taking a quiz
/courses/:id/practice/:quizId/attempts/:attemptId   what you scored
/courses/:id/progress              progress, topic mastery, quizzes to retake
/courses/:id/settings              course details, generation defaults, danger zone
/account                           who you are, and your level
/account/background                profile knowledge
/account/ai                        model choice, and credits when metered
/account/appearance                theme
/admin                             admin (rail entry hidden unless role === 'admin')

  -- kept so older links still resolve --
/workspaces/:id/**                 -> /courses/:id/**
/courses/:id/edit                  -> /courses/:id/settings
/profile                           -> /account
/settings                          -> /dashboard
```

The shell is a left rail (bottom bar under 48rem). Every page renders its own
`<PageHeader>` with breadcrumbs; the last crumb is the current location, is never a link,
and carries `aria-current="page"`. A course-scoped page passes `courseId` so the header
carries that course's light — **a student must never lose track of which course they are
operating in.**

A generated artifact has an address. Setting one up still happens in a dialog, but the
moment it exists the reader is sent to its own page — so a study guide can be linked, a quiz
attempt survives a refresh, and the back button behaves. Reviewing a past attempt
question by question is the one thing still missing, because the backend serves an attempt
only in the response to handing it in.

The URL says the same word the screen says. Everything visible calls these courses, so the
route is `/courses/:id`, not the `/workspaces/` the old code used. Account is four
addressable sections rather than one long scroll, because identity, background, model
choice and theme are four unrelated things and each deserves its own link.

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

## Generation failures

Every AI route sends `X-Error-Code`, and that header — not the status — is the contract.
`no_relevant_material` and `material_not_indexed` both return **409**, so branching on the
status merges two failures that need different words and different next steps: one is the
reader's topic being too narrow, the other is an indexing gap on our side that a retry can
clear.

`describeGenerationError` in `src/api/errors.ts` is the single mapper. Each known code owns
its title, its message, whether a retry can help, and which remedy to offer
(`broaden_topic`, `see_sources`, `shorten`, or none). `GenerationError` renders those
remedies as real buttons. An unknown code keeps whatever the server said rather than
replacing it with a generic apology.

Where credits are involved, the message says so: a provider that could not be reached says
nothing was charged, and an unreadable response says the credit was refunded.

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
- The attempt carries `graded_count` as well as `total_questions`. Score is correct out of
  **graded**; anything ungraded is counted separately and never as an error. Deriving
  "incorrect" as `total - correct` marks unscored written answers wrong, which is the bug
  `tallyAttempt` exists to prevent.
- Each question type gets its own layout: a radio group for multiple choice, a two-way
  choice for true/false, a line for short answer, a box for a written answer. Options are
  real `input[type=radio]` in one named group, never styled buttons — a screen reader must
  be able to say how many choices there are and which is chosen.

## Honesty rules

These are product rules, not style preferences.

- **No screen shows fabricated data.** If the backend cannot produce a number, the number
  is absent — not zero, not a placeholder. Every course card once read "0 sources" forever
  because the mapper hardcoded an empty array; that is the failure mode to avoid.
- **Unavailable is not empty.** A course card reads progress from the single
  `GET /api/progress` request and distinguishes three states, not two: progress that could
  not be loaded says "Progress unavailable", a course with no graded attempt says what it
  is still waiting for, and a scored course shows its average score. A nullable number
  collapsed the first two, so a network failure read as a course the student had never
  touched. The status badge, the time-spent line and the last-studied line come from the
  same response and are omitted entirely when it is missing, because a card that cannot
  load progress must claim nothing.
- **The client derives no course status.** `status` arrives on the progress response and
  `services/course_status.py` is its only owner; `COURSE_STATUS_LABELS` in `api/types.ts`
  maps it to a label and `CoursesPage` to a `BadgeTone`. Five values, furthest-along wins:
  `no_documents` reads "No sources ready", `processing` reads "Processing", `ready` reads
  "Ready to study", `practiced` reads "Practiced", `mastered` reads "Mastered". The backend
  compares the average score rounded the way this client prints it, so the badge can never
  read "Practiced" beside the number 80. The badge label is deliberately "No sources ready"
  rather than "No documents", because that state also covers a course whose uploads all
  failed to process. The line beneath follows the same signal: a course with nothing to
  study says "No sources to study yet" and one still being read says "Sources still
  processing", so "No quiz activity yet" is only ever shown to a student who actually has
  material to be quizzed on.
- **Time spent is absent, never zero.** `total_time_spent_seconds` on the progress reads
  and `time_spent_seconds` on each `quiz_history` item are null when no attempt recorded a
  duration, and `formatStudyTime` returns null for anything at or below zero, so the card
  line and the attempt-history column disappear rather than reading "0m".
- **No control claims an outcome it does not produce.** A settings page that said
  "Preferences saved locally" while persisting nothing is worse than no message.
- **Name what is not built.** The landing page lists unbuilt capabilities as prominently as
  built ones.
- **Label a value as what the API returns.** Average score is "average score", not "course
  progress". `quizzes_completed` is the attempt count — the progress screen says "quiz
  attempts" and adds that retaking the same quiz counts again. `completion` is the average
  score, not coverage.
- **A disabled control explains itself in text.** A `title` on a disabled button reaches
  nobody: disabled buttons are not focusable and the tooltip is not announced. When delete
  is unavailable because the source is still being read, that sentence is on the screen.

## Looking at it in a browser

Tests and contrast maths do not catch a bounced deep link or a chevron tiling across a
focused select. Three throwaway tools under `.user/scripts/` make a real browser cheap:

- `stub_api.py` answers on :8000 with the shapes the client expects, so every authenticated
  screen renders without a database, a worker or a model provider — including the states a
  real backend will not reproduce on demand.
- `shoot.mjs` drives headless Chrome over the DevTools Protocol with no dependencies at all,
  seeds an auth token, captures each route full height, and prints anything the page logged
  as an error.
- `a11y-audit.mjs` drives the same browser the same way but measures instead of capturing:
  per route, whether the page body scrolls sideways and what is sticking out, the heading
  outline, the `<h1>` and `<main>` counts, and any control rendered without an accessible
  name. It exits non-zero when a route has a problem, so it reads as a check rather than a
  report.

```
python .user/scripts/stub_api.py &
npm --prefix frontend run dev &
node .user/scripts/shoot.mjs /tmp/ui 1440 /dashboard /courses/1 /courses/1/progress
node .user/scripts/a11y-audit.mjs 360 /dashboard /courses/1 /courses/1/settings
```

None is used by the app, the tests or CI. Three real defects came out of the first two
runs: a course link opened cold bounced to the dashboard, a focused select tiled its chevron
across the field, and one malformed poll response took the whole workspace down.

The third exists because jsdom has no layout engine, so no vitest test can see a page that
scrolls sideways. It found the one that did: a long course name inside a nowrap button
pushed `/courses/:id/settings` to 413px in a 360px viewport. `Button` now takes `wrap` for
the few labels that carry user text — the course name stays on the button, because naming
what is about to be deleted is what stops a misclick.

## What the test suite enforces

Beyond the per-screen tests, five guards catch whole classes of mistake:

- **React warnings fail the test.** `setupTests.ts` promotes duplicate keys, invalid DOM
  nesting, missing `act`, and bad ARIA into assertion failures. These used to scroll past
  in green runs; a real duplicate-key bug in the topic pickers survived that way.
- **Unknown design tokens fail the test.** `tokenUsage.test.ts` reads every
  `*.module.css` and rejects a `var(--x)` that nothing defines.
- **Raw visual values fail the test.** `rawValues.test.ts` reads every
  `*.module.css` and every non-test `.ts`/`.tsx` and rejects a colour literal —
  hex, URL-encoded hex inside a data URI, a named colour, or a colour function
  whose arguments hold no `var(--`. `hsl(var(--light-hue) …)` is therefore fine
  and `hsl(0 0% 100% / 0.28)` is not. `DOCUMENTED_EXTERNAL_VALUES` is the only
  way through, it needs a reason, and an entry that no longer matches the file
  fails too, so an exemption cannot outlive what it excused.
- **A component may not branch a theme.** The same test rejects `[data-theme]`
  and `prefers-color-scheme` in a `*.module.css`. A component that branches on
  only one of them is wrong for the third theme state, as the token section above
  explains.
- **Regression tests are checked against the bug.** When you fix something, confirm the new
  test fails with the fix reverted. A test that passes either way protects nothing.

## States every screen owes the user

Loading (a skeleton shaped like the content, not a lone spinner), initial empty (an
invitation with the next action, not an apology), populated, processing, failed with a
recovery route, unauthorised, not-found, and success confirmation.

A course you do not own returns the same 404 as one that does not exist — deliberately, so
ids cannot be probed. Do not distinguish them in the UI.

Actions that cannot act on anything are **absent**, not disabled.

## Responsive

Breakpoint tokens: 30 / 48 / 64 / 80 rem, and `styles/breakpoints.test.ts` fails a media
query that breaks anywhere else — media queries cannot read a custom property, so the token
scale is a convention a test has to hold up.

The workspace goes three columns → two → one. **Nothing is ever removed by width.** Under
64rem the study-tools rail moves below the conversation; under 48rem all three stack, in the
order conversation, sources, study tools. The rails used to be `display: none` under 68rem
and 52rem with no other route to them, which meant a reader on a phone could not upload a
source, retry a failed one, or generate anything — roughly half the product. Do not
reintroduce a width that hides a capability instead of moving it.

The rail becomes a bottom bar under 48rem and reserves its own space plus
`env(safe-area-inset-bottom)`. Form controls grow to 16px under `(pointer: coarse)`, because
Safari zooms the viewport when a focused control is smaller and pans the page away from what
the reader was typing. Wide content scrolls inside its own container; the page body never
scrolls sideways, and a scrollable container carries `tabIndex={0}` and a name so it can be
reached without a mouse.

## Server state

`src/lib/query/` is a ~450-line cache: keyed entries, one in-flight request per key,
trailing refresh, staleness, focus revalidation, prefix invalidation and optimistic writes.
It was adopted by explicit team decision in place of TanStack Query, so the
frontend still ships no data-fetching dependency.

- **Keys come from `src/api/queryKeys.ts` and nowhere else.** `lib/query/keyDiscipline.test.ts`
  fails an inline key literal, because a typo'd key silently makes an invalidation do nothing
  and has no runtime symptom.
- **Prefix matching is element-wise.** `["course",1` is a string prefix of
  `["course",11,…]`, so matching on the serialised key would let course 1 invalidate
  course 11. `['courses']` is deliberately a different first segment from `['course', id]`.
- **`status: 'error'` and an empty result are different values**, which is how
  "unavailable is not empty" stays true. `error` means the load failed; `refetchError` means a
  background refresh failed while good data is still on screen.
- **Unmount does not abort.** Only `remove()` and `clear()` do. That is what makes
  StrictMode's double mount a no-op and stops the workspace and the progress page fetching the
  same course twice — and it is why `queryCache.clear()` on logout and on `auth:unauthorized`
  is correctness rather than hygiene.
- **It is a module singleton**, reset once in `setupTests.ts`. A provider would have to be
  added to every `render()` in the suite. It schedules no timers, so it cannot disturb a test
  running fake timers.
- `src/api/invalidations.ts` is the whole mutation → key table in one readable file.
- `CreditContext` is the reference consumer: its coalescing, trailing refresh, identity
  fencing and focus revalidation are all cache behaviour now, and its tests pass unchanged.
  `AuthContext` deliberately stays bespoke — identity is what `me()` returns, so there is no
  key to cache it under that would not contain a bearer token.

## Accessibility baseline

WCAG 2.1 AA. Semantic HTML, one `<h1>` per screen, no skipped heading levels, labels
associated by id, visible focus from `:focus-visible`, focus trapped and restored in every
dialog, accessible names on every icon-only control, `aria-live` for async status changes
(uploads, processing transitions, arriving answers), no colour-only status, and
`prefers-reduced-motion` respected through the motion tokens.

The `<h1>` rule holds on screens with no visual title slot too. The workspace and the course
progress page carry their identity in the course chip and the breadcrumb, so both name
themselves with a `visually-hidden` `<h1>`; without it every section heading on those screens
was an orphaned `<h2>`. Reach for the same utility rather than inventing a visible title on a
screen whose design does not have one.

`AppShell` owns the single `<main>` for every screen inside it, which is why a page within
the shell must not render its own. `AuthLayout` is a second shell rather than a page, sits
outside `AppShell` entirely, and therefore owns a `<main>` of its own — sign-in and
registration would otherwise have no main landmark at all.

Two things this baseline deliberately does not claim: WCAG 2.1 AA has no target-size
criterion (2.5.5 is AAA and 2.5.8 arrived in 2.2), and a screen at rest is allowed to have no
live region — errors render `<Alert live="alert">` and successes go through `ToastProvider`,
whose region is announced. Do not "fix" either against a standard this baseline has not
adopted.

## Adding a new screen

1. Route it in `src/App.tsx` inside `ProtectedRoute > AppShell`.
2. Create `src/features/<domain>/<Name>Page.tsx` + `<Name>Page.module.css`.
3. Render `<PageHeader>` with breadcrumbs, and `courseId` if course-scoped.
4. Build from `src/ui` primitives. Consume tokens; write no literal colour.
5. Call `useDocumentTitle()`.
6. Handle loading, empty, error and permission states deliberately.
7. Test behaviour — roles and user-visible text, not classes.
