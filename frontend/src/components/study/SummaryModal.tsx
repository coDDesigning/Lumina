import { useCallback, useEffect, useRef, useState } from 'react';
import { BookOpen, Check, Copy, Download, Sparkles, XCircle } from 'lucide-react';
import { studyGuideAPI } from '../../api/studyGuide';
import { settingsAPI } from '../../api/settings';
import {
  describeGenerationError,
  isAbortError,
  isInsufficientCredits,
} from '../../api/errors';
import { useCredits } from '../../context/CreditContext';
import CreditBalance from '../credits/CreditBalance';
import CreditExhaustedNotice from '../credits/CreditExhaustedNotice';
import type {
  DetailLevel,
  StudyGuideGenerationResult,
  SummaryFormat,
  SummaryLength,
  SummaryMode,
} from '../../api/types';
import { StudyGuideView } from './StudyGuideView';
import { studyGuideFileName, studyGuideToMarkdown } from './studyGuideMarkdown';
import { Dialog } from '@/ui/Dialog';
import { ALL_TOPICS, topicOptions } from '@/features/study/topicOptions';
import './study.css';

interface SummaryModalProps {
  courseId: number;
  courseName: string;
  topics: string[];
  readyDocumentCount: number;
  onClose: () => void;
}

type SummaryState =
  | { phase: 'idle' }
  | { phase: 'generating' }
  | { phase: 'success'; result: StudyGuideGenerationResult }
  | { phase: 'error'; message: string; retryable: boolean };


const FORMAT_OPTIONS: { value: SummaryFormat; label: string }[] = [
  { value: 'overview', label: 'Quick Overview & Essentials' },
  { value: 'comprehensive', label: 'Comprehensive Study Guide' },
  { value: 'key_concepts', label: 'Key Definitions & Formulas' },
  { value: 'exam_tips', label: 'High-Yield Exam Cram Sheet' },
];

const LENGTH_OPTIONS: { value: SummaryLength; label: string }[] = [
  { value: 'short', label: 'Short' },
  { value: 'medium', label: 'Medium' },
  { value: 'long', label: 'Long' },
];

const DETAIL_OPTIONS: { value: DetailLevel; label: string }[] = [
  { value: 'basic', label: 'Basic — essential definitions' },
  { value: 'standard', label: 'Standard — definitions and reasoning' },
  { value: 'detailed', label: 'Detailed — mechanisms and examples' },
];

const MODE_OPTIONS: { value: SummaryMode; label: string }[] = [
  { value: 'general', label: 'General understanding' },
  { value: 'exam_focused', label: 'Exam focused' },
];

