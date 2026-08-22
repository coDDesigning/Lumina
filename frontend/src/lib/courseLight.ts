const HUES = [266, 196, 32, 146, 334, 218, 12, 96, 288, 172, 52, 310];

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
