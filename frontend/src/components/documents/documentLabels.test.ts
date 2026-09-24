import { describe, expect, it } from 'vitest';
import type { DocumentResponse, VisualAnalysisSummary } from '@/api/types';
import {
  displayFileName,
  isDescribingVisuals,
  pageList,
  visualAnalysisDetail,
  visualAnalysisStatusLabel,
} from './documentLabels';

function summary(overrides: Partial<VisualAnalysisSummary> = {}): VisualAnalysisSummary {
  return {
    total: 12,
    described: 0,
    pending: 0,
    failed: 0,
    failure_reason: null,
    failed_page_numbers: [],
    crowded_pages: 0,
    crowded_page_numbers: [],
    stopped_error_code: null,
    ...overrides,
  };
}

describe('visualAnalysisDetail', () => {
  it.each([
    [
      'progress while figures are described',
      'pending',
      summary({ described: 4, pending: 8 }),
      { lines: ['4 of 12 figures described'], progress: { value: 4, max: 12 } },
    ],
    [
      'an early failure beside the progress',
      'pending',
      summary({ described: 4, pending: 7, failed: 1, failure_reason: 'VISUAL_SERVICE_TEMPORARY' }),
      {
        lines: [
          '4 of 12 figures described',
          "1 couldn't be described: the vision service didn't respond in time",
        ],
        progress: { value: 4, max: 12 },
      },
    ],
    [
      'why describing stopped, without a progress bar',
      'pending',
      summary({ described: 4, pending: 8, stopped_error_code: 'image_understanding_failed' }),
      {
        lines: ['4 of 12 figures described', 'Stopped: the vision service kept failing'],
        progress: null,
      },
    ],
    [
      'the count and commonest reason for a partial result',
      'partial',
      summary({ described: 9, failed: 3, failure_reason: 'VISUAL_ANALYSIS_FAILED' }),
      {
        lines: [
          '9 of 12 figures described',
          "3 couldn't be described: the vision model's answer couldn't be used",
        ],
        progress: null,
      },
    ],
    [
      'crowded pages when nothing failed',
      'partial',
      summary({ total: 5, described: 5, crowded_pages: 2 }),
      {
        lines: ['5 of 5 figures described', '2 pages had too many images to pick figures from'],
        progress: null,
      },
    ],
    [
      'singular nouns for one figure and one page',
      'partial',
      summary({ total: 1, described: 1, crowded_pages: 1 }),
      {
        lines: ['1 of 1 figure described', '1 page had too many images to pick figures from'],
        progress: null,
      },
    ],
    [
      'the page behind a single failure',
      'partial',
      summary({ described: 11, failed: 1, failure_reason: 'VISUAL_ANALYSIS_FAILED', failed_page_numbers: [2] }),
      {
        lines: [
          '11 of 12 figures described',
          "1 couldn't be described (page 2): the vision model's answer couldn't be used",
        ],
        progress: null,
      },
    ],
    [
      'the pages behind a crowded result',
      'partial',
      summary({ total: 5, described: 5, crowded_pages: 2, crowded_page_numbers: [3, 9] }),
      {
        lines: [
          '5 of 5 figures described',
          '2 pages had too many images to pick figures from (pages 3 and 9)',
        ],
        progress: null,
      },
    ],
    [
      'a long page list, truncated',
      'partial',
      summary({
        described: 3,
        failed: 9,
        failure_reason: 'VISUAL_ANALYSIS_FAILED',
        failed_page_numbers: [1, 2, 3, 4, 5, 6, 7, 8, 9],
      }),
      {
        lines: [
          '3 of 12 figures described',
          "9 couldn't be described (pages 1, 2, 3, 4, 5, 6 and 3 more): the vision model's answer couldn't be used",
        ],
        progress: null,
      },
    ],
    [
      'every figure failing',
      'failed',
      summary({ failed: 12, failure_reason: 'VISUAL_SERVICE_TEMPORARY' }),
      {
        lines: [
          '0 of 12 figures described',
          "12 couldn't be described: the vision service didn't respond in time",
        ],
        progress: null,
      },
    ],
    [
      'a generic reason for a code it does not know',
      'failed',
      summary({ total: 2, failed: 2, failure_reason: 'SOMETHING_NEW' }),
      {
        lines: ['0 of 2 figures described', "2 couldn't be described: something went wrong"],
        progress: null,
      },
    ],
    [
      'that no vision model is set up',
      'not_configured',
      null,
      {
        lines: [
          "No vision model is set up on this server, so figures aren't described. The text is still usable.",
        ],
        progress: null,
      },
    ],
    [
      'background work without numbers when the summary is missing',
      'pending',
      null,
      { lines: ['Figures are being described in the background.'], progress: null },
    ],
    [
      'a partial result without numbers when the summary is missing',
      'partial',
      null,
      { lines: ["Some figures couldn't be described. The text is still usable."], progress: null },
    ],
    [
      'a failure without numbers when the summary is missing',
      'failed',
      null,
      { lines: ['No figures could be described. The text is still usable.'], progress: null },
    ],
  ] as const)('explains %s', (_case, status, detailSummary, expected) => {
    expect(visualAnalysisDetail(status, detailSummary)).toEqual(expected);
  });

  it.each([
    ['completed', summary({ described: 12 })],
    ['not_applicable', null],
    [undefined, null],
  ] as const)('has nothing to explain for %s', (status, detailSummary) => {
    expect(visualAnalysisDetail(status, detailSummary)).toBeNull();
  });
});

