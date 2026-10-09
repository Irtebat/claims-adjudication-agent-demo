// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import type { ReactNode } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import type { ClaimDetail } from '@/lib/types';
import { getClaim } from '@/lib/api';

/**
 * Scroll-region regression guard for the claim modal's DECISION rail (the primary half of
 * the chat-scroll fix). The decision/summary region is capped with `max-h-[55%] shrink-0
 * overflow-auto` so it becomes a real internal scroll region and can't crowd the Copilot
 * chat out of the rail / push it (and its own lower half) past the modal edge. A future
 * change that drops the cap would reintroduce the clipped-content bug, so we assert the
 * three classes co-exist on one element in the rendered DOM.
 *
 * jsdom has no layout engine, so this asserts the capped-scroll CLASSES survive rather than
 * measuring pixels. The decision region only renders after getClaim resolves, so we drive
 * the async effect with createRoot + act. appkit-ui's real entry pulls echarts (fails under
 * vitest) and the heavy children (DecisionForm, CopilotPanel/GenieChat, evidence) are
 * irrelevant here, so all are stubbed to inert nodes; @/lib/format stays real (pure).
 */
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('@databricks/appkit-ui/react', () => {
  const Passthrough = ({ children }: { children?: ReactNode }) => <>{children}</>;
  // Dialog/DialogContent must render their children so the inner grid + decision region
  // reach the DOM (the real Radix Dialog portals + gates on `open`; the stub always renders).
  return {
    Badge: Passthrough,
    Dialog: Passthrough,
    DialogContent: ({ children }: { children?: ReactNode }) => <div data-dialog-content>{children}</div>,
    DialogDescription: Passthrough,
    DialogHeader: Passthrough,
    DialogTitle: Passthrough,
  };
});
vi.mock('lucide-react', () => {
  const Noop = () => null;
  return { CircleCheck: Noop, Clock: Noop, TriangleAlert: Noop, UserCheck: Noop };
});
vi.mock('@/components/StatusChip', () => {
  const Noop = () => null;
  return { DecisionStatusChip: Noop, RiskFlags: Noop, VerdictChip: Noop, DispositionChip: Noop };
});
vi.mock('@/components/PageHeader', () => ({ MetaStat: () => null }));
vi.mock('@/components/States', () => ({ ErrorState: () => null, LoadingPanel: () => null }));
vi.mock('./evidence', () => ({
  SupportingSources: () => null,
  Citations: () => null,
  ContextPanel: () => null,
  SimilarClaims: () => null,
}));
vi.mock('./SourceDetailSheet', () => ({ SourceDetailSheet: () => null }));
vi.mock('./CopilotPanel', () => ({ CopilotPanel: () => <div data-testid="copilot" /> }));
vi.mock('./DecisionForm', () => ({ DecisionForm: () => <div data-testid="decision-form" /> }));

vi.mock('@/lib/api', () => ({ getClaim: vi.fn(), ApiError: class ApiError extends Error {} }));

const { ClaimCockpit } = await import('./ClaimCockpit');

/** A minimal RECOMMENDED detail — only the fields the cockpit header/facts read; the rest
 * degrade to the dash via @/lib/format, which never throws on null. */
function recommendedDetail(claimId: string): ClaimDetail {
  return {
    adjudication: {
      adjudication_id: 'ADJ-1',
      claim_id: claimId,
      decision_status: 'RECOMMENDED',
      recommended_verdict: 'APPROVE',
      recommended_disposition: 'CREDIT',
      approved_amount: 5000,
      claimed_amount: 9000,
      confidence: 0.9,
    } as ClaimDetail['adjudication'],
    decision_records: [],
    context: { heats_coils: null, mill_test_cert: null, customer: null, customer_heat_risk: null },
    prior_claims: [],
  };
}

describe('ClaimCockpit — decision rail scroll region', () => {
  it('caps the decision region with max-h-[55%] + shrink-0 + overflow-auto', async () => {
    vi.mocked(getClaim).mockResolvedValue(recommendedDetail('CLM-1'));

    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);

    // First act renders + fires the fetch effect; the second flushes the resolved getClaim
    // microtasks so setDetail re-renders the loaded (decision-region) branch.
    await act(async () => {
      root.render(<ClaimCockpit claimId="CLM-1" onClose={() => {}} />);
      await Promise.resolve();
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    const decisionRegion = Array.from(container.querySelectorAll('div')).find((d) =>
      d.className.includes('max-h-[55%]')
    );
    expect(decisionRegion, 'the capped decision region should render once the claim loads').toBeTruthy();

    const classes = new Set(decisionRegion!.className.split(/\s+/).filter(Boolean));
    // All three must co-exist: the cap (max-h-[55%]) makes overflow-auto a real scroll region;
    // shrink-0 keeps the natural content height up to that cap. Dropping any reopens the bug.
    expect(classes.has('max-h-[55%]')).toBe(true);
    expect(classes.has('shrink-0')).toBe(true);
    expect(classes.has('overflow-auto')).toBe(true);

    act(() => root.unmount());
    container.remove();
  });
});
