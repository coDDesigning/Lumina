import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Every colour in the product comes from the design tokens, so a colour typed
 * literally into a component or a stylesheet is a defect: it will not follow
 * the theme and it will not follow the palette.
 *
 * This is a scan over source text, which bounds what it can see. A colour
 * assembled at runtime is invisible to it by construction — `'#' + toHex(r, g,
 * b)`, a template literal built from computed parts, or a value read from an
 * API response. No regex can close that gap, so a colour built rather than
 * written needs review by eye; only the real-browser axe run in
 * `e2e/a11y.spec.ts` sees what actually gets painted.
 */

const SRC = join(process.cwd(), 'src');

interface Exemption {
  file: string;
  value: string;
  reason: string;
}

export const DOCUMENTED_EXTERNAL_VALUES: Exemption[] = [
  {
    file: 'App.tsx',
    value: "'blue'",
    reason:
      'A member of the Workspace accent union, not a colour. The union is blue | violet | rose | amber, and rose and amber are not CSS colours at all, which is what marks the set as a vocabulary of palette names. Each name is mapped to tokens in the stylesheet; nothing here reaches a paint value.',
  },
  {
    file: 'App.tsx',
    value: "'violet'",
    reason:
      'The second member of the same Workspace accent union as the blue entry above, allowed for the same reason: it names a palette entry that the stylesheet resolves to tokens, not a colour to paint with.',
  },
  {
    file: 'data/workspaces.ts',
    value: "'blue'",
    reason:
      'The declaration site of the Workspace accent union that App.tsx enumerates. It is a type literal in accent: blue | violet | rose | amber, so it names a palette entry rather than a colour.',
  },
  {
    file: 'data/workspaces.ts',
    value: "'violet'",
    reason:
      'The second member of the accent union declared alongside the blue entry above, allowed for the same reason.',
  },
];

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
const COLOUR_FUNCTION =
  /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color-mix|color)\(([^()]*(?:\([^()]*\)[^()]*)*)\)/g;
const NAMED = new RegExp(String.raw`:\s*(?:` + NAMED_COLOURS.join('|') + String.raw`)\s*(?:;|!|$)`, 'gim');

/**
 * The declaration form above needs the colour word to sit directly after a
 * colon, so a quote between the two hides it: `color: 'red'` in a style object,
 * `style={{ color: 'white' }}`, and `cond ? 'white' : 'black'` all slipped
 * through while the same value written as CSS was caught. Scripts are scanned
 * for the quoted form as well.
 */
const QUOTED_NAMED = new RegExp(
  String.raw`['"]\s*(?:` + NAMED_COLOURS.join('|') + String.raw`)\s*['"]`,
  'gi',
);

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

export function literalsIn(file: string, source: string): string[] {
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
  if (/\.tsx?$/.test(file)) {
    for (const hit of clean.matchAll(QUOTED_NAMED)) {
      found.push(hit[0].trim());
    }
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

  it('recognises a colour literal wherever it is written', () => {
    expect(literalsIn('a.module.css', 'color: #ff0000;')).toEqual(['#ff0000']);
    expect(literalsIn('a.module.css', 'color: red;')).toEqual([': red;']);
    expect(literalsIn('a.module.css', 'background: rgba(0, 0, 0, 0.4);')).toEqual([
      'rgba(0, 0, 0, 0.4)',
    ]);
    expect(literalsIn('a.module.css', 'color: color-mix(in srgb, red 50%, blue 50%);')).toEqual(
      ['color-mix(in srgb, red 50%, blue 50%)'],
    );
  });

  it('sees a named colour quoted as a script value', () => {
    expect(literalsIn('a.tsx', "const style = { color: 'red' } as CSSProperties;")).toEqual([
      "'red'",
    ]);
    expect(literalsIn('a.tsx', "<p style={{ color: 'white' }} />")).toEqual(["'white'"]);
    expect(literalsIn('a.tsx', "const c = cond ? 'white' : 'black';")).toEqual([
      "'white'",
      "'black'",
    ]);
  });

  it('leaves a tokenised value alone', () => {
    expect(literalsIn('a.module.css', 'color: var(--destructive);')).toEqual([]);
    expect(literalsIn('a.module.css', 'background: rgb(var(--accent-rgb) / 0.5);')).toEqual([]);
    expect(
      literalsIn('a.module.css', 'color: color-mix(in srgb, var(--a) 50%, var(--b) 50%);'),
    ).toEqual([]);
    expect(literalsIn('a.tsx', "const label = 'redacted';")).toEqual([]);
  });

  it('reads the quoted form only where it means a colour', () => {
    expect(literalsIn('a.module.css', "content: 'red';")).toEqual([]);
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
