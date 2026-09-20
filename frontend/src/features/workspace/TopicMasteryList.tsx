import type { MasteryStatus, TopicMastery } from '@/api/types';
import { cx } from '@/lib/cx';
import { Badge } from '@/ui/Badge';
import type { BadgeTone } from '@/ui/Badge';
import styles from './TopicMasteryList.module.css';

export interface TopicMasteryListProps {
  topics: TopicMastery[];
}

const STATUS_TONE: Record<MasteryStatus, BadgeTone> = {
  Mastered: 'success',
  'In Progress': 'processing',
  'Needs Review': 'warning',
};

const STATUS_FILL: Record<MasteryStatus, string> = {
  Mastered: styles.fillMastered,
  'In Progress': styles.fillProgress,
  'Needs Review': styles.fillReview,
};

function statusTone(status: string): BadgeTone {
  return STATUS_TONE[status as MasteryStatus] ?? 'neutral';
}

function weakestFirst(topics: TopicMastery[]): TopicMastery[] {
  return [...topics].sort(
    (a, b) => a.mastery_percentage - b.mastery_percentage || a.topic.localeCompare(b.topic),
  );
}

function MasteryBar({ topic, className }: { topic: TopicMastery; className?: string }) {
  return (
    <div
      className={cx(styles.bar, className)}
      role="meter"
      aria-valuenow={topic.mastery_percentage}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`${topic.topic} mastery`}
    >
      <div
        className={cx(styles.fill, STATUS_FILL[topic.status as MasteryStatus])}
        style={{ width: `${topic.mastery_percentage}%` }}
      />
    </div>
  );
}

function correctOf(topic: TopicMastery): string {
  return `${topic.questions_correct} of ${topic.questions_answered}`;
}

export function TopicMasteryList({ topics }: TopicMasteryListProps) {
  return (
    <table className={styles.table}>
      <thead>
        <tr>
          <th scope="col">Topic</th>
          <th scope="col">Mastery</th>
          <th scope="col" className={styles.numeric}>
            Correct
          </th>
          <th scope="col">Status</th>
        </tr>
      </thead>
      <tbody>
        {weakestFirst(topics).map((topic) => (
          <tr key={topic.topic}>
            <th scope="row" className={styles.name}>
              {topic.topic}
            </th>
            <td>
              <span className={styles.meterCell}>
                <MasteryBar topic={topic} className={styles.shortBar} />
                <span className={cx(styles.percent, 'tabular')}>{topic.mastery_percentage}%</span>
              </span>
            </td>
            <td className={cx(styles.numeric, styles.muted, 'tabular')}>{correctOf(topic)}</td>
            <td>
              <Badge tone={statusTone(topic.status)}>{topic.status}</Badge>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
