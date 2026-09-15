import { afterEach, describe, expect, it, vi } from 'vitest';
import { APIError } from './client';
import { authAPI } from './auth';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    headers: { get: () => null },
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as unknown as Response;
}

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(body, status));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function jsonBody(fetchMock: ReturnType<typeof stubFetch>): unknown {
  return JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
}

afterEach(() => {
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe('authAPI.register', () => {
  it('sends the policy acknowledgement the sign-up form collected', async () => {
    const fetchMock = stubFetch({ email: 'ada@example.com', verification_required: true });

    await authAPI.register('Ada', 'ada@example.com', 'correct horse', true);

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/auth/register');
    expect(init?.method).toBe('POST');
    expect(jsonBody(fetchMock)).toEqual({
      name: 'Ada',
      email: 'ada@example.com',
      password: 'correct horse',
      policies_acknowledged: true,
    });
  });

  it('sends an explicit false when no acknowledgement is given', async () => {
    const fetchMock = stubFetch({ email: 'ada@example.com', verification_required: true });

    await authAPI.register('Ada', 'ada@example.com', 'correct horse');

    expect(jsonBody(fetchMock)).toMatchObject({ policies_acknowledged: false });
  });

  it('reports a rejected registration', async () => {
    stubFetch({ detail: 'Email already registered.' }, 400);

    await expect(
      authAPI.register('Ada', 'ada@example.com', 'correct horse', true),
    ).rejects.toBeInstanceOf(APIError);
  });
});

describe('authAPI.login', () => {
  it('posts OAuth2 password form fields rather than JSON', async () => {
    const fetchMock = stubFetch({ access_token: 'token', token_type: 'bearer' });

    await expect(authAPI.login('ada@example.com', 'correct horse')).resolves.toEqual({
      access_token: 'token',
      token_type: 'bearer',
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/auth/login');
    expect(init?.method).toBe('POST');
    expect(new Headers(init?.headers).get('Content-Type')).toBe(
      'application/x-www-form-urlencoded',
    );
    const form = init?.body as URLSearchParams;
    expect(form.get('username')).toBe('ada@example.com');
    expect(form.get('password')).toBe('correct horse');
  });
});

describe('authAPI email verification', () => {
  it('redeems a verification token', async () => {
    const fetchMock = stubFetch({ message: 'verified' });

    await authAPI.verifyEmail('link-token');

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/auth/verify-email');
    expect(jsonBody(fetchMock)).toEqual({ token: 'link-token' });
  });

  it('asks for a fresh link by address', async () => {
    const fetchMock = stubFetch({ message: 'sent' });

    await authAPI.resendVerification('ada@example.com');

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/auth/verify-email/resend');
    expect(jsonBody(fetchMock)).toEqual({ email: 'ada@example.com' });
  });
});

describe('authAPI password reset', () => {
  it('requests a reset link by address', async () => {
    const fetchMock = stubFetch({ message: 'sent' });

    await authAPI.requestPasswordReset('ada@example.com');

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/auth/reset-password');
    expect(jsonBody(fetchMock)).toEqual({ email: 'ada@example.com' });
  });

  it('confirms a reset with the token and new password', async () => {
    const fetchMock = stubFetch({ message: 'changed' });

    await authAPI.confirmPasswordReset('reset-token', 'new password');

    expect(String(fetchMock.mock.calls[0][0])).toBe('/api/auth/reset-password/confirm');
    expect(jsonBody(fetchMock)).toEqual({ token: 'reset-token', new_password: 'new password' });
  });
});

describe('authAPI session reads', () => {
  it('reads the password policy with GET', async () => {
    const fetchMock = stubFetch({ min_length: 8, max_bytes: 72 });

    await expect(authAPI.getPasswordPolicy()).resolves.toEqual({ min_length: 8, max_bytes: 72 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/auth/password-policy');
    expect(init?.method).toBe('GET');
  });

  it('reads the signed-in user with the stored bearer token', async () => {
    localStorage.setItem('token', 'test-token');
    const fetchMock = stubFetch({ id: 1, email: 'ada@example.com' });

    await authAPI.me();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/auth/me');
    expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer test-token');
  });

  it('logs out with a bodiless POST', async () => {
    localStorage.setItem('token', 'test-token');
    const fetchMock = stubFetch({ message: 'logged out' });

    await expect(authAPI.logout()).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/auth/logout');
    expect(init?.method).toBe('POST');
    expect(init?.body).toBeUndefined();
  });
});
