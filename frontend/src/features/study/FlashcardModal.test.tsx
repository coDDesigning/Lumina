import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { flashcardsAPI } from '@/api/flashcards';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { FlashcardModal } from './FlashcardModal';

vi.mock('@/api/flashcards', () => ({
  flashcardsAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockEnqueue = vi.mocked(flashcardsAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);

const UNMETERED: CreditStatus = {
  credits: null,
  metering_enabled: false,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: null,
  balance_cap: null,
  next_grant_at: null,
  generation_costs: {},
};

const BROKE: CreditStatus = {
  credits: 0,
  metering_enabled: true,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: 20,
  balance_cap: 100,
  next_grant_at: '2026-09-01T00:00:00Z',
  generation_costs: { flashcard: 1, quiz: 1, quiz_open_ended: 2, study_guide: 1 },
};

function renderModal(readyDocumentCount = 2) {
  const onClose = vi.fn();
  const onQueued = vi.fn();
  const view = render(
    <CreditProvider>
      <FlashcardModal
        courseId={1}
        courseName="Computer Systems"
        readyDocumentCount={readyDocumentCount}
        onClose={onClose}
        onQueued={onQueued}
      />
    </CreditProvider>,
  );
  return { ...view, onClose, onQueued, person: userEvent.setup() };
}

async function queue(readyDocumentCount = 2) {
  const rendered = renderModal(readyDocumentCount);
  await rendered.person.click(await screen.findByRole('button', { name: /make flashcards/i }));
  return rendered;
}

beforeEach(() => {
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockEnqueue.mockResolvedValue({ job_id: 12, status: 'queued' });
});

describe('setting a deck up', () => {
  it('says what a deck is before anything is queued', async () => {
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

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      use_profile_knowledge: true,
      include_profile_context: true,
    });
  });

  it('leaves the profile out by default', async () => {
    await queue();

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      use_profile_knowledge: false,
      include_profile_context: false,
    });
  });

  it('will not spend a balance that cannot cover the deck', async () => {
    mockGetCredits.mockResolvedValue(BROKE);
    renderModal();

    expect(await screen.findByRole('button', { name: /make flashcards/i })).toBeDisabled();
    expect(mockEnqueue).not.toHaveBeenCalled();
  });

  it('shows no credit interface at all for an unmetered account', async () => {
    renderModal();

    await screen.findByRole('button', { name: /make flashcards/i });
    expect(screen.queryByText(/credit/i)).toBeNull();
  });
});

describe('handing the deck off to the queue', () => {
  it('keeps the setup visible while the short enqueue request is pending', async () => {
    mockEnqueue.mockReturnValue(new Promise(() => {}));
    await queue();

    expect(await screen.findByText(/question-and-answer cards/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /make flashcards/i })).toBeDisabled();
  });

  it('reports the accepted job and closes rather than waiting for cards', async () => {
    const { onClose, onQueued } = await queue();

    await waitFor(() => expect(onQueued).toHaveBeenCalledWith(12));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe('when the deck cannot be queued', () => {
  it('explains an unmapped failure in words rather than a bare label', async () => {
    mockEnqueue.mockRejectedValue(new APIError(500, { detail: '' }));
    await queue();

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/flashcards could not be queued/i);
    expect(alert.textContent).not.toBe('flashcard');
  });

  it('names a provider outage and says nothing was charged', async () => {
    mockEnqueue.mockRejectedValue(new APIError(503, { detail: 'down' }, 'provider_unavailable'));
    await queue();

    expect(await screen.findByText('The AI service is down')).toBeInTheDocument();
    expect(screen.getByText(/Nothing was charged/)).toBeInTheDocument();
  });

  it('keeps the dialog open on failure so the student can try again', async () => {
    mockEnqueue.mockRejectedValueOnce(
      new APIError(503, { detail: 'down' }, 'provider_unavailable'),
    );
    const { person, onClose } = await queue();
    expect(onClose).not.toHaveBeenCalled();

    await person.click(await screen.findByRole('button', { name: /try again/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });
});
