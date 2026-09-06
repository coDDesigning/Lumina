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

/**
 * An outcome sentence ends on the outcome, optionally qualified by how it
 * happened: "Defaults saved", "Preferences saved locally", "Knowledge topic
 * added successfully.". Requiring the string to end there is what separates a
 * claim from a participle modifying a noun — "Saved results" and "Loading saved
 * quizzes" are a heading and a status, and neither asserts that anything was
 * written.
 */
const CLAIM =
  /(['"`])([^'"`\n]*\b(saved|updated|added|created|removed|deleted)\b(\s+(successfully|locally|automatically|already|now))?\s*[.!]?)\1/gi;
const NOT_A_CLAIM = /\b(not|n't|nothing|could|cannot|unable|fail|error|no)\b/i;

/**
 * A claim reports an outcome that has happened. A string is not one when it is
 * a bare label ("Added", heading a diff column), or when it addresses the
 * reader rather than reporting ("Hand in what you saved"). Both contain the
 * verb and neither asserts that anything was persisted.
 */
const NOT_AN_OUTCOME = /^\s*\W*\w+\W*$|\b(you|what)\b/i;

/**
 * A request that could have produced the claim: an awaited call into an API
 * module, an awaited mutating method, or an invocation of the callback that
 * hands persistence to a parent. An invocation, not a mention — a component may
 * name onSave in its props type without ever calling it.
 */
const MUTATION =
  /await\s+\w*API\.\w+\s*\(|await\s+\w+\.(create|update|delete|save|upload|import|remove|change|patch|post|put)\w*\s*\(|\bon(Save|Delete|Generated|Update|Created|AttemptRecorded|QuizReady)\s*\(/i;

/**
 * How far from the claim its backing request may sit. A handler that awaits a
 * request and then announces the result keeps the two within a few lines. This
 * window is generous on purpose: the rule exists to catch a screen with no
 * write at all, not to police how handlers are laid out.
 */
const BACKING_WINDOW = 40;

const NULLABLE_METRICS = [
  'average_score',
  'averageScore',
  'total_time_spent_seconds',
  'time_spent_seconds',
  'timeSpentSeconds',
  'profile_knowledge_items_used',
  'monthly_grant',
  'monthlyGrant',
  'graded_count',
  'gradedCount',
  'correct_count',
  'correctCount',
  'scorePercent',
];

const METRIC = `(?:${NULLABLE_METRICS.join('|')})`;

/**
 * The shapes that turn an absent metric into a zero. Only the coalesce used to
 * be policed, so the identical defect written as a null-check ternary, or
 * applied to a destructured rename, passed unnoticed.
 */
const COALESCED_ZERO = new RegExp(String.raw`\b${METRIC}\b[^;\n]*(\?\?|\|\|)\s*0\b`);
const NULL_CHECK_ZERO = new RegExp(
  String.raw`\b${METRIC}\b[^;\n]*(===?|!==?)\s*(null|undefined)\s*\?\s*0\b`,
);
const TERNARY_ZERO = new RegExp(String.raw`\b${METRIC}\b[^;\n]*\?[^;\n]*:\s*0\b`);
/**
 * A rename in a destructuring pattern, so a metric cannot leave the policed
 * vocabulary by being given a new name. Anchored to the declaration keyword on
 * purpose: `{ metric: value }` in an object literal is a property being set,
 * not a binding being renamed, and treating the two alike made every later
 * mention of the property's value look like a fabricated zero.
 */
const DESTRUCTURED_ALIAS = new RegExp(
  String.raw`(?:const|let|var)\s*\{[^}]*?\b${METRIC}\s*:\s*([A-Za-z_$][\w$]*)`,
  'g',
);

const FIXTURE_CONSTANT =
  /^(export\s+)?const\s+(MOCK|FAKE|SAMPLE|DUMMY|PLACEHOLDER|SEED|DEMO|STUB)_[A-Z0-9_]*\s*(:[^=]+)?=\s*[[{]/m;

/**
 * Documented exemptions. Each needs a reason, and a stale entry fails the run,
 * so an exemption cannot outlive the thing it excused.
 */
export const DOCUMENTED_EXEMPTIONS: { file: string; reason: string }[] = [
  {
    file: 'ui/TagInput.tsx',
    reason:
      'Announces "<entry> added" and "<entry> removed" into its own role="status" region. It is a controlled primitive, so the outcome it names is the chip the caller can already see: onChange has produced it by the time the region is read. The rule polices a screen that claims persistence it never performed, and onChange is too generic to whitelist because every input has one.',
  },
];

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

export function successClaims(source: string): string[] {
  return Array.from(source.matchAll(CLAIM))
    .map((match) => match[2])
    .filter((claim) => !NOT_A_CLAIM.test(claim) && !NOT_AN_OUTCOME.test(claim));
}

/**
 * Claims this file makes with no request near enough to have produced them.
 *
 * The rule used to ask only whether the file imported any API module anywhere,
 * which nearly every data-driven screen does in order to read. That let a
 * hardcoded success notice sit unchallenged in a screen that never writes, so
 * the check is scoped to the code around the claim instead.
 */
export function unbackedClaims(source: string): string[] {
  const lines = source.split('\n');
  const unbacked: string[] = [];

  lines.forEach((line, index) => {
    for (const claim of successClaims(line)) {
      const context = lines
        .slice(Math.max(0, index - BACKING_WINDOW), index + BACKING_WINDOW + 1)
        .join('\n');
      if (!MUTATION.test(context)) {
        unbacked.push(claim);
      }
    }
  });

  return unbacked;
}

export function fabricatedZero(source: string): string | null {
  for (const pattern of [COALESCED_ZERO, NULL_CHECK_ZERO, TERNARY_ZERO]) {
    const hit = source.match(pattern);
    if (hit) {
      return hit[0];
    }
  }

  for (const alias of source.matchAll(DESTRUCTURED_ALIAS)) {
    const renamed = new RegExp(
      String.raw`\b${alias[1]}\b[^;\n]*((\?\?|\|\|)\s*0\b|\?[^;\n]*:\s*0\b)`,
    );
    const hit = source.match(renamed);
    if (hit) {
      return hit[0];
    }
  }

  return null;
}

const exempt = new Set(DOCUMENTED_EXEMPTIONS.map((entry) => entry.file));
const scripts = collect(SRC);
const policed = scripts.filter((path) => !exempt.has(key(path)));
const screens = policed.filter((path) => path.endsWith('.tsx'));

const READ_ONLY_SCREEN = `
import { progressAPI } from '@/api/progress';

export function ProgressPanel() {
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    progressAPI.get(courseId).then(setProgress);
  }, [courseId]);

  return <Alert tone="success">{'Preferences saved locally'}</Alert>;
}
`;

const WRITING_SCREEN = `
import { settingsAPI } from '@/api/settings';

export function SettingsPanel() {
  async function handleSave() {
    await settingsAPI.update(courseId, draft);
    notify('Defaults saved');
  }

  return <button onClick={handleSave}>Save defaults</button>;
}
`;

describe('the honesty detectors catch the defects they name', () => {
  it('reads a success notice, and passes over a label that only contains the verb', () => {
    expect(successClaims("notify('Defaults saved');")).toEqual(['Defaults saved']);
    expect(successClaims("notify('Knowledge topic added successfully.');")).toEqual([
      'Knowledge topic added successfully.',
    ]);
    expect(successClaims("<th>{'Added'}</th>")).toEqual([]);
    expect(successClaims("<h2>{'Saved results'}</h2>")).toEqual([]);
    expect(successClaims("<span>{'Hand in what you saved'}</span>")).toEqual([]);
    expect(successClaims("notify('Nothing was removed');")).toEqual([]);
    expect(successClaims("notify('Your preferences saved');")).toEqual([
      'Your preferences saved',
    ]);
  });

  it('flags a claim whose file reads an API but never writes one', () => {
    expect(unbackedClaims(READ_ONLY_SCREEN)).toEqual(['Preferences saved locally']);
    expect(unbackedClaims(WRITING_SCREEN)).toEqual([]);
  });

  it('flags a fabricated zero however it is spelled', () => {
    expect(fabricatedZero('const shown = data.average_score ?? 0;')).not.toBeNull();
    expect(fabricatedZero('const shown = data.average_score || 0;')).not.toBeNull();
    expect(
      fabricatedZero('const shown = data.average_score == null ? 0 : data.average_score;'),
    ).not.toBeNull();
    expect(
      fabricatedZero('const shown = data.average_score === undefined ? 0 : data.average_score;'),
    ).not.toBeNull();
    expect(
      fabricatedZero('const shown = data.average_score != null ? data.average_score : 0;'),
    ).not.toBeNull();
    expect(
      fabricatedZero('const { average_score: avg } = data;\nconst shown = avg ?? 0;'),
    ).not.toBeNull();
  });

  it('leaves an honest absence alone', () => {
    expect(
      fabricatedZero('const shown = data.average_score == null ? null : data.average_score;'),
    ).toBeNull();
    expect(fabricatedZero('const shown = summary.average_score ?? null;')).toBeNull();
    expect(fabricatedZero('const attempts = items.length ?? 0;')).toBeNull();
  });
});

describe('no screen claims an outcome it did not produce', () => {
  it('finds the modules it is meant to police', () => {
    expect(policed.length).toBeGreaterThan(50);
    expect(screens.length).toBeGreaterThan(30);
  });

  it.each(screens.map((path) => [key(path), path] as const))(
    '%s backs every success notice with a request',
    (name, path) => {
      const unbacked = unbackedClaims(readFileSync(path, 'utf8'));

      expect(
        unbacked,
        `${name} says ${JSON.stringify(unbacked[0])} with no request near enough to have produced it. A settings page that said "Preferences saved locally" while persisting nothing is the defect this guards.`,
      ).toEqual([]);
    },
  );
});

describe('no screen prints a number the backend did not send', () => {
  it.each(policed.map((path) => [key(path), path] as const))(
    '%s leaves an unavailable metric absent rather than zero',
    (name, path) => {
      const offender = fabricatedZero(readFileSync(path, 'utf8'));

      expect(
        offender,
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
