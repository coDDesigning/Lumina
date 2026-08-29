import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { profileDocumentsAPI } from '../api/profileDocuments';
import type { ProfileDocumentResponse, ProfileDocumentStatusResponse } from '../api/types';
import { useProfileDocuments } from './useProfileDocuments';

vi.mock('../api/profileDocuments', () => ({
  profileDocumentsAPI: {
    list: vi.fn(),
    getStatus: vi.fn(),
    upload: vi.fn(),
    retry: vi.fn(),
    delete: vi.fn(),
  },
}));

const listDocuments = vi.mocked(profileDocumentsAPI.list);
const getDocumentStatus = vi.mocked(profileDocumentsAPI.getStatus);
const uploadDoc = vi.mocked(profileDocumentsAPI.upload);
const retryDocument = vi.mocked(profileDocumentsAPI.retry);
const deleteDocument = vi.mocked(profileDocumentsAPI.delete);

const DOCUMENT_ID = '11111111-1111-1111-1111-111111111111';

function profileDoc(
  status: string,
  updatedAt: string,
  id: string = DOCUMENT_ID,
): ProfileDocumentResponse {
  return {
    id,
    original_file_name: 'syllabus.pdf',
    file_type: 'pdf',
    mime_type: 'application/pdf',
    file_size: 1024,
    user_id: 1,
    status,
    processing_error: null,
    created_at: '2026-08-19T10:00:00Z',
    updated_at: updatedAt,
  };
}

function docStatus(
  statusStr: string,
  updatedAt: string,
  id: string = DOCUMENT_ID,
): ProfileDocumentStatusResponse {
  return {
    document: profileDoc(statusStr, updatedAt, id),
    processing_job: {
      id: 1,
      status: statusStr === 'ready' ? 'succeeded' : statusStr === 'failed' ? 'failed' : 'running',
      attempt_count: 1,
      max_attempts: 3,
      available_at: '2026-08-19T10:00:00Z',
      started_at: '2026-08-19T10:00:01Z',
      finished_at: statusStr === 'ready' || statusStr === 'failed' ? '2026-08-19T10:00:05Z' : null,
      last_error_code: statusStr === 'failed' ? 'EXTRACTION_FAILED' : null,
      last_error_message: null,
      processing_stage: statusStr === 'ready' ? null : 'extracting_text',
      failed_stage: statusStr === 'failed' ? 'extracting_text' : null,
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

describe('useProfileDocuments hook', () => {
  it('loads documents and counts ready documents', async () => {
    listDocuments.mockResolvedValue([
      profileDoc('ready', '2026-08-19T10:00:00Z', 'doc-1'),
      profileDoc('uploaded', '2026-08-19T10:00:00Z', 'doc-2'),
    ]);
    getDocumentStatus.mockResolvedValue(docStatus('ready', '2026-08-19T10:00:05Z', 'doc-2'));

    const { result } = renderHook(() => useProfileDocuments());

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);

    expect(result.current.entries).toHaveLength(2);
    expect(result.current.readyCount).toBe(2);
    expect(getDocumentStatus).toHaveBeenCalledWith('doc-2', expect.anything());
  });

  it('allows retrying a failed profile document', async () => {
    listDocuments.mockResolvedValue([
      profileDoc('failed', '2026-08-19T10:00:00Z', 'doc-failed'),
    ]);
    retryDocument.mockResolvedValue(docStatus('uploaded', '2026-08-19T10:00:01Z', 'doc-failed'));

    const { result } = renderHook(() => useProfileDocuments());

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);

    expect(result.current.entries[0].document.status).toBe('failed');

    await act(async () => {
      await result.current.retryDocument('doc-failed');
    });

    expect(retryDocument).toHaveBeenCalledWith('doc-failed');
    expect(result.current.entries[0].document.status).toBe('uploaded');
  });

  it('allows deleting a profile document and removes it from entries', async () => {
    listDocuments.mockResolvedValue([
      profileDoc('ready', '2026-08-19T10:00:00Z', 'doc-delete'),
    ]);
    deleteDocument.mockResolvedValue(undefined);

    const { result } = renderHook(() => useProfileDocuments());

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);

    expect(result.current.entries).toHaveLength(1);

    await act(async () => {
      await result.current.deleteDocument('doc-delete');
    });

    expect(deleteDocument).toHaveBeenCalledWith('doc-delete');
    expect(result.current.entries).toHaveLength(0);
  });

  it('uploads a document, appends it to state, and updates readyCount without error', async () => {
    listDocuments.mockResolvedValue([]);
    uploadDoc.mockResolvedValue({
      document: profileDoc('uploaded', '2026-08-19T10:00:00Z', 'doc-uploaded'),
      duplicate: false,
    });
    getDocumentStatus.mockResolvedValue(
      docStatus('ready', '2026-08-19T10:00:05Z', 'doc-uploaded'),
    );

    const { result } = renderHook(() => useProfileDocuments());

    await act(async () => {
      await Promise.resolve();
    });
    await advance(0);

    expect(result.current.entries).toHaveLength(0);

    const fakeFile = new File(['test content'], 'syllabus.pdf', { type: 'application/pdf' });
    await act(async () => {
      await result.current.uploadDocument(fakeFile);
    });

    expect(uploadDoc).toHaveBeenCalledWith(fakeFile);
    expect(result.current.entries).toHaveLength(1);
    expect(result.current.entries[0].document.id).toBe('doc-uploaded');
    expect(result.current.entries[0].document.status).toBe('uploaded');

    await advance(0);
    expect(result.current.entries[0].document.status).toBe('ready');
    expect(result.current.readyCount).toBe(1);
  });
});
