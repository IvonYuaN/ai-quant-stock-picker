import { Component, type ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback ?? (
          <div className="aq-state aq-state-warn">
            <AlertTriangle aria-hidden="true" />
            <span>{this.state.error?.message || "页面加载失败"}</span>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
