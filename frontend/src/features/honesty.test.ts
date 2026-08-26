import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(process.cwd(), 'src');

/**
 * A screen may never claim an outcome it did not produce, and may never print a
 * number the backend did not send. Both rules are recorded in
 * docs/frontend_system.md under "Honesty rules"; both once shipped as real
 * defects, which is why they are policed here rather than left to review.
 */

const SUCCESS_CLAIM = /(['"`])([^'"`\n]*\b(saved|updated|added|created|removed|deleted)\b[^'"`\n]*)\1/gi;
const NOT_A_CLAIM = /\b(not|n't|nothing|could|cannot|unable|fail|error|no)\b/i;

const API_IMPORT = /from\s+['"](@\/api\/(?!errors|types|queryKeys|creditLabels)[a-zA-Z]+|\.\.?\/[^'"]*api\/(?!errors|types|queryKeys|creditLabels)[a-zA-Z]+)['"]/;
const SAVE_CALLBACK = /\bon(Save|Delete|Generated|Update|Created|AttemptRecorded|QuizReady)\b/;

const NULLABLE_METRICS = [
  'average_score',
  'total_time_spent_seconds',
  'time_spent_seconds',
  'profile_knowledge_items_used',
  'monthly_grant',
  'graded_count',
  'correct_count',
];

const FABRICATED_ZERO = new RegExp(
  `\\b(${NULLABLE_METRICS.join('|')})\\b[^;\\n]*(\\?\\?|\\|\\|)\\s*0\\b`,
);

const FIXTURE_CONSTANT =
  /^(export\s+)?const\s+(MOCK|FAKE|SAMPLE|DUMMY|PLACEHOLDER|SEED|DEMO|STUB)_[A-Z0-9_]*\s*(:[^=]+)?=\s*[[{]/m;

/**
 * Documented exemptions. Each needs a reason, and a stale entry fails the run,
 * so an exemption cannot outlive the thing it excused.
 */
export const DOCUMENTED_EXEMPTIONS: { file: string; reason: string }[] = [];

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

function successClaims(source: string): string[] {
  return Array.from(source.matchAll(SUCCESS_CLAIM))
    .map((match) => match[2])
    .filter((claim) => !NOT_A_CLAIM.test(claim));
}

const exempt = new Set(DOCUMENTED_EXEMPTIONS.map((entry) => entry.file));
const scripts = collect(SRC);
const policed = scripts.filter((path) => !exempt.has(key(path)));
const screens = policed.filter((path) => path.endsWith('.tsx'));

describe('no screen claims an outcome it did not produce', () => {
  it('finds the modules it is meant to police', () => {
    expect(policed.length).toBeGreaterThan(50);
    expect(screens.length).toBeGreaterThan(30);
  });

  it.each(screens.map((path) => [key(path), path] as const))(
    '%s backs every success notice with a request',
    (name, path) => {
      const source = readFileSync(path, 'utf8');
      const claims = successClaims(source);

      if (claims.length === 0) {
        return;
      }

      const reaches = API_IMPORT.test(source) || SAVE_CALLBACK.test(source);

      expect(
        reaches,
        `${name} says ${JSON.stringify(claims[0])} but calls no API and takes no save callback. A settings page that said "Preferences saved locally" while persisting nothing is the defect this guards.`,
      ).toBe(true);
    },
  );
});

describe('no screen prints a number the backend did not send', () => {
  it.each(policed.map((path) => [key(path), path] as const))(
    '%s leaves an unavailable metric absent rather than zero',
    (name, path) => {
      const source = readFileSync(path, 'utf8');
      const offender = source.match(FABRICATED_ZERO);

      expect(
        offender?.[0] ?? null,
        `${name} turns a nullable metric into 0. A metric the backend cannot produce is absent, not zero.`,
      ).toBeNull();
    },
  );

  it.each(policed.map((path) => [key(path), path] as const))(
    '%s ships no stand-in data',
    (name, path) => {
      const source = readFileSync(path, 'utf8');
      const offender = source.match(FIXTURE_CONSTANT);

      expect(
        offender?.[0] ?? null,
        `${name} declares a hardcoded fixture. Every course card once read "0 sources" forever because a mapper hardcoded an empty array.`,
      ).toBeNull();
    },
  );
});

describe('the exemption list stays honest', () => {
  it('names only files that still exist', () => {
    const present = new Set(scripts.map(key));
    const stale = DOCUMENTED_EXEMPTIONS.filter((entry) => !present.has(entry.file));

    expect(stale.map((entry) => entry.file)).toEqual([]);
  });

  it('gives every exemption a reason', () => {
    const unexplained = DOCUMENTED_EXEMPTIONS.filter((entry) => entry.reason.trim().length < 20);

    expect(unexplained.map((entry) => entry.file)).toEqual([]);
  });
});
