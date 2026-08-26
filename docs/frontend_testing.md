# Frontend Testing

Lumina's React frontend is tested at two levels. [Vitest](https://vitest.dev/) with
[React Testing Library](https://testing-library.com/docs/react-testing-library/intro/) and `jsdom`
covers behaviour; [Playwright](https://playwright.dev/) drives a real browser for the things jsdom
cannot see. `docs/frontend_system.md` owns the product rules these tests enforce — this file owns
how to run and write them.

## Design Principles

1. **Zero external dependencies.** No test makes a real network request or needs a backend, a
   database, or a model provider. Component tests mock the API modules; the browser suite runs
   against a stub server in this repository.
2. **Deterministic and fast.** Mocked API clients, and fake timers wherever a component polls or
   counts down.
3. **Behaviour-focused.** Tests exercise what a person can observe — roles, names, visible text,
   redirects — never classes, test ids, or internal state. Rule 7 of "Adding a new screen" in
   `docs/frontend_system.md` is the same rule.
4. **A regression test is checked against its bug.** Revert the fix and confirm the new test fails
   before you keep it. A test that passes either way protects nothing.

## Running Tests

From `frontend/`:

```bash
npm test                                   # the whole component suite, once (what CI runs)
npx vitest                                 # watch mode
npx vitest run src/context/AuthContext.test.tsx
npx vitest run -t "upload"                 # everything matching a name
```

The browser suite needs a built frontend and nothing else:

```bash
npx playwright install chromium            # once per machine
npm run build
npm run test:e2e                           # or: npx playwright test --ui
```

`playwright.config.ts` starts `vite preview` on `:4173` and reuses it if it is already running.
The API is answered inside the browser by `e2e/api.ts` through `page.route`, so there is no
second process, no deployed stack, and no dependency on anything outside `frontend/` — the suite
runs offline and on a clean checkout. A request the fixture does not recognise is answered `404`
with the path in the body rather than reaching the network, so a screen that starts calling
something new fails loudly instead of hanging.

## Structure and Conventions

### Setup and environment

- `frontend/vite.config.ts` configures `jsdom`, `setupFiles: ['./src/setupTests.ts']`, and
  `include: ['src/**/*.test.{ts,tsx}']`. Browser specs are `e2e/*.spec.ts`, so the two runners
  never collect each other's files.
- `frontend/src/setupTests.ts` loads `@testing-library/jest-dom/vitest`, runs `cleanup()` and
  `resetQueryCache()` after each test, polyfills `matchMedia` and `scrollTo`, and **promotes React
  warnings to failures** — duplicate keys, invalid DOM nesting, missing `act`, bad ARIA. A green
  run therefore means the render was clean.
- The query cache is a module singleton reset once in `setupTests.ts`. No test needs a provider.
- `Dialog` renders through `createPortal` to `document.body`, so scope queries to `screen`, never
  to the container `render()` returns.

### Mocking API calls

Mock the API module, not `fetch`:

```ts
vi.mock('@/api/quiz', () => ({
  quizAPI: { generate: vi.fn(), submitAttempt: vi.fn() },
}))
const mockGenerate = vi.mocked(quizAPI.generate)
```

Shared fixtures live in [`src/test/mocks/api.ts`](../frontend/src/test/mocks/api.ts) —
`createMockUser`, `createMockCourse`, `createMockDocument`, `createMockDocumentStatus`,
`createMockUploadResponse`, and `MockErrors` for 401/404/409/413/415/422. Most files build their
own local fixture with `Partial<T>` overrides, which is fine; prefer the shared factory when the
shape is one of those five.

Components that read a balance need `CreditProvider` and a mocked `userAPI.getCredits`; anything
using `Link` or `useNavigate` needs a `MemoryRouter`.

### Testing failures

Branch on the `X-Error-Code` header, not the HTTP status — `describeGenerationError` keys on the
code, and `no_relevant_material` and `material_not_indexed` both arrive as 409. Build the error
with the third `APIError` argument:

```ts
mockGenerate.mockRejectedValue(new APIError(503, { detail: 'down' }, 'provider_unavailable'))
```

A dropped connection is a `TypeError`, which the client reports as "You are offline". See
`src/features/study/quiz/QuizModal.failures.test.tsx` for one case per mapped code.

### Testing timers

```ts
beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
afterEach(() => vi.useRealTimers())

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms) })
}
```

`shouldAdvanceTime` keeps `findBy*` able to poll. React's `act` boundary lets a chained
`setTimeout` fire at most once per `advance()`, so a countdown needs one call per second rather
than one long jump — `tick()` in `QuizModal.timer.test.tsx` does exactly that. Assert `deltas`
rather than exact clock values, and confirm `vi.getTimerCount() === 0` after unmount.

That file also holds the shape of the timer regression worth knowing: the countdown effect
depended on a callback keyed on the answers, so every keystroke tore down the pending tick and the
clock silently stopped. The test that catches it interacts *faster than once per second*; a test
that advances a full second between keystrokes passes either way.

## Guards

Ten guards police whole classes of mistake by reading files off disk rather than rendering
anything. They are cheap and they have all caught something real.

| Guard | What it refuses |
|---|---|
| `setupTests.ts` | A React warning during any test |
| `styles/tokenUsage.test.ts` | A `var(--x)` that nothing defines |
| `styles/rawValues.test.ts` | A colour literal outside `tokens.css`, and a theme branch in a component |
| `styles/contrast.test.ts` | A token pair below its WCAG ratio |
| `styles/fadedText.test.ts` | An `opacity` on text that also carries a colour, which blends a passing token below AA where only a browser can see it |
| `styles/darkTokens.test.ts` | The two dark blocks drifting apart |
| `styles/breakpoints.test.ts` | A media query away from the four agreed widths |
| `features/honesty.test.ts` | A success notice with no request behind it, a nullable metric coalesced to `0`, and a hardcoded stand-in fixture |
| `app/navigationHonesty.test.tsx` | An unbuilt capability presented as a product area |
| `lib/query/keyDiscipline.test.ts` | A query key built anywhere but `api/queryKeys.ts` |

`rawValues.test.ts` and `honesty.test.ts` both take exemptions the same way: one documented list,
each entry carrying a reason, and a stale entry fails the run so an exemption cannot outlive what
it excused.

## The browser suite

jsdom has no layout engine, so no component test can catch a contrast, overflow, or focus defect.
`frontend/e2e/` covers what only a browser can:

| Spec | What it covers |
|---|---|
| `quiz.spec.ts` | The core learning interaction end to end — configure, answer one of each supported type, hand in, read the review, and confirm an ungraded written answer reads as unscored rather than wrong. Plus the live countdown and 360px layout. |
| `auth.spec.ts` | Sign-in redirect, sign-in, landmarks, and client-side registration validation. |
| `a11y.spec.ts` | axe over eight screens, failing on any serious or critical violation; one heading and one `<main>` per screen; no sideways scroll at 360px. |

Two conventions matter there. Click what a person clicks: a radio input is visually hidden beneath
its letter badge, so target the label text rather than the input. And the accessibility spec
carries exactly one documented allowance — `--text-subtle` (`#7e8899`) clears 3.58:1, and several
screens use it at 12–13px where AA asks 4.5:1. `contrast.test.ts` holds that token to 3:1 on
purpose, so darkening it is a palette decision rather than a test fix. The allowance is keyed on
the colour, not the route, and a separate test asserts it is still the only failing pair — any new
contrast failure breaks the build.

CI runs the browser suite as **Frontend end-to-end**, currently `continue-on-error: true` while it
proves itself. Job names are referenced by dormant branch rulesets, so flag the ruleset when that
changes.

## What is not covered

- **Load and performance.** No tooling, and no acceptance criterion asks for one yet.
- **Visual regression.** Nothing captures or compares screenshots. Local scratch tooling may
  exist under `.user/`, but nothing committed depends on it.
- **Firefox and WebKit.** The Playwright config runs Chromium only.
