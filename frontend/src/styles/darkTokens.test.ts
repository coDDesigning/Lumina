import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const source = readFileSync(join(process.cwd(), 'src', 'styles', 'tokens.css'), 'utf8');

function tokensIn(block: string): Record<string, string> {
  const found: Record<string, string> = {};
  for (const hit of block.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    found[hit[1]] = hit[2].trim();
  }
  return found;
}

function blockAfter(marker: string): string {
  const start = source.indexOf(marker);
  if (start === -1) {
    throw new Error(`no block for ${marker}`);
  }
  const open = source.indexOf('{', start);
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') {
      depth -= 1;
      if (depth === 0) {
        return source.slice(open + 1, index);
      }
    }
  }
  throw new Error(`unterminated block for ${marker}`);
}

describe('dark theme tokens', () => {
  const followsSystem = tokensIn(blockAfter(":root:not([data-theme='light'])"));
  const chosenExplicitly = tokensIn(blockAfter(":root[data-theme='dark']"));

  it('defines the same tokens whether dark came from the system or the toggle', () => {
    expect(Object.keys(chosenExplicitly).sort()).toEqual(Object.keys(followsSystem).sort());
  });

  it('gives every one of them the same value', () => {
    expect(chosenExplicitly).toEqual(followsSystem);
  });

  it('redefines every colour role the light theme declares', () => {
    const light = tokensIn(blockAfter(':root'));
    const NOT_A_COLOUR = /^--(text-(2xs|xs|sm|base|md|lg|xl|2xl|3xl|4xl|5xl)|border-width(-strong)?)$/;
    const colourRoles = Object.keys(light).filter(
      (token) =>
        /^--(surface|text|border|accent|success|warning|destructive|processing|info|dawn|scrim)/.test(
          token,
        ) && !NOT_A_COLOUR.test(token),
    );

    for (const role of colourRoles) {
      expect(followsSystem, `dark theme is missing ${role}`).toHaveProperty(role);
    }
  });
});
