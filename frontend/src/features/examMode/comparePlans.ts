import type { ExamPlanTopicView, ExamPlanView } from '@/api/types';

/**
 * What changed between two saved plan versions.
 *
 * Joined on `topic_key`, never on `display_label`: the label is what a model
 * wrote and can be reworded between analyses, while the canonical key is the
 * identity Python assigned. Comparing labels would report a rename as a topic
 * being dropped and another appearing.
 *
 * Pure, and derived only from what the two documents already store. Nothing is
 * recomputed against today's course, because a historical ranking re-scored
 * with current data would no longer be the plan the student studied from.
 */

export interface TopicChange {
  topicKey: string;
  label: string;
  before: ExamPlanTopicView | null;
  after: ExamPlanTopicView | null;
  rankDelta: number | null;
  scoreDelta: number | null;
  bandChanged: boolean;
  priorityChanged: boolean;
  masteryChanged: boolean;
}

export interface PlanComparison {
  added: TopicChange[];
  removed: TopicChange[];
  changed: TopicChange[];
  unchanged: TopicChange[];
  analysisChanged: boolean;
  examDateChanged: boolean;
  signalsChanged: string[];
}

function change(
  topicKey: string,
  before: ExamPlanTopicView | null,
  after: ExamPlanTopicView | null,
): TopicChange {
  return {
    topicKey,
    label: after?.display_label ?? before?.display_label ?? topicKey,
    before,
    after,
    rankDelta: before && after ? after.rank - before.rank : null,
    scoreDelta: before && after ? after.priority_score - before.priority_score : null,
    bandChanged: Boolean(before && after && before.priority_band !== after.priority_band),
    priorityChanged: Boolean(before && after && before.is_high_priority !== after.is_high_priority),
    masteryChanged: Boolean(
      before && after && before.mastery_percentage !== after.mastery_percentage,
    ),
  };
}

function isChanged(entry: TopicChange): boolean {
  return (
    entry.rankDelta !== 0 ||
    entry.scoreDelta !== 0 ||
    entry.bandChanged ||
    entry.priorityChanged ||
    entry.masteryChanged
  );
}

export function comparePlans(before: ExamPlanView, after: ExamPlanView): PlanComparison {
  const byKeyBefore = new Map(before.topics.map((topic) => [topic.topic_key, topic]));
  const byKeyAfter = new Map(after.topics.map((topic) => [topic.topic_key, topic]));

  const added: TopicChange[] = [];
  const removed: TopicChange[] = [];
  const changed: TopicChange[] = [];
  const unchanged: TopicChange[] = [];

  for (const topic of after.topics) {
    const previous = byKeyBefore.get(topic.topic_key) ?? null;
    const entry = change(topic.topic_key, previous, topic);
    if (!previous) added.push(entry);
    else if (isChanged(entry)) changed.push(entry);
    else unchanged.push(entry);
  }

  for (const topic of before.topics) {
    if (!byKeyAfter.has(topic.topic_key)) {
      removed.push(change(topic.topic_key, topic, null));
    }
  }

  const signalNames = new Set([
    ...Object.keys(before.signals_available ?? {}),
    ...Object.keys(after.signals_available ?? {}),
  ]);
  const signalsChanged = [...signalNames].filter(
    (name) => (before.signals_available?.[name] ?? false) !== (after.signals_available?.[name] ?? false),
  );

  return {
    added,
    removed,
    changed,
    unchanged,
    analysisChanged: before.analysis_output_id !== after.analysis_output_id,
    examDateChanged: (before.exam_date ?? null) !== (after.exam_date ?? null),
    signalsChanged: signalsChanged.sort(),
  };
}
