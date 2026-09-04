import { describe, expect, it } from 'vitest';
import type { QuizAttemptResponse, QuizQuestionView } from '@/api/types';
import { describeCorrectAnswer, describeSubmittedAnswer, tallyAttempt } from './quizScoring';

function attempt(overrides: Partial<QuizAttemptResponse>): QuizAttemptResponse {
  return {
    attempt_id: 1,
    quiz_id: 1,
    score: 0 as number | null,
    correct_count: 0,
    graded_count: 0,
    total_questions: 0,
    time_spent_seconds: null,
    created_at: '2026-08-23T10:00:00Z',
    quiz_purpose: null,
    timed: false,
    expired: false,
    answers: [],
    ...overrides,
  };
}

describe('tallyAttempt', () => {
  it('never counts an ungraded answer as wrong', () => {
    const tally = tallyAttempt(
      attempt({ total_questions: 5, graded_count: 3, correct_count: 3, score: 1 }),
    );

    expect(tally.correct).toBe(3);
    expect(tally.incorrect).toBe(0);
    expect(tally.ungraded).toBe(2);
  });

  it('scores against what was graded, not against everything asked', () => {
    const tally = tallyAttempt(
      attempt({ total_questions: 10, graded_count: 8, correct_count: 6, score: 0.75 }),
    );

    expect(tally.scorePercent).toBe(75);
    expect(tally.correct + tally.incorrect).toBe(tally.graded);
    expect(tally.graded + tally.ungraded).toBe(tally.total);
  });

  it('reads score as a fraction, not a percentage', () => {
    expect(tallyAttempt(attempt({ score: 0.5, total_questions: 2, graded_count: 2 })).scorePercent)
      .toBe(50);
    expect(tallyAttempt(attempt({ score: 1, total_questions: 2, graded_count: 2 })).scorePercent)
      .toBe(100);
  });

  it('reports no score when nothing on the attempt could be graded', () => {
    const tally = tallyAttempt(
      attempt({ total_questions: 2, graded_count: 0, correct_count: 0, score: null }),
    );

    expect(tally.scorePercent).toBeNull();
    expect(tally.graded).toBe(0);
    expect(tally.ungraded).toBe(2);
  });

  it('reports no score when the server sends a null score', () => {
    expect(
      tallyAttempt(attempt({ total_questions: 2, graded_count: 2, score: null })).scorePercent,
    ).toBeNull();
  });

  it('holds the parts together when the server sends something impossible', () => {
    const tally = tallyAttempt(
      attempt({ total_questions: 4, graded_count: 9, correct_count: 12, score: 1 }),
    );

    expect(tally.graded).toBe(4);
    expect(tally.correct).toBe(4);
    expect(tally.incorrect).toBe(0);
    expect(tally.ungraded).toBe(0);
  });
});

const OPTION_QUESTION: QuizQuestionView = {
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

describe('describeSubmittedAnswer', () => {
  it('reports nothing rather than inventing an answer that was never given', () => {
    const result = describeSubmittedAnswer(OPTION_QUESTION, {
      question_id: 1,
      question_type: 'multiple_choice',
      selected_option_index: null,
      text_response: null,
      correct_option_index: 1,
      correct_answer: null,
      is_correct: false,
      score: 0,
      feedback: null,
    });

    expect(result).toBeNull();
  });

  it('reads the chosen option back as its text', () => {
    const result = describeSubmittedAnswer(OPTION_QUESTION, {
      question_id: 1,
      question_type: 'multiple_choice',
      selected_option_index: 0,
      text_response: null,
      correct_option_index: 1,
      correct_answer: null,
      is_correct: false,
      score: 0,
      feedback: null,
    });

    expect(result).toBe('Quicksort');
  });
});

describe('describeCorrectAnswer', () => {
  it('reads a multiple choice answer as its option text', () => {
    expect(describeCorrectAnswer(OPTION_QUESTION)).toBe('Merge sort');
  });

  it('reads a true or false answer as a word', () => {
    expect(
      describeCorrectAnswer({
        ...OPTION_QUESTION,
        question_type: 'true_false',
        options: ['True', 'False'],
        correct_answer: { type: 'true_false', value: false },
      }),
    ).toBe('False');
  });

  it('reads an open ended answer as its reference answer', () => {
    expect(
      describeCorrectAnswer({
        ...OPTION_QUESTION,
        question_type: 'open_ended',
        options: null,
        correct_option_index: null,
        correct_answer: { type: 'open_ended', reference_answer: 'Because it never swaps equals.' },
      }),
    ).toBe('Because it never swaps equals.');
  });

  it('falls back to the option index when no structured answer came back', () => {
    expect(
      describeCorrectAnswer({ ...OPTION_QUESTION, correct_answer: null }),
    ).toBe('Merge sort');
  });
});
