import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { RetrievedContext, StudyGuideResponse } from '@/api/types';
import { StudyGuide } from './StudyGuide';

const GUIDE: StudyGuideResponse = {
  title: 'Sorting Algorithms',
  summary: 'How comparison sorts trade time against space.',
  key_points: ['Merge sort is stable', 'Quicksort is in place'],
  important_terms: [{ term: 'Stability', definition: 'Equal keys keep their order.' }],
  common_mistakes: [
    { mistake: 'Calling quicksort O(n log n) in the worst case', correction: 'It is O(n squared).' },
  ],
  exam_tips: {
    lecture_based: ['The lecturer stressed the recurrence relation'],
    ai_suggestions: ['Practise tracing a partition by hand'],
  },
  difficulty: { level: 'Medium', reason: 'The recurrences take some getting used to.' },
  estimated_study_time: '45 minutes',
  prerequisites: ['Big-O notation'],
  learning_objectives: ['Choose a sort for a given constraint'],
  coverage: { status: 'Partial', estimated_completeness: 60 },
  confidence_notes: 'The material said little about external sorting.',
};

const CONTEXT: RetrievedContext = {
  chunks_used: 4,
  chunks_available: 20,
  retrieval_narrowed: true,
  context_truncated: true,
  lowest_similarity: 0.41,
  highest_similarity: 0.88,
};

describe('StudyGuide', () => {
  it('renders every field the guide contract carries', () => {
    render(<StudyGuide guide={GUIDE} />);

    expect(screen.getByRole('heading', { name: 'Sorting Algorithms' })).toBeInTheDocument();
    expect(screen.getByText(GUIDE.summary)).toBeInTheDocument();
    expect(screen.getByText('Medium')).toBeInTheDocument();
    expect(screen.getByText(GUIDE.difficulty.reason)).toBeInTheDocument();
    expect(screen.getByText('45 minutes')).toBeInTheDocument();
    expect(screen.getByText(/Partial · 60% of the material/)).toBeInTheDocument();
    expect(screen.getByText('Choose a sort for a given constraint')).toBeInTheDocument();
    expect(screen.getByText('Merge sort is stable')).toBeInTheDocument();
    expect(screen.getByText('Stability')).toBeInTheDocument();
    expect(screen.getByText('Equal keys keep their order.')).toBeInTheDocument();
    expect(screen.getByText(/Calling quicksort O\(n log n\)/)).toBeInTheDocument();
    expect(screen.getByText('It is O(n squared).')).toBeInTheDocument();
    expect(screen.getByText(/stressed the recurrence relation/)).toBeInTheDocument();
    expect(screen.getByText(/Practise tracing a partition/)).toBeInTheDocument();
    expect(screen.getByText('Big-O notation')).toBeInTheDocument();
    expect(screen.getByText(GUIDE.confidence_notes)).toBeInTheDocument();
  });

  it('separates what the material said from what the model suggested', () => {
    render(<StudyGuide guide={GUIDE} />);

    expect(screen.getByRole('heading', { name: 'Said in your material' })).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Suggested, not from your material' }),
    ).toBeInTheDocument();
  });

  it('reports narrowing and truncation as two different things', () => {
    render(<StudyGuide guide={GUIDE} context={CONTEXT} />);

    const narrowed = screen.getByText(/Built from the passages closest to your topic/);
    const truncated = screen.getByText(/Some selected passages did not fit/);

    expect(narrowed).toBeInTheDocument();
    expect(truncated).toBeInTheDocument();
    expect(narrowed).not.toBe(truncated);
    expect(screen.getByText(/Of 20 passages in this course, the 4 most/)).toBeInTheDocument();
  });

  it('says nothing about retrieval when nothing was narrowed or dropped', () => {
    render(
      <StudyGuide
        guide={GUIDE}
        context={{ ...CONTEXT, retrieval_narrowed: false, context_truncated: false }}
      />,
    );

    expect(screen.queryByText(/passages closest to your topic/)).toBeNull();
    expect(screen.queryByText(/did not fit/)).toBeNull();
  });

  it('leaves out the sections the guide has no content for', () => {
    render(
      <StudyGuide
        guide={{
          ...GUIDE,
          prerequisites: [],
          common_mistakes: [],
          confidence_notes: '',
          exam_tips: { lecture_based: [], ai_suggestions: [] },
        }}
      />,
    );

    expect(screen.queryByRole('heading', { name: 'Assumed knowledge' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Where people go wrong' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Going into the exam' })).toBeNull();
    expect(screen.queryByRole('heading', { name: /unsure about/ })).toBeNull();
    expect(screen.getByRole('heading', { name: 'Key points' })).toBeInTheDocument();
  });
});
