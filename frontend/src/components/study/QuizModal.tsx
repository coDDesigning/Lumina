import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Award,
  CheckCircle2,
  Clock,
  HelpCircle,
  Play,
  RotateCcw,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react';
import { quizAPI } from '../../api/quiz';
import { describeError, describeGenerationError, isAbortError } from '../../api/errors';
import type {
  QuizAnswerResult,
  QuizAttemptResponse,
  QuizDifficulty,
  QuizQuestionType,
  QuizQuestionView,
  QuizView,
} from '../../api/types';
import { isOptionBased } from '../../api/types';
import './study.css';

interface QuizModalProps {
  courseId: number;
  topics: string[];
  readyDocumentCount: number;
  onClose: () => void;
  onAttemptRecorded?: () => void;
}

interface QuizSetup {
  questionTypes: QuizQuestionType[];
  questionCount: number;
  difficulty: QuizDifficulty;
  topic: string;
  hasTimer: boolean;
}

/** One in-progress answer, in whichever form its question type calls for. */
interface AnswerDraft {
  optionIndex: number | null;
  text: string;
}

type QuizStep =
  | 'config'
  | 'generating'
  | 'solving'
  | 'submitting'
  | 'results'
  | 'error';

const ALL_TOPICS = 'All Topics';
const SECONDS_PER_QUESTION = 60;
const QUESTION_COUNTS = [5, 10, 15, 20];

const QUESTION_TYPE_OPTIONS: { value: QuizQuestionType; label: string }[] = [
  { value: 'multiple_choice', label: 'Multiple Choice (4 options)' },
  { value: 'true_false', label: 'True / False' },
  { value: 'short_answer', label: 'Short Answer' },
  { value: 'open_ended', label: 'Open Ended' },
];

const QUESTION_TYPE_LABELS: Record<QuizQuestionType, string> = {
  multiple_choice: 'Multiple Choice',
  true_false: 'True / False',
  short_answer: 'Short Answer',
  open_ended: 'Open Ended',
};

const EMPTY_DRAFT: AnswerDraft = { optionIndex: null, text: '' };

// Matches MAX_ANSWER_TEXT_CHARS in schemas/quiz_attempt.py.
const MAX_ANSWER_TEXT_CHARS = 5000;

function reviewStatusClass(isCorrect: boolean | null): string {
  if (isCorrect === null) return 'ungraded';
  return isCorrect ? 'correct' : 'incorrect';
}

function describeSubmittedAnswer(
  question: QuizQuestionView,
  answer: QuizAnswerResult,
): string {
  if (answer.text_response) return answer.text_response;
  if (answer.selected_option_index !== null && question.options) {
    return question.options[answer.selected_option_index] ?? 'Unanswered';
  }
  return 'Unanswered';
}

function describeCorrectAnswer(question: QuizQuestionView): string {
  const answer = question.correct_answer;
  if (!answer) {
    if (question.correct_option_index !== null && question.options) {
      return question.options[question.correct_option_index] ?? '';
    }
    return '';
  }
  switch (answer.type) {
    case 'multiple_choice':
      return question.options?.[answer.option_index] ?? '';
    case 'true_false':
      return answer.value ? 'True' : 'False';
    case 'short_answer':
      return answer.text;
    case 'open_ended':
      return answer.reference_answer;
  }
}

function formatTime(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
}

