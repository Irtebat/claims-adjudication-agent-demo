import { describe, it, expect, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type {
  ConformanceEvidence,
  CoverageEvidence,
  SpecParams,
  MtcMeasured,
  WarrantyTerms,
  CoilSnapshot,
  ClaimInputSnapshot,
} from '@/lib/types';

/**
 * Feature A (spec-vs-measured) + Feature B (warranty proration) read-only detail views.
 *
 * These render the real ConformanceDetail / CoverageDetail components from frozen
 * authority-input snapshots and assert that: the table/derivation renders from sample data,
 * a non-conforming property is marked Fail (the verdict is PRESENTED from the persisted
 * `nonconforming_properties`, never recomputed), and the missing-data paths degrade to a
 * graceful notice instead of crashing (the defensive lesson from the just-fixed cockpit bug).
 *
 * The appkit-ui entry pulls echarts (which fails strict-ESM resolution under vitest's node
 * env), so the UI primitives and icons are stubbed to pass-through nodes; the components'
 * own render logic — including the per-property pass/fail derivation — runs for real.
 */
vi.mock('@databricks/appkit-ui/react', () => {
  const Passthrough = (props: { children?: unknown }) => props.children ?? null;
  return {
    Card: Passthrough,
    CardContent: Passthrough,
    CardHeader: Passthrough,
    CardTitle: Passthrough,
    Collapsible: Passthrough,
    CollapsibleContent: Passthrough,
    CollapsibleTrigger: Passthrough,
    Table: Passthrough,
    TableBody: Passthrough,
    TableCell: Passthrough,
    TableHead: Passthrough,
    TableHeader: Passthrough,
    TableRow: Passthrough,
    Tooltip: Passthrough,
    TooltipContent: Passthrough,
    TooltipTrigger: Passthrough,
    Badge: Passthrough,
    Alert: Passthrough,
    AlertDescription: Passthrough,
    AlertTitle: Passthrough,
    Button: Passthrough,
    Empty: Passthrough,
    EmptyContent: Passthrough,
    EmptyDescription: Passthrough,
    EmptyHeader: Passthrough,
    EmptyMedia: Passthrough,
    EmptyTitle: Passthrough,
    Skeleton: Passthrough,
  };
});
vi.mock('lucide-react', () => {
  const Noop = () => null;
  // Every icon referenced across evidence.tsx and its transitive imports
  // (EvidenceTooltip / StatusChip / States).
  return {
    ArrowUpRight: Noop,
    CheckCircle2: Noop,
    ChevronDown: Noop,
    FileText: Noop,
    MinusCircle: Noop,
    ShieldAlert: Noop,
    ShieldCheck: Noop,
    XCircle: Noop,
    Info: Noop,
    Copy: Noop,
    AlertCircle: Noop,
    RotateCw: Noop,
  };
});

const { ConformanceDetail, CoverageDetail } = await import('./evidence');

/** A full spec_params row as the authority resolved it (public.spec_params). */
function specParams(): SpecParams {
  return {
    carbon_pct_min: 0.0,
    carbon_pct_max: 0.18,
    manganese_pct_min: 0.0,
    manganese_pct_max: 0.7,
    yield_mpa_min: 140,
    yield_mpa_max: 350,
    tensile_mpa_min: 270,
    tensile_mpa_max: 550,
    elongation_pct_min: 20,
    elongation_pct_max: 60,
    gauge_tolerance_mm: 0.04,
    width_tolerance_mm: 2.0,
    min_coating_g_m2: 275,
    coating_adhesion_required: true,
  };
}

/** The coil MTC the authority measured (mtc_measured snapshot). */
function measured(): MtcMeasured {
  return {
    carbon_pct: 0.08,
    manganese_pct: 0.4,
    yield_mpa: 250,
    tensile_mpa: 400,
    elongation_pct: 30,
    gauge_mm: 1.01,
    ordered_gauge_mm: 1.0,
    width_mm: 1201,
    ordered_width_mm: 1200,
    coating_weight_g_m2: 280,
    coating_adhesion_pass: true,
  };
}

describe('ConformanceDetail — spec vs measured (Feature A)', () => {
  it('renders a spec-vs-measured row for each conformance-checked property', () => {
    const conformance: ConformanceEvidence = { conforms: false, nonconforming_properties: ['tensile_mpa'] };
    const html = renderToStaticMarkup(
      <ConformanceDetail conformance={conformance} specParams={specParams()} measured={measured()} />
    );
    // Property labels + the governing band and the measured value are shown.
    expect(html).toContain('Tensile strength');
    expect(html).toContain('Carbon');
    expect(html).toContain('270'); // tensile spec min
    expect(html).toContain('550'); // tensile spec max
    expect(html).toContain('400'); // tensile measured
    expect(html).toContain('MPa');
  });

  it('marks a non-conforming property Fail and a conforming one Pass (verdict presented, not recomputed)', () => {
    const conformance: ConformanceEvidence = { conforms: false, nonconforming_properties: ['tensile_mpa'] };
    const html = renderToStaticMarkup(
      <ConformanceDetail conformance={conformance} specParams={specParams()} measured={measured()} />
    );
    expect(html).toContain('Fail'); // tensile_mpa is in nonconforming_properties
    expect(html).toContain('Pass'); // carbon etc. evaluated and not listed
  });

  it('shows "Not checked" for a property the authority had no measured value for', () => {
    const conformance: ConformanceEvidence = { conforms: true, nonconforming_properties: [] };
    // Drop the carbon measurement — the authority skips it, so it is neither pass nor fail.
    const partial: MtcMeasured = { ...measured(), carbon_pct: null };
    const html = renderToStaticMarkup(
      <ConformanceDetail conformance={conformance} specParams={specParams()} measured={partial} />
    );
    expect(html).toContain('Not checked');
  });

  it('degrades to a graceful notice when the spec snapshot is unavailable (older records)', () => {
    const conformance: ConformanceEvidence = { conforms: true, nonconforming_properties: [] };
    expect(() =>
      renderToStaticMarkup(<ConformanceDetail conformance={conformance} specParams={null} measured={measured()} />)
    ).not.toThrow();
    const html = renderToStaticMarkup(
      <ConformanceDetail conformance={conformance} specParams={null} measured={measured()} />
    );
    expect(html).toContain('Spec-vs-measured detail');
  });
});

function warrantyTerms(): WarrantyTerms {
  return {
    duration_months: 240,
    full_coverage_months: 60,
    excluded_environments: ['marine', 'industrial_aggressive'],
    excluded_installations: ['unventilated', 'standing_water'],
    min_coast_distance_km: 2.0,
    freight_cap: 500,
    coating_class: 'Z275',
  };
}
function coil(): CoilSnapshot {
  return { coil_id: 'COIL-0000002', ship_date: '2014-01-01', coating_class: 'Z275' };
}
function claim(): ClaimInputSnapshot {
  return { claim_date: '2026-01-01', install_date: '2014-01-31', environment: 'inland', installation: 'ventilated' };
}

/**
 * A coverage struct whose numerics may be absent — mirrors a defensively-read older/malformed
 * jsonb row. The single assertion is sound because CoverageEvidence (numeric fields) is
 * assignable to this nullable-field literal type.
 */
function coverageWith(elapsed: number | null, proration: number | null, exclusions: string[] = []): CoverageEvidence {
  return {
    covered: true,
    elapsed_months: elapsed,
    proration_factor: proration,
    exclusions_hit: exclusions,
  } as CoverageEvidence;
}

describe('CoverageDetail — warranty proration derivation (Feature B)', () => {
  it('renders the proration derivation and its inputs from sample data', () => {
    const coverage: CoverageEvidence = {
      covered: true,
      elapsed_months: 144,
      proration_factor: 0.533333,
      exclusions_hit: [],
    };
    const html = renderToStaticMarkup(
      <CoverageDetail coverage={coverage} warrantyTerms={warrantyTerms()} coil={coil()} claim={claim()} />
    );
    expect(html).toContain('In service');
    expect(html).toContain('144'); // elapsed months
    expect(html).toContain('240'); // warranty duration
    expect(html).toContain('60'); // full-coverage threshold
    expect(html).toContain('53%'); // proration factor, presented from the struct
    expect(html).toContain('Z275'); // coating class
  });

  it('humanizes the exclusions hit', () => {
    const coverage: CoverageEvidence = {
      covered: false,
      elapsed_months: 300,
      proration_factor: 0,
      exclusions_hit: ['environment:marine', 'coast_distance', 'duration_expired'],
    };
    const html = renderToStaticMarkup(
      <CoverageDetail coverage={coverage} warrantyTerms={warrantyTerms()} coil={coil()} claim={claim()} />
    );
    expect(html).toContain('Excluded environment: marine');
    expect(html).toContain('Coast distance below minimum');
    expect(html).toContain('Warranty duration expired');
  });

  it('still shows the computed figures (with a note) when the warranty-terms snapshot is missing', () => {
    const coverage: CoverageEvidence = {
      covered: true,
      elapsed_months: 12,
      proration_factor: 1,
      exclusions_hit: [],
    };
    const html = renderToStaticMarkup(
      <CoverageDetail coverage={coverage} warrantyTerms={null} coil={null} claim={null} />
    );
    expect(html).toContain('Full warranty terms'); // the graceful partial note
    expect(html).toContain('12'); // elapsed still shown from the coverage struct
  });

  it('degrades to a graceful notice when no derivation inputs are available at all', () => {
    const coverage = coverageWith(null, null);
    expect(() =>
      renderToStaticMarkup(<CoverageDetail coverage={coverage} warrantyTerms={null} coil={null} claim={null} />)
    ).not.toThrow();
    const html = renderToStaticMarkup(
      <CoverageDetail coverage={coverage} warrantyTerms={null} coil={null} claim={null} />
    );
    expect(html).toContain('Proration derivation detail');
  });
});
