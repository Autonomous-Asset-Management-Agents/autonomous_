import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** When this value changes, the boundary clears its error (e.g. on page nav). */
  resetKey?: unknown;
  /** Label for the logged error (e.g. the page name). */
  label?: string;
}

interface State {
  error: Error | null;
}

const MAX_AUTO_RETRIES = 3;
const AUTO_RETRY_MS = 2500;

/**
 * Catches render errors in a subtree so a single throw never black-screens the
 * whole console. Most real-world triggers are transient: a page rendered
 * partial data while the engine was still warming up. So we auto-retry a few
 * times, offer a manual Retry, reset on navigation, and log the actual error.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };
  private retries = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error(`[ErrorBoundary${this.props.label ? ` · ${this.props.label}` : ""}]`, error, info);
    if (this.retries < MAX_AUTO_RETRIES) {
      this.retries += 1;
      this.timer = setTimeout(() => this.setState({ error: null }), AUTO_RETRY_MS);
    }
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.retries = 0;
      this.setState({ error: null });
    }
  }

  componentWillUnmount() {
    if (this.timer) clearTimeout(this.timer);
  }

  private reset = () => {
    this.retries = 0;
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <div className="flex flex-col items-center justify-center h-full text-center px-8">
          <div className="text-[14px] text-white/70">This view hit a temporary error.</div>
          <div className="text-[12px] text-white/35 mt-2 max-w-md leading-relaxed">
            Usually live data is still loading right after start-up — it recovers on its own as the engine warms up.
          </div>
          <button
            onClick={this.reset}
            className="mt-4 text-[12px] px-3 py-1.5 rounded-md border border-white/15 text-white/80 hover:bg-white/10 transition-colors"
          >
            Retry now
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
