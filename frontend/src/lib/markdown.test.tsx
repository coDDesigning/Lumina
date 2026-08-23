import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Markdown } from './markdown';

describe('Markdown', () => {
  it('reads a plain answer as a paragraph', () => {
    render(<Markdown text="Breadth-first search explores level by level." />);

    expect(
      screen.getByText('Breadth-first search explores level by level.'),
    ).toBeInTheDocument();
  });

  it('sets bold and italic without printing the markers', () => {
    const { container } = render(<Markdown text="This is **important** and *subtle*." />);

    expect(container.querySelector('strong')).toHaveTextContent('important');
    expect(container.querySelector('em')).toHaveTextContent('subtle');
    expect(container.textContent).not.toContain('**');
    expect(container.textContent).not.toContain('*subtle*');
  });

  it('renders a bullet list as a list', () => {
    render(<Markdown text={'- First point\n- Second point\n- Third point'} />);

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveTextContent('First point');
  });

  it('renders a numbered list as an ordered list', () => {
    const { container } = render(<Markdown text={'1. Start here\n2. Then this'} />);

    expect(container.querySelector('ol')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('keeps a fenced code block intact, markers and all', () => {
    const { container } = render(
      <Markdown text={'Try this:\n```python\nfor i in range(3):\n    print(i)\n```'} />,
    );

    const block = container.querySelector('pre');
    expect(block).toBeInTheDocument();
    expect(block?.textContent).toBe('for i in range(3):\n    print(i)');
    expect(container.textContent).not.toContain('```');
  });

  it('leaves markdown characters alone inside code', () => {
    const { container } = render(<Markdown text="Use `a ** b` for powers." />);

    expect(container.querySelector('code')).toHaveTextContent('a ** b');
    expect(container.querySelector('strong')).toBeNull();
  });

  it('renders headings as headings rather than hashes', () => {
    render(<Markdown text={'## What to revise\n\nStart with traversals.'} />);

    expect(screen.getByRole('heading', { name: 'What to revise' })).toBeInTheDocument();
    expect(screen.queryByText(/^##/)).toBeNull();
  });

  it('turns a link into a link that opens away from the app', () => {
    render(<Markdown text="See [the notes](https://example.com/notes) for more." />);

    const link = screen.getByRole('link', { name: 'the notes' });
    expect(link).toHaveAttribute('href', 'https://example.com/notes');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('refuses to make a script URL clickable', () => {
    const { container } = render(
      <Markdown text="[click me](javascript:alert(document.cookie))" />,
    );

    expect(container.querySelector('a')).toBeNull();
    expect(screen.getByText('click me')).toBeInTheDocument();
  });

  it('never builds markup out of the model’s own angle brackets', () => {
    const { container } = render(
      <Markdown text={'Compare <script>alert(1)</script> with <b>bold</b>.'} />,
    );

    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(container.textContent).toContain('<script>alert(1)</script>');
  });

  it('keeps a quote as a quote', () => {
    const { container } = render(<Markdown text={'> The lecturer stressed this.'} />);

    expect(container.querySelector('blockquote')).toHaveTextContent(
      'The lecturer stressed this.',
    );
  });

  it('joins a soft-wrapped sentence back into one line', () => {
    const { container } = render(
      <Markdown text={'Finishing times give a\nreverse topological order.\n\nA new one'} />,
    );

    const paragraphs = container.querySelectorAll('p');
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0].querySelector('br')).toBeNull();
    expect(paragraphs[0].textContent).toBe('Finishing times give a reverse topological order.');
    expect(paragraphs[1]).toHaveTextContent('A new one');
  });

  it('breaks where the writer asked for a break', () => {
    const { container } = render(<Markdown text={'Line one  \nLine two'} />);

    expect(container.querySelector('br')).toBeInTheDocument();
    expect(container.textContent).toBe('Line oneLine two');
  });

  it('handles a list that follows a paragraph with no blank line', () => {
    render(<Markdown text={'Here is why:\n- Reason one\n- Reason two'} />);

    expect(screen.getByText('Here is why:')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
  });

  it('renders bold inside a list item', () => {
    render(<Markdown text={'- **Merge sort** is stable'} />);

    const item = screen.getByRole('listitem');
    expect(within(item).getByText('Merge sort').tagName).toBe('STRONG');
  });

  it('produces nothing at all for an empty answer', () => {
    const { container } = render(<Markdown text="" />);

    expect(container.textContent).toBe('');
  });

  it.each([
    ['c++', 'int main(){}'],
    ['c#', 'class A {}'],
    ['objective-c', '@interface A @end'],
    ['js title="example.js"', 'const a = 1;'],
    ['', 'plain'],
  ])('renders a fence whose info string is %s without hanging', (info, body) => {
    const { container } = render(
      <Markdown text={'Here:\n```' + info + '\n' + body + '\n```'} />,
    );

    expect(container.querySelector('pre')?.textContent).toBe(body);
    expect(container.textContent).not.toContain('```');
  });

  it('ends even when a fence is never closed', () => {
    const { container } = render(<Markdown text={'Here:\n```c++\nint main(){}'} />);

    expect(container.querySelector('pre')?.textContent).toBe('int main(){}');
  });

  it('leaves a snake_case identifier exactly as written', () => {
    const { container } = render(
      <Markdown text="Tell no_relevant_material and material_not_indexed apart." />,
    );

    expect(container.querySelector('em')).toBeNull();
    expect(container.textContent).toBe(
      'Tell no_relevant_material and material_not_indexed apart.',
    );
  });

  it('still italicises a word wrapped in underscores on its own', () => {
    const { container } = render(<Markdown text="This is _subtle_ emphasis." />);

    expect(container.querySelector('em')).toHaveTextContent('subtle');
  });

  it('leaves a snake_case identifier alone inside a bold run too', () => {
    const { container } = render(<Markdown text="Use __graded_count__ not total_questions." />);

    expect(container.querySelector('strong')).toHaveTextContent('graded_count');
    expect(container.textContent).toContain('total_questions');
  });
});
