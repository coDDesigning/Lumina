import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

interface Exemption {
  file: string;
  value: string;
  reason: string;
}

export const DOCUMENTED_EXTERNAL_VALUES: Exemption[] = [];

function key(path: string): string {
  return relative(SRC, path).split(sep).join('/');
}

const NAMED_COLOURS = [
  'white',
  'black',
  'red',
  'green',
  'blue',
  'yellow',
  'orange',
  'purple',
  'pink',
  'brown',
  'gray',
  'grey',
  'silver',
  'gold',
  'cyan',
  'magenta',
  'lime',
  'navy',
  'teal',
  'olive',
  'maroon',
  'aqua',
  'fuchsia',
  'indigo',
  'violet',
  'beige',
  'ivory',
  'khaki',
  'salmon',
  'coral',
  'crimson',
  'tomato',
];

const HEX = /#[0-9a-fA-F]{3,8}\b/g;
const ENCODED_HEX = /%23[0-9a-fA-F]{3,8}\b/g;
const COLOUR_FUNCTION = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g;
const NAMED = new RegExp(String.raw`:\s*(?:` + NAMED_COLOURS.join('|') + String.raw`)\s*(?:;|!|$)`, 'gim');

function collect(dir: string, match: (name: string) => boolean, found: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      collect(path, match, found);
    } else if (match(entry.name)) {
      found.push(path);
    }
  }
  return found;
}

function withoutComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

function isExempt(file: string, value: string): boolean {
  return DOCUMENTED_EXTERNAL_VALUES.some(
    (entry) => entry.file === file && entry.value === value,
  );
}

function literalsIn(file: string, source: string): string[] {
  const clean = withoutComments(source);
  const found: string[] = [];

  for (const hit of clean.matchAll(HEX)) {
    found.push(hit[0]);
  }
  for (const hit of clean.matchAll(ENCODED_HEX)) {
    found.push(hit[0]);
  }
  for (const hit of clean.matchAll(COLOUR_FUNCTION)) {
    if (!hit[1].includes('var(--')) {
      found.push(hit[0]);
    }
  }
  for (const hit of clean.matchAll(NAMED)) {
    found.push(hit[0].trim());
  }

  return found.filter((value) => !isExempt(file, value));
}

const modules = collect(SRC, (name) => name.endsWith('.module.css'));
const scripts = collect(
  SRC,
  (name) =>
    (name.endsWith('.ts') || name.endsWith('.tsx')) &&
    !name.endsWith('.test.ts') &&
    !name.endsWith('.test.tsx'),
);

describe('raw visual values', () => {
  it('finds the files to check', () => {
    expect(modules.length).toBeGreaterThan(10);
    expect(scripts.length).toBeGreaterThan(10);
  });

  it.each([...modules, ...scripts].map((path) => [key(path), path]))(
    '%s carries no colour literal',
    (label, path) => {
      expect(literalsIn(label, readFileSync(path, 'utf8'))).toEqual([]);
    },
  );

  it.each(modules.map((path) => [key(path), path]))(
    '%s branches no theme of its own',
    (_label, path) => {
      const clean = withoutComments(readFileSync(path, 'utf8'));

      expect(clean).not.toMatch(/\[data-theme/);
      expect(clean).not.toMatch(/prefers-color-scheme/);
    },
  );

  it('documents every exemption and keeps none that has gone stale', () => {
    for (const entry of DOCUMENTED_EXTERNAL_VALUES) {
      expect(entry.reason.length).toBeGreaterThan(0);
      const path = join(SRC, entry.file);
      expect(withoutComments(readFileSync(path, 'utf8'))).toContain(entry.value);
    }
  });
});
