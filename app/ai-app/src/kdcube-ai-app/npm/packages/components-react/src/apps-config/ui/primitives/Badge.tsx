/** Small status/label chip. Tone maps to the admin status palette. */
import type { ReactNode } from 'react';

export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warn' | 'danger' | 'muted';

export interface BadgeProps {
  tone?: BadgeTone;
  title?: string;
  children: ReactNode;
}

export function Badge({ tone = 'neutral', title, children }: BadgeProps) {
  return (
    <span className={`ac-badge ac-badge--${tone}`} title={title}>
      {children}
    </span>
  );
}
