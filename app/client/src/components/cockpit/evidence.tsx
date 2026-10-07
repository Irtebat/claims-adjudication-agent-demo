/**
 * The cockpit's read-only evidence surfaces: the four deterministic supporting sources
 * (MTC conformance, warranty coverage, duplicate check, settlement calculation) — each
 * with a tooltip explaining that authority's approach — plus the cited clauses, the
 * coil/heat/MTC context with the heat-risk flag, and the similar-prior-claims table.
 *
 * All values are read from the ALREADY-PERSISTED decision record and context payload
 * (never a live model call). The deterministic structs are preserved forward onto the
 * human-final record, so the same evidence renders for RECOMMENDED and FINAL claims.
 */

import type { ReactNode } from 'react';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@databricks/appkit-ui/react';
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  FileText,
  MinusCircle,
  ShieldAlert,
  ShieldCheck,
  XCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { EvidenceInfo } from '@/components/EvidenceTooltip';
import { VerdictChip } from '@/components/StatusChip';
import { EmptyState, InlineNotice } from '@/components/States';
import { EVIDENCE_APPROACH } from '@/lib/evidence';
import { DASH, humanizeKey, money, num, pct, scalar, shortDate, toNum } from '@/lib/format';
import type {
  CockpitContext,
  DecisionRecord,
  PriorClaim,
  ClaimInputSnapshot,
  CoilSnapshot,
  ConformanceEvidence,
  CoverageEvidence,
  DuplicateEvidence,
  MtcMeasured,
  SettlementEvidence,
  SpecParams,
  WarrantyTerms,
  SourceKind,
  SourceTarget,
  Num,
} from '@/lib/types';

/** Callback that opens the source drill-through panel for one evidence item. */
type OpenSource = (target: SourceTarget) => void;

/** First non-empty string/number field of a jsonb row, coerced to string (else null). */
function idField(row: Record<string, unknown> | null | undefined, key: string): string | null {
  const v = row?.[key];
  if (typeof v === 'string' && v !== '') return v;
  if (typeof v === 'number') return String(v);
  return null;
}

/**
 * A supporting-source row. When `onOpen` is provided it becomes a real, focusable button
 * that drills through to the underlying row; otherwise it is a plain panel. A small
 * arrow-out glyph signals the affordance without shouting for attention.
 */
function EvidenceRow({
  onOpen,
  ariaLabel,
  className,
  children,
}: {
  onOpen?: () => void;
  ariaLabel?: string;
  className?: string;
  children: ReactNode;
}) {
  const base = 'rounded-md border border-border bg-card px-3 py-2';
  if (!onOpen) return <div className={cn(base, className)}>{children}</div>;
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={ariaLabel}
      className={cn(
        base,
        'group relative w-full text-left transition-colors hover:border-primary/50 hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        className
      )}
    >
      <ArrowUpRight
        className="absolute right-2 top-2 h-3.5 w-3.5 text-muted-foreground/50 transition-colors group-hover:text-primary"
        aria-hidden
      />
      {children}
    </button>
  );
}

/** A compact label/value stat used across the evidence cards. */
function Stat({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-0.5', className)}>
      <span className="text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-foreground tabular-nums">{value}</span>
    </div>
  );
}

/** A binary determination pill (e.g. Conforms / Non-conforming). */
function Determination({ ok, positive, negative }: { ok: boolean; positive: string; negative: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold',
        ok ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive'
      )}
    >
      {ok ? <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> : <XCircle className="h-3.5 w-3.5" aria-hidden />}
      {ok ? positive : negative}
    </span>
  );
}

function EvidenceCard({
  title,
  approachKey,
  determination,
  children,
}: {
  title: string;
  approachKey: keyof typeof EVIDENCE_APPROACH;
  determination?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="flex-row items-center justify-between gap-2 space-y-0 border-b border-border px-4 py-2.5">
        <CardTitle className="flex items-center gap-1.5 text-sm font-semibold">
          {title}
          <EvidenceInfo label={title} content={EVIDENCE_APPROACH[approachKey]} />
        </CardTitle>
        {determination}
      </CardHeader>
      <CardContent className="px-4 py-3">{children}</CardContent>
    </Card>
  );
}

