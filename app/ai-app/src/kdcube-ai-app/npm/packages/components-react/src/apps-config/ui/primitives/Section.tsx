/** A labelled section block with an optional count and description. */
import type { ReactNode } from 'react';

export interface SectionProps {
  title: string;
  count?: number;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Section({ title, count, hint, actions, children }: SectionProps) {
  return (
    <section className="ac-section">
      <header className="ac-section__head">
        <h3 className="ac-section__title">
          {title}
          {typeof count === 'number' && <span className="ac-section__count">{count}</span>}
        </h3>
        {actions && <div className="ac-section__actions">{actions}</div>}
      </header>
      {hint && <p className="ac-section__hint">{hint}</p>}
      <div className="ac-section__body">{children}</div>
    </section>
  );
}
