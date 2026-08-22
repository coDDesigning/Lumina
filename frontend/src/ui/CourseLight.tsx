import type { CSSProperties, ElementType, HTMLAttributes, ReactNode } from 'react';
import { courseHue } from '@/lib/courseLight';
import { cx } from '@/lib/cx';
import styles from './CourseLight.module.css';

type HueStyle = CSSProperties & { '--light-hue'?: number };

export interface CourseLightProps extends HTMLAttributes<HTMLElement> {
  as?: ElementType;
  courseId: number | string;
  children?: ReactNode;
}

/**
 * The course light: a soft radial wash whose hue is derived from the course id,
 * so a workspace is recognisable before its name is read. Identity only — it
 * never carries status, which has its own colour roles.
 */
export function CourseLight({
  as,
  courseId,
  className,
  style,
  children,
  ...rest
}: CourseLightProps) {
  const Element = as ?? 'div';
  const hueStyle: HueStyle = { ...style, '--light-hue': courseHue(courseId) };

  return (
    <Element {...rest} className={cx(styles.light, className)} style={hueStyle}>
      {children}
    </Element>
  );
}

export interface CourseChipProps {
  courseId: number | string;
  className?: string;
}

export function CourseChip({ courseId, className }: CourseChipProps) {
  const hueStyle: HueStyle = { '--light-hue': courseHue(courseId) };

  return <span className={cx(styles.chip, className)} style={hueStyle} aria-hidden="true" />;
}
