import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ArtifactRail } from './ArtifactRail';
import { relativeDay } from './relativeDay';
import type { CourseArtifact } from './useCourseArtifacts';

const NOW = Date.parse('2026-08-23T12:00:00Z');

function daysAgo(days: number): string {
  return new Date(NOW - days * 86_400_000).toISOString();
}

beforeEach(() => {
  vi.spyOn(Date, 'now').mockReturnValue(NOW);
});

afterEach(() => {
  vi.mocked(Date.now).mockRestore();
});

const GUIDE: CourseArtifact = {
  kind: 'study_guide',
  key: 'output-1',
  outputId: 1,
  outputType: 'study_guide',
  topic: 'Caches and locality',
  createdAt: daysAgo(2),
};

const DECK: CourseArtifact = {
  kind: 'flashcards',
  key: 'output-2',
  outputId: 2,
  outputType: 'flashcards',
  topic: null,
  createdAt: daysAgo(4),
};

const ATTEMPT: CourseArtifact = {
  kind: 'quiz',
  key: 'attempt-9',
  score: 0.7,
  correctCount: 7,
  totalQuestions: 10,
  createdAt: daysAgo(1),
};

function renderRail(artifacts: CourseArtifact[], handlers: Partial<Record<string, () => void>> = {}) {
  return render(
    <ArtifactRail
      artifacts={artifacts}
      isLoading={false}
      onOpenAll={handlers.all ?? vi.fn()}
      onOpenOutput={handlers.output ?? vi.fn()}
      onOpenProgress={handlers.progress ?? vi.fn()}
    />,
  );
}

describe('relativeDay', () => {
  it('reads recent days the way a person would say them', () => {
    expect(relativeDay(daysAgo(0), NOW)).toBe('today');
    expect(relativeDay(daysAgo(1), NOW)).toBe('yesterday');
    expect(relativeDay(daysAgo(3), NOW)).toBe('3 days ago');
    expect(relativeDay(daysAgo(9), NOW)).toBe('last week');
    expect(relativeDay(daysAgo(21), NOW)).toBe('3 weeks ago');
  });

  it('falls back to a date once "weeks ago" stops being useful', () => {
    expect(relativeDay(daysAgo(120), NOW)).toMatch(/\d/);
  });

  it('says nothing rather than NaN for an unreadable date', () => {
    expect(relativeDay('not a date', NOW)).toBe('');
  });
});

describe('ArtifactRail', () => {
  it('lists what was made rather than hiding it behind one button', () => {
    renderRail([ATTEMPT, GUIDE, DECK]);

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(3);
    expect(within(items[0]).getByText('Quiz · 10 questions')).toBeInTheDocument();
    expect(within(items[1]).getByText('Caches and locality')).toBeInTheDocument();
    expect(within(items[2]).getByText('Flashcards')).toBeInTheDocument();
  });

  it('reports a quiz by the score it actually earned', () => {
    renderRail([ATTEMPT]);

    expect(screen.getByText('Scored 70% · yesterday')).toBeInTheDocument();
  });

  it('says a whole-course guide covered the whole course', () => {
    renderRail([DECK]);

    expect(screen.getByText('Whole course · 4 days ago')).toBeInTheDocument();
  });

  it('names the kind under a topic-focused result, since the topic is the headline', () => {
    renderRail([GUIDE]);

    expect(screen.getByText('Caches and locality')).toBeInTheDocument();
    expect(screen.getByText('Study guide · 2 days ago')).toBeInTheDocument();
  });

  it('shows only the newest few and says how many there are in total', () => {
    const many = Array.from({ length: 9 }, (_, index) => ({
      ...GUIDE,
      key: `output-${index}`,
      outputId: index,
      topic: `Topic ${index}`,
    }));

    renderRail(many);

    expect(screen.getAllByRole('listitem')).toHaveLength(5);
    expect(screen.getByRole('button', { name: 'See all 9' })).toBeInTheDocument();
  });

  it('opens a saved result, and sends a quiz attempt to progress instead', async () => {
    const output = vi.fn();
    const progress = vi.fn();
    renderRail([GUIDE, ATTEMPT], { output, progress });

    await userEvent.click(screen.getByText('Caches and locality'));
    expect(output).toHaveBeenCalledWith(1);

    await userEvent.click(screen.getByText('Quiz · 10 questions'));
    expect(progress).toHaveBeenCalled();
  });

  it('invites a first result rather than showing an empty list', () => {
    renderRail([]);

    expect(screen.getByText(/Whatever you make from this course is kept here/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /see all/i })).toBeNull();
  });
});
