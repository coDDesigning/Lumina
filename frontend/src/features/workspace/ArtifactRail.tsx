import { BookOpen, Layers3, Target } from 'lucide-react';
import { Button } from '@/ui/Button';
import { Skeleton } from '@/ui/Skeleton';
import { relativeDay } from './relativeDay';
import type { CourseArtifact } from './useCourseArtifacts';
import styles from './ArtifactRail.module.css';

export interface ArtifactRailProps {
  artifacts: CourseArtifact[];
  isLoading: boolean;
  onOpenAll: () => void;
  onOpenOutput: (outputId: number) => void;
  onOpenProgress: () => void;
}

const VISIBLE = 5;

function titleOf(artifact: CourseArtifact): string {
  if (artifact.kind === 'quiz') {
    return `Quiz · ${artifact.totalQuestions} question${artifact.totalQuestions === 1 ? '' : 's'}`;
  }
  if (artifact.topic) {
    return artifact.topic;
  }
  if (artifact.kind === 'study_guide') {
    return 'Study guide';
  }
  if (artifact.kind === 'flashcards') {
    return 'Flashcards';
  }
  return artifact.outputType.replace(/_/g, ' ');
}

function metaOf(artifact: CourseArtifact): string {
  const when = relativeDay(artifact.createdAt);

  if (artifact.kind === 'quiz') {
    return `Scored ${Math.round(artifact.score * 100)}% · ${when}`;
  }
  if (!artifact.topic) {
    return `Whole course · ${when}`;
  }
  if (artifact.kind === 'study_guide') {
    return `Study guide · ${when}`;
  }
  if (artifact.kind === 'flashcards') {
    return `Flashcards · ${when}`;
  }
  return when;
}

function iconOf(artifact: CourseArtifact) {
  if (artifact.kind === 'quiz') {
    return <Target aria-hidden="true" />;
  }
  if (artifact.kind === 'flashcards') {
    return <Layers3 aria-hidden="true" />;
  }
  return <BookOpen aria-hidden="true" />;
}

export function ArtifactRail({
  artifacts,
  isLoading,
  onOpenAll,
  onOpenOutput,
  onOpenProgress,
}: ArtifactRailProps) {
  if (isLoading) {
    return (
      <div className={styles.rail}>
        <Skeleton variant="text" />
        <Skeleton variant="text" />
      </div>
    );
  }

  if (artifacts.length === 0) {
    return (
      <p className={styles.empty}>
        Nothing yet. Whatever you make from this course is kept here, and reopening it costs
        nothing.
      </p>
    );
  }

  const shown = artifacts.slice(0, VISIBLE);

  return (
    <div className={styles.rail}>
      <ul className={styles.list}>
        {shown.map((artifact) => (
          <li key={artifact.key}>
            <button
              type="button"
              className={styles.entry}
              onClick={() =>
                artifact.kind === 'quiz' ? onOpenProgress() : onOpenOutput(artifact.outputId)
              }
            >
              <span className={styles.icon} aria-hidden="true">
                {iconOf(artifact)}
              </span>
              <span className={styles.text}>
                <span className={styles.title}>{titleOf(artifact)}</span>
                <span className={styles.meta}>{metaOf(artifact)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>

      <Button variant="ghost" size="sm" fullWidth onClick={onOpenAll}>
        {artifacts.length > VISIBLE ? `See all ${artifacts.length}` : 'See all'}
      </Button>
    </div>
  );
}
