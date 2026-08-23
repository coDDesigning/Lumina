import { useCallback, useEffect, useRef, useState } from 'react';
import { BookOpen, Check, Copy, Download, Sparkles } from 'lucide-react';
import { describeGenerationError, isAbortError, isInsufficientCredits } from '@/api/errors';
import { settingsAPI } from '@/api/settings';
import { studyGuideAPI } from '@/api/studyGuide';
import type {
  DetailLevel,
  StudyGuideGenerationResult,
  SummaryFormat,
  SummaryLength,
  SummaryMode,
} from '@/api/types';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import {
  studyGuideFileName,
  studyGuideToMarkdown,
} from '@/components/study/studyGuideMarkdown';
import { useCredits } from '@/context/CreditContext';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { Dialog } from '@/ui/Dialog';
import { Select } from '@/ui/Input';
import { GeneratingState, GenerationError, NoMaterialNotice, SetupPanel } from './GenerationStates';
import { Provenance } from './Provenance';
import { StudyGuide } from './StudyGuide';
import { ALL_TOPICS, topicOptions } from './topicOptions';
import styles from './StudyGuideModal.module.css';

export interface StudyGuideModalProps {
  courseId: number;
  courseName: string;
  topics: string[];
  readyDocumentCount: number;
  onClose: () => void;
}

