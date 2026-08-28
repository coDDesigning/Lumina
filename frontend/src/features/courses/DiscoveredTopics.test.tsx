import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { examModeAPI } from '@/api/examMode';
import type { ExamAnalysisView, ExamTopicCandidateView } from '@/api/types';
import { DiscoveredTopics } from './DiscoveredTopics';

vi.mock('@/api/examMode', () => ({
  examModeAPI: { getAnalysis: vi.fn() },
}));

function candidate(
  label: string,
  overrides: Partial<ExamTopicCandidateView> = {},
): ExamTopicCandidateView {
  return {
    topic_key: label.toLowerCase().replace(/\s+/g, '-'),
    display_label: label,
    aliases: [],
    in_syllabus: false,
    in_course_topics: false,
    in_past_exams: false,
    in_material: true,
    discovery_confidence: 0.8,
    syllabus_weight_percent: null,
    syllabus_mention_count: 0,
    past_exam_question_count: 0,
    material_chunk_count: 4,
    citations: [],
    ...overrides,
  };
}

function analysis(topics: ExamTopicCandidateView[]): ExamAnalysisView {
  return {
    generated_output_id: 5,
    created_at: '2026-05-01T09:00:00Z',
    model_used: null,
    candidate_count: topics.length,
    past_exam_question_count: 0,
    documents_analysed: [],
    manual_review_recommended: true,
    topics,
    selection_carry_over: {
      previous_plan_output_id: null,
      preselected_topic_keys: [],
      high_priority_topic_keys: [],
      new_topic_keys: [],
      unsupported_topic_keys: [],
    },
    coverage: null,
    confidence_notes: '',
  };
}

beforeEach(() => {
  vi.mocked(examModeAPI.getAnalysis).mockResolvedValue(
    analysis([candidate('Hashing'), candidate('Sorting')]),
  );
});

describe('topics Exam Mode found', () => {
  it('offers them without adding anything on its own', async () => {
    // course_topics is read back as ranking evidence, so writing a finding
    // automatically would let Exam Mode score its own output as a declaration.
    const onAdd = vi.fn();
    render(<DiscoveredTopics courseId={1} declared={[]} onAdd={onAdd} />);

    expect(await screen.findByRole('button', { name: 'Hashing' })).toBeInTheDocument();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('adds one when the student picks it', async () => {
    const onAdd = vi.fn();
    render(<DiscoveredTopics courseId={1} declared={[]} onAdd={onAdd} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Hashing' }));

    expect(onAdd).toHaveBeenCalledWith(['Hashing']);
  });

  it('adds every one at once when asked', async () => {
    const onAdd = vi.fn();
    render(<DiscoveredTopics courseId={1} declared={[]} onAdd={onAdd} />);
    const user = userEvent.setup();

    await user.click(await screen.findByRole('button', { name: 'Add all 2' }));

    expect(onAdd).toHaveBeenCalledWith(['Hashing', 'Sorting']);
  });

  it('trusts the backend about what is already declared', async () => {
    // in_course_topics is the backend's own canonical-key match, which sorts
    // and stems tokens. Re-deriving it here would drift from it.
    vi.mocked(examModeAPI.getAnalysis).mockResolvedValue(
      analysis([candidate('Hashing', { in_course_topics: true }), candidate('Sorting')]),
    );
    render(<DiscoveredTopics courseId={1} declared={[]} onAdd={vi.fn()} />);

    expect(await screen.findByRole('button', { name: 'Sorting' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hashing' })).toBeNull();
  });

  it('drops a topic the moment it is in the box, before any rescan', async () => {
    render(<DiscoveredTopics courseId={1} declared={['hashing']} onAdd={vi.fn()} />);

    expect(await screen.findByRole('button', { name: 'Sorting' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hashing' })).toBeNull();
  });

  it('says nothing at all when there is nothing to offer', async () => {
    vi.mocked(examModeAPI.getAnalysis).mockRejectedValue(new Error('no analysis'));
    const { container } = render(
      <DiscoveredTopics courseId={1} declared={[]} onAdd={vi.fn()} />,
    );

    await vi.waitFor(() => expect(examModeAPI.getAnalysis).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it('offers nothing to a support reader', async () => {
    const { container } = render(
      <DiscoveredTopics courseId={1} declared={[]} onAdd={vi.fn()} disabled />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
