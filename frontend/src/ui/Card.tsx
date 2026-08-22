import type { ElementType, HTMLAttributes, ReactNode } from 'react';
import { cx } from '@/lib/cx';
import styles from './Card.module.css';

export type CardElevation = 'rest' | 'raised' | 'flat';
export type CardPadding = 'none' | 'sm' | 'md' | 'lg';

const PADDING_CLASS: Record<CardPadding, string | false> = {
  none: false,
  sm: styles.padSm,
  md: styles.padMd,
  lg: styles.padLg,
};

export interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: ElementType;
  elevation?: CardElevation;
  padding?: CardPadding;
  interactive?: boolean;
  children?: ReactNode;
}

export function Card({
  as,
  elevation = 'rest',
  padding = 'none',
  interactive = false,
  className,
  children,
  ...rest
}: CardProps) {
  const Element = as ?? 'div';

  return (
    <Element
      {...rest}
      className={cx(
        styles.card,
        styles[elevation],
        PADDING_CLASS[padding],
        interactive && styles.interactive,
        className,
      )}
    >
      {children}
    </Element>
  );
}
