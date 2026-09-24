import type { CSSProperties } from 'react';
import { courseHue } from '@/lib/courseLight';
import { cx } from '@/lib/cx';
import { Skeleton } from '@/ui/Skeleton';
import { RHYTHM_DAYS, summarizeActivity } from './summarizeActivity';
import type { ActivitySummaryData, CourseShare, RhythmDay } from './summarizeActivity';
import { useRecentActivity } from './useRecentActivity';
import styles from './ActivitySummary.module.css';

export interface ActivitySummaryProps {
  limit: number;
}

type IndexedStyle = CSSProperties & { '--i'?: number; '--h'?: string; '--light-hue'?: number };

const DAY_FORMAT = new Intl.DateTimeFormat('en', {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
});

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function weekComparison(thisWeek: number, lastWeek: number): string {
  const difference = thisWeek - lastWeek;
  if (lastWeek === 0 && thisWeek === 0) {
    return 'Nothing in the week before either';
  }
  if (difference === 0) {
    return 'Same as the week before';
  }
  return difference > 0
    ? `${difference} more than the week before`
    : `${-difference} fewer than the week before`;
}

function Rhythm({ days, activeDays }: { days: RhythmDay[]; activeDays: number }) {
  const busiest = Math.max(1, ...days.map((day) => day.count));

  return (
    <div className={styles.rhythm}>
      <div className={styles.rhythmHead}>
        <h3 className={styles.label}>Last 4 weeks</h3>
        <p className={styles.rhythmTotal}>
          <span className={cx(styles.inlineFigure, 'tabular')}>{activeDays}</span> of{' '}
          {RHYTHM_DAYS} days with study
        </p>
      </div>
      <ol
        className={styles.bars}
        aria-label={`${activeDays} of the last ${RHYTHM_DAYS} days with study`}
      >
        {days.map((day, index) => {
          const style: IndexedStyle = {
            '--i': index,
            '--h': `${(day.count / busiest) * 100}%`,
          };
          const count = plural(day.count, 'thing', 'things');
          const description = `${DAY_FORMAT.format(day.date)}, ${count}`;
          return (
            <li key={day.date.toISOString()} className={styles.barSlot} title={description}>
              <span className={styles.srOnly}>{description}</span>
              <span
                className={cx(
                  styles.bar,
                  day.count === 0 && styles.barEmpty,
                  index === days.length - 1 && styles.barToday,
                )}
                style={style}
                aria-hidden="true"
              />
            </li>
          );
        })}
      </ol>
      <div className={styles.axis} aria-hidden="true">
        <span>{DAY_FORMAT.format(days[0].date)}</span>
        <span>Today</span>
      </div>
    </div>
  );
}

function CourseSplit({ courses, total }: { courses: CourseShare[]; total: number }) {
  return (
    <div className={styles.split}>
      <h3 className={styles.label}>By course</h3>
      <div className={styles.splitBar} aria-hidden="true">
        {courses.map((share) => {
          const style: IndexedStyle = {
            flexGrow: share.count,
            '--light-hue': share.courseId === null ? undefined : courseHue(share.courseId),
          };
          return (
            <span
              key={share.courseId ?? 'other'}
              className={cx(styles.segment, share.courseId === null && styles.segmentOther)}
              style={style}
            />
          );
        })}
      </div>
      <ul className={styles.legend}>
        {courses.map((share) => {
          const style: IndexedStyle =
            share.courseId === null ? {} : { '--light-hue': courseHue(share.courseId) };
          return (
            <li key={share.courseId ?? 'other'} className={styles.legendItem}>
              <span
                className={cx(styles.swatch, share.courseId === null && styles.segmentOther)}
                style={style}
                aria-hidden="true"
              />
              <span className={styles.legendTitle}>{share.title}</span>
              <span className={cx(styles.legendCount, 'tabular')}>
                {Math.round((share.count / total) * 100)}%
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function SummaryBody({ summary, capped }: { summary: ActivitySummaryData; capped: boolean }) {
  return (
    <>
      <Rhythm days={summary.rhythm} activeDays={summary.activeDays} />

      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt className={styles.label}>Last 7 days</dt>
          <dd className={cx(styles.figure, 'tabular')}>{summary.thisWeek}</dd>
          <dd className={styles.note}>
            {summary.thisWeek === 1 ? 'thing done' : 'things done'}.{' '}
            {weekComparison(summary.thisWeek, summary.lastWeek)}.
          </dd>
        </div>
        <div className={styles.fact}>
          <dt className={styles.label}>Quiz average</dt>
          {summary.averageScore === null ? (
            <dd className={styles.note}>No scored quiz attempts yet.</dd>
          ) : (
            <>
              <dd className={cx(styles.figure, 'tabular')}>
                {Math.round(summary.averageScore * 100)}%
              </dd>
              <dd className={styles.note}>
                Across {plural(summary.attemptCount, 'attempt', 'attempts')}.
              </dd>
            </>
          )}
        </div>
      </dl>

      <CourseSplit courses={summary.courses} total={summary.total} />

      {capped ? (
        <p className={styles.caveat}>Worked out from your latest {summary.total} events.</p>
      ) : null}
    </>
  );
}

export function ActivitySummary({ limit }: ActivitySummaryProps) {
  const { items, isLoading, error } = useRecentActivity(limit);

  if (error || (!isLoading && items.length === 0)) {
    return null;
  }

  return (
    <section className={styles.panel} aria-labelledby="activity-summary-heading">
      <h2 id="activity-summary-heading" className={styles.srOnly}>
        Summary
      </h2>
      {isLoading ? (
        <div className={styles.loading} aria-hidden="true">
          <Skeleton height="5rem" />
          <Skeleton width="60%" />
        </div>
      ) : (
        <SummaryBody summary={summarizeActivity(items)} capped={items.length >= limit} />
      )}
    </section>
  );
}
