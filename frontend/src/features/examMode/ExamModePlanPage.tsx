import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { CalendarRange, ClipboardList, RefreshCw } from 'lucide-react';
import { examModeAPI } from '@/api/examMode';
import { examRoadmapAPI } from '@/api/examRoadmap';
import { describeGenerationError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import {
  afterExamPlanCreated,
  afterExamReviewSheet,
  afterExamRoadmapGenerated,
} from '@/api/invalidations';
import { queryKeys } from '@/api/queryKeys';
import type { ExamPlanView, ExamReviewSheetDocument, ExamRoadmap } from '@/api/types';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useAuth } from '@/context/AuthContext';
import type { Workspace } from '@/data/workspaces';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { GeneratingState, GenerationError } from '@/features/study/GenerationStates';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { ExamMockExamBuilder } from './ExamMockExamBuilder';
import { ExamReviewSheet } from './ExamReviewSheet';
import { ExamRoadmapPanel } from './ExamRoadmapPanel';
import { RankedTopicList } from './RankedTopicList';
import { describeStaleReasons, formatExamDate, stalenessAction } from './examModeFormatters';
import { useElapsed } from './useElapsed';
import styles from './ExamModePlanPage.module.css';

export interface ExamModePlanPageProps {
  workspace: Workspace;
}

type Busy = null | 'refresh' | 'roadmap' | 'review';

