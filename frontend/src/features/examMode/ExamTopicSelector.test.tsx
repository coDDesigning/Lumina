import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ExamAnalysisView, ExamTopicCandidateView } from '@/api/types';
import { ExamTopicSelector } from './ExamTopicSelector';

function topic(overrides: Partial<ExamTopicCandidateView> = {}): ExamTopicCandidateView {
  return {
    topic_key: 'photosynthesis',
    display_label: 'Photosynthesis',
    aliases: [],
    in_syllabus: true,
    in_course_topics: false,
    in_past_exams: false,
    in_material: true,
    discovery_confidence: 0.9,
    syllabus_weight_percent: 30,
    syllabus_mention_count: 3,
    past_exam_question_count: 0,
    material_chunk_count: 8,
    citations: [],
    ...overrides,
  } as ExamTopicCandidateView;
}

function analysisFixture(overrides: Partial<ExamAnalysisView> = {}): ExamAnalysisView {
  return {
    generated_output_id: 55,
    created_at: '2026-08-20T10:00:00Z',
    model_used: 'ollama:llama3.1',
    candidate_count: 1,
    past_exam_question_count: 0,
    documents_analysed: ['doc-1'],
    manual_review_recommended: false,
    topics: [topic()],
    selection_carry_over: {
      preselected_topic_keys: [],
      high_priority_topic_keys: [],
      unsupported_topic_keys: [],
      new_topic_keys: [],
      previous_plan_output_id: null,
    },
    confidence_notes: '',
    ...overrides,
  } as unknown as ExamAnalysisView;
}

function renderSelector(props: Partial<React.ComponentProps<typeof ExamTopicSelector>> = {}) {
  const handlers = {
    onToggle: vi.fn(),
    onTogglePriority: vi.fn(),
    onSelectAll: vi.fn(),
  };
  render(
    <ExamTopicSelector
      analysis={analysisFixture()}
      selected={new Set()}
      highPriority={new Set()}
      {...handlers}
      {...props}
    />,
  );
  return handlers;
}

describe('ExamTopicSelector', () => {
  it('counts how many topics are selected', () => {
    renderSelector({ selected: new Set(['photosynthesis']) });

    expect(screen.getByText(/topic.? selected/i).textContent).toMatch(/1\s*of\s*1/);
  });

  it('reports the evidence a topic was actually found in', () => {
    renderSelector();

    expect(screen.getByText(/Syllabus \(3 mentions\)/i)).toBeInTheDocument();
    expect(screen.getByText(/8 passages of material/i)).toBeInTheDocument();
  });

  it('does not claim past-exam evidence a topic does not have', () => {
    renderSelector();

    expect(screen.queryByText(/past-exam questions/i)).not.toBeInTheDocument();
  });

  it('names past-exam evidence when the analysis reports it', () => {
    renderSelector({
      analysis: analysisFixture({
        topics: [topic({ in_past_exams: true, past_exam_question_count: 4 })],
      }),
    });

    expect(screen.getByText(/4 past-exam questions/i)).toBeInTheDocument();
  });

  it('toggles a topic when its checkbox is clicked', async () => {
    const user = userEvent.setup();
    const handlers = renderSelector();

    await user.click(screen.getAllByRole('checkbox')[0]);

    expect(handlers.onToggle).toHaveBeenCalledWith('photosynthesis');
  });

  it('selects every discovered topic on request', async () => {
    const user = userEvent.setup();
    const handlers = renderSelector();

    await user.click(screen.getByRole('button', { name: /select every discovered topic/i }));

    expect(handlers.onSelectAll).toHaveBeenCalled();
  });

  it('cannot select all again once everything is already selected', () => {
    renderSelector({ selected: new Set(['photosynthesis']) });

    expect(screen.getByRole('button', { name: /select every discovered topic/i })).toBeDisabled();
  });

  it('offers priority only for a topic that is in the plan', async () => {
    const user = userEvent.setup();
    const handlers = renderSelector({ selected: new Set(['photosynthesis']) });

    const star = screen.getByRole('button', { name: /mark photosynthesis high priority/i });
    await user.click(star);

    expect(handlers.onTogglePriority).toHaveBeenCalledWith('photosynthesis');
  });

  it('hides the priority control for a topic that is not selected', () => {
    renderSelector();

    expect(
      screen.queryByRole('button', { name: /high priority/i }),
    ).not.toBeInTheDocument();
  });

  it('offers to remove priority from a topic that already has it', () => {
    renderSelector({
      selected: new Set(['photosynthesis']),
      highPriority: new Set(['photosynthesis']),
    });

    expect(
      screen.getByRole('button', { name: /remove high priority from photosynthesis/i }),
    ).toBeInTheDocument();
  });

  it('asks the student to review topics the model read out of the material', () => {
    renderSelector({ analysis: analysisFixture({ manual_review_recommended: true }) });

    expect(screen.getByText(/review these before you plan/i)).toBeInTheDocument();
  });

  it('says plainly that a previously planned topic was not found again', () => {
    renderSelector({
      analysis: analysisFixture({
        selection_carry_over: {
          preselected_topic_keys: [],
          high_priority_topic_keys: [],
          unsupported_topic_keys: ['Respiration'],
          new_topic_keys: [],
          previous_plan_output_id: 4,
        },
      } as unknown as Partial<ExamAnalysisView>),
    });

    expect(screen.getByText(/no longer in this analysis/i)).toBeInTheDocument();
    expect(screen.getByText(/Respiration/)).toBeInTheDocument();
  });

  it('offers no controls at all while disabled', () => {
    renderSelector({ selected: new Set(['photosynthesis']), disabled: true });

    expect(
      screen.queryByRole('button', { name: /select every discovered topic/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /high priority/i })).not.toBeInTheDocument();
  });
});
