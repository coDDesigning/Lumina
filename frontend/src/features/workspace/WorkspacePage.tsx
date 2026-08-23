import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  BarChart3,
  FileText,
  Layers3,
  MessageSquarePlus,
  Settings2,
  Sparkles,
  Target,
  Upload,
  Wand2,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { coursesAPI } from '@/api/courses';
import { aiTutorAPI } from '@/api/aiTutor';
import { courseQaAPI } from '@/api/courseQa';
import {
  describeGenerationError,
  describeUploadError,
  isInsufficientCredits,
} from '@/api/errors';
import type {
  ConversationDetail,
  ConversationRole,
  ConversationType,
  CreditSource,
  RetrievedContext,
} from '@/api/types';
import { useDocumentTitle } from '@/app/useDocumentTitle';
import CreditBalance from '@/components/credits/CreditBalance';
import CreditExhaustedNotice from '@/components/credits/CreditExhaustedNotice';
import { DocumentRow } from '@/components/documents/DocumentRow';
import { ConversationHistoryModal } from './ConversationHistoryModal';
import { FlashcardModal } from '@/features/study/FlashcardModal';
import { provenanceParts } from '@/features/study/provenanceParts';
import { QuizModal } from '@/features/study/quiz/QuizModal';
import { StudyHistoryModal } from '@/features/study/StudyHistoryModal';
import { StudyGuideModal } from '@/features/study/StudyGuideModal';
import { useCredits } from '@/context/CreditContext';
import { useAuth } from '@/context/AuthContext';
import type { Workspace } from '@/data/workspaces';
import { useCourseDocuments } from '@/hooks/useCourseDocuments';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Checkbox } from '@/ui/Checkbox';
import { IconButton } from '@/ui/IconButton';
import { Input } from '@/ui/Input';
import { PageHeader } from '@/ui/PageHeader';
import { Spinner } from '@/ui/Spinner';
import { Tabs } from '@/ui/Tabs';
import { PromptGeneratorDialog } from './PromptGeneratorDialog';
import { useCourseProgress } from './useCourseProgress';
import styles from './WorkspacePage.module.css';

export interface WorkspacePageProps {
  workspace: Workspace;
  onUpdateProgress?: (workspaceId: string, progress: number) => void;
}