function TagList({
  items,
  tone = 'neutral',
  onItem,
}: {
  items: string[];
  tone?: 'neutral' | 'warn';
  /** When provided, each tag becomes a clickable drill-through affordance. */
  onItem?: (item: string) => void;
}) {
  if (!items.length) return <span className="text-sm text-muted-foreground">None</span>;
  const toneCls =
    tone === 'warn'
      ? 'border-warning/30 bg-warning/10 text-warning'
      : 'border-border bg-secondary text-secondary-foreground';
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((it) =>
        onItem ? (
          <button
            key={it}
            type="button"
            onClick={() => onItem(it)}
            aria-label={`Open source row for clause ${it}`}
            className={cn(
              'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-xs transition-colors hover:border-primary/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
              toneCls
            )}
          >
            {it}
            <ArrowUpRight className="h-3 w-3 opacity-60" aria-hidden />
          </button>
        ) : (
          <span key={it} className={cn('rounded-sm border px-1.5 py-0.5 font-mono text-xs', toneCls)}>
            {it}
          </span>
        )
      )}
    </div>
  );
}

/**
 * A collapsed-by-default disclosure for an evidence card's extra detail. The affordance is
 * a real focusable button (keyboard + screen-reader reachable); the chevron rotates on open.
 * Kept understated (small label, hairline separator above) so the card stays calm until the
 * adjuster asks for more.
 */
function ExpandSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Collapsible className="mt-3 border-t border-border pt-2.5">
      <CollapsibleTrigger className="group flex w-full items-center justify-between gap-2 rounded-[5px] text-xs font-medium text-primary transition-colors hover:text-primary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <span>{label}</span>
        <ChevronDown
          className="h-3.5 w-3.5 transition-transform duration-200 group-data-[state=open]:rotate-180 motion-reduce:transition-none"
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2.5">{children}</CollapsibleContent>
    </Collapsible>
  );
}

/** The per-property pass/fail/not-checked marker, read from the authority's verdict (never recomputed). */
function ResultPill({ result }: { result: 'pass' | 'fail' | 'none' }) {
  if (result === 'none') return <span className="text-xs text-muted-foreground">Not checked</span>;
  const ok = result === 'pass';
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-semibold',
        ok ? 'border-success/30 bg-success/10 text-success' : 'border-destructive/30 bg-destructive/10 text-destructive'
      )}
    >
      {ok ? <CheckCircle2 className="h-3 w-3" aria-hidden /> : <XCircle className="h-3 w-3" aria-hidden />}
      {ok ? 'Pass' : 'Fail'}
    </span>
  );
}

// --- Feature A: conformance spec-vs-measured detail --------------------------
//
// The rows mirror the exact checks in authorities.compute_conformance (chemistry +
// mechanicals as min/max bands, gauge/width as ordered ± tolerance, coating weight as a
// minimum, coating adhesion as a required pass). The spec requirement and measured value
// are PRESENTED from the frozen spec_params / mtc_measured snapshot the authority used; the
// pass/fail marker is DERIVED from the persisted `nonconforming_properties` list, so the
// verdict is shown, never recomputed. A property whose measured value the authority did not
// have is shown as "Not checked".

type SpecKind = 'range' | 'tolerance' | 'min' | 'adhesion';
interface ConformanceRowSpec {
  /** Must match a `nonconforming_properties` token exactly (agent/src/authorities.py). */
  key: string;
  label: string;
  unit: string;
  kind: SpecKind;
}

const CONFORMANCE_ROWS: ConformanceRowSpec[] = [
  { key: 'carbon_pct', label: 'Carbon', unit: '%', kind: 'range' },
  { key: 'manganese_pct', label: 'Manganese', unit: '%', kind: 'range' },
  { key: 'yield_mpa', label: 'Yield strength', unit: 'MPa', kind: 'range' },
  { key: 'tensile_mpa', label: 'Tensile strength', unit: 'MPa', kind: 'range' },
  { key: 'elongation_pct', label: 'Elongation', unit: '%', kind: 'range' },
  { key: 'gauge_mm', label: 'Gauge', unit: 'mm', kind: 'tolerance' },
  { key: 'width_mm', label: 'Width', unit: 'mm', kind: 'tolerance' },
  { key: 'coating_weight_g_m2', label: 'Coating weight', unit: 'g/m²', kind: 'min' },
  { key: 'coating_adhesion', label: 'Coating adhesion', unit: '', kind: 'adhesion' },
];

