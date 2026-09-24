import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { GenerationFailure } from '@/api/errors';
import { SyllabusTopicSuggestions } from './SyllabusTopicSuggestions';

function failure(overrides: Partial<GenerationFailure> = {}): GenerationFailure {
  return {
    title: 'The AI service is down',
    message: 'The model could not be reached. Nothing was charged.',
    status: 503,
    code: 'provider_unavailable',
    retryable: true,
    remedy: null,
    ...overrides,
  };
}

const defaultProps = {
  suggestions: [],
  isPending: false,
  error: null,
  declared: [] as string[],
};

describe('SyllabusTopicSuggestions', () => {
  it('says nothing before anything has been requested', () => {
    const { container } = render(
      <SyllabusTopicSuggestions {...defaultProps} onAdd={vi.fn()} onRetry={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('says it is reading the syllabus while pending', () => {
    render(
      <SyllabusTopicSuggestions
        {...defaultProps}
        isPending
        onAdd={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText('Reading topics from your syllabus…')).toBeInTheDocument();
  });

  it('shows the error and lets the user retry', async () => {
    const onRetry = vi.fn();
    const user = userEvent.setup();
    render(
      <SyllabusTopicSuggestions
        {...defaultProps}
        error={failure()}
        onAdd={vi.fn()}
        onRetry={onRetry}
      />,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The AI service is down');
    expect(alert).toHaveTextContent('The model could not be reached. Nothing was charged.');

    await user.click(screen.getByRole('button', { name: 'Try again' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('lists what the syllabus found once it succeeds', async () => {
    const onAdd = vi.fn();
    const user = userEvent.setup();
    render(
      <SyllabusTopicSuggestions
        {...defaultProps}
        suggestions={[
          { name: 'Graph Traversal', weight_percent: 20 },
          { name: 'Dynamic Programming', weight_percent: null },
        ]}
        onAdd={onAdd}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByText('Found in your syllabus')).toBeInTheDocument();
    expect(
      screen.getByText(
        "Topics your syllabus lists that aren't in your topic list yet. Nothing is saved until you save the course.",
      ),
    ).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Graph Traversal' }));
    expect(onAdd).toHaveBeenCalledWith(['Graph Traversal']);
  });

  it('hides topics already declared', () => {
    const { container } = render(
      <SyllabusTopicSuggestions
        {...defaultProps}
        suggestions={[{ name: 'Graph Traversal', weight_percent: 20 }]}
        declared={['graph traversal']}
        onAdd={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
