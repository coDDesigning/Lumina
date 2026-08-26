import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * `contrast.test.ts` proves the palette clears its ratios, but it reads the
 * tokens rather than what the browser paints. A component that fades coloured
 * text blends it toward the background and defeats the guarantee: `.dangerBody`
 * set `color: var(--destructive)` beside `opacity: 0.92`, and axe measured the
 * result at 4.03:1 in Chromium while every token assertion still passed. So
 * text that carries a colour may not also carry an opacity.
 */

const SRC = join(process.cwd(), 'src');

function collect(directory: string, found: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      collect(path, found);
      continue;
    }
    if (entry.name.endsWith('.module.css')) {
      found.push(path);
    }
  }
  return found;
}

export function fadedTextRules(css: string): string[] {
  const offenders: string[] = [];

  for (const block of css.split('}')) {
    const brace = block.indexOf('{');
    if (brace === -1) {
      continue;
    }

    const selector = block.slice(0, brace).trim().split('\n').pop()?.trim() ?? '';
    const declarations = block
      .slice(brace + 1)
      .split(';')
      .map((line) => line.trim());

    const colours = declarations.some((line) => line.startsWith('color:'));
    const faded = declarations.find((line) => line.startsWith('opacity:'));

    if (!colours || !faded) {
      continue;
    }

    const value = Number(faded.slice('opacity:'.length).trim());
    if (Number.isFinite(value) && value < 1) {
      offenders.push(`${selector} (opacity ${value})`);
    }
  }

  return offenders;
}

const MODULES = collect(SRC);

describe('no component fades coloured text below its token contrast', () => {
  it('finds the stylesheets it is meant to police', () => {
    expect(MODULES.length).toBeGreaterThan(20);
  });

  it('recognises a faded rule when it sees one', () => {
    expect(fadedTextRules('.a {\n  color: var(--destructive);\n  opacity: 0.92;\n}')).toEqual([
      '.a (opacity 0.92)',
    ]);
    expect(fadedTextRules('.a {\n  color: var(--destructive);\n}')).toEqual([]);
    expect(fadedTextRules('.a {\n  opacity: 0.5;\n}')).toEqual([]);
  });

  it.each(MODULES.map((path) => [relative(SRC, path).split(sep).join('/'), path] as const))(
    '%s sets no opacity on text it also colours',
    (name, path) => {
      const offenders = fadedTextRules(readFileSync(path, 'utf8'));

      expect(
        offenders,
        `${name} fades coloured text. The palette is chosen to clear 4.5:1 and an opacity on top of it silently drops below, where only a real browser can see it.`,
      ).toEqual([]);
    },
  );
});
