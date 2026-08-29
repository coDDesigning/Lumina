import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { ScanSearch } from 'lucide-react';
import { examModeAPI } from '@/api/examMode';
import { describeGenerationError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { afterExamAnalysis, afterExamPlanCreated, afterExamRescan } from '@/api/invalidations';
import type { Workspace } from '@/data/workspaces';
import { useAuth } from '@/context/AuthContext';
import { useCredits } from '@/context/CreditContext';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { GeneratingState, GenerationError } from '@/features/study/GenerationStates';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { ExamPlanHistory } from './ExamPlanHistory';
import { ExamPrerequisiteNotices } from './ExamPrerequisiteNotices';
import { ExamSourceSelector } from './ExamSourceSelector';
import { ExamStage } from './ExamStage';
import type { StageState } from './ExamStage';
import { ExamTopicSelector } from './ExamTopicSelector';
import { useExamMode, useReadiness } from './useExamMode';
import { useElapsed } from './useElapsed';
import styles from './ExamModePage.module.css';

export interface ExamModePageProps {
  workspace: Workspace;
}

type Busy = null | 'analysis' | 'rescan' | 'plan';

export default function ExamModePage({ workspace }: ExamModePageProps) {
  const courseId = Number(workspace.id);
  const navigate = useNavigate();
  const { user } = useAuth();
  const { isMetered, canAfford } = useCredits();
  useDocumentTitle(`${workspace.name} · Exam Mode`);

  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const ownerDisplayName =
    workspace.ownerName ||
    workspace.ownerEmail ||
    (workspace.ownerId ? `User #${workspace.ownerId}` : 'another user');

  const exam = useExamMode(courseId, { readOnly: isSupportView });
  const inventory = exam.sources.data;
  const plans = exam.plans.data;
  const analysis = exam.analysis;

  const readiness = useReadiness({
    inventory,
    examDate: workspace.examDate,
    planCount: plans?.plans.length ?? 0,
  });

  const [selectedSources, setSelectedSources] = useState<ReadonlySet<string>>(new Set());
  const [selectedTopics, setSelectedTopics] = useState<ReadonlySet<string>>(new Set());
  const [highPriority, setHighPriority] = useState<ReadonlySet<string>>(new Set());
  const [busy, setBusy] = useState<Busy>(null);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [exhausted, setExhausted] = useState<'analysis' | 'rescan' | null>(null);
  const elapsed = useElapsed(busy !== null);

  // Start from what the analysis was actually run against, so a returning
  // student picks up their own selection rather than an empty one.
  useEffect(() => {
    if (!analysis) return;
    setSelectedSources(new Set(analysis.documents_analysed));
    setSelectedTopics(new Set(analysis.selection_carry_over.preselected_topic_keys));
    setHighPriority(new Set(analysis.selection_carry_over.high_priority_topic_keys));
  }, [analysis]);

  const readyIds = useMemo(
    () => (readiness?.readyDocuments ?? []).map((document) => document.id),
    [readiness],
  );

  function toggle(set: ReadonlySet<string>, value: string): Set<string> {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  }

  async function run(kind: 'analysis' | 'rescan') {
    const source = kind === 'analysis' ? 'exam_topic_analysis' : 'exam_topic_analysis_rescan';
    if (isMetered && !canAfford(source)) {
      setExhausted(kind);
      return;
    }
    setBusy(kind);
    setFailure(null);
    setExhausted(null);
    try {
      const request = { document_ids: [...selectedSources] };
      const result =
        kind === 'analysis'
          ? await examModeAPI.analyse(courseId, request)
          : await examModeAPI.rescan(courseId, request);
      if (kind === 'analysis') afterExamAnalysis(courseId);
      else afterExamRescan(courseId);
      void exam.analysisQuery.refetch();
      setSelectedTopics(new Set(result.analysis.selection_carry_over.preselected_topic_keys));
      setHighPriority(new Set(result.analysis.selection_carry_over.high_priority_topic_keys));
    } catch (error) {
      const described = describeGenerationError(error, 'Your sources could not be read.');
      if (isInsufficientCredits(described)) setExhausted(kind);
      else setFailure(described);
    } finally {
      setBusy(null);
    }
  }

  async function createPlan(selectionMode: 'manual' | 'all_discovered') {
    if (!analysis) return;
    setBusy('plan');
    setFailure(null);
    try {
      const plan = await examModeAPI.createPlan(courseId, {
        analysis_output_id: analysis.generated_output_id,
        selected_topic_keys: [...selectedTopics],
        high_priority_topic_keys: [...highPriority].filter((key) => selectedTopics.has(key)),
        selection_mode: selectionMode,
      });
      afterExamPlanCreated(courseId);
      navigate(`/courses/${courseId}/exam-mode/plans/${plan.generated_output_id}`);
    } catch (error) {
      setFailure(describeGenerationError(error, 'That plan could not be created.'));
    } finally {
      setBusy(null);
    }
  }

  const header = (
    <PageHeader
      courseId={workspace.id}
      crumbs={[
        { label: 'Courses', to: '/dashboard' },
        { label: workspace.name, to: `/courses/${workspace.id}` },
        { label: 'Exam Mode' },
      ]}
      badges={isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
    />
  );

  if (exam.error) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">{workspace.name} Exam Mode</h1>
          <ErrorState title="Exam Mode could not be loaded" onRetry={exam.reload}>
            {exam.error}
          </ErrorState>
        </div>
      </div>
    );
  }

  // Reading the sources is finished once an analysis exists; choosing topics
  // only becomes actionable at that point.
  const sourceState: StageState = exam.hasAnalysis ? 'done' : 'current';
  const topicState: StageState = analysis ? 'current' : 'waiting';

  return (
    <div className={styles.page}>
      {header}
      <div className={styles.body}>
        {isSupportView ? (
          <Alert tone="info">
            <strong>Read-only support view</strong> — Exam Mode for a course owned by{' '}
            <strong>{ownerDisplayName}</strong>. Nothing here can be generated or changed.
          </Alert>
        ) : null}

        <h1 className="visually-hidden">{workspace.name} Exam Mode</h1>

        {exam.isLoading || !readiness ? (
          <div className={styles.loading} role="status" aria-label="Loading Exam Mode">
            <Skeleton variant="heading" width="14rem" />
            <Skeleton variant="block" height="7rem" />
            <Skeleton variant="block" height="11rem" />
          </div>
        ) : (
          <>
            {readiness.blockers.length > 0 || readiness.warnings.length > 0 ? (
              <div className={styles.notices}>
                <ExamPrerequisiteNotices
                  courseId={courseId}
                  blockers={readiness.blockers}
                  warnings={readiness.warnings}
                  readOnly={isSupportView}
                />
              </div>
            ) : null}

            {plans && plans.plans.length > 0 ? (
              <section className={styles.block} aria-labelledby="exam-plans">
                <h2 id="exam-plans" className={styles.label}>
                  Your plans
                </h2>
                <ExamPlanHistory courseId={courseId} plans={plans} />
              </section>
            ) : null}

            {!isSupportView && inventory ? (
              <ExamStage
                number={1}
                title="Choose what to read"
                state={sourceState}
                headingId="exam-stage-sources"
                lede="Only the sources you tick are read. A past paper's questions are already transcribed, so you never have to type one in."
              >
                <div className={styles.panel}>
                  <ExamSourceSelector
                    documents={inventory.documents}
                    selected={selectedSources}
                    onToggle={(id) => setSelectedSources((current) => toggle(current, id))}
                    onSelectAllReady={() => setSelectedSources(new Set(readyIds))}
                    disabled={busy !== null}
                  />
                </div>

                {exhausted ? (
                  <CreditExhaustedNotice
                    source={
                      exhausted === 'analysis'
                        ? 'exam_topic_analysis'
                        : 'exam_topic_analysis_rescan'
                    }
                    action={exhausted === 'analysis' ? 'a source analysis' : 'a source rescan'}
                  />
                ) : null}

                {failure && busy === null ? (
                  <GenerationError failure={failure} onRetry={() => void run('analysis')} />
                ) : null}

                {busy === 'analysis' || busy === 'rescan' ? (
                  <GeneratingState
                    heading={busy === 'analysis' ? 'Reading your sources' : 'Reading them again'}
                    detail="Finding the topics your material and past papers actually cover."
                    elapsed={elapsed}
                  />
                ) : (
                  <div className={styles.actions}>
                    <Button
                      icon={<ScanSearch aria-hidden="true" />}
                      disabled={!readiness.canAnalyse || selectedSources.size === 0}
                      onClick={() => void run(exam.hasAnalysis ? 'rescan' : 'analysis')}
                    >
                      {exam.hasAnalysis ? 'Read them again' : 'Read these sources'}
                    </Button>
                    {selectedSources.size === 0 ? (
                      <p className={styles.hint}>Tick at least one ready source.</p>
                    ) : null}
                  </div>
                )}
              </ExamStage>
            ) : null}

            {analysis ? (
              <ExamStage
                number={2}
                title="Choose what to study"
                state={topicState}
                headingId="exam-stage-topics"
              >
                <ExamTopicSelector
                  analysis={analysis}
                  selected={selectedTopics}
                  highPriority={highPriority}
                  onToggle={(key) => setSelectedTopics((current) => toggle(current, key))}
                  onTogglePriority={(key) => setHighPriority((current) => toggle(current, key))}
                  onSelectAll={() =>
                    setSelectedTopics(new Set(analysis.topics.map((topic) => topic.topic_key)))
                  }
                  disabled={isSupportView || busy !== null}
                />

                {!isSupportView ? (
                  <div className={styles.actions}>
                    <Button
                      isLoading={busy === 'plan'}
                      loadingLabel="Ranking your topics"
                      disabled={!readiness.canPlan || selectedTopics.size === 0 || busy !== null}
                      onClick={() =>
                        void createPlan(
                          analysis.topics.length > 0 &&
                            selectedTopics.size === analysis.topics.length
                            ? 'all_discovered'
                            : 'manual',
                        )
                      }
                    >
                      Create plan
                    </Button>
                    {selectedTopics.size === 0 ? (
                      <p className={styles.hint}>Select at least one topic.</p>
                    ) : null}
                    {!readiness.canPlan ? (
                      <p className={styles.hint}>
                        A first plan needs an exam still to come. Set the date in course settings.
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </ExamStage>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
