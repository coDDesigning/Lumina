import { useCallback, useEffect, useRef, useState } from 'react';
import { Clock, FileText, History, X, XCircle } from 'lucide-react';
import { generatedOutputsAPI } from '../../api/generatedOutputs';
import { describeError, isAbortError } from '../../api/errors';
import type {
  GeneratedOutputDetail,
  GeneratedOutputSummary,
  RetrievedContext,
  StudyGuideResponse,
  FlashcardGenerationResponse,
} from '../../api/types';
import { StudyGuideView } from './StudyGuideView';
import { FlashcardView } from './FlashcardView';
import './study.css';

interface StudyHistoryModalProps {
  courseId: number;
  courseName: string;
  onClose: () => void;
}

type ListState =
  | { phase: 'loading' }
  | { phase: 'ready'; outputs: GeneratedOutputSummary[] }
  | { phase: 'error'; message: string };

type DetailState =
  | { phase: 'empty' }
  | { phase: 'loading' }
  | { phase: 'ready'; output: GeneratedOutputDetail }
  | { phase: 'error'; message: string };

const OUTPUT_TYPE_LABELS: Record<string, string> = {
  study_guide: 'Study guide',
  flashcards: 'Flashcards',
};

function formatCreatedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
}

function settingBadges(output: GeneratedOutputSummary): string[] {
  const settings = output.generation_settings;
  if (!settings) return [];
  return [
    settings.topic_focus,
    settings.summary_length,
    settings.detail_level,
    settings.summary_mode,
  ].filter((value): value is string => Boolean(value));
}

/**
 * Stored content is only as trustworthy as the schema in force when it was
 * written, so check the fields the view actually dereferences before handing it
 * over. A row that no longer fits must render as raw JSON, not crash the modal.
 */
function isRenderableStudyGuide(content: unknown): content is StudyGuideResponse {
  if (typeof content !== 'object' || content === null) return false;
  const candidate = content as Record<string, unknown>;
  const difficulty = candidate.difficulty as Record<string, unknown> | undefined;
  const coverage = candidate.coverage as Record<string, unknown> | undefined;
  const examTips = candidate.exam_tips as Record<string, unknown> | undefined;

  return (
    typeof candidate.title === 'string' &&
    typeof candidate.summary === 'string' &&
    typeof candidate.estimated_study_time === 'string' &&
    Array.isArray(candidate.key_points) &&
    Array.isArray(candidate.important_terms) &&
    Array.isArray(candidate.common_mistakes) &&
    Array.isArray(candidate.prerequisites) &&
    Array.isArray(candidate.learning_objectives) &&
    typeof difficulty?.level === 'string' &&
    typeof coverage?.status === 'string' &&
    Array.isArray(examTips?.lecture_based) &&
    Array.isArray(examTips?.ai_suggestions)
  );
}

function isRenderableFlashcards(content: unknown): content is FlashcardGenerationResponse {
  if (typeof content !== 'object' || content === null) return false;
  const candidate = content as Record<string, unknown>;
  
  return (
    typeof candidate.deck_title === 'string' &&
    typeof candidate.card_count === 'number' &&
    Array.isArray(candidate.flashcards) &&
    (candidate.flashcards.length === 0 || (
      typeof candidate.flashcards[0] === 'object' &&
      candidate.flashcards[0] !== null &&
      'front' in candidate.flashcards[0] &&
      'back' in candidate.flashcards[0]
    ))
  );
}

/**
 * Renders the stored content when it is a study guide we can still display, and
 * falls back to the raw document otherwise so one unreadable row never breaks
 * the view.
 */
function StoredOutput({ output }: { output: GeneratedOutputDetail }) {
  const { content } = output;

  if (output.output_type === 'flashcards' && isRenderableFlashcards(content)) {
    return <FlashcardView initialCards={content.flashcards} />;
  }

  if (output.output_type !== 'study_guide' || !isRenderableStudyGuide(content)) {
    return (
      <pre className="study-history-raw">
        {typeof content === 'string' ? content : JSON.stringify(content, null, 2)}
      </pre>
    );
  }

  const context = output.generation_context;
  const reporting: RetrievedContext | null = context
    ? {
        context_truncated: context.truncated,
        chunks_used: context.chunks_used,
        chunks_available: context.chunks_available,
        retrieval_narrowed: context.chunks_used < context.chunks_available,
        lowest_similarity: context.lowest_similarity ?? null,
        highest_similarity: context.highest_similarity ?? null,
        profile_knowledge_used: context.profile_knowledge_used ?? false,
        profile_knowledge_items_used: context.profile_knowledge_items_used ?? 0,
      }
    : null;

  return <StudyGuideView guide={content} context={reporting} />;
}

