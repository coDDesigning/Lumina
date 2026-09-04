import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { quizAPI } from '@/api/quiz';
import type { QuizHistoryItem, QuizQuestionView, QuizView } from '@/api/types';
import { StoredQuiz } from './StoredQuiz';

vi.mock('@/api/quiz', () => ({ quizAPI: { listAttempts: vi.fn() } }));

const mockListAttempts = vi.mocked(quizAPI.listAttempts);

const AN_ATTEMPT: QuizHistoryItem = {
  attempt_id: 1,
  quiz_id: 7,
  score: 0.5,
  correct_count: 1,
  total_questions: 2,
  time_spent_seconds: 30,
  created_at: '2026-08-24T10:00:00Z',
  quiz_purpose: null,
  timed: false,
  expired: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockListAttempts.mockResolvedValue([AN_ATTEMPT]);
});

function question(overrides: Partial<QuizQuestionView> = {}): QuizQuestionView {
  return {
    question_id: 1,
    question_number: 1,
    question_type: 'multiple_choice',
    difficulty: 'medium',
    topic: 'Sorting',
    question: 'Which sort is stable?',
    options: ['Quicksort', 'Merge sort'],
    correct_option_index: 1,
    correct_answer: { type: 'multiple_choice', option_index: 1 },
    explanation: 'Merge sort preserves the order of equal keys.',
    ...overrides,
  };
}

function quiz(overrides: Partial<QuizView> = {}): QuizView {
  return {
    quiz_id: 7,
    course_id: 10,
    title: 'Sorting practice',
    created_at: '2026-08-23T10:00:00Z',
    user_id: 1,
    model_used: 'ollama:qwen3:8b',
    generation_settings: null,
    generation_context: null,
    quiz_purpose: null,
    exam_plan_output_id: null,
    exam_topic_key: null,
    timed: false,
    time_limit_seconds: null,
    answers_hidden: false,
    questions: [question()],
    ...overrides,
  };
}

function renderQuiz(view: QuizView) {
  render(
    <MemoryRouter>
      <StoredQuiz quiz={view} courseId={10} />
    </MemoryRouter>,
  );
}

async function seeReview(view: QuizView) {
  renderQuiz(view);
  // The review only appears once the history says this quiz was sat.
  await screen.findByText(/Merge sort|Reference answer|Accepted answer|Lecture 4|Sources:/);
}