interface ThreadMessage {
  role: ConversationRole;
  content: string;
  context?: RetrievedContext;
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

export default function WorkspacePage({ workspace, onUpdateProgress }: WorkspacePageProps) {
  const { user } = useAuth();
  const {
    refresh: refreshCredits,
    canAfford: canAffordCredits,
    isMetered: creditsMetered,
  } = useCredits();

  const courseId = Number(workspace.id);
  useDocumentTitle(workspace.name);

  const [threadType, setThreadType] = useState<ConversationType>('course_qa');
  const [prompt, setPrompt] = useState('');
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [threads, setThreads] = useState<Record<ConversationType, Thread>>({
    course_qa: EMPTY_THREAD,
    ai_tutor: EMPTY_THREAD,
  });

  const [uploadErrors, setUploadErrors] = useState<{ fileName: string; message: string }[]>([]);
  const [uploadNotices, setUploadNotices] = useState<string[]>([]);
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(
    null,
  );

  const [isSummaryOpen, setIsSummaryOpen] = useState(false);
  const [isQuizOpen, setIsQuizOpen] = useState(false);
  const [isFlashcardOpen, setIsFlashcardOpen] = useState(false);
  const [isMadeForYouOpen, setIsMadeForYouOpen] = useState(false);
  const [isPastThreadsOpen, setIsPastThreadsOpen] = useState(false);
  const [isPromptHelperOpen, setIsPromptHelperOpen] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const thread = threads[threadType];
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

  const { progress, reload: reloadProgress } = useCourseProgress(courseId);

  useEffect(() => {
    if (!onUpdateProgress || progress?.average_score == null) {
      return;
    }
    onUpdateProgress(workspace.id, Math.round(progress.average_score * 100));
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
        const response = await coursesAPI.uploadDocument(courseId, file);
        addUploaded(response.document);
        if (response.duplicate) {
          notices.push(`${file.name} is already in this course.`);
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
    setThreads((state) => ({ ...state, [threadType]: EMPTY_THREAD }));
  }

  function resumeConversation(conversation: ConversationDetail) {
    setThreads((state) => ({
      ...state,
      [conversation.conversation_type]: {
        conversationId: conversation.id,
        messages: conversation.messages.map(({ role, content }) => ({ role, content })),
        isLoading: false,
        error: null,
      },
    }));
    setThreadType(conversation.conversation_type);
    setIsPastThreadsOpen(false);
    setPrompt('');
  }

  const canGenerate = readyCount > 0;

  return (
    <div className={styles.page}>
      <PageHeader
        courseId={workspace.id}
        crumbs={[{ label: 'Courses', to: '/dashboard' }, { label: workspace.name }]}
        badges={
          <>
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
            <Link to={`/workspaces/${workspace.id}/progress`} style={{ textDecoration: 'none' }}>
              <Button variant="ghost" size="sm" icon={<BarChart3 aria-hidden="true" />}>
                Progress
              </Button>
            </Link>
            <Link to={`/workspaces/${workspace.id}/settings`} style={{ textDecoration: 'none' }}>
              <Button variant="ghost" size="sm" icon={<Settings2 aria-hidden="true" />}>
                Course settings
              </Button>
            </Link>
            <CreditBalance source={creditSource} />
          </>
        }
      />

      <div className={styles.columns}>
        <section className={`${styles.panel} ${styles.sources}`} aria-label="Sources">
          <div className={styles.panelHead}>
            <span className={styles.panelLabel}>Sources · {entries.length}</span>
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
            <input
              ref={fileInputRef}
              className={styles.uploadInput}
              type="file"
              multiple
              accept=".pdf,.txt,.md,.markdown"
              onChange={(event) => {
                void addSources(event.target.files);
                event.target.value = '';
              }}
            />
          </div>

          {uploadProgress ? (
            <p className={styles.sourceHint} role="status" style={{ marginTop: 0 }}>
              Uploading {uploadProgress.done} of {uploadProgress.total}…
            </p>
          ) : null}

          {uploadErrors.length > 0 || uploadNotices.length > 0 || listError ? (
            <div className={styles.noticeStack}>
              {listError ? (
                <Alert
                  tone="destructive"
                  live="alert"
                  actions={
                    <Button size="sm" onClick={reload}>
                      Try again
                    </Button>
                  }
                >
                  {listError}
                </Alert>
              ) : null}
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
                />
              ))
            )}
          </div>

          <p className={styles.sourceHint}>PDF, TXT and Markdown · up to 50 MB each</p>
        </section>

        <section className={styles.panel} aria-label="Conversation">
          <div className={styles.panelHead}>
            <Tabs
              label="Conversation type"
              options={THREAD_TABS}
              value={threadType}
              onChange={setThreadType}
            />
            <span style={{ display: 'flex', gap: 'var(--space-1)' }}>
              <Button
                variant="ghost"
                size="sm"
                icon={<MessageSquarePlus aria-hidden="true" />}
                onClick={startNewConversation}
              >
                New conversation
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setIsPastThreadsOpen(true)}>
                Past threads
              </Button>
            </span>
          </div>

          <div className={styles.thread} aria-live="polite">
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
                  <p className={styles.answer}>{message.content}</p>
                  {message.context ? (
                    <span className={styles.provenance}>
                      <FileText className={styles.provenanceIcon} aria-hidden="true" />
                      <span>{provenanceParts(message.context).join(' · ')}</span>
                    </span>
                  ) : null}
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

          <form className={styles.composer} onSubmit={submitPrompt}>
            <div className={styles.composerRow}>
              <Input
                label="Enter prompt"
                hideLabel
                fieldClassName={styles.composerInput}
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder={`Ask anything about ${workspace.name}…`}
                disabled={thread.isLoading}
              />
              <IconButton
                label="Help me word this"
                icon={<Wand2 aria-hidden="true" />}
                onClick={() => setIsPromptHelperOpen(true)}
              />
              <Button
                type="submit"
                variant="primary"
                aria-label="Submit prompt"
                isLoading={thread.isLoading}
                loadingLabel="Sending"
                disabled={threadExhausted}
              >
                Send
              </Button>
            </div>

            <div className={styles.composerMeta}>
              <Checkbox
                label="Include personal study profile context"
                description="Includes your profile background as supplementary context. Course material remains primary and authoritative."
                checked={includeProfileContext}
                onChange={(event) => setIncludeProfileContext(event.target.checked)}
              />
            </div>

            {threadExhausted ? (
              <CreditExhaustedNotice source={creditSource} action="ask another question" />
            ) : null}
          </form>
        </section>

        <section className={`${styles.panel} ${styles.outputs}`} aria-label="Study tools">
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
          </div>

          <div className={styles.divider} />

          <div className={styles.panelHead}>
            <span className={styles.panelLabel}>Already made</span>
          </div>
          <div className={styles.actionList}>
            <Button
              alignStart
              fullWidth
              size="sm"
              variant="ghost"
              onClick={() => setIsMadeForYouOpen(true)}
            >
              Made for you
            </Button>
          </div>

          <p className={styles.sourceHint}>
            {canGenerate
              ? `${readyCount} ${readyCount === 1 ? 'source is' : 'sources are'} ready to generate from.`
              : 'Nothing is ready to generate from yet.'}
          </p>
        </section>
      </div>

      {isSummaryOpen ? (
        <StudyGuideModal
          courseId={courseId}
          courseName={workspace.name}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onClose={() => setIsSummaryOpen(false)}
        />
      ) : null}

      {isMadeForYouOpen ? (
        <StudyHistoryModal
          courseId={courseId}
          courseName={workspace.name}
          onClose={() => setIsMadeForYouOpen(false)}
        />
      ) : null}

      {isQuizOpen ? (
        <QuizModal
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
          onClose={() => setIsFlashcardOpen(false)}
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
        />
      ) : null}

    </div>
  );
}
