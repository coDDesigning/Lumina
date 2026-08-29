import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useElapsed } from './useElapsed';

describe('useElapsed', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('reports nothing before the operation starts', () => {
    const { result } = renderHook(() => useElapsed(false));

    expect(result.current).toBe(0);
  });

  it('counts whole seconds while the operation runs', () => {
    const { result } = renderHook(() => useElapsed(true));

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(result.current).toBe(3);
  });

  it('does not advance while the operation is stopped', () => {
    const { result } = renderHook(() => useElapsed(false));

    act(() => {
      vi.advanceTimersByTime(5000);
    });

    expect(result.current).toBe(0);
  });

  it('restarts from zero so a second run never continues the first count', () => {
    const { result, rerender } = renderHook(({ running }) => useElapsed(running), {
      initialProps: { running: true },
    });

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(result.current).toBe(4);

    rerender({ running: false });
    expect(result.current).toBe(0);

    rerender({ running: true });
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(result.current).toBe(1);
  });

  it('stops counting once unmounted', () => {
    const { result, unmount } = renderHook(() => useElapsed(true));

    act(() => {
      vi.advanceTimersByTime(2000);
    });
    const lastValue = result.current;

    unmount();
    act(() => {
      vi.advanceTimersByTime(10000);
    });

    expect(lastValue).toBe(2);
  });
});
