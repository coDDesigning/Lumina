import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Shuffle } from 'lucide-react';
import { examModeAPI } from '@/api/examMode';
import { describeGenerationError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { afterExamSimilarQuestions } from '@/api/invalidations';
import { queryKeys } from '@/api/queryKeys';
import type {
  ExamQuestionPage,
  ExamQuestionView,
  SimilarQuestionDifficultyPolicy,
} from '@/api/types';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { GeneratingState, GenerationError } from '@/features/study/GenerationStates';
import { useQuery } from '@/lib/query/useQuery';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { CitationList } from '@/ui/CitationChip';
import { EmptyState } from '@/ui/EmptyState';
import { ErrorState } from '@/ui/ErrorState';
import { Input, Select } from '@/ui/Input';
import { Skeleton } from '@/ui/Skeleton';
import { useElapsed } from './useElapsed';
import {
  MAX_QUESTION_COUNT,
  MIN_QUESTION_COUNT,
  parseQuestionCount,
} from './questionCount';
import styles from './SimilarQuestionBuilder.module.css';

const POLICIES: { value: SimilarQuestionDifficultyPolicy; label: string }[] = [
  { value: 'match_source', label: 'Match the original' },
  { value: 'easy', label: 'Easier' },
  { value: 'medium', label: 'Medium' },
  { value: 'hard', label: 'Harder' },
];

const PAGE_SIZE = 50;

/**
 * How one listed question is named, both in the ticked set and on the wire.
 *
 * Position alone is per-paper, so two papers collide on it: one tick would
 * check a row in every paper at once and name a question the student never saw.
 */
function refKey(question: Pick<ExamQuestionView, 'document_id' | 'position'>): string {
  return `${question.document_id}#${question.position}`;
}

export interface SimilarQuestionBuilderProps {
  courseId: number;
  planId: number;
  analysisId: number;
  topicKey: string;
}

/**
 * New questions written from the questions a past paper actually asked.
 *
 * The student names sources by identifier, never by pasting text: the paper was
 * transcribed once when it was uploaded. The generated set is style-matched and
 * is described as exactly that -- nothing here claims to know what the real
 * exam will ask.
 */
export function SimilarQuestionBuilder({
  courseId,
  planId,
  analysisId,
  topicKey,
}: SimilarQuestionBuilderProps) {
  const navigate = useNavigate();
  const { isMetered, canAfford } = useCredits();

  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [questionCount, setQuestionCount] = useState('5');
  const [policy, setPolicy] = useState<SimilarQuestionDifficultyPolicy>('match_source');
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const elapsed = useElapsed(busy);

  const count = parseQuestionCount(questionCount);

  const questions = useQuery<ExamQuestionPage>({
    key: queryKeys.examQuestions(courseId, analysisId, topicKey, PAGE_SIZE, 0),
    fetcher: ({ signal }) =>
      examModeAPI.listQuestions(
        courseId,
        analysisId,
        { topicKey, limit: PAGE_SIZE, offset: 0 },
        { signal },
      ),
    fallbackMessage: 'The questions from your past papers could not be loaded.',
  });

  if (questions.status === 'error') {
    return (
      <ErrorState
        title="Your past-paper questions could not be loaded"
        onRetry={() => void questions.refetch()}
      >
        {questions.error?.message}
      </ErrorState>
    );
  }

  if (!questions.data) {
    return (
      <div className={styles.loading} role="status" aria-label="Loading past-paper questions">
        <Skeleton variant="block" height="6rem" />
      </div>
    );
  }

  if (questions.data.questions.length === 0) {
    return (
      <EmptyState
        title="No past-paper questions cover this topic"
        description="These questions are written from the ones your own papers asked. Select a past exam among your sources, or upload one, and scan again."
      />
    );
  }

  const listed = questions.data.questions;
  const total = questions.data.total;

  async function generate() {
    if (isMetered && !canAfford('exam_topic_unlock')) {
      setExhausted(true);
      return;
    }
    setBusy(true);
    setFailure(null);
    setExhausted(false);
    try {
      const result = await examModeAPI.generateSimilarQuestions(courseId, topicKey, {
        plan_output_id: planId,
        source_questions: listed
          .filter((question) => selected.has(refKey(question)))
          .map((question) => ({
            document_id: question.document_id,
            position: question.position,
          })),
        question_count: count.value,
        difficulty_policy: policy,
      });
      afterExamSimilarQuestions(courseId, topicKey);
      navigate(`/courses/${courseId}/practice/${result.quiz.quiz_id}`);
    } catch (error) {
      const described = describeGenerationError(error, 'Those questions could not be written.');
      if (isInsufficientCredits(described)) setExhausted(true);
      else setFailure(described);
    } finally {
      setBusy(false);
    }
  }

  if (busy) {
    return (
      <GeneratingState
        heading="Writing new questions"
        detail="Following the structure and difficulty of the questions you chose."
        elapsed={elapsed}
      />
    );
  }

  return (
    <div className={styles.builder}>
      <Alert tone="info">
        These questions are inspired by the structure and difficulty of your selected past papers.
        They do not predict the real exam.
      </Alert>

      {exhausted ? (
        <CreditExhaustedNotice source="exam_topic_unlock" action="questions for this topic" />
      ) : null}
      {failure ? <GenerationError failure={failure} onRetry={() => void generate()} /> : null}

      <fieldset className={styles.group}>
        <div className={styles.panel}>
          <legend className={styles.legend}>
            Questions to work from ({total} found for this topic)
          </legend>
          <ul className={styles.list}>
          {listed.map((question) => (
            <li key={refKey(question)} className={styles.row}>
              <Checkbox
                checked={selected.has(refKey(question))}
                onChange={() =>
                  setSelected((current) => {
                    const next = new Set(current);
                    const key = refKey(question);
                    if (next.has(key)) next.delete(key);
                    else next.add(key);
                    return next;
                  })
                }
                label={
                  <span className={styles.label}>
                    <span className={styles.question}>{question.question_text}</span>
                    {/*
                      Five badges per row would drown the question itself. These
                      are facts about the source, so they read as quiet meta
                      text, and any the paper did not record is simply absent.
                    */}
                    <span className={styles.meta}>
                      {question.question_label ? <span>{question.question_label}</span> : null}
                      <span>{question.question_type.replace(/_/g, ' ')}</span>
                      {question.difficulty ? <span>{question.difficulty}</span> : null}
                      {question.marks !== null && question.marks !== undefined ? (
                        <span>
                          <span className="tabular">{question.marks}</span> marks
                        </span>
                      ) : null}
                      {question.subparts.length > 0 ? (
                        <span>
                          <span className="tabular">{question.subparts.length}</span> parts
                        </span>
                      ) : null}
                    </span>
                  </span>
                }
              />
              <CitationList citations={question.citations} />
            </li>
          ))}
          </ul>
        </div>
      </fieldset>

      <div className={styles.fields}>
        <Input
          label="How many to write"
          type="number"
          min={MIN_QUESTION_COUNT}
          max={MAX_QUESTION_COUNT}
          value={questionCount}
          error={count.error ?? undefined}
          onChange={(event) => setQuestionCount(event.target.value)}
        />
        <Select
          label="Difficulty"
          value={policy}
          onChange={(event) =>
            setPolicy(event.target.value as SimilarQuestionDifficultyPolicy)
          }
        >
          {POLICIES.map((entry) => (
            <option key={entry.value} value={entry.value}>
              {entry.label}
            </option>
          ))}
        </Select>
      </div>

      <div className={styles.actions}>
        <Button
          icon={<Shuffle aria-hidden="true" />}
          disabled={selected.size === 0 || count.error !== null}
          onClick={() => void generate()}
        >
          Write similar questions
        </Button>
        {selected.size === 0 ? (
          <p className={styles.hint}>Choose at least one question to work from.</p>
        ) : null}
      </div>
    </div>
  );
}
