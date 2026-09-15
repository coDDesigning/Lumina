import { beforeEach, describe, expect, it, vi } from 'vitest';
import { clientErrorsAPI } from '@/api/clientErrors';
import { clientRouteTemplate, reportClientError } from './clientErrorReporter';

vi.mock('@/api/clientErrors', () => ({
  clientErrorsAPI: { report: vi.fn() },
}));

vi.mock('@/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api/client')>()),
  getLastApiRequestId: () => 'request-123',
}));

describe('clientErrorReporter', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(clientErrorsAPI.report).mockReset().mockResolvedValue({ accepted: true });
  });

  it('reports only sanitized metadata and deduplicates repeated failures', async () => {
    localStorage.setItem('token', 'private-token');
    window.history.pushState(
      {},
      '',
      '/courses/private-course/exam-mode/plans/private-plan/topics/private-topic?secret=yes',
    );
    const error = new Error('private model output');
    error.name = 'ChunkLoadError';

    await reportClientError(error);
    await reportClientError(error);

    expect(clientErrorsAPI.report).toHaveBeenCalledTimes(1);
    const report = vi.mocked(clientErrorsAPI.report).mock.calls[0][0];
    expect(report).toEqual({
      route_template: '/courses/{course_id}/exam-mode/plans/{plan_id}/topics/{topic_key}',
      application_version: 'development',
      error_class: 'ChunkLoadError',
      api_request_id: 'request-123',
      fingerprint: expect.stringMatching(/^[a-f0-9]{16,64}$/),
    });
    expect(JSON.stringify(report)).not.toContain('private');
  });

  it('does not report errors without an authenticated session', async () => {
    await reportClientError(new Error('not signed in'));

    expect(clientErrorsAPI.report).not.toHaveBeenCalled();
  });

  it('maps unknown routes to a fixed template', () => {
    expect(clientRouteTemplate('/student-supplied/path')).toBe('/unmatched');
  });
});