export function StudyHistoryModal({
  courseId,
  courseName,
  onClose,
}: StudyHistoryModalProps) {
  const [listState, setListState] = useState<ListState>({ phase: 'loading' });
  const [detailState, setDetailState] = useState<DetailState>({ phase: 'empty' });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const listAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listAbortRef.current = controller;

    generatedOutputsAPI
      .list(courseId, { signal: controller.signal })
      .then((outputs) => {
        if (controller.signal.aborted) return;
        setListState({ phase: 'ready', outputs });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setListState({
          phase: 'error',
          message: describeError(error, 'The history could not be loaded.').message,
        });
      });

    return () => controller.abort();
  }, [courseId]);

  useEffect(
    () => () => {
      listAbortRef.current?.abort();
      detailAbortRef.current?.abort();
    },
    [],
  );

  const handleSelect = useCallback(
    (outputId: number) => {
      detailAbortRef.current?.abort();
      const controller = new AbortController();
      detailAbortRef.current = controller;

      setSelectedId(outputId);
      setDetailState({ phase: 'loading' });

      generatedOutputsAPI
        .get(courseId, outputId, { signal: controller.signal })
        .then((output) => {
          if (controller.signal.aborted) return;
          setDetailState({ phase: 'ready', output });
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted || isAbortError(error)) return;
          setDetailState({
            phase: 'error',
            message: describeError(error, 'This result could not be loaded.').message,
          });
        });
    },
    [courseId],
  );

  return (
    <div className="study-modal-backdrop" role="dialog" aria-modal="true">
      <div className="study-modal large-modal">
        <header className="study-modal-header">
          <div>
            <h2>
              <History aria-hidden="true" />
              Generated History
            </h2>
            <p>Everything Lumina has generated for {courseName}</p>
          </div>
          <button
            className="modal-close-button"
            type="button"
            onClick={onClose}
            aria-label="Close history modal"
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="study-modal-body">
          {listState.phase === 'loading' ? (
            <div className="study-loading-state">
              <div className="study-pulse-spinner" />
              <h3>Loading history</h3>
            </div>
          ) : null}

          {listState.phase === 'error' ? (
            <div className="summary-container">
              <div className="summary-section-card summary-notice is-danger" role="alert">
                <h4>
                  <XCircle aria-hidden="true" />
                  History unavailable
                </h4>
                <p>{listState.message}</p>
              </div>
            </div>
          ) : null}

          {listState.phase === 'ready' && listState.outputs.length === 0 ? (
            <div className="summary-container">
              <div className="summary-section-card">
                <h4>
                  <FileText aria-hidden="true" />
                  Nothing generated yet
                </h4>
                <p className="summary-hint">
                  Generated study guides are saved here so you can reopen them later.
                </p>
              </div>
            </div>
          ) : null}

          {listState.phase === 'ready' && listState.outputs.length > 0 ? (
            <div className="study-history-layout">
              <ul className="study-history-list">
                {listState.outputs.map((output) => (
                  <li key={output.id}>
                    <button
                      type="button"
                      className={
                        output.id === selectedId
                          ? 'study-history-entry is-selected'
                          : 'study-history-entry'
                      }
                      onClick={() => handleSelect(output.id)}
                      aria-current={output.id === selectedId}
                    >
                      <span className="study-history-entry-title">
                        {OUTPUT_TYPE_LABELS[output.output_type] ?? output.output_type}
                      </span>
                      <span className="study-history-entry-meta">
                        <Clock aria-hidden="true" />
                        {formatCreatedAt(output.created_at)}
                      </span>
                      {output.model_used ? (
                        <span className="study-history-entry-meta">
                          {output.model_used}
                        </span>
                      ) : null}
                      {output.generation_settings ? (
                        <span className="study-history-badges">
                          {settingBadges(output).map((badge) => (
                            <span className="study-history-badge" key={badge}>
                              {badge}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <span className="study-history-entry-meta">
                          Settings not recorded
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>

              <div className="study-history-detail">
                {detailState.phase === 'empty' ? (
                  <p className="summary-hint">
                    Select a result to read it again. Nothing is regenerated.
                  </p>
                ) : null}

                {detailState.phase === 'loading' ? (
                  <div className="study-loading-state">
                    <div className="study-pulse-spinner" />
                    <h3>Loading result</h3>
                  </div>
                ) : null}

                {detailState.phase === 'error' ? (
                  <div
                    className="summary-section-card summary-notice is-danger"
                    role="alert"
                  >
                    <h4>
                      <XCircle aria-hidden="true" />
                      Could not open this result
                    </h4>
                    <p>{detailState.message}</p>
                  </div>
                ) : null}

                {detailState.phase === 'ready' ? (
                  <StoredOutput output={detailState.output} />
                ) : null}
              </div>
            </div>
          ) : null}
        </div>

        <footer className="study-modal-footer">
          <button className="secondary-button" type="button" onClick={onClose}>
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
