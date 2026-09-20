const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export type SourceOrder = 'name' | 'newest';

export function compareNames(a: string, b: string): number {
  return collator.compare(a, b);
}

export function sortByName<T>(items: readonly T[], nameOf: (item: T) => string): T[] {
  return [...items].sort((a, b) => compareNames(nameOf(a), nameOf(b)));
}

export function sortByNewest<T>(items: readonly T[], createdAtOf: (item: T) => string): T[] {
  return [...items].sort((a, b) => Date.parse(createdAtOf(b)) - Date.parse(createdAtOf(a)));
}
