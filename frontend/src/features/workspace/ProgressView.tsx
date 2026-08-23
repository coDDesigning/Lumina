import { Link } from 'react-router-dom';
import { CircleDot, Target, TrendingUp } from 'lucide-react';
import type { CourseProgressResponse, MasteryStatus } from '@/api/types';
import { cx } from '@/lib/cx';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import type { BadgeTone } from '@/ui/Badge';
import { EmptyState } from '@/ui/EmptyState';
import { Skeleton } from '@/ui/Skeleton';
import styles from './ProgressView.module.css';

export interface ProgressViewProps {
  courseId?: string | number;
  documentCount: number;
  readyDocumentCount: number;
  progress: CourseProgressResponse | null;
  isLoading: boolean;
  error: string | null;
  actions?: React.ReactNode;
}

const MASTERY_TONE: Record<MasteryStatus, BadgeTone> = {
  Mastered: 'success',
  'In Progress': 'processing',
  'Needs Review': 'warning',
};

function masteryTone(status: string): BadgeTone {
  return MASTERY_TONE[status as MasteryStatus] ?? 'neutral';
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
}

function Figure({ value, label, hint }: { value: string; label: string; hint?: string }) {
  return (
    <div className={styles.figure}>
      <span className={cx(styles.figureValue, 'tabular')}>{value}</span>
      <span className={styles.figureLabel}>{label}</span>
      {hint ? <span className={styles.figureHint}>{hint}</span> : null}
    </div>
  );
}

export function ProgressView({
  courseId,
  documentCount,
  readyDocumentCount,
  progress,
  isLoading,
  error,
  actions,
}: ProgressViewProps) {
  const attempts = progress?.attempts_count ?? 0;
  const topicMastery = progress?.topic_mastery ?? [];
  const weakTopics = progress?.weak_topics ?? [];
  const history = progress?.quiz_history ?? [];
  const averagePercent =
    progress?.average_score != null ? Math.round(progress.average_score * 100) : null;

  if (isLoading) {
    return (
      <div className={styles.view}>
        <Skeleton variant="block" />
        <Skeleton variant="block" />
      </div>
    );
  }

  if (error) {
    return (
      <Alert tone="destructive" live="alert" title="Your progress could not be loaded">
        {error}
      </Alert>
    );
  }

  if (attempts === 0) {
    return (
      <div className={styles.view}>
        <EmptyState
          icon={<Target aria-hidden="true" />}
          title="Nothing measured yet"
          description="Progress here comes from quizzes you have actually taken. Take one and this fills in."
          actions={actions}
          headingLevel="h2"
        />
        <p className={styles.sourceLine}>
          {readyDocumentCount} of {documentCount}{' '}
          {documentCount === 1 ? 'source is' : 'sources are'} ready to be used.
        </p>
      </div>
    );
  }

  return (
    <div className={styles.view}>
      <section className={styles.figures} aria-label="Where you stand">
        <Figure
          value={averagePercent !== null ? `${averagePercent}%` : '—'}
          label="Average score"
          hint="Across every attempt"
        />
        <Figure
          value={String(attempts)}
          label={attempts === 1 ? 'Quiz attempt' : 'Quiz attempts'}
          hint="Retaking the same quiz counts again"
        />
        <Figure
          value={String(readyDocumentCount)}
          label="Sources ready"
          hint={
            documentCount > readyDocumentCount
              ? `${documentCount - readyDocumentCount} still processing or failed`
              : 'All of them'
          }
        />
      </section>

      {weakTopics.length > 0 ? (
        <section className={styles.section} aria-labelledby="weak-topics-heading">
          <h2 id="weak-topics-heading" className={styles.heading}>
            Worth another look
          </h2>
          <ul className={styles.weakList}>
            {weakTopics.map((topic) => (
              <li key={topic}>
                <Badge tone="warning" icon={<CircleDot aria-hidden="true" />}>
                  {topic}
                </Badge>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className={styles.section} aria-labelledby="mastery-heading">
        <h2 id="mastery-heading" className={styles.heading}>
          Topic by topic
        </h2>

        {topicMastery.length === 0 ? (
          <p className={styles.note}>
            No topic breakdown yet. It appears once a quiz has covered named topics.
          </p>
        ) : (
          <ul className={styles.masteryList}>
            {topicMastery.map((topic) => (
              <li key={topic.topic} className={styles.masteryRow}>
                <div className={styles.masteryHead}>
                  <span className={styles.masteryName}>{topic.topic}</span>
                  <Badge tone={masteryTone(topic.status)}>{topic.status}</Badge>
                </div>
                <div
                  className={styles.bar}
                  role="meter"
                  aria-valuenow={topic.mastery_percentage}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`${topic.topic} mastery`}
                >
                  <div className={styles.barFill} style={{ width: `${topic.mastery_percentage}%` }} />
                </div>
                <p className={cx(styles.masteryDetail, 'tabular')}>
                  {topic.questions_correct} of {topic.questions_answered} correct ·{' '}
                  {topic.mastery_percentage}%
                </p>
              </li>
            ))}
          </ul>
        )}

        <p className={styles.note}>
          <TrendingUp className={styles.noteIcon} aria-hidden="true" />
          Mastery counts only the questions you have answered in this course. It is not a measure
          of how much of the material a quiz covered.
        </p>
      </section>

      {history.length > 0 ? (
        <section className={styles.section} aria-labelledby="history-heading">
          <h2 id="history-heading" className={styles.heading}>
            Every attempt
          </h2>
          <ol className={styles.history}>
            {history.map((item) => {
              const content = (
                <>
                  <span className={styles.historyDate}>{formatDate(item.created_at)}</span>
                  <span className={cx(styles.historyScore, 'tabular')}>
                    {Math.round(item.score * 100)}%
                  </span>
                  <span className={cx(styles.historyDetail, 'tabular')}>
                    {item.correct_count} of {item.total_questions}
                  </span>
                </>
              );

              return (
                <li key={item.attempt_id} className={styles.historyRow}>
                  {courseId ? (
                    <Link
                      to={`/courses/${courseId}/practice/${item.quiz_id}/attempts/${item.attempt_id}`}
                      className={styles.historyLink}
                      aria-label={`Review attempt from ${formatDate(item.created_at)}: ${Math.round(item.score * 100)}%`}
                    >
                      {content}
                    </Link>
                  ) : (
                    <div className={styles.historyItem}>{content}</div>
                  )}
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

    </div>
  );
}