/** A jsonb numeric formatted to ≤3 dp, or null when absent/non-finite. */
function n3(v: Num | undefined): string | null {
  const x = toNum(v);
  return x === null ? null : x.toLocaleString('en-US', { maximumFractionDigits: 3 });
}

interface ConformanceRow {
  key: string;
  label: string;
  specText: string;
  measuredText: string;
  result: 'pass' | 'fail' | 'none';
}

/** A property's verdict: fail if the authority listed it, else pass when it was evaluated. */
function resultOf(key: string, evaluated: boolean, nonconforming: string[]): 'pass' | 'fail' | 'none' {
  if (nonconforming.includes(key)) return 'fail';
  return evaluated ? 'pass' : 'none';
}

function buildConformanceRow(
  spec: ConformanceRowSpec,
  sp: SpecParams,
  m: MtcMeasured,
  nonconforming: string[]
): ConformanceRow {
  const { key, label, unit, kind } = spec;
  const withUnit = (s: string | null) => (s === null ? DASH : unit ? `${s} ${unit}` : s);
  let specText = DASH;
  let measuredText = DASH;
  let evaluated = false;

  if (kind === 'range') {
    const lo = n3(sp[`${key}_min`] as Num);
    const hi = n3(sp[`${key}_max`] as Num);
    specText = lo !== null && hi !== null ? withUnit(`${lo}–${hi}`) : DASH;
    measuredText = withUnit(n3(m[key] as Num));
    evaluated = toNum(m[key] as Num) !== null;
  } else if (kind === 'tolerance') {
    const orderedKey = key === 'gauge_mm' ? 'ordered_gauge_mm' : 'ordered_width_mm';
    const tolKey = key === 'gauge_mm' ? 'gauge_tolerance_mm' : 'width_tolerance_mm';
    const ordered = n3(m[orderedKey] as Num);
    const tol = n3(sp[tolKey] as Num);
    specText =
      ordered !== null && tol !== null ? withUnit(`${ordered} ± ${tol}`) : tol !== null ? withUnit(`± ${tol}`) : DASH;
    measuredText = withUnit(n3(m[key] as Num));
    evaluated = toNum(m[key] as Num) !== null && toNum(m[orderedKey] as Num) !== null;
  } else if (kind === 'min') {
    const min = n3(sp.min_coating_g_m2);
    specText = min !== null ? withUnit(`≥ ${min}`) : DASH;
    measuredText = withUnit(n3(m.coating_weight_g_m2));
    evaluated = toNum(m.coating_weight_g_m2) !== null;
  } else {
    const required = sp.coating_adhesion_required === true;
    specText = sp.coating_adhesion_required == null ? DASH : required ? 'Pass required' : 'Not required';
    const pass = m.coating_adhesion_pass;
    measuredText = pass == null ? DASH : pass ? 'Pass' : 'Fail';
    evaluated = pass != null && required;
  }

  return { key, label, specText, measuredText, result: resultOf(key, evaluated, nonconforming) };
}

/**
 * Spec-vs-measured table for the MTC conformance card. Requires both the frozen spec_params
 * and mtc_measured snapshots; when either is absent (older records) it degrades to a clear
 * "detail unavailable" notice rather than rendering an empty or misleading table.
 */
export function ConformanceDetail({
  conformance,
  specParams,
  measured,
}: {
  conformance: ConformanceEvidence;
  specParams: SpecParams | null;
  measured: MtcMeasured | null;
}) {
  if (
    specParams == null ||
    measured == null ||
    Object.keys(specParams).length === 0 ||
    Object.keys(measured).length === 0
  ) {
    return <InlineNotice>Spec-vs-measured detail isn’t recorded on this decision record.</InlineNotice>;
  }
  const nonconforming = conformance.nonconforming_properties ?? [];
  const rows = CONFORMANCE_ROWS.map((s) => buildConformanceRow(s, specParams, measured, nonconforming));
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8">Property</TableHead>
            <TableHead className="h-8">Spec requirement</TableHead>
            <TableHead className="h-8 text-right">Measured</TableHead>
            <TableHead className="h-8 text-right">Result</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.key} className={cn(r.result === 'fail' && 'bg-destructive/5')}>
              <TableCell className="py-1.5 font-medium text-foreground">{r.label}</TableCell>
              <TableCell className="py-1.5 text-muted-foreground tabular-nums">{r.specText}</TableCell>
              <TableCell className="py-1.5 text-right text-foreground tabular-nums">{r.measuredText}</TableCell>
              <TableCell className="py-1.5 text-right">
                <ResultPill result={r.result} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// --- Feature B: warranty coverage / proration detail -------------------------

