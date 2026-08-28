import { Link } from 'react-router-dom';
import type { ExamRoadmap, RoadmapDay } from '@/api/types';
import { cx } from '@/lib/cx';
import { Alert } from '@/ui/Alert';
import { CitationList } from '@/ui/CitationChip';
import { formatRoadmapDay } from './examModeFormatters';
import styles from './ExamRoadmapPanel.module.css';

const KIND_LABELS: Record<string, string> = {
  study: 'Study',
  review: 'Review',
  final_review: 'Final review',
  last_minute: 'Last minute',
};

const MATERIAL_NOTES: Record<string, string> = {
  no_match: 'Nothing in your material matched this topic closely enough to cite.',
  not_indexed: 'Your material is not searchable yet, so no sources are attached.',
  no_material: 'This course has no processed material to study this from.',
  not_requested: 'Sources were not resolved for this plan.',
};

export interface ExamRoadmapPanelProps {
  courseId: number;
  planId: number;
  roadmap: ExamRoadmap;
}

function Day({ courseId, planId, day }: { courseId: number; planId: number; day: RoadmapDay }) {
  return (
    <li className={styles.day}>
      <div className={styles.dayMark}>
        <h3 className={styles.dayNumber}>{formatRoadmapDay(day.day_index)}</h3>
        <span className={cx(styles.dayKind, day.is_exam_day && styles.dayKindExam)}>
          {day.is_exam_day ? 'Exam day' : (KIND_LABELS[day.kind] ?? day.kind)}
        </span>
      </div>

      <div className={styles.dayBody}>
        <p className={styles.focus}>{day.focus}</p>
        <ul className={styles.topics}>
          {day.topics.map((topic, index) => (
            <li key={`${topic.topic}-${topic.pass_number}-${index}`} className={styles.topic}>
              <div className={styles.topicHead}>
                {topic.topic_key ? (
                  <Link
                    className={styles.topicTitle}
                    to={`/courses/${courseId}/exam-mode/plans/${planId}/topics/${encodeURIComponent(
                      topic.topic_key,
                    )}`}
                  >
                    {topic.topic}
                  </Link>
                ) : (
                  <span className={styles.topicTitle}>{topic.topic}</span>
                )}
                {topic.pass_number > 1 ? (
                  <span className={styles.pass}>
                    pass <span className="tabular">{topic.pass_number}</span>
                  </span>
                ) : null}
              </div>
              <p className={styles.goal}>{topic.goal}</p>
              {topic.material_status === 'resolved' ? (
                <CitationList citations={topic.citations} />
              ) : (
                <p className={styles.gap}>{MATERIAL_NOTES[topic.material_status]}</p>
              )}
            </li>
          ))}
        </ul>
      </div>
    </li>
  );
}

/**
 * The schedule, at the student's own pace.
 *
 * Reopening it is a database read: the citations were denormalised when the
 * roadmap was written, so they resolve here with no provider call.
 */
export function ExamRoadmapPanel({ courseId, planId, roadmap }: ExamRoadmapPanelProps) {
  return (
    <div className={styles.panel}>
      <p className={styles.lede}>
        <span className="tabular">{roadmap.scheduled_days}</span>{' '}
        {roadmap.scheduled_days === 1 ? 'day' : 'days'} of study · version{' '}
        <span className="tabular">{roadmap.roadmap_version}</span>
      </p>

      {roadmap.notes.map((note) => (
        <Alert key={note} tone="info">
          {note}
        </Alert>
      ))}

      {roadmap.deferred_topics.length > 0 ? (
        <Alert tone="warning" title="Not enough days for everything">
          {roadmap.deferred_topics.map((topic) => topic.topic).join(', ')} did not fit and{' '}
          {roadmap.deferred_topics.length === 1 ? 'was' : 'were'} left out rather than squeezed in.
        </Alert>
      ) : null}

      <ol className={styles.days}>
        {roadmap.days.map((day) => (
          <Day key={day.day_index} courseId={courseId} planId={planId} day={day} />
        ))}
      </ol>
    </div>
  );
}
