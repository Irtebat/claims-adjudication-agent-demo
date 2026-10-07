// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import type { ReactNode } from 'react';
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Adjudication } from '@/lib/types';
import { finalizeClaim } from '@/lib/api';

/**
 * Bug 1 — opening an investigate claim threw
 * `TypeError: Cannot read properties of undefined (reading 'includes')` inside DecisionForm's
 * `validation` useMemo, because `adjudications.recommended_verdict` is the OPERATIONAL verdict
 * 'PEND' (confirmed live on fe-bar-ir-2026) and DISPOSITIONS_FOR has no 'PEND' key.
 *
 * These tests (a) render the real component for a PEND recommendation and assert it does not
 * throw, and (b) actually DRIVE submit() by clicking "Accept recommendation" and assert the
 * posted payload is one server/finalize.ts accepts for an unchanged investigate hold —
 * verdict PEND_INVESTIGATE, disposition PEND_INVESTIGATE, amount 0, and no override reason
 * (its finalize-side acceptance, differs=false, is asserted in server/finalize.test.ts).
 *
 * The appkit-ui entry pulls echarts (which fails strict-ESM resolution under vitest's node env),
 * so the UI primitives and icons are stubbed to pass-through nodes — Button to a real <button>
 * so the click reaches the component's own handler. '@/lib/api' is stubbed so no network loads.
 */
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('@databricks/appkit-ui/react', () => {
  const Passthrough = ({ children }: { children?: ReactNode }) => children ?? null;
  const Button = ({
    children,
    onClick,
    type,
    disabled,
  }: {
    children?: ReactNode;
    onClick?: () => void;
    type?: 'button' | 'submit' | 'reset';
    disabled?: boolean;
  }) => (
    <button type={type} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  );
  return {
    Alert: Passthrough,
    AlertDescription: Passthrough,
    Button,
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
vi.mock('@/lib/api', () => ({ finalizeClaim: vi.fn(), ApiError: class ApiError extends Error {} }));

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

describe('DecisionForm — investigate (PEND) claim renders', () => {
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

describe('DecisionForm — submission payload', () => {
  beforeEach(() => {
    vi.mocked(finalizeClaim).mockReset();
    vi.mocked(finalizeClaim).mockResolvedValue({
      status: 'finalized',
      adjudicationId: 'ADJ-1',
      decidedBy: 'adjuster@example.com',
      overrideFlag: false,
      eventId: 'adj-ADJ-1',
    });
  });

  it('accepting an UNCHANGED PEND recommendation posts a finalize-valid payload (PEND_INVESTIGATE / PEND_INVESTIGATE / 0, no override reason)', async () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
      root.render(<DecisionForm adjudication={pendAdjudication()} onFinalized={() => {}} />);
      await Promise.resolve();
    });

    const acceptBtn = Array.from(container.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Accept recommendation')
    );
    expect(acceptBtn, 'the Accept recommendation button should render').toBeTruthy();

    // Click drives acceptRecommendation() -> submit(); the trailing microtask flushes the
    // mocked finalizeClaim resolution and its state updates inside act.
    await act(async () => {
      acceptBtn!.click();
      await Promise.resolve();
    });

    // Drives acceptRecommendation() -> submit(): the posted body must be exactly what
    // server/finalize.ts accepts for an unchanged investigate hold.
    expect(vi.mocked(finalizeClaim)).toHaveBeenCalledTimes(1);
    expect(vi.mocked(finalizeClaim)).toHaveBeenCalledWith('CLM-INVESTIGATE-1', {
      final_verdict: 'PEND_INVESTIGATE',
      final_disposition: 'PEND_INVESTIGATE',
      approved_amount: 0,
      override_reason: null,
    });

    await act(async () => {
      root.unmount();
      await Promise.resolve();
    });
    container.remove();
  });
});
