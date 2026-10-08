// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import type { ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

/**
 * Scroll-region regression guard for the Claim Copilot (the AI chat inside the claim modal).
 *
 * The modal's right rail stacks the decision form over this Copilot. The Ask (Genie) tab must
 * be a BOUNDED, internally-scrolling region — the same contract the dashboard and history
 * assistants satisfy by wrapping GenieAssistant in an `overflow-hidden`, definite-height box.
 * The live bug was the chat growing unbounded and clipping the modal; the fix bounds the Ask
 * panel with `min-h-0 flex-1 overflow-hidden`. jsdom has no layout engine, so we assert the
 * scroll-region CLASSES are passed through rather than measure pixels. appkit-ui's real entry
 * pulls echarts (fails strict-ESM under vitest), so the Tabs primitives and GenieAssistant are
 * stubbed to className-preserving nodes.
 */
vi.mock('@databricks/appkit-ui/react', () => {
  const El = ({ children, className }: { children?: ReactNode; className?: string }) => (
    <div data-class={className}>{children}</div>
  );
  // TabsContent exposes its own value so the Ask panel is addressable in the markup.
  const TabsContent = ({
    children,
    className,
    value,
  }: {
    children?: ReactNode;
    className?: string;
    value?: string;
  }) => (
    <div data-value={value} data-class={className}>
      {children}
    </div>
  );
  return { Tabs: El, TabsList: El, TabsTrigger: El, TabsContent };
});
vi.mock('@/components/GenieAssistant', () => ({ GenieAssistant: () => <div data-testid="genie" /> }));
vi.mock('@/components/States', () => ({ EmptyState: () => null, InlineNotice: () => null }));
vi.mock('./evidence', () => ({ SimilarClaims: () => null }));

const { CopilotPanel } = await import('./CopilotPanel');

/** Extract the class string of the <div> carrying data-value="ask" (attribute-order-agnostic). */
function askPanelClasses(html: string): Set<string> {
  const openTag = html.match(/<div[^>]*\bdata-value="ask"[^>]*>/);
  expect(openTag, 'the Ask tab panel should render').toBeTruthy();
  const cls = openTag![0].match(/\bdata-class="([^"]*)"/)?.[1] ?? '';
  return new Set(cls.split(/\s+/).filter(Boolean));
}

describe('CopilotPanel — chat scroll region', () => {
  it('bounds the Ask (Genie) tab as an internally-scrolling region', () => {
    const html = renderToStaticMarkup(<CopilotPanel claimId="CLM-1" record={null} priorClaims={[]} />);
    const classes = askPanelClasses(html);
    // flex-1: fill the rail's remaining height; min-h-0: allowed to shrink below content so the
    // bound engages; overflow-hidden: clip to the box so the Genie chat scrolls internally.
    expect(classes.has('flex-1')).toBe(true);
    expect(classes.has('min-h-0')).toBe(true);
    expect(classes.has('overflow-hidden')).toBe(true);
  });
});
