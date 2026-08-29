import { describe, expect, it } from 'vitest';
import type { Citation, MaybeCited } from '@/api/types';
import { citationLabel, citationsByKey, citedCitations, citedText } from './citations';
import { isRenderableStudyGuide } from './storedOutput';

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    key: 'S1',
    document_id: '11111111-1111-1111-1111-111111111111',
    document_label: 'Lecture 4',
    page_start: 12,
    page_end: 12,
    ...overrides,
  };
}

describe('citedText', () => {
  it('reads a legacy plain string unchanged', () => {
    expect(citedText('Trees are acyclic')).toBe('Trees are acyclic');
  });

  it('reads the text of a cited value', () => {
    expect(citedText({ text: 'Trees are acyclic', citations: [] })).toBe('Trees are acyclic');
  });
});

describe('citedCitations', () => {
  it('reports no citations for a legacy plain string', () => {
    expect(citedCitations('Trees are acyclic')).toEqual([]);
  });

  it('reports the citations of a cited value', () => {
    const value = { text: 'Trees are acyclic', citations: [citation()] };

    expect(citedCitations(value)).toEqual([citation()]);
  });

  it('reports no citations when the field is missing', () => {
    // Intentionally bypass the type to cover malformed stored citation data.
    const malformed = { text: 'Trees are acyclic' } as unknown as MaybeCited;

    expect(citedText(malformed)).toBe('Trees are acyclic');
    expect(citedCitations(malformed)).toEqual([]);
  });
});

describe('citationLabel', () => {
  it('names a single page', () => {
    expect(citationLabel(citation())).toBe('Lecture 4 · p. 12');
  });

  it('names a page range with an en dash', () => {
    expect(citationLabel(citation({ page_start: 12, page_end: 14 }))).toBe(
      'Lecture 4 · pp. 12–14',
    );
  });

  it('names the document alone when the passage has no page', () => {
    expect(citationLabel(citation({ page_start: null, page_end: null }))).toBe('Lecture 4');
  });

  it('names a single page when only the start is known', () => {
    expect(citationLabel(citation({ page_end: null }))).toBe('Lecture 4 · p. 12');
  });

  it('names the document alone when only the end is known', () => {
    expect(citationLabel(citation({ page_start: null, page_end: 14 }))).toBe('Lecture 4');
  });
});

describe('citationsByKey', () => {
  it('indexes citations by their key', () => {
    const first = citation();
    const second = citation({ key: 'S2', page_start: 13, page_end: 13 });

    const index = citationsByKey([first, second]);

    expect(index.get('S1')).toEqual(first);
    expect(index.get('S2')).toEqual(second);
    expect(index.get('S9')).toBeUndefined();
  });
});

describe('isRenderableStudyGuide', () => {
  const BASE = {
    title: 'Sorting',
    estimated_study_time: '45 minutes',
    key_points: [],
    important_terms: [],
    common_mistakes: [],
    prerequisites: [],
    learning_objectives: [],
    difficulty: { level: 'Medium', reason: 'because' },
    coverage: { status: 'Partial', estimated_completeness: 60 },
    exam_tips: { lecture_based: [], ai_suggestions: [] },
  };

  it('accepts a guide stored before citations existed', () => {
    expect(isRenderableStudyGuide({ ...BASE, summary: 'A plain string summary' })).toBe(true);
  });

  it('accepts a guide whose summary carries citations', () => {
    expect(
      isRenderableStudyGuide({ ...BASE, summary: { text: 'A cited summary', citations: [] } }),
    ).toBe(true);
  });

  it('rejects a guide whose summary is neither', () => {
    expect(isRenderableStudyGuide({ ...BASE, summary: 42 })).toBe(false);
    expect(isRenderableStudyGuide({ ...BASE, summary: { citations: [] } })).toBe(false);
  });
});
