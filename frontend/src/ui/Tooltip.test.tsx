import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { Tooltip } from './Tooltip';

function renderTooltip() {
  return render(
    <>
      <Tooltip content="9 of 12 figures described">Partial visuals</Tooltip>
      <button type="button">Elsewhere</button>
    </>,
  );
}

describe('Tooltip', () => {
  it('describes its trigger with the tooltip text before it is ever opened', () => {
    renderTooltip();

    expect(screen.getByRole('button', { name: 'Partial visuals' })).toHaveAccessibleDescription(
      '9 of 12 figures described',
    );
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('opens while hovered and stays open when the pointer moves onto it', async () => {
    const user = userEvent.setup();
    renderTooltip();

    await user.hover(screen.getByRole('button', { name: 'Partial visuals' }));
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveTextContent('9 of 12 figures described');

    await user.hover(tooltip);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    await user.unhover(tooltip);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('opens on keyboard focus and closes when focus moves on', async () => {
    const user = userEvent.setup();
    renderTooltip();

    await user.tab();
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    await user.tab();
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('closes on Escape while hovered, without the pointer moving', async () => {
    const user = userEvent.setup();
    renderTooltip();

    await user.hover(screen.getByRole('button', { name: 'Partial visuals' }));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('opens on a tap and closes on a tap elsewhere', () => {
    renderTooltip();

    fireEvent.click(screen.getByRole('button', { name: 'Partial visuals' }));
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});
