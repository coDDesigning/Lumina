import { useEffect, useState } from 'react';
import { Layers } from 'lucide-react';
import { describeError, isAbortError } from '@/api/errors';
import { generatedOutputsAPI } from '@/api/generatedOutputs';
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

  useEffect(() => {
    const controller = new AbortController();

    generatedOutputsAPI
      .get(courseId, outputId, { signal: controller.signal })
      .then((output) => {
        if (controller.signal.aborted) {
          return;
        }
        const cards = extractFlashcards(output.content);
        if (cards) {
          const parsed =
            typeof output.content === 'string'
              ? tryParseJson(output.content)
              : output.content;
          const candidate =
            typeof parsed === 'object' && parsed !== null
              ? (parsed as Record<string, unknown>)
              : null;
          const deckTitle =
            typeof candidate?.deck_title === 'string'
              ? candidate.deck_title
              : 'Flashcards';
          setState({
            phase: 'ready',
            deck: {
              deck_title: deckTitle,
              card_count: cards.length,
              flashcards: cards,
            },
          });
          return;
        }
        setState({ phase: 'unreadable', output });
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted || isAbortError(caught)) {
          return;
        }
        setState({
          phase: 'error',
          message: describeError(caught, 'This deck could not be opened.').message,
        });
      });

    return () => controller.abort();
  }, [courseId, outputId]);

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
