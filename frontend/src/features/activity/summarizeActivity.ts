import type { ActivityItem } from '@/api/types';

export const RHYTHM_DAYS = 28;
const WEEK_DAYS = 7;
const MAX_COURSES = 4;
const DAY_MS = 86_400_000;

export interface RhythmDay {
  date: Date;
  count: number;
}

export interface CourseShare {
  courseId: number | null;
  title: string;
  count: number;
}

export interface ActivitySummaryData {
  rhythm: RhythmDay[];
  activeDays: number;
  thisWeek: number;
  lastWeek: number;
  attemptCount: number;
  averageScore: number | null;
  courses: CourseShare[];
  total: number;
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function daysBetween(from: Date, to: Date): number {
  return Math.round((startOfDay(to).getTime() - startOfDay(from).getTime()) / DAY_MS);
}

export function summarizeActivity(
  items: ActivityItem[],
  now: Date = new Date(),
): ActivitySummaryData {
  const today = startOfDay(now);
  const rhythm: RhythmDay[] = Array.from({ length: RHYTHM_DAYS }, (_, index) => {
    const date = new Date(today);
    date.setDate(today.getDate() - (RHYTHM_DAYS - 1 - index));
    return { date, count: 0 };
  });

  let thisWeek = 0;
  let lastWeek = 0;
  const scores: number[] = [];
  let attemptCount = 0;
  const byCourse = new Map<number, CourseShare>();

  for (const item of items) {
    const occurred = new Date(item.occurred_at);
    if (!Number.isNaN(occurred.getTime())) {
      const ago = daysBetween(occurred, now);
      if (ago >= 0 && ago < RHYTHM_DAYS) {
        rhythm[RHYTHM_DAYS - 1 - ago].count += 1;
      }
      if (ago >= 0 && ago < WEEK_DAYS) {
        thisWeek += 1;
      } else if (ago >= WEEK_DAYS && ago < WEEK_DAYS * 2) {
        lastWeek += 1;
      }
    }

    if (item.kind === 'attempt') {
      attemptCount += 1;
      if (item.score !== null) {
        scores.push(item.score);
      }
    }

    const share = byCourse.get(item.course_id);
    if (share) {
      share.count += 1;
    } else {
      byCourse.set(item.course_id, {
        courseId: item.course_id,
        title: item.course_title,
        count: 1,
      });
    }
  }

  const ranked = [...byCourse.values()].sort((a, b) => b.count - a.count);
  const courses = ranked.slice(0, MAX_COURSES);
  const rest = ranked.slice(MAX_COURSES).reduce((sum, share) => sum + share.count, 0);
  if (rest > 0) {
    courses.push({ courseId: null, title: 'Other courses', count: rest });
  }

  return {
    rhythm,
    activeDays: rhythm.filter((day) => day.count > 0).length,
    thisWeek,
    lastWeek,
    attemptCount,
    averageScore:
      scores.length > 0 ? scores.reduce((sum, score) => sum + score, 0) / scores.length : null,
    courses,
    total: items.length,
  };
}
