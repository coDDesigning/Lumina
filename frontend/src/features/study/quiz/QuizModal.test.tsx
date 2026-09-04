import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import { userAPI } from '@/api/user';
import type { CreditStatus } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { QuizModal } from './QuizModal';

vi.mock('@/api/quiz', () => ({
  quizAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: { get: vi.fn().mockResolvedValue({ difficulty: 'medium', question_count: 5 }) },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockEnqueue = vi.mocked(quizAPI.enqueue);
const mockGetCredits = vi.mocked(userAPI.getCredits);

const STATUS: CreditStatus = {
  credits: null,
  metering_enabled: false,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: null,
  balance_cap: null,
  next_grant_at: null,
  generation_costs: {},
};

function renderQuiz() {
  return render(
    <CreditProvider>
      <QuizModal
        courseId={1}
        topics={['Sorting']}
        readyDocumentCount={2}
        onQueued={vi.fn()}
        onClose={vi.fn()}
      />
    </CreditProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mockGetCredits.mockResolvedValue(STATUS);
  mockEnqueue.mockResolvedValue({ job_id: 23, status: 'queued' });
});

function duplicateControlNames(): string[] {
  const dialog = screen.getByRole('dialog');
  const names = Array.from(dialog.querySelectorAll('button')).map(
    (button) => button.getAttribute('aria-label') ?? button.textContent?.trim() ?? '',
  );
  const seen = new Set<string>();
  const repeated = new Set<string>();
  for (const name of names) {
    if (seen.has(name)) {
      repeated.add(name);
    }
    seen.add(name);
  }
  return Array.from(repeated);
}

describe('practising one topic', () => {
  function renderPreset(initialTopic: string) {
    return render(
      <CreditProvider>
        <QuizModal
          courseId={1}
          topics={['Sorting']}
          readyDocumentCount={2}
          initialTopic={initialTopic}
          onQueued={vi.fn()}
          onClose={vi.fn()}
        />
      </CreditProvider>,
    );
  }

  it('shows the topic it was opened for, even one the course never listed', async () => {
    renderPreset('Graph Algorithms');

    const select = await screen.findByLabelText(/Which topic/);
    expect(select).toHaveValue('Graph Algorithms');
    expect(
      within(select).getByRole('option', { name: 'Graph Algorithms' }),
    ).toBeInTheDocument();
  });

  it('generates against the preset topic', async () => {
    renderPreset('Graph Algorithms');

    await userEvent.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      topic_focus: 'Graph Algorithms',
    });
  });

  it('lets the topic be changed before anything is generated', async () => {
    renderPreset('Graph Algorithms');

    const select = await screen.findByLabelText(/Which topic/);
    await userEvent.selectOptions(select, 'Sorting');
    await userEvent.click(screen.getByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({ topic_focus: 'Sorting' });
  });

  it('lists a preset topic the course also lists only once', async () => {
    renderPreset('Sorting');

    const select = await screen.findByLabelText(/Which topic/);
    expect(within(select).getAllByRole('option', { name: 'Sorting' })).toHaveLength(1);
  });
});


describe('setting a quiz up', () => {
  it('gives every control in the setup step its own name', async () => {
    renderQuiz();
    await screen.findByRole('button', { name: /start the quiz/i });

    expect(duplicateControlNames()).toEqual([]);
  });
});
