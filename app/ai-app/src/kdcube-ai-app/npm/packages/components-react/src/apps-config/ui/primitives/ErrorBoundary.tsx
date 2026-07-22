/**
 * Catches render errors in the subtree so one bad app's config can't blank the
 * whole widget (and require a page reload). Shows the error message on screen —
 * both for the user to recover and to make the failure diagnosable. Clears the
 * error when `resetKey` changes (e.g. selecting a different app).
 */
import { Component, type ErrorInfo, type ReactNode } from 'react';

export interface ErrorBoundaryProps {
  children: ReactNode;
  resetKey?: unknown;
  label?: string;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidUpdate(prev: ErrorBoundaryProps): void {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // eslint-disable-next-line no-console
    console.error('[apps-config] render error:', error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      return (
        <div className="k-notice ac-note ac-note--error" role="alert">
          <strong>{this.props.label || "Couldn't render this."}</strong>
          <div className="ac-errbody">{error.message || String(error)}</div>
        </div>
      );
    }
    return this.props.children;
  }
}
