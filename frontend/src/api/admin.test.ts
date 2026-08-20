import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { adminAPI } from './admin';
import { MalformedResponseError } from './client';
import type { User } from './types';

const MOCK_USERS: User[] = [
  {
    id: 1,
    name: 'Admin User',
    email: 'admin@example.com',
    role: 'admin',
    is_banned: false,
    credits: null,
    preferred_model: 'gemini:gemini-3.6-flash',
  },
  {
    id: 2,
    name: 'Standard User',
    email: 'user@example.com',
    role: 'user',
    is_banned: false,
    credits: 50,
    preferred_model: 'gemini:gemini-3.6-flash',
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: async () => JSON.stringify(body),
    json: async () => body,
  } as Response;
}

describe('adminAPI', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'test-admin-token');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('lists users successfully', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: MOCK_USERS }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const users = await adminAPI.listUsers();
    expect(users).toHaveLength(2);
    expect(users[0].email).toBe('admin@example.com');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users',
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });

  it('bans user successfully', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'ok',
        data: { ...MOCK_USERS[1], is_banned: true },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const updated = await adminAPI.banUser('user@example.com', true);
    expect(updated.is_banned).toBe(true);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users/user%40example.com/ban?is_banned=true',
      expect.objectContaining({ method: 'PUT' }),
    );
  });

  it('changes user role successfully', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'ok',
        data: { ...MOCK_USERS[1], role: 'admin' },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const updated = await adminAPI.changeUserRole('user@example.com', 'admin');
    expect(updated.role).toBe('admin');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users/user%40example.com/role?role=admin',
      expect.objectContaining({ method: 'PUT' }),
    );
  });

  it('rejects when response is malformed', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn<typeof fetch>(async () =>
        jsonResponse({ success: true, message: 'ok', data: null }),
      ),
    );

    await expect(adminAPI.listUsers()).rejects.toBeInstanceOf(
      MalformedResponseError,
    );
  });
});
