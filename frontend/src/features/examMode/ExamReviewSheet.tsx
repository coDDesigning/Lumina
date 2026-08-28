import type { ExamReviewSheetDocument, MaybeCited } from '@/api/types';
import { citationLabel, citedCitations, citedText } from '@/features/study/citations';
import { CitationList } from '@/ui/CitationChip';
import styles from './ExamReviewSheet.module.css';

export interface ExamReviewSheetProps {
  sheet: ExamReviewSheetDocument;
}

function CitedItem({ item }: { item: MaybeCited }) {
  return (
    <li className={styles.item}>
      <span>{citedText(item)}</span>
      <CitationList citations={citedCitations(item)} />
    </li>
  );
}

/**
 * The last-minute sheet, exactly as it was written and stored.
 *
 * Reopening it is a database read: the citations were denormalised when it was
 * generated, so they resolve here with no provider call and no join.
 */
export function ExamReviewSheet({ sheet }: ExamReviewSheetProps) {
  return (
    <article className={styles.sheet}>
      <h3 className={styles.title}>{sheet.title}</h3>

      {sheet.topics.map((topic) => (
        <section key={topic.topic_key || topic.topic_label} className={styles.topic}>
          <h4 className={styles.topicTitle}>{topic.topic_label}</h4>

          {topic.must_remember.length > 0 ? (
            <>
              <p className={styles.label}>Must remember</p>
              <ul className={styles.items}>
                {topic.must_remember.map((item, index) => (
                  <CitedItem key={`${citationLabelKey(item)}-${index}`} item={item} />
                ))}
              </ul>
            </>
          ) : null}

          {topic.traps.length > 0 ? (
            <>
              <p className={styles.label}>Where this catches people out</p>
              <ul className={styles.items}>
                {topic.traps.map((item, index) => (
                  <CitedItem key={`${citationLabelKey(item)}-${index}`} item={item} />
                ))}
              </ul>
            </>
          ) : null}
        </section>
      ))}

      {sheet.final_checks.length > 0 ? (
        <section className={styles.topic}>
          <h4 className={styles.topicTitle}>Final checks</h4>
          <ul className={styles.items}>
            {sheet.final_checks.map((item, index) => (
              <CitedItem key={`${citationLabelKey(item)}-${index}`} item={item} />
            ))}
          </ul>
        </section>
      ) : null}

      {sheet.confidence_notes ? (
        <p className={styles.notes}>{sheet.confidence_notes}</p>
      ) : null}
    </article>
  );
}

/** A stable-enough key from the item itself, so no index stands alone. */
function citationLabelKey(item: MaybeCited): string {
  const citations = citedCitations(item);
  return citations.length > 0 ? citationLabel(citations[0]) : citedText(item).slice(0, 32);
}
