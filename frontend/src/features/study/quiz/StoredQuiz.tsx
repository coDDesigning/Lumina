import { quizAPI } from '@/api/quiz';
import { queryKeys } from '@/api/queryKeys';
import type { QuizHistoryItem, QuizView } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';
import { Badge } from '@/ui/Badge';
import { CitationList } from '@/ui/CitationChip';
import { LinkButton } from '@/ui/LinkButton';
import styles from './StoredQuiz.module.css';

export interface StoredQuizProps {
  quiz: QuizView;
  courseId: number;
}

const QUESTION_TYPE_LABELS: Record<string, string> = {
  multiple_choice: 'Multiple choice',
  true_false: 'True / false',
  short_answer: 'Short answer',
  open_ended: 'Written answer',
};

export function StoredQuiz({ quiz, courseId }: StoredQuizProps) {
  const settings = quiz.generation_settings;

  // Reading a quiz here must not tell a student the answers to a paper they
  // have not sat. The review is earned by attempting it, so anything short of
  // a recorded attempt -- still loading, or a history that could not be read --
  // keeps the answers back rather than guessing in the student's favour.
  const attempts = useQuery<QuizHistoryItem[]>({
    key: queryKeys.courseQuizAttempts(courseId, quiz.quiz_id),
    fetcher: ({ signal }) => quizAPI.listAttempts(courseId, quiz.quiz_id, { signal }),
    fallbackMessage: 'Your attempts could not be read.',
  });
  const hasSatIt = attempts.status === 'success' && (attempts.data?.length ?? 0) > 0;

  return (
    <article className={styles.container}>
      <header className={styles.masthead}>
        <div className={styles.headerRow}>
          <h3 className={styles.title}>{quiz.title}</h3>
          <LinkButton
            variant="primary"
            size="sm"
            to={`/courses/${courseId}/practice/${quiz.quiz_id}`}
          >
            Take this quiz
          </LinkButton>
        </div>
        {attempts.status !== 'pending' && attempts.status !== 'idle' && !hasSatIt ? (
          <p className={styles.spoilerNote}>
            The answers are held back until you have taken this quiz.
          </p>
        ) : null}
        <div className={styles.badges}>
          <Badge>
            <span className="tabular">{quiz.questions.length}</span>{' '}
            {quiz.questions.length === 1 ? 'question' : 'questions'}
          </Badge>
          {settings?.difficulty ? <Badge>{settings.difficulty}</Badge> : null}
          {settings?.topic_focus && settings.topic_focus !== 'All Topics' ? (
            <Badge>{settings.topic_focus}</Badge>
          ) : null}
        </div>
      </header>

      <div className={styles.list}>
        {quiz.questions.map((q, idx) => (
          <div key={q.question_id || idx} className={styles.card}>
            <div className={styles.cardHeader}>
              <span className={styles.questionNumber}>Question {idx + 1}</span>
              <div className={styles.badges}>
                <Badge>{QUESTION_TYPE_LABELS[q.question_type] ?? q.question_type}</Badge>
                {q.difficulty ? <Badge>{q.difficulty}</Badge> : null}
                {q.topic ? <Badge>{q.topic}</Badge> : null}
              </div>
            </div>

            <p className={styles.questionText}>{q.question}</p>

            {q.options && q.options.length > 0 ? (
              <ul className={styles.optionsList}>
                {q.options.map((option, optIdx) => {
                  const isCorrect = hasSatIt && q.correct_option_index === optIdx;
                  return (
                    <li
                      key={optIdx}
                      className={`${styles.optionItem} ${isCorrect ? styles.optionCorrect : ''}`}
                    >
                      <span>{option}</span>
                      {isCorrect ? <span className={styles.correctBadge}>Correct</span> : null}
                    </li>
                  );
                })}
              </ul>
            ) : null}

            {hasSatIt && q.correct_answer ? (
              <div className={styles.answerSection}>
                <p className={styles.answerLabel}>
                  {q.question_type === 'open_ended' ? 'Reference answer' : 'Accepted answer'}
                </p>
                <p className={styles.answerText}>
                  {q.correct_answer.type === 'short_answer'
                    ? q.correct_answer.text
                    : q.correct_answer.type === 'open_ended'
                    ? q.correct_answer.reference_answer
                    : null}
                </p>
              </div>
            ) : null}

            {hasSatIt ? <CitationList citations={q.citations} /> : null}
            {hasSatIt && q.explanation ? (
              <p className={styles.explanation}>{q.explanation}</p>
            ) : null}
          </div>
        ))}
      </div>
    </article>
  );
}
