import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { flashcardsAPI } from '@/api/flashcards';
import { userAPI } from '@/api/user';
import type { CreditStatus, FlashcardGenerationResult } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { FlashcardModal } from './FlashcardModal';

vi.mock('@/api/flashcards', () => ({
  flashcardsAPI: { generate: vi.fn() },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockGenerate = vi.mocked(flashcardsAPI.generate);
const mockGetCredits = vi.mocked(userAPI.getCredits);

const UNMETERED: CreditStatus = {
  credits: null,
  metering_enabled: false,
  monthly_grant: null,
  balance_cap: null,
  next_grant_at: null,
  generation_costs: {},
};

const BROKE: CreditStatus = {
  credits: 0,
  metering_enabled: true,
  monthly_grant: 20,
  balance_cap: 100,
  next_grant_at: '2026-09-01T00:00:00Z',
  generation_costs: { flashcard: 1, quiz: 1, quiz_open_ended: 2, study_guide: 1 },
};

const DECK: FlashcardGenerationResult = {
  flashcards: {
    deck_title: 'Sorting, one card at a time',
    card_count: 2,
    flashcards: [
      { card_number: 1, front: 'What is a stack?', back: 'Last in, first out.', difficulty: 'Easy' },
      { card_number: 2, front: 'What is a heap?', back: 'A partly ordered tree.', difficulty: 'Hard' },
    ],
  },
  generated_output_id: 12,
  context_truncated: false,
  chunks_used: 4,
  chunks_available: 9,
  retrieval_narrowed: true,
  lowest_similarity: 0.41,
  highest_similarity: 0.88,
};

function renderModal(readyDocumentCount = 2) {
  const onClose = vi.fn();
  const view = render(
    <CreditProvider>
      <FlashcardModal
        courseId={1}
        courseName="Computer Systems"
        readyDocumentCount={readyDocumentCount}
        onClose={onClose}
      />
    </CreditProvider>,
  );
  return { ...view, onClose, person: userEvent.setup() };
}

async function generate(readyDocumentCount = 2) {
  const rendered = renderModal(readyDocumentCount);
  await rendered.person.click(
    await screen.findByRole('button', { name: /make flashcards/i }),
  );
  return rendered;
}

beforeEach(() => {
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockGenerate.mockResolvedValue(DECK);
});

describe('setting a deck up', () => {
  it('says what a deck is before anything is generated', async () => {
    renderModal();

    expect(await screen.findByText(/question-and-answer cards/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /make flashcards/i })).toBeEnabled();
  });

  it('refuses to build a deck out of nothing, and says why', async () => {
    renderModal(0);

    expect(await screen.findByText('There is nothing to work from yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /make flashcards/i })).toBeDisabled();
    expect(screen.queryByRole('checkbox', { name: /study profile/i })).toBeNull();
  });

  it('sends the profile opt-in only when the student asks for it', async () => {
    const { person } = renderModal();

    await person.click(await screen.findByRole('checkbox', { name: /study profile/i }));
    await person.click(screen.getByRole('button', { name: /make flashcards/i }));

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      use_profile_knowledge: true,
      include_profile_context: true,
    });
  });

  it('leaves the profile out by default', async () => {
    await generate();

    await waitFor(() => expect(mockGenerate).toHaveBeenCalled());
    expect(mockGenerate.mock.calls[0][1]).toMatchObject({
      use_profile_knowledge: false,
      include_profile_context: false,
    });
  });

  it('will not spend a balance that cannot cover the deck', async () => {
    mockGetCredits.mockResolvedValue(BROKE);
    renderModal();

    expect(await screen.findByRole('button', { name: /make flashcards/i })).toBeDisabled();
    expect(mockGenerate).not.toHaveBeenCalled();
  });

  it('shows no credit interface at all for an unmetered account', async () => {
    renderModal();

    await screen.findByRole('button', { name: /make flashcards/i });
    expect(screen.queryByText(/credit/i)).toBeNull();
  });
});

describe('while the cards are being written', () => {
  it('says what it is doing rather than leaving the dialog blank', async () => {
    mockGenerate.mockReturnValue(new Promise(() => {}));
    await generate();

    expect(await screen.findByText('Writing your cards')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
  });

  it('drops back to the setup when the student cancels', async () => {
    mockGenerate.mockReturnValue(new Promise(() => {}));
    const { person } = await generate();

    await person.click(await screen.findByRole('button', { name: /cancel/i }));

    expect(await screen.findByText(/question-and-answer cards/i)).toBeInTheDocument();
    expect(screen.queryByText('Writing your cards')).toBeNull();
  });
});

describe('when the deck arrives', () => {
  it('names the deck and deals the first card face up', async () => {
    await generate();

    expect(await screen.findByText('What is a stack?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /make another set/i })).toBeInTheDocument();
  });

  it('reports which part of the course the cards came from', async () => {
    await generate();

    await screen.findByText('What is a stack?');
    expect(screen.getByText(/4 of 9/)).toBeInTheDocument();
  });
});

describe('when the cards cannot be written', () => {
  it('explains an unmapped failure in words rather than a bare label', async () => {
    mockGenerate.mockRejectedValue(new APIError(500, { detail: '' }));
    await generate();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/flashcards could not be written/i);
    expect(alert.textContent).not.toBe('flashcard');
  });

  it('names a provider outage and says nothing was charged', async () => {
    mockGenerate.mockRejectedValue(
      new APIError(503, { detail: 'down' }, 'provider_unavailable'),
    );
    await generate();

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
    expect(screen.getByText(/Nothing was charged/)).toBeInTheDocument();
  });

  it('tries again from the error rather than making the student start over', async () => {
    mockGenerate.mockRejectedValueOnce(
      new APIError(503, { detail: 'down' }, 'provider_unavailable'),
    );
    const { person } = await generate();

    await person.click(await screen.findByRole('button', { name: /try again/i }));

    expect(await screen.findByText('What is a stack?')).toBeInTheDocument();
    expect(mockGenerate).toHaveBeenCalledTimes(2);
  });

  it('returns to the setup when the balance will not cover it', async () => {
    mockGenerate.mockRejectedValue(new APIError(402, { detail: 'Not enough credits' }));
    await generate();

    expect(await screen.findByText(/question-and-answer cards/i)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
