import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(join(process.cwd(), 'src', 'styles', 'tokens.css'), 'utf8');

function blockAfter(marker: string): string {
  const start = source.indexOf(marker);
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(open + 1, index);
    }
  }
  throw new Error(`unterminated ${marker}`);
}

function tokensIn(block: string): Record<string, string> {
  const found: Record<string, string> = {};
  for (const hit of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    found[hit[1]] = hit[2].trim();
  }
  return found;
}

function channel(value: number): number {
  const ratio = value / 255;
  return ratio <= 0.03928 ? ratio / 12.92 : ((ratio + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  const clean = hex.replace('#', '');
  const full =
    clean.length === 3
      ? clean
          .split('')
          .map((part) => part + part)
          .join('')
      : clean;
  const red = Number.parseInt(full.slice(0, 2), 16);
  const green = Number.parseInt(full.slice(2, 4), 16);
  const blue = Number.parseInt(full.slice(4, 6), 16);
  return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue);
}

function contrast(foreground: string, background: string): number {
  const a = luminance(foreground);
  const b = luminance(background);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

const light = tokensIn(blockAfter(':root'));
const dark = tokensIn(blockAfter(":root[data-theme='dark']"));

const BODY_TEXT: [string, string][] = [
  ['--text', '--surface'],
  ['--text', '--surface-raised'],
  ['--text', '--surface-sunken'],
  ['--text-muted', '--surface'],
  ['--text-muted', '--surface-raised'],
  ['--text-on-accent', '--accent'],
  ['--accent', '--surface'],
  ['--accent', '--surface-raised'],
  ['--success', '--success-subtle'],
  ['--warning', '--warning-subtle'],
  ['--destructive', '--destructive-subtle'],
  ['--processing', '--processing-subtle'],
  ['--info', '--info-subtle'],
];

const SUPPORTING_TEXT: [string, string][] = [
  ['--text-subtle', '--surface'],
  ['--text-subtle', '--surface-raised'],
];

describe.each([
  ['light', light],
  ['dark', dark],
])('%s theme contrast', (_name, palette) => {
  it.each(BODY_TEXT)('%s on %s clears 4.5:1', (foreground, background) => {
    const ratio = contrast(palette[foreground], palette[background]);
    expect(Number(ratio.toFixed(2))).toBeGreaterThanOrEqual(4.5);
  });

  it.each(SUPPORTING_TEXT)('%s on %s clears 3:1', (foreground, background) => {
    const ratio = contrast(palette[foreground], palette[background]);
    expect(Number(ratio.toFixed(2))).toBeGreaterThanOrEqual(3);
  });

  it('keeps warning and processing far enough apart to read as different states', () => {
    expect(palette['--warning']).not.toBe(palette['--processing']);
    const separation = Math.abs(
      luminance(palette['--warning']) - luminance(palette['--processing']),
    );
    expect(separation + contrast(palette['--warning'], palette['--processing'])).toBeGreaterThan(1);
  });
});
