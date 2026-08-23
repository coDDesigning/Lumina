import type { QuizView } from '@/api/types';
import { Badge } from '@/ui/Badge';
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
                  const isCorrect = q.correct_option_index === optIdx;
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

            {q.correct_answer ? (
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

            {q.explanation ? (
              <p className={styles.explanation}>{q.explanation}</p>
            ) : null}
          </div>
        ))}
      </div>
    </article>
  );
}