export function QuizModal({
  courseId,
  topics,
  readyDocumentCount,
  onClose,
  onAttemptRecorded,
}: QuizModalProps) {
  const [step, setStep] = useState<QuizStep>('config');
  const [errorMessage, setErrorMessage] = useState('');
  const [setup, setSetup] = useState<QuizSetup>({
    questionTypes: ['multiple_choice'],
    questionCount: 5,
    difficulty: 'medium',
    topic: ALL_TOPICS,
    hasTimer: true,
  });

  const [quiz, setQuiz] = useState<QuizView | null>(null);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [userAnswers, setUserAnswers] = useState<Record<number, AnswerDraft>>({});
  const [timeLeft, setTimeLeft] = useState(0);
  const [attempt, setAttempt] = useState<QuizAttemptResponse | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const startedAtRef = useRef<number>(0);

  useEffect(() => () => abortRef.current?.abort(), []);

  const questions: QuizQuestionView[] = quiz?.questions ?? [];

  const submitAttempt = useCallback(async () => {
    if (!quiz) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const elapsed = Math.max(
      1,
      Math.round((Date.now() - startedAtRef.current) / 1000),
    );

    setStep('submitting');

    try {
      const recorded = await quizAPI.submitAttempt(
        courseId,
        quiz.quiz_id,
        {
          answers: quiz.questions.map((question) => {
            const draft = userAnswers[question.question_id] ?? EMPTY_DRAFT;
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
          time_spent_seconds: elapsed,
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setAttempt(recorded);
      setStep('results');
      onAttemptRecorded?.();
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) return;
      setErrorMessage(
        describeError(error, 'The quiz results could not be saved.').message,
      );
      setStep('error');
    }
  }, [courseId, onAttemptRecorded, quiz, userAnswers]);

  useEffect(() => {
    if (step !== 'solving' || !setup.hasTimer) return;
    if (timeLeft <= 0) {
      void submitAttempt();
      return;
    }
    const timer = setTimeout(() => setTimeLeft((seconds) => seconds - 1), 1000);
    return () => clearTimeout(timer);
  }, [step, setup.hasTimer, timeLeft, submitAttempt]);

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
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;

      setQuiz(generated.quiz);
      setCurrentQuestionIndex(0);
      setUserAnswers({});
      setAttempt(null);
      setTimeLeft(generated.quiz.questions.length * SECONDS_PER_QUESTION);
      startedAtRef.current = Date.now();
      setStep('solving');
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) return;
      setErrorMessage(
        describeGenerationError(error, 'The quiz could not be generated.').message,
      );
      setStep('error');
    }
  }, [courseId, setup]);

  const resetToConfig = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    setQuiz(null);
    setAttempt(null);
    setUserAnswers({});
    setErrorMessage('');
    setStep('config');
  };

  const currentQuestion = questions[currentQuestionIndex];
  const progressPercentage =
    questions.length > 0
      ? Math.round(((currentQuestionIndex + 1) / questions.length) * 100)
      : 0;

  const topicOptions = [ALL_TOPICS, ...topics];
  const hasMaterial = readyDocumentCount > 0;
  const scorePercentage = attempt ? Math.round(attempt.score * 100) : 0;

  return (
    <div className="study-modal-backdrop" role="dialog" aria-modal="true">
      <div className="study-modal large-modal">
        <header className="study-modal-header">
          <div>
            <h2>
              <Award aria-hidden="true" />
              Interactive Practice Quiz
            </h2>
            <p>Test your conceptual understanding and master exam topics</p>
          </div>
          <button
            className="modal-close-button"
            type="button"
            onClick={onClose}
            aria-label="Close quiz modal"
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="study-modal-body">
          {step === 'generating' || step === 'submitting' ? (
            <div className="study-loading-state">
              <div className="study-pulse-spinner" />
              <h3>
                {step === 'generating' ? 'Building Your Quiz' : 'Scoring Your Answers'}
              </h3>
              <p>
                {step === 'generating'
                  ? 'Writing questions from your course material. This usually takes 20-60 seconds.'
                  : 'Recording your attempt.'}
              </p>
            </div>
          ) : null}

          {step === 'error' ? (
            <div className="summary-container">
              <div className="summary-section-card summary-notice is-danger" role="alert">
                <h4>
                  <XCircle aria-hidden="true" />
                  Something went wrong
                </h4>
                <p>{errorMessage}</p>
              </div>
            </div>
          ) : null}

          {step === 'config' ? (
            <div className="summary-container">
              <div className="summary-section-card">
                <h4>
                  <HelpCircle aria-hidden="true" />
                  Configure Your Quiz Session
                </h4>
                <p className="summary-hint">
                  Lumina generates high-yield questions derived directly from your
                  processed course material.
                </p>

                <div className="study-form-grid">
                  <div className="study-field-group">
                    <label htmlFor="quiz-topic">Target Topic</label>
                    <select
                      id="quiz-topic"
                      value={setup.topic}
                      onChange={(event) =>
                        setSetup({ ...setup, topic: event.target.value })
                      }
                    >
                      {topicOptions.map((topic) => (
                        <option key={topic} value={topic}>
                          {topic}
                        </option>
                      ))}
                    </select>
                  </div>

                  <fieldset className="study-field-group">
                    <legend>Question Types</legend>
                    {QUESTION_TYPE_OPTIONS.map(({ value, label }) => {
                      const checked = setup.questionTypes.includes(value);
                      return (
                        <label key={value} className="study-toggle-label">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() =>
                              setSetup((previous) => {
                                const next = checked
                                  ? previous.questionTypes.filter(
                                      (item) => item !== value,
                                    )
                                  : [...previous.questionTypes, value];
                                return next.length === 0
                                  ? previous
                                  : { ...previous, questionTypes: next };
                              })
                            }
                          />
                          <span>{label}</span>
                        </label>
                      );
                    })}
                  </fieldset>

                  <div className="study-field-group">
                    <label htmlFor="question-count">Number of Questions</label>
                    <select
                      id="question-count"
                      value={setup.questionCount}
                      onChange={(event) =>
                        setSetup({
                          ...setup,
                          questionCount: Number(event.target.value),
                        })
                      }
                    >
                      {QUESTION_COUNTS.map((count) => (
                        <option key={count} value={count}>
                          {count} Questions
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="difficulty">Difficulty Level</label>
                    <select
                      id="difficulty"
                      value={setup.difficulty}
                      onChange={(event) =>
                        setSetup({
                          ...setup,
                          difficulty: event.target.value as QuizDifficulty,
                        })
                      }
                    >
                      <option value="easy">Easy (Concept definitions)</option>
                      <option value="medium">Medium (Analytical application)</option>
                      <option value="hard">Hard (Edge-case boundary reasoning)</option>
                    </select>
                  </div>
                </div>

                <label className="study-toggle-label">
                  <input
                    type="checkbox"
                    checked={setup.hasTimer}
                    onChange={(event) =>
                      setSetup({ ...setup, hasTimer: event.target.checked })
                    }
                  />
                  <span>Enable Exam Countdown Timer (1 min per question)</span>
                </label>

                {!hasMaterial ? (
                  <p className="summary-empty-state">
                    This course has no processed material yet. Add a source and wait until
                    it shows Ready.
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {step === 'solving' && currentQuestion ? (
            <div className="summary-container">
              <div className="quiz-solver-header">
                <span className="quiz-progress-indicator">
                  Question {currentQuestionIndex + 1} of {questions.length}
                </span>
                {setup.hasTimer ? (
                  <span className="quiz-timer">
                    <Clock aria-hidden="true" />
                    {formatTime(timeLeft)}
                  </span>
                ) : null}
              </div>

              <div
                className="quiz-progress-bar"
                role="progressbar"
                aria-valuenow={progressPercentage}
                aria-valuemin={0}
                aria-valuemax={100}
              >
                <div
                  className="quiz-progress-bar-fill"
                  style={{ width: `${progressPercentage}%` }}
                />
              </div>

              <div className="quiz-question-box">
                {currentQuestion.topic ? (
                  <span className="summary-meta-badge">{currentQuestion.topic}</span>
                ) : null}
                <span className="summary-meta-badge">
                  {QUESTION_TYPE_LABELS[currentQuestion.question_type]}
                </span>
                <h3>{currentQuestion.question}</h3>

                {currentQuestion.options ? (
                  <div className="quiz-options-list">
                    {currentQuestion.options.map((option, index) => {
                      const isSelected =
                        userAnswers[currentQuestion.question_id]?.optionIndex === index;
                      return (
                        <button
                          key={index}
                          type="button"
                          className={`quiz-option-btn${isSelected ? ' selected' : ''}`}
                          onClick={() =>
                            setUserAnswers((previous) => ({
                              ...previous,
                              [currentQuestion.question_id]: {
                                optionIndex: index,
                                text: '',
                              },
                            }))
                          }
                        >
                          <span className="option-letter">
                            {String.fromCharCode(65 + index)}
                          </span>
                          <span>{option}</span>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <textarea
                    className="quiz-answer-input"
                    aria-label="Your answer"
                    maxLength={MAX_ANSWER_TEXT_CHARS}
                    rows={currentQuestion.question_type === 'open_ended' ? 6 : 2}
                    placeholder={
                      currentQuestion.question_type === 'open_ended'
                        ? 'Explain your reasoning...'
                        : 'Type your answer...'
                    }
                    value={userAnswers[currentQuestion.question_id]?.text ?? ''}
                    onChange={(event) =>
                      setUserAnswers((previous) => ({
                        ...previous,
                        [currentQuestion.question_id]: {
                          optionIndex: null,
                          text: event.target.value,
                        },
                      }))
                    }
                  />
                )}
              </div>
            </div>
          ) : null}

          {step === 'results' && attempt ? (
            <div className="quiz-results-container">
              <div className="score-hero-card">
                <div className="score-hero-left">
                  <h3>
                    {scorePercentage >= 80
                      ? 'Outstanding Mastery!'
                      : scorePercentage >= 60
                        ? 'Solid Performance!'
                        : 'Needs Further Review'}
                  </h3>
                  <p>
                    You answered {attempt.correct_count} out of {attempt.total_questions}{' '}
                    questions correctly
                    {attempt.time_spent_seconds !== null
                      ? ` in ${Math.floor(attempt.time_spent_seconds / 60)}m ${
                          attempt.time_spent_seconds % 60
                        }s`
                      : ''}
                    .
                  </p>
                </div>
                <div className="score-circle-badge">
                  {scorePercentage}%<span>Score</span>
                </div>
              </div>

              <div className="quiz-stats-row">
                <div className="quiz-stat-card">
                  <strong className="stat-correct">{attempt.correct_count}</strong>
                  <span>Correct</span>
                </div>
                <div className="quiz-stat-card">
                  <strong className="stat-incorrect">
                    {attempt.total_questions - attempt.correct_count}
                  </strong>
                  <span>Incorrect</span>
                </div>
                <div className="quiz-stat-card">
                  <strong className="stat-total">{attempt.total_questions}</strong>
                  <span>Total Questions</span>
                </div>
              </div>

              <div className="summary-section-card">
                <h4>
                  <HelpCircle aria-hidden="true" />
                  Detailed Question Analysis & Explanations
                </h4>
                <div className="result-review-list">
                  {attempt.answers.map((answer, index) => {
                    const question = questions.find(
                      (row) => row.question_id === answer.question_id,
                    );
                    if (!question) return null;
                    return (
                      <div
                        key={answer.question_id}
                        className={`review-item ${reviewStatusClass(answer.is_correct)}`}
                      >
                        <div className="review-header">
                          <h4>
                            {index + 1}. {question.question}
                          </h4>
                          {answer.is_correct === null ? (
                            <HelpCircle aria-hidden="true" className="review-icon-bad" />
                          ) : answer.is_correct ? (
                            <CheckCircle2 aria-hidden="true" className="review-icon-ok" />
                          ) : (
                            <XCircle aria-hidden="true" className="review-icon-bad" />
                          )}
                        </div>

                        <div className="review-answers">
                          <div>
                            <strong>Your Answer: </strong>
                            <span
                              className={
                                answer.is_correct ? 'answer-correct' : 'answer-incorrect'
                              }
                            >
                              {describeSubmittedAnswer(question, answer)}
                            </span>
                          </div>
                          {answer.is_correct === null ? (
                            <div>
                              <strong>Not graded: </strong>
                              <span>This answer could not be scored automatically.</span>
                            </div>
                          ) : !answer.is_correct ? (
                            <div>
                              <strong>
                                {question.question_type === 'open_ended'
                                  ? 'Reference Answer: '
                                  : 'Correct Answer: '}
                              </strong>
                              <span className="answer-correct">
                                {describeCorrectAnswer(question)}
                              </span>
                            </div>
                          ) : null}
                        </div>

                        {answer.feedback ? (
                          <div className="review-explanation">
                            <strong>Feedback: </strong>
                            {answer.feedback}
                          </div>
                        ) : null}

                        {question.explanation ? (
                          <div className="review-explanation">
                            <strong>Rationale: </strong>
                            {question.explanation}
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          ) : null}
        </div>

        <footer className="study-modal-footer">
          {step === 'config' ? (
            <>
              <button className="secondary-button" type="button" onClick={onClose}>
                Cancel
              </button>
              <button
                className="primary-button"
                type="button"
                onClick={startQuiz}
                disabled={!hasMaterial}
              >
                <Play aria-hidden="true" />
                Start Quiz
              </button>
            </>
          ) : null}

          {step === 'generating' || step === 'submitting' ? (
            <button
              className="secondary-button"
              type="button"
              onClick={() => {
                abortRef.current?.abort();
                setStep(step === 'generating' ? 'config' : 'solving');
              }}
            >
              Cancel
            </button>
          ) : null}

          {step === 'error' ? (
            <>
              <button className="secondary-button" type="button" onClick={onClose}>
                Close
              </button>
              <button className="primary-button" type="button" onClick={resetToConfig}>
                Back to setup
              </button>
            </>
          ) : null}

          {step === 'solving' ? (
            <>
              <button
                className="secondary-button"
                type="button"
                disabled={currentQuestionIndex === 0}
                onClick={() => setCurrentQuestionIndex((index) => index - 1)}
              >
                Previous
              </button>
              {currentQuestionIndex < questions.length - 1 ? (
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => setCurrentQuestionIndex((index) => index + 1)}
                >
                  Next Question
                </button>
              ) : (
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => void submitAttempt()}
                >
                  <Sparkles aria-hidden="true" />
                  Submit Quiz
                </button>
              )}
            </>
          ) : null}

          {step === 'results' ? (
            <>
              <button className="secondary-button" type="button" onClick={resetToConfig}>
                <RotateCcw aria-hidden="true" />
                New Quiz Setup
              </button>
              <button className="primary-button" type="button" onClick={onClose}>
                Done
              </button>
            </>
          ) : null}
        </footer>
      </div>
    </div>
  );
}
