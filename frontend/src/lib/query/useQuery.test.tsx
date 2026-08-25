import { StrictMode, useRef } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { APIError } from '@/api/client';
import { useQuery } from './useQuery';

interface ProbeProps {
  fetcher: () => Promise<string>;
  enabled?: boolean;
  label?: string;
  staleTime?: number;
}

function Probe({ fetcher, enabled = true, label = 'probe', staleTime }: ProbeProps) {
  const query = useQuery<string>({
    key: enabled ? ['probe'] : null,
    fetcher: () => fetcher(),
    fallbackMessage: 'The probe could not be loaded.',
    staleTime,
  });

  return (
    <div>
      <p data-testid={`${label}-status`}>{query.status}</p>
      <p data-testid={`${label}-data`}>{query.data ?? 'none'}</p>
      <p data-testid={`${label}-error`}>{query.error?.message ?? 'none'}</p>
      <button type="button" onClick={() => void query.refetch()}>
        Refetch {label}
      </button>
    </div>
  );
}

function RenderCounter({ fetcher }: { fetcher: () => Promise<string> }) {
  const renders = useRef(0);
  renders.current += 1;
  const query = useQuery<string>({
    key: ['probe'],
    fetcher: () => fetcher(),
    fallbackMessage: 'The probe could not be loaded.',
  });

  return (
    <div>
      <p data-testid="renders">{renders.current}</p>
      <p data-testid="status">{query.status}</p>
    </div>
  );
}

describe('useQuery', () => {
  it('requests once under StrictMode double mounting', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    render(
      <StrictMode>
        <Probe fetcher={fetcher} />
      </StrictMode>,
    );

    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('success'));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('shares one request between two components on the same key', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    render(
      <>
        <Probe fetcher={fetcher} label="one" />
        <Probe fetcher={fetcher} label="two" />
      </>,
    );

    await waitFor(() => expect(screen.getByTestId('one-status')).toHaveTextContent('success'));
    expect(screen.getByTestId('two-data')).toHaveTextContent('value');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('serves cached data on remount without a loading flash', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const view = render(<Probe fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('success'));

    view.unmount();
    render(<Probe fetcher={fetcher} />);

    expect(screen.getByTestId('probe-status')).toHaveTextContent('success');
    expect(screen.getByTestId('probe-data')).toHaveTextContent('value');
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  });

  it('skips the revalidation entirely while the data is still fresh', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const view = render(<Probe fetcher={fetcher} staleTime={60_000} />);
    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('success'));

    view.unmount();
    render(<Probe fetcher={fetcher} staleTime={60_000} />);

    await waitFor(() => expect(screen.getByTestId('probe-data')).toHaveTextContent('value'));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not re-render endlessly when the parent renders repeatedly', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    const view = render(<RenderCounter fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId('status')).toHaveTextContent('success'));

    for (let index = 0; index < 25; index += 1) {
      view.rerender(<RenderCounter fetcher={fetcher} />);
    }

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(Number(screen.getByTestId('renders').textContent)).toBeLessThan(40);
  });

  it('stays idle and never fetches while the key is null', async () => {
    const fetcher = vi.fn().mockResolvedValue('value');
    render(<Probe fetcher={fetcher} enabled={false} />);

    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('idle'));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('reports a failed load as an error with no data', async () => {
    const fetcher = vi.fn().mockRejectedValue(new APIError(500, { detail: 'Boom' }));
    render(<Probe fetcher={fetcher} />);

    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('error'));
    expect(screen.getByTestId('probe-data')).toHaveTextContent('none');
    expect(screen.getByTestId('probe-error')).toHaveTextContent('Boom');
  });

  it('recovers through refetch after a failure', async () => {
    const user = userEvent.setup();
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new APIError(500, { detail: 'Boom' }))
      .mockResolvedValueOnce('value');
    render(<Probe fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('error'));

    await user.click(screen.getByRole('button', { name: 'Refetch probe' }));

    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('success'));
    expect(screen.getByTestId('probe-data')).toHaveTextContent('value');
  });

  it('does not retry a failed query when a new subscriber mounts', async () => {
    const fetcher = vi.fn().mockRejectedValue(new APIError(500, { detail: 'Boom' }));
    const view = render(<Probe fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('error'));

    view.unmount();
    render(<Probe fetcher={fetcher} />);

    await waitFor(() => expect(screen.getByTestId('probe-status')).toHaveTextContent('error'));
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
