/**
 * A dense, editorial page header: a plain label-style title, an optional one-line
 * description, and a right-aligned slot for counts / freshness / actions. No hero
 * framing — headers are labels, not taglines.
 */

import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export function PageHeader({
  title,
  description,
  meta,
  actions,
  className,
}: {
  title: string;
  description?: string;
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-wrap items-end justify-between gap-x-6 gap-y-3', className)}>
      <div className="min-w-0">
        <h2 className="text-xl font-semibold text-foreground">{title}</h2>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {(meta || actions) && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {meta}
          {actions}
        </div>
      )}
    </div>
  );
}

/** A compact "label: value" pair for header meta and detail grids. */
export function MetaStat({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col', className)}>
      <span className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-foreground tabular-nums">{value}</span>
    </div>
  );
}
