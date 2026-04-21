import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex min-h-screen items-center justify-center bg-[var(--bg-primary)] p-8">
          <div className="max-w-2xl rounded-xl border border-[var(--accent-red)]/40 bg-[var(--bg-card)] p-8">
            <h1 className="text-lg font-bold text-[var(--accent-red)]">Page crashed</h1>
            <pre className="mt-4 overflow-auto rounded-lg bg-[var(--bg-secondary)] p-4 text-xs text-[var(--text-secondary)] whitespace-pre-wrap">
              {this.state.error.message}
              {'\n\n'}
              {this.state.error.stack}
            </pre>
            <button
              onClick={() => this.setState({ error: null })}
              className="mt-4 rounded-lg bg-[var(--accent-blue)]/15 px-4 py-2 text-sm text-[var(--accent-blue)] hover:bg-[var(--accent-blue)]/25"
            >
              Try again
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
