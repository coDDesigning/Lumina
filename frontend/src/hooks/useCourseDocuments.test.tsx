import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '../api/client';
import { coursesAPI } from '../api/courses';
import type { DocumentResponse, DocumentStatusResponse } from '../api/types';
import { useCourseDocuments } from './useCourseDocuments';

vi.mock('../api/courses', () => ({
  coursesAPI: {
    listDocuments: vi.fn(),
    getDocumentStatus: vi.fn(),
    retryDocument: vi.fn(),
    deleteDocument: vi.fn(),
  },
}));

const listDocuments = vi.mocked(coursesAPI.listDocuments);
const getDocumentStatus = vi.mocked(coursesAPI.getDocumentStatus);
const retryDocument = vi.mocked(coursesAPI.retryDocument);

const DOCUMENT_ID = '11111111-1111-1111-1111-111111111111';

function document(
  status: string,
  updatedAt: string,
  id: string = DOCUMENT_ID,
): DocumentResponse {
  return {
    id,
    original_file_name: 'lecture.pdf',
    file_type: 'pdf',
    mime_type: 'application/pdf',
  material_kind: 'unspecified',
    file_size: 1024,
    course_id: 1,
    status,
    created_at: '2026-08-19T10:00:00Z',
    updated_at: updatedAt,
  };
}

function status(
  documentStatus: string,
  updatedAt: string,
  job: Partial<DocumentStatusResponse['processing_job']> = {},
): DocumentStatusResponse {
  return {
    document: document(documentStatus, updatedAt),
    processing_job: {
      id: 1,
      status: 'running',
      attempt_count: 1,
      max_attempts: 3,
      available_at: '2026-08-19T10:00:00Z',
      started_at: '2026-08-19T10:00:01Z',
      finished_at: null,
      last_error_code: null,
      last_error_message: null,
      processing_stage: 'extracting_text',
      failed_stage: null,
      ...job,
    },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe('useCourseDocuments polling lifecycle', () => {
  it('walks a document to ready and then stops polling', async () => {
    listDocuments.mockResolvedValue([document('uploaded', '2026-08-19T10:00:00Z')]);
    getDocumentStatus
      .mockResolvedValueOnce(status('processing', '2026-08-19T10:00:05Z'))
      .mockResolvedValueOnce(
        status('ready', '2026-08-19T10:00:10Z', {
          status: 'succeeded',
          processing_stage: null,
          finished_at: '2026-08-19T10:00:10Z',
        }),
      );

    const { result } = renderHook(() => useCourseDocuments(1));

    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.entries).toHaveLength(1);

    await advance(0);
    expect(result.current.entries[0].document.status).toBe('processing');
    expect(result.current.entries[0].job?.processing_stage).toBe('extracting_text');

    await advance(2000);
    expect(result.current.entries[0].document.status).toBe('ready');
    expect(result.current.readyCount).toBe(1);

    const callsAtTerminal = getDocumentStatus.mock.calls.length;
    await advance(60_000);
    expect(getDocumentStatus).toHaveBeenCalledTimes(callsAtTerminal);
  });

  it('leaves no timer running after unmount', async () => {
    listDocuments.mockResolvedValue([document('processing', '2026-08-19T10:00:00Z')]);
    getDocumentStatus.mockResolvedValue(status('processing', '2026-08-19T10:00:05Z'));

    const { unmount } = renderHook(() => useCourseDocuments(1));

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);
    expect(getDocumentStatus).toHaveBeenCalled();

    unmount();
    const callsAtUnmount = getDocumentStatus.mock.calls.length;

    await advance(60_000);
    expect(getDocumentStatus).toHaveBeenCalledTimes(callsAtUnmount);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('abandons the previous course when the course changes', async () => {
    const otherId = '22222222-2222-2222-2222-222222222222';
    listDocuments.mockImplementation(async (courseId: number) => [
      courseId === 1
        ? document('processing', '2026-08-19T10:00:00Z')
        : document('processing', '2026-08-19T10:00:00Z', otherId),
    ]);
    getDocumentStatus.mockResolvedValue(status('processing', '2026-08-19T10:00:05Z'));

    const { rerender } = renderHook(({ courseId }) => useCourseDocuments(courseId), {
      initialProps: { courseId: 1 },
    });

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);
    expect(getDocumentStatus.mock.calls.every(([courseId]) => courseId === 1)).toBe(true);

    getDocumentStatus.mockClear();
    rerender({ courseId: 2 });

    await act(async () => {
      await Promise.resolve();
    });
    await advance(10_000);

    expect(getDocumentStatus).toHaveBeenCalled();
    expect(getDocumentStatus.mock.calls.every(([courseId]) => courseId === 2)).toBe(true);
  });

  it('removes a document that disappears while polling', async () => {
    listDocuments.mockResolvedValue([document('processing', '2026-08-19T10:00:00Z')]);
    getDocumentStatus.mockRejectedValue(new APIError(404, { detail: 'Document not found' }));

    const { result } = renderHook(() => useCourseDocuments(1));

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);

    expect(result.current.entries).toHaveLength(0);

    const callsAfterRemoval = getDocumentStatus.mock.calls.length;
    await advance(60_000);
    expect(getDocumentStatus).toHaveBeenCalledTimes(callsAfterRemoval);
  });

  it('applies the retry reset and resumes polling from failed', async () => {
    listDocuments.mockResolvedValue([document('failed', '2026-08-19T10:00:00Z')]);
    getDocumentStatus.mockResolvedValue(
      status('failed', '2026-08-19T10:00:00Z', {
        status: 'failed',
        processing_stage: null,
        failed_stage: 'extracting_text',
        last_error_message: 'No readable text could be extracted.',
        finished_at: '2026-08-19T10:00:00Z',
      }),
    );
    retryDocument.mockResolvedValue(
      status('uploaded', '2026-08-19T10:05:00Z', {
        status: 'queued',
        processing_stage: null,
        failed_stage: null,
        last_error_message: null,
        started_at: null,
        finished_at: null,
      }),
    );

    const { result } = renderHook(() => useCourseDocuments(1));

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);

    expect(result.current.entries[0].document.status).toBe('failed');
    expect(result.current.entries[0].job?.last_error_message).toBe(
      'No readable text could be extracted.',
    );

    await act(async () => {
      await result.current.retryDocument(DOCUMENT_ID);
    });

    expect(result.current.entries[0].document.status).toBe('uploaded');
    expect(result.current.entries[0].job?.status).toBe('queued');

    getDocumentStatus.mockClear();
    await advance(2000);
    expect(getDocumentStatus).toHaveBeenCalled();
  });
});
