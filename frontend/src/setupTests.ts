import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, vi } from 'vitest';
import type { MockInstance } from 'vitest';
import { resetQueryCache } from '@/lib/query/cache';

let escaped: string[] = [];
let consoleSpy: MockInstance | null = null;

beforeEach(() => {
  escaped = [];
  consoleSpy = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    const text = args
      .map((arg) => (arg instanceof Error ? arg.message : String(arg)))
      .join(' ');
    escaped.push(text);
    process.stderr.write(`${text}\n`);
  });
});

afterEach(() => {
  cleanup();
  resetQueryCache();
  const found = escaped;
  escaped = [];
  consoleSpy?.mockRestore();
  consoleSpy = null;
  vi.clearAllMocks();
  expect(found, `console.error was called during this test:\n${found.join('\n')}`).toEqual([]);
});

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

window.scrollTo = vi.fn();
