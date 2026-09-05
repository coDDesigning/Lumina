import type { BaseResponse } from './types';

const DEFAULT_BASE_URL = '/api';

function resolveApiBaseUrl(value: string | undefined): string {
  if (value === undefined || !value.trim()) {
    return DEFAULT_BASE_URL;
  }

  if (
    Array.from(value).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint <= 0x1f || (codePoint >= 0x7f && codePoint <= 0x9f);
    })
  ) {
    throw new Error('VITE_API_BASE_URL must not include control characters.');
  }

  const baseUrl = value.trim();

  if (baseUrl.includes('\\')) {
    throw new Error('VITE_API_BASE_URL must not include backslashes.');
  }

  if (baseUrl.includes('?') || baseUrl.includes('#')) {
    throw new Error('VITE_API_BASE_URL must not include a query or fragment.');
  }

  if (baseUrl.startsWith('/')) {
    if (baseUrl.startsWith('//')) {
      throw new Error('VITE_API_BASE_URL must be root-relative or an absolute HTTP(S) URL.');
    }

    return baseUrl.replace(/\/+$/, '') || '/';
  }

  if (!/^https?:\/\//i.test(baseUrl)) {
    throw new Error('VITE_API_BASE_URL must be root-relative or an absolute HTTP(S) URL.');
  }

  let parsedBaseUrl: URL;
  try {
    parsedBaseUrl = new URL(baseUrl);
  } catch {
    throw new Error('VITE_API_BASE_URL must be a valid absolute HTTP(S) URL.');
  }

  if (
    parsedBaseUrl.username ||
    parsedBaseUrl.password ||
    /^https?:\/\/[^/]*@/i.test(baseUrl)
  ) {
    throw new Error('VITE_API_BASE_URL must not include userinfo.');
  }

  return parsedBaseUrl.href.replace(/\/+$/, '');
}

const BASE_URL = resolveApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

function buildApiUrl(endpoint: string): string {
  return `${BASE_URL.replace(/\/+$/, '')}/${endpoint.replace(/^\/+/, '')}`;
}

interface ParsedApiError {
  message: string;
  code: string | null;
}

function parseApiErrorBody(data: unknown): ParsedApiError {
  const fallback: ParsedApiError = { message: 'An API error occurred', code: null };

  if (typeof data !== 'object' || data === null) {
    return fallback;
  }

  const body = data as Record<string, unknown>;

  if (body.success === false && typeof body.message === 'string' && body.message) {
    const nested =
      typeof body.data === 'object' && body.data !== null
        ? (body.data as Record<string, unknown>)
        : null;
    const code = typeof nested?.code === 'string' ? nested.code : null;
    return { message: body.message, code };
  }

  const detail = body.detail;

  if (typeof detail === 'string') {
    return { message: detail, code: null };
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as Record<string, unknown> | null;
    if (typeof first === 'object' && first !== null && 'msg' in first) {
      const loc = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : '';
      const msg = String(first.msg);
      return { message: loc ? `${loc}: ${msg}` : msg, code: null };
    }
  }

  return fallback;
}

export const ERROR_CODE_HEADER = 'X-Error-Code';
const RETRY_AFTER_HEADER = 'Retry-After';
const PERSONAL_KEY_INVALID_ERROR_CODE = 'personal_key_invalid';
const ACCOUNT_BANNED_ERROR_CODE = 'account_banned';
const NETWORK_RETRY_METHODS = new Set(['GET', 'HEAD']);

export type SessionEndReason = 'unauthorized' | 'banned';

export interface SessionEndEventDetail {
  reason: SessionEndReason;
}

function parseRetryAfterSeconds(value: string | null): number | null {
  const normalized = value?.trim();
  if (!normalized || !/^\d+$/.test(normalized)) {
    return null;
  }

  const seconds = Number(normalized);
  return Number.isSafeInteger(seconds) ? seconds : null;
}

export class APIError extends Error {
  public code: string | null;
  public retryAfterSeconds: number | null;

  constructor(
    public status: number,
    public data: unknown,
    headerCode: string | null = null,
    retryAfter: string | null = null,
  ) {
    const parsed = parseApiErrorBody(data);
    super(parsed.message);
    this.name = 'APIError';
    this.code = parsed.code ?? headerCode;
    this.retryAfterSeconds = parseRetryAfterSeconds(retryAfter);
  }
}