/** Turn an authority exclusion token (e.g. `environment:marine`) into readable text. */
function humanizeExclusion(token: string): string {
  if (token === 'duration_expired') return 'Warranty duration expired';
  if (token === 'coast_distance') return 'Coast distance below minimum';
  const idx = token.indexOf(':');
  if (idx > 0) {
    const kind = token.slice(0, idx);
    const value = token.slice(idx + 1);
    if (kind === 'environment') return `Excluded environment: ${value}`;
    if (kind === 'installation') return `Excluded installation: ${value}`;
  }
  return humanizeKey(token);
}

/**
 * Warranty coverage detail: the exclusions evaluated plus the proration derivation shown
 * with its inputs (ship/claim/install dates, coating class, duration + full-coverage
 * threshold, elapsed months). The elapsed months and proration factor are PRESENTED from
 * the persisted coverage struct — never recomputed. When the warranty-terms snapshot is
 * missing (older records) it still shows the computed coverage figures with a clear note;
 * when nothing is available it degrades to a "detail unavailable" notice.
 */
export function CoverageDetail({
  coverage,
  warrantyTerms,
  coil,
  claim,
}: {
  coverage: CoverageEvidence;
  warrantyTerms: WarrantyTerms | null;
  coil: CoilSnapshot | null;
  claim: ClaimInputSnapshot | null;
}) {
  const exclusions = coverage.exclusions_hit ?? [];
  const duration = toNum(warrantyTerms?.duration_months);
  const full = toNum(warrantyTerms?.full_coverage_months);
  const elapsed = toNum(coverage.elapsed_months);
  const proration = coverage.proration_factor;
  const shipDate = coil?.ship_date ?? null;
  const claimDate = claim?.claim_date ?? null;
  const installDate = claim?.install_date ?? null;
  const coatingClass = coil?.coating_class ?? warrantyTerms?.coating_class ?? null;
  const hasTerms = duration !== null || full !== null;
  const canDerive = hasTerms || elapsed !== null || toNum(proration) !== null;

  return (
    <div className="space-y-3">
      <div>
        <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
          Exclusions evaluated
        </p>
        <TagList items={exclusions.map(humanizeExclusion)} tone="warn" />
      </div>

      {canDerive ? (
        <div className="rounded-md border border-border bg-muted/30 p-3">
          <p className="text-sm leading-relaxed text-foreground">
            In service <span className="font-semibold tabular-nums">{num(elapsed, 0)}</span> of{' '}
            <span className="font-semibold tabular-nums">{num(duration, 0)}</span> warranty months
            {full !== null && <> (full coverage through {num(full, 0)} months)</>}
            <span className="mx-1.5 text-muted-foreground">→</span>
            proration <span className="font-semibold tabular-nums">{pct(proration)}</span>
          </p>
          <div className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
            <Stat label="Ship date" value={shortDate(shipDate)} />
            <Stat label="Claim date" value={shortDate(claimDate)} />
            <Stat label="Install date" value={shortDate(installDate)} />
            <Stat label="Coating class" value={coatingClass ?? DASH} />
            <Stat label="Warranty duration" value={duration !== null ? `${num(duration, 0)} mo` : DASH} />
            <Stat label="Full-coverage window" value={full !== null ? `${num(full, 0)} mo` : DASH} />
          </div>
          {!hasTerms && (
            <p className="mt-2.5 text-xs text-muted-foreground">
              Full warranty terms aren’t recorded on this version; showing the computed coverage figures only.
            </p>
          )}
        </div>
      ) : (
        <InlineNotice>Proration derivation detail isn’t recorded on this decision record.</InlineNotice>
      )}
    </div>
  );
}

