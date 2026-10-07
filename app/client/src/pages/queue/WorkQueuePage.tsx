/**
 * Work Queue (Adjuster) — claims awaiting a human decision (decision_status
 * RECOMMENDED: pending approvals + PEND-Investigate). A dense, command-oriented table:
 * filter by type / recommendation / disposition / risk, free-text search, sort by any
 * column, keyboard-navigable rows (↑/↓, Enter). Activating a row opens the Claim
 * Decision Cockpit; finalizing there removes the claim from the queue on refresh.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Input,
  Kbd,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@databricks/appkit-ui/react';
import { ClipboardCheck, CornerDownLeft, RotateCw, Search } from 'lucide-react';
import { getQueue, ApiError } from '@/lib/api';
import { age, ageHours, dispositionLabel, money, pct, shortDate, titleCase, toNum } from '@/lib/format';
import type { QueueItem } from '@/lib/types';
import { PageHeader } from '@/components/PageHeader';
import { DataGrid, type GridColumn } from '@/components/DataGrid';
import { DispositionChip, RiskFlags, VerdictChip } from '@/components/StatusChip';
import { EmptyState, ErrorState, LoadingRows } from '@/components/States';
import { ClaimCockpit } from '@/components/cockpit/ClaimCockpit';

type RiskFilter = 'all' | 'duplicate' | 'fraud';

function riskCount(r: QueueItem): number {
  // Gate on the tuned `high_risk` gold flag, NOT fraud_cluster_id (a graph-component id present
  // on virtually every claim) — so the Risk column only lights up for genuine signals.
  return (r.duplicate_of_claim_id ? 1 : 0) + (r.high_risk ? 1 : 0);
}

function uniqueSorted(values: (string | null)[]): string[] {
  return Array.from(new Set(values.filter((v): v is string => Boolean(v)))).sort();
}

export function WorkQueuePage() {
  const [rows, setRows] = useState<QueueItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [verdict, setVerdict] = useState('all');
  const [disposition, setDisposition] = useState('all');
  const [risk, setRisk] = useState<RiskFilter>('all');

  // Track the exact row the adjuster opened: its claim_id (drives the modal + DataGrid
  // active row) and its adjudication_id (so the cockpit opens precisely that adjudication).
  const [openClaim, setOpenClaim] = useState<string | null>(null);
  const [openAdjId, setOpenAdjId] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    void (async () => {
      setLoading(true);
      setError(null);
      setForbidden(false);
      try {
        const items = await getQueue({ limit: 500 }, ctrl.signal);
        if (!live) return;
        setRows(items);
        setFetchedAt(new Date());
      } catch (err: unknown) {
        if (!live || ctrl.signal.aborted) return;
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setError(err instanceof Error ? err.message : 'Failed to load the queue');
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [reloadNonce]);

  // "/" focuses the search box (unless already typing in a field).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = document.activeElement;
      const typing = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement;
      if (e.key === '/' && !typing) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const types = useMemo(() => uniqueSorted(rows.map((r) => r.claim_type)), [rows]);
  const dispositions = useMemo(() => uniqueSorted(rows.map((r) => r.recommended_disposition)), [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (type !== 'all' && r.claim_type !== type) return false;
      if (verdict !== 'all' && r.recommended_verdict !== verdict) return false;
      if (disposition !== 'all' && r.recommended_disposition !== disposition) return false;
      if (risk === 'duplicate' && !r.duplicate_of_claim_id) return false;
      if (risk === 'fraud' && !r.high_risk) return false;
      if (q) {
        const hay =
          `${r.claim_id} ${r.customer_id ?? ''} ${r.defect_code ?? ''} ${r.defect_narrative ?? ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [rows, search, type, verdict, disposition, risk]);

  const columns: GridColumn<QueueItem>[] = [
    {
      key: 'claim',
      header: 'Claim',
      width: 'w-[15%]',
      sortValue: (r) => r.claim_id,
      cell: (r) => (
        <div className="flex flex-col">
          <span className="font-mono text-sm font-medium text-foreground">{r.claim_id}</span>
          <span className="font-mono text-xs text-muted-foreground">{r.customer_id ?? '—'}</span>
        </div>
      ),
    },
    {
      key: 'type',
      header: 'Type',
      sortValue: (r) => r.claim_type ?? '',
      cell: (r) => (r.claim_type ? titleCase(r.claim_type) : '—'),
    },
    {
      key: 'rec',
      header: 'Recommendation',
      sortValue: (r) => r.recommended_verdict ?? '',
      cell: (r) => (
        <div className="flex flex-wrap items-center gap-1">
          <VerdictChip verdict={r.recommended_verdict} />
          <DispositionChip disposition={r.recommended_disposition} />
        </div>
      ),
    },
    {
      key: 'amount',
      header: 'Amount',
      align: 'right',
      sortValue: (r) => toNum(r.approved_amount) ?? 0,
      cell: (r) => (
        <div className="flex flex-col items-end">
          <span className="font-medium text-foreground">{money(r.approved_amount)}</span>
          <span className="text-xs text-muted-foreground">of {money(r.claimed_amount)}</span>
        </div>
      ),
    },
    {
      key: 'confidence',
      header: 'Conf.',
      align: 'right',
      width: 'w-[8%]',
      sortValue: (r) => toNum(r.confidence) ?? 0,
      cell: (r) => pct(r.confidence),
    },
    {
      key: 'risk',
      header: 'Risk',
      sortValue: (r) => riskCount(r),
      cell: (r) =>
        riskCount(r) > 0 ? (
          <RiskFlags duplicateOf={r.duplicate_of_claim_id} highRisk={Boolean(r.high_risk)} riskReason={r.risk_reason} />
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      key: 'age',
      header: 'Age',
      align: 'right',
      width: 'w-[8%]',
      sortValue: (r) => ageHours(r.recommended_at),
      cell: (r) => (
        <span title={shortDate(r.recommended_at)} className="text-muted-foreground">
          {age(r.recommended_at)}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Work Queue"
        description="Claims awaiting your decision."
        meta={
          <span className="text-sm text-muted-foreground">
            <span className="font-semibold text-foreground tabular-nums">{filtered.length}</span>
            {filtered.length !== rows.length && <span className="tabular-nums"> / {rows.length}</span>} awaiting action
            {fetchedAt && <span className="ml-3 text-xs">as of {fetchedAt.toLocaleTimeString()}</span>}
          </span>
        }
        actions={
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => setReloadNonce((n) => n + 1)}
            disabled={loading}
          >
            <RotateCw className="h-3.5 w-3.5" aria-hidden />
            Refresh
          </Button>
        }
      />

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[220px] flex-1">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <Input
            ref={searchRef}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search claim, customer, defect…"
            className="pl-8 pr-10"
            aria-label="Search the queue"
          />
          <Kbd className="absolute right-2 top-1/2 -translate-y-1/2">/</Kbd>
        </div>
        <FilterSelect label="Type" value={type} onChange={setType} options={types} render={titleCase} />
        <FilterSelect
          label="Verdict"
          value={verdict}
          onChange={setVerdict}
          options={['APPROVE', 'DENY', 'PEND_INVESTIGATE']}
          render={(v) => <VerdictChipLabel value={v} />}
        />
        <FilterSelect
          label="Disposition"
          value={disposition}
          onChange={setDisposition}
          options={dispositions}
          render={dispositionLabel}
        />
        <FilterSelect
          label="Risk"
          value={risk}
          onChange={(v) => setRisk(v as RiskFilter)}
          options={['duplicate', 'fraud']}
          render={titleCase}
        />
      </div>

      {loading ? (
        <div className="rounded-lg border border-border bg-card">
          <LoadingRows rows={10} cols={columns.length} />
        </div>
      ) : forbidden ? (
        <EmptyState title="Not available for your role" message="The work queue is an adjuster surface." />
      ) : error ? (
        <ErrorState message={error} onRetry={() => setReloadNonce((n) => n + 1)} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<ClipboardCheck className="h-6 w-6" />}
          title="Queue clear"
          message="No claims are awaiting a decision right now."
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="No matches"
          message="No claims match the current filters."
          action={
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setSearch('');
                setType('all');
                setVerdict('all');
                setDisposition('all');
                setRisk('all');
              }}
            >
              Clear filters
            </Button>
          }
        />
      ) : (
        <div className="space-y-2">
          <QueueKeyHint count={filtered.length} />
          <DataGrid
            rows={filtered}
            columns={columns}
            getRowId={(r) => r.claim_id}
            onRowActivate={(r) => {
              setOpenClaim(r.claim_id);
              setOpenAdjId(r.adjudication_id);
            }}
            activeRowId={openClaim ?? undefined}
            initialSort={{ key: 'age', dir: 'desc' }}
            ariaLabel="Work queue"
          />
        </div>
      )}

      <ClaimCockpit
        claimId={openClaim}
        adjudicationId={openAdjId}
        onClose={() => {
          setOpenClaim(null);
          setOpenAdjId(null);
        }}
        onFinalized={() => setReloadNonce((n) => n + 1)}
      />
    </div>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
  render,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
  render: (v: string) => React.ReactNode;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="w-[150px]" aria-label={`Filter by ${label.toLowerCase()}`}>
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="all">{label}: All</SelectItem>
        {options.map((o) => (
          <SelectItem key={o} value={o}>
            {render(o)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function VerdictChipLabel({ value }: { value: string }) {
  return <span>{value === 'PEND_INVESTIGATE' ? 'Investigate' : titleCase(value)}</span>;
}

/** A quiet, command-oriented hint line — Linear-style keyboard affordances above the grid. */
function QueueKeyHint({ count }: { count: number }) {
  return (
    <div className="flex items-center gap-3 px-0.5 text-xs text-muted-foreground">
      <span className="tabular-nums">
        {count} {count === 1 ? 'claim' : 'claims'}
      </span>
      <span className="text-border">·</span>
      <span className="hidden items-center gap-1 sm:inline-flex">
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd>
        to navigate
      </span>
      <span className="hidden items-center gap-1 sm:inline-flex">
        <Kbd>
          <CornerDownLeft className="h-3 w-3" aria-hidden />
        </Kbd>
        to open
      </span>
    </div>
  );
}
