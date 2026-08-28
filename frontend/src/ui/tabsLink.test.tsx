import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { Tabs } from './Tabs';

type Mode = 'ask' | 'tutor';

const OPTIONS = [
  { value: 'ask' as const, label: 'Ask' },
  { value: 'tutor' as const, label: 'Tutor' },
];

const LINK = { to: '/courses/1/exam-mode', label: 'Exam Mode' };

/** Controlled, the way the workspace uses it, so arrow keys really advance. */
function Harness({ onChange, withLink = true }: { onChange?: (v: Mode) => void; withLink?: boolean }) {
  const [value, setValue] = useState<Mode>('ask');
  return (
    <MemoryRouter>
      <Tabs
        label="Conversation type"
        options={OPTIONS}
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange?.(next);
        }}
        link={withLink ? LINK : undefined}
      />
    </MemoryRouter>
  );
}

describe('a segmented control holding a destination', () => {
  it('offers the destination as a link, never as a tab', () => {
    // aria-selected promises a panel swapping in place. A control that moves
    // the reader to another page must not make that promise.
    render(<Harness />);

    expect(screen.getByRole('link', { name: /Exam Mode/ })).toHaveAttribute(
      'href',
      '/courses/1/exam-mode',
    );
    expect(screen.queryByRole('tab', { name: /Exam Mode/ })).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(2);
  });

  it('keeps the destination out of the tabs the arrow keys walk', async () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Ask' }));
    onChange.mockClear();

    await user.keyboard('{ArrowRight}');
    await user.keyboard('{ArrowRight}');

    // Two tabs, so two presses wrap back to the first rather than stepping
    // onto the link.
    expect(onChange.mock.calls.map(([value]) => value)).toEqual(['tutor', 'ask']);
    expect(screen.getByRole('link', { name: /Exam Mode/ })).not.toHaveFocus();
  });

  it('reaches the destination by Tab, like any other link', async () => {
    render(<Harness />);
    const user = userEvent.setup();

    await user.click(screen.getByRole('tab', { name: 'Ask' }));
    await user.tab();

    expect(screen.getByRole('link', { name: /Exam Mode/ })).toHaveFocus();
  });

  it('renders no destination when none is given', () => {
    render(<Harness withLink={false} />);

    expect(screen.queryByRole('link')).toBeNull();
  });
});
