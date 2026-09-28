/**
 * Claims History — finalized claims (decision_status FINAL), visible to both roles. A
 * dense, sortable/filterable table of decisions of record; a row opens the Claim
 * Cockpit in its read-only, finalized view. A role-aware Genie panel answers
 * natural-language questions about past claims (OBO): adjusters use the operational
 * space, business users the gold analytics space — the two the server permits per role.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Badge,
  Button,
  Input,
  Kbd,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@databricks/appkit-ui/react';
import { History, RotateCw, Search } from 'lucide-react';
import { getHistory, ApiError } from '@/lib/api';
import { age, ageHours, dispositionLabel, money, shortDate, titleCase, toNum } from '@/lib/format';
import type { HistoryItem } from '@/lib/types';
import { PageHeader } from '@/components/PageHeader';
import { DataGrid, type GridColumn } from '@/components/DataGrid';
import { DispositionChip, VerdictChip } from '@/components/StatusChip';
import { EmptyState, ErrorState, InlineNotice, LoadingRows } from '@/components/States';
import { GenieAssistant } from '@/components/GenieAssistant';
import { useRole } from '@/components/whoami';
import { ClaimCockpit } from '@/components/cockpit/ClaimCockpit';

type OverrideFilter = 'all' | 'overridden' | 'accepted';

function uniqueSorted(values: (string | null)[]): string[] {
  return Array.from(new Set(values.filter((v): v is string => Boolean(v)))).sort();
}

export function ClaimsHistoryPage() {
  const role = useRole();
  const [rows, setRows] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);

  const [search, setSearch] = useState('');
  const [type, setType] = useState('all');
  const [verdict, setVerdict] = useState('all');
  const [disposition, setDisposition] = useState('all');
  const [override, setOverride] = useState<OverrideFilter>('all');

  const [openClaim, setOpenClaim] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    let live = true;
    void (async () => {
      setLoading(true);
      setError(null);
      setForbidden(false);
      try {
        const items = await getHistory({ limit: 500 }, ctrl.signal);
        if (!live) return;
        setRows(items);
        setFetchedAt(new Date());
      } catch (err: unknown) {
        if (!live || ctrl.signal.aborted) return;
        if (err instanceof ApiError && err.status === 403) setForbidden(true);
        else setError(err instanceof Error ? err.message : 'Failed to load history');
      } finally {
        if (live) setLoading(false);
      }
    })();
    return () => {
      live = false;
      ctrl.abort();
    };
  }, [reloadNonce]);

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
  const dispositions = useMemo(() => uniqueSorted(rows.map((r) => r.disposition)), [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (type !== 'all' && r.claim_type !== type) return false;
      if (verdict !== 'all' && r.verdict !== verdict) return false;
      if (disposition !== 'all' && r.disposition !== disposition) return false;
      if (override === 'overridden' && !r.override_flag) return false;
      if (override === 'accepted' && r.override_flag) return false;
      if (q) {
        const hay = `${r.claim_id} ${r.customer_id ?? ''} ${r.defect_code ?? ''} ${r.decided_by ?? ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [rows, search, type, verdict, disposition, override]);

  const columns: GridColumn<HistoryItem>[] = [
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
      key: 'decision',
      header: 'Decision',
      sortValue: (r) => r.verdict ?? '',
      cell: (r) => (
        <div className="flex flex-wrap items-center gap-1">
          <VerdictChip verdict={r.verdict} />
          <DispositionChip disposition={r.disposition} />
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
      key: 'override',
      header: 'Override',
      sortValue: (r) => (r.override_flag ? 1 : 0),
      cell: (r) =>
        r.override_flag ? (
          <Badge
            variant="outline"
            className="rounded-sm border-warning/30 bg-warning/10 text-xs font-semibold text-warning"
            title={r.override_reason ?? undefined}
          >
            Override
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      key: 'decided_by',
      header: 'Decided by',
      sortValue: (r) => r.decided_by ?? '',
      cell: (r) => (
        <span className="block max-w-[18ch] truncate text-sm text-muted-foreground" title={r.decided_by ?? undefined}>
          {r.decided_by ?? '—'}
        </span>
      ),
    },
    {
      key: 'finalized',
      header: 'Finalized',
      align: 'right',
      width: 'w-[9%]',
      sortValue: (r) => ageHours(r.finalized_at),
      cell: (r) => (
        <span title={shortDate(r.finalized_at)} className="text-muted-foreground">
          {age(r.finalized_at)}
        </span>
      ),
    },
  ];

  const assistantAlias = role === 'business_user' ? 'business' : 'cockpit';
  const assistantSpace = role === 'business_user' ? 'Gold analytics Genie space' : 'Operational Genie space';

  return (
    <div className="space-y-4">
      <PageHeader
        title="Claims History"
        description="Finalized claims — decisions of record."
        meta={
          <span className="text-sm text-muted-foreground">
            <span className="font-semibold text-foreground tabular-nums">{filtered.length}</span>
            {filtered.length !== rows.length && <span className="tabular-nums"> / {rows.length}</span>} finalized
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

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="min-w-0 space-y-4">
          {/* Filter toolbar */}
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative min-w-[200px] flex-1">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden
              />
              <Input
                ref={searchRef}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search claim, customer, adjuster…"
                className="pl-8 pr-10"
                aria-label="Search history"
              />
              <Kbd className="absolute right-2 top-1/2 -translate-y-1/2">/</Kbd>
            </div>
            <FilterSelect label="Type" value={type} onChange={setType} options={types} render={titleCase} />
            <FilterSelect
              label="Verdict"
              value={verdict}
              onChange={setVerdict}
              options={['APPROVE', 'DENY', 'PEND']}
              render={(v) => (v === 'PEND' ? 'Investigate' : titleCase(v))}
            />
            <FilterSelect
              label="Disposition"
              value={disposition}
              onChange={setDisposition}
              options={dispositions}
              render={dispositionLabel}
            />
            <FilterSelect
              label="Decision"
              value={override}
              onChange={(v) => setOverride(v as OverrideFilter)}
              options={['overridden', 'accepted']}
              render={titleCase}
            />
          </div>

          {loading ? (
            <div className="rounded-lg border border-border bg-card">
              <LoadingRows rows={10} cols={columns.length} />
            </div>
          ) : forbidden ? (
            <EmptyState title="Not available" message="You don’t have access to claims history." />
          ) : error ? (
            <ErrorState message={error} onRetry={() => setReloadNonce((n) => n + 1)} />
          ) : rows.length === 0 ? (
            <EmptyState
              icon={<History className="h-6 w-6" />}
              title="No finalized claims yet"
              message="Decisions will appear here as claims are finalized."
            />
          ) : filtered.length === 0 ? (
            <EmptyState
              title="No matches"
              message="No finalized claims match the current filters."
              action={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setSearch('');
                    setType('all');
                    setVerdict('all');
                    setDisposition('all');
                    setOverride('all');
                  }}
                >
                  Clear filters
                </Button>
              }
            />
          ) : (
            <DataGrid
              rows={filtered}
              columns={columns}
              getRowId={(r) => r.claim_id}
              onRowActivate={(r) => setOpenClaim(r.claim_id)}
              activeRowId={openClaim ?? undefined}
              initialSort={{ key: 'finalized', dir: 'desc' }}
              ariaLabel="Claims history"
            />
          )}
        </div>

        {/* Role-aware NL panel */}
        <aside className="lg:sticky lg:top-6 lg:h-[calc(100vh-8rem)]">
          {role ? (
            <div className="flex h-[520px] flex-col overflow-hidden rounded-lg border border-border bg-card lg:h-full">
              <GenieAssistant
                alias={assistantAlias}
                title="Ask about past claims"
                description={assistantSpace}
                placeholder="Ask about finalized claims, outcomes, overrides…"
                className="h-full"
              />
            </div>
          ) : (
            <InlineNotice>Sign-in role unavailable — the claims assistant is offline.</InlineNotice>
          )}
        </aside>
      </div>

      <ClaimCockpit
        claimId={openClaim}
        onClose={() => setOpenClaim(null)}
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
