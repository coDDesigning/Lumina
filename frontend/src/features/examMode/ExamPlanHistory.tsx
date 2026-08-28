import { Link } from 'react-router-dom';
import type { ExamPlanList } from '@/api/types';
import { Badge } from '@/ui/Badge';
import { EmptyState } from '@/ui/EmptyState';
import { formatExamDate, formatCreatedAt } from './examModeFormatters';
import styles from './ExamPlanHistory.module.css';

export interface ExamPlanHistoryProps {
  courseId: number;
  plans: ExamPlanList;
  /** The two versions currently chosen for comparison, in click order. */
  comparing?: readonly number[];
  onCompare?: (planId: number) => void;
}

/**
 * Every saved plan version, newest first. Opening one is a database read, so a
 * plan the student worked from last month still opens exactly as it was.
 */
export function ExamPlanHistory({
  courseId,
  plans,
  comparing = [],
  onCompare,
}: ExamPlanHistoryProps) {
  if (plans.plans.length === 0) {
    return (
      <EmptyState
        title="No plan yet"
        description="Analyse your sources and pick your topics to create the first one."
      />
    );
  }

  return (
    <div className={styles.panel}>
      <ul className={styles.list}>
      {plans.plans.map((plan) => {
        const chosen = comparing.includes(plan.generated_output_id);
        return (
          <li key={plan.generated_output_id} className={styles.row}>
            <div className={styles.main}>
              <Link
                className={styles.title}
                to={`/courses/${courseId}/exam-mode/plans/${plan.generated_output_id}`}
              >
                Version <span className="tabular">{plan.plan_version}</span>
              </Link>
              <p className={styles.meta}>
                {formatCreatedAt(plan.created_at)} ·{' '}
                <span className="tabular">{plan.topic_count}</span>{' '}
                {plan.topic_count === 1 ? 'topic' : 'topics'}
                {plan.exam_date ? ` · exam ${formatExamDate(plan.exam_date)}` : ''}
                {plan.selection_mode === 'all_discovered'
                  ? ' · every discovered topic'
                  : ' · manually selected'}
              </p>
            </div>
            <div className={styles.marks}>
              {plan.is_current ? <Badge tone="success">Current</Badge> : null}
              {plan.supersedes_output_id ? (
                <Badge tone="neutral">
                  Replaces v
                  <span className="tabular">{plan.plan_version - 1}</span>
                </Badge>
              ) : null}
              {onCompare ? (
                <label className={styles.compare}>
                  <input
                    type="checkbox"
                    checked={chosen}
                    onChange={() => onCompare(plan.generated_output_id)}
                  />
                  <span>Compare</span>
                </label>
              ) : null}
            </div>
          </li>
          );
        })}
      </ul>
    </div>
  );
}
