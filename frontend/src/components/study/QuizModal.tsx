import React, { useState, useEffect, useCallback } from 'react';
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
import {
  QuizConfig,
  QuizQuestion,
  QuizResult,
  generateMockQuestions,
} from '../../data/mockStudyData';
import './study.css';

interface QuizModalProps {
  isOpen: boolean;
  onClose: () => void;
  courseName: string;
  topics: string[];
  onQuizCompleted?: (result: QuizResult) => void;
}

type QuizStep = 'config' | 'solving' | 'results';

export const QuizModal: React.FC<QuizModalProps> = ({
  isOpen,
  onClose,
  courseName,
  topics,
  onQuizCompleted,
}) => {
  const [step, setStep] = useState<QuizStep>('config');
  const [config, setConfig] = useState<QuizConfig>({
    questionType: 'multiple_choice',
    questionCount: 5,
    difficulty: 'medium',
    topic: 'All Topics',
    hasTimer: true,
    timeLimitSeconds: 300,
  });

  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [userAnswers, setUserAnswers] = useState<{ [questionId: number]: number }>({});
  const [timeLeft, setTimeLeft] = useState(300);
  const [isTimerRunning, setIsTimerRunning] = useState(false);
  const [result, setResult] = useState<QuizResult | null>(null);

  const handleSubmitQuiz = useCallback(() => {
    setIsTimerRunning(false);

    let correct = 0;
    questions.forEach((q) => {
      if (userAnswers[q.id] === q.correctAnswer) {
        correct += 1;
      }
    });

    const total = questions.length;
    const scorePercentage = total > 0 ? Math.round((correct / total) * 100) : 0;
    const timeSpentSeconds = config.questionCount * 60 - timeLeft;

    const finalResult: QuizResult = {
      totalQuestions: total,
      correctCount: correct,
      incorrectCount: total - correct,
      scorePercentage,
      timeSpentSeconds: Math.max(1, timeSpentSeconds),
      userAnswers,
      questions,
      completedAt: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setResult(finalResult);
    setStep('results');
    if (onQuizCompleted) {
      onQuizCompleted(finalResult);
    }
  }, [config.questionCount, onQuizCompleted, questions, timeLeft, userAnswers]);

  // Timer effect
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    if (step === 'solving' && isTimerRunning && config.hasTimer && timeLeft > 0) {
      timer = setInterval(() => {
        setTimeLeft((prev) => {
          if (prev <= 1) {
            if (timer) clearInterval(timer);
            handleSubmitQuiz();
            return 0;
          }
          return prev - 1;
        });
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [step, isTimerRunning, config.hasTimer, timeLeft, handleSubmitQuiz]);

  if (!isOpen) return null;

  const handleStartQuiz = () => {
    const generated = generateMockQuestions(courseName, topics, config);
    setQuestions(generated);
    setCurrentQuestionIndex(0);
    setUserAnswers({});
    setTimeLeft(config.questionCount * 60);
    setIsTimerRunning(true);
    setStep('solving');
  };

  const handleSelectOption = (questionId: number, optionIndex: number) => {
    setUserAnswers((prev) => ({
      ...prev,
      [questionId]: optionIndex,
    }));
  };

  const handleRetake = () => {
    handleStartQuiz();
  };

  const handleResetToConfig = () => {
    setResult(null);
    setStep('config');
  };

  const currentQ = questions[currentQuestionIndex];
  const progressPercentage =
    questions.length > 0 ? Math.round(((currentQuestionIndex + 1) / questions.length) * 100) : 0;

  const formatTime = (secs: number) => {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const topicOptions = ['All Topics', ...(topics.length > 0 ? topics : ['Core Concepts', 'Methodology', 'Applications'])];

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
          {step === 'config' && (
            <div className="summary-container">
              <div className="summary-section-card">
                <h4>
                  <HelpCircle aria-hidden="true" />
                  Configure Your Quiz Session
                </h4>
                <p style={{ margin: '0 0 16px', fontSize: '13px', color: '#666' }}>
                  Lumina generates high-yield questions derived directly from your course syllabus and documents.
                </p>

                <div className="study-form-grid">
                  <div className="study-field-group">
                    <label htmlFor="quiz-topic">Target Topic</label>
                    <select
                      id="quiz-topic"
                      value={config.topic}
                      onChange={(e) => setConfig({ ...config, topic: e.target.value })}
                    >
                      {topicOptions.map((topic) => (
                        <option key={topic} value={topic}>
                          {topic}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="question-type">Question Type</label>
                    <select
                      id="question-type"
                      value={config.questionType}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          questionType: e.target.value as QuizConfig['questionType'],
                        })
                      }
                    >
                      <option value="multiple_choice">Multiple Choice (4 options)</option>
                      <option value="true_false">True / False</option>
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="question-count">Number of Questions</label>
                    <select
                      id="question-count"
                      value={config.questionCount}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          questionCount: Number(e.target.value),
                        })
                      }
                    >
                      <option value={5}>5 Questions (Quick Check)</option>
                      <option value={8}>8 Questions (Standard Practice)</option>
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="difficulty">Difficulty Level</label>
                    <select
                      id="difficulty"
                      value={config.difficulty}
                      onChange={(e) =>
                        setConfig({
                          ...config,
                          difficulty: e.target.value as QuizConfig['difficulty'],
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
                    checked={config.hasTimer}
                    onChange={(e) => setConfig({ ...config, hasTimer: e.target.checked })}
                  />
                  <span>Enable Exam Countdown Timer (1 min per question)</span>
                </label>
              </div>
            </div>
          )}

          {step === 'solving' && currentQ && (
            <div className="summary-container">
              <div className="quiz-solver-header">
                <span className="quiz-progress-indicator">
                  Question {currentQuestionIndex + 1} of {questions.length}
                </span>
                {config.hasTimer && (
                  <span className="quiz-timer">
                    <Clock style={{ width: '14px', height: '14px' }} />
                    {formatTime(timeLeft)}
                  </span>
                )}
              </div>

              <div className="quiz-progress-bar" role="progressbar" aria-valuenow={progressPercentage} aria-valuemin={0} aria-valuemax={100}>
                <div
                  className="quiz-progress-bar-fill"
                  style={{ width: `${progressPercentage}%` }}
                />
              </div>

              <div className="quiz-question-box">
                <span className="summary-meta-badge" style={{ marginBottom: '12px' }}>
                  {currentQ.topic}
                </span>
                <h3>{currentQ.question}</h3>

                <div className="quiz-options-list">
                  {currentQ.options.map((option, idx) => {
                    const isSelected = userAnswers[currentQ.id] === idx;
                    const letter = String.fromCharCode(65 + idx);
                    return (
                      <button
                        key={idx}
                        type="button"
                        className={`quiz-option-btn${isSelected ? ' selected' : ''}`}
                        onClick={() => handleSelectOption(currentQ.id, idx)}
                      >
                        <span className="option-letter">{letter}</span>
                        <span>{option}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )}

          {step === 'results' && result && (
            <div className="quiz-results-container">
              <div className="score-hero-card">
                <div className="score-hero-left">
                  <h3>
                    {result.scorePercentage >= 80
                      ? 'Outstanding Mastery!'
                      : result.scorePercentage >= 60
                      ? 'Solid Performance!'
                      : 'Needs Further Review'}
                  </h3>
                  <p>
                    You answered {result.correctCount} out of {result.totalQuestions} questions correctly in{' '}
                    {Math.floor(result.timeSpentSeconds / 60)}m {result.timeSpentSeconds % 60}s.
                  </p>
                </div>
                <div className="score-circle-badge">
                  {result.scorePercentage}%
                  <span>Score</span>
                </div>
              </div>

              <div className="quiz-stats-row">
                <div className="quiz-stat-card">
                  <strong style={{ color: '#10b981' }}>{result.correctCount}</strong>
                  <span>Correct</span>
                </div>
                <div className="quiz-stat-card">
                  <strong style={{ color: '#ef4444' }}>{result.incorrectCount}</strong>
                  <span>Incorrect</span>
                </div>
                <div className="quiz-stat-card">
                  <strong style={{ color: '#8b5cf6' }}>{result.totalQuestions}</strong>
                  <span>Total Questions</span>
                </div>
              </div>

              <div className="summary-section-card">
                <h4>
                  <HelpCircle aria-hidden="true" />
                  Detailed Question Analysis & Explanations
                </h4>
                <div className="result-review-list">
                  {result.questions.map((q, idx) => {
                    const selectedIdx = result.userAnswers[q.id];
                    const isCorrect = selectedIdx === q.correctAnswer;
                    return (
                      <div
                        key={q.id}
                        className={`review-item ${isCorrect ? 'correct' : 'incorrect'}`}
                      >
                        <div className="review-header">
                          <h4>
                            {idx + 1}. {q.question}
                          </h4>
                          {isCorrect ? (
                            <CheckCircle2 style={{ width: '18px', height: '18px', color: '#10b981', flexShrink: 0 }} />
                          ) : (
                            <XCircle style={{ width: '18px', height: '18px', color: '#ef4444', flexShrink: 0 }} />
                          )}
                        </div>

                        <div style={{ fontSize: '13px', margin: '4px 0 8px', color: '#444' }}>
                          <div>
                            <strong>Your Answer: </strong>
                            <span style={{ color: isCorrect ? '#065f46' : '#991b1b', fontWeight: 600 }}>
                              {selectedIdx !== undefined ? q.options[selectedIdx] : 'Unanswered'}
                            </span>
                          </div>
                          {!isCorrect && (
                            <div style={{ marginTop: '2px' }}>
                              <strong>Correct Answer: </strong>
                              <span style={{ color: '#065f46', fontWeight: 600 }}>{q.options[q.correctAnswer]}</span>
                            </div>
                          )}
                        </div>

                        <div className="review-explanation">
                          <strong>Rationale: </strong>
                          {q.explanation}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>

        <footer className="study-modal-footer">
          {step === 'config' && (
            <>
              <button className="secondary-button" type="button" onClick={onClose}>
                Cancel
              </button>
              <button className="primary-button" type="button" onClick={handleStartQuiz}>
                <Play aria-hidden="true" style={{ width: '16px', height: '16px' }} />
                Start Quiz
              </button>
            </>
          )}

          {step === 'solving' && (
            <>
              <button
                className="secondary-button"
                type="button"
                disabled={currentQuestionIndex === 0}
                onClick={() => setCurrentQuestionIndex((prev) => prev - 1)}
              >
                Previous
              </button>
              {currentQuestionIndex < questions.length - 1 ? (
                <button
                  className="primary-button"
                  type="button"
                  onClick={() => setCurrentQuestionIndex((prev) => prev + 1)}
                >
                  Next Question
                </button>
              ) : (
                <button className="primary-button" type="button" onClick={handleSubmitQuiz}>
                  <Sparkles aria-hidden="true" style={{ width: '16px', height: '16px' }} />
                  Submit Quiz
                </button>
              )}
            </>
          )}

          {step === 'results' && (
            <>
              <button className="secondary-button" type="button" onClick={handleResetToConfig}>
                <RotateCcw style={{ width: '16px', height: '16px' }} />
                New Quiz Setup
              </button>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button className="secondary-button" type="button" onClick={handleRetake}>
                  Retake Same Quiz
                </button>
                <button className="primary-button" type="button" onClick={onClose}>
                  Done
                </button>
              </div>
            </>
          )}
        </footer>
      </div>
    </div>
  );
};
