import { useEffect, useRef, useState } from 'react';
import { Bot, History, MessageCircle, MessagesSquare, UserRound } from 'lucide-react';
import { conversationsAPI } from '@/api/conversations';
import { describeError, isAbortError } from '@/api/errors';
import type { ConversationDetail, ConversationSummary, ConversationType } from '@/api/types';
import { cx } from '@/lib/cx';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { EmptyState } from '@/ui/EmptyState';
import { DetailError, DetailLoading, DetailPlaceholder, MasterDetail } from '@/ui/MasterDetail';
import styles from './ConversationHistoryModal.module.css';

export interface ConversationHistoryModalProps {
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
  course_qa: 'Question',
  ai_tutor: 'Tutoring',
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

  useEffect(() => {
    const controller = new AbortController();

    conversationsAPI
      .list(courseId, { signal: controller.signal })
      .then((conversations) => {
        if (controller.signal.aborted) {
          return;
        }
        setListState({ phase: 'ready', conversations });
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setListState({
          phase: 'error',
          message: describeError(caught, 'Your past threads could not be loaded.').message,
        });
      });

    return () => {
      controller.abort();
      detailAbortRef.current?.abort();
    };
  }, [courseId]);

  const selectConversation = (conversation: ConversationSummary) => {
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;

    setSelectedId(conversation.id);
    setDetailState({ phase: 'loading' });

    conversationsAPI
      .get(courseId, conversation.id, { signal: controller.signal })
      .then((detail) => {
        if (controller.signal.aborted) {
          return;
        }
        setDetailState({ phase: 'ready', conversation: detail });
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setDetailState({
          phase: 'error',
          message: describeError(caught, 'This thread could not be opened.').message,
        });
      });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      size="xl"
      title="Past threads"
      description={`Questions and tutoring for ${courseName}`}
      mark={<History aria-hidden="true" />}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      {listState.phase === 'loading' ? <DetailLoading label="Loading your threads" /> : null}

      {listState.phase === 'error' ? <DetailError message={listState.message} /> : null}

      {listState.phase === 'ready' ? (
        <MasterDetail
          listLabel="Saved threads"
          items={listState.conversations}
          keyOf={(conversation) => conversation.id}
          labelOf={(conversation) =>
            `${TYPE_LABELS[conversation.conversation_type]} ${conversation.id}: ${
              conversation.preview || 'no preview'
            }`
          }
          selectedKey={selectedId}
          onSelect={selectConversation}
          emptyList={
            <EmptyState
              icon={<MessagesSquare aria-hidden="true" />}
              title="No threads yet"
              description="Ask a question about this course, or start a tutoring session. Both are kept here."
              headingLevel="h3"
            />
          }
          renderItem={(conversation) => (
            <>
              <Badge
                tone={conversation.conversation_type === 'ai_tutor' ? 'accent' : 'neutral'}
                icon={
                  conversation.conversation_type === 'ai_tutor' ? (
                    <Bot aria-hidden="true" />
                  ) : (
                    <MessageCircle aria-hidden="true" />
                  )
                }
              >
                {TYPE_LABELS[conversation.conversation_type]}
              </Badge>
              <span className={styles.preview}>
                {conversation.preview || `Thread ${conversation.id}`}
              </span>
              <span className={styles.meta}>
                {conversation.message_count} message{conversation.message_count === 1 ? '' : 's'} ·{' '}
                {formatDate(conversation.updated_at)}
              </span>
            </>
          )}
          detail={
            <>
              {detailState.phase === 'empty' ? (
                <DetailPlaceholder
                  title="Pick a thread"
                  body="Read the exchange before you pick it back up."
                />
              ) : null}
              {detailState.phase === 'loading' ? <DetailLoading label="Opening" /> : null}
              {detailState.phase === 'error' ? <DetailError message={detailState.message} /> : null}
              {detailState.phase === 'ready' ? (
                <>
                  <div className={styles.detailHead}>
                    <h3 className={styles.detailTitle}>
                      {TYPE_LABELS[detailState.conversation.conversation_type]}
                    </h3>
                    {canResume ? (
                      <Button variant="primary" onClick={() => onResume(detailState.conversation)}>
                        Pick this up
                      </Button>
                    ) : (
                      <span className={styles.meta}>You can read this thread, but not continue it.</span>
                    )}
                  </div>

                  <ol className={styles.messages}>
                    {detailState.conversation.messages.map((message) => (
                      <li
                        key={message.id}
                        className={cx(
                          styles.message,
                          message.role === 'user' ? styles.fromUser : styles.fromAssistant,
                        )}
                      >
                        <span className={styles.author}>
                          {message.role === 'user' ? (
                            <UserRound aria-hidden="true" />
                          ) : (
                            <Bot aria-hidden="true" />
                          )}
                          {message.role === 'user' ? 'You' : 'Lumina'}
                        </span>
                        <p className={styles.body}>{message.content}</p>
                      </li>
                    ))}
                  </ol>
                </>
              ) : null}
            </>
          }
        />
      ) : null}
    </Dialog>
  );
}
