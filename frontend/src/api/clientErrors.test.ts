import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, getLastApiRequestId } from './client';
import { clientErrorsAPI } from './clientErrors';
import type { BaseResponse } from './types';

describe('clientErrorsAPI', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('posts sanitized browser diagnostics to the authenticated endpoint', async () => {
    localStorage.setItem('token', 'test-token');
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { ok: true } }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': 'originating-request',
          },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { accepted: true } }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
            'X-Request-ID': 'diagnostic-request',
          },
        }),
      );
    const payload = {
      route_template: '/courses/{course_id}',
      application_version: 'test-version',
      error_class: 'TypeError',
      api_request_id: 'request-123',
      fingerprint: '0123456789abcdef',
    };

    await apiClient.get<BaseResponse<{ ok: boolean }>>('/probe');
    await expect(clientErrorsAPI.report(payload)).resolves.toEqual({ accepted: true });
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/client-errors',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify(payload),
        headers: expect.any(Headers),
      }),
    );
    const headers = fetchMock.mock.calls[1][1]?.headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer test-token');
    expect(getLastApiRequestId()).toBe('originating-request');
  });
});
