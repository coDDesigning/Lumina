import { BookOpen, Calendar, Clock, Flag, Layers, Sparkles } from 'lucide-react';
import type { ExamRoadmap, RoadmapDay, RoadmapTopic, RoadmapDayKind, RoadmapHorizon } from '@/api/types';
import { Badge, type BadgeTone } from '@/ui/Badge';
import styles from './ExamRoadmapView.module.css';

export interface ExamRoadmapViewProps {
  roadmap: ExamRoadmap;
}

const KIND_LABELS: Record<RoadmapDayKind, string> = {
  study: 'Study day',
  review: 'Review day',
  final_review: 'Final review',
  last_minute: 'Last-minute review',
};

const KIND_TONES: Record<RoadmapDayKind, BadgeTone> = {
  study: 'info',
  review: 'accent',
  final_review: 'warning',
  last_minute: 'destructive',
};

const HORIZON_LABELS: Record<RoadmapHorizon, string> = {
  standard: 'Standard schedule',
  one_day: 'Triage: Exam tomorrow',
  zero_day: 'Triage: Exam today',
  long: '90-day horizon',
};

function formatDate(iso: string): string {
  const parts = iso.split('-').map(Number);
  if (parts.length !== 3 || parts.some(isNaN)) {
    return iso;
  }
  const date = new Date(parts[0], parts[1] - 1, parts[2]);
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
}

function materialStatusNote(status: RoadmapTopic['material_status']): string | null {
  switch (status) {
    case 'no_match':
      return 'No matching passages found above relevance floor';
    case 'not_indexed':
      return 'Course material is still pending indexing';
    case 'no_material':
      return 'No course documents uploaded yet';
    default:
      return null;
  }
}

function renderTopicCard(topic: RoadmapTopic) {
  const statusNote = materialStatusNote(topic.material_status);

  return (
    <div key={`${topic.topic}-${topic.pass_number}`} className={styles.topicCard}>
      <div className={styles.topicTop}>
        <span className={styles.topicName}>{topic.topic}</span>
        <div className={styles.topicBadges}>
          <Badge tone="accent">Pass {topic.pass_number}</Badge>
          {topic.mastery_percentage !== null && topic.mastery_percentage !== undefined ? (
            <Badge tone={topic.mastery_percentage >= 75 ? 'success' : 'warning'}>
              {topic.mastery_percentage}% mastery
            </Badge>
          ) : (
            <Badge tone="neutral">Unquizzed</Badge>
          )}
        </div>
      </div>

      <p className={styles.goalText}>{topic.goal}</p>

      {topic.materials.length > 0 ? (
        <div className={styles.materialArea}>
          <span className={styles.materialLabel}>Recommended study material</span>
          <ul className={styles.materialList}>
            {topic.materials.map((mat) => (
              <li key={mat.document_id} className={styles.materialItem}>
                <BookOpen size={12} aria-hidden="true" />
                <span>
                  {mat.document_label}
                  {mat.page_start != null
                    ? mat.page_end != null && mat.page_end !== mat.page_start
                      ? ` (pp. ${mat.page_start}–${mat.page_end})`
                      : ` (p. ${mat.page_start})`
                    : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : statusNote ? (
        <p className={styles.materialGap}>{statusNote}</p>
      ) : null}
    </div>
  );
}

function renderDayCard(day: RoadmapDay) {
  return (
    <article
      key={day.day_index}
      className={`${styles.dayCard} ${day.is_exam_day ? styles.dayCardExam : ''}`}
    >
      <header className={styles.dayHeader}>
        <div className={styles.dayMeta}>
          <span className={styles.dayNumber}>Day {day.day_index}</span>
          <span className={styles.dayDate}>{formatDate(day.date)}</span>
        </div>
        <div className={styles.badgeRow}>
          {day.is_exam_day ? (
            <Badge tone="warning" icon={<Flag size={12} aria-hidden="true" />}>
              Exam day
            </Badge>
          ) : null}
          <Badge tone={KIND_TONES[day.kind]}>{KIND_LABELS[day.kind]}</Badge>
        </div>
      </header>

      <h3 className={styles.dayFocus}>{day.focus}</h3>

      <div className={styles.topicList}>
        {day.topics.map(renderTopicCard)}
      </div>
    </article>
  );
}

export function ExamRoadmapView({ roadmap }: ExamRoadmapViewProps) {
  return (
    <div className={styles.container}>
      <div className={styles.headerCard}>
        <div className={styles.headerTop}>
          <div className={styles.titleArea}>
            <h3 className={styles.title}>Study Schedule</h3>
            <p className={styles.subtitle}>
              Personalized day-by-day plan leading up to your exam on {formatDate(roadmap.exam_date)}
            </p>
          </div>
          <div className={styles.statsCluster}>
            <div className={styles.statItem}>
              <span className={styles.statNumber}>{roadmap.days_until_exam}</span>
              <span className={styles.statLabel}>Days until exam</span>
            </div>
            <div className={styles.statItem}>
              <span className={styles.statNumber}>{roadmap.scheduled_days}</span>
              <span className={styles.statLabel}>Days planned</span>
            </div>
          </div>
        </div>

        <div className={styles.badgeRow}>
          <Badge tone="accent" icon={<Calendar size={12} aria-hidden="true" />}>
            {HORIZON_LABELS[roadmap.horizon]}
          </Badge>
          <Badge tone="neutral" icon={<Layers size={12} aria-hidden="true" />}>
            Version {roadmap.roadmap_version}
          </Badge>
          {roadmap.adapted_from_output_id ? (
            <Badge tone="info" icon={<Sparkles size={12} aria-hidden="true" />}>
              Adapted from #{roadmap.adapted_from_output_id}
            </Badge>
          ) : null}
          {roadmap.lead_in_days > 0 ? (
            <Badge tone="processing" icon={<Clock size={12} aria-hidden="true" />}>
              {roadmap.lead_in_days} lead-in days
            </Badge>
          ) : null}
        </div>

        {roadmap.notes.length > 0 ? (
          <div className={styles.notesList}>
            {roadmap.notes.map((note, index) => (
              <p key={index} className={styles.noteItem}>
                {note}
              </p>
            ))}
          </div>
        ) : null}
      </div>

      <section aria-label="Schedule">
        <div className={styles.sectionHeader}>
          <h3 className={styles.sectionTitle}>Daily Schedule</h3>
        </div>

        <div className={styles.timeline}>
          {roadmap.days.map(renderDayCard)}
        </div>
      </section>

      {roadmap.deferred_topics.length > 0 ? (
        <section className={styles.deferredSection} aria-label="Deferred topics">
          <h4 className={styles.deferredTitle}>Topics deferred due to time horizon</h4>
          <ul className={styles.deferredList}>
            {roadmap.deferred_topics.map((item) => (
              <li key={item.topic} className={styles.deferredItem}>
                {item.topic}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