export function SupportingSources({ record }: { record: DecisionRecord | null }) {
  const conformance: ConformanceEvidence | null = record?.conformance ?? null;
  const coverage: CoverageEvidence | null = record?.coverage ?? null;
  const duplicate: DuplicateEvidence | null = record?.duplicate ?? null;
  const settlement: SettlementEvidence | null = record?.settlement ?? null;

  if (!conformance && !coverage && !duplicate && !settlement) {
    return <EmptyState title="No deterministic evidence" message="This claim has no persisted evidence structs." />;
  }

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <EvidenceCard
        title="MTC conformance"
        approachKey="conformance"
        determination={
          conformance && <Determination ok={conformance.conforms} positive="Conforms" negative="Non-conforming" />
        }
      >
        {conformance ? (
          <>
            <Stat
              label="Non-conforming properties"
              value={<TagList items={conformance.nonconforming_properties ?? []} tone="warn" />}
            />
            <ExpandSection label="Spec vs measured">
              <ConformanceDetail
                conformance={conformance}
                specParams={record?.spec_params ?? null}
                measured={record?.mtc_measured ?? null}
              />
            </ExpandSection>
          </>
        ) : (
          <span className="text-sm text-muted-foreground">{DASH}</span>
        )}
      </EvidenceCard>

      <EvidenceCard
        title="Warranty coverage"
        approachKey="coverage"
        determination={coverage && <Determination ok={coverage.covered} positive="Covered" negative="Not covered" />}
      >
        {coverage ? (
          <>
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Elapsed" value={`${num(coverage.elapsed_months, 0)} mo`} />
              <Stat label="Proration" value={pct(coverage.proration_factor)} />
              <Stat
                label="Exclusions hit"
                value={<TagList items={coverage.exclusions_hit ?? []} tone="warn" />}
                className="col-span-2"
              />
            </div>
            <ExpandSection label="Coverage detail">
              <CoverageDetail
                coverage={coverage}
                warrantyTerms={record?.warranty_terms ?? null}
                coil={record?.coil ?? null}
                claim={record?.claim_input ?? null}
              />
            </ExpandSection>
          </>
        ) : (
          <span className="text-sm text-muted-foreground">{DASH}</span>
        )}
      </EvidenceCard>

      <EvidenceCard
        title="Duplicate check"
        approachKey="duplicate"
        determination={
          duplicate && <Determination ok={!duplicate.is_duplicate} positive="Unique" negative="Duplicate" />
        }
      >
        {duplicate ? (
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Similarity" value={pct(duplicate.narrative_similarity)} />
            <Stat label="Candidates" value={num(duplicate.candidates_considered, 0)} />
            <Stat
              label="Duplicate of"
              value={
                duplicate.duplicate_of_claim_id ? (
                  <span className="font-mono">{duplicate.duplicate_of_claim_id}</span>
                ) : (
                  DASH
                )
              }
              className="col-span-2"
            />
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">{DASH}</span>
        )}
      </EvidenceCard>

      <EvidenceCard
        title="Settlement calculation"
        approachKey="settlement"
        determination={
          settlement?.over_claim_detected ? (
            <span className="inline-flex items-center gap-1 rounded-sm border border-warning/30 bg-warning/10 px-1.5 py-0.5 text-xs font-semibold text-warning">
              <MinusCircle className="h-3.5 w-3.5" aria-hidden />
              Over-claim
            </span>
          ) : undefined
        }
      >
        {settlement ? (
          <div className="grid grid-cols-2 gap-3">
            <Stat
              label="Approved"
              value={<span className="text-base font-bold">{money(settlement.approved_amount)}</span>}
            />
            <Stat label="Claimed" value={money(settlement.claimed_amount)} />
            <Stat label="Covered tonnage" value={`${num(settlement.covered_tonnage)} t`} />
            <Stat
              label="Freight"
              value={settlement.freight_covered ? money(settlement.freight_amount) : 'Not covered'}
            />
            <Stat label="Partial settlement" value={settlement.is_partial ? 'Yes' : 'No'} className="col-span-2" />
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">{DASH}</span>
        )}
      </EvidenceCard>
    </div>
  );
}

