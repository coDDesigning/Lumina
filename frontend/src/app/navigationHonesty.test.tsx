import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

const DEFERRED = [
  { label: 'Exam Mode', pattern: /\bexam[\s_-]?mode\b/i },
  { label: 'reverse quiz', pattern: /\breverse[\s_-]?quiz\b/i },
  { label: 'mock exam', pattern: /\bmock[\s_-]?exam\b/i },
];

const ALLOWED = ['features/marketing/LandingPage.tsx'];

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
    matches.push(path);
  }
  return matches;
}

describe('deferred capabilities are not presented as product areas', () => {
  const scripts = collect(SRC).filter((path) => !ALLOWED.includes(key(path)));

  it('finds the modules it is meant to police', () => {
    expect(scripts.length).toBeGreaterThan(10);
  });

  it.each(scripts.map((path) => [key(path), path] as const))(
    '%s names no unbuilt capability',
    (_name, path) => {
      const source = readFileSync(path, 'utf8');
      const offenders = DEFERRED.filter((deferred) => deferred.pattern.test(source)).map(
        (deferred) => deferred.label,
      );

      expect(
        offenders,
        `${offenders.join(', ')} is designed but not built. Only the landing page may name it, and only as missing.`,
      ).toEqual([]);
    },
  );

  it('keeps the landing page honest about what is missing', () => {
    const landing = readFileSync(join(SRC, 'features/marketing/LandingPage.tsx'), 'utf8');
    expect(landing).toMatch(/Not built yet/);
    expect(landing).toMatch(/Exam Mode/);
  });
});
