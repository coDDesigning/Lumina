import { useCallback, useRef } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import type { QuizQuestionView } from '@/api/types';
import { cx } from '@/lib/cx';
import styles from './QuizNavigator.module.css';

export interface QuizNavigatorProps {
  questions: QuizQuestionView[];
  index: number;
  onIndex: (index: number) => void;
  isAnswered: (questionId: number) => boolean;
}

/**
 * The question pips, with one tab stop and arrow keys between them.
 *
 * Shared by the untimed attempt and the timed sitting so there is one keyboard
 * model for moving through a paper, not two that drift apart.
 */
export function QuizNavigator({ questions, index, onIndex, isAnswered }: QuizNavigatorProps) {
  const listRef = useRef<HTMLElement>(null);

  const focusPip = useCallback((position: number) => {
    const pips = listRef.current?.querySelectorAll<HTMLButtonElement>('button');
    pips?.[position]?.focus();
  }, []);

  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLElement>) => {
      const total = questions.length;
      if (total === 0) return;

      let next: number | null = null;
      if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
        next = (index + 1) % total;
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
        next = (index - 1 + total) % total;
      } else if (event.key === 'Home') {
        next = 0;
      } else if (event.key === 'End') {
        next = total - 1;
      }
      if (next === null) return;

      event.preventDefault();
      onIndex(next);
      focusPip(next);
    },
    [focusPip, index, onIndex, questions.length],
  );

  return (
    <nav ref={listRef} className={styles.navigator} aria-label="Questions" onKeyDown={onKeyDown}>
      {questions.map((question, position) => {
        const answered = isAnswered(question.question_id);
        return (
          <button
            key={question.question_id}
            type="button"
            className={cx(
              styles.pip,
              position === index && styles.pipCurrent,
              answered && styles.pipAnswered,
            )}
            aria-current={position === index ? 'true' : undefined}
            tabIndex={position === index ? 0 : -1}
            aria-label={`Question ${position + 1}${answered ? ', answered' : ', not answered'}`}
            onClick={() => onIndex(position)}
          >
            <span aria-hidden="true">{position + 1}</span>
          </button>
        );
      })}
    </nav>
  );
}