describe('pageList', () => {
  it.each([
    ['no pages', [], ''],
    ['a single page', [3], 'page 3'],
    ['two pages', [3, 7], 'pages 3 and 7'],
    ['three pages', [3, 7, 12], 'pages 3, 7 and 12'],
    ['exactly six pages', [1, 2, 3, 4, 5, 6], 'pages 1, 2, 3, 4, 5 and 6'],
    ['seven pages, truncated to six plus a count', [1, 2, 3, 4, 5, 6, 7], 'pages 1, 2, 3, 4, 5, 6 and 1 more'],
    [
      'nine pages, truncated to six plus a count',
      [1, 2, 3, 4, 5, 6, 7, 8, 9],
      'pages 1, 2, 3, 4, 5, 6 and 3 more',
    ],
  ] as const)('formats %s', (_case, numbers, expected) => {
    expect(pageList(numbers)).toBe(expected);
  });
});

describe('visualAnalysisStatusLabel', () => {
  it('names describing that gave up instead of calling it in progress', () => {
    expect(
      visualAnalysisStatusLabel('pending', summary({ stopped_error_code: 'image_understanding_failed' })),
    ).toBe('Visual analysis stopped');
    expect(visualAnalysisStatusLabel('pending', summary({ pending: 12 }))).toBe('Analyzing visuals');
  });
});

describe('isDescribingVisuals', () => {
  function document(overrides: Partial<DocumentResponse>): DocumentResponse {
    return {
      id: 'doc-1',
      original_file_name: 'lecture.pdf',
      file_type: 'pdf',
      mime_type: 'application/pdf',
      material_kind: 'unspecified',
      file_size: 1024,
      course_id: 1,
      status: 'ready',
      visual_analysis_status: 'pending',
      visual_analysis: summary({ pending: 12 }),
      created_at: '2026-08-19T10:00:00Z',
      updated_at: '2026-08-19T10:00:00Z',
      ...overrides,
    };
  }

  it.each([
    [true, 'a ready document whose figures are pending', {}],
    [
      false,
      'a document whose describing stopped',
      { visual_analysis: summary({ pending: 12, stopped_error_code: 'image_understanding_failed' }) },
    ],
    [false, 'a document still being read', { status: 'processing' }],
    [false, 'a document with a settled visual result', { visual_analysis_status: 'partial' }],
  ] as const)('answers %s for %s', (expected, _case, overrides) => {
    expect(isDescribingVisuals(document(overrides))).toBe(expected);
  });
});

describe('displayFileName', () => {
  it('leaves an ordinary filename untouched', () => {
    expect(displayFileName('week-3-lecture.pdf')).toBe('week-3-lecture.pdf');
  });

  it('strips path separators and .. sequences so it reads as a name, not a path', () => {
    const shown = displayFileName('../../../../../../../../tmp/traversal_probe_x.pdf');
    expect(shown).not.toContain('/');
    expect(shown).not.toContain('\\');
    expect(shown).not.toContain('..');
    expect(shown).toBe('tmp traversal_probe_x.pdf');
  });

  it('replaces control characters with a space', () => {
    expect(displayFileName(`a${String.fromCharCode(1)}bc.txt`)).toBe('a bc.txt');
  });

  it('caps the length with an ellipsis', () => {
    const long = `${'a'.repeat(200)}.pdf`;
    const shown = displayFileName(long, 20);
    expect(shown).toHaveLength(20);
    expect(shown.endsWith('…')).toBe(true);
  });

  it('falls back to a placeholder when nothing printable remains', () => {
    expect(displayFileName('///')).toBe('file');
  });
});