export type MalformedResponseReason = 'missing_data' | 'invalid_data';

export class MalformedResponseError extends Error {
  constructor(
    context: string,
    public readonly reason: MalformedResponseReason = 'missing_data',
  ) {
    super(
      reason === 'missing_data'
        ? `${context} returned no data.`
        : `${context} returned invalid data.`,
    );
    this.name = 'MalformedResponseError';
  }
}

export function unwrapData<T>(response: BaseResponse<T>, context: string): T {
  if (response?.data == null) {
    throw new MalformedResponseError(context);
  }
  return response.data;
}

async function request<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem('token');
  const headers = new Headers(options.headers);

  if (!headers.has('Content-Type') && !(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json');
  }

  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const url = buildApiUrl(endpoint);
  const method = (options.method ?? 'GET').toUpperCase();
  const mayRetryNetworkError = NETWORK_RETRY_METHODS.has(method);

  const maxRetries = 2;
  let response: Response | undefined;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      response = await fetch(url, {
        ...options,
        headers,
      });

      if (
        response.status === 503 &&
        response.headers?.get(RETRY_AFTER_HEADER) === '1' &&
        attempt < maxRetries
      ) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }
      break;
    } catch (networkError) {
      const wasAborted =
        options.signal?.aborted === true ||
        (networkError instanceof Error && networkError.name === 'AbortError');
      if (!wasAborted && mayRetryNetworkError && attempt < maxRetries) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        continue;
      }
      throw networkError;
    }
  }

  if (!response) {
    throw new TypeError('Failed to fetch');
  }

  if (!response.ok) {
    let errorData: unknown = null;
    try {
      errorData = await response.json();
    } catch {
      errorData = { detail: response.statusText };
    }

    const error = new APIError(
      response.status,
      errorData,
      response.headers?.get(ERROR_CODE_HEADER) ?? null,
      response.headers?.get(RETRY_AFTER_HEADER) ?? null,
    );

    const isSessionEnded =
      token !== null &&
      localStorage.getItem('token') === token &&
      ((response.status === 401 && error.code !== PERSONAL_KEY_INVALID_ERROR_CODE) ||
        (response.status === 403 && error.code === ACCOUNT_BANNED_ERROR_CODE));

    if (isSessionEnded) {
      const reason: SessionEndReason =
        response.status === 403 && error.code === ACCOUNT_BANNED_ERROR_CODE
          ? 'banned'
          : 'unauthorized';
      localStorage.removeItem('token');
      window.dispatchEvent(
        new CustomEvent<SessionEndEventDetail>('auth:unauthorized', {
          detail: { reason },
        }),
      );
    }

    throw error;
  }

  const text = await response.text();
  if (!text) {
    return {} as T;
  }

  return JSON.parse(text) as T;
}

export const apiClient = {
  get: <T>(endpoint: string, options?: RequestInit) =>
    request<T>(endpoint, { ...options, method: 'GET' }),

  post: <T>(endpoint: string, data?: unknown, options?: RequestInit) =>
    request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: data === undefined ? undefined : JSON.stringify(data),
    }),

  put: <T>(endpoint: string, data?: unknown, options?: RequestInit) =>
    request<T>(endpoint, {
      ...options,
      method: 'PUT',
      body: data === undefined ? undefined : JSON.stringify(data),
    }),

  patch: <T>(endpoint: string, data?: unknown, options?: RequestInit) =>
    request<T>(endpoint, {
      ...options,
      method: 'PATCH',
      body: data === undefined ? undefined : JSON.stringify(data),
    }),

  delete: <T>(endpoint: string, options?: RequestInit) =>
    request<T>(endpoint, { ...options, method: 'DELETE' }),

  postForm: <T>(
    endpoint: string,
    formData: FormData | URLSearchParams,
    options?: RequestInit,
  ) => {
    const headers = new Headers(options?.headers);

    if (formData instanceof URLSearchParams) {
      headers.set('Content-Type', 'application/x-www-form-urlencoded');
    }

    return request<T>(endpoint, {
      ...options,
      method: 'POST',
      body: formData,
      headers,
    });
  },
};
