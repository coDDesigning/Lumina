import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Trash2 } from 'lucide-react';
import { courseHue } from '@/lib/courseLight';
import { Badge } from './Badge';
import { Button } from './Button';
import { ConfirmDialog } from './ConfirmDialog';
import { Dialog } from './Dialog';
import { IconButton } from './IconButton';
import { Input, Select, Textarea } from './Input';
import { Checkbox, Switch } from './Checkbox';
import { Tabs } from './Tabs';
import { ToastProvider } from './ToastProvider';
import { useToast } from './toastContext';

describe('Button', () => {
  it('disables itself and marks busy while loading', () => {
    render(
      <Button isLoading loadingLabel="Generating">
        Make the guide
      </Button>,
    );

    const button = screen.getByRole('button', { name: /make the guide/i });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('Generating')).toBeInTheDocument();
  });

  it('does not fire onClick while loading', async () => {
    const onClick = vi.fn();
    render(
      <Button isLoading onClick={onClick}>
        Save
      </Button>,
    );

    await userEvent.click(screen.getByRole('button'), { pointerEventsCheck: 0 });
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe('IconButton', () => {
  it('always has an accessible name', () => {
    render(<IconButton label="Remove source" icon={<Trash2 aria-hidden="true" />} />);
    expect(screen.getByRole('button', { name: 'Remove source' })).toBeInTheDocument();
  });
});

describe('Badge', () => {
  it('renders its label so status is never carried by colour alone', () => {
    render(<Badge tone="destructive">Couldn&apos;t read it</Badge>);
    expect(screen.getByText("Couldn't read it")).toBeInTheDocument();
  });
});

describe('form controls', () => {
  it('associates label, hint and error with the input', () => {
    render(
      <Input
        label="Course title"
        hint="Shown on the course card."
        error="Enter a title."
        defaultValue=""
      />,
    );

    const input = screen.getByLabelText('Course title');
    expect(input).toHaveAttribute('aria-invalid', 'true');

    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    const ids = describedBy!.split(' ');
    expect(ids).toHaveLength(2);
    ids.forEach((id) => {
      expect(document.getElementById(id)).toBeInTheDocument();
    });
    expect(screen.getByText('Enter a title.')).toBeInTheDocument();
    expect(screen.getByText('Shown on the course card.')).toBeInTheDocument();
  });

  it('leaves aria-invalid off when there is no error', () => {
    render(<Input label="Term" defaultValue="Fall 2026" />);
    expect(screen.getByLabelText('Term')).not.toHaveAttribute('aria-invalid');
  });

  it('labels textareas and selects the same way', () => {
    render(
      <>
        <Textarea label="Your answer" defaultValue="" />
        <Select label="Difficulty" defaultValue="medium">
          <option value="medium">Medium</option>
        </Select>
      </>,
    );

    expect(screen.getByLabelText('Your answer').tagName).toBe('TEXTAREA');
    expect(screen.getByLabelText('Difficulty').tagName).toBe('SELECT');
  });

  it('labels checkboxes and switches, and exposes a switch role', () => {
    render(
      <>
        <Checkbox label="Also use my profile background" />
        <Switch label="Exam focused" />
      </>,
    );

    expect(screen.getByRole('checkbox', { name: /profile background/i })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Exam focused' })).toBeInTheDocument();
  });
});

/**
 * The inline `onClose` here is deliberate: it changes identity on every parent
 * render, which is what the focus trap must tolerate.
 */
function DialogHarness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open dialog
      </button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
          onClose?.();
        }}
        title="Delete this course?"
        description="This cannot be undone."
        footer={<Button variant="destructive">Delete permanently</Button>}
      >
        <Input label="Reason" value={reason} onChange={(event) => setReason(event.target.value)} />
      </Dialog>
    </>
  );
}

