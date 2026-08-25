import { useState } from 'react';
import { Bot, History, MessageCircle, MessagesSquare, Trash2, UserRound } from 'lucide-react';
import { conversationsAPI } from '@/api/conversations';
import { queryKeys } from '@/api/queryKeys';
import { queryCache } from '@/lib/query/cache';
import { useQuery } from '@/lib/query/useQuery';
import { describeError } from '@/api/errors';
import type { ConversationDetail, ConversationSummary, ConversationType } from '@/api/types';
import { cx } from '@/lib/cx';
import { Markdown } from '@/lib/markdown';
import { Alert } from '@/ui/Alert';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { EmptyState } from '@/ui/EmptyState';
import { ConfirmDialog } from '@/ui/ConfirmDialog';
import { DetailError, DetailLoading, DetailPlaceholder, MasterDetail } from '@/ui/MasterDetail';
import styles from './ConversationHistoryModal.module.css';

export interface ConversationHistoryModalProps {
  courseId: number;
  courseName: string;
  canResume?: boolean;
  onClose: () => void;
  onResume: (conversation: ConversationDetail) => void;
  onDelete?: (conversationId: number) => void;
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
  onDelete,
}: ConversationHistoryModalProps) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [removing, setRemoving] = useState<ConversationSummary | null>(null);
  const [isRemoving, setIsRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  const listQuery = useQuery<ConversationSummary[]>({
    key: queryKeys.courseConversations(courseId),
    fetcher: ({ signal }) => conversationsAPI.list(courseId, { signal }),
    fallbackMessage: 'Your past threads could not be loaded.',
  });

  const detailQuery = useQuery<ConversationDetail>({
    key: selectedId === null ? null : queryKeys.courseConversation(courseId, selectedId),
    fetcher: ({ signal }) => conversationsAPI.get(courseId, selectedId as number, { signal }),
    fallbackMessage: 'This thread could not be opened.',
    staleTime: 60_000,
  });

  const listState: ListState =
    listQuery.status === 'error'
      ? { phase: 'error', message: listQuery.error?.message ?? 'Your past threads could not be loaded.' }
      : listQuery.data
        ? { phase: 'ready', conversations: listQuery.data }
        : { phase: 'loading' };

  const detailState: DetailState =
    selectedId === null
      ? { phase: 'empty' }
      : detailQuery.status === 'error'
        ? { phase: 'error', message: detailQuery.error?.message ?? 'This thread could not be opened.' }
        : detailQuery.data
          ? { phase: 'ready', conversation: detailQuery.data }
          : { phase: 'loading' };

  const selectConversation = (conversation: ConversationSummary) => {
    setSelectedId(conversation.id);
  };

  async function handleRemove() {
    if (!removing) {
      return;
    }
    const removedId = removing.id;
    setIsRemoving(true);
    setRemoveError(null);
    try {
      await conversationsAPI.delete(courseId, removedId);
      onDelete?.(removedId);
      queryCache.setData<ConversationSummary[]>(
        queryKeys.courseConversations(courseId),
        (previous) => previous?.filter((row) => row.id !== removedId),
      );
      queryCache.remove(queryKeys.courseConversation(courseId, removedId));
      if (selectedId === removedId) {
        setSelectedId(null);
      }
      setRemoving(null);
    } catch (caught) {
      setRemoveError(describeError(caught, 'That thread could not be removed.').message);
    } finally {
      setIsRemoving(false);
    }
  }

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
                      <div className={styles.detailActions}>
                        <Button
                          variant="ghost"
                          icon={<Trash2 aria-hidden="true" />}
                          onClick={() => {
                            const summary =
                              listState.phase === 'ready'
                                ? listState.conversations.find(
                                    (row) => row.id === detailState.conversation.id,
                                  )
                                : undefined;
                            setRemoving(summary ?? null);
                          }}
                        >
                          Remove
                        </Button>
                        <Button variant="primary" onClick={() => onResume(detailState.conversation)}>
                          Pick this up
                        </Button>
                      </div>
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
                        {message.role === 'assistant' ? (
                          <Markdown className={styles.body} text={message.content} />
                        ) : (
                          <p className={styles.body}>{message.content}</p>
                        )}
                      </li>
                    ))}
                  </ol>
                </>
              ) : null}
            </>
          }
        />
      ) : null}

      <ConfirmDialog
        open={removing !== null}
        onClose={() => {
          setRemoving(null);
          setRemoveError(null);
        }}
        onConfirm={() => void handleRemove()}
        title="Remove this thread?"
        description="The exchange is deleted for good. Nothing you generated from this course is affected."
        confirmLabel="Remove it"
        pendingLabel="Removing"
        isPending={isRemoving}
        destructive
      >
        {removeError ? (
          <Alert tone="destructive" live="alert">
            {removeError}
          </Alert>
        ) : null}
      </ConfirmDialog>
    </Dialog>
  );
}
