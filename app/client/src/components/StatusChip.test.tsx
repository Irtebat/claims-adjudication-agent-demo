import { describe, it, expect, vi } from 'vitest';
import type { ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

/**
 * Render RiskFlags in isolation. The real @databricks/appkit-ui/react entry transitively loads
 * echarts-for-react, which fails Node's strict-ESM resolution under vitest's node environment;
 * it is irrelevant to RiskFlags, so we stub the few primitives the component uses (and the
 * lucide icons) down to pass-through nodes. renderToStaticMarkup then exercises the component's
 * real gating logic without the chart bundle.
 */
vi.mock('@databricks/appkit-ui/react', () => {
  const Passthrough = (props: { children?: unknown }) => props.children ?? null;
  return { Badge: Passthrough, Tooltip: Passthrough, TooltipContent: Passthrough, TooltipTrigger: Passthrough };
});
vi.mock('lucide-react', () => {
  const Noop = () => null;
  return { Copy: Noop, ShieldAlert: Noop };
});

// Imported AFTER the mocks are registered (vi.mock is hoisted above imports).
const { RiskFlags } = await import('./StatusChip');

/**
 * Bug 2 — the "Fraud cluster" chip showed on nearly every claim (even at risk 0) because it was
 * gated on `fraud_cluster_id`, a graph-component id present on virtually all claims. RiskFlags
 * now renders a SINGLE fraud indicator — "High risk" — gated strictly on the tuned `highRisk`
 * gold flag, with no `fraud_cluster_id`-presence chip and no `fraudCluster` prop at all.
 */
function markup(el: ReactElement): string {
  return renderToStaticMarkup(el);
}

describe('RiskFlags fraud gating', () => {
  it('renders nothing when there is no duplicate and high_risk is false', () => {
    expect(markup(<RiskFlags highRisk={false} />)).toBe('');
  });

  it('renders nothing when high_risk is absent (undefined)', () => {
    expect(markup(<RiskFlags />)).toBe('');
  });

  it('does NOT render a fraud chip when high_risk is false', () => {
    const html = markup(<RiskFlags highRisk={false} />);
    expect(html).not.toContain('High risk');
    expect(html).not.toContain('Fraud cluster');
  });

  it('renders the "High risk" chip when high_risk is strictly true', () => {
    expect(markup(<RiskFlags highRisk={true} />)).toContain('High risk');
  });

  it('renders the "High risk" chip (with reason) when high_risk is true', () => {
    const html = markup(<RiskFlags highRisk={true} riskReason="Fraud cluster: 10 claims from 5 customers." />);
    expect(html).toContain('High risk');
  });

  it('never renders a standalone "Fraud cluster" chip (the removed, over-eager marker)', () => {
    expect(markup(<RiskFlags highRisk={true} />)).not.toContain('Fraud cluster');
    expect(markup(<RiskFlags highRisk={false} />)).not.toContain('Fraud cluster');
    expect(markup(<RiskFlags duplicateOf="CLM-1" highRisk={false} />)).not.toContain('Fraud cluster');
  });

  it('still shows the neutral Duplicate chip independently of fraud risk', () => {
    const html = markup(<RiskFlags duplicateOf="CLM-123" highRisk={false} />);
    expect(html).toContain('Duplicate');
    expect(html).not.toContain('High risk');
  });
});
