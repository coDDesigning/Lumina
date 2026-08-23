import { FormEvent, useEffect, useRef, useState, useCallback } from 'react'
import {
  Bot,
  CircleHelp,
  FilePlus2,
  History,
  Layers3,
  Menu,
  MessageCircle,
  MessageSquarePlus,
  Search,
  Sparkles,
  Target,
  UserRound,
} from 'lucide-react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import WorkspaceNavigation from './components/WorkspaceNavigation'
import type { Workspace, WorkspaceDraft } from './data/workspaces'
import EditPage from './pages/EditPage'
import ProfilePage from './pages/ProfilePage'
import SettingsPage from './pages/SettingsPage'
import AdminPage from './pages/AdminPage'
import WorkspacesPage from './pages/WorkspacesPage'
import LandingPage from './pages/LandingPage'
import LoginPage from './pages/LoginPage'
import RegisterPage from './pages/RegisterPage'
import { ProtectedRoute } from './components/ProtectedRoute'
import { LoadingSpinner } from './components/LoadingSpinner'
import { useAuth } from './context/AuthContext'
import { coursesAPI } from './api/courses'
import { conversationsAPI } from './api/conversations'
import { progressAPI } from './api/progress'
import { courseQaAPI } from './api/courseQa'
import { aiTutorAPI } from './api/aiTutor'
import { promptGeneratorAPI } from './api/promptGenerator'
import {
  describeError,
  describeGenerationError,
  describeUploadError,
  isAbortError,
  isInsufficientCredits,
} from './api/errors'
import { useCredits } from './context/CreditContext'
import CreditBalance from './components/credits/CreditBalance'
import CreditExhaustedNotice from './components/credits/CreditExhaustedNotice'
import type {
  ConversationDetail,
  CreditSource,
  ConversationRole,
  ConversationType,
  Course,
  CourseProgressResponse,
  RetrievedContext,
} from './api/types'
import { useCourseDocuments } from './hooks/useCourseDocuments'
import { DocumentRow } from './components/documents/DocumentRow'
import './App.css'
import './pages/pages.css'
import './pages/workspaces.css'

import { SummaryModal } from './components/study/SummaryModal'
import { StudyHistoryModal } from './components/study/StudyHistoryModal'
import { QuizModal } from './components/study/QuizModal'
import { FlashcardModal } from './components/study/FlashcardModal'
import { ProgressDashboard } from './components/study/ProgressDashboard'
import { ConversationHistoryModal } from './components/conversations/ConversationHistoryModal'

type WorkspaceTab = 'Exam' | 'Tutoring' | 'Practice' | 'Analytics'

type WorkspaceChatMessage = {
  role: ConversationRole
  content: string
  context?: RetrievedContext
}

type WorkspaceConversation = {
  conversationId: number | null
  messages: WorkspaceChatMessage[]
  isLoading: boolean
  error: string | null
}

function getActiveConversationStorageKey(
  courseId: number,
  type: ConversationType,
): string {
  return `lumina.activeConversation.${courseId}.${type}`
}

const tabContent: Record<
  Exclude<WorkspaceTab, 'Analytics'>,
  { body: string; suggestions: string[] }
> = {
  Exam: {
    body: `Ask questions about your uploaded course materials, review concepts, or prepare for exams with retrieval-backed answers. Choose a suggestion below or enter a question to start.`,
    suggestions: [
      'Generate summary',
      'Start a quick practice set',
      'Generate multi-choice problems',
      'What are the key concepts in these sources?',
    ],
  },
  Tutoring: {
    body: `Turn your uploaded material into a focused tutoring session. Lumina can explain difficult ideas one step at a time, connect related concepts, and adapt each explanation to the questions you ask. Choose a suggestion below or enter a topic you would like to understand better.`,
    suggestions: [
      'Explain the central argument',
      'Teach this topic step by step',
      'Generate summary',
      'Ask me a guiding question',
    ],
  },
  Practice: {
    body: `Build a practice session from the sources in this workspace. You can review key concepts, answer questions at your own pace, and identify topics that need more attention before the exam.`,
    suggestions: [
      'Start a quick practice set',
      'Create true or false questions',
      'Practice my weakest topic',
      'Generate summary',
    ],
  },
}

type WorkspacePageProps = {
  workspace: Workspace
  onUpdateProgress?: (workspaceId: string, progress: number) => void
}

