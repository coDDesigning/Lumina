import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import { settingsAPI } from '@/api/settings';
import { userAPI } from '@/api/user';
import type { CourseSettings, CreditStatus } from '@/api/types';
import { CreditProvider } from '@/context/CreditContext';
import { QuizModal } from './QuizModal';

vi.mock('@/api/quiz', () => ({
  quizAPI: { enqueue: vi.fn() },
}));

vi.mock('@/api/settings', () => ({
  settingsAPI: { get: vi.fn(), update: vi.fn() },
}));

vi.mock('@/api/user', () => ({
  userAPI: { getCredits: vi.fn() },
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: true, user: { id: 1 } }),
}));

const mockEnqueue = vi.mocked(quizAPI.enqueue);
const mockSettings = vi.mocked(settingsAPI.get);
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

const METERED: CreditStatus = {
  credits: 40,
  metering_enabled: true,
  email_verification_required: false,
  is_email_verified: true,
  monthly_grant: 20,
  balance_cap: 100,
  next_grant_at: '2026-09-01T00:00:00Z',
  generation_costs: { quiz: 1, quiz_open_ended: 2, flashcard: 1, study_guide: 1 },
};

const SETTINGS = {
  study_mode: 'practice',
  difficulty: 'medium',
  question_count: 5,
  summary_length: 'medium',
  detail_level: 'standard',
} satisfies CourseSettings;

function renderQuiz(
  props: Partial<{
    onQueued: (jobId: number) => void;
    onClose: () => void;
  }> = {},
) {
  const person = userEvent.setup();
  render(
    <CreditProvider>
      <QuizModal
        courseId={1}
        topics={['Sorting']}
        readyDocumentCount={2}
        onQueued={vi.fn()}
        onClose={vi.fn()}
        {...props}
      />
    </CreditProvider>,
  );
  return person;
}

beforeEach(() => {
  mockEnqueue.mockResolvedValue({ job_id: 23, status: 'queued' });
  mockGetCredits.mockResolvedValue(UNMETERED);
  mockSettings.mockResolvedValue(SETTINGS);
});

describe('choosing what to be asked', () => {
  it('offers every question type the backend can write', async () => {
    renderQuiz();

    await screen.findByRole('button', { name: /start the quiz/i });
    for (const label of ['Multiple choice', 'True or false', 'Short answer', 'Written answer']) {
      expect(screen.getByRole('checkbox', { name: new RegExp(label, 'i') })).toBeInTheDocument();
    }
  });

  it('sends only the types that were ticked', async () => {
    const person = renderQuiz();

    await person.click(await screen.findByRole('checkbox', { name: /short answer/i }));
    await person.click(screen.getByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      question_types: ['multiple_choice', 'short_answer'],
    });
  });

  it('refuses to leave the quiz with no question type at all', async () => {
    const person = renderQuiz();

    const only = await screen.findByRole('checkbox', { name: /multiple choice/i });
    await person.click(only);

    expect(only).toBeChecked();

    await person.click(screen.getByRole('button', { name: /start the quiz/i }));
    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      question_types: ['multiple_choice'],
    });
  });

  it('sends the count, topic and difficulty the student picked', async () => {
    const person = renderQuiz();

    await person.selectOptions(await screen.findByLabelText(/How many questions/), '15');
    await person.selectOptions(screen.getByLabelText(/Which topic/), 'Sorting');
    await person.selectOptions(screen.getByLabelText(/How hard/), 'hard');
    await person.click(screen.getByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(mockEnqueue.mock.calls[0][1]).toMatchObject({
      question_count: 15,
      topic_focus: 'Sorting',
      difficulty: 'hard',
    });
  });
});

describe('the defaults the course already saved', () => {
  it('opens on the difficulty the course settings recorded', async () => {
    mockSettings.mockResolvedValue({ ...SETTINGS, difficulty: 'hard', question_count: 10 });
    renderQuiz();

    await waitFor(() => expect(screen.getByLabelText(/How hard/)).toHaveValue('hard'));
    expect(screen.getByLabelText(/How many questions/)).toHaveValue('10');
  });

  it('rounds a stored count up to one the quiz actually offers', async () => {
    mockSettings.mockResolvedValue({ ...SETTINGS, difficulty: 'easy', question_count: 12 });
    renderQuiz();

    await waitFor(() => expect(screen.getByLabelText(/How many questions/)).toHaveValue('15'));
  });

  it('falls back to the largest count rather than an option that does not exist', async () => {
    mockSettings.mockResolvedValue({ ...SETTINGS, question_count: 500 });
    renderQuiz();

    await waitFor(() => expect(screen.getByLabelText(/How many questions/)).toHaveValue('20'));
  });
});

describe('what a quiz costs', () => {
  it('prices a written-answer quiz higher, because grading is prepaid', async () => {
    mockGetCredits.mockResolvedValue(METERED);
    const person = renderQuiz();

    expect(await screen.findByText(/This quiz costs 1\./)).toBeInTheDocument();

    await person.click(screen.getByRole('checkbox', { name: /written answer/i }));

    expect(await screen.findByText(/marked by the model, so this quiz costs 2\./)).toBeInTheDocument();
  });

  it('quotes no price at all to an unmetered account', async () => {
    renderQuiz();

    await screen.findByRole('button', { name: /start the quiz/i });
    expect(screen.queryByText(/this quiz costs/i)).toBeNull();
  });
});

describe('practising from somewhere else in the app', () => {
  it('queues in the background rather than sitting the quiz in the dialog', async () => {
    const onQueued = vi.fn();
    const onClose = vi.fn();
    const person = renderQuiz({ onQueued, onClose });

    await person.click(await screen.findByRole('button', { name: /start the quiz/i }));

    await waitFor(() => expect(mockEnqueue).toHaveBeenCalled());
    expect(onQueued).toHaveBeenCalledWith(23);
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByText('Which sort is stable?')).toBeNull();
  });
});

describe('with nothing to be asked about', () => {
  it('offers no setup controls at all, and says why', async () => {
    render(
      <CreditProvider>
        <QuizModal
          courseId={1}
          topics={['Sorting']}
          readyDocumentCount={0}
          onQueued={vi.fn()}
          onClose={vi.fn()}
        />
      </CreditProvider>,
    );

    expect(await screen.findByText('There is nothing to work from yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /start the quiz/i })).toBeDisabled();
    expect(screen.queryByLabelText(/How many questions/)).toBeNull();
  });
});

describe('naming the controls', () => {
  it('gives the topic list every topic the course carries, once each', async () => {
    render(
      <CreditProvider>
        <QuizModal
          courseId={1}
          topics={['Sorting', 'sorting', 'Graphs']}
          readyDocumentCount={2}
          onQueued={vi.fn()}
          onClose={vi.fn()}
        />
      </CreditProvider>,
    );

    const select = await screen.findByLabelText(/Which topic/);
    expect(within(select).getAllByRole('option', { name: /^sorting$/i })).toHaveLength(1);
    expect(within(select).getByRole('option', { name: 'Graphs' })).toBeInTheDocument();
  });
});
