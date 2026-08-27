import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { BookOpen, Check, Copy, Download } from 'lucide-react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import type { GeneratedOutputDetail, RetrievedContext, StudyGuideResponse } from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import type { Workspace } from '@/data/workspaces';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { ErrorState } from '@/ui/ErrorState';
import { PageHeader } from '@/ui/PageHeader';
import { Skeleton } from '@/ui/Skeleton';
import { StudyGuide } from './StudyGuide';
import { isRenderableStudyGuide, tryParseJson } from './storedOutput';
import { studyGuideFileName, studyGuideToMarkdown } from './studyGuideMarkdown';
import styles from './GuidePage.module.css';

export interface GuidePageProps {
  workspace: Workspace;
}

type State =
  | { phase: 'loading' }
  | { phase: 'ready'; output: GeneratedOutputDetail; guide: StudyGuideResponse }
  | { phase: 'unreadable'; output: GeneratedOutputDetail }
  | { phase: 'error'; message: string };

function reportingContext(output: GeneratedOutputDetail): RetrievedContext | null {
  const context = output.generation_context;
  if (!context) {
    return null;
  }
  return {
    context_truncated: context.truncated,
    chunks_used: context.chunks_used,
    chunks_available: context.chunks_available,
    retrieval_narrowed: context.chunks_used < context.chunks_available,
    lowest_similarity: context.lowest_similarity ?? null,
    highest_similarity: context.highest_similarity ?? null,
    profile_knowledge_used: context.profile_knowledge_used ?? false,
    profile_knowledge_items_used: context.profile_knowledge_items_used ?? null,
  };
}

export default function GuidePage({ workspace }: GuidePageProps) {
  const { outputId } = useParams();
  const navigate = useNavigate();
  const courseId = Number(workspace.id);
  const [state, setState] = useState<State>({ phase: 'loading' });
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');

  useDocumentTitle(
    state.phase === 'ready' ? `${state.guide.title} · ${workspace.name}` : `Study guide · ${workspace.name}`,
  );

  const id = Number(outputId);
  const isValidAddress = Number.isInteger(id) && id > 0;

  const query = useQuery<GeneratedOutputDetail>({
    key: isValidAddress ? queryKeys.courseOutput(courseId, id) : null,
    fetcher: ({ signal }) => generatedOutputsAPI.get(courseId, id, { signal }),
    fallbackMessage: 'This study guide could not be opened.',
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (!isValidAddress) {
      setState({ phase: 'error', message: 'That is not a study guide address.' });
      return;
    }
    if (query.status === 'pending' || query.status === 'idle') {
      setState({ phase: 'loading' });
      return;
    }
    if (query.status === 'error') {
      setState({
        phase: 'error',
        message: query.error?.message ?? 'This study guide could not be opened.',
      });
      return;
    }
    const output = query.data;
    if (!output) {
      return;
    }
    if (
      (output.output_type === 'study_guide' || output.output_type === 'last_minute_review') &&
      isRenderableStudyGuide(output.content)
    ) {
      const guide =
        typeof output.content === 'string'
          ? (tryParseJson(output.content) as StudyGuideResponse)
          : output.content;
      setState({ phase: 'ready', output, guide });
      return;
    }
    setState({ phase: 'unreadable', output });
  }, [isValidAddress, query.status, query.data, query.error]);

  useEffect(() => {
    if (copyState === 'idle') {
      return;
    }
    const timer = setTimeout(() => setCopyState('idle'), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);

  const guide = state.phase === 'ready' ? state.guide : null;
  const context = state.phase === 'ready' ? reportingContext(state.output) : null;

  const markdown = () =>
    studyGuideToMarkdown(
      { study_guide: guide!, ...(context ?? {}) } as Parameters<typeof studyGuideToMarkdown>[0],
      workspace.name,
    );

  const handleCopy = async () => {
    if (!guide) {
      return;
    }
    try {
      await navigator.clipboard.writeText(markdown());
      setCopyState('copied');
    } catch {
      setCopyState('failed');
    }
  };

  const handleDownload = () => {
    if (!guide) {
      return;
    }
    const blob = new Blob([markdown()], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = studyGuideFileName(workspace.name);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[
          { label: 'Courses', to: '/dashboard' },
          { label: workspace.name, to: `/courses/${workspace.id}` },
          { label: 'Study guide' },
        ]}
        actions={
          guide ? (
            <>
              <Button
                onClick={() => void handleCopy()}
                icon={copyState === 'copied' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
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
            </>
          ) : null
        }
      />

      <div className={styles.body}>
        {state.phase === 'loading' ? (
          <div className={styles.pending}>
            <Skeleton variant="heading" />
            <Skeleton variant="block" />
            <Skeleton variant="block" />
          </div>
        ) : null}

        {state.phase === 'error' ? (
          <ErrorState
            title="This guide is not here"
            actions={
              <Button size="sm" onClick={() => navigate(`/courses/${workspace.id}`)}>
                Back to the course
              </Button>
            }
          >
            {state.message}
          </ErrorState>
        ) : null}

        {state.phase === 'unreadable' ? (
          state.output.output_type === 'flashcards' ? (
            <Alert
              tone="info"
              title="This is a flashcard deck, not a study guide"
              actions={
                <Button size="sm" onClick={() => navigate(`/courses/${workspace.id}`)}>
                  Open it on the course page
                </Button>
              }
            >
              Decks are flipped through in a window on the course page rather than read as a
              document.
            </Alert>
          ) : (
            <Alert
              tone="warning"
              title="This result was saved in an older shape"
              actions={
                <Button size="sm" onClick={() => navigate(`/courses/${workspace.id}`)}>
                  Back to the course
                </Button>
              }
            >
              It is kept exactly as it was written, but this version cannot lay it out. You can
              still read it from the course history.
            </Alert>
          )
        ) : null}

        {state.phase === 'ready' ? (
          <>
            <StudyGuide guide={state.guide} context={context} />
            <p className={styles.provenanceLine}>
              <BookOpen className={styles.provenanceIcon} aria-hidden="true" />
              Made on {new Date(state.output.created_at).toLocaleDateString()}
              {state.output.model_used ? ` by ${state.output.model_used}` : ''}. Opening it again
              costs nothing.
            </p>
          </>
        ) : null}
      </div>
    </div>
  );
}
