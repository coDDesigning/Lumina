import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { QuizQuestionView, QuizView } from '@/api/types';
import { StoredQuiz } from './StoredQuiz';

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

  it('marks which option was the right one', () => {
    renderQuiz(quiz());

    const correct = screen.getByText('Merge sort').closest('li') as HTMLElement;
    expect(within(correct).getByText('Correct')).toBeInTheDocument();

    const wrong = screen.getByText('Quicksort').closest('li') as HTMLElement;
    expect(within(wrong).queryByText('Correct')).toBeNull();
  });

  it('calls a written answer a reference rather than the accepted one', () => {
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

    expect(screen.getByText('Reference answer')).toBeInTheDocument();
    expect(screen.getByText('It buffers.')).toBeInTheDocument();
    expect(screen.queryByText('Accepted answer')).toBeNull();
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

  it('names the document and pages a question came from', () => {
    renderQuiz(quiz({ questions: [question({ citations: [CITATION] })] }));

    expect(screen.getByText('Lecture 4 · pp. 12–14')).toBeInTheDocument();
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
