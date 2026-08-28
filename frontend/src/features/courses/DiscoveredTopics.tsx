import { useMemo } from 'react';
import { Plus } from 'lucide-react';
import { examModeAPI } from '@/api/examMode';
import { queryKeys } from '@/api/queryKeys';
import type { ExamAnalysisView } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';
import { Button } from '@/ui/Button';
import styles from './DiscoveredTopics.module.css';

export interface DiscoveredTopicsProps {
  courseId: number;
  /** What is in the form right now, so an addition disappears immediately. */
  declared: readonly string[];
  onAdd: (topics: string[]) => void;
  disabled?: boolean;
}

/**
 * Topics Exam Mode found that this course does not declare.
 *
 * Offered, never written automatically. `course_topics` is read back as
 * evidence when a plan is ranked -- a declared topic scores for being declared
 * -- so a feature that quietly added its own findings would grade its own
 * homework, and the "no syllabus evidence" warning would disappear from a
 * course that still has no syllabus. Adding one is the student's decision, and
 * from then on it is their declaration.
 *
 * Which candidates already match a declared topic is the backend's answer
 * (`in_course_topics`), not a second copy of its canonical-key matching, which
 * sorts and stems tokens and would drift the moment either side changed.
 */
export function DiscoveredTopics({
  courseId,
  declared,
  onAdd,
  disabled,
}: DiscoveredTopicsProps) {
  const analysis = useQuery<ExamAnalysisView>({
    key: queryKeys.examAnalysis(courseId, null),
    fetcher: ({ signal }) => examModeAPI.getAnalysis(courseId, null, { signal }),
    fallbackMessage: 'The topics Exam Mode found could not be loaded.',
  });

  const missing = useMemo(() => {
    // The flag is as of the last analysis, so anything already in the box is
    // filtered too -- that is what makes a just-added topic vanish from here
    // before the next scan.
    const inBox = new Set(declared.map((topic) => topic.trim().toLowerCase()));
    return (analysis.data?.topics ?? [])
      .filter((topic) => !topic.in_course_topics)
      .map((topic) => topic.display_label)
      .filter((label) => label.trim() && !inBox.has(label.trim().toLowerCase()));
  }, [analysis.data, declared]);

  // A course that was never analysed, or whose findings are all declared
  // already, has nothing to offer and says nothing.
  if (disabled || missing.length === 0) {
    return null;
  }

  return (
    <section className={styles.block} aria-labelledby="discovered-topics">
      <h3 id="discovered-topics" className={styles.label}>
        Found by Exam Mode
      </h3>
      <p className={styles.lede}>
        Read out of your material but not in your topic list. Adding one tells the ranking you
        consider it part of this course.
      </p>
      <ul className={styles.list}>
        {missing.map((label) => (
          <li key={label}>
            <Button
              variant="secondary"
              size="sm"
              wrap
              icon={<Plus aria-hidden="true" />}
              onClick={() => onAdd([label])}
            >
              {label}
            </Button>
          </li>
        ))}
      </ul>
      {missing.length > 1 ? (
        <Button variant="ghost" size="sm" onClick={() => onAdd(missing)}>
          Add all {missing.length}
        </Button>
      ) : null}
    </section>
  );
}
