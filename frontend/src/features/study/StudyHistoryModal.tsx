import { useCallback, useEffect, useState } from 'react';
import { Archive, History } from 'lucide-react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import type {
  ExamRoadmap,
  GeneratedOutputDetail,
  GeneratedOutputSummary,
  RetrievedContext,
  StudyGuideResponse,
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
import { ExamRoadmapView } from './ExamRoadmapView';
import { FlashcardDeck } from './FlashcardDeck';
import { StoredQuiz } from './quiz/StoredQuiz';
import { StudyGuide } from './StudyGuide';
import {
  extractFlashcards,
  extractQuiz,
  isRenderableStudyGuide,
  tryParseJson,
} from './storedOutput';
import styles from './StudyHistoryModal.module.css';

export interface StudyHistoryModalProps {
  courseId: number;
  courseName: string;
  initialSelectedId?: number | null;
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
  last_minute_review: 'Last-minute review',
  flashcards: 'Flashcards',
  flashcard: 'Flashcards',
  quiz: 'Practice quiz',
  exam_roadmap: 'Exam roadmap',
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
    settings.difficulty,
    settings.question_count ? `${settings.question_count} questions` : undefined,
  ].filter((value): value is string => Boolean(value));
}

function StoredOutput({ output }: { output: GeneratedOutputDetail }) {
  const { content } = output;

  if (output.output_type === 'exam_roadmap') {
    const roadmap =
      typeof content === 'string'
        ? (tryParseJson(content) as unknown as ExamRoadmap | null)
        : (content as unknown as ExamRoadmap | null);
    if (roadmap && typeof roadmap === 'object' && Array.isArray(roadmap.days)) {
      return <ExamRoadmapView roadmap={roadmap} />;
    }
  }

  if (output.output_type === 'flashcards' || output.output_type === 'flashcard') {
    const cards = extractFlashcards(content);
    if (cards) {
      return <FlashcardDeck cards={cards} />;
    }
  }

  if (output.output_type === 'quiz') {
    const quiz = extractQuiz(content);
    if (quiz) {
      return <StoredQuiz quiz={quiz} courseId={output.course_id} />;
    }
  }

  if (
    (output.output_type === 'study_guide' || output.output_type === 'last_minute_review') &&
    isRenderableStudyGuide(content)
  ) {
    const parsedGuide =
      typeof content === 'string'
        ? (tryParseJson(content) as StudyGuideResponse)
        : (content as StudyGuideResponse);
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
          profile_knowledge_items_used: context.profile_knowledge_items_used ?? null,
        }
      : null;

    return <StudyGuide guide={parsedGuide} context={reporting} />;
  }

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

export function StudyHistoryModal({ courseId, courseName, initialSelectedId, onClose }: StudyHistoryModalProps) {
  const [selectedId, setSelectedId] = useState<number | null>(initialSelectedId ?? null);

  const listQuery = useQuery<GeneratedOutputSummary[]>({
    key: queryKeys.courseOutputs(courseId),
    fetcher: ({ signal }) => generatedOutputsAPI.list(courseId, { signal }),
    fallbackMessage: 'The history could not be loaded.',
  });

  const detailQuery = useQuery<GeneratedOutputDetail>({
    key: selectedId === null ? null : queryKeys.courseOutput(courseId, selectedId),
    fetcher: ({ signal }) => generatedOutputsAPI.get(courseId, selectedId as number, { signal }),
    fallbackMessage: 'This result could not be opened.',
    staleTime: 5 * 60_000,
  });

  const handleSelect = useCallback((output: GeneratedOutputSummary) => {
    setSelectedId(output.id);
  }, []);

  useEffect(() => {
    if (!initialSelectedId) {
      return;
    }
    setSelectedId(initialSelectedId);
  }, [initialSelectedId]);

  // Reverse-quiz sessions have their own history and no viewer here, so they
  // are kept out of this list the same way they are kept out of the rail.
  const visibleOutputs = listQuery.data?.filter(
    (output) => output.output_type !== 'reverse_quiz',
  );

  const listState: ListState =
    listQuery.status === 'error'
      ? { phase: 'error', message: listQuery.error?.message ?? 'The history could not be loaded.' }
      : visibleOutputs
        ? { phase: 'ready', outputs: visibleOutputs }
        : { phase: 'loading' };

  const detailState: DetailState =
    selectedId === null
      ? { phase: 'empty' }
      : detailQuery.status === 'error'
        ? { phase: 'error', message: detailQuery.error?.message ?? 'This result could not be opened.' }
        : detailQuery.data
          ? { phase: 'ready', output: detailQuery.data }
          : { phase: 'loading' };

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
