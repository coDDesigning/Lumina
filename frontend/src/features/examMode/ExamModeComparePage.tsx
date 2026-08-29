import { useMemo } from 'react';
import { useParams } from 'react-router-dom';
import { examModeAPI } from '@/api/examMode';
import { queryKeys } from '@/api/queryKeys';
import type { ExamPlanTopicView, ExamPlanView } from '@/api/types';
import { useAuth } from '@/context/AuthContext';
import type { Workspace } from '@/data/workspaces';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { comparePlans } from './comparePlans';
import type { TopicChange } from './comparePlans';
import { formatExamDate } from './examModeFormatters';
import styles from './ExamModeComparePage.module.css';

export interface ExamModeComparePageProps {
  workspace: Workspace;
}

/** A value, or the words for its absence. Never a stand-in zero. */
function masteryOf(topic: ExamPlanTopicView | null): string {
  if (!topic) return '—';
  if (topic.mastery_percentage === null) {
    return topic.is_unattempted ? 'not attempted' : 'not measured';
  }
  return `${topic.mastery_percentage}%`;
}

function Row({ entry }: { entry: TopicChange }) {
  return (
    <li className={styles.row}>
      <p className={styles.topic}>{entry.label}</p>
      <dl className={styles.values}>
        <div className={styles.value}>
          <dt>Rank</dt>
          <dd>
            <span className="tabular">{entry.before?.rank ?? '—'}</span>
            <span className={styles.arrow} aria-label="becomes">
              →
            </span>
            <span className="tabular">{entry.after?.rank ?? '—'}</span>
            {entry.rankDelta !== null && entry.rankDelta !== 0 ? (
              <span className={styles.delta}>
                {entry.rankDelta < 0 ? 'up' : 'down'}{' '}
                <span className="tabular">{Math.abs(entry.rankDelta)}</span>
              </span>
            ) : null}
          </dd>
        </div>
        <div className={styles.value}>
          <dt>Priority</dt>
          <dd>
            {entry.before?.priority_band ?? '—'}
            <span className={styles.arrow} aria-label="becomes">
              →
            </span>
            {entry.after?.priority_band ?? '—'}
          </dd>
        </div>
        <div className={styles.value}>
          <dt>Mastery</dt>
          <dd>
            {masteryOf(entry.before)}
            <span className={styles.arrow} aria-label="becomes">
              →
            </span>
            {masteryOf(entry.after)}
          </dd>
        </div>
        {entry.priorityChanged ? (
          <div className={styles.value}>
            <dt>You marked it</dt>
            <dd>{entry.after?.is_high_priority ? 'high priority' : 'no longer high priority'}</dd>
          </div>
        ) : null}
      </dl>
    </li>
  );
}

function Group({ title, entries }: { title: string; entries: TopicChange[] }) {
  if (entries.length === 0) return null;
  return (
    <section className={styles.group}>
      <h2 className={styles.groupTitle}>
        {title} (<span className="tabular">{entries.length}</span>)
      </h2>
      <div className={styles.panel}>
        <ul className={styles.list}>
          {entries.map((entry) => (
            <Row key={entry.topicKey} entry={entry} />
          ))}
        </ul>
      </div>
    </section>
  );
}

