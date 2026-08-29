import { FileText, FileCheck2, GraduationCap } from 'lucide-react';
import type { ExamSourceDocument } from '@/api/types';
import {
  documentStatusLabel,
  documentStatusTone,
  materialKindLabel,
} from '@/components/documents/documentLabels';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { isReady } from './examPrerequisites';
import styles from './ExamSourceSelector.module.css';

export interface ExamSourceSelectorProps {
  documents: ExamSourceDocument[];
  selected: ReadonlySet<string>;
  onToggle: (documentId: string) => void;
  onSelectAllReady: () => void;
  disabled?: boolean;
}

/**
 * Which documents Exam Mode is allowed to read.
 *
 * Only the checked ids are ever sent, so a document the student excluded can
 * never reach a prompt. "Use every ready source" fills the selection visibly
 * rather than bypassing it, because a convenience that quietly widens scope is
 * the same defect as ignoring the checkboxes.
 */
export function ExamSourceSelector({
  documents,
  selected,
  onToggle,
  onSelectAllReady,
  disabled,
}: ExamSourceSelectorProps) {
  const ready = documents.filter(isReady);
  const allReadySelected =
    ready.length > 0 && ready.every((document) => selected.has(document.id));

  return (
    <div className={styles.selector}>
      <div className={styles.head}>
        <p className={styles.count}>
          <span className="tabular">{selected.size}</span> of{' '}
          <span className="tabular">{ready.length}</span> ready{' '}
          {ready.length === 1 ? 'source' : 'sources'} selected
        </p>
        {!disabled && ready.length > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onSelectAllReady}
            disabled={allReadySelected}
          >
            Use every ready source
          </Button>
        ) : null}
      </div>

      {documents.length === 0 ? (
        <p className={styles.empty}>This course has no sources yet.</p>
      ) : null}

      <ul className={styles.list}>
        {documents.map((document) => {
          const selectable = isReady(document) && !disabled;
          return (
            <li key={document.id} className={styles.row}>
              <Checkbox
                checked={selected.has(document.id)}
                disabled={!selectable}
                onChange={() => onToggle(document.id)}
                label={
                  <span className={styles.label}>
                    <span className={styles.name}>{document.label}</span>
                    <span className={styles.meta}>
                      {/*
                        The product already names these states. Saying them a
                        second way here would let one document read as "Being
                        read" in Exam Mode and "Processing" in the workspace.
                      */}
                      <Badge tone={documentStatusTone(document.status)}>
                        {documentStatusLabel(document.status)}
                      </Badge>
                      <Badge tone="neutral" icon={<FileText aria-hidden="true" />}>
                        {materialKindLabel(document.material_kind)}
                      </Badge>
                      {document.is_syllabus ? (
                        <Badge tone="accent" icon={<GraduationCap aria-hidden="true" />}>
                          Syllabus
                        </Badge>
                      ) : null}
                      {document.is_past_exam ? (
                        <Badge tone="accent" icon={<FileCheck2 aria-hidden="true" />}>
                          Past exam
                        </Badge>
                      ) : null}
                    </span>
                  </span>
                }
                description={
                  isReady(document)
                    ? undefined
                    : 'Only a source that has finished processing can be analysed.'
                }
              />
            </li>
          );
        })}
      </ul>
    </div>
  );
}