export default function ExamModePlanPage({ workspace }: ExamModePlanPageProps) {
  const { planId: planParam } = useParams();
  const courseId = Number(workspace.id);
  const planId = Number(planParam);
  const navigate = useNavigate();
  const { user } = useAuth();
  useDocumentTitle(`${workspace.name} · Exam plan`);

  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const validId = Number.isInteger(planId) && planId > 0;

  const [busy, setBusy] = useState<Busy>(null);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const [roadmap, setRoadmap] = useState<ExamRoadmap | null>(null);
  const elapsed = useElapsed(busy !== null);

  const plan = useQuery<ExamPlanView>({
    key: validId ? queryKeys.examPlan(courseId, planId) : null,
    fetcher: ({ signal }) => examModeAPI.getPlan(courseId, planId, { signal }),
    fallbackMessage: 'That exam plan could not be loaded.',
    staleTime: 5 * 60_000,
  });

  const entitlements = useQuery({
    key: isSupportView ? null : queryKeys.examEntitlements(courseId),
    fetcher: ({ signal }) => examModeAPI.listEntitlements(courseId, { signal }),
    fallbackMessage: 'Your unlocked topics could not be loaded.',
  });

  // A saved review sheet is read, never generated, when the page opens.
  const reviewSheet = useQuery<ExamReviewSheetDocument>({
    key: validId ? queryKeys.examReviewSheet(courseId) : null,
    fetcher: ({ signal }) => examModeAPI.getReviewSheet(courseId, { signal }),
    fallbackMessage: 'The review sheet could not be loaded.',
  });

  const header = (
    <PageHeader
      courseId={workspace.id}
      crumbs={[
        { label: 'Courses', to: '/dashboard' },
        { label: workspace.name, to: `/courses/${workspace.id}` },
        { label: 'Exam Mode', to: `/courses/${workspace.id}/exam-mode` },
        { label: plan.data ? `Version ${plan.data.plan_version}` : 'Exam plan' },
      ]}
      badges={isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
    />
  );

  if (!validId) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Exam plan</h1>
          <EmptyState
            title="That plan is not available"
            description="The link does not name a plan of this course."
            actions={
              <LinkButton to={`/courses/${courseId}/exam-mode`}>Back to Exam Mode</LinkButton>
            }
          />
        </div>
      </div>
    );
  }

  if (plan.status === 'error') {
    const gone = plan.error?.status === 404;
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Exam plan</h1>
          {gone ? (
            <EmptyState
              title="That plan is not available"
              description="It may no longer exist, or it may not be one of yours."
              actions={
                <LinkButton to={`/courses/${courseId}/exam-mode`}>Back to Exam Mode</LinkButton>
              }
            />
          ) : (
            <ErrorState title="That plan could not be loaded" onRetry={() => void plan.refetch()}>
              {plan.error?.message}
            </ErrorState>
          )}
        </div>
      </div>
    );
  }

  if (!plan.data) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Exam plan</h1>
          <div className={styles.loading} role="status" aria-label="Loading exam plan">
            <Skeleton variant="heading" width="16rem" />
            <Skeleton variant="block" height="14rem" />
          </div>
        </div>
      </div>
    );
  }

  const current = plan.data;
  const stale = stalenessAction(current.staleness);

  async function refreshRanking() {
    setBusy('refresh');
    setFailure(null);
    try {
      const next = await examModeAPI.createPlan(courseId, {
        analysis_output_id: current.analysis_output_id,
        selected_topic_keys: current.topics.map((topic) => topic.topic_key),
        high_priority_topic_keys: current.topics
          .filter((topic) => topic.is_high_priority)
          .map((topic) => topic.topic_key),
        selection_mode: current.selection_mode === 'all_discovered' ? 'all_discovered' : 'manual',
      });
      afterExamPlanCreated(courseId);
      navigate(`/courses/${courseId}/exam-mode/plans/${next.generated_output_id}`);
    } catch (error) {
      setFailure(describeGenerationError(error, 'The ranking could not be refreshed.'));
    } finally {
      setBusy(null);
    }
  }

  async function buildRoadmap() {
    setBusy('roadmap');
    setFailure(null);
    try {
      const result = await examRoadmapAPI.generate(courseId, {
        plan_output_id: current.generated_output_id,
      });
      afterExamRoadmapGenerated(courseId);
      setRoadmap(result.roadmap);
    } catch (error) {
      setFailure(describeGenerationError(error, 'That roadmap could not be built.'));
    } finally {
      setBusy(null);
    }
  }

  async function buildReviewSheet() {
    setBusy('review');
    setFailure(null);
    setExhausted(false);
    try {
      await examModeAPI.generateReviewSheet(courseId, {
        plan_output_id: current.generated_output_id,
      });
      afterExamReviewSheet(courseId);
      void reviewSheet.refetch();
    } catch (error) {
      const described = describeGenerationError(error, 'That review sheet could not be written.');
      if (isInsufficientCredits(described)) setExhausted(true);
      else setFailure(described);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className={styles.page}>
      {header}
      <div className={styles.body}>
        <h1 className="visually-hidden">
          {workspace.name} exam plan version {current.plan_version}
        </h1>

        <div className={styles.masthead}>
          <p className={styles.version}>Version {current.plan_version}</p>
          <p className={styles.facts}>
            <span>
              <span className="tabular">{current.topics.length}</span>{' '}
              {current.topics.length === 1 ? 'topic' : 'topics'}
            </span>
            {current.exam_date ? <span>exam {formatExamDate(current.exam_date)}</span> : null}
            <span>
              {current.selection_mode === 'all_discovered'
                ? 'every discovered topic'
                : 'manually selected'}
            </span>
          </p>
        </div>

        {current.warnings.length > 0 ? (
          <div className={styles.notices}>
            {current.warnings.map((warning) => (
              <Alert key={warning} tone="warning">
                {warning}
              </Alert>
            ))}
          </div>
        ) : null}

        {stale ? (
          <Alert
            tone="info"
            title={
              current.staleness.requires_rescan
                ? 'Your sources have changed since this plan'
                : 'This ranking is out of date'
            }
            actions={
              isSupportView ? null : current.staleness.requires_rescan ? (
                <LinkButton variant="secondary" size="sm" to={`/courses/${courseId}/exam-mode`}>
                  {stale.label}
                </LinkButton>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  icon={<RefreshCw aria-hidden="true" />}
                  isLoading={busy === 'refresh'}
                  loadingLabel="Refreshing"
                  onClick={() => void refreshRanking()}
                >
                  {stale.label}
                </Button>
              )
            }
          >
            This version stays exactly as it is —{' '}
            {describeStaleReasons(current.staleness.stale_reasons)}. {stale.detail}
          </Alert>
        ) : null}

        {failure && busy === null ? (
          <GenerationError failure={failure} onRetry={() => setFailure(null)} />
        ) : null}

        <section className={styles.block} aria-labelledby="plan-topics">
          <h2 id="plan-topics" className={styles.label}>
            What to study, in order
          </h2>
          <RankedTopicList
            courseId={courseId}
            plan={current}
            unlockedTopicKeys={new Set(entitlements.data?.unlocked_topic_keys ?? [])}
          />
        </section>

        <section className={styles.block} aria-labelledby="plan-roadmap">
          <h2 id="plan-roadmap" className={styles.label}>
            Day by day
          </h2>
          {busy === 'roadmap' ? (
            <GeneratingState
              heading="Laying out your days"
              detail="Spreading this plan's topics across the time you have."
              elapsed={elapsed}
            />
          ) : roadmap ? (
            <ExamRoadmapPanel
              courseId={courseId}
              planId={current.generated_output_id}
              roadmap={roadmap}
            />
          ) : (
            <EmptyState
              icon={<CalendarRange aria-hidden="true" />}
              title="No day-by-day plan yet"
              description="Turn this plan into daily study goals. Nothing is charged and no model is asked."
              actions={
                isSupportView ? undefined : (
                  <Button onClick={() => void buildRoadmap()}>Build the day-by-day plan</Button>
                )
              }
            />
          )}
        </section>

        {!isSupportView ? (
          <section className={styles.block} aria-labelledby="plan-mock">
            <h2 id="plan-mock" className={styles.label}>
              Sit a full paper
            </h2>
            <ExamMockExamBuilder courseId={courseId} plan={current} />
          </section>
        ) : null}

        <section className={styles.block} aria-labelledby="plan-review">
          <h2 id="plan-review" className={styles.label}>
            Last-minute review
          </h2>
          {exhausted ? (
            <CreditExhaustedNotice source="exam_review_sheet" action="a review sheet" />
          ) : null}
          {busy === 'review' ? (
            <GeneratingState
              heading="Writing your review sheet"
              detail="Pulling out what you must remember and where this course sets traps."
              elapsed={elapsed}
            />
          ) : reviewSheet.data &&
            reviewSheet.data.plan_output_id === current.generated_output_id ? (
            <ExamReviewSheet sheet={reviewSheet.data} />
          ) : (
            <EmptyState
              icon={<ClipboardList aria-hidden="true" />}
              title="No review sheet for this version"
              description={
                reviewSheet.data
                  ? 'The saved review sheet belongs to a different plan version.'
                  : 'A one-page sheet of what to remember, the traps, and the final checks.'
              }
              actions={
                isSupportView ? undefined : (
                  <Button onClick={() => void buildReviewSheet()}>Write the review sheet</Button>
                )
              }
            />
          )}
        </section>
      </div>
    </div>
  );
}
