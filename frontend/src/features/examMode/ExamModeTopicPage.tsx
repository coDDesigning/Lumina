import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { BookOpen, FileQuestion, Target } from 'lucide-react';
import { examModeAPI } from '@/api/examMode';
import { describeGenerationError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { afterExamTopicArtifact, afterExamTopicQuiz } from '@/api/invalidations';
import { queryKeys } from '@/api/queryKeys';
import type { ExamPlanView, ExamTopicGuideDocument } from '@/api/types';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useAuth } from '@/context/AuthContext';
import { useCredits } from '@/context/CreditContext';
import type { Workspace } from '@/data/workspaces';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { GeneratingState, GenerationError } from '@/features/study/GenerationStates';
import { useQuery } from '@/lib/query/useQuery';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { ExamTopicGuide } from './ExamTopicGuide';
import { SimilarQuestionBuilder } from './SimilarQuestionBuilder';
import { useElapsed } from './useElapsed';
import styles from './ExamModeTopicPage.module.css';

export interface ExamModeTopicPageProps {
  workspace: Workspace;
}

type Busy = null | 'guide' | 'practice' | 'exam';

export default function ExamModeTopicPage({ workspace }: ExamModeTopicPageProps) {
  const { planId: planParam, topicKey: topicParam } = useParams();
  const courseId = Number(workspace.id);
  const planId = Number(planParam);
  const topicKey = decodeURIComponent(topicParam ?? '');
  const navigate = useNavigate();
  const { user } = useAuth();
  const { isMetered, canAfford } = useCredits();

  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const validId = Number.isInteger(planId) && planId > 0 && topicKey.length > 0;

  const [busy, setBusy] = useState<Busy>(null);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const elapsed = useElapsed(busy !== null);

  const plan = useQuery<ExamPlanView>({
    key: validId ? queryKeys.examPlan(courseId, planId) : null,
    fetcher: ({ signal }) => examModeAPI.getPlan(courseId, planId, { signal }),
    fallbackMessage: 'That exam plan could not be loaded.',
    staleTime: 5 * 60_000,
  });

  const guide = useQuery<ExamTopicGuideDocument>({
    key: validId ? queryKeys.examTopicGuide(courseId, topicKey) : null,
    fetcher: ({ signal }) => examModeAPI.getTopicGuide(courseId, topicKey, { signal }),
    fallbackMessage: 'The study guide could not be loaded.',
  });

  const entitlements = useQuery({
    key: isSupportView ? null : queryKeys.examEntitlements(courseId),
    fetcher: ({ signal }) => examModeAPI.listEntitlements(courseId, { signal }),
    fallbackMessage: 'Your unlocked topics could not be loaded.',
  });

  const topic = plan.data?.topics.find((entry) => entry.topic_key === topicKey) ?? null;
  const unlocked = Boolean(entitlements.data?.unlocked_topic_keys.includes(topicKey));
  // A topic is bought once and covers every artifact of it, so a student who
  // already paid is never gated on their balance again.
  const wouldCharge = !unlocked && isMetered;

  useDocumentTitle(
    topic ? `${topic.display_label} · Exam Mode` : `${workspace.name} · Exam Mode`,
  );

  const header = (
    <PageHeader
      courseId={workspace.id}
      crumbs={[
        { label: 'Courses', to: '/dashboard' },
        { label: workspace.name, to: `/courses/${workspace.id}` },
        { label: 'Exam Mode', to: `/courses/${workspace.id}/exam-mode` },
        {
          label: plan.data ? `Version ${plan.data.plan_version}` : 'Exam plan',
          to: `/courses/${workspace.id}/exam-mode/plans/${planId}`,
        },
        { label: topic?.display_label ?? 'Topic' },
      ]}
      badges={isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
    />
  );

  if (!validId || (plan.status === 'success' && !topic)) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Exam topic</h1>
          <EmptyState
            title="That topic is not in this plan"
            description="Only a topic the plan ranked can be studied from it."
            actions={
              <LinkButton to={`/courses/${courseId}/exam-mode/plans/${planId}`}>
                Back to the plan
              </LinkButton>
            }
          />
        </div>
      </div>
    );
  }

  if (plan.status === 'error') {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Exam topic</h1>
          <ErrorState title="That plan could not be loaded" onRetry={() => void plan.refetch()}>
            {plan.error?.message}
          </ErrorState>
        </div>
      </div>
    );
  }

  if (!topic) {
    return (
      <div className={styles.page}>
        {header}
        <div className={styles.body}>
          <h1 className="visually-hidden">Exam topic</h1>
          <div className={styles.loading} role="status" aria-label="Loading topic">
            <Skeleton variant="heading" width="16rem" />
            <Skeleton variant="block" height="12rem" />
          </div>
        </div>
      </div>
    );
  }

  async function generate(kind: Exclude<Busy, null>) {
    if (wouldCharge && !canAfford('exam_topic_unlock')) {
      setExhausted(true);
      return;
    }
    setBusy(kind);
    setFailure(null);
    setExhausted(false);
    try {
      if (kind === 'guide') {
        await examModeAPI.generateTopicGuide(courseId, topicKey, { plan_output_id: planId });
        afterExamTopicArtifact(courseId, topicKey);
        void guide.refetch();
      } else {
        const result =
          kind === 'practice'
            ? await examModeAPI.generateTopicPractice(courseId, topicKey, {
                plan_output_id: planId,
              })
            : await examModeAPI.generateTopicExam(courseId, topicKey, {
                plan_output_id: planId,
              });
        afterExamTopicQuiz(courseId, topicKey);
        navigate(`/courses/${courseId}/practice/${result.quiz.quiz_id}`);
      }
    } catch (error) {
      const described = describeGenerationError(error, 'That could not be generated.');
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
        <h1 className="visually-hidden">{topic.display_label}</h1>

        <div className={styles.masthead}>
          <p className={styles.rank}>
            <span className="visually-hidden">Rank </span>#{topic.rank}
          </p>
          <p className={styles.title}>{topic.display_label}</p>
          <p className={styles.facts}>
            <span>{topic.priority_band} priority</span>
            {topic.mastery_percentage !== null ? (
              <span>
                <span className="tabular">{topic.mastery_percentage}</span>% mastery
              </span>
            ) : topic.is_unattempted ? (
              <span>not attempted yet</span>
            ) : null}
            {unlocked ? <span>unlocked</span> : null}
          </p>
          {topic.explanation ? <p className={styles.explanation}>{topic.explanation}</p> : null}
        </div>

        {exhausted ? (
          <CreditExhaustedNotice source="exam_topic_unlock" action="this topic" />
        ) : null}
        {failure && busy === null ? (
          <GenerationError failure={failure} onRetry={() => setFailure(null)} />
        ) : null}

        <section className={styles.block} aria-labelledby="topic-guide">
          <h2 id="topic-guide" className={styles.label}>
            Study guide
          </h2>
          {busy === 'guide' ? (
            <GeneratingState
              heading="Writing your guide"
              detail="Reading only the sources this plan was built from."
              elapsed={elapsed}
            />
          ) : guide.data ? (
            <ExamTopicGuide guide={guide.data} />
          ) : (
            <EmptyState
              icon={<BookOpen aria-hidden="true" />}
              title="No guide for this topic yet"
              description="A written guide with the sections, terms and pitfalls this topic needs."
              actions={
                isSupportView ? undefined : (
                  <Button onClick={() => void generate('guide')}>Write the guide</Button>
                )
              }
            />
          )}
        </section>

        {!isSupportView ? (
          <section className={styles.block} aria-labelledby="topic-practice">
            <h2 id="topic-practice" className={styles.label}>
              Practise this topic
            </h2>
            <div className={styles.actions}>
              <Button
                variant="secondary"
                icon={<Target aria-hidden="true" />}
                isLoading={busy === 'practice'}
                loadingLabel="Writing questions"
                disabled={busy !== null}
                onClick={() => void generate('practice')}
              >
                Practice questions
              </Button>
              <Button
                variant="secondary"
                icon={<FileQuestion aria-hidden="true" />}
                isLoading={busy === 'exam'}
                loadingLabel="Writing questions"
                disabled={busy !== null}
                onClick={() => void generate('exam')}
              >
                Topic exam
              </Button>
            </div>
            <p className={styles.hint}>
              Practice shows the answers as you go. A topic exam withholds them until you hand it
              in.
            </p>
          </section>
        ) : null}

        {!isSupportView && plan.data ? (
          <section className={styles.block} aria-labelledby="topic-similar">
            <h2 id="topic-similar" className={styles.label}>
              Questions in the style of your past papers
            </h2>
            <SimilarQuestionBuilder
              courseId={courseId}
              planId={planId}
              analysisId={plan.data.analysis_output_id}
              topicKey={topicKey}
            />
          </section>
        ) : null}
      </div>
    </div>
  );
}
