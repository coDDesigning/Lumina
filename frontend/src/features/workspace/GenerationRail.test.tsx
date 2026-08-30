import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GenerationJob } from '@/api/types';
import { GenerationRail } from './GenerationRail';

function job(overrides: Partial<GenerationJob> = {}): GenerationJob {
  return {
    id: 1,
    job_type: 'generate_study_guide',
    status: 'queued',
    attempt_count: 0,
    max_attempts: 3,
    created_at: '2026-08-30T12:00:00Z',
    started_at: null,
    finished_at: null,
    error_code: null,
    error_message: null,
    generated_output_id: null,
    quiz_id: null,
    ...overrides,
  };
}

function renderRail(jobs: GenerationJob[], overrides: Record<string, unknown> = {}) {
  const props = {
    jobs,
    isLoading: false,
    error: null,
    retryingId: null,
    onReload: vi.fn(),
    onRetry: vi.fn(),
    onOpenGuide: vi.fn(),
    onOpenQuiz: vi.fn(),
    ...overrides,
  };
  render(<GenerationRail {...props} />);
  return props;
}

describe('GenerationRail', () => {
  it('names queued and running work without blocking the page', () => {
    renderRail([
      job(),
      job({ id: 2, job_type: 'generate_quiz', status: 'running', attempt_count: 1 }),
    ]);

    expect(screen.getByText('Queued')).toBeInTheDocument();
    expect(screen.getByText('Generating in the background')).toBeInTheDocument();
  });

  it('opens the artifact attached to each completed job', async () => {
    const props = renderRail([
      job({ status: 'succeeded', generated_output_id: 12, finished_at: '2026-08-30T12:01:00Z' }),
      job({
        id: 2,
        job_type: 'generate_quiz',
        status: 'succeeded',
        quiz_id: 42,
        finished_at: '2026-08-30T12:02:00Z',
      }),
      job({
        id: 3,
        job_type: 'generate_flashcard',
        status: 'succeeded',
        generated_output_id: 99,
        finished_at: '2026-08-30T12:03:00Z',
      }),
    ], { onOpenFlashcards: vi.fn() });
    const person = userEvent.setup();

    await person.click(screen.getByRole('button', { name: /Study guide/ }));
    await person.click(screen.getByRole('button', { name: /Practice quiz/ }));
    await person.click(screen.getByRole('button', { name: /Flashcards/ }));

    expect(props.onOpenGuide).toHaveBeenCalledWith(12);
    expect(props.onOpenQuiz).toHaveBeenCalledWith(42);
    expect(props.onOpenFlashcards).toHaveBeenCalledWith(99);
  });

  it('offers the durable retry action for a failed job', async () => {
    const props = renderRail([
      job({
        status: 'failed',
        attempt_count: 3,
        finished_at: '2026-08-30T12:03:00Z',
        error_code: 'provider_unavailable',
        error_message: 'The model could not be reached.',
      }),
    ]);

    expect(screen.getByRole('alert')).toHaveTextContent('The model could not be reached.');
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(props.onRetry).toHaveBeenCalledWith(1);
  });

  it('gives a failed status read a recovery route', async () => {
    const onReload = vi.fn();
    renderRail([], { error: 'Network error.', onReload });

    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onReload).toHaveBeenCalledOnce();
  });
});
