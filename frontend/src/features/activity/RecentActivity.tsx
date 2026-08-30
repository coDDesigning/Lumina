import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { BookOpen, History, Layers3, Sparkles, Target } from 'lucide-react';
import type { ActivityItem } from '@/api/types';
import { cx } from '@/lib/cx';
import { relativeDay } from '@/lib/relativeDay';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { Skeleton } from '@/ui/Skeleton';
import { activityHref } from './activityHref';
import { useRecentActivity } from './useRecentActivity';
import styles from './RecentActivity.module.css';

export interface RecentActivityProps {
  limit?: number;
  heading?: string;
  headingLevel?: 'h1' | 'h2' | 'h3';
  footer?: ReactNode;
}

const ACTION_LABELS: Record<string, string> = {
  study_guide: 'Study guide',
  quiz: 'Practice quiz',
  flashcards: 'Flashcards',
  quiz_attempt: 'Quiz attempt',
  reverse_quiz: 'Reverse quiz',
  exam_topic_analysis: 'Exam source analysis',
  exam_plan: 'Exam plan',
};

function actionLabel(item: ActivityItem): string {
  return ACTION_LABELS[item.action_type] ?? item.action_type.replace(/_/g, ' ');
}

function ActionIcon({ item }: { item: ActivityItem }) {
  if (item.kind === 'attempt' || item.action_type === 'quiz') {
    return <Target aria-hidden="true" />;
  }
  if (item.action_type === 'study_guide') {
    return <Sparkles aria-hidden="true" />;
  }
  if (item.action_type === 'flashcards') {
    return <Layers3 aria-hidden="true" />;
  }
  return <BookOpen aria-hidden="true" />;
}

export function RecentActivity({
  limit,
  heading = 'Recent activity',
  headingLevel = 'h2',
  footer,
}: RecentActivityProps) {
  const { items, isLoading, error, reload } = useRecentActivity(limit);
  const Heading = headingLevel;

  return (
    <section className={styles.section} aria-labelledby="recent-activity-heading">
      <Heading id="recent-activity-heading" className={styles.heading}>
        {heading}
      </Heading>

      {error ? (
        <ErrorState title="Your recent activity could not be loaded" onRetry={reload}>
          {error}
        </ErrorState>
      ) : isLoading ? (
        <div className={styles.loading} aria-hidden="true">
          <Skeleton width="70%" />
          <Skeleton width="55%" />
          <Skeleton width="62%" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<History aria-hidden="true" />}
          title="Nothing studied yet"
          description="What you generate and the quizzes you take show up here, newest first."
          headingLevel={headingLevel === 'h1' ? 'h2' : 'h3'}
        />
      ) : (
        <>
          <ol className={styles.list}>
            {items.map((item) => (
              <li
                key={`${item.kind}-${item.attempt_id ?? item.output_id}-${item.occurred_at}`}
                className={styles.row}
              >
                <Link to={activityHref(item)} className={styles.link}>
                  <span className={styles.icon}>
                    <ActionIcon item={item} />
                  </span>
                  <span className={styles.what}>
                    <span className={styles.action}>{actionLabel(item)}</span>
                    <span className={styles.course}>{item.course_title}</span>
                  </span>
                  {item.topic ? <span className={styles.topic}>{item.topic}</span> : null}
                  {item.score !== null ? (
                    <span className={cx(styles.score, 'tabular')}>
                      {Math.round(item.score * 100)}%
                    </span>
                  ) : null}
                  <time className={styles.when} dateTime={item.occurred_at}>
                    {relativeDay(item.occurred_at)}
                  </time>
                </Link>
              </li>
            ))}
          </ol>
          {footer ? <div className={styles.footer}>{footer}</div> : null}
        </>
      )}
    </section>
  );
}
