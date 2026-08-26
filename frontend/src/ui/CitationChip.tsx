import { citationLabel } from '@/features/study/citations';
import { cx } from '@/lib/cx';
import type { Citation } from '@/api/types';
import { Badge } from './Badge';
import styles from './CitationChip.module.css';

export interface CitationChipProps {
  citation: Citation;
  className?: string;
}

export function CitationChip({ citation, className }: CitationChipProps) {
  return (
    <Badge tone="neutral" className={cx(styles.chip, className)}>
      {citationLabel(citation)}
    </Badge>
  );
}

export interface CitationListProps {
  citations: Citation[] | undefined;
  className?: string;
}

export function CitationList({ citations, className }: CitationListProps) {
  if (!citations || citations.length === 0) {
    return null;
  }

  return (
    <span className={cx(styles.list, className)}>
      <span className="visually-hidden">Sources: </span>
      {citations.map((citation) => (
        <CitationChip citation={citation} key={citation.key} />
      ))}
    </span>
  );
}