export default function ExamModeComparePage({ workspace }: ExamModeComparePageProps) {
  const { planId: planParam, otherPlanId: otherParam } = useParams();
  const courseId = Number(workspace.id);
  const planId = Number(planParam);
  const otherId = Number(otherParam);
  const { user } = useAuth();
  useDocumentTitle(`${workspace.name} · Comparing exam plans`);

  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const valid =
    Number.isInteger(planId) && planId > 0 && Number.isInteger(otherId) && otherId > 0;

  const left = useQuery<ExamPlanView>({
    key: valid ? queryKeys.examPlan(courseId, planId) : null,
    fetcher: ({ signal }) => examModeAPI.getPlan(courseId, planId, { signal }),
    fallbackMessage: 'That exam plan could not be loaded.',
    staleTime: 5 * 60_000,
  });

  const right = useQuery<ExamPlanView>({
    key: valid ? queryKeys.examPlan(courseId, otherId) : null,
    fetcher: ({ signal }) => examModeAPI.getPlan(courseId, otherId, { signal }),
    fallbackMessage: 'That exam plan could not be loaded.',
    staleTime: 5 * 60_000,
  });

  // Older version first, whichever way round the two links were opened.
  const [before, after] = useMemo(() => {
    if (!left.data || !right.data) return [undefined, undefined];
    return left.data.plan_version <= right.data.plan_version
      ? [left.data, right.data]
      : [right.data, left.data];
  }, [left.data, right.data]);

  const comparison = useMemo(
    () => (before && after ? comparePlans(before, after) : null),
    [before, after],
  );

  const header = (
    <PageHeader
      courseId={workspace.id}
      crumbs={[
        { label: 'Courses', to: '/dashboard' },
        { label: workspace.name, to: `/courses/${workspace.id}` },
        { label: 'Exam Mode', to: `/courses/${workspace.id}/exam-mode` },
        { label: 'Comparing versions' },
      ]}
      badges={isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
    />
  );

  if (!valid) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Comparing exam plans</h1>
          <EmptyState
            title="Those plans are not available"
            description="The link does not name two plans of this course."
            actions={
              <LinkButton to={`/courses/${courseId}/exam-mode`}>Back to Exam Mode</LinkButton>
            }
          />
        </div>
      </div>
    );
  }

  const failed =
    left.status === 'error' ? left.error : right.status === 'error' ? right.error : null;
  if (failed) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Comparing exam plans</h1>
          {failed.status === 404 ? (
            <EmptyState
              title="Those plans are not available"
              description="One of them may no longer exist, or may not be one of yours."
              actions={
                <LinkButton to={`/courses/${courseId}/exam-mode`}>Back to Exam Mode</LinkButton>
              }
            />
          ) : (
            <ErrorState
              title="The comparison could not be loaded"
              onRetry={() => {
                void left.refetch();
                void right.refetch();
              }}
            >
              {failed.message}
            </ErrorState>
          )}
        </div>
      </div>
    );
  }

  if (!before || !after || !comparison) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Comparing exam plans</h1>
          <div className={styles.loading} role="status" aria-label="Loading comparison">
            <Skeleton variant="heading" width="18rem" />
            <Skeleton variant="block" height="14rem" />
          </div>
        </div>
      </div>
    );
  }

  const nothingMoved =
    comparison.added.length === 0 &&
    comparison.removed.length === 0 &&
    comparison.changed.length === 0;

  return (
    <div className={styles.page}>
      {header}
      <div className={styles.body}>
        <h1 className="visually-hidden">
          Comparing exam plan versions {before.plan_version} and {after.plan_version}
        </h1>

        <div className={styles.masthead}>
          <p className={styles.summaryTitle}>
            Version <span className="tabular">{before.plan_version}</span> to version{' '}
            <span className="tabular">{after.plan_version}</span>
          </p>
          <p className={styles.facts}>
            <span>
              {comparison.analysisChanged ? 'a different analysis' : 'the same analysis'}
            </span>
            {comparison.examDateChanged ? (
              <span>
                exam moved {before.exam_date ? formatExamDate(before.exam_date) : 'unset'} to{' '}
                {after.exam_date ? formatExamDate(after.exam_date) : 'unset'}
              </span>
            ) : null}
            {comparison.signalsChanged.length > 0 ? (
              <span>
                signals changed:{' '}
                {comparison.signalsChanged.map((name) => name.replace(/_/g, ' ')).join(', ')}
              </span>
            ) : null}
          </p>
          <p className={styles.lede}>
            Both versions are read exactly as they were saved. Neither is re-scored against the
            course as it is today.
          </p>
        </div>

        {after.warnings.length > 0 ? (
          <div className={styles.notices}>
            {after.warnings.map((warning) => (
              <Alert key={warning} tone="warning">
                {warning}
              </Alert>
            ))}
          </div>
        ) : null}

        {nothingMoved ? (
          <EmptyState
            title="Nothing moved between these versions"
            description="Same topics, same order, same priorities."
          />
        ) : (
          <>
            <Group title="Added" entries={comparison.added} />
            <Group title="Removed" entries={comparison.removed} />
            <Group title="Changed" entries={comparison.changed} />
          </>
        )}
      </div>
    </div>
  );
}
