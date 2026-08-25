export function formatStudyTime(seconds: number): string | null {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }

  const whole = Math.floor(seconds);
  if (whole < 60) {
    return `${whole}s`;
  }

  const totalMinutes = Math.floor(whole / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;

  if (hours === 0) {
    return `${minutes}m`;
  }
  if (minutes === 0) {
    return `${hours}h`;
  }
  return `${hours}h ${minutes}m`;
}
