import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type { GeneratedOutputDetail } from '@/api/types';
import { SavedDeckModal } from './SavedDeckModal';

vi.mock('@/api/generatedOutputs', () => ({
  generatedOutputsAPI: { get: vi.fn(), list: vi.fn() },
}));

const mockGet = vi.mocked(generatedOutputsAPI.get);

const CARDS = [
  { card_number: 1, front: 'What is a stack?', back: 'Last in, first out.', difficulty: 'Easy' },
  { card_number: 2, front: 'What is a queue?', back: 'First in, first out.', difficulty: 'Medium' },
];

function output(content: unknown, overrides: Partial<GeneratedOutputDetail> = {}): GeneratedOutputDetail {
  return {
    id: 5,
    course_id: 10,
    output_type: 'flashcards',
    topic: 'Sorting',
    created_at: '2026-08-23T10:00:00Z',
    user_id: 1,
    model_used: 'ollama:qwen3:8b',
    generation_settings: null,
    generation_context: null,
    content: content as Record<string, unknown>,
    ...overrides,
  };
}

function renderModal() {
  const onClose = vi.fn();
  render(<SavedDeckModal courseId={10} outputId={5} courseName="Algorithms" onClose={onClose} />);
  return { onClose, person: userEvent.setup() };
}

beforeEach(() => {
  mockGet.mockResolvedValue(output({ deck_title: 'Sorting basics', flashcards: CARDS }));
});

describe('opening a saved deck', () => {
  it('names the deck it stored and deals the first card', async () => {
    renderModal();

    expect(await screen.findByText('What is a stack?')).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveAccessibleName(/Sorting basics/);
    expect(mockGet).toHaveBeenCalledWith(10, 5, expect.anything());
  });

  it('falls back to a plain name when the row stored no title', async () => {
    mockGet.mockResolvedValue(output({ flashcards: CARDS }));
    renderModal();

    await screen.findByText('What is a stack?');
    expect(screen.getByRole('dialog')).toHaveAccessibleName(/Flashcards/);
  });

  it('says the deck could not be opened rather than showing an empty dialog', async () => {
    mockGet.mockRejectedValue(new APIError(503, { detail: 'That deck is unavailable.' }));
    renderModal();

    expect(await screen.findByRole('alert')).toHaveTextContent('That deck is unavailable.');
    expect(screen.queryByRole('group')).toBeNull();
  });

  it('keeps an older deck rather than pretending it is empty', async () => {
    mockGet.mockResolvedValue(output({ some: 'older shape' }));
    renderModal();

    expect(
      await screen.findByText('This deck was saved in an older shape'),
    ).toBeInTheDocument();
    expect(screen.queryByText('What is a stack?')).toBeNull();
  });

  it('closes when the reader is done with it', async () => {
    const { onClose, person } = renderModal();

    await screen.findByText('What is a stack?');
    await person.click(screen.getByRole('button', { name: 'Done' }));

    expect(onClose).toHaveBeenCalled();
  });
});
