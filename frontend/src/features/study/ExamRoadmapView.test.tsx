import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExamRoadmap } from '@/api/types';
import { ExamRoadmapView } from './ExamRoadmapView';

const SAMPLE_ROADMAP: ExamRoadmap = {
  version: 1,
  output_type: 'exam_roadmap',
  course_id: 10,
  exam_date: '2026-09-20',
  generated_on: '2026-08-27',
  starts_on: '2026-08-27',
  days_until_exam: 24,
  scheduled_days: 25,
  lead_in_days: 0,
  horizon: 'standard',
  materials_available: true,
  attempts_considered: 1,
  roadmap_version: 2,
  adapted_from_output_id: 8,
  notes: ['Review prerequisite algorithms before beginning sorting.'],
  ranked_topics: [
    {
      topic: 'Binary Search Trees',
      source: 'syllabus',
      syllabus_position: 0,
      importance: 1.0,
      mastery_percentage: 85,
      questions_answered: 5,
      priority: 0.8,
    },
    {
      topic: 'Dynamic Programming',
      source: 'syllabus',
      syllabus_position: 1,
      importance: 1.0,
      mastery_percentage: null,
      questions_answered: 0,
      priority: 0.75,
    },
  ],
  days: [
    {
      day_index: 1,
      date: '2026-08-27',
      kind: 'study',
      is_exam_day: false,
      focus: 'First pass: Binary Search Trees',
      topics: [
        {
          topic: 'Binary Search Trees',
          goal: 'Rebuild insertion and deletion from memory',
          pass_number: 1,
          source: 'syllabus',
          syllabus_position: 0,
          importance: 1.0,
          mastery_percentage: 85,
          questions_answered: 5,
          priority: 0.8,
          material_status: 'resolved',
          materials: [
            {
              document_id: 'doc-1',
              document_label: 'Lecture 4 - Trees',
              page_start: 12,
              page_end: 18,
            },
          ],
          citations: [],
        },
      ],
    },
    {
      day_index: 25,
      date: '2026-09-20',
      kind: 'final_review',
      is_exam_day: true,
      focus: 'Exam day. Final review: Dynamic Programming',
      topics: [
        {
          topic: 'Dynamic Programming',
          goal: 'Last-minute formula and recurrence check',
          pass_number: 2,
          source: 'syllabus',
          syllabus_position: 1,
          importance: 1.0,
          mastery_percentage: null,
          questions_answered: 0,
          priority: 0.75,
          material_status: 'no_match',
          materials: [],
          citations: [],
        },
      ],
    },
  ],
  deferred_topics: [
    {
      topic: 'Heaps and Priority Queues',
      priority: 0.3,
      reason: 'horizon_too_short',
    },
  ],
};

describe('ExamRoadmapView', () => {
  it('renders the roadmap summary, days count, and horizon badges', () => {
    render(<ExamRoadmapView roadmap={SAMPLE_ROADMAP} />);

    expect(screen.getByRole('heading', { level: 3, name: 'Study Schedule' })).toBeInTheDocument();
    expect(screen.getByText('24')).toBeInTheDocument();
    expect(screen.getByText('Days until exam')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
    expect(screen.getByText('Days planned')).toBeInTheDocument();
    expect(screen.getByText('Standard schedule')).toBeInTheDocument();
    expect(screen.getByText('Version 2')).toBeInTheDocument();
    expect(screen.getByText('Adapted from #8')).toBeInTheDocument();
    expect(screen.getByText('Review prerequisite algorithms before beginning sorting.')).toBeInTheDocument();
  });

  it('renders day cards with goals, pass badges, and material citations', () => {
    render(<ExamRoadmapView roadmap={SAMPLE_ROADMAP} />);

    expect(screen.getByText('Day 1')).toBeInTheDocument();
    expect(screen.getByText('First pass: Binary Search Trees')).toBeInTheDocument();
    expect(screen.getByText('Rebuild insertion and deletion from memory')).toBeInTheDocument();
    expect(screen.getByText('Pass 1')).toBeInTheDocument();
    expect(screen.getByText('85% mastery')).toBeInTheDocument();
    expect(screen.getByText(/Lecture 4 - Trees \(pp\. 12–18\)/)).toBeInTheDocument();
  });

  it('renders exam day badge and material gap note', () => {
    render(<ExamRoadmapView roadmap={SAMPLE_ROADMAP} />);

    expect(screen.getByText('Day 25')).toBeInTheDocument();
    expect(screen.getByText('Exam day')).toBeInTheDocument();
    expect(screen.getByText('Final review')).toBeInTheDocument();
    expect(screen.getByText('Unquizzed')).toBeInTheDocument();
    expect(screen.getByText('No matching passages found above relevance floor')).toBeInTheDocument();
  });

  it('renders deferred topics section when present', () => {
    render(<ExamRoadmapView roadmap={SAMPLE_ROADMAP} />);

    expect(screen.getByRole('region', { name: 'Deferred topics' })).toBeInTheDocument();
    expect(screen.getByText('Heaps and Priority Queues')).toBeInTheDocument();
  });
});
