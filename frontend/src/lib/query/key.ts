export type QueryKeyPart = string | number | boolean | null;
export type QueryKey = readonly QueryKeyPart[];

export function hashKey(key: QueryKey): string {
  return JSON.stringify(key);
}

export function parseKey(hash: string): QueryKey {
  return JSON.parse(hash) as QueryKey;
}

export function matchesPrefix(key: QueryKey, prefix: QueryKey): boolean {
  if (prefix.length > key.length) {
    return false;
  }
  for (let index = 0; index < prefix.length; index += 1) {
    if (key[index] !== prefix[index]) {
      return false;
    }
  }
  return true;
}
