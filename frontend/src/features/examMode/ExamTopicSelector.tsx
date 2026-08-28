import { Star } from 'lucide-react';
import type { ExamAnalysisView, ExamTopicCandidateView } from '@/api/types';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { CitationList } from '@/ui/CitationChip';
import { IconButton } from '@/ui/IconButton';
import styles from './ExamTopicSelector.module.css';

export interface ExamTopicSelectorProps {
  analysis: ExamAnalysisView;
  selected: ReadonlySet<string>;
  highPriority: ReadonlySet<string>;
  onToggle: (topicKey: string) => void;
  onTogglePriority: (topicKey: string) => void;
  onSelectAll: () => void;
  disabled?: boolean;
}

/** Where a topic was found. Only the evidence the analysis actually reports. */
function evidenceOf(topic: ExamTopicCandidateView): string[] {
  const found: string[] = [];
  if (topic.in_syllabus) {
    found.push(
      topic.syllabus_mention_count > 0
        ? `Syllabus (${topic.syllabus_mention_count} mentions)`
        : 'Syllabus',
    );
  }
  if (topic.in_course_topics) found.push('Course topics');
  if (topic.in_past_exams) {
    found.push(
      topic.past_exam_question_count > 0
        ? `${topic.past_exam_question_count} past-exam questions`
        : 'Past exams',
    );
  }
  if (topic.in_material) {
    found.push(
      topic.material_chunk_count > 0
        ? `${topic.material_chunk_count} passages of material`
        : 'Selected material',
    );
  }
  return found;
}

export function ExamTopicSelector({
  analysis,
  selected,
  highPriority,
  onToggle,
  onTogglePriority,
  onSelectAll,
  disabled,
}: ExamTopicSelectorProps) {
  const carryOver = analysis.selection_carry_over;
  const isNew = new Set(carryOver.new_topic_keys);
  const unsupported = carryOver.unsupported_topic_keys;
  const allSelected =
    analysis.topics.length > 0 &&
    analysis.topics.every((topic) => selected.has(topic.topic_key));

  return (
    <div className={styles.selector}>
      {analysis.manual_review_recommended ? (
        <Alert tone="info" title="Review these before you plan">
          These topics were read out of your material. Checking them yourself is what stops a
          plan being built around something the analysis misread.
        </Alert>
      ) : null}

      {carryOver.previous_plan_output_id ? (
        <Alert tone="info" title="Carried over from your last plan">
          {carryOver.preselected_topic_keys.length} previously selected{' '}
          {carryOver.preselected_topic_keys.length === 1 ? 'topic is' : 'topics are'} still
          supported and have been ticked for you.
          {isNew.size > 0
            ? ` ${isNew.size} new ${isNew.size === 1 ? 'topic is' : 'topics are'} marked for review.`
            : ''}
        </Alert>
      ) : null}

      {unsupported.length > 0 ? (
        <Alert tone="warning" title="No longer in this analysis">
          {unsupported.join(', ')} {unsupported.length === 1 ? 'was' : 'were'} in your previous
          plan but this scan did not find {unsupported.length === 1 ? 'it' : 'them'}. Nothing was
          dropped silently — you can rescan or plan without{' '}
          {unsupported.length === 1 ? 'it' : 'them'}.
        </Alert>
      ) : null}

      <div className={styles.panel}>
        <div className={styles.head}>
        <p className={styles.count}>
          <span className="tabular">{selected.size}</span> of{' '}
          <span className="tabular">{analysis.topics.length}</span>{' '}
          {analysis.topics.length === 1 ? 'topic' : 'topics'} selected
        </p>
        {!disabled ? (
          <Button variant="ghost" size="sm" onClick={onSelectAll} disabled={allSelected}>
            Select every discovered topic
          </Button>
          ) : null}
        </div>

        <ul className={styles.list}>
        {analysis.topics.map((topic) => {
          const isSelected = selected.has(topic.topic_key);
          const isPriority = highPriority.has(topic.topic_key);
          const evidence = evidenceOf(topic);
          return (
            <li key={topic.topic_key} className={styles.row}>
              <div className={styles.rowMain}>
                <Checkbox
                  checked={isSelected}
                  disabled={disabled}
                  onChange={() => onToggle(topic.topic_key)}
                  label={
                    <span className={styles.label}>
                      <span className={styles.name}>{topic.display_label}</span>
                      <span className={styles.meta}>
                        {isNew.has(topic.topic_key) ? (
                          <Badge tone="accent">New in this scan</Badge>
                        ) : null}
                        {isPriority ? (
                          <Badge tone="warning" icon={<Star aria-hidden="true" />}>
                            High priority
                          </Badge>
                        ) : null}
                      </span>
                    </span>
                  }
                  description={evidence.length > 0 ? `Found in: ${evidence.join(' · ')}` : undefined}
                />
                {/*
                  Priority only means something for a topic that is in the plan,
                  so the control is absent until then rather than disabled with
                  a hint repeated down every row.
                */}
                {!disabled && isSelected ? (
                  <IconButton
                    label={
                      isPriority
                        ? `Remove high priority from ${topic.display_label}`
                        : `Mark ${topic.display_label} high priority`
                    }
                    icon={<Star aria-hidden="true" />}
                    tone={isPriority ? 'accent' : 'default'}
                    onClick={() => onTogglePriority(topic.topic_key)}
                  />
                ) : null}
              </div>
              <CitationList citations={topic.citations} />
            </li>
          );
        })}
        </ul>
      </div>
    </div>
  );
}
