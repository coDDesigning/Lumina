import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ExamTopicGuideDocument } from '@/api/types';
import { ErrorBoundary } from '@/app/ErrorBoundary';
import { ExamTopicGuide } from './ExamTopicGuide';

function guideFixture(): ExamTopicGuideDocument {
  return {
    version: 1,
    output_type: 'exam_topic_guide',
    topic_key: 'photosynthesis',
    display_label: 'Photosynthesis',
    plan_output_id: 7,
    rank: 1,
    priority_band: 'high',
    title: 'Photosynthesis study guide',
    overview: 'Photosynthesis converts light energy into chemical energy.',
    sections: [
      {
        heading: 'Light-dependent reactions',
        body: 'These reactions occur in the thylakoid membrane.',
        key_points: ['Light excites electrons in chlorophyll.'],
      },
    ],
    key_terms: [
      {
        term: 'Chlorophyll',
        definition: 'The pigment that absorbs light energy.',
        citations: [],
      },
    ],
    common_pitfalls: [],
    what_to_be_able_to_do: ['Explain how light energy drives ATP production.'],
    coverage: { status: 'Complete', estimated_completeness: 100 },
    confidence_notes: '',
  };
}

function renderGuide(guide: ExamTopicGuideDocument, onRegenerate = vi.fn()) {
  const onCaughtError = vi.fn();
  render(
    <ErrorBoundary>
      <ExamTopicGuide guide={guide} onRegenerate={onRegenerate} />
    </ErrorBoundary>,
    { onCaughtError },
  );
  return { onCaughtError, onRegenerate };
}

describe('ExamTopicGuide', () => {
  it('contains a legacy-shaped document in a readable recovery panel', async () => {
    // Intentionally simulates a persisted guide written before the current document contract.
    const legacyGuide = {
      generated_output_id: 31,
      topic_key: 'photosynthesis',
      content: 'Light-dependent reactions occur in the thylakoid.',
    } as unknown as ExamTopicGuideDocument;
    const { onCaughtError, onRegenerate } = renderGuide(legacyGuide);

    expect(screen.getByText(/this guide could not be displayed/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /regenerate guide/i }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(onCaughtError).not.toHaveBeenCalled();
  });

  it.each([
    'sections',
    'key_terms',
    'common_pitfalls',
    'what_to_be_able_to_do',
  ] as const)('contains a guide whose %s collection is missing', (field) => {
    // Intentionally removes one required persisted collection to exercise runtime degradation.
    const malformed = { ...guideFixture(), [field]: undefined } as unknown as ExamTopicGuideDocument;
    const { onCaughtError } = renderGuide(malformed);

    expect(screen.getByText(/this guide could not be displayed/i)).toBeInTheDocument();
    expect(onCaughtError).not.toHaveBeenCalled();
  });

  it('contains a guide whose scalar fields are incomplete', () => {
    // Intentionally removes a required persisted scalar to exercise runtime degradation.
    const malformed = { ...guideFixture(), title: undefined } as unknown as ExamTopicGuideDocument;
    const { onCaughtError } = renderGuide(malformed);

    expect(screen.getByText(/this guide could not be displayed/i)).toBeInTheDocument();
    expect(onCaughtError).not.toHaveBeenCalled();
  });
});
