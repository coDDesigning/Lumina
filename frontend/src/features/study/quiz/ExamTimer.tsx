import { useEffect, useRef, useState } from 'react';
import { Clock } from 'lucide-react';
import { cx } from '@/lib/cx';
import { formatDuration } from './quizScoring';
import styles from './ExamTimer.module.css';

/**
 * The only moments worth interrupting a candidate for, longest first.
 *
 * A countdown read out every second is unusable with a screen reader on, so the
 * clock is visible and silent, and a separate polite region speaks only when
 * crossing one of these.
 */
const THRESHOLDS = [300, 60, 30];

const LOW_TIME_SECONDS = 60;

export interface ExamTimerProps {
  /** Seconds left, derived from the server's deadline. */
  secondsRemaining: number;
  expired: boolean;
}

function announcementFor(previous: number, current: number): string | null {
  for (const threshold of THRESHOLDS) {
    if (previous > threshold && current <= threshold) {
      return threshold >= 60
        ? `${threshold / 60} ${threshold === 60 ? 'minute' : 'minutes'} left`
        : `${threshold} seconds left`;
    }
  }
  return null;
}

export function ExamTimer({ secondsRemaining, expired }: ExamTimerProps) {
  const [announcement, setAnnouncement] = useState('');
  const previous = useRef(secondsRemaining);

  useEffect(() => {
    // Expiry is announced by the sitting itself, which is the thing that saved
    // the work and can honestly say so. The clock only counts.
    if (expired) {
      setAnnouncement('');
      previous.current = 0;
      return;
    }
    const next = announcementFor(previous.current, secondsRemaining);
    previous.current = secondsRemaining;
    if (next) setAnnouncement(next);
  }, [secondsRemaining, expired]);

  return (
    <>
      {/*
        role="timer" carries the value for anyone reading the screen, and its
        name is the label rather than the changing number, so focus is never
        pulled here as the clock moves.
      */}
      <p
        className={cx(
          styles.timer,
          !expired && secondsRemaining <= LOW_TIME_SECONDS && styles.low,
          expired && styles.expired,
        )}
        role="timer"
        aria-label="Time remaining"
      >
        <Clock aria-hidden="true" />
        <span className="tabular">{expired ? 'Time up' : formatDuration(secondsRemaining)}</span>
      </p>
      <span className="visually-hidden" role="status" aria-live="polite">
        {announcement}
      </span>
    </>
  );
}