describe('Dialog', () => {
  it('renders nothing while closed', () => {
    render(<DialogHarness />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('is a modal labelled by its title and described by its description', async () => {
    render(<DialogHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open dialog' }));

    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleName('Delete this course?');
    expect(dialog).toHaveAccessibleDescription('This cannot be undone.');
  });

  it('moves focus onto the first control inside the dialog, not the panel', async () => {
    render(<DialogHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open dialog' }));

    const close = screen.getByRole('button', { name: 'Close' });
    await waitFor(() => {
      expect(document.activeElement).toBe(close);
    });
  });

  it('closes on Escape and restores focus to the trigger', async () => {
    const onClose = vi.fn();
    render(<DialogHarness onClose={onClose} />);

    const trigger = screen.getByRole('button', { name: 'Open dialog' });
    await userEvent.click(trigger);
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    await userEvent.keyboard('{Escape}');

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(onClose).toHaveBeenCalled();
    await waitFor(() => {
      expect(document.activeElement).toBe(trigger);
    });
  });

  it('cycles Tab through the dialog controls and wraps at the end', async () => {
    render(<DialogHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open dialog' }));

    const close = screen.getByRole('button', { name: 'Close' });
    const reason = screen.getByLabelText('Reason');
    const confirm = screen.getByRole('button', { name: 'Delete permanently' });

    expect(document.activeElement).toBe(close);

    await userEvent.tab();
    expect(document.activeElement).toBe(reason);

    await userEvent.tab();
    expect(document.activeElement).toBe(confirm);

    await userEvent.tab();
    expect(document.activeElement).toBe(close);
  });

  it('wraps backwards from the first control to the last', async () => {
    render(<DialogHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open dialog' }));

    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Close' }));

    await userEvent.tab({ shift: true });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Delete permanently' }));
  });

  it('does not steal focus back when the parent re-renders mid-typing', async () => {
    render(<DialogHarness />);
    await userEvent.click(screen.getByRole('button', { name: 'Open dialog' }));

    const reason = screen.getByLabelText('Reason');
    await userEvent.click(reason);
    await userEvent.type(reason, 'no longer taking this course');

    expect(reason).toHaveValue('no longer taking this course');
    expect(document.activeElement).toBe(reason);
  });
});

describe('ConfirmDialog', () => {
  it('keeps the confirm action disabled until the phrase is typed exactly', async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        onClose={vi.fn()}
        onConfirm={onConfirm}
        title="Delete CS 3410?"
        confirmLabel="Delete permanently"
        confirmPhrase="CS 3410"
      />,
    );

    const confirm = screen.getByRole('button', { name: 'Delete permanently' });
    expect(confirm).toBeDisabled();

    const input = screen.getByLabelText('Type CS 3410 to confirm');
    await userEvent.type(input, 'CS 34');
    expect(confirm).toBeDisabled();

    await userEvent.type(input, '10');
    expect(confirm).toBeEnabled();

    await userEvent.click(confirm);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('confirms without a phrase when none is required', async () => {
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        onClose={vi.fn()}
        onConfirm={onConfirm}
        title="Remove this source?"
        confirmLabel="Remove"
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

describe('Tabs', () => {
  it('moves selection with the arrow keys', async () => {
    function Harness() {
      const [value, setValue] = useState<'ask' | 'tutor'>('ask');
      return (
        <Tabs
          label="Conversation type"
          value={value}
          onChange={setValue}
          options={[
            { value: 'ask', label: 'Ask' },
            { value: 'tutor', label: 'Tutor' },
          ]}
        />
      );
    }

    render(<Harness />);
    const ask = screen.getByRole('tab', { name: 'Ask' });
    expect(ask).toHaveAttribute('aria-selected', 'true');

    ask.focus();
    await userEvent.keyboard('{ArrowRight}');

    expect(screen.getByRole('tab', { name: 'Tutor' })).toHaveAttribute('aria-selected', 'true');
    expect(ask).toHaveAttribute('aria-selected', 'false');
  });
});

describe('ToastProvider', () => {
  it('announces an error toast and lets it be dismissed', async () => {
    function Harness() {
      const { showToast } = useToast();
      return (
        <button
          type="button"
          onClick={() => showToast({ tone: 'error', title: 'Upload failed', message: 'Try again.' })}
        >
          Trigger
        </button>
      );
    }

    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Trigger' }));

    const toast = screen.getByRole('alert');
    expect(toast).toHaveTextContent('Upload failed');
    expect(toast).toHaveTextContent('Try again.');

    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('courseHue', () => {
  it('is stable for the same course and stays within the hue circle', () => {
    expect(courseHue(12)).toBe(courseHue(12));
    for (let id = 0; id < 40; id += 1) {
      const hue = courseHue(id);
      expect(hue).toBeGreaterThanOrEqual(0);
      expect(hue).toBeLessThan(360);
    }
  });

  it('gives adjacent courses different hues', () => {
    expect(courseHue(1)).not.toBe(courseHue(2));
  });

  it('handles string ids without collapsing to one hue', () => {
    const hues = new Set(['cs3410', 'math2210', 'phil1120', 'bio1010'].map(courseHue));
    expect(hues.size).toBeGreaterThan(1);
  });
});
