import { CircleCheck, CircleHelp, CircleX } from 'lucide-react';
import type { QuizAttemptResponse, QuizQuestionView } from '@/api/types';
import { Badge } from '@/ui/Badge';
import type { BadgeTone } from '@/ui/Badge';
import { cx } from '@/lib/cx';
import { describeCorrectAnswer, describeSubmittedAnswer, tallyAttempt } from './quizScoring';
import styles from './QuizResults.module.css';

export interface QuizResultsProps {
  attempt: QuizAttemptResponse;
  questions: QuizQuestionView[];
}

type Verdict = 'correct' | 'incorrect' | 'ungraded';

const VERDICT_LABEL: Record<Verdict, string> = {
  correct: 'Correct',
  incorrect: 'Not correct',
  ungraded: 'Not scored',
};

const VERDICT_TONE: Record<Verdict, BadgeTone> = {
  correct: 'success',
  incorrect: 'destructive',
  ungraded: 'neutral',
};

function verdictOf(isCorrect: boolean | null): Verdict {
  if (isCorrect === null) {
    return 'ungraded';
  }
  return isCorrect ? 'correct' : 'incorrect';
}

function VerdictIcon({ verdict }: { verdict: Verdict }) {
  if (verdict === 'correct') {
    return <CircleCheck aria-hidden="true" />;
  }
  if (verdict === 'incorrect') {
    return <CircleX aria-hidden="true" />;
  }
  return <CircleHelp aria-hidden="true" />;
}

function spentFor(seconds: number | null): string | null {
  if (seconds === null) {
    return null;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes === 0) {
    return `${rest}s`;
  }
  return `${minutes}m ${rest}s`;
}

export function QuizResults({ attempt, questions }: QuizResultsProps) {
  const tally = tallyAttempt(attempt);
  const byId = new Map(questions.map((question) => [question.question_id, question]));
  const spent = spentFor(attempt.time_spent_seconds);

  return (
    <div className={styles.results}>
      <section className={styles.headline} aria-labelledby="quiz-score-heading">
        <div>
          <h3 id="quiz-score-heading" className={cx(styles.score, 'tabular')}>
            {tally.scorePercent}%
          </h3>
          <p className={cx(styles.scoreDetail, 'tabular')}>
            {`${tally.correct} of ${tally.graded} marked answers correct`}
            {spent ? ` · ${spent}` : ''}
          </p>
        </div>
        <dl className={styles.tally}>
          <div className={styles.tallyItem}>
            <dt>Correct</dt>
            <dd className={cx(styles.tallyValue, styles.correct)}>{tally.correct}</dd>
          </div>
          <div className={styles.tallyItem}>
            <dt>Not correct</dt>
            <dd className={cx(styles.tallyValue, styles.incorrect)}>{tally.incorrect}</dd>
          </div>
          {tally.ungraded > 0 ? (
            <div className={styles.tallyItem}>
              <dt>Not scored</dt>
              <dd className={styles.tallyValue}>{tally.ungraded}</dd>
            </div>
          ) : null}
        </dl>
      </section>

      {tally.ungraded > 0 ? (
        <p className={styles.ungradedNote}>
          {tally.ungraded === 1 ? 'One written answer' : `${tally.ungraded} written answers`} could
          not be marked automatically. They count neither for nor against your score.
        </p>
      ) : null}

      <section className={styles.review} aria-labelledby="quiz-review-heading">
        <h3 id="quiz-review-heading" className={styles.reviewHeading}>
          Every question
        </h3>

        <ol className={styles.list}>
          {attempt.answers.map((answer, index) => {
            const question = byId.get(answer.question_id);
            if (!question) {
              return null;
            }
            const verdict = verdictOf(answer.is_correct);
            const submitted = describeSubmittedAnswer(question, answer);
            const correct = describeCorrectAnswer(question);
            const isOpenEnded = question.question_type === 'open_ended';

            return (
              <li key={answer.question_id} className={cx(styles.item, styles[verdict])}>
                <div className={styles.itemHead}>
                  <span className={cx(styles.verdictIcon, styles[`icon-${verdict}`])}>
                    <VerdictIcon verdict={verdict} />
                  </span>
                  <h4 className={styles.question}>
                    <span className={styles.questionNumber}>{index + 1}.</span>{' '}
                    {question.question}
                  </h4>
                  <Badge tone={VERDICT_TONE[verdict]}>{VERDICT_LABEL[verdict]}</Badge>
                </div>

                <dl className={styles.answers}>
                  <div>
                    <dt>You answered</dt>
                    <dd className={submitted ? undefined : styles.blank}>
                      {submitted ?? 'Nothing'}
                    </dd>
                  </div>

                  {verdict !== 'correct' && correct ? (
                    <div>
                      <dt>{isOpenEnded ? 'A reference answer' : 'The answer'}</dt>
                      <dd>{correct}</dd>
                    </div>
                  ) : null}
                </dl>

                {answer.feedback ? (
                  <p className={styles.note}>
                    <span className={styles.noteLabel}>On your answer</span>
                    {answer.feedback}
                  </p>
                ) : null}

                {question.explanation ? (
                  <p className={styles.note}>
                    <span className={styles.noteLabel}>Why</span>
                    {question.explanation}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}
