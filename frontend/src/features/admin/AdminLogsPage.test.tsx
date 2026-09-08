import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { adminAPI } from '@/api/admin';
import type {
  AdminLogEventDetail,
  AdminLogList,
  AdminLogRecord,
  AdminLogSummary,
  AdminLogTrace,
} from '@/api/types';
import AdminLogsPage from './AdminLogsPage';

vi.mock('@/api/admin', () => ({
  adminAPI: {
    listLogs: vi.fn(),
    summarizeLogs: vi.fn(),
    getLogEvent: vi.fn(),
    traceLogEvent: vi.fn(),
    exportLogs: vi.fn(),
  },
}));

const START = '2026-09-08T08:00:00.000Z';
const END = '2026-09-08T09:00:00.000Z';
const RECORD: AdminLogRecord = {
  id: 'operational:event-42',
  source: 'operational',
  timestamp: '2026-09-08T08:45:00.000Z',
  level: 'ERROR',
  service: 'api',
  environment: 'test',
  logger: 'main',
  event: 'request.failed',
  description: 'The request failed during persistence.',
  error_code: 'database_unavailable',
  error_category: 'database',
  exception_type: 'OperationalError',
  exception_chain: ['OperationalError'],
  source_location: 'main.py:240',
  error_signature: 'v1:request-failed',
  http_method: 'POST',
  http_path: '/api/courses/{course_id}/quiz',
  http_status: 500,
  duration_ms: 2_400,
  request_id: 'request-42',
  related_request_id: null,
  operation_id: 'api:operation-42',
  parent_operation_id: null,
  job_id: null,
  job_type: null,
  job_status: null,
  attempt_number: null,
  stage: 'persistence',
  failed_stage: 'persistence',
  user_id: 7,
  course_id: 9,
  document_id: null,
  generation_type: 'quiz',
  provider: 'gemini',
  model: 'gemini-2.5-flash',
  success: false,
  prompt_tokens: 100,
  completion_tokens: 50,
  total_tokens: 150,
  estimated_cost_usd: 0.002,
  pricing_version: '2026-09-01',
  runbook: null,
  details: { retryable: true },
};

const SOURCE_HEALTH = [
  {
    source: 'operational' as const,
    status: 'available' as const,
    available_from: START,
    available_to: END,
    last_successful_fetch_at: END,
    ingestion_delay_seconds: 2,
    malformed_records: 0,
    dropped_records: 0,
    limited: false,
    detail: null,
    supported_filters: ['service', 'error_code'],
    collected_levels: ['INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const,
  },
];

const LIST: AdminLogList = {
  records: [RECORD],
  next_cursor: null,
  query_window: { start: START, end: END, timezone: 'UTC', maximum_days: 30 },
  last_updated_at: END,
  source_health: SOURCE_HEALTH.map((item) => ({
    ...item,
    collected_levels: [...item.collected_levels],
  })),
  partial: false,
  limited: false,
  omitted_records: 0,
};

const SUMMARY: AdminLogSummary = {
  counts: { events: 12, warnings: 2, errors: 1, distinct_failed_operations: 1 },
  error_groups: [
    {
      signature: 'v1:request-failed',
      service: 'api',
      event: 'request.failed',
      error_code: 'database_unavailable',
      exception_type: 'OperationalError',
      source_location: 'main.py:240',
      first_occurrence: RECORD.timestamp,
      last_occurrence: RECORD.timestamp,
      event_count: 1,
      distinct_operations: 1,
    },
  ],
  distribution: [{ start: START, events: 12, warnings: 2, errors: 1 }],
  query_window: LIST.query_window,
  last_updated_at: END,
  source_health: LIST.source_health,
  partial: false,
  limited: false,
};

const DETAIL: AdminLogEventDetail = { record: RECORD, related_filter: {} };
const TRACE: AdminLogTrace = {
  anchor_id: RECORD.id,
  operation_id: RECORD.operation_id,
  records: [
    { ...RECORD, id: 'operational:event-41', event: 'request.started', level: 'INFO' },
    RECORD,
  ],
  correlation_status: 'correlated',
  message: null,
  source_health: LIST.source_health,
  partial: false,
};

const mocked = vi.mocked(adminAPI);

function renderPage() {
  return render(
    <MemoryRouter initialEntries={[`/admin/logs?start=${START}&end=${END}`]}>
      <AdminLogsPage />
    </MemoryRouter>,
  );
}

describe('AdminLogsPage', () => {
  beforeEach(() => {
    mocked.listLogs.mockResolvedValue(LIST);
    mocked.summarizeLogs.mockResolvedValue(SUMMARY);
    mocked.getLogEvent.mockResolvedValue(DETAIL);
    mocked.traceLogEvent.mockResolvedValue(TRACE);
    mocked.exportLogs.mockResolvedValue({ blob: new Blob(), truncated: false });
  });

  it('renders source health, aggregate counts, safe records, and a correlated trace', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Logs and errors' })).toBeInTheDocument();
    expect(await screen.findByText('The request failed during persistence.')).toBeInTheDocument();
    expect(screen.getAllByText('12')).not.toHaveLength(0);
    expect(screen.getAllByText('database_unavailable')).not.toHaveLength(0);

    await userEvent.click(screen.getByRole('button', { name: 'request.failed' }));

    const dialog = await screen.findByRole('dialog', { name: 'Operational event' });
    expect(within(dialog).getAllByText('OperationalError')).toHaveLength(2);
    expect(within(dialog).getByRole('heading', { name: 'Operation timeline' })).toBeInTheDocument();
    expect(await within(dialog).findByText('request.started')).toBeInTheDocument();
    expect(mocked.getLogEvent).toHaveBeenCalledWith(
      RECORD.id,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await userEvent.click(within(dialog).getByRole('button', { name: 'Inspect similar errors' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const params = mocked.listLogs.mock.calls.at(-1)?.[0];
    expect(params?.get('error_signature')).toBe('v1:request-failed');
  });

  it('applies comma-delimited filters through URL-backed API parameters', async () => {
    renderPage();
    await screen.findByText('The request failed during persistence.');

    const service = screen.getByLabelText('Service');
    await userEvent.type(service, 'api, worker');
    await userEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

    await waitFor(() => {
      expect(mocked.listLogs).toHaveBeenCalledWith(
        expect.objectContaining({
          getAll: expect.any(Function),
        }),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
      const params = mocked.listLogs.mock.calls.at(-1)?.[0];
      expect(params?.getAll('service')).toEqual(['api', 'worker']);
    });
  });

  it('does not claim a failed load is an empty result', async () => {
    mocked.listLogs.mockRejectedValue(new TypeError('offline'));
    renderPage();

    expect(await screen.findByText('Network error. Check your connection and try again.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'No records in this period' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
  });

  it('turns an error group into a reproducible signature filter', async () => {
    renderPage();
    const group = await screen.findByRole('button', { name: /request\.failed.*1 events/ });
    await userEvent.click(group);

    await waitFor(() => {
      const params = mocked.listLogs.mock.calls.at(-1)?.[0];
      expect(params?.get('error_signature')).toBe('v1:request-failed');
    });
  });
});
