export function relativeDay(iso: string, now: number = Date.now()): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) {
    return '';
  }

  const days = Math.floor((now - then) / 86_400_000);
  if (days <= 0) {
    return 'today';
  }
  if (days === 1) {
    return 'yesterday';
  }
  if (days < 7) {
    return `${days} days ago`;
  }
  if (days < 14) {
    return 'last week';
  }
  if (days < 62) {
    return `${Math.floor(days / 7)} weeks ago`;
  }
  return new Intl.DateTimeFormat('en', { day: 'numeric', month: 'short' }).format(then);
}
