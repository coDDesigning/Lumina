import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExamSourceDocument } from '@/api/types';
import { ExamPrerequisiteNotices } from './ExamPrerequisiteNotices';
import type { Prerequisite } from './examPrerequisites';

function source(label: string): ExamSourceDocument {
  return {
    id: 'aaaaaaaa-0000-0000-0000-000000000001',
    label,
    material_kind: 'lecture',
    status: 'uploaded',
    is_past_exam: false,
    is_syllabus: false,
  };
}

describe('ExamPrerequisiteNotices', () => {
  it('renders a path-like source label as a plain name in the readiness sentence', () => {
    const warnings: Prerequisite[] = [
      {
        kind: 'sources_processing',
        documents: [source('../../../../../../../../tmp/traversal Probe X')],
      },
    ];

    render(
      <ExamPrerequisiteNotices courseId={1} blockers={[]} warnings={warnings} />,
    );

    const alert = screen.getByText(/not ready yet/);
    expect(alert.textContent).not.toContain('/');
    expect(alert.textContent).not.toContain('..');
    expect(alert.textContent).toContain('tmp traversal Probe X is not ready yet');
  });
});
