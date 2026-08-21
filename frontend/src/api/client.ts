import type { BaseResponse } from './types';

const BASE_URL = '/api';

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

export class APIError extends Error {
  public code: string | null;

  constructor(
    public status: number,
    public data: unknown,
    headerCode: string | null = null,
  ) {
    const parsed = parseApiErrorBody(data);
    super(parsed.message);
    this.name = 'APIError';
    this.code = parsed.code ?? headerCode;
  }
}

export class MalformedResponseError extends Error {
  constructor(context: string) {
    super(`${context} returned no data.`);
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

  const url = `${BASE_URL}${endpoint}`;

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorData: unknown = null;
    try {
      errorData = await response.json();
    } catch {
      errorData = { detail: response.statusText };
    }

    if (response.status === 401) {
      localStorage.removeItem('token');
      window.dispatchEvent(new Event('auth:unauthorized'));
    }

    throw new APIError(
      response.status,
      errorData,
      response.headers?.get(ERROR_CODE_HEADER) ?? null,
    );
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
