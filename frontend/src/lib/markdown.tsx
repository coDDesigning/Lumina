import type { ReactNode } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import type { Citation } from '@/api/types';
import { citationsByKey } from '@/features/study/citations';
import { CitationChip } from '@/ui/CitationChip';
import { cx } from './cx';
import styles from './markdown.module.css';

export interface MarkdownProps {
  text: string;
  className?: string;
  citations?: Citation[];
}

type CitationIndex = Map<string, Citation> | undefined;

const SAFE_LINK = /^(https?:\/\/|mailto:)/i;

// Models routinely typeset with LaTeX: `$x$` / `\(x\)` inline, `$$…$$` / `\[…\]`
// for a display block. KaTeX turns the source into markup; `throwOnError: false`
// makes a malformed expression render as the offending text in the error colour
// rather than throwing.
function TeX({ tex, display = false }: { tex: string; display?: boolean }): ReactNode {
  const trimmed = tex.trim();
  if (!trimmed) {
    return <>{display ? '$$$$' : '$$'}</>;
  }
  let html: string;
  try {
    html = katex.renderToString(trimmed, {
      displayMode: display,
      throwOnError: false,
      errorColor: 'currentColor',
      output: 'htmlAndMathml',
      strict: false,
    });
  } catch {
    // Only reached if KaTeX itself throws despite throwOnError; keep the source.
    return <span className={styles.mathError}>{display ? `$$${trimmed}$$` : `$${trimmed}$`}</span>;
  }
  return (
    <span
      className={display ? styles.mathBlock : styles.mathInline}
      // KaTeX output is a typesetting result we generate, not caller HTML.
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

// A `$$…$$` or `\[…\]` block, possibly spanning several lines. Returns the TeX
// source and the line after the closing delimiter, or null when the opener has
// no matching close within a sane distance (then it is left as plain text).
function readDisplayMath(lines: string[], start: number): { tex: string; next: number } | null {
  const opener = lines[start].trimStart();
  const [open, close] = opener.startsWith('$$')
    ? ['$$', '$$']
    : opener.startsWith('\\[')
      ? ['\\[', '\\]']
      : ['', ''];
  if (!open) {
    return null;
  }

  const first = opener.slice(open.length);
  const limit = Math.min(lines.length, start + 40);

  const onOneLine = first.trimEnd().endsWith(close) && first.trim().length > close.length;
  if (onOneLine) {
    return { tex: first.trimEnd().slice(0, -close.length), next: start + 1 };
  }

  const body: string[] = first ? [first] : [];
  for (let cursor = start + 1; cursor < limit; cursor += 1) {
    const line = lines[cursor];
    if (line.trimEnd().endsWith(close)) {
      body.push(line.trimEnd().slice(0, -close.length));
      return { tex: body.join('\n'), next: cursor + 1 };
    }
    body.push(line);
  }
  return null;
}

function inline(text: string, keyPrefix: string, citations?: CitationIndex): ReactNode[] {
  const nodes: ReactNode[] = [];
  let rest = text;
  let index = 0;

  // Code spans are matched first so that markers inside them stay literal. Math
  // delimiters come next so a `*` or `_` inside an expression is never read as
  // emphasis. `$…$` requires a non-space just inside each delimiter, which keeps
  // prices ("$5 and $10 more") from being mistaken for math.
  const pattern =
    /(`[^`\n]+`)|(\$\$(?:\\.|[^\n])+?\$\$)|(\\\[(?:\\.|[^\n])+?\\\])|(\\\((?:\\.|[^\n])+?\\\))|(\$(?![\s$])(?:\\.|[^$\n])*?[^\s$]\$)|(\*\*[^*\n]+\*\*)|((?<![A-Za-z0-9])__(?:(?!__)[^\n])+__(?![A-Za-z0-9]))|(\*[^*\n]+\*)|((?<![A-Za-z0-9])_[^_\n]+_(?![A-Za-z0-9]))|(\[[^\]\n]*\]\([^)\s]+\))|(\[S\d{1,3}\])/;

  while (rest.length > 0) {
    const hit = pattern.exec(rest);
    if (!hit || hit.index === undefined) {
      nodes.push(rest);
      break;
    }

    if (hit.index > 0) {
      nodes.push(rest.slice(0, hit.index));
    }

    const token = hit[0];
    const key = `${keyPrefix}-${index}`;
    index += 1;

    const citationKey = /^\[(S\d{1,3})\]$/.exec(token);
    if (citationKey) {
      // A key the backend did not supply stays literal text rather than
      // rendering as a source the student cannot trust.
      const citation = citations?.get(citationKey[1]);
      nodes.push(
        citation ? <CitationChip citation={citation} key={key} /> : <span key={key}>{token}</span>,
      );
    } else if (token.startsWith('`')) {
      nodes.push(
        <code className={styles.code} key={key}>
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('$$')) {
      nodes.push(<TeX display key={key} tex={token.slice(2, -2)} />);
    } else if (token.startsWith('\\[')) {
      nodes.push(<TeX display key={key} tex={token.slice(2, -2)} />);
    } else if (token.startsWith('\\(')) {
      nodes.push(<TeX key={key} tex={token.slice(2, -2)} />);
    } else if (token.startsWith('$')) {
      nodes.push(<TeX key={key} tex={token.slice(1, -1)} />);
    } else if (token.startsWith('**') || token.startsWith('__')) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('[')) {
      const split = token.indexOf('](');
      const label = token.slice(1, split);
      const href = token.slice(split + 2, -1);
      nodes.push(
        SAFE_LINK.test(href) ? (
          <a className={styles.link} href={href} key={key} rel="noreferrer noopener" target="_blank">
            {label || href}
          </a>
        ) : (
          <span key={key}>{label || href}</span>
        ),
      );
    } else {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    }

    rest = rest.slice(hit.index + token.length);
  }

  return nodes;
}

