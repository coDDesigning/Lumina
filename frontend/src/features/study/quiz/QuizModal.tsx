import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Award, Clock, Play, RotateCcw, Send } from 'lucide-react';
import {
  describeError,
  describeGenerationError,
  isAbortError,
  isInsufficientCredits,
} from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { quizAPI } from '@/api/quiz';
import { settingsAPI } from '@/api/settings';
import type {
  CreditSource,
  QuizAttemptResponse,
  QuizDifficulty,
  QuizQuestionType,
  QuizQuestionView,
  QuizView,
} from '@/api/types';
import { isOptionBased } from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { cx } from '@/lib/cx';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { Select } from '@/ui/Input';
import {
  GeneratingState,
  GenerationError,
  NoMaterialNotice,
  SetupPanel,
} from '../GenerationStates';
import { ALL_TOPICS, topicOptions } from '../topicOptions';
import { EMPTY_DRAFT } from './answerDraft';
import type { AnswerDraft } from './answerDraft';
import { QuizAnswerField } from './QuizAnswerField';
import { QuizResults } from './QuizResults';
import { formatDuration } from './quizScoring';
import styles from './QuizModal.module.css';

export interface QuizModalProps {
  onQuizReady?: (quizId: number) => void;
  courseId: number;
  topics: string[];
  readyDocumentCount: number;
  initialTopic?: string;
  onClose: () => void;
  onAttemptRecorded?: () => void;
}

interface QuizSetup {
  questionTypes: QuizQuestionType[];
  questionCount: number;
  difficulty: QuizDifficulty;
  topic: string;
  hasTimer: boolean;
  includeProfileContext: boolean;
}

type QuizStep = 'config' | 'generating' | 'solving' | 'submitting' | 'results' | 'error';

const SECONDS_PER_QUESTION = 60;
const QUESTION_COUNTS = [5, 10, 15, 20];
const LOW_TIME_SECONDS = 60;

const QUESTION_TYPE_OPTIONS: { value: QuizQuestionType; label: string; hint: string }[] = [
  { value: 'multiple_choice', label: 'Multiple choice', hint: 'Pick one of four' },
  { value: 'true_false', label: 'True or false', hint: 'Decide if a claim holds' },
  { value: 'short_answer', label: 'Short answer', hint: 'A word or a line' },
  { value: 'open_ended', label: 'Written answer', hint: 'Explain it in your own words' },
];

const QUESTION_TYPE_LABELS: Record<QuizQuestionType, string> = {
  multiple_choice: 'Multiple choice',
  true_false: 'True or false',
  short_answer: 'Short answer',
  open_ended: 'Written answer',
};

const DIFFICULTY_OPTIONS: { value: QuizDifficulty; label: string }[] = [
  { value: 'easy', label: 'Easy — recall and definitions' },
  { value: 'medium', label: 'Medium — apply what you know' },
  { value: 'hard', label: 'Hard — reason about edge cases' },
];

function isAnswered(draft: AnswerDraft | undefined): boolean {
  if (!draft) {
    return false;
  }
  return draft.optionIndex !== null || draft.text.trim().length > 0;
}

