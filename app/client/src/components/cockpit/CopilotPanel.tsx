/**
 * The Claim Copilot. Three tabs:
 *   - Ask       natural-language questions about the claims data via the operational
 *               Genie space (OBO — runs as the signed-in adjuster).
 *   - Rationale the recommendation's rationale, read verbatim from the PERSISTED decision
 *               record — not a live model call.
 *   - Similar   prior claims referenced as precedent (from prior_claims).
 */

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@databricks/appkit-ui/react';
import { pct } from '@/lib/format';
import { GenieAssistant } from '@/components/GenieAssistant';
import { useRole } from '@/components/RoleContext';
import { EmptyState, InlineNotice } from '@/components/States';
import { SimilarClaims } from './evidence';
import type { DecisionRecord, PriorClaim } from '@/lib/types';

export function CopilotPanel({
  claimId,
  record,
  priorClaims,
}: {
  claimId: string;
  record: DecisionRecord | null;
  priorClaims: PriorClaim[];
}) {
  const violations = record?.invariant_violations ?? [];
  // Pick the Genie space the caller's role is permitted to reach: adjusters use the
  // operational cockpit space; business users use the gold analytics space. This keeps
  // the copilot usable when a business user opens a finalized claim from history without
  // tripping the server's per-alias authorization.
  const role = useRole();
  const alias = role === 'business_user' ? 'business' : 'cockpit';
  const spaceLabel = role === 'business_user' ? 'Gold analytics Genie space' : 'Operational Genie space';
  return (
    <Tabs defaultValue="ask" className="flex min-h-0 flex-1 flex-col gap-0">
      <TabsList className="mx-3 mt-3 w-[calc(100%-1.5rem)] shrink-0">
        <TabsTrigger value="ask">Ask</TabsTrigger>
        <TabsTrigger value="rationale">Rationale</TabsTrigger>
        <TabsTrigger value="similar">Similar</TabsTrigger>
      </TabsList>

      <TabsContent value="ask" className="min-h-0 flex-1 data-[state=inactive]:hidden">
        <GenieAssistant
          alias={alias}
          title="Ask about the claims data"
          description={`${spaceLabel} · context: ${claimId}`}
          placeholder={`Ask about ${claimId} or related claims…`}
          className="h-full"
        />
      </TabsContent>

      <TabsContent value="rationale" className="min-h-0 flex-1 overflow-auto p-3 data-[state=inactive]:hidden">
        {record?.rationale ? (
          <div className="space-y-3">
            <InlineNotice>Recorded rationale from the decision record — not a live model response.</InlineNotice>
            <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">{record.rationale}</p>
            <div className="flex flex-wrap gap-4 border-t border-border pt-3 text-sm">
              <span className="text-muted-foreground">
                Confidence <span className="font-semibold text-foreground tabular-nums">{pct(record.confidence)}</span>
              </span>
              {violations.length > 0 && <span className="text-warning">Invariant checks: {violations.join(', ')}</span>}
            </div>
          </div>
        ) : (
          <EmptyState title="No rationale recorded" message="The decision record carries no rationale text." />
        )}
      </TabsContent>

      <TabsContent value="similar" className="min-h-0 flex-1 overflow-auto p-3 data-[state=inactive]:hidden">
        <SimilarClaims claims={priorClaims} compact />
      </TabsContent>
    </Tabs>
  );
}
