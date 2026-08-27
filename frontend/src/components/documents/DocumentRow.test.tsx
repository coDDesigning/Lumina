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
    material_kind: 'slides',
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

  it('offers no removal at all while a source is being read, and says why', () => {
    renderRow(entry('processing'));

    expect(screen.queryByRole('button', { name: /Remove week-3-lecture/ })).toBeNull();
    expect(screen.getByText(/cannot be removed until this finishes/i)).toBeInTheDocument();
  });

  it('reports how far through the reading a source is', () => {
    renderRow(
      entry('processing', {
        job: {
          id: 1,
          status: 'running',
          attempt_count: 1,
          max_attempts: 3,
          available_at: '2026-08-20T09:00:00Z',
          started_at: '2026-08-20T09:00:10Z',
          finished_at: null,
          last_error_code: null,
          last_error_message: null,
          processing_stage: 'running_ocr',
          failed_stage: null,
        },
      }),
    );

    expect(screen.getByText('Reading the scans')).toBeInTheDocument();
    expect(screen.getByText(/text is being read off them/i)).toBeInTheDocument();
    expect(screen.getByText(/step 3 of 7/)).toBeInTheDocument();

    const bar = screen.getByRole('progressbar', { name: /Reading week-3-lecture/ });
    expect(bar).toHaveAttribute('aria-valuenow', '3');
    expect(bar).toHaveAttribute('aria-valuemax', '7');
  });

  it('says what kind of material a ready source is', () => {
    renderRow(entry('ready'));

    expect(screen.getByText('Slides')).toBeInTheDocument();
    expect(screen.getByText('PDF')).toBeInTheDocument();
  });

  it('says nothing about the kind when the reader did not choose one', () => {
    const row = entry('ready');
    renderRow({ ...row, document: { ...row.document, material_kind: 'unspecified' } });

    expect(screen.getByText('PDF')).toBeInTheDocument();
    expect(screen.queryByText('Slides')).toBeNull();
    expect(screen.queryByText('Unspecified')).toBeNull();
  });

  it('says nothing about a lock once the source is ready', () => {
    renderRow(entry('ready'));

    expect(screen.getByRole('button', { name: /Remove week-3-lecture/ })).toBeEnabled();
    expect(screen.queryByText(/cannot be removed/i)).toBeNull();
    expect(screen.queryByRole('progressbar')).toBeNull();
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

  function failedJob(overrides: Partial<NonNullable<DocumentEntry['job']>> = {}) {
    return {
      id: 1,
      status: 'failed' as const,
      attempt_count: 3,
      max_attempts: 3,
      available_at: '2026-08-20T09:00:00Z',
      started_at: '2026-08-20T09:00:10Z',
      finished_at: '2026-08-20T09:01:00Z',
      last_error_code: 'PASSWORD_PROTECTED_PDF',
      last_error_message: 'Document is encrypted; decryption not supported.',
      processing_stage: null,
      failed_stage: 'extracting_text' as const,
      ...overrides,
    };
  }

  it('turns a failure code into words a student can act on', () => {
    renderRow(entry('failed', { job: failedJob() }));

    expect(screen.getByText('Locked file')).toBeInTheDocument();
    expect(screen.getByText(/password protected, so it cannot be opened/i)).toBeInTheDocument();
    expect(screen.getByText(/Save an unlocked copy/i)).toBeInTheDocument();
    expect(screen.queryByText(/decryption not supported/i)).toBeNull();
  });

  it('says how many attempts were spent before giving up', () => {
    renderRow(entry('failed', { job: failedJob() }));

    expect(screen.getByText('attempt 3 of 3')).toBeInTheDocument();
  });

  it('falls back to the reported message for a code it does not recognise', () => {
    renderRow(
      entry('failed', {
        job: failedJob({
          last_error_code: 'SOMETHING_NEW',
          last_error_message: 'The reader ran out of memory.',
        }),
      }),
    );

    expect(screen.getByText('The reader ran out of memory.')).toBeInTheDocument();
  });

  it('reports a failure against this row where the reader can see it', () => {
    renderRow(entry('ready', { error: 'That could not be removed.' }));

    expect(screen.getByRole('alert')).toHaveTextContent('That could not be removed.');
  });

  it('omits retry and remove buttons when readOnly is true', () => {
    render(
      <DocumentRow
        entry={entry('failed', { job: failedJob() })}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
        readOnly
      />,
    );

    expect(screen.queryByRole('button', { name: /try again/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /remove/i })).toBeNull();
  });
});
