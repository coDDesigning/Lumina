import { useCallback, useEffect, useRef, useState } from 'react';
import { Award, Play } from 'lucide-react';
import { describeGenerationError, isAbortError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import { useCourseSettings } from '@/features/courses/useCourseSettings';
import type { CreditSource, QuizDifficulty, QuizQuestionType } from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { Select } from '@/ui/Input';
import { GenerationError, NoMaterialNotice, SetupPanel } from '../GenerationStates';
import { ALL_TOPICS, topicOptions } from '../topicOptions';
import styles from './QuizModal.module.css';

export interface QuizModalProps {
  onQueued: (jobId: number) => void;
  courseId: number;
  topics: string[];
  readyDocumentCount: number;
  initialTopic?: string;
  onClose: () => void;
}

interface QuizSetup {
  questionTypes: QuizQuestionType[];
  questionCount: number;
  difficulty: QuizDifficulty;
  topic: string;
  includeProfileContext: boolean;
}

type QuizStep = 'config' | 'error';

const QUESTION_COUNTS = [5, 10, 15, 20];

const QUESTION_TYPE_OPTIONS: { value: QuizQuestionType; label: string; hint: string }[] = [
  { value: 'multiple_choice', label: 'Multiple choice', hint: 'Pick one of four' },
  { value: 'true_false', label: 'True or false', hint: 'Decide if a claim holds' },
  { value: 'short_answer', label: 'Short answer', hint: 'A word or a line' },
  { value: 'open_ended', label: 'Written answer', hint: 'Explain it in your own words' },
];

const DIFFICULTY_OPTIONS: { value: QuizDifficulty; label: string }[] = [
  { value: 'easy', label: 'Easy — recall and definitions' },
  { value: 'medium', label: 'Medium — apply what you know' },
  { value: 'hard', label: 'Hard — reason about edge cases' },
];

export function QuizModal({
  courseId,
  topics,
  readyDocumentCount,
  initialTopic,
  onClose,
  onQueued,
}: QuizModalProps) {
  const [step, setStep] = useState<QuizStep>('config');
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [setup, setSetup] = useState<QuizSetup>({
    questionTypes: ['multiple_choice'],
    questionCount: 5,
    difficulty: 'medium',
    topic: initialTopic ?? ALL_TOPICS,
    includeProfileContext: false,
  });
  const [isQueueing, setIsQueueing] = useState(false);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const settings = useCourseSettings(courseId);
  const defaults = settings.status === 'success' ? settings.data : undefined;

  useEffect(() => {
    if (!defaults) {
      return;
    }
    const rawDifficulty = defaults.difficulty?.toLowerCase();
    const difficulty: QuizDifficulty =
      rawDifficulty === 'easy' ? 'easy' : rawDifficulty === 'hard' ? 'hard' : 'medium';

    const stored = defaults.question_count ?? 10;
    const questionCount =
      QUESTION_COUNTS.find((option) => stored <= option) ??
      QUESTION_COUNTS[QUESTION_COUNTS.length - 1];

    setSetup((previous) => ({ ...previous, difficulty, questionCount }));
  }, [defaults]);

  const { refresh, canAfford, costOf, isMetered } = useCredits();
  const quizSource: CreditSource = setup.questionTypes.includes('open_ended')
    ? 'quiz_open_ended'
    : 'quiz';
  const exhausted = isMetered && !canAfford(quizSource);
  const quizCost = isMetered ? costOf(quizSource) : null;
  const hasMaterial = readyDocumentCount > 0;

  const startQuiz = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setFailure(null);
    setIsQueueing(true);

    try {
      const accepted = await quizAPI.enqueue(
        courseId,
        {
          question_count: setup.questionCount,
          question_types: setup.questionTypes,
          difficulty: setup.difficulty,
          topic_focus: setup.topic,
          use_profile_knowledge: setup.includeProfileContext,
          include_profile_context: setup.includeProfileContext,
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) {
        return;
      }

      await refresh();
      onQueued(accepted.job_id);
      onClose();
    } catch (caught) {
      if (controller.signal.aborted || isAbortError(caught)) {
        return;
      }
      const described = describeGenerationError(caught, 'The quiz could not be written.');
      if (isInsufficientCredits(described)) {
        await refresh();
        setStep('config');
        return;
      }
      setFailure(described);
      setStep('error');
    } finally {
      if (!controller.signal.aborted) setIsQueueing(false);
    }
  }, [courseId, onClose, onQueued, refresh, setup]);

  const backToSetup = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setFailure(null);
    setStep('config');
  };

  const toggleQuestionType = (type: QuizQuestionType, checked: boolean) => {
    setSetup((previous) => {
      const next = checked
        ? [...previous.questionTypes, type]
        : previous.questionTypes.filter((value) => value !== type);
      return { ...previous, questionTypes: next.length > 0 ? next : previous.questionTypes };
    });
  };

  const topicChoices = topicOptions(initialTopic ? [initialTopic, ...topics] : topics);

  const footer =
    step === 'config' ? (
      <>
        <Button onClick={onClose}>Cancel</Button>
        <div className={styles.footerRight}>
          <CreditBalance source={quizSource} />
          <Button
            variant="primary"
            onClick={() => void startQuiz()}
            disabled={!hasMaterial || exhausted || isQueueing}
            isLoading={isQueueing}
            loadingLabel="Queueing quiz"
            icon={<Play aria-hidden="true" />}
          >
            Start the quiz
          </Button>
        </div>
      </>
    ) : (
      <Button variant="primary" onClick={backToSetup}>
        Back to setup
      </Button>
    );

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Practice quiz"
      description="Questions written from the material in this course"
      mark={<Award aria-hidden="true" />}
      footer={footer}
      spreadFooter
    >
      {step === 'config' ? (
        <SetupPanel lede="Answer questions drawn from your own material, then see where you stand.">
          {hasMaterial ? (
            <>
              <fieldset className={styles.types}>
                <legend className={styles.legend}>What to ask</legend>
                <div className={styles.typeGrid}>
                  {QUESTION_TYPE_OPTIONS.map((option) => (
                    <Checkbox
                      key={option.value}
                      label={option.label}
                      description={option.hint}
                      checked={setup.questionTypes.includes(option.value)}
                      onChange={(event) => toggleQuestionType(option.value, event.target.checked)}
                    />
                  ))}
                </div>
              </fieldset>

              <div className={styles.grid}>
                <Select
                  label="How many questions"
                  value={String(setup.questionCount)}
                  onChange={(event) =>
                    setSetup({ ...setup, questionCount: Number(event.target.value) })
                  }
                >
                  {QUESTION_COUNTS.map((count) => (
                    <option key={count} value={count}>
                      {count} questions
                    </option>
                  ))}
                </Select>

                <Select
                  label="Which topic"
                  value={setup.topic}
                  onChange={(event) => setSetup({ ...setup, topic: event.target.value })}
                >
                  {topicChoices.map((topic) => (
                    <option key={topic} value={topic}>
                      {topic}
                    </option>
                  ))}
                </Select>

                <Select
                  label="How hard"
                  value={setup.difficulty}
                  onChange={(event) =>
                    setSetup({ ...setup, difficulty: event.target.value as QuizDifficulty })
                  }
                >
                  {DIFFICULTY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>

              <Checkbox
                label="Use my study profile"
                description="Adds your background as supporting context. Your course material stays primary."
                checked={setup.includeProfileContext}
                onChange={(event) =>
                  setSetup({ ...setup, includeProfileContext: event.target.checked })
                }
              />

              {quizCost !== null ? (
                <p className={styles.cost}>
                  {setup.questionTypes.includes('open_ended')
                    ? `Written answers are marked by the model, so this quiz costs ${quizCost}.`
                    : `This quiz costs ${quizCost}.`}
                </p>
              ) : null}
            </>
          ) : (
            <NoMaterialNotice what="A quiz" />
          )}
        </SetupPanel>
      ) : null}

      {step === 'config' && exhausted ? (
        <CreditExhaustedNotice source={quizSource} action="a quiz" />
      ) : null}

      {step === 'error' && failure ? (
        <GenerationError
          failure={failure}
          onRetry={() => void startQuiz()}
          onBroadenTopic={() => {
            setSetup((previous) => ({ ...previous, topic: ALL_TOPICS }));
            setStep('config');
          }}
          onSeeSources={onClose}
        />
      ) : null}
    </Dialog>
  );
}

export default QuizModal;
