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
 *
 * The same defect arrives in four shapes, and this guard reads all four: an
 * opacity beside a colour in one rule; an opacity on an element whose colour is
 * declared in a different rule or inherited from an ancestor; an opacity
 * written as a percentage or a `var()` token rather than a bare number; and an
 * opacity set from a `style={{ ... }}` prop in a component.
 *
 * Two shapes stay out of reach, because a stylesheet alone does not say which
 * classes meet on an element or which elements nest inside which:
 *
 * - Composed classes. `.button` carries the opacity while `.primary` carries
 *   the colour, and only the JSX says both land on one element. `ui/Button` and
 *   `ui/IconButton` differ only in that `.iconButton` colours itself, which is
 *   why one is caught here and the other is not.
 * - A container fading coloured descendants, as in `.wrapper:has(input:disabled)`
 *   over a `.description` that is coloured in its own rule.
 *
 * A fade in either shape needs review by eye; the real-browser axe run in
 * `e2e/a11y.spec.ts` is what covers them.
 */

const SRC = join(process.cwd(), 'src');

interface Exemption {
  file: string;
  selector: string;
  reason: string;
}

/**
 * Documented exemptions. Each needs a reason, and an entry naming a rule that
 * no longer fades anything fails the run, so an exemption cannot outlive the
 * thing it excused.
 */
export const DOCUMENTED_FADES: Exemption[] = [
  {
    file: 'ui/IconButton.module.css',
    selector: '.iconButton:disabled',
    reason:
      'Fades a disabled icon button, whose colour comes from .iconButton itself. WCAG 1.4.3 exempts text in an inactive control from the contrast minimum, and the fade is the affordance saying the control cannot be pressed. Colour is not the only route to that fact: the button also reports its disabled state to assistive technology.',
  },
  {
    file: 'ui/IconButton.module.css',
    selector: ".iconButton[aria-disabled='true']",
    reason:
      'The aria-disabled half of the same rule as .iconButton:disabled, exempt for the same reason. Both selectors are listed because a rule is policed per selector, so removing one would leave the other unguarded.',
  },
  {
    file: 'ui/Field.module.css',
    selector: '.control:disabled',
    reason:
      'Fades a disabled input. WCAG 1.4.3 exempts inactive controls. A disabled field is not content the student is being asked to read and act on, and its state reaches assistive technology through the disabled attribute.',
  },
  {
    file: 'ui/Field.module.css',
    selector: '.select:disabled ~ .selectChevron',
    reason:
      'Fades the chevron belonging to a disabled select so the decoration matches the control it serves. It is an icon rather than text, and the select it accompanies is already exempt under WCAG 1.4.3.',
  },
  {
    file: 'features/study/reverseQuiz/ReverseQuizSession.module.css',
    selector: '.textarea:disabled',
    reason:
      'Fades the answer box while a reverse-quiz turn is in flight, so the student can see the field is not accepting input. WCAG 1.4.3 exempts inactive controls, and the state is momentary rather than a way of presenting content.',
  },
];

function key(path: string): string {
  return relative(SRC, path).split(sep).join('/');
}

function collect(
  directory: string,
  matches: (name: string) => boolean,
  found: string[] = [],
): string[] {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      collect(path, matches, found);
      continue;
    }
    if (matches(entry.name)) {
      found.push(path);
    }
  }
  return found;
}

/**
 * Read an `<alpha-value>`. A bare number and a percentage both resolve; a
 * `var()` token or any other computed form does not, and an opacity that
 * cannot be read is reported rather than skipped, because the browser still
 * applies whatever it resolves to.
 */
export function opacityBelowOne(raw: string): boolean {
  const value = raw.trim();

  if (value.endsWith('%')) {
    const percent = Number(value.slice(0, -1));
    return Number.isFinite(percent) && percent < 100;
  }

  const literal = Number(value);
  if (Number.isFinite(literal)) {
    return literal < 1;
  }

  return true;
}

function compounds(selector: string): string[] {
  return selector
    .split(/\s*[>+~]\s*|\s+/)
    .map((part) => part.trim())
    .filter(Boolean);
}

function base(compound: string): string {
  return compound
    .replace(/::?[a-zA-Z-]+(\([^)]*\))?/g, '')
    .replace(/\[[^\]]*\]/g, '')
    .trim();
}

