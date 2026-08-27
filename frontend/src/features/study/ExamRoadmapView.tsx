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

interface MaterialCoverageItem {
  documentId: string;
  documentLabel: string;
  topicNames: string[];
  days: number[];
  pageRangeText: string | null;
}

function aggregateMaterialCoverage(days: readonly RoadmapDay[]): MaterialCoverageItem[] {
  const map = new Map<string, MaterialCoverageItem>();

  for (const day of days) {
    for (const topic of day.topics) {
      for (const mat of topic.materials) {
        let existing = map.get(mat.document_id);
        if (!existing) {
          existing = {
            documentId: mat.document_id,
            documentLabel: mat.document_label,
            topicNames: [],
            days: [],
            pageRangeText: null,
          };
          map.set(mat.document_id, existing);
        }
        if (!existing.topicNames.includes(topic.topic)) {
          existing.topicNames.push(topic.topic);
        }
        if (!existing.days.includes(day.day_index)) {
          existing.days.push(day.day_index);
        }
        if (mat.page_start != null && !existing.pageRangeText) {
          existing.pageRangeText =
            mat.page_end != null && mat.page_end !== mat.page_start
              ? `pp. ${mat.page_start}–${mat.page_end}`
              : `p. ${mat.page_start}`;
        }
      }
    }
  }

  return Array.from(map.values()).sort((a, b) => a.days[0] - b.days[0]);
}

function renderDayCard(day: RoadmapDay) {
  const dayMaterials = Array.from(
    new Map(
      day.topics
        .flatMap((t) => t.materials)
        .map((m) => [m.document_id, m]),
    ).values(),
  );

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

      {dayMaterials.length > 0 ? (
        <div className={styles.dayMaterialsBar}>
          <span className={styles.dayMaterialsLabel}>Lectures for today:</span>
          <div className={styles.dayMaterialsList}>
            {dayMaterials.map((mat) => (
              <span key={mat.document_id} className={styles.dayMaterialChip}>
                <BookOpen size={11} aria-hidden="true" />
                <span>
                  {mat.document_label}
                  {mat.page_start != null
                    ? mat.page_end != null && mat.page_end !== mat.page_start
                      ? ` (pp. ${mat.page_start}–${mat.page_end})`
                      : ` (p. ${mat.page_start})`
                    : ''}
                </span>
              </span>
            ))}
          </div>
        </div>
      ) : null}

      <div className={styles.topicList}>
        {day.topics.map(renderTopicCard)}
      </div>
    </article>
  );
}

export function ExamRoadmapView({ roadmap }: ExamRoadmapViewProps) {
  const coverageItems = aggregateMaterialCoverage(roadmap.days);

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

      {coverageItems.length > 0 ? (
        <section className={styles.materialsSection} aria-label="Course materials breakdown">
          <div className={styles.materialsSectionHeader}>
            <h4 className={styles.materialsSectionTitle}>
              Course Materials & Lectures Breakdown ({coverageItems.length})
            </h4>
            <span className={styles.materialsSectionSubtitle}>
              Which uploaded lecture documents and page ranges are mapped into this roadmap
            </span>
          </div>

          <div className={styles.materialsGrid}>
            {coverageItems.map((item) => (
              <div key={item.documentId} className={styles.materialCoverageCard}>
                <div className={styles.materialCoverageHeader}>
                  <span className={styles.materialCoverageLabel}>{item.documentLabel}</span>
                  {item.pageRangeText ? (
                    <Badge tone="accent">{item.pageRangeText}</Badge>
                  ) : (
                    <Badge tone="neutral">Full document</Badge>
                  )}
                </div>
                <div className={styles.materialCoverageMeta}>
                  <span className={styles.coverageDaysPill}>
                    Scheduled: Day{item.days.length === 1 ? '' : 's'} {item.days.slice(0, 6).join(', ')}{item.days.length > 6 ? '…' : ''}
                  </span>
                  <span className={styles.coverageTopicsCount}>
                    {item.topicNames.length} topic{item.topicNames.length === 1 ? '' : 's'}
                  </span>
                </div>
                <div className={styles.materialCoverageTopics}>
                  {item.topicNames.slice(0, 4).map((t) => (
                    <span key={t} className={styles.coverageTopicPill}>{t}</span>
                  ))}
                  {item.topicNames.length > 4 ? (
                    <span className={styles.coverageTopicMore}>+{item.topicNames.length - 4} more</span>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <div className={styles.materialNoticeCard}>
          <div className={styles.materialNoticeHeader}>
            <BookOpen size={16} aria-hidden="true" />
            <h4 className={styles.materialNoticeTitle}>Attach Your Lecture Documents & Slides</h4>
          </div>
          <p className={styles.materialNoticeBody}>
            No document citations were matched above the relevance floor for these topics. Upload your course slide decks (PDF/TXT) and ensure course topics match your syllabus chapters to attach exact page ranges to each day.
          </p>
        </div>
      )}

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
