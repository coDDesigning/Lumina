import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { adminAPI } from './admin';
import { MalformedResponseError } from './client';
import type { CreditTransaction, User } from './types';

const MOCK_USERS: User[] = [
  {
    id: 1,
    name: 'Admin User',
    email: 'admin@example.com',
    role: 'admin',
    is_banned: false,
    is_email_verified: true,
    credits: null,
    preferred_model: 'gemini:gemini-3.6-flash',
    education_level: 'unspecified',
  },
  {
    id: 2,
    name: 'Standard User',
    email: 'user@example.com',
    role: 'user',
    is_banned: false,
    is_email_verified: true,
    credits: 50,
    preferred_model: 'gemini:gemini-3.6-flash',
    education_level: 'unspecified',
  },
];

const MOCK_TRANSACTION: CreditTransaction = {
  id: 9,
  delta: 20,
  balance_after: 25,
  reason: 'admin_grant',
  actor_type: 'admin',
  actor_user_id: 1,
  actor_label: 'admin@example.com',
  source_type: null,
  source_id: null,
  refunds_transaction_id: null,
  grant_period: null,
  note: 'Support adjustment',
  created_at: '2026-08-20T10:00:00Z',
};

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

  it('reads the UTC provider cost report', async () => {
    const report = {
      timezone: 'UTC',
      start_date: '2026-07-26',
      end_date: '2026-08-24',
      totals: {
        successful_generations: 1,
        prompt_tokens: 100,
        completion_tokens: 200,
        estimated_cost_usd: 0.001,
        unpriced_generations: 0,
      },
      daily: [],
    } as const;
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: report }),
    );
    vi.stubGlobal('fetch', fetchMock);

    expect(await adminAPI.getAiCostReport()).toEqual(report);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/ai-costs?days=30',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it('forwards repeated filters to the operational log endpoint', async () => {
    const list = {
      records: [],
      next_cursor: null,
      query_window: {
        start: '2026-09-08T08:00:00Z',
        end: '2026-09-08T09:00:00Z',
        timezone: 'UTC',
        maximum_days: 30,
      },
      last_updated_at: '2026-09-08T09:00:00Z',
      source_health: [],
      partial: false,
      limited: false,
      omitted_records: 0,
    };
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: list }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const params = new URLSearchParams();
    params.append('source', 'operational');
    params.append('source', 'ai_telemetry');
    params.set('level', 'ERROR');

    await expect(adminAPI.listLogs(params)).resolves.toEqual(list);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/logs?source=operational&source=ai_telemetry&level=ERROR',
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it('reads explicit export truncation metadata', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response('{"type":"export_metadata"}\n', {
        status: 200,
        headers: { 'X-Export-Truncated': 'true' },
      }),
    );

    const result = await adminAPI.exportLogs(new URLSearchParams({ level: 'ERROR' }), 'jsonl');

    expect(result.truncated).toBe(true);
    expect(result.blob.size).toBeGreaterThan(0);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/logs/export?level=ERROR&format=jsonl',
      expect.objectContaining({ method: 'GET', headers: expect.any(Headers) }),
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

  it('changes credits and returns the new balance with its ledger row', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'ok',
        data: {
          user: { ...MOCK_USERS[1], credits: 25 },
          transaction: MOCK_TRANSACTION,
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await adminAPI.changeCredits(
      'user@example.com',
      20,
      'admin_grant',
      'Support adjustment',
    );

    expect(result.user.credits).toBe(25);
    expect(result.transaction.reason).toBe('admin_grant');
    expect(result.transaction.actor_label).toBe('admin@example.com');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users/user%40example.com/credits',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          delta: 20,
          reason: 'admin_grant',
          note: 'Support adjustment',
        }),
      }),
    );
  });

  it('sends a null note when none was supplied', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({
        success: true,
        message: 'ok',
        data: {
          user: { ...MOCK_USERS[1], credits: 45 },
          transaction: { ...MOCK_TRANSACTION, delta: -5, reason: 'admin_adjustment' },
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const result = await adminAPI.changeCredits(
      'user@example.com',
      -5,
      'admin_adjustment',
    );

    expect(result.transaction.delta).toBe(-5);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users/user%40example.com/credits',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          delta: -5,
          reason: 'admin_adjustment',
          note: null,
        }),
      }),
    );
  });

  it('reads another user credit history', async () => {
    const fetchMock = vi.fn<typeof fetch>(async () =>
      jsonResponse({ success: true, message: 'ok', data: [MOCK_TRANSACTION] }),
    );
    vi.stubGlobal('fetch', fetchMock);

    const history = await adminAPI.listUserCreditTransactions('user@example.com');

    expect(history).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/users/user%40example.com/credit-transactions?limit=20',
      expect.objectContaining({ headers: expect.any(Headers) }),
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
