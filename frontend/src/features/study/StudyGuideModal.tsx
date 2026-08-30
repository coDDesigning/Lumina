import { useEffect, useRef, useState } from 'react';
import { BookOpen, Sparkles } from 'lucide-react';
import { describeGenerationError, isAbortError, isInsufficientCredits } from '@/api/errors';
import type { GenerationFailure } from '@/api/errors';
import { studyGuideAPI } from '@/api/studyGuide';
import type {
  DetailLevel,
  SummaryFormat,
  SummaryLength,
  SummaryMode,
} from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { useCredits } from '@/context/CreditContext';
import { useCourseSettings } from '@/features/courses/useCourseSettings';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { Select } from '@/ui/Input';
import { GenerationError, NoMaterialNotice, SetupPanel } from './GenerationStates';
import { ALL_TOPICS, topicOptions } from './topicOptions';
import styles from './StudyGuideModal.module.css';

export interface StudyGuideModalProps {
  courseId: number;
  courseName: string;
  topics: string[];
  readyDocumentCount: number;
  onClose: () => void;
  onQueued?: (jobId: number) => void;
  onGenerated?: (outputId: number) => void;
}

const FORMAT_OPTIONS: { value: SummaryFormat; label: string }[] = [
  { value: 'comprehensive', label: 'Full study guide' },
  { value: 'overview', label: 'Quick overview' },
  { value: 'key_concepts', label: 'Definitions and formulas' },
  { value: 'exam_tips', label: 'Cram sheet' },
];

const LENGTH_OPTIONS: { value: SummaryLength; label: string }[] = [
  { value: 'short', label: 'Short' },
  { value: 'medium', label: 'Medium' },
  { value: 'long', label: 'Long' },
];

const DETAIL_OPTIONS: { value: DetailLevel; label: string }[] = [
  { value: 'basic', label: 'Just the definitions' },
  { value: 'standard', label: 'Definitions and reasoning' },
  { value: 'detailed', label: 'Mechanisms and worked examples' },
];

const MODE_OPTIONS: { value: SummaryMode; label: string }[] = [
  { value: 'general', label: 'Understanding the subject' },
  { value: 'exam_focused', label: 'Getting ready for an exam' },
];

export function StudyGuideModal({
  courseId,
  courseName,
  topics,
  readyDocumentCount,
  onClose,
  onQueued,
}: StudyGuideModalProps) {
  const [summaryFormat, setSummaryFormat] = useState<SummaryFormat>('comprehensive');
  const [topicFocus, setTopicFocus] = useState(ALL_TOPICS);
  const [summaryLength, setSummaryLength] = useState<SummaryLength>('medium');
  const [detailLevel, setDetailLevel] = useState<DetailLevel>('standard');
  const [summaryMode, setSummaryMode] = useState<SummaryMode>('general');
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [isQueueing, setIsQueueing] = useState(false);
  const [failure, setFailure] = useState<GenerationFailure | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const settings = useCourseSettings(courseId);
  const defaults = settings.status === 'success' ? settings.data : undefined;
  useEffect(() => {
    if (!defaults) return;
    const rawLength = defaults.summary_length?.toLowerCase();
    setSummaryLength(rawLength === 'short' ? 'short' : rawLength === 'long' ? 'long' : 'medium');
    const rawDetail = defaults.detail_level?.toLowerCase();
    setDetailLevel(
      rawDetail === 'concise' || rawDetail === 'basic'
        ? 'basic'
        : rawDetail === 'detailed'
          ? 'detailed'
          : 'standard',
    );
    const rawMode = defaults.study_mode?.toLowerCase();
    setSummaryMode(rawMode === 'exam' || rawMode === 'exam_focused' ? 'exam_focused' : 'general');
  }, [defaults]);

  const { refresh, canAfford, isMetered } = useCredits();
  const exhausted = isMetered && !canAfford('study_guide');
  const hasMaterial = readyDocumentCount > 0;

  const handleQueue = async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setFailure(null);
    setIsQueueing(true);
    try {
      const accepted = await studyGuideAPI.enqueue(
        courseId,
        {
          summary_format: summaryFormat,
          topic_focus: topicFocus,
          summary_length: summaryLength,
          detail_level: detailLevel,
          summary_mode: summaryMode,
          use_profile_knowledge: includeProfileContext,
          include_profile_context: includeProfileContext,
        },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      await refresh();
      onQueued?.(accepted.job_id);
      onClose();
    } catch (caught) {
      if (controller.signal.aborted || isAbortError(caught)) return;
      const described = describeGenerationError(caught, 'The study guide could not be queued.');
      if (isInsufficientCredits(described)) await refresh();
      setFailure(described);
    } finally {
      if (!controller.signal.aborted) setIsQueueing(false);
    }
  };

  const footer = (
    <>
      <Button onClick={onClose}>Not now</Button>
      <div className={styles.footerRight}>
        <CreditBalance source="study_guide" />
        <Button
          variant="primary"
          onClick={() => void handleQueue()}
          disabled={!hasMaterial || exhausted || isQueueing}
          isLoading={isQueueing}
          loadingLabel="Queueing study guide"
          icon={<Sparkles aria-hidden="true" />}
        >
          Write my study guide
        </Button>
      </div>
    </>
  );

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Study guide"
      description={courseName}
      mark={<BookOpen aria-hidden="true" />}
      footer={footer}
      spreadFooter
    >
      <SetupPanel lede="A written guide to your course material: what matters, what it means, and where people usually slip.">
        {hasMaterial ? (
          <>
            <div className={styles.grid}>
              <Select
                label="What kind of guide"
                value={summaryFormat}
                onChange={(event) => setSummaryFormat(event.target.value as SummaryFormat)}
              >
                {FORMAT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </Select>
              <Select label="Which topic" value={topicFocus} onChange={(event) => setTopicFocus(event.target.value)}>
                {topicOptions(topics).map((topic) => <option key={topic} value={topic}>{topic}</option>)}
              </Select>
              <Select label="How long" value={summaryLength} onChange={(event) => setSummaryLength(event.target.value as SummaryLength)}>
                {LENGTH_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
              <Select label="How deep" value={detailLevel} onChange={(event) => setDetailLevel(event.target.value as DetailLevel)}>
                {DETAIL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
              <Select label="What it is for" value={summaryMode} onChange={(event) => setSummaryMode(event.target.value as SummaryMode)}>
                {MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </Select>
            </div>
            <Checkbox
              label="Use my study profile"
              description="Adds your background as supporting context. Your course material stays primary."
              checked={includeProfileContext}
              onChange={(event) => setIncludeProfileContext(event.target.checked)}
            />
          </>
        ) : (
          <NoMaterialNotice what="A study guide" />
        )}
      </SetupPanel>

      {exhausted ? <CreditExhaustedNotice source="study_guide" action="a study guide" /> : null}
      {failure && !isInsufficientCredits(failure) ? (
        <GenerationError
          failure={failure}
          onRetry={() => void handleQueue()}
          onBroadenTopic={() => {
            setTopicFocus(ALL_TOPICS);
            setFailure(null);
          }}
          onSeeSources={onClose}
        />
      ) : null}
    </Dialog>
  );
}

export default StudyGuideModal;
