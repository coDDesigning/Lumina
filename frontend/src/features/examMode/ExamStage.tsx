import type { ReactNode } from 'react';
import { Check } from 'lucide-react';
import { cx } from '@/lib/cx';
import styles from './ExamModePage.module.css';

export type StageState = 'done' | 'current' | 'waiting';

export interface ExamStageProps {
  number: number;
  title: string;
  state: StageState;
  lede?: ReactNode;
  headingId: string;
  children: ReactNode;
}

const STATE_WORDS: Record<StageState, string> = {
  done: 'done',
  current: 'do this next',
  waiting: 'not yet',
};

/**
 * One step of the setup sequence.
 *
 * The number is the structure, not an ornament: a student cannot review topics
 * before the sources have been read, so the order is a fact about the work. The
 * state reaches a screen reader as a word rather than as a colour, because the
 * accent on a numeral is reinforcement and never the only signal.
 */
export function ExamStage({ number, title, state, lede, headingId, children }: ExamStageProps) {
  return (
    <section className={styles.stage} aria-labelledby={headingId}>
      <span
        className={cx(
          styles.ordinal,
          state === 'current' && styles.ordinalActive,
          state === 'done' && styles.ordinalDone,
        )}
        aria-hidden="true"
      >
        {state === 'done' ? <Check aria-hidden="true" /> : number}
      </span>
      <div className={styles.stageBody}>
        <h2 id={headingId} className={styles.stageTitle}>
          <span className="visually-hidden">
            Step {number}, {STATE_WORDS[state]}:{' '}
          </span>
          {title}
        </h2>
        {lede ? <p className={styles.lede}>{lede}</p> : null}
        {children}
      </div>
    </section>
  );
}
