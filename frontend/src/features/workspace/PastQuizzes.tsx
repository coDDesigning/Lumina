import { useEffect, useState } from 'react';
import { isAbortError } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import type { QuizSummary } from '@/api/types';
import { LinkButton } from '@/ui/LinkButton';
import { relativeDay } from './relativeDay';
import styles from './PastQuizzes.module.css';

export interface PastQuizzesProps {
  courseId: number;
  workspaceId: string;
}

export function PastQuizzes({ courseId, workspaceId }: PastQuizzesProps) {
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();

    quizAPI
      .list(courseId, { signal: controller.signal })
      .then((rows) => {
        if (!controller.signal.aborted) {
          setQuizzes(rows);
          setIsLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setQuizzes([]);
        setIsLoading(false);
      });

    return () => controller.abort();
  }, [courseId]);

  if (isLoading || quizzes.length === 0) {
    return null;
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
