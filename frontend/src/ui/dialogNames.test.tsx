import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Button } from './Button';
import { Dialog } from './Dialog';

function namesInDialog(): string[] {
  const dialog = screen.getByRole('dialog');
  return Array.from(dialog.querySelectorAll('button')).map(
    (button) => button.getAttribute('aria-label') ?? button.textContent?.trim() ?? '',
  );
}

function duplicatesIn(names: string[]): string[] {
  const seen = new Set<string>();
  const repeated = new Set<string>();
  for (const name of names) {
    if (seen.has(name)) {
      repeated.add(name);
    }
    seen.add(name);
  }
  return Array.from(repeated);
}

describe('dialog control names', () => {
  it('finds two controls that answer to the same name', () => {
    render(
      <Dialog open onClose={vi.fn()} title="Anything" footer={<Button>Close</Button>}>
        <p>Body</p>
      </Dialog>,
    );

    expect(duplicatesIn(namesInDialog())).toEqual(['Close']);
  });

  it('passes a dialog whose footer does not repeat the dismiss control', () => {
    render(
      <Dialog
        open
        onClose={vi.fn()}
        title="Anything"
        footer={
          <>
            <Button>Not now</Button>
            <Button variant="primary">Do it</Button>
          </>
        }
      >
        <p>Body</p>
      </Dialog>,
    );

    expect(duplicatesIn(namesInDialog())).toEqual([]);
  });
});
