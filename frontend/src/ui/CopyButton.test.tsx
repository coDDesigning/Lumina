import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CopyButton } from './CopyButton';

function mockClipboard(fn = vi.fn().mockResolvedValue(undefined)) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: fn },
    configurable: true,
    writable: true,
  });
  return fn;
}

describe('CopyButton', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders with an accessible label', () => {
    mockClipboard();
    render(<CopyButton text="Test response" />);
    const button = screen.getByRole('button', { name: 'Copy response' });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute('type', 'button');
  });

  it('copies the exact text to clipboard and shows copied feedback', async () => {
    const writeText = mockClipboard();
    render(<CopyButton text="Full AI response with **markdown**." />);

    const button = screen.getByRole('button', { name: 'Copy response' });
    await userEvent.click(button);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(writeText).toHaveBeenCalledWith('Full AI response with **markdown**.');

    expect(await screen.findByRole('button', { name: 'Copied to clipboard' })).toBeInTheDocument();
  });

  it('reverts back to the default label after timer duration', async () => {
    vi.useFakeTimers();
    mockClipboard();

    render(<CopyButton text="Temporary feedback test" />);

    const button = screen.getByRole('button', { name: 'Copy response' });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(screen.getByRole('button', { name: 'Copied to clipboard' })).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(2100);
    });

    expect(screen.getByRole('button', { name: 'Copy response' })).toBeInTheDocument();
  });

  it('handles clipboard rejection gracefully without crashing', async () => {
    const writeText = mockClipboard(vi.fn().mockRejectedValue(new Error('Permission denied')));

    render(<CopyButton text="Failed copy attempt" />);

    const button = screen.getByRole('button', { name: 'Copy response' });
    await userEvent.click(button);

    expect(writeText).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Copy response' })).toBeInTheDocument();
  });

  it('supports custom label and copiedLabel', () => {
    mockClipboard();
    render(<CopyButton text="Custom labels" label="Copy answer" copiedLabel="Answer copied" />);
    expect(screen.getByRole('button', { name: 'Copy answer' })).toBeInTheDocument();
  });
});
