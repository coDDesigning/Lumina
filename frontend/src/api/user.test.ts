import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from './client';
import { userAPI } from './user';

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

describe('userAPI.changePassword', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('succeeds on the empty body the endpoint answers with', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'Password changed', data: null }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await expect(userAPI.changePassword('old-one', 'new-one')).resolves.toBeUndefined();

    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe('/api/users/me/password');
    expect(init?.method).toBe('PUT');
    expect(JSON.parse(init?.body as string)).toEqual({
      current_password: 'old-one',
      new_password: 'new-one',
    });
  });

  it('still reports a rejected password change', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ detail: 'Current password is incorrect.' }, 400),
      ),
    );

    await expect(userAPI.changePassword('wrong', 'new-one')).rejects.toBeInstanceOf(
      APIError,
    );
  });
});
