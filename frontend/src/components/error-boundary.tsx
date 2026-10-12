"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="bg-red-50 border border-red-200 rounded-card p-5">
            <h3 className="text-sm font-semibold text-red-800 mb-2">Component Error</h3>
            <pre className="text-xs text-red-600 whitespace-pre-wrap break-words">
              {this.state.error.message}
            </pre>
            <pre className="text-[10px] text-red-400 mt-2 whitespace-pre-wrap break-words max-h-40 overflow-auto">
              {this.state.error.stack}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-3 text-xs text-red-700 underline"
            >
              Try again
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
