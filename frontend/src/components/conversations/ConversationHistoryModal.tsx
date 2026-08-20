import { useEffect, useRef, useState } from 'react';
import {
  Bot,
  Clock3,
  History,
  MessageCircle,
  MessagesSquare,
  UserRound,
  X,
} from 'lucide-react';
import { conversationsAPI } from '../../api/conversations';
import { describeError, isAbortError } from '../../api/errors';
import type {
  ConversationDetail,
  ConversationSummary,
  ConversationType,
} from '../../api/types';
import './conversations.css';

interface ConversationHistoryModalProps {
  courseId: number;
  courseName: string;
  canResume?: boolean;
  onClose: () => void;
  onResume: (conversation: ConversationDetail) => void;
}

type ListState =
  | { phase: 'loading' }
  | { phase: 'ready'; conversations: ConversationSummary[] }
  | { phase: 'error'; message: string };

type DetailState =
  | { phase: 'empty' }
  | { phase: 'loading' }
  | { phase: 'ready'; conversation: ConversationDetail }
  | { phase: 'error'; message: string };

const TYPE_LABELS: Record<ConversationType, string> = {
  course_qa: 'Course Q&A',
  ai_tutor: 'AI Tutor',
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function ConversationHistoryModal({
  courseId,
  courseName,
  canResume = true,
  onClose,
  onResume,
}: ConversationHistoryModalProps) {
  const [listState, setListState] = useState<ListState>({ phase: 'loading' });
  const [detailState, setDetailState] = useState<DetailState>({ phase: 'empty' });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== 'Tab' || !dialogRef.current) return;

      const focusable = Array.from(
        dialogRef.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();

    conversationsAPI
      .list(courseId, { signal: controller.signal })
      .then((conversations) => {
        if (controller.signal.aborted) return;
        setListState({ phase: 'ready', conversations });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setListState({
          phase: 'error',
          message: describeError(
            error,
            'Conversation history could not be loaded.',
          ).message,
        });
      });

    return () => {
      controller.abort();
      detailAbortRef.current?.abort();
    };
  }, [courseId]);

  const selectConversation = (conversationId: number) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;

    setSelectedId(conversationId);
    setDetailState({ phase: 'loading' });

    conversationsAPI
      .get(courseId, conversationId, { signal: controller.signal })
      .then((conversation) => {
        if (controller.signal.aborted) return;
        setDetailState({ phase: 'ready', conversation });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted || isAbortError(error)) return;
        setDetailState({
          phase: 'error',
          message: describeError(error, 'This conversation could not be loaded.')
            .message,
        });
      });
  };

  return (
    <div className="conversation-modal-backdrop">
      <section
        ref={dialogRef}
        className="conversation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="conversation-history-title"
      >
        <header className="conversation-modal-header">
          <div>
            <h2 id="conversation-history-title">
              <History aria-hidden="true" />
              Conversation history
            </h2>
            <p>Course Q&A and AI Tutor threads for {courseName}</p>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            aria-label="Close conversation history"
          >
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="conversation-history-layout">
          <aside className="conversation-history-sidebar" aria-label="Saved conversations">
            {listState.phase === 'loading' ? (
              <p className="conversation-state" role="status">
                Loading conversations...
              </p>
            ) : null}

            {listState.phase === 'error' ? (
              <p className="conversation-state is-error" role="alert">
                {listState.message}
              </p>
            ) : null}

            {listState.phase === 'ready' && listState.conversations.length === 0 ? (
              <div className="conversation-empty-state">
                <MessagesSquare aria-hidden="true" />
                <strong>No conversations yet</strong>
                <span>Ask a course question or start a tutoring session.</span>
              </div>
            ) : null}

            {listState.phase === 'ready' && listState.conversations.length > 0 ? (
              <ul className="conversation-history-list">
                {listState.conversations.map((conversation) => (
                  <li key={conversation.id}>
                    <button
                      type="button"
                      aria-label={`Conversation ${conversation.id}: ${conversation.preview || TYPE_LABELS[conversation.conversation_type]}`}
                      className={
                        selectedId === conversation.id
                          ? 'conversation-history-entry is-selected'
                          : 'conversation-history-entry'
                      }
                      onClick={() => selectConversation(conversation.id)}
                      aria-current={selectedId === conversation.id}
                    >
                      <span
                        className={`conversation-type-badge is-${conversation.conversation_type}`}
                      >
                        {conversation.conversation_type === 'ai_tutor' ? (
                          <Bot aria-hidden="true" />
                        ) : (
                          <MessageCircle aria-hidden="true" />
                        )}
                        {TYPE_LABELS[conversation.conversation_type]}
                      </span>
                      <strong>
                        {conversation.preview || `Conversation ${conversation.id}`}
                      </strong>
                      <span className="conversation-entry-count">
                        {conversation.message_count} message
                        {conversation.message_count === 1 ? '' : 's'}
                      </span>
                      <span className="conversation-entry-date">
                        <Clock3 aria-hidden="true" />
                        {formatDate(conversation.updated_at)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
          </aside>

          <div className="conversation-history-detail">
            {detailState.phase === 'empty' ? (
              <div className="conversation-empty-state is-detail">
                <MessagesSquare aria-hidden="true" />
                <strong>Select a conversation</strong>
                <span>Read the saved exchange before resuming it.</span>
              </div>
            ) : null}

            {detailState.phase === 'loading' ? (
              <p className="conversation-state" role="status">
                Loading messages...
              </p>
            ) : null}

            {detailState.phase === 'error' ? (
              <p className="conversation-state is-error" role="alert">
                {detailState.message}
              </p>
            ) : null}

            {detailState.phase === 'ready' ? (
              <>
                <div className="conversation-detail-heading">
                  <div>
                    <span
                      className={`conversation-type-badge is-${detailState.conversation.conversation_type}`}
                    >
                      {TYPE_LABELS[detailState.conversation.conversation_type]}
                    </span>
                    <h3>Conversation {detailState.conversation.id}</h3>
                  </div>
                  {canResume ? (
                    <button
                      className="primary-button"
                      type="button"
                      onClick={() => onResume(detailState.conversation)}
                    >
                      Resume conversation
                    </button>
                  ) : (
                    <span className="conversation-read-only">Read-only access</span>
                  )}
                </div>

                <ol className="conversation-detail-messages">
                  {detailState.conversation.messages.map((message) => (
                    <li className={`conversation-detail-message is-${message.role}`} key={message.id}>
                      <span className="conversation-message-author">
                        {message.role === 'user' ? (
                          <UserRound aria-hidden="true" />
                        ) : (
                          <Bot aria-hidden="true" />
                        )}
                        {message.role === 'user' ? 'You' : 'Lumina'}
                      </span>
                      <p>{message.content}</p>
                    </li>
                  ))}
                </ol>
              </>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
