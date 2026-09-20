import { useMemo } from 'react';
import { examModeAPI } from '@/api/examMode';
import { queryKeys } from '@/api/queryKeys';
import type { ExamAnalysisView } from '@/api/types';
import { useQuery } from '@/lib/query/useQuery';
import { TopicSuggestions } from './TopicSuggestions';

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

  const suggestions = useMemo(
    () =>
      (analysis.data?.topics ?? [])
        .filter((topic) => !topic.in_course_topics)
        .map((topic) => topic.display_label),
    [analysis.data],
  );

  return (
    <TopicSuggestions
      headingId="discovered-topics"
      title="Found by Exam Mode"
      lede="Read out of your material but not in your topic list. Adding one tells the ranking you consider it part of this course."
      suggestions={suggestions}
      declared={declared}
      onAdd={onAdd}
      disabled={disabled}
    />
  );
}