export function Citations({
  record,
  claimType,
  onOpenSource,
}: {
  record: DecisionRecord | null;
  /** Selects the clause corpus: 'coating_warranty' -> warranty_clauses, else spec_clauses
   * (mirrors the agent's retrieval, agent/src/agent_tools.py). */
  claimType?: string | null;
  onOpenSource?: OpenSource;
}) {
  const citations = record?.citations ?? [];
  const clauseIds = record?.cited_clause_ids ?? [];
  if (citations.length === 0 && clauseIds.length === 0) {
    return <EmptyState title="No citations" message="The recommendation cited no specification or warranty clauses." />;
  }
  // A claim's clauses all come from ONE corpus, keyed on its claim_type — so a citation_key
  // resolves to exactly one of the two clause tables.
  const clauseSource: SourceKind = claimType === 'coating_warranty' ? 'warranty_clauses' : 'spec_clauses';
  const clauseLabel = clauseSource === 'warranty_clauses' ? 'Warranty clause' : 'Spec clause';
  const openClause = (id: string, sectionRef?: string | null) =>
    onOpenSource?.({ source: clauseSource, id, title: `${clauseLabel} · ${sectionRef || id}` });
  return (
    <div className="space-y-2">
      {citations.map((c) => (
        <EvidenceRow
          key={`${c.citation_key}-${c.section_ref}`}
          onOpen={onOpenSource ? () => openClause(c.citation_key, c.section_ref) : undefined}
          ariaLabel={`Open source row for ${clauseLabel.toLowerCase()} ${c.citation_key}`}
        >
          <div className="flex items-start gap-2">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 pr-4">
              <p className="text-sm font-medium text-foreground">{c.citation_key}</p>
              <p className="font-mono text-xs text-muted-foreground">{c.section_ref}</p>
            </div>
          </div>
        </EvidenceRow>
      ))}
      {clauseIds.length > 0 && (
        <div>
          <p className="mb-1 text-[0.68rem] font-medium uppercase tracking-wide text-muted-foreground">
            Cited clause IDs
          </p>
          <TagList items={clauseIds} onItem={onOpenSource ? (id) => openClause(id) : undefined} />
        </div>
      )}
    </div>
  );
}

