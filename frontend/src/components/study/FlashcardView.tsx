import { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, CheckCircle2, RotateCw, Shuffle } from 'lucide-react';
import type { GeneratedFlashcard } from '../../api/types';
import './study.css';

interface FlashcardViewProps {
  initialCards: GeneratedFlashcard[];
  onEscape?: () => void;
}

export function FlashcardView({ initialCards, onEscape }: FlashcardViewProps) {
  const [cards, setCards] = useState<GeneratedFlashcard[]>(initialCards);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);

  useEffect(() => {
    setCards(initialCards);
    setCurrentIndex(0);
    setIsFlipped(false);
  }, [initialCards]);

  const handleFlip = useCallback(() => {
    setIsFlipped((prev) => !prev);
  }, []);

  const handleNext = useCallback(() => {
    if (currentIndex < cards.length - 1) {
      setIsFlipped(false);
      setCurrentIndex((prev) => prev + 1);
    }
  }, [currentIndex, cards.length]);

  const handlePrev = useCallback(() => {
    if (currentIndex > 0) {
      setIsFlipped(false);
      setCurrentIndex((prev) => prev - 1);
    }
  }, [currentIndex]);

  const handleShuffle = useCallback(() => {
    setIsFlipped(false);
    setCurrentIndex(0);
    setCards((prev) => [...prev].sort(() => Math.random() - 0.5));
  }, []);

  // Keyboard navigation
  useEffect(() => {
    if (cards.length === 0) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === ' ' || event.key === 'Enter') {
        // Prevent default only if we're not focused on something else like a button
        if (event.target instanceof HTMLButtonElement) return;
        event.preventDefault();
        handleFlip();
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        handleNext();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        handlePrev();
      } else if (event.key === 'Escape') {
        onEscape?.();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [cards.length, handleFlip, handleNext, handlePrev, onEscape]);

  const currentCard = cards[currentIndex];
  if (!currentCard) return null;

  const progressPercent = cards.length > 0 ? ((currentIndex + 1) / cards.length) * 100 : 0;

  return (
    <div className="flashcard-deck-view">
      <div className="flashcard-progress-bar">
        <div
          className="flashcard-progress-fill"
          style={{ width: `${progressPercent}%` }}
        />
      </div>

      <div className="flashcard-meta-bar">
        <span>
          Card {currentIndex + 1} of {cards.length}
        </span>
        <span
          className={`flashcard-difficulty-badge ${currentCard.difficulty.toLowerCase()}`}
        >
          {currentCard.difficulty}
        </span>
      </div>

      <div
        className="flashcard-scene"
        onClick={handleFlip}
        role="button"
        tabIndex={0}
        aria-label={`Flashcard: ${isFlipped ? 'Answer' : 'Question'}. Click to flip.`}
      >
        <div className={`flashcard-3d ${isFlipped ? 'is-flipped' : ''}`}>
          {/* Front Side */}
          <div className="flashcard-face flashcard-front">
            <div className="flashcard-tag">Concept / Question</div>
            <div className="flashcard-main-content">
              <p>{currentCard.front}</p>
            </div>
            <div className="flashcard-hint">
              <RotateCw size={14} /> Click or Space to reveal answer
            </div>
          </div>

          {/* Back Side */}
          <div className="flashcard-face flashcard-back">
            <div className="flashcard-tag">Explanation / Answer</div>
            <div className="flashcard-main-content">
              <p>{currentCard.back}</p>
            </div>
            <div className="flashcard-hint">
              <CheckCircle2 size={14} /> Click or Space to flip back
            </div>
          </div>
        </div>
      </div>

      <div className="flashcard-controls">
        <button
          type="button"
          className="flashcard-nav-btn"
          onClick={handlePrev}
          disabled={currentIndex === 0}
          aria-label="Previous card"
        >
          <ArrowLeft size={16} /> Previous
        </button>

        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            type="button"
            className="flashcard-nav-btn"
            onClick={handleShuffle}
            title="Shuffle deck"
          >
            <Shuffle size={16} />
          </button>
          <button
            type="button"
            className="flashcard-flip-btn"
            onClick={handleFlip}
          >
            <RotateCw size={16} style={{ display: 'inline', marginRight: '6px' }} />
            {isFlipped ? 'Show Front' : 'Flip Card'}
          </button>
        </div>

        <button
          type="button"
          className="flashcard-nav-btn"
          onClick={handleNext}
          disabled={currentIndex === cards.length - 1}
          aria-label="Next card"
        >
          Next <ArrowRight size={16} />
        </button>
      </div>

      <div className="flashcard-shortcuts">
        <span><kbd>Space</kbd> Flip</span>
        <span><kbd>←</kbd> <kbd>→</kbd> Navigate</span>
        {onEscape && <span><kbd>Esc</kbd> Close</span>}
      </div>
    </div>
  );
}
