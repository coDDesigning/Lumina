import { useState } from 'react';
import { generateReverseQuiz } from '@/api/reverseQuiz';
import type { ReverseQuizResponse } from '@/api/types';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
import { Provenance } from '@/features/study/Provenance';
import styles from './ReverseQuizSession.module.css';
import { describeError } from '@/api/errors';

export interface ReverseQuizSessionProps {
  courseId: number;
  topic: string;
  onRestart: () => void;
}

export function ReverseQuizSession({ courseId, topic, onRestart }: ReverseQuizSessionProps) {
  const [explanation, setExplanation] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ReverseQuizResponse | null>(null);

  const handleSubmit = async () => {
    if (!explanation.trim()) return;

    setIsGenerating(true);
    setError(null);

    try {
      const response = await generateReverseQuiz(courseId, {
        topic,
        explanation: explanation.trim(),
      });
      setResult(response);
    } catch (e) {
      setError(describeError(e, 'Failed to analyze explanation.').message);
    } finally {
      setIsGenerating(false);
    }
  };

  if (result) {
    return (
      <div className={styles.resultContainer}>
        <div className={styles.header}>
          <h2>Analysis for: {topic}</h2>
          <Button onClick={onRestart} variant="secondary">Explain Another Topic</Button>
        </div>

        <div className={styles.section}>
          <h3>Your Explanation</h3>
          <p className={styles.studentExplanation}>{result.explanation}</p>
        </div>

        <div className={styles.section}>
          <h3>Feedback</h3>
          <p className={styles.feedback}>{result.feedback}</p>
        </div>

        {result.misconceptions.length > 0 ? (
          <div className={styles.section}>
            <h3>Missing Points & Misconceptions</h3>
            <ul className={styles.misconceptionList}>
              {result.misconceptions.map((m, i) => (
                <li key={i} className={styles.misconceptionItem}>
                  <div className={styles.misconceptionStatus} data-status={m.status}>
                    {m.status.replace('_', ' ')}
                  </div>
                  <div className={styles.misconceptionConcept}>
                    <strong>{m.concept}</strong>
                  </div>
                  <div className={styles.misconceptionDetail}>
                    {m.detail}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <div className={styles.section}>
            <div className={styles.successMessage}>
              Great job! You explained the concepts perfectly according to the course material.
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
        <Button onClick={onRestart} variant="ghost">Choose Different Topic</Button>
      </div>

      <div className={styles.instructions}>
        Explain the topic in your own words. Lumina will compare your explanation 
        against the course materials and identify any missing key points or misconceptions.
      </div>

      {error && <ErrorState onRetry={handleSubmit}>{error}</ErrorState>}

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
