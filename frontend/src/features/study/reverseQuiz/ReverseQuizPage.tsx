import { useEffect, useRef, useState } from 'react';
import type { Workspace } from '@/data/workspaces';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import { suggestReverseQuizQuestions } from '@/api/reverseQuiz';
import { describeGenerationError, isAbortError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import type { ReverseQuizQuestion } from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { PageHeader } from '@/ui/PageHeader';
import { Button } from '@/ui/Button';
import { Input } from '@/ui/Input';
import { Spinner } from '@/ui/Spinner';
import { GenerationError } from '@/features/study/GenerationStates';
import { ReverseQuizSession } from './ReverseQuizSession';
import styles from './ReverseQuizPage.module.css';

export interface ReverseQuizPageProps {
  workspace: Workspace;
}

interface Picked {
  topic: string;
  question?: string;
}

export default function ReverseQuizPage({ workspace }: ReverseQuizPageProps) {
  const courseId = Number(workspace.id);
  useDocumentTitle(`${workspace.name} · Reverse Quiz`);
  const [selected, setSelected] = useState<Picked | null>(null);
  const [customTopic, setCustomTopic] = useState('');

  const [suggested, setSuggested] = useState<ReverseQuizQuestion[] | null>(null);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [suggestFailure, setSuggestFailure] = useState<GenerationFailure | null>(null);
  const { isMetered, canAfford, refresh: refreshCredits } = useCredits();
  const exhausted = isMetered && !canAfford('reverse_quiz');

  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const startTopic = (topic: string) => {
    if (topic.trim()) {
      setSelected({ topic: topic.trim() });
    }
  };

  const suggestQuestions = async () => {
    if (exhausted) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setIsSuggesting(true);
    setSuggestFailure(null);
    try {
      const response = await suggestReverseQuizQuestions(courseId, controller.signal);
      if (controller.signal.aborted) return;
      setSuggested(response.questions);
      await refreshCredits();
    } catch (e) {
      if (controller.signal.aborted || isAbortError(e)) return;
      const described = describeGenerationError(
        e,
        'Questions could not be drafted from your sources.',
      );
      if (isInsufficientCredits(described)) await refreshCredits();
      setSuggestFailure(described);
    } finally {
      if (!controller.signal.aborted) setIsSuggesting(false);
    }
  };

  return (
    <div className={styles.container}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Reverse Quiz' },
        ]}
      />
      <h1 className="visually-hidden">Reverse Quiz</h1>
      <p className={styles.description}>
        Explain concepts in your own words to check your understanding.
      </p>

      <div className={styles.content}>
        {selected ? (
          <ReverseQuizSession
            courseId={courseId}
            topic={selected.topic}
            question={selected.question}
            onRestart={() => setSelected(null)}
          />
        ) : (
          <div className={styles.chooser}>
            <div className={styles.topicSelection}>
              <h2>What would you like to explain today?</h2>

              {workspace.topics.length > 0 && (
                <div className={styles.topicsList}>
                  <h3>Course Topics</h3>
                  <div className={styles.topicsGrid}>
                    {workspace.topics.map((topic, index) => (
                      <Button
                        key={index}
                        variant="secondary"
                        onClick={() => startTopic(topic)}
                        className={styles.topicButton}
                      >
                        {topic}
                      </Button>
                    ))}
                  </div>
                </div>
              )}

              <div className={styles.customTopic}>
                <h3>Or enter a specific topic</h3>
                <div className={styles.customTopicInput}>
                  <Input
                    label="Enter a specific topic"
                    hideLabel
                    type="text"
                    placeholder="e.g. Photosynthesis, Newton's Laws"
                    value={customTopic}
                    onChange={(e) => setCustomTopic(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && startTopic(customTopic)}
                    fieldClassName={styles.input}
                  />
                  <Button disabled={!customTopic.trim()} onClick={() => startTopic(customTopic)}>
                    Start
                  </Button>
                </div>
              </div>
            </div>

            <div className={styles.sourcePanel}>
              <h3>Questions from your sources</h3>
              <p className={styles.sourceHint}>
                Lumina reads the material you uploaded and your chats for this course, then
                drafts questions to explain in your own words.
              </p>
              <CreditBalance source="reverse_quiz" />

              {exhausted ? (
                <CreditExhaustedNotice source="reverse_quiz" action="reverse quiz questions" />
              ) : null}

              {suggestFailure ? (
                <GenerationError
                  failure={
                    exhausted ? { ...suggestFailure, retryable: false } : suggestFailure
                  }
                  onRetry={() => void suggestQuestions()}
                />
              ) : null}

              {isSuggesting ? (
                <div className={styles.suggesting} role="status">
                  <Spinner size="sm" />
                  <span>Reading your sources…</span>
                </div>
              ) : null}

              {!isSuggesting && suggested !== null ? (
                suggested.length > 0 ? (
                  <ul className={styles.questionList}>
                    {suggested.map((q, i) => (
                      <li key={i}>
                        <button
                          type="button"
                          className={styles.questionCard}
                          onClick={() => setSelected({ topic: q.topic, question: q.question })}
                        >
                          <span className={styles.questionTopic}>{q.topic}</span>
                          <span className={styles.questionText}>{q.question}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className={styles.sourceHint}>
                    No questions could be drawn from this course yet. Upload and process a
                    document first.
                  </p>
                )
              ) : null}

              {!isSuggesting ? (
                <Button
                  variant="secondary"
                  disabled={exhausted}
                  onClick={() => void suggestQuestions()}
                >
                  {suggested === null ? 'Suggest questions' : 'Suggest new questions'}
                </Button>
              ) : null}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