export function SummaryModal({
  courseId,
  courseName,
  topics,
  readyDocumentCount,
  onClose,
}: SummaryModalProps) {
  const [summaryFormat, setSummaryFormat] = useState<SummaryFormat>('comprehensive');
  const [topicFocus, setTopicFocus] = useState(ALL_TOPICS);
  const [summaryLength, setSummaryLength] = useState<SummaryLength>('medium');
  const [detailLevel, setDetailLevel] = useState<DetailLevel>('standard');
  const [summaryMode, setSummaryMode] = useState<SummaryMode>('general');
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [state, setState] = useState<SummaryState>({ phase: 'idle' });
  const [elapsed, setElapsed] = useState(0);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    let active = true;
    settingsAPI
      .get(courseId)
      .then((data) => {
        if (!active) return;
        let len: SummaryLength = 'medium';
        const rawLen = data.summary_length?.toLowerCase();
        if (rawLen === 'short') len = 'short';
        else if (rawLen === 'long') len = 'long';

        let detail: DetailLevel = 'standard';
        const rawDetail = data.detail_level?.toLowerCase();
        if (rawDetail === 'concise' || rawDetail === 'basic') detail = 'basic';
        else if (rawDetail === 'detailed') detail = 'detailed';

        let mode: SummaryMode = 'general';
        const rawMode = data.study_mode?.toLowerCase();
        if (rawMode === 'exam' || rawMode === 'exam_focused') mode = 'exam_focused';

        setSummaryLength(len);
        setDetailLevel(detail);
        setSummaryMode(mode);
      })
      .catch(() => {
        // Keep fallback defaults if settings fetch fails
      });
    return () => {
      active = false;
    };
  }, [courseId]);

  useEffect(() => {
    if (state.phase !== 'generating') return;
    setElapsed(0);
    const timer = setInterval(() => setElapsed((seconds) => seconds + 1), 1000);
    return () => clearInterval(timer);
  }, [state.phase]);

  useEffect(() => {
    if (copyState === 'idle') return;
    const timer = setTimeout(() => setCopyState('idle'), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  const { refresh, canAfford, isMetered } = useCredits();
  const lastResultRef = useRef<StudyGuideGenerationResult | null>(null);
  const exhausted = isMetered && !canAfford('study_guide');

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
      if (controller.signal.aborted) return;
      lastResultRef.current = result;
      setState({ phase: 'success', result });
      void refresh();
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) return;
      const described = describeGenerationError(
        error,
        'The study guide could not be generated.',
      );
      if (isInsufficientCredits(described)) {
        // Correct the balance first, so the screen cannot claim credits remain
        // while also saying there are none. A guide already generated stays put:
        // only the action becomes unavailable.
        await refresh();
        const previous = lastResultRef.current;
        setState(previous ? { phase: 'success', result: previous } : { phase: 'idle' });
        return;
      }
      setState({
        phase: 'error',
        message: described.message,
        retryable: described.retryable,
      });
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
    if (!result) return;
    try {
      await navigator.clipboard.writeText(studyGuideToMarkdown(result, courseName));
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  const handleDownload = () => {
    if (!result) return;
    const blob = new Blob([studyGuideToMarkdown(result, courseName)], {
      type: 'text/markdown',
    });
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
  const hasMaterial = readyDocumentCount > 0;
  const guide = result?.study_guide ?? null;

  const modalFooter = (
    <>
      {state.phase === 'generating' ? (
        <button className="secondary-button" type="button" onClick={handleCancel}>
          Cancel
        </button>
      ) : null}

      {state.phase === 'idle' ? (
        <>
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
          <CreditBalance source="study_guide" />
          <button
            className="primary-button"
            type="button"
            onClick={handleGenerate}
            disabled={!hasMaterial || exhausted}
          >
            <Sparkles aria-hidden="true" />
            Generate study guide
          </button>
        </>
      ) : null}

      {state.phase === 'error' ? (
        <>
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
          <CreditBalance source="study_guide" />
          <button
            className="primary-button"
            type="button"
            onClick={handleGenerate}
            disabled={exhausted}
          >
            {state.retryable ? 'Try again' : 'Try again later'}
          </button>
        </>
      ) : null}

      {state.phase === 'success' ? (
        <>
          <button
            className="secondary-button"
            type="button"
            onClick={() => setState({ phase: 'idle' })}
          >
            New study guide
          </button>
          <CreditBalance source="study_guide" />
          <div className="summary-footer-actions">
            <button className="secondary-button" type="button" onClick={handleCopy}>
              {copyState === 'copied' ? (
                <Check aria-hidden="true" />
              ) : (
                <Copy aria-hidden="true" />
              )}
              {copyState === 'copied'
                ? 'Copied!'
                : copyState === 'failed'
                  ? 'Copy failed'
                  : 'Copy'}
            </button>
            <button
              className="primary-button"
              type="button"
              onClick={handleDownload}
            >
              <Download aria-hidden="true" />
              Download MD
            </button>
          </div>
        </>
      ) : null}
    </>
  );

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Study guide"
      description="Generate clean, high-retention notes from your course sources"
      footer={modalFooter}
      mark={<Sparkles aria-hidden="true" />}
    >

        <div className="study-modal-content">
          {state.phase === 'generating' ? (
            <div className="study-loading-state">
              <div className="study-pulse-spinner" />
              <h3>Generating Structured Study Guide</h3>
              <p>Reading your course material and writing the guide… {elapsed}s</p>
              <p>This usually takes 20-60 seconds.</p>
            </div>
          ) : null}

          {state.phase === 'error' ? (
            <div className="summary-container">
              <div className="summary-section-card summary-notice is-danger" role="alert">
                <h4>
                  <XCircle aria-hidden="true" />
                  Generation failed
                </h4>
                <p>{state.message}</p>
              </div>
            </div>
          ) : null}

          {exhausted && state.phase !== 'generating' ? (
            <div className="summary-container">
              <CreditExhaustedNotice source="study_guide" action="a study guide" />
            </div>
          ) : null}

          {state.phase === 'idle' ? (
            <div className="summary-container">
              <div className="summary-section-card">
                <h4>
                  <BookOpen aria-hidden="true" />
                  Customize Your Study Summary
                </h4>
                <p className="summary-hint">
                  Select the target scope and synthesis format tailored for your revision
                  goals.
                </p>

                <div className="study-form-grid">
                  <div className="study-field-group">
                    <label htmlFor="summary-type">Summary Format</label>
                    <select
                      id="summary-type"
                      value={summaryFormat}
                      onChange={(event) =>
                        setSummaryFormat(event.target.value as SummaryFormat)
                      }
                    >
                      {FORMAT_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="summary-topic">Course Topic Focus</label>
                    <select
                      id="summary-topic"
                      value={topicFocus}
                      onChange={(event) => setTopicFocus(event.target.value)}
                    >
                      {topicChoices.map((topic) => (
                        <option key={topic} value={topic}>
                          {topic}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="summary-length">Summary Length</label>
                    <select
                      id="summary-length"
                      value={summaryLength}
                      onChange={(event) =>
                        setSummaryLength(event.target.value as SummaryLength)
                      }
                    >
                      {LENGTH_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="summary-detail">Detail Level</label>
                    <select
                      id="summary-detail"
                      value={detailLevel}
                      onChange={(event) =>
                        setDetailLevel(event.target.value as DetailLevel)
                      }
                    >
                      {DETAIL_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div className="study-field-group">
                    <label htmlFor="summary-mode">Summary Mode</label>
                    <select
                      id="summary-mode"
                      value={summaryMode}
                      onChange={(event) =>
                        setSummaryMode(event.target.value as SummaryMode)
                      }
                    >
                      {MODE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="study-toggle-group">
                  <label className="study-toggle-label">
                    <input
                      type="checkbox"
                      checked={includeProfileContext}
                      onChange={(event) => setIncludeProfileContext(event.target.checked)}
                    />
                    <span>Include personal study profile context</span>
                  </label>
                  <p className="study-toggle-caption">
                    Includes your profile background as supplementary context. Course material remains primary and authoritative.
                  </p>
                </div>

                {!hasMaterial ? (
                  <p className="summary-empty-state">
                    This course has no processed material yet. Add a source and wait until
                    it shows Ready.
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          {guide && result ? (
            <StudyGuideView guide={guide} context={result} />
          ) : null}
        </div>

    </Dialog>
  );
}
