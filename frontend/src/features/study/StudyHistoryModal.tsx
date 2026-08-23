import { useCallback, useEffect, useRef, useState } from 'react';
import { Archive, History } from 'lucide-react';
import { describeError, isAbortError } from '@/api/errors';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import type {
  GeneratedOutputDetail,
  GeneratedOutputSummary,
  RetrievedContext,
} from '@/api/types';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { EmptyState } from '@/ui/EmptyState';
import {
  DetailError,
  DetailLoading,
  DetailPlaceholder,
  MasterDetail,
} from '@/ui/MasterDetail';
import { FlashcardDeck } from './FlashcardDeck';
import { StudyGuide } from './StudyGuide';
import { isRenderableFlashcards, isRenderableStudyGuide } from './storedOutput';
import styles from './StudyHistoryModal.module.css';

export interface StudyHistoryModalProps {
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

function outputLabel(output: GeneratedOutputSummary): string {
  return OUTPUT_TYPE_LABELS[output.output_type] ?? output.output_type;
}

function formatCreatedAt(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

function settingBadges(output: GeneratedOutputSummary): string[] {
  const settings = output.generation_settings;
  if (!settings) {
    return [];
  }
  return [
    settings.topic_focus,
    settings.summary_length,
    settings.detail_level,
    settings.summary_mode,
  ].filter((value): value is string => Boolean(value));
}

function StoredOutput({ output }: { output: GeneratedOutputDetail }) {
  const { content } = output;

  if (output.output_type === 'flashcards' && isRenderableFlashcards(content)) {
    return <FlashcardDeck cards={content.flashcards} />;
  }

  if (output.output_type !== 'study_guide' || !isRenderableStudyGuide(content)) {
    return (
      <div className={styles.rawWrap}>
        <p className={styles.rawNote}>
          This result was saved in a shape this version no longer recognises, so it is shown as
          it was stored.
        </p>
        <pre className={styles.raw}>
          {typeof content === 'string' ? content : JSON.stringify(content, null, 2)}
        </pre>
      </div>
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

  return <StudyGuide guide={content} context={reporting} />;
}

export function StudyHistoryModal({ courseId, courseName, onClose }: StudyHistoryModalProps) {
  const [listState, setListState] = useState<ListState>({ phase: 'loading' });
  const [detailState, setDetailState] = useState<DetailState>({ phase: 'empty' });
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const detailAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    generatedOutputsAPI
      .list(courseId, { signal: controller.signal })
      .then((outputs) => {
        if (controller.signal.aborted) {
          return;
        }
        setListState({ phase: 'ready', outputs });
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setListState({
          phase: 'error',
          message: describeError(caught, 'The history could not be loaded.').message,
        });
      });

    return () => {
      controller.abort();
      detailAbortRef.current?.abort();
    };
  }, [courseId]);

  const handleSelect = useCallback(
    (output: GeneratedOutputSummary) => {
      detailAbortRef.current?.abort();
      const controller = new AbortController();
      detailAbortRef.current = controller;

      setSelectedId(output.id);
      setDetailState({ phase: 'loading' });

      generatedOutputsAPI
        .get(courseId, output.id, { signal: controller.signal })
        .then((detail) => {
          if (controller.signal.aborted) {
            return;
          }
          setDetailState({ phase: 'ready', output: detail });
        })
        .catch((caught: unknown) => {
          if (controller.signal.aborted || isAbortError(caught)) {
            return;
          }
          setDetailState({
            phase: 'error',
            message: describeError(caught, 'This result could not be opened.').message,
          });
        });
    },
    [courseId],
  );

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Made for you"
      description={`Everything generated for ${courseName}`}
      mark={<History aria-hidden="true" />}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      {listState.phase === 'loading' ? <DetailLoading label="Loading your history" /> : null}

      {listState.phase === 'error' ? <DetailError message={listState.message} /> : null}

      {listState.phase === 'ready' ? (
        <MasterDetail
          listLabel="Saved results"
          items={listState.outputs}
          keyOf={(output) => output.id}
          labelOf={(output) => `${outputLabel(output)} from ${formatCreatedAt(output.created_at)}`}
          selectedKey={selectedId}
          onSelect={handleSelect}
          emptyList={
            <EmptyState
              icon={<Archive aria-hidden="true" />}
              title="Nothing saved yet"
              description="Study guides and flashcard decks are kept here so you can read them again without spending anything."
              headingLevel="h3"
            />
          }
          renderItem={(output) => (
            <>
              <span className={styles.entryTitle}>{outputLabel(output)}</span>
              <span className={styles.entryMeta}>{formatCreatedAt(output.created_at)}</span>
              {output.model_used ? (
                <span className={styles.entryMeta}>{output.model_used}</span>
              ) : null}
              {output.generation_settings ? (
                <span className={styles.entryBadges}>
                  {settingBadges(output).map((badge) => (
                    <Badge key={badge}>{badge}</Badge>
                  ))}
                </span>
              ) : (
                <span className={styles.entryMeta}>Settings not recorded</span>
              )}
            </>
          )}
          detail={
            <>
              {detailState.phase === 'empty' ? (
                <DetailPlaceholder
                  title="Pick something to read"
                  body="Opening a saved result costs nothing and generates nothing new."
                />
              ) : null}
              {detailState.phase === 'loading' ? <DetailLoading label="Opening" /> : null}
              {detailState.phase === 'error' ? <DetailError message={detailState.message} /> : null}
              {detailState.phase === 'ready' ? <StoredOutput output={detailState.output} /> : null}
            </>
          }
        />
      ) : null}
    </Dialog>
  );
}
