import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

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

function definitionsIn(source: string): Set<string> {
  return new Set(Array.from(source.matchAll(/(--[a-z0-9-]+)\s*:/g), (hit) => hit[1]));
}

function referencesIn(source: string): Set<string> {
  return new Set(Array.from(source.matchAll(/var\(\s*(--[a-z0-9-]+)/g), (hit) => hit[1]));
}

const globalTokens = new Set<string>([
  ...definitionsIn(readFileSync(join(SRC, 'styles', 'tokens.css'), 'utf8')),
  ...definitionsIn(readFileSync(join(SRC, 'styles', 'base.css'), 'utf8')),
]);

const setFromScript = new Set<string>(
  collect(SRC, (name) => name.endsWith('.tsx') || name.endsWith('.ts')).flatMap((path) =>
    Array.from(
      readFileSync(path, 'utf8').matchAll(/['"`](--[a-z0-9-]+)['"`]\s*:/g),
      (hit) => hit[1],
    ),
  ),
);

describe('design tokens', () => {
  const modules = collect(SRC, (name) => name.endsWith('.module.css'));

  it('finds the CSS modules to check', () => {
    expect(modules.length).toBeGreaterThan(10);
  });

  it.each(modules.map((path) => [path.slice(SRC.length + 1), path]))(
    '%s only references tokens that exist',
    (_label, path) => {
      const source = readFileSync(path, 'utf8');
      const local = definitionsIn(source);

      const unknown = Array.from(referencesIn(source)).filter(
        (token) => !globalTokens.has(token) && !local.has(token) && !setFromScript.has(token),
      );

      expect(unknown).toEqual([]);
    },
  );
});