interface Rule {
  selectors: string[];
  colours: boolean;
  opacity: string | null;
}

const RULE = /([^{}]+)\{([^{}]*)\}/g;

/**
 * Read the innermost rule blocks. Matching innermost means a rule nested in an
 * `@media` block is read as itself rather than as the at-rule, and a selector
 * spread over several lines keeps every alternative: an earlier version took
 * only the last line of the head, so `.iconButton:disabled` was dropped from
 * `.iconButton:disabled, .iconButton[aria-disabled='true']` and never policed.
 */
function rules(css: string): Rule[] {
  const parsed: Rule[] = [];

  for (const block of css.matchAll(RULE)) {
    const head = block[1]
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line && !line.startsWith('@'))
      .join(' ');
    const declarations = block[2].split(';').map((line) => line.trim());

    parsed.push({
      selectors: head
        .split(',')
        .map((part) => part.trim())
        .filter(Boolean),
      colours: declarations.some((line) => line.startsWith('color:')),
      opacity:
        declarations.find((line) => line.startsWith('opacity:'))?.slice('opacity:'.length) ??
        null,
    });
  }

  return parsed;
}

export function fadedTextRules(css: string): string[] {
  const parsed = rules(css);
  const coloured = new Set<string>();

  for (const rule of parsed) {
    if (!rule.colours) {
      continue;
    }
    for (const selector of rule.selectors) {
      const parts = compounds(selector);
      const last = parts[parts.length - 1];
      if (last) {
        coloured.add(base(last));
      }
    }
  }

  const offenders: string[] = [];

  for (const rule of parsed) {
    if (rule.opacity === null || !opacityBelowOne(rule.opacity)) {
      continue;
    }

    for (const selector of rule.selectors) {
      const parts = compounds(selector);
      const last = parts[parts.length - 1];
      if (!last) {
        continue;
      }

      const carriesColour = rule.colours || coloured.has(base(last));
      const inheritsColour = parts.slice(0, -1).some((part) => coloured.has(base(part)));

      if (carriesColour || inheritsColour) {
        offenders.push(`${selector} (opacity ${rule.opacity.trim()})`);
      }
    }
  }

  return offenders;
}

const INLINE_STYLE = /style=\{\{([^}]*)\}\}/g;

/**
 * A component can fade coloured text without touching a stylesheet, by setting
 * an opacity in a `style={{ ... }}` prop on an element whose colour arrives
 * from a class. No stylesheet scan can see that shape.
 */