// A single newline inside a paragraph is a soft wrap, not a line break — a model that
// wraps its prose at some width should not produce ragged breaks on screen. Two trailing
// spaces still force a break, which is how markdown says to ask for one.
function withBreaks(lines: string[], keyPrefix: string, citations?: CitationIndex): ReactNode[] {
  const nodes: ReactNode[] = [];

  lines.forEach((line, position) => {
    const text = line.trimEnd();

    if (position > 0) {
      const previousForcedBreak = /\s{2,}$/.test(lines[position - 1]);
      if (previousForcedBreak) {
        nodes.push(<br key={`${keyPrefix}-br-${position}`} />);
      } else {
        nodes.push(' ');
      }
    }

    nodes.push(...inline(text, `${keyPrefix}-${position}`, citations));
  });

  return nodes;
}

export function Markdown({ text, className, citations }: MarkdownProps) {
  const index = citations && citations.length > 0 ? citationsByKey(citations) : undefined;
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];

  let cursor = 0;
  let key = 0;

  const nextKey = () => `b${(key += 1)}`;

  while (cursor < lines.length) {
    const startedAt = cursor;
    const line = lines[cursor];

    if (line.trim() === '') {
      cursor += 1;
      continue;
    }

    const fence = /^\s*```/.test(line);
    if (fence) {
      const body: string[] = [];
      cursor += 1;
      while (cursor < lines.length && !/^\s*```\s*$/.test(lines[cursor])) {
        body.push(lines[cursor]);
        cursor += 1;
      }
      cursor += 1;
      blocks.push(
        <pre className={styles.pre} key={nextKey()} tabIndex={0}>
          <code>{body.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    // A display equation standing on its own — `$$…$$` or `\[…\]`, one line or
    // several. An unterminated opener falls through to normal text handling.
    if (/^\s*(\$\$|\\\[)/.test(line)) {
      const math = readDisplayMath(lines, cursor);
      if (math) {
        blocks.push(
          <div className={styles.mathDisplay} key={nextKey()} tabIndex={0}>
            <TeX display tex={math.tex} />
          </div>,
        );
        cursor = math.next;
        continue;
      }
    }

    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const depth = heading[1].length;
      const content = inline(heading[2], nextKey(), index);
      blocks.push(
        depth <= 2 ? (
          <h3 className={styles.heading} key={nextKey()}>
            {content}
          </h3>
        ) : (
          <h4 className={styles.subheading} key={nextKey()}>
            {content}
          </h4>
        ),
      );
      cursor += 1;
      continue;
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push(<hr className={styles.rule} key={nextKey()} />);
      cursor += 1;
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const body: string[] = [];
      while (cursor < lines.length && /^\s*>\s?/.test(lines[cursor])) {
        body.push(lines[cursor].replace(/^\s*>\s?/, ''));
        cursor += 1;
      }
      blocks.push(
        <blockquote className={styles.quote} key={nextKey()}>
          {withBreaks(body, nextKey(), index)}
        </blockquote>,
      );
      continue;
    }

    const bullet = /^\s*[-*+]\s+/;
    const numbered = /^\s*\d+[.)]\s+/;

    if (bullet.test(line) || numbered.test(line)) {
      const ordered = numbered.test(line);
      const marker = ordered ? numbered : bullet;
      const items: string[] = [];
      while (cursor < lines.length && marker.test(lines[cursor])) {
        items.push(lines[cursor].replace(marker, ''));
        cursor += 1;
      }
      const rendered = items.map((item, position) => (
        <li key={`${position}-${item.slice(0, 12)}`}>{inline(item, `li${position}`, index)}</li>
      ));
      blocks.push(
        ordered ? (
          <ol className={styles.ordered} key={nextKey()}>
            {rendered}
          </ol>
        ) : (
          <ul className={styles.unordered} key={nextKey()}>
            {rendered}
          </ul>
        ),
      );
      continue;
    }

    const paragraph: string[] = [];
    while (
      cursor < lines.length &&
      lines[cursor].trim() !== '' &&
      !/^\s*```/.test(lines[cursor]) &&
      !/^#{1,4}\s/.test(lines[cursor]) &&
      !/^\s*>\s?/.test(lines[cursor]) &&
      !bullet.test(lines[cursor]) &&
      !numbered.test(lines[cursor]) &&
      // A later line that opens a display equation starts its own block, even
      // with no blank line before it.
      !(cursor > startedAt && /^\s*(\$\$|\\\[)/.test(lines[cursor]))
    ) {
      paragraph.push(lines[cursor]);
      cursor += 1;
    }
    if (paragraph.length > 0) {
      blocks.push(
        <p className={styles.paragraph} key={nextKey()}>
          {withBreaks(paragraph, nextKey(), index)}
        </p>,
      );
    }

    if (cursor === startedAt) {
      cursor += 1;
    }
  }

  return <div className={cx(styles.prose, className)}>{blocks}</div>;
}
