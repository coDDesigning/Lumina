import { Link } from 'react-router-dom';
import { Star } from 'lucide-react';
import type { ExamPlanTopicView, ExamPlanView } from '@/api/types';
import { cx } from '@/lib/cx';
import styles from './RankedTopicList.module.css';

export interface RankedTopicListProps {
  courseId: number;
  plan: ExamPlanView;
  /** Topic keys already paid for, so a price is never implied twice. */
  unlockedTopicKeys?: ReadonlySet<string>;
}

const BAND_CLASSES: Record<string, string> = {
  critical: styles.bandCritical,
  high: styles.bandHigh,
  medium: styles.bandMedium,
};

/**
 * What a mastery figure means, or the words for its absence.
 *
 * Null is not zero. A topic nobody has been quizzed on has no mastery, and
 * printing 0% would claim the student failed something they never sat.
 */
function masteryNote(topic: ExamPlanTopicView): string | null {
  if (topic.mastery_percentage !== null) return `${topic.mastery_percentage}% mastery`;
  if (topic.is_unattempted) return 'not attempted yet';
  return null;
}

/** An unavailable signal reads as unavailable, never as a zero. */
function describeSignal(value: unknown): string {
  if (value === null || value === undefined) return 'not available';
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    return entries.length === 0
      ? 'not available'
      : entries.map(([name, inner]) => `${name}: ${describeSignal(inner)}`).join(' · ');
  }
  return String(value);
}

function TopicRow({
  courseId,
  planId,
  topic,
  unlocked,
}: {
  courseId: number;
  planId: number;
  topic: ExamPlanTopicView;
  unlocked: boolean;
}) {
  const signals = Object.entries(topic.signals ?? {});
  const hasAudit = topic.reason_codes.length > 0 || signals.length > 0;
  const mastery = masteryNote(topic);

  return (
    <li className={styles.row}>
      <span className={styles.rank}>
        <span className="visually-hidden">Rank </span>
        {topic.rank}
        <span
          className={cx(styles.band, BAND_CLASSES[topic.priority_band.toLowerCase()])}
          aria-hidden="true"
        />
      </span>

      <div className={styles.body}>
        <Link
          className={styles.title}
          to={`/courses/${courseId}/exam-mode/plans/${planId}/topics/${encodeURIComponent(
            topic.topic_key,
          )}`}
        >
          {topic.display_label}
        </Link>

        <p className={styles.meta}>
          <span>{topic.priority_band} priority</span>
          {topic.is_high_priority ? (
            <span>
              <Star aria-hidden="true" size={14} /> you marked this
            </span>
          ) : null}
          {mastery ? <span>{mastery}</span> : null}
          {!topic.has_any_evidence ? <span>no signal available</span> : null}
          {unlocked ? <span>unlocked</span> : null}
        </p>

        {topic.explanation ? <p className={styles.explanation}>{topic.explanation}</p> : null}

        {hasAudit ? (
          <details className={styles.audit}>
            <summary>Why this rank</summary>
            <dl className={styles.signals}>
              <div className={styles.signal}>
                <dt>Priority score</dt>
                <dd className="tabular">{topic.priority_score}</dd>
              </div>
              {topic.reason_codes.length > 0 ? (
                <div className={styles.signal}>
                  <dt>Reasons</dt>
                  <dd>{topic.reason_codes.join(', ')}</dd>
                </div>
              ) : null}
              {signals.map(([name, value]) => (
                <div key={name} className={styles.signal}>
                  <dt>{name.replace(/_/g, ' ')}</dt>
                  <dd>{describeSignal(value)}</dd>
                </div>
              ))}
            </dl>
          </details>
        ) : null}
      </div>
    </li>
  );
}

/**
 * The plan's own order, its own scores, and its own sentences.
 *
 * Nothing here re-derives a ranking, re-sorts a tie, or writes an explanation.
 * `explanation` is assembled backend-side from constants; the raw signals sit
 * behind a disclosure as the audit trail for that sentence, not a second
 * opinion about it.
 */
export function RankedTopicList({ courseId, plan, unlockedTopicKeys }: RankedTopicListProps) {
  return (
    <div className={styles.panel}>
      <ol className={styles.list}>
        {plan.topics.map((topic) => (
          <TopicRow
            key={topic.topic_key}
            courseId={courseId}
            planId={plan.generated_output_id}
            topic={topic}
            unlocked={Boolean(unlockedTopicKeys?.has(topic.topic_key))}
          />
        ))}
      </ol>
    </div>
  );
}
