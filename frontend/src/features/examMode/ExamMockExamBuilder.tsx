import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Timer } from 'lucide-react';
import { examModeAPI } from '@/api/examMode';
import { describeGenerationError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { afterExamMockExam } from '@/api/invalidations';
import type { ExamPlanView, QuizQuestionType } from '@/api/types';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { GeneratingState, GenerationError } from '@/features/study/GenerationStates';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Input, Select } from '@/ui/Input';
import { useElapsed } from './useElapsed';
import styles from './ExamMockExamBuilder.module.css';

/** Exactly the four types the quiz engine can persist. */
const QUESTION_TYPES: { value: QuizQuestionType; label: string }[] = [
  { value: 'multiple_choice', label: 'Multiple choice' },
  { value: 'true_false', label: 'True or false' },
  { value: 'short_answer', label: 'Short answer' },
  { value: 'open_ended', label: 'Written answer' },
];

const DURATIONS = [15, 30, 45, 60, 90, 120, 180];

export interface ExamMockExamBuilderProps {
  courseId: number;
  plan: ExamPlanView;
}

/**
 * Configure a full paper, then hand off to the shared quiz screen.
 *
 * The clock does not start here. Generating writes the paper; the student then
 * reads the instructions and begins the sitting deliberately, because a timer
 * that started while they were still reading settings would be dishonest.
 */
export function ExamMockExamBuilder({ courseId, plan }: ExamMockExamBuilderProps) {
  const navigate = useNavigate();
  const { isMetered, canAfford } = useCredits();

  const [questionCount, setQuestionCount] = useState(6);
  const [durationMinutes, setDurationMinutes] = useState(60);
  const [types, setTypes] = useState<ReadonlySet<QuizQuestionType>>(
    new Set<QuizQuestionType>(['multiple_choice']),
  );
  const [topicKeys, setTopicKeys] = useState<ReadonlySet<string>>(
    new Set(plan.topics.map((topic) => topic.topic_key)),
  );
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [exhausted, setExhausted] = useState(false);
  const elapsed = useElapsed(busy);

  // The backend is authoritative, but a paper that cannot cover every topic it
  // was asked for is a 422, so the form says so before it spends a request.
  const tooFewQuestions = topicKeys.size > questionCount;
  const noTypes = types.size === 0;
  const noTopics = topicKeys.size === 0;
  const invalid = tooFewQuestions || noTypes || noTopics;

  const mix = useMemo(() => {
    const chosen = QUESTION_TYPES.filter((entry) => types.has(entry.value));
    if (chosen.length === 0) return [];
    const base = Math.floor(questionCount / chosen.length);
    const counts = chosen.map(() => base);
    for (let index = 0; index < questionCount - base * chosen.length; index += 1) {
      counts[index] += 1;
    }
    return chosen
      .map((entry, index) => ({ question_type: entry.value, count: counts[index] }))
      .filter((entry) => entry.count > 0);
  }, [types, questionCount]);

  function toggleType(value: QuizQuestionType) {
    setTypes((current) => {
      const next = new Set(current);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  function toggleTopic(value: string) {
    setTopicKeys((current) => {
      const next = new Set(current);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }

  async function generate() {
    if (isMetered && !canAfford('exam_mock_exam')) {
      setExhausted(true);
      return;
    }
    setBusy(true);
    setFailure(null);
    setExhausted(false);
    try {
      const result = await examModeAPI.generateMockExam(courseId, {
        plan_output_id: plan.generated_output_id,
        question_count: questionCount,
        duration_minutes: durationMinutes,
        question_mix: mix,
        topic_keys: [...topicKeys],
      });
      afterExamMockExam(courseId);
      navigate(`/courses/${courseId}/practice/${result.quiz.quiz_id}`);
    } catch (error) {
      const described = describeGenerationError(error, 'That paper could not be written.');
      if (isInsufficientCredits(described)) setExhausted(true);
      else setFailure(described);
    } finally {
      setBusy(false);
    }
  }

  if (busy) {
    return (
      <GeneratingState
        heading="Writing your paper"
        detail="Setting questions across the topics you chose, in the style of this course."
        elapsed={elapsed}
      />
    );
  }

  return (
    <div className={styles.builder}>
      {exhausted ? (
        <CreditExhaustedNotice source="exam_mock_exam" action="a mock exam" />
      ) : null}
      {failure ? <GenerationError failure={failure} onRetry={() => void generate()} /> : null}

      <div className={styles.panel}>
        <div className={styles.fields}>
          <Input
            label="Questions"
            type="number"
            min={1}
            max={20}
            value={questionCount}
            onChange={(event) => setQuestionCount(Number(event.target.value))}
          />
          <Select
            label="Time allowed"
            value={String(durationMinutes)}
            onChange={(event) => setDurationMinutes(Number(event.target.value))}
          >
            {DURATIONS.map((minutes) => (
              <option key={minutes} value={minutes}>
                {minutes} minutes
              </option>
            ))}
          </Select>
        </div>

        <fieldset className={styles.group}>
          <legend className={styles.legend}>Question types</legend>
          <div className={styles.options}>
            {QUESTION_TYPES.map((entry) => (
              <Checkbox
                key={entry.value}
                label={entry.label}
                checked={types.has(entry.value)}
                onChange={() => toggleType(entry.value)}
              />
            ))}
          </div>
        </fieldset>

        <fieldset className={styles.group}>
          <legend className={styles.legend}>Topics covered</legend>
          <div className={styles.options}>
            {plan.topics.map((topic) => (
              <Checkbox
                key={topic.topic_key}
                label={topic.display_label}
                checked={topicKeys.has(topic.topic_key)}
                onChange={() => toggleTopic(topic.topic_key)}
              />
            ))}
          </div>
        </fieldset>
      </div>

      {invalid ? (
        <Alert tone="warning" title="This paper cannot be built yet">
          {noTopics
            ? 'Choose at least one topic for the paper to cover.'
            : noTypes
              ? 'Choose at least one question type.'
              : `Every topic gets at least one question, so ${topicKeys.size} topics need at least ${topicKeys.size} questions.`}
        </Alert>
      ) : null}

      <div className={styles.actions}>
        <Button icon={<Timer aria-hidden="true" />} disabled={invalid} onClick={() => void generate()}>
          Write the paper
        </Button>
        <p className={styles.hint}>
          The clock starts when you begin the sitting, not when the paper is written.
        </p>
      </div>
    </div>
  );
}
