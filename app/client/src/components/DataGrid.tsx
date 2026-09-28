/**
 * A dense, keyboard-navigable table for the adjuster surfaces. The grid owns column
 * sorting (click or Enter/Space on a header), roving-focus row navigation (Up/Down,
 * Home/End), and row activation (click or Enter/Space on a row). Categorical filtering
 * lives in each screen's toolbar, which passes already-filtered rows in.
 *
 * Sorting is client-side over the current page — the queue is the bounded set of claims
 * awaiting action, so a single fetch is sorted/filtered locally without server round
 * trips per interaction.
 */

import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@databricks/appkit-ui/react';
import { ChevronDown, ChevronsUpDown, ChevronUp } from 'lucide-react';
import { cn } from '@/lib/utils';

export type SortDir = 'asc' | 'desc';

export interface GridColumn<T> {
  key: string;
  header: string;
  cell: (row: T) => ReactNode;
  /** Comparable value for sorting; omit to make the column unsortable. */
  sortValue?: (row: T) => number | string;
  align?: 'left' | 'right';
  /** Tailwind width class applied to the header + cells (e.g. "w-28"). */
  width?: string;
  headerClassName?: string;
  cellClassName?: string;
}

export interface DataGridProps<T> {
  rows: T[];
  columns: GridColumn<T>[];
  getRowId: (row: T) => string;
  onRowActivate?: (row: T) => void;
  initialSort?: { key: string; dir: SortDir };
  ariaLabel: string;
  /** Highlight the row matching this id (e.g. the claim open in the cockpit). */
  activeRowId?: string;
}

export function DataGrid<T>({
  rows,
  columns,
  getRowId,
  onRowActivate,
  initialSort,
  ariaLabel,
  activeRowId,
}: DataGridProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(initialSort?.key ?? null);
  const [sortDir, setSortDir] = useState<SortDir>(initialSort?.dir ?? 'desc');
  // Roving tabindex: exactly one row is in the tab order at a time (the last-focused
  // one), so Tab enters the grid once and arrow keys move within it.
  const [focusedIndex, setFocusedIndex] = useState(0);
  const bodyRef = useRef<HTMLTableSectionElement>(null);

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    const col = columns.find((c) => c.key === sortKey);
    if (!col?.sortValue) return rows;
    const getVal = col.sortValue;
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = getVal(a);
      const bv = getVal(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }, [rows, columns, sortKey, sortDir]);

  function toggleSort(col: GridColumn<T>) {
    if (!col.sortValue) return;
    if (sortKey === col.key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(col.key);
      setSortDir('desc');
    }
  }

  function moveFocus(from: number, delta: number) {
    const cells = bodyRef.current?.querySelectorAll<HTMLTableRowElement>('[data-grid-row]');
    if (!cells || cells.length === 0) return;
    const next = Math.min(Math.max(from + delta, 0), cells.length - 1);
    cells[next]?.focus();
  }

  function onRowKeyDown(e: KeyboardEvent<HTMLTableRowElement>, index: number, row: T) {
    switch (e.key) {
      case 'Enter':
      case ' ':
        if (onRowActivate) {
          e.preventDefault();
          onRowActivate(row);
        }
        break;
      case 'ArrowDown':
        e.preventDefault();
        moveFocus(index, 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        moveFocus(index, -1);
        break;
      case 'Home':
        e.preventDefault();
        moveFocus(index, -index);
        break;
      case 'End':
        e.preventDefault();
        moveFocus(index, sorted.length);
        break;
      default:
        break;
    }
  }

  // Which row currently holds the tab stop. Clamp so a sort/filter that shrinks the set
  // never strands the tab stop on a removed row.
  const rovingIndex = focusedIndex < sorted.length ? focusedIndex : 0;

  return (
    <div className="overflow-auto rounded-lg border border-border bg-card">
      <Table aria-label={ariaLabel} className="text-sm">
        <TableHeader className="sticky top-0 z-10 bg-secondary/80 backdrop-blur">
          <TableRow className="border-border hover:bg-transparent">
            {columns.map((col) => {
              const active = sortKey === col.key;
              const sortable = Boolean(col.sortValue);
              return (
                <TableHead
                  key={col.key}
                  aria-sort={active ? (sortDir === 'asc' ? 'ascending' : 'descending') : undefined}
                  className={cn(
                    'h-9 px-4 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground',
                    col.align === 'right' && 'text-right',
                    col.width,
                    col.headerClassName
                  )}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(col)}
                      className={cn(
                        'inline-flex items-center gap-1 rounded-sm transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        col.align === 'right' && 'flex-row-reverse',
                        active && 'text-foreground'
                      )}
                    >
                      {col.header}
                      {active ? (
                        sortDir === 'asc' ? (
                          <ChevronUp className="h-3.5 w-3.5" aria-hidden />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5" aria-hidden />
                        )
                      ) : (
                        <ChevronsUpDown className="h-3 w-3 opacity-50" aria-hidden />
                      )}
                    </button>
                  ) : (
                    col.header
                  )}
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody ref={bodyRef}>
          {sorted.map((row, index) => {
            const id = getRowId(row);
            const activatable = Boolean(onRowActivate);
            return (
              <TableRow
                key={id}
                data-grid-row
                data-active={activeRowId === id ? '' : undefined}
                tabIndex={activatable ? (index === rovingIndex ? 0 : -1) : undefined}
                role={activatable ? 'button' : undefined}
                onClick={activatable ? () => onRowActivate?.(row) : undefined}
                onFocus={activatable ? () => setFocusedIndex(index) : undefined}
                onKeyDown={activatable ? (e) => onRowKeyDown(e, index, row) : undefined}
                className={cn(
                  'border-border transition-colors',
                  activatable &&
                    'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                  activeRowId === id && 'bg-accent'
                )}
              >
                {columns.map((col) => (
                  <TableCell
                    key={col.key}
                    className={cn(
                      'px-4 py-2 align-middle',
                      col.align === 'right' && 'text-right tabular-nums',
                      col.cellClassName
                    )}
                  >
                    {col.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