export function fadedInlineStyles(source: string): string[] {
  const offenders: string[] = [];

  for (const hit of source.matchAll(INLINE_STYLE)) {
    const body = hit[1];
    const opacity = body.match(/\bopacity\s*:\s*([^,}]+)/);
    if (!opacity) {
      continue;
    }

    const literal = opacity[1].trim().replace(/^['"]|['"]$/g, '');
    if (!opacityBelowOne(literal)) {
      continue;
    }

    offenders.push(`style={{${body.trim()}}}`);
  }

  return offenders;
}

const MODULES = collect(SRC, (name) => name.endsWith('.module.css'));
const COMPONENTS = collect(
  SRC,
  (name) =>
    (name.endsWith('.tsx') || name.endsWith('.jsx')) &&
    !name.endsWith('.test.tsx') &&
    !name.endsWith('.test.jsx'),
);

const exempt = new Map<string, Set<string>>();
for (const entry of DOCUMENTED_FADES) {
  const selectors = exempt.get(entry.file) ?? new Set<string>();
  selectors.add(entry.selector);
  exempt.set(entry.file, selectors);
}

function selectorOf(offender: string): string {
  return offender.slice(0, offender.lastIndexOf(' ('));
}

function policed(name: string, offenders: string[]): string[] {
  const allowed = exempt.get(name);
  if (!allowed) {
    return offenders;
  }
  return offenders.filter((offender) => !allowed.has(selectorOf(offender)));
}

describe('no component fades coloured text below its token contrast', () => {
  it('finds the files it is meant to police', () => {
    expect(MODULES.length).toBeGreaterThan(20);
    expect(COMPONENTS.length).toBeGreaterThan(30);
  });

  it('recognises a faded rule when it sees one', () => {
    expect(fadedTextRules('.a {\n  color: var(--destructive);\n  opacity: 0.92;\n}')).toEqual([
      '.a (opacity 0.92)',
    ]);
    expect(fadedTextRules('.a {\n  color: var(--destructive);\n}')).toEqual([]);
    expect(fadedTextRules('.a {\n  opacity: 0.5;\n}')).toEqual([]);
    expect(fadedTextRules('.a {\n  color: var(--text);\n  opacity: 1;\n}')).toEqual([]);
  });

  it('reads an opacity that is not a bare number', () => {
    expect(fadedTextRules('.a {\n  color: var(--destructive);\n  opacity: 92%;\n}')).toEqual([
      '.a (opacity 92%)',
    ]);
    expect(
      fadedTextRules('.a {\n  color: var(--destructive);\n  opacity: var(--half);\n}'),
    ).toEqual(['.a (opacity var(--half))']);
    expect(fadedTextRules('.a {\n  color: var(--destructive);\n  opacity: 100%;\n}')).toEqual(
      [],
    );
  });

  it('links a colour and an opacity that live in different rules', () => {
    expect(
      fadedTextRules('.a {\n  color: var(--destructive);\n}\n.a .b {\n  opacity: 0.5;\n}'),
    ).toEqual(['.a .b (opacity 0.5)']);
    expect(
      fadedTextRules('.chip {\n  color: var(--text);\n}\n.chip:disabled {\n  opacity: 0.5;\n}'),
    ).toEqual(['.chip:disabled (opacity 0.5)']);
    expect(
      fadedTextRules('.a {\n  background: var(--surface);\n}\n.a .b {\n  opacity: 0.5;\n}'),
    ).toEqual([]);
  });

  it('reads every selector in a rule, not only the last', () => {
    expect(fadedTextRules('.a,\n.b {\n  color: var(--text);\n  opacity: 0.4;\n}')).toEqual([
      '.a (opacity 0.4)',
      '.b (opacity 0.4)',
    ]);
  });

  it('sees an opacity set from a style prop', () => {
    expect(
      fadedInlineStyles('<p className={styles.danger} style={{ opacity: 0.92 }}>Gone</p>'),
    ).toEqual(['style={{opacity: 0.92}}']);
    expect(
      fadedInlineStyles("<p style={{ color: 'var(--destructive)', opacity: 0.9 }}>Gone</p>"),
    ).toEqual(["style={{color: 'var(--destructive)', opacity: 0.9}}"]);
    expect(fadedInlineStyles('<div style={{ opacity: 1 }} />')).toEqual([]);
    expect(fadedInlineStyles('<div style={{ width: pct }} />')).toEqual([]);
  });

  it.each(MODULES.map((path) => [key(path), path] as const))(
    '%s sets no opacity on text it also colours',
    (name, path) => {
      const offenders = policed(name, fadedTextRules(readFileSync(path, 'utf8')));

      expect(
        offenders,
        `${name} fades coloured text. The palette is chosen to clear 4.5:1 and an opacity on top of it silently drops below, where only a real browser can see it.`,
      ).toEqual([]);
    },
  );

  it.each(COMPONENTS.map((path) => [key(path), path] as const))(
    '%s sets no opacity from a style prop',
    (name, path) => {
      const offenders = fadedInlineStyles(readFileSync(path, 'utf8'));

      expect(
        offenders,
        `${name} fades an element from a style prop. A colour reaching the element from a class is diluted just the same, and no stylesheet scan can see it.`,
      ).toEqual([]);
    },
  );
});

describe('the faded-text exemption list stays honest', () => {
  it('gives every exemption a reason', () => {
    const unexplained = DOCUMENTED_FADES.filter((entry) => entry.reason.trim().length < 40);

    expect(unexplained.map((entry) => entry.file)).toEqual([]);
  });

  it('keeps no entry that has gone stale', () => {
    const stale = DOCUMENTED_FADES.filter((entry) => {
      const offenders = fadedTextRules(readFileSync(join(SRC, entry.file), 'utf8'));
      return !offenders.some((offender) => selectorOf(offender) === entry.selector);
    });

    expect(stale.map((entry) => `${entry.file} ${entry.selector}`)).toEqual([]);
  });
});
