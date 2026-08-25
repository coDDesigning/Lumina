import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Button } from './Button';
import { Input } from './Input';

function Form({ onSave, guard }: { onSave: () => Promise<void>; guard: boolean }) {
  const [isSaving, setIsSaving] = useState(false);
  const [value, setValue] = useState('');

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (guard && isSaving) {
      return;
    }
    setIsSaving(true);
    await onSave();
    setIsSaving(false);
  }

  return (
    <form onSubmit={handleSubmit}>
      <Input label="Name" value={value} onChange={(event) => setValue(event.target.value)} />
      <Button type="submit" isLoading={isSaving} loadingLabel="Saving">
        Save
      </Button>
    </form>
  );
}

function pending() {
  let release!: () => void;
  const save = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        release = resolve;
      }),
  );
  return { save, release: () => release() };
}

describe('a busy submit button', () => {
  it('keeps focus instead of dropping it to the body', async () => {
    const user = userEvent.setup();
    const { save, release } = pending();
    render(<Form onSave={save} guard />);

    const button = screen.getByRole('button', { name: 'Save' });
    button.focus();
    await user.click(button);

    expect(save).toHaveBeenCalledTimes(1);
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(button).toHaveAttribute('aria-disabled', 'true');
    expect(button).not.toBeDisabled();
    expect(button).toHaveFocus();
    expect(document.activeElement).not.toBe(document.body);

    release();
  });

  it('refuses a second click while it works', async () => {
    const user = userEvent.setup();
    const { save, release } = pending();
    render(<Form onSave={save} guard />);

    const button = screen.getByRole('button', { name: 'Save' });
    await user.click(button);
    await user.click(button);
    await user.click(button);

    expect(save).toHaveBeenCalledTimes(1);
    release();
  });

  it('refuses the Enter key while it works, even with no guard in the handler', async () => {
    const user = userEvent.setup();
    const { save, release } = pending();
    render(<Form onSave={save} guard={false} />);

    const field = screen.getByLabelText('Name');
    await user.type(field, 'Operating Systems');

    await user.keyboard('{Enter}');
    expect(save).toHaveBeenCalledTimes(1);

    await user.keyboard('{Enter}');
    await user.keyboard('{Enter}');
    expect(save).toHaveBeenCalledTimes(1);

    release();
  });
});
