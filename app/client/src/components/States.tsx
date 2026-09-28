/**
 * Shared loading / empty / error states so every data surface handles the four cases
 * the same way. An error is always an inline, actionable message — never a blank panel.
 */

import type { ReactNode } from 'react';
import {
  Alert,
  AlertDescription,
  AlertTitle,
  Button,
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
  Skeleton,
} from '@databricks/appkit-ui/react';
import { AlertCircle, RotateCw } from 'lucide-react';
import { cn } from '@/lib/utils';

/** Skeleton rows for a dense table, matched to a column count. */
export function LoadingRows({ rows = 8, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y divide-border" aria-hidden>
      {Array.from({ length: rows }, (_, r) => (
        <div
          key={`sk-${r}`}
          className="grid items-center gap-4 px-4 py-2.5"
          style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: cols }, (_, c) => (
            <Skeleton key={`sk-${r}-${c}`} className={cn('h-3.5', c === 0 ? 'w-24' : 'w-16')} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** A neutral skeleton block for a panel / card body. */
export function LoadingPanel({ lines = 4, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2.5', className)} aria-hidden>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={`lp-${i}`} className={cn('h-3.5', i === 0 ? 'w-1/3' : i % 2 ? 'w-full' : 'w-4/5')} />
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  message,
  icon,
  action,
  className,
}: {
  title: string;
  message?: string;
  icon?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <Empty className={cn('border border-dashed border-border bg-card/40', className)}>
      <EmptyHeader>
        {icon && <EmptyMedia variant="icon">{icon}</EmptyMedia>}
        <EmptyTitle>{title}</EmptyTitle>
        {message && <EmptyDescription>{message}</EmptyDescription>}
      </EmptyHeader>
      {action && <EmptyContent>{action}</EmptyContent>}
    </Empty>
  );
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  className,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <Alert variant="destructive" className={cn('items-start', className)}>
      <AlertCircle className="h-4 w-4" aria-hidden />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-col items-start gap-2">
        <span>{message}</span>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry} className="gap-1.5">
            <RotateCw className="h-3.5 w-3.5" aria-hidden />
            Retry
          </Button>
        )}
      </AlertDescription>
    </Alert>
  );
}

/** A one-line inline notice for permission / not-found situations inside a panel. */
export function InlineNotice({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn('rounded-md border border-border bg-muted/40 px-3 py-2 text-sm text-muted-foreground', className)}
    >
      {children}
    </div>
  );
}
