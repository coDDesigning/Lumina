import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { QuizQuestionView } from '@/api/types';
import { MAX_ANSWER_TEXT_CHARS, OPEN_ENDED_ROWS, SHORT_ANSWER_ROWS } from './answerDraft';
import type { AnswerDraft } from './answerDraft';
import { QuizAnswerField } from './QuizAnswerField';

const EMPTY: AnswerDraft = { optionIndex: null, text: '' };

function question(overrides: Partial<QuizQuestionView>): QuizQuestionView {
  return {
    question_id: 1,
    question_number: 1,
    question_type: 'multiple_choice',
    difficulty: 'medium',
    topic: 'Sorting',
    question: 'Which sort is stable?',
    options: ['Quicksort', 'Merge sort', 'Heapsort'],
    correct_option_index: 1,
    correct_answer: { type: 'multiple_choice', option_index: 1 },
    explanation: 'Merge sort preserves the order of equal keys.',
    ...overrides,
  };
}

function renderField(view: QuizQuestionView, draft: AnswerDraft = EMPTY) {
  const onChange = vi.fn();
  render(<QuizAnswerField question={view} draft={draft} onChange={onChange} />);
  return { onChange, person: userEvent.setup() };
}

const SHORT_ANSWER = question({
  question_type: 'short_answer',
  question: 'Name a stable sort.',
  options: null,
  correct_option_index: null,
  correct_answer: { type: 'short_answer', text: 'Merge sort', accepted_answers: ['Merge sort'] },
});

const OPEN_ENDED = question({
  question_type: 'open_ended',
  question: 'Explain why merge sort needs extra space.',
  options: null,
  correct_option_index: null,
  correct_answer: { type: 'open_ended', reference_answer: 'It buffers.' },
});

describe('a question with options', () => {
  it('is one named radio group, not a row of buttons', () => {
    renderField(question({}));

    const radios = screen.getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(new Set(radios.map((radio) => radio.getAttribute('name'))).size).toBe(1);
  });

  it('reports the choice by its position, not its text', async () => {
    const { onChange, person } = renderField(question({}));

    await person.click(screen.getByRole('radio', { name: /Merge sort/ }));

    expect(onChange).toHaveBeenCalledWith({ optionIndex: 1, text: '' });
  });

  it('shows which option is already chosen', () => {
    renderField(question({}), { optionIndex: 2, text: '' });

    expect(screen.getByRole('radio', { name: /Heapsort/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /Quicksort/ })).not.toBeChecked();
  });
});

describe('a true or false question', () => {
  const TRUE_FALSE = question({
    question_type: 'true_false',
    question: 'Quicksort is always O(n log n).',
    options: ['True', 'False'],
    correct_answer: { type: 'true_false', value: false },
  });

  it('offers exactly two choices', () => {
    renderField(TRUE_FALSE);

    expect(screen.getAllByRole('radio')).toHaveLength(2);
    expect(screen.getByRole('radio', { name: /True/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /False/ })).toBeInTheDocument();
  });

  it('falls back to true and false when the question carried no labels', () => {
    renderField(question({ ...TRUE_FALSE, options: null }));

    expect(screen.getByRole('radio', { name: /True/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /False/ })).toBeInTheDocument();
  });
});

describe('a written question', () => {
  it('gives a short answer a small box', () => {
    renderField(SHORT_ANSWER);

    const box = screen.getByRole('textbox', { name: /your answer/i });
    expect(box).toHaveAttribute('rows', String(SHORT_ANSWER_ROWS));
    expect(box).toHaveAttribute('maxlength', String(MAX_ANSWER_TEXT_CHARS));
    expect(screen.queryByRole('radio')).toBeNull();
  });

  it('gives a written answer a large box and warns it may come back unscored', () => {
    renderField(OPEN_ENDED);

    const box = screen.getByRole('textbox', { name: /your answer/i });
    expect(box).toHaveAttribute('rows', String(OPEN_ENDED_ROWS));
    expect(screen.getByText(/may come back unscored/i)).toBeInTheDocument();
  });

  it('says nothing about scoring for a short answer, which is matched not marked', () => {
    renderField(SHORT_ANSWER);

    expect(screen.queryByText(/may come back unscored/i)).toBeNull();
  });

  it('reports what was typed', async () => {
    const { onChange, person } = renderField(SHORT_ANSWER);

    await person.type(screen.getByRole('textbox', { name: /your answer/i }), 'M');

    expect(onChange).toHaveBeenCalledWith({ optionIndex: null, text: 'M' });
  });
});

describe('a written question that arrived carrying options', () => {
  const CONTAMINATED = question({
    question_type: 'short_answer',
    question: 'Name a stable sort.',
    options: ['Quicksort', 'Merge sort'],
    correct_option_index: null,
    correct_answer: { type: 'short_answer', text: 'Merge sort', accepted_answers: ['Merge sort'] },
  });

  it('is still answered in writing, because that is how the answer is sent', () => {
    renderField(CONTAMINATED);

    expect(screen.getByRole('textbox', { name: /your answer/i })).toBeInTheDocument();
    expect(screen.queryByRole('radio')).toBeNull();
  });
});
