import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

const CATALOGUE = 'api/queryKeys.ts';
const LIBRARY = 'lib/query/';

function key(path: string): string {
  return relative(SRC, path).split(sep).join('/');
}

function collect(directory: string, matches: string[] = []): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      collect(path, matches);
      continue;
    }
    if (!/\.tsx?$/.test(entry.name) || /\.test\.tsx?$/.test(entry.name)) {
      continue;
    }
    const name = key(path);
    if (name === CATALOGUE || name.startsWith(LIBRARY)) {
      continue;
    }
    matches.push(path);
  }
  return matches;
}

const INLINE_KEY = /\bkey:\s*\[/;

describe('query key discipline', () => {
  const scripts = collect(SRC);

  it('finds the modules it is meant to police', () => {
    expect(scripts.length).toBeGreaterThan(10);
  });

  it.each(scripts.map((path) => [key(path), path] as const))(
    '%s builds every query key through the catalogue',
    (_name, path) => {
      const source = readFileSync(path, 'utf8');
      const offenders = source
        .split('\n')
        .map((line, index) => ({ line: line.trim(), number: index + 1 }))
        .filter((entry) => INLINE_KEY.test(entry.line))
        .map((entry) => `line ${entry.number}: ${entry.line}`);

      expect(
        offenders,
        `Build the key with a queryKeys.* helper instead of an inline array:\n${offenders.join('\n')}`,
      ).toEqual([]);
    },
  );
});
