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
        <div className="panel m-6 border-[color:var(--c-err)]">
          <div className="panel-head">
            <h2 className="panel-head-title text-[color:var(--c-err)]">System fault</h2>
            <span className="panel-head-meta">client crashes are logged server-side</span>
          </div>
          <div className="panel-body">
            <p className="mono text-sm text-[color:var(--c-err)]">{this.state.error.message}</p>
            <p className="fine mt-1">This failure was reported to the server logs.</p>
            <button
              className="btn btn-primary mt-4"
              type="button"
              onClick={() => this.setState({ error: null })}
            >
              Reload this view
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}