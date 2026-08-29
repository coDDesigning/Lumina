import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@/lib/query/useQuery';
import { queryKeys } from '@/api/queryKeys';
import { adsAPI } from '@/api/ads';
import type { AdPlacement, AdStatus } from '@/api/types';
import { useAdConsent } from './useAdConsent';
import styles from './AdSlot.module.css';

export interface AdSlotProps {
  placement: AdPlacement;
  slotId?: string;
  className?: string;
}

export function AdSlot({ placement, slotId, className }: AdSlotProps) {
  const { data: config } = useQuery({
    key: queryKeys.adsConfig(),
    fetcher: ({ signal }) => adsAPI.getConfig({ signal }),
    fallbackMessage: 'Could not load advertising configuration.',
  });

  const { isGranted } = useAdConsent();
  const [adStatus, setAdStatus] = useState<AdStatus | 'idle'>('idle');
  const containerRef = useRef<HTMLDivElement>(null);
  const reportedRef = useRef<boolean>(false);

  useEffect(() => {
    if (!config?.enabled || !isGranted) {
      return;
    }

    let isMounted = true;
    const provider = config.provider || 'adsense';
    const publisherId = config.publisher_id || 'ca-pub-3125212202463432';

    const reportTelemetry = (status: AdStatus) => {
      if (reportedRef.current) return;
      reportedRef.current = true;
      setAdStatus(status);
      void adsAPI
        .recordTelemetry({
          placement,
          provider,
          status,
        })
        .catch(() => {
          // Silently ignore telemetry transmission errors
        });
    };

    const isAdSense = provider === 'adsense' || provider.includes('google');

    if (isAdSense) {
      const scriptSrc = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${publisherId}`;
      let script = document.querySelector<HTMLScriptElement>(
        'script[src*="pagead2.googlesyndication.com"]',
      );
      if (!script) {
        script = document.createElement('script');
        script.src = scriptSrc;
        script.async = true;
        script.crossOrigin = 'anonymous';
        script.onerror = () => {
          if (isMounted) {
            reportTelemetry('blocked');
          }
        };
        document.head.appendChild(script);
      }

      try {
        const win = window as unknown as { adsbygoogle?: Array<Record<string, unknown>> };
        win.adsbygoogle = win.adsbygoogle || [];
        win.adsbygoogle.push({});
        if (isMounted) {
          reportTelemetry('rendered');
        }
      } catch {
        if (isMounted) {
          reportTelemetry('error');
        }
      }

      return () => {
        isMounted = false;
      };
    }

    const scriptSrc = 'https://media.ethicalads.io/media/client/ethicalads.min.js';
    const timeoutId = window.setTimeout(() => {
      if (isMounted && adStatus === 'idle') {
        reportTelemetry('blocked');
      }
    }, 2500);

    let script = document.querySelector<HTMLScriptElement>(`script[src="${scriptSrc}"]`);
    if (!script) {
      script = document.createElement('script');
      script.src = scriptSrc;
      script.async = true;
      script.onerror = () => {
        if (isMounted) {
          reportTelemetry('blocked');
        }
      };
      document.head.appendChild(script);
    }

    const container = containerRef.current;
    const onAdLoaded = () => {
      if (isMounted) {
        reportTelemetry('rendered');
      }
    };
    const onAdEmpty = () => {
      if (isMounted) {
        reportTelemetry('no_fill');
      }
    };

    if (container) {
      container.addEventListener('ea-loaded', onAdLoaded);
      container.addEventListener('ea-empty', onAdEmpty);
    }

    try {
      const win = window as unknown as { ethicalads?: { reload: () => void } };
      win.ethicalads?.reload();
    } catch {
      // Ignore reload errors
    }

    return () => {
      isMounted = false;
      window.clearTimeout(timeoutId);
      if (container) {
        container.removeEventListener('ea-loaded', onAdLoaded);
        container.removeEventListener('ea-empty', onAdEmpty);
      }
    };
  }, [config?.enabled, config?.provider, config?.publisher_id, isGranted, placement, adStatus]);

  if (!config?.enabled || !isGranted) {
    return null;
  }

  if (adStatus === 'blocked' || adStatus === 'no_fill' || adStatus === 'error') {
    return null;
  }

  const publisherId = config.publisher_id || 'ca-pub-3125212202463432';
  const provider = config.provider || 'adsense';
  const isAdSense = provider === 'adsense' || provider.includes('google');

  if (isAdSense) {
    return (
      <div
        ref={containerRef}
        className={`${styles.container} ${className || ''}`}
        id={`lumina-ad-slot-${placement}`}
      >
        <div className={styles.slot}>
          <span className={styles.badge}>Ad</span>
          <ins
            className="adsbygoogle"
            style={{ display: 'block' }}
            data-ad-client={publisherId}
            data-ad-slot={slotId || 'auto'}
            data-ad-format="auto"
            data-full-width-responsive="true"
          />
        </div>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className={`${styles.container} ${className || ''}`}
      data-ea-publisher={publisherId}
      data-ea-type="image"
      data-ea-placement={placement}
      id={`lumina-ad-slot-${placement}`}
    >
      <div className={styles.slot}>
        <span className={styles.badge}>Ad</span>
      </div>
    </div>
  );
}
