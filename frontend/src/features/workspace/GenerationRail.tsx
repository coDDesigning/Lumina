import { CheckCircle2, Clock3, X } from 'lucide-react';
import type { GenerationJob } from '@/api/types';
import { relativeDay } from '@/lib/relativeDay';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
import { IconButton } from '@/ui/IconButton';
import { Skeleton } from '@/ui/Skeleton';
import { Spinner } from '@/ui/Spinner';
import styles from './GenerationRail.module.css';

interface GenerationRailProps {
  jobs: GenerationJob[];
  isLoading: boolean;
  error: string | null;
  retryingId: number | null;
  onReload: () => void;
  onRetry: (jobId: number) => void;
  onDismiss: (jobId: number) => void;
  onOpenGuide: (outputId: number) => void;
  onOpenQuiz: (quizId: number) => void;
  onOpenFlashcards?: (outputId: number) => void;
}

const VISIBLE_JOBS = 5;

function labelFor(job: GenerationJob): string {
  if (job.job_type === 'generate_quiz') return 'Practice quiz';
  if (job.job_type === 'generate_flashcard') return 'Flashcards';
  return 'Study guide';
}

function openResult(
  job: GenerationJob,
  onOpenGuide: (outputId: number) => void,
  onOpenQuiz: (quizId: number) => void,
  onOpenFlashcards?: (outputId: number) => void,
): void {
  if (job.quiz_id !== null) {
    onOpenQuiz(job.quiz_id);
  } else if (job.job_type === 'generate_flashcard' && job.generated_output_id !== null && onOpenFlashcards) {
    onOpenFlashcards(job.generated_output_id);
  } else if (job.generated_output_id !== null) {
    onOpenGuide(job.generated_output_id);
  }
}

export function GenerationRail({
  jobs,
  isLoading,
  error,
  retryingId,
  onReload,
  onRetry,
  onDismiss,
  onOpenGuide,
  onOpenQuiz,
  onOpenFlashcards,
}: GenerationRailProps) {
  if (isLoading) {
    return (
      <div className={styles.loading}>
        <Skeleton variant="text" />
        <Skeleton variant="text" />
      </div>
    );
  }

  if (error) {
    return (
      <ErrorState title="Generation status could not be loaded" onRetry={onReload}>
        {error}
      </ErrorState>
    );
  }

  if (jobs.length === 0) return null;

  return (
    <ul className={styles.list} aria-live="polite" aria-label="Background generations">
      {jobs.slice(0, VISIBLE_JOBS).map((job) => {
        const label = labelFor(job);
        if (job.status === 'failed') {
          return (
            <li key={job.id}>
              <ErrorState
                className={styles.failure}
                title={`${label} failed`}
                onRetry={() => onRetry(job.id)}
                retryLabel={retryingId === job.id ? 'Queueing…' : 'Try again'}
                actions={
                  <Button variant="ghost" size="sm" onClick={() => onDismiss(job.id)}>
                    {`Dismiss ${label}`}
                  </Button>
                }
              >
                {job.error_message ?? 'The generation could not be completed.'}
              </ErrorState>
            </li>
          );
        }

        const completed = job.status === 'succeeded';
        const content = (
          <>
            <span className={styles.icon} aria-hidden="true">
              {completed ? (
                <CheckCircle2 />
              ) : job.status === 'queued' ? (
                <Clock3 />
              ) : (
                <Spinner size="sm" />
              )}
            </span>
            <span className={styles.text}>
              <span className={styles.title}>{label}</span>
              <span className={styles.meta}>
                {completed
                  ? `Ready · ${relativeDay(job.finished_at ?? job.created_at)}`
                  : job.status === 'queued'
                    ? 'Queued'
                    : 'Generating in the background'}
              </span>
            </span>
          </>
        );

        return (
          <li key={job.id}>
            {completed ? (
              <div className={styles.row}>
                <button
                  type="button"
                  className={styles.entry}
                  onClick={() => openResult(job, onOpenGuide, onOpenQuiz, onOpenFlashcards)}
                >
                  {content}
                </button>
                <IconButton
                  className={styles.dismiss}
                  size="sm"
                  label={`Dismiss ${label}`}
                  icon={<X aria-hidden="true" />}
                  onClick={() => onDismiss(job.id)}
                />
              </div>
            ) : (
              <div className={styles.entry}>{content}</div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
