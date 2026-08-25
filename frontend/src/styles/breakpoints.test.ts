import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');
const TOKENS = join(SRC, 'styles', 'tokens.css');

function key(path: string): string {
  return relative(SRC, path).split(sep).join('/');
}

function collect(directory: string, matches: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      collect(path, matches);
    } else if (entry.name.endsWith('.module.css')) {
      matches.push(path);
    }
  }
  return matches;
}

function declaredBreakpoints(): string[] {
  const source = readFileSync(TOKENS, 'utf8');
  return [...source.matchAll(/--bp-[a-z]+:\s*([0-9.]+rem)/g)].map((match) => match[1]);
}

const MEDIA_CONDITION = /@media[^{]*/g;
const WIDTH = /(?:max|min)-width:\s*([0-9.]+(?:rem|px|em))/g;

describe('breakpoints', () => {
  const scale = declaredBreakpoints();
  const modules = collect(SRC);

  it('reads the scale from the token file', () => {
    expect(scale.length).toBeGreaterThanOrEqual(4);
    expect(modules.length).toBeGreaterThan(10);
  });

  it.each(modules.map((path) => [key(path), path] as const))(
    '%s only breaks at a declared breakpoint',
    (_name, path) => {
      const source = readFileSync(path, 'utf8');
      const offenders: string[] = [];

      for (const condition of source.match(MEDIA_CONDITION) ?? []) {
        for (const width of [...condition.matchAll(WIDTH)]) {
          if (!scale.includes(width[1])) {
            offenders.push(`${condition.trim()} uses ${width[1]}`);
          }
        }
      }

      expect(
        offenders,
        `Use one of the --bp-* values (${scale.join(', ')}) so the layout breaks where the design system says it does:\n${offenders.join('\n')}`,
      ).toEqual([]);
    },
  );
});
