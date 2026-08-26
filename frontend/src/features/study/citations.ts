import type { Citation, MaybeCited } from '@/api/types';

export function citedText(value: MaybeCited): string {
  return typeof value === 'string' ? value : value.text;
}

export function citedCitations(value: MaybeCited): Citation[] {
  return typeof value === 'string' ? [] : (value.citations ?? []);
}

export function citationLabel(citation: Citation): string {
  const { page_start: start, page_end: end } = citation;

  if (start == null) {
    return citation.document_label;
  }
  if (end == null || end === start) {
    return `${citation.document_label} · p. ${start}`;
  }
  return `${citation.document_label} · pp. ${start}–${end}`;
}

export function citationsByKey(citations: Citation[]): Map<string, Citation> {
  return new Map(citations.map((citation) => [citation.key, citation]));
}
