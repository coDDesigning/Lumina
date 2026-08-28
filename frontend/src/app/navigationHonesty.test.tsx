import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

const EXAM_MODE_ROUTE = 'exam-mode';
const EXAM_MODE = /\bexam[\s_-]?mode\b/i;

const DEFERRED = [
  { label: 'audio and video', pattern: /\b(audio|video)[\s_-]?(import|upload|transcription)\b/i },
  { label: 'spaced repetition', pattern: /\bspaced[\s_-]?repetition\b/i },
];

const ALLOWED = ['features/marketing/LandingPage.tsx'];

/**
 * The screens a student can actually navigate from.
 *
 * A route on its own is a deep link, reachable only by someone handed the URL,
 * which is how a feature gets built and tested before it is offered. What makes
 * a capability *shipped* is one of these naming it, and that is the moment the
 * landing page has to stop calling it unbuilt.
 */
const ENTRY_POINTS = [
  'app/AppShell.tsx',
  'features/courses/CoursesPage.tsx',
  'features/workspace/WorkspacePage.tsx',
];

function key(path: string): string {
  return relative(SRC, path).split(sep).join('/');
}

function read(name: string): string {
  return readFileSync(join(SRC, name), 'utf8');
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

const routes = read('App.tsx');
const landing = read('features/marketing/LandingPage.tsx');
const examModeIsRouted = routes.includes(`/courses/:courseId/${EXAM_MODE_ROUTE}`);
const offeredFrom = ENTRY_POINTS.filter((name) => EXAM_MODE.test(read(name)));

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
});

describe('the landing page and the product agree about Exam Mode', () => {
  it('never offers a way in that goes nowhere', () => {
    if (offeredFrom.length === 0) {
      return;
    }

    expect(
      examModeIsRouted,
      `${offeredFrom.join(', ')} offers Exam Mode, but /courses/:courseId/${EXAM_MODE_ROUTE} is not routed in App.tsx.`,
    ).toBe(true);
  });

  it('claims Exam Mode is shipped exactly when a student can reach it', () => {
    const unbuilt = landing.match(/Not built yet[\s\S]{0,400}?<\/article>/)?.[0] ?? '';

    if (offeredFrom.length > 0) {
      expect(
        EXAM_MODE.test(unbuilt),
        'Exam Mode is offered in the product, so the landing page must stop listing it as unbuilt.',
      ).toBe(false);
      expect(
        EXAM_MODE.test(landing),
        'Exam Mode ships, so the landing page must say what it does.',
      ).toBe(true);
      return;
    }

    expect(
      unbuilt,
      'Nothing offers Exam Mode yet, so the landing page must still name it as missing.',
    ).toMatch(EXAM_MODE);
  });

  it('keeps naming the capabilities that are still missing', () => {
    expect(landing).toMatch(/Not built yet/);
  });
});
