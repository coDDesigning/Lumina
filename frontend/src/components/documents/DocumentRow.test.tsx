import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { DocumentResponse, DocumentStatus } from '@/api/types';
import type { DocumentEntry } from '@/hooks/useCourseDocuments';
import { DocumentRow } from './DocumentRow';

function entry(status: DocumentStatus, overrides: Partial<DocumentEntry> = {}): DocumentEntry {
  const document: DocumentResponse = {
    id: 'doc-1',
    original_file_name: 'week-3-lecture.pdf',
    file_type: 'pdf',
    mime_type: 'application/pdf',
    file_size: 204_800,
    course_id: 1,
    status,
    created_at: '2026-08-20T09:00:00Z',
    updated_at: '2026-08-20T09:00:00Z',
  };

  return { document, job: null, error: null, pending: null, ...overrides };
}

function renderRow(row: DocumentEntry, handlers: Partial<{ retry: () => void; remove: () => void }> = {}) {
  return render(
    <DocumentRow
      entry={row}
      onRetry={handlers.retry ?? vi.fn()}
      onDelete={handlers.remove ?? vi.fn()}
    />,
  );
}

describe('DocumentRow', () => {
  it('names the status rather than relying on its colour', () => {
    renderRow(entry('processing'));

    expect(screen.getByText('Processing')).toBeInTheDocument();
  });

  it('offers a retry only once processing has actually failed', () => {
    renderRow(entry('ready'));
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();

    renderRow(entry('processing'));
    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();

    renderRow(entry('failed'));
    expect(screen.getAllByRole('button', { name: /try again/i })).toHaveLength(1);
  });

  it('explains in words why a source being read cannot be removed', () => {
    renderRow(entry('processing'));

    expect(screen.getByRole('button', { name: /Remove week-3-lecture/ })).toBeDisabled();
    expect(screen.getByText(/cannot be removed while it is being read/i)).toBeInTheDocument();
  });

  it('says nothing about a lock once the source is ready', () => {
    renderRow(entry('ready'));

    expect(screen.getByRole('button', { name: /Remove week-3-lecture/ })).toBeEnabled();
    expect(screen.queryByText(/cannot be removed/i)).toBeNull();
  });

  it('asks before removing, and says what removing costs', async () => {
    const remove = vi.fn();
    renderRow(entry('ready'), { remove });

    await userEvent.click(screen.getByRole('button', { name: /Remove week-3-lecture/ }));

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/uploading and processing it again/i)).toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole('button', { name: 'Remove it' }));
    expect(remove).toHaveBeenCalledWith('doc-1');
  });

  it('leaves the source alone when the reader backs out', async () => {
    const remove = vi.fn();
    renderRow(entry('ready'), { remove });

    await userEvent.click(screen.getByRole('button', { name: /Remove week-3-lecture/ }));
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(remove).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('surfaces the reason a source failed', () => {
    renderRow(
      entry('failed', {
        job: {
          id: 1,
          status: 'failed',
          attempt_count: 1,
          max_attempts: 3,
          available_at: '2026-08-20T09:00:00Z',
          started_at: '2026-08-20T09:00:10Z',
          finished_at: '2026-08-20T09:01:00Z',
          last_error_code: 'PASSWORD_PROTECTED_PDF',
          last_error_message: 'This PDF is password protected.',
          processing_stage: null,
          failed_stage: 'extracting_text',
        },
      }),
    );

    expect(screen.getByText(/This PDF is password protected\./)).toBeInTheDocument();
  });

  it('reports a failure against this row where the reader can see it', () => {
    renderRow(entry('ready', { error: 'That could not be removed.' }));

    expect(screen.getByRole('alert')).toHaveTextContent('That could not be removed.');
  });
});
