import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TopicSuggestions } from './TopicSuggestions';

const defaultProps = {
  headingId: 'test-suggestions',
  title: 'Found suggestions',
  lede: 'Some suggestions lede.',
  declared: [] as string[],
};

describe('TopicSuggestions', () => {
  it('renders nothing when there is nothing to suggest', () => {
    const { container } = render(
      <TopicSuggestions {...defaultProps} suggestions={[]} onAdd={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing while disabled, even with suggestions to offer', () => {
    const { container } = render(
      <TopicSuggestions {...defaultProps} suggestions={['Hashing']} onAdd={vi.fn()} disabled />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('shows the given title and lede', () => {
    render(<TopicSuggestions {...defaultProps} suggestions={['Hashing']} onAdd={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'Found suggestions' })).toBeInTheDocument();
    expect(screen.getByText('Some suggestions lede.')).toBeInTheDocument();
  });

  it('offers a suggestion without adding it on its own', () => {
    const onAdd = vi.fn();
    render(
      <TopicSuggestions
        {...defaultProps}
        suggestions={['Hashing', 'Sorting']}
        onAdd={onAdd}
      />,
    );

    expect(screen.getByRole('button', { name: 'Hashing' })).toBeInTheDocument();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it('adds one when it is picked', async () => {
    const onAdd = vi.fn();
    const user = userEvent.setup();
    render(
      <TopicSuggestions
        {...defaultProps}
        suggestions={['Hashing', 'Sorting']}
        onAdd={onAdd}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Hashing' }));
    expect(onAdd).toHaveBeenCalledWith(['Hashing']);
  });

  it('adds every one at once when asked', async () => {
    const onAdd = vi.fn();
    const user = userEvent.setup();
    render(
      <TopicSuggestions
        {...defaultProps}
        suggestions={['Hashing', 'Sorting']}
        onAdd={onAdd}
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Add all 2' }));
    expect(onAdd).toHaveBeenCalledWith(['Hashing', 'Sorting']);
  });

  it('does not offer an "add all" button for a single suggestion', () => {
    render(<TopicSuggestions {...defaultProps} suggestions={['Hashing']} onAdd={vi.fn()} />);
    expect(screen.queryByRole('button', { name: /Add all/ })).not.toBeInTheDocument();
  });

  it('hides a suggestion already declared, case-insensitively', () => {
    render(
      <TopicSuggestions
        {...defaultProps}
        suggestions={['hashing', 'Sorting']}
        declared={['Hashing']}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.queryByRole('button', { name: 'hashing' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sorting' })).toBeInTheDocument();
  });

  it('collapses duplicate suggestions and drops blank entries', () => {
    render(
      <TopicSuggestions
        {...defaultProps}
        suggestions={['Hashing', 'hashing', '   ', 'Sorting']}
        onAdd={vi.fn()}
      />,
    );
    expect(screen.getAllByRole('button', { name: /^Hashing$/i })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Add all 2' })).toBeInTheDocument();
  });

  it('says nothing once every suggestion is already declared', () => {
    const { container } = render(
      <TopicSuggestions
        {...defaultProps}
        suggestions={['Hashing']}
        declared={['hashing']}
        onAdd={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
