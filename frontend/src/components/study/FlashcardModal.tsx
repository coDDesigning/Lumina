import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Layers,
  RotateCw,
  Shuffle,
  Sparkles,
  XCircle,
} from 'lucide-react';
import {
  describeGenerationError,
  isAbortError,
  isInsufficientCredits,
} from '../../api/errors';
import { useCredits } from '../../context/CreditContext';
import CreditBalance from '../credits/CreditBalance';
import CreditExhaustedNotice from '../credits/CreditExhaustedNotice';
import { flashcardsAPI } from '../../api/flashcards';
import type { FlashcardGenerationResult, GeneratedFlashcard } from '../../api/types';
import { Dialog } from '@/ui/Dialog';
import './study.css';

interface FlashcardModalProps {
  courseId: number;
  courseName?: string;
  readyDocumentCount: number;
  onClose: () => void;
}

type FlashcardState =
  | { phase: 'idle' }
  | { phase: 'generating' }
  | { phase: 'success'; result: FlashcardGenerationResult }
  | { phase: 'error'; message: string; retryable: boolean };

export function FlashcardModal({
  courseId,
  courseName,
  readyDocumentCount,
  onClose,
}: FlashcardModalProps) {
  const [state, setState] = useState<FlashcardState>({ phase: 'idle' });
  const [includeProfileContext, setIncludeProfileContext] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isFlipped, setIsFlipped] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [cards, setCards] = useState<GeneratedFlashcard[]>([]);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    if (state.phase !== 'generating') return;
    setElapsed(0);
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [state.phase]);

  const { refresh, canAfford, isMetered } = useCredits();
  const exhausted = isMetered && !canAfford('flashcard');

  const handleGenerate = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setState({ phase: 'generating' });
    setIsFlipped(false);
    setCurrentIndex(0);

    try {
      const result = await flashcardsAPI.generate(
        courseId,
        {
          use_profile_knowledge: includeProfileContext,
          include_profile_context: includeProfileContext,
        },
        { signal: controller.signal },
      );
      setCards(result.flashcards.flashcards);
      setState({ phase: 'success', result });
      void refresh();
    } catch (err) {
      if (isAbortError(err)) return;
      const parsed = describeGenerationError(err, 'flashcard');
      if (isInsufficientCredits(parsed)) {
        await refresh();
        setState({ phase: 'idle' });
        return;
      }
      setState({
        phase: 'error',
        message: parsed.message,
        retryable: parsed.retryable,
      });
    }
  }, [courseId, includeProfileContext, refresh]);

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
    if (state.phase !== 'success' || cards.length === 0) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === ' ' || event.key === 'Enter') {
        event.preventDefault();
        handleFlip();
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        handleNext();
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault();
        handlePrev();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [state.phase, cards.length, handleFlip, handleNext, handlePrev]);

  const currentCard = cards[currentIndex];
  const progressPercent =
    cards.length > 0 ? ((currentIndex + 1) / cards.length) * 100 : 0;

  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title="Flashcards"
      description={courseName}
      mark={<Layers aria-hidden="true" />}
    >

        <div className="study-modal-body">
          {state.phase === 'idle' && (
            <div className="study-idle-state" style={{ textAlign: 'center', padding: '32px 16px' }}>
              <div style={{ display: 'inline-flex', padding: '16px', background: '#f5f3ff', borderRadius: '50%', marginBottom: '16px', color: '#8b5cf6' }}>
                <BookOpen size={40} />
              </div>
              <h3 style={{ fontSize: '20px', fontWeight: 700, margin: '0 0 8px 0', color: '#1e1b4b' }}>
                Ready to Study with Flashcards?
              </h3>
              <p style={{ color: '#6b7280', maxWidth: '440px', margin: '0 auto 24px auto', lineHeight: 1.5 }}>
                Generate an intelligent set of interactive flashcards based directly on your course materials.
              </p>
              {readyDocumentCount === 0 ? (
                <div style={{ padding: '12px', background: '#fffbeb', color: '#b45309', borderRadius: '8px', marginBottom: '16px' }}>
                  ⚠️ No ready course materials found. Please upload and process documents first.
                </div>
              ) : (
                <div className="study-toggle-group" style={{ margin: '16px auto 24px auto', maxWidth: '440px', textAlign: 'left' }}>
                  <label className="study-toggle-label" style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '8px' }}>
                    <input
                      type="checkbox"
                      checked={includeProfileContext}
                      onChange={(event) => setIncludeProfileContext(event.target.checked)}
                    />
                    <span>Include personal study profile context</span>
                  </label>
                  <p className="study-toggle-caption" style={{ margin: '4px 0 0 24px' }}>
                    Includes your profile background as supplementary context. Course material remains primary and authoritative.
                  </p>
                </div>
              )}
              {exhausted ? (
                <CreditExhaustedNotice source="flashcard" action="flashcards" />
              ) : null}
              <CreditBalance source="flashcard" />
              <button
                type="button"
                className="study-primary-btn"
                onClick={handleGenerate}
                disabled={readyDocumentCount === 0 || exhausted}
              >
                <Sparkles aria-hidden="true" />
                Generate Flashcards
              </button>
            </div>
          )}

          {state.phase === 'generating' && (
            <div className="study-loading-state">
              <div className="study-pulse-spinner" />
              <h3>Synthesizing Key Concepts…</h3>
              <p>Analyzing course documents and generating interactive flashcard cards.</p>
              <div className="study-elapsed-badge">⏱️ {elapsed}s</div>
            </div>
          )}

          {state.phase === 'error' && (
            <div className="study-error-state" style={{ textAlign: 'center', padding: '32px 16px' }}>
              <XCircle size={48} color="#ef4444" style={{ margin: '0 auto 16px auto' }} />
              <h3 style={{ color: '#991b1b', margin: '0 0 8px 0' }}>Generation Failed</h3>
              <p style={{ color: '#6b7280', marginBottom: '24px' }}>{state.message}</p>
              <div style={{ display: 'flex', gap: '12px', justifyContent: 'center' }}>
                {state.retryable && (
                  <button type="button" className="study-primary-btn" onClick={handleGenerate}>
                    <RotateCw aria-hidden="true" />
                    Try Again
                  </button>
                )}
                <button type="button" className="study-secondary-btn" onClick={onClose}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {state.phase === 'success' && currentCard && (
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
                <span><kbd>Esc</kbd> Close</span>
              </div>
            </div>
          )}
        </div>
    </Dialog>
  );
}

export default FlashcardModal;
