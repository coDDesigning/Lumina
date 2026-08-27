import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TagInput } from './TagInput';

function Harness({ initial = [] as string[] }: { initial?: string[] }) {
  const [values, setValues] = useState<string[]>(initial);
  return <TagInput label="Topics" value={values} onChange={setValues} />;
}

describe('TagInput', () => {
  it('labels the text field so the control has an accessible name', () => {
    render(<Harness />);

    expect(screen.getByRole('textbox', { name: /topics/i })).toBeInTheDocument();
  });

  it('adds a value when Enter is pressed and clears the field', async () => {
    render(<Harness />);
    const field = screen.getByRole('textbox', { name: /topics/i });

    await userEvent.type(field, 'Graph Traversals{Enter}');

    expect(screen.getByText('Graph Traversals')).toBeInTheDocument();
    expect(field).toHaveValue('');
  });

  it('keeps a comma inside a single value instead of splitting on it', async () => {
    const onChange = vi.fn();
    render(<TagInput label="Topics" value={[]} onChange={onChange} />);

    await userEvent.type(
      screen.getByRole('textbox', { name: /topics/i }),
      'Trees, Heaps and Priority Queues{Enter}',
    );

    expect(onChange).toHaveBeenCalledWith(['Trees, Heaps and Priority Queues']);
  });

  it('removes a value through a control that names the value it removes', async () => {
    render(<Harness initial={['Graphs', 'Trees']} />);

    await userEvent.click(screen.getByRole('button', { name: 'Remove Graphs' }));

    expect(screen.queryByText('Graphs')).not.toBeInTheDocument();
    expect(screen.getByText('Trees')).toBeInTheDocument();
  });

  it('removes the last value when Backspace is pressed in an empty field', async () => {
    render(<Harness initial={['Graphs', 'Trees']} />);

    await userEvent.type(screen.getByRole('textbox', { name: /topics/i }), '{Backspace}');

    expect(screen.getByText('Graphs')).toBeInTheDocument();
    expect(screen.queryByText('Trees')).not.toBeInTheDocument();
  });

  it('leaves the values alone when Backspace is pressed with text in the field', async () => {
    render(<Harness initial={['Graphs']} />);
    const field = screen.getByRole('textbox', { name: /topics/i });

    await userEvent.type(field, 'Tre{Backspace}');

    expect(screen.getByText('Graphs')).toBeInTheDocument();
    expect(field).toHaveValue('Tr');
  });

  it('ignores a blank entry', async () => {
    const onChange = vi.fn();
    render(<TagInput label="Topics" value={[]} onChange={onChange} />);

    await userEvent.type(screen.getByRole('textbox', { name: /topics/i }), '   {Enter}');

    expect(onChange).not.toHaveBeenCalled();
  });

  it('trims a value before adding it', async () => {
    const onChange = vi.fn();
    render(<TagInput label="Topics" value={[]} onChange={onChange} />);

    await userEvent.type(
      screen.getByRole('textbox', { name: /topics/i }),
      '  Graphs  {Enter}',
    );

    expect(onChange).toHaveBeenCalledWith(['Graphs']);
  });

  it('refuses a duplicate that differs only by casing', async () => {
    const onChange = vi.fn();
    render(<TagInput label="Topics" value={['Graphs']} onChange={onChange} />);

    await userEvent.type(screen.getByRole('textbox', { name: /topics/i }), 'graphs{Enter}');

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/already added/i)).toBeInTheDocument();
  });

  it('announces what was added and removed', async () => {
    render(<Harness initial={['Graphs']} />);

    await userEvent.type(screen.getByRole('textbox', { name: /topics/i }), 'Trees{Enter}');
    expect(screen.getByRole('status')).toHaveTextContent('Trees added');

    await userEvent.click(screen.getByRole('button', { name: 'Remove Graphs' }));
    expect(screen.getByRole('status')).toHaveTextContent('Graphs removed');
  });

  it('reports a value that exceeds the maximum length instead of adding it', async () => {
    const onChange = vi.fn();
    render(<TagInput label="Topics" value={[]} onChange={onChange} maxLength={10} />);

    await userEvent.type(
      screen.getByRole('textbox', { name: /topics/i }),
      'ThisIsFarTooLong{Enter}',
    );

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/10 characters/i)).toBeInTheDocument();
  });

  it('stops accepting values once the maximum count is reached', async () => {
    const onChange = vi.fn();
    render(<TagInput label="Topics" value={['Graphs']} onChange={onChange} maxItems={1} />);

    await userEvent.type(screen.getByRole('textbox', { name: /topics/i }), 'Trees{Enter}');

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/at most 1/i)).toBeInTheDocument();
  });

  it('adds nothing and shows no remove control when disabled', async () => {
    const onChange = vi.fn();
    render(
      <TagInput label="Topics" value={['Graphs']} onChange={onChange} disabled />,
    );

    expect(screen.getByRole('textbox', { name: /topics/i })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Remove Graphs' })).not.toBeInTheDocument();
  });
});
