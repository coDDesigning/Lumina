import { APIError, getLastApiRequestId } from '@/api/client';
import { clientErrorsAPI } from '@/api/clientErrors';
import type { ClientErrorReport } from '@/api/types';

const ERROR_CLASS_PATTERN = /^[A-Za-z_$][A-Za-z0-9_.$-]{0,99}$/;
const ERROR_CLASSES = new Set([
  'AbortError',
  'AggregateError',
  'APIError',
  'ChunkLoadError',
  'DOMException',
  'Error',
  'EvalError',
  'MalformedResponseError',
  'NetworkError',
  'NotAllowedError',
  'QuotaExceededError',
  'RangeError',
  'ReferenceError',
  'RuntimeError',
  'SecurityError',
  'SyntaxError',
  'TypeError',
  'URIError',
]);
const APPLICATION_VERSION_PATTERN = /^[A-Za-z0-9._-]{1,80}$/;
const configuredVersion = import.meta.env.VITE_APP_VERSION ?? '';
const APPLICATION_VERSION = APPLICATION_VERSION_PATTERN.test(configuredVersion)
  ? configuredVersion
  : 'development';
const MAX_PENDING_REPORTS = 20;
const RECENT_REPORT_MS = 60_000;

interface PendingReport {
  payload: ClientErrorReport;
  token: string;
}

const pending = new Map<string, PendingReport>();
const recent = new Map<string, number>();
let sending = false;
let installed = false;

const STATIC_ROUTES = new Set([
  '/',
  '/account',
  '/account/ai',
  '/account/api-keys',
  '/account/appearance',
  '/account/background',
  '/account/security',
  '/activity',
  '/admin',
  '/admin/logs',
  '/dashboard',
  '/forgot-password',
  '/login',
  '/register',
  '/reset-password',
  '/verify-email',
]);

const DYNAMIC_ROUTES: Array<[RegExp, string]> = [
  [
    /^\/courses\/[^/]+\/exam-mode\/plans\/[^/]+\/compare\/[^/]+$/,
    '/courses/{course_id}/exam-mode/plans/{plan_id}/compare/{other_plan_id}',
  ],
  [
    /^\/courses\/[^/]+\/exam-mode\/plans\/[^/]+\/topics\/[^/]+$/,
    '/courses/{course_id}/exam-mode/plans/{plan_id}/topics/{topic_key}',
  ],
  [
    /^\/courses\/[^/]+\/practice\/[^/]+\/sessions\/[^/]+$/,
    '/courses/{course_id}/practice/{quiz_id}/sessions/{session_id}',
  ],
  [
    /^\/courses\/[^/]+\/practice\/[^/]+\/attempts\/[^/]+$/,
    '/courses/{course_id}/practice/{quiz_id}/attempts/{attempt_id}',
  ],
  [
    /^\/courses\/[^/]+\/exam-mode\/plans\/[^/]+$/,
    '/courses/{course_id}/exam-mode/plans/{plan_id}',
  ],
  [
    /^\/courses\/[^/]+\/practice\/[^/]+$/,
    '/courses/{course_id}/practice/{quiz_id}',
  ],
  [/^\/courses\/[^/]+\/guides\/[^/]+$/, '/courses/{course_id}/guides/{output_id}'],
  [/^\/courses\/[^/]+\/exam-mode$/, '/courses/{course_id}/exam-mode'],
  [/^\/courses\/[^/]+\/reverse-quiz$/, '/courses/{course_id}/reverse-quiz'],
  [/^\/courses\/[^/]+\/progress$/, '/courses/{course_id}/progress'],
  [/^\/courses\/[^/]+\/settings$/, '/courses/{course_id}/settings'],
  [/^\/courses\/[^/]+$/, '/courses/{course_id}'],
  [/^\/workspaces\/[^/]+(?:\/.*)?$/, '/workspaces/{course_id}/{legacy_path}'],
];

export function clientRouteTemplate(pathname: string): string {
  if (STATIC_ROUTES.has(pathname)) return pathname;
  return DYNAMIC_ROUTES.find(([pattern]) => pattern.test(pathname))?.[1] ?? '/unmatched';
}

function errorClass(error: unknown): string {
  const candidate = error instanceof Error ? error.name : '';
  return ERROR_CLASS_PATTERN.test(candidate) && ERROR_CLASSES.has(candidate) ? candidate : 'Error';
}

function fallbackFingerprint(value: string): string {
  let hash = 2_166_136_261;
  for (const character of value) {
    hash ^= character.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16_777_619);
  }
  const part = (hash >>> 0).toString(16).padStart(8, '0');
  return part.repeat(2);
}

async function fingerprint(value: string): Promise<string> {
  if (!globalThis.crypto?.subtle) return fallbackFingerprint(value);
  try {
    const digest = await globalThis.crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(value),
    );
    return Array.from(new Uint8Array(digest), (byte) =>
      byte.toString(16).padStart(2, '0'),
    ).join('');
  } catch {
    return fallbackFingerprint(value);
  }
}

async function flush(): Promise<void> {
  if (sending) return;
  sending = true;
  try {
    for (const [key, report] of pending) {
      if (localStorage.getItem('token') !== report.token) {
        pending.delete(key);
        continue;
      }
      try {
        await clientErrorsAPI.report(report.payload);
      } catch {
        // Diagnostics are best effort and must never obscure the original failure.
      }
      pending.delete(key);
    }
  } finally {
    sending = false;
    if (pending.size > 0) void flush();
  }
}

export async function reportClientError(error: unknown): Promise<void> {
  const token = localStorage.getItem('token');
  if (!token) return;
  const routeTemplate = clientRouteTemplate(window.location.pathname);
  const safeErrorClass = errorClass(error);
  const key = await fingerprint(`${routeTemplate}\u001f${safeErrorClass}`);
  if (localStorage.getItem('token') !== token) return;

  const now = Date.now();
  for (const [recentKey, reportedAt] of recent) {
    if (now - reportedAt >= RECENT_REPORT_MS) recent.delete(recentKey);
  }
  if (recent.has(key) || pending.has(key)) return;
  recent.set(key, now);
  if (pending.size >= MAX_PENDING_REPORTS) return;
  pending.set(key, {
    token,
    payload: {
      route_template: routeTemplate,
      application_version: APPLICATION_VERSION,
      error_class: safeErrorClass,
      api_request_id: error instanceof APIError ? error.requestId : getLastApiRequestId(),
      fingerprint: key,
    },
  });
  await flush();
}

export function installClientErrorReporting(): void {
  if (installed) return;
  installed = true;
  window.addEventListener('error', (event) => {
    void reportClientError(event.error);
  });
  window.addEventListener('unhandledrejection', (event) => {
    void reportClientError(event.reason);
  });
}
