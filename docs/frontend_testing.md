# Frontend Testing Foundation

Lumina's React frontend uses [Vitest](https://vitest.dev/) with [React Testing Library](https://testing-library.com/docs/react-testing-library/intro/) and `jsdom` for automated browser-isolated unit, component, and integration testing.

## Design Principles

1. **Zero External/Backend Dependencies**: Tests never make real network requests or require a running backend or database.
2. **Deterministic & Fast**: Tests run in-memory using mocked API client abstractions and fake timers for asynchronous polling.
3. **Behavior-Focused**: Tests exercise user-observable behavior (e.g. form inputs, error notices, button interactions, redirects) rather than implementation details.

## Running Tests

From the `frontend/` directory:

```bash
# Run all frontend tests once (standard non-interactive CI command)
npm test

# Run tests in watch mode during development
npx vitest

# Run a specific test file
npx vitest run src/context/AuthContext.test.tsx

# Run tests matching a specific pattern
npx vitest run -t "upload"
```

## Structure & Conventions

### Setup and Environment

- `frontend/vite.config.ts`: Configures `jsdom` environment and specifies `setupFiles: ['./src/setupTests.ts']`.
- `frontend/src/setupTests.ts`: Automatically imports `@testing-library/jest-dom/vitest` matchers (such as `toBeInTheDocument()`, `toHaveTextContent()`, `toBeDisabled()`), applies `afterEach` test cleanup, and provides DOM polyfills (e.g., `window.matchMedia`, `window.scrollTo`).

### Mocking API Calls

Reusable mock factories and error builders live in [`src/test/mocks/api.ts`](../frontend/src/test/mocks/api.ts):

- `createMockUser()`: Returns an authenticated user model.
- `createMockCourse()`: Returns a course model.
- `createMockDocument()`: Returns a document metadata record.
- `createMockDocumentStatus()`: Returns document status and processing job state.
- `createMockUploadResponse()`: Returns an upload response.
- `MockErrors`: Helper factories to instantiate `APIError` instances for `401`, `404`, `409`, `413`, `415`, and `422`.

### Testing Polling & Timers

Hooks and components with status polling (e.g. [`useCourseDocuments`](../frontend/src/hooks/useCourseDocuments.ts)) use Vitest fake timers:

```typescript
beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
```

This pattern ensures that polling intervals, retry backoffs, and cleanup on unmount (`vi.getTimerCount() === 0`) are verified deterministically without delays.

## Test Suites Overview

| Suite | Focus Areas |
|---|---|
| [`src/api/client.test.ts`](../frontend/src/api/client.test.ts) | BaseResponse envelope unwrapping, `MalformedResponseError`, `APIError` parsing (Pydantic 422, curated error codes), `apiClient` HTTP methods, 401 `auth:unauthorized` dispatch. |
| [`src/api/errors.test.ts`](../frontend/src/api/errors.test.ts) | Error classification, retryability flags, `describeUploadError` status code mappings (409, 413, 415, 422). |
| [`src/context/AuthContext.test.tsx`](../frontend/src/context/AuthContext.test.tsx) | AuthProvider initialization, token reading on mount, token expiration cleanup, login/logout transitions, `auth:unauthorized` event handling. |
| [`src/components/ProtectedRoute.test.tsx`](../frontend/src/components/ProtectedRoute.test.tsx) | Loading spinner during auth check, unauthenticated redirect to `/login`, authenticated `<Outlet />` rendering. |
| [`src/pages/WorkspacesPage.test.tsx`](../frontend/src/pages/WorkspacesPage.test.tsx) | Course list rendering, search query filtering, empty state handling, workspace creation modal form submission and navigation. |
| [`src/pages/EditPage.test.tsx`](../frontend/src/pages/EditPage.test.tsx) | Course pre-population, form field editing, save submission, form reset. |
| [`src/App.upload.test.tsx`](../frontend/src/App.upload.test.tsx) | Document upload success, duplicate notices, and error alerts for HTTP 409 (conflict), 413 (payload too large), 415 (unsupported media type), and 422 (validation error). |
| [`src/hooks/useCourseDocuments.test.tsx`](../frontend/src/hooks/useCourseDocuments.test.tsx) | Polling lifecycle, progression to terminal `ready` / `failed` status, timer cleanup on unmount, course change cancellation, retry trigger. |