describe('a quiz kept in the course history', () => {
  it('names the quiz and sends the student to take it', () => {
    renderQuiz(quiz());

    expect(screen.getByRole('heading', { name: 'Sorting practice' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /take this quiz/i })).toHaveAttribute(
      'href',
      '/courses/10/practice/7',
    );
  });

  it('counts the questions it actually holds, in the singular when there is one', () => {
    renderQuiz(quiz());
    expect(screen.getByText('question')).toBeInTheDocument();

    renderQuiz(quiz({ questions: [question(), question({ question_id: 2 })] }));
    expect(screen.getByText('questions')).toBeInTheDocument();
  });

  it('names each question type in words rather than the stored key', () => {
    renderQuiz(
      quiz({
        questions: [
          question(),
          question({ question_id: 2, question_type: 'true_false' }),
          question({ question_id: 3, question_type: 'short_answer' }),
          question({ question_id: 4, question_type: 'open_ended' }),
        ],
      }),
    );

    for (const label of ['Multiple choice', 'True / false', 'Short answer', 'Written answer']) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText('multiple_choice')).toBeNull();
  });

  it('marks which option was the right one', async () => {
    await seeReview(quiz());

    const correct = screen.getByText('Merge sort').closest('li') as HTMLElement;
    expect(within(correct).getByText('Correct')).toBeInTheDocument();

    const wrong = screen.getByText('Quicksort').closest('li') as HTMLElement;
    expect(within(wrong).queryByText('Correct')).toBeNull();
  });

  it('calls a written answer a reference rather than the accepted one', async () => {
    renderQuiz(
      quiz({
        questions: [
          question({
            question_type: 'open_ended',
            options: null,
            correct_option_index: null,
            correct_answer: { type: 'open_ended', reference_answer: 'It buffers.' },
          }),
        ],
      }),
    );

    expect(await screen.findByText('Reference answer')).toBeInTheDocument();
    expect(screen.getByText('It buffers.')).toBeInTheDocument();
    expect(screen.queryByText('Accepted answer')).toBeNull();
  });

  it('keeps the answers back from a student who has not sat it yet', async () => {
    // Opening a quiz from the history used to hand over every answer, so a
    // student could read the paper before taking it and never know they had.
    mockListAttempts.mockResolvedValue([]);

    renderQuiz(
      quiz({
        questions: [
          question({ citations: [{ key: 'S1', document_id: 'd', document_label: 'Lecture 4', page_start: 12, page_end: 14 }] }),
        ],
      }),
    );

    expect(await screen.findByText(/answers are held back/i)).toBeInTheDocument();
    expect(screen.getByText('Quicksort')).toBeInTheDocument();
    expect(screen.queryByText('Correct')).toBeNull();
    expect(screen.queryByText('Accepted answer')).toBeNull();
    expect(screen.queryByText(/preserves the order of equal keys/)).toBeNull();
    expect(screen.queryByText(/Lecture 4/)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /take this quiz/i })).toBeInTheDocument();
  });

  it('holds the answers back while the history is still being read', () => {
    mockListAttempts.mockReturnValue(new Promise(() => {}));

    renderQuiz(quiz());

    expect(screen.queryByText('Correct')).toBeNull();
    expect(screen.queryByText(/preserves the order of equal keys/)).toBeNull();
  });

  it('holds the answers back when the history cannot be read at all', async () => {
    mockListAttempts.mockRejectedValue(new Error('offline'));

    renderQuiz(quiz());

    expect(await screen.findByText(/answers are held back/i)).toBeInTheDocument();
    expect(screen.queryByText('Correct')).toBeNull();
  });

  it('shows the topic it was generated for only when it was not the whole course', () => {
    renderQuiz(
      quiz({
        generation_settings: {
          version: 1,
          output_type: 'quiz',
          topic_focus: 'All Topics',
          difficulty: 'medium',
        },
      }),
    );
    expect(screen.queryByText('All Topics')).toBeNull();

    renderQuiz(
      quiz({
        generation_settings: {
          version: 1,
          output_type: 'quiz',
          topic_focus: 'Graphs',
          difficulty: 'medium',
        },
      }),
    );
    expect(screen.getByText('Graphs')).toBeInTheDocument();
  });

  it('says nothing about difficulty the row never recorded', () => {
    renderQuiz(quiz({ questions: [question({ difficulty: null })] }));

    expect(screen.queryByText('medium')).toBeNull();
    expect(screen.queryByText('null')).toBeNull();
  });
});

describe('sources on a stored quiz', () => {
  const CITATION = {
    key: 'S1',
    document_id: '11111111-1111-1111-1111-111111111111',
    document_label: 'Lecture 4',
    page_start: 12,
    page_end: 14,
  };

  it('names the document and pages a question came from', async () => {
    renderQuiz(quiz({ questions: [question({ citations: [CITATION] })] }));

    expect(await screen.findByText('Lecture 4 · pp. 12–14')).toBeInTheDocument();
  });

  it('shows no sources for a question that carries none', () => {
    renderQuiz(quiz({ questions: [question({ citations: [] })] }));

    expect(screen.queryByText(/Lecture 4/)).not.toBeInTheDocument();
    expect(screen.queryByText('Sources:')).not.toBeInTheDocument();
  });

  it('shows no sources for a quiz stored before citations existed', () => {
    renderQuiz(quiz({ questions: [question()] }));

    expect(screen.queryByText('Sources:')).not.toBeInTheDocument();
  });
});
