import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/ui/Button';
import { EmptyState } from '@/ui/EmptyState';

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
}

/**
 * Without this a render throw blanks the whole application. The message stays
 * generic on purpose: an exception string is not something to show a student.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled interface error', error, info.componentStack);
  }

  private handleReload = () => {
    window.location.reload();
  };

  render() {
    if (!this.state.hasError) {
      return this.props.children;
    }

    return (
      <EmptyState
        headingLevel="h1"
        tone="destructive"
        icon={<AlertTriangle aria-hidden="true" />}
        title="Something in the page broke."
        description="Nothing you uploaded or generated was lost. Reloading almost always clears it."
        actions={
          <Button variant="primary" onClick={this.handleReload}>
            Reload the page
          </Button>
        }
      />
    );
  }
}