/** A compact key/value list rendering whatever columns a jsonb context row carries. */
function ContextRow({
  title,
  row,
  onOpen,
}: {
  title: string;
  row: Record<string, unknown> | null;
  /** When provided, a "View full row" affordance drills through to the source table. */
  onOpen?: () => void;
}) {
  const entries = row ? Object.entries(row).filter(([, v]) => v !== null && v !== undefined && v !== '') : [];
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <p className="text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">{title}</p>
        {onOpen && entries.length > 0 && (
          <button
            type="button"
            onClick={onOpen}
            aria-label={`Open the full ${title} source row`}
            className="inline-flex items-center gap-1 rounded-[5px] text-xs font-medium text-primary transition-colors hover:text-primary/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            View full row
            <ArrowUpRight className="h-3 w-3" aria-hidden />
          </button>
        )}
      </div>
      {entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">Not available</p>
      ) : (
        <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
          {entries.map(([k, v]) => (
            <div key={k} className="flex items-baseline justify-between gap-3 border-b border-border/60 py-0.5">
              <dt className="text-xs text-muted-foreground">{humanizeKey(k)}</dt>
              <dd className="truncate text-sm font-medium text-foreground tabular-nums" title={scalar(v)}>
                {scalar(v)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

export function ContextPanel({ context, onOpenSource }: { context: CockpitContext; onOpenSource?: OpenSource }) {
  // Gate on the gold `high_risk` column: an alarming banner appears ONLY when it is strictly
  // true (its `risk_reason` string is the explanation). A pairing that was assessed but not
  // flagged gets a quiet, neutral marker instead — never the alarming treatment. Both fields
  // are read defensively (they may be absent on rows predating the fraud workstream).
  const chr = context.customer_heat_risk;
  const highRisk = chr?.high_risk === true;
  const riskReason = typeof chr?.risk_reason === 'string' ? chr.risk_reason : null;
  const riskScore = chr?.risk_score;

  // Each context source drills through by its own primary key: coil_id / cert_id /
  // customer_id, and customer_heat_risk by its composite (customer_id, heat_no). The
  // affordance appears only when the identifying key is present in the fetched row.
  const coilId = idField(context.heats_coils, 'coil_id');
  const certId = idField(context.mill_test_cert, 'cert_id');
  const customerId = idField(context.customer, 'customer_id');
  const chrCustomerId = idField(chr, 'customer_id');
  const chrHeatNo = idField(chr, 'heat_no');
  const openRisk =
    onOpenSource && chrCustomerId && chrHeatNo
      ? () =>
          onOpenSource({
            source: 'customer_heat_risk',
            id: chrCustomerId,
            extra: { heat_no: chrHeatNo },
            title: 'Customer–heat risk',
          })
      : undefined;

  return (
    <div className="space-y-4">
      {highRisk ? (
        <EvidenceRow
          onOpen={openRisk}
          ariaLabel="Open the customer–heat risk source row"
          className="border-warning/40 bg-warning/12 hover:border-warning/60 hover:bg-warning/15"
        >
          <div className="flex items-start gap-2 pr-4 text-sm text-warning">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="font-semibold">High fraud risk</p>
              <p className="text-warning/90">{riskReason ?? 'This customer and heat form a flagged fraud cluster.'}</p>
            </div>
          </div>
        </EvidenceRow>
      ) : chr ? (
        <EvidenceRow
          onOpen={openRisk}
          ariaLabel="Open the customer–heat risk source row"
          className="bg-muted/40 hover:bg-muted/60"
        >
          <div className="flex items-start gap-2 pr-4 text-sm text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
            <div className="min-w-0">
              <p className="font-medium text-foreground">Customer–heat risk assessed</p>
              <p>Not flagged as high-risk{riskScore != null ? ` · risk ${pct(riskScore)}` : ''}.</p>
            </div>
          </div>
        </EvidenceRow>
      ) : null}
      <ContextRow
        title="Coil / heat"
        row={context.heats_coils}
        onOpen={
          onOpenSource && coilId
            ? () => onOpenSource({ source: 'heats_coils', id: coilId, title: `Coil / heat · ${coilId}` })
            : undefined
        }
      />
      <ContextRow
        title="Mill test certificate"
        row={context.mill_test_cert}
        onOpen={
          onOpenSource && certId
            ? () => onOpenSource({ source: 'mill_test_certs', id: certId, title: `Mill test certificate · ${certId}` })
            : undefined
        }
      />
      <ContextRow
        title="Customer"
        row={context.customer}
        onOpen={
          onOpenSource && customerId
            ? () => onOpenSource({ source: 'customers', id: customerId, title: `Customer · ${customerId}` })
            : undefined
        }
      />
    </div>
  );
}

export function SimilarClaims({
  claims,
  compact = false,
  onOpenSource,
}: {
  claims: PriorClaim[];
  compact?: boolean;
  onOpenSource?: OpenSource;
}) {
  if (!claims.length) {
    return (
      <EmptyState
        title="No similar prior claims"
        message="No precedent claims were referenced for this coil and defect."
      />
    );
  }
  return (
    <div className="space-y-2">
      {claims.map((c) => (
        <EvidenceRow
          key={c.claim_id}
          onOpen={
            onOpenSource
              ? () => onOpenSource({ source: 'prior_claims', id: c.claim_id, title: `Prior claim · ${c.claim_id}` })
              : undefined
          }
          ariaLabel={`Open source row for prior claim ${c.claim_id}`}
        >
          <div className="flex items-center justify-between gap-2 pr-4">
            <span className="flex items-center gap-1.5 font-mono text-xs font-medium text-foreground">
              <FileText className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              {c.claim_id}
            </span>
            <VerdictChip verdict={c.verdict} />
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
            <span>{c.defect_code ?? DASH}</span>
            <span>{shortDate(c.claim_date)}</span>
            <span className="tabular-nums">{money(c.approved_amount)}</span>
            {!compact && c.grade && <span>Grade {c.grade}</span>}
          </div>
          {!compact && c.defect_narrative && (
            <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{c.defect_narrative}</p>
          )}
        </EvidenceRow>
      ))}
    </div>
  );
}