function WorkspacePage({ workspace, onUpdateProgress }: WorkspacePageProps) {
  const { user } = useAuth()
  const {
    refresh: refreshCredits,
    canAfford: canAffordCredits,
    isMetered: creditsMetered,
  } = useCredits()
  const promptGeneratorExhausted =
    creditsMetered && !canAffordCredits('prompt_generator')
  const courseId = Number(workspace.id)
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('Exam')
  const [generatorPrompt, setGeneratorPrompt] = useState('')
  const [mainPrompt, setMainPrompt] = useState('')
  const [isSummaryModalOpen, setIsSummaryModalOpen] = useState(false)
  const [isHistoryModalOpen, setIsHistoryModalOpen] = useState(false)
  const [isConversationHistoryOpen, setIsConversationHistoryOpen] = useState(false)
  const [isQuizModalOpen, setIsQuizModalOpen] = useState(false)
  const [isFlashcardModalOpen, setIsFlashcardModalOpen] = useState(false)
  const [includeProfileContext, setIncludeProfileContext] = useState(false)
  const [isGeneratingPrompt, setIsGeneratingPrompt] = useState(false)
  const [promptGeneratorError, setPromptGeneratorError] = useState<string | null>(null)
  const [uploadErrors, setUploadErrors] = useState<{ fileName: string; message: string }[]>([])
  const [uploadNotices, setUploadNotices] = useState<string[]>([])
  const [uploadProgress, setUploadProgress] = useState<{ done: number; total: number } | null>(null)
  const [progress, setProgress] = useState<CourseProgressResponse | null>(null)
  const [isProgressLoading, setIsProgressLoading] = useState(false)
  const [progressError, setProgressError] = useState<string | null>(null)
  const [progressToken, setProgressToken] = useState(0)
  const [conversations, setConversations] = useState<
    Record<ConversationType, WorkspaceConversation>
  >({
    course_qa: {
      conversationId: null,
      messages: [],
      isLoading: false,
      error: null,
    },
    ai_tutor: {
      conversationId: null,
      messages: [],
      isLoading: false,
      error: null,
    },
  })
  const fileInputRef = useRef<HTMLInputElement>(null)
  const conversationsRef = useRef(conversations)
  conversationsRef.current = conversations

  const activeConversationType: ConversationType =
    activeTab === 'Tutoring' ? 'ai_tutor' : 'course_qa'
  const activeConversation = conversations[activeConversationType]
  const conversationSource: CreditSource = activeConversationType
  const conversationExhausted =
    creditsMetered && !canAffordCredits(conversationSource)

  const {
    entries,
    isLoading: areDocumentsLoading,
    listError,
    readyCount,
    reload,
    addUploaded,
    retryDocument,
    deleteDocument,
  } = useCourseDocuments(courseId)

  useEffect(() => {
    if (!Number.isInteger(courseId) || courseId <= 0) return

    const controller = new AbortController()
    let cancelled = false

    setIsProgressLoading(true)
    setProgressError(null)

    progressAPI
      .get(courseId, { signal: controller.signal })
      .then((result) => {
        if (cancelled) return
        setProgress(result)
        setIsProgressLoading(false)
      })
      .catch((error: unknown) => {
        if (cancelled || isAbortError(error)) return
        setIsProgressLoading(false)
        setProgressError(describeError(error, 'Progress could not be loaded.').message)
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [courseId, progressToken])

  useEffect(() => {
    if (!onUpdateProgress || progress?.average_score == null) return
    onUpdateProgress(workspace.id, Math.round(progress.average_score * 100))
  }, [onUpdateProgress, progress, workspace.id])

  useEffect(() => {
    if (!Number.isInteger(courseId) || courseId <= 0) return
    const savedIdStr = sessionStorage.getItem(
      getActiveConversationStorageKey(courseId, activeConversationType),
    )
    if (!savedIdStr) return
    const savedId = Number(savedIdStr)
    if (!Number.isInteger(savedId) || savedId <= 0) return

    if (
      conversationsRef.current[activeConversationType].conversationId === savedId &&
      conversationsRef.current[activeConversationType].messages.length > 0
    ) {
      return
    }

    const controller = new AbortController()
    let cancelled = false

    setConversations((current) => ({
      ...current,
      [activeConversationType]: {
        ...current[activeConversationType],
        isLoading: true,
        error: null,
      },
    }))

    conversationsAPI
      .get(courseId, savedId, { signal: controller.signal })
      .then((detail) => {
        if (cancelled) return
        setConversations((current) => ({
          ...current,
          [activeConversationType]: {
            conversationId: detail.id,
            messages: detail.messages.map(({ role, content }) => ({
              role,
              content,
            })),
            isLoading: false,
            error: null,
          },
        }))
      })
      .catch((error: unknown) => {
        if (cancelled || isAbortError(error)) return
        sessionStorage.removeItem(
          getActiveConversationStorageKey(courseId, activeConversationType),
        )
        setConversations((current) => ({
          ...current,
          [activeConversationType]: {
            conversationId: null,
            messages: [],
            isLoading: false,
            error: null,
          },
        }))
      })

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [courseId, activeConversationType])

  const addSources = async (fileList: FileList | null) => {
    const files = Array.from(fileList ?? [])
    if (files.length === 0) return

    setUploadErrors([])
    setUploadNotices([])
    setUploadProgress({ done: 0, total: files.length })

    const errors: { fileName: string; message: string }[] = []
    const notices: string[] = []

    for (const file of files) {
      try {
        const response = await coursesAPI.uploadDocument(courseId, file)
        addUploaded(response.document)
        if (response.duplicate) {
          notices.push(`${file.name} is already in this course.`)
        }
      } catch (error) {
        errors.push({ fileName: file.name, message: describeUploadError(error).message })
      } finally {
        setUploadProgress((current) =>
          current ? { ...current, done: current.done + 1 } : current
        )
      }
    }

    setUploadErrors(errors)
    setUploadNotices(notices)
    setUploadProgress(null)
  }

  const generatePrompt = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const request = generatorPrompt.trim()
    if (!request || isGeneratingPrompt) return
    if (promptGeneratorExhausted) return

    setIsGeneratingPrompt(true)
    setPromptGeneratorError(null)

    try {
      const res = await promptGeneratorAPI.generate({ description: request })
      setMainPrompt(res.generated_prompt)
      setGeneratorPrompt('')
      void refreshCredits()
    } catch (err) {
      const described = describeGenerationError(
        err,
        'Failed to generate prompt. Please try again.',
      )
      if (isInsufficientCredits(described)) {
        await refreshCredits()
        setPromptGeneratorError(null)
      } else {
        setPromptGeneratorError(described.message)
      }
    } finally {
      setIsGeneratingPrompt(false)
    }
  }

  const submitPrompt = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const prompt = mainPrompt.trim()
    if (!prompt) return

    const conversationType: ConversationType =
      activeTab === 'Tutoring' ? 'ai_tutor' : 'course_qa'
    const conversation = conversations[conversationType]
    if (conversation.isLoading) return
    // Guarded here rather than only on the button, so an implicit submit from
    // the Enter key cannot get past an empty balance either.
    if (conversationExhausted) return

    setMainPrompt('')
    setConversations((current) => ({
      ...current,
      [conversationType]: {
        ...current[conversationType],
        isLoading: true,
        error: null,
      },
    }))

    const request = {
      question: prompt,
      use_profile_knowledge: includeProfileContext,
      include_profile_context: includeProfileContext,
      ...(conversation.conversationId
        ? { conversation_id: conversation.conversationId }
        : {}),
    }

    try {
      const res =
        conversationType === 'ai_tutor'
          ? await aiTutorAPI.ask(courseId, request)
          : await courseQaAPI.ask(courseId, request)

      setConversations((current) => ({
        ...current,
        [conversationType]: {
          ...current[conversationType],
          conversationId: res.conversation_id,
          messages: [
            ...current[conversationType].messages,
            { role: 'user', content: prompt },
            {
              role: 'assistant',
              content: res.answer,
              context: {
                context_truncated: res.context_truncated,
                chunks_used: res.chunks_used,
                chunks_available: res.chunks_available,
                retrieval_narrowed: res.retrieval_narrowed,
                lowest_similarity: res.lowest_similarity,
                highest_similarity: res.highest_similarity,
              },
            },
          ],
        },
      }))
      sessionStorage.setItem(
        getActiveConversationStorageKey(courseId, conversationType),
        String(res.conversation_id),
      )
      void refreshCredits()
    } catch (err) {
      setMainPrompt(prompt)
      const described = describeGenerationError(
        err,
        conversationType === 'ai_tutor'
          ? 'Failed to generate tutor explanation from course materials.'
          : 'Failed to generate answer from course materials.',
      )
      if (isInsufficientCredits(described)) {
        // The exhaustion notice below the composer carries the recovery route,
        // so a duplicate inline error would only repeat it. Earlier messages in
        // the conversation stay exactly where they are.
        await refreshCredits()
      } else {
        setConversations((current) => ({
          ...current,
          [conversationType]: {
            ...current[conversationType],
            error: described.message,
          },
        }))
      }
    } finally {
      setConversations((current) => ({
        ...current,
        [conversationType]: {
          ...current[conversationType],
          isLoading: false,
        },
      }))
    }
  }

  const startNewConversation = () => {
    sessionStorage.removeItem(
      getActiveConversationStorageKey(courseId, activeConversationType),
    )
    setConversations((current) => ({
      ...current,
      [activeConversationType]: {
        conversationId: null,
        messages: [],
        isLoading: false,
        error: null,
      },
    }))
  }

  const resumeConversation = (conversation: ConversationDetail) => {
    sessionStorage.setItem(
      getActiveConversationStorageKey(courseId, conversation.conversation_type),
      String(conversation.id),
    )
    setConversations((current) => ({
      ...current,
      [conversation.conversation_type]: {
        conversationId: conversation.id,
        messages: conversation.messages.map(({ role, content }) => ({
          role,
          content,
        })),
        isLoading: false,
        error: null,
      },
    }))
    setActiveTab(conversation.conversation_type === 'ai_tutor' ? 'Tutoring' : 'Exam')
    setIsConversationHistoryOpen(false)
    setMainPrompt('')
  }

  const handleConversationDeleted = (deletedId: number) => {
    setConversations((current) => {
      const updated = { ...current }
      let changed = false
      for (const type of ['course_qa', 'ai_tutor'] as const) {
        if (updated[type].conversationId === deletedId) {
          sessionStorage.removeItem(getActiveConversationStorageKey(courseId, type))
          updated[type] = {
            conversationId: null,
            messages: [],
            isLoading: false,
            error: null,
          }
          changed = true
        }
      }
      return changed ? updated : current
    })
  }

  const chooseSuggestion = (suggestion: string) => {
    if (suggestion === 'Generate summary') {
      setIsSummaryModalOpen(true)
      return
    }
    if (
      suggestion === 'Start a quick practice set' ||
      suggestion === 'Create true or false questions' ||
      suggestion === 'Generate multi-choice problems' ||
      suggestion === 'Practice my weakest topic'
    ) {
      setIsQuizModalOpen(true)
      return
    }
    setMainPrompt(suggestion)
  }

  const tabList: WorkspaceTab[] = ['Exam', 'Tutoring', 'Practice', 'Analytics']
  const processingCount = entries.filter(
    (entry) => entry.document.status === 'uploaded' || entry.document.status === 'processing'
  ).length

  return (
    <main className="workspace-shell">
      <aside className="sidebar" aria-label="Study sources and prompt tools">
        <section className="panel sources-panel">
          <header className="panel-header">
            <h1>Sources</h1>
          </header>

          <p className="visually-hidden" role="status">
            {processingCount > 0
              ? `${processingCount} source${processingCount === 1 ? '' : 's'} still processing`
              : 'All sources are up to date'}
          </p>

          {listError ? (
            <p className="source-list-message" role="alert">
              {listError}{' '}
              <button className="text-action" type="button" onClick={reload}>
                Reload
              </button>
            </p>
          ) : null}

          {uploadErrors.length > 0 ? (
            <ul className="source-upload-errors" role="alert">
              {uploadErrors.map((failure) => (
                <li key={failure.fileName}>
                  {failure.fileName}: {failure.message}
                </li>
              ))}
            </ul>
          ) : null}

          {uploadNotices.length > 0 ? (
            <ul className="source-upload-errors source-upload-notices">
              {uploadNotices.map((notice) => (
                <li key={notice}>{notice}</li>
              ))}
            </ul>
          ) : null}

          <div className="source-list">
            {areDocumentsLoading && entries.length === 0 ? (
              <p className="source-list-message">Loading sources…</p>
            ) : null}

            {!areDocumentsLoading && entries.length === 0 && !listError ? (
              <p className="source-list-message">
                No sources yet. Add a PDF, TXT, or Markdown file to get started.
              </p>
            ) : null}

            {entries.map((entry) => (
              <DocumentRow
                key={entry.document.id}
                entry={entry}
                onRetry={retryDocument}
                onDelete={deleteDocument}
              />
            ))}
          </div>

          <div className="add-source-row">
            <input
              ref={fileInputRef}
              className="visually-hidden"
              type="file"
              multiple
              accept=".pdf,.txt,.md,.markdown"
              onChange={(event) => {
                addSources(event.target.files)
                event.target.value = ''
              }}
            />
            <button
              className="text-action"
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadProgress !== null}
            >
              <FilePlus2 aria-hidden="true" strokeWidth={2.1} />
              {uploadProgress
                ? `Uploading ${uploadProgress.done} of ${uploadProgress.total}…`
                : 'Add Sources'}
            </button>
          </div>
        </section>

        <section className="panel generator-panel">
          <header className="panel-header">
            <h2>Prompt Generator</h2>
          </header>

          <div className="generator-description">
            <CircleHelp aria-hidden="true" strokeWidth={2.2} />
            <p>
              Enter a description of your desired prompt to generate study activities, summaries, or quizzes.
            </p>
          </div>

          <form className="prompt-field" onSubmit={generatePrompt}>
            <Menu aria-hidden="true" />
            <label className="visually-hidden" htmlFor="generator-prompt">
              Prompt description
            </label>
            <input
              id="generator-prompt"
              value={generatorPrompt}
              onChange={(event) => setGeneratorPrompt(event.target.value)}
              placeholder="e.g. Create an exam revision guide"
              disabled={isGeneratingPrompt}
            />
            <button
              type="submit"
              aria-label="Generate prompt"
              disabled={isGeneratingPrompt || promptGeneratorExhausted}
            >
              {isGeneratingPrompt ? (
                <LoadingSpinner size="sm" />
              ) : (
                <Search aria-hidden="true" />
              )}
            </button>
          </form>
          {promptGeneratorExhausted ? (
            <CreditExhaustedNotice
              source="prompt_generator"
              action="a prompt"
              className="generator-credit-notice"
            />
          ) : null}
          {promptGeneratorError && (
            <p
              style={{
                color: '#dc2626',
                fontSize: '12px',
                marginTop: '8px',
                padding: '0 4px',
              }}
              role="alert"
            >
              {promptGeneratorError}
            </p>
          )}
        </section>
      </aside>

      <section className="main-workspace">
        <WorkspaceNavigation workspaceId={workspace.id} />

        <div className="workspace-stage">
          <div className="workspace-tabs" role="tablist" aria-label="Study mode">
            {tabList.map((tab) => (
              <button
                className={activeTab === tab ? 'active' : ''}
                type="button"
                role="tab"
                aria-selected={activeTab === tab}
                key={tab}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 'Analytics' ? (
            <ProgressDashboard
              courseName={workspace.name}
              documentCount={entries.length}
              readyDocumentCount={readyCount}
              progress={progress}
              isLoading={isProgressLoading}
              error={progressError}
              onOpenQuizModal={() => setIsQuizModalOpen(true)}
              onOpenSummaryModal={() => setIsSummaryModalOpen(true)}
            />
          ) : (
            <section className="panel chat-panel" role="tabpanel">
              <header className="panel-header chat-header">
                <div>
                  <h2>Chat & Study Tools</h2>
                  <span className="chat-mode-label">
                    {activeConversationType === 'ai_tutor' ? 'AI Tutor' : 'Course Q&A'}
                  </span>
                </div>
                <div className="chat-tool-actions">
                  <button
                    type="button"
                    className="secondary-button chat-tool-button"
                    onClick={() => setIsSummaryModalOpen(true)}
                  >
                    <Sparkles aria-hidden="true" />
                    Summary
                  </button>
                  <button
                    type="button"
                    className="secondary-button chat-tool-button"
                    onClick={() => setIsFlashcardModalOpen(true)}
                  >
                    <Layers3 aria-hidden="true" />
                    Flashcards
                  </button>
                  <button
                    type="button"
                    className="secondary-button chat-tool-button"
                    onClick={() => setIsHistoryModalOpen(true)}
                  >
                    <History aria-hidden="true" />
                    Generated history
                  </button>
                  <button
                    type="button"
                    className="secondary-button chat-tool-button"
                    onClick={() => setIsConversationHistoryOpen(true)}
                    disabled={conversations.course_qa.isLoading || conversations.ai_tutor.isLoading}
                  >
                    <MessageCircle aria-hidden="true" />
                    Conversation history
                  </button>
                  <button
                    type="button"
                    className="primary-button chat-tool-button"
                    onClick={() => setIsQuizModalOpen(true)}
                  >
                    <Target aria-hidden="true" />
                    Practice Quiz
                  </button>
                </div>
              </header>

              <div className="chat-scroll">
                <div className="conversation-toolbar">
                  <p>
                    {activeConversation.conversationId
                      ? `Conversation ${activeConversation.conversationId}`
                      : `New ${activeConversationType === 'ai_tutor' ? 'AI Tutor' : 'Course Q&A'} conversation`}
                  </p>
                  <button
                    type="button"
                    onClick={startNewConversation}
                    disabled={activeConversation.isLoading}
                  >
                    <MessageSquarePlus aria-hidden="true" />
                    New conversation
                  </button>
                </div>

                {activeConversation.messages.length === 0 ? (
                  <>
                    <p className="response-copy">{tabContent[activeTab].body}</p>

                    <div className="suggestions" aria-label="Suggested prompts">
                      {tabContent[activeTab].suggestions.map((suggestion) => (
                        <button
                          type="button"
                          key={suggestion}
                          onClick={() => chooseSuggestion(suggestion)}
                        >
                          <CircleHelp aria-hidden="true" strokeWidth={2.2} />
                          <span>{suggestion}</span>
                        </button>
                      ))}
                    </div>
                  </>
                ) : (
                  <ol className="workspace-conversation" aria-live="polite">
                    {activeConversation.messages.map((message, index) => (
                      <li
                        className={`workspace-message is-${message.role}`}
                        key={`${message.role}-${index}`}
                      >
                        <span className="workspace-message-author">
                          {message.role === 'user' ? (
                            <UserRound aria-hidden="true" />
                          ) : (
                            <Bot aria-hidden="true" />
                          )}
                          {message.role === 'user' ? 'You' : 'Lumina'}
                        </span>
                        <p>{message.content}</p>
                        {message.context ? (
                          <div className="workspace-message-context">
                            <span>
                              {message.context.chunks_used} of{' '}
                              {message.context.chunks_available} course chunks used
                            </span>
                            {message.context.retrieval_narrowed ? (
                              <span>Focused on the most relevant course material</span>
                            ) : null}
                            {message.context.context_truncated ? (
                              <span>Some retrieved context was omitted for length</span>
                            ) : null}
                            {message.context.lowest_similarity != null &&
                            message.context.highest_similarity != null ? (
                              <span>
                                Similarity {message.context.lowest_similarity.toFixed(2)} to{' '}
                                {message.context.highest_similarity.toFixed(2)}
                              </span>
                            ) : null}
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                )}

                {activeConversation.isLoading ? (
                  <div className="qa-loading-indicator" role="status">
                    <LoadingSpinner size="sm" />
                    <p>
                      {activeConversation.messages.length === 0 &&
                      activeConversation.conversationId
                        ? 'Loading conversation history...'
                        : activeConversationType === 'ai_tutor'
                          ? 'AI Tutor is preparing personalized guidance...'
                          : 'Searching course materials and preparing an answer...'}
                    </p>
                  </div>
                ) : null}

                {activeConversation.error ? (
                  <div className="qa-error-alert" role="alert">
                    <p>{activeConversation.error}</p>
                  </div>
                ) : null}

                {conversationExhausted ? (
                  <CreditExhaustedNotice
                    source={conversationSource}
                    action={
                      conversationSource === 'ai_tutor'
                        ? 'a tutor explanation'
                        : 'an answer'
                    }
                  />
                ) : null}
              </div>

              <div className="composer-credit-balance">
                <CreditBalance source={conversationSource} />
              </div>

              <div className="composer-profile-toggle">
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

              <form className="prompt-field main-prompt" onSubmit={submitPrompt}>
                <Menu aria-hidden="true" />
                <label className="visually-hidden" htmlFor="main-prompt">
                  Enter prompt
                </label>
                <input
                  id="main-prompt"
                  value={mainPrompt}
                  onChange={(event) => setMainPrompt(event.target.value)}
                  placeholder={
                    activeConversationType === 'ai_tutor'
                      ? 'Ask your tutor a follow-up question...'
                      : 'Ask a question about your course materials...'
                  }
                  disabled={activeConversation.isLoading}
                />
                <button
                  type="submit"
                  aria-label="Submit prompt"
                  disabled={activeConversation.isLoading || conversationExhausted}
                >
                  {activeConversation.isLoading ? (
                    <LoadingSpinner size="sm" />
                  ) : (
                    <Search aria-hidden="true" />
                  )}
                </button>
              </form>
            </section>
          )}
        </div>
      </section>

      {isSummaryModalOpen ? (
        <SummaryModal
          courseId={courseId}
          courseName={workspace.name}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onClose={() => setIsSummaryModalOpen(false)}
        />
      ) : null}

      {isHistoryModalOpen ? (
        <StudyHistoryModal
          courseId={courseId}
          courseName={workspace.name}
          onClose={() => setIsHistoryModalOpen(false)}
        />
      ) : null}

      {isConversationHistoryOpen ? (
        <ConversationHistoryModal
          courseId={courseId}
          courseName={workspace.name}
          canResume={workspace.ownerId == null || workspace.ownerId === user?.id}
          onClose={() => setIsConversationHistoryOpen(false)}
          onResume={resumeConversation}
          onDelete={handleConversationDeleted}
        />
      ) : null}

      {isQuizModalOpen ? (
        <QuizModal
          courseId={courseId}
          topics={workspace.topics}
          readyDocumentCount={readyCount}
          onClose={() => setIsQuizModalOpen(false)}
          onAttemptRecorded={() => setProgressToken((token) => token + 1)}
        />
      ) : null}

      {isFlashcardModalOpen ? (
        <FlashcardModal
          courseId={courseId}
          courseName={workspace.name}
          readyDocumentCount={readyCount}
          onClose={() => setIsFlashcardModalOpen(false)}
        />
      ) : null}
    </main>

  )
}

const ACTIVE_WORKSPACE_STORAGE_KEY = 'lumina.activeWorkspaceId'
const workspaceAccents: Workspace['accent'][] = [
  'blue',
  'violet',
  'rose',
  'amber',
]

type WorkspaceRouteProps = {
  workspaces: Workspace[]
  isLoading?: boolean
  onSelect: (workspaceId: string) => void
  onUpdateProgress?: (workspaceId: string, progress: number) => void
}

function WorkspaceRoute({ workspaces, isLoading, onSelect, onUpdateProgress }: WorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-gray-50 dark:bg-[#0a0a0a]">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  if (!workspace) return <Navigate to="/" replace />
  return (
    <WorkspacePage
      key={workspace.id}
      workspace={workspace}
      onUpdateProgress={onUpdateProgress}
    />
  )
}

type EditWorkspaceRouteProps = WorkspaceRouteProps & {
  onSave: (workspace: Workspace) => Promise<void> | void
}

function EditWorkspaceRoute({
  workspaces,
  isLoading,
  onSelect,
  onSave,
}: EditWorkspaceRouteProps) {
  const { workspaceId } = useParams()
  const workspace = workspaces.find(({ id }) => id === workspaceId)

  useEffect(() => {
    if (workspace) onSelect(workspace.id)
  }, [onSelect, workspace])

  if (isLoading) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-gray-50 dark:bg-[#0a0a0a]">
        <LoadingSpinner size="lg" />
      </div>
    )
  }

  if (!workspace) return <Navigate to="/" replace />
  return <EditPage key={workspace.id} workspace={workspace} onSave={onSave} />
}

function mapCourseToWorkspace(
  course: Course,
  index: number,
  progressData?: CourseProgressResponse | null,
): Workspace {
  let progress: number | null = null
  let status = 'Not started'

  if (progressData) {
    if (progressData.average_score != null) {
      progress = Math.round(
        progressData.average_score <= 1
          ? progressData.average_score * 100
          : progressData.average_score,
      )
    } else if (progressData.completion != null && progressData.attempts_count > 0) {
      progress = Math.round(progressData.completion)
    }

    if (progressData.attempts_count > 0) {
      status = (progress ?? 0) >= 80 ? 'Mastered' : 'In progress'
    }
  }

  return {
    id: course.id.toString(),
    ownerId: course.owner_id,
    name: course.title,
    subjectArea: course.subject_area || '',
    educationLevel: course.education_level || 'unspecified',
    semester: course.semester || '',
    examDate: course.exam_date || '',
    topics: course.topics ? course.topics.split(',').map((t) => t.trim()) : [],
    syllabus: course.syllabus || '',
    progress,
    status,
    updatedAt: new Date(course.updated_at).toLocaleDateString(),
    accent: workspaceAccents[index % workspaceAccents.length],
    sources: [],
  }
}

function App() {
  const { isAuthenticated } = useAuth()
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [isLoadingWorkspaces, setIsLoadingWorkspaces] = useState(true)
  const [workspacesError, setWorkspacesError] = useState<string | null>(null)

  const [activeWorkspaceId, setActiveWorkspaceId] = useState(
    () => localStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY) ?? '',
  )

  const fetchWorkspaces = useCallback(
    async (signal?: AbortSignal) => {
      if (!isAuthenticated) {
        setIsLoadingWorkspaces(false)
        setWorkspacesError(null)
        return
      }
      setIsLoadingWorkspaces(true)
      setWorkspacesError(null)
      try {
        const courses = await coursesAPI.list({ signal })
        const progressResults = await Promise.allSettled(
          courses.map((course) => progressAPI.get(course.id, { signal })),
        )
        const mappedWorkspaces = courses.map((course, index) => {
          const progResult = progressResults[index]
          const progData =
            progResult?.status === 'fulfilled' ? progResult.value : null
          return mapCourseToWorkspace(course, index, progData)
        })
        setWorkspaces(mappedWorkspaces)

        setActiveWorkspaceId((current) => {
          if (mappedWorkspaces.length > 0 && !current) {
            return mappedWorkspaces[0].id
          }
          return current
        })
      } catch (error: unknown) {
        if (error instanceof Error && error.name === 'AbortError') {
          // Abort/unmount behavior is intentionally NOT shown as an error
          return
        }
        const described = describeError(
          error,
          'Failed to load workspaces. Please try again.',
        )
        setWorkspacesError(described.message)
      } finally {
        setIsLoadingWorkspaces(false)
      }
    },
    [isAuthenticated],
  )

  useEffect(() => {
    const controller = new AbortController()
    fetchWorkspaces(controller.signal)
    return () => {
      controller.abort()
    }
  }, [fetchWorkspaces])

  useEffect(() => {
    if (activeWorkspaceId) {
      localStorage.setItem(ACTIVE_WORKSPACE_STORAGE_KEY, activeWorkspaceId)
    }
  }, [activeWorkspaceId])

  const selectWorkspace = (workspaceId: string) => {
    setActiveWorkspaceId(workspaceId)
  }

  const createWorkspace = async (draft: WorkspaceDraft) => {
    try {
      const newCourse = await coursesAPI.create({
        title: draft.name.trim(),
        subject_area: draft.subjectArea.trim(),
        education_level: draft.educationLevel,
        syllabus: draft.syllabus.trim(),
        semester: draft.semester.trim(),
        exam_date: draft.examDate,
        topics: draft.topics,
      })

      const newWorkspace = mapCourseToWorkspace(newCourse, workspaces.length)
      setWorkspaces((current) => [newWorkspace, ...current])
      setActiveWorkspaceId(newWorkspace.id)
      return newWorkspace
    } catch (error) {
      console.error('Failed to create workspace', error)
      throw error
    }
  }

  const deleteWorkspace = async (workspaceId: string) => {
    await coursesAPI.delete(Number(workspaceId))
    const remaining = workspaces.filter(
      (workspace) => workspace.id !== workspaceId,
    )
    setWorkspaces(remaining)

    if (activeWorkspaceId !== workspaceId) return
    const nextWorkspaceId = remaining[0]?.id ?? ''
    if (!nextWorkspaceId) {
      localStorage.removeItem(ACTIVE_WORKSPACE_STORAGE_KEY)
    }
    setActiveWorkspaceId(nextWorkspaceId)
  }

  const updateWorkspace = async (updatedWorkspace: Workspace) => {
    try {
      const updatedCourse = await coursesAPI.update(
        Number(updatedWorkspace.id),
        {
          title: updatedWorkspace.name.trim(),
          subject_area: updatedWorkspace.subjectArea.trim(),
          education_level: updatedWorkspace.educationLevel,
          syllabus: updatedWorkspace.syllabus.trim(),
          semester: updatedWorkspace.semester.trim(),
          exam_date: updatedWorkspace.examDate,
          topics: updatedWorkspace.topics.join(', '),
        },
      )

      const updatedMappedWorkspace = mapCourseToWorkspace(
        updatedCourse,
        workspaces.findIndex((w) => w.id === updatedWorkspace.id),
      )
      setWorkspaces((current) =>
        current.map((workspace) =>
          workspace.id === updatedWorkspace.id
            ? updatedMappedWorkspace
            : workspace,
        ),
      )
    } catch (error) {
      console.error('Failed to update workspace', error)
    }
  }

  const updateWorkspaceProgress = useCallback(
    (workspaceId: string, progress: number) => {
      setWorkspaces((current) => {
        const target = current.find((w) => w.id === workspaceId)
        if (!target || target.progress === progress) return current
        return current.map((w) =>
          w.id === workspaceId ? { ...w, progress } : w,
        )
      })
    },
    [],
  )

  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />

      <Route element={<ProtectedRoute />}>
        <Route
          path="/dashboard"
          element={
            <WorkspacesPage
              workspaces={workspaces}
              activeWorkspaceId={activeWorkspaceId}
              isLoading={isLoadingWorkspaces}
              error={workspacesError}
              onRetry={() => fetchWorkspaces()}
              onCreate={createWorkspace}
              onSelect={selectWorkspace}
              onDelete={deleteWorkspace}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId"
          element={
            <WorkspaceRoute
              workspaces={workspaces}
              isLoading={isLoadingWorkspaces}
              onSelect={selectWorkspace}
              onUpdateProgress={updateWorkspaceProgress}
            />
          }
        />
        <Route
          path="/workspaces/:workspaceId/edit"
          element={
            <EditWorkspaceRoute
              workspaces={workspaces}
              isLoading={isLoadingWorkspaces}
              onSelect={selectWorkspace}
              onSave={updateWorkspace}
            />
          }
        />
        <Route
          path="/settings"
          element={<SettingsPage workspaceId={activeWorkspaceId} />}
        />
        <Route
          path="/profile"
          element={<ProfilePage workspaceId={activeWorkspaceId} />}
        />
        <Route
          path="/admin"
          element={<AdminPage />}
        />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
