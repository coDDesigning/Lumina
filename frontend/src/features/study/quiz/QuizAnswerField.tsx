import { useId } from 'react';
import { Check, X } from 'lucide-react';
import type { QuizQuestionView } from '@/api/types';
import { cx } from '@/lib/cx';
import { MAX_ANSWER_TEXT_CHARS, OPEN_ENDED_ROWS, SHORT_ANSWER_ROWS } from './answerDraft';
import type { AnswerDraft } from './answerDraft';
import styles from './QuizAnswerField.module.css';

export interface QuizAnswerFieldProps {
  question: QuizQuestionView;
  draft: AnswerDraft;
  onChange: (draft: AnswerDraft) => void;
}

function optionLetter(index: number): string {
  return String.fromCharCode(65 + index);
}

export function QuizAnswerField({ question, draft, onChange }: QuizAnswerFieldProps) {
  const groupName = useId();

  if (question.question_type === 'true_false') {
    const options = question.options ?? ['True', 'False'];

    return (
      <fieldset className={styles.fieldset}>
        <legend className="visually-hidden">Choose true or false</legend>
        <div className={styles.binary}>
          {options.map((option, index) => (
            <label
              key={option}
              className={cx(styles.binaryChoice, draft.optionIndex === index && styles.chosen)}
            >
              <input
                type="radio"
                name={groupName}
                className={styles.control}
                checked={draft.optionIndex === index}
                onChange={() => onChange({ optionIndex: index, text: '' })}
              />
              <span className={styles.binaryMark} aria-hidden="true">
                {index === 0 ? <Check /> : <X />}
              </span>
              <span className={styles.binaryLabel}>{option}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  if (question.options) {
    return (
      <fieldset className={styles.fieldset}>
        <legend className="visually-hidden">Choose one answer</legend>
        <div className={styles.options}>
          {question.options.map((option, index) => (
            <label
              key={index}
              className={cx(styles.option, draft.optionIndex === index && styles.chosen)}
            >
              <input
                type="radio"
                name={groupName}
                className={styles.control}
                checked={draft.optionIndex === index}
                onChange={() => onChange({ optionIndex: index, text: '' })}
              />
              <span className={styles.letter} aria-hidden="true">
                {optionLetter(index)}
              </span>
              <span className={styles.optionLabel}>{option}</span>
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  const isOpenEnded = question.question_type === 'open_ended';
  const remaining = MAX_ANSWER_TEXT_CHARS - draft.text.length;

  return (
    <div className={styles.written}>
      <label className="visually-hidden" htmlFor={groupName}>
        Your answer
      </label>
      <textarea
        id={groupName}
        className={styles.textarea}
        rows={isOpenEnded ? OPEN_ENDED_ROWS : SHORT_ANSWER_ROWS}
        maxLength={MAX_ANSWER_TEXT_CHARS}
        placeholder={isOpenEnded ? 'Explain your reasoning' : 'Type your answer'}
        value={draft.text}
        onChange={(event) => onChange({ optionIndex: null, text: event.target.value })}
      />
      {isOpenEnded ? (
        <p className={styles.counter}>
          <span className="tabular">{remaining.toLocaleString()}</span> characters left. A written
          answer is marked by the model, and may come back unscored.
        </p>
      ) : null}
    </div>
  );
}
