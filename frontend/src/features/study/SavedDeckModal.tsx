import { useEffect, useState } from 'react';
import { Layers } from 'lucide-react';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
import { queryKeys } from '@/api/queryKeys';
import { useQuery } from '@/lib/query/useQuery';
import type { FlashcardGenerationResponse, GeneratedOutputDetail } from '@/api/types';
import { Alert } from '@/ui/Alert';
import { Button } from '@/ui/Button';
import { Dialog } from '@/ui/Dialog';
import { Skeleton } from '@/ui/Skeleton';
import { FlashcardDeck } from './FlashcardDeck';
import { extractFlashcards, tryParseJson } from './storedOutput';

export interface SavedDeckModalProps {
  courseId: number;
  outputId: number;
  courseName?: string;
  onClose: () => void;
}

type State =
  | { phase: 'loading' }
  | { phase: 'ready'; deck: FlashcardGenerationResponse }
  | { phase: 'unreadable'; output: GeneratedOutputDetail }
  | { phase: 'error'; message: string };

export function SavedDeckModal({ courseId, outputId, courseName, onClose }: SavedDeckModalProps) {
  const [state, setState] = useState<State>({ phase: 'loading' });

  const query = useQuery<GeneratedOutputDetail>({
    key: queryKeys.courseOutput(courseId, outputId),
    fetcher: ({ signal }) => generatedOutputsAPI.get(courseId, outputId, { signal }),
    fallbackMessage: 'This deck could not be opened.',
    staleTime: 5 * 60_000,
  });

  useEffect(() => {
    if (query.status === 'pending' || query.status === 'idle') {
      setState({ phase: 'loading' });
      return;
    }
    if (query.status === 'error') {
      setState({
        phase: 'error',
        message: query.error?.message ?? 'This deck could not be opened.',
      });
      return;
    }
    const output = query.data;
    if (!output) {
      return;
    }
    const cards = extractFlashcards(output.content);
    if (!cards) {
      setState({ phase: 'unreadable', output });
      return;
    }
    const parsed = typeof output.content === 'string' ? tryParseJson(output.content) : output.content;
    const candidate = typeof parsed === 'object' && parsed !== null ? (parsed as Record<string, unknown>) : null;
    const deckTitle = typeof candidate?.deck_title === 'string' ? candidate.deck_title : 'Flashcards';
    setState({
      phase: 'ready',
      deck: { deck_title: deckTitle, card_count: cards.length, flashcards: cards },
    });
  }, [query.status, query.data, query.error]);

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title={state.phase === 'ready' ? state.deck.deck_title : 'Flashcards'}
      description={courseName}
      mark={<Layers aria-hidden="true" />}
      footer={<Button onClick={onClose}>Done</Button>}
    >
      {state.phase === 'loading' ? <Skeleton variant="block" /> : null}

      {state.phase === 'error' ? (
        <Alert tone="destructive" live="alert">
          {state.message}
        </Alert>
      ) : null}

      {state.phase === 'unreadable' ? (
        <Alert tone="warning" title="This deck was saved in an older shape">
          It is kept exactly as it was written, but this version cannot lay it out.
        </Alert>
      ) : null}

      {state.phase === 'ready' ? (
        <FlashcardDeck cards={state.deck.flashcards} onEscape={onClose} />
      ) : null}
    </Dialog>
  );
}
