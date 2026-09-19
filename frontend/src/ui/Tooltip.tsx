import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import styles from './Tooltip.module.css';

const VIEWPORT_MARGIN_PX = 8;

export interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
}

export function Tooltip({ content, children }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const bubbleRef = useRef<HTMLSpanElement>(null);

  const show = () => setOpen(true);
  const hide = () => setOpen(false);

  useLayoutEffect(() => {
    const trigger = triggerRef.current;
    const bubble = bubbleRef.current;
    if (!open || !trigger || !bubble) return;

    const anchor = trigger.getBoundingClientRect();
    const size = bubble.getBoundingClientRect();
    const maxLeft = window.innerWidth - size.width - VIEWPORT_MARGIN_PX;
    const fitsBelow = anchor.bottom + size.height <= window.innerHeight - VIEWPORT_MARGIN_PX;
    setPosition({
      top: fitsBelow ? anchor.bottom : anchor.top - size.height,
      left: Math.max(VIEWPORT_MARGIN_PX, Math.min(anchor.left, maxLeft)),
    });
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const close = () => setOpen(false);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    const onPointerDown = (event: Event) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target) || bubbleRef.current?.contains(target)) return;
      close();
    };

    document.addEventListener('keydown', onKeyDown);
    document.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('scroll', close, true);
    window.addEventListener('resize', close);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={styles.trigger}
        aria-describedby={id}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={show}
      >
        {children}
      </button>
      <span
        ref={bubbleRef}
        id={id}
        role="tooltip"
        className={styles.bubble}
        hidden={!open}
        style={position ?? undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
      >
        <span className={styles.panel}>{content}</span>
      </span>
    </>
  );
}
