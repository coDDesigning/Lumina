import { useId, useRef } from 'react';
import type { MouseEvent, ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cx } from '@/lib/cx';
import { useFocusTrap, useScrollLock } from '@/lib/useFocusTrap';
import { IconButton } from './IconButton';
import styles from './Dialog.module.css';

export type DialogSize = 'sm' | 'md' | 'lg' | 'xl';

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  size?: DialogSize;
  mark?: ReactNode;
  markTone?: 'accent' | 'destructive';
  footer?: ReactNode;
  spreadFooter?: boolean;
  children?: ReactNode;
  className?: string;
  showClose?: boolean;
  dismissOnScrimClick?: boolean;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  size = 'md',
  mark,
  markTone = 'accent',
  footer,
  spreadFooter = false,
  children,
  className,
  showClose = true,
  dismissOnScrimClick = true,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const descriptionId = `${titleId}-description`;

  useFocusTrap(panelRef, open, onClose);
  useScrollLock(open);

  if (!open) {
    return null;
  }

  function handleScrimClick(event: MouseEvent<HTMLDivElement>) {
    if (dismissOnScrimClick && event.target === event.currentTarget) {
      onClose();
    }
  }

  return createPortal(
    <div className={styles.scrim} onMouseDown={handleScrimClick}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cx(styles.panel, styles[size], className)}
      >
        <div className={styles.header}>
          {mark ? (
            <span className={cx(styles.mark, markTone === 'destructive' && styles.markDestructive)}>
              {mark}
            </span>
          ) : null}
          <div className={styles.headerText}>
            <h2 className={styles.title} id={titleId}>
              {title}
            </h2>
            {description ? (
              <p className={styles.description} id={descriptionId}>
                {description}
              </p>
            ) : null}
          </div>
          {showClose ? (
            <IconButton label="Close" icon={<X aria-hidden="true" />} size="sm" onClick={onClose} />
          ) : null}
        </div>
        {children ? <div className={styles.body}>{children}</div> : null}
        {footer ? (
          <div className={cx(styles.footer, spreadFooter && styles.footerSpread)}>{footer}</div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