export function QuizModal({
  courseId,
  topics,
  readyDocumentCount,
  initialTopic,
  onClose,
  onAttemptRecorded,
  onQuizReady,
}: QuizModalProps) {
  const [step, setStep] = useState<QuizStep>('config');
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [setup, setSetup] = useState<QuizSetup>({
    questionTypes: ['multiple_choice'],
    questionCount: 5,
    difficulty: 'medium',
    topic: initialTopic ?? ALL_TOPICS,
    hasTimer: true,
    includeProfileContext: false,
  });

  const [quiz, setQuiz] = useState<QuizView | null>(null);
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Record<number, AnswerDraft>>({});
  const [timeLeft, setTimeLeft] = useState(0);
  const [attempt, setAttempt] = useState<QuizAttemptResponse | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const startedAtRef = useRef(0);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    let active = true;
    settingsAPI
      .get(courseId)
      .then((data) => {
        if (!active) {
          return;
        }
        const rawDifficulty = data.difficulty?.toLowerCase();
        const difficulty: QuizDifficulty =
          rawDifficulty === 'easy' ? 'easy' : rawDifficulty === 'hard' ? 'hard' : 'medium';

        const stored = data.question_count ?? 10;
        const questionCount =
          QUESTION_COUNTS.find((option) => stored <= option) ??
          QUESTION_COUNTS[QUESTION_COUNTS.length - 1];

        setSetup((previous) => ({ ...previous, difficulty, questionCount }));
      })
      .catch(() => {
        if (active) {
          setSetup((previous) => ({ ...previous, difficulty: 'medium', questionCount: 10 }));
        }
      });
    return () => {
      active = false;
    };
  }, [courseId]);

  useEffect(() => {
    if (step !== 'generating') {
      return;
    }
    setElapsed(0);
    const timer = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [step]);

  const questions: QuizQuestionView[] = useMemo(() => quiz?.questions ?? [], [quiz]);

  const submitAttempt = useCallback(async () => {
    if (!quiz) {
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const spent = Math.max(1, Math.round((Date.now() - startedAtRef.current) / 1000));
    setStep('submitting');

    try {
      const recorded = await quizAPI.submitAttempt(
        courseId,
        quiz.quiz_id,
        {
          answers: quiz.questions.map((question) => {
            const draft = answers[question.question_id] ?? EMPTY_DRAFT;
            if (isOptionBased(question.question_type)) {
              return {
                question_id: question.question_id,
                selected_option_index: draft.optionIndex,
              };
            }
            return {
              question_id: question.question_id,
              text_response: draft.text.trim() || null,
            };
          }),
          time_spent_seconds: spent,
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) {
        return;
      }
      setAttempt(recorded);
      setStep('results');
      onAttemptRecorded?.();
    } catch (caught) {
      if (controller.signal.aborted || isAbortError(caught)) {
        return;
      }
      const described = describeError(caught, 'Your answers could not be saved.');
      setFailure({ ...described, title: 'Your answers were not saved', retryable: true, remedy: null });
      setStep('error');
    }
  }, [answers, courseId, onAttemptRecorded, quiz]);

  useEffect(() => {
    if (step !== 'solving' || !setup.hasTimer) {
      return;
    }
    if (timeLeft <= 0) {
      void submitAttempt();
      return;
    }
    const timer = setTimeout(() => setTimeLeft((seconds) => seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [step, setup.hasTimer, timeLeft, submitAttempt]);

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

    setStep('generating');

    try {
      const generated = await quizAPI.generate(
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

      if (onQuizReady) {
        onQuizReady(generated.quiz.quiz_id);
        void refresh();
        return;
      }

      setQuiz(generated.quiz);
      setIndex(0);
      setAnswers({});
      setAttempt(null);
      setTimeLeft(generated.quiz.questions.length * SECONDS_PER_QUESTION);
      startedAtRef.current = Date.now();
      setStep('solving');
      void refresh();
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
    }
  }, [courseId, setup, refresh, onQuizReady]);

  const backToSetup = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setQuiz(null);
    setAttempt(null);
    setAnswers({});
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

  const question = questions[index];
  const answeredCount = questions.filter((row) => isAnswered(answers[row.question_id])).length;
  const unanswered = questions.length - answeredCount;
  const topicChoices = topicOptions(initialTopic ? [initialTopic, ...topics] : topics);
  const isLastQuestion = index === questions.length - 1;

  const footer = (() => {
    if (step === 'config') {
      return (
        <>
          <Button onClick={onClose}>Cancel</Button>
          <div className={styles.footerRight}>
            <CreditBalance source={quizSource} />
            <Button
              variant="primary"
              onClick={() => void startQuiz()}
              disabled={!hasMaterial || exhausted}
              icon={<Play aria-hidden="true" />}
            >
              Start the quiz
            </Button>
          </div>
        </>
      );
    }

    if (step === 'generating' || step === 'submitting') {
      return (
        <Button
          onClick={() => {
            abortRef.current?.abort();
            setStep(step === 'generating' ? 'config' : 'solving');
          }}
        >
          Cancel
        </Button>
      );
    }

    if (step === 'error') {
      return (
        <Button variant="primary" onClick={backToSetup}>
          Back to setup
        </Button>
      );
    }

    if (step === 'solving') {
      return (
        <>
          <Button onClick={() => setIndex((current) => current - 1)} disabled={index === 0}>
            Previous
          </Button>
          {isLastQuestion ? (
            <Button
              variant="primary"
              onClick={() => void submitAttempt()}
              icon={<Send aria-hidden="true" />}
            >
              Hand it in
            </Button>
          ) : (
            <Button variant="primary" onClick={() => setIndex((current) => current + 1)}>
              Next question
            </Button>
          )}
        </>
      );
    }

    return (
      <>
        <Button onClick={backToSetup} icon={<RotateCcw aria-hidden="true" />}>
          Another quiz
        </Button>
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      </>
    );
  })();

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
      dismissOnScrimClick={step !== 'solving'}
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
                label="Work against the clock"
                description="One minute a question. When time runs out the quiz is handed in as it stands."
                checked={setup.hasTimer}
                onChange={(event) => setSetup({ ...setup, hasTimer: event.target.checked })}
              />

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

      {step === 'generating' ? (
        <GeneratingState
          heading="Writing your questions"
          detail="Working through your course material. This usually takes twenty to sixty seconds."
          elapsed={elapsed}
        />
      ) : null}

      {step === 'submitting' ? (
        <GeneratingState
          heading="Marking your answers"
          detail="Checking what you wrote against the material."
          elapsed={0}
        />
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

      {step === 'solving' && question ? (
        <div className={styles.attempt}>
          <div className={styles.attemptHead}>
            <p className={styles.position}>
              Question <span className="tabular">{index + 1}</span> of{' '}
              <span className="tabular">{questions.length}</span>
            </p>
            {setup.hasTimer ? (
              <p
                className={cx(styles.timer, timeLeft <= LOW_TIME_SECONDS && styles.timerLow)}
                role="timer"
                aria-label={`Time remaining: ${formatDuration(timeLeft)}`}
              >
                <Clock aria-hidden="true" />
                <span className="tabular">{formatDuration(timeLeft)}</span>
              </p>
            ) : null}
          </div>

          <nav className={styles.navigator} aria-label="Questions">
            {questions.map((row, position) => (
              <button
                key={row.question_id}
                type="button"
                className={cx(
                  styles.pip,
                  position === index && styles.pipCurrent,
                  isAnswered(answers[row.question_id]) && styles.pipAnswered,
                )}
                aria-current={position === index ? 'true' : undefined}
                aria-label={`Question ${position + 1}${
                  isAnswered(answers[row.question_id]) ? ', answered' : ', not answered'
                }`}
                onClick={() => setIndex(position)}
              >
                <span aria-hidden="true">{position + 1}</span>
              </button>
            ))}
          </nav>

          <div className={styles.questionCard}>
            <div className={styles.questionMeta}>
              <span>{QUESTION_TYPE_LABELS[question.question_type]}</span>
              {question.topic ? <span>{question.topic}</span> : null}
            </div>
            <h3 className={styles.questionText}>{question.question}</h3>

            <QuizAnswerField
              question={question}
              draft={answers[question.question_id] ?? EMPTY_DRAFT}
              onChange={(draft) =>
                setAnswers((previous) => ({ ...previous, [question.question_id]: draft }))
              }
            />
          </div>

          {isLastQuestion && unanswered > 0 ? (
            <Alert tone="warning">
              {unanswered === 1
                ? 'One question is still unanswered.'
                : `${unanswered} questions are still unanswered.`}{' '}
              An unanswered question is marked wrong.
            </Alert>
          ) : null}
        </div>
      ) : null}

      {step === 'results' && attempt ? (
        <QuizResults attempt={attempt} questions={questions} />
      ) : null}
    </Dialog>
  );
}

export default QuizModal;
