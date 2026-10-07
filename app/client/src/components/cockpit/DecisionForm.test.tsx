import { describe, it, expect, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Adjudication } from '@/lib/types';

/**
 * Bug 1 — opening an investigate claim threw
 * `TypeError: Cannot read properties of undefined (reading 'includes')` inside DecisionForm's
 * `validation` useMemo, because `adjudications.recommended_verdict` is the OPERATIONAL verdict
 * 'PEND' (confirmed live on fe-bar-ir-2026) and DISPOSITIONS_FOR has no 'PEND' key.
 *
 * These tests render the real component for a PEND recommendation and assert it does not throw.
 * The appkit-ui entry pulls echarts (which fails strict-ESM resolution under vitest's node env),
 * so the UI primitives and icons are stubbed to pass-through nodes; the component's own render +
 * useMemo logic — the crash site — runs for real. '@/lib/api' is stubbed so no network import loads.
 */
vi.mock('@databricks/appkit-ui/react', () => {
  const Passthrough = (props: { children?: unknown }) => props.children ?? null;
  return {
    Alert: Passthrough,
    AlertDescription: Passthrough,
    Button: Passthrough,
    Input: Passthrough,
    Label: Passthrough,
    RadioGroup: Passthrough,
    RadioGroupItem: Passthrough,
    Select: Passthrough,
    SelectContent: Passthrough,
    SelectItem: Passthrough,
    SelectTrigger: Passthrough,
    SelectValue: Passthrough,
    Textarea: Passthrough,
  };
});
vi.mock('lucide-react', () => {
  const Noop = () => null;
  return { AlertCircle: Noop, Check: Noop, Search: Noop };
});
vi.mock('@/lib/api', () => ({
  finalizeClaim: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

const { DecisionForm } = await import('./DecisionForm');

/** A RECOMMENDED investigate adjudication as the API/DB actually returns it (verdict is operational). */
function pendAdjudication(): Adjudication {
  return {
    claim_id: 'CLM-INVESTIGATE-1',
    recommended_verdict: 'PEND', // operational — the value that crashed the form
    recommended_disposition: 'PEND_INVESTIGATE',
    approved_amount: 0,
    claimed_amount: 12000,
    decision_status: 'RECOMMENDED',
  } as Adjudication;
}

describe('DecisionForm — investigate (PEND) claim', () => {
  it('renders without throwing for an operational PEND recommendation', () => {
    expect(() =>
      renderToStaticMarkup(<DecisionForm adjudication={pendAdjudication()} onFinalized={() => {}} />)
    ).not.toThrow();
  });

  it('normalizes the recommendation to the Investigate verdict in the header', () => {
    const html = renderToStaticMarkup(<DecisionForm adjudication={pendAdjudication()} onFinalized={() => {}} />);
    // verdictLabel('PEND_INVESTIGATE') === 'Investigate'; the recommendation line reads "Investigate · Investigate".
    expect(html).toContain('Investigate');
  });

  it('still renders cleanly for an APPROVE recommendation (no regression)', () => {
    const approve = {
      claim_id: 'CLM-APPROVE-1',
      recommended_verdict: 'APPROVE',
      recommended_disposition: 'CREDIT',
      approved_amount: 5000,
      claimed_amount: 9000,
      decision_status: 'RECOMMENDED',
    } as Adjudication;
    expect(() => renderToStaticMarkup(<DecisionForm adjudication={approve} onFinalized={() => {}} />)).not.toThrow();
  });
});
