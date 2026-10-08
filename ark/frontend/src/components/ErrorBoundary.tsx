import { Component, ReactNode } from "react";
import { api } from "../api/client";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/** Global error boundary: reports JS errors to /api/client-log (same logs). */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown): void {
    void api
      .post("/api/client-log", {
        message: error.message,
        stack: error.stack ?? "",
        source: (info as { componentStack?: string } | null)?.componentStack ?? "",
        url: window.location.href,
      })
      .catch(() => undefined);
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div className="card m-6 border-[color:var(--c-err)]">
          <h2 className="text-lg font-semibold text-[color:var(--c-err)]">Something crashed</h2>
          <p className="mt-2 text-sm text-[var(--c-muted)]">
            {this.state.error.message} — reported to the server logs.
          </p>
          <button className="btn btn-primary mt-4" onClick={() => this.setState({ error: null })}>
            Reload this view
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}