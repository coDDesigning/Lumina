import type { ActivityItem } from '@/api/types';

export function activityHref(item: ActivityItem): string {
  const course = `/courses/${item.course_id}`;

  if (item.kind === 'attempt') {
    return item.quiz_id !== null && item.attempt_id !== null
      ? `${course}/practice/${item.quiz_id}/attempts/${item.attempt_id}`
      : course;
  }

  if (item.action_type === 'study_guide' && item.output_id !== null) {
    return `${course}?artifact=${item.output_id}`;
  }

  if (item.action_type === 'quiz' && item.quiz_id !== null) {
    return `${course}/practice/${item.quiz_id}`;
  }

  return course;
}
