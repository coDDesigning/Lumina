import { useCallback, useEffect, useMemo, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { ArrowLeft, ArrowRight, RotateCw, Shuffle } from 'lucide-react';
import type { GeneratedFlashcard } from '@/api/types';
import { cx } from '@/lib/cx';
import { Badge } from '@/ui/Badge';
import type { BadgeTone } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { IconButton } from '@/ui/IconButton';
import { shuffle } from './shuffle';
import styles from './FlashcardDeck.module.css';

export interface FlashcardDeckProps {
  cards: GeneratedFlashcard[];
  onEscape?: () => void;
}

const DIFFICULTY_TONE: Record<GeneratedFlashcard['difficulty'], BadgeTone> = {
  Easy: 'success',
  Medium: 'warning',
  Hard: 'destructive',
};

export function FlashcardDeck({ cards, onEscape }: FlashcardDeckProps) {
  const [order, setOrder] = useState<GeneratedFlashcard[]>(cards);
  const [index, setIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);

  useEffect(() => {
    setOrder(cards);
    setIndex(0);
    setIsFlipped(false);
  }, [cards]);

  const flip = useCallback(() => setIsFlipped((previous) => !previous), []);

  const goTo = useCallback((next: number) => {
    setIsFlipped(false);
    setIndex(next);
  }, []);

  const handleShuffle = useCallback(() => {
    setOrder((previous) => shuffle(previous));
    setIsFlipped(false);
  }, []);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && onEscape) {
      onEscape();
      return;
    }
    if (event.key === 'ArrowRight' && index < order.length - 1) {
      event.preventDefault();
      goTo(index + 1);
      return;
    }
    if (event.key === 'ArrowLeft' && index > 0) {
      event.preventDefault();
      goTo(index - 1);
    }
  };

  const card = order[index];

  const announcement = useMemo(() => {
    if (!card) {
      return '';
    }
    const position = `Card ${index + 1} of ${order.length}.`;
    return isFlipped ? `${position} Answer. ${card.back}` : `${position} Question. ${card.front}`;
  }, [card, index, isFlipped, order.length]);

  if (!card) {
    return null;
  }

  const isFirst = index === 0;
  const isLast = index === order.length - 1;

  return (
    <div className={styles.deck} onKeyDown={handleKeyDown}>
      <div className={styles.rail} aria-hidden="true">
        <div className={styles.railFill} style={{ width: `${((index + 1) / order.length) * 100}%` }} />
      </div>

      <div className={styles.meta}>
        <span className={styles.position}>
          Card <span className="tabular">{index + 1}</span> of{' '}
          <span className="tabular">{order.length}</span>
        </span>
        <Badge tone={DIFFICULTY_TONE[card.difficulty]}>{card.difficulty}</Badge>
      </div>

      <button
        type="button"
        className={styles.scene}
        onClick={flip}
        aria-label={isFlipped ? 'Show the question' : 'Show the answer'}
      >
        <span className={cx(styles.card, isFlipped && styles.flipped)}>
          <span className={cx(styles.face, styles.front)} aria-hidden={isFlipped}>
            <span className={styles.faceLabel}>Question</span>
            <span className={styles.faceBody}>{card.front}</span>
          </span>
          <span className={cx(styles.face, styles.back)} aria-hidden={!isFlipped}>
            <span className={styles.faceLabel}>Answer</span>
            <span className={styles.faceBody}>{card.back}</span>
          </span>
        </span>
      </button>

      <p className="visually-hidden" aria-live="polite">
        {announcement}
      </p>

      <div className={styles.controls}>
        <Button
          variant="ghost"
          onClick={() => {
            if (!isFirst) {
              goTo(index - 1);
            }
          }}
          aria-disabled={isFirst || undefined}
          icon={<ArrowLeft aria-hidden="true" />}
        >
          Previous
        </Button>

        <div className={styles.centreControls}>
          <IconButton
            label="Shuffle the deck"
            icon={<Shuffle aria-hidden="true" />}
            onClick={handleShuffle}
          />
          <Button variant="secondary" onClick={flip} icon={<RotateCw aria-hidden="true" />}>
            {isFlipped ? 'Show question' : 'Show answer'}
          </Button>
        </div>

        <Button
          variant="ghost"
          onClick={() => {
            if (!isLast) {
              goTo(index + 1);
            }
          }}
          aria-disabled={isLast || undefined}
          iconAfter={<ArrowRight aria-hidden="true" />}
        >
          Next
        </Button>
      </div>

      <p className={styles.shortcuts}>
        <span>
          <kbd>Space</kbd> flips
        </span>
        <span>
          <kbd>←</kbd> <kbd>→</kbd> move between cards
        </span>
        {onEscape ? (
          <span>
            <kbd>Esc</kbd> closes
          </span>
        ) : null}
      </p>
    </div>
  );
}
