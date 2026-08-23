const HUES = [214, 232, 196, 258, 176, 280, 148, 32, 320, 104, 12, 52];

export function courseHue(courseId: number | string): number {
  const raw = typeof courseId === 'number' ? courseId : hashString(courseId);
  return HUES[Math.abs(raw) % HUES.length];
}

function hashString(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index);
    hash |= 0;
  }
  return hash;
}
