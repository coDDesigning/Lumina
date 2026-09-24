import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { History } from 'lucide-react';
import type { ActivityItem } from '@/api/types';
import { outputTypeLabel } from '@/features/study/outputTypes';
import { cx } from '@/lib/cx';
import { CourseChip } from '@/ui/CourseLight';
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
  headingClassName?: string;
  footer?: ReactNode;
  className?: string;
}

interface ActivityDay {
  key: string;
  label: string;
  items: ActivityItem[];
}

const SUBHEADING: Record<'h1' | 'h2' | 'h3', 'h2' | 'h3' | 'h4'> = {
  h1: 'h2',
  h2: 'h3',
  h3: 'h4',
};

const TIME_FORMAT = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' });
const DATE_FORMAT = new Intl.DateTimeFormat('en', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
});
const DATE_WITH_YEAR_FORMAT = new Intl.DateTimeFormat('en', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
});

function actionLabel(item: ActivityItem): string {
  return item.action_type === 'quiz_attempt' ? 'Quiz attempt' : outputTypeLabel(item.action_type);
}

function dayKey(date: Date): string {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

function dayLabel(date: Date, now: Date): string {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (dayKey(date) === dayKey(now)) {
    return 'Today';
  }
  if (dayKey(date) === dayKey(yesterday)) {
    return 'Yesterday';
  }
  return date.getFullYear() === now.getFullYear()
    ? DATE_FORMAT.format(date)
    : DATE_WITH_YEAR_FORMAT.format(date);
}

function groupByDay(items: ActivityItem[]): ActivityDay[] {
  const now = new Date();
  const days: ActivityDay[] = [];
  for (const item of items) {
    const date = new Date(item.occurred_at);
    const key = Number.isNaN(date.getTime()) ? 'unknown' : dayKey(date);
    const last = days[days.length - 1];
    if (last && last.key === key) {
      last.items.push(item);
    } else {
      days.push({
        key,
        label: key === 'unknown' ? 'Earlier' : dayLabel(date, now),
        items: [item],
      });
    }
  }
  return days;
}

function timeOf(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : TIME_FORMAT.format(date);
}

export function RecentActivity({
  limit,
  heading = 'Recent activity',
  headingLevel = 'h2',
  headingClassName,
  footer,
  className,
}: RecentActivityProps) {
  const { items, isLoading, error, reload } = useRecentActivity(limit);
  const Heading = headingLevel;
  const DayHeading = SUBHEADING[headingLevel];

  return (
    <section className={cx(styles.section, className)} aria-labelledby="recent-activity-heading">
      <Heading id="recent-activity-heading" className={cx(styles.heading, headingClassName)}>
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
          <div className={styles.days}>
            {groupByDay(items).map((day) => (
              <div key={day.key} className={styles.day}>
                <DayHeading className={styles.dayLabel}>{day.label}</DayHeading>
                <ol className={styles.list}>
                  {day.items.map((item) => (
                    <li
                      key={`${item.kind}-${item.attempt_id ?? item.output_id}-${item.occurred_at}`}
                    >
                      <Link to={activityHref(item)} className={styles.link}>
                        <span className={styles.action}>{actionLabel(item)}</span>
                        <span className={styles.course}>
                          <CourseChip courseId={item.course_id} />
                          <span className={styles.courseName}>{item.course_title}</span>
                          {item.topic ? <span className={styles.topic}>{item.topic}</span> : null}
                        </span>
                        <span className={cx(styles.score, 'tabular')}>
                          {item.score !== null ? `${Math.round(item.score * 100)}%` : null}
                        </span>
                        <time className={cx(styles.when, 'tabular')} dateTime={item.occurred_at}>
                          {timeOf(item.occurred_at)}
                        </time>
                      </Link>
                    </li>
                  ))}
                </ol>
              </div>
            ))}
          </div>
          {footer ? <div className={styles.footer}>{footer}</div> : null}
        </>
      )}
    </section>
  );
}
