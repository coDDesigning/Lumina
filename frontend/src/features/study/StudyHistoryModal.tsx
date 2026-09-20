import { useCallback, useEffect, useState } from 'react';
import { Archive, History } from 'lucide-react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import type {
  ExamPlanView,
  ExamReviewSheetDocument,
  ExamRoadmap,
  ExamTopicGuideDocument,
  GeneratedOutputDetail,
  GeneratedOutputSummary,
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
import { ExamReviewSheet } from '@/features/examMode/ExamReviewSheet';
import { ExamTopicGuide } from '@/features/examMode/ExamTopicGuide';
import { RankedTopicList } from '@/features/examMode/RankedTopicList';
import { ExamRoadmapView } from './ExamRoadmapView';
import { FlashcardDeck } from './FlashcardDeck';
import { StoredQuiz } from './quiz/StoredQuiz';
import { StudyGuide } from './StudyGuide';
import { StudyGuideExportActions } from './studyGuideExport';
import {
  asExportableStudyGuide,
  extractFlashcards,
  extractQuiz,
  isRenderableStudyGuide,
  studyGuideContext,
  tryParseJson,
} from './storedOutput';
import { isListedOutputType, outputTypeLabel, QUIZ_SHAPED_OUTPUT_TYPES } from './outputTypes';
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

function outputLabel(output: GeneratedOutputSummary): string {
  return outputTypeLabel(output.output_type);
}

function asRecord(content: unknown): Record<string, unknown> | null {
  const parsed = tryParseJson(content);
  return typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : null;
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

  if (output.output_type === 'exam_plan') {
    const plan = asRecord(content);
    if (plan && Array.isArray(plan.topics)) {
      return (
        <RankedTopicList
          courseId={output.course_id}
          plan={{ ...plan, generated_output_id: output.id } as unknown as ExamPlanView}
        />
      );
    }
  }

  if (output.output_type === 'exam_review_sheet') {
    const sheet = asRecord(content);
    if (sheet && Array.isArray(sheet.topics) && Array.isArray(sheet.final_checks)) {
      return <ExamReviewSheet sheet={sheet as unknown as ExamReviewSheetDocument} />;
    }
  }

  if (output.output_type === 'exam_topic_guide') {
    const guide = asRecord(content);
    if (guide) {
      return <ExamTopicGuide guide={guide as unknown as ExamTopicGuideDocument} />;
    }
  }

  if (QUIZ_SHAPED_OUTPUT_TYPES.has(output.output_type)) {
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
    return <StudyGuide guide={parsedGuide} context={studyGuideContext(output)} />;
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

  const visibleOutputs = listQuery.data?.filter((output) => isListedOutputType(output.output_type));

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

  // A study guide is the one output a reader takes away as a document, so it
  // keeps the copy and download it had before generation moved to the queue.
  const exportableGuide =
    detailState.phase === 'ready' ? asExportableStudyGuide(detailState.output) : null;

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Made for you"
      description={`Everything generated for ${courseName}`}
      mark={<History aria-hidden="true" />}
      footer={
        <>
          <Button onClick={onClose}>Done</Button>
          {exportableGuide ? (
            <div className={styles.footerRight}>
              <StudyGuideExportActions guide={exportableGuide} courseName={courseName} />
            </div>
          ) : null}
        </>
      }
    >
      {listState.phase === 'loading' ? <DetailLoading label="Loading your history" /> : null}

      {listState.phase === 'error' ? (
        <DetailError message={listState.message} onRetry={() => void listQuery.refetch()} />
      ) : null}

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
              {detailState.phase === 'error' ? (
                <DetailError
                  message={detailState.message}
                  onRetry={() => void detailQuery.refetch()}
                />
              ) : null}
              {detailState.phase === 'ready' ? <StoredOutput output={detailState.output} /> : null}
            </>
          }
        />
      ) : null}
    </Dialog>
  );
}
