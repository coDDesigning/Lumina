import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, vi } from 'vitest';
import type { MockInstance } from 'vitest';

const REACT_DEFECTS = [
  'same key',
  'Each child in a list',
  'validateDOMNesting',
  'cannot appear as a descendant',
  'cannot contain a nested',
  'not wrapped in act',
  'Invalid ARIA',
  'Received `true` for a non-boolean attribute',
  'Warning: Failed prop type',
  'unique "key" prop',
];

let escaped: string[] = [];
let consoleSpy: MockInstance | null = null;

beforeEach(() => {
  escaped = [];
  consoleSpy = vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    const text = args
      .map((arg) => (arg instanceof Error ? arg.message : String(arg)))
      .join(' ');
    if (REACT_DEFECTS.some((defect) => text.includes(defect))) {
      escaped.push(text);
    }
    process.stderr.write(`${text}\n`);
  });
});

afterEach(() => {
  cleanup();
  const found = escaped;
  escaped = [];
  consoleSpy?.mockRestore();
  consoleSpy = null;
  vi.clearAllMocks();
  expect(found, `React reported a defect during this test:\n${found.join('\n')}`).toEqual([]);
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
