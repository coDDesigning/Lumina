import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  BarChart3,
  Calendar,
  FileText,
  HelpCircle,
  Layers3,
  MessageSquarePlus,
  Settings2,
  Sparkles,
  Target,
  Upload,
  Wand2,
} from 'lucide-react';
import { coursesAPI } from '@/api/courses';
import { afterConversationTurn, afterDocumentChanged } from '@/api/invalidations';
import { aiTutorAPI } from '@/api/aiTutor';
import { conversationsAPI } from '@/api/conversations';
import { courseQaAPI } from '@/api/courseQa';
import {
  describeGenerationError,
  describeUploadError,
  isAbortError,
  isInsufficientCredits,
} from '@/api/errors';
import type {
  ConversationDetail,
  ConversationRole,
  ConversationType,
  CreditSource,
  Citation,
  RetrievedContext,
} from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { DocumentRow } from '@/components/documents/DocumentRow';
import { ConversationHistoryModal } from './ConversationHistoryModal';
import { ExamRoadmapModal } from '@/features/study/ExamRoadmapModal';
import { FlashcardModal } from '@/features/study/FlashcardModal';
import { provenanceParts } from '@/features/study/provenanceParts';
import { QuizModal } from '@/features/study/quiz/QuizModal';
import { StudyHistoryModal } from '@/features/study/StudyHistoryModal';
import { SavedDeckModal } from '@/features/study/SavedDeckModal';
import { StudyGuideModal } from '@/features/study/StudyGuideModal';
import { useCredits } from '@/context/CreditContext';
import { useAuth } from '@/context/AuthContext';
import type {
  Workspace,
  WorkspaceProgress,
  WorkspaceProgressStatus,
} from '@/data/workspaces';
import { useCourseDocuments } from '@/hooks/useCourseDocuments';
import { cx } from '@/lib/cx';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { CopyButton } from '@/ui/CopyButton';
import { ErrorState } from '@/ui/ErrorState';
import { IconButton } from '@/ui/IconButton';
import { Input } from '@/ui/Input';
import { LinkButton } from '@/ui/LinkButton';
import { PageHeader } from '@/ui/PageHeader';
import { Spinner } from '@/ui/Spinner';
import { Tabs } from '@/ui/Tabs';
import { PromptGeneratorDialog } from './PromptGeneratorDialog';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Markdown } from '@/lib/markdown';
import type { DocumentMaterialKind } from '@/api/types';
import { MATERIAL_KIND_CHOICES } from '@/components/documents/documentLabels';
import { ArtifactRail } from './ArtifactRail';
import { GenerationRail } from './GenerationRail';
import { useCourseArtifacts } from './useCourseArtifacts';
import { useGenerationJobs } from './useGenerationJobs';
import { useCourseProgress } from './useCourseProgress';
import styles from './WorkspacePage.module.css';

export interface WorkspacePageProps {
  workspace: Workspace;
  onUpdateProgress?: (courseId: string, progress: Partial<WorkspaceProgress>) => void;
}

interface ThreadMessage {
  role: ConversationRole;
  content: string;
  context?: RetrievedContext;
  citations?: Citation[];
}

interface Thread {
  conversationId: number | null;
  messages: ThreadMessage[];
  isLoading: boolean;
  error: string | null;
}

const EMPTY_THREAD: Thread = {
  conversationId: null,
  messages: [],
  isLoading: false,
  error: null,
};

const THREAD_TABS = [
  { value: 'course_qa' as const, label: 'Ask' },
  { value: 'ai_tutor' as const, label: 'Tutor' },
];

const THREAD_COPY: Record<ConversationType, { title: string; body: string }> = {
  course_qa: {
    title: 'Ask anything about this course.',
    body: 'Answers come from the material you uploaded, and each one names the passages it used.',
  },
  ai_tutor: {
    title: 'Work through it with a tutor.',
    body: 'The tutor explains step by step and asks you questions back, rather than handing over the answer.',
  },
};

function uploadedOn(value: string): string | null {
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) {
    return null;
  }
  return `on ${new Intl.DateTimeFormat('en', { day: 'numeric', month: 'long' }).format(parsed)}`;
}