type GuideState =
  | { phase: 'idle' }
  | { phase: 'generating' }
  | { phase: 'success'; result: StudyGuideGenerationResult }
  | { phase: 'error'; message: string; retryable: boolean };

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
}: StudyGuideModalProps) {
  const [summaryFormat, setSummaryFormat] = useState<SummaryFormat>('comprehensive');
  const [topicFocus, setTopicFocus] = useState(ALL_TOPICS);
  const [summaryLength, setSummaryLength] = useState<SummaryLength>('medium');
  const [detailLevel, setDetailLevel] = useState<DetailLevel>('standard');
  const [summaryMode, setSummaryMode] = useState<SummaryMode>('general');
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [state, setState] = useState<GuideState>({ phase: 'idle' });
  const [elapsed, setElapsed] = useState(0);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');

  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    let active = true;
    settingsAPI
      .get(courseId)
      .then((data) => {
        if (!active) {
          return;
        }
        const rawLength = data.summary_length?.toLowerCase();
        setSummaryLength(rawLength === 'short' ? 'short' : rawLength === 'long' ? 'long' : 'medium');

        const rawDetail = data.detail_level?.toLowerCase();
        setDetailLevel(
          rawDetail === 'concise' || rawDetail === 'basic'
            ? 'basic'
            : rawDetail === 'detailed'
              ? 'detailed'
              : 'standard',
        );

        const rawMode = data.study_mode?.toLowerCase();
        setSummaryMode(rawMode === 'exam' || rawMode === 'exam_focused' ? 'exam_focused' : 'general');
      })
      .catch(() => {
        if (!active) {
          return;
        }
        setSummaryLength('medium');
        setDetailLevel('standard');
        setSummaryMode('general');
      });
    return () => {
      active = false;
    };
  }, [courseId]);

  useEffect(() => {
    if (state.phase !== 'generating') {
      return;
    }
    setElapsed(0);
    const timer = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [state.phase]);

  useEffect(() => {
    if (copyState === 'idle') {
      return;
    }
    const timer = setTimeout(() => setCopyState('idle'), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  const { refresh, canAfford, isMetered } = useCredits();
  const lastResultRef = useRef<StudyGuideGenerationResult | null>(null);
  const exhausted = isMetered && !canAfford('study_guide');
  const hasMaterial = readyDocumentCount > 0;

  const handleGenerate = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState({ phase: 'generating' });

    try {
      const result = await studyGuideAPI.generate(
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
      if (controller.signal.aborted) {
        return;
      }
      lastResultRef.current = result;
      setState({ phase: 'success', result });
      void refresh();
    } catch (caught) {
      if (controller.signal.aborted || isAbortError(caught)) {
        return;
      }
      const described = describeGenerationError(caught, 'The study guide could not be generated.');
      if (isInsufficientCredits(described)) {
        await refresh();
        const previous = lastResultRef.current;
        setState(previous ? { phase: 'success', result: previous } : { phase: 'idle' });
        return;
      }
      setState({ phase: 'error', message: described.message, retryable: described.retryable });
    }
  }, [
    courseId,
    detailLevel,
    includeProfileContext,
    refresh,
    summaryFormat,
    summaryLength,
    summaryMode,
    topicFocus,
  ]);

  const handleCancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setState({ phase: 'idle' });
  }, []);

  const result = state.phase === 'success' ? state.result : null;

  const handleCopy = async () => {
    if (!result) {
      return;
    }
    try {
      await navigator.clipboard.writeText(studyGuideToMarkdown(result, courseName));
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  const handleDownload = () => {
    if (!result) {
      return;
    }
    const blob = new Blob([studyGuideToMarkdown(result, courseName)], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = studyGuideFileName(courseName);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const topicChoices = topicOptions(topics);

  const footer = (() => {
    if (state.phase === 'generating') {
      return <Button onClick={handleCancel}>Cancel</Button>;
    }

    if (state.phase === 'success') {
      return (
        <>
          <Button onClick={() => setState({ phase: 'idle' })}>Make another</Button>
          <div className={styles.footerRight}>
            <CreditBalance source="study_guide" />
            <Button
              onClick={() => void handleCopy()}
              icon={
                copyState === 'copied' ? (
                  <Check aria-hidden="true" />
                ) : (
                  <Copy aria-hidden="true" />
                )
              }
            >
              {copyState === 'copied' ? 'Copied' : copyState === 'failed' ? 'Copy failed' : 'Copy'}
            </Button>
            <Button
              variant="primary"
              onClick={handleDownload}
              icon={<Download aria-hidden="true" />}
            >
              Download
            </Button>
          </div>
        </>
      );
    }

    return (
      <>
        <Button onClick={onClose}>Close</Button>
        <div className={styles.footerRight}>
          <CreditBalance source="study_guide" />
          <Button
            variant="primary"
            onClick={() => void handleGenerate()}
            disabled={!hasMaterial || exhausted || (state.phase === 'error' && !state.retryable)}
            icon={<Sparkles aria-hidden="true" />}
          >
            {state.phase === 'error' ? 'Try again' : 'Write my study guide'}
          </Button>
        </div>
      </>
    );
  })();

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
      {state.phase === 'idle' ? (
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
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>

                <Select
                  label="Which topic"
                  value={topicFocus}
                  onChange={(event) => setTopicFocus(event.target.value)}
                >
                  {topicChoices.map((topic) => (
                    <option key={topic} value={topic}>
                      {topic}
                    </option>
                  ))}
                </Select>

                <Select
                  label="How long"
                  value={summaryLength}
                  onChange={(event) => setSummaryLength(event.target.value as SummaryLength)}
                >
                  {LENGTH_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>

                <Select
                  label="How deep"
                  value={detailLevel}
                  onChange={(event) => setDetailLevel(event.target.value as DetailLevel)}
                >
                  {DETAIL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>

                <Select
                  label="What it is for"
                  value={summaryMode}
                  onChange={(event) => setSummaryMode(event.target.value as SummaryMode)}
                >
                  {MODE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
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
      ) : null}

      {exhausted && state.phase !== 'generating' ? (
        <CreditExhaustedNotice source="study_guide" action="a study guide" />
      ) : null}

      {state.phase === 'generating' ? (
        <GeneratingState
          heading="Reading your material"
          detail="Pulling out the ideas that matter, the terms you need, and the mistakes worth avoiding."
          elapsed={elapsed}
        />
      ) : null}

      {state.phase === 'error' ? (
        <GenerationError
          message={state.message}
          retryable={state.retryable}
          onRetry={() => void handleGenerate()}
        />
      ) : null}

      {state.phase === 'success' ? (
        <>
          <StudyGuide guide={state.result.study_guide} context={state.result} />
          <Provenance context={state.result} className={styles.provenance} />
        </>
      ) : null}
    </Dialog>
  );
}

export default StudyGuideModal;
