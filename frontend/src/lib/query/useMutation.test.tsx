import { act, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { queryCache } from './cache';
import { useMutation, patchQuery } from './useMutation';
import { useQuery } from './useQuery';

interface HarnessProps {
  list: () => Promise<string[]>;
  remove: (name: string) => Promise<void>;
  optimistic?: boolean;
  onRun?: (run: (name: string) => Promise<void>) => void;
}

function Harness({ list, remove, optimistic = false, onRun }: HarnessProps) {
  const items = useQuery<string[]>({
    key: ['items'],
    fetcher: () => list(),
    fallbackMessage: 'The items could not be loaded.',
    staleTime: 60_000,
  });

  const mutation = useMutation<string, void>({
    mutate: (name) => remove(name),
    fallbackMessage: 'It could not be removed.',
    optimistic: optimistic
      ? (name) => [
          patchQuery<string[]>(['items'], (previous) =>
            previous?.filter((item) => item !== name),
          ),
        ]
      : undefined,
    invalidates: () => [['items']],
  });

  onRun?.(mutation.run);

  return (
    <div>
      <p data-testid="items">{(items.data ?? []).join(',') || 'none'}</p>
      <p data-testid="pending">{mutation.isPending ? 'yes' : 'no'}</p>
      <p data-testid="error">{mutation.error?.message ?? 'none'}</p>
    </div>
  );
}

describe('useMutation', () => {
  it('invalidates the keys it declares once the mutation succeeds', async () => {
    const list = vi.fn().mockResolvedValueOnce(['a', 'b']).mockResolvedValueOnce(['b']);
    const remove = vi.fn().mockResolvedValue(undefined);
    let run!: (name: string) => Promise<void>;
    render(<Harness list={list} remove={remove} onRun={(fn) => (run = fn)} />);
    await waitFor(() => expect(screen.getByTestId('items')).toHaveTextContent('a,b'));

    await act(async () => {
      await run('a');
    });

    await waitFor(() => expect(screen.getByTestId('items')).toHaveTextContent('b'));
    expect(list).toHaveBeenCalledTimes(2);
  });

  it('does not invalidate when the mutation fails', async () => {
    const list = vi.fn().mockResolvedValue(['a', 'b']);
    const remove = vi.fn().mockRejectedValue(new APIError(500, { detail: 'Boom' }));
    let run!: (name: string) => Promise<void>;
    render(<Harness list={list} remove={remove} onRun={(fn) => (run = fn)} />);
    await waitFor(() => expect(screen.getByTestId('items')).toHaveTextContent('a,b'));

    await act(async () => {
      await expect(run('a')).rejects.toBeInstanceOf(APIError);
    });

    await waitFor(() => expect(screen.getByTestId('error')).toHaveTextContent('Boom'));
    expect(list).toHaveBeenCalledTimes(1);
  });

  it('applies an optimistic patch and rolls it back on failure', async () => {
    const list = vi.fn().mockResolvedValue(['a', 'b']);
    const remove = vi.fn().mockRejectedValue(new APIError(500, { detail: 'Boom' }));
    let run!: (name: string) => Promise<void>;
    render(<Harness list={list} remove={remove} optimistic onRun={(fn) => (run = fn)} />);
    await waitFor(() => expect(screen.getByTestId('items')).toHaveTextContent('a,b'));

    await act(async () => {
      await expect(run('a')).rejects.toBeInstanceOf(APIError);
    });

    expect(screen.getByTestId('items')).toHaveTextContent('a,b');
  });

  it('rejects with the original error so callers can branch on the code', async () => {
    const list = vi.fn().mockResolvedValue([]);
    const remove = vi
      .fn()
      .mockRejectedValue(new APIError(409, { detail: 'Nope' }, 'no_relevant_material'));
    let run!: (name: string) => Promise<void>;
    render(<Harness list={list} remove={remove} onRun={(fn) => (run = fn)} />);

    await act(async () => {
      await run('a').catch((caught: unknown) => {
        expect(caught).toBeInstanceOf(APIError);
        expect((caught as APIError).code).toBe('no_relevant_material');
        expect((caught as APIError).status).toBe(409);
      });
    });
  });

  it('still invalidates when the component unmounts mid-mutation', async () => {
    const list = vi.fn().mockResolvedValue(['a']);
    let resolveRemove!: () => void;
    const remove = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveRemove = () => resolve();
        }),
    );
    let run!: (name: string) => Promise<void>;
    const view = render(<Harness list={list} remove={remove} onRun={(fn) => (run = fn)} />);
    await waitFor(() => expect(screen.getByTestId('items')).toHaveTextContent('a'));

    const invalidate = vi.spyOn(queryCache, 'invalidate');
    const running = run('a');
    view.unmount();
    resolveRemove();
    await running;

    expect(invalidate).toHaveBeenCalledWith(['items']);
    invalidate.mockRestore();
  });
});