function getStoredConversationId(courseId: number, type: ConversationType): number | null {
  try {
    const raw = localStorage.getItem(`lumina:course:${courseId}:conversation:${type}`);
    if (!raw) {
      return null;
    }
    const parsed = Number(raw);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
}

function setStoredConversationId(
  courseId: number,
  type: ConversationType,
  id: number | null,
): void {
  try {
    const key = `lumina:course:${courseId}:conversation:${type}`;
    if (id !== null) {
      localStorage.setItem(key, String(id));
    } else {
      localStorage.removeItem(key);
    }
  } catch {
    // Ignore storage errors in restricted contexts
  }
}

export default function WorkspacePage({ workspace, onUpdateProgress }: WorkspacePageProps) {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const {
    refresh: refreshCredits,
    canAfford: canAffordCredits,
    isMetered: creditsMetered,
  } = useCredits();

  const courseId = Number(workspace.id);
  const artifactParam = searchParams.get('artifact');
  const parsedArtifactId = artifactParam === null ? null : Number(artifactParam);
  const requestedArtifactId =
    parsedArtifactId !== null && Number.isInteger(parsedArtifactId) && parsedArtifactId > 0
      ? parsedArtifactId
      : null;
  useDocumentTitle(workspace.name);

  const [threadType, setThreadType] = useState<ConversationType>('course_qa');
  const [prompt, setPrompt] = useState('');
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [threads, setThreads] = useState<Record<ConversationType, Thread>>({
    course_qa: EMPTY_THREAD,
    ai_tutor: EMPTY_THREAD,
  });

  useEffect(() => {
    if (!Number.isInteger(courseId) || courseId <= 0) {
      return;
    }

    const controller = new AbortController();

    (['course_qa', 'ai_tutor'] as const).forEach((type) => {
      const storedId = getStoredConversationId(courseId, type);
      if (!storedId) {
        return;
      }

      setThreads((state) => ({
        ...state,
        [type]: { ...state[type], isLoading: true, error: null },
      }));

      conversationsAPI
        .get(courseId, storedId, { signal: controller.signal })
        .then((detail) => {
          if (controller.signal.aborted) {
            return;
          }
          setThreads((state) => ({
            ...state,
            [type]: {
              conversationId: detail.id,
              messages: detail.messages.map(({ role, content }) => ({ role, content })),
              isLoading: false,
              error: null,
            },
          }));
        })
        .catch((caught: unknown) => {
          if (controller.signal.aborted || isAbortError(caught)) {
            return;
          }
          setStoredConversationId(courseId, type, null);
          setThreads((state) => ({
            ...state,
            [type]: {
              ...state[type],
              conversationId: null,
              isLoading: false,
            },
          }));
        });
    });

    return () => {
      controller.abort();
    };
  }, [courseId]);

  const [uploadErrors, setUploadErrors] = useState<{ fileName: string; message: string }[]>([]);
  const [uploadNotices, setUploadNotices] = useState<string[]>([]);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(
    null,
  );

  const [isSummaryOpen, setIsSummaryOpen] = useState(false);
  const [isRoadmapOpen, setIsRoadmapOpen] = useState(false);
  const [isQuizOpen, setIsQuizOpen] = useState(false);
  const [isFlashcardOpen, setIsFlashcardOpen] = useState(false);
  const [isMadeForYouOpen, setIsMadeForYouOpen] = useState(requestedArtifactId !== null);
  const [madeForYouInitialId, setMadeForYouInitialId] = useState<number | null>(
    requestedArtifactId,
  );
  const [openDeckId, setOpenDeckId] = useState<number | null>(null);
  const [materialKind, setMaterialKind] = useState<DocumentMaterialKind>('unspecified');
  const [isPastThreadsOpen, setIsPastThreadsOpen] = useState(false);
  const [isPromptHelperOpen, setIsPromptHelperOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);

  const thread = threads[threadType];

  useEffect(() => {
    if (requestedArtifactId === null) {
      return;
    }
    setMadeForYouInitialId(requestedArtifactId);
    setIsMadeForYouOpen(true);
  }, [requestedArtifactId]);

  useEffect(() => {
    const node = threadRef.current;
    if (!node) {
      return;
    }
    node.scrollTop = node.scrollHeight;
  }, [threads, threadType]);
  const creditSource: CreditSource = threadType;
  const threadExhausted = creditsMetered && !canAffordCredits(creditSource);

  const {
    entries,
    isLoading: areDocumentsLoading,
    listError,
    readyCount,
    reload,
    addUploaded,
    retryDocument,
    deleteDocument,
  } = useCourseDocuments(courseId);

  const navigate = useNavigate();
  const { progress, reload: reloadProgress } = useCourseProgress(courseId);
  const {
    artifacts,
    isLoading: areArtifactsLoading,
    error: artifactsError,
    reload: reloadArtifacts,
  } = useCourseArtifacts(courseId, progress);
  const generationJobs = useGenerationJobs(courseId);

  useEffect(() => {
    if (!onUpdateProgress || !progress) {
      return;
    }
    onUpdateProgress(workspace.id, {
      averageScore:
        progress.average_score === null ? null : Math.round(progress.average_score * 100),
      timeSpentSeconds: progress.total_time_spent_seconds ?? null,
      status: progress.status as WorkspaceProgressStatus,
    });
  }, [onUpdateProgress, progress, workspace.id]);

  const processingCount = entries.filter(
    (entry) => entry.document.status === 'uploaded' || entry.document.status === 'processing',
  ).length;

  async function addSources(fileList: FileList | null) {
    const files = Array.from(fileList ?? []);
    if (files.length === 0) {
      return;
    }

    setUploadErrors([]);
    setUploadNotices([]);
    setUploadProgress({ done: 0, total: files.length });

    const errors: { fileName: string; message: string }[] = [];
    const notices: string[] = [];

    for (const file of files) {
      try {
        const response = await coursesAPI.uploadDocument(courseId, file, materialKind);
        addUploaded(response.document);
        afterDocumentChanged(courseId);
        if (response.duplicate) {
          const when = uploadedOn(response.document.created_at);
          notices.push(
            when
              ? `${file.name} is already in this course — you added it ${when}. The original was kept.`
              : `${file.name} is already in this course. The original was kept.`,
          );
        }
      } catch (caught) {
        errors.push({ fileName: file.name, message: describeUploadError(caught).message });
      } finally {
        setUploadProgress((current) => (current ? { ...current, done: current.done + 1 } : current));
      }
    }

    setUploadErrors(errors);
    setUploadNotices(notices);
    setUploadProgress(null);
  }

  async function submitPrompt(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = prompt.trim();
    if (!question) {
      return;
    }

    const current = threads[threadType];
    if (current.isLoading) {
      return;
    }
    if (threadExhausted) {
      return;
    }

    setPrompt('');
    setThreads((state) => ({
      ...state,
      [threadType]: { ...state[threadType], isLoading: true, error: null },
    }));

    const request = {
      question,
      use_profile_knowledge: includeProfileContext,
      include_profile_context: includeProfileContext,
      ...(current.conversationId ? { conversation_id: current.conversationId } : {}),
    };

    try {
      const result =
        threadType === 'ai_tutor'
          ? await aiTutorAPI.ask(courseId, request)
          : await courseQaAPI.ask(courseId, request);

      setStoredConversationId(courseId, threadType, result.conversation_id);
      setThreads((state) => ({
        ...state,
        [threadType]: {
          ...state[threadType],
          conversationId: result.conversation_id,
          messages: [
            ...state[threadType].messages,
            { role: 'user', content: question },
            {
              role: 'assistant',
              content: result.answer,
              citations: result.citations,
              context: {
                context_truncated: result.context_truncated,
                chunks_used: result.chunks_used,
                chunks_available: result.chunks_available,
                retrieval_narrowed: result.retrieval_narrowed,
                lowest_similarity: result.lowest_similarity,
                highest_similarity: result.highest_similarity,
                profile_knowledge_used: result.profile_knowledge_used,
                profile_knowledge_items_used: result.profile_knowledge_items_used,
              },
            },
          ],
        },
      }));
      afterConversationTurn(courseId);
      void refreshCredits();
    } catch (caught) {
      setPrompt(question);
      const described = describeGenerationError(
        caught,
        threadType === 'ai_tutor'
          ? 'Failed to generate tutor explanation from course materials.'
          : 'Failed to generate answer from course materials.',
      );
      if (isInsufficientCredits(described)) {
        await refreshCredits();
      } else {
        setThreads((state) => ({
          ...state,
          [threadType]: { ...state[threadType], error: described.message },
        }));
      }
    } finally {
      setThreads((state) => ({
        ...state,
        [threadType]: { ...state[threadType], isLoading: false },
      }));
    }
  }

  function startNewConversation() {
    setStoredConversationId(courseId, threadType, null);
    setThreads((state) => ({ ...state, [threadType]: EMPTY_THREAD }));
  }

  function resumeConversation(conversation: ConversationDetail) {
    setStoredConversationId(courseId, conversation.conversation_type, conversation.id);
    setThreads((state) => ({
      ...state,
      [conversation.conversation_type]: {
        conversationId: conversation.id,
        // The backend never stored per-message retrieval context, so a resumed
        // turn has no provenance line: absent is honest, zero would not be.
        // The backend never stored per-message retrieval context, so a resumed
        // turn has no provenance line: absent is honest, zero would not be.
        messages: conversation.messages.map(({ role, content, citations }) => ({
          role,
          content,
          citations,
        })),
        isLoading: false,
        error: null,
      },
    }));
    setThreadType(conversation.conversation_type);
    setIsPastThreadsOpen(false);
    setPrompt('');
  }

  const canGenerate = readyCount > 0;
  const isSupportView = Boolean(user && workspace.ownerId != null && workspace.ownerId !== user.id);
  const ownerDisplayName =
    workspace.ownerName ||
    workspace.ownerEmail ||
    (workspace.ownerId ? `User #${workspace.ownerId}` : 'another user');

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[{ label: 'Courses', to: '/dashboard' }, { label: workspace.name }]}
        badges={
          <>
            {isSupportView ? <Badge tone="accent">Read-Only Support</Badge> : null}
            {workspace.semester ? <Badge>{workspace.semester}</Badge> : null}
            {processingCount > 0 ? (
              <Badge tone="processing">
                {processingCount} still being read
              </Badge>
            ) : null}
          </>
        }
        actions={
          <>
            <LinkButton
              variant="ghost"
              size="sm"
              icon={<BarChart3 aria-hidden="true" />}
              to={`/courses/${workspace.id}/progress`}
            >
              Progress
            </LinkButton>
            {!isSupportView ? (
              <LinkButton
                variant="ghost"
                size="sm"
                icon={<Settings2 aria-hidden="true" />}
                to={`/courses/${workspace.id}/settings`}
              >
                Course settings
              </LinkButton>
            ) : null}
            {!isSupportView ? <CreditBalance source={creditSource} /> : null}
          </>
        }
      />

      {isSupportView ? (
        <div className={styles.supportBanner}>
          <Alert tone="info">
            <strong>Read-Only Support View</strong> — Viewing course owned by{' '}
            <strong>{ownerDisplayName}</strong>. Adding sources, editing, and AI generation are disabled.
          </Alert>
        </div>
      ) : null}

      <h1 className="visually-hidden">{workspace.name} workspace</h1>

      <div className={styles.columns}>
        <section className={`${styles.panel} ${styles.sources}`} aria-label="Sources">
          <div className={styles.panelHead}>
            <span className={styles.panelLabel}>Sources · {entries.length}</span>
            {!isSupportView ? (
              <>
                <Button
                  size="sm"
                  icon={<Upload aria-hidden="true" />}
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadProgress !== null}
                  isLoading={uploadProgress !== null}
                  loadingLabel="Uploading"
                >
                  Add Sources
                </Button>
                <label className={styles.kindPicker}>
                  <span className={styles.kindLabel}>Adding as</span>
                  <select
                    className={styles.kindSelect}
                    value={materialKind}
                    onChange={(event) =>
                      setMaterialKind(event.target.value as DocumentMaterialKind)
                    }
                  >
                    {MATERIAL_KIND_CHOICES.map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                  </select>
                </label>
                <input
                  ref={fileInputRef}
                  className={styles.uploadInput}
                  type="file"
                  multiple
                  accept=".pdf,.txt,.md,.markdown,.png,.jpg,.jpeg"
                  onChange={(event) => {
                    void addSources(event.target.files);
                    event.target.value = '';
                  }}
                />
              </>
            ) : null}
          </div>

          {uploadProgress ? (
            <p className={cx(styles.sourceHint, styles.sourceHintFlush)} role="status">
              Uploading {uploadProgress.done} of {uploadProgress.total}…
            </p>
          ) : null}

          {uploadErrors.length > 0 || uploadNotices.length > 0 || listError ? (
            <div className={styles.noticeStack}>
              {listError ? <ErrorState onRetry={reload}>{listError}</ErrorState> : null}
              {uploadErrors.map((failure) => (
                <Alert key={failure.fileName} tone="destructive" live="alert" title={failure.fileName}>
                  {failure.message}
                </Alert>
              ))}
              {uploadNotices.map((notice) => (
                <Alert key={notice} tone="info" live="status">
                  {notice}
                </Alert>
              ))}
            </div>
          ) : null}

          <div className={styles.scrollArea}>
            {areDocumentsLoading && entries.length === 0 ? (
              <p className={styles.sourceHint} role="status">
                Loading sources…
              </p>
            ) : entries.length === 0 ? (
              <p className={styles.sourceHint}>
                Nothing here yet. Add a lecture, your notes, or a past paper — Lumina reads what
                you upload and nothing else.
              </p>
            ) : (
              entries.map((entry) => (
                <DocumentRow
                  key={entry.document.id}
                  entry={entry}
                  onRetry={retryDocument}
                  onDelete={deleteDocument}
                  readOnly={isSupportView}
                />
              ))
            )}
          </div>

          <p className={styles.sourceHint}>PDF, TXT, Markdown, and images (PNG, JPEG)</p>
        </section>

        <section className={`${styles.panel} ${styles.conversation}`} aria-label="Conversation">
          <div className={styles.panelHead}>
            <Tabs
              label="Conversation type"
              options={THREAD_TABS}
              value={threadType}
              onChange={setThreadType}
              link={{ to: `/courses/${courseId}/exam-mode`, label: 'Exam Mode' }}
            />
            <span className={styles.threadActions}>
              {!isSupportView ? (
                <Button
                  variant="ghost"
                  size="sm"
                  icon={<MessageSquarePlus aria-hidden="true" />}
                  onClick={startNewConversation}
                >
                  New conversation
                </Button>
              ) : null}
              <Button variant="ghost" size="sm" onClick={() => setIsPastThreadsOpen(true)}>
                Past threads
              </Button>
            </span>
          </div>

          <div className={styles.thread} ref={threadRef} aria-live="polite">
            {thread.messages.length === 0 && !thread.isLoading ? (
              <div className={styles.emptyThread}>
                <h2 className={styles.emptyThreadTitle}>{THREAD_COPY[threadType].title}</h2>
                <p className={styles.emptyThreadBody}>{THREAD_COPY[threadType].body}</p>
                {!canGenerate ? (
                  <p className={styles.emptyThreadBody}>
                    {processingCount > 0
                      ? 'Your sources are still being read. This usually takes under a minute.'
                      : 'Add a source first — there is nothing to answer from yet.'}
                  </p>
                ) : null}
              </div>
            ) : null}

            {thread.messages.map((message, index) =>
              message.role === 'user' ? (
                <p key={index} className={styles.turnUser}>
                  {message.content}
                </p>
              ) : (
                <div key={index} className={styles.turnAssistant}>
                  <Markdown
                    className={styles.answer}
                    text={message.content}
                    citations={message.citations}
                  />
                  {message.context ? (
                    <span className={styles.provenance}>
                      <FileText className={styles.provenanceIcon} aria-hidden="true" />
                      <span>{provenanceParts(message.context).join(' · ')}</span>
                    </span>
                  ) : null}
                  <div className={styles.turnActions}>
                    <CopyButton text={message.content} />
                  </div>
                </div>
              ),
            )}

            {thread.isLoading ? (
              <p className={styles.emptyThreadBody} role="status">
                <Spinner size="sm" /> Reading your material…
              </p>
            ) : null}

            {thread.error ? (
              <Alert tone="destructive" live="alert">
                {thread.error}
              </Alert>
            ) : null}
          </div>

          {!isSupportView ? (
            <form className={styles.composer} onSubmit={submitPrompt}>
              <div className={styles.composerRow}>
                <Input
                  label="Enter prompt"
                  hideLabel
                  fieldClassName={styles.composerInput}
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  placeholder={`Ask anything about ${workspace.name}…`}
                  readOnly={thread.isLoading}
                />
                <IconButton
                  label="Help me word this"
                  icon={<Wand2 aria-hidden="true" />}
                  onClick={() => setIsPromptHelperOpen(true)}
                />
                <Button
                  type="submit"
                  variant="primary"
                  isLoading={thread.isLoading}
                  loadingLabel="Sending"
                  disabled={threadExhausted}
                >
                  Send
                </Button>
              </div>

              <div className={styles.composerMeta}>
                <Checkbox
                  label="Use my study profile"
                  description="Adds your background as supporting context. Your course material stays primary."
                  checked={includeProfileContext}
                  onChange={(event) => setIncludeProfileContext(event.target.checked)}
                />
              </div>

              {threadExhausted ? (
                <CreditExhaustedNotice source={creditSource} action="ask another question" />
              ) : null}
            </form>
          ) : (
            <div className={styles.supportReadOnlyNotice}>
              <p className={styles.sourceHint}>
                Chat and AI generation are disabled in read-only support view.
              </p>
            </div>
          )}
        </section>

        <section className={`${styles.panel} ${styles.outputs}`} aria-label="Study tools">
          <div className={styles.outputsScroll}>
            {!isSupportView ? (
              <>
                <div className={styles.panelHead}>
                  <span className={styles.panelLabel}>Make something</span>
                </div>
                <div className={styles.actionList}>
                  <Button
                    alignStart
                    fullWidth
                    size="sm"
                    icon={<Sparkles aria-hidden="true" />}
                    onClick={() => setIsSummaryOpen(true)}
                  >
                    Study guide
                  </Button>
                  <Button
                    alignStart
                    fullWidth
                    size="sm"
                    icon={<Calendar aria-hidden="true" />}
                    onClick={() => setIsRoadmapOpen(true)}
                  >
                    Exam roadmap
                  </Button>
                  <Button
                    alignStart
                    fullWidth
                    size="sm"
                    icon={<Target aria-hidden="true" />}
                    onClick={() => setIsQuizOpen(true)}
                  >
                    Practice quiz
                  </Button>
                  <Button
                    alignStart
                    fullWidth
                    size="sm"
                    icon={<Layers3 aria-hidden="true" />}
                    onClick={() => setIsFlashcardOpen(true)}
                  >
                    Flashcards
                  </Button>
                  <Button
                    alignStart
                    fullWidth
                    size="sm"
                    icon={<HelpCircle aria-hidden="true" />}
                    onClick={() => navigate(`/courses/${workspace.id}/reverse-quiz`)}
                  >
                    Reverse quiz
                  </Button>
                </div>

                <div className={styles.divider} />
              </>
            ) : null}

            {!isSupportView &&
            (generationJobs.isLoading || generationJobs.error || generationJobs.jobs.length > 0) ? (
              <>
                <div className={styles.panelHead}>
                  <span className={styles.panelLabel}>Generation activity</span>
                </div>
                <GenerationRail
                  jobs={generationJobs.jobs}
                  isLoading={generationJobs.isLoading}
                  error={generationJobs.error}
                  retryingId={generationJobs.retryingId}
                  onReload={() => void generationJobs.reload()}
                  onRetry={(jobId) => void generationJobs.retry(jobId)}
                  onOpenGuide={(outputId) => {
                    setMadeForYouInitialId(outputId);
                    setIsMadeForYouOpen(true);
                  }}
                  onOpenQuiz={(quizId) => navigate(`/courses/${workspace.id}/practice/${quizId}`)}
                  onOpenFlashcards={(outputId) => setOpenDeckId(outputId)}
                />
                <div className={styles.divider} />
              </>
            ) : null}

            <div className={styles.panelHead}>
              <span className={styles.panelLabel}>
                Made for you{artifacts.length > 0 ? ` · ${artifacts.length}` : ''}
              </span>
            </div>
            <ArtifactRail
              artifacts={artifacts}
              isLoading={areArtifactsLoading}
              error={artifactsError}
              onRetry={reloadArtifacts}
              onOpenAll={() => setIsMadeForYouOpen(true)}
              onOpen={(artifact) => {
                if (artifact.kind === 'flashcards') {
                  setOpenDeckId(artifact.outputId);
                  return;
                }
                setMadeForYouInitialId(artifact.outputId);
                setIsMadeForYouOpen(true);
              }}
              onOpenProgress={() => navigate(`/courses/${workspace.id}/progress`)}
            />

            <p className={styles.sourceHint}>
              {canGenerate
                ? `${readyCount} ${readyCount === 1 ? 'source is' : 'sources are'} ready to generate from.`
                : 'Nothing is ready to generate from yet.'}
            </p>
          </div>
        </section>
      </div>

      {isSummaryOpen ? (
        <StudyGuideModal
          courseId={courseId}
          courseName={workspace.name}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onQueued={() => void generationJobs.reload()}
          onClose={() => {
            setIsSummaryOpen(false);
            reloadArtifacts();
          }}
        />
      ) : null}

      {openDeckId !== null ? (
        <SavedDeckModal
          courseId={courseId}
          outputId={openDeckId}
          courseName={workspace.name}
          onClose={() => setOpenDeckId(null)}
        />
      ) : null}

      {isMadeForYouOpen ? (
        <StudyHistoryModal
          courseId={courseId}
          courseName={workspace.name}
          initialSelectedId={madeForYouInitialId}
          onClose={() => {
            setIsMadeForYouOpen(false);
            setMadeForYouInitialId(null);
            if (searchParams.has('artifact')) {
              const nextParams = new URLSearchParams(searchParams);
              nextParams.delete('artifact');
              setSearchParams(nextParams, { replace: true });
            }
          }}
        />
      ) : null}

      {isRoadmapOpen ? (
        <ExamRoadmapModal
          courseId={courseId}
          courseName={workspace.name}
          examDate={workspace.examDate}
          hasTopics={workspace.topics.length > 0}
          onClose={() => {
            setIsRoadmapOpen(false);
            reloadArtifacts();
          }}
          onGenerated={() => {
            reloadArtifacts();
          }}
        />
      ) : null}

      {isQuizOpen ? (
        <QuizModal
          onQueued={() => void generationJobs.reload()}
          courseId={courseId}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onClose={() => setIsQuizOpen(false)}
          onAttemptRecorded={reloadProgress}
        />
      ) : null}

      {isFlashcardOpen ? (
        <FlashcardModal
          courseId={courseId}
          courseName={workspace.name}
          readyDocumentCount={readyCount}
          onQueued={() => void generationJobs.reload()}
          onClose={() => {
            setIsFlashcardOpen(false);
            reloadArtifacts();
          }}
        />
      ) : null}

      <PromptGeneratorDialog
        open={isPromptHelperOpen}
        onClose={() => setIsPromptHelperOpen(false)}
        onGenerated={setPrompt}
      />

      {isPastThreadsOpen ? (
        <ConversationHistoryModal
          courseId={courseId}
          courseName={workspace.name}
          canResume={workspace.ownerId == null || workspace.ownerId === user?.id}
          onClose={() => setIsPastThreadsOpen(false)}
          onResume={resumeConversation}
          onDelete={(conversationId) => {
            (['course_qa', 'ai_tutor'] as const).forEach((type) => {
              if (getStoredConversationId(courseId, type) === conversationId) {
                setStoredConversationId(courseId, type, null);
              }
            });
            setThreads((state) => {
              let changed = false;
              const next = { ...state };
              for (const type of ['course_qa', 'ai_tutor'] as const) {
                if (next[type].conversationId === conversationId) {
                  next[type] = EMPTY_THREAD;
                  changed = true;
                }
              }
              return changed ? next : state;
            });
          }}
        />
      ) : null}

    </div>
  );
}
