import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SOURCE = readFileSync(join(process.cwd(), 'src', 'ui', 'Button.module.css'), 'utf8');

function ruleBody(selector: string): string {
  const start = SOURCE.indexOf(selector);
  expect(start, `${selector} is not declared`).toBeGreaterThan(-1);
  const open = SOURCE.indexOf('{', start);
  const close = SOURCE.indexOf('}', open);
  return SOURCE.slice(open + 1, close);
}

describe('button label wrapping', () => {
  it('keeps a short label on one line by default', () => {
    expect(ruleBody('.button {')).toMatch(/white-space:\s*nowrap/);
  });

  it('lets a label carrying user text wrap instead of widening the page', () => {
    const wrap = ruleBody('.wrap {');
    expect(wrap).toMatch(/white-space:\s*normal/);
    expect(wrap).toMatch(/overflow-wrap:\s*anywhere/);
  });
});
