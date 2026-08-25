import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { GeneratedFlashcard } from '@/api/types';
import { FlashcardDeck } from './FlashcardDeck';

const CARDS: GeneratedFlashcard[] = [
  { card_number: 1, front: 'What is a stack?', back: 'Last in, first out.', difficulty: 'Easy' },
  { card_number: 2, front: 'What is a queue?', back: 'First in, first out.', difficulty: 'Medium' },
  { card_number: 3, front: 'What is a heap?', back: 'A partly ordered tree.', difficulty: 'Hard' },
];

describe('FlashcardDeck', () => {
  it('opens on the question and names what the card will do', () => {
    render(<FlashcardDeck cards={CARDS} />);

    expect(screen.getByText('What is a stack?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show the answer' })).toBeInTheDocument();
  });

  it('reveals the answer when the card is activated from the keyboard', async () => {
    render(<FlashcardDeck cards={CARDS} />);

    const card = screen.getByRole('button', { name: 'Show the answer' });
    card.focus();
    await userEvent.keyboard(' ');

    expect(screen.getByRole('button', { name: 'Show the question' })).toBeInTheDocument();
  });

  it('hides the face that is turned away from assistive technology', async () => {
    render(<FlashcardDeck cards={CARDS} />);

    expect(screen.getByText('Last in, first out.').closest('[aria-hidden]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );

    await userEvent.click(screen.getByRole('button', { name: 'Show the answer' }));

    expect(screen.getByText('What is a stack?').closest('[aria-hidden]')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
  });

  it('announces the card that is showing, and its content', async () => {
    render(<FlashcardDeck cards={CARDS} />);

    const live = document.querySelector('[aria-live="polite"]');
    expect(live).toHaveTextContent('Card 1 of 3. Question. What is a stack?');

    await userEvent.click(screen.getByRole('button', { name: 'Show the answer' }));

    expect(live).toHaveTextContent('Card 1 of 3. Answer. Last in, first out.');
  });

  it('moves between cards with the arrow keys and turns the next one face down', async () => {
    render(<FlashcardDeck cards={CARDS} />);

    await userEvent.click(screen.getByRole('button', { name: 'Show the answer' }));
    expect(screen.getByRole('button', { name: 'Show the question' })).toBeInTheDocument();

    await userEvent.keyboard('{ArrowRight}');

    expect(screen.getByText('What is a queue?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show the answer' })).toBeInTheDocument();
  });

  it('stops at both ends of the deck', async () => {
    render(<FlashcardDeck cards={CARDS} />);

    expect(screen.getByRole('button', { name: /previous/i })).toBeDisabled();

    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    await userEvent.click(screen.getByRole('button', { name: /next/i }));

    expect(screen.getByText('What is a heap?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
  });

  it('leaves the space bar alone when the reader is typing somewhere else', async () => {
    render(
      <div>
        <FlashcardDeck cards={CARDS} />
        <textarea aria-label="Notes" />
      </div>,
    );

    await userEvent.type(screen.getByLabelText('Notes'), 'a stack is');

    expect(screen.getByLabelText('Notes')).toHaveValue('a stack is');
    expect(screen.getByRole('button', { name: 'Show the answer' })).toBeInTheDocument();
  });

  it('closes on Escape only when the caller can act on it', async () => {
    let closed = 0;
    const { unmount } = render(<FlashcardDeck cards={CARDS} onEscape={() => (closed += 1)} />);

    screen.getByRole('button', { name: 'Show the answer' }).focus();
    await userEvent.keyboard('{Escape}');
    expect(closed).toBe(1);

    unmount();
    render(<FlashcardDeck cards={CARDS} />);
    screen.getByRole('button', { name: 'Show the answer' }).focus();
    await userEvent.keyboard('{Escape}');
    expect(closed).toBe(1);
  });

  it('shuffles the deck while preserving the active card index', async () => {
    render(<FlashcardDeck cards={CARDS} />);

    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(screen.getByText('What is a queue?')).toBeInTheDocument();
    expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(/^Card 2 of 3\./);

    await userEvent.click(screen.getByRole('button', { name: 'Shuffle the deck' }));

    expect(document.querySelector('[aria-live="polite"]')).toHaveTextContent(/^Card 2 of 3\./);
  });
});
