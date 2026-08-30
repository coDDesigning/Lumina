import { BookOpen } from 'lucide-react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import type { GeneratedOutputDetail } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { ErrorState } from '@/ui/ErrorState';
import { Spinner } from '@/ui/Spinner';
import { StudyGuide } from './StudyGuide';
import { StudyGuideExportActions } from './studyGuideExport';
import { asExportableStudyGuide, studyGuideContext } from './storedOutput';
import styles from './StudyGuideViewerModal.module.css';

export interface StudyGuideViewerModalProps {
  courseId: number;
  courseName: string;
  outputId: number;
  onClose: () => void;
}

export function StudyGuideViewerModal({
  courseId,
  courseName,
  outputId,
  onClose,
}: StudyGuideViewerModalProps) {
  const detail = useQuery<GeneratedOutputDetail>({
    key: queryKeys.courseOutput(courseId, outputId),
    fetcher: ({ signal }) => generatedOutputsAPI.get(courseId, outputId, { signal }),
    fallbackMessage: 'This study guide could not be opened.',
    staleTime: 5 * 60_000,
  });

  const output = detail.data ?? null;
  const exportable = output ? asExportableStudyGuide(output) : null;

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Study guide"
      description={courseName}
      mark={<BookOpen aria-hidden="true" />}
      footer={
        <>
          <Button onClick={onClose}>Close</Button>
          {exportable ? (
            <div className={styles.footerRight}>
              <StudyGuideExportActions guide={exportable} courseName={courseName} />
            </div>
          ) : null}
        </>
      }
    >
      {detail.status === 'error' ? (
        <ErrorState onRetry={() => void detail.refetch()}>
          {detail.error?.message ?? 'This study guide could not be opened.'}
        </ErrorState>
      ) : null}

      {detail.status !== 'error' && !output ? (
        <div className={styles.loading}>
          <Spinner label="Opening your study guide" />
        </div>
      ) : null}

      {exportable ? (
        <StudyGuide guide={exportable.study_guide} context={studyGuideContext(output!)} />
      ) : null}

      {output && !exportable ? (
        <p className={styles.unsupported}>
          This result was saved in a shape this version no longer recognises.
        </p>
      ) : null}
    </Dialog>
  );
}
