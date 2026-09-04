import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { QuizAttemptResponse, QuizQuestionView } from '@/api/types';
import { QuizResults } from './QuizResults';

const MULTIPLE_CHOICE: QuizQuestionView = {
  question_id: 1,
  question_number: 1,
  question_type: 'multiple_choice',
  difficulty: 'medium',
  topic: 'Sorting',
  question: 'Which sort is stable?',
  options: ['Quicksort', 'Merge sort', 'Heapsort', 'Selection sort'],
  correct_option_index: 1,
  correct_answer: { type: 'multiple_choice', option_index: 1 },
  explanation: 'Merge sort preserves the order of equal keys.',
};

const TRUE_FALSE: QuizQuestionView = {
  question_id: 2,
  question_number: 2,
  question_type: 'true_false',
  difficulty: 'easy',
  topic: 'Sorting',
  question: 'Quicksort is always O(n log n).',
  options: ['True', 'False'],
  correct_option_index: 1,
  correct_answer: { type: 'true_false', value: false },
  explanation: 'Its worst case is quadratic.',
};

const OPEN_ENDED: QuizQuestionView = {
  question_id: 3,
  question_number: 3,
  question_type: 'open_ended',
  difficulty: 'hard',
  topic: 'Sorting',
  question: 'Explain why merge sort needs extra space.',
  options: null,
  correct_option_index: null,
  correct_answer: { type: 'open_ended', reference_answer: 'It merges into a separate buffer.' },
  explanation: 'The merge step cannot be done in place efficiently.',
};

const QUESTIONS = [MULTIPLE_CHOICE, TRUE_FALSE, OPEN_ENDED];

const ATTEMPT: QuizAttemptResponse = {
  attempt_id: 1,
  quiz_id: 7,
  score: 0.5,
  correct_count: 1,
  graded_count: 2,
  total_questions: 3,
  time_spent_seconds: 95,
  created_at: '2026-08-23T10:00:00Z',
  quiz_purpose: null,
  timed: false,
  expired: false,
  answers: [
    {
      question_id: 1,
      question_type: 'multiple_choice',
      selected_option_index: 1,
      text_response: null,
      correct_option_index: 1,
      correct_answer: null,
      is_correct: true,
      score: 1,
      feedback: null,
    },
    {
      question_id: 2,
      question_type: 'true_false',
      selected_option_index: 0,
      text_response: null,
      correct_option_index: 1,
      correct_answer: null,
      is_correct: false,
      score: 0,
      feedback: null,
    },
    {
      question_id: 3,
      question_type: 'open_ended',
      selected_option_index: null,
      text_response: 'It uses a buffer.',
      correct_option_index: null,
      correct_answer: null,
      is_correct: null,
      score: null,
      feedback: null,
    },
  ],
};

function seeResults(attempt: QuizAttemptResponse = ATTEMPT) {
  render(<QuizResults attempt={attempt} questions={QUESTIONS} />);
}

describe('quiz results', () => {
  it('reads the score as a fraction of what was marked, not of everything asked', () => {
    seeResults();

    expect(screen.getByRole('heading', { name: /50%/ })).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 marked answers correct/)).toBeInTheDocument();
  });

  it('never counts an unscored written answer as wrong', () => {
    seeResults();

    const rows = screen.getAllByRole('listitem');
    const writtenRow = rows[2];
    expect(within(writtenRow).getByText('Not scored')).toBeInTheDocument();
    expect(within(writtenRow).queryByText('Not correct')).toBeNull();

    expect(
      screen.getByText(/One written answer could not be marked automatically/),
    ).toBeInTheDocument();
    expect(screen.getByText(/count neither for nor against/)).toBeInTheDocument();
  });

  it('shows the right answer only for the ones that were wrong', () => {
    seeResults();

    const rows = screen.getAllByRole('listitem');
    const [correctRow, wrongRow] = rows;

    expect(within(correctRow).queryByText('The answer')).toBeNull();
    expect(within(wrongRow).getByText('The answer')).toBeInTheDocument();
    expect(within(wrongRow).getByText('False')).toBeInTheDocument();
  });

  it('offers a reference answer rather than a verdict for a written question', () => {
    seeResults();

    expect(screen.getByText('A reference answer')).toBeInTheDocument();
    expect(screen.getByText('It merges into a separate buffer.')).toBeInTheDocument();
  });

  it('says nothing was answered rather than leaving the row blank', () => {
    seeResults({
      ...ATTEMPT,
      answers: [
        { ...ATTEMPT.answers[0], selected_option_index: null, is_correct: false },
        ATTEMPT.answers[1],
        ATTEMPT.answers[2],
      ],
    });

    expect(screen.getByText('Nothing')).toBeInTheDocument();
  });

  it('claims no score at all when nothing on the attempt could be marked', () => {
    seeResults({
      ...ATTEMPT,
      score: null,
      correct_count: 0,
      graded_count: 0,
      answers: [ATTEMPT.answers[2]],
      total_questions: 1,
    });

    expect(screen.getByRole('heading', { name: 'Not scored' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /%/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/0 of 0 marked answers correct/)).toBeNull();
  });
});
