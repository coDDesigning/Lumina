import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  Check,
  CheckCircle2,
  Copy,
  Download,
  FileText,
  Lightbulb,
  Sparkles,
  X,
  XCircle,
} from 'lucide-react';
import { studyGuideAPI } from '../../api/studyGuide';
import { describeGenerationError, isAbortError } from '../../api/errors';
import type { StudyGuideGenerationResult, SummaryFormat } from '../../api/types';
import { studyGuideFileName, studyGuideToMarkdown } from './studyGuideMarkdown';
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

const ALL_TOPICS = 'All Topics';

const FORMAT_OPTIONS: { value: SummaryFormat; label: string }[] = [
  { value: 'overview', label: 'Quick Overview & Essentials' },
  { value: 'comprehensive', label: 'Comprehensive Study Guide' },
  { value: 'key_concepts', label: 'Key Definitions & Formulas' },
  { value: 'exam_tips', label: 'High-Yield Exam Cram Sheet' },
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
  const [state, setState] = useState<SummaryState>({ phase: 'idle' });
  const [elapsed, setElapsed] = useState(0);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

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

  const handleGenerate = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState({ phase: 'generating' });

    try {
      const result = await studyGuideAPI.generate(
        courseId,
        { summary_format: summaryFormat, topic_focus: topicFocus },
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setState({ phase: 'success', result });
    } catch (error) {
      if (controller.signal.aborted || isAbortError(error)) return;
      const described = describeGenerationError(
        error,
        'The study guide could not be generated.',
      );
      setState({
        phase: 'error',
        message: described.message,
        retryable: described.retryable,
      });
    }
  }, [courseId, summaryFormat, topicFocus]);

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

  const topicOptions = [ALL_TOPICS, ...topics];
  const hasMaterial = readyDocumentCount > 0;
  const guide = result?.study_guide ?? null;

  return (
    <div className="study-modal-backdrop" role="dialog" aria-modal="true">
      <div className="study-modal large-modal">
        <header className="study-modal-header">
          <div>
            <h2>
              <Sparkles aria-hidden="true" />
              AI Study Summary Generator
            </h2>
            <p>Generate clean, high-retention notes from your course sources</p>
          </div>
          <button
            className="modal-close-button"
            type="button"
            onClick={onClose}
            aria-label="Close summary modal"
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="study-modal-body">
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
                      {topicOptions.map((topic) => (
                        <option key={topic} value={topic}>
                          {topic}
                        </option>
                      ))}
                    </select>
                  </div>
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
            <div className="summary-container">
              <div className="summary-meta-badge">
                <FileText aria-hidden="true" />
                <span>{guide.difficulty.level}</span> •{' '}
                <span>{guide.estimated_study_time}</span> •{' '}
                <span>
                  {guide.coverage.status} ({guide.coverage.estimated_completeness}%)
                </span>
              </div>

              {result.context_truncated ? (
                <div className="summary-section-card summary-notice" role="note">
                  <h4>Partial coverage</h4>
                  <p>
                    Built from {result.chunks_used} of {result.chunks_available} content
                    sections, so some material was left out.
                  </p>
                </div>
              ) : null}

              <div className="summary-section-card">
                <h4>
                  <BookOpen aria-hidden="true" />
                  {guide.title}
                </h4>
                <div className="summary-body-text">{guide.summary}</div>
                <p className="summary-hint">{guide.difficulty.reason}</p>
              </div>

              {guide.learning_objectives.length > 0 ? (
                <div className="summary-section-card">
                  <h4>
                    <Check aria-hidden="true" />
                    Learning Objectives
                  </h4>
                  <ul className="summary-bullet-list">
                    {guide.learning_objectives.map((objective, index) => (
                      <li key={index}>{objective}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {guide.key_points.length > 0 ? (
                <div className="summary-section-card">
                  <h4>
                    <Check aria-hidden="true" />
                    Key Points
                  </h4>
                  <ul className="summary-bullet-list">
                    {guide.key_points.map((point, index) => (
                      <li key={index}>{point}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {guide.important_terms.length > 0 ? (
                <div className="summary-section-card">
                  <h4>
                    <Lightbulb aria-hidden="true" />
                    Important Terms
                  </h4>
                  <div className="definitions-grid">
                    {guide.important_terms.map((term, index) => (
                      <div className="definition-item" key={index}>
                        <strong>{term.term}</strong>
                        <p>{term.definition}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {guide.common_mistakes.length > 0 ? (
                <div className="summary-section-card">
                  <h4>
                    <XCircle aria-hidden="true" />
                    Common Mistakes
                  </h4>
                  <ul className="summary-mistake-list">
                    {guide.common_mistakes.map((item, index) => (
                      <li key={index}>
                        <span className="mistake-line">
                          <XCircle aria-hidden="true" />
                          {item.mistake}
                        </span>
                        <span className="correction-line">
                          <CheckCircle2 aria-hidden="true" />
                          {item.correction}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {guide.exam_tips.lecture_based.length > 0 ||
              guide.exam_tips.ai_suggestions.length > 0 ? (
                <div className="summary-section-card">
                  <h4>
                    <Sparkles aria-hidden="true" />
                    Exam Tips
                  </h4>
                  {guide.exam_tips.lecture_based.length > 0 ? (
                    <>
                      <h5>From your lecture material</h5>
                      <ul className="summary-bullet-list">
                        {guide.exam_tips.lecture_based.map((tip, index) => (
                          <li key={index}>{tip}</li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                  {guide.exam_tips.ai_suggestions.length > 0 ? (
                    <>
                      <h5>AI suggestions</h5>
                      <ul className="summary-bullet-list">
                        {guide.exam_tips.ai_suggestions.map((tip, index) => (
                          <li key={index}>{tip}</li>
                        ))}
                      </ul>
                    </>
                  ) : null}
                </div>
              ) : null}

              {guide.prerequisites.length > 0 ? (
                <div className="summary-section-card">
                  <h4>
                    <BookOpen aria-hidden="true" />
                    Prerequisites
                  </h4>
                  <ul className="summary-bullet-list">
                    {guide.prerequisites.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {guide.confidence_notes ? (
                <p className="summary-hint">{guide.confidence_notes}</p>
              ) : null}
            </div>
          ) : null}
        </div>

        <footer className="study-modal-footer">
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
              <button
                className="primary-button"
                type="button"
                onClick={handleGenerate}
                disabled={!hasMaterial}
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
              <button className="primary-button" type="button" onClick={handleGenerate}>
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
        </footer>
      </div>
    </div>
  );
}
