import { useEffect, useState } from 'react';

/**
 * Seconds since a long operation started, for the shared generating state.
 *
 * Reset to zero when the operation ends, so a second run never continues the
 * first one's count.
 */
export function useElapsed(running: boolean): number {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return;
    }
    const started = Date.now();
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - started) / 1000));
    }, 1000);
    return () => clearInterval(timer);
  }, [running]);

  return elapsed;
}
