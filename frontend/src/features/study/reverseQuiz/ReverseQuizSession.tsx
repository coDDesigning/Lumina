import { useEffect, useRef, useState } from 'react';
import { generateReverseQuiz } from '@/api/reverseQuiz';
import { afterReverseQuizGenerated } from '@/api/invalidations';
import { describeGenerationError, isAbortError } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import type { ReverseQuizResponse } from '@/api/types';
import { Button } from '@/ui/Button';
import { Markdown } from '@/lib/markdown';
import { GenerationError } from '@/features/study/GenerationStates';

import styles from './ReverseQuizSession.module.css';

export interface ReverseQuizSessionProps {
  courseId: number;
  topic: string;
  /** Set when the student picked a source-derived question rather than a topic. */
  question?: string;
  onRestart: () => void;
}

export function ReverseQuizSession({
  courseId,
  topic,
  question,
  onRestart,
}: ReverseQuizSessionProps) {
  const [explanation, setExplanation] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const [result, setResult] = useState<ReverseQuizResponse | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const handleSubmit = async () => {
    if (!explanation.trim()) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsGenerating(true);
    setFailure(null);

    try {
      const response = await generateReverseQuiz(
        courseId,
        { topic, question: question ?? null, explanation: explanation.trim() },
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setResult(response);
      afterReverseQuizGenerated(courseId);
    } catch (e) {
      if (controller.signal.aborted || isAbortError(e)) return;
      setFailure(describeGenerationError(e, 'Your explanation could not be analysed.'));
    } finally {
      if (!controller.signal.aborted) setIsGenerating(false);
    }
  };

  if (result) {
    return (
      <div className={styles.resultContainer}>
        <div className={styles.header}>
          <h2>Analysis for: {topic}</h2>
          <Button onClick={onRestart} variant="secondary">
            Explain Another Topic
          </Button>
        </div>

        {question ? (
          <div className={styles.section}>
            <h3>Question</h3>
            <p className={styles.studentExplanation}>{question}</p>
          </div>
        ) : null}

        <div className={styles.section}>
          <h3>Your Explanation</h3>
          <p className={styles.studentExplanation}>{result.explanation}</p>
        </div>

        <div className={styles.section}>
          <h3>Feedback</h3>
          <Markdown className={styles.feedback} text={result.feedback} />
        </div>

        {result.misconceptions.length > 0 ? (
          <div className={styles.section}>
            <h3>Missing Points &amp; Misconceptions</h3>
            <ul className={styles.misconceptionList}>
              {result.misconceptions.map((m, i) => (
                <li key={i} className={styles.misconceptionItem}>
                  <div className={styles.misconceptionStatus} data-status={m.status}>
                    {m.status.replace('_', ' ')}
                  </div>
                  <div className={styles.misconceptionConcept}>
                    <strong>{m.concept}</strong>
                  </div>
                  <Markdown className={styles.misconceptionDetail} text={m.detail} />
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className={styles.section}>
            <div className={styles.successMessage}>
              Your explanation lines up with the course material — nothing to correct.
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className={styles.sessionContainer}>
      <div className={styles.header}>
        <h2>Explain: {topic}</h2>
        <Button onClick={onRestart} variant="ghost">
          Choose Different Topic
        </Button>
      </div>

      <div className={styles.instructions}>
        {question ? (
          <>
            <strong>{question}</strong>
            <br />
            Answer in your own words. Lumina checks it against the course material and flags
            any missing points or misconceptions.
          </>
        ) : (
          <>
            Explain the topic in your own words. Lumina compares your explanation against the
            course material and identifies any missing key points or misconceptions.
          </>
        )}
      </div>

      {failure ? <GenerationError failure={failure} onRetry={handleSubmit} /> : null}

      <textarea
        className={styles.textarea}
        placeholder="Type your explanation here..."
        value={explanation}
        onChange={(e) => setExplanation(e.target.value)}
        disabled={isGenerating}
        rows={8}
      />

      <div className={styles.actions}>
        <Button
          onClick={handleSubmit}
          disabled={!explanation.trim() || isGenerating}
          isLoading={isGenerating}
        >
          {isGenerating ? 'Analyzing...' : 'Submit Explanation'}
        </Button>
      </div>
    </div>
  );
}
