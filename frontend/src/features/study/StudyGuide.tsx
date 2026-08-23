import type { ReactNode } from 'react';
import {
  AlertTriangle,
  BookMarked,
  CircleCheck,
  Clock,
  Compass,
  Layers3,
  ListChecks,
  Sparkles,
} from 'lucide-react';
import type {
  CoverageStatus,
  DifficultyLevel,
  RetrievedContext,
  StudyGuideResponse,
} from '@/api/types';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import type { BadgeTone } from '@/ui/Badge';
import styles from './StudyGuide.module.css';

export interface StudyGuideProps {
  guide: StudyGuideResponse;
  context?: RetrievedContext | null;
}

const DIFFICULTY_TONE: Record<DifficultyLevel, BadgeTone> = {
  Easy: 'success',
  Medium: 'warning',
  Hard: 'destructive',
};

const COVERAGE_TONE: Record<CoverageStatus, BadgeTone> = {
  Complete: 'success',
  'Mostly Complete': 'success',
  Partial: 'warning',
  Limited: 'destructive',
};

function difficultyTone(level: string): BadgeTone {
  return DIFFICULTY_TONE[level as DifficultyLevel] ?? 'neutral';
}

function coverageTone(status: string): BadgeTone {
  return COVERAGE_TONE[status as CoverageStatus] ?? 'neutral';
}

function Section({
  icon,
  heading,
  children,
}: {
  icon: ReactNode;
  heading: string;
  children: ReactNode;
}) {
  return (
    <section className={styles.section}>
      <h3 className={styles.heading}>
        <span className={styles.headingIcon} aria-hidden="true">
          {icon}
        </span>
        {heading}
      </h3>
      {children}
    </section>
  );
}

export function StudyGuide({ guide, context = null }: StudyGuideProps) {
  const hasExamTips =
    guide.exam_tips.lecture_based.length > 0 || guide.exam_tips.ai_suggestions.length > 0;

  return (
    <article className={styles.guide}>
      <header className={styles.masthead}>
        <h3 className={styles.title}>{guide.title}</h3>
        <div className={styles.facts}>
          <Badge tone={difficultyTone(guide.difficulty.level)}>{guide.difficulty.level}</Badge>
          <Badge icon={<Clock aria-hidden="true" />}>{guide.estimated_study_time}</Badge>
          <Badge tone={coverageTone(guide.coverage.status)}>
            {guide.coverage.status} · {guide.coverage.estimated_completeness}% of the material
          </Badge>
        </div>
        <p className={styles.summary}>{guide.summary}</p>
        {guide.difficulty.reason ? (
          <p className={styles.aside}>{guide.difficulty.reason}</p>
        ) : null}
      </header>

      {context?.retrieval_narrowed ? (
        <Alert tone="info" title="Built from the passages closest to your topic">
          Of {context.chunks_available} passages in this course, the {context.chunks_used} most
          relevant were used. Anything outside that focus is not covered here.
        </Alert>
      ) : null}

      {context?.context_truncated ? (
        <Alert tone="warning" title="Some selected passages did not fit">
          The material chosen for this guide was longer than one request allows, so the
          least-relevant of it was left out. Narrowing the topic will cover the rest.
        </Alert>
      ) : null}

      {guide.learning_objectives.length > 0 ? (
        <Section icon={<Compass />} heading="What you should be able to do">
          <ul className={styles.list}>
            {guide.learning_objectives.map((objective, index) => (
              <li key={index}>{objective}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {guide.key_points.length > 0 ? (
        <Section icon={<ListChecks />} heading="Key points">
          <ul className={styles.list}>
            {guide.key_points.map((point, index) => (
              <li key={index}>{point}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {guide.important_terms.length > 0 ? (
        <Section icon={<BookMarked />} heading="Terms to know">
          <dl className={styles.terms}>
            {guide.important_terms.map((term, index) => (
              <div className={styles.term} key={index}>
                <dt>{term.term}</dt>
                <dd>{term.definition}</dd>
              </div>
            ))}
          </dl>
        </Section>
      ) : null}

      {guide.common_mistakes.length > 0 ? (
        <Section icon={<AlertTriangle />} heading="Where people go wrong">
          <ul className={styles.mistakes}>
            {guide.common_mistakes.map((item, index) => (
              <li key={index}>
                <p className={styles.mistake}>
                  <span className={styles.mistakeLabel}>Mistake</span>
                  {item.mistake}
                </p>
                <p className={styles.correction}>
                  <CircleCheck className={styles.correctionIcon} aria-hidden="true" />
                  {item.correction}
                </p>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {hasExamTips ? (
        <Section icon={<Sparkles />} heading="Going into the exam">
          {guide.exam_tips.lecture_based.length > 0 ? (
            <>
              <h4 className={styles.subheading}>Said in your material</h4>
              <ul className={styles.list}>
                {guide.exam_tips.lecture_based.map((tip, index) => (
                  <li key={index}>{tip}</li>
                ))}
              </ul>
            </>
          ) : null}
          {guide.exam_tips.ai_suggestions.length > 0 ? (
            <>
              <h4 className={styles.subheading}>Suggested, not from your material</h4>
              <ul className={styles.list}>
                {guide.exam_tips.ai_suggestions.map((tip, index) => (
                  <li key={index}>{tip}</li>
                ))}
              </ul>
            </>
          ) : null}
        </Section>
      ) : null}

      {guide.prerequisites.length > 0 ? (
        <Section icon={<Layers3 />} heading="Assumed knowledge">
          <ul className={styles.list}>
            {guide.prerequisites.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </Section>
      ) : null}

      {guide.confidence_notes ? (
        <footer className={styles.confidence}>
          <h3 className={styles.confidenceHeading}>What this guide is unsure about</h3>
          <p>{guide.confidence_notes}</p>
        </footer>
      ) : null}
    </article>
  );
}
