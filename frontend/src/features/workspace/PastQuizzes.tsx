import { queryKeys } from '@/api/queryKeys';
import { quizAPI } from '@/api/quiz';
import type { QuizSummary } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';
import { relativeDay } from '@/lib/relativeDay';
import { ErrorState } from '@/ui/ErrorState';
import { LinkButton } from '@/ui/LinkButton';
import { Skeleton } from '@/ui/Skeleton';
import styles from './PastQuizzes.module.css';

export interface PastQuizzesProps {
  courseId: number;
  workspaceId: string;
}

export function PastQuizzes({ courseId, workspaceId }: PastQuizzesProps) {
  const query = useQuery<QuizSummary[]>({
    key: queryKeys.courseQuizzes(courseId),
    fetcher: ({ signal }) => quizAPI.list(courseId, { signal }),
    fallbackMessage: 'Your saved quizzes could not be loaded.',
  });

  const quizzes = query.data ?? [];
  const isLoading = query.status === 'pending' || query.status === 'idle';

  if (isLoading) {
    return (
      <section className={styles.section} aria-labelledby="past-quizzes-heading" aria-busy="true">
        <h2 id="past-quizzes-heading" className={styles.heading}>
          Quizzes you can take again
        </h2>
        <p className={styles.note}>
          Taking one again costs nothing — the questions are already written.
        </p>
        <div className={styles.loadingGroup} role="status" aria-label="Loading saved quizzes">
          <Skeleton variant="block" height="2.5rem" />
          <Skeleton variant="block" height="2.5rem" />
        </div>
      </section>
    );
  }

  if (query.status === 'error') {
    return (
      <section className={styles.section} aria-labelledby="past-quizzes-heading">
        <h2 id="past-quizzes-heading" className={styles.heading}>
          Quizzes you can take again
        </h2>
        <ErrorState
          title="Your saved quizzes could not be loaded"
          onRetry={() => {
            void query.refetch();
          }}
        >
          {query.error?.message}
        </ErrorState>
      </section>
    );
  }

  if (quizzes.length === 0) {
    return (
      <section className={styles.section} aria-labelledby="past-quizzes-heading">
        <h2 id="past-quizzes-heading" className={styles.heading}>
          Quizzes you can take again
        </h2>
        <p className={styles.note}>
          Taking one again costs nothing — the questions are already written.
        </p>
        <p className={styles.empty}>
          No quizzes saved yet. Generate a practice quiz to see it here.
        </p>
      </section>
    );
  }

  return (
    <section className={styles.section} aria-labelledby="past-quizzes-heading">
      <h2 id="past-quizzes-heading" className={styles.heading}>
        Quizzes you can take again
      </h2>
      <p className={styles.note}>
        Taking one again costs nothing — the questions are already written.
      </p>
      <ul className={styles.list}>
        {quizzes.map((quiz) => (
          <li key={quiz.quiz_id} className={styles.row}>
            <span className={styles.title}>{quiz.title}</span>
            <span className={styles.meta}>
              <span className="tabular">{quiz.question_count}</span>{' '}
              {quiz.question_count === 1 ? 'question' : 'questions'} ·{' '}
              {relativeDay(quiz.created_at)}
              {quiz.best_score != null && quiz.last_score != null ? (
                <>
                  {' '}· Best: <span className="tabular">{Math.round(quiz.best_score * 100)}%</span> · Last: <span className="tabular">{Math.round(quiz.last_score * 100)}%</span>
                </>
              ) : (
                <> · Not attempted yet</>
              )}
            </span>
            <LinkButton size="sm" to={`/courses/${workspaceId}/practice/${quiz.quiz_id}`}>
              Take it again
            </LinkButton>
          </li>
        ))}
      </ul>
    </section>
  );
}
