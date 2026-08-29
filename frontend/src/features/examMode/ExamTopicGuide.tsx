import type { ExamTopicGuideDocument, MaybeCited } from '@/api/types';
import { isExamTopicGuideDocument } from '@/api/examTopicGuideDocument';
import { citedCitations, citedText } from '@/features/study/citations';
import { Button } from '@/ui/Button';
import { CitationList } from '@/ui/CitationChip';
import { ErrorState } from '@/ui/ErrorState';
import styles from './ExamTopicGuide.module.css';

export interface ExamTopicGuideProps {
  guide: ExamTopicGuideDocument;
  onRegenerate?: () => void;
  onRetry?: () => void;
}

interface ExamTopicGuideUnavailableProps {
  onRegenerate?: () => void;
  onRetry?: () => void;
}

export function ExamTopicGuideUnavailable({
  onRegenerate,
  onRetry,
}: ExamTopicGuideUnavailableProps) {
  return (
    <ErrorState
      title="This guide could not be displayed"
      onRetry={onRetry}
      actions={
        onRegenerate ? (
          <Button variant="secondary" size="sm" onClick={onRegenerate}>
            Regenerate guide
          </Button>
        ) : undefined
      }
    >
      The saved guide is incomplete or uses a format this version of Lumina cannot read.
    </ErrorState>
  );
}

function Cited({ item }: { item: MaybeCited }) {
  return (
    <>
      <span>{citedText(item)}</span>
      <CitationList citations={citedCitations(item)} />
    </>
  );
}

/**
 * A stored guide, rendered from what was written and saved.
 *
 * Its citations were denormalised at generation time, so a reopen resolves the
 * same sources with no provider call — and a citation survives the deletion of
 * the document it names, which is why it is a label rather than a link.
 */
export function ExamTopicGuide({ guide, onRegenerate, onRetry }: ExamTopicGuideProps) {
  if (!isExamTopicGuideDocument(guide)) {
    return <ExamTopicGuideUnavailable onRegenerate={onRegenerate} onRetry={onRetry} />;
  }

  return (
    <article className={styles.guide}>
      <h3 className={styles.title}>{guide.title}</h3>

      <p className={styles.overview}>
        <Cited item={guide.overview} />
      </p>

      {guide.sections.map((section) => (
        <section key={section.heading} className={styles.section}>
          <h4 className={styles.heading}>{section.heading}</h4>
          <p className={styles.body}>
            <Cited item={section.body} />
          </p>
          {section.key_points.length > 0 ? (
            <ul className={styles.points}>
              {section.key_points.map((point, index) => (
                <li key={`${section.heading}-point-${index}`}>
                  <Cited item={point} />
                </li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}

      {guide.key_terms.length > 0 ? (
        <section className={styles.section}>
          <h4 className={styles.heading}>Terms to know</h4>
          <dl className={styles.terms}>
            {guide.key_terms.map((term) => (
              <div key={term.term} className={styles.term}>
                <dt>{term.term}</dt>
                <dd>
                  {term.definition}
                  <CitationList citations={term.citations} />
                </dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {guide.common_pitfalls.length > 0 ? (
        <section className={styles.section}>
          <h4 className={styles.heading}>Where this catches people out</h4>
          <ul className={styles.points}>
            {guide.common_pitfalls.map((pitfall) => (
              <li key={pitfall.mistake}>
                <strong>{pitfall.mistake}</strong> — {pitfall.correction}
                <CitationList citations={pitfall.citations} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {guide.what_to_be_able_to_do.length > 0 ? (
        <section className={styles.section}>
          <h4 className={styles.heading}>You should be able to</h4>
          <ul className={styles.points}>
            {guide.what_to_be_able_to_do.map((item, index) => (
              <li key={`objective-${index}`}>
                <Cited item={item} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {guide.confidence_notes ? <p className={styles.notes}>{guide.confidence_notes}</p> : null}
    </article>
  );
}
