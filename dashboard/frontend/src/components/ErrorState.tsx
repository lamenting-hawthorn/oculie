import { AlertCircle } from 'lucide-react'

interface ErrorStateProps {
  message?: string
  onRetry?: () => void
}

export function ErrorState({ message = 'Something went wrong', onRetry }: ErrorStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-[var(--text-muted)]">
      <AlertCircle className="mb-3 h-10 w-10 text-[var(--accent-red)] opacity-60" />
      <p className="text-sm font-medium">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-4 rounded px-4 py-1.5 text-xs border border-[var(--border)] hover:bg-[var(--surface-hover)] transition-colors"
        >
          Retry
        </button>
      )}
    </div>
  )
}
