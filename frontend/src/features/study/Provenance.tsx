import type { RetrievedContext } from '@/api/types';
import { cx } from '@/lib/cx';
import { provenanceParts } from './provenanceParts';
import styles from './Provenance.module.css';

export interface ProvenanceProps {
  context: RetrievedContext;
  className?: string;
}

export function Provenance({ context, className }: ProvenanceProps) {
  return <p className={cx(styles.provenance, className)}>{provenanceParts(context).join(' · ')}</p>;
}
