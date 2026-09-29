/**
 * Source drill-through panel — a right-side sheet that opens when the adjuster clicks a
 * supporting source in the cockpit (a cited clause, a similar prior claim, the customer/
 * heat risk, or a coil/MTC/customer context row). It FETCHES AND SHOWS THE ENTIRE
 * underlying row (every column, each field labeled) and names the fully-qualified source
 * table the row came from, so the recommendation's evidence is traceable back to the
 * governed Lakebase/UC data. Strictly read-only: it only reads one row via the governed
 * `GET /api/source/:source/:id` endpoint (adjuster-authz, parameterized, whitelisted).
 *
 * Handles all four data states: loading (skeleton), error (inline + retry), not-found
 * (empty), and loaded (the row). Rendered as a Sheet layered above the cockpit Dialog.
 */

import { useEffect, useState } from 'react';
import { Badge, Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@databricks/appkit-ui/react';
import { Database } from 'lucide-react';
import { getSourceRow, ApiError } from '@/lib/api';
import { DASH, humanizeKey, scalar } from '@/lib/format';
import type { SourceRowDetail, SourceTarget } from '@/lib/types';
import { EmptyState, ErrorState, LoadingPanel } from '@/components/States';

/** A long/complex value shown verbatim in a scrollable mono block; short scalars inline. */
function FieldValue({ value }: { value: unknown }) {
  const s = scalar(value);
  if (s === DASH) return <span className="text-muted-foreground">{DASH}</span>;
  // Vectors, tsvectors, long narratives — keep the ENTIRE value but bound its footprint.
  if (s.length > 100 || Array.isArray(value) || (typeof value === 'object' && value !== null)) {
    return (
      <div className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md border border-border bg-muted/40 p-2 font-mono text-xs text-foreground">
        {s}
      </div>
    );
  }
  return <span className="text-sm font-medium text-foreground tabular-nums">{s}</span>;
}

/** Every column of the fetched row, in insertion order, each field labeled. */
function RowFields({ row }: { row: Record<string, unknown> }) {
  const entries = Object.entries(row);
  if (entries.length === 0) {
    return <EmptyState title="Empty row" message="The source row has no columns." />;
  }
  return (
    <dl className="divide-y divide-border/60">
      {entries.map(([k, v]) => {
        const s = scalar(v);
        const block = s.length > 100 || Array.isArray(v) || (typeof v === 'object' && v !== null);
        return (
          <div
            key={k}
            className={block ? 'flex flex-col gap-1 py-2' : 'flex items-baseline justify-between gap-4 py-1.5'}
          >
            <dt className="text-xs text-muted-foreground">{humanizeKey(k)}</dt>
            <dd className={block ? '' : 'min-w-0 text-right'}>
              <FieldValue value={v} />
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

export function SourceDetailSheet({ target, onClose }: { target: SourceTarget | null; onClose: () => void }) {
  const [detail, setDetail] = useState<SourceRowDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);

  useEffect(() => {
    if (!target) return;
    const ctrl = new AbortController();
    let live = true;
    void (async () => {
      setLoading(true);
      setError(null);
      setNotFound(false);
      setDetail(null);
      try {
        const d = await getSourceRow(target, ctrl.signal);
        if (live) setDetail(d);
      } catch (err: unknown) {
        if (!live || ctrl.signal.aborted) return;
        if (err instanceof ApiError && err.status === 404) setNotFound(true);
        else setError(err instanceof Error ? err.message : 'Failed to load the source row');
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [target, reloadNonce]);

  return (
    <Sheet open={Boolean(target)} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 border-border p-0 sm:max-w-xl">
        <SheetHeader className="shrink-0 space-y-1.5 border-b border-border px-5 py-4">
          <SheetTitle className="text-base">{target?.title ?? 'Source'}</SheetTitle>
          <SheetDescription className="sr-only">
            The full underlying row for this supporting source, with its source table.
          </SheetDescription>
          {detail && (
            <div className="flex items-center gap-1.5">
              <Database className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              <Badge
                variant="outline"
                className="rounded-[5px] border-border font-mono text-xs font-normal text-muted-foreground"
              >
                {detail.table}
              </Badge>
            </div>
          )}
        </SheetHeader>

        <div className="min-h-0 flex-1 overflow-auto px-5 py-4">
          {loading && <LoadingPanel lines={10} />}
          {error && !loading && <ErrorState message={error} onRetry={() => setReloadNonce((n) => n + 1)} />}
          {notFound && !loading && (
            <EmptyState title="Row not found" message="This source row is no longer present in the underlying table." />
          )}
          {detail && !loading && !error && !notFound && <RowFields row={detail.row} />}
        </div>
      </SheetContent>
    </Sheet>
  );
}
